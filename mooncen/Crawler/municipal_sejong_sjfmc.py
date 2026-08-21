"""Fail-closed collector for Sejong Facilities Corporation education courses.

The Sejong Facilities Corporation internet-reservation site exposes two real
education catalogues and three non-course/test catalogues under the same menu.
The reviewed education scope is therefore the exact pair of swimming-course
catalogues below.  Directory drift fails the snapshot closed so a newly added
menu cannot be silently omitted or accidentally classified as education.

List pagination wraps an out-of-range page back to page one.  Every declared
page is read and ``last + 1`` must reproduce page one's marker and identity
signature.  Every listed price/target variant has its own stable detail URL;
all variants are checked against their detail tables before any current/future
rows are returned.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from threading import Lock, local
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


SEJONG_SJFMC_PROVIDER = "SEJONG_SJFMC_EDUCATION"
SEJONG_SJFMC_CANDIDATE_ID = "MUNI_IR_32F6E17F0ADD"
SEJONG_SJFMC_URL = "https://onestop.sjfmc.or.kr/lecture/llist/index"
SEJONG_SJFMC_HOST = "onestop.sjfmc.or.kr"
SEJONG_SJFMC_PATH = "/lecture/llist/index"
SEJONG_SJFMC_PAGE_SIZE = 20
SEJONG_SJFMC_MAX_WORKERS = 8
SEJONG_SJFMC_MUNICIPALITY_CODE = "3611000000"
SEJONG_SJFMC_MUNICIPALITY_NAME = "세종특별자치시"
SEJONG_SJFMC_PARSER = (
    "sejong_sjfmc_exact_education_directory+declared_pages+"
    "wrapped_page_one_recheck+all_variant_details"
)


@dataclass(frozen=True)
class SejongSource:
    center_code: str
    center_name: str
    address: str
    website_url: str
    category_code: str = "100"

    @property
    def key(self) -> str:
        return f"{self.center_code}:{self.category_code}"

    @property
    def branch(self) -> str:
        return f"{SEJONG_SJFMC_MUNICIPALITY_NAME} · {self.center_name}"


SEJONG_SJFMC_SOURCES: tuple[SejongSource, ...] = (
    SejongSource(
        "SEJONG01",
        "보람수영장",
        "세종특별자치시 호려울로 42",
        "https://www.sjfmc.or.kr/boram/sub01_02.do",
    ),
    SejongSource(
        "SEJONG03",
        "조치원복합커뮤니티센터",
        "세종특별자치시 조치원읍 대첩로 76",
        "https://www.sjfmc.or.kr/kor/sub04_02_03.do",
    ),
)
SEJONG_SJFMC_SOURCE_BY_KEY = {source.key: source for source in SEJONG_SJFMC_SOURCES}

# The directory contract includes excluded siblings so they cannot quietly
# become part of the education data plane or hide a newly introduced menu.
SEJONG_SJFMC_DIRECTORY: tuple[tuple[str, str, str], ...] = (
    ("SEJONG01", "100", "수영프로그램"),
    ("SEJONG01", "101", "테스트프로그램"),
    ("SEJONG03", "100", "수영"),
    ("SEJONG03", "102", "시민 물놀이장"),
    ("SEJONG03", "103", "시스템 점검"),
)
SEJONG_SJFMC_EXCLUDED_CATALOGUES: tuple[dict[str, str], ...] = (
    {
        "center_code": "SEJONG01",
        "category_code": "101",
        "name": "테스트프로그램",
        "reason": "test_catalogue",
    },
    {
        "center_code": "SEJONG03",
        "category_code": "102",
        "name": "시민 물놀이장",
        "reason": "wrong_category_admission_ticket",
    },
    {
        "center_code": "SEJONG03",
        "category_code": "103",
        "name": "시스템 점검",
        "reason": "test_catalogue",
    },
)


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DIRECTORY_PATH_RE = re.compile(
    r"/lecture/llist/index/(SEJONG\d{2})/2001/L/(\d+)\Z"
)
_DETAIL_PATH_RE = re.compile(
    r"/lecture/detail/index/(SEJONG\d{2})/2001/([A-Z0-9]+)/([A-Z0-9]+)\Z"
)
_PAGE_INFO_RE = re.compile(
    r"전체갯수\s*:\s*([\d,]+)\s*페이지\s*:\s*(\d+)\s*/\s*(\d+)"
)
_DATE_RANGE_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})"
)
_DATETIME_RANGE_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s+([0-2]\d:[0-5]\d)\s*"
    r"(?:\([^)]*\))?\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s+([0-2]\d:[0-5]\d)"
)
_INTEGER_RE = re.compile(r"\d+")
_STATUS_MAP = {
    "접수준비": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "대기접수": "OPEN",
    "접수마감": "CLOSED",
    "접수종료": "CLOSED",
    "정원마감": "CLOSED",
    "추첨중": "CLOSED",
    "마감": "CLOSED",
}


class SejongSjfmcContractError(ValueError):
    """The official source no longer matches the reviewed contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return _clean(value).casefold()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def is_sejong_sjfmc_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != SEJONG_SJFMC_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname == SEJONG_SJFMC_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path.rstrip("/") == SEJONG_SJFMC_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_sejong_sjfmc_target


