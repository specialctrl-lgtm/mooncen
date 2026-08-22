"""Fail-closed collector for Anyang City's official education catalogues.

Anyang publishes education records through two non-overlapping official
catalogues:

* ``learning.anyang.go.kr/ay_network/Lecture_Search/list.asp`` aggregates the
  Manan/Dongan lifelong-learning centres and senior welfare centres.  Its
  ``MM`` and ``DD`` region partitions exactly cover the unfiltered catalogue.
* ``www.anyang.go.kr/reserve/selectEduLctreWebList.do`` owns the remaining
  municipal education records (city hall, job centre, baby-boomer support
  centre, and the architecture festival).  Its institution filters exactly
  partition that catalogue.

The two sites use the same municipal identity but different course identity
spaces, so this module collects both under one provider.  Every advertised
list page, a post-boundary empty sentinel, each official partition, a page-one
recheck, and every current/future detail are required.  An incomplete or
changed contract returns no rows.

Both official hosts currently require OpenSSL's legacy-server-connect option.
The session factory below only relaxes protocol interoperability: certificate
and hostname verification remain enabled.  TLS bypasses and HTTP downgrade are
deliberately never used.

Facility rental and experience/reservation menus are outside this module.
Free-form descriptions, instructor names, applicant tables, and staff contact
details are deliberately neither returned nor retained in ``raw_fields``.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import ssl
from threading import Lock, local
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


ANYANG_PROVIDER = "ANYANG_LIFELONG_LEARNING"
ANYANG_CANONICAL_URL = (
    "https://learning.anyang.go.kr/ay_network/Lecture_Search/list.asp"
)
ANYANG_CANONICAL_CANDIDATE_ID = "MUNI_IR_E0B9D195B82A"
ANYANG_LEARNING_HOST = "learning.anyang.go.kr"
ANYANG_LEARNING_PATH = "/ay_network/Lecture_Search/list.asp"
ANYANG_LEARNING_PAGE_SIZE = 10

ANYANG_RESERVE_HOST = "www.anyang.go.kr"
ANYANG_RESERVE_PATH = "/reserve/selectEduLctreWebList.do"
ANYANG_RESERVE_DETAIL_PATH = "/reserve/eduLctreWebView.do"
ANYANG_RESERVE_APPLICATION_PATH = "/reserve/selectEduApplcntAgreView.do"
ANYANG_RESERVE_KEY = "1376"
ANYANG_RESERVE_PAGE_SIZE = 100
ANYANG_RESERVE_URL = (
    "https://www.anyang.go.kr/reserve/selectEduLctreWebList.do"
    "?key=1376&searchDiv=1&searchUseAt=Y&searchEmdAt=N"
)
ANYANG_RESERVE_CANDIDATE_ID = "MUNI_IR_C5A982BBF1B6"

ANYANG_CITY_CODE = "4117000000"
ANYANG_MANAN_CODE = "4117100000"
ANYANG_DONGAN_CODE = "4117300000"
ANYANG_MUNICIPALITY_NAMES = {
    ANYANG_CITY_CODE: "경기도 안양시",
    ANYANG_MANAN_CODE: "경기도 안양시 만안구",
    ANYANG_DONGAN_CODE: "경기도 안양시 동안구",
}
ANYANG_COVERED_MUNICIPALITIES: tuple[dict[str, str], ...] = (
    {
        "code": ANYANG_CITY_CODE,
        "sido": "경기도",
        "sigungu": "안양시",
        "full_name": ANYANG_MUNICIPALITY_NAMES[ANYANG_CITY_CODE],
    },
    {
        "code": ANYANG_MANAN_CODE,
        "sido": "경기도",
        "sigungu": "안양시 만안구",
        "full_name": ANYANG_MUNICIPALITY_NAMES[ANYANG_MANAN_CODE],
    },
    {
        "code": ANYANG_DONGAN_CODE,
        "sido": "경기도",
        "sigungu": "안양시 동안구",
        "full_name": ANYANG_MUNICIPALITY_NAMES[ANYANG_DONGAN_CODE],
    },
)

ANYANG_MAX_WORKERS = 8
ANYANG_FETCH_ATTEMPTS = 3
ANYANG_PARSER = (
    "anyang_learning_complete_pages+MM_DD_partitions+reserve_complete_pages+"
    "institution_partitions+sentinels+page1_rechecks+current_details"
)


@dataclass(frozen=True)
class AnyangLearningBranch:
    name: str
    code: str
    detail_prefix: str
    municipality_code: str
    detail_names: tuple[str, ...]


ANYANG_LEARNING_BRANCHES: tuple[AnyangLearningBranch, ...] = (
    AnyangLearningBranch(
        "만안평생학습센터",
        "MW",
        "/MW/edu_guide/MW_Receipt/",
        ANYANG_MANAN_CODE,
        ("만안평생학습센터",),
    ),
    AnyangLearningBranch(
        "만안노인복지회관",
        "MS",
        "/MS/edu_guide/MWS_Receipt/",
        ANYANG_MANAN_CODE,
        ("만안노인복지회관", "만안노인회관"),
    ),
    AnyangLearningBranch(
        "동안평생학습센터",
        "DW",
        "/DW/edu_guide/DW_Receipt/",
        ANYANG_DONGAN_CODE,
        ("동안평생학습센터",),
    ),
    AnyangLearningBranch(
        "동안노인복지회관",
        "DS",
        "/DS/edu_guide/DWS_Receipt/",
        ANYANG_DONGAN_CODE,
        ("동안노인복지회관", "동안노인회관"),
    ),
)
_LEARNING_BRANCH_BY_NAME = {
    item.name: item for item in ANYANG_LEARNING_BRANCHES
}
_LEARNING_BRANCH_BY_CODE = {
    item.code: item for item in ANYANG_LEARNING_BRANCHES
}


@dataclass(frozen=True)
class AnyangReserveInstitution:
    code: str
    name: str
    municipality_code: str


# This is the complete institution menu advertised by the education list.
# The institution partition is also used as the authoritative district
# ownership signal; venue strings alone are too inconsistent for that role.
ANYANG_RESERVE_INSTITUTIONS: tuple[AnyangReserveInstitution, ...] = (
    AnyangReserveInstitution("2", "안양시청", ANYANG_DONGAN_CODE),
    AnyangReserveInstitution("55", "안양시 사회적경제지원센터", ANYANG_DONGAN_CODE),
    AnyangReserveInstitution("57", "일자리센터", ANYANG_DONGAN_CODE),
    AnyangReserveInstitution("50", "베이비부머 지원센터", ANYANG_DONGAN_CODE),
    AnyangReserveInstitution("51", "만안노인복지회관", ANYANG_MANAN_CODE),
    AnyangReserveInstitution("53", "동안노인복지회관", ANYANG_DONGAN_CODE),
    AnyangReserveInstitution("54", "안양문화예술재단", ANYANG_DONGAN_CODE),
    AnyangReserveInstitution("1", "범계역 청년출구", ANYANG_DONGAN_CODE),
    AnyangReserveInstitution("49", "안양도시공사", ANYANG_DONGAN_CODE),
    AnyangReserveInstitution("67", "안양건축문화제", ANYANG_MANAN_CODE),
    AnyangReserveInstitution("44", "만안평생학습센터", ANYANG_MANAN_CODE),
    AnyangReserveInstitution("45", "동안평생학습센터", ANYANG_DONGAN_CODE),
)
_RESERVE_INSTITUTION_BY_CODE = {
    item.code: item for item in ANYANG_RESERVE_INSTITUTIONS
}
_RESERVE_INSTITUTION_OPTIONS = (
    ("", "전체"),
    *((item.code, item.name) for item in ANYANG_RESERVE_INSTITUTIONS),
)


@dataclass(frozen=True)
class AnyangAlias:
    provider: str
    candidate_id: str
    url: str
    ownership: str
    scope: str


# These discoveries must never execute beside the canonical provider.  The
# branch receipt lists overlap their matching rows in the citywide search and
# may additionally expose cancelled/closed records which are out of scope.
ANYANG_NON_EXECUTING_ALIASES: tuple[AnyangAlias, ...] = (
    AnyangAlias(
        "MUNI_LEARNING_ANYANG_GO_KR_1038BD6F",
        "MUNI_IR_0E72B76B3CFF",
        "https://learning.anyang.go.kr/",
        "complete_duplicate_shell",
        "navigation root routed by the legacy collector to the canonical search",
    ),
    AnyangAlias(
        "MUNI_LEARNING_ANYANG_GO_KR_B549E6DB",
        "MUNI_IR_3D3FCBCBF97C",
        "https://learning.anyang.go.kr/DW/edu_guide/DW_Receipt/list.asp",
        "overlapping_branch_subset",
        "동안평생학습센터 branch; cancelled records are not canonical",
    ),
    AnyangAlias(
        "MUNI_LEARNING_ANYANG_GO_KR_6C3182FE",
        "MUNI_IR_864F2455850F",
        "https://learning.anyang.go.kr/DW/edu_guide/online.asp",
        "excluded_guide",
        "동안평생학습센터 application instructions, not a catalogue",
    ),
    AnyangAlias(
        "MUNI_LEARNING_ANYANG_GO_KR_5CE8372E",
        "MUNI_IR_77028643DB26",
        "https://learning.anyang.go.kr/DW/front.asp",
        "excluded_shell",
        "동안평생학습센터 navigation shell",
    ),
    AnyangAlias(
        "MUNI_LEARNING_ANYANG_GO_KR_5D1C2464",
        "MUNI_IR_ED46226E08F5",
        "https://learning.anyang.go.kr/MW/",
        "excluded_shell",
        "만안평생학습센터 navigation shell",
    ),
    AnyangAlias(
        "MUNI_LEARNING_ANYANG_GO_KR_97B9BE64",
        "MUNI_IR_343D44B219ED",
        "https://learning.anyang.go.kr/MW/edu_guide/MW_Receipt/list.asp",
        "overlapping_branch_subset",
        "만안평생학습센터 branch; cancelled records are not canonical",
    ),
)

ANYANG_EXCLUDED_RESERVATION_PATH_PREFIXES = (
    "/reserve/reservWeb",
    "/reserve/reservCalendar",
    "/reserve/selectResveWeb",
)

ANYANG_RAW_FIELD_ALLOWLIST = frozenset(
    {
        "parser",
        "source_kind",
        "source_sequence",
        "source_identity",
        "source_status",
        "source_selection_method",
        "source_method",
        "source_branch_code",
        "source_institution_code",
        "list_institution",
        "list_venue",
        "list_capacity",
        "list_apply_period",
        "municipality_evidence",
        "application_control",
        "official_period_anomaly",
    }
)

_LEARNING_HEADERS = (
    "번호",
    "기관",
    "강좌명",
    "접수기간",
    "교육기간",
    "교육시간",
    "상태",
)
_LEARNING_REGION_OPTIONS = (("", "지역"), ("MM", "만안구"), ("DD", "동안구"))
_LEARNING_TARGET_OPTIONS = (("", "대상"), ("W", "일반"), ("S", "노인"))
_LEARNING_CATEGORY_OPTIONS = (
    ("", "분류"),
    ("A", "직업능력"),
    ("B", "문화예술"),
    ("C", "인문교양"),
    ("D", "시민참여(야간)"),
    ("E", "특강"),
)
_LEARNING_STATUS_MAP = {
    "접수예정": "SCHEDULED",
    "접수기간": "OPEN",
    "접수마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
}

_RESERVE_HEADERS = (
    "No.",
    "프로그램명",
    "강좌명교육기관/장소",
    "운영기간/교육시간",
    "모집인원/신청기간/접수인원",
    "모집방법",
    "모집상태",
    "상세보기",
)
_RESERVE_STATUS_MAP = {
    "모집대기": "SCHEDULED",
    "모집중": "OPEN",
    "대기자모집": "OPEN",
    "모집마감": "CLOSED",
    "교육대기중": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
    "취소": "CLOSED",
    "추첨 완료": "CLOSED",
    "추첨 접수중": "OPEN",
}

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
_LEARNING_LAST_RE = re.compile(r"[?&]page=(\d+)", re.IGNORECASE)
_FUNCTIONAL_TEST_TITLE_RE = re.compile(
    r"[\(（]\s*(?:(?:추첨|선착순)\s*)?테스트\s*[\)）]\s*$"
)
_TARGET_IN_DESCRIPTION_RE = re.compile(
    r"(?:교육|신청|모집)\s*대상\s*[:：]\s*([^□○●■▪※]+?)"
    r"(?=\s*[□○●■▪※]\s*|\s+(?:모집|신청|교육|운영)"
    r"(?:기간|장소|방법|내용)\s*[:：]|$)"
)
_RESERVE_COUNTER_RE = re.compile(
    r"총\s*([\d,]+)\s*건\s*\[\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\]"
)
_RESERVE_CAPACITY_RE = re.compile(
    r"총\s*모집인원\s*:\s*([\d,]+)명.*?"
    r"인터넷\s*접수인원\s*:\s*([\d,]+)명.*?"
    r"방문\s*접수인원\s*:\s*([\d,]+)명",
    re.DOTALL,
)
_DETAIL_CAPACITY_RE = re.compile(
    r"정원\s*:\s*([\d,]+)명\s*/\s*([\d,]+)명\s*/\s*"
    r"대기자\s*정원\s*:\s*([\d,]+)명\s*/\s*([\d,]+)명"
)


class AnyangContractError(ValueError):
    """The official Anyang sources no longer match the audited contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError as exc:
            raise AnyangContractError(f"invalid date in {value}") from exc
    return result


