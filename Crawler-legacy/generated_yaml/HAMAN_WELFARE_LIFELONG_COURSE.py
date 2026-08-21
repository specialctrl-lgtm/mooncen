from __future__ import annotations

import sys
from pathlib import Path


PROVIDER = "HAMAN_WELFARE_LIFELONG_COURSE"
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_GeneratedYamlTargets import main


def run() -> int:
    args = sys.argv[1:]
    if "--provider" not in args and "--all" not in args:
        args = ["--provider", PROVIDER, *args]
    sys.argv = [sys.argv[0], *args]
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
