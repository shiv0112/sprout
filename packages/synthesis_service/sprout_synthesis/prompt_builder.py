"""
sprout_synthesis.prompt_builder
---------------------------------------
Builds the prompt string for OpenCode CLI and writes CONTEXT.md into the workspace.
"""

from __future__ import annotations

import json
from pathlib import Path

from sprout_synthesis.models import SynthesizeRequest

# -- Sprout spec format reference (embedded) ------------------------------------

_SPEC_FORMAT = """\
sprout_version: "1.0"

tool:
  id: com.sprout.tools.<tool_name>
  name: <tool_name>          # MUST match the Python function name exactly
  version: 1.0.0
  description: <What the tool does — this becomes the LLM function description>
  author: sprout_synthesis

interface:
  inputs:
    - name: <param_name>
      type: <string|integer|float|boolean|array|object>
      description: <what it is>
      required: <true|false>
      default: <optional default value>
      enum: [<for enum type only — omit this line if not an enum>]
  outputs:
    - name: <output_field_name>
      type: <string|integer|float|boolean|array|object>
      description: <what this field contains>

implementation:
  runtime: python3.10
  entrypoint: impl.py
  dependencies: []

testing:
  fixtures:
    - input:
        <param>: <value>
      expected_output_contains:
        - <expected_key_1>
        - <expected_key_2>

metadata:
  tags: [synthesized]
  category: general
  generated_by: sprout_synthesis
"""

_IMPL_RULES = """\
## Implementation Rules (impl.py) — read carefully

- MUST define a global list `REQUIRED_ENV_VARS` at the top of the file listing every environment
  variable the tool needs. Each entry is a dict with `name` and `description`. Example:
  ```python
  REQUIRED_ENV_VARS = [
      {"name": "GOOGLE_MAPS_API_KEY", "description": "Google Maps API key for directions and geocoding"},
  ]
  ```
  If the tool needs no env vars, set it to an empty list: `REQUIRED_ENV_VARS = []`
- MUST define exactly one function whose name matches `tool.name` in spec.yaml exactly
  Example: if `tool.name: geopolitical_analysis` then `def geopolitical_analysis(**kwargs) -> dict`
- Accept all inputs defined in spec as keyword arguments with appropriate type hints and defaults
- Return a dict whose keys match the `expected_output_contains` keys in the test fixtures
- On API/network error, return `{"error": "<concrete reason>"}` — NEVER raise exceptions to the caller
- Only import stdlib modules + dependencies declared in spec.implementation.dependencies
- Keep the implementation concise and production-quality

### NO DUMMY DATA — this is the most important rule

The whole point of Sprout tools is that they call REAL services and return REAL data. A tool
that returns hardcoded values is WORSE THAN NOTHING because it lies to the agent. The
following are forbidden:

  - Hardcoded return values that ignore the inputs (e.g. returning the same date every call)
  - "Fallback" branches that fabricate plausible-looking output when the API fails
  - Mock/example/placeholder data anywhere in the function body
  - `try: ... except: return {"foo": "default value"}` patterns that swallow errors with fake output
  - Returning `datetime.now()` or local computation pretending to be the result of an API call
    (the only exception: a tool whose stated purpose IS local computation, e.g. unit conversion)

If an API legitimately doesn't work (down, rate-limited, requires auth you don't have), the
correct behavior is `return {"error": "<exactly what failed and why>"}`. The test will fail.
That is the CORRECT outcome — it tells the synthesis pipeline to retry with a different API
or report the missing tool to the user. A test that "passes" because the function returned
fabricated data is a much worse failure mode than a test that fails honestly.

### API selection

- **STRONGLY prefer free, open APIs that require no API key.** Use paid/key-gated APIs only when
  there is absolutely no free alternative. If a free API is used, `REQUIRED_ENV_VARS` MUST be `[]`.
- If a key-gated API is unavoidable, declare its env var in `REQUIRED_ENV_VARS` and return
  `{"error": "Missing required env var: <VAR>"}` if it is absent.
- Pick an API whose response shape ACTUALLY contains the fields the spec asks for. Do not
  pick an API and then synthesize the missing fields locally.
- One real API per tool. Do NOT chain together random APIs (Wikipedia + arxiv + web_search)
  hoping one of them produces the right answer. If you can't find one good API, the right
  output is `{"error": "no public API exists for <X>"}` and let the user know.
"""


def _format_inputs(request: SynthesizeRequest) -> str:
    lines = []
    for inp in request.inputs:
        parts = [f"- **{inp.name}** ({inp.type})"]
        if inp.description:
            parts.append(f": {inp.description}")
        if inp.required:
            parts.append(" [required]")
        else:
            parts.append(f" [optional, default={inp.default}]")
        if inp.values:
            parts.append(f" — allowed: {inp.values}")
        lines.append("".join(parts))
    return "\n".join(lines)


def _format_output(request: SynthesizeRequest) -> str:
    if not request.output or not request.output.fields:
        return "Return a dict with relevant fields."
    lines = []
    for f in request.output.fields:
        lines.append(f"- **{f.name}** ({f.type}): {f.description}")
    return "\n".join(lines)


