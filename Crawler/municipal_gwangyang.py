"""Fail-closed collector for Gwangyang City's official education catalogue.

The promotion candidate points at the city's lifelong-learning landing page,
while the structured records live in ten official ``/lecture.es`` catalogues.
This module owns that complete education aggregate: the women's culture
centre, citizen IT, digital learning and seven resident-centre branches.

Every advertised list page is read, followed by an immediate empty page and
stable first/last boundary rechecks for every source.  Only current/future
education rows need detail requests, but every one of those details must agree
with its list row and its public application controls.  Application forms are
never requested.  Instructor/contact fields, attachments and free-form course
copy are deliberately neither read nor persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GWANGYANG_PROVIDER = "MUNI_GWANGYANG_GO_KR_5517F0C0"
GWANGYANG_CANONICAL_CANDIDATE_ID = "MUNI_IR_AEE05F7A1573"
GWANGYANG_CANONICAL_URL = (
    "https://gwangyang.go.kr/edu/menu.es?mid=b10300000000"
)
GWANGYANG_HOST = "gwangyang.go.kr"
GWANGYANG_LANDING_PATH = "/edu/menu.es"
GWANGYANG_LANDING_MID = "b10300000000"
GWANGYANG_LIST_PATH = "/lecture.es"
GWANGYANG_APPLICATION_PATH = "/lectureMemberForm.es"
GWANGYANG_MUNICIPALITY_CODE = "1219000000"
GWANGYANG_MUNICIPALITY_NAME = "전남광주통합특별시 광양시"
GWANGYANG_SIDO = "전남광주통합특별시"
GWANGYANG_SIGUNGU = "광양시"
GWANGYANG_PAGE_SIZE = 10
GWANGYANG_FETCH_ATTEMPTS = 8
GWANGYANG_RETRY_BACKOFF_SECONDS = 0.5
GWANGYANG_MAX_WORKERS = 4
GWANGYANG_MAX_HTML_BYTES = 8_000_000
GWANGYANG_PARSER = (
    "gwangyang_official_10_education_sources+complete_pages+empty_sentinels+"
    "stable_first_last+current_details+status_and_course_bound_controls+"
    "pii_allowlist"
)
GWANGYANG_OWNERSHIP_SCOPE = "gwangyang_official_integrated_education_catalogues"


@dataclass(frozen=True)
class GwangyangSource:
    key: str
    branch: str
    mid: str
    even_cg: str
    edcc_cg: str = ""
    category: str = "평생학습"

    @property
    def page_title(self) -> str:
        if self.edcc_cg:
            return (
                f"{self.branch} | 주민자치센터 | 교육/강좌 : "
                "광양시청 통합예약 시스템"
            )
        return f"{self.branch} | 교육/강좌 : 광양시청 통합예약 시스템"


GWANGYANG_SOURCES: tuple[GwangyangSource, ...] = (
    GwangyangSource(
        "women_culture",
        "여성문화센터",
        "a90101000000",
        "EVEN001",
        category="여성문화센터",
    ),
    GwangyangSource(
        "citizen_it",
        "시민정보화교육",
        "a90105000000",
        "EVEN002",
        category="시민정보화교육",
    ),
    GwangyangSource(
        "digital_learning",
        "디지털배움터",
        "a90106000000",
        "EVEN004",
        category="디지털배움터",
    ),
    GwangyangSource(
        "resident_okgok",
        "옥곡면 주민자치센터",
        "a90103010000",
        "EVEN003",
        "EDCC001",
        "주민자치센터",
    ),
    GwangyangSource(
        "resident_golyak",
        "골약동 주민자치센터",
        "a90103020000",
        "EVEN003",
        "EDCC002",
        "주민자치센터",
    ),
    GwangyangSource(
        "resident_jungma",
        "중마동 주민자치센터",
        "a90103030000",
        "EVEN003",
        "EDCC003",
        "주민자치센터",
    ),
    GwangyangSource(
        "resident_taein",
        "태인동 주민자치센터",
        "a90103040000",
        "EVEN003",
        "EDCC004",
        "주민자치센터",
    ),
    GwangyangSource(
        "resident_geumho",
        "금호동 주민자치센터",
        "a90103050000",
        "EVEN003",
        "EDCC005",
        "주민자치센터",
    ),
    GwangyangSource(
        "resident_gwangyeong",
        "광영동 주민자치센터",
        "a90103060000",
        "EVEN003",
        "EDCC006",
        "주민자치센터",
    ),
    GwangyangSource(
        "resident_gwangyang_eup",
        "광양읍 주민자치센터",
        "a90103070000",
        "EVEN003",
        "EDCC007",
        "주민자치센터",
    ),
)

GWANGYANG_SOURCE_BY_KEY: Mapping[str, GwangyangSource] = {
    source.key: source for source in GWANGYANG_SOURCES
}

# The lifelong landing exposes only these four direct catalogue links.  The
# ten-source inventory is therefore an audited allowlist, and every individual
# source additionally proves its own branch/title/form contract.
GWANGYANG_LANDING_DISCOVERY_KEYS = frozenset(
    {
        "women_culture",
        "resident_okgok",
        "resident_gwangyeong",
        "resident_gwangyang_eup",
    }
)

GWANGYANG_DIGITAL_SUBSET_PROVIDER = "MUNI_GWANGYANG_GO_KR_900203DD"
GWANGYANG_DIGITAL_SUBSET_URL = (
    "https://gwangyang.go.kr/lecture.es?mid=a90106000000&&even_cg=EVEN004"
)
GWANGYANG_RESIDENT_SUBSET_CANDIDATE_ID = "MUNI_IR_7C2F9F55659E"
GWANGYANG_RESIDENT_SUBSET_PROVIDER = "MUNI_GWANGYANG_GO_KR_8F76BFD3"
GWANGYANG_RESIDENT_SUBSET_URL = (
    "https://gwangyang.go.kr/reserve/menu.es?mid=a90103030000"
)

GWANGYANG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    GWANGYANG_CANONICAL_CANDIDATE_ID: {
        "decision": "canonical_complete_education_aggregate",
        "provider": GWANGYANG_PROVIDER,
        "url": GWANGYANG_CANONICAL_URL,
        "owner": GWANGYANG_PROVIDER,
    },
    GWANGYANG_RESIDENT_SUBSET_CANDIDATE_ID: {
        "decision": "subset_alias_middle_town_resident_centre",
        "provider": GWANGYANG_RESIDENT_SUBSET_PROVIDER,
        "url": GWANGYANG_RESIDENT_SUBSET_URL,
        "owner": GWANGYANG_PROVIDER,
    },
}

GWANGYANG_EXISTING_TARGET_AUDIT: Mapping[str, Mapping[str, str]] = {
    GWANGYANG_DIGITAL_SUBSET_PROVIDER: {
        "decision": "subset_duplicate_digital_learning_only",
        "url": GWANGYANG_DIGITAL_SUBSET_URL,
        "owner": GWANGYANG_PROVIDER,
    }
}

GWANGYANG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": GWANGYANG_CANONICAL_URL,
    "source_count": 10,
    "historical_rows": 979,
    "data_pages": 102,
    "empty_sentinels": 10,
    "unique_identities": 979,
    "current_or_future_rows": 157,
    "current_source_counts": {
        "women_culture": 38,
        "citizen_it": 4,
        "digital_learning": 4,
        "resident_okgok": 8,
        "resident_golyak": 14,
        "resident_jungma": 25,
        "resident_taein": 0,
        "resident_geumho": 34,
        "resident_gwangyeong": 12,
        "resident_gwangyang_eup": 18,
    },
    "current_status_counts": {"CLOSED": 140, "OPEN": 11, "SCHEDULED": 6},
    "current_details_verified": 157,
    "open_application_controls": 11,
    "single_education_date_historical_rows": 39,
    "attachment_links_discarded": 126,
    "excluded_scope": "swimming_pool_static_and_sports_catalogues",
    "conclusion": "canonical_owner_supersedes_digital_and_resident_subsets",
}

GWANGYANG_PII_FIELDS_DISCARDED = (
    "강사명",
    "문의전화",
    "강의소개",
    "첨부파일",
    "instructor",
    "contact",
    "attachments",
    "free_form_detail",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GwangyangContractError(ValueError):
    """Raised when an official Gwangyang page no longer matches its contract."""


@dataclass(frozen=True)
class _ListPage:
    source_key: str
    page: int
    total: int
    last: int
    displayed_page: int
    rows: tuple[dict[str, Any], ...]


_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_DATETIME_RE = re.compile(r"20\d{2}-\d{2}-\d{2}\s+[0-2]\d:[0-5]\d")
_INFO_RE = re.compile(
    r"^총게시물\s*:\s*(?P<total>[\d,]+)\s*건\s*"
    r"페이지\s*:\s*(?P<page>\d+)\s*/\s*(?P<last>\d+)$"
)
_CAPACITY_RE = re.compile(
    r"^(?P<current>\d{1,7})\s*/\s*(?P<total>\d{1,7})"
    r"(?:\s*\(\s*(?P<wait>\d{1,7})\s*\))?$"
)
_DETAIL_CAPACITY_RE = re.compile(
    r"^신청\s*(?P<current>\d{1,7})\s*명\s*/\s*"
    r"정원\s*(?P<total>\d{1,7})\s*명"
    r"(?:\s*/?\s*\(?\s*대기\s*(?P<wait>\d{1,7})\s*명\s*\)?)?$"
)
_LIST_APPLICATION_ONCLICK_RE = re.compile(
    r"^location\.href\s*=\s*['\"](?P<href>[^'\"]+)['\"]\s*;?$"
)
_MOVE_VIEW_RE = re.compile(
    r"^move_view\(\s*(?P<identity>[1-9]\d*)\s*\)\s*;\s*return\s+false\s*;?$"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIST_HEADERS = (
    "번호",
    "강좌명",
    "접수기간",
    "교육기간 교육요일/시간",
    "선발방법",
    "신청/모집 (대기자)",
    "신청방법",
    "접수상태",
)
_LIST_CELL_CLASSES = (
    "num",
    "title",
    "apply_date",
    "leccation_date",
    "leccation_way",
    "leccation_num",
    "apply_way",
    "leccation_statue",
)
_DETAIL_FIELDS = frozenset(
    {
        "접수기간",
        "접수현황",
        "선발방법",
        "신청방법",
        "교육대상",
        "교육기간",
        "교육시간",
        "교육장",
        "강사명",
        "수강료",
        "문의전화",
        "강의소개",
        "첨부파일",
    }
)
_SAFE_DETAIL_FIELDS = frozenset(
    {
        "접수기간",
        "접수현황",
        "선발방법",
        "신청방법",
        "교육대상",
        "교육기간",
        "교육시간",
        "교육장",
        "수강료",
    }
)
_FORBIDDEN_DETAIL_FIELDS = _DETAIL_FIELDS - _SAFE_DETAIL_FIELDS
_DETAIL_STATUS: Mapping[str, tuple[str, str]] = {
    "OPEN": ("접수중", "ing"),
    "SCHEDULED": ("접수예정", "due"),
    "CLOSED": ("접수마감", "finish"),
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_key",
        "source_mid",
        "source_even_cg",
        "source_edcc_cg",
        "list_page",
        "source_status",
        "detail_status",
        "source_application_period",
        "source_education_period",
        "source_schedule",
        "source_selection_method",
        "source_application_method",
        "source_target",
        "source_venue",
        "source_fee",
        "capacity_current",
        "capacity_total",
        "waitlist_count",
        "list_capacity_current",
        "list_capacity_total",
        "list_waitlist_count",
        "capacity_snapshot_changed",
        "capacity_snapshot_source",
        "education_single_date",
        "historical_missing_status",
        "detail_verified",
        "application_confirmation_link_verified",
        "application_control_present",
        "application_control_contract",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "teacher",
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


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).lower()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _safe_parsed_port(parsed: Any) -> Optional[int]:
    try:
        return parsed.port
    except ValueError:
        return -1


def is_gwangyang_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != GWANGYANG_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GWANGYANG_HOST
        and _safe_parsed_port(parsed) is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GWANGYANG_LANDING_PATH
        and not parsed.params
        and query == {"mid": [GWANGYANG_LANDING_MID]}
        and not parsed.fragment
    )


is_target = is_gwangyang_education_target


def is_gwangyang_subset_target(target: Any) -> bool:
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    candidate = _clean(_target_value(target, "candidate_id"))
    return bool(
        (provider == GWANGYANG_DIGITAL_SUBSET_PROVIDER and url == GWANGYANG_DIGITAL_SUBSET_URL)
        or (
            provider == GWANGYANG_RESIDENT_SUBSET_PROVIDER
            and url == GWANGYANG_RESIDENT_SUBSET_URL
        )
        or candidate == GWANGYANG_RESIDENT_SUBSET_CANDIDATE_ID
    )


def _source_value(source: GwangyangSource | str) -> GwangyangSource:
    if isinstance(source, GwangyangSource):
        return source
    try:
        return GWANGYANG_SOURCE_BY_KEY[_clean(source)]
    except KeyError as exc:
        raise ValueError("unknown Gwangyang education source") from exc


def gwangyang_list_url(source: GwangyangSource | str, page: int = 1) -> str:
    current = _source_value(source)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    query: list[tuple[str, str]] = [
        ("mid", current.mid),
        ("even_cg", current.even_cg),
    ]
    if current.edcc_cg:
        query.append(("edcc_cg", current.edcc_cg))
    query.append(("nPage", str(page)))
    return f"https://{GWANGYANG_HOST}{GWANGYANG_LIST_PATH}?{urlencode(query)}"


def gwangyang_detail_url(source: GwangyangSource | str, identity: str) -> str:
    current = _source_value(source)
    value = _clean(identity)
    if _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    query = urlencode((('mid', current.mid), ('lec_seq', value), ('act', 'view')))
    return f"https://{GWANGYANG_HOST}{GWANGYANG_LIST_PATH}?{query}"


def gwangyang_application_url(source: GwangyangSource | str, identity: str) -> str:
    current = _source_value(source)
    value = _clean(identity)
    if _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    query = urlencode((('mid', current.mid), ('lec_seq', value)))
    return f"https://{GWANGYANG_HOST}{GWANGYANG_APPLICATION_PATH}?{query}"


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": GWANGYANG_CANONICAL_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    status = int(getattr(response, "status_code", 0))
    if status != 200:
        raise GwangyangContractError(f"unexpected HTTP status {status}")
    if getattr(response, "headers", {}).get("Location"):
        raise GwangyangContractError("redirect response is not accepted")
    content = getattr(response, "content", b"")
    if not content:
        raise GwangyangContractError("empty HTTP response")
    if len(content) > GWANGYANG_MAX_HTML_BYTES:
        raise GwangyangContractError("HTTP response exceeded the HTML byte cap")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > GWANGYANG_MAX_HTML_BYTES:
            raise GwangyangContractError("HTML fixture exceeded the byte cap")
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > GWANGYANG_MAX_HTML_BYTES:
            raise GwangyangContractError("HTML fixture exceeded the byte cap")
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher returned neither HTML nor a response")
    if len(content) > GWANGYANG_MAX_HTML_BYTES:
        raise GwangyangContractError("HTTP response exceeded the HTML byte cap")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _fetch_soup(
    url: str,
    *,
    timeout: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> BeautifulSoup:
    last_error: Optional[Exception] = None
    for attempt in range(GWANGYANG_FETCH_ATTEMPTS):
        session: Any = None
        try:
            session = session_factory()
            return _coerce_soup(fetcher(session, url, timeout))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < GWANGYANG_FETCH_ATTEMPTS:
                time.sleep(GWANGYANG_RETRY_BACKOFF_SECONDS * (attempt + 1))
        finally:
            _close_quietly(session)
    assert last_error is not None
    raise last_error


def _page_title(soup: BeautifulSoup, label: str) -> str:
    titles = soup.select("head > title")
    if len(titles) != 1:
        raise GwangyangContractError(f"{label}: document title missing or duplicated")
    value = _clean(titles[0].get_text(" ", strip=True))
    if not value:
        raise GwangyangContractError(f"{label}: document title is empty")
    return value


def _list_query_identity(value: Any, source: GwangyangSource) -> bool:
    parsed = urlparse(urljoin(GWANGYANG_CANONICAL_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected: dict[str, list[str]] = {
        "mid": [source.mid],
        "even_cg": [source.even_cg],
    }
    if source.edcc_cg:
        expected["edcc_cg"] = [source.edcc_cg]
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == GWANGYANG_HOST
        and _safe_parsed_port(parsed) is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GWANGYANG_LIST_PATH
        and query == expected
        and not parsed.fragment
    )


def _parse_landing(soup: BeautifulSoup) -> int:
    title = _page_title(soup, "canonical landing")
    if title != "강의안내/신청 : 평생학습도시":
        raise GwangyangContractError("canonical lifelong-learning landing title changed")
    found: set[str] = set()
    for anchor in soup.select("a[href]"):
        for source in GWANGYANG_SOURCES:
            if _list_query_identity(anchor.get("href"), source):
                found.add(source.key)
                break
    if not GWANGYANG_LANDING_DISCOVERY_KEYS.issubset(found):
        missing = sorted(GWANGYANG_LANDING_DISCOVERY_KEYS - found)
        raise GwangyangContractError(
            "canonical landing lost official catalogue links: " + ", ".join(missing)
        )
    return len(found)


def _parse_detail_identity(value: Any, source: GwangyangSource) -> str:
    parsed = urlparse(urljoin(GWANGYANG_CANONICAL_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identities = query.get("lec_seq", [])
    if (
        parsed.scheme != "https"
        or parsed.hostname != GWANGYANG_HOST
        or _safe_parsed_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != GWANGYANG_LIST_PATH
        or query.get("mid") != [source.mid]
        or query.get("act") != ["view"]
        or set(query) != {"mid", "lec_seq", "act"}
        or len(identities) != 1
        or _IDENTITY_RE.fullmatch(identities[0]) is None
        or parsed.fragment
    ):
        raise GwangyangContractError(f"{source.key}: course detail link changed")
    return identities[0]


def _parse_application_identity(value: Any, source: GwangyangSource) -> str:
    parsed = urlparse(urljoin(GWANGYANG_CANONICAL_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identities = query.get("lec_seq", [])
    if (
        parsed.scheme != "https"
        or parsed.hostname != GWANGYANG_HOST
        or _safe_parsed_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != GWANGYANG_APPLICATION_PATH
        or query.get("mid") != [source.mid]
        or set(query) != {"mid", "lec_seq"}
        or len(identities) != 1
        or _IDENTITY_RE.fullmatch(identities[0]) is None
        or parsed.fragment
    ):
        raise GwangyangContractError(f"{source.key}: application link changed")
    return identities[0]


def _datetime_pair(value: Any, label: str) -> tuple[datetime, datetime]:
    text = _clean(value)
    matches = _DATETIME_RE.findall(text)
    if len(matches) != 2:
        raise GwangyangContractError(f"{label}: datetime pair changed")
    values = [datetime.strptime(item, "%Y-%m-%d %H:%M") for item in matches]
    if values[0] > values[1]:
        raise GwangyangContractError(f"{label}: datetime range is reversed")
    return values[0], values[1]


def _date_span(value: Any, label: str) -> tuple[date, date, bool]:
    text = _clean(value)
    matches = _DATE_RE.findall(text)
    if len(matches) not in {1, 2}:
        raise GwangyangContractError(f"{label}: education date span changed")
    values = [date.fromisoformat(item) for item in matches]
    if len(values) == 1:
        return values[0], values[0], True
    if values[0] > values[1]:
        raise GwangyangContractError(f"{label}: education date span is reversed")
    return values[0], values[1], False


def _education_parts(cell: Any, label: str) -> tuple[str, str, date, date, bool]:
    periods = cell.select(":scope > p.acc_date")
    if len(periods) != 1:
        raise GwangyangContractError(f"{label}: education period structure changed")
    period = _clean(periods[0].get_text(" ", strip=True))
    start, end, single = _date_span(period, label)
    clone = BeautifulSoup(str(cell), "lxml").select_one("td")
    if clone is None:
        raise GwangyangContractError(f"{label}: education cell clone failed")
    for node in clone.select("p.acc_date"):
        node.decompose()
    schedule = _clean(clone.get_text(" ", strip=True))
    if not schedule:
        raise GwangyangContractError(f"{label}: education schedule is empty")
    return period, schedule, start, end, single


def _application_from_onclick(value: Any, source: GwangyangSource) -> str:
    match = _LIST_APPLICATION_ONCLICK_RE.fullmatch(_clean(value))
    if match is None:
        raise GwangyangContractError(f"{source.key}: application onclick changed")
    identity = _parse_application_identity(match.group("href"), source)
    return identity


def _list_status(
    cell: Any,
    source: GwangyangSource,
    identity: str,
    *,
    application_end: date,
    education_end: date,
    cutoff: date,
) -> tuple[str, str, str, bool]:
    controls = cell.find_all(["span", "button"], recursive=False)
    text = _clean(cell.get_text(" ", strip=True))
    if not controls and not text:
        if application_end >= cutoff or education_end >= cutoff:
            raise GwangyangContractError(
                f"course {identity}: current row has a missing source status"
            )
        return "CLOSED", "", "", True
    if len(controls) != 1:
        raise GwangyangContractError(f"course {identity}: list status control changed")
    control = controls[0]
    classes = set(control.get("class") or [])
    if control.name == "span" and text == "접수마감" and "finish" in classes:
        if control.get("onclick") or control.get("href"):
            raise GwangyangContractError(f"course {identity}: closed status became actionable")
        return "CLOSED", text, "", False
    if text == "접수대기" and "due" in classes:
        if control.name == "span":
            if control.get("onclick") or control.get("href"):
                raise GwangyangContractError(
                    f"course {identity}: scheduled status became actionable"
                )
        elif control.name == "button":
            alert_handler = _clean(control.get("onclick")).replace(" ", "")
            if (
                alert_handler != "accept_alert();returnfalse;"
                or _clean(control.get("type")).lower() != "submit"
                or control.get("href")
            ):
                raise GwangyangContractError(
                    f"course {identity}: scheduled alert control changed"
                )
        else:
            raise GwangyangContractError(
                f"course {identity}: scheduled status element changed"
            )
        return "SCHEDULED", text, "", False
    if control.name == "button" and text == "접수가능" and "ing" in classes:
        bound_identity = _application_from_onclick(control.get("onclick"), source)
        if bound_identity != identity:
            raise GwangyangContractError(f"course {identity}: list application identity mismatch")
        return (
            "OPEN",
            text,
            gwangyang_application_url(source, identity),
            False,
        )
    raise GwangyangContractError(f"course {identity}: unknown list status")


def _parse_list_row(
    row: Any,
    source: GwangyangSource,
    page: int,
    position: int,
    total: int,
    cutoff: date,
) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != len(_LIST_CELL_CLASSES):
        raise GwangyangContractError(f"{source.key} page {page}: row width changed")
    actual_classes = tuple(
        next((name for name in _LIST_CELL_CLASSES if name in (cell.get("class") or [])), "")
        for cell in cells
    )
    if actual_classes != _LIST_CELL_CLASSES:
        raise GwangyangContractError(f"{source.key} page {page}: row classes changed")

    expected_number = total - ((page - 1) * GWANGYANG_PAGE_SIZE + position)
    number = _clean(cells[0].get_text(" ", strip=True))
    if not number.isdigit() or int(number) != expected_number:
        raise GwangyangContractError(f"{source.key} page {page}: row number changed")

    anchors = cells[1].select(":scope > a.subject[href]")
    if len(anchors) != 1:
        raise GwangyangContractError(f"{source.key} page {page}: course link changed")
    anchor = anchors[0]
    identity = _parse_detail_identity(anchor.get("href"), source)
    onclick_match = _MOVE_VIEW_RE.fullmatch(_clean(anchor.get("onclick")))
    if onclick_match is None or onclick_match.group("identity") != identity:
        raise GwangyangContractError(f"course {identity}: view handler identity mismatch")
    title = _clean(anchor.get_text(" ", strip=True))
    if not title:
        raise GwangyangContractError(f"course {identity}: title is empty")

    application_period = _clean(cells[2].get_text(" ", strip=True))
    application_start_dt, application_end_dt = _datetime_pair(
        application_period, f"course {identity} application period"
    )
    education_period, schedule, start, end, single_date = _education_parts(
        cells[3], f"course {identity} education period"
    )
    selection_method = _clean(cells[4].get_text(" ", strip=True))
    application_method = _clean(cells[6].get_text(" ", strip=True))
    if application_method != "인터넷":
        raise GwangyangContractError(f"course {identity}: application method changed")
    if selection_method and selection_method != "선착순":
        raise GwangyangContractError(f"course {identity}: selection method changed")

    capacity_text = _clean(cells[5].get_text(" ", strip=True))
    capacity_match = _CAPACITY_RE.fullmatch(capacity_text)
    if capacity_match is None:
        raise GwangyangContractError(f"course {identity}: list capacity changed")
    capacity_current = int(capacity_match.group("current"))
    capacity_total = int(capacity_match.group("total"))
    waitlist_count = int(capacity_match.group("wait") or 0)
    if capacity_total == 0 and (capacity_current != 0 or waitlist_count != 0):
        raise GwangyangContractError(f"course {identity}: zero-capacity values are invalid")

    status, source_status, application_url, missing_status = _list_status(
        cells[7],
        source,
        identity,
        application_end=application_end_dt.date(),
        education_end=end,
        cutoff=cutoff,
    )
    if not selection_method and status != "CLOSED":
        raise GwangyangContractError(
            f"course {identity}: active selection method is empty"
        )
    if status == "OPEN" and not (
        application_start_dt.date() <= cutoff <= application_end_dt.date()
    ):
        raise GwangyangContractError(f"course {identity}: open status/date mismatch")
    # The source changes from ``접수대기`` to ``접수가능`` at a specific time.
    # A date-only crawl can therefore legitimately see due status on the
    # application start date before that time.
    if status == "SCHEDULED" and cutoff > application_start_dt.date():
        raise GwangyangContractError(f"course {identity}: scheduled status/date mismatch")

    return {
        "identity": identity,
        "title": title,
        "source_key": source.key,
        "list_page": page,
        "source_status": source_status,
        "status": status,
        "selection_method": selection_method,
        "application_method": application_method,
        "application_period": application_period,
        "application_start": application_start_dt,
        "application_end": application_end_dt,
        "education_period": education_period,
        "schedule": schedule,
        "start": start,
        "end": end,
        "single_date": single_date,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_count": waitlist_count,
        "detail_url": gwangyang_detail_url(source, identity),
        "list_application_url": application_url,
        "historical_missing_status": missing_status,
    }


def _validate_list_form(root: Any, source: GwangyangSource, page: int) -> None:
    forms = root.select("form#infoForm")
    if len(forms) != 1:
        raise GwangyangContractError(f"{source.key} page {page}: list form changed")
    form = forms[0]
    if _clean(form.get("method")).lower() != "post":
        raise GwangyangContractError(f"{source.key} page {page}: list form method changed")
    action = urlparse(urljoin(GWANGYANG_CANONICAL_URL, _clean(form.get("action"))))
    if (
        action.scheme != "https"
        or action.hostname != GWANGYANG_HOST
        or action.path != GWANGYANG_LIST_PATH
        or parse_qs(action.query, keep_blank_values=True) != {"mid": [source.mid]}
        or action.fragment
    ):
        raise GwangyangContractError(f"{source.key} page {page}: list form action changed")
    inputs = form.select("input[name]")
    values: dict[str, str] = {}
    for node in inputs:
        name = _clean(node.get("name"))
        if not name or name in values:
            raise GwangyangContractError(f"{source.key} page {page}: list form field duplicated")
        values[name] = _clean(node.get("value"))
    expected_names = {
        "mid",
        "seq",
        "act",
        "even_cg",
        "edcc_cg",
        "nPage",
        "keyWord",
        "_csrf",
    }
    if set(values) != expected_names or not values["_csrf"]:
        raise GwangyangContractError(f"{source.key} page {page}: list form fields changed")
    expected_values = {
        "mid": source.mid,
        "seq": "",
        "act": "list",
        "even_cg": source.even_cg,
        "edcc_cg": source.edcc_cg,
        "nPage": str(page),
        "keyWord": "",
    }
    if any(values[key] != value for key, value in expected_values.items()):
        raise GwangyangContractError(f"{source.key} page {page}: list form ownership changed")


def _validate_empty_body(tbody: Any, source: GwangyangSource, page: int) -> None:
    rows = tbody.find_all("tr", recursive=False)
    if len(rows) != 1:
        raise GwangyangContractError(f"{source.key} page {page}: empty table marker changed")
    cells = rows[0].find_all("td", recursive=False)
    if (
        len(cells) != 1
        or _clean(cells[0].get("colspan")) != "8"
        or len(cells[0].select(":scope > p.no_result.nodata")) != 1
        or _clean(cells[0].get_text(" ", strip=True)) != "해당 날짜는 교육이 없습니다."
    ):
        raise GwangyangContractError(f"{source.key} page {page}: empty result marker changed")


def _parse_list_page(
    soup: BeautifulSoup,
    source: GwangyangSource,
    page: int,
    cutoff: date,
) -> _ListPage:
    if _page_title(soup, f"{source.key} page {page}") != source.page_title:
        raise GwangyangContractError(f"{source.key} page {page}: branch title changed")
    _validate_list_form(soup, source, page)
    infos = soup.select(".bbs_info")
    if len(infos) != 1:
        raise GwangyangContractError(f"{source.key} page {page}: count summary changed")
    info_match = _INFO_RE.fullmatch(_clean(infos[0].get_text(" ", strip=True)))
    if info_match is None:
        raise GwangyangContractError(f"{source.key} page {page}: count summary text changed")
    total = int(info_match.group("total").replace(",", ""))
    displayed_page = int(info_match.group("page"))
    last = int(info_match.group("last"))
    expected_last = max(1, math.ceil(total / GWANGYANG_PAGE_SIZE))
    if displayed_page != page or last != expected_last:
        raise GwangyangContractError(f"{source.key} page {page}: page boundary changed")

    tables = soup.select("table.bbs_table")
    if len(tables) != 1:
        raise GwangyangContractError(f"{source.key} page {page}: list table changed")
    table = tables[0]
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _LIST_HEADERS:
        raise GwangyangContractError(f"{source.key} page {page}: list headers changed")
    tbodies = table.find_all("tbody", recursive=False)
    if len(tbodies) != 1:
        raise GwangyangContractError(f"{source.key} page {page}: table body changed")
    body_rows = tbodies[0].find_all("tr", recursive=False)
    course_rows = [row for row in body_rows if row.select_one("td.title > a.subject[href]")]
    if course_rows and len(course_rows) != len(body_rows):
        raise GwangyangContractError(f"{source.key} page {page}: mixed result rows")
    if not course_rows:
        _validate_empty_body(tbodies[0], source, page)
    rows = tuple(
        _parse_list_row(row, source, page, index, total, cutoff)
        for index, row in enumerate(course_rows)
    )
    return _ListPage(source.key, page, total, last, displayed_page, rows)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("source_status")),
            _clean(row.get("application_period")),
            _clean(row.get("education_period")),
            _clean(row.get("schedule")),
            str(row.get("capacity_current")),
            str(row.get("capacity_total")),
        )
        for row in rows
    )


def _detail_field_nodes(table: Any, identity: str) -> dict[str, Any]:
    tbodies = table.find_all("tbody", recursive=False)
    if len(tbodies) != 1:
        raise GwangyangContractError(f"course {identity}: detail table body changed")
    fields: dict[str, Any] = {}
    for row in tbodies[0].find_all("tr", recursive=False):
        labels = row.find_all("th", recursive=False)
        values = row.find_all("td", recursive=False)
        if len(labels) != 1 or len(values) != 1:
            raise GwangyangContractError(f"course {identity}: detail field structure changed")
        label = _clean(labels[0].get_text(" ", strip=True))
        if not label or label in fields:
            raise GwangyangContractError(f"course {identity}: detail field duplicated")
        fields[label] = values[0]
    if set(fields) != _DETAIL_FIELDS:
        raise GwangyangContractError(f"course {identity}: detail field set changed")
    return fields


def _safe_detail_values(fields: Mapping[str, Any], identity: str) -> dict[str, str]:
    values = {
        label: _clean(fields[label].get_text(" ", strip=True))
        for label in _SAFE_DETAIL_FIELDS
    }
    optional_empty = {"선발방법"}
    if any(not value for label, value in values.items() if label not in optional_empty):
        raise GwangyangContractError(f"course {identity}: safe detail field is empty")
    return values


def _detail_title_and_status(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
) -> tuple[str, str]:
    identity = _clean(listed.get("identity"))
    roots = soup.select(".bbs_detail_tit")
    if len(roots) != 1:
        raise GwangyangContractError(f"course {identity}: detail title root changed")
    headings = roots[0].find_all("h4", recursive=False)
    if len(headings) != 1:
        raise GwangyangContractError(f"course {identity}: detail heading changed")
    heading = headings[0]
    statuses = heading.find_all("span", recursive=False)
    if len(statuses) != 1:
        raise GwangyangContractError(f"course {identity}: detail status marker changed")
    marker = statuses[0]
    expected_text, expected_class = _DETAIL_STATUS[_clean(listed.get("status"))]
    detail_status = _clean(marker.get_text(" ", strip=True))
    if detail_status != expected_text or expected_class not in set(marker.get("class") or []):
        raise GwangyangContractError(f"course {identity}: list/detail status mismatch")
    clone = BeautifulSoup(str(heading), "lxml").select_one("h4")
    if clone is None:
        raise GwangyangContractError(f"course {identity}: detail heading clone failed")
    for node in clone.find_all("span", recursive=False):
        node.decompose()
    title = _clean(clone.get_text(" ", strip=True))
    if title != _clean(listed.get("title")):
        raise GwangyangContractError(f"course {identity}: list/detail title mismatch")
    return title, detail_status


def _fee_amount(value: str, identity: str) -> int:
    compact = _clean(value).replace(",", "")
    if compact in {"0", "0원", "무료", "없음", "해당없음"}:
        return 0
    leading = re.match(r"^(?P<amount>\d+)(?:원)?(?:\s*\(.*\))?$", compact)
    if leading is None:
        raise GwangyangContractError(f"course {identity}: tuition field changed")
    return int(leading.group("amount"))


def _branch_code(source: GwangyangSource) -> str:
    digest = hashlib.sha1(source.branch.encode("utf-8")).hexdigest()[:10]
    return f"gwangyang:{source.key}:{digest}"


def _parse_detail(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
    source: GwangyangSource,
    cutoff: date,
) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    if _page_title(soup, f"course {identity} detail") != source.page_title:
        raise GwangyangContractError(f"course {identity}: detail branch title changed")
    title, detail_status = _detail_title_and_status(soup, listed)
    tables = soup.select("table.bbs_table.type02")
    if len(tables) != 1:
        raise GwangyangContractError(f"course {identity}: structured detail table changed")
    captions = tables[0].find_all("caption", recursive=False)
    if len(captions) != 1 or not _clean(captions[0].get_text(" ", strip=True)).startswith(
        "강좌조회 -"
    ):
        raise GwangyangContractError(f"course {identity}: detail caption changed")
    field_nodes = _detail_field_nodes(tables[0], identity)
    # Values for the four forbidden fields are intentionally never read.
    if not _FORBIDDEN_DETAIL_FIELDS.issubset(field_nodes):
        raise GwangyangContractError(f"course {identity}: discarded field shape changed")
    fields = _safe_detail_values(field_nodes, identity)

    apply_start, apply_end = _datetime_pair(
        fields["접수기간"], f"course {identity} detail application period"
    )
    start, end, _single = _date_span(
        fields["교육기간"], f"course {identity} detail education period"
    )
    if (
        apply_start != listed.get("application_start")
        or apply_end != listed.get("application_end")
        or start != listed.get("start")
        or end != listed.get("end")
    ):
        raise GwangyangContractError(f"course {identity}: list/detail date mismatch")
    if _normalized(fields["교육시간"]) != _normalized(listed.get("schedule")):
        raise GwangyangContractError(f"course {identity}: list/detail schedule mismatch")
    if fields["선발방법"] != _clean(listed.get("selection_method")):
        raise GwangyangContractError(f"course {identity}: list/detail selection mismatch")
    if fields["신청방법"] != _clean(listed.get("application_method")):
        raise GwangyangContractError(f"course {identity}: list/detail application method mismatch")

    capacity_match = _DETAIL_CAPACITY_RE.fullmatch(fields["접수현황"])
    if capacity_match is None:
        raise GwangyangContractError(f"course {identity}: detail capacity changed")
    capacity_current = int(capacity_match.group("current"))
    capacity_total = int(capacity_match.group("total"))
    detail_wait = capacity_match.group("wait")
    waitlist_count = (
        int(detail_wait)
        if detail_wait is not None
        else int(listed.get("waitlist_count") or 0)
    )
    capacity_snapshot_changed = bool(
        capacity_current != listed.get("capacity_current")
        or capacity_total != listed.get("capacity_total")
        or waitlist_count != int(listed.get("waitlist_count") or 0)
    )

    confirmation_links = soup.select('a[href*="lectureMemberForm.es"]')
    if len(confirmation_links) != 1:
        raise GwangyangContractError(
            f"course {identity}: confirmation application link changed"
        )
    confirmation_identity = _parse_application_identity(
        confirmation_links[0].get("href"), source
    )
    if confirmation_identity != identity:
        raise GwangyangContractError(
            f"course {identity}: confirmation application identity mismatch"
        )
    canonical_application_url = gwangyang_application_url(source, identity)
    status = _clean(listed.get("status"))
    application_buttons = soup.select("button.go_apply")
    application_control = status == "OPEN"
    if application_control:
        if len(application_buttons) != 1:
            raise GwangyangContractError(
                f"course {identity}: open detail lacks one application button"
            )
        button = application_buttons[0]
        classes = set(button.get("class") or [])
        if (
            _clean(button.get("onclick")).replace(" ", "") != "beforeApply()"
            or "btn_darkgray" not in classes
            or _normalized(button.get_text(" ", strip=True)) != _normalized("신청 하기")
            or _clean(listed.get("list_application_url")) != canonical_application_url
        ):
            raise GwangyangContractError(
                f"course {identity}: open application button contract changed"
            )
    elif application_buttons or listed.get("list_application_url"):
        raise GwangyangContractError(
            f"course {identity}: inactive detail exposes an application button"
        )
    if status == "OPEN" and not (apply_start.date() <= cutoff <= apply_end.date()):
        raise GwangyangContractError(f"course {identity}: detail open status/date mismatch")
    if status == "SCHEDULED" and cutoff > apply_start.date():
        raise GwangyangContractError(f"course {identity}: detail scheduled status/date mismatch")

    venue = fields["교육장"]
    fee = fields["수강료"]
    fee_amount = _fee_amount(fee, identity)
    row: dict[str, Any] = {
        "provider": GWANGYANG_PROVIDER,
        "provider_course_id": f"{GWANGYANG_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": source.branch,
        "branch_code": _branch_code(source),
        "preserve_branch": True,
        "category": source.category,
        "program_type": "교육",
        "raw_url": gwangyang_detail_url(source, identity),
        "application_url": canonical_application_url if application_control else "",
        "application_type": (
            "ONLINE_RESERVATION_LOGIN_REQUIRED" if application_control else "INFO_ONLY"
        ),
        "application_method": "온라인",
        "application_methods": ["온라인"],
        "reservation_available": application_control,
        "status": status,
        "fee": fee,
        "fee_amount": fee_amount,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": fields["접수기간"],
        "apply_start": apply_start.date().isoformat(),
        "apply_end": apply_end.date().isoformat(),
        "schedule_raw": fields["교육시간"],
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_count": waitlist_count,
        "target": fields["교육대상"],
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GWANGYANG_PARSER,
        "municipality_code": GWANGYANG_MUNICIPALITY_CODE,
        "municipality_full_name": GWANGYANG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_key": source.key,
            "source_mid": source.mid,
            "source_even_cg": source.even_cg,
            "source_edcc_cg": source.edcc_cg,
            "list_page": int(listed.get("list_page") or 0),
            "source_status": _clean(listed.get("source_status")),
            "detail_status": detail_status,
            "source_application_period": fields["접수기간"],
            "source_education_period": fields["교육기간"],
            "source_schedule": fields["교육시간"],
            "source_selection_method": fields["선발방법"],
            "source_application_method": fields["신청방법"],
            "source_target": fields["교육대상"],
            "source_venue": venue,
            "source_fee": fee,
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "waitlist_count": waitlist_count,
            "list_capacity_current": int(listed.get("capacity_current") or 0),
            "list_capacity_total": int(listed.get("capacity_total") or 0),
            "list_waitlist_count": int(listed.get("waitlist_count") or 0),
            "capacity_snapshot_changed": capacity_snapshot_changed,
            "capacity_snapshot_source": "detail",
            "education_single_date": bool(listed.get("single_date")),
            "historical_missing_status": False,
            "detail_verified": True,
            "application_confirmation_link_verified": True,
            "application_control_present": application_control,
            "application_control_contract": (
                "status_open_plus_visible_beforeApply_plus_course_bound_confirmation"
                if application_control
                else "inactive_status_without_visible_button_plus_bound_confirmation_only"
            ),
            "service_family": "education",
        },
        "_source_end_date": end,
    }
    return row


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "_source_end_date"}
        }
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail description persisted")
    if _clean((raw_fields or {}).get("service_family")) != "education":
        errors.append("non-education row reached the result")
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
        "request_count": 0,
        "landing_requests": 0,
        "landing_verified": False,
        "landing_source_links_verified": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "sentinel_requests": 0,
        "sentinel_verified_count": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "single_education_date_count": 0,
        "returned_count": 0,
        "identity_duplicate_count": 0,
        "raw_url_duplicate_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": error,
        "municipality_code": GWANGYANG_MUNICIPALITY_CODE,
        "municipality_name": GWANGYANG_MUNICIPALITY_NAME,
        "canonical_candidate_id": GWANGYANG_CANONICAL_CANDIDATE_ID,
        "canonical_url": GWANGYANG_CANONICAL_URL,
        "ownership_scope": GWANGYANG_OWNERSHIP_SCOPE,
        "source_count": len(GWANGYANG_SOURCES),
    }


def collect_gwangyang_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 250,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GWANGYANG_MAX_WORKERS,
    recovery_passes: int = 0,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Gwangyang education snapshot."""

    meta = _base_meta()
    if not is_gwangyang_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Gwangyang education owner"
        )
        return [], GWANGYANG_PARSER, meta
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
        or isinstance(recovery_passes, bool)
        or not isinstance(recovery_passes, int)
        or recovery_passes < 0
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], GWANGYANG_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], GWANGYANG_PARSER, meta

    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    workers = min(max_workers, GWANGYANG_MAX_WORKERS)
    errors: list[str] = []

    try:
        landing = _fetch_soup(
            GWANGYANG_CANONICAL_URL,
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        meta["landing_source_links_verified"] = _parse_landing(landing)
        meta["landing_requests"] = 1
        meta["landing_verified"] = True
        meta["pages"] = 1
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"canonical landing: {type(exc).__name__}: {_clean(exc)}"
        )
        return [], GWANGYANG_PARSER, meta

    first_pages: dict[str, _ListPage] = {}
    failed_first_pages: dict[str, tuple[GwangyangSource, Exception]] = {}

    def fetch_list(source: GwangyangSource, page: int) -> _ListPage:
        soup = _fetch_soup(
            gwangyang_list_url(source, page),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return _parse_list_page(soup, source, page, cutoff)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_list, source, 1): source for source in GWANGYANG_SOURCES
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                first_pages[source.key] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                failed_first_pages[source.key] = (source, exc)
    for _pass in range(recovery_passes):
        if not failed_first_pages:
            break
        for source_key, (source, _previous_error) in list(failed_first_pages.items()):
            try:
                first_pages[source.key] = fetch_list(source, 1)
                meta["list_requests"] += 1
                meta["pages"] += 1
                meta["first_page_recoveries"] = meta.get("first_page_recoveries", 0) + 1
                del failed_first_pages[source_key]
            except Exception as exc:
                failed_first_pages[source_key] = (source, exc)
    for source, exc in failed_first_pages.values():
        errors.append(f"{source.key} page 1: {type(exc).__name__}: {_clean(exc)}")
    if errors or len(first_pages) != len(GWANGYANG_SOURCES):
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        meta["request_count"] = meta["pages"]
        return [], GWANGYANG_PARSER, meta

    source_totals = {
        source.key: first_pages[source.key].total for source in GWANGYANG_SOURCES
    }
    source_pages = {
        source.key: first_pages[source.key].last for source in GWANGYANG_SOURCES
    }
    required_list_requests = sum(last + 3 for last in source_pages.values())
    meta.update(
        {
            "source_totals": source_totals,
            "source_pages": source_pages,
            "declared_source_rows": sum(source_totals.values()),
            "required_list_requests": required_list_requests,
        }
    )
    if required_list_requests > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "request_count": meta["pages"],
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of "
                    f"{required_list_requests} required list requests"
                ),
            }
        )
        return [], GWANGYANG_PARSER, meta

    jobs: list[tuple[str, GwangyangSource, int]] = []
    for source in GWANGYANG_SOURCES:
        last = source_pages[source.key]
        jobs.extend(("data", source, page) for page in range(2, last + 1))
        jobs.extend(
            (
                ("sentinel", source, last + 1),
                ("first_recheck", source, 1),
                ("last_recheck", source, last),
            )
        )
    parsed_jobs: dict[tuple[str, str, int], _ListPage] = {}
    failed_jobs: dict[
        tuple[str, str, int], tuple[str, GwangyangSource, int, Exception]
    ] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_list, source, page): (kind, source, page)
            for kind, source, page in jobs
        }
        for future in as_completed(futures):
            kind, source, page = futures[future]
            try:
                parsed_jobs[(kind, source.key, page)] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                failed_jobs[(kind, source.key, page)] = (kind, source, page, exc)
    for _pass in range(recovery_passes):
        if not failed_jobs:
            break
        for key, (kind, source, page, _previous_error) in list(failed_jobs.items()):
            try:
                parsed_jobs[key] = fetch_list(source, page)
                meta["list_requests"] += 1
                meta["pages"] += 1
                meta["list_job_recoveries"] = meta.get("list_job_recoveries", 0) + 1
                del failed_jobs[key]
            except Exception as exc:
                failed_jobs[key] = (kind, source, page, exc)
    for kind, source, page, exc in failed_jobs.values():
        errors.append(
            f"{source.key} {kind} page {page}: "
            f"{type(exc).__name__}: {_clean(exc)}"
        )

    listed: list[dict[str, Any]] = []
    source_current_counts: dict[str, int] = {}
    source_data_page_counts: dict[str, int] = {}
    for source in GWANGYANG_SOURCES:
        first = first_pages[source.key]
        last = first.last
        total = first.total
        pages: dict[int, _ListPage] = {1: first}
        for page in range(2, last + 1):
            parsed = parsed_jobs.get(("data", source.key, page))
            if parsed is not None:
                pages[page] = parsed
        source_rows: list[dict[str, Any]] = []
        for page in range(1, last + 1):
            parsed = pages.get(page)
            if parsed is None:
                errors.append(f"{source.key} data page {page}: response missing")
                continue
            if parsed.total != total or parsed.last != last:
                errors.append(f"{source.key} data page {page}: boundary changed")
            expected = (
                0
                if total == 0
                else min(GWANGYANG_PAGE_SIZE, total - (page - 1) * GWANGYANG_PAGE_SIZE)
            )
            if len(parsed.rows) != expected:
                errors.append(
                    f"{source.key} data page {page}: row count {len(parsed.rows)} != {expected}"
                )
            source_rows.extend(parsed.rows)
        if len(source_rows) != total:
            errors.append(
                f"{source.key}: complete row count {len(source_rows)} != declared {total}"
            )
        source_data_page_counts[source.key] = len(pages)

        sentinel = parsed_jobs.get(("sentinel", source.key, last + 1))
        if sentinel is None:
            errors.append(f"{source.key}: immediate post-last sentinel missing")
        elif (
            sentinel.page != last + 1
            or sentinel.total != total
            or sentinel.last != last
            or sentinel.rows
        ):
            errors.append(f"{source.key}: immediate post-last sentinel is not stable empty")
        else:
            meta["sentinel_verified_count"] += 1
            meta["sentinel_requests"] += 1

        first_recheck = parsed_jobs.get(("first_recheck", source.key, 1))
        last_recheck = parsed_jobs.get(("last_recheck", source.key, last))
        expected_last_rows = pages.get(last).rows if pages.get(last) else ()
        if first_recheck is None or last_recheck is None:
            errors.append(f"{source.key}: first/last stability recheck missing")
        else:
            meta["stability_rechecks"] += 2
            if (
                first_recheck.total != total
                or first_recheck.last != last
                or _page_signature(first_recheck.rows) != _page_signature(first.rows)
            ):
                errors.append(f"{source.key}: first-page stability recheck changed")
            if (
                last_recheck.total != total
                or last_recheck.last != last
                or _page_signature(last_recheck.rows) != _page_signature(expected_last_rows)
            ):
                errors.append(f"{source.key}: last-page stability recheck changed")
        source_current_counts[source.key] = sum(
            row["end"] >= cutoff for row in source_rows
        )
        listed.extend(source_rows)

    identities = [_clean(row.get("identity")) for row in listed]
    identity_duplicate_count = len(identities) - len(set(identities))
    if identity_duplicate_count:
        errors.append(f"{identity_duplicate_count} duplicate official identities")
    detail_urls = [_clean(row.get("detail_url")) for row in listed]
    raw_url_duplicate_count = len(detail_urls) - len(set(detail_urls))
    if raw_url_duplicate_count:
        errors.append(f"{raw_url_duplicate_count} duplicate official detail URLs")
    list_complete = bool(
        not errors
        and meta["list_requests"] == required_list_requests
        and meta["sentinel_verified_count"] == len(GWANGYANG_SOURCES)
        and meta["stability_rechecks"] == len(GWANGYANG_SOURCES) * 2
        and len(listed) == sum(source_totals.values())
    )
    current_listed = [row for row in listed if row["end"] >= cutoff]
    meta.update(
        {
            "data_pages": sum(source_pages.values()),
            "source_data_page_counts": source_data_page_counts,
            "source_rows": len(listed),
            "current_source_count": len(current_listed),
            "expired_count": len(listed) - len(current_listed),
            "single_education_date_count": sum(bool(row["single_date"]) for row in listed),
            "identity_duplicate_count": identity_duplicate_count,
            "raw_url_duplicate_count": raw_url_duplicate_count,
            "source_current_counts": source_current_counts,
            "pagination_complete": list_complete,
        }
    )
    if not list_complete:
        meta["request_count"] = meta["pages"]
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], GWANGYANG_PARSER, meta

    if len(current_listed) > detail_limit:
        meta.update(
            {
                "source_cap_reached": True,
                "request_count": meta["pages"],
                "configured_collection_error": (
                    f"detail_limit cap allows {detail_limit} of "
                    f"{len(current_listed)} required current details"
                ),
            }
        )
        return [], GWANGYANG_PARSER, meta

    meta["detail_attempts"] = len(current_listed)
    detailed: dict[str, dict[str, Any]] = {}
    detail_errors: list[str] = []
    failed_details: dict[str, tuple[Mapping[str, Any], Exception]] = {}

    def fetch_detail(listed_row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        identity = _clean(listed_row.get("identity"))
        source = GWANGYANG_SOURCE_BY_KEY[_clean(listed_row.get("source_key"))]
        soup = _fetch_soup(
            _clean(listed_row.get("detail_url")),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return identity, _parse_detail(soup, listed_row, source, cutoff)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_detail, row): row for row in current_listed}
        for future in as_completed(futures):
            listed_row = futures[future]
            identity = _clean(listed_row.get("identity"))
            try:
                parsed_identity, row = future.result()
                if parsed_identity in detailed:
                    raise GwangyangContractError("duplicate parsed detail identity")
                detailed[parsed_identity] = row
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                failed_details[identity] = (listed_row, exc)
    for _pass in range(recovery_passes):
        if not failed_details:
            break
        for identity, (listed_row, _previous_error) in list(failed_details.items()):
            try:
                parsed_identity, row = fetch_detail(listed_row)
                if parsed_identity in detailed:
                    raise GwangyangContractError("duplicate parsed detail identity")
                detailed[parsed_identity] = row
                meta["detail_pages"] += 1
                meta["pages"] += 1
                meta["detail_recoveries"] = meta.get("detail_recoveries", 0) + 1
                del failed_details[identity]
            except Exception as exc:
                failed_details[identity] = (listed_row, exc)
    for identity, (_listed_row, exc) in failed_details.items():
        detail_errors.append(
            f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"
        )
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        not detail_errors
        and meta["detail_pages"] == len(current_listed)
        and len(detailed) == len(current_listed)
    )
    ordered = [detailed[identity] for identity in identities if identity in detailed]
    application_controls_complete = bool(
        details_complete
        and all(
            bool(row.get("raw_fields", {}).get("application_confirmation_link_verified"))
            for row in ordered
        )
    )
    result: list[dict[str, Any]] = []
    if details_complete and application_controls_complete and not errors:
        for row in ordered:
            errors.extend(_privacy_errors(row))
        if not errors:
            persistable: list[dict[str, Any]] = []
            for row in ordered:
                clean_row = dict(row)
                clean_row.pop("_source_end_date", None)
                persistable.append(clean_row)
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(persistable))
            except Exception as exc:
                errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                result = []
            if len(result) != len(persistable):
                errors.append(
                    "dedupe changed official identity cardinality "
                    f"{len(persistable)} to {len(result)}"
                )
                result = []

    snapshot_complete = bool(
        list_complete
        and details_complete
        and application_controls_complete
        and not errors
    )
    if not snapshot_complete:
        result = []
    semantic_counter = Counter(
        (_normalized(row.get("title")), _clean(row.get("period")), _clean(row.get("branch")))
        for row in ordered
    )
    meta.update(
        {
            "request_count": meta["pages"],
            "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
            "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
            "application_control_count": sum(
                bool(row.get("raw_fields", {}).get("application_control_present"))
                for row in ordered
            ),
            "application_confirmation_links_verified": sum(
                bool(
                    row.get("raw_fields", {}).get(
                        "application_confirmation_link_verified"
                    )
                )
                for row in ordered
            ),
            "semantic_duplicate_group_count": sum(
                count > 1 for count in semantic_counter.values()
            ),
            "semantic_duplicate_excess_rows": sum(
                max(0, count - 1) for count in semantic_counter.values()
            ),
            "semantic_duplicate_policy": "preserve_distinct_official_lec_seq",
            "details_complete": details_complete,
            "application_controls_complete": application_controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not current_listed),
            "no_current_reason": (
                "the complete ten-source catalogue has no current/future education"
                if snapshot_complete and not current_listed
                else ""
            ),
            "municipality_coverage": [GWANGYANG_MUNICIPALITY_CODE],
            "candidate_audit": {
                key: dict(value) for key, value in GWANGYANG_CANDIDATE_AUDIT.items()
            },
            "existing_target_audit": {
                key: dict(value)
                for key, value in GWANGYANG_EXISTING_TARGET_AUDIT.items()
            },
            "discovery_audit": dict(GWANGYANG_DISCOVERY_AUDIT),
            "superseded_providers": [
                GWANGYANG_DIGITAL_SUBSET_PROVIDER,
                GWANGYANG_RESIDENT_SUBSET_PROVIDER,
            ],
            "pii_fields_discarded": list(GWANGYANG_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "network_concurrency": workers,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, GWANGYANG_PARSER, meta


class _SessionLease:
    """Delegate to a pooled session while leaving lifecycle to the pool."""

    def __init__(self, managed_session: Any) -> None:
        self._managed_session = managed_session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._managed_session, name)

    def close(self) -> None:
        return None


class _ThreadSessionPool:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._local = threading.local()
        self._lock = threading.Lock()
        self._sessions: list[Any] = []

    def acquire(self) -> _SessionLease:
        managed_session = getattr(self._local, "managed_session", None)
        if managed_session is None:
            managed_session = self._session_factory()
            self._local.managed_session = managed_session
            with self._lock:
                self._sessions.append(managed_session)
        return _SessionLease(managed_session)

    def close(self) -> None:
        with self._lock:
            sessions, self._sessions = self._sessions, []
        for managed_session in sessions:
            _close_quietly(managed_session)


def collect_gwangyang_education_managed(
    target: Any,
    *,
    session_factory: SessionFactory,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Run with one managed session per worker and close every session at exit."""

    pool = _ThreadSessionPool(session_factory)
    try:
        kwargs.setdefault("recovery_passes", 1)
        return collect_gwangyang_education(
            target,
            session_factory=pool.acquire,
            **kwargs,
        )
    finally:
        pool.close()


collect = collect_gwangyang_education


__all__ = [
    "GWANGYANG_CANONICAL_CANDIDATE_ID",
    "GWANGYANG_CANONICAL_URL",
    "GWANGYANG_CANDIDATE_AUDIT",
    "GWANGYANG_DIGITAL_SUBSET_PROVIDER",
    "GWANGYANG_DIGITAL_SUBSET_URL",
    "GWANGYANG_DISCOVERY_AUDIT",
    "GWANGYANG_EXISTING_TARGET_AUDIT",
    "GWANGYANG_MAX_WORKERS",
    "GWANGYANG_MUNICIPALITY_CODE",
    "GWANGYANG_MUNICIPALITY_NAME",
    "GWANGYANG_PARSER",
    "GWANGYANG_PROVIDER",
    "GWANGYANG_RESIDENT_SUBSET_CANDIDATE_ID",
    "GWANGYANG_RESIDENT_SUBSET_PROVIDER",
    "GWANGYANG_RESIDENT_SUBSET_URL",
    "GWANGYANG_SOURCES",
    "GwangyangContractError",
    "GwangyangSource",
    "collect",
    "collect_gwangyang_education",
    "collect_gwangyang_education_managed",
    "gwangyang_application_url",
    "gwangyang_detail_url",
    "gwangyang_list_url",
    "is_gwangyang_education_target",
    "is_gwangyang_subset_target",
    "is_target",
]
