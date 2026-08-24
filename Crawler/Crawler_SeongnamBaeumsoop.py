from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
import yaml
from bs4 import BeautifulSoup, Tag


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.course_lifecycle import mark_stale_courses, utc_now
from DB.db_utils import get_db_cursor
from utils import setup_logger
from utils.course_semantic_eligibility import (
    CourseSemanticEligibilityError,
    guard_course_before_upsert,
)
from utils.outbound_http import SafeSession
from utils.url_security import sanitize_course_payload


PROVIDER = "SEONGNAM_BAEUMSOOP"
BASE_URL = "https://sugang.seongnam.go.kr"
LIST_PATH = "/ilms/learning/learningList.do"
DETAIL_PATH = "/ilms/learning/learningDetail.do"
SEONGNAM_MUNICIPALITY_CODE = "4113000000"
SEONGNAM_MUNICIPALITY_NAME = "경기도 성남시"
SEONGNAM_DISTRICT_MUNICIPALITIES = {
    "수정구": ("4113100000", "경기도 성남시 수정구"),
    "중원구": ("4113300000", "경기도 성남시 중원구"),
    "분당구": ("4113500000", "경기도 성남시 분당구"),
}
REPORT_DIR = ROOT / "logs" / "crawler_reports"
logger = setup_logger(__name__, "logs/crawler_seongnam_baeumsoop.log")

MAX_LIMIT = 100_000
MAX_OFFICES = 500
MAX_PAGES = 200
MAX_TIMEOUT_SECONDS = 120


def _validate_options(
    limit: int | None,
    office_limit: int | None,
    max_pages: int,
    timeout: int,
) -> None:
    if limit is not None and not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if office_limit is not None and not 1 <= office_limit <= MAX_OFFICES:
        raise ValueError(f"office_limit must be between 1 and {MAX_OFFICES}")
    if not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")
    if not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS}")

DEFAULT_OFFICES = [
    {"office_code": "OFFICE_00000670", "branch": ""},
    {"office_code": "OFFICE_00000680", "branch": ""},
    {"office_code": "OFFICE_00000681", "branch": ""},
    {"office_code": "OFFICE_00001080", "branch": ""},
    {"office_code": "OFFICE_00002180", "branch": ""},
]

OFFICE_ADDRESS_MAP = {
    "OFFICE_00000670": "경기도 성남시 분당구 분당로 50",
    "OFFICE_00000680": "경기도 성남시 수정구 수정로 283",
    "OFFICE_00000681": "경기도 성남시 중원구 제일로 36",
    "OFFICE_00001080": "경기도 성남시 중원구 성남대로 997",
    "OFFICE_00002180": "경기도 성남시 수정구 성남대로 1342",
}

PRACTICE_TITLE_PATTERNS = (
    "수강신청 연습용",
    "강의접수 연습용",
    "접수연습용",
    "실제 강좌 아님",
    "실제 강의 아님",
)


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_id(*parts: object) -> str:
    raw = "|".join(clean_text(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def session() -> requests.Session:
    s = SafeSession()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": f"{BASE_URL}/ilms/learning/officeList.do",
        }
    )
    return s


def score_fields(rows: list[dict[str, Any]]) -> dict[str, int]:
    fields = [
        "title",
        "branch",
        "raw_url",
        "address",
        "period",
        "schedule_raw",
        "target",
        "fee",
        "status",
        "description",
        "image_url",
        "application_url",
    ]
    return {field: sum(1 for row in rows if clean_text(row.get(field))) for field in fields}


def parse_short_date(value: str) -> str:
    match = re.search(r"(\d{2,4})[.-](\d{1,2})[.-](\d{1,2})", clean_text(value))
    if not match:
        return ""
    year = int(match.group(1))
    if year < 100:
        year += 2000
    normalized = f"{year:04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def normalize_status(value: str) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if any(token in text for token in ("접수중", "방문접수중", "신청가능")):
        return "OPEN"
    if any(token in text for token in ("접수예정", "예정", "대기")):
        return "SCHEDULED"
    if any(token in text for token in ("마감", "종료", "교육중", "접수불가")):
        return "CLOSED"
    return "OPEN" if "접수" in text else "SCHEDULED"


