"""Fail-closed collector for Daejeon Seo-gu's official program catalogues.

The district owns two public catalogue families on ``www.seogu.go.kr``:

* four disjoint lifelong-learning leaves; and
* the course lists of all five district libraries.

The landing pages and the single-course search candidates are aliases, not
additional stores.  A snapshot is returned only after the official landing
pages still expose the expected fan-out, every advertised page has been read,
the immediate post-last pages are empty, page one is stable on re-read, and
every current/future education or experience row passes its course-bound
detail contract.

The library tables also contain records which are explicitly experiences or
performances.  Experience rows are emitted with their own course-level Ops
category, while performances remain outside this collector.  Instructor
names, contacts, free-form descriptions, attachments, and source HTML are
never persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


DAEJEON_SEOGU_PROVIDER = "MUNI_WWW_SEOGU_GO_KR_E4434123"
DAEJEON_SEOGU_CANONICAL_CANDIDATE_ID = "MUNI_IR_08857C35A2C1"
DAEJEON_SEOGU_HOST = "www.seogu.go.kr"
DAEJEON_SEOGU_MUNICIPALITY_CODE = "3017000000"
DAEJEON_SEOGU_MUNICIPALITY_NAME = "대전광역시 서구"
DAEJEON_SEOGU_CANONICAL_PATH = "/learning/contents/learning/main.jsp"
DAEJEON_SEOGU_CANONICAL_URL = (
    f"https://{DAEJEON_SEOGU_HOST}{DAEJEON_SEOGU_CANONICAL_PATH}"
)
DAEJEON_SEOGU_LIFELONG_PATH = (
    "/learning/damoa/contents/learning/edu/01/edu.01.001.motion"
)
DAEJEON_SEOGU_LIFELONG_PAGE_SIZE = 12
DAEJEON_SEOGU_LIBRARY_PAGE_SIZE = 10
DAEJEON_SEOGU_FETCH_ATTEMPTS = 2
DAEJEON_SEOGU_MAX_WORKERS = 12
DAEJEON_SEOGU_MAX_HTML_BYTES = 4_000_000
DAEJEON_SEOGU_PARSER = (
    "daejeon_seogu_official_fanout+four_lifelong_leaves+five_libraries+"
    "all_pages+empty_sentinels+stable_rechecks+current_details+"
    "education_experience_performance_partition+pii_allowlist"
)
DAEJEON_SEOGU_OWNERSHIP_SCOPE = (
    "seogu_official_lifelong_and_five_library_course_catalogues"
)


@dataclass(frozen=True)
class DaejeonSeoguSource:
    key: str
    kind: str
    label: str
    branch: str
    path: str
    menu_code: str
    filter_code: str = ""
    library_slug: str = ""

    @property
    def page_size(self) -> int:
        return (
            DAEJEON_SEOGU_LIFELONG_PAGE_SIZE
            if self.kind == "lifelong"
            else DAEJEON_SEOGU_LIBRARY_PAGE_SIZE
        )

    @property
    def list_url(self) -> str:
        return daejeon_seogu_list_url(self.key, 1)

    @property
    def root_url(self) -> str:
        if not self.library_slug:
            return DAEJEON_SEOGU_CANONICAL_URL
        return (
            f"https://{DAEJEON_SEOGU_HOST}/library/"
            f"{self.library_slug}/index.do"
        )


DAEJEON_SEOGU_LIFELONG_SOURCES: tuple[DaejeonSeoguSource, ...] = (
    DaejeonSeoguSource(
        "lifelong_program",
        "lifelong",
        "평생학습관프로그램",
        "평생학습관프로그램",
        DAEJEON_SEOGU_LIFELONG_PATH,
        "MENU0100061",
        "01,02,03,04,05,06",
    ),
    DaejeonSeoguSource(
        "poomasi_school",
        "lifelong",
        "품앗이스쿨",
        "품앗이스쿨",
        DAEJEON_SEOGU_LIFELONG_PATH,
        "MENU0100070",
        "37",
    ),
    DaejeonSeoguSource(
        "seorami_university",
        "lifelong",
        "서람이자치대학",
        "서람이자치대학",
        DAEJEON_SEOGU_LIFELONG_PATH,
        "MENU0100063",
        "30",
    ),
    DaejeonSeoguSource(
        "special_lecture",
        "lifelong",
        "특강강좌",
        "특강강좌",
        DAEJEON_SEOGU_LIFELONG_PATH,
        "MENU0100092",
        "99",
    ),
)

DAEJEON_SEOGU_LIBRARY_SOURCES: tuple[DaejeonSeoguSource, ...] = (
    DaejeonSeoguSource(
        "library_galma",
        "library",
        "갈마도서관",
        "갈마도서관",
        "/library/galmalib/contents/learning/lib/02/lib.02.001.motion",
        "MENU0200030",
        library_slug="galmalib",
    ),
    DaejeonSeoguSource(
        "library_gasuwon",
        "library",
        "가수원도서관",
        "가수원도서관",
        "/library/gasuwonlib/contents/learning/lib/02/lib.02.001.motion",
        "MENU0300030",
        library_slug="gasuwonlib",
    ),
    DaejeonSeoguSource(
        "library_dunsan",
        "library",
        "둔산도서관",
        "둔산도서관",
        "/library/dunsanlib/contents/learning/lib/02/lib.02.001.motion",
        "MENU0400030",
        library_slug="dunsanlib",
    ),
    DaejeonSeoguSource(
        "library_wolpyeong",
        "library",
        "월평도서관",
        "월평도서관",
        "/library/wolpyeonglib/contents/learning/lib/02/lib.02.001.motion",
        "MENU0600030",
        library_slug="wolpyeonglib",
    ),
    DaejeonSeoguSource(
        "library_child",
        "library",
        "어린이도서관",
        "어린이도서관",
        "/library/childlib/contents/learning/lib/02/lib.02.001.motion",
        "MENU0500023",
        library_slug="childlib",
    ),
)

DAEJEON_SEOGU_SOURCES = (
    *DAEJEON_SEOGU_LIFELONG_SOURCES,
    *DAEJEON_SEOGU_LIBRARY_SOURCES,
)
DAEJEON_SEOGU_SOURCE_BY_KEY = {item.key: item for item in DAEJEON_SEOGU_SOURCES}
DAEJEON_SEOGU_LIBRARY_ROOTS = tuple(
    item.root_url for item in DAEJEON_SEOGU_LIBRARY_SOURCES
)
DAEJEON_SEOGU_INDEX_URLS = (
    DAEJEON_SEOGU_CANONICAL_URL,
    *DAEJEON_SEOGU_LIBRARY_ROOTS,
)

# Search candidates and YAML targets that are owned leaves/shells of the one
# canonical collector.  The Dunsan search result is a detail URL carrying the
# neighbouring "this month's events" menu code; the course itself belongs to
# Dunsan's canonical MENU0400030 archive.
DAEJEON_SEOGU_ALIAS_PROVIDERS = frozenset(
    {
        "MUNI_WWW_SEOGU_GO_KR_A27782FE",
        "MUNI_WWW_SEOGU_GO_KR_BA28DE1F",
        "MUNI_WWW_SEOGU_GO_KR_FD57747F",
    }
)
DAEJEON_SEOGU_ALIAS_CANDIDATE_IDS = frozenset(
    {
        "MUNI_IR_222C74329C58",
        "MUNI_IR_72541B622CD4",
        "MUNI_IR_B6AC6F872237",
    }
)
DAEJEON_SEOGU_DETAIL_ALIAS_URL = (
    "https://www.seogu.go.kr/library/dunsanlib/contents/learning/lib/02/"
    "lib.02.001.motion?bmode=detail&lecId=LEC_000000004610&mnucd=MENU0400029"
)
DAEJEON_SEOGU_ALIAS_URLS = (
    DAEJEON_SEOGU_DETAIL_ALIAS_URL,
    "https://www.seogu.go.kr/library/wolpyeonglib/index.do",
    (
        "https://www.seogu.go.kr/learning/damoa/contents/learning/edu/01/"
        "edu.01.001.motion?searchLecDivArray=99&mnucd=MENU0100092"
    ),
    (
        "https://www.seogu.go.kr/library/dunsanlib/contents/learning/lib/02/"
        "lib.02.001.motion?mnucd=MENU0400030"
    ),
)

DAEJEON_SEOGU_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_IR_08857C35A2C1": {
        "decision": "canonical_owner_shell",
        "provider": DAEJEON_SEOGU_PROVIDER,
    },
    "MUNI_IR_B6AC6F872237": {
        "decision": "owned_lifelong_leaf_alias",
        "provider": "MUNI_WWW_SEOGU_GO_KR_BA28DE1F",
    },
    "MUNI_IR_72541B622CD4": {
        "decision": "owned_library_shell_alias",
        "provider": "MUNI_WWW_SEOGU_GO_KR_A27782FE",
    },
    "MUNI_IR_222C74329C58": {
        "decision": "owned_dunsan_detail_alias_wrong_menu_code",
        "provider": "MUNI_WWW_SEOGU_GO_KR_FD57747F",
    },
}

# Read-only exhaustive comparison on 2026-07-21.  OK reservation's Seo-gu
# selector is a separate metropolitan catalogue.  Its current/future rows do
# not duplicate a Seo-gu-owned row by normalized title plus education period.
DAEJEON_SEOGU_OK_OVERLAP_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "comparison_scope": "current_or_future_normalized_title_and_period",
    "independent_source_rows": 2903,
    "independent_current_rows": 213,
    "ok_seogu_category_totals": {"8101": 2150, "8102": 0},
    "ok_seogu_current_rows": 261,
    "normalized_title_period_overlap_count": 0,
    "conclusion": "independent_non_alias_catalogue",
}

# The sports site is intentionally not folded into date-bounded education:
# its active records are rolling 1/3/6-month memberships without concrete
# course start/end dates.
DAEJEON_SEOGU_SPORTS_EXCLUSION_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "url": "https://www.seogu.go.kr/gym/fmcs/92",
    "active_membership_products": 13,
    "centre_counts": {
        "도마실국민체육센터": 0,
        "서구국민체육센터": 8,
        "남선공원종합체육관": 5,
        "갈마체육관": 0,
        "관저다목적체육관": 0,
    },
    "reason": "rolling_membership_without_concrete_start_end_dates",
}


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE4_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_DATE2_RE = re.compile(
    r"(?<!\d)(\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_DIGITS_RE = re.compile(r"\d+")
_LIFE_ID_RE = re.compile(
    r"^\s*fn_egov_select1\(document\.getElementById\(['\"]listForm['\"]\),\s*"
    r"['\"](LEC_\d{12})['\"],\s*['\"](ORD_\d{12})['\"],\s*"
    r"['\"]36['\"],\s*['\"]9999999['\"]\);\s*return false;\s*$"
)
_LIBRARY_ID_RE = re.compile(
    r"^\s*fn_egov_select\(document\.getElementById\(['\"]listForm['\"]\),\s*"
    r"['\"](LEC_\d{12})['\"]\);\s*return false;\s*$"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LIFE_COUNT_RE = re.compile(
    r"^총\s*게시물\s*:\s*([\d,]+)\s*건\s*현재\s*(\d+)\s*/\s*"
    r"전체\s*(\d+)\s*페이지$"
)
_LIBRARY_COUNT_RE = re.compile(
    r"^총\s*([\d,]+)\s*건,\s*현재\s*(\d+)\s*/\s*전체\s*(\d+)\s*페이지$"
)

_LIFE_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "대기신청": "WAITLIST",
    "접수대기": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "결제마감": "CLOSED",
    "폐강": "CLOSED",
}
_LIBRARY_CATEGORIES = frozenset({"어린이", "청소년", "일반인"})
_LIBRARY_TABS = (
    ("전체", ""),
    ("행사", "5"),
    ("어린이강좌", "2"),
    ("청소년강좌", "3"),
    ("일반인강좌", "4"),
)
_LIBRARY_HEADERS = ("번호", "구분", "제목", "대상", "일시", "시간", "정원")
_EXPERIENCE_TERMS = ("체험", "견학", "탐방", "관람")
_PERFORMANCE_TERMS = ("매직쇼", "음악회", "인형극", "공연", "콘서트", "상영회")

_SAFE_RAW_FIELDS = frozenset(
    {
        "source_kind",
        "source_key",
        "identity",
        "menu_code",
        "source_filter_code",
        "list_page",
        "source_category",
        "source_status",
        "source_period",
        "source_application_period",
        "source_schedule",
        "source_target",
        "source_capacity",
        "source_capacity_current",
        "source_capacity_total",
        "source_wait_current",
        "source_wait_total",
        "source_fee",
        "source_application_count",
        "service_family",
        "service_family_evidence",
        "application_control_present",
        "application_control_contract",
        "detail_verified",
        "education_institution",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "contact",
        "contact_name",
        "phone",
        "email",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)


@dataclass
class _ListPage:
    rows: list[dict[str, Any]]
    total: int
    last: int
    errors: list[str]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _clean(value).casefold(), flags=re.UNICODE)


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise ValueError("today must be an ISO date") from exc


def _canonical_compare_url(value: Any) -> str:
    raw = _clean(value)
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https" or parsed.hostname != DAEJEON_SEOGU_HOST:
        return ""
    if parsed.fragment or parsed.username or parsed.password or parsed.port:
        return ""
    pairs = sorted(parse_qsl(parsed.query, keep_blank_values=True))
    return f"https://{DAEJEON_SEOGU_HOST}{parsed.path}" + (
        f"?{urlencode(pairs)}" if pairs else ""
    )


def is_daejeon_seogu_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == DAEJEON_SEOGU_PROVIDER
        and _canonical_compare_url(_target_value(target, "url"))
        == DAEJEON_SEOGU_CANONICAL_URL
    )


def is_daejeon_seogu_owned_alias_target(target: Any) -> bool:
    provider = _clean(_target_value(target, "provider"))
    candidate_id = _clean(_target_value(target, "candidate_id"))
    compared = _canonical_compare_url(_target_value(target, "url"))
    aliases = {_canonical_compare_url(item) for item in DAEJEON_SEOGU_ALIAS_URLS}
    return bool(
        provider in DAEJEON_SEOGU_ALIAS_PROVIDERS
        or candidate_id in DAEJEON_SEOGU_ALIAS_CANDIDATE_IDS
        or compared in aliases
    )


def daejeon_seogu_list_url(source_key: Any, page: Any = 1) -> str:
    source = DAEJEON_SEOGU_SOURCE_BY_KEY.get(_clean(source_key))
    if source is None or isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return ""
    pairs: list[tuple[str, str]] = []
    if source.kind == "lifelong":
        pairs.append(("searchLecDivArray", source.filter_code))
    pairs.append(("mnucd", source.menu_code))
    if page != 1:
        pairs.append(("pageIndex", str(page)))
    return f"https://{DAEJEON_SEOGU_HOST}{source.path}?{urlencode(pairs)}"


def daejeon_seogu_detail_url(
    source_key: Any,
    identity: Any,
    order_code: Any = "",
) -> str:
    source = DAEJEON_SEOGU_SOURCE_BY_KEY.get(_clean(source_key))
    identity_value = _clean(identity)
    if source is None or not re.fullmatch(r"LEC_\d{12}", identity_value):
        return ""
    if source.kind == "lifelong":
        order_value = _clean(order_code)
        if not re.fullmatch(r"ORD_\d{12}", order_value):
            return ""
        pairs = [
            ("searchLecDivArray", source.filter_code),
            ("mnucd", source.menu_code),
            ("bmode", "detail1"),
            ("lecId", identity_value),
            ("ordCd", order_value),
            ("ordSidoCd", "36"),
            ("ordLocalCd", "9999999"),
        ]
    else:
        pairs = [
            ("mnucd", source.menu_code),
            ("bmode", "detail"),
            ("lecId", identity_value),
        ]
    return f"https://{DAEJEON_SEOGU_HOST}{source.path}?{urlencode(pairs)}"


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://www.seogu.go.kr/)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    final = urlparse(_clean(getattr(response, "url", url)))
    if final.scheme.lower() != "https" or final.hostname != DAEJEON_SEOGU_HOST:
        raise ValueError("response left the official HTTPS host")
    content_type = _clean(response.headers.get("Content-Type")).lower()
    if "html" not in content_type:
        raise ValueError("response is not HTML")
    content = response.content
    if len(content) > DAEJEON_SEOGU_MAX_HTML_BYTES:
        raise ValueError("HTML response exceeded the bounded size limit")
    return BeautifulSoup(content, "html.parser")


def _close_quietly(value: Any) -> None:
    try:
        close = getattr(value, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > DAEJEON_SEOGU_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > DAEJEON_SEOGU_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return _coerce_soup(bytes(content))
    raise TypeError("fetcher must return HTML, bytes, a response, or BeautifulSoup")


def _fetch_parse_many(
    items: Iterable[tuple[Any, str, Callable[[BeautifulSoup], Any]]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, Any], list[str]]:
    tasks = list(items)
    if not tasks:
        return {}, []

    def worker(
        key: Any, url: str, parser: Callable[[BeautifulSoup], Any]
    ) -> tuple[Any, Any]:
        last_error: Optional[Exception] = None
        for _attempt in range(DAEJEON_SEOGU_FETCH_ATTEMPTS):
            session = session_factory()
            try:
                soup = _coerce_soup(fetcher(session, url, timeout))
                return key, parser(soup)
            except Exception as exc:
                last_error = exc
            finally:
                _close_quietly(session)
        raise RuntimeError(_clean(last_error))

    results: dict[Any, Any] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        futures = {
            executor.submit(worker, key, url, parser): key
            for key, url, parser in tasks
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                result_key, value = future.result()
                results[result_key] = value
            except Exception as exc:
                errors.append(f"{key}: {_clean(exc)}")
    return results, errors


def _date_pair(value: Any, field: str, *, short_year: bool = False) -> tuple[date, date]:
    matches = (_DATE2_RE if short_year else _DATE4_RE).findall(_clean(value))
    if len(matches) != 2:
        raise ValueError(f"{field}: expected exactly two dates")
    parsed: list[date] = []
    for year, month, day_value in matches:
        full_year = 2000 + int(year) if short_year else int(year)
        try:
            parsed.append(date(full_year, int(month), int(day_value)))
        except ValueError as exc:
            raise ValueError(f"{field}: invalid calendar date") from exc
    return parsed[0], parsed[1]


def _integer(value: Any, field: str) -> int:
    match = re.search(r"[\d,]+", _clean(value))
    if match is None:
        raise ValueError(f"{field}: integer missing")
    return int(match.group().replace(",", ""))


def _fee(value: Any) -> tuple[str, int]:
    raw = _clean(value)
    if not raw:
        return "", 0
    if "무료" in raw:
        return "무료", 0
    match = re.search(r"([\d,]+)\s*원", raw)
    if match is None:
        raise ValueError("fee format changed")
    return raw, int(match.group(1).replace(",", ""))


def _service_family(title: Any) -> tuple[str, str]:
    value = _clean(title)
    if any(term in value for term in _EXPERIENCE_TERMS):
        return "experience", "explicit_experience_title_term"
    if "우리가족 굿즈 만들기" in value:
        return "experience", "official_cross_branch_experience_family"
    if any(term in value for term in _PERFORMANCE_TERMS):
        return "performance", "explicit_performance_title_term"
    return "education", "official_course_category_without_experience_signal"


def _base_row(
    source: DaejeonSeoguSource,
    identity: str,
    title: str,
    start: date,
    end: date,
    page: int,
) -> dict[str, Any]:
    family, evidence = _service_family(title)
    is_experience = family == "experience"
    return {
        "provider": DAEJEON_SEOGU_PROVIDER,
        "provider_course_id": f"{DAEJEON_SEOGU_PROVIDER}:{source.key}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": source.branch,
        "branch_code": source.key,
        "preserve_branch": True,
        "provider_organizer": source.branch,
        "category": source.label,
        "program_type": "체험" if is_experience else "교육",
        "raw_url": "",
        "application_url": "",
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "status": "CLOSED",
        "fee": "",
        "fee_amount": 0,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": "",
        "apply_start": "",
        "apply_end": "",
        "schedule_raw": "",
        "capacity": "",
        "capacity_current": 0,
        "capacity_total": 0,
        "target": "",
        "venue": "",
        "collection_category": "공공예약",
        "domain_category": "체험·견학" if is_experience else "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험" if is_experience else "공공강좌",
        "service_group_policy": "locked",
        "collection_type": DAEJEON_SEOGU_PARSER,
        "municipality_code": DAEJEON_SEOGU_MUNICIPALITY_CODE,
        "municipality_full_name": DAEJEON_SEOGU_MUNICIPALITY_NAME,
        "raw_fields": {
            "source_kind": source.kind,
            "source_key": source.key,
            "identity": identity,
            "menu_code": source.menu_code,
            "source_filter_code": source.filter_code,
            "list_page": page,
            "source_category": source.label,
            "service_family": family,
            "service_family_evidence": evidence,
            "application_control_present": False,
            "application_control_contract": "",
            "detail_verified": False,
        },
    }


def _hidden(form: Any, name: str) -> tuple[int, str]:
    nodes = form.select(f'input[name="{name}"]') if form is not None else []
    return len(nodes), _clean(nodes[0].get("value")) if len(nodes) == 1 else ""


def _parse_lifelong_list(
    soup: BeautifulSoup,
    source: DaejeonSeoguSource,
    page: int,
    cutoff: date,
) -> _ListPage:
    label = f"{source.key} page {page}"
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "온라인 수강신청 : 목록 화면 - 대전광역시 서구 평생학습관":
        errors.append(f"{label}: official page title changed")
    forms = soup.select("form#listForm")
    if len(forms) != 1:
        return _ListPage([], 0, 1, [*errors, f"{label}: list form missing or duplicated"])
    form = forms[0]
    if _clean(form.get("method")).lower() != "post" or _clean(form.get("action")) != source.path:
        errors.append(f"{label}: list form method/action changed")
    expected_hidden = {
        "mnucd": source.menu_code,
        "searchLecDivArray": source.filter_code,
        "bmode": "",
        "pageIndex": str(page),
        "lecId": "",
        "ordCd": "",
        "ordSidoCd": "",
        "ordLocalCd": "",
    }
    for name, expected in expected_hidden.items():
        count, value = _hidden(form, name)
        if count != 1 or value != expected:
            errors.append(f"{label}: hidden field {name} changed")
    options = tuple(
        (_clean(node.get("value")), _clean(node.get_text(" ", strip=True)))
        for node in form.select('select[name="searchCondition"] > option')
    )
    if options != (("1", "강좌명"), ("2", "강좌장소"), ("6", "접수중")):
        errors.append(f"{label}: search selector changed")
    counters = soup.select(".sub_program .board_search .count")
    counter_text = _clean(counters[0].get_text(" ", strip=True)) if len(counters) == 1 else ""
    match = _LIFE_COUNT_RE.fullmatch(counter_text)
    if match is None:
        errors.append(f"{label}: advertised total/page counter changed")
        total, response_page, last = 0, 0, 1
    else:
        total = int(match.group(1).replace(",", ""))
        response_page = int(match.group(2))
        last = int(match.group(3))
        expected_last = max(1, math.ceil(total / source.page_size))
        if response_page != page or last != expected_last:
            errors.append(f"{label}: response page/last declaration changed")

    rows: list[dict[str, Any]] = []
    boxes = soup.select(".sub_program_list > .box")
    for box in boxes:
        anchors = box.select(":scope > a[onclick]")
        parts = box.select(":scope > a > p.part")
        headings = box.select(":scope > a > h4")
        statuses = box.select(":scope > a > .progress")
        confirmations = box.select(":scope > a > .confirm00 strong")
        if not all(len(items) == 1 for items in (anchors, parts, headings, statuses, confirmations)):
            errors.append(f"{label}: course card shape changed")
            continue
        identity_match = _LIFE_ID_RE.fullmatch(_clean(anchors[0].get("onclick")))
        if identity_match is None:
            errors.append(f"{label}: course identity control changed")
            continue
        identity, order_code = identity_match.groups()
        title_value = _clean(headings[0].get_text(" ", strip=True))
        source_category = _clean(parts[0].get_text(" ", strip=True))
        source_status = _clean(statuses[0].get_text(" ", strip=True))
        if not title_value or source_category != source.label:
            errors.append(f"{label}/{identity}: title/category changed")
            continue
        if source_status not in _LIFE_STATUS_MAP:
            errors.append(f"{label}/{identity}: unknown source status {source_status}")
            continue
        values: dict[str, str] = {}
        for node in box.select("ul.list_01 > li"):
            raw = _clean(node.get_text(" ", strip=True))
            if ":" not in raw:
                errors.append(f"{label}/{identity}: card field delimiter changed")
                continue
            name, value = raw.split(":", 1)
            values[_clean(name)] = _clean(value)
        if set(values) != {"교육", "시간", "인원", "대상", "수강료"}:
            errors.append(f"{label}/{identity}: card fields changed")
            continue
        try:
            raw_start, raw_end = _date_pair(values["교육"], f"{source.key}/{identity}.period")
            current_or_future = max(raw_start, raw_end) >= cutoff
            if current_or_future and raw_end < raw_start:
                raise ValueError(f"{source.key}/{identity}.period: current range reversed")
            start, end = sorted((raw_start, raw_end))
            capacity_total = _integer(values["인원"], f"{source.key}/{identity}.capacity")
            application_count = _integer(
                confirmations[0].get_text(" ", strip=True),
                f"{source.key}/{identity}.application count",
            )
            fee, fee_amount = _fee(values["수강료"])
        except ValueError as exc:
            errors.append(_clean(exc))
            continue
        row = _base_row(source, identity, title_value, start, end, page)
        row.update(
            {
                "raw_url": daejeon_seogu_detail_url(source.key, identity, order_code),
                "status": _LIFE_STATUS_MAP[source_status],
                "fee": fee,
                "fee_amount": fee_amount,
                "schedule_raw": values["시간"],
                "capacity": str(capacity_total),
                "capacity_current": application_count,
                "capacity_total": capacity_total,
                "target": values["대상"],
            }
        )
        row["raw_fields"].update(
            {
                "source_status": source_status,
                "source_period": values["교육"],
                "source_schedule": values["시간"],
                "source_target": values["대상"],
                "source_capacity": values["인원"],
                "source_capacity_current": application_count,
                "source_capacity_total": capacity_total,
                "source_fee": values["수강료"],
                "source_application_count": application_count,
                "order_code": order_code,
            }
        )
        # order_code is required while constructing details but is removed
        # before privacy validation because it is an implementation identity,
        # not persisted raw metadata.
        rows.append(row)
    return _ListPage(rows, total, last, errors)


def _parse_library_list(
    soup: BeautifulSoup,
    source: DaejeonSeoguSource,
    page: int,
    cutoff: date,
) -> _ListPage:
    label = f"{source.key} page {page}"
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "행사 및 강좌 신청 - 대전광역시 서구 평생학습관":
        errors.append(f"{label}: official page title changed")
    forms = soup.select("form#listForm")
    if len(forms) != 1:
        return _ListPage([], 0, 1, [*errors, f"{label}: list form missing or duplicated"])
    form = forms[0]
    if _clean(form.get("method")).lower() != "post" or _clean(form.get("action")) != source.path:
        errors.append(f"{label}: list form method/action changed")
    expected_hidden = {
        "mnucd": source.menu_code,
        "bmode": "",
        "pageIndex": str(page),
        "searchLecGubun": "",
        "lecId": "",
    }
    for name, expected in expected_hidden.items():
        count, value = _hidden(form, name)
        if count != 1 or value != expected:
            errors.append(f"{label}: hidden field {name} changed")
    tabs: list[tuple[str, str]] = []
    tab_re = re.compile(
        r"^fn_egov_selectTabList\(document\.getElementById\(['\"]listForm['\"]\),"
        r"\s*['\"]([^'\"]*)['\"]\);\s*return false;$"
    )
    for node in soup.select("a[onclick*='fn_egov_selectTabList']"):
        match = tab_re.fullmatch(_clean(node.get("onclick")))
        if match:
            tabs.append((_clean(node.get_text(" ", strip=True)), match.group(1)))
    if tuple(tabs) != _LIBRARY_TABS:
        errors.append(f"{label}: official event/course tabs changed")
    counters = soup.select("p.total")
    counter_text = _clean(counters[0].get_text(" ", strip=True)) if len(counters) == 1 else ""
    match = _LIBRARY_COUNT_RE.fullmatch(counter_text)
    if match is None:
        errors.append(f"{label}: advertised total/page counter changed")
        total, response_page, last = 0, 0, 1
    else:
        total = int(match.group(1).replace(",", ""))
        response_page = int(match.group(2))
        last = int(match.group(3))
        expected_last = max(1, math.ceil(total / source.page_size))
        if response_page != page or last != expected_last:
            errors.append(f"{label}: response page/last declaration changed")
    tables = soup.select("table.tbl_basic_list, table.tbl_basic")
    tables = [table for table in tables if table.select("thead th")]
    if len(tables) != 1:
        return _ListPage([], total, last, [*errors, f"{label}: course table missing or duplicated"])
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in tables[0].select("thead th"))
    if headers != _LIBRARY_HEADERS:
        errors.append(f"{label}: course table headers changed")
    rows: list[dict[str, Any]] = []
    empty_markers = 0
    for tr in tables[0].select("tbody > tr"):
        cells = tr.select(":scope > td")
        anchors = tr.select("a[onclick*='fn_egov_select']")
        text = _clean(tr.get_text(" ", strip=True))
        if not anchors:
            if len(cells) == 1 and any(term in text for term in ("없습니다", "조회된")):
                empty_markers += 1
                continue
            errors.append(f"{label}: unexpected non-course row")
            continue
        if len(cells) != 7 or len(anchors) != 1:
            errors.append(f"{label}: course row shape changed")
            continue
        identity_match = _LIBRARY_ID_RE.fullmatch(_clean(anchors[0].get("onclick")))
        if identity_match is None:
            errors.append(f"{label}: course identity control changed")
            continue
        identity = identity_match.group(1)
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        title_value = _clean(anchors[0].get_text(" ", strip=True))
        if not title_value or _normalized(values[2]) != _normalized(title_value):
            errors.append(f"{label}/{identity}: title/link mismatch")
            continue
        source_category = values[1]
        if source_category not in _LIBRARY_CATEGORIES:
            errors.append(f"{label}/{identity}: non-course source category changed")
            continue
        try:
            raw_start, raw_end = _date_pair(values[4], f"{source.key}/{identity}.period")
            current_or_future = max(raw_start, raw_end) >= cutoff
            if current_or_future and raw_end < raw_start:
                raise ValueError(f"{source.key}/{identity}.period: current range reversed")
            start, end = sorted((raw_start, raw_end))
            capacity_match = re.fullmatch(r"([\d,]+)\s*/\s*([\d,]+)", values[6])
            if capacity_match is None:
                raise ValueError(f"{source.key}/{identity}.capacity: invalid fraction")
            capacity_current = int(capacity_match.group(1).replace(",", ""))
            capacity_total = int(capacity_match.group(2).replace(",", ""))
        except ValueError as exc:
            errors.append(_clean(exc))
            continue
        row = _base_row(source, identity, title_value, start, end, page)
        row.update(
            {
                "category": f"{source_category}강좌",
                "raw_url": daejeon_seogu_detail_url(source.key, identity),
                "schedule_raw": values[5],
                "capacity": f"{capacity_current}/{capacity_total}",
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "target": values[3],
                "venue": source.branch,
            }
        )
        row["raw_fields"].update(
            {
                "source_category": source_category,
                "source_period": values[4],
                "source_schedule": values[5],
                "source_target": values[3],
                "source_capacity": values[6],
                "source_capacity_current": capacity_current,
                "source_capacity_total": capacity_total,
            }
        )
        rows.append(row)
    if rows and empty_markers:
        errors.append(f"{label}: course rows and empty marker coexist")
    if not rows and total == 0 and empty_markers != 1:
        errors.append(f"{label}: empty catalogue lacks one official marker")
    return _ListPage(rows, total, last, errors)


def _parse_list(
    soup: BeautifulSoup,
    source: DaejeonSeoguSource,
    page: int,
    cutoff: date,
) -> _ListPage:
    if source.kind == "lifelong":
        return _parse_lifelong_list(soup, source, page, cutoff)
    return _parse_library_list(soup, source, page, cutoff)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("title")),
            _clean(row.get("end_date")),
        )
        for row in rows
    )


def _absolute_set(soup: BeautifulSoup, base: str, path_pattern: re.Pattern[str]) -> set[str]:
    result: set[str] = set()
    for node in soup.select("a[href]"):
        absolute = urljoin(base, _clean(node.get("href")))
        parsed = urlparse(absolute)
        if parsed.scheme.lower() == "https" and parsed.hostname == DAEJEON_SEOGU_HOST and path_pattern.fullmatch(parsed.path):
            compared = _canonical_compare_url(absolute)
            if compared:
                result.add(compared)
    return result


def _validate_lifelong_index(soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "대전광역시 서구 평생학습관":
        errors.append("lifelong index title changed")
    actual = _absolute_set(
        soup,
        DAEJEON_SEOGU_CANONICAL_URL,
        re.compile(re.escape(DAEJEON_SEOGU_LIFELONG_PATH)),
    )
    expected = {_canonical_compare_url(item.list_url) for item in DAEJEON_SEOGU_LIFELONG_SOURCES}
    if actual != expected:
        errors.append("lifelong official catalogue fanout changed")
    return errors


def _validate_library_index(
    soup: BeautifulSoup, source: DaejeonSeoguSource
) -> list[str]:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if source.label not in title or "대전광역시 서구" not in title:
        errors.append(f"{source.key}: library index title changed")
    roots = _absolute_set(
        soup,
        source.root_url,
        re.compile(r"/library/(?:galmalib|gasuwonlib|dunsanlib|wolpyeonglib|childlib)/index\.do"),
    )
    if roots != {_canonical_compare_url(item) for item in DAEJEON_SEOGU_LIBRARY_ROOTS}:
        errors.append(f"{source.key}: five-library fanout changed")
    own_lists = _absolute_set(soup, source.root_url, re.compile(re.escape(source.path)))
    if _canonical_compare_url(source.list_url) not in own_lists:
        errors.append(f"{source.key}: course-list route missing from official index")
    return errors


def _detail_fields_from_lis(soup: BeautifulSoup) -> tuple[dict[str, str], list[str]]:
    result: dict[str, str] = {}
    errors: list[str] = []
    for node in soup.select("ul.detail > li"):
        names = node.select(":scope > .titles strong")
        values = node.select(":scope > .txts")
        if len(names) != 1 or len(values) != 1:
            continue
        name = _clean(names[0].get_text(" ", strip=True))
        if name in result:
            errors.append(f"duplicate detail field {name}")
        result[name] = _clean(values[0].get_text(" ", strip=True))
    return result, errors


def _validate_lifelong_detail(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    source: DaejeonSeoguSource,
) -> tuple[dict[str, Any], list[str]]:
    row = dict(listed)
    row["raw_fields"] = dict(listed["raw_fields"])
    identity = _clean(row["raw_fields"].get("identity"))
    order_code = _clean(row["raw_fields"].get("order_code"))
    label = f"{source.key}/{identity}"
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "온라인 수강신청 : 상세 화면 - 대전광역시 서구 평생학습관":
        errors.append(f"{label}: detail title changed")
    forms = soup.select("form#detailForm")
    if len(forms) != 1:
        return row, [*errors, f"{label}: detail form missing or duplicated"]
    form = forms[0]
    if _clean(form.get("method")).lower() != "post" or _clean(form.get("action")) != source.path:
        errors.append(f"{label}: detail form method/action changed")
    expected = {
        "mnucd": source.menu_code,
        "bmode": "detail1",
        "lecId": identity,
        "ordCd": order_code,
        "ordSidoCd": "36",
        "ordLocalCd": "9999999",
        "searchLecDivArray": source.filter_code,
    }
    for name, value in expected.items():
        count, actual = _hidden(form, name)
        if count != 1 or actual != value:
            errors.append(f"{label}: detail identity field {name} changed")
    fields, field_errors = _detail_fields_from_lis(soup)
    errors.extend(f"{label}: {item}" for item in field_errors)
    required = {
        "과목명",
        "교육일정",
        "교육대상",
        "모집인원",
        "수강료",
        "교육장소",
        "교육기관",
        "교육기간",
        "수강신청기간",
        "모집방법",
    }
    if not required <= set(fields):
        errors.append(f"{label}: required detail fields changed")
        return row, errors
    try:
        start, end = _date_pair(fields["교육기간"], f"{label}.detail period", short_year=True)
        apply_start, apply_end = _date_pair(
            fields["수강신청기간"], f"{label}.detail application period", short_year=True
        )
        detail_capacity = _integer(fields["모집인원"], f"{label}.detail capacity")
        fee, fee_amount = _fee(fields["수강료"])
    except ValueError as exc:
        return row, [*errors, f"{label}: {_clean(exc)}"]
    if (start.isoformat(), end.isoformat()) != (row["start_date"], row["end_date"]):
        errors.append(f"{label}: detail/list education period mismatch")
    if _normalized(fields["과목명"]) != _normalized(row["title"]):
        errors.append(f"{label}: detail/list title mismatch")
    if detail_capacity != int(row["capacity_total"]):
        errors.append(f"{label}: detail/list capacity mismatch")
    if fee_amount != int(row["fee_amount"]):
        errors.append(f"{label}: detail/list fee mismatch")
    controls = [
        node
        for node in soup.select("a[onclick]")
        if _clean(node.get_text(" ", strip=True)) == "프로그램신청"
    ]
    scripts = "\n".join(node.get_text() for node in soup.select("script"))
    member_check = f"/learning/damoa/Classesinfo/MberCheck.do?lecId={identity}"
    pay_fragment = f"'{identity}','{order_code}','36','9999999'"
    login_fragment = (
        "/learning/damoa/contents/learning/member/01/member.01.001.motion?"
        "mnucd=MENU1000052"
    )
    if (
        len(controls) != 1
        or _clean(controls[0].get("onclick")) != "fn_NonCheck(); return false;"
        or member_check not in scripts
        or pay_fragment not in scripts
        or login_fragment not in scripts
    ):
        errors.append(f"{label}: course-bound application/login control changed")
    else:
        row["raw_fields"]["application_control_present"] = True
        row["raw_fields"]["application_control_contract"] = (
            "official_login_gate_and_course_bound_member_check"
        )
        if row["status"] in {"OPEN", "WAITLIST"}:
            row["application_url"] = row["raw_url"]
            row["application_type"] = "ONLINE_RESERVATION_LOGIN_REQUIRED"
            row["reservation_available"] = True
    row.update(
        {
            "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
            "apply_start": apply_start.isoformat(),
            "apply_end": apply_end.isoformat(),
            "schedule_raw": fields["교육일정"],
            "target": fields["교육대상"],
            "venue": fields["교육장소"],
            "provider_organizer": fields["교육기관"],
            "fee": fee,
            "fee_amount": fee_amount,
        }
    )
    row["raw_fields"].update(
        {
            "source_application_period": fields["수강신청기간"],
            "education_institution": fields["교육기관"],
        }
    )
    row["raw_fields"].pop("order_code", None)
    if not errors:
        row["raw_fields"]["detail_verified"] = True
    return row, errors


def _table_fields(soup: BeautifulSoup) -> tuple[dict[str, str], list[str]]:
    tables = soup.select("table.tbl_basic_view")
    if len(tables) != 1:
        return {}, ["detail table missing or duplicated"]
    fields: dict[str, str] = {}
    errors: list[str] = []
    pending_name = ""
    for tr in tables[0].select("tr"):
        names = tr.select(":scope > th")
        values = tr.select(":scope > td")
        # The production template intentionally renders ``강의내용`` as a
        # heading-only row followed by a value-only row.  Preserve that exact
        # two-row contract while rejecting every other unpaired shape.
        if len(names) == 1 and not values and not pending_name:
            pending_name = _clean(names[0].get_text(" ", strip=True))
            continue
        if not names and len(values) == 1 and pending_name:
            if pending_name in fields:
                errors.append(f"duplicate detail field {pending_name}")
            fields[pending_name] = _clean(values[0].get_text(" ", strip=True))
            pending_name = ""
            continue
        if len(names) != len(values):
            errors.append("detail table label/value shape changed")
            continue
        for name_node, value_node in zip(names, values):
            name = _clean(name_node.get_text(" ", strip=True))
            if name in fields:
                errors.append(f"duplicate detail field {name}")
            fields[name] = _clean(value_node.get_text(" ", strip=True))
    if pending_name:
        errors.append("detail table ended with an unpaired label")
    return fields, errors


def _validate_library_detail(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    source: DaejeonSeoguSource,
    cutoff: date,
) -> tuple[dict[str, Any], list[str]]:
    row = dict(listed)
    row["raw_fields"] = dict(listed["raw_fields"])
    identity = _clean(row["raw_fields"].get("identity"))
    label = f"{source.key}/{identity}"
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "행사 및 강좌 신청 : 상세 화면 - 대전광역시 서구 평생학습관":
        errors.append(f"{label}: detail title changed")
    forms = soup.select("form#detailForm")
    if len(forms) != 1:
        return row, [*errors, f"{label}: detail form missing or duplicated"]
    form = forms[0]
    if _clean(form.get("method")).lower() != "post" or _clean(form.get("action")) != source.path:
        errors.append(f"{label}: detail form method/action changed")
    expected = {"mnucd": source.menu_code, "bmode": "detail", "lecId": identity}
    for name, value in expected.items():
        count, actual = _hidden(form, name)
        if count != 1 or actual != value:
            errors.append(f"{label}: detail identity field {name} changed")
    fields, field_errors = _table_fields(soup)
    errors.extend(f"{label}: {item}" for item in field_errors)
    required = {
        "제목",
        "일시",
        "시간",
        "신청기간",
        "강사",
        "대상",
        "모집인원",
        "예비인원",
        "파일첨부",
        "강의내용",
    }
    if not required <= set(fields):
        errors.append(f"{label}: required detail fields changed")
        return row, errors
    source_category = _clean(row["raw_fields"].get("source_category"))
    if (
        _normalized(row["title"]) not in _normalized(fields["제목"])
        or _normalized(f"{source_category}강좌") not in _normalized(fields["제목"])
    ):
        errors.append(f"{label}: detail/list title or category mismatch")
    try:
        start, end = _date_pair(fields["일시"], f"{label}.detail period")
        apply_start, apply_end = _date_pair(fields["신청기간"], f"{label}.application period")
        capacity_match = re.fullmatch(r"([\d,]+)\s*/\s*([\d,]+)", fields["모집인원"])
        wait_match = re.fullmatch(r"([\d,]+)\s*/\s*([\d,]+)", fields["예비인원"])
        if capacity_match is None or wait_match is None:
            raise ValueError("detail capacity/wait format changed")
        capacity_current = int(capacity_match.group(1).replace(",", ""))
        capacity_total = int(capacity_match.group(2).replace(",", ""))
        wait_current = int(wait_match.group(1).replace(",", ""))
        wait_total = int(wait_match.group(2).replace(",", ""))
    except ValueError as exc:
        return row, [*errors, f"{label}: {_clean(exc)}"]
    if (start.isoformat(), end.isoformat()) != (row["start_date"], row["end_date"]):
        errors.append(f"{label}: detail/list education period mismatch")
    if (capacity_current, capacity_total) != (
        int(row["capacity_current"]),
        int(row["capacity_total"]),
    ):
        errors.append(f"{label}: detail/list capacity mismatch")
    apply_controls = [
        node for node in soup.select(".btn_area a[onclick]")
        if _clean(node.get_text(" ", strip=True)) == "수강신청"
    ]
    confirmation = [
        node for node in soup.select(".btn_area a[onclick]")
        if _clean(node.get_text(" ", strip=True)) == "수강신청확인"
    ]
    back = [
        node for node in soup.select(".btn_area a[onclick]")
        if _clean(node.get_text(" ", strip=True)) == "목록"
    ]
    expected_confirm = re.compile(
        r"^fn_egov_selectUserList\(document\.getElementById\(['\"]detailForm['\"]\),"
        rf"\s*['\"]{re.escape(identity)}['\"]\);\s*return false;$"
    )
    if (
        len(apply_controls) != 1
        or len(confirmation) != 1
        or len(back) != 1
        or expected_confirm.fullmatch(_clean(confirmation[0].get("onclick"))) is None
        or "fn_egov_selectList" not in _clean(back[0].get("onclick"))
    ):
        errors.append(f"{label}: official application controls changed")
    else:
        onclick = _clean(apply_controls[0].get("onclick"))
        add_pattern = re.compile(
            r"^fn_egov_addView\(document\.getElementById\(['\"]detailForm['\"]\),"
            rf"\s*['\"]{re.escape(identity)}['\"]\);\s*return false;$"
        )
        alert_pattern = re.compile(r"^alert\(['\"].+['\"]\);\s*return false;$")
        if add_pattern.fullmatch(onclick):
            row["status"] = "OPEN"
            row["application_url"] = row["raw_url"]
            row["application_type"] = "ONLINE_RESERVATION"
            row["reservation_available"] = True
            contract = "official_course_bound_write_control"
        elif alert_pattern.fullmatch(onclick):
            row["status"] = "SCHEDULED" if apply_start >= cutoff and "마감" not in onclick else "CLOSED"
            contract = "official_non_writable_alert_control"
        else:
            errors.append(f"{label}: application write/closed control changed")
            contract = ""
        if contract:
            row["raw_fields"]["application_control_present"] = True
            row["raw_fields"]["application_control_contract"] = contract
    row.update(
        {
            "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
            "apply_start": apply_start.isoformat(),
            "apply_end": apply_end.isoformat(),
            "schedule_raw": " ".join(
                item for item in (fields.get("요일", ""), fields["시간"]) if item
            ),
            "target": fields["대상"],
            "capacity": f"{capacity_current}/{capacity_total}",
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
        }
    )
    row["raw_fields"].update(
        {
            "source_application_period": fields["신청기간"],
            "source_wait_current": wait_current,
            "source_wait_total": wait_total,
        }
    )
    if not errors:
        row["raw_fields"]["detail_verified"] = True
    return row, errors


def _validate_detail(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    source: DaejeonSeoguSource,
    cutoff: date,
) -> tuple[dict[str, Any], list[str]]:
    if source.kind == "lifelong":
        return _validate_lifelong_detail(listed, soup, source)
    return _validate_library_detail(listed, soup, source, cutoff)


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr(
        {key: value for key, value in row.items() if key not in {"raw_url", "application_url"}}
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("arbitrary detail description persisted")
    if _clean(row.get("raw_fields", {}).get("service_family")) not in {
        "education",
        "experience",
    }:
        errors.append("unsupported service family reached program persistence")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "index_requests": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "required_source_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "configured_collection_error": error,
    }


def collect_daejeon_seogu_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 600,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = DAEJEON_SEOGU_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Seo-gu program snapshot."""

    meta = _base_meta()
    if not is_daejeon_seogu_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Seo-gu owner"
        return [], DAEJEON_SEOGU_PARSER, meta
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
                "configured_collection_error": "invalid timeout/max_pages/detail_limit/max_workers cap",
            }
        )
        return [], DAEJEON_SEOGU_PARSER, meta
    try:
        cutoff = _today(today)
    except ValueError as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], DAEJEON_SEOGU_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    errors: list[str] = []

    bootstrap: list[tuple[Any, str, Callable[[BeautifulSoup], Any]]] = [
        (
            ("index", "lifelong"),
            DAEJEON_SEOGU_CANONICAL_URL,
            _validate_lifelong_index,
        )
    ]
    for source in DAEJEON_SEOGU_LIBRARY_SOURCES:
        bootstrap.append(
            (
                ("index", source.key),
                source.root_url,
                lambda soup, current=source: _validate_library_index(soup, current),
            )
        )
    for source in DAEJEON_SEOGU_SOURCES:
        bootstrap.append(
            (
                ("list", source.key, 1, "data"),
                source.list_url,
                lambda soup, current=source: _parse_list(soup, current, 1, cutoff),
            )
        )
    initial, initial_fetch_errors = _fetch_parse_many(
        bootstrap,
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(initial_fetch_errors)
    meta["pages"] += len(initial)
    meta["index_requests"] = sum(key[0] == "index" for key in initial)
    meta["list_requests"] = sum(key[0] == "list" for key in initial)
    for key, value in initial.items():
        if key[0] == "index":
            errors.extend(value)

    first_pages: dict[str, _ListPage] = {}
    totals: dict[str, int] = {}
    lasts: dict[str, int] = {}
    for source in DAEJEON_SEOGU_SOURCES:
        result = initial.get(("list", source.key, 1, "data"))
        if not isinstance(result, _ListPage):
            errors.append(f"{source.key}: first page missing")
            continue
        first_pages[source.key] = result
        totals[source.key] = result.total
        lasts[source.key] = result.last
        errors.extend(result.errors)
    if len(totals) != len(DAEJEON_SEOGU_SOURCES):
        meta.update(
            {
                "source_totals": totals,
                "declared_pages": lasts,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return [], DAEJEON_SEOGU_PARSER, meta

    required_list_requests = sum(last + 2 for last in lasts.values())
    required_source_requests = len(DAEJEON_SEOGU_INDEX_URLS) + required_list_requests
    meta["required_list_requests"] = required_list_requests
    meta["required_source_requests"] = required_source_requests
    if required_source_requests > max_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {max_pages} of {required_source_requests} required source requests"
        )
    if errors:
        meta.update(
            {
                "source_totals": totals,
                "declared_pages": lasts,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return [], DAEJEON_SEOGU_PARSER, meta

    remaining_items: list[tuple[Any, str, Callable[[BeautifulSoup], Any]]] = []
    for source in DAEJEON_SEOGU_SOURCES:
        last = lasts[source.key]
        for page in range(2, last + 1):
            remaining_items.append(
                (
                    ("list", source.key, page, "data"),
                    daejeon_seogu_list_url(source.key, page),
                    lambda soup, current=source, current_page=page: _parse_list(
                        soup, current, current_page, cutoff
                    ),
                )
            )
        sentinel_page = last + 1
        remaining_items.extend(
            [
                (
                    ("list", source.key, sentinel_page, "sentinel"),
                    daejeon_seogu_list_url(source.key, sentinel_page),
                    lambda soup, current=source, current_page=sentinel_page: _parse_list(
                        soup, current, current_page, cutoff
                    ),
                ),
                (
                    ("list", source.key, 1, "recheck"),
                    source.list_url,
                    lambda soup, current=source: _parse_list(soup, current, 1, cutoff),
                ),
            ]
        )
    remaining, remaining_fetch_errors = _fetch_parse_many(
        remaining_items,
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(remaining_fetch_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)
    meta["sentinel_requests"] = sum(
        ("list", source.key, lasts[source.key] + 1, "sentinel") in remaining
        for source in DAEJEON_SEOGU_SOURCES
    )
    meta["stability_rechecks"] = sum(
        ("list", source.key, 1, "recheck") in remaining
        for source in DAEJEON_SEOGU_SOURCES
    )

    all_rows: list[dict[str, Any]] = []
    page_counts: dict[str, dict[int, int]] = {}
    for source in DAEJEON_SEOGU_SOURCES:
        source_rows: list[dict[str, Any]] = []
        page_counts[source.key] = {}
        signatures: dict[int, tuple[tuple[str, str, str], ...]] = {}
        total, last = totals[source.key], lasts[source.key]
        for page in range(1, last + 1):
            result = (
                first_pages[source.key]
                if page == 1
                else remaining.get(("list", source.key, page, "data"))
            )
            if not isinstance(result, _ListPage):
                errors.append(f"{source.key} page {page}: missing response")
                continue
            errors.extend(result.errors)
            if (result.total, result.last) != (total, last):
                errors.append(f"{source.key} page {page}: total/last changed")
            expected = (
                0
                if total == 0
                else source.page_size
                if page < last
                else total - source.page_size * (last - 1)
            )
            if len(result.rows) != expected:
                errors.append(
                    f"{source.key} page {page}: row count {len(result.rows)} != {expected}"
                )
            page_counts[source.key][page] = len(result.rows)
            signatures[page] = _page_signature(result.rows)
            source_rows.extend(result.rows)
        if len(source_rows) != total:
            errors.append(f"{source.key}: advertised total does not match parsed rows")
        nonempty_signatures = [item for item in signatures.values() if item]
        if len(nonempty_signatures) != len(set(nonempty_signatures)):
            errors.append(f"{source.key}: duplicate non-empty page signature")
        sentinel = remaining.get(("list", source.key, last + 1, "sentinel"))
        recheck = remaining.get(("list", source.key, 1, "recheck"))
        if not isinstance(sentinel, _ListPage) or not isinstance(recheck, _ListPage):
            errors.append(f"{source.key}: sentinel or page-one recheck missing")
        else:
            errors.extend(sentinel.errors)
            errors.extend(recheck.errors)
            if (sentinel.total, sentinel.last) != (total, last) or sentinel.rows:
                errors.append(f"{source.key}: immediate post-last page is not empty")
            if (
                (recheck.total, recheck.last) != (total, last)
                or _page_signature(recheck.rows) != signatures.get(1, ())
            ):
                errors.append(f"{source.key}: page-one recheck changed")
        all_rows.extend(source_rows)

    identities = [
        (
            _clean(row.get("raw_fields", {}).get("source_key")),
            _clean(row.get("raw_fields", {}).get("identity")),
        )
        for row in all_rows
    ]
    identity_duplicate_count = len(identities) - len(set(identities))
    if identity_duplicate_count:
        errors.append(f"{identity_duplicate_count} duplicate official identities")
    semantic_counter = Counter(
        (
            _normalized(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
        )
        for row in all_rows
    )
    semantic_duplicate_groups = sum(value > 1 for value in semantic_counter.values())
    semantic_duplicate_excess = sum(max(0, value - 1) for value in semantic_counter.values())
    current_source_rows = [
        row for row in all_rows if date.fromisoformat(_clean(row["end_date"])) >= cutoff
    ]
    current_education_rows = [
        row
        for row in current_source_rows
        if _clean(row.get("raw_fields", {}).get("service_family")) == "education"
    ]
    current_experience_rows = [
        row
        for row in current_source_rows
        if _clean(row.get("raw_fields", {}).get("service_family")) == "experience"
    ]
    current_program_rows = [*current_education_rows, *current_experience_rows]
    excluded_current_rows = [
        row for row in current_source_rows if row not in current_program_rows
    ]
    list_complete = bool(
        not errors
        and len(all_rows) == sum(totals.values())
        and meta["list_requests"] == required_list_requests
        and meta["index_requests"] == len(DAEJEON_SEOGU_INDEX_URLS)
    )
    if len(current_program_rows) > detail_limit:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {detail_limit} of {len(current_program_rows)} required details"
        )

    detailed_rows: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    if list_complete and not errors:
        detail_items: list[tuple[Any, str, Callable[[BeautifulSoup], Any]]] = []
        for listed in current_program_rows:
            source_key = _clean(listed["raw_fields"]["source_key"])
            identity = _clean(listed["raw_fields"]["identity"])
            source = DAEJEON_SEOGU_SOURCE_BY_KEY[source_key]
            detail_items.append(
                (
                    ("detail", source_key, identity),
                    _clean(listed["raw_url"]),
                    lambda soup, current=dict(listed), current_source=source: _validate_detail(
                        current, soup, current_source, cutoff
                    ),
                )
            )
        meta["detail_attempts"] = len(detail_items)
        details, detail_fetch_errors = _fetch_parse_many(
            detail_items,
            fetcher=current_fetcher,
            session_factory=factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        meta["pages"] += len(details)
        for listed in current_program_rows:
            key = (
                "detail",
                _clean(listed["raw_fields"]["source_key"]),
                _clean(listed["raw_fields"]["identity"]),
            )
            result = details.get(key)
            if not isinstance(result, tuple) or len(result) != 2:
                detail_errors.append(f"{key}: detail response missing")
                continue
            detailed, item_errors = result
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                detailed_rows.append(detailed)
                meta["detail_pages"] += 1
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        list_complete
        and meta["detail_attempts"] == len(current_program_rows)
        and meta["detail_pages"] == len(current_program_rows)
        and not detail_errors
    )

    result: list[dict[str, Any]] = []
    if details_complete and not errors:
        for row in detailed_rows:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(detailed_rows))
            except Exception as exc:
                errors.append(f"dedupe failed: {_clean(exc)}")
                result = []
            if len(result) != len(detailed_rows):
                errors.append(
                    f"dedupe changed official identity cardinality {len(detailed_rows)} to {len(result)}"
                )
                result = []
    snapshot_complete = bool(list_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    source_counts = Counter(_clean(row["raw_fields"]["source_key"]) for row in all_rows)
    current_counts = Counter(
        _clean(row["raw_fields"]["source_key"]) for row in current_source_rows
    )
    current_education_counts = Counter(
        _clean(row["raw_fields"]["source_key"]) for row in current_education_rows
    )
    current_experience_counts = Counter(
        _clean(row["raw_fields"]["source_key"]) for row in current_experience_rows
    )
    family_counts = Counter(_clean(row["raw_fields"]["service_family"]) for row in all_rows)
    current_family_counts = Counter(
        _clean(row["raw_fields"]["service_family"]) for row in current_source_rows
    )
    excluded_ids = [
        f"{row['raw_fields']['source_key']}:{row['raw_fields']['identity']}"
        for row in excluded_current_rows
    ]
    branch_counts = Counter(_clean(row["branch"]) for row in result)
    status_counts = Counter(_clean(row["status"]) for row in result)
    meta.update(
        {
            "ownership_scope": DAEJEON_SEOGU_OWNERSHIP_SCOPE,
            "ownership_fanout_urls": [item.list_url for item in DAEJEON_SEOGU_SOURCES],
            "official_index_urls": list(DAEJEON_SEOGU_INDEX_URLS),
            "source_totals": totals,
            "declared_pages": lasts,
            "page_counts": page_counts,
            "source_rows": len(all_rows),
            "source_counts": dict(source_counts),
            "current_source_count": len(current_source_rows),
            "current_counts": dict(current_counts),
            "current_education_count": len(current_education_rows),
            "current_education_counts": dict(current_education_counts),
            "current_experience_count": len(current_experience_rows),
            "current_experience_counts": dict(current_experience_counts),
            "current_program_count": len(current_program_rows),
            "expired_count": len(all_rows) - len(current_source_rows),
            "service_family_counts": dict(family_counts),
            "current_service_family_counts": dict(current_family_counts),
            "excluded_current_count": len(excluded_current_rows),
            "excluded_current_counts": dict(
                Counter(_clean(row["raw_fields"]["service_family"]) for row in excluded_current_rows)
            ),
            "excluded_current_ids": excluded_ids,
            "identity_duplicate_count": identity_duplicate_count,
            "semantic_duplicate_group_count": semantic_duplicate_groups,
            "semantic_duplicate_excess_rows": semantic_duplicate_excess,
            "semantic_duplicate_policy": "preserve_distinct_official_source_identities",
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "domain_category_counts": dict(
                Counter(_clean(row["domain_category"]) for row in result)
            ),
            "service_group_counts": dict(
                Counter(_clean(row["service_group"]) for row in result)
            ),
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not current_program_rows),
            "no_current_reason": (
                "all complete Seo-gu education and experience catalogues have ended"
                if snapshot_complete and not current_program_rows
                else ""
            ),
            "municipality_coverage": [DAEJEON_SEOGU_MUNICIPALITY_CODE],
            "candidate_audit": {key: dict(value) for key, value in DAEJEON_SEOGU_CANDIDATE_AUDIT.items()},
            "alias_providers": sorted(DAEJEON_SEOGU_ALIAS_PROVIDERS),
            "ok_overlap_audit": dict(DAEJEON_SEOGU_OK_OVERLAP_AUDIT),
            "ok_catalogue_is_alias": False,
            "sports_exclusion_audit": dict(DAEJEON_SEOGU_SPORTS_EXCLUSION_AUDIT),
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, DAEJEON_SEOGU_PARSER, meta


collect = collect_daejeon_seogu_education


__all__ = [
    "DAEJEON_SEOGU_ALIAS_CANDIDATE_IDS",
    "DAEJEON_SEOGU_ALIAS_PROVIDERS",
    "DAEJEON_SEOGU_ALIAS_URLS",
    "DAEJEON_SEOGU_CANONICAL_CANDIDATE_ID",
    "DAEJEON_SEOGU_CANONICAL_URL",
    "DAEJEON_SEOGU_CANDIDATE_AUDIT",
    "DAEJEON_SEOGU_INDEX_URLS",
    "DAEJEON_SEOGU_LIBRARY_SOURCES",
    "DAEJEON_SEOGU_LIFELONG_SOURCES",
    "DAEJEON_SEOGU_MUNICIPALITY_CODE",
    "DAEJEON_SEOGU_MUNICIPALITY_NAME",
    "DAEJEON_SEOGU_OK_OVERLAP_AUDIT",
    "DAEJEON_SEOGU_PARSER",
    "DAEJEON_SEOGU_PROVIDER",
    "DAEJEON_SEOGU_SOURCES",
    "DAEJEON_SEOGU_SPORTS_EXCLUSION_AUDIT",
    "collect",
    "collect_daejeon_seogu_education",
    "daejeon_seogu_detail_url",
    "daejeon_seogu_list_url",
    "is_daejeon_seogu_education_target",
    "is_daejeon_seogu_owned_alias_target",
]
