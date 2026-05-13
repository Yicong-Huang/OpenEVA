#!/bin/bash
# Eva server restart with zombie-prevention.
#
# Why this exists: a sequence of background-launched `nohup python
# server.py &` restarts during /loop iterations occasionally left
# stale server processes alive when `pkill` returned before the SIGTERM
# took effect, then the new process started anyway. Each zombie kept a
# write-mode handle on data/eva.db, contributing to DB-lock contention.
# This script waits for actual death + port-free before launching.

set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Pick the interpreter in priority order:
# 1. EVA_PYTHON env override (takes precedence; per-machine setups)
# 2. $REPO/.venv/bin/python (project-local venv -- the OSS convention)
# 3. plain `python3` on PATH (fallback; user is responsible for deps)
if [ -n "${EVA_PYTHON:-}" ]; then
    PYTHON="$EVA_PYTHON"
elif [ -x "$REPO/.venv/bin/python" ]; then
    PYTHON="$REPO/.venv/bin/python"
else
    PYTHON="python3"
fi
LOG="${EVA_LOG:-/tmp/eva-server.log}"
PORT="${EVA_PORT:-8021}"
# Launch is `python core/src/server.py`. Match on the relative path
# `core/src/server.py` plus the python interpreter so we don't kill
# unrelated `server.py` scripts owned by other tools.
PKILL_PATTERN="python.*core/src/server\\.py"

# 1. Send SIGTERM to every running server process.
pkill -f "$PKILL_PATTERN" 2>/dev/null || true

# 2. Wait up to 10s for them to actually die. Poll every 0.5s.
for _ in $(seq 1 20); do
    if ! pgrep -f "$PKILL_PATTERN" >/dev/null; then
        break
    fi
    sleep 0.5
done

# 3. Force-kill anything still alive after the grace window.
if pgrep -f "$PKILL_PATTERN" >/dev/null; then
    echo "[restart-server] graceful kill failed; SIGKILLing remainders" >&2
    pkill -9 -f "$PKILL_PATTERN" 2>/dev/null || true
    sleep 1
fi

# 4. Confirm the listen port is free before we re-bind. Avoids a
# silent "address in use" failure on the new process.
for _ in $(seq 1 10); do
    if ! ss -tln 2>/dev/null | grep -q ":${PORT} "; then
        break
    fi
    sleep 0.5
done

# 5. Start the new server detached. server.py lives under
#    `core/src/`. PYTHONPATH starts with `core/src` (always present)
#    plus every sibling-of-core extension's `<ext>/src` (discovered
#    via the `extension.conf` marker, matching conftest.py + the
#    `core.common.extensions.discover` runtime).
cd "$REPO"
EXT_PATHS=""
for marker in "$REPO"/*/extension.conf; do
    [ -f "$marker" ] || continue
    ext_dir="$(dirname "$marker")"
    [ "$(basename "$ext_dir")" = "core" ] && continue
    [ -d "$ext_dir/src" ] && EXT_PATHS="${EXT_PATHS}:${ext_dir}/src"
done
PYTHONPATH="$REPO/core/src${EXT_PATHS}${PYTHONPATH:+:$PYTHONPATH}" \
    nohup "$PYTHON" core/src/server.py > "$LOG" 2>&1 &
disown

# 6. Wait for /api/projects to respond so the caller knows we're up.
for _ in $(seq 1 20); do
    if curl -sf "http://localhost:${PORT}/api/projects" >/dev/null 2>&1; then
        echo "[restart-server] up on :${PORT} (log: ${LOG})"
        exit 0
    fi
    sleep 0.5
done

echo "[restart-server] WARNING: server did not respond within 10s" >&2
echo "[restart-server] last log lines:" >&2
tail -10 "$LOG" >&2
exit 1
