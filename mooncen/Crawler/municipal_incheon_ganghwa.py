"""Atomic collector for Ganghwa-gun's official integrated education ledger.

The registered target is the lifelong-learning landing page, while the
course-of-record is the linked ``lecture.do?act=list`` catalogue.  The same
catalogue is also rendered below the 읍·면 site; that surface is a duplicate,
not another owner.  Incheon metropolitan reservations, Ganghwa libraries and
the Ganghwa Happiness Center keep separate ledgers and are deliberately not
traversed here.

Every advertised archive page is read because the site has no reliable
current-only filter.  The server clamps a request beyond the final page back
to the final page, so that exact clamp is used as the pagination sentinel.
The first and final pages are then fetched again before all rows whose
education end date has not passed are validated against their detail pages.

The detail page has no audited application control.  Any future form or
application/reservation link inside the course detail therefore fails closed;
it is never followed.  Generic test, sample, notice and placeholder rows also
fail closed instead of being silently published or broadly discarded.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import hashlib
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


GANGHWA_PROVIDER = "MUNI_WWW_GANGHWA_GO_KR_E1374F0C"
GANGHWA_CANONICAL_CANDIDATE_ID = "MUNI_IR_C5D5D85A5F6F"
GANGHWA_REGISTERED_CANDIDATE_ID = "MUNI_IR_E7829E889C7E"
GANGHWA_DONG_DUPLICATE_CANDIDATE_ID = "MUNI_IR_4227AC8BDB35"
GANGHWA_MUNICIPALITY_CODE = "2871000000"
GANGHWA_MUNICIPALITY_NAME = "인천광역시 강화군"

GANGHWA_HOST = "www.ganghwa.go.kr"
GANGHWA_LIST_PATH = "/open_content/main/lecture/lecture.do"
GANGHWA_CANONICAL_URL = (
    f"https://{GANGHWA_HOST}{GANGHWA_LIST_PATH}?act=list"
)
GANGHWA_REGISTERED_URL = (
    f"https://{GANGHWA_HOST}/open_content/main/part/job/lifelong.jsp"
)
GANGHWA_DONG_DUPLICATE_URL = (
    f"https://{GANGHWA_HOST}/open_content/dong/lecture/lecture.do?act=list"
)
GANGHWA_URL = GANGHWA_CANONICAL_URL
GANGHWA_PAGE_SIZE = 10
GANGHWA_FETCH_ATTEMPTS = 3
GANGHWA_MAX_WORKERS = 8
GANGHWA_MAX_HTML_BYTES = 2_000_000
GANGHWA_PARSER = (
    "ganghwa_official_integrated_education_all_pages+exact_final_page_clamp+"
    "stable_boundaries+all_current_safe_details+official_institutions+"
    "zero_application_controls+zero_test_notice_rows+semantic_duplicate_zero"
)
GANGHWA_OWNERSHIP_SCOPE = (
    "ganghwa_official_integrated_education_catalogue_only"
)


class GanghwaContractError(ValueError):
    """Raised when the audited Ganghwa source contract changes."""


GANGHWA_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    GANGHWA_CANONICAL_CANDIDATE_ID: {
        "decision": "canonical_complete_integrated_education_ledger",
        "provider": GANGHWA_PROVIDER,
        "url": GANGHWA_CANONICAL_URL,
        "owner": GANGHWA_PROVIDER,
    },
    GANGHWA_REGISTERED_CANDIDATE_ID: {
        "decision": "registered_lifelong_landing_alias_to_canonical_ledger",
        "provider": GANGHWA_PROVIDER,
        "url": GANGHWA_REGISTERED_URL,
        "owner": GANGHWA_PROVIDER,
    },
    GANGHWA_DONG_DUPLICATE_CANDIDATE_ID: {
        "decision": "duplicate_dong_surface_same_lecture_database",
        "provider": GANGHWA_PROVIDER,
        "url": GANGHWA_DONG_DUPLICATE_URL,
        "owner": GANGHWA_PROVIDER,
    },
    "MUNI_IR_BEC1AAE9DDEA": {
        "decision": "official_navigation_shell_not_course_ledger",
        "provider": "MUNI_WWW_GANGHWA_GO_KR_40F3B480",
        "url": "https://www.ganghwa.go.kr/",
        "owner": "MUNI_WWW_GANGHWA_GO_KR_40F3B480",
    },
    "MUNI_IR_6FC6F8469CA1": {
        "decision": "separate_incheon_metropolitan_reservation_owner",
        "provider": "INCHEON_RESERVATION",
        "url": "https://www.incheon.go.kr/res/",
        "owner": "INCHEON_RESERVATION",
    },
    "MUNI_IR_A876988923EE": {
        "decision": "separate_ganghwa_library_platform_three_registered_branches",
        "provider": "CULTURE_PUBLIC_LIBRARY_7B22F74689",
        "url": "https://lib.ganghwa.go.kr",
        "owner": "CULTURE_PUBLIC_LIBRARY_7B22F74689",
        "registered_branch_owners": (
            "CULTURE_PUBLIC_LIBRARY_7B22F74689",
            "CULTURE_PUBLIC_LIBRARY_E1BD0F310A",
            "CULTURE_PUBLIC_LIBRARY_9DCED00F22",
        ),
    },
    "MUNI_IR_AE64514DC9DF": {
        "decision": "separate_unregistered_ganghwa_happiness_center_navigation",
        "provider": "",
        "url": "https://www.ghhappy.or.kr/program/information.jsp",
        "owner": "unregistered_external_ganghwa_happiness_center",
    },
    "MUNI_IR_C89B11F02457": {
        "decision": "separate_happiness_center_current_programme_ledger",
        "provider": "",
        "url": "https://www.ghhappy.or.kr/program/programInfoList.do?prgmdiv=lifelong",
        "owner": "unregistered_external_ganghwa_happiness_center",
        "official_name": "강화군 행복센터",
    },
    "MUNI_IR_8322011D6C75": {
        "decision": "separate_ganghwa_library_platform_canonical",
        "provider": "CULTURE_PUBLIC_LIBRARY_7B22F74689",
        "url": "https://lib.ganghwa.go.kr/front/",
        "owner": "CULTURE_PUBLIC_LIBRARY_7B22F74689",
        "registered_branch_owners": (
            "CULTURE_PUBLIC_LIBRARY_7B22F74689",
            "CULTURE_PUBLIC_LIBRARY_E1BD0F310A",
            "CULTURE_PUBLIC_LIBRARY_9DCED00F22",
        ),
    },
    "MUNI_IR_F8BF5F20A278": {
        "decision": "separate_ganghwa_facilities_corporation_class_ledger",
        "provider": "",
        "url": "https://www.ghss.or.kr/user/reserv/class/classList.do",
        "owner": "unregistered_ganghwa_facilities_corporation",
        "official_name": "강화군시설관리공단",
    },
    "MUNI_IR_DB8867E3B586": {
        "decision": "separate_youth_culture_house_programme_ledger",
        "provider": "",
        "url": "https://www.ghss.or.kr/user/welfare/youthCulture/programList.do",
        "owner": "unregistered_ganghwa_facilities_corporation",
    },
    "MUNI_IR_C644B175C0E2": {
        "decision": "separate_field_trip_ledger_with_one_exact_test_row",
        "provider": "",
        "url": "https://www.ghss.or.kr/user/reserv/fieldTrip/fieldTrip.do",
        "owner": "unregistered_ganghwa_facilities_corporation",
        "audited_test_row": {
            "identity": "field_idx:1",
            "title": "웹접근성테스트",
            "detail_url": "https://www.ghss.or.kr/user/reserv/fieldTrip/fieldView.do?idx=1",
            "detail_candidate_id": "MUNI_IR_BE88BA1E0DB9",
        },
    },
    "MUNI_IR_72C2099B0528": {
        "decision": "separate_facilities_reservation_owner_wrong_category",
        "provider": "",
        "url": "https://www.ghss.or.kr/ttreserve",
        "owner": "unregistered_ganghwa_facilities_corporation",
    },
    "MUNI_IR_56A9C18BFC2F": {
        "decision": "separate_incheon_education_office_lifelong_owner",
        "provider": "",
        "url": "https://ganghwa.ice.go.kr/mini/lifelong/program/program.asp",
        "owner": "unregistered_ganghwa_education_support_office",
        "official_name": "강화교육지원청 평생학습관",
    },
    "MUNI_IR_10002E959250": {
        "decision": "separate_ganghwa_museum_education_owner",
        "provider": "CULTURE_MUSEUM_2240F05623",
        "url": "https://www.ganghwa.go.kr/open_content/museum_history/edu/schedule.jsp",
        "owner": "CULTURE_MUSEUM_2240F05623",
        "registered_related_owners": (
            "CULTURE_MUSEUM_2240F05623",
            "CULTURE_MUSEUM_B561B30859",
            "CULTURE_MUSEUM_5053D7342E",
        ),
    },
    "MUNI_IR_F63CD5BDB9CF": {
        "decision": "separate_health_centre_education_calendar_owner",
        "provider": "",
        "url": "https://www.ganghwa.go.kr/open_content/clinic/",
        "owner": "unregistered_ganghwa_health_centre_calendar",
    },
    "MUNI_IR_55017277A32F": {
        "decision": "separate_agriculture_information_microsite_not_ledger_alias",
        "provider": "",
        "url": "https://www.ganghwa.go.kr/open_content/agriculture/",
        "owner": "unregistered_ganghwa_agriculture_information",
    },
    "MUNI_IR_456BBE15FD9D": {
        "decision": "separate_happiness_center_kids_cafe_facility_owner",
        "provider": "",
        "url": "https://reserv.tfunkorea.co.kr/sale/siteDetail/GHHAPPY?indivimall=ghhappy",
        "owner": "unregistered_happiness_center_kids_cafe",
    },
}

GANGHWA_EXCLUDED_SOURCE_AUDIT: tuple[Mapping[str, str], ...] = (
    {
        "url": GANGHWA_DONG_DUPLICATE_URL,
        "reason": "same lecture identities rendered through the 읍면 site",
    },
    {
        "url": "https://www.incheon.go.kr/res/",
        "reason": "metropolitan reservation aggregator has a separate owner",
    },
    {
        "url": "https://lib.ganghwa.go.kr",
        "reason": "county libraries have their own programme platform",
    },
    {
        "url": "https://www.ghhappy.or.kr/program/information.jsp",
        "reason": "Happiness Center maintains a separate detailed ledger",
    },
    {
        "url": "https://www.ghss.or.kr/user/reserv/class/classList.do",
        "reason": "facilities corporation has its own online class ledger",
    },
    {
        "url": "https://ganghwa.ice.go.kr/mini/lifelong/program/program.asp",
        "reason": "education support office is not operated by Ganghwa-gun",
    },
    {
        "url": "https://www.ganghwa.go.kr/open_content/museum_history/edu/schedule.jsp",
        "reason": "registered museums are separate facility owners",
    },
)

# These names are the exact 교육기관 labels confirmed on all 315 current/future
# detail pages on 2026-07-22.  A new label requires an ownership/name review.
GANGHWA_CURRENT_INSTITUTIONS = frozenset(
    {
        "강화문화원",
        "장애인복지관",
        "교동향교",
        "강화향교",
        "보건소",
        "강화군 건강가정·다문화가족지원센터",
        "강화군청",
        "농업기술센터",
        "청소년문화의집",
        "강화군노인문화센터",
        "군립도서관",
        "노인복지관",
        "강화군행복센터",
        "읍면사무소(강화읍)",
        "읍면사무소(선원면)",
        "읍면사무소(불은면)",
        "읍면사무소(길상면)",
        "읍면사무소(화도면)",
        "읍면사무소(양도면)",
        "읍면사무소(내가면)",
        "읍면사무소(하점면)",
        "읍면사무소(양사면)",
        "읍면사무소(송해면)",
        "읍면사무소(교동면)",
        "읍면사무소(삼산면)",
    }
)

# Sixteen historical rows have an exact, source-authored reversed reception
# interval.  They are not current and are never detailed/published, but the
# archive still has to be traversed.  Identity, title, institution and both
# endpoints are bound so no new defect (or reused identity) is tolerated.
GANGHWA_AUDITED_REVERSED_APPLICATION_PERIODS: Mapping[str, Mapping[str, str]] = {
    "2958": {"title": "난타", "branch": "읍면사무소(하점면)", "start": "2025-01-02", "end": "2024-01-10"},
    "2960": {"title": "노래교실", "branch": "읍면사무소(하점면)", "start": "2025-01-02", "end": "2024-01-10"},
    "2961": {"title": "농악", "branch": "읍면사무소(하점면)", "start": "2025-01-02", "end": "2024-01-10"},
    "2957": {"title": "라인댄스", "branch": "읍면사무소(하점면)", "start": "2025-01-02", "end": "2024-01-10"},
    "2962": {"title": "스포츠 댄스", "branch": "읍면사무소(하점면)", "start": "2025-01-02", "end": "2024-01-10"},
    "2955": {"title": "요가교실", "branch": "읍면사무소(하점면)", "start": "2025-01-02", "end": "2024-01-10"},
    "2959": {"title": "탁구", "branch": "읍면사무소(하점면)", "start": "2025-01-02", "end": "2024-01-10"},
    "2956": {"title": "트롯댄스장구", "branch": "읍면사무소(하점면)", "start": "2025-01-02", "end": "2024-01-10"},
    "1654": {"title": "사진 및 동영상", "branch": "강화군청", "start": "2019-10-09", "end": "2019-10-02"},
    "1655": {"title": "사진편집 포토샵", "branch": "강화군청", "start": "2019-10-09", "end": "2019-10-02"},
    "1656": {"title": "생활문서만들기", "branch": "강화군청", "start": "2019-10-09", "end": "2019-10-02"},
    "1653": {"title": "컴퓨터 기초", "branch": "강화군청", "start": "2019-10-09", "end": "2019-10-02"},
    "1701": {"title": "블로그인터넷판매", "branch": "강화군청", "start": "2019-09-09", "end": "2019-09-02"},
    "1700": {"title": "사진 및 동영상", "branch": "강화군청", "start": "2019-09-09", "end": "2019-09-02"},
    "1698": {"title": "컴퓨터 기초", "branch": "강화군청", "start": "2019-09-09", "end": "2019-09-02"},
    "1699": {"title": "파워포인트", "branch": "강화군청", "start": "2019-09-09", "end": "2019-09-02"},
}

# Six live 삼산면 rows explicitly publish ``~`` and no reception method.  They
# remain valid education information, but are never advertised as reservable.
GANGHWA_AUDITED_NO_APPLICATION_PERIODS: Mapping[str, Mapping[str, str]] = {
    "3363": {"title": "건강댄스", "branch": "읍면사무소(삼산면)"},
    "3365": {"title": "드럼교실", "branch": "읍면사무소(삼산면)"},
    "3359": {"title": "버들장구(댄스트롯 장구)", "branch": "읍면사무소(삼산면)"},
    "3361": {"title": "악기교실", "branch": "읍면사무소(삼산면)"},
    "3364": {"title": "요가교실", "branch": "읍면사무소(삼산면)"},
    "3367": {"title": "탁구교실", "branch": "읍면사무소(삼산면)"},
}

GANGHWA_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "source_total": 1650,
    "source_pages": 165,
    "page_size": 10,
    "sentinel_page": 166,
    "sentinel_kind": "exact_final_page_clamp",
    "required_list_requests": 168,
    "current_source_count": 315,
    "detail_pages": 315,
    "complete_network_requests": 483,
    "current_institution_count": 25,
    "current_venue_count": 77,
    "source_identity_duplicates": 0,
    "current_semantic_duplicates": 0,
    "test_or_notice_rows": 0,
    "application_controls": 0,
    "audited_reversed_application_periods": 16,
    "current_truncated_list_schedules": 9,
    "current_no_application_periods": 6,
    "current_source_status_counts": {
        "[접수중] [교육중]": 76,
        "[접수마감] [교육중]": 210,
        "[접수예정] [강좌준비]": 21,
        "[접수마감] [강좌준비]": 8,
    },
}


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Parser = Callable[[BeautifulSoup, str], Any]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_SUMMARY_RE = re.compile(
    r"전체\s*건수\s*([\d,]+)\s*건\s*,?\s*현재페이지\s*:\s*"
    r"([\d,]+)\s*/\s*([\d,]+)"
)
_FULL_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)"
)
_SHORT_RANGE_RE = re.compile(
    r"(?<!\d)(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*~\s*"
    r"(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)"
)
_GENERIC_NON_COURSE_RE = re.compile(
    r"^(?:(?:test|sample|테스트|샘플)(?:\s*[-_#]?\s*\d+)?|"
    r"(?:교육\s*)?(?:안내|공지)(?:사항)?|(?:강좌|교육)?\s*(?:등록|없음))$",
    re.IGNORECASE,
)
_ADDRESS_RE = re.compile(r"^(.*?)\((강화군\s+.+)\)$")

_LIST_TITLE = "교육종합정보 목록 | 강화군청>분야별정보>일자리·교육>평생교육정보"
_LIST_HEADERS = ("강좌명", "교육기관", "수강료", "접수기간/교육기간", "진행상태")
_DETAIL_LABELS = (
    "분야",
    "교육기관",
    "교육장소",
    "교육대상",
    "수강료",
    "접수기간",
    "교육기간",
    "교육요일/시간",
    "접수방법",
    "모집인원",
    "문의전화",
    "교재및 재료비",
)
_STATUS_MAP: Mapping[str, str] = {
    "[접수중] [교육중]": "OPEN",
    "[접수마감] [교육중]": "CLOSED",
    "[접수예정] [강좌준비]": "SCHEDULED",
    "[접수마감] [강좌준비]": "CLOSED",
    "[접수마감] [교육종료]": "CLOSED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).casefold()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise GanghwaContractError(f"{label} may not be boolean")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GanghwaContractError(f"invalid {label}") from exc
    if parsed < 1:
        raise GanghwaContractError(f"{label} must be positive")
    return parsed


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _normal_path(value: Any) -> str:
    return re.sub(r"/{2,}", "/", str(value or "/"))


def _compare_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
    ):
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return (
        f"https://{parsed.hostname.rstrip('.').lower()}{_normal_path(parsed.path)}"
        + (f"?{query}" if query else "")
    )


def is_ganghwa_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != GANGHWA_PROVIDER:
        return False
    candidate = _clean(_target_value(target, "candidate_id"))
    if candidate and candidate not in {
        GANGHWA_CANONICAL_CANDIDATE_ID,
        GANGHWA_REGISTERED_CANDIDATE_ID,
    }:
        return False
    compared = _compare_url(_target_value(target, "url"))
    return compared in {
        _compare_url(GANGHWA_CANONICAL_URL),
        _compare_url(GANGHWA_REGISTERED_URL),
    }


is_target = is_ganghwa_education_target


def ganghwa_list_url(page: int = 1) -> str:
    current = _positive_int(page, "list page")
    return f"https://{GANGHWA_HOST}{GANGHWA_LIST_PATH}?" + urlencode(
        (("act", "list"), ("nowPage", current))
    )


def ganghwa_detail_url(identity: Any) -> str:
    token = _clean(identity)
    if not _IDENTITY_RE.fullmatch(token):
        raise GanghwaContractError("invalid lecture identity")
    return f"https://{GANGHWA_HOST}{GANGHWA_LIST_PATH}?" + urlencode(
        (("act", "detail"), ("lecture_seq", token))
    )


def canonical_ganghwa_detail_identity(
    current_url: str,
    value: Any,
    *,
    expected_page: Optional[int] = None,
) -> str:
    resolved = urlparse(urljoin(current_url, _clean(value)))
    if (
        resolved.scheme.lower() != "https"
        or resolved.hostname != GANGHWA_HOST
        or resolved.path != GANGHWA_LIST_PATH
        or resolved.port is not None
        or resolved.username
        or resolved.password
        or resolved.params
        or resolved.fragment
    ):
        return ""
    query = parse_qs(resolved.query, keep_blank_values=True)
    if set(query) != {"act", "lecture_seq", "nowPage"}:
        return ""
    if query.get("act") != ["detail"]:
        return ""
    identity = _clean((query.get("lecture_seq") or [""])[0])
    page = _clean((query.get("nowPage") or [""])[0])
    if not _IDENTITY_RE.fullmatch(identity) or not _IDENTITY_RE.fullmatch(page):
        return ""
    if expected_page is not None and int(page) != expected_page:
        return ""
    return identity


def _full_date_range(value: Any, label: str) -> tuple[date, date]:
    matches = _FULL_DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise GanghwaContractError(f"{label} must contain exactly two full dates")
    try:
        start, end = (
            date(int(year), int(month), int(day))
            for year, month, day in matches
        )
    except ValueError as exc:
        raise GanghwaContractError(f"{label} contains an invalid date") from exc
    if start > end:
        raise GanghwaContractError(f"{label} date range is reversed")
    return start, end


def _list_application_ranges(
    value: Any,
    *,
    identity: str,
    title: str,
    branch: str,
) -> tuple[tuple[tuple[date, date], ...], bool]:
    result: list[tuple[date, date]] = []
    reversed_found = False
    for match in _SHORT_RANGE_RE.findall(_clean(value)):
        try:
            start = date(2000 + int(match[0]), int(match[1]), int(match[2]))
            end = date(2000 + int(match[3]), int(match[4]), int(match[5]))
        except ValueError as exc:
            raise GanghwaContractError("list application period has an invalid date") from exc
        if start > end:
            expected = GANGHWA_AUDITED_REVERSED_APPLICATION_PERIODS.get(identity)
            actual = {
                "title": title,
                "branch": branch,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
            if expected != actual:
                raise GanghwaContractError(
                    f"unaudited reversed list application period for {identity}"
                )
            reversed_found = True
        result.append((start, end))
    return tuple(result), reversed_found


def _summary(soup: BeautifulSoup) -> tuple[int, int, int]:
    candidates = [
        _text(node)
        for node in soup.select("#contents p.right, #contents .right")
        if _SUMMARY_RE.search(_text(node))
    ]
    if len(candidates) != 1:
        raise GanghwaContractError(
            f"expected one list total/page summary, found {len(candidates)}"
        )
    match = _SUMMARY_RE.search(candidates[0])
    assert match is not None
    return tuple(int(value.replace(",", "")) for value in match.groups())  # type: ignore[return-value]


def _validate_list_shell(soup: BeautifulSoup) -> None:
    title = _text(soup.title)
    if title != _LIST_TITLE:
        raise GanghwaContractError("list page title changed")
    forms = [
        form
        for form in soup.select("form")
        if urlparse(urljoin(GANGHWA_CANONICAL_URL, _clean(form.get("action")))).path
        == GANGHWA_LIST_PATH
        and _clean(form.get("method")).lower() == "get"
    ]
    if len(forms) != 1:
        raise GanghwaContractError("canonical list search form changed")
    act = forms[0].select("input[name='act'][value='list']")
    if len(act) != 1:
        raise GanghwaContractError("canonical list form lost act=list")
    tables = soup.select("#contents table")
    matching = [
        table
        for table in tables
        if tuple(_text(node) for node in table.select("thead th")) == _LIST_HEADERS
    ]
    if len(matching) != 1:
        raise GanghwaContractError("course list table/header contract changed")


def _list_status(raw: str, start: date, end: date, cutoff: date) -> str:
    if raw not in _STATUS_MAP:
        raise GanghwaContractError(f"unknown course status {raw!r}")
    education = raw.split()[-1]
    expected = "[강좌준비]" if start > cutoff else "[교육중]" if end >= cutoff else "[교육종료]"
    if education != expected:
        raise GanghwaContractError(
            f"education state {education!r} disagrees with its dates"
        )
    return _STATUS_MAP[raw]


def _parse_list_row(
    target: Any,
    row: Tag,
    *,
    reported_page: int,
    expected_link_page: int,
    cutoff: date,
) -> dict[str, Any]:
    cells = row.find_all(["th", "td"], recursive=False)
    if len(cells) != len(_LIST_HEADERS):
        text = _clean(row.get_text(" ", strip=True))
        raise GanghwaContractError(f"non-course/instruction table row encountered: {text!r}")
    title_cell = cells[0]
    link = title_cell.select_one("p.lecture > a[href]")
    schedule_node = title_cell.select_one("p.time")
    if link is None or schedule_node is None:
        raise GanghwaContractError("course row title/schedule structure changed")
    identity = canonical_ganghwa_detail_identity(
        ganghwa_list_url(expected_link_page),
        link.get("href"),
        expected_page=expected_link_page,
    )
    if not identity:
        raise GanghwaContractError(
            "course detail link escaped its page/identity: "
            f"reported_page={reported_page} expected_link_page={expected_link_page} "
            f"href={_clean(link.get('href'))!r}"
        )
    title = _text(link)
    if not title:
        raise GanghwaContractError("course row has a blank title")
    if _GENERIC_NON_COURSE_RE.fullmatch(title):
        raise GanghwaContractError(
            f"unaudited test/information row encountered: {identity}:{title}"
        )
    schedule = _text(schedule_node)
    if len(schedule) < 2 or not schedule.startswith("(") or not schedule.endswith(")"):
        raise GanghwaContractError("list schedule wrapper changed")
    schedule = _clean(schedule[1:-1])
    branch = _text(cells[1])
    fee = _text(cells[2])
    dates = _text(cells[3])
    raw_status = _text(cells[4])
    if not branch or not fee or not dates:
        raise GanghwaContractError("course row has a blank required field")
    marker = re.search(r"(?:^|\s)-\s*교육\s*:\s*(.+)$", dates)
    if marker is None:
        raise GanghwaContractError("list row lost its education period marker")
    start, end = _full_date_range(marker.group(1), "list education period")
    status = _list_status(raw_status, start, end, cutoff)
    application_ranges, historical_reversed_application_period = (
        _list_application_ranges(
            dates[: marker.start()],
            identity=identity,
            title=title,
            branch=branch,
        )
    )
    return {
        "provider": _clean(_target_value(target, "provider")),
        "provider_course_id": f"{GANGHWA_PROVIDER}:lecture:{identity}",
        "title": title,
        "branch": branch,
        "branch_code": "GANGHWA_" + hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper(),
        "category": "평생교육",
        "raw_url": ganghwa_detail_url(identity),
        "status": status,
        "reservation_available": status in {"OPEN", "SCHEDULED"},
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "apply_period": "",
        "schedule_raw": schedule,
        "fee": fee,
        "venue_name": branch,
        "program_type": "강좌",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html+detail_html",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "raw_fields": {
            "source_identity": identity,
            "source_status": raw_status,
            "source_branch": branch,
            "list_page": reported_page,
            "list_date_text": dates,
            "application_ranges": tuple(
                (start.isoformat(), end.isoformat())
                for start, end in application_ranges
            ),
            "parser": "ganghwa_integrated_education_list",
            "historical_reversed_application_period": historical_reversed_application_period,
        },
        "_identity": identity,
        "_source_status": raw_status,
        "_application_ranges": application_ranges,
    }


def _parse_list_page(
    target: Any,
    soup: BeautifulSoup,
    *,
    requested_page: int,
    cutoff: date,
    expected_total: Optional[int] = None,
    allow_final_clamp: bool = False,
) -> tuple[list[dict[str, Any]], int, int]:
    _validate_list_shell(soup)
    total, reported_page, reported_last = _summary(soup)
    expected_last = math.ceil(total / GANGHWA_PAGE_SIZE) if total else 0
    if expected_total is not None and total != expected_total:
        raise GanghwaContractError("advertised source total changed during scan")
    if reported_last != expected_last:
        raise GanghwaContractError("advertised last page disagrees with total")
    if total < 1 or reported_last < 1:
        raise GanghwaContractError("audited integrated archive unexpectedly became empty")
    if allow_final_clamp:
        if requested_page != reported_last + 1 or reported_page != reported_last:
            raise GanghwaContractError("beyond-final page did not clamp exactly")
    elif reported_page != requested_page:
        raise GanghwaContractError("requested data page was silently clamped")
    table = next(
        table
        for table in soup.select("#contents table")
        if tuple(_text(node) for node in table.select("thead th")) == _LIST_HEADERS
    )
    body_rows = table.select("tbody > tr")
    rows = [
        _parse_list_row(
            target,
            row,
            reported_page=reported_page,
            expected_link_page=requested_page,
            cutoff=cutoff,
        )
        for row in body_rows
    ]
    if len(rows) > GANGHWA_PAGE_SIZE:
        raise GanghwaContractError("course list page exceeds audited page size")
    return rows, total, reported_last


def _source_signature(rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("_identity")),
            _clean(row.get("title")),
            _clean(row.get("branch")),
            _clean(row.get("_source_status")),
            _clean(row.get("period")),
            _clean(row.get("schedule_raw")),
        )
        for row in rows
    )


def _detail_pairs(root: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    labels: list[str] = []
    for item in root.select(":scope > dl.list"):
        key_node = item.find("dt", recursive=False)
        value_node = item.find("dd", recursive=False)
        key = _text(key_node)
        value = _text(value_node)
        if not key or value_node is None or key in pairs:
            raise GanghwaContractError("detail definition list changed")
        labels.append(key)
        pairs[key] = value
    if tuple(labels) != _DETAIL_LABELS:
        raise GanghwaContractError(
            f"detail field schema changed: {tuple(labels)!r}"
        )
    for key in ("분야", "교육기관", "교육장소", "교육대상", "수강료", "접수기간", "교육기간", "교육요일/시간", "모집인원"):
        if not pairs[key]:
            raise GanghwaContractError(f"detail required field {key!r} is blank")
    return pairs


def _capacity(value: Any) -> int:
    match = re.fullmatch(r"\s*([\d,]+)\s*명\s*", _clean(value))
    if not match:
        raise GanghwaContractError("detail capacity format changed")
    return int(match.group(1).replace(",", ""))


def _venue(value: Any) -> tuple[str, str]:
    text = _clean(value)
    match = _ADDRESS_RE.fullmatch(text)
    if match:
        name, address = map(_clean, match.groups())
        return name or text, address
    return text, ""


def _validate_zero_application_controls(root: Tag) -> None:
    if root.select("form"):
        raise GanghwaContractError("unaudited application form appeared in course detail")
    controls: list[Tag] = []
    for anchor in root.select("a[href], a[onclick]"):
        text = _text(anchor)
        raw = " ".join((_clean(anchor.get("href")), _clean(anchor.get("onclick"))))
        if (
            any(token in text for token in ("신청", "접수", "예약"))
            or re.search(r"apply|request|reserve|receipt", raw, re.IGNORECASE)
        ):
            controls.append(anchor)
    if controls:
        raise GanghwaContractError("unaudited application control appeared in course detail")


def _parse_detail(
    soup: BeautifulSoup,
    final_url: str,
    listed: Mapping[str, Any],
    cutoff: date,
) -> dict[str, Any]:
    identity = _clean(listed.get("_identity"))
    if _compare_url(final_url) != _compare_url(ganghwa_detail_url(identity)):
        raise GanghwaContractError("detail response URL changed")
    roots = soup.select("#detail_con > .board_view")
    if len(roots) != 1:
        raise GanghwaContractError("expected one course detail root")
    root = roots[0]
    title_nodes = root.select(":scope > .edu_title > .tit")
    state_nodes = root.select(":scope > .edu_title > .state")
    if len(title_nodes) != 1 or len(state_nodes) != 1:
        raise GanghwaContractError("detail title/status structure changed")
    if _text(title_nodes[0]) != _clean(listed.get("title")):
        raise GanghwaContractError("detail title does not match list identity")
    source_status = _text(state_nodes[0])
    if source_status != _clean(listed.get("_source_status")):
        raise GanghwaContractError("detail status does not match list identity")
    pairs = _detail_pairs(root)
    branch = pairs["교육기관"]
    if branch != _clean(listed.get("branch")):
        raise GanghwaContractError("detail institution does not match list identity")
    if branch not in GANGHWA_CURRENT_INSTITUTIONS:
        raise GanghwaContractError(f"unaudited current institution name {branch!r}")
    start, end = _full_date_range(pairs["교육기간"], "detail education period")
    if (start.isoformat(), end.isoformat()) != (
        _clean(listed.get("start_date")),
        _clean(listed.get("end_date")),
    ):
        raise GanghwaContractError("detail education period disagrees with list")
    _list_status(source_status, start, end, cutoff)
    list_schedule = _clean(listed.get("schedule_raw"))
    detail_schedule = pairs["교육요일/시간"]
    list_schedule_truncated = list_schedule.endswith("...")
    if list_schedule_truncated:
        prefix = _clean(list_schedule[:-3])
        if not prefix or not detail_schedule.startswith(prefix):
            raise GanghwaContractError(
                "detail schedule disagrees with truncated list prefix"
            )
    elif detail_schedule != list_schedule:
        raise GanghwaContractError("detail schedule disagrees with list")
    if pairs["수강료"] != _clean(listed.get("fee")):
        raise GanghwaContractError("detail fee disagrees with list")
    application_ranges = tuple(listed.get("_application_ranges") or ())
    no_application_period = pairs["접수기간"] == "~"
    if no_application_period:
        expected = GANGHWA_AUDITED_NO_APPLICATION_PERIODS.get(identity)
        actual = {"title": _clean(listed.get("title")), "branch": branch}
        if (
            expected != actual
            or application_ranges
            or pairs["접수방법"]
            or _STATUS_MAP[source_status] != "CLOSED"
        ):
            raise GanghwaContractError(
                f"unaudited missing application period for {identity}"
            )
        apply_start = apply_end = None
    else:
        apply_start, apply_end = _full_date_range(
            pairs["접수기간"], "detail application period"
        )
        if application_ranges and any(
            item != (apply_start, apply_end) for item in application_ranges
        ):
            raise GanghwaContractError("detail application period disagrees with list")
    _validate_zero_application_controls(root)
    venue_name, venue_address = _venue(pairs["교육장소"])
    raw_fields = dict(listed.get("raw_fields") or {})
    raw_fields.update(
        {
            "parser": "ganghwa_integrated_education_list+safe_detail",
            "detail_field_labels": _DETAIL_LABELS,
            "source_status": source_status,
            "material_fee": pairs["교재및 재료비"],
            "application_control_count": 0,
            "list_schedule_truncated": list_schedule_truncated,
            "audited_no_application_period": no_application_period,
        }
    )
    enriched = {
        key: value
        for key, value in listed.items()
        if not key.startswith("_") and key != "raw_fields"
    }
    enriched.update(
        {
            "branch": branch,
            "category": pairs["분야"],
            "status": _STATUS_MAP[source_status],
            "application_url": "",
            "application_type": (
                "OFFLINE_RESERVATION"
                if _STATUS_MAP[source_status] == "OPEN"
                and pairs["접수방법"] in {"방문", "전화", "방문 전화"}
                else "SCHEDULED_INFORMATION"
                if _STATUS_MAP[source_status] == "SCHEDULED"
                else "INFORMATION_ONLY"
            ),
            "apply_period": (
                f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
                if apply_start is not None and apply_end is not None
                else ""
            ),
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "schedule_raw": detail_schedule,
            "target": pairs["교육대상"],
            "fee": pairs["수강료"],
            "capacity_total": _capacity(pairs["모집인원"]),
            "room": pairs["교육장소"],
            "venue_name": venue_name,
            "venue_address": venue_address,
            "application_method_raw": pairs["접수방법"],
            "phone": pairs["문의전화"],
            "raw_fields": raw_fields,
        }
    )
    return _clean_row(enriched)


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _compact(row.get("title")),
        _compact(row.get("branch")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _compact(row.get("schedule_raw")),
    )


def _clean_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if not key.startswith("_") and value not in (None, "", [], {}, ())
    }


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MoonCenBot/1.0; public-course-audit)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    return response


def _close_quietly(value: Any) -> None:
    try:
        value.close()
    except Exception:
        pass


class _Runner:
    def __init__(
        self,
        *,
        timeout: int,
        maximum: int,
        fetcher: Fetcher,
        session_factory: SessionFactory,
        sleeper: Sleeper,
    ) -> None:
        self.timeout = timeout
        self.maximum = maximum
        self.fetcher = fetcher
        self.session_factory = session_factory
        self.sleeper = sleeper
        self.requests = 0
        self.retries = 0
        self.sessions_created = 0
        self._lock = threading.Lock()
        self._local = threading.local()
        self._sessions: list[Any] = []

    def _session(self) -> Any:
        current = getattr(self._local, "session", None)
        if current is not None:
            return current
        current = self.session_factory()
        self._local.session = current
        with self._lock:
            self._sessions.append(current)
            self.sessions_created += 1
        return current

    def _claim(self) -> None:
        with self._lock:
            if self.requests >= self.maximum:
                raise GanghwaContractError("network request cap exceeded")
            self.requests += 1

    def get(self, url: str, parser: Parser) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(GANGHWA_FETCH_ATTEMPTS):
            self._claim()
            try:
                response = self.fetcher(self._session(), url, self.timeout)
                if (
                    isinstance(response, tuple)
                    and len(response) == 2
                    and isinstance(response[0], BeautifulSoup)
                ):
                    soup, final_url = response
                elif isinstance(response, BeautifulSoup):
                    soup, final_url = response, url
                else:
                    status = int(getattr(response, "status_code", 200) or 0)
                    if status != 200:
                        raise RuntimeError(f"HTTP {status}")
                    final_url = _clean(getattr(response, "url", url)) or url
                    content = bytes(getattr(response, "content", b"") or b"")
                    if not content:
                        content = str(getattr(response, "text", "") or "").encode("utf-8")
                    if not content:
                        raise RuntimeError("empty HTML response")
                    if len(content) > GANGHWA_MAX_HTML_BYTES:
                        raise GanghwaContractError("HTML response is too large")
                    soup = BeautifulSoup(content, "lxml")
                if _compare_url(final_url) != _compare_url(url):
                    raise GanghwaContractError("request response URL changed")
                return parser(soup, final_url)
            except GanghwaContractError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < GANGHWA_FETCH_ATTEMPTS:
                    with self._lock:
                        self.retries += 1
                    self.sleeper(0.35 * (attempt + 1))
        raise GanghwaContractError(
            f"failed source fetch after retries: {last_error}"
        ) from last_error

    def close(self) -> None:
        for current in self._sessions:
            _close_quietly(current)


def _parallel_fetch(
    runner: _Runner,
    jobs: Sequence[tuple[Any, str, Parser]],
    *,
    max_workers: int,
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(runner.get, url, parser): key
            for key, url, parser in jobs
        }
        for future in as_completed(futures):
            key = futures[future]
            result[key] = future.result()
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
        "candidate_id": GANGHWA_CANONICAL_CANDIDATE_ID,
        "registered_candidate_id": GANGHWA_REGISTERED_CANDIDATE_ID,
        "canonical_url": GANGHWA_CANONICAL_URL,
        "registered_url": GANGHWA_REGISTERED_URL,
        "ownership_scope": GANGHWA_OWNERSHIP_SCOPE,
        "owner_boundary_audit": dict(GANGHWA_OWNER_BOUNDARY_AUDIT),
        "excluded_source_audit": tuple(GANGHWA_EXCLUDED_SOURCE_AUDIT),
        "discovery_audit": dict(GANGHWA_DISCOVERY_AUDIT),
    }


def _scan_archive(
    target: Any,
    runner: _Runner,
    *,
    cutoff: date,
    max_pages: int,
    max_workers: int,
) -> tuple[list[dict[str, Any]], int, int]:
    first_rows, total, last = runner.get(
        ganghwa_list_url(1),
        lambda soup, _final: _parse_list_page(
            target, soup, requested_page=1, cutoff=cutoff
        ),
    )
    if last + 1 > max_pages:
        raise GanghwaContractError(
            f"sentinel page {last + 1} exceeds max_pages cap"
        )
    pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
    jobs: list[tuple[int, str, Parser]] = []
    for page in range(2, last + 1):
        jobs.append(
            (
                page,
                ganghwa_list_url(page),
                lambda soup, _final, page=page: _parse_list_page(
                    target,
                    soup,
                    requested_page=page,
                    cutoff=cutoff,
                    expected_total=total,
                )[0],
            )
        )
    pages.update(_parallel_fetch(runner, jobs, max_workers=max_workers))
    expected_last_size = total - GANGHWA_PAGE_SIZE * (last - 1)
    for page in range(1, last + 1):
        expected_size = GANGHWA_PAGE_SIZE if page < last else expected_last_size
        if len(pages[page]) != expected_size:
            raise GanghwaContractError(
                f"page {page} has {len(pages[page])} of {expected_size} expected rows"
            )
    rows = [row for page in range(1, last + 1) for row in pages[page]]
    if len(rows) != total:
        raise GanghwaContractError("complete page union disagrees with advertised total")
    identities = [_clean(row.get("_identity")) for row in rows]
    if len(identities) != len(set(identities)):
        raise GanghwaContractError("archive has duplicate lecture identities")

    sentinel_rows, sentinel_total, sentinel_last = runner.get(
        ganghwa_list_url(last + 1),
        lambda soup, _final: _parse_list_page(
            target,
            soup,
            requested_page=last + 1,
            cutoff=cutoff,
            expected_total=total,
            allow_final_clamp=True,
        ),
    )
    if sentinel_total != total or sentinel_last != last:
        raise GanghwaContractError("final-page clamp summary changed")
    if _source_signature(sentinel_rows) != _source_signature(pages[last]):
        raise GanghwaContractError("final-page clamp contents changed")

    boundary_jobs: list[tuple[str, str, Parser]] = [
        (
            "first",
            ganghwa_list_url(1),
            lambda soup, _final: _parse_list_page(
                target,
                soup,
                requested_page=1,
                cutoff=cutoff,
                expected_total=total,
            )[0],
        )
    ]
    if last > 1:
        boundary_jobs.append(
            (
                "last",
                ganghwa_list_url(last),
                lambda soup, _final: _parse_list_page(
                    target,
                    soup,
                    requested_page=last,
                    cutoff=cutoff,
                    expected_total=total,
                )[0],
            )
        )
    boundaries = _parallel_fetch(
        runner, boundary_jobs, max_workers=min(max_workers, len(boundary_jobs))
    )
    if _source_signature(boundaries["first"]) != _source_signature(pages[1]):
        raise GanghwaContractError("first page changed during stable recheck")
    if last > 1 and _source_signature(boundaries["last"]) != _source_signature(pages[last]):
        raise GanghwaContractError("last page changed during stable recheck")
    return rows, last, len(boundary_jobs)


def collect_incheon_ganghwa_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 200,
    detail_limit: int = 400,
    max_requests: int = 600,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = GANGHWA_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete, fail-closed current/future Ganghwa snapshot."""

    meta = _base_meta()
    if not is_ganghwa_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the canonical/registered Ganghwa owner"
        )
        return [], GANGHWA_PARSER, meta
    try:
        timeout = _positive_int(timeout, "timeout")
        max_pages = _positive_int(max_pages, "max_pages")
        detail_limit = _positive_int(detail_limit, "detail_limit")
        max_requests = _positive_int(max_requests, "max_requests")
        max_workers = _positive_int(max_workers, "max_workers")
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], GANGHWA_PARSER, meta

    runner = _Runner(
        timeout=timeout,
        maximum=max_requests,
        fetcher=fetcher or _default_fetcher,
        session_factory=session_factory or _default_session_factory,
        sleeper=sleeper,
    )
    try:
        source_rows, source_pages, stability_rechecks = _scan_archive(
            target,
            runner,
            cutoff=cutoff,
            max_pages=max_pages,
            max_workers=max_workers,
        )
        current_rows = [
            row
            for row in source_rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
        ]
        if len(current_rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise GanghwaContractError(
                f"detail_limit cap allows {detail_limit} of "
                f"{len(current_rows)} required details"
            )
        required_list_requests = source_pages + 1 + stability_rechecks
        required_requests = required_list_requests + len(current_rows)
        if required_requests > max_requests:
            meta["source_cap_reached"] = True
            raise GanghwaContractError(
                f"max_requests cap allows {max_requests} of {required_requests} "
                "required requests"
            )
        jobs: list[tuple[str, str, Parser]] = []
        for row in current_rows:
            identity = _clean(row.get("_identity"))
            jobs.append(
                (
                    identity,
                    ganghwa_detail_url(identity),
                    lambda soup, final, row=row: _parse_detail(
                        soup, final, row, cutoff
                    ),
                )
            )
        details = _parallel_fetch(runner, jobs, max_workers=max_workers)
        enriched = [
            details[_clean(row.get("_identity"))]
            for row in current_rows
        ]
        semantic = [_semantic_key(row) for row in enriched]
        if len(semantic) != len(set(semantic)):
            raise GanghwaContractError("current snapshot has semantic duplicates")
        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(enriched))
        if len(result) != len(enriched):
            raise GanghwaContractError("dedupe changed the atomic row count")
        identities = [_clean(row.get("provider_course_id")) for row in result]
        if len(identities) != len(set(identities)):
            raise GanghwaContractError("returned provider identities are not unique")
        meta.update(
            {
                "pages": source_pages,
                "list_requests": required_list_requests,
                "required_list_requests": required_list_requests,
                "sentinel_requests": 1,
                "sentinel_page": source_pages + 1,
                "sentinel_kind": "exact_final_page_clamp",
                "stability_rechecks": stability_rechecks,
                "detail_attempts": len(current_rows),
                "detail_pages": len(current_rows),
                "detail_errors": 0,
                "source_total": len(source_rows),
                "source_rows": len(source_rows),
                "unique_source_rows": len(source_rows),
                "current_source_count": len(current_rows),
                "publishable_current_count": len(result),
                "returned_count": len(result),
                "source_status_counts": dict(
                    Counter(_clean(row.get("_source_status")) for row in source_rows)
                ),
                "current_source_status_counts": dict(
                    Counter(_clean(row.get("_source_status")) for row in current_rows)
                ),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "branch_count": len({_clean(row.get("branch")) for row in result}),
                "venue_count": len({_clean(row.get("venue_name")) for row in result}),
                "test_or_notice_row_count": 0,
                "semantic_duplicate_count": 0,
                "application_control_count": 0,
                "truncated_list_schedule_count": sum(
                    bool(
                        (row.get("raw_fields") or {}).get(
                            "list_schedule_truncated"
                        )
                    )
                    for row in result
                ),
                "audited_no_application_period_count": sum(
                    bool(
                        (row.get("raw_fields") or {}).get(
                            "audited_no_application_period"
                        )
                    )
                    for row in result
                ),
                "audited_reversed_application_period_count": sum(
                    bool(
                        (row.get("raw_fields") or {}).get(
                            "historical_reversed_application_period"
                        )
                    )
                    for row in source_rows
                ),
                "audited_reversed_application_period_ids": sorted(
                    (
                        _clean(row.get("_identity"))
                        for row in source_rows
                        if bool(
                            (row.get("raw_fields") or {}).get(
                                "historical_reversed_application_period"
                            )
                        )
                    ),
                    key=int,
                ),
                "privacy_violations": 0,
                "pagination_detected": source_pages > 1,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "atomic_snapshot_complete": True,
                "source_cap_reached": False,
                "no_current_data": not result,
                "no_current_reason": (
                    "the complete Ganghwa archive has no course whose education end date is current/future"
                    if not result
                    else ""
                ),
                "configured_collection_error": "",
            }
        )
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        if "cap" in _clean(exc):
            meta["source_cap_reached"] = True
        return [], GANGHWA_PARSER, meta
    finally:
        meta["network_requests"] = runner.requests
        meta["network_retry_count"] = runner.retries
        meta["sessions_created"] = runner.sessions_created
        runner.close()
    return result, GANGHWA_PARSER, meta


