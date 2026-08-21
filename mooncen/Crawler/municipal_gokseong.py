"""Fail-closed collectors for Gokseong-gun's official education catalogues.

Gokseong has two independent public education systems.  The Gokseong Future
Education Foundation portal owns three catalogues: foundation programmes,
institution/village programmes, and school-only programmes.  Its retired
``educationList.es`` route is not a complete alias: it contains the first two
catalogues but omits every school-only programme.  The three current
``education.es`` routes are therefore crawled together under one canonical
provider.

The Jeonnam-Gwangju Special Metropolitan City Office of Education's Gokseong
Education and Culture Center separately owns a lifelong lecture list and a
reading/culture event list.  They have disjoint identities and titles, so both
are canonical sources rather than aliases of the municipal portal.

All three collectors prove the declared catalogue boundary, every numbered
page, an immediate empty sentinel, continuous source numbering, and every
current/future detail page before publishing a snapshot.  The municipal
portal expands bookable schedule rows into courses and retains programmes
without schedule rows as one course.  TLS verification is never disabled.

This module deliberately does not import ``Crawler_MunicipalYaml``.  The
shared router must inject its managed session factory (and normally its
managed soup fetcher), avoiding an import cycle and preserving the outbound
security boundary.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GOKSEONG_MUNICIPALITY_CODE = "1272000000"
GOKSEONG_MUNICIPALITY_NAME = "전남광주통합특별시 곡성군"

GOKSEONG_GOKMG_PROVIDER = "MUNI_WWW_GOKMG_OR_KR_58036A89"
GOKSEONG_GOKMG_HOST = "www.gokmg.or.kr"
GOKSEONG_GOKMG_PATH = "/edu/education.es"
GOKSEONG_GOKMG_URL = (
    "https://www.gokmg.or.kr/edu/education.es?"
    "mid=a10301000000&category=foundation"
)
GOKSEONG_GOKMG_BRANCH = "곡성군미래교육재단"
GOKSEONG_GOKMG_PAGE_SIZE = 10
GOKSEONG_GOKMG_PARSER = (
    "gokseong_gokmg_three_complete_catalogues+empty_sentinels+"
    "current_detail_schedules+identity_bound_application_route_variants"
)

GOKSEONG_JNE_HOST = "gslib.jne.go.kr"
GOKSEONG_JNE_BRANCH = "전남광주통합특별시교육청곡성교육문화회관"
GOKSEONG_JNE_LECTURE_PROVIDER = "MUNI_GSLIB_JNE_GO_KR_80914C01"
GOKSEONG_JNE_LECTURE_URL = (
    "https://gslib.jne.go.kr/lecture.es?mid=c10402000000"
)
GOKSEONG_JNE_LECTURE_PARSER = (
    "gokseong_jne_lecture_complete_pages+empty_sentinel+current_detail"
)
GOKSEONG_JNE_EDUCATION_PROVIDER = "MUNI_GSLIB_JNE_GO_KR_F1BD0233"
GOKSEONG_JNE_EDUCATION_URL = (
    "https://gslib.jne.go.kr/education.es?mid=c10208000000&eid=0130"
)
GOKSEONG_JNE_EDUCATION_PARSER = (
    "gokseong_jne_reading_event_complete_pages+empty_sentinel+current_detail"
)

GOKSEONG_SESSION_REQUEST_LIMIT = 150
GOKSEONG_SOURCE_SCAN_ATTEMPTS = 4
GOKSEONG_REQUEST_SAFETY_MARGIN = 10

# Audited aliases, subsets, discovery shells, and non-education routes.  The
# shared target configuration can bind these constants without rediscovering
# ownership semantics.
GOKSEONG_GOKMG_LEGACY_AGGREGATE_URL = (
    "https://www.gokmg.or.kr/edu/educationList.es?mid=a10301000000"
)
GOKSEONG_GOKMG_LEGACY_DETAIL_URL = (
    "https://www.gokmg.or.kr/edu/educationView.es?"
    "mid=a10301000000&edu_seq=491"
)
GOKSEONG_GOKMG_MENU_ALIAS_URLS = (
    "https://www.gokmg.or.kr/edu/menu.es?mid=a10301000000",
    "https://www.gokmg.or.kr/edu/menu.es?mid=a10304000000",
    "https://www.gokmg.or.kr/edu/menu.es?mid=a10305000000",
)
GOKSEONG_GOKMG_SPACE_RESERVATION_URL = (
    "https://www.gokmg.or.kr/edu/rentList.es?mid=a10302010000"
)
GOKSEONG_DISCOVERY_SHELL_URLS = (
    "https://www.gokseong.go.kr/",
    "https://www.gokseong.go.kr/kr/main.do",
)
GOKSEONG_RETIRED_MENU_URLS = (
    "https://www.gokmg.or.kr/edu/menu.es?mid=a50804040000",
)

GOKSEONG_CANDIDATE_IDS: Mapping[str, str] = {
    "jne_lecture": "MUNI_IR_C925653A81D5",
    "jne_reading_event": "MUNI_IR_EA48968BCAE0",
    "gokmg_legacy_detail": "MUNI_IR_30AB3358C05F",
    "gokseong_intro": "MUNI_IR_C2976FD8D532",
    "gokseong_main": "MUNI_IR_75A649D22820",
}


@dataclass(frozen=True)
class GokmgSource:
    code: str
    mid: str
    category: str
    name: str
    branch: str


GOKSEONG_GOKMG_SOURCES: tuple[GokmgSource, ...] = (
    GokmgSource(
        "foundation",
        "a10301000000",
        "foundation",
        "미래교육재단 교육",
        GOKSEONG_GOKMG_BRANCH,
    ),
    GokmgSource(
        "agency",
        "a10304000000",
        "agency",
        "기관·마을 배움터 교육",
        "곡성군 기관·마을 배움터",
    ),
    GokmgSource(
        "school",
        "a10305000000",
        "school",
        "학교대상 교육",
        "곡성군 학교대상 교육",
    ),
)
_GOKMG_SOURCE_BY_CODE = {source.code: source for source in GOKSEONG_GOKMG_SOURCES}


@dataclass(frozen=True)
class JneSource:
    kind: str
    provider: str
    url: str
    path: str
    mid: str
    eid: str
    page_size: int
    parser: str
    category: str


GOKSEONG_JNE_SOURCES: tuple[JneSource, ...] = (
    JneSource(
        "lecture",
        GOKSEONG_JNE_LECTURE_PROVIDER,
        GOKSEONG_JNE_LECTURE_URL,
        "/lecture.es",
        "c10402000000",
        "",
        100,
        GOKSEONG_JNE_LECTURE_PARSER,
        "평생학습",
    ),
    JneSource(
        "education",
        GOKSEONG_JNE_EDUCATION_PROVIDER,
        GOKSEONG_JNE_EDUCATION_URL,
        "/education.es",
        "c10208000000",
        "0130",
        10,
        GOKSEONG_JNE_EDUCATION_PARSER,
        "독서문화행사",
    ),
)
_JNE_SOURCE_BY_PROVIDER = {
    source.provider: source for source in GOKSEONG_JNE_SOURCES
}


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_IDENTITY_RE = re.compile(r"\d+")
_GOKMG_TOTAL_RE = re.compile(
    r"전체\s*([\d,]+)\s*건\s*페이지\s*(\d+)\s*/\s*(\d+)"
)
_CAPACITY_RE = re.compile(r"([\d,]+)\s*명?\s*/\s*([\d,]+)\s*명?")

_GOKMG_HEADERS = (
    "번호",
    "프로그램명",
    "선정방법",
    "신청/정원",
    "대기/대기정원",
    "진행상태",
)
_GOKMG_CATEGORIES = frozenset(
    {"미래교육재단", "교육기관", "청소년기관", "마을학교", "마을배움터", "기타"}
)
_GOKMG_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "대기자접수": "OPEN",
    "상시모집": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "별도문의": "CLOSED",
}
_GOKMG_TEST_TITLES = frozenset({"테스트 페이지"})
# The official education catalogue also carries a small number of civic-group,
# grant, and publicity recruitments.  They are legitimate portal records but
# not education courses, so keep the exclusion narrow and title-auditable.
_GOKMG_NON_COURSE_TITLE_TOKENS = (
    "1388청소년지원단",
    "평생학습공동체",
    "미디어대학생홍보단",
)

_JNE_STATUS_MAP: Mapping[str, str] = {
    "신청": "OPEN",
    "신청가능": "OPEN",
    "신청중": "OPEN",
    "접수중": "OPEN",
    "모집중": "OPEN",
    "대기접수": "OPEN",
    "접수전": "SCHEDULED",
    "신청예정": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "모집예정": "SCHEDULED",
    "마감": "CLOSED",
    "신청마감": "CLOSED",
    "접수마감": "CLOSED",
    "모집마감": "CLOSED",
    "종료": "CLOSED",
}
_JNE_LECTURE_HEADERS = (
    "연번",
    "강좌명",
    "대상",
    "운영기간",
    "인터넷접수",
    "신청 / 정원 (대기인원)",
    "상태",
)
_JNE_EDUCATION_HEADERS = (
    "번호",
    "강좌명",
    "인터넷접수",
    "수강기간",
    "신청 / 정원 (신청/대기)",
    "비고",
)
_JNE_LECTURE_DETAIL_REQUIRED = frozenset(
    {
        "강좌명",
        "대상",
        "신청기간",
        "운영기간",
        "강의 시간",
        "강의 요일",
        "교육장소",
        "모집인원",
        "신청자",
        "신청방법",
        "접수상태",
    }
)
_JNE_EDUCATION_DETAIL_REQUIRED = frozenset(
    {
        "강좌명",
        "대상",
        "수강시간",
        "인터넷 접수기간",
        "수강인원",
        "신청자",
        "교육장소",
        "비고",
    }
)
_JNE_EDUCATION_PERIOD_FIELDS = ("수강일", "수강기간")


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).lower())


def _jne_status(value: Any) -> str:
    """Normalize an exact JNE status or its audited scheduled-detail suffix."""

    cleaned = _clean(value)
    if cleaned in _JNE_STATUS_MAP:
        return _JNE_STATUS_MAP[cleaned]
    match = re.fullmatch(
        r"(신청예정|접수예정|모집예정)\s+접수시작\s*:\s*"
        r"20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}\s+\d{1,2}:\d{2}",
        cleaned,
    )
    return _JNE_STATUS_MAP.get(match.group(1), "") if match else ""


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _single_query(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _date_tokens(value: Any) -> tuple[date, ...]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            result.append(date(*(int(part) for part in match.groups())))
        except ValueError:
            return ()
    return tuple(result)


def _date_range(value: Any, *, allow_single: bool = False) -> tuple[str, str, str]:
    tokens = _date_tokens(value)
    if len(tokens) == 1 and allow_single:
        tokens = (tokens[0], tokens[0])
    if len(tokens) != 2 or tokens[1] < tokens[0]:
        return "", "", ""
    start = tokens[0].isoformat()
    end = tokens[1].isoformat()
    return start, end, f"{start} ~ {end}"


def _integer(value: Any) -> Optional[int]:
    raw = re.sub(r"[^\d]", "", _clean(value))
    return int(raw) if raw else None


def _capacity_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    match = _CAPACITY_RE.search(_clean(value))
    if not match:
        return None, None
    return int(match.group(1).replace(",", "")), int(
        match.group(2).replace(",", "")
    )


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _response_soup(response: Any) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError("HTTP redirects are not accepted")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    return BeautifulSoup(content, "lxml")


class _SoupClient:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        fetcher: Optional[Fetcher],
        timeout: int,
    ) -> None:
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.timeout = timeout
        self.current: Any = None
        self.session_requests = GOKSEONG_SESSION_REQUEST_LIMIT
        self.sessions_created = 0
        self.physical_requests = 0

    def _new_session(self) -> None:
        _close_quietly(self.current)
        self.current = self.session_factory()
        self.session_requests = 0
        self.sessions_created += 1
        headers = getattr(self.current, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                }
            )

    def get(self, url: str) -> BeautifulSoup:
        if (
            self.current is None
            or self.session_requests >= GOKSEONG_SESSION_REQUEST_LIMIT
        ):
            self._new_session()
        self.session_requests += 1
        self.physical_requests += 1
        if self.fetcher is not None:
            value = self.fetcher(self.current, url, self.timeout)
            if not hasattr(value, "select"):
                raise ValueError("managed fetcher did not return parsed HTML")
            return value
        response = self.current.get(
            url,
            timeout=self.timeout,
            allow_redirects=False,
        )
        return _response_soup(response)

    def post(
        self,
        url: str,
        data: Mapping[str, str],
        *,
        fixture_url: str = "",
    ) -> BeautifulSoup:
        """Submit the portal's official pagination form in the same session.

        The GOKMG list order is not snapshot-stable when numbered pages are
        fetched as independent GETs.  Its own browser flow posts ``infoForm``;
        doing the same retains the server-side list context and produces a
        stable identity set.  Lightweight fixture sessions intentionally lack
        ``post`` and continue through the injected HTML fetcher.
        """

        if (
            self.current is None
            or self.session_requests >= GOKSEONG_SESSION_REQUEST_LIMIT
        ):
            self._new_session()
        self.session_requests += 1
        self.physical_requests += 1
        post = getattr(self.current, "post", None)
        if callable(post):
            response = post(
                url,
                data=dict(data),
                timeout=self.timeout,
                allow_redirects=False,
            )
            return _response_soup(response)
        if self.fetcher is not None and fixture_url:
            value = self.fetcher(self.current, fixture_url, self.timeout)
            if not hasattr(value, "select"):
                raise ValueError("managed fetcher did not return parsed HTML")
            return value
        raise ValueError("managed session does not support form POST")

    def close(self) -> None:
        _close_quietly(self.current)

    def rotate(self) -> None:
        self._new_session()


def _strict_https_route(
    value: Any,
    *,
    host: str,
    path: str,
) -> tuple[Any, dict[str, list[str]]]:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != host
        or parsed.port is not None
        or parsed.path != path
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return None, {}
    return parsed, query


def is_gokseong_gokmg_target(target: Any) -> bool:
    parsed, query = _strict_https_route(
        _target_url(target), host=GOKSEONG_GOKMG_HOST, path=GOKSEONG_GOKMG_PATH
    )
    return bool(
        _provider(target) == GOKSEONG_GOKMG_PROVIDER
        and parsed is not None
        and set(query) == {"mid", "category"}
        and _single_query(query, "mid") == GOKSEONG_GOKMG_SOURCES[0].mid
        and _single_query(query, "category")
        == GOKSEONG_GOKMG_SOURCES[0].category
    )


def _is_jne_target(target: Any, source: JneSource) -> bool:
    parsed, query = _strict_https_route(
        _target_url(target), host=GOKSEONG_JNE_HOST, path=source.path
    )
    expected = {"mid"} | ({"eid"} if source.eid else set())
    return bool(
        _provider(target) == source.provider
        and parsed is not None
        and set(query) == expected
        and _single_query(query, "mid") == source.mid
        and (not source.eid or _single_query(query, "eid") == source.eid)
    )


def is_gokseong_education_target(target: Any) -> bool:
    if is_gokseong_gokmg_target(target):
        return True
    return any(_is_jne_target(target, source) for source in GOKSEONG_JNE_SOURCES)


is_target = is_gokseong_education_target


def gokmg_list_url(source_code: Any, page: Any = 1) -> str:
    source = _GOKMG_SOURCE_BY_CODE.get(_clean(source_code))
    raw_page = _clean(page)
    if source is None or not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    query: list[tuple[str, Any]] = [
        ("mid", source.mid),
        ("category", source.category),
    ]
    if int(raw_page) > 1:
        query.append(("nPage", int(raw_page)))
    return f"https://{GOKSEONG_GOKMG_HOST}{GOKSEONG_GOKMG_PATH}?" + urlencode(
        query
    )


def gokmg_detail_url(source_code: Any, identity: Any) -> str:
    source = _GOKMG_SOURCE_BY_CODE.get(_clean(source_code))
    raw_identity = _clean(identity)
    if source is None or not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{GOKSEONG_GOKMG_HOST}/edu/educationView.es?" + urlencode(
        {
            "mid": source.mid,
            "category": source.category,
            "edu_seq": raw_identity,
        }
    )


def gokmg_page_form(source_code: Any, page: Any) -> dict[str, str]:
    """Return the exact empty-filter ``infoForm`` payload used by the site."""

    source = _GOKMG_SOURCE_BY_CODE.get(_clean(source_code))
    raw_page = _clean(page)
    if source is None or not raw_page.isdigit() or int(raw_page) < 1:
        return {}
    return {
        "mid": source.mid,
        "category": source.category,
        "edu_seq": "",
        "seq": "",
        "nPage": str(int(raw_page)),
        "offset": "Y",
        "chk_lepr_arr": "",
        "chk_belo_arr": "",
        "chk_state_arr": "",
        "srh_edu_sdate": "",
        "srh_edu_edate": "",
        "keyField": "",
        "keyWord": "",
    }


def jne_list_url(provider: Any, page: Any = 1) -> str:
    source = _JNE_SOURCE_BY_PROVIDER.get(_clean(provider))
    raw_page = _clean(page)
    if source is None or not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    query: list[tuple[str, Any]] = [("mid", source.mid)]
    if source.eid:
        query.append(("eid", source.eid))
    if int(raw_page) > 1:
        query.append(("nPage", int(raw_page)))
    return f"https://{GOKSEONG_JNE_HOST}{source.path}?" + urlencode(query)


def _gokmg_detail_identity(source: GokmgSource, value: Any) -> str:
    parsed, query = _strict_https_route(
        urljoin(gokmg_list_url(source.code), _clean(value)),
        host=GOKSEONG_GOKMG_HOST,
        path="/edu/educationView.es",
    )
    identity = _single_query(query, "edu_seq")
    if (
        parsed is None
        or set(query) != {"mid", "category", "edu_seq"}
        or _single_query(query, "mid") != source.mid
        or _single_query(query, "category") != source.category
        or not _IDENTITY_RE.fullmatch(identity)
    ):
        return ""
    return identity


def _gokmg_application_url(
    source: GokmgSource, identity: str, value: Any
) -> str:
    parsed, query = _strict_https_route(
        urljoin(gokmg_detail_url(source.code, identity), _clean(value)),
        host=GOKSEONG_GOKMG_HOST,
        path="/edu/educationMemberForm.es",
    )
    sequence = _single_query(query, "seq")
    if (
        parsed is None
        or _single_query(query, "mid") != source.mid
        or _single_query(query, "edu_seq") != identity
    ):
        return ""
    legacy = bool(
        set(query) == {"mid", "category", "edu_seq", "seq"}
        and _single_query(query, "category") == source.category
        and _IDENTITY_RE.fullmatch(sequence)
    )
    current = bool(
        set(query) == {"mid", "target", "educ_cg", "edu_seq", "seq"}
        and _single_query(query, "target") == ""
        and _single_query(query, "educ_cg") == ""
        and sequence.isdigit()
    )
    if not legacy and not current:
        return ""
    values = (
        {
            "mid": source.mid,
            "category": source.category,
            "edu_seq": identity,
            "seq": sequence,
        }
        if legacy
        else {
            "mid": source.mid,
            "target": "",
            "educ_cg": "",
            "edu_seq": identity,
            "seq": sequence,
        }
    )
    return f"https://{GOKSEONG_GOKMG_HOST}{parsed.path}?" + urlencode(values)


def _jne_detail_identity(source: JneSource, value: Any) -> str:
    parsed, query = _strict_https_route(
        urljoin(source.url, _clean(value)), host=GOKSEONG_JNE_HOST, path=source.path
    )
    identity_key = "el_seq" if source.kind == "lecture" else "edu_seq"
    identity = _single_query(query, identity_key)
    allowed = {"mid", "act", identity_key, "nPage"}
    if source.eid:
        allowed |= {"eid", "educ_cg"}
    if (
        parsed is None
        or not set(query).issubset(allowed)
        or not {"mid", "act", identity_key}.issubset(query)
        or _single_query(query, "mid") != source.mid
        or _single_query(query, "act") != "view"
        or (source.eid and _single_query(query, "eid") != source.eid)
        or not _IDENTITY_RE.fullmatch(identity)
    ):
        return ""
    return identity


def _jne_detail_url(source: JneSource, identity: str) -> str:
    key = "el_seq" if source.kind == "lecture" else "edu_seq"
    query: list[tuple[str, str]] = [("mid", source.mid)]
    if source.eid:
        query.append(("eid", source.eid))
    query.extend(((key, identity), ("act", "view")))
    return f"https://{GOKSEONG_JNE_HOST}{source.path}?" + urlencode(query)


def _jne_application_url(source: JneSource, identity: str, value: Any) -> str:
    parsed, query = _strict_https_route(
        urljoin(source.url, _clean(value)), host=GOKSEONG_JNE_HOST, path=source.path
    )
    identity_key = "el_seq" if source.kind == "lecture" else "edu_seq"
    allowed_actions = {"agree", "write", "apply"}
    if (
        parsed is None
        or _single_query(query, "mid") != source.mid
        or _single_query(query, identity_key) != identity
        or _single_query(query, "act") not in allowed_actions
        or (source.eid and _single_query(query, "eid") != source.eid)
    ):
        return ""
    allowed = {"mid", "act", identity_key}
    if source.eid:
        allowed |= {"eid", "educ_cg"}
    if not set(query).issubset(allowed):
        return ""
    canonical: list[tuple[str, str]] = [("mid", source.mid)]
    if source.eid:
        canonical.append(("eid", source.eid))
    canonical.extend(
        ((identity_key, identity), ("act", _single_query(query, "act")))
    )
    return f"https://{GOKSEONG_JNE_HOST}{source.path}?" + urlencode(canonical)


def _table_headers(table: Any) -> tuple[str, ...]:
    return tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))


def _gokmg_page_contract(soup: BeautifulSoup) -> Optional[tuple[int, int, int]]:
    matches = {
        (int(total.replace(",", "")), int(page), int(last))
        for total, page, last in _GOKMG_TOTAL_RE.findall(
            _clean(soup.get_text(" ", strip=True))
        )
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _gokmg_list_rows(
    source: GokmgSource,
    soup: BeautifulSoup,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
    if source.name not in title or "곡성교육포털" not in title:
        errors.append(f"{source.code}: wrong catalogue page title")
    tables = soup.select("table.tstyle_list")
    if len(tables) != 1:
        return [], errors + [f"{source.code}: expected one catalogue table"]
    table = tables[0]
    if _table_headers(table) != _GOKMG_HEADERS:
        errors.append(f"{source.code}: catalogue header contract changed")

    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        number = _clean(cells[0].get_text(" ", strip=True))
        if not number.isdigit():
            if (
                "no-data" in (cells[0].get("class") or [])
                or cells[0].select_one(".no_result.nodata") is not None
                or "해당되는 교육이 없습니다" in _clean(
                    tr.get_text(" ", strip=True)
                )
            ):
                continue
            errors.append(f"{source.code}: non-numeric source sequence")
            continue
        if len(cells) != 7:
            errors.append(f"{source.code}: malformed catalogue row {number}")
            continue
        program_cell = cells[2]
        links = program_cell.select("a[href]")
        if len(links) != 1:
            errors.append(f"{source.code}: row {number} has ambiguous detail link")
            continue
        identity = _gokmg_detail_identity(source, links[0].get("href"))
        title_node = program_cell.select_one("span.title strong")
        category_node = program_cell.select_one("span.title span.cate")
        target_node = program_cell.select_one("span.item.object span")
        period_node = program_cell.select_one("span.item.edu-period span")
        apply_node = program_cell.select_one("span.item.appl-period span")
        row_title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
        category = _clean(category_node.get_text(" ", strip=True)) if category_node else ""
        target = _clean(target_node.get_text(" ", strip=True)) if target_node else ""
        raw_period = _clean(period_node.get_text(" ", strip=True)) if period_node else ""
        raw_apply = _clean(apply_node.get_text(" ", strip=True)) if apply_node else ""
        source_status = _clean(cells[6].get_text(" ", strip=True))
        start, end, period = _date_range(raw_period, allow_single=True)
        apply_start, apply_end, apply_period = _date_range(
            raw_apply, allow_single=True
        )
        current_capacity, total_capacity = _capacity_pair(cells[4].get_text(" ", strip=True))
        wait_current, wait_total = _capacity_pair(cells[5].get_text(" ", strip=True))

        if not identity or not row_title:
            errors.append(f"{source.code}: row {number} lacks stable identity/title")
        if category not in _GOKMG_CATEGORIES:
            errors.append(f"{source.code}: row {number} has unknown organizer category")
        if source_status not in _GOKMG_STATUS_MAP:
            errors.append(f"{source.code}: row {number} has unknown source status")
        if not period:
            errors.append(f"{source.code}: row {number} has malformed education period")
        if raw_apply and not apply_period and source_status != "별도문의":
            errors.append(f"{source.code}: row {number} has malformed application period")
        if source_status not in {"별도문의"} and not raw_apply:
            errors.append(f"{source.code}: row {number} lacks application period")
        if current_capacity is None or total_capacity is None:
            errors.append(f"{source.code}: row {number} has malformed capacity")
        if wait_current is None or wait_total is None:
            errors.append(f"{source.code}: row {number} has malformed waitlist capacity")

        rows.append(
            {
                "source_sequence": int(number),
                "source_code": source.code,
                "identity": identity,
                "title": row_title,
                "provider_organizer": category,
                "target": target,
                "raw_period": raw_period,
                "period": period,
                "start_date": start,
                "end_date": end,
                "raw_apply_period": raw_apply,
                "apply_period": apply_period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "source_status": source_status,
                "status": _GOKMG_STATUS_MAP.get(source_status, ""),
                "selection_method": _clean(cells[3].get_text(" ", strip=True)),
                "capacity_current": current_capacity,
                "capacity_total": total_capacity,
                "waitlist_current": wait_current,
                "waitlist_total": wait_total,
                "raw_url": gokmg_detail_url(source.code, identity),
            }
        )
    return rows, errors


def _basic_pairs(container: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if container is None:
        return pairs
    for item in container.select("ul.item > li:not(.title)"):
        label = item.find("strong", recursive=False)
        value = item.find("span", recursive=False)
        if label is not None and value is not None:
            pairs[_clean(label.get_text(" ", strip=True))] = _clean(
                value.get_text(" ", strip=True)
            )
    return pairs


def _schedule_identity(
    source: GokmgSource,
    parent_identity: str,
    label: str,
    period: str,
) -> str:
    # The application window is mutable administrative metadata.  Excluding it
    # keeps the same source schedule on the same ID when only reception dates
    # are corrected; the source, parent, label and course period still form an
    # unambiguous identity.
    fingerprint = "|".join(
        (
            source.code,
            parent_identity,
            _normalized(label),
            period,
        )
    )
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:20]
    return f"{GOKSEONG_GOKMG_PROVIDER}:schedule:{parent_identity}:{digest}"


def _schedule_raw_url(value: Any, provider_course_id: str) -> str:
    parsed = urlparse(_clean(value))
    digest = provider_course_id.rsplit(":", 1)[-1]
    return parsed._replace(fragment=f"schedule-{digest}").geturl()


def _is_gokmg_non_course_title(value: Any) -> bool:
    normalized = _normalized(value)
    return any(
        _normalized(token) in normalized
        for token in _GOKMG_NON_COURSE_TITLE_TOKENS
    )


def _schedule_display_title(parent_title: Any, label: Any) -> str:
    parent = _clean(parent_title)
    variant = re.sub(r"^\d+\s*회차\s*/\s*", "", _clean(label)).strip()
    if not variant or _normalized(variant) == _normalized(parent):
        return parent or variant
    if _normalized(parent) and _normalized(parent) in _normalized(variant):
        return variant
    return f"{parent} - {variant}" if parent else variant


def _base_course_fields() -> dict[str, Any]:
    return {
        "collection_category": "교육",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_integrated_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "program_type": "강좌",
        "region": GOKSEONG_MUNICIPALITY_NAME,
        "municipality_code": GOKSEONG_MUNICIPALITY_CODE,
        "municipality_full_name": GOKSEONG_MUNICIPALITY_NAME,
    }


def _gokmg_detail_rows(
    source: GokmgSource,
    parent: dict[str, Any],
    soup: BeautifulSoup,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    identity = _clean(parent.get("identity"))
    view = soup.select_one("div.board_view.type2")
    basic = view.select_one("div.basic") if view is not None else None
    if view is None or basic is None:
        return [], [f"{source.code}:{identity}: missing detail container"]
    title_node = basic.select_one("ul.item > li.title > strong")
    detail_title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
    category_node = basic.select_one("ul.item > li.title .state .type.category")
    detail_category = _clean(category_node.get_text(" ", strip=True)) if category_node else ""
    if _normalized(detail_title) != _normalized(parent.get("title")):
        errors.append(f"{source.code}:{identity}: detail/list title mismatch")
    if detail_category != _clean(parent.get("provider_organizer")):
        errors.append(f"{source.code}:{identity}: detail/list organizer mismatch")

    pairs = _basic_pairs(basic)
    detail_start, detail_end, detail_period = _date_range(
        pairs.get("교육기간"), allow_single=True
    )
    if (
        detail_period != _clean(parent.get("period"))
        or detail_start != _clean(parent.get("start_date"))
        or detail_end != _clean(parent.get("end_date"))
    ):
        errors.append(f"{source.code}:{identity}: detail/list education period mismatch")
    detail_apply_start, detail_apply_end, detail_apply_period = _date_range(
        pairs.get("신청기간"), allow_single=True
    )
    if parent.get("apply_period"):
        if detail_apply_period and (
            detail_apply_start != _clean(parent.get("apply_start"))
            or detail_apply_end != _clean(parent.get("apply_end"))
        ):
            errors.append(
                f"{source.code}:{identity}: detail/list application period mismatch"
            )
        elif (
            not detail_apply_period
            and _clean(parent.get("source_status")) != "별도문의"
        ):
            errors.append(
                f"{source.code}:{identity}: detail omitted application period"
            )

    venue = _clean(pairs.get("교육장소"))
    target = (
        _clean(pairs.get("교육대상"))
        or _clean(parent.get("target"))
        or "대상 별도 안내"
    )
    contact = _clean(pairs.get("접수담당"))
    description_node = view.select_one("div.detail div.edu_conts")
    description = (
        _clean(description_node.get_text(" ", strip=True))
        if description_node is not None
        else ""
    )
    location_node = view.select_one("div.detail .place-info")
    location = (
        _clean(location_node.get_text(" ", strip=True))
        if location_node is not None
        else ""
    )

    tables = view.select("div.detail table.tstyle_list")
    if len(tables) > 1:
        errors.append(f"{source.code}:{identity}: ambiguous education schedule tables")
        return [], errors
    schedule_table = tables[0] if tables else None
    result: list[dict[str, Any]] = []

    if schedule_table is not None:
        headers = _table_headers(schedule_table)
        required = {"교육기간", "접수기간", "신청 / 정원", "진행상태"}
        has_round_title = bool(headers and headers[0] == "회차명")
        if (
            not required.issubset(headers)
            or headers[-1] != "진행상태"
            or headers[0] not in {"회차명", "교육기간"}
        ):
            errors.append(f"{source.code}:{identity}: schedule header contract changed")
            return [], errors
        seen_fingerprints: set[tuple[str, str]] = set()
        for index, tr in enumerate(schedule_table.select("tbody tr"), start=1):
            cells = tr.find_all("td", recursive=False)
            if len(cells) != len(headers):
                errors.append(f"{source.code}:{identity}: malformed schedule row {index}")
                continue
            values = {
                header: _clean(cell.get_text(" ", strip=True))
                for header, cell in zip(headers, cells)
            }
            label = values.get("회차명") or _clean(parent.get("title"))
            start, end, period = _date_range(
                values.get("교육기간"), allow_single=True
            )
            apply_start, apply_end, apply_period = _date_range(
                values.get("접수기간"), allow_single=True
            )
            status_node = cells[-1].select_one("[data-label]")
            source_status = (
                _clean(status_node.get("data-label"))
                if status_node is not None
                else values.get("진행상태", "")
            )
            application_links = cells[-1].select("a[href]")
            application_url = ""
            if len(application_links) == 1:
                application_url = _gokmg_application_url(
                    source, identity, application_links[0].get("href")
                )
                if not application_url:
                    errors.append(
                        f"{source.code}:{identity}: unsafe schedule application link"
                    )
            elif len(application_links) > 1:
                errors.append(
                    f"{source.code}:{identity}: ambiguous schedule application links"
                )
            if source_status not in _GOKMG_STATUS_MAP:
                errors.append(
                    f"{source.code}:{identity}: unknown schedule source status"
                )
            if not label or not period or not apply_period:
                errors.append(
                    f"{source.code}:{identity}: malformed schedule dates/title"
                )
            if (_GOKMG_STATUS_MAP.get(source_status) == "OPEN") != bool(
                application_url
            ):
                errors.append(
                    f"{source.code}:{identity}: schedule status/application mismatch"
                )
            fingerprint = (_normalized(label), period)
            if fingerprint in seen_fingerprints:
                errors.append(
                    f"{source.code}:{identity}: duplicate schedule fingerprint"
                )
            seen_fingerprints.add(fingerprint)

            capacity_current, capacity_total = _capacity_pair(
                values.get("신청 / 정원")
            )
            wait_current, wait_total = _capacity_pair(
                values.get("대기 / 대기정원")
            )
            if capacity_current is None or capacity_total is None:
                errors.append(f"{source.code}:{identity}: malformed schedule capacity")
            display_title = (
                _schedule_display_title(parent.get("title"), label)
                if has_round_title
                else _clean(parent.get("title"))
            )
            provider_course_id = _schedule_identity(
                source, identity, label, period
            )
            row: dict[str, Any] = {
                "provider": GOKSEONG_GOKMG_PROVIDER,
                "provider_course_id": provider_course_id,
                "prefer_incoming_provider_course_id": True,
                "title": display_title or _clean(parent.get("title")),
                "branch": source.branch,
                "provider_organizer": detail_category,
                "category": source.name,
                "raw_url": _schedule_raw_url(
                    parent.get("raw_url"), provider_course_id
                ),
                "application_url": application_url,
                "application_type": (
                    "ONLINE_RESERVATION" if application_url else ""
                ),
                "reservation_available": bool(application_url),
                "status": _GOKMG_STATUS_MAP.get(source_status, ""),
                "period": period,
                "start_date": start,
                "end_date": end,
                "apply_period": apply_period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": (
                    label if has_round_title else values.get("교육기간", "")
                ),
                "target": target,
                "fee": values.get("수강료") or "요금 별도 안내",
                "capacity": capacity_total,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "waitlist_current": wait_current,
                "waitlist_total": wait_total,
                "venue_name": venue or source.branch,
                "venue_address": location,
                "contact": contact,
                "description": description or _clean(parent.get("title")),
                "collection_type": "complete_html_pages+detail_schedule",
                "raw_fields": {
                    "parser": GOKSEONG_GOKMG_PARSER,
                    "source_code": source.code,
                    "source_sequence": parent.get("source_sequence"),
                    "parent_edu_seq": identity,
                    "parent_title": parent.get("title"),
                    "source_status": source_status,
                    "list_source_status": parent.get("source_status"),
                    "schedule_label": label,
                    "schedule_headers": list(headers),
                    "detail_pairs": pairs,
                },
                **_base_course_fields(),
            }
            result.append(_clean_row(row))
    else:
        status_node = basic.select_one(
            "ul.item > li.title .state .type:not(.category)"
        )
        source_status = (
            _clean(status_node.get_text(" ", strip=True))
            if status_node is not None
            else _clean(parent.get("source_status"))
        )
        if source_status not in _GOKMG_STATUS_MAP:
            errors.append(f"{source.code}:{identity}: unknown detail source status")
        application_links = view.select("a[href*='educationMemberForm.es']")
        application_url = ""
        if len(application_links) == 1:
            application_url = _gokmg_application_url(
                source, identity, application_links[0].get("href")
            )
            if not application_url:
                errors.append(f"{source.code}:{identity}: unsafe application link")
        elif len(application_links) > 1:
            errors.append(f"{source.code}:{identity}: ambiguous application links")
        if (_GOKMG_STATUS_MAP.get(source_status) == "OPEN") != bool(
            application_url
        ):
            errors.append(f"{source.code}:{identity}: status/application mismatch")
        row = {
            "provider": GOKSEONG_GOKMG_PROVIDER,
            "provider_course_id": (
                f"{GOKSEONG_GOKMG_PROVIDER}:program:{identity}"
            ),
            "prefer_incoming_provider_course_id": True,
            "title": _clean(parent.get("title")),
            "branch": source.branch,
            "provider_organizer": detail_category,
            "category": source.name,
            "raw_url": _clean(parent.get("raw_url")),
            "application_url": application_url,
            "application_type": "ONLINE_RESERVATION" if application_url else "",
            "reservation_available": bool(application_url),
            "status": _GOKMG_STATUS_MAP.get(source_status, ""),
            "period": _clean(parent.get("period")),
            "start_date": _clean(parent.get("start_date")),
            "end_date": _clean(parent.get("end_date")),
            "apply_period": detail_apply_period or parent.get("apply_period"),
            "apply_start": detail_apply_start or parent.get("apply_start"),
            "apply_end": detail_apply_end or parent.get("apply_end"),
            "schedule_raw": (
                _clean(pairs.get("교육시간"))
                or _clean(parent.get("period"))
                or "시간 별도 안내"
            ),
            "target": target,
            "fee": _clean(pairs.get("수강료")) or "요금 별도 안내",
            "capacity": parent.get("capacity_total"),
            "capacity_current": parent.get("capacity_current"),
            "capacity_total": parent.get("capacity_total"),
            "waitlist_current": parent.get("waitlist_current"),
            "waitlist_total": parent.get("waitlist_total"),
            "venue_name": venue or source.branch,
            "venue_address": location,
            "contact": contact,
            "description": description or _clean(parent.get("title")),
            "collection_type": "complete_html_pages+detail",
            "raw_fields": {
                "parser": GOKSEONG_GOKMG_PARSER,
                "source_code": source.code,
                "source_sequence": parent.get("source_sequence"),
                "parent_edu_seq": identity,
                "source_status": source_status,
                "list_source_status": parent.get("source_status"),
                "detail_pairs": pairs,
            },
            **_base_course_fields(),
        }
        result.append(_clean_row(row))
    return result, errors


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _canonical_gokmg_source_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Classify portal rows sharing an edu_seq for drift-safe retry handling.

    The live portal can expose one detail identity at two consecutive source
    numbers while rows move between paginated requests.  A canonical view is
    useful for diagnostics, but the caller must retry the whole snapshot and
    must not publish this possibly incomplete first pass.
    """

    canonical: list[dict[str, Any]] = []
    by_identity: dict[str, dict[str, Any]] = {}
    exact_duplicate_ids: list[str] = []
    errors: list[str] = []
    for row in rows:
        identity = _clean(row.get("identity"))
        previous = by_identity.get(identity)
        if previous is None:
            by_identity[identity] = row
            canonical.append(row)
            continue
        previous_payload = {
            key: value for key, value in previous.items() if key != "source_sequence"
        }
        current_payload = {
            key: value for key, value in row.items() if key != "source_sequence"
        }
        if previous_payload != current_payload:
            errors.append(f"{identity}: conflicting duplicate programme identity")
        elif identity not in exact_duplicate_ids:
            exact_duplicate_ids.append(identity)
    return canonical, exact_duplicate_ids, errors


