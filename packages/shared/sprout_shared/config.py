"""
sprout_shared.config
------------------
Centralized configuration for Sprout services.
Auth-related settings shared across all backend services.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SproutConfig:
    """Immutable configuration loaded from environment variables."""

    clerk_domain: str
    clerk_secret_key: str
    internal_secret: str

    @classmethod
    def from_env(cls) -> SproutConfig:
        return cls(
            clerk_domain=os.environ.get("CLERK_DOMAIN", ""),
            clerk_secret_key=os.environ.get("CLERK_SECRET_KEY", ""),
            internal_secret=os.environ.get("SPROUT_INTERNAL_SECRET", ""),
        )


_config: SproutConfig | None = None


def get_config() -> SproutConfig:
    global _config  # noqa: PLW0603
    if _config is None:
        _config = SproutConfig.from_env()
    return _config
