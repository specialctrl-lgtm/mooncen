"""Fail-closed collectors for the three audited Gyeongsan education ledgers.

The official lifelong-learning site exposes three disjoint course ledgers in
the scope audited here.  The incumbent providers retain non-overlapping
ownership:

* ``87106AA0`` owns only the eup/myeon/dong learning-centre ledger.
* ``999BABE7`` owns the general lifelong-program and women's-centre ledgers.

All advertised pages, an exact post-last sentinel, every current detail, and
the full ledgers again after the details are required before a snapshot is
returned.  Login, application, instructor, attachment, and other endpoints
that can disclose or accept personal information are deliberately outside the
request allow-list.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha1, sha256
import re
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


GYEONGSAN_TOWN_PROVIDER = "MUNI_WWW_GBGS_GO_KR_87106AA0"
GYEONGSAN_PROGRAM_PROVIDER = "MUNI_WWW_GBGS_GO_KR_999BABE7"
GYEONGSAN_DUPLICATE_TOWN_PROVIDER = "MUNI_WWW_GBGS_GO_KR_4D7732DD"
GYEONGSAN_PROVIDERS = (
    GYEONGSAN_TOWN_PROVIDER,
    GYEONGSAN_PROGRAM_PROVIDER,
)
GYEONGSAN_MUNICIPALITY_CODE = "4729000000"
GYEONGSAN_MUNICIPALITY_NAME = "경상북도 경산시"
GYEONGSAN_HOST = "www.gbgs.go.kr"
GYEONGSAN_DETAIL_PATH = "/lll/edu/detail.tc"
GYEONGSAN_PAGE_SIZE = 15
GYEONGSAN_RECOMMENDED_MAX_PAGES = 50
GYEONGSAN_RECOMMENDED_DETAIL_LIMIT = 500
GYEONGSAN_RECOMMENDED_DETAIL_WORKERS = 4
GYEONGSAN_FETCH_ATTEMPTS = 2
GYEONGSAN_MAX_HTML_BYTES = 2_000_000
GYEONGSAN_PARSER = (
    "gyeongsan_complete_owned_ledgers+declared_total_and_pages+"
    "exact_post_last_sentinel+all_current_details+stable_full_recheck+"
    "global_eduNo_disjoint+source_status_controls+application_attachment_pii_no_fetch"
)


@dataclass(frozen=True)
class GyeongsanLedger:
    key: str
    owner_provider: str
    menu_name: str
    mn: str
    page_no: str
    search_inst_no: str
    category: str = ""

    @property
    def path(self) -> str:
        return f"/lll/page/{self.mn}/{self.page_no}.tc"

    @property
    def canonical_url(self) -> str:
        pairs = (
            ("mn", self.mn),
            ("pageIndex", "1"),
            ("pageNo", self.page_no),
            ("paramIdx", ""),
            ("eduNo", "-1"),
            ("searchInstNo", self.search_inst_no),
            ("srchCtgryCd", self.category),
            ("srchLlPrgrmCd", ""),
            ("srchRgnCd", ""),
            ("srchEduNm", ""),
        )
        return f"https://{GYEONGSAN_HOST}{self.path}?{urlencode(pairs)}"


GYEONGSAN_TOWN_LEDGER = GyeongsanLedger(
    "town",
    GYEONGSAN_TOWN_PROVIDER,
    "읍면동학습관",
    "2391",
    "1649",
    "1",
)
GYEONGSAN_PROGRAM_LEDGER = GyeongsanLedger(
    "program",
    GYEONGSAN_PROGRAM_PROVIDER,
    "평생학습 프로그램",
    "2400",
    "1604",
    "2",
)
GYEONGSAN_WOMEN_LEDGER = GyeongsanLedger(
    "women",
    GYEONGSAN_PROGRAM_PROVIDER,
    "여성회관",
    "2399",
    "1650",
    "3",
)
GYEONGSAN_LEDGERS = (
    GYEONGSAN_TOWN_LEDGER,
    GYEONGSAN_PROGRAM_LEDGER,
    GYEONGSAN_WOMEN_LEDGER,
)
_LEDGER_BY_KEY = {ledger.key: ledger for ledger in GYEONGSAN_LEDGERS}

GYEONGSAN_TOWN_LEGACY_URL = (
    "https://www.gbgs.go.kr/lll/page/link.tc?mn=2391&pageNo=1649"
)
GYEONGSAN_TOWN_BARE_URL = "https://www.gbgs.go.kr/lll/page/2391/1649.tc"
GYEONGSAN_TOWN_CANONICAL_URL = GYEONGSAN_TOWN_LEDGER.canonical_url
GYEONGSAN_PROGRAM_CANONICAL_URL = GYEONGSAN_PROGRAM_LEDGER.canonical_url
GYEONGSAN_WOMEN_CANDIDATE_URL = "https://www.gbgs.go.kr/lll/page/2399/1650.tc"
GYEONGSAN_WOMEN_CANONICAL_URL = GYEONGSAN_WOMEN_LEDGER.canonical_url

GYEONGSAN_TOWN_REGIONS: tuple[tuple[str, str], ...] = (
    ("", "전체"),
    ("CTI0020008", "하양읍"),
    ("CTI0020009", "진량읍"),
    ("CTI0020010", "압량읍"),
    ("CTI0020011", "와촌면"),
    ("CTI0020012", "자인면"),
    ("CTI0020013", "용성면"),
    ("CTI0020014", "남산면"),
    ("CTI0020015", "남천면"),
    ("CTI0020001", "중앙동"),
    ("CTI0020002", "동부동"),
    ("CTI0020003", "서부1동"),
    ("CTI0020004", "서부2동"),
    ("CTI0020005", "남부동"),
    ("CTI0020006", "북부동"),
    ("CTI0020007", "중방동"),
)
GYEONGSAN_PROGRAM_FILTERS: tuple[tuple[str, str], ...] = (
    ("LEC0030001", "경산아카데미"),
    ("LEC0030002", "스마트메이커"),
    ("LEC0030004", "동네배움터"),
    ("LEC0030006", "가족주말 야외체험학교"),
    ("LEC0030007", "평생학습강좌(일반)"),
)
GYEONGSAN_WOMEN_FILTERS: tuple[tuple[str, str], ...] = (
    ("CTR0010001", "정규강좌"),
    ("CTR0010002", "특별강좌"),
)

GYEONGSAN_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_IR_C4B3754F68ED": {
        "provider": GYEONGSAN_TOWN_PROVIDER,
        "url": GYEONGSAN_TOWN_LEGACY_URL,
        "decision": "retain_incumbent_for_town_ledger_and_retarget_redirect_to_canonical",
    },
    "MUNI_IR_788C48C5538C": {
        "provider": GYEONGSAN_PROGRAM_PROVIDER,
        "url": GYEONGSAN_WOMEN_CANDIDATE_URL,
        "decision": "retain_as_second_disjoint_ledger_of_program_incumbent",
    },
    "MUNI_IR_AE0852243BB3": {
        "provider": "MUNI_WWW_GBGS_GO_KR_47FC57AD",
        "url": "https://www.gbgs.go.kr/lll/board/detail.tc?mn=2422&mngNo=1&boardNo=213807",
        "decision": "single_past_notice_without_course_identity_ledger",
    },
    "MUNI_IR_FC84D7243A3E": {
        "provider": "MUNI_WWW_GBGS_GO_KR_52AEEEE4",
        "url": "https://www.gbgs.go.kr/lll/page/2391/1649.tc?pageIndex=17",
        "decision": "pagination_alias_of_town_canonical",
    },
    "ACTIVE_DUPLICATE_4D7732DD": {
        "provider": GYEONGSAN_DUPLICATE_TOWN_PROVIDER,
        "url": (
            "https://www.gbgs.go.kr/lll/page/2391/1649.tc?mn=2391&pageIndex=1&"
            "pageNo=1649&paramIdx=&eduNo=-1&searchInstNo=1&srchCtgryCd=&"
            "srchLlPrgrmCd=&srchRgnCd=&srchEduNm="
        ),
        "decision": "exclude_complete_duplicate_of_87106AA0_town_scope",
    },
}

GYEONGSAN_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "url": "https://www.gbgs.go.kr/lll/page/link.tc?mn=2398&pageNo=1651",
        "decision": "separate_hayang_culture_hall_course_owner",
    },
    {
        "url": "https://www.gbgs.go.kr/lll/page/link.tc?mn=2397&pageNo=1652",
        "decision": "separate_civic_hall_course_owner",
    },
    {
        "url": "https://www.gbgs.go.kr/lll/page/link.tc?mn=2396&pageNo=1654",
        "decision": "separate_samseonghyeon_history_culture_owner",
    },
    {
        "url": "https://www.gbgs.go.kr/lll/page/link.tc?mn=2395&pageNo=1664",
        "decision": "separate_city_museum_course_owner",
    },
    {
        "url": "https://www.gbgs.go.kr/lll/page/link.tc?mn=2394&pageNo=1665",
        "decision": "separate_agricultural_technology_course_owner",
    },
    {
        "url": "https://www.gbgs.go.kr/swimmingpool/course/index.do",
        "decision": "separate_swimming_pool_course_owner",
    },
)

GYEONGSAN_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "cutoff": "2026-07-23",
    "ledger_source_counts": {"town": 432, "program": 105, "women": 110},
    "ledger_page_counts": {"town": 29, "program": 7, "women": 8},
    "ledger_current_counts": {"town": 203, "program": 46, "women": 55},
    "ledger_first_identities": {"town": "10837", "program": "10757", "women": "10664"},
    "ledger_final_identities": {"town": "10023", "program": "5990", "women": "10398"},
    "ledger_final_page_counts": {"town": 12, "program": 15, "women": 5},
    "ledger_post_last_pages": {"town": 30, "program": 8, "women": 9},
    "full_identity_sha256": {
        "town": "f42ecb93ae53a3fbbcf3be5701053518759f1634b3a1443ca7c99d03502009ca",
        "program": "dba56d063f62d4df5406fe801923c29a49494c1307b18ae6606fe6ac28384b48",
        "women": "7197927ebe8930305b9249e80d1494d5f33f7f641102792e67a3b928d95ae7bb",
    },
    "current_identity_sha256": {
        "town": "b9b4319fad41cd05ce728ccd224d3248da1f93d6f8a1a7fc8fee4e5223963908",
        "program": "75e7d3fd9887463f6f74c189194f9af370d15a8dd17a7e89b670450287739f18",
        "women": "681e464cd11c7a376b1813bb0d1fe13d878451a9f49b6c2913de268e9a1c7598",
    },
    "current_total": 304,
    "status_counts": {"SCHEDULED": 278, "OPEN": 3, "CLOSED": 23},
    "application_controls": 3,
    "town_branch_counts": {
        "남부동": 9,
        "남산면": 9,
        "동부동": 21,
        "서부1동": 33,
        "서부2동": 20,
        "압량읍": 18,
        "용성면": 9,
        "자인면": 11,
        "중방동": 12,
        "중앙동": 15,
        "진량읍": 41,
        "하양읍": 5,
    },
    "program_branch_counts": {
        "LH친구작은도서관": 1,
        "경산시 평생학습관": 26,
        "도율한문교습소": 1,
        "든든노인상담교육연구소": 1,
        "로뎀공방": 1,
        "미트웍스": 1,
        "별마루": 1,
        "빛담": 1,
        "쁘니공방": 1,
        "소담공방": 1,
        "연담": 1,
        "예나공방": 1,
        "우담퀼트": 1,
        "원혜힐링아트": 1,
        "위올": 1,
        "지수화": 1,
        "짓다": 1,
        "체험농장 팜더랑": 1,
        "치유농장 연원당": 1,
        "한설차문화원": 1,
        "향만가": 1,
    },
    "women_branch_counts": {"경산시 여성회관": 55},
    "town_provider_requests": {"list": 60, "detail": 203, "total": 263},
    "program_provider_requests": {"list": 34, "detail": 101, "total": 135},
    "combined_requests_per_snapshot": 398,
    "two_snapshot_requests": 796,
}


class GyeongsanContractError(ValueError):
    """Raised when the audited official-source contract no longer holds."""


@dataclass(frozen=True)
class _ListedCourse:
    ledger_key: str
    page: int
    sequence: int
    edu_no: str
    source_title: str
    title: str
    branch: str
    branch_code: str
    event_start: date
    event_end: date
    apply_start: date
    apply_end: date
    period: str
    apply_period: str
    schedule: str
    venue: str
    fee: str
    capacity_current: Optional[int]
    capacity_total: Optional[int]
    wait_current: Optional[int]
    wait_total: Optional[int]
    source_status: str
    status: str
    reservation_available: bool


@dataclass(frozen=True)
class _Page:
    requested_page: int
    advertised_last_page: int
    advertised_total: int
    rows: tuple[_ListedCourse, ...]
    exact_empty: bool


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_DETAIL_ONCLICK_RE = re.compile(r"^fnDetail\('(\d+)'\);$")
_PAGE_ONCLICK_RE = re.compile(r"^pageMove\((\d+)\); return false;$")
_NO_DATA_ONCLICK_RE = re.compile(r"^alert\('등록된 자료가 없습니다\.'\)$")
_CAPACITY_RE = re.compile(
    r"^신청/정원\s*:\s*([\d,]+)\s*/\s*([\d,]+)명,\s*"
    r"후보/정원\s*:\s*([\d,]+)\s*/\s*([\d,]+)명$"
)
_DETAIL_CAPACITY_RE = re.compile(
    r"^([\d,]+)명\s*/\s*([\d,]+)명\s*\((신청|후보)/정원\)$"
)
_TOWN_TITLE_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
_PROGRAM_BRANCH_RE = re.compile(r"\[\s*배움터\s*:\s*([^\]]+)\]")
_DETAIL_ID_RE = re.compile(r'var\s+param\s*=\s*"\?eduNo=(\d+)"')
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_STATUS_CONTRACT: Mapping[str, tuple[str, tuple[str, ...], tuple[str, ...], bool]] = {
    "접수대기": ("SCHEDULED", ("receipt_gray",), ("acceptable",), False),
    "접수중": ("OPEN", ("receipt_green",), ("acceptable",), True),
    "신청마감": ("CLOSED", ("receipt_dark",), (), False),
}
_DETAIL_FIELD_SHAPES = frozenset(
    {
        ("교육대상", "교육방법", "수강시간", "강의계획서", "기타금액안내", "담당자명", "문의처"),
        (
            "교육대상",
            "교육방법",
            "유의사항",
            "수강시간",
            "강의계획서",
            "기타금액안내",
            "담당자명",
            "문의처",
        ),
        (
            "교육대상",
            "교육방법",
            "기타",
            "유의사항",
            "수강시간",
            "강의계획서",
            "기타금액안내",
            "담당자명",
            "문의처",
        ),
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "contact",
        "contacts",
        "phone",
        "email",
        "instructor",
        "instructor_name",
        "attachments",
        "attachment_urls",
        "application_endpoint",
        "source_html",
        "raw_html",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def is_gyeongsan_education_target(target: Any) -> bool:
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    if provider == GYEONGSAN_TOWN_PROVIDER:
        return url in {
            GYEONGSAN_TOWN_LEGACY_URL,
            GYEONGSAN_TOWN_BARE_URL,
            GYEONGSAN_TOWN_CANONICAL_URL,
        }
    if provider == GYEONGSAN_PROGRAM_PROVIDER:
        return url in {
            GYEONGSAN_PROGRAM_CANONICAL_URL,
            GYEONGSAN_WOMEN_CANDIDATE_URL,
            GYEONGSAN_WOMEN_CANONICAL_URL,
        }
    return False


is_target = is_gyeongsan_education_target


def _owned_ledgers(provider: str) -> tuple[GyeongsanLedger, ...]:
    if provider == GYEONGSAN_TOWN_PROVIDER:
        return (GYEONGSAN_TOWN_LEDGER,)
    if provider == GYEONGSAN_PROGRAM_PROVIDER:
        return (GYEONGSAN_PROGRAM_LEDGER, GYEONGSAN_WOMEN_LEDGER)
    raise GyeongsanContractError("unknown Gyeongsan owner provider")


def _query_pairs(ledger: GyeongsanLedger, page: int, edu_no: str = "-1") -> tuple[tuple[str, str], ...]:
    return (
        ("mn", ledger.mn),
        ("pageIndex", str(page)),
        ("pageNo", ledger.page_no),
        ("paramIdx", ""),
        ("eduNo", edu_no),
        ("searchInstNo", ledger.search_inst_no),
        ("srchCtgryCd", ledger.category),
        ("srchLlPrgrmCd", ""),
        ("srchRgnCd", ""),
        ("srchEduNm", ""),
    )


def _list_url(ledger: GyeongsanLedger, page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("invalid Gyeongsan page")
    return f"https://{GYEONGSAN_HOST}{ledger.path}?{urlencode(_query_pairs(ledger, page))}"


def _detail_url(ledger: GyeongsanLedger, page: int, edu_no: str) -> str:
    identity = _clean(edu_no)
    if _IDENTITY_RE.fullmatch(identity) is None:
        raise ValueError("invalid Gyeongsan course identity")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("invalid Gyeongsan source page")
    return (
        f"https://{GYEONGSAN_HOST}{GYEONGSAN_DETAIL_PATH}?"
        f"{urlencode(_query_pairs(ledger, page, identity))}"
    )


def _decode_allowed_url(url: str) -> tuple[GyeongsanLedger, int, str]:
    try:
        parsed = urlparse(url)
        pairs = tuple(parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True))
        port = parsed.port
    except ValueError as exc:
        raise GyeongsanContractError("malformed request URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != GYEONGSAN_HOST
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise GyeongsanContractError("request left the audited official host")
    ledger = next((item for item in GYEONGSAN_LEDGERS if item.path == parsed.path), None)
    is_detail = parsed.path == GYEONGSAN_DETAIL_PATH
    if ledger is None and is_detail:
        if len(pairs) != 10:
            raise GyeongsanContractError("detail query shape changed")
        ledger = next(
            (
                item
                for item in GYEONGSAN_LEDGERS
                if pairs[0][1] == item.mn
                and pairs[2][1] == item.page_no
                and pairs[5][1] == item.search_inst_no
                and pairs[6][1] == item.category
            ),
            None,
        )
    if ledger is None:
        raise GyeongsanContractError("request path is outside the list/detail allow-list")
    if len(pairs) != 10 or pairs[1][0] != "pageIndex" or not pairs[1][1].isdigit():
        raise GyeongsanContractError("request pagination query changed")
    page = int(pairs[1][1])
    if page < 1:
        raise GyeongsanContractError("request page must be positive")
    edu_no = pairs[4][1]
    if is_detail:
        if _IDENTITY_RE.fullmatch(edu_no) is None:
            raise GyeongsanContractError("detail identity query changed")
    elif edu_no != "-1":
        raise GyeongsanContractError("list request contains a detail identity")
    expected = _query_pairs(ledger, page, edu_no)
    if pairs != expected:
        raise GyeongsanContractError("request query left the audited full-ledger scope")
    return ledger, page, edu_no


def _raw_session() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return value


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _increment(meta: dict[str, Any], key: str, lock: Lock) -> None:
    with lock:
        meta[key] += 1


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
    lock: Lock,
) -> BeautifulSoup:
    _decode_allowed_url(url)
    _increment(meta, "source_requests", lock)
    last_error: Optional[Exception] = None
    for _ in range(GYEONGSAN_FETCH_ATTEMPTS):
        _increment(meta, "request_attempts", lock)
        try:
            response = fetcher(session, url, timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            if int(getattr(response, "status_code", 200)) != 200:
                raise GyeongsanContractError("unexpected HTTP status")
            if getattr(response, "history", None):
                raise GyeongsanContractError("redirect is not allowed")
            final_url = _clean(getattr(response, "url", url)) or url
            if final_url != url:
                raise GyeongsanContractError("response URL changed")
            _decode_allowed_url(final_url)
            headers = getattr(response, "headers", {}) or {}
            content_type = _clean(headers.get("content-type") or headers.get("Content-Type"))
            if content_type and (
                "text/html" not in content_type.lower()
                or "utf-8" not in content_type.lower()
            ):
                raise GyeongsanContractError("official page is no longer UTF-8 HTML")
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", response)).encode("utf-8")
            if not isinstance(content, (bytes, bytearray)):
                content = bytes(content)
            if not content or len(content) > GYEONGSAN_MAX_HTML_BYTES:
                raise GyeongsanContractError("empty or oversized official HTML")
            try:
                html = bytes(content).decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise GyeongsanContractError("official page is no longer strict UTF-8") from exc
            return BeautifulSoup(html, "html.parser")
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise GyeongsanContractError("official page fetch failed")


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Asia/Seoul")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("today must be an ISO date")


def _date_range(value: str, label: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise GyeongsanContractError(f"{label}: exact two-date range missing")
    try:
        start, end = (date.fromisoformat(item) for item in matches)
    except ValueError as exc:
        raise GyeongsanContractError(f"{label}: invalid date") from exc
    if end < start:
        raise GyeongsanContractError(f"{label}: reversed date range")
    return start, end


def _owner_contract(soup: BeautifulSoup, ledger: GyeongsanLedger) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    expected = f"경산시 평생학습관 > 교육신청 > {ledger.menu_name}"
    if title != expected:
        raise GyeongsanContractError(f"{ledger.key}: official title changed: {title}")
    text = _clean(soup.get_text(" ", strip=True))
    if (
        "경산시 평생학습관" not in text
        or "경산시 남매로 159, 경산시청 2별관 1층 교육도시과" not in text
        or "GYEONGSAN LIFELONG LEARNING CENTER" not in text
    ):
        raise GyeongsanContractError(f"{ledger.key}: official owner evidence missing")


def _filter_registry(soup: BeautifulSoup, ledger: GyeongsanLedger) -> None:
    values: list[tuple[str, str, str, bool]] = []
    for node in soup.select("form#form1 ul.com_tab > li"):
        anchors = node.select(":scope > a[href]")
        if len(anchors) != 1:
            raise GyeongsanContractError(f"{ledger.key}: filter anchor changed")
        href = _clean(anchors[0].get("href"))
        match = re.fullmatch(r"javascript:(fnSearch\w+)\('([^']*)'\);", href)
        if match is None:
            raise GyeongsanContractError(f"{ledger.key}: filter action changed")
        values.append(
            (
                match.group(1),
                match.group(2),
                _clean(anchors[0].get_text(" ", strip=True)),
                "active" in (node.get("class") or ()),
            )
        )
    if ledger.key == "town":
        expected = tuple(("fnSearchRgnCode", code, label, code == "") for code, label in GYEONGSAN_TOWN_REGIONS)
    elif ledger.key == "program":
        expected = tuple(("fnSearchLlPrgrm", code, label, False) for code, label in GYEONGSAN_PROGRAM_FILTERS)
    else:
        expected = tuple(("fnSearchCategory", code, label, False) for code, label in GYEONGSAN_WOMEN_FILTERS)
    if tuple(values) != expected:
        raise GyeongsanContractError(f"{ledger.key}: complete filter registry changed")


def _form_contract(soup: BeautifulSoup, ledger: GyeongsanLedger, page: int) -> None:
    forms = soup.select("form#form1[name='form1']")
    if (
        len(forms) != 1
        or _clean(forms[0].get("method")).lower() != "post"
        or _clean(forms[0].get("action"))
    ):
        raise GyeongsanContractError(f"{ledger.key}: list form changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in forms[0].select("input[type='hidden'][name]")
    }
    expected = dict(_query_pairs(ledger, page))
    expected.pop("srchEduNm")
    if hidden != expected:
        raise GyeongsanContractError(f"{ledger.key}: list hidden binding changed")
    keywords = forms[0].select("input#srchEduNm[name='srchEduNm'][type='text']")
    if len(keywords) != 1 or _clean(keywords[0].get("value")):
        raise GyeongsanContractError(f"{ledger.key}: keyword boundary changed")
    _filter_registry(soup, ledger)


def _pager_contract(soup: BeautifulSoup, page: int, has_rows: bool) -> int:
    pagers = soup.select(".pagenation")
    if len(pagers) != 1:
        raise GyeongsanContractError("course pagination wrapper changed")
    values = {1}
    for anchor in pagers[0].select("a[onclick]"):
        match = _PAGE_ONCLICK_RE.fullmatch(_clean(anchor.get("onclick")))
        if match is None:
            raise GyeongsanContractError("course pagination action changed")
        values.add(int(match.group(1)))
    active = pagers[0].select("a.active[title]")
    if has_rows:
        if (
            len(active) != 1
            or _clean(active[0].get_text(" ", strip=True)) != str(page)
            or _clean(active[0].get("title")) != f"현재페이지{page}"
            or _clean(active[0].get("href")) != "javascript:;"
        ):
            raise GyeongsanContractError(f"page {page}: active pagination binding changed")
    elif active:
        raise GyeongsanContractError("post-last page unexpectedly has an active page")
    return max(values)


def _label_value(node: Any, label: str, *, allow_empty: bool = False) -> str:
    value = _clean(node.get_text(" ", strip=True))
    match = re.fullmatch(rf"{re.escape(label)}\s*:\s*(.*)", value)
    if match is None:
        raise GyeongsanContractError(f"list field {label} changed")
    result = _clean(match.group(1))
    if not result and not allow_empty:
        raise GyeongsanContractError(f"list field {label} is empty")
    return result


def _branch(ledger: GyeongsanLedger, source_title: str) -> tuple[str, str, str]:
    if ledger.key == "town":
        match = _TOWN_TITLE_RE.fullmatch(source_title)
        allowed = {label: code for code, label in GYEONGSAN_TOWN_REGIONS if code}
        if match is None or match.group(1) not in allowed or not _clean(match.group(2)):
            raise GyeongsanContractError("town title/branch binding changed")
        name = match.group(1)
        return _clean(match.group(2)), name, f"GYEONGSAN_TOWN_{allowed[name]}"
    if ledger.key == "program":
        match = _PROGRAM_BRANCH_RE.search(source_title)
        name = _clean(match.group(1)) if match else "경산시 평생학습관"
        suffix = sha1(name.encode("utf-8")).hexdigest()[:10].upper()
        return source_title, name, f"GYEONGSAN_PROGRAM_{suffix}"
    return source_title, "경산시 여성회관", "GYEONGSAN_WOMEN_CENTER"


def _parse_status(card: Any, link: Any, edu_no: str) -> tuple[str, str, bool]:
    status_node = card.find_next_sibling("li")
    if status_node is None:
        raise GyeongsanContractError(f"course {edu_no}: status sibling missing")
    source = _clean(status_node.get_text(" ", strip=True))
    contract = _STATUS_CONTRACT.get(source)
    if contract is None:
        raise GyeongsanContractError(f"course {edu_no}: unknown status {source}")
    status, status_classes, link_classes, available = contract
    allowed_link_classes = {link_classes}
    if source == "신청마감":
        # A capacity-full course can retain the site's generic detail-link
        # class while its source state and detail page expose no application.
        allowed_link_classes.add(("acceptable",))
    if (
        tuple(status_node.get("class") or ()) != status_classes
        or tuple(link.get("class") or ()) not in allowed_link_classes
        or _clean(link.get("href")) != "javascript:;"
    ):
        raise GyeongsanContractError(f"course {edu_no}: status control contract changed")
    return source, status, available


def _parse_card(card: Any, ledger: GyeongsanLedger, page: int, sequence: int) -> _ListedCourse:
    if re.fullmatch(r"data\d+", _clean(card.get("id"))) is None:
        raise GyeongsanContractError(f"{ledger.key} page {page}: source ordinal changed")
    links = card.find_all("a", recursive=False)
    if len(links) != 1:
        raise GyeongsanContractError(f"{ledger.key} page {page}: course link changed")
    link = links[0]
    identity = _DETAIL_ONCLICK_RE.fullmatch(_clean(link.get("onclick")))
    if identity is None:
        raise GyeongsanContractError(f"{ledger.key} page {page}: detail identity changed")
    edu_no = identity.group(1)
    outer = link.find_all("li", recursive=False)
    if len(outer) != 5:
        raise GyeongsanContractError(f"course {edu_no}: card field count changed")
    source_title = _clean(outer[0].get_text(" ", strip=True))
    title, branch, branch_code = _branch(ledger, source_title)
    period_items = outer[1].select(":scope > ul.list_item.bf_img1 > li")
    if len(period_items) != 3:
        raise GyeongsanContractError(f"course {edu_no}: period fields changed")
    period = _label_value(period_items[0], "수강기간")
    schedule = _label_value(period_items[1], "수강시간", allow_empty=True)
    apply_period = _label_value(period_items[2], "접수기간")
    event_start, event_end = _date_range(period, f"course {edu_no} event")
    apply_start, apply_end = _date_range(apply_period, f"course {edu_no} application")
    capacities = outer[2].select(":scope > ul.list_item.bf_img2 > li")
    if len(capacities) != 1:
        raise GyeongsanContractError(f"course {edu_no}: capacity field changed")
    capacity_text = _clean(capacities[0].get_text(" ", strip=True))
    capacity_match = _CAPACITY_RE.fullmatch(capacity_text)
    if capacity_text in {"", "신청/정원 : -"}:
        capacity_current = capacity_total = wait_current = wait_total = None
    elif capacity_match is not None:
        capacity_current, capacity_total, wait_current, wait_total = (
            int(value.replace(",", "")) for value in capacity_match.groups()
        )
    else:
        raise GyeongsanContractError(f"course {edu_no}: capacity vocabulary changed")
    venue = _label_value(outer[3], "교육장소", allow_empty=True)
    fee = _label_value(outer[4], "수강료")
    if not source_title or not title or not fee:
        raise GyeongsanContractError(f"course {edu_no}: required public list field missing")
    source_status, status, available = _parse_status(card, link, edu_no)
    public = " ".join((source_title, schedule, venue, fee))
    if _PHONE_RE.search(public) or _EMAIL_RE.search(public) or _RESIDENT_RE.search(public):
        raise GyeongsanContractError(f"course {edu_no}: list fields contain contact/PII")
    return _ListedCourse(
        ledger.key,
        page,
        sequence,
        edu_no,
        source_title,
        title,
        branch,
        branch_code,
        event_start,
        event_end,
        apply_start,
        apply_end,
        period,
        apply_period,
        schedule,
        venue,
        fee,
        capacity_current,
        capacity_total,
        wait_current,
        wait_total,
        source_status,
        status,
        available,
    )


def _parse_page(soup: BeautifulSoup, ledger: GyeongsanLedger, page: int) -> _Page:
    _owner_contract(soup, ledger)
    _form_contract(soup, ledger, page)
    stats = soup.select("form#form1 .search_txt .num")
    if len(stats) != 2:
        raise GyeongsanContractError(f"{ledger.key}: total/page counters changed")
    total_text = _clean(stats[0].get_text(" ", strip=True)).replace(",", "")
    page_text = _clean(stats[1].get_text(" ", strip=True)).replace(",", "")
    if not total_text.isdigit() or page_text != str(page):
        raise GyeongsanContractError(f"{ledger.key}: total/page counter binding changed")
    total = int(total_text)
    cards = soup.select("form#form1 .edu_content > ul.content_list")
    data_cards: list[Any] = []
    empty_cards: list[Any] = []
    for card in cards:
        direct = card.find_all("a", recursive=False)
        if len(direct) == 1 and _DETAIL_ONCLICK_RE.fullmatch(_clean(direct[0].get("onclick"))):
            data_cards.append(card)
        else:
            empty_cards.append(card)
    if data_cards:
        if empty_cards or len(data_cards) > GYEONGSAN_PAGE_SIZE:
            raise GyeongsanContractError(f"{ledger.key} page {page}: rows/empty boundary changed")
        rows = tuple(
            _parse_card(card, ledger, page, index)
            for index, card in enumerate(data_cards, 1)
        )
        exact_empty = False
    else:
        if len(empty_cards) != 1:
            raise GyeongsanContractError(f"{ledger.key} page {page}: empty sentinel count changed")
        links = empty_cards[0].find_all("a", recursive=False)
        if (
            len(links) != 1
            or _clean(links[0].get("href")) != "javascript:;"
            or _NO_DATA_ONCLICK_RE.fullmatch(_clean(links[0].get("onclick"))) is None
            or _clean(links[0].get_text(" ", strip=True)) != "자료가 없습니다."
        ):
            raise GyeongsanContractError(f"{ledger.key} page {page}: exact empty sentinel changed")
        rows = ()
        exact_empty = True
    identities = [row.edu_no for row in rows]
    if len(identities) != len(set(identities)):
        raise GyeongsanContractError(f"{ledger.key} page {page}: duplicate course identity")
    return _Page(page, _pager_contract(soup, page, bool(rows)), total, rows, exact_empty)


def _collect_pages(
    session: Any,
    ledger: GyeongsanLedger,
    timeout: int,
    max_pages: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
    lock: Lock,
) -> tuple[list[_ListedCourse], int, int]:
    def fetch(page: int) -> _Page:
        _increment(meta, "list_requests", lock)
        soup = _fetch_soup(session, _list_url(ledger, page), timeout, fetcher, meta, lock)
        return _parse_page(soup, ledger, page)

    first = fetch(1)
    last = first.advertised_last_page
    if last < 1 or last > max_pages:
        meta["source_cap_reached"] = True
        raise GyeongsanContractError(
            f"{ledger.key}: advertised last page {last} exceeds max_pages={max_pages}"
        )
    if not first.rows:
        if first.advertised_total != 0 or last != 1:
            raise GyeongsanContractError(f"{ledger.key}: empty first page has nonempty totals")
        return [], 1, 0
    pages = {1: first}
    for page in range(2, last + 1):
        current = fetch(page)
        if current.advertised_last_page != last or current.advertised_total != first.advertised_total:
            raise GyeongsanContractError(f"{ledger.key} page {page}: pagination totals changed")
        pages[page] = current
    for page, current in pages.items():
        if page < last and len(current.rows) != GYEONGSAN_PAGE_SIZE:
            raise GyeongsanContractError(f"{ledger.key} page {page}: premature short page")
        if page == last and not current.rows:
            raise GyeongsanContractError(f"{ledger.key}: advertised final page is empty")
    sentinel = fetch(last + 1)
    _increment(meta, "post_last_requests", lock)
    if (
        sentinel.rows
        or not sentinel.exact_empty
        or sentinel.advertised_last_page != last
        or sentinel.advertised_total != first.advertised_total
    ):
        raise GyeongsanContractError(f"{ledger.key}: post-last page is not the exact empty boundary")
    rows = [row for page in sorted(pages) for row in pages[page].rows]
    identities = [row.edu_no for row in rows]
    if len(rows) != first.advertised_total:
        raise GyeongsanContractError(
            f"{ledger.key}: declared total {first.advertised_total} != parsed {len(rows)}"
        )
    if len(identities) != len(set(identities)):
        raise GyeongsanContractError(f"{ledger.key}: duplicate identity across pages")
    return rows, last, first.advertised_total


def _listed_signature(row: _ListedCourse) -> tuple[Any, ...]:
    return (
        row.ledger_key,
        row.page,
        row.sequence,
        row.edu_no,
        row.source_title,
        row.title,
        row.branch,
        row.event_start,
        row.event_end,
        row.apply_start,
        row.apply_end,
        row.schedule,
        row.venue,
        row.fee,
        row.capacity_current,
        row.capacity_total,
        row.wait_current,
        row.wait_total,
        row.source_status,
        row.status,
        row.reservation_available,
    )


def _ledger_signature(rows: Iterable[_ListedCourse]) -> tuple[Any, ...]:
    return tuple(_listed_signature(row) for row in rows)


def _detail_pairs(soup: BeautifulSoup, edu_no: str) -> tuple[dict[str, str], tuple[str, ...]]:
    tables = soup.select("form#form1 .text_detail table.com_table tbody")
    if len(tables) != 1:
        raise GyeongsanContractError(f"course {edu_no}: detail information table changed")
    output: dict[str, str] = {}
    shape: list[str] = []
    for row in tables[0].find_all("tr", recursive=False):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) != 2 or cells[0].name != "th" or cells[1].name != "td":
            raise GyeongsanContractError(f"course {edu_no}: detail row shape changed")
        key = _compact(cells[0].get_text(" ", strip=True))
        if not key or key in output:
            raise GyeongsanContractError(f"course {edu_no}: duplicate/empty detail field")
        output[key] = _clean(cells[1].get_text(" ", strip=True))
        shape.append(key)
    if tuple(shape) not in _DETAIL_FIELD_SHAPES:
        raise GyeongsanContractError(f"course {edu_no}: detail field vocabulary changed")
    return output, tuple(shape)


def _core_pairs(soup: BeautifulSoup, edu_no: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for node in soup.select("form#form1 .img_jb .right .com3"):
        label = node.find("p", recursive=False)
        value = node.find("span", recursive=False)
        if label is None or value is None:
            continue
        key = _compact(label.get_text(" ", strip=True))
        if not key or key in output:
            raise GyeongsanContractError(f"course {edu_no}: duplicate core detail field")
        output[key] = _clean(value.get_text(" ", strip=True))
    expected = {"신청일정", "교육일정", "교육장소", "수강료"}
    allowed = expected | {"신청현황", "후보현황", "연령제한"}
    if (
        not expected.issubset(output)
        or not set(output).issubset(allowed)
        or (("신청현황" in output) != ("후보현황" in output))
    ):
        raise GyeongsanContractError(f"course {edu_no}: core detail vocabulary changed")
    return output


def _detail_capacity(value: str, kind: str, edu_no: str) -> tuple[int, int]:
    match = _DETAIL_CAPACITY_RE.fullmatch(_clean(value))
    if match is None or match.group(3) != kind:
        raise GyeongsanContractError(f"course {edu_no}: {kind} detail capacity changed")
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


def _detail_form_contract(soup: BeautifulSoup, listed: _ListedCourse, ledger: GyeongsanLedger) -> None:
    forms = soup.select("form#form1[name='form1']")
    if (
        len(forms) != 1
        or _clean(forms[0].get("method")).lower() != "post"
        or _clean(forms[0].get("action"))
    ):
        raise GyeongsanContractError(f"course {listed.edu_no}: detail form changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in forms[0].select("input[type='hidden'][name]")
    }
    expected = dict(_query_pairs(ledger, listed.page))
    expected.pop("eduNo")
    if hidden != expected:
        raise GyeongsanContractError(f"course {listed.edu_no}: detail search binding changed")
    scripts = "\n".join(node.get_text() for node in soup.select("script"))
    identities = _DETAIL_ID_RE.findall(scripts)
    if identities != [listed.edu_no]:
        raise GyeongsanContractError(f"course {listed.edu_no}: detail JS identity changed")


def _detail_controls(soup: BeautifulSoup, listed: _ListedCourse) -> None:
    actual = tuple(
        (
            _clean(node.get_text(" ", strip=True)),
            _clean(node.get("href")),
            _clean(node.get("onclick")),
            tuple(node.get("class") or ()),
        )
        for node in soup.select("form#form1 .bot_btn > a")
    )
    listing = (
        "목록으로",
        "javascript:;",
        "selectList()",
        ("com_btn", "button2", "bg_gray"),
    )
    application = (
        "신청하기",
        "javascript:;",
        "fnRequest()",
        ("com_btn", "button2", "bg_green"),
    )
    expected = (application, listing) if listed.reservation_available else (listing,)
    if actual != expected:
        raise GyeongsanContractError(f"course {listed.edu_no}: application control changed")


def _parse_detail(
    target: Any,
    provider: str,
    listed: _ListedCourse,
    soup: BeautifulSoup,
) -> dict[str, Any]:
    ledger = _LEDGER_BY_KEY[listed.ledger_key]
    _owner_contract(soup, ledger)
    _detail_form_contract(soup, listed, ledger)
    title_nodes = soup.select("form#form1 .img_jb .right .g_name")
    title = _clean(title_nodes[0].get_text(" ", strip=True)) if len(title_nodes) == 1 else ""
    if title != listed.title:
        raise GyeongsanContractError(f"course {listed.edu_no}: list/detail title mismatch")
    core = _core_pairs(soup, listed.edu_no)
    detail_apply = _date_range(core["신청일정"], f"course {listed.edu_no} detail application")
    detail_event = _date_range(core["교육일정"], f"course {listed.edu_no} detail event")
    if (
        detail_apply != (listed.apply_start, listed.apply_end)
        or detail_event != (listed.event_start, listed.event_end)
        or core["교육장소"] != listed.venue
        or not listed.fee.startswith(core["수강료"])
    ):
        raise GyeongsanContractError(f"course {listed.edu_no}: list/detail core fields disagree")
    has_counts = "신청현황" in core or "후보현황" in core
    if has_counts and not {"신청현황", "후보현황"}.issubset(core):
        raise GyeongsanContractError(f"course {listed.edu_no}: partial detail capacity")
    detail_current = detail_total = detail_wait_current = detail_wait_total = None
    if has_counts:
        detail_current, detail_total = _detail_capacity(core["신청현황"], "신청", listed.edu_no)
        detail_wait_current, detail_wait_total = _detail_capacity(core["후보현황"], "후보", listed.edu_no)
    if listed.capacity_total is not None and (
        (detail_current, detail_total) != (listed.capacity_current, listed.capacity_total)
        or (detail_wait_current, detail_wait_total) != (listed.wait_current, listed.wait_total)
    ):
        raise GyeongsanContractError(f"course {listed.edu_no}: list/detail capacity disagrees")
    fields, field_shape = _detail_pairs(soup, listed.edu_no)
    target_text = fields["교육대상"]
    schedule = fields["수강시간"]
    if not target_text or not schedule:
        raise GyeongsanContractError(f"course {listed.edu_no}: safe detail fields missing")
    _detail_controls(soup, listed)
    safe_text = " ".join(
        (listed.title, listed.branch, listed.venue, core["수강료"], target_text, schedule)
    )
    if _PHONE_RE.search(safe_text) or _EMAIL_RE.search(safe_text) or _RESIDENT_RE.search(safe_text):
        raise GyeongsanContractError(f"course {listed.edu_no}: persisted fields contain contact/PII")
    extra = _target_extra(target)
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:education:{listed.edu_no}",
        "title": listed.title,
        "description": listed.title,
        "branch": listed.branch,
        "branch_code": listed.branch_code,
        "source_branch": listed.branch,
        "preserve_branch": True,
        "branch_url": ledger.canonical_url,
        "branch_address": "경상북도 경산시",
        "raw_url": _detail_url(ledger, listed.page, listed.edu_no),
        "application_url": (
            _detail_url(ledger, listed.page, listed.edu_no)
            if listed.reservation_available
            else ""
        ),
        "application_type": (
            "ONLINE_LOGIN_REQUIRED"
            if listed.reservation_available
            else "INFO_ONLY_DISABLED_SOURCE_CONTROL"
        ),
        "reservation_available": listed.reservation_available,
        "status": listed.status,
        "raw_status": listed.source_status,
        "period": listed.period,
        "apply_period": listed.apply_period,
        "schedule_raw": schedule,
        "start_date": listed.event_start.isoformat(),
        "end_date": listed.event_end.isoformat(),
        "apply_start_date": listed.apply_start.isoformat(),
        "apply_end_date": listed.apply_end.isoformat(),
        "target": target_text,
        "capacity": detail_total if detail_total is not None else listed.capacity_total,
        "capacity_total": detail_total if detail_total is not None else listed.capacity_total,
        "capacity_current": detail_current if detail_current is not None else listed.capacity_current,
        "waitlist_total": detail_wait_total if detail_wait_total is not None else listed.wait_total,
        "waitlist_current": (
            detail_wait_current if detail_wait_current is not None else listed.wait_current
        ),
        "fee": core["수강료"],
        "venue_name": listed.venue,
        "room": listed.venue,
        "address": "경상북도 경산시",
        "venue_address": "",
        "category": (
            "읍면동학습관"
            if listed.ledger_key == "town"
            else "여성회관"
            if listed.ledger_key == "women"
            else "평생학습 프로그램"
        ),
        "collection_category": _clean(extra.get("collection_category") or "평생학습"),
        "domain_category": _clean(extra.get("domain_category") or "교육·강좌"),
        "operator_type": _clean(extra.get("operator_type") or "지자체/공공기관"),
        "source_group": _clean(extra.get("source_group") or "lifelong_learning"),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "complete_static_html+all_current_detail_html",
        "program_type": "교육",
        "municipality_code": GYEONGSAN_MUNICIPALITY_CODE,
        "municipality_name": GYEONGSAN_MUNICIPALITY_NAME,
        "municipality_full_name": GYEONGSAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": GYEONGSAN_PARSER,
            "identity": listed.edu_no,
            "ledger": listed.ledger_key,
            "source_page": listed.page,
            "source_title": listed.source_title,
            "source_status": listed.source_status,
            "source_schedule": listed.schedule,
            "detail_field_shape": list(field_shape),
            "detail_verified": True,
            "list_detail_binding": "eduNo+title+dates+venue+capacity_when_declared",
            "application_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "instructor_endpoint_fetched": False,
            "pii_endpoint_fetched": False,
            "discarded_fields": [
                "교육방법",
                "기타",
                "유의사항",
                "강의계획서",
                "기타 금액안내",
                "담당자명",
                "문의처",
            ],
        },
    }


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors = [f"forbidden key {key}" for key in _FORBIDDEN_ROW_KEYS if key in row]
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping):
        return [*errors, "raw_fields missing"]
    values = [_clean(value) for value in row.values() if isinstance(value, str)]
    values.extend(_clean(value) for value in raw.values() if isinstance(value, str))
    serialized = " ".join(values)
    if _PHONE_RE.search(serialized) or _EMAIL_RE.search(serialized) or _RESIDENT_RE.search(serialized):
        errors.append("persisted row contains contact/PII")
    forbidden_tokens = (
        "/lll/edu/request.tc",
        "/lll/edu/instr/detail.json",
        "/lll/jfile/readFile.tc",
        "/lll/login/",
    )
    if any(token.lower() in serialized.lower() for token in forbidden_tokens):
        errors.append("unsafe endpoint persisted")
    return errors


def _initial_meta(provider: str = "") -> dict[str, Any]:
    return {
        "municipality_code": GYEONGSAN_MUNICIPALITY_CODE,
        "municipality_full_name": GYEONGSAN_MUNICIPALITY_NAME,
        "owner_provider": provider,
        "canonical_providers": list(GYEONGSAN_PROVIDERS),
        "canonical_urls": {
            "town": GYEONGSAN_TOWN_CANONICAL_URL,
            "program": GYEONGSAN_PROGRAM_CANONICAL_URL,
            "women": GYEONGSAN_WOMEN_CANONICAL_URL,
        },
        "provider_scope": {
            GYEONGSAN_TOWN_PROVIDER: ["town"],
            GYEONGSAN_PROGRAM_PROVIDER: ["program", "women"],
        },
        "excluded_duplicate_provider": GYEONGSAN_DUPLICATE_TOWN_PROVIDER,
        "candidate_audit": {key: dict(value) for key, value in GYEONGSAN_CANDIDATE_AUDIT.items()},
        "owner_boundaries": [dict(value) for value in GYEONGSAN_OWNER_BOUNDARIES],
        "live_audit_baseline": dict(GYEONGSAN_LIVE_AUDIT_BASELINE),
        "parser": GYEONGSAN_PARSER,
        "recommended_max_pages": GYEONGSAN_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": GYEONGSAN_RECOMMENDED_DETAIL_LIMIT,
        "recommended_detail_workers": GYEONGSAN_RECOMMENDED_DETAIL_WORKERS,
        "fetch_attempts": GYEONGSAN_FETCH_ATTEMPTS,
        "max_html_bytes": GYEONGSAN_MAX_HTML_BYTES,
        "source_requests": 0,
        "request_attempts": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "post_last_requests": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "returned_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "post_last_complete": False,
        "global_identity_disjoint": False,
        "details_complete": False,
        "full_ledgers_rechecked_after_details": False,
        "application_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "instructor_endpoints_called": 0,
        "pii_endpoints_called": 0,
        "privacy_violations": 0,
        "semantic_duplicate_count": 0,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "configured_collection_error": "",
    }


def collect_gyeongsan_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = GYEONGSAN_RECOMMENDED_MAX_PAGES,
    detail_limit: int = GYEONGSAN_RECOMMENDED_DETAIL_LIMIT,
    detail_workers: int = GYEONGSAN_RECOMMENDED_DETAIL_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic snapshot for the incumbent provider's owned ledgers."""

    provider = _clean(_target_value(target, "provider"))
    meta = _initial_meta(provider)
    if not is_gyeongsan_education_target(target):
        meta["configured_collection_error"] = "target does not match an exact Gyeongsan owner alias"
        return [], GYEONGSAN_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], GYEONGSAN_PARSER, meta
        session_factory = _raw_session
    try:
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
            or isinstance(detail_workers, bool)
            or not isinstance(detail_workers, int)
            or detail_workers < 1
            or detail_workers > 32
        ):
            raise ValueError("timeout/max_pages/detail_limit/detail_workers are invalid")
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], GYEONGSAN_PARSER, meta

    ledgers = _owned_ledgers(provider)
    meta["selected_ledgers"] = [ledger.key for ledger in ledgers]
    current_fetcher = fetcher or _default_fetcher
    session = session_factory()
    lock = Lock()
    try:
        source_by_ledger: dict[str, list[_ListedCourse]] = {}
        pages_by_ledger: dict[str, int] = {}
        totals_by_ledger: dict[str, int] = {}
        all_ids: set[str] = set()
        for ledger in ledgers:
            source, pages, total = _collect_pages(
                session,
                ledger,
                timeout,
                max_pages,
                current_fetcher,
                meta,
                lock,
            )
            ids = {row.edu_no for row in source}
            if len(ids) != len(source) or all_ids & ids:
                raise GyeongsanContractError("owned ledgers overlap or duplicate global eduNo identities")
            all_ids.update(ids)
            source_by_ledger[ledger.key] = source
            pages_by_ledger[ledger.key] = pages
            totals_by_ledger[ledger.key] = total
        meta["global_identity_disjoint"] = True
        source_rows = [row for ledger in ledgers for row in source_by_ledger[ledger.key]]
        current = [row for row in source_rows if row.event_end >= cutoff]
        if any(not row.schedule or not row.venue for row in current):
            raise GyeongsanContractError("a current course is missing schedule or venue")
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise GyeongsanContractError(
                f"detail_limit {detail_limit} below required {len(current)}"
            )
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "ledger_pages": dict(pages_by_ledger),
                "ledger_post_last_pages": {
                    key: value + 1 for key, value in pages_by_ledger.items()
                },
                "ledger_source_counts": dict(totals_by_ledger),
                "ledger_current_counts": {
                    ledger.key: sum(row.event_end >= cutoff for row in source_by_ledger[ledger.key])
                    for ledger in ledgers
                },
                "ledger_expired_counts": {
                    ledger.key: sum(row.event_end < cutoff for row in source_by_ledger[ledger.key])
                    for ledger in ledgers
                },
                "ledger_first_identities": {
                    ledger.key: source_by_ledger[ledger.key][0].edu_no
                    if source_by_ledger[ledger.key]
                    else ""
                    for ledger in ledgers
                },
                "ledger_final_identities": {
                    ledger.key: source_by_ledger[ledger.key][-1].edu_no
                    if source_by_ledger[ledger.key]
                    else ""
                    for ledger in ledgers
                },
                "ledger_final_page_counts": {
                    ledger.key: sum(
                        row.page == pages_by_ledger[ledger.key]
                        for row in source_by_ledger[ledger.key]
                    )
                    for ledger in ledgers
                },
                "ledger_identity_sha256": {
                    ledger.key: sha256(
                        "\n".join(row.edu_no for row in source_by_ledger[ledger.key]).encode()
                    ).hexdigest()
                    for ledger in ledgers
                },
                "ledger_current_identity_sha256": {
                    ledger.key: sha256(
                        "\n".join(
                            row.edu_no
                            for row in source_by_ledger[ledger.key]
                            if row.event_end >= cutoff
                        ).encode()
                    ).hexdigest()
                    for ledger in ledgers
                },
                "source_rows": len(source_rows),
                "source_identity_count": len(all_ids),
                "current_source_count": len(current),
                "expired_source_count": len(source_rows) - len(current),
                "source_status_counts": dict(Counter(row.source_status for row in source_rows)),
                "current_source_status_counts": dict(Counter(row.source_status for row in current)),
                "pagination_complete": True,
                "post_last_complete": True,
            }
        )

        def parse_one(listed: _ListedCourse) -> dict[str, Any]:
            ledger = _LEDGER_BY_KEY[listed.ledger_key]
            detail_session = session_factory()
            try:
                soup = _fetch_soup(
                    detail_session,
                    _detail_url(ledger, listed.page, listed.edu_no),
                    timeout,
                    current_fetcher,
                    meta,
                    lock,
                )
                return _parse_detail(target, provider, listed, soup)
            finally:
                close = getattr(detail_session, "close", None)
                if callable(close):
                    close()

        meta["detail_pages"] = len(current)
        if detail_workers == 1 or len(current) < 2:
            rows = [parse_one(listed) for listed in current]
        else:
            with ThreadPoolExecutor(max_workers=min(detail_workers, len(current))) as executor:
                rows = list(executor.map(parse_one, current))

        for ledger in ledgers:
            checked, checked_pages, checked_total = _collect_pages(
                session,
                ledger,
                timeout,
                max_pages,
                current_fetcher,
                meta,
                lock,
            )
            if (
                checked_pages != pages_by_ledger[ledger.key]
                or checked_total != totals_by_ledger[ledger.key]
                or _ledger_signature(checked) != _ledger_signature(source_by_ledger[ledger.key])
            ):
                raise GyeongsanContractError(
                    f"{ledger.key}: full ledger stability recheck changed"
                )
        meta["full_ledgers_rechecked_after_details"] = True

        rows.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("provider_course_id")),
            )
        )
        rows = list((dedupe_rows or _dedupe)(rows))
        expected_ids = {f"{provider}:education:{row.edu_no}" for row in current}
        if len(rows) != len(current) or {
            _clean(row.get("provider_course_id")) for row in rows
        } != expected_ids:
            raise GyeongsanContractError("dedupe changed the complete current identity set")
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        meta["privacy_violations"] = len(privacy_errors)
        if privacy_errors:
            raise GyeongsanContractError("; ".join(privacy_errors[:5]))
        semantic_counts = Counter(
            (
                _clean(row.get("title")).casefold(),
                _clean(row.get("branch")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
                _clean(row.get("venue_name")),
            )
            for row in rows
        )
        semantic_duplicates = sum(count - 1 for count in semantic_counts.values() if count > 1)
        meta["semantic_duplicate_count"] = semantic_duplicates
        if semantic_duplicates:
            raise GyeongsanContractError("semantic duplicate current courses detected")
        meta.update(
            {
                "returned_count": len(rows),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in rows)),
                "raw_status_counts": dict(Counter(_clean(row.get("raw_status")) for row in rows)),
                "branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
                "ledger_returned_counts": dict(
                    Counter(_clean(row.get("raw_fields", {}).get("ledger")) for row in rows)
                ),
                "application_control_count": sum(
                    bool(row.get("reservation_available")) for row in rows
                ),
                "actionable_application_count": sum(
                    bool(row.get("reservation_available")) for row in rows
                ),
                "details_complete": meta["detail_pages"] == len(current),
                "no_current_data": not rows,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return rows, GYEONGSAN_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
                "pagination_complete": False,
                "post_last_complete": False,
                "global_identity_disjoint": False,
                "details_complete": False,
                "full_ledgers_rechecked_after_details": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "returned_count": 0,
            }
        )
        return [], GYEONGSAN_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_gyeongsan_education


