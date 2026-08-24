"""Fail-closed public education collectors for Gwangju-si, Gyeonggi-do.

The municipality code is ``4161000000``.  This module must not be confused
with Gwangju Metropolitan City.  Six independent public ledgers are kept as
separate provider identities: the branded GSEEK tenant, the fifteen
resident-centre partitions, eleven public-library branches, citizen IT
education, agricultural education, and the youth training centre's two
lifelong-education partitions.

Only public list and detail resources are read.  Login, identity
verification, applicant lookup, application, cancellation, payment, file
download and view-count mutation endpoints are never requested.  Production
callers must inject the repository's managed ``session_factory``; raw
``requests`` sessions are available solely behind an explicit test/live-audit
switch.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import html
import json
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


GYEONGGI_GWANGJU_MUNICIPALITY_CODE = "4161000000"
GYEONGGI_GWANGJU_MUNICIPALITY_NAME = "경기도 광주시"

GYEONGGI_GWANGJU_GSEEK_PROVIDER = "MUNI_GJEDU_GSEEK_KR_F929637E"
GYEONGGI_GWANGJU_GSEEK_CANDIDATE_ID = "MUNI_IR_EB8041731BA6"
GYEONGGI_GWANGJU_GSEEK_URL = "https://gjedu.gseek.kr/user/course/offline/list"
GYEONGGI_GWANGJU_GSEEK_API_URL = GYEONGGI_GWANGJU_GSEEK_URL + "/search"
GYEONGGI_GWANGJU_GSEEK_PARENT_URL = "https://www.gseek.kr/user/course/offline/list"
GYEONGGI_GWANGJU_GSEEK_REGION_CODE = GYEONGGI_GWANGJU_MUNICIPALITY_CODE
GYEONGGI_GWANGJU_GSEEK_CO_SPONSOR_ID = "G000007"
GYEONGGI_GWANGJU_GSEEK_PAGE_SIZE = 9
GYEONGGI_GWANGJU_GSEEK_BRANCHES = (
    "광주시 평생학습관",
    "여성비전센터",
    "검천평생학습센터",
    "송정 청소년 문화의 집",
    "신현 청소년 문화의 집",
    "광주시 읍면동 평생학습센터",
    "광주시 장애인평생학습센터",
)

GYEONGGI_GWANGJU_RESIDENT_PROVIDER = "MUNI_WWW_GJCITY_GO_KR_CF520672"
GYEONGGI_GWANGJU_RESIDENT_CANDIDATE_ID = "MUNI_IR_984A2B92E269"
GYEONGGI_GWANGJU_RESIDENT_URL = (
    "https://www.gjcity.go.kr/jumin/edu/program/list.do?"
    "category=2&programKind=3&mId=0301030000"
)
GYEONGGI_GWANGJU_RESIDENT_LIST_ENDPOINT = (
    "https://www.gjcity.go.kr/jumin/edu/program/list.do"
)
GYEONGGI_GWANGJU_RESIDENT_DETAIL_ENDPOINT = (
    "https://www.gjcity.go.kr/jumin/edu/program/view.do"
)
GYEONGGI_GWANGJU_RESIDENT_APPLICATION_ENDPOINT = (
    "https://www.gjcity.go.kr/jumin/edu/enrollee/write.do"
)
GYEONGGI_GWANGJU_RESIDENT_BRANCHES: tuple[tuple[str, str], ...] = (
    ("2", "초월읍"),
    ("3", "곤지암읍"),
    ("4", "도척면"),
    ("5", "퇴촌남종면"),
    ("6", "남한산성면"),
    ("1", "오포1동"),
    ("13", "오포2동"),
    ("14", "신현동"),
    ("15", "능평동"),
    ("7", "경안동"),
    ("11", "쌍령동"),
    ("16", "송정동"),
    ("8", "탄벌동"),
    ("12", "광남1동"),
    ("9", "광남2동"),
)
GYEONGGI_GWANGJU_RESIDENT_EXCLUDED_TITLES = frozenset({"test"})

GYEONGGI_GWANGJU_LIBRARY_PROVIDER = "MUNI_LIB_GJCITY_GO_KR_56EBD1BF"
GYEONGGI_GWANGJU_LIBRARY_CANDIDATE_ID = "MUNI_IR_A29F139A5AF7"
GYEONGGI_GWANGJU_LIBRARY_URL = (
    "https://lib.gjcity.go.kr/center/lay1/program/S8T48C62/"
    "cultureprogram/cultureWrt_list.do"
)
GYEONGGI_GWANGJU_LIBRARY_BRANCHES: tuple[tuple[str, str], ...] = (
    ("중앙도서관", GYEONGGI_GWANGJU_LIBRARY_URL),
    ("오포도서관", "https://lib.gjcity.go.kr/op/lay1/program/S26T186C189/cultureprogram/cultureWrt_list.do"),
    ("초월도서관", "https://lib.gjcity.go.kr/cw/lay1/program/S28T315C317/cultureprogram/cultureWrt_list.do"),
    ("곤지암도서관", "https://lib.gjcity.go.kr/gj/lay1/program/S27T249C251/cultureprogram/cultureWrt_list.do"),
    ("능평도서관", "https://lib.gjcity.go.kr/np/lay1/program/S29T377C379/cultureprogram/cultureWrt_list.do"),
    ("양벌도서관", "https://lib.gjcity.go.kr/yb/lay1/program/S25T2805C2807/cultureprogram/cultureWrt_list.do"),
    ("광남도서관", "https://lib.gjcity.go.kr/gn/lay1/program/S22T3341C3343/cultureprogram/cultureWrt_list.do"),
    ("퇴촌도서관", "https://lib.gjcity.go.kr/tc/lay1/program/S23T3030C3032/cultureprogram/cultureWrt_list.do"),
    ("만선도서관", "https://lib.gjcity.go.kr/ms/lay1/program/S24T3091C3093/cultureprogram/cultureWrt_list.do"),
    ("신현도서관", "https://lib.gjcity.go.kr/sh/lay1/program/S21T3643C3645/cultureprogram/cultureWrt_list.do"),
    ("작은도서관", "https://lib.gjcity.go.kr/slib/lay1/program/S39T422C434/cultureprogram/cultureWrt_list.do"),
)

GYEONGGI_GWANGJU_IT_PROVIDER = "MUNI_WWW_GJCITY_GO_KR_4BA53CE8"
GYEONGGI_GWANGJU_IT_CANDIDATE_ID = "MUNI_IR_E2A9171394F3"
GYEONGGI_GWANGJU_IT_URL = (
    "https://www.gjcity.go.kr/depart/cyberoff/lecture/list.do?mId=0205050100"
)
GYEONGGI_GWANGJU_IT_LIST_ENDPOINT = (
    "https://www.gjcity.go.kr/depart/cyberoff/lecture/list.do"
)
GYEONGGI_GWANGJU_IT_DETAIL_ENDPOINT = (
    "https://www.gjcity.go.kr/depart/cyberoff/lecture/view.do"
)
GYEONGGI_GWANGJU_IT_APPLICATION_ENDPOINT = (
    "https://www.gjcity.go.kr/depart/cyberoff/enroll/write.do"
)
GYEONGGI_GWANGJU_IT_BRANCH = "광주시 시민정보화교육장"

GYEONGGI_GWANGJU_AGRI_PROVIDER = "MUNI_WWW_GJCITY_GO_KR_5B834C82"
GYEONGGI_GWANGJU_AGRI_CANDIDATE_ID = "MUNI_IR_C0E557892668"
GYEONGGI_GWANGJU_AGRI_URL = (
    "https://www.gjcity.go.kr/portal/agritec/lecture/list.do?mId=0408070100"
)
GYEONGGI_GWANGJU_AGRI_LIST_ENDPOINT = (
    "https://www.gjcity.go.kr/portal/agritec/lecture/list.do"
)
GYEONGGI_GWANGJU_AGRI_DETAIL_ENDPOINT = (
    "https://www.gjcity.go.kr/portal/agritec/lecture/view.do"
)
GYEONGGI_GWANGJU_AGRI_APPLICATION_ENDPOINT = (
    "https://www.gjcity.go.kr/portal/agritec/enroll/write.do"
)
GYEONGGI_GWANGJU_AGRI_BRANCH = "광주시농업기술센터"

GYEONGGI_GWANGJU_YOUTH_PROVIDER = "MUNI_WWW_GJYOUTH_OR_KR_E2AB883F"
GYEONGGI_GWANGJU_YOUTH_CANDIDATE_ID = "MUNI_IR_61F1BD0958D1"
GYEONGGI_GWANGJU_YOUTH_URL = "https://www.gjyouth.or.kr/board/life_y.asp"
GYEONGGI_GWANGJU_YOUTH_BRANCH = "광주시청소년수련관"
GYEONGGI_GWANGJU_YOUTH_PARTITIONS: tuple[tuple[str, str, str], ...] = (
    ("평생교육(청소년)", "life_y.asp", "life_y_v.asp"),
    ("평생교육(성인)", "life_a.asp", "life_a_v.asp"),
)

GYEONGGI_GWANGJU_WOMEN_INFO_URL = (
    "https://www.gjcity.go.kr/depart/contents.do?mId=1016030000"
)
GYEONGGI_GWANGJU_SPORTS_OWNER_URL = "https://gjcenter.gjcs.or.kr/fmcs/13"
GYEONGGI_GWANGJU_YOUTH_PLAYPASS_URL = "https://web.gjyouth.or.kr/"
GYEONGGI_GWANGJU_YOUTH_SPORTS_URLS = (
    "https://www.gjyouth.or.kr/board/sport_y.asp",
    "https://www.gjyouth.or.kr/board/sport_a.asp",
)
GYEONGGI_GWANGJU_METROPOLITAN_URL = "https://www.gwangju.go.kr/"
GYEONGGI_GWANGJU_IT_ALIAS_URL = (
    "https://cyberoff.gjcity.go.kr/depart/contents.do?mId=0205020000"
)

GYEONGGI_GWANGJU_APPLICATION_PATHS = frozenset({
    "/jumin/edu/enrollee/write.do",
    "/depart/cyberoff/enroll/write.do",
    "/portal/agritec/enroll/write.do",
})

GYEONGGI_GWANGJU_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "gseek": {
        "provider": GYEONGGI_GWANGJU_GSEEK_PROVIDER,
        "url": GYEONGGI_GWANGJU_GSEEK_URL,
        "decision": "dedicated_G000007_branded_tenant_owner",
    },
    "gseek_parent": {
        "url": GYEONGGI_GWANGJU_GSEEK_PARENT_URL,
        "decision": "exclude_rows_where_d_co_sprvsn_id_is_G000007",
    },
    "resident_centres": {
        "provider": GYEONGGI_GWANGJU_RESIDENT_PROVIDER,
        "url": GYEONGGI_GWANGJU_RESIDENT_URL,
        "decision": "one_owner_with_fifteen_exact_official_partitions",
    },
    "libraries": {
        "provider": GYEONGGI_GWANGJU_LIBRARY_PROVIDER,
        "url": GYEONGGI_GWANGJU_LIBRARY_URL,
        "decision": "one_owner_with_eleven_exact_official_branch_ledgers",
    },
    "citizen_it": {
        "provider": GYEONGGI_GWANGJU_IT_PROVIDER,
        "url": GYEONGGI_GWANGJU_IT_URL,
        "decision": "independent_city_education_ledger",
    },
    "citizen_it_annual_schedule_alias": {
        "url": GYEONGGI_GWANGJU_IT_ALIAS_URL,
        "decision": "information_only_schedule; canonical_application_ledger_is_www_owner",
    },
    "agriculture": {
        "provider": GYEONGGI_GWANGJU_AGRI_PROVIDER,
        "url": GYEONGGI_GWANGJU_AGRI_URL,
        "decision": "independent_agricultural_education_ledger",
    },
    "youth_lifelong": {
        "provider": GYEONGGI_GWANGJU_YOUTH_PROVIDER,
        "url": GYEONGGI_GWANGJU_YOUTH_URL,
        "decision": "one_owner_for_youth_and_adult_lifelong_partitions",
    },
    "women_vision_centre": {
        "url": GYEONGGI_GWANGJU_WOMEN_INFO_URL,
        "decision": "information_only_expired_schedule_linking_to_gseek_application_owner",
    },
    "municipal_sports": {
        "url": GYEONGGI_GWANGJU_SPORTS_OWNER_URL,
        "decision": "separate_sports_owner_not_education",
    },
    "youth_sports": {
        "urls": GYEONGGI_GWANGJU_YOUTH_SPORTS_URLS,
        "decision": "separate_sports_partitions_not_collected_by_lifelong_owner",
    },
    "youth_playpass": {
        "url": GYEONGGI_GWANGJU_YOUTH_PLAYPASS_URL,
        "decision": "separate_activity_and_facility_reservation_owner",
    },
    "gwangju_metropolitan_city": {
        "url": GYEONGGI_GWANGJU_METROPOLITAN_URL,
        "decision": "hard_exclude_different_municipality",
    },
}

GYEONGGI_GWANGJU_LIVE_AUDIT_BASELINE: Mapping[str, Mapping[str, Any]] = {
    "gseek": {
        "checked_at": "2026-07-28", "source_total": 202, "current_count": 198,
        "sentinel_start": 203,
        "sorted_identity_sha256": "c57681a70d8a09c8d27c7fbe7e24f07bce3b412a2ee846cc57451ac4d73c2ecb",
    },
    "resident": {
        "checked_at": "2026-07-23", "source_total": 3473,
        "source_current_count": 525, "current_count": 524,
        "excluded_test_count": 1,
        "sorted_identity_sha256": "70eec9ab73cd24a8427855d4dea53865834dad49467bd57dab13b9cc9a8e65ac",
    },
    "citizen_it": {
        "checked_at": "2026-07-23", "source_total": 10, "current_count": 10,
        "sentinel_page": 2,
        "sorted_identity_sha256": "d21b61ce6767f088941c201eef283cbded06d31bc04d01a79b39e8465eefb7dd",
    },
    "agriculture": {
        "checked_at": "2026-07-23", "source_total": 254, "current_count": 4,
        "sentinel_page": 27,
        "sorted_identity_sha256": "9c43208f17c1bf385e247d3269e53e41f0e39f7d35b451e8d78fd2672417d00d",
    },
    "youth": {
        "checked_at": "2026-07-23", "source_total": 38, "current_count": 38,
        "sentinel_pages": {"평생교육(청소년)": 8, "평생교육(성인)": 2},
        "sorted_identity_sha256": "9d77a3c0ccb34d8eb2647948a6ffdfde86035971af6c1aa487dfc96559a07c5f",
    },
}

GYEONGGI_GWANGJU_PARSER = (
    "gyeonggi_gwangju_owner_dispatch+complete_advertised_pagination+"
    "exact_empty_sentinels+stable_edges+all_current_safe_details+"
    "official_branches+pii_minimized+no_application_calls"
)
GYEONGGI_GWANGJU_DEFAULT_MAX_PAGES = 700
GYEONGGI_GWANGJU_DEFAULT_DETAIL_LIMIT = 1_200
GYEONGGI_GWANGJU_DEFAULT_MAX_REQUESTS = 5_000
GYEONGGI_GWANGJU_SESSION_REQUEST_LIMIT = 60

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]

_SPACE_RE = re.compile(r"\s+")
_ID_RE = re.compile(r"[1-9]\d*")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_PHONE_RE = re.compile(r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_GSEEK_STATUS = {
    "모집중": "OPEN", "마감임박": "OPEN", "대기접수": "OPEN", "추가접수": "OPEN",
    "모집예정": "SCHEDULED", "마감": "CLOSED",
}
_RESIDENT_STATUS = {
    "접수중": "OPEN", "방문접수중": "OPEN", "대기자접수중": "OPEN",
    "접수대기": "SCHEDULED", "접수마감": "CLOSED",
    "교육중": "CLOSED", "교육마감": "CLOSED", "교육완료": "CLOSED",
}
_LIBRARY_STATUS = {
    "접수중": "OPEN", "접수전": "SCHEDULED", "접수대기": "SCHEDULED",
    "접수마감": "CLOSED", "강좌종료": "CLOSED", "교육중": "CLOSED",
}
_IT_STATUS = {
    "접수중": "OPEN", "대기자접수중": "OPEN", "접수대기": "SCHEDULED",
    "접수마감": "CLOSED", "교육중": "CLOSED", "교육완료": "CLOSED",
}
_AGRI_STATUS = {
    "접수중": "OPEN", "대기자접수중": "OPEN", "접수대기": "SCHEDULED",
    "접수마감": "CLOSED", "교육중": "CLOSED", "교육마감": "CLOSED",
    "교육완료": "CLOSED",
}


class GyeonggiGwangjuContractError(ValueError):
    """Raised when an audited public-source contract changes."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", html.unescape(str(value or "")).replace("\xa0", " ")).strip()


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in _clean(value) if ch.isalnum())


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
        raise GyeonggiGwangjuContractError(f"{name} must be a positive integer") from exc
    if result < 1:
        raise GyeonggiGwangjuContractError(f"{name} must be a positive integer")
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


