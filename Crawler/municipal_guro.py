"""Fail-closed collector for Guro-gu's official education reservations.

Guro exposes two official education catalogs under one reservation service:
information-technology education and resident-center education.  The latter
contains several thousand historical rows, so a generic crawler is both slow
and unsafe: it can stop at a sample and still look successful.  This collector
uses the source's 1,000-row pagination, verifies every declared list row,
filters only courses whose education period has not ended, and requires every
returned course detail before publishing a snapshot.
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


GURO_PROVIDER = "MUNI_WWW_GURO_GO_KR_A4A5D3E3"
GURO_URL = (
    "https://www.guro.go.kr/yeyak/webEdcLctreList.do?"
    "key=3589&rep=1&searchLctreGroup=1&jachi=0"
)
GURO_RESIDENT_URL = (
    "https://www.guro.go.kr/yeyak/webEdcLctreList.do?"
    "key=3600&rep=1&searchLctreGroup=0&jachi=1"
)
GURO_HOST = "www.guro.go.kr"
GURO_LIST_PATH = "/yeyak/webEdcLctreList.do"
GURO_DETAIL_PATH = "/yeyak/edcLctreView.do"
GURO_AGREE_PATH = "/yeyak/webEdcLctreAgree.do"
GURO_INFORMATION_AGREE_PATH = "/yeyak/webEdcLctreAgree2.do"
GURO_PAGE_UNIT = 1000
GURO_MUNICIPALITY_CODE = "1153000000"
GURO_MUNICIPALITY_NAME = "서울특별시 구로구"
GURO_PARSER = "guro_two_official_catalogs_complete_current_future+detail"
GURO_MAX_WORKERS = 8

GURO_SOURCES: tuple[tuple[str, str, str, str, str], ...] = (
    ("information", "3589", "0", "1", "정보화교육"),
    ("resident_center", "3600", "1", "0", "자치회관"),
)

GURO_RESIDENT_BRANCHES = (
    "신도림동 자치회관",
    "구로1동 자치회관",
    "구로2동 자치회관",
    "구로3동 자치회관",
    "구로4동 자치회관",
    "구로5동 자치회관",
    "가리봉동 자치회관",
    "고척1동 자치회관",
    "고척2동 자치회관",
    "개봉1동 자치회관",
    "개봉2동 자치회관",
    "개봉3동 자치회관",
    "오류1동 자치회관",
    "오류2동 자치회관",
    "수궁동 자치회관",
    "항동 자치회관",
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DECLARATION_RE = re.compile(
    r"총\s*([\d,]+)\s*건\s*\[\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\]"
)
_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})[.\-/](?P<month>\d{1,2})[.\-/](?P<day>\d{1,2})(?!\d)"
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
    return _clean(_target_value(target, "url")).rstrip("&")


def is_guro_education_target(target: Any) -> bool:
    return _provider(target) == GURO_PROVIDER and _target_url(target) == GURO_URL


is_target = is_guro_education_target


def _default_session_factory() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return value


def _default_fetcher(current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        raise ValueError("empty HTTP response")
    final_host = (urlparse(_clean(getattr(response, "url", ""))).hostname or "").lower()
    if final_host and final_host != GURO_HOST:
        raise ValueError("unexpected cross-host redirect")
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


def _source_url(source: tuple[str, str, str, str, str], page: int) -> str:
    _kind, key, jachi, group, _label = source
    return f"https://{GURO_HOST}{GURO_LIST_PATH}?" + urlencode(
        {
            "key": key,
            "rep": "1",
            "searchLctreGroup": group,
            "jachi": jachi,
            "pageUnit": GURO_PAGE_UNIT,
            "pageIndex": page,
        }
    )


def guro_detail_url(lecture_key: str, *, source_kind: str) -> str:
    identity = _clean(lecture_key)
    source = next((item for item in GURO_SOURCES if item[0] == source_kind), None)
    if not identity.isdigit() or source is None:
        return ""
    _kind, key, jachi, _group, _label = source
    return f"https://{GURO_HOST}{GURO_DETAIL_PATH}?" + urlencode(
        {
            "key": key,
            "searchLctreKey": identity,
            "searchInsttCode": "",
            "jachi": jachi,
        }
    )


def _declaration(soup: BeautifulSoup) -> Optional[tuple[int, int, int]]:
    match = _DECLARATION_RE.search(_clean(soup.get_text(" ", strip=True)))
    if match is None:
        return None
    return int(match.group(1).replace(",", "")), int(match.group(2)), int(match.group(3))


def _date_range(value: Any) -> tuple[str, str, str]:
    tokens: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            tokens.append(
                date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            )
        except ValueError:
            continue
    if len(tokens) < 2 or tokens[1] < tokens[0]:
        return "", "", ""
    start, end = tokens[:2]
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _status(value: Any) -> str:
    compact = _clean(value).replace(" ", "")
    if "대기접수" in compact:
        return "OPEN"
    if "모집중" in compact:
        return "OPEN"
    if "모집대기" in compact:
        return "SCHEDULED"
    if "모집마감" in compact or "폐강" in compact:
        return "CLOSED"
    return ""


def _capacity(value: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    numbers = [int(token.replace(",", "")) for token in re.findall(r"\d[\d,]*", _clean(value))]
    if len(numbers) < 2:
        return None, None, None
    return numbers[1], numbers[0], numbers[2] if len(numbers) >= 3 else None


def _branch(source_kind: str, venue: str) -> str:
    venue_clean = _clean(venue)
    if source_kind != "resident_center":
        return venue_clean or "구로구 정보화교육"
    venue_compact = venue_clean.replace(" ", "")
    for candidate in GURO_RESIDENT_BRANCHES:
        prefix = candidate.split("동", 1)[0] + "동"
        if prefix.replace(" ", "") in venue_compact:
            return candidate
    return venue_clean or "구로구 자치회관"


def _branch_code(value: str) -> str:
    digest = hashlib.sha1(_clean(value).encode("utf-8")).hexdigest()[:12].upper()
    return f"GURO_BRANCH_{digest}"


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _parse_list_rows(
    target: Any,
    soup: BeautifulSoup,
    source: tuple[str, str, str, str, str],
) -> tuple[list[dict[str, Any]], int]:
    source_kind, key, jachi, _group, source_label = source
    rows: list[dict[str, Any]] = []
    malformed = 0
    for tr in soup.select("table tbody tr"):
        link = tr.select_one("a[href*='edcLctreView.do']")
        if link is None:
            continue
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.find_all("td", recursive=False)]
        parsed = urlparse(urljoin(GURO_URL, _clean(link.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        identity = _clean((query.get("searchLctreKey") or [""])[0])
        title = _clean(link.get_text(" ", strip=True))
        raw_url = guro_detail_url(identity, source_kind=source_kind)
        valid = (
            (parsed.hostname or "").lower() == GURO_HOST
            and parsed.path == GURO_DETAIL_PATH
            and identity.isdigit()
            and len(cells) >= 6
            and bool(title)
            and bool(raw_url)
        )
        if not valid:
            malformed += 1
            continue
        apply_start, apply_end, apply_period = _date_range(cells[3])
        start_date, end_date, period = _date_range(cells[4])
        status = _status(cells[0])
        if not status or not apply_period or not period:
            malformed += 1
            continue
        venue = cells[2]
        branch = _branch(source_kind, venue)
        capacity_current, capacity_total, waitlist_current = _capacity(cells[5])
        row: dict[str, Any] = {
            "provider": _provider(target),
            "provider_course_id": f"{_provider(target)}:edc:{identity}"[:100],
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "program_type": "강좌",
            "category": "교육·강좌",
            "branch": branch,
            "branch_code": _branch_code(branch),
            "branch_url": _source_url(source, 1),
            "preserve_branch": True,
            "raw_url": raw_url,
            "status": status,
            "period": period,
            "start_date": start_date,
            "end_date": end_date,
            "apply_period": apply_period,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "schedule_raw": cells[4],
            "room": venue,
            "venue_name": venue,
            "capacity": cells[5],
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "waitlist_current": waitlist_current,
            "reservation_available": False,
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": GURO_MUNICIPALITY_CODE,
            "municipality_full_name": GURO_MUNICIPALITY_NAME,
            "collection_type": "complete_official_catalogs+detail_html",
            "description": _clean(" ".join(cells)),
            "raw_fields": {
                "parser": GURO_PARSER,
                "lecture_key": identity,
                "source_kind": source_kind,
                "source_label": source_label,
                "source_key": key,
                "source_jachi": jachi,
                "source_status": cells[0],
                "list_cells": cells,
            },
        }
        rows.append(row)
    return rows, malformed


def _pairs(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for tr in soup.select("table tr"):
        pending = ""
        for cell in tr.find_all(["th", "td"], recursive=False):
            if cell.name == "th":
                pending = _clean(cell.get_text(" ", strip=True))
            elif pending:
                value = _clean(cell.get_text(" ", strip=True))
                if pending not in result or not result[pending]:
                    result[pending] = value
                pending = ""
    return result


def _first(pairs: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = _clean(pairs.get(name))
        if value:
            return value
    return ""


def _application_link(soup: BeautifulSoup, row: Mapping[str, Any]) -> str:
    raw = row.get("raw_fields") if isinstance(row.get("raw_fields"), Mapping) else {}
    identity = _clean(raw.get("lecture_key"))
    expected_key = _clean(raw.get("source_key"))
    expected_jachi = _clean(raw.get("source_jachi"))
    expected_path = (
        GURO_INFORMATION_AGREE_PATH
        if _clean(raw.get("source_kind")) == "information"
        else GURO_AGREE_PATH
    )
    for link in soup.select("a[href*='webEdcLctreAgree']"):
        url = urljoin(_clean(row.get("raw_url")), _clean(link.get("href")))
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme.lower() == "https"
            and (parsed.hostname or "").lower() == GURO_HOST
            and parsed.path == expected_path
            and _clean((query.get("key") or [""])[0]) == expected_key
            and _clean((query.get("lctreKey") or [""])[0]) == identity
            and _clean((query.get("jachi") or [""])[0]) == expected_jachi
        ):
            return url
    return ""


def _enrich_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    identity = _clean(row.get("raw_fields", {}).get("lecture_key"))
    pairs = _pairs(soup)
    detail_start, detail_end, detail_period = _date_range(_first(pairs, "교육기간"))
    apply_start, apply_end, apply_period = _date_range(_first(pairs, "신청기간", "접수기간"))
    room = _first(pairs, "강의장소", "교육장소", "장소")
    method = _first(pairs, "수강신청방법", "신청방법", "접수방법")
    missing = [
        name
        for name, value in {
            "education_period": detail_period,
            "application_period": apply_period,
            "room": room,
            "application_method": method,
        }.items()
        if not value
    ]
    if missing:
        errors.append(f"lecture {identity}: missing detail fields {','.join(missing)}")
    if detail_period and detail_period != _clean(row.get("period")):
        errors.append(f"lecture {identity}: detail/list education period mismatch")
    if apply_period and apply_period != _clean(row.get("apply_period")):
        errors.append(f"lecture {identity}: detail/list application period mismatch")

    application_url = _application_link(soup, row)
    if row.get("status") == "OPEN" and not application_url:
        errors.append(f"lecture {identity}: open course has no canonical application link")

    address_match = re.search(r"(?P<address>\d{5}\s+서울특별시\s+구로구\s+.+)$", room)
    venue_address = _clean(address_match.group("address")) if address_match else ""
    venue_name = _clean(room[: address_match.start()]) if address_match else room
    capacity = _first(pairs, "정원", "모집정원")
    capacity_numbers = [int(value.replace(",", "")) for value in re.findall(r"\d[\d,]*", capacity)]
    wait_match = re.search(r"대기(?:자)?\s*(\d[\d,]*)", capacity)
    row.update(
        {
            "period": detail_period or row.get("period"),
            "start_date": detail_start or row.get("start_date"),
            "end_date": detail_end or row.get("end_date"),
            "apply_period": apply_period or row.get("apply_period"),
            "apply_start": apply_start or row.get("apply_start"),
            "apply_end": apply_end or row.get("apply_end"),
            "category": _first(pairs, "강좌영역", "강좌구분") or row.get("category"),
            "schedule_raw": _first(pairs, "강의시간", "교육시간") or row.get("schedule_raw"),
            "application_method_raw": method,
            "selection_method": "선착순" if "선착순" in method else "",
            "fee": _first(pairs, "수강료", "교육비"),
            "room": venue_name,
            "venue_name": venue_name,
            "venue_address": venue_address,
            "target": _first(pairs, "수강대상", "교육대상"),
            "capacity": capacity or row.get("capacity"),
            "capacity_total": capacity_numbers[0] if capacity_numbers else row.get("capacity_total"),
            "waitlist_total": int(wait_match.group(1).replace(",", "")) if wait_match else None,
            "provider_organizer": _first(pairs, "주최", "운영기관"),
            "phone": _first(pairs, "문의", "문의전화"),
            "reservation_available": bool(row.get("status") == "OPEN" and application_url),
        }
    )
    if application_url and row.get("status") == "OPEN":
        row["application_url"] = application_url
        source_status = _clean(row.get("raw_fields", {}).get("source_status")).replace(
            " ", ""
        )
        row["application_type"] = (
            "WAITLIST_APPLY" if "대기접수" in source_status else "ONLINE_RESERVATION"
        )
    else:
        row.pop("application_url", None)
        row["raw_fields"]["clear_application_url"] = True
    row["raw_fields"].update(
        {"detail_pairs": pairs, "canonical_application_link": bool(application_url)}
    )
    return errors


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


def collect_guro_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 6,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete current/future snapshot or fail closed with no rows."""

    if not is_guro_education_target(target):
        return [], GURO_PARSER, _failure("target does not match the canonical Guro provider route")
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], GURO_PARSER, _failure("managed fetcher and session_factory injection are required")
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory
    assert fetcher is not None
    assert session_factory is not None

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    source_totals: dict[str, int] = {}
    source_pages: dict[str, int] = {}
    list_requests = 0
    malformed_count = 0
    source_cap_reached = False

    for source in GURO_SOURCES:
        source_kind = source[0]
        current_session: Any = None
        try:
            current_session = session_factory()
            first = _fetch(fetcher, current_session, _source_url(source, 1), timeout)
            list_requests += 1
            declaration = _declaration(first)
            if declaration is None:
                errors.append(f"{source_kind}: missing or malformed page declaration")
                continue
            total, current_page, pages = declaration
            source_totals[source_kind] = total
            source_pages[source_kind] = pages
            if current_page != 1 or pages < 1:
                errors.append(f"{source_kind}: invalid first-page declaration")
                continue
            if list_requests + pages - 1 > allowed_pages:
                source_cap_reached = True
                errors.append(
                    f"max_pages cap allows {allowed_pages} of {list_requests + pages - 1} required list requests"
                )
                continue
            for page in range(1, pages + 1):
                soup = first if page == 1 else _fetch(
                    fetcher, current_session, _source_url(source, page), timeout
                )
                if page > 1:
                    list_requests += 1
                page_declaration = _declaration(soup)
                if page_declaration != (total, page, pages):
                    errors.append(f"{source_kind}: inconsistent declaration on page {page}")
                    continue
                page_rows, page_malformed = _parse_list_rows(target, soup, source)
                malformed_count += page_malformed
                expected = min(GURO_PAGE_UNIT, max(0, total - (page - 1) * GURO_PAGE_UNIT))
                if len(page_rows) != expected or page_malformed:
                    errors.append(
                        f"{source_kind}: page {page} expected {expected}, parsed {len(page_rows)}, malformed {page_malformed}"
                    )
                rows.extend(page_rows)
        except Exception as exc:
            errors.append(f"{source_kind}: list fetch {type(exc).__name__}")
        finally:
            _close_quietly(current_session)

    identities = [_clean(row.get("provider_course_id")) for row in rows]
    duplicate_count = len(identities) - len(set(identities))
    declared_total = sum(source_totals.values())
    if duplicate_count:
        errors.append(f"{duplicate_count} duplicate lecture identities across official catalogs")
    if len(rows) != declared_total:
        errors.append(f"official catalogs declared {declared_total}, parsed {len(rows)}")

    current_rows: list[dict[str, Any]] = []
    expired_count = 0
    for row in rows:
        try:
            ended = date.fromisoformat(_clean(row.get("end_date")))
        except ValueError:
            errors.append(f"{_clean(row.get('provider_course_id'))}: invalid education end date")
            continue
        if ended < cutoff:
            expired_count += 1
        else:
            current_rows.append(row)

    list_complete = not errors and list_requests == sum(source_pages.values())
    required_details = len(current_rows)
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    allowed_details = max(0, int(detail_limit))
    if allowed_details < required_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} required details"
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
            try:
                soup = _fetch(fetcher, thread_session(), _clean(row.get("raw_url")), timeout)
                return True, _enrich_detail(row, soup)
            except Exception as exc:
                return False, [
                    f"{_clean(row.get('provider_course_id'))}: detail fetch {type(exc).__name__}"
                ]

        detail_attempts = required_details
        workers = min(GURO_MAX_WORKERS, max(1, int(max_workers)), required_details)
        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="guro-detail") as pool:
                results = list(pool.map(enrich, current_rows))
        finally:
            for value in sessions:
                _close_quietly(value)
        detail_pages = sum(success for success, _item_errors in results)
        detail_errors = [error for _success, item_errors in results for error in item_errors]

    errors.extend(detail_errors)
    details_complete = (
        detail_attempts == required_details
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
            errors.append(f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}")
        cleaned = deduped

    snapshot_complete = list_complete and details_complete and not errors
    if not snapshot_complete:
        cleaned = []
    status_counts = Counter(_clean(row.get("status")) for row in current_rows)
    branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
    current_source_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_kind")) for row in current_rows
    )
    meta: dict[str, Any] = {
        "pages": list_requests,
        "list_requests": list_requests,
        "max_pages": allowed_pages,
        "page_unit": GURO_PAGE_UNIT,
        "source_total": declared_total,
        "source_totals": source_totals,
        "source_pages": source_pages,
        "discovered_links": len(set(identities)),
        "malformed_count": malformed_count,
        "duplicate_count": duplicate_count,
        "expired_count": expired_count,
        "current_count": len(current_rows),
        "current_source_counts": dict(current_source_counts),
        "returned_count": len(cleaned),
        "required_detail_count": required_details,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "pagination_detected": declared_total > GURO_PAGE_UNIT,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "status_counts": dict(status_counts),
        "branch_counts": dict(branch_counts),
        "reservation_discovery_links": sum(bool(row.get("application_url")) for row in current_rows),
        "no_current_data": snapshot_complete and not current_rows,
        "no_current_reason": (
            "all official Guro education courses have ended"
            if snapshot_complete and not current_rows
            else ""
        ),
    }
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
    return cleaned, GURO_PARSER, meta


collect_guro_target = collect_guro_education_courses


__all__ = [
    "GURO_AGREE_PATH",
    "GURO_DETAIL_PATH",
    "GURO_HOST",
    "GURO_INFORMATION_AGREE_PATH",
    "GURO_LIST_PATH",
    "GURO_MAX_WORKERS",
    "GURO_MUNICIPALITY_CODE",
    "GURO_MUNICIPALITY_NAME",
    "GURO_PAGE_UNIT",
    "GURO_PARSER",
    "GURO_PROVIDER",
    "GURO_RESIDENT_BRANCHES",
    "GURO_RESIDENT_URL",
    "GURO_SOURCES",
    "GURO_URL",
    "collect_guro_education_courses",
    "collect_guro_target",
    "guro_detail_url",
    "is_guro_education_target",
    "is_target",
]
