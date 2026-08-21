"""Fail-closed collector for Jindo-gun's official lifelong course catalogue.

The municipal search candidates are landing, introduction, and tourism pages.
The course owner already exists at ``edu/edu/E0004.cs?m=9``; this module keeps
that provider identity and treats the other pages as duplicate discovery or
separate service boundaries.

The unfiltered catalogue is read through every declared page, the immediate
empty page, and stable first/last rechecks.  Historical rows prove catalogue
completeness, but only current/future rows are returned and only their details
are opened.  Detail extraction is allowlisted: lecturer, contact, attachment,
related-site, free-form content, and the embedded applicant form are never read
or persisted.  A visible online application is accepted only when the detail
button and JavaScript binding both name the same official course identity.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


JINDO_PROVIDER = "MUNI_WWW_JINDO_GO_KR_070F7C38"
JINDO_DUPLICATE_PROVIDER = "MUNI_WWW_JINDO_GO_KR_21B9A5BD"
JINDO_ROOT_CANDIDATE_ID = "MUNI_IR_19D31F07087B"
JINDO_INTRO_CANDIDATE_ID = "MUNI_IR_34F75112452B"
JINDO_TOUR_CANDIDATE_ID = "MUNI_IR_8450687B2D38"

JINDO_HOST = "www.jindo.go.kr"
JINDO_PATH = "/edu/edu/E0004.cs"
JINDO_URL = f"https://{JINDO_HOST}{JINDO_PATH}?m=9"
JINDO_ROOT_URL = f"https://{JINDO_HOST}/"
JINDO_EDU_LANDING_URL = f"https://{JINDO_HOST}/edu/main.cs"
JINDO_INTRO_URL = f"https://{JINDO_HOST}/edu/sub.cs?m=6"
JINDO_TOUR_URL = "https://jindo.go.kr/tour/main.cs"
JINDO_SAFETY_URL = f"https://{JINDO_HOST}/safety/"
JINDO_AGRICULTURE_URL = f"https://{JINDO_HOST}/atc/main.cs"
JINDO_LIBRARY_URL = "https://lib.jindo.go.kr/"
JINDO_CONFIRM_URL = f"https://{JINDO_HOST}/edu/edu/E0004.cs?m=10&act=confirmList"
JINDO_PAGE_SIZE = 10
JINDO_MUNICIPALITY_CODE = "1286000000"
JINDO_MUNICIPALITY_NAME = "전남광주통합특별시 진도군"
JINDO_MAX_WORKERS = 4
JINDO_FETCH_ATTEMPTS = 3
JINDO_RETRY_BACKOFF_SECONDS = 0.2
JINDO_MAX_HTML_BYTES = 4_000_000
JINDO_PARSER = (
    "jindo_official_lifelong_all_pages+empty_sentinel+stable_boundaries+"
    "current_detail_venues+identity_bound_js_controls+pii_allowlist"
)
JINDO_OWNERSHIP_SCOPE = "jindo_official_lifelong_education_schedule_catalogue"

JINDO_STATUS_MAP: Mapping[str, str] = {
    "신청중": "OPEN",
    "신청예정": "SCHEDULED",
    "신청마감": "CLOSED",
}

JINDO_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "EXISTING_JINDO_LIFELONG_OWNER": {
        "decision": "include_existing_provider_as_canonical_course_catalogue_owner",
        "provider": JINDO_PROVIDER,
        "url": JINDO_URL,
        "owner": JINDO_PROVIDER,
    },
    "EXISTING_JINDO_DUPLICATE_PROVIDER": {
        "decision": "exclude_duplicate_provider_for_same_canonical_url",
        "provider": JINDO_DUPLICATE_PROVIDER,
        "url": JINDO_URL,
        "owner": JINDO_PROVIDER,
    },
    JINDO_ROOT_CANDIDATE_ID: {
        "decision": "roll_official_root_candidate_into_existing_catalogue_owner",
        "provider": JINDO_PROVIDER,
        "url": JINDO_ROOT_URL,
        "owner": JINDO_PROVIDER,
    },
    JINDO_INTRO_CANDIDATE_ID: {
        "decision": "roll_introduction_page_into_existing_catalogue_owner",
        "provider": JINDO_DUPLICATE_PROVIDER,
        "url": JINDO_INTRO_URL,
        "owner": JINDO_PROVIDER,
    },
    JINDO_TOUR_CANDIDATE_ID: {
        "decision": "exclude_separate_tourism_site_without_lifelong_course_ownership",
        "provider": "MUNI_JINDO_GO_KR_D9F8002C",
        "url": JINDO_TOUR_URL,
        "owner": "jindo_tourism",
    },
    "OFFICIAL_JINDO_EDU_LANDING": {
        "decision": "exclude_duplicate_landing_republishing_canonical_course_identities",
        "provider": JINDO_PROVIDER,
        "url": JINDO_EDU_LANDING_URL,
        "owner": JINDO_PROVIDER,
        "audited_identity_overlap": 11,
    },
    "SEPARATE_JINDO_MARINE_SAFETY_RESERVATION": {
        "decision": "exclude_separate_experience_reservation_owner",
        "provider": "SEPARATE_WWW_JINDO_GO_KR_SAFETY",
        "url": JINDO_SAFETY_URL,
        "owner": "jindo_national_marine_safety_center",
    },
    "SEPARATE_JINDO_AGRICULTURE_EDUCATION": {
        "decision": "exclude_separate_editorial_training_owner_without_shared_booking_identity",
        "provider": "SEPARATE_WWW_JINDO_GO_KR_ATC",
        "url": JINDO_AGRICULTURE_URL,
        "owner": "jindo_agricultural_technology_center",
    },
    "SEPARATE_JINDO_LIBRARY": {
        "decision": "exclude_separate_library_owner_unavailable_during_live_audit",
        "provider": "SEPARATE_LIB_JINDO_GO_KR",
        "url": JINDO_LIBRARY_URL,
        "owner": "jindo_cheolma_library",
        "live_result": "dns_resolution_failed_2026-07-21",
    },
    "JINDO_MY_APPLICATIONS_PII_BOUNDARY": {
        "decision": "never_fetch_private_application_confirmation_route",
        "provider": JINDO_PROVIDER,
        "url": JINDO_CONFIRM_URL,
        "owner": JINDO_PROVIDER,
    },
}

JINDO_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": JINDO_URL,
    "source_rows": 11,
    "data_pages": 2,
    "required_list_requests": 5,
    "page_row_counts": {1: 10, 2: 1},
    "sentinel_page": 3,
    "current_or_future_rows": 0,
    "expired_rows": 11,
    "source_status_counts": {"신청마감": 11},
    "source_identities_by_page": {
        1: ["199", "197", "212", "206", "202", "196", "200", "201", "198", "205"],
        2: ["204"],
    },
    "historical_details_manually_verified": 11,
    "historical_visible_application_controls": 0,
    "current_branch_names": [],
    "historical_exact_branch_counts": {
        "진도군 여성플라자 2층 어울마당": 3,
        "진도군 옥주골 문화 복지센터": 1,
        "진도군 운림삼별초 파크골프장": 1,
        "진도군 청년센터": 1,
        "진도군 의신면 운림예술촌 국악전수관": 1,
        "진도군 여성플라자 1층 회의실": 1,
        "진도군 여성플라자 2층 프로그램 1실": 1,
        "진도군 여성플라자 프로그램 1실": 1,
        "진도군 유림회관": 1,
    },
    "identity_duplicate_count": 0,
    "duplicate_landing_identity_overlap": 11,
    "conclusion": "three candidates reconcile to one existing canonical course owner",
}

JINDO_PII_FIELDS_DISCARDED = (
    "강사명",
    "문의처",
    "관련홈페이지",
    "첨부파일",
    "강연내용",
    "application_form_values",
    "applicant_name",
    "applicant_gender",
    "applicant_phone",
    "applicant_address",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class JindoContractError(ValueError):
    """Raised when the audited official catalogue contract changes."""


@dataclass(frozen=True)
class _ListPage:
    requested_page: int
    source_pages: int
    rows: tuple[dict[str, Any], ...]
    empty_marker: bool


_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"^[1-9]\d*$")
_DATE_RANGE_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_CAPACITY_RE = re.compile(r"^[\d,]+$")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_DETAIL_LABELS = (
    "과정명",
    "교육기관",
    "강사명",
    "교육장소",
    "모집인원",
    "신청기간",
    "교육기간",
    "운영시간",
    "문의처",
    "관련홈페이지",
    "첨부파일",
)
_SAFE_DETAIL_LABELS = frozenset(
    {"과정명", "교육기관", "교육장소", "모집인원", "신청기간", "교육기간", "운영시간"}
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_page",
        "source_status",
        "source_display_title",
        "source_period",
        "source_venue",
        "source_capacity_total",
        "detail_institution",
        "detail_schedule",
        "detail_verified",
        "visible_application_control_present",
        "application_control_contract",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "contact",
        "phone",
        "email",
        "attachments",
        "related_homepage",
        "description_html",
        "detail_description",
        "source_html",
        "raw_html",
        "applicant_name",
        "applicant_phone",
        "applicant_address",
        "list_pairs",
        "detail_pairs",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _safe_port(parsed: Any) -> Optional[int]:
    try:
        return parsed.port
    except ValueError:
        return -1


def is_jindo_education_target(target: Any) -> bool:
    """Return true only for the existing provider's exact canonical route."""

    if _clean(_target_value(target, "provider")) != JINDO_PROVIDER:
        return False
    value = _clean(_target_value(target, "url"))
    if value != JINDO_URL:
        return False
    parsed = urlparse(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == JINDO_HOST
        and _safe_port(parsed) is None
        and parsed.path == JINDO_PATH
        and not parsed.params
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
        and query == {"m": ["9"]}
    )