def parse_money(value: object) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    if "무료" in text:
        return 0
    numbers = re.findall(r"\d+", text.replace(",", ""))
    return int(numbers[0]) if numbers else None


def parse_capacity(value: str) -> tuple[int | None, int | None]:
    text = clean_text(value)
    match = re.search(r"(\d+)\s*명\s*/\s*(\d+)\s*명", text)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def should_skip_course(row: dict[str, Any]) -> bool:
    title = clean_text(row.get("title"))
    compact_title = re.sub(r"\s+", "", title)
    return any(
        re.sub(r"\s+", "", pattern) in compact_title
        for pattern in PRACTICE_TITLE_PATTERNS
    )


def assign_seongnam_municipality(row: dict[str, Any]) -> tuple[str, str]:
    """Resolve an exact Seongnam district from structured row location data.

    Official office addresses and detail venue fields are authoritative. A
    branch/place label is used only when neither address field identifies a
    district. Conflicting or absent evidence stays at parent Seongnam instead
    of being guessed into one of the three districts.
    """

    evidence_fields = (
        ("address", clean_text(row.get("address"))),
        ("venue_address", clean_text(row.get("venue_address"))),
    )
    matches = {
        district
        for _field_name, value in evidence_fields
        for district in SEONGNAM_DISTRICT_MUNICIPALITIES
        if district in value
    }
    evidence_kind = "official_address"
    if not matches:
        evidence_fields = (
            ("venue_name", clean_text(row.get("venue_name"))),
            ("branch", clean_text(row.get("branch"))),
        )
        matches = {
            district
            for _field_name, value in evidence_fields
            for district in SEONGNAM_DISTRICT_MUNICIPALITIES
            if district in value
        }
        evidence_kind = "official_venue_or_branch"

    if len(matches) == 1:
        district = next(iter(matches))
        municipality_code, municipality_full_name = (
            SEONGNAM_DISTRICT_MUNICIPALITIES[district]
        )
        resolution_source = evidence_kind
    else:
        municipality_code = SEONGNAM_MUNICIPALITY_CODE
        municipality_full_name = SEONGNAM_MUNICIPALITY_NAME
        resolution_source = (
            "conservative_parent_conflicting_evidence"
            if len(matches) > 1
            else "conservative_parent_no_district_evidence"
        )

    row.update(
        {
            "municipality_code": municipality_code,
            "municipality_full_name": municipality_full_name,
            "municipality_region_verified": True,
            "region_sido": "경기도",
            "region_sigungu": municipality_full_name.removeprefix("경기도 "),
            "municipality_resolution_source": resolution_source,
        }
    )
    return municipality_code, municipality_full_name


def list_url(office_code: str, page: int = 1) -> str:
    params = {
        "searchUseYn": "Y",
        "searchCondition3": office_code,
        "pageIndex": page,
    }
    return f"{BASE_URL}{LIST_PATH}?{urlencode(params)}"


def detail_url(learning_id: str) -> str:
    return f"{BASE_URL}{DETAIL_PATH}?{urlencode({'learning_id': learning_id})}"


def fetch(s: requests.Session, url: str, timeout: int) -> str:
    if not url.startswith(f"{BASE_URL}/ilms/learning/"):
        raise ValueError("refusing an untrusted Seongnam source URL")
    for attempt in range(2):
        try:
            response = s.get(url, timeout=timeout, allow_redirects=False)
            if 300 <= response.status_code < 400:
                raise requests.TooManyRedirects("Seongnam provider redirects are not allowed")
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except requests.RequestException:
            if attempt:
                raise
            time.sleep(0.2)
    raise AssertionError("unreachable")


def is_waiting_page(html: str) -> bool:
    text = html[:30000]
    return "TRACER" in text and "서비스 접근 대기" in text and "learningList" not in text


