"""Fail-closed education collector for Busan Buk-gu's official ledgers.

The district integrated-reservation site exposes four education catalogues:
information education, lifelong-learning programmes, library programmes, and
the Siranggol small-library programme.  Its resident-council menu redirects to
the Busan integrated-reservation site, so that source is audited through the
exact ``srchGugun=8``/``srchResveInsttCd=33`` partition instead of through an
unfiltered city page.

Two source defects are deliberately part of the contract.  The lifelong list
declares more rows than its status partitions can render and its final two
advertised pages are empty JSP shells.  We therefore census the unfiltered
list and every official status partition, prove their exact identity set
relationships, and keep the declared-but-unrendered gap visible in metadata.
The library list advertises a calculated final page before all rows have been
rendered.  Collection continues until unique ``programIdx`` values equal the
declared total and only then accepts the immediate empty sentinel.  Library
cards expose only the first class date for multi-session programmes.  Every
programme beginning in the current calendar year is therefore detail-probed
before the current/future cut is applied.  A full audit of all 340 programmes
beginning in 2025 found no cross-year period; the current-year boundary is
kept explicit in metadata instead of silently treating the card date as an
end date.

The Busan Lifelong Learning Platform office ``OFFICE_00002650`` contains both
native ``LEARNING_*`` rows and external Buk-gu ``programIdx`` links.  Exact
external identities are suppressed as duplicates of the district owner;
native identities remain independent.  ``OFFICE_00002800`` is also audited
and is currently empty.  Current/future details are mandatory, application
controls are retained only after identity binding, and application forms,
account/history pages, applicant values, instructors, contacts, attachments,
and free-form detail values are never fetched or read.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, NavigableString, Tag

from Crawler import municipal_busan_lifelong as _lifelong
from Crawler import municipal_busan_namgu as _transport


BUSAN_BUKGU_PROVIDER = "MUNI_WWW_BSBUKGU_GO_KR_E60701D6"
BUSAN_BUKGU_HOME_PROVIDER = "MUNI_WWW_BSBUKGU_GO_KR_D974304A"
BUSAN_BUKGU_LIFELONG_DETAIL_PROVIDER = "MUNI_WWW_BSBUKGU_GO_KR_141AA5C4"
BUSAN_BUKGU_LIBRARY_PROVIDER = "MUNI_WWW_BSBUKGU_GO_KR_2BDDF955"
BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER

BUSAN_BUKGU_MUNICIPALITY_CODE = "2632000000"
BUSAN_BUKGU_MUNICIPALITY_NAME = "부산광역시 북구"
BUSAN_BUKGU_HOST = "www.bsbukgu.go.kr"
BUSAN_BUKGU_PATH = "/reservation/index.bsbukgu"
BUSAN_BUKGU_URL = f"https://{BUSAN_BUKGU_HOST}{BUSAN_BUKGU_PATH}"
BUSAN_BUKGU_CANONICAL_URL = BUSAN_BUKGU_URL
BUSAN_BUKGU_HOME_URL = "https://www.bsbukgu.go.kr/"

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_BUKGU_GUGUN = "8"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_BUKGU_URL = f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
    (
        ("curPage", "1"),
        ("srchGugun", BUSAN_CITY_BUKGU_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    )
)

BUSAN_LIFELONG_BUKGU_OFFICES = (
    "OFFICE_00002650",
    "OFFICE_00002800",
)
BUSAN_LIFELONG_BUKGU_OFFICE_NAMES = {
    "OFFICE_00002650": "북구청",
    "OFFICE_00002800": "북구평생학습관",
}
BUSAN_LIFELONG_PAGE_SIZE = 1000

BUSAN_BUKGU_PAGE_SIZE = 20
BUSAN_BUKGU_FETCH_ATTEMPTS = 3
BUSAN_BUKGU_MAX_WORKERS = 8
BUSAN_BUKGU_MAX_HTML_BYTES = 4_000_000
BUSAN_BUKGU_DECLARED_UNRENDERED_LIFELONG_ROWS = 19
BUSAN_BUKGU_DEFAULT_ERROR_SHELL_COUNT = 2
BUSAN_BUKGU_DEFAULT_PARTITION_RECOVERY_ROWS = 7
BUSAN_BUKGU_DETAIL_RECHECK_REQUESTS = 13

BUSAN_BUKGU_PARSER = (
    "busan_bukgu_four_district_ledgers_declared_unique_complete+lifelong_"
    "default_and_status_partition_identity_union+audited_unrendered_gap+"
    "library_beyond_advertised_last_until_declared_total+current_year_"
    "detail_projection_recovery+prior_year_no_cross_year_audit+sentinels+stable_"
    "boundaries+busan_city_gugun8_office33_complete+empty_sentinel+current_"
    "details+lifelong_"
    "offices00002650_00002800_two_semantic_censuses+external_programidx_"
    "duplicate_suppression+native_current_details+identity_bound_apply_no_"
    "form_fetch+pii_allowlist+atomic_snapshot"
)
BUSAN_BUKGU_OWNERSHIP_SCOPE = (
    "busan_bukgu_complete_integrated_reservation_education_and_native_lifelong_platform_courses"
)

BUSAN_BUKGU_CANDIDATE_IDS: Mapping[str, str] = {
    "canonical_integrated_reservation": "MUNI_IR_8A228D1C0236",
    "district_home_alias": "MUNI_IR_1F166A3322CD",
    "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
    "busan_city_occation_notice": "MUNI_IR_E6A435D3EB6B",
}

BUSAN_BUKGU_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_BUKGU_PROVIDER: {
        "decision": "canonical_complete_district_education_owner",
        "candidate_id": BUSAN_BUKGU_CANDIDATE_IDS["canonical_integrated_reservation"],
        "url": BUSAN_BUKGU_CANONICAL_URL,
        "identity_rule": "ledger menuCd plus lectureIdx/programIdx",
    },
    BUSAN_BUKGU_HOME_PROVIDER: {
        "decision": "duplicate_home_alias_of_integrated_reservation_owner",
        "candidate_id": BUSAN_BUKGU_CANDIDATE_IDS["district_home_alias"],
        "url": BUSAN_BUKGU_HOME_URL,
    },
    BUSAN_BUKGU_LIFELONG_DETAIL_PROVIDER: {
        "decision": "duplicate_single_detail_of_complete_lifelong_ledger",
        "url": (f"{BUSAN_BUKGU_URL}?menuCd=DOM_000001801002000000&mode=view&programIdx=2142"),
    },
    BUSAN_BUKGU_LIBRARY_PROVIDER: {
        "decision": "duplicate_subledger_of_complete_integrated_owner",
        "url": (f"{BUSAN_BUKGU_URL}?menuCd=DOM_000001801003000000"),
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "suppress_external_programidx_keep_native_learning_ids",
        "candidate_id": BUSAN_BUKGU_CANDIDATE_IDS["busan_lifelong_federation"],
        "office_codes": BUSAN_LIFELONG_BUKGU_OFFICES,
        "identity_rule": ("external bsbukgu programIdx belongs to district owner; LEARNING_* remains independent"),
    },
    "OFFICIAL_BUSAN_CITY_RESERVATION": {
        "decision": "collect_exact_bukgu_resident_council_partition",
        "url": BUSAN_CITY_BUKGU_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_BUKGU_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
        "observed_rows": 1,
    },
    "PRIVATE_AND_NON_EDUCATION_BOUNDARY": {
        "decision": "never_fetch",
        "excluded": (
            "experience, performance, facility, my-reservation, account, application-form, and applicant-list routes"
        ),
    },
}

BUSAN_BUKGU_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-29",
    "canonical_url": BUSAN_BUKGU_CANONICAL_URL,
    "information_rows": 2,
    "lifelong_declared_rows": 1406,
    "lifelong_default_rendered_rows": 1380,
    "lifelong_status_union_rows": 1387,
    "lifelong_declared_unrendered_rows": 19,
    "lifelong_status_partition_recovered_rows": 7,
    "lifelong_default_error_shell_pages": (70, 71),
    "lifelong_normal_empty_sentinel_page": 72,
    "library_rows": 4345,
    "library_advertised_last_page": 218,
    "library_actual_data_pages": 223,
    "library_empty_sentinel_page": 224,
    "small_library_rows": 31,
    "small_library_identity_overlap_rows": 0,
    "library_list_projected_current_rows": 44,
    "library_current_year_detail_probe_rows": 215,
    "library_projection_recovered_current_rows": 15,
    "library_current_rows": 54,
    "library_prior_year_detail_audit_rows": 340,
    "library_prior_year_cross_year_rows": 0,
    "library_prior_year_max_end": "2025-12-28",
    "library_prior_year_max_duration_days": 336,
    "small_library_current_year_detail_probe_rows": 9,
    "small_library_projection_recovered_current_rows": 2,
    "small_library_current_rows": 3,
    "district_current_rows_before_test_exclusion": 71,
    "district_exact_test_rows_excluded": 1,
    "district_current_rows": 70,
    "platform_office_rows": {
        "OFFICE_00002650": 32,
        "OFFICE_00002800": 0,
    },
    "platform_external_duplicate_rows": 20,
    "platform_native_rows": 12,
    "platform_native_current_rows": 8,
    "busan_city_resident_rows": 1,
    "atomic_current_rows": 79,
}

BUSAN_BUKGU_PII_FIELDS_NEVER_READ = (
    "instructor and contact values",
    "application and applicant forms",
    "account and reservation-history values",
    "attachments and filenames",
    "free-form introductions, notes, plans, and descriptions",
    "platform enrolment counts and hidden telephone fields",
)


class BusanBukguContractError(ValueError):
    """Raised when an audited Buk-gu source contract changes."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Probe = Callable[[BeautifulSoup], None]


@dataclass(frozen=True)
class _LocalLedger:
    key: str
    name: str
    menu: str
    identity_key: str
    category: str
    branch: str


_INFORMATION = _LocalLedger(
    "information",
    "정보화교육",
    "DOM_000001801001000000",
    "lectureIdx",
    "정보화교육",
    "북구 정보화교육",
)
_LIFELONG = _LocalLedger(
    "lifelong",
    "평생학습프로그램",
    "DOM_000001801002000000",
    "programIdx",
    "평생학습",
    "북구 평생학습",
)
_LIBRARY = _LocalLedger(
    "library",
    "도서관프로그램",
    "DOM_000001801003000000",
    "programIdx",
    "도서관프로그램",
    "북구 도서관",
)
_SMALL_LIBRARY = _LocalLedger(
    "small_library",
    "시랑골 아이누리 작은 도서관",
    "DOM_000001801006000000",
    "programIdx",
    "도서관프로그램",
    "시랑골 아이누리 작은도서관",
)
BUSAN_BUKGU_LOCAL_LEDGERS = (
    _INFORMATION,
    _LIFELONG,
    _LIBRARY,
    _SMALL_LIBRARY,
)
_LEDGER_BY_KEY = {ledger.key: ledger for ledger in BUSAN_BUKGU_LOCAL_LEDGERS}

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_LEARNING_ID_RE = re.compile(r"^LEARNING_\d{8}$")
_TOTAL_RE = re.compile(r"^총\s*([\d,]+)\s*건의\s*게시물이\s*있습니다$")
_PAGE_ACTION_RE = re.compile(r"^linkPage\(\s*(\d+)\s*\);\s*return\s+false;$")
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
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*"
    r"(\d{1,2})(?:\.)?(?!\d)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[-\s*]+[\d*]{3,4}[-\s*]+[\d*]{4}|"
    r"0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LOCAL_STATUS_MAP = {
    "접수중": "OPEN",
    "대기접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "접수종료": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
}
_LIFELONG_PARTITION_STATUSES = {
    "": frozenset(_LOCAL_STATUS_MAP),
    "ing": frozenset({"접수중", "대기접수중", "접수마감"}),
    "wait": frozenset({"접수대기"}),
    "close": frozenset({"교육중", "교육종료"}),
}
_CITY_LIST_TITLE = "강좌/교육 : 부산광역시 통합예약"
_CITY_CARD_LABELS = ("기관", "대상", "장소", "일자", "방법", "문의")
_CITY_STATUS_MAP = {
    "대기중": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}