def is_jindo_candidate_alias(target: Any) -> bool:
    candidate_id = _clean(_target_value(target, "candidate_id"))
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    expected = {
        JINDO_ROOT_CANDIDATE_ID: (JINDO_PROVIDER, JINDO_ROOT_URL),
        JINDO_INTRO_CANDIDATE_ID: (JINDO_DUPLICATE_PROVIDER, JINDO_INTRO_URL),
        JINDO_TOUR_CANDIDATE_ID: ("MUNI_JINDO_GO_KR_D9F8002C", JINDO_TOUR_URL),
    }
    return bool(candidate_id in expected and (provider, url) == expected[candidate_id])


is_target = is_jindo_education_target


def jindo_list_url(page: Any = 1) -> str:
    raw = _clean(page)
    if not raw.isdigit() or int(raw) < 1:
        return ""
    page_no = int(raw)
    if page_no == 1:
        return JINDO_URL
    return f"https://{JINDO_HOST}{JINDO_PATH}?" + urlencode(
        (("m", "9"), ("pageIndex", page_no))
    )


def jindo_detail_url(identity: Any) -> str:
    raw = _clean(identity)
    if _POSITIVE_ID_RE.fullmatch(raw) is None:
        return ""
    return f"https://{JINDO_HOST}{JINDO_PATH}?" + urlencode(
        (
            ("act", "view"),
            ("infoId", raw),
            ("searchKeyword", ""),
            ("searchCondition", ""),
            ("m", "9"),
        )
    )


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, str):
        payload: Any = value
        size = len(value.encode("utf-8"))
    elif isinstance(value, (bytes, bytearray)):
        payload = bytes(value)
        size = len(payload)
    else:
        status = int(getattr(value, "status_code", 200))
        if 300 <= status < 400:
            raise JindoContractError("HTTP redirects are not accepted")
        raise_for_status = getattr(value, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        payload = getattr(value, "content", None)
        if payload is None:
            payload = getattr(value, "text", None)
        size = len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload or b"")
    if not payload:
        raise JindoContractError("empty HTTP response")
    if size > JINDO_MAX_HTML_BYTES:
        raise JindoContractError("HTTP response exceeds audited HTML size")
    return BeautifulSoup(payload, "lxml")


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
    for attempt in range(JINDO_FETCH_ATTEMPTS):
        session: Any = None
        try:
            session = session_factory()
            return _coerce_soup(fetcher(session, url, timeout))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < JINDO_FETCH_ATTEMPTS:
                time.sleep(JINDO_RETRY_BACKOFF_SECONDS * (attempt + 1))
        finally:
            _close_quietly(session)
    assert last_error is not None
    raise last_error