def discover_offices_from_files() -> list[dict[str, str]]:
    paths = [
        ROOT / "config" / "collected_yaml_crawl_targets.yaml",
        ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml",
        ROOT / "config" / "generated_yaml_crawler_registry.yaml",
        ROOT / "logs" / "crawler_dev_reports" / "generated_612_quality_20260526_183657_site_quality.csv",
    ]
    found: dict[str, dict[str, str]] = {}
    for office in DEFAULT_OFFICES:
        found[office["office_code"]] = dict(office)
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for code in re.findall(r"OFFICE_\d{8}", text):
            found.setdefault(code, {"office_code": code, "branch": ""})
    return list(found.values())


def max_page_from_soup(soup: BeautifulSoup) -> int:
    max_page = 1
    for element in soup.select("a[href*='pageIndex'], a[onclick*='fn_list']"):
        text = clean_text(element.get_text(" ", strip=True))
        href = element.get("href") or ""
        onclick = element.get("onclick") or ""
        for value in re.findall(r"pageIndex=(\d+)|fn_list\((\d+)", f"{href} {onclick}"):
            page_text = value[0] or value[1]
            if page_text:
                max_page = max(max_page, int(page_text))
        if text.isdigit():
            max_page = max(max_page, int(text))
    return max_page


def extract_cell_text(tr: Tag, label: str) -> str:
    for strong in tr.select("strong.tits"):
        if label not in clean_text(strong.get_text(" ", strip=True)):
            continue
        parent = strong.parent
        if not isinstance(parent, Tag):
            continue
        parts: list[str] = []
        for child in parent.children:
            if isinstance(child, Tag) and child.name == "strong":
                continue
            if isinstance(child, Tag):
                parts.append(child.get_text(" ", strip=True))
            else:
                parts.append(str(child))
        return clean_text(" ".join(parts))
    return ""


def parse_period_schedule(tr: Tag) -> tuple[str, str, str | None, str | None, list[str]]:
    date_values = [clean_text(node.get_text(" ", strip=True)) for node in tr.select(".peroiddate")]
    day_values = [clean_text(node.get_text(" ", strip=True)) for node in tr.select(".peroidyoil")]
    time_values = [clean_text(node.get_text(" ", strip=True)) for node in tr.select(".peroidtime")]
    cell_text = " ".join(date_values + day_values + time_values) or extract_cell_text(tr, "교육기간")
    dates = [parse_short_date(text) for text in (date_values or re.findall(r"\d{2,4}[.-]\d{1,2}[.-]\d{1,2}", cell_text))]
    dates = [date for date in dates if date]
    days = day_values or re.findall(r"[월화수목금토일](?:,\s*[월화수목금토일])*", cell_text)
    time_match = re.search(r"\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}", " ".join(time_values) or cell_text)
    period = ""
    start_date = None
    end_date = None
    if len(dates) >= 2:
        start_date, end_date = dates[0], dates[1]
        period = start_date if start_date == end_date else f"{start_date} ~ {end_date}"
    elif dates:
        start_date = end_date = dates[0]
        period = dates[0]
    day_text = days[-1] if days else ""
    time_text = clean_text(time_match.group(0)) if time_match else ""
    schedule_parts = [part for part in (day_text, time_text) if part]
    return period, " ".join(schedule_parts), start_date, end_date, [day.strip() for day in day_text.split(",") if day.strip()]


