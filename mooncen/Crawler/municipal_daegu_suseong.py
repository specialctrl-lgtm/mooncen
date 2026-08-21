"""Atomic collector for Daegu Suseong-gu's official education ledgers.

Suseong-gu exposes three public, structured reservation surfaces rather than
one combined API.  The lifelong-learning platform has separate ledgers for
the six district learning centres and for Suseong Lifelong Learning Hall; the
district reservation service has a smaller lifelong/information-education
ledger.  This collector owns that fixed fan-out and returns a snapshot only
after every source has passed its pagination, empty-sentinel, stable-boundary,
current-period, detail-identity, application-control, and privacy contracts.

The platform's private-institution directory, libraries, sports/facility and
tourism reservations, cultural-foundation programmes, youth facilities, and
Daegu city's separate aggregate are deliberately outside this provider.
Applicant/result pages, registration submissions, instructor/contact fields,
attachments, maps, and free-form descriptions are never requested or stored.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from lxml import html as lxml_html
from lxml.html import HtmlElement

from utils.outbound_http import SafeSession


DAEGU_SUSEONG_PROVIDER = "MUNI_LLL_SUSEONG_KR_2C82AF9F"
DAEGU_SUSEONG_CANDIDATE_ID = "MUNI_IR_0918A27B489E"
DAEGU_SUSEONG_MUNICIPALITY_CODE = "2726000000"
DAEGU_SUSEONG_MUNICIPALITY_NAME = "대구광역시 수성구"

DAEGU_SUSEONG_HOST = "lll.suseong.kr"
DAEGU_SUSEONG_DISTRICT_HOST = "www.suseong.kr"
DAEGU_SUSEONG_URL = "https://lll.suseong.kr/index.do?menu_id=00001969&menu_link=/reservation/learning/searchLearning.do"
DAEGU_SUSEONG_HALL_URL = (
    "https://lll.suseong.kr/index.do?menu_id=00002307&menu_link=/reservation/learningHall/searchLearningHall.do"
)
DAEGU_SUSEONG_RESERVATION_URL = (
    "https://www.suseong.kr/yeyak/index.do?menu_id=00031480&menu_link=front/yeyak/yeyakList.do"
)

DAEGU_SUSEONG_PARSER = (
    "daegu_suseong_three_official_education_ledgers+bounded_complete_list+"
    "declared_final_pages+empty_sentinels+stable_first_final_boundaries+"
    "current_details+identity_bound_application_controls+"
    "cancel_and_practice_suppression+pii_allowlist+atomic_snapshot"
)
DAEGU_SUSEONG_OWNERSHIP_SCOPE = "suseong_learning_centres_learning_hall_and_district_education_reservations"

DAEGU_SUSEONG_PAGE_SIZE = 10
DAEGU_SUSEONG_FETCH_ATTEMPTS = 4
DAEGU_SUSEONG_MAX_WORKERS = 8
DAEGU_SUSEONG_MAX_LIST_BYTES = 40_000_000
DAEGU_SUSEONG_MAX_DETAIL_BYTES = 2_000_000
DAEGU_SUSEONG_MANAGED_MAX_RESPONSE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class EducationLedger:
    key: str
    search_url: str
    list_menu_link: str
    detail_menu_link: str
    function_name: str
    form_id: str
    title_marker: str
    branch_code: str


DAEGU_SUSEONG_LEDGERS = (
    EducationLedger(
        key="learning_centres",
        search_url=DAEGU_SUSEONG_URL,
        list_menu_link="/reservation/learning/list.do",
        detail_menu_link="/reservation/learning/details.do",
        function_name="fn_learning_details",
        form_id="icmsLearning",
        title_marker="강좌 및 수강신청",
        branch_code="DAEGU_SUSEONG_LEARNING_CENTRES",
    ),
    EducationLedger(
        key="learning_hall",
        search_url=DAEGU_SUSEONG_HALL_URL,
        list_menu_link="/reservation/learningHall/list.do",
        detail_menu_link="/reservation/learningHall/details.do",
        function_name="fn_learningHall_details",
        form_id="icmsLearningHall",
        title_marker="프로그램 신청",
        branch_code="DAEGU_SUSEONG_LEARNING_HALL",
    ),
)
_LEDGER_BY_KEY = {ledger.key: ledger for ledger in DAEGU_SUSEONG_LEDGERS}

DAEGU_SUSEONG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    DAEGU_SUSEONG_CANDIDATE_ID: {
        "decision": "canonical_complete_owner_with_fixed_official_fanout",
        "url": DAEGU_SUSEONG_URL,
        "fanout_urls": (
            DAEGU_SUSEONG_HALL_URL,
            DAEGU_SUSEONG_RESERVATION_URL,
        ),
    },
    "MUNI_IR_7188BB211A7B": {
        "decision": "retarget_registered_home_to_canonical_course_ledger",
        "url": "https://lll.suseong.kr/",
    },
    "MUNI_IR_1964ED931061": {
        "decision": "duplicate_menu_wrapper_of_canonical_course_ledger",
        "url": "https://lll.suseong.kr/index.do?menu_id=00001969",
        "existing_owner": "MUNI_LLL_SUSEONG_KR_F59F7BFE",
    },
    "MUNI_IR_5935B34EDA23": {
        "decision": "include_fixed_learning_hall_child_ledger",
        "url": DAEGU_SUSEONG_HALL_URL,
    },
    "MUNI_IR_F4E1E044659E": {
        "decision": "include_fixed_district_education_reservation_child_ledger",
        "url": DAEGU_SUSEONG_RESERVATION_URL,
    },
    "MUNI_IR_2BD0606E8578": {
        "decision": "exclude_private_institution_directory_separate_owner",
        "url": ("https://lll.suseong.kr/index.do?menu_id=00001883&menu_link=/front/education/searchEdu.do"),
        "existing_owner": "MUNI_LLL_SUSEONG_KR_C40B81D9",
    },
    "MUNI_IR_60F7F72FFD9E": {
        "decision": "include_district_reservation_shell_only_through_education_filters",
        "url": "https://www.suseong.kr/yeyak/index.do",
    },
}

DAEGU_SUSEONG_EXCLUDED_SCOPE: Mapping[str, Mapping[str, str]] = {
    "private_institution_directory": {
        "url": ("https://lll.suseong.kr/index.do?menu_id=00001883&menu_link=/front/education/searchEdu.do"),
        "reason": "separate_existing_owner_and_private_institution_directory",
    },
    "daegu_city_aggregate": {
        "url": "https://yeyak.daegu.go.kr/lect/list",
        "reason": "separate_citywide_aggregate_owner_DAEGU_RESERVATION",
    },
    "suseong_libraries": {
        "url": "https://library.suseong.kr/",
        "reason": "separate_library_network_owner",
    },
    "suseong_cultural_foundation": {
        "url": "https://www.sscf.or.kr/",
        "reason": "separate_cultural_foundation_and_facility_owner",
    },
    "suseong_future_education": {
        "url": "https://www.s-next.or.kr/",
        "reason": "separate_specialised_future_education_facility_owner",
    },
    "youth_training_centre": {
        "url": "https://dawa.or.kr/",
        "reason": "separate_youth_facility_owner",
    },
    "sports_and_facilities": {
        "url": "https://www.suseong.kr/yeyak/index.do",
        "reason": "noneducation_categories_excluded_by_category_filter",
    },
    "tourism_experiences": {
        "url": "https://www.suseong.kr/tour/index.do",
        "reason": "separate_tourism_and_experience_owner",
    },
}

DAEGU_SUSEONG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "source_rows": 14649,
    "source_rows_by_ledger": {
        "learning_centres": 13758,
        "learning_hall": 890,
        "district_reservation": 1,
    },
    "declared_pages": {
        "learning_centres": 1376,
        "learning_hall": 89,
        "district_reservation": 1,
    },
    "current_source_rows": 412,
    "suppressed_cancelled_rows": 15,
    "suppressed_practice_rows": 1,
    "returned_rows": 396,
    "status_counts": {"OPEN": 8, "SCHEDULED": 1, "CLOSED": 387},
    "network_requests": 436,
    "duplicate_rows": 0,
    "application_controls": 8,
    "audited_application_date_anomalies": 2,
    "audited_education_date_anomalies": 13,
}


class DaeguSuseongContractError(ValueError):
    """Raised when a public Suseong-gu source contract changes."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_CRS_ID_RE = re.compile(r"CRS_\d{12}")