def _single_query(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _parse_owned_url(value: Any) -> Any:
    parsed = urlparse(urljoin(JINDO_URL, _clean(value)))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != JINDO_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise JindoContractError("official URL ownership changed")
    return parsed


def _detail_link(value: Any) -> tuple[str, str]:
    parsed = _parse_owned_url(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query(query, "infoId")
    if (
        parsed.path != JINDO_PATH
        or set(query) != {"act", "infoId", "searchKeyword", "searchCondition", "m"}
        or _single_query(query, "act") != "view"
        or _single_query(query, "searchKeyword")
        or _single_query(query, "searchCondition")
        or _single_query(query, "m") != "9"
        or _POSITIVE_ID_RE.fullmatch(identity) is None
    ):
        raise JindoContractError("course detail identity link changed")
    return identity, jindo_detail_url(identity)


def _pagination_link_page(value: Any) -> int:
    parsed = _parse_owned_url(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    page = _single_query(query, "pageIndex")
    if (
        parsed.path != JINDO_PATH
        or set(query) != {"m", "pageIndex"}
        or _single_query(query, "m") != "9"
        or _POSITIVE_ID_RE.fullmatch(page) is None
    ):
        raise JindoContractError("pagination link changed")
    return int(page)


def _options(node: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in node.find_all("option", recursive=False)
    )


def _validate_search(soup: BeautifulSoup, page: int) -> None:
    forms = soup.select("div.proSearch > form")
    if len(forms) != 1:
        raise JindoContractError(f"page {page}: official all-history search form changed")
    form = forms[0]
    parsed = _parse_owned_url(form.get("action"))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        _clean(form.get("method")).lower() != "post"
        or parsed.path != JINDO_PATH
        or query != {"act": ["list"], "m": ["9"]}
    ):
        raise JindoContractError(f"page {page}: list search ownership changed")
    years = form.select('select#searchYear[name="searchYear"]')
    types = form.select('select#searchType[name="searchCondition"]')
    keywords = form.select('input#keyword[name="searchKeyword"]')
    submits = form.select('button[type="submit"]')
    if len(years) != 1 or len(types) != 1 or len(keywords) != 1 or len(submits) != 1:
        raise JindoContractError(f"page {page}: list search controls changed")
    year_options = _options(years[0])
    numeric_years = [int(value) for value, label in year_options[1:] if value == label and value.isdigit()]
    if (
        not year_options
        or year_options[0] != ("0", "전체")
        or len(numeric_years) != len(year_options) - 1
        or numeric_years != sorted(set(numeric_years), reverse=True)
        or _options(types[0]) != (("", "전체보기"), ("titleSub", "제목"), ("title", "과정명"))
        or _clean(keywords[0].get("value"))
        or _clean(submits[0].get_text(" ", strip=True)) != "검색"
    ):
        raise JindoContractError(f"page {page}: empty all-history search contract changed")
    private_links = form.select('a[href*="confirmList"]')
    if len(private_links) != 1:
        raise JindoContractError(f"page {page}: private application boundary changed")
    private = _parse_owned_url(private_links[0].get("href"))
    private_query = parse_qs(private.query, keep_blank_values=True)
    if (
        private.path != JINDO_PATH
        or private_query != {"m": ["10"], "act": ["confirmList"]}
        or _clean(private_links[0].get_text(" ", strip=True)) != "나의 신청목록"
    ):
        raise JindoContractError(f"page {page}: private application route changed")


def _validate_pagination(
    soup: BeautifulSoup, *, requested_page: int, sentinel: bool
) -> int:
    pagers = soup.select("div.paginate")
    if len(pagers) != 1:
        raise JindoContractError(f"page {requested_page}: pagination root changed")
    current = pagers[0].select("div.current_pages > em")
    declared = pagers[0].select("div.current_pages > span")
    if (
        len(current) != 1
        or len(declared) != 1
        or not _clean(current[0].get_text(" ", strip=True)).isdigit()
        or not _clean(declared[0].get_text(" ", strip=True)).isdigit()
    ):
        raise JindoContractError(f"page {requested_page}: pagination declaration changed")
    actual_page = int(_clean(current[0].get_text(" ", strip=True)))
    source_pages = int(_clean(declared[0].get_text(" ", strip=True)))
    if actual_page != requested_page or source_pages < 1:
        raise JindoContractError(f"page {requested_page}: pagination boundary changed")
    pages = pagers[0].select("div.pages > ul > li")
    page_numbers: list[int] = []
    active: list[int] = []
    for item in pages:
        children = item.find_all(["a", "strong"], recursive=False)
        if len(children) != 1:
            raise JindoContractError(f"page {requested_page}: numeric pagination changed")
        node = children[0]
        text = _clean(node.get_text(" ", strip=True))
        if not text.isdigit() or int(text) < 1 or int(text) > source_pages:
            raise JindoContractError(f"page {requested_page}: numeric page label changed")
        number = int(text)
        page_numbers.append(number)
        if node.name == "strong":
            active.append(number)
        elif _pagination_link_page(node.get("href")) != number:
            raise JindoContractError(f"page {requested_page}: numeric page link changed")
    if (
        not page_numbers
        or len(page_numbers) != len(set(page_numbers))
        or page_numbers != sorted(page_numbers)
        or (not sentinel and active != [requested_page])
        or (sentinel and active)
    ):
        raise JindoContractError(f"page {requested_page}: active page marker changed")
    for link in pagers[0].select("div.page_ctrl a[href]"):
        linked = _pagination_link_page(link.get("href"))
        if linked < 1 or linked > source_pages:
            raise JindoContractError(f"page {requested_page}: boundary control changed")
    return source_pages


def _date_range(value: Any, identity: str, label: str) -> tuple[date, date, str]:
    cleaned = _clean(value)
    match = _DATE_RANGE_RE.fullmatch(cleaned)
    if match is None:
        raise JindoContractError(f"course {identity}: {label} changed")
    try:
        start = date.fromisoformat(match.group("start"))
        end = date.fromisoformat(match.group("end"))
    except ValueError as exc:
        raise JindoContractError(f"course {identity}: invalid {label}") from exc
    if end < start:
        raise JindoContractError(f"course {identity}: reversed {label}")
    return start, end, f"{start.isoformat()} ~ {end.isoformat()}"


def _capacity(value: Any, identity: str) -> int:
    cleaned = _clean(value)
    if _CAPACITY_RE.fullmatch(cleaned) is None:
        raise JindoContractError(f"course {identity}: capacity changed")
    parsed = int(cleaned.replace(",", ""))
    if parsed < 1:
        raise JindoContractError(f"course {identity}: invalid capacity")
    return parsed


def _parse_list_row(item: Any, page: int) -> dict[str, Any]:
    links = item.find_all("a", recursive=False, href=True)
    if len(links) != 1:
        raise JindoContractError(f"page {page}: course card link changed")
    identity, raw_url = _detail_link(links[0].get("href"))
    data = links[0].select(":scope > div.data")
    if len(data) != 1:
        raise JindoContractError(f"course {identity}: card data wrapper changed")
    display = data[0].select(":scope > span.tit")
    info = data[0].select(":scope > ul.info")
    if len(display) != 1 or len(info) != 1:
        raise JindoContractError(f"course {identity}: card title/info schema changed")
    display_title = _clean(display[0].get_text(" ", strip=True))
    values: dict[str, str] = {}
    statuses: list[str] = []
    for li in info[0].find_all("li", recursive=False):
        labels = li.find_all("em", recursive=False)
        if labels:
            if len(labels) != 1:
                raise JindoContractError(f"course {identity}: card label changed")
            label = _clean(labels[0].get_text(" ", strip=True))
            clone = BeautifulSoup(str(li), "lxml").find("li")
            clone.find("em", recursive=False).extract()
            value = _clean(clone.get_text(" ", strip=True))
            if label in values or label not in {"과정명", "교육기간", "교육장소", "모집인원"}:
                raise JindoContractError(f"course {identity}: card field changed")
            values[label] = value
        else:
            status_nodes = li.select(":scope > span.status")
            classes = set(li.get("class") or ())
            if len(status_nodes) != 1 or "progress" not in classes:
                raise JindoContractError(f"course {identity}: status row changed")
            statuses.append(_clean(status_nodes[0].get_text(" ", strip=True)))
    if set(values) != {"과정명", "교육기간", "교육장소", "모집인원"} or len(statuses) != 1:
        raise JindoContractError(f"course {identity}: card fields are incomplete")
    title = values["과정명"]
    venue = values["교육장소"]
    source_status = statuses[0]
    status = JINDO_STATUS_MAP.get(source_status, "")
    start, end, period = _date_range(values["교육기간"], identity, "education period")
    capacity_total = _capacity(values["모집인원"], identity)
    if not title or not display_title or not venue or not status:
        raise JindoContractError(f"course {identity}: required card value changed")
    return {
        "identity": identity,
        "source_page": page,
        "title": title,
        "display_title": display_title,
        "venue": venue,
        "source_status": source_status,
        "status": status,
        "start": start,
        "end": end,
        "period": period,
        "capacity_total": capacity_total,
        "raw_url": raw_url,
    }


def _parse_list_page(soup: BeautifulSoup, page: int, *, sentinel: bool = False) -> _ListPage:
    titles = soup.select("head > title")
    if len(titles) != 1 or _clean(titles[0].get_text(" ", strip=True)) != "교육일정 안내 : 진도 평생학습관":
        raise JindoContractError(f"page {page}: official list title changed")
    _validate_search(soup, page)
    roots = soup.select("div.proWrap > ul.proList")
    if len(roots) != 1:
        raise JindoContractError(f"page {page}: official course list changed")
    items = roots[0].find_all("li", recursive=False)
    rows = tuple(_parse_list_row(item, page) for item in items)
    empty_nodes = soup.select("div.no_data")
    if rows:
        if empty_nodes:
            raise JindoContractError(f"page {page}: mixed course/empty markers")
        empty_marker = False
    else:
        if len(empty_nodes) != 1 or _clean(empty_nodes[0].get_text(" ", strip=True)) != "해당 데이터가 없습니다.":
            raise JindoContractError(f"page {page}: explicit empty marker changed")
        empty_marker = True
    source_pages = _validate_pagination(soup, requested_page=page, sentinel=sentinel)
    if sentinel != empty_marker:
        raise JindoContractError(f"page {page}: data/sentinel expectation changed")
    if not sentinel and page < source_pages and len(rows) != JINDO_PAGE_SIZE:
        raise JindoContractError(f"page {page}: non-final page size changed")
    if not sentinel and page == source_pages and not 1 <= len(rows) <= JINDO_PAGE_SIZE:
        raise JindoContractError(f"page {page}: final page size changed")
    return _ListPage(page, source_pages, rows, empty_marker)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("display_title")),
            _clean(row.get("venue")),
            _clean(row.get("source_status")),
            _clean(row.get("period")),
            _clean(row.get("capacity_total")),
        )
        for row in rows
    )