def parse_course_row(tr: Tag, office: dict[str, str], source_url: str) -> dict[str, Any] | None:
    title_node = tr.select_one(".subject .tit")
    branch_node = tr.select_one(".org")
    status_node = tr.select_one(".s_btn")
    title = clean_text(title_node.get_text(" ", strip=True)) if title_node else extract_cell_text(tr, "강좌명")
    branch = (
        clean_text(branch_node.get_text(" ", strip=True))
        if branch_node
        else extract_cell_text(tr, "교육기관") or office.get("branch") or office["office_code"]
    )
    status_raw = clean_text(status_node.get_text(" ", strip=True)) if status_node else extract_cell_text(tr, "상태")
    capacity_total, capacity_current = parse_capacity(extract_cell_text(tr, "모집인원/접수인원"))
    period, schedule_raw, start_date, end_date, schedule_days = parse_period_schedule(tr)

    learning_id = ""
    for element in tr.select("[onclick*='fn_learning_detail']"):
        match = re.search(r"fn_learning_detail\('([^']+)'\)", element.get("onclick") or "")
        if match:
            learning_id = match.group(1)
            break
    if not title:
        return None

    raw_url = detail_url(learning_id) if learning_id else source_url
    status = normalize_status(status_raw)
    application_available = status == "OPEN"
    provider_course_id = learning_id or stable_id(PROVIDER, office["office_code"], title, period, schedule_raw)
    row: dict[str, Any] = {
        "provider": PROVIDER,
        "provider_course_id": provider_course_id,
        "title": title,
        "branch": branch,
        "branch_code": office["office_code"],
        "address": OFFICE_ADDRESS_MAP.get(office["office_code"], ""),
        "phone": "",
        "raw_url": raw_url,
        "application_url": raw_url if application_available else "",
        "application_type": "ONLINE_RESERVATION",
        "application_method_raw": status_raw,
        "reservation_available": application_available,
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "schedule_raw": schedule_raw,
        "schedule_days": schedule_days,
        "target": "",
        "fee": "",
        "material_fee": None,
        "status_raw": status_raw,
        "status": status,
        "capacity_total": capacity_total,
        "capacity_current": capacity_current,
        "capacity_remaining": (
            max(0, capacity_total - capacity_current)
            if capacity_total is not None and capacity_current is not None
            else None
        ),
        "description": "",
        "image_url": "",
        "venue_name": branch,
        "venue_address": "",
        "collection_category": "OTHER",
        "domain_category": "평생교육",
        "source_group": "local_lifelong_learning",
        "operator_type": "municipal",
        "collection_type": "office_course_list",
        "program_type": "강좌",
        "discovery_status": "seongnam_office_learning_list",
        "source_url": source_url,
    }
    assign_seongnam_municipality(row)
    return row


