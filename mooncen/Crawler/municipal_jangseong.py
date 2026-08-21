"""Fail-closed collector for Jangseong-gun's official education catalogues.

The municipal discovery candidate is an unverified third-party guide and is
not a data owner.  The official owner is Jangseong-gun's own CMS.  This
collector reconciles the integrated-reservation digital-education catalogue
and the eight disjoint lifelong-learning application catalogues hosted by the
same municipality.

Every declared data page is fetched, followed by the immediate empty page and
stable first/last boundary rechecks.  Empty catalogues are rechecked as empty.
Only current/future detail pages are opened.  Applicant lists, login/application
forms, instructor/contact cells, free-form content, addresses, and attachments
are never fetched or persisted.  An online application is retained only when
the list exposes an identity-bound official login control for that course.

The separately operated Jangseong County Library SPA/API is deliberately not
mixed into this owner; it requires its own provider and crawler.
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


JANGSEONG_PROVIDER = "MUNI_WWW_JANGSEONG_GO_KR_531090D8"
JANGSEONG_CANDIDATE_ID = "MUNI_IR_17B938068B7C"
JANGSEONG_UNTRUSTED_CANDIDATE_PROVIDER = "MUNI_GUSLE_KR_12284CD8"

JANGSEONG_HOST = "www.jangseong.go.kr"
JANGSEONG_ROOT_URL = f"https://{JANGSEONG_HOST}/home/ok"
JANGSEONG_DIGITAL_PATH = "/home/ok/edu/edu_02/edu_02_01"
JANGSEONG_DIGITAL_URL = f"https://{JANGSEONG_HOST}{JANGSEONG_DIGITAL_PATH}"
JANGSEONG_CANONICAL_URL = JANGSEONG_DIGITAL_URL
JANGSEONG_LIFELONG_ROOT_URL = f"https://{JANGSEONG_HOST}/home/lifelong"
JANGSEONG_LIBRARY_URL = "https://lib.jangseong.go.kr/Lecture"
JANGSEONG_LIBRARY_API_URL = "https://lib.jangseong.go.kr/user/lecture/list/1"
JANGSEONG_EDUCATION_SUPPORT_URL = (
    "https://jsed.jne.go.kr/jsed/na/ntt/selectNttList.do"
)
JANGSEONG_MUNICIPALITY_CODE = "1284000000"
JANGSEONG_MUNICIPALITY_NAME = "전남광주통합특별시 장성군"
JANGSEONG_PAGE_SIZE = 15
# The official endpoint intermittently answers concurrent TLS handshakes with
# WRONG_VERSION_NUMBER.  Serial requests are slower but make the complete
# snapshot reliable; callers cannot raise this above the audited limit.
JANGSEONG_MAX_WORKERS = 1
JANGSEONG_FETCH_ATTEMPTS = 5
JANGSEONG_RETRY_BACKOFF_SECONDS = 1.0
JANGSEONG_MAX_HTML_BYTES = 4_000_000
JANGSEONG_PARSER = (
    "jangseong_official_integrated_digital+eight_lifelong_application_scopes+"
    "all_pages+empty_sentinels+stable_boundaries+current_detail_venues+"
    "identity_bound_login_controls+pii_allowlist"
)
JANGSEONG_OWNERSHIP_SCOPE = (
    "jangseong_official_integrated_digital_and_lifelong_application_catalogues"
)


@dataclass(frozen=True)
class _Catalogue:
    key: str
    path: str
    label: str
    title: str
    caption: str
    numbered: bool
    site: str


def _lifelong_catalogue(key: str, section: str, label: str) -> _Catalogue:
    path = f"/home/lifelong/lifelong02/{section}/{section}_02"
    return _Catalogue(
        key=key,
        path=path,
        label=label,
        title=f"신청하기 < {label} < 평생학습 프로그램 < 장성군 평생학습관",
        caption=f"{label}으로 강좌명, 교육장소, 접수현황, 기간으로 구성된 표입니다.",
        numbered=True,
        site="lifelong",
    )


_CATALOGUES: tuple[_Catalogue, ...] = (
    _Catalogue(
        key="digital",
        path=JANGSEONG_DIGITAL_PATH,
        label="정보화교육",
        title="정보화교육신청 < 정보화교육 < 교육/강좌 < 장성군청",
        caption="강좌예약으로 강좌명, 교육장소, 접수현황, 기간으로 구성된 표입니다.",
        numbered=False,
        site="ok",
    ),
    _lifelong_catalogue("lifelong_children", "lifelong02_27", "아동,청소년"),
    _lifelong_catalogue("lifelong_disabled", "lifelong02_28", "장애인"),
    _lifelong_catalogue("lifelong_senior", "lifelong02_29", "백세누리"),
    _lifelong_catalogue("lifelong_job", "lifelong02_30", "직업교육"),
    _lifelong_catalogue("lifelong_health", "lifelong02_25", "건강체육"),
    _lifelong_catalogue("lifelong_culture", "lifelong02_23", "문화예술"),
    _lifelong_catalogue("lifelong_hobby", "lifelong02_24", "인문교양"),
    _lifelong_catalogue("lifelong_resident", "lifelong02_26", "주민자치센터"),
)
JANGSEONG_CATALOGUES: Mapping[str, _Catalogue] = {
    catalogue.key: catalogue for catalogue in _CATALOGUES
}
JANGSEONG_SCOPE_PATHS: Mapping[str, str] = {
    catalogue.key: catalogue.path for catalogue in _CATALOGUES
}
JANGSEONG_SCOPE_URLS: Mapping[str, str] = {
    catalogue.key: f"https://{JANGSEONG_HOST}{catalogue.path}"
    for catalogue in _CATALOGUES
}

_LIST_HEADERS_DIGITAL = ("강좌명", "교육장소", "접수현황", "기간")
_LIST_HEADERS_NUMBERED = (
    "번호",
    "강좌명",
    "교육장소",
    "접수현황",
    "기간",
)
_DETAIL_CAPTION_SUFFIX = (
    "글 내용보기 - 교육구분, 교육정원, 접수기간, 교육기간, 총시간, "
    "교육장소, 내용, 첨부파일, 교육과정 제공 표"
)
_DIGITAL_DETAIL_LABELS = (
    "교육구분",
    "교육정원",
    "접수기간",
    "교육기간",
    "총시간",
    "교육장소",
    "내용",
    "첨부파일",
)
_LIFELONG_DETAIL_LABELS = (
    "강좌명",
    "교육대상",
    "모집인원",
    "교육장소",
    "상세주소",
    "접수기간",
    "교육기간",
    "강사명",
    "문의전화",
    "내용",
    "신청url",
    "첨부파일",
)
_APPLICATION_STATUS_MAP: Mapping[str, str] = {
    "신청하기": "OPEN",
    "신청마감": "CLOSED",
}
_STATUS_IMAGE_SOURCES: Mapping[str, str] = {
    "신청": "/images/www/sub/lecture_application.gif",
    "신청하기": "/images/www/sub/lecture_application_ing.gif",
    "신청마감": "/images/www/sub/lecture_application_end.gif",
    "교육": "/images/www/sub/lecture_edu.gif",
    "교육중": "/images/www/sub/lecture_edu_ing.gif",
    "교육종료": "/images/www/sub/lecture_edu_end.gif",
}

JANGSEONG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    JANGSEONG_CANDIDATE_ID: {
        "decision": "exclude_unverified_third_party_guide_and_replace_with_official_owner",
        "provider": JANGSEONG_UNTRUSTED_CANDIDATE_PROVIDER,
        "url": "https://gusle.kr/jangseong-gov-website/",
        "owner": JANGSEONG_PROVIDER,
        "official_url": JANGSEONG_ROOT_URL,
    },
    "OFFICIAL_JANGSEONG_INTEGRATED_DIGITAL": {
        "decision": "include_canonical_integrated_reservation_education_catalogue",
        "provider": JANGSEONG_PROVIDER,
        "url": JANGSEONG_DIGITAL_URL,
        "owner": JANGSEONG_PROVIDER,
    },
    "OFFICIAL_JANGSEONG_LIFELONG_APPLICATIONS": {
        "decision": "include_eight_disjoint_official_application_scopes",
        "provider": JANGSEONG_PROVIDER,
        "url": JANGSEONG_LIFELONG_ROOT_URL,
        "owner": JANGSEONG_PROVIDER,
        "scope_count": 8,
    },
    "OFFICIAL_JANGSEONG_LIFELONG_GUIDES": {
        "decision": "exclude_information_guides_republishing_application_course_identity",
        "provider": JANGSEONG_PROVIDER,
        "url": JANGSEONG_LIFELONG_ROOT_URL,
        "owner": JANGSEONG_PROVIDER,
    },
    "SEPARATE_JANGSEONG_COUNTY_LIBRARY": {
        "decision": "exclude_separate_library_spa_api_owner_requires_own_provider",
        "provider": "SEPARATE_LIB_JANGSEONG_GO_KR",
        "url": JANGSEONG_LIBRARY_URL,
        "public_api": JANGSEONG_LIBRARY_API_URL,
        "owner": "jangseong_county_library",
        "audited_total_rows": 582,
        "audited_total_pages": 59,
        "branch_codes": {
            "A": "중앙",
            "B": "삼계",
            "C": "북이",
            "D": "진원",
            "E": "삼서드림빌",
            "F": "도서관주간",
            "G": "독서의달",
        },
    },
    "SEPARATE_JANGSEONG_EDUCATION_SUPPORT_NEWS": {
        "decision": "exclude_separate_education_support_notice_board_not_booking_catalogue",
        "provider": "SEPARATE_JSED_JNE_GO_KR",
        "url": JANGSEONG_EDUCATION_SUPPORT_URL,
        "owner": "jangseong_education_support_office",
    },
}

JANGSEONG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": JANGSEONG_CANONICAL_URL,
    "source_rows": 43,
    "source_rows_by_scope": {
        "digital": 32,
        "lifelong_children": 1,
        "lifelong_disabled": 0,
        "lifelong_senior": 1,
        "lifelong_job": 1,
        "lifelong_health": 8,
        "lifelong_culture": 0,
        "lifelong_hobby": 0,
        "lifelong_resident": 0,
    },
    "data_pages": 7,
    "required_list_requests": 30,
    "current_or_future_rows": 9,
    "current_scope_counts": {
        "lifelong_children": 1,
        "lifelong_health": 8,
    },
    "expired_rows": 34,
    "source_status_counts": {"신청마감": 36, "신청하기": 7},
    "current_normalized_status_counts": {"OPEN": 7, "CLOSED": 2},
    "detail_pages_verified": 9,
    "visible_identity_bound_application_controls": 7,
    "current_branch_counts": {
        "장성군청소년수련관 2층": 1,
        "삼계테니스장": 1,
        "워라밸돔구장": 1,
        "생활체육공원": 1,
        "홍길동체육관 지하1층": 4,
        "홍길동체육관": 1,
    },
    "current_branch_names": [
        "삼계테니스장",
        "생활체육공원",
        "워라밸돔구장",
        "장성군청소년수련관 2층",
        "홍길동체육관",
        "홍길동체육관 지하1층",
    ],
    "identity_duplicate_count": 0,
    "historical_unparseable_education_period_count": 1,
    "separate_library_total_rows": 582,
    "separate_library_total_pages": 59,
    "conclusion": "official_cms_scopes_roll_up_to_new_owner_library_remains_separate",
}

JANGSEONG_PII_FIELDS_DISCARDED = (
    "상세주소",
    "강사명",
    "문의전화",
    "내용",
    "신청url",
    "첨부파일",
    "교육과정",
    "request_list",
    "application_form_values",
    "applicant_identity",
    "member_profile",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class JangseongContractError(ValueError):
    """Raised when the audited official catalogue contract changes."""


@dataclass(frozen=True)
class _ListPage:
    scope: str
    requested_page: int
    declared_total: Optional[int]
    source_pages: int
    rows: tuple[dict[str, Any], ...]
    empty_marker: bool


_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"^[1-9]\d*$")
_DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_DIGITAL_CAPACITY_RE = re.compile(r"^(?P<total>[\d,]+)명$")
_LIFELONG_CAPACITY_RE = re.compile(r"^(?P<current>[\d,]+)\s*/\s*(?P<total>[\d,]+)$")
_PHONE_RE = re.compile(r"(?<!\d)(?:0\d{1,2}[-. ]?)?\d{3,4}[-. ]\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_FORBIDDEN_PERSISTED_KEYS = {
    "address",
    "address_detail",
    "attachment",
    "attachments",
    "contact",
    "contact_phone",
    "content",
    "instructor",
    "lecturer",
    "manager",
    "phone",
    "source_html",
}
_SAFE_RAW_FIELDS = {
    "scope",
    "identity",
    "source_page",
    "source_position",
    "source_row_number",
    "source_status",
    "source_education_status",
    "source_period",
    "source_apply_period",
    "source_capacity_current",
    "source_capacity_total",
    "source_application_control_present",
    "detail_target",
    "detail_schedule",
    "detail_venue",
    "detail_capacity_total",
    "fee_evidence",
    "target_evidence",
    "schedule_evidence",
    "detail_verified",
    "visible_application_control_present",
    "application_control_contract",
    "service_family",
}


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


def is_jangseong_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != JANGSEONG_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == JANGSEONG_HOST
        and _safe_port(parsed) is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == JANGSEONG_DIGITAL_PATH
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_jangseong_education_target


def is_jangseong_candidate_alias(target: Any) -> bool:
    candidate = _clean(_target_value(target, "candidate_id"))
    url = _clean(_target_value(target, "url")).rstrip("/")
    return bool(
        candidate == JANGSEONG_CANDIDATE_ID
        or url
        in {
            "https://gusle.kr/jangseong-gov-website",
            JANGSEONG_ROOT_URL,
            JANGSEONG_LIFELONG_ROOT_URL,
            JANGSEONG_LIBRARY_URL,
        }
    )


def _catalogue(scope: Any) -> _Catalogue:
    key = _clean(scope)
    if key not in JANGSEONG_CATALOGUES:
        raise ValueError("unknown Jangseong catalogue scope")
    return JANGSEONG_CATALOGUES[key]


def jangseong_list_url(scope: str, page: int) -> str:
    catalogue = _catalogue(scope)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    return f"https://{JANGSEONG_HOST}{catalogue.path}?" + urlencode(
        (
            ("page", str(page)),
            ("search", ""),
            ("keyword", ""),
            ("cate_sel", ""),
        )
    )


def jangseong_detail_url(scope: str, identity: Any, page: int = 1) -> str:
    catalogue = _catalogue(scope)
    value = _clean(identity)
    if _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    return f"https://{JANGSEONG_HOST}{catalogue.path}/show/{value}?" + urlencode(
        (
            ("page", str(page)),
            ("search", ""),
            ("keyword", ""),
            ("cate_sel", ""),
        )
    )


def jangseong_application_url(scope: str, identity: Any) -> str:
    catalogue = _catalogue(scope)
    value = _clean(identity)
    if _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    return_target = (
        "http:" f"//{JANGSEONG_HOST}{catalogue.path}/?mode=request_insert"
    )
    return (
        f"https://{JANGSEONG_HOST}/home/{catalogue.site}/support/login"
        f"?set=attest&return_url={return_target}&idx={value}"
    )


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        parsed = _clean(value)
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", parsed) is None:
            raise ValueError("today must be YYYY-MM-DD")
        return date.fromisoformat(parsed)
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(JANGSEONG_FETCH_ATTEMPTS):
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            final = urlparse(_clean(getattr(response, "url", url)))
            if (
                final.scheme != "https"
                or (final.hostname or "").rstrip(".").lower() != JANGSEONG_HOST
                or _safe_port(final) is not None
                or final.username is not None
                or final.password is not None
            ):
                raise JangseongContractError(
                    "official response redirected out of ownership"
                )
            content = bytes(getattr(response, "content", b""))
            if len(content) > JANGSEONG_MAX_HTML_BYTES:
                raise JangseongContractError("official HTML exceeded size cap")
            content_type = _clean(
                getattr(response, "headers", {}).get("Content-Type")
            )
            if content_type and not any(
                token in content_type.lower()
                for token in ("html", "xhtml", "text/plain")
            ):
                raise JangseongContractError("official response is not HTML")
            response.encoding = "utf-8"
            return response
        except Exception as exc:
            last_error = exc
            if attempt + 1 < JANGSEONG_FETCH_ATTEMPTS:
                time.sleep(JANGSEONG_RETRY_BACKOFF_SECONDS * (attempt + 1))
    assert last_error is not None
    raise last_error


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value.decode("utf-8"), "html.parser")
    if isinstance(value, str):
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if isinstance(content, bytes):
        return BeautifulSoup(content.decode("utf-8"), "html.parser")
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return BeautifulSoup(text, "html.parser")
    raise TypeError("fetcher must return HTML, BeautifulSoup, or a response")


def _close_quietly(value: Any) -> None:
    closer = getattr(value, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def _fetch_soup(
    url: str,
    *,
    timeout: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> BeautifulSoup:
    session = session_factory()
    try:
        return _coerce_soup(fetcher(session, url, timeout))
    finally:
        _close_quietly(session)


def _owned_detail_url(value: Any, scope: str, source_page: int) -> tuple[str, str]:
    catalogue = _catalogue(scope)
    parsed = urlparse(
        urljoin(f"https://{JANGSEONG_HOST}{catalogue.path}", _clean(value))
    )
    prefix = catalogue.path + "/show/"
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != JANGSEONG_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith(prefix)
        or parsed.fragment
    ):
        raise JangseongContractError("course detail URL escaped its official scope")
    identity = parsed.path[len(prefix) :]
    if "/" in identity or _POSITIVE_ID_RE.fullmatch(identity) is None:
        raise JangseongContractError("course detail identity changed")
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected = {
        "page": [str(source_page)],
        "search": [""],
        "keyword": [""],
        "cate_sel": [""],
    }
    if query != expected:
        raise JangseongContractError("course detail query contract changed")
    return identity, jangseong_detail_url(scope, identity, source_page)


def _owned_application_url(value: Any, scope: str, identity: str) -> str:
    catalogue = _catalogue(scope)
    parsed = urlparse(
        urljoin(f"https://{JANGSEONG_HOST}{catalogue.path}", _clean(value))
    )
    expected_path = f"/home/{catalogue.site}/support/login"
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != JANGSEONG_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != expected_path
        or parsed.fragment
        or set(query) != {"set", "return_url", "idx"}
        or any(len(values) != 1 for values in query.values())
        or query["set"] != ["attest"]
        or query["idx"] != [identity]
    ):
        raise JangseongContractError(
            f"course {identity}: application login control changed"
        )
    return_target = urlparse(query["return_url"][0])
    return_query = parse_qs(return_target.query, keep_blank_values=True)
    if (
        return_target.scheme != "http"
        or (return_target.hostname or "").rstrip(".").lower() != JANGSEONG_HOST
        or _safe_port(return_target) is not None
        or return_target.username is not None
        or return_target.password is not None
        or return_target.path != catalogue.path + "/"
        or return_query != {"mode": ["request_insert"]}
        or return_target.fragment
    ):
        raise JangseongContractError(
            f"course {identity}: application return identity changed"
        )
    return jangseong_application_url(scope, identity)


def _pagination(
    soup: BeautifulSoup,
    catalogue: _Catalogue,
    requested_page: int,
    *,
    empty: bool,
) -> tuple[int, tuple[int, ...]]:
    roots = soup.select("#content > div.pagenum")
    if len(roots) != 1:
        raise JangseongContractError(
            f"{catalogue.key} page {requested_page}: pagination root changed"
        )
    current = roots[0].find_all("strong", recursive=False)
    if empty:
        if current:
            raise JangseongContractError(
                f"{catalogue.key} page {requested_page}: empty page is marked current"
            )
    elif len(current) != 1 or _clean(current[0].get_text()) != str(requested_page):
        raise JangseongContractError(
            f"{catalogue.key} page {requested_page}: current page marker changed"
        )
    values: list[int] = []
    if current:
        values.append(int(_clean(current[0].get_text())))
    for anchor in roots[0].find_all("a", recursive=False):
        parsed = urlparse(
            urljoin(
                f"https://{JANGSEONG_HOST}{catalogue.path}",
                _clean(anchor.get("href")),
            )
        )
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").rstrip(".").lower() != JANGSEONG_HOST
            or parsed.path != catalogue.path
            or parsed.fragment
            or set(query) != {"page", "search", "keyword", "cate_sel"}
            or any(len(items) != 1 for items in query.values())
            or query["search"] != [""]
            or query["keyword"] != [""]
            or query["cate_sel"] != [""]
            or not query["page"][0].isdigit()
            or int(query["page"][0]) < 1
        ):
            raise JangseongContractError(
                f"{catalogue.key} page {requested_page}: pagination link changed"
            )
        values.append(int(query["page"][0]))
    return (max(values) if values else 0), tuple(values)


def _parse_capacity(cell: Any, numbered: bool, identity: str) -> tuple[int, int]:
    text = _clean(cell.get_text(" ", strip=True))
    match = (
        _LIFELONG_CAPACITY_RE.fullmatch(text)
        if numbered
        else _DIGITAL_CAPACITY_RE.fullmatch(text)
    )
    if match is None:
        raise JangseongContractError(f"course {identity}: capacity changed")
    total = int(match.group("total").replace(",", ""))
    current = int(match.group("current").replace(",", "")) if numbered else 0
    if total < 1 or current < 0 or current > total:
        raise JangseongContractError(f"course {identity}: capacity is invalid")
    return current, total


def _parse_period_status(
    cell: Any,
    scope: str,
    identity: str,
) -> tuple[str, str, str, str, Optional[date], Optional[date], date, date, str]:
    images = cell.select("img[alt][src]")
    if len(images) != 4:
        raise JangseongContractError(f"course {identity}: status images changed")
    labels = tuple(_clean(image.get("alt")) for image in images)
    if (
        labels[0] != "신청"
        or labels[1] not in _APPLICATION_STATUS_MAP
        or labels[2] != "교육"
        or labels[3] not in {"교육중", "교육종료"}
    ):
        raise JangseongContractError(f"course {identity}: source status changed")
    for image, label in zip(images, labels):
        if _clean(image.get("src")) != _STATUS_IMAGE_SOURCES[label]:
            raise JangseongContractError(
                f"course {identity}: source status image changed"
            )
    application_links = cell.find_all("a")
    application_url = ""
    if labels[1] == "신청하기":
        parent = images[1].parent
        if (
            getattr(parent, "name", None) != "a"
            or parent not in application_links
            or len(application_links) != 1
        ):
            raise JangseongContractError(
                f"course {identity}: open application control changed"
            )
        application_url = _owned_application_url(parent.get("href"), scope, identity)
    elif application_links:
        raise JangseongContractError(
            f"course {identity}: closed row exposes application control"
        )
    dates = _DATE_RE.findall(_clean(cell.get_text(" ", strip=True)))
    if len(dates) not in {2, 4}:
        raise JangseongContractError(f"course {identity}: date ranges changed")
    apply_start = date.fromisoformat(dates[0])
    apply_end = date.fromisoformat(dates[1])
    start: Optional[date] = None
    end: Optional[date] = None
    if len(dates) == 4:
        start = date.fromisoformat(dates[2])
        end = date.fromisoformat(dates[3])
    elif labels[3] != "교육종료":
        raise JangseongContractError(
            f"course {identity}: current education period is incomplete"
        )
    return (
        labels[1],
        _APPLICATION_STATUS_MAP[labels[1]],
        labels[3],
        application_url,
        start,
        end,
        apply_start,
        apply_end,
        f"{dates[2]} ~ {dates[3]}" if len(dates) == 4 else "",
    )


def _parse_list_row(
    row: Any,
    catalogue: _Catalogue,
    page: int,
    position: int,
) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    expected_columns = 5 if catalogue.numbered else 4
    if len(cells) != expected_columns:
        raise JangseongContractError(
            f"{catalogue.key} page {page}: course columns changed"
        )
    offset = 1 if catalogue.numbered else 0
    row_number: Optional[int] = None
    if catalogue.numbered:
        raw_number = _clean(cells[0].get_text(" ", strip=True))
        if not raw_number.isdigit() or int(raw_number) < 1:
            raise JangseongContractError(
                f"{catalogue.key} page {page}: row number changed"
            )
        row_number = int(raw_number)
    links = cells[offset].find_all("a", recursive=False)
    if len(links) != 1:
        raise JangseongContractError(
            f"{catalogue.key} page {page}: detail link changed"
        )
    identity, raw_url = _owned_detail_url(
        links[0].get("href"), catalogue.key, page
    )
    title = _clean(links[0].get_text(" ", strip=True))
    venue = _clean(cells[offset + 1].get_text(" ", strip=True))
    if not title or len(title) > 300 or len(venue) > 200:
        raise JangseongContractError(f"course {identity}: safe list fields changed")
    capacity_current, capacity_total = _parse_capacity(
        cells[offset + 2], catalogue.numbered, identity
    )
    (
        source_status,
        status,
        education_status,
        application_url,
        start,
        end,
        apply_start,
        apply_end,
        period,
    ) = _parse_period_status(cells[offset + 3], catalogue.key, identity)
    return {
        "scope": catalogue.key,
        "identity": identity,
        "source_page": page,
        "source_position": position,
        "source_row_number": row_number,
        "title": title,
        "list_venue": venue,
        "raw_url": raw_url,
        "source_status": source_status,
        "status": status,
        "source_education_status": education_status,
        "application_url": application_url,
        "source_application_control_present": bool(application_url),
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "start": start,
        "end": end,
        "period": period,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
    }


def _parse_list_page(
    soup: BeautifulSoup,
    scope: str,
    requested_page: int,
    *,
    expected_total: Optional[int] = None,
    expected_pages: Optional[int] = None,
    sentinel: bool = False,
) -> _ListPage:
    catalogue = _catalogue(scope)
    titles = soup.select("head > title")
    if len(titles) != 1 or _clean(titles[0].get_text(" ", strip=True)) != catalogue.title:
        raise JangseongContractError(
            f"{scope} page {requested_page}: official title changed"
        )
    tables = soup.select("#content > table#board_list_table.list_table")
    if len(tables) != 1:
        raise JangseongContractError(
            f"{scope} page {requested_page}: course table changed"
        )
    table = tables[0]
    captions = table.find_all("caption", recursive=False)
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    expected_headers = (
        _LIST_HEADERS_NUMBERED if catalogue.numbered else _LIST_HEADERS_DIGITAL
    )
    if (
        len(captions) != 1
        or _clean(captions[0].get_text(" ", strip=True)) != catalogue.caption
        or headers != expected_headers
    ):
        raise JangseongContractError(
            f"{scope} page {requested_page}: table schema changed"
        )
    bodies = table.find_all("tbody", recursive=False)
    if len(bodies) != 1:
        raise JangseongContractError(
            f"{scope} page {requested_page}: table body changed"
        )
    raw_rows = bodies[0].find_all("tr", recursive=False)
    empty_cells = bodies[0].select("td.list_empty")
    empty = bool(empty_cells)
    if empty:
        if (
            len(raw_rows) != 1
            or len(empty_cells) != 1
            or _clean(empty_cells[0].get_text(" ", strip=True)) != "검색내역이 없습니다."
        ):
            raise JangseongContractError(
                f"{scope} page {requested_page}: empty sentinel changed"
            )
        rows: tuple[dict[str, Any], ...] = ()
    else:
        if not raw_rows or len(raw_rows) > JANGSEONG_PAGE_SIZE:
            raise JangseongContractError(
                f"{scope} page {requested_page}: page size changed"
            )
        rows = tuple(
            _parse_list_row(row, catalogue, requested_page, position)
            for position, row in enumerate(raw_rows, start=1)
        )
    pagination_max, _ = _pagination(
        soup, catalogue, requested_page, empty=empty
    )
    declared_total: Optional[int] = None
    if catalogue.numbered:
        if requested_page == 1 and rows:
            declared_total = int(rows[0]["source_row_number"])
        else:
            declared_total = expected_total
        source_pages = (
            math.ceil(declared_total / JANGSEONG_PAGE_SIZE)
            if declared_total is not None and declared_total > 0
            else 0
        )
        if rows and declared_total is None:
            raise JangseongContractError(
                f"{scope} page {requested_page}: total row number missing"
            )
        if rows:
            expected_numbers = tuple(
                range(
                    int(declared_total) - (requested_page - 1) * JANGSEONG_PAGE_SIZE,
                    int(declared_total)
                    - (requested_page - 1) * JANGSEONG_PAGE_SIZE
                    - len(rows),
                    -1,
                )
            )
            actual_numbers = tuple(int(row["source_row_number"]) for row in rows)
            if actual_numbers != expected_numbers:
                raise JangseongContractError(
                    f"{scope} page {requested_page}: row-number continuity changed"
                )
    else:
        declared_total = None
        source_pages = pagination_max if rows or expected_pages else 0
        if rows and source_pages < 1:
            source_pages = 1
    if expected_pages is not None and source_pages != expected_pages:
        raise JangseongContractError(
            f"{scope} page {requested_page}: declared page boundary changed"
        )
    if expected_total is not None and declared_total != expected_total:
        raise JangseongContractError(
            f"{scope} page {requested_page}: declared total changed"
        )
    if source_pages and pagination_max != source_pages:
        raise JangseongContractError(
            f"{scope} page {requested_page}: paginator endpoint changed"
        )
    if sentinel:
        if rows or not empty:
            raise JangseongContractError(
                f"{scope} page {requested_page}: immediate sentinel is not empty"
            )
    elif source_pages:
        expected_count = min(
            JANGSEONG_PAGE_SIZE,
            (declared_total or requested_page * JANGSEONG_PAGE_SIZE)
            - (requested_page - 1) * JANGSEONG_PAGE_SIZE,
        )
        if catalogue.numbered and len(rows) != expected_count:
            raise JangseongContractError(
                f"{scope} page {requested_page}: declared row count changed"
            )
        if not catalogue.numbered and requested_page < source_pages and len(rows) != JANGSEONG_PAGE_SIZE:
            raise JangseongContractError(
                f"{scope} page {requested_page}: intermediate page is short"
            )
    return _ListPage(
        scope=scope,
        requested_page=requested_page,
        declared_total=declared_total,
        source_pages=source_pages,
        rows=rows,
        empty_marker=empty,
    )


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            _clean(row.get("scope")),
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("period")),
            _clean(row.get("source_status")),
            _clean(row.get("list_venue")),
        )
        for row in rows
    )


def _detail_cells(
    soup: BeautifulSoup, catalogue: _Catalogue, identity: str
) -> Mapping[str, Any]:
    tables = soup.select("#content > table.show_form")
    if len(tables) != 1:
        raise JangseongContractError(f"course {identity}: detail table changed")
    table = tables[0]
    captions = table.find_all("caption", recursive=False)
    expected_caption = (
        "강좌예약 " + _DETAIL_CAPTION_SUFFIX
        if not catalogue.numbered
        else f"{catalogue.label} " + _DETAIL_CAPTION_SUFFIX
    )
    if (
        len(captions) != 1
        or _clean(captions[0].get_text(" ", strip=True)) != expected_caption
    ):
        raise JangseongContractError(f"course {identity}: detail caption changed")
    labels: list[str] = []
    cells: dict[str, Any] = {}
    for row in table.select("tbody > tr"):
        heads = row.find_all("th", recursive=False)
        values = row.find_all("td", recursive=False)
        if len(heads) != 1 or len(values) != 1:
            raise JangseongContractError(f"course {identity}: detail row changed")
        label = _clean(heads[0].get_text(" ", strip=True))
        if not label or label in cells:
            raise JangseongContractError(f"course {identity}: detail labels changed")
        labels.append(label)
        cells[label] = values[0]
    expected_labels = (
        _LIFELONG_DETAIL_LABELS if catalogue.numbered else _DIGITAL_DETAIL_LABELS
    )
    if tuple(labels) != expected_labels:
        raise JangseongContractError(f"course {identity}: detail labels changed")
    return cells


def _safe_detail_text(cell: Any, identity: str, field: str, *, maximum: int = 300) -> str:
    value = _clean(cell.get_text(" ", strip=True))
    if not value or len(value) > maximum:
        raise JangseongContractError(f"course {identity}: detail {field} changed")
    return value


def _detail_dates(cell: Any, identity: str, field: str) -> tuple[date, date]:
    values = _DATE_RE.findall(_safe_detail_text(cell, identity, field))
    if len(values) != 2:
        raise JangseongContractError(f"course {identity}: detail {field} range changed")
    return date.fromisoformat(values[0]), date.fromisoformat(values[1])


def _detail_capacity(value: str, identity: str) -> int:
    numbers = re.findall(r"\d[\d,]*", value)
    if len(numbers) != 1:
        raise JangseongContractError(f"course {identity}: detail capacity changed")
    result = int(numbers[0].replace(",", ""))
    if result < 1:
        raise JangseongContractError(f"course {identity}: detail capacity is invalid")
    return result


def _branch_code(venue: str) -> str:
    digest = hashlib.sha1(_normalized(venue).encode("utf-8")).hexdigest()[:12].upper()
    return f"JANGSEONG_{digest}"


def _parse_detail(soup: BeautifulSoup, listed: Mapping[str, Any]) -> dict[str, Any]:
    scope = _clean(listed.get("scope"))
    identity = _clean(listed.get("identity"))
    catalogue = _catalogue(scope)
    titles = soup.select("head > title")
    if len(titles) != 1 or _clean(titles[0].get_text(" ", strip=True)) != catalogue.title:
        raise JangseongContractError(f"course {identity}: official detail title changed")
    cells = _detail_cells(soup, catalogue, identity)
    if catalogue.numbered:
        title = _safe_detail_text(cells["강좌명"], identity, "title")
        target = _safe_detail_text(cells["교육대상"], identity, "target")
        schedule = "시간 별도 안내"
        target_evidence = "official_structured_detail"
        schedule_evidence = "official_structured_detail_field_absent"
        capacity_total = _detail_capacity(
            _safe_detail_text(cells["모집인원"], identity, "capacity"), identity
        )
        venue = _safe_detail_text(cells["교육장소"], identity, "venue")
    else:
        title = _safe_detail_text(cells["교육구분"], identity, "title")
        target = "대상 별도 안내"
        schedule = _safe_detail_text(cells["총시간"], identity, "schedule")
        target_evidence = "official_structured_detail_field_absent"
        schedule_evidence = "official_structured_detail_total_time"
        capacity_total = _detail_capacity(
            _safe_detail_text(cells["교육정원"], identity, "capacity"), identity
        )
        venue = _safe_detail_text(cells["교육장소"], identity, "venue")
    detail_apply = _detail_dates(cells["접수기간"], identity, "application period")
    detail_education = _detail_dates(cells["교육기간"], identity, "education period")
    if (
        title != _clean(listed.get("title"))
        or venue != _clean(listed.get("list_venue"))
        or capacity_total != int(listed.get("capacity_total") or 0)
        or detail_apply
        != (listed.get("apply_start_date"), listed.get("apply_end_date"))
        or detail_education != (listed.get("start"), listed.get("end"))
    ):
        raise JangseongContractError(
            f"course {identity}: list/detail safe fields mismatch"
        )
    visible_control = bool(listed.get("application_url"))
    if (listed.get("status") == "OPEN") != visible_control:
        raise JangseongContractError(
            f"course {identity}: application status/control mismatch"
        )
    return {
        "provider": JANGSEONG_PROVIDER,
        "provider_course_id": f"{JANGSEONG_PROVIDER}:{scope}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": venue,
        "branch_code": _branch_code(venue),
        "preserve_branch": True,
        "category": "교육·강좌",
        "program_type": "교육",
        "raw_url": _clean(listed.get("raw_url")),
        "application_url": _clean(listed.get("application_url")),
        "application_type": (
            "ONLINE_RESERVATION" if visible_control else "INFO_ONLY"
        ),
        "application_method": "온라인" if visible_control else "",
        "application_methods": ["온라인"] if visible_control else [],
        "reservation_available": visible_control,
        "status": _clean(listed.get("status")),
        "fee": "요금 별도 안내",
        "fee_amount": None,
        "period": _clean(listed.get("period")),
        "start_date": listed["start"].isoformat(),
        "end_date": listed["end"].isoformat(),
        "apply_period": _clean(listed.get("apply_period")),
        "apply_start": listed["apply_start_date"].isoformat(),
        "apply_end": listed["apply_end_date"].isoformat(),
        "schedule_raw": schedule,
        "capacity": f"{capacity_total}명",
        "capacity_current": int(listed.get("capacity_current") or 0),
        "capacity_total": capacity_total,
        "target": target,
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": JANGSEONG_PARSER,
        "municipality_code": JANGSEONG_MUNICIPALITY_CODE,
        "municipality_full_name": JANGSEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "scope": scope,
            "identity": identity,
            "source_page": int(listed.get("source_page") or 0),
            "source_position": int(listed.get("source_position") or 0),
            "source_row_number": listed.get("source_row_number"),
            "source_status": _clean(listed.get("source_status")),
            "source_education_status": _clean(
                listed.get("source_education_status")
            ),
            "source_period": _clean(listed.get("period")),
            "source_apply_period": _clean(listed.get("apply_period")),
            "source_capacity_current": int(listed.get("capacity_current") or 0),
            "source_capacity_total": int(listed.get("capacity_total") or 0),
            "source_application_control_present": bool(
                listed.get("source_application_control_present")
            ),
            "detail_target": target,
            "detail_schedule": schedule,
            "detail_venue": venue,
            "detail_capacity_total": capacity_total,
            "fee_evidence": "official_structured_detail_field_absent",
            "target_evidence": target_evidence,
            "schedule_evidence": schedule_evidence,
            "detail_verified": True,
            "visible_application_control_present": visible_control,
            "application_control_contract": (
                "identity_bound_official_login_route_not_fetched"
                if visible_control
                else "verified_no_control"
            ),
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
        "empty_scope_rechecks": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "identity_duplicate_count": 0,
        "raw_url_duplicate_count": 0,
        "semantic_duplicate_group_count": 0,
        "historical_semantic_duplicate_group_count": 0,
        "historical_unparseable_education_period_count": 0,
        "historical_reversed_application_period_count": 0,
        "historical_reversed_education_period_count": 0,
        "current_reversed_application_period_count": 0,
        "current_reversed_education_period_count": 0,
        "source_application_control_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": error,
        "municipality_code": JANGSEONG_MUNICIPALITY_CODE,
        "municipality_name": JANGSEONG_MUNICIPALITY_NAME,
        "canonical_url": JANGSEONG_CANONICAL_URL,
        "ownership_scope": JANGSEONG_OWNERSHIP_SCOPE,
        "candidate_audit": {
            key: dict(value) for key, value in JANGSEONG_CANDIDATE_AUDIT.items()
        },
        "discovery_audit": dict(JANGSEONG_DISCOVERY_AUDIT),
        "municipality_coverage": [JANGSEONG_MUNICIPALITY_CODE],
        "pii_fields_discarded": list(JANGSEONG_PII_FIELDS_DISCARDED),
        "pii_payload_persisted": False,
    }


def collect_jangseong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 120,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    max_workers: int = JANGSEONG_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future Jangseong education snapshot."""

    meta = _base_meta()
    if not is_jangseong_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Jangseong education catalogue owner"
        )
        return [], JANGSEONG_PARSER, meta
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
        return [], JANGSEONG_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], JANGSEONG_PARSER, meta
    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    workers = min(max_workers, JANGSEONG_MAX_WORKERS)
    meta["network_concurrency"] = workers

    def fetch_list(
        scope: str,
        page: int,
        *,
        total: Optional[int] = None,
        pages: Optional[int] = None,
        sentinel: bool = False,
    ) -> _ListPage:
        soup = _fetch_soup(
            jangseong_list_url(scope, page),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return _parse_list_page(
            soup,
            scope,
            page,
            expected_total=total,
            expected_pages=pages,
            sentinel=sentinel,
        )

    first_pages: dict[str, _ListPage] = {}
    first_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_list, scope, 1): scope
            for scope in JANGSEONG_CATALOGUES
        }
        for future in as_completed(futures):
            scope = futures[future]
            try:
                first_pages[scope] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                first_errors.append(
                    f"{scope} page 1: {type(exc).__name__}: {_clean(exc)}"
                )
    if first_errors or set(first_pages) != set(JANGSEONG_CATALOGUES):
        meta["configured_collection_error"] = "; ".join(
            first_errors or ["first pages missing"]
        )
        return [], JANGSEONG_PARSER, meta

    required = sum(
        first.source_pages + 3 if first.source_pages else 2
        for first in first_pages.values()
    )
    meta.update(
        {
            "required_list_requests": required,
            "declared_source_rows_by_scope": {
                scope: first.declared_total
                for scope, first in first_pages.items()
            },
            "declared_data_pages_by_scope": {
                scope: first.source_pages for scope, first in first_pages.items()
            },
            "declared_data_pages": sum(
                first.source_pages for first in first_pages.values()
            ),
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
        return [], JANGSEONG_PARSER, meta

    jobs: list[tuple[str, str, int, bool]] = []
    for scope, first in first_pages.items():
        if first.source_pages:
            jobs.extend(
                (scope, "data", page, False)
                for page in range(2, first.source_pages + 1)
            )
            jobs.extend(
                (
                    (scope, "sentinel", first.source_pages + 1, True),
                    (scope, "first_recheck", 1, False),
                    (scope, "last_recheck", first.source_pages, False),
                )
            )
        else:
            jobs.append((scope, "empty_recheck", 1, True))
    parsed_jobs: dict[tuple[str, str, int], _ListPage] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                fetch_list,
                scope,
                page,
                total=first_pages[scope].declared_total,
                pages=first_pages[scope].source_pages,
                sentinel=sentinel,
            ): (scope, kind, page)
            for scope, kind, page, sentinel in jobs
        }
        for future in as_completed(futures):
            scope, kind, page = futures[future]
            try:
                parsed_jobs[(scope, kind, page)] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(
                    f"{scope} {kind} page {page}: {type(exc).__name__}: {_clean(exc)}"
                )

    page_rows: dict[tuple[str, int], tuple[dict[str, Any], ...]] = {}
    per_scope_rows: dict[str, int] = {}
    per_scope_pages: dict[str, int] = {}
    sentinel_count = 0
    recheck_count = 0
    empty_recheck_count = 0
    for scope, first in first_pages.items():
        if not first.source_pages:
            recheck = parsed_jobs.get((scope, "empty_recheck", 1))
            if recheck is None or recheck.rows or not recheck.empty_marker:
                errors.append(f"{scope}: empty catalogue stability recheck failed")
            elif _page_signature(recheck.rows) != _page_signature(first.rows):
                errors.append(f"{scope}: empty catalogue boundary changed")
            else:
                recheck_count += 1
                empty_recheck_count += 1
            per_scope_rows[scope] = 0
            per_scope_pages[scope] = 0
            continue
        for page in range(1, first.source_pages + 1):
            parsed = first if page == 1 else parsed_jobs.get((scope, "data", page))
            if parsed is None:
                errors.append(f"{scope} data page {page}: response missing")
                continue
            page_rows[(scope, page)] = parsed.rows
        sentinel = parsed_jobs.get((scope, "sentinel", first.source_pages + 1))
        if sentinel is None or sentinel.rows or not sentinel.empty_marker:
            errors.append(f"{scope}: immediate post-last sentinel response failed")
        else:
            sentinel_count += 1
        first_recheck = parsed_jobs.get((scope, "first_recheck", 1))
        last_recheck = parsed_jobs.get(
            (scope, "last_recheck", first.source_pages)
        )
        if first_recheck is None or last_recheck is None:
            errors.append(f"{scope}: first/last stability recheck missing")
        else:
            recheck_count += 2
            if (
                _page_signature(first_recheck.rows) != _page_signature(first.rows)
                or _page_signature(last_recheck.rows)
                != _page_signature(page_rows.get((scope, first.source_pages), ()))
            ):
                errors.append(f"{scope}: first/last boundary changed")
        row_count = sum(
            len(page_rows.get((scope, page), ()))
            for page in range(1, first.source_pages + 1)
        )
        per_scope_rows[scope] = row_count
        per_scope_pages[scope] = sum(
            (scope, page) in page_rows
            for page in range(1, first.source_pages + 1)
        )
        if first.declared_total is not None and row_count != first.declared_total:
            errors.append(
                f"{scope}: complete source row count {row_count} != {first.declared_total}"
            )

    listed = [
        row
        for scope in JANGSEONG_CATALOGUES
        for page in range(1, first_pages[scope].source_pages + 1)
        for row in page_rows.get((scope, page), ())
    ]
    identity_keys = [
        (_clean(row.get("scope")), _clean(row.get("identity"))) for row in listed
    ]
    raw_urls = [_clean(row.get("raw_url")) for row in listed]
    identity_duplicates = len(identity_keys) - len(set(identity_keys))
    raw_url_duplicates = len(raw_urls) - len(set(raw_urls))
    if identity_duplicates:
        errors.append(f"{identity_duplicates} duplicate official scope identities")
    if raw_url_duplicates:
        errors.append(f"{raw_url_duplicates} duplicate canonical detail URLs")
    current_listed: list[dict[str, Any]] = []
    historical_listed: list[dict[str, Any]] = []
    for row in listed:
        end = row.get("end")
        education_status = _clean(row.get("source_education_status"))
        is_current = isinstance(end, date) and end >= cutoff
        if is_current and education_status != "교육중":
            errors.append(
                f"course {row['scope']}:{row['identity']}: current status/end mismatch"
            )
        if not is_current and education_status != "교육종료":
            errors.append(
                f"course {row['scope']}:{row['identity']}: expired status/end mismatch"
            )
        if row["status"] == "OPEN" and not (
            row["apply_start_date"] <= cutoff <= row["apply_end_date"]
        ):
            errors.append(
                f"course {row['scope']}:{row['identity']}: open status/application period mismatch"
            )
        (current_listed if is_current else historical_listed).append(row)
    current_reversed_apply = sum(
        row["apply_start_date"] > row["apply_end_date"] for row in current_listed
    )
    current_reversed_education = sum(
        row["start"] > row["end"] for row in current_listed
    )
    historical_reversed_apply = sum(
        row["apply_start_date"] > row["apply_end_date"]
        for row in historical_listed
    )
    historical_reversed_education = sum(
        isinstance(row.get("start"), date)
        and isinstance(row.get("end"), date)
        and row["start"] > row["end"]
        for row in historical_listed
    )
    historical_unparseable = sum(
        row.get("start") is None or row.get("end") is None
        for row in historical_listed
    )
    if current_reversed_apply:
        errors.append(f"{current_reversed_apply} current reversed application periods")
    if current_reversed_education:
        errors.append(f"{current_reversed_education} current reversed education periods")
    historical_semantic = Counter(
        (
            _clean(row.get("scope")),
            _normalized(row.get("title")),
            _clean(row.get("period")),
            _normalized(row.get("list_venue")),
        )
        for row in historical_listed
    )
    historical_semantic_groups = sum(
        value > 1 for value in historical_semantic.values()
    )
    expected_sentinels = sum(
        first.source_pages > 0 for first in first_pages.values()
    )
    expected_rechecks = sum(
        2 if first.source_pages else 1 for first in first_pages.values()
    )
    list_complete = bool(
        not errors
        and meta["list_requests"] == required
        and sentinel_count == expected_sentinels
        and recheck_count == expected_rechecks
        and len(listed) == sum(per_scope_rows.values())
    )
    meta.update(
        {
            "source_total": len(listed),
            "source_rows": len(listed),
            "source_rows_by_scope": per_scope_rows,
            "data_pages": sum(per_scope_pages.values()),
            "data_pages_by_scope": per_scope_pages,
            "sentinel_requests": sentinel_count,
            "empty_scope_rechecks": empty_recheck_count,
            "stability_rechecks": recheck_count,
            "current_source_count": len(current_listed),
            "current_scope_counts": dict(
                Counter(row["scope"] for row in current_listed)
            ),
            "expired_count": len(historical_listed),
            "identity_duplicate_count": identity_duplicates,
            "raw_url_duplicate_count": raw_url_duplicates,
            "historical_semantic_duplicate_group_count": historical_semantic_groups,
            "historical_unparseable_education_period_count": historical_unparseable,
            "historical_reversed_application_period_count": historical_reversed_apply,
            "historical_reversed_education_period_count": historical_reversed_education,
            "current_reversed_application_period_count": current_reversed_apply,
            "current_reversed_education_period_count": current_reversed_education,
            "source_application_control_count": sum(
                bool(row["source_application_control_present"]) for row in listed
            ),
            "source_status_counts": dict(
                Counter(row["source_status"] for row in listed)
            ),
            "current_normalized_status_counts": dict(
                Counter(row["status"] for row in current_listed)
            ),
            "pagination_detected": any(
                first.source_pages > 1 for first in first_pages.values()
            ),
            "pagination_complete": list_complete,
        }
    )
    if not list_complete:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], JANGSEONG_PARSER, meta
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
        return [], JANGSEONG_PARSER, meta

    meta["detail_attempts"] = len(current_listed)
    detailed: dict[tuple[str, str], dict[str, Any]] = {}
    detail_errors: list[str] = []

    def fetch_detail(
        listed_row: Mapping[str, Any],
    ) -> tuple[tuple[str, str], dict[str, Any]]:
        key = (
            _clean(listed_row.get("scope")),
            _clean(listed_row.get("identity")),
        )
        soup = _fetch_soup(
            _clean(listed_row.get("raw_url")),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return key, _parse_detail(soup, listed_row)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_detail, row): row for row in current_listed
        }
        for future in as_completed(futures):
            listed_row = futures[future]
            key = (
                _clean(listed_row.get("scope")),
                _clean(listed_row.get("identity")),
            )
            try:
                parsed_key, parsed = future.result()
                if parsed_key in detailed:
                    raise JangseongContractError(
                        "duplicate parsed detail scope identity"
                    )
                detailed[parsed_key] = parsed
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                detail_errors.append(
                    f"detail {key[0]}:{key[1]}: {type(exc).__name__}: {_clean(exc)}"
                )
    meta["detail_errors"] = len(detail_errors)
    errors.extend(detail_errors)
    details_complete = bool(
        not detail_errors
        and meta["detail_pages"] == len(current_listed)
        and len(detailed) == len(current_listed)
    )
    ordered = [
        detailed[(row["scope"], row["identity"])]
        for row in current_listed
        if (row["scope"], row["identity"]) in detailed
    ]
    semantic = Counter(
        (
            _normalized(row.get("title")),
            _clean(row.get("period")),
            _normalized(row.get("raw_fields", {}).get("detail_venue")),
        )
        for row in ordered
    )
    semantic_groups = sum(value > 1 for value in semantic.values())
    if semantic_groups:
        errors.append(f"{semantic_groups} current semantic duplicate groups")
    controls_complete = bool(
        details_complete
        and all(
            (row.get("status") == "OPEN")
            == bool(
                row.get("raw_fields", {}).get(
                    "visible_application_control_present"
                )
            )
            for row in ordered
        )
    )
    result: list[dict[str, Any]] = []
    if details_complete and controls_complete and not semantic_groups and not errors:
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
                    f"dedupe changed official identity cardinality "
                    f"{len(ordered)} to {len(result)}"
                )
                result = []
    snapshot_complete = bool(
        list_complete
        and details_complete
        and controls_complete
        and not semantic_groups
        and not errors
    )
    if not snapshot_complete:
        result = []
    meta.update(
        {
            "returned_count": len(result),
            "semantic_duplicate_group_count": semantic_groups,
            "branch_counts": dict(
                Counter(_clean(row.get("branch")) for row in result)
            ),
            "current_branch_names": sorted(
                {_clean(row.get("branch")) for row in result}
            ),
            "venue_counts": dict(
                Counter(
                    _clean(row.get("raw_fields", {}).get("detail_venue"))
                    for row in result
                )
            ),
            "status_counts": dict(
                Counter(_clean(row.get("status")) for row in result)
            ),
            "visible_public_application_control_count": sum(
                bool(
                    row.get("raw_fields", {}).get(
                        "visible_application_control_present"
                    )
                )
                for row in ordered
            ),
            "details_complete": details_complete,
            "application_controls_complete": controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "no_current_data": bool(snapshot_complete and not current_listed),
            "no_current_reason": (
                "the complete official Jangseong education catalogues have no "
                "current/future courses"
                if snapshot_complete and not current_listed
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, JANGSEONG_PARSER, meta


collect = collect_jangseong_education


__all__ = [
    "JANGSEONG_CANDIDATE_AUDIT",
    "JANGSEONG_CANDIDATE_ID",
    "JANGSEONG_CANONICAL_URL",
    "JANGSEONG_CATALOGUES",
    "JANGSEONG_DIGITAL_URL",
    "JANGSEONG_DISCOVERY_AUDIT",
    "JANGSEONG_LIBRARY_URL",
    "JANGSEONG_MUNICIPALITY_CODE",
    "JANGSEONG_MUNICIPALITY_NAME",
    "JANGSEONG_PARSER",
    "JANGSEONG_PROVIDER",
    "JANGSEONG_SCOPE_PATHS",
    "JANGSEONG_SCOPE_URLS",
    "collect",
    "collect_jangseong_education",
    "is_jangseong_candidate_alias",
    "is_jangseong_education_target",
    "is_target",
    "jangseong_application_url",
    "jangseong_detail_url",
    "jangseong_list_url",
]
