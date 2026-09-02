"""Fail-closed education collector for Busan Jung-gu's two official ledgers.

The district's canonical owner is the unfiltered ``BBS_0000078`` education
board.  It includes lifelong learning, resident-autonomy, information,
international-centre and district culture-building courses.  Facility rental,
other services, personal reservations and identity verification are different
menu families and are never followed by this collector.

The source archive is traversed through its declared final page, followed by
the immediate empty page and stable first/final-page rechecks.  Only courses
whose education end date is current/future are opened.  Detail parsing uses a
small allowlist: instructor, telephone, address, introduction, attachments and
free-form course content are structurally discarded without reading their
values.  Applicant/write pages are never opened or submitted; an application
URL is retained only when the list exposes an exact course-identity-bound
control.

The companion Busan integrated-reservation partition is fixed to region
``15`` (Jung-gu) and operator ``33`` (resident-autonomy councils).  Every card
and detail must name a Jung-gu resident council; unfiltered city records and
individual details owned by the city, a museum, or another district are never
attributed to Jung-gu.  Both ledgers form one atomic snapshot.

The 부산시 평생학습 office feed republishes a subset of this same board under
``OFFICE_00002681``.  Its external URLs differ only in ``menuCd`` while
``boardId`` and ``dataSid`` are identical.  Ownership evidence below records
that overlap so the district board can remain the canonical owner.
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
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


BUSAN_JUNGGU_PROVIDER = "MUNI_WWW_BSJUNGGU_GO_KR_C443BFF0"
BUSAN_JUNGGU_FIXED_WRITE_PROVIDER = "MUNI_WWW_BSJUNGGU_GO_KR_4313BF64"
BUSAN_JUNGGU_DUPLICATE_HOME_PROVIDER = "MUNI_WWW_BSJUNGGU_GO_KR_9C069B21"
BUSAN_LIFELONG_PROVIDER = "MUNI_LLL_BUSAN_GO_KR_944C621B"
BUSAN_JUNGGU_MUNICIPALITY_CODE = "2611000000"
BUSAN_JUNGGU_MUNICIPALITY_NAME = "부산광역시 중구"
BUSAN_JUNGGU_HOST = "www.bsjunggu.go.kr"
BUSAN_JUNGGU_BOARD_ID = "BBS_0000078"
BUSAN_JUNGGU_APPLICATION_BOARD_ID = "BBS_0000080"
BUSAN_JUNGGU_MENU_CODE = "DOM_000001001000000000"
BUSAN_JUNGGU_LIST_PATH = "/yeyak/board/list.junggu"
BUSAN_JUNGGU_DETAIL_PATH = "/yeyak/board/view.junggu"
BUSAN_JUNGGU_APPLICATION_PATH = "/board/write.junggu"
BUSAN_JUNGGU_CANONICAL_URL = (
    f"https://{BUSAN_JUNGGU_HOST}{BUSAN_JUNGGU_LIST_PATH}?"
    + urlencode(
        (
            ("boardId", BUSAN_JUNGGU_BOARD_ID),
            ("menuCd", BUSAN_JUNGGU_MENU_CODE),
        )
    )
)
BUSAN_JUNGGU_HOME_URL = "https://www.bsjunggu.go.kr/lll/index.junggu"
BUSAN_JUNGGU_FIXED_WRITE_URL = (
    "https://www.bsjunggu.go.kr/board/write.junggu?"
    "boardId=BBS_0000080&menuCd=DOM_000001001004000000&INTNUM=254494"
)
BUSAN_LIFELONG_OFFICE_URL = (
    "https://lll.busan.go.kr/yeyak/ilms/learning/officeList.do"
)
BUSAN_LIFELONG_JUNGGU_OFFICE = "OFFICE_00002681"
BUSAN_PROVINCIAL_LIBRARY_URL = "https://home.pen.go.kr/joonganglib/main.do"
BUSAN_CITY_RESERVATION_URL = "https://reserve.busan.go.kr/lctre"
BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_JUNGGU_GUGUN = "15"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_JUNGGU_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_JUNGGU_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)
BUSAN_JUNGGU_FACILITY_URL = (
    "https://www.bsjunggu.go.kr/yeyak/index.junggu?"
    "menuCd=DOM_000001002000000000"
)
BUSAN_JUNGGU_OTHER_SERVICE_URL = (
    "https://www.bsjunggu.go.kr/yeyak/index.junggu?"
    "menuCd=DOM_000001003000000000"
)
BUSAN_JUNGGU_ACCOUNT_URL = (
    "https://www.bsjunggu.go.kr/yeyak/index.junggu?"
    "menuCd=DOM_000001004000000000"
)
BUSAN_JUNGGU_ATTESTATION_URL = (
    "https://www.bsjunggu.go.kr/yeyak/index.junggu?"
    "menuCd=DOM_000001005000000000"
)

BUSAN_JUNGGU_PAGE_SIZE = 8
BUSAN_JUNGGU_FETCH_ATTEMPTS = 4
BUSAN_JUNGGU_MAX_WORKERS = 8
BUSAN_JUNGGU_MAX_HTML_BYTES = 4_000_000
BUSAN_JUNGGU_PARSER = (
    "busan_junggu_education_bbs_0000078_all_declared_pages+empty_post_last+"
    "stable_first_last+busan_reserve_gugun15_office33_all_pages+sentinel+"
    "stable_first_last+all_current_safe_details+identity_bound_write_links+"
    "pii_content_never_read+atomic_two_source_snapshot"
)
BUSAN_JUNGGU_OWNERSHIP_SCOPE = (
    "busan_junggu_district_education_board_bbs_0000078_and_"
    "busan_city_reservation_junggu_resident_centres"
)

BUSAN_JUNGGU_CANDIDATE_IDS: Mapping[str, str] = {
    "busan_city_museum_detail": "MUNI_IR_2BA97ED12CEB",
    "busan_lifelong_widget_home": "MUNI_IR_5E508121336B",
    "jung_gu_lifelong_home": "MUNI_IR_68AB15A0C263",
    "expired_application_form": "MUNI_IR_E858F721A742",
}

BUSAN_JUNGGU_CATEGORY_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "교육기관 선택"),
    ("A1", "정보화교육"),
    ("B1", "평생학습프로그램"),
    ("C1", "주민자치프로그램"),
    ("D1", "40계단문화관"),
    ("E1", "보건소 건강아카데미"),
    ("F1", "희망교육"),
    ("G1", "국제화센터프로그램"),
    ("H1", "보수동책방골목문화관"),
)
BUSAN_JUNGGU_CATEGORY_BY_NAME = {
    name: code for code, name in BUSAN_JUNGGU_CATEGORY_OPTIONS if code
}

BUSAN_JUNGGU_PII_FIELDS_NEVER_READ = (
    "강사명 value",
    "기관 전화번호 value",
    "기관 주소 value",
    "기관 소개 value",
    "첨부파일 names/content",
    "강좌소개/free-form contents",
    "applicant/write form payload",
    "personal reservation and attestation pages",
)

BUSAN_JUNGGU_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_JUNGGU_PROVIDER: {
        "decision": "canonical_district_education_owner",
        "candidate_id": BUSAN_JUNGGU_CANDIDATE_IDS["jung_gu_lifelong_home"],
        "registered_url": BUSAN_JUNGGU_HOME_URL,
        "canonical_url": BUSAN_JUNGGU_CANONICAL_URL,
        "operator": "부산광역시 중구 통합예약시스템",
        "reason": "the home-page/category previews are replaced by the complete board",
    },
    BUSAN_JUNGGU_FIXED_WRITE_PROVIDER: {
        "decision": "superseded_identity_bound_write_alias_never_fetch",
        "candidate_id": BUSAN_JUNGGU_CANDIDATE_IDS["expired_application_form"],
        "url": BUSAN_JUNGGU_FIXED_WRITE_URL,
        "canonical_course_identity": "BBS_0000078:254494",
        "reason": "one expired applicant/write route is already a course in the board archive",
    },
    BUSAN_JUNGGU_DUPLICATE_HOME_PROVIDER: {
        "decision": "reject_cross_district_widget_as_source",
        "candidate_id": BUSAN_JUNGGU_CANDIDATE_IDS["busan_lifelong_widget_home"],
        "url": BUSAN_JUNGGU_HOME_URL + "?menuCd=DOM_000000702003001000",
        "reason": (
            "the landing-page widget can show courses from other Busan districts; "
            "it is not a Jung-gu ledger"
        ),
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "separate_city_index_but_suppress_junggu_office_duplicate",
        "url": BUSAN_LIFELONG_OFFICE_URL,
        "office_code": BUSAN_LIFELONG_JUNGGU_OFFICE,
        "operator": "부산광역시 평생학습 통합안내",
        "reason": (
            "50 of 51 office rows are external BBS_0000078/dataSid identities; "
            "the remaining row is an internal test course"
        ),
    },
    "OFFICIAL_BUSAN_PROVINCIAL_JOONGANG_LIBRARY": {
        "decision": "keep_separate_provincial_education_office_owner",
        "url": BUSAN_PROVINCIAL_LIBRARY_URL,
    },
    "OFFICIAL_BUSAN_CITY_RESERVATION": {
        "decision": "collect_only_junggu_resident_centre_partition",
        "url": BUSAN_CITY_RESERVATION_URL,
        "canonical_partition_url": BUSAN_CITY_JUNGGU_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_JUNGGU_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
    },
    "MUNI_IR_2BA97ED12CEB": {
        "decision": "exclude_wrong_operator_single_detail",
        "url": (
            "https://reserve.busan.go.kr/lctre/view?"
            "resveGroupSn=532&progrmSn=24398"
        ),
        "operator": "부산박물관",
    },
    "BUSAN_CITY_DETAIL_384_24458": {
        "decision": "exclude_wrong_municipality_single_detail",
        "url": (
            "https://reserve.busan.go.kr/lctre/view?"
            "resveGroupSn=384&progrmSn=24458"
        ),
        "operator": "서구 서대신1동 주민자치회",
    },
    "OFFICIAL_BUSAN_JUNGGU_FACILITY_RENTAL": {
        "decision": "exclude_non_education_facility_family",
        "url": BUSAN_JUNGGU_FACILITY_URL,
    },
    "OFFICIAL_BUSAN_JUNGGU_OTHER_SERVICE": {
        "decision": "exclude_non_education_health_service_family",
        "url": BUSAN_JUNGGU_OTHER_SERVICE_URL,
    },
    "OFFICIAL_BUSAN_JUNGGU_ACCOUNT": {
        "decision": "exclude_account_and_applicant_boundary",
        "url": BUSAN_JUNGGU_ACCOUNT_URL,
    },
    "OFFICIAL_BUSAN_JUNGGU_ATTESTATION": {
        "decision": "exclude_identity_verification_boundary",
        "url": BUSAN_JUNGGU_ATTESTATION_URL,
    },
}

BUSAN_JUNGGU_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "canonical_url": BUSAN_JUNGGU_CANONICAL_URL,
    "companion_resident_url": BUSAN_CITY_JUNGGU_URL,
    "source_rows": 1563,
    "district_source_rows": 1540,
    "city_source_rows": 23,
    "district_data_pages": 193,
    "city_data_pages": 3,
    "page_size": 8,
    "page_counts": {"1-192": 8, "193": 4},
    "empty_sentinel_page": 194,
    "first_last_rechecks": 2,
    "unique_identities": 1563,
    "source_status_counts": {"마감": 1536, "접수중": 4, "접수마감": 23},
    "source_category_counts": {
        "평생학습프로그램": 1128,
        "국제화센터프로그램": 176,
        "정보화교육": 161,
        "40계단문화관": 66,
        "보수동책방골목문화관": 8,
        "주민자치프로그램": 24,
        "보건소 건강아카데미": 0,
        "희망교육": 0,
    },
    "current_or_future_rows": 36,
    "district_current_ids": (
        "257015",
        "257010",
        "257009",
        "257012",
        "256792",
        "256791",
        "256789",
        "256787",
        "256786",
        "256234",
        "256233",
        "256232",
        "256231",
    ),
    "city_current_ids": (
        "reserve:364:24237",
        "reserve:364:24236",
        "reserve:364:24235",
        "reserve:364:24234",
        "reserve:364:24233",
        "reserve:364:24232",
        "reserve:123:22299",
        "reserve:123:22298",
        "reserve:123:22297",
        "reserve:123:11437",
        "reserve:123:11435",
        "reserve:123:11434",
        "reserve:123:9349",
        "reserve:125:24096",
        "reserve:125:24095",
        "reserve:125:24094",
        "reserve:125:24093",
        "reserve:125:24092",
        "reserve:125:24091",
        "reserve:125:24090",
        "reserve:125:24089",
        "reserve:125:24088",
        "reserve:125:24087",
    ),
    "current_status_counts": {"접수중": 4, "마감": 9, "접수마감": 23},
    "current_category_counts": {
        "보수동책방골목문화관": 4,
        "평생학습프로그램": 5,
        "국제화센터프로그램": 4,
        "주민자치프로그램": 23,
    },
    "current_facility_counts": {
        "보수동책방골목문화관": 4,
        "중구 평생학습관": 3,
        "행복학습센터": 1,
        "국제화센터프로그램": 4,
        "중구 대청동 주민자치회": 6,
        "중구 보수동 주민자치회": 7,
        "중구 광복동 주민자치회": 10,
    },
    "current_missing_facility_ids": ("256791",),
    "resident_autonomy": {"archive_rows": 1, "current_rows": 0},
    "busan_city_resident_partition": {
        "filters": {"srchGugun": "15", "srchResveInsttCd": "33"},
        "archive_rows": 23,
        "current_rows": 23,
        "page_counts": {"1": 10, "2": 10, "3": 3},
        "branches": {
            "중구 대청동 주민자치회": 6,
            "중구 보수동 주민자치회": 7,
            "중구 광복동 주민자치회": 10,
        },
    },
    "busan_lifelong_overlap": {
        "office_code": BUSAN_LIFELONG_JUNGGU_OFFICE,
        "office_rows": 51,
        "same_board_identity_rows": 50,
        "internal_test_rows": 1,
        "current_rows": 5,
        "current_overlap_rows": 5,
        "current_overlap_ids": ("256792", "256791", "256789", "256787", "256786"),
        "identity_rule": "ignore menuCd; compare boardId=BBS_0000078 and dataSid",
    },
    "conclusion": (
        "atomically collect the complete district BBS_0000078 board and Busan "
        "reservation gugun=15/office=33 resident-centre partition; suppress the "
        "Busan lifelong OFFICE_00002681 duplicate and all district home/write aliases"
    ),
}


class BusanJungguContractError(ValueError):
    """Raised when the audited source contract no longer holds."""


class _TransientFetchError(RuntimeError):
    """Retryable transport or non-HTML response error."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_TOTAL_RE = re.compile(r"총게시물\s*:\s*(\d+)\s*건")
