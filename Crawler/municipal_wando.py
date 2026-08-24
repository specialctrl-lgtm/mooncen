"""Fail-closed collectors for Wando-gun's two official education catalogues.

The Wando website rejects a cold request to ``sub.cs`` with HTTP 400.  A
normal, certificate-verified visit to the official Wando home page establishes
the required session cookie, after which the server-side rendered catalogues
are available.  This module keeps that warm-up and every catalogue/detail
request on one sequential session because the origin is also unreliable under
parallel TLS connections.

Each catalogue exposes a descending source number, fifteen rows per page and a
complete numbered pager.  A snapshot is published only after the source
numbering is continuous through one, every declared page was read, and the
page immediately after the declared end is empty.  Historical rows are used to
prove completeness; only current/future rows are returned and those rows must
also pass their detail-page contract.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


WANDO_HOST = "www.wando.go.kr"
WANDO_ROOT_URL = "https://www.wando.go.kr/wando/index.cs"
WANDO_LIST_PATH = "/wando/sub.cs"
WANDO_PAGE_SIZE = 15
WANDO_MUNICIPALITY_CODE = "1285000000"
WANDO_MUNICIPALITY_NAME = "전남광주통합특별시 완도군"
WANDO_PARSER = "wando_education_complete_pages+sentinel+current_detail"

WANDO_LIFELONG_PROVIDER = "MUNI_WWW_WANDO_GO_KR_AFCA6FD7"
WANDO_LITERACY_PROVIDER = "MUNI_WWW_WANDO_GO_KR_64D0194B"
WANDO_LIFELONG_URL = "https://www.wando.go.kr/wando/sub.cs?m=490"
WANDO_LITERACY_URL = "https://www.wando.go.kr/wando/sub.cs?m=502"


@dataclass(frozen=True)
class WandoSource:
    provider: str
    url: str
    menu: str
    detail_menu: str
    catalogue_name: str


WANDO_SOURCES = (
    WandoSource(
        provider=WANDO_LIFELONG_PROVIDER,
        url=WANDO_LIFELONG_URL,
        menu="490",
        detail_menu="886",
        catalogue_name="완도군 평생교육",
    ),
    WandoSource(
        provider=WANDO_LITERACY_PROVIDER,
        url=WANDO_LITERACY_URL,
        menu="502",
        detail_menu="1145",
        catalogue_name="완도군 문해교육",
    ),
)
_SOURCE_BY_PROVIDER = {source.provider: source for source in WANDO_SOURCES}

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_INFO_ID_RE = re.compile(r"INFO_\d{15}")
_DATE_RANGE_RE = re.compile(
    r"(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})\s*~\s*"
    r"(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)"
)
_CAPACITY_RE = re.compile(r"([\d,]+)\s*/\s*([\d,]+)\s*명")
_TOTAL_CAPACITY_RE = re.compile(r"([\d,]+)\s*명")
_LIST_HEADERS = ("번호", "강좌명", "정원", "수강기간", "장소", "기관", "접수")
_STATUS_MAP: Mapping[str, str] = {
    "접수": "OPEN",
    "접수중": "OPEN",
    "모집중": "OPEN",
    "접수예정": "SCHEDULED",
    "모집예정": "SCHEDULED",
    "마감": "CLOSED",
    "접수마감": "CLOSED",
    "모집마감": "CLOSED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).lower()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _source_for_target(target: Any) -> Optional[WandoSource]:
    source = _SOURCE_BY_PROVIDER.get(_provider(target))
    if source is None or _target_url(target) != source.url:
        return None
    return source


def is_wando_education_target(target: Any) -> bool:
    """Return true only for one of the two exact provider-owned routes."""

    return _source_for_target(target) is not None


is_target = is_wando_education_target


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
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
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
    if 300 <= status < 400:
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


def _fetch(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def wando_list_url(provider: Any, page: Any = 1) -> str:
    source = _SOURCE_BY_PROVIDER.get(_clean(provider))
    raw_page = _clean(page)
    if source is None or not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    page_no = int(raw_page)
    if page_no == 1:
        return source.url
    return f"https://{WANDO_HOST}{WANDO_LIST_PATH}?" + urlencode(
        (("m", source.menu), ("currentPageNo", page_no))
    )


def wando_detail_url(provider: Any, identity: Any) -> str:
    source = _SOURCE_BY_PROVIDER.get(_clean(provider))
    raw_identity = _clean(identity)
    if source is None or not _INFO_ID_RE.fullmatch(raw_identity):
        return ""
    return f"https://{WANDO_HOST}{WANDO_LIST_PATH}?" + urlencode(
        (("m", source.detail_menu), ("infoId", raw_identity))
    )


def _single_query_value(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _page_from_href(source: WandoSource, value: Any) -> int:
    parsed = urlparse(urljoin(source.url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    page = _single_query_value(query, "currentPageNo")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != WANDO_HOST
        or parsed.port is not None
        or parsed.path != WANDO_LIST_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or set(query) != {"m", "currentPageNo"}
        or _single_query_value(query, "m") != source.menu
        or not page.isdigit()
        or int(page) < 1
    ):
        return 0
    return int(page)


def _pagination_contract(
    soup: BeautifulSoup, source: WandoSource
) -> Optional[tuple[tuple[int, ...], int]]:
    pagers = soup.select("div.paging")
    if len(pagers) != 1:
        return None
    links = pagers[0].select("a[href]")
    if not links:
        return None
    pages: list[int] = []
    active_pages: list[int] = []
    for link in links:
        page = _page_from_href(source, link.get("href"))
        if not page:
            return None
        pages.append(page)
        classes = {_clean(value) for value in (link.get("class") or [])}
        if "on" in classes:
            active_pages.append(page)
    if len(pages) != len(set(pages)) or len(active_pages) > 1:
        return None
    return tuple(sorted(pages)), active_pages[0] if active_pages else 0


def _detail_identity(source: WandoSource, value: Any) -> tuple[str, str]:
    parsed = urlparse(urljoin(source.url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query_value(query, "infoId")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != WANDO_HOST
        or parsed.port is not None
        or parsed.path != WANDO_LIST_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or set(query) != {"m", "infoId"}
        or _single_query_value(query, "m") != source.detail_menu
        or not _INFO_ID_RE.fullmatch(identity)
    ):
        return "", ""
    return identity, wando_detail_url(source.provider, identity)


def _range(value: Any) -> tuple[str, str, str]:
    match = _DATE_RANGE_RE.search(_clean(value))
    if match is None:
        return "", "", ""
    try:
        start = date(*(int(part) for part in match.groups()[:3]))
        end = date(*(int(part) for part in match.groups()[3:]))
    except ValueError:
        return "", "", ""
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _capacity(value: Any) -> tuple[Optional[int], Optional[int]]:
    match = _CAPACITY_RE.search(_clean(value))
    if match is None:
        return None, None
    current = int(match.group(1).replace(",", ""))
    total = int(match.group(2).replace(",", ""))
    if current < 0 or total < 1 or current > total:
        return None, None
    return current, total


def _stable_branch_code(source: WandoSource, branch: str) -> str:
    digest = hashlib.sha1(
        f"{source.provider}|{_normalized(branch)}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"WANDO_BRANCH_{digest}"


def _parse_list_page(
    target: Any,
    source: WandoSource,
    soup: BeautifulSoup,
    *,
    page: int,
) -> tuple[list[dict[str, Any]], int]:
    tables = soup.select("table.board_t1")
    if len(tables) != 1:
        return [], 1
    table = tables[0]
    header_row = table.find("tr")
    headers = tuple(
        _clean(cell.get_text(" ", strip=True))
        for cell in (header_row.find_all("th", recursive=False) if header_row else [])
    )
    if headers != _LIST_HEADERS:
        return [], 1

    parsed: list[dict[str, Any]] = []
    malformed = 0
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        if (
            len(cells) == 1
            and "nolist" in (cells[0].get("class") or [])
            and _clean(cells[0].get("colspan")) == str(len(_LIST_HEADERS))
            and _clean(cells[0].get_text(" ", strip=True))
            == "등록(검색)된 데이터가 없습니다."
        ):
            continue
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        link = cells[1].select_one("a[href*='infoId']") if len(cells) > 1 else None
        identity, raw_url = _detail_identity(source, link.get("href") if link else "")
        title = _clean(link.get_text(" ", strip=True)) if link else ""
        source_number = int(values[0]) if values and values[0].isdigit() else 0
        start, end, period = _range(values[3] if len(values) > 3 else "")
        capacity_current, capacity_total = _capacity(values[2] if len(values) > 2 else "")
        raw_status = values[6] if len(values) > 6 else ""
        venue = values[4] if len(values) > 4 else ""
        institution = values[5] if len(values) > 5 else ""
        if (
            len(values) != len(_LIST_HEADERS)
            or not source_number
            or not identity
            or not raw_url
            or not title
            or not start
            or not end
            or capacity_current is None
            or capacity_total is None
            or not venue
            or not institution
            or raw_status not in _STATUS_MAP
        ):
            malformed += 1
            continue
        branch = institution
        parsed.append(
            {
                "provider": source.provider,
                "provider_course_id": f"{source.provider}:{identity}"[:100],
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "program_type": "교육·강좌",
                "category": "교육·강좌",
                "branch": branch,
                "branch_code": _stable_branch_code(source, branch),
                "branch_url": source.url,
                "preserve_branch": True,
                "raw_url": raw_url,
                "status": _STATUS_MAP[raw_status],
                "period": period,
                "start_date": start,
                "end_date": end,
                "room": venue,
                "venue_name": venue,
                "capacity": capacity_total,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "reservation_available": False,
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "municipality_code": WANDO_MUNICIPALITY_CODE,
                "municipality_full_name": WANDO_MUNICIPALITY_NAME,
                "collection_type": "complete_ssr_pages+sentinel+current_detail",
                "description": _clean(" ".join(values[2:])),
                "raw_fields": {
                    "parser": WANDO_PARSER,
                    "catalogue_menu": source.menu,
                    "detail_menu": source.detail_menu,
                    "identity": identity,
                    "source_number": source_number,
                    "source_page": page,
                    "source_status": raw_status,
                    "list_venue": venue,
                    "list_institution": institution,
                    "list_capacity": values[2],
                    "list_cells": values,
                },
            }
        )
    return parsed, malformed


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    tables = soup.select("table.board_t1_view")
    if len(tables) != 1:
        return {}
    result: dict[str, str] = {}
    for tr in tables[0].select("tr"):
        heading = tr.find("th", recursive=False)
        value = tr.find("td", recursive=False)
        if heading is None or value is None:
            continue
        key = _clean(heading.get_text(" ", strip=True))
        if key and key not in result:
            result[key] = _clean(value.get_text(" ", strip=True))
    return result


def _detail_marker_matches(
    soup: BeautifulSoup, source: WandoSource, identity: str
) -> bool:
    marker = soup.select_one("link[rel~='canonical'][href]")
    if marker is None:
        return False
    parsed = urlparse(urljoin(source.url, _clean(marker.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        parsed.scheme.lower() in {"http", "https"}
        and (parsed.hostname or "").rstrip(".").lower() == WANDO_HOST
        and parsed.port is None
        and parsed.path == WANDO_LIST_PATH
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
        and set(query) == {"m", "infoId"}
        and _single_query_value(query, "m") == source.detail_menu
        and _single_query_value(query, "infoId") == identity
    )


def _validate_detail(
    row: dict[str, Any], source: WandoSource, soup: BeautifulSoup
) -> list[str]:
    raw = row.get("raw_fields", {})
    identity = _clean(raw.get("identity"))
    errors: list[str] = []
    if not _detail_marker_matches(soup, source, identity):
        errors.append(f"{identity}: detail canonical identity mismatch")
    pairs = _detail_pairs(soup)
    required = {
        "강좌명",
        "신청기간",
        "교육기간",
        "교육장소",
        "교육기관",
        "모집정원",
    }
    if not required.issubset(pairs):
        errors.append(f"{identity}: detail fields are incomplete")
        return errors
    if _normalized(pairs.get("강좌명")) != _normalized(row.get("title")):
        errors.append(f"{identity}: detail title mismatch")
    detail_start, detail_end, detail_period = _range(pairs.get("교육기간"))
    if (
        detail_start != _clean(row.get("start_date"))
        or detail_end != _clean(row.get("end_date"))
        or detail_period != _clean(row.get("period"))
    ):
        errors.append(f"{identity}: detail education period mismatch")
    if _normalized(pairs.get("교육장소")) != _normalized(row.get("venue_name")):
        errors.append(f"{identity}: detail venue mismatch")
    if _normalized(pairs.get("교육기관")) != _normalized(row.get("branch")):
        errors.append(f"{identity}: detail institution mismatch")
    capacity_match = _TOTAL_CAPACITY_RE.fullmatch(_clean(pairs.get("모집정원")))
    detail_capacity = (
        int(capacity_match.group(1).replace(",", "")) if capacity_match else None
    )
    if detail_capacity != row.get("capacity_total"):
        errors.append(f"{identity}: detail capacity mismatch")
    apply_start, apply_end, apply_period = _range(pairs.get("신청기간"))
    if not apply_start or not apply_end:
        errors.append(f"{identity}: detail application period is missing")
    if errors:
        return errors

    row.update(
        {
            "apply_start": apply_start,
            "apply_end": apply_end,
            "apply_period": apply_period,
        }
    )
    target = _clean(pairs.get("교육대상"))
    fee = _clean(pairs.get("수강료"))
    phone = _clean(pairs.get("문의전화"))
    instructor = _clean(pairs.get("강사소개"))
    description = _clean(pairs.get("강좌소개/강의계획"))
    if target:
        row["target"] = target
    if fee:
        row["fee"] = fee
    if phone:
        row["contact"] = phone
    if instructor:
        row["instructor"] = instructor.removeprefix("강사명 :").strip()
    if description:
        row["description"] = description
    raw["detail_pairs"] = pairs
    raw["detail_valid"] = True
    return []


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


def collect_wando_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = 10,
    detail_limit: int = 200,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Wando education snapshot."""

    source = _source_for_target(target)
    if source is None:
        return [], WANDO_PARSER, _failure("target does not match a canonical Wando provider route")
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], WANDO_PARSER, _failure(
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
    rows: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    source_total = 0
    source_pages = 0
    required_list_requests = 0
    list_requests = 0
    malformed_count = 0
    duplicate_count = 0
    expired_count = 0
    source_cap_reached = False
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    list_complete = False
    details_complete = False
    current_session: Any = None

    try:
        current_session = session_factory()
        warm = _fetch(fetcher, current_session, WANDO_ROOT_URL, timeout)
        warm_title = _clean(warm.title.get_text(" ", strip=True)) if warm.title else ""
        if "완도" not in warm_title:
            errors.append("official home warm-up response is not the Wando site")
        if allowed_pages < 1:
            source_cap_reached = True
            errors.append("max_pages cap cannot inspect the first official page")
        else:
            first = _fetch(fetcher, current_session, source.url, timeout)
            list_requests += 1
            first_rows, malformed = _parse_list_page(
                target, source, first, page=1
            )
            malformed_count += malformed
            first_contract = _pagination_contract(first, source)
            if malformed or not first_rows:
                errors.append("first page is empty or contains malformed rows")
            elif first_contract is None:
                errors.append("first-page pagination contract is malformed")
            else:
                source_total = int(first_rows[0]["raw_fields"]["source_number"])
                source_pages = math.ceil(source_total / WANDO_PAGE_SIZE)
                required_list_requests = source_pages + 1
                expected_page_numbers = tuple(range(1, source_pages + 1))
                if first_contract != (expected_page_numbers, 1):
                    errors.append("first-page pagination contract does not match the source total")
                expected_first = list(
                    range(source_total, max(0, source_total - WANDO_PAGE_SIZE), -1)
                )
                actual_first = [
                    int(row["raw_fields"]["source_number"]) for row in first_rows
                ]
                if actual_first != expected_first:
                    errors.append("first-page source numbering mismatch")
                rows.extend(first_rows)

                if required_list_requests > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap allows {allowed_pages} of {required_list_requests} required list requests"
                    )
                else:
                    for page in range(2, source_pages + 1):
                        soup = _fetch(
                            fetcher,
                            current_session,
                            wando_list_url(source.provider, page),
                            timeout,
                        )
                        list_requests += 1
                        page_rows, malformed = _parse_list_page(
                            target, source, soup, page=page
                        )
                        malformed_count += malformed
                        contract = _pagination_contract(soup, source)
                        if contract != (expected_page_numbers, page):
                            errors.append(f"page {page}: pagination contract mismatch")
                        expected_start = source_total - (page - 1) * WANDO_PAGE_SIZE
                        expected_end = max(0, expected_start - WANDO_PAGE_SIZE)
                        expected_numbers = list(range(expected_start, expected_end, -1))
                        actual_numbers = [
                            int(row["raw_fields"]["source_number"])
                            for row in page_rows
                        ]
                        if malformed or actual_numbers != expected_numbers:
                            errors.append(
                                f"page {page}: source numbering mismatch or malformed rows"
                            )
                        rows.extend(page_rows)

                    sentinel_page = source_pages + 1
                    sentinel = _fetch(
                        fetcher,
                        current_session,
                        wando_list_url(source.provider, sentinel_page),
                        timeout,
                    )
                    list_requests += 1
                    sentinel_rows, sentinel_malformed = _parse_list_page(
                        target, source, sentinel, page=sentinel_page
                    )
                    malformed_count += sentinel_malformed
                    sentinel_contract = _pagination_contract(sentinel, source)
                    if sentinel_rows or sentinel_malformed:
                        errors.append("post-boundary sentinel page is not empty")
                    if sentinel_contract != (expected_page_numbers, 0):
                        errors.append("post-boundary sentinel pagination contract mismatch")
    except Exception as exc:
        errors.append(f"list fetch {type(exc).__name__}")

    identities = [
        _clean(row.get("raw_fields", {}).get("identity")) for row in rows
    ]
    duplicate_count = len(identities) - len(set(identities))
    if duplicate_count:
        errors.append(f"{duplicate_count} duplicate course identities")
    if source_total and len(rows) != source_total:
        errors.append(f"source declared {source_total}, parsed {len(rows)}")
    expected_all_numbers = list(range(source_total, 0, -1))
    actual_all_numbers = [
        int(row.get("raw_fields", {}).get("source_number", 0)) for row in rows
    ]
    if source_total and actual_all_numbers != expected_all_numbers:
        errors.append("full source numbering is not continuous through one")

    for row in rows:
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
        and len(rows) == source_total
        and actual_all_numbers == expected_all_numbers
    )
    required_details = len(current_rows)
    if allowed_details < required_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} required details"
        )
    elif list_complete:
        for row in current_rows:
            detail_attempts += 1
            identity = _clean(row.get("raw_fields", {}).get("identity"))
            try:
                detail = _fetch(
                    fetcher,
                    current_session,
                    _clean(row.get("raw_url")),
                    timeout,
                )
                item_errors = _validate_detail(row, source, detail)
                if item_errors:
                    detail_errors.extend(item_errors)
                else:
                    detail_pages += 1
            except Exception as exc:
                detail_errors.append(f"{identity}: detail fetch {type(exc).__name__}")

    errors.extend(detail_errors)
    details_complete = (
        list_complete
        and detail_attempts == required_details
        and detail_pages == required_details
        and not detail_errors
    )
    cleaned = [_clean_row(row) for row in current_rows]
    dedupe = dedupe_rows or _dedupe_default
    if list_complete and details_complete:
        try:
            deduped = list(dedupe(cleaned))
        except Exception as exc:
            errors.append(f"dedupe failed {type(exc).__name__}")
            deduped = []
        if len(deduped) != len(cleaned):
            errors.append(
                f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}"
            )
        cleaned = deduped

    snapshot_complete = list_complete and details_complete and not errors
    if not snapshot_complete:
        cleaned = []
    _close_quietly(current_session)

    status_counts = Counter(_clean(row.get("status")) for row in current_rows)
    branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
    source_branch_counts = Counter(_clean(row.get("branch")) for row in rows)
    meta: dict[str, Any] = {
        "pages": list_requests,
        "list_requests": list_requests,
        "required_list_requests": required_list_requests,
        "warmup_requests": 1 if current_session is not None else 0,
        "max_pages": allowed_pages,
        "page_unit": WANDO_PAGE_SIZE,
        "source_total": source_total,
        "source_pages": source_pages,
        "sentinel_pages": 1 if required_list_requests else 0,
        "discovered_links": len(set(identities)),
        "malformed_count": malformed_count,
        "duplicate_count": duplicate_count,
        "expired_count": expired_count,
        "current_count": len(current_rows),
        "returned_count": len(cleaned),
        "required_detail_count": required_details,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "pagination_detected": source_pages > 1,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "status_counts": dict(status_counts),
        "branch_count": len(branch_counts),
        "branch_counts": dict(branch_counts),
        "source_branch_count": len(source_branch_counts),
        "source_branch_counts": dict(source_branch_counts),
        "reservation_discovery_links": 0,
        "no_current_data": snapshot_complete and not current_rows,
        "no_current_reason": (
            f"all official {source.catalogue_name} rows have ended"
            if snapshot_complete and not current_rows
            else ""
        ),
    }
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
    return cleaned, WANDO_PARSER, meta


collect_wando_target = collect_wando_education


__all__ = [
    "WANDO_HOST",
    "WANDO_LIFELONG_PROVIDER",
    "WANDO_LIFELONG_URL",
    "WANDO_LIST_PATH",
    "WANDO_LITERACY_PROVIDER",
    "WANDO_LITERACY_URL",
    "WANDO_MUNICIPALITY_CODE",
    "WANDO_MUNICIPALITY_NAME",
    "WANDO_PAGE_SIZE",
    "WANDO_PARSER",
    "WANDO_ROOT_URL",
    "WANDO_SOURCES",
    "collect_wando_education",
    "collect_wando_target",
    "is_target",
    "is_wando_education_target",
    "wando_detail_url",
    "wando_list_url",
]
