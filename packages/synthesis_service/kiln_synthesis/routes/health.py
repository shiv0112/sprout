"""
kiln_synthesis.routes.health
----------------------------
Liveness and readiness probes for the synthesis service.

- ``GET /livez``  — cheap, no dependency calls. Used by orchestrators
  to decide whether to RESTART the container.
- ``GET /readyz`` — real readiness check. Pings the registry callback URL
  (where synthesized tools are POSTed back) as a HARD dependency. Returns
  503 if the registry is unreachable so orchestrators stop routing
  synthesis jobs to a dead instance.
- ``GET /health`` — combined endpoint kept for backwards compatibility
  with the iter-37 chat_backend `/readyz` check that calls this URL.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from kiln_shared.httpx_client import async_client
from kiln_synthesis.config import get_settings

router = APIRouter()


def _registry_livez_url() -> str:
    """Derive the registry's /livez URL from the configured callback URL.

    callback_url is something like ``http://host.docker.internal:8766/synthesis/callback``
    — strip the path and append ``/livez``.
    """
    parsed = urlparse(get_settings().callback_url)
    return f"{parsed.scheme}://{parsed.netloc}/livez"


@router.get("/livez", summary="Liveness — process is up")
def livez() -> dict:
    """Cheap liveness probe. No dependency calls."""
    return {"status": "ok", "service": "kiln-synthesis-service"}


@router.get("/readyz", summary="Readiness — registry callback reachable", response_model=None)
async def readyz() -> dict | JSONResponse:
    """Real readiness probe.

    Pings the registry's /livez at the URL derived from KILN_SYNTHESIS_CALLBACK_URL.
    Returns 503 if unreachable so orchestrators stop sending us synthesis
    jobs we wouldn't be able to deliver back.
    """
    checks: dict[str, str] = {}
    overall = "ok"

    target = _registry_livez_url()
    try:
        async with async_client(timeout=2.0) as client:
            resp = await client.get(target)
            if resp.status_code == 200:
                checks["registry_callback"] = f"ok ({target})"
            else:
                checks["registry_callback"] = f"unhealthy: HTTP {resp.status_code} ({target})"
                overall = "degraded"
    except Exception as exc:
        checks["registry_callback"] = f"unreachable: {exc!s} ({target})"
        overall = "degraded"

    body: dict = {
        "status": overall,
        "service": "kiln-synthesis-service",
        "checks": checks,
    }
    if overall != "ok":
        return JSONResponse(status_code=503, content=body)
    return body


@router.get("/health", summary="Combined health: live + ready (legacy compat)", response_model=None)
async def health_check() -> dict | JSONResponse:
    """Legacy combined health endpoint.

    Iter-37 chat_backend's /readyz fetches this URL as the synthesis_service
    soft-dependency check. Keep returning 200 with a usable shape so
    chat_backend keeps reporting synthesis as ok when we're up.
    """
    ready = await readyz()
    if isinstance(ready, JSONResponse):
        return ready
    return {
        "status": "ok",
        "service": "kiln-synthesis-service",
        "version": "1.0.0",
        "checks": ready["checks"],
    }