_CATEGORY_TITLE_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
_DATE_RANGE_RE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})$"
)
_DETAIL_APPLY_RE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})$"
)
_TOTAL_CAPACITY_RE = re.compile(r"^총\s*:\s*(\d+)명,\s*대기\s*:\s*(\d*)명$")
_CURRENT_CAPACITY_RE = re.compile(r"^(\d+)명$")
_LIST_CAPACITY_RE = re.compile(r"^총\s*(\d+)명$")
_LIST_APPLICATION_COUNTS_RE = re.compile(r"^(\d+)\s*/\s*(\d+)명$")
_YEAR_TITLE_RE = re.compile(r"^\[\s*20\d{2}년\s*\]\s*(.+)$")
_CITY_ACTION_RE = re.compile(
    r"^fn_viewProgrm\(\s*['\"]([1-9]\d*)['\"]\s*,\s*"
    r"['\"]([1-9]\d*)['\"]\s*\);\s*return\s+false;?$"
)
_CITY_CARD_DATES_RE = re.compile(
    r"^\[신청\]\s*(20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s*\[행사\]\s*"
    r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})$"
)
_CITY_DETAIL_DATE_RE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")

# The complete official archive contains four stable, expired rows whose
# education-period text predates the board's current zero-padded date format.
# Keep these exceptions identity- and value-bound: a newly malformed row (or a
# change to one of these rows) must still fail the complete snapshot.
_AUDITED_LEGACY_EDUCATION_RANGES: Mapping[str, tuple[str, str, str]] = {
    "234995": ("20240702 ~ 2024-08-06", "2024-07-02", "2024-08-06"),
    "229911": ("2023-12-5 ~ 2023-12-12", "2023-12-05", "2023-12-12"),
    "219710": ("2022-09-16 ~ 2022-12-2", "2022-09-16", "2022-12-02"),
    "219741": ("2022-09-05 ~ 2022-09-02", "2022-09-02", "2022-09-05"),
}
_AUDITED_LEGACY_CAPACITIES: Mapping[str, tuple[str, int]] = {
    "189968": ("총 10명명", 10),
}

_LIST_TITLE = "(교육/강좌)의 목록 |부산광역시 중구 통합예약시스템"
_DETAIL_TITLE = "(교육/강좌)의 내용 |부산광역시 중구 통합예약시스템"
_LIST_FORM_ACTION = "/board/list.junggu"
_RATING_FORM_ACTION = "/menu/insertGradeAct.junggu"
_LIST_LABELS = ("교육기간", "수강인원", "신청/대기", "접수방법")
_DETAIL_LABELS = (
    "접수기간",
    "수강인원",
    "접수인원",
    "교육기간",
    "수강료",
    "교육대상",
    "교육시간",
    "교육횟수",
    "접수방법",
    "강사명",
)
_DETAIL_SAFE_LABELS = frozenset(_DETAIL_LABELS[:-1])
_DETAIL_SKIPPED_LABELS = frozenset({"강사명"})
_INSTITUTION_LABELS = ("기관명", "전화번호", "주소", "소개")
_STATUS_MAP = {"대기중": "SCHEDULED", "접수중": "OPEN", "마감": "CLOSED"}
_DETAIL_STATUS_BY_LIST = {"대기중": "접수대기", "접수중": "접수중", "마감": "접수마감"}
_STATE_CLASS_BY_STATUS = {"대기중": "st1", "접수중": "st2", "마감": "st3"}
_CITY_LIST_TITLE = "강좌/교육 : 부산광역시 통합예약"
_CITY_CARD_LABELS = ("기관", "대상", "장소", "일자", "방법", "문의")
_CITY_DETAIL_LABELS = (
    "운영기간",
    "신청기간",
    "취소여부",
    "신청방법",
    "수강료",
    "요일 /시간",
    "문의전화",
    "운영기관",
    "대상",
)
_CITY_SAFE_DETAIL_LABELS = frozenset(_CITY_DETAIL_LABELS) - {"문의전화"}
_CITY_STATUS_MAP = {
    "대기중": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}