def sejong_sjfmc_list_url(source: SejongSource, page: int) -> str:
    if page < 1:
        raise ValueError("page must be positive")
    return (
        f"https://{SEJONG_SJFMC_HOST}{SEJONG_SJFMC_PATH}/"
        f"{source.center_code}/2001/L/{source.category_code}/"
        f"0/0/0/0/0/0/0/1/-/-/2/{page}"
    )


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": SEJONG_SJFMC_URL,
        }
    )
    return current


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    response = current.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise SejongSjfmcContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise SejongSjfmcContractError("redirect response is not accepted")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("HTML fetcher returned neither HTML nor a response")
    encoding = _clean(getattr(value, "encoding", "")) or "euc-kr"
    try:
        text = content.decode(encoding, errors="strict")
    except (LookupError, UnicodeDecodeError):
        text = content.decode("euc-kr", errors="strict")
    return BeautifulSoup(text, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _validate_ownership(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if not title.startswith("세종시설공단 - 인터넷예약시스템"):
        raise SejongSjfmcContractError("site ownership title changed")


def _directory_contract(soup: BeautifulSoup) -> tuple[tuple[str, str, str], ...]:
    _validate_ownership(soup)
    found: set[tuple[str, str, str]] = set()
    for anchor in soup.select("a[href]"):
        parsed = urlparse(urljoin(SEJONG_SJFMC_URL, _clean(anchor.get("href"))))
        if parsed.scheme != "https" or parsed.hostname != SEJONG_SJFMC_HOST:
            continue
        match = _DIRECTORY_PATH_RE.fullmatch(parsed.path.rstrip("/"))
        if not match or parsed.query or parsed.fragment:
            continue
        found.add((match.group(1), match.group(2), _clean(anchor.get_text(" ", strip=True))))
    contract = tuple(sorted(found))
    expected = tuple(sorted(SEJONG_SJFMC_DIRECTORY))
    if contract != expected:
        raise SejongSjfmcContractError(
            f"education directory changed: expected {expected!r}, got {contract!r}"
        )
    return contract


def _page_contract(soup: BeautifulSoup) -> tuple[int, int, int]:
    nodes = soup.select("li.total_info")
    if len(nodes) != 1:
        raise SejongSjfmcContractError("list page total marker changed")
    match = _PAGE_INFO_RE.fullmatch(_clean(nodes[0].get_text(" ", strip=True)))
    if not match:
        raise SejongSjfmcContractError("list page total marker is malformed")
    total, active, last = (
        int(match.group(1).replace(",", "")),
        int(match.group(2)),
        int(match.group(3)),
    )
    expected_last = max(1, (total + SEJONG_SJFMC_PAGE_SIZE - 1) // SEJONG_SJFMC_PAGE_SIZE)
    if last != expected_last or active < 1 or active > last:
        raise SejongSjfmcContractError("declared page count/current marker changed")
    return total, active, last


def _owned_detail_url(raw_url: Any, source: SejongSource) -> tuple[str, str, str]:
    absolute = urljoin(SEJONG_SJFMC_URL, _clean(raw_url))
    parsed = urlparse(absolute)
    match = _DETAIL_PATH_RE.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != SEJONG_SJFMC_HOST
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not match
        or match.group(1) != source.center_code
    ):
        raise SejongSjfmcContractError("list row contains an unowned detail URL")
    return absolute, match.group(2), match.group(3)


def _parse_list_page(
    soup: BeautifulSoup,
    source: SejongSource,
) -> list[dict[str, Any]]:
    _validate_ownership(soup)
    tables = soup.select("table.table_class_list")
    if len(tables) != 1:
        raise SejongSjfmcContractError("course list table changed")
    result: list[dict[str, Any]] = []
    carry: dict[str, Any] = {}
    for tr in tables[0].select("tbody > tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        detail_anchors = [
            anchor
            for anchor in tr.select("a[href]")
            if "/lecture/detail/index/" in _clean(anchor.get("href"))
        ]
        urls: list[str] = []
        identities: dict[str, tuple[str, str]] = {}
        for anchor in detail_anchors:
            absolute, course_id, item_id = _owned_detail_url(anchor.get("href"), source)
            if absolute not in urls:
                urls.append(absolute)
                identities[absolute] = (course_id, item_id)
        if not urls:
            continue
        if len(urls) != 1:
            raise SejongSjfmcContractError("one visual list row owns multiple detail identities")
        raw_url = urls[0]
        course_id, item_id = identities[raw_url]

        status_cells = [cell for cell in cells if "table_content7" in (cell.get("class") or [])]
        if status_cells:
            if len(status_cells) != 1:
                raise SejongSjfmcContractError("list row status cell changed")
            parent_link = cells[0].select_one("a[href*='/lecture/detail/index/']")
            if parent_link is None:
                raise SejongSjfmcContractError("parent course title link is missing")
            carry["parent_title"] = _clean(parent_link.get_text(" ", strip=True))
            status_images = status_cells[0].select("img[alt]")
            if len(status_images) != 1:
                raise SejongSjfmcContractError("list row status image changed")
            source_status = _clean(status_images[0].get("alt"))
            if source_status not in _STATUS_MAP:
                raise SejongSjfmcContractError(f"unknown source status {source_status!r}")
            carry["source_status"] = source_status
            teacher = next(
                (
                    _clean(cell.get_text(" ", strip=True))
                    for cell in cells[1:]
                    if "table_content8" in (cell.get("class") or [])
                    and not cell.select("a[href]")
                ),
                "",
            )
            target = next(
                (
                    _clean(cell.get_text(" ", strip=True))
                    for cell in cells
                    if "table_content2" in (cell.get("class") or [])
                ),
                "",
            )
            schedule = next(
                (
                    _clean(cell.get_text(" ", strip=True))
                    for cell in cells
                    if "table_content3" in (cell.get("class") or [])
                ),
                "",
            )
            remaining_text = next(
                (
                    _clean(cell.get_text(" ", strip=True))
                    for cell in cells
                    if "table_content6" in (cell.get("class") or [])
                ),
                "",
            )
            if not target or not schedule or not remaining_text:
                raise SejongSjfmcContractError("parent list fields changed")
            carry.update(
                {
                    "instructor": teacher,
                    "target": target,
                    "schedule": schedule,
                    "remaining_text": remaining_text,
                }
            )
        required = {
            "parent_title",
            "source_status",
            "target",
            "schedule",
            "remaining_text",
        }
        if not required.issubset(carry):
            raise SejongSjfmcContractError("continuation row appeared before its parent row")

        linked_texts: list[str] = []
        for anchor in detail_anchors:
            text = _clean(anchor.get_text(" ", strip=True))
            if text and text not in linked_texts:
                linked_texts.append(text)
        if carry["parent_title"] in linked_texts:
            linked_texts.remove(carry["parent_title"])
        if len(linked_texts) != 1:
            raise SejongSjfmcContractError("course price/target variant label changed")
        variant = linked_texts[0]
        price_cells = [
            cell
            for cell in cells
            if "table_content5" in (cell.get("class") or [])
        ]
        if len(price_cells) != 1:
            raise SejongSjfmcContractError("course list price cell changed")
        result.append(
            {
                "source": source,
                "course_id": course_id,
                "item_id": item_id,
                "raw_url": raw_url,
                "parent_title": carry["parent_title"],
                "variant": variant,
                "source_status": carry["source_status"],
                "status": _STATUS_MAP[carry["source_status"]],
                "target": carry["target"],
                "schedule": carry["schedule"],
                "instructor": carry.get("instructor", ""),
                "remaining_text": carry["remaining_text"],
                "list_price": _clean(price_cells[0].get_text(" ", strip=True)),
            }
        )
    return result


def _list_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("course_id")),
            _clean(row.get("item_id")),
            _clean(row.get("parent_title")),
            _clean(row.get("variant")),
            _clean(row.get("source_status")),
        )
        for row in rows
    )


