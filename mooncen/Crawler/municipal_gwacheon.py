"""Fail-closed collector for Gwacheon's official lifelong-learning ledger.

The public list page is a JavaScript shell backed by a same-origin JSON POST
endpoint.  This collector walks the complete archive, verifies every
current/future public detail, and retains the configured incumbent provider as
the sole owner of the ``stageIdx/programIdx/extraYn`` identity namespace.

Only the public shell, JSON list, and public detail routes are requested.
Application, login, applicant, attachment, preview, and download endpoints are
intentionally parsed only as controls and are never followed.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GWACHEON_PROVIDER = "MUNI_WWW_GCCITY_GO_KR_854A9E81"
GWACHEON_CANDIDATE_ID = "MUNI_IR_A598F3AA9702"
GWACHEON_NOTICE_PROVIDER = "MUNI_WWW_GCCITY_GO_KR_7B6F9BF9"
GWACHEON_MUNICIPALITY_CODE = "4129000000"
GWACHEON_MUNICIPALITY_NAME = "경기도 과천시"
GWACHEON_HOST = "www.gccity.go.kr"
GWACHEON_LIST_PATH = "/reservation/gcedu/edu/app/list.do"
GWACHEON_JSON_PATH = "/reservation/gcedu/edu/app/json/list.do"
GWACHEON_DETAIL_PATH = "/reservation/gcedu/edu/app/view.do"
GWACHEON_APPLICATION_PATH = "/reservation/gcedu/edu/app/write.do"
GWACHEON_MID = "0103010000"
GWACHEON_CANONICAL_URL = (
    f"https://{GWACHEON_HOST}{GWACHEON_LIST_PATH}?mId={GWACHEON_MID}"
)
GWACHEON_JSON_URL = f"https://{GWACHEON_HOST}{GWACHEON_JSON_PATH}"
GWACHEON_PAGE_SIZE = 10
GWACHEON_MAX_PAGES = 700
GWACHEON_MAX_DETAILS = 600
GWACHEON_MAX_WORKERS = 10
GWACHEON_MAX_HTML_BYTES = 2_000_000
GWACHEON_MAX_JSON_BYTES = 1_000_000
GWACHEON_TEST_BRANCH = "테스트 학습장"
GWACHEON_ADMIN_BRANCH = "열린민원과(행정모니터 검증)"
GWACHEON_PARSER = (
    "gwacheon_json_owner+stage_program_extra_identity+full_5539_archive+"
    "empty_sentinel+stable_boundaries+all_current_details+official_place_registry+"
    "test_and_admin_exclusion+no_private_endpoints"
)

GWACHEON_PLACE_REGISTRY: tuple[tuple[str, str], ...] = (
    ("", "학습장 선택"),
    ("52", GWACHEON_ADMIN_BRANCH),
    ("10", "과천동문화교육센터"),
    ("11", "별양동문화교육센터"),
    ("12", "원문동문화교육센터"),
    ("48", "갈현동문화교육센터"),
    ("13", "부림동문화교육센터"),
    ("14", "중앙동문화교육센터"),
    ("15", "문원동문화교육센터"),
    ("39", "과천일자리센터"),
    ("2", "과천시체육회"),
    ("9", "과천문화원"),
    ("50", "문원생활문화센터"),
    ("22", "여성비전센터"),
    ("5", "종합사회복지관"),
    ("35", "전통줄타기보존회"),
    ("46", "청소년수련관"),
    ("1", "평생학습센터"),
)
GWACHEON_PLACE_CODE_BY_NAME = {
    label: code for code, label in GWACHEON_PLACE_REGISTRY if code
}
GWACHEON_OFFICIAL_BRANCHES = frozenset(GWACHEON_PLACE_CODE_BY_NAME)

GWACHEON_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "owner": {
        "provider": GWACHEON_PROVIDER,
        "candidate_id": GWACHEON_CANDIDATE_ID,
        "url": GWACHEON_CANONICAL_URL,
        "decision": "retain_configured_complete_json_ledger_owner",
    },
    "youth_notice": {
        "provider": GWACHEON_NOTICE_PROVIDER,
        "decision": "exclude_static_single_notice_without_course_identity_ledger",
    },
    "portal_home_and_intro": {
        "decision": "exclude_navigation_shells_without_independent_course_identities"
    },
}

_ROW_KEYS = frozenset(
    {
        "placeName",
        "isFree",
        "money",
        "fee",
        "isOnline",
        "ageTypeList",
        "categoryName",
        "title",
        "nowAppCnt",
        "appCnt",
        "stageStateAlias",
        "lecOpenDate",
        "lecCloseDate",
        "timeList",
        "lecDays",
        "stageIdx",
        "programIdx",
        "extraYn",
    }
)
_TIME_KEYS = frozenset({"idx", "pIdx", "startTime", "endTime", "createDate"})
_PAGINATION_KEYS = frozenset(
    {
        "currentPageNo",
        "recordCountPerPage",
        "pageSize",
        "totalRecordCount",
        "totalPageCount",
        "firstPageNoOnPageList",
        "lastPageNoOnPageList",
        "firstRecordIndex",
        "lastRecordIndex",
        "firstPageNo",
        "lastPageNo",
    }
)
_STATUS = {
    "접수 중": "OPEN",
    "대기자 접수 중": "OPEN",
    "접수 대기": "SCHEDULED",
    "접수 마감": "CLOSED",
    "교육 중": "CLOSED",
    "교육 마감": "ENDED",
    "폐강": "CANCELLED",
}
_DETAIL_REQUIRED = frozenset(
    {"접수기간", "학습장/강의실", "교육기간", "교육시간/요일", "수강료", "교육대상", "모집인원/방법"}
)
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_INT_RE = re.compile(r"\d[\d,]*")
_SPACE_RE = re.compile(r"\s+")
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ADMIN_TITLE_RE = re.compile(r"행정모니터.*(?:인증|검증)")
_DETAIL_QUERY_KEYS = ("stageIdx", "programIdx", "extraYn", "mId")


class GwacheonContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Page:
    number: int
    total: int
    rows: tuple[dict[str, Any], ...]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _strict_canonical(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GWACHEON_HOST
        and port is None
        and not parsed.username
        and not parsed.password
        and parsed.path == GWACHEON_LIST_PATH
        and query == [("mId", GWACHEON_MID)]
        and not parsed.fragment
    )


def is_gwacheon_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == GWACHEON_PROVIDER
        and _strict_canonical(_target_value(target, "url"))
    )


is_target = is_gwacheon_education_target


def gwacheon_source_identity(stage_idx: Any, program_idx: Any, extra_yn: Any) -> str:
    stage = _positive_int(stage_idx, "stageIdx")
    program = _positive_int(program_idx, "programIdx")
    extra = _clean(extra_yn)
    if extra not in {"N", "Y"}:
        raise ValueError("extraYn is invalid")
    return f"{GWACHEON_PROVIDER}:stage:{stage}:program:{program}:extra:{extra}"


def gwacheon_list_data(page: int) -> dict[str, str]:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    return {"page": str(page)}


def gwacheon_detail_url(stage_idx: Any, program_idx: Any, extra_yn: Any) -> str:
    stage = _positive_int(stage_idx, "stageIdx")
    program = _positive_int(program_idx, "programIdx")
    extra = _clean(extra_yn)
    if extra not in {"N", "Y"}:
        raise ValueError("extraYn is invalid")
    query = urlencode(
        (("stageIdx", stage), ("programIdx", program), ("extraYn", extra), ("mId", GWACHEON_MID))
    )
    return f"https://{GWACHEON_HOST}{GWACHEON_DETAIL_PATH}?{query}"


def gwacheon_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/138.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": GWACHEON_CANONICAL_URL,
        }
    )
    return current


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise GwacheonContractError(f"{field} is not a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise GwacheonContractError(f"{field} is not a positive integer") from exc
    if number < 1 or str(number) != _clean(value):
        raise GwacheonContractError(f"{field} is not a canonical positive integer")
    return number


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise GwacheonContractError(f"{field} is not a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise GwacheonContractError(f"{field} is not a non-negative integer") from exc
    if number < 0:
        raise GwacheonContractError(f"{field} is not a non-negative integer")
    return number


def _iso_date(value: Any, field: str) -> date:
    text = _clean(value)
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text):
        raise GwacheonContractError(f"{field} is not an ISO date")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise GwacheonContractError(f"{field} is not a valid date") from exc


def _two_dates(value: Any, field: str) -> tuple[date, date]:
    found = _DATE_RE.findall(_clean(value))
    if len(found) != 2:
        raise GwacheonContractError(f"{field} date range changed")
    start, end = (date(*map(int, item)) for item in found)
    if end < start:
        raise GwacheonContractError(f"{field} date range is reversed")
    return start, end


def _allowed(method: str, url: str, data: Optional[Mapping[str, str]]) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    common = (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GWACHEON_HOST
        and port is None
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )
    if not common:
        return False
    if method == "GET" and data is None and parsed.path == GWACHEON_LIST_PATH:
        return query == [("mId", GWACHEON_MID)]
    if method == "POST" and parsed.path == GWACHEON_JSON_PATH and not query:
        if not isinstance(data, Mapping) or set(data) != {"page"}:
            return False
        try:
            return dict(data) == gwacheon_list_data(int(_clean(data.get("page"))))
        except (TypeError, ValueError):
            return False
    if method == "GET" and data is None and parsed.path == GWACHEON_DETAIL_PATH:
        if tuple(key for key, _value in query) != _DETAIL_QUERY_KEYS:
            return False
        values = dict(query)
        try:
            expected = gwacheon_detail_url(
                values["stageIdx"], values["programIdx"], values["extraYn"]
            )
        except (KeyError, TypeError, ValueError):
            return False
        return values.get("mId") == GWACHEON_MID and url == expected
    return False


def _default_fetcher(
    session: Any,
    method: str,
    url: str,
    *,
    timeout: int,
    data: Optional[Mapping[str, str]] = None,
) -> Any:
    if method == "GET":
        return session.get(url, timeout=timeout, allow_redirects=False)
    if method == "POST":
        return session.post(
            url,
            data=dict(data or {}),
            headers={"Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest"},
            timeout=timeout,
            allow_redirects=False,
        )
    raise GwacheonContractError("unaudited HTTP method")


class _Requester:
    def __init__(
        self,
        session_factory: Callable[[], Any],
        fetcher: Callable[..., Any],
        timeout: int,
        meta: dict[str, Any],
    ) -> None:
        self._factory = session_factory
        self._fetcher = fetcher
        self._timeout = timeout
        self._meta = meta
        self._local = threading.local()
        self._sessions: list[Any] = []
        self._lock = threading.Lock()

    def _session(self) -> Any:
        value = getattr(self._local, "session", None)
        if value is None:
            value = self._factory()
            self._local.session = value
            with self._lock:
                self._sessions.append(value)
        return value

    def request(
        self,
        method: str,
        url: str,
        kind: str,
        data: Optional[Mapping[str, str]] = None,
    ) -> Any:
        if not _allowed(method, url, data):
            raise GwacheonContractError("refusing unaudited route")
        counter = {"landing": "landing_pages", "list": "list_requests", "detail": "detail_pages"}[kind]
        with self._lock:
            self._meta["logical_requests"] += 1
            self._meta[counter] += 1
        last_error: Optional[BaseException] = None
        for attempt in range(2):
            with self._lock:
                self._meta["physical_requests"] += 1
            try:
                response = self._fetcher(
                    self._session(), method, url, timeout=self._timeout, data=data
                )
                status = int(getattr(response, "status_code", 0))
                if status in {429, 500, 502, 503, 504} and attempt == 0:
                    with self._lock:
                        self._meta["request_retry_count"] += 1
                    continue
                return _strict_response(response, url)
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 0:
                    with self._lock:
                        self._meta["request_retry_count"] += 1
                    continue
                raise
        raise GwacheonContractError(f"request failed: {last_error}")

    def close(self) -> None:
        for value in self._sessions:
            close = getattr(value, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _strict_response(response: Any, requested_url: str) -> Any:
    if int(getattr(response, "status_code", 0)) != 200:
        raise GwacheonContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    headers = getattr(response, "headers", {}) or {}
    if getattr(response, "history", None) or headers.get("Location") or headers.get("location"):
        raise GwacheonContractError("redirect response is not accepted")
    if _clean(getattr(response, "url", requested_url) or requested_url) != requested_url:
        raise GwacheonContractError("response URL changed")
    return response


def _html(response: Any, requested_url: str) -> BeautifulSoup:
    content_type = _clean((getattr(response, "headers", {}) or {}).get("Content-Type", "text/html")).lower()
    if "html" not in content_type:
        raise GwacheonContractError("HTML route returned a non-HTML response")
    content = getattr(response, "content", None)
    if content is None:
        content = str(getattr(response, "text", "")).encode("utf-8")
    if not content or len(content) > GWACHEON_MAX_HTML_BYTES:
        raise GwacheonContractError("HTML response is empty or oversized")
    return BeautifulSoup(content, "html.parser")


def _json(response: Any) -> Mapping[str, Any]:
    content_type = _clean((getattr(response, "headers", {}) or {}).get("Content-Type", "application/json")).lower()
    if "json" not in content_type:
        raise GwacheonContractError("JSON route returned a non-JSON response")
    content = getattr(response, "content", b"") or b""
    if content and len(content) > GWACHEON_MAX_JSON_BYTES:
        raise GwacheonContractError("JSON response is oversized")
    loader = getattr(response, "json", None)
    if not callable(loader):
        raise GwacheonContractError("JSON response has no decoder")
    try:
        value = loader()
    except Exception as exc:
        raise GwacheonContractError("JSON response is invalid") from exc
    if not isinstance(value, Mapping):
        raise GwacheonContractError("JSON response root is not an object")
    return value


def _landing_contract(soup: BeautifulSoup) -> None:
    forms = soup.select("form#listForm")
    if len(forms) != 1:
        raise GwacheonContractError("canonical list form changed")
    form = forms[0]
    action = urljoin(GWACHEON_CANONICAL_URL, _clean(form.get("action")))
    if _clean(form.get("method")).upper() != "POST" or action != GWACHEON_CANONICAL_URL:
        raise GwacheonContractError("canonical list form route changed")
    places = tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in form.select("select#searchPlaceIdx[name='searchPlaceIdx'] > option")
    )
    if places != GWACHEON_PLACE_REGISTRY:
        raise GwacheonContractError("official place registry changed")
    page = form.select_one("input#page[name='page']")
    idx = form.select_one("input#idx[name='idx']")
    gender = form.select_one("input[name='qualifiedGenderType'][value='A'][checked]")
    all_age = form.select_one("input#allAgeTypeBtn[checked]")
    all_week = form.select_one("input#allLecTimeWeekTypeBtn[checked]")
    active = form.select_one("input[name='searchStageState'][checked]")
    if (
        page is None
        or _clean(page.get("value")) != "1"
        or idx is None
        or _clean(idx.get("value"))
        or gender is None
        or all_age is None
        or all_week is None
        or active is not None
    ):
        raise GwacheonContractError("unfiltered list defaults changed")
    scripts = {
        urlparse(urljoin(GWACHEON_CANONICAL_URL, _clean(node.get("src")))).path
        for node in soup.select("script[src]")
    }
    if "/reservation/js/unit/gcedu/edu/app/list.js" not in scripts:
        raise GwacheonContractError("canonical JSON list script changed")


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row["source_identity"],
        row["place"],
        row["category"],
        row["title"],
        row["source_status"],
        row["event_start"],
        row["event_end"],
        row["fee"],
        row["capacity"],
        row["applicants"],
        row["schedule"],
    )


def _parse_row(value: Any, page: int, position: int) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _ROW_KEYS:
        raise GwacheonContractError(f"page {page} row {position}: JSON vocabulary changed")
    stage = _positive_int(value.get("stageIdx"), "stageIdx")
    program = _positive_int(value.get("programIdx"), "programIdx")
    extra = _clean(value.get("extraYn"))
    source_identity = gwacheon_source_identity(stage, program, extra)
    place = _clean(value.get("placeName"))
    category = _clean(value.get("categoryName"))
    title = _clean(value.get("title"))
    source_status = _clean(value.get("stageStateAlias"))
    if not place or not title or source_status not in _STATUS:
        raise GwacheonContractError(f"course {source_identity}: core list fields changed")
    event_start = _iso_date(value.get("lecOpenDate"), "lecOpenDate")
    event_end = _iso_date(value.get("lecCloseDate"), "lecCloseDate")
    if event_end < event_start:
        raise GwacheonContractError(f"course {source_identity}: reversed education dates")
    if not isinstance(value.get("isFree"), bool) or value.get("isOnline") not in {None, True, False, "Y", "N"}:
        raise GwacheonContractError(f"course {source_identity}: delivery/fee flags changed")
    money = _nonnegative_int(value.get("money"), "money")
    fee = _clean(value.get("fee"))
    if not fee:
        raise GwacheonContractError(f"course {source_identity}: fee contract changed")
    applicants = _nonnegative_int(value.get("nowAppCnt"), "nowAppCnt")
    capacity = _nonnegative_int(value.get("appCnt"), "appCnt")
    ages = value.get("ageTypeList")
    if not isinstance(ages, list) or any(not isinstance(item, str) for item in ages):
        raise GwacheonContractError(f"course {source_identity}: age registry changed")
    times = value.get("timeList")
    if not isinstance(times, list):
        raise GwacheonContractError(f"course {source_identity}: time list changed")
    parsed_times: list[str] = []
    for item in times:
        if not isinstance(item, Mapping) or set(item) != _TIME_KEYS:
            raise GwacheonContractError(f"course {source_identity}: time vocabulary changed")
        _positive_int(item.get("idx"), "time.idx")
        if _positive_int(item.get("pIdx"), "time.pIdx") != program:
            raise GwacheonContractError(f"course {source_identity}: time identity drift")
        start, end = _clean(item.get("startTime")), _clean(item.get("endTime"))
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start) or not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", end
        ):
            raise GwacheonContractError(f"course {source_identity}: time format changed")
        if isinstance(item.get("createDate"), bool) or not isinstance(item.get("createDate"), int):
            raise GwacheonContractError(f"course {source_identity}: time creation marker changed")
        parsed_times.append(f"{start} ~ {end}")
    days = _clean(value.get("lecDays"))
    schedule = "; ".join(parsed_times)
    if days:
        schedule = f"{schedule} ({days})" if schedule else days
    return {
        "identity": (stage, program, extra),
        "source_identity": source_identity,
        "detail_url": gwacheon_detail_url(stage, program, extra),
        "page": page,
        "place": place,
        "category": category,
        "title": title,
        "source_status": source_status,
        "status": _STATUS[source_status],
        "event_start": event_start,
        "event_end": event_end,
        "fee": fee,
        "money": money,
        "capacity": capacity,
        "applicants": applicants,
        "schedule": schedule,
        "ages": tuple(_clean(item) for item in ages if _clean(item)),
        "is_online": value.get("isOnline") in {True, "Y"},
    }


def _parse_page(payload: Mapping[str, Any], page: int, expected_total: Optional[int] = None) -> _Page:
    if set(payload) != {"totalCnt", "list", "pagination"}:
        raise GwacheonContractError(f"page {page}: JSON envelope changed")
    total = _nonnegative_int(payload.get("totalCnt"), "totalCnt")
    if expected_total is not None and total != expected_total:
        raise GwacheonContractError(f"page {page}: total count drift")
    pagination = payload.get("pagination")
    if not isinstance(pagination, Mapping) or set(pagination) != _PAGINATION_KEYS:
        raise GwacheonContractError(f"page {page}: pagination vocabulary changed")
    expected_pagination = {
        "currentPageNo": page,
        "recordCountPerPage": GWACHEON_PAGE_SIZE,
        "pageSize": GWACHEON_PAGE_SIZE,
        "firstRecordIndex": (page - 1) * GWACHEON_PAGE_SIZE,
        "lastRecordIndex": page * GWACHEON_PAGE_SIZE,
    }
    for key, expected in expected_pagination.items():
        if pagination.get(key) != expected:
            raise GwacheonContractError(f"page {page}: pagination {key} changed")
    values = payload.get("list")
    if not isinstance(values, list):
        raise GwacheonContractError(f"page {page}: list is not an array")
    last = math.ceil(total / GWACHEON_PAGE_SIZE) if total else 0
    expected_rows = (
        GWACHEON_PAGE_SIZE
        if page <= last and page < last
        else total - GWACHEON_PAGE_SIZE * (last - 1)
        if page == last and last
        else 0
    )
    if len(values) != expected_rows:
        raise GwacheonContractError(
            f"page {page}: exposes {len(values)} rows, expected {expected_rows}"
        )
    rows = tuple(_parse_row(value, page, position) for position, value in enumerate(values, 1))
    return _Page(number=page, total=total, rows=rows)


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return page.total, tuple(_row_signature(row) for row in page.rows)


def _pairs(root: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in root.select(".view_detail > dl, .btn-wrap-box > dl, dl.view_file"):
        key_node = node.select_one(":scope > dt")
        value_node = node.select_one(":scope > dd")
        key = _clean(key_node.get_text(" ", strip=True)) if key_node else ""
        value = _clean(value_node.get_text(" ", strip=True)) if value_node else ""
        if not key or value_node is None or key in result:
            raise GwacheonContractError("detail field structure changed")
        result[key] = value
    return result


def _application_form(row: Mapping[str, Any], soup: BeautifulSoup) -> int:
    forms = soup.select("form#apply")
    if len(forms) != 1:
        raise GwacheonContractError(f"course {row['source_identity']}: application form changed")
    form = forms[0]
    action = urlparse(urljoin(row["detail_url"], _clean(form.get("action"))))
    query = parse_qsl(action.query, keep_blank_values=True, strict_parsing=True)
    if (
        _clean(form.get("method")).upper() != "POST"
        or action.scheme != "https"
        or (action.hostname or "").lower() != GWACHEON_HOST
        or action.path != GWACHEON_APPLICATION_PATH
        or query != [("mId", GWACHEON_MID)]
    ):
        raise GwacheonContractError(f"course {row['source_identity']}: application route changed")
    inputs = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[name]")
    }
    if set(inputs) != {"placeIdx", "stageIdx", "programIdx", "extraYn"}:
        raise GwacheonContractError(f"course {row['source_identity']}: application fields changed")
    stage, program, extra = row["identity"]
    if inputs["stageIdx"] != str(stage) or inputs["programIdx"] != str(program) or inputs["extraYn"] != extra:
        raise GwacheonContractError(f"course {row['source_identity']}: application identity drift")
    official_code = GWACHEON_PLACE_CODE_BY_NAME.get(row["place"])
    if official_code is not None and inputs["placeIdx"] != official_code:
        raise GwacheonContractError(f"course {row['source_identity']}: application place drift")
    if not inputs["placeIdx"].isdigit():
        raise GwacheonContractError(f"course {row['source_identity']}: application place changed")
    return 1


def _attachments(row: Mapping[str, Any], root: Any) -> int:
    controls = root.select("dl.view_file a[onclick]")
    download_ids: set[tuple[str, str]] = set()
    for control in controls:
        onclick = _clean(control.get("onclick"))
        if onclick.startswith("fn_egov_downFile("):
            match = re.fullmatch(
                r"fn_egov_downFile\('([0-9a-f]{32,64})','([0-9a-f]{32,64})'\); return false;",
                onclick,
            )
            if not match:
                raise GwacheonContractError(f"course {row['source_identity']}: attachment control changed")
            download_ids.add((match.group(1), match.group(2)))
        elif onclick.startswith("fn_egov_preview("):
            match = re.fullmatch(
                r"fn_egov_preview\('([0-9a-f]{32,64})','([0-9a-f]{32,64})'\); return false;",
                onclick,
            )
            if not match or (match.group(1), match.group(2)) not in download_ids:
                raise GwacheonContractError(f"course {row['source_identity']}: preview control changed")
        else:
            raise GwacheonContractError(f"course {row['source_identity']}: file control changed")
    return len(download_ids)


def _excluded_reason(row: Mapping[str, Any]) -> str:
    if row["place"] == GWACHEON_TEST_BRANCH:
        return "test_place"
    if row["place"] == GWACHEON_ADMIN_BRANCH and _ADMIN_TITLE_RE.search(row["title"]):
        return "administrative_identity_check"
    return ""


def _parse_detail(row: Mapping[str, Any], soup: BeautifulSoup) -> tuple[Optional[dict[str, Any]], Counter[str]]:
    roots = soup.select(".bod_app_detail")
    if len(roots) != 1:
        raise GwacheonContractError(f"course {row['source_identity']}: detail root changed")
    root = roots[0]
    heading = root.select_one(":scope > h4")
    status_node = heading.select_one(".icons span[data-type]") if heading else None
    title_node = heading.select_one("strong") if heading else None
    category_node = title_node.select_one(".point") if title_node else None
    source_status = _clean(status_node.get("data-type")) if status_node else ""
    if status_node is None or _clean(status_node.get_text(" ", strip=True)) != source_status:
        raise GwacheonContractError(f"course {row['source_identity']}: detail status changed")
    if source_status != row["source_status"] or title_node is None or category_node is None:
        raise GwacheonContractError(f"course {row['source_identity']}: title/status drift")
    category = _clean(category_node.get_text(" ", strip=True)).strip("[] ")
    category_node.extract()
    title = _clean(title_node.get_text(" ", strip=True))
    if (row["category"] and category != row["category"]) or title != row["title"]:
        raise GwacheonContractError(f"course {row['source_identity']}: title/category drift")
    fields = _pairs(root)
    if not _DETAIL_REQUIRED.issubset(fields):
        raise GwacheonContractError(f"course {row['source_identity']}: required detail fields changed")
    event = _two_dates(fields["교육기간"], "detail education")
    application = _two_dates(fields["접수기간"], "detail application")
    if event != (row["event_start"], row["event_end"]):
        raise GwacheonContractError(f"course {row['source_identity']}: education dates drift")
    venue = fields["학습장/강의실"]
    if not (venue == row["place"] or venue.startswith(f"{row['place']} /")):
        raise GwacheonContractError(f"course {row['source_identity']}: official place drift")
    compact_fee = fields["수강료"].replace(" ", "")
    list_fee = row["fee"].replace(" ", "")
    if compact_fee != list_fee and {compact_fee, list_fee} != {"무료", "0원"}:
        raise GwacheonContractError(f"course {row['source_identity']}: fee drift")
    numbers = [int(value.replace(",", "")) for value in _INT_RE.findall(fields["모집인원/방법"])]
    if len(numbers) < 2 or numbers[:2] != [row["capacity"], row["applicants"]]:
        raise GwacheonContractError(f"course {row['source_identity']}: capacity drift")
    detail_times = _TIME_RE.findall(fields["교육시간/요일"])
    list_times = _TIME_RE.findall(row["schedule"])
    if detail_times != list_times:
        raise GwacheonContractError(f"course {row['source_identity']}: schedule drift")
    application_controls = _application_form(row, soup)
    attachment_count = _attachments(row, root)
    excluded = _excluded_reason(row)
    if not excluded and row["place"] not in GWACHEON_OFFICIAL_BRANCHES:
        raise GwacheonContractError(f"course {row['source_identity']}: unaudited official branch")
    counters = Counter(
        application_controls=application_controls,
        attachments=attachment_count,
        sensitive=sum(
            int(label in fields)
            for label in ("문의전화", "강사명", "강사소개")
        )
        + int(root.select_one(".view_cont") is not None),
    )
    if excluded:
        counters[f"excluded_{excluded}"] += 1
        return None, counters
    output = {
        "provider": GWACHEON_PROVIDER,
        "municipality_code": GWACHEON_MUNICIPALITY_CODE,
        "municipality_name": GWACHEON_MUNICIPALITY_NAME,
        "provider_course_id": row["source_identity"],
        "source_course_id": f"{row['identity'][0]}:{row['identity'][1]}:{row['identity'][2]}",
        "title": title,
        "status": row["status"],
        "source_status": source_status,
        "start_date": row["event_start"].isoformat(),
        "end_date": row["event_end"].isoformat(),
        "apply_start_date": application[0].isoformat(),
        "apply_end_date": application[1].isoformat(),
        "schedule": row["schedule"],
        "branch": row["place"],
        "venue": venue,
        "category": category,
        "fee": fields["수강료"],
        "capacity": row["capacity"],
        "applicants": row["applicants"],
        "source_url": row["detail_url"],
        "application_url": "",
        "raw_fields": {
            "age_groups": list(row["ages"]),
            "is_online": row["is_online"],
        },
    }
    return output, counters


def _base_meta(cutoff: date) -> dict[str, Any]:
    return {
        "provider": GWACHEON_PROVIDER,
        "municipality_code": GWACHEON_MUNICIPALITY_CODE,
        "audit_date": cutoff.isoformat(),
        "logical_requests": 0,
        "physical_requests": 0,
        "landing_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "request_retry_count": 0,
        "reservation_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "preview_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_values_persisted": 0,
        "discovered_links": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "source_cap_reached": False,
        "snapshot_complete": False,
        "no_current_data": False,
        "no_current_reason": "",
    }


def _privacy(rows: Iterable[Mapping[str, Any]]) -> int:
    forbidden = {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "teacher",
        "attachments",
        "detail_description",
        "source_html",
    }
    identifier_fields = {
        "provider",
        "municipality_code",
        "provider_course_id",
        "source_course_id",
        "source_url",
        "application_url",
    }
    findings = 0
    for row in rows:
        findings += len(set(row) & forbidden)
        for key, value in row.items():
            if key in identifier_fields:
                continue
            text = repr(value)
            findings += len(_PHONE_RE.findall(text)) + len(_EMAIL_RE.findall(text))
    return findings


def _parallel_map(
    values: Sequence[int] | Sequence[Mapping[str, Any]],
    callback: Callable[[Any], Any],
    max_workers: int,
) -> list[Any]:
    if not values:
        return []
    results: dict[int, Any] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(values))) as executor:
        futures = {executor.submit(callback, value): index for index, value in enumerate(values)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [results[index] for index in range(len(values))]


def collect_gwacheon_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = GWACHEON_MAX_PAGES,
    detail_limit: int = GWACHEON_MAX_DETAILS,
    max_workers: int = GWACHEON_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[Callable[[], Any]] = None,
    fetcher: Optional[Callable[..., Any]] = None,
    dedupe_rows: Optional[
        Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
    ] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    try:
        cutoff = (
            today.date()
            if isinstance(today, datetime)
            else today
            if isinstance(today, date)
            else date.fromisoformat(_clean(today))
            if today
            else datetime.now(ZoneInfo("Asia/Seoul")).date()
        )
    except Exception:
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
        meta = _base_meta(cutoff)
        meta["configured_collection_error"] = "today is invalid"
        return [], GWACHEON_PARSER, meta
    meta = _base_meta(cutoff)
    if not is_gwacheon_education_target(target):
        meta["configured_collection_error"] = "target does not match Gwacheon incumbent owner"
        return [], GWACHEON_PARSER, meta
    try:
        timeout = int(timeout)
        max_pages = int(max_pages)
        detail_limit = int(detail_limit)
        max_workers = int(max_workers)
        if timeout < 1 or max_pages < 1 or detail_limit < 0 or not 1 <= max_workers <= 16:
            raise ValueError
    except Exception:
        meta["configured_collection_error"] = "invalid limits"
        return [], GWACHEON_PARSER, meta
    requester = _Requester(
        session_factory or gwacheon_session_factory,
        fetcher or _default_fetcher,
        timeout,
        meta,
    )
    listed: list[dict[str, Any]] = []
    try:
        landing = _html(
            requester.request("GET", GWACHEON_CANONICAL_URL, "landing"),
            GWACHEON_CANONICAL_URL,
        )
        _landing_contract(landing)
        first = _parse_page(
            _json(
                requester.request(
                    "POST", GWACHEON_JSON_URL, "list", gwacheon_list_data(1)
                )
            ),
            1,
        )
        last_page = math.ceil(first.total / GWACHEON_PAGE_SIZE) if first.total else 0
        sentinel_page = max(1, last_page + 1)
        boundary_numbers = tuple(dict.fromkeys((1, last_page or 1, sentinel_page)))
        required_list_requests = max(1, last_page) + 1 + len(boundary_numbers)
        meta["required_list_requests"] = required_list_requests
        if required_list_requests > max_pages:
            meta["source_cap_reached"] = True
            raise GwacheonContractError(
                f"max_pages cap allows {max_pages} of {required_list_requests} list requests"
            )

        def load_page(number: int) -> _Page:
            return _parse_page(
                _json(
                    requester.request(
                        "POST", GWACHEON_JSON_URL, "list", gwacheon_list_data(number)
                    )
                ),
                number,
                first.total,
            )

        pages: dict[int, _Page] = {1: first}
        numbers = list(range(2, last_page + 1))
        for page in _parallel_map(numbers, load_page, max_workers):
            pages[page.number] = page
        sentinel = load_page(sentinel_page)
        if sentinel.rows:
            raise GwacheonContractError("immediate post-boundary sentinel is not empty")
        originals = {1: first, sentinel_page: sentinel}
        if last_page:
            originals[last_page] = pages[last_page]
        rechecks: dict[str, bool] = {}
        for number in boundary_numbers:
            observed = load_page(number)
            stable = _page_signature(observed) == _page_signature(originals[number])
            rechecks[str(number)] = stable
            if not stable:
                raise GwacheonContractError(f"page {number}: boundary stability changed")
        for number in range(1, last_page + 1):
            listed.extend(dict(row) for row in pages[number].rows)
        if len(listed) != first.total:
            raise GwacheonContractError("full archive count does not match declared total")
        unique: dict[str, dict[str, Any]] = {}
        duplicate_count = 0
        for row in listed:
            previous = unique.get(row["source_identity"])
            if previous is None:
                unique[row["source_identity"]] = row
            elif _row_signature(previous) == _row_signature(row):
                duplicate_count += 1
            else:
                raise GwacheonContractError("duplicate source identity has conflicting data")
        current = [row for row in unique.values() if row["event_end"] >= cutoff]
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise GwacheonContractError(
                f"detail cap allows {detail_limit} of {len(current)} current identities"
            )

        def load_detail(row: Mapping[str, Any]) -> tuple[Optional[dict[str, Any]], Counter[str]]:
            soup = _html(
                requester.request("GET", row["detail_url"], "detail"),
                row["detail_url"],
            )
            return _parse_detail(row, soup)

        parsed = _parallel_map(current, load_detail, max_workers)
        output = [row for row, _counter in parsed if row is not None]
        discarded: Counter[str] = Counter()
        for _row, counter in parsed:
            discarded.update(counter)
        if dedupe_rows:
            deduped = list(dedupe_rows(output))
        else:
            seen: set[str] = set()
            deduped = []
            for row in output:
                if row["provider_course_id"] not in seen:
                    seen.add(row["provider_course_id"])
                    deduped.append(row)
        if len(deduped) != len(output):
            raise GwacheonContractError("output dedupe changed the complete snapshot")
        privacy = _privacy(deduped)
        meta["pii_values_persisted"] = privacy
        if privacy:
            raise GwacheonContractError("PII allowlist violation")
        deduped.sort(
            key=lambda row: (row["start_date"], row["title"], row["provider_course_id"])
        )
        identities = sorted(unique)
        meta.update(
            {
                "pages": last_page,
                "page_counts": {number: len(pages[number].rows) for number in pages},
                "source_total": first.total,
                "source_rows": len(listed),
                "source_identity_count": len(unique),
                "source_duplicate_count": duplicate_count,
                "discovered_links": len(unique),
                "source_identity_sha256": hashlib.sha256(
                    "\n".join(identities).encode("utf-8")
                ).hexdigest(),
                "sentinel_page": sentinel_page,
                "sentinel_rows": len(sentinel.rows),
                "boundary_rechecks": rechecks,
                "current_source_count": len(current),
                "expired_count": len(unique) - len(current),
                "detail_verified": len(current),
                "excluded_test_count": discarded["excluded_test_place"],
                "excluded_non_course_count": discarded[
                    "excluded_administrative_identity_check"
                ],
                "returned_count": len(deduped),
                "source_status_counts": dict(
                    Counter(row["source_status"] for row in current)
                ),
                "status_counts": dict(Counter(row["status"] for row in deduped)),
                "branch_counts": dict(Counter(row["branch"] for row in deduped)),
                "application_control_count": discarded["application_controls"],
                "attachment_fields_discarded": discarded["attachments"],
                "sensitive_detail_fields_discarded": discarded["sensitive"],
                "official_place_registry_count": len(GWACHEON_OFFICIAL_BRANCHES),
                "pagination_detected": last_page > 1,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not deduped,
                "no_current_reason": (
                    "the complete official Gwacheon catalogue contains no current courses"
                    if not deduped
                    else ""
                ),
            }
        )
        return deduped, GWACHEON_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "source_rows": len(listed),
                "returned_count": 0,
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], GWACHEON_PARSER, meta
    finally:
        requester.close()


collect = collect_gwacheon_education

__all__ = [name for name in globals() if name.startswith("GWACHEON_")] + [
    "GwacheonContractError",
    "collect",
    "collect_gwacheon_education",
    "gwacheon_detail_url",
    "gwacheon_list_data",
    "gwacheon_source_identity",
    "is_gwacheon_education_target",
    "is_target",
]
