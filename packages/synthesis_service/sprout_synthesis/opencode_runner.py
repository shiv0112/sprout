"""
sprout_synthesis.opencode_runner
--------------------------------------
Spawns the OpenCode CLI as an async subprocess with streaming JSON output.

NOTE: Uses asyncio.create_subprocess_exec (not shell exec) for safe
process spawning without shell injection risk.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path

from sprout_synthesis.config import get_settings

logger = logging.getLogger(__name__)


class OpenCodeError(Exception):
    """Raised when OpenCode CLI fails or times out."""
    pass


async def _drain_stderr(stream: asyncio.StreamReader) -> str:
    """Read stderr continuously to prevent pipe buffer from filling up."""
    chunks: list[bytes] = []
    try:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except Exception:
        pass
    return b"".join(chunks).decode("utf-8", errors="replace")


async def run_opencode(
    prompt: str,
    workdir: Path,
    on_event: Callable[[dict], None] | None = None,
) -> None:
    """Spawn `opencode run` in JSON streaming mode and forward events via callback.

    Uses asyncio.create_subprocess_exec for safe process spawning (no shell).

    Args:
        prompt: The task prompt for OpenCode CLI.
        workdir: Working directory where OpenCode will create files.
        on_event: Optional callback invoked for each NDJSON line from stdout.

    Raises:
        OpenCodeError: If OpenCode CLI exits with non-zero code or times out.
    """
    settings = get_settings()

    cmd = [
        "opencode", "run",
        "--format", "json",
        "-m", settings.opencode_model,
        "--", prompt,
    ]

    # Isolate OpenCode's per-run state. All runs otherwise share one global
    # OpenCode data dir (~/.local/share/opencode), whose SQLite DB throws
    # "database is locked" under concurrent runs — or when a previously
    # force-killed run leaves a stale lock. Give each run its own HOME (with the
    # shared config copied in) so runs never collide and a killed run's lock is
    # confined to its own throwaway directory.
    oc_home = workdir / ".opencode-home"
    oc_home.mkdir(parents=True, exist_ok=True)
    _cfg_root = os.environ.get("XDG_CONFIG_HOME")
    _src_cfg = (Path(_cfg_root) if _cfg_root else Path.home() / ".config") / "opencode" / "opencode.json"
    if _src_cfg.exists():
        _dst_cfg = oc_home / ".config" / "opencode"
        _dst_cfg.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_src_cfg, _dst_cfg / "opencode.json")

    # Suppress TUI rendering in subprocess — no TTY, no color.
    env = {
        **os.environ,
        "TERM": "dumb",
        "NO_COLOR": "1",
        "HOME": str(oc_home),
        "XDG_CONFIG_HOME": str(oc_home / ".config"),
        "XDG_DATA_HOME": str(oc_home / ".local" / "share"),
        "XDG_STATE_HOME": str(oc_home / ".local" / "state"),
        "XDG_CACHE_HOME": str(oc_home / ".cache"),
    }

    logger.info("Spawning OpenCode CLI in %s", workdir)
    logger.info("Command: opencode run <prompt> --format json --quiet --model %s", settings.opencode_model)

    try:
        # asyncio.create_subprocess_exec spawns the process directly (no shell)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=str(workdir),
            env=env,
        )
        # PIPE was requested for both stdout and stderr, so they are guaranteed
        # StreamReader. Narrow for mypy and fail loudly if asyncio breaks contract.
        if proc.stdout is None or proc.stderr is None:
            raise OpenCodeError("subprocess did not expose stdout/stderr pipes")

        # CRITICAL: Drain stderr concurrently to prevent pipe buffer deadlock.
        # OpenCode may write progress/TUI codes to stderr which can fill the
        # 64KB OS pipe buffer, blocking the process from writing to stdout.
        stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))

        # Read stdout line-by-line (NDJSON streaming via --format json).
        # Per-line timeout is 120s (stalled I/O). Total process timeout is
        # opencode_timeout (default 600s) to guard against runaway processes.
        import time as _time
        opencode_error: str | None = None
        tail: list[str] = []   # recent stdout lines, for diagnosing silent exits
        line_timeout = 120
        deadline = _time.monotonic() + settings.opencode_timeout
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                proc.kill()
                raise OpenCodeError(f"OpenCode exceeded total timeout ({settings.opencode_timeout}s)")
            line = await asyncio.wait_for(
                proc.stdout.readline(),
                timeout=min(line_timeout, remaining),
            )
            if not line:
                break

            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            logger.debug("OpenCode stdout: %s", line_str[:200])
            tail.append(line_str)
            del tail[:-12]

            # Forward to event callback
            if on_event is not None:
                try:
                    event = json.loads(line_str)
                    if event.get("type") == "error":
                        error_payload = event.get("error") or {}
                        if isinstance(error_payload, dict):
                            nested = error_payload.get("data") or {}
                            if isinstance(nested, dict):
                                opencode_error = (
                                    nested.get("message")
                                    or error_payload.get("message")
                                    or line_str
                                )
                            else:
                                opencode_error = error_payload.get("message") or line_str
                        else:
                            opencode_error = line_str
                    on_event({"type": "opencode", **event})
                except json.JSONDecodeError:
                    on_event({"type": "opencode", "raw": line_str})

        # Wait for process to finish.
        # Known issue: OpenCode may hang after completing work (GitHub #17516).
        # Use a shorter timeout and force-kill if it hangs — output is already
        # captured from stdout so this is safe.
        try:
            await asyncio.wait_for(proc.wait(), timeout=30)
        except TimeoutError:
            logger.warning("OpenCode process hung after stdout EOF, force-killing (known issue)")
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=5)

        # Collect stderr
        stderr_str = await stderr_task

    except TimeoutError:
        proc.kill()
        # Still try to collect stderr for diagnostics
        stderr_str = ""
        with contextlib.suppress(Exception):
            stderr_str = await asyncio.wait_for(stderr_task, timeout=5)
        logger.error("OpenCode CLI timed out. stderr tail: %s", stderr_str[-2000:] if stderr_str else "(empty)")
        raise OpenCodeError("OpenCode CLI timed out") from None

    if proc.returncode != 0:
        detail = stderr_str.strip() or (" | ".join(tail[-5:]) if tail else "(no output on stdout/stderr)")
        logger.error(
            "OpenCode CLI exited with code %d\nstderr: %s\nlast stdout: %s",
            proc.returncode, stderr_str[:2000], " | ".join(tail[-8:]),
        )
        raise OpenCodeError(
            f"OpenCode CLI exited with code {proc.returncode}: {detail[:800]}"
        )

    if opencode_error:
        logger.error("OpenCode reported an error despite zero exit code: %s", opencode_error)
        raise OpenCodeError(opencode_error)

    logger.info("OpenCode CLI completed successfully in %s", workdir)
