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

echo "Starting MoonCen Crawler Queue Worker on macOS (Node: ${WORKER_NODE})..."
exec "${PYTHON_BIN}" -X utf8 tools/crawler_queue_worker.py \
    --worker-node "${WORKER_NODE}" \
    --poll-interval 5.0 \
    --heartbeat-interval 30.0
