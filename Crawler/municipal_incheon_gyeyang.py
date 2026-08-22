"""Atomic collector for Gyeyang-gu's official public education ledger.

The Gyeyang lifelong-learning portal owns three public catalogues behind one
controller: the lifelong-learning centre, twelve resident centres, and the
``골목틈새학교`` programme.  The tempting ``acptrun=y`` view is only an
application-window filter: it omits both not-yet-open courses and many courses
whose registration ended before their education did.  This collector instead
exhausts all 2,000+ archived rows in the three owner partitions, determines
current/future scope from the education end date, and uses the ``all`` view as
an independently totalled, stable aggregate boundary check.

Every partition's advertised total, exact final-page clamp, and stable
first/final boundaries are required.  Every current/future row is then
validated against its same-host, identity-bound detail page.

Registration forms, applicant data, attachments, images, instructor/contact
fields, and free-form detail bodies are deliberately never followed or
stored.  A single, exactly bound public practice course is validated and then
suppressed.  Any pagination, ownership, branch, identity, date, status, or
detail drift suppresses the entire snapshot.

``Crawler_MunicipalYaml`` injects its managed session and dedupe helpers when
the promoted municipal target is collected.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


GYEYANG_PROVIDER = "MUNI_GYLLE_GYEYANG_GO_KR_1630ABDE"
GYEYANG_CANDIDATE_ID = "MUNI_IR_0CA80BB9B401"
GYEYANG_LEGACY_HOME_CANDIDATE_ID = "MUNI_IR_9FBB86F259B7"
GYEYANG_MUNICIPALITY_CODE = "2824500000"
GYEYANG_MUNICIPALITY_NAME = "인천광역시 계양구"

GYEYANG_HOST = "gylle.gyeyang.go.kr"
GYEYANG_LIST_PATH = "/program/programInfoList.do"
GYEYANG_DETAIL_PATH = "/program/programInfoDetail.do"
GYEYANG_APPLICATION_PATH = "/program/programAcptRegForm.do"
GYEYANG_URL = f"https://{GYEYANG_HOST}{GYEYANG_LIST_PATH}?prgmdiv=all"
GYEYANG_PAGE_SIZE = 10
GYEYANG_MAX_WORKERS = 6
GYEYANG_FETCH_ATTEMPTS = 3
GYEYANG_MAX_HTML_BYTES = 2_000_000
GYEYANG_PARSER = (
    "gyeyang_three_complete_official_education_archives+current_by_end_date+"
    "aggregate_total_and_boundary+"
    "declared_totals+exact_final_page_clamps+stable_first_final_boundaries+"
    "all_current_safe_details+identity_bound_application_controls+"
    "exact_practice_course_suppression+pii_allowlist+atomic_snapshot"
)
GYEYANG_OWNERSHIP_SCOPE = (
    "gyeyang_lifelong_resident_centres_and_alley_school_public_courses"
)


@dataclass(frozen=True)
class GyeyangCatalogue:
    key: str
    label: str
    default_branch: str


GYEYANG_CATALOGUES = (
    GyeyangCatalogue("life", "평생학습관", "평생학습관"),
    GyeyangCatalogue("citizen", "주민자치센터", ""),
    GyeyangCatalogue("school", "골목틈새학교", "골목틈새학교"),
)
GYEYANG_AGGREGATE = GyeyangCatalogue("all", "교육신청", "")
_CATALOGUE_BY_KEY = {
    item.key: item for item in (*GYEYANG_CATALOGUES, GYEYANG_AGGREGATE)
}

GYEYANG_RESIDENT_CENTRES: Mapping[str, str] = {
    "hyosung1": "효성1동",
    "hyosung2": "효성2동",
    "gyesan1": "계산1동",
    "gyesan2": "계산2동",
    "gyesan3": "계산3동",
    "gyesan4": "계산4동",
    "jakjeon1": "작전1동",
    "jakjeon2": "작전2동",
    "jakjeonseoun": "작전서운동",
    "gyeyang1": "계양1동",
    "gyeyang2": "계양2동",
    "gyeyang3": "계양3동",
}
_RESIDENT_BRANCHES = frozenset(GYEYANG_RESIDENT_CENTRES.values())
_ALL_FIXED_BRANCHES = _RESIDENT_BRANCHES | {"평생학습관", "골목틈새학교"}

GYEYANG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    GYEYANG_CANDIDATE_ID: {
        "decision": "canonical_complete_three_catalogue_owner",
        "provider": GYEYANG_PROVIDER,
        "url": GYEYANG_URL,
    },
    GYEYANG_LEGACY_HOME_CANDIDATE_ID: {
        "decision": "retarget_portal_home_to_complete_three_catalogue_owner",
        "provider": GYEYANG_PROVIDER,
        "url": "https://gylle.gyeyang.go.kr/main/main.do",
        "canonical_url": GYEYANG_URL,
    },
    "MUNI_IR_E7645B1861FF": {
        "decision": "duplicate_resident_catalogue_alias",
        "provider": "MUNI_GYLLE_GYEYANG_GO_KR_5F2FE039",
        "url": "https://gylle.gyeyang.go.kr/edu/center.jsp",
        "owner": GYEYANG_PROVIDER,
    },
    "MUNI_IR_59BA11A61563": {
        "decision": "duplicate_lifelong_catalogue_alias",
        "provider": "MUNI_GYLLE_GYEYANG_GO_KR_CE66C2A4",
        "url": "https://gylle.gyeyang.go.kr/edu/lle.jsp",
        "owner": GYEYANG_PROVIDER,
    },
    "GYEYANG_LIFELONG": {
        "decision": "duplicate_legacy_public_reservation_home_alias",
        "url": "https://gylle.gyeyang.go.kr/main.jsp",
        "owner": GYEYANG_PROVIDER,
    },
    "MUNI_IR_EAFA3C86B2E4": {
        "decision": "separate_public_corporation_culture_centre_owner",
        "provider": "MUNI_GYCCENTER_GYSISEOL_OR_KR_31C8D6F3",
        "url": "https://gyccenter.gysiseol.or.kr/sub/district.jsp",
    },
}

GYEYANG_EXCLUDED_SCOPE: Mapping[str, Mapping[str, str]] = {
    "culture_centre": {
        "url": "https://gyccenter.gysiseol.or.kr/sub/district.jsp",
        "reason": "separate_facilities_corporation_course_owner",
    },
    "municipal_home": {
        "url": "https://www.gyeyang.go.kr/open_content/main/",
        "reason": "district_information_shell_not_course_ledger",
    },
    "naver_login": {
        "url": "https://nid.naver.com/user2/campaign/introNaverIdLogin.nhn",
        "reason": "external_authentication_endpoint_not_public_course_source",
    },
}

GYEYANG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "archive_rows_by_catalogue": {"life": 777, "citizen": 1582, "school": 64},
    "archive_total": 2423,
    "archive_data_pages": 244,
    "current_rows_by_catalogue": {"life": 10, "citizen": 159, "school": 9},
    "current_total": 178,
    "aggregate_archive_total": 2423,
    "current_details": 178,
    "suppressed_practice_rows": 1,
    "returned_rows": 177,
    "status_counts": {
        "OPEN": 70,
        "WAITING": 16,
        "SCHEDULED": 12,
        "CLOSED": 79,
    },
    "branch_counts": {
        "계양3동": 20,
        "계산2동": 18,
        "계산3동": 17,
        "작전서운동": 17,
        "계양2동": 15,
        "계양1동": 15,
        "작전2동": 15,
        "계산4동": 14,
        "평생학습관": 9,
        "효성1동": 9,
        "골목틈새학교": 9,
        "효성2동": 8,
        "작전1동": 7,
        "계산1동": 4,
    },
    "online_application_controls": 48,
    "reconciled_open_waiting_status_rows": 1,
    "closed_rows_without_application_period": 2,
    "logical_network_requests": 433,
    "current_identity_sha256": "f58d5687f38baedf8b481be4235e169e396345e51dedf40c96a7e480db4a4d30",
    "duplicate_source_rows": 0,
    "semantic_duplicate_rows": 0,
}


class GyeyangContractError(ValueError):
    """Raised when the audited Gyeyang public-course contract changes."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_SUMMARY_RE = re.compile(
    r"전체\s*([\d,]+)건\s*,\s*현재페이지\s*([\d,]+)\s*/\s*([\d,]+)"
)
_FULL_PERIOD_RE = re.compile(
    r"^\s*(20\d{2})[.-](\d{2})[.-](\d{2})\s*~\s*"
    r"(20\d{2})[.-](\d{2})[.-](\d{2})\s*$"
)
_CAPACITY_PAIR_RE = re.compile(r"(?:방문|온라인)?\s*:?\s*(\d[\d,]*)\s*/\s*(\d[\d,]*)\s*명?")
_CAPACITY_TOTAL_RE = re.compile(r"(\d[\d,]*)\s*명")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]\d{4}(?!\d)")

