#!/bin/bash
# run_mac_crawler_worker.sh
# Run MoonCen Crawler Queue Worker on macOS
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${WORK_DIR}"

# Check virtualenv in work dir or parent
if [ -f "${WORK_DIR}/.venv/bin/python" ]; then
    PYTHON_BIN="${WORK_DIR}/.venv/bin/python"
elif [ -f "${WORK_DIR}/../.venv/bin/python" ]; then
    PYTHON_BIN="${WORK_DIR}/../.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "Python 3 not found" >&2
    exit 1
fi

export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"
export PYTHONUNBUFFERED=1
export TZ=Asia/Seoul
export WORKER_NODE="mac"

# Automatically update to latest Git code before starting worker
if [ -d "${WORK_DIR}/.git" ] && command -v git >/dev/null 2>&1; then
    echo "Checking for latest MoonCen crawler code from Git..."
    if git fetch origin main --quiet 2>/dev/null; then
        LOCAL_HASH=$(git rev-parse HEAD 2>/dev/null || echo "")
        REMOTE_HASH=$(git rev-parse origin/main 2>/dev/null || echo "")
        if [ -n "$LOCAL_HASH" ] && [ -n "$REMOTE_HASH" ] && [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
            echo "New version detected (${LOCAL_HASH:0:7} -> ${REMOTE_HASH:0:7}). Updating codebase..."
            git pull --ff-only origin main 2>/dev/null || git pull origin main 2>/dev/null || echo "Git pull skipped due to conflict or network."
        else
            echo "Crawler code is up-to-date (${LOCAL_HASH:0:7})."
        fi
    else
        echo "Git remote check skipped (offline or network unreachable)."
    fi
fi

echo "Starting MoonCen Crawler Queue Worker on macOS (Node: ${WORKER_NODE})..."
exec "${PYTHON_BIN}" -X utf8 tools/crawler_queue_worker.py \
    --worker-node "${WORKER_NODE}" \
    --poll-interval 5.0 \
    --heartbeat-interval 30.0