def parse_detail_if_available(s: requests.Session, row: dict[str, Any], timeout: int) -> bool:
    raw_url = clean_text(row.get("raw_url"))
    if not raw_url or "learning_id=" not in raw_url:
        return False
    try:
        html = fetch(s, raw_url, timeout)
    except Exception as exc:
        logger.warning("Seongnam detail fetch failed for course_id=%s: %s", row.get("provider_course_id"), exc)
        return False
    if is_waiting_page(html):
        return False
    soup = BeautifulSoup(html, "html.parser")
    text = clean_text(soup.get_text(" ", strip=True))
    if "알 수 없는 오류" in text or len(text) < 200:
        return False

    image = soup.select_one("img[src*='upload'], img[src*='attach'], .view img")
    if image and image.get("src"):
        row["image_url"] = requests.compat.urljoin(raw_url, image["src"])

    detail_pairs: dict[str, str] = {}
    for tr in soup.select("tr"):
        headers = [clean_text(th.get_text(" ", strip=True)) for th in tr.select("th")]
        cells = [clean_text(td.get_text(" ", strip=True)) for td in tr.select("td")]
        for index, key in enumerate(headers):
            if key and index < len(cells):
                detail_pairs[key] = cells[index]
    for dl in soup.select("dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if dt and dd:
            detail_pairs[clean_text(dt.get_text(" ", strip=True))] = clean_text(dd.get_text(" ", strip=True))

    row["target"] = detail_pairs.get("교육대상") or detail_pairs.get("대상") or row.get("target", "")
    row["fee"] = detail_pairs.get("수강료") or detail_pairs.get("교육비") or row.get("fee", "")
    row["description"] = (
        detail_pairs.get("강좌소개")
        or detail_pairs.get("교육내용")
        or detail_pairs.get("내용")
        or row.get("description", "")
    )
    row["venue_address"] = detail_pairs.get("교육장소") or detail_pairs.get("장소") or row.get("venue_address", "")
    assign_seongnam_municipality(row)
    return True


def validate_office(s: requests.Session, office: dict[str, str], timeout: int) -> dict[str, Any] | None:
    url = list_url(office["office_code"])
    html = fetch(s, url, timeout)
    if is_waiting_page(html):
        raise RuntimeError("Seongnam source returned an access waiting page")
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("table") is None:
        raise ValueError("Seongnam source omitted the expected course table")
    rows: list[dict[str, Any]] = []
    parse_errors = 0
    for tr in soup.select("table tbody tr"):
        try:
            row = parse_course_row(tr, office, url)
        except Exception as exc:
            parse_errors += 1
            logger.warning("Seongnam course row parse failed for office_code=%s: %s", office["office_code"], exc)
            continue
        if row:
            rows.append(row)
    if not rows:
        return {
            "html": html,
            "soup": soup,
            "rows": [],
            "max_page": max_page_from_soup(soup),
            "parse_errors": parse_errors,
        }
    first = rows[0]
    if first and not office.get("branch"):
        office["branch"] = first.get("branch") or office["office_code"]
    return {
        "html": html,
        "soup": soup,
        "rows": rows,
        "max_page": max_page_from_soup(soup),
        "parse_errors": parse_errors,
    }


def collect(
    *,
    limit: int | None,
    office_limit: int | None,
    max_pages: int,
    timeout: int,
    detail: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _validate_options(limit, office_limit, max_pages, timeout)
    candidates = discover_offices_from_files()
    rows: list[dict[str, Any]] = []
    offices_seen = 0
    offices_with_rows = 0
    pages_fetched = 0
    detail_pages = 0
    office_errors = 0
    page_errors = 0
    capped_offices = 0
    duplicate_rows = 0
    seen_course_ids: set[str] = set()
    row_parse_errors = 0
    office_summaries: list[dict[str, Any]] = []

    with session() as s:
        for office in candidates:
            if office_limit is not None and offices_seen >= office_limit:
                break
            offices_seen += 1
            try:
                validation = validate_office(s, office, timeout)
            except Exception as exc:
                office_errors += 1
                logger.warning("Seongnam office validation failed for office_code=%s: %s", office["office_code"], exc)
                continue
            if not validation:
                continue
            if validation["rows"]:
                offices_with_rows += 1
            row_parse_errors += int(validation.get("parse_errors") or 0)
            detected_max_page = max(1, int(validation["max_page"] or 1))
            office_count = 0
            office_pages = 0
            branch_name = office.get("branch") or office["office_code"]

            page = 1
            while page <= min(max_pages, detected_max_page):
                current_url = list_url(office["office_code"], page)
                try:
                    if page == 1:
                        soup = validation["soup"]
                    else:
                        html = fetch(s, current_url, timeout)
                        if is_waiting_page(html):
                            raise RuntimeError("Seongnam source returned an access waiting page")
                        soup = BeautifulSoup(html, "html.parser")
                except Exception as exc:
                    page_errors += 1
                    logger.warning(
                        "Seongnam page fetch failed for office_code=%s page=%s: %s",
                        office["office_code"],
                        page,
                        exc,
                    )
                    break
                pages_fetched += 1
                office_pages += 1
                detected_max_page = max(detected_max_page, max_page_from_soup(soup))
                if page == 1:
                    page_rows = validation["rows"]
                else:
                    page_rows = []
                    for tr in soup.select("table tbody tr"):
                        try:
                            row = parse_course_row(tr, office, current_url)
                        except Exception as exc:
                            row_parse_errors += 1
                            logger.warning(
                                "Seongnam course row parse failed for office_code=%s page=%s: %s",
                                office["office_code"],
                                page,
                                exc,
                            )
                            continue
                        if row:
                            page_rows.append(row)
                for row in page_rows:
                    if detail and parse_detail_if_available(s, row, timeout):
                        detail_pages += 1
                    if should_skip_course(row):
                        continue
                    identity = clean_text(row.get("provider_course_id"))
                    if identity in seen_course_ids:
                        duplicate_rows += 1
                        continue
                    seen_course_ids.add(identity)
                    rows.append(row)
                    office_count += 1
                    if limit is not None and len(rows) >= limit:
                        break
                if limit is not None and len(rows) >= limit:
                    break
                page += 1

            if detected_max_page > max_pages:
                capped_offices += 1

            office_summaries.append(
                {
                    "office_code": office["office_code"],
                    "branch": branch_name,
                    "pages": office_pages,
                    "detected_pages": detected_max_page,
                    "collected": office_count,
                }
            )
            if limit is not None and len(rows) >= limit:
                break

    complete = bool(
        limit is None
        and office_limit is None
        and offices_seen == len(candidates)
        and not office_errors
        and not page_errors
        and not capped_offices
        and not row_parse_errors
    )

    meta = {
        "parser": "seongnam_office_learning_list",
        "candidate_offices": len(candidates),
        "offices_checked": offices_seen,
        "offices_with_rows": offices_with_rows,
        "pages": pages_fetched,
        "detail_pages": detail_pages,
        "office_errors": office_errors,
        "page_errors": page_errors,
        "capped_offices": capped_offices,
        "duplicate_rows": duplicate_rows,
        "row_parse_errors": row_parse_errors,
        "complete": complete,
        "offices": office_summaries,
    }
    return rows, meta


def bounded_raw_fields(row: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    for key, value in list(row.items())[:64]:
        if isinstance(value, str):
            payload[key] = value[:4_000]
        elif isinstance(value, (list, tuple)):
            payload[key] = [clean_text(item)[:200] for item in value[:64]]
        elif value is None or isinstance(value, (bool, int, float)):
            payload[key] = value
        else:
            payload[key] = clean_text(value)[:4_000]
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) <= 64 * 1024:
        return encoded
    core = {
        key: payload.get(key)
        for key in ("provider", "provider_course_id", "title", "branch_code", "raw_url", "status")
    }
    return json.dumps(core, ensure_ascii=False, default=str)


def save_db(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    publishable_rows: list[dict[str, Any]] = []
    for row in rows:
        sanitize_course_payload(row)
        try:
            guard_course_before_upsert(row)
        except CourseSemanticEligibilityError as exc:
            logger.warning(
                "Rejected non-course row before database write. course_id=%s reason=%s evidence=%s",
                row.get("provider_course_id"),
                exc.reason,
                ",".join(exc.evidence),
            )
            continue
        publishable_rows.append(row)
    if not publishable_rows:
        return 0

    branch_ids: dict[str, str] = {}
    saved = 0
    with get_db_cursor() as cur:
        for row in publishable_rows:
            branch_code = clean_text(row.get("branch_code"))[:50]
            if branch_code not in branch_ids:
                cur.execute(
                    """
                    INSERT INTO branches(
                        provider, branch_code, name, address, phone, website_url,
                        address_source, region_sido, region_sigungu
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (provider, branch_code)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        address = COALESCE(NULLIF(EXCLUDED.address, ''), branches.address),
                        phone = COALESCE(NULLIF(EXCLUDED.phone, ''), branches.phone),
                        website_url = EXCLUDED.website_url,
                        region_sido = COALESCE(
                            NULLIF(EXCLUDED.region_sido, ''),
                            branches.region_sido
                        ),
                        region_sigungu = COALESCE(
                            NULLIF(EXCLUDED.region_sigungu, ''),
                            branches.region_sigungu
                        ),
                        updated_at = now()
                    RETURNING id
                    """,
                    (
                        PROVIDER,
                        branch_code,
                        clean_text(row.get("branch"))[:100],
                        clean_text(row.get("address"))[:2_000],
                        clean_text(row.get("phone"))[:100],
                        BASE_URL,
                        "crawler" if row.get("address") else None,
                        clean_text(row.get("region_sido"))[:50],
                        clean_text(row.get("region_sigungu"))[:80],
                    ),
                )
                branch_ids[branch_code] = str(cur.fetchone()["id"])

            params = {
                **row,
                "branch_id": branch_ids[branch_code],
                "fee_numeric": parse_money(row.get("fee")),
                "material_fee_numeric": parse_money(row.get("material_fee")),
                "raw_fields": bounded_raw_fields(row),
            }
            cur.execute(
                """
                INSERT INTO courses(
                    provider, provider_course_id, branch_id, title, target, category_raw,
                    collection_category, domain_category, source_group, operator_type, collection_type,
                    fee, material_fee, schedule_raw, schedule_days, start_date, end_date,
                    capacity_total, capacity_current, capacity_remaining,
                    venue_name, venue_address, application_url, application_type, application_method_raw,
                    reservation_available, discovery_status, program_type, raw_fields,
                    status, raw_url, description, image_url, is_active, last_seen_at
                )
                VALUES (
                    %(provider)s, %(provider_course_id)s, %(branch_id)s, %(title)s, %(target)s, %(category_raw)s,
                    %(collection_category)s, %(domain_category)s, %(source_group)s, %(operator_type)s, %(collection_type)s,
                    %(fee_numeric)s, %(material_fee_numeric)s, %(schedule_raw)s, %(schedule_days)s, %(start_date)s, %(end_date)s,
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
                    material_fee = EXCLUDED.material_fee,
                    schedule_raw = EXCLUDED.schedule_raw,
                    schedule_days = EXCLUDED.schedule_days,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
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
                    **params,
                    "category_raw": row.get("domain_category"),
                },
            )
            saved += 1
    return saved


def write_report(rows: list[dict[str, Any]], meta: dict[str, Any], saved: int) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"seongnam_baeumsoop_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml"
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provider": PROVIDER,
        "success": bool(rows),
        "collected": len(rows),
        "saved": saved,
        "meta": meta,
        "fields": score_fields(rows),
        "samples": rows[:10],
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")
    return path


def print_summary(rows: list[dict[str, Any]], meta: dict[str, Any], saved: int, report_path: Path) -> None:
    print(f"provider={PROVIDER} collected={len(rows)} saved={saved} parser={meta.get('parser')} report={report_path}")
    print(
        "offices "
        f"candidates={meta.get('candidate_offices')} checked={meta.get('offices_checked')} "
        f"with_rows={meta.get('offices_with_rows')} pages={meta.get('pages')} details={meta.get('detail_pages')}"
    )
    print("field_counts " + " ".join(f"{key}={value}" for key, value in score_fields(rows).items()))
    print("office_results")
    for office in meta.get("offices", [])[:20]:
        print(
            f"- {office.get('office_code')} {office.get('branch')} "
            f"pages={office.get('pages')} collected={office.get('collected')}"
        )
    for row in rows[:5]:
        print(
            "sample "
            f"branch={row.get('branch')} title={row.get('title')} "
            f"period={row.get('period')} schedule={row.get('schedule_raw')} status={row.get('status_raw')}"
        )


def run(
    *,
    limit: int | None,
    save: bool,
    mark_stale: bool,
    office_limit: int | None,
    max_pages: int,
    timeout: int,
    detail: bool,
) -> list[dict[str, Any]]:
    crawl_started_at = utc_now()
    rows, meta = collect(
        limit=limit,
        office_limit=office_limit,
        max_pages=max_pages,
        timeout=timeout,
        detail=detail,
    )
    saved = save_db(rows) if save else 0
    if save and mark_stale and saved == len(rows) and meta.get("complete"):
        mark_stale_courses(PROVIDER, crawl_started_at)
    elif save and mark_stale:
        raise RuntimeError("stale cleanup refused because the Seongnam crawl was partial")
    report_path = write_report(rows, meta, saved)
    print_summary(rows, meta, saved, report_path)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl Seongnam Baeumsoop by office.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--office-limit", type=int, default=None)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    args = parser.parse_args()
    if args.mark_stale and not args.save_db:
        parser.error("--mark-stale requires --save-db")
    if args.mark_stale and (args.limit is not None or args.office_limit is not None):
        parser.error("--mark-stale cannot be used with --limit or --office-limit")
    return args


def main() -> int:
    args = parse_args()
    try:
        rows = run(
            limit=args.limit,
            save=args.save_db,
            mark_stale=args.mark_stale,
            office_limit=args.office_limit,
            max_pages=args.max_pages,
            timeout=args.timeout,
            detail=not args.no_detail,
        )
    except Exception as exc:
        logger.error("Seongnam crawler failed closed: %s: %s", type(exc).__name__, exc)
        return 1
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