_YEYAK_ID_RE = re.compile(r"Yeyak_\d{9}")
_SHORT_RANGE_RE = re.compile(
    r"(?<!\d)(\d{2})\.(\d{2})\.(\d{2})\s*~\s*"
    r"(\d{2})\.(\d{2})\.(\d{2})(?!\d)"
)
_FULL_RANGE_RE = re.compile(
    r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?:\s+\d{1,2}:\d{2})?\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})(?:\s+\d{1,2}:\d{2})?(?!\d)"
)
_CAPACITY_RE = re.compile(
    r"(?:온라인\s*:\s*)?(\d[\d,]*)\s*명?\s*"
    r"\(현재\s*신청인원\s*:\s*(\d[\d,]*)\s*명?\)"
)
_SLASH_CAPACITY_RE = re.compile(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)")
_AMOUNT_RE = re.compile(r"(?<!\d)(\d[\d,]*)\s*원")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_MOBILE_RE = re.compile(r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)")

_CENTRE_BRANCHES: Mapping[str, str] = {
    "고산": "고산평생학습센터",
    "지산": "지산평생학습센터",
    "두산": "두산평생학습센터",
    "수성동": "수성동평생학습센터",
    "만촌": "만촌평생학습센터",
    "파동": "파동평생학습센터",
    "정보화": "수성구 정보화교육",
    "거꾸로인생학교": "거꾸로인생학교",
}
_INFO_PARTITIONS: Mapping[str, tuple[str, str]] = {
    "coming": ("진행예정", "SCHEDULED"),
    "open": ("진행중", "OPEN"),
    "closed": ("진행완료", "CLOSED"),
}
_AUDITED_REVERSED_APPLICATION = {
    (
        "learning_centres",
        "CRS_000000043096",
    ): (date(2026, 6, 4), date(2026, 5, 21)),
    (
        "learning_hall",
        "CRS_000000033981",
    ): (date(2024, 10, 1), date(2024, 9, 30)),
}
_AUDITED_REVERSED_EDUCATION = {
    ("learning_hall", "CRS_000000040924"): (date(2026, 6, 8), date(2026, 2, 8)),
    ("learning_hall", "CRS_000000037696"): (date(2025, 8, 24), date(2025, 2, 23)),
    ("learning_hall", "CRS_000000037694"): (date(2025, 8, 24), date(2025, 2, 23)),
    ("learning_hall", "CRS_000000037692"): (date(2025, 8, 17), date(2025, 2, 16)),
    ("learning_hall", "CRS_000000037690"): (date(2025, 8, 17), date(2025, 2, 16)),
    ("learning_hall", "CRS_000000037680"): (date(2025, 11, 17), date(2025, 8, 17)),
    ("learning_hall", "CRS_000000034287"): (date(2025, 2, 7), date(2025, 1, 7)),
    ("learning_hall", "CRS_000000034086"): (date(2025, 2, 16), date(2024, 11, 16)),
    ("learning_hall", "CRS_000000033772"): (date(2024, 10, 7), date(2024, 9, 7)),
    ("learning_hall", "CRS_000000033752"): (date(2024, 11, 28), date(2024, 9, 28)),
    ("learning_hall", "CRS_000000033744"): (date(2025, 3, 28), date(2024, 9, 28)),
    ("learning_hall", "CRS_000000022881"): (date(2022, 12, 19), date(2022, 11, 23)),
    ("learning_hall", "CRS_000000021518"): (date(2022, 11, 25), date(2022, 6, 25)),
}
_AUDITED_PRACTICE_ROWS = frozenset(
    {
        ("Yeyak_000000567", "주민정보화교육 접수 연습용"),
        ("CRS_000000042071", "연습용 강좌"),
    }
)

_COURSE_DETAIL_FIELDS = {
    "강좌명",
    "강좌분류",
    "내용분류",
    "내용별 분류",
    "교육기관",
    "교육대상",
    "모집인원",
    "신청기간",
    "교육기간",
    "교육시간",
    "교육장소",
    "수강료",
    "재료비",
    "연령제한",
    "개인정보 동의",
    "접수방법",
    "강사",
    "강좌소개",
    "강의계획서",
    "문의전화",
    "신청상태",
    "교육상태",
    "오시는 길",
    "주소",
    "약도",
    "주의사항",
    "환불정책",
}
_INFO_DETAIL_ALLOWED_FIELDS = {
    "제목",
    "신청기간",
    "신청인원/모집인원",
    "교육기간",
    "장소",
    "선정방식",
    "선정자발표",
    "교육대상",
    "비용",
    "담당자",
    "글내용",
    "첨부파일목록",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _node_text(node: HtmlElement | None) -> str:
    if node is None:
        return ""
    return _clean(" ".join(node.itertext()))


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value)[:10])


def _canonical_query(value: str) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
    parsed = urlparse(_clean(value))
    if parsed.username or parsed.password or parsed.port is not None or parsed.params or parsed.fragment:
        return "", "", "", ()
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").rstrip(".").lower(),
        parsed.path,
        tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
    )


def is_daegu_suseong_education_target(target: Any) -> bool:
    """Match only the canonical Suseong-gu education owner."""

    if _clean(_target_value(target, "provider")) != DAEGU_SUSEONG_PROVIDER:
        return False
    if _canonical_query(_target_value(target, "url")) != _canonical_query(DAEGU_SUSEONG_URL):
        return False
    candidate = _clean(_target_value(target, "candidate_id"))
    return not candidate or candidate == DAEGU_SUSEONG_CANDIDATE_ID


is_target = is_daegu_suseong_education_target


def daegu_suseong_list_url(ledger: str | EducationLedger, page: int) -> str:
    item = _LEDGER_BY_KEY.get(ledger) if isinstance(ledger, str) else ledger
    if item is None or isinstance(page, bool) or not isinstance(page, int):
        return ""
    query = urlencode(
        {
            "menu_id": ("00001969" if item.key == "learning_centres" else "00002307"),
            "menu_link": item.list_menu_link,
            "pageIndex": page,
        }
    )
    return f"https://{DAEGU_SUSEONG_HOST}/index.do?{query}"


def daegu_suseong_detail_url(ledger: str | EducationLedger, identity: Any) -> str:
    item = _LEDGER_BY_KEY.get(ledger) if isinstance(ledger, str) else ledger
    course_id = _clean(identity)
    if item is None or not _CRS_ID_RE.fullmatch(course_id):
        return ""
    query = urlencode(
        {
            "menu_id": ("00001969" if item.key == "learning_centres" else "00002307"),
            "menu_link": item.detail_menu_link,
            "crsId": course_id,
        }
    )
    return f"https://{DAEGU_SUSEONG_HOST}/index.do?{query}"


def daegu_suseong_info_list_url(partition: str, page: int) -> str:
    if partition not in _INFO_PARTITIONS or page < 1:
        return ""
    query = urlencode(
        {
            "menu_id": "00031480",
            "menu_link": "front/yeyak/yeyakList.do",
            "searchCategory": "3,5",
            "searchStatus": partition,
            "pageIndex": page,
        }
    )
    return f"https://{DAEGU_SUSEONG_DISTRICT_HOST}/yeyak/index.do?{query}"


def daegu_suseong_info_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _YEYAK_ID_RE.fullmatch(value):
        return ""
    query = urlencode(
        {
            "menu_id": "00031480",
            "menu_link": "front/yeyak/yeyakView.do",
            "yeyak_id": value,
        }
    )
    return f"https://{DAEGU_SUSEONG_DISTRICT_HOST}/yeyak/index.do?{query}"


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def daegu_suseong_session_factory() -> SafeSession:
    """Return a managed session scoped to the audited complete-list response."""

    current = SafeSession(
        max_response_bytes=DAEGU_SUSEONG_MANAGED_MAX_RESPONSE_BYTES,
        total_timeout_seconds=120,
    )
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current: requests.Session, url: str, timeout: int) -> Any:
    return current.get(
        url,
        timeout=timeout,
        verify=True,
        allow_redirects=False,
        headers={"Referer": url},
    )


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _RequestBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.count = 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.count >= self.maximum:
                raise DaeguSuseongContractError("max_requests budget exceeded")
            self.count += 1


