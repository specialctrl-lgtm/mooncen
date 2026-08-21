"""Fail-closed collectors for Yangcheon-gu's official education sources.

This module deliberately has no dependency on ``Crawler_MunicipalYaml`` so it can
be imported there without creating a circular import.  The public dispatcher is
``collect_yangcheon_integrated``; callers may inject their existing session and
BeautifulSoup fetch helpers through ``session_factory`` and ``fetcher``.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import ipaddress
import math
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urldefrag, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YANGCHEON_INTEGRATED_PROVIDER = "MUNI_WWW_YANGCHEON_GO_KR_BF8AB775"
YANGCHEON_INTEGRATED_URL = "https://www.yangcheon.go.kr/reservation/reservation/ex/lecture/List.do"
YANGCHEON_INTEGRATED_HOST = "www.yangcheon.go.kr"
YANGCHEON_INTEGRATED_LIST_PATH = "/reservation/reservation/ex/lecture/List.do"
YANGCHEON_INTEGRATED_DETAIL_PATH = "/reservation/reservation/ex/lecture/View.do"
YANGCHEON_INTEGRATED_PAGE_SIZE = 12
YANGCHEON_INTEGRATED_PARSER = "yangcheon_integrated_current_future+detail"

YANGCHEON_LIFESTUDY_PROVIDER = "MUNI_LIFESTUDY_YANGCHEON_GO_KR_9F2085A4"
YANGCHEON_LIFESTUDY_URL = "https://lifestudy.yangcheon.go.kr/sugang/prgm/lctre/list.do?menuNo=300001"
YANGCHEON_LIFESTUDY_HOST = "lifestudy.yangcheon.go.kr"
YANGCHEON_LIFESTUDY_LIST_PATH = "/sugang/prgm/lctre/list.do"
YANGCHEON_LIFESTUDY_DETAIL_PATH = "/sugang/prgm/lctre/view.do"
YANGCHEON_LIFESTUDY_MENU_NO = "300001"
YANGCHEON_LIFESTUDY_PAGE_SIZE = 40
YANGCHEON_LIFESTUDY_PARSER = "yangcheon_lifestudy_active_states_current_future+detail"
YANGCHEON_LIFESTUDY_STATES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("READY", "SCHEDULED", ("접수예정", "접수대기")),
    ("RECPT_PROGRESS", "OPEN", ("접수중",)),
    ("RECPT_END", "CLOSED", ("접수마감", "접수종료", "접수완료")),
)

YANGCHEON_MUNICIPALITY_CODE = "1147000000"
YANGCHEON_MUNICIPALITY_NAME = "서울특별시 양천구"
YANGCHEON_MAX_DETAIL_WORKERS = 8

YANGCHEON_PROVIDERS = frozenset((YANGCHEON_INTEGRATED_PROVIDER, YANGCHEON_LIFESTUDY_PROVIDER))
YANGCHEON_CANONICAL_URLS = {
    YANGCHEON_INTEGRATED_PROVIDER: YANGCHEON_INTEGRATED_URL,
    YANGCHEON_LIFESTUDY_PROVIDER: YANGCHEON_LIFESTUDY_URL,
}

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(?P<year>\d{2}|\d{4})[.\-/](?P<month>\d{1,2})[.\-/](?P<day>\d{1,2})(?!\d)")
_INTEGRATED_ID_RE = re.compile(r"doLectureUserView\(\s*['\"](?P<id>L\d+)['\"]\s*\)", re.IGNORECASE)
_LIFESTUDY_ID_RE = re.compile(r"fnView\(\s*['\"](?P<id>\d+)['\"]\s*\)", re.IGNORECASE)


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


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


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


def _default_fetcher(current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        raise ValueError("empty HTTP response")
    return BeautifulSoup(response.content, "lxml")


def _fetch(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _today(value: Optional[date | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _date_tokens(value: Any) -> list[date]:
    parsed: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        year = int(match.group("year"))
        if year < 100:
            year += 2000
        try:
            parsed.append(date(year, int(match.group("month")), int(match.group("day"))))
        except ValueError:
            continue
    return parsed


def _date_range(value: Any) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if len(values) < 2:
        return "", "", ""
    start, end = values[0], values[1]
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _stable_branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"YANGCHEON_BRANCH_{digest}"


def _is_public_http_url(value: Any, base_url: str = "") -> str:
    raw = _clean(value)
    if not raw:
        return ""
    candidate = urljoin(base_url, raw)
    candidate, _fragment = urldefrag(candidate)
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return ""
    return candidate


def _pairs_from_table(table: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if table is None:
        return pairs
    for row in table.select("tr"):
        key = ""
        for cell in row.find_all(["th", "td"], recursive=False):
            if cell.name == "th":
                key = _clean(cell.get_text(" ", strip=True))
            elif key:
                pairs[key] = _clean(cell.get_text(" ", strip=True))
                key = ""
    return pairs


def _pairs_from_definition_list(container: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if container is None:
        return pairs
    for group in container.select("dl"):
        key = ""
        for node in group.find_all(["dt", "dd"], recursive=False):
            if node.name == "dt":
                key = _clean(node.get_text(" ", strip=True))
            elif key:
                value = _clean(node.get_text(" ", strip=True))
                if key not in pairs or not pairs[key]:
                    pairs[key] = value
                elif value and value not in pairs[key]:
                    pairs[key] = f"{pairs[key]} / {value}"
                key = ""
    return pairs


def _capacity_slashes(value: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    numbers = [int(token.replace(",", "")) for token in re.findall(r"\d[\d,]*", _clean(value))]
    if len(numbers) < 2:
        return None, None, None
    total = numbers[0]
    if len(numbers) >= 3:
        return numbers[2], total, numbers[1]
    return numbers[0], numbers[1], None


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _comparable_lifestudy_title(value: Any) -> str:
    """Remove the list-only aggregate session suffix used by series courses."""

    return _clean(re.sub(r"\s*총\s*\d[\d,]*\s*차\s*$", "", _clean(value)))


def _base_row(
    target: Any,
    *,
    identity_label: str,
    identity: str,
    title: str,
    branch: str,
    raw_url: str,
    parser: str,
) -> dict[str, Any]:
    provider = _provider(target)
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:{identity_label}:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": _clean(title),
        "branch": _clean(branch),
        "branch_code": _stable_branch_code(branch),
        "preserve_branch": True,
        "branch_url": _target_url(target),
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
        "municipality_code": YANGCHEON_MUNICIPALITY_CODE,
        "municipality_full_name": YANGCHEON_MUNICIPALITY_NAME,
        "raw_fields": {"parser": parser},
    }


def _target_signature(target: Any) -> tuple[str, str]:
    return _provider(target), _target_url(target)


def is_yangcheon_integrated_target(target: Any) -> bool:
    provider, raw_url = _target_signature(target)
    parsed = urlparse(raw_url)
    return (
        provider == YANGCHEON_INTEGRATED_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == YANGCHEON_INTEGRATED_HOST
        and parsed.path == YANGCHEON_INTEGRATED_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def is_yangcheon_lifestudy_target(target: Any) -> bool:
    provider, raw_url = _target_signature(target)
    parsed = urlparse(raw_url)
    return (
        provider == YANGCHEON_LIFESTUDY_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == YANGCHEON_LIFESTUDY_HOST
        and parsed.path == YANGCHEON_LIFESTUDY_LIST_PATH
        and not parsed.params
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True) == {"menuNo": [YANGCHEON_LIFESTUDY_MENU_NO]}
    )


def is_yangcheon_target(target: Any) -> bool:
    """Return whether *target* is one of the two exact provider-owned routes."""

    return is_yangcheon_integrated_target(target) or is_yangcheon_lifestudy_target(target)


# Short alias intended for a parent crawler's dispatch table.
is_target = is_yangcheon_target


def yangcheon_integrated_list_url(page_index: int) -> str:
    return f"{YANGCHEON_INTEGRATED_URL}?{urlencode({'pageIndex': max(1, int(page_index))})}"


def yangcheon_integrated_detail_url(lecture_id: str) -> str:
    identity = _clean(lecture_id)
    if not re.fullmatch(r"L\d+", identity):
        return ""
    return f"https://{YANGCHEON_INTEGRATED_HOST}{YANGCHEON_INTEGRATED_DETAIL_PATH}?{urlencode({'clIdx': identity})}"


def yangcheon_lifestudy_list_url(state_code: str, page_index: int) -> str:
    query = urlencode(
        (
            ("menuNo", YANGCHEON_LIFESTUDY_MENU_NO),
            ("searchSttusCdArr", _clean(state_code)),
            ("pageUnit", str(YANGCHEON_LIFESTUDY_PAGE_SIZE)),
            ("pageIndex", str(max(1, int(page_index)))),
        )
    )
    return f"https://{YANGCHEON_LIFESTUDY_HOST}{YANGCHEON_LIFESTUDY_LIST_PATH}?{query}"


def yangcheon_lifestudy_detail_url(lecture_id: str) -> str:
    identity = _clean(lecture_id)
    if not identity.isdigit():
        return ""
    query = urlencode((("menuNo", YANGCHEON_LIFESTUDY_MENU_NO), ("lctreNo", identity)))
    return f"https://{YANGCHEON_LIFESTUDY_HOST}{YANGCHEON_LIFESTUDY_DETAIL_PATH}?{query}"


def _integrated_page_declaration(soup: BeautifulSoup) -> tuple[Optional[int], Optional[int]]:
    current_node = soup.select_one(".pagination_wrap .page_on")
    current_text = _clean(current_node.get_text(" ", strip=True) if current_node else "")
    pages: list[int] = []
    for node in soup.select(".pagination_wrap .page_no"):
        text = _clean(node.get_text(" ", strip=True))
        if text.isdigit():
            pages.append(int(text))
        match = re.search(r"doLectureUserPag\(\s*(\d+)\s*\)", _clean(node.get("onclick")))
        if match:
            pages.append(int(match.group(1)))
    if not current_text.isdigit() or not pages:
        return None, None
    return int(current_text), max(pages)


def _integrated_list_rows(
    target: Any,
    soup: BeautifulSoup,
    *,
    page_index: int,
) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    exposed = 0
    body = soup.select_one("table.table_list tbody")
    if body is None:
        return rows, 1, exposed
    for tr in body.find_all("tr", recursive=False):
        text = _clean(tr.get_text(" ", strip=True))
        if not text or any(token in text for token in ("등록된 강좌가 없습니다", "조회된 자료가 없습니다")):
            continue
        exposed += 1
        identity_match = _INTEGRATED_ID_RE.search(_clean(tr.get("onclick")))
        title_node = tr.select_one("td.edu-subj")
        branch_node = tr.select_one("td.dong")
        number_node = tr.select_one("td.num")
        date_nodes = tr.select("td.edu-date span")
        status_node = tr.select_one("td.state")
        identity = identity_match.group("id") if identity_match else ""
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        branch = _clean(branch_node.get_text(" ", strip=True) if branch_node else "")
        number_text = _clean(number_node.get_text(" ", strip=True) if number_node else "")
        status_raw = _clean(status_node.get_text(" ", strip=True) if status_node else "")
        apply_raw = next(
            (_clean(node.get_text(" ", strip=True)) for node in date_nodes if "접수" in node.get_text()),
            "",
        )
        period_raw = next(
            (_clean(node.get_text(" ", strip=True)) for node in date_nodes if "교육" in node.get_text()),
            "",
        )
        start_date, end_date, period = _date_range(period_raw)
        apply_start, apply_end, apply_period = _date_range(apply_raw)
        status = {
            "접수중": "OPEN",
            "접수대기": "SCHEDULED",
            "접수예정": "SCHEDULED",
            "접수마감": "CLOSED",
            "접수종료": "CLOSED",
        }.get(status_raw, "")
        detail_url = yangcheon_integrated_detail_url(identity)
        if (
            not identity
            or not title
            or not branch
            or not number_text.isdigit()
            or not start_date
            or not end_date
            or not apply_start
            or not apply_end
            or not status
            or not detail_url
        ):
            invalid += 1
            continue
        capacity_raw = _clean(
            tr.select_one("td.people").get_text(" ", strip=True) if tr.select_one("td.people") else ""
        )
        capacity_current, capacity_total, waitlist_total = _capacity_slashes(capacity_raw)
        schedule = _clean(
            tr.select_one("td.conf-date").get_text(" ", strip=True) if tr.select_one("td.conf-date") else ""
        )
        selection = _clean(tr.select_one("td.method").get_text(" ", strip=True) if tr.select_one("td.method") else "")
        row = _base_row(
            target,
            identity_label="lecture",
            identity=identity,
            title=title,
            branch=branch,
            raw_url=detail_url,
            parser=YANGCHEON_INTEGRATED_PARSER,
        )
        row.update(
            {
                "status": status,
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
                "apply_period": apply_period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": schedule,
                "selection_method_raw": selection,
                "capacity": capacity_raw,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "waitlist_total": waitlist_total,
                "description": text,
                "collection_type": "complete_numbered_pages+detail_html",
            }
        )
        row["raw_fields"].update(
            {
                "lecture_id": identity,
                "list_number": int(number_text),
                "source_status": status_raw,
                "page_index": page_index,
                "list_period_raw": period_raw,
                "list_apply_period_raw": apply_raw,
            }
        )
        rows.append(row)
    return rows, invalid, exposed


def _integrated_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    fields = _pairs_from_table(soup.select_one("table.common-table"))
    required = (
        "교육기관",
        "교육기간",
        "교육요일",
        "수강료",
        "교육장소",
        "접수기간",
        "접수방법",
        "신청방법",
        "모집인원",
        "전화문의",
    )
    missing = [key for key in required if not _clean(fields.get(key))]
    identity = _clean(row.get("raw_fields", {}).get("lecture_id"))
    if missing:
        errors.append(f"lecture {identity}: missing detail fields {','.join(missing)}")
    title_node = soup.select_one("meta#mtTitle[content], meta[property='og:title'][content]")
    detail_title = _clean(title_node.get("content") if title_node else "")
    if not detail_title or detail_title != _clean(row.get("title")):
        errors.append(f"lecture {identity}: detail/list title mismatch")
    branch = _clean(fields.get("교육기관"))
    if not branch or branch != _clean(row.get("branch")):
        errors.append(f"lecture {identity}: detail/list institution mismatch")
    detail_start, detail_end, detail_period = _date_range(fields.get("교육기간"))
    apply_start, apply_end, apply_period = _date_range(fields.get("접수기간"))
    if not detail_period or detail_period != _clean(row.get("period")):
        errors.append(f"lecture {identity}: detail/list education period mismatch")
    if not apply_period or apply_period != _clean(row.get("apply_period")):
        errors.append(f"lecture {identity}: detail/list reception period mismatch")

    reservation_control = None
    for candidate in soup.select("a.submit-btn[onclick]"):
        match = re.search(r"doMemberForm\(\s*['\"](L\d+)['\"]", _clean(candidate.get("onclick")))
        if match and match.group(1) == identity:
            reservation_control = candidate
            break
    is_open = _clean(row.get("status")) == "OPEN"
    application_method = _clean(fields.get("접수방법"))
    accepts_online = "온라인" in application_method
    accepts_offline = any(token in application_method for token in ("전화", "현장", "방문", "우편", "팩스"))
    separate_site = "별도사이트" in application_method or "별도 사이트" in application_method
    if is_open and accepts_online and reservation_control is None:
        errors.append(f"lecture {identity}: open online course has no reservation control")
    if is_open and not any((accepts_online, accepts_offline, separate_site)):
        errors.append(f"lecture {identity}: open course reception method could not be classified")

    row.update(
        {
            "branch": branch or row.get("branch"),
            "branch_code": _stable_branch_code(branch or _clean(row.get("branch"))),
            "period": detail_period or row.get("period"),
            "start_date": detail_start or row.get("start_date"),
            "end_date": detail_end or row.get("end_date"),
            "apply_period": apply_period or row.get("apply_period"),
            "apply_start": apply_start or row.get("apply_start"),
            "apply_end": apply_end or row.get("apply_end"),
            "schedule_raw": _clean(fields.get("교육요일")) or row.get("schedule_raw"),
            "fee": _clean(fields.get("수강료")),
            "room": _clean(fields.get("교육장소")),
            "venue_name": _clean(fields.get("교육장소")),
            "instructor": _clean(fields.get("강사명")),
            "phone": _clean(fields.get("전화문의")),
            "contact": _clean(fields.get("전화문의")),
            "application_method_raw": application_method,
            "selection_method_raw": _clean(fields.get("신청방법")),
            "capacity": _clean(fields.get("모집인원")) or row.get("capacity"),
            "reservation_available": bool(is_open and reservation_control is not None),
        }
    )
    description_node = soup.select_one(".view-detail")
    description = _clean(description_node.get_text(" ", strip=True) if description_node else "")
    if description:
        row["description"] = description
    if is_open and reservation_control is not None:
        row["application_url"] = _clean(row.get("raw_url"))
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        row["raw_fields"]["clear_application_url"] = True
        if is_open and accepts_offline:
            row["application_type"] = "OFFLINE_APPLY"
        elif is_open and separate_site:
            row["application_type"] = "INFO_ONLY"
    row["raw_fields"].update({"detail_pairs": fields, "reservation_control": bool(reservation_control)})
    return errors


def _parallel_details(
    rows: list[dict[str, Any]],
    *,
    timeout: int,
    detail_limit: int,
    max_workers: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    parser: Callable[[dict[str, Any], BeautifulSoup], list[str]],
) -> tuple[int, int, int, list[str], bool]:
    required_rows = [row for row in rows if row.get("raw_fields", {}).get("detail_required", True) is not False]
    allowed = max(0, int(detail_limit))
    selected = required_rows[:allowed]
    capped = allowed < len(required_rows)
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def current_session() -> Any:
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
            soup = _fetch(fetcher, current_session(), _clean(row.get("raw_url")), timeout)
            return True, parser(row, soup)
        except Exception as exc:
            return False, [f"{identity}: detail fetch {type(exc).__name__}"]

    results: list[tuple[bool, list[str]]] = []
    try:
        if selected:
            workers = min(YANGCHEON_MAX_DETAIL_WORKERS, max(1, int(max_workers)), len(selected))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="yangcheon-detail") as pool:
                results = list(pool.map(enrich, selected))
    finally:
        for value in sessions:
            _close_quietly(value)
    detail_pages = sum(success for success, _errors in results)
    errors = [error for _success, item_errors in results for error in item_errors]
    return len(required_rows), len(selected), detail_pages, errors, capped


def _finish_meta(
    *,
    rows: list[dict[str, Any]],
    pages: int,
    list_complete: bool,
    detail_required_count: int,
    detail_attempts: int,
    detail_pages: int,
    detail_errors: list[str],
    source_cap_reached: bool,
    errors: list[str],
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    details_complete = (
        detail_attempts == detail_required_count and detail_pages == detail_required_count and not detail_errors
    )
    all_errors = list(dict.fromkeys([*errors, *detail_errors]))
    snapshot_complete = list_complete and details_complete and not all_errors
    no_current_data = snapshot_complete and not rows
    meta: dict[str, Any] = {
        "pages": pages,
        "detail_pages": detail_pages,
        "detail_attempts": detail_attempts,
        "detail_required_count": detail_required_count,
        "required_detail_count": detail_required_count,
        "detail_exempt_count": max(0, len(rows) - detail_required_count),
        "detail_errors": len(detail_errors),
        "pagination_detected": pages > 1,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "current_count": len(rows),
        "no_current_data": no_current_data,
        "no_current_reason": "official current/future education list is empty" if no_current_data else "",
        "branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
    }
    if extra:
        meta.update(extra)
    if all_errors:
        meta["configured_collection_error"] = "; ".join(all_errors)
    return meta


def collect_yangcheon_portal(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 2000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | str] = None,
    max_workers: int = 6,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the numbered Yangcheon integrated-reservation lecture list."""

    errors: list[str] = []
    if not is_yangcheon_integrated_target(target):
        errors.append("target does not match the canonical Yangcheon integrated source")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    listed: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages = 0
    invalid = 0
    duplicates = 0
    exposed_total = 0
    expired_count = 0
    declared_total = 0
    declared_pages = 0
    source_cap_reached = False
    primary_session: Any = None

    try:
        if not errors:
            primary_session = make_session()
            try:
                first_soup = _fetch(fetch, primary_session, yangcheon_integrated_list_url(1), timeout)
            except Exception as exc:
                errors.append(f"page 1 fetch {type(exc).__name__}")
                first_soup = None
            if first_soup is not None:
                declaration = _integrated_page_declaration(first_soup)
                first_rows, first_invalid, first_exposed = _integrated_list_rows(target, first_soup, page_index=1)
                invalid += first_invalid
                numbers = [row["raw_fields"]["list_number"] for row in first_rows]
                declared_total = max(numbers, default=0)
                if declaration[0] != 1 or declaration[1] is None:
                    errors.append("page 1 pagination declaration is missing or malformed")
                    declared_pages = 1
                else:
                    declared_pages = int(declaration[1])
                expected_pages = max(1, math.ceil(declared_total / YANGCHEON_INTEGRATED_PAGE_SIZE))
                if declared_pages != expected_pages:
                    errors.append(
                        f"paginator declares {declared_pages} pages but numbered total {declared_total} requires {expected_pages}"
                    )
                allowed_pages = max(1, int(max_pages))
                if declared_pages > allowed_pages:
                    source_cap_reached = True
                    errors.append(f"max_pages cap reached after {allowed_pages} of {declared_pages} declared pages")
                for page_index in range(1, min(declared_pages, allowed_pages) + 1):
                    if page_index == 1:
                        soup = first_soup
                        page_rows, page_invalid, page_exposed = (
                            first_rows,
                            first_invalid,
                            first_exposed,
                        )
                    else:
                        try:
                            soup = _fetch(
                                fetch,
                                primary_session,
                                yangcheon_integrated_list_url(page_index),
                                timeout,
                            )
                        except Exception as exc:
                            errors.append(f"page {page_index} fetch {type(exc).__name__}")
                            break
                        page_rows, page_invalid, page_exposed = _integrated_list_rows(
                            target, soup, page_index=page_index
                        )
                        invalid += page_invalid
                    pages += 1
                    exposed_total += page_exposed
                    if _integrated_page_declaration(soup) != (page_index, declared_pages):
                        errors.append(f"page {page_index} pagination declaration changed")
                    expected_rows = min(
                        YANGCHEON_INTEGRATED_PAGE_SIZE,
                        max(0, declared_total - (page_index - 1) * YANGCHEON_INTEGRATED_PAGE_SIZE),
                    )
                    if page_exposed != expected_rows:
                        errors.append(f"page {page_index} exposed {page_exposed} rows; expected {expected_rows}")
                    for row in page_rows:
                        identity = _clean(row["raw_fields"].get("lecture_id"))
                        if identity in seen:
                            duplicates += 1
                            continue
                        seen.add(identity)
                        if date.fromisoformat(_clean(row["end_date"])) < cutoff:
                            expired_count += 1
                            continue
                        listed.append(row)
    finally:
        _close_quietly(primary_session)

    if invalid:
        errors.append(f"{invalid} integrated list rows were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate lecture IDs crossed pages")
    if declared_total != len(seen):
        errors.append(f"declared total {declared_total} does not match {len(seen)} unique lectures")
    list_complete = (
        not errors and pages == declared_pages and exposed_total == declared_total and not invalid and not duplicates
    )

    (
        detail_required_count,
        detail_attempts,
        detail_pages,
        detail_errors,
        detail_capped,
    ) = _parallel_details(
        listed,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
        parser=_integrated_detail,
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(f"detail_limit cap allows {detail_attempts} of {detail_required_count} required detail pages")
    rows = [_clean_row(row) for row in listed]
    if dedupe_rows is not None:
        rows = list(dedupe_rows(rows))
    meta = _finish_meta(
        rows=rows,
        pages=pages,
        list_complete=list_complete,
        detail_required_count=detail_required_count,
        detail_attempts=detail_attempts,
        detail_pages=detail_pages,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
        extra={
            "total_pages": declared_pages,
            "total_count": declared_total,
            "discovered_links": len(seen),
            "exposed_rows": exposed_total,
            "expired_count": expired_count,
            "invalid_count": invalid,
            "duplicate_count": duplicates,
        },
    )
    return rows, YANGCHEON_INTEGRATED_PARSER, meta


def _lifestudy_page_declaration(
    soup: BeautifulSoup,
) -> Optional[tuple[int, int, int]]:
    node = soup.select_one(".board_total")
    text = _clean(node.get_text(" ", strip=True) if node else "")
    match = re.search(r"총\s*([\d,]+)\s*건\s*\(\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\)", text)
    if not match:
        return None
    return (
        int(match.group(1).replace(",", "")),
        int(match.group(2)),
        int(match.group(3)),
    )


def _lifestudy_list_rows(
    target: Any,
    soup: BeautifulSoup,
    *,
    state_code: str,
    normalized_status: str,
    source_statuses: Iterable[str],
    page_index: int,
) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    cards = list(soup.select(".course_card"))
    accepted_statuses = {_clean(value) for value in source_statuses}
    for card in cards:
        identity_control = card.select_one("[onclick*='fnView']")
        identity_match = _LIFESTUDY_ID_RE.search(_clean(identity_control.get("onclick") if identity_control else ""))
        identity = identity_match.group("id") if identity_match else ""
        title_link = card.select_one(".course_title a")
        title = _clean(title_link.get_text(" ", strip=True) if title_link else "")
        branch_node = card.select_one(".course_institute")
        branch = _clean(branch_node.get_text(" ", strip=True) if branch_node else "")
        status_node = card.select_one(".course_status .status_label")
        source_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
        info_node = card.select_one(".course_info")
        info_text = _clean(info_node.get_text(" ", strip=True) if info_node else "")
        start_date, end_date, period = _date_range(info_text)
        apply_node = card.select_one(".course_status .status_period")
        apply_raw = _clean(apply_node.get_text(" ", strip=True) if apply_node else "")
        apply_start, apply_end, apply_period = _date_range(apply_raw)
        internal_detail_url = yangcheon_lifestudy_detail_url(identity)
        title_href = _clean(title_link.get("href") if title_link else "")
        external_detail_url = (
            _is_public_http_url(title_href, YANGCHEON_LIFESTUDY_URL)
            if title_href and not title_href.startswith("#")
            else ""
        )
        detail_url = internal_detail_url
        if (
            not identity
            or not title
            or not branch
            or source_status not in accepted_statuses
            or not start_date
            or not end_date
            or not apply_start
            or not apply_end
            or not detail_url
            or (title_href and not external_detail_url)
        ):
            invalid += 1
            continue
        category_node = card.select_one(".course_tag")
        category = _clean(category_node.get_text(" ", strip=True) if category_node else "")
        order_nodes = card.select(".teg_order_list > span")
        order_values = [_clean(node.get_text(" ", strip=True)) for node in order_nodes]
        fee = order_values[0] if order_values else ""
        application_method = order_values[1] if len(order_values) > 1 else ""
        schedule = " / ".join(value for value in order_values[2:] if value)
        capacity_current: Optional[int] = None
        capacity_total: Optional[int] = None
        waitlist_current: Optional[int] = None
        capacity_match = re.search(
            r"접수인원\s*[:：]\s*(\d[\d,]*)\s*/\s*(\d[\d,]*)\s*명(?:\s*대기\s*(\d[\d,]*)\s*명)?",
            info_text,
        )
        if capacity_match:
            capacity_current = int(capacity_match.group(1).replace(",", ""))
            capacity_total = int(capacity_match.group(2).replace(",", ""))
            if capacity_match.group(3):
                waitlist_current = int(capacity_match.group(3).replace(",", ""))
        row = _base_row(
            target,
            identity_label="lecture",
            identity=identity,
            title=title,
            branch=branch,
            raw_url=detail_url,
            parser=YANGCHEON_LIFESTUDY_PARSER,
        )
        row.update(
            {
                "category": category or "교육·강좌",
                "status": normalized_status,
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
                "apply_period": apply_period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": schedule,
                "fee": fee,
                "application_method_raw": application_method,
                "capacity": info_text,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "waitlist_current": waitlist_current,
                "description": _clean(card.get_text(" ", strip=True)),
                "collection_type": "status_filtered_declared_pages+detail_html",
            }
        )
        row["raw_fields"].update(
            {
                "lecture_id": identity,
                "state_code": state_code,
                "source_status": source_status,
                "page_index": page_index,
                "list_period_raw": info_text,
                "list_apply_period_raw": apply_raw,
                "list_title": title,
                "detail_required": not bool(external_detail_url),
                "internal_detail_url": internal_detail_url,
                "external_detail_url": external_detail_url,
            }
        )
        if normalized_status == "OPEN" and external_detail_url:
            row["application_url"] = external_detail_url
            row["application_type"] = "EXTERNAL_RESERVATION"
            row["reservation_available"] = True
        rows.append(row)
    return rows, invalid, len(cards)


def _lifestudy_application_url(soup: BeautifulSoup, detail_url: str) -> tuple[str, bool]:
    control = soup.select_one(".view-hgroup .title-btn-set a.b-save")
    if control is None:
        return "", False
    href = _clean(control.get("href"))
    if href and not href.startswith(("#", "javascript:")):
        safe = _is_public_http_url(href, detail_url)
        return safe, bool(safe)
    text = _clean(control.get_text(" ", strip=True))
    onclick = _clean(control.get("onclick"))
    if any(token in text for token in ("예약", "신청")) and onclick:
        return detail_url, True
    return "", False


def _lifestudy_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    identity = _clean(row.get("raw_fields", {}).get("lecture_id"))
    title_node = soup.select_one(".view-hgroup__title")
    detail_title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if not detail_title or _comparable_lifestudy_title(detail_title) != _comparable_lifestudy_title(row.get("title")):
        errors.append(f"lecture {identity}: detail/list title mismatch")
    elif detail_title:
        row["title"] = detail_title
    fields = _pairs_from_definition_list(soup.select_one(".bd-view"))
    required = ("기관", "교육기간", "대상", "수강료")
    missing = [key for key in required if not _clean(fields.get(key))]
    if missing:
        errors.append(f"lecture {identity}: missing detail fields {','.join(missing)}")
    branch = _clean(fields.get("기관"))
    if not branch or branch != _clean(row.get("branch")):
        errors.append(f"lecture {identity}: detail/list institution mismatch")
    detail_start, detail_end, detail_period = _date_range(fields.get("교육기간"))
    if not detail_period or detail_period != _clean(row.get("period")):
        errors.append(f"lecture {identity}: detail/list education period mismatch")

    application_url, has_application_control = _lifestudy_application_url(soup, _clean(row.get("raw_url")))
    is_open = _clean(row.get("status")) == "OPEN"
    unsafe_control = soup.select_one(".view-hgroup .title-btn-set a.b-save") is not None and not has_application_control
    if is_open and unsafe_control:
        errors.append(f"lecture {identity}: application control has no safe http(s) destination")

    application_method = _clean(fields.get("모집방법/접수방법")) or _clean(row.get("application_method_raw"))
    accepts_offline = any(token in application_method for token in ("전화", "현장", "방문", "우편", "팩스"))

    general_apply = _clean(fields.get("일반접수기간"))
    priority_apply = _clean(fields.get("우선접수기간"))
    apply_source = general_apply or priority_apply
    apply_start, apply_end, apply_period = _date_range(apply_source)
    schedule = _clean(fields.get("교육요일 및 교육시간 안내")) or _clean(fields.get("교육요일"))
    venue = _clean(fields.get("장소"))
    venue_address = _clean(fields.get("주소"))
    row.update(
        {
            "branch": branch or row.get("branch"),
            "branch_code": _stable_branch_code(branch or _clean(row.get("branch"))),
            "category": _clean(fields.get("분야")) or _clean(fields.get("분류")) or row.get("category"),
            "period": detail_period or row.get("period"),
            "start_date": detail_start or row.get("start_date"),
            "end_date": detail_end or row.get("end_date"),
            "schedule_raw": schedule or row.get("schedule_raw"),
            "target": _clean(fields.get("대상")),
            "eligibility_raw": _clean(fields.get("대상")),
            "fee": _clean(fields.get("수강료")) or row.get("fee"),
            "material_fee": _clean(fields.get("재료비")),
            "capacity": _clean(fields.get("총접수인원")) or row.get("capacity"),
            "application_method_raw": application_method,
            "room": venue,
            "venue_name": venue,
            "venue_address": venue_address,
            "address": venue_address,
            "instructor": _clean(fields.get("강사")),
            "phone": _clean(fields.get("문의전화")),
            "contact": _clean(fields.get("문의전화")),
            "description": _clean(fields.get("강좌소개")) or row.get("description"),
            "reservation_available": bool(is_open and application_url),
        }
    )
    if apply_period:
        row.update(
            {
                "apply_period": apply_period,
                "apply_start": apply_start,
                "apply_end": apply_end,
            }
        )
    if is_open and application_url:
        row["application_url"] = application_url
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        row["raw_fields"]["clear_application_url"] = True
        if is_open and accepts_offline:
            row["application_type"] = "OFFLINE_APPLY"
        elif is_open:
            row["application_type"] = "INFO_ONLY"
    row["raw_fields"].update(
        {
            "detail_pairs": fields,
            "detail_status": _clean(
                soup.select_one(".view-hgroup__cate").get_text(" ", strip=True)
                if soup.select_one(".view-hgroup__cate")
                else ""
            ),
            "application_endpoint": application_url,
        }
    )
    return errors


def collect_yangcheon_lifestudy(
    target: Any,
    timeout: int = 20,
    max_pages: int = 30,
    detail_limit: int = 2000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | str] = None,
    max_workers: int = 6,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect READY/RECPT_PROGRESS/RECPT_END from Yangcheon Lifelong Study."""

    errors: list[str] = []
    if not is_yangcheon_lifestudy_target(target):
        errors.append("target does not match the canonical Yangcheon lifestudy source")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    listed: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages = 0
    invalid = 0
    duplicates = 0
    expired_count = 0
    source_cap_reached = False
    declared_totals: dict[str, int] = {}
    declared_pages_by_status: dict[str, int] = {}
    status_pages: dict[str, int] = {}
    status_complete: dict[str, bool] = {}
    primary_session: Any = None

    try:
        if not errors:
            primary_session = make_session()
            for state_code, normalized_status, source_statuses in YANGCHEON_LIFESTUDY_STATES:
                state_error_count = len(errors)
                state_seen: set[str] = set()
                state_exposed = 0
                state_pages_fetched = 0
                try:
                    first_soup = _fetch(
                        fetch,
                        primary_session,
                        yangcheon_lifestudy_list_url(state_code, 1),
                        timeout,
                    )
                except Exception as exc:
                    errors.append(f"{state_code} page 1 fetch {type(exc).__name__}")
                    declared_totals[state_code] = 0
                    declared_pages_by_status[state_code] = 0
                    status_pages[state_code] = 0
                    status_complete[state_code] = False
                    continue
                declaration = _lifestudy_page_declaration(first_soup)
                if declaration is None:
                    errors.append(f"{state_code} .board_total declaration is missing")
                    declared_total, current_page, declared_pages = 0, 1, 1
                else:
                    declared_total, current_page, declared_pages = declaration
                    if current_page != 1:
                        errors.append(f"{state_code} first page declares current page {current_page}")
                expected_pages = max(1, math.ceil(declared_total / YANGCHEON_LIFESTUDY_PAGE_SIZE))
                if declared_pages != expected_pages:
                    errors.append(
                        f"{state_code} declares {declared_pages} pages for {declared_total} rows; expected {expected_pages}"
                    )
                allowed_pages = max(1, int(max_pages))
                if declared_pages > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"{state_code} max_pages cap reached after {allowed_pages} of {declared_pages} declared pages"
                    )
                for page_index in range(1, min(declared_pages, allowed_pages) + 1):
                    if page_index == 1:
                        soup = first_soup
                    else:
                        try:
                            soup = _fetch(
                                fetch,
                                primary_session,
                                yangcheon_lifestudy_list_url(state_code, page_index),
                                timeout,
                            )
                        except Exception as exc:
                            errors.append(f"{state_code} page {page_index} fetch {type(exc).__name__}")
                            break
                    pages += 1
                    state_pages_fetched += 1
                    if _lifestudy_page_declaration(soup) != (
                        declared_total,
                        page_index,
                        declared_pages,
                    ):
                        errors.append(f"{state_code} page {page_index} .board_total declaration changed")
                    hidden = soup.select_one("input[name='searchSttusCdArr']")
                    if _clean(hidden.get("value") if hidden else "") != state_code:
                        errors.append(f"{state_code} page {page_index} filter marker changed")
                    page_rows, page_invalid, page_exposed = _lifestudy_list_rows(
                        target,
                        soup,
                        state_code=state_code,
                        normalized_status=normalized_status,
                        source_statuses=source_statuses,
                        page_index=page_index,
                    )
                    invalid += page_invalid
                    state_exposed += page_exposed
                    expected_rows = min(
                        YANGCHEON_LIFESTUDY_PAGE_SIZE,
                        max(
                            0,
                            declared_total - (page_index - 1) * YANGCHEON_LIFESTUDY_PAGE_SIZE,
                        ),
                    )
                    if page_exposed != expected_rows:
                        errors.append(
                            f"{state_code} page {page_index} exposed {page_exposed} rows; expected {expected_rows}"
                        )
                    for row in page_rows:
                        identity = _clean(row["raw_fields"].get("lecture_id"))
                        if identity in seen:
                            duplicates += 1
                            continue
                        seen.add(identity)
                        state_seen.add(identity)
                        if date.fromisoformat(_clean(row["end_date"])) < cutoff:
                            expired_count += 1
                            continue
                        listed.append(row)
                if len(state_seen) != declared_total:
                    errors.append(
                        f"{state_code} declared total {declared_total} does not match {len(state_seen)} unique lectures"
                    )
                declared_totals[state_code] = declared_total
                declared_pages_by_status[state_code] = declared_pages
                status_pages[state_code] = state_pages_fetched
                status_complete[state_code] = (
                    len(errors) == state_error_count
                    and state_pages_fetched == declared_pages
                    and state_exposed == declared_total
                    and len(state_seen) == declared_total
                )
    finally:
        _close_quietly(primary_session)

    if invalid:
        errors.append(f"{invalid} lifestudy course cards were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate lecture IDs crossed status/page boundaries")
    list_complete = (
        bool(status_complete) and all(status_complete.values()) and not errors and not invalid and not duplicates
    )
    (
        detail_required_count,
        detail_attempts,
        detail_pages,
        detail_errors,
        detail_capped,
    ) = _parallel_details(
        listed,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
        parser=_lifestudy_detail,
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(f"detail_limit cap allows {detail_attempts} of {detail_required_count} required detail pages")
    rows = [_clean_row(row) for row in listed]
    if dedupe_rows is not None:
        rows = list(dedupe_rows(rows))
    meta = _finish_meta(
        rows=rows,
        pages=pages,
        list_complete=list_complete,
        detail_required_count=detail_required_count,
        detail_attempts=detail_attempts,
        detail_pages=detail_pages,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
        extra={
            "status_pages": status_pages,
            "declared_pages_by_status": declared_pages_by_status,
            "declared_totals_by_status": declared_totals,
            "status_complete": status_complete,
            "discovered_links": len(seen),
            "expired_count": expired_count,
            "invalid_count": invalid,
            "duplicate_count": duplicates,
        },
    )
    return rows, YANGCHEON_LIFESTUDY_PARSER, meta


def collect_yangcheon_integrated(
    target: Any,
    timeout: int = 20,
    max_pages: int = 30,
    detail_limit: int = 2000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | str] = None,
    max_workers: int = 6,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Dispatch an exact Yangcheon provider target to its official collector."""

    common = {
        "timeout": timeout,
        "max_pages": max_pages,
        "detail_limit": detail_limit,
        "fetcher": fetcher,
        "session_factory": session_factory,
        "today": today,
        "max_workers": max_workers,
        "dedupe_rows": dedupe_rows,
    }
    if is_yangcheon_integrated_target(target):
        return collect_yangcheon_portal(target, **common)
    if is_yangcheon_lifestudy_target(target):
        return collect_yangcheon_lifestudy(target, **common)
    provider = _provider(target)
    parser = YANGCHEON_LIFESTUDY_PARSER if provider == YANGCHEON_LIFESTUDY_PROVIDER else YANGCHEON_INTEGRATED_PARSER
    meta = _finish_meta(
        rows=[],
        pages=0,
        list_complete=False,
        detail_required_count=0,
        detail_attempts=0,
        detail_pages=0,
        detail_errors=[],
        source_cap_reached=False,
        errors=["target does not match a provider-owned canonical Yangcheon route"],
    )
    return [], parser, meta


# Alternative descriptive name for importers that do not use the historical
# ``integrated`` dispatcher name.
collect_yangcheon_target = collect_yangcheon_integrated


__all__ = [
    "YANGCHEON_CANONICAL_URLS",
    "YANGCHEON_INTEGRATED_DETAIL_PATH",
    "YANGCHEON_INTEGRATED_LIST_PATH",
    "YANGCHEON_INTEGRATED_PARSER",
    "YANGCHEON_INTEGRATED_PROVIDER",
    "YANGCHEON_INTEGRATED_URL",
    "YANGCHEON_LIFESTUDY_DETAIL_PATH",
    "YANGCHEON_LIFESTUDY_LIST_PATH",
    "YANGCHEON_LIFESTUDY_PARSER",
    "YANGCHEON_LIFESTUDY_PROVIDER",
    "YANGCHEON_LIFESTUDY_STATES",
    "YANGCHEON_LIFESTUDY_URL",
    "YANGCHEON_MUNICIPALITY_CODE",
    "YANGCHEON_MUNICIPALITY_NAME",
    "YANGCHEON_PROVIDERS",
    "collect_yangcheon_integrated",
    "collect_yangcheon_lifestudy",
    "collect_yangcheon_portal",
    "collect_yangcheon_target",
    "is_target",
    "is_yangcheon_integrated_target",
    "is_yangcheon_lifestudy_target",
    "is_yangcheon_target",
    "yangcheon_integrated_detail_url",
    "yangcheon_integrated_list_url",
    "yangcheon_lifestudy_detail_url",
    "yangcheon_lifestudy_list_url",
]