_STATUS_MAP: Mapping[str, str] = {
    "정시접수중": "OPEN",
    "추가접수중": "OPEN",
    "방문접수중": "OPEN",
    "대기접수": "WAITING",
    "접수마감": "CLOSED",
    "정시접수예정": "SCHEDULED",
    "추가접수예정": "SCHEDULED",
    "방문접수예정": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "신청예정": "SCHEDULED",
}
_ONLINE_CONTROL_STATUSES = frozenset({"정시접수중", "추가접수중", "대기접수"})
_LIST_ALLOWED_FIELDS = frozenset(
    {
        "정시접수",
        "추가접수",
        "방문접수",
        "주최기관",
        "교육일정",
        "교육시간",
        "수강료",
        "수강정원",
    }
)
_DETAIL_ALLOWED_FIELDS = frozenset(
    {
        "정시 접수",
        "추가 접수",
        "방문 접수",
        "분야",
        "교육기관",
        "추첨여부",
        "정원",
        "대기",
        "교육 레벨",
        "교육 대상",
        "나이제한",
        "교육기간",
        "교육 요일",
        "교육 시간",
        "수강료",
        "재료비",
        "강의실",
    }
)
_DETAIL_REQUIRED_FIELDS = frozenset(
    {
        "분야",
        "교육기관",
        "추첨여부",
        "정원",
        "대기",
        "교육 레벨",
        "교육 대상",
        "나이제한",
        "교육기간",
        "교육 요일",
        "교육 시간",
        "수강료",
        "재료비",
        "강의실",
    }
)
_APPLICATION_FIELDS = ("추가 접수", "정시 접수", "방문 접수")
_PRACTICE_ROW: Mapping[str, str] = {
    "identity": "1431",
    "division": "life",
    "title": "수강신청을 연습하는 화면입니다. (실제 강좌 없음)",
    "branch": "평생학습관",
    "start_date": "2027-01-01",
    "end_date": "2027-01-01",
}
_AUDITED_HISTORICAL_PERIOD_ANOMALIES: Mapping[
    tuple[str, str], Mapping[str, str]
] = {
    ("citizen", "1051"): {
        "title": "컴퓨터 A반 (기초)",
        "branch": "작전2동",
        "period": "~ 2024.03.31",
        "end_date": "2024-03-31",
    },
    ("citizen", "831"): {
        "title": "명화로 만나는 나의 재능",
        "branch": "작전2동",
        "period": "~",
        "end_date": "",
    },
}
_NONPRODUCTION_TOKENS = ("테스트", "연습", "실제 강좌 없음", "test", "sample", "샘플")


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True) if node is not None else "")


def _compact_label(value: Any) -> str:
    return re.sub(r"[\s:]", "", _clean(value))


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _normalized_target_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname != GYEYANG_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
        or parsed.path != GYEYANG_LIST_PATH
    ):
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query != {"prgmdiv": ["all"]}:
        return ""
    return GYEYANG_URL


def is_incheon_gyeyang_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != GYEYANG_PROVIDER:
        return False
    if _normalized_target_url(_target_value(target, "url")) != GYEYANG_URL:
        return False
    candidate = _clean(_target_value(target, "candidate_id"))
    return not candidate or candidate in {
        GYEYANG_CANDIDATE_ID,
        GYEYANG_LEGACY_HOME_CANDIDATE_ID,
    }


is_target = is_incheon_gyeyang_education_target


def gyeyang_list_url(catalogue: GyeyangCatalogue | str, page: int = 1) -> str:
    item = _CATALOGUE_BY_KEY[catalogue] if isinstance(catalogue, str) else catalogue
    query = urlencode(
        (
            ("prgmdiv", item.key),
            ("acptrun", "n"),
            ("orderby", "edt"),
            ("pgno", str(max(1, int(page)))),
        )
    )
    return f"https://{GYEYANG_HOST}{GYEYANG_LIST_PATH}?{query}"


def gyeyang_detail_url(division: str, identity: Any) -> str:
    source_id = _clean(identity)
    if division not in {item.key for item in GYEYANG_CATALOGUES}:
        return ""
    if not _IDENTITY_RE.fullmatch(source_id):
        return ""
    query = urlencode((("prgm_seq", source_id), ("prgmdiv", division)))
    return f"https://{GYEYANG_HOST}{GYEYANG_DETAIL_PATH}?{query}"