_CITY_DETAIL_REQUIRED_LABELS = (
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
_CITY_DETAIL_SAFE_LABELS = frozenset(_CITY_DETAIL_REQUIRED_LABELS) - {"문의전화"}
_CITY_DETAIL_SKIPPED_LABELS = frozenset({"문의전화", "첨부파일"})
_LOCAL_LIST_LABELS = {
    "information": ("교육기간", "접수인원", "대기인원", "모집인원"),
    "lifelong": (
        "신청기간",
        "교육기간",
        "온라인접수",
        "전화접수",
        "방문접수",
        "교육대상",
    ),
    "library": ("신청기간", "수강일자", "정원/현재원"),
    "small_library": ("신청기간", "수강일자", "정원/현재원"),
}
_LIBRARY_OPTIONAL_LABEL = "강의장소"
_AUDITED_REVERSED_LOCAL_RANGES = {
    ("lifelong", "2630", "education"): "2025-10-15 ~ 2025-09-15",
    ("lifelong", "2397", "education"): "2024-05-18 ~ 2024-04-18",
    ("lifelong", "2152", "application"): ("2022-12-20(10:00) ~ 2022-12-05(17:00)"),
    ("library", "1056555", "application"): "2022-09-28 ~ 2022-09-23",
    ("library", "504061", "application"): "2019-12-12 ~ 2019-01-18",
    ("library", "502139", "education"): ("2017-05-02 ~ 2017-04-19 (10:00 ~ 12:00 (10일 과정))"),
    ("library", "501975", "education"): ("2015-01-02 ~ 2014-12-23 (14:00~16:00)"),
}
_AUDITED_LIBRARY_APPLICATION_TYPO = {
    "identity": "1104574",
    "value": "2026-06-30 ~ 2206-07-24",
    "normalized_end": "2026-07-24",
    "detail_value": "2026-06-30 10:00 ~ 2206-07-24 17:00",
}
_AUDITED_LIBRARY_DETAIL_COURSE_TYPO = {
    "identity": "1098371",
    "value": "2026-01-08 ~ 2025-12-03",
    "normalized_end": "2026-12-03",
}
_AUDITED_UNBOUND_LIBRARY_APPLICATION = {
    "identity": "1102558",
    "label": "프로그램신청",
    "url": ("https://www.bsbukgu.go.kr/mdlib/index.bsbukgu?menuCd=DOM_000001104002004002"),
}
_AUDITED_TEST_COURSE = {
    "identity": "2234",
    "title": "테스트 강좌(연습용)",
    "start_date": "2026-12-01",
    "end_date": "2026-12-31",
    "source_status": "접수중",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""


def _one(values: Iterable[Any], label: str) -> Any:
    found = list(values)
    if len(found) != 1:
        raise BusanBukguContractError(f"expected one {label}, found {len(found)}")
    return found[0]


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


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
    return f"https://{parsed.hostname.rstrip('.').lower()}{_normal_path(parsed.path)}" + (f"?{query}" if query else "")


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise BusanBukguContractError(f"{label} may not be boolean")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BusanBukguContractError(f"invalid {label}") from exc
    if result < 1:
        raise BusanBukguContractError(f"{label} must be positive")
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_busan_bukgu_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == BUSAN_BUKGU_PROVIDER
        and _compare_url(_target_value(target, "url")) == _compare_url(BUSAN_BUKGU_CANONICAL_URL)
    )


is_target = is_busan_bukgu_education_target


def busan_bukgu_list_url(
    ledger: _LocalLedger | str,
    page: int = 1,
    *,
    register_status: str = "",
) -> str:
    item = _LEDGER_BY_KEY.get(ledger) if isinstance(ledger, str) else ledger
    if item not in BUSAN_BUKGU_LOCAL_LEDGERS:
        raise BusanBukguContractError("unknown Buk-gu local ledger")
    current = _positive_int(page, "local page")
    pairs: list[tuple[str, Any]] = [("menuCd", item.menu)]
    if item is _LIFELONG and register_status:
        if register_status not in {"ing", "wait", "close"}:
            raise BusanBukguContractError("unknown lifelong status partition")
        pairs.append(("registerStatus", register_status))
    pairs.append(("page", current))
    return f"{BUSAN_BUKGU_URL}?" + urlencode(pairs)


def busan_bukgu_detail_url(ledger: _LocalLedger | str, identity: Any) -> str:
    item = _LEDGER_BY_KEY.get(ledger) if isinstance(ledger, str) else ledger
    if item not in BUSAN_BUKGU_LOCAL_LEDGERS:
        raise BusanBukguContractError("unknown Buk-gu local ledger")
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise BusanBukguContractError("invalid Buk-gu local identity")
    return f"{BUSAN_BUKGU_URL}?" + urlencode((("menuCd", item.menu), ("mode", "view"), (item.identity_key, value)))


def busan_bukgu_lifelong_list_url(office_code: str, page: int = 1) -> str:
    if office_code not in BUSAN_LIFELONG_BUKGU_OFFICES:
        raise BusanBukguContractError("unknown Buk-gu lifelong office")
    current = _positive_int(page, "lifelong page")
    payload = _lifelong._list_payload(office_code, current)
    payload["pageUnit"] = str(BUSAN_LIFELONG_PAGE_SIZE)
    return _lifelong.BUSAN_LIFELONG_LIST_URL + "?" + urlencode(payload)


def busan_bukgu_lifelong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _LEARNING_ID_RE.fullmatch(value):
        raise BusanBukguContractError("invalid native lifelong identity")
    return _lifelong.busan_lifelong_detail_url(value)


def busan_bukgu_city_list_url(page: int = 1) -> str:
    current = _positive_int(page, "city page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (
            ("curPage", current),
            ("srchGugun", BUSAN_CITY_BUKGU_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )


def busan_bukgu_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _clean(group_id)
    program = _clean(program_id)
    if not _IDENTITY_RE.fullmatch(group) or not _IDENTITY_RE.fullmatch(program):
        raise BusanBukguContractError("invalid Busan city course identity")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def canonical_busan_bukgu_course_identity(value: Any) -> str:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_BUKGU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_BUKGU_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"menuCd", "mode", "programIdx"}
        or query.get("menuCd") != [_LIFELONG.menu]
        or query.get("mode") != ["view"]
    ):
        return ""
    identity = _clean(query.get("programIdx", [""])[0])
    return identity if _IDENTITY_RE.fullmatch(identity) else ""


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError as exc:
            raise BusanBukguContractError("invalid source date") from exc
    return result


def _strict_range(
    value: Any,
    *,
    ledger: _LocalLedger,
    identity: str,
    kind: str,
) -> tuple[str, str, bool]:
    raw = _clean(value)
    found = _dates(raw)
    if len(found) != 2:
        if (
            ledger is _LIBRARY
            and identity == _AUDITED_LIBRARY_APPLICATION_TYPO["identity"]
            and kind == "application"
            and raw == _AUDITED_LIBRARY_APPLICATION_TYPO["value"]
            and len(found) == 1
        ):
            return (
                found[0].isoformat(),
                _AUDITED_LIBRARY_APPLICATION_TYPO["normalized_end"],
                True,
            )
        raise BusanBukguContractError(f"{ledger.key} {identity} {kind} range changed")
    reversed_source = found[1] < found[0]
    if reversed_source:
        expected = _AUDITED_REVERSED_LOCAL_RANGES.get((ledger.key, identity, kind))
        if raw != expected:
            raise BusanBukguContractError(f"{ledger.key} {identity} new reversed {kind} range")
        found = sorted(found)
    return found[0].isoformat(), found[1].isoformat(), reversed_source