def _safe_detail_values(scope: Any, identity: str) -> dict[str, str]:
    lists = scope.select(":scope > ul")
    if len(lists) != 1:
        raise JindoContractError(f"course {identity}: detail field list changed")
    items = lists[0].find_all("li", recursive=False)
    labels: list[str] = []
    safe: dict[str, str] = {}
    for item in items:
        headings = item.find_all("em", recursive=False)
        spans = item.find_all("span", recursive=False)
        if len(headings) != 1 or len(spans) != 1:
            raise JindoContractError(f"course {identity}: detail row schema changed")
        label = _clean(headings[0].get_text(" ", strip=True))
        labels.append(label)
        if label in _SAFE_DETAIL_LABELS:
            value = _clean(spans[0].get_text(" ", strip=True))
            if not value or len(value) > 300 or _PHONE_RE.search(value) or _EMAIL_RE.search(value):
                raise JindoContractError(f"course {identity}: safe detail {label} changed")
            safe[label] = value
    if tuple(labels) != _DETAIL_LABELS or set(safe) != _SAFE_DETAIL_LABELS:
        raise JindoContractError(f"course {identity}: detail labels changed")
    return safe


def _script_identity_contract(soup: BeautifulSoup, identity: str) -> None:
    scripts = [script.string or script.get_text(" ", strip=True) for script in soup.find_all("script")]
    info_pattern = re.compile(
        r"thisForm\.find\(\s*['\"]\[name=infoId\]['\"]\s*\)\.val\(\s*['\"]"
        + re.escape(identity)
        + r"['\"]\s*\)"
    )
    return_pattern = re.compile(
        r"thisForm\.find\(\s*['\"]\[name=returnQueryString\]['\"]\s*\)\.val\(\s*['\"]"
        + re.escape(f"act=view&infoId={identity}&m=9")
        + r"['\"]\s*\)"
    )
    matching = [text for text in scripts if "function fn_eduReser()" in text and info_pattern.search(text) and return_pattern.search(text)]
    if len(matching) != 1:
        raise JindoContractError(f"course {identity}: JavaScript application identity binding changed")


