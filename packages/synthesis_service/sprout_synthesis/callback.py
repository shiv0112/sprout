"""
sprout_synthesis.callback
---------------------------------
Posts synthesis results back to the Sprout registry webhook.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sprout_shared.httpx_client import async_client
from sprout_synthesis.config import get_settings

logger = logging.getLogger(__name__)


def _auth_headers() -> dict[str, str]:
    """Build headers with internal service-to-service auth secret."""
    secret = get_settings().internal_secret
    if secret:
        return {"X-Internal-Secret": secret}
    return {}


async def notify_success(
    callback_url: str,
    tool_id: str,
    spec_path: Path,
    impl_path: Path,
    env_vars: list[dict[str, str]] | None = None,
) -> None:
    """POST multipart form with spec.yaml and impl.py to the Sprout registry."""
    logger.info("Sending synthesized tool %s to %s", tool_id, callback_url)

    form_data = {"tool_id": tool_id}
    if env_vars:
        form_data["env_vars"] = json.dumps(env_vars)

    async with async_client(timeout=30) as client:
        response = await client.post(
            callback_url,
            data=form_data,
            files={
                "spec": ("spec.yaml", spec_path.read_bytes(), "application/x-yaml"),
                "impl": ("impl.py", impl_path.read_bytes(), "text/x-python"),
            },
            headers=_auth_headers(),
        )
        response.raise_for_status()
        logger.info("Tool %s registered successfully: %s", tool_id, response.json())


async def notify_failure(
    callback_url: str,
    tool_id: str,
    error: str,
) -> None:
    """Log synthesis failure. The registry's callback endpoint only accepts
    multipart success payloads (spec+impl files), so failure notifications
    are logged server-side. The chat backend detects failures via its own
    polling timeout when the tool never appears in the registry.
    """
    logger.warning("Synthesis failed for %s: %s", tool_id, error)
    logger.info(
        "Failure not forwarded to registry — %s only accepts multipart success payloads. "
        "The chat backend will detect this via synthesis timeout.",
        callback_url,
    )
