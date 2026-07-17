"""
kiln_executor/sandbox.py
────────────────────────
Isolated subprocess execution for Kiln tools.

Creates a temporary directory with the tool's source code, optionally
installs pip dependencies into an isolated virtualenv, then runs the
tool function in a subprocess with strict timeout enforcement.

On Cloud Run, this runs inside a gVisor-sandboxed container with
1 concurrent request per instance — the subprocess inherits the
gVisor kernel's syscall filtering for defense in depth.

For local development, subprocess isolation + blocked-package
validation provides a reasonable security boundary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Pre-baked packages that don't need pip install (available in the base image)
PREBAKED_PACKAGES = frozenset({
    "requests", "httpx", "beautifulsoup4", "bs4", "lxml",
    "pydantic", "pyyaml", "yaml", "json", "re", "math",
    "datetime", "collections", "itertools", "functools",
    "urllib", "urllib3", "certifi", "charset-normalizer",
    "idna", "typing", "typing_extensions", "dataclasses",
})


@dataclass
class ExecutionResult:
    """Result of a sandboxed tool execution."""

    success: bool
    data: dict | list | str | int | float | bool | None = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""


def _build_runner_script(function_name: str, args: dict) -> str:
    """
    Generate the Python script that runs inside the subprocess.

    The runner:
      1. Imports the tool module
      2. Calls the specified function with the given args
      3. Prints the result as JSON to stdout
      4. On error, prints a JSON error object
    """
    args_json = json.dumps(args)
    return textwrap.dedent(f"""\
        import json
        import sys
        import os

        # Redirect any print() calls from the tool to stderr
        # so they don't corrupt our JSON output on stdout
        _real_stdout = sys.stdout
        sys.stdout = sys.stderr

        try:
            # Import the tool module from the working directory
            sys.path.insert(0, os.getcwd())
            import impl

            fn = getattr(impl, {function_name!r})
            args = json.loads({args_json!r})
            result = fn(**args)

            # Restore stdout and write the result
            sys.stdout = _real_stdout
            json.dump({{"success": True, "result": result}}, sys.stdout)
        except Exception as exc:
            sys.stdout = _real_stdout
            json.dump({{"success": False, "error": f"{{type(exc).__name__}}: {{exc}}"}}, sys.stdout)
    """)


async def _install_requirements(
    venv_dir: Path,
    requirements: list[str],
    timeout: int = 60,
) -> tuple[bool, str]:
    """
    Install pip requirements into an isolated virtualenv.

    Uses asyncio.create_subprocess_exec (not shell) to avoid injection.
    Returns (success, error_message).
    """
    # Create virtualenv
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "venv", str(venv_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode() if proc.stderr else ""
        return False, f"Failed to create virtualenv: {stderr}"

    # Install requirements using the venv's pip
    pip_path = venv_dir / "bin" / "pip"
    if not pip_path.exists():
        pip_path = venv_dir / "Scripts" / "pip"  # Windows fallback

    proc = await asyncio.create_subprocess_exec(
        str(pip_path), "install", "--no-cache-dir", "--quiet", *requirements,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return False, f"pip install failed: {stderr_bytes.decode()[:500]}"
        return True, ""
    except TimeoutError:
        proc.kill()
        return False, f"pip install timed out after {timeout}s"


async def run_in_sandbox(
    tool_id: str,
    function_name: str,
    code: str,
    args: dict,
    requirements: list[str] | None = None,
    timeout: int = 30,
    env_vars: dict[str, str] | None = None,
) -> ExecutionResult:
    """
    Run tool code in an isolated subprocess.

    Steps:
      1. Create temp directory with impl.py (the tool code)
      2. If requirements are specified, create a virtualenv and pip install them
      3. Generate a runner script that imports impl and calls the function
      4. Run in subprocess with timeout (uses create_subprocess_exec, not shell)
      5. Parse JSON result from stdout
    """
    requirements = requirements or []
    env_vars = env_vars or {}

    with tempfile.TemporaryDirectory(prefix=f"kiln_exec_{tool_id.split('.')[-1]}_") as tmpdir:
        work_dir = Path(tmpdir)

        # Write tool implementation
        (work_dir / "impl.py").write_text(code)

        # Write runner script
        runner_code = _build_runner_script(function_name, args)
        runner_path = work_dir / "_runner.py"
        runner_path.write_text(runner_code)

        # Determine Python interpreter
        python_bin = sys.executable

        # Install requirements if any non-prebaked packages are needed
        needs_install = [
            r for r in requirements
            if r.split(">=")[0].split("==")[0].strip().lower() not in PREBAKED_PACKAGES
        ]

        if needs_install:
            venv_dir = work_dir / ".venv"
            ok, err = await _install_requirements(venv_dir, requirements, timeout=60)
            if not ok:
                return ExecutionResult(success=False, error=err)
            python_bin = str(venv_dir / "bin" / "python")
            if not Path(python_bin).exists():
                python_bin = str(venv_dir / "Scripts" / "python")  # Windows

        # Build subprocess environment — minimal, no leaking host env
        proc_env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": tmpdir,
            "TMPDIR": tmpdir,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        # Inject user-provided env vars (API keys etc.)
        proc_env.update(env_vars)

        # Run in subprocess (create_subprocess_exec — no shell, no injection risk)
        logger.info(
            "Executing %s::%s (timeout=%ds, deps=%d)",
            tool_id, function_name, timeout, len(requirements),
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                python_bin, str(runner_path),
                cwd=str(work_dir),
                env=proc_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecutionResult(
                success=False,
                error=f"Execution timed out after {timeout}s",
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if stderr:
            logger.debug("Tool stderr: %s", stderr[:500])

        # Parse result
        if proc.returncode != 0:
            return ExecutionResult(
                success=False,
                error=f"Process exited with code {proc.returncode}: {stderr[:500]}",
                stdout=stdout,
                stderr=stderr,
            )

        if not stdout:
            return ExecutionResult(
                success=False,
                error="Tool produced no output",
                stderr=stderr,
            )

        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            return ExecutionResult(
                success=False,
                error=f"Tool output is not valid JSON: {stdout[:200]}",
                stdout=stdout,
                stderr=stderr,
            )

        if result.get("success"):
            return ExecutionResult(
                success=True,
                data=result.get("result"),
                stdout=stdout,
                stderr=stderr,
            )
        else:
            return ExecutionResult(
                success=False,
                error=result.get("error", "Unknown execution error"),
                stdout=stdout,
                stderr=stderr,
            )
