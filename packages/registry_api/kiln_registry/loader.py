"""
kiln_registry/loader.py
───────────────────────
KilnLoader: load a tool from a spec.yaml + implementation.py on disk.

This is the entry point for synthesis-generated tools.
The synthesis pipeline writes two files into the registry directory:

    spec.yaml          <- validated against kiln.schema.json
    <entrypoint>.py    <- pure Python, no framework imports

KilnLoader reads both, validates the spec, dynamically imports the
function, runs the test fixtures, and registers the result in the
SQLite registry — producing the same KilnTool object that the
@kiln_tool decorator produces for hand-written tools.

The pipeline:

    spec.yaml + implementation.py
          |  validate (JSON Schema)
          |  import function
          |  run test fixtures
          |  register in SQLite
          |
        KilnTool  ->  any adapter  ->  any framework

Usage:
    loader = KilnLoader()

    # Load & register one versioned tool
    tool = loader.load("registry/tools/com.kiln.tools.weather/1.0.0")

    # Run test fixtures without registering
    report = loader.test("registry/tools/com.kiln.tools.weather/1.0.0")
    print(report)   # {"passed": 2, "failed": 0, "results": [...]}

    # Load every tool in the registry directory
    tools = loader.load_all("registry/tools")
"""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

import jsonschema
import yaml

import kiln_shared
from kiln_shared.env_allowlist import DisallowedEnvVarError, validate_env_var_name
from kiln_shared.spec import KilnTool, KilnToolSpec, ToolParam, ToolReturn

from .registry import register

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA_PATH = Path(kiln_shared.__file__).parent / "schema" / "kiln.schema.json"

# ── YAML type -> Kiln type string ─────────────────────────────────────────────

_TYPE_MAP: dict[str, str] = {
    "string":  "str",
    "integer": "int",
    "number":  "float",
    "boolean": "bool",
    "array":   "list",
    "object":  "dict",
}


# ── KilnLoader ───────────────────────────────────────────────────────────────

