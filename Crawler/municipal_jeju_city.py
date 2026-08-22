"""Fail-closed public programme collectors covering Jeju-si.

Jeju-si (5011000000) is an administrative city.  The Jeju Special
Self-Governing Province integrated reservation, resident-centre, public
library and agricultural ledgers are therefore valid owners for Jeju-si only
where an audited province branch is province-wide or has a Jeju-si address.
The city lifelong-learning, youth and starlight ledgers and the public Dream
Library ledger remain independent owners.

Only public list, JSON and current detail resources are read.  Login,
application, applicant-list, identity-verification, cancellation, payment,
file-download and application-state endpoints are never requested.  A
repository-managed session factory is required in production; raw requests
are available only through an explicit live-audit/test switch.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import html
import json
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


JEJU_CITY_MUNICIPALITY_CODE = "5011000000"
JEJU_CITY_MUNICIPALITY_NAME = "제주특별자치도 제주시"
JEJU_CITY_PARSER = "municipal_jeju_city_v1"

JEJU_INTEGRATED_PROVIDER = "MUNI_WWW_JEJU_GO_KR_2B65844D"
JEJU_INTEGRATED_CANDIDATE_ID = "MUNI_IR_D2A732A67398"
JEJU_INTEGRATED_URL = "https://www.jeju.go.kr/booking/edu/edu.htm"

JEJU_LIFELONG_PROVIDER = "MUNI_WWW_JEJUSI_GO_KR_72D06B44"
JEJU_LIFELONG_CANDIDATE_ID = "MUNI_IR_FBCDA95998C0"
JEJU_LIFELONG_URL = "https://www.jejusi.go.kr/qolup/info/lecture.do"

JEJU_YOUTH_PROVIDER = "MUNI_WWW_JEJUSI_GO_KR_A449522B"
JEJU_YOUTH_CANDIDATE_ID = "MUNI_IR_CD0681CB1B4F"
JEJU_YOUTH_URL = "https://www.jejusi.go.kr/youth/program/apply.do"

JEJU_RESIDENT_PROVIDER = "MUNI_WWW_JEJU_GO_KR_6E577892"
JEJU_RESIDENT_CANDIDATE_ID = "MUNI_IR_1CB1143C3BAF"
JEJU_RESIDENT_URL = (
    "https://www.jeju.go.kr/jumin/program/list.htm?organPrefix=1001"
)

JEJU_LIBRARY_PROVIDER = "MUNI_WWW_JEJU_GO_KR_310502FA"
JEJU_LIBRARY_CANDIDATE_ID = "MUNI_IR_CADC7A7B6524"
JEJU_LIBRARY_URL = "https://www.jeju.go.kr/lib/event/program/add.htm"

JEJU_AGRICULTURE_PROVIDER = "MUNI_AGRI_JEJU_GO_KR_84F944BE"
JEJU_AGRICULTURE_CANDIDATE_ID = "MUNI_IR_8C47A0959FA1"
JEJU_AGRICULTURE_URL = (
    "https://agri.jeju.go.kr/agri/farminginfo/education.htm"
)

JEJU_STAR_PROVIDER = "MUNI_WWW_JEJUSI_GO_KR_F9643CD9"
JEJU_STAR_CANDIDATE_ID = "MUNI_IR_C7C6E257D173"
JEJU_STAR_URL = "https://www.jejusi.go.kr/star/intro/application.do"

JEJU_DREAM_LIBRARY_PROVIDER = "MUNI_JJDREAMLIB_OR_KR_1A8AAB7D"
JEJU_DREAM_LIBRARY_CANDIDATE_ID = "MUNI_IR_D7C6778DE6A1"
JEJU_DREAM_LIBRARY_URL = "https://jjdreamlib.or.kr/class/all.htm"

JEJU_EXECUTING_TARGETS = (
    (JEJU_INTEGRATED_PROVIDER, JEJU_INTEGRATED_URL),
    (JEJU_LIFELONG_PROVIDER, JEJU_LIFELONG_URL),
    (JEJU_YOUTH_PROVIDER, JEJU_YOUTH_URL),
    (JEJU_RESIDENT_PROVIDER, JEJU_RESIDENT_URL),
    (JEJU_LIBRARY_PROVIDER, JEJU_LIBRARY_URL),
    (JEJU_AGRICULTURE_PROVIDER, JEJU_AGRICULTURE_URL),
    (JEJU_STAR_PROVIDER, JEJU_STAR_URL),
    (JEJU_DREAM_LIBRARY_PROVIDER, JEJU_DREAM_LIBRARY_URL),
)

JEJU_BOOKING_API = "https://www.jeju.go.kr/tool/bookingportal/program.jsp"
JEJU_BOOKING_REGISTRY_API = "https://www.jeju.go.kr/api/bookingportal/organ"

JEJU_INTEGRATED_BRANCHES: tuple[tuple[int, str], ...] = (
    (1, "설문대여성문화센터"),
    (45, "민속자연사박물관"),
    (46, "돌문화공원"),
    (47, "해녀박물관"),
    (48, "공공정책연수원"),
    (65, "제주문학관"),
    (67, "문화예술진흥원"),
    (70, "제주특별자치도"),
    (73, "자치경찰단"),
    (74, "제주어교육플랫폼"),
)

JEJU_LIBRARY_BRANCHES: tuple[tuple[int, str], ...] = (
    (49, "한라도서관"),
    (50, "우당도서관"),
    (51, "탐라도서관"),
    (52, "제주시기적도서관"),
    (53, "애월도서관"),
    (54, "조천읍도서관"),
    (55, "한경도서관"),
)

JEJU_RESIDENT_BRANCHES: tuple[tuple[int, str, str], ...] = (
    (2, "일도1동", "주민자치센터(일도1동)"),
    (3, "일도2동", "주민자치센터(일도2동)"),
    (4, "이도1동", "주민자치센터(이도1동)"),
    (5, "이도2동", "주민자치센터(이도2동)"),
    (6, "삼도1동", "주민자치센터(삼도1동)"),
    (7, "삼도2동", "주민자치센터(삼도2동)"),
    (8, "용담1동", "주민자치센터(용담1동)"),
    (9, "용담2동", "주민자치센터(용담2동)"),
    (10, "건입동", "주민자치센터(건입동)"),
    (11, "화북동", "주민자치센터(화북동)"),
    (12, "삼양동", "주민자치센터(삼양동)"),
    (13, "봉개동", "주민자치센터(봉개동)"),
    (14, "아라동", "주민자치센터(아라동)"),
    (15, "오라동", "주민자치센터(오라동)"),
    (16, "연동", "주민자치센터(연동)"),
    (17, "노형동", "주민자치센터(노형동)"),
    (18, "외도동", "주민자치센터(외도동)"),
    (19, "이호동", "주민자치센터(이호동)"),
    (20, "도두동", "주민자치센터(도두동)"),
    (21, "한림읍", "주민자치센터(한림읍)"),
    (22, "애월읍", "주민자치센터(애월읍)"),
    (23, "구좌읍", "주민자치센터(구좌읍)"),
    (24, "조천읍", "주민자치센터(조천읍)"),
    (25, "한경면", "주민자치센터(한경면)"),
    (26, "추자면", "주민자치센터(추자면)"),
    (27, "우도면", "주민자치센터(우도면)"),
)

JEJU_YOUTH_BRANCHES = (
    "청소년수련관",
    "한림 청소년문화의집",
    "추자 청소년문화의집",
    "구좌 청소년문화의집",
    "도남 청소년문화의집",
    "이도1동 청소년문화의집",
    "화북 청소년문화의집",
    "용담1동 청소년문화의집",
    "도평 청소년문화의집",
    "아라 청소년문화의집",
    "삼도1동 청소년문화의집",
    "애월 청소년문화의집",
    "조천 청소년문화의집",
    "노형 청소년문화의집",
)

JEJU_AGRICULTURE_BRANCHES: tuple[tuple[str, str, str], ...] = (
    (
        "jeju",
        "https://agri.jeju.go.kr/agri/farminginfo/education/jeju.htm",
        "제주농업기술센터",
    ),
    (
        "dongbu",
        "https://agri.jeju.go.kr/agri/farminginfo/education/dongbu.htm",
        "동부농업기술센터",
    ),
    (
        "seobu",
        "https://agri.jeju.go.kr/agri/farminginfo/education/seobu.htm",
        "서부농업기술센터",
    ),
)

JEJU_LIFELONG_BRANCH = "제주시 평생학습관"
JEJU_STAR_BRANCH = "제주별빛누리공원"
JEJU_DREAM_LIBRARY_BRANCH = "제주꿈바당어린이도서관"

JEJU_CITY_RESERVE_LANDING_URL = "https://www.jejusi.go.kr/field/reserve.do"
JEJU_DAMOA_ALIAS_URL = (
    "https://damoa.jeju.kr/lecture/lecture.htm?mode=all&act=index"
)
JEJU_DAMOA_OLD_ALIAS_URL = "https://jiles.or.kr/damoa/online.htm"
JEJU_LLLCARD_INFO_URL = "https://www.lllcard.kr/reg/jeju/main/mainView.do"
JEJU_EDUCATION_OFFICE_URL = "https://org.jje.go.kr/reserve/index.jje"
JEJU_DOMIN_INFO_URL = "https://www.jejudomin.kr/ko/info/educls2"

JEJU_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "province_integrated": {
        "provider": JEJU_INTEGRATED_PROVIDER,
        "url": JEJU_INTEGRATED_URL,
        "decision": (
            "province owner encompasses Jeju administrative city; collect only "
            "audited Jeju-si/province-wide education branches"
        ),
    },
    "city_lifelong": {
        "provider": JEJU_LIFELONG_PROVIDER,
        "url": JEJU_LIFELONG_URL,
        "decision": "direct Jeju-si course ledger; canonical over Damoa alias",
    },
    "city_youth": {
        "provider": JEJU_YOUTH_PROVIDER,
        "url": JEJU_YOUTH_URL,
        "decision": "independent Jeju-si youth programme ledger",
    },
    "province_resident": {
        "provider": JEJU_RESIDENT_PROVIDER,
        "url": JEJU_RESIDENT_URL,
        "decision": (
            "province shared system scoped by official organPrefix=1001; direct "
            "list is canonical because booking API contains misassigned rows"
        ),
    },
    "province_libraries": {
        "provider": JEJU_LIBRARY_PROVIDER,
        "url": JEJU_LIBRARY_URL,
        "decision": "province owner restricted to seven official Jeju-si libraries",
    },
    "province_agriculture": {
        "provider": JEJU_AGRICULTURE_PROVIDER,
        "url": JEJU_AGRICULTURE_URL,
        "decision": (
            "province owner restricted to Jeju, Dongbu and Seobu centres with "
            "Jeju-si addresses; headquarters and Seogwipo centre excluded"
        ),
    },
    "city_starlight": {
        "provider": JEJU_STAR_PROVIDER,
        "url": JEJU_STAR_URL,
        "decision": "independent city event/education application ledger",
    },
    "dream_library": {
        "provider": JEJU_DREAM_LIBRARY_PROVIDER,
        "url": JEJU_DREAM_LIBRARY_URL,
        "decision": "independent public children-library programme ledger",
    },
    "city_reserve_landing": {
        "url": JEJU_CITY_RESERVE_LANDING_URL,
        "decision": "directory alias only; exact owner ledgers are canonical",
    },
    "damoa": {
        "urls": (JEJU_DAMOA_ALIAS_URL, JEJU_DAMOA_OLD_ALIAS_URL),
        "decision": "aggregator/obsolete alias; exclude duplicate and non-public rows",
    },
    "voucher": {
        "url": JEJU_LLLCARD_INFO_URL,
        "decision": "voucher information page, not a course ledger",
    },
    "education_office": {
        "url": JEJU_EDUCATION_OFFICE_URL,
        "decision": "separate provincial education-office owner",
    },
    "jeju_domin": {
        "url": JEJU_DOMIN_INFO_URL,
        "decision": "static information page, not the current application ledger",
    },
}

# Live evidence captured on 2026-07-23 (Asia/Seoul).  Identity hashes use
# newline-joined lexicographically sorted source identities.
JEJU_LIVE_AUDIT_BASELINE: Mapping[str, Mapping[str, Any]] = {
    "integrated": {"source_total": 4669, "current_count": 63,
                   "source_identity_sha256": "636de33faf1fbb92ab61c6a37a5155c2a666f76baddaab86ecf5564ed6352501"},
    "lifelong": {"source_total": 486, "current_count": 68,
                 "source_identity_sha256": "a8426e068eb0e3fd248a264ba8b72045be05966c5b1ebba09010569b9c64812d"},
    "youth": {"source_total": 171, "current_count": 9,
              "source_identity_sha256": "c68e591fe5716274d2f60c98f2cba656216436fdfef4709962b4e3ffeb8f1ae8"},
    "resident": {"source_total": 1748, "current_count": 0,
                 "source_identity_sha256": "c97390c5d849a6f77a51918cba24cc0f11d1646729a1367d52a4046f87500127"},
    "library": {"source_total": 1564, "current_count": 42,
                "source_identity_sha256": "f7d032db95c416c696736df7acb2fb543e9c4fd5eebc21e87fca753eb03da1f1"},
    "agriculture": {"source_total": 485, "current_count": 16,
                    "source_identity_sha256": "12770c6826d24b8397200a5cf5d2fd64967c33b4d3050b251bd284f0a5443b6b"},
    "star": {"source_total": 162, "current_count": 2,
             "source_identity_sha256": "7394b806ef772831e59c1a9cd59eca89c40a66e7e4505290babee9d1284e3d68"},
    "dream_library": {"source_total": 514, "current_count": 13,
                      "source_identity_sha256": "3076ea4070f318aa0f6fafde6f1f95915146072f03f4ed54fb25cab2bbafaa4e"},
}

JEJU_DEFAULT_MAX_PAGES = 120
JEJU_DEFAULT_DETAIL_LIMIT = 100
JEJU_DEFAULT_MAX_REQUESTS = 1_000
JEJU_SESSION_REQUEST_LIMIT = 80
JEJU_BOOKING_PAGE_SIZE = 500

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_SHORT_DATE_RE = re.compile(
    r"(?<!\d)(\d{2})\s*[.]\s*(\d{1,2})\s*[.]\s*(\d{1,2})(?!\d)"
)
_KOREAN_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"
)
_COMPACT_DATE_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_ALLOWED_HOSTS = frozenset({
    "www.jeju.go.kr", "www.jejusi.go.kr", "agri.jeju.go.kr", "jjdreamlib.or.kr"
})
_FORBIDDEN_PATH_MARKERS = (
    "/sso/", "/login", "/logout", "/payment", "/cancel", "/file/",
    "/filedown", "/filedownload", "/file_download", "/download",
    "/attachment", "/attach/", "/upload", "/auth", "/cert", "/member",
    "/mypage", "/my/", "/user/", "/edcreqmem", "/checkuserappl",
    "/applicant",
)
_MUTATING_QUERY_KEYS = frozenset(
    {"act", "action", "cmd", "command", "method", "mode", "operation", "process"}
)
_FORBIDDEN_ACTION_MARKERS = (
    "apply", "attach", "cancel", "delete", "download", "file", "insert",
    "join", "login", "payment", "register", "save", "submit", "update",
    "upload", "write",
)


class JejuCityContractError(ValueError):
    """Raised when an audited official-source contract changes."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in _clean(value) if ch.isalnum())


