"""Fail-closed collector for Gurye County's official course ledger.

The registered Gurye County target is stale: its former course URL is now a
bus-timetable page.  The current County-owned catalogue is the ``/yeyak``
education and culture ledger on ``www.gurye.go.kr``.  It is distinct from the
independently owned Gurye Family Center and JNE Gurye Library catalogues.

The collector walks consecutive pages through a stable empty sentinel,
rechecks the first, last, and sentinel pages, filters by course end date, and
verifies every retained row against its identity-bearing detail page.  Contact
data, free-form bodies, attachments, session identifiers, and application
payloads are intentionally discarded.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


GURYE_PROVIDER = "MUNI_WWW_GURYE_GO_KR_6C92FD6B"
GURYE_STALE_PROVIDER = "MUNI_WWW_GURYE_GO_KR_B1245CE2"
GURYE_STALE_CANDIDATE_ID = "MUNI_IR_C36C248E3256"
GURYE_MUNICIPALITY_CODE = "1273000000"
GURYE_MUNICIPALITY_NAME = "전남광주통합특별시 구례군"
GURYE_OWNER_BRANCH = "구례군 교육·문화 프로그램"

GURYE_HOST = "www.gurye.go.kr"
GURYE_LIST_PATH = "/yeyak/YeyakList.do"
GURYE_DETAIL_PATH = "/yeyak/YeyakView.do"
GURYE_MENU_NO = "119001001000"
GURYE_LIST_URL = (
    f"https://{GURYE_HOST}{GURYE_LIST_PATH}?{urlencode({'searchTrainingCaCode': '', 'menuNo': GURYE_MENU_NO})}"
)
GURYE_STALE_URL = "https://www.gurye.go.kr/kr/subPage.do?menuNo=117006001002"
GURYE_FAMILY_CENTER_URL = "https://gurye.familynet.or.kr/center/lay1/program/S295T322C451/recruitReceipt/list.do"
GURYE_JNE_READING_URL = "https://grlib.jne.go.kr/lecture.es?mid=a70202010000"
GURYE_JNE_LIFELONG_URL = "https://grlib.jne.go.kr/lecture.es?mid=a70402000000"
GURYE_AGRICULTURAL_ALIAS_URL = (
    "https://www.gurye.go.kr/yeyak/YeyakList.do?searchTrainingCaCode=OMA005&menuNo=144008000000"
)
GURYE_HEALTH_ALIAS_URL = "https://www.gurye.go.kr/yeyak/YeyakList.do?searchTrainingCaCode=OMA001&menuNo=136005000000"

GURYE_PAGE_SIZE = 10
GURYE_MAX_PAGES = 40
GURYE_MAX_WORKERS = 6
GURYE_MAX_HTML_BYTES = 3_000_000
GURYE_PARSER = (
    "gurye_county_yeyak_complete_catalogue+stable_empty_sentinel+"
    "current_detail_contract+facility_branch_split+pii_allowlist"
)

GURYE_CATEGORY_CODES: Mapping[str, str] = {
    "": "전체",
    "OMA010": "평생교육과",
    "OMA004": "정보화교육",
    "OMA002": "매천도서관",
    "OMA005": "농업기술센터",
    "OMA001": "보건의료원",
    "OMA008": "목재문화체험장",
    "OMA009": "지리산정원관리사업소",
    "OMA007": "기타",
}

GURYE_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": GURYE_LIST_URL,
    "source_total": 18,
    "page_counts": [10, 8],
    "empty_sentinel_page": 3,
    "declared_pager_max": 2,
    "status_counts": {"진행중": 7, "종료": 11},
    "current_or_future": 7,
    "current_status_counts": {"진행중": 7},
    "current_active_application_controls": 0,
    "current_offline_controls": 6,
    "current_closed_combined_controls": 1,
    "category_counts": {
        "평생교육과": 9,
        "정보화교육": 0,
        "매천도서관": 2,
        "농업기술센터": 2,
        "보건의료원": 0,
        "목재문화체험장": 2,
        "지리산정원관리사업소": 3,
        "기타": 0,
    },
    "current_branch_counts": {
        "구례목재문화체험장": 1,
        "구례여성문화회관": 3,
        "구례군 평생학습관": 1,
        "압화체험교육관": 1,
        "구례군 종합사회복지관": 1,
    },
    "stale_candidate_now_serves": "공영버스터미널 버스시간 및 요금",
    "family_center_total": 29,
    "family_center_page_counts": [5, 5, 5, 5, 5, 4],
    "family_center_empty_sentinel_page": 7,
    "family_center_declared_pager_max": 5,
    "family_center_current_or_future": 24,
    "family_center_expired_but_open": 5,
    "jne_reading_total": 9,
    "jne_reading_current_or_future": 3,
    "jne_lifelong_total": 8,
    "jne_lifelong_current_or_future": 8,
    "conclusion": (
        "replace the stale County target with the official /yeyak ledger; "
        "keep Family Center and both JNE library catalogues as separate owners"
    ),
}

GURYE_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    GURYE_STALE_CANDIDATE_ID: {
        "provider": GURYE_STALE_PROVIDER,
        "url": GURYE_STALE_URL,
        "decision": "replace_stale_menu_number_reassigned_to_bus_timetable",
    },
    "MUNI_IR_1426A6B141EB": {
        "provider": "MUNI_GRLIB_JNE_GO_KR_133262C9",
        "url": GURYE_JNE_READING_URL,
        "decision": "keep_existing_separate_jne_library_owner",
    },
    "MUNI_IR_3C27427CA35A": {
        "provider": "MUNI_GRLIB_JNE_GO_KR_E6838F98",
        "url": GURYE_JNE_LIFELONG_URL,
        "decision": "keep_existing_separate_jne_library_owner",
    },
    "MUNI_IR_2143C71DB860": {
        "provider": "MUNI_ENERZAY_COM_BF9D3C76",
        "decision": "exclude_unverified_third_party_blog",
    },
}

GURYE_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    GURYE_PROVIDER: {
        "decision": "new_county_owned_complete_course_ledger",
        "exact_branch": GURYE_OWNER_BRANCH,
        "catalogues": (GURYE_LIST_URL,),
    },
    "gurye_family_center": {
        "decision": "keep_separate_family_center_owner",
        "exact_branch": "구례군 가족센터",
        "catalogues": (GURYE_FAMILY_CENTER_URL,),
    },
    "MUNI_GRLIB_JNE_GO_KR_133262C9": {
        "decision": "keep_separate_education_office_library_owner",
        "exact_branch": "전남광주통합특별시교육청구례도서관",
        "catalogues": (GURYE_JNE_READING_URL,),
    },
    "MUNI_GRLIB_JNE_GO_KR_E6838F98": {
        "decision": "keep_separate_education_office_library_owner",
        "exact_branch": "전남광주통합특별시교육청구례도서관",
        "catalogues": (GURYE_JNE_LIFELONG_URL,),
    },
    "gurye_maecheon_library": {
        "decision": "county_facility_inside_county_ledger_not_jne_duplicate",
        "exact_branch": "구례군매천도서관",
        "catalogues": (GURYE_LIST_URL,),
    },
}

GURYE_PII_FIELDS_DISCARDED = (
    "phone/email/contact/staff data",
    "free-form course bodies",
    "attachments and preview/download URLs",
    "CSRF and session identifiers",
    "application and applicant payloads",
    "source HTML",
)

_TITLE = "프로그램신청 < 교육⋅문화 프로그램 < 평생교육 구례군청"
_EMPTY_MARKER = "등록된 강좌가 없습니다."
_OFFLINE_ALERT = "javascript:alert('오프라인 접수 입니다. 전화문의 바랍니다.');"
_IDENTITY_RE = re.compile(r"^YEYAK_[0-9]{10}$")
_DATE_RANGE_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_DATETIME_RANGE_RE = re.compile(
    r"^(?P<sd>20\d{2}-\d{2}-\d{2})\s+(?P<sh>\d{1,2})시"
    r"(?:\s*(?P<sm>\d{1,2})분)?\s*~\s*"
    r"(?P<ed>20\d{2}-\d{2}-\d{2})\s+(?P<eh>\d{1,2})시"
    r"(?:\s*(?P<em>\d{1,2})분)?$"
)
_CAPACITY_RE = re.compile(r"^(?P<count>[0-9]+)\s*명$")
_APPLIED_RE = re.compile(r"^(?P<current>[0-9]+)\s*/\s*(?P<total>[1-9][0-9]*)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[\s().-]*\d{3,4}[\s.-]*\d{4}|"
    r"0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SPACE_RE = re.compile(r"\s+")

_STATUS_MAP: Mapping[str, tuple[str, str]] = {
    "접수중": ("OPEN", "accept"),
    "진행대기": ("SCHEDULED", "standby"),
    "진행중": ("CLOSED", "lecture"),
    "종료": ("CLOSED", "finish"),
    "준비중": ("SCHEDULED", "preparing"),
    "상시": ("OPEN", "always"),
}
_METHODS = frozenset({"온라인접수", "오프라인접수", "온/오프라인접수"})
_LIST_REQUIRED_FIELDS = frozenset({"접수기간", "수강기간", "수강시간", "수강료", "교육장소"})
_LIST_OPTIONAL_FIELDS = frozenset({"신청/모집", "모집인원", "재료비"})
_DETAIL_REQUIRED_FIELDS = frozenset(
    {
        "교육과정구분",
        "모집인원",
        "접수기간",
        "수강기간",
        "수강시간",
        "교육대상",
        "교육장소",
        "수강료",
    }
)
_DETAIL_IGNORED_FIELDS = frozenset({"문의전화", "기타사항", "강의계획서"})
_ALLOWED_ROW_KEYS = frozenset(
    {
        "provider",
        "provider_course_id",
        "prefer_incoming_provider_course_id",
        "title",
        "description",
        "branch",
        "branch_code",
        "preserve_branch",
        "category",
        "program_type",
        "raw_url",
        "application_url",
        "application_type",
        "application_method_raw",
        "reservation_available",
        "status",
        "fee",
        "period",
        "start_date",
        "end_date",
        "apply_period",
        "apply_start_date",
        "apply_end_date",
        "schedule_raw",
        "target",
        "capacity_current",
        "capacity_total",
        "venue_name",
        "collection_category",
        "domain_category",
        "operator_type",
        "source_group",
        "service_group",
        "collection_type",
        "municipality_code",
        "municipality_name",
        "raw_fields",
    }
)
_ALLOWED_RAW_KEYS = frozenset(
    {
        "parser",
        "source_identity",
        "source_page",
        "source_status",
        "source_office",
        "detail_verified",
        "application_control_contract",
        "application_control_verified",
    }
)
_PII_CHECK_ROW_KEYS = frozenset(
    {
        "title",
        "description",
        "branch",
        "category",
        "application_method_raw",
        "schedule_raw",
        "target",
        "venue_name",
        "municipality_name",
    }
)
_PII_CHECK_RAW_KEYS = frozenset(
    {
        "source_status",
        "source_office",
        "application_control_contract",
    }
)


class GuryeContractError(ValueError):
    """Raised when the official Gurye source violates its audited contract."""


@dataclass(frozen=True)
class _ListPage:
    page: int
    rows: tuple[dict[str, Any], ...]
    declared_pager_max: int
    empty_marker: bool


SessionFactory = Callable[[], Any]
HtmlFetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _safe_port(parsed: Any) -> Optional[int]:
    try:
        return parsed.port
    except ValueError:
        return -1


def _validate_url(
    value: str,
    *,
    path: str,
    expected_query: Mapping[str, str],
) -> str:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != GURYE_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.fragment
        or parsed.path != path
        or query != {key: [item] for key, item in expected_query.items()}
    ):
        raise GuryeContractError(f"non-canonical Gurye URL: {value!r}")
    return parsed.geturl()


def gurye_list_url(page: int) -> str:
    if not isinstance(page, int) or page < 1:
        raise GuryeContractError(f"invalid list page: {page!r}")
    return (
        f"https://{GURYE_HOST}{GURYE_LIST_PATH}?"
        f"{urlencode({'searchTrainingCaCode': '', 'menuNo': GURYE_MENU_NO, 'pageIndex': page})}"
    )


def gurye_detail_url(identity: str) -> str:
    identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(identity):
        raise GuryeContractError(f"invalid Gurye course identity: {identity!r}")
    return (
        f"https://{GURYE_HOST}{GURYE_DETAIL_PATH}?"
        f"{urlencode({'trainingId': identity, 'menuNo': GURYE_MENU_NO, 'searchTrainingCaCode': ''})}"
    )


def _new_session() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; Mooncen-Gurye-Audit/1.0)",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    return current.get(url, timeout=timeout, allow_redirects=False)


def _header(response: Any, name: str) -> str:
    for key, value in (getattr(response, "headers", {}) or {}).items():
        if str(key).lower() == name.lower():
            return _clean(value)
    return ""


def _response_bytes(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, bytearray):
        return bytes(content)
    text = getattr(response, "text", None)
    return text.encode("utf-8") if isinstance(text, str) else b""


def _fetch_soup(
    current: Any,
    url: str,
    timeout: int,
    fetcher: HtmlFetcher,
) -> BeautifulSoup:
    response = fetcher(current, url, timeout)
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise GuryeContractError(f"{url}: HTTP {getattr(response, 'status_code', None)!r}")
    if tuple(getattr(response, "history", ()) or ()):
        raise GuryeContractError(f"{url}: redirects are not permitted")
    if _clean(getattr(response, "url", "")) != url:
        raise GuryeContractError(f"{url}: unexpected final URL")
    if "text/html" not in _header(response, "Content-Type").lower():
        raise GuryeContractError(f"{url}: non-HTML response")
    body = _response_bytes(response)
    if not body or len(body) > GURYE_MAX_HTML_BYTES:
        raise GuryeContractError(f"{url}: invalid body size {len(body)}")
    try:
        text = body.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise GuryeContractError(f"{url}: invalid UTF-8") from exc
    return BeautifulSoup(text, "html.parser")


def _parse_period(value: str, *, identity: str) -> tuple[date, date]:
    match = _DATE_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise GuryeContractError(f"course {identity}: invalid course period")
    start = date.fromisoformat(match.group("start"))
    end = date.fromisoformat(match.group("end"))
    if start > end:
        raise GuryeContractError(f"course {identity}: reversed course period")
    return start, end


def _parse_apply_period(
    value: str,
    *,
    identity: str,
) -> tuple[datetime, datetime]:
    match = _DATETIME_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise GuryeContractError(f"course {identity}: invalid application period")
    start = datetime(
        *map(int, match.group("sd").split("-")),
        int(match.group("sh")),
        int(match.group("sm") or 0),
    )
    end = datetime(
        *map(int, match.group("ed").split("-")),
        int(match.group("eh")),
        int(match.group("em") or 0),
    )
    if start > end:
        raise GuryeContractError(f"course {identity}: reversed application period")
    return start, end


def _matrix_free_path(value: str) -> str:
    return urlparse(value).path.split(";", 1)[0]


def _identity_from_detail_href(value: str) -> str:
    absolute = urljoin(f"https://{GURYE_HOST}/", _clean(value))
    parsed = urlparse(absolute)
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _clean((query.get("trainingId") or [""])[0])
    if (
        parsed.scheme != "https"
        or parsed.hostname != GURYE_HOST
        or _matrix_free_path(absolute) != GURYE_DETAIL_PATH
        or set(query) != {"trainingId", "menuNo", "searchTrainingCaCode"}
        or query.get("menuNo") != [GURYE_MENU_NO]
        or query.get("searchTrainingCaCode") != [""]
        or not _IDENTITY_RE.fullmatch(identity)
    ):
        raise GuryeContractError("invalid identity-bearing detail link")
    return identity


def _parse_fields(card: Any, *, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in card.select(".info > .list > li"):
        label_node = item.find("em", recursive=False)
        value_node = item.find("span", recursive=False)
        if label_node is None or value_node is None:
            raise GuryeContractError(f"course {identity}: malformed list field")
        label = _clean(label_node.get_text(" ", strip=True))
        value = _clean(value_node.get_text(" ", strip=True))
        if label in fields or label not in _LIST_REQUIRED_FIELDS | _LIST_OPTIONAL_FIELDS:
            raise GuryeContractError(f"course {identity}: unexpected list field {label!r}")
        fields[label] = value
    if not _LIST_REQUIRED_FIELDS.issubset(fields):
        raise GuryeContractError(f"course {identity}: incomplete list fields")
    if ("신청/모집" in fields) == ("모집인원" in fields):
        raise GuryeContractError(f"course {identity}: ambiguous capacity fields")
    return fields


def _parse_list_control(card: Any, method: str, status: str, identity: str) -> str:
    anchor = card.select_one(".btn > .apply > a")
    if anchor is None or _clean(anchor.get_text(" ", strip=True)) != "신청하기":
        raise GuryeContractError(f"course {identity}: application control missing")
    href = _clean(anchor.get("href"))
    onclick = _clean(anchor.get("onclick"))
    classes = {_clean(item) for item in (anchor.get("class") or [])}
    if method == "오프라인접수":
        if href != "javascript:void(0)" or onclick != _OFFLINE_ALERT or "disable" not in classes:
            raise GuryeContractError(f"course {identity}: offline control changed")
        return "offline_disabled_alert"
    if href == "javascript:void(0)":
        if onclick or "disable" not in classes or status == "접수중":
            raise GuryeContractError(f"course {identity}: closed online control changed")
        return "closed_online_disabled"
    if status != "접수중":
        raise GuryeContractError(f"course {identity}: active control contradicts status")
    _validate_application_href(href, identity)
    return "active_online_identity_link"


def _validate_application_href(value: str, identity: str) -> str:
    absolute = urljoin(f"https://{GURYE_HOST}/", _clean(value))
    parsed = urlparse(absolute)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != GURYE_HOST
        or _safe_port(parsed) is not None
        or not _matrix_free_path(absolute).startswith("/yeyak/")
        or query.get("trainingId") != [identity]
        or set(query) - {"trainingId", "menuNo", "searchTrainingCaCode"}
        or query.get("menuNo", [GURYE_MENU_NO]) != [GURYE_MENU_NO]
        or query.get("searchTrainingCaCode", [""]) != [""]
    ):
        raise GuryeContractError(f"course {identity}: unsafe application link")
    path = _matrix_free_path(absolute)
    return f"https://{GURYE_HOST}{path}?{urlencode({key: values[0] for key, values in query.items()})}"


def _validate_category_tabs(soup: BeautifulSoup) -> None:
    observed: dict[str, str] = {}
    for anchor in soup.select("#content a[href*='YeyakList.do'][href*='searchTrainingCaCode']"):
        href = _clean(anchor.get("href"))
        parsed = urlparse(urljoin(f"https://{GURYE_HOST}/", href))
        query = parse_qs(parsed.query, keep_blank_values=True)
        code = _clean((query.get("searchTrainingCaCode") or [""])[0])
        label = _clean(anchor.get_text(" ", strip=True))
        if code in GURYE_CATEGORY_CODES and label == GURYE_CATEGORY_CODES[code]:
            observed[code] = label
    if observed != dict(GURYE_CATEGORY_CODES):
        raise GuryeContractError("category tabs changed or are incomplete")


def _parse_list_page(soup: BeautifulSoup, page: int) -> _ListPage:
    if soup.title is None or _clean(soup.title.get_text(" ", strip=True)) != _TITLE:
        raise GuryeContractError(f"page {page}: document owner/title changed")
    form = soup.select_one("form#articleForm")
    if form is None or _matrix_free_path(_clean(form.get("action"))) != GURYE_LIST_PATH:
        raise GuryeContractError(f"page {page}: catalogue form changed")
    menu = form.select_one("input[name=menuNo]")
    category = form.select_one("input[name=searchTrainingCaCode]")
    if menu is None or _clean(menu.get("value")) != GURYE_MENU_NO or category is None or _clean(category.get("value")):
        raise GuryeContractError(f"page {page}: catalogue form scope changed")
    _validate_category_tabs(soup)

    rows: list[dict[str, Any]] = []
    empty_cards = 0
    for card in soup.select("#content .applyProgram"):
        detail = card.select_one(".btn > .view > a[href*='trainingId']")
        if detail is None:
            if _clean(card.get_text(" ", strip=True)) == _EMPTY_MARKER:
                empty_cards += 1
                continue
            raise GuryeContractError(f"page {page}: non-course card without sentinel")
        identity = _identity_from_detail_href(_clean(detail.get("href")))
        title_node = card.select_one(":scope > h5")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        if not title or len(title) > 300 or _PHONE_RE.search(title) or _EMAIL_RE.search(title):
            raise GuryeContractError(f"course {identity}: invalid title")
        tags = card.select(":scope > .tag > span")
        if len(tags) != 3:
            raise GuryeContractError(f"course {identity}: tag contract changed")
        status = _clean(tags[0].get_text(" ", strip=True))
        office = _clean(tags[1].get_text(" ", strip=True))
        method = _clean(tags[2].get_text(" ", strip=True))
        if status not in _STATUS_MAP or office not in set(GURYE_CATEGORY_CODES.values()) - {"전체"}:
            raise GuryeContractError(f"course {identity}: invalid status/office")
        if method not in _METHODS:
            raise GuryeContractError(f"course {identity}: invalid application method")
        expected_class = _STATUS_MAP[status][1]
        if expected_class not in {_clean(item) for item in (tags[0].get("class") or [])}:
            raise GuryeContractError(f"course {identity}: status class mismatch")
        fields = _parse_fields(card, identity=identity)
        event_start, event_end = _parse_period(fields["수강기간"], identity=identity)
        apply_start, apply_end = _parse_apply_period(fields["접수기간"], identity=identity)
        if "신청/모집" in fields:
            capacity_match = _APPLIED_RE.match(fields["신청/모집"])
            if not capacity_match:
                raise GuryeContractError(f"course {identity}: invalid applied/capacity")
            capacity_current: Optional[int] = int(capacity_match.group("current"))
            capacity_total = int(capacity_match.group("total"))
        else:
            if not fields["모집인원"].isdigit() or int(fields["모집인원"]) < 1:
                raise GuryeContractError(f"course {identity}: invalid capacity")
            capacity_current = None
            capacity_total = int(fields["모집인원"])
        if capacity_current is not None and capacity_current > capacity_total:
            raise GuryeContractError(f"course {identity}: impossible capacity")
        control = _parse_list_control(card, method, status, identity)
        rows.append(
            {
                "identity": identity,
                "title": title,
                "page": page,
                "source_status": status,
                "status": _STATUS_MAP[status][0],
                "office": office,
                "method": method,
                "event_start": event_start,
                "event_end": event_end,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule": fields["수강시간"],
                "fee": fields["수강료"],
                "materials": fields.get("재료비", ""),
                "venue": fields["교육장소"],
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "application_control": control,
                "application_href": _clean(card.select_one(".btn > .apply > a").get("href")),
            }
        )

    empty_marker = empty_cards == 1 and not rows
    if empty_cards and not empty_marker:
        raise GuryeContractError(f"page {page}: mixed/duplicate empty sentinel")
    pager_values = [
        int(text)
        for node in soup.select("#content .paging li > a")
        if (text := _clean(node.get_text(" ", strip=True))).isdigit()
    ]
    if not pager_values:
        raise GuryeContractError(f"page {page}: pager contract missing")
    return _ListPage(page, tuple(rows), max(pager_values), empty_marker)


def _page_signature(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.page,
        page.empty_marker,
        page.declared_pager_max,
        tuple(
            (
                row["identity"],
                row["title"],
                row["source_status"],
                row["event_start"],
                row["event_end"],
                row["application_control"],
            )
            for row in page.rows
        ),
    )


def _branch_for(office: str, title: str, venue: str) -> str:
    combined = _clean(f"{title} {venue}")
    if "여성문화회관" in combined:
        return "구례여성문화회관"
    if "종합사회복지관" in combined:
        return "구례군 종합사회복지관"
    if "평생학습관" in combined:
        return "구례군 평생학습관"
    if "압화체험교육관" in combined:
        return "압화체험교육관"
    return {
        "목재문화체험장": "구례목재문화체험장",
        "매천도서관": "구례군매천도서관",
        "농업기술센터": "구례군 농업기술센터",
        "보건의료원": "구례군 보건의료원",
        "지리산정원관리사업소": "구례군 지리산정원관리사업소",
        "정보화교육": "구례군 정보화교육",
        "기타": "구례군청",
        "평생교육과": "구례군 평생교육과",
    }[office]


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    return f"{GURYE_PROVIDER}:BRANCH:{digest}"[:100]


def _detail_fields(soup: BeautifulSoup, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    allowed = _DETAIL_REQUIRED_FIELDS | _LIST_OPTIONAL_FIELDS | _DETAIL_IGNORED_FIELDS
    for item in soup.select(".applyView > .list > li"):
        label_node = item.find("em", recursive=False)
        value_node = item.find("span", recursive=False)
        if label_node is None or value_node is None:
            continue
        label = _clean(label_node.get_text(" ", strip=True))
        if label not in allowed:
            raise GuryeContractError(f"detail {identity}: unexpected field {label!r}")
        if label in fields:
            raise GuryeContractError(f"detail {identity}: duplicate field {label!r}")
        if label in _DETAIL_IGNORED_FIELDS:
            fields[label] = "present"
            continue
        fields[label] = _clean(value_node.get_text(" ", strip=True))
    if not _DETAIL_REQUIRED_FIELDS.issubset(fields):
        raise GuryeContractError(f"detail {identity}: incomplete fields")
    return fields


def _parse_detail(soup: BeautifulSoup, listed: Mapping[str, Any]) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    if soup.title is None or _clean(soup.title.get_text(" ", strip=True)) != _TITLE:
        raise GuryeContractError(f"detail {identity}: document owner/title changed")
    header = soup.select_one(".applyView_tit > .applySearch_wrap")
    status_node = header.select_one(":scope > .state") if header else None
    title_node = header.select_one(":scope > h3") if header else None
    source_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if source_status != listed.get("source_status") or title != listed.get("title"):
        raise GuryeContractError(f"detail {identity}: title/status mismatch")
    fields = _detail_fields(soup, identity)
    expected_course = f"{listed['office']} > {listed['title']}"
    if fields["교육과정구분"] != expected_course:
        raise GuryeContractError(f"detail {identity}: office/title mismatch")
    capacity_match = _CAPACITY_RE.fullmatch(fields["모집인원"])
    if not capacity_match or int(capacity_match.group("count")) != listed.get("capacity_total"):
        raise GuryeContractError(f"detail {identity}: capacity mismatch")
    event_start, event_end = _parse_period(fields["수강기간"], identity=identity)
    apply_start, apply_end = _parse_apply_period(fields["접수기간"], identity=identity)
    if (
        event_start != listed.get("event_start")
        or event_end != listed.get("event_end")
        or apply_start != listed.get("apply_start")
        or apply_end != listed.get("apply_end")
        or fields["수강시간"] != listed.get("schedule")
        or fields["교육장소"] != listed.get("venue")
        or fields["수강료"] != listed.get("fee")
    ):
        raise GuryeContractError(f"detail {identity}: list/detail facts mismatch")
    if _clean(fields.get("재료비")) != _clean(listed.get("materials")):
        raise GuryeContractError(f"detail {identity}: material fee mismatch")
    target = fields["교육대상"]
    if not target or len(target) > 500 or _PHONE_RE.search(target) or _EMAIL_RE.search(target):
        raise GuryeContractError(f"detail {identity}: unsafe target")

    control = soup.select_one("#content .apply_btn > a:first-child")
    contract = _clean(listed.get("application_control"))
    application_url = ""
    if contract == "offline_disabled_alert":
        if (
            control is None
            or _clean(control.get_text(" ", strip=True)) != "신청하기"
            or _clean(control.get("href")) != "javascript:void(0)"
            or _clean(control.get("onclick")) != _OFFLINE_ALERT
            or "disable" not in {_clean(item) for item in (control.get("class") or [])}
        ):
            raise GuryeContractError(f"detail {identity}: offline control mismatch")
    elif contract == "closed_online_disabled":
        if control is not None and _clean(control.get_text(" ", strip=True)) == "신청하기":
            if _clean(control.get("href")) != "javascript:void(0)" or _clean(control.get("onclick")):
                raise GuryeContractError(f"detail {identity}: closed control mismatch")
    elif contract == "active_online_identity_link":
        if control is None or _clean(control.get_text(" ", strip=True)) != "신청하기":
            raise GuryeContractError(f"detail {identity}: active control missing")
        application_url = _validate_application_href(_clean(control.get("href")), identity)
    else:
        raise GuryeContractError(f"detail {identity}: unknown control contract")

    branch = _branch_for(
        _clean(listed.get("office")),
        _clean(listed.get("title")),
        _clean(listed.get("venue")),
    )
    status = _clean(listed.get("status"))
    reservation_available = bool(application_url) and status == "OPEN"
    row = {
        "provider": GURYE_PROVIDER,
        "provider_course_id": f"gurye_yeyak:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": "교육",
        "program_type": "구례군 교육·문화 프로그램",
        "raw_url": gurye_detail_url(identity),
        "application_url": application_url,
        "application_type": "온라인" if application_url else _clean(listed.get("method")),
        "application_method_raw": _clean(listed.get("method")),
        "reservation_available": reservation_available,
        "status": status,
        "fee": fields["수강료"],
        "period": f"{event_start.isoformat()} ~ {event_end.isoformat()}",
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": (f"{apply_start.strftime('%Y-%m-%d %H:%M')} ~ {apply_end.strftime('%Y-%m-%d %H:%M')}"),
        "apply_start_date": apply_start.strftime("%Y-%m-%d %H:%M"),
        "apply_end_date": apply_end.strftime("%Y-%m-%d %H:%M"),
        "schedule_raw": fields["수강시간"],
        "target": target,
        "capacity_current": listed.get("capacity_current"),
        "capacity_total": int(listed.get("capacity_total") or 0),
        "venue_name": fields["교육장소"],
        "collection_category": "education",
        "domain_category": "교육",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "education",
        "collection_type": "course",
        "municipality_code": GURYE_MUNICIPALITY_CODE,
        "municipality_name": GURYE_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": GURYE_PARSER,
            "source_identity": identity,
            "source_page": int(listed.get("page") or 0),
            "source_status": source_status,
            "source_office": _clean(listed.get("office")),
            "detail_verified": True,
            "application_control_contract": contract,
            "application_control_verified": True,
        },
    }
    _validate_output(row)
    return row


def _validate_output(row: Mapping[str, Any]) -> None:
    if frozenset(row) - _ALLOWED_ROW_KEYS:
        raise GuryeContractError("emitted row contains unknown fields")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or frozenset(raw) - _ALLOWED_RAW_KEYS:
        raise GuryeContractError("emitted row contains unsafe raw_fields")
    # Scan source-derived prose, not opaque identifiers or ISO dates.  A course
    # identity such as ``YEYAK_0000000367`` otherwise resembles a local phone
    # number to the intentionally broad contact-data regular expression.
    values = [str(row[key]) for key in _PII_CHECK_ROW_KEYS if key in row and row[key] is not None]
    values.extend(str(raw[key]) for key in _PII_CHECK_RAW_KEYS if key in raw and raw[key] is not None)
    combined = " ".join(values)
    if _PHONE_RE.search(combined) or _EMAIL_RE.search(combined):
        raise GuryeContractError("emitted row leaked contact data")


def is_gurye_education_target(target: Any) -> bool:
    """Return true only for the audited County-owned complete catalogue."""

    return (
        _clean(_target_value(target, "provider")) == GURYE_PROVIDER
        and _clean(_target_value(target, "url")) == GURYE_LIST_URL
    )


is_target = is_gurye_education_target


def collect_gurye_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = GURYE_MAX_PAGES,
    detail_limit: int = 100,
    workers: int = GURYE_MAX_WORKERS,
    cutoff: Optional[date] = None,
    session_factory: Optional[SessionFactory] = None,
    html_fetcher: Optional[HtmlFetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future Gurye County course snapshot."""

    audit_date = cutoff or date.today()
    factory = session_factory or _new_session
    fetcher = html_fetcher or _default_fetcher
    meta: dict[str, Any] = {
        "municipality_code": GURYE_MUNICIPALITY_CODE,
        "owner_provider": GURYE_PROVIDER,
        "canonical_url": GURYE_LIST_URL,
        "parser": GURYE_PARSER,
        "cutoff": audit_date.isoformat(),
        "pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "pagination_complete": False,
        "source_cap_reached": False,
        "application_form_requests": 0,
    }
    try:
        if _clean(_target_value(target, "provider")) != GURYE_PROVIDER:
            raise GuryeContractError("target provider does not own the Gurye ledger")
        target_url = _clean(_target_value(target, "url"))
        _validate_url(
            target_url,
            path=GURYE_LIST_PATH,
            expected_query={"searchTrainingCaCode": "", "menuNo": GURYE_MENU_NO},
        )
        if target_url != GURYE_LIST_URL:
            raise GuryeContractError("target URL is not the canonical catalogue")
        if timeout < 1 or max_pages < 1 or detail_limit < 0 or workers < 1:
            raise GuryeContractError("invalid collector limits")

        current = factory()
        pages: dict[int, _ListPage] = {}
        sentinel: Optional[_ListPage] = None
        try:
            for page_number in range(1, max_pages + 1):
                parsed = _parse_list_page(
                    _fetch_soup(
                        current,
                        gurye_list_url(page_number),
                        timeout,
                        fetcher,
                    ),
                    page_number,
                )
                meta["list_requests"] += 1
                if parsed.rows:
                    pages[page_number] = parsed
                    continue
                sentinel = parsed
                break
            if sentinel is None:
                meta["source_cap_reached"] = True
                raise GuryeContractError("max_pages reached before empty sentinel")
            first_check = _parse_list_page(_fetch_soup(current, gurye_list_url(1), timeout, fetcher), 1)
            last_number = max(pages, default=1)
            last_check = _parse_list_page(
                _fetch_soup(current, gurye_list_url(last_number), timeout, fetcher),
                last_number,
            )
            sentinel_check = _parse_list_page(
                _fetch_soup(current, gurye_list_url(sentinel.page), timeout, fetcher),
                sentinel.page,
            )
            meta["list_requests"] += 3
        finally:
            close = getattr(current, "close", None)
            if callable(close):
                close()

        if not pages or sorted(pages) != list(range(1, max(pages) + 1)):
            raise GuryeContractError("missing or non-consecutive data pages")
        first = pages[1]
        last = pages[max(pages)]
        if (
            _page_signature(first_check) != _page_signature(first)
            or _page_signature(last_check) != _page_signature(last)
            or not sentinel.empty_marker
            or sentinel.rows
            or not sentinel_check.empty_marker
            or sentinel_check.rows
        ):
            raise GuryeContractError("first/last/sentinel stability check changed")
        for page_number, page in pages.items():
            if page_number < max(pages) and len(page.rows) != GURYE_PAGE_SIZE:
                raise GuryeContractError(f"page {page_number}: premature short page")
            if page_number == max(pages) and not 1 <= len(page.rows) <= GURYE_PAGE_SIZE:
                raise GuryeContractError("invalid final page size")
        declared = max(page.declared_pager_max for page in (*pages.values(), sentinel))
        if declared != len(pages):
            raise GuryeContractError("declared and observed page boundaries differ")

        listed = [row for number in sorted(pages) for row in pages[number].rows]
        identities = [_clean(row.get("identity")) for row in listed]
        if len(identities) != len(set(identities)):
            raise GuryeContractError("duplicate identities across pages")
        current_rows = [row for row in listed if row["event_end"] >= audit_date]
        expired_rows = [row for row in listed if row["event_end"] < audit_date]
        for row in current_rows:
            if row["source_status"] == "접수중" and not (
                row["apply_start"].date() <= audit_date <= row["apply_end"].date()
            ):
                raise GuryeContractError(f"course {row['identity']}: OPEN date contradiction")
        meta.update(
            {
                "pages": len(pages),
                "data_pages": len(pages),
                "page_counts": {number: len(page.rows) for number, page in sorted(pages.items())},
                "empty_sentinel_page": sentinel.page,
                "declared_pager_max": declared,
                "source_rows": len(listed),
                "source_total": len(listed),
                "source_status_counts": dict(Counter(row["source_status"] for row in listed)),
                "source_office_counts": dict(Counter(row["office"] for row in listed)),
                "current_source_count": len(current_rows),
                "expired_source_count": len(expired_rows),
                "stability_rechecks": 3,
            }
        )
        if len(current_rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise GuryeContractError("detail_limit would create a partial snapshot")

        detailed: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        def fetch_detail(parent: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
            identity = _clean(parent.get("identity"))
            detail_session = factory()
            try:
                soup = _fetch_soup(
                    detail_session,
                    gurye_detail_url(identity),
                    timeout,
                    fetcher,
                )
                return identity, _parse_detail(soup, parent)
            finally:
                close = getattr(detail_session, "close", None)
                if callable(close):
                    close()

        if current_rows:
            with ThreadPoolExecutor(max_workers=min(workers, len(current_rows))) as pool:
                futures = {pool.submit(fetch_detail, row): row for row in current_rows}
                for future in as_completed(futures):
                    identity = _clean(futures[future].get("identity"))
                    try:
                        result_identity, row = future.result()
                        detailed[result_identity] = row
                    except Exception as exc:  # fail closed after workers settle
                        errors.append(f"detail {identity}: {exc}")
            meta["detail_pages"] = len(current_rows)
        if errors or len(detailed) != len(current_rows):
            raise GuryeContractError("; ".join(sorted(errors)) or "detail loss")

        output = [detailed[row["identity"]] for row in current_rows]
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        meta.update(
            {
                "pagination_complete": True,
                "detail_verified": len(current_rows),
                "application_controls_verified": len(current_rows),
                "active_application_controls": sum(bool(row.get("reservation_available")) for row in output),
                "branch_counts": dict(Counter(row["branch"] for row in output)),
                "output_rows": len(output),
                "configured_collection_error": "",
            }
        )
        return output, GURYE_PARSER, meta
    except (GuryeContractError, requests.RequestException, ValueError, TypeError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        meta["pagination_complete"] = False
        meta["output_rows"] = 0
        return [], GURYE_PARSER, meta


collect = collect_gurye_education


__all__ = [
    "GURYE_AGRICULTURAL_ALIAS_URL",
    "GURYE_CANDIDATE_AUDIT",
    "GURYE_CATEGORY_CODES",
    "GURYE_DISCOVERY_AUDIT",
    "GURYE_FAMILY_CENTER_URL",
    "GURYE_HEALTH_ALIAS_URL",
    "GURYE_HOST",
    "GURYE_JNE_LIFELONG_URL",
    "GURYE_JNE_READING_URL",
    "GURYE_LIST_PATH",
    "GURYE_LIST_URL",
    "GURYE_MENU_NO",
    "GURYE_MUNICIPALITY_CODE",
    "GURYE_MUNICIPALITY_NAME",
    "GURYE_OWNER_BOUNDARY_AUDIT",
    "GURYE_OWNER_BRANCH",
    "GURYE_PARSER",
    "GURYE_PII_FIELDS_DISCARDED",
    "GURYE_PROVIDER",
    "GURYE_STALE_CANDIDATE_ID",
    "GURYE_STALE_PROVIDER",
    "GURYE_STALE_URL",
    "GuryeContractError",
    "collect_gurye_education",
    "gurye_detail_url",
    "gurye_list_url",
    "is_gurye_education_target",
]
