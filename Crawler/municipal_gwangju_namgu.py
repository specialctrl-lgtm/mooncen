"""Fail-closed education catalogue collector for Gwangju Nam-gu.

The promoted provider currently points at one historical notice.  That URL is
kept as the routing identity, but it is not treated as a catalogue.  The
municipality's executable education inventory is split between:

* the Nam-gu lifelong-learning ``/lecture.es`` catalogue, and
* the resident information-education application cards on the main site.

The lifelong catalogue is traversed from its unfiltered controller because
the two menu entry points expose only partial category totals and their pager
then drops the category filter.  The controller declares 204 source rows over
21 ten-row pages.  Four old rows are explicit ``진행중 강좌가 없습니다``
placeholders; they remain part of source cardinality but are never courses.

Every data page, the immediately empty post-last page, page one, and the last
page are verified before a snapshot is emitted.  The information-education
cards are also fetched twice.  Only current/future lifelong rows are opened.
Application controls are inspected but applicant/login/form routes are never
requested.  Inquiry phone/e-mail values, instructor/staff names, attachments,
free-form descriptions, source HTML, and applicant data are not persisted.

The integrated library programme service is a separate owner.  The annual
smartphone schedule and resident-centre tables are discovery evidence only:
the former is phone-only without a live per-course status/control and the
latter has no course dates, so neither can pass the current-course contract.
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
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GWANGJU_NAMGU_PROVIDER = "MUNI_WWW_NAMGU_GWANGJU_KR_8A2E3D93"
GWANGJU_NAMGU_CANDIDATE_ID = "MUNI_IR_9697CDA1240A"
GWANGJU_NAMGU_MUNICIPALITY_CODE = "1227000000"
GWANGJU_NAMGU_MUNICIPALITY_NAME = "전남광주통합특별시 남구"

GWANGJU_NAMGU_MAIN_HOST = "www.namgu.gwangju.kr"
GWANGJU_NAMGU_LIFELONG_HOST = "lll.namgu.gwangju.kr"
GWANGJU_NAMGU_CANDIDATE_URL = "https://www.namgu.gwangju.kr/board.es?mid=a10604010000&bid=0001&act=view&list_no=8474"
GWANGJU_NAMGU_INFORMATION_URL = "https://www.namgu.gwangju.kr/education.es?mid=a10104010300"
GWANGJU_NAMGU_INFORMATION_GUIDE_URL = "https://www.namgu.gwangju.kr/menu.es?mid=a10104010100"
GWANGJU_NAMGU_DIGITAL_SCHEDULE_URL = "https://www.namgu.gwangju.kr/menu.es?mid=a10104060000"
GWANGJU_NAMGU_RESIDENT_CENTRE_URL = "https://www.namgu.gwangju.kr/menu.es?mid=a10104020000"
GWANGJU_NAMGU_LIFELONG_ROOT_URL = "https://lll.namgu.gwangju.kr/"
GWANGJU_NAMGU_LIFELONG_MID = "a10202010100"
GWANGJU_NAMGU_LIFELONG_PATH = "/lecture.es"
GWANGJU_NAMGU_LIFELONG_URL = "https://lll.namgu.gwangju.kr/lecture.es?mid=a10202010100&nPage=1"
GWANGJU_NAMGU_LIBRARY_URL = "https://lib.namgu.gwangju.kr/main/clturReq/1"
GWANGJU_NAMGU_SPORTS_URL = "https://www.namgu.gwangju.kr/sports/reserveSearchList.es"

GWANGJU_NAMGU_PAGE_SIZE = 10
GWANGJU_NAMGU_MAX_WORKERS = 8
GWANGJU_NAMGU_MAX_HTML_BYTES = 3_000_000
GWANGJU_NAMGU_PLACEHOLDER_TITLE = "진행중 강좌가 없습니다"
GWANGJU_NAMGU_INFORMATION_BRANCH = "남구청 교육장"
GWANGJU_NAMGU_PARSER = (
    "gwangju_namgu_lifelong_all_controller_pages+information_application_cards+"
    "empty_post_last+stable_first_last_and_information+current_details+"
    "course_bound_application_control_no_form_fetch+pii_allowlist"
)
GWANGJU_NAMGU_OWNERSHIP_SCOPE = "namgu_official_lifelong_and_resident_information_education_catalogues"

GWANGJU_NAMGU_ALIAS_URLS: tuple[str, ...] = (
    GWANGJU_NAMGU_INFORMATION_URL,
    GWANGJU_NAMGU_INFORMATION_GUIDE_URL,
    GWANGJU_NAMGU_LIFELONG_ROOT_URL,
    GWANGJU_NAMGU_LIFELONG_URL,
)
GWANGJU_NAMGU_SEPARATE_OWNER_URLS: Mapping[str, str] = {
    "namgu_integrated_library_programmes": GWANGJU_NAMGU_LIBRARY_URL,
    "namgu_sports_facility_reservations": GWANGJU_NAMGU_SPORTS_URL,
    "external_all_education_platform": "https://gjnamgu.alledu.co.kr/",
}
GWANGJU_NAMGU_EXCLUDED_URLS: Mapping[str, str] = {
    "phone_only_digital_schedule_without_live_status": (GWANGJU_NAMGU_DIGITAL_SCHEDULE_URL),
    "undated_resident_centre_programme_tables": GWANGJU_NAMGU_RESIDENT_CENTRE_URL,
    "lifelong_notice_board_not_course_catalogue": ("https://lll.namgu.gwangju.kr/board.es?mid=a10206000000&bid=0001"),
}

GWANGJU_NAMGU_RESIDENT_CENTRE_BRANCHES: tuple[str, ...] = (
    "양림동",
    "방림1동",
    "방림2동",
    "봉선1동",
    "봉선2동",
    "사직동",
    "월산동",
    "월산4동",
    "월산5동",
    "백운1동",
    "백운2동",
    "주월1동",
    "주월2동",
    "진월동",
    "효덕동",
    "송암동",
    "대촌동",
)
GWANGJU_NAMGU_LIBRARY_BRANCHES: tuple[str, ...] = (
    "문화정보도서관",
    "푸른길도서관",
    "청소년도서관",
    "효천어울림도서관",
)
GWANGJU_NAMGU_LIBRARY_CATALOGUE_TABS: tuple[str, ...] = (
    "문화정보",
    "푸른길",
    "청소년",
    "효천어울림",
    "작은도서관",
    "기타",
    "전집대출",
)

GWANGJU_NAMGU_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    GWANGJU_NAMGU_CANDIDATE_ID: {
        "provider": GWANGJU_NAMGU_PROVIDER,
        "url": GWANGJU_NAMGU_CANDIDATE_URL,
        "decision": "retain_existing_owner_expand_notice_seed_to_two_catalogues",
        "reason": "the promoted URL is one expired notice, not a course list",
    }
}

GWANGJU_NAMGU_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "registered_seed_url": GWANGJU_NAMGU_CANDIDATE_URL,
    "canonical_catalogues": {
        "lifelong": GWANGJU_NAMGU_LIFELONG_URL,
        "information": GWANGJU_NAMGU_INFORMATION_URL,
    },
    "source_totals": {"lifelong": 204, "information": 2},
    "lifelong_data_pages": 21,
    "lifelong_empty_sentinel_page": 22,
    "lifelong_unique_identities": 204,
    "lifelong_placeholder_rows": 4,
    "lifelong_latest_end_date": "2025-12-02",
    "current_future_counts": {"lifelong": 0, "information": 2},
    "current_future_total": 2,
    "current_status_counts": {"SCHEDULED": 2},
    "current_branch_counts": {GWANGJU_NAMGU_INFORMATION_BRANCH: 2},
    "current_details_verified": 2,
    "visible_public_application_controls": 0,
    "digital_schedule_rows": 18,
    "digital_schedule_current_future_rows": 8,
    "digital_schedule_exclusion": "phone_only_no_live_per_course_status_or_control",
    "resident_centre_rows": 139,
    "resident_centre_branches": GWANGJU_NAMGU_RESIDENT_CENTRE_BRANCHES,
    "resident_centre_exclusion": "undated_evergreen_table_cannot_apply_current_cutoff",
    "separate_library_total": 1162,
    "separate_library_pages": 78,
    "separate_library_branches": GWANGJU_NAMGU_LIBRARY_BRANCHES,
    "separate_library_catalogue_tabs": GWANGJU_NAMGU_LIBRARY_CATALOGUE_TABS,
    "separate_library_status_counts": {
        "접수예정": 4,
        "접수중": 0,
        "대기자접수": 0,
        "접수마감": 0,
        "종료": 1158,
    },
    "conclusion": (
        "keep the existing main-site provider for the two municipal catalogues; "
        "schedule the integrated library only under a separate owner"
    ),
}

GWANGJU_NAMGU_PII_FIELDS_DISCARDED: tuple[str, ...] = (
    "문의사항/문의전화",
    "강사/담당자/작성자",
    "첨부파일/다운로드",
    "기타/강좌내용/공지 본문",
    "신청자 이름/주소/전화/e-mail",
    "로그인/본인확인 payload",
    "source HTML",
)


class GwangjuNamguContractError(ValueError):
    """Raised when an audited Nam-gu source no longer matches its contract."""


@dataclass(frozen=True)
class _LifelongPage:
    requested_page: int
    total: int
    last_page: int
    rows: tuple[dict[str, Any], ...]
    empty_marker: bool


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"^[1-9]\d*$")
_DATE_RANGE_RE = re.compile(
    r"^(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})\s*~\s*"
    r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})$"
)
_CAPACITY_RE = re.compile(r"^접수\s*(\d+)\s*\(정원\s*(\d+)\)$")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[\s().-]*\d{3,4}[\s.-]*\d{4}|"
    r"0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIFELONG_HEADERS = ("강좌명", "강좌기간", "교육기관", "수강료")
_LIFELONG_DETAIL_LABELS = frozenset(
    {
        "강좌기간",
        "신청기간",
        "교육기관",
        "대상",
        "접수방법",
        "수강료",
        "문의사항",
        "첨부파일",
        "기타",
    }
)
_INFORMATION_FIELDS = frozenset({"장소", "접수기간", "교육기간", "시간", "인원"})
_INFORMATION_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "신청가능": "OPEN",
    "접수마감": "CLOSED",
    "접수종료": "CLOSED",
}
_INFORMATION_CLASS_STATUS: Mapping[str, str] = {
    "state01": "SCHEDULED",
    "state02": "OPEN",
    "state03": "CLOSED",
}

_ALLOWED_ROW_KEYS = frozenset(
    {
        "provider",
        "provider_course_id",
        "prefer_incoming_provider_course_id",
        "title",
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
        "collection_type",
        "municipality_code",
        "municipality_name",
        "raw_fields",
    }
)
_ALLOWED_RAW_KEYS = frozenset(
    {
        "parser",
        "source_code",
        "source_identity",
        "source_page",
        "source_status",
        "source_application_method",
        "detail_verified",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "instructor",
        "teacher",
        "manager",
        "staff",
        "contact",
        "phone",
        "email",
        "attachment",
        "attachments",
        "description",
        "source_html",
        "raw_html",
        "application_payload",
        "applicant",
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


def _canonical_public_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
        or parsed.params
    ):
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return f"https://{parsed.hostname.rstrip('.').lower()}{parsed.path or '/'}" + (f"?{query}" if query else "")


def is_gwangju_namgu_education_target(target: Any) -> bool:
    compared = _canonical_public_url(_target_value(target, "url"))
    return bool(
        _clean(_target_value(target, "provider")) == GWANGJU_NAMGU_PROVIDER
        and compared
        in {
            _canonical_public_url(GWANGJU_NAMGU_CANDIDATE_URL),
            _canonical_public_url(GWANGJU_NAMGU_INFORMATION_URL),
            _canonical_public_url(GWANGJU_NAMGU_LIFELONG_URL),
        }
    )


def is_gwangju_namgu_alias_target(target: Any) -> bool:
    compared = _canonical_public_url(_target_value(target, "url"))
    return bool(compared and compared in {_canonical_public_url(url) for url in GWANGJU_NAMGU_ALIAS_URLS})


def is_gwangju_namgu_excluded_target(target: Any) -> bool:
    compared = _canonical_public_url(_target_value(target, "url"))
    excluded = tuple(GWANGJU_NAMGU_EXCLUDED_URLS.values()) + tuple(GWANGJU_NAMGU_SEPARATE_OWNER_URLS.values())
    return bool(compared and compared in {_canonical_public_url(url) for url in excluded})


def is_target(target: Any) -> bool:
    return is_gwangju_namgu_education_target(target)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise ValueError("today must be an ISO date") from exc


def _date_range(value: Any, label: str) -> tuple[str, str]:
    matched = _DATE_RANGE_RE.fullmatch(_clean(value))
    if not matched:
        raise GwangjuNamguContractError(f"{label} is not a two-date range")
    numbers = [int(part) for part in matched.groups()]
    try:
        start = date(numbers[0], numbers[1], numbers[2])
        end = date(numbers[3], numbers[4], numbers[5])
    except ValueError as exc:
        raise GwangjuNamguContractError(f"{label} contains an invalid date") from exc
    if end < start:
        raise GwangjuNamguContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _branch_code(branch: str) -> str:
    return "GNAMGU-" + hashlib.sha256(branch.encode("utf-8")).hexdigest()[:12].upper()


def _information_identity(title: str, start: str, end: str, schedule: str, venue: str) -> str:
    seed = "\x1f".join((title, start, end, schedule, venue))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def _lifelong_page_url(page: int) -> str:
    return f"https://{GWANGJU_NAMGU_LIFELONG_HOST}{GWANGJU_NAMGU_LIFELONG_PATH}?" + urlencode(
        {"mid": GWANGJU_NAMGU_LIFELONG_MID, "nPage": page}
    )


def _lifelong_detail_url(identity: str) -> str:
    return f"https://{GWANGJU_NAMGU_LIFELONG_HOST}{GWANGJU_NAMGU_LIFELONG_PATH}?" + urlencode(
        {
            "mid": GWANGJU_NAMGU_LIFELONG_MID,
            "act": "view",
            "seq": identity,
        }
    )


def _allowed_request_url(url: str) -> bool:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
        or parsed.params
    ):
        return False
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query, keep_blank_values=True)
    if host == GWANGJU_NAMGU_MAIN_HOST and parsed.path == "/education.es":
        return query == {"mid": ["a10104010300"]}
    if host != GWANGJU_NAMGU_LIFELONG_HOST or parsed.path != GWANGJU_NAMGU_LIFELONG_PATH:
        return False
    if query.get("mid") != [GWANGJU_NAMGU_LIFELONG_MID]:
        return False
    if query.get("act") == ["view"]:
        return set(query) == {"mid", "act", "seq"} and bool(_POSITIVE_ID_RE.fullmatch(query.get("seq", [""])[0]))
    return set(query) == {"mid", "nPage"} and bool(_POSITIVE_ID_RE.fullmatch(query.get("nPage", [""])[0]))


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": "mooncen-gwangju-namgu/1.0"})
    return session


def _default_fetcher(session: requests.Session, url: str, timeout: int) -> requests.Response:
    if not _allowed_request_url(url):
        raise GwangjuNamguContractError("attempted fetch outside audited routes")
    return session.get(url, timeout=timeout, allow_redirects=False)


def _response_soup(response: Any, requested_url: str) -> BeautifulSoup:
    if isinstance(response, str):
        payload = response.encode("utf-8")
        status = 200
        final_url = requested_url
        headers: Mapping[str, Any] = {}
        history: Iterable[Any] = ()
    elif isinstance(response, bytes):
        payload = response
        status = 200
        final_url = requested_url
        headers = {}
        history = ()
    else:
        status = int(getattr(response, "status_code", 0) or 0)
        payload = getattr(response, "content", None)
        if payload is None:
            payload = str(getattr(response, "text", "")).encode("utf-8")
        elif isinstance(payload, str):
            payload = payload.encode("utf-8")
        final_url = _clean(getattr(response, "url", requested_url)) or requested_url
        headers = getattr(response, "headers", {}) or {}
        history = getattr(response, "history", ()) or ()
    if status != 200:
        raise GwangjuNamguContractError(f"HTTP {status}")
    if history:
        raise GwangjuNamguContractError("redirected response is not allowed")
    if _canonical_public_url(final_url) != _canonical_public_url(requested_url):
        raise GwangjuNamguContractError("response URL differs from requested URL")
    if not isinstance(payload, (bytes, bytearray)):
        raise GwangjuNamguContractError("response body is not bytes")
    if not payload or len(payload) > GWANGJU_NAMGU_MAX_HTML_BYTES:
        raise GwangjuNamguContractError("response body size is outside the HTML contract")
    content_type = _clean(headers.get("Content-Type") or headers.get("content-type"))
    if content_type and not any(allowed in content_type.lower() for allowed in ("text/html", "application/xhtml")):
        raise GwangjuNamguContractError("response content type is not HTML")
    try:
        text = bytes(payload).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GwangjuNamguContractError("response is not UTF-8 HTML") from exc
    soup = BeautifulSoup(text, "html.parser")
    if not soup.select_one("#content_detail"):
        raise GwangjuNamguContractError("official content container is missing")
    return soup


def _query_identity(href: str, expected_kind: str) -> str:
    parsed = urlparse(href)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if expected_kind == "lifelong":
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != GWANGJU_NAMGU_LIFELONG_HOST
            or parsed.path != GWANGJU_NAMGU_LIFELONG_PATH
            or query.get("mid") != [GWANGJU_NAMGU_LIFELONG_MID]
            or query.get("act") != ["view"]
            or set(query) != {"mid", "act", "seq"}
        ):
            return ""
        identity = query.get("seq", [""])[0]
        return identity if _POSITIVE_ID_RE.fullmatch(identity) else ""
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != GWANGJU_NAMGU_MAIN_HOST
        or parsed.path != "/education.es"
        or query.get("mid") != ["a10104010300"]
        or query.get("act", [""])[0] not in {"form", "mem_form", "write"}
    ):
        return ""
    identity_keys = [key for key in ("seq", "edu_seq", "edu_no", "list_no") if key in query]
    if len(identity_keys) != 1:
        return ""
    allowed = {"mid", "act", identity_keys[0]}
    if set(query) != allowed:
        return ""
    identity = query.get(identity_keys[0], [""])[0]
    return identity if _POSITIVE_ID_RE.fullmatch(identity) else ""


def _parse_lifelong_page(soup: BeautifulSoup, requested_page: int) -> _LifelongPage:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "평생학습도시 남구" not in title or "평생학습 강좌" not in title:
        raise GwangjuNamguContractError("lifelong page title changed")
    content = soup.select_one("#content_detail")
    container = content.select_one(".program") if content else None
    if not content or not container:
        raise GwangjuNamguContractError("lifelong programme container is missing")
    total_nodes = container.select("p.page_info strong.txt_bold")
    if len(total_nodes) != 1:
        raise GwangjuNamguContractError("lifelong declared total is missing or ambiguous")
    total_text = _clean(total_nodes[0].get_text(" ", strip=True)).replace(",", "")
    if not total_text.isdigit():
        raise GwangjuNamguContractError("lifelong declared total is invalid")
    total = int(total_text)
    last_page = max(1, math.ceil(total / GWANGJU_NAMGU_PAGE_SIZE))

    table = container.select_one("table.tstyle_list")
    if not table:
        raise GwangjuNamguContractError("lifelong list table is missing")
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _LIFELONG_HEADERS:
        raise GwangjuNamguContractError("lifelong table headers changed")

    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody > tr"):
        cells = tr.find_all("td", recursive=False)
        anchor = tr.select_one('a[href*="seq="]')
        if not anchor:
            if "결과가 없습니다" in _clean(tr.get_text(" ", strip=True)):
                continue
            raise GwangjuNamguContractError("lifelong row has no detail identity")
        if len(cells) != 4:
            raise GwangjuNamguContractError("lifelong row width changed")
        detail_url = urljoin(_lifelong_page_url(requested_page), anchor.get("href", ""))
        identity = _query_identity(detail_url, "lifelong")
        if not identity:
            raise GwangjuNamguContractError("lifelong detail route is not course-bound")
        title_text = _clean(anchor.get_text(" ", strip=True))
        if title_text != _clean(cells[0].get_text(" ", strip=True)):
            raise GwangjuNamguContractError(f"lifelong course {identity}: title cell is ambiguous")
        start, end = _date_range(cells[1].get_text(" ", strip=True), "education period")
        branch = _clean(cells[2].get_text(" ", strip=True))
        fee = _clean(cells[3].get_text(" ", strip=True))
        placeholder = title_text == GWANGJU_NAMGU_PLACEHOLDER_TITLE
        if not title_text:
            raise GwangjuNamguContractError(f"lifelong course {identity}: blank title")
        if placeholder:
            if branch or fee:
                raise GwangjuNamguContractError(f"lifelong course {identity}: placeholder contract changed")
        elif not branch:
            raise GwangjuNamguContractError(f"lifelong course {identity}: blank education institution")
        rows.append(
            {
                "source": "lifelong",
                "identity": identity,
                "source_page": requested_page,
                "title": title_text,
                "start_date": start,
                "end_date": end,
                "branch": branch,
                "fee": fee,
                "raw_url": detail_url,
                "placeholder": placeholder,
            }
        )

    empty_marker = "결과가 없습니다" in _clean(container.get_text(" ", strip=True))
    if requested_page <= last_page:
        expected = GWANGJU_NAMGU_PAGE_SIZE
        if requested_page == last_page:
            expected = total - (last_page - 1) * GWANGJU_NAMGU_PAGE_SIZE
        if len(rows) != expected or empty_marker:
            raise GwangjuNamguContractError(f"lifelong page {requested_page}: expected {expected} rows")
        current = content.select_one(".board_pager a.pageNow strong")
        if not current or _clean(current.get_text(" ", strip=True)) != str(requested_page):
            raise GwangjuNamguContractError(f"lifelong page {requested_page}: active pager changed")
        last_anchor = content.select_one(".board_pager a.pageLast[href]")
        if not last_anchor:
            raise GwangjuNamguContractError("lifelong last-page control is missing")
        last_query = parse_qs(urlparse(last_anchor.get("href", "")).query)
        if last_query.get("nPage") != [str(last_page)]:
            raise GwangjuNamguContractError("lifelong last-page control disagrees with total")
    elif rows or not empty_marker:
        raise GwangjuNamguContractError("lifelong post-last page is not explicitly empty")
    return _LifelongPage(requested_page, total, last_page, tuple(rows), empty_marker)


def _lifelong_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("branch")),
            _clean(row.get("fee")),
        )
        for row in rows
    )


def _active_application_anchors(
    card: Any,
    base_url: str,
    selector: str = "a[href]",
) -> list[tuple[Any, str]]:
    found: list[tuple[Any, str]] = []
    seen_urls: set[str] = set()
    for anchor in card.select(selector):
        text = _clean(anchor.get_text(" ", strip=True))
        title = _clean(anchor.get("title", ""))
        href = urljoin(base_url, anchor.get("href", ""))
        query = parse_qs(urlparse(href).query)
        if (
            "신청" in text
            or "접수" in text
            or "신청" in title
            or query.get("act", [""])[0] in {"form", "mem_form", "write"}
        ) and href not in seen_urls:
            seen_urls.add(href)
            found.append((anchor, href))
    return found


def _parse_information_page(soup: BeautifulSoup, crawl_day: date) -> tuple[dict[str, Any], ...]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "교육 신청" not in title or GWANGJU_NAMGU_MUNICIPALITY_NAME not in title:
        raise GwangjuNamguContractError("information-education page title changed")
    root = soup.select_one("#content_detail .eduContainer")
    if not root:
        raise GwangjuNamguContractError("information-education container is missing")
    heading = root.select_one("h4.h4")
    if not heading or _clean(heading.get_text(" ", strip=True)) != "교육일정 및 신청":
        raise GwangjuNamguContractError("information-education heading changed")
    list_root = root.select_one("#eduprogram_responsive ul.eduList")
    if not list_root:
        raise GwangjuNamguContractError("information-education list is missing")
    cards = list_root.find_all("li", recursive=False)
    if not cards:
        text = _clean(list_root.get_text(" ", strip=True))
        if not any(marker in text for marker in ("교육일정이 없습니다", "등록된 정보가 없습니다")):
            raise GwangjuNamguContractError("information-education empty list has no explicit marker")
        return ()

    parsed_rows: list[dict[str, Any]] = []
    for card in cards:
        title_node = card.select_one(".textTitle")
        if not title_node:
            raise GwangjuNamguContractError("information course title is missing")
        course_title = _clean(title_node.get_text(" ", strip=True))
        fields: dict[str, str] = {}
        for item in card.select("ul.textEdu > li"):
            strong = item.select_one("strong")
            if not strong:
                raise GwangjuNamguContractError("information course field label is missing")
            label = _clean(strong.get_text(" ", strip=True)).rstrip(" :")
            clone = BeautifulSoup(str(item), "html.parser")
            for node in clone.select("strong"):
                node.decompose()
            value = _clean(clone.get_text(" ", strip=True)).lstrip(": ")
            if label in fields:
                raise GwangjuNamguContractError("information course field is duplicated")
            fields[label] = value
        if frozenset(fields) != _INFORMATION_FIELDS:
            raise GwangjuNamguContractError("information course fields changed")
        start, end = _date_range(fields["교육기간"], "education period")
        apply_start, apply_end = _date_range(fields["접수기간"], "application period")
        capacity_match = _CAPACITY_RE.fullmatch(fields["인원"])
        if not capacity_match:
            raise GwangjuNamguContractError("information capacity changed")
        capacity_current, capacity_total = map(int, capacity_match.groups())
        if capacity_current > capacity_total or capacity_total <= 0:
            raise GwangjuNamguContractError("information capacity is invalid")
        status_node = card.select_one("ul.btnArea > li")
        status_class = ""
        if status_node:
            status_class = next(
                (class_name for class_name in status_node.get("class", ()) if class_name in _INFORMATION_CLASS_STATUS),
                "",
            )
            status_clone = BeautifulSoup(str(status_node), "html.parser")
            for node in status_clone.select("a, button"):
                node.decompose()
            source_status = _clean(status_clone.get_text(" ", strip=True))
        else:
            source_status = ""
        status = _INFORMATION_STATUS_MAP.get(source_status, "") or _INFORMATION_CLASS_STATUS.get(status_class, "")
        if not status:
            raise GwangjuNamguContractError("information status changed")
        if status_class and _INFORMATION_CLASS_STATUS[status_class] != status:
            raise GwangjuNamguContractError("information status text/class disagree")
        if not source_status:
            source_status = {
                "SCHEDULED": "접수대기",
                "OPEN": "접수중",
                "CLOSED": "접수마감",
            }[status]
        apply_start_day = date.fromisoformat(apply_start)
        apply_end_day = date.fromisoformat(apply_end)
        if status == "SCHEDULED" and crawl_day >= apply_start_day:
            raise GwangjuNamguContractError("scheduled information course has started reception")
        if status == "OPEN" and not (apply_start_day <= crawl_day <= apply_end_day):
            raise GwangjuNamguContractError("open information course is outside reception period")

        controls = _active_application_anchors(
            card,
            GWANGJU_NAMGU_INFORMATION_URL,
            "ul.btnArea a[href]",
        )
        application_url = ""
        control_identity = ""
        if len(controls) > 1:
            raise GwangjuNamguContractError(
                "information course exposes multiple application routes"
            )
        if controls:
            control_identity = _query_identity(controls[0][1], "information")
            if not control_identity:
                raise GwangjuNamguContractError(
                    "information application control is not course-bound"
                )
        if status == "OPEN":
            if len(controls) != 1:
                raise GwangjuNamguContractError("open information course must expose exactly one application control")
            application_url = controls[0][1]
        identity = _information_identity(course_title, start, end, fields["시간"], fields["장소"])
        parsed_rows.append(
            {
                "source": "information",
                "identity": identity,
                "control_identity": control_identity,
                "title": course_title,
                "start_date": start,
                "end_date": end,
                "apply_start_date": apply_start,
                "apply_end_date": apply_end,
                "schedule": fields["시간"],
                "branch": fields["장소"],
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "source_status": source_status,
                "status": status,
                "application_control_present": bool(controls),
                "source_course_url": controls[0][1] if controls else "",
                "application_url": application_url,
            }
        )
    identities = [row["identity"] for row in parsed_rows]
    if len(identities) != len(set(identities)):
        raise GwangjuNamguContractError("duplicate information course identities")
    return tuple(parsed_rows)


def _information_signature(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("apply_start_date")),
            _clean(row.get("apply_end_date")),
            _clean(row.get("schedule")),
            _clean(row.get("branch")),
            _clean(row.get("source_status")),
            _clean(row.get("source_course_url")),
            _clean(row.get("application_url")),
        )
        for row in rows
    )


def _detail_pairs(soup: BeautifulSoup, identity: str) -> tuple[str, dict[str, str]]:
    table = soup.select_one("#content_detail table.tstyle_view")
    if not table:
        raise GwangjuNamguContractError(f"lifelong course {identity}: detail table missing")
    title_node = table.select_one("th.title")
    if not title_node:
        raise GwangjuNamguContractError(f"lifelong course {identity}: detail title missing")
    title = _clean(title_node.get_text(" ", strip=True))
    fields: dict[str, str] = {}
    for tr in table.select("tr"):
        direct = tr.find_all(["th", "td"], recursive=False)
        if not direct or tr.select_one("th.title"):
            continue
        index = 0
        while index < len(direct):
            node = direct[index]
            if node.name != "th":
                index += 1
                continue
            label = _clean(node.get_text(" ", strip=True))
            if label not in _LIFELONG_DETAIL_LABELS:
                raise GwangjuNamguContractError(f"lifelong course {identity}: unknown detail field {label}")
            if label in {"기타"}:
                index += 1
                continue
            if index + 1 >= len(direct) or direct[index + 1].name != "td":
                raise GwangjuNamguContractError(f"lifelong course {identity}: detail field has no value")
            if label in fields:
                raise GwangjuNamguContractError(f"lifelong course {identity}: duplicate detail field {label}")
            fields[label] = _clean(direct[index + 1].get_text(" ", strip=True))
            index += 2
    required = {"강좌기간", "신청기간", "교육기관", "대상", "접수방법", "수강료"}
    if not required.issubset(fields):
        missing = ", ".join(sorted(required - set(fields)))
        raise GwangjuNamguContractError(f"lifelong course {identity}: required detail fields missing ({missing})")
    return title, fields


def _application_type(method: str) -> str:
    normalized = _normalized(method)
    if any(token in normalized for token in ("인터넷", "온라인", "홈페이지")):
        return "ONLINE_RESERVATION"
    if any(token in normalized for token in ("앱", "방문", "전화", "이메일")):
        return "OFFLINE_APPLY"
    return "INFO_ONLY"


def _lifelong_application_control(
    soup: BeautifulSoup,
    identity: str,
    detail_url: str,
    expected_status: str,
    application_type: str,
) -> tuple[str, bool, str]:
    content = soup.select_one("#content_detail")
    if not content:
        raise GwangjuNamguContractError(f"lifelong course {identity}: content missing")
    controls: list[str] = []
    for anchor, href in _active_application_anchors(content, detail_url):
        del anchor
        parsed = urlparse(href)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == GWANGJU_NAMGU_LIFELONG_HOST
            and parsed.path == GWANGJU_NAMGU_LIFELONG_PATH
            and query.get("mid") == [GWANGJU_NAMGU_LIFELONG_MID]
            and query.get("act") == ["mem_form"]
            and query.get("seq") == [identity]
            and set(query) == {"mid", "act", "seq"}
        ):
            controls.append(href)
        elif query.get("act", [""])[0] in {"mem_form", "form", "write"}:
            raise GwangjuNamguContractError(f"lifelong course {identity}: application control is not course-bound")
    if expected_status == "OPEN" and application_type == "ONLINE_RESERVATION":
        if len(controls) != 1:
            raise GwangjuNamguContractError(f"lifelong course {identity}: exactly one application control required")
    elif controls:
        raise GwangjuNamguContractError(f"lifelong course {identity}: non-online/open course exposes active control")
    application_url = controls[0] if controls else ""
    contract = "same_host_lecture_mem_form_same_seq" if controls else "no_public_web_control_expected"
    return application_url, bool(controls), contract


def _lifelong_row(listed: Mapping[str, Any], soup: BeautifulSoup, crawl_day: date) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    title, fields = _detail_pairs(soup, identity)
    if _normalized(title) != _normalized(listed.get("title")):
        raise GwangjuNamguContractError(f"lifelong course {identity}: detail title differs from list")
    start, end = _date_range(fields["강좌기간"], "detail education period")
    if (start, end) != (
        _clean(listed.get("start_date")),
        _clean(listed.get("end_date")),
    ):
        raise GwangjuNamguContractError(f"lifelong course {identity}: detail period differs from list")
    branch = _clean(fields["교육기관"])
    if _normalized(branch) != _normalized(listed.get("branch")):
        raise GwangjuNamguContractError(f"lifelong course {identity}: detail institution differs from list")
    if _normalized(fields["수강료"]) != _normalized(listed.get("fee")):
        raise GwangjuNamguContractError(f"lifelong course {identity}: detail fee differs from list")
    apply_start, apply_end = _date_range(fields["신청기간"], "application period")
    application_kind = _application_type(fields["접수방법"])
    if application_kind == "INFO_ONLY":
        raise GwangjuNamguContractError(f"lifelong course {identity}: unknown application method")
    apply_start_day = date.fromisoformat(apply_start)
    apply_end_day = date.fromisoformat(apply_end)
    if crawl_day < apply_start_day:
        status = "SCHEDULED"
    elif crawl_day <= apply_end_day:
        status = "OPEN"
    else:
        status = "CLOSED"
    application_url, control_present, control_contract = _lifelong_application_control(
        soup,
        identity,
        _clean(listed.get("raw_url")),
        status,
        application_kind,
    )
    return {
        "provider": GWANGJU_NAMGU_PROVIDER,
        "provider_course_id": f"lifelong:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": "평생학습",
        "program_type": "교육",
        "raw_url": _clean(listed.get("raw_url")),
        "application_url": application_url,
        "application_type": application_kind,
        "application_method_raw": _clean(fields["접수방법"]),
        "reservation_available": bool(control_present),
        "status": status,
        "fee": _clean(fields["수강료"]),
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "schedule_raw": "",
        "target": _clean(fields["대상"]),
        "capacity_current": None,
        "capacity_total": None,
        "venue_name": branch,
        "collection_category": "공공교육",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_education",
        "collection_type": GWANGJU_NAMGU_PARSER,
        "municipality_code": GWANGJU_NAMGU_MUNICIPALITY_CODE,
        "municipality_name": GWANGJU_NAMGU_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": GWANGJU_NAMGU_PARSER,
            "source_code": "lifelong",
            "source_identity": identity,
            "source_page": int(listed.get("source_page") or 0),
            "source_status": status,
            "source_application_method": _clean(fields["접수방법"]),
            "detail_verified": True,
            "application_control_present": control_present,
            "application_control_contract": control_contract,
            "application_control_verified": True,
        },
    }


def _information_row(listed: Mapping[str, Any]) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    branch = _clean(listed.get("branch"))
    application_url = _clean(listed.get("application_url"))
    source_course_url = _clean(listed.get("source_course_url"))
    status = _clean(listed.get("status"))
    return {
        "provider": GWANGJU_NAMGU_PROVIDER,
        "provider_course_id": f"information:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(listed.get("title")),
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": "정보화교육",
        "program_type": "교육",
        "raw_url": source_course_url or (
            f"{GWANGJU_NAMGU_INFORMATION_URL}#course-{identity}"
        ),
        "application_url": application_url,
        "application_type": (
            "ONLINE_RESERVATION" if application_url else "INFO_ONLY"
        ),
        "application_method_raw": "인터넷 접수",
        "reservation_available": bool(application_url),
        "status": status,
        "fee": "무료",
        "period": (f"{_clean(listed.get('start_date'))} ~ {_clean(listed.get('end_date'))}"),
        "start_date": _clean(listed.get("start_date")),
        "end_date": _clean(listed.get("end_date")),
        "apply_period": (f"{_clean(listed.get('apply_start_date'))} ~ {_clean(listed.get('apply_end_date'))}"),
        "apply_start_date": _clean(listed.get("apply_start_date")),
        "apply_end_date": _clean(listed.get("apply_end_date")),
        "schedule_raw": _clean(listed.get("schedule")),
        "target": "남구 주민 누구나",
        "capacity_current": int(listed.get("capacity_current") or 0),
        "capacity_total": int(listed.get("capacity_total") or 0),
        "venue_name": branch,
        "collection_category": "공공교육",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_education",
        "collection_type": GWANGJU_NAMGU_PARSER,
        "municipality_code": GWANGJU_NAMGU_MUNICIPALITY_CODE,
        "municipality_name": GWANGJU_NAMGU_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": GWANGJU_NAMGU_PARSER,
            "source_code": "information",
            "source_identity": identity,
            "source_page": 1,
            "source_status": _clean(listed.get("source_status")),
            "source_application_method": "인터넷 접수",
            "detail_verified": True,
            "application_control_present": bool(
                listed.get("application_control_present")
            ),
            "application_control_contract": (
                "same_host_education_form_course_identity"
                if application_url
                else (
                    "status_gated_same_host_education_form_course_identity"
                    if listed.get("application_control_present")
                    else "no_public_control_while_not_open"
                )
            ),
            "application_control_verified": True,
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    unexpected = set(row) - _ALLOWED_ROW_KEYS
    if unexpected:
        errors.append("unexpected persisted keys: " + ", ".join(sorted(unexpected)))
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping):
        errors.append("raw_fields is not a mapping")
    else:
        raw_unexpected = set(raw) - _ALLOWED_RAW_KEYS
        if raw_unexpected:
            errors.append("unexpected raw_fields keys: " + ", ".join(sorted(raw_unexpected)))

    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                lowered = _clean(key).casefold()
                if lowered in _FORBIDDEN_KEYS or any(
                    token in lowered for token in ("instructor", "teacher", "manager", "contact", "phone", "email")
                ):
                    errors.append(f"forbidden persisted key: {path}{key}")
                walk(child, f"{path}{key}.")
        elif isinstance(value, (list, tuple, set)):
            for index, child in enumerate(value):
                walk(child, f"{path}{index}.")
        elif isinstance(value, str) and (_PHONE_RE.search(value) or _EMAIL_RE.search(value)):
            errors.append(f"PII-like value persisted at {path.rstrip('.')}")

    walk(row)
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            raise GwangjuNamguContractError("duplicate output provider_course_id")
        seen.add(identity)
        result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
        "list_requests": 0,
        "required_list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "returned_count": 0,
        "no_current_data": False,
        "candidate_audit": dict(GWANGJU_NAMGU_CANDIDATE_AUDIT),
        "discovery_audit": dict(GWANGJU_NAMGU_DISCOVERY_AUDIT),
        "ownership_scope": GWANGJU_NAMGU_OWNERSHIP_SCOPE,
        "separate_owner_urls": dict(GWANGJU_NAMGU_SEPARATE_OWNER_URLS),
        "excluded_urls": dict(GWANGJU_NAMGU_EXCLUDED_URLS),
        "pii_fields_discarded": GWANGJU_NAMGU_PII_FIELDS_DISCARDED,
    }


def collect_gwangju_namgu_education(
    target: Any,
    *,
    today: Optional[date | datetime | str] = None,
    timeout: int = 30,
    max_pages: Optional[int] = None,
    detail_limit: Optional[int] = None,
    max_workers: int = GWANGJU_NAMGU_MAX_WORKERS,
    session_factory: SessionFactory = _default_session_factory,
    fetcher: Fetcher = _default_fetcher,
    dedupe_rows: DedupeRows = _dedupe_default,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a validated current/future snapshot for the registered provider.

    Caps are hard safety boundaries.  If a cap cannot cover the complete
    source or every required current/future detail, no partial rows are
    returned.  The ``fetcher`` hook exists for isolated contract tests.
    """

    meta = _base_meta()
    request_count = 0
    request_lock = threading.Lock()

    def fail(message: str) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        meta["configured_collection_error"] = _clean(message)
        if not meta["list_requests"]:
            meta["list_requests"] = request_count
        return [], GWANGJU_NAMGU_PARSER, meta

    try:
        if not is_gwangju_namgu_education_target(target):
            raise GwangjuNamguContractError("target does not match registered Nam-gu owner")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValueError("timeout must be a positive integer")
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
            raise ValueError("max_workers must be a positive integer")
        if max_pages is not None and (isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 0):
            raise ValueError("max_pages must be a non-negative integer or None")
        if detail_limit is not None and (
            isinstance(detail_limit, bool) or not isinstance(detail_limit, int) or detail_limit < 0
        ):
            raise ValueError("detail_limit must be a non-negative integer or None")
        crawl_day = _today(today)
        if max_pages is not None and max_pages < 2:
            meta["source_cap_reached"] = True
            raise GwangjuNamguContractError("list request cap cannot cover the two source entry pages")

        def fetch_soup(url: str) -> BeautifulSoup:
            nonlocal request_count
            session = session_factory()
            try:
                response = fetcher(session, url, timeout)
                soup = _response_soup(response, url)
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            with request_lock:
                request_count += 1
            return soup

        lifelong_first = _parse_lifelong_page(fetch_soup(_lifelong_page_url(1)), 1)
        information_first = _parse_information_page(fetch_soup(GWANGJU_NAMGU_INFORMATION_URL), crawl_day)
        last_page = lifelong_first.last_page
        required_list_requests = last_page + 5
        # lifelong pages 1..last, empty sentinel, first and last rechecks;
        # information entry and recheck.
        meta["required_list_requests"] = required_list_requests
        if max_pages is not None and max_pages < required_list_requests:
            meta["source_cap_reached"] = True
            raise GwangjuNamguContractError(
                f"list request cap covers {max_pages} of {required_list_requests} required requests"
            )

        page_results: dict[int, _LifelongPage] = {1: lifelong_first}

        def fetch_lifelong_page(page: int) -> _LifelongPage:
            return _parse_lifelong_page(fetch_soup(_lifelong_page_url(page)), page)

        if last_page > 1:
            workers = min(max_workers, last_page - 1)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(fetch_lifelong_page, page): page for page in range(2, last_page + 1)}
                for future in as_completed(futures):
                    page = futures[future]
                    page_results[page] = future.result()

        sentinel = fetch_lifelong_page(last_page + 1)
        first_recheck = fetch_lifelong_page(1)
        last_recheck = fetch_lifelong_page(last_page)
        information_recheck = _parse_information_page(fetch_soup(GWANGJU_NAMGU_INFORMATION_URL), crawl_day)

        if request_count != required_list_requests:
            raise GwangjuNamguContractError("list request accounting changed")
        meta["list_requests"] = required_list_requests
        for page in page_results.values():
            if page.total != lifelong_first.total or page.last_page != last_page:
                raise GwangjuNamguContractError("lifelong pagination totals changed mid-snapshot")
        if sentinel.total != lifelong_first.total or sentinel.last_page != last_page:
            raise GwangjuNamguContractError("lifelong sentinel total changed")
        if sentinel.rows or not sentinel.empty_marker:
            raise GwangjuNamguContractError("lifelong post-last sentinel changed")
        if _lifelong_signature(first_recheck.rows) != _lifelong_signature(lifelong_first.rows):
            raise GwangjuNamguContractError("lifelong page-one stability recheck changed")
        if _lifelong_signature(last_recheck.rows) != _lifelong_signature(page_results[last_page].rows):
            raise GwangjuNamguContractError("lifelong last-page stability recheck changed")
        if _information_signature(information_recheck) != _information_signature(information_first):
            raise GwangjuNamguContractError("information-education stability recheck changed")

        lifelong_all = [row for page in range(1, last_page + 1) for row in page_results[page].rows]
        if len(lifelong_all) != lifelong_first.total:
            raise GwangjuNamguContractError("lifelong collected cardinality differs from declared total")
        source_identities = [_clean(row.get("identity")) for row in lifelong_all]
        if len(source_identities) != len(set(source_identities)):
            raise GwangjuNamguContractError("duplicate lifelong source identities")
        placeholders = [row for row in lifelong_all if row.get("placeholder")]

        lifelong_current = [
            row
            for row in lifelong_all
            if not row.get("placeholder") and date.fromisoformat(_clean(row.get("end_date"))) >= crawl_day
        ]
        information_current = [
            row for row in information_first if date.fromisoformat(_clean(row.get("end_date"))) >= crawl_day
        ]
        if detail_limit is not None and detail_limit < len(lifelong_current):
            meta["source_cap_reached"] = True
            raise GwangjuNamguContractError(
                f"detail cap covers {detail_limit} of {len(lifelong_current)} required details"
            )

        meta["detail_attempts"] = len(lifelong_current)
        lifelong_output: list[dict[str, Any]] = []
        if lifelong_current:
            workers = min(max_workers, len(lifelong_current))

            def fetch_detail(listed: Mapping[str, Any]) -> dict[str, Any]:
                soup = fetch_soup(_clean(listed.get("raw_url")))
                return _lifelong_row(listed, soup, crawl_day)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(fetch_detail, listed): _clean(listed.get("identity")) for listed in lifelong_current
                }
                for future in as_completed(futures):
                    lifelong_output.append(future.result())
        meta["detail_pages"] = len(lifelong_output)

        information_output = [_information_row(row) for row in information_current]
        all_output = lifelong_output + information_output
        expected_ids = {
            *(f"lifelong:{_clean(row.get('identity'))}" for row in lifelong_current),
            *(f"information:{_clean(row.get('identity'))}" for row in information_current),
        }
        if {row["provider_course_id"] for row in all_output} != expected_ids:
            raise GwangjuNamguContractError("output identities differ from current source rows")

        before_ids = [row["provider_course_id"] for row in all_output]
        deduped = list(dedupe_rows(all_output))
        after_ids = [str(row.get("provider_course_id") or "") for row in deduped]
        if Counter(before_ids) != Counter(after_ids):
            raise GwangjuNamguContractError("dedupe changed official identity cardinality")
        for row in deduped:
            errors = _privacy_errors(row)
            if errors:
                raise GwangjuNamguContractError("; ".join(errors))

        result = sorted(
            deduped,
            key=lambda row: (
                _clean(row.get("end_date")),
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            ),
        )
        semantic_counts = Counter(
            (
                _normalized(row.get("title")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
                _normalized(row.get("branch")),
            )
            for row in result
        )
        meta.update(
            {
                "list_requests": required_list_requests,
                "source_totals": {
                    "lifelong": len(lifelong_all),
                    "information": len(information_first),
                },
                "source_total": len(lifelong_all) + len(information_first),
                "source_pages": {"lifelong": last_page, "information": 1},
                "lifelong_placeholder_rows": len(placeholders),
                "lifelong_unique_identities": len(set(source_identities)),
                "sentinel_requests": 1,
                "stability_rechecks": 3,
                "current_future_counts": {
                    "lifelong": len(lifelong_current),
                    "information": len(information_current),
                },
                "current_future_total": len(lifelong_current) + len(information_current),
                "status_counts": dict(Counter(row["status"] for row in result)),
                "branch_counts": dict(Counter(row["branch"] for row in result)),
                "public_application_control_count": sum(bool(row.get("application_url")) for row in result),
                "offline_open_count": sum(
                    row.get("status") == "OPEN" and row.get("application_type") == "OFFLINE_APPLY" for row in result
                ),
                "semantic_duplicate_group_count": sum(count > 1 for count in semantic_counts.values()),
                "semantic_duplicate_excess_rows": sum(max(0, count - 1) for count in semantic_counts.values()),
                "semantic_duplicate_policy": "preserve_distinct_source_prefixed_identities",
                "pagination_complete": True,
                "details_complete": meta["detail_pages"] == meta["detail_attempts"],
                "application_controls_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "returned_count": len(result),
                "no_current_data": not result,
                "no_current_reason": (
                    "both complete official catalogues have no current/future courses" if not result else ""
                ),
            }
        )
        return result, GWANGJU_NAMGU_PARSER, meta
    except Exception as exc:  # fail closed; caller receives diagnostic metadata
        return fail(str(exc))


collect_courses = collect_gwangju_namgu_education


__all__ = [
    "GWANGJU_NAMGU_ALIAS_URLS",
    "GWANGJU_NAMGU_CANDIDATE_AUDIT",
    "GWANGJU_NAMGU_CANDIDATE_ID",
    "GWANGJU_NAMGU_CANDIDATE_URL",
    "GWANGJU_NAMGU_DISCOVERY_AUDIT",
    "GWANGJU_NAMGU_EXCLUDED_URLS",
    "GWANGJU_NAMGU_INFORMATION_URL",
    "GWANGJU_NAMGU_LIBRARY_BRANCHES",
    "GWANGJU_NAMGU_LIBRARY_CATALOGUE_TABS",
    "GWANGJU_NAMGU_LIFELONG_URL",
    "GWANGJU_NAMGU_MUNICIPALITY_CODE",
    "GWANGJU_NAMGU_MUNICIPALITY_NAME",
    "GWANGJU_NAMGU_OWNERSHIP_SCOPE",
    "GWANGJU_NAMGU_PARSER",
    "GWANGJU_NAMGU_PII_FIELDS_DISCARDED",
    "GWANGJU_NAMGU_PROVIDER",
    "GWANGJU_NAMGU_RESIDENT_CENTRE_BRANCHES",
    "GWANGJU_NAMGU_SEPARATE_OWNER_URLS",
    "GwangjuNamguContractError",
    "collect_courses",
    "collect_gwangju_namgu_education",
    "is_gwangju_namgu_alias_target",
    "is_gwangju_namgu_education_target",
    "is_gwangju_namgu_excluded_target",
    "is_target",
]