_SELECT_OPTIONS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "categoryCode1": BUSAN_JUNGGU_CATEGORY_OPTIONS,
    "categoryCode2": (
        ("", "교육대상 선택"),
        ("A01", "전체"),
        ("A08", "부산시민"),
        ("A02", "지역주민"),
        ("A03", "유아"),
        ("A04", "초등"),
        ("A05", "청소년"),
        ("A06", "성인"),
        ("A07", "실버"),
    ),
    "gubun1": (
        ("", "교육분류 선택"),
        ("01", "기초문해 교육"),
        ("02", "학력보완 교육"),
        ("03", "직업능력교육"),
        ("04", "문화예술교육"),
        ("05", "인문교양교육"),
        ("06", "시민참여교육"),
        ("07", "정보화교육"),
        ("08", "기타"),
    ),
    "state": (
        ("", "접수상태 선택"),
        ("state1", "대기중"),
        ("state2", "접수중"),
        ("state3", "접수마감"),
    ),
    "dateselect": (
        ("", "접수/교육기간 선택"),
        ("apptime", "접수기간"),
        ("edutime", "교육기간"),
    ),
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    if not isinstance(node, Tag):
        return ""
    return _clean(node.get_text(" ", strip=True))


def _one(values: Iterable[Any], label: str) -> Any:
    nodes = list(values)
    if len(nodes) != 1:
        raise BusanJungguContractError(f"expected one {label}, found {len(nodes)}")
    return nodes[0]


def _direct_value(label: Tag) -> str:
    """Read only direct text siblings, never nested later labels/PII."""

    parts: list[str] = []
    for sibling in label.next_siblings:
        if isinstance(sibling, NavigableString):
            value = _clean(sibling)
            if value:
                parts.append(value)
        elif isinstance(sibling, Tag) and sibling.name == "br":
            continue
        # Other tags are deliberately ignored.  Malformed live HTML nests
        # subsequent <li> elements under 교육대상; recursive text would leak
        # later labels, including instructor names.
    return _clean(" ".join(parts))


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
    if isinstance(value, str):
        return date.fromisoformat(_clean(value))
    raise TypeError("today must be a date, datetime, ISO date string, or None")


def is_busan_junggu_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == BUSAN_JUNGGU_PROVIDER
        and _clean(_target_value(target, "url")) == BUSAN_JUNGGU_CANONICAL_URL
    )


def is_busan_junggu_registered_home_alias(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == BUSAN_JUNGGU_PROVIDER
        and _clean(_target_value(target, "url")) == BUSAN_JUNGGU_HOME_URL
    )


def busan_junggu_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise BusanJungguContractError("page must be a positive integer")
    if page == 1:
        return BUSAN_JUNGGU_CANONICAL_URL
    return BUSAN_JUNGGU_CANONICAL_URL + "&" + urlencode(
        (
            ("searchType", ""),
            ("keyword", ""),
            ("categoryCode1", ""),
            ("nowPage", str(page)),
        )
    )


def busan_junggu_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise BusanJungguContractError("detail identity must be a positive integer")
    return f"https://{BUSAN_JUNGGU_HOST}{BUSAN_JUNGGU_DETAIL_PATH}?" + urlencode(
        (
            ("boardId", BUSAN_JUNGGU_BOARD_ID),
            ("startPage", "1"),
            ("menuCd", BUSAN_JUNGGU_MENU_CODE),
            ("dataSid", value),
        )
    )


def busan_junggu_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise BusanJungguContractError("application identity must be a positive integer")
    return f"https://{BUSAN_JUNGGU_HOST}{BUSAN_JUNGGU_APPLICATION_PATH}?" + urlencode(
        (
            ("boardId", BUSAN_JUNGGU_APPLICATION_BOARD_ID),
            ("menuCd", BUSAN_JUNGGU_MENU_CODE),
            ("INTNUM", value),
        )
    )


def busan_junggu_city_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise BusanJungguContractError("city page must be a positive integer")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (
            ("curPage", str(page)),
            ("srchGugun", BUSAN_CITY_JUNGGU_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )


def busan_junggu_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _clean(group_id)
    program = _clean(program_id)
    if not _IDENTITY_RE.fullmatch(group) or not _IDENTITY_RE.fullmatch(program):
        raise BusanJungguContractError("city detail identities must be positive integers")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def canonical_busan_junggu_course_identity(value: Any) -> str:
    """Normalize district/common-feed URLs to the board/dataSid identity."""

    parsed = urlparse(_clean(value))
    if (parsed.hostname or "").rstrip(".").lower() != BUSAN_JUNGGU_HOST:
        return ""
    if parsed.scheme.lower() != "https" or parsed.username or parsed.password:
        return ""
    if parsed.path != BUSAN_JUNGGU_DETAIL_PATH:
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query.get("boardId") != [BUSAN_JUNGGU_BOARD_ID]:
        return ""
    identities = query.get("dataSid", [])
    if len(identities) != 1 or not _IDENTITY_RE.fullmatch(identities[0]):
        return ""
    return f"{BUSAN_JUNGGU_BOARD_ID}:{identities[0]}"


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": "https://www.bsjunggu.go.kr/yeyak/index.junggu",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _RequestBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0
        self._lock = threading.Lock()

    def consume(self) -> None:
        with self._lock:
            if self.count >= self.limit:
                raise BusanJungguContractError(
                    f"max_requests cap exhausted at {self.limit} HTTP attempts"
                )
            self.count += 1


def _response_soup(response: Any, requested_url: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise _TransientFetchError(f"HTTP {status}")
    if getattr(response, "history", None):
        raise _TransientFetchError("redirected response")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and final_url != requested_url:
        raise _TransientFetchError("response URL changed")
    headers = getattr(response, "headers", None)
    if headers:
        content_type = _clean(headers.get("Content-Type")).casefold()
        if content_type and "html" not in content_type:
            raise _TransientFetchError("response is not HTML")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if isinstance(content, str):
        payload = content.encode("utf-8")
    elif isinstance(content, bytes):
        payload = content
    elif isinstance(response, BeautifulSoup):
        payload = response.encode("utf-8")
    else:
        payload = b""
    if not payload:
        raise _TransientFetchError("empty HTML response")
    if len(payload) > BUSAN_JUNGGU_MAX_HTML_BYTES:
        raise _TransientFetchError("HTML response exceeds byte cap")
    soup = BeautifulSoup(payload, "lxml")
    requested = urlparse(requested_url)
    if requested.path == BUSAN_JUNGGU_LIST_PATH:
        # The site occasionally answers a list request with a generic 200 HTML
        # page.  Treat that as a retryable transport response, while the same
        # contract failure across all attempts still closes the snapshot.
        try:
            _list_contract(soup)
        except BusanJungguContractError as exc:
            raise _TransientFetchError(f"invalid list response: {exc}") from exc
    elif requested.path == BUSAN_CITY_LIST_PATH:
        try:
            query = parse_qs(requested.query, keep_blank_values=True)
            page_raw = _query_single(query, "curPage")
            if not page_raw.isdigit() or int(page_raw) < 1:
                raise BusanJungguContractError("invalid city request page")
            _city_list_contract(soup, page=int(page_raw))
        except BusanJungguContractError as exc:
            raise _TransientFetchError(
                f"invalid Busan city list response: {exc}"
            ) from exc
    elif requested.path == BUSAN_JUNGGU_DETAIL_PATH:
        try:
            if _text(_one(soup.select("title"), "detail title")) != _DETAIL_TITLE:
                raise BusanJungguContractError("detail page title changed")
            _one(soup.select("div.bbs_vtype.edu"), "education detail root")
            _detail_forms_contract(soup)
        except BusanJungguContractError as exc:
            raise _TransientFetchError(f"invalid detail response: {exc}") from exc
    elif requested.path == BUSAN_CITY_DETAIL_PATH:
        try:
            if _text(_one(soup.select("title"), "city detail title")) != _CITY_LIST_TITLE:
                raise BusanJungguContractError("Busan city detail title changed")
            _one(soup.select("form#viewForm"), "Busan city detail form")
            _one(
                soup.select("form#viewForm div.reserveStateInfo"),
                "Busan city detail values",
            )
        except BusanJungguContractError as exc:
            raise _TransientFetchError(
                f"invalid Busan city detail response: {exc}"
            ) from exc
    return soup


@dataclass(frozen=True)
class _FetchResult:
    values: Mapping[Any, BeautifulSoup]
    errors: tuple[str, ...]
    retries: int
    sessions: int


def _fetch_many(
    jobs: Sequence[tuple[Any, str]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
) -> _FetchResult:
    if not jobs:
        return _FetchResult({}, (), 0, 0)
    worker_count = min(max(1, max_workers), len(jobs), BUSAN_JUNGGU_MAX_WORKERS)
    chunks: list[list[tuple[Any, str]]] = [[] for _ in range(worker_count)]
    for index, job in enumerate(jobs):
        chunks[index % worker_count].append(job)

    def run_chunk(chunk: Sequence[tuple[Any, str]]):
        values: dict[Any, BeautifulSoup] = {}
        errors: list[str] = []
        retries = 0
        sessions = 1
        session = session_factory()
        try:
            for key, url in chunk:
                messages: list[str] = []
                for attempt in range(1, BUSAN_JUNGGU_FETCH_ATTEMPTS + 1):
                    try:
                        budget.consume()
                        response = fetcher(session, url, timeout)
                        values[key] = _response_soup(response, url)
                        break
                    except BusanJungguContractError as exc:
                        messages.append(str(exc))
                        break
                    except Exception as exc:
                        messages.append(
                            f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}"
                        )
                        if attempt >= BUSAN_JUNGGU_FETCH_ATTEMPTS:
                            break
                        retries += 1
                        _close_quietly(session)
                        session = session_factory()
                        sessions += 1
                        sleeper(min(0.25 * attempt, 0.75))
                if key not in values:
                    errors.append(f"{key}: {'; '.join(messages)}")
        finally:
            _close_quietly(session)
        return values, errors, retries, sessions

    combined: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    retries = 0
    sessions = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(run_chunk, chunk) for chunk in chunks if chunk]
        for future in as_completed(futures):
            values, chunk_errors, chunk_retries, chunk_sessions = future.result()
            combined.update(values)
            errors.extend(chunk_errors)
            retries += chunk_retries
            sessions += chunk_sessions
    return _FetchResult(combined, tuple(errors), retries, sessions)


def _option_contract(form: Tag) -> None:
    for name, expected in _SELECT_OPTIONS.items():
        select = _one(form.select(f"select[name='{name}']"), f"{name} select")
        actual = tuple(
            (_clean(option.get("value")), _text(option))
            for option in select.select("option")
        )
        if actual != expected:
            raise BusanJungguContractError(f"{name} options changed")


def _list_contract(soup: BeautifulSoup) -> tuple[int, Tag]:
    title = _one(soup.select("title"), "list title")
    if _text(title) != _LIST_TITLE:
        raise BusanJungguContractError("list title changed")
    form = _one(soup.select("form.rfc_bbs_searchForm"), "education search form")
    if _clean(form.get("method")).casefold() != "get":
        raise BusanJungguContractError("education search form method changed")
    if urlparse(_clean(form.get("action"))).path != _LIST_FORM_ACTION:
        raise BusanJungguContractError("education search form action changed")
    required = {
        "orderBy": "",
        "boardId": BUSAN_JUNGGU_BOARD_ID,
        "menuCd": BUSAN_JUNGGU_MENU_CODE,
        "contentsSid": "1038",
        "startPage": "1",
    }
    for name, expected in required.items():
        field = _one(form.select(f"input[name='{name}']"), f"{name} field")
        if _clean(field.get("value")) != expected:
            raise BusanJungguContractError(f"search form {name} changed")
    _option_contract(form)
    page_text = _one(soup.select("p.boardPage"), "board total")
    match = _TOTAL_RE.search(_text(page_text))
    if not match:
        raise BusanJungguContractError("declared total is missing")
    total = int(match.group(1))
    container = _one(soup.select("div.bbsEdu"), "education list")
    course_list = _one(container.find_all("ul", recursive=False), "education row list")
    return total, course_list


def _query_single(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key, [])
    if len(values) != 1:
        raise BusanJungguContractError(f"URL {key} is missing or repeated")
    return _clean(values[0])


def _identity_from_detail_href(value: Any) -> str:
    absolute = urljoin(BUSAN_JUNGGU_CANONICAL_URL, _clean(value))
    parsed = urlparse(absolute)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_JUNGGU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.params
        or parsed.path != BUSAN_JUNGGU_DETAIL_PATH
    ):
        raise BusanJungguContractError("unsafe or unexpected detail URL")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"boardId", "startPage", "menuCd", "dataSid"}:
        raise BusanJungguContractError("detail URL query changed")
    if _query_single(query, "boardId") != BUSAN_JUNGGU_BOARD_ID:
        raise BusanJungguContractError("detail board identity changed")
    if _query_single(query, "startPage") != "1":
        raise BusanJungguContractError("detail startPage changed")
    if _query_single(query, "menuCd") != BUSAN_JUNGGU_MENU_CODE:
        raise BusanJungguContractError("detail menu identity changed")
    identity = _query_single(query, "dataSid")
    if not _IDENTITY_RE.fullmatch(identity):
        raise BusanJungguContractError("malformed detail identity")
    return identity


