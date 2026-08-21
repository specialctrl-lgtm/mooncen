from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode


PROVIDER = "MUNI_YEYAK_HSCITY_GO_KR_2DFD650A"
PROVIDER_NAME = "화성특례시 통합예약 강좌"
BASE_URL = "https://yeyak.hscity.go.kr"
LIST_PATH = "/1002/3001/lectureList.do"
LIST_URL = f"{BASE_URL}{LIST_PATH}"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.generated_yaml import MUNI_YEYAK_HSCITY_GO_KR_E7FCC3C0 as base  # noqa: E402


base.PROVIDER = PROVIDER
base.PROVIDER_NAME = PROVIDER_NAME
base.LIST_URL = LIST_URL
base.logger = base.setup_logger("Crawler_HwaseongPublicLecture")
base.BRANCH_ADDRESS_MAP.update(
    {
        "화성국민체육센터": "경기도 화성시 봉담읍 동화길 18",
        "화성시생활문화센터": "경기도 화성시 향남읍 향남로 470",
        "병점구보건소": "경기도 화성시 병점3로 23",
        "화성시 보건소": "경기도 화성시 향남읍 3.1만세로 1055",
        "동탄보건소": "경기도 화성시 노작로 226-9",
        "봉담도서관": "경기도 화성시 봉담읍 샘마을1길 8",
        "병점도서관": "경기도 화성시 병점3로 132-6",
    }
)


def list_url(page: int) -> str:
    query = {
        "currentPageNo": str(page),
        "recordCountPerPage": "10",
        "searchCondition": "lectureNm",
        "searchAreaEmd": "",
        "statusCd": "",
        "freeYn": "",
        "targetCd": "",
    }
    return f"{LIST_URL}?{urlencode(query)}"


base.list_url = list_url
_base_collect = base.collect


def normalize_row(row: dict) -> dict:
    row["provider"] = PROVIDER
    row["provider_name"] = PROVIDER_NAME
    row["collection_category"] = "공공예약"
    row["domain_category"] = "공공예약"
    row["source_group"] = "public_reservation"
    row["operator_type"] = "지자체/공공기관"
    if row.get("category") in ["", "도서관"]:
        detail_pairs = row.get("raw_fields", {}).get("detail_pairs", {})
        row["category"] = base.normalize_space(detail_pairs.get("강좌분류")) or "강좌/교육"
    return row


def collect(
    limit: int | None = None,
    max_pages: int = 5,
    timeout: int = 20,
    include_expired: bool = False,
    detail: bool = True,
) -> list[dict]:
    return [
        normalize_row(row)
        for row in _base_collect(
            limit=limit,
            max_pages=max_pages,
            timeout=timeout,
            include_expired=include_expired,
            detail=detail,
        )
    ]


base.collect = collect


def main() -> int:
    parser = argparse.ArgumentParser(description="Hwaseong public lecture crawler")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--per-target-limit", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--detail-limit", type=int)
    args = parser.parse_args()

    started = datetime.now()
    rows = collect(
        limit=args.limit or args.per_target_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
        detail=not args.no_detail,
    )
    saved = base.save_rows(rows) if args.save_db else 0
    print(json.dumps(base.quality(rows), ensure_ascii=False, indent=2))
    print("\nSAMPLE")
    for row in rows[:5]:
        print(
            " | ".join(
                [
                    base.normalize_space(row.get("title")),
                    base.normalize_space(row.get("branch")),
                    base.normalize_space(row.get("address")),
                    base.normalize_space(row.get("period")),
                    base.normalize_space(row.get("target")),
                    base.normalize_space(row.get("fee")),
                    base.normalize_space(row.get("status")),
                ]
            )
        )
    base.logger.info(
        "%s completed collected=%s saved=%s elapsed=%.1fs",
        PROVIDER,
        len(rows),
        saved,
        (datetime.now() - started).total_seconds(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
