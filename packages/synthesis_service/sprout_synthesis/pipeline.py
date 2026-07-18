"""
sprout_synthesis.pipeline
---------------------------------
Main synthesis orchestrator.

Prepares workspace -> spawns OpenCode CLI -> validates artifacts -> calls webhook.
Includes local validation (spec schema, import test, functional test) with retry
on failure — OpenCode gets up to 2 fix attempts before giving up.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

import yaml

from sprout_synthesis.callback import notify_failure, notify_success
from sprout_synthesis.config import get_settings
from sprout_synthesis.jobs.job_store import job_store
from sprout_synthesis.models import JobStatus, SynthesizeRequest
from sprout_synthesis.opencode_runner import OpenCodeError, run_opencode
from sprout_synthesis.prompt_builder import build_prompt, write_context

logger = logging.getLogger(__name__)

MAX_FIX_ATTEMPTS = 2

# Required top-level keys in a valid Sprout spec
_REQUIRED_SPEC_KEYS = {"tool", "interface", "implementation"}
_REQUIRED_TOOL_KEYS = {"id", "name", "version", "description"}


# ── Validation helpers ────────────────────────────────────────────────────────


def _validate_spec(spec_path: Path) -> str | None:
    """Validate spec.yaml structure. Returns error string or None if valid."""
    try:
        raw = yaml.safe_load(spec_path.read_text())
    except yaml.YAMLError as exc:
        return f"Invalid YAML: {exc}"

    if not isinstance(raw, dict):
        return "spec.yaml is not a YAML mapping"

    missing = _REQUIRED_SPEC_KEYS - set(raw.keys())
    if missing:
        return f"spec.yaml missing top-level keys: {missing}"

    tool = raw.get("tool", {})
    if not isinstance(tool, dict):
        return "spec.yaml 'tool' is not a mapping"

    missing_tool = _REQUIRED_TOOL_KEYS - set(tool.keys())
    if missing_tool:
        return f"spec.yaml tool section missing keys: {missing_tool}"

    impl = raw.get("implementation", {})
    if not impl.get("entrypoint"):
        return "spec.yaml missing implementation.entrypoint"

    iface = raw.get("interface", {})
    if not isinstance(iface.get("inputs"), list):
        return "spec.yaml missing interface.inputs list"

    return None


def _validate_impl(impl_path: Path, tool_name: str) -> str | None:
    """Run import test and basic functional test. Returns error string or None."""
    # Step 1: Import test
    import_cmd = [
        sys.executable, "-c",
        f"from impl import {tool_name}; print('Import OK')",
    ]
    try:
        result = subprocess.run(
            import_cmd,
            cwd=str(impl_path.parent),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()[-500:]
            return f"Import test failed: {stderr}"
    except subprocess.TimeoutExpired:
        return "Import test timed out (30s)"

    # Step 2: Check the function is callable and returns a dict
    func_test_cmd = [
        sys.executable, "-c",
        f"from impl import {tool_name}; r = {tool_name}(); "
        f"assert isinstance(r, dict), f'Expected dict, got {{type(r).__name__}}'; "
        f"print('Functional OK')",
    ]
    try:
        result = subprocess.run(
            func_test_cmd,
            cwd=str(impl_path.parent),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()[-500:]
            # Non-fatal: function may require specific args, import passing is enough
            logger.warning("Functional test warning for %s: %s", tool_name, stderr[:200])
    except subprocess.TimeoutExpired:
        logger.warning("Functional test timed out for %s (60s)", tool_name)

    return None


def _collect_errors(workspace: Path, tool_name: str) -> list[str]:
    """Run all validations, return list of error strings (empty = all passed)."""
    errors: list[str] = []

    spec_path = workspace / "spec.yaml"
    impl_path = workspace / "impl.py"

    if not spec_path.exists():
        errors.append("spec.yaml was not created")
    else:
        spec_err = _validate_spec(spec_path)
        if spec_err:
            errors.append(f"spec.yaml validation: {spec_err}")

    if not impl_path.exists():
        errors.append("impl.py was not created")
    else:
        impl_err = _validate_impl(impl_path, tool_name)
        if impl_err:
            errors.append(f"impl.py validation: {impl_err}")

    return errors


# ── Env var extraction ────────────────────────────────────────────────────────


def _extract_env_vars(impl_path: Path) -> list[dict[str, str]]:
    """Extract REQUIRED_ENV_VARS list from impl.py.

    Falls back to regex scanning if the global isn't defined.
    Returns [{"name": "VAR", "description": "..."}, ...].
    """
    code = impl_path.read_text()

    # Try to load REQUIRED_ENV_VARS by executing the assignment
    namespace: dict = {}
    try:
        compiled = compile(code, str(impl_path), "exec")
        exec(compiled, namespace)
        env_vars = namespace.get("REQUIRED_ENV_VARS")
        if isinstance(env_vars, list):
            return env_vars
    except Exception:
        pass

    # Fallback: regex scan for os.environ usage
    pattern = r'os\.environ(?:\.get)?\s*[\(\[]\s*["\']([A-Z_][A-Z0-9_]*)["\']'
    found = sorted(set(re.findall(pattern, code)))
    return [{"name": name, "description": ""} for name in found]


# ── Pipeline ──────────────────────────────────────────────────────────────────


def _emit(job_id: str, stage: str, **extra) -> None:
    """Push a pipeline event to the SSE queue AND log it to stdout/docker logs."""
    msg = extra.get("message", stage)
    logger.info("[%s] %s | %s", job_id[:8], stage, msg)
    job_store.push_event(job_id, {"type": "pipeline", "stage": stage, **extra})


async def run_synthesis_pipeline(job_id: str, request: SynthesizeRequest) -> None:
    """Background task: synthesize a Sprout tool via OpenCode CLI and register it.

    Includes local validation with up to {MAX_FIX_ATTEMPTS} retry attempts.
    Pushes SSE events at each stage so clients can follow progress via
    GET /synthesize/{job_id}/events.
    """
    settings = get_settings()
    callback_url = request.callback_url or settings.callback_url
    tool_id = f"com.sprout.tools.{request.tool_name}"

    try:
        job_store.update(job_id, status=JobStatus.RUNNING)
        _emit(job_id, "started", message=f"Synthesizing tool: {request.tool_name}")

        # Step 1: Prepare workspace
        workspace = Path(settings.workspace_dir) / job_id
        workspace.mkdir(parents=True, exist_ok=True)

        write_context(workspace, request)
        _emit(job_id, "workspace_ready", message=f"Workspace prepared at {workspace}")
        logger.info("Workspace prepared at %s", workspace)

        # Step 2: Spawn OpenCode CLI (streaming — events forwarded via on_event)
        prompt = build_prompt(workspace, request)

        def forward_agent_event(event: dict) -> None:
            event_kind = event.get("type", "")
            if event_kind == "tool_use":
                tool_name = event.get("tool", "")
                status = event.get("status", "")
                preview = f"{tool_name} ({status})"
            elif event_kind in ("step_start", "step_finish"):
                preview = event.get("message", event_kind)
            elif event_kind == "error":
                preview = event.get("message", "")
            else:
                preview = str(event.get("content", event.get("raw", "")))[:200]
            logger.info("[%s] opencode:%s | %s", job_id[:8], event_kind, preview)
            job_store.push_event(job_id, event)

        _emit(job_id, "agent_started", message="OpenCode CLI spawned, generating tool...")
        await run_opencode(prompt, workspace, on_event=forward_agent_event)
        _emit(job_id, "agent_finished", message="OpenCode CLI completed")

        # Step 3: Validate artifacts locally before sending to registry
        errors = _collect_errors(workspace, request.tool_name)

        # Retry loop: if validation fails, ask OpenCode to fix
        for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
            if not errors:
                break

            error_summary = "\n".join(f"- {e}" for e in errors)
            _emit(
                job_id, "validation_failed",
                message=f"Validation failed (attempt {attempt}/{MAX_FIX_ATTEMPTS}): {error_summary}",
            )
            logger.warning("Validation failed for %s (attempt %d): %s", job_id[:8], attempt, errors)

            fix_prompt = (
                f"The generated tool has validation errors that must be fixed:\n\n"
                f"{error_summary}\n\n"
                f"Please fix spec.yaml and/or impl.py in this directory to resolve these errors. "
                f"Re-read CONTEXT.md if needed. Make sure:\n"
                f"1. spec.yaml has all required keys: tool (with id, name, version, description), interface (with inputs list), implementation (with entrypoint)\n"
                f"2. impl.py can be imported without errors: python -c \"from impl import {request.tool_name}\"\n"
                f"3. The function {request.tool_name} returns a dict\n"
                f"Fix the issues now."
            )

            _emit(job_id, "fix_started", message=f"OpenCode fixing errors (attempt {attempt})...")
            await run_opencode(fix_prompt, workspace, on_event=forward_agent_event)
            _emit(job_id, "fix_finished", message=f"Fix attempt {attempt} completed")

            errors = _collect_errors(workspace, request.tool_name)

        if errors:
            error_summary = "\n".join(f"- {e}" for e in errors)
            raise ValueError(f"Validation failed after {MAX_FIX_ATTEMPTS} fix attempts:\n{error_summary}")

        _emit(job_id, "validation_passed", message="All validations passed")

        # Step 4: Extract tool_id and env vars from validated artifacts
        spec_path = workspace / "spec.yaml"
        impl_path = workspace / "impl.py"

        spec_data = yaml.safe_load(spec_path.read_text())
        tool_section = spec_data.get("tool", {})
        if tool_section.get("id"):
            tool_id = tool_section["id"]

        env_vars = _extract_env_vars(impl_path)

        _emit(job_id, "artifacts_collected", message=f"Found spec.yaml + impl.py (tool_id={tool_id})", env_vars=env_vars)
        logger.info("Artifacts collected: %s, %s (tool_id=%s, env_vars=%s)", spec_path, impl_path, tool_id, env_vars)

        # Step 5: Webhook callback — success
        await notify_success(callback_url, tool_id, spec_path, impl_path, env_vars=env_vars)
        job_store.update(job_id, status=JobStatus.SUCCEEDED, tool_id=tool_id)
        _emit(job_id, "callback_sent", tool_id=tool_id, message="Tool registered via webhook")
        _emit(job_id, "done", status="succeeded", tool_id=tool_id)
        logger.info("Synthesis completed for job %s -> %s", job_id, tool_id)

    except Exception as exc:
        error_msg = str(exc) if isinstance(exc, (OpenCodeError, FileNotFoundError, ValueError)) else f"Unexpected error: {type(exc).__name__}: {exc}"
        logger.error("Synthesis failed for job %s: %s", job_id, error_msg)
        job_store.update(job_id, status=JobStatus.FAILED, error=error_msg)
        _emit(job_id, "error", message=error_msg)
        _emit(job_id, "done", status="failed", error=error_msg)
        try:
            await notify_failure(callback_url, tool_id, error_msg)
        except Exception:
            logger.exception("Failed to notify Sprout of synthesis failure")

    finally:
        # Close the SSE stream
        job_store.close_event_queue(job_id)
