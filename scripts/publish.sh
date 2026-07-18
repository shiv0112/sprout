#!/usr/bin/env bash
# scripts/publish.sh — build + publish sprout-shared and sprout-registry-api to PyPI
#
# Usage:
#   export UV_PUBLISH_TOKEN="pypi-XXXXX"   # mint at https://pypi.org/manage/account/token/
#   ./scripts/publish.sh                    # publishes to PyPI
#   ./scripts/publish.sh --test             # publishes to TestPyPI for a dry-run
#
# The script always:
#   1. Cleans dist/
#   2. Builds both wheels
#   3. Runs `twine check` (PyPI's metadata validator)
#   4. Smoke-tests the wheels in a fresh venv
#   5. Asks for confirmation
#   6. Uploads both packages (sprout-shared first, since registry depends on it)

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT"

# Reject *any* extra args too — a stray word after `--test` (typo, shell
# glob, copy-paste accident) must never be ignored silently, because the
# next step of this script publishes to production PyPI.
if [[ $# -gt 1 ]]; then
  echo "ERROR: Too many arguments. Got $#: $*" >&2
  echo "Usage: $0 [--test]" >&2
  exit 2
fi

PUBLISH_URL=""
case "${1:-}" in
  "")
    echo "→ Publishing to PyPI"
    ;;
  --test)
    PUBLISH_URL="--publish-url https://test.pypi.org/legacy/"
    echo "→ Publishing to TestPyPI"
    ;;
  -h|--help)
    echo "Usage: $0 [--test]"
    echo "  (no args)   publish to PyPI"
    echo "  --test      publish to TestPyPI"
    exit 0
    ;;
  *)
    echo "ERROR: Unknown argument '$1'." >&2
    echo "Usage: $0 [--test]" >&2
    echo "A mistyped flag must not silently publish to production, so" >&2
    echo "this script rejects anything it doesn't recognise." >&2
    exit 2
    ;;
esac

if [[ -z "${UV_PUBLISH_TOKEN:-}" ]]; then
  echo "ERROR: UV_PUBLISH_TOKEN is not set. Mint one at"
  echo "       https://pypi.org/manage/account/token/   (or testpypi.org for --test)"
  echo "       then:  export UV_PUBLISH_TOKEN='pypi-...'"
  exit 1
fi

echo "→ Cleaning dist/"
rm -rf dist

echo "→ Building wheels"
uv build --package sprout-shared
uv build --package sprout-registry-api

echo
echo "→ Built artefacts:"
ls -lh dist/

echo
echo "→ Validating metadata with twine check"
uvx twine check dist/*

echo
echo "→ Smoke-testing in a clean venv (/tmp/sprout-publish-test)"
rm -rf /tmp/sprout-publish-test
python3 -m venv /tmp/sprout-publish-test
/tmp/sprout-publish-test/bin/pip install --quiet \
  dist/sprout_shared-*-py3-none-any.whl \
  dist/sprout_registry_api-*-py3-none-any.whl
/tmp/sprout-publish-test/bin/python -c "
from sprout_shared import SproutTool, SproutToolSpec
from sprout_registry.runtime import SproutRuntime, ADAPTERS
assert set(ADAPTERS) == {'ag2', 'langchain', 'pydantic_ai', 'mistral'}
print('  smoke test passed')
"

echo
read -p "→ All checks green. Push to ${PUBLISH_URL:-PyPI}? [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

echo
echo "→ Publishing sprout-shared (first — sprout-registry-api depends on it)"
# shellcheck disable=SC2086
uv publish $PUBLISH_URL dist/sprout_shared-*

echo
echo "→ Publishing sprout-registry-api"
# shellcheck disable=SC2086
uv publish $PUBLISH_URL dist/sprout_registry_api-*

echo
echo "✓ Done. View at:"
if [[ -n "$PUBLISH_URL" ]]; then
  echo "  https://test.pypi.org/project/sprout-shared/"
  echo "  https://test.pypi.org/project/sprout-registry-api/"
else
  echo "  https://pypi.org/project/sprout-shared/"
  echo "  https://pypi.org/project/sprout-registry-api/"
fi
