"""Fail-closed collector for Mokpo City's official education catalogue.

The Mokpo lifelong-learning portal exposes the same 94 resident-centre
programmes through both ``me_id=sub220`` and ``me_id=sub222``.  The portal
root also links into that same result set.  This collector deliberately owns
only the already-configured, explicit ``sub222`` target and publishes one
canonical provider snapshot; the root provider and the ``sub220`` menu alias
must remain excluded as duplicate sources.

The catalogue has fifteen rows per page and a declared final-page link.  A
snapshot is published only after every declared page and the immediately
following empty sentinel have been read, every current/future row has passed
its detail-page contract, and identity/URL/semantic duplicate checks succeed.
Historical rows are used to prove page completeness but are not returned.

This module intentionally does not import ``Crawler_MunicipalYaml`` so the
shared router can import it without creating a cycle.  Production callers
must inject the router's managed fetcher and session factory.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


MOKPO_PROVIDER = "MUNI_LIFELONG_MOKPO_GO_KR_0E89BA53"
MOKPO_URL = (
    "https://lifelong.mokpo.go.kr/lecture/lecture_list_program.php?"
    "me_id=sub222"
)
MOKPO_HOST = "lifelong.mokpo.go.kr"
MOKPO_LIST_PATH = "/lecture/lecture_list_program.php"
MOKPO_DETAIL_PATH = "/lecture/lecture_list_view.php"
MOKPO_MENU_ID = "sub222"
MOKPO_PAGE_SIZE = 15
MOKPO_MUNICIPALITY_CODE = "1211000000"
MOKPO_MUNICIPALITY_NAME = "전남광주통합특별시 목포시"
MOKPO_PARSER = "mokpo_lifelong_complete_pages+sentinel+current_detail"

# These official routes are audited exclusions, not additional providers.
MOKPO_DUPLICATE_ALIAS_URLS = (
    "https://lifelong.mokpo.go.kr/",
    "https://lifelong.mokpo.go.kr/lecture/lecture_list_program.php?me_id=sub220",
)
MOKPO_EXCLUDED_STATIC_INFO_URLS = (
    "https://lifelong.mokpo.go.kr/lecture/lecture_list_program.php?me_id=sub230",
    "https://lifelong.mokpo.go.kr/lecture/lecture_organ.php?me_id=sub311",
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d{1,10}")
_DATE_RANGE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})"
    r"\s*~\s*"
    r"(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_CAPACITY_RE = re.compile(
    r"접수\s*:\s*([\d,]+)\s*명\s*/\s*정원\s*:\s*([\d,]+)\s*명"
)
_LIST_HEADERS = (
    "교육기관",
    "강좌명",
    "강사명",
    "교육기간",
    "신청기간",
    "수강료",
    "상태",
)
_DETAIL_KEYS = (
    "프로그램명",
    "강좌명",
    "분야",
    "강의분야",
    "신청방법",
    "강좌상태",
    "신청기간",
    "접수/정원",
    "교육기간",
    "교육일시",
    "교육대상",
    "강사명",
    "수강료",
    "재료비",
    "교육장소",
    "교육기관",
    "강좌소개",
    "강의자료",
    "홈페이지",
    "문의",
    "기타",
)
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수전": "SCHEDULED",
    "접수마감": "CLOSED",
    "교육중": "CLOSED",
    "교육마감": "CLOSED",
    "종료": "CLOSED",
    "휴강": "CANCELLED",
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


def is_mokpo_education_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == MOKPO_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == MOKPO_HOST
        and parsed.port is None
        and parsed.path == MOKPO_LIST_PATH
        and parse_qs(parsed.query, keep_blank_values=True)
        == {"me_id": [MOKPO_MENU_ID]}
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_mokpo_education_target


def mokpo_list_url(page: Any = 1) -> str:
    raw_page = _clean(page)
    if not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    page_number = int(raw_page)
    if page_number == 1:
        return MOKPO_URL
    return f"https://{MOKPO_HOST}{MOKPO_LIST_PATH}?" + urlencode(
        (("me_id", MOKPO_MENU_ID), ("page", page_number))
    )


def mokpo_detail_url(identity: Any) -> str:
    raw_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{MOKPO_HOST}{MOKPO_DETAIL_PATH}?" + urlencode(
        (("le_id", raw_identity), ("me_id", MOKPO_MENU_ID))
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


def _fetch(
    fetcher: Fetcher,
    current_session: Any,
    url: str,
    timeout: int,
) -> BeautifulSoup:
    if not url:
        raise ValueError("empty fetch URL")
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _single_query_value(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _detail_identity(value: Any, base_url: str) -> tuple[str, str]:
    parsed = urlparse(urljoin(base_url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query_value(query, "le_id")
    expected_page = _single_query_value(
        parse_qs(urlparse(base_url).query, keep_blank_values=True), "page"
    ) or "1"
    linked_page = _single_query_value(query, "page")
    allowed_keys = {"le_id", "me_id"}
    if "page" in query:
        allowed_keys.add("page")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != MOKPO_HOST
        or parsed.port is not None
        or parsed.path != MOKPO_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or set(query) != allowed_keys
        or _single_query_value(query, "me_id") != MOKPO_MENU_ID
        or ("page" in query and linked_page != expected_page)
        or not _IDENTITY_RE.fullmatch(identity)
    ):
        return "", ""
    return identity, mokpo_detail_url(identity)


def _page_from_href(value: Any, base_url: str) -> int:
    parsed = urlparse(urljoin(base_url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    raw_page = _single_query_value(query, "page")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != MOKPO_HOST
        or parsed.port is not None
        or parsed.path != MOKPO_LIST_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or set(query) != {"me_id", "page"}
        or _single_query_value(query, "me_id") != MOKPO_MENU_ID
        or not raw_page.isdigit()
        or int(raw_page) < 1
    ):
        return 0
    return int(raw_page)


def _declared_last_page(soup: BeautifulSoup, base_url: str) -> int:
    wrappers = soup.select(".pg_wrap")
    if not wrappers:
        return 1
    if len(wrappers) != 1:
        return 0
    pages: list[int] = [1]
    active_page = _current_page(soup)
    if active_page < 0:
        return 0
    if active_page:
        pages.append(active_page)
    for link in wrappers[0].select("a.pg_page[href]"):
        page = _page_from_href(link.get("href"), base_url)
        if not page:
            return 0
        pages.append(page)
    ending = wrappers[0].select("a.pg_end[href]")
    if len(ending) > 1:
        return 0
    last_page = max(pages)
    if ending and _page_from_href(ending[0].get("href"), base_url) != last_page:
        return 0
    return last_page


def _current_page(soup: BeautifulSoup) -> int:
    values = soup.select(".pg_wrap .pg_current")
    if not values:
        return 0
    if len(values) != 1:
        return -1
    value = _clean(values[0].get_text(" ", strip=True))
    return int(value) if value.isdigit() and int(value) >= 1 else -1


def _catalogue_table(soup: BeautifulSoup) -> Optional[Any]:
    matches = []
    for table in soup.select("table"):
        headers = tuple(
            _clean(cell.get_text(" ", strip=True))
            for cell in table.select("thead th")
        )
        if headers == _LIST_HEADERS:
            matches.append(table)
    return matches[0] if len(matches) == 1 else None


def _date_range(value: Any) -> tuple[str, str, str]:
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


def _branch_name(value: Any) -> str:
    cleaned = _clean(value)
    if cleaned.endswith("동"):
        return re.sub(r"\s+", "", cleaned)
    return cleaned


def _branch_code(value: Any) -> str:
    digest = hashlib.sha1(
        f"{MOKPO_PROVIDER}|{_normalized(value)}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"MOKPO_BRANCH_{digest}"


def _parse_list_page(
    target: Any,
    soup: BeautifulSoup,
    *,
    page: int,
    source_url: str,
) -> tuple[list[dict[str, Any]], int]:
    table = _catalogue_table(soup)
    if table is None:
        return [], 1
    rows: list[dict[str, Any]] = []
    malformed = 0
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if (
            len(cells) == 1
            and _clean(cells[0].get("colspan")) == str(len(_LIST_HEADERS))
            and _clean(cells[0].get_text(" ", strip=True)) == "자료가 없습니다."
        ):
            continue
        if len(cells) != len(_LIST_HEADERS):
            malformed += 1
            continue
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        link = cells[1].select_one("a[href]")
        identity, raw_url = _detail_identity(
            link.get("href") if link is not None else "", source_url
        )
        branch = _branch_name(values[0])
        start, end, period = _date_range(values[3])
        apply_start, apply_end, apply_period = _date_range(values[4])
        source_status = values[6]
        if (
            not identity
            or not branch
            or not values[1]
            or not start
            or not apply_start
            or source_status not in _STATUS_MAP
        ):
            malformed += 1
            continue
        row: dict[str, Any] = {
            "provider": _provider(target),
            "provider_course_id": f"{_provider(target)}:lecture:{identity}"[:100],
            "prefer_incoming_provider_course_id": True,
            "title": values[1],
            "program_type": "동 주민사랑방 프로그램",
            "category": "교육·강좌",
            "branch": branch,
            "branch_code": _branch_code(branch),
            "branch_url": MOKPO_URL,
            "preserve_branch": True,
            "raw_url": raw_url,
            "application_url": raw_url,
            "reservation_available": False,
            "status": _STATUS_MAP[source_status],
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "instructor": values[2],
            "fee": values[5],
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": MOKPO_MUNICIPALITY_CODE,
            "municipality_full_name": MOKPO_MUNICIPALITY_NAME,
            "collection_type": "complete_pages+detail_html",
            "description": _clean(tr.get_text(" ", strip=True)),
            "raw_fields": {
                "parser": MOKPO_PARSER,
                "lecture_id": identity,
                "source_page": page,
                "source_status": source_status,
                "source_branch": values[0],
                "list_cells": values,
            },
        }
        rows.append(row)
    return rows, malformed


def _detail_pairs(soup: BeautifulSoup) -> Optional[dict[str, str]]:
    tables = soup.select("table.td_left")
    if len(tables) != 1:
        return None
    result: dict[str, str] = {}
    for tr in tables[0].select("tr"):
        pending = ""
        for cell in tr.find_all(["th", "td"], recursive=False):
            if cell.name == "th":
                pending = _clean(cell.get_text(" ", strip=True))
            elif pending:
                if pending in result:
                    return None
                if pending == "강좌상태":
                    state_nodes = cell.select(".state")
                    if len(state_nodes) > 1:
                        return None
                    result[pending] = _clean(
                        state_nodes[0].get_text(" ", strip=True)
                        if state_nodes
                        else cell.get_text(" ", strip=True)
                    )
                else:
                    result[pending] = _clean(cell.get_text(" ", strip=True))
                pending = ""
    return result


def _detail_errors(row: dict[str, Any], pairs: Optional[dict[str, str]]) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("lecture_id"))
    if pairs is None:
        return [f"lecture {identity}: detail table contract mismatch"]
    missing = [key for key in _DETAIL_KEYS if key not in pairs]
    errors = (
        [f"lecture {identity}: missing detail keys {','.join(missing)}"]
        if missing
        else []
    )
    comparisons = (
        ("title", pairs.get("강좌명"), row.get("title")),
        ("branch", _branch_name(pairs.get("교육기관")), row.get("branch")),
        (
            "status",
            pairs.get("강좌상태"),
            row.get("raw_fields", {}).get("source_status"),
        ),
        ("education period", pairs.get("교육기간"), row.get("period")),
        ("application period", pairs.get("신청기간"), row.get("apply_period")),
    )
    for label, actual, expected in comparisons:
        if _normalized(actual) != _normalized(expected):
            errors.append(f"lecture {identity}: detail/list {label} mismatch")
    if pairs.get("프로그램명") != "동 주민사랑방 프로그램":
        errors.append(f"lecture {identity}: unexpected programme owner")
    return errors


def _enrich_detail(row: dict[str, Any], pairs: dict[str, str]) -> None:
    current_capacity: Optional[int] = None
    total_capacity: Optional[int] = None
    match = _CAPACITY_RE.fullmatch(_clean(pairs.get("접수/정원")))
    if match is not None:
        current_capacity = int(match.group(1).replace(",", ""))
        total_capacity = int(match.group(2).replace(",", ""))
        if total_capacity < 1 or current_capacity < 0 or current_capacity > total_capacity:
            current_capacity = None
            total_capacity = None

    target_value = _clean(pairs.get("교육대상"))
    row.update(
        {
            "program_type": pairs.get("프로그램명") or row["program_type"],
            "category": pairs.get("강의분야") or pairs.get("분야") or row["category"],
            "application_method_raw": pairs.get("신청방법", ""),
            "capacity": pairs.get("접수/정원", ""),
            "capacity_current": current_capacity,
            "capacity_total": total_capacity,
            "schedule_raw": pairs.get("교육일시", ""),
            "target": target_value or "공식 페이지 미기재",
            "instructor": pairs.get("강사명") or row.get("instructor", ""),
            "fee": pairs.get("수강료") or row.get("fee", ""),
            "material_fee": pairs.get("재료비", ""),
            "venue_name": pairs.get("교육장소", ""),
            "phone": pairs.get("문의", ""),
        }
    )
    description_parts = (
        pairs.get("강좌소개"),
        pairs.get("기타"),
        pairs.get("강의자료"),
    )
    description = _clean(" ".join(value for value in description_parts if value))
    if description:
        row["description"] = description
    row["raw_fields"] = {
        **row["raw_fields"],
        "detail_pairs": pairs,
        "online_application_control": False,
        "target_source_omission": not target_value,
    }


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("branch")),
        _normalized(row.get("period")),
        _normalized(row.get("schedule_raw")),
    )


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "declared_pages": 0,
        "required_list_requests": 0,
        "sentinel_page": 0,
        "page_counts": {},
        "source_rows": 0,
        "current_count": 0,
        "expired_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "duplicate_count": 0,
        "duplicate_url_count": 0,
        "semantic_duplicate_count": 0,
        "branch_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "reservation_discovery_links": 0,
        "recursion_depth": 0,
        "configured_collection_error": "",
    }


def collect_mokpo_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 200,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future canonical Mokpo snapshot."""

    meta = _base_meta()
    if not is_mokpo_education_target(target):
        meta["configured_collection_error"] = "target is not the canonical Mokpo education route"
        return [], MOKPO_PARSER, meta
    if fetcher is None or session_factory is None:
        meta["configured_collection_error"] = (
            "managed fetcher and session_factory injection are required"
        )
        return [], MOKPO_PARSER, meta
    if max_pages < 1 or detail_limit < 0:
        meta["configured_collection_error"] = "max_pages/detail_limit are invalid"
        return [], MOKPO_PARSER, meta

    errors: list[str] = []
    current_session: Any = None
    all_rows: list[dict[str, Any]] = []
    try:
        current_session = session_factory()
        first_url = mokpo_list_url(1)
        first_soup = _fetch(fetcher, current_session, first_url, timeout)
        declared_pages = _declared_last_page(first_soup, first_url)
        if declared_pages < 1:
            raise ValueError("first-page pagination contract mismatch")
        sentinel_page = declared_pages + 1
        required_list_requests = sentinel_page
        meta.update(
            {
                "declared_pages": declared_pages,
                "required_list_requests": required_list_requests,
                "sentinel_page": sentinel_page,
                "pagination_detected": declared_pages > 1,
            }
        )
        if max_pages < required_list_requests:
            meta["source_cap_reached"] = True
            errors.append(
                f"max_pages cap allows {max_pages} of {required_list_requests} required list requests"
            )
        else:
            for page in range(1, required_list_requests + 1):
                source_url = mokpo_list_url(page)
                soup = first_soup if page == 1 else _fetch(
                    fetcher, current_session, source_url, timeout
                )
                meta["pages"] += 1
                current_page = _current_page(soup)
                observed_last_page = _declared_last_page(soup, source_url)
                if observed_last_page != declared_pages:
                    errors.append(
                        f"page {page}: declared final page changed to {observed_last_page}"
                    )
                if declared_pages > 1 and page <= declared_pages and current_page != page:
                    errors.append(
                        f"page {page}: active pagination marker is {current_page}"
                    )
                if page == sentinel_page and current_page not in {0}:
                    errors.append("sentinel page unexpectedly has an active page marker")
                rows, malformed = _parse_list_page(
                    target, soup, page=page, source_url=source_url
                )
                meta["page_counts"][page] = len(rows)
                if malformed:
                    errors.append(f"page {page}: {malformed} malformed catalogue rows")
                if page < declared_pages and len(rows) != MOKPO_PAGE_SIZE:
                    errors.append(
                        f"page {page}: expected {MOKPO_PAGE_SIZE} rows before final page"
                    )
                if page == declared_pages and not (0 <= len(rows) <= MOKPO_PAGE_SIZE):
                    errors.append(f"page {page}: invalid final-page row count")
                if declared_pages > 1 and page == declared_pages and not rows:
                    errors.append("declared final page is empty")
                if page == sentinel_page and rows:
                    errors.append("sentinel page is not empty")
                if page <= declared_pages:
                    all_rows.extend(rows)

        meta["source_rows"] = len(all_rows)
        identities = [row["raw_fields"]["lecture_id"] for row in all_rows]
        raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
        meta["duplicate_count"] = len(identities) - len(set(identities))
        meta["duplicate_url_count"] = len(raw_urls) - len(set(raw_urls))
        if meta["duplicate_count"]:
            errors.append(f"{meta['duplicate_count']} duplicate lecture identities")
        if meta["duplicate_url_count"]:
            errors.append(f"{meta['duplicate_url_count']} duplicate detail URLs")

        reference_day = _today(today)
        current_rows = [
            row for row in all_rows if date.fromisoformat(row["end_date"]) >= reference_day
        ]
        meta["current_count"] = len(current_rows)
        meta["expired_count"] = len(all_rows) - len(current_rows)
        if detail_limit < len(current_rows):
            meta["source_cap_reached"] = True
            errors.append(
                f"detail_limit allows {detail_limit} of {len(current_rows)} required details"
            )

        if not errors:
            for row in current_rows:
                identity = row["raw_fields"]["lecture_id"]
                meta["detail_attempts"] += 1
                try:
                    detail_soup = _fetch(
                        fetcher,
                        current_session,
                        mokpo_detail_url(identity),
                        timeout,
                    )
                    pairs = _detail_pairs(detail_soup)
                    detail_errors = _detail_errors(row, pairs)
                    if detail_errors:
                        meta["detail_errors"] += 1
                        errors.extend(detail_errors)
                        continue
                    assert pairs is not None
                    _enrich_detail(row, pairs)
                    meta["detail_pages"] += 1
                except Exception as exc:
                    meta["detail_errors"] += 1
                    errors.append(
                        f"lecture {identity}: detail fetch/parse failed ({type(exc).__name__})"
                    )

            semantic_counts = Counter(_semantic_key(row) for row in current_rows)
            meta["semantic_duplicate_count"] = sum(
                count - 1 for count in semantic_counts.values() if count > 1
            )
            if meta["semantic_duplicate_count"]:
                errors.append(
                    f"{meta['semantic_duplicate_count']} semantic duplicate courses"
                )

            if dedupe_rows is not None and not errors:
                deduped = list(dedupe_rows(current_rows))
                if len(deduped) != len(current_rows):
                    errors.append(
                        "dedupe changed complete row count "
                        f"{len(current_rows)} to {len(deduped)}"
                    )
                else:
                    current_rows = deduped

        meta["branch_count"] = len(
            {_normalized(row.get("branch")) for row in current_rows if row.get("branch")}
        )
        meta["details_complete"] = (
            meta["detail_pages"] == len(current_rows)
            and meta["detail_errors"] == 0
            and not meta["source_cap_reached"]
        )
        meta["pagination_complete"] = (
            meta["pages"] == meta["required_list_requests"]
            and not meta["source_cap_reached"]
            and not any("page" in error or "pagination" in error for error in errors)
        )
        meta["snapshot_complete"] = (
            not errors
            and meta["pagination_complete"]
            and meta["details_complete"]
            and meta["duplicate_count"] == 0
            and meta["duplicate_url_count"] == 0
            and meta["semantic_duplicate_count"] == 0
        )
        meta["no_current_data"] = meta["snapshot_complete"] and not current_rows
        if meta["no_current_data"]:
            meta["no_current_reason"] = (
                "the complete official Mokpo catalogue has no current/future courses"
                if all_rows
                else "the complete official Mokpo catalogue is empty"
            )
        meta["configured_collection_error"] = "; ".join(errors)
        return (
            current_rows if meta["snapshot_complete"] else [],
            MOKPO_PARSER,
            meta,
        )
    except Exception as exc:
        errors.append(f"catalogue fetch/parse failed ({type(exc).__name__})")
        meta["configured_collection_error"] = "; ".join(errors)
        return [], MOKPO_PARSER, meta
    finally:
        _close_quietly(current_session)


collect = collect_mokpo_education_courses


__all__ = [
    "MOKPO_DUPLICATE_ALIAS_URLS",
    "MOKPO_EXCLUDED_STATIC_INFO_URLS",
    "MOKPO_MENU_ID",
    "MOKPO_MUNICIPALITY_CODE",
    "MOKPO_MUNICIPALITY_NAME",
    "MOKPO_PARSER",
    "MOKPO_PROVIDER",
    "MOKPO_URL",
    "collect",
    "collect_mokpo_education_courses",
    "is_mokpo_education_target",
    "is_target",
    "mokpo_detail_url",
    "mokpo_list_url",
]
