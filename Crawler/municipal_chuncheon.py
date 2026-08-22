"""Fail-closed collector for Chuncheon City's legacy lifelong catalogue.

The public landing page advertises exactly three education catalogues:
face-to-face (``jung``), online (``dan``), and specialised (``etc``).  Each
catalogue publishes a complete cohort table.  Every cohort in turn owns one
complete POST-only class table.  There is no numbered pagination at either
level, so completeness is established by walking every advertised cohort and
then re-reading the catalogue and first/last class boundaries for stability.

Only cohorts whose advertised education window can still contain a current or
future class are opened at detail level.  Every such detail is identity-bound
to its catalogue row, checked against the cohort date boundary, and checked
against the visible application control.  A partial or structurally changed
snapshot returns no rows.

Applicant-count links point at ``edu_app_pop.do`` and may expose applicant
information.  They are validated as inert identity markers but are never
requested.  Instructor names, free-form descriptions, plans, staff/contact
data, application forms, and source HTML are never persisted; result rows are
built from an explicit structured-field allowlist.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


CHUNCHEON_PROVIDER = "MUNI_CLC_CHUNCHEON_GO_KR_A560168D"
CHUNCHEON_CANONICAL_CANDIDATE_ID = "MUNI_IR_68BAB2356B75"
CHUNCHEON_HOME_PROVIDER = "MUNI_CLC_CHUNCHEON_GO_KR_65451210"
CHUNCHEON_HOME_CANDIDATE_ID = "MUNI_IR_37CBDB458111"
CHUNCHEON_BWB_PROVIDER = "MUNI_BWB_CHUNCHEON_GO_KR_1CCE214F"
CHUNCHEON_BWB_CANDIDATE_ID = "MUNI_IR_F4DAE31AD205"

CHUNCHEON_MUNICIPALITY_CODE = "5111000000"
CHUNCHEON_MUNICIPALITY_NAME = "강원특별자치도 춘천시"
CHUNCHEON_BRANCH = "춘천시 평생학습관"
CHUNCHEON_ADDRESS = "강원특별자치도 춘천시 퇴계농공로 40(퇴계동)"

CHUNCHEON_HOST = "clc.chuncheon.go.kr"
CHUNCHEON_ROOT = f"https://{CHUNCHEON_HOST}"
CHUNCHEON_CANONICAL_PATH = "/site/edu/edu_apply_info.do"
CHUNCHEON_GISU_PATH = "/site/edu/edu_gisu_list.do"
CHUNCHEON_CLASS_LIST_PATH = "/site/edu/edu_class_list.do"
CHUNCHEON_CLASS_DETAIL_PATH = "/site/edu/edu_class_view.do"
CHUNCHEON_APPLICANT_POPUP_PATH = "/site/edu/edu_app_pop.do"
CHUNCHEON_APPLICATION_FORM_PATH = "/site/edu/edu_class_agree.do"
CHUNCHEON_CANONICAL_URL = CHUNCHEON_ROOT + CHUNCHEON_CANONICAL_PATH
CHUNCHEON_HOME_URL = CHUNCHEON_ROOT + "/"
CHUNCHEON_CLASS_LIST_URL = CHUNCHEON_ROOT + CHUNCHEON_CLASS_LIST_PATH

CHUNCHEON_E_TYPES: Mapping[str, str] = {
    "jung": "대면교육",
    "dan": "비대면교육",
    "etc": "특화교육",
}
CHUNCHEON_FETCH_ATTEMPTS = 2
CHUNCHEON_MAX_HTML_BYTES = 4_000_000
CHUNCHEON_PARSER = (
    "chuncheon_clc_three_types+all_advertised_cohorts+complete_class_posts+"
    "stable_catalogue_and_type_boundaries+current_details+"
    "identity_bound_application_state+fixed_facility_branch+pii_allowlist"
)

CHUNCHEON_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "canonical_url": CHUNCHEON_CANONICAL_URL,
    "education_types": dict(CHUNCHEON_E_TYPES),
    "gisu_counts": {"jung": 6, "dan": 11, "etc": 18},
    "gisu_total": 35,
    "class_counts": {"jung": 341, "dan": 295, "etc": 152},
    "source_total": 788,
    "unique_class_identities": 788,
    "empty_class_pages": {
        "jung": ["GISU_000000000000390"],
        "dan": ["GISU_000000000000391"],
        "etc": [],
    },
    "source_status_counts": {"신청마감": 788},
    "current_or_future": 0,
    "detail_pages_required": 0,
    "branch": CHUNCHEON_BRANCH,
    "conclusion": (
        "complete official legacy catalogue; all 788 advertised classes are "
        "expired as of the audit date"
    ),
}

CHUNCHEON_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    CHUNCHEON_PROVIDER: {
        "decision": "canonical_clc_structured_catalogue_owner",
        "candidate_id": CHUNCHEON_CANONICAL_CANDIDATE_ID,
        "catalogues": (CHUNCHEON_CANONICAL_URL,),
        "aliases": (CHUNCHEON_HOME_URL,),
    },
    CHUNCHEON_HOME_PROVIDER: {
        "decision": f"home_alias_of_{CHUNCHEON_PROVIDER}",
        "candidate_id": CHUNCHEON_HOME_CANDIDATE_ID,
        "catalogues": (CHUNCHEON_HOME_URL,),
    },
    CHUNCHEON_BWB_PROVIDER: {
        "decision": "keep_separate_new_integrated_learning_platform_owner",
        "candidate_id": CHUNCHEON_BWB_CANDIDATE_ID,
        "catalogues": ("https://bwb.chuncheon.go.kr/enrollment/category/",),
    },
}

CHUNCHEON_PII_FIELDS_NEVER_PERSISTED = (
    "강사명",
    "강의계획서와 첨부파일",
    "강의방법 자유서술",
    "교육내용 자유서술",
    "신청자 현황과 신청자 명단",
    "신청자 이름·연락처·신청시각",
    "담당자 이름·전화번호·이메일",
    "로그인·세션·CI 값",
    "신청 동의/등록 폼과 제출 payload",
    "source_html",
)


class ChuncheonContractError(ValueError):
    """Raised when the official catalogue no longer matches its contract."""


Requester = Callable[
    [Any, str, str, int, Optional[Mapping[str, str]]],
    Any,
]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


@dataclass(frozen=True)
class _Gisu:
    e_type: str
    identity: str
    name: str
    apply_period: str
    apply_start: date
    apply_end: date
    period: str
    start: date
    end: date
    source_status: str


@dataclass(frozen=True)
class _ClassPage:
    e_type: str
    gisu_identity: str
    rows: tuple[dict[str, Any], ...]
    structural_empty: bool


_SPACE_RE = re.compile(r"\s+")
_GISU_RE = re.compile(r"^GISU_\d{15}$")
_CLASS_RE = re.compile(r"^[1-9]\d*$")
_GISU_ONCLICK_RE = re.compile(r"^\s*fn_classList\(\s*['\"](?P<id>GISU_\d{15})['\"]\s*\)\s*;?\s*$")
_CLASS_ONCLICK_RE = re.compile(r"^\s*fn_classView\(\s*['\"](?P<id>[1-9]\d*)['\"]\s*\)\s*;?\s*$")
_APPLICANT_POPUP_RE = re.compile(
    r"^\s*javascript\s*:\s*edu_app_pop\(\s*['\"](?P<id>[1-9]\d*)['\"]\s*\)\s*;?\s*$",
    re.IGNORECASE,
)
_DATE_PAIR_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})(?:\s+[0-2]\d:[0-5]\d)?\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})(?:\s+[0-2]\d:[0-5]\d)?$"
)
_CAPACITY_RE = re.compile(
    r"^(?P<current>\d{1,6})\s*/\s*(?P<total>\d{1,6})"
    r"(?:\s*\(\s*(?P<wait>\d{1,6})\s*\))?$"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[\s.)-]?\d{3,4}[\s-]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)

_STATUS_MAP: Mapping[str, str] = {
    "신청중": "OPEN",
    "접수중": "OPEN",
    "신청하기": "OPEN",
    "추가접수": "OPEN",
    "대기자신청": "OPEN",
    "신청대기": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "신청마감": "CLOSED",
    "접수마감": "CLOSED",
    "마감": "CLOSED",
}
_CLASS_HEADERS = (
    "구분",
    "교육과목",
    "강의시간",
    "대상자",
    "접수인원/정원 (대기인원)",
    "상태",
)
_GISU_HEADERS = ("기수명", "접수기간", "교육기간", "상태")
_SAFE_DETAIL_LABELS = frozenset(
    {
        "분야",
        "교육방법",
        "강좌명",
        "선정방식",
        "접수현황",
        "대상자",
        "접수기간",
        "교육기간",
        "교육시간",
        "수강료",
        "재료비",
    }
)
_DISCARDED_DETAIL_LABELS = frozenset(
    {"강사명", "강의계획서", "강의방법", "교육내용"}
)
_DETAIL_LABELS = _SAFE_DETAIL_LABELS | _DISCARDED_DETAIL_LABELS
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "e_type",
        "education_type",
        "gisu_identity",
        "gisu_name",
        "source_category",
        "source_status",
        "source_capacity",
        "source_target",
        "source_education_method",
        "source_selection_method",
        "detail_verified",
        "application_control_present",
        "applicant_popup_identity_verified_without_request",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "teacher",
        "contact",
        "contact_name",
        "phone",
        "email",
        "attachments",
        "attachment_urls",
        "raw_detail_pairs",
        "detail_description",
        "source_html",
        "raw_html",
        "applicants",
        "applicant_names",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).lower()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _safe_port(parsed: Any) -> Optional[int]:
    try:
        return parsed.port
    except ValueError:
        return -1


def is_chuncheon_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != CHUNCHEON_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == CHUNCHEON_HOST
        and _safe_port(parsed) is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == CHUNCHEON_CANONICAL_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_chuncheon_education_target


def is_chuncheon_home_alias_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == CHUNCHEON_HOME_PROVIDER
        and _clean(_target_value(target, "url")) == CHUNCHEON_HOME_URL
    )


def is_chuncheon_bwb_separate_target(target: Any) -> bool:
    return _clean(_target_value(target, "provider")) == CHUNCHEON_BWB_PROVIDER


def chuncheon_gisu_url(e_type: str) -> str:
    if e_type not in CHUNCHEON_E_TYPES:
        raise ValueError("unsupported Chuncheon education type")
    return CHUNCHEON_ROOT + CHUNCHEON_GISU_PATH + "?" + urlencode({"e_type": e_type})


def chuncheon_detail_url(e_type: str, gisu_identity: str, class_identity: str) -> str:
    if e_type not in CHUNCHEON_E_TYPES:
        raise ValueError("unsupported Chuncheon education type")
    gisu = _clean(gisu_identity)
    identity = _clean(class_identity)
    if _GISU_RE.fullmatch(gisu) is None or _CLASS_RE.fullmatch(identity) is None:
        raise ValueError("invalid Chuncheon cohort/course identity")
    return CHUNCHEON_ROOT + CHUNCHEON_CLASS_DETAIL_PATH + "?" + urlencode(
        (("e_type", e_type), ("yy", gisu), ("class_no", identity))
    )


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(ZoneInfo("Asia/Seoul")).date()
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(_clean(value))
    raise TypeError("today must be a date, datetime, ISO date string, or None")


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    retry = Retry(
        total=CHUNCHEON_FETCH_ATTEMPTS - 1,
        connect=CHUNCHEON_FETCH_ATTEMPTS - 1,
        read=CHUNCHEON_FETCH_ATTEMPTS - 1,
        status=CHUNCHEON_FETCH_ATTEMPTS - 1,
        allowed_methods=frozenset({"GET", "POST"}),
        status_forcelist=(429, 500, 502, 503, 504),
        backoff_factor=0.35,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    current.mount("https://", adapter)
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; Mooncen/1.0; public-course-audit)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": CHUNCHEON_CANONICAL_URL,
        }
    )
    return current


def _default_requester(
    session: requests.Session,
    method: str,
    url: str,
    timeout: int,
    data: Optional[Mapping[str, str]],
) -> requests.Response:
    return session.request(
        method,
        url,
        data=dict(data) if data is not None else None,
        timeout=timeout,
        verify=True,
        allow_redirects=False,
    )


def _validate_request_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != CHUNCHEON_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        not in {
            CHUNCHEON_GISU_PATH,
            CHUNCHEON_CLASS_LIST_PATH,
            CHUNCHEON_CLASS_DETAIL_PATH,
        }
        or parsed.fragment
    ):
        raise ChuncheonContractError("request escaped the Chuncheon public catalogue")


def _response_html(response: Any, requested_url: str) -> str:
    status = getattr(response, "status_code", None)
    if status != 200:
        raise ChuncheonContractError(f"HTTP {status}")
    final_url = _clean(getattr(response, "url", ""))
    if final_url:
        _validate_request_url(final_url)
        if urlparse(final_url).path != urlparse(requested_url).path:
            raise ChuncheonContractError("response endpoint changed")
    headers = getattr(response, "headers", {}) or {}
    content_type = _clean(headers.get("Content-Type") or headers.get("content-type")).lower()
    if content_type and not any(value in content_type for value in ("text/html", "application/xhtml")):
        raise ChuncheonContractError("response is not HTML")
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        if len(content) > CHUNCHEON_MAX_HTML_BYTES:
            raise ChuncheonContractError("HTML response exceeds size limit")
        encoding = _clean(getattr(response, "encoding", "")) or "utf-8"
        try:
            return content.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            return content.decode("utf-8", errors="strict")
    text = str(getattr(response, "text", ""))
    if len(text.encode("utf-8")) > CHUNCHEON_MAX_HTML_BYTES:
        raise ChuncheonContractError("HTML response exceeds size limit")
    return text


def _request_soup(
    session: Any,
    requester: Requester,
    method: str,
    url: str,
    timeout: int,
    data: Optional[Mapping[str, str]] = None,
) -> BeautifulSoup:
    _validate_request_url(url)
    response = requester(session, method, url, timeout, data)
    html = _response_html(response, url)
    soup = BeautifulSoup(html, "lxml")
    if soup.title is None or "춘천시 평생학습관" not in _clean(soup.title.get_text(" ", strip=True)):
        raise ChuncheonContractError("official page identity changed")
    return soup


def _date_pair(value: str, label: str) -> tuple[date, date]:
    match = _DATE_PAIR_RE.fullmatch(_clean(value))
    if match is None:
        raise ChuncheonContractError(f"{label} date pair changed")
    start = date.fromisoformat(match.group("start"))
    end = date.fromisoformat(match.group("end"))
    if start > end:
        raise ChuncheonContractError(f"{label} date pair is reversed")
    return start, end


def _safe_public_text(value: str, label: str, *, allow_empty: bool = False) -> str:
    cleaned = _clean(value)
    if not cleaned and not allow_empty:
        raise ChuncheonContractError(f"{label} is empty")
    if _PHONE_RE.search(cleaned) or _EMAIL_RE.search(cleaned):
        raise ChuncheonContractError(f"unsafe {label} contains contact data")
    return cleaned


def _one_table(soup: BeautifulSoup, headers: tuple[str, ...], label: str) -> Any:
    matches = []
    for table in soup.select("table.tbl_bbs"):
        actual = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
        if actual == headers:
            matches.append(table)
    if len(matches) != 1:
        raise ChuncheonContractError(f"{label} table/header contract changed")
    if soup.select(".pagination a, .paginate a, .paging a, .page_navi a"):
        raise ChuncheonContractError(f"{label} unexpectedly exposes pagination")
    return matches[0]


def _form_named_values(form: Any) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in form.select("[name]"):
        name = _clean(node.get("name"))
        if not name or name in values:
            raise ChuncheonContractError("form field identity changed")
        values[name] = _clean(node.get("value"))
    return values


def _parse_gisu_page(soup: BeautifulSoup, e_type: str) -> tuple[_Gisu, ...]:
    table = _one_table(soup, _GISU_HEADERS, f"{e_type} cohort")
    forms = soup.select("form")
    expected_forms = []
    for form in forms:
        action = urlparse(urljoin(CHUNCHEON_ROOT, _clean(form.get("action")))).path
        if action == CHUNCHEON_CLASS_LIST_PATH:
            expected_forms.append(form)
    if len(expected_forms) != 1:
        raise ChuncheonContractError(f"{e_type} cohort form changed")
    form = expected_forms[0]
    if _clean(form.get("method")).lower() != "post":
        raise ChuncheonContractError(f"{e_type} cohort form method changed")
    if _form_named_values(form) != {"e_type": e_type, "yy": ""}:
        raise ChuncheonContractError(f"{e_type} cohort form fields changed")

    result: list[_Gisu] = []
    for index, tr in enumerate(table.select("tbody > tr"), start=1):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 4:
            raise ChuncheonContractError(f"{e_type} cohort row {index} changed")
        links = cells[0].find_all("a", onclick=True)
        if len(links) != 1:
            raise ChuncheonContractError(f"{e_type} cohort row {index} identity changed")
        match = _GISU_ONCLICK_RE.fullmatch(_clean(links[0].get("onclick")))
        if match is None:
            raise ChuncheonContractError(f"{e_type} cohort row {index} handler changed")
        identity = match.group("id")
        name = _safe_public_text(cells[0].get_text(" ", strip=True), "cohort name")
        apply_period = _clean(cells[1].get_text(" ", strip=True))
        period = _clean(cells[2].get_text(" ", strip=True))
        apply_start, apply_end = _date_pair(apply_period, f"cohort {identity} application")
        start, end = _date_pair(period, f"cohort {identity} education")
        source_status = _clean(cells[3].get_text(" ", strip=True))
        if source_status not in _STATUS_MAP:
            raise ChuncheonContractError(f"cohort {identity} status changed")
        result.append(
            _Gisu(
                e_type=e_type,
                identity=identity,
                name=name,
                apply_period=apply_period,
                apply_start=apply_start,
                apply_end=apply_end,
                period=period,
                start=start,
                end=end,
                source_status=source_status,
            )
        )
    if not result:
        raise ChuncheonContractError(f"{e_type} cohort catalogue unexpectedly empty")
    identities = [item.identity for item in result]
    if len(identities) != len(set(identities)):
        raise ChuncheonContractError(f"{e_type} cohort identities are duplicated")
    return tuple(result)


def _parse_class_page(soup: BeautifulSoup, gisu: _Gisu) -> _ClassPage:
    table = _one_table(soup, _CLASS_HEADERS, f"{gisu.e_type}:{gisu.identity} class")
    forms = soup.select("form")
    expected_forms = []
    for form in forms:
        values = _form_named_values(form)
        if set(values) == {"e_type", "yy", "class_no"}:
            expected_forms.append((form, values))
    if len(expected_forms) != 1:
        raise ChuncheonContractError(f"cohort {gisu.identity} class form changed")
    form, values = expected_forms[0]
    if _clean(form.get("method")).lower() != "post" or values != {
        "e_type": gisu.e_type,
        "yy": gisu.identity,
        "class_no": "",
    }:
        raise ChuncheonContractError(f"cohort {gisu.identity} class form identity changed")

    table_rows = table.select("tbody > tr")
    empty_rows = [row for row in table_rows if "no-contents" in (row.get("class") or [])]
    if empty_rows:
        if (
            len(table_rows) != 1
            or len(empty_rows) != 1
            or _clean(empty_rows[0].get_text(" ", strip=True)) != "등록된 교육이 없습니다."
            or empty_rows[0].find("a") is not None
        ):
            raise ChuncheonContractError(
                f"cohort {gisu.identity} structural empty sentinel changed"
            )
        return _ClassPage(gisu.e_type, gisu.identity, (), True)

    result: list[dict[str, Any]] = []
    for index, tr in enumerate(table_rows, start=1):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 6:
            raise ChuncheonContractError(f"cohort {gisu.identity} class row {index} changed")
        title_links = []
        for link in cells[1].find_all("a", onclick=True):
            if _CLASS_ONCLICK_RE.fullmatch(_clean(link.get("onclick"))):
                title_links.append(link)
        if len(title_links) != 1:
            raise ChuncheonContractError(f"cohort {gisu.identity} class identity changed")
        identity_match = _CLASS_ONCLICK_RE.fullmatch(_clean(title_links[0].get("onclick")))
        assert identity_match is not None
        identity = identity_match.group("id")
        status_links = []
        for link in cells[5].find_all("a", onclick=True):
            match = _CLASS_ONCLICK_RE.fullmatch(_clean(link.get("onclick")))
            if match is not None:
                status_links.append(match.group("id"))
        if status_links != [identity]:
            raise ChuncheonContractError(f"course {identity} status/detail identity mismatch")
        popup_links = []
        for link in cells[4].find_all("a", href=True):
            match = _APPLICANT_POPUP_RE.fullmatch(_clean(link.get("href")))
            if match is not None:
                popup_links.append(match.group("id"))
        if popup_links != [identity]:
            raise ChuncheonContractError(f"course {identity} applicant popup identity changed")

        source_status = _clean(cells[5].get_text(" ", strip=True))
        if source_status not in _STATUS_MAP:
            raise ChuncheonContractError(f"course {identity} status changed")
        capacity = _clean(cells[4].get_text(" ", strip=True))
        if _CAPACITY_RE.fullmatch(capacity) is None:
            raise ChuncheonContractError(f"course {identity} capacity changed")
        result.append(
            {
                "identity": identity,
                "e_type": gisu.e_type,
                "gisu_identity": gisu.identity,
                "gisu": gisu,
                "category": _safe_public_text(
                    cells[0].get_text(" ", strip=True), f"course {identity} category"
                ),
                "title": _safe_public_text(
                    title_links[0].get_text(" ", strip=True), f"course {identity} title"
                ),
                "schedule": _safe_public_text(
                    cells[2].get_text(" ", strip=True), f"course {identity} schedule"
                ),
                "target": _safe_public_text(
                    cells[3].get_text(" ", strip=True), f"course {identity} target"
                ),
                "capacity": capacity,
                "source_status": source_status,
                "detail_url": chuncheon_detail_url(gisu.e_type, gisu.identity, identity),
                "applicant_popup_identity_verified": True,
            }
        )
    if not result:
        raise ChuncheonContractError(
            f"cohort {gisu.identity} has neither courses nor an empty sentinel"
        )
    identities = [row["identity"] for row in result]
    if len(identities) != len(set(identities)):
        raise ChuncheonContractError(f"cohort {gisu.identity} class identities are duplicated")
    return _ClassPage(gisu.e_type, gisu.identity, tuple(result), False)


def _gisu_signature(rows: Iterable[_Gisu]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            row.identity,
            row.name,
            row.apply_period,
            row.period,
            row.source_status,
        )
        for row in rows
    )


def _class_signature(page: _ClassPage) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("category")),
            _clean(row.get("title")),
            _clean(row.get("schedule")),
            _clean(row.get("target")),
            _clean(row.get("capacity")),
            _clean(row.get("source_status")),
        )
        for row in page.rows
    )


def _detail_fields(table: Any, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    labels: set[str] = set()
    for tr in table.select("tbody > tr") or table.select("tr"):
        heads = tr.find_all("th", recursive=False)
        values = tr.find_all("td", recursive=False)
        if len(heads) != 1 or len(values) != 1:
            raise ChuncheonContractError(f"course {identity} detail row structure changed")
        label = _clean(heads[0].get_text(" ", strip=True))
        if not label or label in labels:
            raise ChuncheonContractError(f"course {identity} detail label duplicated")
        labels.add(label)
        if label not in _DETAIL_LABELS:
            raise ChuncheonContractError(f"course {identity} detail field set changed")
        if label in _SAFE_DETAIL_LABELS:
            fields[label] = _safe_public_text(
                values[0].get_text(" ", strip=True),
                f"course {identity} {label}",
                allow_empty=label in {"재료비"},
            )
        # Values of discarded labels are deliberately never read.
    if labels != _DETAIL_LABELS or set(fields) != _SAFE_DETAIL_LABELS:
        raise ChuncheonContractError(f"course {identity} detail field set changed")
    return fields


def _application_control(soup: BeautifulSoup, identity: str, status: str) -> bool:
    controls = []
    for node in soup.select("div.cont.clearfix div.btn_wrap a, div.cont.clearfix div.btn_wrap button, div.cont.clearfix div.btn_wrap input"):
        text = _clean(node.get("value") or node.get_text(" ", strip=True))
        onclick = _clean(node.get("onclick"))
        if "신청" in text or "접수" in text or re.search(r"edu_(?:regist|login)\s*\(", onclick):
            controls.append(node)
    if status == "OPEN":
        if len(controls) != 1:
            raise ChuncheonContractError(
                f"course {identity} open status has no unique public application control"
            )
        onclick = _clean(controls[0].get("onclick"))
        if re.fullmatch(r"edu_(?:regist|login)\s*\(\s*\)\s*;?", onclick) is None:
            raise ChuncheonContractError(f"course {identity} application control changed")
        return True
    if controls:
        raise ChuncheonContractError(
            f"course {identity} inactive status exposes an application control"
        )
    return False


def _fee_amount(value: str, identity: str) -> int:
    cleaned = _clean(value).replace(",", "").replace(" ", "")
    if cleaned in {"무료", "없음", "0", "0원"}:
        return 0
    match = re.fullmatch(r"(?P<amount>\d{1,9})원", cleaned)
    if match is None:
        raise ChuncheonContractError(f"course {identity} fee changed")
    return int(match.group("amount"))


def _parse_detail(listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    tables = soup.select("table.bbs_form")
    if len(tables) != 1:
        raise ChuncheonContractError(f"course {identity} detail table changed")
    fields = _detail_fields(tables[0], identity)
    if fields["분야"] != _clean(listed.get("category")):
        raise ChuncheonContractError(f"course {identity} list/detail category mismatch")
    if fields["강좌명"] != _clean(listed.get("title")):
        raise ChuncheonContractError(f"course {identity} list/detail title mismatch")
    if fields["접수현황"] != _clean(listed.get("capacity")):
        raise ChuncheonContractError(f"course {identity} list/detail capacity mismatch")
    if fields["대상자"] != _clean(listed.get("target")):
        raise ChuncheonContractError(f"course {identity} list/detail target mismatch")
    if fields["교육시간"] != _clean(listed.get("schedule")):
        raise ChuncheonContractError(f"course {identity} list/detail schedule mismatch")

    forms = soup.select("form#edu_form")
    if len(forms) != 1:
        raise ChuncheonContractError(f"course {identity} detail identity form changed")
    form = forms[0]
    action = urlparse(urljoin(CHUNCHEON_ROOT, _clean(form.get("action")))).path
    named = _form_named_values(form)
    if (
        _clean(form.get("method")).lower() != "post"
        or action != "/site/edu/edu_class_regist.do"
        or named
        != {
            "class_no": identity,
            "yy": _clean(listed.get("gisu_identity")),
            "e_type": _clean(listed.get("e_type")),
        }
    ):
        raise ChuncheonContractError(f"course {identity} detail identity mismatch")

    gisu = listed.get("gisu")
    if not isinstance(gisu, _Gisu):
        raise ChuncheonContractError(f"course {identity} cohort context missing")
    apply_start, apply_end = _date_pair(fields["접수기간"], f"course {identity} application")
    start, end = _date_pair(fields["교육기간"], f"course {identity} education")
    if not (gisu.apply_start <= apply_start <= apply_end <= gisu.apply_end):
        raise ChuncheonContractError(f"course {identity} application escaped cohort boundary")
    if not (gisu.start <= start <= end <= gisu.end):
        raise ChuncheonContractError(f"course {identity} education escaped cohort boundary")

    status = _STATUS_MAP[_clean(listed.get("source_status"))]
    if status == "OPEN" and not (apply_start <= cutoff <= apply_end):
        raise ChuncheonContractError(f"course {identity} open status/date mismatch")
    if status == "SCHEDULED" and not cutoff < apply_start:
        raise ChuncheonContractError(f"course {identity} scheduled status/date mismatch")
    control = _application_control(soup, identity, status)
    capacity_match = _CAPACITY_RE.fullmatch(fields["접수현황"])
    assert capacity_match is not None
    capacity_current = int(capacity_match.group("current"))
    capacity_total = int(capacity_match.group("total"))
    waitlist_total = int(capacity_match.group("wait") or 0)
    if capacity_total < 1:
        raise ChuncheonContractError(f"course {identity} capacity is invalid")

    raw_url = _clean(listed.get("detail_url"))
    row: dict[str, Any] = {
        "provider": CHUNCHEON_PROVIDER,
        "provider_course_id": (
            f"{CHUNCHEON_PROVIDER}:{gisu.e_type}:{gisu.identity}:{identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": fields["강좌명"],
        "description": fields["강좌명"],
        "branch": CHUNCHEON_BRANCH,
        "branch_code": "chuncheon:" + hashlib.sha1(CHUNCHEON_BRANCH.encode("utf-8")).hexdigest()[:12],
        "preserve_branch": True,
        "category": "평생학습",
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": raw_url if control else "",
        "application_type": "ONLINE_RESERVATION_LOGIN_REQUIRED" if control else "INFO_ONLY",
        "application_method": "온라인",
        "application_methods": ["온라인"],
        "reservation_available": control,
        "status": status,
        "fee": fields["수강료"],
        "fee_amount": _fee_amount(fields["수강료"], identity),
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": fields["접수기간"],
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": fields["교육시간"],
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_current": max(0, capacity_current - capacity_total),
        "waitlist_total": waitlist_total,
        "target": fields["대상자"],
        "venue": "온라인" if fields["교육방법"] == "온라인" else CHUNCHEON_BRANCH,
        "venue_name": "온라인" if fields["교육방법"] == "온라인" else CHUNCHEON_BRANCH,
        "address": CHUNCHEON_ADDRESS,
        "venue_address": CHUNCHEON_ADDRESS,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": CHUNCHEON_PARSER,
        "municipality_code": CHUNCHEON_MUNICIPALITY_CODE,
        "municipality_full_name": CHUNCHEON_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "e_type": gisu.e_type,
            "education_type": CHUNCHEON_E_TYPES[gisu.e_type],
            "gisu_identity": gisu.identity,
            "gisu_name": gisu.name,
            "source_category": fields["분야"],
            "source_status": _clean(listed.get("source_status")),
            "source_capacity": fields["접수현황"],
            "source_target": fields["대상자"],
            "source_education_method": fields["교육방법"],
            "source_selection_method": fields["선정방식"],
            "detail_verified": True,
            "application_control_present": control,
            "applicant_popup_identity_verified_without_request": bool(
                listed.get("applicant_popup_identity_verified")
            ),
            "service_family": "education",
        },
        "_source_end_date": end,
    }
    return row


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "_source_end_date"}
        }
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail description persisted")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "gisu_requests": 0,
        "class_list_requests": 0,
        "stability_rechecks": 0,
        "gisu_stability_rechecks": 0,
        "class_stability_rechecks": 0,
        "declared_class_pages": 0,
        "data_pages": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_gisu_total": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_candidate_count": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "archived_rows_skipped_before_detail": 0,
        "identity_duplicate_count": 0,
        "empty_class_page_count": 0,
        "empty_class_pages": [],
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": CHUNCHEON_MUNICIPALITY_CODE,
        "municipality_name": CHUNCHEON_MUNICIPALITY_NAME,
        "canonical_candidate_id": CHUNCHEON_CANONICAL_CANDIDATE_ID,
        "canonical_url": CHUNCHEON_CANONICAL_URL,
        "boundary_mode": (
            "three stable cohort catalogues plus one complete class POST per "
            "advertised cohort and per-type first/last class rechecks"
        ),
    }


def collect_chuncheon_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    requester: Optional[Requester] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future snapshot from the CLC catalogue."""

    meta = _base_meta()
    if not is_chuncheon_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Chuncheon CLC owner"
        return [], CHUNCHEON_PARSER, meta
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages < 1
        or isinstance(detail_limit, bool)
        or not isinstance(detail_limit, int)
        or detail_limit < 0
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], CHUNCHEON_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], CHUNCHEON_PARSER, meta

    current_factory = session_factory or _default_session_factory
    current_requester = requester or _default_requester
    session = current_factory()
    errors: list[str] = []
    gisu_by_type: dict[str, tuple[_Gisu, ...]] = {}
    pages_by_key: dict[tuple[str, str], _ClassPage] = {}
    result: list[dict[str, Any]] = []
    try:
        for e_type in CHUNCHEON_E_TYPES:
            soup = _request_soup(
                session,
                current_requester,
                "GET",
                chuncheon_gisu_url(e_type),
                timeout,
            )
            gisu_by_type[e_type] = _parse_gisu_page(soup, e_type)
            meta["gisu_requests"] += 1
            meta["list_requests"] += 1
            meta["pages"] += 1

        all_gisu = [item for e_type in CHUNCHEON_E_TYPES for item in gisu_by_type[e_type]]
        gisu_ids = [item.identity for item in all_gisu]
        if len(gisu_ids) != len(set(gisu_ids)):
            errors.append("duplicate official cohort identities across education types")
        meta.update(
            {
                "source_gisu_total": len(all_gisu),
                "gisu_counts": {
                    e_type: len(gisu_by_type[e_type]) for e_type in CHUNCHEON_E_TYPES
                },
                "declared_class_pages": len(all_gisu),
            }
        )
        if len(all_gisu) > max_pages:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (
                        f"max_pages cap allows {max_pages} of {len(all_gisu)} "
                        "advertised cohort class pages"
                    ),
                }
            )
            return [], CHUNCHEON_PARSER, meta

        for gisu in all_gisu:
            soup = _request_soup(
                session,
                current_requester,
                "POST",
                CHUNCHEON_CLASS_LIST_URL,
                timeout,
                {"e_type": gisu.e_type, "yy": gisu.identity},
            )
            pages_by_key[(gisu.e_type, gisu.identity)] = _parse_class_page(soup, gisu)
            meta["class_list_requests"] += 1
            meta["list_requests"] += 1
            meta["pages"] += 1

        # Re-read all three advertised cohort catalogues after the class walk.
        for e_type in CHUNCHEON_E_TYPES:
            soup = _request_soup(
                session,
                current_requester,
                "GET",
                chuncheon_gisu_url(e_type),
                timeout,
            )
            rechecked = _parse_gisu_page(soup, e_type)
            meta["gisu_requests"] += 1
            meta["list_requests"] += 1
            meta["pages"] += 1
            meta["gisu_stability_rechecks"] += 1
            if _gisu_signature(rechecked) != _gisu_signature(gisu_by_type[e_type]):
                errors.append(f"{e_type} cohort catalogue stability recheck changed")

        # Every type's first and last advertised class table are the declared
        # boundaries.  A one-cohort type is re-read once, not twice.
        boundary_gisu: list[_Gisu] = []
        for e_type in CHUNCHEON_E_TYPES:
            typed = gisu_by_type[e_type]
            boundary_gisu.append(typed[0])
            if typed[-1].identity != typed[0].identity:
                boundary_gisu.append(typed[-1])
        for gisu in boundary_gisu:
            soup = _request_soup(
                session,
                current_requester,
                "POST",
                CHUNCHEON_CLASS_LIST_URL,
                timeout,
                {"e_type": gisu.e_type, "yy": gisu.identity},
            )
            rechecked = _parse_class_page(soup, gisu)
            meta["class_list_requests"] += 1
            meta["list_requests"] += 1
            meta["pages"] += 1
            meta["class_stability_rechecks"] += 1
            original = pages_by_key[(gisu.e_type, gisu.identity)]
            if (
                rechecked.structural_empty != original.structural_empty
                or _class_signature(rechecked) != _class_signature(original)
            ):
                errors.append(
                    f"{gisu.e_type}:{gisu.identity} class boundary stability recheck changed"
                )
        meta["stability_rechecks"] = (
            meta["gisu_stability_rechecks"] + meta["class_stability_rechecks"]
        )
        meta["required_list_requests"] = (
            len(CHUNCHEON_E_TYPES) * 2 + len(all_gisu) + len(boundary_gisu)
        )

        listed = [
            row
            for gisu in all_gisu
            for row in pages_by_key[(gisu.e_type, gisu.identity)].rows
        ]
        identities = [_clean(row.get("identity")) for row in listed]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate official class identities")
        empty_pages = [
            f"{gisu.e_type}:{gisu.identity}"
            for gisu in all_gisu
            if pages_by_key[(gisu.e_type, gisu.identity)].structural_empty
        ]
        source_type_counts = Counter(_clean(row.get("e_type")) for row in listed)
        source_status_counts = Counter(_clean(row.get("source_status")) for row in listed)
        page_counts = {
            f"{gisu.e_type}:{gisu.identity}": len(
                pages_by_key[(gisu.e_type, gisu.identity)].rows
            )
            for gisu in all_gisu
        }
        list_complete = bool(
            not errors
            and meta["list_requests"] == meta["required_list_requests"]
            and meta["gisu_stability_rechecks"] == len(CHUNCHEON_E_TYPES)
            and meta["class_stability_rechecks"] == len(boundary_gisu)
        )
        meta.update(
            {
                "data_pages": len(all_gisu),
                "class_page_counts": page_counts,
                "source_total": len(listed),
                "source_rows": len(listed),
                "source_type_counts": dict(source_type_counts),
                "source_status_counts": dict(source_status_counts),
                "identity_duplicate_count": duplicate_count,
                "empty_class_page_count": len(empty_pages),
                "empty_class_pages": empty_pages,
                "pagination_complete": list_complete,
            }
        )
        if not list_complete:
            meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
            return [], CHUNCHEON_PARSER, meta

        detail_candidates = [
            row for row in listed if isinstance(row.get("gisu"), _Gisu) and row["gisu"].end >= cutoff
        ]
        meta.update(
            {
                "current_candidate_count": len(detail_candidates),
                "archived_rows_skipped_before_detail": len(listed) - len(detail_candidates),
            }
        )
        if len(detail_candidates) > detail_limit:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (
                        f"detail_limit cap allows {detail_limit} of "
                        f"{len(detail_candidates)} current/future cohort details"
                    ),
                }
            )
            return [], CHUNCHEON_PARSER, meta

        meta["detail_attempts"] = len(detail_candidates)
        detailed: list[dict[str, Any]] = []
        for listed_row in detail_candidates:
            identity = _clean(listed_row.get("identity"))
            try:
                soup = _request_soup(
                    session,
                    current_requester,
                    "GET",
                    _clean(listed_row.get("detail_url")),
                    timeout,
                )
                detailed.append(_parse_detail(listed_row, soup, cutoff))
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(f"detail {identity}: {type(exc).__name__}: {_clean(exc)}")
                meta["detail_errors"] += 1
        details_complete = bool(
            not errors
            and meta["detail_attempts"] == meta["detail_pages"]
            and len(detailed) == len(detail_candidates)
        )
        current_rows = [row for row in detailed if row["_source_end_date"] >= cutoff]
        expired_count = len(listed) - len(current_rows)
        application_controls_complete = bool(
            details_complete
            and all(
                bool(row.get("raw_fields", {}).get("detail_verified"))
                for row in detailed
            )
        )

        if details_complete and application_controls_complete:
            for row in current_rows:
                errors.extend(_privacy_errors(row))
            if not errors:
                persistable = []
                for row in current_rows:
                    clean_row = dict(row)
                    clean_row.pop("_source_end_date", None)
                    persistable.append(clean_row)
                deduper = dedupe_rows or _dedupe_default
                try:
                    result = list(deduper(persistable))
                except Exception as exc:
                    errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                    result = []
                if len(result) != len(persistable):
                    errors.append(
                        "dedupe changed official identity cardinality "
                        f"{len(persistable)} to {len(result)}"
                    )
                    result = []

        snapshot_complete = bool(
            list_complete and details_complete and application_controls_complete and not errors
        )
        if not snapshot_complete:
            result = []
        meta.update(
            {
                "current_source_count": len(current_rows),
                "expired_count": expired_count,
                "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
                "application_control_count": sum(
                    bool(row.get("raw_fields", {}).get("application_control_present"))
                    for row in current_rows
                ),
                "details_complete": details_complete,
                "application_controls_complete": application_controls_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": snapshot_complete,
                "returned_count": len(result),
                "no_current_data": bool(snapshot_complete and not current_rows),
                "no_current_reason": (
                    "all advertised CLC cohorts/classes are expired"
                    if snapshot_complete and not current_rows
                    else ""
                ),
                "municipality_coverage": [CHUNCHEON_MUNICIPALITY_CODE],
                "discovery_audit": dict(CHUNCHEON_DISCOVERY_AUDIT),
                "owner_boundary_audit": {
                    key: dict(value) for key, value in CHUNCHEON_OWNER_BOUNDARY_AUDIT.items()
                },
                "pii_fields_never_persisted": list(CHUNCHEON_PII_FIELDS_NEVER_PERSISTED),
                "pii_payload_persisted": False,
                "forbidden_applicant_endpoint_requests": 0,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return result, CHUNCHEON_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["pagination_complete"] = False
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], CHUNCHEON_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_chuncheon_education


__all__ = [
    "CHUNCHEON_ADDRESS",
    "CHUNCHEON_BRANCH",
    "CHUNCHEON_BWB_CANDIDATE_ID",
    "CHUNCHEON_BWB_PROVIDER",
    "CHUNCHEON_CANONICAL_CANDIDATE_ID",
    "CHUNCHEON_CANONICAL_URL",
    "CHUNCHEON_DISCOVERY_AUDIT",
    "CHUNCHEON_E_TYPES",
    "CHUNCHEON_HOME_CANDIDATE_ID",
    "CHUNCHEON_HOME_PROVIDER",
    "CHUNCHEON_HOME_URL",
    "CHUNCHEON_MUNICIPALITY_CODE",
    "CHUNCHEON_MUNICIPALITY_NAME",
    "CHUNCHEON_OWNER_BOUNDARY_AUDIT",
    "CHUNCHEON_PARSER",
    "CHUNCHEON_PII_FIELDS_NEVER_PERSISTED",
    "CHUNCHEON_PROVIDER",
    "ChuncheonContractError",
    "chuncheon_detail_url",
    "chuncheon_gisu_url",
    "collect",
    "collect_chuncheon_education",
    "is_chuncheon_bwb_separate_target",
    "is_chuncheon_education_target",
    "is_chuncheon_home_alias_target",
    "is_target",
]
