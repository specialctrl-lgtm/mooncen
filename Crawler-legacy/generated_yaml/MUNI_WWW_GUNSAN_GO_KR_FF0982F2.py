from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup


PROVIDER = "MUNI_WWW_GUNSAN_GO_KR_FF0982F2"
PROVIDER_NAME = "군산시청"
BASE_URL = "https://www.gunsan.go.kr"
SOURCE_URL = "https://www.gunsan.go.kr/main/m140"
COURSE_URL = "https://lll.gunsan.go.kr/pro/course.php"
COURSE_LIST_URL = "https://lll.gunsan.go.kr/pro/course.php?pm=list"
DEFAULT_BRANCH = "군산시 평생학습정보망"
DEFAULT_ADDRESS = "전북특별자치도 군산시"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_id(*parts: object) -> str:
    raw = "|".join(clean_text(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": SOURCE_URL,
        }
    )
    return session


def fetch_soup(session: requests.Session, url: str, timeout: int, *, data: dict[str, str] | None = None) -> BeautifulSoup:
    if data is None:
        response = session.get(url, timeout=timeout, verify=False)
    else:
        response = session.post(url, data=data, timeout=timeout, verify=False)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding or "euc-kr"
    return BeautifulSoup(response.text, "html.parser")


def normalize_status(value: str) -> str:
    text = clean_text(value)
    if any(token in text for token in ("신청", "접수", "모집")) and not any(token in text for token in ("마감", "종료")):
        return "OPEN"
    if any(token in text for token in ("예정", "대기")):
        return "SCHEDULED"
    if any(token in text for token in ("마감", "종료", "완료")):
        return "CLOSED"
    return "SCHEDULED" if text else "OPEN"


def branch_code(value: str) -> str:
    code = re.sub(r"[^A-Za-z0-9가-힣]+", "_", clean_text(value)).strip("_")
    return (code or "gunsan_lifelong")[:50]


def parse_int(value: str) -> int | None:
    match = re.search(r"\d+", clean_text(value).replace(",", ""))
    return int(match.group(0)) if match else None


def parse_capacity(value: str) -> tuple[int | None, int | None]:
    text = clean_text(value)
    numbers = [int(item) for item in re.findall(r"\d+", text.replace(",", ""))]
    if len(numbers) >= 2:
        return numbers[1], numbers[0]
    if len(numbers) == 1:
        return numbers[0], None
    return None, None


def discover_course_url(session: requests.Session, timeout: int) -> str:
    soup = fetch_soup(session, SOURCE_URL, timeout)
    for link in soup.select("a[href]"):
        href = clean_text(link.get("href"))
        text = clean_text(link.get_text(" ", strip=True))
        if "lll.gunsan.go.kr" in href:
            return href.rstrip("/") + "/pro/course.php?pm=list" if href.rstrip("/") == "https://lll.gunsan.go.kr" else href
        if "평생학습정보망" in text and href:
            return urljoin(SOURCE_URL, href)
    return COURSE_LIST_URL