def _url_value(value: Any) -> str:
    # ``html.unescape`` must not be used on an already parsed href: a query
    # such as ``&currentPageNo`` otherwise starts with the valid ``&curren``
    # entity and is silently corrupted to ``¤tPageNo``.
    return str(value or "").strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _positive(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise JejuCityContractError(f"{name} must be a positive integer") from exc
    if result < 1:
        raise JejuCityContractError(f"{name} must be a positive integer")
    return result


def _exact_target(url: str, canonical: str) -> bool:
    parsed, wanted = urlparse(url), urlparse(canonical)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == wanted.hostname
        and parsed.port is None
        and parsed.path == wanted.path
        and parse_qs(parsed.query, keep_blank_values=True)
        == parse_qs(wanted.query, keep_blank_values=True)
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


_TARGETS = dict(JEJU_EXECUTING_TARGETS)
_OWNERS = {
    JEJU_INTEGRATED_PROVIDER: "integrated",
    JEJU_LIFELONG_PROVIDER: "lifelong",
    JEJU_YOUTH_PROVIDER: "youth",
    JEJU_RESIDENT_PROVIDER: "resident",
    JEJU_LIBRARY_PROVIDER: "library",
    JEJU_AGRICULTURE_PROVIDER: "agriculture",
    JEJU_STAR_PROVIDER: "star",
    JEJU_DREAM_LIBRARY_PROVIDER: "dream_library",
}


def is_jeju_city_education_target(target: Any) -> bool:
    canonical = _TARGETS.get(_provider(target))
    return bool(canonical and _exact_target(_target_url(target), canonical))


is_target = is_jeju_city_education_target


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    })
    return session


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _decoded_body(response: Any) -> str:
    content = getattr(response, "content", b"")
    if isinstance(content, str):
        return content
    if not content and hasattr(response, "text"):
        return str(response.text)
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return bytes(content).decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return bytes(content).decode("utf-8", errors="replace")