def _response_content(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    text = getattr(response, "text", "")
    return str(text or "").encode("utf-8")


def _strict_response(response: Any, url: str, maximum_bytes: int) -> bytes:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise DaeguSuseongContractError(f"unexpected HTTP {status} for {url}")
    if getattr(response, "history", None):
        raise DaeguSuseongContractError(f"redirect is not accepted for {url}")
    content = _response_content(response)
    if not content or len(content) > maximum_bytes:
        raise DaeguSuseongContractError(f"response byte boundary changed for {url}: {len(content)}")
    headers = getattr(response, "headers", {}) or {}
    content_type = _clean(headers.get("content-type") or headers.get("Content-Type"))
    if content_type and "html" not in content_type.lower():
        raise DaeguSuseongContractError(f"non-HTML response for {url}")
    return content


def _fetch(
    current: Any,
    url: str,
    *,
    fetcher: Fetcher,
    timeout: int,
    attempts: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
    maximum_bytes: int,
) -> tuple[bytes, int]:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            budget.take()
            response = fetcher(current, url, timeout)
            return _strict_response(response, url, maximum_bytes), attempt - 1
        except Exception as exc:
            errors.append(f"attempt {attempt}: {_clean(exc)}")
            if attempt < attempts:
                sleeper(min(0.4 * attempt, 1.2))
    raise DaeguSuseongContractError(f"request failed for {url}: {' | '.join(errors)}")


def _document(content: bytes, label: str) -> HtmlElement:
    try:
        root = lxml_html.fromstring(content.decode("utf-8", errors="strict"))
    except Exception as exc:
        raise DaeguSuseongContractError(f"{label}: invalid UTF-8 HTML") from exc
    if root.tag.lower() != "html" and not root.xpath("//html"):
        raise DaeguSuseongContractError(f"{label}: missing HTML root")
    return root


def _single(nodes: list[HtmlElement], label: str) -> HtmlElement:
    if len(nodes) != 1:
        raise DaeguSuseongContractError(f"{label}: expected one node, got {len(nodes)}")
    return nodes[0]


def _short_range(value: str, label: str) -> tuple[date, date]:
    match = _SHORT_RANGE_RE.search(_clean(value))
    if not match:
        raise DaeguSuseongContractError(f"{label}: invalid short date range")
    try:
        start = date(2000 + int(match[1]), int(match[2]), int(match[3]))
        end = date(2000 + int(match[4]), int(match[5]), int(match[6]))
    except ValueError as exc:
        raise DaeguSuseongContractError(f"{label}: impossible short date") from exc
    return start, end


def _full_range(value: str, label: str) -> tuple[date, date]:
    match = _FULL_RANGE_RE.search(_clean(value))
    if not match:
        raise DaeguSuseongContractError(f"{label}: invalid full date range")
    try:
        start = date(int(match[1]), int(match[2]), int(match[3]))
        end = date(int(match[4]), int(match[5]), int(match[6]))
    except ValueError as exc:
        raise DaeguSuseongContractError(f"{label}: impossible full date") from exc
    return start, end


def _validate_ranges(
    ledger: str,
    identity: str,
    apply_start: date,
    apply_end: date,
    education_start: date,
    education_end: date,
) -> tuple[bool, bool]:
    education_anomaly = education_end < education_start
    if education_anomaly and _AUDITED_REVERSED_EDUCATION.get((ledger, identity)) != (education_start, education_end):
        raise DaeguSuseongContractError(f"{identity}: reversed education period")
    application_anomaly = apply_end < apply_start
    if application_anomaly and _AUDITED_REVERSED_APPLICATION.get((ledger, identity)) != (apply_start, apply_end):
        raise DaeguSuseongContractError(f"{identity}: reversed application period")
    return application_anomaly, education_anomaly


def _pager(root: HtmlElement, function_name: str) -> tuple[int, Optional[int]]:
    pagination = _single(
        root.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' pagination ')]"),
        "pagination",
    )
    pages: list[int] = []
    pattern = re.compile(re.escape(function_name.replace("details", "list")) + r"\((\d+)")
    # Hall uses fn_learningHall_list and the replacement above preserves it.
    for node in pagination.xpath(".//*[@onclick]"):
        pages.extend(int(value) for value in pattern.findall(node.get("onclick") or ""))
    if not pages:
        # The info ledger uses a different navigation function.
        for node in pagination.xpath(".//*[@onclick]"):
            pages.extend(int(value) for value in re.findall(r"fn_icms_navi_list\((\d+)", node.get("onclick") or ""))
    if not pages:
        raise DaeguSuseongContractError("pagination has no declared page boundary")
    strong = pagination.xpath("./strong")
    current: Optional[int] = None
    if strong and _node_text(strong[0]).isdigit():
        current = int(_node_text(strong[0]))
    return max(pages), current


def _class_node(root: HtmlElement, name: str) -> list[HtmlElement]:
    return root.xpath(
        ".//*[contains(concat(' ', normalize-space(@class), ' '), $token)]",
        token=f" {name} ",
    )


def _source_status(application: str, education: str, combined: str) -> str:
    text = _clean(" ".join((application, education, combined)))
    if "폐강" in text:
        return "CLOSED"
    if any(marker in text for marker in ("신청하기", "신청중", "접수중")):
        return "OPEN"
    if any(marker in text for marker in ("신청예정", "접수예정")):
        return "SCHEDULED"
    if any(marker in text for marker in ("신청마감", "접수마감", "마감")):
        return "CLOSED"
    if not application and education in {"", "교육완료", "교육중", "교육예정", "폐강"}:
        return "CLOSED"
    raise DaeguSuseongContractError(f"unknown source status {text!r}")


def _course_headers(ledger: EducationLedger) -> tuple[str, ...]:
    return (
        "번호",
        "강좌명 교육기관",
        "신청기간 교육기간",
        "수강료 재료비",
        "신청/모집",
        "접수방법",
        "상태",
    )