def _date_range(value: Any, *, allow_historic_missing: bool = False) -> tuple[str, str, str]:
    values = _dates(value)
    if len(values) != 2:
        if allow_historic_missing and not values:
            return "", "", _clean(value)
        raise AnyangContractError(f"expected one date range in {value}")
    if values[1] < values[0]:
        raise AnyangContractError(f"reversed date range in {value}")
    return values[0].isoformat(), values[1].isoformat(), (
        f"{values[0].isoformat()} ~ {values[1].isoformat()}"
    )


def _learning_education_range(
    value: Any, reference_day: date
) -> tuple[str, str, str, str]:
    """Accept only source anomalies whose displayed dates prove expiry.

    Two historic records currently display ``2022.09.19 ~ 2019.09.30``.
    Dropping either record would break the source's continuous numbering, but
    treating it as current would be unsafe.  When *both* displayed dates are
    before the reference year, the later displayed date is a conservative end
    bound and the anomaly can safely remain in the historic completeness scan.
    """

    values = _dates(value)
    if len(values) != 2:
        raise AnyangContractError(f"expected one date range in {value}")
    if values[1] >= values[0]:
        return (
            values[0].isoformat(),
            values[1].isoformat(),
            f"{values[0].isoformat()} ~ {values[1].isoformat()}",
            "",
        )
    if max(values).year >= reference_day.year:
        raise AnyangContractError(f"reversed current-year date range in {value}")
    conservative_start, conservative_end = min(values), max(values)
    return (
        conservative_start.isoformat(),
        conservative_end.isoformat(),
        f"{conservative_start.isoformat()} ~ {conservative_end.isoformat()}",
        f"historic_reversed_source_range:{_clean(value)}",
    )


def _learning_application_range(
    value: Any, *, education_end: str, reference_day: date
) -> tuple[str, str, str, str]:
    values = _dates(value)
    education_is_historic = (
        bool(education_end)
        and date.fromisoformat(education_end).year < reference_day.year
    )
    if len(values) == 2 and values[1] >= values[0]:
        return (
            values[0].isoformat(),
            values[1].isoformat(),
            f"{values[0].isoformat()} ~ {values[1].isoformat()}",
            "",
        )
    if (
        len(values) == 2
        and education_is_historic
        and max(values).year < reference_day.year
    ):
        conservative_start, conservative_end = min(values), max(values)
        return (
            conservative_start.isoformat(),
            conservative_end.isoformat(),
            f"{conservative_start.isoformat()} ~ {conservative_end.isoformat()}",
            f"historic_reversed_application_range:{_clean(value)}",
        )
    if not values and education_is_historic:
        return "", "", _clean(value), "historic_missing_application_range"
    raise AnyangContractError(f"invalid current/recent application range in {value}")


def _branch_code(source: str, branch: str) -> str:
    digest = hashlib.sha1(
        f"{ANYANG_PROVIDER}|{source}|{_clean(branch)}".encode("utf-8")
    ).hexdigest()[:12]
    return f"ANYANG_BRANCH_{digest}"[:50]


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = _clean(row.get("provider_course_id"))
        if key and key not in seen:
            seen.add(key)
            result.append(row)
    return result


def is_anyang_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != ANYANG_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == ANYANG_LEARNING_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ANYANG_LEARNING_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_anyang_target


def anyang_learning_list_url(page: int = 1, region: str = "") -> str:
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if region not in {"", "MM", "DD"}:
        raise ValueError("unknown Anyang learning region")
    if page == 1 and not region:
        return ANYANG_CANONICAL_URL
    values = (
        ("Page", page),
        ("s0", ""),
        ("s1", region),
        ("s2", ""),
        ("s3", ""),
        ("st", ""),
    )
    return f"{ANYANG_CANONICAL_URL}?{urlencode(values)}"