def _request_query(url: str, params: Any) -> Mapping[str, list[str]]:
    query = parse_qs(urlparse(url).query, keep_blank_values=True)
    if isinstance(params, Mapping):
        for key, value in params.items():
            values = value if isinstance(value, (list, tuple)) else [value]
            query[str(key)] = [str(item) for item in values]
    return query


def _guard_request(url: str, params: Any = None) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.port is not None or parsed.username or parsed.password
        or parsed.fragment
    ):
        raise JejuCityContractError("request escaped an audited official host")
    path = parsed.path
    for _ in range(8):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    else:
        raise JejuCityContractError("request path is excessively encoded")
    path = path.lower()
    if any(marker in path for marker in _FORBIDDEN_PATH_MARKERS):
        raise JejuCityContractError("application/applicant endpoint invocation is forbidden")
    query = _request_query(url, params)
    action = " ".join(
        value
        for key, values in query.items()
        if key.lower() in _MUTATING_QUERY_KEYS
        for value in values
    ).lower()
    if any(marker in action for marker in _FORBIDDEN_ACTION_MARKERS):
        raise JejuCityContractError("application endpoint invocation is forbidden")


def _validate_response(response: Any, expected_url: str) -> None:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise JejuCityContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise JejuCityContractError("redirects are not accepted")
    final = _clean(getattr(response, "url", ""))
    if final:
        got, wanted = urlparse(final), urlparse(expected_url)
        if (got.scheme, got.hostname, got.port, got.path) != (
            "https", wanted.hostname, None, wanted.path
        ):
            raise JejuCityContractError("response escaped the audited endpoint")
    body = _decoded_body(response)
    blocked = (
        "403 Forbidden", "Access Denied", "WebKnight Application Firewall Alert",
        "비정상적으로 빠른 요청", "서비스 이용이 제한", "captcha",
    )
    if any(marker.lower() in body.lower() for marker in blocked):
        raise JejuCityContractError("official source rate-limit/WAF response")


class _Runner:
    def __init__(
        self, factory: SessionFactory, timeout: int, max_requests: int, sleeper: Sleeper
    ) -> None:
        self.factory = factory
        self.timeout = timeout
        self.max_requests = max_requests
        self.sleeper = sleeper
        self.session: Any = None
        self.session_requests = JEJU_SESSION_REQUEST_LIMIT
        self.physical_requests = 0
        self.retry_count = 0
        self.sessions_created = 0
        self.requested_urls: list[str] = []

    def close(self) -> None:
        _close(self.session)
        self.session = None

    def _new(self) -> None:
        self.close()
        self.session = self.factory()
        self.sessions_created += 1
        self.session_requests = 0
        headers = getattr(self.session, "headers", None)
        if hasattr(headers, "update"):
            headers.update({"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})

    def get(self, url: str, **kwargs: Any) -> Any:
        _guard_request(url, kwargs.get("params"))
        last: Optional[Exception] = None
        for attempt in range(2):
            if self.physical_requests >= self.max_requests:
                raise JejuCityContractError(
                    f"max_requests cap {self.max_requests} exhausted"
                )
            if self.session is None or self.session_requests >= JEJU_SESSION_REQUEST_LIMIT:
                self._new()
            self.physical_requests += 1
            self.session_requests += 1
            self.requested_urls.append(url)
            try:
                response = self.session.get(
                    url, timeout=self.timeout, allow_redirects=False, **kwargs
                )
                _validate_response(response, url)
                return response
            except Exception as exc:
                last = exc
                if attempt == 0:
                    self.retry_count += 1
                    self._new()
                    self.sleeper(0.2)
        raise JejuCityContractError(_clean(last) or "request failed")

    def soup(self, url: str, **kwargs: Any) -> BeautifulSoup:
        return BeautifulSoup(_decoded_body(self.get(url, **kwargs)), "lxml")

    def json(self, url: str, **kwargs: Any) -> Any:
        response = self.get(url, **kwargs)
        try:
            value = response.json()
        except Exception:
            try:
                value = json.loads(_decoded_body(response))
            except Exception as exc:
                raise JejuCityContractError("official JSON response is invalid") from exc
        return value


def _date_tokens(value: Any, *, short_year: bool = False) -> list[date]:
    result: list[date] = []
    for parts in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(*(int(part) for part in parts)))
        except ValueError:
            pass
    if short_year:
        for year, month, day in _SHORT_DATE_RE.findall(_clean(value)):
            try:
                result.append(date(2000 + int(year), int(month), int(day)))
            except ValueError:
                pass
    for parts in _KOREAN_DATE_RE.findall(_clean(value)):
        try:
            item = date(*(int(part) for part in parts))
            if item not in result:
                result.append(item)
        except ValueError:
            pass
    for parts in _COMPACT_DATE_RE.findall(_clean(value)):
        try:
            item = date(*(int(part) for part in parts))
            if item not in result:
                result.append(item)
        except ValueError:
            pass
    return result


def _date_range(
    value: Any, *, short_year: bool = False, allow_single: bool = False
) -> tuple[str, str, str, bool]:
    values = _date_tokens(value, short_year=short_year)
    if len(values) == 1 and allow_single:
        item = values[0].isoformat()
        return item, item, item, False
    if len(values) < 2:
        return "", "", "", True
    start, end = values[0], values[1]
    if end < start:
        return "", "", "", True
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}", False


