"""Complete, fail-closed collector for Namyangju resident-centre education.

The incumbent ``jumin.nyj.go.kr`` provider owns the ``edc_prgm_no`` identity
namespace.  Namyangju's municipal reservation portal republishes the same
rows and links every card back to this ledger, so it is an alias rather than a
second owner.  This collector requests only the public landing page, paged
JSON list API, and public course-detail pages.  It never requests application,
login, applicant, attachment, image, download, or personal-information routes.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import re
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


NAMYANGJU_PROVIDER = "MUNI_JUMIN_NYJ_GO_KR_4D92ADDF"
NAMYANGJU_LEGACY_CANDIDATE_ID = "MUNI_IR_D0E167F9683F"
NAMYANGJU_CANONICAL_CANDIDATE_ID = "MUNI_IR_8AB617A3E5EC"
NAMYANGJU_MUNICIPAL_MIRROR_CANDIDATE_ID = "MUNI_IR_C46F1518F7AA"
NAMYANGJU_STATIC_NOTICE_PROVIDER = "MUNI_WWW_NYJ_GO_KR_DDD40BD4"
NAMYANGJU_MUNICIPALITY_CODE = "4136000000"
NAMYANGJU_MUNICIPALITY_NAME = "경기도 남양주시"

NAMYANGJU_HOST = "jumin.nyj.go.kr"
NAMYANGJU_CANONICAL_PATH = "/web/edc/program/list"
NAMYANGJU_API_PATH = "/web/edc/program/list2Ajax"
NAMYANGJU_DETAIL_PREFIX = "/web/edc/program/"
NAMYANGJU_CANONICAL_URL = f"https://{NAMYANGJU_HOST}{NAMYANGJU_CANONICAL_PATH}"
NAMYANGJU_API_URL = f"https://{NAMYANGJU_HOST}{NAMYANGJU_API_PATH}"
NAMYANGJU_MUNICIPAL_MIRROR_URL = (
    "https://www.nyj.go.kr/reserve/selectAutonomousProgramListU.do?key=3351"
)
NAMYANGJU_LEGACY_DETAIL_URL = "https://jumin.nyj.go.kr/web/edc/program/1788"
NAMYANGJU_STATIC_NOTICE_URL = (
    "https://www.nyj.go.kr/www/selectBbsNttView.do?key=2498&bbsNo=68&nttNo=432741"
)

NAMYANGJU_PAGE_SIZE = 10
NAMYANGJU_RECOMMENDED_MAX_PAGES = 200
NAMYANGJU_RECOMMENDED_DETAIL_LIMIT = 1_500
NAMYANGJU_RECOMMENDED_MAX_WORKERS = 8
NAMYANGJU_MAX_WORKERS = 12
NAMYANGJU_MAX_JSON_BYTES = 2_000_000
NAMYANGJU_MAX_HTML_BYTES = 4_000_000
NAMYANGJU_MAX_SOURCE_ROWS = 10_000

NAMYANGJU_PARSER = (
    "namyangju_jumin_owner+edc_prgm_no_identity+all_json_pages+empty_sentinel+"
    "stable_boundaries+all_current_details+official_branch_registry+"
    "municipal_mirror_excluded+no_private_endpoints"
)

NAMYANGJU_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "owner": {
        "provider": NAMYANGJU_PROVIDER,
        "candidate_id": NAMYANGJU_CANONICAL_CANDIDATE_ID,
        "url": NAMYANGJU_CANONICAL_URL,
        "decision": "retain_incumbent_jumin_edc_prgm_no_owner",
    },
    "legacy_detail_seed": {
        "candidate_id": NAMYANGJU_LEGACY_CANDIDATE_ID,
        "url": NAMYANGJU_LEGACY_DETAIL_URL,
        "decision": "retarget_same_provider_to_canonical_complete_list",
    },
    "municipal_reservation_projection": {
        "candidate_id": NAMYANGJU_MUNICIPAL_MIRROR_CANDIDATE_ID,
        "url": NAMYANGJU_MUNICIPAL_MIRROR_URL,
        "decision": "exclude_exact_projection_of_jumin_identity_ledger",
    },
    "municipal_jumin_redirect": {
        "url": "https://www.nyj.go.kr/jumin",
        "decision": "exclude_redirect_alias_to_jumin_root",
    },
    "static_press_release": {
        "provider": NAMYANGJU_STATIC_NOTICE_PROVIDER,
        "url": NAMYANGJU_STATIC_NOTICE_URL,
        "decision": "disable_information_only_2023_press_release",
    },
    "municipal_online_applications": {
        "url": "https://www.nyj.go.kr/reserve/selectOnlineRceptListU.do?key=4222",
        "decision": "separate_application_ledger_not_resident_centre_alias",
    },
    "dasan_lifelong_learning": {
        "url": "https://nyjedu.gseek.kr/",
        "decision": "separate_provincial_platform_not_resident_centre_alias",
    },
}

# This is the exact registry rendered by the canonical public search form.
NAMYANGJU_BRANCH_REGISTRY = (
    ("", "전체"),
    ("18", "와부읍 주민자치센터"),
    ("19", "진접읍 주민자치센터"),
    ("20", "화도읍 주민자치센터"),
    ("21", "진건읍 주민자치센터"),
    ("22", "오남읍 주민자치센터"),
    ("23", "퇴계원읍주민자치센터"),
    ("24", "별내면 주민자치센터"),
    ("25", "수동면 주민자치센터"),
    ("26", "조안면 주민자치센터"),
    ("27", "호평동 주민자치센터"),
    ("28", "평내동 주민자치센터"),
    ("17", "금곡동 주민자치센터"),
    ("29", "양정동 주민자치센터"),
    ("30", "다산1동 주민자치센터"),
    ("31", "다산2동 주민자치센터"),
    ("32", "별내동 주민자치센터"),
    ("40", "남양주시"),
)
NAMYANGJU_BRANCH_BY_ORG = dict(NAMYANGJU_BRANCH_REGISTRY[1:])

NAMYANGJU_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-07-23T07:38:00+09:00",
    "cutoff": "2026-07-23",
    "source_total": 1418,
    "pages": 142,
    "last_page_rows": 8,
    "current_source_count": 1037,
    "source_status_counts": {"종료": 1262, "접수중": 140, "마감": 16},
    "current_source_status_counts": {"종료": 891, "접수중": 130, "마감": 16},
    "current_branch_counts": {
        "화도읍 주민자치센터": 132,
        "다산1동 주민자치센터": 117,
        "와부읍 주민자치센터": 92,
        "진접읍 주민자치센터": 92,
        "별내동 주민자치센터": 89,
        "다산2동 주민자치센터": 69,
        "평내동 주민자치센터": 69,
        "진건읍 주민자치센터": 62,
        "오남읍 주민자치센터": 62,
        "호평동 주민자치센터": 62,
        "별내면 주민자치센터": 52,
        "퇴계원읍주민자치센터": 49,
        "금곡동 주민자치센터": 39,
        "수동면 주민자치센터": 26,
        "조안면 주민자치센터": 14,
        "양정동 주민자치센터": 11,
    },
    "source_identity_sha256": "28e9e13c75bbcba3a507d24f2f396c13bfb37b930c3dcf49252139b08cae5726",
    "current_identity_sha256": "89a4c3fed7063b31700ca82b156a17a23ae74800a058a696ad79f4fc9f786f42",
    "two_live_detail_runs_identical": True,
}


_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"[1-9]\d*")
_DATE8_RE = re.compile(r"20\d{6}")
_FILE_PATH_RE = re.compile(r"/web/common/file/view/20\d{4}/EDC_\d+")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_STATUS_MAP = {
    "접수중": "OPEN",
    "접수전": "SCHEDULED",
    "준비": "SCHEDULED",
    "마감": "CLOSED",
    "접수마감": "CLOSED",
    "강제마감": "CLOSED",
    "종료": "CLOSED",
}
_AUDITED_STATUS_CLASS = {"접수중": "01", "마감": "07", "종료": "09"}
_ACCESS_METHOD = {"1001": "온라인", "2001": "방문", "3001": "온라인+방문"}
_CATEGORY_NAMES = {"", "문화교양", "어학.정보화", "유아.아동", "생활체육", "헬스", "문화강좌"}

_DETAIL_HEADERS = (
    "교육기관",
    "접수방법",
    "모집인원",
    "신청/모집인원",
    "신청기간",
    "교육기간",
    "강사명",
    "교육장소",
    "교육시간",
    "교육비",
    "강의계획서",
    "문의전화",
    "강좌소개",
    "특이사항 (준비물, 기타사항 등)",
    "교육대상",
    "주소",
)

_LIST_PARAM_ITEMS = (
    ("searchKeyword", ""),
    ("strOrgNo", ""),
    ("searchOrgNo", ""),
    ("areaCd", "0"),
    ("searchCondition", ""),
    ("searchTab", "EDCANM"),
    ("searchOnOff", "ALL"),
    ("searchHurry", ""),
    ("scrollVar", ""),
    ("usePagingYn", "Y"),
    ("orgltype", ""),
    ("orgMtype", ""),
    ("strOrgMtype", ""),
    ("strCtgCd", ""),
    ("CtgCd", ""),
    ("edcTargetinfo", ""),
    ("strEdcTargetinfo", ""),
    ("strEdcStatus", ""),
    ("hourbandGbn", ""),
    ("strHourbandGbn", ""),
    ("dayGbn", ""),
    ("strDayGbn", ""),
    ("strEdcFeeType", ""),
    ("searchOrderBy", ""),
)


class NamyangjuContractError(RuntimeError):
    """Raised when the audited public contract changes."""


@dataclass(frozen=True)
class _ListedCourse:
    identity: str
    source_identity: str
    rnum: int
    title: str
    branch: str
    org_no: str
    source_status: str
    status: str
    status_class: str
    event_start: date
    event_end: date
    apply_start: date
    apply_end: date
    day: str
    time: str
    room: str
    target: str
    category: str
    category_code: str
    area_code: str
    area_name: str
    fee_amount: int
    capacity: int
    access_code: str
    application_method: str
    reservation_type: str
    reservation_set: str
    plan_file_id: str
    detail_url: str


@dataclass(frozen=True)
class _Page:
    requested: int
    declared_total: int
    rows: tuple[_ListedCourse, ...]
    sentinel: bool = False


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return _SPACE_RE.sub(" ", str(value).replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _strict_url(url: Any, path: str) -> bool:
    parsed = urlparse(_clean(url))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == NAMYANGJU_HOST
        and port is None
        and not parsed.username
        and not parsed.password
        and parsed.path == path
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def is_namyangju_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == NAMYANGJU_PROVIDER
        and _strict_url(_target_value(target, "url"), NAMYANGJU_CANONICAL_PATH)
    )


is_target = is_namyangju_education_target


def namyangju_source_identity(identity: Any) -> str:
    value = _clean(identity)
    if not _POSITIVE_ID_RE.fullmatch(value):
        raise ValueError("invalid Namyangju edc_prgm_no")
    return f"{NAMYANGJU_PROVIDER}:edc_prgm_no:{value}"


def namyangju_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _POSITIVE_ID_RE.fullmatch(value):
        raise ValueError("invalid Namyangju edc_prgm_no")
    return f"https://{NAMYANGJU_HOST}{NAMYANGJU_DETAIL_PREFIX}{value}"


def namyangju_list_params(page: int) -> dict[str, str]:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    return {"pageIndex": str(page), **dict(_LIST_PARAM_ITEMS)}


def namyangju_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 Chrome/138.0",
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.5",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": NAMYANGJU_CANONICAL_URL,
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return current


def _default_fetcher(
    current: Any,
    method: str,
    url: str,
    *,
    timeout: int,
    params: Optional[Mapping[str, str]] = None,
) -> Any:
    if method != "GET":
        raise NamyangjuContractError("refusing unaudited HTTP method")
    return current.get(
        url,
        params=params,
        timeout=timeout,
        allow_redirects=False,
    )


def _allowed_request(
    method: str,
    url: str,
    params: Optional[Mapping[str, str]],
) -> bool:
    if method != "GET":
        return False
    if url == NAMYANGJU_CANONICAL_URL:
        return params is None
    if url == NAMYANGJU_API_URL:
        if not isinstance(params, Mapping):
            return False
        page_text = _clean(params.get("pageIndex"))
        if not _POSITIVE_ID_RE.fullmatch(page_text):
            return False
        return dict(params) == namyangju_list_params(int(page_text))
    parsed = urlparse(url)
    if params is not None or parsed.query or parsed.fragment or parsed.params:
        return False
    if not _strict_url(url, parsed.path):
        return False
    identity = parsed.path.removeprefix(NAMYANGJU_DETAIL_PREFIX)
    return (
        parsed.path.startswith(NAMYANGJU_DETAIL_PREFIX)
        and _POSITIVE_ID_RE.fullmatch(identity) is not None
        and parsed.path == f"{NAMYANGJU_DETAIL_PREFIX}{identity}"
    )


def _response_bytes(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    return str(getattr(response, "text", response)).encode("utf-8")


def _response_contract(
    response: Any,
    requested_url: str,
    params: Optional[Mapping[str, str]],
    expected_content: str,
) -> bytes:
    status = int(getattr(response, "status_code", 200))
    if status != 200 or getattr(response, "history", None):
        raise NamyangjuContractError(f"HTTP/redirect changed: {status}")
    headers = getattr(response, "headers", {}) or {}
    if headers.get("Location") or headers.get("location"):
        raise NamyangjuContractError("redirect changed")
    content_type = _clean(headers.get("Content-Type", expected_content)).lower()
    if expected_content not in content_type:
        raise NamyangjuContractError(f"unexpected content type: {content_type}")
    observed_url = _clean(getattr(response, "url", requested_url) or requested_url)
    observed = urlparse(observed_url)
    requested = urlparse(requested_url)
    if (
        observed.scheme != requested.scheme
        or observed.hostname != requested.hostname
        or observed.port != requested.port
        or observed.path != requested.path
        or observed.fragment
    ):
        raise NamyangjuContractError("response URL changed")
    if params is None:
        if observed.query:
            raise NamyangjuContractError("response query changed")
    else:
        try:
            observed_query = parse_qsl(
                observed.query,
                keep_blank_values=True,
                strict_parsing=True,
            )
        except ValueError as exc:
            raise NamyangjuContractError("response query changed") from exc
        if observed_query and observed_query != list(params.items()):
            raise NamyangjuContractError("response query changed")
    content = _response_bytes(response)
    maximum = NAMYANGJU_MAX_JSON_BYTES if expected_content == "json" else NAMYANGJU_MAX_HTML_BYTES
    if not content or len(content) > maximum:
        raise NamyangjuContractError("empty/oversize response")
    return content


def _parse_date8(value: Any, identity: str, field: str) -> date:
    text = _clean(value)
    if not _DATE8_RE.fullmatch(text):
        raise NamyangjuContractError(f"course {identity}: invalid {field}")
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise NamyangjuContractError(f"course {identity}: invalid {field}") from exc


def _nonnegative_int(value: Any, identity: str, field: str) -> int:
    if isinstance(value, bool):
        raise NamyangjuContractError(f"course {identity}: invalid {field}")
    text = _clean(value).replace(",", "")
    if not re.fullmatch(r"\d+", text):
        raise NamyangjuContractError(f"course {identity}: invalid {field}")
    return int(text)


def _parse_listed(item: Mapping[str, Any], total: int) -> _ListedCourse:
    identity = _clean(item.get("edc_prgm_no"))
    if not _POSITIVE_ID_RE.fullmatch(identity):
        raise NamyangjuContractError("list identity changed")
    title = _clean(item.get("edc_prgm_nm"))
    org_no = _clean(item.get("org_no"))
    branch = _clean(item.get("org_name"))
    if not title or NAMYANGJU_BRANCH_BY_ORG.get(org_no) != branch:
        raise NamyangjuContractError(f"course {identity}: official branch changed")
    source_status = _clean(item.get("edc_status"))
    status_class = _clean(item.get("edc_status_class"))
    if source_status not in _STATUS_MAP or not re.fullmatch(r"\d{2}", status_class):
        raise NamyangjuContractError(f"course {identity}: status changed")
    audited_class = _AUDITED_STATUS_CLASS.get(source_status)
    if audited_class and status_class != audited_class:
        raise NamyangjuContractError(f"course {identity}: status class changed")
    event_start = _parse_date8(item.get("edc_sdate"), identity, "education start")
    event_end = _parse_date8(item.get("edc_edate"), identity, "education end")
    apply_start = _parse_date8(item.get("edc_rsvn_sdate"), identity, "application start")
    apply_end = _parse_date8(item.get("edc_rsvn_edate"), identity, "application end")
    if event_end < event_start or apply_end < apply_start:
        raise NamyangjuContractError(f"course {identity}: reversed dates")
    rnum = _nonnegative_int(item.get("rnum"), identity, "rnum")
    if rnum < 1 or _nonnegative_int(item.get("tot_count"), identity, "tot_count") != total:
        raise NamyangjuContractError(f"course {identity}: list count changed")
    access_code = _clean(item.get("edc_rsvn_accssrd"))
    if access_code not in _ACCESS_METHOD:
        raise NamyangjuContractError(f"course {identity}: access method changed")
    reservation_type = _clean(item.get("rsvn_type_nm"))
    if reservation_type not in {"선착접수", "선착마감대기"}:
        raise NamyangjuContractError(f"course {identity}: reservation type changed")
    category = _clean(item.get("ctg_nm"))
    if category not in _CATEGORY_NAMES:
        raise NamyangjuContractError(f"course {identity}: category changed")
    reservation_set = _clean(item.get("edc_rsvnset_seq"))
    if not re.fullmatch(r"20\d{4}", reservation_set):
        raise NamyangjuContractError(f"course {identity}: reservation set changed")
    day = _clean(item.get("edc_day_gbn_nm"))
    time_text = _clean(item.get("edc_time"))
    room = _clean(item.get("edc_place_nm"))
    if not day or not time_text:
        raise NamyangjuContractError(f"course {identity}: schedule changed")
    return _ListedCourse(
        identity=identity,
        source_identity=namyangju_source_identity(identity),
        rnum=rnum,
        title=title,
        branch=branch,
        org_no=org_no,
        source_status=source_status,
        status=_STATUS_MAP[source_status],
        status_class=status_class,
        event_start=event_start,
        event_end=event_end,
        apply_start=apply_start,
        apply_end=apply_end,
        day=day,
        time=time_text,
        room=room,
        target=_clean(item.get("target_name")),
        category=category or "주민자치센터 교육",
        category_code=_clean(item.get("ctg_cd")),
        area_code=_clean(item.get("area_cd")),
        area_name=_clean(item.get("area_nm")),
        fee_amount=_nonnegative_int(item.get("sale_amt"), identity, "fee"),
        capacity=_nonnegative_int(item.get("edc_pncpa"), identity, "capacity"),
        access_code=access_code,
        application_method=f"{_ACCESS_METHOD[access_code]}/{reservation_type}",
        reservation_type=reservation_type,
        reservation_set=reservation_set,
        plan_file_id=_clean(item.get("edc_plan_fileid")),
        detail_url=namyangju_detail_url(identity),
    )


def _decode_payload(content: bytes) -> Mapping[str, Any]:
    try:
        payload: Any = json.loads(content.decode("utf-8-sig"))
        if isinstance(payload, str):
            payload = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NamyangjuContractError("list JSON changed") from exc
    if not isinstance(payload, Mapping) or payload.get("result") is not True:
        raise NamyangjuContractError("list result changed")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise NamyangjuContractError("list data changed")
    return data


def _parse_page(
    response: Any,
    requested_page: int,
    *,
    expected_total: Optional[int] = None,
    sentinel: bool = False,
) -> _Page:
    params = namyangju_list_params(requested_page)
    content = _response_contract(response, NAMYANGJU_API_URL, params, "json")
    data = _decode_payload(content)
    pagination = data.get("pagination")
    contents = data.get("contents")
    if not isinstance(pagination, Mapping) or not isinstance(contents, list):
        raise NamyangjuContractError(f"page {requested_page}: response shape changed")
    total = _nonnegative_int(pagination.get("TotalRecordCount"), "list", "declared total")
    if sentinel:
        if total != 0 or contents:
            raise NamyangjuContractError("empty sentinel changed")
        return _Page(requested_page, total, (), True)
    if total < 1 or total > NAMYANGJU_MAX_SOURCE_ROWS:
        raise NamyangjuContractError(f"page {requested_page}: declared total changed")
    if expected_total is not None and total != expected_total:
        raise NamyangjuContractError(f"page {requested_page}: declared total drift")
    last = math.ceil(total / NAMYANGJU_PAGE_SIZE)
    expected_count = NAMYANGJU_PAGE_SIZE if requested_page < last else total % NAMYANGJU_PAGE_SIZE or NAMYANGJU_PAGE_SIZE
    if requested_page > last or len(contents) != expected_count:
        raise NamyangjuContractError(f"page {requested_page}: row count changed")
    rows = tuple(_parse_listed(item, total) for item in contents if isinstance(item, Mapping))
    if len(rows) != len(contents):
        raise NamyangjuContractError(f"page {requested_page}: non-object row")
    expected_rnums = set(
        range(
            (requested_page - 1) * NAMYANGJU_PAGE_SIZE + 1,
            (requested_page - 1) * NAMYANGJU_PAGE_SIZE + 1 + len(rows),
        )
    )
    if {row.rnum for row in rows} != expected_rnums:
        raise NamyangjuContractError(f"page {requested_page}: rnum coverage changed")
    identities = [row.source_identity for row in rows]
    if len(identities) != len(set(identities)):
        raise NamyangjuContractError(f"page {requested_page}: duplicate identities")
    return _Page(requested_page, total, rows)


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.declared_total,
        page.sentinel,
        tuple(
            (
                row.source_identity,
                row.title,
                row.branch,
                row.source_status,
                row.event_start,
                row.event_end,
                row.capacity,
            )
            for row in page.rows
        ),
    )


def _parse_landing(response: Any) -> None:
    content = _response_contract(response, NAMYANGJU_CANONICAL_URL, None, "html")
    soup = BeautifulSoup(content, "html.parser")
    forms = soup.select("form#searchVO[name='searchVO']")
    if len(forms) != 1 or _clean(forms[0].get("method")).lower() != "get":
        raise NamyangjuContractError("canonical search form changed")
    form = forms[0]
    defaults = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[name]")
    }
    expected_defaults = {"pageIndex": "1", **dict(_LIST_PARAM_ITEMS)}
    for key, expected in expected_defaults.items():
        if defaults.get(key) != expected:
            raise NamyangjuContractError(f"canonical search default changed: {key}")
    observed: list[tuple[str, str]] = []
    for node in soup.select("form#searchSmart input[name='sOrgNo']"):
        node_id = _clean(node.get("id"))
        label = soup.find("label", attrs={"for": node_id}) if node_id else None
        if label is None:
            raise NamyangjuContractError("official branch label changed")
        observed.append((_clean(node.get("value")), _clean(label.get_text(" ", strip=True))))
    if tuple(observed) != NAMYANGJU_BRANCH_REGISTRY:
        raise NamyangjuContractError("official branch registry changed")


def _cell_text(cell: Any) -> str:
    clone_soup = BeautifulSoup(str(cell), "html.parser")
    clone = clone_soup.find("td")
    if clone is None:
        return ""
    for node in clone.select(".tooltip,script,style"):
        node.decompose()
    return _clean(clone.get_text(" ", strip=True))


def _detail_pairs(table: Any) -> tuple[dict[str, str], dict[str, Any]]:
    pairs: dict[str, str] = {}
    cells: dict[str, Any] = {}
    ordered: list[str] = []
    for tr in table.select("tr"):
        children = tr.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(children):
            if (
                children[index].name != "th"
                or index + 1 >= len(children)
                or children[index + 1].name != "td"
            ):
                raise NamyangjuContractError("detail table pairing changed")
            key = _clean(children[index].get_text(" ", strip=True))
            if not key or key in pairs:
                raise NamyangjuContractError("detail table labels changed")
            ordered.append(key)
            pairs[key] = _cell_text(children[index + 1])
            cells[key] = children[index + 1]
            index += 2
    if tuple(ordered) != _DETAIL_HEADERS:
        raise NamyangjuContractError("detail field registry changed")
    return pairs, cells


def _detail_amount(text: str) -> Optional[int]:
    matches = re.findall(r"\d[\d,]*\s*원", text)
    if not matches:
        return 0 if "무료" in text else None
    return int(re.sub(r"\D", "", matches[-1]))


def _validate_attachment(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != NAMYANGJU_HOST:
            return False
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return (
        _FILE_PATH_RE.fullmatch(parsed.path) is not None
        and len(query) == 1
        and query[0][0] == "originName"
        and bool(query[0][1])
        and not parsed.fragment
    )


def _parse_detail(response: Any, item: _ListedCourse) -> Counter[str]:
    content = _response_contract(response, item.detail_url, None, "html")
    soup = BeautifulSoup(content, "html.parser")
    roots = soup.select(".myTable")
    if len(roots) != 1:
        raise NamyangjuContractError(f"course {item.identity}: detail root changed")
    root = roots[0]
    headings = root.select("h3.myTable-title")
    tables = root.select(":scope > .myTable-inner > .myTable-wrap > table")
    if len(headings) != 1 or len(tables) != 1:
        raise NamyangjuContractError(f"course {item.identity}: detail heading/table changed")
    title_node = headings[0].select_one(":scope > p")
    status_node = headings[0].select_one(":scope > .bedge")
    if (
        title_node is None
        or status_node is None
        or _clean(title_node.get_text(" ", strip=True)) != item.title
        or _clean(status_node.get_text(" ", strip=True)) != item.source_status
    ):
        raise NamyangjuContractError(f"course {item.identity}: title/status drift")
    pairs, cells = _detail_pairs(tables[0])
    if pairs["교육기관"] != item.branch:
        raise NamyangjuContractError(f"course {item.identity}: official branch drift")
    capacity_values = [int(value.replace(",", "")) for value in re.findall(r"\d[\d,]*", pairs["모집인원"])]
    applied_values = [int(value.replace(",", "")) for value in re.findall(r"\d[\d,]*", pairs["신청/모집인원"])]
    if capacity_values != [item.capacity] or len(applied_values) != 2 or applied_values[-1] != item.capacity:
        raise NamyangjuContractError(f"course {item.identity}: capacity drift")
    if item.room and pairs["교육장소"] != item.room:
        raise NamyangjuContractError(f"course {item.identity}: place drift")
    expected_schedule = f"{item.day} / {item.time}"
    if not pairs["교육시간"].startswith(expected_schedule):
        raise NamyangjuContractError(f"course {item.identity}: schedule drift")
    if item.target and pairs["교육대상"] != item.target:
        raise NamyangjuContractError(f"course {item.identity}: target drift")
    if _detail_amount(pairs["교육비"]) != item.fee_amount:
        raise NamyangjuContractError(f"course {item.identity}: fee drift")

    attachment_links = cells["강의계획서"].select("a[href]")
    if len(attachment_links) > 1:
        raise NamyangjuContractError(f"course {item.identity}: attachment count drift")
    for link in attachment_links:
        if not _validate_attachment(_clean(link.get("href"))):
            raise NamyangjuContractError(f"course {item.identity}: attachment route changed")

    button_groups = soup.select(".badge-btn")
    if len(button_groups) != 1:
        raise NamyangjuContractError(f"course {item.identity}: action controls changed")
    controls = button_groups[0].find_all("a", recursive=False)
    if len(controls) != 2 or _clean(controls[0].get("href")) != "javascript:history.back();":
        raise NamyangjuContractError(f"course {item.identity}: list control changed")
    action = controls[1]
    action_href = _clean(action.get("href"))
    action_classes = set(action.get("class") or [])
    if item.source_status == "접수중" and item.access_code in {"1001", "3001"}:
        valid_action = action_href == "javascript:fnDetailApply();" and action_classes == {"green"}
        application_controls = 1
    elif item.source_status == "접수중" and item.access_code == "2001":
        valid_action = action_href == "#none" and action_classes == {"red"}
        application_controls = 0
    else:
        valid_action = action_href == "#none" and bool(action_classes & {"gray", "blue"})
        application_controls = 0
    if not valid_action:
        raise NamyangjuContractError(f"course {item.identity}: application control changed")

    scripts = "\n".join(node.get_text(" ", strip=False) for node in soup.select("script"))
    identity = re.escape(item.identity)
    reservation_set = re.escape(item.reservation_set)
    identity_patterns = (
        rf"data\.edcPrgmNo\s*=\s*['\"]?{identity}['\"]?\s*;",
        rf"data\.edcRsvnsetSeq\s*=\s*['\"]?{reservation_set}['\"]?\s*;",
        rf"\.\./rsvn/termsAgree/{identity}/{reservation_set}",
        rf"\.\./rsvn/termsAgreeAjax/{identity}/{reservation_set}",
    )
    if any(re.search(pattern, scripts) is None for pattern in identity_patterns):
        raise NamyangjuContractError(f"course {item.identity}: application identity drift")

    sensitive_nonempty = sum(
        bool(pairs[key])
        for key in ("강사명", "문의전화", "강좌소개", "특이사항 (준비물, 기타사항 등)", "주소")
    )
    return Counter(
        application_controls=application_controls,
        attachment_fields=len(attachment_links),
        stale_plan_file_ids=int(bool(item.plan_file_id) and not attachment_links),
        unindexed_attachment_fields=int(bool(attachment_links) and not item.plan_file_id),
        sensitive_detail_fields=sensitive_nonempty,
        pii_modal_fields=len(soup.select("table.table-check")),
    )


def _row(item: _ListedCourse) -> dict[str, Any]:
    fee = "무료" if item.fee_amount == 0 else f"{item.fee_amount:,}원"
    return {
        "provider": NAMYANGJU_PROVIDER,
        "municipality_code": NAMYANGJU_MUNICIPALITY_CODE,
        "municipality_name": NAMYANGJU_MUNICIPALITY_NAME,
        "provider_course_id": item.source_identity,
        "source_course_id": item.identity,
        "title": item.title,
        "status": item.status,
        "source_status": item.source_status,
        "start_date": item.event_start.isoformat(),
        "end_date": item.event_end.isoformat(),
        "apply_start_date": item.apply_start.isoformat(),
        "apply_end_date": item.apply_end.isoformat(),
        "schedule": f"{item.day} / {item.time}",
        "branch": item.branch,
        "venue": item.room,
        "category": item.category,
        "target": item.target,
        "fee": fee,
        "capacity": item.capacity,
        "application_method": item.application_method,
        "source_url": item.detail_url,
        "application_url": "",
        "raw_fields": {
            "org_no": item.org_no,
            "area_code": item.area_code,
            "area_name": item.area_name,
            "category_code": item.category_code,
            "status_class": item.status_class,
            "reservation_set": item.reservation_set,
        },
    }


def _dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _privacy_violations(rows: Iterable[Mapping[str, Any]]) -> int:
    forbidden = {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "teacher",
        "attachments",
        "description",
        "source_html",
    }
    violations = 0
    for row in rows:
        violations += len(set(row) & forbidden)
        text = " ".join(
            _clean(row.get(key))
            for key in ("title", "branch", "venue", "category", "target")
        )
        violations += len(_PHONE_RE.findall(text)) + len(_EMAIL_RE.findall(text))
    return violations


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _initial_meta(cutoff: date) -> dict[str, Any]:
    return {
        "provider": NAMYANGJU_PROVIDER,
        "municipality_code": NAMYANGJU_MUNICIPALITY_CODE,
        "audit_date": cutoff.isoformat(),
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "landing_requests": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "reservation_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_values_persisted": 0,
        "discovered_links": 0,
        "pagination_detected": False,
        "source_rows": 0,
        "returned_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
    }


def collect_namyangju_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = NAMYANGJU_RECOMMENDED_MAX_PAGES,
    detail_limit: int = NAMYANGJU_RECOMMENDED_DETAIL_LIMIT,
    max_workers: int = NAMYANGJU_RECOMMENDED_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[Callable[[], Any]] = None,
    fetcher: Optional[Callable[..., Any]] = None,
    dedupe_rows: Optional[Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one all-pages, all-current-details Namyangju snapshot."""

    try:
        cutoff = _today(today)
    except Exception:
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
        meta = _initial_meta(cutoff)
        meta["configured_collection_error"] = "today is invalid"
        return [], NAMYANGJU_PARSER, meta
    meta = _initial_meta(cutoff)
    if not is_namyangju_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match incumbent Namyangju provider on exact canonical URL"
        )
        return [], NAMYANGJU_PARSER, meta
    try:
        numeric = (timeout, max_pages, detail_limit, max_workers)
        if any(isinstance(value, bool) for value in numeric):
            raise ValueError
        timeout, max_pages, detail_limit, max_workers = map(int, numeric)
        if timeout < 1 or max_pages < 1 or detail_limit < 0 or max_workers < 1:
            raise ValueError
        if max_workers > NAMYANGJU_MAX_WORKERS:
            raise ValueError
    except Exception:
        meta["configured_collection_error"] = "invalid collection limits"
        return [], NAMYANGJU_PARSER, meta

    factory = session_factory or namyangju_session_factory
    transport = fetcher or _default_fetcher
    lock = Lock()
    listed: list[_ListedCourse] = []

    def load(
        url: str,
        kind: str,
        params: Optional[Mapping[str, str]] = None,
    ) -> Any:
        if not _allowed_request("GET", url, params):
            raise NamyangjuContractError("refusing unaudited route")
        with lock:
            meta["logical_requests"] += 1
            meta[f"{kind}_requests" if kind != "detail" else "detail_pages"] += 1
        current = factory()
        try:
            last_error: Optional[Exception] = None
            for attempt in range(2):
                with lock:
                    meta["physical_requests"] += 1
                try:
                    response = transport(
                        current,
                        "GET",
                        url,
                        timeout=timeout,
                        params=params,
                    )
                    status = int(getattr(response, "status_code", 200))
                    if status in {429, 500, 502, 503, 504} and attempt == 0:
                        with lock:
                            meta["request_retry_count"] += 1
                        continue
                    return response
                except requests.RequestException as exc:
                    last_error = exc
                    if attempt == 0:
                        with lock:
                            meta["request_retry_count"] += 1
                        continue
                    raise
            raise NamyangjuContractError(f"request failed: {last_error}")
        finally:
            close = getattr(current, "close", None)
            if callable(close):
                close()

    try:
        _parse_landing(load(NAMYANGJU_CANONICAL_URL, "landing"))
        meta["landing_verified"] = True

        first = _parse_page(
            load(NAMYANGJU_API_URL, "list", namyangju_list_params(1)),
            1,
        )
        total = first.declared_total
        last = math.ceil(total / NAMYANGJU_PAGE_SIZE)
        sentinel_number = last + 1
        recheck_numbers = sorted({1, last, sentinel_number})
        required_list_requests = last + 1 + len(recheck_numbers)
        meta["required_list_requests"] = required_list_requests
        if required_list_requests > max_pages:
            meta["source_cap_reached"] = True
            raise NamyangjuContractError(
                f"max_pages cap allows {max_pages} of {required_list_requests} list requests"
            )

        pages: dict[int, _Page] = {1: first}

        def fetch_page(page: int) -> _Page:
            response = load(NAMYANGJU_API_URL, "list", namyangju_list_params(page))
            return _parse_page(
                response,
                page,
                expected_total=total,
                sentinel=page == sentinel_number,
            )

        requested = list(range(2, sentinel_number + 1))
        if requested:
            with ThreadPoolExecutor(
                max_workers=min(max_workers, len(requested)),
                thread_name_prefix="namyangju-list",
            ) as executor:
                futures = {executor.submit(fetch_page, page): page for page in requested}
                for future in as_completed(futures):
                    page = futures[future]
                    pages[page] = future.result()

        sentinel = pages[sentinel_number]
        for page in range(1, last + 1):
            listed.extend(pages[page].rows)
        identities = [item.source_identity for item in listed]
        if len(listed) != total or len(identities) != len(set(identities)):
            raise NamyangjuContractError("complete page union changed")
        if {item.rnum for item in listed} != set(range(1, total + 1)):
            raise NamyangjuContractError("complete rnum coverage changed")
        current_rows = [item for item in listed if item.event_end >= cutoff]
        if len(current_rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise NamyangjuContractError(
                f"detail_limit {detail_limit} below required {len(current_rows)}"
            )
        meta.update(
            {
                "pages": last,
                "page_counts": {page: len(pages[page].rows) for page in range(1, last + 1)},
                "source_total": total,
                "source_rows": total,
                "source_identity_count": len(identities),
                "discovered_links": len(identities),
                "source_identity_sha256": hashlib.sha256(
                    "\n".join(sorted(identities)).encode("utf-8")
                ).hexdigest(),
                "source_status_counts": dict(Counter(item.source_status for item in listed)),
                "current_source_count": len(current_rows),
                "expired_count": total - len(current_rows),
                "current_source_status_counts": dict(
                    Counter(item.source_status for item in current_rows)
                ),
                "sentinel_page": sentinel_number,
                "sentinel_rows": len(sentinel.rows),
                "pagination_detected": last > 1,
                "pagination_complete": True,
            }
        )

        detail_counters = Counter()

        def fetch_detail(item: _ListedCourse) -> tuple[_ListedCourse, Counter[str]]:
            response = load(item.detail_url, "detail")
            return item, _parse_detail(response, item)

        verified: list[_ListedCourse] = []
        if current_rows:
            with ThreadPoolExecutor(
                max_workers=min(max_workers, len(current_rows)),
                thread_name_prefix="namyangju-detail",
            ) as executor:
                futures = [executor.submit(fetch_detail, item) for item in current_rows]
                for future in as_completed(futures):
                    item, counters = future.result()
                    verified.append(item)
                    detail_counters.update(counters)

        originals = {1: first, last: pages[last], sentinel_number: sentinel}
        boundary_rechecks: dict[str, bool] = {}
        for page in recheck_numbers:
            response = load(NAMYANGJU_API_URL, "list", namyangju_list_params(page))
            observed = _parse_page(
                response,
                page,
                expected_total=total,
                sentinel=page == sentinel_number,
            )
            stable = _page_signature(observed) == _page_signature(originals[page])
            boundary_rechecks[str(page)] = stable
            if not stable:
                raise NamyangjuContractError(f"page {page}: boundary stability changed")

        output = [_row(item) for item in verified]
        output.sort(key=lambda row: (row["start_date"], row["title"], row["provider_course_id"]))
        deduped = list((dedupe_rows or _dedupe)(output))
        if len(deduped) != len(output):
            raise NamyangjuContractError("dedupe changed complete output")
        output_ids = {_clean(row.get("provider_course_id")) for row in deduped}
        expected_ids = {item.source_identity for item in current_rows}
        if output_ids != expected_ids:
            raise NamyangjuContractError("current identity coverage changed")
        privacy = _privacy_violations(deduped)
        meta["pii_values_persisted"] = privacy
        if privacy:
            raise NamyangjuContractError("PII allowlist violation")
        meta.update(
            {
                "detail_verified": len(verified),
                "details_complete": True,
                "boundary_rechecks": boundary_rechecks,
                "current_identity_sha256": hashlib.sha256(
                    "\n".join(sorted(expected_ids)).encode("utf-8")
                ).hexdigest(),
                "status_counts": dict(Counter(row["status"] for row in deduped)),
                "branch_counts": dict(Counter(row["branch"] for row in deduped)),
                "application_control_count": detail_counters["application_controls"],
                "attachment_fields_discarded": detail_counters["attachment_fields"],
                "stale_plan_file_ids": detail_counters["stale_plan_file_ids"],
                "unindexed_attachment_fields": detail_counters[
                    "unindexed_attachment_fields"
                ],
                "sensitive_detail_fields_discarded": detail_counters[
                    "sensitive_detail_fields"
                ],
                "pii_modal_fields_discarded": detail_counters["pii_modal_fields"],
                "returned_count": len(deduped),
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not deduped,
                "no_current_reason": (
                    "the complete official Namyangju catalogue contains no current courses"
                    if not deduped
                    else ""
                ),
            }
        )
        return deduped, NAMYANGJU_PARSER, meta
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
        return [], NAMYANGJU_PARSER, meta


collect = collect_namyangju_education


__all__ = [name for name in globals() if name.startswith("NAMYANGJU_")] + [
    "NamyangjuContractError",
    "collect",
    "collect_namyangju_education",
    "is_namyangju_education_target",
    "is_target",
    "namyangju_detail_url",
    "namyangju_list_params",
    "namyangju_source_identity",
]
