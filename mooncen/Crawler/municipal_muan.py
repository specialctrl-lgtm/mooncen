"""Fail-closed education catalogue collector for Muan-gun.

The registered municipal candidate is the generic Muan home page.  It is a
routing seed, not a catalogue.  The current official education menu exposes
two structured, identity-bearing catalogues owned by the same municipality:

* ``군민평생학습`` (140 rows over ten pages at the 2026-07-21 audit), and
* ``기타교육`` (six rows on one page at the same audit).

Both catalogues use the same fifteen-row YB course module.  A snapshot is
emitted only after every declared page, the immediately empty post-last page,
and stable first/last rechecks pass for both sources.  Only current/future
rows are opened.  Visible online application controls must be bound to the
same course identity, but the applicant form is never requested.

The current resident-centre and information-education pages are month-only
notices without exact per-course dates/status controls.  Hope-course posts
are resident proposals, and the library, sports service and education support
office have separate owners.  Those surfaces are audit evidence only.

Detail parsing is an allowlist.  Inquiry phone numbers, lecturer/staff names,
attachments, free-form introductions, source HTML and applicant data are
never persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


MUAN_PROVIDER = "MUNI_WWW_MUAN_GO_KR_AC45723C"
MUAN_CANDIDATE_ID = "MUNI_IR_757589E97FC8"
MUAN_MUNICIPALITY_CODE = "1281000000"
MUAN_MUNICIPALITY_NAME = "전남광주통합특별시 무안군"
MUAN_PROVIDER_NAME = "무안군청"

MUAN_HOST = "www.muan.go.kr"
MUAN_CANDIDATE_URL = "https://www.muan.go.kr/"
MUAN_EDUCATION_ROOT_URL = "https://www.muan.go.kr/www/education/register_courses"
MUAN_LIFELONG_PATH = "/www/education/register_courses/lifelong_study"
MUAN_OTHER_PATH = "/www/education/register_courses/other_education"
MUAN_LIFELONG_URL = f"https://{MUAN_HOST}{MUAN_LIFELONG_PATH}"
MUAN_OTHER_URL = f"https://{MUAN_HOST}{MUAN_OTHER_PATH}"

MUAN_PAGE_SIZE = 15
MUAN_MAX_WORKERS = 6
MUAN_FETCH_ATTEMPTS = 2
MUAN_RETRY_BACKOFF_SECONDS = 0.15
MUAN_MAX_HTML_BYTES = 3_000_000
MUAN_PARSER = (
    "muan_two_official_course_catalogues+all_pages+empty_sentinels+"
    "stable_boundaries+current_details+identity_bound_application_controls+"
    "pii_allowlist"
)
MUAN_OWNERSHIP_SCOPE = "muan_official_lifelong_and_other_education_catalogues"


@dataclass(frozen=True)
class _Source:
    code: str
    label: str
    path: str
    url: str
    category: str


MUAN_SOURCES: tuple[_Source, ...] = (
    _Source(
        "lifelong",
        "군민평생학습",
        MUAN_LIFELONG_PATH,
        MUAN_LIFELONG_URL,
        "평생교육",
    ),
    _Source(
        "other",
        "기타교육",
        MUAN_OTHER_PATH,
        MUAN_OTHER_URL,
        "기타교육",
    ),
)
_SOURCE_BY_CODE = {source.code: source for source in MUAN_SOURCES}
_SOURCE_BY_PATH = {source.path: source for source in MUAN_SOURCES}

MUAN_ALIAS_URLS: tuple[str, ...] = (
    MUAN_CANDIDATE_URL,
    MUAN_EDUCATION_ROOT_URL,
    MUAN_LIFELONG_URL,
    MUAN_OTHER_URL,
)
MUAN_SEPARATE_OWNER_URLS: Mapping[str, str] = {
    "muan_county_library": "https://lib.muan.go.kr/",
    "muan_education_support_office": "https://maed.jne.go.kr/",
    "muan_public_sports_facilities": "https://www.muan.go.kr/sports",
    "seungdal_culture_and_arts_center": "https://www.muan.go.kr/culture",
    "oh_seungwoo_museum_of_art": "https://www.muan.go.kr/museum",
}
MUAN_EXCLUDED_URLS: Mapping[str, str] = {
    "retired_resident_centre_course_module": ("https://www.muan.go.kr/www/education/register_courses/community_center"),
    "month_only_resident_centre_notice": ("https://www.muan.go.kr/www/education/register_courses/community_center_new"),
    "month_only_information_education_notice": ("https://www.muan.go.kr/www/education/register_courses/itedu/muan_edu"),
    "umbrella_digital_learning_notice": ("https://www.muan.go.kr/www/education/register_courses/itedu/jeonnam_edu"),
    "evergreen_online_learning_materials": ("https://www.muan.go.kr/www/education/register_courses/itedu/online_edu"),
    "resident_course_proposal_board": ("https://www.muan.go.kr/www/education/register_courses/hope_course"),
    "education_survey_not_catalogue": ("https://www.muan.go.kr/www/education/register_courses/question_investigation"),
    "external_online_lifelong_links": ("https://www.muan.go.kr/www/education/online_lifelong"),
}

MUAN_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    MUAN_CANDIDATE_ID: {
        "provider": MUAN_PROVIDER,
        "url": MUAN_CANDIDATE_URL,
        "decision": "promote_home_seed_to_two_same_owner_course_catalogues",
        "reason": "the registered home page is navigation, not a course inventory",
    }
}
MUAN_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "registered_seed_url": MUAN_CANDIDATE_URL,
    "canonical_catalogues": {
        "lifelong": MUAN_LIFELONG_URL,
        "other": MUAN_OTHER_URL,
    },
    "structured_source_totals": {"lifelong": 140, "other": 6},
    "structured_data_pages": {"lifelong": 10, "other": 1},
    "structured_unique_identities": {"lifelong": 140, "other": 6},
    "latest_end_dates": {"lifelong": "2026-06-30", "other": "2025-11-28"},
    "current_future_counts": {"lifelong": 0, "other": 0},
    "current_future_total": 0,
    "source_status_counts": {
        "lifelong": {"수강종료": 110, "수강확정": 27, "강의종료": 3},
        "other": {"수강종료": 6},
    },
    "source_application_method_counts": {
        "lifelong": {
            "서면접수": 83,
            "온라인접수": 41,
            "병합(온라인+서면)접수": 16,
        },
        "other": {"서면접수": 6},
    },
    "historical_institution_counts": {
        "lifelong": {
            "무안군 자치행정과": 138,
            "망운면사무소": 1,
            "무안군청 미래성장과 일자리팀": 1,
        },
        "other": {"무안군 주민생활과": 6},
    },
    "retired_resident_centre_rows": 18,
    "retired_resident_centre_latest_end_date": "2023-12-30",
    "resident_centre_notice_programme_rows": 11,
    "resident_centre_notice_exclusion": (
        "month-only shared period, no per-course source identity/status, and applicant-document flow"
    ),
    "information_education_rows": 6,
    "information_education_exclusion": (
        "month-only periods and phone/visit intake without per-course live status controls"
    ),
    "hope_course_resident_proposals": 29,
    "hope_course_exclusion": "resident-authored proposal board is not an offered-course catalogue",
    "separate_owner_roots": dict(MUAN_SEPARATE_OWNER_URLS),
    "conclusion": (
        "schedule the two identity-bearing municipal catalogues under one provider; "
        "keep notices, proposals and separate institutions outside this owner"
    ),
}

MUAN_PII_FIELDS_DISCARDED: tuple[str, ...] = (
    "강 사 명/강사명/강사소개",
    "문의전화/담당자/담당전화번호",
    "첨부파일/신청서/증빙서류",
    "강좌소개/강의계획/free-form body",
    "등록자이름/등록자아이디 search values",
    "applicant form/profile/payload",
    "source HTML",
)

MUAN_SEARCH_STATUSES: tuple[tuple[str, str], ...] = (
    ("all", "전체"),
    ("1", "접수대기"),
    ("2", "접수중"),
    ("4", "수강대기"),
    ("8", "수강중"),
    ("16", "수강종료"),
    ("32", "강의종료"),
    ("64", "폐강"),
    ("128", "수강확정"),
)
MUAN_SEARCH_TYPES: tuple[tuple[str, str], ...] = (
    ("title", "강좌명"),
    ("lecturer", "강사명"),
    ("institute", "교육기관"),
    ("reg_name", "등록자이름"),
    ("reg_id", "등록자아이디"),
)
MUAN_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "수강대기": "CLOSED",
    "수강중": "CLOSED",
    "수강종료": "CLOSED",
    "강의종료": "CLOSED",
    "수강확정": "CLOSED",
    "폐강": "CANCELLED",
}
_STATUS_CLASS: Mapping[str, str] = {
    "접수대기": "bt1",
    "접수중": "bt2",
    "수강대기": "bt3",
    "수강중": "bt3",
    "수강종료": "bt4",
    "강의종료": "bt4",
    "수강확정": "bt5",
    "폐강": "bt6",
}
_METHOD_DETAIL: Mapping[str, str] = {
    "서면접수": "서면",
    "온라인접수": "온라인",
    "병합(온라인+서면)접수": "병합(온라인+서면)",
}


class MuanContractError(ValueError):
    """Raised when a live Muan catalogue no longer matches its audited contract."""


@dataclass(frozen=True)
class _ListPage:
    source: str
    requested_page: int
    total: int
    source_pages: int
    rows: tuple[dict[str, Any], ...]
    empty_marker: bool


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"^[1-9]\d*$")
_CSRF_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RANGE_RE = re.compile(r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*(?P<end>20\d{2}-\d{2}-\d{2})$")
_DATETIME_RANGE_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})$"
)
_COUNT_RE = re.compile(r"^(?P<count>[\d,]+)$")
_WAIT_RE = re.compile(r"^\(\s*(?P<count>[\d,]+)\s*\)$")
_CAPACITY_RE = re.compile(r"^(?P<count>[\d,]+)\s*명$")
_DETAIL_CAPACITY_RE = re.compile(r"^(?P<count>[\d,]+)\s*명(?:\s*:\s*(?P<selection>.+))?$")
_FEE_RE = re.compile(r"^(?:무료|[\d,]+\s*원)$")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[\s().-]*\d{3,4}[\s.-]*\d{4}|"
    r"0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LIST_HEADERS = (
    "번호",
    "[기수]강좌명/강사명/신청기간/교육기간",
    "교육장소",
    "수강생선정방법 신청(대기)/정원",
    "접수현황",
)
_DETAIL_LABELS = (
    "강좌명(기수)",
    "신청기간",
    "교육기간",
    "수강신청방법",
    "수강대상선정방법",
    "교육대상",
    "보호자동의여부",
    "모집정원",
    "모집대기인원",
    "교육기관",
    "교육장소",
    "수강료",
    "문의전화",
    "강좌소개 강의계획",
    "강사명",
    "강사소개",
)
_ALLOWED_ROW_KEYS = frozenset(
    {
        "provider",
        "provider_course_id",
        "prefer_incoming_provider_course_id",
        "title",
        "description",
        "branch",
        "branch_code",
        "preserve_branch",
        "category",
        "program_type",
        "raw_url",
        "application_url",
        "application_type",
        "application_method_raw",
        "reservation_available",
        "status",
        "fee",
        "period",
        "start_date",
        "end_date",
        "apply_period",
        "apply_start_date",
        "apply_end_date",
        "schedule_raw",
        "target",
        "selection_method",
        "capacity_current",
        "capacity_wait",
        "capacity_total",
        "capacity_wait_total",
        "venue_name",
        "collection_category",
        "domain_category",
        "operator_type",
        "source_group",
        "service_group",
        "collection_type",
        "municipality_code",
        "municipality_name",
        "raw_fields",
    }
)
_ALLOWED_RAW_KEYS = frozenset(
    {
        "parser",
        "source_code",
        "source_identity",
        "source_page",
        "source_row_number",
        "source_status",
        "source_application_method",
        "detail_verified",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "instructor",
        "teacher",
        "lecturer",
        "manager",
        "staff",
        "contact",
        "phone",
        "email",
        "attachment",
        "attachments",
        "description_html",
        "detail_description",
        "source_html",
        "raw_html",
        "application_payload",
        "applicant",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _safe_port(parsed: Any) -> Optional[int]:
    try:
        return parsed.port
    except ValueError:
        return -1


def _canonical_public_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != MUAN_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return f"https://{MUAN_HOST}{parsed.path or '/'}"


def _canonical_any_https_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return f"https://{hostname}{parsed.path or '/'}"


def is_muan_education_target(target: Any) -> bool:
    compared = _canonical_public_url(_target_value(target, "url"))
    return bool(_clean(_target_value(target, "provider")) == MUAN_PROVIDER and compared and compared in MUAN_ALIAS_URLS)


def is_muan_alias_target(target: Any) -> bool:
    compared = _canonical_public_url(_target_value(target, "url"))
    return bool(compared and compared in MUAN_ALIAS_URLS)


def is_muan_excluded_target(target: Any) -> bool:
    compared = _canonical_any_https_url(_target_value(target, "url"))
    excluded = tuple(MUAN_EXCLUDED_URLS.values()) + tuple(MUAN_SEPARATE_OWNER_URLS.values())
    return bool(compared and compared in {_canonical_any_https_url(value) for value in excluded})


is_target = is_muan_education_target


def muan_list_url(source_code: str, page: int) -> str:
    source = _SOURCE_BY_CODE.get(_clean(source_code))
    if source is None or isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("source_code and positive integer page are required")
    return f"{source.url}?{urlencode((('page', page),))}"


def muan_detail_url(source_code: str, identity: Any) -> str:
    source = _SOURCE_BY_CODE.get(_clean(source_code))
    value = _clean(identity)
    if source is None or _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("source_code and positive course identity are required")
    return f"{source.url}?" + urlencode((("idx", value), ("mode", "view")))


def muan_application_url(source_code: str, identity: Any) -> str:
    source = _SOURCE_BY_CODE.get(_clean(source_code))
    value = _clean(identity)
    if source is None or _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("source_code and positive course identity are required")
    return f"{source.url}?" + urlencode((("lecture_idx", value), ("mode", "reserve_form")))


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("today must be an ISO date") from exc


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": MUAN_EDUCATION_ROOT_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _transport_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != MUAN_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.fragment
    ):
        return ""
    query = urlencode(
        sorted((key, item) for key, values in parse_qs(parsed.query, keep_blank_values=True).items() for item in values)
    )
    return f"https://{MUAN_HOST}{parsed.path or '/'}" + (f"?{query}" if query else "")


def _coerce_soup(value: Any, requested_url: str) -> BeautifulSoup:
    status = int(getattr(value, "status_code", 0) or 0)
    if status != 200:
        raise MuanContractError(f"unexpected HTTP status {status}")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location") or headers.get("location") or getattr(value, "history", None):
        raise MuanContractError("redirect response is not accepted")
    content_type = _clean(headers.get("Content-Type") or headers.get("content-type")).lower()
    if not (content_type.startswith("text/html") or content_type.startswith("application/xhtml+xml")):
        raise MuanContractError("response is not HTML")
    final_url = _clean(getattr(value, "url", ""))
    if not final_url or _transport_url(final_url) != _transport_url(requested_url):
        raise MuanContractError("final response URL changed")
    content = getattr(value, "content", b"")
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise MuanContractError("empty HTTP response")
    if len(content) > MUAN_MAX_HTML_BYTES:
        raise MuanContractError("HTTP response exceeded HTML byte cap")
    return BeautifulSoup(bytes(content), "html.parser", from_encoding="utf-8")


def _fetch_soup(
    url: str,
    *,
    timeout: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> BeautifulSoup:
    last_error: Optional[Exception] = None
    for attempt in range(MUAN_FETCH_ATTEMPTS):
        session: Any = None
        try:
            session = session_factory()
            return _coerce_soup(fetcher(session, url, timeout), url)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < MUAN_FETCH_ATTEMPTS:
                time.sleep(MUAN_RETRY_BACKOFF_SECONDS * (attempt + 1))
        finally:
            _close_quietly(session)
    assert last_error is not None
    raise last_error


def _single_query(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _owned_url(value: Any, source: _Source) -> Any:
    parsed = urlparse(urljoin(source.url, _clean(value)))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != MUAN_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.fragment
        or parsed.path != source.path
    ):
        raise MuanContractError(f"{source.code}: course URL ownership changed")
    return parsed


def _detail_link(value: Any, source: _Source) -> tuple[str, str]:
    parsed = _owned_url(value, source)
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query(query, "idx")
    if (
        set(query) != {"idx", "mode"}
        or _single_query(query, "mode") != "view"
        or _POSITIVE_ID_RE.fullmatch(identity) is None
    ):
        raise MuanContractError(f"{source.code}: detail identity link changed")
    return identity, muan_detail_url(source.code, identity)


def _application_link(value: Any, source: _Source) -> tuple[str, str]:
    parsed = _owned_url(value, source)
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query(query, "lecture_idx")
    if (
        set(query) != {"lecture_idx", "mode"}
        or _single_query(query, "mode") != "reserve_form"
        or _POSITIVE_ID_RE.fullmatch(identity) is None
    ):
        raise MuanContractError(f"{source.code}: application identity link changed")
    return identity, muan_application_url(source.code, identity)


def _pagination_link(value: Any, source: _Source) -> int:
    parsed = _owned_url(value, source)
    query = parse_qs(parsed.query, keep_blank_values=True)
    raw = _single_query(query, "page")
    if set(query) != {"page"} or _POSITIVE_ID_RE.fullmatch(raw) is None:
        raise MuanContractError(f"{source.code}: pagination link changed")
    return int(raw)


def _options(node: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in node.find_all("option", recursive=False)
    )


def _validate_list_document(soup: BeautifulSoup, source: _Source, page: int) -> None:
    titles = soup.select("head > title")
    if len(titles) != 1:
        raise MuanContractError(f"{source.code} page {page}: list title changed")
    title = _clean(titles[0].get_text(" ", strip=True))
    if (
        not title.startswith(f"{page} 페이지 목록보기 <")
        or source.label not in title
        or "교육신청" not in title
        or not title.endswith("무안군청")
    ):
        raise MuanContractError(f"{source.code} page {page}: official list title changed")
    forms = soup.select("form#list_search")
    if len(forms) != 1:
        raise MuanContractError(f"{source.code} page {page}: search form changed")
    form = forms[0]
    action = urlparse(urljoin(source.url, _clean(form.get("action"))))
    if (
        tuple(form.get("class") or ()) != ("list_sch2",)
        or form.has_attr("method")
        or action.scheme != "https"
        or (action.hostname or "").lower() != MUAN_HOST
        or action.path != source.path
        or action.query
        or action.fragment
    ):
        raise MuanContractError(f"{source.code} page {page}: search ownership changed")
    csrf = form.select('input[type="hidden"][name="csrf_token"]')
    statuses = form.select('select#search_status[name="search_status"]')
    search_types = form.select('select#search_type[name="search_type"]')
    words = form.select('input[type="text"]#search_word[name="search_word"]')
    submits = form.select('input[type="submit"][value="검색"]')
    if (
        len(csrf) != 1
        or _CSRF_RE.fullmatch(_clean(csrf[0].get("value"))) is None
        or len(statuses) != 1
        or _options(statuses[0]) != MUAN_SEARCH_STATUSES
        or len(search_types) != 1
        or _options(search_types[0]) != MUAN_SEARCH_TYPES
        or len(words) != 1
        or _clean(words[0].get("value"))
        or len(submits) != 1
    ):
        raise MuanContractError(f"{source.code} page {page}: search taxonomy changed")


def _date_range(value: Any, identity: str, label: str) -> tuple[date, date, str]:
    text = _clean(value)
    match = _DATE_RANGE_RE.fullmatch(text)
    if match is None:
        raise MuanContractError(f"course {identity}: {label} changed")
    try:
        start = date.fromisoformat(match.group("start"))
        end = date.fromisoformat(match.group("end"))
    except ValueError as exc:
        raise MuanContractError(f"course {identity}: invalid {label}") from exc
    if start > end:
        raise MuanContractError(f"course {identity}: reversed {label}")
    return start, end, f"{start.isoformat()} ~ {end.isoformat()}"


def _datetime_range(value: Any, identity: str) -> tuple[datetime, datetime, str]:
    text = _clean(value)
    match = _DATETIME_RANGE_RE.fullmatch(text)
    if match is None:
        raise MuanContractError(f"course {identity}: application period changed")
    try:
        start = datetime.fromisoformat(match.group("start"))
        end = datetime.fromisoformat(match.group("end"))
    except ValueError as exc:
        raise MuanContractError(f"course {identity}: invalid application period") from exc
    if start > end:
        raise MuanContractError(f"course {identity}: reversed application period")
    return start, end, f"{match.group('start')} ~ {match.group('end')}"


def _count(value: Any, regex: re.Pattern[str], identity: str, label: str) -> int:
    match = regex.fullmatch(_clean(value))
    if match is None:
        raise MuanContractError(f"course {identity}: {label} changed")
    return int(match.group("count").replace(",", ""))


def _status_and_control(cell: Any, identity: str, source: _Source, method: str) -> tuple[str, str, str]:
    children = cell.find_all(["span", "a"], recursive=False)
    if not children:
        raise MuanContractError(f"course {identity}: status cell changed")
    status_nodes: list[tuple[Any, str]] = []
    controls: list[tuple[str, str]] = []
    offline_badge = False
    for node in children:
        text = _clean(node.get_text(" ", strip=True))
        stripped = _clean(text.replace("접수하기", ""))
        if stripped in MUAN_STATUS_MAP:
            status_nodes.append((node, stripped))
            if node.name == "a":
                controls.append(_application_link(node.get("href"), source))
        elif text == "접수하기" and node.name == "a":
            controls.append(_application_link(node.get("href"), source))
        elif text == "서면접수" and node.name == "span":
            offline_badge = True
        else:
            raise MuanContractError(f"course {identity}: unknown status control appeared")
    if len(status_nodes) != 1:
        raise MuanContractError(f"course {identity}: status marker changed")
    node, source_status = status_nodes[0]
    classes = tuple(node.get("class") or ())
    if classes:
        if (
            "s_bt" not in classes
            or _STATUS_CLASS[source_status] not in classes
            or any(value not in {"s_bt", _STATUS_CLASS[source_status]} for value in classes)
        ):
            raise MuanContractError(f"course {identity}: status class changed")
    elif not (method == "서면접수" and offline_badge):
        raise MuanContractError(f"course {identity}: unstyled status marker changed")
    if offline_badge != (method == "서면접수"):
        raise MuanContractError(f"course {identity}: offline method badge changed")
    if len(controls) != len(set(controls)):
        raise MuanContractError(f"course {identity}: duplicate application controls")
    status = MUAN_STATUS_MAP[source_status]
    online_capable = "온라인" in method
    if status == "OPEN" and online_capable:
        if len(controls) != 1 or controls[0][0] != identity:
            raise MuanContractError(f"course {identity}: open online row lacks one identity-bound control")
        return source_status, status, controls[0][1]
    if controls:
        raise MuanContractError(f"course {identity}: inactive/offline row exposes an application control")
    return source_status, status, ""


def _parse_list_row(row: Any, source: _Source, page: int) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != len(_LIST_HEADERS):
        raise MuanContractError(f"{source.code} page {page}: row field count changed")
    row_number = _count(cells[0].get_text(" ", strip=True), _COUNT_RE, "unknown", "row number")
    links = cells[1].select(":scope > a[href]")
    if len(links) != 1:
        raise MuanContractError(f"{source.code} page {page}: detail link changed")
    identity, raw_url = _detail_link(links[0].get("href"), source)
    spans = links[0].find_all("span", recursive=False)
    if (
        len(spans) != 5
        or tuple(spans[0].get("class") or ()) != ("fc_blue3",)
        or any(span.get("class") for span in spans[1:])
    ):
        raise MuanContractError(f"course {identity}: list information schema changed")
    title = _clean(spans[0].get_text(" ", strip=True))
    instructor_label = _clean(spans[1].get_text(" ", strip=True))
    apply_text = _clean(spans[2].get_text(" ", strip=True))
    period_text = _clean(spans[3].get_text(" ", strip=True))
    method_text = _clean(spans[4].get_text(" ", strip=True))
    if (
        not title
        or not instructor_label.startswith("강 사 명 :")
        or not _clean(instructor_label[len("강 사 명 :") :])
        or not apply_text.startswith("신청기간 :")
        or not period_text.startswith("교육기간 :")
        or not method_text.startswith("접수방법 :")
    ):
        raise MuanContractError(f"course {identity}: list labels changed")
    apply_start, apply_end, apply_period = _datetime_range(apply_text[len("신청기간 :") :], identity)
    start, end, period = _date_range(period_text[len("교육기간 :") :], identity, "education period")
    method = _clean(method_text[len("접수방법 :") :])
    if method not in _METHOD_DETAIL:
        raise MuanContractError(f"course {identity}: application method changed")
    place_spans = cells[2].find_all("span", recursive=False)
    if len(place_spans) == 0:
        # One audited legacy row predates the two-span venue template and
        # exposes only its institution as direct cell text.  A current row in
        # this shape still has to obtain and verify its venue from detail.
        branch = _clean(cells[2].get_text(" ", strip=True))
        venue = ""
    elif (
        len(place_spans) == 2
        and not place_spans[0].get("class")
        and tuple(place_spans[1].get("class") or ()) == ("fc_blue3",)
    ):
        branch = _clean(place_spans[0].get_text(" ", strip=True))
        venue = _clean(place_spans[1].get_text(" ", strip=True))
        if not branch or not venue:
            raise MuanContractError(f"course {identity}: place values changed")
    else:
        raise MuanContractError(f"course {identity}: place schema changed")
    selection_nodes = cells[3].find_all("div", recursive=False)
    applications = cells[3].select(":scope > span.apply")
    waits = cells[3].select(":scope > span.wait")
    capacities = cells[3].select(":scope > span.fix_poeple")
    direct_spans = cells[3].find_all("span", recursive=False)
    if (
        len(selection_nodes) != 1
        or len(applications) != 1
        or len(waits) != 1
        or len(capacities) != 1
        or len(direct_spans) != 3
    ):
        raise MuanContractError(f"course {identity}: capacity schema changed")
    selection = _clean(selection_nodes[0].get_text(" ", strip=True))
    if not selection:
        raise MuanContractError(f"course {identity}: selection method is empty")
    capacity_current = _count(applications[0].get_text(" ", strip=True), _COUNT_RE, identity, "application count")
    capacity_wait = _count(waits[0].get_text(" ", strip=True), _WAIT_RE, identity, "waiting count")
    capacity_total = _count(capacities[0].get_text(" ", strip=True), _CAPACITY_RE, identity, "capacity")
    source_status, status, application_url = _status_and_control(cells[4], identity, source, method)
    return {
        "source": source.code,
        "identity": identity,
        "source_page": page,
        "row_number": row_number,
        "title": title,
        "branch": branch,
        "venue": venue,
        "selection": selection,
        "source_status": source_status,
        "status": status,
        "start": start,
        "end": end,
        "period": period,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "apply_period": apply_period,
        "method": method,
        "capacity_current": capacity_current,
        "capacity_wait": capacity_wait,
        "capacity_total": capacity_total,
        "raw_url": raw_url,
        "application_url": application_url,
    }


def _validate_pagination(
    soup: BeautifulSoup,
    source: _Source,
    *,
    requested_page: int,
    source_pages: int,
    sentinel: bool,
) -> None:
    roots = soup.select("div.list_paging > div.num")
    if len(roots) != 1:
        raise MuanContractError(f"{source.code} page {requested_page}: pager changed")
    root = roots[0]
    active = root.select(":scope > a.on")
    last_links = root.select(":scope > a.last[href]")
    linked_pages: list[tuple[Any, int]] = []
    for node in root.select(":scope > a[href]"):
        linked_pages.append((node, _pagination_link(node.get("href"), source)))
    if any(page < 1 or page > source_pages for _, page in linked_pages):
        raise MuanContractError(f"{source.code} page {requested_page}: pager escaped boundary")
    if sentinel:
        if active or last_links:
            raise MuanContractError(f"{source.code}: sentinel exposes active/last marker")
        previous = root.select(":scope > a.prev[href]")
        if len(previous) != 1 or _pagination_link(previous[0].get("href"), source) != source_pages:
            raise MuanContractError(f"{source.code}: sentinel boundary changed")
        return
    if (
        len(active) != 1
        or _clean(active[0].get_text(" ", strip=True)) != str(requested_page)
        or active[0].has_attr("href")
    ):
        raise MuanContractError(f"{source.code} page {requested_page}: active page changed")
    if source_pages > 1 and requested_page < source_pages:
        if len(last_links) != 1 or _pagination_link(last_links[0].get("href"), source) != source_pages:
            raise MuanContractError(f"{source.code} page {requested_page}: last boundary changed")
    elif last_links:
        raise MuanContractError(f"{source.code} page {requested_page}: terminal last link appeared")


def _parse_list_page(
    soup: BeautifulSoup,
    source: _Source,
    page: int,
    *,
    sentinel: bool = False,
) -> _ListPage:
    _validate_list_document(soup, source, page)
    tables = soup.select("table.list_table")
    if len(tables) != 1:
        raise MuanContractError(f"{source.code} page {page}: list table changed")
    table = tables[0]
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead > tr > th"))
    if headers != _LIST_HEADERS:
        raise MuanContractError(f"{source.code} page {page}: list columns changed")
    captions = table.select(":scope > caption")
    if len(captions) != 1:
        raise MuanContractError(f"{source.code} page {page}: caption changed")
    pattern = re.compile(
        rf"^{re.escape(source.label)} 게시물\. 총 (?P<total>[\d,]+)건, "
        rf"(?P<pages>[\d,]+)페이지 중 (?P<active>[\d,]+)페이지 "
        rf"(?P<visible>[\d,]+)건 입니다\.$"
    )
    match = pattern.fullmatch(_clean(captions[0].get_text(" ", strip=True)))
    if match is None:
        raise MuanContractError(f"{source.code} page {page}: caption grammar changed")
    total = int(match.group("total").replace(",", ""))
    source_pages = int(match.group("pages").replace(",", ""))
    active = int(match.group("active").replace(",", ""))
    visible = int(match.group("visible").replace(",", ""))
    if total < 1 or source_pages != math.ceil(total / MUAN_PAGE_SIZE) or active != page:
        raise MuanContractError(f"{source.code} page {page}: total/page declaration changed")
    expected_visible = 0 if page > source_pages else min(MUAN_PAGE_SIZE, total - (page - 1) * MUAN_PAGE_SIZE)
    if visible != expected_visible:
        raise MuanContractError(f"{source.code} page {page}: visible count changed")
    bodies = table.find_all("tbody", recursive=False)
    if len(bodies) != 1:
        raise MuanContractError(f"{source.code} page {page}: table body changed")
    source_rows = bodies[0].find_all("tr", recursive=False)
    data_rows = [row for row in source_rows if row.select_one("td.lecture_title > a[href]")]
    if data_rows:
        if len(data_rows) != len(source_rows) or len(data_rows) != visible:
            raise MuanContractError(f"{source.code} page {page}: mixed/partial rows")
        rows = tuple(_parse_list_row(row, source, page) for row in data_rows)
        empty_marker = False
    else:
        cells = source_rows[0].find_all("td", recursive=False) if len(source_rows) == 1 else []
        if (
            len(cells) != 1
            or _clean(cells[0].get("colspan")) != str(len(_LIST_HEADERS))
            or _clean(cells[0].get_text(" ", strip=True)) != "개설된 강좌가 없습니다."
            or visible != 0
        ):
            raise MuanContractError(f"{source.code} page {page}: empty marker changed")
        rows = ()
        empty_marker = True
    if sentinel != (page == source_pages + 1):
        raise MuanContractError(f"{source.code} page {page}: sentinel boundary mismatch")
    if sentinel != empty_marker:
        raise MuanContractError(f"{source.code} page {page}: data/empty boundary changed")
    _validate_pagination(
        soup,
        source,
        requested_page=page,
        source_pages=source_pages,
        sentinel=sentinel,
    )
    return _ListPage(source.code, page, total, source_pages, rows, empty_marker)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("source")),
            _clean(row.get("identity")),
            str(row.get("row_number")),
            _clean(row.get("title")),
            _clean(row.get("branch")),
            _clean(row.get("venue")),
            _clean(row.get("source_status")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
            _clean(row.get("method")),
            str(row.get("capacity_current")),
            str(row.get("capacity_wait")),
            str(row.get("capacity_total")),
            _clean(row.get("raw_url")),
            _clean(row.get("application_url")),
        )
        for row in rows
    )


def _detail_schema(table: Any, source: _Source, identity: str) -> dict[str, Any]:
    bodies = table.find_all("tbody", recursive=False)
    if len(bodies) != 1:
        raise MuanContractError(f"course {identity}: detail body changed")
    rows = bodies[0].find_all("tr", recursive=False)
    if len(rows) != len(_DETAIL_LABELS):
        raise MuanContractError(f"course {identity}: detail field count changed")
    values: dict[str, Any] = {}
    labels: list[str] = []
    for row in rows:
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) != 2 or cells[0].name != "th" or cells[1].name != "td":
            raise MuanContractError(f"course {identity}: detail row schema changed")
        label = _clean(cells[0].get_text(" ", strip=True))
        labels.append(label)
        values[label] = cells[1]
    if tuple(labels) != _DETAIL_LABELS:
        raise MuanContractError(f"course {identity}: detail labels changed")
    return values


def _safe_detail_text(values: Mapping[str, Any], identity: str, label: str, *, allow_empty: bool = False) -> str:
    node = values.get(label)
    text = _clean(node.get_text(" ", strip=True)) if node is not None else ""
    if not text and not allow_empty:
        raise MuanContractError(f"course {identity}: {label} is empty")
    return text


def _branch_code(value: str) -> str:
    digest = hashlib.sha1(_normalized(value).encode("utf-8")).hexdigest()[:10].upper()
    return f"MUAN_BRANCH_{digest}"


def _parse_detail(soup: BeautifulSoup, listed: Mapping[str, Any], source: _Source) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    titles = soup.select("head > title")
    if (
        len(titles) != 1
        or source.label not in _clean(titles[0].get_text(" ", strip=True))
        or not _clean(titles[0].get_text(" ", strip=True)).endswith("무안군청")
    ):
        raise MuanContractError(f"course {identity}: detail title changed")
    headings = [node for node in soup.select("#content > h3") if _clean(node.get_text(" ", strip=True)) == "강좌정보"]
    tables = soup.select("#content > table.view_table")
    if len(headings) != 1 or len(tables) != 1:
        raise MuanContractError(f"course {identity}: detail catalogue changed")
    values = _detail_schema(tables[0], source, identity)
    title = _safe_detail_text(values, identity, "강좌명(기수)")
    if _normalized(title) != _normalized(listed.get("title")):
        raise MuanContractError(f"course {identity}: detail/list title mismatch")
    apply_start, apply_end, apply_period = _datetime_range(_safe_detail_text(values, identity, "신청기간"), identity)
    start, end, period = _date_range(
        _safe_detail_text(values, identity, "교육기간"), identity, "detail education period"
    )
    if (
        apply_start != listed.get("apply_start")
        or apply_end != listed.get("apply_end")
        or start != listed.get("start")
        or end != listed.get("end")
    ):
        raise MuanContractError(f"course {identity}: detail/list period mismatch")
    method = _safe_detail_text(values, identity, "수강신청방법")
    if method != _METHOD_DETAIL.get(_clean(listed.get("method"))):
        raise MuanContractError(f"course {identity}: detail/list method mismatch")
    selection = _safe_detail_text(values, identity, "수강대상선정방법")
    if _normalized(selection) != _normalized(listed.get("selection")):
        raise MuanContractError(f"course {identity}: detail/list selection mismatch")
    target = _safe_detail_text(values, identity, "교육대상")
    capacity_text = _safe_detail_text(values, identity, "모집정원")
    capacity_match = _DETAIL_CAPACITY_RE.fullmatch(capacity_text)
    if capacity_match is None:
        raise MuanContractError(f"course {identity}: detail capacity changed")
    capacity_total = int(capacity_match.group("count").replace(",", ""))
    if capacity_total != int(listed.get("capacity_total") or -1):
        raise MuanContractError(f"course {identity}: detail/list capacity mismatch")
    if capacity_match.group("selection") and _normalized(capacity_match.group("selection")) != _normalized(selection):
        raise MuanContractError(f"course {identity}: detail capacity selection mismatch")
    capacity_wait_total = _count(
        _safe_detail_text(values, identity, "모집대기인원"),
        _CAPACITY_RE,
        identity,
        "detail waiting capacity",
    )
    branch = _safe_detail_text(values, identity, "교육기관")
    venue = _safe_detail_text(values, identity, "교육장소")
    if _clean(listed.get("branch")) and _normalized(branch) != _normalized(listed.get("branch")):
        raise MuanContractError(f"course {identity}: detail/list institution mismatch")
    if _clean(listed.get("venue")) and _normalized(venue) != _normalized(listed.get("venue")):
        raise MuanContractError(f"course {identity}: detail/list venue mismatch")
    fee = _safe_detail_text(values, identity, "수강료")
    if _FEE_RE.fullmatch(fee) is None:
        raise MuanContractError(f"course {identity}: fee changed")
    controls: list[tuple[str, str]] = []
    for link in soup.select("#content a[href]"):
        parsed = urlparse(urljoin(source.url, _clean(link.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if "lecture_idx" in query or _single_query(query, "mode").startswith("reserve_form"):
            controls.append(_application_link(link.get("href"), source))
    expected_control = _clean(listed.get("application_url"))
    unique_controls = sorted(set(controls))
    if expected_control:
        if unique_controls != [(identity, expected_control)]:
            raise MuanContractError(f"course {identity}: detail application control mismatch")
    elif unique_controls:
        raise MuanContractError(f"course {identity}: unexpected detail application control")
    application_type = (
        "ONLINE_RESERVATION"
        if expected_control
        else "OFFLINE_APPLY"
        if method in {"서면", "병합(온라인+서면)"}
        else "INFO_ONLY"
    )
    provider_course_id = f"{MUAN_PROVIDER}:{source.code}:{identity}"
    return {
        "provider": MUAN_PROVIDER,
        "provider_course_id": provider_course_id,
        "prefer_incoming_provider_course_id": True,
        "title": _clean(listed.get("title")),
        "description": _clean(listed.get("title")),
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": source.category,
        "program_type": "강좌",
        "raw_url": _clean(listed.get("raw_url")),
        "application_url": expected_control or _clean(listed.get("raw_url")),
        "application_type": application_type,
        "application_method_raw": method,
        "reservation_available": bool(expected_control),
        "status": _clean(listed.get("status")),
        "fee": fee,
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": apply_period,
        "apply_start_date": apply_start.date().isoformat(),
        "apply_end_date": apply_end.date().isoformat(),
        "schedule_raw": period,
        "target": target,
        "selection_method": selection,
        "capacity_current": int(listed.get("capacity_current") or 0),
        "capacity_wait": int(listed.get("capacity_wait") or 0),
        "capacity_total": capacity_total,
        "capacity_wait_total": capacity_wait_total,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "collection_type": MUAN_PARSER,
        "municipality_code": MUAN_MUNICIPALITY_CODE,
        "municipality_name": MUAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": MUAN_PARSER,
            "source_code": source.code,
            "source_identity": identity,
            "source_page": int(listed.get("source_page") or 0),
            "source_row_number": int(listed.get("row_number") or 0),
            "source_status": _clean(listed.get("source_status")),
            "source_application_method": _clean(listed.get("method")),
            "detail_verified": True,
            "application_control_present": bool(expected_control),
            "application_control_contract": (
                "identity_bound_unfetched_form" if expected_control else "verified_no_control"
            ),
            "application_control_verified": True,
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) != _ALLOWED_ROW_KEYS:
        errors.append("persisted row exceeded the exact PII-safe field allowlist")
    if set(row) & _FORBIDDEN_KEYS:
        errors.append("forbidden detail/PII keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or set(raw_fields) != _ALLOWED_RAW_KEYS:
        errors.append("raw_fields exceeded the exact PII-safe allowlist")
    payload = repr({key: value for key, value in row.items() if key not in {"raw_url", "application_url"}})
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("PII-like contact value persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form description persisted")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "source_cap_reached": False,
        "configured_collection_error": error,
        "municipality_code": MUAN_MUNICIPALITY_CODE,
        "municipality_name": MUAN_MUNICIPALITY_NAME,
        "provider_name": MUAN_PROVIDER_NAME,
        "canonical_urls": {source.code: source.url for source in MUAN_SOURCES},
        "ownership_scope": MUAN_OWNERSHIP_SCOPE,
        "candidate_audit": {key: dict(value) for key, value in MUAN_CANDIDATE_AUDIT.items()},
        "discovery_audit": dict(MUAN_DISCOVERY_AUDIT),
        "excluded_urls": dict(MUAN_EXCLUDED_URLS),
        "separate_owner_urls": dict(MUAN_SEPARATE_OWNER_URLS),
        "municipality_coverage": [MUAN_MUNICIPALITY_CODE],
        "pii_fields_discarded": list(MUAN_PII_FIELDS_DISCARDED),
        "pii_payload_persisted": False,
    }


def collect_muan_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 160,
    detail_limit: int = 120,
    today: Optional[date | datetime | str] = None,
    max_workers: int = MUAN_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future snapshot from both Muan catalogues."""

    meta = _base_meta()
    if not is_muan_education_target(target):
        meta["configured_collection_error"] = "target does not match the exact Muan education catalogue owner"
        return [], MUAN_PARSER, meta
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages < len(MUAN_SOURCES)
        or isinstance(detail_limit, bool)
        or not isinstance(detail_limit, int)
        or detail_limit < 0
        or isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], MUAN_PARSER, meta
    try:
        cutoff = _today(today)
    except ValueError as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], MUAN_PARSER, meta
    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    workers = min(max_workers, MUAN_MAX_WORKERS)
    meta["network_concurrency"] = workers

    def fetch_list(source: _Source, page: int, *, sentinel: bool = False) -> _ListPage:
        soup = _fetch_soup(
            muan_list_url(source.code, page),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return _parse_list_page(soup, source, page, sentinel=sentinel)

    first_pages: dict[str, _ListPage] = {}
    errors: list[str] = []
    for source in MUAN_SOURCES:
        try:
            first_pages[source.code] = fetch_list(source, 1)
            meta["list_requests"] += 1
            meta["pages"] += 1
        except Exception as exc:
            errors.append(f"{source.code} page 1: {type(exc).__name__}: {_clean(exc)}")
    if errors or len(first_pages) != len(MUAN_SOURCES):
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], MUAN_PARSER, meta
    required = sum(page.source_pages + 3 for page in first_pages.values())
    meta.update(
        {
            "required_list_requests": required,
            "declared_source_totals": {code: page.total for code, page in first_pages.items()},
            "declared_source_pages": {code: page.source_pages for code, page in first_pages.items()},
        }
    )
    if required > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of {required} required list requests"
                ),
            }
        )
        return [], MUAN_PARSER, meta

    all_pages: dict[str, dict[int, _ListPage]] = {code: {1: page} for code, page in first_pages.items()}
    sentinels: dict[str, _ListPage] = {}
    first_rechecks: dict[str, _ListPage] = {}
    last_rechecks: dict[str, _ListPage] = {}
    for source in MUAN_SOURCES:
        first = first_pages[source.code]
        for page in range(2, first.source_pages + 1):
            try:
                all_pages[source.code][page] = fetch_list(source, page)
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(f"{source.code} page {page}: {type(exc).__name__}: {_clean(exc)}")
        for kind, page, sentinel in (
            ("sentinel", first.source_pages + 1, True),
            ("first_recheck", 1, False),
            ("last_recheck", first.source_pages, False),
        ):
            try:
                parsed = fetch_list(source, page, sentinel=sentinel)
                meta["list_requests"] += 1
                meta["pages"] += 1
                if kind == "sentinel":
                    sentinels[source.code] = parsed
                elif kind == "first_recheck":
                    first_rechecks[source.code] = parsed
                else:
                    last_rechecks[source.code] = parsed
            except Exception as exc:
                errors.append(f"{source.code} {kind} page {page}: {type(exc).__name__}: {_clean(exc)}")

    source_rows: dict[str, list[dict[str, Any]]] = {}
    page_counts: dict[str, dict[int, int]] = {}
    identity_duplicates: dict[str, int] = {}
    raw_url_duplicates: dict[str, int] = {}
    row_number_duplicates: dict[str, int] = {}
    for source in MUAN_SOURCES:
        first = first_pages[source.code]
        pages = all_pages[source.code]
        page_counts[source.code] = {page: len(parsed.rows) for page, parsed in sorted(pages.items())}
        for page, parsed in pages.items():
            if parsed.total != first.total or parsed.source_pages != first.source_pages:
                errors.append(f"{source.code} page {page}: total/page boundary changed")
            if not parsed.rows or parsed.empty_marker:
                errors.append(f"{source.code} page {page}: declared data page is empty")
        sentinel = sentinels.get(source.code)
        if (
            sentinel is None
            or sentinel.total != first.total
            or sentinel.source_pages != first.source_pages
            or sentinel.rows
            or not sentinel.empty_marker
        ):
            errors.append(f"{source.code}: immediate post-last sentinel is not stable empty")
        else:
            meta["sentinel_requests"] += 1
        first_recheck = first_rechecks.get(source.code)
        last = pages.get(first.source_pages)
        last_recheck = last_rechecks.get(source.code)
        if first_recheck is None or last is None or last_recheck is None:
            errors.append(f"{source.code}: first/last stability recheck missing")
        else:
            meta["stability_rechecks"] += 2
            if (
                first_recheck.total != first.total
                or first_recheck.source_pages != first.source_pages
                or _page_signature(first_recheck.rows) != _page_signature(first.rows)
            ):
                errors.append(f"{source.code}: first-page stability recheck changed")
            if (
                last_recheck.total != first.total
                or last_recheck.source_pages != first.source_pages
                or _page_signature(last_recheck.rows) != _page_signature(last.rows)
            ):
                errors.append(f"{source.code}: last-page stability recheck changed")
        rows = [
            row
            for page in range(1, first.source_pages + 1)
            for row in pages.get(
                page,
                _ListPage(source.code, page, first.total, first.source_pages, (), True),
            ).rows
        ]
        source_rows[source.code] = rows
        identities = [_clean(row.get("identity")) for row in rows]
        raw_urls = [_clean(row.get("raw_url")) for row in rows]
        row_numbers = [int(row.get("row_number") or 0) for row in rows]
        identity_duplicates[source.code] = len(identities) - len(set(identities))
        raw_url_duplicates[source.code] = len(raw_urls) - len(set(raw_urls))
        row_number_duplicates[source.code] = len(row_numbers) - len(set(row_numbers))
        if len(rows) != first.total:
            errors.append(f"{source.code}: complete source row count {len(rows)} != {first.total}")
        if sorted(row_numbers) != list(range(1, first.total + 1)):
            errors.append(f"{source.code}: official row numbers do not reconcile total")
        if identity_duplicates[source.code]:
            errors.append(f"{source.code}: {identity_duplicates[source.code]} duplicate identities")
        if raw_url_duplicates[source.code]:
            errors.append(f"{source.code}: {raw_url_duplicates[source.code]} duplicate detail URLs")
        if row_number_duplicates[source.code]:
            errors.append(f"{source.code}: {row_number_duplicates[source.code]} duplicate row numbers")

    listed = [row for source in MUAN_SOURCES for row in source_rows[source.code]]
    current_listed = [row for row in listed if row["end"] >= cutoff]
    historical_listed = [row for row in listed if row["end"] < cutoff]
    for row in current_listed:
        status = _clean(row.get("status"))
        apply_start = row.get("apply_start")
        apply_end = row.get("apply_end")
        if status == "OPEN" and not (
            isinstance(apply_start, datetime)
            and isinstance(apply_end, datetime)
            and apply_start.date() <= cutoff <= apply_end.date()
        ):
            errors.append(f"course {row['source']}:{row['identity']}: OPEN date contradiction")
        if status == "SCHEDULED" and isinstance(apply_start, datetime) and cutoff > apply_start.date():
            errors.append(f"course {row['source']}:{row['identity']}: SCHEDULED date contradiction")
    list_complete = bool(
        not errors
        and all(len(all_pages[source.code]) == first_pages[source.code].source_pages for source in MUAN_SOURCES)
        and meta["list_requests"] == required
        and meta["sentinel_requests"] == len(MUAN_SOURCES)
        and meta["stability_rechecks"] == len(MUAN_SOURCES) * 2
    )
    meta.update(
        {
            "data_pages": sum(len(value) for value in all_pages.values()),
            "page_counts": page_counts,
            "source_totals": {source.code: len(source_rows[source.code]) for source in MUAN_SOURCES},
            "source_pages": {source.code: first_pages[source.code].source_pages for source in MUAN_SOURCES},
            "source_total": len(listed),
            "source_rows": len(listed),
            "current_source_count": len(current_listed),
            "current_future_counts": {
                source.code: sum(row["source"] == source.code for row in current_listed) for source in MUAN_SOURCES
            },
            "expired_counts": {
                source.code: sum(row["source"] == source.code for row in historical_listed) for source in MUAN_SOURCES
            },
            "identity_duplicate_counts": identity_duplicates,
            "raw_url_duplicate_counts": raw_url_duplicates,
            "row_number_duplicate_counts": row_number_duplicates,
            "source_status_counts": {
                source.code: dict(Counter(row["source_status"] for row in source_rows[source.code]))
                for source in MUAN_SOURCES
            },
            "source_application_method_counts": {
                source.code: dict(Counter(row["method"] for row in source_rows[source.code])) for source in MUAN_SOURCES
            },
            "source_application_control_count": sum(bool(row.get("application_url")) for row in listed),
            "pagination_detected": any(first_pages[source.code].source_pages > 1 for source in MUAN_SOURCES),
            "pagination_complete": list_complete,
        }
    )
    if not list_complete:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], MUAN_PARSER, meta
    if len(current_listed) > detail_limit:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit cap allows {detail_limit} of {len(current_listed)} required current details"
                ),
            }
        )
        return [], MUAN_PARSER, meta

    meta["detail_attempts"] = len(current_listed)
    detailed: dict[tuple[str, str], dict[str, Any]] = {}
    detail_errors: list[str] = []

    def fetch_detail(row: Mapping[str, Any]) -> tuple[tuple[str, str], dict[str, Any]]:
        source = _SOURCE_BY_CODE[_clean(row.get("source"))]
        identity = _clean(row.get("identity"))
        soup = _fetch_soup(
            _clean(row.get("raw_url")),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return (source.code, identity), _parse_detail(soup, row, source)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_detail, row): row for row in current_listed}
        for future in as_completed(futures):
            row = futures[future]
            key = (_clean(row.get("source")), _clean(row.get("identity")))
            try:
                parsed_key, parsed = future.result()
                if parsed_key in detailed:
                    raise MuanContractError("duplicate parsed detail identity")
                detailed[parsed_key] = parsed
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                detail_errors.append(f"detail {key[0]}:{key[1]}: {type(exc).__name__}: {_clean(exc)}")
    meta["detail_errors"] = len(detail_errors)
    errors.extend(detail_errors)
    details_complete = bool(
        not detail_errors and meta["detail_pages"] == len(current_listed) and len(detailed) == len(current_listed)
    )
    ordered = [
        detailed[(row["source"], row["identity"])]
        for row in current_listed
        if (row["source"], row["identity"]) in detailed
    ]
    semantics = Counter(
        (
            _normalized(row.get("title")),
            _normalized(row.get("branch")),
            _clean(row.get("period")),
            _normalized(row.get("venue_name")),
        )
        for row in ordered
    )
    semantic_duplicate_groups = sum(value > 1 for value in semantics.values())
    if semantic_duplicate_groups:
        errors.append(f"{semantic_duplicate_groups} current semantic duplicate groups")
    controls_complete = bool(
        details_complete
        and all(
            bool(row.get("reservation_available")) == bool(row.get("raw_fields", {}).get("application_control_present"))
            for row in ordered
        )
    )
    result: list[dict[str, Any]] = []
    if details_complete and controls_complete and not semantic_duplicate_groups and not errors:
        for row in ordered:
            errors.extend(_privacy_errors(row))
        if not errors:
            try:
                result = list((dedupe_rows or _dedupe_default)(ordered))
            except Exception as exc:
                errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
            if len(result) != len(ordered):
                errors.append(
                    f"dedupe changed official source-prefixed identity cardinality {len(ordered)} to {len(result)}"
                )
                result = []
    snapshot_complete = bool(
        list_complete and details_complete and controls_complete and not semantic_duplicate_groups and not errors
    )
    if not snapshot_complete:
        result = []
    meta.update(
        {
            "returned_count": len(result),
            "semantic_duplicate_group_count": semantic_duplicate_groups,
            "branch_counts": dict(Counter(row.get("branch") for row in result)),
            "current_branch_names": sorted({row.get("branch") for row in result}),
            "status_counts": dict(Counter(row.get("status") for row in result)),
            "visible_public_application_control_count": sum(bool(row.get("reservation_available")) for row in ordered),
            "details_complete": details_complete,
            "application_controls_complete": controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "no_current_data": bool(snapshot_complete and not current_listed),
            "no_current_reason": (
                "both complete official Muan course catalogues have no current/future rows"
                if snapshot_complete and not current_listed
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, MUAN_PARSER, meta


collect = collect_muan_education


__all__ = [
    "MUAN_ALIAS_URLS",
    "MUAN_CANDIDATE_AUDIT",
    "MUAN_CANDIDATE_ID",
    "MUAN_CANDIDATE_URL",
    "MUAN_DISCOVERY_AUDIT",
    "MUAN_EDUCATION_ROOT_URL",
    "MUAN_EXCLUDED_URLS",
    "MUAN_LIFELONG_URL",
    "MUAN_MUNICIPALITY_CODE",
    "MUAN_MUNICIPALITY_NAME",
    "MUAN_OTHER_URL",
    "MUAN_PARSER",
    "MUAN_PROVIDER",
    "MUAN_PROVIDER_NAME",
    "MUAN_SEPARATE_OWNER_URLS",
    "collect",
    "collect_muan_education",
    "is_muan_alias_target",
    "is_muan_education_target",
    "is_muan_excluded_target",
    "is_target",
    "muan_application_url",
    "muan_detail_url",
    "muan_list_url",
]
