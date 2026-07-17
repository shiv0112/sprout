# Publishing Kiln to PyPI

Two PyPI packages, both must ship together:

| Package | Wheel size | What it ships |
|---|---|---|
| `kiln-shared` | ~24 KB | SDK shape: `KilnTool`, `KilnToolSpec`, `@kiln_tool`, JSON schema |
| `kiln-registry-api` | ~45 KB | Runtime, four lazy-loaded compilers, optional FastAPI server |

Install paths users get after publish:

```bash
pip install kiln-registry-api                     # SDK only — no heavy deps
pip install "kiln-registry-api[langchain]"        # + LangChain adapter
pip install "kiln-registry-api[ag2]"              # + AG2 / AutoGen
pip install "kiln-registry-api[pydantic_ai]"      # + Pydantic AI
pip install "kiln-registry-api[mistral]"          # + Mistral
pip install "kiln-registry-api[server]"           # + FastAPI registry server
pip install "kiln-registry-api[all]"              # all of the above
```

---

## First-time setup (5 minutes)

### 1. PyPI account
Register at <https://pypi.org/account/register/>. Verify your email.

### 2. API token
- Go to <https://pypi.org/manage/account/token/>
- "Add API token"
- Token name: `kiln-publish` (or anything memorable)
- Scope: **Entire account** for the first push (you can scope to per-project after both packages exist).
- Copy the token — it starts with `pypi-` and is shown only once.

### 3. Export the token in your shell
```bash
export UV_PUBLISH_TOKEN="pypi-XXXXX...your token..."
```
Persist it in `~/.zshrc` or `~/.bashrc` if you want it across shells.

---

## Publish (one command)

```bash
./scripts/publish.sh
```

The script:
1. Cleans `dist/`
2. Builds both wheels (`uv build --package kiln-shared`, `uv build --package kiln-registry-api`)
3. Runs `twine check` (PyPI's metadata validator)
4. Smoke-tests both wheels in a fresh `/tmp/kiln-publish-test` venv
5. **Asks you to confirm** before pushing
6. Pushes `kiln-shared` first (since `kiln-registry-api` depends on it), then `kiln-registry-api`

When done, your packages are live at:
- <https://pypi.org/project/kiln-shared/>
- <https://pypi.org/project/kiln-registry-api/>

### Dry-run on TestPyPI first

```bash
# Token from https://test.pypi.org/manage/account/token/
export UV_PUBLISH_TOKEN="pypi-...test token..."
./scripts/publish.sh --test
```

Install from TestPyPI to verify (real PyPI hosts the deps so add it as extra-index):

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            "kiln-registry-api[langchain]"
```

---

## Cutting a new version

Bump the version in **both** files in lock-step:

- `packages/shared/pyproject.toml` → `version = "1.0.1"`
- `packages/registry_api/pyproject.toml` → `version = "1.0.1"`

Then either:
- Run `./scripts/publish.sh` locally, or
- Tag and push: `git tag v1.0.1 && git push --tags` — the GitHub Actions workflow at `.github/workflows/publish-pypi.yml` will publish for you (requires `PYPI_API_TOKEN` secret in the repo settings).

PyPI rejects re-uploads of the same version — that's the safety net. If you mess up a release, bump the patch number and re-publish.

---

## Notes & gotchas

- **`[tool.uv.sources] kiln-shared = { workspace = true }`** in `packages/registry_api/pyproject.toml` is uv-only — pip and PyPI ignore it. Inside the monorepo it overrides PyPI to use the local workspace; outside, consumers get whatever's published.
- **License declared = `Apache-2.0`** (matches repo `LICENSE`). PyPI's modern SPDX form. The LICENSE file is bundled inside both wheels.
- **The HTTP integrations endpoint** at `GET /tools/{id}/integrations` does not require any framework lib to be installed — it generates snippets purely from text.
- **Tools are not PyPI packages.** Adding/synthesising a new tool changes nothing on PyPI; tools live as `spec.yaml + impl.py` inside the registry server.
