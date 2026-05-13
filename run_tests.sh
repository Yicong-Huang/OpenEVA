#!/usr/bin/env bash
# Run the Eva test suite.
#
# We used to pass `--ignore=tests/test_pty_terminal.py` here because
# the pty tests were flaky when first added. They've been stable for
# several iters now (24 passing, full + isolated runs), so the suite
# runs in full -- excluding them was masking a chunk of pty_manager
# coverage from the report.
set -e

cd "$(dirname "$0")"

# Pick the interpreter in priority order:
#   1. $EVA_PYTHON env override
#   2. project-local .venv (the OSS convention; works for any checkout)
#   3. plain `python3` on PATH (assumes the user installed deps there)
# Mirrors the same resolution used by bin/restart-server.sh so a single
# venv setup works for both server and tests.
if [ -n "${EVA_PYTHON:-}" ]; then
    PY="$EVA_PYTHON"
elif [ -x "./.venv/bin/python" ]; then
    PY="./.venv/bin/python"
else
    PY="python3"
fi
# Discover test trees dynamically:
#   - `core/test/` is always there (OSS / framework tests).
#   - Each extension (sibling-of-core folder carrying an
#     `extension.conf` marker) contributes its own `<ext>/test/`.
# This loop mirrors `core.extensions.discover()` so adding a new
# extension folder doesn't require editing this script.
REPO="$(cd "$(dirname "$0")" && pwd)"
TEST_PATHS=("$REPO/core/test")
for marker in "$REPO"/*/extension.conf; do
    [ -f "$marker" ] || continue
    ext_dir="$(dirname "$marker")"
    [ "$(basename "$ext_dir")" = "core" ] && continue
    [ -d "$ext_dir/test" ] && TEST_PATHS+=("$ext_dir/test")
done

"$PY" -m pytest "${TEST_PATHS[@]}" -v "$@"
