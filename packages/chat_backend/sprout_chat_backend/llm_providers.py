"""Shared LLM provider configuration for planning and agent execution.

Both NVIDIA NIM and Mistral expose an OpenAI-compatible API, so every LLM
call in the chat backend goes through the ``openai`` SDK with a per-provider
``base_url``. The default chain is **NVIDIA NIM primary, Mistral fallback**:

    request ─► NVIDIA NIM (glm-5.2)  ──on error/429──►  Mistral (large)

- The planner (``planner.py``) walks the chain manually with backoff.
- The AG2 executor (``graph_flow.py`` via ``main.py``) passes the chain as a
  multi-entry ``config_list``; AG2 tries each entry in order and falls back
  automatically on failure.

Env vars:
  NVIDIA_API_KEY        NVIDIA NIM key (enables the NIM provider)
  MISTRAL_API_KEY       Mistral key (enables the Mistral provider / fallback)
  SPROUT_NIM_MODEL        NIM model id            (default: z-ai/glm-5.2)
  SPROUT_MISTRAL_MODEL    Mistral model id        (default: mistral-large-latest)
  SPROUT_LLM_PRIMARY      "nim" | "mistral"       (default: nim)
"""
from __future__ import annotations

import os

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"

DEFAULT_NIM_MODEL = "z-ai/glm-5.2"
DEFAULT_MISTRAL_MODEL = "mistral-large-latest"


def _nim_model() -> str:
    return os.environ.get("SPROUT_NIM_MODEL", DEFAULT_NIM_MODEL).strip() or DEFAULT_NIM_MODEL


def _mistral_model() -> str:
    return os.environ.get("SPROUT_MISTRAL_MODEL", DEFAULT_MISTRAL_MODEL).strip() or DEFAULT_MISTRAL_MODEL


def provider_chain(mistral_api_key: str = "") -> list[dict]:
    """Ordered list of available LLM providers (primary first).

    A provider is included only when its API key is present. ``mistral_api_key``
    (usually the per-request key) takes precedence over ``MISTRAL_API_KEY``.

    Raises:
        RuntimeError: when neither provider has a key.
    """
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "").strip()
    mistral_key = (mistral_api_key or os.environ.get("MISTRAL_API_KEY", "")).strip()

    nim = (
        {"name": "nvidia-nim", "model": _nim_model(), "api_key": nvidia_key, "base_url": NIM_BASE_URL}
        if nvidia_key
        else None
    )
    mistral = (
        {"name": "mistral", "model": _mistral_model(), "api_key": mistral_key, "base_url": MISTRAL_BASE_URL}
        if mistral_key
        else None
    )

    primary = os.environ.get("SPROUT_LLM_PRIMARY", "nim").strip().lower()
    ordered = [mistral, nim] if primary == "mistral" else [nim, mistral]
    chain = [p for p in ordered if p is not None]

    if not chain:
        raise RuntimeError(
            "No LLM provider configured. Set NVIDIA_API_KEY (NVIDIA NIM) "
            "and/or MISTRAL_API_KEY (Mistral)."
        )
    return chain


def ag2_config_list(mistral_api_key: str = "") -> list[dict]:
    """AG2 ``config_list`` — each provider as an OpenAI-compatible entry.

    AG2 tries entries in order and falls back to the next on failure, which
    gives us "NVIDIA primary, Mistral backoff" for the multi-agent executor
    with no custom retry code.
    """
    return [
        {
            "model": p["model"],
            "api_key": p["api_key"],
            "base_url": p["base_url"],
            "api_type": "openai",
        }
        for p in provider_chain(mistral_api_key)
    ]