def _application_from_href(value: Any, identity: str) -> str:
    absolute = urljoin(BUSAN_JUNGGU_CANONICAL_URL, _clean(value))
    parsed = urlparse(absolute)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_JUNGGU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.params
        or parsed.path != BUSAN_JUNGGU_APPLICATION_PATH
    ):
        raise BusanJungguContractError("application control left the write-route boundary")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"boardId", "menuCd", "INTNUM"}:
        raise BusanJungguContractError("application URL query changed")
    if _query_single(query, "boardId") != BUSAN_JUNGGU_APPLICATION_BOARD_ID:
        raise BusanJungguContractError("application board identity changed")
    if _query_single(query, "menuCd") != BUSAN_JUNGGU_MENU_CODE:
        raise BusanJungguContractError("application menu identity changed")
    if _query_single(query, "INTNUM") != identity:
        raise BusanJungguContractError("application control is not course-identity-bound")
    return busan_junggu_application_url(identity)


def _strict_date_range(value: Any, label: str) -> tuple[str, str]:
    match = _DATE_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise BusanJungguContractError(f"{label} is not an exact date range")
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
    except ValueError as exc:
        raise BusanJungguContractError(f"{label} contains an invalid date") from exc
    if end < start:
        raise BusanJungguContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _list_education_date_range(
    value: Any,
    *,
    identity: str,
    label: str,
) -> tuple[str, str, bool]:
    """Parse a list period, allowing only the three audited archive typos."""

    raw = _clean(value)
    try:
        start, end = _strict_date_range(raw, label)
        return start, end, False
    except BusanJungguContractError:
        legacy = _AUDITED_LEGACY_EDUCATION_RANGES.get(identity)
        if legacy is None or legacy[0] != raw:
            raise
        return legacy[1], legacy[2], True


def _optional_historical_date_range(value: Any) -> tuple[str, str]:
    match = _DATE_RANGE_RE.fullmatch(_clean(value))
    if not match:
        return "", ""
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
    except ValueError:
        return "", ""
    if end < start:
        return "", ""
    return start.isoformat(), end.isoformat()


def _methods(value: Any) -> tuple[str, ...]:
    return tuple(part for part in (_clean(item) for item in _clean(value).split(",")) if part)


def _list_capacity(value: Any, *, identity: str) -> tuple[int, bool]:
    raw = _clean(value)
    match = _LIST_CAPACITY_RE.fullmatch(raw)
    if match:
        return int(match.group(1)), False
    legacy = _AUDITED_LEGACY_CAPACITIES.get(identity)
    if legacy is None or legacy[0] != raw:
        raise BusanJungguContractError("list capacity changed")
    return legacy[1], True


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_total: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    total, course_list = _list_contract(soup)
    if expected_total is not None and total != expected_total:
        raise BusanJungguContractError(f"page {page} declared total changed")
    rows: list[dict[str, Any]] = []
    for position, outer in enumerate(course_list.find_all("li", recursive=False), 1):
        box = _one(outer.find_all("div", class_="box", recursive=False), "course box")
        state = _one(box.select(":scope > div.state"), "course state")
        source_status = _text(_one(state.select(":scope > span.txt"), "status text"))
        if source_status not in _STATUS_MAP:
            raise BusanJungguContractError(
                f"page {page} row {position}: unknown source status {source_status!r}"
            )
        classes = set(state.get("class", []))
        if _STATE_CLASS_BY_STATUS[source_status] not in classes:
            raise BusanJungguContractError(f"page {page} row {position}: state class changed")
        source_target = _text(_one(state.select(":scope > span.targ"), "target text"))
        if not source_target:
            raise BusanJungguContractError(f"page {page} row {position}: empty target")
        apply_raw = _text(_one(box.select(":scope > span.data"), "application period"))
        apply_start, apply_end = _optional_historical_date_range(apply_raw)

        title_link = _one(box.select(":scope > span.tit > a[href]"), "course detail link")
        identity = _identity_from_detail_href(title_link.get("href"))
        title_match = _CATEGORY_TITLE_RE.fullmatch(_text(title_link))
        if not title_match:
            raise BusanJungguContractError(f"page {page} row {position}: title/category changed")
        category = _clean(title_match.group(1))
        title = _clean(title_match.group(2))
        if category not in BUSAN_JUNGGU_CATEGORY_BY_NAME or not title:
            raise BusanJungguContractError(f"page {page} row {position}: unknown category/title")

        values_list = _one(box.find_all("ul", recursive=False), "course values")
        spans = values_list.select("span.name")
        labels = tuple(_text(span) for span in spans)
        if labels != _LIST_LABELS:
            raise BusanJungguContractError(f"page {page} row {position}: list labels changed")
        values = {label: _direct_value(span) for label, span in zip(labels, spans)}
        education_start, education_end, legacy_period_normalized = (
            _list_education_date_range(
                values["교육기간"],
                identity=identity,
                label=f"page {page} row {position} education period",
            )
        )
        capacity_total, legacy_capacity_normalized = _list_capacity(
            values["수강인원"], identity=identity
        )
        count_match = _LIST_APPLICATION_COUNTS_RE.fullmatch(values["신청/대기"])
        methods = _methods(values["접수방법"])
        if not count_match or not methods:
            raise BusanJungguContractError(
                f"page {page} row {position}: capacity/application methods changed"
            )

        button = _one(box.select(":scope > span.btn"), "application status/control")
        controls = button.select("a[href]")
        application_url = ""
        if controls:
            control = _one(controls, "application control")
            if _text(control) != "접수하러가기":
                raise BusanJungguContractError(
                    f"page {page} row {position}: application label changed"
                )
            if source_status != "접수중":
                raise BusanJungguContractError(
                    f"page {page} row {position}: unavailable course gained a control"
                )
            application_url = _application_from_href(control.get("href"), identity)
        else:
            if source_status == "접수중":
                raise BusanJungguContractError(
                    f"page {page} row {position}: open course lost its control"
                )
            button_text = _text(button)
            if source_status == "마감" and button_text != "접수마감되었습니다.":
                raise BusanJungguContractError(
                    f"page {page} row {position}: closed marker changed"
                )

        row = {
            "provider": BUSAN_JUNGGU_PROVIDER,
            "provider_course_id": identity,
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "description": title,
            "branch": "",
            "branch_code": BUSAN_JUNGGU_CATEGORY_BY_NAME[category],
            "preserve_branch": True,
            "category": category,
            "program_type": "교육/강좌",
            "raw_url": busan_junggu_detail_url(identity),
            "application_url": application_url,
            "application_type": "ONLINE" if application_url else "INFO_ONLY",
            "application_method_raw": ", ".join(methods),
            "reservation_available": bool(application_url),
            "status": _STATUS_MAP[source_status],
            "fee": "",
            "period": f"{education_start} ~ {education_end}",
            "start_date": education_start,
            "end_date": education_end,
            "apply_period": (
                f"{apply_start} ~ {apply_end}" if apply_start and apply_end else apply_raw
            ),
            "apply_start": apply_start,
            "apply_end": apply_end,
            "schedule_raw": "",
            "target": source_target,
            "capacity_total": capacity_total,
            "capacity_current": int(count_match.group(1)),
            "venue_name": "",
            "municipality_code": BUSAN_JUNGGU_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_JUNGGU_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "중구",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "collection_type": "static_html+detail_html",
            "raw_fields": {
                "parser": BUSAN_JUNGGU_PARSER,
                "source_catalog": "busan_junggu_district_board",
                "source_identity": f"{BUSAN_JUNGGU_BOARD_ID}:{identity}",
                "source_page": page,
                "source_position": position,
                "source_status": source_status,
                "source_category_code": BUSAN_JUNGGU_CATEGORY_BY_NAME[category],
                "source_category_name": category,
                "source_target": source_target,
                "source_application_period": apply_raw,
                "source_education_period": values["교육기간"],
                "legacy_education_period_normalized": legacy_period_normalized,
                "legacy_capacity_normalized": legacy_capacity_normalized,
                "source_application_methods": list(methods),
                "source_waiting_count": int(count_match.group(2)),
                "application_control_present": bool(application_url),
                "application_control_identity_verified": bool(application_url),
                "applicant_write_boundary_never_fetched": True,
                "detail_verified": False,
                "service_family": "education",
            },
        }
        rows.append(row)
    return rows, total


