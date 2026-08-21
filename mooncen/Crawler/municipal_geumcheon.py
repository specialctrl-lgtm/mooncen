"""Fail-closed collector for Geumcheon-gu's official education reservation list.

The module is intentionally independent from ``Crawler_MunicipalYaml``.  A
parent crawler should inject its managed HTTP session/fetch helper and dedupe
function.  Direct ``requests`` access is disabled unless a caller explicitly
opts in with ``allow_raw_requests_for_tests=True``.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GEUMCHEON_PROVIDER = "MUNI_WWW_GEUMCHEON_GO_KR_237EA1EA"
GEUMCHEON_URL = (
    "https://www.geumcheon.go.kr/reserve/webEdcLctreList.do?key=112&rep=1"
)
GEUMCHEON_HOST = "www.geumcheon.go.kr"
GEUMCHEON_LIST_PATH = "/reserve/webEdcLctreList.do"
GEUMCHEON_DETAIL_PATH = "/reserve/edcLctreView.do"
GEUMCHEON_AGREE_PATH = "/reserve/webEdcLctreAgree.do"
GEUMCHEON_KEY = "112"
GEUMCHEON_PAGE_UNIT = 1000
GEUMCHEON_PARSER = "geumcheon_current_future_complete_groups+detail"
GEUMCHEON_MUNICIPALITY_CODE = "1154500000"
GEUMCHEON_MUNICIPALITY_NAME = "서울특별시 금천구"
GEUMCHEON_MAX_WORKERS = 8

# These are the official labels and values exposed by the source page.  The
# all-source route (group 0) is canonical; these routes are partitions used to
# preserve the owning branch, not separate providers.
GEUMCHEON_GROUPS: tuple[tuple[str, str], ...] = (
    ("1", "평생학습관"),
    ("120193", "글로벌인재학당"),
    ("120194", "글로벌인재학당 온라인"),
    ("29", "정보화교육"),
    ("38", "금천사이언스큐브"),
    ("42", "금천구보건소"),
    ("120164", "동네배움터"),
    ("120214", "금천시민대학"),
    ("120165", "느린학습자지원센터"),
    ("120220", "시흥행궁전시관"),
    ("5", "가산동자치회관"),
    ("19", "독산1동자치회관"),
    ("20", "독산2동자치회관"),
    ("21", "독산3동자치회관"),
    ("22", "독산4동자치회관"),
    ("23", "시흥1동자치회관"),
    ("24", "시흥2동자치회관"),
    ("25", "시흥3동자치회관"),
    ("26", "시흥4동자치회관"),
    ("27", "시흥5동자치회관"),
    ("34", "기타 프로그램"),
)
GEUMCHEON_REQUIRED_LIST_REQUESTS = 1 + len(GEUMCHEON_GROUPS)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DECLARATION_RE = re.compile(
    r"총\s*([\d,]+)\s*건\s*\[\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\]"
)
_EDUCATION_PERIOD_RE = re.compile(
    r"교육\s*:\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
    r"\s*~\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
)
_APPLICATION_PERIOD_RE = re.compile(
    r"신청\s*:\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
    r"\s*~\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
)
_DATE_TOKEN_RE = re.compile(
    r"(?<!\d)(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)"
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def is_geumcheon_target(target: Any) -> bool:
    """Match only the provider-owned canonical source, byte for byte."""

    return _provider(target) == GEUMCHEON_PROVIDER and _target_url(target) == GEUMCHEON_URL


is_target = is_geumcheon_target


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        raise ValueError("empty HTTP response")
    return BeautifulSoup(response.content, "lxml")


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher did not return HTML or BeautifulSoup")
    return BeautifulSoup(content, "lxml")


def _fetch(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _list_url(group_code: Optional[str] = None, page_index: int = 1) -> str:
    page = max(1, int(page_index))
    query: list[tuple[str, str]] = [
        ("key", GEUMCHEON_KEY),
        ("rep", "1"),
        ("pageUnit", str(GEUMCHEON_PAGE_UNIT)),
        ("pageIndex", str(page)),
    ]
    if group_code is not None:
        query.append(("searchLctreGroup", _clean(group_code)))
    return f"https://{GEUMCHEON_HOST}{GEUMCHEON_LIST_PATH}?{urlencode(query)}"


def geumcheon_detail_url(lecture_key: str) -> str:
    identity = _clean(lecture_key)
    if not identity.isdigit():
        return ""
    return (
        f"https://{GEUMCHEON_HOST}{GEUMCHEON_DETAIL_PATH}?"
        f"{urlencode({'key': GEUMCHEON_KEY, 'searchLctreKey': identity})}"
    )


def geumcheon_agree_url(lecture_key: str) -> str:
    identity = _clean(lecture_key)
    if not identity.isdigit():
        return ""
    return (
        f"https://{GEUMCHEON_HOST}{GEUMCHEON_AGREE_PATH}?"
        f"{urlencode({'key': GEUMCHEON_KEY, 'lctreKey': identity})}"
    )


def _page_declaration(soup: BeautifulSoup) -> Optional[tuple[int, int, int]]:
    match = _DECLARATION_RE.search(_clean(soup.get_text(" ", strip=True)))
    if not match:
        return None
    return (
        int(match.group(1).replace(",", "")),
        int(match.group(2)),
        int(match.group(3)),
    )


def _range(match: Optional[re.Match[str]]) -> tuple[str, str, str]:
    if match is None:
        return "", "", ""
    try:
        start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
    except ValueError:
        return "", "", ""
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _date_range_text(value: Any) -> tuple[str, str, str]:
    tokens = _DATE_TOKEN_RE.findall(_clean(value))
    if len(tokens) < 2:
        return "", "", ""
    try:
        start = date(*(int(part) for part in tokens[0]))
        end = date(*(int(part) for part in tokens[1]))
    except ValueError:
        return "", "", ""
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _capacity_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    numbers = [int(token.replace(",", "")) for token in re.findall(r"\d[\d,]*", _clean(value))]
    if len(numbers) < 2:
        return None, None
    return numbers[0], numbers[1]


def _stable_branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"GEUMCHEON_BRANCH_{digest}"


def _status(value: Any) -> str:
    compact = _clean(value).replace(" ", "")
    if "우선접수" in compact:
        return "우선접수"
    if "모집중" in compact:
        return "접수중"
    if "모집대기" in compact:
        return "접수예정"
    if "모집마감" in compact:
        return "접수마감"
    return ""


def _is_applicable(value: Any) -> bool:
    compact = _clean(value).replace(" ", "")
    return "모집중" in compact or "우선접수" in compact


def _apply_official_category(row: dict[str, Any], value: Any) -> None:
    """Route the official lecture area into the Ops Console big category.

    Geumcheon's canonical education catalogue also contains records whose
    official ``강좌영역`` is ``체험/견학``.  The catalogue URL alone therefore
    cannot be used as the course-level category.
    """

    category = _clean(value)
    compact = category.replace(" ", "")
    row["category"] = category
    if "체험" in compact or "견학" in compact:
        row.update(
            {
                "program_type": "체험",
                "domain_category": "체험·견학",
                "service_group": "체험",
            }
        )
        return
    row.update(
        {
            "program_type": "강좌",
            "domain_category": "교육·강좌",
            "service_group": "공공강좌",
        }
    )


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _base_row(target: Any, lecture_key: str, title: str, raw_url: str) -> dict[str, Any]:
    provider = _provider(target)
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:edc:{lecture_key}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "program_type": "강좌",
        "category": "교육·강좌",
        "raw_url": raw_url,
        "reservation_available": False,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "municipality_code": GEUMCHEON_MUNICIPALITY_CODE,
        "municipality_full_name": GEUMCHEON_MUNICIPALITY_NAME,
        "collection_type": "complete_single_page_groups+detail_html",
        "raw_fields": {"parser": GEUMCHEON_PARSER, "lecture_key": lecture_key},
    }


def _list_rows(
    target: Any,
    soup: BeautifulSoup,
    *,
    build_rows: bool,
) -> tuple[list[dict[str, Any]], list[str], int, int]:
    rows: list[dict[str, Any]] = []
    identities: list[str] = []
    invalid = 0
    exposed = 0
    for tr in soup.select("table tbody tr"):
        link = tr.select_one(".p-subject a[href], a[href*='edcLctreView.do']")
        if link is None:
            continue
        exposed += 1
        raw_href = _clean(link.get("href"))
        parsed_link = urlparse(urljoin(GEUMCHEON_URL, raw_href))
        query = parse_qs(parsed_link.query, keep_blank_values=True)
        lecture_key = _clean((query.get("searchLctreKey") or [""])[0])
        title = _clean(link.get_text(" ", strip=True))
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.find_all("td")]
        raw_url = geumcheon_detail_url(lecture_key)
        valid_identity = (
            parsed_link.hostname and parsed_link.hostname.lower() == GEUMCHEON_HOST
            and parsed_link.path == GEUMCHEON_DETAIL_PATH
            and lecture_key.isdigit()
            and _clean((query.get("key") or [GEUMCHEON_KEY])[0]) == GEUMCHEON_KEY
        )
        if not valid_identity or not title or len(cells) < 7 or not raw_url:
            invalid += 1
            continue
        identities.append(lecture_key)
        if not build_rows:
            continue
        status_raw = cells[0]
        normalized_status = _status(status_raw)
        start_date, end_date, period = _range(_EDUCATION_PERIOD_RE.search(cells[3]))
        apply_start, apply_end, apply_period = _range(
            _APPLICATION_PERIOD_RE.search(cells[3])
        )
        if not normalized_status or not start_date or not end_date or not apply_period:
            invalid += 1
            identities.pop()
            continue
        subject_cell = link.find_parent("td")
        venue_node = subject_cell.select_one("p") if subject_cell else None
        venue = _clean(venue_node.get_text(" ", strip=True) if venue_node else "")
        capacity_current, capacity_total = _capacity_pair(cells[4])
        row = _base_row(target, lecture_key, title, raw_url)
        row.update(
            {
                "status": normalized_status,
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
                "apply_period": apply_period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "target": cells[2],
                "capacity": cells[4],
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "fee": cells[5],
                "application_method_raw": cells[6],
                "room": venue,
                "venue_name": venue,
                "description": _clean(" ".join(cells)),
            }
        )
        row["raw_fields"].update(
            {
                "source_status": status_raw,
                "list_cells": cells,
                "list_period_raw": cells[3],
            }
        )
        rows.append(row)
    return rows, identities, invalid, exposed


def _pairs_from_tables(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table tr"):
        key = ""
        for cell in tr.find_all(["th", "td"], recursive=False):
            if cell.name == "th":
                key = _clean(cell.get_text(" ", strip=True))
            elif key:
                value = _clean(cell.get_text(" ", strip=True))
                if key not in pairs or not pairs[key]:
                    pairs[key] = value
                key = ""
    return pairs


def _first(pairs: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = _clean(pairs.get(name))
        if value:
            return value
    return ""


def _agree_link(soup: BeautifulSoup, lecture_key: str, base_url: str) -> str:
    for link in soup.select("a[href*='webEdcLctreAgree.do']"):
        candidate = urljoin(base_url, _clean(link.get("href")))
        parsed = urlparse(candidate)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme.lower() == "https"
            and (parsed.hostname or "").lower() == GEUMCHEON_HOST
            and parsed.path == GEUMCHEON_AGREE_PATH
            and query == {"key": [GEUMCHEON_KEY], "lctreKey": [lecture_key]}
        ):
            return geumcheon_agree_url(lecture_key)
    return ""


def _detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    identity = _clean(row.get("raw_fields", {}).get("lecture_key"))
    pairs = _pairs_from_tables(soup)
    detail_period_raw = _first(pairs, "교육기간", "강의기간")
    detail_apply_raw = _first(pairs, "신청기간", "접수기간")
    official_category = _first(pairs, "강좌영역", "강좌구분", "분류")
    detail_start, detail_end, detail_period = _date_range_text(detail_period_raw)
    apply_start, apply_end, apply_period = _date_range_text(detail_apply_raw)
    required = {
        "official_category": official_category,
        "education_period": detail_period,
        "application_period": apply_period,
        "application_method": _first(pairs, "수강신청방법", "신청방법", "접수방법"),
        "room": _first(pairs, "강의장소", "교육장소", "장소"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        errors.append(f"lecture {identity}: missing detail fields {','.join(missing)}")
    if detail_period and detail_period != _clean(row.get("period")):
        errors.append(f"lecture {identity}: detail/list education period mismatch")
    if apply_period and apply_period != _clean(row.get("apply_period")):
        errors.append(f"lecture {identity}: detail/list application period mismatch")

    agree_url = _agree_link(soup, identity, _clean(row.get("raw_url")))
    applicable = _is_applicable(row.get("raw_fields", {}).get("source_status"))
    if applicable and not agree_url:
        errors.append(f"lecture {identity}: applicable course has no canonical agree link")

    room = required["room"]
    address_match = re.search(r"((?:\d{5}\s+)?서울특별시\s+금천구\s+.+)$", room)
    venue_address = _clean(address_match.group(1)) if address_match else ""
    venue_name = _clean(room[: address_match.start()]) if address_match else room
    capacity = _first(pairs, "정원", "모집정원", "신청인원")
    # Detail pages normally expose ``8명 + 대기자 5명`` (capacity, not
    # applicants/capacity).  Keep the accurate list-side applicant count and
    # take only the first detail number as the authoritative capacity.
    capacity_numbers = [
        int(token.replace(",", ""))
        for token in re.findall(r"\d[\d,]*", capacity)
    ]
    capacity_total = capacity_numbers[0] if capacity_numbers else None
    waitlist_total = None
    waitlist_match = re.search(r"대기(?:자)?\s*(\d[\d,]*)", capacity)
    if waitlist_match:
        waitlist_total = int(waitlist_match.group(1).replace(",", ""))
    row.update(
        {
            "period": detail_period or row.get("period"),
            "start_date": detail_start or row.get("start_date"),
            "end_date": detail_end or row.get("end_date"),
            "apply_period": apply_period or row.get("apply_period"),
            "apply_start": apply_start or row.get("apply_start"),
            "apply_end": apply_end or row.get("apply_end"),
            "category": official_category or row.get("category"),
            "schedule_raw": _first(pairs, "강의시간", "교육시간", "요일/시간"),
            "application_method_raw": required["application_method"] or row.get("application_method_raw"),
            "selection_method": _first(pairs, "선별방법"),
            "fee": _first(pairs, "수강료", "교육비", "이용요금") or row.get("fee"),
            "room": venue_name,
            "venue_name": venue_name,
            "venue_address": venue_address,
            "target": _first(pairs, "수강대상", "교육대상", "이용대상") or row.get("target"),
            "capacity": capacity or row.get("capacity"),
            "capacity_current": row.get("capacity_current"),
            "capacity_total": capacity_total if capacity_total is not None else row.get("capacity_total"),
            "waitlist_total": waitlist_total,
            "provider_organizer": _first(pairs, "주최", "운영기관", "기관"),
            "phone": _first(pairs, "문의", "문의전화", "전화"),
            "reservation_available": bool(applicable and agree_url),
        }
    )
    if official_category:
        _apply_official_category(row, official_category)
    if applicable and agree_url:
        row["application_url"] = agree_url
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        row["raw_fields"]["clear_application_url"] = True
    row["raw_fields"].update(
        {
            "detail_pairs": pairs,
            "canonical_agree_link": bool(agree_url),
            "official_course_area": official_category,
        }
    )
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _failure_meta(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "detail_attempts": 0,
        "detail_required_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "current_count": 0,
        "returned_count": 0,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_geumcheon_current(
    target: Any,
    timeout: int = 20,
    max_pages: int = 30,
    detail_limit: int = 2000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 6,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future snapshot from the canonical source.

    A malformed declaration, missing/duplicate group identity, truncated detail
    set, or detail parse error returns no rows and ``snapshot_complete=False``.
    """

    if not is_geumcheon_target(target):
        return [], GEUMCHEON_PARSER, _failure_meta(
            "target does not match the canonical Geumcheon provider route"
        )
    allowed_list_requests = max(0, int(max_pages))
    if allowed_list_requests < GEUMCHEON_REQUIRED_LIST_REQUESTS:
        meta = _failure_meta(
            f"max_pages cap allows {allowed_list_requests} of "
            f"{GEUMCHEON_REQUIRED_LIST_REQUESTS} required list requests"
        )
        meta.update(
            {
                "max_pages": allowed_list_requests,
                "required_list_requests": GEUMCHEON_REQUIRED_LIST_REQUESTS,
                "source_cap_reached": True,
            }
        )
        return [], GEUMCHEON_PARSER, meta
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], GEUMCHEON_PARSER, _failure_meta(
                "managed fetcher and session_factory injection are required"
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory

    assert fetcher is not None
    assert session_factory is not None
    cutoff = _today(today)
    errors: list[str] = []
    source_cap_reached = False
    all_rows: list[dict[str, Any]] = []
    all_ids: list[str] = []
    declared_total = 0
    declared_pages = 0
    list_requests = 0
    required_list_requests = GEUMCHEON_REQUIRED_LIST_REQUESTS
    pagination_detected = False
    invalid_count = 0
    duplicate_count = 0
    all_exposed = 0
    primary_session: Any = None

    try:
        primary_session = session_factory()
        try:
            list_requests += 1
            all_soup = _fetch(fetcher, primary_session, _list_url(), timeout)
        except Exception as exc:
            errors.append(f"all-list fetch {type(exc).__name__}")
            all_soup = None
        if all_soup is not None:
            declaration = _page_declaration(all_soup)
            if declaration is None:
                errors.append("all-list declaration is missing or malformed")
            else:
                declared_total, current_page, declared_pages = declaration
                expected_pages = max(
                    1, (declared_total + GEUMCHEON_PAGE_UNIT - 1) // GEUMCHEON_PAGE_UNIT
                )
                if current_page != 1 or declared_pages != expected_pages:
                    errors.append(
                        f"all-list declaration {current_page}/{declared_pages} does not match "
                        f"pageUnit={GEUMCHEON_PAGE_UNIT} and total {declared_total}"
                    )
                pagination_detected = declared_pages > 1
                required_list_requests = declared_pages + len(GEUMCHEON_GROUPS)
                if required_list_requests > allowed_list_requests:
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap allows {allowed_list_requests} of "
                        f"{required_list_requests} required list requests"
                    )
            page_rows, page_ids, page_invalid, page_exposed = _list_rows(
                target, all_soup, build_rows=True
            )
            all_rows.extend(page_rows)
            all_ids.extend(page_ids)
            invalid_count += page_invalid
            all_exposed += page_exposed

            if not errors:
                for page_index in range(2, declared_pages + 1):
                    try:
                        list_requests += 1
                        page_soup = _fetch(
                            fetcher,
                            primary_session,
                            _list_url(page_index=page_index),
                            timeout,
                        )
                    except Exception as exc:
                        errors.append(
                            f"all-list page {page_index} fetch {type(exc).__name__}"
                        )
                        break
                    page_declaration = _page_declaration(page_soup)
                    if page_declaration != (declared_total, page_index, declared_pages):
                        errors.append(
                            f"all-list page {page_index} declaration changed or is malformed"
                        )
                        break
                    page_rows, page_ids, page_invalid, page_exposed = _list_rows(
                        target, page_soup, build_rows=True
                    )
                    all_rows.extend(page_rows)
                    all_ids.extend(page_ids)
                    invalid_count += page_invalid
                    all_exposed += page_exposed

            duplicate_count = len(all_ids) - len(set(all_ids))
            if not source_cap_reached:
                if invalid_count:
                    errors.append(f"{invalid_count} all-list rows were malformed")
                if duplicate_count:
                    errors.append(f"{duplicate_count} duplicate lecture keys in all-list")
                if all_exposed != declared_total or len(set(all_ids)) != declared_total:
                    errors.append(
                        f"all-list declared {declared_total}, exposed {all_exposed}, "
                        f"unique {len(set(all_ids))}"
                    )
    finally:
        _close_quietly(primary_session)

    group_totals: dict[str, int] = {}
    group_ids: dict[str, set[str]] = {}
    group_failures: list[str] = []

    def fetch_group_first(item: tuple[str, str]) -> dict[str, Any]:
        code, label = item
        local_errors: list[str] = []
        current_session: Any = None
        try:
            current_session = session_factory()
            soup = _fetch(fetcher, current_session, _list_url(code), timeout)
            declaration = _page_declaration(soup)
            if declaration is None:
                return {
                    "code": code,
                    "label": label,
                    "total": 0,
                    "pages": 1,
                    "identities": [],
                    "invalid": 0,
                    "exposed": 0,
                    "errors": [f"group {code}: declaration missing"],
                    "requests": 1,
                }
            total, current_page, pages = declaration
            expected_pages = max(
                1, (total + GEUMCHEON_PAGE_UNIT - 1) // GEUMCHEON_PAGE_UNIT
            )
            _rows, identities, invalid, exposed = _list_rows(
                target, soup, build_rows=False
            )
            if current_page != 1 or pages != expected_pages:
                local_errors.append(
                    f"group {code}: declaration {current_page}/{pages} does not match "
                    f"pageUnit={GEUMCHEON_PAGE_UNIT} and total {total}"
                )
            if invalid:
                local_errors.append(f"group {code}: {invalid} malformed rows")
            return {
                "code": code,
                "label": label,
                "total": total,
                "pages": pages,
                "identities": identities,
                "invalid": invalid,
                "exposed": exposed,
                "errors": local_errors,
                "requests": 1,
            }
        except Exception as exc:
            return {
                "code": code,
                "label": label,
                "total": 0,
                "pages": 1,
                "identities": [],
                "invalid": 0,
                "exposed": 0,
                "errors": [f"group {code}: fetch {type(exc).__name__}"],
                "requests": 1,
            }
        finally:
            _close_quietly(current_session)

    def fetch_group_remaining(first: dict[str, Any]) -> dict[str, Any]:
        code = str(first["code"])
        total = int(first["total"])
        pages = int(first["pages"])
        identities: list[str] = []
        invalid = 0
        exposed = 0
        local_errors: list[str] = []
        requests_made = 0
        current_session: Any = None
        try:
            current_session = session_factory()
            for page_index in range(2, pages + 1):
                try:
                    requests_made += 1
                    soup = _fetch(
                        fetcher,
                        current_session,
                        _list_url(code, page_index),
                        timeout,
                    )
                except Exception as exc:
                    local_errors.append(
                        f"group {code} page {page_index}: fetch {type(exc).__name__}"
                    )
                    break
                if _page_declaration(soup) != (total, page_index, pages):
                    local_errors.append(
                        f"group {code} page {page_index}: declaration changed or is malformed"
                    )
                    break
                _rows, page_ids, page_invalid, page_exposed = _list_rows(
                    target, soup, build_rows=False
                )
                identities.extend(page_ids)
                invalid += page_invalid
                exposed += page_exposed
        finally:
            _close_quietly(current_session)
        return {
            "code": code,
            "identities": identities,
            "invalid": invalid,
            "exposed": exposed,
            "errors": local_errors,
            "requests": requests_made,
        }

    if not errors:
        workers = min(GEUMCHEON_MAX_WORKERS, max(1, int(max_workers)), len(GEUMCHEON_GROUPS))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="geumcheon-group") as pool:
            group_results = list(pool.map(fetch_group_first, GEUMCHEON_GROUPS))
        list_requests += sum(int(result["requests"]) for result in group_results)
        required_list_requests = declared_pages + sum(
            max(1, int(result["pages"])) for result in group_results
        )
        pagination_detected = pagination_detected or any(
            int(result["pages"]) > 1 for result in group_results
        )
        group_first_errors = [
            error
            for result in group_results
            for error in result["errors"]
        ]
        if required_list_requests > allowed_list_requests:
            source_cap_reached = True
            group_failures.append(
                f"max_pages cap allows {allowed_list_requests} of "
                f"{required_list_requests} required list requests"
            )

        remaining_by_code: dict[str, dict[str, Any]] = {}
        if not group_first_errors and not group_failures:
            multi_page_groups = [
                result for result in group_results if int(result["pages"]) > 1
            ]
            if multi_page_groups:
                with ThreadPoolExecutor(
                    max_workers=min(workers, len(multi_page_groups)),
                    thread_name_prefix="geumcheon-group-page",
                ) as pool:
                    remaining_results = list(
                        pool.map(fetch_group_remaining, multi_page_groups)
                    )
                list_requests += sum(
                    int(result["requests"]) for result in remaining_results
                )
                remaining_by_code = {
                    str(result["code"]): result for result in remaining_results
                }

        membership: Counter[str] = Counter()
        branch_by_id: dict[str, tuple[str, str]] = {}
        for result in group_results:
            code = str(result["code"])
            label = str(result["label"])
            total = int(result["total"])
            identities = list(result["identities"])
            invalid = int(result["invalid"])
            exposed = int(result["exposed"])
            local_errors = list(result["errors"])
            remaining = remaining_by_code.get(code)
            if remaining is not None:
                identities.extend(remaining["identities"])
                invalid += int(remaining["invalid"])
                exposed += int(remaining["exposed"])
                local_errors.extend(remaining["errors"])
            unique = set(identities)
            invalid_error = f"group {code}: {invalid} malformed rows"
            if invalid and invalid_error not in local_errors:
                local_errors.append(invalid_error)
            if len(identities) != len(unique):
                local_errors.append(f"group {code}: duplicate lecture keys")
            if not source_cap_reached and (
                total != exposed or total != len(unique)
            ):
                local_errors.append(
                    f"group {code}: declared {total}, exposed {exposed}, "
                    f"unique {len(unique)}"
                )
            group_totals[label] = total
            group_ids[label] = unique
            group_failures.extend(local_errors)
            for identity in unique:
                membership[identity] += 1
                branch_by_id[identity] = (code, label)
        errors.extend(group_failures)
        duplicate_group_ids = {identity for identity, count in membership.items() if count != 1}
        group_union = set(membership)
        if duplicate_group_ids:
            errors.append(
                f"{len(duplicate_group_ids)} lecture keys occur in multiple official groups"
            )
        if group_union != set(all_ids):
            errors.append(
                f"official group union {len(group_union)} does not match all-list {len(set(all_ids))}"
            )
        if sum(group_totals.values()) != declared_total:
            errors.append(
                f"official group totals {sum(group_totals.values())} do not match all-list {declared_total}"
            )
        if not errors:
            for row in all_rows:
                identity = _clean(row.get("raw_fields", {}).get("lecture_key"))
                code, label = branch_by_id[identity]
                row.update(
                    {
                        "branch": label,
                        "branch_code": _stable_branch_code(label),
                        "branch_url": _list_url(code),
                        "preserve_branch": True,
                    }
                )
                row["raw_fields"].update(
                    {"official_group_code": code, "official_group_label": label}
                )

    expired_count = 0
    current_rows: list[dict[str, Any]] = []
    for row in all_rows:
        end_text = _clean(row.get("end_date"))
        try:
            ended = date.fromisoformat(end_text)
        except ValueError:
            errors.append(
                f"lecture {_clean(row.get('provider_course_id'))}: invalid education end date"
            )
            continue
        if ended < cutoff:
            expired_count += 1
        else:
            current_rows.append(row)

    list_complete = not errors and bool(declared_total or not all_rows)
    detail_required_count = len(current_rows)
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    allowed = max(0, int(detail_limit))
    if allowed < detail_required_count:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed} of {detail_required_count} required details"
        )
    elif list_complete and current_rows:
        sessions: list[Any] = []
        sessions_lock = threading.Lock()
        local = threading.local()

        def thread_session() -> Any:
            value = getattr(local, "session", None)
            if value is None:
                value = session_factory()
                local.session = value
                with sessions_lock:
                    sessions.append(value)
            return value

        def enrich(row: dict[str, Any]) -> tuple[bool, list[str]]:
            identity = _clean(row.get("provider_course_id"))
            try:
                soup = _fetch(fetcher, thread_session(), _clean(row.get("raw_url")), timeout)
                return True, _detail(row, soup)
            except Exception as exc:
                return False, [f"{identity}: detail fetch {type(exc).__name__}"]

        detail_attempts = detail_required_count
        try:
            workers = min(
                GEUMCHEON_MAX_WORKERS,
                max(1, int(max_workers)),
                detail_required_count,
            )
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="geumcheon-detail") as pool:
                detail_results = list(pool.map(enrich, current_rows))
        finally:
            for value in sessions:
                _close_quietly(value)
        detail_pages = sum(success for success, _item_errors in detail_results)
        detail_errors = [
            error
            for _success, item_errors in detail_results
            for error in item_errors
        ]
    elif list_complete:
        detail_attempts = 0

    errors.extend(detail_errors)
    details_complete = (
        detail_attempts == detail_required_count
        and detail_pages == detail_required_count
        and not detail_errors
    )

    rows = [_clean_row(row) for row in current_rows]
    dedupe = dedupe_rows or _dedupe_default
    if list_complete and details_complete:
        try:
            deduped = list(dedupe(rows))
        except Exception as exc:
            errors.append(f"dedupe failed {type(exc).__name__}")
            deduped = []
        if len(deduped) != len(rows):
            errors.append(f"dedupe changed complete row count {len(rows)} to {len(deduped)}")
        rows = deduped

    snapshot_complete = list_complete and details_complete and not errors
    if not snapshot_complete:
        rows = []
    status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in current_rows
    )
    branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
    domain_category_counts = Counter(
        _clean(row.get("domain_category")) for row in current_rows
    )
    service_group_counts = Counter(
        _clean(row.get("service_group")) for row in current_rows
    )
    meta: dict[str, Any] = {
        "pages": list_requests,
        "list_requests": list_requests,
        "max_pages": allowed_list_requests,
        "required_list_requests": required_list_requests,
        "page_unit": GEUMCHEON_PAGE_UNIT,
        "total_pages": declared_pages,
        "total_count": declared_total,
        "discovered_links": len(set(all_ids)),
        "invalid_count": invalid_count,
        "duplicate_count": duplicate_count,
        "expired_count": expired_count,
        "current_count": len(current_rows),
        "returned_count": len(rows),
        "detail_required_count": detail_required_count,
        "required_detail_count": detail_required_count,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "pagination_detected": pagination_detected,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "group_count": len(GEUMCHEON_GROUPS),
        "group_totals": group_totals,
        "status_counts": dict(status_counts),
        "branch_counts": dict(branch_counts),
        "domain_category_counts": dict(domain_category_counts),
        "service_group_counts": dict(service_group_counts),
        "reservation_discovery_links": sum(
            bool(row.get("application_url")) for row in current_rows
        ),
        "no_current_data": snapshot_complete and not current_rows,
        "no_current_reason": (
            "all official Geumcheon education and experience programs have ended"
            if snapshot_complete and not current_rows
            else ""
        ),
    }
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
    return rows, GEUMCHEON_PARSER, meta


collect_geumcheon_target = collect_geumcheon_current


__all__ = [
    "GEUMCHEON_AGREE_PATH",
    "GEUMCHEON_DETAIL_PATH",
    "GEUMCHEON_GROUPS",
    "GEUMCHEON_HOST",
    "GEUMCHEON_KEY",
    "GEUMCHEON_LIST_PATH",
    "GEUMCHEON_MAX_WORKERS",
    "GEUMCHEON_MUNICIPALITY_CODE",
    "GEUMCHEON_MUNICIPALITY_NAME",
    "GEUMCHEON_PAGE_UNIT",
    "GEUMCHEON_PARSER",
    "GEUMCHEON_PROVIDER",
    "GEUMCHEON_REQUIRED_LIST_REQUESTS",
    "GEUMCHEON_URL",
    "collect_geumcheon_current",
    "collect_geumcheon_target",
    "geumcheon_agree_url",
    "geumcheon_detail_url",
    "is_geumcheon_target",
    "is_target",
]