def _canonical_link(page_url: str, candidate: Any, *, expected_division: str) -> tuple[str, str]:
    resolved = urlparse(urljoin(page_url, _clean(candidate)))
    if (
        resolved.scheme.lower() != "https"
        or resolved.hostname != GYEYANG_HOST
        or resolved.port is not None
        or resolved.username
        or resolved.password
        or resolved.path != GYEYANG_DETAIL_PATH
        or resolved.params
        or resolved.fragment
    ):
        raise GyeyangContractError("detail link escaped the official controller")
    query = parse_qs(resolved.query, keep_blank_values=True)
    identity_values = query.get("prgm_seq") or []
    division_values = query.get("prgmdiv") or []
    if len(identity_values) != 1 or not _IDENTITY_RE.fullmatch(identity_values[0]):
        raise GyeyangContractError("detail link has an invalid programme identity")
    if division_values != [expected_division]:
        raise GyeyangContractError("detail link changed catalogue ownership")
    identity = identity_values[0]
    canonical_division = expected_division
    if expected_division == "all":
        return (
            f"https://{GYEYANG_HOST}{GYEYANG_DETAIL_PATH}?"
            + urlencode((("prgm_seq", identity), ("prgmdiv", "all"))),
            identity,
        )
    return gyeyang_detail_url(canonical_division, identity), identity


def _parse_date_range(value: Any) -> tuple[str, str]:
    match = _FULL_PERIOD_RE.fullmatch(_clean(value))
    if not match:
        raise GyeyangContractError(f"invalid education period: {_clean(value)!r}")
    start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
    if end < start:
        raise GyeyangContractError("reversed education period")
    return start.isoformat(), end.isoformat()


def _list_date_range(
    catalogue: GyeyangCatalogue,
    identity: str,
    title: str,
    branch: str,
    value: Any,
) -> tuple[str, str, bool]:
    period = _clean(value)
    try:
        start, end = _parse_date_range(period)
        return start, end, False
    except (GyeyangContractError, ValueError):
        audited = _AUDITED_HISTORICAL_PERIOD_ANOMALIES.get(
            (catalogue.key, identity)
        )
        observed = {
            "title": title,
            "branch": branch,
            "period": period,
            "end_date": _clean(audited.get("end_date")) if audited else "",
        }
        if audited is None or observed != dict(audited):
            raise GyeyangContractError(
                f"unaudited education-period anomaly for {catalogue.key}:{identity}"
            )
        end = _clean(audited.get("end_date")) or "1900-01-01"
        return end, end, True


def _list_pairs(item: Tag) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for field in item.select("ul.lec_info > li"):
        key_node = field.select_one(".q")
        compact = _compact_label(_text(key_node))
        canonical = next(
            (label for label in _LIST_ALLOWED_FIELDS if _compact_label(label) == compact),
            "",
        )
        if not canonical:
            # In particular, never read the public contact value.
            continue
        clone = BeautifulSoup(str(field), "lxml")
        for node in clone.select(".q"):
            node.extract()
        value = _text(clone)
        if canonical in pairs:
            raise GyeyangContractError(f"duplicate list field {canonical}")
        pairs[canonical] = value.lstrip(": ")
    return pairs


def _branch_for_list(catalogue: GyeyangCatalogue, host_branch: str) -> str:
    if catalogue.key == "life":
        if host_branch != "평생학습관":
            raise GyeyangContractError("lifelong catalogue branch changed")
        return host_branch
    if catalogue.key == "citizen":
        if host_branch not in _RESIDENT_BRANCHES:
            raise GyeyangContractError(f"unknown resident-centre branch {host_branch!r}")
        return host_branch
    if catalogue.key == "school":
        if host_branch != "골목틈새학교":
            raise GyeyangContractError("alley-school branch changed")
        return host_branch
    if host_branch not in _ALL_FIXED_BRANCHES:
        raise GyeyangContractError(f"aggregate exposed an unknown branch {host_branch!r}")
    return host_branch


def _parse_list_item(
    target: Any,
    item: Tag,
    catalogue: GyeyangCatalogue,
    page_url: str,
) -> dict[str, Any]:
    link = item.select_one("p.tit > a[href*='programInfoDetail.do'][href*='prgm_seq=']")
    if link is None:
        raise GyeyangContractError("course card has no identity-bound detail link")
    raw_url, identity = _canonical_link(
        page_url, link.get("href"), expected_division=catalogue.key
    )
    title = _text(link)
    if not title:
        raise GyeyangContractError("course card has a blank title")
    status_node = item.select_one("p.tag_state")
    raw_status = _text(status_node)
    if raw_status not in _STATUS_MAP:
        raise GyeyangContractError(f"unknown list status {raw_status!r}")
    pairs = _list_pairs(item)
    required = {"주최기관", "교육일정", "교육시간", "수강료", "수강정원"}
    missing = required - set(pairs)
    if missing:
        raise GyeyangContractError(f"list row missing fields {sorted(missing)!r}")
    application_fields = [key for key in ("추가접수", "정시접수", "방문접수") if key in pairs]
    branch = _branch_for_list(catalogue, pairs["주최기관"])
    start_date, end_date, historical_period_anomaly = _list_date_range(
        catalogue,
        identity,
        title,
        branch,
        pairs["교육일정"],
    )
    return {
        "provider": _clean(_target_value(target, "provider")),
        "provider_course_id": f"{GYEYANG_PROVIDER}:prgm:{identity}",
        "title": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "category": catalogue.label,
        "raw_url": raw_url,
        "status": _STATUS_MAP[raw_status],
        "reservation_available": _STATUS_MAP[raw_status] in {"OPEN", "WAITING"},
        "period": pairs["교육일정"],
        "apply_period": pairs[application_fields[0]] if application_fields else "",
        "schedule_raw": pairs["교육시간"],
        "fee": pairs["수강료"],
        "capacity": pairs["수강정원"],
        "venue_name": branch,
        "program_type": "강좌",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html",
        "start_date": start_date,
        "end_date": end_date,
        "raw_fields": {
            "division": catalogue.key,
            "prgm_seq": identity,
            "list_status": raw_status,
            "list_application_kind": application_fields[0] if application_fields else "",
            "historical_period_anomaly": historical_period_anomaly,
            "parser": "gyeyang_current_course",
        },
        "_division": catalogue.key,
        "_identity": identity,
        "_list_status": raw_status,
        "_list_branch": branch,
        "_historical_period_anomaly": historical_period_anomaly,
    }


def _branch_code(branch: str) -> str:
    if branch == "평생학습관":
        return "GYEYANG_LIFELONG"
    if branch == "골목틈새학교":
        return "GYEYANG_ALLEY_SCHOOL"
    code = next((code for code, name in GYEYANG_RESIDENT_CENTRES.items() if name == branch), "")
    if not code:
        raise GyeyangContractError(f"branch has no stable code: {branch!r}")
    return f"GYEYANG_{code.upper()}"


