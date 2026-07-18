"""
sprout_synthesis.main
-------------------
FastAPI application — tool synthesis service powered by OpenCode CLI.

Run from the synthesis_service/ directory:
    python sprout_synthesis/main.py
    # or
    uvicorn sprout_synthesis.main:app --host 0.0.0.0 --port 8002
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure `synthesis_service/` is on sys.path so `from sprout_synthesis.` imports resolve
# regardless of whether we run via `python sprout_synthesis/main.py` or `uvicorn sprout_synthesis.main:app`
_SYNTHESIS_ROOT = str(Path(__file__).parent.parent)
if _SYNTHESIS_ROOT not in sys.path:
    sys.path.insert(0, _SYNTHESIS_ROOT)

from fastapi import FastAPI  # noqa: E402

from sprout_synthesis.routes.events import router as events_router  # noqa: E402
from sprout_synthesis.routes.health import router as health_router  # noqa: E402
from sprout_synthesis.routes.synthesize import router as synthesize_router  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

app = FastAPI(
    title="Sprout Synthesis",
    description=(
        "Sprout Synthesis service — generates missing tools on demand "
        "using OpenCode CLI, then registers them via webhook."
    ),
    version="1.0.0",
)

# Per-request correlation ID propagation.
from sprout_shared.request_id import SproutRequestIDMiddleware  # noqa: E402

app.add_middleware(SproutRequestIDMiddleware)

app.include_router(health_router)
app.include_router(synthesize_router)
app.include_router(events_router)

# Wire log_dir from settings into the job store
from sprout_synthesis.config import get_settings  # noqa: E402
from sprout_synthesis.jobs.job_store import job_store  # noqa: E402

job_store.set_log_dir(get_settings().log_dir)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("sprout_synthesis.main:app", host="0.0.0.0", port=8002, reload=False)
