from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.maintenance.backfill_library_usage_info import parse_args, run


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
