"""Run crawler database preflight from the trusted control checkout.

Worker services intentionally execute signed release code from
``/opt/mooncen-crawler/current``.  Invoking ``python -m tools...`` from that
directory would let a release shadow the installer-owned preflight package,
so systemd calls this absolute wrapper under isolated mode instead.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    from tools.preflight_distributed_crawler_control import main as preflight_main

    return preflight_main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