def parse_rows_from_table(soup: BeautifulSoup, current_url: str, status_hint: str) -> list[dict]:
    rows: list[dict] = []
    table = soup.select_one("table.borad_skin") or soup.find("table")
    if not table:
        return rows
    for tr in table.select("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all("td")]
        if len(cells) < 10:
            continue
        if "검색 결과가 없습니다" in " ".join(cells):
            continue
        title = cells[2]
        if not title or title == "강좌명":
            continue
        title_link = tr.find("a", href=True)
        apply_link = tr.select_one("td:last-child a[href]")
        raw_url = urljoin(current_url, clean_text(title_link.get("href"))) if title_link else current_url
        application_url = urljoin(current_url, clean_text(apply_link.get("href"))) if apply_link else raw_url
        branch = cells[5] or DEFAULT_BRANCH
        venue = cells[6] or branch
        capacity_total, capacity_current = parse_capacity(cells[7])
        schedule_raw = clean_text(" ".join(part for part in [cells[3], cells[4]] if part))
        status_raw = cells[0] or status_hint
        rows.append(
            {
                "provider": PROVIDER,
                "provider_course_id": stable_id(PROVIDER, raw_url, title, branch, schedule_raw),
                "title": title,
                "branch": branch,
                "branch_code": branch_code(branch),
                "address": DEFAULT_ADDRESS,
                "phone": "",
                "website_url": COURSE_LIST_URL,
                "target": cells[8],
                "category": cells[1],
                "collection_category": "평생학습",
                "domain_category": "평생교육",
                "operator_type": "지자체/공공기관",
                "source_group": "lifelong_learning",
                "collection_type": "static_html",
                "fee": "",
                "schedule_raw": schedule_raw,
                "schedule_days": [cells[3]] if cells[3] else [],
                "period": "",
                "start_date": None,
                "end_date": None,
                "apply_period": "",
                "capacity_total": capacity_total,
                "capacity_current": capacity_current,
                "capacity_remaining": None,
                "venue_name": venue,
                "venue_address": DEFAULT_ADDRESS,
                "application_url": application_url,
                "application_type": "ONLINE_RESERVATION",
                "application_method_raw": "군산시 평생학습정보망 개설강좌신청",
                "reservation_available": normalize_status(status_raw) in {"OPEN", "SCHEDULED", "WAITING"},
                "discovery_status": "gunsan_lifelong_course_table",
                "program_type": "강좌",
                "status": normalize_status(status_raw),
                "status_raw": status_raw,
                "raw_url": raw_url,
                "description": clean_text(" / ".join(cells)),
                "image_url": "",
                "raw_fields": {"cells": cells, "source_url": current_url},
            }
        )
    return rows


def collect(limit: int, max_pages: int, timeout: int, include_closed: bool) -> tuple[list[dict], dict]:
    session = make_session()
    discovered_url = discover_course_url(session, timeout)
    rows: list[dict] = []
    status_values = [("1", "신청 강좌")]
    if include_closed:
        status_values.append(("2", "마감 강좌"))
    pages = 0
    for status_value, status_hint in status_values:
        for page in range(1, max_pages + 1):
            data = {
                "m": "00000000",
                "pm": "list",
                "page": str(page),
                "status": status_value,
                "key": "1",
                "openType": "",
                "keyword1": "",
            }
            soup = fetch_soup(session, COURSE_URL, timeout, data=data)
            pages += 1
            page_rows = parse_rows_from_table(soup, COURSE_URL, status_hint)
            if not page_rows:
                break
            rows.extend(page_rows)
            if limit and len(rows) >= limit:
                return rows[:limit], {"pages": pages, "source_url": SOURCE_URL, "course_url": discovered_url}
    return rows[:limit] if limit else rows, {"pages": pages, "source_url": SOURCE_URL, "course_url": discovered_url}


def save_db(rows: list[dict], skip_expired: bool = True) -> int:
    if not rows:
        return 0
    from DB.db_utils import get_db_cursor

    today = date.today().isoformat()
    saved = 0
    branch_ids: dict[str, str] = {}
    with get_db_cursor() as cur:
        for row in rows:
            if skip_expired and row.get("end_date") and row["end_date"] < today:
                continue
            code = clean_text(row.get("branch_code"))[:50] or "gunsan_lifelong"
            if code not in branch_ids:
                cur.execute(
                    """
                    INSERT INTO branches(provider, branch_code, name, address, phone, website_url, address_source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (provider, branch_code)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        address = COALESCE(NULLIF(EXCLUDED.address, ''), branches.address),
                        phone = COALESCE(NULLIF(EXCLUDED.phone, ''), branches.phone),
                        website_url = COALESCE(NULLIF(EXCLUDED.website_url, ''), branches.website_url),
                        updated_at = now()
                    RETURNING id
                    """,
                    (
                        PROVIDER,
                        code,
                        clean_text(row.get("branch"))[:100],
                        clean_text(row.get("address")),
                        clean_text(row.get("phone")),
                        clean_text(row.get("website_url")) or COURSE_LIST_URL,
                        "crawler",
                    ),
                )
                branch_ids[code] = str(cur.fetchone()["id"])
            cur.execute(
                """
                INSERT INTO courses(
                    provider, provider_course_id, branch_id, title, target, category_raw,
                    collection_category, domain_category, source_group, operator_type, collection_type,
                    fee, schedule_raw, schedule_days, start_date, end_date, apply_period_raw,
                    capacity_total, capacity_current, capacity_remaining,
                    venue_name, venue_address, application_url, application_type, application_method_raw,
                    reservation_available, discovery_status, program_type, raw_fields,
                    status, raw_url, description, image_url, is_active, last_seen_at
                )
                VALUES (
                    %(provider)s, %(provider_course_id)s, %(branch_id)s, %(title)s, %(target)s, %(category_raw)s,
                    %(collection_category)s, %(domain_category)s, %(source_group)s, %(operator_type)s, %(collection_type)s,
                    %(fee)s, %(schedule_raw)s, %(schedule_days)s, %(start_date)s, %(end_date)s, %(apply_period_raw)s,
                    %(capacity_total)s, %(capacity_current)s, %(capacity_remaining)s,
                    %(venue_name)s, %(venue_address)s, %(application_url)s, %(application_type)s, %(application_method_raw)s,
                    %(reservation_available)s, %(discovery_status)s, %(program_type)s, %(raw_fields)s::jsonb,
                    %(status)s, %(raw_url)s, %(description)s, %(image_url)s, TRUE, now()
                )
                ON CONFLICT (provider, provider_course_id)
                DO UPDATE SET
                    branch_id = EXCLUDED.branch_id,
                    title = EXCLUDED.title,
                    target = EXCLUDED.target,
                    category_raw = EXCLUDED.category_raw,
                    collection_category = EXCLUDED.collection_category,
                    domain_category = EXCLUDED.domain_category,
                    source_group = EXCLUDED.source_group,
                    operator_type = EXCLUDED.operator_type,
                    collection_type = EXCLUDED.collection_type,
                    fee = EXCLUDED.fee,
                    schedule_raw = EXCLUDED.schedule_raw,
                    schedule_days = EXCLUDED.schedule_days,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    apply_period_raw = EXCLUDED.apply_period_raw,
                    capacity_total = EXCLUDED.capacity_total,
                    capacity_current = EXCLUDED.capacity_current,
                    capacity_remaining = EXCLUDED.capacity_remaining,
                    venue_name = EXCLUDED.venue_name,
                    venue_address = EXCLUDED.venue_address,
                    application_url = EXCLUDED.application_url,
                    application_type = EXCLUDED.application_type,
                    application_method_raw = EXCLUDED.application_method_raw,
                    reservation_available = EXCLUDED.reservation_available,
                    discovery_status = EXCLUDED.discovery_status,
                    program_type = EXCLUDED.program_type,
                    raw_fields = EXCLUDED.raw_fields,
                    status = EXCLUDED.status,
                    raw_url = EXCLUDED.raw_url,
                    description = EXCLUDED.description,
                    image_url = EXCLUDED.image_url,
                    is_active = TRUE,
                    last_seen_at = now()
                """,
                {
                    **row,
                    "branch_id": branch_ids[code],
                    "category_raw": row.get("category"),
                    "apply_period_raw": row.get("apply_period"),
                    "raw_fields": json.dumps(row.get("raw_fields") or {}, ensure_ascii=False),
                },
            )
            saved += 1
    return saved


def field_counts(rows: list[dict]) -> dict[str, int]:
    keys = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "image_url", "application_url"]
    return {key: sum(1 for row in rows if clean_text(row.get(key))) for key in keys}