__all__ = [
    "GYEONGSAN_TOWN_PROVIDER",
    "GYEONGSAN_PROGRAM_PROVIDER",
    "GYEONGSAN_DUPLICATE_TOWN_PROVIDER",
    "GYEONGSAN_PROVIDERS",
    "GYEONGSAN_MUNICIPALITY_CODE",
    "GYEONGSAN_MUNICIPALITY_NAME",
    "GYEONGSAN_TOWN_LEDGER",
    "GYEONGSAN_PROGRAM_LEDGER",
    "GYEONGSAN_WOMEN_LEDGER",
    "GYEONGSAN_LEDGERS",
    "GYEONGSAN_TOWN_LEGACY_URL",
    "GYEONGSAN_TOWN_BARE_URL",
    "GYEONGSAN_TOWN_CANONICAL_URL",
    "GYEONGSAN_PROGRAM_CANONICAL_URL",
    "GYEONGSAN_WOMEN_CANDIDATE_URL",
    "GYEONGSAN_WOMEN_CANONICAL_URL",
    "GYEONGSAN_TOWN_REGIONS",
    "GYEONGSAN_PROGRAM_FILTERS",
    "GYEONGSAN_WOMEN_FILTERS",
    "GYEONGSAN_CANDIDATE_AUDIT",
    "GYEONGSAN_OWNER_BOUNDARIES",
    "GYEONGSAN_LIVE_AUDIT_BASELINE",
    "GYEONGSAN_RECOMMENDED_MAX_PAGES",
    "GYEONGSAN_RECOMMENDED_DETAIL_LIMIT",
    "GYEONGSAN_RECOMMENDED_DETAIL_WORKERS",
    "GYEONGSAN_PARSER",
    "GyeongsanContractError",
    "collect",
    "collect_gyeongsan_education",
    "is_target",
    "is_gyeongsan_education_target",
]
