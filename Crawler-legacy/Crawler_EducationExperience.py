from __future__ import annotations

import sys
from pathlib import Path


PROVIDERS = [
    "MUNI_RESERVE_ANSAN_GO_KR_8236CAF0",
    "MUNI_RESERVE_ANSAN_GO_KR_02253999",
]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_GeneratedYamlTargets import main


def run() -> int:
    args = sys.argv[1:]
    if "--provider" not in args and "--all" not in args:
        provider_args = []
        for provider in PROVIDERS:
            provider_args.extend(["--provider", provider])
        args = [*provider_args, *args]
    sys.argv = [sys.argv[0], *args]
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
