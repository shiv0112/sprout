"""
kiln_synthesis.config
--------------------
Settings via pydantic-settings, loaded from environment variables.

Env var mapping (prefix: KILN_SYNTHESIS_):
  KILN_SYNTHESIS_CALLBACK_URL      → callback_url
  KILN_SYNTHESIS_INTERNAL_SECRET   → internal_secret
  KILN_SYNTHESIS_WORKSPACE_DIR     → workspace_dir
  KILN_SYNTHESIS_LOG_DIR           → log_dir
  KILN_SYNTHESIS_OPENCODE_MODEL    → opencode_model
  KILN_SYNTHESIS_OPENCODE_TIMEOUT  → opencode_timeout

Provider API keys (e.g. OPENAI_API_KEY) are read by OpenCode from its own config.
"""

import os

from pydantic_settings import BaseSettings


def _default_callback_url() -> str:
    if os.environ.get("KILN_ENV", "dev").lower() == "dev":
        return "http://host.docker.internal:8766/synthesis/callback"
    raise RuntimeError(
        "KILN_SYNTHESIS_CALLBACK_URL must be set when KILN_ENV != 'dev' "
        "(expected something like http://registry-api.kiln.svc.cluster.local:8766/synthesis/callback)"
    )


class Settings(BaseSettings):
    opencode_model: str = "mistral/codestral-latest"

    opencode_timeout: int = 600

    callback_url: str = ""

    internal_secret: str = ""

    workspace_dir: str = "/tmp/opencode_workspace"

    log_dir: str = "/app/logs"

    model_config = {"env_prefix": "KILN_SYNTHESIS_"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings  # noqa: PLW0603
    if _settings is None:
        s = Settings()
        stripped = s.callback_url.strip() if s.callback_url else ""
        s.callback_url = stripped or _default_callback_url()
        _settings = s
    return _settings