def print_report(rows: list[dict], meta: dict, saved: int) -> None:
    fields = field_counts(rows)
    print("| provider | ok | rows | saved | pages | parser | title | branch | address | period | schedule | fee | target | status | desc | image | apply |")
    print("| -------- | -- | ---- | ----- | ----- | ------ | ----- | ------ | ------- | ------ | -------- | --- | ------ | ------ | ---- | ----- | ----- |")
    print(
        f"| {PROVIDER} | {'Y' if rows else 'N'} | {len(rows)} | {saved} | {meta.get('pages', 0)} | "
        f"gunsan_lifelong_table | {fields['title']} | {fields['branch']} | {fields['address']} | {fields['period']} | "
        f"{fields['schedule_raw']} | {fields['fee']} | {fields['target']} | {fields['status']} | {fields['description']} | "
        f"{fields['image_url']} | {fields['application_url']} |"
    )
    print(f"source: {meta.get('source_url')} -> {meta.get('course_url')}")
    if not rows:
        print("- 현재 군산시 평생학습정보망 개설강좌 목록은 신청/마감 조회 모두 결과가 없습니다.")
    for row in rows[:5]:
        print(f"- {row.get('branch')} / {row.get('title')} / {row.get('schedule_raw')} / {row.get('status_raw')} / {row.get('raw_url')}")
    print(
        json.dumps(
            {
                "provider": PROVIDER,
                "collected": len(rows),
                "saved": saved,
                "parser": "gunsan_lifelong_table",
                "field_counts": fields,
                "no_current_data": not rows,
                "no_current_reason": "no_current_courses" if not rows else "",
                "source_url": meta.get("source_url"),
                "course_url": meta.get("course_url"),
                "pages": meta.get("pages", 0),
            },
            ensure_ascii=False,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawl Gunsan lifelong learning course table.")
    parser.add_argument("--limit", "--per-target-limit", dest="limit", type=int, default=10)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--include-closed", action="store_true", default=True)
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    rows, meta = collect(args.limit, args.max_pages, args.timeout, args.include_closed)
    saved = save_db(rows, skip_expired=not args.include_expired) if args.save_db else 0
    if args.save_db and args.mark_stale and not rows:
        from DB.course_lifecycle import mark_stale_courses

        stale_count = mark_stale_courses(PROVIDER, started)
        print(f"stale_marked={stale_count}")
    print_report(rows, meta, saved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