_TARGETS = {
    GYEONGGI_GWANGJU_GSEEK_PROVIDER: GYEONGGI_GWANGJU_GSEEK_URL,
    GYEONGGI_GWANGJU_RESIDENT_PROVIDER: GYEONGGI_GWANGJU_RESIDENT_URL,
    GYEONGGI_GWANGJU_LIBRARY_PROVIDER: GYEONGGI_GWANGJU_LIBRARY_URL,
    GYEONGGI_GWANGJU_IT_PROVIDER: GYEONGGI_GWANGJU_IT_URL,
    GYEONGGI_GWANGJU_AGRI_PROVIDER: GYEONGGI_GWANGJU_AGRI_URL,
    GYEONGGI_GWANGJU_YOUTH_PROVIDER: GYEONGGI_GWANGJU_YOUTH_URL,
}


def is_gyeonggi_gwangju_education_target(target: Any) -> bool:
    canonical = _TARGETS.get(_provider(target))
    return bool(canonical and _exact_target(_target_url(target), canonical))


is_target = is_gyeonggi_gwangju_education_target


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
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
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return bytes(content).decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return bytes(content).decode("utf-8", errors="replace")


def _validate_response(response: Any, expected_url: str, *, parameterized: bool = False) -> None:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise GyeonggiGwangjuContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise GyeonggiGwangjuContractError("redirects are not accepted")
    final = _clean(getattr(response, "url", ""))
    if final:
        got, wanted = urlparse(final), urlparse(expected_url)
        if parameterized:
            if (got.scheme, got.hostname, got.port, got.path) != (
                "https", wanted.hostname, None, wanted.path
            ):
                raise GyeonggiGwangjuContractError("response escaped the audited endpoint")
        elif final != expected_url:
            raise GyeonggiGwangjuContractError("response escaped the canonical URL")
    body = _decoded_body(response)
    blocked = (
        "WebKnight Application Firewall Alert",
        "비정상적으로 빠른 요청",
        "이용이 제한되었습니다",
        "Access Denied",
    )
    if any(marker in body for marker in blocked):
        raise GyeonggiGwangjuContractError("official source rate-limit/WAF response")


