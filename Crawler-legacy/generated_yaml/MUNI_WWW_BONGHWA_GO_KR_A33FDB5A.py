from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup


PROVIDER = "MUNI_WWW_BONGHWA_GO_KR_A33FDB5A"
PROVIDER_NAME = "봉화군 평생학습관 평생학습강좌"
BASE_URL = "https://www.bonghwa.go.kr"
LIST_URL = f"{BASE_URL}/edu/portal/academy/program/list.do?mId=0301000000"
AJAX_URL = f"{BASE_URL}/edu/portal/academy/program/ajax/list.do"
DETAIL_URL = f"{BASE_URL}/edu/portal/academy/program/view.do"
DEFAULT_BRANCH = "봉화군 평생학습관"
DEFAULT_ADDRESS = "경상북도 봉화군 봉화읍 내성로5길 13"
DEFAULT_PHONE = "054-679-6395"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_YamlSources import YamlSourceCrawler, parse_date_range  # noqa: E402
from DB.db_utils import get_db_cursor  # noqa: E402
from data_parser import ScheduleParser, TargetParser  # noqa: E402
from utils import clean_text, extract_krw_amount, setup_logger  # noqa: E402


logger = setup_logger("Crawler_BonghwaLifelongA33")


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
            "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "identity",
            "Referer": LIST_URL,
        }
    )
    return session


def normalize_space(value: Any) -> str:
    text = clean_text(value).replace("\xa0", " ")
    return clean_text(re.sub(r"\s+", " ", text))


def stable_id(*parts: Any) -> str:
    seed = "|".join(normalize_space(part) for part in parts if normalize_space(part))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def branch_code(branch: Any) -> str:
    return stable_id(PROVIDER, normalize_space(branch) or DEFAULT_BRANCH)[:12]


def normalize_date_range(start: Any, end: Any) -> str:
    start_text = normalize_space(start)
    end_text = normalize_space(end)
    if start_text and end_text:
        return f"{start_text} ~ {end_text}"
    return start_text or end_text


def normalize_time_range(day: Any, start: Any, end: Any) -> str:
    day_text = normalize_space(day)
    start_text = normalize_space(start)
    end_text = normalize_space(end)
    time_text = f"{start_text}~{end_text}" if start_text and end_text else start_text or end_text
    return normalize_space(" ".join(part for part in [day_text, time_text] if part))


def normalize_status(value: Any) -> str:
    text = normalize_space(value)
    if any(token in text for token in ["모집중", "대기자모집", "추첨결과공개"]):
        return "OPEN"
    if any(token in text for token in ["모집예정", "추첨대기", "신청예정"]):
        return "SCHEDULED"
    if any(token in text for token in ["마감", "종료", "취소"]):
        return "CLOSED"
    return "OPEN" if not text else text


def normalize_fee(row: dict[str, Any]) -> str:
    if row.get("isFree") == "Y":
        return "무료"
    tuition = normalize_space(row.get("eduTuition"))
    if tuition:
        return tuition if "원" in tuition else f"{tuition}원"
    return ""


