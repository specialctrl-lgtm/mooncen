"""Fail-closed collector for Seosan City's complete education ledger.

The reviewed ``www.seosan.go.kr/learning`` URL is the lifelong-learning
navigation home.  Its official enrolment shortcut opens the existing Seosan
integrated-reservation service.  The complete public education ledger is the
unpartitioned ``key=2`` list on ``total.seosan.go.kr``.  The legacy production
provider already owns several subsets of that same ledger, so this collector
deliberately preserves that provider instead of creating a duplicate owner.

Every declared list page, the immediate empty post-last page, and stable
first/last/sentinel rechecks are required.  Only current/future course details
are fetched.  Application, login, applicant, attachment, download and other
PII-bearing endpoints are never requested.  Instructor/contact/free-form
detail values are discarded, and any contract drift suppresses the snapshot.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# Preserve the incumbent production owner.  The reviewed homepage-derived
# provider is an alias candidate, not a second owner for the same courses.
SEOSAN_PROVIDER = "SEOSAN_WELFARE_TOTAL_RESERVATION"
SEOSAN_REVIEW_PROVIDER = "MUNI_WWW_SEOSAN_GO_KR_D18127D4"
SEOSAN_CANONICAL_DERIVED_PROVIDER_NOT_TO_CREATE = "MUNI_TOTAL_SEOSAN_GO_KR_F5ACE4CA"
SEOSAN_CHUNGNAM_PROVIDER = "MUNI_WWW_CHUNGNAM_GO_KR_57C753B7"

SEOSAN_HOME_CANDIDATE_ID = "MUNI_IR_B588388CAD68"
SEOSAN_CHUNGNAM_CANDIDATE_ID = "MUNI_IR_55277C6BA9C1"
SEOSAN_CANONICAL_CANDIDATE_ID = "MUNI_IR_CB54B9EA91C9"
SEOSAN_MUNICIPALITY_CODE = "4421000000"
SEOSAN_MUNICIPALITY_NAME = "충청남도 서산시"

SEOSAN_HOST = "total.seosan.go.kr"
SEOSAN_LIST_PATH = "/total/selectEdcAtrCourseListU.do"
SEOSAN_DETAIL_PATH = "/total/selectEdcAtrCourseViewU.do"
SEOSAN_APPLICATION_PATH = "/total/addEdcAtrViewU.do"
SEOSAN_LOGIN_PATHS = frozenset(
    {
        "/total/loginView.do",
        "/total/nameLoginView.do",
        "/total/selectEdcAtrListM.do",
    }
)
SEOSAN_CANONICAL_URL = f"https://{SEOSAN_HOST}{SEOSAN_LIST_PATH}?key=2"
SEOSAN_LEARNING_HOME_URL = "https://www.seosan.go.kr/learning"
SEOSAN_CHUNGNAM_DIRECTORY_URL = (
    "https://www.chungnam.go.kr/cnportal/main/contents.do?menuNo=5100141"
)
SEOSAN_FACILITY_RESERVATION_URL = "https://yeyak.seosan.go.kr/"
SEOSAN_PAGE_SIZE = 50
SEOSAN_MAX_HTML_BYTES = 3_000_000
SEOSAN_MAX_PAGES = 200
SEOSAN_PARSER = (
    "seosan_incumbent_integrated_owner+complete_key2_education_ledger+"
    "declared_pages+immediate_empty_sentinel+stable_first_last_sentinel+"
    "current_future_details+exact_official_institution+application_no_fetch+"
    "pii_attachment_free_text_allowlist"
)

SEOSAN_LEGACY_PARTITION_URLS = (
    "https://total.seosan.go.kr/total/selectEdcAtrCourseListU.do?key=326",
    "https://total.seosan.go.kr/total/selectEdcAtrCourseListU.do?key=326&searchInsttCode=02&cl1No=29",
    "https://total.seosan.go.kr/total/selectEdcAtrCourseListU.do?key=327&searchInsttCode=02&cl1No=30",
    "https://total.seosan.go.kr/total/selectEdcAtrCourseListU.do?key=186&searchInsttCode=01&cl1No=25",
    "https://total.seosan.go.kr/total/selectEdcAtrCourseListU.do?key=187&searchInsttCode=01&cl1No=26",
    "https://total.seosan.go.kr/total/selectEdcAtrCourseListU.do?key=248&searchInsttCode=04&cl1No=46",
    "https://total.seosan.go.kr/total/selectEdcAtrCourseListU.do?key=249&searchInsttCode=04&cl1No=245",
    "https://total.seosan.go.kr/total/selectEdcAtrCourseListU.do?key=246&searchInsttCode=05&cl1No=55",
    "https://total.seosan.go.kr/total/selectEdcAtrCourseListU.do?key=406&searchInsttCode=06&cl1No=57",
)

SEOSAN_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "reviewed_lifelong_home": {
        "candidate_id": SEOSAN_HOME_CANDIDATE_ID,
        "provider": SEOSAN_REVIEW_PROVIDER,
        "url": SEOSAN_LEARNING_HOME_URL,
        "decision": "retarget_navigation_home_to_incumbent_integrated_owner",
        "owner": SEOSAN_PROVIDER,
    },
    "canonical_complete_education_ledger": {
        "candidate_id": SEOSAN_CANONICAL_CANDIDATE_ID,
        "provider": SEOSAN_PROVIDER,
        "url": SEOSAN_CANONICAL_URL,
        "decision": "reuse_incumbent_provider_for_complete_unpartitioned_ledger",
        "new_provider_created": False,
    },
    "legacy_partition_lists": {
        "provider": SEOSAN_PROVIDER,
        "url": SEOSAN_LEGACY_PARTITION_URLS[0],
        "decision": "replace_incomplete_overlapping_subsets_with_canonical_ledger",
    },
    "chungnam_municipality_profile": {
        "candidate_id": SEOSAN_CHUNGNAM_CANDIDATE_ID,
        "provider": SEOSAN_CHUNGNAM_PROVIDER,
        "url": SEOSAN_CHUNGNAM_DIRECTORY_URL,
        "decision": "exclude_separate_provincial_profile_without_course_ledger",
    },
    "facility_and_sports_reservation": {
        "url": SEOSAN_FACILITY_RESERVATION_URL,
        "decision": "exclude_separate_facility_and_sports_owner",
    },
}

# These are exact official ``기관명`` values audited in the integrated service.
# A new institution changes the branch/ownership contract and therefore needs
# review rather than being silently folded into an existing branch.
SEOSAN_OFFICIAL_INSTITUTIONS: Mapping[str, str] = {
    "평생학습관": "SEOSAN_LIFELONG_LEARNING",
    "종합사회복지관": "SEOSAN_SOCIAL_WELFARE",
    "시립도서관": "SEOSAN_CITY_LIBRARY",
    "어린이도서관": "SEOSAN_CHILDREN_LIBRARY",
    "농업기술센터": "SEOSAN_AGRICULTURAL_TECHNOLOGY",
    "스마트정보과": "SEOSAN_SMART_INFORMATION",
}

_LIST_HEADERS = ("강좌 정보", "상태")
_LIST_FIELD_LABELS = frozenset(
    {
        "교육기간",
        "교육요일",
        "교육시간",
        "접수기간",
        "접수방식",
        "모집인원",
        "정원",
        "접수완료",
        "대기",
    }
)
_LIST_REQUIRED_FIELDS = frozenset(
    {"교육기간", "교육요일", "교육시간", "접수기간", "접수방식", "모집인원"}
)
_DETAIL_REQUIRED_FIELDS = frozenset(
    {
        "기관명",
        "강좌명",
        "기수",
        "접수기간",
        "접수방식",
        "모집인원",
        "선발방식",
        "대기인원",
        "강사명",
        "교육기간",
        "총교육일",
        "교육시간",
        "교육대상",
        "수강료",
        "교육장소",
        "강의개요",
        "교재 및 참고자료",
        "강의계획서",
        "수강신청 유의사항",
    }
)
_DETAIL_OPTIONAL_FIELDS = frozenset({"재료비", "교육과정문의", "담당자", "문의전화", "첨부파일"})
_DETAIL_SENSITIVE_FIELDS = frozenset(
    {"강사명", "교육과정문의", "담당자", "문의전화"}
)
_DETAIL_FREE_TEXT_FIELDS = frozenset(
    {"강의개요", "교재 및 참고자료", "수강신청 유의사항"}
)
_DETAIL_ATTACHMENT_FIELDS = frozenset({"강의계획서", "첨부파일"})
_STATUS_MAP = {
    "접수중": "OPEN",
    "대기접수": "WAITING",
    "대기신청": "WAITING",
    "접수예정": "SCHEDULED",
    "신청마감": "CLOSED",
    "접수마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
}
_APPLICATION_STATUSES = frozenset({"접수중", "대기접수", "대기신청"})

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d{0,11}$")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_INTEGER_RE = re.compile(r"(\d[\d,]*)")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CANCELLED_RE = re.compile(r"(?:폐강|강좌\s*취소|운영\s*취소)")

SessionFactory = Callable[[], Any]
HtmlFetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class SeosanContractError(RuntimeError):
    """Raised when the audited public Seosan contract changes."""


@dataclass(frozen=True)
class _Page:
    number: int
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _strict_url(value: Any, *, path: str, query: list[tuple[str, str]]) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
        actual_query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SEOSAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == path
        and actual_query == query
        and not parsed.fragment
    )


def is_seosan_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == SEOSAN_PROVIDER
        and _strict_url(
            _target_value(target, "url"), path=SEOSAN_LIST_PATH, query=[("key", "2")]
        )
    )


is_target = is_seosan_education_target


def seosan_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    query = [
        ("key", "2"),
        ("pageUnit", str(SEOSAN_PAGE_SIZE)),
        ("searchInsttCode", ""),
        ("cl1No", ""),
        ("cl2No", ""),
        ("searchKrwd", ""),
        ("searchIngSttus", ""),
        ("pageIndex", str(page)),
    ]
    return f"https://{SEOSAN_HOST}{SEOSAN_LIST_PATH}?{urlencode(query)}"


def seosan_detail_url(identity: Any, page: int = 1) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise ValueError("invalid Seosan course identity")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    query = [
        ("key", "2"),
        ("edcCourseNo", value),
        ("cl1No", ""),
        ("cl2No", ""),
        ("pageUnit", str(SEOSAN_PAGE_SIZE)),
        ("searchInsttCode", ""),
        ("searchKrwd", ""),
        ("pageIndex", str(page)),
    ]
    return f"https://{SEOSAN_HOST}{SEOSAN_DETAIL_PATH}?{urlencode(query)}"


def seosan_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _safe_request_url(url: str, kind: str) -> None:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SeosanContractError("malformed request URL") from exc
    expected_path = SEOSAN_LIST_PATH if kind == "list" else SEOSAN_DETAIL_PATH
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == SEOSAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == expected_path
        and not parsed.fragment
    ):
        raise SeosanContractError(f"refusing unsafe {kind} request URL")


def _coerce_soup(value: Any, requested_url: str) -> BeautifulSoup:
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise SeosanContractError(f"unexpected HTTP status {status}")
    if getattr(value, "history", None):
        raise SeosanContractError("redirect history is not accepted")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location") or headers.get("location"):
        raise SeosanContractError("redirect response is not accepted")
    content_type = _clean(headers.get("Content-Type") or headers.get("content-type"))
    if content_type and "html" not in content_type.lower():
        raise SeosanContractError("official response is not HTML")
    final_url = _clean(getattr(value, "url", requested_url) or requested_url)
    expected = urlparse(requested_url)
    actual = urlparse(final_url)
    try:
        expected_query = parse_qsl(expected.query, keep_blank_values=True, strict_parsing=True)
        actual_query = parse_qsl(actual.query, keep_blank_values=True, strict_parsing=True)
        expected_port = expected.port
        actual_port = actual.port
    except (TypeError, ValueError) as exc:
        raise SeosanContractError("malformed official response URL") from exc
    if not (
        actual.scheme == expected.scheme == "https"
        and (actual.hostname or "").lower() == SEOSAN_HOST
        and (expected.hostname or "").lower() == SEOSAN_HOST
        and actual_port is expected_port is None
        and actual.username is None
        and actual.password is None
        and actual.path == expected.path
        and actual_query == expected_query
        and not actual.fragment
    ):
        raise SeosanContractError("official response URL changed")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if not content:
        raise SeosanContractError("empty official response")
    if len(content) > SEOSAN_MAX_HTML_BYTES:
        raise SeosanContractError("HTML size cap exceeded")
    return BeautifulSoup(content, "html.parser")


def _dates(value: Any, *, identity: str, field: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise SeosanContractError(f"course {identity}: {field} must contain two dates")
    start, end = (date(int(year), int(month), int(day)) for year, month, day in matches)
    if end < start:
        raise SeosanContractError(f"course {identity}: reversed {field}")
    return start, end


def _positive_integer(value: Any, *, identity: str, field: str, allow_zero: bool = True) -> int:
    match = _INTEGER_RE.search(_clean(value))
    if match is None:
        raise SeosanContractError(f"course {identity}: {field} count missing")
    result = int(match.group(1).replace(",", ""))
    if result < 0 or (not allow_zero and result == 0):
        raise SeosanContractError(f"course {identity}: invalid {field} count")
    return result


def _validate_course_url(url: str, *, path: str, identity: str, page: int) -> dict[str, list[str]]:
    parsed = urlparse(url)
    try:
        port = parsed.port
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError) as exc:
        raise SeosanContractError(f"course {identity}: malformed course control URL") from exc
    required = {
        "key",
        "edcCourseNo",
        "cl1No",
        "cl2No",
        "pageUnit",
        "searchInsttCode",
        "searchKrwd",
        "pageIndex",
    }
    if path == SEOSAN_APPLICATION_PATH:
        required |= {"searchCnd"}
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == SEOSAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == path
        and set(query) == required
        and all(len(values) == 1 for values in query.values())
        and query["key"] == ["2"]
        and query["edcCourseNo"] == [identity]
        and (
            query["pageUnit"] in (["10"], [str(SEOSAN_PAGE_SIZE)])
            if path == SEOSAN_APPLICATION_PATH
            else query["pageUnit"] == [str(SEOSAN_PAGE_SIZE)]
        )
        and query["pageIndex"] == [str(page)]
        and not parsed.fragment
    ):
        raise SeosanContractError(f"course {identity}: course identity/path drift")
    if path == SEOSAN_APPLICATION_PATH and query["searchCnd"] != ["all"]:
        raise SeosanContractError(f"course {identity}: application search scope changed")
    return query


def _status_control(cell: Any, *, identity: str, page: int) -> tuple[str, str, str]:
    source_status = _clean(cell.get_text(" ", strip=True))
    status = _STATUS_MAP.get(source_status)
    if status is None:
        raise SeosanContractError(f"course {identity}: unknown status {source_status!r}")
    anchors = cell.select("a[href]")
    if source_status not in _APPLICATION_STATUSES:
        if not anchors:
            return status, "", source_status
        if (
            source_status != "신청마감"
            or len(anchors) != 1
            or _clean(anchors[0].get_text(" ", strip=True)) != source_status
        ):
            raise SeosanContractError(f"course {identity}: inactive status exposes application")
        inert_url = urljoin(
            SEOSAN_CANONICAL_URL,
            _clean(anchors[0].get("href")),
        )
        _validate_course_url(
            inert_url,
            path=SEOSAN_APPLICATION_PATH,
            identity=identity,
            page=page,
        )
        return status, "", source_status
    if len(anchors) != 1 or _clean(anchors[0].get_text(" ", strip=True)) != source_status:
        raise SeosanContractError(f"course {identity}: application control changed")
    application_url = urljoin(SEOSAN_CANONICAL_URL, _clean(anchors[0].get("href")))
    _validate_course_url(
        application_url, path=SEOSAN_APPLICATION_PATH, identity=identity, page=page
    )
    return status, application_url, source_status


def _list_fields(container: Any, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for node in container.select(":scope > span"):
        text = _clean(node.get_text(" ", strip=True))
        if ":" not in text:
            if text not in {"|", "온라인 모집현황"}:
                raise SeosanContractError(f"course {identity}: unknown list fragment {text!r}")
            continue
        label, value = (_clean(part) for part in text.split(":", 1))
        if label not in _LIST_FIELD_LABELS or not value or label in fields:
            raise SeosanContractError(f"course {identity}: list field contract changed")
        fields[label] = value
    if not _LIST_REQUIRED_FIELDS <= set(fields):
        missing = sorted(_LIST_REQUIRED_FIELDS - set(fields))
        raise SeosanContractError(f"course {identity}: list fields missing {missing}")
    return fields


def _parse_list_row(row: Any, page: int) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 2:
        raise SeosanContractError(f"page {page}: list row cell count changed")
    subject = cells[0].select_one(":scope > .p-subject")
    detail = cells[0].select_one(":scope > .detail_info")
    anchors = subject.select('a[href*="selectEdcAtrCourseViewU.do"]') if subject else []
    badges = subject.select(":scope > .p-badge") if subject else []
    if detail is None or len(anchors) != 1 or len(badges) != 2:
        raise SeosanContractError(f"page {page}: course card structure changed")
    title = _clean(anchors[0].get_text(" ", strip=True))
    detail_url = urljoin(SEOSAN_CANONICAL_URL, _clean(anchors[0].get("href")))
    parsed = urlparse(detail_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _clean((query.get("edcCourseNo") or [""])[0])
    if not title or not _IDENTITY_RE.fullmatch(identity):
        raise SeosanContractError(f"page {page}: course identity/title missing")
    _validate_course_url(detail_url, path=SEOSAN_DETAIL_PATH, identity=identity, page=page)
    fields = _list_fields(detail, identity)
    event_start, event_end = _dates(fields["교육기간"], identity=identity, field="education period")
    apply_start, apply_end = _dates(fields["접수기간"], identity=identity, field="application period")
    capacity_total = _positive_integer(fields["모집인원"], identity=identity, field="capacity")
    online_capacity = (
        _positive_integer(fields["정원"], identity=identity, field="online capacity")
        if "정원" in fields
        else None
    )
    applicants = (
        _positive_integer(fields["접수완료"], identity=identity, field="applicants")
        if "접수완료" in fields
        else None
    )
    waitlist = (
        _positive_integer(fields["대기"], identity=identity, field="waitlist")
        if "대기" in fields
        else None
    )
    if set(fields) & {"정원", "접수완료", "대기"} and not {
        "정원",
        "접수완료",
        "대기",
    } <= set(fields):
        raise SeosanContractError(f"course {identity}: partial online-capacity fields")
    status, application_url, source_status = _status_control(
        cells[1], identity=identity, page=page
    )
    return {
        "identity": identity,
        "page": page,
        "title": title,
        "detail_url": detail_url,
        "target_badge": _clean(badges[0].get_text(" ", strip=True)),
        "fee_badge": _clean(badges[1].get_text(" ", strip=True)),
        "event_start": event_start,
        "event_end": event_end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "education_day": fields["교육요일"],
        "education_time": fields["교육시간"],
        "application_method": fields["접수방식"],
        "capacity_total": capacity_total,
        "online_capacity": online_capacity,
        "applicants": applicants,
        "waitlist": waitlist,
        "status": status,
        "source_status": source_status,
        "application_url": application_url,
    }


def _parse_list_page(soup: BeautifulSoup, page: int) -> _Page:
    root = soup.select_one("#template4")
    if root is None:
        raise SeosanContractError(f"page {page}: canonical content root missing")
    forms = root.select('form#searchForm[name="searchForm"]')
    if len(forms) != 1 or _clean(forms[0].get("method")).upper() != "POST":
        raise SeosanContractError(f"page {page}: canonical search form changed")
    form = forms[0]
    action = urljoin(SEOSAN_CANONICAL_URL, _clean(form.get("action")))
    if urlparse(action).path != SEOSAN_LIST_PATH:
        raise SeosanContractError(f"page {page}: search form action changed")
    values = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[name]")
    }
    if {key: values.get(key) for key in ("key", "cl1No", "cl2No", "searchInsttCode")} != {
        "key": "2",
        "cl1No": "",
        "cl2No": "",
        "searchInsttCode": "",
    }:
        raise SeosanContractError(f"page {page}: unpartitioned search scope changed")
    units = form.select_one('select[name="pageUnit"]')
    options = tuple(_clean(node.get("value")) for node in units.select("option")) if units else ()
    selected = tuple(
        _clean(node.get("value")) for node in (units.select("option[selected]") if units else ())
    )
    if options != ("10", "20", "30", "40", "50") or selected != (str(SEOSAN_PAGE_SIZE),):
        raise SeosanContractError(f"page {page}: page-size contract changed")
    count = root.select_one(".bbs_count")
    strong = count.select("strong") if count else []
    if len(strong) != 2:
        raise SeosanContractError(f"page {page}: declared pagination missing")
    total_text = _clean(strong[0].get_text(" ", strip=True))
    current_text = _clean(strong[1].get_text(" ", strip=True))
    count_text = _clean(count.get_text(" ", strip=True)) if count else ""
    total_match = re.fullmatch(r"\d[\d,]*", total_text)
    ratio_match = re.search(r"페이지\s+(\d[\d,]*)\s*/\s*(\d[\d,]*)", count_text)
    if total_match is None or ratio_match is None:
        raise SeosanContractError(f"page {page}: malformed declared pagination")
    total = int(total_text.replace(",", ""))
    if total < 1:
        raise SeosanContractError("canonical education ledger unexpectedly has no rows")
    last = math.ceil(total / SEOSAN_PAGE_SIZE)
    if current_text != str(page) or int(ratio_match.group(1).replace(",", "")) != page:
        raise SeosanContractError(f"page {page}: response page identity changed")
    if int(ratio_match.group(2).replace(",", "")) != last:
        raise SeosanContractError(f"page {page}: declared last page changed")
    tables = root.select("table.reserve_education")
    if len(tables) != 1:
        raise SeosanContractError(f"page {page}: canonical course table changed")
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in tables[0].select("thead th"))
    caption = _clean(tables[0].caption.get_text(" ", strip=True) if tables[0].caption else "")
    if headers != _LIST_HEADERS or not caption.startswith("강좌 목록"):
        raise SeosanContractError(f"page {page}: list header/caption changed")
    body_rows = tables[0].select("tbody > tr")
    rows = tuple(_parse_list_row(row, page) for row in body_rows)
    expected = min(SEOSAN_PAGE_SIZE, total - ((page - 1) * SEOSAN_PAGE_SIZE)) if page <= last else 0
    if len(rows) != expected:
        raise SeosanContractError(f"page {page}: expected {expected} rows, found {len(rows)}")
    identities = [str(row["identity"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise SeosanContractError(f"page {page}: duplicate course identities")
    return _Page(page, total, last, rows)


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple(
            (
                row["identity"],
                row["title"],
                row["event_start"],
                row["event_end"],
                row["source_status"],
                row["application_url"],
            )
            for row in page.rows
        ),
    )


def _detail_fields(table: Any, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in table.select("tbody > tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index + 1 < len(cells):
            if cells[index].name != "th" or cells[index + 1].name != "td":
                raise SeosanContractError(f"course {identity}: detail pairing changed")
            label = _clean(cells[index].get_text(" ", strip=True))
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if not label or label in fields:
                raise SeosanContractError(f"course {identity}: conflicting detail field")
            fields[label] = value
            index += 2
        if index != len(cells):
            raise SeosanContractError(f"course {identity}: unpaired detail cell")
    if not _DETAIL_REQUIRED_FIELDS <= set(fields):
        missing = sorted(_DETAIL_REQUIRED_FIELDS - set(fields))
        raise SeosanContractError(f"course {identity}: detail fields missing {missing}")
    allowed = _DETAIL_REQUIRED_FIELDS | _DETAIL_OPTIONAL_FIELDS
    if not set(fields) <= allowed:
        extra = sorted(set(fields) - allowed)
        raise SeosanContractError(f"course {identity}: unaudited detail fields {extra}")
    return fields


def _schedule_key(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _detail_application_contract(root: Any, listed: Mapping[str, Any]) -> int:
    identity = str(listed["identity"])
    panels = root.select(":scope > div.clearfix")
    if not panels:
        raise SeosanContractError(f"course {identity}: detail status panel missing")
    control = panels[0]
    status_node = control.select_one(":scope > .fright")
    detail_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
    left_node = control.select_one(":scope > .fleft")
    left_status = _clean(left_node.get_text(" ", strip=True) if left_node else "")
    anchors = control.select(f'a[href*="{SEOSAN_APPLICATION_PATH.rsplit("/", 1)[-1]}"]')
    if listed["application_url"]:
        detail_normalized_status = _STATUS_MAP.get(detail_status)
        status_matches = detail_normalized_status == listed["status"] or {
            detail_normalized_status,
            listed["status"],
        } == {"OPEN", "WAITING"}
        if (
            not status_matches
            or left_status != "신청"
            or len(anchors) != 2
        ):
            raise SeosanContractError(f"course {identity}: detail application controls changed")
        for anchor in anchors:
            url = urljoin(SEOSAN_CANONICAL_URL, _clean(anchor.get("href")))
            _validate_course_url(
                url,
                path=SEOSAN_APPLICATION_PATH,
                identity=identity,
                page=int(listed["page"]),
            )
    elif _STATUS_MAP.get(detail_status) == listed["status"] and not anchors:
        pass
    elif (
        listed["status"] == "CLOSED"
        and left_status == listed["source_status"] == "신청마감"
        and detail_status in _APPLICATION_STATUSES
        and len(anchors) == 1
    ):
        # The official page retains a category/application link in the right
        # badge for some not-yet-open future courses while the authoritative
        # list and disabled left control both say ``신청마감``.  Validate its
        # identity but neither emit nor request it.
        inert = left_node.select("a[href]") if left_node else []
        if len(inert) != 1 or _clean(inert[0].get("href")) != "#n":
            raise SeosanContractError(f"course {identity}: closed detail control changed")
        url = urljoin(SEOSAN_CANONICAL_URL, _clean(anchors[0].get("href")))
        _validate_course_url(
            url,
            path=SEOSAN_APPLICATION_PATH,
            identity=identity,
            page=int(listed["page"]),
        )
    else:
        raise SeosanContractError(f"course {identity}: list/detail status drift")
    return len(anchors)


def _row_from_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], int, int, int, int]:
    identity = str(listed["identity"])
    roots = soup.select("#template4")
    if len(roots) != 1:
        raise SeosanContractError(f"course {identity}: detail root changed")
    root = roots[0]
    heading = root.select_one(":scope > h3")
    if _clean(heading.get_text(" ", strip=True) if heading else "") != "강좌상세정보":
        raise SeosanContractError(f"course {identity}: detail heading changed")
    tables = [
        table
        for table in root.select("table.table")
        if _clean(table.caption.get_text(" ", strip=True) if table.caption else "")
        == "수강생관리 강좌상세"
    ]
    if len(tables) != 1:
        raise SeosanContractError(f"course {identity}: detail table changed")
    fields = _detail_fields(tables[0], identity)
    term = fields["기수"]
    detail_title = fields["강좌명"] + (f"({term})" if term else "")
    event_start, event_end = _dates(fields["교육기간"], identity=identity, field="education period")
    apply_start, apply_end = _dates(fields["접수기간"], identity=identity, field="application period")
    expected_schedule = f"{listed['education_day']} {listed['education_time']}"
    fee_matches = fields["수강료"] == listed["fee_badge"] or (
        listed["fee_badge"] == "유료" and fields["수강료"].startswith("유료 ")
    )
    if not (
        detail_title == listed["title"]
        and (event_start, event_end) == (listed["event_start"], listed["event_end"])
        and (apply_start, apply_end) == (listed["apply_start"], listed["apply_end"])
        and _schedule_key(fields["교육시간"]) == _schedule_key(expected_schedule)
        and fields["교육대상"] == listed["target_badge"]
        and fee_matches
        and fields["접수방식"] == listed["application_method"]
    ):
        raise SeosanContractError(f"course {identity}: list/detail identity drift")
    branch = fields["기관명"]
    branch_code = SEOSAN_OFFICIAL_INSTITUTIONS.get(branch)
    if branch_code is None:
        raise SeosanContractError(f"course {identity}: unaudited official institution {branch!r}")
    venue = fields["교육장소"]
    detail_controls = _detail_application_contract(root, listed)
    period = f"{event_start.isoformat()} ~ {event_end.isoformat()}"
    apply_period = f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
    application_url = str(listed["application_url"])
    material_fee = fields.get("재료비", "")
    sensitive_discarded = sum(bool(fields.get(label)) for label in _DETAIL_SENSITIVE_FIELDS)
    free_text_discarded = sum(bool(fields.get(label)) for label in _DETAIL_FREE_TEXT_FIELDS)
    attachment_discarded = sum(bool(fields.get(label)) for label in _DETAIL_ATTACHMENT_FIELDS)
    row: dict[str, Any] = {
        "provider": SEOSAN_PROVIDER,
        "provider_course_id": f"{SEOSAN_PROVIDER}:education:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": branch,
        "branch_code": branch_code,
        "preserve_branch": True,
        "category": "교육·강좌",
        "program_type": "교육",
        "raw_url": str(listed["detail_url"]),
        "application_url": application_url,
        "application_type": (
            "ONLINE_WAITLIST_LOGIN_REQUIRED"
            if listed["status"] == "WAITING"
            else "ONLINE_RESERVATION_LOGIN_REQUIRED"
            if application_url
            else "INFO_ONLY"
        ),
        "application_method": str(listed["application_method"]),
        "reservation_available": bool(application_url),
        "status": str(listed["status"]),
        "raw_status": str(listed["source_status"]),
        "fee": fields["수강료"],
        "material_fee": material_fee,
        "period": period,
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": apply_period,
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": fields["교육시간"],
        "capacity": str(listed["capacity_total"]),
        "capacity_total": int(listed["capacity_total"]),
        "target": fields["교육대상"],
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": SEOSAN_PARSER,
        "municipality_code": SEOSAN_MUNICIPALITY_CODE,
        "municipality_name": SEOSAN_MUNICIPALITY_NAME,
        "municipality_full_name": SEOSAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": int(listed["page"]),
            "source_status": str(listed["source_status"]),
            "source_institution": branch,
            "source_education_period": period,
            "source_application_period": apply_period,
            "source_application_method": str(listed["application_method"]),
            "source_capacity_total": int(listed["capacity_total"]),
            "source_online_capacity": listed["online_capacity"],
            "source_applicant_count": listed["applicants"],
            "source_waitlist_count": listed["waitlist"],
            "detail_verified": True,
            "detail_application_control_count": detail_controls,
            "application_endpoint_not_requested": True,
            "login_endpoint_not_requested": True,
            "attachment_endpoint_not_requested": True,
            "sensitive_detail_fields_discarded": sensitive_discarded,
            "free_text_fields_discarded": free_text_discarded,
            "attachment_fields_discarded": attachment_discarded,
            "branch_basis": "identity-verified detail 기관명",
            "venue_basis": (
                "identity-verified detail 교육장소"
                if venue
                else "official detail 교육장소 blank; no venue inferred"
            ),
            "service_family": "education",
        },
    }
    return row, sensitive_discarded, free_text_discarded, attachment_discarded, detail_controls


def _privacy_violations(rows: Iterable[Mapping[str, Any]]) -> int:
    forbidden = {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "teacher",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
    violations = 0
    for row in rows:
        violations += len(set(row) & forbidden)
        raw = row.get("raw_fields")
        if isinstance(raw, Mapping):
            violations += len(set(raw) & forbidden)
        payload = repr(row)
        violations += len(_PHONE_RE.findall(payload)) + len(_EMAIL_RE.findall(payload))
    return violations


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        re.sub(r"[^0-9a-z가-힣]+", "", _clean(row.get("title")).casefold()),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _schedule_key(row.get("schedule_raw")),
        _clean(row.get("branch")),
        _clean(row.get("venue_name")),
    )


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _base_meta(cutoff: date) -> dict[str, Any]:
    return {
        "municipality_code": SEOSAN_MUNICIPALITY_CODE,
        "municipality_name": SEOSAN_MUNICIPALITY_NAME,
        "owner_provider": SEOSAN_PROVIDER,
        "review_provider": SEOSAN_REVIEW_PROVIDER,
        "candidate_id": SEOSAN_HOME_CANDIDATE_ID,
        "canonical_candidate_id": SEOSAN_CANONICAL_CANDIDATE_ID,
        "canonical_url": SEOSAN_CANONICAL_URL,
        "parser": SEOSAN_PARSER,
        "cutoff": cutoff.isoformat(),
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_values_persisted": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
        "owner_boundary_audit": {key: dict(value) for key, value in SEOSAN_OWNER_BOUNDARY_AUDIT.items()},
    }


def collect_seosan_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = SEOSAN_MAX_PAGES,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[HtmlFetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Seosan education snapshot."""

    try:
        cutoff = _audit_date(today)
    except (TypeError, ValueError):
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
        meta = _base_meta(cutoff)
        meta["configured_collection_error"] = "today is invalid"
        return [], SEOSAN_PARSER, meta
    meta = _base_meta(cutoff)
    if not is_seosan_education_target(target):
        meta["configured_collection_error"] = "target does not match the canonical Seosan education owner"
        return [], SEOSAN_PARSER, meta
    try:
        request_timeout = int(timeout)
        allowed_requests = int(max_pages)
        allowed_details = int(detail_limit)
        if request_timeout < 1 or allowed_requests < 1 or allowed_details < 0:
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "timeout/max_pages/detail_limit are invalid"
        return [], SEOSAN_PARSER, meta

    factory = session_factory or seosan_session_factory
    html_fetcher = fetcher or _default_fetcher
    session: Any = None
    pages: dict[int, _Page] = {}
    listed_rows: list[dict[str, Any]] = []

    def load(url: str, *, kind: str) -> BeautifulSoup:
        _safe_request_url(url, kind)
        counter = "list_requests" if kind == "list" else "detail_pages"
        meta[counter] = int(meta[counter]) + 1
        meta["logical_requests"] = int(meta["logical_requests"]) + 1
        last_error: Optional[BaseException] = None
        for attempt in range(2):
            meta["physical_requests"] = int(meta["physical_requests"]) + 1
            try:
                response = html_fetcher(session, url, request_timeout)
                status = int(getattr(response, "status_code", 200))
                if status in {429, 500, 502, 503, 504} and attempt == 0:
                    meta["request_retry_count"] = int(meta["request_retry_count"]) + 1
                    continue
                return _coerce_soup(response, url)
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 0:
                    meta["request_retry_count"] = int(meta["request_retry_count"]) + 1
                    continue
                raise
        raise SeosanContractError(f"request failed: {last_error}")

    try:
        session = factory()
        first = _parse_list_page(load(seosan_list_url(1), kind="list"), 1)
        last = first.last
        required_list_requests = last + 1 + len(set((1, last, last + 1)))
        meta["required_list_requests"] = required_list_requests
        if required_list_requests > allowed_requests:
            meta["source_cap_reached"] = True
            raise SeosanContractError(
                f"max_pages cap allows {allowed_requests} of {required_list_requests} required list requests"
            )
        pages[1] = first
        for page in range(2, last + 1):
            pages[page] = _parse_list_page(load(seosan_list_url(page), kind="list"), page)
        sentinel_page = last + 1
        sentinel = _parse_list_page(
            load(seosan_list_url(sentinel_page), kind="list"), sentinel_page
        )
        if sentinel.rows:
            raise SeosanContractError("immediate post-last page is not empty")

        for page in range(1, last + 1):
            parsed = pages[page]
            if parsed.total != first.total or parsed.last != last:
                raise SeosanContractError(f"page {page}: pagination declaration drift")
            listed_rows.extend(dict(row) for row in parsed.rows)
        if len(listed_rows) != first.total:
            raise SeosanContractError(
                f"declared total {first.total} differs from {len(listed_rows)} source rows"
            )
        identities = [str(row["identity"]) for row in listed_rows]
        duplicate_source_ids = len(identities) - len(set(identities))
        meta["duplicate_source_id_count"] = duplicate_source_ids
        if duplicate_source_ids:
            raise SeosanContractError("duplicate identities across declared pages")

        expired = [row for row in listed_rows if row["event_end"] < cutoff]
        cancelled = [
            row
            for row in listed_rows
            if row["event_end"] >= cutoff and _CANCELLED_RE.search(str(row["title"]))
        ]
        current_rows = [
            row
            for row in listed_rows
            if row["event_end"] >= cutoff and not _CANCELLED_RE.search(str(row["title"]))
        ]
        if len(current_rows) > allowed_details:
            meta["source_cap_reached"] = True
            raise SeosanContractError(
                f"detail_limit cap allows {allowed_details} of {len(current_rows)} current/future details"
            )

        output: list[dict[str, Any]] = []
        sensitive_discarded = free_text_discarded = attachment_discarded = 0
        detail_application_controls = 0
        for listed in current_rows:
            soup = load(str(listed["detail_url"]), kind="detail")
            row, sensitive, free_text, attachments, controls = _row_from_detail(listed, soup)
            output.append(row)
            sensitive_discarded += sensitive
            free_text_discarded += free_text
            attachment_discarded += attachments
            detail_application_controls += controls

        # Recheck all three pagination boundaries after detail traversal.  The
        # sentinel is rechecked as a page contract, not merely as an HTTP 200.
        boundary_rechecks: dict[str, bool] = {}
        expected_boundaries = {1: pages[1], last: pages[last], sentinel_page: sentinel}
        for page, expected in expected_boundaries.items():
            observed = _parse_list_page(load(seosan_list_url(page), kind="list"), page)
            stable = _page_signature(observed) == _page_signature(expected)
            boundary_rechecks[str(page)] = stable
            if not stable:
                raise SeosanContractError(f"page {page}: boundary stability recheck changed")

        semantic_signatures = [_semantic_signature(row) for row in output]
        semantic_duplicates = len(semantic_signatures) - len(set(semantic_signatures))
        meta["semantic_duplicate_count"] = semantic_duplicates
        if semantic_duplicates:
            raise SeosanContractError(f"{semantic_duplicates} duplicate current semantic signatures")
        deduped = list((dedupe_rows or _default_dedupe)(output))
        if len(deduped) != len(output):
            raise SeosanContractError(
                f"dedupe changed complete row count {len(output)} to {len(deduped)}"
            )
        privacy = _privacy_violations(deduped)
        meta["pii_values_persisted"] = privacy
        if privacy:
            raise SeosanContractError(f"{privacy} PII allowlist violations")
        deduped.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )

        meta.update(
            {
                "pages": last,
                "data_pages": last,
                "page_counts": {page: len(parsed.rows) for page, parsed in pages.items()},
                "empty_sentinel_page": sentinel_page,
                "boundary_rechecks": boundary_rechecks,
                "boundary_recheck_count": len(boundary_rechecks),
                "source_total": first.total,
                "source_rows": len(listed_rows),
                "current_source_count": len(current_rows),
                "current_education_count": len(current_rows),
                "expired_count": len(expired),
                "cancelled_count": len(cancelled),
                "detail_attempts": len(current_rows),
                "detail_verified": len(output),
                "returned_count": len(deduped),
                "source_status_counts": dict(Counter(row["source_status"] for row in current_rows)),
                "status_counts": dict(Counter(row["status"] for row in deduped)),
                "branch_counts": dict(Counter(row["branch"] for row in deduped)),
                "application_control_count": sum(bool(row["application_url"]) for row in current_rows),
                "detail_application_control_count": detail_application_controls,
                "sensitive_detail_fields_discarded": sensitive_discarded,
                "free_text_fields_discarded": free_text_discarded,
                "attachment_fields_discarded": attachment_discarded,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not deduped,
                "no_current_reason": (
                    "complete Seosan education ledger contains no current/future courses"
                    if not deduped
                    else ""
                ),
            }
        )
        return deduped, SEOSAN_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "pages": max(pages, default=0),
                "data_pages": max(pages, default=0),
                "source_rows": len(listed_rows),
                "returned_count": 0,
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], SEOSAN_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


