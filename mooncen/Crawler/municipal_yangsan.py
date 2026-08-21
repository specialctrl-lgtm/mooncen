"""Fail-closed collectors for Yangsan City's three education ledgers.

Two incumbent providers are preserved.  The lifelong provider owns two
disjoint partitions (``eduType=1`` learning-centre courses and ``eduType=6``
happy-learning-centre courses).  The booking provider owns the separate city
integrated-reservation lecture archive.  The booking ``main.do`` target is a
legacy navigation shell accepted only so central configuration can be safely
retargeted to the canonical lecture list.

Every advertised data page, the exact immediate post-last sentinel, and
stable first/final/sentinel rechecks are required.  Only current/future
details are fetched.  Application, receipt, login, file, attachment and other
PII-bearing endpoints are validated as inert controls where exposed but are
never requested.  Instructor/contact/free-text/attachment values are never
persisted.  Any incomplete or changed contract returns no rows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YANGSAN_LIFELONG_PROVIDER = "MUNI_WWW_YANGSAN_GO_KR_059D4DD1"
YANGSAN_BOOKING_PROVIDER = "MUNI_WWW_YANGSAN_GO_KR_DBBB1885"
YANGSAN_LIFELONG_CANDIDATE_ID = "MUNI_IR_D922439A72A1"
YANGSAN_BOOKING_LEGACY_CANDIDATE_ID = "MUNI_IR_CA25868761ED"
YANGSAN_BOOKING_CANONICAL_CANDIDATE_ID = "MUNI_IR_FC2DE5C5D225"
YANGSAN_MUNICIPALITY_CODE = "4833000000"
YANGSAN_MUNICIPALITY_NAME = "경상남도 양산시"

YANGSAN_HOST = "www.yangsan.go.kr"
YANGSAN_MID = "0301000000"
YANGSAN_LIFELONG_LIST_PATH = "/edu/forever/lecture/search.do"
YANGSAN_FOREVER_DETAIL_PATH = "/edu/forever/lecture/totalDetail.do"
YANGSAN_HAPPINESS_DETAIL_PATH = "/edu/happinessLearning/totalDetail.do"
YANGSAN_BOOKING_LIST_PATH = "/booking/lecture/list.do"
YANGSAN_BOOKING_DETAIL_PATH = "/booking/lecture/view.do"
YANGSAN_BOOKING_LEGACY_PATH = "/booking/main.do"
YANGSAN_BOOKING_APPLICATION_PATH = "/booking/lecture/app/write.do"
YANGSAN_LIFELONG_CANONICAL_URL = f"https://{YANGSAN_HOST}{YANGSAN_LIFELONG_LIST_PATH}?mid={YANGSAN_MID}"
YANGSAN_BOOKING_CANONICAL_URL = f"https://{YANGSAN_HOST}{YANGSAN_BOOKING_LIST_PATH}?mid={YANGSAN_MID}"
YANGSAN_BOOKING_LEGACY_URL = f"https://{YANGSAN_HOST}{YANGSAN_BOOKING_LEGACY_PATH}"

YANGSAN_LIFELONG_PAGE_SIZE = 12
YANGSAN_BOOKING_PAGE_SIZE = 10
YANGSAN_MAX_HTML_BYTES = 4_000_000
YANGSAN_MAX_PAGES = 250
YANGSAN_MAX_DETAILS = 200
YANGSAN_AUDITED_SOURCE_TOTALS = {
    "forever_1": 200,
    "forever_6": 70,
    "booking": 576,
    "all": 846,
}

YANGSAN_LIFELONG_PARSER = (
    "yangsan_lifelong_incumbent+eduType1_and_6_disjoint+advertised_all_pages+"
    "exact_no_data_sentinels+stable_first_final_sentinel+current_post_details+"
    "type1_fixed_branch+type6_detail_center+no_private_endpoints+pii_allowlist"
)
YANGSAN_BOOKING_PARSER = (
    "yangsan_booking_incumbent+legacy_main_retarget+official_org_registry+"
    "advertised_all_archive+exact_empty_sentinel+stable_first_final_sentinel+"
    "current_future_status01_06_details+exact_malformed_period_exclusions+"
    "source_state_bound_application_controls+no_private_endpoints+pii_allowlist"
)


@dataclass(frozen=True)
class LifelongPartition:
    edu_type: str
    key: str
    label: str
    detail_path: str


YANGSAN_LIFELONG_PARTITIONS = (
    LifelongPartition("1", "forever_1", "학습관교육", YANGSAN_FOREVER_DETAIL_PATH),
    LifelongPartition("6", "forever_6", "행복학습센터", YANGSAN_HAPPINESS_DETAIL_PATH),
)
_LIFELONG_BY_TYPE = {item.edu_type: item for item in YANGSAN_LIFELONG_PARTITIONS}

YANGSAN_BOOKING_ORG_REGISTRY: tuple[tuple[str, str], ...] = (
    ("", "기관(전체)"),
    ("79", "증산다누리터"),
    ("77", "양산시농업기술센터"),
    ("75", "동면행정복지센터"),
    ("74", "물금읍행정복지센터"),
    ("73", "상북면행정복지센터"),
    ("69", "양산시농업기술센터"),
    ("68", "양주동행정복지센터"),
    ("65", "건강증진과"),
    ("64", "시민정보화교육"),
    ("58", "양산시 반려동물지원센터(교육)"),
    ("54", "양산시립독립기념관"),
    ("50", "시립박물관"),
    ("49", "소주동행정복지센터"),
    ("45", "여성복지센터"),
    ("37", "교육기관(모두)"),
    ("36", "교육기관(비대면)"),
    ("35", "교육기관(결제)"),
    ("34", "교육기관(없음)"),
)
_BOOKING_REGISTRY_LABELS = frozenset(label for _code, label in YANGSAN_BOOKING_ORG_REGISTRY)
YANGSAN_BOOKING_BRANCH_NORMALIZATION = {
    "양산시 반려동물지원센터(교육)": "반려동물지원센터(교육)",
    "양산시농업기술센터": "양산시농업기술센터",
}
_BOOKING_CURRENT_BRANCHES = frozenset(
    {
        "반려동물지원센터(교육)",
        "증산다누리터",
        "물금읍행정복지센터",
        "동면행정복지센터",
        "양주동행정복지센터",
        "양산시농업기술센터",
    }
)

YANGSAN_BOOKING_STATE_REGISTRY: tuple[tuple[str, str], ...] = (
    ("", "상태(전체)"),
    ("01", "접수전"),
    ("02", "접수중"),
    ("03", "대기자 접수 중"),
    ("04", "정원마감"),
    ("05", "접수마감"),
    ("06", "교육중"),
    ("07", "교육완료"),
)
_BOOKING_STATE_CODE = {label: code for code, label in YANGSAN_BOOKING_STATE_REGISTRY if code}
_BOOKING_STATUS_MAP = {
    "01": "SCHEDULED",
    "02": "OPEN",
    "03": "OPEN",
    "04": "CLOSED",
    "05": "CLOSED",
    "06": "CLOSED",
    "07": "ENDED",
}
_BOOKING_CURRENT_CODES = frozenset({"01", "02", "03", "04", "05", "06"})

# These two current rows are malformed in both the official list and detail.
# Preserve the exact source text so a correction starts collecting
# automatically while any different malformed value still fails closed.
_AUDITED_BOOKING_REVERSED_PERIODS = {
    ("752", "education period"): "2026.09.07 ~ 2026.08.31",
    ("754", "application period"): "2026.08.18 ~ 2026.05.15 09:00 ~ 15:00",
}

YANGSAN_BOOKING_TYPE_REGISTRY: tuple[tuple[str, str], ...] = (
    ("", "분류(전체)"),
    ("01", "어린이"),
    ("02", "청소년"),
    ("03", "성인"),
    ("04", "노인"),
    ("05", "가족(가정)"),
    ("06", "행사"),
    ("07", "교육"),
    ("08", "교양"),
    ("09", "역사"),
    ("10", "언어"),
    ("11", "IT"),
    ("12", "음악"),
    ("13", "미술"),
    ("14", "체육"),
    ("15", "문학"),
    ("16", "취미"),
    ("17", "사회"),
    ("18", "농업"),
)

YANGSAN_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "lifelong_type1": {
        "provider": YANGSAN_LIFELONG_PROVIDER,
        "candidate_id": YANGSAN_LIFELONG_CANDIDATE_ID,
        "url": YANGSAN_LIFELONG_CANONICAL_URL,
        "decision": "retain_incumbent_owner_partition_eduType1",
    },
    "lifelong_type6": {
        "provider": YANGSAN_LIFELONG_PROVIDER,
        "candidate_id": YANGSAN_LIFELONG_CANDIDATE_ID,
        "url": YANGSAN_LIFELONG_CANONICAL_URL,
        "decision": "retain_incumbent_owner_partition_eduType6",
    },
    "booking_legacy_home": {
        "provider": YANGSAN_BOOKING_PROVIDER,
        "candidate_id": YANGSAN_BOOKING_LEGACY_CANDIDATE_ID,
        "url": YANGSAN_BOOKING_LEGACY_URL,
        "decision": "accept_legacy_match_then_retarget_to_canonical_list",
    },
    "booking_canonical": {
        "provider": YANGSAN_BOOKING_PROVIDER,
        "candidate_id": YANGSAN_BOOKING_CANONICAL_CANDIDATE_ID,
        "url": YANGSAN_BOOKING_CANONICAL_URL,
        "decision": "retain_incumbent_owner_on_complete_booking_lecture_ledger",
    },
    "three_way_overlap": {
        "decision": "namespaced_source_identities_must_be_pairwise_disjoint",
        "audited_total": YANGSAN_AUDITED_SOURCE_TOTALS["all"],
    },
}

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d{0,11}$")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[./-](\d{2})[./-](\d{2})(?!\d)")
_LIFELONG_ONCLICK_RE = re.compile(
    r"^fn_popup_open_totalLecture\(\s*([1-9]\d{0,11})\s*,\s*([16])\s*,\s*['\"]Y['\"]\s*\);?$"
)
_INTEGER_RE = re.compile(r"\d[\d,]*")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIFELONG_STATUS_MAP = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
}
_LIFELONG_TYPE1_REQUIRED = frozenset(
    {
        "교육분야",
        "교육구분",
        "교육시간",
        "교육대상",
        "1차접수기간",
        "2차접수기간",
        "수강료",
        "재료비(기타비용)",
        "모집형태",
        "정원/신청/확정",
        "교육장소",
        "문의처",
        "교육내용",
        "강의계획서",
    }
)
_LIFELONG_TYPE6_REQUIRED = frozenset(
    {
        "교육분야",
        "강사명",
        "교육내용",
        "프로그램 기간",
        "접수 기간",
        "교육대상",
        "연령제한",
        "수강정원",
        "강좌횟수/시수",
        "수강료",
        "재료비",
        "강의장소",
        "센터명",
        "문의처",
        "강의계획서",
    }
)
_BOOKING_DETAIL_REQUIRED = frozenset(
    {
        "강좌명",
        "강사명",
        "접수기간",
        "교육기간",
        "교육시간",
        "총수강료",
        "재료비",
        "교육장소",
        "교육대상",
        "문의처",
        "모집인원",
        "모집현황",
        "강좌설명",
        "결제방법",
        "안내계좌",
    }
)
_BOOKING_DETAIL_OPTIONAL = frozenset({"결제기간", "첨부파일", "비대면감면대상", "감면대상", "환불규정", "주의사항"})

Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class YangsanContractError(RuntimeError):
    """Raised when an audited public Yangsan source contract changes."""


@dataclass(frozen=True)
class _Page:
    source: str
    number: int
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _schedule_matches(list_value: Any, detail_value: Any) -> bool:
    """Compare weekday/time while allowing the detail's date-first layout."""
    listed = _clean(list_value)
    detail = _clean(detail_value)
    listed_times = re.findall(r"(?<!\d)([0-2]?\d:[0-5]\d)(?!\d)", listed)
    detail_times = re.findall(r"(?<!\d)([0-2]?\d:[0-5]\d)(?!\d)", detail)
    listed_days = set(re.findall(r"[월화수목금토일]", listed))
    detail_days = set(re.findall(r"[월화수목금토일]", detail))
    return bool(
        len(listed_times) == 2
        and detail_times[:1] == listed_times[:1]
        and detail_times[-1:] == listed_times[-1:]
        and listed_days
        and listed_days <= detail_days
    )


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _strict_get_url(value: Any, path: str, query: list[tuple[str, str]]) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
        actual_query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == YANGSAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == path
        and actual_query == query
        and not parsed.fragment
    )


