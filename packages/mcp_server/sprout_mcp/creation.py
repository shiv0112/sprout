from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
import yaml

from sprout_shared.env import required_url
from sprout_shared.env_allowlist import DisallowedEnvVarError, validate_env_var_name
from sprout_shared.httpx_client import async_client

logger = logging.getLogger(__name__)

REGISTRY_URL = required_url("SPROUT_REGISTRY_URL", "http://localhost:8766")

TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,}$")
PY_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_PY_TO_JSON = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}
_JSON_TYPES = set(_PY_TO_JSON.values())
_SUPPORTED_TYPES = set(_PY_TO_JSON.keys()) | _JSON_TYPES


class ToolCreationError(ValueError):
    pass


def _canonical_type(value: str, *, field: str) -> str:
    if value in _JSON_TYPES:
        return value
    if value in _PY_TO_JSON:
        return _PY_TO_JSON[value]
    raise ToolCreationError(
        f"{field}: type must be one of {sorted(_JSON_TYPES)} (or Python aliases "
        f"{sorted(_PY_TO_JSON)}), got {value!r}"
    )


def _validate_params(params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in params:
        if not isinstance(raw, dict):
            raise ToolCreationError(f"each param must be an object, got {type(raw).__name__}")
        name = raw.get("name")
        if not isinstance(name, str) or not PY_IDENT_RE.match(name):
            raise ToolCreationError(f"param name must be a valid python identifier, got {name!r}")
        if name in seen:
            raise ToolCreationError(f"duplicate param name: {name}")
        seen.add(name)

        ptype = _canonical_type(raw.get("type", "string"), field=f"param {name}")

        required = raw.get("required", True)
        if not isinstance(required, bool):
            raise ToolCreationError(
                f"param {name}: required must be a boolean, got {type(required).__name__} "
                f"({required!r}) — strings like 'false' are rejected on purpose"
            )

        entry: dict[str, Any] = {
            "name": name,
            "type": ptype,
            "description": str(raw.get("description", "")),
            "required": required,
        }
        if raw.get("default") is not None:
            entry["default"] = raw["default"]
        if raw.get("enum"):
            if not isinstance(raw["enum"], list) or not all(isinstance(x, str) for x in raw["enum"]):
                raise ToolCreationError(f"param {name}: enum must be a list of strings")
            entry["enum"] = list(raw["enum"])
        cleaned.append(entry)
    return cleaned


def _normalize_output(returns: dict[str, Any] | None) -> dict[str, Any]:
    if returns is None:
        return {"name": "result", "type": "object"}
    out_name = returns.get("name", "result")
    out_type = _canonical_type(returns.get("type", "object"), field="returns")
    entry = {"name": out_name, "type": out_type}
    if returns.get("description"):
        entry["description"] = str(returns["description"])
    return entry


def build_spec_yaml(
    *,
    tool_id: str,
    name: str,
    description: str,
    params: list[dict[str, Any]],
    returns: dict[str, Any] | None,
    dependencies: list[str] | None,
    version: str,
    author: str,
    tags: list[str] | None,
    category: str,
    required_env_vars: list[str] | None = None,
) -> str:
    if not TOOL_ID_RE.match(tool_id):
        raise ToolCreationError(
            f"tool_id must be dotted-lowercase like com.sprout.tools.my_tool, got {tool_id!r}"
        )
    if not PY_IDENT_RE.match(name):
        raise ToolCreationError(f"name must be a valid python identifier, got {name!r}")
    stripped = description.strip()
    if len(stripped) < 10:
        raise ToolCreationError(
            "description must be at least 10 characters; write a clear sentence "
            "so the LLM knows when to call this tool"
        )

    spec = {
        "sprout_version": "1.0",
        "tool": {
            "id": tool_id,
            "name": name,
            "version": version,
            "description": stripped,
            "author": author,
        },
        "interface": {
            "inputs": _validate_params(params),
            "outputs": [_normalize_output(returns)],
        },
        "implementation": {
            "runtime": "python3.10",
            "entrypoint": f"{name}.py",
            "dependencies": list(dependencies or []),
            "required_env_vars": _validate_required_env_vars(required_env_vars or []),
        },
        "metadata": {
            "tags": list(tags or []),
            "category": category,
            "generated_by": "mcp_client",
        },
    }
    return yaml.safe_dump(spec, sort_keys=False)


def _validate_required_env_vars(names: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for n in names:
        if not isinstance(n, str):
            raise ToolCreationError(
                f"required_env_vars entries must be strings, got {type(n).__name__}"
            )
        if n in seen:
            raise ToolCreationError(f"duplicate required_env_var: {n!r}")
        try:
            validate_env_var_name(n)
        except DisallowedEnvVarError as exc:
            raise ToolCreationError(str(exc)) from exc
        seen.add(n)
        cleaned.append(n)
    return cleaned


@dataclass(frozen=True)
class EnvVarScan:
    """Result of scanning impl source for env var references."""

    literals: frozenset[str]
    has_dynamic_access: bool
    dynamic_access_lines: tuple[int, ...]


def parse_impl(impl_code: str) -> ast.Module:
    """Parse impl source, raising ``ToolCreationError`` on syntax errors.

    Exposed so the create-tool flow can parse once and feed the same tree to
    every validator — avoiding redundant re-parses.
    """
    try:
        return ast.parse(impl_code)
    except SyntaxError as exc:
        raise ToolCreationError(
            f"impl_code has a Python syntax error: {exc.msg} (line {exc.lineno})"
        ) from exc


def detect_env_var_refs(impl_code: str | ast.Module) -> EnvVarScan:
    """Find every `os.environ[...]`, `os.environ.get(...)`, and `os.getenv(...)` reference.

    Tracks three kinds of aliasing in a single ``ast.walk`` pass:

    - ``import os`` / ``import os as my_os`` → names bound to the ``os`` module
    - ``from os import environ [as X]``      → names bound to ``os.environ``
    - ``from os import getenv [as X]``       → names bound to ``os.getenv``

    The returned ``literals`` set contains the constant string names read;
    ``has_dynamic_access`` is True if any lookup uses a non-literal key (e.g.
    ``os.environ[var]``).

    We deliberately don't resolve shadowing or re-assignment — if a tool author
    does ``os = something_else``, the scan may produce false positives. That's
    acceptable: declared vars are already validated against the allowlist, and
    false-positive detection just adds a harmless declaration.

    Accepts either raw source or a pre-parsed ``ast.Module`` so callers can
    reuse a single parse across multiple validators.
    """
    tree = impl_code if isinstance(impl_code, ast.Module) else parse_impl(impl_code)

    os_aliases: set[str] = {"os"}
    environ_aliases: set[str] = set()
    getenv_aliases: set[str] = set()
    subscripts: list[ast.Subscript] = []
    calls: list[ast.Call] = []

    # One walk: collect imports (to build the alias sets) and candidate nodes
    # (to resolve after the walk completes, against the fully-built sets).
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_aliases.add(alias.asname or "os")
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                bound = alias.asname or alias.name
                if alias.name == "environ":
                    environ_aliases.add(bound)
                elif alias.name == "getenv":
                    getenv_aliases.add(bound)
        elif isinstance(node, ast.Subscript):
            subscripts.append(node)
        elif isinstance(node, ast.Call):
            calls.append(node)

    literals: set[str] = set()
    dynamic_lines: list[int] = []

    def record(node: ast.AST, lineno: int) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
        else:
            dynamic_lines.append(lineno)

    for sub in subscripts:
        if _is_environ_ref(sub.value, os_aliases, environ_aliases):
            record(sub.slice, sub.lineno)

    for call in calls:
        if not call.args:
            continue
        func = call.func
        if isinstance(func, ast.Attribute):
            if func.attr == "get" and _is_environ_ref(func.value, os_aliases, environ_aliases) or (
                func.attr == "getenv"
                and isinstance(func.value, ast.Name)
                and func.value.id in os_aliases
            ):
                record(call.args[0], call.lineno)
        elif isinstance(func, ast.Name) and func.id in getenv_aliases:
            record(call.args[0], call.lineno)

    return EnvVarScan(
        literals=frozenset(literals),
        has_dynamic_access=bool(dynamic_lines),
        dynamic_access_lines=tuple(sorted(set(dynamic_lines))),
    )


def _is_environ_ref(
    node: ast.AST, os_aliases: set[str], environ_aliases: set[str]
) -> bool:
    """True if `node` evaluates to `os.environ` via any known alias."""
    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return isinstance(node.value, ast.Name) and node.value.id in os_aliases
    if isinstance(node, ast.Name):
        return node.id in environ_aliases
    return False


def reconcile_env_vars(
    *,
    detected: EnvVarScan,
    declared: list[str],
) -> None:
    """Enforce the contract between impl source and declared env vars.

    Rules:
      - Dynamic accesses (non-literal keys) are rejected — the sandbox can't
        satisfy them and they'd fail at runtime with a less clear message.
      - Every literal the impl reads must appear in `declared`. Missing
        declarations are rejected so the spec stays the single source of truth
        for "what secrets does this tool touch."

    Declared-but-unused entries are allowed (a helper library might read them
    internally). They surface as `unused_declarations` in the creation response
    but don't block registration.
    """
    if detected.has_dynamic_access:
        raise ToolCreationError(
            "impl_code reads os.environ / os.getenv with a non-literal key "
            f"(line(s) {', '.join(str(n) for n in detected.dynamic_access_lines)}). "
            "The sandbox only exposes declared env vars, so dynamic lookups can "
            "never resolve. Replace the dynamic access with a literal string "
            "(e.g. os.environ['OPENAI_API_KEY']) and list it in required_env_vars."
        )
    declared_set = set(declared)
    undeclared = sorted(detected.literals - declared_set)
    if undeclared:
        raise ToolCreationError(
            f"impl_code reads env var(s) {undeclared} that are not listed in "
            f"implementation.required_env_vars. Add them to required_env_vars "
            f"(every entry must be on the Sprout provider allowlist) so the "
            f"sandbox knows to inject them."
        )


def validate_impl_defines_function(
    impl_code: str | ast.Module, function_name: str
) -> None:
    """Check that `impl_code` has a top-level sync/async def of `function_name`.

    Accepts either raw source or a pre-parsed ``ast.Module``; syntax errors in
    source form are normalized to ``ToolCreationError``.
    """
    if isinstance(impl_code, ast.Module):
        tree = impl_code
    else:
        if not impl_code.strip():
            raise ToolCreationError("impl_code must not be empty")
        tree = parse_impl(impl_code)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return
    raise ToolCreationError(
        f"impl_code must define a top-level function named {function_name!r} "
        f"(class methods and nested defs don't count)"
    )


async def submit_to_registry(
    *,
    spec_yaml: str,
    impl_code: str,
    entrypoint: str,
    user_id: str | None,
) -> dict[str, Any]:
    internal_secret = os.environ.get("SPROUT_INTERNAL_SECRET", "")
    if not internal_secret:
        raise ToolCreationError(
            "SPROUT_INTERNAL_SECRET not configured on the MCP server; cannot authenticate to the registry"
        )

    headers = {"X-Internal-Secret": internal_secret}
    if user_id:
        headers["X-Sprout-User-ID"] = user_id

    files = {
        "spec_file": ("spec.yaml", spec_yaml.encode("utf-8"), "application/x-yaml"),
        "impl_file": (entrypoint, impl_code.encode("utf-8"), "text/x-python"),
    }

    try:
        async with async_client(timeout=60) as client:
            resp = await client.post(
                f"{REGISTRY_URL}/tools/register",
                files=files,
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise ToolCreationError(
            f"registry timed out after 60s while validating the tool "
            f"(fixtures may be too slow): {exc}"
        ) from exc
    except httpx.RequestError as exc:
        raise ToolCreationError(
            f"registry unreachable at {REGISTRY_URL}: {exc}"
        ) from exc

    if resp.status_code >= 400:
        detail: Any
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise ToolCreationError(f"registry rejected tool: {detail}")

    try:
        return resp.json()
    except ValueError as exc:
        raise ToolCreationError(
            f"registry returned a non-JSON response (status {resp.status_code}): "
            f"{resp.text[:200]}"
        ) from exc
