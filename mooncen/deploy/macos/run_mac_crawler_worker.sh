#!/bin/bash
# run_mac_crawler_worker.sh
# Run MoonCen Crawler Queue Worker on macOS
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${REPO_DIR}"

# Check virtualenv
if [ -d ".venv" ]; then
    PYTHON_BIN=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    echo "Python 3 not found" >&2
    exit 1
fi

export PYTHONUNBUFFERED=1
export TZ=Asia/Seoul
export WORKER_NODE="mac"

echo "Starting MoonCen Crawler Queue Worker on macOS (Node: ${WORKER_NODE})..."
exec "${PYTHON_BIN}" -X utf8 tools/crawler_queue_worker.py \
    --worker-node "${WORKER_NODE}" \
    --poll-interval 5.0 \
    --heartbeat-interval 30.0