class _Runner:
    def __init__(self, factory: SessionFactory, timeout: int, max_requests: int, sleeper: Sleeper) -> None:
        self.factory = factory
        self.timeout = timeout
        self.max_requests = max_requests
        self.sleeper = sleeper
        self.session: Any = None
        self.session_requests = GYEONGGI_GWANGJU_SESSION_REQUEST_LIMIT
        self.physical_requests = 0
        self.retry_count = 0
        self.sessions_created = 0

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

    def request(self, method: str, url: str, *, parameterized: bool = False, **kwargs: Any) -> Any:
        path = urlparse(url).path
        if path in GYEONGGI_GWANGJU_APPLICATION_PATHS or "/apply/" in path.lower():
            raise GyeonggiGwangjuContractError("application endpoint invocation is forbidden")
        last: Optional[Exception] = None
        for attempt in range(2):
            if self.physical_requests >= self.max_requests:
                raise GyeonggiGwangjuContractError(
                    f"max_requests cap {self.max_requests} exhausted"
                )
            if self.session is None or self.session_requests >= GYEONGGI_GWANGJU_SESSION_REQUEST_LIMIT:
                self._new()
            self.physical_requests += 1
            self.session_requests += 1
            try:
                operation = getattr(self.session, method)
                response = operation(
                    url, timeout=self.timeout, allow_redirects=False, **kwargs
                )
                _validate_response(response, url, parameterized=parameterized)
                return response
            except Exception as exc:
                last = exc
                if attempt == 0:
                    self.retry_count += 1
                    self._new()
                    self.sleeper(0.5)
        assert last is not None
        raise last

    def soup(self, method: str, url: str, *, parameterized: bool = False, **kwargs: Any) -> BeautifulSoup:
        response = self.request(method, url, parameterized=parameterized, **kwargs)
        content = getattr(response, "content", None)
        if content is None:
            content = getattr(response, "text", "")
        if not content:
            raise GyeonggiGwangjuContractError("empty HTML response")
        return BeautifulSoup(content, "lxml")

    def json(self, method: str, url: str, *, parameterized: bool = False, **kwargs: Any) -> Any:
        response = self.request(method, url, parameterized=parameterized, **kwargs)
        try:
            return response.json()
        except Exception:
            return json.loads(_decoded_body(response))


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for parts in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(*(int(part) for part in parts)))
        except ValueError:
            pass
    return result


def _range(value: Any) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if len(values) < 2 or values[1] < values[0]:
        return "", "", ""
    start, end = values[0], values[1]
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _integer(value: Any) -> Optional[int]:
    raw = re.sub(r"[^0-9]", "", _clean(value).replace(",", ""))
    return int(raw) if raw else None


def _capacity(value: Any) -> tuple[Optional[int], Optional[int]]:
    numbers = [int(v.replace(",", "")) for v in re.findall(r"\d[\d,]*", _clean(value))]
    return (numbers[1], numbers[0]) if len(numbers) >= 2 else (None, None)


def _branch_code(owner: str, branch: Any) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"GYEONGGI_GWANGJU_{owner}_{digest}"


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


def _signature(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def _identity_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    identities = sorted(
        _clean(row.get("raw_fields", {}).get("source_identity"))
        for row in rows
    )
    return hashlib.sha256("\n".join(identities).encode()).hexdigest()


def _base_meta(owner: str, error: str = "") -> dict[str, Any]:
    return {
        "owner": owner,
        "pages": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "discovered_links": 0,
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
    }


def _common_row(
    provider: str,
    identity: str,
    title: str,
    branch: str,
    raw_url: str,
    owner: str,
) -> dict[str, Any]:
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
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "program_type": "강좌",
        "region": GYEONGGI_GWANGJU_MUNICIPALITY_NAME,
        "municipality_code": GYEONGGI_GWANGJU_MUNICIPALITY_CODE,
        "municipality_full_name": GYEONGGI_GWANGJU_MUNICIPALITY_NAME,
    }


def _detail_pairs(soup: BeautifulSoup, root: Optional[Tag] = None) -> dict[str, str]:
    scope: BeautifulSoup | Tag = root or soup
    result: dict[str, str] = {}
    for tr in scope.select("table tr"):
        cells = tr.select("th,td")
        for index in range(0, len(cells) - 1, 2):
            if cells[index].name == "th":
                key = _clean(cells[index].get_text(" ", strip=True))
                value = _clean(cells[index + 1].get_text(" ", strip=True))
                if key and key not in result:
                    result[key] = value
    for dt in scope.select("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is not None:
            key, value = _clean(dt.get_text(" ", strip=True)), _clean(dd.get_text(" ", strip=True))
            if key and key not in result:
                result[key] = value
    return result


def _advertised_last_page(soup: BeautifulSoup, *, default: int = 1) -> int:
    values: list[int] = []
    for node in soup.select("a"):
        href, onclick = _clean(node.get("href")), _clean(node.get("onclick"))
        query = parse_qs(urlparse(href).query)
        for key in ("cpage", "currentPageNo", "page", "pageIndex"):
            for value in query.get(key, []):
                if value.isdigit():
                    values.append(int(value))
        values.extend(int(v) for v in re.findall(r"(?:movePage|goPage|page_l)\s*\(\s*['\"]?(\d+)", onclick))
    text = _clean(soup.get_text(" ", strip=True))
    values.extend(int(v) for v in re.findall(r"(?:전체\s*페이지|페이지\s*\d+\s*/|전체\s*\d+\s*건\s*,?\s*페이지\s*\d+\s*/)\s*(\d+)", text))
    return max(values, default=default)


def _safe_detail_enrichment(row: dict[str, Any], pairs: Mapping[str, str]) -> None:
    venue = _clean(pairs.get("교육장소") or pairs.get("교육장") or pairs.get("강의장소"))
    target = _clean(pairs.get("대상") or pairs.get("교육대상"))
    fee = _clean(pairs.get("교육비") or pairs.get("수강료"))
    schedule = _clean(pairs.get("교육시간") or pairs.get("강좌시간"))
    if venue and not (_PHONE_RE.search(venue) or _EMAIL_RE.search(venue)):
        row["venue"] = venue
    if target and not (_PHONE_RE.search(target) or _EMAIL_RE.search(target)):
        row["target"] = target
    if fee:
        row["fee"] = "무료" if _norm(fee) in {"무료", "무 료", "0", "0원"} else fee
    if schedule:
        row["schedule_raw"] = schedule


def _parallel_details(
    runner: _Runner,
    rows: list[dict[str, Any]],
    callback: Callable[[_Runner, dict[str, Any]], int],
    *,
    workers: int = 6,
) -> int:
    """Validate independent read-only details with bounded session workers."""

    if not rows:
        return 0
    worker_count = min(max(1, workers), len(rows))
    if runner.physical_requests + len(rows) * 2 > runner.max_requests:
        raise GyeonggiGwangjuContractError(
            "max_requests cannot cover retry-safe detail validation"
        )
    chunks = [rows[index::worker_count] for index in range(worker_count)]

    def validate(chunk: list[dict[str, Any]]) -> tuple[int, int, int, int]:
        local = _Runner(
            runner.factory,
            runner.timeout,
            max(2, len(chunk) * 2),
            runner.sleeper,
        )
        try:
            controls = sum(callback(local, row) for row in chunk)
            return (
                controls,
                local.physical_requests,
                local.retry_count,
                local.sessions_created,
            )
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(validate, chunks))
    runner.physical_requests += sum(result[1] for result in results)
    runner.retry_count += sum(result[2] for result in results)
    runner.sessions_created += sum(result[3] for result in results)
    return sum(result[0] for result in results)


# GSEEK ---------------------------------------------------------------------

def _gseek_detail_url(subject: Any, cycle: Any) -> str:
    subject, cycle = _clean(subject), _clean(cycle)
    if not _ID_RE.fullmatch(subject) or not _ID_RE.fullmatch(cycle):
        return ""
    return GYEONGGI_GWANGJU_GSEEK_URL.removesuffix("/list") + "/view?" + urlencode({
        "s_sbjct_sn": subject,
        "s_sbjct_cycl_sn": cycle,
    })


def _gseek_row(item: Mapping[str, Any]) -> dict[str, Any]:
    subject = _clean(item.get("d_sbjct_sn"))
    cycle = _clean(item.get("d_sbjct_cycl_sn"))
    identity = f"{subject}:{cycle}"
    title = _clean(item.get("d_sbjct_nm"))
    branch = _clean(item.get("d_edu_gvmnfc"))
    source_status = _clean(item.get("d_recrut_stts_nm"))
    start, end, period = _range(
        f"{item.get('d_edu_bgng_dt')} ~ {item.get('d_edu_end_dt')}"
    )
    if not _ID_RE.fullmatch(subject) or not _ID_RE.fullmatch(cycle) or not title:
        raise GyeonggiGwangjuContractError("malformed GSEEK identity/title")
    if branch not in GYEONGGI_GWANGJU_GSEEK_BRANCHES:
        raise GyeonggiGwangjuContractError(f"unknown GSEEK official branch {branch}")
    if _clean(item.get("d_co_sprvsn_id")) != GYEONGGI_GWANGJU_GSEEK_CO_SPONSOR_ID:
        raise GyeonggiGwangjuContractError(f"{identity}: foreign GSEEK co-sponsor")
    if source_status not in _GSEEK_STATUS or not start or not end:
        raise GyeonggiGwangjuContractError(f"{identity}: unknown status or invalid dates")
    row = _common_row(
        GYEONGGI_GWANGJU_GSEEK_PROVIDER,
        identity,
        title,
        branch,
        _gseek_detail_url(subject, cycle),
        "GSEEK",
    )
    row.update({
        "preserve_branch": True,
        "venue_name": branch,
        "category": " > ".join(filter(None, (
            _clean(item.get("d_clsf_depth1_nm")),
            _clean(item.get("d_clsf_depth2_nm")),
            _clean(item.get("d_clsf_depth3_nm")),
        ))) or "평생학습",
        "status": _GSEEK_STATUS[source_status],
        "period": period,
        "start_date": start,
        "end_date": end,
        "schedule_raw": " ".join(filter(None, (
            _clean(item.get("d_edu_wday_cd_nm")),
            f"{_clean(item.get('d_edu_start_time'))} ~ {_clean(item.get('d_edu_end_time'))}",
        ))),
        "target": _clean(item.get("d_sbjct_trgt_nm_1")),
        "fee": "무료" if _integer(item.get("d_sbjct_amt")) == 0 else _clean(item.get("d_sbjct_amt")),
        "capacity_total": _integer(item.get("d_edu_nope")),
        "capacity_current": _integer(item.get("d_aply_cnt")),
        "description": title,
        "application_method_raw": _clean(
            item.get("d_stdnt_chice_mthd_cd_nm") or item.get("d_rcrt_chice_mthd_cd_nm")
        ),
        "reservation_available": source_status in {"모집중", "마감임박", "대기접수", "추가접수"},
        "collection_type": "json_api+detail_html",
        "raw_fields": {
            "parser": GYEONGGI_GWANGJU_PARSER,
            "source_identity": identity,
            "subject_id": subject,
            "cycle_id": cycle,
            "source_status": source_status,
            "source_region": _clean(item.get("d_rgn")),
            "co_sponsor_id": GYEONGGI_GWANGJU_GSEEK_CO_SPONSOR_ID,
            "source_description_omitted_for_pii": True,
        },
    })
    return _clean_row(row)