def _summary(soup: BeautifulSoup) -> tuple[int, int, int]:
    node = soup.select_one(".edu_array")
    match = _SUMMARY_RE.search(_text(node))
    if not match:
        raise GyeyangContractError("course total/page summary changed")
    return tuple(int(value.replace(",", "")) for value in match.groups())  # type: ignore[return-value]


def _validate_citizen_registry(soup: BeautifulSoup) -> None:
    actual = {
        _clean(node.get("value"))
        for node in soup.select("input[name='cate2'][value]")
    }
    if actual != set(GYEYANG_RESIDENT_CENTRES):
        raise GyeyangContractError(
            f"resident-centre registry changed: expected {sorted(GYEYANG_RESIDENT_CENTRES)!r}, "
            f"got {sorted(actual)!r}"
        )


def _parse_page(
    target: Any,
    soup: BeautifulSoup,
    catalogue: GyeyangCatalogue,
    *,
    requested_page: int,
    expected_total: Optional[int] = None,
    allow_clamp: bool = False,
) -> tuple[list[dict[str, Any]], int, int, int]:
    state = soup.select_one(".edu_state")
    if state is None or "off" not in (state.get("class") or []):
        raise GyeyangContractError("server did not preserve the complete-archive filter")
    hidden = soup.select_one(
        f"form[action='{GYEYANG_LIST_PATH}'] input[name='prgmdiv'][value='{catalogue.key}']"
    )
    if hidden is None:
        raise GyeyangContractError("catalogue form identity changed")
    if catalogue.key == "citizen":
        _validate_citizen_registry(soup)
    total, reported_page, reported_last = _summary(soup)
    if expected_total is not None and total != expected_total:
        raise GyeyangContractError("advertised catalogue total changed during scan")
    expected_last = math.ceil(total / GYEYANG_PAGE_SIZE) if total else 0
    if reported_last != expected_last:
        raise GyeyangContractError("advertised final page disagrees with total")
    if total == 0:
        if reported_page != 0:
            raise GyeyangContractError("empty catalogue has a nonzero current page")
    elif allow_clamp:
        if reported_page != expected_last:
            raise GyeyangContractError("page beyond final boundary did not clamp exactly")
    elif reported_page != requested_page:
        raise GyeyangContractError("requested catalogue page was silently clamped")
    page_url = gyeyang_list_url(catalogue, requested_page)
    rows = [
        _parse_list_item(target, item, catalogue, page_url)
        for item in soup.select("ul.lecList > li")
    ]
    if len(rows) > GYEYANG_PAGE_SIZE:
        raise GyeyangContractError("catalogue page exceeds the audited page size")
    if total == 0 and rows:
        raise GyeyangContractError("empty catalogue unexpectedly exposed rows")
    return rows, total, reported_page, reported_last


def _source_signature(rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("_identity")),
            _clean(row.get("title")),
            _clean(row.get("_list_status")),
            _clean(row.get("_list_branch")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
        )
        for row in rows
    )


@dataclass(frozen=True)
class _CatalogueSnapshot:
    rows: list[dict[str, Any]]
    total: int
    pages: int
    list_requests: int
    stability_rechecks: int
    sentinel_kind: str


class _RequestBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0
        self._lock = threading.Lock()

    def claim(self) -> None:
        with self._lock:
            if self.count >= self.limit:
                raise GyeyangContractError("network request cap exceeded")
            self.count += 1


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MoonCenBot/1.0; public-course-audit)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    content = bytes(response.content)
    if len(content) > GYEYANG_MAX_HTML_BYTES:
        raise GyeyangContractError("HTML response exceeded the audited size limit")
    encoding = response.encoding or "utf-8"
    return BeautifulSoup(content.decode(encoding, errors="replace"), "lxml")


def _as_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    status_code = int(getattr(value, "status_code", 200) or 0)
    if status_code >= 400:
        error = requests.HTTPError(f"HTTP {status_code}")
        error.response = value
        raise error
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", "")).encode("utf-8")
    content = bytes(content)
    if len(content) > GYEYANG_MAX_HTML_BYTES:
        raise GyeyangContractError("HTML response exceeded the audited size limit")
    encoding = getattr(value, "encoding", None) or "utf-8"
    return BeautifulSoup(content.decode(encoding, errors="replace"), "lxml")


def _fetch_soup(
    current_session: Any,
    url: str,
    *,
    fetcher: Fetcher,
    timeout: int,
    attempts: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
) -> tuple[BeautifulSoup, int]:
    retries = 0
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        budget.claim()
        try:
            return _as_soup(fetcher(current_session, url, timeout)), retries
        except GyeyangContractError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            retries += 1
            sleeper(min(2.0, 0.25 * (2**attempt)))
    assert last_error is not None
    raise last_error