@dataclass
class _GokmgSourceScan:
    contract: Optional[tuple[int, int, int]]
    rows: list[dict[str, Any]]
    canonical_rows: list[dict[str, Any]]
    exact_duplicate_ids: list[str]
    page_counts: dict[int, int]
    required_list_requests: int
    list_requests: int
    data_pages: int
    sentinel_pages: int
    source_cap_reached: bool
    errors: list[str]


def _scan_gokmg_source(
    client: _SoupClient,
    source: GokmgSource,
    *,
    max_pages: int,
) -> _GokmgSourceScan:
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    canonical_rows: list[dict[str, Any]] = []
    exact_duplicate_ids: list[str] = []
    counts: dict[int, int] = {}
    contract: Optional[tuple[int, int, int]] = None
    list_requests = 0
    required_list_requests = 0
    data_pages = 0
    sentinel_pages = 0
    source_cap_reached = False

    try:
        first_soup = client.get(gokmg_list_url(source.code))
        list_requests = 1
    except Exception as exc:
        errors.append(f"{source.code}: first page fetch {type(exc).__name__}")
        return _GokmgSourceScan(
            contract,
            rows,
            canonical_rows,
            exact_duplicate_ids,
            counts,
            required_list_requests,
            list_requests,
            data_pages,
            sentinel_pages,
            source_cap_reached,
            errors,
        )

    contract = _gokmg_page_contract(first_soup)
    if contract is None:
        errors.append(f"{source.code}: missing unambiguous total/page contract")
        return _GokmgSourceScan(
            contract, rows, canonical_rows, exact_duplicate_ids, counts, 0,
            list_requests, 0, 0, source_cap_reached, errors
        )
    total, page_number, last = contract
    expected_last = max(1, math.ceil(total / GOKSEONG_GOKMG_PAGE_SIZE))
    required_list_requests = last + 1
    data_pages = last
    sentinel_pages = 1
    if page_number != 1 or last != expected_last or total < 1:
        errors.append(f"{source.code}: invalid first-page total contract")
    if required_list_requests > max_pages:
        source_cap_reached = True
        errors.append(
            f"{source.code}: max_pages cap allows {max_pages} of "
            f"{required_list_requests} required catalogue/sentinel requests"
        )
    if errors:
        return _GokmgSourceScan(
            contract, rows, canonical_rows, exact_duplicate_ids, counts,
            required_list_requests, list_requests, data_pages, sentinel_pages,
            source_cap_reached, errors
        )

    for page in range(1, last + 1):
        try:
            soup = (
                first_soup
                if page == 1
                else client.post(
                    gokmg_list_url(source.code),
                    gokmg_page_form(source.code, page),
                    fixture_url=gokmg_list_url(source.code, page),
                )
            )
            if page > 1:
                list_requests += 1
        except Exception as exc:
            errors.append(f"{source.code}: page {page} fetch {type(exc).__name__}")
            break
        current_contract = _gokmg_page_contract(soup)
        if current_contract != (total, page, last):
            errors.append(f"{source.code}: page {page} total contract changed")
        parsed, row_errors = _gokmg_list_rows(source, soup)
        errors.extend(row_errors)
        expected = min(
            GOKSEONG_GOKMG_PAGE_SIZE,
            total - (page - 1) * GOKSEONG_GOKMG_PAGE_SIZE,
        )
        if len(parsed) != expected:
            errors.append(
                f"{source.code}: page {page} expected {expected} rows, got {len(parsed)}"
            )
        counts[page] = len(parsed)
        rows.extend(parsed)
    if errors:
        return _GokmgSourceScan(
            contract, rows, canonical_rows, exact_duplicate_ids, counts,
            required_list_requests, list_requests, data_pages, sentinel_pages,
            source_cap_reached, errors
        )

    try:
        sentinel = client.post(
            gokmg_list_url(source.code),
            gokmg_page_form(source.code, last + 1),
            fixture_url=gokmg_list_url(source.code, last + 1),
        )
        list_requests += 1
        parsed_sentinel, sentinel_errors = _gokmg_list_rows(source, sentinel)
        errors.extend(sentinel_errors)
        if _gokmg_page_contract(sentinel) != (total, last + 1, last):
            errors.append(f"{source.code}: invalid sentinel page contract")
        if parsed_sentinel:
            errors.append(f"{source.code}: immediate sentinel page is not empty")
        counts[last + 1] = len(parsed_sentinel)
    except Exception as exc:
        errors.append(f"{source.code}: sentinel fetch {type(exc).__name__}")

    numbers = [row["source_sequence"] for row in rows]
    if numbers != list(range(total, 0, -1)):
        errors.append(f"{source.code}: source numbering is not continuous")
    if len(rows) != total:
        errors.append(
            f"{source.code}: declared total {total} != parsed rows {len(rows)}"
        )
    canonical_rows, exact_duplicate_ids, duplicate_errors = (
        _canonical_gokmg_source_rows(rows)
    )
    errors.extend(f"{source.code}:{message}" for message in duplicate_errors)
    if exact_duplicate_ids:
        errors.append(f"{source.code}: duplicate programme identities")
    return _GokmgSourceScan(
        contract,
        rows,
        canonical_rows,
        exact_duplicate_ids,
        counts,
        required_list_requests,
        list_requests,
        data_pages,
        sentinel_pages,
        source_cap_reached,
        errors,
    )


