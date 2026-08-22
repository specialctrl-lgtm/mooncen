from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
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
from utils.outbound_http import SafeSession


PROVIDER = "ESONGPA_SPORTS_CULTURE"
BASE_URL = "https://www.esongpa.or.kr"
LIST_URL = f"{BASE_URL}/lecture/list/20000001"
BRANCH_NAME = "송파구체육문화회관"
BRANCH_CODE = "SONGPA01"
BRANCH_ADDRESS = "서울시 송파구 양산로 15"
BRANCH_PHONE = "02-402-3291"
REPORT_DIR = Path(__file__).resolve().parents[1] / "logs" / "crawler_reports"

logger = setup_logger(__name__, "logs/crawler_esongpa_sports_culture.log")

MAX_LIMIT = 100_000
MAX_PAGE_SIZE = 500
MAX_PAGES = 200
MAX_TIMEOUT_SECONDS = 120


def _validate_options(limit: Optional[int], timeout: int, page_size: int, max_pages: int) -> None:
    if limit is not None and not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS}")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    if not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")


def _yyyymmdd(value: Any) -> str:
    text = clean_text(value)
    if not re.fullmatch(r"\d{8}", text):
        return ""
    try:
        return datetime.strptime(text, "%Y%m%d").strftime("%Y%m%d")
    except ValueError:
        return ""


def session() -> requests.Session:
    s = SafeSession()
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
    response = None
    for attempt in range(2):
        try:
            response = s.get(LIST_URL, timeout=timeout, verify=True, allow_redirects=False)
            if 300 <= response.status_code < 400:
                raise requests.TooManyRedirects("Songpa provider redirects are not allowed")
            response.raise_for_status()
            break
        except requests.RequestException:
            if attempt:
                raise
            time.sleep(0.2)
    if response is None:
        raise RuntimeError("Songpa session initialization failed")
    soup = BeautifulSoup(response.text, "lxml")
    token_el = soup.select_one('meta[name="_csrf"]')
    header_el = soup.select_one('meta[name="_csrf_header"]')
    if token_el and header_el:
        header_name = clean_text(header_el.get("content"))
        token = clean_text(token_el.get("content"))
        if header_name.upper() in {"X-CSRF-TOKEN", "X-XSRF-TOKEN"} and token and len(token) <= 512:
            s.headers[header_name] = token
    today_response = None
    for attempt in range(2):
        try:
            today_response = s.get(
                f"{BASE_URL}/data/lecture/today",
                timeout=timeout,
                verify=True,
                allow_redirects=False,
            )
            if 300 <= today_response.status_code < 400:
                raise requests.TooManyRedirects("Songpa provider redirects are not allowed")
            today_response.raise_for_status()
            break
        except requests.RequestException:
            if attempt:
                raise
            time.sleep(0.2)
    if today_response is None:
        raise RuntimeError("Songpa date endpoint failed")
    today_payload = today_response.json()
    today = _yyyymmdd(today_payload.get("today")) if isinstance(today_payload, dict) else ""
    return today or datetime.now().strftime("%Y%m%d")


def post_json(s: requests.Session, endpoint: str, data: dict[str, Any], timeout: int) -> Any:
    if endpoint != "/data/getLectureList":
        raise ValueError("unsupported Songpa API endpoint")
    for attempt in range(2):
        try:
            response = s.post(
                f"{BASE_URL}{endpoint}",
                data=data,
                timeout=timeout,
                verify=True,
                allow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise requests.TooManyRedirects("Songpa provider redirects are not allowed")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Songpa API response must be an object")
            return payload
        except requests.RequestException:
            if attempt:
                raise
            time.sleep(0.2)
    raise AssertionError("unreachable")


def provider_course_id(row: dict[str, Any]) -> str:
    raw = "|".join(str(row.get(key) or "") for key in ("comcd", "classCd", "sportsCd", "msportsCd"))
    if raw.replace("|", ""):
        return raw
    canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:20]


def format_krw(value: Any) -> str:
    try:
        amount = int(value or 0)
    except (TypeError, ValueError):
        return clean_text(value)
    if amount < 0:
        return ""
    return "무료" if amount == 0 else f"{amount:,}원"


