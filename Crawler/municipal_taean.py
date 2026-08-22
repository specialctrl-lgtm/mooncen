"""Fail-closed collector for Taean County's complete education ledger.

The official ``all`` list is the canonical owner.  The nine institution/category
lists are disjoint filtered views of that ledger, while the former generic
fanout repeatedly downloaded seven of those subsets and did not prove a stable
complete snapshot.  This collector reads every page of the complete ledger at
the official ``pageUnit=100`` boundary, requires the immediate empty sentinel,
and rechecks the first and last pages before publishing anything.

Only current/future course details are requested.  Applicant forms, external
forms, attachments, instructors, staff/contact fields and free-form detail text
are never fetched separately or persisted.  A small set of source date defects
is corrected only when both the audited raw value and discarded detail evidence
still match.  Any other identity, pagination, detail, application-control or
privacy drift makes the whole snapshot atomically empty.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


TAEAN_PROVIDER = "MUNI_WWW_TAEAN_GO_KR_ADF2555A"
TAEAN_CANDIDATE_ID = "MUNI_IR_824C5741E529"
TAEAN_MUNICIPALITY_CODE = "4482500000"
TAEAN_MUNICIPALITY_NAME = "충청남도 태안군"
TAEAN_HOST = "www.taean.go.kr"
TAEAN_CANONICAL_URL = f"https://{TAEAN_HOST}/edu.do"
TAEAN_LIST_PATH = "/prog/educate/all/edu/sub01_01/list.do"
TAEAN_DETAIL_PATH = "/prog/educate/all/edu/sub01_01/view.do"
TAEAN_APPLICATION_PATH = "/prog/educate_reserve/all/edu/sub01_01/write.do"
TAEAN_SOURCE_URL = f"https://{TAEAN_HOST}{TAEAN_LIST_PATH}?se=01"
TAEAN_PAGE_SIZE = 100
TAEAN_MAX_WORKERS = 12
TAEAN_MAX_HTML_BYTES = 2_000_000
TAEAN_PARSER = (
    "taean_complete_all_education_ledger+pageunit100_all_pages+exact_empty_sentinel+"
    "stable_first_last+all_current_details+audited_source_date_repairs+"
    "identity_bound_application_controls+actual_institution_branches+pii_allowlist"
)


TAEAN_CATEGORY_VIEWS: dict[str, dict[str, Any]] = {
    "평생학습관": {
        "path": "/prog/educate/lll/edu/sub02_01_01/list.do?se=01",
        "audited_total_2026_07_22": 835,
    },
    "청소년수련관": {
        "path": "/prog/educate/teen/edu/sub06_01_01/list.do?se=01",
        "audited_total_2026_07_22": 653,
    },
    "가족센터": {
        "path": "/prog/educate/damunhwa/edu/sub09_01_01/list.do?se=01",
        "audited_total_2026_07_22": 358,
    },
    "가족공감센터": {
        "path": "/prog/educate/family/edu/sub10_01_01/list.do?se=01",
        "audited_total_2026_07_22": 158,
    },
    "전산관리팀": {
        "path": "/prog/educate/info/edu/sub05_01_01/list.do?se=01",
        "audited_total_2026_07_22": 105,
    },
    "농업기술센터": {
        "path": "/prog/educate/atc/edu/sub04_01_01/list.do?se=01",
        "audited_total_2026_07_22": 105,
    },
    "태안청년창업비즈니스센터": {
        "path": "/prog/educate/youth/edu/sub07_02_02/list.do?se=01",
        "audited_total_2026_07_22": 79,
    },
    "유관 교육기관": {
        "path": "/prog/educate/yugwan/edu/sub07_01_01/list.do?se=01",
        "audited_total_2026_07_22": 19,
    },
    "먹거리유통과": {
        "path": "/prog/educate/food/edu/sub04_02_01/list.do?se=01",
        "audited_total_2026_07_22": 4,
    },
}

TAEAN_UNASSIGNED_LEGACY_IDS = frozenset(
    {
        "233",
        "241",
        "242",
        "243",
        "244",
        "245",
        "246",
        "247",
        "267",
        "288",
        "292",
        "293",
        "294",
        "308",
        "319",
        "321",
        "324",
        "330",
        "341",
        "359",
        "360",
        "458",
        "623",
        "630",
        "757",
        "852",
        "860",
    }
)

TAEAN_DISCOVERY_AUDIT: dict[str, Any] = {
    "canonical_owner": {
        "decision": "include_complete_all_education_ledger",
        "landing_url": TAEAN_CANONICAL_URL,
        "source_url": TAEAN_SOURCE_URL,
        "provider": TAEAN_PROVIDER,
        "candidate_id": TAEAN_CANDIDATE_ID,
        "audited_total_2026_07_22": 2343,
    },
    "category_views": {
        "decision": "same_owner_disjoint_filtered_views_not_separate_sources",
        "audited_union_2026_07_22": 2316,
        "views": TAEAN_CATEGORY_VIEWS,
    },
    "unassigned_legacy_rows": {
        "decision": "present_only_in_complete_owner",
        "audited_count_2026_07_22": 27,
        "identities": tuple(sorted(TAEAN_UNASSIGNED_LEGACY_IDS, key=int)),
    },
    "legacy_woman_route": {
        "decision": "exclude_dead_http_500_route",
        "path": "/prog/educate/woman/edu/sub03_01_01/list.do?se=01",
    },
    "generic_fanout": {
        "decision": "replace_redundant_partial_generic_fanout",
        "reason": (
            "the all view already owns every identity; seven repeated subset views add no "
            "courses, omit the official lll/food filters, share a page cap, and do not prove "
            "an empty sentinel, stable boundaries, current detail identity or application controls"
        ),
    },
}

TAEAN_PII_FIELDS_NEVER_PERSISTED = (
    "강사명",
    "담당자",
    "문의전화",
    "전화번호",
    "신청자명",
    "생년월일",
    "주소",
    "이메일",
    "교육내용",
    "기타사항",
    "강의계획서",
    "첨부파일",
    "원문 HTML",
)


@dataclass(frozen=True)
class _PeriodCorrection:
    raw: str
    start: date
    end: date
    title_fragment: str
    evidence_regex: str


def _correction(
    raw: str,
    start: str,
    end: str,
    title_fragment: str,
    evidence_regex: str,
) -> _PeriodCorrection:
    return _PeriodCorrection(
        raw=raw,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        title_fragment=title_fragment,
        evidence_regex=evidence_regex,
    )


# These are source-side input errors, not inferred rolling rules.  A correction
# is used only while its exact raw period, title fragment and discarded detail
# evidence remain present.  Course 402 is an old 2016 row whose end year was
# entered as 2106; correcting it prevents a false future course.
TAEAN_PERIOD_CORRECTIONS: dict[str, _PeriodCorrection] = {
    "4028": _correction(
        "2026-07-30 ~ 2026-07-30",
        "2026-08-20",
        "2026-08-20",
        "공학교실 8월 20일 - 저학년",
        r"운영기간\s*:\s*2026\.\s*8\.\s*20\.",
    ),
    "4027": _correction(
        "2026-07-30 ~ 2026-03-30",
        "2026-08-20",
        "2026-08-20",
        "공학교실 8월 20일 - 고학년",
        r"운영기간\s*:\s*2026\.\s*8\.\s*20\.",
    ),
    "4026": _correction(
        "2026-07-30 ~ 2026-07-30",
        "2026-08-06",
        "2026-08-06",
        "공학교실 8월 6일 - 저학년",
        r"운영기간\s*:\s*2026\.\s*8\.\s*6\.",
    ),
    "4025": _correction(
        "2026-07-30 ~ 2026-07-30",
        "2026-08-06",
        "2026-08-06",
        "공학교실 8월 6일 - 고학년",
        r"운영기간\s*:\s*2026\.\s*8\.\s*6\.",
    ),
    "4024": _correction(
        "2026-10-30 ~ 2026-07-30",
        "2026-07-30",
        "2026-07-30",
        "공학교실 7월 30일 - 고학년",
        r"운영기간\s*:\s*2026\.\s*7\.\s*30\.",
    ),
    "4023": _correction(
        "2026-08-30 ~ 2026-07-30",
        "2026-07-30",
        "2026-07-30",
        "공학교실 7월 30일 - 저학년",
        r"운영기간\s*:\s*2026\.\s*7\.\s*30\.",
    ),
    "4012": _correction(
        "2026-08-07 ~ 2026-07-31",
        "2026-07-08",
        "2026-07-31",
        "수영강습 7월 A반",
        r"수영강습\s*A반.*기간\s*:\s*1년",
    ),
    "3982": _correction(
        "2026-03-14 ~ 2026-08-30",
        "2026-06-14",
        "2026-08-23",
        "여름 프로그램)암벽등반",
        r"강습일\s*:\s*6월\s*14일.*8월\s*9일\s*,\s*23일",
    ),
    "402": _correction(
        "2016-08-31 ~ 2106-11-23",
        "2016-08-31",
        "2016-11-23",
        "사진교실",
        r"사진교실",
    ),
}

# Every malformed/reversed historical period observed in the complete ledger on
# 2026-07-22, excluding the four current rows handled by corrections above.
# Unknown malformed rows are never silently ignored.
TAEAN_AUDITED_HISTORICAL_PERIOD_DEFECT_IDS = frozenset(
    {
        "3938",
        "3914",
        "3800",
        "3634",
        "3579",
        "3517",
        "3500",
        "3409",
        "3379",
        "3329",
        "3173",
        "2991",
        "2958",
        "2928",
        "2880",
        "2844",
        "2489",
        "2376",
        "2368",
        "2344",
        "2254",
        "1982",
        "1978",
        "1925",
        "1831",
        "1810",
        "1753",
        "1630",
        "1470",
        "1458",
        "1377",
        "1165",
        "933",
        "798",
        "778",
        "775",
        "736",
        "732",
        "376",
        "267",
        "247",
        "246",
        "245",
        "244",
        "243",
        "242",
    }
)

TAEAN_NONEDUCATION_IDS = frozenset({"267"})  # exact legacy "테스트 강좌"

# Old rows for which the application-method badge or capacity denominator was
# never populated.  They are retained for total/identity reconciliation but are
# all expired; a new/current row with either defect is rejected.
TAEAN_AUDITED_BLANK_METHOD_IDS = frozenset(
    {
        "3170",
        "2343",
        "2305",
        "2299",
        "2184",
        "2158",
        "2052",
        "1443",
        "1378",
        "1111",
        "1010",
        "1001",
        "999",
        "998",
        "997",
        "996",
        "995",
        "994",
        "993",
        "985",
        "977",
        "900",
        "894",
        "890",
        "441",
        "397",
        "358",
        "267",
        "247",
        "246",
        "245",
        "244",
        "243",
    }
)
TAEAN_AUDITED_MISSING_CAPACITY_IDS = frozenset(
    {
        "3770",
        "3615",
        "2637",
        "2404",
        "809",
        "794",
        "324",
        "319",
        "306",
        "303",
        "288",
        "267",
        "247",
        "246",
        "245",
        "244",
        "243",
    }
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class TaeanContractError(ValueError):
    """Raised when the audited official Taean contract changes."""


@dataclass(frozen=True)
class _ListPage:
    page: int
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE_TOKEN = re.compile(r"(?<!\d)((?:20|21)\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS = {"접수가능": "OPEN", "접수대기": "SCHEDULED", "접수마감": "CLOSED"}
_METHODS = {"자체접수", "기관접수", "방문접수", ""}
_INSTITUTIONS = frozenset(TAEAN_CATEGORY_VIEWS)
_REQUIRED_LIST_FIELDS = frozenset(
    {"교육기관", "접수기간", "신청/정원", "교육기간", "교육시간"}
)
_REQUIRED_DETAIL_FIELDS = frozenset(
    {
        "강좌명",
        "교육기간",
        "교육시간",
        "접수기간",
        "교육장소",
        "정원",
        "교육대상",
        "수강료",
        "교육기관",
    }
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "source_category",
        "source_status",
        "source_method",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_institution",
        "source_capacity",
        "period_corrected",
        "detail_verified",
        "visible_application_control_present",
        "actionable_application_control_present",
        "external_control_blocked",
        "insecure_external_control_blocked",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "instructor",
        "staff",
        "attachments",
        "attachment_urls",
        "detail_description",
        "education_content",
        "other_notes",
        "source_html",
        "raw_html",
    }
)

_FIXED_BRANCHES = {
    "전산관리팀": "태안군청 전산교육장",
    "가족공감센터": "태안군가족공감센터",
    "가족센터": "태안군가족센터",
    "청소년수련관": "태안군청소년수련관",
    "태안청년창업비즈니스센터": "태안청년창업비즈니스센터",
    "농업기술센터": "태안군농업기술센터",
    "유관 교육기관": "태안군 유관 교육기관",
    "먹거리유통과": "태안군 먹거리유통과",
}
_LIFELONG_BRANCH_RULES = (
    ("원북면", "태안군 원북면 평생학습센터"),
    ("소원면", "태안군 소원면 평생학습센터"),
    ("고남면", "태안군 고남면 평생학습센터"),
    ("남면", "태안군 남면 평생학습센터"),
    ("안면읍", "태안군 안면읍 평생학습센터"),
    ("장애인가족지원센터", "태안군장애인가족지원센터"),
    ("장애인복지관", "태안군장애인복지관"),
    ("태안지역자활센터", "태안지역자활센터"),
    ("시각장애인회관", "충남시각장애인협회 태안군지회"),
    ("충남장애인부모회", "(사)충남장애인부모회 태안지회"),
    ("파크골프장", "태안군 파크골프장"),
    ("교육문화센터", "태안군교육문화센터"),
    ("기원", "태안군 기원"),
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_taean_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != TAEAN_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == TAEAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == "/edu.do"
        and not query
        and not parsed.params
        and not parsed.fragment
    )


is_target = is_taean_education_target


def taean_list_url(page: Any) -> str:
    raw = _clean(page)
    if not raw.isdigit() or int(raw) < 1:
        return ""
    return f"https://{TAEAN_HOST}{TAEAN_LIST_PATH}?" + urlencode(
        {"se": "01", "pageUnit": TAEAN_PAGE_SIZE, "pageIndex": int(raw)}
    )


def taean_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY.fullmatch(value):
        return ""
    return f"https://{TAEAN_HOST}{TAEAN_DETAIL_PATH}?" + urlencode(
        {"eduNo": value, "se": "1"}
    )


def taean_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY.fullmatch(value):
        return ""
    return f"https://{TAEAN_HOST}{TAEAN_APPLICATION_PATH}?" + urlencode(
        {
            "pageIndex": "1",
            "eduNo": value,
            "oneInwon": "",
            "resvChk": "N",
            "se": "1",
        }
    )


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
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


def _official_request_url(value: str) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == TAEAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.fragment
    ):
        return False
    if parsed.path == TAEAN_LIST_PATH:
        return bool(
            set(query) == {"se", "pageUnit", "pageIndex"}
            and query["se"] == ["01"]
            and query["pageUnit"] == [str(TAEAN_PAGE_SIZE)]
            and len(query["pageIndex"]) == 1
            and _IDENTITY.fullmatch(query["pageIndex"][0])
        )
    if parsed.path == TAEAN_DETAIL_PATH:
        return bool(
            set(query) == {"eduNo", "se"}
            and query["se"] == ["1"]
            and len(query["eduNo"]) == 1
            and _IDENTITY.fullmatch(query["eduNo"][0])
        )
    return False


def _response_soup(
    url: str,
    timeout: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    if not _official_request_url(url):
        raise TaeanContractError("non-canonical request URL refused")
    session = session_factory()
    try:
        response = fetcher(session, url, timeout)
        status = int(getattr(response, "status_code", 200))
        if status < 200 or status >= 300:
            raise TaeanContractError(f"HTTP {status} is not a successful response")
        final_url = _clean(getattr(response, "url", url)) or url
        if not _official_request_url(final_url):
            raise TaeanContractError("redirect outside the audited Taean routes")
        content = getattr(response, "content", None)
        if content is None:
            content = _clean(getattr(response, "text", response)).encode("utf-8")
        if not isinstance(content, (bytes, bytearray)) or not content:
            raise TaeanContractError("empty HTML response")
        if len(content) > TAEAN_MAX_HTML_BYTES:
            raise TaeanContractError("HTML response size cap exceeded")
        return BeautifulSoup(bytes(content), "html.parser")
    finally:
        _close_quietly(session)


def _safe_public_text(value: Any, *, label: str, required: bool = False) -> str:
    text = _clean(value)
    if required and not text:
        raise TaeanContractError(f"empty {label}")
    if _PHONE.search(text) or _EMAIL.search(text):
        raise TaeanContractError(f"PII/contact pattern in {label}")
    return text


def _list_fields(card: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in card.select("ul.info > li"):
        label_node = item.select_one("b")
        if label_node is None:
            continue
        label = _clean(label_node.get_text(" ", strip=True))
        whole = _clean(item.get_text(" ", strip=True))
        value = whole[len(label) :].lstrip(" :") if whole.startswith(label) else ""
        if not label or label in fields:
            raise TaeanContractError("duplicate/empty list field label")
        fields[label] = _clean(value)
    return fields


def _date_pair(value: str) -> tuple[date, date]:
    matches = _DATE_TOKEN.findall(_clean(value))
    if len(matches) != 2:
        raise ValueError("period does not contain exactly two dates")
    values = tuple(date(int(year), int(month), int(day)) for year, month, day in matches)
    if values[1] < values[0]:
        raise ValueError("period is reversed")
    return values[0], values[1]


def _education_period(
    identity: str,
    title: str,
    raw_period: str,
    source_status: str,
) -> tuple[Optional[date], Optional[date], bool, bool]:
    correction = TAEAN_PERIOD_CORRECTIONS.get(identity)
    if correction is not None and raw_period == correction.raw:
        if correction.title_fragment not in title:
            raise TaeanContractError(f"course {identity}: date-correction title drift")
        return correction.start, correction.end, True, False
    try:
        start, end = _date_pair(raw_period)
        return start, end, False, False
    except (TypeError, ValueError):
        if (
            identity in TAEAN_AUDITED_HISTORICAL_PERIOD_DEFECT_IDS
            and source_status == "접수마감"
        ):
            return None, None, False, True
        raise TaeanContractError(f"course {identity}: unaudited invalid education period")


def _capacity(value: str, identity: str) -> tuple[int, Optional[int], Optional[int]]:
    match = re.search(r"(\d[\d,]*)\s*명\s*/\s*(\d[\d,]*)\s*명", value)
    if match is None:
        missing = re.fullmatch(r"\s*(\d[\d,]*)\s*명\s*/\s*명\s*", value)
        if missing is not None and identity in TAEAN_AUDITED_MISSING_CAPACITY_IDS:
            return int(missing.group(1).replace(",", "")), None, None
        raise TaeanContractError(f"course {identity}: invalid application/capacity value")
    applied = int(match.group(1).replace(",", ""))
    total = int(match.group(2).replace(",", ""))
    wait = re.search(r"대기\s*\(\s*(\d[\d,]*)\s*명\s*\)", value)
    return applied, total, int(wait.group(1).replace(",", "")) if wait else None


def _parse_list_page(soup: BeautifulSoup, page: int) -> _ListPage:
    marker = soup.select_one(".board_total")
    if marker is None:
        raise TaeanContractError(f"page {page}: total marker missing")
    marker_text = _clean(marker.get_text(" ", strip=True))
    total_match = re.search(r"Total\s*:\s*(\d[\d,]*)", marker_text, re.IGNORECASE)
    if total_match is None:
        raise TaeanContractError(f"page {page}: total marker changed")
    total = int(total_match.group(1).replace(",", ""))
    last = max(1, (total + TAEAN_PAGE_SIZE - 1) // TAEAN_PAGE_SIZE)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in soup.select("div.courses_wrap > div.list"):
        anchors = card.select(".tit a[href*='eduNo=']")
        if len(anchors) != 1:
            raise TaeanContractError(f"page {page}: course identity link changed")
        anchor = anchors[0]
        href = urljoin(taean_list_url(page), _clean(anchor.get("href")))
        parsed = urlparse(href)
        try:
            port = parsed.port
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise TaeanContractError(f"page {page}: malformed detail link") from exc
        identities = query.get("eduNo", [])
        identity = identities[0] if len(identities) == 1 else ""
        if not (
            parsed.scheme == "https"
            and (parsed.hostname or "").rstrip(".").lower() == TAEAN_HOST
            and port is None
            and parsed.username is None
            and parsed.password is None
            and parsed.path == TAEAN_DETAIL_PATH
            and not parsed.fragment
            and _IDENTITY.fullmatch(identity)
            and identity not in seen
        ):
            raise TaeanContractError(f"page {page}: invalid/duplicate course identity")
        seen.add(identity)
        title = _safe_public_text(
            anchor.get_text(" ", strip=True), label=f"course {identity} title", required=True
        )
        category_node = card.select_one(".tit .cate")
        category = _safe_public_text(
            category_node.get_text(" ", strip=True) if category_node else "",
            label=f"course {identity} category",
        )
        status_node = card.select_one(".state_btn b")
        source_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
        if source_status not in _STATUS:
            raise TaeanContractError(f"course {identity}: unknown source status")
        method_node = card.select_one(".state_btn span")
        source_method = _clean(
            method_node.get_text(" ", strip=True) if method_node else ""
        )
        if source_method not in _METHODS:
            raise TaeanContractError(f"course {identity}: unknown application method")
        fields = _list_fields(card)
        if not _REQUIRED_LIST_FIELDS <= set(fields):
            raise TaeanContractError(f"course {identity}: required list fields missing")
        institution = _clean(fields["교육기관"])
        if institution not in _INSTITUTIONS and not (
            not institution and identity in TAEAN_UNASSIGNED_LEGACY_IDS
        ):
            raise TaeanContractError(f"course {identity}: unmapped education institution")
        if not source_method and not (
            source_status == "접수마감" and identity in TAEAN_AUDITED_BLANK_METHOD_IDS
        ):
            raise TaeanContractError(f"course {identity}: missing current application method")
        applied, capacity_total, wait_count = _capacity(fields["신청/정원"], identity)
        start, end, corrected, historical_defect = _education_period(
            identity, title, fields["교육기간"], source_status
        )
        rows.append(
            {
                "identity": identity,
                "title": title,
                "category": category,
                "source_status": source_status,
                "source_method": source_method,
                "institution": institution,
                "apply_period": _clean(fields["접수기간"]),
                "education_period": _clean(fields["교육기간"]),
                "schedule": _safe_public_text(
                    fields["교육시간"], label=f"course {identity} schedule"
                ),
                "capacity_source": _clean(fields["신청/정원"]),
                "capacity_current": applied,
                "capacity_total": capacity_total,
                "waitlist_current": wait_count,
                "start": start,
                "end": end,
                "period_corrected": corrected,
                "historical_period_defect": historical_defect,
                "list_page": page,
            }
        )
    return _ListPage(page=page, total=total, last=last, rows=tuple(rows))


def _page_signature(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple(
            (
                row["identity"],
                row["title"],
                row["source_status"],
                row["source_method"],
                row["institution"],
                row["education_period"],
                row["apply_period"],
                row["capacity_source"],
            )
            for row in page.rows
        ),
    )


def _detail_fields(root: Any, identity: str) -> dict[str, str]:
    for table in root.select("table"):
        labels = {_clean(node.get_text(" ", strip=True)) for node in table.select("th")}
        if "강좌명" not in labels:
            continue
        fields: dict[str, str] = {}
        for tr in table.select("tr"):
            pending = ""
            for cell in tr.find_all(["th", "td"], recursive=False):
                if cell.name == "th":
                    pending = _clean(cell.get_text(" ", strip=True))
                    if not pending or pending in fields:
                        raise TaeanContractError(
                            f"course {identity}: duplicate/empty detail field label"
                        )
                elif pending:
                    fields[pending] = _clean(cell.get_text(" ", strip=True))
                    pending = ""
            if pending:
                raise TaeanContractError(f"course {identity}: detail field value missing")
        return fields
    raise TaeanContractError(f"course {identity}: detail table missing")


def _branch_name(institution: str, venue: str) -> str:
    if institution == "평생학습관":
        for token, branch in _LIFELONG_BRANCH_RULES:
            if token in venue:
                return branch
        return "태안군평생학습관"
    return _FIXED_BRANCHES.get(institution, "태안군 교육기관")


def _branch_code(branch: str) -> str:
    return "TAEAN_" + hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()


def _fee(value: str, identity: str) -> tuple[str, int]:
    raw = _clean(value)
    if not re.fullmatch(r"\d[\d,]*", raw):
        raise TaeanContractError(f"course {identity}: non-numeric audited fee")
    amount = int(raw.replace(",", ""))
    return ("무료" if amount == 0 else f"{amount:,}원"), amount


def _safe_external_url(value: str) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and not parsed.fragment
    )


def _control_contract(
    root: Any,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    identity = str(source["identity"])
    source_status = str(source["source_status"])
    source_method = str(source["source_method"])
    controls = root.select("a.writing[href]")
    if len(controls) > 1:
        raise TaeanContractError(f"course {identity}: multiple application controls")
    control = controls[0] if controls else None
    text = _clean(control.get_text(" ", strip=True) if control else "")
    href = _clean(control.get("href") if control else "")
    visible = control is not None
    actionable = False
    application_url = ""
    application_type = "INFO_ONLY"
    external_blocked = False
    insecure_external = False

    if source_status == "접수가능" and source_method == "자체접수":
        expected = taean_application_url(identity)
        absolute = urljoin(taean_detail_url(identity), href)
        if text != "수강신청" or absolute != expected:
            raise TaeanContractError(
                f"course {identity}: active internal application identity mismatch"
            )
        actionable = True
        application_url = expected
        application_type = "ONLINE_RESERVATION"
    elif source_status == "접수가능" and source_method == "기관접수":
        if text != "강좌신청" or not href:
            raise TaeanContractError(f"course {identity}: institution control missing")
        # The official page does not bind third-party forms to eduNo.  Even a
        # syntactically safe URL remains information-only until identity can be
        # proved without submitting/requesting an applicant form.
        external_blocked = True
        insecure_external = not _safe_external_url(href)
    elif source_status == "접수가능" and source_method == "방문접수":
        if control is not None:
            raise TaeanContractError(f"course {identity}: unexpected visit control")
        application_type = "OFFLINE_APPLY"
    elif source_status == "접수대기":
        if source_method != "자체접수" or text != "대기중" or href != "#":
            raise TaeanContractError(f"course {identity}: scheduled control mismatch")
    elif source_status == "접수마감" and source_method == "자체접수":
        if text != "접수마감" or href != "#":
            raise TaeanContractError(f"course {identity}: closed control mismatch")
    elif source_status == "접수마감" and source_method == "기관접수":
        if text != "강좌신청" or not href:
            raise TaeanContractError(f"course {identity}: closed institution control missing")
        external_blocked = True
        insecure_external = not _safe_external_url(href)
    elif source_status == "접수마감" and source_method == "방문접수":
        if control is not None:
            raise TaeanContractError(f"course {identity}: closed visit control changed")
    else:
        raise TaeanContractError(f"course {identity}: unaudited status/method branch")

    return {
        "visible": visible,
        "actionable": actionable,
        "application_url": application_url,
        "application_type": application_type,
        "external_blocked": external_blocked,
        "insecure_external": insecure_external,
    }


def _detail_row(
    source: Mapping[str, Any],
    soup: BeautifulSoup,
    cutoff: date,
) -> dict[str, Any]:
    identity = str(source["identity"])
    root = soup.select_one("#content")
    if root is None:
        raise TaeanContractError(f"course {identity}: detail root missing")
    fields = _detail_fields(root, identity)
    if not _REQUIRED_DETAIL_FIELDS <= set(fields):
        raise TaeanContractError(f"course {identity}: required detail fields missing")
    if _clean(fields["강좌명"]) != source["title"]:
        raise TaeanContractError(f"course {identity}: detail title identity drift")
    for label, source_key in (
        ("교육기관", "institution"),
        ("교육기간", "education_period"),
        ("접수기간", "apply_period"),
        ("교육시간", "schedule"),
    ):
        if _clean(fields[label]) != _clean(source[source_key]):
            raise TaeanContractError(f"course {identity}: detail {label} drift")
    try:
        detail_capacity = int(_clean(fields["정원"]).replace(",", ""))
    except ValueError as exc:
        raise TaeanContractError(f"course {identity}: invalid detail capacity") from exc
    if detail_capacity != source["capacity_total"]:
        raise TaeanContractError(f"course {identity}: detail capacity drift")
    start = source.get("start")
    end = source.get("end")
    if not isinstance(start, date) or not isinstance(end, date) or end < cutoff:
        raise TaeanContractError(f"course {identity}: non-current detail requested")
    if source.get("period_corrected"):
        correction = TAEAN_PERIOD_CORRECTIONS.get(identity)
        evidence = _clean(
            f"{fields.get('강좌명', '')} {fields.get('교육내용', '')}"
        )
        if correction is None or re.search(correction.evidence_regex, evidence) is None:
            raise TaeanContractError(f"course {identity}: date-correction evidence drift")
    venue = _safe_public_text(
        fields["교육장소"], label=f"course {identity} venue"
    )
    target = _safe_public_text(fields["교육대상"], label=f"course {identity} target")
    fee, fee_amount = _fee(fields["수강료"], identity)
    branch = _branch_name(str(source["institution"]), venue)
    controls = _control_contract(root, source)
    source_method = str(source["source_method"])
    method = {
        "자체접수": "온라인",
        "기관접수": "기관접수",
        "방문접수": "방문",
    }[source_method]
    raw_url = taean_detail_url(identity)
    status = _STATUS[str(source["source_status"])]
    category = str(source["category"]) or "기타"
    return {
        "provider": TAEAN_PROVIDER,
        "provider_course_id": f"{TAEAN_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": source["title"],
        "description": source["title"],
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": category,
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": controls["application_url"],
        "application_type": controls["application_type"],
        "application_method": method,
        "application_methods": [method],
        "reservation_available": bool(controls["actionable"] and status == "OPEN"),
        "status": status,
        "fee": fee,
        "fee_amount": fee_amount,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": str(source["apply_period"]),
        "schedule_raw": str(source["schedule"]),
        "capacity": f"{source['capacity_total']}명",
        "capacity_current": source["capacity_current"],
        "capacity_total": source["capacity_total"],
        "waitlist_current": source["waitlist_current"],
        "target": target,
        "venue": venue or branch,
        "venue_name": venue or branch,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": TAEAN_PARSER,
        "municipality_code": TAEAN_MUNICIPALITY_CODE,
        "municipality_full_name": TAEAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": source["list_page"],
            "source_category": str(source["category"]),
            "source_status": str(source["source_status"]),
            "source_method": source_method,
            "source_apply_period": str(source["apply_period"]),
            "source_education_period": str(source["education_period"]),
            "source_schedule": str(source["schedule"]),
            "source_institution": str(source["institution"]),
            "source_capacity": str(source["capacity_source"]),
            "period_corrected": bool(source["period_corrected"]),
            "detail_verified": True,
            "visible_application_control_present": bool(controls["visible"]),
            "actionable_application_control_present": bool(controls["actionable"]),
            "external_control_blocked": bool(controls["external_blocked"]),
            "insecure_external_control_blocked": bool(controls["insecure_external"]),
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden detail/PII key persisted")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw field allowlist exceeded")
    payload = repr({key: value for key, value in row.items() if key not in {"raw_url", "application_url"}})
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("contact data persisted")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = str(row["provider_course_id"])
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Asia/Seoul")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _load_list_page(
    page: int,
    timeout: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> _ListPage:
    return _parse_list_page(
        _response_soup(taean_list_url(page), timeout, session_factory, fetcher), page
    )


def collect_taean_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 300,
    detail_limit: int = 200,
    today: Optional[date | datetime | str] = None,
    max_workers: int = TAEAN_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "municipality_code": TAEAN_MUNICIPALITY_CODE,
        "owner_provider": TAEAN_PROVIDER,
        "candidate_id": TAEAN_CANDIDATE_ID,
        "canonical_url": TAEAN_CANONICAL_URL,
        "source_url": TAEAN_SOURCE_URL,
        "parser": TAEAN_PARSER,
        "list_requests": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "forbidden_application_endpoint_requests": 0,
        "configured_collection_error": "",
    }
    if not is_taean_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Taean owner"
        return [], TAEAN_PARSER, meta
    try:
        cutoff = _today(today)
        numeric = (timeout, max_pages, max_workers)
        if any(isinstance(item, bool) or int(item) < 1 for item in numeric):
            raise ValueError("invalid positive collection limit")
        if isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("invalid detail limit")
    except Exception as exc:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], TAEAN_PARSER, meta

    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    workers = min(int(max_workers), TAEAN_MAX_WORKERS)
    try:
        first = _load_list_page(1, int(timeout), factory, current_fetcher)
        meta["list_requests"] = 1
        required_requests = first.last + 3
        if required_requests > int(max_pages):
            raise TaeanContractError(
                f"max_pages {max_pages} below required {required_requests} list requests"
            )
        jobs = [
            ("data", page) for page in range(2, first.last + 1)
        ] + [("sentinel", first.last + 1), ("first", 1), ("last", first.last)]
        pages: dict[int, _ListPage] = {1: first}
        checks: dict[str, _ListPage] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _load_list_page,
                    page,
                    int(timeout),
                    factory,
                    current_fetcher,
                ): (kind, page)
                for kind, page in jobs
            }
            for future in as_completed(futures):
                kind, page = futures[future]
                parsed = future.result()
                meta["list_requests"] += 1
                if kind == "data":
                    pages[page] = parsed
                else:
                    checks[kind] = parsed
        expected_pages = set(range(1, first.last + 1))
        if set(pages) != expected_pages:
            raise TaeanContractError("one or more advertised data pages are missing")
        if any(page.total != first.total or page.last != first.last for page in pages.values()):
            raise TaeanContractError("catalogue total/last-page drift")
        for page_number, page in pages.items():
            expected_size = (
                TAEAN_PAGE_SIZE
                if page_number < first.last
                else first.total - (first.last - 1) * TAEAN_PAGE_SIZE
            )
            if first.total == 0:
                expected_size = 0
            if len(page.rows) != expected_size:
                raise TaeanContractError(
                    f"page {page_number}: expected {expected_size} rows, got {len(page.rows)}"
                )
        sentinel = checks.get("sentinel")
        if sentinel is None or sentinel.total != first.total or sentinel.rows:
            raise TaeanContractError("immediate empty sentinel missing")
        if checks.get("first") is None or _page_signature(checks["first"]) != _page_signature(first):
            raise TaeanContractError("first page stable-boundary recheck failed")
        if checks.get("last") is None or _page_signature(checks["last"]) != _page_signature(pages[first.last]):
            raise TaeanContractError("last page stable-boundary recheck failed")
        listed = [
            row for page in range(1, first.last + 1) for row in pages[page].rows
        ]
        identities = [str(row["identity"]) for row in listed]
        if len(listed) != first.total or len(set(identities)) != first.total:
            raise TaeanContractError("advertised total does not match unique course identities")
    except Exception as exc:
        message = f"{type(exc).__name__}: {_clean(exc)}"
        meta["configured_collection_error"] = message
        meta["source_cap_reached"] = "max_pages" in message
        return [], TAEAN_PARSER, meta

    publishable = [row for row in listed if row["identity"] not in TAEAN_NONEDUCATION_IDS]
    current = [
        row
        for row in publishable
        if isinstance(row.get("end"), date) and row["end"] >= cutoff
    ]
    if any(row.get("capacity_total") is None or not row.get("source_method") for row in current):
        meta["configured_collection_error"] = (
            "audited historical method/capacity defect became current"
        )
        return [], TAEAN_PARSER, meta
    source_status_counts = Counter(str(row["source_status"]) for row in listed)
    source_method_counts = Counter(str(row["source_method"]) or "미지정" for row in listed)
    source_institution_counts = Counter(str(row["institution"]) or "미지정" for row in listed)
    meta.update(
        {
            "cutoff": cutoff.isoformat(),
            "source_rows": len(listed),
            "source_total": first.total,
            "data_pages": first.last,
            "page_size": TAEAN_PAGE_SIZE,
            "page_sizes": [len(pages[page].rows) for page in range(1, first.last + 1)],
            "empty_sentinel_page": first.last + 1,
            "source_status_counts": dict(source_status_counts),
            "source_method_counts": dict(source_method_counts),
            "source_institution_counts": dict(source_institution_counts),
            "historical_period_defect_count": sum(
                bool(row["historical_period_defect"]) for row in listed
            ),
            "source_period_correction_count": sum(
                bool(row["period_corrected"]) for row in listed
            ),
            "noneducation_source_count": len(listed) - len(publishable),
            "current_source_count": len(current),
            "expired_or_unpublishable_count": len(listed) - len(current),
            "current_status_counts": dict(
                Counter(str(row["source_status"]) for row in current)
            ),
            "current_method_counts": dict(
                Counter(str(row["source_method"]) for row in current)
            ),
            "current_institution_counts": dict(
                Counter(str(row["institution"]) for row in current)
            ),
            "current_period_correction_count": sum(
                bool(row["period_corrected"]) for row in current
            ),
            "pagination_complete": True,
        }
    )
    if len(current) > int(detail_limit):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit {detail_limit} below required {len(current)}"
                ),
            }
        )
        return [], TAEAN_PARSER, meta

    rows: list[dict[str, Any]] = []
    detail_errors: list[str] = []

    def load_detail(source: Mapping[str, Any]) -> dict[str, Any]:
        identity = str(source["identity"])
        soup = _response_soup(
            taean_detail_url(identity), int(timeout), factory, current_fetcher
        )
        return _detail_row(source, soup, cutoff)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(load_detail, source): str(source["identity"]) for source in current
        }
        for future in as_completed(futures):
            identity = futures[future]
            try:
                rows.append(future.result())
                meta["detail_pages"] += 1
            except Exception as exc:
                detail_errors.append(
                    f"{identity}: {type(exc).__name__}: {_clean(exc)}"
                )
    if detail_errors:
        meta["configured_collection_error"] = "; ".join(detail_errors[:5])
        return [], TAEAN_PARSER, meta

    rows.sort(key=lambda row: (row["start_date"], int(row["raw_fields"]["identity"])))
    rows = list((dedupe_rows or _dedupe)(rows))
    privacy_errors = [error for row in rows for error in _privacy_errors(row)]
    expected_ids = {
        f"{TAEAN_PROVIDER}:{source['identity']}" for source in current
    }
    actual_ids = {str(row.get("provider_course_id")) for row in rows}
    if privacy_errors or len(rows) != len(current) or actual_ids != expected_ids:
        meta["configured_collection_error"] = (
            "; ".join(privacy_errors[:5])
            or "dedupe changed official current identity set"
        )
        return [], TAEAN_PARSER, meta

    active_rows = [row for row in rows if row["status"] == "OPEN"]
    meta.update(
        {
            "returned_count": len(rows),
            "status_counts": dict(Counter(str(row["status"]) for row in rows)),
            "branch_counts": dict(Counter(str(row["branch"]) for row in rows)),
            "visible_application_control_count": sum(
                bool(row["raw_fields"]["visible_application_control_present"])
                for row in rows
            ),
            "active_visible_application_control_count": sum(
                bool(row["raw_fields"]["visible_application_control_present"])
                for row in active_rows
            ),
            "actionable_application_control_count": sum(
                bool(row["raw_fields"]["actionable_application_control_present"])
                for row in rows
            ),
            "external_controls_blocked": sum(
                bool(row["raw_fields"]["external_control_blocked"]) for row in rows
            ),
            "active_external_controls_blocked": sum(
                bool(row["raw_fields"]["external_control_blocked"])
                for row in active_rows
            ),
            "insecure_external_controls_blocked": sum(
                bool(row["raw_fields"]["insecure_external_control_blocked"])
                for row in rows
            ),
            "open_offline_application_count": sum(
                row["status"] == "OPEN" and row["application_type"] == "OFFLINE_APPLY"
                for row in rows
            ),
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not rows,
        }
    )
    return rows, TAEAN_PARSER, meta


collect = collect_taean_education