def _city_list_contract(
    soup: BeautifulSoup, *, page: int
) -> tuple[int, Optional[Tag]]:
    title = _one(soup.select("title"), "Busan city list title")
    if _text(title) != _CITY_LIST_TITLE:
        raise BusanJungguContractError("Busan city list title changed")
    form = _one(soup.select("form#srchForm"), "Busan city search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
    ):
        raise BusanJungguContractError("Busan city search form changed")
    page_field = _one(
        form.select("input[name='curPage']"), "Busan city curPage field"
    )
    if _clean(page_field.get("value")) != str(page):
        raise BusanJungguContractError("Busan city form page differs from request")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_JUNGGU_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanJungguContractError(
                f"Busan city {name} owner filter changed"
            )

    end_link = _one(soup.select("div.paginate > a.pgEnd[href]"), "city last page")
    end_url = urljoin(BUSAN_CITY_JUNGGU_URL, _clean(end_link.get("href")))
    parsed = urlparse(end_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_CITY_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_CITY_LIST_PATH
        or parsed.fragment
        or parsed.params
        or set(query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or _query_single(query, "srchGugun") != BUSAN_CITY_JUNGGU_GUGUN
        or _query_single(query, "srchResveInsttCd")
        != BUSAN_CITY_RESIDENT_OFFICE
    ):
        raise BusanJungguContractError("unsafe Busan city last-page control")
    last_raw = _query_single(query, "curPage")
    if not last_raw.isdigit() or int(last_raw) < 1:
        raise BusanJungguContractError("invalid Busan city last page")
    last_page = int(last_raw)
    roots = soup.select("ul.reserveList")
    if page <= last_page:
        root: Optional[Tag] = _one(roots, "Busan city reserve list")
    elif page == last_page + 1:
        if roots:
            raise BusanJungguContractError(
                "Busan city sentinel unexpectedly retained a reserve list"
            )
        root = None
    else:
        raise BusanJungguContractError("Busan city page passed the sentinel boundary")
    return last_page, root


def _city_card_date_ranges(value: Any, *, label: str) -> tuple[str, str, str, str]:
    match = _CITY_CARD_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanJungguContractError(f"{label} changed")
    try:
        apply_start, apply_end, start, end = (
            date.fromisoformat(part) for part in match.groups()
        )
    except ValueError as exc:
        raise BusanJungguContractError(f"{label} contains an invalid date") from exc
    if apply_end < apply_start or end < start:
        raise BusanJungguContractError(f"{label} is reversed")
    return (
        apply_start.isoformat(),
        apply_end.isoformat(),
        start.isoformat(),
        end.isoformat(),
    )


def _parse_city_list_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    last_page, root = _city_list_contract(soup, page=page)
    if expected_last is not None and last_page != expected_last:
        raise BusanJungguContractError(
            f"Busan city page {page}: displayed last page changed"
        )
    rows: list[dict[str, Any]] = []
    items = root.find_all("li", recursive=False) if root is not None else []
    for position, item in enumerate(items, 1):
        link = _one(
            item.select(":scope > a.reserveItem[onclick]"),
            "Busan city course link",
        )
        action = _clean(link.get("onclick"))
        action_match = _CITY_ACTION_RE.fullmatch(action)
        if not action_match:
            raise BusanJungguContractError(
                f"Busan city page {page} row {position}: identity action changed"
            )
        group_id, program_id = action_match.groups()
        title_node = _one(link.select(":scope .tit"), "Busan city title")
        title_attr = _clean(title_node.get("title"))
        title = _text(title_node)
        if not title or title_attr != title:
            raise BusanJungguContractError(
                f"Busan city page {page} row {position}: title changed"
            )
        status_node = _one(link.select(":scope .statusMark"), "Busan city status")
        source_status = _text(status_node)
        if source_status not in _CITY_STATUS_MAP:
            raise BusanJungguContractError(
                f"Busan city page {page} row {position}: unknown status"
            )

        values_root = _one(link.select(":scope .infoBox > dl"), "Busan city values")
        headings = values_root.find_all("dt", recursive=False)
        values = values_root.find_all("dd", recursive=False)
        labels = tuple(_text(heading) for heading in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(headings):
            raise BusanJungguContractError(
                f"Busan city page {page} row {position}: card labels changed"
            )
        # 문의 is intentionally the final pair and its value is never read.
        safe = {
            label: _text(value)
            for label, value in zip(labels[:-1], values[:-1])
        }
        if any(not value for value in safe.values()):
            raise BusanJungguContractError(
                f"Busan city page {page} row {position}: safe card value is empty"
            )
        branch = safe["기관"]
        if not branch.startswith("중구 ") or not branch.endswith("주민자치회"):
            raise BusanJungguContractError(
                f"Busan city page {page} row {position}: course left Jung-gu owner"
            )
        apply_start, apply_end, start, end = _city_card_date_ranges(
            safe["일자"], label=f"Busan city page {page} row {position} dates"
        )
        raw_url = busan_junggu_city_detail_url(group_id, program_id)
        rows.append(
            {
                "provider": BUSAN_JUNGGU_PROVIDER,
                "provider_course_id": f"reserve:{group_id}:{program_id}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"reserve-{group_id}",
                "preserve_branch": True,
                "category": "주민자치프로그램",
                "program_type": "교육/강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "application_method_raw": safe["방법"],
                "reservation_available": False,
                "status": _CITY_STATUS_MAP[source_status],
                "fee": "",
                "period": f"{start} ~ {end}",
                "start_date": start,
                "end_date": end,
                "apply_period": f"{apply_start} ~ {apply_end}",
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": "",
                "target": safe["대상"],
                "capacity_total": None,
                "capacity_current": None,
                "venue_name": safe["장소"],
                "provider_organizer": branch,
                "municipality_code": BUSAN_JUNGGU_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_JUNGGU_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "중구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "collection_type": "static_html+detail_html",
                "raw_fields": {
                    "parser": BUSAN_JUNGGU_PARSER,
                    "source_catalog": "busan_reserve_junggu_resident_centres",
                    "source_identity": f"{group_id}:{program_id}",
                    "source_group_id": group_id,
                    "source_program_id": program_id,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_application_method": safe["방법"],
                    "source_card_dates": safe["일자"],
                    "inquiry_value_never_read": True,
                    "detail_verified": False,
                    "service_family": "education",
                },
            }
        )
    return rows, last_page


def _city_page_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        "\x1e".join(
            "\x1f".join(
                (
                    _clean(row.get("provider_course_id")),
                    _clean(row.get("title")),
                    _clean(row.get("start_date")),
                    _clean(row.get("end_date")),
                    _clean(row.get("raw_fields", {}).get("source_status")),
                )
            )
            for row in rows
        ).encode("utf-8")
    ).hexdigest()


def _city_detail_dates(value: Any, *, label: str) -> tuple[str, str]:
    values = _CITY_DETAIL_DATE_RE.findall(_clean(value))
    if len(values) != 2:
        raise BusanJungguContractError(f"{label} changed")
    try:
        start, end = (date.fromisoformat(value) for value in values)
    except ValueError as exc:
        raise BusanJungguContractError(f"{label} contains an invalid date") from exc
    if end < start:
        raise BusanJungguContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _parse_city_detail(
    soup: BeautifulSoup,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    title = _one(soup.select("title"), "Busan city detail title")
    if _text(title) != _CITY_LIST_TITLE:
        raise BusanJungguContractError("Busan city detail page title changed")
    form = _one(soup.select("form#viewForm"), "Busan city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(form.get("action")):
        raise BusanJungguContractError("Busan city detail form changed")
    raw = dict(row.get("raw_fields", {}))
    group_id = _clean(raw.get("source_group_id"))
    program_id = _clean(raw.get("source_program_id"))
    for name, expected in (
        ("resveGroupSn", group_id),
        ("progrmSn", program_id),
    ):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanJungguContractError("Busan city detail identity changed")

    heading = _one(form.select(":scope > div.contHeader > h3.titPage"), "city heading")
    source_status = _text(
        _one(heading.select(":scope .statusMark"), "city detail status")
    )
    direct_title = _clean(
        " ".join(
            _clean(child)
            for child in heading.children
            if isinstance(child, NavigableString) and _clean(child)
        )
    )
    if direct_title != _clean(row.get("title")):
        raise BusanJungguContractError("Busan city list/detail title differs")
    if source_status != _clean(raw.get("source_status")):
        raise BusanJungguContractError("Busan city list/detail status differs")

    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "Busan city safe detail values",
    )
    definitions = info.find_all("dl", recursive=False)
    headings: list[Tag] = []
    values: list[Tag] = []
    for definition in definitions:
        headings.append(_one(definition.find_all("dt", recursive=False), "city dt"))
        values.append(_one(definition.find_all("dd", recursive=False), "city dd"))
    labels = tuple(_text(heading) for heading in headings)
    if labels != _CITY_DETAIL_LABELS or len(values) != len(labels):
        raise BusanJungguContractError("Busan city detail labels changed")
    safe_values = {
        label: _text(value)
        for label, value in zip(labels, values)
        if label in _CITY_SAFE_DETAIL_LABELS
    }
    if set(safe_values) != _CITY_SAFE_DETAIL_LABELS or any(
        not value for value in safe_values.values()
    ):
        raise BusanJungguContractError("Busan city safe detail value is empty")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanJungguContractError("Busan city free-form detail boundary changed")

    start, end = _city_detail_dates(
        safe_values["운영기간"], label="Busan city operating period"
    )
    apply_start, apply_end = _city_detail_dates(
        safe_values["신청기간"], label="Busan city application period"
    )
    if (start, end) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
    ):
        raise BusanJungguContractError("Busan city list/detail dates differ")
    if safe_values["신청방법"] != _clean(raw.get("source_application_method")):
        raise BusanJungguContractError("Busan city list/detail method differs")
    if safe_values["운영기관"] != _clean(row.get("branch")):
        raise BusanJungguContractError("Busan city list/detail owner differs")
    if safe_values["대상"] != _clean(row.get("target")):
        raise BusanJungguContractError("Busan city list/detail target differs")

    control = _one(
        form.select(":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL"),
        "Busan city application/status control",
    )
    control_label = _text(control)
    method = safe_values["신청방법"]
    normalized_status = _CITY_STATUS_MAP[source_status]
    application_url = ""
    application_type = "INFO_ONLY"
    reservation_available = False
    if normalized_status == "OPEN":
        if "온라인" in method:
            if not any(token in control_label for token in ("신청", "예약")) or "마감" in control_label:
                raise BusanJungguContractError(
                    "open Busan city online course lacks an active control"
                )
            application_url = _clean(row.get("raw_url"))
            application_type = "ONLINE_RESERVATION"
            reservation_available = True
        elif any(token in method for token in ("방문", "전화")):
            if not any(token in control_label for token in ("방문", "전화", "예약", "신청")):
                raise BusanJungguContractError(
                    "open Busan city offline course lacks an explicit control"
                )
            application_type = "OFFLINE_APPLY"
        else:
            raise BusanJungguContractError(
                "open Busan city course has an unknown application method"
            )
    elif normalized_status == "CLOSED":
        if control_label != "접수마감":
            raise BusanJungguContractError("closed Busan city control changed")
    elif normalized_status == "SCHEDULED":
        if control_label not in {"대기중", "접수대기"}:
            raise BusanJungguContractError("scheduled Busan city control changed")

    result = dict(row)
    result.update(
        {
            "application_url": application_url,
            "application_type": application_type,
            "reservation_available": reservation_available,
            "fee": safe_values["수강료"],
            "schedule_raw": safe_values["요일 /시간"],
        }
    )
    raw.update(
        {
            "detail_verified": True,
            "detail_source_status": source_status,
            "detail_operating_period": safe_values["운영기간"],
            "detail_application_period": safe_values["신청기간"],
            "detail_cancellation": safe_values["취소여부"],
            "detail_application_method": method,
            "detail_fee": safe_values["수강료"],
            "detail_schedule": safe_values["요일 /시간"],
            "detail_application_control": control_label,
            "inquiry_phone_value_never_read": True,
            "free_form_detail_never_read": True,
        }
    )
    result["raw_fields"] = raw
    return result


def _page_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = []
    for row in rows:
        raw = row.get("raw_fields", {})
        values.append(
            "\x1f".join(
                (
                    _clean(row.get("provider_course_id")),
                    _clean(row.get("title")),
                    _clean(row.get("start_date")),
                    _clean(row.get("end_date")),
                    _clean(raw.get("source_status")),
                    _clean(row.get("application_url")),
                )
            )
        )
    return hashlib.sha256("\x1e".join(values).encode("utf-8")).hexdigest()


def _detail_forms_contract(soup: BeautifulSoup) -> None:
    forms = soup.select("form")
    if len(forms) != 1:
        raise BusanJungguContractError("detail form boundary changed")
    form = forms[0]
    if (
        _clean(form.get("id")) != "gradeFrm"
        or _clean(form.get("name")) != "gradeFrm"
        or _clean(form.get("method")).casefold() != "post"
        or urlparse(_clean(form.get("action"))).path != _RATING_FORM_ACTION
    ):
        raise BusanJungguContractError("unexpected detail/application form")


def _detail_title_and_status(root: Tag) -> tuple[str, str]:
    heading = _one(root.select(":scope > dl.infor > dt"), "detail heading")
    state = _one(heading.select(":scope > span.state"), "detail state")
    source_state = _text(state)
    direct = _clean(
        " ".join(
            _clean(child)
            for child in heading.children
            if isinstance(child, NavigableString) and _clean(child)
        )
    )
    match = _YEAR_TITLE_RE.fullmatch(direct)
    if not match:
        raise BusanJungguContractError("detail title/year contract changed")
    return _clean(match.group(1)), source_state


def _detail_apply_period(value: Any) -> tuple[str, str, str]:
    match = _DETAIL_APPLY_RE.fullmatch(_clean(value))
    if not match:
        raise BusanJungguContractError("detail application period changed")
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(3))
    except ValueError as exc:
        raise BusanJungguContractError("detail application date is invalid") from exc
    if end < start:
        raise BusanJungguContractError("detail application period is reversed")
    raw = f"{start.isoformat()} {match.group(2)} ~ {end.isoformat()} {match.group(4)}"
    return start.isoformat(), end.isoformat(), raw