def _parse_course_page(
    content: bytes,
    ledger: EducationLedger,
    *,
    requested_page: int,
) -> tuple[list[dict[str, Any]], int, Optional[int]]:
    root = _document(content, f"{ledger.key} page {requested_page}")
    title = _clean(root.xpath("string(//title)"))
    if ledger.title_marker not in title:
        raise DaeguSuseongContractError(f"{ledger.key}: title marker changed")
    forms = root.xpath(f"//form[@id='{ledger.form_id}']")
    if len(forms) != 1:
        raise DaeguSuseongContractError(f"{ledger.key}: list form changed")
    tables = root.xpath("//table[.//thead//th and .//tbody]")
    candidates = [
        table
        for table in tables
        if tuple(_node_text(node) for node in table.xpath(".//thead/tr[1]/th")) == _course_headers(ledger)
    ]
    table = _single(candidates, f"{ledger.key} course table")
    last_page, current_page = _pager(root, ledger.function_name)
    if requested_page > 0 and requested_page <= last_page and current_page != requested_page:
        raise DaeguSuseongContractError(f"{ledger.key}: requested/current page mismatch")
    rows: list[dict[str, Any]] = []
    for row_node in table.xpath(".//tbody/tr"):
        cells = row_node.xpath("./td")
        if not cells:
            if _node_text(row_node):
                raise DaeguSuseongContractError(f"{ledger.key}: unknown empty-row marker")
            continue
        expected_cells = 8 if ledger.key == "learning_centres" else 7
        if len(cells) != expected_cells:
            raise DaeguSuseongContractError(f"{ledger.key}: list column shape changed")
        number = _node_text(cells[0])
        if not number.isdigit():
            raise DaeguSuseongContractError(f"{ledger.key}: invalid row number")
        links = row_node.xpath(f".//a[contains(@onclick, '{ledger.function_name}')]")
        identities = {
            match[1]
            for link in links
            if (
                match := re.search(
                    re.escape(ledger.function_name) + r"\('([^']+)'",
                    link.get("onclick") or "",
                )
            )
        }
        if len(identities) != 1:
            raise DaeguSuseongContractError(f"{ledger.key}: invalid course identity")
        identity = identities.pop()
        if not _CRS_ID_RE.fullmatch(identity):
            raise DaeguSuseongContractError(f"{ledger.key}: invalid course identity")
        title_node = _single(_class_node(row_node, "lecture"), f"{identity} list title")
        branch_node = _single(_class_node(row_node, "educational"), f"{identity} list branch")
        title_text = _node_text(title_node)
        branch = _node_text(branch_node)
        if not title_text or len(title_text) > 500:
            raise DaeguSuseongContractError(f"{identity}: invalid title")
        p1 = _single(_class_node(row_node, "p1"), f"{identity} application period")
        p2 = _single(_class_node(row_node, "p2"), f"{identity} education period")
        p3_nodes = _class_node(row_node, "p3")
        apply_start, apply_end = _short_range(_node_text(p1), f"{identity} application period")
        education_start, education_end = _short_range(_node_text(p2), f"{identity} education period")
        audited_application_anomaly, audited_education_anomaly = _validate_ranges(
            ledger.key,
            identity,
            apply_start,
            apply_end,
            education_start,
            education_end,
        )
        app_groups = _class_node(p1, "group_p1")
        education_groups = _class_node(p2, "group_p2")
        application_state = _node_text(app_groups[0]) if app_groups else ""
        education_state = _node_text(education_groups[0]) if education_groups else ""
        combined_status = _node_text(cells[-1])
        normalized_status = _source_status(application_state, education_state, combined_status)
        cancelled = "폐강" in _clean(" ".join((title_text, education_state, combined_status)))
        if ledger.key == "learning_centres":
            schedule_cell = _clean(_node_text(cells[2]).removeprefix("시간 :"))
            fee_cell, capacity_cell, method_cell = cells[4], cells[5], cells[6]
        else:
            schedule_cell = ""
            fee_cell, capacity_cell, method_cell = cells[3], cells[4], cells[5]
        schedule = _node_text(p3_nodes[0]) if p3_nodes else schedule_cell
        schedule = re.sub(r"^요일\s*시간\s*", "", schedule).strip()
        method = re.sub(r"^접수방법\s*:\s*", "", _node_text(method_cell)).strip()
        rows.append(
            {
                "_ledger": ledger.key,
                "_identity": identity,
                "_ordinal": int(number),
                "title": title_text,
                "_branch_alias": branch,
                "_application_state": application_state,
                "_education_state": education_state,
                "_combined_status": combined_status,
                "_status": normalized_status,
                "_cancelled": cancelled,
                "_audited_application_anomaly": audited_application_anomaly,
                "_audited_education_anomaly": audited_education_anomaly,
                "apply_start": apply_start.isoformat(),
                "apply_end": apply_end.isoformat(),
                "start_date": education_start.isoformat(),
                "end_date": education_end.isoformat(),
                "schedule_raw": schedule,
                "fee_list_raw": _node_text(fee_cell),
                "capacity_list_raw": _node_text(capacity_cell),
                "application_method_raw": method,
                "raw_url": daegu_suseong_detail_url(ledger, identity),
            }
        )
    return rows, last_page, current_page


def _signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.get("_ledger"),
            row.get("_identity"),
            row.get("title"),
            row.get("_branch_alias"),
            row.get("apply_start"),
            row.get("apply_end"),
            row.get("start_date"),
            row.get("end_date"),
            row.get("_combined_status"),
        )
        for row in rows
    )


