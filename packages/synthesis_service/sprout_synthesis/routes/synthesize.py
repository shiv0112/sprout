"""
sprout_synthesis.routes.synthesize
--------------------------------
POST /synthesize — accepts a tool synthesis request, queues background work,
and calls the webhook on completion.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from sprout_synthesis.jobs.job_store import job_store
from sprout_synthesis.models import JobStatus, SynthesizeRequest, SynthesizeResponse
from sprout_synthesis.pipeline import run_synthesis_pipeline

router = APIRouter()


@router.post("/synthesize", response_model=SynthesizeResponse, summary="Synthesize a new Sprout tool")
async def synthesize_tool(
    request: SynthesizeRequest,
    background_tasks: BackgroundTasks,
):
    job_id = request.job_id or str(uuid.uuid4())
    job_store.create(job_id, request.tool_name)
    background_tasks.add_task(run_synthesis_pipeline, job_id, request)

    return SynthesizeResponse(
        job_id=job_id,
        status=JobStatus.ACCEPTED,
        message=f"Synthesis started for tool: {request.tool_name}",
    )


@router.get("/synthesize/status/{tool_ref}", summary="Get the latest synthesis status for a tool")
async def synthesize_status(tool_ref: str):
    job = job_store.find_latest_for_tool(tool_ref)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"status": "not_found", "error": f"No synthesis job found for {tool_ref}"},
        )

    body = {
        "job_id": job.job_id,
        "tool_name": job.tool_name,
        "tool_id": job.tool_id,
        "status": job.status,
        "error": job.error,
        "updated_at": job.updated_at.isoformat(),
    }
    return JSONResponse(content=body)