def _direct_pairs(table: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) != 2:
            continue
        key = _clean(cells[0].get_text(" ", strip=True))
        value = _clean(cells[1].get_text(" ", strip=True))
        if not key or key in pairs:
            raise SejongSjfmcContractError("detail table has a missing/duplicate label")
        pairs[key] = value
    return pairs


def _integer(value: Any, label: str) -> int:
    numbers = _INTEGER_RE.findall(_clean(value).replace(",", ""))
    if len(numbers) != 1:
        raise SejongSjfmcContractError(f"{label} is not one integer")
    return int(numbers[0])


def _branch_code(source: SejongSource) -> str:
    digest = hashlib.sha1(source.branch.encode("utf-8")).hexdigest()[:12].upper()
    return f"{SEJONG_SJFMC_PROVIDER}:CENTER:{digest}"[:100]


def _application_control(soup: BeautifulSoup) -> bool:
    areas = soup.select("div.lecture_status_area")
    if len(areas) != 1:
        raise SejongSjfmcContractError("detail application-status area changed")
    action_roots = [areas[0], *soup.select("div.button_area1")]
    for root in action_roots:
        for node in root.select("a[href], button, input[type='submit'], input[type='image']"):
            descriptor = " ".join(
                _clean(value)
                for value in (
                    node.get_text(" ", strip=True),
                    node.get("alt"),
                    node.get("title"),
                    node.get("value"),
                    " ".join(_clean(image.get("alt")) for image in node.select("img[alt]")),
                )
                if _clean(value)
            )
            if ("신청" in descriptor or "접수" in descriptor) and "목록" not in descriptor:
                return True
    return False