def _library_course_range(
    value: Any,
    *,
    identity: str,
    source_status: str,
    cutoff: date,
) -> tuple[str, str, bool, bool]:
    raw = _clean(value)
    found = _dates(raw)
    unique: list[date] = []
    for item in found:
        if not unique or item != unique[-1]:
            unique.append(item)
    if len(unique) == 1:
        return unique[0].isoformat(), unique[0].isoformat(), False, False
    if len(unique) == 2:
        if unique[1] < unique[0]:
            expected = _AUDITED_REVERSED_LOCAL_RANGES.get(("library", identity, "education"))
            if raw != expected:
                raise BusanBukguContractError(f"library {identity} new reversed course range")
            unique = sorted(unique)
            return unique[0].isoformat(), unique[1].isoformat(), False, True
        return unique[0].isoformat(), unique[1].isoformat(), False, False
    years = [int(value) for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", raw)]
    if source_status != "접수종료" or (years and max(years) >= cutoff.year - 1):
        raise BusanBukguContractError(f"library {identity} recent/unclosed course date is ambiguous")
    return "", "", True, False


def _local_href_identity(value: Any, *, ledger: _LocalLedger, page: int) -> str:
    parsed = urlparse(urljoin(BUSAN_BUKGU_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_BUKGU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_BUKGU_PATH
        or parsed.params
        or parsed.fragment
        or query.get("menuCd") != [ledger.menu]
        or query.get("mode") != ["view"]
    ):
        raise BusanBukguContractError("unsafe Buk-gu detail link")
    identity = _clean(query.get(ledger.identity_key, [""])[0])
    if not _IDENTITY_RE.fullmatch(identity):
        raise BusanBukguContractError("malformed Buk-gu list identity")
    expected = {"menuCd", "mode", ledger.identity_key}
    if ledger is _INFORMATION:
        expected.add("page")
        if query.get("page") != [str(page)]:
            raise BusanBukguContractError("information detail page binding changed")
    elif ledger is _LIBRARY:
        expected.add("page")
        if query.get("page") != [str(page)]:
            raise BusanBukguContractError("library detail page binding changed")
    elif ledger is _SMALL_LIBRARY:
        numeric = [key for key in query if key.isdigit()]
        if numeric != [str(page)] or query.get(str(page)) != [""]:
            raise BusanBukguContractError("small-library page binding changed")
        expected.add(str(page))
    if set(query) != expected:
        raise BusanBukguContractError("Buk-gu detail query shape changed")
    return identity


def _local_total_and_advertised_last(
    soup: BeautifulSoup,
    *,
    ledger: _LocalLedger,
    page: int,
    register_status: str,
) -> tuple[int, int]:
    expected_title = f"교육/강좌 < {ledger.name}"
    if _text(_one(soup.select("title"), "local list title")) != expected_title:
        raise BusanBukguContractError(f"{ledger.key} page title changed")
    total_node = _one(soup.select(".board-top .total"), "local total")
    match = _TOTAL_RE.fullmatch(_text(total_node))
    if not match:
        raise BusanBukguContractError(f"{ledger.key} total declaration changed")
    total = int(match.group(1).replace(",", ""))
    form = _one(soup.select(".board-top .search form"), "local search form")
    action = urlparse(urljoin(BUSAN_BUKGU_URL, _clean(form.get("action"))))
    expected_method = "get" if ledger is _LIBRARY else "post"
    action_query = parse_qs(action.query, keep_blank_values=True)
    if (
        _clean(form.get("method")).casefold() != expected_method
        or (action.hostname or "").rstrip(".").lower() != BUSAN_BUKGU_HOST
        or _normal_path(action.path) != BUSAN_BUKGU_PATH
        or action_query != {"menuCd": [ledger.menu], "mode": ["list"]}
    ):
        raise BusanBukguContractError(f"{ledger.key} search form changed")
    if ledger is _LIBRARY:
        hidden_menu = form.select("input[type='hidden'][name='menuCd']")
        if len(hidden_menu) != 1 or _clean(hidden_menu[0].get("value")) != ledger.menu:
            raise BusanBukguContractError("library search binding changed")
    selector = _one(form.select("select[name='registerStatus']"), "status selector")
    selected = selector.select("option[selected]")
    selected_value = _clean(selected[0].get("value")) if len(selected) == 1 else ""
    if ledger is _LIFELONG and register_status:
        if selected_value != register_status:
            raise BusanBukguContractError("lifelong status partition changed")
    elif selected_value:
        raise BusanBukguContractError("unexpected local status filter")
    values: set[int] = set()
    for link in soup.select(".pageing a[onclick]"):
        action_text = _clean(link.get("onclick"))
        page_match = _PAGE_ACTION_RE.fullmatch(action_text)
        if not page_match:
            raise BusanBukguContractError("local pagination action changed")
        values.add(int(page_match.group(1)))
    expected_last = max(1, math.ceil(total / BUSAN_BUKGU_PAGE_SIZE))
    current_markers = [int(_text(node)) for node in soup.select(".pageing strong") if _text(node).isdigit()]
    if (
        any(value > max(expected_last, page) for value in values)
        or len(current_markers) > 1
        or (current_markers and current_markers != [page])
        or (expected_last > 1 and expected_last not in values and current_markers != [expected_last])
    ):
        raise BusanBukguContractError(f"{ledger.key} advertised last page contradicts total")
    return total, expected_last


def _local_values(item: Tag) -> tuple[tuple[str, ...], dict[str, str]]:
    labels: list[str] = []
    values: dict[str, str] = {}
    for pair in item.select(".inlec > p"):
        heading = _one(pair.find_all("strong", recursive=False), "card label")
        value = _one(pair.find_all("span", recursive=False), "card value")
        label = _text(heading)
        if not label or label in values:
            raise BusanBukguContractError("duplicate/empty local card label")
        labels.append(label)
        values[label] = _text(value)
    return tuple(labels), values


def _library_branch(item: Tag, ledger: _LocalLedger) -> str:
    if ledger not in {_LIBRARY, _SMALL_LIBRARY}:
        return ledger.branch
    node = _one(item.select(".btxt .lib_name"), "library branch")
    token = _text(node).strip("() ")
    if not token or len(token) > 30:
        raise BusanBukguContractError("library branch changed")
    known = {
        "덕천": "덕천도서관",
        "금곡": "금곡도서관",
        "화명": "화명도서관",
        "만덕": "만덕도서관",
        "시랑골": "시랑골 아이누리 작은도서관",
        "솔밭": "솔밭작은도서관",
        "상학": "상학작은도서관",
    }
    return known.get(token, f"북구 {token} 도서관")


def _parse_local_page(
    soup: BeautifulSoup,
    *,
    ledger: _LocalLedger,
    page: int,
    cutoff: date,
    register_status: str = "",
    expected_total: Optional[int] = None,
    expected_advertised_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    total, advertised_last = _local_total_and_advertised_last(
        soup, ledger=ledger, page=page, register_status=register_status
    )
    if expected_total is not None and total != expected_total:
        raise BusanBukguContractError(f"{ledger.key} total changed between pages")
    if expected_advertised_last is not None and advertised_last != expected_advertised_last:
        raise BusanBukguContractError(f"{ledger.key} advertised last changed between pages")
    roots = soup.select(".courseList-wrap > ul")
    if roots:
        root = _one(roots, "course list")
        items = root.find_all("li", recursive=False)
    else:
        items = []
    rows: list[dict[str, Any]] = []
    allowed_statuses = (
        _LIFELONG_PARTITION_STATUSES[register_status] if ledger is _LIFELONG else frozenset(_LOCAL_STATUS_MAP)
    )
    for position, item in enumerate(items, 1):
        link = _one(item.select(":scope > a[href]"), "local course link")
        identity = _local_href_identity(link.get("href"), ledger=ledger, page=page)
        title = _text(_one(link.select(".btxt .tit"), "local course title"))
        source_status = _text(_one(link.select(".btxt .state"), "local course status"))
        if not title or source_status not in allowed_statuses:
            raise BusanBukguContractError(f"{ledger.key} {identity} title/status changed")
        labels, values = _local_values(item)
        expected_labels = _LOCAL_LIST_LABELS[ledger.key]
        if ledger in {_LIBRARY, _SMALL_LIBRARY}:
            allowed_labels = {
                expected_labels,
                (*expected_labels, _LIBRARY_OPTIONAL_LABEL),
            }
            if labels not in allowed_labels:
                raise BusanBukguContractError(f"{ledger.key} {identity} card fields changed")
        elif labels != expected_labels:
            raise BusanBukguContractError(f"{ledger.key} {identity} card fields changed")

        apply_start = apply_end = ""
        start = end = ""
        historical_unparseable = False
        corrected_source = False
        if ledger is _INFORMATION:
            start, end, corrected_source = _strict_range(
                values["교육기간"],
                ledger=ledger,
                identity=identity,
                kind="education",
            )
        elif ledger is _LIFELONG:
            apply_start, apply_end, corrected_apply = _strict_range(
                values["신청기간"],
                ledger=ledger,
                identity=identity,
                kind="application",
            )
            start, end, corrected_education = _strict_range(
                values["교육기간"],
                ledger=ledger,
                identity=identity,
                kind="education",
            )
            corrected_source = corrected_apply or corrected_education
        else:
            try:
                apply_start, apply_end, corrected_source = _strict_range(
                    values["신청기간"],
                    ledger=ledger,
                    identity=identity,
                    kind="application",
                )
            except BusanBukguContractError:
                years = [int(value) for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", values["신청기간"])]
                if source_status != "접수종료" or (years and max(years) >= cutoff.year - 1):
                    raise
                corrected_source = False
            (
                start,
                end,
                historical_unparseable,
                corrected_course,
            ) = _library_course_range(
                values["수강일자"],
                identity=identity,
                source_status=source_status,
                cutoff=cutoff,
            )
            corrected_source = corrected_source or corrected_course
        branch = _library_branch(item, ledger)
        venue = values.get(_LIBRARY_OPTIONAL_LABEL) or branch
        raw_url = busan_bukgu_detail_url(ledger, identity)
        row = {
            "provider": BUSAN_BUKGU_PROVIDER,
            "provider_course_id": (f"{BUSAN_BUKGU_PROVIDER}:{ledger.key}:{identity}"),
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "description": title,
            "branch": branch,
            "branch_code": (f"bukgu-{ledger.key}-" + hashlib.sha256(branch.encode("utf-8")).hexdigest()[:10]),
            "preserve_branch": True,
            "category": ledger.category,
            "program_type": "교육/강좌",
            "raw_url": raw_url,
            "application_url": "",
            "application_type": "INFO_ONLY",
            "reservation_available": False,
            "status": _LOCAL_STATUS_MAP[source_status],
            "period": f"{start} ~ {end}" if start and end else "",
            "start_date": start,
            "end_date": end,
            "apply_period": (f"{apply_start} ~ {apply_end}" if apply_start and apply_end else ""),
            "apply_start": apply_start,
            "apply_end": apply_end,
            "schedule_raw": (values.get("교육대상", "") if ledger is _LIFELONG else values.get("수강일자", "")),
            "target": "",
            "fee": "",
            "venue_name": venue,
            "provider_organizer": branch,
            "municipality_code": BUSAN_BUKGU_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_BUKGU_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "북구",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": "complete_html_pages+current_detail_allowlist",
            "raw_fields": {
                "parser": BUSAN_BUKGU_PARSER,
                "source_catalog": f"busan_bukgu_{ledger.key}",
                "source_ledger": ledger.key,
                "source_menu": ledger.menu,
                "source_identity": identity,
                "source_page": page,
                "source_position": position,
                "source_status": source_status,
                "source_partition": register_status or "all",
                "source_application_period": values.get("신청기간", ""),
                "source_education_period": (values.get("교육기간") or values.get("수강일자", "")),
                "source_date_correction": corrected_source,
                "historical_unparseable_course_date": historical_unparseable,
                "detail_verified": False,
                "application_form_fetched": False,
                "applicant_list_fetched": False,
                "reservation_history_fetched": False,
                "service_family": "education",
            },
        }
        rows.append(row)
    return rows, total, advertised_last


def _is_lifelong_error_shell(soup: BeautifulSoup) -> bool:
    title = soup.select("title")
    roots = soup.select("#conts")
    return bool(
        len(title) == 1
        and _text(title[0]) == f"교육/강좌 < {_LIFELONG.name}"
        and len(roots) == 1
        and not _text(roots[0])
        and not roots[0].find(True)
        and not soup.select(".board-top, .courseList-wrap")
    )


def _local_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("raw_fields", {}).get("source_ledger")),
            _clean(row.get("raw_fields", {}).get("source_identity")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


_LIFELONG_DETAIL_SAFE_LABELS = frozenset(
    {
        "교육장소",
        "교육대상",
        "신청기간",
        "교육기간",
        "교육시간",
        "교육요일/횟수",
        "수강자부담",
    }
)
_LIFELONG_DETAIL_SKIPPED_LABELS = frozenset({"모집인원", "첨부파일", "강좌소개"})
_LIBRARY_DETAIL_SAFE_LABELS = frozenset({"수강기간", "수강일시", "장소", "신청상태", "신청기간", "수강료"})
_LIBRARY_DETAIL_SKIPPED_LABELS = frozenset({"정원/현재원", "추가인원", "강사명", "강의계획서", "비고"})


def _validate_local_detail_url(final_url: str, *, ledger: _LocalLedger, identity: str) -> None:
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_BUKGU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_BUKGU_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"menuCd", "mode", ledger.identity_key}
        or query.get("menuCd") != [ledger.menu]
        or query.get("mode") != ["view"]
        or query.get(ledger.identity_key) != [identity]
    ):
        raise BusanBukguContractError(f"{ledger.key} detail response left identity scope")


def _detail_table_values(
    table: Tag,
    *,
    safe_labels: frozenset[str],
    skipped_labels: frozenset[str],
) -> tuple[dict[str, str], set[str], int]:
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    free_form_rows = 0
    for row in table.select(":scope > tbody > tr"):
        headings = row.find_all("th", recursive=False)
        values = row.find_all("td", recursive=False)
        if not headings:
            if len(values) != 1:
                raise BusanBukguContractError("malformed free-form detail row")
            free_form_rows += 1
            continue
        if len(headings) != len(values) or len(headings) not in {1, 2}:
            raise BusanBukguContractError("detail label/value layout changed")
        for heading, value in zip(headings, values):
            label = _text(heading)
            if not label or label in safe or label in skipped:
                raise BusanBukguContractError(f"duplicate/empty detail label {label!r}")
            if label in safe_labels:
                safe[label] = _text(value)
            elif label in skipped_labels:
                # Do not call get_text on applicant counts, instructor,
                # attachment, note, or free-form cells.
                skipped.add(label)
            else:
                raise BusanBukguContractError(f"unknown local detail label {label!r}")
    return safe, skipped, free_form_rows


def _local_application_control(
    soup: BeautifulSoup,
    *,
    ledger: _LocalLedger,
    identity: str,
    active: bool,
) -> tuple[str, bool]:
    controls = [
        node
        for node in soup.select("#conts .taC.mg30t > a.btn.done[href]")
        if _text(node) in {"수강신청", "프로그램신청"}
    ]
    if not active:
        if controls:
            raise BusanBukguContractError(f"closed/scheduled {ledger.key} row became actionable")
        return "", False
    control = _one(controls, f"{ledger.key} application control")
    expected_label = "수강신청" if ledger is _LIFELONG else "프로그램신청"
    parsed = urlparse(urljoin(BUSAN_BUKGU_URL, _clean(control.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    audited_unbound = _AUDITED_UNBOUND_LIBRARY_APPLICATION
    if (
        ledger is _LIBRARY
        and identity == audited_unbound["identity"]
        and _text(control) == audited_unbound["label"]
        and _compare_url(parsed.geturl()) == _compare_url(audited_unbound["url"])
    ):
        return expected_label, False
    if (
        _text(control) != expected_label
        or parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_BUKGU_HOST
        or _normal_path(parsed.path) != BUSAN_BUKGU_PATH
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
        or set(query) != {"menuCd", "mode", ledger.identity_key, "command"}
        or query.get("menuCd") != [ledger.menu]
        or query.get("mode") != ["form"]
        or query.get(ledger.identity_key) != [identity]
        or query.get("command") != ["insert"]
    ):
        raise BusanBukguContractError(f"{ledger.key} application control changed identity/scope")
    return expected_label, True


def _parse_local_detail(
    soup: BeautifulSoup,
    final_url: str,
    parent: Mapping[str, Any],
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    ledger = _LEDGER_BY_KEY.get(_clean(raw.get("source_ledger")))
    identity = _clean(raw.get("source_identity"))
    if ledger not in {_LIFELONG, _LIBRARY, _SMALL_LIBRARY}:
        raise BusanBukguContractError("unsupported current local detail ledger")
    _validate_local_detail_url(final_url, ledger=ledger, identity=identity)
    if _text(_one(soup.select("title"), "local detail title")) != (f"교육/강좌 < {ledger.name}"):
        raise BusanBukguContractError("local detail page title changed")
    table = _one(soup.select("#conts > .tbl_wrap > table.tbl"), "detail table")
    heading = _text(_one(table.select(":scope > thead > tr > th"), "detail course title"))
    if heading != _clean(parent.get("title")):
        raise BusanBukguContractError(f"{ledger.key} {identity} list/detail title mismatch")
    source_status = _clean(raw.get("source_status"))
    active = source_status in {"접수중", "대기접수중"}
    list_course_date_projection = False
    if ledger is _LIFELONG:
        safe, skipped, free_form_rows = _detail_table_values(
            table,
            safe_labels=_LIFELONG_DETAIL_SAFE_LABELS,
            skipped_labels=_LIFELONG_DETAIL_SKIPPED_LABELS,
        )
        required = _LIFELONG_DETAIL_SAFE_LABELS
        if set(safe) != required or skipped != _LIFELONG_DETAIL_SKIPPED_LABELS:
            raise BusanBukguContractError("lifelong detail field contract changed")
        if free_form_rows:
            raise BusanBukguContractError("unexpected lifelong free-form row layout")
        start, end, _ = _strict_range(
            safe["교육기간"],
            ledger=ledger,
            identity=identity,
            kind="education",
        )
        apply_start, apply_end, _ = _strict_range(
            safe["신청기간"],
            ledger=ledger,
            identity=identity,
            kind="application",
        )
        venue = safe["교육장소"]
        target = safe["교육대상"]
        fee = safe["수강자부담"]
        schedule = _clean(" ".join(value for value in (safe["교육시간"], safe["교육요일/횟수"]) if value))
    else:
        safe, skipped, free_form_rows = _detail_table_values(
            table,
            safe_labels=_LIBRARY_DETAIL_SAFE_LABELS,
            skipped_labels=_LIBRARY_DETAIL_SKIPPED_LABELS,
        )
        if set(safe) != _LIBRARY_DETAIL_SAFE_LABELS or skipped != _LIBRARY_DETAIL_SKIPPED_LABELS or free_form_rows != 1:
            raise BusanBukguContractError("library detail field contract changed")
        course_period = safe["수강기간"]
        detail_dates = _dates(course_period)
        if len(detail_dates) != 2:
            raise BusanBukguContractError("library detail course period changed")
        if detail_dates[1] < detail_dates[0]:
            expected_course = _AUDITED_LIBRARY_DETAIL_COURSE_TYPO
            if not (identity == expected_course["identity"] and course_period == expected_course["value"]):
                raise BusanBukguContractError("library detail course period changed")
            detail_dates[1] = date.fromisoformat(expected_course["normalized_end"])
        start, end = detail_dates[0].isoformat(), detail_dates[1].isoformat()
        parent_start = _clean(parent.get("start_date"))
        parent_end = _clean(parent.get("end_date"))
        if parent_start != start or parent_end not in {start, end}:
            raise BusanBukguContractError(f"library {identity} list/detail course projection changed")
        list_course_date_projection = parent_end == start and end != start
        expected = _AUDITED_LIBRARY_APPLICATION_TYPO
        if (
            identity == expected["identity"]
            and _clean(raw.get("source_application_period")) == expected["value"]
            and safe["신청기간"] == expected["detail_value"]
        ):
            apply_start = "2026-06-30"
            apply_end = expected["normalized_end"]
        else:
            apply_start, apply_end, _ = _strict_range(
                safe["신청기간"],
                ledger=ledger,
                identity=identity,
                kind="application",
            )
        venue = safe["장소"] or _clean(parent.get("venue_name"))
        target = "대상 별도 안내"
        fee = safe["수강료"]
        schedule = safe["수강일시"]
        detail_status = safe["신청상태"]
        if detail_status != source_status:
            raise BusanBukguContractError(f"library {identity} list/detail status mismatch")
    if (ledger is _LIFELONG and (start, end) != (_clean(parent.get("start_date")), _clean(parent.get("end_date")))) or (
        apply_start,
        apply_end,
    ) != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise BusanBukguContractError(f"{ledger.key} {identity} list/detail dates mismatch")
    control_label, identity_bound_application = _local_application_control(
        soup,
        ledger=ledger,
        identity=identity,
        active=active,
    )
    result = dict(parent)
    result.update(
        {
            "application_url": (_clean(parent.get("raw_url")) if active and identity_bound_application else ""),
            "application_type": (
                "WAITLIST_APPLY"
                if (source_status == "대기접수중" and identity_bound_application)
                else "ONLINE_RESERVATION"
                if active and identity_bound_application
                else "INFO_ONLY"
            ),
            "reservation_available": active and identity_bound_application,
            "period": f"{start} ~ {end}",
            "start_date": start,
            "end_date": end,
            "apply_period": f"{apply_start} ~ {apply_end}",
            "apply_start": apply_start,
            "apply_end": apply_end,
            "venue_name": venue or parent.get("branch"),
            "target": target,
            "fee": fee,
            "schedule_raw": schedule,
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "target_evidence": (
            "official_lifelong_detail" if ledger is _LIFELONG else "official_library_detail_omits_target"
        ),
        "detail_application_control": control_label,
        "unbound_application_control_blocked": (active and not identity_bound_application),
        "list_course_date_projection": list_course_date_projection,
        "instructor_value_never_read": True,
        "contact_values_never_read": True,
        "enrolment_values_never_read": True,
        "attachments_never_read": True,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _platform_office(office_code: str) -> _lifelong.BusanOffice:
    office = _lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(office_code)
    if (
        office is None
        or office.name != BUSAN_LIFELONG_BUKGU_OFFICE_NAMES.get(office_code)
        or office.ownership != "duplicate_dedicated_bukgu_owner"
    ):
        raise BusanBukguContractError("lifelong Buk-gu office ownership changed")
    return office


def _parse_platform_page(
    soup: BeautifulSoup,
    *,
    office_code: str,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    office = _platform_office(office_code)
    form_errors = _lifelong._form_errors(soup, office, page)
    if form_errors:
        raise BusanBukguContractError("; ".join(form_errors))
    form = _one(soup.select("form#learningVO"), "lifelong list form")
    action = urlparse(urljoin(_lifelong.BUSAN_LIFELONG_LIST_URL, _clean(form.get("action"))))
    action_query = parse_qs(action.query, keep_blank_values=True)
    if action_query.get("pageUnit") != [str(BUSAN_LIFELONG_PAGE_SIZE)]:
        raise BusanBukguContractError("lifelong pageUnit response changed")
    page_unit_select = _one(form.select("select[name='pageUnit']"), "lifelong pageUnit selector")
    page_unit_options = {_clean(node.get("value")) for node in page_unit_select.select("option")}
    if page_unit_options != {"10", "20", "50"}:
        raise BusanBukguContractError("lifelong pageUnit selector changed")
    last, errors = _lifelong._advertised_last(soup)
    if errors:
        raise BusanBukguContractError("; ".join(errors))
    if expected_last is not None and last != expected_last:
        raise BusanBukguContractError("lifelong final page changed")
    if last != 1:
        raise BusanBukguContractError("pageUnit=1000 no longer fits Buk-gu office")
    rows, row_errors = _lifelong._parse_list_page(soup, office=office, page=page)
    if row_errors:
        raise BusanBukguContractError("; ".join(row_errors))
    return rows, last


def _platform_archive_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = sorted(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("raw_fields", {}).get("identity_kind")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("apply_start")),
            _clean(row.get("apply_end")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    )
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _platform_native_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    if raw.get("identity_kind") != "internal" or not _LEARNING_ID_RE.fullmatch(identity):
        raise BusanBukguContractError("invalid native lifelong identity")
    result = dict(row)
    result.update(
        {
            "provider": BUSAN_BUKGU_PROVIDER,
            "provider_course_id": (f"{BUSAN_BUKGU_PROVIDER}:lifelong:{identity}"),
            "prefer_incoming_provider_course_id": True,
            "branch": "북구청 평생학습",
            "branch_code": "bukgu-lifelong-office00002650",
            "preserve_branch": True,
            "provider_organizer": "북구청",
            "municipality_code": BUSAN_BUKGU_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_BUKGU_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "북구",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": "complete_shared_office+native_current_detail",
        }
    )
    result["raw_fields"] = {
        **raw,
        "parser": BUSAN_BUKGU_PARSER,
        "source_catalog": "busan_lifelong_bukgu_native",
        "source_provider": BUSAN_LIFELONG_PROVIDER,
        "detail_verified": False,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
        "service_family": "education",
    }
    return result


def _safe_platform_detail_values(
    soup: BeautifulSoup,
) -> tuple[tuple[str, ...], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    allowed = set(_transport._PLATFORM_DETAIL_REQUIRED_LABELS) | set(_transport._PLATFORM_DETAIL_OPTIONAL_LABELS)
    for definition in soup.select("div.form_group dl"):
        heading = _one(definition.find_all("dt", recursive=False), "lifelong detail label")
        value = _one(definition.find_all("dd", recursive=False), "lifelong detail value")
        label = _text(heading)
        if not label or label in labels or label not in allowed:
            raise BusanBukguContractError(f"unknown or duplicate lifelong detail field {label!r}")
        labels.append(label)
        if label in _transport._PLATFORM_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        else:
            skipped.add(label)
    required = list(_transport._PLATFORM_DETAIL_REQUIRED_LABELS)
    without_optional = [label for label in labels if label not in _transport._PLATFORM_DETAIL_OPTIONAL_LABELS]
    if without_optional != required:
        raise BusanBukguContractError("lifelong detail field order changed")
    expected_skipped = set(required) - set(_transport._PLATFORM_DETAIL_SAFE_LABELS)
    if not expected_skipped.issubset(skipped):
        raise BusanBukguContractError("lifelong private/free boundary changed")
    return tuple(labels), safe, skipped


def _parse_platform_detail(
    soup: BeautifulSoup,
    final_url: str,
    parent: Mapping[str, Any],
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != _lifelong.BUSAN_LIFELONG_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != _lifelong.BUSAN_LIFELONG_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"lng_id"}
        or query.get("lng_id") != [identity]
    ):
        raise BusanBukguContractError("lifelong detail response scope changed")
    form = _one(
        soup.select("form#learningVO[name='learningVO']"),
        "lifelong detail form",
    )
    action = urlparse(urljoin(final_url, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).casefold() != "post"
        or action.path != _lifelong.BUSAN_LIFELONG_DETAIL_PATH
        or parse_qs(action.query, keep_blank_values=True).get("lng_id") != [identity]
    ):
        raise BusanBukguContractError("lifelong detail form changed")
    identities = {_clean(node.get("value")) for node in form.select("input[name='lng_id']")}
    offices = {_clean(node.get("value")) for node in form.select("input[name='inst_id']")}
    if identities != {identity} or offices != {"OFFICE_00002650"}:
        raise BusanBukguContractError("lifelong detail identity/office changed")
    heading = _one(soup.select("h2.enrolTit"), "lifelong detail heading")
    prefix = _one(heading.select(":scope > span"), "lifelong office prefix")
    if _text(prefix) != "[북구청]":
        raise BusanBukguContractError("lifelong detail office prefix changed")
    clone = BeautifulSoup(str(heading), "lxml")
    for node in clone.select("span"):
        node.extract()
    if _clean(clone.get_text(" ", strip=True)) != _clean(parent.get("title")):
        raise BusanBukguContractError("lifelong list/detail title mismatch")
    labels, safe, skipped = _safe_platform_detail_values(soup)
    detail_dates = _dates(safe.get("교육기간"))
    if len(detail_dates) != 2 or detail_dates[1] < detail_dates[0]:
        raise BusanBukguContractError("lifelong detail education period changed")
    if (detail_dates[0].isoformat(), detail_dates[1].isoformat()) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ):
        raise BusanBukguContractError("lifelong list/detail dates mismatch")
    if parent.get("apply_start") and parent.get("apply_end"):
        apply_dates = _dates(safe.get("일반모집기간"))
        if len(apply_dates) != 2 or (apply_dates[0].isoformat(), apply_dates[1].isoformat()) != (
            _clean(parent.get("apply_start")),
            _clean(parent.get("apply_end")),
        ):
            raise BusanBukguContractError("lifelong list/detail application dates mismatch")
    source_status = _clean(raw.get("source_status"))
    active = source_status in {"접수중", "대기접수"}
    controls = soup.select("#learning_aply_btn")
    control_label = ""
    if active:
        control = _one(controls, "lifelong application control")
        control_label = _text(control)
        if (
            control_label not in {"일반모집신청", "수강신청", "대기자신청"}
            or _clean(control.get("onclick")) != "fn_learning_apply(); return false;"
        ):
            raise BusanBukguContractError("lifelong application control changed")
        if source_status == "대기접수" and control_label != "대기자신청":
            raise BusanBukguContractError("lifelong waitlist control changed")
    elif controls:
        raise BusanBukguContractError("non-open lifelong row became actionable")
    result = dict(parent)
    result.update(
        {
            "status": ("OPEN" if active else "SCHEDULED" if source_status == "대기" else "CLOSED"),
            "application_url": (busan_bukgu_lifelong_detail_url(identity) if active else ""),
            "application_type": (
                "WAITLIST_APPLY" if control_label == "대기자신청" else "ONLINE_RESERVATION" if active else "INFO_ONLY"
            ),
            "reservation_available": active,
            "target": safe.get("교육대상", ""),
            "venue_name": safe.get("교육장소") or "북구청",
            "fee": safe.get("수강료", ""),
            "schedule_raw": safe.get("교육시간", ""),
            "application_method_raw": safe.get("모집방법", ""),
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_application_control": control_label,
        "detail_source_status": safe.get("신청상태", ""),
        "contact_value_never_read": "문의전화" in skipped,
        "instructor_value_never_read": "강사" in skipped,
        "enrolment_counts_never_read": "접수인원" in skipped,
        "attachments_never_read": True,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
        "detail_labels": labels,
    }
    return result


def _city_list_contract(
    soup: BeautifulSoup, *, page: int, expected_last: Optional[int] = None
) -> tuple[int, Optional[Tag]]:
    if _text(_one(soup.select("title"), "Busan city title")) != _CITY_LIST_TITLE:
        raise BusanBukguContractError("Busan city list title changed")
    form = _one(soup.select("form#srchForm[name='srchForm']"), "Busan city search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
        or _clean(_one(form.select("input[name='curPage']"), "Busan city page field").get("value")) != str(page)
    ):
        raise BusanBukguContractError("Busan city search form changed")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_BUKGU_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanBukguContractError(f"Busan city {name} filter changed")
    end_link = _one(soup.select("div.paginate > a.pgEnd[href]"), "Busan city last page")
    parsed = urlparse(urljoin(BUSAN_CITY_BUKGU_URL, _clean(end_link.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_CITY_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_CITY_LIST_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or query.get("srchGugun") != [BUSAN_CITY_BUKGU_GUGUN]
        or query.get("srchResveInsttCd") != [BUSAN_CITY_RESIDENT_OFFICE]
    ):
        raise BusanBukguContractError("unsafe Busan city last-page control")
    last_values = query.get("curPage", [])
    last_raw = _clean(last_values[0]) if len(last_values) == 1 else ""
    if not last_raw.isdigit() or int(last_raw) < 1:
        raise BusanBukguContractError("invalid Busan city last page")
    last = int(last_raw)
    if expected_last is not None and last != expected_last:
        raise BusanBukguContractError("Busan city last page changed")
    roots = soup.select("ul.reserveList")
    if page <= last:
        root: Optional[Tag] = _one(roots, "Busan city reserve list")
    elif page == last + 1:
        if roots:
            raise BusanBukguContractError("Busan city sentinel retained list")
        empty = _text(_one(soup.select("div.txtCenter"), "Busan city empty result"))
        if empty != "등록된 강좌가 없습니다.":
            raise BusanBukguContractError("Busan city sentinel changed")
        root = None
    else:
        raise BusanBukguContractError("Busan city request passed sentinel")
    return last, root


def _city_card_date_ranges(value: Any, label: str) -> tuple[str, str, str, str]:
    match = _CITY_CARD_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanBukguContractError(f"{label} changed")
    try:
        apply_start, apply_end, start, end = (date.fromisoformat(part) for part in match.groups())
    except ValueError as exc:
        raise BusanBukguContractError(f"{label} contains invalid date") from exc
    if apply_end < apply_start or end < start:
        raise BusanBukguContractError(f"{label} is reversed")
    return (
        apply_start.isoformat(),
        apply_end.isoformat(),
        start.isoformat(),
        end.isoformat(),
    )


def _parse_city_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    last, root = _city_list_contract(soup, page=page, expected_last=expected_last)
    rows: list[dict[str, Any]] = []
    items = root.find_all("li", recursive=False) if root is not None else []
    for position, item in enumerate(items, 1):
        link = _one(item.select(":scope > a.reserveItem[onclick]"), "Busan city course")
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanBukguContractError("Busan city identity action changed")
        group_id, program_id = action.groups()
        title_node = _one(link.select(":scope .tit"), "Busan city title")
        title = _text(title_node)
        title_attribute = _clean(title_node.get("title"))
        normalized_title_attribute = title_attribute
        if title.startswith("[권역]") and title_attribute == title.removeprefix("[권역]"):
            normalized_title_attribute = title
        if not title or normalized_title_attribute != title:
            raise BusanBukguContractError("Busan city card title changed")
        source_status = _text(_one(link.select(":scope .statusMark"), "Busan city status"))
        if source_status not in _CITY_STATUS_MAP:
            raise BusanBukguContractError("unknown Busan city status")
        definitions = _one(link.select(":scope .infoBox > dl"), "Busan city card values")
        headings = definitions.find_all("dt", recursive=False)
        values = definitions.find_all("dd", recursive=False)
        labels = tuple(_text(node) for node in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(labels):
            raise BusanBukguContractError("Busan city card labels changed")
        # 문의 is deliberately excluded from collected text.
        safe = {label: _text(value) for label, value in zip(labels[:-1], values[:-1])}
        if any(not value for value in safe.values()):
            raise BusanBukguContractError("Busan city safe card value is empty")
        branch = safe["기관"]
        if not branch.startswith("북구 ") or not branch.endswith(" 주민자치회"):
            raise BusanBukguContractError("Busan city row left Buk-gu owner")
        apply_start, apply_end, start, end = _city_card_date_ranges(
            safe["일자"], f"Busan city page {page} row {position} dates"
        )
        source_venue = safe["장소"]
        venue = branch if source_venue == "-" else source_venue
        raw_url = busan_bukgu_city_detail_url(group_id, program_id)
        rows.append(
            {
                "provider": BUSAN_BUKGU_PROVIDER,
                "provider_course_id": (f"{BUSAN_BUKGU_PROVIDER}:reserve:{group_id}:{program_id}"),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"bukgu-reserve-{group_id}",
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
                "venue_name": venue,
                "provider_organizer": branch,
                "municipality_code": BUSAN_BUKGU_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_BUKGU_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "북구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": ("complete_html_pages+current_detail_allowlist"),
                "raw_fields": {
                    "parser": BUSAN_BUKGU_PARSER,
                    "source_catalog": "busan_reserve_bukgu_resident_councils",
                    "source_identity": f"{group_id}:{program_id}",
                    "source_group_id": group_id,
                    "source_program_id": program_id,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_application_method": safe["방법"],
                    "source_card_dates": safe["일자"],
                    "source_venue": source_venue,
                    "venue_fallback_used": source_venue == "-",
                    "inquiry_value_never_read": True,
                    "detail_verified": False,
                    "application_form_fetched": False,
                    "service_family": "education",
                },
            }
        )
    expected_count = 0 if page == last + 1 else 10 if page < last else len(rows)
    if page < last and len(rows) != expected_count:
        raise BusanBukguContractError("Busan city intermediate page is short")
    if page == last and not 1 <= len(rows) <= 10:
        raise BusanBukguContractError("Busan city final page row count changed")
    return rows, last


def _city_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        repr(
            [
                (
                    _clean(row.get("provider_course_id")),
                    _clean(row.get("title")),
                    _clean(row.get("start_date")),
                    _clean(row.get("end_date")),
                    _clean(row.get("raw_fields", {}).get("source_status")),
                )
                for row in rows
            ]
        ).encode("utf-8")
    ).hexdigest()


def _city_detail_dates(value: Any, label: str) -> tuple[str, str]:
    found = _CITY_DETAIL_DATE_RE.findall(_clean(value))
    if len(found) != 2:
        raise BusanBukguContractError(f"{label} changed")
    try:
        start, end = (date.fromisoformat(part) for part in found)
    except ValueError as exc:
        raise BusanBukguContractError(f"{label} has invalid date") from exc
    if end < start:
        raise BusanBukguContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _city_method_key(value: Any) -> str:
    text = re.sub(r",\s*,", ",", _clean(value))
    return "".join(text.split())


def _safe_city_detail_values(
    info: Tag,
) -> tuple[list[str], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in info.find_all("dl", recursive=False):
        heading = _one(
            definition.find_all("dt", recursive=False),
            "Busan city detail label",
        )
        value = _one(
            definition.find_all("dd", recursive=False),
            "Busan city detail value",
        )
        label = _text(heading)
        if label in labels:
            raise BusanBukguContractError("duplicate Busan city detail field")
        labels.append(label)
        if label in _CITY_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        elif label in _CITY_DETAIL_SKIPPED_LABELS:
            skipped.add(label)
        else:
            raise BusanBukguContractError(f"unknown Busan city detail field {label!r}")
    without_attachment = [label for label in labels if label != "첨부파일"]
    if tuple(without_attachment) != _CITY_DETAIL_REQUIRED_LABELS:
        raise BusanBukguContractError("Busan city detail field order changed")
    if "문의전화" not in skipped:
        raise BusanBukguContractError("Busan city inquiry boundary changed")
    return labels, safe, skipped


def _parse_city_detail(soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    group_id = _clean(raw.get("source_group_id"))
    program_id = _clean(raw.get("source_program_id"))
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_CITY_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_CITY_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"resveGroupSn", "progrmSn"}
        or query.get("resveGroupSn") != [group_id]
        or query.get("progrmSn") != [program_id]
    ):
        raise BusanBukguContractError("Busan city detail response scope changed")
    if _text(_one(soup.select("title"), "Busan city detail title")) != (_CITY_LIST_TITLE):
        raise BusanBukguContractError("Busan city detail title changed")
    form = _one(soup.select("form#viewForm"), "Busan city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(form.get("action")):
        raise BusanBukguContractError("Busan city detail form changed")
    for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanBukguContractError("Busan city detail identity changed")
    heading = _one(
        form.select(":scope > div.contHeader > h3.titPage"),
        "Busan city detail heading",
    )
    source_status = _text(_one(heading.select(":scope .statusMark"), "Busan city detail status"))
    direct_title = _clean(
        " ".join(_clean(child) for child in heading.children if isinstance(child, NavigableString) and _clean(child))
    )
    if direct_title != _clean(parent.get("title")):
        raise BusanBukguContractError("Busan city list/detail title mismatch")
    if source_status != _clean(raw.get("source_status")):
        raise BusanBukguContractError("Busan city list/detail status mismatch")
    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "Busan city safe detail values",
    )
    _labels, safe, skipped = _safe_city_detail_values(info)
    if any(not safe.get(label) for label in _CITY_DETAIL_SAFE_LABELS):
        raise BusanBukguContractError("Busan city safe detail value is empty")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanBukguContractError("Busan city free-form boundary changed")
    start, end = _city_detail_dates(safe["운영기간"], "city operating period")
    apply_start, apply_end = _city_detail_dates(safe["신청기간"], "city application period")
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise BusanBukguContractError("Busan city list/detail dates mismatch")
    for label, expected in (
        ("신청방법", raw.get("source_application_method")),
        ("운영기관", parent.get("branch")),
        ("대상", parent.get("target")),
    ):
        actual_key = _city_method_key(safe[label]) if label == "신청방법" else _clean(safe[label])
        expected_key = _city_method_key(expected) if label == "신청방법" else _clean(expected)
        if actual_key != expected_key:
            raise BusanBukguContractError(f"Busan city list/detail {label} mismatch")
    controls = form.select(":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL")
    actionable_controls = [control for control in controls if _text(control) != "목록"]
    if len(actionable_controls) > 1:
        raise BusanBukguContractError("multiple Busan city application controls")
    control_label = _text(actionable_controls[0]) if actionable_controls else ""
    normalized_status = _CITY_STATUS_MAP[source_status]
    method = safe["신청방법"]
    active = False
    application_type = "INFO_ONLY"
    if normalized_status == "OPEN":
        if "온라인" in method:
            if not actionable_controls or not any(token in control_label for token in ("신청", "예약")):
                raise BusanBukguContractError("open online city row lacks identity-bound control")
            active = True
            application_type = "ONLINE_RESERVATION"
        elif any(token in method for token in ("방문", "전화")):
            if control_label not in {"", "방문예약", "전화접수"}:
                raise BusanBukguContractError("offline Busan city control changed")
            application_type = "OFFLINE_APPLY"
        else:
            raise BusanBukguContractError("unknown Busan city application method")
    elif normalized_status == "CLOSED":
        if control_label not in {"", "접수마감"}:
            raise BusanBukguContractError("closed Busan city control changed")
    elif normalized_status == "SCHEDULED":
        if control_label not in {"", "대기중", "접수대기"}:
            raise BusanBukguContractError("scheduled Busan city control changed")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": application_type,
            "reservation_available": active,
            "fee": safe["수강료"],
            "schedule_raw": safe["요일 /시간"],
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_application_control": control_label,
        "inquiry_value_never_read": True,
        "attachments_never_read": "첨부파일" in skipped,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
    }
    return result


_PII_KEY_PARTS = (
    "phone",
    "telephone",
    "telno",
    "mobile",
    "email",
    "instructor",
    "teacher",
    "강사",
    "전화",
    "메일",
    "applicant",
)


def _pii_key(value: Any) -> bool:
    lowered = _clean(value).casefold()
    return any(part in lowered for part in _PII_KEY_PARTS)


def _sanitize_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    redactions = 0

    def visit(value: Any) -> Any:
        nonlocal redactions
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, child in value.items():
                if _pii_key(key):
                    redactions += 1
                    continue
                result[str(key)] = visit(child)
            return result
        if isinstance(value, tuple):
            return tuple(visit(item) for item in value)
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, str):
            updated, phones = _PHONE_RE.subn("", value)
            updated, emails = _EMAIL_RE.subn("", updated)
            redactions += phones + emails
            return _clean(updated)
        return value

    return visit(row), redactions


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "information_source_rows": 0,
        "lifelong_declared_rows": 0,
        "lifelong_default_rendered_rows": 0,
        "lifelong_default_data_pages": 0,
        "lifelong_default_error_shell_pages": [],
        "lifelong_partition_totals": {},
        "lifelong_partition_page_counts": {},
        "lifelong_status_union_rows": 0,
        "lifelong_declared_unrendered_rows": 0,
        "lifelong_status_partition_recovered_rows": 0,
        "library_source_rows": 0,
        "library_advertised_last_page": 0,
        "library_actual_data_pages": 0,
        "library_page_counts": {},
        "library_list_projected_current_rows": 0,
        "library_current_year_detail_probe_rows": 0,
        "library_projection_recovered_current_rows": 0,
        "library_current_count": 0,
        "library_prior_year_detail_audit_rows": 340,
        "library_prior_year_cross_year_rows": 0,
        "small_library_source_rows": 0,
        "small_library_independent_rows": 0,
        "small_library_identity_overlap_rows": 0,
        "small_library_duplicate_rows": 0,
        "small_library_list_projected_current_rows": 0,
        "small_library_current_year_detail_probe_rows": 0,
        "small_library_projection_recovered_current_rows": 0,
        "small_library_current_count": 0,
        "district_source_rows": 0,
        "district_current_before_test_exclusion": 0,
        "district_exact_test_rows_excluded": 0,
        "district_current_count": 0,
        "platform_source_rows": 0,
        "platform_office_counts": {},
        "platform_native_rows": 0,
        "platform_native_current_count": 0,
        "platform_external_duplicate_rows": 0,
        "platform_external_unmatched_rows": 0,
        "platform_external_projection_mismatch_rows": 0,
        "platform_external_projection_mismatch_fields": {},
        "city_source_rows": 0,
        "city_current_count": 0,
        "source_total": 0,
        "source_rows": 0,
        "unique_education_source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "non_current_count": 0,
        "returned_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "application_control_count": 0,
        "offline_application_count": 0,
        "status_counts": {},
        "branch_count": 0,
        "branch_counts": {},
        "duplicate_source_identity_count": 0,
        "privacy_redactions": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": BUSAN_BUKGU_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_BUKGU_MUNICIPALITY_NAME,
        "registered_url": BUSAN_BUKGU_CANONICAL_URL,
        "canonical_url": BUSAN_BUKGU_CANONICAL_URL,
        "city_canonical_url": BUSAN_CITY_BUKGU_URL,
        "lifelong_office_codes": BUSAN_LIFELONG_BUKGU_OFFICES,
        "ownership_scope": BUSAN_BUKGU_OWNERSHIP_SCOPE,
        "candidate_ids": dict(BUSAN_BUKGU_CANDIDATE_IDS),
        "owner_boundary_audit": dict(BUSAN_BUKGU_OWNER_BOUNDARY_AUDIT),
    }


def _is_exact_test_course(row: Mapping[str, Any]) -> bool:
    expected = _AUDITED_TEST_COURSE
    return bool(
        _clean(row.get("raw_fields", {}).get("source_identity")) == expected["identity"]
        and _clean(row.get("title")) == expected["title"]
        and _clean(row.get("start_date")) == expected["start_date"]
        and _clean(row.get("end_date")) == expected["end_date"]
        and _clean(row.get("raw_fields", {}).get("source_status")) == expected["source_status"]
    )


def _rows_by_identity(rows: Sequence[Mapping[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = _clean(row.get("raw_fields", {}).get("source_identity"))
        if not identity or identity in result:
            raise BusanBukguContractError(f"{label} contains duplicate identity")
        result[identity] = dict(row)
    return result


def _same_course_identity_fields(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    return not _course_identity_field_mismatches(first, second)


def _course_identity_field_mismatches(first: Mapping[str, Any], second: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        key
        for key in (
            "title",
            "start_date",
            "end_date",
            "apply_start",
            "apply_end",
        )
        if _clean(first.get(key)) != _clean(second.get(key))
    )


def collect_busan_bukgu_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 450,
    detail_limit: int = 300,
    max_requests: int = 700,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_BUKGU_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future snapshot of every Buk-gu ledger."""

    meta = _base_meta()
    if not is_busan_bukgu_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact Busan Buk-gu integrated reservation education owner"
        )
        return [], BUSAN_BUKGU_PARSER, meta
    try:
        if any(
            isinstance(value, bool)
            for value in (
                timeout,
                max_pages,
                detail_limit,
                max_requests,
                max_workers,
            )
        ):
            raise ValueError("boolean limits are invalid")
        request_timeout = max(1, int(timeout))
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        request_cap = max(0, int(max_requests))
        workers = min(max(1, int(max_workers)), BUSAN_BUKGU_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_BUKGU_PARSER, meta
    if page_cap < 10 or request_cap < 10:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "caps do not allow the ten mandatory first-ledger requests"
        return [], BUSAN_BUKGU_PARSER, meta

    fetch = fetcher or _transport._default_fetcher
    factory = session_factory or _transport._default_session_factory
    budget = _transport._RequestBudget(request_cap)

    def run_jobs(jobs: Sequence[tuple[Any, str, Probe]], *, list_phase: bool) -> _transport._FetchResult:
        result = _transport._fetch_many(
            jobs,
            fetcher=fetch,
            session_factory=factory,
            timeout=request_timeout,
            max_workers=min(workers, max(1, len(jobs))),
            sleeper=sleeper,
            budget=budget,
        )
        meta["network_retry_count"] += result.retries
        meta["sessions_created"] += result.sessions
        meta["network_requests"] = budget.count
        if list_phase:
            meta["list_requests"] += len(result.values)
            meta["pages"] += len(result.values)
        return result

    first_jobs: list[tuple[Any, str, Probe]] = []
    for ledger in (_INFORMATION, _LIBRARY, _SMALL_LIBRARY):
        first_jobs.append(
            (
                ("local", ledger.key, "", 1),
                busan_bukgu_list_url(ledger, 1),
                lambda soup, ledger=ledger: _parse_local_page(soup, ledger=ledger, page=1, cutoff=cutoff),
            )
        )
    for partition in ("", "ing", "wait", "close"):
        first_jobs.append(
            (
                ("local", _LIFELONG.key, partition, 1),
                busan_bukgu_list_url(_LIFELONG, 1, register_status=partition),
                lambda soup, partition=partition: _parse_local_page(
                    soup,
                    ledger=_LIFELONG,
                    page=1,
                    cutoff=cutoff,
                    register_status=partition,
                ),
            )
        )
    for office_code in BUSAN_LIFELONG_BUKGU_OFFICES:
        first_jobs.append(
            (
                ("platform", office_code, "first"),
                busan_bukgu_lifelong_list_url(office_code, 1),
                lambda soup, office_code=office_code: _parse_platform_page(soup, office_code=office_code, page=1),
            )
        )
    first_jobs.append(
        (
            ("city", 1),
            busan_bukgu_city_list_url(1),
            lambda soup: _parse_city_page(soup, page=1),
        )
    )
    first = run_jobs(first_jobs, list_phase=True)
    if first.errors or len(first.values) != len(first_jobs):
        meta["configured_collection_error"] = "; ".join(first.errors) or ("missing one or more first-ledger responses")
        return [], BUSAN_BUKGU_PARSER, meta

    try:
        first_local: dict[tuple[str, str], list[dict[str, Any]]] = {}
        totals: dict[tuple[str, str], int] = {}
        advertised: dict[tuple[str, str], int] = {}
        for ledger, partitions in (
            (_INFORMATION, ("",)),
            (_LIBRARY, ("",)),
            (_SMALL_LIBRARY, ("",)),
            (_LIFELONG, ("", "ing", "wait", "close")),
        ):
            for partition in partitions:
                rows, total, last = _parse_local_page(
                    first.values[("local", ledger.key, partition, 1)][0],
                    ledger=ledger,
                    page=1,
                    cutoff=cutoff,
                    register_status=partition,
                )
                first_local[(ledger.key, partition)] = rows
                totals[(ledger.key, partition)] = total
                advertised[(ledger.key, partition)] = last
        first_platform: dict[str, list[dict[str, Any]]] = {}
        for office_code in BUSAN_LIFELONG_BUKGU_OFFICES:
            rows, last = _parse_platform_page(
                first.values[("platform", office_code, "first")][0],
                office_code=office_code,
                page=1,
            )
            if last != 1:
                raise BusanBukguContractError("lifelong office last page changed")
            first_platform[office_code] = rows
        first_city, city_last = _parse_city_page(first.values[("city", 1)][0], page=1)
        minimum_pages = (
            sum(value + 1 for value in advertised.values()) + len(BUSAN_LIFELONG_BUKGU_OFFICES) * 3 + city_last + 1
        )
        if minimum_pages > page_cap:
            raise BusanBukguContractError(
                f"max_pages cap allows {page_cap} of at least {minimum_pages} declared/sentinel pages"
            )
        if minimum_pages > request_cap:
            raise BusanBukguContractError(f"max_requests cap {request_cap} is below the list census floor")
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"first-page contract: {_clean(exc)}"
        return [], BUSAN_BUKGU_PARSER, meta

    def default_probe(soup: BeautifulSoup, page: int) -> None:
        if _is_lifelong_error_shell(soup):
            return
        _parse_local_page(
            soup,
            ledger=_LIFELONG,
            page=page,
            cutoff=cutoff,
            expected_total=totals[(_LIFELONG.key, "")],
            expected_advertised_last=advertised[(_LIFELONG.key, "")],
        )

    remaining_jobs: list[tuple[Any, str, Probe]] = []
    for ledger, partitions in (
        (_INFORMATION, ("",)),
        (_LIBRARY, ("",)),
        (_SMALL_LIBRARY, ("",)),
        (_LIFELONG, ("", "ing", "wait", "close")),
    ):
        for partition in partitions:
            last = advertised[(ledger.key, partition)]
            for page in range(2, last + 2):
                if ledger is _LIFELONG and not partition:

                    def probe(soup: BeautifulSoup, page: int = page) -> None:
                        default_probe(soup, page)

                else:

                    def probe(
                        soup: BeautifulSoup,
                        ledger: _LocalLedger = ledger,
                        partition: str = partition,
                        page: int = page,
                    ) -> None:
                        _parse_local_page(
                            soup,
                            ledger=ledger,
                            page=page,
                            cutoff=cutoff,
                            register_status=partition,
                            expected_total=totals[(ledger.key, partition)],
                            expected_advertised_last=advertised[(ledger.key, partition)],
                        )

                remaining_jobs.append(
                    (
                        ("local", ledger.key, partition, page),
                        busan_bukgu_list_url(ledger, page, register_status=partition),
                        probe,
                    )
                )
    for office_code in BUSAN_LIFELONG_BUKGU_OFFICES:
        remaining_jobs.extend(
            (
                (
                    ("platform", office_code, "sentinel"),
                    busan_bukgu_lifelong_list_url(office_code, 2),
                    lambda soup, office_code=office_code: _parse_platform_page(
                        soup,
                        office_code=office_code,
                        page=2,
                        expected_last=1,
                    ),
                ),
                (
                    ("platform", office_code, "second"),
                    busan_bukgu_lifelong_list_url(office_code, 1),
                    lambda soup, office_code=office_code: _parse_platform_page(
                        soup,
                        office_code=office_code,
                        page=1,
                        expected_last=1,
                    ),
                ),
            )
        )
    for page in range(2, city_last + 2):
        remaining_jobs.append(
            (
                ("city", page),
                busan_bukgu_city_list_url(page),
                lambda soup, page=page: _parse_city_page(soup, page=page, expected_last=city_last),
            )
        )
    remaining = run_jobs(remaining_jobs, list_phase=True)
    if remaining.errors or len(remaining.values) != len(remaining_jobs):
        meta["configured_collection_error"] = "; ".join(remaining.errors) or (
            "missing complete ledger/sentinel response"
        )
        return [], BUSAN_BUKGU_PARSER, meta

    try:
        city_pages: dict[int, list[dict[str, Any]]] = {1: first_city}
        for page in range(2, city_last + 2):
            rows, _ = _parse_city_page(
                remaining.values[("city", page)][0],
                page=page,
                expected_last=city_last,
            )
            city_pages[page] = rows
        if city_pages[city_last + 1]:
            raise BusanBukguContractError("Busan city sentinel is not empty")
        city_rows = [row for page in range(1, city_last + 1) for row in city_pages[page]]
        city_by_id = _rows_by_identity(city_rows, label="Busan city resident-council census")
        if len(city_by_id) != len(city_rows):
            raise BusanBukguContractError("Busan city resident-council identity count changed")
    except Exception as exc:
        meta["configured_collection_error"] = f"Busan city complete census: {_clean(exc)}"
        return [], BUSAN_BUKGU_PARSER, meta

    try:
        default_pages: dict[int, list[dict[str, Any]]] = {1: first_local[("lifelong", "")]}
        default_errors: list[int] = []
        default_last = advertised[("lifelong", "")]
        for page in range(2, default_last + 1):
            soup = remaining.values[("local", "lifelong", "", page)][0]
            if _is_lifelong_error_shell(soup):
                default_errors.append(page)
                continue
            rows, _, _ = _parse_local_page(
                soup,
                ledger=_LIFELONG,
                page=page,
                cutoff=cutoff,
                expected_total=totals[("lifelong", "")],
                expected_advertised_last=default_last,
            )
            default_pages[page] = rows
        sentinel_rows, _, _ = _parse_local_page(
            remaining.values[("local", "lifelong", "", default_last + 1)][0],
            ledger=_LIFELONG,
            page=default_last + 1,
            cutoff=cutoff,
            expected_total=totals[("lifelong", "")],
            expected_advertised_last=default_last,
        )
        if sentinel_rows:
            raise BusanBukguContractError("lifelong default post-advertised sentinel is not empty")
        data_pages = sorted(page for page, rows in default_pages.items() if rows)
        if (
            len(default_errors) != BUSAN_BUKGU_DEFAULT_ERROR_SHELL_COUNT
            or data_pages != list(range(1, max(data_pages) + 1))
            or default_errors != list(range(max(data_pages) + 1, default_last + 1))
        ):
            raise BusanBukguContractError("lifelong default error-shell/data-page topology changed")
        default_rows = [row for page in data_pages for row in default_pages[page]]
        default_by_id = _rows_by_identity(default_rows, label="lifelong default census")

        partition_rows: dict[str, list[dict[str, Any]]] = {}
        partition_pages: dict[str, dict[int, list[dict[str, Any]]]] = {}
        partition_by_id: dict[str, dict[str, dict[str, Any]]] = {}
        for partition in ("ing", "wait", "close"):
            last = advertised[("lifelong", partition)]
            pages = {1: first_local[("lifelong", partition)]}
            for page in range(2, last + 1):
                rows, _, _ = _parse_local_page(
                    remaining.values[("local", "lifelong", partition, page)][0],
                    ledger=_LIFELONG,
                    page=page,
                    cutoff=cutoff,
                    register_status=partition,
                    expected_total=totals[("lifelong", partition)],
                    expected_advertised_last=last,
                )
                pages[page] = rows
            sentinel, _, _ = _parse_local_page(
                remaining.values[("local", "lifelong", partition, last + 1)][0],
                ledger=_LIFELONG,
                page=last + 1,
                cutoff=cutoff,
                register_status=partition,
                expected_total=totals[("lifelong", partition)],
                expected_advertised_last=last,
            )
            if sentinel:
                raise BusanBukguContractError(f"lifelong {partition} sentinel is not empty")
            rows = [row for page in range(1, last + 1) for row in pages[page]]
            if len(rows) != totals[("lifelong", partition)]:
                raise BusanBukguContractError(f"lifelong {partition} rows differ from declared total")
            partition_rows[partition] = rows
            partition_pages[partition] = pages
            partition_by_id[partition] = _rows_by_identity(rows, label=f"lifelong {partition} partition")
        partition_sets = [set(partition_by_id[key]) for key in ("ing", "wait", "close")]
        if any(
            partition_sets[first_index] & partition_sets[second_index]
            for first_index in range(len(partition_sets))
            for second_index in range(first_index + 1, len(partition_sets))
        ):
            raise BusanBukguContractError("lifelong status partitions overlap")
        union_by_id: dict[str, dict[str, Any]] = {}
        for partition in ("ing", "wait", "close"):
            union_by_id.update(partition_by_id[partition])
        declared_gap = totals[("lifelong", "")] - len(union_by_id)
        recovered = len(union_by_id) - len(default_by_id)
        if (
            not set(default_by_id).issubset(union_by_id)
            or declared_gap != BUSAN_BUKGU_DECLARED_UNRENDERED_LIFELONG_ROWS
            or recovered != BUSAN_BUKGU_DEFAULT_PARTITION_RECOVERY_ROWS
        ):
            raise BusanBukguContractError("lifelong declaration/default/status identity equation changed")
        for identity, row in default_by_id.items():
            if not _same_course_identity_fields(row, union_by_id[identity]):
                raise BusanBukguContractError(f"lifelong identity {identity} differs by partition")
    except Exception as exc:
        meta["configured_collection_error"] = f"lifelong complete identity census: {_clean(exc)}"
        return [], BUSAN_BUKGU_PARSER, meta

    local_pages_by_ledger: dict[str, dict[int, list[dict[str, Any]]]] = {}
    local_rows_by_ledger: dict[str, list[dict[str, Any]]] = {}
    actual_last_by_ledger: dict[str, int] = {}

    try:
        for ledger in (_INFORMATION, _LIBRARY, _SMALL_LIBRARY):
            total = totals[(ledger.key, "")]
            last = advertised[(ledger.key, "")]
            pages: dict[int, list[dict[str, Any]]] = {1: first_local[(ledger.key, "")]}
            seen: dict[str, dict[str, Any]] = {}
            for row in pages[1]:
                identity = _clean(row.get("raw_fields", {}).get("source_identity"))
                seen[identity] = row
            page = 2
            sentinel_page = 0
            while True:
                if page <= last + 1:
                    soup = remaining.values[("local", ledger.key, "", page)][0]
                else:
                    if page > page_cap:
                        raise BusanBukguContractError(f"max_pages cap reached before {ledger.key} sentinel")
                    job = (
                        ("tail", ledger.key, page),
                        busan_bukgu_list_url(ledger, page),
                        lambda soup, ledger=ledger, page=page: _parse_local_page(
                            soup,
                            ledger=ledger,
                            page=page,
                            cutoff=cutoff,
                            expected_total=total,
                            expected_advertised_last=last,
                        ),
                    )
                    fetched = run_jobs([job], list_phase=True)
                    if fetched.errors or job[0] not in fetched.values:
                        raise BusanBukguContractError("; ".join(fetched.errors) or "missing tail page")
                    soup = fetched.values[job[0]][0]
                rows, _, _ = _parse_local_page(
                    soup,
                    ledger=ledger,
                    page=page,
                    cutoff=cutoff,
                    expected_total=total,
                    expected_advertised_last=last,
                )
                pages[page] = rows
                if not rows:
                    if len(seen) != total:
                        raise BusanBukguContractError(f"{ledger.key} became empty at {len(seen)} of {total}")
                    sentinel_page = page
                    break
                if len(seen) >= total:
                    raise BusanBukguContractError(f"{ledger.key} retained rows after declared total")
                for row in rows:
                    identity = _clean(row.get("raw_fields", {}).get("source_identity"))
                    if identity in seen:
                        raise BusanBukguContractError(f"{ledger.key} repeats identity {identity}")
                    seen[identity] = row
                if len(seen) > total:
                    raise BusanBukguContractError(f"{ledger.key} unique rows exceed declared total")
                page += 1
            data_pages_for_ledger = [number for number, rows in pages.items() if rows]
            if (
                data_pages_for_ledger != list(range(1, max(data_pages_for_ledger) + 1))
                or sentinel_page != max(data_pages_for_ledger) + 1
            ):
                raise BusanBukguContractError(f"{ledger.key} data/sentinel topology changed")
            local_pages_by_ledger[ledger.key] = pages
            local_rows_by_ledger[ledger.key] = list(seen.values())
            actual_last_by_ledger[ledger.key] = max(data_pages_for_ledger)
        library_by_id = _rows_by_identity(local_rows_by_ledger["library"], label="library ledger")
        small_by_id = _rows_by_identity(local_rows_by_ledger["small_library"], label="small-library ledger")
        overlap = set(small_by_id) & set(library_by_id)
        if overlap:
            raise BusanBukguContractError("independent small-library ledger now overlaps library identities")
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"district complete declared census: {_clean(exc)}"
        return [], BUSAN_BUKGU_PARSER, meta

    try:
        platform_rows_by_office: dict[str, list[dict[str, Any]]] = {}
        for office_code in BUSAN_LIFELONG_BUKGU_OFFICES:
            first_rows = first_platform[office_code]
            sentinel, _ = _parse_platform_page(
                remaining.values[("platform", office_code, "sentinel")][0],
                office_code=office_code,
                page=2,
                expected_last=1,
            )
            second_rows, _ = _parse_platform_page(
                remaining.values[("platform", office_code, "second")][0],
                office_code=office_code,
                page=1,
                expected_last=1,
            )
            if sentinel:
                raise BusanBukguContractError(f"lifelong {office_code} sentinel is not empty")
            if _platform_archive_signature(first_rows) != _platform_archive_signature(second_rows):
                raise BusanBukguContractError(f"lifelong {office_code} semantic census changed")
            identities = [_clean(row.get("raw_fields", {}).get("identity")) for row in first_rows]
            if len(identities) != len(set(identities)):
                raise BusanBukguContractError(f"lifelong {office_code} identities are duplicated")
            if first_rows:
                sequences = sorted(int(row.get("raw_fields", {}).get("list_sequence") or 0) for row in first_rows)
                if sequences != list(range(1, len(first_rows) + 1)):
                    raise BusanBukguContractError(f"lifelong {office_code} sequence has a gap")
            platform_rows_by_office[office_code] = first_rows
        if platform_rows_by_office["OFFICE_00002800"]:
            raise BusanBukguContractError("audited empty Buk-gu lifelong-learning-centre office gained rows")
    except Exception as exc:
        meta["configured_collection_error"] = f"shared/city complete census: {_clean(exc)}"
        return [], BUSAN_BUKGU_PARSER, meta

    try:
        lifelong_by_id = union_by_id
        external_rows: list[dict[str, Any]] = []
        external_projection_mismatch_rows = 0
        external_projection_mismatch_fields: Counter[str] = Counter()
        native_rows: list[dict[str, Any]] = []
        for row in platform_rows_by_office["OFFICE_00002650"]:
            raw = row.get("raw_fields", {})
            kind = _clean(raw.get("identity_kind"))
            if kind == "external":
                identity = canonical_busan_bukgu_course_identity(raw.get("identity"))
                if not identity or identity not in lifelong_by_id:
                    raise BusanBukguContractError("lifelong external row is absent from district owner")
                owner = lifelong_by_id[identity]
                mismatches = _course_identity_field_mismatches(row, owner)
                if mismatches:
                    external_projection_mismatch_rows += 1
                    external_projection_mismatch_fields.update(mismatches)
                external_rows.append(dict(row))
            elif kind == "internal":
                native_rows.append(_platform_native_row(row))
            else:
                raise BusanBukguContractError(f"unsupported lifelong identity kind {kind!r}")
        cutoff_iso = cutoff.isoformat()
        information_rows = local_rows_by_ledger["information"]
        library_rows = local_rows_by_ledger["library"]
        small_library_rows = local_rows_by_ledger["small_library"]
        direct_current_before_test = [
            row for row in [*information_rows, *lifelong_by_id.values()] if _clean(row.get("end_date")) >= cutoff_iso
        ]
        excluded_tests = [row for row in direct_current_before_test if _is_exact_test_course(row)]
        if len(excluded_tests) != 1:
            raise BusanBukguContractError("audited future test-course exclusion changed")
        direct_current = [row for row in direct_current_before_test if not _is_exact_test_course(row)]
        annual_floor = f"{cutoff.year:04d}-01-01"
        library_detail_candidates = [row for row in library_rows if _clean(row.get("start_date")) >= annual_floor]
        small_library_detail_candidates = [
            row for row in small_library_rows if _clean(row.get("start_date")) >= annual_floor
        ]
        library_list_projected_current = [row for row in library_rows if _clean(row.get("end_date")) >= cutoff_iso]
        small_library_list_projected_current = [
            row for row in small_library_rows if _clean(row.get("end_date")) >= cutoff_iso
        ]
        native_current = [row for row in native_rows if _clean(row.get("end_date")) >= cutoff_iso]
        city_current = [row for row in city_rows if _clean(row.get("end_date")) >= cutoff_iso]
        district_detail_rows = [
            *direct_current,
            *library_detail_candidates,
            *small_library_detail_candidates,
        ]
        detail_source_rows = [
            *district_detail_rows,
            *native_current,
            *city_current,
        ]
        identities = [_clean(row.get("provider_course_id")) for row in detail_source_rows]
        if not all(identities) or len(identities) != len(set(identities)):
            raise BusanBukguContractError("mandatory detail identities overlap")
        for row in district_detail_rows:
            ledger_key = _clean(row.get("raw_fields", {}).get("source_ledger"))
            if ledger_key != "information" and (not row.get("apply_start") or not row.get("apply_end")):
                raise BusanBukguContractError("current district row lacks an application period")
        if len(detail_source_rows) > detail_cap:
            raise BusanBukguContractError(
                f"detail_limit cap allows {detail_cap} of {len(detail_source_rows)} mandatory annual/current details"
            )
        if (
            budget.count + len(detail_source_rows) + BUSAN_BUKGU_DETAIL_RECHECK_REQUESTS + int(city_last > 1)
            > request_cap
        ):
            raise BusanBukguContractError(f"max_requests cap {request_cap} cannot finish details/rechecks")
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"ownership/current partition: {_clean(exc)}"
        return [], BUSAN_BUKGU_PARSER, meta

    detail_jobs: list[tuple[Any, str, Probe]] = []
    for row in district_detail_rows:
        identity = _clean(row.get("raw_fields", {}).get("source_identity"))
        ledger = _LEDGER_BY_KEY[_clean(row.get("raw_fields", {}).get("source_ledger"))]
        url = busan_bukgu_detail_url(ledger, identity)
        detail_jobs.append(
            (
                ("detail", "local", ledger.key, identity),
                url,
                lambda soup, row=row, url=url: _parse_local_detail(soup, url, row),
            )
        )
    for row in native_current:
        identity = _clean(row.get("raw_fields", {}).get("identity"))
        url = busan_bukgu_lifelong_detail_url(identity)
        detail_jobs.append(
            (
                ("detail", "platform", identity),
                url,
                lambda soup, row=row, url=url: _parse_platform_detail(soup, url, row),
            )
        )
    for row in city_current:
        raw = row.get("raw_fields", {})
        group_id = _clean(raw.get("source_group_id"))
        program_id = _clean(raw.get("source_program_id"))
        url = busan_bukgu_city_detail_url(group_id, program_id)
        detail_jobs.append(
            (
                ("detail", "city", group_id, program_id),
                url,
                lambda soup, row=row, url=url: _parse_city_detail(soup, url, row),
            )
        )
    details = run_jobs(detail_jobs, list_phase=False)
    meta["detail_attempts"] = len(detail_jobs)
    meta["detail_errors"] = len(details.errors)
    if details.errors or len(details.values) != len(detail_jobs):
        meta["configured_collection_error"] = "; ".join(details.errors) or (
            "missing one or more current/future details"
        )
        return [], BUSAN_BUKGU_PARSER, meta
    try:
        enriched_district_details: list[dict[str, Any]] = []
        for row in district_detail_rows:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            ledger = _LEDGER_BY_KEY[_clean(row.get("raw_fields", {}).get("source_ledger"))]
            soup, final_url = details.values[("detail", "local", ledger.key, identity)]
            enriched_district_details.append(_parse_local_detail(soup, final_url, row))
        enriched_native: list[dict[str, Any]] = []
        for row in native_current:
            identity = _clean(row.get("raw_fields", {}).get("identity"))
            soup, final_url = details.values[("detail", "platform", identity)]
            enriched_native.append(_parse_platform_detail(soup, final_url, row))
        enriched_city: list[dict[str, Any]] = []
        for row in city_current:
            raw = row.get("raw_fields", {})
            group_id = _clean(raw.get("source_group_id"))
            program_id = _clean(raw.get("source_program_id"))
            soup, final_url = details.values[("detail", "city", group_id, program_id)]
            enriched_city.append(_parse_city_detail(soup, final_url, row))
        direct_count = len(direct_current)
        library_probe_count = len(library_detail_candidates)
        enriched_direct = enriched_district_details[:direct_count]
        enriched_library_probes = enriched_district_details[direct_count : direct_count + library_probe_count]
        enriched_small_library_probes = enriched_district_details[direct_count + library_probe_count :]
        for row in [*enriched_library_probes, *enriched_small_library_probes]:
            if _clean(row.get("start_date"))[:4] != _clean(row.get("end_date"))[:4]:
                raise BusanBukguContractError("library annual detail boundary changed")
        library_current = [row for row in enriched_library_probes if _clean(row.get("end_date")) >= cutoff_iso]
        small_library_current = [
            row for row in enriched_small_library_probes if _clean(row.get("end_date")) >= cutoff_iso
        ]
        library_projected_ids = {
            _clean(row.get("raw_fields", {}).get("source_identity")) for row in library_list_projected_current
        }
        library_current_ids = {_clean(row.get("raw_fields", {}).get("source_identity")) for row in library_current}
        small_projected_ids = {
            _clean(row.get("raw_fields", {}).get("source_identity")) for row in small_library_list_projected_current
        }
        small_current_ids = {_clean(row.get("raw_fields", {}).get("source_identity")) for row in small_library_current}
        if not library_projected_ids.issubset(library_current_ids):
            raise BusanBukguContractError("library card/detail current set regressed")
        if not small_projected_ids.issubset(small_current_ids):
            raise BusanBukguContractError("small-library card/detail current set regressed")
        district_current = [
            *enriched_direct,
            *library_current,
            *small_library_current,
        ]
        district_current_before_test = [*district_current, *excluded_tests]
        enriched = [*district_current, *enriched_native, *enriched_city]
        current_rows = enriched
    except Exception as exc:
        meta["detail_errors"] += 1
        meta["configured_collection_error"] = f"detail contract: {_clean(exc)}"
        return [], BUSAN_BUKGU_PARSER, meta

    recheck_jobs: list[tuple[Any, str, Probe]] = []

    def add_local_recheck(
        key: Any,
        ledger: _LocalLedger,
        page: int,
        *,
        partition: str = "",
    ) -> None:
        recheck_jobs.append(
            (
                key,
                busan_bukgu_list_url(ledger, page, register_status=partition),
                lambda soup, ledger=ledger, page=page, partition=partition: _parse_local_page(
                    soup,
                    ledger=ledger,
                    page=page,
                    cutoff=cutoff,
                    register_status=partition,
                    expected_total=totals[(ledger.key, partition)],
                    expected_advertised_last=advertised[(ledger.key, partition)],
                ),
            )
        )

    add_local_recheck(("recheck", "information", 1), _INFORMATION, 1)
    add_local_recheck(("recheck", "library", 1), _LIBRARY, 1)
    add_local_recheck(
        ("recheck", "library", "last"),
        _LIBRARY,
        actual_last_by_ledger["library"],
    )
    add_local_recheck(("recheck", "small", 1), _SMALL_LIBRARY, 1)
    add_local_recheck(
        ("recheck", "small", "last"),
        _SMALL_LIBRARY,
        actual_last_by_ledger["small_library"],
    )
    add_local_recheck(("recheck", "lifelong", "default-first"), _LIFELONG, 1)
    add_local_recheck(
        ("recheck", "lifelong", "default-last"),
        _LIFELONG,
        max(data_pages),
    )
    add_local_recheck(("recheck", "lifelong", "ing"), _LIFELONG, 1, partition="ing")
    add_local_recheck(("recheck", "lifelong", "wait"), _LIFELONG, 1, partition="wait")
    add_local_recheck(
        ("recheck", "lifelong", "close-last"),
        _LIFELONG,
        advertised[("lifelong", "close")],
        partition="close",
    )
    for office_code in BUSAN_LIFELONG_BUKGU_OFFICES:
        recheck_jobs.append(
            (
                ("recheck", "platform", office_code),
                busan_bukgu_lifelong_list_url(office_code, 1),
                lambda soup, office_code=office_code: _parse_platform_page(
                    soup,
                    office_code=office_code,
                    page=1,
                    expected_last=1,
                ),
            )
        )
    recheck_jobs.append(
        (
            ("recheck", "city", "first"),
            busan_bukgu_city_list_url(1),
            lambda soup: _parse_city_page(soup, page=1, expected_last=city_last),
        )
    )
    if city_last > 1:
        recheck_jobs.append(
            (
                ("recheck", "city", "last"),
                busan_bukgu_city_list_url(city_last),
                lambda soup: _parse_city_page(soup, page=city_last, expected_last=city_last),
            )
        )
    rechecks = run_jobs(recheck_jobs, list_phase=True)
    meta["stability_rechecks"] = len(rechecks.values)
    if rechecks.errors or len(rechecks.values) != len(recheck_jobs):
        meta["configured_collection_error"] = "; ".join(rechecks.errors) or ("missing one or more stability rechecks")
        return [], BUSAN_BUKGU_PARSER, meta
    try:
        comparisons: list[tuple[Any, Sequence[Mapping[str, Any]]]] = [
            (("recheck", "information", 1), local_pages_by_ledger["information"][1]),
            (("recheck", "library", 1), local_pages_by_ledger["library"][1]),
            (
                ("recheck", "library", "last"),
                local_pages_by_ledger["library"][actual_last_by_ledger["library"]],
            ),
            (("recheck", "small", 1), local_pages_by_ledger["small_library"][1]),
            (
                ("recheck", "small", "last"),
                local_pages_by_ledger["small_library"][actual_last_by_ledger["small_library"]],
            ),
            (("recheck", "lifelong", "default-first"), default_pages[1]),
            (
                ("recheck", "lifelong", "default-last"),
                default_pages[max(data_pages)],
            ),
            (("recheck", "lifelong", "ing"), partition_pages["ing"][1]),
            (("recheck", "lifelong", "wait"), partition_pages["wait"][1]),
            (
                ("recheck", "lifelong", "close-last"),
                partition_pages["close"][advertised[("lifelong", "close")]],
            ),
        ]
        for key, original in comparisons:
            if key[1] in {"information", "library", "small"}:
                ledger = (
                    _INFORMATION if key[1] == "information" else _LIBRARY if key[1] == "library" else _SMALL_LIBRARY
                )
                page = 1 if key[-1] == 1 else actual_last_by_ledger[ledger.key]
                partition = ""
            else:
                ledger = _LIFELONG
                if key[-1] == "default-first":
                    page, partition = 1, ""
                elif key[-1] == "default-last":
                    page, partition = max(data_pages), ""
                elif key[-1] == "close-last":
                    page, partition = advertised[("lifelong", "close")], "close"
                else:
                    page, partition = 1, str(key[-1])
            rows, _, _ = _parse_local_page(
                rechecks.values[key][0],
                ledger=ledger,
                page=page,
                cutoff=cutoff,
                register_status=partition,
                expected_total=totals[(ledger.key, partition)],
                expected_advertised_last=advertised[(ledger.key, partition)],
            )
            if _local_signature(rows) != _local_signature(original):
                raise BusanBukguContractError(f"boundary {key} changed")
        for office_code in BUSAN_LIFELONG_BUKGU_OFFICES:
            rows, _ = _parse_platform_page(
                rechecks.values[("recheck", "platform", office_code)][0],
                office_code=office_code,
                page=1,
                expected_last=1,
            )
            if _platform_archive_signature(rows) != _platform_archive_signature(platform_rows_by_office[office_code]):
                raise BusanBukguContractError(f"platform {office_code} changed after details")
        city_first_check, _ = _parse_city_page(
            rechecks.values[("recheck", "city", "first")][0],
            page=1,
            expected_last=city_last,
        )
        if _city_signature(city_first_check) != _city_signature(city_pages[1]):
            raise BusanBukguContractError("Busan city first boundary changed")
        if city_last > 1:
            city_last_check, _ = _parse_city_page(
                rechecks.values[("recheck", "city", "last")][0],
                page=city_last,
                expected_last=city_last,
            )
            if _city_signature(city_last_check) != _city_signature(city_pages[city_last]):
                raise BusanBukguContractError("Busan city final boundary changed")
    except Exception as exc:
        meta["configured_collection_error"] = f"stability recheck: {_clean(exc)}"
        return [], BUSAN_BUKGU_PARSER, meta

    safe_rows: list[dict[str, Any]] = []
    privacy_redactions = 0
    for row in enriched:
        safe, count = _sanitize_row(row)
        safe_rows.append(safe)
        privacy_redactions += count
    deduper = dedupe_rows or _default_dedupe
    result = list(deduper(safe_rows))
    if len(result) != len(safe_rows):
        meta["configured_collection_error"] = f"dedupe changed atomic row count {len(safe_rows)} to {len(result)}"
        return [], BUSAN_BUKGU_PARSER, meta

    status_counts = Counter(_clean(row.get("status")) for row in result)
    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    raw_district_rows = (
        len(information_rows)
        + totals[("lifelong", "")]
        + len(library_rows)
        + len(local_rows_by_ledger["small_library"])
    )
    rendered_district_rows = (
        len(information_rows) + len(lifelong_by_id) + len(library_rows) + len(local_rows_by_ledger["small_library"])
    )
    unique_real_source_rows = (
        len(information_rows)
        + len(lifelong_by_id)
        + len(library_rows)
        + len(local_rows_by_ledger["small_library"])
        + len(native_rows)
        + len(city_rows)
        - len(excluded_tests)
    )
    meta.update(
        {
            "network_requests": budget.count,
            "required_list_requests": meta["list_requests"],
            "sentinel_requests": (len(advertised) + len(BUSAN_LIFELONG_BUKGU_OFFICES) + 1),
            "information_source_rows": len(information_rows),
            "lifelong_declared_rows": totals[("lifelong", "")],
            "lifelong_default_rendered_rows": len(default_rows),
            "lifelong_default_data_pages": len(data_pages),
            "lifelong_default_error_shell_pages": default_errors,
            "lifelong_partition_totals": {key: totals[("lifelong", key)] for key in ("ing", "wait", "close")},
            "lifelong_partition_page_counts": {
                key: {page: len(rows) for page, rows in pages.items()} for key, pages in partition_pages.items()
            },
            "lifelong_status_union_rows": len(lifelong_by_id),
            "lifelong_declared_unrendered_rows": declared_gap,
            "lifelong_status_partition_recovered_rows": recovered,
            "library_source_rows": len(library_rows),
            "library_advertised_last_page": advertised[("library", "")],
            "library_actual_data_pages": actual_last_by_ledger["library"],
            "library_page_counts": {page: len(rows) for page, rows in local_pages_by_ledger["library"].items() if rows},
            "library_list_projected_current_rows": len(library_list_projected_current),
            "library_current_year_detail_probe_rows": len(library_detail_candidates),
            "library_projection_recovered_current_rows": (len(library_current) - len(library_list_projected_current)),
            "library_current_count": len(library_current),
            "small_library_source_rows": len(local_rows_by_ledger["small_library"]),
            "small_library_independent_rows": len(small_by_id),
            "small_library_identity_overlap_rows": 0,
            "small_library_duplicate_rows": 0,
            "small_library_list_projected_current_rows": len(small_library_list_projected_current),
            "small_library_current_year_detail_probe_rows": len(small_library_detail_candidates),
            "small_library_projection_recovered_current_rows": (
                len(small_library_current) - len(small_library_list_projected_current)
            ),
            "small_library_current_count": len(small_library_current),
            "district_source_rows": rendered_district_rows,
            "district_current_before_test_exclusion": len(district_current_before_test),
            "district_exact_test_rows_excluded": len(excluded_tests),
            "district_current_count": len(district_current),
            "platform_source_rows": sum(len(rows) for rows in platform_rows_by_office.values()),
            "platform_office_counts": {key: len(rows) for key, rows in platform_rows_by_office.items()},
            "platform_native_rows": len(native_rows),
            "platform_native_current_count": len(native_current),
            "platform_external_duplicate_rows": len(external_rows),
            "platform_external_unmatched_rows": 0,
            "platform_external_projection_mismatch_rows": (external_projection_mismatch_rows),
            "platform_external_projection_mismatch_fields": dict(external_projection_mismatch_fields),
            "city_source_rows": len(city_rows),
            "city_data_pages": city_last,
            "city_page_counts": {page: len(rows) for page, rows in city_pages.items() if page <= city_last},
            "city_current_count": len(city_current),
            "source_total": raw_district_rows
            + sum(len(rows) for rows in platform_rows_by_office.values())
            + len(city_rows),
            "source_rows": rendered_district_rows
            + sum(len(rows) for rows in platform_rows_by_office.values())
            + len(city_rows),
            "unique_education_source_rows": unique_real_source_rows,
            "current_source_count": len(current_rows),
            "expired_count": unique_real_source_rows - len(current_rows),
            "non_current_count": unique_real_source_rows - len(current_rows),
            "returned_count": len(result),
            "detail_pages": len(details.values),
            "detail_errors": 0,
            "application_control_count": sum(bool(row.get("reservation_available")) for row in result),
            "offline_application_count": sum(row.get("application_type") == "OFFLINE_APPLY" for row in result),
            "status_counts": dict(status_counts),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "duplicate_source_identity_count": len(external_rows),
            "privacy_redactions": privacy_redactions,
            "pagination_detected": True,
            "pagination_complete": True,
            "details_complete": True,
            "snapshot_complete": True,
            "source_cap_reached": False,
            "no_current_data": not result,
            "no_current_reason": ("all unique real education rows ended before the crawl date" if not result else ""),
            "configured_collection_error": "",
        }
    )
    return result, BUSAN_BUKGU_PARSER, meta


collect_courses = collect_busan_bukgu_education
collect = collect_busan_bukgu_education


__all__ = [
    "BUSAN_BUKGU_PROVIDER",
    "BUSAN_BUKGU_HOME_PROVIDER",
    "BUSAN_BUKGU_LIFELONG_DETAIL_PROVIDER",
    "BUSAN_BUKGU_LIBRARY_PROVIDER",
    "BUSAN_LIFELONG_PROVIDER",
    "BUSAN_BUKGU_MUNICIPALITY_CODE",
    "BUSAN_BUKGU_MUNICIPALITY_NAME",
    "BUSAN_BUKGU_URL",
    "BUSAN_BUKGU_CANONICAL_URL",
    "BUSAN_BUKGU_HOME_URL",
    "BUSAN_CITY_BUKGU_URL",
    "BUSAN_LIFELONG_BUKGU_OFFICES",
    "BUSAN_BUKGU_PARSER",
    "BUSAN_BUKGU_CANDIDATE_IDS",
    "BUSAN_BUKGU_OWNER_BOUNDARY_AUDIT",
    "BUSAN_BUKGU_DISCOVERY_AUDIT",
    "BUSAN_BUKGU_LOCAL_LEDGERS",
    "BusanBukguContractError",
    "is_busan_bukgu_education_target",
    "is_target",
    "busan_bukgu_list_url",
    "busan_bukgu_detail_url",
    "busan_bukgu_lifelong_list_url",
    "busan_bukgu_lifelong_detail_url",
    "busan_bukgu_city_list_url",
    "busan_bukgu_city_detail_url",
    "canonical_busan_bukgu_course_identity",
    "collect_busan_bukgu_education",
    "collect_courses",
    "collect",
]
