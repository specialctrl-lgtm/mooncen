"""Fail-closed collector for Geochang County's lifelong-learning ledger.

The retained provider owns the official 30020201 all-course ledger.  Search
results which point at 30010101 are an application guide, while 30020203
results are individual detail aliases of rows in that same ledger.  They must
not create additional providers.

One snapshot reads every advertised list page, the exact post-last empty
sentinel, every current/future public detail, and then rechecks the first,
final, and sentinel pages.  Application, login, roster-fragment, external
operator, attachment, image-document, download, and other PII-bearing links
are never requested or persisted.  The detail page itself contains a roster
section for some courses; parsing stops at the two public metadata tables and
never reads that section's cells.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GEOCHANG_PROVIDER = "MUNI_EDUCITY_GEOCHANG_GO_KR_3187BF2A"
GEOCHANG_MUNICIPALITY_CODE = "4888000000"
GEOCHANG_MUNICIPALITY_NAME = "경상남도 거창군"

GEOCHANG_HOST = "educity.geochang.go.kr"
GEOCHANG_LIST_PATH = "/E0003/30020201.asp"
GEOCHANG_DETAIL_PATH = "/E0003/30020203.asp"
GEOCHANG_APPLICATION_PATH = "/E0003/30020501.asp"
GEOCHANG_GUIDE_PATH = "/E0003/30010101.asp"
GEOCHANG_CANONICAL_URL = f"https://{GEOCHANG_HOST}{GEOCHANG_LIST_PATH}"
GEOCHANG_GUIDE_URL = f"https://{GEOCHANG_HOST}{GEOCHANG_GUIDE_PATH}"
GEOCHANG_DETAIL_CANDIDATE_URLS: tuple[str, ...] = tuple(
    f"https://{GEOCHANG_HOST}{GEOCHANG_DETAIL_PATH}?lc={identity}"
    for identity in ("1992", "1991", "2006", "1985")
)

GEOCHANG_CANONICAL_URL_SHA256 = (
    "05aff5a1866cea99da6ca2d3651bf925337e7c5903309acd3ed28f40b5d2ed14"
)
GEOCHANG_CANONICAL_CANDIDATE_ID = "MUNI_IR_05AFF5A1866C"
GEOCHANG_GUIDE_CANDIDATE_ID = "MUNI_IR_C88685AE6190"
GEOCHANG_DETAIL_CANDIDATE_IDS: tuple[str, ...] = (
    "MUNI_IR_DF77E8197542",
    "MUNI_IR_9A22B92DB477",
    "MUNI_IR_A25BA65B6056",
    "MUNI_IR_564D1101CB6F",
)
GEOCHANG_COUNTY_HOME_CANDIDATE_ID = "MUNI_IR_C6819EE06F14"
GEOCHANG_COUNTY_MAIN_CANDIDATE_ID = "MUNI_IR_BC30D1AE95E8"

GEOCHANG_PAGE_SIZE = 5
GEOCHANG_RECOMMENDED_MAX_PAGES = 200
GEOCHANG_RECOMMENDED_DETAIL_LIMIT = 100
GEOCHANG_FETCH_ATTEMPTS = 2
GEOCHANG_MAX_HTML_BYTES = 1_000_000
GEOCHANG_HARD_MAX_PAGES = 300
GEOCHANG_HARD_MAX_DETAILS = 500
GEOCHANG_PARSER = (
    "geochang_complete_all_course_ledger+advertised_152_pages+"
    "exact_post_last_empty_sentinel+current_future_all_safe_details+"
    "first_final_sentinel_stability_recheck+source_status_controls+"
    "detail_branch_binding+guide_and_detail_candidate_reconciliation+"
    "application_login_roster_attachment_download_and_pii_no_fetch"
)
GEOCHANG_OWNERSHIP_SCOPE = (
    "official_geochang_lifelong_center_all_course_ledger"
)


@dataclass(frozen=True)
class GeochangBranch:
    source_name: str
    code: str
    address: str = ""


GEOCHANG_BRANCHES: tuple[GeochangBranch, ...] = (
    GeochangBranch(
        "거창군평생교육센터",
        "GEOCHANG_LIFELONG_EDUCATION_CENTER",
        "경상남도 거창군 거창읍 중앙로 103 신청사 5층",
    ),
    GeochangBranch("거창흥사단", "GEOCHANG_HEUNGSADAN"),
    GeochangBranch("한국컴퓨터학원", "GEOCHANG_KOREA_COMPUTER_ACADEMY"),
    GeochangBranch(
        "국립창원대학교(거창캠퍼스) 평생교육원",
        "CWNU_GEOCHANG_LIFELONG_EDUCATION_CENTER",
    ),
    GeochangBranch("거창군청소년문화의집", "GEOCHANG_YOUTH_CULTURE_HOUSE"),
    GeochangBranch("인구교육과", "GEOCHANG_POPULATION_EDUCATION_DIVISION"),
)
_BRANCH_BY_NAME = {item.source_name: item for item in GEOCHANG_BRANCHES}

GEOCHANG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    GEOCHANG_CANONICAL_CANDIDATE_ID: {
        "provider": GEOCHANG_PROVIDER,
        "url": GEOCHANG_CANONICAL_URL,
        "decision": "retain_incumbent_provider_as_complete_official_ledger",
    },
    GEOCHANG_GUIDE_CANDIDATE_ID: {
        "provider": GEOCHANG_PROVIDER,
        "url": GEOCHANG_GUIDE_URL,
        "decision": "excluded_application_guide_without_course_identity_ledger",
    },
    **{
        candidate_id: {
            "provider": GEOCHANG_PROVIDER,
            "url": url,
            "decision": "single_detail_alias_included_by_canonical_lc_identity",
        }
        for candidate_id, url in zip(
            GEOCHANG_DETAIL_CANDIDATE_IDS, GEOCHANG_DETAIL_CANDIDATE_URLS
        )
    },
    GEOCHANG_COUNTY_HOME_CANDIDATE_ID: {
        "provider": "MUNI_WWW_GEOCHANG_GO_KR_B939E464",
        "url": "https://www.geochang.go.kr/",
        "decision": "excluded_county_navigation_without_course_ledger",
    },
    GEOCHANG_COUNTY_MAIN_CANDIDATE_ID: {
        "provider": "MUNI_WWW_GEOCHANG_GO_KR_B939E464",
        "url": "https://www.geochang.go.kr/main.web",
        "decision": "excluded_county_navigation_alias_without_course_ledger",
    },
}

GEOCHANG_EXCLUDED_EVIDENCE: tuple[Mapping[str, str], ...] = (
    {
        "url": GEOCHANG_GUIDE_URL,
        "selector": "content heading 신청방법; no .listover ledger",
        "reason": "application instructions and identity-verification guidance only",
    },
    {
        "url": "https://www.geochang.go.kr/",
        "selector": "county portal navigation",
        "reason": "navigation entry point, not a course identity ledger",
    },
    {
        "url": "https://www.geochang.go.kr/main.web",
        "selector": "county representative portal navigation",
        "reason": "navigation alias, not a course identity ledger",
    },
)

GEOCHANG_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "url": "https://gcyka.or.kr/theme/coreweb/html/program/02.php",
        "owner": "거창흥사단",
        "decision": "portal mirror row retained; external application owner not crawled",
    },
    {
        "url": "https://psl.gc.ac.kr/P0301/F0002.asp",
        "owner": "국립창원대학교(거창캠퍼스) 평생교육원",
        "decision": "portal mirror row retained; separate operator ledger not crawled",
    },
    {
        "url": "https://www.geochang.go.kr/01278/01316/01317.web",
        "owner": "거창군청소년문화의집",
        "decision": "portal mirror row retained; county notice/detail not crawled",
    },
    {
        "url": f"https://{GEOCHANG_HOST}{GEOCHANG_APPLICATION_PATH}",
        "owner": "거창군평생학습센터 identity/application flow",
        "decision": "application and real-name verification endpoint never requested or persisted",
    },
    {
        "url": f"https://{GEOCHANG_HOST}{GEOCHANG_DETAIL_PATH}#lec_list",
        "owner": "embedded applicant roster section",
        "decision": "fragment control never followed; roster cells never parsed or persisted",
    },
)

GEOCHANG_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "audited_at": "2026-07-23",
    "cutoff": "2026-07-23",
    "source_rows": 758,
    "source_identity_count": 752,
    "duplicate_source_rows": 6,
    "duplicate_lc": ["1927", "1928", "1929", "1930", "1931", "1932"],
    "pages": 152,
    "page_size": 5,
    "final_page_rows": 3,
    "post_last_page": 153,
    "current_rows": 42,
    "expired_source_rows": 716,
    "ordered_source_lc_sha256": (
        "cc7a576d6d842ee1ef748437a40446115fc2048df6adfe8367969dcc9fe43ca1"
    ),
    "ordered_current_lc_sha256": (
        "4e01ae3efecbc8749c6b634c1985b1086e515d9191f116d4963acbcf141b4980"
    ),
    "first_lc": ["2031", "2030", "2029", "2028", "1926"],
    "final_lc": ["1279", "1258", "1247"],
    "source_status_counts": {"예정": 15, "접수중": 10, "마감": 2, "종료": 731},
    "current_source_status_counts": {"예정": 15, "접수중": 10, "마감": 2, "종료": 15},
    "branch_counts": {
        "거창군평생교육센터": 15,
        "거창흥사단": 11,
        "한국컴퓨터학원": 10,
        "국립창원대학교(거창캠퍼스) 평생교육원": 3,
        "거창군청소년문화의집": 2,
        "인구교육과": 1,
    },
    "application_method_counts": {
        "바로접수 : 평생학습센터": 12,
        "홈페이지 : 기관 별도": 16,
        "전화접수": 14,
    },
    "internal_application_controls": 4,
    "actionable_internal_application_controls": 2,
    "roster_fragment_controls": 12,
    "external_operator_controls": 16,
    "candidate_lc_pages": {"1992": 12, "1991": 11, "2006": 10, "1985": 10},
    "search_conditions": {
        "header": "GET st",
        "body": "POST st",
        "pagination": "GET page with st when searched",
        "audited_keyword": "AI",
        "audited_rows": 11,
        "audited_pages": 3,
        "get_post_first_page_identical": True,
        "post_page_parameter_ignored": True,
    },
    "list_requests_per_snapshot": 156,
    "detail_requests_per_snapshot": 42,
    "requests_per_snapshot": 198,
    "two_snapshot_requests": 396,
}


class GeochangContractError(ValueError):
    """Raised when the audited public contract no longer holds."""


@dataclass(frozen=True)
class _ListedCourse:
    lc: str
    title: str
    source_status: str
    status: str
    apply_period: str
    event_period: str
    event_start: date
    event_end: date
    target: str
    capacity: int
    venue: str
    image_url: str
    application_control: bool
    roster_control: bool
    external_control: bool
    external_control_has_url: bool
    page: int
    sequence: int

    @property
    def detail_url(self) -> str:
        return _detail_url(self.lc)

    @property
    def public_signature(self) -> tuple[Any, ...]:
        return (
            self.lc,
            self.title,
            self.source_status,
            self.apply_period,
            self.event_period,
            self.target,
            self.capacity,
            self.venue,
            self.image_url,
            self.application_control,
            self.roster_control,
            self.external_control,
            self.external_control_has_url,
        )


@dataclass(frozen=True)
class _Page:
    number: int
    advertised_total: int
    advertised_pages: int
    rows: tuple[_ListedCourse, ...]
    exact_empty_sentinel: bool

    @property
    def signature(self) -> tuple[Any, ...]:
        return (
            self.number,
            self.advertised_total,
            self.advertised_pages,
            tuple(row.public_signature for row in self.rows),
            self.exact_empty_sentinel,
        )


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})\.(\d{2})\.(\d{2})(?!\d)")
_SUMMARY_RE = re.compile(
    r"^총\s+(\d+)\s+건의\s+강좌가\s+있습니다\.\(\s*(\d+)\s*/\s*(\d+)페이지\)$"
)
_TARGET_RE = re.compile(r"^(.+?)\s*/\s*([\d,]+)명$")
_CAPACITY_RE = re.compile(r"^온라인\s+([\d,]+)\s*명$")
_BRANCH_CONTACT_RE = re.compile(r"^(.+?)\s*\(문의\s+.+\)$")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_STATUS_MAP: Mapping[str, str] = {
    "예정": "SCHEDULED",
    "접수중": "OPEN",
    "마감": "CLOSED",
    "종료": "ENDED",
}
_DETAIL_METHODS = frozenset(
    {"바로접수 : 평생학습센터", "홈페이지 : 기관 별도", "전화접수"}
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "contact",
        "contacts",
        "email",
        "instructor",
        "instructor_name",
        "applicant_name",
        "applicant_phone",
        "applicants",
        "attachments",
        "attachment_urls",
        "download_url",
        "application_endpoint",
        "external_application_url",
        "password",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def is_geochang_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == GEOCHANG_PROVIDER
        and _clean(_target_value(target, "url"))
        in {GEOCHANG_CANONICAL_URL, *GEOCHANG_DETAIL_CANDIDATE_URLS}
    )


is_target = is_geochang_education_target


def _raw_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _list_url(page: int) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("invalid Geochang page")
    if page == 1:
        return GEOCHANG_CANONICAL_URL
    return GEOCHANG_CANONICAL_URL + "?" + urlencode(
        (("page", str(page)), ("lc", ""), ("search_date", ""))
    )


def _detail_url(identity: str) -> str:
    value = _clean(identity)
    if _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError("invalid Geochang course identity")
    return (
        f"https://{GEOCHANG_HOST}{GEOCHANG_DETAIL_PATH}?"
        + urlencode((("lc", value),))
    )


def _allowed_request_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError:
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GEOCHANG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.fragment
    ):
        return False
    if parsed.path == GEOCHANG_DETAIL_PATH:
        return bool(
            len(pairs) == 1
            and pairs[0][0] == "lc"
            and _IDENTITY_RE.fullmatch(pairs[0][1])
        )
    if parsed.path != GEOCHANG_LIST_PATH:
        return False
    if not pairs:
        return True
    return bool(
        len(pairs) == 3
        and pairs[0][0] == "page"
        and _IDENTITY_RE.fullmatch(pairs[0][1])
        and pairs[1] == ("lc", "")
        and pairs[2] == ("search_date", "")
    )


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
) -> BeautifulSoup:
    if not _allowed_request_url(url):
        raise GeochangContractError("request left the audited list/detail allowlist")
    meta["source_requests"] += 1
    last_error: Optional[Exception] = None
    for _ in range(GEOCHANG_FETCH_ATTEMPTS):
        meta["request_attempts"] += 1
        try:
            response = fetcher(session, url, timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            if int(getattr(response, "status_code", 200)) != 200:
                raise GeochangContractError("unexpected HTTP status")
            if getattr(response, "history", None):
                raise GeochangContractError("redirect is not allowed")
            final_url = _clean(getattr(response, "url", url)) or url
            if final_url != url or not _allowed_request_url(final_url):
                raise GeochangContractError("response URL changed")
            headers = getattr(response, "headers", {}) or {}
            content_type = _clean(
                headers.get("content-type") or headers.get("Content-Type")
            )
            if content_type and "text/html" not in content_type.lower():
                raise GeochangContractError("official page is no longer HTML")
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", response)).encode("utf-8")
            if not isinstance(content, (bytes, bytearray)):
                content = bytes(content)
            if not content or len(content) > GEOCHANG_MAX_HTML_BYTES:
                raise GeochangContractError("empty or oversized official HTML")
            try:
                html = bytes(content).decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise GeochangContractError(
                    "official page is no longer strict UTF-8"
                ) from exc
            return BeautifulSoup(html, "html.parser")
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise GeochangContractError("official page fetch failed")


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return (
            value.astimezone(ZoneInfo("Asia/Seoul")).date()
            if value.tzinfo
            else value.date()
        )
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("today must be an ISO date")


def _date_range(value: str, label: str, *, allow_reversed: bool = False) -> tuple[date, date]:
    matches = list(_DATE_RE.finditer(_clean(value)))
    if len(matches) != 2:
        raise GeochangContractError(f"{label}: exact two-date range missing")
    try:
        values = tuple(
            date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            for match in matches
        )
    except ValueError as exc:
        raise GeochangContractError(f"{label}: invalid date") from exc
    if values[1] < values[0] and not allow_reversed:
        raise GeochangContractError(f"{label}: reversed date range")
    return values[0], values[1]


def _owner_contract(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "평생학습강좌 | 거창군평생학습센터":
        raise GeochangContractError(f"official owner/title changed: {title}")
    logos = soup.select("#header h1 a[href='/'] img[alt='로고']")
    if len(logos) != 1 or _clean(logos[0].get("src")) != "/images/common/logo_geochangeducity.png":
        raise GeochangContractError("official portal logo changed")
    footer_nodes = soup.select("#footer")
    footer = _clean(footer_nodes[0].get_text(" ", strip=True)) if len(footer_nodes) == 1 else ""
    if (
        "경상남도 거창군 거창읍 중앙로 103 신청사 5층" not in footer
        or "거창군평생교육센터" not in footer
    ):
        raise GeochangContractError("official owner/footer evidence missing")


def _search_form_contract(soup: BeautifulSoup) -> None:
    header_forms = soup.select(
        f"form[name='searchForm_tab'][method='GET'][action='{GEOCHANG_LIST_PATH}']"
    )
    if len(header_forms) != 1:
        raise GeochangContractError("header GET search form changed")
    header_inputs = header_forms[0].select("input[type='text'][name='st']")
    if (
        len(header_inputs) != 1
        or _clean(header_inputs[0].get("id")) != "search_name_tab"
        or _clean(header_inputs[0].get("value"))
    ):
        raise GeochangContractError("header GET search condition changed")
    body_forms = soup.select("form[name='search_form']")
    if (
        len(body_forms) != 1
        or _clean(body_forms[0].get("method")).lower() != "post"
        or _clean(body_forms[0].get("action")) != "30020201.asp"
    ):
        raise GeochangContractError("body POST search form changed")
    body_inputs = body_forms[0].select("input[type='text'][name='st']")
    if len(body_inputs) != 1 or _clean(body_inputs[0].get("value")):
        raise GeochangContractError("body POST keyword condition changed")
    buttons = body_forms[0].select("a[onclick]")
    if (
        len(buttons) != 1
        or _clean(buttons[0].get("onclick")) != "javascript:Search();"
        or _clean(buttons[0].get_text(" ", strip=True)) != "검색"
    ):
        raise GeochangContractError("body POST search submission control changed")


def _summary_contract(soup: BeautifulSoup, page: int) -> tuple[int, int]:
    nodes = soup.select("div[style*='border-bottom']")
    matches: list[re.Match[str]] = []
    for node in nodes:
        match = _SUMMARY_RE.fullmatch(_clean(node.get_text(" ", strip=True)))
        if match:
            matches.append(match)
    if len(matches) != 1:
        raise GeochangContractError(f"page {page}: advertised total/pages summary changed")
    total, current, pages = (int(value) for value in matches[0].groups())
    if current != page or pages < 1 or total < 1:
        raise GeochangContractError(f"page {page}: advertised page binding changed")
    if pages != (total + GEOCHANG_PAGE_SIZE - 1) // GEOCHANG_PAGE_SIZE:
        raise GeochangContractError("advertised total/page-size arithmetic changed")
    return total, pages


def _pager_contract(soup: BeautifulSoup, page: int, pages: int, sentinel: bool) -> None:
    pagers = soup.select(".paging_wrap > ul.paging")
    if len(pagers) != 1:
        raise GeochangContractError(f"page {page}: pagination wrapper changed")
    active = pagers[0].select(":scope > li.on")
    if sentinel:
        if active:
            raise GeochangContractError("post-last sentinel unexpectedly has an active page")
    elif (
        len(active) != 1
        or _clean(active[0].get_text(" ", strip=True)) != str(page)
        or len(active[0].select(":scope > a[href='#']")) != 1
    ):
        raise GeochangContractError(f"page {page}: active pager binding changed")
    for anchor in pagers[0].find_all("a", href=True):
        href = _clean(anchor.get("href"))
        if href == "#":
            continue
        parsed = urlparse(urljoin(GEOCHANG_CANONICAL_URL, href))
        try:
            query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise GeochangContractError("malformed pagination URL") from exc
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != GEOCHANG_HOST
            or parsed.path != GEOCHANG_LIST_PATH
            or not query
            or query[0][0] != "page"
            or _IDENTITY_RE.fullmatch(query[0][1]) is None
            or int(query[0][1]) > pages
            or any(name not in {"page", "lc", "search_date", "st"} for name, _ in query)
        ):
            raise GeochangContractError("pagination left the audited GET page/search boundary")


def _internal_image_url(current_url: str, node: Any) -> str:
    source = _clean(node.get("src") if node else "")
    if not source:
        return ""
    url = urljoin(current_url, source)
    parsed = urlparse(url)
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GEOCHANG_HOST
        and parsed.path.startswith("/images/lec_img/")
        and not parsed.query
        and not parsed.fragment
    ):
        raise GeochangContractError("course image left the public image boundary")
    return url


def _course_detail_identity(href: str, *, allow_fragment: bool) -> Optional[str]:
    parsed = urlparse(urljoin(GEOCHANG_CANONICAL_URL, href))
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GEOCHANG_HOST
        and parsed.path == GEOCHANG_DETAIL_PATH
        and len(query) == 1
        and query[0][0] == "lc"
        and _IDENTITY_RE.fullmatch(query[0][1])
    ):
        return None
    if allow_fragment:
        if parsed.fragment not in {"", "lec_list"}:
            return None
    elif parsed.fragment:
        return None
    return query[0][1]


def _application_control_identity(href: str) -> Optional[str]:
    parsed = urlparse(urljoin(GEOCHANG_CANONICAL_URL, href))
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GEOCHANG_HOST
        and parsed.path == GEOCHANG_APPLICATION_PATH
        and len(query) == 4
        and tuple(name for name, _ in query) == ("lc", "path", "query", "lcat")
        and _IDENTITY_RE.fullmatch(query[0][1])
        and query[1][1] == "/03Sub/03_Apply.asp"
        and query[2][1] == f"lc={query[0][1]}"
        and query[3][1].isdigit()
        and not parsed.fragment
    ):
        return None
    return query[0][1]


def _parse_card(node: Any, current_url: str, page: int, sequence: int) -> _ListedCourse:
    title_nodes = node.select(":scope > a > .lec_title")
    if len(title_nodes) != 1:
        raise GeochangContractError(f"page {page} row {sequence}: title changed")
    title_node = title_nodes[0]
    status_nodes = title_node.select(":scope > span")
    if len(status_nodes) != 1:
        raise GeochangContractError(f"page {page} row {sequence}: status badge changed")
    source_status = _clean(status_nodes[0].get_text(" ", strip=True))
    if source_status not in _STATUS_MAP:
        raise GeochangContractError(
            f"page {page} row {sequence}: unknown status {source_status}"
        )
    status_nodes[0].extract()
    title = _clean(title_node.get_text(" ", strip=True))
    if not title:
        raise GeochangContractError(f"page {page} row {sequence}: empty title")

    detail_links: list[str] = []
    application_control = False
    roster_control = False
    external_control = False
    external_control_has_url = False
    for anchor in node.find_all("a"):
        label = _clean(anchor.get_text(" ", strip=True))
        href = _clean(anchor.get("href"))
        detail_identity = _course_detail_identity(href, allow_fragment=True) if href else None
        parsed = urlparse(urljoin(current_url, href)) if href else None
        if detail_identity is not None and parsed is not None and not parsed.fragment:
            detail_links.append(detail_identity)
            continue
        if label == "명단":
            if (
                detail_identity is None
                or parsed is None
                or parsed.fragment != "lec_list"
            ):
                raise GeochangContractError("roster fragment identity changed")
            roster_control = True
            continue
        if label == "수강신청":
            identity = _application_control_identity(href)
            if identity is None:
                raise GeochangContractError("application control boundary changed")
            application_control = True
            detail_links.append(f"application:{identity}")
            continue
        if label == "별도링크":
            external_control = True
            if href:
                if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise GeochangContractError("external operator control changed")
                external_control_has_url = True
            continue
        if label:
            raise GeochangContractError(f"unexpected course control: {label}")

    normal_detail_ids = [value for value in detail_links if not value.startswith("application:")]
    if len(normal_detail_ids) != 2 or len(set(normal_detail_ids)) != 1:
        raise GeochangContractError(f"page {page} row {sequence}: detail links changed")
    identity = normal_detail_ids[0]
    if application_control and f"application:{identity}" not in detail_links:
        raise GeochangContractError("application/detail identity mismatch")

    list_items = node.select(":scope > .lec_left_wrap li")
    pairs: dict[str, str] = {}
    for item in list_items:
        labels = item.find_all("b", recursive=False)
        values = item.find_all("span", recursive=False)
        if len(labels) != 1 or len(values) != 1:
            raise GeochangContractError(f"course {identity}: list field shape changed")
        key = _clean(labels[0].get_text(" ", strip=True))
        if key in pairs:
            raise GeochangContractError(f"course {identity}: duplicate list field")
        pairs[key] = _clean(values[0].get_text(" ", strip=True))
    if tuple(pairs) != ("접수", "교육", "대상", "장소", "문의"):
        raise GeochangContractError(f"course {identity}: list fields changed")
    event_start, event_end = _date_range(pairs["교육"], f"course {identity} event")
    target_match = _TARGET_RE.fullmatch(pairs["대상"])
    if target_match is None or not _clean(target_match.group(1)):
        raise GeochangContractError(f"course {identity}: target/capacity changed")
    capacity = int(target_match.group(2).replace(",", ""))
    if capacity < 0:
        raise GeochangContractError(f"course {identity}: invalid capacity")
    if not pairs["문의"]:
        raise GeochangContractError(f"course {identity}: contact evidence missing")
    image_nodes = node.select(":scope > .lec_left_img > img")
    if len(image_nodes) > 1:
        raise GeochangContractError(f"course {identity}: image count changed")
    image_url = _internal_image_url(current_url, image_nodes[0] if image_nodes else None)
    public = " ".join((title, pairs["대상"], pairs["장소"]))
    if _PHONE_RE.search(public) or _EMAIL_RE.search(public) or _RESIDENT_RE.search(public):
        raise GeochangContractError(f"course {identity}: public list fields contain PII")
    return _ListedCourse(
        lc=identity,
        title=title,
        source_status=source_status,
        status=_STATUS_MAP[source_status],
        apply_period=pairs["접수"],
        event_period=pairs["교육"],
        event_start=event_start,
        event_end=event_end,
        target=pairs["대상"],
        capacity=capacity,
        venue=pairs["장소"],
        image_url=image_url,
        application_control=application_control,
        roster_control=roster_control,
        external_control=external_control,
        external_control_has_url=external_control_has_url,
        page=page,
        sequence=sequence,
    )


def _parse_page(soup: BeautifulSoup, page: int, *, sentinel: bool = False) -> _Page:
    _owner_contract(soup)
    _search_form_contract(soup)
    total, pages = _summary_contract(soup, page)
    if sentinel != (page == pages + 1):
        raise GeochangContractError(f"page {page}: sentinel/page-count binding changed")
    _pager_contract(soup, page, pages, sentinel)
    cards = soup.select("#sub_contents .listover")
    if not cards:
        cards = soup.select(".listover")
    if len(cards) != len({id(node) for node in cards}):
        raise GeochangContractError("duplicate DOM card selection")
    expected = 0 if sentinel else min(GEOCHANG_PAGE_SIZE, total - (page - 1) * GEOCHANG_PAGE_SIZE)
    if expected < 1 and not sentinel:
        raise GeochangContractError(f"page {page}: advertised non-sentinel page is empty")
    if len(cards) != expected:
        raise GeochangContractError(
            f"page {page}: expected {expected} source rows, found {len(cards)}"
        )
    current_url = _list_url(page)
    rows = tuple(
        _parse_card(card, current_url, page, sequence)
        for sequence, card in enumerate(cards, 1)
    )
    return _Page(page, total, pages, rows, sentinel and not rows)


def _fetch_page(
    session: Any,
    page: int,
    timeout: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
    *,
    sentinel: bool = False,
) -> _Page:
    meta["list_requests"] += 1
    if sentinel:
        meta["post_last_requests"] += 1
    soup = _fetch_soup(session, _list_url(page), timeout, fetcher, meta)
    return _parse_page(soup, page, sentinel=sentinel)


def _detail_fields(
    wrapper: Any, identity: str
) -> tuple[Mapping[str, str], str, int, str]:
    con_nodes = wrapper.select(":scope > .con01")
    if len(con_nodes) != 1:
        raise GeochangContractError(f"course {identity}: public detail header changed")
    con = con_nodes[0]
    fields: dict[str, str] = {}
    for item in con.select(".txt li"):
        labels = item.find_all("span", recursive=False)
        if len(labels) != 1:
            raise GeochangContractError(f"course {identity}: detail header field changed")
        key = _clean(labels[0].get_text(" ", strip=True))
        labels[0].extract()
        if key in fields:
            raise GeochangContractError(f"course {identity}: duplicate detail field")
        fields[key] = _clean(item.get_text(" ", strip=True))
    if tuple(fields) != ("기관", "접수", "일정", "대상", "장소"):
        raise GeochangContractError(f"course {identity}: detail fields changed")

    tables = wrapper.find_all("table", class_="basic_tbl01", recursive=False)
    if len(tables) != 3:
        raise GeochangContractError(f"course {identity}: detail table boundary changed")
    first_headers = tuple(
        _clean(row.find("th", recursive=False).get_text(" ", strip=True))
        for row in tables[0].find_all("tr")
        if row.find("th", recursive=False)
    )
    if first_headers != ("학습 목표", "학습 계획", "개인준비물"):
        raise GeochangContractError(f"course {identity}: curriculum boundary changed")
    second_headers: list[str] = []
    safe: dict[str, str] = {}
    for row in tables[1].find_all("tr"):
        header = row.find("th", recursive=False)
        cell = row.find("td", recursive=False)
        if header is None or cell is None:
            raise GeochangContractError(f"course {identity}: safe detail row changed")
        key = _clean(header.get_text(" ", strip=True))
        second_headers.append(key)
        if key in {"수 강 료", "교육정원", "접수방법"}:
            safe[key] = _clean(cell.get_text(" ", strip=True))
    if tuple(second_headers) != (
        "강 사 명",
        "수 강 료",
        "교육정원",
        "접수방법",
        "별로링크",
    ):
        raise GeochangContractError(f"course {identity}: safe detail headers changed")
    roster_heading = tables[2].find_previous_sibling("h4")
    if roster_heading is None or not _clean(roster_heading.get_text(" ", strip=True)).startswith("접수자 정보"):
        raise GeochangContractError(f"course {identity}: applicant-section boundary changed")
    # Deliberately do not access tables[2] descendants: they can contain masked PII.
    capacity_match = _CAPACITY_RE.fullmatch(safe.get("교육정원", ""))
    if capacity_match is None:
        raise GeochangContractError(f"course {identity}: detail capacity changed")
    capacity = int(capacity_match.group(1).replace(",", ""))
    method = safe.get("접수방법", "")
    if method not in _DETAIL_METHODS:
        raise GeochangContractError(f"course {identity}: application method changed")
    return fields, safe.get("수 강 료", ""), capacity, method


def _parse_detail(target: Any, listed: _ListedCourse, soup: BeautifulSoup) -> dict[str, Any]:
    _owner_contract(soup)
    wrappers = soup.select(".sub0202_view_wrap")
    if len(wrappers) != 1:
        raise GeochangContractError(f"course {listed.lc}: detail wrapper changed")
    wrapper = wrappers[0]
    titles = wrapper.select(":scope > .con01 .tit > em")
    title = _clean(titles[0].get_text(" ", strip=True)) if len(titles) == 1 else ""
    if title != listed.title:
        raise GeochangContractError(f"course {listed.lc}: list/detail title mismatch")
    fields, fee, detail_capacity, method = _detail_fields(wrapper, listed.lc)
    branch_match = _BRANCH_CONTACT_RE.fullmatch(fields["기관"])
    if branch_match is None:
        raise GeochangContractError(f"course {listed.lc}: branch/contact boundary changed")
    branch_name = _clean(branch_match.group(1))
    branch = _BRANCH_BY_NAME.get(branch_name)
    if branch is None:
        raise GeochangContractError(f"course {listed.lc}: unknown official branch {branch_name}")
    if fields["접수"] != listed.apply_period:
        raise GeochangContractError(f"course {listed.lc}: application period mismatch")
    if not fields["일정"].startswith(listed.event_period):
        raise GeochangContractError(f"course {listed.lc}: event period mismatch")
    if fields["대상"] != listed.target or fields["장소"] != listed.venue:
        raise GeochangContractError(f"course {listed.lc}: target/venue mismatch")
    if detail_capacity != listed.capacity:
        raise GeochangContractError(f"course {listed.lc}: capacity mismatch")
    apply_start, apply_end = _date_range(
        listed.apply_period, f"course {listed.lc} application"
    )
    if method == "바로접수 : 평생학습센터" and listed.source_status == "접수중" and not listed.application_control:
        raise GeochangContractError(f"course {listed.lc}: open direct application control missing")
    safe_text = " ".join(
        (listed.title, branch_name, listed.target, listed.venue, fields["일정"], fee)
    )
    if _PHONE_RE.search(safe_text) or _EMAIL_RE.search(safe_text) or _RESIDENT_RE.search(safe_text):
        raise GeochangContractError(f"course {listed.lc}: persisted detail fields contain PII")

    if listed.source_status != "접수중":
        application_type = "INFO_ONLY_SOURCE_STATUS"
    elif method == "바로접수 : 평생학습센터":
        application_type = "ONLINE_IDENTITY_REQUIRED_ENDPOINT_UNSTORED"
    elif method == "전화접수":
        application_type = "PHONE_CONTACT_UNSTORED"
    else:
        application_type = "EXTERNAL_OPERATOR_ENDPOINT_UNSTORED"
    extra = _target_extra(target)
    return {
        "provider": GEOCHANG_PROVIDER,
        "provider_course_id": f"{GEOCHANG_PROVIDER}:education:{listed.lc}",
        "title": listed.title,
        "description": listed.title,
        "branch": branch.source_name,
        "branch_code": branch.code,
        "source_branch": branch.source_name,
        "preserve_branch": True,
        "branch_url": GEOCHANG_CANONICAL_URL,
        "branch_address": branch.address,
        "raw_url": listed.detail_url,
        "application_url": "",
        "application_type": application_type,
        "application_method": method,
        "reservation_available": listed.source_status == "접수중",
        "status": listed.status,
        "raw_status": listed.source_status,
        "period": listed.event_period,
        "apply_period": listed.apply_period,
        "schedule_raw": fields["일정"],
        "start_date": listed.event_start.isoformat(),
        "end_date": listed.event_end.isoformat(),
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "target": listed.target,
        "capacity": listed.capacity,
        "capacity_total": listed.capacity,
        "fee": fee,
        "venue_name": listed.venue,
        "room": listed.venue,
        "address": branch.address,
        "venue_address": "",
        "image_url": listed.image_url,
        "category": "평생학습",
        "collection_category": _clean(extra.get("collection_category") or "평생학습"),
        "domain_category": _clean(extra.get("domain_category") or "평생학습"),
        "operator_type": _clean(extra.get("operator_type") or "지자체/공공기관"),
        "source_group": _clean(extra.get("source_group") or "lifelong_learning"),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "complete_ledger_html+safe_detail_html",
        "program_type": "교육",
        "municipality_code": GEOCHANG_MUNICIPALITY_CODE,
        "municipality_name": GEOCHANG_MUNICIPALITY_NAME,
        "municipality_full_name": GEOCHANG_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": GEOCHANG_PARSER,
            "identity": listed.lc,
            "source_page": listed.page,
            "source_sequence": listed.sequence,
            "source_status": listed.source_status,
            "source_apply_period": listed.apply_period,
            "source_event_period": listed.event_period,
            "detail_verified": True,
            "list_detail_binding": "lc+title+periods+target+venue+capacity",
            "official_branch_verified_from_detail": True,
            "list_application_control_present": listed.application_control,
            "roster_fragment_control_present": listed.roster_control,
            "external_operator_control_present": listed.external_control,
            "application_endpoint_fetched": False,
            "login_endpoint_fetched": False,
            "applicant_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "download_endpoint_fetched": False,
            "pii_endpoint_fetched": False,
            "applicant_section_cells_parsed": False,
            "discarded_fields": [
                "문의",
                "강 사 명",
                "학습 목표",
                "학습 계획",
                "개인준비물",
                "별로링크",
                "접수자 정보",
            ],
        },
    }


def _dedupe_source(rows: Iterable[_ListedCourse]) -> tuple[list[_ListedCourse], list[str]]:
    output: list[_ListedCourse] = []
    first: dict[str, _ListedCourse] = {}
    duplicates: list[str] = []
    for row in rows:
        prior = first.get(row.lc)
        if prior is None:
            first[row.lc] = row
            output.append(row)
            continue
        if prior.public_signature != row.public_signature:
            raise GeochangContractError(f"course {row.lc}: conflicting duplicate source rows")
        duplicates.append(row.lc)
    return output, duplicates


def _dedupe_output(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors = [f"forbidden key {key}" for key in _FORBIDDEN_ROW_KEYS if key in row]
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping):
        errors.append("raw_fields missing")
        return errors
    serialized = " ".join(_clean(value) for value in row.values() if isinstance(value, str))
    if _PHONE_RE.search(serialized) or _EMAIL_RE.search(serialized) or _RESIDENT_RE.search(serialized):
        errors.append("public row contains contact/PII")
    forbidden_tokens = (
        GEOCHANG_APPLICATION_PATH.lower(),
        "#lec_list",
        "/images/lec_doc/",
        "download",
        "downfile",
        "gcyka.or.kr",
        "psl.gc.ac.kr",
        "/01278/01316/01317.web",
    )
    for key, value in row.items():
        if isinstance(value, str) and any(token in value.lower() for token in forbidden_tokens):
            errors.append(f"unsafe endpoint persisted in {key}")
    return errors


def _initial_meta() -> dict[str, Any]:
    return {
        "municipality_code": GEOCHANG_MUNICIPALITY_CODE,
        "municipality_full_name": GEOCHANG_MUNICIPALITY_NAME,
        "owner_provider": GEOCHANG_PROVIDER,
        "canonical_provider": GEOCHANG_PROVIDER,
        "canonical_candidate_id": GEOCHANG_CANONICAL_CANDIDATE_ID,
        "canonical_url": GEOCHANG_CANONICAL_URL,
        "canonical_url_sha256": GEOCHANG_CANONICAL_URL_SHA256,
        "provider_decision": "retain incumbent provider; canonical all-course ledger owns detail aliases",
        "ownership_scope": GEOCHANG_OWNERSHIP_SCOPE,
        "candidate_audit": {key: dict(value) for key, value in GEOCHANG_CANDIDATE_AUDIT.items()},
        "excluded_evidence": [dict(value) for value in GEOCHANG_EXCLUDED_EVIDENCE],
        "owner_boundaries": [dict(value) for value in GEOCHANG_OWNER_BOUNDARIES],
        "parser": GEOCHANG_PARSER,
        "recommended_max_pages": GEOCHANG_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": GEOCHANG_RECOMMENDED_DETAIL_LIMIT,
        "recommended_timeout_seconds": 30,
        "fetch_attempts": GEOCHANG_FETCH_ATTEMPTS,
        "max_html_bytes": GEOCHANG_MAX_HTML_BYTES,
        "live_audit_baseline": dict(GEOCHANG_LIVE_AUDIT_BASELINE),
        "search_contract": {
            "header_method": "GET",
            "header_parameter": "st",
            "body_method": "POST",
            "body_parameter": "st",
            "pagination_method": "GET",
            "pagination_parameter": "page",
            "search_requests_during_collection": 0,
        },
        "source_requests": 0,
        "request_attempts": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "post_last_requests": 0,
        "source_rows": 0,
        "source_identity_count": 0,
        "duplicate_source_rows": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "returned_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "exact_empty_sentinel": False,
        "first_page_rechecked": False,
        "final_page_rechecked": False,
        "sentinel_rechecked": False,
        "details_complete": False,
        "privacy_violations": 0,
        "semantic_duplicate_count": 0,
        "application_endpoints_called": 0,
        "login_endpoints_called": 0,
        "applicant_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "download_endpoints_called": 0,
        "pii_endpoints_called": 0,
        "unsafe_endpoints_persisted": 0,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "configured_collection_error": "",
    }


def collect_geochang_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = GEOCHANG_RECOMMENDED_MAX_PAGES,
    detail_limit: int = GEOCHANG_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic, complete current/future Geochang snapshot."""

    meta = _initial_meta()
    if not is_geochang_education_target(target):
        meta["configured_collection_error"] = "target does not match exact retained Geochang owner"
        return [], GEOCHANG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], GEOCHANG_PARSER, meta
        session_factory = _raw_session
    try:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or timeout < 1
            or isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or max_pages < 1
            or max_pages > GEOCHANG_HARD_MAX_PAGES
            or isinstance(detail_limit, bool)
            or not isinstance(detail_limit, int)
            or detail_limit < 0
            or detail_limit > GEOCHANG_HARD_MAX_DETAILS
        ):
            raise ValueError("timeout/max_pages/detail_limit are invalid or unbounded")
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], GEOCHANG_PARSER, meta

    current_fetcher = fetcher or _default_fetcher
    session = session_factory()
    try:
        first = _fetch_page(session, 1, timeout, current_fetcher, meta)
        pages = first.advertised_pages
        if pages + 1 > max_pages:
            meta["source_cap_reached"] = True
            raise GeochangContractError(
                f"max_pages {max_pages} below advertised ledger+sentinel {pages + 1}"
            )
        page_objects = [first]
        for page in range(2, pages + 1):
            parsed = _fetch_page(session, page, timeout, current_fetcher, meta)
            if (
                parsed.advertised_total != first.advertised_total
                or parsed.advertised_pages != pages
            ):
                raise GeochangContractError("advertised ledger totals changed during traversal")
            page_objects.append(parsed)
        sentinel_page = _fetch_page(
            session, pages + 1, timeout, current_fetcher, meta, sentinel=True
        )
        if (
            sentinel_page.advertised_total != first.advertised_total
            or sentinel_page.advertised_pages != pages
            or not sentinel_page.exact_empty_sentinel
        ):
            raise GeochangContractError("post-last empty sentinel changed")

        source = [row for page in page_objects for row in page.rows]
        if len(source) != first.advertised_total:
            raise GeochangContractError("source row count does not match advertised total")
        unique, duplicate_lc = _dedupe_source(source)
        current = [row for row in unique if row.event_end >= cutoff]
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise GeochangContractError(
                f"detail_limit {detail_limit} below required {len(current)}"
            )
        source_lc = [row.lc for row in source]
        current_lc = [row.lc for row in current]
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "pages": pages,
                "page_size": GEOCHANG_PAGE_SIZE,
                "final_page_rows": len(page_objects[-1].rows),
                "post_last_page": pages + 1,
                "source_rows": len(source),
                "source_total": len(source),
                "source_identity_count": len(unique),
                "duplicate_source_rows": len(duplicate_lc),
                "duplicate_source_lc": duplicate_lc,
                "current_source_count": len(current),
                "expired_source_count": len(source) - len(current),
                "unique_expired_source_count": len(unique) - len(current),
                "source_status_counts": dict(Counter(row.source_status for row in source)),
                "current_source_status_counts": dict(Counter(row.source_status for row in current)),
                "source_lc_sha256": hashlib.sha256("\n".join(source_lc).encode()).hexdigest(),
                "current_lc_sha256": hashlib.sha256("\n".join(current_lc).encode()).hexdigest(),
                "first_page_lc": [row.lc for row in first.rows],
                "final_page_lc": [row.lc for row in page_objects[-1].rows],
                "pagination_complete": True,
                "exact_empty_sentinel": True,
            }
        )

        rows: list[dict[str, Any]] = []
        for listed in current:
            meta["detail_pages"] += 1
            soup = _fetch_soup(
                session, listed.detail_url, timeout, current_fetcher, meta
            )
            rows.append(_parse_detail(target, listed, soup))

        checked_first = _fetch_page(session, 1, timeout, current_fetcher, meta)
        checked_final = _fetch_page(session, pages, timeout, current_fetcher, meta)
        checked_sentinel = _fetch_page(
            session, pages + 1, timeout, current_fetcher, meta, sentinel=True
        )
        if checked_first.signature != first.signature:
            raise GeochangContractError("first page changed after detail traversal")
        meta["first_page_rechecked"] = True
        if checked_final.signature != page_objects[-1].signature:
            raise GeochangContractError("final page changed after detail traversal")
        meta["final_page_rechecked"] = True
        if checked_sentinel.signature != sentinel_page.signature:
            raise GeochangContractError("sentinel changed after detail traversal")
        meta["sentinel_rechecked"] = True

        rows = list((dedupe_rows or _dedupe_output)(rows))
        expected_ids = {
            f"{GEOCHANG_PROVIDER}:education:{listed.lc}" for listed in current
        }
        if len(rows) != len(current) or {
            _clean(row.get("provider_course_id")) for row in rows
        } != expected_ids:
            raise GeochangContractError("dedupe changed the current course identity set")
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        meta["privacy_violations"] = len(privacy_errors)
        if privacy_errors:
            raise GeochangContractError("; ".join(privacy_errors[:5]))
        semantic_counts = Counter(
            (
                _clean(row.get("title")).casefold(),
                _clean(row.get("branch")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
                _clean(row.get("venue_name")),
            )
            for row in rows
        )
        semantic_duplicates = sum(
            count - 1 for count in semantic_counts.values() if count > 1
        )
        meta["semantic_duplicate_count"] = semantic_duplicates
        if semantic_duplicates:
            raise GeochangContractError("semantic duplicate current courses detected")
        expected_requests = pages + 1 + len(current) + 3
        if meta["source_requests"] != expected_requests:
            raise GeochangContractError("bounded request accounting changed")
        meta.update(
            {
                "returned_count": len(rows),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in rows)),
                "raw_status_counts": dict(Counter(_clean(row.get("raw_status")) for row in rows)),
                "branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
                "application_method_counts": dict(
                    Counter(_clean(row.get("application_method")) for row in rows)
                ),
                "internal_application_control_count": sum(row.application_control for row in current),
                "actionable_internal_application_control_count": sum(
                    row.application_control and row.source_status == "접수중" for row in current
                ),
                "roster_fragment_control_count": sum(row.roster_control for row in current),
                "external_operator_control_count": sum(row.external_control for row in current),
                "external_operator_url_control_count": sum(
                    row.external_control_has_url for row in current
                ),
                "reservation_available_count": sum(
                    bool(row.get("reservation_available")) for row in rows
                ),
                "details_complete": meta["detail_pages"] == len(current),
                "no_current_data": not rows,
                "expected_requests_per_snapshot": expected_requests,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return rows, GEOCHANG_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "returned_count": 0,
            }
        )
        return [], GEOCHANG_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_geochang_education