def _collect_course_ledger(
    ledger: EducationLedger,
    current: Any,
    *,
    fetcher: Fetcher,
    timeout: int,
    attempts: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
    max_pages: int,
    source_limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    retries = 0

    def page(number: int) -> tuple[list[dict[str, Any]], int, Optional[int]]:
        nonlocal retries
        url = daegu_suseong_list_url(ledger, number)
        content, retry_count = _fetch(
            current,
            url,
            fetcher=fetcher,
            timeout=timeout,
            attempts=attempts,
            sleeper=sleeper,
            budget=budget,
            maximum_bytes=DAEGU_SUSEONG_MAX_LIST_BYTES,
        )
        retries += retry_count
        return _parse_course_page(content, ledger, requested_page=number)

    first, last_page, _ = page(1)
    if last_page < 1 or last_page + 1 > max_pages:
        raise DaeguSuseongContractError(f"{ledger.key}: sentinel page {last_page + 1} exceeds max_pages")
    if last_page > 1 and len(first) != DAEGU_SUSEONG_PAGE_SIZE:
        raise DaeguSuseongContractError(f"{ledger.key}: first page is unexpectedly short")
    complete, complete_last, complete_current = page(-1)
    if complete_current is not None or complete_last != last_page:
        raise DaeguSuseongContractError(f"{ledger.key}: complete-list boundary changed")
    if not complete or len(complete) > source_limit:
        raise DaeguSuseongContractError(f"{ledger.key}: source row cap reached")
    identities = [row["_identity"] for row in complete]
    if len(identities) != len(set(identities)):
        raise DaeguSuseongContractError(f"{ledger.key}: duplicate source identity")
    if _signature(complete[: len(first)]) != _signature(first):
        raise DaeguSuseongContractError(f"{ledger.key}: complete-list first edge mismatch")
    final, final_last, _ = page(last_page)
    if final_last != last_page or not final or len(final) > DAEGU_SUSEONG_PAGE_SIZE:
        raise DaeguSuseongContractError(f"{ledger.key}: invalid final page")
    expected_count = (last_page - 1) * DAEGU_SUSEONG_PAGE_SIZE + len(final)
    if len(complete) != expected_count:
        raise DaeguSuseongContractError(f"{ledger.key}: complete rows {len(complete)} != declared {expected_count}")
    if _signature(complete[-len(final) :]) != _signature(final):
        raise DaeguSuseongContractError(f"{ledger.key}: complete-list final edge mismatch")
    sentinel, sentinel_last, sentinel_current = page(last_page + 1)
    if sentinel or sentinel_last != last_page or sentinel_current is not None:
        raise DaeguSuseongContractError(f"{ledger.key}: empty sentinel changed")
    first_recheck, first_last, _ = page(1)
    final_recheck, final_recheck_last, _ = page(last_page)
    if first_last != last_page or _signature(first_recheck) != _signature(first):
        raise DaeguSuseongContractError(f"{ledger.key}: stable first boundary changed")
    if final_recheck_last != last_page or _signature(final_recheck) != _signature(final):
        raise DaeguSuseongContractError(f"{ledger.key}: stable final boundary changed")
    return complete, {
        "pages": last_page,
        "list_requests": 6,
        "sentinel_kind": "empty",
        "stability_rechecks": 2,
        "retries": retries,
    }


def _parse_info_page(
    content: bytes,
    partition: str,
    *,
    requested_page: int,
) -> tuple[list[dict[str, Any]], int, Optional[int]]:
    root = _document(content, f"district reservation {partition} page {requested_page}")
    if "수성구 예약서비스" not in _clean(root.xpath("string(//title)")):
        raise DaeguSuseongContractError("district reservation title changed")
    forms = root.xpath("//form[@id='yeyakVO']")
    if len(forms) != 1:
        raise DaeguSuseongContractError("district reservation list form changed")
    table = _single(root.xpath("//table[@id='bbsList']"), "district reservation table")
    headers = tuple(_node_text(node) for node in table.xpath(".//thead/tr[1]/th"))
    if headers != ("번호", "카테고리", "기관", "제목", "신청기간", "처리현황"):
        raise DaeguSuseongContractError("district reservation headers changed")
    last_page, current_page = _pager(root, "fn_icms_navi_details")
    if requested_page <= last_page and current_page != requested_page:
        raise DaeguSuseongContractError("district reservation page mismatch")
    expected_source_status, normalized_status = _INFO_PARTITIONS[partition]
    rows: list[dict[str, Any]] = []
    for row_node in table.xpath(".//tbody/tr"):
        cells = row_node.xpath("./td")
        if not cells:
            if _node_text(row_node):
                raise DaeguSuseongContractError("district reservation empty marker changed")
            continue
        if len(cells) != 6 or not _node_text(cells[0]).isdigit():
            raise DaeguSuseongContractError("district reservation row shape changed")
        link = _single(cells[3].xpath(".//a[@href]"), "district reservation detail link")
        identity_match = re.search(r"viewPage\('([^']+)'\)", link.get("href") or "")
        if not identity_match or not _YEYAK_ID_RE.fullmatch(identity_match[1]):
            raise DaeguSuseongContractError("district reservation identity changed")
        identity = identity_match[1]
        category = _node_text(cells[1])
        institution = _node_text(cells[2])
        title = _node_text(link)
        source_status = _node_text(cells[5])
        if category not in {"평생학습", "정보화교육"}:
            raise DaeguSuseongContractError("noneducation category entered owned filter")
        if source_status != expected_source_status or not institution or not title:
            raise DaeguSuseongContractError("district reservation row contract changed")
        apply_start, apply_end = _full_range(_node_text(cells[4]), f"{identity} application period")
        if apply_end < apply_start:
            raise DaeguSuseongContractError(f"{identity}: reversed application period")
        rows.append(
            {
                "_ledger": "district_reservation",
                "_partition": partition,
                "_identity": identity,
                "_ordinal": int(_node_text(cells[0])),
                "title": title,
                "_branch_alias": institution,
                "_category": category,
                "_combined_status": source_status,
                "_status": normalized_status,
                "_cancelled": False,
                "apply_start": apply_start.isoformat(),
                "apply_end": apply_end.isoformat(),
                "raw_url": daegu_suseong_info_detail_url(identity),
            }
        )
    return rows, last_page, current_page


def _collect_info_ledger(
    current: Any,
    *,
    fetcher: Fetcher,
    timeout: int,
    attempts: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
    max_pages: int,
    source_limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    partition_pages: dict[str, int] = {}
    retries = 0
    requests_count = 0
    for partition in _INFO_PARTITIONS:

        def page(number: int) -> tuple[list[dict[str, Any]], int, Optional[int]]:
            nonlocal retries, requests_count
            content, retry_count = _fetch(
                current,
                daegu_suseong_info_list_url(partition, number),
                fetcher=fetcher,
                timeout=timeout,
                attempts=attempts,
                sleeper=sleeper,
                budget=budget,
                maximum_bytes=DAEGU_SUSEONG_MAX_DETAIL_BYTES,
            )
            retries += retry_count
            requests_count += 1
            return _parse_info_page(content, partition, requested_page=number)

        first, last_page, _ = page(1)
        if last_page < 1 or last_page + 1 > max_pages:
            raise DaeguSuseongContractError(f"district reservation {partition}: max_pages exceeded")
        pages = [first]
        for number in range(2, last_page + 1):
            values, declared, _ = page(number)
            if declared != last_page:
                raise DaeguSuseongContractError(f"district reservation {partition}: page total drift")
            pages.append(values)
        complete = [row for values in pages for row in values]
        if len(complete) > source_limit:
            raise DaeguSuseongContractError("district reservation source row cap reached")
        if last_page > 1 and any(len(values) != DAEGU_SUSEONG_PAGE_SIZE for values in pages[:-1]):
            raise DaeguSuseongContractError("district reservation short interior page")
        sentinel, declared, sentinel_current = page(last_page + 1)
        if sentinel or declared != last_page or sentinel_current is not None:
            raise DaeguSuseongContractError(f"district reservation {partition}: empty sentinel changed")
        first_recheck, declared, _ = page(1)
        final_recheck, final_declared, _ = page(last_page)
        if declared != last_page or _signature(first_recheck) != _signature(first):
            raise DaeguSuseongContractError(f"district reservation {partition}: stable first boundary changed")
        if final_declared != last_page or _signature(final_recheck) != _signature(pages[-1]):
            raise DaeguSuseongContractError(f"district reservation {partition}: stable final boundary changed")
        partition_pages[partition] = last_page
        all_rows.extend(complete)
    identities = [row["_identity"] for row in all_rows]
    if len(identities) != len(set(identities)):
        raise DaeguSuseongContractError("district reservation duplicate identity across status partitions")
    return all_rows, {
        "pages": partition_pages,
        "list_requests": requests_count,
        "sentinel_kind": "empty_per_status_partition",
        "stability_rechecks": len(_INFO_PARTITIONS) * 2,
        "retries": retries,
    }


def _direct_table_pairs(root: HtmlElement, ledger: EducationLedger) -> dict[str, str]:
    table = _single(
        root.xpath("//table[contains(concat(' ', normalize-space(@class), ' '), ' tbl02 ')]"),
        "course detail table",
    )
    pairs: dict[str, str] = {}
    for row in table.xpath(".//tr"):
        headers = row.xpath("./th")
        values = row.xpath("./td")
        if not headers and not values:
            continue
        if len(headers) != 1 or len(values) != 1:
            raise DaeguSuseongContractError("course detail table shape changed")
        key = _node_text(headers[0])
        if not key or key in pairs:
            raise DaeguSuseongContractError("course detail labels changed")
        pairs[key] = _node_text(values[0])
    expected = _COURSE_DETAIL_FIELDS if ledger.key == "learning_centres" else _COURSE_DETAIL_FIELDS - {"교육장소"}
    if set(pairs) != expected:
        raise DaeguSuseongContractError("course detail field boundary changed")
    return pairs


def _capacity(value: str) -> tuple[Optional[int], Optional[int]]:
    match = _CAPACITY_RE.search(_clean(value))
    if not match:
        return None, None
    return int(match[2].replace(",", "")), int(match[1].replace(",", ""))


def _fee(value: str) -> tuple[str, Optional[int]]:
    text = _clean(value)
    if not text:
        return "", None
    if text in {"무료", "0원", "0 원"}:
        return "무료", 0
    match = _AMOUNT_RE.search(text)
    if not match:
        return text, None
    amount = int(match[1].replace(",", ""))
    return f"{amount:,}원", amount


def _base_row(
    listed: Mapping[str, Any],
    *,
    branch: str,
    category: str,
    target: str,
    schedule: str,
    venue: str,
    address: str,
    fee: str,
    fee_amount: Optional[int],
    material_fee: str,
    capacity_current: Optional[int],
    capacity_total: Optional[int],
    application_method: str,
    application_url: str,
    raw_detail_status: Mapping[str, str],
) -> dict[str, Any]:
    ledger = _clean(listed["_ledger"])
    identity = _clean(listed["_identity"])
    source_start = _clean(listed["start_date"])
    source_end = _clean(listed["end_date"])
    source_apply_start = _clean(listed["apply_start"])
    source_apply_end = _clean(listed["apply_end"])
    start_date, end_date = sorted((source_start, source_end))
    apply_start, apply_end = sorted((source_apply_start, source_apply_end))
    return {
        "provider": DAEGU_SUSEONG_PROVIDER,
        "provider_course_id": f"{ledger}:{identity}",
        "title": _clean(listed["title"]),
        "branch": branch,
        "branch_code": (
            _LEDGER_BY_KEY[ledger].branch_code if ledger in _LEDGER_BY_KEY else "DAEGU_SUSEONG_DISTRICT_RESERVATION"
        ),
        "category": category or "평생교육",
        "raw_url": _clean(listed["raw_url"]),
        "application_url": application_url,
        "status": _clean(listed["_status"]),
        "reservation_available": bool(application_url),
        "fee": fee,
        "fee_amount": fee_amount,
        "material_fee": material_fee,
        "period": f"{start_date} ~ {end_date}",
        "start_date": start_date,
        "end_date": end_date,
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule_raw": schedule,
        "target": target,
        "capacity": (
            f"{capacity_current}/{capacity_total}명"
            if capacity_current is not None and capacity_total is not None
            else ""
        ),
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "venue_name": venue,
        "venue_address": address,
        "application_method_raw": application_method,
        "description": "",
        "municipality_code": DAEGU_SUSEONG_MUNICIPALITY_CODE,
        "municipality_name": DAEGU_SUSEONG_MUNICIPALITY_NAME,
        "sido": "대구광역시",
        "sigungu": "수성구",
        "raw_fields": {
            "parser": DAEGU_SUSEONG_PARSER,
            "ledger": ledger,
            "course_identity": identity,
            "source_status": _clean(listed.get("_combined_status")),
            "source_application_state": _clean(listed.get("_application_state")),
            "source_education_state": _clean(listed.get("_education_state")),
            "detail_status": dict(raw_detail_status),
            "audited_application_date_anomaly": bool(listed.get("_audited_application_anomaly")),
            "source_application_period": (
                f"{source_apply_start} ~ {source_apply_end}"
            ),
            "source_education_period": f"{source_start} ~ {source_end}",
            "pii_fields_stored": [],
        },
    }


def _parse_course_detail(
    content: bytes,
    listed: Mapping[str, Any],
    reference_day: date,
) -> dict[str, Any]:
    ledger = _LEDGER_BY_KEY[_clean(listed["_ledger"])]
    identity = _clean(listed["_identity"])
    root = _document(content, f"{ledger.key} detail {identity}")
    if ledger.title_marker not in _clean(root.xpath("string(//title)")):
        raise DaeguSuseongContractError(f"{identity}: detail title marker changed")
    forms = root.xpath(f"//form[@id='{ledger.form_id}Apply' or @name='{ledger.form_id}Apply']")
    form = _single(forms, f"{identity} detail form")
    crs_values = {_clean(node.get("value")) for node in form.xpath(".//input[@name='crsId']")}
    if crs_values != {identity}:
        raise DaeguSuseongContractError(f"{identity}: detail form identity mismatch")
    edu_values = {
        _clean(node.get("value")) for node in form.xpath(".//input[@name='edu_id']") if _clean(node.get("value"))
    }
    if edu_values and edu_values != {identity}:
        raise DaeguSuseongContractError(f"{identity}: secondary identity mismatch")
    pairs = _direct_table_pairs(root, ledger)
    if pairs["강좌명"] != listed["title"]:
        raise DaeguSuseongContractError(f"{identity}: detail heading mismatch")
    apply_start, apply_end = _full_range(pairs["신청기간"], f"{identity} detail application period")
    start, end = _full_range(pairs["교육기간"], f"{identity} detail education period")
    _validate_ranges(ledger.key, identity, apply_start, apply_end, start, end)
    if (
        apply_start.isoformat() != listed["apply_start"]
        or apply_end.isoformat() != listed["apply_end"]
        or start.isoformat() != listed["start_date"]
        or end.isoformat() != listed["end_date"]
    ):
        raise DaeguSuseongContractError(f"{identity}: list/detail period mismatch")
    branch_alias = _clean(listed["_branch_alias"])
    detail_branch = _clean(pairs["교육기관"])
    if ledger.key == "learning_centres":
        if branch_alias not in _CENTRE_BRANCHES:
            raise DaeguSuseongContractError(f"{identity}: unknown centre branch")
        branch = _CENTRE_BRANCHES[branch_alias]
        if detail_branch not in {branch, branch_alias}:
            raise DaeguSuseongContractError(f"{identity}: branch identity mismatch")
    else:
        branch = "수성구 평생학습관"
        if branch_alias not in {"", branch} or detail_branch not in {"", branch}:
            raise DaeguSuseongContractError(f"{identity}: hall branch identity mismatch")
    address = _clean(pairs["주소"])
    audited_practice = (identity, _clean(listed["title"])) in _AUDITED_PRACTICE_ROWS
    if (
        "대구광역시 수성구" not in address
        and "대구 수성구" not in address
        and not audited_practice
    ):
        raise DaeguSuseongContractError(f"{identity}: municipality address evidence changed")
    controls = [
        node
        for node in form.xpath(".//a[@onclick]")
        if (
            "fn_apply_learningHall2" in (node.get("onclick") or "")
            or "fn_apply_learning2" in (node.get("onclick") or "")
        )
    ]
    source_expects_control = listed["_status"] == "OPEN" and not listed["_cancelled"] and "인터넷" in pairs["접수방법"]
    if source_expects_control and len(controls) != 1:
        raise DaeguSuseongContractError(f"{identity}: application control changed")
    if not source_expects_control and controls:
        raise DaeguSuseongContractError(f"{identity}: unavailable course exposes application control")
    application_url = (
        _clean(listed["raw_url"]) if source_expects_control and apply_start <= reference_day <= apply_end else ""
    )
    current_count, total_count = _capacity(pairs["모집인원"])
    fee, fee_amount = _fee(pairs["수강료"])
    category = " > ".join(
        value
        for value in (
            _clean(pairs["강좌분류"]),
            _clean(pairs["내용분류"]),
            _clean(pairs["내용별 분류"]),
        )
        if value
    )
    detail_status = {
        "application": _clean(pairs["신청상태"]),
        "education": _clean(pairs["교육상태"]),
    }
    if any(not value or len(value) > 50 for value in detail_status.values()):
        raise DaeguSuseongContractError(f"{identity}: detail status changed")
    return _base_row(
        listed,
        branch=branch,
        category=category,
        target=_clean(pairs["교육대상"]).lstrip("| "),
        schedule=_clean(pairs["교육시간"]),
        venue=_clean(pairs.get("교육장소")) or branch,
        address=address,
        fee=fee,
        fee_amount=fee_amount,
        material_fee=_clean(pairs["재료비"]),
        capacity_current=current_count,
        capacity_total=total_count,
        application_method=_clean(pairs["접수방법"]).split("※", 1)[0].strip(),
        application_url=application_url,
        raw_detail_status=detail_status,
    )


def _info_pairs(root: HtmlElement) -> dict[str, str]:
    containers = root.xpath("//form[@id='yeyakVO']/div[@id='bbsView']")
    container = _single(containers, "district reservation public detail")
    pairs: dict[str, str] = {}
    for block in container.xpath(".//dl"):
        labels = block.xpath("./dt")
        values = block.xpath("./dd")
        if len(labels) != 1 or len(values) != 1:
            raise DaeguSuseongContractError("district detail field shape changed")
        key = _node_text(labels[0])
        if key in pairs:
            raise DaeguSuseongContractError("district detail duplicate label")
        value = _node_text(values[0])
        pairs[key] = value
    if not {"제목", "신청기간", "교육기간", "장소", "교육대상", "비용"}.issubset(pairs) or not set(pairs).issubset(
        _INFO_DETAIL_ALLOWED_FIELDS
    ):
        raise DaeguSuseongContractError("district detail field boundary changed")
    return pairs


def _parse_info_detail(
    content: bytes,
    listed: Mapping[str, Any],
    reference_day: date,
) -> dict[str, Any]:
    identity = _clean(listed["_identity"])
    root = _document(content, f"district reservation detail {identity}")
    if "수성구 예약서비스" not in _clean(root.xpath("string(//title)")):
        raise DaeguSuseongContractError(f"{identity}: district detail title changed")
    hidden_ids = {
        _clean(node.get("value")) for node in root.xpath("//form[@id='yeyakDetailVO']//input[@name='yeyak_id']")
    }
    if hidden_ids != {identity}:
        raise DaeguSuseongContractError(f"{identity}: district detail identity mismatch")
    # These public lookup fields must be empty.  Applicant/result endpoints are
    # never followed, and none of their values are allowed into output rows.
    for node in root.xpath("//*[@id='inputMyInfo']//input | //form[@id='yeyakDetailVO']//input"):
        name = _clean(node.get("name") or node.get("id")).lower()
        if name in {
            "name",
            "mobile_first",
            "mobile_middle",
            "mobile_last",
        } and _clean(node.get("value")):
            raise DaeguSuseongContractError(f"{identity}: private lookup payload exposed")
    pairs = _info_pairs(root)
    detail_title = pairs["제목"]
    source_status = _clean(listed["_combined_status"])
    if source_status and detail_title.endswith(source_status):
        detail_title = detail_title[: -len(source_status)].strip()
    if detail_title != listed["title"]:
        raise DaeguSuseongContractError(f"{identity}: district detail heading mismatch")
    apply_start, apply_end = _full_range(pairs["신청기간"], f"{identity} district application period")
    start, end = _full_range(pairs["교육기간"], f"{identity} district education period")
    if apply_end < apply_start or end < start:
        raise DaeguSuseongContractError(f"{identity}: reversed district detail period")
    if apply_start.isoformat() != listed["apply_start"] or apply_end.isoformat() != listed["apply_end"]:
        raise DaeguSuseongContractError(f"{identity}: district list/detail period mismatch")
    controls = root.xpath("//form[@id='yeyakVO']//a[contains(@href, 'registerPage')]")
    source_expects_control = listed["_status"] == "OPEN"
    if source_expects_control and len(controls) != 1:
        raise DaeguSuseongContractError(f"{identity}: district application control changed")
    if not source_expects_control and controls:
        raise DaeguSuseongContractError(f"{identity}: unavailable district row exposes application control")
    if controls:
        scripts = " ".join(root.xpath("//script/text()"))
        if f"yeyak_id={identity}" not in scripts or "addYeyak.do" not in scripts:
            raise DaeguSuseongContractError(f"{identity}: district application identity mismatch")
    capacity_current: Optional[int] = None
    capacity_total: Optional[int] = None
    capacity_text = _clean(pairs.get("신청인원/모집인원"))
    capacity_match = _SLASH_CAPACITY_RE.search(capacity_text)
    if capacity_match:
        capacity_current = int(capacity_match[1].replace(",", ""))
        capacity_total = int(capacity_match[2].replace(",", ""))
    listed = {
        **dict(listed),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    fee, fee_amount = _fee(pairs["비용"])
    return _base_row(
        listed,
        branch=_clean(listed["_branch_alias"]),
        category=_clean(listed["_category"]),
        target=_clean(pairs["교육대상"]),
        schedule="",
        venue=_clean(pairs["장소"]),
        address="대구광역시 수성구",
        fee=fee,
        fee_amount=fee_amount,
        material_fee="",
        capacity_current=capacity_current,
        capacity_total=capacity_total,
        application_method="온라인",
        application_url=(
            _clean(listed["raw_url"]) if source_expects_control and apply_start <= reference_day <= apply_end else ""
        ),
        raw_detail_status={
            "application": _clean(listed["_combined_status"]),
            "education": "",
        },
    )


@dataclass
class _ManyResult:
    values: dict[tuple[str, str], bytes]
    retries: int
    sessions: int
    errors: list[str]


def _fetch_many(
    jobs: list[tuple[tuple[str, str], str]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    attempts: int,
    max_workers: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
) -> _ManyResult:
    if not jobs:
        return _ManyResult({}, 0, 0, [])
    local = threading.local()
    sessions: list[Any] = []
    session_lock = threading.Lock()

    def work(key: tuple[str, str], url: str) -> tuple[tuple[str, str], bytes, int]:
        if not hasattr(local, "session"):
            local.session = session_factory()
            if local.session is None:
                raise DaeguSuseongContractError("session factory returned no session")
            with session_lock:
                sessions.append(local.session)
        content, retries = _fetch(
            local.session,
            url,
            fetcher=fetcher,
            timeout=timeout,
            attempts=attempts,
            sleeper=sleeper,
            budget=budget,
            maximum_bytes=DAEGU_SUSEONG_MAX_DETAIL_BYTES,
        )
        return key, content, retries

    values: dict[tuple[str, str], bytes] = {}
    errors: list[str] = []
    retry_count = 0
    try:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as executor:
            futures = {executor.submit(work, key, url): (key, url) for key, url in jobs}
            for future in as_completed(futures):
                key, url = futures[future]
                try:
                    returned_key, content, retries = future.result()
                    if returned_key in values:
                        raise DaeguSuseongContractError("duplicate detail result identity")
                    values[returned_key] = content
                    retry_count += retries
                except Exception as exc:
                    errors.append(f"{key[0]}:{key[1]} {url}: {_clean(exc)}")
    finally:
        for current in sessions:
            _close_quietly(current)
    return _ManyResult(values, retry_count, len(sessions), errors)


def _failed_meta(error: str = "") -> dict[str, Any]:
    return {
        "source_total": 0,
        "source_rows": 0,
        "source_rows_by_ledger": {},
        "source_current_rows_by_ledger": {},
        "returned_rows_by_ledger": {},
        "declared_pages_by_ledger": {},
        "pages": 0,
        "list_requests": 0,
        "complete_list_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "current_count": 0,
        "expired_count": 0,
        "suppressed_cancelled_rows": 0,
        "suppressed_practice_rows": 0,
        "suppressed_nonproduction_rows": 0,
        "audited_application_date_anomalies": 0,
        "audited_education_date_anomalies": 0,
        "duplicate_source_rows": 0,
        "semantic_duplicate_rows": 0,
        "returned_count": 0,
        "status_counts": {},
        "branch_counts": {},
        "application_control_count": 0,
        "network_requests": 0,
        "retry_count": 0,
        "worker_sessions": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "stable_recheck_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": error,
        "errors": [error] if error else [],
        "no_current_data": False,
        "no_current_reason": "",
        "parser": DAEGU_SUSEONG_PARSER,
        "candidate_id": DAEGU_SUSEONG_CANDIDATE_ID,
        "canonical_url": DAEGU_SUSEONG_URL,
        "ownership_scope": DAEGU_SUSEONG_OWNERSHIP_SCOPE,
        "excluded_scope": DAEGU_SUSEONG_EXCLUDED_SCOPE,
        "pii_policy": (
            "public_structured_allowlist; applicant/result/registration pages, "
            "instructors, contacts, attachments, maps and free text not requested/stored"
        ),
        "pii_payload_persisted": False,
        "application_pages_requested": 0,
        "applicant_result_pages_requested": 0,
    }


def collect_daegu_suseong_education(
    target: Any,
    timeout: int = 35,
    max_pages: int = 1500,
    detail_limit: int = 500,
    *,
    today: Optional[date | datetime | str] = None,
    max_requests: int = 4_000,
    source_limit: int = 20_000,
    max_workers: int = DAEGU_SUSEONG_MAX_WORKERS,
    fetch_attempts: int = DAEGU_SUSEONG_FETCH_ATTEMPTS,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete Suseong-gu education snapshot or fail atomically."""

    if not is_daegu_suseong_education_target(target):
        return (
            [],
            DAEGU_SUSEONG_PARSER,
            _failed_meta("target does not match the exact canonical Daegu Suseong-gu education owner"),
        )
    integer_limits = (
        timeout,
        max_pages,
        detail_limit,
        max_requests,
        source_limit,
        max_workers,
        fetch_attempts,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in integer_limits):
        return [], DAEGU_SUSEONG_PARSER, _failed_meta("invalid collection limits")

    factory = session_factory or _default_session_factory
    request = fetcher or _default_fetcher
    budget = _RequestBudget(max_requests)
    list_session: Any = None
    listed_by_ledger: dict[str, list[dict[str, Any]]] = {}
    stats_by_ledger: dict[str, dict[str, Any]] = {}
    retries = 0
    detail_attempts = 0
    worker_sessions = 0

    try:
        reference_day = _today(today)
        list_session = factory()
        if list_session is None:
            raise DaeguSuseongContractError("session factory returned no session")
        for ledger in DAEGU_SUSEONG_LEDGERS:
            values, stats = _collect_course_ledger(
                ledger,
                list_session,
                fetcher=request,
                timeout=timeout,
                attempts=fetch_attempts,
                sleeper=sleeper,
                budget=budget,
                max_pages=max_pages,
                source_limit=source_limit,
            )
            listed_by_ledger[ledger.key] = values
            stats_by_ledger[ledger.key] = stats
            retries += int(stats["retries"])
        info_values, info_stats = _collect_info_ledger(
            list_session,
            fetcher=request,
            timeout=timeout,
            attempts=fetch_attempts,
            sleeper=sleeper,
            budget=budget,
            max_pages=max_pages,
            source_limit=source_limit,
        )
        listed_by_ledger["district_reservation"] = info_values
        stats_by_ledger["district_reservation"] = info_stats
        retries += int(info_stats["retries"])

        all_listed = [row for ledger_rows in listed_by_ledger.values() for row in ledger_rows]
        source_total = len(all_listed)
        if source_total > source_limit:
            raise DaeguSuseongContractError("combined source row cap reached")
        global_identities = [row["_identity"] for row in all_listed]
        duplicate_source_rows = len(global_identities) - len(set(global_identities))
        if duplicate_source_rows:
            raise DaeguSuseongContractError("duplicate source identity across ledgers")

        current_course_rows = [
            row
            for key in ("learning_centres", "learning_hall")
            for row in listed_by_ledger[key]
            if date.fromisoformat(row["end_date"]) >= reference_day
        ]
        # The district ledger exposes no education end date in its list, so
        # every bounded source row is detailed before current filtering.
        detail_source_rows = current_course_rows + info_values
        detail_attempts = len(detail_source_rows)
        if detail_attempts > detail_limit:
            raise DaeguSuseongContractError(f"detail count {detail_attempts} exceeds detail_limit {detail_limit}")
        jobs = [((row["_ledger"], row["_identity"]), row["raw_url"]) for row in detail_source_rows]
        details = _fetch_many(
            jobs,
            fetcher=request,
            session_factory=factory,
            timeout=timeout,
            attempts=fetch_attempts,
            max_workers=max_workers,
            sleeper=sleeper,
            budget=budget,
        )
        retries += details.retries
        worker_sessions = details.sessions
        if details.errors or len(details.values) != detail_attempts:
            raise DaeguSuseongContractError("detail snapshot incomplete: " + "; ".join(details.errors))

        parsed_rows: list[dict[str, Any]] = []
        current_counts: Counter[str] = Counter()
        returned_counts: Counter[str] = Counter()
        suppressed_cancelled = 0
        suppressed_practice = 0
        for listed in current_course_rows:
            key = (listed["_ledger"], listed["_identity"])
            parsed = _parse_course_detail(details.values[key], listed, reference_day)
            current_counts[listed["_ledger"]] += 1
            if (listed["_identity"], listed["title"]) in _AUDITED_PRACTICE_ROWS:
                suppressed_practice += 1
                continue
            if listed["_cancelled"]:
                suppressed_cancelled += 1
                continue
            parsed_rows.append(parsed)
            returned_counts[listed["_ledger"]] += 1
        for listed in info_values:
            key = (listed["_ledger"], listed["_identity"])
            parsed = _parse_info_detail(details.values[key], listed, reference_day)
            if date.fromisoformat(parsed["end_date"]) < reference_day:
                continue
            current_counts["district_reservation"] += 1
            if (listed["_identity"], listed["title"]) in _AUDITED_PRACTICE_ROWS:
                suppressed_practice += 1
                continue
            parsed_rows.append(parsed)
            returned_counts["district_reservation"] += 1

        semantic_keys = [
            (
                re.sub(r"[^0-9A-Za-z가-힣]+", "", row["title"]).casefold(),
                row["start_date"],
                row["end_date"],
                re.sub(r"\s+", "", row["venue_address"]).casefold(),
            )
            for row in parsed_rows
        ]
        semantic_duplicates = len(semantic_keys) - len(set(semantic_keys))
        if semantic_duplicates:
            raise DaeguSuseongContractError("semantic duplicate course across ledgers")
        if dedupe_rows is not None:
            deduped = list(dedupe_rows(parsed_rows))
            if len(deduped) != len(parsed_rows):
                raise DaeguSuseongContractError("downstream dedupe changed owned snapshot")
            parsed_rows = deduped
        serialized = repr(parsed_rows)
        if _EMAIL_RE.search(serialized) or _MOBILE_RE.search(serialized):
            raise DaeguSuseongContractError("PII-like value escaped public allowlist")

        current_count = sum(current_counts.values())
        status_counts = dict(Counter(row["status"] for row in parsed_rows))
        branch_counts = dict(Counter(row["branch"] for row in parsed_rows))
        source_rows_by_ledger = {key: len(values) for key, values in listed_by_ledger.items()}
        declared_pages_by_ledger: dict[str, Any] = {
            key: stats_by_ledger[key]["pages"] for key in ("learning_centres", "learning_hall")
        }
        declared_pages_by_ledger["district_reservation"] = dict(info_stats["pages"])
        list_requests = sum(int(stats["list_requests"]) for stats in stats_by_ledger.values())
        stability_rechecks = sum(int(stats["stability_rechecks"]) for stats in stats_by_ledger.values())
        audited_anomalies = sum(bool(row.get("_audited_application_anomaly")) for row in all_listed)
        audited_education_anomalies = sum(bool(row.get("_audited_education_anomaly")) for row in all_listed)
        suppressed = suppressed_cancelled + suppressed_practice
        meta = {
            **_failed_meta(),
            "source_total": source_total,
            "source_rows": source_total,
            "source_rows_by_ledger": source_rows_by_ledger,
            "source_current_rows_by_ledger": dict(current_counts),
            "returned_rows_by_ledger": dict(returned_counts),
            "declared_pages_by_ledger": declared_pages_by_ledger,
            "pages": (
                int(stats_by_ledger["learning_centres"]["pages"])
                + int(stats_by_ledger["learning_hall"]["pages"])
                + sum(int(value) for value in info_stats["pages"].values())
            ),
            "list_requests": list_requests,
            "complete_list_requests": len(DAEGU_SUSEONG_LEDGERS),
            "sentinel_requests": len(DAEGU_SUSEONG_LEDGERS) + len(_INFO_PARTITIONS),
            "stability_rechecks": stability_rechecks,
            "detail_attempts": detail_attempts,
            "detail_pages": len(details.values),
            "current_count": current_count,
            "expired_count": source_total - current_count,
            "suppressed_cancelled_rows": suppressed_cancelled,
            "suppressed_practice_rows": suppressed_practice,
            "suppressed_nonproduction_rows": suppressed,
            "audited_application_date_anomalies": audited_anomalies,
            "audited_education_date_anomalies": audited_education_anomalies,
            "duplicate_source_rows": duplicate_source_rows,
            "semantic_duplicate_rows": semantic_duplicates,
            "returned_count": len(parsed_rows),
            "status_counts": status_counts,
            "branch_counts": branch_counts,
            "application_control_count": sum(bool(row["reservation_available"]) for row in parsed_rows),
            "network_requests": budget.count,
            "retry_count": retries,
            "worker_sessions": worker_sessions,
            "pagination_detected": True,
            "pagination_complete": True,
            "details_complete": True,
            "stable_recheck_complete": True,
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "configured_collection_error": "",
            "errors": [],
            "no_current_data": not parsed_rows,
            "no_current_reason": (
                "complete official ledgers contain no current/future production course" if not parsed_rows else ""
            ),
        }
        return parsed_rows, DAEGU_SUSEONG_PARSER, meta
    except Exception as exc:
        error = f"{type(exc).__name__}: {_clean(exc)}"
        source_rows_by_ledger = {key: len(values) for key, values in listed_by_ledger.items()}
        meta = {
            **_failed_meta(error),
            "source_total": sum(source_rows_by_ledger.values()),
            "source_rows": sum(source_rows_by_ledger.values()),
            "source_rows_by_ledger": source_rows_by_ledger,
            "list_requests": sum(int(stats.get("list_requests", 0)) for stats in stats_by_ledger.values()),
            "detail_attempts": detail_attempts,
            "network_requests": budget.count,
            "retry_count": retries,
            "worker_sessions": worker_sessions,
            "source_cap_reached": any(
                marker in error
                for marker in (
                    "max_pages",
                    "max_requests",
                    "detail_limit",
                    "source row cap",
                    "source row cap reached",
                )
            ),
        }
        return [], DAEGU_SUSEONG_PARSER, meta
    finally:
        _close_quietly(list_session)


collect = collect_daegu_suseong_education


__all__ = [
    "DAEGU_SUSEONG_PROVIDER",
    "DAEGU_SUSEONG_CANDIDATE_ID",
    "DAEGU_SUSEONG_MUNICIPALITY_CODE",
    "DAEGU_SUSEONG_MUNICIPALITY_NAME",
    "DAEGU_SUSEONG_URL",
    "DAEGU_SUSEONG_HALL_URL",
    "DAEGU_SUSEONG_RESERVATION_URL",
    "DAEGU_SUSEONG_PARSER",
    "DAEGU_SUSEONG_OWNERSHIP_SCOPE",
    "DAEGU_SUSEONG_CANDIDATE_AUDIT",
    "DAEGU_SUSEONG_EXCLUDED_SCOPE",
    "DAEGU_SUSEONG_DISCOVERY_AUDIT",
    "DAEGU_SUSEONG_MANAGED_MAX_RESPONSE_BYTES",
    "DAEGU_SUSEONG_LEDGERS",
    "DaeguSuseongContractError",
    "is_daegu_suseong_education_target",
    "is_target",
    "daegu_suseong_list_url",
    "daegu_suseong_detail_url",
    "daegu_suseong_info_list_url",
    "daegu_suseong_info_detail_url",
    "collect_daegu_suseong_education",
    "daegu_suseong_session_factory",
    "collect",
]
