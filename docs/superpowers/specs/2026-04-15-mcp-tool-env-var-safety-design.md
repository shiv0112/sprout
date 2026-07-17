# MCP Tool Creation: Env Var Awareness & Sandbox Safety

**Date:** 2026-04-15
**Status:** Proposed
**Scope:** `packages/mcp_server`, `packages/registry_api`, tool spec schema

## Problem

Kiln lets any authenticated user create a tool via the MCP server's `kiln_create_tool`, and any other user can then invoke it. When a tool needs secrets (e.g. `OPENAI_API_KEY`), three gaps exist today:

1. **Silent creation.** `kiln_create_tool` inspects impl only for syntax and a top-level function definition. If the generated Python reads `os.environ["OPENAI_API_KEY"]`, nothing tells the AI client (and therefore the human user) that a key needs to be configured in Kiln before the tool can run.
2. **Undeclared secret consumption.** The sandbox at execution time currently injects the invoking user's full `tool_env_vars` dict from Clerk `private_metadata` (see `packages/mcp_server/kiln_mcp/user_env.py:15`). A malicious or careless tool author can therefore read any env var the invoking user has set — including ones the author never "declared." There is no server-side contract about which vars a tool is allowed to touch.
3. **No provider allowlist.** A tool can declare (explicitly or implicitly) any env var name it likes. A bad actor can publish a tool that references `AWS_SECRET_ACCESS_KEY` or `GITHUB_TOKEN` and hope some user has them set.

Each user bringing their own keys (confirmed — `fetch_user_env_vars(user_id)` uses the invoking user's Clerk ID, not the author's) is the right baseline model, but it only isolates *whose* keys load — not *what code* sees them once loaded.

## Goals

- **G1.** When `kiln_create_tool` returns, the AI client receives a structured list of env vars the tool needs and which of them the creator has already set, so it can tell the user what to configure next.
- **G2.** The sandbox injects only env vars the tool has **declared** in its spec. Undeclared `os.environ[...]` reads return empty. Declaration becomes the contract.
- **G3.** Declared env var names must belong to a curated allowlist of known providers. Additions to the allowlist are a registry-side code change, not something a tool author can do via the MCP.

## Non-goals

- Author-only vs public execution trust tiers. (Deferred — separate spec.)
- Egress analysis / network-call review for secret-consuming tools. (Deferred.)
- Automatic key provisioning from the AI client's own environment. (Out of scope — users configure keys via the Kiln UI.)
- Migrating tools registered before this spec. Existing tools continue to work under the old injection model until re-registered; see Migration.

## Design

### 1. Spec schema extension

Add a new optional field under `implementation`:

```yaml
implementation:
  runtime: python3.10
  entrypoint: foo.py
  dependencies: []
  required_env_vars:         # NEW, optional, defaults to []
    - OPENAI_API_KEY
```