def _failure(message: str, *, source_count: int = 0) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "recovery_list_requests": 0,
        "aggregate_list_requests": 0,
        "physical_requests": 0,
        "request_safety_budget": 0,
        "request_budget_remaining": 0,
        "request_budget_exhausted": False,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_count": source_count,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "transient_duplicate_retry_count": 0,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_gokseong_gokmg_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 60,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return the complete current/future three-catalogue municipal snapshot."""

    if not is_gokseong_gokmg_target(target):
        return [], GOKSEONG_GOKMG_PARSER, _failure(
            "target does not match the canonical Gokseong education portal route",
            source_count=3,
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], GOKSEONG_GOKMG_PARSER, _failure(
                "managed session_factory injection is required", source_count=3
            )
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        request_safety_budget = (
            allowed_pages + allowed_details + GOKSEONG_REQUEST_SAFETY_MARGIN
        )
        cutoff = _today(today)
    except (TypeError, ValueError):
        return [], GOKSEONG_GOKMG_PARSER, _failure(
            "max_pages/detail_limit/today are invalid", source_count=3
        )

    errors: list[str] = []
    source_cap_reached = False
    client = _SoupClient(
        session_factory=session_factory, fetcher=fetcher, timeout=timeout
    )
    contracts: dict[str, tuple[int, int, int]] = {}
    source_rows: dict[str, list[dict[str, Any]]] = {}
    canonical_source_rows: dict[str, list[dict[str, Any]]] = {}
    source_exact_duplicate_ids: dict[str, list[str]] = {}
    page_counts: dict[str, dict[int, int]] = {}
    required_list_requests = 0
    list_requests = 0
    recovery_list_requests = 0
    request_budget_exhausted = False
    transient_duplicate_retry_count = 0
    source_scan_attempts: dict[str, int] = {}
    source_transient_duplicate_ids: dict[str, list[str]] = {}
    data_pages = 0
    sentinel_pages = 0
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    excluded_test_count = 0
    excluded_non_course_count = 0
    excluded_non_course_parent_ids: list[str] = []

    try:
        if allowed_pages < len(GOKSEONG_GOKMG_SOURCES):
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of at least "
                f"{len(GOKSEONG_GOKMG_SOURCES)} required source requests"
            )
        if not errors:
            # The portal keeps the selected catalogue in server-side session
            # state.  Crawl each source from page one through its sentinel
            # before selecting the next source; prefetching all three first
            # pages would contaminate later pagination with the last category.
            for source in GOKSEONG_GOKMG_SOURCES:
                remaining_pages = allowed_pages - required_list_requests
                scans: list[_GokmgSourceScan] = []
                transient_ids: list[str] = []
                for attempt in range(GOKSEONG_SOURCE_SCAN_ATTEMPTS):
                    remaining_physical_requests = (
                        request_safety_budget - client.physical_requests
                    )
                    if remaining_physical_requests < 1:
                        request_budget_exhausted = True
                        source_cap_reached = True
                        errors.append(
                            "physical request safety budget exhausted before "
                            f"{source.code} scan attempt {attempt + 1}"
                        )
                        break
                    if attempt:
                        client.rotate()
                    scan = _scan_gokmg_source(
                        client,
                        source,
                        max_pages=min(
                            remaining_pages,
                            remaining_physical_requests,
                        ),
                    )
                    if (
                        scan.source_cap_reached
                        and remaining_physical_requests < remaining_pages
                    ):
                        request_budget_exhausted = True
                        scan.errors.append(
                            "physical request safety budget cannot fit the "
                            f"complete {source.code} scan "
                            f"({remaining_physical_requests} remaining, "
                            f"{scan.required_list_requests} required)"
                        )
                    scans.append(scan)
                    for identity in scan.exact_duplicate_ids:
                        if identity not in transient_ids:
                            transient_ids.append(identity)
                    duplicate_only = scan.errors == [
                        f"{source.code}: duplicate programme identities"
                    ]
                    if duplicate_only and attempt + 1 < GOKSEONG_SOURCE_SCAN_ATTEMPTS:
                        continue
                    break

                if not scans:
                    source_scan_attempts[source.code] = 0
                    source_transient_duplicate_ids[source.code] = transient_ids
                    break
                final_scan = scans[-1]
                source_scan_attempts[source.code] = len(scans)
                source_transient_duplicate_ids[source.code] = transient_ids
                transient_duplicate_retry_count += len(scans) - 1
                recovery_list_requests += sum(
                    scan.list_requests for scan in scans[:-1]
                )
                list_requests += final_scan.list_requests
                required_list_requests += final_scan.required_list_requests
                data_pages += final_scan.data_pages
                sentinel_pages += final_scan.sentinel_pages
                source_cap_reached = bool(
                    source_cap_reached or final_scan.source_cap_reached
                )
                if (
                    final_scan.source_cap_reached
                    and client.physical_requests >= request_safety_budget
                ):
                    request_budget_exhausted = True
                if final_scan.contract is not None:
                    contracts[source.code] = final_scan.contract
                source_rows[source.code] = final_scan.rows
                canonical_source_rows[source.code] = final_scan.canonical_rows
                source_exact_duplicate_ids[source.code] = (
                    final_scan.exact_duplicate_ids
                )
                page_counts[source.code] = final_scan.page_counts
                errors.extend(final_scan.errors)
                if errors:
                    break

        all_parents = [
            row
            for source in GOKSEONG_GOKMG_SOURCES
            for row in canonical_source_rows.get(source.code, [])
        ]
        all_parent_ids = [_clean(row.get("identity")) for row in all_parents]
        cross_source_duplicate_count = len(all_parent_ids) - len(set(all_parent_ids))
        if cross_source_duplicate_count:
            errors.append(
                f"{cross_source_duplicate_count} cross-source programme identities"
            )

        current_parents: list[dict[str, Any]] = []
        expired_parent_count = 0
        for row in all_parents:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"{row.get('identity')}: invalid parent end date")
                continue
            if end < cutoff:
                expired_parent_count += 1
            elif _clean(row.get("title")) in _GOKMG_TEST_TITLES:
                excluded_test_count += 1
            elif _is_gokmg_non_course_title(row.get("title")):
                excluded_non_course_count += 1
                excluded_non_course_parent_ids.append(_clean(row.get("identity")))
            else:
                current_parents.append(row)

        if len(current_parents) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_parents)} required current/future programme details"
            )

        if (
            not errors
            and client.physical_requests + len(current_parents)
            > request_safety_budget
        ):
            request_budget_exhausted = True
            source_cap_reached = True
            errors.append(
                "physical request safety budget allows "
                f"{request_safety_budget - client.physical_requests} of "
                f"{len(current_parents)} required detail requests after "
                f"{client.physical_requests} catalogue requests"
            )

        expanded_rows: list[dict[str, Any]] = []
        if not errors:
            for parent in current_parents:
                source = _GOKMG_SOURCE_BY_CODE[_clean(parent.get("source_code"))]
                detail_attempts += 1
                try:
                    soup = client.get(_clean(parent.get("raw_url")))
                    parsed, row_errors = _gokmg_detail_rows(source, parent, soup)
                    if row_errors:
                        detail_errors += len(row_errors)
                        errors.extend(row_errors)
                    else:
                        detail_pages += 1
                        expanded_rows.extend(parsed)
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{source.code}:{parent.get('identity')}: detail fetch "
                        f"{type(exc).__name__}"
                    )

        current_rows: list[dict[str, Any]] = []
        expired_schedule_count = 0
        for row in expanded_rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(
                    f"{row.get('provider_course_id')}: invalid expanded end date"
                )
                continue
            if end < cutoff:
                expired_schedule_count += 1
            else:
                current_rows.append(row)

        identities = [_clean(row.get("provider_course_id")) for row in current_rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate expanded course identities")

        result: list[dict[str, Any]] = []
        if not errors:
            deduper = dedupe_rows or _default_dedupe
            result = list(deduper([_clean_row(row) for row in current_rows]))
            if len(result) != len(current_rows):
                errors.append(
                    f"dedupe changed complete row count {len(current_rows)} to "
                    f"{len(result)}"
                )
                result = []
        result.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )

        source_totals = {
            code: contract[0] for code, contract in contracts.items()
        }
        source_current_parent_counts = Counter(
            _clean(row.get("source_code")) for row in current_parents
        )
        branch_counts = Counter(_clean(row.get("branch")) for row in result)
        status_counts = Counter(_clean(row.get("status")) for row in result)
        source_status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status")) for row in result
        )
        snapshot_complete = not errors
        pagination_complete = bool(
            snapshot_complete
            and list_requests == required_list_requests
            and len(source_rows) == len(GOKSEONG_GOKMG_SOURCES)
            and all(
                len(source_rows[source.code]) == contracts[source.code][0]
                and page_counts[source.code].get(contracts[source.code][2] + 1)
                == 0
                for source in GOKSEONG_GOKMG_SOURCES
            )
        )
        details_complete = bool(
            snapshot_complete
            and detail_attempts == len(current_parents)
            and detail_pages == len(current_parents)
            and detail_errors == 0
        )
        meta = {
            # ``pages`` is the logical complete catalogue snapshot consumed by
            # the generated runner's max_pages contract.  Recovery traffic is
            # reported separately and is still bounded by the explicit
            # physical request safety budget below.
            "pages": list_requests,
            "list_requests": list_requests,
            "aggregate_list_requests": (
                list_requests + recovery_list_requests
            ),
            "physical_requests": client.physical_requests,
            "request_safety_budget": request_safety_budget,
            "request_budget_remaining": max(
                0, request_safety_budget - client.physical_requests
            ),
            "request_budget_exhausted": request_budget_exhausted,
            "sessions_created": client.sessions_created,
            "source_count": len(GOKSEONG_GOKMG_SOURCES),
            "source_total": sum(source_totals.values()),
            "source_rows": sum(len(rows) for rows in source_rows.values()),
            "canonical_source_rows": len(all_parents),
            "exact_source_duplicate_count": sum(
                len(source_rows.get(source.code, []))
                - len(canonical_source_rows.get(source.code, []))
                for source in GOKSEONG_GOKMG_SOURCES
            ),
            "source_exact_duplicate_ids": source_exact_duplicate_ids,
            "source_totals": source_totals,
            "source_page_counts": page_counts,
            "data_pages": data_pages,
            "sentinel_pages": sentinel_pages,
            "required_list_requests": required_list_requests,
            "recovery_list_requests": recovery_list_requests,
            "source_scan_attempts": source_scan_attempts,
            "source_transient_duplicate_ids": source_transient_duplicate_ids,
            "expired_parent_count": expired_parent_count,
            "current_parent_count": len(current_parents),
            "excluded_test_count": excluded_test_count,
            "excluded_non_course_count": excluded_non_course_count,
            "excluded_non_course_parent_ids": excluded_non_course_parent_ids,
            "source_current_parent_counts": dict(source_current_parent_counts),
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_errors": detail_errors,
            "expanded_count": len(expanded_rows),
            "expired_schedule_count": expired_schedule_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "source_status_counts": dict(source_status_counts),
            "duplicate_count": duplicate_count,
            "cross_source_duplicate_count": cross_source_duplicate_count,
            "discovered_links": sum(len(rows) for rows in source_rows.values()),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "pagination_detected": data_pages > len(GOKSEONG_GOKMG_SOURCES),
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "transient_duplicate_retry_count": transient_duplicate_retry_count,
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "all complete Gokseong education portal programmes have ended"
                if snapshot_complete and not current_rows
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
            "legacy_aggregate_is_subset": True,
            "legacy_aggregate_audited_rows": 389,
            "canonical_union_audited_rows": 450,
            "legacy_missing_school_rows": 61,
        }
        if errors:
            return [], GOKSEONG_GOKMG_PARSER, meta
        return result, GOKSEONG_GOKMG_PARSER, meta
    finally:
        client.close()


