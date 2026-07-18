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
  GROQ_API_KEY          Groq key (enables the Groq provider)
  OPENAI_API_KEY        OpenAI key (enables the OpenAI provider)
  NVIDIA_API_KEY        NVIDIA NIM key (enables the NIM provider)
  MISTRAL_API_KEY       Mistral key (enables the Mistral provider / fallback)
  GROQ_BASE_URL           Groq base URL           (default: https://api.groq.com/openai/v1)
  GROQ_MODEL_REASONING    Groq reasoning model    (default: openai/gpt-oss-120b)
  GROQ_MODEL_FAST         Groq fast/non-thinking  (default: llama-3.3-70b-versatile)
  SPROUT_OPENAI_MODEL     OpenAI model id         (default: gpt-4o-mini)
  SPROUT_NIM_MODEL        NIM model id            (default: z-ai/glm-5.2)
  SPROUT_MISTRAL_MODEL    Mistral model id        (default: mistral-large-latest)
  SPROUT_LLM_PRIMARY      "groq" | "openai" | "mistral" | "nim"   (default: groq)
"""
from __future__ import annotations

import os

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"

# Groq runs a strong reasoning model by default and a fast non-thinking model
# for smaller tasks. Both support tool-calling; gpt-oss-120b keeps its reasoning
# in a separate channel, so JSON/tool output in `content` stays clean.
DEFAULT_GROQ_REASONING_MODEL = "openai/gpt-oss-120b"
DEFAULT_GROQ_FAST_MODEL = "llama-3.3-70b-versatile"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_NIM_MODEL = "z-ai/glm-5.2"
DEFAULT_MISTRAL_MODEL = "mistral-large-latest"


def _groq_base_url() -> str:
    return os.environ.get("GROQ_BASE_URL", GROQ_BASE_URL).strip() or GROQ_BASE_URL


def _groq_reasoning_model() -> str:
    return os.environ.get("GROQ_MODEL_REASONING", DEFAULT_GROQ_REASONING_MODEL).strip() or DEFAULT_GROQ_REASONING_MODEL


def _groq_fast_model() -> str:
    return os.environ.get("GROQ_MODEL_FAST", DEFAULT_GROQ_FAST_MODEL).strip() or DEFAULT_GROQ_FAST_MODEL


def _openai_model() -> str:
    return os.environ.get("SPROUT_OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL


def _nim_model() -> str:
    return os.environ.get("SPROUT_NIM_MODEL", DEFAULT_NIM_MODEL).strip() or DEFAULT_NIM_MODEL


def _mistral_model() -> str:
    return os.environ.get("SPROUT_MISTRAL_MODEL", DEFAULT_MISTRAL_MODEL).strip() or DEFAULT_MISTRAL_MODEL


def provider_chain(mistral_api_key: str = "", reasoning: bool = True) -> list[dict]:
    """Ordered list of available LLM providers (primary first).

    A provider is included only when its API key is present. ``mistral_api_key``
    (usually the per-request key) takes precedence over ``MISTRAL_API_KEY``.
    ``SPROUT_LLM_PRIMARY`` ("groq" | "openai" | "mistral" | "nim") chooses which
    runs first; the default is **Groq**, falling back to Mistral then NVIDIA NIM.

    ``reasoning=True`` (default) uses Groq's strong reasoning model
    (gpt-oss-120b); ``reasoning=False`` uses the fast non-thinking model
    (llama-3.3-70b-versatile) — pass it for smaller/cheaper tasks.

    OpenAI participates only when it is the explicit primary (it needs its own
    billing), so it never sits silently in the fallback path.

    Raises:
        RuntimeError: when no provider has a key.
    """
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "").strip()
    mistral_key = (mistral_api_key or os.environ.get("MISTRAL_API_KEY", "")).strip()

    # Reasoning model by default; fast non-thinking model for smaller tasks.
    groq_model = _groq_reasoning_model() if reasoning else _groq_fast_model()

    providers: dict[str, dict] = {}
    if groq_key:
        providers["groq"] = {"name": "groq", "model": groq_model, "api_key": groq_key, "base_url": _groq_base_url()}
    if openai_key:
        providers["openai"] = {"name": "openai", "model": _openai_model(), "api_key": openai_key, "base_url": OPENAI_BASE_URL}
    if nvidia_key:
        providers["nim"] = {"name": "nvidia-nim", "model": _nim_model(), "api_key": nvidia_key, "base_url": NIM_BASE_URL}
    if mistral_key:
        providers["mistral"] = {"name": "mistral", "model": _mistral_model(), "api_key": mistral_key, "base_url": MISTRAL_BASE_URL}

    primary = os.environ.get("SPROUT_LLM_PRIMARY", "groq").strip().lower()
    # Fallback order after the primary: Groq → Mistral → NVIDIA NIM. NIM is last
    # because its serverless big models can stall. OpenAI only when it's primary.
    fallback_order = ("groq", "mistral", "nim")
    order = [primary] + [k for k in fallback_order if k != primary]
    chain = [providers[k] for k in order if k in providers]

    if not chain:
        raise RuntimeError(
            "No LLM provider configured. Set GROQ_API_KEY (Groq), OPENAI_API_KEY "
            "(OpenAI), NVIDIA_API_KEY (NVIDIA NIM), and/or MISTRAL_API_KEY (Mistral)."
        )
    return chain


def ag2_config_list(mistral_api_key: str = "", reasoning: bool = True) -> list[dict]:
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
        for p in provider_chain(mistral_api_key, reasoning)
    ]