def is_yangsan_lifelong_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == YANGSAN_LIFELONG_PROVIDER
        and _strict_get_url(
            _target_value(target, "url"),
            YANGSAN_LIFELONG_LIST_PATH,
            [("mid", YANGSAN_MID)],
        )
    )


def is_yangsan_booking_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != YANGSAN_BOOKING_PROVIDER:
        return False
    value = _target_value(target, "url")
    return _strict_get_url(value, YANGSAN_BOOKING_LIST_PATH, [("mid", YANGSAN_MID)]) or _strict_get_url(
        value, YANGSAN_BOOKING_LEGACY_PATH, []
    )


def is_yangsan_education_target(target: Any) -> bool:
    return is_yangsan_lifelong_target(target) or is_yangsan_booking_target(target)


is_target = is_yangsan_education_target


def yangsan_lifelong_list_url(edu_type: str, page: int) -> str:
    if edu_type not in _LIFELONG_BY_TYPE:
        raise ValueError("edu_type must be 1 or 6")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    query = [("mid", YANGSAN_MID), ("eduType", edu_type), ("page", str(page))]
    return f"https://{YANGSAN_HOST}{YANGSAN_LIFELONG_LIST_PATH}?{urlencode(query)}"


def yangsan_lifelong_detail_url(edu_type: str) -> str:
    partition = _LIFELONG_BY_TYPE.get(edu_type)
    if partition is None:
        raise ValueError("edu_type must be 1 or 6")
    return f"https://{YANGSAN_HOST}{partition.detail_path}?{urlencode({'mid': YANGSAN_MID})}"


def yangsan_booking_list_url(page: int) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    query = [("mid", YANGSAN_MID), ("page", str(page))]
    return f"https://{YANGSAN_HOST}{YANGSAN_BOOKING_LIST_PATH}?{urlencode(query)}"


def yangsan_booking_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise ValueError("invalid Yangsan booking identity")
    query = [("mid", YANGSAN_MID), ("idx", value)]
    return f"https://{YANGSAN_HOST}{YANGSAN_BOOKING_DETAIL_PATH}?{urlencode(query)}"


def yangsan_source_identity(provider: str, partition: str, identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise ValueError("invalid Yangsan source identity")
    if (provider, partition) not in {
        (YANGSAN_LIFELONG_PROVIDER, "forever_1"),
        (YANGSAN_LIFELONG_PROVIDER, "forever_6"),
        (YANGSAN_BOOKING_PROVIDER, "booking"),
    }:
        raise ValueError("invalid Yangsan owner partition")
    return f"{provider}:{partition}:{value}"


def yangsan_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
    )
    return session


def _default_fetcher(
    session: Any,
    method: str,
    url: str,
    *,
    timeout: int,
    data: Mapping[str, str],
) -> Any:
    if method == "GET":
        return session.get(url, timeout=timeout, allow_redirects=False)
    if method == "POST":
        return session.post(url, data=dict(data), timeout=timeout, allow_redirects=False)
    raise YangsanContractError("unsupported request method")


