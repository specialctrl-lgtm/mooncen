from __future__ import annotations

import argparse
import re
import sys
from collections import deque
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_Lotte import LotteCrawler
from DB.db_utils import get_db_cursor


GROUP_LOG_RE = re.compile(
    r"Found\s+\d+\s+LOTTE grouped course options:\s+"
    r"(?P<course_id>\d{4}-\d{4}-[A-Za-z0-9_-]+-[A-Za-z0-9_-]+)"
)
COURSE_ID_RE = re.compile(
    r"^(?P<branch>\d{4})-(?P<year>\d{4})-"
    r"(?P<semester>[A-Za-z0-9_-]+)-(?P<lecture>[A-Za-z0-9_-]+)$"
)
MAX_REPAIR_IDS = 100_000
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 1_000


def grouped_course_ids_from_lines(lines: Iterable[str]) -> set[str]:
    return {
        match.group("course_id")
        for line in lines
        if (match := GROUP_LOG_RE.search(line))
    }


def lotte_url_from_course_id(course_id: str) -> str:
    match = COURSE_ID_RE.fullmatch(course_id)
    if not match:
        raise ValueError(f"invalid LOTTE provider course id: {course_id}")
    return (
        "https://culture.lotteshopping.com/application/search/view.do"
        f"?brchCd={match.group('branch')}"
        f"&yy={match.group('year')}"
        f"&lectSmsterCd={match.group('semester')}"
        f"&lectCd={match.group('lecture')}"
    )


def load_lotte_branch_ids() -> dict[str, str]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT branch_code, id
              FROM branches
             WHERE provider = 'LOTTE'
               AND branch_code ~ '^[0-9]{4}$'
            """
        )
        rows = cursor.fetchall()
    return {str(row["branch_code"]): str(row["id"]) for row in rows}


def repair_grouped_options(
    course_ids: Iterable[str],
    *,
    limit: int = MAX_REPAIR_IDS,
    delay_seconds: float = 0.2,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    if not 1 <= limit <= MAX_REPAIR_IDS:
        raise ValueError(f"limit must be between 1 and {MAX_REPAIR_IDS}")
    if not 0 <= delay_seconds <= 5:
        raise ValueError("delay_seconds must be between 0 and 5")
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")

    pending = deque(sorted(set(course_ids))[:limit])
    branch_ids = load_lotte_branch_ids()
    crawler = LotteCrawler()
    crawler._existing_course_ids_by_raw_url = (
        crawler._load_existing_course_ids_by_raw_url()
    )
    crawler._detail_request_delay_seconds = lambda: delay_seconds
    processed: set[str] = set()
    summary = {
        "logged_ids": len(pending),
        "families": 0,
        "saved": 0,
        "skipped": 0,
        "errors": 0,
    }

    try:
        while pending:
            batch_ids: list[str] = []
            while pending and len(batch_ids) < batch_size:
                course_id = pending.popleft()
                if course_id not in processed:
                    batch_ids.append(course_id)
            if not batch_ids:
                continue

            try:
                batch_results = crawler.scrape_course_details(
                    [
                        {"url": lotte_url_from_course_id(course_id)}
                        for course_id in batch_ids
                    ]
                )
            except Exception as exc:
                print(
                    "LOTTE grouped repair batch failed "
                    f"batch_count={len(batch_ids)} error_type={type(exc).__name__}",
                    file=sys.stderr,
                )
                processed.update(batch_ids)
                summary["errors"] += len(batch_ids)
                continue

            for course_id, course_data in zip(
                batch_ids,
                batch_results,
                strict=True,
            ):
                if course_id in processed:
                    continue
                if not isinstance(course_data, list) or not course_data:
                    processed.add(course_id)
                    summary["skipped"] += 1
                    continue

                try:
                    family_ids = {
                        str(item.get("provider_course_id") or "")
                        for item in course_data
                        if item.get("provider_course_id")
                    }
                    processed.add(course_id)
                    processed.update(family_ids)
                    summary["families"] += 1

                    for item in course_data:
                        branch_id = branch_ids.get(
                            str(item.get("branch_code") or "")
                        )
                        if not branch_id:
                            print(
                                "LOTTE grouped repair missing branch "
                                f"course_id={item.get('provider_course_id') or course_id} "
                                f"branch_code={item.get('branch_code') or ''}",
                                file=sys.stderr,
                            )
                            summary["errors"] += 1
                            continue
                        if crawler.save_course(item, branch_id):
                            summary["saved"] += 1
                except Exception as exc:
                    print(
                        "LOTTE grouped repair failed "
                        f"course_id={course_id} error_type={type(exc).__name__}",
                        file=sys.stderr,
                    )
                    summary["errors"] += 1
    finally:
        crawler._close_driver()
        crawler.http_session.close()

    if crawler.had_errors:
        summary["errors"] += 1
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair LOTTE grouped option fields from a completed crawl log."
    )
    parser.add_argument("log_path", type=Path)
    parser.add_argument("--limit", type=int, default=MAX_REPAIR_IDS)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.log_path.is_file():
        raise SystemExit(f"log file does not exist: {args.log_path}")
    course_ids = grouped_course_ids_from_lines(
        args.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    )
    if not course_ids:
        print("No LOTTE grouped course IDs were found.")
        return 1

    summary = repair_grouped_options(
        course_ids,
        limit=args.limit,
        delay_seconds=args.delay,
        batch_size=args.batch_size,
    )
    print(
        "LOTTE grouped repair "
        + " ".join(f"{key}={value}" for key, value in summary.items())
    )
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
