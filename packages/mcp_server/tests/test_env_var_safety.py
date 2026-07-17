"""Tests for env-var awareness and sandbox safety in kiln_mcp.creation.

Covers the three mechanisms added to close the silent-secret-consumption gap:

1. AST-based detection of `os.environ` / `os.getenv` access (literal + dynamic).
2. Reconciliation between what the impl reads and what the spec declares.
3. Provider allowlist enforcement on declared `required_env_vars`.
"""

from __future__ import annotations

import ast

import pytest
import yaml

from kiln_mcp.creation import (
    EnvVarScan,
    ToolCreationError,
    build_spec_yaml,
    detect_env_var_refs,
    parse_impl,
    reconcile_env_vars,
    validate_impl_defines_function,
)
from kiln_shared.env_allowlist import PROVIDER_ENV_ALLOWLIST

# ── AST detection ──────────────────────────────────────────────────────────────


def test_detect_subscript_access() -> None:
    src = """
import os
def run(q: str) -> dict:
    key = os.environ["OPENAI_API_KEY"]
    return {"ok": True, "k": key[:4]}
"""
    scan = detect_env_var_refs(src)
    assert scan.literals == frozenset({"OPENAI_API_KEY"})
    assert scan.has_dynamic_access is False


def test_detect_environ_dot_get() -> None:
    src = """
import os
def run() -> dict:
    return {"v": os.environ.get("ANTHROPIC_API_KEY", "")}
"""
    scan = detect_env_var_refs(src)
    assert scan.literals == frozenset({"ANTHROPIC_API_KEY"})


def test_detect_os_getenv() -> None:
    src = """
import os
def run() -> dict:
    a = os.getenv("STRIPE_SECRET_KEY")
    b = os.getenv("NOTION_API_KEY", "default")
    return {"a": a, "b": b}
"""
    scan = detect_env_var_refs(src)
    assert scan.literals == frozenset({"STRIPE_SECRET_KEY", "NOTION_API_KEY"})


def test_detect_from_os_import_environ_getenv() -> None:
    src = """
from os import environ, getenv
def run() -> dict:
    a = environ["OPENAI_API_KEY"]
    b = getenv("TAVILY_API_KEY")
    return {"a": a, "b": b}
"""
    scan = detect_env_var_refs(src)
    assert scan.literals == frozenset({"OPENAI_API_KEY", "TAVILY_API_KEY"})


def test_detect_from_os_import_with_alias() -> None:
    src = """
from os import environ as _env
def run() -> dict:
    return {"k": _env["BRAVE_API_KEY"]}
"""
    scan = detect_env_var_refs(src)
    assert scan.literals == frozenset({"BRAVE_API_KEY"})


def test_detect_flags_dynamic_subscript() -> None:
    src = """
import os
def run(name: str) -> dict:
    return {"v": os.environ[name]}
"""
    scan = detect_env_var_refs(src)
    assert scan.literals == frozenset()
    assert scan.has_dynamic_access is True
    assert scan.dynamic_access_lines == (4,)


def test_detect_flags_dynamic_getenv() -> None:
    src = """
import os
def run(name: str) -> dict:
    return {"v": os.getenv(name)}
"""
    scan = detect_env_var_refs(src)
    assert scan.has_dynamic_access is True


def test_detect_empty_when_no_env_access() -> None:
    src = """
def run(x: int) -> dict:
    return {"x": x + 1}
"""
    scan = detect_env_var_refs(src)
    assert scan.literals == frozenset()
    assert scan.has_dynamic_access is False


def test_detect_ignores_unrelated_subscript() -> None:
    src = """
def run(d: dict) -> dict:
    return {"v": d["OPENAI_API_KEY"]}
"""
    scan = detect_env_var_refs(src)
    # A plain dict indexing must not be mistaken for an env read.
    assert scan.literals == frozenset()


def test_detect_rejects_syntax_error() -> None:
    with pytest.raises(ToolCreationError, match="syntax error"):
        detect_env_var_refs("def broken(:")


def test_detect_handles_import_os_as_alias() -> None:
    # Gemini review: previously missed `import os as X`. With aliasing fixed,
    # both the attribute access and the os.getenv call should be picked up.
    src = """
import os as my_os
def run() -> dict:
    a = my_os.environ["OPENAI_API_KEY"]
    b = my_os.getenv("ANTHROPIC_API_KEY")
    return {"a": a, "b": b}
"""
    scan = detect_env_var_refs(src)
    assert scan.literals == frozenset({"OPENAI_API_KEY", "ANTHROPIC_API_KEY"})
    assert scan.has_dynamic_access is False