class KilnLoader:
    """
    Loads KilnTools from spec.yaml + implementation.py on disk.

    Identical output to @kiln_tool — both produce a KilnTool that the
    runtime and all adapters treat exactly the same way.

    This class is what the synthesis pipeline writes into and what the
    planner uses to hydrate the registry at startup.
    """

    def __init__(self, auto_register: bool = True):
        """
        Args:
            auto_register: Register each loaded tool in the global SQLite
                           registry automatically after loading. Set to
                           False if you only want to test without registering.
        """
        self._schema        = self._load_schema()
        self._auto_register = auto_register

    # ── Public API ─────────────────────────────────────────────────────────────

    def load(self, tool_dir: str | Path) -> KilnTool:
        """
        Load a single tool from its versioned directory.

        The directory must contain:
            spec.yaml           <- validated against kiln.schema.json
            <entrypoint>.py     <- pure Python implementation

        Args:
            tool_dir: Versioned directory, e.g.
                      registry/tools/com.kiln.tools.weather/1.0.0

        Returns:
            A KilnTool registered in the SQLite registry and ready for
            use with any adapter (ag2, mistral, pydantic_ai, langchain).

        Raises:
            FileNotFoundError  - spec.yaml or entrypoint file missing
            jsonschema.ValidationError - spec.yaml fails schema check
            AttributeError     - function name not found in entrypoint file
        """
        tool_dir = Path(tool_dir)

        raw = self._read_spec(tool_dir)
        self._validate(raw, tool_dir / "spec.yaml")

        fn      = self._import_function(tool_dir, raw)
        spec    = self._build_spec(raw)
        tool    = KilnTool(spec=spec, fn=fn)

        if self._auto_register:
            register(tool)

        logger.info(f"Loaded: {tool.id}  v{tool.spec.version}  from {tool_dir}")
        return tool

    def test(self, tool_dir: str | Path) -> dict:
        """
        Run the test fixtures defined in spec.yaml without registering.

        Used by the synthesis loop to decide whether to publish
        or feed the error back for another generation attempt.

        Returns:
            {
                "passed":  int,
                "failed":  int,
                "results": [
                    {"fixture": 1, "passed": True,  "error": None},
                    {"fixture": 2, "passed": False, "error": "Missing keys: ['success']"},
                ]
            }
        """
        tool_dir = Path(tool_dir)
        raw      = self._read_spec(tool_dir)
        fn       = self._import_function(tool_dir, raw)
        fixtures = raw.get("testing", {}).get("fixtures", [])

        results = []
        for i, fixture in enumerate(fixtures):
            inputs   = fixture["input"]
            expected = fixture["expected_output_contains"]
            try:
                output  = fn(**inputs)
                if not isinstance(output, dict):
                    results.append({
                        "fixture": i + 1,
                        "passed":  False,
                        "error":   f"Expected dict output, got {type(output).__name__}: {output!r:.200}",
                    })
                    continue
                missing = [k for k in expected if k not in output]
                if missing:
                    results.append({
                        "fixture": i + 1,
                        "passed":  False,
                        "error":   f"Output missing required keys: {missing}",
                    })
                else:
                    results.append({"fixture": i + 1, "passed": True, "error": None})
            except Exception as exc:
                results.append({
                    "fixture": i + 1,
                    "passed":  False,
                    "error":   f"{type(exc).__name__}: {exc}",
                })

        passed = sum(1 for r in results if r["passed"])
        return {"passed": passed, "failed": len(results) - passed, "results": results}

    def load_all(self, registry_dir: str | Path) -> list[KilnTool]:
        """
        Scan a registry directory and load the latest version of every tool.

        Expected layout:
            registry_dir/
                com.kiln.tools.weather/
                    1.0.0/
                        spec.yaml
                        weather.py
                com.kiln.tools.resy_booking/
                    1.0.0/
                        spec.yaml
                        resy_booking.py

        Tools that fail to load are skipped with a warning.
        """
        registry_dir = Path(registry_dir)
        tools: list[KilnTool] = []

        for tool_id_dir in sorted(registry_dir.iterdir()):
            if not tool_id_dir.is_dir():
                continue
            versions = sorted(
                [v for v in tool_id_dir.iterdir() if v.is_dir()],
                key=lambda v: v.name,
            )
            if not versions:
                continue
            latest = versions[-1]
            try:
                tools.append(self.load(latest))
            except Exception as exc:
                logger.warning(f"Skipped {tool_id_dir.name}: {exc}")

        return tools

    # ── Private helpers ────────────────────────────────────────────────────────

    def _load_schema(self) -> dict:
        with open(_SCHEMA_PATH) as f:
            return json.load(f)

    def _read_spec(self, tool_dir: Path) -> dict:
        spec_path = tool_dir / "spec.yaml"
        if not spec_path.exists():
            raise FileNotFoundError(f"spec.yaml not found in {tool_dir}")
        with open(spec_path) as f:
            raw = yaml.safe_load(f)

        # Accept both kiln_version and babel_version for backwards compatibility
        # during migration from Babel to Kiln.
        if "babel_version" in raw and "kiln_version" not in raw:
            raw["kiln_version"] = raw["babel_version"]

        return raw

    def _validate(self, raw: dict, spec_path: Path) -> None:
        """Raise jsonschema.ValidationError with a clean message if invalid."""
        try:
            jsonschema.validate(instance=raw, schema=self._schema)
        except jsonschema.ValidationError as exc:
            raise jsonschema.ValidationError(
                f"Invalid spec.yaml at {spec_path}:\n  {exc.message} "
                f"(path: {' → '.join(str(p) for p in exc.absolute_path)})"
            ) from exc

    def _import_function(self, tool_dir: Path, raw: dict):
        """Dynamically import the entrypoint file and extract the named function."""
        entrypoint = raw["implementation"]["entrypoint"]
        fn_name    = raw["tool"]["name"]
        impl_path  = tool_dir / entrypoint

        if not impl_path.exists():
            raise FileNotFoundError(
                f"Entrypoint '{entrypoint}' not found in {tool_dir}"
            )

        module_spec = importlib.util.spec_from_file_location(impl_path.stem, impl_path)
        if module_spec is None or module_spec.loader is None:
            raise ImportError(
                f"Could not build a module spec for {impl_path}. "
                f"The file exists but importlib refused to load it — "
                f"check that the path is a valid Python source file."
            )
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)

        fn = getattr(module, fn_name, None)
        if fn is None:
            available = [n for n in dir(module) if not n.startswith("_") and callable(getattr(module, n))]
            raise AttributeError(
                f"Function '{fn_name}' not found in {impl_path}. "
                f"Available callables: {available}"
            )
        if not callable(fn):
            raise AttributeError(f"'{fn_name}' in {impl_path} is not callable.")

        return fn

    def _build_spec(self, raw: dict) -> KilnToolSpec:
        """Convert parsed YAML dict -> KilnToolSpec (same type all adapters use)."""
        tool_meta = raw["tool"]
        iface     = raw["interface"]
        meta      = raw.get("metadata", {})
        impl      = raw.get("implementation", {})

        params = [
            ToolParam(
                name=inp["name"],
                type=_TYPE_MAP.get(inp.get("type", "string"), "str"),
                description=inp.get("description", ""),
                required=inp.get("required", True),
                default=inp.get("default"),
                enum=inp.get("enum"),
            )
            for inp in iface.get("inputs", [])
        ]

        required_env_vars = _load_required_env_vars(
            impl.get("required_env_vars", []), tool_id=tool_meta["id"]
        )

        return KilnToolSpec(
            id=tool_meta["id"],
            name=tool_meta["name"],
            description=tool_meta["description"],
            params=params,
            returns=ToolReturn(type="dict"),
            version=str(tool_meta.get("version", "1.0.0")),
            author=tool_meta.get("author", ""),
            tags=meta.get("tags", []),
            category=meta.get("category", "general"),
            required_env_vars=required_env_vars,
        )


def _load_required_env_vars(raw: object, *, tool_id: str) -> list[str]:
    if raw in (None, [], ""):
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"{tool_id}: implementation.required_env_vars must be a list, "
            f"got {type(raw).__name__}"
        )
    seen: set[str] = set()
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise ValueError(
                f"{tool_id}: required_env_vars entries must be strings, "
                f"got {type(entry).__name__}"
            )
        if entry in seen:
            raise ValueError(f"{tool_id}: duplicate required_env_var {entry!r}")
        try:
            validate_env_var_name(entry)
        except DisallowedEnvVarError as exc:
            raise ValueError(f"{tool_id}: {exc}") from exc
        seen.add(entry)
        out.append(entry)
    return out