def _parse_detail(
    target: Any,
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    cutoff: date,
) -> dict[str, Any]:
    _validate_ownership(soup)
    preview_tables = soup.select("table.lecture_preview_table")
    if len(preview_tables) != 1:
        raise SejongSjfmcContractError("detail preview table changed")
    preview = preview_tables[0]
    title_nodes = preview.select("td.table_lecture_title")
    if len(title_nodes) != 1:
        raise SejongSjfmcContractError("detail title cell changed")
    detail_title = _clean(title_nodes[0].get_text(" ", strip=True))
    expected_title = f"{_clean(listed['parent_title'])} - {_clean(listed['variant'])}"
    if detail_title != expected_title:
        raise SejongSjfmcContractError("list/detail course title mismatch")
    pairs = _direct_pairs(preview)
    required = {
        "교육대상",
        "교육기간",
        "교육시간",
        "교육장소",
        "수강료(원)",
        "신규접수기간",
    }
    if not required.issubset(pairs):
        raise SejongSjfmcContractError("detail preview fields changed")
    period_match = _DATE_RANGE_RE.search(pairs["교육기간"])
    apply_match = _DATETIME_RANGE_RE.search(pairs["신규접수기간"])
    if not period_match or not apply_match:
        raise SejongSjfmcContractError("detail education/application period is malformed")
    start_date, end_date = period_match.groups()
    apply_start_at = f"{apply_match.group(1)} {apply_match.group(2)}"
    apply_end_at = f"{apply_match.group(3)} {apply_match.group(4)}"
    if date.fromisoformat(end_date) < date.fromisoformat(start_date):
        raise SejongSjfmcContractError("detail education period is reversed")
    if datetime.fromisoformat(apply_end_at) < datetime.fromisoformat(apply_start_at):
        raise SejongSjfmcContractError("detail application period is reversed")
    if _integer(pairs["수강료(원)"], "detail price") != _integer(
        listed["list_price"], "list price"
    ):
        raise SejongSjfmcContractError("list/detail price mismatch")

    status_tables = soup.select("table.receipt_status_table1")
    if len(status_tables) != 1:
        raise SejongSjfmcContractError("detail receipt-status table changed")
    status_rows = status_tables[0].select("tbody > tr")
    if len(status_rows) != 1:
        raise SejongSjfmcContractError("detail receipt-status row changed")
    status_cells = status_rows[0].find_all("td", recursive=False)
    if len(status_cells) != 3:
        raise SejongSjfmcContractError("detail receipt-status columns changed")
    application_method = _clean(status_cells[0].get_text(" ", strip=True))
    capacity_total = _integer(status_cells[1].get_text(" ", strip=True), "capacity")
    capacity_current = _integer(status_cells[2].get_text(" ", strip=True), "applications")
    remaining = max(0, capacity_total - capacity_current)
    list_capacity_value = _integer(
        listed["remaining_text"],
        "list capacity summary",
    )
    list_detail_capacity_mismatch = list_capacity_value != remaining

    detail_tables = soup.select("table.lecture_detail_table")
    if len(detail_tables) != 1:
        raise SejongSjfmcContractError("course-description table changed")
    descriptions = _direct_pairs(detail_tables[0])
    description = " ".join(
        value for key, value in descriptions.items() if value and key != "강의계획서"
    )
    source: SejongSource = listed["source"]
    status = _clean(listed["status"])
    application_available = _application_control(soup)
    if status == "OPEN" and not application_available:
        raise SejongSjfmcContractError("open course has no public application control")
    raw_url = _clean(listed["raw_url"])
    price = _integer(pairs["수강료(원)"], "detail price")
    identity = f"{source.center_code}:{listed['course_id']}:{listed['item_id']}"
    row: dict[str, Any] = {
        "provider": SEJONG_SJFMC_PROVIDER,
        "provider_course_id": f"{SEJONG_SJFMC_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": detail_title,
        "branch": source.branch,
        "branch_code": _branch_code(source),
        "branch_address": source.address,
        "branch_address_source": "official_facility_page",
        "branch_url": source.website_url,
        "period": f"{start_date} ~ {end_date}",
        "start_date": start_date,
        "end_date": end_date,
        "apply_period": f"{apply_start_at} ~ {apply_end_at}",
        "apply_start_date": apply_start_at[:10],
        "apply_end_date": apply_end_at[:10],
        "status": status,
        "category": "체육·건강",
        "program_type": "시설공단 교육·강좌",
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "complete_paginated_catalogue+all_details",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "instructor": _clean(listed.get("instructor")),
        "schedule_raw": pairs["교육시간"],
        "target": pairs["교육대상"],
        "room": pairs["교육장소"],
        "venue": pairs["교육장소"],
        "venue_address": source.address,
        "description": description,
        "fee": price,
        "price": price,
        "price_text": f"{price:,}원",
        "capacity_total": capacity_total,
        "capacity_current": capacity_current,
        "capacity_remaining": remaining,
        "application_method": application_method,
        "application_methods": ["온라인", "추첨"] if "추첨" in application_method else ["온라인"],
        "reservation_available": status == "OPEN",
        "application_url": raw_url if status == "OPEN" else "",
        "application_type": "ONLINE_RESERVATION" if status == "OPEN" else "",
        "raw_url": raw_url,
        "source_url": _clean(_target_value(target, "url")),
        "municipality_code": SEJONG_SJFMC_MUNICIPALITY_CODE,
        "municipality_full_name": SEJONG_SJFMC_MUNICIPALITY_NAME,
        "municipality_region_verified": True,
        "region_sido": SEJONG_SJFMC_MUNICIPALITY_NAME,
        "region_sigungu": SEJONG_SJFMC_MUNICIPALITY_NAME,
        "raw_fields": {
            "center_code": source.center_code,
            "center_name": source.center_name,
            "category_code": source.category_code,
            "course_id": _clean(listed["course_id"]),
            "item_id": _clean(listed["item_id"]),
            "source_status": _clean(listed["source_status"]),
            "list_schedule": _clean(listed["schedule"]),
            "list_target": _clean(listed["target"]),
            "list_capacity_text": _clean(listed["remaining_text"]),
            "list_capacity_value": list_capacity_value,
            "list_detail_capacity_mismatch": list_detail_capacity_mismatch,
            "municipality_code": SEJONG_SJFMC_MUNICIPALITY_CODE,
            "municipality_name": SEJONG_SJFMC_MUNICIPALITY_NAME,
            "data_plane": "official_euc_kr_html_list_and_detail",
        },
    }
    row["raw_fields"]["expired_at_collection"] = date.fromisoformat(end_date) < cutoff
    return row


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "landing_requests": 0,
        "landing_rechecks": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "declared_totals": {},
        "declared_pages": {},
        "page_counts": {},
        "source_rows": 0,
        "current_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "list_detail_capacity_mismatch_count": 0,
        "unique_id_count": 0,
        "duplicate_count": 0,
        "semantic_duplicate_count": 0,
        "status_counts": {},
        "source_counts": {},
        "excluded_catalogues": list(SEJONG_SJFMC_EXCLUDED_CATALOGUES),
        "reservation_discovery_links": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "recursion_depth": 0,
        "configured_collection_error": "",
    }


