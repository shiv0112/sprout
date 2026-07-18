"""
sprout_synthesis.routes.events
-----------------------------
SSE endpoint to stream real-time synthesis progress.

Usage:
    curl -N http://localhost:8002/synthesize/{job_id}/events
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from sprout_synthesis.jobs.job_store import job_store

router = APIRouter()


@router.get("/synthesize/{job_id}/events", summary="Stream synthesis events via SSE")
async def stream_events(job_id: str):
    """Server-Sent Events stream for a synthesis job.

    Events are newline-delimited JSON objects.
    The stream closes when the job completes (succeeded or failed).
    """
    queue = job_store.get_event_queue(job_id)
    if queue is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    async def event_generator():
        while True:
            event = await queue.get()
            if event is None:  # sentinel — job done
                yield f"data: {json.dumps({'type': 'pipeline', 'stage': 'stream_closed'})}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
