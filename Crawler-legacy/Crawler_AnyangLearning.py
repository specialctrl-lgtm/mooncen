from __future__ import annotations

import sys
from pathlib import Path

PROVIDER = "ANYANG_LIFELONG_LEARNING"
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.generated_yaml.manual_generic_crawler import run_cli


if __name__ == "__main__":
    raise SystemExit(run_cli(PROVIDER))