def capacity_values(row: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    total = int(row.get("appOnNum") or 0) + int(row.get("appOffNum") or 0)
    current = int(row.get("appliedOnNum") or 0) + int(row.get("appliedOffNum") or 0)
    wait_total = int(row.get("appWaitNum") or 0)
    return current, total, wait_total


def infer_age_group(target: str, title: str) -> str:
    text = f"{target} {title}"
    if re.search(r"유아|초등|어린이|아동|\d+\s*[~-]\s*\d+\s*세", text):
        return "KIDS"
    if re.search(r"청소년|중학생|고등학생|중등|고등", text):
        return "TEEN"
    if re.search(r"성인|군민|누구나|재직자|사업자", text):
        return "ADULT"
    return ""


def detail_url(program_idx: Any) -> str:
    return f"{DETAIL_URL}?{urlencode({'mId': '0301000000', 'programAppIdx': program_idx})}"


def api_payload(page: int, state: str) -> dict[str, Any]:
    return {
        "mId": "0301000000",
        "page": page,
        "searchTxt": "",
        "searchAppSortState": state,
        "searchField": "",
        "searchFieldDetail": "",
        "searchAppType": "",
        "searchEduTime": "",
    }


def fetch_api(session: requests.Session, page: int, state: str, timeout: int) -> dict[str, Any]:
    response = session.post(AJAX_URL, data=api_payload(page, state), timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_soup(session: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    table = soup.select_one("table")
    if not table:
        return pairs
    for tr in table.select("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        i = 0
        while i < len(cells):
            if cells[i].name != "th":
                i += 1
                continue
            key = normalize_space(cells[i].get_text(" ", strip=True))
            value = ""
            if i + 1 < len(cells) and cells[i + 1].name == "td":
                value = normalize_space(cells[i + 1].get_text(" ", strip=True))
                i += 2
            else:
                i += 1
            if key:
                pairs[key] = value
    return pairs


def extract_image_url(soup: BeautifulSoup) -> str:
    for img in soup.select("table img[src], #contents img[src]"):
        src = normalize_space(img.get("src"))
        alt = normalize_space(img.get("alt"))
        if not src or src.endswith(".svg") or "ico-" in src or "logo" in src:
            continue
        if "관련 이미지" in alt or "홍보물" in alt or "/file/img/" in src:
            return urljoin(BASE_URL, src)
    return ""


def parse_row(row: dict[str, Any], state: str) -> dict[str, Any]:
    program_idx = str(row.get("programAppIdx") or "")
    title = normalize_space(row.get("eduTitle"))
    category = normalize_space(" | ".join(part for part in [row.get("eduFieldName"), row.get("eduFieldDetailName")] if normalize_space(part)))
    period = normalize_date_range(row.get("eduSdate"), row.get("eduEdate"))
    schedule = normalize_space(" ".join([period, normalize_time_range(row.get("eduDayValue"), row.get("eduStime"), row.get("eduEtime"))]))
    reception_period = normalize_date_range(row.get("appSdate"), row.get("appEdate"))
    capacity_current, capacity_total, waitlist_total = capacity_values(row)
    material_note = normalize_space(row.get("eduCost"))
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "external_id": program_idx,
        "provider_course_id": program_idx or stable_id(title, period),
        "title": title,
        "branch": DEFAULT_BRANCH,
        "branch_code": branch_code(DEFAULT_BRANCH),
        "address": DEFAULT_ADDRESS,
        "phone": DEFAULT_PHONE,
        "period": period,
        "schedule_raw": schedule,
        "target": "",
        "age_group": infer_age_group("", title),
        "fee": normalize_fee(row),
        "material_fee": extract_krw_amount(material_note),
        "material_note": material_note,
        "status": normalize_status(row.get("appStateValue") or state),
        "status_raw": normalize_space(row.get("appStateValue")),
        "description": "",
        "image_url": "",
        "raw_url": detail_url(program_idx),
        "application_url": detail_url(program_idx),
        "application_type": "ONLINE" if int(row.get("appOnNum") or 0) > 0 else "OFFLINE",
        "application_method_raw": normalize_space(row.get("appTypeValue")),
        "reservation_available": normalize_status(row.get("appStateValue")) == "OPEN",
        "category_raw": category,
        "venue_name": DEFAULT_BRANCH,
        "venue_address": DEFAULT_ADDRESS,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_total": waitlist_total,
        "reception_period": reception_period,
        "collection_category": "교육·체험",
        "domain_category": "평생학습",
        "source_group": "lifelong_learning",
        "operator_type": "지자체/공공기관",
        "collection_type": "json_api+detail_html",
        "program_type": "OFFLINE",
        "raw_fields": {"api_row": row, "list_parser": "bonghwa_lifelong_json"},
    }


def enrich_detail(session: requests.Session, row: dict[str, Any], timeout: int) -> dict[str, Any]:
    soup = fetch_soup(session, row["raw_url"], timeout)
    pairs = table_pairs(soup)
    target = normalize_space(pairs.get("모집대상"))
    venue = normalize_space(pairs.get("교육장소"))
    description = normalize_space(pairs.get("강의내용"))
    material_note = normalize_space(pairs.get("재료비")) or row.get("material_note", "")
    detail_schedule = normalize_space(pairs.get("교육시간"))
    period = normalize_space(pairs.get("교육기간")) or row.get("period", "")
    instructor = normalize_space(pairs.get("강사"))
    row.update(
        {
            "target": target or row.get("target", ""),
            "age_group": infer_age_group(target, row["title"]),
            "period": period,
            "schedule_raw": normalize_space(" ".join(part for part in [period, detail_schedule] if part)) or row.get("schedule_raw", ""),
            "venue_name": venue or row.get("venue_name", ""),
            "venue_address": DEFAULT_ADDRESS,
            "instructor": instructor,
            "teacher": instructor,
            "material_fee": extract_krw_amount(material_note),
            "material_note": material_note,
            "description": description,
            "image_url": extract_image_url(soup),
        }
    )
    if pairs.get("학습분야"):
        row["category_raw"] = normalize_space(pairs["학습분야"])
    if pairs.get("모집기간"):
        row["reception_period"] = normalize_space(pairs["모집기간"])
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_parser"] = "bonghwa_lifelong_detail"
    return row


def is_expired_course(row: dict[str, Any]) -> bool:
    try:
        _start, end_date = parse_date_range(row.get("period"))
    except Exception:  # noqa: BLE001
        return False
    if end_date is None:
        return False
    return end_date < date.today()


def collect(
    limit: int | None = None,
    max_pages: int = 5,
    timeout: int = 20,
    include_expired: bool = False,
    detail: bool = True,
) -> list[dict[str, Any]]:
    session = make_session()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    states = ["", "ing", "wait"] if not include_expired else ["", "ing", "wait", "end"]
    for state in states:
        for page in range(1, max_pages + 1):
            data = fetch_api(session, page, state, timeout)
            page_rows = data.get("programList") or []
            if not page_rows:
                break
            for api_row in page_rows:
                row = parse_row(api_row, state)
                key = row["provider_course_id"]
                if key in seen:
                    continue
                seen.add(key)
                if detail:
                    try:
                        row = enrich_detail(session, row, timeout)
                    except Exception as exc:
                        logger.warning("Bonghwa detail failed %s: %s", row.get("raw_url"), exc)
                if not include_expired and is_expired_course(row):
                    continue
                rows.append(row)
                if limit and len(rows) >= limit:
                    return rows
            pagination = json.loads(data.get("paginationInfo") or "{}")
            if int(pagination.get("currentPageNo") or page) >= int(pagination.get("totalPageCount") or page):
                break
    return rows


def quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["title", "branch", "address", "period", "schedule_raw", "target", "fee", "status", "description", "image_url"]
    counts = {field: sum(1 for row in rows if normalize_space(row.get(field))) for field in fields}
    score = round(sum(counts.values()) / (len(rows) * len(fields)) * 100, 1) if rows else 0.0
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    return {"rows": len(rows), "score": score, "grade": grade, "field_counts": counts}


def print_quality(rows: list[dict[str, Any]]) -> None:
    print(json.dumps(quality(rows), ensure_ascii=False, indent=2))
    print("\nSAMPLE")
    for row in rows[:10]:
        print(
            " | ".join(
                [
                    normalize_space(row.get("title")),
                    normalize_space(row.get("branch")),
                    normalize_space(row.get("period")),
                    normalize_space(row.get("schedule_raw")),
                    normalize_space(row.get("target")),
                    normalize_space(row.get("fee")),
                    normalize_space(row.get("status")),
                ]
            )
        )


def save_branch_with_address(row: dict[str, Any]) -> str:
    branch = {
        "provider": PROVIDER,
        "branch_code": (normalize_space(row.get("branch_code")) or branch_code(row.get("branch")))[:50],
        "name": (normalize_space(row.get("branch")) or DEFAULT_BRANCH)[:100],
        "address": normalize_space(row.get("address")) or DEFAULT_ADDRESS,
        "phone": normalize_space(row.get("phone")) or DEFAULT_PHONE,
        "website_url": LIST_URL,
        "address_source": "crawler_static",
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
                address_source = COALESCE(NULLIF(EXCLUDED.address_source, ''), branches.address_source),
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            branch,
        )
        return str(cursor.fetchone()["id"])


def save_rows(rows: list[dict[str, Any]], mark_stale: bool = False) -> int:
    crawler = YamlSourceCrawler.__new__(YamlSourceCrawler)
    crawler.provider = PROVIDER
    crawler.target_parser = TargetParser()
    crawler.schedule_parser = ScheduleParser()
    saved = 0
    branch_ids: dict[str, str] = {}
    for row in rows:
        code = normalize_space(row.get("branch_code")) or branch_code(row.get("branch"))
        if code not in branch_ids:
            branch_ids[code] = save_branch_with_address(row)
        course = crawler.normalize_course(row, branch_ids[code])
        if crawler.save_course(course):
            saved += 1
    if mark_stale and saved > 0:
        from DB.course_lifecycle import mark_stale_courses, utc_now

        mark_stale_courses(PROVIDER, utc_now())
    logger.info("%s saved %s/%s rows.", PROVIDER, saved, len(rows))
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROVIDER_NAME} crawler")
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

    effective_limit = args.limit if args.limit is not None else args.per_target_limit
    started = datetime.now()
    rows = collect(
        limit=effective_limit,
        max_pages=args.max_pages,
        timeout=args.timeout,
        include_expired=args.include_expired,
        detail=not args.no_detail,
    )
    print_quality(rows)
    saved = save_rows(rows, mark_stale=args.mark_stale) if args.save_db else 0
    logger.info(
        "%s completed collected=%s saved=%s elapsed=%.1fs",
        PROVIDER,
        len(rows),
        saved,
        (datetime.now() - started).total_seconds(),
    )
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
