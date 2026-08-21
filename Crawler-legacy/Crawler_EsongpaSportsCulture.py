from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import requests
import yaml
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from Crawler.Crawler_MunicipalYaml import MunicipalDbWriter, score_fields
from DB.course_lifecycle import mark_stale_courses, utc_now
from DB.db_utils import get_db_cursor
from utils import clean_text, setup_logger


PROVIDER = "ESONGPA_SPORTS_CULTURE"
BASE_URL = "https://www.esongpa.or.kr"
LIST_URL = f"{BASE_URL}/lecture/list/20000001"
BRANCH_NAME = "송파구체육문화회관"
BRANCH_CODE = "SONGPA01"
BRANCH_ADDRESS = "서울시 송파구 양산로 15"
BRANCH_PHONE = "02-402-3291"
REPORT_DIR = Path(__file__).resolve().parents[1] / "logs" / "crawler_reports"

logger = setup_logger(__name__, "logs/crawler_esongpa_sports_culture.log")


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": LIST_URL,
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return s


def init_session(s: requests.Session, timeout: int) -> str:
    response = s.get(LIST_URL, timeout=timeout, verify=False)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")
    token_el = soup.select_one('meta[name="_csrf"]')
    header_el = soup.select_one('meta[name="_csrf_header"]')
    if token_el and header_el:
        s.headers.update({header_el.get("content", "X-CSRF-TOKEN"): token_el.get("content", "")})
    today = s.get(f"{BASE_URL}/data/lecture/today", timeout=timeout, verify=False).json().get("today")
    return str(today or datetime.now().strftime("%Y%m%d"))


def post_json(s: requests.Session, endpoint: str, data: dict[str, Any], timeout: int) -> Any:
    response = s.post(f"{BASE_URL}{endpoint}", data=data, timeout=timeout, verify=False)
    response.raise_for_status()
    return response.json()


def provider_course_id(row: dict[str, Any]) -> str:
    raw = "|".join(str(row.get(key) or "") for key in ("comcd", "classCd", "sportsCd", "msportsCd"))
    return raw or hashlib.sha1(str(row).encode("utf-8")).hexdigest()[:20]


def format_krw(value: Any) -> str:
    try:
        amount = int(value or 0)
    except (TypeError, ValueError):
        return clean_text(value)
    return "무료" if amount <= 0 else f"{amount:,}원"


def status_from_row(row: dict[str, Any], today: str) -> str:
    rec_start = str((row.get("grpcd") or {}).get("recSdate") or "")
    rec_end = str((row.get("grpcd") or {}).get("recEdate") or "")
    web_accept_finish = clean_text(row.get("webAcceptFinishYn"))
    remain = row.get("webUser")
    try:
        remain_count = int(remain or 0)
    except (TypeError, ValueError):
        remain_count = 0
    if web_accept_finish == "Y":
        return "CLOSED"
    if rec_start and rec_end and rec_start <= today <= rec_end and remain_count > 0:
        return "OPEN"
    if rec_start and today < rec_start:
        return "SCHEDULED"
    return "CLOSED"


def detail_url(row: dict[str, Any]) -> str:
    # The site opens detail with a POST-only form. Keep a stable, openable
    # source URL while preserving identifiers in the query for traceability.
    params = {
        "comcd": row.get("comcd") or BRANCH_CODE,
        "sportsCd": row.get("sportsCd") or "",
        "msportsCd": row.get("msportsCd") or "",
        "classCd": row.get("classCd") or "",
    }
    return f"{LIST_URL}?{urlencode(params)}"


def schedule_raw(row: dict[str, Any]) -> str:
    day = clean_text(row.get("trainDayNm"))
    start = clean_text(row.get("trainStimeNm"))
    end = clean_text(row.get("trainEtimeNm"))
    if start and end:
        return clean_text(f"{day} {start} ~ {end}")
    return clean_text(" ".join(part for part in [day, row.get("trainTimeNm")] if part))


def period_from_row(row: dict[str, Any]) -> str:
    grpcd = row.get("grpcd") or {}
    start = clean_text(grpcd.get("classSdate") or row.get("repSdate") or "")
    end = clean_text(grpcd.get("classEdate") or row.get("repEdate") or "")
    if start and end:
        return f"{start} ~ {end}"
    return ""


def description_from_row(row: dict[str, Any]) -> str:
    return clean_text(
        " ".join(
            part
            for part in [
                row.get("classDesc"),
                row.get("classNote"),
                row.get("classReady"),
                row.get("bigo"),
            ]
            if part
        )
    )


def row_to_course(row: dict[str, Any], today: str) -> dict[str, Any]:
    max_cost = row.get("maxCost")
    min_cost = row.get("minCost")
    if row.get("freeClassYn") == "Y":
        fee = "무료"
    elif max_cost == min_cost:
        fee = format_krw(max_cost)
    else:
        fee = f"{format_krw(min_cost)} ~ {format_krw(max_cost)}"
    category = " / ".join(
        part
        for part in [
            clean_text(row.get("partCdNm")),
            clean_text(row.get("sportsCdNm")),
            clean_text(row.get("msportsCdNm")),
        ]
        if part
    )
    material_fee = format_krw(row.get("matCost")) if row.get("matCost") else ""
    return {
        "provider": PROVIDER,
        "provider_course_id": provider_course_id(row),
        "title": clean_text(row.get("classNm")),
        "branch": clean_text(row.get("comnm")) or BRANCH_NAME,
        "branch_code": clean_text(row.get("comcd")) or BRANCH_CODE,
        "address": BRANCH_ADDRESS,
        "phone": BRANCH_PHONE,
        "category": category,
        "raw_url": detail_url(row),
        "period": period_from_row(row),
        "schedule_raw": schedule_raw(row),
        "target": clean_text(row.get("classObj")),
        "fee": fee,
        "material_fee": material_fee,
        "material_note": clean_text(row.get("matTeach") or row.get("classReady")),
        "room": clean_text(row.get("placeCdNm")),
        "status": status_from_row(row, today),
        "description": description_from_row(row),
    }


