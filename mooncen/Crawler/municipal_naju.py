"""Fail-closed collectors for Naju City's official education catalogues.

The lifelong-learning ``all`` route is the canonical superset of its menu
partitions (city hall, resident centres, libraries, public institutions,
lifelong institutions and external applications).  The configured ``other``
route is consequently a duplicate subset and must not own a second snapshot.

The official list silently defaults its education-start filter to January 1
of the current year.  That can omit a long-running course which started in an
earlier year, so the collector explicitly audits the complete supported
history from 2000-01-01.  A snapshot is published only after the declared
total, every fifteen-row page, the immediately following empty sentinel, and
every current/future detail page have all passed their contracts.

The public-activities centre catalogue is a separate, non-overlapping source.
It exposes its list through a same-origin JSON POST API and is collected under
its existing provider.  It follows the same total/page/sentinel/detail rules.

This module intentionally does not import ``Crawler_MunicipalYaml``.  The
shared router must inject its managed fetcher/session factory/dedupe function.
Raw ``requests`` are available only behind the explicit test-only opt-in.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
from html import unescape
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


NAJU_LIFELONG_PROVIDER = "MUNI_WWW_NAJU_GO_KR_406D58D1"
NAJU_LIFELONG_URL = "https://www.naju.go.kr/edu/lifelong/course_reception/all"
NAJU_LIFELONG_PATH = "/edu/lifelong/course_reception/all"
NAJU_LIFELONG_HISTORY_START = "2000-01-01"
NAJU_LIFELONG_PAGE_SIZE = 15
NAJU_LIFELONG_SESSION_LIMIT = 75
NAJU_LIFELONG_PARSER = "naju_lifelong_complete_history+sentinel+current_detail"

NAJU_GONGIK_PROVIDER = "MUNI_WWW_NAJU_GO_KR_DE1B1AE9"
NAJU_GONGIK_URL = "https://www.naju.go.kr/gongik/use_app/edu"
NAJU_GONGIK_PATH = "/gongik/use_app/edu"
NAJU_GONGIK_PAGE_SIZE = 15
NAJU_GONGIK_PARSER = "naju_gongik_json_pages+sentinel+current_detail"

NAJU_HOST = "www.naju.go.kr"
NAJU_MUNICIPALITY_CODE = "1217000000"
NAJU_MUNICIPALITY_NAME = "전남광주통합특별시 나주시"

# Audited menu aliases/partitions of the lifelong ``all`` route.
NAJU_LIFELONG_DUPLICATE_PROVIDER = "MUNI_WWW_NAJU_GO_KR_D8842639"
NAJU_LIFELONG_DUPLICATE_URLS = (
    "https://www.naju.go.kr/edu/lifelong/course_reception/cityhall",
    "https://www.naju.go.kr/edu/lifelong/course_reception/center",
    "https://www.naju.go.kr/edu/lifelong/course_reception/library",
    "https://www.naju.go.kr/edu/lifelong/course_reception/public",
    "https://www.naju.go.kr/edu/lifelong/course_reception/lfcenter",
    "https://www.naju.go.kr/edu/lifelong/course_reception/other",
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_DATETIME_RANGE_RE = re.compile(
    r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})"
    r"(?:\s+(\d{2}):(\d{2})(?::\d{2})?)?\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})"
    r"(?:\s+(\d{2}):(\d{2})(?::\d{2})?)?(?!\d)"
)
_TOTAL_RE = re.compile(r"^전체\s*\(\s*([\d,]+)\s*\)$")
_CAPACITY_RE = re.compile(
    r"(?:신청\s*)?([\d,]+)\s*\(\s*([\d,]+)\s*\)\s*/\s*([\d,]+)\s*명"
)
_SIMPLE_CAPACITY_RE = re.compile(r"(?:정원\s*:\s*)?([\d,]+)\s*명")
_DETAIL_CAPACITY_RE = re.compile(r"^\s*([\d,]+)\s*명(?:\s*:\s*(.*))?$")

_LIFELONG_HEADERS = (
    "번호",
    "[기수]강좌명/강사명/신청기간/교육기간",
    "교육기관",
    "수강생선정방법 신청(대기)/정원",
    "수강료",
    "접수현황",
)
_LIFELONG_REQUIRED_DETAIL_KEYS = (
    "강좌명(기수)",
    "교육대상",
    "수강료",
    "신청기간",
    "교육기간",
    "교육장소",
    "교육분류",
    "수강신청방법",
    "수강신청선정방법",
    "교육기관",
    "문의전화",
    "강좌소개 강의계획",
    "강사명",
    "모집정원",
    "모집대기인원",
)
_GONGIK_REQUIRED_DETAIL_KEYS = (
    "모집명",
    "주관부서",
    "모집기간",
    "모집대상",
    "모집정원",
    "접수방법",
    "선정방법",
    "교육기간",
    "요일/시간",
    "교육장소",
    "교육소개",
    "문의전화",
)
_LIFELONG_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
    "수강대기": "CLOSED",
    "수강중": "CLOSED",
    "수강종료": "CLOSED",
    "강의종료": "CLOSED",
    "수강확정": "CLOSED",
    "폐강": "CANCELLED",
    # External-information records do not expose an internal reservation
    # state.  They remain visible as informational, non-reservable rows.
    "교육정보": "CLOSED",
}
_GONGIK_STATUS_MAP: Mapping[str, str] = {
    "ing": "OPEN",
    "wait": "SCHEDULED",
    "end": "CLOSED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\s\u200b]+", "", _clean(value)).casefold()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _is_exact_target(target: Any, provider: str, path: str) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == provider
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == NAJU_HOST
        and parsed.port is None
        and parsed.path == path
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_naju_lifelong_target(target: Any) -> bool:
    return _is_exact_target(target, NAJU_LIFELONG_PROVIDER, NAJU_LIFELONG_PATH)


def is_naju_gongik_target(target: Any) -> bool:
    return _is_exact_target(target, NAJU_GONGIK_PROVIDER, NAJU_GONGIK_PATH)


def is_naju_education_target(target: Any) -> bool:
    return is_naju_lifelong_target(target) or is_naju_gongik_target(target)


def naju_lifelong_list_url(page: Any = 1) -> str:
    raw_page = _clean(page)
    if not _IDENTITY_RE.fullmatch(raw_page) or int(raw_page) < 1:
        return ""
    query: list[tuple[str, Any]] = []
    if int(raw_page) > 1:
        query.append(("page", int(raw_page)))
    query.extend(
        (
            ("search_startdate", NAJU_LIFELONG_HISTORY_START),
            ("search_status", "all"),
        )
    )
    return f"https://{NAJU_HOST}{NAJU_LIFELONG_PATH}?{urlencode(query)}"


def naju_lifelong_detail_url(identity: Any) -> str:
    raw_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{NAJU_HOST}{NAJU_LIFELONG_PATH}?" + urlencode(
        (("idx", raw_identity), ("mode", "view"))
    )


def naju_lifelong_application_url(identity: Any) -> str:
    raw_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{NAJU_HOST}{NAJU_LIFELONG_PATH}?" + urlencode(
        (("lecture_idx", raw_identity), ("mode", "reserve_form"))
    )


def naju_gongik_page_url(page: Any = 1) -> str:
    raw_page = _clean(page)
    if not _IDENTITY_RE.fullmatch(raw_page) or int(raw_page) < 1:
        return ""
    if int(raw_page) == 1:
        return NAJU_GONGIK_URL
    return f"{NAJU_GONGIK_URL}?{urlencode((('page', int(raw_page)), ('sub_mode', 'all')))}"


def naju_gongik_detail_url(identity: Any) -> str:
    raw_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"{NAJU_GONGIK_URL}?" + urlencode(
        (("idx", raw_identity), ("mode", "view"))
    )


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    return current_session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(value, "history", None):
        raise ValueError("HTTP redirects are not accepted")
    raise_for_status = getattr(value, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise ValueError("empty HTTP response")
    return BeautifulSoup(content, "lxml")


def _fetch(fetcher: Fetcher, current: Any, url: str, timeout: int) -> BeautifulSoup:
    if not url:
        raise ValueError("empty fetch URL")
    last_error: Optional[Exception] = None
    for _attempt in range(2):
        try:
            return _coerce_soup(fetcher(current, url, timeout))
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _date_range(value: Any) -> tuple[str, str, str]:
    values: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            values.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    if len(values) != 2 or values[1] < values[0]:
        return "", "", ""
    start, end = values
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _date_tokens(value: Any) -> list[date]:
    values: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            values.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    return values


def _datetime_range(value: Any) -> tuple[str, str, str]:
    match = _DATETIME_RANGE_RE.fullmatch(_clean(value))
    if not match:
        return "", "", ""
    try:
        start_date = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        end_date = date(int(match.group(6)), int(match.group(7)), int(match.group(8)))
    except ValueError:
        return "", "", ""
    if end_date < start_date:
        return "", "", ""
    start = start_date.isoformat()
    end = end_date.isoformat()
    if match.group(4) and match.group(5):
        start += f" {match.group(4)}:{match.group(5)}"
    if match.group(9) and match.group(10):
        end += f" {match.group(9)}:{match.group(10)}"
    return start, end, f"{start} ~ {end}"


def _integer(value: Any) -> Optional[int]:
    match = re.search(r"[\d,]+", _clean(value))
    return int(match.group(0).replace(",", "")) if match else None


def _stable_branch_code(branch: Any) -> str:
    digest = hashlib.sha1(_normalized(branch).encode("utf-8")).hexdigest()[:10].upper()
    return f"NAJU_BRANCH_{digest}"


def _detail_pairs(table: Any) -> dict[str, str]:
    if table is None:
        return {}
    result: dict[str, str] = {}
    body = table.find("tbody", recursive=False)
    rows = body.find_all("tr", recursive=False) if body else table.find_all("tr", recursive=False)
    for row in rows:
        cells = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index + 1 < len(cells):
            if cells[index].name == "th" and cells[index + 1].name == "td":
                key = _clean(cells[index].get_text(" ", strip=True)).rstrip(":：")
                if key and key not in result:
                    result[key] = _clean(cells[index + 1].get_text(" ", strip=True))
                index += 2
            else:
                index += 1
    return result


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("branch")),
        _normalized(row.get("period")),
        _normalized(row.get("schedule_raw")),
    )


def _title_signature(value: Any) -> tuple[str, str]:
    text = _clean(value)
    cohort_match = re.search(r"(?<!\d)(\d+)\s*기(?!\w)", text)
    cohort = cohort_match.group(1) if cohort_match else ""
    base = re.sub(r"[\[(（]?\s*\d+\s*기\s*[\])）]?", "", text)
    return _normalized(base), cohort


def _failure(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "detail_pages": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "current_count": 0,
        "returned_count": 0,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def _lifelong_total(soup: BeautifulSoup) -> int:
    values: set[int] = set()
    for item in soup.select("ul.cate_list > li.first"):
        match = _TOTAL_RE.fullmatch(_clean(item.get_text(" ", strip=True)))
        if match:
            values.add(int(match.group(1).replace(",", "")))
    return values.pop() if len(values) == 1 else 0


def _lifelong_headers(soup: BeautifulSoup) -> tuple[str, ...]:
    tables = [
        table
        for table in soup.select("table.list_table")
        if table.select_one("td.lecture_title") or "강좌관리목록" in _clean(table.get_text(" ", strip=True))
    ]
    if len(tables) != 1:
        return ()
    return tuple(_clean(item.get_text(" ", strip=True)) for item in tables[0].select("thead th"))


def _single_query(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _lifelong_link_identity(value: Any, mode: str) -> tuple[str, str]:
    parsed = urlparse(urljoin(NAJU_LIFELONG_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    key = "idx" if mode == "view" else "lecture_idx"
    identity = _single_query(query, key)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != NAJU_HOST
        or parsed.port is not None
        or parsed.path != NAJU_LIFELONG_PATH
        or _single_query(query, "mode") != mode
        or not _IDENTITY_RE.fullmatch(identity)
        or set(query) != {key, "mode"}
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return "", ""
    canonical = (
        naju_lifelong_detail_url(identity)
        if mode == "view"
        else naju_lifelong_application_url(identity)
    )
    return identity, canonical


def _lifelong_page_number(soup: BeautifulSoup) -> int:
    active = soup.select("div.list_paging div.num > a.on")
    if len(active) != 1:
        return 0
    text = _clean(active[0].get_text(" ", strip=True))
    return int(text) if text.isdigit() else 0


def _lifelong_pagination_contract(
    soup: BeautifulSoup, *, requested_page: int, source_pages: int, sentinel: bool
) -> bool:
    if _lifelong_total(soup) <= 0:
        return False
    active_page = _lifelong_page_number(soup)
    if sentinel:
        return active_page == 0
    if active_page != requested_page:
        return False
    last_links = soup.select("div.list_paging a.last[href]")
    if source_pages <= 1:
        return not last_links
    if requested_page == source_pages:
        return not last_links
    if len(last_links) != 1:
        return False
    parsed = urlparse(urljoin(NAJU_LIFELONG_URL, last_links[0].get("href")))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        parsed.path == NAJU_LIFELONG_PATH
        and _single_query(query, "page") == str(source_pages)
        and _single_query(query, "search_startdate") == NAJU_LIFELONG_HISTORY_START
        and _single_query(query, "search_status") == "all"
    )


def _lifelong_source_status(value: Any) -> tuple[str, str]:
    text = _clean(value).replace("접수하기", "").strip()
    for label in sorted(_LIFELONG_STATUS_MAP, key=len, reverse=True):
        if text == label or text.startswith(f"{label} "):
            suffix = _clean(text[len(label) :])
            if suffix and suffix != "서면접수":
                return "", ""
            return label, _LIFELONG_STATUS_MAP[label]
    return "", ""


def _parse_lifelong_page(
    target: Any, soup: BeautifulSoup, *, page: int
) -> tuple[list[dict[str, Any]], int]:
    if _lifelong_headers(soup) != _LIFELONG_HEADERS:
        return [], 1
    rows: list[dict[str, Any]] = []
    malformed = 0
    for tr in soup.select("table.list_table tbody > tr"):
        link = tr.select_one("td.lecture_title a[href]")
        if link is None:
            empty_text = _clean(tr.get_text(" ", strip=True))
            if not any(
                marker in empty_text
                for marker in ("검색내역이 없습니다", "개설된 강좌가 없습니다")
            ):
                malformed += 1
            continue
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 6:
            malformed += 1
            continue
        identity, raw_url = _lifelong_link_identity(link.get("href"), "view")
        title_node = link.select_one("span.fc_blue3")
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
        lines = [_clean(item.get_text(" ", strip=True)) for item in link.select(":scope > span")]
        values: dict[str, str] = {}
        for line in lines:
            for label in ("강 사 명", "신청기간", "교육기간"):
                if line.startswith(label):
                    values[label] = _clean(line[len(label) :].lstrip(" :："))
        apply_start, apply_end, apply_period = _datetime_range(values.get("신청기간"))
        start, end, period = _date_range(values.get("교육기간"))
        branch = _clean(cells[2].get_text(" ", strip=True))
        source_status, status = _lifelong_source_status(cells[5].get_text(" ", strip=True))
        reserve_links = cells[5].select("a[href*='mode=reserve_form']")
        application_url = ""
        application_identity = ""
        if len(reserve_links) == 1:
            application_identity, application_url = _lifelong_link_identity(
                reserve_links[0].get("href"), "reserve_form"
            )
        capacity_text = _clean(cells[3].get_text(" ", strip=True))
        capacity_match = _CAPACITY_RE.search(capacity_text)
        simple_capacity = _SIMPLE_CAPACITY_RE.search(capacity_text)
        current_count = int(capacity_match.group(1).replace(",", "")) if capacity_match else None
        wait_count = int(capacity_match.group(2).replace(",", "")) if capacity_match else None
        capacity_total = (
            int(capacity_match.group(3).replace(",", ""))
            if capacity_match
            else (int(simple_capacity.group(1).replace(",", "")) if simple_capacity else None)
        )
        row_number = _integer(cells[0].get_text(" ", strip=True))
        if (
            not identity
            or not raw_url
            or not title
            or not branch
            or not row_number
            or not apply_start
            or not apply_end
            or not start
            or not end
            or not status
            or (reserve_links and (len(reserve_links) != 1 or application_identity != identity))
            or (status == "OPEN" and not application_url)
            or (status != "OPEN" and application_url)
            or capacity_total is None
        ):
            malformed += 1
            continue
        rows.append(
            {
                "provider": _provider(target),
                "provider_course_id": f"{_provider(target)}:lecture:{identity}",
                "title": title,
                "branch": branch,
                "branch_code": _stable_branch_code(branch),
                "category": "",
                "raw_url": raw_url,
                "application_url": application_url or raw_url,
                "application_type": "ONLINE_RESERVATION" if application_url else "INFO_ONLY",
                "reservation_available": bool(application_url),
                "status": status,
                "fee": _clean(cells[4].get_text(" ", strip=True)),
                "period": period,
                "apply_period": apply_period,
                "start_date": start,
                "end_date": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": period,
                "target": "",
                "capacity_current": current_count,
                "capacity_wait": wait_count,
                "capacity_total": capacity_total,
                "instructor": values.get("강 사 명", ""),
                "description": _clean(tr.get_text(" ", strip=True)),
                "municipality_code": NAJU_MUNICIPALITY_CODE,
                "municipality_name": NAJU_MUNICIPALITY_NAME,
                "program_type": "강좌",
                "service_group": "공공강좌",
                "source_subtype": "municipal_lifelong_learning",
                "raw_fields": {
                    "identity": identity,
                    "page": page,
                    "row_number": row_number,
                    "source_status": source_status,
                    "selection_method": _clean(cells[3].find("div").get_text(" ", strip=True))
                    if cells[3].find("div")
                    else "",
                    "active_application_control": bool(application_url),
                    "parser": NAJU_LIFELONG_PARSER,
                },
            }
        )
    return rows, malformed


def _lifelong_detail_status(soup: BeautifulSoup) -> str:
    values: set[str] = set()
    for heading in soup.select("h3"):
        text = _clean(heading.get_text(" ", strip=True))
        match = re.fullmatch(r"수강신청자 목록\s*\(([^()]+)\)", text)
        if match:
            values.add(_clean(match.group(1)))
    return values.pop() if len(values) == 1 else ""


def _validate_lifelong_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    tables = soup.select("table.view_table")
    if not tables:
        return [f"{identity}: detail table is missing"]
    pairs = _detail_pairs(tables[0])
    if any(key not in pairs or not _clean(pairs[key]) for key in _LIFELONG_REQUIRED_DETAIL_KEYS):
        return [f"{identity}: required detail fields are incomplete"]
    errors: list[str] = []
    if _title_signature(pairs.get("강좌명(기수)")) != _title_signature(row.get("title")):
        errors.append(f"{identity}: detail/list title mismatch")
    if _normalized(row.get("branch")) not in _normalized(pairs.get("교육기관")):
        errors.append(f"{identity}: detail/list branch mismatch")
    detail_apply_start, detail_apply_end, detail_apply_period = _datetime_range(pairs.get("신청기간"))
    detail_start, detail_end, detail_period = _date_range(pairs.get("교육기간"))
    if (detail_apply_start, detail_apply_end) != (row.get("apply_start"), row.get("apply_end")):
        errors.append(f"{identity}: detail/list application period mismatch")
    if (detail_start, detail_end) != (row.get("start_date"), row.get("end_date")):
        errors.append(f"{identity}: detail/list education period mismatch")
    capacity_match = _DETAIL_CAPACITY_RE.fullmatch(_clean(pairs.get("모집정원")))
    detail_capacity = int(capacity_match.group(1).replace(",", "")) if capacity_match else None
    if detail_capacity != row.get("capacity_total"):
        errors.append(f"{identity}: detail/list capacity mismatch")
    detail_status = _lifelong_detail_status(soup)
    detail_status_mismatch = bool(
        detail_status and _LIFELONG_STATUS_MAP.get(detail_status) != row.get("status")
    )

    controls: list[str] = []
    for link in soup.select("a[href]"):
        linked_identity, linked_url = _lifelong_link_identity(link.get("href"), "reserve_form")
        if linked_identity:
            controls.append(linked_url)
    controls = sorted(set(controls))
    expected_active = bool(row.get("raw_fields", {}).get("active_application_control"))
    if expected_active:
        expected_url = naju_lifelong_application_url(identity)
        if controls != [expected_url] or row.get("application_url") != expected_url:
            errors.append(f"{identity}: detail/list application control mismatch")
    elif controls:
        errors.append(f"{identity}: unexpected detail application control")
    if errors:
        return errors

    method = _clean(pairs.get("수강신청방법"))
    info_urls: list[str] = []
    key_cell = next(
        (
            cell
            for cell in tables[0].select("th")
            if _clean(cell.get_text(" ", strip=True)) == "교육정보"
        ),
        None,
    )
    value_cell = key_cell.find_next_sibling("td") if key_cell else None
    for link in value_cell.select("a[href]") if value_cell else []:
        parsed = urlparse(urljoin(row["raw_url"], link.get("href")))
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            info_urls.append(parsed.geturl())

    row.update(
        {
            "category": _clean(pairs.get("교육분류")),
            "period": detail_period,
            "apply_period": detail_apply_period,
            "schedule_raw": detail_period,
            "target": _clean(pairs.get("교육대상")),
            "venue_name": _clean(pairs.get("교육장소")),
            "phone": _clean(pairs.get("문의전화")),
            "contact": _clean(pairs.get("문의전화")),
            "fee": _clean(pairs.get("수강료")),
            "instructor": _clean(pairs.get("강사명")),
            "description": _clean(pairs.get("강좌소개 강의계획")),
            "application_method_raw": method,
            "selection_method": _clean(pairs.get("수강신청선정방법")),
            "capacity_wait_total": _integer(pairs.get("모집대기인원")),
        }
    )
    if info_urls:
        row["education_info_url"] = info_urls[0]
    if not expected_active:
        if "온라인" in method:
            row["application_type"] = "ONLINE_RESERVATION"
        elif any(token in method for token in ("서면", "방문", "전화")):
            row["application_type"] = "OFFLINE_APPLY"
        else:
            row["application_type"] = "INFO_ONLY"
        row["reservation_available"] = False
    row["raw_fields"].update(
        {
            "detail_pairs": pairs,
            "detail_status": detail_status,
            "detail_status_mismatch": detail_status_mismatch,
            "detail_application_controls": controls,
            "detail_valid": True,
        }
    )
    return []


def collect_naju_lifelong_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 130,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete current/future Naju lifelong-learning snapshot."""

    if not is_naju_lifelong_target(target):
        return [], NAJU_LIFELONG_PARSER, _failure("target does not match the canonical Naju lifelong route")
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], NAJU_LIFELONG_PARSER, _failure(
                "managed fetcher and session_factory injection are required"
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory
    assert fetcher is not None
    assert session_factory is not None

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    errors: list[str] = []
    detail_errors: list[str] = []
    all_rows: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    source_total = 0
    source_pages = 0
    required_list_requests = 0
    list_requests = 0
    malformed_count = 0
    duplicate_count = 0
    duplicate_url_count = 0
    semantic_duplicate_count = 0
    expired_count = 0
    source_cap_reached = False
    list_complete = False
    detail_attempts = 0
    detail_pages = 0
    current: Any = None
    session_requests = 0
    sessions_created = 0

    try:
        if allowed_pages < 1:
            source_cap_reached = True
            errors.append("max_pages cap cannot inspect the first official page")
        else:
            current = session_factory()
            sessions_created += 1
            first = _fetch(fetcher, current, naju_lifelong_list_url(1), timeout)
            list_requests = 1
            session_requests = 1
            source_total = _lifelong_total(first)
            source_pages = math.ceil(source_total / NAJU_LIFELONG_PAGE_SIZE) if source_total else 0
            required_list_requests = source_pages + 1 if source_pages else 0
            first_rows, malformed = _parse_lifelong_page(target, first, page=1)
            malformed_count += malformed
            page_counts[1] = len(first_rows)
            all_rows.extend(first_rows)
            if not source_total or not source_pages:
                errors.append("first page does not expose one positive official total")
            elif not _lifelong_pagination_contract(
                first, requested_page=1, source_pages=source_pages, sentinel=False
            ):
                errors.append("first-page pagination contract is malformed")
            if malformed or not first_rows:
                errors.append("first page row count or row contract mismatch")
            if required_list_requests > allowed_pages:
                source_cap_reached = True
                errors.append(
                    f"max_pages cap allows {allowed_pages} of "
                    f"{required_list_requests} required list requests"
                )
            elif source_pages:
                for page in range(2, source_pages + 1):
                    if session_requests >= NAJU_LIFELONG_SESSION_LIMIT:
                        _close_quietly(current)
                        current = session_factory()
                        sessions_created += 1
                        session_requests = 0
                    soup = _fetch(fetcher, current, naju_lifelong_list_url(page), timeout)
                    list_requests += 1
                    session_requests += 1
                    page_rows, malformed = _parse_lifelong_page(target, soup, page=page)
                    malformed_count += malformed
                    page_counts[page] = len(page_rows)
                    high = source_total - (page - 1) * NAJU_LIFELONG_PAGE_SIZE
                    low = max(1, source_total - page * NAJU_LIFELONG_PAGE_SIZE + 1)
                    numbers = [int(row["raw_fields"]["row_number"]) for row in page_rows]
                    if (
                        malformed
                        or not page_rows
                        or numbers != sorted(numbers, reverse=True)
                        or any(number < low or number > high for number in numbers)
                    ):
                        errors.append(f"page {page}: row count or row contract mismatch")
                    if not _lifelong_pagination_contract(
                        soup, requested_page=page, source_pages=source_pages, sentinel=False
                    ):
                        errors.append(f"page {page}: pagination contract mismatch")
                    all_rows.extend(page_rows)
                sentinel_page = source_pages + 1
                if session_requests >= NAJU_LIFELONG_SESSION_LIMIT:
                    _close_quietly(current)
                    current = session_factory()
                    sessions_created += 1
                    session_requests = 0
                sentinel = _fetch(fetcher, current, naju_lifelong_list_url(sentinel_page), timeout)
                list_requests += 1
                session_requests += 1
                sentinel_rows, sentinel_malformed = _parse_lifelong_page(
                    target, sentinel, page=sentinel_page
                )
                malformed_count += sentinel_malformed
                page_counts[sentinel_page] = len(sentinel_rows)
                if sentinel_rows or sentinel_malformed:
                    errors.append("post-boundary sentinel page is not empty")
                if not _lifelong_pagination_contract(
                    sentinel,
                    requested_page=sentinel_page,
                    source_pages=source_pages,
                    sentinel=True,
                ):
                    errors.append("post-boundary sentinel pagination contract mismatch")
    except Exception as exc:
        errors.append(f"list fetch {type(exc).__name__}")

    identities = [_clean(row.get("raw_fields", {}).get("identity")) for row in all_rows]
    raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
    duplicate_count = len(identities) - len(set(identities))
    duplicate_url_count = len(raw_urls) - len(set(raw_urls))
    row_numbers = [int(row.get("raw_fields", {}).get("row_number") or 0) for row in all_rows]
    duplicate_row_number_count = len(row_numbers) - len(set(row_numbers))
    hidden_row_count = max(0, source_total - len(set(row_numbers)))
    if duplicate_count:
        errors.append(f"{duplicate_count} duplicate course identities")
    if duplicate_url_count:
        errors.append(f"{duplicate_url_count} duplicate canonical detail URLs")
    if duplicate_row_number_count:
        errors.append(f"{duplicate_row_number_count} duplicate official row numbers")
    if source_total and (
        not row_numbers
        or max(row_numbers) != source_total
        or min(row_numbers) != 1
        or len(set(row_numbers)) + hidden_row_count != source_total
    ):
        errors.append("official total is not reconciled by visible rows and hidden row-number gaps")
    for row in all_rows:
        try:
            end = date.fromisoformat(_clean(row.get("end_date")))
        except ValueError:
            errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
            continue
        if end < cutoff:
            expired_count += 1
        else:
            current_rows.append(row)

    list_complete = (
        not errors
        and source_total > 0
        and list_requests == required_list_requests
        and not duplicate_count
        and not duplicate_url_count
        and not duplicate_row_number_count
    )
    required_details = len(current_rows)
    if allowed_details < required_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} required details"
        )
    elif list_complete:
        for row in current_rows:
            if session_requests >= NAJU_LIFELONG_SESSION_LIMIT:
                _close_quietly(current)
                current = session_factory()
                sessions_created += 1
                session_requests = 0
            identity = _clean(row.get("raw_fields", {}).get("identity"))
            detail_attempts += 1
            try:
                try:
                    detail = _fetch(fetcher, current, _clean(row.get("raw_url")), timeout)
                except Exception:
                    _close_quietly(current)
                    current = session_factory()
                    sessions_created += 1
                    session_requests = 0
                    detail = _fetch(fetcher, current, _clean(row.get("raw_url")), timeout)
                session_requests += 1
                item_errors = _validate_lifelong_detail(row, detail)
                if item_errors:
                    detail_errors.extend(item_errors)
                else:
                    detail_pages += 1
            except Exception as exc:
                detail_errors.append(f"{identity}: detail fetch {type(exc).__name__}")
    _close_quietly(current)
    errors.extend(detail_errors)

    if not detail_errors and detail_pages == required_details:
        semantic_keys = [_semantic_key(row) for row in current_rows]
        semantic_duplicate_count = len(semantic_keys) - len(set(semantic_keys))
        if semantic_duplicate_count:
            errors.append(f"{semantic_duplicate_count} semantic duplicate current courses")
    details_complete = (
        list_complete
        and detail_attempts == required_details
        and detail_pages == required_details
        and not detail_errors
        and not semantic_duplicate_count
    )
    cleaned = [_clean_row(row) for row in current_rows]
    dedupe = dedupe_rows or _dedupe_default
    if details_complete:
        try:
            deduped = list(dedupe(cleaned))
        except Exception as exc:
            errors.append(f"dedupe failed {type(exc).__name__}")
            deduped = []
        if len(deduped) != len(cleaned):
            errors.append(f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}")
        cleaned = deduped
    snapshot_complete = list_complete and details_complete and not errors
    if not snapshot_complete:
        cleaned = []

    status_counts = Counter(_clean(row.get("status")) for row in current_rows)
    branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
    source_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in all_rows
    )
    detail_status_mismatch_count = sum(
        bool(row.get("raw_fields", {}).get("detail_status_mismatch"))
        for row in current_rows
    )
    application_urls = [
        _clean(row.get("application_url"))
        for row in current_rows
        if bool(row.get("reservation_available"))
    ]
    meta: dict[str, Any] = {
        "pages": list_requests,
        "list_requests": list_requests,
        "required_list_requests": required_list_requests,
        "max_pages": allowed_pages,
        "page_unit": NAJU_LIFELONG_PAGE_SIZE,
        "history_start": NAJU_LIFELONG_HISTORY_START,
        "source_total": source_total,
        "source_rows": len(all_rows),
        "hidden_row_count": hidden_row_count,
        "source_pages": source_pages,
        "sentinel_page": source_pages + 1 if source_pages else 0,
        "page_counts": page_counts,
        "sessions_created": sessions_created,
        "discovered_links": len(set(identities)),
        "malformed_count": malformed_count,
        "duplicate_count": duplicate_count,
        "duplicate_url_count": duplicate_url_count,
        "duplicate_row_number_count": duplicate_row_number_count,
        "semantic_duplicate_count": semantic_duplicate_count,
        "expired_count": expired_count,
        "current_count": len(current_rows),
        "returned_count": len(cleaned),
        "required_detail_count": required_details,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "detail_status_mismatch_count": detail_status_mismatch_count,
        "detail_limit": allowed_details,
        "status_counts": dict(sorted(status_counts.items())),
        "source_status_counts": dict(sorted(source_status_counts.items())),
        "branch_counts": dict(sorted(branch_counts.items())),
        "branch_count": len(branch_counts),
        "application_url_count": len(application_urls),
        "unique_application_url_count": len(set(application_urls)),
        "pagination_detected": source_pages > 1,
        "pagination_complete": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "no_current_data": snapshot_complete and not cleaned,
        "no_current_reason": (
            "all complete Naju lifelong-learning courses have ended"
            if snapshot_complete and not cleaned
            else ""
        ),
        "configured_collection_error": "; ".join(errors),
    }
    return cleaned, NAJU_LIFELONG_PARSER, meta