def collect_sejong_sjfmc_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = SEJONG_SJFMC_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future Sejong SJFMC education snapshot."""

    meta = _base_meta()
    if not is_sejong_sjfmc_target(target):
        meta["configured_collection_error"] = "target is not the exact reviewed Sejong SJFMC education URL"
        return [], SEJONG_SJFMC_PARSER, meta
    try:
        page_cap = int(max_pages)
        detail_cap = int(detail_limit)
        workers = int(max_workers)
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "collection limits are not integers"
        return [], SEJONG_SJFMC_PARSER, meta
    if page_cap < 1 or detail_cap < 0 or workers < 1:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "collection limits are invalid"
        return [], SEJONG_SJFMC_PARSER, meta

    current_fetcher = fetcher or _default_fetcher
    current_session_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    cutoff = _today(today)
    errors: list[str] = []
    list_session: Any = None
    detail_sessions: list[Any] = []
    detail_session_lock = Lock()
    thread_state = local()
    listed_rows: list[dict[str, Any]] = []
    first_signatures: dict[str, tuple[tuple[str, ...], ...]] = {}

    def detail_session() -> Any:
        value = getattr(thread_state, "session", None)
        if value is None:
            value = current_session_factory()
            thread_state.session = value
            with detail_session_lock:
                detail_sessions.append(value)
        return value

    try:
        list_session = current_session_factory()
        try:
            landing = _coerce_soup(current_fetcher(list_session, SEJONG_SJFMC_URL, timeout))
            meta["landing_requests"] += 1
            _directory_contract(landing)
        except Exception as exc:
            errors.append(f"directory: {type(exc).__name__}: {_clean(exc)}")

        declarations: dict[str, tuple[int, int]] = {}
        first_pages: dict[str, BeautifulSoup] = {}
        if not errors:
            for source in SEJONG_SJFMC_SOURCES:
                try:
                    soup = _coerce_soup(
                        current_fetcher(list_session, sejong_sjfmc_list_url(source, 1), timeout)
                    )
                    meta["list_requests"] += 1
                    total, active, last = _page_contract(soup)
                    if active != 1:
                        raise SejongSjfmcContractError("first list page is not active page one")
                    first_pages[source.key] = soup
                    declarations[source.key] = (total, last)
                    meta["declared_totals"][source.key] = total
                    meta["declared_pages"][source.key] = last
                    meta["pagination_detected"] = bool(meta["pagination_detected"] or last > 1)
                except Exception as exc:
                    errors.append(f"{source.key} first page: {type(exc).__name__}: {_clean(exc)}")
        required = sum(last + 1 for _, last in declarations.values())
        meta["required_list_requests"] = required
        if len(declarations) != len(SEJONG_SJFMC_SOURCES):
            errors.append("reviewed two-source fan-out discovery is incomplete")
        if required > page_cap:
            meta["source_cap_reached"] = True
            errors.append(f"max_pages cap {page_cap} is below {required} required list requests")

        if not errors:
            # Page one has already been requested and is reused here.
            for source in SEJONG_SJFMC_SOURCES:
                total, last = declarations[source.key]
                source_rows: list[dict[str, Any]] = []
                for page in range(1, last + 2):
                    try:
                        soup = first_pages[source.key] if page == 1 else _coerce_soup(
                            current_fetcher(list_session, sejong_sjfmc_list_url(source, page), timeout)
                        )
                        if page != 1:
                            meta["list_requests"] += 1
                        observed_total, active, observed_last = _page_contract(soup)
                        expected_active = page if page <= last else 1
                        if observed_total != total or observed_last != last or active != expected_active:
                            raise SejongSjfmcContractError("pagination marker/wrap contract changed")
                        page_rows = _parse_list_page(soup, source)
                        meta["page_counts"][f"{source.key}:{page}"] = len(page_rows)
                        signature = _list_signature(page_rows)
                        if page == 1:
                            first_signatures[source.key] = signature
                        elif page == last + 1:
                            if signature != first_signatures[source.key]:
                                raise SejongSjfmcContractError("wrapped sentinel differs from page one")
                            continue
                        expected_count = (
                            SEJONG_SJFMC_PAGE_SIZE
                            if page < last
                            else total - SEJONG_SJFMC_PAGE_SIZE * (last - 1)
                        )
                        if len(page_rows) != expected_count:
                            raise SejongSjfmcContractError(
                                f"page {page} has {len(page_rows)} rows, expected {expected_count}"
                            )
                        source_rows.extend(page_rows)
                    except Exception as exc:
                        errors.append(f"{source.key} page {page}: {type(exc).__name__}: {_clean(exc)}")
                if len(source_rows) != total:
                    errors.append(f"{source.key}: parsed {len(source_rows)} of declared {total} rows")
                meta["source_counts"][source.key] = len(source_rows)
                listed_rows.extend(source_rows)

        identities = [
            f"{row['source'].center_code}:{row['course_id']}:{row['item_id']}"
            for row in listed_rows
        ]
        meta["source_rows"] = len(listed_rows)
        meta["unique_id_count"] = len(set(identities))
        meta["duplicate_count"] = len(identities) - len(set(identities))
        if meta["duplicate_count"]:
            errors.append(f"catalogue contains {meta['duplicate_count']} duplicate identities")
        if len(listed_rows) > detail_cap:
            meta["source_cap_reached"] = True
            errors.append(f"detail_limit cap {detail_cap} is below {len(listed_rows)} required details")

        detail_rows: list[dict[str, Any]] = []
        if not errors:
            meta["detail_attempts"] = len(listed_rows)

            def fetch_detail(listed: Mapping[str, Any]) -> dict[str, Any]:
                soup = _coerce_soup(
                    current_fetcher(detail_session(), _clean(listed["raw_url"]), timeout)
                )
                return _parse_detail(target, listed, soup, cutoff)

            with ThreadPoolExecutor(max_workers=min(workers, max(1, len(listed_rows)))) as executor:
                futures = {executor.submit(fetch_detail, listed): listed for listed in listed_rows}
                for future in as_completed(futures):
                    listed = futures[future]
                    identity = f"{listed['source'].center_code}:{listed['course_id']}:{listed['item_id']}"
                    try:
                        detail_rows.append(future.result())
                        meta["detail_pages"] += 1
                    except Exception as exc:
                        meta["detail_errors"] += 1
                        errors.append(f"detail {identity}: {type(exc).__name__}: {_clean(exc)}")

        try:
            landing_recheck = _coerce_soup(
                current_fetcher(list_session, SEJONG_SJFMC_URL, timeout)
            )
            meta["landing_requests"] += 1
            meta["landing_rechecks"] += 1
            _directory_contract(landing_recheck)
        except Exception as exc:
            errors.append(f"directory recheck: {type(exc).__name__}: {_clean(exc)}")

        detail_rows.sort(key=lambda row: _clean(row.get("provider_course_id")))
        meta["list_detail_capacity_mismatch_count"] = sum(
            bool(
                (row.get("raw_fields") or {}).get(
                    "list_detail_capacity_mismatch"
                )
            )
            for row in detail_rows
        )
        current_rows = [
            row for row in detail_rows if date.fromisoformat(row["end_date"]) >= cutoff
        ]
        meta["expired_count"] = len(detail_rows) - len(current_rows)
        meta["current_count"] = len(current_rows)
        meta["status_counts"] = dict(Counter(row["status"] for row in current_rows))
        semantic_counts = Counter(
            (
                _normalized(row.get("title")),
                _normalized(row.get("period")),
                _normalized(row.get("schedule_raw")),
                _normalized(row.get("branch")),
            )
            for row in current_rows
        )
        meta["semantic_duplicate_count"] = sum(
            count - 1 for count in semantic_counts.values() if count > 1
        )
        if meta["semantic_duplicate_count"]:
            errors.append(f"snapshot contains {meta['semantic_duplicate_count']} semantic duplicates")
        cleaned = list(current_rows)
        if not errors:
            try:
                deduped = list(current_dedupe(cleaned))
            except Exception as exc:
                errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                deduped = []
            if len(deduped) != len(cleaned):
                errors.append(f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}")
            cleaned = deduped

        meta["pages"] = meta["landing_requests"] + meta["list_requests"]
        meta["pagination_complete"] = (
            not meta["source_cap_reached"]
            and meta["list_requests"] == required
            and len(first_signatures) == len(SEJONG_SJFMC_SOURCES)
            and sum(meta["source_counts"].values()) == sum(meta["declared_totals"].values())
        )
        meta["details_complete"] = (
            not meta["source_cap_reached"]
            and meta["detail_attempts"] == len(listed_rows)
            and meta["detail_pages"] == len(listed_rows)
            and meta["detail_errors"] == 0
        )
        meta["snapshot_complete"] = (
            not errors
            and meta["landing_rechecks"] == 1
            and meta["pagination_complete"]
            and meta["details_complete"]
            and meta["duplicate_count"] == 0
            and meta["semantic_duplicate_count"] == 0
        )
        meta["full_snapshot_validated"] = meta["snapshot_complete"]
        if not meta["snapshot_complete"]:
            cleaned = []
        meta["returned_count"] = len(cleaned)
        meta["reservation_discovery_links"] = sum(
            bool(row.get("application_url")) for row in cleaned
        )
        meta["no_current_data"] = meta["snapshot_complete"] and not current_rows
        if meta["no_current_data"]:
            meta["no_current_reason"] = (
                "the complete reviewed Sejong SJFMC education catalogues have no current/future courses"
            )
        if errors:
            meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return cleaned, SEJONG_SJFMC_PARSER, meta
    finally:
        _close_quietly(list_session)
        for value in detail_sessions:
            _close_quietly(value)


collect = collect_sejong_sjfmc_education


__all__ = [
    "SEJONG_SJFMC_CANDIDATE_ID",
    "SEJONG_SJFMC_DIRECTORY",
    "SEJONG_SJFMC_EXCLUDED_CATALOGUES",
    "SEJONG_SJFMC_HOST",
    "SEJONG_SJFMC_MAX_WORKERS",
    "SEJONG_SJFMC_MUNICIPALITY_CODE",
    "SEJONG_SJFMC_MUNICIPALITY_NAME",
    "SEJONG_SJFMC_PAGE_SIZE",
    "SEJONG_SJFMC_PARSER",
    "SEJONG_SJFMC_PATH",
    "SEJONG_SJFMC_PROVIDER",
    "SEJONG_SJFMC_SOURCES",
    "SEJONG_SJFMC_URL",
    "SejongSjfmcContractError",
    "SejongSource",
    "collect",
    "collect_sejong_sjfmc_education",
    "is_sejong_sjfmc_target",
    "is_target",
    "sejong_sjfmc_list_url",
]
