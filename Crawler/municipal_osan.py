"""Fail-closed collector for the complete Osan education-portal ledger.

The public list is a CSRF-protected HTML-fragment API.  A complete snapshot
therefore starts from the canonical search form, proves the six status
partitions, exhausts the four active partitions, checks empty sentinels and
stable boundaries, and then validates every current LFT detail.  Legacy DLV
instructor-catalogue rows are counted but never published or opened.

Detail descriptions, instructors, contacts, notices, attachments, conditions
and preparation notes can contain personal or free-form information.  Their
labels are validated, but their values are deliberately never read.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import html
import math
import re
import threading
import unicodedata
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


OSAN_PROVIDER = "MUNI_WWW_OSANEDU_GO_KR_8A50CEDC"
OSAN_CANDIDATE_ID = "MUNI_IR_C980368128AF"
OSAN_LEGACY_REDIRECT_CANDIDATE_ID = "MUNI_IR_EA9C2D144222"
OSAN_MUNICIPALITY_CODE = "4137000000"
OSAN_MUNICIPALITY_NAME = "경기도 오산시"
OSAN_HOST = "www.osanedu.go.kr"
OSAN_BASE_URL = f"https://{OSAN_HOST}"
OSAN_LIST_PATH = "/app/app0101/selectEdcView.do"
OSAN_API_PATH = "/app/app0101/selectEdcList.do"
OSAN_DETAIL_PATH = "/app/app0101/selectEdcDtls.do"
OSAN_CANONICAL_URL = f"{OSAN_BASE_URL}{OSAN_LIST_PATH}"
OSAN_PHYSICAL_VENUES: Mapping[str, Mapping[str, Any]] = {
    "오산시청": {
        "branch_code": "OSAN_VENUE_CITY_HALL",
        "address": "경기도 오산시 성호대로 141",
        "lat": 37.1497727,
        "lon": 127.0770233,
        "coordinate_source": "GOOGLE_PLACES_TEXT_SEARCH",
    },
    "온마을목공체험장": {
        "branch_code": "OSAN_VENUE_WOODWORK",
        "address": "경기도 오산시 오산천로 52",
        "lat": 37.1380974,
        "lon": 127.0648497,
        "coordinate_source": "NAVER_LOCAL_SEARCH",
    },
}
OSAN_API_URL = f"{OSAN_BASE_URL}{OSAN_API_PATH}"
OSAN_PAGE_SIZE = 10
OSAN_MAX_WORKERS = 10
OSAN_MAX_HTML_BYTES = 2 * 1024 * 1024
OSAN_PARSER = (
    "osan_todaye_csrf_six_status_complete_active_census_empty_sentinels_"
    "stable_edges_current_lft_detail_allowlist_multi_phase_application_"
    "audited_pseudo_course_exclusion_dlv_exclusion_pii_never_read"
)
OSAN_OWNERSHIP_SCOPE = "complete_osan_todaye_public_education_ledger"

OSAN_LEGACY_HOME_PROVIDER = "MUNI_WWW_OSANEDU_GO_KR_17F973C3"
OSAN_LEGACY_BUSINESS_PROVIDER = "MUNI_WWW_OSANEDU_GO_KR_81D29560"
OSAN_CANONICAL_HASH_PROVIDER_CANDIDATE = "MUNI_WWW_OSANEDU_GO_KR_516E2A49"
OSAN_CANONICAL_HASH_CANDIDATE_ID = "MUNI_IR_C980368128AF"
OSAN_SPORTS_PROVIDER_CANDIDATE = "MUNI_WWW_OSANSPORTS_OR_KR_8B5E3E9A"
OSAN_SPORTS_CANDIDATE_ID = "MUNI_IR_BB70970BA673"

OSAN_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    OSAN_PROVIDER: {
        "decision": "retain_incumbent_complete_owner_and_retarget_canonical",
        "candidate_id": OSAN_CANDIDATE_ID,
        "legacy_redirect_candidate_id": OSAN_LEGACY_REDIRECT_CANDIDATE_ID,
        "registered_url": (
            "https://www.osanedu.go.kr/app/app0101/selectBsnsView.do"
        ),
        "canonical_url": OSAN_CANONICAL_URL,
    },
    OSAN_LEGACY_HOME_PROVIDER: {
        "decision": "exclude_duplicate_homepage_alias",
        "candidate_id": "MUNI_IR_65DE57BCCE70",
        "url": "https://www.osanedu.go.kr/",
        "duplicate_of": OSAN_PROVIDER,
    },
    OSAN_LEGACY_BUSINESS_PROVIDER: {
        "decision": "exclude_duplicate_global_ledger_alias",
        "candidate_id": "MUNI_IR_9C57368C3103",
        "url": (
            "https://www.osanedu.go.kr/app/app0101/"
            "selectBsnsEdcView.do?p1=A00009&p2=C00007"
        ),
        "duplicate_of": OSAN_PROVIDER,
        "reason": "p1/p2 are ignored and the returned IDs equal the global ledger",
    },
    OSAN_CANONICAL_HASH_PROVIDER_CANDIDATE: {
        "decision": "do_not_create_new_owner_for_canonical_url_hash",
        "candidate_id": OSAN_CANONICAL_HASH_CANDIDATE_ID,
        "canonical_owner": OSAN_PROVIDER,
    },
    OSAN_SPORTS_PROVIDER_CANDIDATE: {
        "decision": "separate_owner_not_collected_here",
        "candidate_id": OSAN_SPORTS_CANDIDATE_ID,
        "url": "https://www.osansports.or.kr/fmcs/29",
        "reason": "different host, operator, account and application ledger",
        "audited_todaye_overlap": 0,
    },
    "OSAN_CITY_EDUCATION_DIRECTORY": {
        "decision": "exclude_non_ledger_directory",
        "url": "https://www.osan.go.kr/depart/contents.do?mId=0105000000",
    },
}

OSAN_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "unfiltered_total": 12651,
    "six_status_totals": {
        "01": 4,
        "02": 48,
        "03": 295,
        "04": 178,
        "05": 11026,
        "06": 1100,
    },
    "active_rows": 525,
    "active_lft_rows": 266,
    "excluded_dlv_rows": 259,
    "current_lft_rows": 264,
    "required_list_requests": 69,
    "required_detail_requests": 264,
    "complete_network_requests": 334,
}

OSAN_INSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("C00001", "(재)오산교육재단"),
    ("C00003", "건강생활지원센터"),
    ("C00005", "고현초 꿈키움도서관"),
    ("C00097", "기획예산과"),
    ("C00035", "기후환경정책과"),
    ("C00008", "꿈두레도서관"),
    ("C00010", "남촌동 행정복지센터"),
    ("C00011", "대원1동 행정복지센터"),
    ("C00157", "대원2동 행정복지센터"),
    ("C00118", "도시농업과"),
    ("C00179", "동 평생학습센터(남촌동)"),
    ("C00177", "동 평생학습센터(대원2동)"),
    ("C00178", "동 평생학습센터(중앙동)"),
    ("C00012", "무지개작은도서관"),
    ("C00013", "문화예술과"),
    ("C00258", "생활문화센터 오산이음라운지"),
    ("C00015", "세마동 행정복지센터"),
    ("C00016", "소리울도서관"),
    ("C00017", "신장1동 행정복지센터"),
    ("C00018", "쌍용예가 시민개방 도서관"),
    ("C00020", "양산도서관"),
    ("C00029", "어서오산 휴센터"),
    ("C00098", "오산AI코딩에듀랩"),
    ("C00021", "오산남부종합사회복지관"),
    ("C00059", "오산대학교"),
    ("C00079", "오산대학교 창업지원센터"),
    ("C00022", "오산대학교 평생교육원"),
    ("C00078", "오산시가족센터"),
    ("C00007", "오산시근로자종합복지관"),
    ("C00004", "오산시보건소"),
    ("C00038", "오산시청(TEST)"),
    ("C00032", "오산시평생학습관"),
    ("C00218", "오산시평생학습관(동 평생)"),
    ("C00077", "오산시하나울복지센터"),
    ("C00137", "오산예총"),
    ("C00197", "오산오색문화스포츠센터"),
    ("C00036", "오산장애인종합복지관"),
    ("C00058", "오산종합사회복지관"),
    ("C00027", "오산진로진학상담센터"),
    ("C00217", "오산화성궐리사"),
    ("C00037", "위생과"),
    ("C00159", "유엔군 초전기념관"),
    ("C00238", "주택과"),
    ("C00025", "중앙도서관"),
    ("C00026", "중앙동 행정복지센터"),
    ("C00080", "청년일자리지원센터 이루잡"),
    ("C00028", "청학도서관"),
    ("C00030", "초평도서관"),
    ("C00031", "초평동 행정복지센터"),
    ("C00057", "평생교육과"),
    ("C00006", "하천녹지과"),
    ("C00034", "햇살마루도서관"),
)
OSAN_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("01", "기초문해교육"),
    ("02", "학력보완교육"),
    ("03", "직업능력교육"),
    ("04", "문화예술교육"),
    ("05", "인문교양교육"),
    ("06", "시민참여교육"),
    ("07", "성인진로교육"),
)
OSAN_STATUSES: tuple[tuple[str, str], ...] = (
    ("01", "접수예정"),
    ("02", "접수중"),
    ("03", "접수마감"),
    ("04", "교육진행중"),
    ("05", "교육종료"),
    ("06", "폐강"),
)
OSAN_ACTIVE_STATUS_CODES = ("01", "02", "03", "04")
OSAN_APPLICATION_PERIOD_LABELS = (
    "1차신청기간",
    "2차신청기간",
    "3차신청기간",
)
OSAN_AUDITED_PSEUDO_COURSES: Mapping[str, Mapping[str, str]] = {
    "LFT0029288": {
        "title": "결제테스트",
        "branch": "중앙동 행정복지센터",
        "institution_id": "C00026",
        "business_id": "A00009",
        "source_status_code": "02",
        "source_status": "접수중",
        "start_date": "2026-08-01",
        "end_date": "2026-08-01",
    },
}

OSAN_DETAIL_REQUIRED_LABELS = frozenset(
    {
        "사업명", "기관", "모집공고일", "선정방식", "신청인원 및 정원",
        "강사", "교육방법", "교육기간", "교육일시", "교육대상", "교육장소",
        "수강료", "수료율", "준비물", "재료비", "문의",
    }
)
OSAN_DETAIL_OPTIONAL_LABELS = frozenset(
    {
        *OSAN_APPLICATION_PERIOD_LABELS,
        "공지사항",
        "참고자료",
        "신청조건",
    }
)
OSAN_DETAIL_SAFE_LABELS = frozenset(
    {
        "사업명", "기관", "모집공고일", *OSAN_APPLICATION_PERIOD_LABELS, "선정방식",
        "신청인원 및 정원", "교육방법", "교육기간", "교육일시", "교육대상",
        "교육장소", "수강료", "수료율", "재료비",
    }
)
OSAN_DETAIL_PRIVATE_LABELS = frozenset(
    {"강사", "준비물", "문의", "공지사항", "참고자료", "신청조건"}
)
_DETAIL_LABEL_ORDER = (
    "사업명", "기관", "모집공고일", *OSAN_APPLICATION_PERIOD_LABELS, "선정방식",
    "신청인원 및 정원", "강사", "교육방법", "교육기간", "교육일시",
    "교육대상", "교육장소", "수강료", "수료율", "준비물", "재료비",
    "공지사항", "문의", "신청조건", "참고자료",
)


class OsanEducationContractError(ValueError):
    """Raised when the audited public Osan contract changes."""


SessionFactory = Callable[[], Any]
Requester = Callable[
    [Any, str, str, int, Optional[dict[str, Any]], Mapping[str, str]], Any
]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_UUID_RE = re.compile(
    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$"
)
_CODE_RE = re.compile(r"^(?:LFT|DLV|LT)\d{7,10}$")
_LFT_CODE_RE = re.compile(r"^(?:LFT|LT)\d{7,10}$")
_DLV_CODE_RE = re.compile(r"^(?:DLV|LT)\d{7,10}$")
_BUSINESS_RE = re.compile(r"^A\d{5}$")
_INSTITUTION_RE = re.compile(r"^C\d{5}$")
_DATE_RE = re.compile(r"^(20\d{2})-(\d{2})-(\d{2})$")
_PERIOD_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s*~\s*(20\d{2})-(\d{2})-(\d{2})$"
)
_APPLICATION_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_GENERIC_TITLE_RE = re.compile(
    r"^(?:test|테스트\d*|교육|강좌|프로그램|제목\s*없음)$", re.IGNORECASE
)
_TEST_OR_NOTICE_TITLE_RE = re.compile(
    r"(?:test|테스트|sample|샘플|공지사항|^공지$)", re.IGNORECASE
)
_SCHEDULE_DURATION_RE = re.compile(r"\s*\(총[^()]*(?:시간|분)[^()]*\)\s*$")
_STATUS_CARD_VALUES: Mapping[str, frozenset[str]] = {
    "01": frozenset({"접수예정"}),
    "02": frozenset({"접수중", "대기접수"}),
    "03": frozenset({"접수마감"}),
    "04": frozenset({"교육진행중"}),
    "05": frozenset({"교육종료"}),
    "06": frozenset({"폐강"}),
}
_NORMAL_STATUS = {
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "대기접수": "WAITING",
    "접수마감": "CLOSED",
    "교육진행중": "CLOSED",
}
_AUDITED_STALE_PERIOD_ANOMALIES: Mapping[str, Mapping[str, str]] = {
    "LT00013792": {
        "title": "한글서예",
        "business_id": "A00009",
        "institution_id": "C00011",
        "branch": "대원1동 행정복지센터",
        "source_status": "교육진행중",
        "raw_period": "2022-01-03 ~ 2202-03-14",
        "effective_start": "2022-01-03",
        "effective_end": "2022-03-14",
    }
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True) if node is not None else "")


def _one(values: Sequence[Any], label: str) -> Any:
    if len(values) != 1:
        raise OsanEducationContractError(f"expected exactly one {label}")
    return values[0]


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _normal_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != OSAN_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != OSAN_LIST_PATH
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return OSAN_CANONICAL_URL


def is_osan_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == OSAN_PROVIDER
        and _normal_url(_target_value(target, "url")) == OSAN_CANONICAL_URL
    )


is_target = is_osan_education_target


def osan_detail_url(edc_code: Any) -> str:
    code = _clean(edc_code)
    if not _LFT_CODE_RE.fullmatch(code):
        raise OsanEducationContractError("invalid LFT detail identity")
    return f"{OSAN_BASE_URL}{OSAN_DETAIL_PATH}?" + urlencode(
        (("edcCode", code), ("edcTy", "LFT"))
    )


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _parse_date(value: Any) -> date:
    raw = _clean(value)
    match = _DATE_RE.fullmatch(raw)
    if match is None:
        raise OsanEducationContractError(f"invalid date {raw!r}")
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError as exc:
        raise OsanEducationContractError(f"invalid date {raw!r}") from exc


def _parse_period(value: Any) -> tuple[str, str, str]:
    raw = _clean(value)
    match = _PERIOD_RE.fullmatch(raw)
    if match is None:
        raise OsanEducationContractError(f"invalid education period {raw!r}")
    try:
        start = date(*(int(part) for part in match.groups()[:3]))
        end = date(*(int(part) for part in match.groups()[3:]))
    except ValueError as exc:
        raise OsanEducationContractError(f"invalid education period {raw!r}") from exc
    if end < start:
        raise OsanEducationContractError("reversed education period")
    period = f"{start.isoformat()} ~ {end.isoformat()}"
    return start.isoformat(), end.isoformat(), period


def _parse_application_period(value: Any) -> tuple[str, str, str]:
    match = _APPLICATION_RE.search(_clean(value))
    if match is None:
        raise OsanEducationContractError("invalid application period")
    start_date = _parse_date(match.group(1))
    end_date = _parse_date(match.group(3))
    start = f"{start_date.isoformat()} {match.group(2)}"
    end = f"{end_date.isoformat()} {match.group(4)}"
    if end < start:
        raise OsanEducationContractError("reversed application period")
    return start, end, f"{start} ~ {end}"


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return session


def _default_requester(
    session: Any,
    method: str,
    url: str,
    timeout: int,
    payload: Optional[dict[str, Any]],
    headers: Mapping[str, str],
) -> Any:
    return session.request(
        method,
        url,
        json=payload if method == "POST" else None,
        headers=dict(headers),
        timeout=timeout,
        allow_redirects=False,
    )


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _RequestBudget:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.count = 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.count >= self.maximum:
                raise OsanEducationContractError(
                    f"max_requests cap {self.maximum} exhausted"
                )
            self.count += 1


def _response_soup(response: Any, requested_url: str) -> BeautifulSoup:
    if isinstance(response, BeautifulSoup):
        return response
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise OsanEducationContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise OsanEducationContractError("redirected source response")
    final_url = _clean(getattr(response, "url", "")) or requested_url
    requested = urlparse(requested_url)
    final = urlparse(final_url)
    if (
        final.scheme.lower() != "https"
        or (final.hostname or "").rstrip(".").lower() != OSAN_HOST
        or final.port is not None
        or final.username
        or final.password
        or final.path != requested.path
        or final.params
        or final.fragment
        or parse_qs(final.query, keep_blank_values=True)
        != parse_qs(requested.query, keep_blank_values=True)
    ):
        raise OsanEducationContractError("source response URL changed scope")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if content is None or content == b"" or content == "":
        raise OsanEducationContractError("empty source response")
    size = len(content) if isinstance(content, bytes) else len(
        str(content).encode("utf-8")
    )
    if size > OSAN_MAX_HTML_BYTES:
        raise OsanEducationContractError("source HTML exceeds safety limit")
    return BeautifulSoup(content, "lxml")


def _select_options(soup: BeautifulSoup, selector: str) -> tuple[tuple[str, str], ...]:
    select = _one(soup.select(selector), selector)
    options = select.find_all("option", recursive=False)
    if not options or _clean(options[0].get("value")) or _text(options[0]) != "전체":
        raise OsanEducationContractError(f"{selector} all-option changed")
    return tuple(
        (_clean(option.get("value")), _text(option)) for option in options[1:]
    )


def _parse_bootstrap(soup: BeautifulSoup) -> str:
    if _text(soup.title) != "오산시 교육포털 | 교육신청 교육별 신청목록":
        raise OsanEducationContractError("bootstrap page title changed")
    form = _one(soup.select("form#searchFrm"), "search form")
    token_input = _one(form.select('input[name="CSRFToken"]'), "CSRF token")
    token = _clean(token_input.get("value"))
    if not _UUID_RE.fullmatch(token):
        raise OsanEducationContractError("bootstrap CSRF token changed")
    if _select_options(soup, "#insttList") != OSAN_INSTITUTIONS:
        raise OsanEducationContractError("institution registry changed")
    if _select_options(soup, "#edcLclasList") != OSAN_CATEGORIES:
        raise OsanEducationContractError("education-category registry changed")
    if _select_options(soup, "#eduSttusList") != OSAN_STATUSES:
        raise OsanEducationContractError("six-status registry changed")
    return token


def _card_header(card: Tag) -> tuple[str, str]:
    header = _one(
        card.select("div.flex.items-center.gap-2.mb-3"), "list-card header"
    )
    spans = header.find_all("span", recursive=False)
    if len(spans) != 2:
        raise OsanEducationContractError("list-card header shape changed")
    return _text(spans[0]), _text(spans[1])


def _mobile_fields(card: Tag, *, education_type: str) -> dict[str, str]:
    required_classes = {
        "flex", "flex-col", "gap-1", "text-sm", "text-gray-600", "xl:hidden"
    }
    blocks = [
        node
        for node in card.find_all("div")
        if required_classes.issubset(set(node.get("class") or ()))
    ]
    block = _one(blocks, "mobile public-field block")
    result: dict[str, str] = {}
    rows = block.find_all("div", recursive=False)
    expected_labels = (
        ("교육기간", "교육일시", "교육대상", "수강료")
        if education_type == "LFT"
        else ("강사명",)
    )
    if len(rows) != len(expected_labels):
        raise OsanEducationContractError("list-card public-field row count changed")
    for row, expected_label in zip(rows, expected_labels):
        spans = row.find_all("span", recursive=False)
        if len(spans) != 3 or _text(spans[0]) != expected_label or _text(spans[1]) != "|":
            raise OsanEducationContractError("list-card public-field shape changed")
        if education_type == "DLV":
            # The third span is an instructor value.  Its existence is enough;
            # reading it would cross the privacy boundary.
            continue
        result[expected_label] = _text(spans[2])
    return result


def _parse_card(
    card: Tag,
    *,
    status_code: Optional[str],
    institutions: Mapping[str, str],
) -> dict[str, Any]:
    if _clean(card.get("href")) != "javascript:void(0);":
        raise OsanEducationContractError("list-card action changed")
    education_type = _clean(card.get("data-ty"))
    if education_type not in {"LFT", "DLV"}:
        raise OsanEducationContractError("unsupported education type")
    identity = _clean(card.get("data-ecd"))
    business = _clean(card.get("data-bsns"))
    institution = _clean(card.get("data-instt"))
    if (
        not _CODE_RE.fullmatch(identity)
        or education_type == "LFT" and not _LFT_CODE_RE.fullmatch(identity)
        or education_type == "DLV" and not _DLV_CODE_RE.fullmatch(identity)
    ):
        raise OsanEducationContractError("invalid list identity")
    if not _BUSINESS_RE.fullmatch(business):
        raise OsanEducationContractError("invalid business identity")
    if not _INSTITUTION_RE.fullmatch(institution) or institution not in institutions:
        raise OsanEducationContractError("unknown institution identity")
    raw_status, branch = _card_header(card)
    if branch != institutions[institution]:
        raise OsanEducationContractError("institution code/name mismatch")
    if status_code is not None and raw_status not in _STATUS_CARD_VALUES[status_code]:
        raise OsanEducationContractError("card status differs from requested partition")
    if status_code is None and raw_status not in set().union(*_STATUS_CARD_VALUES.values()):
        raise OsanEducationContractError("unknown unfiltered card status")
    title = _text(_one(card.select("h3"), "list-card title"))
    if not title or _GENERIC_TITLE_RE.fullmatch(title):
        raise OsanEducationContractError("empty/generic course title")
    fields = _mobile_fields(card, education_type=education_type)
    if education_type == "DLV":
        if card.select(".st-num"):
            raise OsanEducationContractError("DLV instructor template gained capacity")
        return {
            "source_identity": identity,
            "education_type": education_type,
            "business_id": business,
            "institution_id": institution,
            "branch": branch,
            "source_status": raw_status,
            "source_status_code": status_code or "",
            "title": title,
            "list_labels": ("강사명",),
        }
    _one(card.select(".st-num"), "LFT capacity control")
    raw_period = _clean(fields["교육기간"])
    anomaly = _AUDITED_STALE_PERIOD_ANOMALIES.get(identity)
    if anomaly is None:
        start, end, period = _parse_period(raw_period)
    else:
        observed = {
            "title": title,
            "business_id": business,
            "institution_id": institution,
            "branch": branch,
            "source_status": raw_status,
            "raw_period": raw_period,
        }
        if any(observed[key] != anomaly[key] for key in observed):
            raise OsanEducationContractError("audited stale-period anomaly changed")
        start = anomaly["effective_start"]
        end = anomaly["effective_end"]
        period = f"{start} ~ {end}"
    result = {
        "source_identity": identity,
        "education_type": education_type,
        "business_id": business,
        "institution_id": institution,
        "branch": branch,
        "source_status": raw_status,
        "source_status_code": status_code or "",
        "title": title,
        "start_date": start,
        "end_date": end,
        "period": period,
        "schedule": fields["교육일시"],
        "target": fields["교육대상"],
        "list_fee": fields["수강료"],
        "list_labels": tuple(fields),
    }
    if anomaly is not None:
        result["audited_stale_period_anomaly"] = True
        result["source_period"] = raw_period
    return result


def _parse_list_fragment(
    soup: BeautifulSoup,
    *,
    status_code: Optional[str],
) -> tuple[list[dict[str, Any]], int]:
    total_input = _one(
        soup.select('input#totalRecordCount[name="totalRecordCount"]'),
        "totalRecordCount",
    )
    raw_total = _clean(total_input.get("value"))
    if not raw_total.isdigit():
        raise OsanEducationContractError("invalid totalRecordCount")
    total = int(raw_total)
    _one(soup.select("div.class_list"), "class_list")
    institutions = dict(OSAN_INSTITUTIONS)
    rows = [
        _parse_card(card, status_code=status_code, institutions=institutions)
        for card in soup.select("div.class_list > a.btn_dtls")
    ]
    if len(rows) > OSAN_PAGE_SIZE:
        raise OsanEducationContractError("API page exceeded audited page size")
    return rows, total


def _list_signature(rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (_clean(row.get("education_type")), _clean(row.get("source_identity")))
        for row in rows
    )


def _expected_detail_status(raw_status: str) -> str:
    if raw_status == "대기접수":
        return "접수중"
    return raw_status


def _safe_detail_values(
    root: Tag,
) -> tuple[dict[str, str], tuple[str, ...]]:
    view = _one(root.select(".class_detail_view"), "detail safe-field view")
    labels: list[str] = []
    values: dict[str, str] = {}
    for item in view.find_all("dl", recursive=False):
        dt = _one(item.find_all("dt", recursive=False), "detail label")
        label = _text(dt)
        if label in labels:
            raise OsanEducationContractError("duplicate detail label")
        if label not in OSAN_DETAIL_REQUIRED_LABELS | OSAN_DETAIL_OPTIONAL_LABELS:
            raise OsanEducationContractError(f"unknown detail label {label!r}")
        labels.append(label)
        if label in OSAN_DETAIL_SAFE_LABELS:
            dd = _one(
                item.find_all("dd", recursive=False), "detail value container"
            )
            values[label] = _text(dd)
        elif label not in OSAN_DETAIL_PRIVATE_LABELS:
            raise OsanEducationContractError("detail privacy boundary changed")
        elif len(item.find_all("dd", recursive=False)) > 1:
            raise OsanEducationContractError("private detail container shape changed")
        # Private/free-form dd values are intentionally not read.
    if not OSAN_DETAIL_REQUIRED_LABELS.issubset(labels):
        raise OsanEducationContractError("required detail labels changed")
    ranks = [_DETAIL_LABEL_ORDER.index(label) for label in labels]
    if ranks != sorted(ranks):
        raise OsanEducationContractError("detail label order changed")
    return values, tuple(labels)


def _hidden_form_value(form: Tag, name: str) -> str:
    node = _one(form.select(f'input[name="{name}"]'), f"form field {name}")
    return _clean(node.get("value"))


def _normalized_schedule(value: Any) -> str:
    return _clean(_SCHEDULE_DURATION_RE.sub("", _clean(value)))


def _physical_venue_location(value: Any) -> tuple[str, Mapping[str, Any]] | None:
    venue = _clean(value)
    for name, location in OSAN_PHYSICAL_VENUES.items():
        road_and_number = " ".join(_clean(location.get("address")).split()[-2:])
        if name in venue and road_and_number in venue:
            return name, location
    return None


def _parse_detail(
    soup: BeautifulSoup,
    *,
    source: Mapping[str, Any],
    detail_url: str,
) -> dict[str, Any]:
    if _text(soup.title) != "오산시 교육포털 | 교육신청 평생교육 교육상세정보":
        raise OsanEducationContractError("detail page title changed")
    root = _one(
        soup.select("#content .class_detail_wrap"), "detail contract root"
    )
    _one(root.select(".detail_content.ct_info > .detail_txt"), "private detail text")
    values, labels = _safe_detail_values(root)
    card = _one(root.select(".class_list > .class_box"), "detail identity card")
    detail_title = _text(_one(card.select("b"), "detail title"))
    detail_status = _text(_one(card.select(".class_state"), "detail status"))
    if detail_title != _clean(source.get("title")):
        raise OsanEducationContractError("list/detail title mismatch")
    if detail_status != _expected_detail_status(_clean(source.get("source_status"))):
        raise OsanEducationContractError("list/detail status mismatch")
    if values.get("기관") != _clean(source.get("branch")):
        raise OsanEducationContractError("list/detail institution mismatch")
    start, end, period = _parse_period(values.get("교육기간"))
    if (start, end, period) != (
        source.get("start_date"), source.get("end_date"), source.get("period")
    ):
        raise OsanEducationContractError("list/detail education period mismatch")
    if _normalized_schedule(values.get("교육일시")) != _normalized_schedule(
        source.get("schedule")
    ):
        raise OsanEducationContractError("list/detail schedule mismatch")
    _parse_date(values.get("모집공고일"))

    status_code = _clean(source.get("source_status_code"))
    application_labels = [
        label for label in OSAN_APPLICATION_PERIOD_LABELS if label in labels
    ]
    if application_labels != list(
        OSAN_APPLICATION_PERIOD_LABELS[: len(application_labels)]
    ):
        raise OsanEducationContractError("application period phases are not contiguous")
    has_application_period = bool(application_labels)
    if has_application_period != (status_code in {"01", "02"}):
        raise OsanEducationContractError("application-period/status contract changed")
    apply_start = apply_end = application_period = ""
    if has_application_period:
        windows = [
            _parse_application_period(values[label])
            for label in application_labels
        ]
        for previous, current in zip(windows, windows[1:]):
            if current[0] < previous[0] or current[1] < previous[1]:
                raise OsanEducationContractError(
                    "application period phase order is reversed"
                )
        apply_start = windows[0][0]
        apply_end = windows[-1][1]
        application_period = f"{apply_start} ~ {apply_end}"

    form = _one(soup.select("form#frmReqst"), "application identity form")
    if (
        _hidden_form_value(form, "edcCode") != source.get("source_identity")
        or _hidden_form_value(form, "edcTy") != "LFT"
        or _hidden_form_value(form, "unitBsnsId") != source.get("business_id")
        or _hidden_form_value(form, "insttId") != source.get("institution_id")
        or _hidden_form_value(form, "insttNm") != source.get("branch")
    ):
        raise OsanEducationContractError("detail hidden identity mismatch")

    controls = root.select("a#btn_reqst")
    should_apply = status_code == "02"
    if len(controls) != (1 if should_apply else 0):
        raise OsanEducationContractError("application control/status mismatch")
    if should_apply:
        control = controls[0]
        if (
            "reserve_btn" not in (control.get("class") or ())
            or _clean(control.get("href")) != "javascript:fn_reqst();"
            or _text(control) != "신청하기"
        ):
            raise OsanEducationContractError("application control changed")
    endpoint = "/app/app0102/selectEdcReqstLft.do"
    if not any(
        endpoint in (script.string or "") for script in soup.find_all("script")
    ):
        raise OsanEducationContractError("application endpoint script changed")

    raw_status = _clean(source.get("source_status"))
    normalized_status = _NORMAL_STATUS.get(raw_status)
    if normalized_status is None:
        raise OsanEducationContractError("unsupported current status")
    identity = _clean(source.get("source_identity"))
    schedule = _normalized_schedule(values.get("교육일시", "")) or "시간 별도 안내"
    target = values.get("교육대상", "") or "대상 별도 안내"
    venue = values.get("교육장소", "") or _clean(source.get("branch"))
    source_fee = _clean(values.get("수강료", ""))
    fee = source_fee if source_fee not in {"", "-"} else "요금 별도 안내"
    raw_fields = {
        "source_identity": identity,
        "education_type": "LFT",
        "business_id": _clean(source.get("business_id")),
        "institution_id": _clean(source.get("institution_id")),
        "source_status_code": status_code,
        "source_status": raw_status,
        "business_name": values.get("사업명", ""),
        "announcement_date": values.get("모집공고일", ""),
        "primary_application_period": values.get("1차신청기간", ""),
        "secondary_application_period": values.get("2차신청기간", ""),
        "tertiary_application_period": values.get("3차신청기간", ""),
        "application_period_phases": {
            label: values[label] for label in application_labels
        },
        "schedule_fallback_used": schedule == "시간 별도 안내",
        "target_fallback_used": target == "대상 별도 안내",
        "venue_fallback_used": not bool(values.get("교육장소", "")),
        "source_fee": source_fee,
        "fee_fallback_used": fee == "요금 별도 안내",
        "selection_method": values.get("선정방식", ""),
        "capacity_text": values.get("신청인원 및 정원", ""),
        "delivery_method": values.get("교육방법", ""),
        "completion_rate": values.get("수료율", ""),
        "material_fee": values.get("재료비", ""),
        "detail_labels": list(labels),
    }
    row = {
        "provider": OSAN_PROVIDER,
        "provider_course_id": f"{OSAN_PROVIDER}:education:LFT:{identity}",
        "title": detail_title,
        "description": detail_title,
        "category": "교육",
        "municipality_code": OSAN_MUNICIPALITY_CODE,
        "municipality_name": OSAN_MUNICIPALITY_NAME,
        "branch": _clean(source.get("branch")),
        "branch_code": _clean(source.get("institution_id")),
        "status": normalized_status,
        "start_date": start,
        "end_date": end,
        "period": period,
        "application_period": application_period,
        "apply_period": application_period,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule": schedule,
        "schedule_raw": schedule,
        "target": target,
        "fee": fee,
        "venue": venue,
        "venue_name": venue,
        "source_url": OSAN_CANONICAL_URL,
        "raw_url": detail_url,
        "application_url": detail_url if should_apply else "",
        "reservation_available": should_apply,
        "raw_fields": raw_fields,
    }
    physical_venue = _physical_venue_location(venue)
    if physical_venue:
        branch, location = physical_venue
        address = _clean(location.get("address"))
        row.update(
            {
                "branch": branch,
                "branch_code": _clean(location.get("branch_code")),
                "preserve_branch": True,
                "address": address,
                "venue_address": address,
                "branch_address_source": "OFFICIAL_OSAN_EDUCATION_DETAIL",
                "branch_lat": location.get("lat"),
                "branch_lon": location.get("lon"),
                "branch_coordinate_source": _clean(
                    location.get("coordinate_source")
                ),
                "branch_location_confidence": 100,
                "branch_location_verified": True,
                "branch_location_query": detail_url,
            }
        )
        raw_fields["source_institution_branch"] = _clean(source.get("branch"))
        raw_fields["physical_venue_promoted"] = True
    return row


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    title = unicodedata.normalize("NFKC", _clean(row.get("title"))).casefold()
    return (
        title,
        _clean(row.get("branch_code")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    )


def _validate_safe_row(row: Mapping[str, Any]) -> None:
    forbidden_key_parts = (
        "instructor", "teacher", "contact", "phone", "email", "description",
        "notice", "attachment", "preparation", "condition", "문의", "강사",
        "준비물", "공지사항", "참고자료", "신청조건",
    )

    def walk(value: Any, key: str = "") -> None:
        lowered = key.casefold()
        if any(part.casefold() in lowered for part in forbidden_key_parts):
            raise OsanEducationContractError("private/free-form key persisted")
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                walk(child, str(child_key))
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child, key)
        elif isinstance(value, str) and (
            _PHONE_RE.search(value) or _EMAIL_RE.search(value)
        ):
            raise OsanEducationContractError("PII-like value persisted")

    if _clean(row.get("description")) != _clean(row.get("title")):
        raise OsanEducationContractError("description must remain title-only")
    for key, value in row.items():
        if key != "description":
            walk(value, str(key))


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "provider": OSAN_PROVIDER,
        "candidate_id": OSAN_CANDIDATE_ID,
        "legacy_redirect_candidate_id": OSAN_LEGACY_REDIRECT_CANDIDATE_ID,
        "canonical_url": OSAN_CANONICAL_URL,
        "municipality_code": OSAN_MUNICIPALITY_CODE,
        "municipality_name": OSAN_MUNICIPALITY_NAME,
        "ownership_scope": OSAN_OWNERSHIP_SCOPE,
        "owner_boundary_audit": {
            key: dict(value) for key, value in OSAN_OWNER_BOUNDARY_AUDIT.items()
        },
        "discovery_audit": dict(OSAN_DISCOVERY_AUDIT),
        "checked_at": "",
        "bootstrap_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "pages": 0,
        "detail_pages": 0,
        "network_requests": 0,
        "sessions_created": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "six_status_totals": {},
        "status_page_sizes": {},
        "unfiltered_total": 0,
        "active_source_count": 0,
        "active_lft_count": 0,
        "dlv_excluded_count": 0,
        "stale_lft_count": 0,
        "current_lft_count": 0,
        "test_or_notice_row_count": 0,
        "private_values_read": 0,
        "application_control_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
    }


def collect_osan_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 400,
    max_requests: int = 500,
    *,
    today: Optional[date | datetime | str] = None,
    requester: Optional[Requester] = None,
    session_factory: Optional[SessionFactory] = None,
    max_workers: int = OSAN_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future snapshot of the Osan ledger."""

    meta = _base_meta()
    if not is_osan_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact canonical Osan education owner"
        )
        return [], OSAN_PARSER, meta
    try:
        limits = (timeout, max_pages, detail_limit, max_requests, max_workers)
        if any(isinstance(value, bool) for value in limits):
            raise ValueError("boolean limits are invalid")
        request_timeout = max(1, int(timeout))
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        request_cap = max(0, int(max_requests))
        workers = min(max(1, int(max_workers)), OSAN_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], OSAN_PARSER, meta
    if page_cap < 7 or request_cap < 8:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "caps cannot inspect bootstrap, global total and six status partitions"
        )
        return [], OSAN_PARSER, meta

    request = requester or _default_requester
    factory = session_factory or _default_session_factory
    budget = _RequestBudget(request_cap)
    main_session: Any = None
    phase_counter_lock = threading.Lock()

    def fetch_soup(
        session: Any,
        method: str,
        url: str,
        *,
        payload: Optional[dict[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        phase: str,
    ) -> BeautifulSoup:
        budget.take()
        response = request(
            session,
            method,
            url,
            request_timeout,
            payload,
            headers or {},
        )
        soup = _response_soup(response, url)
        with phase_counter_lock:
            if phase == "bootstrap":
                meta["bootstrap_requests"] += 1
            elif phase == "list":
                meta["list_requests"] += 1
            elif phase == "detail":
                meta["detail_requests"] += 1
            meta["network_requests"] = budget.count
        return soup

    try:
        main_session = factory()
        meta["sessions_created"] = 1
        bootstrap = fetch_soup(
            main_session, "GET", OSAN_CANONICAL_URL, phase="bootstrap"
        )
        token = _parse_bootstrap(bootstrap)
        api_headers = {
            "Accept": "text/html, */*; q=0.01",
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": OSAN_CANONICAL_URL,
            "X-CSRF-TOKEN": token,
            "X-Requested-With": "XMLHttpRequest",
            "ajaxAt": "Y",
        }

        def api_page(status_code: Optional[str], page: int) -> tuple[list[dict[str, Any]], int]:
            req_data: dict[str, Any] = {
                "CSRFToken": token,
                "pageIndex": str(page),
            }
            if status_code is not None:
                req_data["eduSttusList"] = status_code
            soup = fetch_soup(
                main_session,
                "POST",
                OSAN_API_URL,
                payload={"reqData": req_data},
                headers=api_headers,
                phase="list",
            )
            return _parse_list_fragment(soup, status_code=status_code)

        global_rows, global_total = api_page(None, 1)
        if len(global_rows) != min(OSAN_PAGE_SIZE, global_total):
            raise OsanEducationContractError("unfiltered first-page size changed")

        first_pages: dict[str, list[dict[str, Any]]] = {}
        status_totals: dict[str, int] = {}
        status_last_pages: dict[str, int] = {}
        for status_code, _status_name in OSAN_STATUSES:
            rows, total = api_page(status_code, 1)
            if len(rows) != min(OSAN_PAGE_SIZE, total):
                raise OsanEducationContractError(
                    f"status {status_code} first-page size changed"
                )
            first_pages[status_code] = rows
            status_totals[status_code] = total
            status_last_pages[status_code] = max(
                1, math.ceil(total / OSAN_PAGE_SIZE)
            )
        if sum(status_totals.values()) != global_total:
            raise OsanEducationContractError(
                "six status totals do not partition the unfiltered ledger"
            )

        required_list_requests = (
            1
            + len(OSAN_STATUSES)
            + sum(status_last_pages[code] - 1 for code in OSAN_ACTIVE_STATUS_CODES)
            + len(OSAN_ACTIVE_STATUS_CODES)
            + 2 * len(OSAN_ACTIVE_STATUS_CODES)
        )
        if required_list_requests > page_cap:
            raise OsanEducationContractError(
                f"max_pages cap {page_cap} is below {required_list_requests} "
                "mandatory list requests"
            )
        if 1 + required_list_requests > request_cap:
            raise OsanEducationContractError(
                f"max_requests cap {request_cap} is below the "
                f"{1 + required_list_requests} request census floor"
            )

        active_pages: dict[str, dict[int, list[dict[str, Any]]]] = {}
        status_page_sizes: dict[str, list[int]] = {}
        for status_code in OSAN_ACTIVE_STATUS_CODES:
            total = status_totals[status_code]
            last_page = status_last_pages[status_code]
            pages: dict[int, list[dict[str, Any]]] = {
                1: first_pages[status_code]
            }
            for page in range(2, last_page + 1):
                rows, found_total = api_page(status_code, page)
                if found_total != total:
                    raise OsanEducationContractError(
                        f"status {status_code} total changed during pagination"
                    )
                expected_size = (
                    OSAN_PAGE_SIZE
                    if page < last_page
                    else total - OSAN_PAGE_SIZE * (last_page - 1)
                )
                if len(rows) != expected_size:
                    raise OsanEducationContractError(
                        f"status {status_code} page {page} size changed"
                    )
                pages[page] = rows
            sentinel_rows, sentinel_total = api_page(status_code, last_page + 1)
            meta["sentinel_requests"] += 1
            if sentinel_rows or sentinel_total != 0:
                raise OsanEducationContractError(
                    f"status {status_code} empty sentinel changed"
                )

            # First and last are deliberately requested separately even when
            # a one-page partition makes them the same page.
            first_again, first_total = api_page(status_code, 1)
            last_again, last_total = api_page(status_code, last_page)
            meta["stability_rechecks"] += 2
            if (
                first_total != total
                or last_total != total
                or _list_signature(first_again) != _list_signature(pages[1])
                or _list_signature(last_again) != _list_signature(pages[last_page])
            ):
                raise OsanEducationContractError(
                    f"status {status_code} boundary changed during census"
                )
            active_pages[status_code] = pages
            status_page_sizes[status_code] = [
                len(pages[page]) for page in range(1, last_page + 1)
            ]

        if meta["list_requests"] != required_list_requests:
            raise OsanEducationContractError("list request accounting changed")
        active_rows = [
            row
            for status_code in OSAN_ACTIVE_STATUS_CODES
            for page in range(1, status_last_pages[status_code] + 1)
            for row in active_pages[status_code][page]
        ]
        if len(active_rows) != sum(
            status_totals[code] for code in OSAN_ACTIVE_STATUS_CODES
        ):
            raise OsanEducationContractError("active partition row total changed")
        active_keys = [
            (_clean(row.get("education_type")), _clean(row.get("source_identity")))
            for row in active_rows
        ]
        if len(active_keys) != len(set(active_keys)):
            raise OsanEducationContractError("duplicate identity across active partitions")

        dlv_rows = [row for row in active_rows if row["education_type"] == "DLV"]
        lft_rows = [row for row in active_rows if row["education_type"] == "LFT"]
        for row in dlv_rows:
            if (
                row.get("source_status_code") != "03"
                or row.get("source_status") != "접수마감"
                or row.get("business_id") != "A00001"
                or row.get("institution_id") != "C00032"
                or row.get("branch") != "오산시평생학습관"
                or tuple(row.get("list_labels") or ()) != ("강사명",)
                or any(key in row for key in ("start_date", "end_date", "period"))
            ):
                raise OsanEducationContractError(
                    "DLV legacy instructor-catalogue exclusion contract changed"
                )

        current_rows = [
            row
            for row in lft_rows
            if _parse_date(row.get("end_date")) >= cutoff
        ]
        stale_lft_count = len(lft_rows) - len(current_rows)
        test_or_notice_rows = [
            row
            for row in current_rows
            if row.get("branch") == "오산시청(TEST)"
            or _TEST_OR_NOTICE_TITLE_RE.search(_clean(row.get("title")))
        ]
        unexpected_pseudo_rows = []
        for row in test_or_notice_rows:
            identity = _clean(row.get("source_identity"))
            expected = OSAN_AUDITED_PSEUDO_COURSES.get(identity)
            if expected is None or any(
                _clean(row.get(key)) != value for key, value in expected.items()
            ):
                unexpected_pseudo_rows.append(row)
        if unexpected_pseudo_rows:
            raise OsanEducationContractError(
                "current ledger contains test/sample/notice pseudo-courses"
            )
        excluded_pseudo_ids = {
            _clean(row.get("source_identity")) for row in test_or_notice_rows
        }
        current_rows = [
            row
            for row in current_rows
            if _clean(row.get("source_identity")) not in excluded_pseudo_ids
        ]
        current_identity = [row["source_identity"] for row in current_rows]
        if len(current_identity) != len(set(current_identity)):
            raise OsanEducationContractError("duplicate current LFT identity")
        rough_semantic = [
            (
                unicodedata.normalize("NFKC", _clean(row.get("title"))).casefold(),
                _clean(row.get("institution_id")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
            )
            for row in current_rows
        ]
        if len(rough_semantic) != len(set(rough_semantic)):
            raise OsanEducationContractError("duplicate current semantic course")
        if len(current_rows) > detail_cap:
            raise OsanEducationContractError(
                f"detail_limit cap {detail_cap} is below "
                f"{len(current_rows)} mandatory current details"
            )
        expected_requests = 1 + required_list_requests + len(current_rows)
        if expected_requests > request_cap:
            raise OsanEducationContractError(
                f"max_requests cap {request_cap} cannot finish "
                f"{expected_requests} mandatory requests"
            )

        detail_sessions: list[Any] = []
        detail_session_lock = threading.Lock()
        local = threading.local()

        def thread_session() -> Any:
            current = getattr(local, "session", None)
            if current is None:
                current = factory()
                local.session = current
                with detail_session_lock:
                    detail_sessions.append(current)
            return current

        def detail_one(source: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
            identity = _clean(source.get("source_identity"))
            url = osan_detail_url(identity)
            soup = fetch_soup(
                thread_session(),
                "GET",
                url,
                headers={"Referer": OSAN_CANONICAL_URL},
                phase="detail",
            )
            return identity, _parse_detail(soup, source=source, detail_url=url)

        details: dict[str, dict[str, Any]] = {}
        try:
            with ThreadPoolExecutor(
                max_workers=min(workers, max(1, len(current_rows)))
            ) as executor:
                futures = {
                    executor.submit(detail_one, source): source["source_identity"]
                    for source in current_rows
                }
                errors: list[str] = []
                for future in as_completed(futures):
                    identity = futures[future]
                    try:
                        found_identity, row = future.result()
                        if found_identity in details:
                            raise OsanEducationContractError(
                                "duplicate detail identity"
                            )
                        details[found_identity] = row
                    except Exception as exc:
                        errors.append(
                            f"{identity}: {type(exc).__name__}: {_clean(exc)}"
                        )
                if errors:
                    raise OsanEducationContractError("; ".join(errors))
        finally:
            for current in detail_sessions:
                _close_quietly(current)
            meta["sessions_created"] += len(detail_sessions)
        if len(details) != len(current_rows):
            raise OsanEducationContractError("incomplete current detail set")
        rows = [details[source["source_identity"]] for source in current_rows]
        for row in rows:
            _validate_safe_row(row)
        semantic_keys = [_semantic_key(row) for row in rows]
        if len(semantic_keys) != len(set(semantic_keys)):
            raise OsanEducationContractError("detail semantic identity collision")

        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(rows))
        before_ids = [_clean(row.get("provider_course_id")) for row in rows]
        after_ids = [_clean(row.get("provider_course_id")) for row in result]
        if (
            len(result) != len(rows)
            or Counter(before_ids) != Counter(after_ids)
            or len(after_ids) != len(set(after_ids))
        ):
            raise OsanEducationContractError("dedupe changed complete identity set")

        detail_variants = Counter(
            tuple(row.get("raw_fields", {}).get("detail_labels", ()))
            for row in result
        )
        meta.update(
            {
                "checked_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(
                    timespec="seconds"
                ),
                "network_requests": budget.count,
                "required_list_requests": required_list_requests,
                "required_detail_requests": len(current_rows),
                "expected_complete_network_requests": expected_requests,
                "pages": sum(
                    status_last_pages[code]
                    for code in OSAN_ACTIVE_STATUS_CODES
                ),
                "detail_pages": len(result),
                "six_status_totals": status_totals,
                "status_page_sizes": status_page_sizes,
                "unfiltered_total": global_total,
                "active_source_count": len(active_rows),
                "active_lft_count": len(lft_rows),
                "dlv_excluded_count": len(dlv_rows),
                "dlv_exclusion_reason": "legacy_delivery_instructor_catalogue",
                "stale_lft_count": stale_lft_count,
                "audited_stale_period_anomaly_count": sum(
                    bool(row.get("audited_stale_period_anomaly"))
                    for row in lft_rows
                ),
                "current_lft_count": len(current_rows),
                "test_or_notice_row_count": len(test_or_notice_rows),
                "audited_pseudo_excluded_ids": sorted(excluded_pseudo_ids),
                "returned_count": len(result),
                "private_values_read": 0,
                "private_labels_never_read": sorted(OSAN_DETAIL_PRIVATE_LABELS),
                "application_control_count": sum(
                    bool(row.get("reservation_available")) for row in result
                ),
                "raw_status_counts": dict(
                    Counter(
                        row.get("raw_fields", {}).get("source_status")
                        for row in result
                    )
                ),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "business_counts": dict(
                    Counter(
                        _clean(row.get("raw_fields", {}).get("business_id"))
                        for row in result
                    )
                ),
                "detail_schema_variants": {
                    " | ".join(labels): count
                    for labels, count in detail_variants.items()
                },
                "pagination_detected": any(
                    status_last_pages[code] > 1
                    for code in OSAN_ACTIVE_STATUS_CODES
                ),
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "source_cap_reached": False,
                "no_current_data": not result,
                "no_current_reason": (
                    "all active LFT rows ended before the crawl date"
                    if not result
                    else ""
                ),
                "configured_collection_error": "",
            }
        )
        if budget.count != expected_requests:
            raise OsanEducationContractError("complete request accounting changed")
        return result, OSAN_PARSER, meta
    except Exception as exc:
        message = _clean(exc) or exc.__class__.__name__
        meta["network_requests"] = budget.count
        meta["checked_at"] = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(
            timespec="seconds"
        )
        meta["source_cap_reached"] = "cap" in message or "limit" in message
        meta["configured_collection_error"] = message
        meta["snapshot_complete"] = False
        return [], OSAN_PARSER, meta
    finally:
        _close_quietly(main_session)


collect_courses = collect_osan_education
osan_session_factory = _default_session_factory


__all__ = [
    "OSAN_PROVIDER",
    "OSAN_CANDIDATE_ID",
    "OSAN_LEGACY_REDIRECT_CANDIDATE_ID",
    "OSAN_MUNICIPALITY_CODE",
    "OSAN_MUNICIPALITY_NAME",
    "OSAN_CANONICAL_URL",
    "OSAN_API_URL",
    "OSAN_PARSER",
    "OSAN_OWNERSHIP_SCOPE",
    "OSAN_OWNER_BOUNDARY_AUDIT",
    "OSAN_DISCOVERY_AUDIT",
    "OSAN_INSTITUTIONS",
    "OSAN_CATEGORIES",
    "OSAN_STATUSES",
    "OSAN_DETAIL_REQUIRED_LABELS",
    "OSAN_DETAIL_OPTIONAL_LABELS",
    "OSAN_DETAIL_SAFE_LABELS",
    "OSAN_DETAIL_PRIVATE_LABELS",
    "OsanEducationContractError",
    "is_osan_education_target",
    "is_target",
    "osan_detail_url",
    "osan_session_factory",
    "collect_osan_education",
    "collect_courses",
]
