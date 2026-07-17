"""Smoke tests for ``KilnLoader``.

These tests load real tool fixtures from the on-disk ``registry/tools/``
directory rather than constructed in-memory specs. That mirrors how the
loader is actually used at boot time and catches regressions in the
spec.yaml schema, the impl.py import path, and the dataclass build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kiln_registry.loader import KilnLoader
from kiln_shared.spec import KilnTool

REPO_ROOT = Path(__file__).resolve().parents[3]
CURRENT_DATE_DIR = REPO_ROOT / "registry" / "tools" / "com.kiln.tools.current_date" / "1.0.0"


@pytest.fixture
def loader() -> KilnLoader:
    """A loader with auto_register disabled so the global registry stays clean."""
    return KilnLoader(auto_register=False)


def test_current_date_dir_exists() -> None:
    """Sanity-check the fixture path before any other test runs.

    If this fails the rest of the suite is meaningless — fail fast with
    a clear message rather than letting jsonschema/yaml errors bury it.
    """
    assert CURRENT_DATE_DIR.is_dir(), f"Fixture missing: {CURRENT_DATE_DIR}"
    assert (CURRENT_DATE_DIR / "spec.yaml").is_file()
    assert (CURRENT_DATE_DIR / "impl.py").is_file()


def test_loader_loads_current_date(loader: KilnLoader) -> None:
    """Loading current_date returns a fully-formed KilnTool."""
    tool = loader.load(CURRENT_DATE_DIR)

    assert isinstance(tool, KilnTool)
    assert tool.spec.id == "com.kiln.tools.current_date"
    assert tool.spec.name == "current_date"
    assert tool.spec.version == "1.0.0"
    assert callable(tool.fn)


def test_loaded_tool_invokes_with_fallback(loader: KilnLoader) -> None:
    """Calling the tool returns a dict with the expected keys.

    current_date hits an external API but has a local-time fallback, so
    the call works offline. We assert on the contract (returns a dict
    with ``date`` + ``timestamp`` keys), not on the specific values.
    """
    tool = loader.load(CURRENT_DATE_DIR)
    result = tool.fn(format="%Y-%m-%d")

    assert isinstance(result, dict)
    # Either we got a real result or the fallback both produce date+timestamp.
    # If something went truly wrong the impl returns {"error": "..."}.
    if "error" in result:
        pytest.fail(f"current_date returned an error: {result['error']}")
    assert "date" in result
    assert "timestamp" in result
    assert isinstance(result["date"], str)
    assert isinstance(result["timestamp"], int)
