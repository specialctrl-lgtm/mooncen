"""Fail-closed collector for Gwangju Dong-gu's official course catalogue.

Search promotion found one historical course detail, two municipal landing
pages and two data.go.kr descriptions.  The executable owner is the official
lifelong-learning ``/lecture.es`` search list.  The existing detail provider
is retained as owner but its single-detail URL is superseded by this complete
list URL.

The source currently advertises 38 records in ten-row pages while its visual
page counter incorrectly says two pages (it is calculated at twenty rows per
page).  We therefore derive the data boundary from the declared total and the
observed ten-row table contract, require the immediate empty sentinel, and
recheck both boundary pages.  Only current/future rows require details.

Instructor/manager/contact values, free-form descriptions, cancellation copy
and the applicant form's personal fields are deliberately never read or
persisted.  The embedded application form is hidden for the current catalogue;
an offline rolling-reception course is not misreported as an online booking.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GWANGJU_DONGGU_PROVIDER = "MUNI_WWW_DONGGU_KR_4B011833"
GWANGJU_DONGGU_CANONICAL_CANDIDATE_ID = "MUNI_IR_E6245F0A69B0"
GWANGJU_DONGGU_LANDING_CANDIDATE_ID = "MUNI_IR_EBE2A47F3A4F"
GWANGJU_DONGGU_DEPRECATED_LANDING_PROVIDER = "MUNI_WWW_DONGGU_KR_F3EB5A73"
GWANGJU_DONGGU_HOST = "www.donggu.kr"
GWANGJU_DONGGU_PATH = "/lecture.es"
GWANGJU_DONGGU_MID = "a60202000000"
GWANGJU_DONGGU_CANONICAL_URL = (
    "https://www.donggu.kr/lecture.es?"
    "mid=a60202000000&act=search_list&nPage=1"
)
GWANGJU_DONGGU_EXISTING_DETAIL_URL = (
    "https://www.donggu.kr/lecture.es?mid=a60202000000&lec_no=195&"
    "act=view&return_act=search_list"
)
GWANGJU_DONGGU_LIFELONG_LANDING_URL = "https://www.donggu.kr/index.es?sid=a6"
GWANGJU_DONGGU_GENERIC_LANDING_URL = "https://www.donggu.kr/index.es?sid=a1"
GWANGJU_DONGGU_LIBRARY_DATASET_URL = (
    "https://www.data.go.kr/data/15120373/fileData.do"
)
GWANGJU_DONGGU_NOTICE_DATASET_URL = (
    "https://www.data.go.kr/data/15121029/fileData.do"
)
GWANGJU_DONGGU_MUNICIPALITY_CODE = "1221000000"
GWANGJU_DONGGU_MUNICIPALITY_NAME = "전남광주통합특별시 동구"
GWANGJU_DONGGU_PAGE_SIZE = 10
GWANGJU_DONGGU_BROKEN_COUNTER_PAGE_SIZE = 20
GWANGJU_DONGGU_MAX_WORKERS = 4
GWANGJU_DONGGU_FETCH_ATTEMPTS = 3
GWANGJU_DONGGU_RETRY_BACKOFF_SECONDS = 0.2
GWANGJU_DONGGU_MAX_HTML_BYTES = 6_000_000
GWANGJU_DONGGU_PARSER = (
    "gwangju_donggu_official_lifelong_complete_total_pages+empty_sentinel+"
    "stable_first_last+current_details+institution_branch+"
    "public_application_visibility+pii_allowlist"
)
GWANGJU_DONGGU_OWNERSHIP_SCOPE = (
    "gwangju_donggu_official_lifelong_lecture_search_catalogue"
)

GWANGJU_DONGGU_PAGE_TITLES = frozenset(
    {
        "프로그램 안내 | 프로그램 : 광주광역시 동구 평생학습도시",
        "프로그램 안내 | 프로그램 : 전남광주통합특별시 동구 평생학습도시",
    }
)

GWANGJU_DONGGU_INSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("", "기관분류"),
    ("ORGA001", "평생학습강좌"),
    ("ORGA002", "동구문화센터"),
    ("ORGA004", "아동/청소년/여성"),
    ("ORGA003", "동구국민체육센터"),
    ("ORGA005", "사회복지기관"),
    ("ORGA015", "외부기관"),
    ("ORGA006", "도서관"),
    ("ORGA007", "야학"),
    ("ORGA008", "문화원/문화센터"),
    ("ORGA009", "영어체험센터"),
    ("ORGA010", "초등학교"),
    ("ORGA011", "중학교"),
    ("ORGA012", "고등학교"),
    ("ORGA014", "사단법인평생교육기관"),
)
GWANGJU_DONGGU_TARGETS: tuple[tuple[str, str], ...] = (
    ("", "대상분류"),
    ("TARG001", "공통"),
    ("TARG002", "아동/청소년"),
    ("TARG003", "성인"),
    ("TARG004", "어르신"),
)
GWANGJU_DONGGU_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("", "강좌분야"),
    ("TYPE001", "예능"),
    ("TYPE002", "컴퓨터"),
    ("TYPE003", "어학"),
    ("TYPE004", "스포츠"),
    ("TYPE005", "취업교육"),
    ("TYPE006", "취미"),
    ("TYPE007", "기타"),
)
GWANGJU_DONGGU_STATUSES: tuple[tuple[str, str], ...] = (
    ("", "접수상태"),
    ("A", "수시접수"),
    ("W", "접수준비"),
    ("T", "접수중"),
    ("E", "접수마감"),
)

GWANGJU_DONGGU_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    GWANGJU_DONGGU_CANONICAL_CANDIDATE_ID: {
        "decision": "include_existing_owner_but_expand_single_detail_to_complete_list",
        "provider": GWANGJU_DONGGU_PROVIDER,
        "url": GWANGJU_DONGGU_EXISTING_DETAIL_URL,
        "canonical_url": GWANGJU_DONGGU_CANONICAL_URL,
        "owner": GWANGJU_DONGGU_PROVIDER,
    },
    GWANGJU_DONGGU_LANDING_CANDIDATE_ID: {
        "decision": "exclude_generic_city_landing_without_executable_course_rows",
        "provider": GWANGJU_DONGGU_DEPRECATED_LANDING_PROVIDER,
        "url": GWANGJU_DONGGU_GENERIC_LANDING_URL,
        "owner": GWANGJU_DONGGU_PROVIDER,
    },
    "SEARCH_RESULT_LIFELONG_LANDING_A6": {
        "decision": "official_discovery_evidence_only",
        "provider": GWANGJU_DONGGU_PROVIDER,
        "url": GWANGJU_DONGGU_LIFELONG_LANDING_URL,
        "owner": GWANGJU_DONGGU_PROVIDER,
    },
    "SEARCH_RESULT_DATA_GO_KR_15120373": {
        "decision": "exclude_external_library_dataset_not_live_lifelong_catalogue",
        "provider": "DATA_GO_KR",
        "url": GWANGJU_DONGGU_LIBRARY_DATASET_URL,
        "owner": "separate_library_dataset",
    },
    "SEARCH_RESULT_DATA_GO_KR_15121029": {
        "decision": "exclude_external_notice_dataset_not_live_course_catalogue",
        "provider": "DATA_GO_KR",
        "url": GWANGJU_DONGGU_NOTICE_DATASET_URL,
        "owner": GWANGJU_DONGGU_PROVIDER,
    },
}

GWANGJU_DONGGU_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": GWANGJU_DONGGU_CANONICAL_URL,
    "historical_rows": 38,
    "data_pages": 4,
    "empty_sentinel_page": 5,
    "source_counter_last_page": 2,
    "source_counter_defect": "counter_uses_20_rows_while_table_pages_at_10",
    "unique_identities": 38,
    "current_or_future_rows": 2,
    "current_details_verified": 2,
    "current_status_counts": {"OPEN": 1, "CLOSED": 1},
    "historical_source_status_counts": {"접수마감": 33, "수시접수": 5},
    "category_counts": {"기타": 29, "예능": 6, "취업교육": 2, "취미": 1},
    "target_counts": {"공통": 28, "성인": 10},
    "institution_counts_current": {"평생학습강좌": 2},
    "visible_online_application_controls": 0,
    "offline_open_rows": 1,
    "historical_reversed_period_rows": 1,
    "historical_reversed_period_identity": "162",
    "semantic_duplicate_count": 0,
    "conclusion": "single_detail_and_landings_roll_up_to_complete_official_list",
}

GWANGJU_DONGGU_PII_FIELDS_DISCARDED = (
    "강사",
    "담당자",
    "문의전화",
    "강좌소개",
    "주의사항 및 취소 환불 규정",
    "성명",
    "성별",
    "이메일",
    "주소",
    "전화번호",
    "휴대폰",
    "applicant_form_values",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GwangjuDongguContractError(ValueError):
    """Raised when the official course catalogue changes contract."""


@dataclass(frozen=True)
class _ListPage:
    page: int
    total: int
    derived_last: int
    displayed_page: int
    broken_counter_last: int
    rows: tuple[dict[str, Any], ...]


_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_DATE_TOKEN_RE = re.compile(
    r"(?P<year>20\d{2})\s*(?:-|년\s*)\s*"
    r"(?P<month>\d{1,2})\s*(?:-|월\s*)\s*"
    r"(?P<day>\d{1,2})(?:일)?"
)
_PAGE_INFO_RE = re.compile(
    r"^전체\s*(?P<total>[\d,]+)\s*건\s*,\s*현재 페이지\s*"
    r"(?P<page>\d+)\s*/\s*(?P<last>\d+)$"
)
_MOVE_PAGE_RE = re.compile(
    r"^move_page\(\s*['\"](?P<page>\d+)['\"]\s*\)\s*;\s*return\s+false\s*;?$"
)
_DETAIL_STATUS_MAP: Mapping[str, frozenset[str]] = {
    "수시접수": frozenset({"수시접수"}),
    "접수중": frozenset({"접수중"}),
    "접수준비": frozenset({"접수준비"}),
    "접수마감": frozenset({"접수마감", "강좌마감"}),
}
_NORMALIZED_STATUS: Mapping[str, str] = {
    "수시접수": "OPEN",
    "접수중": "OPEN",
    "접수준비": "SCHEDULED",
    "접수마감": "CLOSED",
}
_LIST_HEADERS = ("강좌명", "대상", "교육기간", "장소", "접수방법", "접수상태")
_APPLICATION_METHODS = frozenset(
    {
        "",
        "인터넷",
        "전화접수",
        "방문",
        "인터넷 방문",
        "전화접수 방문",
        "인터넷 전화접수",
        "인터넷 전화접수 방문",
    }
)
_SAFE_DETAIL_FIELDS = frozenset(
    {
        "대상구분",
        "교육구분",
        "접수일자",
        "교육기간",
        "교육시간",
        "정원",
        "수강료",
        "기관구분",
        "교육장소",
        "접수방법",
        "강좌번호",
    }
)
_FORBIDDEN_PAIR_FIELDS = frozenset({"강사", "담당자", "문의전화"})
_REQUIRED_DETAIL_FIELDS = frozenset(
    {"대상구분", "접수일자", "교육기간", "정원", "기관구분", "접수방법", "담당자"}
)
_FREEFORM_SECTIONS = ("강좌소개", "주의사항 및 취소 환불 규정")
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "source_category",
        "source_target",
        "source_period",
        "source_venue",
        "source_application_method",
        "source_application_methods",
        "source_status",
        "source_institution",
        "source_capacity",
        "historical_reversed_period",
        "detail_verified",
        "hidden_application_template_verified",
        "visible_application_control_present",
        "application_control_contract",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "manager",
        "manager_name",
        "contact",
        "phone",
        "email",
        "address",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).lower()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _safe_port(parsed: Any) -> Optional[int]:
    try:
        return parsed.port
    except ValueError:
        return -1


def is_gwangju_donggu_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != GWANGJU_DONGGU_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    if _clean(_target_value(target, "url")) == GWANGJU_DONGGU_EXISTING_DETAIL_URL:
        return True
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GWANGJU_DONGGU_HOST
        and _safe_port(parsed) is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GWANGJU_DONGGU_PATH
        and query
        == {
            "mid": [GWANGJU_DONGGU_MID],
            "act": ["search_list"],
            "nPage": ["1"],
        }
        and not parsed.fragment
    )


is_target = is_gwangju_donggu_education_target


def is_gwangju_donggu_candidate_alias(target: Any) -> bool:
    candidate = _clean(_target_value(target, "candidate_id"))
    url = _clean(_target_value(target, "url"))
    return bool(
        candidate in GWANGJU_DONGGU_CANDIDATE_AUDIT
        or url
        in {
            GWANGJU_DONGGU_EXISTING_DETAIL_URL,
            GWANGJU_DONGGU_LIFELONG_LANDING_URL,
            GWANGJU_DONGGU_GENERIC_LANDING_URL,
            GWANGJU_DONGGU_LIBRARY_DATASET_URL,
            GWANGJU_DONGGU_NOTICE_DATASET_URL,
        }
    )


def gwangju_donggu_list_url(page: int) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    query = urlencode(
        (("mid", GWANGJU_DONGGU_MID), ("act", "search_list"), ("nPage", str(page)))
    )
    return f"https://{GWANGJU_DONGGU_HOST}{GWANGJU_DONGGU_PATH}?{query}"


def gwangju_donggu_detail_url(identity: str, list_page: int) -> str:
    value = _clean(identity)
    if _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    if isinstance(list_page, bool) or not isinstance(list_page, int) or list_page < 1:
        raise ValueError("list_page must be a positive integer")
    query = urlencode(
        (
            ("mid", GWANGJU_DONGGU_MID),
            ("act", "view"),
            ("return_act", "search_list"),
            ("lec_no", value),
            ("nPage", str(list_page)),
        )
    )
    return f"https://{GWANGJU_DONGGU_HOST}{GWANGJU_DONGGU_PATH}?{query}"


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
            "Referer": GWANGJU_DONGGU_LIFELONG_LANDING_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    status = int(getattr(response, "status_code", 0))
    if status != 200:
        raise GwangjuDongguContractError(f"unexpected HTTP status {status}")
    if getattr(response, "headers", {}).get("Location"):
        raise GwangjuDongguContractError("redirect response is not accepted")
    content = getattr(response, "content", b"")
    if not content:
        raise GwangjuDongguContractError("empty HTTP response")
    if len(content) > GWANGJU_DONGGU_MAX_HTML_BYTES:
        raise GwangjuDongguContractError("HTTP response exceeded HTML byte cap")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > GWANGJU_DONGGU_MAX_HTML_BYTES:
            raise GwangjuDongguContractError("HTML fixture exceeded byte cap")
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > GWANGJU_DONGGU_MAX_HTML_BYTES:
            raise GwangjuDongguContractError("HTML fixture exceeded byte cap")
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher returned neither HTML nor response")
    if len(content) > GWANGJU_DONGGU_MAX_HTML_BYTES:
        raise GwangjuDongguContractError("HTTP response exceeded HTML byte cap")
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
    for attempt in range(GWANGJU_DONGGU_FETCH_ATTEMPTS):
        session: Any = None
        try:
            session = session_factory()
            return _coerce_soup(fetcher(session, url, timeout))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < GWANGJU_DONGGU_FETCH_ATTEMPTS:
                time.sleep(GWANGJU_DONGGU_RETRY_BACKOFF_SECONDS * (attempt + 1))
        finally:
            _close_quietly(session)
    assert last_error is not None
    raise last_error


def _document_title(soup: BeautifulSoup, label: str) -> str:
    titles = soup.select("head > title")
    if len(titles) != 1:
        raise GwangjuDongguContractError(f"{label}: document title changed")
    title = _clean(titles[0].get_text(" ", strip=True))
    if title not in GWANGJU_DONGGU_PAGE_TITLES:
        raise GwangjuDongguContractError(f"{label}: official page title changed")
    return title


def _select_options(form: Any, name: str) -> tuple[tuple[str, str], ...]:
    selects = form.select(f'select[name="{name}"]')
    if len(selects) != 1:
        raise GwangjuDongguContractError(f"search taxonomy {name} changed")
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in selects[0].select(":scope > option")
    )


def _validate_search_form(soup: BeautifulSoup, page: int) -> None:
    forms = soup.select("form#srhForm")
    if len(forms) != 1:
        raise GwangjuDongguContractError(f"page {page}: search form changed")
    form = forms[0]
    action = urlparse(urljoin(GWANGJU_DONGGU_CANONICAL_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "post"
        or action.scheme != "https"
        or action.hostname != GWANGJU_DONGGU_HOST
        or action.path != GWANGJU_DONGGU_PATH
        or parse_qs(action.query, keep_blank_values=True)
        != {"mid": [GWANGJU_DONGGU_MID], "act": ["search_list"]}
        or action.fragment
    ):
        raise GwangjuDongguContractError(f"page {page}: search form ownership changed")
    expected_hidden = {
        "mid": GWANGJU_DONGGU_MID,
        "act": "search_list",
        "nPage": str(page),
    }
    for name, expected in expected_hidden.items():
        nodes = form.select(f'input[type="hidden"][name="{name}"]')
        if len(nodes) != 1 or _clean(nodes[0].get("value")) != expected:
            raise GwangjuDongguContractError(f"page {page}: search field {name} changed")
    if _select_options(form, "organ_cd") != GWANGJU_DONGGU_INSTITUTIONS:
        raise GwangjuDongguContractError("institution taxonomy changed")
    if _select_options(form, "target_cd") != GWANGJU_DONGGU_TARGETS:
        raise GwangjuDongguContractError("target taxonomy changed")
    if _select_options(form, "type_cd") != GWANGJU_DONGGU_CATEGORIES:
        raise GwangjuDongguContractError("course-category taxonomy changed")
    if _select_options(form, "status_cd") != GWANGJU_DONGGU_STATUSES:
        raise GwangjuDongguContractError("reception-status taxonomy changed")


def _parse_detail_identity(value: Any, page: int) -> str:
    parsed = urlparse(urljoin(GWANGJU_DONGGU_CANONICAL_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identities = query.get("lec_no", [])
    if (
        parsed.scheme != "https"
        or parsed.hostname != GWANGJU_DONGGU_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != GWANGJU_DONGGU_PATH
        or set(query) != {"mid", "act", "return_act", "lec_no", "nPage"}
        or query.get("mid") != [GWANGJU_DONGGU_MID]
        or query.get("act") != ["view"]
        or query.get("return_act") != ["search_list"]
        or query.get("nPage") != [str(page)]
        or len(identities) != 1
        or _IDENTITY_RE.fullmatch(identities[0]) is None
        or parsed.fragment
    ):
        raise GwangjuDongguContractError(f"page {page}: detail link changed")
    return identities[0]


def _date_pair(
    value: Any,
    identity: str,
    cutoff: date,
) -> tuple[date, date, bool]:
    text = _clean(value)
    matches = list(_DATE_TOKEN_RE.finditer(text))
    if len(matches) != 2:
        raise GwangjuDongguContractError(f"course {identity}: education period changed")
    dates = [
        date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
        for match in matches
    ]
    reversed_period = dates[0] > dates[1]
    if reversed_period:
        if identity != "162" or max(dates) >= cutoff:
            raise GwangjuDongguContractError(
                f"course {identity}: unexpected reversed education period"
            )
        return min(dates), max(dates), True
    return dates[0], dates[1], False


def _method_tokens(value: Any, identity: str) -> tuple[str, ...]:
    method = _clean(value)
    if method not in _APPLICATION_METHODS:
        raise GwangjuDongguContractError(f"course {identity}: application method changed")
    return tuple(token for token in ("인터넷", "전화접수", "방문") if token in method)


def _parse_list_row(row: Any, page: int, cutoff: date) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 6:
        raise GwangjuDongguContractError(f"page {page}: course row width changed")
    anchors = cells[0].select(":scope > a[href]")
    if len(anchors) != 1:
        raise GwangjuDongguContractError(f"page {page}: course anchor changed")
    anchor = anchors[0]
    identity = _parse_detail_identity(anchor.get("href"), page)
    category_nodes = anchor.select(":scope > span.lecture-cate")
    if len(category_nodes) != 1:
        raise GwangjuDongguContractError(f"course {identity}: category marker changed")
    category = _clean(category_nodes[0].get_text(" ", strip=True))
    allowed_categories = {label for code, label in GWANGJU_DONGGU_CATEGORIES if code}
    if category not in allowed_categories:
        raise GwangjuDongguContractError(f"course {identity}: unknown category")
    clone = BeautifulSoup(str(anchor), "lxml").select_one("a")
    if clone is None:
        raise GwangjuDongguContractError(f"course {identity}: title clone failed")
    for node in clone.select(":scope > span.lecture-cate"):
        node.decompose()
    title = _clean(clone.get_text(" ", strip=True))
    if not title:
        raise GwangjuDongguContractError(f"course {identity}: title is empty")

    target = _clean(cells[1].get_text(" ", strip=True))
    allowed_targets = {label for code, label in GWANGJU_DONGGU_TARGETS if code}
    if target not in allowed_targets:
        raise GwangjuDongguContractError(f"course {identity}: target category changed")
    source_period = _clean(cells[2].get_text(" ", strip=True))
    start, end, reversed_period = _date_pair(source_period, identity, cutoff)
    venue = _clean(cells[3].get_text(" ", strip=True))
    if not venue:
        raise GwangjuDongguContractError(f"course {identity}: venue is empty")
    method = _clean(cells[4].get_text(" ", strip=True))
    methods = _method_tokens(method, identity)
    source_status = _clean(cells[5].get_text(" ", strip=True))
    if source_status not in _NORMALIZED_STATUS:
        raise GwangjuDongguContractError(f"course {identity}: unknown reception status")
    status = _NORMALIZED_STATUS[source_status]
    if status == "OPEN" and end >= cutoff and not methods:
        raise GwangjuDongguContractError(
            f"course {identity}: current open row has no application method"
        )
    return {
        "identity": identity,
        "title": title,
        "category": category,
        "target": target,
        "source_period": source_period,
        "start": start,
        "end": end,
        "historical_reversed_period": reversed_period,
        "venue": venue,
        "application_method": method,
        "application_methods": methods,
        "source_status": source_status,
        "status": status,
        "list_page": page,
        "detail_url": gwangju_donggu_detail_url(identity, page),
    }


def _parse_list_page(soup: BeautifulSoup, page: int, cutoff: date) -> _ListPage:
    _document_title(soup, f"page {page}")
    _validate_search_form(soup, page)
    infos = soup.select("p.page_info")
    if len(infos) != 1:
        raise GwangjuDongguContractError(f"page {page}: page summary changed")
    match = _PAGE_INFO_RE.fullmatch(_clean(infos[0].get_text(" ", strip=True)))
    if match is None:
        raise GwangjuDongguContractError(f"page {page}: page summary text changed")
    total = int(match.group("total").replace(",", ""))
    displayed_page = int(match.group("page"))
    broken_counter_last = int(match.group("last"))
    derived_last = max(1, math.ceil(total / GWANGJU_DONGGU_PAGE_SIZE))
    expected_broken_last = max(
        1, math.ceil(total / GWANGJU_DONGGU_BROKEN_COUNTER_PAGE_SIZE)
    )
    if (
        displayed_page != page
        or broken_counter_last != expected_broken_last
        or derived_last < broken_counter_last
    ):
        raise GwangjuDongguContractError(f"page {page}: catalogue boundary changed")

    tables = soup.select("table.tstyle_list")
    if len(tables) != 1:
        raise GwangjuDongguContractError(f"page {page}: list table changed")
    table = tables[0]
    captions = table.find_all("caption", recursive=False)
    if len(captions) != 1 or _clean(captions[0].get_text(" ", strip=True)) != (
        "게시판 > 프로그램 목록"
    ):
        raise GwangjuDongguContractError(f"page {page}: list caption changed")
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _LIST_HEADERS:
        raise GwangjuDongguContractError(f"page {page}: list headers changed")
    tbodies = table.find_all("tbody", recursive=False)
    if len(tbodies) != 1:
        raise GwangjuDongguContractError(f"page {page}: list body changed")
    body_rows = tbodies[0].find_all("tr", recursive=False)
    course_rows = [row for row in body_rows if row.select_one('a[href*="lec_no="]')]
    if course_rows and len(course_rows) != len(body_rows):
        raise GwangjuDongguContractError(f"page {page}: mixed list result rows")
    if not course_rows:
        if (
            len(body_rows) != 1
            or len(body_rows[0].find_all("td", recursive=False)) != 1
            or _clean(body_rows[0].td.get("colspan")) != "6"
            or _clean(body_rows[0].td.get_text(" ", strip=True))
            != "등록된 자료가 없습니다."
        ):
            raise GwangjuDongguContractError(f"page {page}: empty result marker changed")
    rows = tuple(_parse_list_row(row, page, cutoff) for row in course_rows)
    return _ListPage(
        page,
        total,
        derived_last,
        displayed_page,
        broken_counter_last,
        rows,
    )


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("source_period")),
            _clean(row.get("venue")),
            _clean(row.get("application_method")),
            _clean(row.get("source_status")),
        )
        for row in rows
    )


def _detail_structure(table: Any, identity: str) -> tuple[dict[str, Any], tuple[str, ...]]:
    tbodies = table.find_all("tbody", recursive=False)
    if len(tbodies) != 1:
        raise GwangjuDongguContractError(f"course {identity}: detail table body changed")
    pairs: dict[str, Any] = {}
    sections: list[str] = []
    awaiting_content = False
    for row in tbodies[0].find_all("tr", recursive=False):
        headers = row.find_all("th", recursive=False)
        values = row.find_all("td", recursive=False)
        children = [
            child
            for child in row.children
            if getattr(child, "name", None) in {"th", "td"}
        ]
        if len(headers) in {1, 2} and len(values) == len(headers):
            if awaiting_content:
                raise GwangjuDongguContractError(
                    f"course {identity}: free-form section content missing"
                )
            expected_children = [
                node
                for pair in zip(headers, values)
                for node in pair
            ]
            if children != expected_children:
                raise GwangjuDongguContractError(
                    f"course {identity}: paired detail layout changed"
                )
            for header, value in zip(headers, values):
                label = _clean(header.get_text(" ", strip=True))
                if (
                    not label
                    or label in pairs
                    or label not in _SAFE_DETAIL_FIELDS | _FORBIDDEN_PAIR_FIELDS
                ):
                    raise GwangjuDongguContractError(
                        f"course {identity}: detail field set changed"
                    )
                # Forbidden fields are retained only as nodes so the complete
                # schema can be verified.  Their values are never read.
                pairs[label] = value
            continue
        if len(headers) == 1 and not values:
            if awaiting_content:
                raise GwangjuDongguContractError(
                    f"course {identity}: nested free-form section changed"
                )
            label = _clean(headers[0].get_text(" ", strip=True))
            if (
                children != [headers[0]]
                or _clean(headers[0].get("colspan")) != "4"
                or label not in _FREEFORM_SECTIONS
                or label in sections
            ):
                raise GwangjuDongguContractError(
                    f"course {identity}: free-form section label changed"
                )
            sections.append(label)
            awaiting_content = True
            continue
        if (
            not headers
            and len(values) == 1
            and children == [values[0]]
            and _clean(values[0].get("colspan")) == "4"
            and awaiting_content
        ):
            # Deliberately do not read this free-form cell.
            awaiting_content = False
            continue
        raise GwangjuDongguContractError(
            f"course {identity}: structured/free-form detail layout changed"
        )
    if awaiting_content or not _REQUIRED_DETAIL_FIELDS.issubset(pairs):
        raise GwangjuDongguContractError(f"course {identity}: required detail fields missing")
    if tuple(sections) != _FREEFORM_SECTIONS:
        raise GwangjuDongguContractError(f"course {identity}: free-form sections changed")
    return pairs, tuple(sections)


def _field_text(pairs: Mapping[str, Any], label: str, identity: str) -> str:
    if label not in _SAFE_DETAIL_FIELDS or label not in pairs:
        raise GwangjuDongguContractError(f"course {identity}: unsafe/missing field access")
    return _clean(pairs[label].get_text(" ", strip=True))


def _detail_title_status(table: Any, listed: Mapping[str, Any]) -> tuple[str, str]:
    identity = _clean(listed.get("identity"))
    titles = table.select("thead p.title")
    if len(titles) != 1:
        raise GwangjuDongguContractError(f"course {identity}: detail title changed")
    markers = titles[0].select(":scope > span.state02")
    if len(markers) != 1:
        raise GwangjuDongguContractError(f"course {identity}: detail status marker changed")
    detail_status = _clean(markers[0].get_text(" ", strip=True))
    source_status = _clean(listed.get("source_status"))
    if detail_status not in _DETAIL_STATUS_MAP[source_status]:
        raise GwangjuDongguContractError(f"course {identity}: list/detail status mismatch")
    clone = BeautifulSoup(str(titles[0]), "lxml").select_one("p.title")
    if clone is None:
        raise GwangjuDongguContractError(f"course {identity}: detail title clone failed")
    for marker in clone.select(":scope > span.state02"):
        marker.decompose()
    detail_title = re.sub(
        r"^\[\s*\]\s*", "", _clean(clone.get_text(" ", strip=True))
    ).strip()
    if detail_title != _clean(listed.get("title")):
        raise GwangjuDongguContractError(f"course {identity}: list/detail title mismatch")
    return detail_title, detail_status


def _hidden_value(form: Any, name: str, identity: str) -> str:
    nodes = form.select(f'input[type="hidden"][name="{name}"]')
    if len(nodes) != 1:
        raise GwangjuDongguContractError(
            f"course {identity}: safe application field {name} changed"
        )
    return _clean(nodes[0].get("value"))


def _application_contract(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
) -> tuple[bool, str]:
    identity = _clean(listed.get("identity"))
    containers = soup.select("div.application")
    if len(containers) != 1:
        raise GwangjuDongguContractError(f"course {identity}: application template changed")
    container = containers[0]
    hidden = bool(re.search(r"display\s*:\s*none", _clean(container.get("style")), re.I))
    forms = container.select(":scope > form#insForm")
    if len(forms) != 1:
        raise GwangjuDongguContractError(f"course {identity}: application form changed")
    form = forms[0]
    action = urlparse(urljoin(GWANGJU_DONGGU_CANONICAL_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "post"
        or action.scheme != "https"
        or action.hostname != GWANGJU_DONGGU_HOST
        or action.path != GWANGJU_DONGGU_PATH
        or action.query
        or action.fragment
        or _hidden_value(form, "mid", identity) != GWANGJU_DONGGU_MID
        or _hidden_value(form, "act", identity) != "mem_ins"
        or _hidden_value(form, "actionUrl", identity) != GWANGJU_DONGGU_PATH
        or _hidden_value(form, "lec_no", identity) != identity
        or _hidden_value(form, "nPage", identity) != str(listed.get("list_page"))
    ):
        raise GwangjuDongguContractError(f"course {identity}: application ownership changed")
    submitters = form.select('button[type="button"][onclick]')
    submitters = [
        node
        for node in submitters
        if _clean(node.get("onclick")).replace(" ", "")
        == "ins_mem_check();returnfalse;"
    ]
    if len(submitters) != 1 or _clean(
        submitters[0].get_text(" ", strip=True)
    ) != "신청하기":
        raise GwangjuDongguContractError(
            f"course {identity}: embedded submit control changed"
        )
    online = "인터넷" in tuple(listed.get("application_methods") or ())
    active_online = _clean(listed.get("status")) == "OPEN" and online
    if active_online and hidden:
        raise GwangjuDongguContractError(
            f"course {identity}: internet-open course has only a hidden application template"
        )
    if not active_online and not hidden:
        raise GwangjuDongguContractError(
            f"course {identity}: inactive/offline course exposes the applicant form"
        )
    contract = (
        "visible_course_bound_post_form"
        if active_online
        else "hidden_non_public_applicant_template"
    )
    return active_online, contract


def _branch_name(institution: str) -> str:
    return f"{GWANGJU_DONGGU_MUNICIPALITY_NAME} / {institution}"


def _branch_code(institution: str) -> str:
    digest = hashlib.sha1(institution.encode("utf-8")).hexdigest()[:12]
    return f"gwangju-donggu:{digest}"


def _fee_amount(value: str, identity: str) -> Optional[int]:
    if not value:
        return None
    compact = _clean(value).replace(",", "")
    if compact in {"0", "0원", "무료", "없음"} or (
        "무료" in compact and not re.search(r"\d", compact)
    ):
        return 0
    numbers = re.findall(r"\d+", compact)
    if len(numbers) != 1:
        raise GwangjuDongguContractError(f"course {identity}: tuition changed")
    return int(numbers[0])


def _parse_detail(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
    cutoff: date,
) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    _document_title(soup, f"course {identity} detail")
    tables = soup.select("table.tstyle_view")
    if len(tables) != 1:
        raise GwangjuDongguContractError(f"course {identity}: detail table changed")
    table = tables[0]
    title, detail_status = _detail_title_status(table, listed)
    pairs, _sections = _detail_structure(table, identity)
    target = _field_text(pairs, "대상구분", identity)
    detail_period = _field_text(pairs, "교육기간", identity)
    detail_start, detail_end, detail_reversed = _date_pair(
        detail_period, identity, cutoff
    )
    capacity_text = _field_text(pairs, "정원", identity)
    capacity_match = re.fullmatch(r"(?P<count>[\d,]+)\s*명", capacity_text)
    if capacity_match is None:
        raise GwangjuDongguContractError(f"course {identity}: capacity changed")
    capacity_total = int(capacity_match.group("count").replace(",", ""))
    institution = _field_text(pairs, "기관구분", identity)
    known_institutions = {
        label for code, label in GWANGJU_DONGGU_INSTITUTIONS if code
    }
    if institution not in known_institutions:
        raise GwangjuDongguContractError(f"course {identity}: unknown institution branch")
    detail_method = _field_text(pairs, "접수방법", identity)
    detail_methods = _method_tokens(detail_method, identity)
    if (
        target != _clean(listed.get("target"))
        or detail_start != listed.get("start")
        or detail_end != listed.get("end")
        or detail_reversed != bool(listed.get("historical_reversed_period"))
        or detail_methods != tuple(listed.get("application_methods") or ())
    ):
        raise GwangjuDongguContractError(f"course {identity}: list/detail fields mismatch")
    if "교육장소" in pairs:
        detail_venue = _field_text(pairs, "교육장소", identity)
        if _normalized(detail_venue) != _normalized(listed.get("venue")):
            raise GwangjuDongguContractError(f"course {identity}: list/detail venue mismatch")
    schedule = (
        _field_text(pairs, "교육시간", identity) if "교육시간" in pairs else ""
    )
    fee = _field_text(pairs, "수강료", identity) if "수강료" in pairs else ""
    fee_amount = _fee_amount(fee, identity)
    visible_online, application_contract = _application_contract(soup, listed)

    normalized_methods = [
        {"인터넷": "온라인", "전화접수": "전화", "방문": "방문"}[method]
        for method in detail_methods
    ]
    status = _clean(listed.get("status"))
    offline_open = status == "OPEN" and not visible_online
    application_type = (
        "ONLINE_RESERVATION"
        if visible_online
        else "OFFLINE_APPLY"
        if offline_open
        else "INFO_ONLY"
    )
    branch = _branch_name(institution)
    row: dict[str, Any] = {
        "provider": GWANGJU_DONGGU_PROVIDER,
        "provider_course_id": f"{GWANGJU_DONGGU_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(institution),
        "preserve_branch": True,
        "category": _clean(listed.get("category")),
        "program_type": "교육",
        "raw_url": gwangju_donggu_detail_url(
            identity, int(listed.get("list_page") or 0)
        ),
        "application_url": (
            gwangju_donggu_detail_url(identity, int(listed.get("list_page") or 0))
            if visible_online
            else ""
        ),
        "application_type": application_type,
        "application_method": " / ".join(normalized_methods),
        "application_methods": normalized_methods,
        "reservation_available": visible_online,
        "status": status,
        "fee": fee,
        "fee_amount": fee_amount,
        "period": f"{detail_start.isoformat()} ~ {detail_end.isoformat()}",
        "start_date": detail_start.isoformat(),
        "end_date": detail_end.isoformat(),
        "apply_period": _field_text(pairs, "접수일자", identity),
        "schedule_raw": schedule,
        "capacity": f"{capacity_total}명",
        "capacity_current": None,
        "capacity_total": capacity_total,
        "target": target,
        "venue": _clean(listed.get("venue")),
        "venue_name": _clean(listed.get("venue")),
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GWANGJU_DONGGU_PARSER,
        "municipality_code": GWANGJU_DONGGU_MUNICIPALITY_CODE,
        "municipality_full_name": GWANGJU_DONGGU_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": int(listed.get("list_page") or 0),
            "source_category": _clean(listed.get("category")),
            "source_target": target,
            "source_period": detail_period,
            "source_venue": _clean(listed.get("venue")),
            "source_application_method": detail_method,
            "source_application_methods": list(detail_methods),
            "source_status": _clean(listed.get("source_status")),
            "source_institution": institution,
            "source_capacity": capacity_text,
            "historical_reversed_period": bool(
                listed.get("historical_reversed_period")
            ),
            "detail_verified": True,
            "hidden_application_template_verified": not visible_online,
            "visible_application_control_present": visible_online,
            "application_control_contract": application_contract,
            "service_family": "education",
        },
        "_source_end_date": detail_end,
    }
    return row


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded PII-safe allowlist")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "_source_end_date"}
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
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "identity_duplicate_count": 0,
        "raw_url_duplicate_count": 0,
        "historical_reversed_period_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": error,
        "municipality_code": GWANGJU_DONGGU_MUNICIPALITY_CODE,
        "municipality_name": GWANGJU_DONGGU_MUNICIPALITY_NAME,
        "canonical_candidate_id": GWANGJU_DONGGU_CANONICAL_CANDIDATE_ID,
        "canonical_url": GWANGJU_DONGGU_CANONICAL_URL,
        "ownership_scope": GWANGJU_DONGGU_OWNERSHIP_SCOPE,
    }


def collect_gwangju_donggu_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 30,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GWANGJU_DONGGU_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future official Dong-gu course snapshot."""

    meta = _base_meta()
    if not is_gwangju_donggu_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Gwangju Dong-gu course owner"
        )
        return [], GWANGJU_DONGGU_PARSER, meta
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
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], GWANGJU_DONGGU_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], GWANGJU_DONGGU_PARSER, meta

    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    workers = min(max_workers, GWANGJU_DONGGU_MAX_WORKERS)
    errors: list[str] = []

    def fetch_list(page: int) -> _ListPage:
        soup = _fetch_soup(
            gwangju_donggu_list_url(page),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return _parse_list_page(soup, page, cutoff)

    try:
        first = fetch_list(1)
        meta["list_requests"] = 1
        meta["pages"] = 1
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"page 1: {type(exc).__name__}: {_clean(exc)}"
        )
        return [], GWANGJU_DONGGU_PARSER, meta

    last = first.derived_last
    required_list_requests = last + 3
    meta.update(
        {
            "declared_source_rows": first.total,
            "derived_data_pages": last,
            "source_counter_last_page": first.broken_counter_last,
            "source_counter_defect_verified": (
                last != first.broken_counter_last
            ),
            "required_list_requests": required_list_requests,
        }
    )
    if required_list_requests > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of "
                    f"{required_list_requests} required list requests"
                ),
            }
        )
        return [], GWANGJU_DONGGU_PARSER, meta

    jobs: list[tuple[str, int]] = [
        ("data", page) for page in range(2, last + 1)
    ]
    jobs.extend(
        (("sentinel", last + 1), ("first_recheck", 1), ("last_recheck", last))
    )
    parsed_jobs: dict[tuple[str, int], _ListPage] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_list, page): (kind, page) for kind, page in jobs}
        for future in as_completed(futures):
            kind, page = futures[future]
            try:
                parsed_jobs[(kind, page)] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(
                    f"{kind} page {page}: {type(exc).__name__}: {_clean(exc)}"
                )

    page_rows: dict[int, tuple[dict[str, Any], ...]] = {1: first.rows}
    for page in range(1, last + 1):
        parsed = first if page == 1 else parsed_jobs.get(("data", page))
        if parsed is None:
            errors.append(f"data page {page}: response missing")
            continue
        if (
            parsed.total != first.total
            or parsed.derived_last != last
            or parsed.broken_counter_last != first.broken_counter_last
        ):
            errors.append(f"data page {page}: total/page boundary changed")
        expected = min(
            GWANGJU_DONGGU_PAGE_SIZE,
            max(0, first.total - (page - 1) * GWANGJU_DONGGU_PAGE_SIZE),
        )
        if len(parsed.rows) != expected:
            errors.append(f"data page {page}: row count {len(parsed.rows)} != {expected}")
        page_rows[page] = parsed.rows

    sentinel = parsed_jobs.get(("sentinel", last + 1))
    if sentinel is None:
        errors.append("immediate post-last sentinel response missing")
    elif (
        sentinel.total != first.total
        or sentinel.derived_last != last
        or sentinel.broken_counter_last != first.broken_counter_last
        or sentinel.rows
    ):
        errors.append("immediate post-last sentinel is not stable empty")
    else:
        meta["sentinel_requests"] = 1

    first_recheck = parsed_jobs.get(("first_recheck", 1))
    last_recheck = parsed_jobs.get(("last_recheck", last))
    if first_recheck is None or last_recheck is None:
        errors.append("first/last stability recheck response missing")
    else:
        meta["stability_rechecks"] = 2
        if (
            first_recheck.total != first.total
            or first_recheck.derived_last != last
            or _page_signature(first_recheck.rows) != _page_signature(first.rows)
        ):
            errors.append("first-page stability recheck changed")
        if (
            last_recheck.total != first.total
            or last_recheck.derived_last != last
            or _page_signature(last_recheck.rows)
            != _page_signature(page_rows.get(last, ()))
        ):
            errors.append("last-page stability recheck changed")

    listed = [
        row for page in range(1, last + 1) for row in page_rows.get(page, ())
    ]
    identities = [_clean(row.get("identity")) for row in listed]
    identity_duplicate_count = len(identities) - len(set(identities))
    if identity_duplicate_count:
        errors.append(f"{identity_duplicate_count} duplicate official identities")
    detail_urls = [_clean(row.get("detail_url")) for row in listed]
    raw_url_duplicate_count = len(detail_urls) - len(set(detail_urls))
    if raw_url_duplicate_count:
        errors.append(f"{raw_url_duplicate_count} duplicate official detail URLs")
    if len(listed) != first.total:
        errors.append(
            f"complete row count {len(listed)} != declared total {first.total}"
        )
    list_complete = bool(
        not errors
        and meta["list_requests"] == required_list_requests
        and meta["sentinel_requests"] == 1
        and meta["stability_rechecks"] == 2
        and len(listed) == first.total
    )
    current_listed = [row for row in listed if row["end"] >= cutoff]
    meta.update(
        {
            "data_pages": len(page_rows),
            "source_rows": len(listed),
            "current_source_count": len(current_listed),
            "expired_count": len(listed) - len(current_listed),
            "identity_duplicate_count": identity_duplicate_count,
            "raw_url_duplicate_count": raw_url_duplicate_count,
            "historical_reversed_period_count": sum(
                bool(row.get("historical_reversed_period")) for row in listed
            ),
            "historical_source_status_counts": dict(
                Counter(_clean(row.get("source_status")) for row in listed)
            ),
            "category_counts_all": dict(
                Counter(_clean(row.get("category")) for row in listed)
            ),
            "target_counts_all": dict(
                Counter(_clean(row.get("target")) for row in listed)
            ),
            "pagination_complete": list_complete,
        }
    )
    if not list_complete:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], GWANGJU_DONGGU_PARSER, meta

    if len(current_listed) > detail_limit:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit cap allows {detail_limit} of "
                    f"{len(current_listed)} required current details"
                ),
            }
        )
        return [], GWANGJU_DONGGU_PARSER, meta

    meta["detail_attempts"] = len(current_listed)
    detailed: dict[str, dict[str, Any]] = {}
    detail_errors: list[str] = []

    def fetch_detail(listed_row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        identity = _clean(listed_row.get("identity"))
        soup = _fetch_soup(
            _clean(listed_row.get("detail_url")),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return identity, _parse_detail(soup, listed_row, cutoff)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_detail, row): row for row in current_listed}
        for future in as_completed(futures):
            listed_row = futures[future]
            identity = _clean(listed_row.get("identity"))
            try:
                parsed_identity, parsed = future.result()
                if parsed_identity in detailed:
                    raise GwangjuDongguContractError("duplicate parsed detail identity")
                detailed[parsed_identity] = parsed
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
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
            bool(
                row.get("raw_fields", {}).get(
                    "hidden_application_template_verified"
                )
                or row.get("raw_fields", {}).get(
                    "visible_application_control_present"
                )
            )
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
        (
            _normalized(row.get("title")),
            _clean(row.get("period")),
            _normalized(row.get("venue")),
        )
        for row in ordered
    )
    meta.update(
        {
            "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
            "institution_counts": dict(
                Counter(
                    _clean(row.get("raw_fields", {}).get("source_institution"))
                    for row in result
                )
            ),
            "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
            "visible_online_application_control_count": sum(
                bool(
                    row.get("raw_fields", {}).get(
                        "visible_application_control_present"
                    )
                )
                for row in ordered
            ),
            "offline_open_count": sum(
                row.get("status") == "OPEN"
                and row.get("application_type") == "OFFLINE_APPLY"
                for row in ordered
            ),
            "semantic_duplicate_group_count": sum(
                count > 1 for count in semantic_counter.values()
            ),
            "semantic_duplicate_excess_rows": sum(
                max(0, count - 1) for count in semantic_counter.values()
            ),
            "semantic_duplicate_policy": "preserve_distinct_official_lec_no",
            "details_complete": details_complete,
            "application_controls_complete": application_controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not current_listed),
            "no_current_reason": (
                "the complete official catalogue has no current/future courses"
                if snapshot_complete and not current_listed
                else ""
            ),
            "candidate_audit": {
                key: dict(value)
                for key, value in GWANGJU_DONGGU_CANDIDATE_AUDIT.items()
            },
            "discovery_audit": dict(GWANGJU_DONGGU_DISCOVERY_AUDIT),
            "superseded_detail_urls": [GWANGJU_DONGGU_EXISTING_DETAIL_URL],
            "excluded_providers": [GWANGJU_DONGGU_DEPRECATED_LANDING_PROVIDER],
            "municipality_coverage": [GWANGJU_DONGGU_MUNICIPALITY_CODE],
            "pii_fields_discarded": list(GWANGJU_DONGGU_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "network_concurrency": workers,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, GWANGJU_DONGGU_PARSER, meta


collect = collect_gwangju_donggu_education


__all__ = [
    "GWANGJU_DONGGU_CANONICAL_CANDIDATE_ID",
    "GWANGJU_DONGGU_CANONICAL_URL",
    "GWANGJU_DONGGU_CANDIDATE_AUDIT",
    "GWANGJU_DONGGU_DEPRECATED_LANDING_PROVIDER",
    "GWANGJU_DONGGU_DISCOVERY_AUDIT",
    "GWANGJU_DONGGU_EXISTING_DETAIL_URL",
    "GWANGJU_DONGGU_GENERIC_LANDING_URL",
    "GWANGJU_DONGGU_LANDING_CANDIDATE_ID",
    "GWANGJU_DONGGU_LIFELONG_LANDING_URL",
    "GWANGJU_DONGGU_MUNICIPALITY_CODE",
    "GWANGJU_DONGGU_MUNICIPALITY_NAME",
    "GWANGJU_DONGGU_PARSER",
    "GWANGJU_DONGGU_PROVIDER",
    "GwangjuDongguContractError",
    "collect",
    "collect_gwangju_donggu_education",
    "gwangju_donggu_detail_url",
    "gwangju_donggu_list_url",
    "is_gwangju_donggu_candidate_alias",
    "is_gwangju_donggu_education_target",
    "is_target",
]
