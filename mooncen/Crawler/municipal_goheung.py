"""Fail-closed collectors for Goheung-gun's official education owners.

The municipality audit found three disjoint structured owners.  The county
education site owns two course categories, the education-office lifelong
learning center owns four ``lecture.es`` catalogues plus a reading catalogue,
and Goheung County Library owns ``/ProgramJoin``.  They must not be merged by
URL host or scheduled twice through discovery aliases.

Every collector walks the complete history, requires the immediate empty page
after the last data page, and re-fetches page one before accepting a snapshot.
Only current/future course details are fetched.  Detail parsing is deliberately
allowlisted: instructor/contact/remarks/attachments/free-form descriptions and
masked applicant lists are never persisted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
import hashlib
import math
import re
import ssl
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


GOHEUNG_MUNICIPALITY_CODE = "1274000000"
GOHEUNG_MUNICIPALITY_NAME = "전남광주통합특별시 고흥군"

# Retain the already configured county provider.  Its provider suffix came
# from the original HTTP URL with a trailing slash, while the target URL was
# subsequently upgraded to HTTPS.  The discovery candidate without the slash
# is the same owner and must be disabled rather than scheduled separately.
GOHEUNG_COUNTY_PROVIDER = "MUNI_WWW_GOHEUNG_GO_KR_CEE514D6"
GOHEUNG_COUNTY_ALIAS_PROVIDER = "MUNI_WWW_GOHEUNG_GO_KR_20DDCA5A"
GOHEUNG_COUNTY_URL = "https://www.goheung.go.kr/education/"
GOHEUNG_COUNTY_HTTP_ALIAS_URL = "http:" "//www.goheung.go.kr/education/"
GOHEUNG_COUNTY_HOST = "www.goheung.go.kr"
GOHEUNG_COUNTY_BRANCH = "전남광주통합특별시 고흥군(여성가족과)"
GOHEUNG_COUNTY_PAGE_SIZE = 6

GOHEUNG_LIFELONG_PROVIDER = "MUNI_GHLIFE_JNE_GO_KR_0B6360AE"
GOHEUNG_LIFELONG_URL = "https://ghlife.jne.go.kr/menu.es?mid=c40401090000"
GOHEUNG_LIFELONG_HOST = "ghlife.jne.go.kr"
GOHEUNG_LIFELONG_BRANCH = "전남광주통합특별시교육청고흥평생교육관"
GOHEUNG_LIFELONG_LOGIN_PATH = "/login_search.es"
GOHEUNG_LIFELONG_LOGIN_SID = "c4"
GOHEUNG_LIFELONG_TLS_CIPHER = "AES256-GCM-SHA384"
GOHEUNG_LECTURE_PAGE_SIZE = 100
GOHEUNG_READING_PAGE_SIZE = 10

GOHEUNG_LIBRARY_URL = "https://www.ghlib.go.kr/ProgramJoin"
GOHEUNG_LIBRARY_PROVIDER = (
    "MUNI_WWW_GHLIB_GO_KR_"
    + hashlib.sha1(GOHEUNG_LIBRARY_URL.encode("utf-8")).hexdigest()[:8].upper()
)
GOHEUNG_LIBRARY_HOST = "www.ghlib.go.kr"
GOHEUNG_LIBRARY_BRANCH = "고흥군립도서관"
GOHEUNG_LIBRARY_PAGE_SIZE = 10

GOHEUNG_BUNCHEONG_URL = "https://buncheong.goheung.go.kr/site/buncheong/29"
GOHEUNG_FORESTTRIP_URL = (
    "https://www.foresttrip.go.kr/indvz/main.do?hmpgId=ID02030069"
)
GOHEUNG_EDUCATION_OFFICE_URL = "https://ghed.jne.go.kr/"
GOHEUNG_AGRICULTURE_URL = "https://www.goheung.go.kr/farm/"

GOHEUNG_COUNTY_PARSER = (
    "goheung_county_two_complete_categories+empty_sentinels+stable_page1+"
    "current_safe_detail+identity_bound_post_control+pii_allowlist"
)
GOHEUNG_LIFELONG_PARSER = (
    "goheung_lifelong_four_lecture_plus_reading_catalogues+empty_sentinels+"
    "stable_page1+current_safe_details+login_gate+pii_allowlist"
)
GOHEUNG_LIBRARY_PARSER = (
    "goheung_library_programjoin_all_pages+empty_sentinel+stable_page1+"
    "current_safe_detail+opening_day_status_boundary+"
    "identity_bound_apply_control+pii_allowlist"
)
GOHEUNG_PARSER = "goheung_three_disjoint_education_owners_dispatch"


@dataclass(frozen=True)
class GoheungCountySource:
    code: str
    page_id: str
    category: str
    menu: str

    @property
    def url(self) -> str:
        return goheung_county_list_url(self.code, 1)


GOHEUNG_COUNTY_SOURCES: tuple[GoheungCountySource, ...] = (
    GoheungCountySource("lifelong", "education34", "5", "평생학습프로그램"),
    GoheungCountySource("advanced", "education46", "6", "순천대 고흥첨단교육센터"),
)
_COUNTY_SOURCE_BY_CODE = {row.code: row for row in GOHEUNG_COUNTY_SOURCES}


@dataclass(frozen=True)
class GoheungLectureSource:
    code: str
    mid: str
    menu: str
    program_type: str

    @property
    def url(self) -> str:
        return goheung_lecture_list_url(self.code, 1)


GOHEUNG_LECTURE_SOURCES: tuple[GoheungLectureSource, ...] = (
    GoheungLectureSource("resident", "c40402010100", "주민 프로그램", "주민 평생학습"),
    GoheungLectureSource("student", "c40402010200", "학생 프로그램", "학생 평생학습"),
    GoheungLectureSource("experience", "c40402010300", "체험 프로그램", "체험 프로그램"),
    GoheungLectureSource("vacation", "c40402010400", "방학 프로그램", "방학 프로그램"),
)
_LECTURE_SOURCE_BY_CODE = {row.code: row for row in GOHEUNG_LECTURE_SOURCES}

GOHEUNG_READING_URL = (
    "https://ghlife.jne.go.kr/education.es?"
    "mid=c40208000000&eid=0128&educ_cg=&nPage=1"
)

GOHEUNG_LIBRARY_BRANCHES: Mapping[str, str] = {
    "중앙": "고흥군립중앙도서관",
    "남부": "고흥군립남부도서관",
    "북부": "고흥군립북부도서관",
    "과역": "과역작은도서관",
    "봉래": "봉래작은도서관",
}

GOHEUNG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "MUNI_IR_8F458F92163F": {
        "decision": "retain_candidate_provider_expand_to_five_catalogues",
        "provider": GOHEUNG_LIFELONG_PROVIDER,
        "url": GOHEUNG_LIFELONG_URL,
        "canonical_catalogues": tuple(
            f"https://{GOHEUNG_LIFELONG_HOST}/lecture.es?mid={source.mid}"
            for source in GOHEUNG_LECTURE_SOURCES
        ) + (GOHEUNG_READING_URL,),
        "owner": GOHEUNG_LIFELONG_PROVIDER,
    },
    "MUNI_IR_B8037A4195A6": {
        "decision": "disable_http_no_slash_alias_retain_existing_county_owner",
        "provider": GOHEUNG_COUNTY_ALIAS_PROVIDER,
        "url": GOHEUNG_COUNTY_HTTP_ALIAS_URL,
        "canonical_url": GOHEUNG_COUNTY_URL,
        "owner": GOHEUNG_COUNTY_PROVIDER,
    },
    "MUNI_IR_E0D61DBD8A04": {
        "decision": "exclude_accommodation_owner",
        "provider": "MUNI_WWW_FORESTTRIP_GO_KR_27623971",
        "url": GOHEUNG_FORESTTRIP_URL,
        "owner": "palyeongsan_forest_lodging",
    },
    "NEW_OFFICIAL_COUNTY_LIBRARY_OWNER": {
        "decision": "add_new_separate_structured_program_owner",
        "provider": GOHEUNG_LIBRARY_PROVIDER,
        "url": GOHEUNG_LIBRARY_URL,
        "canonical_url": GOHEUNG_LIBRARY_URL,
        "owner": GOHEUNG_LIBRARY_PROVIDER,
    },
    "BUNCHEONG_MUSEUM": {
        "decision": "keep_separate_museum_education_experience_owner",
        "provider": "SEPARATE_GOHEUNG_BUNCHEONG_MUSEUM",
        "url": GOHEUNG_BUNCHEONG_URL,
        "owner": "goheung_buncheong_museum",
    },
    "EDUCATION_SUPPORT_OFFICE": {
        "decision": "exclude_no_public_structured_course_catalogue",
        "provider": "SEPARATE_GOHED_JNE_GO_KR",
        "url": GOHEUNG_EDUCATION_OFFICE_URL,
        "owner": "goheung_education_support_office",
    },
    "AGRICULTURE_CENTER": {
        "decision": "keep_separate_editorial_agricultural_training_owner",
        "provider": "SEPARATE_GOHEUNG_AGRICULTURE",
        "url": GOHEUNG_AGRICULTURE_URL,
        "owner": "goheung_agricultural_extension",
    },
}

GOHEUNG_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    GOHEUNG_COUNTY_PROVIDER: {
        "decision": "retain_existing_owner_union_two_county_categories",
        "catalogues": tuple(
            f"https://{GOHEUNG_COUNTY_HOST}/education/pg/hmCourseMasterList.do?"
            + urlencode(
                (("pageId", source.page_id), ("ctgry", source.category), ("movePage", "1"))
            )
            for source in GOHEUNG_COUNTY_SOURCES
        ),
        "included_aliases": (GOHEUNG_COUNTY_ALIAS_PROVIDER,),
        "exact_current_branch": GOHEUNG_COUNTY_BRANCH,
    },
    GOHEUNG_LIFELONG_PROVIDER: {
        "decision": "retain_candidate_owner_union_four_lecture_and_reading",
        "catalogues": tuple(
            f"https://{GOHEUNG_LIFELONG_HOST}/lecture.es?mid={source.mid}"
            for source in GOHEUNG_LECTURE_SOURCES
        )
        + (GOHEUNG_READING_URL,),
        "exact_branch": GOHEUNG_LIFELONG_BRANCH,
    },
    GOHEUNG_LIBRARY_PROVIDER: {
        "decision": "new_separate_county_library_program_owner",
        "catalogues": (GOHEUNG_LIBRARY_URL,),
        "exact_branches": tuple(GOHEUNG_LIBRARY_BRANCHES.values()),
    },
}

GOHEUNG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "coverage_state": "review",
    "county_source_totals": {"lifelong": 121, "advanced": 28},
    "county_page_counts": {
        "lifelong": [6] * 20 + [1],
        "advanced": [6] * 4 + [4],
    },
    "county_current_or_future": 12,
    "county_current_status_counts": {
        "신청중": 7,
        "신청마감": 3,
        "접수마감": 2,
    },
    "county_current_identity_bound_controls": 7,
    "lifelong_lecture_totals": {
        "resident": 47,
        "student": 26,
        "experience": 6,
        "vacation": 10,
    },
    "lifelong_lecture_current_or_future": {
        "resident": 47,
        "student": 20,
        "experience": 0,
        "vacation": 10,
    },
    "lifelong_reading_total": 27,
    "lifelong_reading_page_counts": [10, 10, 7],
    "lifelong_reading_current_or_future": 1,
    "lifelong_total_current_or_future": 78,
    "lifelong_current_status_counts": {
        "접수전": 67,
        "신청하기": 6,
        "마감": 4,
        "대기자신청하기": 1,
    },
    "lifelong_current_identity_bound_controls": 7,
    "lifelong_cross_catalogue_identity_overlap": 0,
    "library_source_total": 483,
    "library_page_counts": [10] * 48 + [3],
    "library_current_or_future": 0,
    "library_all_status_counts": {"모집마감": 483},
    "conclusion": (
        "schedule three owners once each; fold the HTTP county alias into the "
        "existing county provider; keep museum, agriculture and lodging owners separate"
    ),
}

GOHEUNG_PII_FIELDS_DISCARDED = (
    "강사명/강사 소개",
    "교육문의전화/연락처",
    "상세소개/내용/비고/준비물",
    "첨부파일/이미지/강의계획서/교육일정표",
    "신청승인/대기 신청자 이름·계정·신청시각",
    "신청 폼과 개인 입력값",
)


class GoheungContractError(ValueError):
    """Raised when an audited Goheung source contract changes."""


class GoheungJneTlsAdapter(HTTPAdapter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.ssl_context = build_goheung_jne_tls_context()
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self.ssl_context
        super().init_poolmanager(*args, **kwargs)


def build_goheung_jne_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(GOHEUNG_LIFELONG_TLS_CIPHER)
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("verified TLS defaults unexpectedly unavailable")
    return context


_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})")
_DATETIME_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})"
    r"(?:\s+|T)(\d{1,2})\s*(?::|시)\s*(\d{1,2})(?:\s*분)?(?!\d)"
)
_IDENTITY_RE = re.compile(r"[1-9]\d{0,18}")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[-)]\s*\d{3,4}[-]\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _clean(value), flags=re.UNICODE).casefold()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _exact_https_url(value: Any, expected: str) -> bool:
    raw = _clean(value)
    if raw != expected:
        return False
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.fragment
    )


def is_goheung_county_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == GOHEUNG_COUNTY_PROVIDER
        and _exact_https_url(_target_value(target, "url"), GOHEUNG_COUNTY_URL)
    )


def is_goheung_lifelong_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == GOHEUNG_LIFELONG_PROVIDER
        and _exact_https_url(_target_value(target, "url"), GOHEUNG_LIFELONG_URL)
    )


def is_goheung_library_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == GOHEUNG_LIBRARY_PROVIDER
        and _exact_https_url(_target_value(target, "url"), GOHEUNG_LIBRARY_URL)
    )


def is_goheung_target(target: Any) -> bool:
    return (
        is_goheung_county_target(target)
        or is_goheung_lifelong_target(target)
        or is_goheung_library_target(target)
    )


is_target = is_goheung_target


def goheung_county_list_url(source_code: str, page: int = 1) -> str:
    source = _COUNTY_SOURCE_BY_CODE.get(_clean(source_code))
    if source is None or isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("unknown county source or invalid page")
    query = (("pageId", source.page_id), ("ctgry", source.category), ("movePage", str(page)))
    return (
        f"https://{GOHEUNG_COUNTY_HOST}/education/pg/hmCourseMasterList.do?"
        + urlencode(query)
    )


def goheung_county_detail_url(source_code: str, identity: Any) -> str:
    source = _COUNTY_SOURCE_BY_CODE.get(_clean(source_code))
    identity = _clean(identity)
    if source is None or _IDENTITY_RE.fullmatch(identity) is None:
        raise ValueError("unknown county source or invalid identity")
    query = (("sn", identity), ("pageId", source.page_id), ("ctgry", source.category))
    return (
        f"https://{GOHEUNG_COUNTY_HOST}/education/pg/hmCourseMasterView.do?"
        + urlencode(query)
    )


def goheung_lecture_list_url(source_code: str, page: int = 1) -> str:
    source = _LECTURE_SOURCE_BY_CODE.get(_clean(source_code))
    if source is None or isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("unknown lecture source or invalid page")
    query: list[tuple[str, str]] = [("mid", source.mid)]
    if page > 1:
        query.append(("nPage", str(page)))
    return f"https://{GOHEUNG_LIFELONG_HOST}/lecture.es?{urlencode(query)}"


def goheung_lecture_detail_url(source_code: str, identity: Any) -> str:
    source = _LECTURE_SOURCE_BY_CODE.get(_clean(source_code))
    identity = _clean(identity)
    if source is None or _IDENTITY_RE.fullmatch(identity) is None:
        raise ValueError("unknown lecture source or invalid identity")
    return f"https://{GOHEUNG_LIFELONG_HOST}/lecture.es?" + urlencode(
        (("mid", source.mid), ("act", "view"), ("el_seq", identity))
    )


def goheung_reading_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    return f"https://{GOHEUNG_LIFELONG_HOST}/education.es?" + urlencode(
        (("mid", "c40208000000"), ("eid", "0128"), ("educ_cg", ""), ("nPage", str(page)))
    )


def goheung_reading_detail_url(identity: Any) -> str:
    identity = _clean(identity)
    if _IDENTITY_RE.fullmatch(identity) is None:
        raise ValueError("invalid reading identity")
    return f"https://{GOHEUNG_LIFELONG_HOST}/education.es?" + urlencode(
        (
            ("mid", "c40208000000"),
            ("eid", "0128"),
            ("edu_seq", identity),
            ("educ_cg", ""),
            ("act", "view"),
        )
    )


def goheung_library_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    return f"https://{GOHEUNG_LIBRARY_HOST}/ProgramJoin/All/All/{page}"


def goheung_library_detail_url(page: int, identity: Any) -> str:
    identity = _clean(identity)
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
        or _IDENTITY_RE.fullmatch(identity) is None
    ):
        raise ValueError("invalid library page or identity")
    return f"{goheung_library_list_url(page)}/read/{identity}"


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return session


def _jne_session_factory() -> requests.Session:
    session = _default_session_factory()
    session.mount("https://", GoheungJneTlsAdapter())
    session.headers["Referer"] = GOHEUNG_LIFELONG_URL
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise GoheungContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise GoheungContractError("redirect response is not accepted")
    return response


def _payload_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, bytearray):
        payload = bytes(value)
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = getattr(value, "content", None)
        if payload is None:
            text = getattr(value, "text", None)
            payload = text.encode("utf-8") if isinstance(text, str) else None
    if not payload:
        raise GoheungContractError("empty HTTP response")
    if len(payload) > 5_000_000:
        raise GoheungContractError("HTTP response exceeds audited byte cap")
    return payload


def _coerce_soup(value: Any) -> BeautifulSoup:
    return BeautifulSoup(_payload_bytes(value), "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _Client:
    def __init__(
        self,
        *,
        timeout: int,
        fetcher: Fetcher,
        session_factory: SessionFactory,
        attempts: int = 3,
    ) -> None:
        self.timeout = timeout
        self.fetcher = fetcher
        self.session = session_factory()
        self.attempts = attempts
        self.requests = 0

    def html(self, url: str) -> BeautifulSoup:
        error: Optional[Exception] = None
        for _attempt in range(self.attempts):
            try:
                self.requests += 1
                return _coerce_soup(self.fetcher(self.session, url, self.timeout))
            except Exception as exc:
                error = exc
        assert error is not None
        raise error

    def close(self) -> None:
        _close_quietly(self.session)


def _one(nodes: list[Any], label: str) -> Any:
    if len(nodes) != 1:
        raise GoheungContractError(f"expected one {label}, found {len(nodes)}")
    return nodes[0]


def _date_range(value: Any, label: str, *, allow_single: bool = False) -> tuple[str, str]:
    matches = _DATE_RE.findall(_clean(value))
    if allow_single and len(matches) == 1:
        matches = [matches[0], matches[0]]
    if len(matches) != 2:
        raise GoheungContractError(f"{label} date range changed")
    result: list[str] = []
    for match in matches:
        try:
            parsed = date(*(int(part) for part in match))
        except ValueError as exc:
            raise GoheungContractError(f"{label} contains invalid date") from exc
        result.append(parsed.isoformat())
    if result[1] < result[0]:
        raise GoheungContractError(f"{label} ends before it starts")
    return result[0], result[1]


def _application_datetime_range(value: Any, label: str) -> tuple[str, str]:
    raw = _clean(value)
    matches = _DATETIME_RE.findall(raw)
    timezone = ZoneInfo("Asia/Seoul")
    if matches:
        if len(matches) != 2:
            raise GoheungContractError(f"{label} datetime range changed")
        try:
            start, end = (
                datetime(*(int(part) for part in match), tzinfo=timezone)
                for match in matches
            )
        except ValueError as exc:
            raise GoheungContractError(f"{label} contains invalid datetime") from exc
    else:
        start_date, end_date = _date_range(raw, label)
        start = datetime.combine(date.fromisoformat(start_date), time.min, tzinfo=timezone)
        end = datetime.combine(
            date.fromisoformat(end_date), time(23, 59, 59), tzinfo=timezone
        )
    if end < start:
        raise GoheungContractError(f"{label} datetime range reversed")
    return start.isoformat(), end.isoformat()


def _reference_datetime(value: Optional[date | datetime | str]) -> datetime:
    timezone = ZoneInfo("Asia/Seoul")
    if value is None:
        return datetime.now(timezone)
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=timezone)
            if value.tzinfo is None
            else value.astimezone(timezone)
        )
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone)
    try:
        parsed = datetime.fromisoformat(_clean(value))
    except ValueError as exc:
        raise GoheungContractError("today must be an ISO date or datetime") from exc
    return (
        parsed.replace(tzinfo=timezone)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone)
    )


def _today(value: Optional[date | datetime | str]) -> date:
    return _reference_datetime(value).date()


def _query_dict(url: str) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(url)
    query: dict[str, str] = {}
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in query:
            raise GoheungContractError("duplicate URL query parameter")
        query[key] = value
    return parsed, query


def _th_td_map(table: Any, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for th in table.find_all("th"):
        key = _clean(th.get_text(" ", strip=True))
        td = th.find_next_sibling("td")
        if not key or td is None:
            continue
        if key in result:
            raise GoheungContractError(f"{label} duplicate detail field {key}")
        result[key] = _clean(td.get_text(" ", strip=True))
    return result


def _branch_code(provider: str, branch: str) -> str:
    digest = hashlib.sha1(f"{provider}|{branch}".encode("utf-8")).hexdigest()[:12].upper()
    return f"{provider}:{digest}"[:100]


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


_SAFE_RAW_FIELDS = {
    "owner_scope",
    "source_catalogue",
    "source_sequence",
    "source_identity",
    "source_status",
    "list_status",
    "detail_status",
    "list_detail_status_transition",
    "list_schema_verified",
    "detail_schema_verified",
    "list_detail_verified",
    "capacity_verified",
    "capacity_snapshot_changed",
    "application_control_verified",
    "login_gate_verified",
    "pii_allowlist_verified",
    "fee_evidence",
    "venue_evidence",
}


def _walk(value: Any) -> Iterable[Any]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield key
            yield from _walk(nested)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for nested in value:
            yield from _walk(nested)
    else:
        yield value


def _validate_output(row: Mapping[str, Any]) -> None:
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        raise GoheungContractError("persisted raw-field allowlist changed")
    strings = [value for value in _walk(row) if isinstance(value, str)]
    if any(_PHONE_RE.search(value) or _EMAIL_RE.search(value) for value in strings):
        raise GoheungContractError("phone/email reached output")
    if row.get("description") != row.get("title"):
        raise GoheungContractError("description must contain title only")
    if bool(row.get("application_url")) != bool(row.get("reservation_available")):
        raise GoheungContractError("application URL/availability mismatch")
    required = (
        "target",
        "fee",
        "start_date",
        "end_date",
        "venue_name",
        "category",
        "schedule_raw",
    )
    missing = [key for key in required if not _clean(row.get(key))]
    if missing:
        raise GoheungContractError(
            f"required target/fee/date/place/category/time fields missing: {missing}"
        )


def _base_meta(owner: str) -> dict[str, Any]:
    return {
        "owner": owner,
        "pages": 0,
        "request_count": 0,
        "source_rows": 0,
        "current_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "required_list_requests": 0,
        "list_requests": 0,
        "list_rechecks": 0,
        "sentinel_pages": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "duplicate_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
    }


def _failure(owner: str, message: str, **updates: Any) -> dict[str, Any]:
    meta = _base_meta(owner)
    meta.update(updates)
    meta["configured_collection_error"] = message
    return meta


def _validated_limits(
    timeout: Any, max_pages: Any, detail_limit: Any, today: Any
) -> tuple[int, int, int, date]:
    if isinstance(max_pages, bool) or isinstance(detail_limit, bool):
        raise ValueError
    timeout_value = int(timeout)
    pages = int(max_pages)
    details = int(detail_limit)
    cutoff = _today(today)
    if timeout_value <= 0 or pages < 0 or details < 0:
        raise ValueError
    return timeout_value, pages, details, cutoff


_COUNTY_HEADERS = (
    "교육기간",
    "교육시간",
    "접수기간",
    "모집인원",
    "교육장소",
    "수강료",
)
_COUNTY_LIST_STATUSES = {
    "접수전",
    "신청중",
    "신청마감",
    "접수마감",
    "교육종료",
    "폐강",
}


def _county_page(
    source: GoheungCountySource, soup: BeautifulSoup, page: int
) -> tuple[list[dict[str, Any]], bool]:
    title = _one(soup.select("head > title"), f"county {source.code} title")
    if _clean(title.get_text(" ", strip=True)) != "수강신청 | 교육/일자리/청년":
        raise GoheungContractError(f"county {source.code} owner title changed")
    form = _one(soup.select("form#searchForm"), f"county {source.code} search form")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[name]")
    }
    if (
        _clean(form.get("method")).lower() != "post"
        or _clean(form.get("action")) != "/education/pg/hmCourseMasterList.do"
        or hidden.get("pageId") != source.page_id
        or hidden.get("ctgry") != source.category
        or hidden.get("movePage") != str(page)
        or hidden.get("boardId") != "BD_00018"
    ):
        raise GoheungContractError(f"county {source.code} pagination form changed")
    board = _one(soup.select("ul.board_list.board_type_d"), f"county {source.code} board")
    items = board.find_all("li", recursive=False)
    if not items:
        if board.find("a", href=re.compile(r"hmCourseMasterView")) is not None:
            raise GoheungContractError("county empty page contains a detail link")
        if _clean(board.get_text(" ", strip=True)) not in {
            "",
            "등록된 강좌가 없습니다.",
            "등록된 자료가 없습니다.",
        }:
            raise GoheungContractError("county empty page marker changed")
        return [], True
    rows: list[dict[str, Any]] = []
    for item in items:
        anchor = _one(
            item.select("h5 > a[href*='hmCourseMasterView.do']"),
            f"county {source.code} course link",
        )
        parsed, query = _query_dict(urljoin(source.url, _clean(anchor.get("href"))))
        if (
            parsed.scheme != "https"
            or parsed.netloc != GOHEUNG_COUNTY_HOST
            or parsed.path != "/education/pg/hmCourseMasterView.do"
            or set(query) != {"sn", "pageId", "ctgry"}
            or query.get("pageId") != source.page_id
            or query.get("ctgry") != source.category
            or _IDENTITY_RE.fullmatch(query.get("sn", "")) is None
        ):
            raise GoheungContractError("county detail identity URL changed")
        labels: list[str] = []
        fields: dict[str, str] = {}
        for dl in item.select("dl"):
            dt = dl.find("dt")
            dd = dl.find("dd")
            if dt is None or dd is None:
                raise GoheungContractError("county list field lost dt/dd")
            key = _clean(dt.get_text(" ", strip=True))
            if key in fields:
                raise GoheungContractError("county duplicate list field")
            labels.append(key)
            fields[key] = _clean(dd.get_text(" ", strip=True))
        if tuple(labels) != _COUNTY_HEADERS:
            raise GoheungContractError(
                f"county {source.code} list labels changed: {labels}"
            )
        start, end = _date_range(fields["교육기간"], "county operating")
        apply_start, apply_end = _date_range(fields["접수기간"], "county application")
        capacity_match = re.fullmatch(r"(\d+)명", fields["모집인원"])
        status_node = _one(item.select(".list_label"), "county list status")
        source_status = _clean(status_node.get_text(" ", strip=True))
        if source_status not in _COUNTY_LIST_STATUSES:
            raise GoheungContractError(f"county status changed: {source_status}")
        application = item.select(f"#appln_act_{query['sn']}")
        if source_status == "신청중":
            button = _one(application, "county open application control")
            if (
                _clean(button.get("data-sn")) != query["sn"]
                or _clean(button.get("data-ctgry")) != source.category
                or _clean(button.get("data-action")) != "I"
                or _clean(button.get("href")) != "javascript:void(0)"
                or _clean(button.get("onclick")) != f"fn_appln({query['sn']})"
            ):
                raise GoheungContractError("county list application control changed")
        elif application:
            raise GoheungContractError("closed county course has application control")
        rows.append(
            {
                "source_catalogue": source.code,
                "source_page": page,
                "identity": query["sn"],
                "title": _clean(anchor.get_text(" ", strip=True)),
                "start_date": start,
                "end_date": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule": fields["교육시간"],
                "venue": fields["교육장소"],
                "fee": fields["수강료"],
                "capacity_total": (
                    int(capacity_match.group(1)) if capacity_match else None
                ),
                "source_status": source_status,
                "raw_url": goheung_county_detail_url(source.code, query["sn"]),
            }
        )
    return rows, False


def _county_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.get("source_catalogue"),
            row.get("identity"),
            row.get("title"),
            row.get("start_date"),
            row.get("end_date"),
            row.get("source_status"),
        )
        for row in rows
    )


def _county_status(parent: Mapping[str, Any], cutoff: date) -> str:
    start = date.fromisoformat(_clean(parent.get("apply_start")))
    end = date.fromisoformat(_clean(parent.get("apply_end")))
    source = _clean(parent.get("source_status"))
    if cutoff < start:
        if source != "신청중":
            return "SCHEDULED"
        return "SCHEDULED"
    if start <= cutoff <= end and source == "신청중":
        return "OPEN"
    return "CLOSED"


def _county_detail(
    source: GoheungCountySource,
    parent: Mapping[str, Any],
    soup: BeautifulSoup,
    target: Any,
    cutoff: date,
) -> dict[str, Any]:
    identity = _clean(parent.get("identity"))
    title = _one(soup.select("head > title"), f"county {identity} title")
    if _clean(title.get_text(" ", strip=True)) != "수강신청 | 교육/일자리/청년":
        raise GoheungContractError(f"county {identity} detail owner changed")
    form = _one(soup.select("form#syForm"), f"county {identity} detail form")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[type='hidden'][name]")
    }
    if (
        _clean(form.get("method")).lower() != "post"
        or hidden != {"progId": "eduProgram", "pageAction": "I", "sn": identity}
    ):
        raise GoheungContractError(f"county {identity} identity form changed")
    detail_title = _one(form.select(".bd_view_top > h4"), f"county {identity} heading")
    if _normalized(detail_title.get_text(" ", strip=True)) != _normalized(parent.get("title")):
        raise GoheungContractError(f"county {identity} title mismatch")
    values: dict[str, str] = {}
    for dl in form.select(".bd_view_cont > dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if dt is None or dd is None:
            raise GoheungContractError(f"county {identity} detail field changed")
        key = _clean(dt.get_text(" ", strip=True))
        if key in values:
            raise GoheungContractError(f"county {identity} duplicate detail field")
        values[key] = _clean(dd.get_text(" ", strip=True))
    required = {
        "기관",
        "강좌분류",
        "교육기간",
        "접수기간",
        "교육주기",
        "교육정원",
        "대기자정원",
        "교육시간",
        "수강료",
        "교육장소",
        "접수방법",
        "교육대상",
        "접수상태",
    }
    if not required <= set(values):
        raise GoheungContractError(f"county {identity} required detail fields changed")
    start, end = _date_range(values["교육기간"], f"county {identity} operating")
    apply_start, apply_end = _date_range(
        values["접수기간"], f"county {identity} application"
    )
    if (start, end) != (parent.get("start_date"), parent.get("end_date")):
        raise GoheungContractError(f"county {identity} operating mismatch")
    if (apply_start, apply_end) != (parent.get("apply_start"), parent.get("apply_end")):
        raise GoheungContractError(f"county {identity} application mismatch")
    if _normalized(values["교육시간"]) != _normalized(parent.get("schedule")):
        raise GoheungContractError(f"county {identity} schedule mismatch")
    if _normalized(values["교육장소"]) != _normalized(parent.get("venue")):
        raise GoheungContractError(f"county {identity} venue mismatch")
    if _normalized(values["수강료"]) != _normalized(parent.get("fee")):
        raise GoheungContractError(f"county {identity} fee mismatch")
    list_status = _clean(parent.get("source_status"))
    detail_status = values["접수상태"]
    closed_aliases = {"신청마감", "접수마감"}
    ended_transition = (
        list_status in closed_aliases
        and detail_status == "교육종료"
        and date.fromisoformat(_clean(parent.get("end_date"))) <= cutoff
    )
    if detail_status != list_status and not (
        (detail_status in closed_aliases and list_status in closed_aliases)
        or ended_transition
    ):
        raise GoheungContractError(f"county {identity} status mismatch")
    total_match = re.fullmatch(r"(\d+)명", values["교육정원"])
    wait_match = re.fullmatch(r"(\d+)명", values["대기자정원"])
    if total_match is None or wait_match is None:
        raise GoheungContractError(f"county {identity} detail capacity changed")
    total = int(total_match.group(1))
    wait_total = int(wait_match.group(1))
    if total != parent.get("capacity_total"):
        raise GoheungContractError(f"county {identity} capacity mismatch")
    button = soup.select(f"#appln_act_{identity}")
    open_control = detail_status == "신청중"
    if open_control:
        node = _one(button, f"county {identity} application button")
        if (
            _clean(node.get("data-sn")) != identity
            or _clean(node.get("data-ctgry")) != source.category
            or _clean(node.get("data-action")) != "I"
            or _clean(node.get("onclick")) != f"fn_appln({identity})"
        ):
            raise GoheungContractError(f"county {identity} application button changed")
        scripts = "\n".join(node.get_text("\n", strip=False) for node in soup.find_all("script"))
        expected_path = f"/education/pg/HmCourseAppln.do?pageId={source.page_id}&ctgry={source.category}"
        if expected_path not in scripts:
            raise GoheungContractError(f"county {identity} POST destination changed")
    elif button:
        raise GoheungContractError(f"county {identity} closed button appeared")
    status = _county_status(parent, cutoff)
    if status == "OPEN" and not open_control:
        raise GoheungContractError(f"county {identity} open status lacks control")
    branch = values["기관"]
    venue = values["교육장소"]
    if not branch or not venue:
        raise GoheungContractError(f"county {identity} institution/venue empty")
    available = status == "OPEN" and open_control
    row = {
        "provider": GOHEUNG_COUNTY_PROVIDER,
        "provider_course_id": f"{GOHEUNG_COUNTY_PROVIDER}:{source.code}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(parent.get("title")),
        "branch": branch,
        "branch_code": _branch_code(GOHEUNG_COUNTY_PROVIDER, branch),
        "provider_organizer": branch,
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "status": status,
        "category": "교육",
        "program_type": values["강좌분류"] or source.menu,
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "official_paginated_list+verified_detail",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "schedule_raw": _clean(f"{values['교육주기']} {values['교육시간']}"),
        "target": values["교육대상"],
        "fee": values["수강료"],
        "room": venue,
        "venue": venue,
        "venue_name": venue,
        "description": _clean(parent.get("title")),
        "capacity": total,
        "capacity_total": total,
        "capacity_remaining": None,
        "waitlist_total": wait_total,
        "application_method": values["접수방법"],
        "application_methods": [values["접수방법"]],
        "reservation_available": available,
        "application_url": _clean(parent.get("raw_url")) if available else "",
        "application_type": "ONLINE_RESERVATION" if available else "",
        "raw_url": _clean(parent.get("raw_url")),
        "source_url": _clean(_target_value(target, "url")),
        "raw_fields": {
            "owner_scope": "goheung_county",
            "source_catalogue": source.code,
            "source_identity": identity,
            "source_status": detail_status,
            "list_status": list_status,
            "detail_status": detail_status,
            "list_detail_status_transition": ended_transition,
            "list_schema_verified": True,
            "detail_schema_verified": True,
            "list_detail_verified": True,
            "capacity_verified": True,
            "application_control_verified": True,
            "pii_allowlist_verified": True,
        },
    }
    _validate_output(row)
    return row


def collect_goheung_county_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 200,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    owner = "goheung_county"
    if not is_goheung_county_target(target):
        return [], GOHEUNG_COUNTY_PARSER, _failure(owner, "target does not match retained county owner")
    try:
        timeout_value, allowed_pages, allowed_details, cutoff = _validated_limits(
            timeout, max_pages, detail_limit, today
        )
    except (TypeError, ValueError):
        return [], GOHEUNG_COUNTY_PARSER, _failure(owner, "max_pages/detail_limit/timeout/today invalid")
    client = _Client(
        timeout=timeout_value,
        fetcher=fetcher or _default_fetcher,
        session_factory=session_factory or _default_session_factory,
    )
    errors: list[str] = []
    source_cap = False
    list_requests = rechecks = sentinels = 0
    source_rows: dict[str, list[dict[str, Any]]] = {}
    page_counts: dict[str, list[int]] = {}
    first_pages: dict[str, list[dict[str, Any]]] = {}
    try:
        for source in GOHEUNG_COUNTY_SOURCES:
            rows: list[dict[str, Any]] = []
            counts: list[int] = []
            page = 1
            while True:
                if list_requests >= allowed_pages:
                    source_cap = True
                    raise GoheungContractError("max_pages reached before county sentinel")
                parsed, empty = _county_page(source, client.html(goheung_county_list_url(source.code, page)), page)
                list_requests += 1
                if page == 1:
                    first_pages[source.code] = parsed
                if empty:
                    sentinels += 1
                    break
                counts.append(len(parsed))
                rows.extend(parsed)
                page += 1
            if not counts or any(count != GOHEUNG_COUNTY_PAGE_SIZE for count in counts[:-1]):
                raise GoheungContractError(f"county {source.code} page-size continuity changed")
            if not 1 <= counts[-1] <= GOHEUNG_COUNTY_PAGE_SIZE:
                raise GoheungContractError(f"county {source.code} final page size invalid")
            if list_requests >= allowed_pages:
                source_cap = True
                raise GoheungContractError("max_pages reached before county recheck")
            checked, checked_empty = _county_page(source, client.html(source.url), 1)
            list_requests += 1
            rechecks += 1
            if checked_empty or _county_signature(checked) != _county_signature(first_pages[source.code]):
                raise GoheungContractError(f"county {source.code} page-one recheck changed")
            ids = [row["identity"] for row in rows]
            if len(ids) != len(set(ids)):
                raise GoheungContractError(f"county {source.code} duplicate identities")
            source_rows[source.code] = rows
            page_counts[source.code] = counts
    except Exception as exc:
        errors.append(f"completeness: {type(exc).__name__}: {exc}")
    all_rows = [row for source in GOHEUNG_COUNTY_SOURCES for row in source_rows.get(source.code, [])]
    owners: dict[str, set[str]] = {}
    for row in all_rows:
        owners.setdefault(row["identity"], set()).add(row["source_catalogue"])
    overlap = sum(len(value) - 1 for value in owners.values() if len(value) > 1)
    if overlap:
        errors.append(f"{overlap} county identities overlap across categories")
    current = [row for row in all_rows if date.fromisoformat(row["end_date"]) >= cutoff]
    expired = len(all_rows) - len(current)
    for row in current:
        if row.get("capacity_total") is None or not row.get("venue"):
            errors.append(f"county current course {row['identity']} fields incomplete")
    if len(current) > allowed_details:
        source_cap = True
        errors.append(f"detail_limit allows {allowed_details} of {len(current)}")
    detailed: list[dict[str, Any]] = []
    detail_attempts = 0
    if not errors:
        for parent in current:
            detail_attempts += 1
            source = _COUNTY_SOURCE_BY_CODE[parent["source_catalogue"]]
            try:
                detailed.append(
                    _county_detail(source, parent, client.html(parent["raw_url"]), target, cutoff)
                )
            except Exception as exc:
                errors.append(f"course {parent['identity']} detail: {type(exc).__name__}: {exc}")
                break
    result: list[dict[str, Any]] = []
    if not errors:
        result = list((dedupe_rows or _dedupe_default)(detailed))
        if len(result) != len(detailed):
            errors.append("dedupe changed complete county row count")
            result = []
    result.sort(key=lambda row: (row["start_date"], row["title"], row["provider_course_id"]))
    duplicates = len(detailed) - len({row["provider_course_id"] for row in detailed})
    if duplicates and not errors:
        errors.append(f"{duplicates} duplicate output identities")
        result = []
    required_list = sum(len(value) + 2 for value in page_counts.values())
    snapshot = not errors
    pagination = bool(
        snapshot
        and len(source_rows) == len(GOHEUNG_COUNTY_SOURCES)
        and list_requests == required_list
        and sentinels == len(GOHEUNG_COUNTY_SOURCES)
        and rechecks == len(GOHEUNG_COUNTY_SOURCES)
    )
    details_complete = bool(snapshot and detail_attempts == len(current) and len(detailed) == len(current))
    controls = bool(
        details_complete
        and all(row["raw_fields"].get("application_control_verified") for row in detailed)
    )
    meta = _base_meta(owner)
    meta.update(
        {
            "pages": client.requests,
            "request_count": client.requests,
            "source_totals": {key: len(value) for key, value in source_rows.items()},
            "source_page_counts": page_counts,
            "source_status_counts": {
                key: dict(Counter(row["source_status"] for row in value))
                for key, value in source_rows.items()
            },
            "source_rows": len(all_rows),
            "current_count": len(current),
            "expired_count": expired,
            "returned_count": len(result),
            "required_list_requests": required_list,
            "list_requests": list_requests,
            "list_rechecks": rechecks,
            "sentinel_pages": sentinels,
            "detail_attempts": detail_attempts,
            "detail_pages": len(detailed),
            "cross_source_duplicate_count": overlap,
            "duplicate_count": duplicates,
            "source_cap_reached": source_cap,
            "pagination_complete": pagination,
            "partition_union_complete": bool(snapshot and len(source_rows) == 2 and not overlap),
            "details_complete": details_complete,
            "application_controls_complete": controls,
            "snapshot_complete": snapshot,
            "full_snapshot_validated": bool(snapshot and pagination and details_complete and controls),
            "no_current_data": bool(snapshot and not current),
            "no_current_reason": "all rows in both county categories ended" if snapshot and not current else "",
            "exact_current_branches": dict(Counter(row["branch"] for row in detailed)),
            "configured_collection_error": "; ".join(errors),
        }
    )
    client.close()
    return ([] if errors else result), GOHEUNG_COUNTY_PARSER, meta


_LECTURE_HEADERS = (
    "연번",
    "강좌명",
    "대상",
    "운영기간",
    "인터넷접수",
    "신청 / 정원 (대기인원)",
    "상태",
)
_READING_HEADERS = (
    "번호",
    "강좌명",
    "인터넷접수",
    "수강기간",
    "신청 / 정원 (신청/대기)",
    "비고",
)
_JNE_STATUS_CLASS = {
    "접수전": "w_wait",
    "신청하기": "w_app",
    "대기자신청하기": "w_tmp",
    "마감": "w_close",
}


def _jne_owner_title(soup: BeautifulSoup, expected: str, label: str) -> None:
    title = _one(soup.select("head > title"), f"{label} title")
    if _clean(title.get_text(" ", strip=True)) != expected:
        raise GoheungContractError(f"{label} owner/title changed")


def _parse_four_counts(cell: Any, label: str) -> tuple[int, int, int, int]:
    values = [
        _clean(node.get_text(" ", strip=True))
        for node in cell.select("span.edu-state01, span.edu-state02")
    ]
    if len(values) != 4 or any(not value.isdigit() for value in values):
        raise GoheungContractError(f"{label} capacity markup changed")
    current, total, wait_current, wait_total = (int(value) for value in values)
    return current, total, wait_current, wait_total


def _validate_jne_status_cell(cell: Any, identity: str) -> tuple[str, str]:
    spans = cell.select("span.w_wait, span.w_app, span.w_tmp, span.w_close")
    node = _one(spans, f"JNE course {identity} status")
    status = _clean(node.get_text(" ", strip=True))
    classes = node.get("class") or []
    css = next((value for value in classes if value in set(_JNE_STATUS_CLASS.values())), "")
    expected_classes = (
        {"w_tmp", "w_app"}
        if status == "대기자신청하기"
        else {_JNE_STATUS_CLASS.get(status, "")}
    )
    if css not in expected_classes:
        raise GoheungContractError(f"JNE course {identity} status class changed")
    parent = node.find_parent("a")
    if status in {"신청하기", "대기자신청하기"}:
        if (
            parent is None
            or _clean(parent.get("href")) != "#"
            or "checkLogin()" not in _clean(parent.get("onclick"))
        ):
            raise GoheungContractError(f"JNE course {identity} login control changed")
    elif parent is not None:
        raise GoheungContractError(f"JNE course {identity} closed/scheduled link appeared")
    return status, css


def _lecture_page(
    source: GoheungLectureSource, soup: BeautifulSoup, page: int
) -> tuple[list[dict[str, Any]], bool]:
    expected = (
        f"글쓰기 | {source.menu} | 수강 신청 | 평생학습 : "
        f"{GOHEUNG_LIFELONG_BRANCH}"
    )
    _jne_owner_title(soup, expected, f"lecture {source.code}")
    form = _one(soup.select("form[name='srhForm']"), f"lecture {source.code} form")
    values = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("[name]")
    }
    if (
        _clean(form.get("method")).lower() != "post"
        or _clean(form.get("action")) != f"/lecture.es?mid={source.mid}"
        or values.get("actionUrl") != "/lecture.es"
        or values.get("mid") != source.mid
        or values.get("act") != "list"
        or values.get("b_list") != str(GOHEUNG_LECTURE_PAGE_SIZE)
        or values.get("nPage") != ("" if page == 1 else str(page))
    ):
        raise GoheungContractError(f"lecture {source.code} pagination form changed")
    table = _one(soup.select("table.tstyle_list"), f"lecture {source.code} table")
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _LECTURE_HEADERS:
        raise GoheungContractError(f"lecture {source.code} headers changed: {headers}")
    body_rows = table.select("tbody > tr")
    parsed: list[dict[str, Any]] = []
    empty = False
    for tr in body_rows:
        cells = tr.find_all("td", recursive=False)
        if len(cells) == 1:
            if _clean(cells[0].get_text(" ", strip=True)) != "등록된 자료가 존재하지 않습니다.":
                raise GoheungContractError("lecture empty marker changed")
            empty = True
            continue
        if len(cells) != 7 or empty:
            raise GoheungContractError("lecture row width changed")
        sequence_text = _clean(cells[0].get_text(" ", strip=True))
        if not sequence_text.isdigit():
            raise GoheungContractError("lecture sequence changed")
        anchor = _one(cells[1].select("a[href]"), "lecture detail link")
        parsed_url, query = _query_dict(urljoin(source.url, _clean(anchor.get("href"))))
        if (
            parsed_url.scheme != "https"
            or parsed_url.netloc != GOHEUNG_LIFELONG_HOST
            or parsed_url.path != "/lecture.es"
            or set(query) != {"mid", "act", "el_seq", "nPage"}
            or query.get("mid") != source.mid
            or query.get("act") != "view"
            or query.get("nPage") not in {"", str(page)}
            or _IDENTITY_RE.fullmatch(query.get("el_seq", "")) is None
        ):
            raise GoheungContractError("lecture detail URL changed")
        raw_period = _clean(cells[3].get_text(" ", strip=True))
        raw_application = _clean(cells[4].get_text(" ", strip=True))
        start, end = _date_range(raw_period, "lecture operating")
        apply_start, apply_end = _date_range(raw_application, "lecture application")
        apply_start_at, apply_end_at = _application_datetime_range(
            raw_application, "lecture application"
        )
        current, total, wait_current, wait_total = _parse_four_counts(
            cells[5], "lecture"
        )
        status, css = _validate_jne_status_cell(cells[6], query["el_seq"])
        parsed.append(
            {
                "source_catalogue": source.code,
                "source_sequence": int(sequence_text),
                "identity": query["el_seq"],
                "title": _clean(anchor.get_text(" ", strip=True)),
                "target": _clean(cells[2].get_text(" ", strip=True)),
                "raw_period": raw_period,
                "raw_application": raw_application,
                "start_date": start,
                "end_date": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "apply_start_at": apply_start_at,
                "apply_end_at": apply_end_at,
                "capacity_current": current,
                "capacity_total": total,
                "waitlist_current": wait_current,
                "waitlist_total": wait_total,
                "source_status": status,
                "status_class": css,
                "raw_url": goheung_lecture_detail_url(source.code, query["el_seq"]),
            }
        )
    if empty and parsed:
        raise GoheungContractError("lecture page mixes data and empty marker")
    if not body_rows:
        raise GoheungContractError("lecture table body disappeared")
    return parsed, empty


def _reading_page(soup: BeautifulSoup, page: int) -> tuple[list[dict[str, Any]], bool]:
    expected = f"독서프로그램 신청 | 독서문화진흥 : {GOHEUNG_LIFELONG_BRANCH}"
    _jne_owner_title(soup, expected, "reading")
    form = _one(soup.select("form[name='srhForm']"), "reading form")
    values = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("[name]")
    }
    if (
        _clean(form.get("method")).lower() != "post"
        or _clean(form.get("action")) != "/education.es?mid=c40208000000&eid=0128"
        or values.get("mid") != "c40208000000"
        or values.get("eid") != "0128"
        or values.get("act") != "list"
        or values.get("nPage") != str(page)
    ):
        raise GoheungContractError("reading pagination form changed")
    table = _one(soup.select("table.tstyle_list"), "reading table")
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _READING_HEADERS:
        raise GoheungContractError(f"reading headers changed: {headers}")
    body_rows = table.select("tbody > tr")
    parsed: list[dict[str, Any]] = []
    empty = False
    for tr in body_rows:
        cells = tr.find_all("td", recursive=False)
        if len(cells) == 1:
            if _clean(cells[0].get_text(" ", strip=True)) != "결과 없음":
                raise GoheungContractError("reading empty marker changed")
            empty = True
            continue
        if len(cells) != 6 or empty:
            raise GoheungContractError("reading row width changed")
        sequence_text = _clean(cells[0].get_text(" ", strip=True))
        if not sequence_text.isdigit():
            raise GoheungContractError("reading sequence changed")
        anchor = _one(cells[1].select("a.subject[href]"), "reading detail link")
        parsed_url, query = _query_dict(urljoin(GOHEUNG_READING_URL, _clean(anchor.get("href"))))
        if (
            parsed_url.scheme != "https"
            or parsed_url.netloc != GOHEUNG_LIFELONG_HOST
            or parsed_url.path != "/education.es"
            or set(query) != {"mid", "eid", "edu_seq", "educ_cg", "act"}
            or query.get("mid") != "c40208000000"
            or query.get("eid") != "0128"
            or query.get("educ_cg") != ""
            or query.get("act") != "view"
            or _IDENTITY_RE.fullmatch(query.get("edu_seq", "")) is None
        ):
            raise GoheungContractError("reading detail URL changed")
        raw_application = _clean(cells[2].get_text(" ", strip=True))
        apply_start, apply_end = _date_range(raw_application, "reading application")
        apply_start_at, apply_end_at = _application_datetime_range(
            raw_application, "reading application"
        )
        start, end = _date_range(cells[3].get_text(" ", strip=True), "reading operating")
        current, total, wait_current, wait_total = _parse_four_counts(cells[4], "reading")
        status_node = _one(
            cells[5].select("span.w_wait, span.w_app, span.w_tmp, span.w_close"),
            "reading status",
        )
        status = _clean(status_node.get_text(" ", strip=True))
        classes = status_node.get("class") or []
        css = next((value for value in classes if value in set(_JNE_STATUS_CLASS.values())), "")
        if _JNE_STATUS_CLASS.get(status) != css:
            raise GoheungContractError("reading status/class changed")
        parent_link = status_node.find_parent("a")
        if status in {"신청하기", "대기자신청하기"}:
            if parent_link is None or "checkLogin()" not in _clean(parent_link.get("onclick")):
                raise GoheungContractError("reading login control changed")
        elif parent_link is not None:
            raise GoheungContractError("reading closed/scheduled link appeared")
        parsed.append(
            {
                "source_catalogue": "reading",
                "source_sequence": int(sequence_text),
                "identity": query["edu_seq"],
                "title": _clean(anchor.get_text(" ", strip=True)),
                "start_date": start,
                "end_date": end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "apply_start_at": apply_start_at,
                "apply_end_at": apply_end_at,
                "capacity_current": current,
                "capacity_total": total,
                "waitlist_current": wait_current,
                "waitlist_total": wait_total,
                "source_status": status,
                "status_class": css,
                "raw_url": goheung_reading_detail_url(query["edu_seq"]),
            }
        )
    if empty and parsed:
        raise GoheungContractError("reading page mixes data and empty marker")
    if not body_rows:
        raise GoheungContractError("reading table body disappeared")
    return parsed, empty


def _jne_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.get("source_catalogue"),
            row.get("source_sequence"),
            row.get("identity"),
            row.get("title"),
            row.get("start_date"),
            row.get("end_date"),
            row.get("apply_start_at"),
            row.get("apply_end_at"),
            row.get("source_status"),
        )
        for row in rows
    )


def _jne_status(parent: Mapping[str, Any], cutoff_at: datetime) -> str:
    try:
        apply_start_at = datetime.fromisoformat(_clean(parent.get("apply_start_at")))
        apply_end_at = datetime.fromisoformat(_clean(parent.get("apply_end_at")))
    except ValueError as exc:
        raise GoheungContractError("JNE application datetime missing or invalid") from exc
    if apply_start_at.tzinfo is None or apply_end_at.tzinfo is None:
        raise GoheungContractError("JNE application datetime lacks timezone")
    timezone = ZoneInfo("Asia/Seoul")
    apply_start_at = apply_start_at.astimezone(timezone)
    apply_end_at = apply_end_at.astimezone(timezone)
    cutoff_at = cutoff_at.astimezone(timezone)
    source = _clean(parent.get("source_status"))
    if source == "접수전":
        if cutoff_at >= apply_start_at:
            raise GoheungContractError("scheduled JNE course reached application datetime")
        return "SCHEDULED"
    if source in {"신청하기", "대기자신청하기"}:
        if not apply_start_at <= cutoff_at <= apply_end_at:
            raise GoheungContractError("open JNE course outside application period")
        return "OPEN"
    if source == "마감":
        return "CLOSED"
    raise GoheungContractError(f"unknown JNE status {source}")


def _validate_jne_login(soup: BeautifulSoup, identity: str, *, require_control: bool) -> None:
    login = f"{GOHEUNG_LIFELONG_LOGIN_PATH}?sid={GOHEUNG_LIFELONG_LOGIN_SID}"
    if not soup.select(f"a[href='{login}']"):
        raise GoheungContractError(f"JNE course {identity} owner login path changed")
    if require_control:
        scripts = "\n".join(node.get_text("\n", strip=False) for node in soup.find_all("script"))
        if "checkLogin" not in scripts or login not in scripts:
            raise GoheungContractError(f"JNE course {identity} login gate changed")


def _detail_capacity(value: str, label: str) -> tuple[int, int]:
    numbers = [int(number) for number in re.findall(r"\d+", _clean(value))]
    if len(numbers) < 2:
        raise GoheungContractError(f"{label} capacity changed")
    return numbers[0], numbers[1]


def _lecture_detail(
    source: GoheungLectureSource,
    parent: Mapping[str, Any],
    soup: BeautifulSoup,
    target: Any,
    cutoff_at: datetime,
) -> dict[str, Any]:
    identity = _clean(parent.get("identity"))
    expected = (
        f"글쓰기 | {source.menu} | 수강 신청 | 평생학습 : "
        f"{GOHEUNG_LIFELONG_BRANCH}"
    )
    _jne_owner_title(soup, expected, f"lecture detail {identity}")
    table = _one(soup.select("table.tstyle_write"), f"lecture {identity} detail table")
    values = _th_td_map(table, f"lecture {identity}")
    required = {
        "강좌명",
        "대상",
        "신청기간",
        "운영기간",
        "강의 시간",
        "회차",
        "강의 요일",
        "교육장소",
        "모집인원",
        "신청자",
        "신청방법",
        "접수상태",
    }
    if not required <= set(values):
        raise GoheungContractError(f"lecture {identity} required fields changed")
    form = _one(soup.select("form[name='insForm']"), f"lecture {identity} form")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[type='hidden'][name]")
    }
    if (
        _clean(form.get("method")).lower() != "post"
        or _clean(form.get("action")) != "/lecture.es&act=ins"
        or hidden != {"actionUrl": "/lecture.es", "nPage": "", "act": "list"}
    ):
        raise GoheungContractError(f"lecture {identity} form changed")
    if _normalized(values["강좌명"]) != _normalized(parent.get("title")):
        raise GoheungContractError(f"lecture {identity} title mismatch")
    if _normalized(values["대상"]) != _normalized(parent.get("target")):
        raise GoheungContractError(f"lecture {identity} target mismatch")
    start, end = _date_range(values["운영기간"], f"lecture {identity} operating")
    apply_start, apply_end = _date_range(values["신청기간"], f"lecture {identity} application")
    apply_start_at, apply_end_at = _application_datetime_range(
        values["신청기간"], f"lecture {identity} application"
    )
    if (start, end) != (parent.get("start_date"), parent.get("end_date")):
        raise GoheungContractError(f"lecture {identity} operating mismatch")
    if (apply_start, apply_end) != (parent.get("apply_start"), parent.get("apply_end")):
        raise GoheungContractError(f"lecture {identity} application mismatch")
    if (apply_start_at, apply_end_at) != (
        parent.get("apply_start_at"),
        parent.get("apply_end_at"),
    ):
        raise GoheungContractError(f"lecture {identity} application datetime mismatch")
    total, wait_total = _detail_capacity(values["모집인원"], "lecture total")
    current, wait_current = _detail_capacity(values["신청자"], "lecture current")
    if (total, wait_total) != (
        parent.get("capacity_total"),
        parent.get("waitlist_total"),
    ):
        raise GoheungContractError(f"lecture {identity} capacity limits mismatch")
    capacity_snapshot_changed = (current, wait_current) != (
        parent.get("capacity_current"),
        parent.get("waitlist_current"),
    )
    if values["신청방법"] != "인터넷":
        raise GoheungContractError(f"lecture {identity} application method changed")
    status_cell = None
    for th in table.find_all("th"):
        if _clean(th.get_text(" ", strip=True)) == "접수상태":
            status_cell = th.find_next_sibling("td")
            break
    if status_cell is None:
        raise GoheungContractError(f"lecture {identity} status cell missing")
    source_status, css = _validate_jne_status_cell(status_cell, identity)
    css_matches = css == parent.get("status_class") or (
        source_status == "대기자신청하기"
        and parent.get("status_class") == "w_tmp"
        and css == "w_app"
    )
    if source_status != parent.get("source_status") or not css_matches:
        raise GoheungContractError(f"lecture {identity} status mismatch")
    status = _jne_status(parent, cutoff_at)
    _validate_jne_login(soup, identity, require_control=(status == "OPEN"))
    venue = values["교육장소"]
    if not venue:
        raise GoheungContractError(f"lecture {identity} venue empty")
    schedule = _clean(f"{values['강의 요일']} {values['강의 시간']}")
    available = status == "OPEN"
    row = {
        "provider": GOHEUNG_LIFELONG_PROVIDER,
        "provider_course_id": f"{GOHEUNG_LIFELONG_PROVIDER}:{source.code}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(parent.get("title")),
        "branch": GOHEUNG_LIFELONG_BRANCH,
        "branch_code": _branch_code(GOHEUNG_LIFELONG_PROVIDER, GOHEUNG_LIFELONG_BRANCH),
        "provider_organizer": GOHEUNG_LIFELONG_BRANCH,
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "status": status,
        "category": "교육",
        "program_type": source.program_type,
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "official_paginated_list+verified_detail",
        "source_group": "municipal_reservation",
        "operator_type": "교육청/평생교육관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "schedule_raw": schedule,
        "target": _clean(parent.get("target")),
        "fee": "요금 별도 안내",
        "room": venue,
        "venue": venue,
        "venue_name": venue,
        "description": _clean(parent.get("title")),
        "capacity": total,
        "capacity_current": current,
        "capacity_total": total,
        "capacity_remaining": max(0, total - current),
        "waitlist_current": wait_current,
        "waitlist_total": wait_total,
        "application_method": "온라인 수강신청",
        "application_methods": ["온라인"],
        "reservation_available": available,
        "application_url": _clean(parent.get("raw_url")) if available else "",
        "application_type": "ONLINE_RESERVATION" if available else "",
        "raw_url": _clean(parent.get("raw_url")),
        "source_url": _clean(_target_value(target, "url")),
        "raw_fields": {
            "owner_scope": "goheung_lifelong",
            "source_catalogue": source.code,
            "source_sequence": parent.get("source_sequence"),
            "source_identity": identity,
            "source_status": source_status,
            "list_schema_verified": True,
            "detail_schema_verified": True,
            "list_detail_verified": True,
            "capacity_verified": True,
            "capacity_snapshot_changed": capacity_snapshot_changed,
            "application_control_verified": True,
            "login_gate_verified": True,
            "pii_allowlist_verified": True,
            "fee_evidence": "official_list_and_detail_omit_fee",
            "venue_evidence": "official_detail_value",
        },
    }
    _validate_output(row)
    return row


def _reading_detail(
    parent: Mapping[str, Any], soup: BeautifulSoup, target: Any, cutoff_at: datetime
) -> dict[str, Any]:
    identity = _clean(parent.get("identity"))
    expected = (
        f"{_clean(parent.get('title'))} | 독서프로그램 신청 | 독서문화진흥 : "
        f"{GOHEUNG_LIFELONG_BRANCH}"
    )
    _jne_owner_title(soup, expected, f"reading detail {identity}")
    table = _one(soup.select("table.tstyle_view"), f"reading {identity} detail table")
    values = _th_td_map(table, f"reading {identity}")
    period_key = "수강기간" if "수강기간" in values else "수강일" if "수강일" in values else ""
    required = {
        "강좌명",
        "대상",
        "인터넷 접수기간",
        "수강시간",
        "수강요일",
        "수강인원",
        "신청자",
        "교육장소",
        "비고",
    }
    if not period_key or not required <= set(values):
        raise GoheungContractError(f"reading {identity} required fields changed")
    form = _one(soup.select("form[name='vewForm']"), f"reading {identity} form")
    if (
        _clean(form.get("method")).lower() != "post"
        or _clean(form.get("action")) != "/education.es?mid=c40208000000"
    ):
        raise GoheungContractError(f"reading {identity} form changed")
    if _normalized(values["강좌명"]) != _normalized(parent.get("title")):
        raise GoheungContractError(f"reading {identity} title mismatch")
    start, end = _date_range(values[period_key], f"reading {identity} operating", allow_single=True)
    apply_start, apply_end = _date_range(
        values["인터넷 접수기간"], f"reading {identity} application"
    )
    apply_start_at, apply_end_at = _application_datetime_range(
        values["인터넷 접수기간"], f"reading {identity} application"
    )
    if (start, end) != (parent.get("start_date"), parent.get("end_date")):
        raise GoheungContractError(f"reading {identity} operating mismatch")
    if (apply_start, apply_end) != (parent.get("apply_start"), parent.get("apply_end")):
        raise GoheungContractError(f"reading {identity} application mismatch")
    if (apply_start_at, apply_end_at) != (
        parent.get("apply_start_at"),
        parent.get("apply_end_at"),
    ):
        raise GoheungContractError(f"reading {identity} application datetime mismatch")
    total, wait_total = _detail_capacity(values["수강인원"], "reading total")
    current, wait_current = _detail_capacity(values["신청자"], "reading current")
    if (total, wait_total) != (
        parent.get("capacity_total"),
        parent.get("waitlist_total"),
    ):
        raise GoheungContractError(f"reading {identity} capacity limits mismatch")
    capacity_snapshot_changed = (current, wait_current) != (
        parent.get("capacity_current"),
        parent.get("waitlist_current"),
    )
    if values["비고"] != parent.get("source_status"):
        raise GoheungContractError(f"reading {identity} status mismatch")
    status = _jne_status(parent, cutoff_at)
    _validate_jne_login(soup, identity, require_control=(status == "OPEN"))
    status_nodes = soup.select("span.w_wait, span.w_app, span.w_tmp, span.w_close")
    if status == "OPEN":
        if not any(
            _clean(node.get_text(" ", strip=True)) == parent.get("source_status")
            and node.find_parent("a") is not None
            and "checkLogin()" in _clean(node.find_parent("a").get("onclick"))
            for node in status_nodes
        ):
            raise GoheungContractError(f"reading {identity} open control missing")
    schedule = _clean(f"{values['수강요일']} {values['수강시간']}")
    available = status == "OPEN"
    raw_venue = values["교육장소"]
    venue = raw_venue or GOHEUNG_LIFELONG_BRANCH
    row = {
        "provider": GOHEUNG_LIFELONG_PROVIDER,
        "provider_course_id": f"{GOHEUNG_LIFELONG_PROVIDER}:reading:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(parent.get("title")),
        "branch": GOHEUNG_LIFELONG_BRANCH,
        "branch_code": _branch_code(GOHEUNG_LIFELONG_PROVIDER, GOHEUNG_LIFELONG_BRANCH),
        "provider_organizer": GOHEUNG_LIFELONG_BRANCH,
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "status": status,
        "category": "교육",
        "program_type": "독서문화 프로그램",
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "official_paginated_list+verified_detail",
        "source_group": "municipal_reservation",
        "operator_type": "교육청/평생교육관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "schedule_raw": schedule,
        "target": values["대상"],
        "fee": "요금 별도 안내",
        "room": venue,
        "venue": venue,
        "venue_name": venue,
        "description": _clean(parent.get("title")),
        "capacity": total,
        "capacity_current": current,
        "capacity_total": total,
        "capacity_remaining": max(0, total - current),
        "waitlist_current": wait_current,
        "waitlist_total": wait_total,
        "application_method": "온라인 수강신청",
        "application_methods": ["온라인"],
        "reservation_available": available,
        "application_url": _clean(parent.get("raw_url")) if available else "",
        "application_type": "ONLINE_RESERVATION" if available else "",
        "raw_url": _clean(parent.get("raw_url")),
        "source_url": _clean(_target_value(target, "url")),
        "raw_fields": {
            "owner_scope": "goheung_lifelong",
            "source_catalogue": "reading",
            "source_sequence": parent.get("source_sequence"),
            "source_identity": identity,
            "source_status": values["비고"],
            "list_schema_verified": True,
            "detail_schema_verified": True,
            "list_detail_verified": True,
            "capacity_verified": True,
            "capacity_snapshot_changed": capacity_snapshot_changed,
            "application_control_verified": True,
            "login_gate_verified": True,
            "pii_allowlist_verified": True,
            "fee_evidence": "official_list_and_detail_omit_fee",
            "venue_evidence": (
                "official_detail_value"
                if raw_venue
                else "official_detail_omits_venue_owner_fallback"
            ),
        },
    }
    _validate_output(row)
    return row


def collect_goheung_lifelong_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 200,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    owner = "goheung_lifelong"
    if not is_goheung_lifelong_target(target):
        return [], GOHEUNG_LIFELONG_PARSER, _failure(owner, "target does not match retained lifelong owner")
    try:
        timeout_value, allowed_pages, allowed_details, cutoff = _validated_limits(
            timeout, max_pages, detail_limit, today
        )
        cutoff_at = _reference_datetime(today)
    except (TypeError, ValueError):
        return [], GOHEUNG_LIFELONG_PARSER, _failure(owner, "max_pages/detail_limit/timeout/today invalid")
    client = _Client(
        timeout=timeout_value,
        fetcher=fetcher or _default_fetcher,
        session_factory=session_factory or _jne_session_factory,
    )
    errors: list[str] = []
    source_cap = False
    list_requests = rechecks = sentinels = 0
    source_rows: dict[str, list[dict[str, Any]]] = {}
    page_counts: dict[str, list[int]] = {}
    try:
        for source in GOHEUNG_LECTURE_SOURCES:
            if list_requests >= allowed_pages:
                source_cap = True
                raise GoheungContractError("max_pages reached before lecture first page")
            first, first_empty = _lecture_page(source, client.html(source.url), 1)
            list_requests += 1
            total = first[0]["source_sequence"] if first else 0
            data_pages = max(1, math.ceil(total / GOHEUNG_LECTURE_PAGE_SIZE))
            rows: list[dict[str, Any]] = []
            counts: list[int] = []
            for page in range(1, data_pages + 1):
                if page == 1:
                    parsed, empty = first, first_empty
                else:
                    if list_requests >= allowed_pages:
                        source_cap = True
                        raise GoheungContractError("max_pages reached in lecture data")
                    parsed, empty = _lecture_page(source, client.html(goheung_lecture_list_url(source.code, page)), page)
                    list_requests += 1
                expected = min(GOHEUNG_LECTURE_PAGE_SIZE, max(0, total - (page - 1) * GOHEUNG_LECTURE_PAGE_SIZE))
                if empty != (expected == 0) or len(parsed) != expected:
                    raise GoheungContractError(f"lecture {source.code} page {page} expected {expected}")
                counts.append(len(parsed))
                rows.extend(parsed)
            if list_requests >= allowed_pages:
                source_cap = True
                raise GoheungContractError("max_pages reached before lecture sentinel")
            sentinel_rows, sentinel_empty = _lecture_page(
                source,
                client.html(goheung_lecture_list_url(source.code, data_pages + 1)),
                data_pages + 1,
            )
            list_requests += 1
            if sentinel_rows or not sentinel_empty:
                raise GoheungContractError(f"lecture {source.code} sentinel not empty")
            sentinels += 1
            if list_requests >= allowed_pages:
                source_cap = True
                raise GoheungContractError("max_pages reached before lecture recheck")
            checked, checked_empty = _lecture_page(source, client.html(source.url), 1)
            list_requests += 1
            rechecks += 1
            if checked_empty != first_empty or _jne_signature(checked) != _jne_signature(first):
                raise GoheungContractError(f"lecture {source.code} page-one recheck changed")
            numbers = [row["source_sequence"] for row in rows]
            identities = [row["identity"] for row in rows]
            if numbers != list(range(total, 0, -1)) or len(rows) != total:
                raise GoheungContractError(f"lecture {source.code} numbering incomplete")
            if len(identities) != len(set(identities)):
                raise GoheungContractError(f"lecture {source.code} duplicate identities")
            source_rows[source.code] = rows
            page_counts[source.code] = counts
        # Reading uses a ten-row pager and is the fifth catalogue of this owner.
        if list_requests >= allowed_pages:
            source_cap = True
            raise GoheungContractError("max_pages reached before reading first page")
        first, first_empty = _reading_page(client.html(goheung_reading_list_url(1)), 1)
        list_requests += 1
        total = first[0]["source_sequence"] if first else 0
        data_pages = max(1, math.ceil(total / GOHEUNG_READING_PAGE_SIZE))
        reading: list[dict[str, Any]] = []
        counts: list[int] = []
        for page in range(1, data_pages + 1):
            if page == 1:
                parsed, empty = first, first_empty
            else:
                if list_requests >= allowed_pages:
                    source_cap = True
                    raise GoheungContractError("max_pages reached in reading data")
                parsed, empty = _reading_page(client.html(goheung_reading_list_url(page)), page)
                list_requests += 1
            expected = min(GOHEUNG_READING_PAGE_SIZE, max(0, total - (page - 1) * GOHEUNG_READING_PAGE_SIZE))
            if empty != (expected == 0) or len(parsed) != expected:
                raise GoheungContractError(f"reading page {page} expected {expected}")
            counts.append(len(parsed))
            reading.extend(parsed)
        if list_requests >= allowed_pages:
            source_cap = True
            raise GoheungContractError("max_pages reached before reading sentinel")
        sentinel_rows, sentinel_empty = _reading_page(
            client.html(goheung_reading_list_url(data_pages + 1)), data_pages + 1
        )
        list_requests += 1
        if sentinel_rows or not sentinel_empty:
            raise GoheungContractError("reading sentinel not empty")
        sentinels += 1
        if list_requests >= allowed_pages:
            source_cap = True
            raise GoheungContractError("max_pages reached before reading recheck")
        checked, checked_empty = _reading_page(client.html(goheung_reading_list_url(1)), 1)
        list_requests += 1
        rechecks += 1
        if checked_empty != first_empty or _jne_signature(checked) != _jne_signature(first):
            raise GoheungContractError("reading page-one recheck changed")
        numbers = [row["source_sequence"] for row in reading]
        identities = [row["identity"] for row in reading]
        if numbers != list(range(total, 0, -1)) or len(reading) != total:
            raise GoheungContractError("reading numbering incomplete")
        if len(identities) != len(set(identities)):
            raise GoheungContractError("reading duplicate identities")
        source_rows["reading"] = reading
        page_counts["reading"] = counts
    except Exception as exc:
        errors.append(f"completeness: {type(exc).__name__}: {exc}")
    ordered_codes = [source.code for source in GOHEUNG_LECTURE_SOURCES] + ["reading"]
    all_rows = [row for code in ordered_codes for row in source_rows.get(code, [])]
    identity_sources: dict[str, set[str]] = {}
    for row in all_rows:
        identity_sources.setdefault(row["identity"], set()).add(row["source_catalogue"])
    overlap = sum(len(scopes) - 1 for scopes in identity_sources.values() if len(scopes) > 1)
    if overlap:
        errors.append(f"{overlap} identities overlap across lifelong catalogues")
    current: list[dict[str, Any]] = []
    expired = 0
    for row in all_rows:
        if date.fromisoformat(row["end_date"]) < cutoff:
            expired += 1
        else:
            try:
                row["status"] = _jne_status(row, cutoff_at)
                current.append(row)
            except Exception as exc:
                errors.append(f"course {row['identity']} status: {exc}")
    if len(current) > allowed_details:
        source_cap = True
        errors.append(f"detail_limit allows {allowed_details} of {len(current)}")
    detailed: list[dict[str, Any]] = []
    attempts = 0
    if not errors:
        for parent in current:
            attempts += 1
            try:
                if parent["source_catalogue"] == "reading":
                    row = _reading_detail(
                        parent, client.html(parent["raw_url"]), target, cutoff_at
                    )
                else:
                    source = _LECTURE_SOURCE_BY_CODE[parent["source_catalogue"]]
                    row = _lecture_detail(
                        source, parent, client.html(parent["raw_url"]), target, cutoff_at
                    )
                detailed.append(row)
            except Exception as exc:
                errors.append(f"course {parent['identity']} detail: {type(exc).__name__}: {exc}")
                break
    result: list[dict[str, Any]] = []
    if not errors:
        result = list((dedupe_rows or _dedupe_default)(detailed))
        if len(result) != len(detailed):
            errors.append("dedupe changed complete lifelong row count")
            result = []
    result.sort(key=lambda row: (row["start_date"], row["title"], row["provider_course_id"]))
    duplicates = len(detailed) - len({row["provider_course_id"] for row in detailed})
    if duplicates and not errors:
        errors.append(f"{duplicates} duplicate output identities")
        result = []
    required_list = sum(len(value) + 2 for value in page_counts.values())
    snapshot = not errors
    pagination = bool(
        snapshot
        and len(source_rows) == 5
        and list_requests == required_list
        and sentinels == 5
        and rechecks == 5
    )
    details_complete = bool(snapshot and attempts == len(current) and len(detailed) == len(current))
    controls = bool(
        details_complete
        and all(
            row["raw_fields"].get("application_control_verified")
            and row["raw_fields"].get("login_gate_verified")
            for row in detailed
        )
    )
    meta = _base_meta(owner)
    meta.update(
        {
            "pages": client.requests,
            "request_count": client.requests,
            "source_totals": {key: len(value) for key, value in source_rows.items()},
            "source_page_counts": page_counts,
            "source_current_counts": {
                key: sum(date.fromisoformat(row["end_date"]) >= cutoff for row in value)
                for key, value in source_rows.items()
            },
            "source_status_counts": {
                key: dict(Counter(row["source_status"] for row in value))
                for key, value in source_rows.items()
            },
            "source_rows": len(all_rows),
            "current_count": len(current),
            "expired_count": expired,
            "returned_count": len(result),
            "required_list_requests": required_list,
            "list_requests": list_requests,
            "list_rechecks": rechecks,
            "sentinel_pages": sentinels,
            "detail_attempts": attempts,
            "detail_pages": len(detailed),
            "cross_source_duplicate_count": overlap,
            "duplicate_count": duplicates,
            "source_cap_reached": source_cap,
            "pagination_complete": pagination,
            "partition_union_complete": bool(snapshot and len(source_rows) == 5 and not overlap),
            "details_complete": details_complete,
            "application_controls_complete": controls,
            "snapshot_complete": snapshot,
            "full_snapshot_validated": bool(snapshot and pagination and details_complete and controls),
            "no_current_data": bool(snapshot and not current),
            "no_current_reason": "all rows in five lifelong catalogues ended" if snapshot and not current else "",
            "exact_branch_name": GOHEUNG_LIFELONG_BRANCH,
            "configured_collection_error": "; ".join(errors),
        }
    )
    client.close()
    return ([] if errors else result), GOHEUNG_LIFELONG_PARSER, meta


_LIBRARY_HEADERS = ("번호", "강좌정보", "강좌대상/인원", "현재접수현황", "상태")
_LIBRARY_STATUSES = {"모집전", "모집중", "모집마감"}


def _library_page(soup: BeautifulSoup, page: int) -> tuple[list[dict[str, Any]], bool]:
    title = _one(soup.select("head > title"), "library title")
    if _clean(title.get_text(" ", strip=True)) != "프로그램 신청 < 문화마당 - 고흥군립도서관":
        raise GoheungContractError("library owner title changed")
    form = _one(
        soup.select("form[method='post'][action='/ProgramJoin/All/All']"),
        "library search form",
    )
    names = [_clean(node.get("name")) for node in form.select("input[name]")]
    if "csrf_token" not in names or "query" not in names:
        raise GoheungContractError("library search form changed")
    table = _one(soup.select("table"), "library program table")
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _LIBRARY_HEADERS:
        raise GoheungContractError(f"library headers changed: {headers}")
    rows: list[dict[str, Any]] = []
    empty = False
    for tr in table.select("tbody > tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) == 1:
            if _clean(cells[0].get_text(" ", strip=True)) != "등록된 프로그램이 없습니다.":
                raise GoheungContractError("library empty marker changed")
            empty = True
            continue
        if len(cells) != 5 or empty:
            raise GoheungContractError("library row width changed")
        sequence_text = _clean(cells[0].get_text(" ", strip=True))
        if not sequence_text.isdigit():
            raise GoheungContractError("library sequence changed")
        program = cells[1]
        category_node = _one(program.select("span.label"), "library category")
        anchor = _one(program.select("a.title[href]"), "library detail link")
        parsed = urlparse(urljoin(goheung_library_list_url(page), _clean(anchor.get("href"))))
        expected_prefix = f"/ProgramJoin/All/All/{page}/read/"
        identity = parsed.path.removeprefix(expected_prefix)
        if (
            parsed.scheme != "https"
            or parsed.netloc != GOHEUNG_LIBRARY_HOST
            or not parsed.path.startswith(expected_prefix)
            or "/" in identity
            or _IDENTITY_RE.fullmatch(identity) is None
            or parsed.query
            or parsed.fragment
        ):
            raise GoheungContractError("library detail identity URL changed")
        start, end = _date_range(
            _clean(_one(program.select("p.desc"), "library period").get_text(" ", strip=True)),
            "library operating",
        )
        target_capacity = _clean(cells[2].get_text(" ", strip=True))
        target_match = re.fullmatch(r"(.+?)\s+(\d+)명\s*\(대기\s*:\s*(\d+)명\)", target_capacity)
        current_text = _clean(cells[3].get_text(" ", strip=True))
        if target_match is None:
            if current_text != "오프라인 접수" or not target_capacity:
                raise GoheungContractError(f"library course {identity} target/capacity changed")
            target = target_capacity
            capacity_current = capacity_total = wait_current = wait_total = None
            source_method = "OFFLINE"
        else:
            current_match = re.fullmatch(
                r"(\d+)명 신청(?:\s*\(대기\s*:\s*(\d+)명\))?"
                r"(?:\s*접수 인원 가득참)?",
                current_text,
            )
            if current_match is None:
                raise GoheungContractError(f"library course {identity} current count changed")
            target = _clean(target_match.group(1))
            capacity_current = int(current_match.group(1))
            capacity_total = int(target_match.group(2))
            wait_current = int(current_match.group(2) or 0)
            wait_total = int(target_match.group(3))
            source_method = "ONLINE"
        status_node = _one(cells[4].select("span.label"), "library status")
        status = _clean(status_node.get_text(" ", strip=True))
        if status not in _LIBRARY_STATUSES:
            raise GoheungContractError(f"library status changed: {status}")
        rows.append(
            {
                "source_catalogue": "programjoin",
                "source_page": page,
                "source_sequence": int(sequence_text),
                "identity": identity,
                "title": _clean(anchor.get_text(" ", strip=True)),
                "program_type": _clean(category_node.get_text(" ", strip=True)),
                "target": target,
                "start_date": start,
                "end_date": end,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "waitlist_current": wait_current,
                "waitlist_total": wait_total,
                "source_method": source_method,
                "source_status": status,
                "raw_url": goheung_library_detail_url(page, identity),
            }
        )
    if empty and rows:
        raise GoheungContractError("library page mixes data and empty marker")
    if not table.select("tbody > tr"):
        raise GoheungContractError("library table body disappeared")
    return rows, empty


def _library_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.get("source_sequence"),
            row.get("identity"),
            row.get("title"),
            row.get("start_date"),
            row.get("end_date"),
            row.get("source_status"),
        )
        for row in rows
    )


def _library_branch(title: str) -> str:
    match = re.match(r"^\[([^\]]+)\]", _clean(title))
    if match is None:
        return GOHEUNG_LIBRARY_BRANCH
    tag = match.group(1)
    branch = GOHEUNG_LIBRARY_BRANCHES.get(tag)
    if branch is None:
        raise GoheungContractError(f"unknown library branch prefix [{tag}]")
    return branch


def _library_status(source: str, apply_start: str, apply_end: str, cutoff: date) -> str:
    start = date.fromisoformat(apply_start)
    end = date.fromisoformat(apply_end)
    if source == "모집전":
        if cutoff > start:
            raise GoheungContractError("library scheduled status/date mismatch")
        return "SCHEDULED"
    if source == "모집중":
        if not start <= cutoff <= end:
            raise GoheungContractError("library open status/date mismatch")
        return "OPEN"
    if source == "모집마감":
        return "CLOSED"
    raise GoheungContractError(f"unknown library status {source}")


def _library_safe_info(board: Any, identity: str) -> dict[str, str]:
    sections = board.select("section.styleguide")
    if not sections:
        raise GoheungContractError(f"library course {identity} info section missing")
    values: dict[str, str] = {}
    for item in sections[0].select("li"):
        strong = item.find("strong")
        if strong is None:
            continue
        key = _clean(strong.get_text(" ", strip=True))
        full = _clean(item.get_text(" ", strip=True))
        value = _clean(full[len(key) :].lstrip(" :"))
        if key in values:
            raise GoheungContractError(f"library course {identity} duplicate safe field")
        values[key] = value
    return values


def _library_detail(
    parent: Mapping[str, Any], soup: BeautifulSoup, target: Any, cutoff: date
) -> dict[str, Any]:
    identity = _clean(parent.get("identity"))
    title = _one(soup.select("head > title"), f"library {identity} title")
    if _clean(title.get_text(" ", strip=True)) != "프로그램 신청 < 문화마당 - 고흥군립도서관":
        raise GoheungContractError(f"library {identity} detail owner changed")
    board = _one(soup.select("article .boardRead"), f"library {identity} detail")
    heading = _one(board.select(":scope > h1"), f"library {identity} heading")
    category_node = _one(heading.select("span.label"), f"library {identity} category")
    category = _clean(category_node.get_text(" ", strip=True))
    heading_text = _clean(heading.get_text(" ", strip=True))
    detail_title = _clean(heading_text[len(category) :])
    if _normalized(detail_title) != _normalized(parent.get("title")):
        raise GoheungContractError(f"library {identity} title mismatch")
    if _normalized(category) != _normalized(parent.get("program_type")):
        raise GoheungContractError(f"library {identity} category mismatch")
    values = _library_safe_info(board, identity)
    required = {
        "강좌대상",
        "강좌기간",
        "강좌시간",
        "신청기간",
        "모집인원",
        "신청대상",
        "장소",
        "비용",
    }
    if not required <= set(values):
        raise GoheungContractError(f"library {identity} safe detail fields changed")
    if _normalized(values["강좌대상"]) != _normalized(parent.get("target")):
        raise GoheungContractError(f"library {identity} target mismatch")
    start, end = _date_range(values["강좌기간"], f"library {identity} operating")
    apply_start, apply_end = _date_range(values["신청기간"], f"library {identity} application")
    if (start, end) != (parent.get("start_date"), parent.get("end_date")):
        raise GoheungContractError(f"library {identity} operating mismatch")
    capacity_match = re.fullmatch(r"(\d+)명\s*\(대기\s*:\s*(\d+)명\)", values["모집인원"])
    if capacity_match is None:
        raise GoheungContractError(f"library {identity} capacity changed")
    total, wait_total = (int(value) for value in capacity_match.groups())
    if parent.get("capacity_total") is not None and (total, wait_total) != (
        parent.get("capacity_total"),
        parent.get("waitlist_total"),
    ):
        raise GoheungContractError(f"library {identity} capacity mismatch")
    page = int(parent.get("source_page") or 0)
    expected_apply = f"/ProgramJoin/All/All/{page}/apply/{identity}"
    footer_links = board.select("footer a[href]")
    apply_candidates = [
        link
        for link in footer_links
        if "/apply/" in _clean(link.get("href"))
    ]
    if any(_clean(link.get("href")) != expected_apply for link in apply_candidates):
        raise GoheungContractError(f"library {identity} apply identity changed")
    apply_links = [
        link
        for link in apply_candidates
        if _clean(link.get("href")) == expected_apply
    ]
    if len(apply_links) > 1:
        raise GoheungContractError(f"library {identity} duplicate apply controls")
    if apply_links and _clean(apply_links[0].get_text(" ", strip=True)) != "신청하기":
        raise GoheungContractError(f"library {identity} apply label changed")
    status = _library_status(parent.get("source_status"), apply_start, apply_end, cutoff)
    online = parent.get("source_method") == "ONLINE"
    if status == "OPEN" and online and len(apply_links) != 1:
        raise GoheungContractError(f"library {identity} open apply control missing")
    branch = _library_branch(parent.get("title"))
    venue = values["장소"]
    if not venue:
        raise GoheungContractError(f"library {identity} venue empty")
    available = status == "OPEN" and online and len(apply_links) == 1
    row = {
        "provider": GOHEUNG_LIBRARY_PROVIDER,
        "provider_course_id": f"{GOHEUNG_LIBRARY_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(parent.get("title")),
        "branch": branch,
        "branch_code": _branch_code(GOHEUNG_LIBRARY_PROVIDER, branch),
        "provider_organizer": branch,
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start": apply_start,
        "apply_end": apply_end,
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "status": status,
        "category": "교육",
        "program_type": category,
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "official_paginated_list+verified_detail",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공도서관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "schedule_raw": values["강좌시간"],
        "target": values["강좌대상"],
        "room": venue,
        "venue": venue,
        "venue_name": venue,
        "description": _clean(parent.get("title")),
        "capacity": total,
        "capacity_current": parent.get("capacity_current"),
        "capacity_total": total,
        "capacity_remaining": (
            max(0, total - int(parent.get("capacity_current")))
            if parent.get("capacity_current") is not None
            else None
        ),
        "waitlist_current": parent.get("waitlist_current"),
        "waitlist_total": wait_total,
        "fee": values["비용"],
        "application_method": "온라인 프로그램 신청" if online else "오프라인 접수",
        "application_methods": ["온라인" if online else "오프라인"],
        "reservation_available": available,
        "application_url": _clean(parent.get("raw_url")) if available else "",
        "application_type": "ONLINE_RESERVATION" if available else "",
        "raw_url": _clean(parent.get("raw_url")),
        "source_url": _clean(_target_value(target, "url")),
        "raw_fields": {
            "owner_scope": "goheung_library",
            "source_catalogue": "programjoin",
            "source_sequence": parent.get("source_sequence"),
            "source_identity": identity,
            "source_status": parent.get("source_status"),
            "list_schema_verified": True,
            "detail_schema_verified": True,
            "list_detail_verified": True,
            "capacity_verified": True,
            "application_control_verified": True,
            "pii_allowlist_verified": True,
        },
    }
    _validate_output(row)
    return row


def collect_goheung_library_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 200,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    owner = "goheung_library"
    if not is_goheung_library_target(target):
        return [], GOHEUNG_LIBRARY_PARSER, _failure(owner, "target does not match county library owner")
    try:
        timeout_value, allowed_pages, allowed_details, cutoff = _validated_limits(
            timeout, max_pages, detail_limit, today
        )
    except (TypeError, ValueError):
        return [], GOHEUNG_LIBRARY_PARSER, _failure(owner, "max_pages/detail_limit/timeout/today invalid")
    client = _Client(
        timeout=timeout_value,
        fetcher=fetcher or _default_fetcher,
        session_factory=session_factory or _default_session_factory,
    )
    errors: list[str] = []
    source_cap = False
    list_requests = rechecks = sentinels = 0
    rows: list[dict[str, Any]] = []
    counts: list[int] = []
    try:
        if allowed_pages < 3:
            source_cap = True
            raise GoheungContractError("max_pages cannot fetch library data/sentinel/recheck")
        first, first_empty = _library_page(client.html(goheung_library_list_url(1)), 1)
        list_requests += 1
        total = first[0]["source_sequence"] if first else 0
        data_pages = max(1, math.ceil(total / GOHEUNG_LIBRARY_PAGE_SIZE))
        required = data_pages + 2
        if required > allowed_pages:
            source_cap = True
            raise GoheungContractError(f"max_pages allows {allowed_pages} of {required} required")
        for page in range(1, data_pages + 1):
            if page == 1:
                parsed, empty = first, first_empty
            else:
                parsed, empty = _library_page(client.html(goheung_library_list_url(page)), page)
                list_requests += 1
            expected = min(GOHEUNG_LIBRARY_PAGE_SIZE, max(0, total - (page - 1) * GOHEUNG_LIBRARY_PAGE_SIZE))
            if empty != (expected == 0) or len(parsed) != expected:
                raise GoheungContractError(f"library page {page} expected {expected}")
            counts.append(len(parsed))
            rows.extend(parsed)
        sentinel_rows, sentinel_empty = _library_page(
            client.html(goheung_library_list_url(data_pages + 1)), data_pages + 1
        )
        list_requests += 1
        if sentinel_rows or not sentinel_empty:
            raise GoheungContractError("library immediate sentinel not empty")
        sentinels += 1
        checked, checked_empty = _library_page(client.html(goheung_library_list_url(1)), 1)
        list_requests += 1
        rechecks += 1
        if checked_empty != first_empty or _library_signature(checked) != _library_signature(first):
            raise GoheungContractError("library page-one recheck changed")
        numbers = [row["source_sequence"] for row in rows]
        identities = [row["identity"] for row in rows]
        if numbers != list(range(total, 0, -1)) or len(rows) != total:
            raise GoheungContractError("library numbering/total incomplete")
        if len(identities) != len(set(identities)):
            raise GoheungContractError("library duplicate identities")
    except Exception as exc:
        errors.append(f"completeness: {type(exc).__name__}: {exc}")
    current = [row for row in rows if date.fromisoformat(row["end_date"]) >= cutoff]
    expired = len(rows) - len(current)
    if len(current) > allowed_details:
        source_cap = True
        errors.append(f"detail_limit allows {allowed_details} of {len(current)}")
    detailed: list[dict[str, Any]] = []
    attempts = 0
    if not errors:
        for parent in current:
            attempts += 1
            try:
                detailed.append(_library_detail(parent, client.html(parent["raw_url"]), target, cutoff))
            except Exception as exc:
                errors.append(f"course {parent['identity']} detail: {type(exc).__name__}: {exc}")
                break
    result: list[dict[str, Any]] = []
    if not errors:
        result = list((dedupe_rows or _dedupe_default)(detailed))
        if len(result) != len(detailed):
            errors.append("dedupe changed complete library row count")
            result = []
    result.sort(key=lambda row: (row["start_date"], row["title"], row["provider_course_id"]))
    duplicates = len(detailed) - len({row["provider_course_id"] for row in detailed})
    if duplicates and not errors:
        errors.append(f"{duplicates} duplicate output identities")
        result = []
    required_list = len(counts) + 2 if counts else 0
    snapshot = not errors
    pagination = bool(
        snapshot
        and list_requests == required_list
        and sentinels == 1
        and rechecks == 1
        and len(rows) == sum(counts)
    )
    details_complete = bool(snapshot and attempts == len(current) and len(detailed) == len(current))
    controls = bool(
        details_complete
        and all(row["raw_fields"].get("application_control_verified") for row in detailed)
    )
    meta = _base_meta(owner)
    meta.update(
        {
            "pages": client.requests,
            "request_count": client.requests,
            "source_total": len(rows),
            "source_page_counts": counts,
            "source_status_counts": dict(Counter(row["source_status"] for row in rows)),
            "source_rows": len(rows),
            "current_count": len(current),
            "expired_count": expired,
            "returned_count": len(result),
            "required_list_requests": required_list,
            "list_requests": list_requests,
            "list_rechecks": rechecks,
            "sentinel_pages": sentinels,
            "detail_attempts": attempts,
            "detail_pages": len(detailed),
            "duplicate_count": duplicates,
            "source_cap_reached": source_cap,
            "pagination_complete": pagination,
            "details_complete": details_complete,
            "application_controls_complete": controls,
            "snapshot_complete": snapshot,
            "full_snapshot_validated": bool(snapshot and pagination and details_complete and controls),
            "no_current_data": bool(snapshot and not current),
            "no_current_reason": "all rows in the complete ProgramJoin catalogue ended" if snapshot and not current else "",
            "exact_current_branches": dict(Counter(row["branch"] for row in detailed)),
            "configured_collection_error": "; ".join(errors),
        }
    )
    client.close()
    return ([] if errors else result), GOHEUNG_LIBRARY_PARSER, meta


def collect_goheung_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = 200,
    detail_limit: int = 500,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if is_goheung_county_target(target):
        return collect_goheung_county_courses(
            target, timeout, max_pages, detail_limit, **kwargs
        )
    if is_goheung_lifelong_target(target):
        return collect_goheung_lifelong_courses(
            target, timeout, max_pages, detail_limit, **kwargs
        )
    if is_goheung_library_target(target):
        return collect_goheung_library_courses(
            target, timeout, max_pages, detail_limit, **kwargs
        )
    return [], GOHEUNG_PARSER, _failure(
        "goheung_dispatch", "target is not one of the three audited Goheung education owners"
    )


collect = collect_goheung_education


__all__ = [
    "GOHEUNG_AGRICULTURE_URL",
    "GOHEUNG_BUNCHEONG_URL",
    "GOHEUNG_CANDIDATE_AUDIT",
    "GOHEUNG_COUNTY_ALIAS_PROVIDER",
    "GOHEUNG_COUNTY_BRANCH",
    "GOHEUNG_COUNTY_HTTP_ALIAS_URL",
    "GOHEUNG_COUNTY_PARSER",
    "GOHEUNG_COUNTY_PROVIDER",
    "GOHEUNG_COUNTY_SOURCES",
    "GOHEUNG_COUNTY_URL",
    "GOHEUNG_DISCOVERY_AUDIT",
    "GOHEUNG_EDUCATION_OFFICE_URL",
    "GOHEUNG_FORESTTRIP_URL",
    "GOHEUNG_LIBRARY_BRANCH",
    "GOHEUNG_LIBRARY_BRANCHES",
    "GOHEUNG_LIBRARY_PARSER",
    "GOHEUNG_LIBRARY_PROVIDER",
    "GOHEUNG_LIBRARY_URL",
    "GOHEUNG_LIFELONG_BRANCH",
    "GOHEUNG_LIFELONG_PARSER",
    "GOHEUNG_LIFELONG_PROVIDER",
    "GOHEUNG_LIFELONG_URL",
    "GOHEUNG_LECTURE_SOURCES",
    "GOHEUNG_MUNICIPALITY_CODE",
    "GOHEUNG_MUNICIPALITY_NAME",
    "GOHEUNG_OWNER_BOUNDARY_AUDIT",
    "GOHEUNG_PARSER",
    "GOHEUNG_PII_FIELDS_DISCARDED",
    "GOHEUNG_READING_URL",
    "GoheungContractError",
    "GoheungCountySource",
    "GoheungJneTlsAdapter",
    "GoheungLectureSource",
    "build_goheung_jne_tls_context",
    "collect",
    "collect_goheung_county_courses",
    "collect_goheung_education",
    "collect_goheung_library_courses",
    "collect_goheung_lifelong_courses",
    "goheung_county_detail_url",
    "goheung_county_list_url",
    "goheung_lecture_detail_url",
    "goheung_lecture_list_url",
    "goheung_library_detail_url",
    "goheung_library_list_url",
    "goheung_reading_detail_url",
    "goheung_reading_list_url",
    "is_goheung_county_target",
    "is_goheung_library_target",
    "is_goheung_lifelong_target",
    "is_goheung_target",
    "is_target",
]
