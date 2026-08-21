"""Fail-closed collector for Yeongcheon City's official education catalogue.

The public site advertises 1,115 rows / 112 pages, while its actual paged
result set currently contains 195 distinct program IDs on pages 1 through 20.
Pages 21 through 112 and a page-113 sentinel are empty.  The inflated count is
therefore not used as a row-count assertion.  Instead, this collector scans
every advertised page plus the sentinel, requires a contiguous result prefix,
and validates every current/future program against its detail page before it
publishes an all-or-nothing snapshot.

This module deliberately does not import ``Crawler_MunicipalYaml`` so the main
router can import it without a cycle.  Production callers should inject the
managed fetcher and session factory used by that router.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YEONGCHEON_PROVIDER = "MUNI_WWW_YC_GO_KR_54558363"
YEONGCHEON_URL = (
    "https://www.yc.go.kr/edu/portal/academy/lecture/program/list.do?"
    "mId=0303000000"
)
YEONGCHEON_HOST = "www.yc.go.kr"
YEONGCHEON_LIST_PATH = "/edu/portal/academy/lecture/program/list.do"
YEONGCHEON_DETAIL_PATH = "/edu/portal/academy/lecture/program/view.do"
YEONGCHEON_APPLICATION_PATH = (
    "/edu/portal/academy/lecture/program/app/apply.do"
)
YEONGCHEON_MID = "0303000000"
YEONGCHEON_PAGE_SIZE = 10
YEONGCHEON_MAX_WORKERS = 8
YEONGCHEON_FETCH_ATTEMPTS = 3
YEONGCHEON_MUNICIPALITY_CODE = "4723000000"
YEONGCHEON_MUNICIPALITY_NAME = "경상북도 영천시"
YEONGCHEON_LIFELONG_BRANCH = "평생학습관"
YEONGCHEON_LIFELONG_ADDRESS = "경상북도 영천시 최무선로 243"
YEONGCHEON_ACADEMY_SOURCE_BRANCHES = frozenset(
    {
        "건강교육과정",
        "교양문화과정",
        "디지털과정",
        "시민대학",
        "야간교육과정",
        "음악교육과정",
        "창업부업과정",
        "평생학습형일자리연계지원",
    }
)
YEONGCHEON_PARSER = (
    "yeongcheon_advertised_pages_complete_current_future+detail"
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_KEYSET_RE = re.compile(r"\{\s*['\"]idx['\"]\s*:\s*['\"](?P<idx>\d+)['\"]\s*\}")
_TOTAL_RE = re.compile(r"\(\s*전체\s*([\d,]+)\s*건\s*\)")
_PAGE_CALL_RE = re.compile(r"\bgoPage\(\s*(\d+)\s*\)")

_STATUS_MAP = {
    "접수 중": "OPEN",
    "추가 접수": "OPEN",
    "접수 대기": "SCHEDULED",
    "접수 마감": "CLOSED",
    "교육 중": "CLOSED",
    "교육 마감": "CLOSED",
    "폐강": "CANCELLED",
}


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


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_yeongcheon_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == YEONGCHEON_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == YEONGCHEON_HOST
        and parsed.port is None
        and parsed.path == YEONGCHEON_LIST_PATH
        and parse_qs(parsed.query, keep_blank_values=True)
        == {"mId": [YEONGCHEON_MID]}
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_yeongcheon_target


def yeongcheon_list_url(page: Any) -> str:
    raw_page = _clean(page)
    if not _IDENTITY_RE.fullmatch(raw_page) or int(raw_page) < 1:
        return ""
    return f"https://{YEONGCHEON_HOST}{YEONGCHEON_LIST_PATH}?" + urlencode(
        {"mId": YEONGCHEON_MID, "page": int(raw_page)}
    )


def yeongcheon_detail_url(identity: Any) -> str:
    raw_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{YEONGCHEON_HOST}{YEONGCHEON_DETAIL_PATH}?" + urlencode(
        {"mId": YEONGCHEON_MID, "idx": raw_identity}
    )


def yeongcheon_application_url(identity: Any) -> str:
    raw_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{YEONGCHEON_HOST}{YEONGCHEON_APPLICATION_PATH}?" + urlencode(
        {"mId": YEONGCHEON_MID, "programIdx": raw_identity}
    )


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
    content = getattr(value, "content", None)
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


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return result


def _date_range(value: Any) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if len(values) != 2 or values[1] < values[0]:
        return "", "", ""
    start, end = values
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _pairs(container: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if container is None:
        return result
    for row in container.select("tr"):
        pending = ""
        for cell in row.find_all(["th", "td"], recursive=False):
            if cell.name == "th":
                pending = _clean(cell.get_text(" ", strip=True))
            elif pending:
                value = _clean(cell.get_text(" ", strip=True))
                if pending not in result or not result[pending]:
                    result[pending] = value
                pending = ""
    return result


def _labeled_number(value: Any, label: str) -> Optional[int]:
    match = re.search(rf"{re.escape(label)}\s*:?\s*([\d,]+)", _clean(value))
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _branch_code(branch: Any) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"YEONGCHEON_BRANCH_{digest}"


def yeongcheon_physical_location(
    source_branch: Any,
    venue: Any,
) -> tuple[str, str] | None:
    branch = _clean(source_branch)
    venue_text = _clean(venue)
    if branch not in YEONGCHEON_ACADEMY_SOURCE_BRANCHES:
        return None
    if "외부공방" in venue_text and "중앙동3길 88" in venue_text:
        return "외부공방", "경상북도 영천시 중앙동3길 88"
    return YEONGCHEON_LIFELONG_BRANCH, YEONGCHEON_LIFELONG_ADDRESS


def _source_total(soup: BeautifulSoup) -> Optional[int]:
    match = _TOTAL_RE.search(_clean(soup.get_text(" ", strip=True)))
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _advertised_last_page(soup: BeautifulSoup) -> int:
    pages: list[int] = []
    for node in soup.select("[onclick]"):
        pages.extend(int(value) for value in _PAGE_CALL_RE.findall(_clean(node.get("onclick"))))
    return max(pages, default=1)


def _page_number(soup: BeautifulSoup) -> Optional[int]:
    node = soup.select_one("form#list input[name=page]")
    raw = _clean(node.get("value")) if node is not None else ""
    if not _IDENTITY_RE.fullmatch(raw):
        return None
    return int(raw)


def _status_text(card: Any) -> str:
    node = card.select_one("[class^=process]")
    if node is None:
        node = card.select_one("a[data-keyset]")
    return _clean(node.get_text(" ", strip=True)) if node is not None else ""


def _parse_list_page(
    target: Any,
    soup: BeautifulSoup,
    *,
    requested_page: int,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    for card in soup.select(".cardWrap"):
        action = card.select_one("a[data-action][data-keyset]")
        title_node = card.select_one(".title")
        branch_node = card.select_one(".course")
        keyset = _clean(action.get("data-keyset")) if action is not None else ""
        identity_match = _KEYSET_RE.fullmatch(keyset)
        identity = identity_match.group("idx") if identity_match else ""
        expected_action = f"{YEONGCHEON_DETAIL_PATH}?mId={YEONGCHEON_MID}"
        action_path = _clean(action.get("data-action")) if action is not None else ""
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
        branch = _clean(branch_node.get_text(" ", strip=True)) if branch_node else ""
        source_status = _status_text(card)
        table_pairs = _pairs(card)
        start, end, period = _date_range(table_pairs.get("교육기간"))
        apply_start, apply_end, apply_period = _date_range(
            table_pairs.get("접수기간")
        )
        if (
            not identity
            or action_path != expected_action
            or not title
            or source_status not in _STATUS_MAP
            or not start
            or not apply_start
        ):
            malformed += 1
            continue

        capacity_text = table_pairs.get("모집인원", "")
        enrollment_text = table_pairs.get("신청현황", "")
        raw_url = yeongcheon_detail_url(identity)
        row: dict[str, Any] = {
            "provider": _provider(target),
            "provider_course_id": f"{_provider(target)}:program:{identity}"[:100],
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "program_type": "교육·강좌",
            "category": "교육·강좌",
            "branch": (
                branch
                or _clean(_target_value(target, "branch"))
                or YEONGCHEON_MUNICIPALITY_NAME
            ),
            "branch_code": _branch_code(branch or YEONGCHEON_MUNICIPALITY_NAME),
            "branch_url": YEONGCHEON_URL,
            "preserve_branch": True,
            "raw_url": raw_url,
            "status": _STATUS_MAP[source_status],
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "schedule_raw": table_pairs.get("교육일시", ""),
            "target": table_pairs.get("교육대상", ""),
            "fee": table_pairs.get("수강료/재료비") or table_pairs.get("재료비", ""),
            "phone": table_pairs.get("문의처", ""),
            "capacity": capacity_text,
            "capacity_total": _labeled_number(capacity_text, "정원"),
            "capacity_current": _labeled_number(enrollment_text, "신청"),
            "waitlist_total": _labeled_number(capacity_text, "후보자"),
            "waitlist_current": _labeled_number(enrollment_text, "후보자"),
            "reservation_available": False,
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": YEONGCHEON_MUNICIPALITY_CODE,
            "municipality_full_name": YEONGCHEON_MUNICIPALITY_NAME,
            "collection_type": "advertised_pages_complete+detail_html",
            "description": _clean(card.get_text(" ", strip=True)),
            "raw_fields": {
                "parser": YEONGCHEON_PARSER,
                "program_idx": identity,
                "source_page": requested_page,
                "source_status": source_status,
                "source_branch": branch,
                "list_pairs": table_pairs,
            },
        }
        rows.append(row)
    return rows, malformed


def _detail_errors(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("program_idx"))
    errors: list[str] = []
    table = soup.select_one(".tbl-apply table.tbl")
    pairs = _pairs(table)
    required = (
        "강좌명",
        "접수 기간",
        "분류",
        "교육 기간",
        "교육 시간",
        "교육 대상",
        "수강료",
        "모집 인원",
        "신청 현황",
        "강사명",
        "강의 장소",
        "문의 전화",
    )
    missing = [key for key in required if key not in pairs]
    if missing:
        errors.append(f"program {identity}: missing detail keys {','.join(missing)}")

    title_status = _clean(pairs.get("강좌명"))
    source_title = _clean(row.get("title"))
    source_branch = _clean(row.get("raw_fields", {}).get("source_branch"))
    expected_prefix = f"[{source_branch}] " if source_branch else ""
    if source_branch and not title_status.startswith(expected_prefix):
        errors.append(f"program {identity}: detail/list branch mismatch")

    detail_status_node = table.select_one("[class^=process]") if table else None
    detail_status = (
        _clean(detail_status_node.get_text(" ", strip=True))
        if detail_status_node is not None
        else ""
    )
    if detail_status != _clean(row.get("raw_fields", {}).get("source_status")):
        errors.append(f"program {identity}: detail/list status mismatch")
    comparable_title = (
        title_status[len(expected_prefix) :]
        if title_status.startswith(expected_prefix)
        else title_status
    )
    expected_suffix = f" - {detail_status}" if detail_status else ""
    if expected_suffix and comparable_title.endswith(expected_suffix):
        comparable_title = comparable_title[: -len(expected_suffix)].rstrip()
    if comparable_title != source_title:
        errors.append(f"program {identity}: detail/list title mismatch")

    detail_start, detail_end, detail_period = _date_range(pairs.get("교육 기간"))
    detail_apply_start, detail_apply_end, detail_apply_period = _date_range(
        pairs.get("접수 기간")
    )
    if not detail_start or detail_period != _clean(row.get("period")):
        errors.append(f"program {identity}: detail/list education period mismatch")
    if not detail_apply_start or detail_apply_period != _clean(row.get("apply_period")):
        errors.append(f"program {identity}: detail/list application period mismatch")

    form = soup.select_one("form#apply")
    form_action = _clean(form.get("action")) if form is not None else ""
    form_identity_node = form.select_one("input[name=programIdx]") if form else None
    form_identity = _clean(form_identity_node.get("value")) if form_identity_node else ""
    expected_action = f"{YEONGCHEON_APPLICATION_PATH}?mId={YEONGCHEON_MID}"
    if form_action != expected_action or form_identity != identity:
        errors.append(f"program {identity}: malformed application form")
    apply_button = soup.select_one("input[onclick*='document.apply.submit']")
    is_open = _clean(row.get("status")) == "OPEN"
    if bool(apply_button) != is_open:
        errors.append(f"program {identity}: status/application control mismatch")

    detail_capacity = _labeled_number(pairs.get("모집 인원"), "정원")
    detail_current = _labeled_number(pairs.get("신청 현황"), "신청")
    if (
        row.get("capacity_total") is not None
        and detail_capacity != row.get("capacity_total")
    ):
        errors.append(f"program {identity}: detail/list capacity mismatch")
    if (
        row.get("capacity_current") is not None
        and detail_current != row.get("capacity_current")
    ):
        errors.append(f"program {identity}: detail/list enrollment mismatch")

    venue = _clean(pairs.get("강의 장소"))
    physical_location = yeongcheon_physical_location(source_branch, venue)
    if physical_location:
        branch, venue_address = physical_location
    else:
        branch = source_branch or _clean(row.get("branch"))
        venue_address = ""
    description = _clean(
        " ".join(
            value
            for value in (pairs.get("강좌 정보"), pairs.get("유의 사항"))
            if value
        )
    )
    row.update(
        {
            "branch": branch or row.get("branch"),
            "branch_code": _branch_code(branch or row.get("branch")),
            "provider_organizer": source_branch or branch,
            "schedule_raw": _clean(pairs.get("교육 시간")) or row.get("schedule_raw"),
            "target": _clean(pairs.get("교육 대상")) or row.get("target"),
            "fee": _clean(pairs.get("수강료")) or row.get("fee"),
            "material_fee": _clean(pairs.get("재료비")),
            "instructor": _clean(pairs.get("강사명")),
            "room": venue,
            "venue_name": venue,
            "venue_address": venue_address,
            "phone": _clean(pairs.get("문의 전화")) or row.get("phone"),
            "description": description or row.get("description"),
            "reservation_available": bool(is_open and apply_button),
        }
    )
    if is_open and apply_button:
        row["application_url"] = yeongcheon_application_url(identity)
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        row["raw_fields"]["clear_application_url"] = True
    row["raw_fields"].update(
        {
            "detail_pairs": pairs,
            "canonical_application_form": form_action == expected_action,
            "application_control": bool(apply_button),
            "detail_start": detail_start,
            "detail_end": detail_end,
            "detail_apply_start": detail_apply_start,
            "detail_apply_end": detail_apply_end,
        }
    )
    return errors


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
        "list_requests": 0,
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


def _parallel_fetch(
    pages_or_rows: list[tuple[Any, str]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, BeautifulSoup], list[str]]:
    fetched: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
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

    def one(item: tuple[Any, str]) -> tuple[Any, Optional[BeautifulSoup], str]:
        key, url = item
        last_error = ""
        for attempt in range(YEONGCHEON_FETCH_ATTEMPTS):
            try:
                return key, _fetch(fetcher, thread_session(), url, timeout), ""
            except Exception as exc:
                last_error = type(exc).__name__
                if attempt + 1 < YEONGCHEON_FETCH_ATTEMPTS:
                    time.sleep(0.25 * (2**attempt))
        return key, None, f"{key}: fetch {last_error}"

    workers = min(max(1, int(max_workers)), YEONGCHEON_MAX_WORKERS, len(pages_or_rows))
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="yeongcheon") as pool:
            for key, soup, error in pool.map(one, pages_or_rows):
                if soup is not None:
                    fetched[key] = soup
                if error:
                    errors.append(error)
    finally:
        for current in sessions:
            _close_quietly(current)
    return fetched, errors


def collect_yeongcheon_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 120,
    detail_limit: int = 200,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 6,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete current/future Yeongcheon education snapshot."""

    if not is_yeongcheon_target(target):
        return [], YEONGCHEON_PARSER, _failure(
            "target does not match the canonical Yeongcheon provider route"
        )
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], YEONGCHEON_PARSER, _failure(
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
    source_cap_reached = False
    first_session: Any = None
    first_soup: Optional[BeautifulSoup] = None
    try:
        first_session = session_factory()
        last_error = ""
        for attempt in range(YEONGCHEON_FETCH_ATTEMPTS):
            try:
                first_soup = _fetch(
                    fetcher, first_session, yeongcheon_list_url(1), timeout
                )
                break
            except Exception as exc:
                last_error = type(exc).__name__
                if attempt + 1 < YEONGCHEON_FETCH_ATTEMPTS:
                    time.sleep(0.25 * (2**attempt))
        if first_soup is None:
            errors.append(f"page 1: fetch {last_error}")
    finally:
        _close_quietly(first_session)

    if first_soup is None:
        meta = _failure("; ".join(errors))
        return [], YEONGCHEON_PARSER, meta

    declared_total = _source_total(first_soup)
    advertised_last = _advertised_last_page(first_soup)
    if declared_total is None:
        errors.append("page 1: missing advertised source total")
        declared_total = 0
    expected_last = max(1, math.ceil(declared_total / YEONGCHEON_PAGE_SIZE))
    if advertised_last != expected_last:
        errors.append(
            f"page 1: advertised last page {advertised_last} != calculated {expected_last}"
        )
    required_list_requests = advertised_last + 1
    if required_list_requests > allowed_pages:
        source_cap_reached = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of "
            f"{required_list_requests} required list requests"
        )

    page_soups: dict[int, BeautifulSoup] = {1: first_soup}
    list_fetch_errors: list[str] = []
    if not errors:
        requested = [
            (page, yeongcheon_list_url(page))
            for page in range(2, required_list_requests + 1)
        ]
        fetched, list_fetch_errors = _parallel_fetch(
            requested,
            fetcher=fetcher,
            session_factory=session_factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        page_soups.update(fetched)
        errors.extend(list_fetch_errors)

    rows_by_page: dict[int, list[dict[str, Any]]] = {}
    malformed_count = 0
    if not errors:
        for page in range(1, required_list_requests + 1):
            soup = page_soups.get(page)
            if soup is None:
                errors.append(f"page {page}: missing fetched document")
                continue
            if _page_number(soup) != page:
                errors.append(f"page {page}: server returned a different page marker")
            page_total = _source_total(soup)
            if page_total != declared_total:
                errors.append(f"page {page}: advertised total changed during crawl")
            parsed, malformed = _parse_list_page(
                target, soup, requested_page=page
            )
            rows_by_page[page] = parsed
            malformed_count += malformed
            if malformed:
                errors.append(f"page {page}: {malformed} malformed course cards")

    all_rows = [
        row
        for page in range(1, required_list_requests + 1)
        for row in rows_by_page.get(page, [])
    ]
    page_counts = {
        page: len(rows_by_page.get(page, []))
        for page in range(1, required_list_requests + 1)
    }
    nonempty_pages = [page for page, count in page_counts.items() if count]
    last_nonempty = max(nonempty_pages, default=0)
    if nonempty_pages:
        if nonempty_pages != list(range(1, last_nonempty + 1)):
            errors.append("course rows resume after an empty pagination page")
        for page in range(1, last_nonempty):
            if page_counts.get(page) != YEONGCHEON_PAGE_SIZE:
                errors.append(f"page {page}: non-terminal page is not full")
        if not (1 <= page_counts.get(last_nonempty, 0) <= YEONGCHEON_PAGE_SIZE):
            errors.append("last non-empty page has an invalid row count")
    if page_counts.get(required_list_requests, 0):
        errors.append("sentinel page after advertised pagination is not empty")

    identities = [
        _clean(row.get("raw_fields", {}).get("program_idx")) for row in all_rows
    ]
    duplicate_count = len(identities) - len(set(identities))
    if duplicate_count:
        errors.append(f"{duplicate_count} duplicate program identities")
    if declared_total < len(all_rows):
        errors.append(
            f"advertised total {declared_total} is smaller than parsed rows {len(all_rows)}"
        )

    current_rows: list[dict[str, Any]] = []
    expired_count = 0
    empty_current_branch_count = 0
    for row in all_rows:
        try:
            end = date.fromisoformat(_clean(row.get("end_date")))
        except ValueError:
            errors.append(
                f"{_clean(row.get('provider_course_id'))}: invalid education end date"
            )
            continue
        if end < cutoff:
            expired_count += 1
            continue
        if not _clean(row.get("raw_fields", {}).get("source_branch")):
            empty_current_branch_count += 1
            errors.append(
                f"{_clean(row.get('provider_course_id'))}: current course has no source branch"
            )
            continue
        current_rows.append(row)

    list_complete = (
        not errors
        and len(page_soups) == required_list_requests
        and page_counts.get(required_list_requests, 0) == 0
    )
    required_details = len(current_rows)
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    if allowed_details < required_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} required details"
        )
    elif list_complete and current_rows:
        detail_attempts = required_details
        requested_details = [
            (_clean(row["raw_fields"]["program_idx"]), _clean(row.get("raw_url")))
            for row in current_rows
        ]
        detail_soups, detail_fetch_errors = _parallel_fetch(
            requested_details,
            fetcher=fetcher,
            session_factory=session_factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        row_by_identity = {
            _clean(row["raw_fields"]["program_idx"]): row for row in current_rows
        }
        for identity, soup in detail_soups.items():
            detail_pages += 1
            detail_errors.extend(_detail_errors(row_by_identity[identity], soup))

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
            errors.append(
                f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}"
            )
        cleaned = deduped

    snapshot_complete = list_complete and details_complete and not errors
    if not snapshot_complete:
        cleaned = []
    status_counts = Counter(_clean(row.get("status")) for row in current_rows)
    source_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in current_rows
    )
    branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
    source_branch_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_branch"))
        for row in all_rows
        if _clean(row.get("raw_fields", {}).get("source_branch"))
    )
    semantic_signatures = [
        (
            re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(row.get("title"))).lower(),
            _clean(row.get("branch")),
            _clean(row.get("period")),
            _clean(row.get("schedule_raw")),
        )
        for row in current_rows
    ]
    semantic_duplicate_count = len(semantic_signatures) - len(set(semantic_signatures))
    reservation_links = sum(bool(row.get("application_url")) for row in current_rows)
    meta: dict[str, Any] = {
        "pages": len(page_soups),
        "list_requests": len(page_soups),
        "required_list_requests": required_list_requests,
        "max_pages": allowed_pages,
        "page_unit": YEONGCHEON_PAGE_SIZE,
        "advertised_last_page": advertised_last,
        "sentinel_page": required_list_requests,
        "last_nonempty_page": last_nonempty,
        "source_total": declared_total,
        "source_rows": len(all_rows),
        "source_total_mismatch": declared_total - len(all_rows),
        "source_total_consistent": declared_total == len(all_rows),
        "page_counts": page_counts,
        "discovered_links": len(set(identities)),
        "duplicate_count": duplicate_count,
        "malformed_count": malformed_count,
        "expired_count": expired_count,
        "empty_current_branch_count": empty_current_branch_count,
        "current_count": len(current_rows),
        "returned_count": len(cleaned),
        "required_detail_count": required_details,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "pagination_detected": advertised_last > 1,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "status_counts": dict(status_counts),
        "source_status_counts": dict(source_status_counts),
        "branch_count": len(branch_counts),
        "branch_counts": dict(branch_counts),
        "source_branch_count": len(source_branch_counts),
        "source_branch_counts": dict(source_branch_counts),
        "semantic_duplicate_count": semantic_duplicate_count,
        "reservation_discovery_links": reservation_links,
        "no_current_data": snapshot_complete and not current_rows,
        "no_current_reason": (
            "all official Yeongcheon education programs have ended"
            if snapshot_complete and not current_rows
            else ""
        ),
    }
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
    return cleaned, YEONGCHEON_PARSER, meta


collect_yeongcheon_target = collect_yeongcheon_education_courses


__all__ = [
    "YEONGCHEON_APPLICATION_PATH",
    "YEONGCHEON_DETAIL_PATH",
    "YEONGCHEON_FETCH_ATTEMPTS",
    "YEONGCHEON_HOST",
    "YEONGCHEON_LIST_PATH",
    "YEONGCHEON_MAX_WORKERS",
    "YEONGCHEON_MID",
    "YEONGCHEON_MUNICIPALITY_CODE",
    "YEONGCHEON_MUNICIPALITY_NAME",
    "YEONGCHEON_PAGE_SIZE",
    "YEONGCHEON_PARSER",
    "YEONGCHEON_PROVIDER",
    "YEONGCHEON_URL",
    "collect_yeongcheon_education_courses",
    "collect_yeongcheon_target",
    "is_target",
    "is_yeongcheon_target",
    "yeongcheon_application_url",
    "yeongcheon_detail_url",
    "yeongcheon_list_url",
]