def test_detect_accepts_prebuilt_ast() -> None:
    # Callers that already parsed the source should reuse the tree instead of
    # paying for a second ast.parse().
    src = """
import os
def run() -> dict:
    return {"k": os.environ["OPENAI_API_KEY"]}
"""
    tree = parse_impl(src)
    assert isinstance(tree, ast.Module)
    scan = detect_env_var_refs(tree)
    assert scan.literals == frozenset({"OPENAI_API_KEY"})


def test_validate_impl_defines_function_accepts_prebuilt_ast() -> None:
    src = "def run(x: int) -> dict:\n    return {'x': x}\n"
    tree = parse_impl(src)
    validate_impl_defines_function(tree, "run")
    with pytest.raises(ToolCreationError, match="top-level function"):
        validate_impl_defines_function(tree, "missing")


# ── Reconciliation ─────────────────────────────────────────────────────────────


def test_reconcile_accepts_declared_literals() -> None:
    scan = EnvVarScan(
        literals=frozenset({"OPENAI_API_KEY"}),
        has_dynamic_access=False,
        dynamic_access_lines=(),
    )
    reconcile_env_vars(detected=scan, declared=["OPENAI_API_KEY"])


def test_reconcile_rejects_dynamic_access() -> None:
    scan = EnvVarScan(
        literals=frozenset(),
        has_dynamic_access=True,
        dynamic_access_lines=(4,),
    )
    with pytest.raises(ToolCreationError, match="non-literal key"):
        reconcile_env_vars(detected=scan, declared=[])


def test_reconcile_rejects_undeclared_literal() -> None:
    scan = EnvVarScan(
        literals=frozenset({"OPENAI_API_KEY"}),
        has_dynamic_access=False,
        dynamic_access_lines=(),
    )
    with pytest.raises(ToolCreationError, match="not listed in implementation.required_env_vars"):
        reconcile_env_vars(detected=scan, declared=[])


def test_reconcile_allows_unused_declaration() -> None:
    scan = EnvVarScan(
        literals=frozenset(),
        has_dynamic_access=False,
        dynamic_access_lines=(),
    )
    # Declared but unused is fine — helper libs may read env themselves.
    reconcile_env_vars(detected=scan, declared=["OPENAI_API_KEY"])


# ── build_spec_yaml with required_env_vars ────────────────────────────────────


def _sample_spec(**overrides):
    defaults = dict(
        tool_id="com.kiln.tools.sample",
        name="sample",
        description="A sample tool that exercises required_env_vars.",
        params=[{"name": "q", "type": "str", "description": "Q", "required": True}],
        returns={"type": "dict"},
        dependencies=None,
        version="1.0.0",
        author="tester",
        tags=None,
        category="general",
    )
    defaults.update(overrides)
    return build_spec_yaml(**defaults)


def test_build_spec_yaml_includes_required_env_vars() -> None:
    yaml_str = _sample_spec(required_env_vars=["OPENAI_API_KEY"])
    parsed = yaml.safe_load(yaml_str)
    assert parsed["implementation"]["required_env_vars"] == ["OPENAI_API_KEY"]


def test_build_spec_yaml_defaults_to_empty_list() -> None:
    yaml_str = _sample_spec()
    parsed = yaml.safe_load(yaml_str)
    assert parsed["implementation"]["required_env_vars"] == []


def test_build_spec_yaml_rejects_non_allowlisted_name() -> None:
    with pytest.raises(ToolCreationError, match="not in the Kiln provider allowlist"):
        _sample_spec(required_env_vars=["AWS_SECRET_ACCESS_KEY"])


def test_build_spec_yaml_rejects_lowercase_name() -> None:
    with pytest.raises(ToolCreationError, match="env var name must match"):
        _sample_spec(required_env_vars=["openai_api_key"])


def test_build_spec_yaml_rejects_duplicates() -> None:
    with pytest.raises(ToolCreationError, match="duplicate"):
        _sample_spec(required_env_vars=["OPENAI_API_KEY", "OPENAI_API_KEY"])


def test_allowlist_contains_expected_providers() -> None:
    # Lock in the core providers so someone casually trimming the allowlist
    # has to consciously remove a test assertion.
    for expected in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MISTRAL_API_KEY"):
        assert expected in PROVIDER_ENV_ALLOWLIST