Rules (enforced by `packages/mcp_server/kiln_mcp/creation.py:build_spec_yaml` and mirrored in the registry's spec loader):

- Each entry must match `^[A-Z][A-Z0-9_]*$`.
- Each entry must be in the **provider allowlist** (see §3).
- Duplicate entries rejected.
- The list is the complete set the sandbox will expose. Anything not listed is invisible to the tool at runtime.

### 2. Creation-time detection and reminder

In `kiln_create_tool` (`packages/mcp_server/kiln_mcp/main.py:197`), after `validate_impl_defines_function` and before `submit_to_registry`:

**a. Scan impl_code via AST** for env var references:

- `os.environ["NAME"]`, `os.environ.get("NAME", ...)` (Subscript and Attribute-Call)
- `os.getenv("NAME", ...)`
- `environ["NAME"]` / `getenv("NAME")` when `os.environ` / `os.getenv` are imported as `from os import ...`

Extract the **literal string** in the first argument / subscript. Non-literal accesses (e.g. `os.environ[var]` where `var` is computed) are **rejected** with `ToolCreationError`: under the sandbox contract any undeclared name will be absent from the environment, so dynamic lookups are guaranteed to fail at runtime. Forcing them out at creation time gives the author a clear error instead of a runtime `KeyError`/`None` surprise.

**b. Reconcile against `required_env_vars` in the spec:**

- Any detected var **not** in `required_env_vars` → reject with `ToolCreationError("impl_code reads env var X that is not declared in implementation.required_env_vars")`. Forces authors to declare.
- Any declared var **not** detected in impl → warning in the response (`unused_declarations`), not a hard error (covers cases like helper libraries that read env themselves).

**c. Allowlist check:**

- Any declared var not in the provider allowlist → reject with `ToolCreationError("env var X is not in the Kiln provider allowlist; supported: [...]")`.

**d. Cross-check against creator's saved keys:**

- If `user_id` is present, call `fetch_user_env_vars(user_id)` (already cached 5 min).
- Build `required_env_vars: [{name, already_set: bool}]` for the response.
- If `user_id` is None (unauthenticated dev/stdio), return the list with `already_set: null`.

**e. Response shape** (additive; preserves current registry response fields):

```json
{
  "success": true,
  "tool_id": "...",
  "version": "...",
  "required_env_vars": [
    {"name": "OPENAI_API_KEY", "already_set": false},
    {"name": "STRIPE_SECRET_KEY", "already_set": true}
  ],
  "setup_hint": "Missing keys can be added at Kiln → Settings → Tool Env Vars. The creator must set them to run the tool; every other invoking user must set their own.",
  "mcp_catalog": {...}
}
```

The AI client's LLM sees the structured fields and phrases the reminder to the user in natural language.

### 3. Provider allowlist

Stored as a module-level constant in a new file `packages/shared/kiln_shared/env_allowlist.py`:

```python
PROVIDER_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "MISTRAL_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "COHERE_API_KEY",
    "HUGGINGFACE_API_KEY",
    "STRIPE_SECRET_KEY",
    "SERPAPI_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_API_KEY",
    "NOTION_API_KEY",
    "LINEAR_API_KEY",
})
```

Both `kiln_mcp.creation` and `kiln_registry` import from this shared module. Adding a provider is a one-line PR reviewed by a maintainer — not something an MCP caller can do. Keeping it in `kiln_shared` avoids a registry-HTTP roundtrip during creation validation.

### 4. Sandbox injection contract

At tool execution time (registry-side, wherever the sandboxed subprocess is spawned — typically `packages/registry_api/kiln_registry/...`):

- Load the tool's parsed `spec.yaml`.
- Read `implementation.required_env_vars` (default `[]`).
- Intersect with the **invoking user's** `tool_env_vars` from `fetch_user_env_vars(user_id)`.
- Pass **only that intersection** into the subprocess env. Everything else the parent process has (including the registry's own secrets, other users' cached data, etc.) stays out of the child env.
- If a declared var is missing from the user's set, **omit it from the env entirely** (do not set it to an empty string). This matches standard Python idioms: `os.environ["X"]` raises `KeyError`, `os.getenv("X")` returns `None`. An explicit `""` would bypass `if os.getenv("X"):` truthiness checks and silently forward empty credentials to upstream APIs, producing confusing 401s instead of clear "not configured" errors.
- We don't pre-fail if a declared var is absent — the tool's own `os.environ["X"]` raise gives a clearer stack than a synthetic server error, and we avoid an extra Clerk round-trip on the hot path.

This replaces any current behavior of passing the whole `tool_env_vars` dict into the sandbox.

### 5. Tests

New tests in `packages/mcp_server/tests/`:

- `test_env_var_detection.py` — AST detection across all four access patterns, including `from os import environ` aliasing; verifies non-literal accesses are ignored; verifies undeclared detected vars are rejected.
- `test_allowlist.py` — declared var outside allowlist is rejected; allowlist entries pass.
- `test_creation_env_reminder.py` — full `kiln_create_tool` flow asserts response includes `required_env_vars` with correct `already_set` flags, given a fake Clerk env.

Registry-side tests in `packages/registry_api/tests/`:

- `test_sandbox_env_contract.py` — asserts only declared vars reach the subprocess env; verifies undeclared `os.environ[...]` reads return empty; verifies invoker-not-author key isolation.

### 6. Migration

Existing tools in `registry/tools/` were registered without `required_env_vars`. Behavior for them under the new contract:

- Spec loader treats missing field as `[]`.
- Under the new sandbox, they'll receive an empty env for any `os.environ[...]` they have — which is the safe default but may break their behavior.
- **Mitigation:** one-shot audit script (`scripts/audit_legacy_env_vars.py`) scans every existing spec's impl for env var references and writes a migration PR that adds `required_env_vars` to each spec. Gated behind allowlist; any tool that references a non-allowlisted var fails the audit and requires manual review.

Audit output committed as part of the implementation PR so no tool breaks silently.

## Architecture

```
kiln_create_tool (MCP)
  │
  ├── build_spec_yaml ──────────────► validates required_env_vars against allowlist
  │
  ├── validate_impl_defines_function
  │
  ├── detect_env_var_refs (NEW, AST) ─► reconcile with declared list
  │
  ├── fetch_user_env_vars(creator_id) ─► build already_set flags
  │
  └── submit_to_registry ──► registry persists spec with required_env_vars
                              │
                              ▼
                      later: tool invocation (User B)
                              │
                      fetch_user_env_vars(User B's id)
                              │
                      intersect with spec.required_env_vars
                              │
                      spawn sandbox with ONLY that intersection
```

## Security properties after this spec

- ✅ A tool can no longer silently read env vars it didn't declare. Declaration is visible in `spec.yaml` and reviewable by the invoking user via the registry UI.
- ✅ A tool cannot declare arbitrary env var names to fish for credentials — allowlist is enforced at creation and at registry-side spec load.
- ✅ The AI client gets structured info at creation to tell the creator which of their own keys to configure.
- ⚠️ A tool author can still exfiltrate the invoking user's declared key (e.g. log `OPENAI_API_KEY` to a remote server). Mitigating this needs trust tiers + egress review — explicitly deferred.
- ⚠️ Users must trust the Kiln-maintained allowlist. A compromised maintainer could add a sensitive var. Outside this spec.

## Open questions

- Should `already_set: null` (unauthenticated creation) suppress the reminder entirely, or include the list so a dev can eyeball it? Current design: include it.
- Do we want a per-user override allowing "local" env var names (e.g. `MY_CUSTOM_API`) for private tools? Current design: no — allowlist is global. Revisit if users ask.