__all__ = [
    "GEOCHANG_PROVIDER",
    "GEOCHANG_MUNICIPALITY_CODE",
    "GEOCHANG_MUNICIPALITY_NAME",
    "GEOCHANG_CANONICAL_URL",
    "GEOCHANG_GUIDE_URL",
    "GEOCHANG_DETAIL_CANDIDATE_URLS",
    "GEOCHANG_CANONICAL_URL_SHA256",
    "GEOCHANG_CANONICAL_CANDIDATE_ID",
    "GEOCHANG_GUIDE_CANDIDATE_ID",
    "GEOCHANG_DETAIL_CANDIDATE_IDS",
    "GEOCHANG_COUNTY_HOME_CANDIDATE_ID",
    "GEOCHANG_COUNTY_MAIN_CANDIDATE_ID",
    "GEOCHANG_CANDIDATE_AUDIT",
    "GEOCHANG_EXCLUDED_EVIDENCE",
    "GEOCHANG_OWNER_BOUNDARIES",
    "GEOCHANG_BRANCHES",
    "GEOCHANG_LIVE_AUDIT_BASELINE",
    "GEOCHANG_RECOMMENDED_MAX_PAGES",
    "GEOCHANG_RECOMMENDED_DETAIL_LIMIT",
    "GEOCHANG_FETCH_ATTEMPTS",
    "GEOCHANG_PARSER",
    "GeochangContractError",
    "collect_geochang_education",
    "collect",
    "is_geochang_education_target",
    "is_target",
]