def anyang_reserve_list_url(
    page: int = 1, institution_code: str = ""
) -> str:
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if institution_code and institution_code not in _RESERVE_INSTITUTION_BY_CODE:
        raise ValueError("unknown Anyang reserve institution")
    values: list[tuple[str, Any]] = [
        ("pageUnit", ANYANG_RESERVE_PAGE_SIZE),
        ("pageIndex", page),
        ("searchCnd", "all"),
        ("key", ANYANG_RESERVE_KEY),
        ("searchDiv", "1"),
        ("searchUseAt", "Y"),
        ("searchEmdAt", "N"),
    ]
    if institution_code:
        values.append(("searchInsttNo", institution_code))
    return f"https://{ANYANG_RESERVE_HOST}{ANYANG_RESERVE_PATH}?{urlencode(values)}"


def anyang_reserve_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not re.fullmatch(r"[1-9]\d{0,11}", value):
        raise ValueError("invalid Anyang reserve identity")
    return (
        f"https://{ANYANG_RESERVE_HOST}{ANYANG_RESERVE_DETAIL_PATH}?"
        + urlencode((("key", ANYANG_RESERVE_KEY), ("eduLctreNo", value)))
    )


class _AnyangLegacyTLSAdapter(HTTPAdapter):
    """Strict-verification adapter compatible with the two legacy TLS hosts."""

    @staticmethod
    def context() -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.set_ciphers("DEFAULT@SECLEVEL=1")
        context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        return context

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self.context()
        super().init_poolmanager(*args, **kwargs)


def anyang_session_factory() -> requests.Session:
    """Return a CA-validating session that can negotiate Anyang's legacy TLS."""

    current = requests.Session()
    current.mount("https://", _AnyangLegacyTLSAdapter())
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": ANYANG_CANONICAL_URL,
        }
    )
    return current


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (bytes, bytearray, str)):
        if not value:
            raise AnyangContractError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = getattr(value, "status_code", None)
    if status is not None and int(status) != 200:
        raise AnyangContractError(f"unexpected HTTP status {status}")
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise AnyangContractError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _fetch(
    fetcher: Optional[Fetcher],
    current_session: Any,
    url: str,
    timeout: int,
    *,
    allow_redirects: bool,
) -> BeautifulSoup:
    if not url:
        raise AnyangContractError("empty fetch URL")
    if fetcher is None:
        value: Any = current_session.get(
            url, timeout=timeout, allow_redirects=allow_redirects
        )
    else:
        value = fetcher(current_session, url, timeout)
    return _coerce_soup(value)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _ThreadSessions:
    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory
        self._local = local()
        self._lock = Lock()
        self._sessions: list[Any] = []

    def get(self) -> Any:
        current = getattr(self._local, "session", None)
        if current is None:
            current = self._factory()
            self._local.session = current
            with self._lock:
                self._sessions.append(current)
        return current

    def close(self) -> None:
        for current in reversed(self._sessions):
            _close_quietly(current)


def _parallel_fetch(
    items: list[tuple[Any, str, bool]],
    *,
    fetcher: Optional[Fetcher],
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, BeautifulSoup], list[str]]:
    fetched: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    sessions = _ThreadSessions(session_factory)

    def one(item: tuple[Any, str, bool]) -> tuple[Any, Optional[BeautifulSoup], str]:
        key, url, redirects = item
        last_error = ""
        for _attempt in range(ANYANG_FETCH_ATTEMPTS):
            try:
                return (
                    key,
                    _fetch(
                        fetcher,
                        sessions.get(),
                        url,
                        timeout,
                        allow_redirects=redirects,
                    ),
                    "",
                )
            except Exception as exc:
                last_error = type(exc).__name__
        return key, None, f"{key}: fetch {last_error}"

    try:
        workers = min(max(1, max_workers), ANYANG_MAX_WORKERS, max(1, len(items)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="anyang") as pool:
            for key, soup, error in pool.map(one, items):
                if soup is not None:
                    fetched[key] = soup
                if error:
                    errors.append(error)
    finally:
        sessions.close()
    return fetched, errors


def _options(select: Any) -> tuple[tuple[str, str], ...]:
    if select is None:
        return ()
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in select.select("option")
    )


def _table_headers(table: Any) -> tuple[str, ...]:
    return tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("tr:first-child th"))


def _learning_contract(
    soup: BeautifulSoup, *, expected_total: Optional[int] = None
) -> tuple[int, int]:
    form = soup.select_one('form[name="frmSearch"]')
    if form is None:
        raise AnyangContractError("learning search form is absent")
    if _options(form.select_one('select[name="s1"]')) != _LEARNING_REGION_OPTIONS:
        raise AnyangContractError("learning region options changed")
    if _options(form.select_one('select[name="s2"]')) != _LEARNING_TARGET_OPTIONS:
        raise AnyangContractError("learning target options changed")
    if _options(form.select_one('select[name="s3"]')) != _LEARNING_CATEGORY_OPTIONS:
        raise AnyangContractError("learning category options changed")
    tables = soup.select("table")
    if len(tables) != 1 or _table_headers(tables[0]) != _LEARNING_HEADERS:
        raise AnyangContractError("learning table/header contract changed")
    rows = [
        tr
        for tr in tables[0].select("tr")
        if _clean((tr.find("td") or {}).get_text(" ", strip=True) if tr.find("td") else "").isdigit()
    ]
    first_sequence = (
        int(_clean(rows[0].find("td").get_text(" ", strip=True))) if rows else 0
    )
    # The source has no separate total marker.  On page one the descending
    # display sequence is the total; later pages (and the empty sentinel) must
    # be checked against the already-audited page-one total.
    total = first_sequence if expected_total is None else expected_total
    last_values: list[int] = []
    for link in soup.select("a[href]"):
        match = _LEARNING_LAST_RE.search(_clean(link.get("href")))
        if match:
            last_values.append(int(match.group(1)))
    advertised_last = max(last_values or [1])
    expected_last = max(1, math.ceil(total / ANYANG_LEARNING_PAGE_SIZE))
    if advertised_last != expected_last:
        raise AnyangContractError(
            f"learning last page {advertised_last} != expected {expected_last}"
        )
    return total, advertised_last


def _learning_identity(href: Any, branch: AnyangLearningBranch) -> str:
    parsed = urlparse(urljoin(ANYANG_CANONICAL_URL, _clean(href)))
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != ANYANG_LEARNING_HOST
        or not parsed.path.lower().startswith(branch.detail_prefix.lower())
        or not parsed.path.lower().endswith("/viewok.asp")
    ):
        return ""
    query = parse_qs(parsed.query)
    values = query.get("NUM") or query.get("num") or []
    return values[0] if len(values) == 1 and re.fullmatch(r"[1-9]\d{0,11}", values[0]) else ""


def _canonical_learning_detail_url(
    branch: AnyangLearningBranch, identity: str
) -> str:
    return (
        f"https://{ANYANG_LEARNING_HOST}{branch.detail_prefix}viewOk.asp?"
        + urlencode({"NUM": identity})
    )


