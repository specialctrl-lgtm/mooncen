"""Fail-closed recovery collector for Bonghwa-gun lifelong-learning courses.

The production provider is deliberately preserved as
``MUNI_WWW_BONGHWA_GO_KR_A33FDB5A``.  The old ``/edu/portal`` URL was moved
and now redirects to the new portal home rather than the course ledger.  The
canonical owner is the official reservation path under ``/reservation/edu``.

The catalogue is a JSON POST service split into three source states:
``wait``, ``ing``, and ``end``.  This collector reads every page of every
partition, its immediate empty post-last sentinel, and then rechecks the
first, last, and sentinel responses after current details have been read.
The partitions must be disjoint and their union is the audited source ledger.

As of 2026-07-23 the ``end`` endpoint declares 206 records but returns only
205 identities (the declared final page size is six, while the response has
five).  That stable one-row source defect is explicitly constrained; any
different deficit fails closed.  The current ``wait`` + ``ing`` identity set
is unaffected and contains 30 rows.

Only current/future details are fetched.  The public GET application form and
its identity-bound hidden fields are verified, but it is never submitted or
requested.  Applicant pages, login pages, files, images, instructor names,
free-text course bodies, contacts, and attachments are never persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import json
import re
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


BONGHWA_PROVIDER = "MUNI_WWW_BONGHWA_GO_KR_A33FDB5A"
BONGHWA_DUPLICATE_PROVIDER = "MUNI_WWW_BONGHWA_GO_KR_C3F54364"
BONGHWA_STALE_CANDIDATE_PROVIDER = "MUNI_WWW_BONGHWA_GO_KR_E3E87C9A"
BONGHWA_NEW_URL_HASH_PROVIDER_NOT_TO_CREATE = "MUNI_WWW_BONGHWA_GO_KR_1423AF64"

BONGHWA_MUNICIPALITY_CODE = "4792000000"
BONGHWA_MUNICIPALITY_NAME = "경상북도 봉화군"
BONGHWA_HOST = "www.bonghwa.go.kr"
BONGHWA_MID = "0301000000"
BONGHWA_BRANCH = "봉화군 평생학습관"
BONGHWA_BRANCH_ADDRESS = "경상북도 봉화군 봉화읍 내성로 5길 13"

BONGHWA_LIST_PATH = "/reservation/edu/academy/program/list.do"
BONGHWA_AJAX_PATH = "/reservation/edu/academy/program/ajax/list.do"
BONGHWA_DETAIL_PATH = "/reservation/edu/academy/program/view.do"
BONGHWA_APPLICATION_PATH = "/reservation/edu/academy/apply/agree.do"
BONGHWA_CANONICAL_URL = (
    f"https://{BONGHWA_HOST}{BONGHWA_LIST_PATH}?mid={BONGHWA_MID}"
)
BONGHWA_AJAX_URL = f"https://{BONGHWA_HOST}{BONGHWA_AJAX_PATH}"
BONGHWA_OLD_URL = (
    "https://www.bonghwa.go.kr/edu/portal/academy/program/list.do?"
    "mId=0301000000"
)
BONGHWA_PORTAL_CANDIDATE_URL = "https://www.bonghwa.go.kr/edu"
BONGHWA_STALE_CANDIDATE_URL = (
    "https://www.bonghwa.go.kr/open.content/ko/welfare/edu/facility/"
    "application/?i=353"
)

BONGHWA_PORTAL_CANDIDATE_ID = "MUNI_IR_70E0E5CFA3F4"
BONGHWA_STALE_CANDIDATE_ID = "MUNI_IR_33DB12DA34BF"
BONGHWA_CANONICAL_CANDIDATE_ID = "MUNI_IR_752857C970F3"

BONGHWA_PAGE_SIZE = 10
BONGHWA_RECOMMENDED_MAX_PAGES = 30
BONGHWA_RECOMMENDED_DETAIL_LIMIT = 100
BONGHWA_RECOMMENDED_MAX_WORKERS = 5
BONGHWA_FETCH_ATTEMPTS = 2
BONGHWA_MAX_RESPONSE_BYTES = 3_000_000
BONGHWA_PARSER = (
    "bonghwa_incumbent_owner_complete_wait_ing_end_json_partitions+"
    "declared_total_and_audited_end_deficit+exact_empty_post_last+"
    "stable_first_last_sentinel+current_detail_identity_binding+"
    "get_application_contract_no_fetch+pii_free_text_attachment_allowlist"
)


class BonghwaContractError(RuntimeError):
    """Raised when the official Bonghwa source violates the audited contract."""


@dataclass(frozen=True)
class BonghwaPartition:
    key: str
    label: str
    current: bool
    source_statuses: frozenset[str]
    audited_declared_deficit: int


BONGHWA_PARTITIONS: tuple[BonghwaPartition, ...] = (
    BonghwaPartition("wait", "모집예정", True, frozenset({"모집예정"}), 0),
    BonghwaPartition("ing", "모집중", True, frozenset({"모집중"}), 0),
    BonghwaPartition(
        "end",
        "마감",
        False,
        frozenset({"모집마감", "정원마감", "추첨대기", "추첨결과공개"}),
        1,
    ),
)
BONGHWA_PARTITION_BY_KEY = {item.key: item for item in BONGHWA_PARTITIONS}

BONGHWA_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BONGHWA_PORTAL_CANDIDATE_ID: {
        "provider": BONGHWA_DUPLICATE_PROVIDER,
        "url": BONGHWA_PORTAL_CANDIDATE_URL,
        "decision": "retarget_to_incumbent_owner_not_new_provider",
        "owner": BONGHWA_PROVIDER,
        "redirect": "https://www.bonghwa.go.kr/reservation/edu/main.do",
    },
    BONGHWA_STALE_CANDIDATE_ID: {
        "provider": BONGHWA_STALE_CANDIDATE_PROVIDER,
        "url": BONGHWA_STALE_CANDIDATE_URL,
        "decision": "reject_stale_county_url_redirecting_to_general_main",
        "owner": "",
        "redirect": "https://www.bonghwa.go.kr/main.do",
    },
    BONGHWA_CANONICAL_CANDIDATE_ID: {
        "provider": BONGHWA_PROVIDER,
        "url": BONGHWA_CANONICAL_URL,
        "decision": "reuse_incumbent_provider_on_new_official_canonical",
        "owner": BONGHWA_PROVIDER,
        "new_provider_created": False,
    },
}

BONGHWA_NON_EXECUTING_ALIASES: tuple[Mapping[str, str], ...] = (
    {
        "url": BONGHWA_OLD_URL,
        "decision": "old_path_redirects_to_new_portal_home_not_course_ledger",
        "owner": BONGHWA_PROVIDER,
    },
    {
        "url": "https://www.bonghwa.go.kr/reservation/edu/main.do",
        "decision": "navigation_home_without_complete_course_identity_rows",
        "owner": BONGHWA_PROVIDER,
    },
    {
        "url": "https://www.bonghwa.go.kr/reservation/edu/contents.do?mid=0305000000",
        "decision": "separate_related_institution_course_menu",
        "owner": "",
    },
    {
        "url": "https://www.bonghwa.go.kr/reservation/edu/contents.do?mid=0304000000",
        "decision": "personal_application_history_not_public_course_source",
        "owner": "",
    },
)

BONGHWA_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "checked_on": "2026-07-28",
    "declared_counts": {"wait": 0, "ing": 16, "end": 237},
    "returned_counts": {"wait": 0, "ing": 16, "end": 236},
    "data_pages": {"wait": 0, "ing": 2, "end": 24},
    "page_counts": {
        "wait": [],
        "ing": [10, 6],
        "end": [10] * 23 + [6],
    },
    "sentinel_pages": {"wait": 2, "ing": 3, "end": 25},
    "declared_deficits": {"wait": 0, "ing": 0, "end": 1},
    "partition_union_count": 252,
    "partition_overlap_count": 0,
    "source_status_counts": {
        "모집중": 16,
        "정원마감": 1,
        "모집마감": 155,
        "추첨결과공개": 75,
        "추첨대기": 5,
    },
    "current_rows": 16,
    "current_details": 16,
    "application_controls": 16,
    "expected_logical_requests": 55,
    "expected_post_requests": 38,
    "expected_get_requests": 17,
}

BONGHWA_RECOMMENDED_OVERRIDE: Mapping[str, Any] = {
    "code": BONGHWA_MUNICIPALITY_CODE,
    "full_name": BONGHWA_MUNICIPALITY_NAME,
    "provider": BONGHWA_PROVIDER,
    "canonical_candidate_id": BONGHWA_CANONICAL_CANDIDATE_ID,
    "url": BONGHWA_CANONICAL_URL,
    "branch": BONGHWA_BRANCH,
    "address": BONGHWA_BRANCH_ADDRESS,
    "provider_decision": "reuse_incumbent_and_keep_C3F54364_duplicate",
    "duplicate_provider": BONGHWA_DUPLICATE_PROVIDER,
    "crawler": "Crawler.municipal_bonghwa:collect_bonghwa_education",
    "recommended_max_pages": BONGHWA_RECOMMENDED_MAX_PAGES,
    "recommended_detail_limit": BONGHWA_RECOMMENDED_DETAIL_LIMIT,
    "recommended_max_workers": BONGHWA_RECOMMENDED_MAX_WORKERS,
}


_JSON_ROW_KEYS = frozenset(
    {
        "programAppIdx",
        "appStateValue",
        "eduSdate",
        "eduTuition",
        "appTypeValue",
        "eduCost",
        "appliedOnNum",
        "eduDayValue",
        "appSdate",
        "eduFieldDetailName",
        "appliedOffNum",
        "appOnNum",
        "appliedWaitNum",
        "eduEtime",
        "isFree",
        "eduEdate",
        "appEdate",
        "appWaitNum",
        "eduFieldName",
        "eduField",
        "eduTitle",
        "eduStime",
        "appOffNum",
    }
)
_JSON_TOP_KEYS = frozenset(
    {"listOrder", "programListCnt", "paginationInfo", "programList", "page"}
)
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
    }
)
_POST_KEYS = (
    "mid",
    "page",
    "searchTxt",
    "searchAppSortState",
    "searchField",
    "searchFieldDetail",
    "searchAppType",
    "searchEduTime",
)
_FIELD_NAMES: Mapping[str, str] = {
    "1": "인문교양",
    "2": "문화예술",
    "3": "시민참여",
    "4": "문해교육",
    "5": "직업능력",
    "6": "학력보완",
    "7": "기타",
}
_DETAIL_FIELD_NAMES: Mapping[str, frozenset[str]] = {
    "1": frozenset({"", "생활건강", "외국어", "정보화", "인문교양"}),
    "2": frozenset({"", "음악", "미술", "운동", "생활문화예술"}),
    "3": frozenset({""}),
    "4": frozenset({""}),
    "5": frozenset({""}),
    "6": frozenset({""}),
    "7": frozenset({""}),
}
_DETAIL_LABELS = frozenset(
    {
        "학습분야",
        "모집대상",
        "모집기간",
        "추첨예정일",
        "교육기간",
        "교육시간",
        "강사",
        "재료비",
        "방문접수처 (오프라인신청)",
        "교육장소",
        "강의내용",
        "관련 이미지",
        "첨부파일",
    }
)
_DETAIL_REQUIRED_LABELS = _DETAIL_LABELS - {"추첨예정일"}
_DISCARDED_DETAIL_FIELDS = (
    "강사",
    "방문접수처 (오프라인신청)",
    "강의내용",
    "관련 이미지",
    "첨부파일",
    "추첨예정일",
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "parser",
        "identity",
        "partition",
        "source_page",
        "source_status",
        "source_application_type",
        "source_field_code",
        "source_field_name",
        "source_field_detail_name",
        "online_capacity",
        "offline_capacity",
        "waitlist_capacity",
        "online_applied",
        "offline_applied",
        "waitlist_applied",
        "application_stage_id",
        "application_program_id",
        "detail_verified",
        "application_control_present",
        "application_control_verified",
        "application_endpoint_requested",
        "applicant_endpoint_requested",
        "attachment_endpoint_requested",
        "discarded_detail_fields",
        "privacy_policy",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "contacts",
        "instructor",
        "instructor_name",
        "manager",
        "manager_name",
        "attachments",
        "attachment_urls",
        "body",
        "course_content",
        "applicant_name",
        "applicant_phone",
        "password",
        "source_html",
        "raw_html",
    }
)

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_DATE_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DATETIME_RANGE_RE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s+([0-2]\d:[0-5]\d)\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s+([0-2]\d:[0-5]\d)$"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[-\s)]?\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_SENSITIVE_PATH_RE = re.compile(
    r"/(?:apply|login|member|mypage|file|download|docviewer)(?:/|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ListedCourse:
    partition: str
    page: int
    identity: str
    title: str
    source_status: str
    application_type: str
    apply_start: date
    apply_end: date
    event_start: date
    event_end: date
    day: str
    start_time: str
    end_time: str
    field_code: str
    field_name: str
    field_detail_name: str
    is_free: bool
    tuition: str
    material_cost: str
    online_capacity: int
    offline_capacity: int
    waitlist_capacity: int
    online_applied: int
    offline_applied: int
    waitlist_applied: int

    @property
    def detail_url(self) -> str:
        return (
            f"https://{BONGHWA_HOST}{BONGHWA_DETAIL_PATH}?"
            f"{urlencode({'mid': BONGHWA_MID, 'programAppIdx': self.identity})}"
        )


@dataclass(frozen=True)
class _JsonPage:
    partition: str
    requested_page: int
    declared_total: int
    advertised_last: int
    rows: tuple[_ListedCourse, ...]
    pagination: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _PartitionSnapshot:
    partition: BonghwaPartition
    first: _JsonPage
    pages: tuple[_JsonPage, ...]
    sentinel: _JsonPage
    rows: tuple[_ListedCourse, ...]


Transport = Callable[[Any, str, str, int, Optional[Mapping[str, str]]], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_bonghwa_education_target(target: Any) -> bool:
    """Match only the incumbent provider on the exact new canonical URL."""

    if _clean(_target_value(target, "provider")) != BONGHWA_PROVIDER:
        return False
    value = _clean(_target_value(target, "url"))
    if value != BONGHWA_CANONICAL_URL:
        return False
    try:
        parsed = urlparse(value)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname == BONGHWA_HOST
            and parsed.port is None
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and parsed.path == BONGHWA_LIST_PATH
            and parse_qs(parsed.query, keep_blank_values=True)
            == {"mid": [BONGHWA_MID]}
        )
    except ValueError:
        return False


is_target = is_bonghwa_education_target


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(ZoneInfo("Asia/Seoul")).date()
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _parse_date(value: Any, label: str) -> date:
    text = _clean(value)
    if not _DATE_RE.fullmatch(text):
        raise BonghwaContractError(f"invalid {label}: {text}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise BonghwaContractError(f"invalid {label}: {text}") from exc


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": BONGHWA_CANONICAL_URL,
        }
    )
    return current


def _request(
    session: Any,
    method: str,
    url: str,
    timeout: int,
    data: Optional[Mapping[str, str]],
) -> Any:
    if method == "GET":
        return session.get(url, timeout=timeout, allow_redirects=True)
    if method == "POST":
        return session.post(
            url,
            data=data,
            timeout=timeout,
            allow_redirects=True,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    raise BonghwaContractError(f"unsupported HTTP method: {method}")


def _post_data(partition: str, page: int) -> dict[str, str]:
    if partition not in BONGHWA_PARTITION_BY_KEY or page < 1:
        raise BonghwaContractError("invalid partition/page")
    return {
        "mid": BONGHWA_MID,
        "page": str(page),
        "searchTxt": "",
        "searchAppSortState": partition,
        "searchField": "",
        "searchFieldDetail": "",
        "searchAppType": "",
        "searchEduTime": "",
    }


def _guard_request(
    method: str,
    url: str,
    data: Optional[Mapping[str, str]],
) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != BONGHWA_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
        or _SENSITIVE_PATH_RE.search(parsed.path)
    ):
        raise BonghwaContractError(f"unsafe request URL: {url}")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if method == "GET":
        if data is not None:
            raise BonghwaContractError("GET request unexpectedly carries form data")
        if parsed.path == BONGHWA_LIST_PATH:
            if query != {"mid": [BONGHWA_MID]}:
                raise BonghwaContractError("unsafe canonical list query")
            return
        if parsed.path == BONGHWA_DETAIL_PATH:
            if set(query) != {"mid", "programAppIdx"} or query["mid"] != [BONGHWA_MID]:
                raise BonghwaContractError("unsafe detail query")
            if not _IDENTITY_RE.fullmatch(_clean(query["programAppIdx"][0])):
                raise BonghwaContractError("unsafe detail identity")
            return
        raise BonghwaContractError(f"GET path outside read allowlist: {parsed.path}")
    if method != "POST" or parsed.path != BONGHWA_AJAX_PATH or query:
        raise BonghwaContractError("only the exact JSON list POST is allowed")
    if not isinstance(data, Mapping) or tuple(data) != _POST_KEYS:
        raise BonghwaContractError("JSON list POST parameter contract changed")
    if _clean(data.get("mid")) != BONGHWA_MID:
        raise BonghwaContractError("JSON list POST mid changed")
    page = _clean(data.get("page"))
    partition = _clean(data.get("searchAppSortState"))
    if not page.isdigit() or int(page) < 1 or partition not in BONGHWA_PARTITION_BY_KEY:
        raise BonghwaContractError("invalid JSON list page/partition")
    if any(_clean(data.get(key)) for key in _POST_KEYS[2:] if key != "searchAppSortState"):
        raise BonghwaContractError("unfiltered owner POST unexpectedly has search values")


def _response_size(result: Any) -> int:
    content = getattr(result, "content", b"")
    if isinstance(content, bytes) and content:
        return len(content)
    text = getattr(result, "text", "")
    if text:
        return len(str(text).encode("utf-8"))
    try:
        return len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    except TypeError:
        return 0


def _coerce_response(
    result: Any,
    method: str,
    requested_url: str,
    data: Optional[Mapping[str, str]],
) -> Any:
    if not isinstance(result, (str, bytes, BeautifulSoup, Mapping)):
        status = int(getattr(result, "status_code", 200))
        if status != 200:
            raise BonghwaContractError(f"HTTP {status} for {requested_url}")
        final_url = _clean(getattr(result, "url", requested_url)) or requested_url
        _guard_request(method, final_url, data)
    size = _response_size(result)
    if size > BONGHWA_MAX_RESPONSE_BYTES:
        raise BonghwaContractError("response exceeds byte limit")
    if method == "POST":
        if isinstance(result, Mapping):
            return dict(result)
        if hasattr(result, "json"):
            try:
                value = result.json()
            except Exception as exc:
                raise BonghwaContractError("JSON endpoint returned invalid JSON") from exc
        else:
            raw = result.decode("utf-8") if isinstance(result, bytes) else str(result)
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise BonghwaContractError("JSON endpoint returned invalid JSON") from exc
        if not isinstance(value, Mapping):
            raise BonghwaContractError("JSON endpoint returned non-object")
        return dict(value)
    if isinstance(result, BeautifulSoup):
        return result
    if isinstance(result, (str, bytes)):
        return BeautifulSoup(result, "html.parser")
    payload = getattr(result, "content", b"") or getattr(result, "text", "")
    return BeautifulSoup(payload, "html.parser")


def _fetch(
    session: Any,
    method: str,
    url: str,
    timeout: int,
    data: Optional[Mapping[str, str]],
    transport: Transport,
    meta: dict[str, Any],
    lock: Optional[Lock] = None,
) -> Any:
    _guard_request(method, url, data)
    if lock:
        with lock:
            meta["logical_requests"] += 1
            meta[f"{method.lower()}_requests"] += 1
    else:
        meta["logical_requests"] += 1
        meta[f"{method.lower()}_requests"] += 1
    error: Optional[Exception] = None
    for _ in range(BONGHWA_FETCH_ATTEMPTS):
        if lock:
            with lock:
                meta["physical_attempts"] += 1
        else:
            meta["physical_attempts"] += 1
        try:
            return _coerce_response(
                transport(session, method, url, timeout, data),
                method,
                url,
                data,
            )
        except Exception as exc:
            error = exc
    if isinstance(error, BonghwaContractError):
        raise error
    raise BonghwaContractError(f"request failed for {url}: {_clean(error)}") from error


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True) if node is not None else "")


def _parse_landing(soup: BeautifulSoup) -> None:
    if _text(soup.title) != "평생학습강좌 | 수강신청 | 홈페이지":
        raise BonghwaContractError("canonical landing title changed")
    if _text(soup.select_one("#titWrap h3")) != "평생학습강좌":
        raise BonghwaContractError("canonical landing owner heading changed")
    scripts = [_clean(node.get("src")) for node in soup.select("script[src]")]
    if "/reservation/edu/js/unit/academy/program/list.js" not in scripts:
        raise BonghwaContractError("canonical JSON list script changed")
    sorts = {
        _clean(node.get("data-sort")): _text(node)
        for node in soup.select("a[data-sort]")
    }
    if sorts != {"wait": "모집예정", "ing": "모집중", "end": "마감"}:
        raise BonghwaContractError("official state partitions changed")
    fields = {
        (_clean(node.get("data-field")), _clean(node.get("data-field-detail"))): _text(node)
        for node in soup.select('a[data-search="field"]')
    }
    required = {
        ("", ""): "전체",
        ("1", ""): "인문교양",
        ("2", ""): "문화예술",
        ("3", ""): "시민참여",
        ("4", ""): "문해교육",
        ("5", ""): "직업능력",
        ("6", ""): "학력보완",
        ("7", ""): "기타",
    }
    if not all(fields.get(key) == value for key, value in required.items()):
        raise BonghwaContractError("official learning fields changed")
    if len(soup.select("div.enrolment-list ul")) != 1:
        raise BonghwaContractError("canonical JSON list container changed")


def _positive_int(value: Any, label: str, *, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BonghwaContractError(f"{label} must be an integer")
    if value < (0 if allow_zero else 1):
        raise BonghwaContractError(f"{label} is out of range")
    return value


def _parse_json_row(
    value: Any,
    partition: BonghwaPartition,
    page: int,
) -> _ListedCourse:
    if not isinstance(value, Mapping) or set(value) != _JSON_ROW_KEYS:
        raise BonghwaContractError(f"{partition.key}: JSON row vocabulary changed")
    identity_value = _positive_int(value["programAppIdx"], "programAppIdx", allow_zero=False)
    identity = str(identity_value)
    strings: dict[str, str] = {}
    for key in _JSON_ROW_KEYS - {
        "programAppIdx",
        "appliedOnNum",
        "appliedOffNum",
        "appliedWaitNum",
        "appOnNum",
        "appOffNum",
        "appWaitNum",
    }:
        if not isinstance(value[key], str):
            raise BonghwaContractError(f"{partition.key}:{identity}: {key} type changed")
        strings[key] = _clean(value[key])
    title = strings["eduTitle"]
    if not title or len(title) > 300:
        raise BonghwaContractError(f"{partition.key}:{identity}: invalid title")
    source_status = strings["appStateValue"]
    if source_status not in partition.source_statuses:
        raise BonghwaContractError(
            f"{partition.key}:{identity}: source status escaped partition"
        )
    application_type = strings["appTypeValue"]
    if application_type not in {"선착순", "추첨"}:
        raise BonghwaContractError(f"{partition.key}:{identity}: unknown application type")
    apply_start = _parse_date(strings["appSdate"], "application start")
    apply_end = _parse_date(strings["appEdate"], "application end")
    event_start = _parse_date(strings["eduSdate"], "education start")
    event_end = _parse_date(strings["eduEdate"], "education end")
    if apply_end < apply_start or event_end < event_start:
        raise BonghwaContractError(f"{partition.key}:{identity}: reversed date range")
    start_time, end_time = strings["eduStime"], strings["eduEtime"]
    if not _TIME_RE.fullmatch(start_time) or not _TIME_RE.fullmatch(end_time):
        raise BonghwaContractError(f"{partition.key}:{identity}: invalid education time")
    field_code = strings["eduField"]
    field_name = strings["eduFieldName"]
    field_detail = strings["eduFieldDetailName"]
    if _FIELD_NAMES.get(field_code) != field_name:
        raise BonghwaContractError(f"{partition.key}:{identity}: field code/name mismatch")
    if field_detail not in _DETAIL_FIELD_NAMES[field_code]:
        raise BonghwaContractError(f"{partition.key}:{identity}: field detail changed")
    is_free_raw = strings["isFree"]
    if is_free_raw not in {"Y", "N"}:
        raise BonghwaContractError(f"{partition.key}:{identity}: invalid isFree")
    tuition = strings["eduTuition"]
    if tuition and not tuition.isdigit():
        raise BonghwaContractError(f"{partition.key}:{identity}: tuition shape changed")
    counts = {
        key: _positive_int(value[key], f"{partition.key}:{identity}:{key}")
        for key in (
            "appOnNum",
            "appOffNum",
            "appWaitNum",
            "appliedOnNum",
            "appliedOffNum",
            "appliedWaitNum",
        )
    }
    return _ListedCourse(
        partition=partition.key,
        page=page,
        identity=identity,
        title=title,
        source_status=source_status,
        application_type=application_type,
        apply_start=apply_start,
        apply_end=apply_end,
        event_start=event_start,
        event_end=event_end,
        day=strings["eduDayValue"],
        start_time=start_time,
        end_time=end_time,
        field_code=field_code,
        field_name=field_name,
        field_detail_name=field_detail,
        is_free=is_free_raw == "Y",
        tuition=tuition,
        material_cost=strings["eduCost"],
        online_capacity=counts["appOnNum"],
        offline_capacity=counts["appOffNum"],
        waitlist_capacity=counts["appWaitNum"],
        online_applied=counts["appliedOnNum"],
        offline_applied=counts["appliedOffNum"],
        waitlist_applied=counts["appliedWaitNum"],
    )


def _parse_json_page(
    data: Any,
    partition: BonghwaPartition,
    requested_page: int,
) -> _JsonPage:
    if not isinstance(data, Mapping) or set(data) != _JSON_TOP_KEYS:
        raise BonghwaContractError(f"{partition.key}: JSON top-level vocabulary changed")
    page_value = _positive_int(data["page"], "page")
    declared = _positive_int(data["programListCnt"], "programListCnt")
    list_order = data["listOrder"]
    if isinstance(list_order, bool) or not isinstance(list_order, int):
        raise BonghwaContractError(f"{partition.key}: listOrder type changed")
    if page_value != requested_page or list_order != declared - (requested_page - 1) * BONGHWA_PAGE_SIZE:
        raise BonghwaContractError(f"{partition.key}: JSON page/listOrder binding changed")
    raw_pagination = data["paginationInfo"]
    if not isinstance(raw_pagination, str):
        raise BonghwaContractError(f"{partition.key}: paginationInfo is no longer JSON text")
    try:
        pagination = json.loads(raw_pagination)
    except json.JSONDecodeError as exc:
        raise BonghwaContractError(f"{partition.key}: invalid paginationInfo") from exc
    if not isinstance(pagination, Mapping) or set(pagination) != _PAGINATION_KEYS:
        raise BonghwaContractError(f"{partition.key}: pagination vocabulary changed")
    values = {
        key: _positive_int(value, f"pagination.{key}")
        for key, value in pagination.items()
    }
    advertised_last = max(1, (declared + BONGHWA_PAGE_SIZE - 1) // BONGHWA_PAGE_SIZE)
    if (
        values["currentPageNo"] != requested_page
        or values["recordCountPerPage"] != BONGHWA_PAGE_SIZE
        or values["pageSize"] != BONGHWA_PAGE_SIZE
        or values["totalRecordCount"] != declared
        or values["totalPageCount"] != advertised_last
        or values["firstRecordIndex"] != (requested_page - 1) * BONGHWA_PAGE_SIZE
        or values["lastRecordIndex"] != requested_page * BONGHWA_PAGE_SIZE
    ):
        raise BonghwaContractError(f"{partition.key}: pagination values changed")
    raw_rows = data["programList"]
    if not isinstance(raw_rows, list):
        raise BonghwaContractError(f"{partition.key}: programList is not a list")
    rows = tuple(_parse_json_row(item, partition, requested_page) for item in raw_rows)
    identities = [item.identity for item in rows]
    if len(identities) != len(set(identities)):
        raise BonghwaContractError(f"{partition.key}: duplicate identity within page")
    return _JsonPage(
        partition=partition.key,
        requested_page=requested_page,
        declared_total=declared,
        advertised_last=advertised_last,
        rows=rows,
        pagination=tuple(sorted(values.items())),
    )


def _page_signature(page: _JsonPage) -> tuple[Any, ...]:
    return (
        page.partition,
        page.requested_page,
        page.declared_total,
        page.advertised_last,
        page.pagination,
        tuple(
            (
                item.identity,
                item.title,
                item.source_status,
                item.application_type,
                item.apply_start,
                item.apply_end,
                item.event_start,
                item.event_end,
                item.day,
                item.start_time,
                item.end_time,
                item.field_code,
                item.field_name,
                item.field_detail_name,
                item.is_free,
                item.tuition,
                item.material_cost,
                item.online_capacity,
                item.offline_capacity,
                item.waitlist_capacity,
                item.online_applied,
                item.offline_applied,
                item.waitlist_applied,
            )
            for item in page.rows
        ),
    )


def _fetch_json_page(
    session: Any,
    partition: BonghwaPartition,
    page: int,
    timeout: int,
    transport: Transport,
    meta: dict[str, Any],
) -> _JsonPage:
    data = _post_data(partition.key, page)
    payload = _fetch(
        session,
        "POST",
        BONGHWA_AJAX_URL,
        timeout,
        data,
        transport,
        meta,
    )
    return _parse_json_page(payload, partition, page)


def _collect_partition(
    session: Any,
    partition: BonghwaPartition,
    timeout: int,
    max_pages: int,
    transport: Transport,
    meta: dict[str, Any],
) -> _PartitionSnapshot:
    first = _fetch_json_page(session, partition, 1, timeout, transport, meta)
    if first.advertised_last > max_pages:
        meta["source_cap_reached"] = True
        raise BonghwaContractError(
            f"{partition.key}: advertised last {first.advertised_last} exceeds max_pages"
        )
    pages: list[_JsonPage] = [] if first.declared_total == 0 else [first]
    if first.declared_total == 0 and first.rows:
        raise BonghwaContractError(f"{partition.key}: zero-total partition returned rows")
    for number in range(2, first.advertised_last + 1):
        page = _fetch_json_page(session, partition, number, timeout, transport, meta)
        if page.declared_total != first.declared_total or page.advertised_last != first.advertised_last:
            raise BonghwaContractError(f"{partition.key}: pagination totals drifted")
        pages.append(page)
    sentinel_number = first.advertised_last + 1
    sentinel = _fetch_json_page(
        session, partition, sentinel_number, timeout, transport, meta
    )
    if (
        sentinel.rows
        or sentinel.declared_total != first.declared_total
        or sentinel.advertised_last != first.advertised_last
    ):
        raise BonghwaContractError(f"{partition.key}: post-last sentinel changed")
    for page in pages[:-1]:
        if len(page.rows) != BONGHWA_PAGE_SIZE:
            raise BonghwaContractError(f"{partition.key}: non-final page is not full")
    rows = tuple(item for page in pages for item in page.rows)
    expected_returned = first.declared_total - partition.audited_declared_deficit
    if len(rows) != expected_returned:
        raise BonghwaContractError(
            f"{partition.key}: declared {first.declared_total}, expected audited returned "
            f"{expected_returned}, got {len(rows)}"
        )
    if pages:
        expected_final = (
            first.declared_total
            - (first.advertised_last - 1) * BONGHWA_PAGE_SIZE
            - partition.audited_declared_deficit
        )
        if len(pages[-1].rows) != expected_final or expected_final < 1:
            raise BonghwaContractError(f"{partition.key}: final page boundary changed")
    identities = [item.identity for item in rows]
    if len(identities) != len(set(identities)):
        raise BonghwaContractError(f"{partition.key}: identity repeated across pages")
    return _PartitionSnapshot(
        partition=partition,
        first=first,
        pages=tuple(pages),
        sentinel=sentinel,
        rows=rows,
    )


def _detail_pairs(table: Any, identity: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in table.select("tbody tr"):
        children = tr.find_all(["th", "td"], recursive=False)
        for index, node in enumerate(children):
            if node.name != "th" or index + 1 >= len(children) or children[index + 1].name != "td":
                continue
            key, value = _text(node), _text(children[index + 1])
            if not key or key in pairs:
                raise BonghwaContractError(f"{identity}: duplicate/empty detail label")
            pairs[key] = value
    if not _DETAIL_REQUIRED_LABELS <= set(pairs) or not set(pairs) <= _DETAIL_LABELS:
        raise BonghwaContractError(f"{identity}: detail vocabulary changed")
    return pairs


def _detail_title(box: Any) -> str:
    clone = BeautifulSoup(str(box), "html.parser")
    for selector in (".organName", ".statusWrap"):
        node = clone.select_one(selector)
        if node is not None:
            node.decompose()
    return _text(clone)


def _detail_datetime_range(value: str, identity: str) -> tuple[date, date]:
    match = _DATETIME_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise BonghwaContractError(f"{identity}: detail application period changed")
    start, end = _parse_date(match.group(1), "detail application start"), _parse_date(
        match.group(3), "detail application end"
    )
    if end < start:
        raise BonghwaContractError(f"{identity}: reversed detail application period")
    return start, end


def _application_contract(
    soup: BeautifulSoup,
    listed: _ListedCourse,
) -> tuple[bool, str, str, str]:
    forms = soup.select("form#apply")
    if len(forms) != 1:
        raise BonghwaContractError(f"{listed.identity}: application form count changed")
    form = forms[0]
    action = urljoin(BONGHWA_CANONICAL_URL, _clean(form.get("action")))
    parsed = urlparse(action)
    if (
        _clean(form.get("method")).lower() != "get"
        or parsed.scheme != "https"
        or parsed.hostname != BONGHWA_HOST
        or parsed.path != BONGHWA_APPLICATION_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise BonghwaContractError(f"{listed.identity}: unsafe application form")
    inputs = form.select("input[type=hidden][name]")
    values: dict[str, str] = {}
    for node in inputs:
        name, value = _clean(node.get("name")), _clean(node.get("value"))
        if not name or name in values:
            raise BonghwaContractError(f"{listed.identity}: duplicate application hidden field")
        values[name] = value
    if set(values) != {"searchStage", "searchProgram", "searchAppProgram", "mid"}:
        raise BonghwaContractError(f"{listed.identity}: application hidden vocabulary changed")
    if (
        not _IDENTITY_RE.fullmatch(values["searchStage"])
        or not _IDENTITY_RE.fullmatch(values["searchProgram"])
        or values["searchAppProgram"] != listed.identity
        or values["mid"] != BONGHWA_MID
    ):
        raise BonghwaContractError(f"{listed.identity}: application identity binding changed")
    controls = [
        node
        for node in soup.select(".veiw-wrap .btn-wrap a, .veiw-wrap .btn-wrap button")
        if _text(node) == "신청하기"
    ]
    if len(controls) > 1:
        raise BonghwaContractError(f"{listed.identity}: multiple application controls")
    present = bool(controls)
    if present:
        control = controls[0]
        if (
            _clean(control.get("href")) != "javascript:void(0);"
            or "document.getElementById('apply').submit()" not in _clean(control.get("onclick"))
        ):
            raise BonghwaContractError(f"{listed.identity}: application control changed")
    if listed.partition == "ing" and not present:
        raise BonghwaContractError(f"{listed.identity}: open course lost application control")
    query = urlencode(
        {
            "searchStage": values["searchStage"],
            "searchProgram": values["searchProgram"],
            "searchAppProgram": values["searchAppProgram"],
            "mid": values["mid"],
        }
    )
    return present, f"{action}?{query}" if present else "", values["searchStage"], values["searchProgram"]


def _parse_detail(
    soup: BeautifulSoup,
    listed: _ListedCourse,
) -> dict[str, Any]:
    if _text(soup.title) != "평생학습강좌 | 수강신청 | 홈페이지":
        raise BonghwaContractError(f"{listed.identity}: detail owner title changed")
    wrappers = soup.select(".veiw-wrap")
    tables = soup.select(".veiw-wrap table.tbl")
    title_boxes = soup.select(".veiw-wrap .enrolment-tit")
    if len(wrappers) != 1 or len(tables) != 1 or len(title_boxes) != 1:
        raise BonghwaContractError(f"{listed.identity}: detail structure changed")
    title_box = title_boxes[0]
    if _detail_title(title_box) != listed.title:
        raise BonghwaContractError(f"{listed.identity}: list/detail title mismatch")
    category = listed.field_name + (
        f" | {listed.field_detail_name}" if listed.field_detail_name else ""
    )
    if _text(title_box.select_one(".organName span")) != category:
        raise BonghwaContractError(f"{listed.identity}: detail category mismatch")
    states = [_text(node) for node in title_box.select(".statusWrap p")]
    if states != [listed.application_type, listed.source_status]:
        raise BonghwaContractError(f"{listed.identity}: detail status mismatch")
    pairs = _detail_pairs(tables[0], listed.identity)
    if listed.application_type == "추첨" and "추첨예정일" not in pairs:
        raise BonghwaContractError(f"{listed.identity}: draw schedule is missing")
    if pairs["학습분야"] != category:
        raise BonghwaContractError(f"{listed.identity}: detail learning field mismatch")
    apply_start, apply_end = _detail_datetime_range(pairs["모집기간"], listed.identity)
    if apply_start != listed.apply_start or apply_end != listed.apply_end:
        raise BonghwaContractError(f"{listed.identity}: list/detail application dates mismatch")
    expected_period = f"{listed.event_start.isoformat()} ~ {listed.event_end.isoformat()}"
    if pairs["교육기간"] != expected_period:
        raise BonghwaContractError(f"{listed.identity}: list/detail education period mismatch")
    expected_time = f"{listed.day} ({listed.start_time}~{listed.end_time})"
    if re.sub(r"\s+", "", pairs["교육시간"]) != re.sub(r"\s+", "", expected_time):
        raise BonghwaContractError(f"{listed.identity}: list/detail education time mismatch")
    if _clean(pairs["재료비"]) != listed.material_cost:
        raise BonghwaContractError(f"{listed.identity}: list/detail material cost mismatch")
    target = pairs["모집대상"]
    room = pairs["교육장소"]
    if not target or not room or "평생학습관" not in room:
        raise BonghwaContractError(f"{listed.identity}: target/venue owner changed")
    control, application_url, stage_id, program_id = _application_contract(soup, listed)
    return {
        "target": target,
        "room": room,
        "application_control": control,
        "application_url": application_url,
        "application_stage_id": stage_id,
        "application_program_id": program_id,
    }


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def _row(
    target: Any,
    listed: _ListedCourse,
    detail: Mapping[str, Any],
) -> dict[str, Any]:
    control = bool(detail["application_control"])
    if control != (listed.partition == "ing"):
        raise BonghwaContractError(f"{listed.identity}: partition/application availability mismatch")
    status = "OPEN" if listed.partition == "ing" else "SCHEDULED"
    capacity_total = listed.online_capacity + listed.offline_capacity
    capacity_current = listed.online_applied + listed.offline_applied
    fee = "무료" if listed.is_free else (
        f"{int(listed.tuition):,}원" if listed.tuition else "유료"
    )
    category = listed.field_name + (
        f" | {listed.field_detail_name}" if listed.field_detail_name else ""
    )
    extra = _target_extra(target)
    output: dict[str, Any] = {
        "provider": BONGHWA_PROVIDER,
        "provider_course_id": f"{BONGHWA_PROVIDER}:programAppIdx:{listed.identity}",
        "title": listed.title,
        "branch": BONGHWA_BRANCH,
        "branch_code": f"{BONGHWA_PROVIDER}:lifelong_learning_center",
        "preserve_branch": True,
        "branch_url": BONGHWA_CANONICAL_URL,
        "raw_url": listed.detail_url,
        "application_url": _clean(detail["application_url"]),
        "application_type": "ONLINE_FORM" if control else "INFO_ONLY_DISABLED_CONTROL",
        "application_method_raw": "온라인 신청 (GET 동의 폼)" if control else "모집예정",
        "reservation_available": control,
        "status": status,
        "raw_status": listed.source_status,
        "period": f"{listed.event_start.isoformat()} ~ {listed.event_end.isoformat()}",
        "start_date": listed.event_start.isoformat(),
        "end_date": listed.event_end.isoformat(),
        "apply_period": f"{listed.apply_start.isoformat()} ~ {listed.apply_end.isoformat()}",
        "apply_start_date": listed.apply_start.isoformat(),
        "apply_end_date": listed.apply_end.isoformat(),
        "schedule_raw": f"{listed.day} · {listed.start_time}~{listed.end_time}",
        "target": _clean(detail["target"]),
        "capacity": f"{capacity_current} / {capacity_total}",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_current": listed.waitlist_applied,
        "waitlist_total": listed.waitlist_capacity,
        "fee": fee,
        "material_fee": listed.material_cost,
        "room": _clean(detail["room"]),
        "venue_name": BONGHWA_BRANCH,
        "address": BONGHWA_BRANCH_ADDRESS,
        "venue_address": BONGHWA_BRANCH_ADDRESS,
        "category": category,
        "collection_category": _clean(extra.get("collection_category") or "교육·체험"),
        "domain_category": _clean(extra.get("domain_category") or "평생학습"),
        "operator_type": _clean(extra.get("operator_type") or "지자체/공공기관"),
        "source_group": _clean(extra.get("source_group") or "lifelong_learning"),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "json_api+detail_html",
        "program_type": "교육",
        "municipality_code": BONGHWA_MUNICIPALITY_CODE,
        "municipality_name": BONGHWA_MUNICIPALITY_NAME,
        "municipality_full_name": BONGHWA_MUNICIPALITY_NAME,
        "description": listed.title,
        "raw_fields": {
            "parser": BONGHWA_PARSER,
            "identity": listed.identity,
            "partition": listed.partition,
            "source_page": listed.page,
            "source_status": listed.source_status,
            "source_application_type": listed.application_type,
            "source_field_code": listed.field_code,
            "source_field_name": listed.field_name,
            "source_field_detail_name": listed.field_detail_name,
            "online_capacity": listed.online_capacity,
            "offline_capacity": listed.offline_capacity,
            "waitlist_capacity": listed.waitlist_capacity,
            "online_applied": listed.online_applied,
            "offline_applied": listed.offline_applied,
            "waitlist_applied": listed.waitlist_applied,
            "application_stage_id": _clean(detail["application_stage_id"]),
            "application_program_id": _clean(detail["application_program_id"]),
            "detail_verified": True,
            "application_control_present": control,
            "application_control_verified": True,
            "application_endpoint_requested": False,
            "applicant_endpoint_requested": False,
            "attachment_endpoint_requested": False,
            "discarded_detail_fields": _DISCARDED_DETAIL_FIELDS,
            "privacy_policy": "structured_allowlist_no_instructor_body_contact_or_file",
        },
    }
    errors = _privacy_errors(output)
    if errors:
        raise BonghwaContractError("; ".join(errors))
    return output


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden PII/free-text row key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields allowlist exceeded")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "branch_url", "raw_fields"}
        }
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload) or _RESIDENT_RE.search(payload):
        errors.append("PII persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-text description persisted")
    if row.get("address") != BONGHWA_BRANCH_ADDRESS or row.get("venue_address") != BONGHWA_BRANCH_ADDRESS:
        errors.append("branch address binding changed")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _initial_meta() -> dict[str, Any]:
    return {
        "municipality_code": BONGHWA_MUNICIPALITY_CODE,
        "municipality_full_name": BONGHWA_MUNICIPALITY_NAME,
        "owner_provider": BONGHWA_PROVIDER,
        "canonical_provider": BONGHWA_PROVIDER,
        "canonical_candidate_id": BONGHWA_CANONICAL_CANDIDATE_ID,
        "canonical_url": BONGHWA_CANONICAL_URL,
        "official_branch": BONGHWA_BRANCH,
        "official_branch_address": BONGHWA_BRANCH_ADDRESS,
        "provider_reused": True,
        "new_provider_created": False,
        "duplicate_provider": BONGHWA_DUPLICATE_PROVIDER,
        "candidate_audit": {key: dict(value) for key, value in BONGHWA_CANDIDATE_AUDIT.items()},
        "non_executing_aliases": [dict(value) for value in BONGHWA_NON_EXECUTING_ALIASES],
        "parser": BONGHWA_PARSER,
        "recommended_max_pages": BONGHWA_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": BONGHWA_RECOMMENDED_DETAIL_LIMIT,
        "recommended_max_workers": BONGHWA_RECOMMENDED_MAX_WORKERS,
        "recommended_timeout_seconds": 30,
        "live_audit_baseline": dict(BONGHWA_LIVE_AUDIT_BASELINE),
        "logical_requests": 0,
        "physical_attempts": 0,
        "get_requests": 0,
        "post_requests": 0,
        "landing_verified": False,
        "partition_declared_counts": {},
        "partition_returned_counts": {},
        "partition_declared_deficits": {},
        "partition_data_pages": {},
        "partition_page_counts": {},
        "partition_sentinel_pages": {},
        "partition_first_rechecked": {},
        "partition_last_rechecked": {},
        "partition_sentinel_rechecked": {},
        "partition_union_count": 0,
        "partition_overlap_count": 0,
        "source_rows": 0,
        "source_status_counts": {},
        "current_source_count": 0,
        "historical_source_count": 0,
        "detail_pages": 0,
        "returned_count": 0,
        "application_control_count": 0,
        "application_endpoints_called": 0,
        "applicant_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "privacy_violations": 0,
        "source_cap_reached": False,
        "pages": 0,
        "discovered_links": 0,
        "reservation_discovery_links": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
    }


def collect_bonghwa_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = BONGHWA_RECOMMENDED_MAX_PAGES,
    detail_limit: int = BONGHWA_RECOMMENDED_DETAIL_LIMIT,
    max_workers: int = BONGHWA_RECOMMENDED_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    transport: Optional[Transport] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one stable, complete, privacy-safe Bonghwa snapshot."""

    meta = _initial_meta()
    if not is_bonghwa_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match incumbent Bonghwa provider on exact canonical URL"
        )
        return [], BONGHWA_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], BONGHWA_PARSER, meta
        session_factory = _default_session_factory
    try:
        cutoff = _today(today)
        if any(
            isinstance(value, bool) or int(value) < 1
            for value in (timeout, max_pages, max_workers)
        ):
            raise ValueError("timeout, max_pages, and max_workers must be positive integers")
        if isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("detail_limit must be a non-negative integer")
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], BONGHWA_PARSER, meta

    current_transport = transport or _request
    session = session_factory()
    try:
        landing = _fetch(
            session,
            "GET",
            BONGHWA_CANONICAL_URL,
            int(timeout),
            None,
            current_transport,
            meta,
        )
        _parse_landing(landing)
        meta["landing_verified"] = True

        snapshots: dict[str, _PartitionSnapshot] = {}
        for partition in BONGHWA_PARTITIONS:
            snapshot = _collect_partition(
                session,
                partition,
                int(timeout),
                int(max_pages),
                current_transport,
                meta,
            )
            snapshots[partition.key] = snapshot
            meta["partition_declared_counts"][partition.key] = snapshot.first.declared_total
            meta["partition_returned_counts"][partition.key] = len(snapshot.rows)
            meta["partition_declared_deficits"][partition.key] = (
                snapshot.first.declared_total - len(snapshot.rows)
            )
            meta["partition_data_pages"][partition.key] = len(snapshot.pages)
            meta["partition_page_counts"][partition.key] = [
                len(page.rows) for page in snapshot.pages
            ]
            meta["partition_sentinel_pages"][partition.key] = (
                snapshot.sentinel.requested_page
            )

        identity_owner: dict[str, str] = {}
        overlap = 0
        all_listed: list[_ListedCourse] = []
        for partition in BONGHWA_PARTITIONS:
            for item in snapshots[partition.key].rows:
                if item.identity in identity_owner:
                    overlap += 1
                else:
                    identity_owner[item.identity] = partition.key
                all_listed.append(item)
        if overlap:
            raise BonghwaContractError("wait/ing/end partitions overlap")
        current_listed = [
            item
            for partition in BONGHWA_PARTITIONS
            if partition.current
            for item in snapshots[partition.key].rows
        ]
        if any(item.event_end < cutoff for item in current_listed):
            raise BonghwaContractError("current partition contains an expired education row")
        if len(current_listed) > int(detail_limit):
            meta["source_cap_reached"] = True
            raise BonghwaContractError(
                f"detail_limit {detail_limit} below required {len(current_listed)}"
            )
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "pages": sum(len(snapshot.pages) + 1 for snapshot in snapshots.values()),
                "discovered_links": len(all_listed),
                "pagination_detected": any(
                    snapshot.first.advertised_last > 1
                    for snapshot in snapshots.values()
                ),
                "partition_union_count": len(identity_owner),
                "partition_overlap_count": overlap,
                "source_rows": len(all_listed),
                "source_status_counts": dict(Counter(item.source_status for item in all_listed)),
                "current_source_count": len(current_listed),
                "historical_source_count": len(all_listed) - len(current_listed),
                "pagination_complete": True,
            }
        )

        rows: list[dict[str, Any]] = []
        lock = Lock()

        def fetch_detail(item: _ListedCourse) -> dict[str, Any]:
            detail_session = session_factory()
            try:
                soup = _fetch(
                    detail_session,
                    "GET",
                    item.detail_url,
                    int(timeout),
                    None,
                    current_transport,
                    meta,
                    lock,
                )
                detail = _parse_detail(soup, item)
                return _row(target, item, detail)
            finally:
                close_detail = getattr(detail_session, "close", None)
                if callable(close_detail):
                    close_detail()

        if current_listed:
            with ThreadPoolExecutor(
                max_workers=min(int(max_workers), len(current_listed))
            ) as executor:
                futures = [executor.submit(fetch_detail, item) for item in current_listed]
                for future in as_completed(futures):
                    rows.append(future.result())
                    meta["detail_pages"] += 1

        for partition in BONGHWA_PARTITIONS:
            snapshot = snapshots[partition.key]
            first_recheck = _fetch_json_page(
                session,
                partition,
                1,
                int(timeout),
                current_transport,
                meta,
            )
            if _page_signature(first_recheck) != _page_signature(snapshot.first):
                raise BonghwaContractError(f"{partition.key}: first-page stability failed")
            meta["partition_first_rechecked"][partition.key] = True
            if snapshot.first.advertised_last == 1:
                meta["partition_last_rechecked"][partition.key] = True
            else:
                last_recheck = _fetch_json_page(
                    session,
                    partition,
                    snapshot.first.advertised_last,
                    int(timeout),
                    current_transport,
                    meta,
                )
                if _page_signature(last_recheck) != _page_signature(snapshot.pages[-1]):
                    raise BonghwaContractError(f"{partition.key}: last-page stability failed")
                meta["partition_last_rechecked"][partition.key] = True
            sentinel_recheck = _fetch_json_page(
                session,
                partition,
                snapshot.sentinel.requested_page,
                int(timeout),
                current_transport,
                meta,
            )
            if _page_signature(sentinel_recheck) != _page_signature(snapshot.sentinel):
                raise BonghwaContractError(f"{partition.key}: sentinel stability failed")
            meta["partition_sentinel_rechecked"][partition.key] = True

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _dedupe)(rows))
        expected_ids = {
            f"{BONGHWA_PROVIDER}:programAppIdx:{item.identity}"
            for item in current_listed
        }
        if len(rows) != len(current_listed) or {
            _clean(row.get("provider_course_id")) for row in rows
        } != expected_ids:
            raise BonghwaContractError("dedupe changed the current identity set")
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        if privacy_errors:
            meta["privacy_violations"] = len(privacy_errors)
            raise BonghwaContractError("; ".join(privacy_errors[:5]))
        meta.update(
            {
                "returned_count": len(rows),
                "details_complete": meta["detail_pages"] == len(current_listed),
                "status_counts": dict(Counter(_clean(row["status"]) for row in rows)),
                "category_counts": dict(Counter(_clean(row["category"]) for row in rows)),
                "branch_counts": dict(Counter(_clean(row["branch"]) for row in rows)),
                "application_control_count": sum(
                    bool(row["reservation_available"]) for row in rows
                ),
                "reservation_discovery_links": sum(
                    bool(row.get("application_url")) for row in rows
                ),
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not rows,
                "no_current_reason": (
                    "official wait and ing partitions are empty"
                    if not rows
                    else ""
                ),
            }
        )
        return rows, BONGHWA_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], BONGHWA_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_bonghwa_education