def _iso_date(value: Any) -> str:
    try:
        return date.fromisoformat(_clean(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise JejuCityContractError(f"invalid official ISO date: {_clean(value)}") from exc


def _numbers(value: Any) -> list[int]:
    return [int(raw.replace(",", "")) for raw in re.findall(r"\d[\d,]*", _clean(value))]


def _sanitize(value: Any, limit: int = 4_000) -> str:
    text = _clean(value)
    text = _RESIDENT_ID_RE.sub("", text)
    text = _PHONE_RE.sub("", text)
    text = _EMAIL_RE.sub("", text)
    return _clean(text)[:limit]


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256(
        "\n".join(sorted(_clean(value) for value in values)).encode("utf-8")
    ).hexdigest()


def _branch_code(owner: str, branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"JEJU_{owner.upper()}_{digest}"


def _looks_experience(*values: Any) -> bool:
    text = " ".join(_clean(value) for value in values)
    return any(marker in text for marker in ("체험", "견학", "탐방"))


def _common_row(
    provider: str,
    identity: str,
    title: str,
    branch: str,
    raw_url: str,
    owner: str,
    *,
    experience: bool = False,
) -> dict[str, Any]:
    domain = "체험" if experience else "교육·강좌"
    service = "체험" if experience else "공공강좌"
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:course:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": branch,
        "branch_code": _branch_code(owner, branch),
        "provider_organizer": branch,
        "raw_url": raw_url,
        "collection_category": "공공예약",
        "domain_category": domain,
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": service,
        "service_group_policy": "locked",
        "classification_locked": True,
        "program_type": "체험" if experience else "강좌",
        "region": JEJU_CITY_MUNICIPALITY_NAME,
        "municipality_code": JEJU_CITY_MUNICIPALITY_CODE,
        "municipality_full_name": JEJU_CITY_MUNICIPALITY_NAME,
    }


def _base_meta(owner: str, error: str = "") -> dict[str, Any]:
    return {
        "owner": owner,
        "owner_scope": "Jeju Special Self-Governing Province encompasses Jeju-si"
        if owner in {"integrated", "resident", "library", "agriculture"}
        else "Jeju-si direct/public owner",
        "pages": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "source_current_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": error,
        "application_endpoints_called": 0,
        "applicant_endpoints_called": 0,
    }


def _status(value: Any) -> str:
    text = _norm(value)
    if any(marker in text for marker in ("접수중", "모집중", "신청중", "접수가능")):
        return "OPEN"
    if any(marker in text for marker in ("접수대기", "모집예정", "접수예정", "준비중")):
        return "SCHEDULED"
    if any(marker in text for marker in (
        "마감", "종료", "완료", "진행중", "교육중", "접수불가", "접수종료",
        "취소", "폐강"
    )):
        return "CLOSED"
    return "UNKNOWN"


def _pairs(container: Tag, item_selector: str = "dl") -> dict[str, str]:
    result: dict[str, str] = {}
    for item in container.select(item_selector):
        label = item.select_one("dt")
        value = item.select_one("dd")
        if label is not None and value is not None:
            result[_norm(label.get_text(" ", strip=True))] = _clean(
                value.get_text(" ", strip=True)
            )
    return result


def _booking_status(payload: Mapping[str, Any]) -> str:
    join = _clean(payload.get("joinStat")).upper()
    if join in {"JOIN", "WAIT", "POSSIBLE"}:
        return "OPEN"
    if join in {"BEFORE", "READY"}:
        return "SCHEDULED"
    if join in {"END", "CLOSE", "CLOSED"}:
        return "CLOSED"
    raise JejuCityContractError(f"unknown booking joinStat: {join}")


def _booking_row(
    payload: Mapping[str, Any], owner: str, expected_organ: int, expected_branch: str
) -> dict[str, Any]:
    try:
        identity = str(int(payload.get("seq")))
        organ = int(payload.get("organ"))
    except (TypeError, ValueError) as exc:
        raise JejuCityContractError("booking JSON identity/organ changed") from exc
    if organ != expected_organ:
        raise JejuCityContractError(
            f"booking {identity}: expected organ {expected_organ}, got {organ}"
        )
    bean = payload.get("organBean")
    if not isinstance(bean, Mapping):
        raise JejuCityContractError(f"booking {identity}: organBean missing")
    branch = _clean(bean.get("name"))
    if branch != expected_branch or int(bean.get("seq") or 0) != expected_organ:
        raise JejuCityContractError(
            f"booking {identity}: official branch changed from {expected_branch} to {branch}"
        )
    if any(payload.get(flag) is not True for flag in ("use", "display", "accept")):
        raise JejuCityContractError(f"booking {identity}: non-public row leaked into API")
    title = _clean(payload.get("title"))
    if not title:
        raise JejuCityContractError(f"booking {identity}: title missing")
    start = _iso_date(payload.get("eduStart"))
    end = _iso_date(payload.get("eduEnd"))
    date_anomaly = end < start
    apply_start = _iso_date(payload.get("appStartDate"))
    apply_end = _iso_date(payload.get("appEndDate"))
    apply_date_anomaly = apply_end < apply_start

    provider = JEJU_INTEGRATED_PROVIDER if owner == "integrated" else JEJU_LIBRARY_PROVIDER
    canonical = JEJU_INTEGRATED_URL if owner == "integrated" else JEJU_LIBRARY_URL
    raw_url = canonical + "?" + urlencode({"act": "view", "program": identity})
    sep = _clean(payload.get("sep"))
    experience = owner == "integrated" and _looks_experience(title, sep)
    row = _common_row(
        provider, identity, title, branch, raw_url, owner, experience=experience
    )
    total = payload.get("total")
    current = payload.get("acceptCount")
    wait = payload.get("waitCount")
    try:
        capacity_total = int(total) if total is not None else None
        capacity_current = int(current) if current is not None else None
        capacity_waiting = int(wait) if wait is not None else None
    except (TypeError, ValueError) as exc:
        raise JejuCityContractError(f"booking {identity}: capacity changed") from exc
    status = _booking_status(payload)
    if (date_anomaly or apply_date_anomaly) and status != "CLOSED":
        raise JejuCityContractError(f"booking {identity}: active row has reversed dates")
    row.update({
        "category": sep,
        "status": status,
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "schedule_raw": _clean(payload.get("eduTime")),
        "venue": _clean(payload.get("location")),
        "target": _clean(payload.get("target")) or _clean(payload.get("targetInput")),
        "price": _clean(payload.get("pay")),
        "capacity_total": capacity_total,
        "capacity_current": capacity_current,
        "capacity_waiting": capacity_waiting,
        "reservation_available": status == "OPEN",
        "collection_type": "official_json+current_detail_html",
        "raw_fields": {
            "parser": JEJU_CITY_PARSER,
            "source_identity": identity,
            "organ_id": expected_organ,
            "source_module": "PROGRAM",
            "source_application_control_present": status == "OPEN",
            "source_date_anomaly": date_anomaly,
            "source_apply_date_anomaly": apply_date_anomaly,
        },
    })
    return _clean_row(row)


def _booking_detail(runner: _Runner, row: dict[str, Any], owner: str) -> None:
    soup = runner.soup(row["raw_url"])
    identity = row["raw_fields"]["source_identity"]
    root = soup.select_one(".booking-view")
    if root is None:
        raise JejuCityContractError(f"{owner} {identity}: detail contract missing")
    if owner == "integrated":
        title_node = root.select_one(":scope > h3")
        detail_text = root.get_text(" ", strip=True)
    else:
        table = root.select_one("table")
        caption = table.select_one("caption") if table is not None else None
        title_node = caption
        detail_text = table.get_text(" ", strip=True) if table is not None else ""
    if title_node is None or _norm(row["title"]) not in _norm(
        title_node.get_text(" ", strip=True)
    ):
        raise JejuCityContractError(f"{owner} {identity}: detail/list title mismatch")
    dates = {item.isoformat() for item in _date_tokens(detail_text)}
    if row["start_date"] not in dates or row["end_date"] not in dates:
        raise JejuCityContractError(f"{owner} {identity}: detail/list date mismatch")
    row["raw_fields"]["detail_verified"] = True


def _collect_booking_api(
    runner: _Runner,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    owner: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    branches = JEJU_INTEGRATED_BRANCHES if owner == "integrated" else JEJU_LIBRARY_BRANCHES
    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    branch_source_counts: Counter[str] = Counter({branch: 0 for _, branch in branches})
    sentinel_pages: dict[str, int] = {}
    page_counts: dict[str, list[int]] = {}
    list_requests = 0

    for organ, branch in branches:
        counts: list[int] = []
        for page in range(1, max_pages + 1):
            value = runner.json(
                JEJU_BOOKING_API,
                params={"organ": organ, "page": page, "pageSize": JEJU_BOOKING_PAGE_SIZE},
            )
            list_requests += 1
            if not isinstance(value, list):
                raise JejuCityContractError(f"{owner} organ {organ}: JSON list changed")
            if not value:
                sentinel_pages[str(organ)] = page
                break
            counts.append(len(value))
            for payload in value:
                if not isinstance(payload, Mapping):
                    raise JejuCityContractError(f"{owner} organ {organ}: row shape changed")
                row = _booking_row(payload, owner, organ, branch)
                identity = row["raw_fields"]["source_identity"]
                if identity in identities:
                    raise JejuCityContractError(f"{owner}: duplicate source identity {identity}")
                identities.add(identity)
                rows.append(row)
                branch_source_counts[branch] += 1
        else:
            raise JejuCityContractError(
                f"{owner} organ {organ}: no empty sentinel within max_pages={max_pages}"
            )
        page_counts[str(organ)] = counts

    for row in rows:
        if row["raw_fields"].get("source_date_anomaly") and max(
            row["start_date"], row["end_date"]
        ) >= cutoff.isoformat():
            raise JejuCityContractError(
                f"{owner} {row['raw_fields']['source_identity']}: current date anomaly"
            )
    current = [row for row in rows if row["end_date"] >= cutoff.isoformat()]
    if len(current) > detail_limit:
        raise JejuCityContractError(
            f"{owner} current details {len(current)} exceed detail_limit={detail_limit}"
        )
    for row in current:
        _booking_detail(runner, row, owner)

    meta = _base_meta(owner)
    meta.update({
        "pages": list_requests,
        "list_requests": list_requests,
        "sentinel_pages": sentinel_pages,
        "sentinel_count": 0,
        "page_counts": page_counts,
        "source_total": len(rows),
        "source_rows": len(rows),
        "source_current_count": len(current),
        "current_count": len(current),
        "detail_attempts": len(current),
        "detail_pages": len(current),
        "source_identity_sha256": _identity_hash(identities),
        "branch_count": len(branches),
        "branch_source_counts": dict(branch_source_counts),
        "branch_counts": dict(Counter(row["branch"] for row in current)),
        "municipality_counts": {JEJU_CITY_MUNICIPALITY_CODE: len(current)},
        "pagination_complete": True,
        "details_complete": True,
        "province_owner_encompasses_jeju_city": True,
    })
    return current, meta


def _colon_labels(items: Iterable[Tag]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        text = _clean(item.get_text(" ", strip=True))
        if ":" in text:
            key, value = text.split(":", 1)
            result[_norm(key)] = _clean(value)
    return result


def _parse_lifelong_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in soup.select(".db_list.edu > ul > li"):
        anchor = item.select_one("a[href*='lecture_id=']")
        title_node = item.select_one("strong")
        if anchor is None or title_node is None:
            raise JejuCityContractError("lifelong card identity/title changed")
        query = parse_qs(urlparse(_url_value(anchor.get("href"))).query)
        identity = _clean((query.get("lecture_id") or [""])[0])
        if not re.fullmatch(r"[1-9]\d*", identity):
            raise JejuCityContractError("lifelong lecture_id changed")
        title = _clean(title_node.get_text(" ", strip=True))
        labels = _colon_labels(item.select("ul.list_sty01 > li"))
        start, end, period, anomaly = _date_range(labels.get(_norm("교육기간")))
        apply_start, apply_end, apply_period, apply_anomaly = _date_range(
            labels.get(_norm("접수기간"))
        )
        if not title or anomaly or apply_anomaly:
            raise JejuCityContractError(f"lifelong {identity}: date/title changed")
        status_text = _clean(
            item.select_one(".tag_edu .text").get_text(" ", strip=True)
            if item.select_one(".tag_edu .text") else ""
        )
        status = _status(status_text)
        if status == "UNKNOWN":
            raise JejuCityContractError(f"lifelong {identity}: unknown status {status_text}")
        raw_url = JEJU_LIFELONG_URL + "?" + urlencode(
            {"mode": "detail", "lecture_id": identity}
        )
        category_node = item.select_one("em")
        capacity = _numbers(labels.get(_norm("모집인원")))
        row = _common_row(
            JEJU_LIFELONG_PROVIDER,
            identity,
            title,
            JEJU_LIFELONG_BRANCH,
            raw_url,
            "lifelong",
            experience=_looks_experience(title, category_node.get_text() if category_node else ""),
        )
        row.update({
            "category": _clean(category_node.get_text(" ", strip=True)) if category_node else "",
            "status": status,
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "target": labels.get(_norm("교육대상")),
            "venue": labels.get(_norm("교육장소")),
            "capacity_current": capacity[0] if len(capacity) >= 1 else None,
            "capacity_total": capacity[1] if len(capacity) >= 2 else None,
            "capacity_waiting": capacity[2] if len(capacity) >= 3 else None,
            "reservation_available": status == "OPEN",
            "collection_type": "html_cards+current_detail_html",
            "raw_fields": {
                "parser": JEJU_CITY_PARSER,
                "source_identity": identity,
                "source_application_control_present": bool(
                    item.select_one("[onclick*='checkUserAppl']")
                ),
            },
        })
        result.append(_clean_row(row))
    return result


def _parse_youth_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for anchor in soup.find_all("a", href=re.compile(r"doDetail\(")):
        match = re.search(r"doDetail\('([0-9]+)'\)", _url_value(anchor.get("href")))
        title_node = anchor.select_one(".title")
        branch_node = anchor.select_one(".label-typeB em")
        status_image = anchor.select_one(".label-typeA img[alt]")
        if match is None or title_node is None or branch_node is None or status_image is None:
            raise JejuCityContractError("youth card contract changed")
        identity = match.group(1)
        title = _clean(title_node.get_text(" ", strip=True))
        branch = _clean(branch_node.get_text(" ", strip=True))
        if branch not in JEJU_YOUTH_BRANCHES:
            raise JejuCityContractError(f"youth {identity}: unknown official branch {branch}")
        labels = _pairs(anchor.select_one(".memo") or anchor)
        start, end, period, anomaly = _date_range(labels.get(_norm("운영기간")))
        apply_start, apply_end, apply_period, apply_anomaly = _date_range(
            labels.get(_norm("모집일시"))
        )
        if not title or anomaly or apply_anomaly:
            raise JejuCityContractError(f"youth {identity}: date/title changed")
        status_src = _url_value(status_image.get("src")).lower()
        if "label-start" in status_src:
            status = "OPEN"
        elif any(marker in status_src for marker in ("label-ready", "label-wait")):
            status = "SCHEDULED"
        elif any(marker in status_src for marker in (
            "label-end", "label-deadline", "label-ongoing"
        )):
            status = "CLOSED"
        else:
            raise JejuCityContractError(
                f"youth {identity}: unknown status image {status_src}"
            )
        summary_node = anchor.select_one(".memo .text")
        summary = _sanitize(summary_node.get_text(" ", strip=True)) if summary_node else ""
        raw_url = JEJU_YOUTH_URL + "?" + urlencode(
            {"mode": "Detail", "program_id": identity}
        )
        row = _common_row(
            JEJU_YOUTH_PROVIDER,
            identity,
            title,
            branch,
            raw_url,
            "youth",
            experience=_looks_experience(title, summary),
        )
        row.update({
            "status": status,
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "target": labels.get(_norm("참여대상")),
            "description": summary,
            "reservation_method": labels.get(_norm("접수방법")),
            "reservation_available": status == "OPEN",
            "collection_type": "html_cards+current_detail_html",
            "raw_fields": {
                "parser": JEJU_CITY_PARSER,
                "source_identity": identity,
                "source_application_control_present": status == "OPEN",
                "source_status_image": status_src.rsplit("/", 1)[-1],
            },
        })
        result.append(_clean_row(row))
    return result


_RESIDENT_BRANCH_BY_NORMALIZED = {
    _norm(local): official for _, local, official in JEJU_RESIDENT_BRANCHES
}


def _parse_resident_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row_node in soup.select("table tbody tr"):
        anchor = row_node.select_one("a[href*='course=']")
        if anchor is None:
            continue
        cells = row_node.select("td")
        query = parse_qs(urlparse(_url_value(anchor.get("href"))).query)
        identity = _clean((query.get("course") or [""])[0])
        if not re.fullmatch(r"[1-9]\d*", identity) or len(cells) < 6:
            raise JejuCityContractError("resident row identity/columns changed")
        title = _clean(anchor.get_text(" ", strip=True))
        local = _clean(cells[1].get_text(" ", strip=True))
        branch = _RESIDENT_BRANCH_BY_NORMALIZED.get(_norm(local))
        if not branch:
            raise JejuCityContractError(f"resident {identity}: unknown Jeju-si branch {local}")
        start, end, period, anomaly = _date_range(cells[3].get_text(" ", strip=True))
        if not title or anomaly:
            raise JejuCityContractError(f"resident {identity}: date/title changed")
        status_text = _clean(cells[5].get_text(" ", strip=True))
        status = _status(status_text)
        if status == "UNKNOWN":
            raise JejuCityContractError(f"resident {identity}: unknown status {status_text}")
        raw_url = JEJU_RESIDENT_URL + "&" + urlencode(
            {"act": "view", "course": identity}
        )
        row = _common_row(
            JEJU_RESIDENT_PROVIDER,
            identity,
            title,
            branch,
            raw_url,
            "resident",
            experience=_looks_experience(title),
        )
        row.update({
            "status": status,
            "period": period,
            "start_date": start,
            "end_date": end,
            "reservation_available": status == "OPEN",
            "collection_type": "filtered_html_table+current_detail_html",
            "raw_fields": {
                "parser": JEJU_CITY_PARSER,
                "source_identity": identity,
                "source_branch_label": local,
                "organ_prefix": "1001",
                "source_application_control_present": status == "OPEN",
            },
        })
        result.append(_clean_row(row))
    return result


def _parse_star_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for anchor in soup.select("a[href*='mode=detail'][href*='edc_num=']"):
        query = parse_qs(urlparse(_url_value(anchor.get("href"))).query)
        identity = _clean((query.get("edc_num") or [""])[0])
        title_node = anchor.select_one(".title")
        category_image = anchor.select_one(".label-title img[alt]")
        status_image = anchor.select_one(".label-info img[alt]")
        if (
            not re.fullmatch(r"EDC\d+", identity) or title_node is None
            or category_image is None or status_image is None
        ):
            raise JejuCityContractError("starlight card contract changed")
        title = _clean(title_node.get_text(" ", strip=True))
        labels = _pairs(anchor.select_one(".info-area") or anchor)
        category = _clean(category_image.get("alt"))
        status_text = _clean(status_image.get("alt"))
        status = _status(status_text)
        if status == "UNKNOWN":
            raise JejuCityContractError(f"starlight {identity}: unknown status {status_text}")
        start, end, period, anomaly = _date_range(labels.get(_norm("진행기간")))
        apply_start, apply_end, apply_period, apply_anomaly = _date_range(
            labels.get(_norm("신청기간"))
        )
        if not title or apply_anomaly or (anomaly and status != "CLOSED"):
            raise JejuCityContractError(f"starlight {identity}: date/title changed")
        if anomaly:
            values = _date_tokens(labels.get(_norm("진행기간")))
            if len(values) >= 2:
                start, end = values[0].isoformat(), values[1].isoformat()
                period = ""
        capacity = _numbers(labels.get(_norm("모집인원")))
        raw_url = JEJU_STAR_URL + "?" + urlencode(
            {"mode": "detail", "edc_num": identity}
        )
        card = anchor.find_parent("li")
        row = _common_row(
            JEJU_STAR_PROVIDER,
            identity,
            title,
            JEJU_STAR_BRANCH,
            raw_url,
            "star",
            experience=category == "행사" or _looks_experience(title, category),
        )
        row.update({
            "category": category,
            "status": status,
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "target": labels.get(_norm("모집대상")),
            "capacity_total": capacity[0] if capacity else None,
            "reservation_method": labels.get(_norm("진행방식")),
            "reservation_available": status == "OPEN",
            "collection_type": "html_cards+current_detail_html",
            "raw_fields": {
                "parser": JEJU_CITY_PARSER,
                "source_identity": identity,
                "source_application_control_present": status == "OPEN",
                "applicant_list_control_present": bool(
                    card and card.select_one("[onclick*='edcReqMemPop']")
                ),
                "source_date_anomaly": anomaly,
            },
        })
        result.append(_clean_row(row))
    return result


_AGRICULTURE_BY_URL = {
    url: (key, branch) for key, url, branch in JEJU_AGRICULTURE_BRANCHES
}


def _parse_agriculture_page(
    soup: BeautifulSoup, branch_key: str, branch: str, list_url: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for anchor in soup.select("a[href*='act=view'][href*='seq=']"):
        query = parse_qs(urlparse(_url_value(anchor.get("href"))).query)
        source_id = _clean((query.get("seq") or [""])[0])
        title_node = anchor.select_one(".tit")
        status_node = anchor.select_one(".badge")
        if not re.fullmatch(r"[1-9]\d*", source_id) or title_node is None or status_node is None:
            raise JejuCityContractError("agriculture card contract changed")
        identity = f"{branch_key}:{source_id}"
        title = _clean(title_node.get_text(" ", strip=True))
        labels = _pairs(anchor.select_one(".info-dl") or anchor)
        start, end, period, anomaly = _date_range(
            labels.get(_norm("교육기간")), short_year=True
        )
        apply_start, apply_end, apply_period, apply_anomaly = _date_range(
            labels.get(_norm("신청기간")), short_year=True, allow_single=True
        )
        if not title or anomaly or apply_anomaly:
            raise JejuCityContractError(f"agriculture {identity}: date/title changed")
        status_text = _clean(status_node.get_text(" ", strip=True))
        status = _status(status_text)
        if status == "UNKNOWN":
            raise JejuCityContractError(f"agriculture {identity}: unknown status {status_text}")
        applicants = _numbers(labels.get(_norm("신청자")))
        raw_url = list_url + "?" + urlencode({"act": "view", "seq": source_id})
        row = _common_row(
            JEJU_AGRICULTURE_PROVIDER,
            identity,
            title,
            branch,
            raw_url,
            "agriculture",
            experience=_looks_experience(title),
        )
        row.update({
            "status": status,
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "venue": labels.get(_norm("교육장소")),
            "reservation_method": labels.get(_norm("신청방법")),
            "capacity_current": applicants[0] if applicants else None,
            "reservation_available": status == "OPEN",
            "collection_type": "html_cards+current_detail_html",
            "raw_fields": {
                "parser": JEJU_CITY_PARSER,
                "source_identity": identity,
                "education_id": source_id,
                "branch_key": branch_key,
                "source_application_control_present": status == "OPEN",
            },
        })
        result.append(_clean_row(row))
    return result


def _parse_dream_library_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for anchor in soup.select("table tbody a[href*='course=']"):
        query = parse_qs(urlparse(_url_value(anchor.get("href"))).query)
        identity = _clean((query.get("course") or [""])[0])
        row_node = anchor.find_parent("tr")
        cells = row_node.select("td") if row_node else []
        if not re.fullmatch(r"[1-9]\d*", identity) or len(cells) < 7:
            raise JejuCityContractError("dream-library row contract changed")
        title = _clean(cells[1].get_text(" ", strip=True))
        status_text = _clean(cells[6].get_text(" ", strip=True))
        status = _status(status_text)
        if status == "UNKNOWN":
            raise JejuCityContractError(
                f"dream-library {identity}: unknown status {status_text}"
            )
        apply_start, apply_end, apply_period, apply_anomaly = _date_range(
            cells[2].get_text(" ", strip=True), short_year=True, allow_single=True
        )
        start, end, period, anomaly = _date_range(
            cells[3].get_text(" ", strip=True), short_year=True, allow_single=True
        )
        if anomaly and status == "CLOSED" and apply_end:
            # A small number of closed historical rows publish only a time in
            # the education cell.  Their completed application date is safe
            # solely as a past/current decision sentinel and is never returned.
            start = end = apply_end
            period = ""
        if not title or apply_anomaly or (anomaly and status != "CLOSED"):
            raise JejuCityContractError(f"dream-library {identity}: date/title changed")
        capacity = _numbers(cells[5].get_text(" ", strip=True))
        raw_url = JEJU_DREAM_LIBRARY_URL + "?" + urlencode(
            {"act": "view", "course": identity}
        )
        row = _common_row(
            JEJU_DREAM_LIBRARY_PROVIDER,
            identity,
            title,
            JEJU_DREAM_LIBRARY_BRANCH,
            raw_url,
            "dream_library",
            experience=_looks_experience(title),
        )
        row.update({
            "status": status,
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "schedule_raw": _clean(cells[3].get_text(" ", strip=True)),
            "target": _clean(cells[4].get_text(" ", strip=True)),
            "capacity_total": capacity[0] if capacity else None,
            "capacity_current": capacity[1] if len(capacity) >= 2 else None,
            "reservation_available": status == "OPEN",
            "collection_type": "html_table+current_detail_html",
            "raw_fields": {
                "parser": JEJU_CITY_PARSER,
                "source_identity": identity,
                "source_application_control_present": status == "OPEN",
                "source_date_anomaly": anomaly,
            },
        })
        result.append(_clean_row(row))
    return result


def _verify_html_detail(runner: _Runner, row: dict[str, Any], owner: str) -> None:
    soup = runner.soup(row["raw_url"])
    identity = row["raw_fields"]["source_identity"]
    short_year = False
    if owner == "lifelong":
        root = soup.select_one(".content-box")
        title_node = root.select_one("h4.title") if root else None
    elif owner == "youth":
        root = soup.select_one(".content-box table")
        title_node = None
    elif owner == "resident":
        root = soup.select_one(".module-wrapper")
        title_node = None
    elif owner == "star":
        root = soup.select_one(".view-wrap")
        title_node = root.select_one("strong") if root else None
    elif owner == "agriculture":
        root = soup.select_one(".view-wrap")
        title_node = None
        short_year = True
    else:
        root = soup.select_one("table.table-articles")
        title_node = None
        short_year = True
    if root is None:
        raise JejuCityContractError(f"{owner} {identity}: detail contract missing")
    root_text = _clean(root.get_text(" ", strip=True))
    if title_node is not None:
        title_matches = _norm(title_node.get_text(" ", strip=True)) == _norm(row["title"])
    else:
        title_matches = _norm(row["title"]) in _norm(root_text)
    if not title_matches:
        raise JejuCityContractError(f"{owner} {identity}: detail/list title mismatch")
    dates = {
        item.isoformat() for item in _date_tokens(root_text, short_year=short_year)
    }
    if row["start_date"] not in dates or row["end_date"] not in dates:
        raise JejuCityContractError(f"{owner} {identity}: detail/list date mismatch")
    row["raw_fields"]["detail_verified"] = True


def _advertised_total(owner: str, soup: BeautifulSoup) -> Optional[int]:
    text = _clean(soup.get_text(" ", strip=True))
    patterns = {
        "resident": (
            r"(?:총|전체)\s*([\d,]+)\s*(?:건|개)",
            r"([\d,]+)\s*개의\s*(?:프로그램|게시물)",
        ),
        "dream_library": (r"([\d,]+)\s*개의\s*게시물",),
    }.get(owner, ())
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def _crawl_html_pages(
    runner: _Runner,
    owner: str,
    list_url: str,
    page_parameter: str,
    fixed_params: Mapping[str, Any],
    parser: Callable[[BeautifulSoup], list[dict[str, Any]]],
    max_pages: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    page_counts: list[int] = []
    advertised_total: Optional[int] = None
    for page in range(1, max_pages + 1):
        params = dict(fixed_params)
        params[page_parameter] = page
        soup = runner.soup(list_url, params=params)
        if page == 1:
            advertised_total = _advertised_total(owner, soup)
        parsed = parser(soup)
        if not parsed:
            if page == 1:
                raise JejuCityContractError(f"{owner}: first source page is empty")
            sentinel_page = page
            break
        page_counts.append(len(parsed))
        for row in parsed:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            if not identity or identity in identities:
                raise JejuCityContractError(
                    f"{owner}: duplicate/empty source identity {identity} on page {page}"
                )
            identities.add(identity)
            rows.append(row)
    else:
        raise JejuCityContractError(
            f"{owner}: no empty sentinel within max_pages={max_pages}"
        )
    if advertised_total is not None and advertised_total != len(rows):
        raise JejuCityContractError(
            f"{owner}: advertised total {advertised_total} != parsed {len(rows)}"
        )
    return rows, {
        "pages": sentinel_page,
        "list_requests": sentinel_page,
        "sentinel_page": sentinel_page,
        "sentinel_count": 0,
        "page_counts": page_counts,
        "advertised_total": advertised_total,
        "source_identity_sha256": _identity_hash(identities),
    }


def _collect_html_owner(
    runner: _Runner,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    owner: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    chunks: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    if owner == "lifelong":
        chunks.append(_crawl_html_pages(
            runner, owner, JEJU_LIFELONG_URL, "currentPageNo", {},
            _parse_lifelong_page, max_pages,
        ))
    elif owner == "youth":
        chunks.append(_crawl_html_pages(
            runner, owner, JEJU_YOUTH_URL, "currentPageNo", {},
            _parse_youth_page, max_pages,
        ))
    elif owner == "resident":
        chunks.append(_crawl_html_pages(
            runner, owner, JEJU_RESIDENT_URL, "page", {},
            _parse_resident_page, max_pages,
        ))
    elif owner == "star":
        chunks.append(_crawl_html_pages(
            runner, owner, JEJU_STAR_URL, "currentPageNo",
            {"mode": "list", "searchFlag": 2}, _parse_star_page, max_pages,
        ))
    elif owner == "dream_library":
        chunks.append(_crawl_html_pages(
            runner, owner, JEJU_DREAM_LIBRARY_URL, "page", {},
            _parse_dream_library_page, max_pages,
        ))
    elif owner == "agriculture":
        for key, list_url, branch in JEJU_AGRICULTURE_BRANCHES:
            chunks.append(_crawl_html_pages(
                runner,
                owner,
                list_url,
                "page",
                {},
                lambda soup, k=key, b=branch, u=list_url: _parse_agriculture_page(
                    soup, k, b, u
                ),
                max_pages,
            ))
    else:
        raise JejuCityContractError(f"unsupported HTML owner {owner}")

    rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    if owner == "resident":
        official_branches = [official for _, _, official in JEJU_RESIDENT_BRANCHES]
    elif owner == "youth":
        official_branches = list(JEJU_YOUTH_BRANCHES)
    elif owner == "agriculture":
        official_branches = [branch for _, _, branch in JEJU_AGRICULTURE_BRANCHES]
    elif owner == "lifelong":
        official_branches = [JEJU_LIFELONG_BRANCH]
    elif owner == "star":
        official_branches = [JEJU_STAR_BRANCH]
    else:
        official_branches = [JEJU_DREAM_LIBRARY_BRANCH]
    branch_source_counts: Counter[str] = Counter({branch: 0 for branch in official_branches})
    for chunk, _ in chunks:
        for row in chunk:
            identity = row["raw_fields"]["source_identity"]
            if identity in identities:
                raise JejuCityContractError(f"{owner}: duplicate identity across branches")
            identities.add(identity)
            rows.append(row)
            branch_source_counts[row["branch"]] += 1

    for row in rows:
        if row["raw_fields"].get("source_date_anomaly") and max(
            row["start_date"], row["end_date"]
        ) >= cutoff.isoformat():
            raise JejuCityContractError(
                f"{owner} {row['raw_fields']['source_identity']}: current date anomaly"
            )
    current = [row for row in rows if row["end_date"] >= cutoff.isoformat()]
    if len(current) > detail_limit:
        raise JejuCityContractError(
            f"{owner} current details {len(current)} exceed detail_limit={detail_limit}"
        )
    for row in current:
        _verify_html_detail(runner, row, owner)

    sentinel_pages = {
        (
            JEJU_AGRICULTURE_BRANCHES[index][0]
            if owner == "agriculture" else owner
        ): info["sentinel_page"]
        for index, (_, info) in enumerate(chunks)
    }
    meta = _base_meta(owner)
    meta.update({
        "pages": sum(info["pages"] for _, info in chunks),
        "list_requests": sum(info["list_requests"] for _, info in chunks),
        "sentinel_pages": sentinel_pages,
        "sentinel_count": 0,
        "page_counts": {
            (
                JEJU_AGRICULTURE_BRANCHES[index][0]
                if owner == "agriculture" else owner
            ): info["page_counts"]
            for index, (_, info) in enumerate(chunks)
        },
        "advertised_total": sum(
            int(info["advertised_total"] or 0) for _, info in chunks
        ) or None,
        "source_total": len(rows),
        "source_rows": len(rows),
        "source_current_count": len(current),
        "current_count": len(current),
        "detail_attempts": len(current),
        "detail_pages": len(current),
        "source_identity_sha256": _identity_hash(identities),
        "branch_count": len(official_branches),
        "branch_source_counts": dict(branch_source_counts),
        "branch_counts": dict(Counter(row["branch"] for row in current)),
        "municipality_counts": {JEJU_CITY_MUNICIPALITY_CODE: len(current)},
        "pagination_complete": True,
        "details_complete": True,
        "province_owner_encompasses_jeju_city": owner in {"resident", "agriculture"},
    })
    return current, meta


def collect_jeju_city_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = JEJU_DEFAULT_MAX_PAGES,
    detail_limit: int = JEJU_DEFAULT_DETAIL_LIMIT,
    *,
    max_requests: int = JEJU_DEFAULT_MAX_REQUESTS,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete and atomic Jeju-si public programme snapshot."""

    owner = _OWNERS.get(_provider(target), "unknown")
    meta = _base_meta(owner)
    if not is_jeju_city_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match an exact canonical Jeju-si owner route"
        )
        return [], JEJU_CITY_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], JEJU_CITY_PARSER, meta
        session_factory = _default_session_factory
    try:
        timeout = _positive(timeout, "timeout")
        max_pages = _positive(max_pages, "max_pages")
        detail_limit = _positive(detail_limit, "detail_limit")
        max_requests = _positive(max_requests, "max_requests")
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], JEJU_CITY_PARSER, meta

    runner = _Runner(session_factory, timeout, max_requests, sleeper)
    try:
        try:
            if owner in {"integrated", "library"}:
                rows, meta = _collect_booking_api(
                    runner, cutoff, max_pages, detail_limit, owner
                )
            else:
                rows, meta = _collect_html_owner(
                    runner, cutoff, max_pages, detail_limit, owner
                )
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in rows]))
            if len(result) != len(rows):
                raise JejuCityContractError(
                    f"dedupe changed complete row count {len(rows)} to {len(result)}"
                )
            forbidden_keys = {
                "instructor", "teacher", "phone", "contact", "email", "manager",
                "applicant_name", "birth_date", "address", "file_url",
            }
            if any(forbidden_keys.intersection(row) for row in result):
                raise JejuCityContractError("PII-bearing output field detected")
            serialized = json.dumps(result, ensure_ascii=False)
            if (
                _RESIDENT_ID_RE.search(serialized)
                or _EMAIL_RE.search(serialized)
                or _PHONE_RE.search(serialized)
            ):
                raise JejuCityContractError("PII-bearing output value detected")
            meta.update({
                "returned_count": len(result),
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "discovered_links": int(meta.get("source_rows") or 0),
                "pagination_detected": int(meta.get("source_rows") or 0) > 20,
                "no_current_data": not result,
                "no_current_reason": (
                    "complete owner ledger has no current/future courses" if not result else ""
                ),
                "physical_requests": runner.physical_requests,
                "retry_count": runner.retry_count,
                "sessions_created": runner.sessions_created,
                "max_requests": max_requests,
                "configured_collection_error": "",
                "application_endpoints_called": 0,
                "applicant_endpoints_called": 0,
            })
            return result, JEJU_CITY_PARSER, meta
        except Exception as exc:
            meta.update({
                "physical_requests": runner.physical_requests,
                "retry_count": runner.retry_count,
                "sessions_created": runner.sessions_created,
                "max_requests": max_requests,
                "configured_collection_error": _clean(exc),
                "snapshot_complete": False,
                "returned_count": 0,
                "application_endpoints_called": 0,
                "applicant_endpoints_called": 0,
            })
            return [], JEJU_CITY_PARSER, meta
    finally:
        runner.close()


collect = collect_jeju_city_education_courses


__all__ = [name for name in globals() if name.startswith("JEJU_")] + [
    "JejuCityContractError",
    "collect",
    "collect_jeju_city_education_courses",
    "is_jeju_city_education_target",
    "is_target",
]
