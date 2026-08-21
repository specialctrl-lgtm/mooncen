from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

PROVIDER = "BUSAN_RESERVATION"
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_GeneratedYamlTargets import main


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(
        argument == "--all"
        or argument == "--write-registry"
        or argument.startswith("--provider")
        for argument in args
    ):
        raise SystemExit(
            f"{PROVIDER} wrapper has a fixed provider; provider/all/registry overrides are not permitted"
        )
    return main(["--provider", PROVIDER, *args])


if __name__ == "__main__":
    raise SystemExit(run())
