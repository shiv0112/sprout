"""
kiln_executor/main.py
─────────────────────
Kiln Tool Executor — isolated, sandboxed tool execution service.

Receives tool source code + arguments, installs dependencies in a temporary
virtualenv, validates against blocked-package rules, and executes in an
isolated subprocess with strict timeout enforcement.

On Cloud Run this service runs with gVisor sandboxing and 1 concurrent
request per instance for complete isolation between tool executions.

Endpoints
─────────
  POST /execute          -> run tool code in isolated subprocess
  GET  /health           -> service health
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from kiln_shared.auth import verify_internal_secret
from kiln_shared.metrics import mount_metrics

from .safety import validate_source
from .sandbox import ExecutionResult, run_in_sandbox

logger = logging.getLogger(__name__)

app = FastAPI(
    title="KilnToolExecutor",
    description=(
        "Isolated tool execution service. Receives tool source code and "
        "arguments, installs dependencies in a temp virtualenv, validates "
        "blocked imports, and executes in a subprocess with timeout."
    ),
    version="1.0.0",
    lifespan=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3001,http://localhost:8766",
    ).split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

mount_metrics(app, "tool_executor")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    from kiln_shared.logging_config import setup_logging
    setup_logging()
    logger.info("Tool Executor started")
    yield


app.router.lifespan_context = _lifespan


# ── Request / Response Models ────────────────────────────────────────────────


class ExecuteRequest(BaseModel):
    """Payload for POST /execute."""

    tool_id: str = Field(..., description="Kiln tool ID, e.g. com.kiln.tools.weather")
    function_name: str = Field(..., description="Name of the Python function to call")
    code: str = Field(..., description="Python source code of the tool implementation")
    args: dict = Field(default_factory=dict, description="Arguments to pass to the function")
    requirements: list[str] = Field(
        default_factory=list,
        description="pip requirements (e.g. ['requests>=2.0', 'beautifulsoup4'])",
    )
    timeout: int = Field(default=30, ge=1, le=300, description="Execution timeout in seconds")
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to inject (e.g. API keys)",
    )


class ExecuteResponse(BaseModel):
    """Response from POST /execute."""

    success: bool
    tool_id: str
    result: dict | list | str | int | float | bool | None = None
    error: str | None = None
    execution_time_ms: int = 0


# ── Routes ───────────────────────────────────────────────────────────────────


@app.get("/health", summary="Service health")
@app.get("/livez", summary="Liveness probe", include_in_schema=False)
def health():
    return {"status": "ok", "service": "kiln-tool-executor"}


@app.get("/readyz", summary="Readiness probe")
def readyz():
    return {"status": "ok", "service": "kiln-tool-executor"}


@app.post("/execute", summary="Execute tool code in isolated sandbox")
async def execute(body: ExecuteRequest, request: Request) -> ExecuteResponse:
    """
    Execute a tool in an isolated subprocess.

    Flow:
      1. Validate source code against blocked-package rules
      2. Create temp directory with tool code + requirements
      3. Install dependencies in isolated virtualenv (if any)
      4. Execute function in subprocess with timeout
      5. Return structured result

    Authentication: internal service secret (X-Internal-Secret header).
    This endpoint is called by registry_api, not directly by users.
    """
    verify_internal_secret(request)

    start = time.monotonic()

    # Step 1: Safety validation
    violations = validate_source(body.code)
    if violations:
        return ExecuteResponse(
            success=False,
            tool_id=body.tool_id,
            error=f"Blocked imports detected: {'; '.join(violations)}",
        )

    # Step 2-4: Run in sandbox
    result: ExecutionResult = await run_in_sandbox(
        tool_id=body.tool_id,
        function_name=body.function_name,
        code=body.code,
        args=body.args,
        requirements=body.requirements,
        timeout=body.timeout,
        env_vars=body.env_vars,
    )

    elapsed_ms = int((time.monotonic() - start) * 1000)

    if result.success:
        return ExecuteResponse(
            success=True,
            tool_id=body.tool_id,
            result=result.data,
            execution_time_ms=elapsed_ms,
        )
    else:
        return ExecuteResponse(
            success=False,
            tool_id=body.tool_id,
            error=result.error,
            execution_time_ms=elapsed_ms,
        )