def _branch_code(venue: str) -> str:
    digest = hashlib.sha1(_normalized(venue).encode("utf-8")).hexdigest()[:12]
    return f"jindo:{digest}"


def _parse_detail(
    soup: BeautifulSoup, listed: Mapping[str, Any], cutoff: date
) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    titles = soup.select("head > title")
    if len(titles) != 1 or _clean(titles[0].get_text(" ", strip=True)) != "교육일정 안내 : 진도 평생학습관":
        raise JindoContractError(f"course {identity}: official detail title changed")
    scopes = soup.select("div.proInfo")
    if len(scopes) != 1:
        raise JindoContractError(f"course {identity}: primary detail scope changed")
    headings = scopes[0].find_all("h4", recursive=False)
    if len(headings) != 1:
        raise JindoContractError(f"course {identity}: detail heading changed")
    values = _safe_detail_values(scopes[0], identity)
    title = values["과정명"]
    institution = values["교육기관"]
    venue = values["교육장소"]
    capacity_total = _capacity(values["모집인원"], identity)
    apply_start, apply_end, apply_period = _date_range(values["신청기간"], identity, "application period")
    start, end, period = _date_range(values["교육기간"], identity, "education period")
    schedule = values["운영시간"]
    if (
        _clean(headings[0].get_text(" ", strip=True)) != title
        or title != _clean(listed.get("title"))
        or venue != _clean(listed.get("venue"))
        or capacity_total != int(listed.get("capacity_total") or 0)
        or (start, end, period) != (listed.get("start"), listed.get("end"), _clean(listed.get("period")))
    ):
        raise JindoContractError(f"course {identity}: list/detail safe fields mismatch")
    _script_identity_contract(soup, identity)
    controls = scopes[0].select(":scope > p.lkBtn > a")
    if len(controls) != 1:
        raise JindoContractError(f"course {identity}: visible application control changed")
    control_text = _clean(controls[0].get_text(" ", strip=True))
    control_href = re.sub(r"\s+", "", _clean(controls[0].get("href"))).casefold()
    status = _clean(listed.get("status"))
    visible_control = False
    if status == "OPEN":
        if control_text not in {"신청하기", "신청중"} or control_href != "javascript:fn_edureser();":
            raise JindoContractError(f"course {identity}: open application control changed")
        if not (apply_start <= cutoff <= apply_end):
            raise JindoContractError(f"course {identity}: open status/application period conflict")
        visible_control = True
    elif status == "SCHEDULED":
        if control_text != "신청예정" or control_href != "javascript:;" or cutoff >= apply_start:
            raise JindoContractError(f"course {identity}: scheduled application control changed")
    elif status == "CLOSED":
        if control_text != "신청마감" or control_href != "javascript:;":
            raise JindoContractError(f"course {identity}: closed application control changed")
    else:
        raise JindoContractError(f"course {identity}: normalized status changed")
    return_links = soup.select("p.btnCenter > a.button_list[href]")
    if len(return_links) != 1:
        raise JindoContractError(f"course {identity}: list return control changed")
    returned = _parse_owned_url(return_links[0].get("href"))
    returned_query = parse_qs(returned.query, keep_blank_values=True)
    if (
        returned.path != JINDO_PATH
        or returned_query != {"searchKeyword": [""], "searchCondition": [""], "m": ["9"]}
        or _clean(return_links[0].get_text(" ", strip=True)) != "목록보기"
    ):
        raise JindoContractError(f"course {identity}: detail ownership changed")
    application_url = _clean(listed.get("raw_url")) if visible_control else ""
    return {
        "provider": JINDO_PROVIDER,
        "provider_course_id": f"{JINDO_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": venue,
        "branch_code": _branch_code(venue),
        "branch_url": JINDO_URL,
        "preserve_branch": True,
        "category": "평생학습",
        "program_type": "교육",
        "raw_url": _clean(listed.get("raw_url")),
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if visible_control else "INFO_ONLY",
        "application_method": "온라인" if visible_control else "",
        "application_methods": ["온라인"] if visible_control else [],
        "reservation_available": visible_control,
        "status": status,
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": apply_period,
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": schedule,
        "capacity": f"{capacity_total}명",
        "capacity_total": capacity_total,
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": JINDO_PARSER,
        "municipality_code": JINDO_MUNICIPALITY_CODE,
        "municipality_full_name": JINDO_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": int(listed.get("source_page") or 0),
            "source_status": _clean(listed.get("source_status")),
            "source_display_title": _clean(listed.get("display_title")),
            "source_period": period,
            "source_venue": venue,
            "source_capacity_total": capacity_total,
            "detail_institution": institution,
            "detail_schedule": schedule,
            "detail_verified": True,
            "visible_application_control_present": visible_control,
            "application_control_contract": (
                "identity_bound_js_modal" if visible_control else "verified_no_visible_control"
            ),
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded PII-safe allowlist")
    payload = repr({key: value for key, value in row.items() if key not in {"raw_url", "application_url"}})
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
        "semantic_duplicate_group_count": 0,
        "historical_semantic_duplicate_group_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": error,
        "municipality_code": JINDO_MUNICIPALITY_CODE,
        "municipality_name": JINDO_MUNICIPALITY_NAME,
        "canonical_url": JINDO_URL,
        "ownership_scope": JINDO_OWNERSHIP_SCOPE,
        "candidate_audit": {key: dict(value) for key, value in JINDO_CANDIDATE_AUDIT.items()},
        "discovery_audit": dict(JINDO_DISCOVERY_AUDIT),
        "duplicate_provider_aliases": [JINDO_DUPLICATE_PROVIDER],
        "municipality_coverage": [JINDO_MUNICIPALITY_CODE],
        "pii_fields_discarded": list(JINDO_PII_FIELDS_DISCARDED),
        "pii_payload_persisted": False,
    }