def _scan_catalogue(
    target: Any,
    catalogue: GyeyangCatalogue,
    *,
    current_session: Any,
    fetcher: Fetcher,
    timeout: int,
    attempts: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
    max_pages: int,
) -> tuple[_CatalogueSnapshot, int]:
    retries = 0
    first_soup, count = _fetch_soup(
        current_session,
        gyeyang_list_url(catalogue, 1),
        fetcher=fetcher,
        timeout=timeout,
        attempts=attempts,
        sleeper=sleeper,
        budget=budget,
    )
    retries += count
    first_rows, total, _, last_page = _parse_page(
        target, first_soup, catalogue, requested_page=1
    )
    data_pages = max(1, last_page)
    sentinel_page = max(2, last_page + 1)
    if sentinel_page > max_pages:
        raise GyeyangContractError(
            f"{catalogue.key} sentinel page {sentinel_page} exceeds max_pages"
        )
    pages: list[list[dict[str, Any]]] = [first_rows]
    for page in range(2, last_page + 1):
        soup, count = _fetch_soup(
            current_session,
            gyeyang_list_url(catalogue, page),
            fetcher=fetcher,
            timeout=timeout,
            attempts=attempts,
            sleeper=sleeper,
            budget=budget,
        )
        retries += count
        rows, _, _, _ = _parse_page(
            target,
            soup,
            catalogue,
            requested_page=page,
            expected_total=total,
        )
        pages.append(rows)
    flattened = [row for page_rows in pages for row in page_rows]
    if len(flattened) != total:
        raise GyeyangContractError(
            f"{catalogue.key} rows {len(flattened)} do not match advertised total {total}"
        )
    if total:
        for index, page_rows in enumerate(pages, 1):
            expected_size = (
                GYEYANG_PAGE_SIZE
                if index < last_page
                else total - GYEYANG_PAGE_SIZE * (last_page - 1)
            )
            if len(page_rows) != expected_size:
                raise GyeyangContractError(
                    f"{catalogue.key} page {index} has an unexpected row count"
                )
    identities = [_clean(row.get("_identity")) for row in flattened]
    if len(identities) != len(set(identities)):
        raise GyeyangContractError(f"duplicate source identity in {catalogue.key}")

    sentinel_soup, count = _fetch_soup(
        current_session,
        gyeyang_list_url(catalogue, sentinel_page),
        fetcher=fetcher,
        timeout=timeout,
        attempts=attempts,
        sleeper=sleeper,
        budget=budget,
    )
    retries += count
    sentinel_rows, _, _, _ = _parse_page(
        target,
        sentinel_soup,
        catalogue,
        requested_page=sentinel_page,
        expected_total=total,
        allow_clamp=bool(total),
    )
    if total:
        if _source_signature(sentinel_rows) != _source_signature(pages[-1]):
            raise GyeyangContractError(f"{catalogue.key} final-page clamp changed")
        sentinel_kind = "exact_final_page_clamp"
    else:
        if sentinel_rows:
            raise GyeyangContractError(f"{catalogue.key} empty sentinel exposed rows")
        sentinel_kind = "stable_empty"

    boundary_pages = [(1, _source_signature(pages[0]))]
    if last_page > 1:
        boundary_pages.append((last_page, _source_signature(pages[-1])))
    for page, expected in boundary_pages:
        soup, count = _fetch_soup(
            current_session,
            gyeyang_list_url(catalogue, page),
            fetcher=fetcher,
            timeout=timeout,
            attempts=attempts,
            sleeper=sleeper,
            budget=budget,
        )
        retries += count
        rows, _, _, _ = _parse_page(
            target,
            soup,
            catalogue,
            requested_page=page,
            expected_total=total,
        )
        if _source_signature(rows) != expected:
            raise GyeyangContractError(
                f"{catalogue.key} page {page} changed during stable recheck"
            )
    return (
        _CatalogueSnapshot(
            rows=flattened,
            total=total,
            pages=last_page,
            list_requests=data_pages + 1 + len(boundary_pages),
            stability_rechecks=len(boundary_pages),
            sentinel_kind=sentinel_kind,
        ),
        retries,
    )


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in soup.select(".board_view ul.data_list dl"):
        key_node = item.find("dt", recursive=False)
        key = _text(key_node)
        if key not in _DETAIL_ALLOWED_FIELDS:
            # Instructor/contact values are intentionally not read.
            continue
        value_node = item.find("dd", recursive=False)
        value = _text(value_node)
        if key in pairs:
            raise GyeyangContractError(f"duplicate detail field {key}")
        pairs[key] = value
    missing = _DETAIL_REQUIRED_FIELDS - set(pairs)
    if missing:
        raise GyeyangContractError(f"detail missing safe fields {sorted(missing)!r}")
    return pairs


def _capacity(value: Any) -> tuple[Optional[int], Optional[int]]:
    matches = [
        (int(current.replace(",", "")), int(total.replace(",", "")))
        for current, total in _CAPACITY_PAIR_RE.findall(_clean(value))
    ]
    if matches:
        return sum(current for current, _ in matches), sum(total for _, total in matches)
    match = _CAPACITY_TOTAL_RE.search(_clean(value))
    if match:
        return None, int(match.group(1).replace(",", ""))
    raise GyeyangContractError("detail capacity format changed")


def _waitlist(value: Any) -> tuple[Optional[int], Optional[int]]:
    match = _CAPACITY_PAIR_RE.search(_clean(value))
    if not match:
        raise GyeyangContractError("detail waitlist format changed")
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


def _detail_branch(division: str, pairs: Mapping[str, str]) -> tuple[str, str]:
    field = _clean(pairs.get("분야"))
    parts = [_clean(part) for part in field.split(">")]
    institution = _clean(pairs.get("교육기관"))
    if division == "life":
        if not parts or parts[0] != "평생학습관" or institution != "평생학습관":
            raise GyeyangContractError("lifelong detail ownership changed")
        return "평생학습관", "평생학습관"
    if division == "citizen":
        if len(parts) != 2 or parts[0] != "주민자치센터" or institution != "주민자치센터":
            raise GyeyangContractError("resident-centre detail ownership changed")
        if parts[1] not in _RESIDENT_BRANCHES:
            raise GyeyangContractError(f"unknown detail branch {parts[1]!r}")
        return parts[1], parts[1]
    if division == "school":
        if not parts or parts[0] != "골목틈새학교" or institution != "골목틈새학교":
            raise GyeyangContractError("alley-school detail ownership changed")
        category = parts[1] if len(parts) == 2 and parts[1] != "없음" else "골목틈새학교"
        return "골목틈새학교", category
    raise GyeyangContractError("detail has an unknown catalogue division")


def _application_url(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
) -> str:
    controls = soup.select(f"a[href*='{GYEYANG_APPLICATION_PATH}'][href*='prgm_seq=']")
    raw_status = _clean(listed.get("_list_status"))
    expected_control = raw_status in _ONLINE_CONTROL_STATUSES
    if len(controls) != int(expected_control):
        raise GyeyangContractError(
            f"application control mismatch for status {raw_status!r}"
        )
    if not controls:
        return ""
    resolved = urlparse(urljoin(_clean(listed.get("raw_url")), controls[0].get("href")))
    if (
        resolved.scheme.lower() != "https"
        or resolved.hostname != GYEYANG_HOST
        or resolved.path != GYEYANG_APPLICATION_PATH
        or resolved.port is not None
        or resolved.username
        or resolved.password
        or resolved.params
        or resolved.fragment
    ):
        raise GyeyangContractError("application control escaped the official controller")
    query = parse_qs(resolved.query, keep_blank_values=True)
    if query.get("prgm_seq") != [_clean(listed.get("_identity"))]:
        raise GyeyangContractError("application control changed programme identity")
    if query.get("prgmdiv") != [_clean(listed.get("_division"))]:
        raise GyeyangContractError("application control changed catalogue identity")
    return (
        f"https://{GYEYANG_HOST}{GYEYANG_APPLICATION_PATH}?"
        + urlencode(
            (
                ("prgm_seq", _clean(listed.get("_identity"))),
                ("prgmdiv", _clean(listed.get("_division"))),
            )
        )
    )


