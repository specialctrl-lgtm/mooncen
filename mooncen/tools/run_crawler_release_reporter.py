"""Run the worker-host reporter from the trusted control release.

The systemd unit invokes this file by absolute path under Python isolated mode,
so a signed crawler artifact cannot shadow ``tools`` or ``ops_agent`` while the
reporter reads a privileged local spool.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--preflight"]:
        from tools.preflight_distributed_crawler_control import main as preflight_main

        return preflight_main(
            [
                "--component",
                "reporter",
                "--env-file",
                "/etc/mooncen/crawler-release-reporter.env",
            ]
        )
    if arguments:
        raise SystemExit("run_crawler_release_reporter.py accepts only --preflight")
    from ops_agent.crawler_release_reporter import main as reporter_main

    return reporter_main([])


if __name__ == "__main__":
    raise SystemExit(main())