def status_from_row(row: dict[str, Any], today: str) -> str:
    group = row.get("grpcd") if isinstance(row.get("grpcd"), dict) else {}
    rec_start = _yyyymmdd(group.get("recSdate"))
    rec_end = _yyyymmdd(group.get("recEdate"))
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
    grpcd = row.get("grpcd") if isinstance(row.get("grpcd"), dict) else {}
    start = _yyyymmdd(grpcd.get("classSdate") or row.get("repSdate"))
    end = _yyyymmdd(grpcd.get("classEdate") or row.get("repEdate"))
    if start and end:
        return f"{start[:4]}-{start[4:6]}-{start[6:]} ~ {end[:4]}-{end[4:6]}-{end[6:]}"
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


def row_to_course(row: dict[str, Any], today: str) -> Optional[dict[str, Any]]:
    title = clean_text(row.get("classNm"))
    class_code = clean_text(row.get("classCd"))
    if not title or not class_code:
        return None
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
        "title": title,
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
            "address": clean_text(row.get("address"))[:2_000],
            "phone": clean_text(row.get("phone"))[:100],
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
    _validate_options(limit, timeout, page_size, max_pages)
    rows: list[dict[str, Any]] = []
    page = 1
    pages_fetched = 0
    total_count = 0
    invalid_rows = 0
    duplicate_rows = 0
    stalled = False
    seen_ids: set[str] = set()
    with session() as s:
        today = init_session(s, timeout)
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
            if not isinstance(result_list, list):
                raise ValueError("Songpa API resultList must be an array")
            try:
                total_count = max(0, int(data.get("totalCount") or total_count or 0))
            except (TypeError, ValueError) as exc:
                raise ValueError("Songpa API totalCount must be an integer") from exc
            pages_fetched += 1
            if not result_list:
                break
            page_added = 0
            for raw in result_list:
                if not isinstance(raw, dict):
                    invalid_rows += 1
                    continue
                try:
                    course = row_to_course(raw, today)
                except Exception as exc:
                    invalid_rows += 1
                    logger.warning("Songpa course row parse failed: %s", exc)
                    continue
                if not course:
                    invalid_rows += 1
                    continue
                identity = clean_text(course.get("provider_course_id"))
                if identity in seen_ids:
                    duplicate_rows += 1
                    continue
                seen_ids.add(identity)
                rows.append(course)
                page_added += 1
                if limit is not None and len(rows) >= limit:
                    break
            if len(rows) >= total_count:
                break
            if page_added == 0:
                stalled = True
                break
            page += 1
    capped = bool(limit is None and page > max_pages and len(rows) < total_count)
    complete = bool(
        limit is None
        and not capped
        and not stalled
        and not invalid_rows
        and len(rows) >= total_count
    )
    meta = {
        "pages": pages_fetched,
        "detail_pages": 0,
        "total_count": total_count,
        "today": today,
        "parser": "esongpa_json_api",
        "invalid_rows": invalid_rows,
        "duplicate_rows": duplicate_rows,
        "stalled": stalled,
        "capped": capped,
        "complete": complete,
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
    crawl_started_at = utc_now()
    rows, meta = collect(limit=limit, timeout=timeout, page_size=page_size, max_pages=max_pages)
    saved = 0
    if save_db and rows:
        writer = EsongpaDbWriter(PROVIDER)
        saved = writer.save_rows(rows)
        if mark_stale and saved == len(rows) and meta.get("complete"):
            mark_stale_courses(PROVIDER, crawl_started_at)
        elif mark_stale:
            raise RuntimeError("stale cleanup refused because the Songpa crawl was partial")
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
    args = parser.parse_args()
    if args.mark_stale and not args.save_db:
        parser.error("--mark-stale requires --save-db")
    if args.mark_stale and args.limit is not None:
        parser.error("--mark-stale cannot be used with --limit")
    return args


def main() -> int:
    args = parse_args()
    try:
        rows = run(
            limit=args.limit,
            save_db=args.save_db,
            mark_stale=args.mark_stale,
            timeout=args.timeout,
            page_size=args.page_size,
            max_pages=args.max_pages,
        )
    except Exception as exc:
        logger.error("Songpa crawler failed closed: %s: %s", type(exc).__name__, exc)
        return 1
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
