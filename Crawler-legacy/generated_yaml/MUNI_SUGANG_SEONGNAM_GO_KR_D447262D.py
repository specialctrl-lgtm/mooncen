from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROVIDER = "MUNI_SUGANG_SEONGNAM_GO_KR_D447262D"
PROVIDER_NAME = "성남 배움숲 분당구청 시민정보화교육"
OFFICE_CODE = "OFFICE_00000670"
OFFICE_BRANCH = "분당구청 시민정보화교육"
OFFICE_ADDRESS = "경기도 성남시 분당구 분당로 50"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler import Crawler_SeongnamBaeumsoop as base  # noqa: E402


def _is_expired(row: dict[str, Any]) -> bool:
    end_date = base.clean_text(row.get("end_date"))
    if not end_date:
        return False
    try:
        return datetime.strptime(end_date, "%Y-%m-%d").date() < datetime.now().date()
    except ValueError:
        return False


def configure_base() -> None:
    base.PROVIDER = PROVIDER
    base.DEFAULT_OFFICES = [{"office_code": OFFICE_CODE, "branch": OFFICE_BRANCH}]
    base.OFFICE_ADDRESS_MAP[OFFICE_CODE] = OFFICE_ADDRESS

    def discover_single_office() -> list[dict[str, str]]:
        return [{"office_code": OFFICE_CODE, "branch": OFFICE_BRANCH}]

    def skip_practice_and_ended(row: dict[str, Any]) -> bool:
        title = base.clean_text(row.get("title"))
        if "접수연습용" in title or "실제강의 아님" in title:
            return True
        if any(pattern in title for pattern in base.PRACTICE_TITLE_PATTERNS):
            return True
        return _is_expired(row)

    base.discover_offices_from_files = discover_single_office
    base.should_skip_course = skip_practice_and_ended


def run(
    *,
    limit: int | None,
    save_db: bool,
    mark_stale: bool,
    max_pages: int,
    timeout: int,
    detail: bool,
) -> list[dict[str, Any]]:
    configure_base()
    return base.run(
        limit=limit,
        save=save_db,
        mark_stale=mark_stale,
        office_limit=1,
        max_pages=max_pages,
        timeout=timeout,
        detail=detail,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seongnam Baeumsoop Bundang information education crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--office-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(
        limit=args.limit or args.per_target_limit,
        save_db=args.save_db,
        mark_stale=args.mark_stale,
        max_pages=args.max_pages,
        timeout=args.timeout,
        detail=not args.no_detail,
    )


if __name__ == "__main__":
    main()