def _response_json(value: Any) -> dict[str, Any]:
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(value, "history", None):
        raise ValueError("HTTP redirects are not accepted")
    json_method = getattr(value, "json", None)
    if callable(json_method):
        payload = json_method()
    elif isinstance(value, Mapping):
        payload = value
    else:
        raise ValueError("response does not expose JSON")
    if not isinstance(payload, dict):
        raise ValueError("JSON response is not an object")
    return dict(payload)


def _gongik_landing_contract(soup: BeautifulSoup) -> bool:
    forms = soup.select(f"form#board_sch1[action='{NAJU_GONGIK_PATH}']")
    tabs = {
        _clean(link.get("data-type"))
        for link in soup.select("a.category_tab_btn[data-type]")
    }
    scripts = {
        urlparse(urljoin(NAJU_GONGIK_URL, _clean(script.get("src")))).path
        for script in soup.select("script[src]")
    }
    return (
        len(forms) == 1
        and tabs == {"ing", "end", "wait", "all"}
        and f"{NAJU_GONGIK_PATH}/ybmodule.pkg/js/list_lecture.js" in scripts
    )


def _post_gongik_page(current: Any, page: int, timeout: int) -> dict[str, Any]:
    response = current.post(
        naju_gongik_page_url(page),
        data={
            "mode": "list",
            "sub_mode": "all",
            "page_scale": str(NAJU_GONGIK_PAGE_SIZE),
            "return": "json",
        },
        timeout=timeout,
        allow_redirects=False,
        headers={
            "Referer": NAJU_GONGIK_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    return _response_json(response)


def _gongik_payload_contract(
    payload: Mapping[str, Any], *, requested_page: int, expected_total: Optional[int] = None
) -> tuple[int, list[dict[str, Any]]]:
    total = _integer(payload.get("total_count"))
    page = _integer(payload.get("page"))
    page_scale = _integer(payload.get("page_scale"))
    block_scale = _integer(payload.get("block_scale"))
    items = payload.get("list")
    if items is None:
        items = []
    navigation = unescape(_clean(payload.get("json_navi_parameter")))
    navigation_query = parse_qs(navigation, keep_blank_values=True)
    expected_navigation = {"sub_mode": ["all"]}
    if requested_page > 1:
        expected_navigation["page"] = [str(requested_page)]
    if (
        total is None
        or page != requested_page
        or page_scale != NAJU_GONGIK_PAGE_SIZE
        or block_scale != 10
        or navigation_query != expected_navigation
        or not isinstance(items, list)
        or (expected_total is not None and total != expected_total)
        or any(not isinstance(item, dict) for item in items)
    ):
        raise ValueError("Gongik JSON envelope contract mismatch")
    return total, [dict(item) for item in items]


def _gongik_status(item: Mapping[str, Any]) -> tuple[str, str]:
    status = item.get("status")
    if not isinstance(status, list) or len(status) < 2:
        return "", ""
    tag = _clean(status[0])
    label = _clean(status[1])
    normalized = _GONGIK_STATUS_MAP.get(tag, "")
    expected_labels = {
        "ing": {"접수중"},
        "wait": {"접수대기"},
        "end": {"접수마감", "모집마감"},
    }
    if label not in expected_labels.get(tag, set()):
        return "", ""
    return normalized, label


def _parse_gongik_items(
    target: Any, items: list[dict[str, Any]], *, page: int
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    for item in items:
        identity = _clean(item.get("idx"))
        title = _clean(item.get("title"))
        branch = _clean(item.get("category_1")) or "나주시 공익활동지원센터"
        apply_start, apply_end, apply_period = _datetime_range(
            f"{_clean(item.get('receipt_start'))} ~ {_clean(item.get('receipt_end'))}"
        )
        education_start_raw = _clean(item.get("lecture_start"))
        education_end_raw = _clean(item.get("lecture_end"))
        start, end, period = _date_range(
            f"{education_start_raw} ~ {education_end_raw}"
        )
        education_period_valid = bool(start and end)
        if not education_period_valid:
            start_tokens = _date_tokens(education_start_raw)
            end_tokens = _date_tokens(education_end_raw)
            start = start_tokens[0].isoformat() if len(start_tokens) == 1 else ""
            end = end_tokens[0].isoformat() if len(end_tokens) == 1 else ""
            period = _clean(f"{education_start_raw} ~ {education_end_raw}")
        status, source_status = _gongik_status(item)
        raw_url = naju_gongik_detail_url(identity)
        capacity_total = _integer(item.get("quota"))
        capacity_wait_total = _integer(item.get("quota_standby"))
        registered = _integer(item.get("student_cnt"))
        wait_current = _integer(item.get("standby_cnt"))
        if (
            not _IDENTITY_RE.fullmatch(identity)
            or not title
            or not branch
            or not apply_start
            or not apply_end
            or not status
            or not raw_url
            or capacity_total is None
        ):
            malformed += 1
            continue
        rows.append(
            {
                "provider": _provider(target),
                "provider_course_id": f"{_provider(target)}:lecture:{identity}",
                "title": title,
                "branch": branch,
                "branch_code": _stable_branch_code(branch),
                "category": _clean(item.get("category_2")),
                "raw_url": raw_url,
                "application_url": raw_url,
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": status,
                "fee": _clean(item.get("cost")),
                "period": period,
                "apply_period": apply_period,
                "start_date": start,
                "end_date": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": _clean(item.get("varchar_1")),
                "target": _clean(item.get("target")),
                "capacity_current": registered,
                "capacity_wait": wait_current,
                "capacity_total": capacity_total,
                "capacity_wait_total": capacity_wait_total,
                "instructor": _clean(item.get("lecturer")),
                "description": _clean(item.get("introduce")),
                "venue_name": _clean(item.get("location")),
                "application_method_raw": _clean(item.get("varchar_2")),
                "contact": _clean(item.get("varchar_4")),
                "municipality_code": NAJU_MUNICIPALITY_CODE,
                "municipality_name": NAJU_MUNICIPALITY_NAME,
                "program_type": "강좌",
                "service_group": "공공강좌",
                "source_subtype": "municipal_public_activity_education",
                "raw_fields": {
                    "identity": identity,
                    "page": page,
                    "list_number": _integer(item.get("list_num")),
                    "source_status": source_status,
                    "status_tag": _clean((item.get("status") or [""])[0]),
                    "board_id": _clean(item.get("board_id")),
                    "education_period_valid": education_period_valid,
                    "parser": NAJU_GONGIK_PARSER,
                },
            }
        )
    return rows, malformed


def _safe_gongik_application_link(value: Any, base_url: str) -> str:
    parsed = urlparse(urljoin(base_url, _clean(value)))
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != NAJU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == NAJU_GONGIK_PATH and _single_query(query, "mode") in {"write", "reserve"}:
        return parsed.geturl()
    if parsed.path in {
        "/www/operation_guide/member_login",
        "/www/support/member_login",
    }:
        return_url = _single_query(query, "return_url")
        if return_url and urlparse(urljoin(NAJU_GONGIK_URL, return_url)).path == NAJU_GONGIK_PATH:
            return parsed.geturl()
    return ""


def _validate_gongik_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    tables = soup.select("table#lecture_view_table.board_t1_view")
    if len(tables) != 1:
        return [f"{identity}: detail table is missing or ambiguous"]
    pairs = _detail_pairs(tables[0])
    if any(key not in pairs or not _clean(pairs[key]) for key in _GONGIK_REQUIRED_DETAIL_KEYS):
        return [f"{identity}: required detail fields are incomplete"]
    errors: list[str] = []
    if _normalized(pairs.get("모집명")) != _normalized(row.get("title")):
        errors.append(f"{identity}: detail/list title mismatch")
    detail_apply_start, detail_apply_end, detail_apply_period = _datetime_range(pairs.get("모집기간"))
    detail_start, detail_end, detail_period = _date_range(pairs.get("교육기간"))
    if (detail_apply_start, detail_apply_end) != (row.get("apply_start"), row.get("apply_end")):
        errors.append(f"{identity}: detail/list application period mismatch")
    if (detail_start, detail_end) != (row.get("start_date"), row.get("end_date")):
        errors.append(f"{identity}: detail/list education period mismatch")
    detail_capacity = _integer(pairs.get("모집정원"))
    if detail_capacity != row.get("capacity_total"):
        errors.append(f"{identity}: detail/list capacity mismatch")
    if _normalized(pairs.get("교육장소")) != _normalized(row.get("venue_name")):
        errors.append(f"{identity}: detail/list venue mismatch")

    controls: list[str] = []
    for link in soup.select("div.lecture_btn_box a[href]"):
        safe = _safe_gongik_application_link(link.get("href"), row["raw_url"])
        if safe:
            controls.append(safe)
    controls = sorted(set(controls))
    if row.get("status") == "OPEN" and len(controls) != 1:
        errors.append(f"{identity}: open course has no unique application control")
    if row.get("status") != "OPEN" and controls:
        errors.append(f"{identity}: inactive course exposes an application control")
    if errors:
        return errors

    method = _clean(pairs.get("접수방법"))
    application_url = controls[0] if controls else row["raw_url"]
    application_type = (
        "ONLINE_RESERVATION"
        if controls or "홈페이지" in method or "온라인" in method
        else ("OFFLINE_APPLY" if any(token in method for token in ("방문", "전화")) else "INFO_ONLY")
    )
    row.update(
        {
            "application_url": application_url,
            "application_type": application_type,
            "reservation_available": bool(controls),
            "apply_period": detail_apply_period,
            "period": detail_period,
            "target": _clean(pairs.get("모집대상")),
            "capacity_total": detail_capacity,
            "schedule_raw": _clean(pairs.get("요일/시간")),
            "venue_name": _clean(pairs.get("교육장소")),
            "description": _clean(pairs.get("교육소개")),
            "phone": _clean(pairs.get("문의전화")),
            "contact": _clean(pairs.get("문의전화")),
            "application_method_raw": method,
            "selection_method": _clean(pairs.get("선정방법")),
        }
    )
    row["raw_fields"].update(
        {
            "detail_pairs": pairs,
            "detail_application_controls": controls,
            "detail_valid": True,
        }
    )
    return []


def collect_naju_gongik_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 10,
    detail_limit: int = 50,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete current/future public-activities education snapshot."""

    if not is_naju_gongik_target(target):
        return [], NAJU_GONGIK_PARSER, _failure("target does not match the canonical Naju Gongik route")
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], NAJU_GONGIK_PARSER, _failure(
                "managed fetcher and session_factory injection are required"
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory
    assert fetcher is not None
    assert session_factory is not None

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    errors: list[str] = []
    detail_errors: list[str] = []
    all_rows: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    source_total = 0
    source_pages = 0
    required_list_requests = 0
    list_requests = 0
    malformed_count = 0
    duplicate_count = 0
    duplicate_url_count = 0
    semantic_duplicate_count = 0
    expired_count = 0
    historical_missing_education_end_count = 0
    source_cap_reached = False
    list_complete = False
    detail_attempts = 0
    detail_pages = 0
    current: Any = None

    try:
        if allowed_pages < 2:
            source_cap_reached = True
            errors.append("max_pages cap cannot inspect landing and first JSON page")
        else:
            current = session_factory()
            landing = _fetch(fetcher, current, NAJU_GONGIK_URL, timeout)
            list_requests = 1
            if not _gongik_landing_contract(landing):
                errors.append("landing-page AJAX contract is malformed")
            first_payload = _post_gongik_page(current, 1, timeout)
            list_requests += 1
            source_total, first_items = _gongik_payload_contract(first_payload, requested_page=1)
            source_pages = math.ceil(source_total / NAJU_GONGIK_PAGE_SIZE) if source_total else 0
            required_list_requests = 1 + source_pages + 1
            first_rows, malformed = _parse_gongik_items(target, first_items, page=1)
            malformed_count += malformed
            page_counts[1] = len(first_rows)
            all_rows.extend(first_rows)
            expected_first = min(NAJU_GONGIK_PAGE_SIZE, source_total)
            if malformed or len(first_rows) != expected_first:
                errors.append("first JSON page row count or row contract mismatch")
            if required_list_requests > allowed_pages:
                source_cap_reached = True
                errors.append(
                    f"max_pages cap allows {allowed_pages} of "
                    f"{required_list_requests} required list requests"
                )
            else:
                for page in range(2, source_pages + 1):
                    payload = _post_gongik_page(current, page, timeout)
                    list_requests += 1
                    _total, items = _gongik_payload_contract(
                        payload, requested_page=page, expected_total=source_total
                    )
                    page_rows, malformed = _parse_gongik_items(target, items, page=page)
                    malformed_count += malformed
                    page_counts[page] = len(page_rows)
                    expected = min(
                        NAJU_GONGIK_PAGE_SIZE,
                        source_total - (page - 1) * NAJU_GONGIK_PAGE_SIZE,
                    )
                    if malformed or len(page_rows) != expected:
                        errors.append(f"JSON page {page}: row count or row contract mismatch")
                    all_rows.extend(page_rows)
                sentinel_page = source_pages + 1
                sentinel_payload = _post_gongik_page(current, sentinel_page, timeout)
                list_requests += 1
                _total, sentinel_items = _gongik_payload_contract(
                    sentinel_payload,
                    requested_page=sentinel_page,
                    expected_total=source_total,
                )
                page_counts[sentinel_page] = len(sentinel_items)
                if sentinel_items:
                    errors.append("post-boundary JSON sentinel page is not empty")
    except Exception as exc:
        errors.append(f"list fetch {type(exc).__name__}")

    identities = [_clean(row.get("raw_fields", {}).get("identity")) for row in all_rows]
    raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
    duplicate_count = len(identities) - len(set(identities))
    duplicate_url_count = len(raw_urls) - len(set(raw_urls))
    if duplicate_count:
        errors.append(f"{duplicate_count} duplicate course identities")
    if duplicate_url_count:
        errors.append(f"{duplicate_url_count} duplicate canonical detail URLs")
    if len(all_rows) != source_total:
        errors.append(f"source declared {source_total}, parsed {len(all_rows)}")
    for row in all_rows:
        end_text = _clean(row.get("end_date"))
        if end_text:
            try:
                end_value = date.fromisoformat(end_text)
            except ValueError:
                errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
                continue
        else:
            apply_end_text = _clean(row.get("apply_end")).split(" ", 1)[0]
            try:
                apply_end_value = date.fromisoformat(apply_end_text)
            except ValueError:
                apply_end_value = cutoff
            if row.get("status") == "CLOSED" and apply_end_value < cutoff:
                historical_missing_education_end_count += 1
                expired_count += 1
                continue
            errors.append(f"{_clean(row.get('provider_course_id'))}: missing current education end date")
            continue
        if end_value < cutoff:
            expired_count += 1
        else:
            current_rows.append(row)

    list_complete = (
        not errors
        and list_requests == required_list_requests
        and len(all_rows) == source_total
        and not duplicate_count
        and not duplicate_url_count
    )
    required_details = len(current_rows)
    if allowed_details < required_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} required details"
        )
    elif list_complete:
        for row in current_rows:
            identity = _clean(row.get("raw_fields", {}).get("identity"))
            detail_attempts += 1
            try:
                detail = _fetch(fetcher, current, _clean(row.get("raw_url")), timeout)
                item_errors = _validate_gongik_detail(row, detail)
                if item_errors:
                    detail_errors.extend(item_errors)
                else:
                    detail_pages += 1
            except Exception as exc:
                detail_errors.append(f"{identity}: detail fetch {type(exc).__name__}")
    _close_quietly(current)
    errors.extend(detail_errors)

    if not detail_errors and detail_pages == required_details:
        semantic_keys = [_semantic_key(row) for row in current_rows]
        semantic_duplicate_count = len(semantic_keys) - len(set(semantic_keys))
        if semantic_duplicate_count:
            errors.append(f"{semantic_duplicate_count} semantic duplicate current courses")
    details_complete = (
        list_complete
        and detail_attempts == required_details
        and detail_pages == required_details
        and not detail_errors
        and not semantic_duplicate_count
    )
    cleaned = [_clean_row(row) for row in current_rows]
    dedupe = dedupe_rows or _dedupe_default
    if details_complete:
        try:
            deduped = list(dedupe(cleaned))
        except Exception as exc:
            errors.append(f"dedupe failed {type(exc).__name__}")
            deduped = []
        if len(deduped) != len(cleaned):
            errors.append(f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}")
        cleaned = deduped
    snapshot_complete = list_complete and details_complete and not errors
    if not snapshot_complete:
        cleaned = []

    status_counts = Counter(_clean(row.get("status")) for row in current_rows)
    branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
    source_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in all_rows
    )
    meta: dict[str, Any] = {
        "pages": list_requests,
        "list_requests": list_requests,
        "required_list_requests": required_list_requests,
        "max_pages": allowed_pages,
        "page_unit": NAJU_GONGIK_PAGE_SIZE,
        "source_total": source_total,
        "source_rows": len(all_rows),
        "source_pages": source_pages,
        "sentinel_page": source_pages + 1,
        "page_counts": page_counts,
        "discovered_links": len(set(identities)),
        "malformed_count": malformed_count,
        "duplicate_count": duplicate_count,
        "duplicate_url_count": duplicate_url_count,
        "semantic_duplicate_count": semantic_duplicate_count,
        "expired_count": expired_count,
        "historical_missing_education_end_count": historical_missing_education_end_count,
        "current_count": len(current_rows),
        "returned_count": len(cleaned),
        "required_detail_count": required_details,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "detail_limit": allowed_details,
        "status_counts": dict(sorted(status_counts.items())),
        "source_status_counts": dict(sorted(source_status_counts.items())),
        "branch_counts": dict(sorted(branch_counts.items())),
        "branch_count": len(branch_counts),
        "pagination_detected": source_pages > 1,
        "pagination_complete": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "no_current_data": snapshot_complete and not cleaned,
        "no_current_reason": (
            "all complete Naju public-activities education courses have ended"
            if snapshot_complete and not cleaned
            else ""
        ),
        "configured_collection_error": "; ".join(errors),
    }
    return cleaned, NAJU_GONGIK_PARSER, meta


def collect_naju_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 130,
    detail_limit: int = 100,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if is_naju_lifelong_target(target):
        return collect_naju_lifelong_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    if is_naju_gongik_target(target):
        return collect_naju_gongik_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    return [], NAJU_LIFELONG_PARSER, _failure(
        "target does not match a canonical Naju education provider route"
    )


is_target = is_naju_education_target
collect = collect_naju_education_courses


__all__ = [
    "NAJU_GONGIK_PAGE_SIZE",
    "NAJU_GONGIK_PARSER",
    "NAJU_GONGIK_PATH",
    "NAJU_GONGIK_PROVIDER",
    "NAJU_GONGIK_URL",
    "NAJU_LIFELONG_DUPLICATE_PROVIDER",
    "NAJU_LIFELONG_DUPLICATE_URLS",
    "NAJU_LIFELONG_HISTORY_START",
    "NAJU_LIFELONG_PAGE_SIZE",
    "NAJU_LIFELONG_PARSER",
    "NAJU_LIFELONG_PATH",
    "NAJU_LIFELONG_PROVIDER",
    "NAJU_LIFELONG_URL",
    "NAJU_MUNICIPALITY_CODE",
    "NAJU_MUNICIPALITY_NAME",
    "collect",
    "collect_naju_education_courses",
    "collect_naju_gongik_courses",
    "collect_naju_lifelong_courses",
    "is_naju_education_target",
    "is_naju_gongik_target",
    "is_naju_lifelong_target",
    "is_target",
    "naju_gongik_detail_url",
    "naju_gongik_page_url",
    "naju_lifelong_application_url",
    "naju_lifelong_detail_url",
    "naju_lifelong_list_url",
]