def _jne_page_contract(source: JneSource, soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
    if GOKSEONG_JNE_BRANCH not in title:
        errors.append(f"{source.kind}: exact institution title changed")
    tables = soup.select("table.tstyle_list")
    if len(tables) != 1:
        errors.append(f"{source.kind}: expected one catalogue table")
        return errors
    expected = (
        _JNE_LECTURE_HEADERS
        if source.kind == "lecture"
        else _JNE_EDUCATION_HEADERS
    )
    if _table_headers(tables[0]) != expected:
        errors.append(f"{source.kind}: catalogue header contract changed")
    return errors


def _jne_list_rows(
    source: JneSource,
    soup: BeautifulSoup,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors = _jne_page_contract(source, soup)
    table = soup.select_one("table.tstyle_list")
    if table is None:
        return [], errors
    rows: list[dict[str, Any]] = []
    expected_cells = 7 if source.kind == "lecture" else 6
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        number = _clean(cells[0].get_text(" ", strip=True))
        if not number.isdigit():
            if (
                "no-data" in (cells[0].get("class") or [])
                or "등록된 자료가 존재하지 않습니다"
                in _clean(tr.get_text(" ", strip=True))
            ):
                continue
            errors.append(f"{source.kind}: non-numeric source sequence")
            continue
        if len(cells) != expected_cells:
            errors.append(f"{source.kind}: malformed row {number}")
            continue
        links = tr.select("a[href]")
        if len(links) != 1:
            errors.append(f"{source.kind}: row {number} has ambiguous detail link")
            continue
        identity = _jne_detail_identity(source, links[0].get("href"))
        title = _clean(cells[1].get_text(" ", strip=True))
        if source.kind == "lecture":
            target = _clean(cells[2].get_text(" ", strip=True))
            raw_period = _clean(cells[3].get_text(" ", strip=True))
            raw_apply = _clean(cells[4].get_text(" ", strip=True))
            raw_capacity = _clean(cells[5].get_text(" ", strip=True))
            source_status = _clean(cells[6].get_text(" ", strip=True))
        else:
            target = ""
            raw_apply = _clean(cells[2].get_text(" ", strip=True))
            raw_period = _clean(cells[3].get_text(" ", strip=True))
            raw_capacity = _clean(cells[4].get_text(" ", strip=True))
            source_status = _clean(cells[5].get_text(" ", strip=True))
        start, end, period = _date_range(raw_period, allow_single=True)
        apply_start, apply_end, apply_period = _date_range(
            raw_apply, allow_single=True
        )
        capacity_current, capacity_total = _capacity_pair(raw_capacity)
        capacity_current_reported = capacity_current
        if (
            capacity_current is not None
            and capacity_total is not None
            and capacity_current > capacity_total
        ):
            capacity_current = capacity_total
        wait_matches = re.findall(
            r"\(\s*([\d,]+)\s*/\s*([\d,]+)\s*\)", raw_capacity
        )
        wait_current = (
            int(wait_matches[-1][0].replace(",", "")) if wait_matches else None
        )
        wait_total = (
            int(wait_matches[-1][1].replace(",", "")) if wait_matches else None
        )
        if not identity or not title:
            errors.append(f"{source.kind}: row {number} lacks identity/title")
        if not period or not apply_period:
            errors.append(f"{source.kind}: row {number} has malformed dates")
        status = _jne_status(source_status)
        if not status:
            errors.append(f"{source.kind}: row {number} has unknown status")
        if capacity_current is None or capacity_total is None:
            errors.append(f"{source.kind}: row {number} has malformed capacity")
        rows.append(
            {
                "source_sequence": int(number),
                "identity": identity,
                "title": title,
                "target": target,
                "raw_period": raw_period,
                "period": period,
                "start_date": start,
                "end_date": end,
                "raw_apply_period": raw_apply,
                "apply_period": apply_period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "capacity_current": capacity_current,
                "capacity_current_reported": capacity_current_reported,
                "capacity_total": capacity_total,
                "waitlist_current": wait_current,
                "waitlist_total": wait_total,
                "source_status": source_status,
                "status": status,
                "raw_url": _jne_detail_url(source, identity),
            }
        )
    return rows, errors


def _detail_pairs(table: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if table is None:
        return result
    for tr in table.select("tr"):
        nodes = tr.find_all(["th", "td"], recursive=False)
        for index in range(0, len(nodes) - 1, 2):
            if nodes[index].name == "th":
                result[_clean(nodes[index].get_text(" ", strip=True))] = _clean(
                    nodes[index + 1].get_text(" ", strip=True)
                )
    return result


def _jne_detail_row(
    source: JneSource,
    parent: dict[str, Any],
    soup: BeautifulSoup,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    identity = _clean(parent.get("identity"))
    table_class = "tstyle_write" if source.kind == "lecture" else "tstyle_view"
    tables = soup.select(f"table.{table_class}")
    if len(tables) != 1:
        return {}, [f"{source.kind}:{identity}: expected one detail table"]
    pairs = _detail_pairs(tables[0])
    required = (
        _JNE_LECTURE_DETAIL_REQUIRED
        if source.kind == "lecture"
        else _JNE_EDUCATION_DETAIL_REQUIRED
    )
    if not required.issubset(pairs):
        errors.append(f"{source.kind}:{identity}: detail field contract changed")
    education_period_field = ""
    if source.kind == "education":
        education_period_fields = [
            field for field in _JNE_EDUCATION_PERIOD_FIELDS if field in pairs
        ]
        if len(education_period_fields) != 1:
            errors.append(
                f"{source.kind}:{identity}: detail period field contract changed"
            )
        elif education_period_fields:
            education_period_field = education_period_fields[0]
    if _normalized(pairs.get("강좌명")) != _normalized(parent.get("title")):
        errors.append(f"{source.kind}:{identity}: detail/list title mismatch")

    if source.kind == "lecture":
        detail_start, detail_end, detail_period = _date_range(pairs.get("운영기간"))
        apply_start, apply_end, apply_period = _date_range(pairs.get("신청기간"))
        detail_status = _clean(pairs.get("접수상태"))
        target = _clean(pairs.get("대상")) or _clean(parent.get("target"))
        schedule = " ".join(
            value
            for value in (
                _clean(pairs.get("강의 요일")),
                _clean(pairs.get("강의 시간")),
            )
            if value
        )
        raw_capacity = pairs.get("모집인원")
        description = _clean(pairs.get("비고")) or _clean(parent.get("title"))
        instructor = ""
    else:
        detail_start, detail_end, detail_period = _date_range(
            pairs.get(education_period_field), allow_single=True
        )
        apply_start, apply_end, apply_period = _date_range(
            pairs.get("인터넷 접수기간"), allow_single=True
        )
        detail_status = _clean(pairs.get("비고"))
        target = _clean(pairs.get("대상"))
        schedule = " ".join(
            value
            for value in (
                _clean(pairs.get("수강요일")),
                _clean(pairs.get("수강시간")),
            )
            if value
        )
        raw_capacity = pairs.get("수강인원")
        description = _clean(pairs.get("내용")) or _clean(parent.get("title"))
        instructor = _clean(pairs.get("강사명"))

    if (
        detail_start != _clean(parent.get("start_date"))
        or detail_end != _clean(parent.get("end_date"))
        or detail_period != _clean(parent.get("period"))
    ):
        errors.append(f"{source.kind}:{identity}: detail/list period mismatch")
    if (
        apply_start != _clean(parent.get("apply_start"))
        or apply_end != _clean(parent.get("apply_end"))
        or apply_period != _clean(parent.get("apply_period"))
    ):
        errors.append(f"{source.kind}:{identity}: detail/list application period mismatch")
    normalized_detail_status = _jne_status(detail_status)
    if not normalized_detail_status:
        errors.append(f"{source.kind}:{identity}: unknown detail status")
    elif normalized_detail_status != _clean(parent.get("status")):
        errors.append(f"{source.kind}:{identity}: detail/list status mismatch")

    capacity_total = _integer(raw_capacity.split("(", 1)[0])
    if capacity_total != parent.get("capacity_total"):
        errors.append(f"{source.kind}:{identity}: detail/list capacity mismatch")
    application_candidates: list[str] = []
    for link in soup.select("a[href]"):
        candidate = _jne_application_url(source, identity, link.get("href"))
        if candidate:
            application_candidates.append(candidate)
    application_candidates = list(dict.fromkeys(application_candidates))
    if len(application_candidates) > 1:
        errors.append(f"{source.kind}:{identity}: ambiguous application links")
    application_url = application_candidates[0] if application_candidates else ""
    if (normalized_detail_status == "OPEN") != bool(application_url):
        errors.append(f"{source.kind}:{identity}: status/application mismatch")

    row: dict[str, Any] = {
        "provider": source.provider,
        "provider_course_id": f"{source.provider}:course:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(parent.get("title")),
        "branch": GOKSEONG_JNE_BRANCH,
        "provider_organizer": GOKSEONG_JNE_BRANCH,
        "category": source.category,
        "raw_url": _clean(parent.get("raw_url")),
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if application_url else "",
        "reservation_available": bool(application_url),
        "status": normalized_detail_status,
        "period": detail_period,
        "start_date": detail_start,
        "end_date": detail_end,
        "apply_period": apply_period,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule_raw": schedule or _clean(parent.get("raw_period")),
        "target": target,
        "fee": "무료",
        "capacity": parent.get("capacity_total"),
        "capacity_current": parent.get("capacity_current"),
        "capacity_total": parent.get("capacity_total"),
        "waitlist_current": parent.get("waitlist_current"),
        "waitlist_total": parent.get("waitlist_total"),
        "venue_name": _clean(pairs.get("교육장소")),
        "instructor": instructor,
        "description": description,
        "collection_type": "complete_html_pages+detail",
        "raw_fields": {
            "parser": source.parser,
            "source_kind": source.kind,
            "source_sequence": parent.get("source_sequence"),
            "source_identity": identity,
            "source_status": detail_status,
            "list_source_status": parent.get("source_status"),
            "capacity_current_reported": parent.get("capacity_current_reported"),
            "detail_pairs": pairs,
        },
        **_base_course_fields(),
    }
    return _clean_row(row), errors


def collect_gokseong_jne_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 10,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future JNE catalogue snapshot."""

    source = _JNE_SOURCE_BY_PROVIDER.get(_provider(target))
    parser = source.parser if source is not None else GOKSEONG_JNE_LECTURE_PARSER
    if source is None or not _is_jne_target(target, source):
        return [], parser, _failure(
            "target does not match a canonical Gokseong JNE education route",
            source_count=1,
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], parser, _failure(
                "managed session_factory injection is required", source_count=1
            )
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        cutoff = _today(today)
    except (TypeError, ValueError):
        return [], parser, _failure(
            "max_pages/detail_limit/today are invalid", source_count=1
        )

    client = _SoupClient(
        session_factory=session_factory, fetcher=fetcher, timeout=timeout
    )
    errors: list[str] = []
    source_cap_reached = False
    rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    first_soup: Optional[BeautifulSoup] = None
    source_total = 0
    data_pages = 0
    required_list_requests = 0
    sentinel_empty = False

    try:
        if allowed_pages < 1:
            source_cap_reached = True
            errors.append("max_pages cap does not allow the first catalogue request")
        if not errors:
            try:
                first_soup = client.get(jne_list_url(source.provider))
            except Exception as exc:
                errors.append(f"first page fetch {type(exc).__name__}")
        first_rows: list[dict[str, Any]] = []
        if first_soup is not None:
            first_rows, first_errors = _jne_list_rows(source, first_soup)
            errors.extend(first_errors)
            if first_rows:
                source_total = int(first_rows[0]["source_sequence"])
            elif "등록된 자료가 존재하지 않습니다" in _clean(
                first_soup.get_text(" ", strip=True)
            ):
                source_total = 0
            else:
                errors.append("first page lacks rows and explicit empty marker")
            data_pages = max(1, math.ceil(source_total / source.page_size))
            required_list_requests = data_pages + 1
        if not errors and required_list_requests > allowed_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of "
                f"{required_list_requests} required catalogue/sentinel requests"
            )

        if not errors:
            for page in range(1, data_pages + 1):
                try:
                    soup = (
                        first_soup
                        if page == 1
                        else client.get(jne_list_url(source.provider, page))
                    )
                except Exception as exc:
                    errors.append(f"page {page} fetch {type(exc).__name__}")
                    break
                parsed, page_errors = (
                    (first_rows, [])
                    if page == 1
                    else _jne_list_rows(source, soup)
                )
                errors.extend(page_errors)
                expected = (
                    min(
                        source.page_size,
                        source_total - (page - 1) * source.page_size,
                    )
                    if source_total
                    else 0
                )
                if len(parsed) != expected:
                    errors.append(
                        f"page {page} expected {expected} rows, got {len(parsed)}"
                    )
                page_counts[page] = len(parsed)
                rows.extend(parsed)
            if not errors:
                try:
                    sentinel_soup = client.get(
                        jne_list_url(source.provider, data_pages + 1)
                    )
                    sentinel_rows, sentinel_errors = _jne_list_rows(
                        source, sentinel_soup
                    )
                    errors.extend(sentinel_errors)
                    if sentinel_rows:
                        errors.append("immediate sentinel page is not empty")
                    sentinel_empty = not sentinel_rows
                    page_counts[data_pages + 1] = len(sentinel_rows)
                except Exception as exc:
                    errors.append(f"sentinel fetch {type(exc).__name__}")

        numbers = [int(row["source_sequence"]) for row in rows]
        identities = [_clean(row.get("identity")) for row in rows]
        duplicate_count = len(identities) - len(set(identities))
        if numbers != list(range(source_total, 0, -1)):
            errors.append("source numbering is not continuous")
        if len(rows) != source_total:
            errors.append(
                f"declared total {source_total} != parsed rows {len(rows)}"
            )
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate source identities")

        current_parents: list[dict[str, Any]] = []
        expired_count = 0
        for row in rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"{row.get('identity')}: invalid end date")
                continue
            if end < cutoff:
                expired_count += 1
            else:
                current_parents.append(row)
        if len(current_parents) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_parents)} required current/future details"
            )

        detailed_rows: list[dict[str, Any]] = []
        if not errors:
            for parent in current_parents:
                detail_attempts += 1
                try:
                    soup = client.get(_clean(parent.get("raw_url")))
                    row, row_errors = _jne_detail_row(source, parent, soup)
                    if row_errors:
                        detail_errors += len(row_errors)
                        errors.extend(row_errors)
                    else:
                        detail_pages += 1
                        detailed_rows.append(row)
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{parent.get('identity')}: detail fetch {type(exc).__name__}"
                    )

        result: list[dict[str, Any]] = []
        if not errors:
            deduper = dedupe_rows or _default_dedupe
            result = list(deduper([_clean_row(row) for row in detailed_rows]))
            if len(result) != len(detailed_rows):
                errors.append(
                    f"dedupe changed complete row count {len(detailed_rows)} to "
                    f"{len(result)}"
                )
                result = []
        result.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )

        status_counts = Counter(_clean(row.get("status")) for row in result)
        source_status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status")) for row in result
        )
        snapshot_complete = not errors
        pagination_complete = bool(
            snapshot_complete
            and client.physical_requests >= required_list_requests
            and sentinel_empty
            and len(rows) == source_total
        )
        details_complete = bool(
            snapshot_complete
            and detail_attempts == len(current_parents)
            and detail_pages == len(current_parents)
            and detail_errors == 0
        )
        meta = {
            "pages": client.physical_requests,
            "list_requests": required_list_requests if first_soup is not None else 0,
            "physical_requests": client.physical_requests,
            "sessions_created": client.sessions_created,
            "source_count": 1,
            "source_total": source_total,
            "source_rows": len(rows),
            "data_pages": data_pages,
            "required_list_requests": required_list_requests,
            "sentinel_page": data_pages + 1 if data_pages else 0,
            "page_counts": page_counts,
            "expired_count": expired_count,
            "current_count": len(current_parents),
            "returned_count": len(result),
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_errors": detail_errors,
            "status_counts": dict(status_counts),
            "source_status_counts": dict(source_status_counts),
            "duplicate_count": duplicate_count,
            "branch_count": 1 if result else 0,
            "branch_counts": {GOKSEONG_JNE_BRANCH: len(result)} if result else {},
            "discovered_links": len(rows),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "pagination_detected": data_pages > 1,
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not current_parents),
            "no_current_reason": (
                f"all complete Gokseong JNE {source.kind} catalogue rows have ended"
                if snapshot_complete and not current_parents
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
            "exact_branch_name": GOKSEONG_JNE_BRANCH,
        }
        if errors:
            return [], parser, meta
        return result, parser, meta
    finally:
        client.close()


def collect_gokseong_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 60,
    detail_limit: int = 100,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Dispatch an exact canonical Gokseong education target."""

    if is_gokseong_gokmg_target(target):
        return collect_gokseong_gokmg_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    return collect_gokseong_jne_courses(
        target,
        timeout=timeout,
        max_pages=max_pages,
        detail_limit=detail_limit,
        **kwargs,
    )


collect = collect_gokseong_education_courses


__all__ = [
    "GOKSEONG_CANDIDATE_IDS",
    "GOKSEONG_DISCOVERY_SHELL_URLS",
    "GOKSEONG_GOKMG_BRANCH",
    "GOKSEONG_GOKMG_HOST",
    "GOKSEONG_GOKMG_LEGACY_AGGREGATE_URL",
    "GOKSEONG_GOKMG_LEGACY_DETAIL_URL",
    "GOKSEONG_GOKMG_MENU_ALIAS_URLS",
    "GOKSEONG_GOKMG_PARSER",
    "GOKSEONG_GOKMG_PROVIDER",
    "GOKSEONG_GOKMG_SOURCES",
    "GOKSEONG_GOKMG_SPACE_RESERVATION_URL",
    "GOKSEONG_GOKMG_URL",
    "GOKSEONG_JNE_BRANCH",
    "GOKSEONG_JNE_EDUCATION_PARSER",
    "GOKSEONG_JNE_EDUCATION_PROVIDER",
    "GOKSEONG_JNE_EDUCATION_URL",
    "GOKSEONG_JNE_LECTURE_PARSER",
    "GOKSEONG_JNE_LECTURE_PROVIDER",
    "GOKSEONG_JNE_LECTURE_URL",
    "GOKSEONG_MUNICIPALITY_CODE",
    "GOKSEONG_MUNICIPALITY_NAME",
    "GOKSEONG_RETIRED_MENU_URLS",
    "collect",
    "collect_gokseong_education_courses",
    "collect_gokseong_gokmg_courses",
    "collect_gokseong_jne_courses",
    "gokmg_detail_url",
    "gokmg_page_form",
    "gokmg_list_url",
    "is_gokseong_education_target",
    "is_gokseong_gokmg_target",
    "is_target",
    "jne_list_url",
]