class EsongpaDbWriter(MunicipalDbWriter):
    def save_rows(self, rows: list[dict[str, Any]]) -> int:
        branch_ids: dict[str, str] = {}
        saved = 0
        for row in rows:
            code = clean_text(row.get("branch_code")) or BRANCH_CODE
            branch_id = branch_ids.get(code)
            if not branch_id:
                branch_id = self.save_branch_from_row(row)
                if not branch_id:
                    continue
                branch_ids[code] = branch_id
            if self.save_course(self.normalize_course(row, branch_id)):
                saved += 1
        return saved

    def save_branch_from_row(self, row: dict[str, Any]) -> Optional[str]:
        data = {
            "provider": self.provider,
            "branch_code": clean_text(row.get("branch_code"))[:50],
            "name": clean_text(row.get("branch"))[:100],
            "address": clean_text(row.get("address")),
            "phone": clean_text(row.get("phone")),
            "website_url": BASE_URL,
            "address_source": "crawler",
        }
        with get_db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO branches (provider, branch_code, name, address, phone, website_url, address_source)
                VALUES (%(provider)s, %(branch_code)s, %(name)s, %(address)s, %(phone)s, %(website_url)s, %(address_source)s)
                ON CONFLICT (provider, branch_code)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    address = COALESCE(NULLIF(EXCLUDED.address, ''), branches.address),
                    phone = COALESCE(NULLIF(EXCLUDED.phone, ''), branches.phone),
                    website_url = EXCLUDED.website_url,
                    address_source = COALESCE(EXCLUDED.address_source, branches.address_source),
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                data,
            )
            return str(cursor.fetchone()["id"])


def collect(limit: Optional[int], timeout: int, page_size: int, max_pages: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    s = session()
    today = init_session(s, timeout)
    rows: list[dict[str, Any]] = []
    page = 1
    total_count = 0
    while page <= max_pages and (limit is None or len(rows) < limit):
        start_row = (page - 1) * page_size
        payload = {
            "msportsCd": "all",
            "sportsCd": "all",
            "comcd": BRANCH_CODE,
            "searchValue": "null",
            "yoil": "null",
            "sugang": "YES",
            "pageIndex": str(page),
            "pageSize": str(page_size),
            "startRow": str(start_row),
        }
        data = post_json(s, "/data/getLectureList", payload, timeout)
        result_list = data.get("resultList") or []
        total_count = int(data.get("totalCount") or total_count or 0)
        if not result_list:
            break
        for raw in result_list:
            rows.append(row_to_course(raw, today))
            if limit is not None and len(rows) >= limit:
                break
        if len(rows) >= total_count:
            break
        page += 1
    meta = {
        "pages": page,
        "detail_pages": 0,
        "total_count": total_count,
        "today": today,
        "parser": "esongpa_json_api",
    }
    return rows, meta


def write_report(rows: list[dict[str, Any]], meta: dict[str, Any], saved: int) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"esongpa_sports_culture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml"
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provider": PROVIDER,
        "success": bool(rows),
        "collected": len(rows),
        "saved": saved,
        "meta": meta,
        "fields": score_fields(rows),
        "samples": rows[:5],
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return path


def print_summary(rows: list[dict[str, Any]], meta: dict[str, Any], saved: int, report_path: Path) -> None:
    fields = score_fields(rows)
    print(f"provider={PROVIDER} collected={len(rows)} saved={saved} parser={meta.get('parser')} report={report_path}")
    print(f"pages={meta.get('pages')} total_count={meta.get('total_count')} today={meta.get('today')}")
    print("field_counts " + " ".join(f"{key}={value}" for key, value in fields.items()))
    for row in rows[:5]:
        print(
            "sample "
            f"title={row.get('title')} branch={row.get('branch')} category={row.get('category')} "
            f"schedule={row.get('schedule_raw')} fee={row.get('fee')} status={row.get('status')}"
        )


def run(limit: Optional[int], save_db: bool, mark_stale: bool, timeout: int, page_size: int, max_pages: int) -> list[dict[str, Any]]:
    rows, meta = collect(limit=limit, timeout=timeout, page_size=page_size, max_pages=max_pages)
    saved = 0
    if save_db and rows:
        writer = EsongpaDbWriter(PROVIDER)
        saved = writer.save_rows(rows)
        if mark_stale and saved > 0:
            mark_stale_courses(PROVIDER, utc_now())
    report_path = write_report(rows, meta, saved)
    print_summary(rows, meta, saved, report_path)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl Songpa sports/culture center courses.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(
        limit=args.limit,
        save_db=args.save_db,
        mark_stale=args.mark_stale,
        timeout=args.timeout,
        page_size=args.page_size,
        max_pages=args.max_pages,
    )


if __name__ == "__main__":
    main()
