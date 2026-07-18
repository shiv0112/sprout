"""Environment helpers shared across Sprout services.

``required_url`` resolves a service URL from an env var, with a localhost
default that is only honoured when ``SPROUT_ENV=dev``. In any other
environment, the var is required and missing-value raises immediately at
module-import time so deploys fail fast instead of silently pointing at
``localhost``.
"""
from __future__ import annotations

import os


def sprout_env() -> str:
    return os.environ.get("SPROUT_ENV", "dev").lower()


def is_dev() -> bool:
    return sprout_env() == "dev"


def required_url(name: str, dev_default: str) -> str:
    """Return the env var value, the dev default in dev, or raise in prod."""
    val = os.environ.get(name)
    if val:
        return val
    if is_dev():
        return dev_default
    raise RuntimeError(
        f"{name} must be set when SPROUT_ENV != 'dev' (got SPROUT_ENV={sprout_env()!r})."
    )
