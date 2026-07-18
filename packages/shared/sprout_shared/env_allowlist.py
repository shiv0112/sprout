"""Curated list of environment variable names Sprout tools may declare.

A tool's ``implementation.required_env_vars`` entries must all be members of
this set. This prevents a malicious tool from declaring `AWS_SECRET_ACCESS_KEY`
or similarly sensitive names and fishing for them in users' saved env vars.

Adding a provider is a one-line change in this file, reviewed like any other
registry-side code change — it is not something a tool author can request via
the MCP.
"""

from __future__ import annotations

import re

# Policy: this list contains single-service API keys whose blast radius, if
# leaked by a malicious tool, is bounded to that service (and typically
# capped/rotatable via the provider's dashboard). Broad-scope session
# credentials — GitHub PATs, Slack bot tokens, cloud-provider keys — are
# deliberately excluded: their compromise gives repo-wide / workspace-wide /
# account-wide access, which is exactly the fishing target the allowlist is
# meant to prevent. Those integrations should use per-call OAuth flows instead
# of an env-var injected into sandboxed code.
PROVIDER_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "MISTRAL_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "COHERE_API_KEY",
    "HUGGINGFACE_API_KEY",
    "REPLICATE_API_TOKEN",
    "STRIPE_SECRET_KEY",
    "SERPAPI_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_API_KEY",
    "NOTION_API_KEY",
    "LINEAR_API_KEY",
    "ELEVENLABS_API_KEY",
    "RESEND_API_KEY",
    "SENDGRID_API_KEY",
})

ENV_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class DisallowedEnvVarError(ValueError):
    """Raised when a declared env var is not in ``PROVIDER_ENV_ALLOWLIST``."""


def validate_env_var_name(name: str) -> None:
    """Reject names that aren't upper-snake or aren't on the allowlist.

    Raises ``DisallowedEnvVarError`` with a message listing the supported names
    so the caller can surface it to the user directly.
    """
    if not isinstance(name, str) or not ENV_VAR_NAME_RE.match(name):
        raise DisallowedEnvVarError(
            f"env var name must match ^[A-Z][A-Z0-9_]*$, got {name!r}"
        )
    if name not in PROVIDER_ENV_ALLOWLIST:
        raise DisallowedEnvVarError(
            f"{name!r} is not in the Sprout provider allowlist. "
            f"Supported: {sorted(PROVIDER_ENV_ALLOWLIST)}"
        )