def _gseek_detail_contract(soup: BeautifulSoup, row: Mapping[str, Any]) -> None:
    fields = row.get("raw_fields") or {}
    for name, expected in (
        ("s_sbjct_sn", fields.get("subject_id")),
        ("s_sbjct_cycl_sn", fields.get("cycle_id")),
    ):
        node = soup.select_one(f"input[name='{name}']")
        if node is None or _clean(node.get("value")) != _clean(expected):
            raise GyeonggiGwangjuContractError(f"GSEEK detail {name} mismatch")
    if _norm(row.get("title")) not in _norm(soup.get_text(" ", strip=True)):
        raise GyeonggiGwangjuContractError("GSEEK detail title mismatch")


def _collect_gseek(
    runner: _Runner, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = _base_meta("gseek")
    landing = runner.soup("get", GYEONGGI_GWANGJU_GSEEK_URL)
    total_node = landing.select_one("#totSubjCnt")
    region = landing.select_one("input#s_resion_cd1[name='s_resion_cd1']")
    sponsor = landing.select_one("input[name='ARK_CO_SPRVSN_ID']")
    total = _integer(total_node.get_text(" ", strip=True) if total_node else None)
    if not total or region is None or _clean(region.get("value")) != GYEONGGI_GWANGJU_GSEEK_REGION_CODE:
        raise GyeonggiGwangjuContractError("GSEEK landing total/region contract changed")
    if sponsor is None or _clean(sponsor.get("value")) != GYEONGGI_GWANGJU_GSEEK_CO_SPONSOR_ID:
        raise GyeonggiGwangjuContractError("GSEEK landing sponsor contract changed")
    page_count = math.ceil(total / GYEONGGI_GWANGJU_GSEEK_PAGE_SIZE)
    if page_count + 1 > max_pages:
        meta["source_cap_reached"] = True
        raise GyeonggiGwangjuContractError("max_pages cannot cover GSEEK census and sentinel")

    def fetch(start: int) -> Any:
        return runner.json(
            "post",
            GYEONGGI_GWANGJU_GSEEK_API_URL,
            data={
                "s_sort_by": "1",
                "s_row_start": str(start),
                "s_row_end": str(start + GYEONGGI_GWANGJU_GSEEK_PAGE_SIZE),
                "resion": GYEONGGI_GWANGJU_GSEEK_REGION_CODE,
            },
            headers={
                "Referer": GYEONGGI_GWANGJU_GSEEK_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
        )

    payloads: list[list[Any]] = []
    for page in range(page_count):
        start = page * GYEONGGI_GWANGJU_GSEEK_PAGE_SIZE + 1
        payload = fetch(start)
        expected = min(GYEONGGI_GWANGJU_GSEEK_PAGE_SIZE, total - page * GYEONGGI_GWANGJU_GSEEK_PAGE_SIZE)
        if not isinstance(payload, list) or len(payload) != expected:
            raise GyeonggiGwangjuContractError(f"GSEEK range {page + 1} row count changed")
        payloads.append(payload)
    sentinel_start = total + 1
    if fetch(sentinel_start) != []:
        raise GyeonggiGwangjuContractError("GSEEK exact post-total sentinel is not empty")
    edges = sorted({0, page_count - 1})
    for page in edges:
        start = page * GYEONGGI_GWANGJU_GSEEK_PAGE_SIZE + 1
        if _signature(fetch(start)) != _signature(payloads[page]):
            raise GyeonggiGwangjuContractError("GSEEK boundary changed during census")
    source = [
        _gseek_row(item)
        for payload in payloads
        for item in payload
        if isinstance(item, Mapping)
    ]
    if len(source) != total or len({row["provider_course_id"] for row in source}) != total:
        raise GyeonggiGwangjuContractError("GSEEK total or identity completeness failed")
    if set(row["branch"] for row in source) != set(GYEONGGI_GWANGJU_GSEEK_BRANCHES):
        raise GyeonggiGwangjuContractError("GSEEK official branch registry incomplete")
    current = [row for row in source if date.fromisoformat(row["end_date"]) >= cutoff]
    if len(current) > detail_limit:
        meta["source_cap_reached"] = True
        raise GyeonggiGwangjuContractError("detail_limit cannot cover every current GSEEK row")
    for row in current:
        _gseek_detail_contract(runner.soup("get", row["raw_url"], parameterized=True), row)
        if row.get("reservation_available"):
            row["application_url"] = row["raw_url"]
    meta.update({
        "pages": page_count + 1,
        "list_requests": page_count + 3,
        "detail_attempts": len(current),
        "detail_pages": len(current),
        "source_total": total,
        "source_rows": len(source),
        "current_count": len(current),
        "sentinel_start": sentinel_start,
        "sentinel_count": 0,
        "stability_rechecks": len(edges),
        "source_status_counts": dict(Counter(row["raw_fields"]["source_status"] for row in source)),
        "branch_counts": dict(Counter(row["branch"] for row in current)),
        "source_identity_sha256": _identity_hash(source),
        "parent_aggregate_exclusion_required": True,
        "parent_aggregate_exclusion_field": "d_co_sprvsn_id",
        "parent_aggregate_exclusion_value": GYEONGGI_GWANGJU_GSEEK_CO_SPONSOR_ID,
        "pagination_complete": True,
        "details_complete": True,
    })
    return current, meta


# Resident centres ----------------------------------------------------------

def _resident_list_url(category: str, page: int) -> str:
    return GYEONGGI_GWANGJU_RESIDENT_LIST_ENDPOINT + "?" + urlencode({
        "category": category,
        "programKind": "3",
        "mId": "0301030000",
        "currentPageNo": page,
    })


def _resident_detail_url(identity: str, category: str) -> str:
    return GYEONGGI_GWANGJU_RESIDENT_DETAIL_ENDPOINT + "?" + urlencode({
        "mId": "0301030000",
        "idx": identity,
        "category": category,
    })


def _resident_registry(soup: BeautifulSoup) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    for anchor in soup.select("a[onclick*='goCategory']"):
        match = re.search(r"goCategory\s*\(\s*['\"](\d+)['\"]", _clean(anchor.get("onclick")))
        name = _clean(anchor.get("title"))
        if match and name:
            found.append((match.group(1), name))
    return tuple(dict.fromkeys(found))


def _resident_summary(soup: BeautifulSoup) -> tuple[int, int]:
    text = _clean(soup.get_text(" ", strip=True))
    match = re.search(r"전체\s*([\d,]+)\s*건\s*,?\s*페이지\s*\d+\s*/\s*(\d+)", text)
    if match is None:
        raise GyeonggiGwangjuContractError("resident advertised total/page summary changed")
    return int(match.group(1).replace(",", "")), int(match.group(2))


def _resident_page(soup: BeautifulSoup, expected_category: str, expected_branch: str) -> list[dict[str, Any]]:
    header = ["번호", "읍면동", "프로그램명", "접수기간", "교육기간", "접수자/정원", "접수방법", "접수상태"]
    table: Optional[Tag] = None
    for candidate in soup.select("table"):
        first = candidate.select_one("tr")
        if first is not None and [_clean(x.get_text(" ", strip=True)) for x in first.select("th")] == header:
            table = candidate
            break
    if table is None:
        raise GyeonggiGwangjuContractError("resident table header changed")
    result: list[dict[str, Any]] = []
    for button in table.select("button[data-button='view'][data-idx]"):
        tr = button.find_parent("tr")
        if tr is None:
            continue
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.select("td")]
        if len(cells) != 8:
            raise GyeonggiGwangjuContractError("resident primary row width changed")
        identity = _clean(button.get("data-idx"))
        branch_term = re.fullmatch(r"\[([^\]]+)\]\s*(.+)", cells[1])
        title = re.sub(r"\s*상세보기\s*$", "", _clean(button.get_text(" ", strip=True)))
        source_status = cells[7]
        event_dates = _date_tokens(cells[4])
        source_date_anomaly = len(event_dates) < 2 or event_dates[1] < event_dates[0]
        start = event_dates[0].isoformat() if event_dates else ""
        end = event_dates[1].isoformat() if len(event_dates) >= 2 else ""
        period = f"{start} ~ {end}" if start and end else ""
        apply_start, apply_end, apply_period = _range(cells[3])
        if not _ID_RE.fullmatch(identity) or not branch_term or not title:
            raise GyeonggiGwangjuContractError("malformed resident identity/branch/title")
        branch, term = _clean(branch_term.group(1)), _clean(branch_term.group(2))
        if branch != expected_branch or source_status not in _RESIDENT_STATUS or not start or not end:
            raise GyeonggiGwangjuContractError(f"resident {identity}: branch/status/date mismatch")
        secondary = tr.find_next_sibling("tr")
        secondary_cells = (
            [_clean(td.get_text(" ", strip=True)) for td in secondary.select("td")]
            if isinstance(secondary, Tag) else []
        )
        if len(secondary_cells) != 2:
            raise GyeonggiGwangjuContractError(f"resident {identity}: secondary row changed")
        capacity_total, capacity_current = _capacity(cells[5])
        row = _common_row(
            GYEONGGI_GWANGJU_RESIDENT_PROVIDER,
            identity,
            title,
            branch,
            _resident_detail_url(identity, expected_category),
            "RESIDENT",
        )
        row.update({
            "category": "주민자치(문화)프로그램",
            "status": _RESIDENT_STATUS[source_status],
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "schedule_raw": secondary_cells[0],
            "fee": "무료" if _norm(secondary_cells[1]) in {"무료", "0", "0원"} else secondary_cells[1],
            "capacity_total": capacity_total,
            "capacity_current": capacity_current,
            "application_method_raw": cells[6],
            "reservation_available": source_status in {"접수중", "방문접수중", "대기자접수중"},
            "description": title,
            "collection_type": "html_table+detail_html",
            "raw_fields": {
                "parser": GYEONGGI_GWANGJU_PARSER,
                "source_identity": identity,
                "program_id": identity,
                "category_id": expected_category,
                "source_status": source_status,
                "term": term,
                "explicit_source_test_course": _norm(title) in GYEONGGI_GWANGJU_RESIDENT_EXCLUDED_TITLES,
                "source_date_anomaly": source_date_anomaly,
            },
        })
        result.append(_clean_row(row))
    return result


def _resident_detail(runner: _Runner, row: dict[str, Any]) -> int:
    soup = runner.soup("get", row["raw_url"], parameterized=True)
    pairs = _detail_pairs(soup)
    identity = row["raw_fields"]["program_id"]
    if _norm(pairs.get("강좌명")) != _norm(row["title"]):
        raise GyeonggiGwangjuContractError(f"resident {identity}: detail/list title mismatch")
    start, end, _ = _range(pairs.get("교육기간"))
    if start != row["start_date"] or end != row["end_date"]:
        raise GyeonggiGwangjuContractError(f"resident {identity}: detail/list date mismatch")
    _safe_detail_enrichment(row, pairs)
    controls = [
        node for node in soup.select("[data-button='write'][data-program-idx]")
        if _clean(node.get("data-program-idx")) == identity
    ]
    if controls and row.get("reservation_available") and "온라인" in _clean(row.get("application_method_raw")):
        row["application_url"] = GYEONGGI_GWANGJU_RESIDENT_APPLICATION_ENDPOINT + "?" + urlencode({
            "mId": "0301030000", "programIdx": identity,
        })
    return int(bool(controls))


def _collect_resident(
    runner: _Runner, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = _base_meta("resident")
    all_rows: list[dict[str, Any]] = []
    advertised_totals: dict[str, int] = {}
    sentinel_pages: dict[str, int] = {}
    page_counts: dict[str, list[int]] = {}
    def census_branch(
        indexed_branch: tuple[int, tuple[str, str]],
    ) -> dict[str, Any]:
        branch_index, (category, branch) = indexed_branch
        local = _Runner(
            runner.factory,
            runner.timeout,
            max_pages + 6,
            runner.sleeper,
        )
        local_list_requests = 0
        local_stability = 0
        try:
            first_soup = local.soup(
                "get", _resident_list_url(category, 1), parameterized=True
            )
            local_list_requests += 1
            if (
                branch_index == 0
                and _resident_registry(first_soup)
                != GYEONGGI_GWANGJU_RESIDENT_BRANCHES
            ):
                raise GyeonggiGwangjuContractError(
                    "resident official branch registry changed"
                )
            advertised_total, last_page = _resident_summary(first_soup)
            if last_page + 1 > max_pages:
                raise GyeonggiGwangjuContractError(
                    f"max_pages cannot cover {branch} census"
                )
            pages = [_resident_page(first_soup, category, branch)]
            if advertised_total == 0:
                if pages[0]:
                    raise GyeonggiGwangjuContractError(
                        f"{branch}: zero advertised total has rows"
                    )
                sentinel_page = 1
                repeated = _resident_page(
                    local.soup(
                        "get", _resident_list_url(category, 1), parameterized=True
                    ),
                    category,
                    branch,
                )
                local_list_requests += 1
                local_stability += 1
                if repeated:
                    raise GyeonggiGwangjuContractError(
                        f"{branch}: empty sentinel changed"
                    )
            else:
                for page in range(2, last_page + 1):
                    pages.append(_resident_page(
                        local.soup(
                            "get",
                            _resident_list_url(category, page),
                            parameterized=True,
                        ),
                        category,
                        branch,
                    ))
                    local_list_requests += 1
                sentinel_page = last_page + 1
                sentinel = _resident_page(
                    local.soup(
                        "get",
                        _resident_list_url(category, sentinel_page),
                        parameterized=True,
                    ),
                    category,
                    branch,
                )
                local_list_requests += 1
                if sentinel:
                    raise GyeonggiGwangjuContractError(
                        f"{branch}: post-last sentinel is not empty"
                    )
                for index in sorted({0, len(pages) - 1}):
                    repeated = _resident_page(
                        local.soup(
                            "get",
                            _resident_list_url(category, index + 1),
                            parameterized=True,
                        ),
                        category,
                        branch,
                    )
                    local_list_requests += 1
                    local_stability += 1
                    if _signature(repeated) != _signature(pages[index]):
                        raise GyeonggiGwangjuContractError(
                            f"{branch}: boundary changed during census"
                        )
            branch_rows = [row for page in pages for row in page]
            if len(branch_rows) != advertised_total:
                raise GyeonggiGwangjuContractError(
                    f"{branch}: advertised {advertised_total}, parsed {len(branch_rows)}"
                )
            return {
                "branch": branch,
                "rows": branch_rows,
                "advertised_total": advertised_total,
                "sentinel_page": sentinel_page,
                "page_counts": [len(page) for page in pages]
                + ([] if advertised_total == 0 else [0]),
                "list_requests": local_list_requests,
                "stability": local_stability,
                "physical_requests": local.physical_requests,
                "retry_count": local.retry_count,
                "sessions_created": local.sessions_created,
            }
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=5) as executor:
        branch_results = list(executor.map(
            census_branch,
            enumerate(GYEONGGI_GWANGJU_RESIDENT_BRANCHES),
        ))
    runner.physical_requests += sum(
        int(result["physical_requests"]) for result in branch_results
    )
    runner.retry_count += sum(int(result["retry_count"]) for result in branch_results)
    runner.sessions_created += sum(
        int(result["sessions_created"]) for result in branch_results
    )
    if runner.physical_requests > runner.max_requests:
        raise GyeonggiGwangjuContractError("resident census exceeded max_requests")
    list_requests = sum(int(result["list_requests"]) for result in branch_results)
    stability = sum(int(result["stability"]) for result in branch_results)
    for result in branch_results:
        branch = str(result["branch"])
        advertised_totals[branch] = int(result["advertised_total"])
        sentinel_pages[branch] = int(result["sentinel_page"])
        page_counts[branch] = list(result["page_counts"])
        all_rows.extend(result["rows"])
    if len({row["provider_course_id"] for row in all_rows}) != len(all_rows):
        raise GyeonggiGwangjuContractError("duplicate cross-branch resident identity")
    source_current = [
        row for row in all_rows
        if not row["raw_fields"].get("source_date_anomaly")
        and date.fromisoformat(row["end_date"]) >= cutoff
    ]
    current = [row for row in source_current if not row["raw_fields"]["explicit_source_test_course"]]
    if len(current) > detail_limit:
        meta["source_cap_reached"] = True
        raise GyeonggiGwangjuContractError("detail_limit cannot cover every current resident row")
    application_controls = _parallel_details(runner, current, _resident_detail)
    meta.update({
        "pages": sum(max(1, len(values)) for values in page_counts.values()),
        "list_requests": list_requests,
        "detail_attempts": len(current),
        "detail_pages": len(current),
        "source_total": len(all_rows),
        "source_rows": len(all_rows),
        "source_current_count": len(source_current),
        "current_count": len(current),
        "excluded_test_count": len(source_current) - len(current),
        "excluded_source_date_anomaly_count": sum(
            bool(row["raw_fields"].get("source_date_anomaly")) for row in all_rows
        ),
        "advertised_totals": advertised_totals,
        "sentinel_pages": sentinel_pages,
        "sentinel_count": 0,
        "page_counts": page_counts,
        "stability_rechecks": stability,
        "branch_count": len(GYEONGGI_GWANGJU_RESIDENT_BRANCHES),
        "branch_counts": dict(Counter(row["branch"] for row in current)),
        "source_status_counts": dict(Counter(row["raw_fields"]["source_status"] for row in all_rows)),
        "source_identity_sha256": _identity_hash(all_rows),
        "application_control_count": application_controls,
        "pagination_complete": True,
        "details_complete": True,
    })
    return current, meta


# Public libraries ----------------------------------------------------------

def _library_list_url(base: str, page: int) -> str:
    return base if page == 1 else base + "?" + urlencode({"rows": 10, "cpage": page})


def _labeled_value(text: str, label: str, labels: Iterable[str]) -> str:
    marker = re.escape(label)
    endings = "|".join(re.escape(value) for value in labels if value != label)
    pattern = rf"{marker}\s*:?\s*(.*?)(?=\s+(?:{endings})\s*:?|$)" if endings else rf"{marker}\s*:?\s*(.*)$"
    match = re.search(pattern, _clean(text))
    return _clean(match.group(1)) if match else ""


def _library_page(soup: BeautifulSoup, branch: str, base_url: str) -> list[dict[str, Any]]:
    expected_path = urlparse(base_url).path.replace("cultureWrt_list.do", "cultureWrt_wrt.do")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in soup.select("a[href*='cultureWrt_wrt.do']"):
        candidate = urljoin(base_url, _clean(link.get("href")))
        parsed, query = urlparse(candidate), parse_qs(urlparse(candidate).query)
        identity = _clean((query.get("fn_seq") or [""])[0])
        if not identity or identity in seen:
            continue
        if (
            parsed.scheme != "https"
            or parsed.hostname != "lib.gjcity.go.kr"
            or parsed.path != expected_path
            or not _ID_RE.fullmatch(identity)
        ):
            raise GyeonggiGwangjuContractError("library detail escaped official route")
        card = link.find_parent("dl") or link.find_parent("li")
        if card is None:
            raise GyeonggiGwangjuContractError(f"library {identity}: card container changed")
        matching_links = [
            node for node in card.select("a[href]")
            if _clean((parse_qs(urlparse(urljoin(base_url, _clean(node.get('href')))).query).get("fn_seq") or [""])[0]) == identity
        ]
        titles = [
            _clean(node.get_text(" ", strip=True)) for node in matching_links
            if _clean(node.get_text(" ", strip=True)) not in {"상세보기", "자세히보기"}
        ]
        title = max(titles, key=len, default=_clean(link.get_text(" ", strip=True)))
        card_text = _clean(card.get_text(" ", strip=True))
        labels = ("강좌기간", "강좌시간", "강좌대상", "접수기간", "접수현황")
        start, end, period = _range(_labeled_value(card_text, "강좌기간", labels))
        apply_start, apply_end, apply_period = _range(_labeled_value(card_text, "접수기간", labels))
        source_status = next((status for status in _LIBRARY_STATUS if status in card_text), "")
        if not title or not start or not end or source_status not in _LIBRARY_STATUS:
            raise GyeonggiGwangjuContractError(
                f"library {identity}: title/date/status contract changed"
            )
        row = _common_row(
            GYEONGGI_GWANGJU_LIBRARY_PROVIDER,
            identity,
            title,
            branch,
            candidate,
            "LIBRARY",
        )
        row.update({
            "category": "독서문화프로그램",
            "status": _LIBRARY_STATUS[source_status],
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "schedule_raw": _labeled_value(card_text, "강좌시간", labels),
            "target": _labeled_value(card_text, "강좌대상", labels),
            "description": title,
            "reservation_available": source_status == "접수중",
            "application_method_raw": "온라인 신청",
            "collection_type": "html_cards+detail_html",
            "raw_fields": {
                "parser": GYEONGGI_GWANGJU_PARSER,
                "source_identity": identity,
                "function_sequence": identity,
                "source_status": source_status,
                "official_branch": branch,
            },
        })
        result.append(_clean_row(row))
        seen.add(identity)
    return result


def _library_detail(runner: _Runner, row: dict[str, Any]) -> int:
    soup = runner.soup("get", row["raw_url"], parameterized=True)
    content = soup.select_one("#content, #contents, .contents, .conts") or soup
    text = _clean(content.get_text(" ", strip=True))
    identity = row["raw_fields"]["function_sequence"]
    if _norm(row["title"]) not in _norm(text):
        raise GyeonggiGwangjuContractError(f"library {identity}: detail/list title mismatch")
    dates = {value.isoformat() for value in _date_tokens(text)}
    if row["start_date"] not in dates or row["end_date"] not in dates:
        raise GyeonggiGwangjuContractError(f"library {identity}: detail/list date mismatch")
    pairs = _detail_pairs(soup, content if isinstance(content, Tag) else None)
    _safe_detail_enrichment(row, pairs)
    controls = []
    for node in content.select("a,button"):
        label = _clean(node.get_text(" ", strip=True))
        if "신청" in label and "신청조회" not in label:
            controls.append(node)
    return int(bool(controls))


def _collect_library(
    runner: _Runner, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = _base_meta("library")
    all_rows: list[dict[str, Any]] = []
    sentinel_pages: dict[str, int] = {}
    page_counts: dict[str, list[int]] = {}
    list_requests = 0
    stability = 0
    for branch, base_url in GYEONGGI_GWANGJU_LIBRARY_BRANCHES:
        first = runner.soup("get", _library_list_url(base_url, 1), parameterized=True)
        list_requests += 1
        pages = [_library_page(first, branch, base_url)]
        last_page = _advertised_last_page(first)
        if not pages[0]:
            raise GyeonggiGwangjuContractError(f"{branch}: first page unexpectedly empty")
        if last_page + 1 > max_pages:
            meta["source_cap_reached"] = True
            raise GyeonggiGwangjuContractError(f"max_pages cannot cover {branch} census")
        runner.sleeper(0.15)
        for page in range(2, last_page + 1):
            parsed = _library_page(
                runner.soup("get", _library_list_url(base_url, page), parameterized=True),
                branch,
                base_url,
            )
            list_requests += 1
            if not parsed:
                raise GyeonggiGwangjuContractError(f"{branch}: advertised page {page} is empty")
            pages.append(parsed)
            runner.sleeper(0.15)
        sentinel_page = last_page + 1
        sentinel = _library_page(
            runner.soup("get", _library_list_url(base_url, sentinel_page), parameterized=True),
            branch,
            base_url,
        )
        list_requests += 1
        if sentinel:
            raise GyeonggiGwangjuContractError(f"{branch}: post-last sentinel is not empty")
        sentinel_pages[branch] = sentinel_page
        for index in sorted({0, len(pages) - 1}):
            repeated = _library_page(
                runner.soup("get", _library_list_url(base_url, index + 1), parameterized=True),
                branch,
                base_url,
            )
            list_requests += 1
            stability += 1
            if _signature(repeated) != _signature(pages[index]):
                raise GyeonggiGwangjuContractError(f"{branch}: boundary changed during census")
            runner.sleeper(0.15)
        branch_rows = [row for page in pages for row in page]
        page_counts[branch] = [len(page) for page in pages] + [0]
        all_rows.extend(branch_rows)
    if len({row["provider_course_id"] for row in all_rows}) != len(all_rows):
        raise GyeonggiGwangjuContractError("duplicate cross-branch library identity")
    if set(row["branch"] for row in all_rows) != {
        branch for branch, _ in GYEONGGI_GWANGJU_LIBRARY_BRANCHES
    }:
        raise GyeonggiGwangjuContractError("library official branch registry incomplete")
    current = [row for row in all_rows if date.fromisoformat(row["end_date"]) >= cutoff]
    if len(current) > detail_limit:
        meta["source_cap_reached"] = True
        raise GyeonggiGwangjuContractError("detail_limit cannot cover every current library row")
    application_controls = sum(_library_detail(runner, row) for row in current)
    meta.update({
        "pages": sum(len(values) for values in page_counts.values()),
        "list_requests": list_requests,
        "detail_attempts": len(current),
        "detail_pages": len(current),
        "source_total": len(all_rows),
        "source_rows": len(all_rows),
        "current_count": len(current),
        "sentinel_pages": sentinel_pages,
        "sentinel_count": 0,
        "page_counts": page_counts,
        "stability_rechecks": stability,
        "branch_count": len(GYEONGGI_GWANGJU_LIBRARY_BRANCHES),
        "branch_counts": dict(Counter(row["branch"] for row in current)),
        "source_status_counts": dict(Counter(row["raw_fields"]["source_status"] for row in all_rows)),
        "source_identity_sha256": _identity_hash(all_rows),
        "application_control_count": application_controls,
        "rate_limit_fail_closed": True,
        "pagination_complete": True,
        "details_complete": True,
    })
    return current, meta


# City lecture ledgers ------------------------------------------------------

def _lecture_detail_url(endpoint: str, mid: str, identity: str) -> str:
    return endpoint + "?" + urlencode({"mId": mid, "idx": identity})


def _it_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    expected = ["년/월", "접수 구분", "교육 유형", "강좌명", "신청기간", "교육기간", "접수자/정원 (예비자/정원)", "상태"]
    table: Optional[Tag] = None
    for candidate in soup.select("table"):
        if [_clean(node.get_text(" ", strip=True)) for node in candidate.select("thead th")] == expected:
            table = candidate
            break
    if table is None:
        raise GyeonggiGwangjuContractError("citizen IT table header changed")
    result: list[dict[str, Any]] = []
    for button in table.select("[data-button='view'][data-idx]"):
        tr = button.find_parent("tr")
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.select("td")] if tr else []
        identity = _clean(button.get("data-idx"))
        if len(cells) != 8 or not _ID_RE.fullmatch(identity):
            raise GyeonggiGwangjuContractError("malformed citizen IT row")
        source_status = cells[7]
        start, end, period = _range(cells[5])
        apply_start, apply_end, apply_period = _range(cells[4])
        if source_status not in _IT_STATUS or not start or not end:
            raise GyeonggiGwangjuContractError(f"citizen IT {identity}: status/date changed")
        capacity_total, capacity_current = _capacity(cells[6])
        row = _common_row(
            GYEONGGI_GWANGJU_IT_PROVIDER,
            identity,
            cells[3],
            GYEONGGI_GWANGJU_IT_BRANCH,
            _lecture_detail_url(GYEONGGI_GWANGJU_IT_DETAIL_ENDPOINT, "0205050100", identity),
            "IT",
        )
        row.update({
            "category": f"시민정보화교육 > {cells[1]} > {cells[2]}",
            "status": _IT_STATUS[source_status],
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "schedule_raw": cells[5],
            "capacity_total": capacity_total,
            "capacity_current": capacity_current,
            "description": cells[3],
            "application_method_raw": "온라인 신청",
            "reservation_available": source_status in {"접수중", "대기자접수중"},
            "collection_type": "html_table+detail_html",
            "raw_fields": {
                "parser": GYEONGGI_GWANGJU_PARSER,
                "source_identity": identity,
                "lecture_id": identity,
                "source_status": source_status,
                "year_month": cells[0],
            },
        })
        result.append(_clean_row(row))
    return result


def _agri_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    expected = ["번호", "교육명", "신청기간", "교육기간", "접수자/정원 (예비자/정원)", "상태", "신청방식"]
    table: Optional[Tag] = None
    for candidate in soup.select("table"):
        if [_clean(node.get_text(" ", strip=True)) for node in candidate.select("thead th")] == expected:
            table = candidate
            break
    if table is None:
        raise GyeonggiGwangjuContractError("agricultural education table header changed")
    result: list[dict[str, Any]] = []
    for button in table.select("[data-button='view'][data-idx]"):
        tr = button.find_parent("tr")
        cells = [_clean(td.get_text(" ", strip=True)) for td in tr.select("td")] if tr else []
        identity = _clean(button.get("data-idx"))
        if len(cells) != 7 or not _ID_RE.fullmatch(identity):
            raise GyeonggiGwangjuContractError("malformed agricultural education row")
        source_status = cells[5]
        event_dates = _date_tokens(cells[3])
        source_date_anomaly = len(event_dates) < 2 or event_dates[1] < event_dates[0]
        start = event_dates[0].isoformat() if event_dates else ""
        end = event_dates[1].isoformat() if len(event_dates) >= 2 else ""
        period = f"{start} ~ {end}" if start and end else ""
        apply_start, apply_end, apply_period = _range(cells[2])
        if source_status not in _AGRI_STATUS or not start or not end:
            raise GyeonggiGwangjuContractError(f"agriculture {identity}: status/date changed")
        capacity_total, capacity_current = _capacity(cells[4])
        row = _common_row(
            GYEONGGI_GWANGJU_AGRI_PROVIDER,
            identity,
            cells[1],
            GYEONGGI_GWANGJU_AGRI_BRANCH,
            _lecture_detail_url(GYEONGGI_GWANGJU_AGRI_DETAIL_ENDPOINT, "0408070100", identity),
            "AGRI",
        )
        row.update({
            "category": "농업기술센터교육",
            "status": _AGRI_STATUS[source_status],
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "schedule_raw": cells[3],
            "capacity_total": capacity_total,
            "capacity_current": capacity_current,
            "description": cells[1],
            "application_method_raw": cells[6],
            "reservation_available": source_status in {"접수중", "대기자접수중"},
            "collection_type": "html_table+detail_html",
            "raw_fields": {
                "parser": GYEONGGI_GWANGJU_PARSER,
                "source_identity": identity,
                "lecture_id": identity,
                "source_status": source_status,
                "source_number": cells[0],
                "explicit_source_test_course": _norm(cells[1]) == "test",
                "source_date_anomaly": source_date_anomaly,
                "detail_description_omitted_for_pii": True,
            },
        })
        result.append(_clean_row(row))
    return result


def _city_lecture_detail(runner: _Runner, row: dict[str, Any], owner: str) -> int:
    soup = runner.soup("get", row["raw_url"], parameterized=True)
    root = soup.select_one(".bod_view, #conts") or soup
    pairs = _detail_pairs(soup, root if isinstance(root, Tag) else None)
    identity = row["raw_fields"]["lecture_id"]
    title = _clean(pairs.get("강좌명"))
    if owner == "agriculture":
        heading = root.select_one("h4") if isinstance(root, Tag) else None
        title = _clean(heading.get_text(" ", strip=True) if heading else title)
    if _norm(title) != _norm(row["title"]):
        raise GyeonggiGwangjuContractError(f"{owner} {identity}: detail/list title mismatch")
    start, end, _ = _range(pairs.get("교육기간"))
    if start != row["start_date"] or end != row["end_date"]:
        raise GyeonggiGwangjuContractError(f"{owner} {identity}: detail/list date mismatch")
    _safe_detail_enrichment(row, pairs)
    controls = [
        node for node in root.select("[data-button='write'][data-idx]")
        if _clean(node.get("data-idx")) == identity
    ] if isinstance(root, Tag) else []
    if controls and row.get("reservation_available"):
        row["application_url"] = row["raw_url"]
    return int(bool(controls))


def _collect_city_lecture(
    runner: _Runner,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    *,
    owner: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    is_it = owner == "citizen_it"
    base = GYEONGGI_GWANGJU_IT_LIST_ENDPOINT if is_it else GYEONGGI_GWANGJU_AGRI_LIST_ENDPOINT
    mid = "0205050100" if is_it else "0408070100"
    parser = _it_page if is_it else _agri_page
    meta = _base_meta(owner)
    first = runner.soup("get", base, parameterized=True, params={"mId": mid, "currentPageNo": 1})
    pages = [parser(first)]
    last_page = _advertised_last_page(first)
    if not pages[0]:
        raise GyeonggiGwangjuContractError(f"{owner}: first page unexpectedly empty")
    if last_page + 1 > max_pages:
        meta["source_cap_reached"] = True
        raise GyeonggiGwangjuContractError(f"max_pages cannot cover {owner} census")
    for page in range(2, last_page + 1):
        parsed = parser(runner.soup(
            "get", base, parameterized=True,
            params={"mId": mid, "currentPageNo": page},
        ))
        if not parsed:
            raise GyeonggiGwangjuContractError(f"{owner}: advertised page {page} is empty")
        pages.append(parsed)
    sentinel_page = last_page + 1
    sentinel = parser(runner.soup(
        "get", base, parameterized=True,
        params={"mId": mid, "currentPageNo": sentinel_page},
    ))
    if sentinel:
        raise GyeonggiGwangjuContractError(f"{owner}: post-last sentinel is not empty")
    for index in sorted({0, len(pages) - 1}):
        repeated = parser(runner.soup(
            "get", base, parameterized=True,
            params={"mId": mid, "currentPageNo": index + 1},
        ))
        if _signature(repeated) != _signature(pages[index]):
            raise GyeonggiGwangjuContractError(f"{owner}: boundary changed during census")
    source = [row for page in pages for row in page]
    if len({row["provider_course_id"] for row in source}) != len(source):
        raise GyeonggiGwangjuContractError(f"{owner}: duplicate identity")
    eligible = [
        row for row in source
        if not row["raw_fields"].get("explicit_source_test_course")
        and not row["raw_fields"].get("source_date_anomaly")
    ]
    current = [row for row in eligible if date.fromisoformat(row["end_date"]) >= cutoff]
    if len(current) > detail_limit:
        meta["source_cap_reached"] = True
        raise GyeonggiGwangjuContractError(f"detail_limit cannot cover every current {owner} row")
    application_controls = sum(_city_lecture_detail(runner, row, owner) for row in current)
    meta.update({
        "pages": len(pages) + 1,
        "list_requests": len(pages) + 1 + len({0, len(pages) - 1}),
        "detail_attempts": len(current),
        "detail_pages": len(current),
        "source_total": len(source),
        "source_rows": len(source),
        "current_count": len(current),
        "excluded_test_count": sum(
            bool(row["raw_fields"].get("explicit_source_test_course")) for row in source
        ),
        "excluded_source_date_anomaly_count": sum(
            bool(row["raw_fields"].get("source_date_anomaly")) for row in source
        ),
        "sentinel_page": sentinel_page,
        "sentinel_count": 0,
        "page_counts": [len(page) for page in pages] + [0],
        "stability_rechecks": len({0, len(pages) - 1}),
        "source_status_counts": dict(Counter(row["raw_fields"]["source_status"] for row in source)),
        "source_identity_sha256": _identity_hash(source),
        "application_control_count": application_controls,
        "pagination_complete": True,
        "details_complete": True,
    })
    return current, meta


# Youth training centre ----------------------------------------------------

def _youth_list_url(filename: str) -> str:
    return f"https://www.gjyouth.or.kr/board/{filename}"


def _youth_detail_url(filename: str, identity: str) -> str:
    return _youth_list_url(filename) + "?" + urlencode({"valnum": identity})


def _youth_source_status(value: str) -> tuple[str, bool]:
    value = _clean(value)
    if "접수중" in value:
        return "OPEN", True
    if "접수대기" in value or "준비중" in value:
        return "SCHEDULED", False
    if "강좌 진행중" in value:
        return ("OPEN", True) if "미달" in value else ("CLOSED", False)
    if any(marker in value for marker in ("접수마감", "강좌종료", "종강")):
        return "CLOSED", False
    raise GyeonggiGwangjuContractError(f"unknown youth source status {value}")


def _youth_page(
    soup: BeautifulSoup, category: str, list_filename: str, detail_filename: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for card in soup.select(".listArea02 > ul > li, .listArea02 li"):
        detail_link = card.select_one("a[href*='view_d']")
        title_link = card.select_one("dt a[href*='view_d']") or detail_link
        if detail_link is None or title_link is None:
            continue
        match = re.search(
            r"view_d\s*\(\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]\s*,\s*['\"]([^'\"]+)['\"]",
            _clean(detail_link.get("href")),
        )
        title = _clean(title_link.get_text(" ", strip=True))
        if not match or not title:
            raise GyeonggiGwangjuContractError("malformed youth course identity/title")
        identity, source_page, detail_key = match.groups()
        if detail_key != detail_filename.removesuffix(".asp"):
            raise GyeonggiGwangjuContractError(f"youth {identity}: detail partition escaped")
        values: dict[str, str] = {}
        for dd in card.select("dd"):
            text = _clean(dd.get_text(" ", strip=True)).lstrip("• ")
            if ":" in text:
                key, value = text.split(":", 1)
                values[_clean(key).replace(" ", "")] = _clean(value)
        source_status = _clean(values.get("현재"))
        status, available = _youth_source_status(source_status)
        capacity_total = _integer(values.get("수강정원"))
        schedule = " ".join(filter(None, (values.get("요일"), values.get("교육시간"))))
        row = _common_row(
            GYEONGGI_GWANGJU_YOUTH_PROVIDER,
            identity,
            title,
            GYEONGGI_GWANGJU_YOUTH_BRANCH,
            _youth_detail_url(detail_filename, identity),
            "YOUTH",
        )
        row.update({
            "category": category,
            "status": status,
            "target": values.get("교육대상"),
            "schedule_raw": schedule,
            "capacity_total": capacity_total,
            "fee": values.get("수강료"),
            "description": title,
            "reservation_available": available,
            "application_method_raw": "홈페이지 온라인 신청",
            "collection_type": "active_html_cards+detail_html",
            "raw_fields": {
                "parser": GYEONGGI_GWANGJU_PARSER,
                "source_identity": identity,
                "course_id": identity,
                "source_page": source_page,
                "list_filename": list_filename,
                "detail_filename": detail_filename,
                "source_status": source_status,
                "term_snapshot_without_dates": True,
                "instructor_omitted_for_pii_minimization": True,
            },
        })
        result.append(_clean_row(row))
    return result


def _youth_fetch_page(
    runner: _Runner,
    category: str,
    list_filename: str,
    detail_filename: str,
    page: int,
) -> list[dict[str, Any]]:
    url = _youth_list_url(list_filename)
    if page == 1:
        soup = runner.soup("get", url)
    else:
        soup = runner.soup("post", url, data={
            "valnum": "",
            "curpge": str(page),
            "tgtasp": list_filename,
            "sekword": "",
            "searcht": "",
        })
    return _youth_page(soup, category, list_filename, detail_filename)


def _youth_detail(runner: _Runner, row: dict[str, Any]) -> int:
    soup = runner.soup("get", row["raw_url"], parameterized=True)
    content = soup.select_one("#sub_content, .sub_content, #content") or soup
    text = _clean(content.get_text(" ", strip=True))
    identity = row["raw_fields"]["course_id"]
    if _norm(row["title"]) not in _norm(text):
        raise GyeonggiGwangjuContractError(f"youth {identity}: detail/list title mismatch")
    labels = ("교육기간", "강좌기간", "운영기간", "교육시간", "교육대상", "수강료")
    for label in ("교육기간", "강좌기간", "운영기간"):
        start, end, period = _range(_labeled_value(text, label, labels))
        if start and end:
            row.update({"start_date": start, "end_date": end, "period": period})
            row["raw_fields"]["term_snapshot_without_dates"] = False
            break
    controls = [
        node for node in content.select("a,button,input")
        if "신청" in _clean(node.get_text(" ", strip=True) or node.get("value"))
    ] if isinstance(content, Tag) else []
    if controls and row.get("reservation_available"):
        row["application_url"] = row["raw_url"]
    return int(bool(controls))


def _collect_youth(
    runner: _Runner, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = _base_meta("youth")
    all_rows: list[dict[str, Any]] = []
    page_counts: dict[str, list[int]] = {}
    sentinel_pages: dict[str, int] = {}
    list_requests = 0
    stability = 0
    for category, list_filename, detail_filename in GYEONGGI_GWANGJU_YOUTH_PARTITIONS:
        first_url = _youth_list_url(list_filename)
        first_soup = runner.soup("get", first_url)
        list_requests += 1
        pages = [_youth_page(first_soup, category, list_filename, detail_filename)]
        last_page = _advertised_last_page(first_soup)
        if not pages[0] or last_page + 1 > max_pages:
            meta["source_cap_reached"] = last_page + 1 > max_pages
            raise GyeonggiGwangjuContractError(f"youth {category}: first page/max_pages contract")
        for page in range(2, last_page + 1):
            parsed = _youth_fetch_page(runner, category, list_filename, detail_filename, page)
            list_requests += 1
            if not parsed:
                raise GyeonggiGwangjuContractError(f"youth {category}: advertised page {page} empty")
            pages.append(parsed)
        sentinel_page = last_page + 1
        sentinel = _youth_fetch_page(runner, category, list_filename, detail_filename, sentinel_page)
        list_requests += 1
        if sentinel:
            raise GyeonggiGwangjuContractError(f"youth {category}: post-last sentinel is not empty")
        sentinel_pages[category] = sentinel_page
        for index in sorted({0, len(pages) - 1}):
            repeated = _youth_fetch_page(
                runner, category, list_filename, detail_filename, index + 1
            )
            list_requests += 1
            stability += 1
            if _signature(repeated) != _signature(pages[index]):
                raise GyeonggiGwangjuContractError(f"youth {category}: boundary changed")
        page_counts[category] = [len(page) for page in pages] + [0]
        all_rows.extend(row for page in pages for row in page)
    if len({row["provider_course_id"] for row in all_rows}) != len(all_rows):
        raise GyeonggiGwangjuContractError("duplicate youth identity")
    if len(all_rows) > detail_limit:
        meta["source_cap_reached"] = True
        raise GyeonggiGwangjuContractError("detail_limit cannot cover every active youth row")
    application_controls = sum(_youth_detail(runner, row) for row in all_rows)
    meta.update({
        "pages": sum(len(values) for values in page_counts.values()),
        "list_requests": list_requests,
        "detail_attempts": len(all_rows),
        "detail_pages": len(all_rows),
        "source_total": len(all_rows),
        "source_rows": len(all_rows),
        "current_count": len(all_rows),
        "sentinel_pages": sentinel_pages,
        "sentinel_count": 0,
        "page_counts": page_counts,
        "stability_rechecks": stability,
        "branch_count": 1,
        "branch_counts": {GYEONGGI_GWANGJU_YOUTH_BRANCH: len(all_rows)},
        "partition_counts": dict(Counter(row["category"] for row in all_rows)),
        "source_status_counts": dict(Counter(row["raw_fields"]["source_status"] for row in all_rows)),
        "source_identity_sha256": _identity_hash(all_rows),
        "application_control_count": application_controls,
        "active_catalogue_without_guessed_dates": True,
        "pagination_complete": True,
        "details_complete": True,
    })
    return all_rows, meta


def collect_gyeonggi_gwangju_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = GYEONGGI_GWANGJU_DEFAULT_MAX_PAGES,
    detail_limit: int = GYEONGGI_GWANGJU_DEFAULT_DETAIL_LIMIT,
    *,
    max_requests: int = GYEONGGI_GWANGJU_DEFAULT_MAX_REQUESTS,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, atomic Gyeonggi Gwangju owner snapshot."""

    owner = {
        GYEONGGI_GWANGJU_GSEEK_PROVIDER: "gseek",
        GYEONGGI_GWANGJU_RESIDENT_PROVIDER: "resident",
        GYEONGGI_GWANGJU_LIBRARY_PROVIDER: "library",
        GYEONGGI_GWANGJU_IT_PROVIDER: "citizen_it",
        GYEONGGI_GWANGJU_AGRI_PROVIDER: "agriculture",
        GYEONGGI_GWANGJU_YOUTH_PROVIDER: "youth",
    }.get(_provider(target), "unknown")
    meta = _base_meta(owner)
    if not is_gyeonggi_gwangju_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match an exact canonical Gyeonggi Gwangju owner route"
        )
        return [], GYEONGGI_GWANGJU_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], GYEONGGI_GWANGJU_PARSER, meta
        session_factory = _default_session_factory
    try:
        timeout = _positive(timeout, "timeout")
        max_pages = _positive(max_pages, "max_pages")
        detail_limit = _positive(detail_limit, "detail_limit")
        max_requests = _positive(max_requests, "max_requests")
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], GYEONGGI_GWANGJU_PARSER, meta

    runner = _Runner(session_factory, timeout, max_requests, sleeper)
    try:
        try:
            if owner == "gseek":
                rows, meta = _collect_gseek(runner, cutoff, max_pages, detail_limit)
            elif owner == "resident":
                rows, meta = _collect_resident(runner, cutoff, max_pages, detail_limit)
            elif owner == "library":
                rows, meta = _collect_library(runner, cutoff, max_pages, detail_limit)
            elif owner == "citizen_it":
                rows, meta = _collect_city_lecture(
                    runner, cutoff, max_pages, detail_limit, owner=owner
                )
            elif owner == "agriculture":
                rows, meta = _collect_city_lecture(
                    runner, cutoff, max_pages, detail_limit, owner=owner
                )
            else:
                rows, meta = _collect_youth(runner, max_pages, detail_limit)
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in rows]))
            if len(result) != len(rows):
                raise GyeonggiGwangjuContractError(
                    f"dedupe changed complete row count {len(rows)} to {len(result)}"
                )
            forbidden_keys = {"instructor", "phone", "contact", "email"}
            if any(forbidden_keys.intersection(row) for row in result):
                raise GyeonggiGwangjuContractError("PII-bearing output field detected")
            meta.update({
                "returned_count": len(result),
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "discovered_links": int(meta.get("source_rows") or 0),
                "pagination_detected": int(meta.get("pages") or 0) > 1,
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
            })
            return result, GYEONGGI_GWANGJU_PARSER, meta
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
            })
            return [], GYEONGGI_GWANGJU_PARSER, meta
    finally:
        runner.close()


collect = collect_gyeonggi_gwangju_education_courses


__all__ = [name for name in globals() if name.startswith("GYEONGGI_GWANGJU_")] + [
    "GyeonggiGwangjuContractError",
    "collect",
    "collect_gyeonggi_gwangju_education_courses",
    "is_gyeonggi_gwangju_education_target",
    "is_target",
]
