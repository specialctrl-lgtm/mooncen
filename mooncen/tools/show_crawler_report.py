from __future__ import annotations

import argparse
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from tools.crawler_report import latest_report_path


def fmt_rate(value) -> str:
    return f"{float(value or 0):5.1f}%"


def print_provider(provider: dict) -> None:
    print(
        f"{provider.get('provider'):8} "
        f"success={str(provider.get('success')):5} "
        f"created={provider.get('created_since', 0):5} "
        f"updated={provider.get('updated_since', 0):5} "
        f"active={provider.get('active_total', 0):6} "
        f"elapsed={provider.get('elapsed_seconds', 0):7}s"
    )

    quality = provider.get("quality", {})
    fields = [
        ("branch", "branch"),
        ("raw_url", "url"),
        ("description", "desc"),
        ("image", "image"),
        ("schedule_raw", "schedule"),
        ("schedule_days", "days"),
        ("schedule_time", "time"),
        ("target_age_group", "age"),
        ("fee", "fee"),
        ("status", "status"),
    ]
    print("  quality:", " ".join(f"{label}={fmt_rate(quality.get(key, {}).get('rate'))}" for key, label in fields))

    missing = provider.get("missing", {})
    weak = {key: value for key, value in missing.items() if value}
    if weak:
        shown = ", ".join(f"{key}:{value}" for key, value in list(weak.items())[:8])
        print(f"  missing: {shown}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Show the latest crawler JSON report")
    parser.add_argument("--file", help="Specific report JSON path")
    args = parser.parse_args()

    path = args.file or latest_report_path()
    if not path:
        print("No crawler report found.")
        return

    with open(path, "r", encoding="utf-8") as report_file:
        report = json.load(report_file)

    print(f"Report: {path}")
    print(f"Cycle : {report.get('started_at')} -> {report.get('finished_at')}")
    print("Summary:", json.dumps(report.get("summary", {}), ensure_ascii=False))
    print()

    for provider in report.get("providers", []):
        print_provider(provider)
        print()


if __name__ == "__main__":
    main()