def _allowed_request(method: str, url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == YANGSAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        return False
    routes = {
        ("GET", YANGSAN_LIFELONG_LIST_PATH),
        ("POST", YANGSAN_FOREVER_DETAIL_PATH),
        ("POST", YANGSAN_HAPPINESS_DETAIL_PATH),
        ("GET", YANGSAN_BOOKING_LIST_PATH),
        ("GET", YANGSAN_BOOKING_DETAIL_PATH),
    }
    if (method, parsed.path) not in routes:
        return False
    if parsed.path in {YANGSAN_FOREVER_DETAIL_PATH, YANGSAN_HAPPINESS_DETAIL_PATH}:
        return query == [("mid", YANGSAN_MID)]
    return bool(dict(query).get("mid") == YANGSAN_MID)


def _coerce_soup(value: Any, requested_url: str) -> BeautifulSoup:
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise YangsanContractError(f"unexpected HTTP status {status}")
    if getattr(value, "history", None):
        raise YangsanContractError("redirect history is not accepted")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location") or headers.get("location"):
        raise YangsanContractError("redirect response is not accepted")
    content_type = _clean(headers.get("Content-Type") or headers.get("content-type"))
    if content_type and "html" not in content_type.lower():
        raise YangsanContractError("official response is not HTML")
    final_url = _clean(getattr(value, "url", requested_url) or requested_url)
    expected = urlparse(requested_url)
    actual = urlparse(final_url)
    try:
        expected_query = parse_qsl(expected.query, keep_blank_values=True, strict_parsing=True)
        actual_query = parse_qsl(actual.query, keep_blank_values=True, strict_parsing=True)
        expected_port = expected.port
        actual_port = actual.port
    except (TypeError, ValueError) as exc:
        raise YangsanContractError("malformed official response URL") from exc
    if not (
        actual.scheme == expected.scheme == "https"
        and (actual.hostname or "").lower() == YANGSAN_HOST
        and (expected.hostname or "").lower() == YANGSAN_HOST
        and actual_port is expected_port is None
        and actual.username is None
        and actual.password is None
        and actual.path == expected.path
        and actual_query == expected_query
        and not actual.fragment
    ):
        raise YangsanContractError("official response URL changed")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if not content:
        raise YangsanContractError("empty official response")
    if len(content) > YANGSAN_MAX_HTML_BYTES:
        raise YangsanContractError("HTML size cap exceeded")
    return BeautifulSoup(content, "html.parser")


def _dates(value: Any, identity: str, field: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise YangsanContractError(f"course {identity}: {field} must contain two dates")
    start, end = (date(int(year), int(month), int(day)) for year, month, day in matches)
    if end < start:
        raise YangsanContractError(f"course {identity}: reversed {field}")
    return start, end


def _booking_dates(value: Any, identity: str, field: str) -> tuple[date, date, bool]:
    raw = _clean(value)
    matches = _DATE_RE.findall(raw)
    if len(matches) != 2:
        raise YangsanContractError(f"course {identity}: {field} must contain two dates")
    start, end = (date(int(year), int(month), int(day)) for year, month, day in matches)
    if end >= start:
        return start, end, False
    if _AUDITED_BOOKING_REVERSED_PERIODS.get((identity, field)) != raw:
        raise YangsanContractError(f"course {identity}: reversed {field}")
    return start, end, True


def _detail_fields(table: Any, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in table.select("tbody > tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index + 1 < len(cells):
            if cells[index].name != "th" or cells[index + 1].name != "td":
                raise YangsanContractError(f"course {identity}: detail cell pairing changed")
            label = _clean(cells[index].get_text(" ", strip=True))
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if not label or label in fields:
                raise YangsanContractError(f"course {identity}: conflicting detail field")
            fields[label] = value
            index += 2
        if index != len(cells):
            raise YangsanContractError(f"course {identity}: unpaired detail cell")
    return fields


def _lifelong_list_form(soup: BeautifulSoup, partition: LifelongPartition, page: int) -> None:
    forms = soup.select("form#listForm[name='listForm']")
    if len(forms) != 1 or _clean(forms[0].get("method")).upper() != "POST":
        raise YangsanContractError(f"{partition.key} page {page}: list form changed")
    form = forms[0]
    action = urlparse(urljoin(YANGSAN_LIFELONG_CANONICAL_URL, _clean(form.get("action"))))
    if action.path != YANGSAN_LIFELONG_LIST_PATH or parse_qs(action.query) != {"mid": [YANGSAN_MID]}:
        raise YangsanContractError(f"{partition.key} page {page}: list action changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[name]")
        if _clean(node.get("name")) in {"page", "currentPageNo", "eduType", "keyword"}
    }
    if hidden != {
        "page": str(page),
        # The server updates ``page`` but leaves this legacy field at one.
        "currentPageNo": "1",
        "eduType": partition.edu_type,
        "keyword": "",
    }:
        raise YangsanContractError(f"{partition.key} page {page}: list scope changed")
    select = form.select_one("select[name='recordCountPerPage']")
    options = tuple(_clean(option.get("value")) for option in (select.select("option") if select else ()))
    if options != ("12", "16", "24"):
        raise YangsanContractError(f"{partition.key} page {page}: page-size registry changed")


def _lifelong_card(card: Any, partition: LifelongPartition, page: int) -> dict[str, Any]:
    anchors = card.select(":scope > a[onclick]")
    if len(anchors) != 1:
        raise YangsanContractError(f"{partition.key} page {page}: card control changed")
    anchor = anchors[0]
    match = _LIFELONG_ONCLICK_RE.fullmatch(_clean(anchor.get("onclick")))
    if match is None or match.group(2) != partition.edu_type:
        raise YangsanContractError(f"{partition.key} page {page}: detail identity/type drift")
    identity = match.group(1)
    if _clean(anchor.get("href")) != "javascript:void(0);" or _clean(anchor.get("data-target")) != "layerpopup_mycode":
        raise YangsanContractError(f"course {identity}: popup control changed")
    title_nodes = anchor.select(":scope > p.subj")
    badge_nodes = anchor.select(":scope > em.badge")
    state_nodes = anchor.select(":scope > span.state")
    info_nodes = anchor.select(":scope > ul.info")
    if not all(len(nodes) == 1 for nodes in (title_nodes, badge_nodes, state_nodes, info_nodes)):
        raise YangsanContractError(f"course {identity}: lifelong card structure changed")
    title = _clean(title_nodes[0].get_text(" ", strip=True))
    if _clean(badge_nodes[0].get_text(" ", strip=True)) != partition.label:
        raise YangsanContractError(f"course {identity}: source partition label changed")
    source_status = _clean(state_nodes[0].get_text(" ", strip=True))
    status = _LIFELONG_STATUS_MAP.get(source_status)
    if not title or status is None:
        raise YangsanContractError(f"course {identity}: title/status changed")
    fields: dict[str, str] = {}
    for node in info_nodes[0].select(":scope > li"):
        label_node = node.select_one(":scope > strong")
        value_node = node.select_one(":scope > span")
        label = _clean(label_node.get_text(" ", strip=True) if label_node else "")
        value = _clean(value_node.get_text(" ", strip=True) if value_node else "")
        if label not in {"교육장소", "교육기간", "교육시간"} or not value or label in fields:
            raise YangsanContractError(f"course {identity}: lifelong list fields changed")
        fields[label] = value
    if set(fields) != {"교육장소", "교육기간", "교육시간"}:
        raise YangsanContractError(f"course {identity}: lifelong list fields missing")
    start, end = _dates(fields["교육기간"], identity, "education period")
    return {
        "provider": YANGSAN_LIFELONG_PROVIDER,
        "partition": partition.key,
        "edu_type": partition.edu_type,
        "identity": identity,
        "source_identity": yangsan_source_identity(YANGSAN_LIFELONG_PROVIDER, partition.key, identity),
        "page": page,
        "title": title,
        "venue": fields["교육장소"],
        "event_start": start,
        "event_end": end,
        "schedule": fields["교육시간"],
        "source_status": source_status,
        "status": status,
    }


def _parse_lifelong_page(soup: BeautifulSoup, partition: LifelongPartition, page: int) -> _Page:
    _lifelong_list_form(soup, partition, page)
    head = soup.select_one("form#listForm .bod_head")
    head_text = _clean(head.get_text(" ", strip=True) if head else "")
    total_match = re.search(r"전체\s*([\d,]+)\s*건", head_text)
    if total_match is None:
        raise YangsanContractError(f"{partition.key} page {page}: advertised total missing")
    total = int(total_match.group(1).replace(",", ""))
    if total < 1:
        raise YangsanContractError(f"{partition.key}: source unexpectedly empty")
    last = math.ceil(total / YANGSAN_LIFELONG_PAGE_SIZE)
    containers = soup.select("form#listForm div.bod_cardList > ul.clFix")
    if len(containers) != 1:
        raise YangsanContractError(f"{partition.key} page {page}: card list changed")
    cards = containers[0].find_all("li", recursive=False)
    owned = [card for card in cards if card.select_one('a[onclick*="fn_popup_open_totalLecture"]')]
    rows = tuple(_lifelong_card(card, partition, page) for card in owned)
    expected = min(YANGSAN_LIFELONG_PAGE_SIZE, total - (page - 1) * YANGSAN_LIFELONG_PAGE_SIZE) if page <= last else 0
    if len(rows) != expected:
        raise YangsanContractError(f"{partition.key} page {page}: expected {expected} owned cards, found {len(rows)}")
    if page <= last and len(cards) != len(rows):
        raise YangsanContractError(f"{partition.key} page {page}: foreign structural card")
    if page > last:
        if len(cards) != 1 or _clean(cards[0].get("class")) != "['no-data']":
            # BeautifulSoup stringifies a list for ``class``; use membership as
            # the authoritative check below and keep this branch readable.
            if len(cards) != 1 or "no-data" not in (cards[0].get("class") or []):
                raise YangsanContractError(f"{partition.key}: sentinel structure changed")
        if _clean(cards[0].get_text(" ", strip=True)) != "등록된 데이터가 없습니다.":
            raise YangsanContractError(f"{partition.key}: sentinel text changed")
    identities = [str(row["identity"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise YangsanContractError(f"{partition.key} page {page}: duplicate identities")
    return _Page(partition.key, page, total, last, rows)


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.source,
        page.total,
        page.last,
        tuple(
            (
                row["source_identity"],
                row["title"],
                row["event_start"],
                row["event_end"],
                row["source_status"],
                row.get("branch_source", ""),
            )
            for row in page.rows
        ),
    )


def _lifelong_output_row(listed: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], dict[str, int]]:
    identity = str(listed["identity"])
    edu_type = str(listed["edu_type"])
    title_nodes = soup.select(".pop-tit > h3")
    state_nodes = soup.select(".pop-tit [data-state]")
    tables = soup.select("table.tbl.detail")
    hidden_idx = soup.select("input[name='idx']")
    hidden_type = soup.select("input[name='selectedEduType']")
    if len(title_nodes) != 1 or len(state_nodes) != 1 or len(tables) != 1:
        raise YangsanContractError(f"course {identity}: lifelong detail structure changed")
    if hidden_idx and _clean(hidden_idx[0].get("value")) != identity:
        raise YangsanContractError(f"course {identity}: detail hidden identity changed")
    if hidden_type and _clean(hidden_type[0].get("value")) != edu_type:
        raise YangsanContractError(f"course {identity}: detail hidden type changed")
    title = _clean(title_nodes[0].get_text(" ", strip=True))
    detail_status = _clean(state_nodes[0].get("data-state"))
    if title != listed["title"] or detail_status != listed["source_status"]:
        raise YangsanContractError(f"course {identity}: list/detail title/status drift")
    fields = _detail_fields(tables[0], identity)
    required = _LIFELONG_TYPE1_REQUIRED if edu_type == "1" else _LIFELONG_TYPE6_REQUIRED
    if set(fields) != required:
        missing = sorted(required - set(fields))
        extra = sorted(set(fields) - required)
        raise YangsanContractError(f"course {identity}: lifelong detail fields changed missing={missing} extra={extra}")
    period_field = "교육시간" if edu_type == "1" else "프로그램 기간"
    detail_start, detail_end = _dates(fields[period_field], identity, "detail education period")
    if (detail_start, detail_end) != (listed["event_start"], listed["event_end"]):
        raise YangsanContractError(f"course {identity}: list/detail period drift")
    venue_field = "교육장소" if edu_type == "1" else "강의장소"
    if _compact(fields[venue_field]) != _compact(listed["venue"]):
        raise YangsanContractError(f"course {identity}: list/detail venue drift")
    # List schedule includes day + time.  The detail may additionally include
    # dates; both must contain the same compact day/time tail.
    if not _schedule_matches(listed["schedule"], fields[period_field]):
        raise YangsanContractError(f"course {identity}: list/detail schedule drift")
    write_controls = soup.select("button#writeBtn[onclick]")
    if listed["status"] == "OPEN":
        if len(write_controls) != 1 or _clean(write_controls[0].get("onclick")) != "moveWrite();":
            raise YangsanContractError(f"course {identity}: open application control changed")
    elif write_controls:
        raise YangsanContractError(f"course {identity}: closed detail exposes application control")
    if edu_type == "1":
        branch = "양산시 평생학습관"
        branch_code = "YANGSAN_LIFELONG_LEARNING_CENTER"
        category = fields["교육분야"]
        target = fields["교육대상"]
        fee = fields["수강료"]
        material_fee = fields["재료비(기타비용)"]
        capacity = int((_INTEGER_RE.search(fields["정원/신청/확정"]) or ["0"])[0])
        apply_parts = [fields["1차접수기간"], fields["2차접수기간"]]
        sensitive = int(bool(fields["문의처"]))
        free_text = int(bool(fields["교육내용"]))
    else:
        branch = fields["센터명"]
        if branch not in {"사송트루엘 행복학습센터", "동글이행복학습센터"}:
            raise YangsanContractError(f"course {identity}: unaudited 행복학습센터 {branch!r}")
        branch_code = (
            "YANGSAN_HAPPY_SASONG_TRUELL" if branch == "사송트루엘 행복학습센터" else "YANGSAN_HAPPY_DONGGEURI"
        )
        category = fields["교육분야"]
        target = fields["교육대상"]
        fee = fields["수강료"]
        material_fee = fields["재료비"]
        capacity = int((_INTEGER_RE.search(fields["수강정원"]) or ["0"])[0])
        apply_parts = [fields["접수 기간"]]
        sensitive = sum(bool(fields[label]) for label in ("강사명", "문의처"))
        free_text = int(bool(fields["교육내용"]))
    attachments = int(bool(fields["강의계획서"]))
    period = f"{detail_start.isoformat()} ~ {detail_end.isoformat()}"
    row = {
        "provider": YANGSAN_LIFELONG_PROVIDER,
        "provider_course_id": str(listed["source_identity"]),
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": branch_code,
        "preserve_branch": True,
        "category": category,
        "program_type": "교육",
        "raw_url": yangsan_lifelong_list_url(edu_type, int(listed["page"])),
        "application_url": "",
        "application_type": (
            "ONLINE_RESERVATION_CONTROL_NO_ENDPOINT_FETCH" if listed["status"] == "OPEN" else "INFO_ONLY"
        ),
        "reservation_available": listed["status"] == "OPEN",
        "status": str(listed["status"]),
        "raw_status": str(listed["source_status"]),
        "period": period,
        "start_date": detail_start.isoformat(),
        "end_date": detail_end.isoformat(),
        "apply_period": " / ".join(part for part in apply_parts if part),
        "schedule_raw": str(listed["schedule"]),
        "fee": fee,
        "material_fee": material_fee,
        "capacity": str(capacity),
        "capacity_total": capacity,
        "target": target,
        "venue": fields[venue_field],
        "venue_name": fields[venue_field],
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": YANGSAN_LIFELONG_PARSER,
        "municipality_code": YANGSAN_MUNICIPALITY_CODE,
        "municipality_name": YANGSAN_MUNICIPALITY_NAME,
        "municipality_full_name": YANGSAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "partition": str(listed["partition"]),
            "edu_type": edu_type,
            "source_page": int(listed["page"]),
            "source_status": str(listed["source_status"]),
            "detail_verified": True,
            "application_control_count": len(write_controls),
            "application_endpoint_not_requested": True,
            "receipt_endpoint_not_requested": True,
            "login_endpoint_not_requested": True,
            "attachment_endpoint_not_requested": True,
            "sensitive_detail_fields_discarded": sensitive,
            "free_text_fields_discarded": free_text,
            "attachment_fields_discarded": attachments,
            "branch_basis": (
                "audited fixed 학습관교육 owner" if edu_type == "1" else "identity-verified detail 센터명"
            ),
        },
    }
    return row, {
        "sensitive": sensitive,
        "free_text": free_text,
        "attachments": attachments,
        "application_controls": len(write_controls),
    }


def _select_options(select: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in (select.select("option") if select else ())
    )


def _booking_form_and_registries(soup: BeautifulSoup, page: int) -> Any:
    forms = soup.select("form#list[name='list']")
    if len(forms) != 1 or _clean(forms[0].get("method")).upper() != "POST":
        raise YangsanContractError(f"booking page {page}: list form changed")
    form = forms[0]
    action = urlparse(urljoin(YANGSAN_BOOKING_CANONICAL_URL, _clean(form.get("action"))))
    if action.path != YANGSAN_BOOKING_LIST_PATH or parse_qs(action.query) != {"mid": [YANGSAN_MID]}:
        raise YangsanContractError(f"booking page {page}: list action changed")
    hidden = {_clean(node.get("name")): _clean(node.get("value")) for node in form.select("input[name]")}
    expected = {
        "page": str(page),
        "lecStartDt": "",
        "lecEndDt": "",
        "appStartDt": "",
        "appEndDt": "",
        "searchTxt": "",
    }
    if {key: hidden.get(key) for key in expected} != expected:
        raise YangsanContractError(f"booking page {page}: unfiltered scope changed")
    if _select_options(form.select_one("select[name='orgIdx']")) != YANGSAN_BOOKING_ORG_REGISTRY:
        raise YangsanContractError(f"booking page {page}: official institution registry changed")
    if _select_options(form.select_one("select[name='lecStateType']")) != YANGSAN_BOOKING_STATE_REGISTRY:
        raise YangsanContractError(f"booking page {page}: status registry changed")
    if _select_options(form.select_one("select[name='lecType']")) != YANGSAN_BOOKING_TYPE_REGISTRY:
        raise YangsanContractError(f"booking page {page}: category registry changed")
    return form


def _booking_capacity(value: str, identity: str) -> dict[str, int]:
    values = [int(item.replace(",", "")) for item in _INTEGER_RE.findall(value)]
    if len(values) != 5:
        raise YangsanContractError(f"course {identity}: booking capacity contract changed")
    return {
        "online_capacity": values[0],
        "online_applicants": values[1],
        "online_waitlist": values[2],
        "offline_capacity": values[3],
        "offline_applicants": values[4],
    }


def _booking_row(first: Any, second: Any, page: int, total: int, offset: int) -> dict[str, Any]:
    primary = first.find_all("td", recursive=False)
    secondary = second.find_all("td", recursive=False)
    if len(primary) != 7 or len(secondary) != 4:
        raise YangsanContractError(f"booking page {page}: paired row structure changed")
    number_text = _clean(primary[0].get_text(" ", strip=True))
    expected_number = total - ((page - 1) * YANGSAN_BOOKING_PAGE_SIZE) - offset
    if number_text != str(expected_number):
        raise YangsanContractError(f"booking page {page}: advertised row numbering changed")
    branch_node = primary[1].select_one(":scope > span.bk")
    branch_source = _clean(branch_node.get_text(" ", strip=True) if branch_node else "")
    if branch_source not in (_BOOKING_REGISTRY_LABELS | {""}):
        raise YangsanContractError(f"booking page {page}: unregistered institution {branch_source!r}")
    title_text = _clean(primary[1].get_text(" ", strip=True))
    title = _clean(title_text[len(branch_source) :]) if title_text.startswith(branch_source) else ""
    anchors = primary[6].select('a[href*="/booking/lecture/view.do"]')
    if len(anchors) != 1 or not title:
        raise YangsanContractError(f"booking page {page}: title/detail control changed")
    detail_url = urljoin(YANGSAN_BOOKING_CANONICAL_URL, _clean(anchors[0].get("href")))
    parsed = urlparse(detail_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _clean((query.get("idx") or [""])[0])
    if not (
        _IDENTITY_RE.fullmatch(identity)
        and _strict_get_url(
            detail_url,
            YANGSAN_BOOKING_DETAIL_PATH,
            [("mid", YANGSAN_MID), ("idx", identity)],
        )
    ):
        raise YangsanContractError(f"booking page {page}: detail identity/path drift")
    source_status = _clean(anchors[0].get_text(" ", strip=True))
    status_code = _BOOKING_STATE_CODE.get(source_status)
    if status_code is None:
        raise YangsanContractError(f"course {identity}: unknown booking status")
    event_start, event_end, event_period_malformed = _booking_dates(
        primary[3].get_text(" ", strip=True), identity, "education period"
    )
    apply_start, apply_end, application_period_malformed = _booking_dates(
        secondary[1].get_text(" ", strip=True), identity, "application period"
    )
    branch = YANGSAN_BOOKING_BRANCH_NORMALIZATION.get(branch_source, branch_source)
    return {
        "provider": YANGSAN_BOOKING_PROVIDER,
        "partition": "booking",
        "identity": identity,
        "source_identity": yangsan_source_identity(YANGSAN_BOOKING_PROVIDER, "booking", identity),
        "page": page,
        "title": title,
        "detail_url": detail_url,
        "branch_source": branch_source,
        "branch": branch,
        "venue": _clean(primary[2].get_text(" ", strip=True)),
        "event_start": event_start,
        "event_end": event_end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule": _clean(secondary[2].get_text(" ", strip=True)),
        "target": _clean(primary[5].get_text(" ", strip=True)),
        "fee": _clean(secondary[3].get_text(" ", strip=True)),
        "capacity": _booking_capacity(primary[4].get_text(" ", strip=True), identity),
        "source_status": source_status,
        "status_code": status_code,
        "status": _BOOKING_STATUS_MAP[status_code],
        "audited_malformed_periods": tuple(
            field
            for field, malformed in (
                ("education period", event_period_malformed),
                ("application period", application_period_malformed),
            )
            if malformed
        ),
    }


def _parse_booking_page(soup: BeautifulSoup, page: int, known_total: int | None = None) -> _Page:
    form = _booking_form_and_registries(soup, page)
    tables = form.select("div.tbl-box > table.tbl.taC")
    no_data = form.select("div.tbl-box > .no_data")
    if known_total is None:
        if len(tables) != 1:
            raise YangsanContractError("booking page 1: source table missing")
        first_number = tables[0].select_one("tbody > tr > td[rowspan='2']")
        total_text = _clean(first_number.get_text(" ", strip=True) if first_number else "")
        if not total_text.isdigit() or int(total_text) < 1:
            raise YangsanContractError("booking page 1: advertised total missing")
        total = int(total_text)
    else:
        total = known_total
    last = math.ceil(total / YANGSAN_BOOKING_PAGE_SIZE)
    expected_headers = (
        "번호",
        "강좌명",
        "교육장소",
        "교육기간",
        "온라인(정원/접수/대기) 오프라인(정원/접수)",
        "수강대상",
        "상태",
        "강사명",
        "접수기간",
        "교육시간",
        "수강료",
    )
    if page > last:
        empty_table = bool(
            len(tables) == 1
            and not no_data
            and not tables[0].select("tbody > tr")
            and tuple(_clean(node.get_text(" ", strip=True)) for node in tables[0].select("thead th"))
            == expected_headers
        )
        empty_message = bool(
            not tables
            and len(no_data) == 1
            and _clean(no_data[0].get_text(" ", strip=True)) == "등록된 교육/강좌가 없습니다."
        )
        if not (empty_table or empty_message):
            raise YangsanContractError("booking immediate post-last sentinel changed")
        return _Page("booking", page, total, last, ())
    if len(tables) != 1 or no_data:
        raise YangsanContractError(f"booking page {page}: data table changed")
    table = tables[0]
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != expected_headers:
        raise YangsanContractError(f"booking page {page}: table headers changed")
    table_rows = table.select("tbody > tr")
    expected = min(YANGSAN_BOOKING_PAGE_SIZE, total - (page - 1) * YANGSAN_BOOKING_PAGE_SIZE)
    if len(table_rows) != expected * 2:
        raise YangsanContractError(f"booking page {page}: expected {expected * 2} physical rows")
    rows = tuple(
        _booking_row(table_rows[index], table_rows[index + 1], page, total, index // 2)
        for index in range(0, len(table_rows), 2)
    )
    identities = [str(row["identity"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise YangsanContractError(f"booking page {page}: duplicate identities")
    return _Page("booking", page, total, last, rows)


def _booking_output_row(listed: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], dict[str, int]]:
    identity = str(listed["identity"])
    tables = [
        table
        for table in soup.select("table.tbl")
        if table.select_one("th") and "강좌명" in table.get_text(" ", strip=True)
    ]
    if len(tables) != 1:
        raise YangsanContractError(f"course {identity}: booking detail table changed")
    fields = _detail_fields(tables[0], identity)
    allowed = _BOOKING_DETAIL_REQUIRED | _BOOKING_DETAIL_OPTIONAL
    if not _BOOKING_DETAIL_REQUIRED <= set(fields) or not set(fields) <= allowed:
        missing = sorted(_BOOKING_DETAIL_REQUIRED - set(fields))
        extra = sorted(set(fields) - allowed)
        raise YangsanContractError(f"course {identity}: booking detail fields changed missing={missing} extra={extra}")
    detail_start, detail_end = _dates(fields["교육기간"], identity, "detail education period")
    apply_start, apply_end = _dates(fields["접수기간"], identity, "detail application period")
    if not (
        fields["강좌명"] == listed["title"]
        and (detail_start, detail_end) == (listed["event_start"], listed["event_end"])
        and (apply_start, apply_end) == (listed["apply_start"], listed["apply_end"])
        and _compact(fields["교육시간"]) in _compact(listed["schedule"])
        and _compact(fields["교육장소"]) == _compact(listed["venue"])
        and _compact(fields["교육대상"]) == _compact(listed["target"])
        and _compact(fields["총수강료"]) == _compact(listed["fee"])
    ):
        raise YangsanContractError(f"course {identity}: booking list/detail identity drift")
    apply_forms = soup.select("form#apply[name='apply']")
    if len(apply_forms) > 1:
        raise YangsanContractError(f"course {identity}: booking application form count changed")
    application_url = ""
    if apply_forms:
        if _clean(apply_forms[0].get("method")).upper() != "POST":
            raise YangsanContractError(f"course {identity}: booking application method changed")
        action = urljoin(YANGSAN_BOOKING_CANONICAL_URL, _clean(apply_forms[0].get("action")))
        parsed = urlparse(action)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if not (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == YANGSAN_HOST
            and parsed.path == YANGSAN_BOOKING_APPLICATION_PATH
            and query == {"lecIdx": [identity], "mid": [YANGSAN_MID]}
        ):
            raise YangsanContractError(f"course {identity}: booking application identity drift")
        if _BOOKING_STATUS_MAP[str(listed["status_code"])] == "OPEN":
            application_url = action
    elif _BOOKING_STATUS_MAP[str(listed["status_code"])] == "OPEN":
        raise YangsanContractError(f"course {identity}: open booking application control missing")
    capacity = int(listed["capacity"]["online_capacity"]) + int(listed["capacity"]["offline_capacity"])
    sensitive = sum(bool(fields.get(label)) for label in ("강사명", "문의처", "안내계좌"))
    free_text = sum(bool(fields.get(label)) for label in ("강좌설명", "환불규정", "주의사항"))
    attachments = int(bool(fields.get("첨부파일")))
    branch = str(listed["branch"])
    branch_code = "YANGSAN_BOOKING_" + hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    period = f"{detail_start.isoformat()} ~ {detail_end.isoformat()}"
    row = {
        "provider": YANGSAN_BOOKING_PROVIDER,
        "provider_course_id": str(listed["source_identity"]),
        "prefer_incoming_provider_course_id": True,
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": branch,
        "branch_code": branch_code,
        "preserve_branch": True,
        "category": "교육·강좌",
        "program_type": "교육",
        "raw_url": str(listed["detail_url"]),
        "application_url": application_url,
        "application_type": ("ONLINE_RESERVATION" if application_url else "INFO_ONLY"),
        "reservation_available": bool(application_url),
        "status": _BOOKING_STATUS_MAP[str(listed["status_code"])],
        "raw_status": str(listed["source_status"]),
        "period": period,
        "start_date": detail_start.isoformat(),
        "end_date": detail_end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": fields["교육시간"],
        "fee": fields["총수강료"],
        "material_fee": fields["재료비"],
        "capacity": str(capacity),
        "capacity_total": capacity,
        "target": fields["교육대상"],
        "venue": fields["교육장소"],
        "venue_name": fields["교육장소"],
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": YANGSAN_BOOKING_PARSER,
        "municipality_code": YANGSAN_MUNICIPALITY_CODE,
        "municipality_name": YANGSAN_MUNICIPALITY_NAME,
        "municipality_full_name": YANGSAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "partition": "booking",
            "source_page": int(listed["page"]),
            "source_status": str(listed["source_status"]),
            "source_status_code": str(listed["status_code"]),
            "source_institution": str(listed["branch_source"]),
            "detail_verified": True,
            "application_form_count": len(apply_forms),
            "application_endpoint_not_requested": True,
            "receipt_endpoint_not_requested": True,
            "login_endpoint_not_requested": True,
            "file_download_endpoint_not_requested": True,
            "attachment_endpoint_not_requested": True,
            "sensitive_detail_fields_discarded": sensitive,
            "free_text_fields_discarded": free_text,
            "attachment_fields_discarded": attachments,
            "branch_basis": "official institution select registry + list institution",
        },
    }
    return row, {
        "sensitive": sensitive,
        "free_text": free_text,
        "attachments": attachments,
        "application_controls": len(apply_forms),
    }


def _privacy_violations(rows: Iterable[Mapping[str, Any]]) -> int:
    forbidden = {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "teacher",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
        "bank_account",
    }
    count = 0
    for row in rows:
        count += len(set(row) & forbidden)
        raw = row.get("raw_fields")
        if isinstance(raw, Mapping):
            count += len(set(raw) & forbidden)
        # The public navigation scope token resembles a Korean telephone
        # number but is a fixed machine identifier embedded in every URL.
        payload = repr(row).replace(YANGSAN_MID, "")
        count += len(_PHONE_RE.findall(payload)) + len(_EMAIL_RE.findall(payload))
    return count


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        re.sub(r"[^0-9a-z가-힣]+", "", _clean(row.get("title")).casefold()),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _compact(row.get("schedule_raw")),
        _clean(row.get("branch")),
        _clean(row.get("venue_name")),
    )


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta(provider: str, parser: str, canonical_url: str, cutoff: date) -> dict[str, Any]:
    return {
        "provider": provider,
        "municipality_code": YANGSAN_MUNICIPALITY_CODE,
        "municipality_name": YANGSAN_MUNICIPALITY_NAME,
        "canonical_url": canonical_url,
        "parser": parser,
        "cutoff": cutoff.isoformat(),
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "application_endpoint_requests": 0,
        "receipt_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "file_download_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_values_persisted": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
        "owner_boundary_audit": {key: dict(value) for key, value in YANGSAN_OWNER_BOUNDARY_AUDIT.items()},
        "audited_source_totals": dict(YANGSAN_AUDITED_SOURCE_TOTALS),
    }


def _collector_loader(
    meta: dict[str, Any],
    session: Any,
    fetcher: Fetcher,
    timeout: int,
) -> Callable[..., BeautifulSoup]:
    def load(
        method: str,
        url: str,
        *,
        kind: str,
        data: Optional[Mapping[str, str]] = None,
    ) -> BeautifulSoup:
        if not _allowed_request(method, url):
            raise YangsanContractError("refusing request outside audited Yangsan routes")
        body = dict(data or {})
        if method == "POST":
            if (
                set(body) != {"idx", "eduType"}
                or not _IDENTITY_RE.fullmatch(_clean(body.get("idx")))
                or _clean(body.get("eduType")) not in {"1", "6"}
            ):
                raise YangsanContractError("refusing unaudited Yangsan POST body")
        meta["list_requests" if kind == "list" else "detail_pages"] += 1
        meta["logical_requests"] += 1
        last_error: Optional[BaseException] = None
        for attempt in range(2):
            meta["physical_requests"] += 1
            try:
                response = fetcher(session, method, url, timeout=timeout, data=body)
                status = int(getattr(response, "status_code", 200))
                if status in {429, 500, 502, 503, 504} and attempt == 0:
                    meta["request_retry_count"] += 1
                    continue
                return _coerce_soup(response, url)
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 0:
                    meta["request_retry_count"] += 1
                    continue
                raise
        raise YangsanContractError(f"request failed: {last_error}")

    return load


def _finalize_rows(
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
    dedupe_rows: Optional[DedupeRows],
) -> list[dict[str, Any]]:
    signatures = [_semantic_signature(row) for row in rows]
    duplicate_semantics = len(signatures) - len(set(signatures))
    meta["semantic_duplicate_count"] = duplicate_semantics
    # Distinct official identities can intentionally repeat the same class in
    # separate booking slots.  Preserve them and expose the overlap in audit
    # metadata instead of collapsing the source ledger.
    deduped = list((dedupe_rows or _default_dedupe)(rows))
    if len(deduped) != len(rows):
        raise YangsanContractError(f"dedupe changed complete row count {len(rows)} to {len(deduped)}")
    privacy = _privacy_violations(deduped)
    meta["pii_values_persisted"] = privacy
    if privacy:
        raise YangsanContractError(f"{privacy} PII allowlist violations")
    deduped.sort(
        key=lambda row: (
            _clean(row.get("start_date")),
            _clean(row.get("title")),
            _clean(row.get("provider_course_id")),
        )
    )
    return deduped


def collect_yangsan_lifelong(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = YANGSAN_MAX_PAGES,
    detail_limit: int = YANGSAN_MAX_DETAILS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    try:
        cutoff = _audit_date(today)
    except (TypeError, ValueError):
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
        meta = _base_meta(
            YANGSAN_LIFELONG_PROVIDER,
            YANGSAN_LIFELONG_PARSER,
            YANGSAN_LIFELONG_CANONICAL_URL,
            cutoff,
        )
        meta["configured_collection_error"] = "today is invalid"
        return [], YANGSAN_LIFELONG_PARSER, meta
    meta = _base_meta(
        YANGSAN_LIFELONG_PROVIDER,
        YANGSAN_LIFELONG_PARSER,
        YANGSAN_LIFELONG_CANONICAL_URL,
        cutoff,
    )
    meta["candidate_id"] = YANGSAN_LIFELONG_CANDIDATE_ID
    if not is_yangsan_lifelong_target(target):
        meta["configured_collection_error"] = "target does not match Yangsan lifelong owner"
        return [], YANGSAN_LIFELONG_PARSER, meta
    try:
        request_timeout = int(timeout)
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        if request_timeout < 1 or allowed_pages < 1 or allowed_details < 0:
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "timeout/max_pages/detail_limit are invalid"
        return [], YANGSAN_LIFELONG_PARSER, meta
    session: Any = None
    pages: dict[str, dict[int, _Page]] = {item.key: {} for item in YANGSAN_LIFELONG_PARTITIONS}
    listed: list[dict[str, Any]] = []
    try:
        session = (session_factory or yangsan_session_factory)()
        load = _collector_loader(meta, session, fetcher or _default_fetcher, request_timeout)
        first_pages: dict[str, _Page] = {}
        required_requests = 0
        for partition in YANGSAN_LIFELONG_PARTITIONS:
            first = _parse_lifelong_page(
                load("GET", yangsan_lifelong_list_url(partition.edu_type, 1), kind="list"),
                partition,
                1,
            )
            first_pages[partition.key] = first
            pages[partition.key][1] = first
            required_requests += first.last + 1 + len(set((1, first.last, first.last + 1)))
        meta["required_list_requests"] = required_requests
        if required_requests > allowed_pages:
            meta["source_cap_reached"] = True
            raise YangsanContractError(
                f"max_pages cap allows {allowed_pages} of {required_requests} required list requests"
            )
        sentinels: dict[str, _Page] = {}
        for partition in YANGSAN_LIFELONG_PARTITIONS:
            first = first_pages[partition.key]
            for page in range(2, first.last + 1):
                pages[partition.key][page] = _parse_lifelong_page(
                    load(
                        "GET",
                        yangsan_lifelong_list_url(partition.edu_type, page),
                        kind="list",
                    ),
                    partition,
                    page,
                )
            sentinel_page = first.last + 1
            sentinels[partition.key] = _parse_lifelong_page(
                load(
                    "GET",
                    yangsan_lifelong_list_url(partition.edu_type, sentinel_page),
                    kind="list",
                ),
                partition,
                sentinel_page,
            )
            for page in range(1, first.last + 1):
                parsed = pages[partition.key][page]
                if parsed.total != first.total or parsed.last != first.last:
                    raise YangsanContractError(f"{partition.key} pagination declaration drift")
                listed.extend(dict(row) for row in parsed.rows)
        source_ids = [str(row["source_identity"]) for row in listed]
        if len(source_ids) != len(set(source_ids)):
            raise YangsanContractError("lifelong partition identities overlap")
        current = [row for row in listed if row["event_end"] >= cutoff]
        expired = [row for row in listed if row["event_end"] < cutoff]
        if len(current) > allowed_details:
            meta["source_cap_reached"] = True
            raise YangsanContractError(
                f"detail_limit cap allows {allowed_details} of {len(current)} current/future details"
            )
        output: list[dict[str, Any]] = []
        discarded = Counter()
        for row in current:
            edu_type = str(row["edu_type"])
            soup = load(
                "POST",
                yangsan_lifelong_detail_url(edu_type),
                kind="detail",
                data={"idx": str(row["identity"]), "eduType": edu_type},
            )
            parsed, counters = _lifelong_output_row(row, soup)
            output.append(parsed)
            discarded.update(counters)
        rechecks: dict[str, bool] = {}
        for partition in YANGSAN_LIFELONG_PARTITIONS:
            first = first_pages[partition.key]
            expected = {
                1: pages[partition.key][1],
                first.last: pages[partition.key][first.last],
                first.last + 1: sentinels[partition.key],
            }
            for page, original in expected.items():
                observed = _parse_lifelong_page(
                    load(
                        "GET",
                        yangsan_lifelong_list_url(partition.edu_type, page),
                        kind="list",
                    ),
                    partition,
                    page,
                )
                stable = _page_signature(observed) == _page_signature(original)
                rechecks[f"{partition.key}:{page}"] = stable
                if not stable:
                    raise YangsanContractError(f"{partition.key} page {page}: boundary stability recheck changed")
        deduped = _finalize_rows(output, meta, dedupe_rows)
        partition_totals = {key: first_pages[key].total for key in ("forever_1", "forever_6")}
        partition_current = dict(Counter(row["partition"] for row in current))
        meta.update(
            {
                "pages": sum(first.last for first in first_pages.values()),
                "data_pages": sum(first.last for first in first_pages.values()),
                "partition_totals": partition_totals,
                "source_total": sum(partition_totals.values()),
                "source_rows": len(listed),
                "partition_page_counts": {
                    key: {page: len(value.rows) for page, value in source_pages.items()}
                    for key, source_pages in pages.items()
                },
                "empty_sentinel_pages": {key: first_pages[key].last + 1 for key in first_pages},
                "boundary_rechecks": rechecks,
                "boundary_recheck_count": len(rechecks),
                "current_source_count": len(current),
                "current_partition_counts": partition_current,
                "expired_count": len(expired),
                "detail_verified": len(output),
                "returned_count": len(deduped),
                "source_status_counts": dict(Counter(row["source_status"] for row in current)),
                "status_counts": dict(Counter(row["status"] for row in deduped)),
                "branch_counts": dict(Counter(row["branch"] for row in deduped)),
                "source_identity_count": len(source_ids),
                "source_identity_sha256": hashlib.sha256("\n".join(sorted(source_ids)).encode("utf-8")).hexdigest(),
                "application_control_count": discarded["application_controls"],
                "sensitive_detail_fields_discarded": discarded["sensitive"],
                "free_text_fields_discarded": discarded["free_text"],
                "attachment_fields_discarded": discarded["attachments"],
                "pagination_detected": first.last > 1,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, YANGSAN_LIFELONG_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "source_rows": len(listed),
                "returned_count": 0,
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], YANGSAN_LIFELONG_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def collect_yangsan_booking(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = YANGSAN_MAX_PAGES,
    detail_limit: int = YANGSAN_MAX_DETAILS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    try:
        cutoff = _audit_date(today)
    except (TypeError, ValueError):
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
        meta = _base_meta(
            YANGSAN_BOOKING_PROVIDER,
            YANGSAN_BOOKING_PARSER,
            YANGSAN_BOOKING_CANONICAL_URL,
            cutoff,
        )
        meta["configured_collection_error"] = "today is invalid"
        return [], YANGSAN_BOOKING_PARSER, meta
    meta = _base_meta(
        YANGSAN_BOOKING_PROVIDER,
        YANGSAN_BOOKING_PARSER,
        YANGSAN_BOOKING_CANONICAL_URL,
        cutoff,
    )
    meta.update(
        {
            "legacy_candidate_id": YANGSAN_BOOKING_LEGACY_CANDIDATE_ID,
            "canonical_candidate_id": YANGSAN_BOOKING_CANONICAL_CANDIDATE_ID,
            "configured_url_was_legacy": _clean(_target_value(target, "url")) == YANGSAN_BOOKING_LEGACY_URL,
        }
    )
    if not is_yangsan_booking_target(target):
        meta["configured_collection_error"] = "target does not match Yangsan booking owner"
        return [], YANGSAN_BOOKING_PARSER, meta
    try:
        request_timeout = int(timeout)
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        if request_timeout < 1 or allowed_pages < 1 or allowed_details < 0:
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "timeout/max_pages/detail_limit are invalid"
        return [], YANGSAN_BOOKING_PARSER, meta
    session: Any = None
    pages: dict[int, _Page] = {}
    listed: list[dict[str, Any]] = []
    try:
        session = (session_factory or yangsan_session_factory)()
        load = _collector_loader(meta, session, fetcher or _default_fetcher, request_timeout)
        first = _parse_booking_page(load("GET", yangsan_booking_list_url(1), kind="list"), 1)
        pages[1] = first
        required_requests = first.last + 1 + len(set((1, first.last, first.last + 1)))
        meta["required_list_requests"] = required_requests
        if required_requests > allowed_pages:
            meta["source_cap_reached"] = True
            raise YangsanContractError(
                f"max_pages cap allows {allowed_pages} of {required_requests} required list requests"
            )
        for page in range(2, first.last + 1):
            pages[page] = _parse_booking_page(
                load("GET", yangsan_booking_list_url(page), kind="list"),
                page,
                first.total,
            )
        sentinel_page = first.last + 1
        sentinel = _parse_booking_page(
            load("GET", yangsan_booking_list_url(sentinel_page), kind="list"),
            sentinel_page,
            first.total,
        )
        for page in range(1, first.last + 1):
            listed.extend(dict(row) for row in pages[page].rows)
        if len(listed) != first.total:
            raise YangsanContractError("booking advertised total differs from row union")
        source_ids = [str(row["source_identity"]) for row in listed]
        if len(source_ids) != len(set(source_ids)):
            raise YangsanContractError("booking duplicate source identities")
        audited_malformed = [row for row in listed if row["audited_malformed_periods"]]
        audited_malformed_current = [
            row for row in audited_malformed if max(row["event_start"], row["event_end"]) >= cutoff
        ]
        valid_listed = [row for row in listed if not row["audited_malformed_periods"]]
        unexpected_future = [
            row
            for row in valid_listed
            if row["event_end"] >= cutoff and row["status_code"] not in _BOOKING_CURRENT_CODES
        ]
        if unexpected_future:
            raise YangsanContractError(f"{len(unexpected_future)} future rows use unaudited booking status codes")
        current = [
            row for row in valid_listed if row["event_end"] >= cutoff and row["status_code"] in _BOOKING_CURRENT_CODES
        ]
        if {str(row["branch"]) for row in current} - _BOOKING_CURRENT_BRANCHES:
            raise YangsanContractError("current booking institution is outside the audited branch set")
        expired = [row for row in valid_listed if row["event_end"] < cutoff]
        if len(current) > allowed_details:
            meta["source_cap_reached"] = True
            raise YangsanContractError(f"detail_limit cap allows {allowed_details} of {len(current)} current details")
        output: list[dict[str, Any]] = []
        discarded = Counter()
        for row in current:
            soup = load("GET", str(row["detail_url"]), kind="detail")
            parsed, counters = _booking_output_row(row, soup)
            output.append(parsed)
            discarded.update(counters)
        rechecks: dict[str, bool] = {}
        expected = {1: pages[1], first.last: pages[first.last], sentinel_page: sentinel}
        for page, original in expected.items():
            observed = _parse_booking_page(
                load("GET", yangsan_booking_list_url(page), kind="list"),
                page,
                first.total,
            )
            stable = _page_signature(observed) == _page_signature(original)
            rechecks[str(page)] = stable
            if not stable:
                raise YangsanContractError(f"booking page {page}: boundary stability recheck changed")
        deduped = _finalize_rows(output, meta, dedupe_rows)
        meta.update(
            {
                "pages": first.last,
                "data_pages": first.last,
                "page_counts": {page: len(value.rows) for page, value in pages.items()},
                "empty_sentinel_page": sentinel_page,
                "boundary_rechecks": rechecks,
                "boundary_recheck_count": len(rechecks),
                "source_total": first.total,
                "source_rows": len(listed),
                "source_publishable_rows": len(valid_listed),
                "current_source_count": len(current) + len(audited_malformed_current),
                "publishable_current_source_count": len(current),
                "expired_count": len(expired),
                "audited_malformed_source_count": len(audited_malformed),
                "audited_malformed_current_count": len(audited_malformed_current),
                "audited_malformed_identities": [str(row["identity"]) for row in audited_malformed],
                "audited_malformed_period_counts": dict(
                    Counter(field for row in audited_malformed for field in row["audited_malformed_periods"])
                ),
                "detail_verified": len(output),
                "returned_count": len(deduped),
                "source_status_code_counts": dict(Counter(row["status_code"] for row in current)),
                "source_status_counts": dict(Counter(row["source_status"] for row in current)),
                "status_counts": dict(Counter(row["status"] for row in deduped)),
                "branch_counts": dict(Counter(row["branch"] for row in deduped)),
                "source_identity_count": len(source_ids),
                "source_identity_sha256": hashlib.sha256("\n".join(sorted(source_ids)).encode("utf-8")).hexdigest(),
                "application_control_count": discarded["application_controls"],
                "sensitive_detail_fields_discarded": discarded["sensitive"],
                "free_text_fields_discarded": discarded["free_text"],
                "attachment_fields_discarded": discarded["attachments"],
                "pagination_detected": first.last > 1,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, YANGSAN_BOOKING_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "source_rows": len(listed),
                "returned_count": 0,
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], YANGSAN_BOOKING_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def collect_yangsan_education(target: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if _clean(_target_value(target, "provider")) == YANGSAN_LIFELONG_PROVIDER:
        return collect_yangsan_lifelong(target, **kwargs)
    if _clean(_target_value(target, "provider")) == YANGSAN_BOOKING_PROVIDER:
        return collect_yangsan_booking(target, **kwargs)
    cutoff = _audit_date(kwargs.get("today"))
    meta = _base_meta("", "", "", cutoff)
    meta["configured_collection_error"] = "unsupported Yangsan provider"
    return [], "", meta


collect = collect_yangsan_education


__all__ = [
    "YANGSAN_AUDITED_SOURCE_TOTALS",
    "YANGSAN_BOOKING_APPLICATION_PATH",
    "YANGSAN_BOOKING_BRANCH_NORMALIZATION",
    "YANGSAN_BOOKING_CANONICAL_CANDIDATE_ID",
    "YANGSAN_BOOKING_CANONICAL_URL",
    "YANGSAN_BOOKING_DETAIL_PATH",
    "YANGSAN_BOOKING_LEGACY_CANDIDATE_ID",
    "YANGSAN_BOOKING_LEGACY_URL",
    "YANGSAN_BOOKING_LIST_PATH",
    "YANGSAN_BOOKING_ORG_REGISTRY",
    "YANGSAN_BOOKING_PARSER",
    "YANGSAN_BOOKING_PROVIDER",
    "YANGSAN_BOOKING_STATE_REGISTRY",
    "YANGSAN_BOOKING_TYPE_REGISTRY",
    "YANGSAN_FOREVER_DETAIL_PATH",
    "YANGSAN_HAPPINESS_DETAIL_PATH",
    "YANGSAN_HOST",
    "YANGSAN_LIFELONG_CANDIDATE_ID",
    "YANGSAN_LIFELONG_CANONICAL_URL",
    "YANGSAN_LIFELONG_LIST_PATH",
    "YANGSAN_LIFELONG_PARSER",
    "YANGSAN_LIFELONG_PARTITIONS",
    "YANGSAN_LIFELONG_PROVIDER",
    "YANGSAN_MID",
    "YANGSAN_MUNICIPALITY_CODE",
    "YANGSAN_MUNICIPALITY_NAME",
    "YANGSAN_OWNER_BOUNDARY_AUDIT",
    "YangsanContractError",
    "collect",
    "collect_yangsan_booking",
    "collect_yangsan_education",
    "collect_yangsan_lifelong",
    "is_target",
    "is_yangsan_booking_target",
    "is_yangsan_education_target",
    "is_yangsan_lifelong_target",
    "yangsan_booking_detail_url",
    "yangsan_booking_list_url",
    "yangsan_lifelong_detail_url",
    "yangsan_lifelong_list_url",
    "yangsan_session_factory",
    "yangsan_source_identity",
]
