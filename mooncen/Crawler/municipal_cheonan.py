"""Fail-closed public education and experience collectors for Cheonan-si.

The audited municipality codes are Cheonan-si 4413000000, Dongnam-gu
4413100000 and Seobuk-gu 4413300000.  Six independent public ledgers are
kept as separate provider identities: the city integrated reservation
education and experience ledgers, the municipal library programme ledger,
Seongjeong and Dujeong lifelong-learning centres, the Cheonan disability
lifelong-learning centre, and the Cheonan media centre Bichae.

Only public list and detail resources are read.  Applicant lists, identity
verification, login, application, cancellation, payment, file download and
application-state lookup endpoints are never requested.  Production callers
must inject the repository managed session factory.  Raw requests are only
available behind an explicit live-audit/test switch.
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
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


CHEONAN_MUNICIPALITY_CODE = "4413000000"
CHEONAN_DONGNAM_CODE = "4413100000"
CHEONAN_SEOBUK_CODE = "4413300000"
CHEONAN_MUNICIPALITY_NAME = "충청남도 천안시"
CHEONAN_DONGNAM_NAME = "충청남도 천안시 동남구"
CHEONAN_SEOBUK_NAME = "충청남도 천안시 서북구"

CHEONAN_INTEGRATED_PROVIDER = "MUNI_WWW_CHEONAN_GO_KR_5BC13FB4"
CHEONAN_INTEGRATED_CANDIDATE_ID = "MUNI_IR_5B204E3F7C41"
CHEONAN_INTEGRATED_URL = (
    "https://www.cheonan.go.kr/prog/yeyakEdu/yeyak/sub01_01/list.do"
)
CHEONAN_INTEGRATED_LIST_ENDPOINT = CHEONAN_INTEGRATED_URL
CHEONAN_INTEGRATED_DETAIL_ENDPOINT = (
    "https://www.cheonan.go.kr/prog/yeyakEdu/yeyak/sub01_01/view.do"
)
CHEONAN_INTEGRATED_APPLICATION_ENDPOINT = (
    "https://www.cheonan.go.kr/prog/yeyakEduAplcnt/yeyak/sub01_01/write.do"
)

CHEONAN_LIBRARY_PROVIDER = "MUNI_WWW_CHEONAN_GO_KR_7F8F5560"
CHEONAN_LIBRARY_CANDIDATE_ID = "MUNI_IR_EF4401CBD71B"
CHEONAN_LIBRARY_URL = (
    "https://www.cheonan.go.kr/prog/libLctr/lib/sub02_01/list.do"
)
CHEONAN_LIBRARY_LIST_ENDPOINT = CHEONAN_LIBRARY_URL
CHEONAN_LIBRARY_DETAIL_ENDPOINT = (
    "https://www.cheonan.go.kr/prog/libLctr/lib/sub02_01/view.do"
)
CHEONAN_LIBRARY_APPLICATION_ENDPOINT = (
    "https://www.cheonan.go.kr/prog/libLctrAplcnt/lib/sub02_01/write.do"
)

CHEONAN_SEONGJEONG_PROVIDER = "MUNI_WWW_CHEONAN_GO_KR_C97CA6FD"
CHEONAN_SEONGJEONG_CANDIDATE_ID = "MUNI_IR_82155B0EE0E2"
CHEONAN_SEONGJEONG_URL = (
    "https://www.cheonan.go.kr/prog/lllSjLctr/lll/sub04_01_01_01/list.do"
)
CHEONAN_SEONGJEONG_DETAIL_ENDPOINT = (
    "https://www.cheonan.go.kr/prog/lllSjLctr/lll/sub04_01_01_01/view.do"
)
CHEONAN_SEONGJEONG_APPLICATION_ENDPOINT = (
    "https://www.cheonan.go.kr/prog/lllSjLctrAplcnt/lll/sub04_01_01_01/write.do"
)
CHEONAN_SEONGJEONG_BRANCH = "성정평생학습관"

CHEONAN_DUJEONG_PROVIDER = "MUNI_WWW_CHEONAN_GO_KR_EA8D366B"
CHEONAN_DUJEONG_CANDIDATE_ID = "MUNI_IR_54F52FF28EC8"
CHEONAN_DUJEONG_URL = (
    "https://www.cheonan.go.kr/prog/lllDjLctr/lll/sub04_01_02_01/list.do"
)
CHEONAN_DUJEONG_DETAIL_ENDPOINT = (
    "https://www.cheonan.go.kr/prog/lllDjLctr/lll/sub04_01_02_01/view.do"
)
CHEONAN_DUJEONG_APPLICATION_ENDPOINT = (
    "https://www.cheonan.go.kr/prog/lllDjLctrAplcnt/lll/sub04_01_02_01/write.do"
)
CHEONAN_DUJEONG_BRANCH = "두정평생학습관"

CHEONAN_DISABILITY_PROVIDER = "MUNI_WWW_CHEONANLIFEEDU_ORG_41183F3B"
CHEONAN_DISABILITY_CANDIDATE_ID = "MUNI_IR_428F75E651A7"
CHEONAN_DISABILITY_URL = (
    "https://www.cheonanlifeedu.org/bbs/board.php?bo_table=edu_app"
)
CHEONAN_DISABILITY_LIST_ENDPOINT = "https://www.cheonanlifeedu.org/bbs/board.php"
CHEONAN_DISABILITY_APPLICATION_ENDPOINT = (
    "https://www.cheonanlifeedu.org/bbs/edu_write_update.php"
)
CHEONAN_DISABILITY_STATE_ENDPOINT = (
    "https://www.cheonanlifeedu.org/edu/edu_state.php"
)
CHEONAN_DISABILITY_BRANCH = "천안시장애인평생교육센터"

CHEONAN_MEDIA_PROVIDER = (
    "MUNI_WWW_XN_2Z1BR4K89DEOA28DJVFZVASSQ98BDZK_KR_81F"
)
CHEONAN_MEDIA_CANDIDATE_ID = "MUNI_IR_5BA1790CEA99"
CHEONAN_MEDIA_URL = (
    "https://www.xn--2z1br4k89deoa28djvfzvassq98bdzk.kr/edu/list.php"
)
CHEONAN_MEDIA_DETAIL_ENDPOINT = (
    "https://www.xn--2z1br4k89deoa28djvfzvassq98bdzk.kr/edu/view.php"
)
CHEONAN_MEDIA_APPLICATION_ENDPOINT = (
    "https://www.xn--2z1br4k89deoa28djvfzvassq98bdzk.kr/edu/reg.php"
)
CHEONAN_MEDIA_BRANCH = "천안시영상미디어센터 비채"

CHEONAN_EXPERIENCE_PROVIDER = "MUNI_WWW_CHEONAN_GO_KR_478DFA4B"
CHEONAN_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_16B801E6D0C9"
CHEONAN_EXPERIENCE_URL = (
    "https://www.cheonan.go.kr/prog/yeyakExprn/yeyak/sub03_01/list.do"
)
CHEONAN_EXPERIENCE_LIST_ENDPOINT = CHEONAN_EXPERIENCE_URL
CHEONAN_EXPERIENCE_DETAIL_ENDPOINT = (
    "https://www.cheonan.go.kr/prog/yeyakExprn/yeyak/sub03_01/view.do"
)
CHEONAN_EXPERIENCE_APPLICATION_ENDPOINT = (
    "https://www.cheonan.go.kr/prog/yeyakExprnSessAplcnt/yeyak/"
    "sub03_01/write.do"
)

CHEONAN_EXECUTING_TARGETS = (
    (CHEONAN_INTEGRATED_PROVIDER, CHEONAN_INTEGRATED_URL),
    (CHEONAN_LIBRARY_PROVIDER, CHEONAN_LIBRARY_URL),
    (CHEONAN_SEONGJEONG_PROVIDER, CHEONAN_SEONGJEONG_URL),
    (CHEONAN_DUJEONG_PROVIDER, CHEONAN_DUJEONG_URL),
    (CHEONAN_DISABILITY_PROVIDER, CHEONAN_DISABILITY_URL),
    (CHEONAN_MEDIA_PROVIDER, CHEONAN_MEDIA_URL),
    (CHEONAN_EXPERIENCE_PROVIDER, CHEONAN_EXPERIENCE_URL),
)

CHEONAN_LIFELONG_PORTAL_ALIAS_URL = "https://www.cheonan.go.kr/lll.do"
CHEONAN_MEDIA_INFO_ALIAS_URL = (
    "https://www.xn--2z1br4k89deoa28djvfzvassq98bdzk.kr/"
    "sub.php?menucode=0201"
)
CHEONAN_FINDING_LIFELONG_APPLICATIONS_URL = (
    "https://www.cheonan.go.kr/prog/lllLfLctrAply/lll/sub03_02/list.do"
)
CHEONAN_SPORTS_URL = (
    "https://sports.cauc.or.kr/lecture/llist/index/"
    "CHEONAN03/2001/A/100/1/2/3/4/5/6/7/1/-/-/1/3"
)
CHEONAN_SPORTS_EXISTING_PROVIDER = "MUNI_SPORTS_CAUC_OR_KR_05B3AD85"
CHEONAN_PROVINCIAL_LIFELONG_URL = "https://cle.cne.go.kr/"
CHEONAN_PROVINCIAL_ONLINE_URL = "https://www.clehrd.or.kr/clehrd/sub02_01_05.do"

CHEONAN_LIBRARY_BRANCHES: tuple[tuple[str, str, str], ...] = (
    ("AD", "도서관정책과", CHEONAN_MUNICIPALITY_CODE),
    ("JY", "중앙도서관", CHEONAN_DONGNAM_CODE),
    ("SG", "성거도서관", CHEONAN_SEOBUK_CODE),
    ("SY", "쌍용도서관", CHEONAN_SEOBUK_CODE),
    ("AW", "아우내도서관", CHEONAN_DONGNAM_CODE),
    ("DS", "도솔도서관", CHEONAN_SEOBUK_CODE),
    ("DJ", "두정도서관", CHEONAN_SEOBUK_CODE),
    ("SB", "신방도서관", CHEONAN_DONGNAM_CODE),
    ("CS", "청수도서관", CHEONAN_DONGNAM_CODE),
    ("JS", "직산도서관", CHEONAN_SEOBUK_CODE),
)

CHEONAN_LIBRARY_BRANCH_BY_SHORT: Mapping[str, tuple[str, str]] = {
    name.removesuffix("도서관").removesuffix("과"): (name, code)
    for _, name, code in CHEONAN_LIBRARY_BRANCHES
}
CHEONAN_LIBRARY_BRANCH_BY_SHORT = {
    **CHEONAN_LIBRARY_BRANCH_BY_SHORT,
    "도서관정책과": ("도서관정책과", CHEONAN_MUNICIPALITY_CODE),
}

CHEONAN_DONGNAM_REGIONS = frozenset({
    "목천읍", "풍세면", "광덕면", "북면", "성남면", "수신면", "병천면",
    "동면", "중앙동", "문성동", "원성1동", "원성2동", "봉명동", "일봉동",
    "신방동", "청룡동", "신안동", "유량동",
})
CHEONAN_SEOBUK_REGIONS = frozenset({
    "성환읍", "성거읍", "직산읍", "입장면", "성정1동", "성정2동",
    "쌍용1동", "쌍용2동", "쌍용3동", "백석동", "불당1동", "불당2동",
    "부성1동", "부성2동",
})

CHEONAN_INTEGRATED_DELEGATED_INSTITUTIONS = frozenset({
    "중앙도서관", "성거도서관", "쌍용도서관", "아우내도서관", "도솔도서관",
    "두정도서관", "신방도서관", "청수도서관", "직산도서관",
    CHEONAN_SEONGJEONG_BRANCH, CHEONAN_DUJEONG_BRANCH,
})

CHEONAN_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "integrated_education": {
        "provider": CHEONAN_INTEGRATED_PROVIDER,
        "url": CHEONAN_INTEGRATED_URL,
        "decision": "collect_direct_city_education; exclude delegated library/lifelong aliases",
    },
    "libraries": {
        "provider": CHEONAN_LIBRARY_PROVIDER,
        "url": CHEONAN_LIBRARY_URL,
        "decision": "one municipal library owner with nine official library branches",
    },
    "seongjeong_lifelong": {
        "provider": CHEONAN_SEONGJEONG_PROVIDER,
        "url": CHEONAN_SEONGJEONG_URL,
        "decision": "independent application ledger",
    },
    "dujeong_lifelong": {
        "provider": CHEONAN_DUJEONG_PROVIDER,
        "url": CHEONAN_DUJEONG_URL,
        "decision": "independent application ledger",
    },
    "disability_lifelong": {
        "provider": CHEONAN_DISABILITY_PROVIDER,
        "url": CHEONAN_DISABILITY_URL,
        "decision": "official city-linked independent public programme ledger",
    },
    "media_centre": {
        "provider": CHEONAN_MEDIA_PROVIDER,
        "url": CHEONAN_MEDIA_URL,
        "decision": "canonical education list replaces information-page candidate alias",
    },
    "lifelong_portal_landing": {
        "url": CHEONAN_LIFELONG_PORTAL_ALIAS_URL,
        "decision": "information landing; exact course ledgers are canonical",
    },
    "finding_lifelong_applications": {
        "url": CHEONAN_FINDING_LIFELONG_APPLICATIONS_URL,
        "decision": "hard_exclude applicant-request ledger containing masked applicant names",
    },
    "experience": {
        "provider": CHEONAN_EXPERIENCE_PROVIDER,
        "url": CHEONAN_EXPERIENCE_URL,
        "decision": "independent canonical experience ledger; never mix into education owner",
    },
    "municipal_sports": {
        "provider": CHEONAN_SPORTS_EXISTING_PROVIDER,
        "url": CHEONAN_SPORTS_URL,
        "decision": "existing separate sports owner; candidate snippet was a false association",
    },
    "provincial_education_office": {
        "urls": (CHEONAN_PROVINCIAL_LIFELONG_URL, CHEONAN_PROVINCIAL_ONLINE_URL),
        "decision": "separate Chungcheongnam-do education-office/provincial owners",
    },
}

CHEONAN_LIVE_AUDIT_BASELINE: Mapping[str, Mapping[str, Any]] = {
    "integrated": {
        "source_total": 2202,
        "source_current_count": 11,
        "current_count": 10,
        "sorted_identity_sha256": (
            "b050b2842c9e39d83d64385f2869603c7214ed513b0d1a141ca6a424edf68e7c"
        ),
    },
    "library": {
        "source_total": 31,
        "source_current_count": 19,
        "current_count": 19,
        "sorted_identity_sha256": (
            "26c1d541b4ec314be4ca51ac72708fbac931cbb77016d2fd313e4e0864f6911b"
        ),
    },
    "seongjeong": {
        "source_total": 62,
        "source_current_count": 62,
        "current_count": 62,
        "sorted_identity_sha256": (
            "28b4355933bfb01e79db85cc48e21efd0f7944eecc764ebafac90abc513e41c7"
        ),
    },
    "dujeong": {
        "source_total": 40,
        "source_current_count": 38,
        "current_count": 38,
        "sorted_identity_sha256": (
            "17db653ac587e2dac3ee4d0bad1116b177c2380baee36ab6234b1f277ecdf24b"
        ),
    },
    "disability": {
        "source_total": 57,
        "source_current_count": 14,
        "current_count": 13,
        "sorted_identity_sha256": (
            "19fd4666fd093e7e4a9a413b01fda7451707f7cba2373d764f116868d177fae9"
        ),
    },
    "media": {
        "source_total": 155,
        "source_current_count": 12,
        "current_count": 12,
        "sorted_identity_sha256": (
            "0b58352f8e66912b6cab8eb0eb3fd92efba9b424436fc663bc298ee33de2df42"
        ),
    },
    "experience": {
        "source_total": 85,
        "source_current_count": 12,
        "current_count": 12,
        "sorted_identity_sha256": (
            "de090d9c55fe1639a7069fb4031a40dda55745c8940ce7afc36751f86c433645"
        ),
    },
}

CHEONAN_PARSER = (
    "cheonan_owner_dispatch+complete_advertised_pagination+empty_or_no_new_sentinel+"
    "all_current_safe_details+official_branches+delegated_alias_exclusion+"
    "pii_minimized+no_application_calls"
)
CHEONAN_DEFAULT_MAX_PAGES = 100
CHEONAN_DEFAULT_DETAIL_LIMIT = 500
CHEONAN_DEFAULT_MAX_REQUESTS = 1_000
CHEONAN_SESSION_REQUEST_LIMIT = 80

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]

_SPACE_RE = re.compile(r"\s+")
_ID_RE = re.compile(r"[1-9]\d*")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_INTEGRATED_STATUS = {
    "접수중": "OPEN",
    "대기자 접수중": "OPEN",
    "접수 예정": "SCHEDULED",
    "접수마감": "CLOSED",
}
_LIBRARY_STATUS = {
    "모집예정": "SCHEDULED",
    "모집중": "OPEN",
    "대기자모집중": "OPEN",
    "모집마감": "CLOSED",
}
_SEONGJEONG_STATUS = {
    "우선접수 모집중": "OPEN",
    "신규접수 모집중": "OPEN",
    "일반접수 모집중": "OPEN",
    "추가접수 모집중": "OPEN",
    "모집예정": "SCHEDULED",
    "모집마감": "CLOSED",
}
_DUJEONG_STATUS = {
    "모집중": "OPEN",
    "대기자모집중": "OPEN",
    "추가 모집중": "OPEN",
    "추가대기자 모집중": "OPEN",
    "모집예정": "SCHEDULED",
    "모집마감": "CLOSED",
}
_DISABILITY_STATUS = {"진행": "OPEN", "대기": "SCHEDULED", "종료": "CLOSED"}
_EXPERIENCE_STATUS = {
    "접수하기": "OPEN",
    "접수예정": "SCHEDULED",
    "접수 예정": "SCHEDULED",
    "접수마감": "CLOSED",
}
_EXPERIENCE_SELECTION_METHODS = frozenset({"선착순", "승인제"})

CHEONAN_APPLICATION_PATH_PREFIXES = (
    "/prog/yeyakEduAplcnt/",
    "/prog/libLctrAplcnt/",
    "/prog/lllSjLctrAplcnt/",
    "/prog/lllDjLctrAplcnt/",
    "/prog/yeyakExprnSessAplcnt/",
    "/bbs/edu_write_update.php",
    "/edu/edu_state.php",
    "/edu/reg.php",
)


class CheonanContractError(ValueError):
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
        raise CheonanContractError(f"{name} must be a positive integer") from exc
    if result < 1:
        raise CheonanContractError(f"{name} must be a positive integer")
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


_TARGETS = dict(CHEONAN_EXECUTING_TARGETS)

_OWNERS = {
    CHEONAN_INTEGRATED_PROVIDER: "integrated",
    CHEONAN_LIBRARY_PROVIDER: "library",
    CHEONAN_SEONGJEONG_PROVIDER: "seongjeong",
    CHEONAN_DUJEONG_PROVIDER: "dujeong",
    CHEONAN_DISABILITY_PROVIDER: "disability",
    CHEONAN_MEDIA_PROVIDER: "media",
    CHEONAN_EXPERIENCE_PROVIDER: "experience",
}


def is_cheonan_education_target(target: Any) -> bool:
    canonical = _TARGETS.get(_provider(target))
    return bool(canonical and _exact_target(_target_url(target), canonical))


is_target = is_cheonan_education_target


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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


def _validate_response(response: Any, expected_url: str, *, parameterized: bool) -> None:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise CheonanContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise CheonanContractError("redirects are not accepted")
    final = _clean(getattr(response, "url", ""))
    if final:
        got, wanted = urlparse(final), urlparse(expected_url)
        if parameterized:
            if (got.scheme, got.hostname, got.port, got.path) != (
                "https", wanted.hostname, None, wanted.path
            ):
                raise CheonanContractError("response escaped the audited endpoint")
        elif not _exact_target(final, expected_url):
            raise CheonanContractError("response escaped the canonical URL")
    body = _decoded_body(response)
    blocked = (
        "403 Forbidden", "Access Denied", "WebKnight Application Firewall Alert",
        "비정상적으로 빠른 요청", "서비스 이용이 제한",
    )
    if any(marker in body for marker in blocked):
        raise CheonanContractError("official source rate-limit/WAF response")


class _Runner:
    def __init__(
        self, factory: SessionFactory, timeout: int, max_requests: int, sleeper: Sleeper
    ) -> None:
        self.factory = factory
        self.timeout = timeout
        self.max_requests = max_requests
        self.sleeper = sleeper
        self.session: Any = None
        self.session_requests = CHEONAN_SESSION_REQUEST_LIMIT
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

    def get(self, url: str, *, parameterized: bool = True, **kwargs: Any) -> Any:
        path = urlparse(url).path
        if any(path.startswith(prefix) for prefix in CHEONAN_APPLICATION_PATH_PREFIXES):
            raise CheonanContractError("application endpoint invocation is forbidden")
        last: Optional[Exception] = None
        for attempt in range(2):
            if self.physical_requests >= self.max_requests:
                raise CheonanContractError(
                    f"max_requests cap {self.max_requests} exhausted"
                )
            if self.session is None or self.session_requests >= CHEONAN_SESSION_REQUEST_LIMIT:
                self._new()
            self.physical_requests += 1
            self.session_requests += 1
            try:
                response = self.session.get(
                    url, timeout=self.timeout, allow_redirects=False, **kwargs
                )
                _validate_response(response, url, parameterized=parameterized)
                return response
            except Exception as exc:
                last = exc
                if attempt == 0:
                    self.retry_count += 1
                    self._new()
                    self.sleeper(0.2)
        raise CheonanContractError(_clean(last) or "request failed")

    def soup(self, url: str, *, parameterized: bool = True, **kwargs: Any) -> BeautifulSoup:
        return BeautifulSoup(
            _decoded_body(self.get(url, parameterized=parameterized, **kwargs)), "lxml"
        )


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for parts in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(*(int(part) for part in parts)))
        except ValueError:
            pass
    return result


def _date_range(value: Any, *, allow_single: bool = False) -> tuple[str, str, str, bool]:
    values = _date_tokens(value)
    if len(values) == 1 and allow_single:
        item = values[0].isoformat()
        return item, item, item, True
    if len(values) < 2:
        return "", "", "", True
    start, end = values[0], values[1]
    if end < start:
        return "", "", "", True
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}", False


def _numbers(value: Any) -> list[int]:
    return [int(raw.replace(",", "")) for raw in re.findall(r"\d[\d,]*", _clean(value))]


def _capacity(value: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    text = _clean(value)
    values = _numbers(text)
    if not values:
        return None, None, None
    if "제한없음" in text:
        return None, values[0], None
    if len(values) >= 2:
        wait = values[2] if len(values) >= 3 else None
        return values[1], values[0], wait
    return values[0], None, None


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
    return hashlib.sha256("\n".join(sorted(_clean(value) for value in values)).encode()).hexdigest()


def _branch_code(owner: str, branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"CHEONAN_{owner.upper()}_{digest}"


def _municipality_name(code: str) -> str:
    return {
        CHEONAN_MUNICIPALITY_CODE: CHEONAN_MUNICIPALITY_NAME,
        CHEONAN_DONGNAM_CODE: CHEONAN_DONGNAM_NAME,
        CHEONAN_SEOBUK_CODE: CHEONAN_SEOBUK_NAME,
    }[code]


def _municipality_for_region(region: str) -> tuple[str, str]:
    if region in CHEONAN_DONGNAM_REGIONS:
        return CHEONAN_DONGNAM_CODE, CHEONAN_DONGNAM_NAME
    if region in CHEONAN_SEOBUK_REGIONS:
        return CHEONAN_SEOBUK_CODE, CHEONAN_SEOBUK_NAME
    if not region:
        return CHEONAN_MUNICIPALITY_CODE, CHEONAN_MUNICIPALITY_NAME
    raise CheonanContractError(f"unknown official Cheonan region: {region}")


def _common_row(
    provider: str,
    identity: str,
    title: str,
    branch: str,
    raw_url: str,
    owner: str,
    municipality_code: str,
) -> dict[str, Any]:
    municipality_name = _municipality_name(municipality_code)
    experience = owner == "experience"
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
        "domain_category": "체험·견학" if experience else "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험" if experience else "공공강좌",
        "service_group_policy": "locked",
        "classification_locked": True,
        "program_type": "체험" if experience else "강좌",
        "region": municipality_name,
        "municipality_code": municipality_code,
        "municipality_full_name": municipality_name,
    }


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
    }


def _table_with_header(soup: BeautifulSoup, header: list[str]) -> Tag:
    expected = [_norm(value) for value in header]
    for table in soup.select("table"):
        row = table.select_one("thead tr") or table.select_one("tr")
        if row is None:
            continue
        found = [_norm(cell.get_text(" ", strip=True)) for cell in row.select("th")]
        if found == expected:
            return table
    raise CheonanContractError(f"table header changed: {' / '.join(header)}")


def _cheonan_summary(soup: BeautifulSoup) -> tuple[int, int]:
    text = _clean(soup.get_text(" ", strip=True))
    match = re.search(
        r"총\s*게시물\s*([\d,]+)\s*,\s*페이지\s*\d+\s*/\s*(\d+)", text
    )
    if match is None:
        raise CheonanContractError("advertised total/page summary changed")
    return int(match.group(1).replace(",", "")), int(match.group(2))


def _detail_pairs(root: Tag) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in root.select(".pe-content li, .info-list li"):
        key = item.select_one(".subjact")
        value = item.select_one(".con")
        if key is not None and value is not None:
            result[_clean(key.get_text(" ", strip=True))] = _clean(
                value.get_text(" ", strip=True)
            )
    return result


def _integrated_detail_url(identity: str) -> str:
    return CHEONAN_INTEGRATED_DETAIL_ENDPOINT + "?" + urlencode({"eduNo": identity})


def _integrated_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    header = [
        "기관구분", "지역구분", "강좌명", "접수기간", "교육기간", "교육시간",
        "선정방식", "신청 /모집 인원 (명)", "접수상태",
    ]
    table = _table_with_header(soup, header)
    result: list[dict[str, Any]] = []
    for anchor in table.select("tbody a[href*='eduNo=']"):
        tr = anchor.find_parent("tr")
        if tr is None:
            continue
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select("td")]
        if len(cells) != 9:
            raise CheonanContractError("integrated reservation row width changed")
        match = re.search(r"(?:\?|&)eduNo=(\d+)", _clean(anchor.get("href")))
        title = _clean(anchor.get_text(" ", strip=True))
        if match is None:
            raise CheonanContractError("integrated reservation identity changed")
        identity = match.group(1)
        source_status = cells[8]
        if source_status not in _INTEGRATED_STATUS:
            raise CheonanContractError(f"integrated {identity}: unknown status {source_status}")
        missing_source_title = not title
        if missing_source_title and source_status != "접수마감":
            raise CheonanContractError(f"integrated {identity}: active title is missing")
        if missing_source_title:
            title = f"원본 제목 없음 ({identity})"
        start, end, period, date_anomaly = _date_range(cells[4], allow_single=True)
        if (not start or not end) and source_status != "접수마감":
            raise CheonanContractError(f"integrated {identity}: missing education date")
        apply_start, apply_end, apply_period, apply_anomaly = _date_range(cells[3])
        institution = cells[0] or "천안시 통합예약"
        region = cells[1]
        municipality_code, _ = _municipality_for_region(region)
        capacity_total, capacity_current, wait_current = _capacity(cells[7])
        row = _common_row(
            CHEONAN_INTEGRATED_PROVIDER,
            identity,
            title,
            institution,
            _integrated_detail_url(identity),
            "integrated",
            municipality_code,
        )
        row.update({
            "category": "통합예약 교육/강좌",
            "status": _INTEGRATED_STATUS[source_status],
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period or cells[3],
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "schedule_raw": cells[5],
            "selection_method": cells[6],
            "capacity_total": capacity_total,
            "capacity_current": capacity_current,
            "waitlist_current": wait_current,
            "reservation_available": source_status in {"접수중", "대기자 접수중"},
            "description": title,
            "collection_type": "html_table+detail_html",
            "raw_fields": {
                "parser": CHEONAN_PARSER,
                "source_identity": f"integrated:{identity}",
                "education_id": identity,
                "source_status": source_status,
                "source_institution": cells[0],
                "source_region": region,
                "delegated_owner_alias": institution in CHEONAN_INTEGRATED_DELEGATED_INSTITUTIONS,
                "missing_source_title": missing_source_title,
                "source_date_anomaly": date_anomaly,
                "source_apply_date_anomaly": apply_anomaly,
            },
        })
        result.append(_clean_row(row))
    return result


def _integrated_detail(runner: _Runner, row: dict[str, Any]) -> int:
    soup = runner.soup(row["raw_url"], parameterized=True)
    root = soup.select_one(".yeyakView")
    if root is None:
        raise CheonanContractError("integrated detail contract missing")
    title = root.select_one(".info-title")
    identity = row["raw_fields"]["education_id"]
    if title is None or _norm(title.get_text(" ", strip=True)) != _norm(row["title"]):
        raise CheonanContractError(f"integrated {identity}: detail/list title mismatch")
    pairs = _detail_pairs(root)
    start, end, _, _ = _date_range(pairs.get("교육기간"), allow_single=True)
    if start != row["start_date"] or end != row["end_date"]:
        raise CheonanContractError(f"integrated {identity}: detail/list date mismatch")
    if pairs.get("교육장소"):
        row["venue"] = pairs["교육장소"]
    if pairs.get("교육대상"):
        row["target"] = pairs["교육대상"]
    if pairs.get("수강료"):
        row["fee"] = pairs["수강료"]
    descriptions = [
        _sanitize(node.get_text(" ", strip=True))
        for node in root.select(".progView-bottom-box .view-content")
    ]
    descriptions = [value for value in descriptions if value]
    if descriptions:
        row["description"] = " ".join(descriptions)[:4_000]
    controls = root.select(".button_write")
    if controls and row.get("reservation_available"):
        row["application_url"] = CHEONAN_INTEGRATED_APPLICATION_ENDPOINT + "?" + urlencode({
            "eduNo": identity,
        })
    return len(controls)


def _collect_integrated(
    runner: _Runner, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first = runner.soup(
        CHEONAN_INTEGRATED_LIST_ENDPOINT,
        params={"pageIndex": 1, "pageUnit": 50},
    )
    total, last_page = _cheonan_summary(first)
    if last_page + 1 > max_pages:
        raise CheonanContractError("integrated pagination exceeds max_pages")
    rows: list[dict[str, Any]] = []
    page_counts: list[int] = []
    for page in range(1, last_page + 1):
        soup = first if page == 1 else runner.soup(
            CHEONAN_INTEGRATED_LIST_ENDPOINT,
            params={"pageIndex": page, "pageUnit": 50},
        )
        parsed = _integrated_page(soup)
        if not parsed:
            raise CheonanContractError(f"integrated page {page} unexpectedly empty")
        rows.extend(parsed)
        page_counts.append(len(parsed))
    sentinel = _integrated_page(runner.soup(
        CHEONAN_INTEGRATED_LIST_ENDPOINT,
        params={"pageIndex": last_page + 1, "pageUnit": 50},
    ))
    if sentinel:
        raise CheonanContractError("integrated empty-page sentinel changed")
    identities = [row["raw_fields"]["source_identity"] for row in rows]
    if len(rows) != total or len(set(identities)) != total:
        raise CheonanContractError(
            f"integrated source total mismatch: advertised {total}, parsed {len(rows)}"
        )
    source_current = [
        row for row in rows
        if row.get("end_date") and row["end_date"] >= cutoff.isoformat()
    ]
    owned = [
        row for row in source_current
        if not row["raw_fields"]["delegated_owner_alias"]
    ]
    if len(owned) > detail_limit:
        raise CheonanContractError("integrated current details exceed detail_limit")
    controls = sum(_integrated_detail(runner, row) for row in owned)
    meta = _base_meta("integrated")
    meta.update({
        "pages": last_page + 1,
        "list_requests": last_page + 1,
        "advertised_last_page": last_page,
        "sentinel_page": last_page + 1,
        "sentinel_count": 0,
        "page_counts": page_counts,
        "source_total": total,
        "source_rows": len(rows),
        "source_current_count": len(source_current),
        "delegated_alias_excluded_count": len(source_current) - len(owned),
        "current_count": len(owned),
        "detail_attempts": len(owned),
        "detail_pages": len(owned),
        "application_control_count": controls,
        "source_identity_sha256": _identity_hash(identities),
        "branch_counts": dict(Counter(row["branch"] for row in owned)),
        "municipality_counts": dict(Counter(row["municipality_code"] for row in owned)),
        "pagination_complete": True,
        "details_complete": True,
    })
    return owned, meta


def _experience_detail_url(identity: str) -> str:
    return CHEONAN_EXPERIENCE_DETAIL_ENDPOINT + "?" + urlencode({"exprnNo": identity})


def _experience_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    header = ["기관구분", "체험명", "체험기간", "선정방식", "접수상태"]
    table = _table_with_header(soup, header)
    result: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        title_anchors = tr.select("a.botton_view[href*='exprnNo=']")
        if not title_anchors:
            cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select("td")]
            if len(cells) == 1 and cells[0] == "데이터가 없습니다.":
                continue
            if not cells:
                continue
            raise CheonanContractError("experience list contains an unrecognized row")
        if len(title_anchors) != 1:
            raise CheonanContractError("experience title-link cardinality changed")
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select("td")]
        if len(cells) != 5:
            raise CheonanContractError("experience reservation row width changed")
        anchor = title_anchors[0]
        href = urljoin(CHEONAN_EXPERIENCE_LIST_ENDPOINT, _clean(anchor.get("href")))
        parsed_href = urlparse(href)
        identities = parse_qs(parsed_href.query, keep_blank_values=True).get("exprnNo", [])
        if (
            len(identities) != 1
            or not _ID_RE.fullmatch(identities[0])
            or parsed_href.path != urlparse(CHEONAN_EXPERIENCE_DETAIL_ENDPOINT).path
        ):
            raise CheonanContractError("experience reservation identity changed")
        identity = identities[0]
        expected_url = _experience_detail_url(identity)
        if not _exact_target(href, expected_url):
            raise CheonanContractError(f"experience {identity}: detail URL changed")
        title = _clean(anchor.get_text(" ", strip=True))
        institution = cells[0]
        if not title or not institution:
            raise CheonanContractError(f"experience {identity}: title/institution missing")
        start, end, period, anomaly = _date_range(cells[2], allow_single=True)
        if not start or not end or anomaly:
            raise CheonanContractError(f"experience {identity}: invalid experience date")
        selection = cells[3]
        if selection not in _EXPERIENCE_SELECTION_METHODS:
            raise CheonanContractError(
                f"experience {identity}: unknown selection method {selection}"
            )
        source_status = cells[4]
        if source_status not in _EXPERIENCE_STATUS:
            raise CheonanContractError(
                f"experience {identity}: unknown status {source_status}"
            )
        status_links = [
            urljoin(CHEONAN_EXPERIENCE_LIST_ENDPOINT, _clean(item.get("href")))
            for item in tr.select("td:nth-of-type(5) a[href]")
        ]
        if source_status == "접수하기" and status_links != [expected_url]:
            raise CheonanContractError(
                f"experience {identity}: active detail control changed"
            )
        if any(not _exact_target(value, expected_url) for value in status_links):
            raise CheonanContractError(
                f"experience {identity}: status control escaped its detail"
            )
        row = _common_row(
            CHEONAN_EXPERIENCE_PROVIDER,
            identity,
            title,
            institution,
            expected_url,
            "experience",
            CHEONAN_MUNICIPALITY_CODE,
        )
        row.update({
            "category": "통합예약 체험/견학",
            "status": _EXPERIENCE_STATUS[source_status],
            "period": period,
            "start_date": start,
            "end_date": end,
            "selection_method": selection,
            "reservation_available": source_status == "접수하기",
            "description": title,
            "collection_type": "html_table+detail_html",
            "raw_fields": {
                "parser": CHEONAN_PARSER,
                "source_identity": f"experience:{identity}",
                "experience_id": identity,
                "source_status": source_status,
                "source_institution": institution,
                "official_detail_required": True,
            },
        })
        result.append(_clean_row(row))
    return result


_LOCATION_VALUE_RE = re.compile(
    r"(?:장\s*소|위\s*치|주\s*소)\s*[:：]\s*([^\r\n]{1,300})"
)


def _experience_location_evidence(root: Tag) -> tuple[list[str], list[str]]:
    labelled: list[str] = []
    mapping_evidence: list[str] = []
    for node in root.select(".progView-bottom-box .view-content"):
        raw_text = node.get_text("\n", strip=True)
        for match in _LOCATION_VALUE_RE.finditer(raw_text):
            value = _sanitize(match.group(1), 300)
            if value and value not in labelled:
                labelled.append(value)
                mapping_evidence.append(value)
    for anchor in root.select("a[href]"):
        decoded = _sanitize(unquote(_clean(anchor.get("href"))), 800)
        if decoded and any(
            token in decoded
            for token in ("동남구", "서북구", "/address/", "address/")
        ):
            mapping_evidence.append(decoded)
    return labelled, list(dict.fromkeys(mapping_evidence))


def _region_token_present(value: str, token: str) -> bool:
    return bool(re.search(rf"(?<![가-힣]){re.escape(token)}(?![가-힣])", value))


def _municipality_from_experience_evidence(evidence: Iterable[str]) -> tuple[str, str]:
    values = [_clean(value) for value in evidence if _clean(value)]
    joined = " \n".join(values)
    explicit_dongnam = "동남구" in joined
    explicit_seobuk = "서북구" in joined
    if explicit_dongnam and explicit_seobuk:
        raise CheonanContractError("experience detail has conflicting district evidence")
    if explicit_dongnam:
        return CHEONAN_DONGNAM_CODE, CHEONAN_DONGNAM_NAME
    if explicit_seobuk:
        return CHEONAN_SEOBUK_CODE, CHEONAN_SEOBUK_NAME
    dongnam = any(
        _region_token_present(value, token)
        for value in values
        for token in CHEONAN_DONGNAM_REGIONS
    )
    seobuk = any(
        _region_token_present(value, token)
        for value in values
        for token in CHEONAN_SEOBUK_REGIONS
    )
    if dongnam and seobuk:
        raise CheonanContractError("experience detail has conflicting region evidence")
    if dongnam:
        return CHEONAN_DONGNAM_CODE, CHEONAN_DONGNAM_NAME
    if seobuk:
        return CHEONAN_SEOBUK_CODE, CHEONAN_SEOBUK_NAME
    return CHEONAN_MUNICIPALITY_CODE, CHEONAN_MUNICIPALITY_NAME


def _experience_detail(runner: _Runner, row: dict[str, Any]) -> int:
    soup = runner.soup(row["raw_url"], parameterized=True)
    root = soup.select_one(".yeyakView")
    identity = row["raw_fields"]["experience_id"]
    if root is None:
        raise CheonanContractError(f"experience {identity}: detail contract missing")
    identity_nodes = soup.select("input#exprnNo[name='exprnNo']")
    if len(identity_nodes) != 1 or _clean(identity_nodes[0].get("value")) != identity:
        raise CheonanContractError(f"experience {identity}: detail identity mismatch")
    title = root.select_one(".info-title")
    if title is None or _norm(title.get_text(" ", strip=True)) != _norm(row["title"]):
        raise CheonanContractError(f"experience {identity}: detail/list title mismatch")
    pairs = _detail_pairs(root)
    if _norm(pairs.get("기관 구분")) != _norm(row["branch"]):
        raise CheonanContractError(f"experience {identity}: detail/list institution mismatch")
    start, end, _, anomaly = _date_range(pairs.get("체험기간"), allow_single=True)
    if anomaly or start != row["start_date"] or end != row["end_date"]:
        raise CheonanContractError(f"experience {identity}: detail/list date mismatch")
    if pairs.get("체험대상"):
        row["target"] = _sanitize(pairs["체험대상"], 500)
    if pairs.get("체험료"):
        row["fee"] = _sanitize(pairs["체험료"], 500)
    for key, raw_key in (
        ("individual_application_range", "개인 최소 / 최대 신청인원"),
        ("group_application_range", "단체 최소 / 최대 신청인원"),
    ):
        value = _sanitize(pairs.get(raw_key), 100)
        if value:
            row["raw_fields"][key] = value
    descriptions = [
        _sanitize(node.get_text(" ", strip=True))
        for node in root.select(".progView-bottom-box .view-content")
    ]
    descriptions = [value for value in descriptions if value]
    if descriptions:
        row["description"] = " ".join(descriptions)[:4_000]
    venues, evidence = _experience_location_evidence(root)
    if venues:
        row["venue"] = venues[0]
    municipality_code, municipality_name = _municipality_from_experience_evidence(
        evidence
    )
    row.update({
        "region": municipality_name,
        "municipality_code": municipality_code,
        "municipality_full_name": municipality_name,
    })
    row["raw_fields"]["official_location_evidence"] = evidence
    row["raw_fields"]["detail_identity_verified"] = True
    return 1


def _experience_signature(rows: Iterable[dict[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("branch")),
            _clean(row.get("period")),
            _clean(row.get("status")),
        )
        for row in rows
    )


def _collect_experience(
    runner: _Runner, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {"pageIndex": 1, "pageUnit": 50}
    first = runner.soup(CHEONAN_EXPERIENCE_LIST_ENDPOINT, params=params)
    total, last_page = _cheonan_summary(first)
    if last_page < 1 or last_page + 1 > max_pages:
        raise CheonanContractError("experience pagination exceeds max_pages")
    rows: list[dict[str, Any]] = []
    page_rows: dict[int, list[dict[str, Any]]] = {}
    page_counts: list[int] = []
    for page in range(1, last_page + 1):
        soup = first if page == 1 else runner.soup(
            CHEONAN_EXPERIENCE_LIST_ENDPOINT,
            params={"pageIndex": page, "pageUnit": 50},
        )
        advertised_total, advertised_last = _cheonan_summary(soup)
        if (advertised_total, advertised_last) != (total, last_page):
            raise CheonanContractError("experience pagination summary changed mid-crawl")
        parsed = _experience_page(soup)
        if not parsed and total:
            raise CheonanContractError(f"experience page {page} unexpectedly empty")
        page_rows[page] = parsed
        page_counts.append(len(parsed))
        rows.extend(parsed)
    sentinel_soup = runner.soup(
        CHEONAN_EXPERIENCE_LIST_ENDPOINT,
        params={"pageIndex": last_page + 1, "pageUnit": 50},
    )
    if _cheonan_summary(sentinel_soup) != (total, last_page):
        raise CheonanContractError("experience sentinel summary changed")
    sentinel = _experience_page(sentinel_soup)
    if sentinel:
        raise CheonanContractError("experience empty-page sentinel changed")
    identities = [row["raw_fields"]["source_identity"] for row in rows]
    if len(rows) != total or len(set(identities)) != total:
        raise CheonanContractError(
            f"experience source total mismatch: advertised {total}, parsed {len(rows)}"
        )
    current = [
        row for row in rows
        if row.get("end_date") and row["end_date"] >= cutoff.isoformat()
    ]
    if len(current) > detail_limit:
        raise CheonanContractError("experience current details exceed detail_limit")
    detail_pages = sum(_experience_detail(runner, row) for row in current)
    boundary_pages = sorted({1, last_page})
    for page in boundary_pages:
        stable_soup = runner.soup(
            CHEONAN_EXPERIENCE_LIST_ENDPOINT,
            params={"pageIndex": page, "pageUnit": 50},
        )
        if _cheonan_summary(stable_soup) != (total, last_page):
            raise CheonanContractError("experience stable boundary summary changed")
        if _experience_signature(_experience_page(stable_soup)) != _experience_signature(
            page_rows[page]
        ):
            raise CheonanContractError(
                f"experience page {page} changed during stable boundary recheck"
            )
    meta = _base_meta("experience")
    meta.update({
        "pages": last_page + 1 + len(boundary_pages),
        "list_requests": last_page + 1 + len(boundary_pages),
        "advertised_last_page": last_page,
        "sentinel_page": last_page + 1,
        "sentinel_count": 0,
        "stable_boundary_pages": boundary_pages,
        "stable_boundary_count": len(boundary_pages),
        "page_counts": page_counts,
        "source_total": total,
        "source_rows": len(rows),
        "source_current_count": len(current),
        "current_count": len(current),
        "detail_attempts": len(current),
        "detail_pages": detail_pages,
        "source_identity_sha256": _identity_hash(identities),
        "current_identity_sha256": _identity_hash(
            row["raw_fields"]["source_identity"] for row in current
        ),
        "source_status_counts": dict(Counter(row["raw_fields"]["source_status"] for row in rows)),
        "branch_counts": dict(Counter(row["branch"] for row in current)),
        "municipality_counts": dict(Counter(row["municipality_code"] for row in current)),
        "domain_category_counts": {"체험·견학": len(current)},
        "service_group_counts": {"체험": len(current)},
        "pagination_complete": True,
        "details_complete": True,
    })
    return current, meta


def _library_detail_url(identity: str) -> str:
    return CHEONAN_LIBRARY_DETAIL_ENDPOINT + "?" + urlencode({"mngNo": identity})


def _library_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    header = [
        "No.", "분야", "강좌명/ 강사명", "대상", "참여가능년생", "접수기간",
        "교육기간/ 시간", "신청/ 모집인원", "모집방법/ 모집상태",
    ]
    table = _table_with_header(soup, header)
    result: list[dict[str, Any]] = []
    for anchor in table.select("tbody a[href*='mngNo=']"):
        tr = anchor.find_parent("tr")
        if tr is None:
            continue
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select("td")]
        if len(cells) != 9:
            raise CheonanContractError("library row width changed")
        match = re.search(r"(?:\?|&)mngNo=(\d+)", _clean(anchor.get("href")))
        if match is None:
            raise CheonanContractError("library programme identity changed")
        identity = match.group(1)
        title = _clean(anchor.get_text(" ", strip=True))
        branch_match = re.match(r"\[([^\]]+)\]", cells[2])
        if not title or branch_match is None:
            raise CheonanContractError(f"library {identity}: title/branch changed")
        short = _clean(branch_match.group(1))
        branch_info = CHEONAN_LIBRARY_BRANCH_BY_SHORT.get(short)
        if branch_info is None:
            raise CheonanContractError(f"library {identity}: unknown official branch {short}")
        branch, municipality_code = branch_info
        source_status = cells[8]
        if source_status not in _LIBRARY_STATUS:
            raise CheonanContractError(f"library {identity}: unknown status {source_status}")
        start, end, period, date_anomaly = _date_range(cells[6])
        if date_anomaly:
            raise CheonanContractError(f"library {identity}: education date changed")
        apply_start, apply_end, apply_period, apply_anomaly = _date_range(cells[5])
        if not apply_start:
            tokens = _date_tokens(cells[5])
            apply_start = tokens[0].isoformat() if tokens else ""
        capacity_total, capacity_current, wait_current = _capacity(cells[7])
        row = _common_row(
            CHEONAN_LIBRARY_PROVIDER,
            identity,
            title,
            branch,
            _library_detail_url(identity),
            "library",
            municipality_code,
        )
        row.update({
            "category": cells[1],
            "status": _LIBRARY_STATUS[source_status],
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period or cells[5],
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "schedule_raw": cells[6],
            "target": cells[3],
            "participation_birth_year_raw": cells[4],
            "capacity_total": capacity_total,
            "capacity_current": capacity_current,
            "waitlist_current": wait_current,
            "reservation_available": source_status in {"모집중", "대기자모집중"},
            "description": title,
            "collection_type": "html_table+detail_html",
            "raw_fields": {
                "parser": CHEONAN_PARSER,
                "source_identity": f"library:{identity}",
                "management_no": identity,
                "source_status": source_status,
                "source_branch_short": short,
                "source_date_anomaly": date_anomaly,
                "source_apply_date_anomaly": apply_anomaly,
            },
        })
        result.append(_clean_row(row))
    return result


def _library_detail(runner: _Runner, row: dict[str, Any]) -> int:
    soup = runner.soup(row["raw_url"], parameterized=True)
    root = soup.select_one(".programLecture.plView")
    if root is None:
        raise CheonanContractError("library detail contract missing")
    title = root.select_one(".info-title")
    identity = row["raw_fields"]["management_no"]
    if title is None or _norm(title.get_text(" ", strip=True)) != _norm(row["title"]):
        raise CheonanContractError(f"library {identity}: detail/list title mismatch")
    pairs = _detail_pairs(root)
    start, end, _, _ = _date_range(pairs.get("교육기간"))
    if start != row["start_date"] or end != row["end_date"]:
        raise CheonanContractError(f"library {identity}: detail/list date mismatch")
    if pairs.get("교육장소"):
        row["venue"] = pairs["교육장소"]
    if pairs.get("수업료"):
        row["fee"] = pairs["수업료"]
    descriptions = [
        _sanitize(node.get_text(" ", strip=True))
        for node in root.select(".view-content")
    ]
    descriptions = [value for value in descriptions if value]
    if descriptions:
        row["description"] = " ".join(descriptions)[:4_000]
    controls = root.select(".button_aply")
    if controls and row.get("reservation_available"):
        row["application_url"] = CHEONAN_LIBRARY_APPLICATION_ENDPOINT + "?" + urlencode({
            "mngNo": identity,
        })
    return len(controls)


def _collect_library(
    runner: _Runner, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first = runner.soup(
        CHEONAN_LIBRARY_LIST_ENDPOINT,
        params={"pageIndex": 1, "pageUnit": 20},
    )
    total, last_page = _cheonan_summary(first)
    if last_page + 1 > max_pages:
        raise CheonanContractError("library pagination exceeds max_pages")
    page_rows: list[list[dict[str, Any]]] = []
    for page in range(1, last_page + 1):
        soup = first if page == 1 else runner.soup(
            CHEONAN_LIBRARY_LIST_ENDPOINT,
            params={"pageIndex": page, "pageUnit": 20},
        )
        page_rows.append(_library_page(soup))
    unique: dict[str, dict[str, Any]] = {}
    for parsed in page_rows:
        for row in parsed:
            unique.setdefault(row["raw_fields"]["source_identity"], row)
    sentinel_rows = _library_page(runner.soup(
        CHEONAN_LIBRARY_LIST_ENDPOINT,
        params={"pageIndex": last_page + 1, "pageUnit": 20},
    ))
    sentinel_new = [
        row for row in sentinel_rows
        if row["raw_fields"]["source_identity"] not in unique
    ]
    if sentinel_new:
        raise CheonanContractError("library sentinel disclosed new rows")
    rows = list(unique.values())
    if len(rows) != total:
        raise CheonanContractError(
            f"library source total mismatch: advertised {total}, unique {len(rows)}"
        )
    source_current = [row for row in rows if row["end_date"] >= cutoff.isoformat()]
    if len(source_current) > detail_limit:
        raise CheonanContractError("library current details exceed detail_limit")
    controls = sum(_library_detail(runner, row) for row in source_current)
    identities = [row["raw_fields"]["source_identity"] for row in rows]
    meta = _base_meta("library")
    meta.update({
        "pages": last_page + 1,
        "list_requests": last_page + 1,
        "advertised_last_page": last_page,
        "sentinel_page": last_page + 1,
        "sentinel_count": len(sentinel_new),
        "sentinel_raw_rows": len(sentinel_rows),
        "sentinel_mode": "no_new_identity_due_source_ignoring_page_index",
        "page_counts": [len(value) for value in page_rows],
        "source_total": total,
        "source_rows": len(rows),
        "source_current_count": len(source_current),
        "current_count": len(source_current),
        "detail_attempts": len(source_current),
        "detail_pages": len(source_current),
        "application_control_count": controls,
        "source_identity_sha256": _identity_hash(identities),
        "branch_count": len(CHEONAN_LIBRARY_BRANCHES) - 1,
        "branch_counts": dict(Counter(row["branch"] for row in source_current)),
        "municipality_counts": dict(
            Counter(row["municipality_code"] for row in source_current)
        ),
        "pagination_complete": True,
        "details_complete": True,
    })
    return source_current, meta


def _lifelong_config(owner: str) -> tuple[str, str, str, str, str, Mapping[str, str]]:
    if owner == "seongjeong":
        return (
            CHEONAN_SEONGJEONG_PROVIDER,
            CHEONAN_SEONGJEONG_URL,
            CHEONAN_SEONGJEONG_DETAIL_ENDPOINT,
            CHEONAN_SEONGJEONG_APPLICATION_ENDPOINT,
            CHEONAN_SEONGJEONG_BRANCH,
            _SEONGJEONG_STATUS,
        )
    return (
        CHEONAN_DUJEONG_PROVIDER,
        CHEONAN_DUJEONG_URL,
        CHEONAN_DUJEONG_DETAIL_ENDPOINT,
        CHEONAN_DUJEONG_APPLICATION_ENDPOINT,
        CHEONAN_DUJEONG_BRANCH,
        _DUJEONG_STATUS,
    )


def _lifelong_page(soup: BeautifulSoup, owner: str) -> list[dict[str, Any]]:
    provider, _, detail_endpoint, _, branch, status_map = _lifelong_config(owner)
    header = (
        ["번호", "강좌명", "강사명", "접수기간", "모집/신청 인원 (명)", "상태"]
        if owner == "seongjeong"
        else [
            "번호", "강좌명/강사명", "접수기간", "교육기간/ 교육시간",
            "모집인원/ 신청인원/ 대기인원(명)", "상태",
        ]
    )
    table = _table_with_header(soup, header)
    result: list[dict[str, Any]] = []
    for button in table.select("tbody .button_view[data-lctr-no]"):
        tr = button.find_parent("tr")
        if tr is None:
            continue
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select("td")]
        if len(cells) != 6:
            raise CheonanContractError(f"{owner} row width changed")
        identity = _clean(button.get("data-lctr-no"))
        title = _clean(button.get_text(" ", strip=True))
        if owner == "dujeong":
            if " / " not in title:
                raise CheonanContractError(f"dujeong {identity}: title/instructor delimiter changed")
            title = title.split(" / ", 1)[0]
        if not _ID_RE.fullmatch(identity) or not title:
            raise CheonanContractError(f"{owner}: malformed identity/title")
        source_status = cells[5]
        if source_status not in status_map:
            raise CheonanContractError(f"{owner} {identity}: unknown status {source_status}")
        apply_cell = cells[3] if owner == "seongjeong" else cells[2]
        apply_start, apply_end, apply_period, apply_anomaly = _date_range(apply_cell)
        capacity_cell = cells[4]
        values = _numbers(capacity_cell)
        capacity_total = values[0] if values else None
        capacity_current = values[1] if len(values) >= 2 else None
        wait_current = values[2] if len(values) >= 3 else None
        raw_url = detail_endpoint + "?" + urlencode({"lctrNo": identity})
        row = _common_row(
            provider, identity, title, branch, raw_url, owner, CHEONAN_SEOBUK_CODE
        )
        row.update({
            "category": "평생학습관 교육프로그램",
            "preserve_branch": True,
            "status": status_map[source_status],
            "apply_period": apply_period or apply_cell,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "capacity_total": capacity_total,
            "capacity_current": capacity_current,
            "waitlist_current": wait_current,
            "reservation_available": source_status in {
                "우선접수 모집중", "신규접수 모집중", "일반접수 모집중",
                "추가접수 모집중", "모집중", "대기자모집중",
                "추가 모집중", "추가대기자 모집중",
            },
            "description": title,
            "collection_type": "html_table+detail_html",
            "raw_fields": {
                "parser": CHEONAN_PARSER,
                "source_identity": f"{owner}:{identity}",
                "lecture_no": identity,
                "source_status": source_status,
                "source_apply_date_anomaly": apply_anomaly,
                "instructor_omitted_for_pii": True,
            },
        })
        result.append(_clean_row(row))
    return result


def _lifelong_detail(runner: _Runner, row: dict[str, Any], owner: str) -> int:
    _, _, _, application_endpoint, _, _ = _lifelong_config(owner)
    soup = runner.soup(row["raw_url"], parameterized=True)
    root = soup.select_one(".lifelongLearningView")
    if root is None:
        raise CheonanContractError(f"{owner} detail contract missing")
    title = root.select_one(".info-title")
    identity = row["raw_fields"]["lecture_no"]
    if title is None or _norm(title.get_text(" ", strip=True)) != _norm(row["title"]):
        raise CheonanContractError(f"{owner} {identity}: detail/list title mismatch")
    pairs = _detail_pairs(root)
    start, end, period, anomaly = _date_range(pairs.get("교육기간"))
    if anomaly:
        raise CheonanContractError(f"{owner} {identity}: education date changed")
    row.update({"start_date": start, "end_date": end, "period": period})
    if pairs.get("교육시간"):
        row["schedule_raw"] = pairs["교육시간"]
    if pairs.get("교육장소"):
        row["venue"] = pairs["교육장소"]
    if pairs.get("교육대상"):
        row["target"] = pairs["교육대상"]
    if pairs.get("수업료"):
        row["fee"] = pairs["수업료"]
    values = _numbers(pairs.get("정원/신청/대기"))
    if len(values) >= 2:
        row["capacity_total"], row["capacity_current"] = values[0], values[1]
    if len(values) >= 3:
        row["waitlist_current"] = values[2]
    descriptions = [
        _sanitize(node.get_text(" ", strip=True))
        for node in root.select(".view-content")
    ]
    descriptions = [value for value in descriptions if value]
    if descriptions:
        row["description"] = " ".join(descriptions)[:4_000]
    discovered_controls = root.select(
        ".btn_apply, button.button_aply[data-lctr-no][data-apply-type]"
    )
    if len(discovered_controls) > 1:
        raise CheonanContractError(f"{owner} {identity}: duplicated application controls")
    if discovered_controls:
        control_identity = _clean(discovered_controls[0].get("data-lctr-no"))
        if control_identity and control_identity != identity:
            raise CheonanContractError(
                f"{owner} {identity}: application control identity mismatch"
            )
    controls = [
        control
        for control in discovered_controls
        if not control.has_attr("disabled")
        and _clean(control.get("aria-disabled")).lower() != "true"
    ]
    if bool(controls) != bool(row.get("reservation_available")):
        raise CheonanContractError(
            f"{owner} {identity}: status/application control mismatch"
        )
    if controls:
        row["application_url"] = application_endpoint + "?" + urlencode({
            "lctrNo": identity,
        })
    return len(controls)


def _collect_lifelong(
    runner: _Runner,
    cutoff: date,
    max_pages: int,
    detail_limit: int,
    owner: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _, list_endpoint, _, _, branch, _ = _lifelong_config(owner)
    first = runner.soup(list_endpoint, params={"pageIndex": 1, "pageUnit": 50})
    total, last_page = _cheonan_summary(first)
    if last_page + 1 > max_pages:
        raise CheonanContractError(f"{owner} pagination exceeds max_pages")
    rows: list[dict[str, Any]] = []
    page_counts: list[int] = []
    for page in range(1, last_page + 1):
        soup = first if page == 1 else runner.soup(
            list_endpoint, params={"pageIndex": page, "pageUnit": 50}
        )
        parsed = _lifelong_page(soup, owner)
        if not parsed:
            raise CheonanContractError(f"{owner} page {page} unexpectedly empty")
        rows.extend(parsed)
        page_counts.append(len(parsed))
    sentinel = _lifelong_page(runner.soup(
        list_endpoint, params={"pageIndex": last_page + 1, "pageUnit": 50}
    ), owner)
    if sentinel:
        raise CheonanContractError(f"{owner} empty-page sentinel changed")
    identities = [row["raw_fields"]["source_identity"] for row in rows]
    if len(rows) != total or len(set(identities)) != total:
        raise CheonanContractError(
            f"{owner} source total mismatch: advertised {total}, parsed {len(rows)}"
        )
    if len(rows) > detail_limit:
        raise CheonanContractError(f"{owner} details exceed detail_limit")
    controls = sum(_lifelong_detail(runner, row, owner) for row in rows)
    current = [row for row in rows if row["end_date"] >= cutoff.isoformat()]
    meta = _base_meta(owner)
    meta.update({
        "pages": last_page + 1,
        "list_requests": last_page + 1,
        "advertised_last_page": last_page,
        "sentinel_page": last_page + 1,
        "sentinel_count": 0,
        "page_counts": page_counts,
        "source_total": total,
        "source_rows": len(rows),
        "source_current_count": len(current),
        "current_count": len(current),
        "detail_attempts": len(rows),
        "detail_pages": len(rows),
        "application_control_count": controls,
        "source_identity_sha256": _identity_hash(identities),
        "branch_count": 1,
        "branch_counts": {branch: len(current)},
        "municipality_counts": {CHEONAN_SEOBUK_CODE: len(current)},
        "pagination_complete": True,
        "details_complete": True,
    })
    return current, meta


def _disability_detail_url(identity: str) -> str:
    return CHEONAN_DISABILITY_LIST_ENDPOINT + "?" + urlencode({
        "bo_table": "edu_app", "wr_id": identity,
    })


def _disability_total(soup: BeautifulSoup) -> int:
    text = _clean(soup.get_text(" ", strip=True))
    match = re.search(r"총\s*([\d,]+)\s*개의\s*교육이\s*등록", text)
    if match is None:
        raise CheonanContractError("disability advertised total changed")
    return int(match.group(1).replace(",", ""))


def _disability_last_page(soup: BeautifulSoup) -> int:
    pages = []
    for anchor in soup.select(".pg_wrap a[href*='page=']"):
        match = re.search(r"(?:\?|&)page=(\d+)", _clean(anchor.get("href")))
        if match:
            pages.append(int(match.group(1)))
    return max(pages, default=1)


def _disability_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    table = soup.select_one("table.pc_view")
    if table is None:
        raise CheonanContractError("disability desktop table missing")
    header = ["번호", "상태", "교육명", "교육대상", "교육일시", "정원", "접수기간"]
    found = [_clean(cell.get_text(" ", strip=True)) for cell in table.select("thead th")]
    if found != header:
        raise CheonanContractError("disability table header changed")
    result: list[dict[str, Any]] = []
    for anchor in table.select("tbody a.bo_subject[href*='wr_id=']"):
        tr = anchor.find_parent("tr")
        if tr is None:
            continue
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select("td")]
        if len(cells) != 7:
            raise CheonanContractError("disability row width changed")
        match = re.search(r"(?:\?|&)wr_id=(\d+)", _clean(anchor.get("href")))
        if match is None:
            raise CheonanContractError("disability programme identity changed")
        identity = match.group(1)
        title = _clean(anchor.get_text(" ", strip=True))
        source_status = cells[1]
        if not title or source_status not in _DISABILITY_STATUS:
            raise CheonanContractError(f"disability {identity}: title/status changed")
        start, end, period, date_anomaly = _date_range(cells[4])
        if date_anomaly and source_status != "종료":
            raise CheonanContractError(f"disability {identity}: education date changed")
        apply_start, apply_end, apply_period, apply_anomaly = _date_range(cells[6])
        capacity_total, capacity_current, wait_current = _capacity(cells[5])
        row = _common_row(
            CHEONAN_DISABILITY_PROVIDER,
            identity,
            title,
            CHEONAN_DISABILITY_BRANCH,
            _disability_detail_url(identity),
            "disability",
            CHEONAN_SEOBUK_CODE,
        )
        row.update({
            "category": "장애인 평생교육",
            "status": _DISABILITY_STATUS[source_status],
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period or cells[6],
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "schedule_raw": cells[4],
            "target": cells[3],
            "capacity_total": capacity_total,
            "capacity_current": capacity_current,
            "waitlist_current": wait_current,
            "reservation_available": source_status == "진행",
            "description": title,
            "collection_type": "html_table+detail_html",
            "raw_fields": {
                "parser": CHEONAN_PARSER,
                "source_identity": f"disability:{identity}",
                "write_id": identity,
                "source_status": source_status,
                "source_date_anomaly": date_anomaly,
                "source_apply_date_anomaly": apply_anomaly,
            },
        })
        result.append(_clean_row(row))
    return result


def _disability_detail(runner: _Runner, row: dict[str, Any]) -> int:
    soup = runner.soup(row["raw_url"], parameterized=True)
    root = soup.select_one("#bo_v")
    if root is None:
        raise CheonanContractError("disability detail contract missing")
    title = root.select_one(".bo_v_tit")
    identity = row["raw_fields"]["write_id"]
    if title is None or _norm(title.get_text(" ", strip=True)) != _norm(row["title"]):
        raise CheonanContractError(f"disability {identity}: detail/list title mismatch")
    description = root.select_one("#bo_v_con")
    if description is not None:
        safe = _sanitize(description.get_text(" ", strip=True))
        if safe:
            row["description"] = safe
    controls = root.select("form#fwrite[action$='edu_write_update.php']")
    if controls and row.get("reservation_available"):
        row["application_url"] = row["raw_url"]
    return len(controls)


def _collapse_disability_aliases(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    chosen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    status_rank = {"OPEN": 3, "SCHEDULED": 2, "CLOSED": 1}
    for row in rows:
        key = (
            _norm(row["title"]), row["start_date"], row["end_date"],
            _norm(row.get("schedule_raw")),
        )
        previous = chosen.get(key)
        if previous is None:
            chosen[key] = row
            continue
        left = (status_rank.get(row.get("status"), 0), int(row["raw_fields"]["write_id"]))
        right = (
            status_rank.get(previous.get("status"), 0),
            int(previous["raw_fields"]["write_id"]),
        )
        if left > right:
            chosen[key] = row
    return list(chosen.values()), len(rows) - len(chosen)


def _collect_disability(
    runner: _Runner, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first = runner.soup(
        CHEONAN_DISABILITY_LIST_ENDPOINT,
        params={"bo_table": "edu_app", "page": 1},
    )
    total = _disability_total(first)
    last_page = _disability_last_page(first)
    if last_page + 1 > max_pages:
        raise CheonanContractError("disability pagination exceeds max_pages")
    rows: list[dict[str, Any]] = []
    page_counts: list[int] = []
    for page in range(1, last_page + 1):
        soup = first if page == 1 else runner.soup(
            CHEONAN_DISABILITY_LIST_ENDPOINT,
            params={"bo_table": "edu_app", "page": page},
        )
        parsed = _disability_page(soup)
        if not parsed:
            raise CheonanContractError(f"disability page {page} unexpectedly empty")
        rows.extend(parsed)
        page_counts.append(len(parsed))
    sentinel = _disability_page(runner.soup(
        CHEONAN_DISABILITY_LIST_ENDPOINT,
        params={"bo_table": "edu_app", "page": last_page + 1},
    ))
    if sentinel:
        raise CheonanContractError("disability empty-page sentinel changed")
    identities = [row["raw_fields"]["source_identity"] for row in rows]
    if len(rows) != total or len(set(identities)) != total:
        raise CheonanContractError(
            f"disability source total mismatch: advertised {total}, parsed {len(rows)}"
        )
    source_current = [
        row for row in rows
        if row.get("end_date") and row["end_date"] >= cutoff.isoformat()
    ]
    current, aliases = _collapse_disability_aliases(source_current)
    if len(current) > detail_limit:
        raise CheonanContractError("disability current details exceed detail_limit")
    controls = sum(_disability_detail(runner, row) for row in current)
    meta = _base_meta("disability")
    meta.update({
        "pages": last_page + 1,
        "list_requests": last_page + 1,
        "advertised_last_page": last_page,
        "sentinel_page": last_page + 1,
        "sentinel_count": 0,
        "page_counts": page_counts,
        "source_total": total,
        "source_rows": len(rows),
        "source_current_count": len(source_current),
        "duplicate_offering_aliases_excluded": aliases,
        "current_count": len(current),
        "detail_attempts": len(current),
        "detail_pages": len(current),
        "application_control_count": controls,
        "source_identity_sha256": _identity_hash(identities),
        "branch_count": 1,
        "branch_counts": {CHEONAN_DISABILITY_BRANCH: len(current)},
        "municipality_counts": {CHEONAN_SEOBUK_CODE: len(current)},
        "pagination_complete": True,
        "details_complete": True,
    })
    return current, meta


def _media_total(soup: BeautifulSoup) -> tuple[int, int]:
    text = _clean(soup.get_text(" ", strip=True))
    match = re.search(r"Total\s*([\d,]+)\s*건\s*[·ㆍ]?\s*\d+\s*/\s*(\d+)페이지", text)
    if match is None:
        raise CheonanContractError("media advertised total/page summary changed")
    return int(match.group(1).replace(",", "")), int(match.group(2))


def _media_label_map(item: Tag) -> dict[str, str]:
    result: dict[str, str] = {}
    for paragraph in item.select(".info p.sub"):
        label = paragraph.select_one("span")
        if label is None:
            continue
        key = _clean(label.get_text(" ", strip=True))
        clone = BeautifulSoup(str(paragraph), "lxml").select_one("p")
        if clone is None:
            continue
        first = clone.select_one("span")
        if first is not None:
            first.decompose()
        result[key] = _clean(clone.get_text(" ", strip=True))
    return result


def _media_page(soup: BeautifulSoup, cutoff: date) -> list[dict[str, Any]]:
    container = soup.select_one(".leehong_web_list")
    if container is None:
        raise CheonanContractError("media course list contract missing")
    result: list[dict[str, Any]] = []
    for item in container.select(":scope > .item"):
        anchor = item.select_one("a[href*='view.php?idx=']")
        if anchor is None:
            continue
        match = re.search(r"(?:\?|&)idx=(\d+)", _clean(anchor.get("href")))
        title_node = item.select_one(".info .title")
        category_node = item.select_one(".info .ind")
        if match is None or title_node is None or category_node is None:
            raise CheonanContractError("media identity/title/category changed")
        identity = match.group(1)
        title = _clean(title_node.get_text(" ", strip=True))
        labels = _media_label_map(item)
        apply_start, apply_end, apply_period, apply_anomaly = _date_range(
            labels.get("접수기간")
        )
        start, end, period, date_anomaly = _date_range(labels.get("교육기간"))
        if not title:
            raise CheonanContractError(f"media {identity}: title/education date changed")
        registration = item.select_one("a[href*='reg.php?idx=']")
        registration_url = (
            urljoin(CHEONAN_MEDIA_URL, _clean(registration.get("href")))
            if registration is not None else ""
        )
        if registration_url and not registration_url.startswith(
            CHEONAN_MEDIA_APPLICATION_ENDPOINT + "?"
        ):
            raise CheonanContractError(f"media {identity}: application URL escaped owner")
        if registration_url:
            status = "OPEN"
        elif apply_start and cutoff.isoformat() < apply_start:
            status = "SCHEDULED"
        elif apply_end and cutoff.isoformat() <= apply_end:
            status = "OPEN"
        else:
            status = "CLOSED"
        if date_anomaly and (
            registration_url
            or (apply_end and cutoff.isoformat() <= apply_end)
        ):
            raise CheonanContractError(
                f"media {identity}: active row has no valid education date"
            )
        capacity = _numbers(labels.get("모집인원"))
        row = _common_row(
            CHEONAN_MEDIA_PROVIDER,
            identity,
            title,
            CHEONAN_MEDIA_BRANCH,
            CHEONAN_MEDIA_DETAIL_ENDPOINT + "?" + urlencode({"idx": identity}),
            "media",
            CHEONAN_DONGNAM_CODE,
        )
        row.update({
            "category": _clean(category_node.get_text(" ", strip=True)),
            "status": status,
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "schedule_raw": labels.get("교육시간"),
            "venue": labels.get("교육장소"),
            "capacity_total": capacity[0] if capacity else None,
            "reservation_available": bool(registration_url),
            "application_url": registration_url,
            "description": title,
            "collection_type": "html_cards+detail_html",
            "raw_fields": {
                "parser": CHEONAN_PARSER,
                "source_identity": f"media:{identity}",
                "education_id": identity,
                "source_date_anomaly": date_anomaly,
                "source_apply_date_anomaly": apply_anomaly,
                "source_application_control_present": bool(registration_url),
            },
        })
        result.append(_clean_row(row))
    return result


def _media_detail(runner: _Runner, row: dict[str, Any], cutoff: date) -> None:
    soup = runner.soup(row["raw_url"], parameterized=True)
    identity = row["raw_fields"]["education_id"]
    item = soup.select_one(".leehong_web_list > .item")
    if item is None:
        raise CheonanContractError(f"media {identity}: detail card contract changed")
    title_node = item.select_one(".info .title")
    labels = _media_label_map(item)
    start, end, _, anomaly = _date_range(labels.get("교육기간"))
    if (
        title_node is None
        or anomaly
        or _norm(title_node.get_text(" ", strip=True)) != _norm(row["title"])
        or start != row["start_date"]
        or end != row["end_date"]
    ):
        raise CheonanContractError(f"media {identity}: detail/list mismatch")
    content = soup.select_one("#leehong_board .board_scon")
    if content is not None:
        safe = _sanitize(content.get_text(" ", strip=True))
        if safe:
            row["description"] = safe
    control = item.select_one("a[href*='reg.php?idx=']")
    application_url = (
        urljoin(CHEONAN_MEDIA_URL, _clean(control.get("href")))
        if control is not None else ""
    )
    if application_url and not application_url.startswith(
        CHEONAN_MEDIA_APPLICATION_ENDPOINT + "?"
    ):
        raise CheonanContractError(f"media {identity}: detail application URL escaped owner")
    if application_url:
        row["application_url"] = application_url
        row["reservation_available"] = True


def _collect_media(
    runner: _Runner, cutoff: date, max_pages: int, detail_limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first = runner.soup(CHEONAN_MEDIA_URL, params={"page": 1})
    total, last_page = _media_total(first)
    if last_page + 1 > max_pages:
        raise CheonanContractError("media pagination exceeds max_pages")
    rows: list[dict[str, Any]] = []
    page_counts: list[int] = []
    for page in range(1, last_page + 1):
        soup = first if page == 1 else runner.soup(CHEONAN_MEDIA_URL, params={"page": page})
        parsed = _media_page(soup, cutoff)
        if not parsed:
            raise CheonanContractError(f"media page {page} unexpectedly empty")
        rows.extend(parsed)
        page_counts.append(len(parsed))
    sentinel = _media_page(
        runner.soup(CHEONAN_MEDIA_URL, params={"page": last_page + 1}), cutoff
    )
    if sentinel:
        raise CheonanContractError("media empty-page sentinel changed")
    identities = [row["raw_fields"]["source_identity"] for row in rows]
    if len(rows) != total or len(set(identities)) != total:
        raise CheonanContractError(
            f"media source total mismatch: advertised {total}, parsed {len(rows)}"
        )
    current = [
        row for row in rows
        if row.get("end_date") and row["end_date"] >= cutoff.isoformat()
    ]
    if len(current) > detail_limit:
        raise CheonanContractError("media current details exceed detail_limit")
    for row in current:
        _media_detail(runner, row, cutoff)
    meta = _base_meta("media")
    meta.update({
        "pages": last_page + 1,
        "list_requests": last_page + 1,
        "advertised_last_page": last_page,
        "sentinel_page": last_page + 1,
        "sentinel_count": 0,
        "page_counts": page_counts,
        "source_total": total,
        "source_rows": len(rows),
        "source_current_count": len(current),
        "current_count": len(current),
        "detail_attempts": len(current),
        "detail_pages": len(current),
        "application_control_count": sum(
            bool(row.get("application_url")) for row in current
        ),
        "source_identity_sha256": _identity_hash(identities),
        "branch_count": 1,
        "branch_counts": {CHEONAN_MEDIA_BRANCH: len(current)},
        "municipality_counts": {CHEONAN_DONGNAM_CODE: len(current)},
        "pagination_complete": True,
        "details_complete": True,
    })
    return current, meta


def collect_cheonan_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = CHEONAN_DEFAULT_MAX_PAGES,
    detail_limit: int = CHEONAN_DEFAULT_DETAIL_LIMIT,
    *,
    max_requests: int = CHEONAN_DEFAULT_MAX_REQUESTS,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete and atomic Cheonan public programme snapshot."""

    owner = _OWNERS.get(_provider(target), "unknown")
    meta = _base_meta(owner)
    if not is_cheonan_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match an exact canonical Cheonan owner route"
        )
        return [], CHEONAN_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], CHEONAN_PARSER, meta
        session_factory = _default_session_factory
    try:
        timeout = _positive(timeout, "timeout")
        max_pages = _positive(max_pages, "max_pages")
        detail_limit = _positive(detail_limit, "detail_limit")
        max_requests = _positive(max_requests, "max_requests")
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], CHEONAN_PARSER, meta

    runner = _Runner(session_factory, timeout, max_requests, sleeper)
    try:
        try:
            if owner == "integrated":
                rows, meta = _collect_integrated(runner, cutoff, max_pages, detail_limit)
            elif owner == "library":
                rows, meta = _collect_library(runner, cutoff, max_pages, detail_limit)
            elif owner in {"seongjeong", "dujeong"}:
                rows, meta = _collect_lifelong(
                    runner, cutoff, max_pages, detail_limit, owner
                )
            elif owner == "disability":
                rows, meta = _collect_disability(runner, cutoff, max_pages, detail_limit)
            elif owner == "experience":
                rows, meta = _collect_experience(
                    runner, cutoff, max_pages, detail_limit
                )
            else:
                rows, meta = _collect_media(runner, cutoff, max_pages, detail_limit)
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in rows]))
            if len(result) != len(rows):
                raise CheonanContractError(
                    f"dedupe changed complete row count {len(rows)} to {len(result)}"
                )
            forbidden_keys = {
                "instructor", "teacher", "phone", "contact", "email", "manager",
                "applicant_name", "birth_date", "address",
            }
            if any(forbidden_keys.intersection(row) for row in result):
                raise CheonanContractError("PII-bearing output field detected")
            serialized = json.dumps(result, ensure_ascii=False)
            if _RESIDENT_ID_RE.search(serialized) or _EMAIL_RE.search(serialized):
                raise CheonanContractError("PII-bearing output value detected")
            meta.update({
                "returned_count": len(result),
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "discovered_links": int(meta.get("source_rows") or 0),
                "pagination_detected": int(meta.get("advertised_last_page") or 1) > 1,
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
            return result, CHEONAN_PARSER, meta
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
            return [], CHEONAN_PARSER, meta
    finally:
        runner.close()


collect = collect_cheonan_education_courses


__all__ = [name for name in globals() if name.startswith("CHEONAN_")] + [
    "CheonanContractError",
    "collect",
    "collect_cheonan_education_courses",
    "is_cheonan_education_target",
    "is_target",
]