def _parse_detail(soup: BeautifulSoup, row: Mapping[str, Any]) -> dict[str, Any]:
    page_title = _one(soup.select("title"), "detail page title")
    if _text(page_title) != _DETAIL_TITLE:
        raise BusanJungguContractError("detail page title changed")
    _detail_forms_contract(soup)
    root = _one(soup.select("div.bbs_vtype.edu"), "education detail root")

    # Institution information lives inside the otherwise unsafe free-form
    # content area.  Read only the direct 기관명 text; never read the sibling
    # phone/address/introduction values, then destroy the whole content area.
    contents = _one(root.select(":scope > div.contents"), "detail contents boundary")
    info_blocks = contents.select("ul.edu_infor")
    facility = ""
    if info_blocks:
        info = _one(info_blocks, "institution information block")
        labels = info.select("span.name")
        label_names = tuple(_text(label) for label in labels)
        if label_names != _INSTITUTION_LABELS:
            raise BusanJungguContractError("institution/PII labels changed")
        facility = _direct_value(labels[0])
        if not facility:
            raise BusanJungguContractError("published institution name is empty")
    contents.decompose()

    title, detail_status = _detail_title_and_status(root)
    raw = dict(row.get("raw_fields", {}))
    if title != _clean(row.get("title")):
        raise BusanJungguContractError("list/detail title differs")
    expected_status = _DETAIL_STATUS_BY_LIST.get(_clean(raw.get("source_status")))
    if detail_status != expected_status:
        raise BusanJungguContractError("list/detail status differs")

    detail = _one(root.select(":scope > dl.infor > dd.edu"), "safe detail values")
    spans = detail.select("span.name")
    labels = tuple(_text(span) for span in spans)
    if labels != _DETAIL_LABELS:
        raise BusanJungguContractError("safe detail labels changed")
    safe_values: dict[str, str] = {}
    for label, span in zip(labels, spans):
        if label in _DETAIL_SKIPPED_LABELS:
            continue
        if label not in _DETAIL_SAFE_LABELS:
            raise BusanJungguContractError("unknown detail field at PII boundary")
        value = _direct_value(span)
        if not value:
            raise BusanJungguContractError(f"safe detail {label} is empty")
        safe_values[label] = value

    apply_start, apply_end, apply_raw = _detail_apply_period(safe_values["접수기간"])
    education_start, education_end = _strict_date_range(
        safe_values["교육기간"], "detail education period"
    )
    if (education_start, education_end) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ):
        raise BusanJungguContractError("list/detail education period differs")
    capacity = _TOTAL_CAPACITY_RE.fullmatch(safe_values["수강인원"])
    current = _CURRENT_CAPACITY_RE.fullmatch(safe_values["접수인원"])
    if not capacity or not current:
        raise BusanJungguContractError("detail capacity contract changed")
    detail_methods = _methods(safe_values["접수방법"])
    list_methods = _methods(row.get("application_method_raw"))
    if not detail_methods or detail_methods != list_methods:
        raise BusanJungguContractError("list/detail application methods differ")

    result = dict(row)
    result.update(
        {
            "branch": facility,
            "venue_name": facility,
            "fee": safe_values["수강료"],
            "apply_period": apply_raw,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "schedule_raw": safe_values["교육시간"],
            "target": safe_values["교육대상"],
            "capacity_total": int(capacity.group(1)),
            "capacity_current": int(current.group(1)),
        }
    )
    raw.update(
        {
            "detail_verified": True,
            "detail_source_status": detail_status,
            "source_application_period": apply_raw,
            "source_education_period": safe_values["교육기간"],
            "source_education_time": safe_values["교육시간"],
            "source_education_rounds": safe_values["교육횟수"],
            "source_fee": safe_values["수강료"],
            "source_target": safe_values["교육대상"],
            "source_facility_name": facility,
            "facility_name_published": bool(facility),
            "free_form_contents_structurally_discarded": True,
            "instructor_value_never_read": True,
            "institution_phone_address_intro_never_read": True,
            "attachments_never_read": True,
        }
    )
    result["raw_fields"] = raw
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "parser": BUSAN_JUNGGU_PARSER,
        "canonical_url": BUSAN_JUNGGU_CANONICAL_URL,
        "companion_resident_url": BUSAN_CITY_JUNGGU_URL,
        "ownership_scope": BUSAN_JUNGGU_OWNERSHIP_SCOPE,
        "owner_boundary_audit": BUSAN_JUNGGU_OWNER_BOUNDARY_AUDIT,
        "discovery_audit": BUSAN_JUNGGU_DISCOVERY_AUDIT,
        "candidate_ids": dict(BUSAN_JUNGGU_CANDIDATE_IDS),
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "district_source_rows": 0,
        "city_source_rows": 0,
        "current_source_count": 0,
        "district_current_count": 0,
        "city_current_count": 0,
        "expired_count": 0,
        "page_counts": {},
        "district_page_counts": {},
        "city_page_counts": {},
        "city_data_pages": 0,
        "source_status_counts": {},
        "source_category_counts": {},
        "current_status_counts": {},
        "current_category_counts": {},
        "current_facility_counts": {},
        "current_missing_facility_ids": [],
        "current_detail_ids": [],
        "application_control_count": 0,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
    }