def _build_test_input(request: SynthesizeRequest) -> str:
    """Build a sample test invocation from the inputs."""
    # Mixed value types (str/int/float/bool/list/dict) — use object for mypy.
    sample: dict[str, object] = {}
    for inp in request.inputs:
        if not inp.required and inp.default is not None:
            continue
        t = inp.type.lower()
        if t in ("string", "str"):
            sample[inp.name] = "test"
        elif t in ("integer", "int"):
            sample[inp.name] = 1
        elif t in ("float", "number"):
            sample[inp.name] = 1.0
        elif t in ("boolean", "bool"):
            sample[inp.name] = True
        elif t in ("array", "list"):
            sample[inp.name] = []
        elif t in ("object", "dict"):
            sample[inp.name] = {}
        elif t == "enum" and inp.values:
            sample[inp.name] = inp.values[0]
        else:
            sample[inp.name] = "test"
    return json.dumps(sample)


def write_context(workspace: Path, request: SynthesizeRequest) -> None:
    """Write CONTEXT.md into the workspace directory."""
    context = f"""\
# Sprout Tool Synthesis Context

## Your Task

Generate a Sprout tool with the following requirements:

- **Tool name**: {request.tool_name}
- **Tool ID**: com.sprout.tools.{request.tool_name}
- **Description**: {request.description}

### Inputs
{_format_inputs(request)}

### Expected Output
{_format_output(request)}
"""

    if request.env_vars:
        env_lines = "\n".join(
            f"- `{ev.name}`: {ev.description}" for ev in request.env_vars
        )
        context += f"""
### Required Environment Variables

The implementation MUST read these from `os.environ.get()`. If a required env var is missing, return `{"error": "Missing required env var: <VAR_NAME>"}` — do NOT return mock or fallback data:

{env_lines}
"""

    if request.constraints:
        context += f"""
### Constraints
{request.constraints}
"""

    context += f"""
## Sprout Spec Format (spec.yaml)

Every tool is defined as a YAML spec with this exact structure:

```yaml
{_SPEC_FORMAT}
```

{_IMPL_RULES}

## Testing — CRITICAL

After generating both files, you MUST test the tool thoroughly:

### Step 1: Import test
```bash
cd {workspace}
python -c "from impl import {request.tool_name}; print('Import OK')"
```
If this fails, fix syntax errors in impl.py before proceeding.

### Step 2: Functional test
The default test input uses placeholder values that real APIs may reject (e.g. `city="test"`).
**Replace placeholder values with REAL ones** that the API you chose will actually accept
(e.g. `city="London"`, `symbol="BTC"`, `lat=51.5, lon=-0.13`). Then run:

```bash
cd {workspace}
python -c "from impl import {request.tool_name}; import json; result = {request.tool_name}(**{{REAL_INPUTS}}); print(json.dumps(result, indent=2))"
```

### Verification checklist (in order)
1. The command runs **without any Python errors or tracebacks**
2. The output is a valid dict
3. The output contains the expected keys from the spec
4. The output looks like REAL data the API would have produced — not hardcoded constants,
   not `datetime.now()` masquerading as an API result, not the same value every call

### What to do when the API fails
**Do NOT add a local fallback. Do NOT fabricate data. Do NOT switch to local computation
unless local computation IS the tool's stated purpose.** Instead:

1. First, try a different real input. The placeholder may have been the problem.
2. If the API genuinely doesn't work, try ONE alternative free public API for the same data.
3. If no free public API works, the correct outcome is for `impl.py` to return
   `{{"error": "<exact failure reason>"}}` AND for you to STOP and report:
   *"No public API found for <X>. Synthesis cannot proceed without inventing fake data."*

A test that fails honestly is INFINITELY better than a tool that ships hardcoded lies.
The synthesis pipeline knows how to handle a failed test (retry, ask the user, mark
the tool as needing manual implementation). It cannot recover from a tool that
silently fabricates output.

## Files to Create

Create these two files in the current directory (`{workspace}`):

1. **spec.yaml** — Valid Sprout tool spec following the format above
2. **impl.py** — Python implementation following the rules above
"""

    (workspace / "CONTEXT.md").write_text(context)


def build_prompt(workspace: Path, request: SynthesizeRequest) -> str:
    """Build the prompt string for OpenCode CLI."""
    test_input = _build_test_input(request)

    return (
        f"Read the file CONTEXT.md in this directory for full instructions. "
        f"Generate a Sprout tool called '{request.tool_name}' that: {request.description}. "
        f"Create two files in this directory: spec.yaml and impl.py. "
        f"Follow the Sprout spec format exactly as described in CONTEXT.md. "
        f"IMPORTANT: First test the import works: python -c \"from impl import {request.tool_name}; print('OK')\". "
        f"Then test the full tool with REAL inputs (not placeholders): "
        f'python -c "from impl import {request.tool_name}; import json; print(json.dumps({request.tool_name}(**{test_input}), indent=2))" '
        f"If the API fails, try ONE alternative free public API. If no free API works, return {{\"error\": \"<reason>\"}} — "
        f"do NOT fabricate data or switch to local computation unless local computation IS the tool's stated purpose."
    )