def _parse_detail(soup: BeautifulSoup, listed: Mapping[str, Any]) -> dict[str, Any]:
    title_node = soup.select_one(".board_view .title p.margin_t10")
    title = _text(title_node)
    if title != _clean(listed.get("title")):
        raise GyeyangContractError("detail title does not match its list identity")
    tags = [_text(node) for node in soup.select(".board_view .title span")]
    raw_status = _clean(listed.get("_list_status"))
    status_tags = [tag for tag in tags if tag in _STATUS_MAP]
    if len(status_tags) != 1:
        raise GyeyangContractError("detail status does not match its list row")
    detail_raw_status = status_tags[0]
    list_status = _STATUS_MAP[raw_status]
    detail_status = _STATUS_MAP[detail_raw_status]
    status_reconciled = list_status != detail_status
    if status_reconciled and {list_status, detail_status} != {"OPEN", "WAITING"}:
        raise GyeyangContractError("detail status does not match its list row")
    pairs = _detail_pairs(soup)
    division = _clean(listed.get("_division"))
    branch, category = _detail_branch(division, pairs)
    if branch != _clean(listed.get("_list_branch")):
        raise GyeyangContractError("detail branch does not match its list row")
    start_date, end_date = _parse_date_range(pairs["교육기간"])
    if (start_date, end_date) != (
        _clean(listed.get("start_date")),
        _clean(listed.get("end_date")),
    ):
        raise GyeyangContractError("detail education period does not match its list row")
    application_keys = [key for key in _APPLICATION_FIELDS if key in pairs]
    list_application_kind = _clean(
        ((listed.get("raw_fields") or {}).get("list_application_kind"))
    )
    if not application_keys and (
        detail_status != "CLOSED" or list_application_kind
    ):
        raise GyeyangContractError("detail has no public application period")
    capacity_current, capacity_total = _capacity(pairs["정원"])
    waitlist_current, waitlist_total = _waitlist(pairs["대기"])
    application_url = _application_url(soup, listed)
    row = {
        key: value
        for key, value in listed.items()
        if not key.startswith("_") and key != "raw_fields"
    }
    row.update(
        {
            "branch": branch,
            "branch_code": _branch_code(branch),
            "category": category,
            "venue_name": branch,
            "status": detail_status,
            "reservation_available": detail_status in {"OPEN", "WAITING"},
            "application_url": application_url,
            "period": pairs["교육기간"],
            "apply_period": " / ".join(
                f"{key}: {pairs[key]}" for key in application_keys
            ),
            "schedule_raw": _clean(
                " ".join(
                    value
                    for value in (pairs["교육 요일"], pairs["교육 시간"])
                    if value
                )
            ),
            "target": pairs["교육 대상"],
            "age_limit": pairs["나이제한"],
            "fee": pairs["수강료"],
            "room": pairs["강의실"],
            "capacity": pairs["정원"],
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "waitlist_current": waitlist_current,
            "waitlist_total": waitlist_total,
            "application_method_raw": detail_raw_status,
            "start_date": start_date,
            "end_date": end_date,
            "raw_fields": {
                "division": division,
                "prgm_seq": _clean(listed.get("_identity")),
                "list_status": raw_status,
                "detail_status": detail_raw_status,
                "status_reconciled": status_reconciled,
                "draw_method": pairs["추첨여부"],
                "education_level": pairs["교육 레벨"],
                "application_period_kinds": application_keys,
                "parser": "gyeyang_current_course+safe_detail",
            },
        }
    )
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


@dataclass(frozen=True)
class _DetailBatch:
    values: Mapping[tuple[str, str], BeautifulSoup]
    errors: tuple[str, ...]
    retries: int
    sessions: int


def _fetch_many_details(
    rows: Sequence[Mapping[str, Any]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    attempts: int,
    max_workers: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
) -> _DetailBatch:
    local = threading.local()
    sessions: list[Any] = []
    sessions_lock = threading.Lock()

    def get_session() -> Any:
        current = getattr(local, "session", None)
        if current is None:
            current = session_factory()
            if current is None:
                raise GyeyangContractError("session factory returned no detail session")
            local.session = current
            with sessions_lock:
                sessions.append(current)
        return current

    def task(row: Mapping[str, Any]) -> tuple[tuple[str, str], BeautifulSoup, int]:
        key = (_clean(row.get("_division")), _clean(row.get("_identity")))
        soup, retry_count = _fetch_soup(
            get_session(),
            _clean(row.get("raw_url")),
            fetcher=fetcher,
            timeout=timeout,
            attempts=attempts,
            sleeper=sleeper,
            budget=budget,
        )
        return key, soup, retry_count

    values: dict[tuple[str, str], BeautifulSoup] = {}
    errors: list[str] = []
    retries = 0
    try:
        with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(rows)))) as pool:
            futures = [pool.submit(task, row) for row in rows]
            for future in as_completed(futures):
                try:
                    key, soup, retry_count = future.result()
                    if key in values:
                        raise GyeyangContractError("duplicate detail identity")
                    values[key] = soup
                    retries += retry_count
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {_clean(exc)}")
    finally:
        for current in sessions:
            _close_quietly(current)
    return _DetailBatch(values, tuple(errors), retries, len(sessions))


def _close_quietly(value: Any) -> None:
    if value is None:
        return
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value)[:10])


def _is_nonproduction_title(value: Any) -> bool:
    title = _clean(value)
    lowered = title.lower()
    return any(token.lower() in lowered for token in _NONPRODUCTION_TOKENS)


def _practice_row_matches(row: Mapping[str, Any]) -> bool:
    raw = row.get("raw_fields") if isinstance(row.get("raw_fields"), Mapping) else {}
    observed = {
        "identity": _clean(raw.get("prgm_seq")),
        "division": _clean(raw.get("division")),
        "title": _clean(row.get("title")),
        "branch": _clean(row.get("branch")),
        "start_date": _clean(row.get("start_date")),
        "end_date": _clean(row.get("end_date")),
    }
    return observed == dict(_PRACTICE_ROW)


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    normalized_title = re.sub(
        r"[^0-9A-Za-z가-힣]+", "", _clean(row.get("title"))
    ).lower()
    return (
        _clean(row.get("branch_code")),
        normalized_title,
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    )


def _failed_meta(error: str = "") -> dict[str, Any]:
    return {
        "source_total": 0,
        "source_rows": 0,
        "source_rows_by_catalogue": {},
        "aggregate_total": 0,
        "data_pages_by_catalogue": {},
        "aggregate_pages": 0,
        "current_count": 0,
        "expired_count": 0,
        "historical_period_anomalies": 0,
        "pages": 0,
        "list_requests": 0,
        "sentinel_requests": 0,
        "sentinel_kinds": {},
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "suppressed_nonproduction_rows": 0,
        "duplicate_source_rows": 0,
        "semantic_duplicate_rows": 0,
        "returned_count": 0,
        "status_counts": {},
        "branch_counts": {},
        "reservation_available_count": 0,
        "online_application_count": 0,
        "reconciled_status_rows": 0,
        "closed_without_application_period_count": 0,
        "network_requests": 0,
        "retry_count": 0,
        "worker_sessions": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "aggregate_union_complete": False,
        "aggregate_total_and_boundary_complete": False,
        "details_complete": False,
        "stable_recheck_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": error,
        "errors": [error] if error else [],
        "no_current_data": False,
        "no_current_reason": "",
        "parser": GYEYANG_PARSER,
        "candidate_id": GYEYANG_CANDIDATE_ID,
        "canonical_url": GYEYANG_URL,
        "ownership_scope": GYEYANG_OWNERSHIP_SCOPE,
        "excluded_scope": GYEYANG_EXCLUDED_SCOPE,
        "pii_policy": (
            "public_structured_allowlist; registration_forms/applicants/attachments/"
            "images/instructors/contacts/free_text_not_followed_or_stored"
        ),
    }


