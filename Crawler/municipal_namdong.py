"""Fail-closed collector for Namdong-gu's official education ledgers.

The municipality publishes education through two authoritative owners.  The
lifelong-learning service has six registered category ledgers, while the
district library has separate normal/event ledgers for each library branch.
This collector walks every declared page of both services, proves the page
boundary, rechecks the first and last pages, and verifies every current or
future record against its detail page.

``biz.namdong.go.kr`` omits its intermediate certificate from the TLS
handshake.  The adapter below adds the issuer's official AIA intermediate to
the normal operating-system trust store; CA, hostname, and validity checks all
remain enabled.  It never disables certificate verification.

The city-management corporation's sports portals are intentionally not owned
by this collector.  They are a separate operator and must be promoted under a
separate provider if sports coverage is desired.  Legacy ``lecturelll`` pages
are also excluded because they are strict subsets of the canonical
``lecture`` ledger.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
import ssl
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


NAMDONG_PROVIDER = "MUNI_BIZ_NAMDONG_GO_KR_8423F6B9"
NAMDONG_CANDIDATE_ID = "MUNI_IR_2EA49EB2BA7B"
NAMDONG_MUNICIPALITY_CODE = "2820000000"
NAMDONG_MUNICIPALITY_NAME = "인천광역시 남동구"

NAMDONG_BIZ_HOST = "biz.namdong.go.kr"
NAMDONG_LIBRARY_HOST = "www.namdonglib.go.kr"
NAMDONG_LIFE_LIST_PATH = "/lecture/lectureList.do"
NAMDONG_LIFE_DETAIL_PATH = "/lecture/lectureDetail.do"
NAMDONG_CANONICAL_URL = (
    "https://biz.namdong.go.kr/lecture/lectureList.do?"
    "cd=life&leccate=101&sitediv=life"
)

NAMDONG_PARSER = (
    "namdong_six_lifelong_catalogues+eleven_library_branch_catalogues+"
    "all_declared_pages+post_last_boundaries+stable_first_last+"
    "current_details+identity_bound_application_controls+"
    "verified_aia_intermediate+facility_branches+legacy_subset_exclusions+"
    "pii_allowlist"
)

NAMDONG_PAGE_SIZE_LIFE = 12
NAMDONG_PAGE_SIZE_LIBRARY = 10
NAMDONG_MAX_HTML_BYTES = 3_000_000
NAMDONG_AIA_INTERMEDIATE_SHA256 = (
    "a6f9c967eb8aa9283a1ca649b87b764720e9f5c3afa81c150676f4ca36e98cf6"
)
NAMDONG_LEAF_SHA256_AUDITED_2026_07_22 = (
    "676e9cdf9416bdfb8fd317256e9c84d7f5b9c89f0fc9e78f78243136b8a83a53"
)

# Official issuer AIA object:
# https://public.wisekey.com/crt/tsrsasecureca2.cer
NAMDONG_AIA_INTERMEDIATE_PEM = """-----BEGIN CERTIFICATE-----
MIIFjjCCBHagAwIBAgIQcCosoce0HIWpncOmISmyLzANBgkqhkiG9w0BAQsFADBt
MQswCQYDVQQGEwJDSDEQMA4GA1UEChMHV0lTZUtleTEiMCAGA1UECxMZT0lTVEUg
Rm91bmRhdGlvbiBFbmRvcnNlZDEoMCYGA1UEAxMfT0lTVEUgV0lTZUtleSBHbG9i
YWwgUm9vdCBHQiBDQTAeFw0yNTA1MjcxNTEwMzRaFw0zMDA1MjYxNTEwMzRaMFEx
CzAJBgNVBAYTAkNIMR0wGwYDVQQKDBRUdXJpbmdTaWduIEdsb2JhbCBTQTEjMCEG
A1UEAwwaVHVyaW5nU2lnbiBSU0EgU2VjdXJlIENBIDIwggIiMA0GCSqGSIb3DQEB
AQUAA4ICDwAwggIKAoICAQDGDBcFU6l+Hs5OUzBVjDQP8xGhdPG7xvNPu2Q5FF1f
L4IOIIYnx2E3ZFVbYf4a6d/8q4HFlWLT98BIPGo3nlsZiyaKb6MKMGONE5/4DfMk
zn+JkQaggOmXNLhn0hbezFJOJaYBcCroBZmDyOKbHRSHnBDZuG8Fx5UqbSG3Zlic
ywd4ET0CZXL/QZCcJzRJ6OMyndQpvmxbCq8TUwbqT4FwFDOwigqBPNlEgjSje0vc
3Xg7KUOgcHs9NI26Vo72YR/uiA9N/0gMfum0DLp/31vhIHw68LC/7cU/4Rp6yYaY
c8OfyhRuwfsMHWTXpAroHqbK8zlK4ZFOaTv+6MeFHnADyYRLdLl4cPTDmLUZFbyo
3Ec/NFepKYP/hFM0Fo7wFHMg1QsLSOD9KcQzxOkAhggX5bHd3DvQZyo3g3EnC6l0
FFQ4UwTI2qLKXpVN8EUfh3HSJmbVsQoyUdmbOz+qjtIjHAP2mIwip6AvE3DWA28E
K09fLTCbCbP/NBAfZAWbfzSeombpwib5pLUQ6/0FzMRw8dE6jm5t5L5INBXaUUCx
wXM9BJxMc+gqjxRJD5SEbyK0dFR74n2nkzzUS83GyFJXkfYDOnYBUN0kGtUzn4bt
RLdQ00+xewgFVMPGXTeQMK0VpavOb0uFcu4ZhLA28B2iT8XWc4Not1Bj84+5O50K
EwIDAQABo4IBRDCCAUAwEgYDVR0TAQH/BAgwBgEB/wIBADAfBgNVHSMEGDAWgBQ1
D8g2Y17io+z5O2YVzlFS45GaPTBrBggrBgEFBQcBAQRfMF0wNgYIKwYBBQUHMAKG
Kmh0dHA6Ly9wdWJsaWMud2lzZWtleS5jb20vY3J0L293Z3JnYmNhLmNlcjAjBggr
BgEFBQcwAYYXaHR0cDovL29jc3Aud2lzZWtleS5jb20wEQYDVR0gBAowCDAGBgRV
HSAAMB0GA1UdJQQWMBQGCCsGAQUFBwMCBggrBgEFBQcDATA7BgNVHR8ENDAyMDCg
LqAshipodHRwOi8vcHVibGljLndpc2VrZXkuY29tL2NybC9vd2dyZ2JjYS5jcmww
HQYDVR0OBBYEFM3OdTxWi2FRu9+xUPmb6hymFzMRMA4GA1UdDwEB/wQEAwIBBjAN
BgkqhkiG9w0BAQsFAAOCAQEAbjvOB6/tTaX0YG/8sPytIvU6nEWuq2Zfxl7FMMB7
wAm7IPPf5MSTXcc8mmPh97YDj/A6N3jOf09G7IJEGYo7Sf9948ZhL6czKmByyKhU
r3yCEmVV/+MyhTvhc5aJIG6dnADXw8C1lMwEt6gzMolsNyQ3gY6slPxZ2xUEcPZi
wm9veB9aR+QfcUl7UHQHpfC7EoeelSir7AfcvLdbseaqM5GeWlFWmsCH7SweFybv
Tjz94Rfsafz5fEL2EaApecOUK3bLh9mO6cgL7n8yryrUKG5hY6D4OirSYpYJvS6y
u2wLYijDNYa2wMqRFdIoMB/7NxDyVQ3lfc7Kj50d33TUsQ==
-----END CERTIFICATE-----"""


@dataclass(frozen=True)
class LifelongCategory:
    code: str
    label: str


NAMDONG_LIFELONG_CATEGORIES: tuple[LifelongCategory, ...] = (
    LifelongCategory("101", "인생사계학교"),
    LifelongCategory("102", "실버청춘학교"),
    LifelongCategory("103", "희망잡(JOB)"),
    LifelongCategory("104", "인문시민학교"),
    LifelongCategory("106", "평생학습관기획강좌"),
    LifelongCategory("107", "남동그린시민학교"),
)


@dataclass(frozen=True)
class LibraryCatalogue:
    key: str
    label: str
    list_path: str
    detail_path: str
    application_path: str
    mnidx: str
    cldidx: str
    branches: tuple[tuple[str, str], ...]


_LIBRARY_COMMON_BRANCHES = (
    ("172", "남동논현도서관"),
    ("272", "소래도서관"),
    ("393", "서창도서관"),
    ("430", "간석3동 어린이도서관"),
    ("568", "만수2동 어린이도서관"),
)
NAMDONG_LIBRARY_CATALOGUES: tuple[LibraryCatalogue, ...] = (
    LibraryCatalogue(
        "program",
        "프로그램 신청",
        "/ndglib/usr/program/openProgramList.do",
        "/ndglib/usr/program/openProgramDetail.do",
        "/ndglib/usr/programApp/openProgramAppWrite.do",
        "125",
        "169",
        _LIBRARY_COMMON_BRANCHES,
    ),
    LibraryCatalogue(
        "event",
        "이벤트 프로그램 신청",
        "/ndglib/usr/programEvent/openProgramEventList.do",
        "/ndglib/usr/programEvent/openProgramEventDetail.do",
        "/ndglib/usr/programEventApp/openProgramEventAppWrite.do",
        "126",
        "172",
        _LIBRARY_COMMON_BRANCHES + (("836", "서창어울마당작은도서관"),),
    ),
)

NAMDONG_EXCLUDED_OFFICIAL_SOURCES: tuple[dict[str, str], ...] = (
    {
        "source": "https://biz.namdong.go.kr/lecturelll/",
        "reason": "legacy_subset_of_canonical_lecture_ledger",
    },
    {
        "source": "https://www.namdong.go.kr/lll/enrolment/",
        "reason": "wrapper_for_legacy_subset",
    },
    {
        "source": "lifelong_category_105",
        "reason": "misroutes_to_enterprise_support_not_education",
    },
    {
        "source": "https://www.namdonglib.go.kr/ndglib/usr/trip/",
        "reason": "library_trip_is_experience_not_education",
    },
    {
        "source": "https://www.namdongsports.or.kr/",
        "reason": "separate_city_management_corporation_sports_owner",
    },
    {
        "source": "https://kukmin.namdongsports.or.kr/",
        "reason": "separate_city_management_corporation_sports_owner",
    },
    {
        "source": "https://km.namdongsports.or.kr/",
        "reason": "mobile_mirror_of_separate_sports_owner",
    },
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]


class NamdongContractError(ValueError):
    """Raised when an official source no longer satisfies its audited shape."""


class _SSLContextAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext, **kwargs: Any) -> None:
        self._context = context
        super().__init__(**kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self._context
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        proxy_kwargs["ssl_context"] = self._context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def namdong_session_factory() -> requests.Session:
    """Return a normal verified session augmented with the missing issuer."""

    context = ssl.create_default_context()
    context.load_verify_locations(cadata=NAMDONG_AIA_INTERMEDIATE_PEM)
    session = requests.Session()
    session.mount(
        f"https://{NAMDONG_BIZ_HOST}/",
        _SSLContextAdapter(context, max_retries=0),
    )
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=True)


_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CANCELLED_RE = re.compile(r"^\s*\[(?:폐강|취소|휴강)\]")
# Exact closed imports audited on 2026-07-22.  The server retains these in the
# canonical history with one blank range.  No newly-created identity inherits
# this exception.
_LEGACY_BLANK_APPLY_IDS = frozenset(
    {
        ("101", "26004"),
        ("101", "24742"),
        ("101", "23309"),
        ("101", "23306"),
        ("101", "23295"),
        ("101", "10893"),
    }
)
_LEGACY_BLANK_EDUCATION_IDS = frozenset({("106", "23300")})
_LIFE_STATUS = {
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "대기접수": "OPEN",
    "접수마감": "CLOSED",
}
_LIBRARY_STATUS = {
    "접수대기": "SCHEDULED",
    "접수진행": "OPEN",
    "접수종료": "CLOSED",
}
_LIBRARY_DETAIL_STATUS = {
    "SCHEDULED": frozenset({"접수대기", "접수예정"}),
    "OPEN": frozenset({"접수중", "접수진행"}),
    "CLOSED": frozenset({"접수마감", "접수종료"}),
}
_LIBRARY_DETAIL_EVENT_STATUS = {
    "행사대기": frozenset({"행사대기"}),
    "행사진행": frozenset({"행사진행", "행사중"}),
    "행사종료": frozenset({"행사종료", "행사마감"}),
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_kind",
        "source_catalogue",
        "source_category",
        "source_branch_code",
        "source_branch",
        "list_page",
        "source_status",
        "source_apply_period",
        "source_education_period",
        "source_institution",
        "detail_verified",
        "visible_application_control_present",
        "actionable_application_control_present",
        "application_control_contract",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "instructor",
        "teacher",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _as_date(value: Any) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _dates(value: str, *, context: str) -> tuple[date, date]:
    tokens = [date(int(y), int(m), int(d)) for y, m, d in _DATE_RE.findall(value)]
    if len(tokens) < 2:
        raise NamdongContractError(f"{context}: date range missing")
    start, end = tokens[0], tokens[-1]
    if end < start:
        raise NamdongContractError(f"{context}: reversed date range")
    return start, end


def _query_exact(url: str, expected: list[tuple[str, str]]) -> bool:
    try:
        return parse_qsl(urlparse(url).query, keep_blank_values=True, strict_parsing=True) == expected
    except ValueError:
        return False


def is_namdong_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != NAMDONG_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == NAMDONG_BIZ_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == NAMDONG_LIFE_LIST_PATH
        and _query_exact(
            parsed.geturl(),
            [("cd", "life"), ("leccate", "101"), ("sitediv", "life")],
        )
        and not parsed.fragment
    )


is_target = is_namdong_education_target


@dataclass(frozen=True)
class _Page:
    requested: int
    actual: int
    last: int
    rows: tuple[dict[str, Any], ...]
    empty: bool


class _Budget:
    def __init__(self, maximum: int) -> None:
        if maximum < 1:
            raise NamdongContractError("max_pages must be positive")
        self.maximum = maximum
        self.used = 0

    def take(self) -> None:
        if self.used >= self.maximum:
            raise NamdongContractError(
                f"max_pages {self.maximum} cannot cover complete boundary proof"
            )
        self.used += 1


def _close_session(session: Any) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _Requester:
    _RETRYABLE = frozenset({403, 429, 500, 502, 503, 504})

    def __init__(
        self,
        session: Any,
        fetcher: Fetcher,
        timeout: int,
        sleeper: Sleeper,
        session_factory: Optional[SessionFactory] = None,
    ) -> None:
        self.session = session
        self.fetcher = fetcher
        self.timeout = timeout
        self.sleeper = sleeper
        self.session_factory = session_factory
        self.http_attempts = 0
        self.retry_count = 0
        self.waf_retry_count = 0
        self.session_refresh_count = 0

    def _refresh_session(self) -> None:
        if self.session_factory is None:
            return
        _close_session(self.session)
        self.session = self.session_factory()
        self.session_refresh_count += 1

    def close(self) -> None:
        _close_session(self.session)

    def soup(self, url: str, *, host: str) -> BeautifulSoup:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != host
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise NamdongContractError("non-canonical source URL refused")

        last_error: Optional[BaseException] = None
        for attempt in range(9):
            try:
                self.http_attempts += 1
                response = self.fetcher(self.session, url, self.timeout)
                status = int(getattr(response, "status_code", 200) or 200)
                if status in self._RETRYABLE and attempt < 8:
                    self.retry_count += 1
                    if status == 403:
                        self.waf_retry_count += 1
                        self._refresh_session()
                    delay = (
                        2.0
                        if status == 403
                        else min(2.0, 0.5 + (attempt * 0.5))
                    )
                    self.sleeper(delay)
                    continue
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                if status >= 400:
                    raise NamdongContractError(f"HTTP {status} from official source")
                content = getattr(response, "content", None)
                if content is None:
                    content = str(getattr(response, "text", response)).encode("utf-8")
                if len(content) > NAMDONG_MAX_HTML_BYTES:
                    raise NamdongContractError("HTML size cap exceeded")
                final = urlparse(str(getattr(response, "url", url)))
                if (
                    final.scheme != "https"
                    or (final.hostname or "").lower() != host
                    or final.username is not None
                    or final.password is not None
                ):
                    raise NamdongContractError("redirect outside official host")
                parsed_content = BeautifulSoup(content, "html.parser")
                # The district WAF also rate-limits otherwise valid sequential
                # requests.  A small host-specific cadence avoids turning a
                # complete 47-page walk into a burst while keeping tests fast
                # through the injected sleeper.
                if host == NAMDONG_BIZ_HOST:
                    self.sleeper(0.15)
                return parsed_content
            except (requests.RequestException, TimeoutError) as exc:
                last_error = exc
                if attempt >= 8:
                    raise
                self.retry_count += 1
                self.sleeper(min(2.0, 0.5 + (attempt * 0.5)))
        if last_error is not None:
            raise last_error
        raise NamdongContractError("official source request failed")


def _life_list_url(category: str, page: int) -> str:
    query = {
        "cd": "life",
        "leccate": category,
        "sitediv": "life",
        "nowPage": page,
    }
    return f"https://{NAMDONG_BIZ_HOST}{NAMDONG_LIFE_LIST_PATH}?{urlencode(query)}"


def _life_detail_url(category: str, identity: str) -> str:
    query = {
        "lecseq": identity,
        "leccate": category,
        "sitediv": "life",
        "cd": "life",
    }
    return f"https://{NAMDONG_BIZ_HOST}{NAMDONG_LIFE_DETAIL_PATH}?{urlencode(query)}"


def _library_list_url(catalogue: LibraryCatalogue, branch: str, page: int) -> str:
    query = {
        "mnid": "mn03",
        "mnidx": catalogue.mnidx,
        "cldidx": catalogue.cldidx,
        "AGENCY_CD": branch,
        "pageNo": page,
    }
    return f"https://{NAMDONG_LIBRARY_HOST}{catalogue.list_path}?{urlencode(query)}"


def _library_detail_url(
    catalogue: LibraryCatalogue, branch: str, identity: str
) -> str:
    query = {
        "mnid": "mn03",
        "mnidx": catalogue.mnidx,
        "cldidx": catalogue.cldidx,
        "IDX": identity,
        "AGENCY_CD": branch,
    }
    return f"https://{NAMDONG_LIBRARY_HOST}{catalogue.detail_path}?{urlencode(query)}"


def _life_fields(root: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in root.select(".lec_info > li"):
        label = item.select_one(".wfont")
        if label is None:
            continue
        key = _clean(label.get_text(" ", strip=True)).rstrip(":").strip()
        clone = BeautifulSoup(str(item), "html.parser")
        for node in clone.select(".wfont"):
            node.decompose()
        fields[key] = _clean(clone.get_text(" ", strip=True)).lstrip(":").strip()
    return fields


def _parse_life_page(
    soup: BeautifulSoup,
    category: LifelongCategory,
    requested: int,
) -> _Page:
    cards = soup.select(".lecList > li")
    nodata = [_clean(x.get_text(" ", strip=True)) for x in soup.select(".board_list .nodata")]
    if not cards:
        if nodata != ["등록된 교육이 없음"]:
            raise NamdongContractError(
                f"lifelong {category.code} page {requested}: empty sentinel missing"
            )
        return _Page(requested, 0, 0, (), True)

    active = soup.select_one(".paging .num.select")
    if active is None or not _clean(active.get_text(" ", strip=True)).isdigit():
        raise NamdongContractError(
            f"lifelong {category.code} page {requested}: active page missing"
        )
    actual = int(_clean(active.get_text(" ", strip=True)))
    page_numbers = [actual]
    for anchor in soup.select(".paging a[href*='nowPage=']"):
        values = parse_qs(urlparse(anchor.get("href", "")).query).get("nowPage", [])
        if len(values) == 1 and values[0].isdigit():
            page_numbers.append(int(values[0]))
    last = max(page_numbers)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        anchor = card.select_one(".tit a[href*='lectureDetail.do'][href*='lecseq=']")
        status_node = card.select_one(".tag_state")
        if anchor is None or status_node is None:
            raise NamdongContractError(
                f"lifelong {category.code} page {requested}: card contract changed"
            )
        href = urljoin(NAMDONG_CANONICAL_URL, anchor.get("href", ""))
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        identity = (query.get("lecseq") or [""])[0]
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != NAMDONG_BIZ_HOST
            or parsed.path != NAMDONG_LIFE_DETAIL_PATH
            or not _IDENTITY_RE.fullmatch(identity)
            or query.get("leccate") != [category.code]
            or query.get("sitediv") != ["life"]
            or query.get("cd") != ["life"]
            or identity in seen
        ):
            raise NamdongContractError(
                f"lifelong {category.code} page {requested}: invalid identity link"
            )
        seen.add(identity)
        title = _clean(anchor.get_text(" ", strip=True))
        status_raw = _clean(status_node.get_text(" ", strip=True))
        if not title or status_raw not in _LIFE_STATUS:
            raise NamdongContractError(f"lifelong {identity}: title/status missing")
        fields = _life_fields(card)
        required = {"접수", "교육기관", "교육"}
        if not required <= set(fields):
            raise NamdongContractError(f"lifelong {identity}: required fields missing")
        cancelled = bool(_CANCELLED_RE.match(title))
        try:
            apply_start, apply_end = _dates(
                fields["접수"], context=f"lifelong {identity} apply"
            )
        except NamdongContractError:
            if _DATE_RE.findall(fields["접수"]):
                raise
            if (
                not cancelled
                and (category.code, identity) not in _LEGACY_BLANK_APPLY_IDS
            ):
                raise
            if status_raw != "접수마감":
                raise NamdongContractError(
                    f"lifelong {identity}: undated import is not closed"
                )
            apply_start = apply_end = None
        try:
            start, end = _dates(
                fields["교육"], context=f"lifelong {identity} education"
            )
        except NamdongContractError:
            if (
                _DATE_RE.findall(fields["교육"])
                or (category.code, identity) not in _LEGACY_BLANK_EDUCATION_IDS
                or status_raw != "접수마감"
                or apply_end is None
            ):
                raise
            start = end = apply_end
        rows.append(
            {
                "source_kind": "lifelong",
                "category": category.code,
                "category_label": category.label,
                "identity": identity,
                "title": title,
                "status_raw": status_raw,
                "status": _LIFE_STATUS[status_raw],
                "apply_start": apply_start,
                "apply_end": apply_end,
                "start": start,
                "end": end,
                "institution": _clean(fields["교육기관"]),
                "fee": _clean(fields.get("수강료")),
                "materials_fee": _clean(fields.get("교재비")),
                "schedule": _clean(fields.get("교육 요일 및 시간")),
                "apply_period": _clean(fields["접수"]),
                "education_period": _clean(fields["교육"]),
                "cancelled": cancelled,
                "list_page": actual,
                "raw_url": _life_detail_url(category.code, identity),
            }
        )
    if len(rows) > NAMDONG_PAGE_SIZE_LIFE:
        raise NamdongContractError(
            f"lifelong {category.code} page {requested}: page size changed"
        )
    return _Page(requested, actual, last, tuple(rows), False)


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.actual,
        page.last,
        tuple(
            (
                row["source_kind"],
                row.get("category") or row.get("catalogue"),
                row.get("branch_code", ""),
                row["identity"],
                row["title"],
                row["status_raw"],
                row["start"],
                row["end"],
            )
            for row in page.rows
        ),
    )


def _library_last_page(soup: BeautifulSoup) -> int:
    numbers: list[int] = []
    for anchor in soup.select(".paging a"):
        blob = f"{anchor.get('onclick', '')} {anchor.get('href', '')}"
        numbers.extend(int(x) for x in re.findall(r"fn_movePage\(['\"]?(\d+)", blob))
    return max(numbers, default=1)


def _parse_library_page(
    soup: BeautifulSoup,
    catalogue: LibraryCatalogue,
    branch_code: str,
    branch_name: str,
    requested: int,
    *,
    expected_last: Optional[int] = None,
) -> _Page:
    hidden = soup.select_one("form[name='frm'] input[name='AGENCY_CD']")
    active_branch = soup.select_one("li.active > a[name='libClick']")
    if (
        hidden is None
        or _clean(hidden.get("value")) != branch_code
        or active_branch is None
        or _clean(active_branch.get_text(" ", strip=True)) != branch_name
    ):
        raise NamdongContractError(
            f"library {catalogue.key}/{branch_code} page {requested}: branch drift"
        )
    cards = soup.select(".eventListBox > ul > li")
    last = expected_last if expected_last is not None else _library_last_page(soup)
    focus = soup.select_one(".paging a.focus")
    if cards:
        if focus is None or not _clean(focus.get_text(" ", strip=True)).isdigit():
            raise NamdongContractError(
                f"library {catalogue.key}/{branch_code} page {requested}: pager missing"
            )
        actual = int(_clean(focus.get_text(" ", strip=True)))
    else:
        if focus is not None:
            raise NamdongContractError(
                f"library {catalogue.key}/{branch_code} page {requested}: empty page focused"
            )
        actual = 0

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = {"접수기간", "교육기간", "교육대상", "접수/정원", "예비접수/예비정원"}
    for card in cards:
        identity_node = card.select_one("input#IDX")
        heading = card.select_one(".eventListCon h3")
        statuses = [_clean(x.get_text(" ", strip=True)) for x in card.select(".eventListBtn p")]
        if identity_node is None or heading is None or len(statuses) != 2:
            raise NamdongContractError(
                f"library {catalogue.key}/{branch_code}: card contract changed"
            )
        identity = _clean(identity_node.get("value"))
        if not _IDENTITY_RE.fullmatch(identity) or identity in seen:
            raise NamdongContractError(
                f"library {catalogue.key}/{branch_code}: invalid/duplicate identity"
            )
        seen.add(identity)
        fields: dict[str, str] = {}
        for term in card.select(".eventListCon dl > dt"):
            value = term.find_next_sibling("dd")
            if value is not None:
                fields[_clean(term.get_text(" ", strip=True))] = _clean(
                    value.get_text(" ", strip=True)
                )
        if not required <= set(fields) or statuses[0] not in _LIBRARY_STATUS:
            raise NamdongContractError(
                f"library {catalogue.key}/{branch_code}/{identity}: fields/status missing"
            )
        if statuses[1] not in {"행사대기", "행사진행", "행사종료"}:
            raise NamdongContractError(
                f"library {catalogue.key}/{branch_code}/{identity}: event status changed"
            )
        title = _clean(heading.get_text(" ", strip=True))
        apply_start, apply_end = _dates(
            fields["접수기간"], context=f"library {catalogue.key}/{identity} apply"
        )
        start, end = _dates(
            fields["교육기간"], context=f"library {catalogue.key}/{identity} education"
        )
        capacity_match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", fields["접수/정원"])
        wait_match = re.fullmatch(
            r"\s*(\d+)\s*/\s*(\d+)\s*", fields["예비접수/예비정원"]
        )
        if not title or capacity_match is None or wait_match is None:
            raise NamdongContractError(
                f"library {catalogue.key}/{branch_code}/{identity}: capacity/title invalid"
            )
        rows.append(
            {
                "source_kind": "library",
                "catalogue": catalogue.key,
                "catalogue_label": catalogue.label,
                "branch_code": branch_code,
                "branch": branch_name,
                "identity": identity,
                "title": title,
                "status_raw": statuses[0],
                "event_status_raw": statuses[1],
                "status": _LIBRARY_STATUS[statuses[0]],
                "apply_start": apply_start,
                "apply_end": apply_end,
                "start": start,
                "end": end,
                "target": _clean(fields["교육대상"]),
                "capacity_current": int(capacity_match.group(1)),
                "capacity_total": int(capacity_match.group(2)),
                "waitlist_current": int(wait_match.group(1)),
                "waitlist_total": int(wait_match.group(2)),
                "apply_period": _clean(fields["접수기간"]),
                "education_period": _clean(fields["교육기간"]),
                "list_page": actual,
                "raw_url": _library_detail_url(catalogue, branch_code, identity),
            }
        )
    if len(rows) > NAMDONG_PAGE_SIZE_LIBRARY:
        raise NamdongContractError(
            f"library {catalogue.key}/{branch_code} page {requested}: page size changed"
        )
    if cards and expected_last is not None:
        if actual != requested or requested > last:
            raise NamdongContractError(
                f"library {catalogue.key}/{branch_code} page {requested}: page drift"
            )
        if requested < last and len(rows) != NAMDONG_PAGE_SIZE_LIBRARY:
            raise NamdongContractError(
                f"library {catalogue.key}/{branch_code} page {requested}: short interior page"
            )
    return _Page(requested, actual, last if cards else 0, tuple(rows), not cards)


def _table_fields(root: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in root.select("tr"):
        for heading in row.find_all("th", recursive=False):
            value = heading.find_next_sibling("td")
            if value is not None:
                fields[_clean(heading.get_text(" ", strip=True))] = _clean(
                    value.get_text(" ", strip=True)
                )
    return fields


def _capacity_life(value: str, identity: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
    online = re.search(r"온라인\s*:\s*(\d+)\s*명", value)
    wait = re.search(r"대기\s*:\s*(\d+)\s*명", value)
    if online is None:
        simple = re.search(r"(?<!\d)(\d+)\s*명", value)
        if simple is None:
            raise NamdongContractError(f"lifelong {identity}: capacity changed")
        return None, int(simple.group(1)), int(wait.group(1)) if wait else None
    return None, int(online.group(1)), int(wait.group(1)) if wait else None


def _safe_life_application(
    root: Any, identity: str, status: str
) -> tuple[str, bool, str]:
    active: list[str] = []
    closed_controls = 0
    for anchor in root.select(".btnBox a"):
        text = _clean(anchor.get_text(" ", strip=True))
        href = _clean(anchor.get("href"))
        if text == "목록":
            continue
        if href == "#close" and "신청마감" in text:
            closed_controls += 1
            continue
        if not href or href.startswith("#"):
            continue
        resolved = urljoin(NAMDONG_CANONICAL_URL, href)
        parsed = urlparse(resolved)
        query = parse_qs(parsed.query)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != NAMDONG_BIZ_HOST
            or query.get("lecseq") != [identity]
            or "lecture" not in parsed.path.lower()
        ):
            raise NamdongContractError(
                f"lifelong {identity}: application identity/control changed"
            )
        active.append(resolved)
    if status == "OPEN":
        if not active:
            # The official ledger can continue to label a structurally valid
            # course as open after its application control has disappeared
            # (for example, when capacity closes before the advertised end of
            # the application window).  The caller has already verified the
            # list/detail identity, education and application periods,
            # institution, venue, and online method.  Keep that education row
            # but never invent or expose an application URL.
            return "", False, "official_open_without_application_control_conservative_closed"
        if len(active) != 1:
            raise NamdongContractError(
                f"lifelong {identity}: open course has ambiguous identity-bound application controls"
            )
        return active[0], True, "visible_identity_bound_application_anchor"
    if active:
        raise NamdongContractError(
            f"lifelong {identity}: inactive course exposes application control"
        )
    if status == "CLOSED" and closed_controls > 1:
        raise NamdongContractError(f"lifelong {identity}: close controls changed")
    return "", False, "closed_sentinel" if closed_controls else "no_active_control"


def _life_branch(institution: str, venue: str) -> str:
    institution = _clean(institution)
    venue = _clean(venue)
    if "남동구평생학습관" in venue or institution in {
        "남동평생학습관",
        "남동구평생학습관",
    }:
        return "남동구평생학습관"
    return institution or venue


def _branch_code(value: str) -> str:
    return "NAMDONG_" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:12].upper()


def _life_detail(listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date) -> dict[str, Any]:
    identity = _clean(listed["identity"])
    root = soup.select_one("#detail_con .board_view")
    title_node = root.select_one(":scope > .title") if root is not None else None
    if root is None or title_node is None:
        raise NamdongContractError(f"lifelong {identity}: detail root/title missing")
    title = _clean(title_node.get_text(" ", strip=True))
    if title != _clean(listed["title"]):
        raise NamdongContractError(f"lifelong {identity}: detail title mismatch")
    fields: dict[str, str] = {}
    for item in root.select(":scope > .data_list dl"):
        term, value = item.select_one("dt"), item.select_one("dd")
        if term is not None and value is not None:
            fields[_clean(term.get_text(" ", strip=True))] = _clean(
                value.get_text(" ", strip=True)
            )
    required = {"교육기관", "접수방법", "접수기간", "교육기간", "신청정원", "교육장소"}
    if not required <= set(fields):
        raise NamdongContractError(f"lifelong {identity}: detail fields missing")
    if _clean(fields["교육기관"]) != _clean(listed["institution"]):
        raise NamdongContractError(f"lifelong {identity}: institution mismatch")
    if _dates(fields["교육기간"], context=f"lifelong {identity} detail education") != (
        listed["start"],
        listed["end"],
    ):
        raise NamdongContractError(f"lifelong {identity}: education period mismatch")
    if _dates(fields["접수기간"], context=f"lifelong {identity} detail apply") != (
        listed["apply_start"],
        listed["apply_end"],
    ):
        raise NamdongContractError(f"lifelong {identity}: apply period mismatch")
    if "온라인" not in fields["접수방법"]:
        raise NamdongContractError(f"lifelong {identity}: non-online method changed")
    status = _clean(listed["status"])
    if status == "SCHEDULED" and cutoff > listed["apply_start"]:
        raise NamdongContractError(f"lifelong {identity}: scheduled date contradiction")
    if status == "OPEN" and not (listed["apply_start"] <= cutoff <= listed["apply_end"]):
        raise NamdongContractError(f"lifelong {identity}: open date contradiction")
    application_url, control, contract = _safe_life_application(root, identity, status)
    if status == "OPEN" and not control:
        status = "CLOSED"
    current, total, waitlist = _capacity_life(fields["신청정원"], identity)
    venue = _clean(fields["교육장소"])
    branch = _life_branch(fields["교육기관"], venue)
    if not branch or _PHONE_RE.search(branch) or _EMAIL_RE.search(branch):
        raise NamdongContractError(f"lifelong {identity}: unsafe branch")
    raw_url = _clean(listed["raw_url"])
    return {
        "provider": NAMDONG_PROVIDER,
        "provider_course_id": f"{NAMDONG_PROVIDER}:life:{listed['category']}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": _clean(listed["category_label"]),
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if control else "INFO_ONLY",
        "application_method": "온라인" if control else "",
        "application_methods": ["온라인"] if control else [],
        "reservation_available": control,
        "status": status,
        "fee": _clean(listed.get("fee")),
        "fee_amount": None,
        "period": f"{listed['start'].isoformat()} ~ {listed['end'].isoformat()}",
        "start_date": listed["start"].isoformat(),
        "end_date": listed["end"].isoformat(),
        "apply_period": _clean(fields["접수기간"]),
        "schedule_raw": _clean(listed.get("schedule")),
        "capacity": _clean(fields["신청정원"]),
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_total": waitlist,
        "target": _clean(fields.get("교육대상")),
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": NAMDONG_PARSER,
        "municipality_code": NAMDONG_MUNICIPALITY_CODE,
        "municipality_full_name": NAMDONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_kind": "lifelong",
            "source_catalogue": "canonical_lecture",
            "source_category": _clean(listed["category"]),
            "source_branch_code": "",
            "source_branch": branch,
            "list_page": int(listed["list_page"]),
            "source_status": _clean(listed["status_raw"]),
            "source_apply_period": _clean(listed["apply_period"]),
            "source_education_period": _clean(listed["education_period"]),
            "source_institution": _clean(listed["institution"]),
            "detail_verified": True,
            "visible_application_control_present": control,
            "actionable_application_control_present": control,
            "application_control_contract": contract,
            "service_family": "education",
        },
    }


def _library_detail(
    listed: Mapping[str, Any],
    catalogue: LibraryCatalogue,
    soup: BeautifulSoup,
    cutoff: date,
) -> dict[str, Any]:
    identity = _clean(listed["identity"])
    root = soup.select_one(".subContentsArea .tbView")
    if root is None:
        raise NamdongContractError(
            f"library {catalogue.key}/{listed['branch_code']}/{identity}: detail root missing"
        )
    fields = _table_fields(root)
    required = {"행사명", "접수기간", "강좌상태", "대상", "교육기간", "정원"}
    if not required <= set(fields):
        raise NamdongContractError(f"library {catalogue.key}/{identity}: detail fields missing")
    if _clean(fields["행사명"]) != _clean(listed["title"]):
        raise NamdongContractError(f"library {catalogue.key}/{identity}: title mismatch")
    if _dates(fields["교육기간"], context=f"library {catalogue.key}/{identity} detail education") != (
        listed["start"],
        listed["end"],
    ):
        raise NamdongContractError(f"library {catalogue.key}/{identity}: education mismatch")
    if _dates(fields["접수기간"], context=f"library {catalogue.key}/{identity} detail apply") != (
        listed["apply_start"],
        listed["apply_end"],
    ):
        raise NamdongContractError(f"library {catalogue.key}/{identity}: apply mismatch")
    status = _clean(listed["status"])
    detail_state = _clean(fields["강좌상태"])
    reception_ok = any(
        detail_state == token or detail_state.startswith(f"{token} ")
        for token in _LIBRARY_DETAIL_STATUS[status]
    )
    closed_capacity_override = (
        status == "CLOSED"
        and any(
            detail_state == token or detail_state.startswith(f"{token} ")
            for token in _LIBRARY_DETAIL_STATUS["OPEN"]
        )
        and int(listed["capacity_current"]) >= int(listed["capacity_total"])
        and int(listed["waitlist_current"]) >= int(listed["waitlist_total"])
    )
    event_ok = any(
        detail_state == token or detail_state.endswith(f" {token}")
        for token in _LIBRARY_DETAIL_EVENT_STATUS[_clean(listed["event_status_raw"])]
    )
    if (not reception_ok and not closed_capacity_override) or not event_ok:
        raise NamdongContractError(f"library {catalogue.key}/{identity}: status mismatch")
    if not _clean(fields["정원"]).isdigit() or int(_clean(fields["정원"])) != int(
        listed["capacity_total"]
    ):
        raise NamdongContractError(f"library {catalogue.key}/{identity}: capacity mismatch")
    scripts = " ".join(x.get_text(" ", strip=False) for x in soup.select("script"))
    agency_matches = set(re.findall(r"var\s+ac\s*=\s*['\"](\d+)['\"]", scripts))
    if agency_matches != {_clean(listed["branch_code"])}:
        raise NamdongContractError(f"library {catalogue.key}/{identity}: branch identity mismatch")

    visible = False
    actionable = False
    contract = "no_active_control"
    application_url = ""
    buttons = [
        node
        for node in soup.select(".subContentsArea a.btn2")
        if _clean(node.get_text(" ", strip=True)) != "목록"
    ]
    if len(buttons) != 1:
        raise NamdongContractError(f"library {catalogue.key}/{identity}: state control changed")
    button = buttons[0]
    button_text = _clean(button.get_text(" ", strip=True))
    button_href = _clean(button.get("href"))
    if status == "OPEN":
        required_script = (
            catalogue.application_path in scripts
            and 'addParam("IDX", obj.parent().find("#IDX").val())' in scripts
            and 'addParam("PARENTS_IDX", obj.parent().find("#IDX").val())' in scripts
            and 'addParam("PARENTS_AGENCY_CD", obj.parent().find("#AGENCY_CD").val())' in scripts
        )
        if not required_script:
            raise NamdongContractError(
                f"library {catalogue.key}/{identity}: identity-bound application handler changed"
            )
        login_gate = (
            button_text == "로그인"
            and button_href
            == "/ndglib/usr/member/memberLogin.do?mnid=mn07&mnidx=154"
        )
        direct_control = (
            button_text == "접수신청"
            and button_href == "#this"
            and _clean(button.get("name")) == "apply"
        )
        if direct_control:
            parent = button.parent
            hidden = {
                _clean(node.get("id")): _clean(node.get("value"))
                for node in parent.select("input[type='hidden'][id]")
            }
            if (
                hidden.get("IDX") != identity
                or hidden.get("AGENCY_CD") != _clean(listed["branch_code"])
                or hidden.get("AGENCY_NAME") != _clean(listed["branch"])
                or not hidden.get("BBS_ID", "").startswith("PROGRAM")
            ):
                raise NamdongContractError(
                    f"library {catalogue.key}/{identity}: direct control identity changed"
                )
        elif not login_gate:
            raise NamdongContractError(
                f"library {catalogue.key}/{identity}: application gate changed"
            )
        visible = True
        actionable = True
        contract = (
            "visible_identity_bound_apply+identity_bound_write_handler"
            if direct_control
            else "identity_detail+authenticated_identity_bound_write_handler"
        )
        application_url = _clean(listed["raw_url"])
    else:
        expected = "접수대기" if status == "SCHEDULED" else "접수종료"
        inactive_sentinel = button_text == expected and button_href == "#"
        closed_login_gate = (
            status == "CLOSED"
            and button_text == "로그인"
            and button_href
            == "/ndglib/usr/member/memberLogin.do?mnid=mn07&mnidx=154"
        )
        if not inactive_sentinel and not closed_login_gate:
            raise NamdongContractError(
                f"library {catalogue.key}/{identity}: inactive state control changed"
            )
        visible = closed_login_gate
        contract = (
            (
                "closed_list_capacity_overrides_open_detail+generic_login_gate"
                if closed_capacity_override
                else "closed_list_overrides_generic_login_gate"
            )
            if closed_login_gate
            else ("scheduled_sentinel" if status == "SCHEDULED" else "closed_sentinel")
        )

    if status == "SCHEDULED" and cutoff > listed["apply_start"]:
        raise NamdongContractError(f"library {catalogue.key}/{identity}: scheduled date contradiction")
    if status == "OPEN" and not (listed["apply_start"] <= cutoff <= listed["apply_end"]):
        raise NamdongContractError(f"library {catalogue.key}/{identity}: open date contradiction")

    branch = _clean(listed["branch"])
    title = _clean(listed["title"])
    raw_url = _clean(listed["raw_url"])
    return {
        "provider": NAMDONG_PROVIDER,
        "provider_course_id": (
            f"{NAMDONG_PROVIDER}:library:{catalogue.key}:"
            f"{listed['branch_code']}:{identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": catalogue.label,
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if actionable else "INFO_ONLY",
        "application_method": "온라인(로그인)" if actionable else "",
        "application_methods": ["온라인"] if actionable else [],
        "reservation_available": actionable,
        "status": status,
        "fee": _clean(fields.get("수강료")),
        "fee_amount": (
            int(_clean(fields["수강료"]))
            if _clean(fields.get("수강료")).isdigit()
            else None
        ),
        "period": f"{listed['start'].isoformat()} ~ {listed['end'].isoformat()}",
        "start_date": listed["start"].isoformat(),
        "end_date": listed["end"].isoformat(),
        "apply_period": _clean(fields["접수기간"]),
        "schedule_raw": _clean(fields.get("교육시간")),
        "capacity": _clean(fields["정원"]),
        "capacity_current": int(listed["capacity_current"]),
        "capacity_total": int(listed["capacity_total"]),
        "waitlist_current": int(listed["waitlist_current"]),
        "waitlist_total": int(listed["waitlist_total"]),
        "target": _clean(fields["대상"]),
        "venue": _clean(fields.get("장소")) or branch,
        "venue_name": _clean(fields.get("장소")) or branch,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": NAMDONG_PARSER,
        "municipality_code": NAMDONG_MUNICIPALITY_CODE,
        "municipality_full_name": NAMDONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_kind": "library",
            "source_catalogue": catalogue.key,
            "source_category": catalogue.label,
            "source_branch_code": _clean(listed["branch_code"]),
            "source_branch": branch,
            "list_page": int(listed["list_page"]),
            "source_status": _clean(listed["status_raw"]),
            "source_apply_period": _clean(listed["apply_period"]),
            "source_education_period": _clean(listed["education_period"]),
            "source_institution": branch,
            "detail_verified": True,
            "visible_application_control_present": visible,
            "actionable_application_control_present": actionable,
            "application_control_contract": contract,
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden detail/PII keys persisted")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
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
        key = _clean(row.get("provider_course_id"))
        if key and key not in seen:
            seen.add(key)
            output.append(row)
    return output


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "configured_collection_error": error,
        "source_cap_reached": False,
        "pagination_complete": False,
        "snapshot_complete": False,
        "application_controls_complete": False,
        "list_requests": 0,
        "http_attempts": 0,
        "retry_count": 0,
        "waf_retry_count": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "cancelled_count": 0,
        "detail_pages": 0,
        "returned_count": 0,
        "status_counts": {},
        "branch_counts": {},
        "source_counts": {},
        "lifelong_category_counts": {},
        "lifelong_category_pages": {},
        "library_catalogue_counts": {},
        "library_catalogue_pages": {},
        "verified_tls": True,
        "tls_contract": "system_trust_plus_official_aia_intermediate",
        "aia_intermediate_sha256": NAMDONG_AIA_INTERMEDIATE_SHA256,
        "audited_leaf_sha256_2026_07_22": NAMDONG_LEAF_SHA256_AUDITED_2026_07_22,
        "excluded_official_sources": [dict(item) for item in NAMDONG_EXCLUDED_OFFICIAL_SOURCES],
        "covered_municipalities": [
            {
                "code": NAMDONG_MUNICIPALITY_CODE,
                "full_name": NAMDONG_MUNICIPALITY_NAME,
            }
        ],
    }


def collect_namdong_education(
    target: Any,
    *,
    today: Any = None,
    max_pages: int = 400,
    detail_limit: int = 200,
    timeout: int = 30,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete, current/future Namdong education snapshot."""

    meta = _base_meta()
    if not is_namdong_education_target(target):
        meta["configured_collection_error"] = "target does not match exact Namdong canonical owner"
        return [], NAMDONG_PARSER, meta
    try:
        cutoff = _as_date(today)
        if detail_limit < 0:
            raise NamdongContractError("detail_limit must be non-negative")
        budget = _Budget(max_pages)
        factory = session_factory or namdong_session_factory
        current_fetcher = fetcher or _request
        session = factory()
        requester = _Requester(
            session,
            current_fetcher,
            timeout,
            sleeper,
            session_factory=factory,
        )
        listed: list[dict[str, Any]] = []
        life_counts: dict[str, int] = {}
        life_pages: dict[str, int] = {}
        library_counts: dict[str, int] = {}
        library_pages: dict[str, int] = {}

        try:
            for category in NAMDONG_LIFELONG_CATEGORIES:
                budget.take()
                first = _parse_life_page(
                    requester.soup(
                        _life_list_url(category.code, 1), host=NAMDONG_BIZ_HOST
                    ),
                    category,
                    1,
                )
                category_rows: list[dict[str, Any]] = list(first.rows)
                if first.empty:
                    budget.take()
                    sentinel = _parse_life_page(
                        requester.soup(
                            _life_list_url(category.code, 2), host=NAMDONG_BIZ_HOST
                        ),
                        category,
                        2,
                    )
                    if not sentinel.empty:
                        raise NamdongContractError(
                            f"lifelong {category.code}: empty-category sentinel changed"
                        )
                    budget.take()
                    first_check = _parse_life_page(
                        requester.soup(
                            _life_list_url(category.code, 1), host=NAMDONG_BIZ_HOST
                        ),
                        category,
                        1,
                    )
                    if _page_signature(first_check) != _page_signature(first):
                        raise NamdongContractError(
                            f"lifelong {category.code}: empty first page drift"
                        )
                    last_page = 0
                else:
                    if first.actual != 1 or first.last < 1:
                        raise NamdongContractError(
                            f"lifelong {category.code}: first-page contract changed"
                        )
                    last_page = first.last
                    last_data = first
                    for page_number in range(2, last_page + 1):
                        budget.take()
                        parsed = _parse_life_page(
                            requester.soup(
                                _life_list_url(category.code, page_number),
                                host=NAMDONG_BIZ_HOST,
                            ),
                            category,
                            page_number,
                        )
                        if parsed.actual != page_number or parsed.last != last_page or parsed.empty:
                            raise NamdongContractError(
                                f"lifelong {category.code} page {page_number}: pagination drift"
                            )
                        if page_number < last_page and len(parsed.rows) != NAMDONG_PAGE_SIZE_LIFE:
                            raise NamdongContractError(
                                f"lifelong {category.code} page {page_number}: short interior page"
                            )
                        category_rows.extend(parsed.rows)
                        last_data = parsed
                    budget.take()
                    clamp = _parse_life_page(
                        requester.soup(
                            _life_list_url(category.code, last_page + 1),
                            host=NAMDONG_BIZ_HOST,
                        ),
                        category,
                        last_page + 1,
                    )
                    if _page_signature(clamp) != _page_signature(last_data):
                        raise NamdongContractError(
                            f"lifelong {category.code}: post-last clamp changed"
                        )
                    budget.take()
                    first_check = _parse_life_page(
                        requester.soup(
                            _life_list_url(category.code, 1), host=NAMDONG_BIZ_HOST
                        ),
                        category,
                        1,
                    )
                    if _page_signature(first_check) != _page_signature(first):
                        raise NamdongContractError(
                            f"lifelong {category.code}: first page changed during crawl"
                        )
                    if last_page > 1:
                        budget.take()
                        last_check = _parse_life_page(
                            requester.soup(
                                _life_list_url(category.code, last_page),
                                host=NAMDONG_BIZ_HOST,
                            ),
                            category,
                            last_page,
                        )
                        if _page_signature(last_check) != _page_signature(last_data):
                            raise NamdongContractError(
                                f"lifelong {category.code}: last page changed during crawl"
                            )
                keys = [row["identity"] for row in category_rows]
                if len(keys) != len(set(keys)):
                    raise NamdongContractError(
                        f"lifelong {category.code}: duplicate identity across pages"
                    )
                life_counts[category.code] = len(category_rows)
                life_pages[category.code] = last_page
                listed.extend(category_rows)

            for catalogue in NAMDONG_LIBRARY_CATALOGUES:
                for branch_code, branch_name in catalogue.branches:
                    ledger_key = f"{catalogue.key}:{branch_code}:{branch_name}"
                    budget.take()
                    first_soup = requester.soup(
                        _library_list_url(catalogue, branch_code, 1),
                        host=NAMDONG_LIBRARY_HOST,
                    )
                    first = _parse_library_page(
                        first_soup,
                        catalogue,
                        branch_code,
                        branch_name,
                        1,
                    )
                    catalogue_rows: list[dict[str, Any]] = list(first.rows)
                    if first.empty:
                        last_page = 0
                        budget.take()
                        sentinel = _parse_library_page(
                            requester.soup(
                                _library_list_url(catalogue, branch_code, 2),
                                host=NAMDONG_LIBRARY_HOST,
                            ),
                            catalogue,
                            branch_code,
                            branch_name,
                            2,
                        )
                        if not sentinel.empty:
                            raise NamdongContractError(
                                f"library {ledger_key}: empty sentinel changed"
                            )
                        budget.take()
                        first_check = _parse_library_page(
                            requester.soup(
                                _library_list_url(catalogue, branch_code, 1),
                                host=NAMDONG_LIBRARY_HOST,
                            ),
                            catalogue,
                            branch_code,
                            branch_name,
                            1,
                        )
                        if _page_signature(first_check) != _page_signature(first):
                            raise NamdongContractError(f"library {ledger_key}: empty page drift")
                    else:
                        last_page = first.last
                        if first.actual != 1 or last_page < 1:
                            raise NamdongContractError(
                                f"library {ledger_key}: first-page contract changed"
                            )
                        last_data = first
                        for page_number in range(2, last_page + 1):
                            budget.take()
                            parsed = _parse_library_page(
                                requester.soup(
                                    _library_list_url(catalogue, branch_code, page_number),
                                    host=NAMDONG_LIBRARY_HOST,
                                ),
                                catalogue,
                                branch_code,
                                branch_name,
                                page_number,
                                expected_last=last_page,
                            )
                            catalogue_rows.extend(parsed.rows)
                            last_data = parsed
                        budget.take()
                        sentinel = _parse_library_page(
                            requester.soup(
                                _library_list_url(catalogue, branch_code, last_page + 1),
                                host=NAMDONG_LIBRARY_HOST,
                            ),
                            catalogue,
                            branch_code,
                            branch_name,
                            last_page + 1,
                        )
                        if not sentinel.empty:
                            raise NamdongContractError(
                                f"library {ledger_key}: post-last page is not empty"
                            )
                        budget.take()
                        first_check = _parse_library_page(
                            requester.soup(
                                _library_list_url(catalogue, branch_code, 1),
                                host=NAMDONG_LIBRARY_HOST,
                            ),
                            catalogue,
                            branch_code,
                            branch_name,
                            1,
                            expected_last=last_page,
                        )
                        if _page_signature(first_check) != _page_signature(first):
                            raise NamdongContractError(
                                f"library {ledger_key}: first page changed during crawl"
                            )
                        if last_page > 1:
                            budget.take()
                            last_check = _parse_library_page(
                                requester.soup(
                                    _library_list_url(catalogue, branch_code, last_page),
                                    host=NAMDONG_LIBRARY_HOST,
                                ),
                                catalogue,
                                branch_code,
                                branch_name,
                                last_page,
                                expected_last=last_page,
                            )
                            if _page_signature(last_check) != _page_signature(last_data):
                                raise NamdongContractError(
                                    f"library {ledger_key}: last page changed during crawl"
                                )
                    keys = [row["identity"] for row in catalogue_rows]
                    if len(keys) != len(set(keys)):
                        raise NamdongContractError(
                            f"library {ledger_key}: duplicate identity across pages"
                        )
                    library_counts[ledger_key] = len(catalogue_rows)
                    library_pages[ledger_key] = last_page
                    listed.extend(catalogue_rows)

            unique_keys = [
                (
                    row["source_kind"],
                    row.get("category") or row.get("catalogue"),
                    row.get("branch_code", ""),
                    row["identity"],
                )
                for row in listed
            ]
            if len(unique_keys) != len(set(unique_keys)):
                raise NamdongContractError("cross-ledger identity collision")

            current_all = [row for row in listed if row["end"] >= cutoff]
            cancelled = [row for row in current_all if row.get("cancelled")]
            current = [row for row in current_all if not row.get("cancelled")]
            if any(
                row.get("apply_start") is None or row.get("apply_end") is None
                for row in current
            ):
                identities = ",".join(
                    row["identity"]
                    for row in current
                    if row.get("apply_start") is None or row.get("apply_end") is None
                )
                raise NamdongContractError(
                    f"current lifelong rows lack application dates: {identities}"
                )
            if len(current) > detail_limit:
                raise NamdongContractError(
                    f"detail_limit {detail_limit} below required {len(current)}"
                )
            rows: list[dict[str, Any]] = []
            for listed_row in current:
                detail_soup = requester.soup(
                    listed_row["raw_url"],
                    host=(
                        NAMDONG_BIZ_HOST
                        if listed_row["source_kind"] == "lifelong"
                        else NAMDONG_LIBRARY_HOST
                    ),
                )
                if listed_row["source_kind"] == "lifelong":
                    built = _life_detail(listed_row, detail_soup, cutoff)
                else:
                    catalogue = next(
                        item
                        for item in NAMDONG_LIBRARY_CATALOGUES
                        if item.key == listed_row["catalogue"]
                    )
                    built = _library_detail(listed_row, catalogue, detail_soup, cutoff)
                privacy = _privacy_errors(built)
                if privacy:
                    raise NamdongContractError(
                        f"{built['provider_course_id']}: {'; '.join(privacy)}"
                    )
                rows.append(built)
        finally:
            requester.close()

        collapsed = list((dedupe_rows or _dedupe_default)(rows))
        for row in collapsed:
            privacy = _privacy_errors(row)
            if privacy:
                raise NamdongContractError(
                    f"dedupe output {row.get('provider_course_id')}: {'; '.join(privacy)}"
                )
        collapsed.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        meta.update(
            {
                "pagination_complete": True,
                "snapshot_complete": True,
                "application_controls_complete": True,
                "list_requests": budget.used,
                "http_attempts": requester.http_attempts,
                "retry_count": requester.retry_count,
                "waf_retry_count": requester.waf_retry_count,
                "session_refresh_count": requester.session_refresh_count,
                "source_rows": len(listed),
                "current_source_count": len(current),
                "expired_count": len(listed) - len(current_all),
                "cancelled_count": len(cancelled),
                "detail_pages": len(current),
                "returned_count": len(collapsed),
                "status_counts": dict(Counter(row["status"] for row in collapsed)),
                "branch_counts": dict(Counter(row["branch"] for row in collapsed)),
                "source_counts": dict(Counter(row["source_kind"] for row in listed)),
                "lifelong_category_counts": life_counts,
                "lifelong_category_pages": life_pages,
                "library_catalogue_counts": library_counts,
                "library_catalogue_pages": library_pages,
            }
        )
        return collapsed, NAMDONG_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {exc}"
        if "max_pages" in str(exc):
            meta["source_cap_reached"] = True
        budget_value = locals().get("budget")
        requester_value = locals().get("requester")
        if isinstance(budget_value, _Budget):
            meta["list_requests"] = budget_value.used
        if isinstance(requester_value, _Requester):
            meta["http_attempts"] = requester_value.http_attempts
            meta["retry_count"] = requester_value.retry_count
            meta["waf_retry_count"] = requester_value.waf_retry_count
            meta["session_refresh_count"] = requester_value.session_refresh_count
        return [], NAMDONG_PARSER, meta


collect = collect_namdong_education


__all__ = [
    "NAMDONG_AIA_INTERMEDIATE_PEM",
    "NAMDONG_AIA_INTERMEDIATE_SHA256",
    "NAMDONG_CANONICAL_URL",
    "NAMDONG_CANDIDATE_ID",
    "NAMDONG_EXCLUDED_OFFICIAL_SOURCES",
    "NAMDONG_LIBRARY_CATALOGUES",
    "NAMDONG_LIFELONG_CATEGORIES",
    "NAMDONG_MUNICIPALITY_CODE",
    "NAMDONG_MUNICIPALITY_NAME",
    "NAMDONG_PARSER",
    "NAMDONG_PROVIDER",
    "NamdongContractError",
    "collect",
    "collect_namdong_education",
    "is_namdong_education_target",
    "is_target",
    "namdong_session_factory",
]
