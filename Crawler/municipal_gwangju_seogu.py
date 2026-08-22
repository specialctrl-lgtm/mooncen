"""Fail-closed collector for Gwangju Seo-gu's official education catalogue.

The official integrated-reservation education list is the executable owner.
It aggregates native Seo-gu reservations, Seo-gu's 365 lifelong-learning
service and the municipal job centre.  Landing pages and a separate cultural
centre article are discovery evidence, not additional owners of this list.

Every data page is derived from the declared total, the immediate empty page
is required, and the first and last data pages are rechecked.  Current/future
rows are detail-verified on their owning official service.  Public application
controls must be bound to the same source identity.  Inquiry values, instructor
data, free-form descriptions and applicant forms are deliberately never read
or persisted.
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


GWANGJU_SEOGU_PROVIDER = "MUNI_WWW_SEOGU_GWANGJU_KR_10B34AC9"
GWANGJU_SEOGU_CANONICAL_CANDIDATE_ID = "MUNI_IR_3F9E86C7C753"
GWANGJU_SEOGU_HEALTH_CANDIDATE_ID = "MUNI_IR_DF76F1936AF1"
GWANGJU_SEOGU_LANDING_CANDIDATE_ID = "MUNI_IR_4381711B5B74"
GWANGJU_SEOGU_CULTURE_CANDIDATE_ID = "MUNI_IR_A7DBABD1AA55"
GWANGJU_SEOGU_HEALTH_PROVIDER = "MUNI_WWW_SEOGU_GWANGJU_KR_2E45746E"
GWANGJU_SEOGU_LANDING_PROVIDER = "MUNI_WWW_SEOGU_GWANGJU_KR_08EC4C8E"
GWANGJU_SEOGU_CULTURE_PROVIDER = "MUNI_WWW_SEOMUNSEN_OR_KR_98C1F8C5"

GWANGJU_SEOGU_HOST = "www.seogu.gwangju.kr"
GWANGJU_SEOGU_365_HOST = "365edu.seogu.gwangju.kr"
GWANGJU_SEOGU_LIST_PATH = "/applySearchList.es"
GWANGJU_SEOGU_NATIVE_DETAIL_PATH = "/applyView.es"
GWANGJU_SEOGU_NATIVE_APPLICATION_PATH = "/applyMemForm.es"
GWANGJU_SEOGU_JOB_DETAIL_PATH = "/jobProgramView.es"
GWANGJU_SEOGU_JOB_APPLICATION_PATH = "/jobProgramMemForm.es"
GWANGJU_SEOGU_MID = "c40101000000"
GWANGJU_SEOGU_JOB_MID = "b70405000000"
GWANGJU_SEOGU_EDUCATION_DIVISION = "L"
GWANGJU_SEOGU_365_CONTENT_UID = "9be5df897dbb8ddc017de605496870d4"
GWANGJU_SEOGU_365_LOGIN_RETURN_UID = "9be5df897dbb8ddc017e8ecd8a222dfe"
GWANGJU_SEOGU_CANONICAL_URL = (
    "https://www.seogu.gwangju.kr/applySearchList.es?"
    "mid=c40101000000&srh_div=L&nPage=1"
)
GWANGJU_SEOGU_CANDIDATE_URL = (
    "https://www.seogu.gwangju.kr/applySearchList.es?"
    "mid=c40101000000&srh_div=L"
)
GWANGJU_SEOGU_LANDING_URL = "https://www.seogu.gwangju.kr/index.es?sid=c4"
GWANGJU_SEOGU_HEALTH_URL = "https://www.seogu.gwangju.kr/health/"
GWANGJU_SEOGU_CULTURE_URL = (
    "http:" "//www.seomunsen.or.kr/index.php/?sid=13&wbb=md:view;uid:215;"
)
GWANGJU_SEOGU_MUNICIPALITY_CODE = "1224000000"
GWANGJU_SEOGU_MUNICIPALITY_NAME = "전남광주통합특별시 서구"
GWANGJU_SEOGU_PAGE_SIZE = 10
GWANGJU_SEOGU_MAX_WORKERS = 4
GWANGJU_SEOGU_FETCH_ATTEMPTS = 3
GWANGJU_SEOGU_RETRY_BACKOFF_SECONDS = 0.2
GWANGJU_SEOGU_MAX_HTML_BYTES = 8_000_000
GWANGJU_SEOGU_LIST_TITLE = "교육신청 | 교육/강좌 : 서구통합예약서비스"
GWANGJU_SEOGU_365_TITLE = "서구 세큰대 평생학습관 > 강좌 정보"
GWANGJU_SEOGU_JOB_TITLE = "교육프로그램 | Job Cafe : 일자리센터"
GWANGJU_SEOGU_PARSER = (
    "gwangju_seogu_official_integrated_education_all_pages+empty_sentinel+"
    "stable_first_last+all_current_details+native_365edu_job_controls+"
    "native_waitlist_control+official_target_fee_fallback+"
    "closed_list_overrides_stale_365edu_detail+institution_subbranch+"
    "semantic_duplicate_zero+pii_allowlist"
)
GWANGJU_SEOGU_OWNERSHIP_SCOPE = (
    "gwangju_seogu_official_integrated_reservation_education_catalogue"
)

GWANGJU_SEOGU_INSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("", "교육기관 선택"),
    ("EACD001", "정보화교육"),
    ("EACD002", "하이에듀넷"),
    ("EACD004", "통합행정복지센터"),
    ("EACD006", "빛고을국악전수관"),
    ("EACD008", "정신건강복지센터"),
    ("EACD009", "문화관광"),
    ("EACD011", "일자리센터"),
    ("EACD012", "물품공유센터"),
    ("EACD013", "보건소"),
    ("EACD014", "물품공유센터"),
    ("EACD015", "사회적가치지원센터"),
    ("EACD024", "도서관"),
    ("EACD025", "평생학습관"),
    ("EACD028", "서구청"),
    ("EACD033", "서구청 전월세 주거안심매니저"),
    ("EACD060", "휠체어차량"),
)
GWANGJU_SEOGU_TARGETS: tuple[tuple[str, str], ...] = (
    ("", "교육대상 선택"),
    ("APTG001", "유아/어린이"),
    ("APTG002", "청소년"),
    ("APTG003", "성인"),
    ("APTG004", "어르신"),
    ("APTG005", "가족"),
    ("APTG006", "기타"),
)
GWANGJU_SEOGU_TUITIONS: tuple[tuple[str, str], ...] = (
    ("", "수강료 선택"),
    ("TUIT001", "무료"),
    ("TUIT002", "유료"),
    ("TUIT003", "재료비 별도"),
)
GWANGJU_SEOGU_FIELDS: tuple[tuple[str, str], ...] = (
    ("", "교육분야 선택"),
    ("FIEL001", "문화예술"),
    ("FIEL002", "건강"),
    ("FIEL003", "미디어/컴퓨터"),
    ("FIEL004", "가족/자녀"),
    ("FIEL005", "언어"),
    ("FIEL006", "인문교양"),
    ("FIEL007", "자연과학"),
    ("FIEL008", "취업/자격증"),
    ("FIEL009", "상담"),
    ("FIEL010", "기타"),
)
GWANGJU_SEOGU_SEARCH_STATUSES: tuple[tuple[str, str], ...] = (
    ("", "접수상태 선택"),
    ("Y", "신청가능"),
    ("A", "신청대기"),
    ("N", "신청마감"),
)

GWANGJU_SEOGU_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    GWANGJU_SEOGU_CANONICAL_CANDIDATE_ID: {
        "decision": "include_as_complete_official_integrated_education_owner",
        "provider": GWANGJU_SEOGU_PROVIDER,
        "url": GWANGJU_SEOGU_CANDIDATE_URL,
        "canonical_url": GWANGJU_SEOGU_CANONICAL_URL,
        "owner": GWANGJU_SEOGU_PROVIDER,
    },
    GWANGJU_SEOGU_LANDING_CANDIDATE_ID: {
        "decision": "official_partial_landing_superseded_by_complete_education_list",
        "provider": GWANGJU_SEOGU_LANDING_PROVIDER,
        "url": GWANGJU_SEOGU_LANDING_URL,
        "owner": GWANGJU_SEOGU_PROVIDER,
    },
    GWANGJU_SEOGU_HEALTH_CANDIDATE_ID: {
        "decision": "exclude_health_branch_homepage_without_complete_course_rows",
        "provider": GWANGJU_SEOGU_HEALTH_PROVIDER,
        "url": GWANGJU_SEOGU_HEALTH_URL,
        "owner": GWANGJU_SEOGU_PROVIDER,
    },
    GWANGJU_SEOGU_CULTURE_CANDIDATE_ID: {
        "decision": "exclude_separate_cultural_centre_editorial_url_not_integrated_owner_alias",
        "provider": GWANGJU_SEOGU_CULTURE_PROVIDER,
        "url": GWANGJU_SEOGU_CULTURE_URL,
        "owner": "separate_cultural_centre_service",
    },
}

GWANGJU_SEOGU_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": GWANGJU_SEOGU_CANONICAL_URL,
    "source_rows": 38,
    "data_pages": 4,
    "empty_sentinel_page": 5,
    "list_requests_with_rechecks": 7,
    "unique_identities": 38,
    "current_or_future_rows": 38,
    "detail_pages_verified": 38,
    "source_kind_counts": {"native": 20, "365edu": 17, "job": 1},
    "status_counts": {"OPEN": 27, "SCHEDULED": 10, "CLOSED": 1},
    "institution_counts": {
        "빛고을국악전수관": 6,
        "정보화교육": 2,
        "평생학습관": 17,
        "통합행정복지센터": 10,
        "서구청": 2,
        "일자리센터": 1,
    },
    "visible_public_application_controls": 28,
    "actionable_application_controls": 27,
    "semantic_duplicate_count": 0,
    "identity_duplicate_count": 0,
    "conclusion": "landing_and_branch_candidates_roll_up_to_complete_education_list",
}

GWANGJU_SEOGU_PII_FIELDS_DISCARDED = (
    "문의",
    "문의처",
    "문의전화",
    "강사 정보",
    "교육담당",
    "교육내용",
    "강의내용",
    "첨부파일",
    "성명",
    "생년월일",
    "주소",
    "휴대전화",
    "이메일",
    "applicant_form_values",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GwangjuSeoguContractError(ValueError):
    """Raised when an official source changes its verified contract."""


@dataclass(frozen=True)
class _ListPage:
    page: int
    total: int
    data_last: int
    displayed_page: int
    displayed_last: int
    rows: tuple[dict[str, Any], ...]


_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"^[1-9]\d*$")
_EAP_CODE_RE = re.compile(r"^(?:|C\d{2})$")
_OES_RE = re.compile(r"^OES_\d{16}$")
_OEC_RE = re.compile(r"^OEC_\d{16}$")
_DATE_PAIR_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_APPLY_PERIOD_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*"
    r"\((?P<start_time>\d{2}:\d{2})\)\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})\s*"
    r"\((?P<end_time>\d{2}:\d{2})\)$"
)
_NATIVE_ONCLICK_RE = re.compile(
    r"^goView2\('(?P<identity>[1-9]\d*)','c40101000000',"
    r"'(?P<eas_code>[^']*)','(?P<eap_code>[^']*)'\);\s*"
    r"return\s+false;$"
)
_NATIVE_APPLICATION_RE = re.compile(
    r"^mem_goForm\('(?P<identity>[1-9]\d*)',"
    r"'(?P<title>(?:\\.|[^'\\])*)','(?P<wait>[NY])',"
    r"'(?P<eap_code>(?:|C\d{2}))','Y'\);\s*return\s+false;$"
)
_JOB_APPLICATION_RE = re.compile(
    r"^goForm\('b70405000000','(?P<identity>[1-9]\d*)'\);\s*"
    r"return\s+false;$"
)
_SUMMARY_TOTAL_RE = re.compile(r"^(?P<total>[\d,]+)건$")
_LIST_FIELD_LABELS = (
    "교육장소",
    "교육일자",
    "교육시간",
    "접수기간",
    "등록일자",
)
_NATIVE_DETAIL_LABELS = (
    "상태",
    "교육명",
    "장소",
    "교육기간",
    "교육시간",
    "모집정원",
    "문의",
)
_JOB_DETAIL_LABELS = (
    "상태",
    "교육명",
    "교육장소",
    "교육분류",
    "교육기간",
    "교육시간",
    "모집정원",
    "문의",
)
_STATUS_CONTRACT: Mapping[
    str, tuple[str, tuple[str, ...], frozenset[str], str]
] = {
    "접수중": ("OPEN", ("state", "ing"), frozenset({"접수중"}), "접수중"),
    "접수대기": (
        "SCHEDULED",
        ("state", "wait"),
        frozenset({"접수예정"}),
        "접수예정",
    ),
    "정원마감": (
        "CLOSED",
        ("state", "end"),
        frozenset({"접수마감", "종료"}),
        "종료",
    ),
    "대기신청": (
        "WAITLIST",
        ("state", "book"),
        frozenset({"진행중"}),
        "대기",
    ),
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_kind",
        "list_page",
        "source_status",
        "source_institution",
        "source_subbranch",
        "source_period",
        "source_apply_period",
        "source_schedule",
        "source_venue",
        "source_eap_code",
        "source_institution_code",
        "source_detail_category",
        "detail_verified",
        "visible_application_control_present",
        "actionable_application_control_present",
        "application_control_contract",
        "source_target_omitted",
        "source_fee_omitted",
        "venue_fallback_used",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "manager",
        "manager_name",
        "contact",
        "phone",
        "email",
        "address",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


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


def is_gwangju_seogu_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != GWANGJU_SEOGU_PROVIDER:
        return False
    if _clean(_target_value(target, "url")) == GWANGJU_SEOGU_CANDIDATE_URL:
        return True
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GWANGJU_SEOGU_HOST
        and _safe_port(parsed) is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GWANGJU_SEOGU_LIST_PATH
        and parse_qs(parsed.query, keep_blank_values=True)
        == {
            "mid": [GWANGJU_SEOGU_MID],
            "srh_div": [GWANGJU_SEOGU_EDUCATION_DIVISION],
            "nPage": ["1"],
        }
        and not parsed.fragment
    )


is_target = is_gwangju_seogu_education_target


def is_gwangju_seogu_candidate_alias(target: Any) -> bool:
    candidate = _clean(_target_value(target, "candidate_id"))
    url = _clean(_target_value(target, "url"))
    return bool(
        candidate in GWANGJU_SEOGU_CANDIDATE_AUDIT
        or url
        in {
            GWANGJU_SEOGU_CANDIDATE_URL,
            GWANGJU_SEOGU_LANDING_URL,
            GWANGJU_SEOGU_HEALTH_URL,
            GWANGJU_SEOGU_CULTURE_URL,
        }
    )


def gwangju_seogu_list_url(page: int) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    query = urlencode(
        (
            ("mid", GWANGJU_SEOGU_MID),
            ("srh_div", GWANGJU_SEOGU_EDUCATION_DIVISION),
            ("nPage", str(page)),
        )
    )
    return f"https://{GWANGJU_SEOGU_HOST}{GWANGJU_SEOGU_LIST_PATH}?{query}"


def gwangju_seogu_native_detail_url(identity: str, eap_code: str = "") -> str:
    value = _clean(identity)
    code = _clean(eap_code)
    if _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("native identity must be a positive integer")
    if _EAP_CODE_RE.fullmatch(code) is None:
        raise ValueError("invalid native branch code")
    query = urlencode(
        (
            ("mid", GWANGJU_SEOGU_MID),
            ("eap_seq", value),
            ("eas_code", ""),
            ("search_yn", "Y"),
            ("eap_code", code),
        )
    )
    return f"https://{GWANGJU_SEOGU_HOST}{GWANGJU_SEOGU_NATIVE_DETAIL_PATH}?{query}"


def gwangju_seogu_native_application_url(
    identity: str,
    institution_code: str,
    eap_code: str = "",
    *,
    wait_yn: str = "N",
) -> str:
    value = _clean(identity)
    institution = _clean(institution_code)
    code = _clean(eap_code)
    wait = _clean(wait_yn)
    known_codes = {key for key, _label in GWANGJU_SEOGU_INSTITUTIONS if key}
    if _POSITIVE_ID_RE.fullmatch(value) is None or institution not in known_codes:
        raise ValueError("invalid native application identity/institution")
    if _EAP_CODE_RE.fullmatch(code) is None or wait not in {"N", "Y"}:
        raise ValueError("invalid native application state")
    query = urlencode(
        (
            ("mid", GWANGJU_SEOGU_MID),
            ("eap_seq", value),
            ("eas_code", institution),
            ("wait_yn", wait),
            ("eap_code", code),
            ("search_yn", "Y"),
        )
    )
    return f"https://{GWANGJU_SEOGU_HOST}{GWANGJU_SEOGU_NATIVE_APPLICATION_PATH}?{query}"


def gwangju_seogu_job_detail_url(identity: str) -> str:
    value = _clean(identity)
    if _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("job identity must be a positive integer")
    query = urlencode((("mid", GWANGJU_SEOGU_JOB_MID), ("jp_seq", value)))
    return f"https://{GWANGJU_SEOGU_HOST}{GWANGJU_SEOGU_JOB_DETAIL_PATH}?{query}"


def gwangju_seogu_job_application_url(identity: str) -> str:
    value = _clean(identity)
    if _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("job identity must be a positive integer")
    query = urlencode((("mid", GWANGJU_SEOGU_JOB_MID), ("jp_seq", value)))
    return f"https://{GWANGJU_SEOGU_HOST}{GWANGJU_SEOGU_JOB_APPLICATION_PATH}?{query}"


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": GWANGJU_SEOGU_LANDING_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    status = int(getattr(response, "status_code", 0))
    if status != 200:
        raise GwangjuSeoguContractError(f"unexpected HTTP status {status}")
    if getattr(response, "headers", {}).get("Location"):
        raise GwangjuSeoguContractError("redirect response is not accepted")
    content = getattr(response, "content", b"")
    if not content:
        raise GwangjuSeoguContractError("empty HTTP response")
    if len(content) > GWANGJU_SEOGU_MAX_HTML_BYTES:
        raise GwangjuSeoguContractError("HTTP response exceeded HTML byte cap")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > GWANGJU_SEOGU_MAX_HTML_BYTES:
            raise GwangjuSeoguContractError("HTML fixture exceeded byte cap")
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > GWANGJU_SEOGU_MAX_HTML_BYTES:
            raise GwangjuSeoguContractError("HTML fixture exceeded byte cap")
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher returned neither HTML nor response")
    if len(content) > GWANGJU_SEOGU_MAX_HTML_BYTES:
        raise GwangjuSeoguContractError("HTTP response exceeded HTML byte cap")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _fetch_soup(
    url: str,
    *,
    timeout: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> BeautifulSoup:
    last_error: Optional[Exception] = None
    for attempt in range(GWANGJU_SEOGU_FETCH_ATTEMPTS):
        session: Any = None
        try:
            session = session_factory()
            return _coerce_soup(fetcher(session, url, timeout))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < GWANGJU_SEOGU_FETCH_ATTEMPTS:
                time.sleep(GWANGJU_SEOGU_RETRY_BACKOFF_SECONDS * (attempt + 1))
        finally:
            _close_quietly(session)
    assert last_error is not None
    raise last_error


def _document_title(soup: BeautifulSoup, expected: str, label: str) -> None:
    titles = soup.select("head > title")
    if len(titles) != 1 or _clean(titles[0].get_text(" ", strip=True)) != expected:
        raise GwangjuSeoguContractError(f"{label}: official page title changed")


def _single_input_value(form: Any, name: str, *, input_type: str = "hidden") -> str:
    nodes = form.select(f'input[type="{input_type}"][name="{name}"]')
    if len(nodes) != 1:
        raise GwangjuSeoguContractError(f"form field {name} changed")
    return _clean(nodes[0].get("value"))


def _select_options(form: Any, name: str) -> tuple[tuple[str, str], ...]:
    nodes = form.select(f'select[name="{name}"]')
    if len(nodes) != 1:
        raise GwangjuSeoguContractError(f"search taxonomy {name} changed")
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in nodes[0].select(":scope > option")
    )


def _validate_search_form(soup: BeautifulSoup, page: int) -> None:
    forms = soup.select("form#srhForm")
    if len(forms) != 1:
        raise GwangjuSeoguContractError(f"page {page}: search form changed")
    form = forms[0]
    action = urlparse(urljoin(GWANGJU_SEOGU_CANONICAL_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "post"
        or action.scheme != "https"
        or action.hostname != GWANGJU_SEOGU_HOST
        or action.path != GWANGJU_SEOGU_LIST_PATH
        or parse_qs(action.query, keep_blank_values=True)
        != {"mid": [GWANGJU_SEOGU_MID]}
        or action.fragment
    ):
        raise GwangjuSeoguContractError(f"page {page}: search form ownership changed")
    expected = {
        "mid": GWANGJU_SEOGU_MID,
        "nPage": str(page),
        "eap_seq": "",
        "srh_div": GWANGJU_SEOGU_EDUCATION_DIVISION,
        "srh_inte_code": "",
    }
    for name, value in expected.items():
        if _single_input_value(form, name) != value:
            raise GwangjuSeoguContractError(f"page {page}: search field {name} changed")
    csrf = form.select('input[type="hidden"][name="_csrf"]')
    if len(csrf) != 1 or not _clean(csrf[0].get("value")):
        raise GwangjuSeoguContractError(f"page {page}: CSRF field changed")
    for name in ("srh_sdate", "srh_edate", "keyWord"):
        if _single_input_value(form, name, input_type="text") != "":
            raise GwangjuSeoguContractError(f"page {page}: search input {name} changed")
    contracts = (
        ("srh_eas_code", GWANGJU_SEOGU_INSTITUTIONS, "institution"),
        ("srh_target", GWANGJU_SEOGU_TARGETS, "target"),
        ("srh_tuition", GWANGJU_SEOGU_TUITIONS, "tuition"),
        ("srh_field", GWANGJU_SEOGU_FIELDS, "education-field"),
        ("srh_state", GWANGJU_SEOGU_SEARCH_STATUSES, "reception-status"),
    )
    for name, options, label in contracts:
        if _select_options(form, name) != options:
            raise GwangjuSeoguContractError(f"{label} taxonomy changed")


def _parse_summary(soup: BeautifulSoup, page: int) -> tuple[int, int, int, int]:
    nodes = soup.select("p.page")
    if len(nodes) != 1:
        raise GwangjuSeoguContractError(f"page {page}: page summary changed")
    total_nodes = nodes[0].select(":scope > span.total > b")
    current_nodes = nodes[0].select(":scope > span.current")
    if len(total_nodes) != 1 or len(current_nodes) != 1:
        raise GwangjuSeoguContractError(f"page {page}: page summary layout changed")
    match = _SUMMARY_TOTAL_RE.fullmatch(_clean(total_nodes[0].get_text(" ", strip=True)))
    strong = current_nodes[0].select(":scope > strong")
    last_nodes = current_nodes[0].select(":scope > b")
    if match is None or len(strong) != 1 or len(last_nodes) != 1:
        raise GwangjuSeoguContractError(f"page {page}: page summary values changed")
    total = int(match.group("total").replace(",", ""))
    displayed_page = int(_clean(strong[0].get_text(" ", strip=True)))
    displayed_last = int(_clean(last_nodes[0].get_text(" ", strip=True)))
    expected_last = math.ceil(total / GWANGJU_SEOGU_PAGE_SIZE)
    data_last = max(1, expected_last)
    if displayed_page != page or displayed_last != expected_last:
        raise GwangjuSeoguContractError(f"page {page}: catalogue boundary changed")
    return total, data_last, displayed_page, displayed_last


def _parse_date_pair(value: Any, identity: str) -> tuple[date, date]:
    match = _DATE_PAIR_RE.fullmatch(_clean(value))
    if match is None:
        raise GwangjuSeoguContractError(f"course {identity}: education period changed")
    start = date.fromisoformat(match.group("start"))
    end = date.fromisoformat(match.group("end"))
    if start > end:
        raise GwangjuSeoguContractError(f"course {identity}: reversed education period")
    return start, end


def _parse_apply_period(value: Any, identity: str) -> str:
    text = _clean(value)
    match = _APPLY_PERIOD_RE.fullmatch(text)
    if match is None:
        raise GwangjuSeoguContractError(f"course {identity}: reception period changed")
    start = datetime.fromisoformat(f"{match.group('start')}T{match.group('start_time')}")
    end = datetime.fromisoformat(f"{match.group('end')}T{match.group('end_time')}")
    if start > end:
        raise GwangjuSeoguContractError(f"course {identity}: reversed reception period")
    return text


def _validate_native_anchor(anchor: Any) -> tuple[str, str, str]:
    parsed = urlparse(urljoin(GWANGJU_SEOGU_CANONICAL_URL, _clean(anchor.get("href"))))
    if (
        parsed.scheme != "https"
        or parsed.hostname != GWANGJU_SEOGU_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/applyList.es"
        or parse_qs(parsed.query, keep_blank_values=True)
        != {"mid": [GWANGJU_SEOGU_MID]}
        or parsed.fragment
    ):
        raise GwangjuSeoguContractError("native list anchor changed")
    match = _NATIVE_ONCLICK_RE.fullmatch(_clean(anchor.get("onclick")))
    if match is None or match.group("eas_code") != "":
        raise GwangjuSeoguContractError("native list handler changed")
    code = match.group("eap_code")
    if _EAP_CODE_RE.fullmatch(code) is None:
        raise GwangjuSeoguContractError("native list branch code changed")
    return match.group("identity"), code, gwangju_seogu_native_detail_url(
        match.group("identity"), code
    )


def _parse_365_anchor(anchor: Any, source_status: str) -> tuple[str, str]:
    if _clean(anchor.get("onclick")):
        raise GwangjuSeoguContractError("365edu direct anchor gained a handler")
    parsed = urlparse(_clean(anchor.get("href")))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != GWANGJU_SEOGU_365_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/365edu/index.9is"
        or set(query)
        != {"contentUid", "oesSubjectId", "oecId", "isAccept", "isAddAccept"}
        or query.get("contentUid") != [GWANGJU_SEOGU_365_CONTENT_UID]
        or len(query.get("oesSubjectId", [])) != 1
        or len(query.get("oecId", [])) != 1
        or _OES_RE.fullmatch(query["oesSubjectId"][0]) is None
        or _OEC_RE.fullmatch(query["oecId"][0]) is None
        or query.get("isAddAccept") != ["N"]
        or parsed.fragment
    ):
        raise GwangjuSeoguContractError("365edu course link changed")
    expected_accept = "R" if source_status == "접수대기" else "P"
    if query.get("isAccept") != [expected_accept]:
        raise GwangjuSeoguContractError("365edu link/status mismatch")
    canonical_query = urlencode(
        (
            ("contentUid", GWANGJU_SEOGU_365_CONTENT_UID),
            ("oesSubjectId", query["oesSubjectId"][0]),
            ("oecId", query["oecId"][0]),
            ("isAccept", expected_accept),
            ("isAddAccept", "N"),
        )
    )
    url = f"https://{GWANGJU_SEOGU_365_HOST}/365edu/index.9is?{canonical_query}"
    identity = f"365edu:{query['oesSubjectId'][0]}:{query['oecId'][0]}"
    return identity, url


def _parse_job_anchor(anchor: Any, source_status: str) -> tuple[str, str]:
    if _clean(anchor.get("onclick")) or source_status != "접수중":
        raise GwangjuSeoguContractError("job-centre list control changed")
    parsed = urlparse(urljoin(GWANGJU_SEOGU_CANONICAL_URL, _clean(anchor.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identities = query.get("jp_seq", [])
    if (
        parsed.scheme != "https"
        or parsed.hostname != GWANGJU_SEOGU_HOST
        or _safe_port(parsed) is not None
        or parsed.path != GWANGJU_SEOGU_JOB_DETAIL_PATH
        or set(query) != {"mid", "jp_seq"}
        or query.get("mid") != [GWANGJU_SEOGU_JOB_MID]
        or len(identities) != 1
        or _POSITIVE_ID_RE.fullmatch(identities[0]) is None
        or parsed.fragment
    ):
        raise GwangjuSeoguContractError("job-centre course link changed")
    return f"job:{identities[0]}", gwangju_seogu_job_detail_url(identities[0])


def _parse_list_card(card: Any, page: int) -> dict[str, Any]:
    anchors = card.select(":scope > a[href]")
    if len(anchors) != 1:
        raise GwangjuSeoguContractError(f"page {page}: course anchor changed")
    anchor = anchors[0]
    titles = anchor.select(":scope > div.txt > div.tt > strong")
    types = anchor.select(":scope > div.txt > div.tt > div.type")
    if len(titles) != 1 or len(types) != 1:
        raise GwangjuSeoguContractError(f"page {page}: course title layout changed")
    title = _clean(titles[0].get_text(" ", strip=True))
    if not title:
        raise GwangjuSeoguContractError(f"page {page}: course title is empty")
    markers = types[0].select(":scope > span.state")
    if len(markers) not in {2, 3}:
        raise GwangjuSeoguContractError(f"page {page}: course taxonomy markers changed")
    source_status = _clean(markers[0].get_text(" ", strip=True))
    contract = _STATUS_CONTRACT.get(source_status)
    if (
        contract is None
        or tuple(markers[0].get("class") or ()) != contract[1]
        or _clean(markers[0].get("data-label")) not in contract[2]
    ):
        raise GwangjuSeoguContractError(f"page {page}: reception marker changed")
    for marker in markers[1:]:
        if tuple(marker.get("class") or ()) != ("state", "cate"):
            raise GwangjuSeoguContractError(f"page {page}: institution marker changed")
    institution = _clean(markers[1].get_text(" ", strip=True))
    known_institutions = {label for code, label in GWANGJU_SEOGU_INSTITUTIONS if code}
    if institution not in known_institutions:
        raise GwangjuSeoguContractError(f"page {page}: unknown institution")
    subbranch = _clean(markers[2].get_text(" ", strip=True)) if len(markers) == 3 else ""
    if len(subbranch) > 80 or _PHONE_RE.search(subbranch) or _EMAIL_RE.search(subbranch):
        raise GwangjuSeoguContractError(f"page {page}: unsafe subbranch value")

    field_rows = anchor.select(":scope > div.txt > ul.con > li")
    if len(field_rows) != len(_LIST_FIELD_LABELS):
        raise GwangjuSeoguContractError(f"page {page}: list field count changed")
    fields: dict[str, Any] = {}
    for expected_label, row in zip(_LIST_FIELD_LABELS, field_rows):
        labels = row.select(":scope > strong")
        values = row.select(":scope > span")
        if (
            len(labels) != 1
            or len(values) != 1
            or _clean(labels[0].get_text(" ", strip=True)) != expected_label
        ):
            raise GwangjuSeoguContractError(f"page {page}: list field layout changed")
        fields[expected_label] = values[0]
    source_period = _clean(fields["교육일자"].get_text(" ", strip=True))
    source_venue = _clean(fields["교육장소"].get_text(" ", strip=True))
    source_schedule = _clean(fields["교육시간"].get_text(" ", strip=True))
    source_apply_period = _clean(fields["접수기간"].get_text(" ", strip=True))

    parsed_href = urlparse(urljoin(GWANGJU_SEOGU_CANONICAL_URL, _clean(anchor.get("href"))))
    if _clean(anchor.get("onclick")):
        native_identity, eap_code, raw_url = _validate_native_anchor(anchor)
        identity = f"native:{native_identity}"
        source_kind = "native"
    elif parsed_href.hostname == GWANGJU_SEOGU_365_HOST:
        identity, raw_url = _parse_365_anchor(anchor, source_status)
        native_identity = ""
        eap_code = ""
        source_kind = "365edu"
        if institution != "평생학습관" or subbranch:
            raise GwangjuSeoguContractError("365edu institution contract changed")
    elif parsed_href.path == GWANGJU_SEOGU_JOB_DETAIL_PATH:
        identity, raw_url = _parse_job_anchor(anchor, source_status)
        native_identity = identity.split(":", 1)[1]
        eap_code = ""
        source_kind = "job"
        if institution != "일자리센터" or subbranch:
            raise GwangjuSeoguContractError("job-centre institution contract changed")
    else:
        raise GwangjuSeoguContractError(f"page {page}: unknown official source type")

    start, end = _parse_date_pair(source_period, identity)
    _parse_apply_period(source_apply_period, identity)
    return {
        "identity": identity,
        "native_identity": native_identity,
        "source_kind": source_kind,
        "title": title,
        "source_status": source_status,
        "status": contract[0],
        "institution": institution,
        "subbranch": subbranch,
        "source_period": source_period,
        "start": start,
        "end": end,
        "venue": source_venue,
        "schedule": source_schedule,
        "apply_period": source_apply_period,
        "eap_code": eap_code,
        "raw_url": raw_url,
        "list_page": page,
    }


def _parse_list_page(soup: BeautifulSoup, page: int) -> _ListPage:
    _document_title(soup, GWANGJU_SEOGU_LIST_TITLE, f"page {page}")
    _validate_search_form(soup, page)
    total, data_last, displayed_page, displayed_last = _parse_summary(soup, page)
    roots = soup.select("div.apply_list.webzine")
    if len(roots) != 1:
        raise GwangjuSeoguContractError(f"page {page}: course list changed")
    lists = roots[0].select(":scope > ul.gallery_list")
    if len(lists) != 1:
        raise GwangjuSeoguContractError(f"page {page}: course list body changed")
    items = lists[0].find_all("li", recursive=False)
    nodata = [item for item in items if "nodata" in (item.get("class") or ())]
    if nodata:
        if (
            len(items) != 1
            or len(nodata) != 1
            or list(nodata[0].children) == []
            or _clean(nodata[0].get_text(" ", strip=True)) != "해당 내용이 없습니다."
            or nodata[0].find(True) is not None
        ):
            raise GwangjuSeoguContractError(f"page {page}: empty marker changed")
        rows: tuple[dict[str, Any], ...] = ()
    else:
        rows = tuple(_parse_list_card(item, page) for item in items)
    return _ListPage(page, total, data_last, displayed_page, displayed_last, rows)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("source_status")),
            _clean(row.get("institution")),
            _clean(row.get("subbranch")),
            _clean(row.get("source_period")),
            _clean(row.get("apply_period")),
            _clean(row.get("venue")),
            _clean(row.get("raw_url")),
        )
        for row in rows
    )


def _structured_table(
    table: Any,
    expected_labels: tuple[str, ...],
    identity: str,
) -> dict[str, Any]:
    tbodies = table.find_all("tbody", recursive=False)
    if len(tbodies) != 1:
        raise GwangjuSeoguContractError(f"course {identity}: detail body changed")
    rows = tbodies[0].find_all("tr", recursive=False)
    if len(rows) != len(expected_labels):
        raise GwangjuSeoguContractError(f"course {identity}: detail row count changed")
    fields: dict[str, Any] = {}
    for expected, row in zip(expected_labels, rows):
        headers = row.find_all("th", recursive=False)
        values = row.find_all("td", recursive=False)
        children = [child for child in row.children if getattr(child, "name", None)]
        if (
            len(headers) != 1
            or len(values) != 1
            or children != [headers[0], values[0]]
            or _clean(headers[0].get_text(" ", strip=True)) != expected
        ):
            raise GwangjuSeoguContractError(f"course {identity}: detail schema changed")
        fields[expected] = values[0]
    return fields


def _safe_field(fields: Mapping[str, Any], name: str, identity: str) -> str:
    if name in {"문의", "문의처", "문의전화"} or name not in fields:
        raise GwangjuSeoguContractError(f"course {identity}: unsafe detail access")
    return _clean(fields[name].get_text(" ", strip=True))


def _capacity(value: str, identity: str) -> tuple[str, int]:
    match = re.fullmatch(r"(?P<count>[\d,]+)\s*명", _clean(value))
    if match is None:
        raise GwangjuSeoguContractError(f"course {identity}: capacity changed")
    count = int(match.group("count").replace(",", ""))
    return f"{count}명", count


def _validate_navigation_form(
    soup: BeautifulSoup,
    *,
    action_path: str,
    expected: Mapping[str, str],
    identity: str,
) -> None:
    forms = soup.select("form#srhForm")
    if len(forms) != 1:
        raise GwangjuSeoguContractError(f"course {identity}: navigation form changed")
    form = forms[0]
    action = urlparse(urljoin(GWANGJU_SEOGU_CANONICAL_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "post"
        or action.scheme != "https"
        or action.hostname != GWANGJU_SEOGU_HOST
        or action.path != action_path
        or action.query
        or action.fragment
    ):
        raise GwangjuSeoguContractError(f"course {identity}: navigation ownership changed")
    for name, value in expected.items():
        if _single_input_value(form, name) != value:
            raise GwangjuSeoguContractError(
                f"course {identity}: navigation field {name} changed"
            )


def _parse_native_detail(soup: BeautifulSoup, listed: Mapping[str, Any]) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    native_identity = _clean(listed.get("native_identity"))
    _document_title(soup, GWANGJU_SEOGU_LIST_TITLE, f"course {identity}")
    boards = soup.select("div.board_view.webzine")
    if len(boards) != 1 or len(boards[0].select(":scope > div.con")) != 1:
        raise GwangjuSeoguContractError(f"course {identity}: detail/free-form layout changed")
    tables = boards[0].select(":scope > div.head > div.txt > table")
    if len(tables) != 1:
        raise GwangjuSeoguContractError(f"course {identity}: detail table changed")
    expected_caption = (
        f"{_clean(listed.get('title'))} 프로그램을 상태, 교육명, 장소, 교육기간, "
        "교육시간, 모집정원, 문의를 구분한 표입니다."
    )
    captions = tables[0].find_all("caption", recursive=False)
    if len(captions) != 1 or _clean(captions[0].get_text(" ", strip=True)) != expected_caption:
        raise GwangjuSeoguContractError(f"course {identity}: detail caption changed")
    fields = _structured_table(tables[0], _NATIVE_DETAIL_LABELS, identity)
    status_cell = fields["상태"]
    markers = status_cell.select(":scope > span.state")
    source_status = _clean(listed.get("source_status"))
    contract = _STATUS_CONTRACT[source_status]
    if (
        len(markers) != 1
        or _clean(markers[0].get_text(" ", strip=True)) != source_status
        or tuple(markers[0].get("class") or ()) != contract[1]
        or _clean(markers[0].get("data-label")) != contract[3]
    ):
        raise GwangjuSeoguContractError(f"course {identity}: list/detail status mismatch")
    title = _safe_field(fields, "교육명", identity)
    venue = _safe_field(fields, "장소", identity)
    period = _safe_field(fields, "교육기간", identity)
    schedule = _safe_field(fields, "교육시간", identity)
    if (
        title != _clean(listed.get("title"))
        or _normalized(venue) != _normalized(listed.get("venue"))
        or period != _clean(listed.get("source_period"))
        or _normalized(schedule) != _normalized(listed.get("schedule"))
    ):
        raise GwangjuSeoguContractError(f"course {identity}: list/detail fields mismatch")
    capacity_text, capacity_total = _capacity(
        _safe_field(fields, "모집정원", identity), identity
    )
    institution_labels: dict[str, set[str]] = {}
    for code, label in GWANGJU_SEOGU_INSTITUTIONS:
        if code:
            institution_labels.setdefault(label, set()).add(code)
    institution = _clean(listed.get("institution"))
    _validate_navigation_form(
        soup,
        action_path=GWANGJU_SEOGU_NATIVE_DETAIL_PATH,
        expected={
            "mid": GWANGJU_SEOGU_MID,
            "eap_seq": native_identity,
            "eas_code": next(iter(institution_labels[institution]))
            if len(institution_labels[institution]) == 1
            else _single_input_value(soup.select_one("form#srhForm"), "eas_code"),
            "keyField": "",
            "keyWord": "",
            "nPage": "",
            "eap_code": _clean(listed.get("eap_code")),
        },
        identity=identity,
    )
    form = soup.select_one("form#srhForm")
    assert form is not None
    institution_code = _single_input_value(form, "eas_code")
    if institution_code not in institution_labels[institution]:
        raise GwangjuSeoguContractError(f"course {identity}: institution code mismatch")

    areas = soup.select("p.board_btns")
    if len(areas) != 1:
        raise GwangjuSeoguContractError(f"course {identity}: button area changed")
    application_buttons = [
        node
        for node in areas[0].select(':scope > button[type="button"]')
        if _clean(node.get_text(" ", strip=True)) in {"신청", "대기신청"}
    ]
    application_url = ""
    control = False
    normalized_status = _clean(listed.get("status"))
    if normalized_status in {"OPEN", "WAITLIST"}:
        expected_label = "대기신청" if normalized_status == "WAITLIST" else "신청"
        if len(application_buttons) != 1 or tuple(
            application_buttons[0].get("class") or ()
        ) != ("btn", "type2") or _clean(
            application_buttons[0].get_text(" ", strip=True)
        ) != expected_label:
            raise GwangjuSeoguContractError(
                f"course {identity}: active course has no unique application control"
            )
        match = _NATIVE_APPLICATION_RE.fullmatch(
            _clean(application_buttons[0].get("onclick"))
        )
        if (
            match is None
            or match.group("identity") != native_identity
            or match.group("eap_code") != _clean(listed.get("eap_code"))
            or match.group("wait")
            != ("Y" if normalized_status == "WAITLIST" else "N")
        ):
            raise GwangjuSeoguContractError(
                f"course {identity}: application control identity mismatch"
            )
        application_url = gwangju_seogu_native_application_url(
            native_identity,
            institution_code,
            _clean(listed.get("eap_code")),
            wait_yn=match.group("wait"),
        )
        control = True
    elif application_buttons:
        raise GwangjuSeoguContractError(
            f"course {identity}: inactive course exposes an application control"
        )
    return {
        "application_url": application_url,
        "control": control,
        "capacity": capacity_text,
        "capacity_total": capacity_total,
        "institution_code": institution_code,
        "detail_category": "",
        "contract": "native_course_bound_mem_go_form" if control else "verified_no_control",
    }


def _parse_365_detail(soup: BeautifulSoup, listed: Mapping[str, Any]) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    _document_title(soup, GWANGJU_SEOGU_365_TITLE, f"course {identity}")
    headings = soup.select("table.board_view2 td.view_title.textind > h4")
    if len(headings) != 1:
        raise GwangjuSeoguContractError(f"course {identity}: 365edu title changed")
    markers = headings[0].select(":scope > span.mask")
    if len(markers) != 1:
        raise GwangjuSeoguContractError(f"course {identity}: 365edu status changed")
    status = _clean(listed.get("status"))
    expected_by_status = {
        "OPEN": ("신청가능", ("mask", "ms03")),
        "SCHEDULED": ("모집예정", ("mask", "ms04")),
        # The 365edu detail template remains generically open after the
        # integrated list has calculated that capacity is full.  The list's
        # explicit 정원마감 state is authoritative; the stale detail control
        # is identity-checked below but is never exposed as actionable.
        "CLOSED": ("신청가능", ("mask", "ms03")),
    }
    expected = expected_by_status.get(status)
    if expected is None:
        raise GwangjuSeoguContractError(
            f"course {identity}: unsupported 365edu status"
        )
    if (
        _clean(markers[0].get_text(" ", strip=True)) != expected[0]
        or tuple(markers[0].get("class") or ()) != expected[1]
    ):
        raise GwangjuSeoguContractError(f"course {identity}: 365edu list/detail status mismatch")
    clone = BeautifulSoup(str(headings[0]), "lxml").select_one("h4")
    if clone is None:
        raise GwangjuSeoguContractError(f"course {identity}: 365edu title clone failed")
    for marker in clone.select(":scope > span.mask"):
        marker.decompose()
    if _clean(clone.get_text(" ", strip=True)) != _clean(listed.get("title")):
        raise GwangjuSeoguContractError(f"course {identity}: 365edu title mismatch")
    title_table = headings[0].find_parent("table")
    if title_table is None:
        raise GwangjuSeoguContractError(f"course {identity}: 365edu title table changed")
    title_rows = title_table.select(":scope > tbody > tr")
    if (
        len(title_rows) != 2
        or len(title_rows[1].select(":scope > td.data_cont")) != 1
        or _clean(title_rows[1].select_one("td.data_cont > h4").get_text(" ", strip=True))
        != "강의내용"
    ):
        raise GwangjuSeoguContractError(f"course {identity}: 365edu free-form layout changed")
    # The data_cont cell is intentionally never read.
    controls = [
        node
        for node in soup.select("div.tc > a.btns")
        if _clean(node.get_text(" ", strip=True)) == "신청"
    ]
    application_url = ""
    control = False
    visible_control = bool(controls)
    if status in {"OPEN", "CLOSED"}:
        if (
            len(controls) != 1
            or tuple(controls[0].get("class") or ()) != ("btns", "theme")
            or _clean(controls[0].get("href")) != "javascript:void(0)"
        ):
            raise GwangjuSeoguContractError(
                f"course {identity}: 365edu open control changed"
            )
        onclick = _clean(controls[0].get("onclick"))
        match = re.search(r"loginReturnUrl\('(?P<query>[^']+)'\)$", onclick)
        if match is None:
            raise GwangjuSeoguContractError(
                f"course {identity}: 365edu application handler changed"
            )
        query = parse_qs(match.group("query"), keep_blank_values=True)
        identity_parts = identity.split(":")
        if query != {
            "contentUid": [GWANGJU_SEOGU_365_LOGIN_RETURN_UID],
            "oesSubjectId": [identity_parts[1]],
            "oecId": [identity_parts[2]],
            "isAccept": ["P"],
            "isAddAccept": ["N"],
        }:
            raise GwangjuSeoguContractError(
                f"course {identity}: 365edu application identity mismatch"
            )
        if status == "OPEN":
            application_url = _clean(listed.get("raw_url"))
            control = True
    elif controls:
        raise GwangjuSeoguContractError(
            f"course {identity}: scheduled 365edu course exposes an application control"
        )
    return {
        "application_url": application_url,
        "control": control,
        "visible_control": visible_control,
        "capacity": "",
        "capacity_total": None,
        "institution_code": "EACD025",
        "detail_category": "",
        "contract": (
            "365edu_login_bound_course_control"
            if control
            else (
                "closed_list_overrides_identity_bound_stale_365edu_control"
                if status == "CLOSED"
                else "verified_no_control"
            )
        ),
    }


def _parse_job_detail(soup: BeautifulSoup, listed: Mapping[str, Any]) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    native_identity = _clean(listed.get("native_identity"))
    _document_title(soup, GWANGJU_SEOGU_JOB_TITLE, f"course {identity}")
    boards = soup.select("div.board_view.jobProgram")
    if len(boards) != 1 or len(boards[0].select(":scope > div.con")) != 1:
        raise GwangjuSeoguContractError(f"course {identity}: job detail layout changed")
    tables = boards[0].select(":scope > div.head > div.txt > table")
    if len(tables) != 1:
        raise GwangjuSeoguContractError(f"course {identity}: job detail table changed")
    fields = _structured_table(tables[0], _JOB_DETAIL_LABELS, identity)
    status_markers = fields["상태"].select(":scope > span.state")
    if (
        _clean(listed.get("status")) != "OPEN"
        or len(status_markers) != 1
        or _clean(status_markers[0].get_text(" ", strip=True)) != "신청가능"
        or tuple(status_markers[0].get("class") or ()) != ("state", "ing")
        or _clean(status_markers[0].get("data-label")) != "접수"
    ):
        raise GwangjuSeoguContractError(f"course {identity}: job status mismatch")
    title = _safe_field(fields, "교육명", identity)
    venue = _safe_field(fields, "교육장소", identity)
    period = _safe_field(fields, "교육기간", identity)
    schedule = _safe_field(fields, "교육시간", identity)
    if (
        title != _clean(listed.get("title"))
        or _normalized(venue) != _normalized(listed.get("venue"))
        or period != _clean(listed.get("source_period"))
        or not _normalized(schedule).endswith(_normalized(listed.get("schedule")))
    ):
        raise GwangjuSeoguContractError(f"course {identity}: job list/detail mismatch")
    category = _safe_field(fields, "교육분류", identity)
    capacity_text, capacity_total = _capacity(
        _safe_field(fields, "모집정원", identity), identity
    )
    _validate_navigation_form(
        soup,
        action_path=GWANGJU_SEOGU_JOB_DETAIL_PATH,
        expected={
            "mid": GWANGJU_SEOGU_JOB_MID,
            "jp_seq": native_identity,
            "keyField": "",
            "keyWord": "",
            "nPage": "",
        },
        identity=identity,
    )
    buttons = [
        node
        for node in soup.select('div.board_btns > button[type="button"]')
        if _clean(node.get_text(" ", strip=True)) == "신청"
    ]
    if len(buttons) != 1 or tuple(buttons[0].get("class") or ()) != ("btn", "type2"):
        raise GwangjuSeoguContractError(f"course {identity}: job application control changed")
    match = _JOB_APPLICATION_RE.fullmatch(_clean(buttons[0].get("onclick")))
    if match is None or match.group("identity") != native_identity:
        raise GwangjuSeoguContractError(f"course {identity}: job application identity mismatch")
    return {
        "application_url": gwangju_seogu_job_application_url(native_identity),
        "control": True,
        "capacity": capacity_text,
        "capacity_total": capacity_total,
        "institution_code": "EACD011",
        "detail_category": category,
        "contract": "job_course_bound_go_form",
    }


def _branch_name(institution: str, subbranch: str) -> str:
    parts = [GWANGJU_SEOGU_MUNICIPALITY_NAME, institution]
    if subbranch:
        parts.append(subbranch)
    return " / ".join(parts)


def _branch_code(institution: str, subbranch: str) -> str:
    digest = hashlib.sha1(f"{institution}\0{subbranch}".encode("utf-8")).hexdigest()[:12]
    return f"gwangju-seogu:{digest}"


def _build_row(listed: Mapping[str, Any], detail: Mapping[str, Any]) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    institution = _clean(listed.get("institution"))
    subbranch = _clean(listed.get("subbranch"))
    control = bool(detail.get("control"))
    visible_control = bool(detail.get("visible_control", control))
    status = _clean(listed.get("status"))
    application_type = (
        "WAITLIST_APPLY"
        if control and status == "WAITLIST"
        else "ONLINE_RESERVATION"
        if control
        else "INFO_ONLY"
    )
    branch = _branch_name(institution, subbranch)
    source_venue = _clean(listed.get("venue"))
    venue = source_venue or branch
    return {
        "provider": GWANGJU_SEOGU_PROVIDER,
        "provider_course_id": f"{GWANGJU_SEOGU_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(listed.get("title")),
        "description": _clean(listed.get("title")),
        "branch": branch,
        "branch_code": _branch_code(institution, subbranch),
        "preserve_branch": True,
        "category": _clean(detail.get("detail_category")) or institution,
        "program_type": "교육",
        "raw_url": _clean(listed.get("raw_url")),
        "application_url": _clean(detail.get("application_url")),
        "application_type": application_type,
        "application_method": "온라인" if control else "",
        "application_methods": ["온라인"] if control else [],
        "reservation_available": control,
        "status": status,
        "fee": "요금 별도 안내",
        "fee_amount": None,
        "period": (
            f"{listed['start'].isoformat()} ~ {listed['end'].isoformat()}"
        ),
        "start_date": listed["start"].isoformat(),
        "end_date": listed["end"].isoformat(),
        "apply_period": _clean(listed.get("apply_period")),
        "schedule_raw": _clean(listed.get("schedule")),
        "capacity": _clean(detail.get("capacity")),
        "capacity_current": None,
        "capacity_total": detail.get("capacity_total"),
        "target": "대상 별도 안내",
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GWANGJU_SEOGU_PARSER,
        "municipality_code": GWANGJU_SEOGU_MUNICIPALITY_CODE,
        "municipality_full_name": GWANGJU_SEOGU_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_kind": _clean(listed.get("source_kind")),
            "list_page": int(listed.get("list_page") or 0),
            "source_status": _clean(listed.get("source_status")),
            "source_institution": institution,
            "source_subbranch": subbranch,
            "source_period": _clean(listed.get("source_period")),
            "source_apply_period": _clean(listed.get("apply_period")),
            "source_schedule": _clean(listed.get("schedule")),
            "source_venue": _clean(listed.get("venue")),
            "source_eap_code": _clean(listed.get("eap_code")),
            "source_institution_code": _clean(detail.get("institution_code")),
            "source_detail_category": _clean(detail.get("detail_category")),
            "detail_verified": True,
            "visible_application_control_present": visible_control,
            "actionable_application_control_present": control,
            "application_control_contract": _clean(detail.get("contract")),
            "source_target_omitted": True,
            "source_fee_omitted": True,
            "venue_fallback_used": not bool(source_venue),
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded PII-safe allowlist")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url"}
        }
    )
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
        "source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "identity_duplicate_count": 0,
        "raw_url_duplicate_count": 0,
        "semantic_duplicate_group_count": 0,
        "semantic_duplicate_excess_rows": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": error,
        "municipality_code": GWANGJU_SEOGU_MUNICIPALITY_CODE,
        "municipality_name": GWANGJU_SEOGU_MUNICIPALITY_NAME,
        "canonical_candidate_id": GWANGJU_SEOGU_CANONICAL_CANDIDATE_ID,
        "canonical_url": GWANGJU_SEOGU_CANONICAL_URL,
        "ownership_scope": GWANGJU_SEOGU_OWNERSHIP_SCOPE,
    }


def collect_gwangju_seogu_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 30,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GWANGJU_SEOGU_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future education snapshot."""

    meta = _base_meta()
    if not is_gwangju_seogu_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Gwangju Seo-gu education owner"
        )
        return [], GWANGJU_SEOGU_PARSER, meta
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
        return [], GWANGJU_SEOGU_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], GWANGJU_SEOGU_PARSER, meta

    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    workers = min(max_workers, GWANGJU_SEOGU_MAX_WORKERS)
    errors: list[str] = []

    def fetch_list(page: int) -> _ListPage:
        soup = _fetch_soup(
            gwangju_seogu_list_url(page),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return _parse_list_page(soup, page)

    try:
        first = fetch_list(1)
        meta["list_requests"] = 1
        meta["pages"] = 1
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"page 1: {type(exc).__name__}: {_clean(exc)}"
        )
        return [], GWANGJU_SEOGU_PARSER, meta

    last = first.data_last
    required_list_requests = last + 3
    meta.update(
        {
            "declared_source_rows": first.total,
            "declared_data_pages": first.displayed_last,
            "derived_data_pages": last,
            "required_list_requests": required_list_requests,
            "sentinel_mode": "explicit_empty_not_clamped",
        }
    )
    if required_list_requests > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of "
                    f"{required_list_requests} required list requests"
                ),
            }
        )
        return [], GWANGJU_SEOGU_PARSER, meta

    jobs: list[tuple[str, int]] = [("data", page) for page in range(2, last + 1)]
    jobs.extend(
        (("sentinel", last + 1), ("first_recheck", 1), ("last_recheck", last))
    )
    parsed_jobs: dict[tuple[str, int], _ListPage] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_list, page): (kind, page) for kind, page in jobs}
        for future in as_completed(futures):
            kind, page = futures[future]
            try:
                parsed_jobs[(kind, page)] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(
                    f"{kind} page {page}: {type(exc).__name__}: {_clean(exc)}"
                )

    page_rows: dict[int, tuple[dict[str, Any], ...]] = {1: first.rows}
    for page in range(1, last + 1):
        parsed = first if page == 1 else parsed_jobs.get(("data", page))
        if parsed is None:
            errors.append(f"data page {page}: response missing")
            continue
        if (
            parsed.total != first.total
            or parsed.data_last != last
            or parsed.displayed_last != first.displayed_last
        ):
            errors.append(f"data page {page}: total/page boundary changed")
        expected = min(
            GWANGJU_SEOGU_PAGE_SIZE,
            max(0, first.total - (page - 1) * GWANGJU_SEOGU_PAGE_SIZE),
        )
        if len(parsed.rows) != expected:
            errors.append(f"data page {page}: row count {len(parsed.rows)} != {expected}")
        page_rows[page] = parsed.rows

    sentinel = parsed_jobs.get(("sentinel", last + 1))
    if sentinel is None:
        errors.append("immediate post-last sentinel response missing")
    elif (
        sentinel.total != first.total
        or sentinel.data_last != last
        or sentinel.displayed_last != first.displayed_last
        or sentinel.rows
    ):
        errors.append("immediate post-last sentinel is not stable empty")
    else:
        meta["sentinel_requests"] = 1

    first_recheck = parsed_jobs.get(("first_recheck", 1))
    last_recheck = parsed_jobs.get(("last_recheck", last))
    if first_recheck is None or last_recheck is None:
        errors.append("first/last stability recheck response missing")
    else:
        meta["stability_rechecks"] = 2
        if (
            first_recheck.total != first.total
            or first_recheck.data_last != last
            or _page_signature(first_recheck.rows) != _page_signature(first.rows)
        ):
            errors.append("first-page stability recheck changed")
        if (
            last_recheck.total != first.total
            or last_recheck.data_last != last
            or _page_signature(last_recheck.rows)
            != _page_signature(page_rows.get(last, ()))
        ):
            errors.append("last-page stability recheck changed")

    listed = [
        row for page in range(1, last + 1) for row in page_rows.get(page, ())
    ]
    identities = [_clean(row.get("identity")) for row in listed]
    identity_duplicates = len(identities) - len(set(identities))
    raw_urls = [_clean(row.get("raw_url")) for row in listed]
    raw_url_duplicates = len(raw_urls) - len(set(raw_urls))
    semantic_counter = Counter(
        (
            _normalized(row.get("title")),
            _clean(row.get("source_period")),
            _normalized(row.get("venue")),
        )
        for row in listed
    )
    semantic_groups = sum(count > 1 for count in semantic_counter.values())
    semantic_excess = sum(max(0, count - 1) for count in semantic_counter.values())
    if identity_duplicates:
        errors.append(f"{identity_duplicates} duplicate official identities")
    if raw_url_duplicates:
        errors.append(f"{raw_url_duplicates} duplicate canonical detail URLs")
    if semantic_groups:
        errors.append(f"{semantic_groups} semantic duplicate groups")
    if len(listed) != first.total:
        errors.append(f"complete row count {len(listed)} != declared total {first.total}")
    list_complete = bool(
        not errors
        and meta["list_requests"] == required_list_requests
        and meta["sentinel_requests"] == 1
        and meta["stability_rechecks"] == 2
        and len(listed) == first.total
    )
    current_listed = [row for row in listed if row["end"] >= cutoff]
    meta.update(
        {
            "data_pages": len(page_rows),
            "source_rows": len(listed),
            "current_source_count": len(current_listed),
            "expired_count": len(listed) - len(current_listed),
            "identity_duplicate_count": identity_duplicates,
            "raw_url_duplicate_count": raw_url_duplicates,
            "semantic_duplicate_group_count": semantic_groups,
            "semantic_duplicate_excess_rows": semantic_excess,
            "source_kind_counts_all": dict(
                Counter(_clean(row.get("source_kind")) for row in listed)
            ),
            "source_status_counts_all": dict(
                Counter(_clean(row.get("source_status")) for row in listed)
            ),
            "institution_counts_all": dict(
                Counter(_clean(row.get("institution")) for row in listed)
            ),
            "pagination_complete": list_complete,
        }
    )
    if not list_complete:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], GWANGJU_SEOGU_PARSER, meta
    if len(current_listed) > detail_limit:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit cap allows {detail_limit} of "
                    f"{len(current_listed)} required current details"
                ),
            }
        )
        return [], GWANGJU_SEOGU_PARSER, meta

    meta["detail_attempts"] = len(current_listed)
    detailed: dict[str, dict[str, Any]] = {}
    detail_errors: list[str] = []

    def fetch_detail(listed_row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        identity = _clean(listed_row.get("identity"))
        soup = _fetch_soup(
            _clean(listed_row.get("raw_url")),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        kind = _clean(listed_row.get("source_kind"))
        if kind == "native":
            detail = _parse_native_detail(soup, listed_row)
        elif kind == "365edu":
            detail = _parse_365_detail(soup, listed_row)
        elif kind == "job":
            detail = _parse_job_detail(soup, listed_row)
        else:
            raise GwangjuSeoguContractError("unknown detail parser")
        return identity, _build_row(listed_row, detail)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_detail, row): row for row in current_listed}
        for future in as_completed(futures):
            listed_row = futures[future]
            identity = _clean(listed_row.get("identity"))
            try:
                parsed_identity, parsed = future.result()
                if parsed_identity in detailed:
                    raise GwangjuSeoguContractError("duplicate parsed detail identity")
                detailed[parsed_identity] = parsed
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                detail_errors.append(
                    f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                )
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        not detail_errors
        and meta["detail_pages"] == len(current_listed)
        and len(detailed) == len(current_listed)
    )
    ordered = [detailed[identity] for identity in identities if identity in detailed]
    controls_complete = bool(
        details_complete
        and all(
            (row.get("status") in {"OPEN", "WAITLIST"})
            == bool(
                row.get("raw_fields", {}).get(
                    "actionable_application_control_present"
                )
            )
            and bool(row.get("application_url"))
            == bool(
                row.get("raw_fields", {}).get(
                    "actionable_application_control_present"
                )
            )
            and (
                not row.get("raw_fields", {}).get(
                    "actionable_application_control_present"
                )
                or row.get("raw_fields", {}).get(
                    "visible_application_control_present"
                )
            )
            for row in ordered
        )
    )
    result: list[dict[str, Any]] = []
    if details_complete and controls_complete and not errors:
        for row in ordered:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(ordered))
            except Exception as exc:
                errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                result = []
            if len(result) != len(ordered):
                errors.append(
                    "dedupe changed official identity cardinality "
                    f"{len(ordered)} to {len(result)}"
                )
                result = []

    snapshot_complete = bool(
        list_complete and details_complete and controls_complete and not errors
    )
    if not snapshot_complete:
        result = []
    meta.update(
        {
            "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
            "institution_counts": dict(
                Counter(
                    _clean(row.get("raw_fields", {}).get("source_institution"))
                    for row in result
                )
            ),
            "source_kind_counts": dict(
                Counter(
                    _clean(row.get("raw_fields", {}).get("source_kind"))
                    for row in result
                )
            ),
            "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
            "visible_public_application_control_count": sum(
                bool(
                    row.get("raw_fields", {}).get(
                        "visible_application_control_present"
                    )
                )
                for row in ordered
            ),
            "actionable_application_control_count": sum(
                bool(
                    row.get("raw_fields", {}).get(
                        "actionable_application_control_present"
                    )
                )
                for row in ordered
            ),
            "details_complete": details_complete,
            "application_controls_complete": controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not current_listed),
            "no_current_reason": (
                "the complete official education catalogue has no current/future courses"
                if snapshot_complete and not current_listed
                else ""
            ),
            "candidate_audit": {
                key: dict(value) for key, value in GWANGJU_SEOGU_CANDIDATE_AUDIT.items()
            },
            "discovery_audit": dict(GWANGJU_SEOGU_DISCOVERY_AUDIT),
            "superseded_landing_providers": [GWANGJU_SEOGU_LANDING_PROVIDER],
            "excluded_providers": [
                GWANGJU_SEOGU_HEALTH_PROVIDER,
                GWANGJU_SEOGU_CULTURE_PROVIDER,
            ],
            "municipality_coverage": [GWANGJU_SEOGU_MUNICIPALITY_CODE],
            "pii_fields_discarded": list(GWANGJU_SEOGU_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "network_concurrency": workers,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, GWANGJU_SEOGU_PARSER, meta


collect = collect_gwangju_seogu_education


__all__ = [
    "GWANGJU_SEOGU_CANONICAL_CANDIDATE_ID",
    "GWANGJU_SEOGU_CANONICAL_URL",
    "GWANGJU_SEOGU_CANDIDATE_AUDIT",
    "GWANGJU_SEOGU_CANDIDATE_URL",
    "GWANGJU_SEOGU_CULTURE_CANDIDATE_ID",
    "GWANGJU_SEOGU_DISCOVERY_AUDIT",
    "GWANGJU_SEOGU_HEALTH_CANDIDATE_ID",
    "GWANGJU_SEOGU_LANDING_CANDIDATE_ID",
    "GWANGJU_SEOGU_MUNICIPALITY_CODE",
    "GWANGJU_SEOGU_MUNICIPALITY_NAME",
    "GWANGJU_SEOGU_PARSER",
    "GWANGJU_SEOGU_PROVIDER",
    "GwangjuSeoguContractError",
    "collect",
    "collect_gwangju_seogu_education",
    "gwangju_seogu_job_application_url",
    "gwangju_seogu_job_detail_url",
    "gwangju_seogu_list_url",
    "gwangju_seogu_native_application_url",
    "gwangju_seogu_native_detail_url",
    "is_gwangju_seogu_candidate_alias",
    "is_gwangju_seogu_education_target",
    "is_target",
]