collect = collect_seosan_education


__all__ = [
    "SEOSAN_APPLICATION_PATH",
    "SEOSAN_CANONICAL_CANDIDATE_ID",
    "SEOSAN_CANONICAL_DERIVED_PROVIDER_NOT_TO_CREATE",
    "SEOSAN_CANONICAL_URL",
    "SEOSAN_CHUNGNAM_CANDIDATE_ID",
    "SEOSAN_CHUNGNAM_DIRECTORY_URL",
    "SEOSAN_CHUNGNAM_PROVIDER",
    "SEOSAN_DETAIL_PATH",
    "SEOSAN_HOME_CANDIDATE_ID",
    "SEOSAN_LEARNING_HOME_URL",
    "SEOSAN_LEGACY_PARTITION_URLS",
    "SEOSAN_LIST_PATH",
    "SEOSAN_LOGIN_PATHS",
    "SEOSAN_MUNICIPALITY_CODE",
    "SEOSAN_MUNICIPALITY_NAME",
    "SEOSAN_OFFICIAL_INSTITUTIONS",
    "SEOSAN_OWNER_BOUNDARY_AUDIT",
    "SEOSAN_PAGE_SIZE",
    "SEOSAN_PARSER",
    "SEOSAN_PROVIDER",
    "SEOSAN_REVIEW_PROVIDER",
    "SeosanContractError",
    "collect",
    "collect_seosan_education",
    "is_seosan_education_target",
    "is_target",
    "seosan_detail_url",
    "seosan_list_url",
    "seosan_session_factory",
]