def collect_incheon_gyeyang_education(
    target: Any,
    timeout: int = 25,
    max_pages: int = 200,
    detail_limit: int = 200,
    *,
    today: Optional[date | datetime | str] = None,
    max_requests: int = 520,
    max_workers: int = GYEYANG_MAX_WORKERS,
    fetch_attempts: int = GYEYANG_FETCH_ATTEMPTS,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current Gyeyang education snapshot or fail closed."""

    if not is_incheon_gyeyang_education_target(target):
        return [], GYEYANG_PARSER, _failed_meta(
            "target does not match the canonical Gyeyang education owner"
        )
    integer_limits = (timeout, max_pages, max_requests, max_workers, fetch_attempts)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in integer_limits
    ) or isinstance(detail_limit, bool) or not isinstance(detail_limit, int) or detail_limit < 0:
        return [], GYEYANG_PARSER, _failed_meta("invalid collection limits")

    factory = session_factory or _default_session_factory
    request = fetcher or _default_fetcher
    budget = _RequestBudget(max_requests)
    list_session: Any = None
    snapshots: dict[str, _CatalogueSnapshot] = {}
    listed: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    historical_period_anomalies = 0
    retries = 0
    worker_sessions = 0
    detail_pages = 0
    suppressed = 0
    semantic_duplicates = 0
    aggregate_union_complete = False
    aggregate_total = 0
    aggregate_pages = 0
    aggregate_list_requests = 0
    aggregate_stability_rechecks = 0

    try:
        reference_day = _today(today)
        list_session = factory()
        if list_session is None:
            raise GyeyangContractError("session factory returned no list session")
        for catalogue in GYEYANG_CATALOGUES:
            snapshot, retry_count = _scan_catalogue(
                target,
                catalogue,
                current_session=list_session,
                fetcher=request,
                timeout=timeout,
                attempts=fetch_attempts,
                sleeper=sleeper,
                budget=budget,
                max_pages=max_pages,
            )
            snapshots[catalogue.key] = snapshot
            retries += retry_count
            listed.extend(snapshot.rows)

        identities = [_clean(row["_identity"]) for row in listed]
        duplicate_source_rows = len(identities) - len(set(identities))
        if duplicate_source_rows:
            raise GyeyangContractError("programme identity appears in multiple owner catalogues")
        union_by_id = {_clean(row["_identity"]): row for row in listed}

        # The aggregate has 243 pages in the audited snapshot, while every
        # complete owner partition remains below the public max-pages ceiling.
        # Exhaust the three disjoint owner partitions above; then independently
        # bind the aggregate's declared total and stable newest-page boundary.
        aggregate_soup, retry_count = _fetch_soup(
            list_session,
            gyeyang_list_url(GYEYANG_AGGREGATE, 1),
            fetcher=request,
            timeout=timeout,
            attempts=fetch_attempts,
            sleeper=sleeper,
            budget=budget,
        )
        retries += retry_count
        aggregate_list_requests += 1
        aggregate_rows, aggregate_total, _, aggregate_pages = _parse_page(
            target,
            aggregate_soup,
            GYEYANG_AGGREGATE,
            requested_page=1,
            expected_total=len(listed),
        )
        aggregate_boundary_signature = _source_signature(aggregate_rows)
        if len({_clean(row["_identity"]) for row in aggregate_rows}) != len(
            aggregate_rows
        ):
            raise GyeyangContractError("duplicate identity on aggregate boundary")
        for aggregate in aggregate_rows:
            identity = _clean(aggregate["_identity"])
            owned = union_by_id.get(identity)
            if owned is None:
                raise GyeyangContractError(
                    "aggregate boundary identity is absent from owner partitions"
                )
            owned_signature = (
                _clean(owned["title"]),
                _clean(owned["_list_status"]),
                _clean(owned["branch"]),
                _clean(owned["period"]),
                _clean(owned["apply_period"]),
            )
            aggregate_row_signature = (
                _clean(aggregate["title"]),
                _clean(aggregate["_list_status"]),
                _clean(aggregate["branch"]),
                _clean(aggregate["period"]),
                _clean(aggregate["apply_period"]),
            )
            if owned_signature != aggregate_row_signature:
                raise GyeyangContractError(
                    f"aggregate row {identity} differs from its owner partition"
                )
        aggregate_recheck, retry_count = _fetch_soup(
            list_session,
            gyeyang_list_url(GYEYANG_AGGREGATE, 1),
            fetcher=request,
            timeout=timeout,
            attempts=fetch_attempts,
            sleeper=sleeper,
            budget=budget,
        )
        retries += retry_count
        aggregate_list_requests += 1
        aggregate_stability_rechecks += 1
        rechecked_rows, _, _, _ = _parse_page(
            target,
            aggregate_recheck,
            GYEYANG_AGGREGATE,
            requested_page=1,
            expected_total=aggregate_total,
        )
        if _source_signature(rechecked_rows) != aggregate_boundary_signature:
            raise GyeyangContractError(
                "aggregate page 1 changed during stable recheck"
            )
        aggregate_union_complete = True

        historical_period_anomalies = sum(
            bool(row.get("_historical_period_anomaly")) for row in listed
        )
        for row in listed:
            if row.get("_historical_period_anomaly") and date.fromisoformat(
                row["end_date"]
            ) >= reference_day:
                raise GyeyangContractError(
                    "audited historical period anomaly entered current scope"
                )
        current = [
            row
            for row in listed
            if date.fromisoformat(row["end_date"]) >= reference_day
        ]
        if len(current) > detail_limit:
            raise GyeyangContractError(
                f"current detail count {len(current)} exceeds detail_limit {detail_limit}"
            )

        details = _fetch_many_details(
            current,
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
        detail_pages = len(details.values)
        if details.errors or len(details.values) != len(current):
            raise GyeyangContractError(
                "detail snapshot incomplete: " + "; ".join(details.errors)
            )

        parsed_rows: list[dict[str, Any]] = []
        for listed_row in current:
            key = (_clean(listed_row["_division"]), _clean(listed_row["_identity"]))
            parsed = _parse_detail(details.values[key], listed_row)
            if _practice_row_matches(parsed):
                suppressed += 1
                continue
            if _is_nonproduction_title(parsed.get("title")):
                raise GyeyangContractError(
                    "unapproved nonproduction course entered the current ledger"
                )
            parsed_rows.append(parsed)

        semantic_keys = [_semantic_key(row) for row in parsed_rows]
        semantic_duplicates = len(semantic_keys) - len(set(semantic_keys))
        if semantic_duplicates:
            raise GyeyangContractError("semantic duplicate course rows detected")
        if dedupe_rows is not None:
            deduped = list(dedupe_rows(parsed_rows))
            if len(deduped) != len(parsed_rows):
                raise GyeyangContractError("downstream dedupe changed owned snapshot")
            parsed_rows = deduped

        serialized = repr(parsed_rows)
        if _EMAIL_RE.search(serialized) or _PHONE_RE.search(serialized):
            raise GyeyangContractError("contact data escaped the public allowlist")
        forbidden_keys = {"phone", "instructor", "description", "attachments"}
        if any(forbidden_keys & set(row) for row in parsed_rows):
            raise GyeyangContractError("forbidden detail field escaped the allowlist")

        rows_by_catalogue = {
            item.key: snapshots[item.key].total for item in GYEYANG_CATALOGUES
        }
        pages_by_catalogue = {
            item.key: snapshots[item.key].pages for item in GYEYANG_CATALOGUES
        }
        sentinel_kinds = {
            item.key: snapshots[item.key].sentinel_kind
            for item in GYEYANG_CATALOGUES
        }
        status_counts = dict(Counter(row["status"] for row in parsed_rows))
        branch_counts = dict(Counter(row["branch"] for row in parsed_rows))
        meta = {
            **_failed_meta(),
            "source_total": len(listed),
            "source_rows": len(listed),
            "source_rows_by_catalogue": rows_by_catalogue,
            "aggregate_total": aggregate_total,
            "data_pages_by_catalogue": pages_by_catalogue,
            "aggregate_pages": aggregate_pages,
            "current_count": len(current),
            "expired_count": len(listed) - len(current),
            "historical_period_anomalies": historical_period_anomalies,
            "pages": sum(pages_by_catalogue.values()),
            "list_requests": sum(snapshot.list_requests for snapshot in snapshots.values())
            + aggregate_list_requests,
            "sentinel_requests": len(GYEYANG_CATALOGUES),
            "sentinel_kinds": sentinel_kinds,
            "stability_rechecks": sum(
                snapshot.stability_rechecks for snapshot in snapshots.values()
            )
            + aggregate_stability_rechecks,
            "detail_attempts": len(current),
            "detail_pages": detail_pages,
            "suppressed_nonproduction_rows": suppressed,
            "duplicate_source_rows": duplicate_source_rows,
            "semantic_duplicate_rows": semantic_duplicates,
            "returned_count": len(parsed_rows),
            "status_counts": status_counts,
            "branch_counts": branch_counts,
            "reservation_available_count": sum(
                bool(row.get("reservation_available")) for row in parsed_rows
            ),
            "online_application_count": sum(
                bool(row.get("application_url")) for row in parsed_rows
            ),
            "reconciled_status_rows": sum(
                bool((row.get("raw_fields") or {}).get("status_reconciled"))
                for row in parsed_rows
            ),
            "closed_without_application_period_count": sum(
                row.get("status") == "CLOSED" and not row.get("apply_period")
                for row in parsed_rows
            ),
            "network_requests": budget.count,
            "retry_count": retries,
            "worker_sessions": worker_sessions,
            "pagination_detected": any(value > 1 for value in pages_by_catalogue.values()),
            "pagination_complete": True,
            "aggregate_union_complete": True,
            "aggregate_total_and_boundary_complete": True,
            "details_complete": True,
            "stable_recheck_complete": True,
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "configured_collection_error": "",
            "errors": [],
            "no_current_data": not parsed_rows,
            "no_current_reason": (
                "all three complete official catalogues contain no current production courses"
                if not parsed_rows
                else ""
            ),
        }
        return parsed_rows, GYEYANG_PARSER, meta
    except Exception as exc:
        error = f"{type(exc).__name__}: {_clean(exc)}"
        rows_by_catalogue = {
            item.key: snapshots[item.key].total
            for item in GYEYANG_CATALOGUES
            if item.key in snapshots
        }
        pages_by_catalogue = {
            item.key: snapshots[item.key].pages
            for item in GYEYANG_CATALOGUES
            if item.key in snapshots
        }
        meta = {
            **_failed_meta(error),
            "source_total": len(listed),
            "source_rows": len(listed),
            "source_rows_by_catalogue": rows_by_catalogue,
            "aggregate_total": aggregate_total,
            "data_pages_by_catalogue": pages_by_catalogue,
            "aggregate_pages": aggregate_pages,
            "current_count": len(current),
            "expired_count": len(listed) - len(current),
            "historical_period_anomalies": historical_period_anomalies,
            "pages": sum(pages_by_catalogue.values()),
            "list_requests": sum(snapshot.list_requests for snapshot in snapshots.values())
            + aggregate_list_requests,
            "sentinel_requests": len(snapshots),
            "sentinel_kinds": {
                key: value.sentinel_kind for key, value in snapshots.items()
            },
            "stability_rechecks": sum(
                snapshot.stability_rechecks for snapshot in snapshots.values()
            )
            + aggregate_stability_rechecks,
            "detail_pages": detail_pages,
            "suppressed_nonproduction_rows": suppressed,
            "semantic_duplicate_rows": semantic_duplicates,
            "aggregate_union_complete": aggregate_union_complete,
            "network_requests": budget.count,
            "retry_count": retries,
            "worker_sessions": worker_sessions,
            "source_cap_reached": any(
                marker in error
                for marker in ("max_pages", "max_requests", "detail_limit", "cap", "exceeded")
            ),
        }
        return [], GYEYANG_PARSER, meta
    finally:
        _close_quietly(list_session)


collect = collect_incheon_gyeyang_education


__all__ = [
    "GYEYANG_PROVIDER",
    "GYEYANG_CANDIDATE_ID",
    "GYEYANG_LEGACY_HOME_CANDIDATE_ID",
    "GYEYANG_MUNICIPALITY_CODE",
    "GYEYANG_MUNICIPALITY_NAME",
    "GYEYANG_HOST",
    "GYEYANG_URL",
    "GYEYANG_PARSER",
    "GYEYANG_OWNERSHIP_SCOPE",
    "GYEYANG_CATALOGUES",
    "GYEYANG_RESIDENT_CENTRES",
    "GYEYANG_CANDIDATE_AUDIT",
    "GYEYANG_EXCLUDED_SCOPE",
    "GYEYANG_DISCOVERY_AUDIT",
    "GyeyangCatalogue",
    "GyeyangContractError",
    "is_incheon_gyeyang_education_target",
    "is_target",
    "gyeyang_list_url",
    "gyeyang_detail_url",
    "collect_incheon_gyeyang_education",
    "collect",
]