def collect_jindo_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    max_workers: int = JINDO_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Jindo lifelong snapshot."""

    meta = _base_meta()
    if not is_jindo_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Jindo lifelong catalogue owner"
        return [], JINDO_PARSER, meta
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
        meta.update({"source_cap_reached": True, "configured_collection_error": "invalid collection limits"})
        return [], JINDO_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], JINDO_PARSER, meta
    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    workers = min(max_workers, JINDO_MAX_WORKERS)
    meta["network_concurrency"] = workers

    def fetch_list(page: int, *, sentinel: bool = False) -> _ListPage:
        soup = _fetch_soup(
            jindo_list_url(page),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return _parse_list_page(soup, page, sentinel=sentinel)

    try:
        first = fetch_list(1)
        meta["list_requests"] = 1
        meta["pages"] = 1
    except Exception as exc:
        meta["configured_collection_error"] = f"page 1: {type(exc).__name__}: {_clean(exc)}"
        return [], JINDO_PARSER, meta
    source_pages = first.source_pages
    required = source_pages + 3
    meta.update(
        {
            "declared_data_pages": source_pages,
            "required_list_requests": required,
            "sentinel_page": source_pages + 1,
        }
    )
    if required > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": f"max_pages cap allows {max_pages} of {required} required list requests",
            }
        )
        return [], JINDO_PARSER, meta

    pages: dict[int, _ListPage] = {1: first}
    errors: list[str] = []
    for page in range(2, source_pages + 1):
        try:
            pages[page] = fetch_list(page)
            meta["list_requests"] += 1
            meta["pages"] += 1
        except Exception as exc:
            errors.append(f"page {page}: {type(exc).__name__}: {_clean(exc)}")
    sentinel: Optional[_ListPage] = None
    first_recheck: Optional[_ListPage] = None
    last_recheck: Optional[_ListPage] = None
    for kind, page, is_sentinel in (
        ("sentinel", source_pages + 1, True),
        ("first_recheck", 1, False),
        ("last_recheck", source_pages, False),
    ):
        try:
            parsed = fetch_list(page, sentinel=is_sentinel)
            meta["list_requests"] += 1
            meta["pages"] += 1
            if kind == "sentinel":
                sentinel = parsed
            elif kind == "first_recheck":
                first_recheck = parsed
            else:
                last_recheck = parsed
        except Exception as exc:
            errors.append(f"{kind} page {page}: {type(exc).__name__}: {_clean(exc)}")

    for page, parsed in pages.items():
        if parsed.source_pages != source_pages:
            errors.append(f"page {page}: declared page boundary changed")
        if not parsed.rows or parsed.empty_marker:
            errors.append(f"page {page}: declared data page is empty")
    if sentinel is None:
        errors.append("immediate post-last sentinel response missing")
    elif sentinel.source_pages != source_pages or sentinel.rows or not sentinel.empty_marker:
        errors.append("immediate post-last sentinel is not stable empty")
    else:
        meta["sentinel_requests"] = 1
    if first_recheck is None or last_recheck is None:
        errors.append("first/last stability recheck response missing")
    else:
        meta["stability_rechecks"] = 2
        if first_recheck.source_pages != source_pages or _page_signature(first_recheck.rows) != _page_signature(first.rows):
            errors.append("first-page stability recheck changed")
        last = pages.get(source_pages)
        if last is None or last_recheck.source_pages != source_pages or _page_signature(last_recheck.rows) != _page_signature(last.rows):
            errors.append("last-page stability recheck changed")

    listed = [row for page in range(1, source_pages + 1) for row in pages.get(page, _ListPage(page, source_pages, (), True)).rows]
    identities = [_clean(row.get("identity")) for row in listed]
    raw_urls = [_clean(row.get("raw_url")) for row in listed]
    identity_duplicates = len(identities) - len(set(identities))
    raw_url_duplicates = len(raw_urls) - len(set(raw_urls))
    if identity_duplicates:
        errors.append(f"{identity_duplicates} duplicate official identities")
    if raw_url_duplicates:
        errors.append(f"{raw_url_duplicates} duplicate canonical detail URLs")
    expired_status_conflicts = [row["identity"] for row in listed if row["end"] < cutoff and row["status"] in {"OPEN", "SCHEDULED"}]
    if expired_status_conflicts:
        errors.append("expired rows expose active application status: " + ",".join(expired_status_conflicts))
    current_listed = [row for row in listed if row["end"] >= cutoff]
    historical_listed = [row for row in listed if row["end"] < cutoff]
    historical_semantic = Counter(
        (_normalized(row.get("title")), _clean(row.get("period")), _normalized(row.get("venue")))
        for row in historical_listed
    )
    historical_semantic_groups = sum(value > 1 for value in historical_semantic.values())
    list_complete = bool(
        not errors
        and len(pages) == source_pages
        and meta["list_requests"] == required
        and meta["sentinel_requests"] == 1
        and meta["stability_rechecks"] == 2
    )
    meta.update(
        {
            "data_pages": len(pages),
            "page_counts": {page: len(parsed.rows) for page, parsed in pages.items()},
            "source_rows": len(listed),
            "current_source_count": len(current_listed),
            "expired_count": len(historical_listed),
            "identity_duplicate_count": identity_duplicates,
            "raw_url_duplicate_count": raw_url_duplicates,
            "historical_semantic_duplicate_group_count": historical_semantic_groups,
            "source_status_counts": dict(Counter(_clean(row.get("source_status")) for row in listed)),
            "source_exact_branch_counts": dict(Counter(_clean(row.get("venue")) for row in listed)),
            "current_normalized_status_counts": dict(Counter(_clean(row.get("status")) for row in current_listed)),
            "pagination_detected": source_pages > 1,
            "pagination_complete": list_complete,
        }
    )
    if not list_complete:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], JINDO_PARSER, meta
    if len(current_listed) > detail_limit:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": f"detail_limit cap allows {detail_limit} of {len(current_listed)} required current details",
            }
        )
        return [], JINDO_PARSER, meta

    meta["detail_attempts"] = len(current_listed)
    detailed: dict[str, dict[str, Any]] = {}
    detail_errors: list[str] = []

    def fetch_detail(listed_row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        identity = _clean(listed_row.get("identity"))
        soup = _fetch_soup(
            _clean(listed_row.get("raw_url")),
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
                    raise JindoContractError("duplicate parsed detail identity")
                detailed[parsed_identity] = parsed
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                detail_errors.append(f"detail {identity}: {type(exc).__name__}: {_clean(exc)}")
    meta["detail_errors"] = len(detail_errors)
    errors.extend(detail_errors)
    details_complete = bool(
        not detail_errors
        and meta["detail_pages"] == len(current_listed)
        and len(detailed) == len(current_listed)
    )
    ordered = [detailed[row["identity"]] for row in current_listed if row["identity"] in detailed]
    semantic = Counter(
        (_normalized(row.get("title")), _clean(row.get("period")), _normalized(row.get("branch")))
        for row in ordered
    )
    semantic_groups = sum(value > 1 for value in semantic.values())
    if semantic_groups:
        errors.append(f"{semantic_groups} current semantic duplicate groups")
    controls_complete = bool(
        details_complete
        and all(
            (row.get("status") == "OPEN")
            == bool(row.get("raw_fields", {}).get("visible_application_control_present"))
            for row in ordered
        )
    )
    result: list[dict[str, Any]] = []
    if details_complete and controls_complete and not semantic_groups and not errors:
        for row in ordered:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(ordered))
            except Exception as exc:
                errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                result = []
            if len(result) != len(ordered):
                errors.append(f"dedupe changed official identity cardinality {len(ordered)} to {len(result)}")
                result = []
    snapshot_complete = bool(
        list_complete and details_complete and controls_complete and not semantic_groups and not errors
    )
    if not snapshot_complete:
        result = []
    meta.update(
        {
            "returned_count": len(result),
            "semantic_duplicate_group_count": semantic_groups,
            "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
            "current_branch_names": sorted({_clean(row.get("branch")) for row in result if _clean(row.get("branch"))}),
            "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
            "visible_public_application_control_count": sum(bool(row.get("raw_fields", {}).get("visible_application_control_present")) for row in ordered),
            "details_complete": details_complete,
            "application_controls_complete": controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "no_current_data": bool(snapshot_complete and not current_listed),
            "no_current_reason": (
                "the complete official Jindo lifelong catalogue has no current/future courses"
                if snapshot_complete and not current_listed
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, JINDO_PARSER, meta


collect = collect_jindo_education
collect_jindo_target = collect_jindo_education


__all__ = [
    "JINDO_CANDIDATE_AUDIT",
    "JINDO_DISCOVERY_AUDIT",
    "JINDO_DUPLICATE_PROVIDER",
    "JINDO_INTRO_CANDIDATE_ID",
    "JINDO_MUNICIPALITY_CODE",
    "JINDO_MUNICIPALITY_NAME",
    "JINDO_PAGE_SIZE",
    "JINDO_PARSER",
    "JINDO_PROVIDER",
    "JINDO_ROOT_CANDIDATE_ID",
    "JINDO_TOUR_CANDIDATE_ID",
    "JINDO_URL",
    "collect",
    "collect_jindo_education",
    "collect_jindo_target",
    "is_jindo_candidate_alias",
    "is_jindo_education_target",
    "is_target",
    "jindo_detail_url",
    "jindo_list_url",
]