collect_ganghwa_education = collect_incheon_ganghwa_education
collect_courses = collect_incheon_ganghwa_education
collect = collect_incheon_ganghwa_education


__all__ = [
    "GANGHWA_AUDITED_NO_APPLICATION_PERIODS",
    "GANGHWA_AUDITED_REVERSED_APPLICATION_PERIODS",
    "GANGHWA_CANONICAL_CANDIDATE_ID",
    "GANGHWA_CANONICAL_URL",
    "GANGHWA_CURRENT_INSTITUTIONS",
    "GANGHWA_DISCOVERY_AUDIT",
    "GANGHWA_DONG_DUPLICATE_CANDIDATE_ID",
    "GANGHWA_DONG_DUPLICATE_URL",
    "GANGHWA_EXCLUDED_SOURCE_AUDIT",
    "GANGHWA_MUNICIPALITY_CODE",
    "GANGHWA_MUNICIPALITY_NAME",
    "GANGHWA_OWNER_BOUNDARY_AUDIT",
    "GANGHWA_OWNERSHIP_SCOPE",
    "GANGHWA_PAGE_SIZE",
    "GANGHWA_PARSER",
    "GANGHWA_PROVIDER",
    "GANGHWA_REGISTERED_CANDIDATE_ID",
    "GANGHWA_REGISTERED_URL",
    "GANGHWA_URL",
    "GanghwaContractError",
    "canonical_ganghwa_detail_identity",
    "collect",
    "collect_courses",
    "collect_ganghwa_education",
    "collect_incheon_ganghwa_education",
    "ganghwa_detail_url",
    "ganghwa_list_url",
    "is_ganghwa_education_target",
    "is_target",
]