def collect_busan_junggu_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 250,
    detail_limit: int = 50,
    max_requests: int = 350,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_JUNGGU_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return the atomic current/future snapshot of both official ledgers."""

    meta = _base_meta()
    if not is_busan_junggu_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact canonical Busan Jung-gu education board"
        )
        return [], BUSAN_JUNGGU_PARSER, meta
    try:
        if any(isinstance(value, bool) for value in (timeout, max_pages, detail_limit, max_requests, max_workers)):
            raise ValueError("boolean limits are invalid")
        request_timeout = max(1, int(timeout))
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        request_cap = max(0, int(max_requests))
        workers = min(max(1, int(max_workers)), BUSAN_JUNGGU_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_JUNGGU_PARSER, meta
    if page_cap < 1 or detail_cap < 0 or request_cap < 1:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "page/detail/request caps do not allow discovery"
        return [], BUSAN_JUNGGU_PARSER, meta

    fetch = fetcher or _default_fetcher
    factory = session_factory or _default_session_factory
    budget = _RequestBudget(request_cap)

    def add_fetch(result: _FetchResult) -> None:
        meta["network_retry_count"] += result.retries
        meta["sessions_created"] += result.sessions
        meta["network_requests"] = budget.count

    first_result = _fetch_many(
        ((1, busan_junggu_list_url(1)),),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=1,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(first_result)
    meta["list_requests"] = len(first_result.values)
    if first_result.errors or 1 not in first_result.values:
        meta["configured_collection_error"] = "; ".join(first_result.errors) or "missing first page"
        return [], BUSAN_JUNGGU_PARSER, meta
    try:
        first_rows, declared_total = _parse_list_page(first_result.values[1], page=1)
        data_pages = max(1, math.ceil(declared_total / BUSAN_JUNGGU_PAGE_SIZE))
        required_list_requests = data_pages + 3
        meta.update(
            {
                "data_pages": data_pages,
                "required_list_requests": required_list_requests,
            }
        )
        if data_pages > page_cap:
            raise BusanJungguContractError(
                f"max_pages cap allows {page_cap} of {data_pages} declared data pages"
            )
        if required_list_requests > request_cap:
            raise BusanJungguContractError(
                f"max_requests cap allows {request_cap} of at least "
                f"{required_list_requests} required list requests"
            )
        expected_first = min(BUSAN_JUNGGU_PAGE_SIZE, declared_total)
        if len(first_rows) != expected_first:
            raise BusanJungguContractError("first-page row count differs from declared total")
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"first page: {_clean(exc)}"
        return [], BUSAN_JUNGGU_PARSER, meta

    jobs = [
        (page, busan_junggu_list_url(page)) for page in range(2, data_pages + 2)
    ]
    remaining = _fetch_many(
        jobs,
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(remaining)
    meta["list_requests"] += len(remaining.values)
    if remaining.errors:
        meta["source_cap_reached"] = any("max_requests" in item for item in remaining.errors)
        meta["configured_collection_error"] = "; ".join(remaining.errors)
        return [], BUSAN_JUNGGU_PARSER, meta

    page_rows: dict[int, list[dict[str, Any]]] = {1: first_rows}
    page_counts: dict[int, int] = {1: len(first_rows)}
    try:
        for page in range(2, data_pages + 1):
            if page not in remaining.values:
                raise BusanJungguContractError(f"page {page} response is missing")
            rows, _ = _parse_list_page(
                remaining.values[page], page=page, expected_total=declared_total
            )
            expected = (
                BUSAN_JUNGGU_PAGE_SIZE
                if page < data_pages
                else declared_total - BUSAN_JUNGGU_PAGE_SIZE * (data_pages - 1)
            )
            if declared_total == 0:
                expected = 0
            if len(rows) != expected:
                raise BusanJungguContractError(f"page {page} row count mismatch")
            page_rows[page] = rows
            page_counts[page] = len(rows)

        sentinel_page = data_pages + 1
        sentinel_soup = remaining.values.get(sentinel_page)
        if sentinel_soup is None:
            raise BusanJungguContractError("immediate empty sentinel response is missing")
        sentinel_rows, _ = _parse_list_page(
            sentinel_soup, page=sentinel_page, expected_total=declared_total
        )
        if sentinel_rows:
            raise BusanJungguContractError("immediate post-final page is not an empty sentinel")
        _, sentinel_list = _list_contract(sentinel_soup)
        if _text(sentinel_list):
            raise BusanJungguContractError("empty sentinel list contains unexpected text")
        meta["sentinel_requests"] = 1

        listed = [row for page in range(1, data_pages + 1) for row in page_rows[page]]
        if len(listed) != declared_total:
            raise BusanJungguContractError("declared total and traversed rows differ")
        identities = [_clean(row.get("provider_course_id")) for row in listed]
        duplicates = [key for key, count in Counter(identities).items() if count > 1]
        if duplicates:
            raise BusanJungguContractError("duplicate official identities in complete archive")
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], BUSAN_JUNGGU_PARSER, meta

    district_listed = listed
    city_first_result = _fetch_many(
        (("city-first", busan_junggu_city_list_url(1)),),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=1,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(city_first_result)
    meta["list_requests"] += len(city_first_result.values)
    if city_first_result.errors or "city-first" not in city_first_result.values:
        meta["source_cap_reached"] = any(
            "max_requests" in item for item in city_first_result.errors
        )
        meta["configured_collection_error"] = (
            "; ".join(city_first_result.errors) or "missing Busan city first page"
        )
        return [], BUSAN_JUNGGU_PARSER, meta
    try:
        city_first_rows, city_last_page = _parse_city_list_page(
            city_first_result.values["city-first"], page=1
        )
        if not city_first_rows:
            raise BusanJungguContractError(
                "Busan city Jung-gu first page contains no resident-centre courses"
            )
        combined_data_pages = data_pages + city_last_page
        required_list_requests = (data_pages + 3) + (city_last_page + 3)
        meta.update(
            {
                "city_data_pages": city_last_page,
                "required_list_requests": required_list_requests,
            }
        )
        if combined_data_pages > page_cap:
            raise BusanJungguContractError(
                f"max_pages cap allows {page_cap} of {combined_data_pages} "
                "combined declared data pages"
            )
        if required_list_requests > request_cap:
            raise BusanJungguContractError(
                f"max_requests cap allows {request_cap} of at least "
                f"{required_list_requests} required list requests"
            )
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"Busan city first page: {_clean(exc)}"
        return [], BUSAN_JUNGGU_PARSER, meta

    city_remaining = _fetch_many(
        tuple(
            (f"city-page-{page}", busan_junggu_city_list_url(page))
            for page in range(2, city_last_page + 2)
        ),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(city_remaining)
    meta["list_requests"] += len(city_remaining.values)
    if city_remaining.errors:
        meta["source_cap_reached"] = any(
            "max_requests" in item for item in city_remaining.errors
        )
        meta["configured_collection_error"] = "; ".join(city_remaining.errors)
        return [], BUSAN_JUNGGU_PARSER, meta

    city_page_rows: dict[int, list[dict[str, Any]]] = {1: city_first_rows}
    city_page_counts: dict[int, int] = {1: len(city_first_rows)}
    try:
        for page in range(2, city_last_page + 1):
            key = f"city-page-{page}"
            soup = city_remaining.values.get(key)
            if soup is None:
                raise BusanJungguContractError(
                    f"Busan city page {page} response is missing"
                )
            rows, _ = _parse_city_list_page(
                soup, page=page, expected_last=city_last_page
            )
            expected_full = page < city_last_page
            if (expected_full and len(rows) != 10) or (
                not expected_full and not 1 <= len(rows) <= 10
            ):
                raise BusanJungguContractError(
                    f"Busan city page {page} row count is invalid"
                )
            city_page_rows[page] = rows
            city_page_counts[page] = len(rows)

        sentinel_page = city_last_page + 1
        sentinel_key = f"city-page-{sentinel_page}"
        sentinel_soup = city_remaining.values.get(sentinel_key)
        if sentinel_soup is None:
            raise BusanJungguContractError(
                "Busan city immediate empty sentinel response is missing"
            )
        sentinel_rows, _ = _parse_city_list_page(
            sentinel_soup, page=sentinel_page, expected_last=city_last_page
        )
        if sentinel_rows:
            raise BusanJungguContractError(
                "Busan city immediate post-final page is not empty"
            )
        _, sentinel_root = _city_list_contract(sentinel_soup, page=sentinel_page)
        if _text(sentinel_root):
            raise BusanJungguContractError(
                "Busan city empty sentinel contains unexpected text"
            )
        meta["sentinel_requests"] += 1

        city_listed = [
            row
            for page in range(1, city_last_page + 1)
            for row in city_page_rows[page]
        ]
        city_identities = [row["provider_course_id"] for row in city_listed]
        if len(city_identities) != len(set(city_identities)):
            raise BusanJungguContractError(
                "duplicate identities in Busan city Jung-gu ledger"
            )
    except Exception as exc:
        meta["configured_collection_error"] = f"Busan city ledger: {_clean(exc)}"
        return [], BUSAN_JUNGGU_PARSER, meta

    city_boundaries = (
        (1, city_last_page) if city_last_page > 1 else (1, 1)
    )
    city_rechecks = _fetch_many(
        tuple(
            (
                f"city-recheck-{page}-{index}",
                busan_junggu_city_list_url(page),
            )
            for index, page in enumerate(city_boundaries)
        ),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=2,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(city_rechecks)
    meta["list_requests"] += len(city_rechecks.values)
    if city_rechecks.errors:
        meta["source_cap_reached"] = any(
            "max_requests" in item for item in city_rechecks.errors
        )
        meta["configured_collection_error"] = "; ".join(city_rechecks.errors)
        return [], BUSAN_JUNGGU_PARSER, meta
    try:
        for index, page in enumerate(city_boundaries):
            key = f"city-recheck-{page}-{index}"
            rows, _ = _parse_city_list_page(
                city_rechecks.values[key],
                page=page,
                expected_last=city_last_page,
            )
            if _city_page_signature(rows) != _city_page_signature(
                city_page_rows[page]
            ):
                raise BusanJungguContractError(
                    f"Busan city boundary page {page} changed"
                )
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], BUSAN_JUNGGU_PARSER, meta
    meta["stability_rechecks"] += 2

    listed = district_listed + city_listed
    all_identities = [row["provider_course_id"] for row in listed]
    if len(all_identities) != len(set(all_identities)):
        meta["configured_collection_error"] = (
            "duplicate identities across the two Jung-gu official ledgers"
        )
        return [], BUSAN_JUNGGU_PARSER, meta

    meta.update(
        {
            "source_rows": len(listed),
            "district_source_rows": len(district_listed),
            "city_source_rows": len(city_listed),
            "page_counts": page_counts,
            "district_page_counts": page_counts,
            "city_page_counts": city_page_counts,
            "source_status_counts": dict(
                Counter(row["raw_fields"]["source_status"] for row in listed)
            ),
            "source_category_counts": dict(Counter(row["category"] for row in listed)),
        }
    )
    current = [row for row in listed if date.fromisoformat(row["end_date"]) >= cutoff]
    district_current = [
        row
        for row in current
        if row["raw_fields"]["source_catalog"] == "busan_junggu_district_board"
    ]
    city_current = [
        row
        for row in current
        if row["raw_fields"]["source_catalog"]
        == "busan_reserve_junggu_resident_centres"
    ]
    meta["current_source_count"] = len(current)
    meta["district_current_count"] = len(district_current)
    meta["city_current_count"] = len(city_current)
    meta["expired_count"] = len(listed) - len(current)
    meta["current_status_counts"] = dict(
        Counter(row["raw_fields"]["source_status"] for row in current)
    )
    meta["current_category_counts"] = dict(Counter(row["category"] for row in current))
    if len(current) > detail_cap:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            f"detail_limit cap allows {detail_cap} of {len(current)} current details"
        )
        return [], BUSAN_JUNGGU_PARSER, meta
    minimum_remaining = len(current) + 2
    if budget.count + minimum_remaining > request_cap:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            f"max_requests cap cannot cover {len(current)} details and two stability rechecks"
        )
        return [], BUSAN_JUNGGU_PARSER, meta

    detail_jobs = [
        (
            (
                row["raw_fields"]["source_catalog"],
                row["provider_course_id"],
            ),
            row["raw_url"],
        )
        for row in current
    ]
    details = _fetch_many(
        detail_jobs,
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(details)
    if details.errors:
        meta["source_cap_reached"] = any("max_requests" in item for item in details.errors)
        meta["configured_collection_error"] = "; ".join(details.errors)
        return [], BUSAN_JUNGGU_PARSER, meta
    verified: list[dict[str, Any]] = []
    try:
        for row in current:
            identity = row["provider_course_id"]
            source_catalog = row["raw_fields"]["source_catalog"]
            soup = details.values.get((source_catalog, identity))
            if soup is None:
                raise BusanJungguContractError(f"detail {identity} response is missing")
            if source_catalog == "busan_junggu_district_board":
                verified.append(_parse_detail(soup, row))
            elif source_catalog == "busan_reserve_junggu_resident_centres":
                verified.append(_parse_city_detail(soup, row))
            else:
                raise BusanJungguContractError(
                    f"detail {identity} has an unknown source catalogue"
                )
    except Exception as exc:
        meta["configured_collection_error"] = f"detail contract: {_clean(exc)}"
        return [], BUSAN_JUNGGU_PARSER, meta
    meta["detail_pages"] = len(verified)

    boundary_pages = (1, data_pages) if data_pages > 1 else (1, 1)
    rechecks = _fetch_many(
        tuple((f"recheck-{page}-{index}", busan_junggu_list_url(page)) for index, page in enumerate(boundary_pages)),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=2,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(rechecks)
    meta["list_requests"] += len(rechecks.values)
    if rechecks.errors:
        meta["source_cap_reached"] = any("max_requests" in item for item in rechecks.errors)
        meta["configured_collection_error"] = "; ".join(rechecks.errors)
        return [], BUSAN_JUNGGU_PARSER, meta
    try:
        for index, page in enumerate(boundary_pages):
            key = f"recheck-{page}-{index}"
            rows, _ = _parse_list_page(
                rechecks.values[key], page=page, expected_total=declared_total
            )
            label = "first-page" if index == 0 else "last-page"
            if _page_signature(rows) != _page_signature(page_rows[page]):
                raise BusanJungguContractError(f"{label} stability recheck changed")
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], BUSAN_JUNGGU_PARSER, meta
    meta["stability_rechecks"] += 2

    if dedupe_rows is not None:
        try:
            deduped = list(dedupe_rows(list(verified)))
        except Exception as exc:
            meta["configured_collection_error"] = f"dedupe failed: {_clean(exc)}"
            return [], BUSAN_JUNGGU_PARSER, meta
        before = [row["provider_course_id"] for row in verified]
        after = [row.get("provider_course_id") for row in deduped]
        if before != after:
            meta["configured_collection_error"] = (
                "dedupe changed canonical current course identities"
            )
            return [], BUSAN_JUNGGU_PARSER, meta
        verified = deduped

    missing_facility = [
        row["provider_course_id"] for row in verified if not _clean(row.get("branch"))
    ]
    meta.update(
        {
            "pages": meta["list_requests"] + meta["detail_pages"],
            "current_detail_ids": [row["provider_course_id"] for row in verified],
            "current_facility_counts": dict(
                Counter(row["branch"] for row in verified if row["branch"])
            ),
            "current_missing_facility_ids": missing_facility,
            "application_control_count": sum(
                bool(row.get("application_url")) for row in verified
            ),
            "network_requests": budget.count,
            "snapshot_complete": True,
            "no_current_data": not verified,
            "configured_collection_error": "",
        }
    )
    return verified, BUSAN_JUNGGU_PARSER, meta


collect = collect_busan_junggu_education
is_target = is_busan_junggu_education_target