def _learning_rows(
    soup: BeautifulSoup,
    *,
    page: int,
    reference_day: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    table = soup.select_one("table")
    if table is None:
        return [], [f"learning page {page}: table absent"]
    for tr in table.select("tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        sequence_text = _clean(cells[0].get_text(" ", strip=True))
        if not sequence_text.isdigit():
            continue
        sequence = int(sequence_text)
        if len(cells) != len(_LEARNING_HEADERS):
            errors.append(f"learning page {page} row {sequence}: expected seven cells")
            continue
        branch_name = _clean(cells[1].get_text(" ", strip=True))
        branch = _LEARNING_BRANCH_BY_NAME.get(branch_name)
        links = cells[2].select("a[href]")
        title = _clean(links[0].get_text(" ", strip=True)) if len(links) == 1 else ""
        identity = _learning_identity(links[0].get("href"), branch) if len(links) == 1 and branch else ""
        source_status = _clean(cells[6].get_text(" ", strip=True))
        try:
            start, end, period, period_anomaly = _learning_education_range(
                cells[4].get_text(" ", strip=True), reference_day
            )
            apply_start, apply_end, apply_period, apply_anomaly = (
                _learning_application_range(
                    cells[3].get_text(" ", strip=True),
                    education_end=end,
                    reference_day=reference_day,
                )
            )
            period_anomaly = ";".join(
                value for value in (period_anomaly, apply_anomaly) if value
            )
        except AnyangContractError as exc:
            errors.append(f"learning page {page} row {sequence}: {exc}")
            continue
        is_current = date.fromisoformat(end) >= reference_day
        status = _LEARNING_STATUS_MAP.get(source_status, "")
        if not branch or not title or not identity:
            errors.append(
                f"learning page {page} row {sequence}: branch/title/identity changed"
            )
            continue
        if is_current and not status:
            errors.append(
                f"learning page {page} row {sequence}: unknown current status {source_status}"
            )
            continue
        if is_current and not apply_period:
            errors.append(
                f"learning page {page} row {sequence}: current application range absent"
            )
            continue
        if (
            is_current
            and source_status == "접수예정"
            and date.fromisoformat(apply_start) < reference_day
        ):
            errors.append(
                f"learning page {page} row {sequence}: scheduled status is past "
                "the application start date"
            )
            continue
        detail_url = _canonical_learning_detail_url(branch, identity)
        rows.append(
            {
                "provider": ANYANG_PROVIDER,
                "provider_course_id": (
                    f"{ANYANG_PROVIDER}:learning:{branch.code}:{identity}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "category": "education",
                "program_type": "강좌",
                "raw_url": detail_url,
                "application_url": "",
                "reservation_available": False,
                "application_type": "INFORMATION_ONLY",
                "status": status,
                "period": period,
                "start_date": start,
                "end_date": end,
                "apply_period": apply_period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": _clean(cells[5].get_text(" ", strip=True)),
                "target": "",
                "fee": "",
                "capacity": None,
                "capacity_current": None,
                "capacity_total": None,
                "venue_name": branch.name,
                "venue_address": "",
                "branch": branch.name,
                "branch_code": _branch_code("learning", branch.name),
                "municipality_code": branch.municipality_code,
                "municipality_full_name": ANYANG_MUNICIPALITY_NAMES[
                    branch.municipality_code
                ],
                "provider_organizer": "안양시 평생학습원",
                "collection_category": "교육",
                "domain_category": "교육·강좌",
                "source_group": "municipal_reservation",
                "operator_type": "지자체/공공기관",
                "collection_type": "complete_html_pages+current_detail",
                "raw_fields": {
                    "parser": ANYANG_PARSER,
                    "source_kind": "learning",
                    "source_sequence": sequence,
                    "source_identity": identity,
                    "source_status": source_status,
                    "source_branch_code": branch.code,
                    "list_institution": branch.name,
                    "list_apply_period": apply_period or _clean(cells[3].get_text(" ", strip=True)),
                    "municipality_evidence": "official MM/DD region partition and branch",
                    "official_period_anomaly": period_anomaly,
                },
            }
        )
    return rows, errors


def _reserve_counter(soup: BeautifulSoup) -> tuple[int, int, int]:
    matches: list[tuple[int, int, int]] = []
    for node in soup.select("div.small"):
        match = _RESERVE_COUNTER_RE.fullmatch(_clean(node.get_text(" ", strip=True)))
        if match:
            matches.append(
                tuple(int(value.replace(",", "")) for value in match.groups())
            )
    if len(matches) != 1:
        raise AnyangContractError("reserve count marker changed")
    total, current, advertised_last = matches[0]
    expected_last = max(1, math.ceil(total / ANYANG_RESERVE_PAGE_SIZE))
    if advertised_last != expected_last:
        raise AnyangContractError(
            f"reserve last page {advertised_last} != expected {expected_last}"
        )
    tables = soup.select("table")
    matching = [table for table in tables if _table_headers(table) == _RESERVE_HEADERS]
    if len(matching) != 1:
        raise AnyangContractError("reserve table/header contract changed")
    return total, current, advertised_last


def _reserve_table(soup: BeautifulSoup) -> Any:
    tables = [table for table in soup.select("table") if _table_headers(table) == _RESERVE_HEADERS]
    if len(tables) != 1:
        raise AnyangContractError("reserve table/header contract changed")
    return tables[0]


def _reserve_identity(href: Any) -> str:
    parsed = urlparse(urljoin(f"https://{ANYANG_RESERVE_HOST}{ANYANG_RESERVE_PATH}", _clean(href)))
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != ANYANG_RESERVE_HOST
        or parsed.path != ANYANG_RESERVE_DETAIL_PATH
    ):
        return ""
    values = parse_qs(parsed.query).get("eduLctreNo") or []
    return values[0] if len(values) == 1 and re.fullmatch(r"[1-9]\d{0,11}", values[0]) else ""


def _reserve_title_institution_venue(cell: Any) -> tuple[str, str, str]:
    links = cell.select("a[href]")
    title_node = links[0].find("b") if len(links) == 1 else None
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    clone = BeautifulSoup(str(cell), "lxml").find("td")
    if clone is None:
        return title, "", ""
    for node in clone.select("a"):
        node.decompose()
    parts = [_clean(value) for value in clone.stripped_strings if _clean(value)]
    institution = parts[0] if parts else ""
    venue = " ".join(parts[1:]) if len(parts) > 1 else ""
    return title, institution, venue


def _reserve_rows(
    soup: BeautifulSoup,
    *,
    page: int,
    reference_day: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    table = _reserve_table(soup)
    for tr in table.select("tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        sequence_text = _clean(cells[0].get_text(" ", strip=True))
        if not sequence_text.isdigit():
            continue
        sequence = int(sequence_text)
        if len(cells) != len(_RESERVE_HEADERS):
            errors.append(f"reserve page {page} row {sequence}: expected eight cells")
            continue
        links = cells[2].select("a[href]")
        identity = _reserve_identity(links[0].get("href")) if len(links) == 1 else ""
        title, institution, venue = _reserve_title_institution_venue(cells[2])
        try:
            start, end, period = _date_range(cells[3].get_text(" ", strip=True))
            apply_start, apply_end, apply_period = _date_range(cells[4].get_text(" ", strip=True))
        except AnyangContractError as exc:
            errors.append(f"reserve page {page} row {sequence}: {exc}")
            continue
        selection_nodes = cells[6].select("span.state")
        selection_method = _clean(selection_nodes[0].get_text(" ", strip=True)) if selection_nodes else ""
        source_status = _clean(selection_nodes[-1].get_text(" ", strip=True)) if len(selection_nodes) >= 2 else ""
        is_current = date.fromisoformat(end) >= reference_day
        status = _RESERVE_STATUS_MAP.get(source_status, "")
        capacity_text = _clean(cells[4].get_text(" ", strip=True))
        capacity_match = _RESERVE_CAPACITY_RE.search(capacity_text)
        if not identity or not title or not institution or not venue:
            errors.append(
                f"reserve page {page} row {sequence}: identity/title/institution/venue changed"
            )
            continue
        if is_current and not status:
            errors.append(
                f"reserve page {page} row {sequence}: unknown current status {source_status}"
            )
            continue
        if is_current and capacity_match is None:
            errors.append(f"reserve page {page} row {sequence}: capacity changed")
            continue
        capacity_total = int(capacity_match.group(1).replace(",", "")) if capacity_match else None
        capacity_current = (
            int(capacity_match.group(2).replace(",", ""))
            + int(capacity_match.group(3).replace(",", ""))
            if capacity_match
            else None
        )
        detail_url = anyang_reserve_detail_url(identity)
        rows.append(
            {
                "provider": ANYANG_PROVIDER,
                "provider_course_id": f"{ANYANG_PROVIDER}:reserve:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "category": "education",
                "program_type": "강좌",
                "raw_url": detail_url,
                "application_url": "",
                "reservation_available": False,
                "application_type": "INFORMATION_ONLY",
                "status": status,
                "period": period,
                "start_date": start,
                "end_date": end,
                "apply_period": apply_period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": _clean(cells[3].get_text(" ", strip=True)).replace(period.replace(" ~ ", "~"), "").strip(),
                "target": "",
                "fee": "",
                "capacity": capacity_total,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "venue_name": _clean(f"{institution} {venue}"),
                "venue_address": "",
                "branch": institution,
                "branch_code": "",
                "municipality_code": "",
                "municipality_full_name": "",
                "provider_organizer": "안양시 통합예약",
                "collection_category": "교육",
                "domain_category": "교육·강좌",
                "source_group": "municipal_reservation",
                "operator_type": "지자체/공공기관",
                "collection_type": "complete_html_pages+current_detail",
                "raw_fields": {
                    "parser": ANYANG_PARSER,
                    "source_kind": "reserve",
                    "source_sequence": sequence,
                    "source_identity": identity,
                    "source_status": source_status,
                    "source_selection_method": selection_method,
                    "source_method": _clean(cells[5].get_text(" ", strip=True)),
                    "list_institution": institution,
                    "list_venue": venue,
                    "list_capacity": capacity_text,
                    "list_apply_period": apply_period,
                },
            }
        )
    return rows, errors


def _reserve_partition_identities(soup: BeautifulSoup) -> list[str]:
    result: list[str] = []
    for tr in _reserve_table(soup).select("tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells or not _clean(cells[0].get_text(" ", strip=True)).isdigit():
            continue
        links = cells[2].select("a[href]")
        identity = _reserve_identity(links[0].get("href")) if len(links) == 1 else ""
        if not identity:
            raise AnyangContractError("reserve partition identity changed")
        result.append(identity)
    return result


def _detail_pairs(table: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in table.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index + 1 < len(cells):
            if cells[index].name == "th" and cells[index + 1].name == "td":
                key = _clean(cells[index].get_text(" ", strip=True))
                value = _clean(cells[index + 1].get_text(" ", strip=True))
                if key and key not in result:
                    result[key] = value
                index += 2
            else:
                index += 1
    return result


def _learning_capacity(table: Any) -> Optional[int]:
    for node in table.select("th"):
        if _clean(node.get_text(" ", strip=True)) == "전체정원 (우선+인터넷)":
            sibling = node.find_next_sibling("td")
            value = _clean(sibling.get_text(" ", strip=True) if sibling else "")
            if value.replace(",", "").isdigit():
                return int(value.replace(",", ""))
    return None


def _reserve_target(pairs: Mapping[str, str]) -> str:
    for key in ("교육대상", "신청대상", "모집대상", "대상"):
        value = _clean(pairs.get(key))
        if value:
            return value
    match = _TARGET_IN_DESCRIPTION_RE.search(_clean(pairs.get("교육내용")))
    return _clean(match.group(1)) if match else ""


def _learning_detail(
    row: dict[str, Any], soup: BeautifulSoup, reference_day: date
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("source_identity"))
    code = _clean(row.get("raw_fields", {}).get("source_branch_code"))
    branch = _LEARNING_BRANCH_BY_CODE.get(code)
    errors: list[str] = []
    tables = soup.select("table")
    if not tables or branch is None:
        return [f"learning detail {identity}: detail table/branch absent"]
    table = tables[0]
    pairs = _detail_pairs(table)
    required = {
        "교육기관",
        "강좌명",
        "교육기간",
        "수강료",
        "교육일시",
        "강의실",
        "교육대상",
    }
    missing = sorted(required - set(pairs))
    if missing:
        return [f"learning detail {identity}: missing {','.join(missing)}"]
    if _normalized(pairs["강좌명"]) != _normalized(row.get("title")):
        errors.append(f"learning detail {identity}: title mismatch")
    if _normalized(pairs["교육기관"]) not in {
        _normalized(value) for value in branch.detail_names
    }:
        errors.append(f"learning detail {identity}: institution mismatch")
    try:
        start, end, period = _date_range(pairs["교육기간"])
    except AnyangContractError as exc:
        errors.append(f"learning detail {identity}: {exc}")
        start = end = period = ""
    if start != row.get("start_date") or end != row.get("end_date"):
        errors.append(f"learning detail {identity}: education period mismatch")
    apply_keys = [key for key in pairs if "접수 기간" in key and pairs[key]]
    public_course_without_detail_period = False
    if len(apply_keys) != 1:
        errors.append(f"learning detail {identity}: application period ambiguous")
    else:
        detail_apply_value = pairs[apply_keys[0]]
        if detail_apply_value == "해당없음 (공개강좌)":
            # Three audited senior-centre public courses retain an official
            # application window in the integrated list while their detail
            # labels the visit period as not applicable.  The list dates and
            # the detail's login-gated application control are authoritative.
            public_course_without_detail_period = True
        else:
            try:
                apply_start, apply_end, apply_period = _date_range(detail_apply_value)
                if (
                    apply_start != row.get("apply_start")
                    or apply_end != row.get("apply_end")
                ):
                    errors.append(f"learning detail {identity}: application period mismatch")
            except AnyangContractError as exc:
                errors.append(f"learning detail {identity}: {exc}")
    capacity = _learning_capacity(table)
    if capacity is None:
        errors.append(f"learning detail {identity}: capacity absent")

    apply_controls = []
    for link in soup.select('a[href="#edu_introduce_write"]'):
        alt = _clean((link.find("img") or {}).get("alt") if link.find("img") else "")
        onclick = _clean(link.get("onclick"))
        apply_controls.append((alt, onclick))
    source_status = _clean(row.get("raw_fields", {}).get("source_status"))
    if source_status == "접수기간":
        if len(apply_controls) != 1:
            errors.append(f"learning detail {identity}: login application control absent")
        else:
            alt, onclick = apply_controls[0]
            if alt != "강좌접수 신청" or "로그인후 수강신청" not in onclick:
                errors.append(f"learning detail {identity}: unsafe application control")
    elif apply_controls:
        errors.append(f"learning detail {identity}: unexpected application control")

    if errors:
        return errors
    row.update(
        {
            "period": period,
            "fee": pairs["수강료"],
            "schedule_raw": pairs["교육일시"],
            "target": pairs["교육대상"],
            "capacity": capacity,
            "capacity_total": capacity,
            "venue_name": _clean(f"{branch.name} {pairs['강의실']}"),
        }
    )
    if source_status == "접수기간":
        row["application_url"] = row["raw_url"]
        row["reservation_available"] = True
        row["application_type"] = "ONLINE_LOGIN_REQUIRED"
        row["raw_fields"]["application_control"] = (
            "anonymous_login_alert_public_course"
            if public_course_without_detail_period
            else "anonymous_login_alert"
        )
    elif source_status == "접수예정":
        row["raw_fields"]["application_control"] = (
            "not_yet_offered_before_application_window"
        )
    elif source_status == "접수마감":
        row["raw_fields"]["application_control"] = (
            "not_offered_after_application_closed"
        )
    else:
        row["raw_fields"]["application_control"] = "not_offered_education_in_progress"
    return []


def _reserve_detail(
    row: dict[str, Any], soup: BeautifulSoup
) -> tuple[list[str], str]:
    identity = _clean(row.get("raw_fields", {}).get("source_identity"))
    errors: list[str] = []
    tables = soup.select("table")
    if not tables:
        return [f"reserve detail {identity}: table absent"], ""
    pairs = _detail_pairs(tables[0])
    required = {
        "프로그램명",
        "강좌명",
        "선발방식",
        "모집기간",
        "운영기간",
        "교육기관/장소",
        "모집인원 및 신청현황",
        "교육시간",
        "수강료",
    }
    missing = sorted(required - set(pairs))
    if missing:
        return [f"reserve detail {identity}: missing {','.join(missing)}"], ""
    detail_title = pairs["강좌명"]
    source_status = _clean(row.get("raw_fields", {}).get("source_status"))
    if source_status and detail_title.endswith(source_status):
        detail_title = detail_title[: -len(source_status)].strip()
    if _normalized(detail_title) != _normalized(row.get("title")):
        errors.append(f"reserve detail {identity}: title mismatch")
    list_place = _clean(
        f"{row.get('raw_fields', {}).get('list_institution')} "
        f"{row.get('raw_fields', {}).get('list_venue')}"
    )
    if _normalized(pairs["교육기관/장소"]) != _normalized(list_place):
        errors.append(f"reserve detail {identity}: institution/venue mismatch")
    try:
        start, end, period = _date_range(pairs["운영기간"])
        apply_start, apply_end, apply_period = _date_range(pairs["모집기간"])
    except AnyangContractError as exc:
        errors.append(f"reserve detail {identity}: {exc}")
        start = end = period = apply_start = apply_end = apply_period = ""
    if start != row.get("start_date") or end != row.get("end_date"):
        errors.append(f"reserve detail {identity}: operating period mismatch")
    if apply_start != row.get("apply_start") or apply_end != row.get("apply_end"):
        errors.append(f"reserve detail {identity}: application period mismatch")
    capacity_match = _DETAIL_CAPACITY_RE.search(pairs["모집인원 및 신청현황"])
    if capacity_match is None:
        errors.append(f"reserve detail {identity}: capacity changed")
    else:
        current, total, _wait_current, _wait_total = (
            int(value.replace(",", "")) for value in capacity_match.groups()
        )
        if total != row.get("capacity_total"):
            errors.append(f"reserve detail {identity}: total capacity mismatch")
        # The list adds internet and visit counts; the detail presents the
        # system's accepted count.  It may legitimately lag, so only bounds
        # and total are contractual.
        if current < 0 or current > total:
            errors.append(f"reserve detail {identity}: invalid accepted count")
    target = _reserve_target(pairs)
    if not target:
        errors.append(f"reserve detail {identity}: target absent")

    write_links = soup.select("a.p-button.write[href]")
    application_url = ""
    if source_status in {"모집중", "대기자모집", "추첨 접수중"}:
        if len(write_links) != 1 or _clean(write_links[0].get_text(" ", strip=True)) != "신청":
            errors.append(f"reserve detail {identity}: application control absent")
        else:
            application_url = urljoin(row["raw_url"], write_links[0].get("href"))
            parsed = urlparse(application_url)
            values = parse_qs(parsed.query).get("eduLctreNo") or []
            if (
                parsed.scheme.lower() != "https"
                or (parsed.hostname or "").lower() != ANYANG_RESERVE_HOST
                or parsed.path != ANYANG_RESERVE_APPLICATION_PATH
                or values != [identity]
            ):
                errors.append(f"reserve detail {identity}: unsafe application URL")
    elif write_links:
        errors.append(f"reserve detail {identity}: unexpected application control")

    if errors:
        return errors, ""
    institution_code = _clean(row.get("raw_fields", {}).get("source_institution_code"))
    institution = _RESERVE_INSTITUTION_BY_CODE.get(institution_code)
    if institution is None:
        return [f"reserve detail {identity}: institution partition absent"], ""
    row.update(
        {
            "period": period,
            "apply_period": apply_period,
            "schedule_raw": pairs["교육시간"],
            "fee": pairs["수강료"],
            "target": target,
            "venue_name": pairs["교육기관/장소"],
            "branch": institution.name,
            "branch_code": _branch_code("reserve", institution.name),
            "municipality_code": institution.municipality_code,
            "municipality_full_name": ANYANG_MUNICIPALITY_NAMES[
                institution.municipality_code
            ],
        }
    )
    row["raw_fields"]["municipality_evidence"] = (
        f"official reserve institution partition {institution.code}:{institution.name}"
    )
    if application_url:
        row["application_url"] = application_url
        row["reservation_available"] = True
        row["application_type"] = "ONLINE_IDENTITY_REQUIRED"
        row["raw_fields"]["application_control"] = "identity_verification_gate"
    else:
        row["raw_fields"]["application_control"] = "not_offered_by_source_status"
    return [], application_url


def _application_gate(identity: str, soup: BeautifulSoup) -> list[str]:
    scripts = " ".join(_clean(node.get_text(" ", strip=True)) for node in soup.select("script"))
    if "본인인증 후 이용이 가능합니다." not in scripts:
        return [f"reserve application {identity}: identity gate text changed"]
    if "/loginView.do" not in scripts or "eduLctreWebView.do" not in scripts:
        return [f"reserve application {identity}: login redirect changed"]
    if soup.select("form"):
        return [f"reserve application {identity}: anonymous mutation form exposed"]
    return []


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in row.items() if not key.startswith("_")}
    raw_fields = dict(result.get("raw_fields") or {})
    result["raw_fields"] = {
        key: raw_fields[key] for key in ANYANG_RAW_FIELD_ALLOWLIST if key in raw_fields
    }
    return result


def _page_signature(rows: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("end_date")),
        )
        for row in rows
    )


def _is_functional_test_row(row: Mapping[str, Any]) -> bool:
    return (
        _clean(row.get("raw_fields", {}).get("source_kind")) == "reserve"
        and _FUNCTIONAL_TEST_TITLE_RE.search(_clean(row.get("title"))) is not None
    )


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "required_list_requests": 0,
        "source_total": 0,
        "source_rows": 0,
        "learning_total": 0,
        "learning_pages": 0,
        "learning_current_count": 0,
        "learning_region_totals": {},
        "reserve_total": 0,
        "reserve_pages": 0,
        "reserve_current_count": 0,
        "reserve_institution_totals": {},
        "expired_count": 0,
        "source_current_count": 0,
        "current_count": 0,
        "functional_test_exclusion_count": 0,
        "functional_test_exclusion_ids": [],
        "returned_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "application_gate_attempts": 0,
        "application_gate_pages": 0,
        "duplicate_count": 0,
        "duplicate_identity_count": 0,
        "duplicate_url_count": 0,
        "period_anomaly_count": 0,
        "period_anomaly_ids": [],
        "required_field_counts": {},
        "status_counts": {},
        "source_kind_counts": {},
        "branch_counts": {},
        "municipality_counts": {},
        "pagination_detected": False,
        "pagination_complete": False,
        "partitions_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "covered_municipalities": [
            dict(item) for item in ANYANG_COVERED_MUNICIPALITIES
        ],
        "ownership_alias_providers": [
            item.provider for item in ANYANG_NON_EXECUTING_ALIASES
        ],
    }


def collect_anyang_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 1200,
    detail_limit: int = 1000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = ANYANG_MAX_WORKERS,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Anyang education snapshot."""

    meta = _base_meta()
    if not is_anyang_target(target):
        meta["configured_collection_error"] = (
            "target is not the canonical Anyang education owner"
        )
        return [], ANYANG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = (
                "Anyang legacy-TLS session_factory injection is required"
            )
            return [], ANYANG_PARSER, meta
        session_factory = anyang_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        worker_count = max(1, min(int(max_workers), ANYANG_MAX_WORKERS))
        reference_day = _today(today)
    except (TypeError, ValueError):
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], ANYANG_PARSER, meta

    errors: list[str] = []
    source_cap_reached = False
    main_session: Any = None
    learning_rows: list[dict[str, Any]] = []
    reserve_rows: list[dict[str, Any]] = []
    learning_total = learning_last = reserve_total = reserve_last = 0
    learning_first_rows: list[dict[str, Any]] = []
    reserve_first_rows: list[dict[str, Any]] = []
    learning_region_totals: dict[str, int] = {}
    reserve_institution_totals: dict[str, int] = {}
    reserve_partition_members: dict[str, list[str]] = {}
    required_requests = 0

    try:
        main_session = session_factory()
        try:
            learning_first = _fetch(
                fetcher,
                main_session,
                anyang_learning_list_url(),
                timeout,
                allow_redirects=False,
            )
            learning_total, learning_last = _learning_contract(learning_first)
            learning_first_rows, item_errors = _learning_rows(
                learning_first, page=1, reference_day=reference_day
            )
            errors.extend(item_errors)
        except Exception as exc:
            errors.append(f"learning first page failed ({type(exc).__name__})")

        try:
            reserve_first = _fetch(
                fetcher,
                main_session,
                anyang_reserve_list_url(),
                timeout,
                allow_redirects=False,
            )
            reserve_total, reserve_current, reserve_last = _reserve_counter(reserve_first)
            if reserve_current != 1:
                raise AnyangContractError("reserve first-page marker changed")
            select = reserve_first.select_one('select[name="searchInsttNo"]')
            if _options(select) != _RESERVE_INSTITUTION_OPTIONS:
                raise AnyangContractError("reserve institution menu changed")
            reserve_first_rows, item_errors = _reserve_rows(
                reserve_first, page=1, reference_day=reference_day
            )
            errors.extend(item_errors)
        except Exception as exc:
            errors.append(f"reserve first page failed ({type(exc).__name__})")

        # Official partition declarations.  Learning exposes two regions;
        # reserve exposes its complete institution menu.
        if not errors:
            for region in ("MM", "DD"):
                try:
                    soup = _fetch(
                        fetcher,
                        main_session,
                        anyang_learning_list_url(1, region),
                        timeout,
                        allow_redirects=False,
                    )
                    total, _last = _learning_contract(soup)
                    selected = soup.select(
                        f'form[name="frmSearch"] select[name="s1"] '
                        f'option[value="{region}"][selected]'
                    )
                    if len(selected) != 1:
                        raise AnyangContractError("learning region filter not reflected")
                    learning_region_totals[region] = total
                except Exception as exc:
                    errors.append(
                        f"learning region {region} failed ({type(exc).__name__})"
                    )
            if sum(learning_region_totals.values()) != learning_total:
                errors.append("learning MM/DD totals do not equal unfiltered total")

        partition_first: dict[str, BeautifulSoup] = {}
        if not errors:
            partition_items = [
                (
                    ("reserve_partition", item.code, 1),
                    anyang_reserve_list_url(1, item.code),
                    False,
                )
                for item in ANYANG_RESERVE_INSTITUTIONS
            ]
            partition_first, partition_errors = _parallel_fetch(
                partition_items,
                fetcher=fetcher,
                session_factory=session_factory,
                timeout=timeout,
                max_workers=worker_count,
            )
            errors.extend(partition_errors)
            extra_partition_items: list[tuple[Any, str, bool]] = []
            for institution in ANYANG_RESERVE_INSTITUTIONS:
                key = ("reserve_partition", institution.code, 1)
                soup = partition_first.get(key)
                if soup is None:
                    continue
                try:
                    total, current, last = _reserve_counter(soup)
                    if current != 1:
                        raise AnyangContractError("partition first-page marker changed")
                    selected = soup.select(
                        f'select[name="searchInsttNo"] '
                        f'option[value="{institution.code}"][selected]'
                    )
                    if len(selected) != 1:
                        raise AnyangContractError("institution filter not reflected")
                    reserve_institution_totals[institution.code] = total
                    reserve_partition_members[institution.code] = (
                        _reserve_partition_identities(soup)
                    )
                    for page in range(2, last + 1):
                        extra_partition_items.append(
                            (
                                ("reserve_partition", institution.code, page),
                                anyang_reserve_list_url(page, institution.code),
                                False,
                            )
                        )
                except Exception as exc:
                    errors.append(
                        f"reserve institution {institution.code} failed "
                        f"({type(exc).__name__})"
                    )
            if extra_partition_items and not errors:
                extra, extra_errors = _parallel_fetch(
                    extra_partition_items,
                    fetcher=fetcher,
                    session_factory=session_factory,
                    timeout=timeout,
                    max_workers=worker_count,
                )
                partition_first.update(extra)
                errors.extend(extra_errors)
                for key, soup in extra.items():
                    _kind, code, page = key
                    try:
                        total, current, _last = _reserve_counter(soup)
                        if (
                            total != reserve_institution_totals.get(code)
                            or current != page
                        ):
                            raise AnyangContractError("partition counter changed")
                        reserve_partition_members[code].extend(
                            _reserve_partition_identities(soup)
                        )
                    except Exception as exc:
                        errors.append(
                            f"reserve institution {code} page {page} failed "
                            f"({type(exc).__name__})"
                        )

        reserve_partition_page_count = len(partition_first)
        required_requests = (
            learning_last
            + 1  # learning sentinel
            + 2  # MM/DD partition declarations
            + 1  # learning page-one recheck
            + reserve_last
            + 1  # reserve sentinel
            + reserve_partition_page_count
            + 1  # reserve page-one recheck
        )
        meta["required_list_requests"] = required_requests
        if required_requests > allowed_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of {required_requests} required list requests"
            )

        learning_page_rows: dict[int, list[dict[str, Any]]] = {1: learning_first_rows}
        reserve_page_rows: dict[int, list[dict[str, Any]]] = {1: reserve_first_rows}
        if not errors:
            list_items = [
                (
                    ("learning", page),
                    anyang_learning_list_url(page),
                    False,
                )
                for page in range(2, learning_last + 2)
            ] + [
                (
                    ("reserve", page),
                    anyang_reserve_list_url(page),
                    False,
                )
                for page in range(2, reserve_last + 2)
            ]
            fetched, fetch_errors = _parallel_fetch(
                list_items,
                fetcher=fetcher,
                session_factory=session_factory,
                timeout=timeout,
                max_workers=worker_count,
            )
            errors.extend(fetch_errors)
            for key, soup in fetched.items():
                kind, page = key
                try:
                    if kind == "learning":
                        total, last = _learning_contract(
                            soup, expected_total=learning_total
                        )
                        if total != learning_total or last != learning_last:
                            raise AnyangContractError("learning declaration changed")
                        parsed, item_errors = _learning_rows(
                            soup, page=page, reference_day=reference_day
                        )
                        errors.extend(item_errors)
                        learning_page_rows[page] = parsed
                    else:
                        total, current, last = _reserve_counter(soup)
                        if (
                            total != reserve_total
                            or current != page
                            or last != reserve_last
                        ):
                            raise AnyangContractError("reserve declaration changed")
                        parsed, item_errors = _reserve_rows(
                            soup, page=page, reference_day=reference_day
                        )
                        errors.extend(item_errors)
                        reserve_page_rows[page] = parsed
                except Exception as exc:
                    errors.append(f"{kind} page {page} failed ({type(exc).__name__})")

        # Exact page row boundaries and source numbering.
        for page in range(1, learning_last + 2):
            expected = (
                min(
                    ANYANG_LEARNING_PAGE_SIZE,
                    learning_total - (page - 1) * ANYANG_LEARNING_PAGE_SIZE,
                )
                if page <= learning_last
                else 0
            )
            if len(learning_page_rows.get(page, [])) != expected:
                errors.append(f"learning page {page}: terminal/full-page count mismatch")
        for page in range(1, reserve_last + 2):
            expected = (
                min(
                    ANYANG_RESERVE_PAGE_SIZE,
                    reserve_total - (page - 1) * ANYANG_RESERVE_PAGE_SIZE,
                )
                if page <= reserve_last
                else 0
            )
            if len(reserve_page_rows.get(page, [])) != expected:
                errors.append(f"reserve page {page}: terminal/full-page count mismatch")

        learning_rows = [
            row
            for page in range(1, learning_last + 1)
            for row in learning_page_rows.get(page, [])
        ]
        reserve_rows = [
            row
            for page in range(1, reserve_last + 1)
            for row in reserve_page_rows.get(page, [])
        ]
        learning_sequences = [
            int(row["raw_fields"]["source_sequence"]) for row in learning_rows
        ]
        reserve_sequences = [
            int(row["raw_fields"]["source_sequence"]) for row in reserve_rows
        ]
        if learning_sequences != list(range(learning_total, 0, -1)):
            errors.append("learning numbering is not continuous")
        if reserve_sequences != list(range(reserve_total, 0, -1)):
            errors.append("reserve numbering is not continuous")

        learning_branch_counts = Counter(
            row["raw_fields"]["source_branch_code"] for row in learning_rows
        )
        observed_regions = {
            "MM": sum(
                count
                for code, count in learning_branch_counts.items()
                if _LEARNING_BRANCH_BY_CODE[code].municipality_code == ANYANG_MANAN_CODE
            ),
            "DD": sum(
                count
                for code, count in learning_branch_counts.items()
                if _LEARNING_BRANCH_BY_CODE[code].municipality_code == ANYANG_DONGAN_CODE
            ),
        }
        if observed_regions != learning_region_totals:
            errors.append("learning parsed MM/DD counts differ from official partitions")

        reserve_identity_set = {
            _clean(row["raw_fields"]["source_identity"]) for row in reserve_rows
        }
        identity_to_institution: dict[str, str] = {}
        partition_union: set[str] = set()
        for code, values in reserve_partition_members.items():
            if len(values) != reserve_institution_totals.get(code, -1):
                errors.append(f"reserve institution {code}: declared/parsed mismatch")
            for identity in values:
                if identity in identity_to_institution:
                    errors.append(f"reserve identity {identity}: overlapping institutions")
                identity_to_institution[identity] = code
                partition_union.add(identity)
        if sum(reserve_institution_totals.values()) != reserve_total:
            errors.append("reserve institution totals do not equal unfiltered total")
        if partition_union != reserve_identity_set:
            errors.append("reserve institution union differs from unfiltered catalogue")
        for row in reserve_rows:
            identity = _clean(row["raw_fields"]["source_identity"])
            code = identity_to_institution.get(identity, "")
            if not code:
                errors.append(f"reserve identity {identity}: institution owner absent")
            row["raw_fields"]["source_institution_code"] = code

        # Recheck both unfiltered first pages after all list/partition requests.
        if not errors:
            try:
                learning_recheck = _fetch(
                    fetcher,
                    main_session,
                    anyang_learning_list_url(),
                    timeout,
                    allow_redirects=False,
                )
                total, last = _learning_contract(learning_recheck)
                parsed, item_errors = _learning_rows(
                    learning_recheck, page=1, reference_day=reference_day
                )
                errors.extend(item_errors)
                if (
                    total != learning_total
                    or last != learning_last
                    or _page_signature(parsed) != _page_signature(learning_first_rows)
                ):
                    errors.append("learning page one changed during crawl")
            except Exception as exc:
                errors.append(f"learning page-one recheck failed ({type(exc).__name__})")
            try:
                reserve_recheck = _fetch(
                    fetcher,
                    main_session,
                    anyang_reserve_list_url(),
                    timeout,
                    allow_redirects=False,
                )
                total, current, last = _reserve_counter(reserve_recheck)
                parsed, item_errors = _reserve_rows(
                    reserve_recheck, page=1, reference_day=reference_day
                )
                errors.extend(item_errors)
                if (
                    total != reserve_total
                    or current != 1
                    or last != reserve_last
                    or _page_signature(parsed) != _page_signature(reserve_first_rows)
                ):
                    errors.append("reserve page one changed during crawl")
            except Exception as exc:
                errors.append(f"reserve page-one recheck failed ({type(exc).__name__})")

        all_rows = learning_rows + reserve_rows
        identities = [
            (
                row["raw_fields"]["source_kind"],
                row["raw_fields"]["source_branch_code"]
                if row["raw_fields"]["source_kind"] == "learning"
                else "",
                row["raw_fields"]["source_identity"],
            )
            for row in all_rows
        ]
        course_ids = [_clean(row.get("provider_course_id")) for row in all_rows]
        raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
        meta["duplicate_identity_count"] = len(identities) - len(set(identities))
        meta["duplicate_count"] = len(course_ids) - len(set(course_ids))
        meta["duplicate_url_count"] = len(raw_urls) - len(set(raw_urls))
        if meta["duplicate_identity_count"] or meta["duplicate_count"] or meta["duplicate_url_count"]:
            errors.append("duplicate official identities/course IDs/detail URLs")

        source_current_rows = [
            row
            for row in all_rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= reference_day
        ]
        functional_test_rows = [
            row for row in source_current_rows if _is_functional_test_row(row)
        ]
        functional_test_ids = {
            _clean(row.get("provider_course_id")) for row in functional_test_rows
        }
        current_rows = [
            row
            for row in source_current_rows
            if _clean(row.get("provider_course_id")) not in functional_test_ids
        ]
        learning_current = [
            row for row in current_rows if row["raw_fields"]["source_kind"] == "learning"
        ]
        reserve_current_rows = [
            row for row in current_rows if row["raw_fields"]["source_kind"] == "reserve"
        ]
        if len(current_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_rows)} required details"
            )

        enriched: list[dict[str, Any]] = []
        application_items: list[tuple[Any, str, bool]] = []
        if not errors and current_rows:
            detail_items = [
                (
                    (
                        "detail",
                        row["raw_fields"]["source_kind"],
                        row["raw_fields"]["source_branch_code"]
                        if row["raw_fields"]["source_kind"] == "learning"
                        else "",
                        row["raw_fields"]["source_identity"],
                    ),
                    row["raw_url"],
                    row["raw_fields"]["source_kind"] == "learning",
                )
                for row in current_rows
            ]
            meta["detail_attempts"] = len(detail_items)
            details, detail_fetch_errors = _parallel_fetch(
                detail_items,
                fetcher=fetcher,
                session_factory=session_factory,
                timeout=timeout,
                max_workers=worker_count,
            )
            errors.extend(detail_fetch_errors)
            row_by_key = {
                item[0]: row for item, row in zip(detail_items, current_rows)
            }
            for key, soup in details.items():
                row = row_by_key.get(key)
                if row is None:
                    errors.append(f"detail {key}: parent row absent")
                    continue
                if key[1] == "learning":
                    item_errors = _learning_detail(row, soup, reference_day)
                    application_url = ""
                else:
                    item_errors, application_url = _reserve_detail(row, soup)
                if item_errors:
                    meta["detail_errors"] += 1
                    errors.extend(item_errors)
                else:
                    meta["detail_pages"] += 1
                    enriched.append(row)
                    if application_url:
                        application_items.append(
                            (("application", key[-1]), application_url, True)
                        )

        if not errors and application_items:
            meta["application_gate_attempts"] = len(application_items)
            gates, gate_fetch_errors = _parallel_fetch(
                application_items,
                fetcher=fetcher,
                session_factory=session_factory,
                timeout=timeout,
                max_workers=worker_count,
            )
            errors.extend(gate_fetch_errors)
            for key, soup in gates.items():
                item_errors = _application_gate(key[1], soup)
                if item_errors:
                    errors.extend(item_errors)
                else:
                    meta["application_gate_pages"] += 1

        cleaned = [_clean_row(row) for row in enriched] if not errors else []
        deduper = dedupe_rows or _default_dedupe
        if cleaned:
            deduped = list(deduper(cleaned))
            if len(deduped) != len(cleaned):
                errors.append("dedupe changed an already unique official snapshot")
                cleaned = []
            else:
                cleaned = deduped
        cleaned.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )
        required_fields = (
            "target",
            "fee",
            "start_date",
            "end_date",
            "venue_name",
            "category",
            "schedule_raw",
        )
        required_field_counts = {
            field: sum(bool(_clean(row.get(field))) for row in cleaned)
            for field in required_fields
        }
        missing_required = {
            field: len(cleaned) - count
            for field, count in required_field_counts.items()
            if count != len(cleaned)
        }
        if cleaned and missing_required:
            errors.append(f"required output fields absent {missing_required}")
            cleaned = []

        list_complete = (
            not errors
            and len(learning_rows) == learning_total
            and len(reserve_rows) == reserve_total
            and learning_page_rows.get(learning_last + 1) == []
            and reserve_page_rows.get(reserve_last + 1) == []
        )
        partitions_complete = (
            not errors
            and observed_regions == learning_region_totals
            and partition_union == reserve_identity_set
            and sum(reserve_institution_totals.values()) == reserve_total
        )
        details_complete = (
            not errors
            and meta["detail_pages"] == len(current_rows)
            and meta["detail_errors"] == 0
            and meta["application_gate_pages"] == len(application_items)
        )
        snapshot_complete = (
            list_complete
            and partitions_complete
            and details_complete
            and not source_cap_reached
            and len(cleaned) == len(current_rows)
        )
        if not snapshot_complete:
            cleaned = []

        meta.update(
            {
                "pages": required_requests if list_complete else 0,
                "source_total": learning_total + reserve_total,
                "source_rows": len(learning_rows) + len(reserve_rows),
                "learning_total": learning_total,
                "learning_pages": learning_last,
                "learning_current_count": len(learning_current),
                "learning_region_totals": dict(learning_region_totals),
                "reserve_total": reserve_total,
                "reserve_pages": reserve_last,
                "reserve_current_count": len(reserve_current_rows),
                "reserve_institution_totals": dict(reserve_institution_totals),
                "expired_count": len(all_rows) - len(source_current_rows),
                "source_current_count": len(source_current_rows),
                "current_count": len(current_rows),
                "functional_test_exclusion_count": len(functional_test_rows),
                "functional_test_exclusion_ids": sorted(functional_test_ids),
                "returned_count": len(cleaned),
                "period_anomaly_count": sum(
                    bool(row.get("raw_fields", {}).get("official_period_anomaly"))
                    for row in learning_rows
                ),
                "period_anomaly_ids": [
                    _clean(row.get("raw_fields", {}).get("source_identity"))
                    for row in learning_rows
                    if row.get("raw_fields", {}).get("official_period_anomaly")
                ],
                "required_field_counts": required_field_counts,
                "status_counts": dict(
                    sorted(Counter(_clean(row.get("status")) for row in cleaned).items())
                ),
                "source_kind_counts": dict(
                    sorted(
                        Counter(
                            _clean(row.get("raw_fields", {}).get("source_kind"))
                            for row in cleaned
                        ).items()
                    )
                ),
                "branch_counts": dict(
                    sorted(Counter(_clean(row.get("branch")) for row in cleaned).items())
                ),
                "municipality_counts": dict(
                    sorted(
                        Counter(
                            _clean(row.get("municipality_full_name")) for row in cleaned
                        ).items()
                    )
                ),
                "pagination_detected": learning_last > 1 or reserve_last > 1,
                "pagination_complete": list_complete,
                "partitions_complete": partitions_complete,
                "details_complete": details_complete,
                "snapshot_complete": snapshot_complete,
                "source_cap_reached": source_cap_reached,
                "no_current_data": snapshot_complete and not cleaned,
                "no_current_reason": (
                    "all current source rows are excluded functional test records"
                    if snapshot_complete and not cleaned and functional_test_rows
                    else "both complete Anyang education catalogues contain only ended courses"
                    if snapshot_complete and not cleaned
                    else ""
                ),
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return cleaned, ANYANG_PARSER, meta
    finally:
        _close_quietly(main_session)


collect_anyang_target = collect_anyang_education_courses


__all__ = [
    "ANYANG_CANONICAL_CANDIDATE_ID",
    "ANYANG_CANONICAL_URL",
    "ANYANG_CITY_CODE",
    "ANYANG_COVERED_MUNICIPALITIES",
    "ANYANG_DONGAN_CODE",
    "ANYANG_FETCH_ATTEMPTS",
    "ANYANG_LEARNING_BRANCHES",
    "ANYANG_LEARNING_HOST",
    "ANYANG_LEARNING_PAGE_SIZE",
    "ANYANG_LEARNING_PATH",
    "ANYANG_MANAN_CODE",
    "ANYANG_MAX_WORKERS",
    "ANYANG_MUNICIPALITY_NAMES",
    "ANYANG_NON_EXECUTING_ALIASES",
    "ANYANG_PARSER",
    "ANYANG_PROVIDER",
    "ANYANG_RAW_FIELD_ALLOWLIST",
    "ANYANG_RESERVE_CANDIDATE_ID",
    "ANYANG_RESERVE_HOST",
    "ANYANG_RESERVE_INSTITUTIONS",
    "ANYANG_RESERVE_PAGE_SIZE",
    "ANYANG_RESERVE_URL",
    "anyang_learning_list_url",
    "anyang_reserve_detail_url",
    "anyang_reserve_list_url",
    "anyang_session_factory",
    "collect_anyang_education_courses",
    "collect_anyang_target",
    "is_anyang_target",
    "is_target",
]
