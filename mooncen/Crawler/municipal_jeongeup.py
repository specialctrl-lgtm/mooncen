"""Fail-closed collector for Jeongeup City's complete education ledger.

The search-review URL is not a course ledger: it redirects to the culture
site's whole-menu sitemap.  Jeongeup's official integrated reservation site
instead exposes four sibling education lists (lifelong learning, Danpung
Academy, youth culture/sports, and youth counselling).  There is no complete
parent list, so this collector treats those four lists as one owner and proves
every advertised page for every branch before publishing anything.

Every list has a ten-row page boundary.  A request for the exact page after
the advertised last page repeats the last page while dropping the current-page
marker.  A complete snapshot therefore proves that clamped overflow boundary,
rechecks the first/last/overflow edges of all four branches, verifies every
current/future detail, and never calls the inline application form, reservation
lookup, attachment, or other PII-bearing endpoint.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


JEONGEUP_PROVIDER = "MUNI_WWW_JEONGEUP_GO_KR_C8631DF4"
JEONGEUP_CANONICAL_CANDIDATE_ID = "MUNI_IR_8D354C3B4F5D"
JEONGEUP_REVIEW_SITEMAP_CANDIDATE_ID = "MUNI_IR_33F33834A488"
JEONGEUP_PORTAL_CANDIDATE_ID = "MUNI_IR_361D8A742310"
JEONGEUP_MUNICIPALITY_CODE = "5218000000"
JEONGEUP_MUNICIPALITY_NAME = "전북특별자치도 정읍시"

JEONGEUP_HOST = "www.jeongeup.go.kr"
JEONGEUP_PATH = "/reserve/index.jeongeup"
JEONGEUP_LINK_PATH = "/index.jeongeup"
JEONGEUP_CANONICAL_MENU = "DOM_000001201001000000"
JEONGEUP_CANONICAL_URL = (
    f"https://{JEONGEUP_HOST}{JEONGEUP_PATH}?menuCd={JEONGEUP_CANONICAL_MENU}"
)
JEONGEUP_CANONICAL_URL_SHA256 = (
    "8d354c3b4f5d40e5f8493ee7cf24fcf6cd80fcf6516f49802bbbd41ecbe03696"
)
JEONGEUP_PAGE_SIZE = 10
JEONGEUP_RECOMMENDED_MAX_PAGES = 40
JEONGEUP_RECOMMENDED_DETAIL_LIMIT = 50
JEONGEUP_RECOMMENDED_MAX_WORKERS = 4
JEONGEUP_FETCH_ATTEMPTS = 2
JEONGEUP_MAX_HTML_BYTES = 2_000_000
JEONGEUP_PARSER = (
    "jeongeup_four_branch_complete_education_re_ledger+"
    "advertised_totals_and_clamped_overflow+stable_all_branch_edges+"
    "all_current_details+inline_application_control_no_submit+"
    "pii_and_free_text_allowlist"
)
JEONGEUP_OWNERSHIP_SCOPE = (
    "jeongeup_integrated_reservation_four_branch_complete_education_re_ledger"
)


class JeongeupContractError(ValueError):
    """Raised when the official source no longer satisfies the audited contract."""


@dataclass(frozen=True)
class JeongeupBranch:
    code: str
    name: str
    list_menu: str
    detail_menu: str
    candidate_id: str
    derived_provider: str
    category_options: tuple[tuple[str, str], ...]

    @property
    def url(self) -> str:
        return (
            f"https://{JEONGEUP_HOST}{JEONGEUP_PATH}?"
            + urlencode((('menuCd', self.list_menu),))
        )


_LIFELONG_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("", "선택"),
    ("001", "기초문해"),
    ("002", "학력보완"),
    ("003", "성인진로역량개발"),
    ("004", "문화예술"),
    ("005", "인문교양"),
    ("006", "시민참여"),
)
_NO_CATEGORIES: tuple[tuple[str, str], ...] = (("", "선택"),)

JEONGEUP_BRANCHES: tuple[JeongeupBranch, ...] = (
    JeongeupBranch(
        "JEONGEUP_LIFELONG_LEARNING_CENTER",
        "평생학습관",
        "DOM_000001201001000000",
        "DOM_000001201001001000",
        "MUNI_IR_8D354C3B4F5D",
        "MUNI_WWW_JEONGEUP_GO_KR_C8631DF4",
        _LIFELONG_CATEGORIES,
    ),
    JeongeupBranch(
        "JEONGEUP_DANPUNG_ACADEMY",
        "정읍 단풍아카데미",
        "DOM_000001201005000000",
        "DOM_000001201005001000",
        "MUNI_IR_05A6A01884F0",
        "MUNI_WWW_JEONGEUP_GO_KR_2E6E7F91",
        _NO_CATEGORIES,
    ),
    JeongeupBranch(
        "JEONGEUP_YOUTH_CULTURE_SPORTS_CENTER",
        "청소년문화체육관",
        "DOM_000001201007000000",
        "DOM_000001201007001000",
        "MUNI_IR_5DB117C7E9B7",
        "MUNI_WWW_JEONGEUP_GO_KR_0D01420A",
        _NO_CATEGORIES,
    ),
    JeongeupBranch(
        "JEONGEUP_YOUTH_COUNSELLING_WELFARE_CENTER",
        "청소년상담복지센터",
        "DOM_000001201008000000",
        "DOM_000001201008001000",
        "MUNI_IR_D40E4635D19A",
        "MUNI_WWW_JEONGEUP_GO_KR_FCCF0052",
        _NO_CATEGORIES,
    ),
)
JEONGEUP_BRANCH_BY_LIST_MENU = {
    branch.list_menu: branch for branch in JEONGEUP_BRANCHES
}
JEONGEUP_BRANCH_BY_DETAIL_MENU = {
    branch.detail_menu: branch for branch in JEONGEUP_BRANCHES
}

JEONGEUP_REVIEW_SITEMAP_URL = (
    "https://www.jeongeup.go.kr/index.jeongeup?"
    "menuCd=DOM_000000607001000000"
)
JEONGEUP_REVIEW_SITEMAP_DESTINATION = (
    "https://www.jeongeup.go.kr/culture/index.jeongeup?"
    "menuCd=DOM_000000607001000000"
)
JEONGEUP_PORTAL_URL = f"https://{JEONGEUP_HOST}{JEONGEUP_PATH}"

JEONGEUP_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    JEONGEUP_CANONICAL_CANDIDATE_ID: {
        "provider": JEONGEUP_PROVIDER,
        "url": JEONGEUP_CANONICAL_URL,
        "url_sha256": JEONGEUP_CANONICAL_URL_SHA256,
        "decision": "new_owner_and_canonical_first_branch",
    },
    JEONGEUP_REVIEW_SITEMAP_CANDIDATE_ID: {
        "provider": "MUNI_WWW_JEONGEUP_GO_KR_BBC04A35",
        "url": JEONGEUP_REVIEW_SITEMAP_URL,
        "redirect_destination": JEONGEUP_REVIEW_SITEMAP_DESTINATION,
        "decision": "exclude_redirected_culture_whole_menu_without_course_ledger",
    },
    JEONGEUP_PORTAL_CANDIDATE_ID: {
        "provider": "MUNI_WWW_JEONGEUP_GO_KR_EFFEC561",
        "url": JEONGEUP_PORTAL_URL,
        "decision": "exclude_recent_mixed_owner_portal_not_complete_ledger",
    },
    **{
        branch.candidate_id: {
            "provider": JEONGEUP_PROVIDER,
            "url": branch.url,
            "derived_provider_not_used": branch.derived_provider,
            "decision": "include_as_sibling_branch_under_single_complete_education_owner",
        }
        for branch in JEONGEUP_BRANCHES
        if branch.list_menu != JEONGEUP_CANONICAL_MENU
    },
}

JEONGEUP_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "url": "https://spt.jeongeup.go.kr/fmcs/5",
        "decision": "exclude_separate_sports_course_identity_owner",
        "owner": "JEONGEUP_SPORTS_FMCS_SEPARATE_OWNER",
    },
    {
        "url": "https://lib.jeongeup.go.kr/main/cultureReq.do?PID=0402",
        "decision": "exclude_separate_municipal_library_program_owner",
        "owner": "JEONGEUP_MUNICIPAL_LIBRARY_SEPARATE_OWNER",
    },
    {
        "url": (
            f"https://{JEONGEUP_HOST}{JEONGEUP_PATH}?"
            "menuCd=DOM_000001202001000000"
        ),
        "decision": "exclude_separate_museum_culture_experience_re_ledger",
        "owner": "JEONGEUP_MUSEUM_EXPERIENCE_SEPARATE_OWNER",
    },
    {
        "url": (
            f"https://{JEONGEUP_HOST}{JEONGEUP_PATH}?"
            "menuCd=DOM_000001202002001000"
        ),
        "decision": "exclude_separate_art_museum_program_re_ledger",
        "owner": "JEONGEUP_ART_MUSEUM_SEPARATE_OWNER",
    },
    {
        "url": (
            f"https://{JEONGEUP_HOST}/reserve/facilitie/list.jeongeup?"
            "menuCd=DOM_000001207001001000"
        ),
        "decision": "exclude_separate_camping_facility_reservation_owner",
        "owner": "JEONGEUP_FACILITY_RESERVATION_SEPARATE_OWNER",
    },
    {
        "url": (
            "https://sotong.jeongeup.go.kr/board.es?act=view&bid=0001&"
            "list_no=2320&mid=a10704000000"
        ),
        "decision": "exclude_single_recruitment_notice_without_course_identities",
        "owner": "",
    },
)

JEONGEUP_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "cutoff": "2026-07-23",
    "advertised_total": 236,
    "data_pages": 25,
    "branch_totals": {
        "평생학습관": 77,
        "정읍 단풍아카데미": 16,
        "청소년문화체육관": 119,
        "청소년상담복지센터": 24,
    },
    "branch_last_pages": {
        "평생학습관": 8,
        "정읍 단풍아카데미": 2,
        "청소년문화체육관": 12,
        "청소년상담복지센터": 3,
    },
    "source_status_counts": {"교육종료": 229, "교육중": 5, "접수완료": 2},
    "current_rows": 7,
    "current_branch_counts": {
        "평생학습관": 3,
        "청소년문화체육관": 3,
        "청소년상담복지센터": 1,
    },
    "current_raw_status_counts": {"교육중": 5, "접수완료": 2},
    "detail_pages": 7,
    "application_controls": 0,
    "expected_requests": 48,
}

JEONGEUP_STATUS_MAP: Mapping[str, str] = {
    "계획중": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "접수완료": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
}
JEONGEUP_STATUS_CLASSES: Mapping[str, tuple[frozenset[str], ...]] = {
    "계획중": (frozenset({"rec", "rec01"}),),
    "접수예정": (frozenset({"rec", "rec01"}), frozenset({"rec", "rec04"})),
    "접수중": (
        frozenset({"rec", "rec01"}),
        frozenset({"rec", "rec02"}),
        frozenset({"rec", "rec03"}),
    ),
    "접수완료": (frozenset({"rec", "rec03"}), frozenset({"rec", "rec04"})),
    "교육중": (frozenset({"rec", "rec04"}),),
    "교육종료": (frozenset({"rec", "rec04"}),),
}
JEONGEUP_LIST_LABELS = ("교육기간", "접수기간", "교육장")
JEONGEUP_DETAIL_LABELS = (
    "접수기간",
    "교육기간",
    "교육시간",
    "교육장",
    "강사명",
    "수강료/재료비",
    "교육대상",
    "신청/정원",
    "문의담당자",
    "문의전화",
    "교육내용",
    "강의자료",
    "접수상태",
)
JEONGEUP_DETAIL_LABELS_WITH_HOMEPAGE = (
    *JEONGEUP_DETAIL_LABELS[:8],
    "홈페이지",
    *JEONGEUP_DETAIL_LABELS[8:],
)
JEONGEUP_DISCARDED_DETAIL_FIELDS = (
    "강사명",
    "문의담당자",
    "문의전화",
    "교육내용",
    "강의자료",
    "홈페이지",
    "inline application form fields",
    "attachments and images",
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^RE\d{7}$")
_DATE_RANGE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})$"
)
_CAPACITY = re.compile(r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)$")
_RESULT_COUNT = re.compile(r"(\d[\d,]*)\s*건")
_PHONE = re.compile(r"(?<!\d)(?:0\d{1,2}[\s().-]*)?\d{3,4}[\s.-]+\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_WRITE_FUNC = re.compile(r"^\s*writeFunc\s*\(\s*\)\s*;?\s*$")

_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_branch",
        "source_branch_menu",
        "source_page",
        "source_position",
        "source_status",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_target",
        "source_venue",
        "source_capacity_current",
        "source_capacity_total",
        "detail_verified",
        "application_control_present",
        "application_control_verified",
        "application_form_submitted",
        "application_endpoint_fetched",
        "reservation_lookup_endpoint_fetched",
        "attachment_endpoint_fetched",
        "discarded_detail_fields",
        "address_policy",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "manager",
        "manager_name",
        "contact",
        "phone",
        "email",
        "homepage",
        "attachments",
        "attachment_urls",
        "course_content",
        "detail_description",
        "source_html",
        "raw_html",
        "applicant_name",
        "applicant_phone",
        "applicant_birth_date",
        "applicant_address",
        "password",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_jeongeup_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != JEONGEUP_PROVIDER:
        return False
    url = _clean(_target_value(target, "url"))
    if url != JEONGEUP_CANONICAL_URL:
        return False
    try:
        parsed = urlparse(url)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == JEONGEUP_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == JEONGEUP_PATH
        and query == [("menuCd", JEONGEUP_CANONICAL_MENU)]
        and not parsed.fragment
    )


is_target = is_jeongeup_education_target


def _raw_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _list_url(branch: JeongeupBranch, page: int = 1) -> str:
    query = [("menuCd", branch.list_menu)]
    if page > 1:
        query.append(("startPage", str(page)))
    return f"https://{JEONGEUP_HOST}{JEONGEUP_PATH}?{urlencode(query)}"


def _detail_url(branch: JeongeupBranch, identity: str) -> str:
    return (
        f"https://{JEONGEUP_HOST}{JEONGEUP_PATH}?"
        + urlencode((('menuCd', branch.detail_menu), ('reUniqId', identity)))
    )


def _same_response_url(actual: str, expected: str) -> bool:
    try:
        left = urlparse(actual)
        right = urlparse(expected)
        return bool(
            left.scheme == right.scheme == "https"
            and (left.hostname or "").lower()
            == (right.hostname or "").lower()
            == JEONGEUP_HOST
            and left.port is None
            and right.port is None
            and left.username is None
            and left.password is None
            and left.path == right.path == JEONGEUP_PATH
            and parse_qsl(left.query, keep_blank_values=True, strict_parsing=True)
            == parse_qsl(right.query, keep_blank_values=True, strict_parsing=True)
            and not left.fragment
            and not right.fragment
        )
    except ValueError:
        return False


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
) -> tuple[BeautifulSoup, int]:
    last_error: Optional[Exception] = None
    for attempt in range(1, JEONGEUP_FETCH_ATTEMPTS + 1):
        try:
            response = fetcher(session, url, timeout)
            if getattr(response, "status_code", None) != 200:
                raise JeongeupContractError(
                    f"HTTP {getattr(response, 'status_code', None)} for {url}"
                )
            if getattr(response, "history", None):
                raise JeongeupContractError(f"redirect is not allowed for {url}")
            response_url = _clean(getattr(response, "url", ""))
            if not _same_response_url(response_url, url):
                raise JeongeupContractError(
                    f"response URL drift: expected {url}, received {response_url}"
                )
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", "")).encode("utf-8")
            if len(content) > JEONGEUP_MAX_HTML_BYTES:
                raise JeongeupContractError(f"HTML exceeds byte limit for {url}")
            return BeautifulSoup(content, "html.parser"), attempt
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _options(select: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in select.find_all("option", recursive=False)
    )


def _unique_query(url: str) -> tuple[Any, dict[str, str]]:
    try:
        parsed = urlparse(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise JeongeupContractError(f"malformed URL: {url}") from exc
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise JeongeupContractError(f"duplicate query key {key}: {url}")
        values[key] = value
    return parsed, values


def _parse_count(node: Any, label: str) -> int:
    text = _clean(node.get_text(" ", strip=True))
    if label not in text:
        raise JeongeupContractError(f"result count label drift: expected {label}")
    match = _RESULT_COUNT.search(text)
    if not match:
        raise JeongeupContractError(f"result count shape drift: {text}")
    return int(match.group(1).replace(",", ""))


def _validate_list_form(
    soup: BeautifulSoup,
    branch: JeongeupBranch,
    requested_page: int,
) -> tuple[int, int, int]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != f"교육/강좌 > {branch.name}":
        raise JeongeupContractError(f"{branch.name}: list title drift")
    heading = soup.select_one("#content h3")
    if heading is None or _clean(heading.get_text(" ", strip=True)) != branch.name:
        raise JeongeupContractError(f"{branch.name}: list heading drift")

    form = soup.select_one('form[name="listForm"]')
    if form is None:
        raise JeongeupContractError(f"{branch.name}: missing listForm")
    if (
        _clean(form.get("method")).lower() != "get"
        or _clean(form.get("action")) != JEONGEUP_LINK_PATH
    ):
        raise JeongeupContractError(f"{branch.name}: list form action drift")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select('input[type="hidden"][name]')
    }
    expected_hidden = {
        "menuCd": branch.list_menu,
        "startPage": str(requested_page),
        "searchCondition": "RE_NAME",
        "orderField": "",
        "searchDateGubun": "3",
    }
    if hidden != expected_hidden:
        raise JeongeupContractError(f"{branch.name}: hidden list filter drift")
    keyword = form.select_one('input[name="searchKeyword"]')
    if keyword is None or _clean(keyword.get("value")):
        raise JeongeupContractError(f"{branch.name}: keyword filter drift")
    category = form.select_one('select[name="lectureType"]')
    if category is None or _options(category) != branch.category_options:
        raise JeongeupContractError(f"{branch.name}: category selector drift")
    buttons = tuple(
        (
            _clean(button.get_text(" ", strip=True)),
            _clean(button.get("onclick")),
        )
        for button in form.select("ul.btn_condition button")
    )
    if buttons != (("전체", "searchDatefunc('3')"), ("접수중", "searchDatefunc('1')")):
        raise JeongeupContractError(f"{branch.name}: status selector drift")

    result = soup.select("ul.search_result > li")
    if len(result) != 3:
        raise JeongeupContractError(f"{branch.name}: result summary drift")
    return (
        _parse_count(result[0], "모집중"),
        _parse_count(result[1], "마감"),
        _parse_count(result[2], "검색된 결과"),
    )


def _parse_pager_href(href: str, branch: JeongeupBranch) -> int:
    absolute = urljoin(JEONGEUP_CANONICAL_URL, href)
    parsed, values = _unique_query(absolute)
    expected_keys = {
        "menuCd",
        "searchCondition",
        "searchKeyword",
        "orderField",
        "orderSort",
        "searchDateGubun",
        "startPage",
    }
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != JEONGEUP_HOST
        or parsed.port is not None
        or parsed.path not in {JEONGEUP_LINK_PATH, JEONGEUP_PATH}
        or set(values) != expected_keys
        or values["menuCd"] != branch.list_menu
        or values["searchCondition"] != "RE_NAME"
        or values["searchKeyword"] != ""
        or values["orderField"] != ""
        or values["orderSort"] != "asc"
        or values["searchDateGubun"] != "3"
        or not values["startPage"].isdigit()
        or int(values["startPage"]) < 1
        or parsed.fragment
    ):
        raise JeongeupContractError(f"{branch.name}: pager URL drift")
    return int(values["startPage"])


def _advertised_last(soup: BeautifulSoup, branch: JeongeupBranch) -> int:
    pager = soup.select_one("div.bbs_page")
    if pager is None:
        raise JeongeupContractError(f"{branch.name}: missing pager")
    pages = [
        _parse_pager_href(_clean(anchor.get("href")), branch)
        for anchor in pager.select("a[href]")
    ]
    if not pages:
        raise JeongeupContractError(f"{branch.name}: pager has no page links")
    return max(pages)


def _parse_period(value: str, identity: str, label: str) -> tuple[date, date]:
    match = _DATE_RANGE.fullmatch(_clean(value))
    if not match:
        raise JeongeupContractError(f"course {identity}: {label} period shape drift")
    start, end = (date.fromisoformat(token) for token in match.groups())
    if end < start:
        raise JeongeupContractError(f"course {identity}: reversed {label} period")
    return start, end


def _parse_detail_href(
    href: str,
    branch: JeongeupBranch,
    page: int,
) -> tuple[str, str]:
    absolute = urljoin(JEONGEUP_CANONICAL_URL, href)
    parsed, values = _unique_query(absolute)
    expected = {
        "menuCd",
        "reUniqId",
        "searchCondition",
        "searchKeyword",
        "orderField",
        "orderSort",
        "searchDateGubun",
        "startPage",
    }
    identity = values.get("reUniqId", "")
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != JEONGEUP_HOST
        or parsed.port is not None
        or parsed.path not in {JEONGEUP_LINK_PATH, JEONGEUP_PATH}
        or set(values) != expected
        or values.get("menuCd") != branch.detail_menu
        or not _IDENTITY.fullmatch(identity)
        or values.get("searchCondition") != "RE_NAME"
        or values.get("searchKeyword") != ""
        or values.get("orderField") != ""
        or values.get("orderSort") != "asc"
        or values.get("searchDateGubun") != "3"
        or values.get("startPage") != str(page)
        or parsed.fragment
    ):
        raise JeongeupContractError(f"{branch.name}: detail URL drift")
    return identity, _detail_url(branch, identity)


def _direct_text(node: Any) -> str:
    return _clean(" ".join(str(value) for value in node.find_all(string=True, recursive=False)))


def _parse_list_fields(node: Any, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    labels: list[str] = []
    for dd in node.select("dl > dd"):
        strong = dd.find("strong", recursive=False)
        if strong is None:
            raise JeongeupContractError(f"course {identity}: malformed list field")
        label = _clean(strong.get_text(" ", strip=True))
        if label in fields:
            raise JeongeupContractError(f"course {identity}: repeated list field")
        labels.append(label)
        value = _clean(
            " ".join(
                str(item)
                for item in dd.find_all(string=True)
                if item.parent is not strong
            )
        )
        fields[label] = value
    if tuple(labels) != JEONGEUP_LIST_LABELS:
        raise JeongeupContractError(f"course {identity}: list field vocabulary drift")
    return fields


def _validate_open_list_control(
    control: Any,
    branch: JeongeupBranch,
    identity: str,
    page: int,
) -> None:
    if frozenset(control.get("class") or ()) != frozenset(
        {"possible", "possible01", "blink"}
    ):
        raise JeongeupContractError(f"course {identity}: open control class drift")
    if _clean(control.get_text(" ", strip=True)) not in {
        "예약신청",
        "접수신청",
        "신청하기",
    }:
        raise JeongeupContractError(f"course {identity}: open control text drift")
    href = _clean(control.get("href"))
    if not href:
        raise JeongeupContractError(f"course {identity}: open control lacks detail URL")
    control_identity, _ = _parse_detail_href(href, branch, page)
    if control_identity != identity:
        raise JeongeupContractError(f"course {identity}: open control identity drift")
    if _clean(control.get("onclick")):
        raise JeongeupContractError(f"course {identity}: unexpected list onclick")


def _parse_list_row(
    node: Any,
    branch: JeongeupBranch,
    page: int,
    position: int,
) -> dict[str, Any]:
    link = node.select_one('dl > dt > a[href*="reUniqId"]')
    if link is None:
        raise JeongeupContractError(f"{branch.name}: row lacks detail link")
    identity, detail_url = _parse_detail_href(_clean(link.get("href")), branch, page)
    title = _clean(link.get_text(" ", strip=True))
    if not title:
        raise JeongeupContractError(f"course {identity}: empty title")
    fields = _parse_list_fields(node, identity)
    event_start, event_end = _parse_period(fields["교육기간"], identity, "education")
    apply_start, apply_end = _parse_period(fields["접수기간"], identity, "application")
    venue = fields["교육장"]
    if not venue:
        raise JeongeupContractError(f"course {identity}: empty list venue")

    status_node = node.select_one("p.rec")
    if status_node is None:
        raise JeongeupContractError(f"course {identity}: missing source status")
    raw_status = _direct_text(status_node)
    if raw_status not in JEONGEUP_STATUS_MAP:
        raise JeongeupContractError(f"course {identity}: unknown status {raw_status}")
    if frozenset(status_node.get("class") or ()) not in JEONGEUP_STATUS_CLASSES[raw_status]:
        raise JeongeupContractError(f"course {identity}: status class drift")
    capacity_node = status_node.find("span")
    capacity_text = _clean(capacity_node.get_text(" ", strip=True) if capacity_node else "")
    capacity_match = _CAPACITY.fullmatch(capacity_text)
    if not capacity_match:
        raise JeongeupContractError(f"course {identity}: list capacity shape drift")
    capacity_current, capacity_total = (
        int(token.replace(",", "")) for token in capacity_match.groups()
    )

    controls = node.select("a.possible")
    if len(controls) != 1:
        raise JeongeupContractError(f"course {identity}: list control count drift")
    control = controls[0]
    open_control = raw_status == "접수중"
    if open_control:
        _validate_open_list_control(control, branch, identity, page)
    else:
        expected_text = (
            "접수대기"
            if JEONGEUP_STATUS_MAP[raw_status] == "SCHEDULED"
            else "접수마감"
        )
        if (
            frozenset(control.get("class") or ())
            != frozenset({"possible", "possible02"})
            or _clean(control.get_text(" ", strip=True)) != expected_text
            or _clean(control.get("href"))
            or _clean(control.get("onclick"))
        ):
            raise JeongeupContractError(
                f"course {identity}: inactive control drift"
            )

    return {
        "identity": identity,
        "detail_url": detail_url,
        "branch": branch,
        "page": page,
        "position": position,
        "title": title,
        "event_start": event_start,
        "event_end": event_end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "venue": venue,
        "raw_status": raw_status,
        "status": JEONGEUP_STATUS_MAP[raw_status],
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "list_application_control": open_control,
    }


def _parse_list_page(
    soup: BeautifulSoup,
    branch: JeongeupBranch,
    requested_page: int,
) -> dict[str, Any]:
    open_count, closed_count, advertised_total = _validate_list_form(
        soup, branch, requested_page
    )
    advertised_last = _advertised_last(soup, branch)
    calculated_last = max(
        1, (advertised_total + JEONGEUP_PAGE_SIZE - 1) // JEONGEUP_PAGE_SIZE
    )
    if advertised_last != calculated_last:
        raise JeongeupContractError(f"{branch.name}: total/last-page disagreement")
    items = soup.select("div.bbs_list01 > ul > li")
    if not items:
        raise JeongeupContractError(f"{branch.name}: education list unexpectedly empty")
    if len(items) > JEONGEUP_PAGE_SIZE:
        raise JeongeupContractError(f"{branch.name}: page exceeds ten rows")
    rows = [
        _parse_list_row(item, branch, requested_page, position)
        for position, item in enumerate(items, 1)
    ]
    pager = soup.select_one("div.bbs_page")
    assert pager is not None
    current = pager.select_one("span.on")
    current_page: Optional[int] = None
    if current is not None:
        text = _clean(current.get_text(" ", strip=True))
        if not text.isdigit():
            raise JeongeupContractError(f"{branch.name}: current-page marker drift")
        current_page = int(text)
    return {
        "branch": branch,
        "requested_page": requested_page,
        "current_page": current_page,
        "advertised_total": advertised_total,
        "advertised_last": advertised_last,
        "result_open_count": open_count,
        "result_closed_count": closed_count,
        "rows": rows,
    }


def _page_signature(parsed: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        parsed["branch"].list_menu,
        int(parsed["advertised_total"]),
        int(parsed["advertised_last"]),
        int(parsed["result_open_count"]),
        int(parsed["result_closed_count"]),
        tuple(
            (
                row["identity"],
                row["position"],
                row["title"],
                row["raw_status"],
                row["event_start"].isoformat(),
                row["event_end"].isoformat(),
                row["apply_start"].isoformat(),
                row["apply_end"].isoformat(),
                row["venue"],
                row["capacity_current"],
                row["capacity_total"],
                row["list_application_control"],
            )
            for row in parsed["rows"]
        ),
    )


def _detail_fields(soup: BeautifulSoup, identity: str) -> dict[str, str]:
    table = soup.select_one("div.edu_view01 table.view_table")
    if table is None:
        raise JeongeupContractError(f"course {identity}: missing detail table")
    fields: dict[str, str] = {}
    labels: list[str] = []
    for row in table.select("tbody > tr"):
        label_node = row.find("th", recursive=False)
        value_node = row.find("td", recursive=False)
        if label_node is None or value_node is None:
            raise JeongeupContractError(f"course {identity}: malformed detail field")
        label = _clean(label_node.get_text(" ", strip=True))
        if label in fields:
            raise JeongeupContractError(f"course {identity}: repeated detail field {label}")
        labels.append(label)
        fields[label] = _clean(value_node.get_text(" ", strip=True))
    if tuple(labels) not in {JEONGEUP_DETAIL_LABELS, JEONGEUP_DETAIL_LABELS_WITH_HOMEPAGE}:
        raise JeongeupContractError(f"course {identity}: detail vocabulary/order drift")
    return fields


def _validate_back_control(node: Any, listed: Mapping[str, Any]) -> None:
    branch: JeongeupBranch = listed["branch"]
    href = _clean(node.get("href"))
    absolute = urljoin(JEONGEUP_CANONICAL_URL, href)
    parsed, values = _unique_query(absolute)
    expected = {
        "menuCd",
        "searchCondition",
        "searchKeyword",
        "orderField",
        "orderSort",
        "searchDateGubun",
        "startPage",
    }
    navigation_context = (
        values.get("orderSort"),
        values.get("startPage"),
    )
    allowed_navigation_contexts = {
        ("asc", str(listed["page"])),
        ("desc", "1"),
    }
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != JEONGEUP_HOST
        or parsed.port is not None
        or parsed.path not in {JEONGEUP_LINK_PATH, JEONGEUP_PATH}
        or set(values) != expected
        or values.get("menuCd") != branch.list_menu
        or values.get("searchCondition") != "RE_NAME"
        or values.get("searchKeyword") != ""
        or values.get("orderField") != ""
        or navigation_context not in allowed_navigation_contexts
        or values.get("searchDateGubun") != "3"
        or parsed.fragment
    ):
        raise JeongeupContractError(f"course {listed['identity']}: back control drift")


def _detail_controls(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
) -> tuple[bool, str]:
    identity = str(listed["identity"])
    apply_container = soup.select_one("div.edu_view01 div.btn > p.btn_apply")
    back = soup.select_one("div.edu_view01 div.btn > p.btn_back > a[href]")
    write_area = soup.select_one("div.edu_view01 #writeArea")
    if apply_container is None or back is None or write_area is None:
        raise JeongeupContractError(f"course {identity}: detail controls missing")
    _validate_back_control(back, listed)
    controls = apply_container.find_all(["button", "a"], recursive=False)
    if len(controls) != 1:
        raise JeongeupContractError(f"course {identity}: apply control count drift")
    control = controls[0]
    raw_status = str(listed["raw_status"])
    if raw_status == "접수중":
        if (
            control.name != "button"
            or _clean(control.get_text(" ", strip=True)) not in {"신청", "신청하기", "접수신청"}
            or not _WRITE_FUNC.fullmatch(_clean(control.get("onclick")))
            or _clean(control.get("formaction"))
        ):
            raise JeongeupContractError(f"course {identity}: open detail control drift")
        return True, str(listed["detail_url"])
    expected_text = (
        "접수대기" if str(listed["status"]) == "SCHEDULED" else "접수마감"
    )
    if (
        _clean(control.get_text(" ", strip=True)) != expected_text
        or _clean(control.get("href"))
        or _clean(control.get("onclick"))
        or _clean(control.get("formaction"))
    ):
        raise JeongeupContractError(f"course {identity}: inactive detail control drift")
    return False, ""


def _fee_amount(value: str) -> Optional[int]:
    text = _clean(value)
    if not text:
        return None
    if text in {"없음", "무료", "무료/무료", "0", "0원"}:
        return 0
    match = re.fullmatch(r"(?:과정당\s*)?(\d[\d,]*)\s*원", text)
    if match:
        return int(match.group(1).replace(",", ""))
    match = re.fullmatch(r"(?:과정당\s*)?(\d[\d,]*)\s*만원", text)
    if match:
        return int(match.group(1).replace(",", "")) * 10_000
    return None


def _parse_detail(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    cutoff: date,
) -> dict[str, Any]:
    identity = str(listed["identity"])
    branch: JeongeupBranch = listed["branch"]
    expected_title = f"교육/강좌 > {branch.name} > 신청하기"
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if page_title != expected_title:
        raise JeongeupContractError(f"course {identity}: detail page title drift")
    title_node = soup.select_one("div.edu_view01 h4")
    if title_node is None or _clean(title_node.get_text(" ", strip=True)) != listed["title"]:
        raise JeongeupContractError(f"course {identity}: list/detail title drift")
    fields = _detail_fields(soup, identity)
    apply_start, apply_end = _parse_period(fields["접수기간"], identity, "application")
    event_start, event_end = _parse_period(fields["교육기간"], identity, "education")
    if (
        apply_start != listed["apply_start"]
        or apply_end != listed["apply_end"]
        or event_start != listed["event_start"]
        or event_end != listed["event_end"]
    ):
        raise JeongeupContractError(f"course {identity}: list/detail period drift")
    venue = fields["교육장"]
    if venue != listed["venue"]:
        raise JeongeupContractError(f"course {identity}: list/detail venue drift")
    capacity_match = _CAPACITY.fullmatch(fields["신청/정원"])
    if not capacity_match:
        raise JeongeupContractError(f"course {identity}: detail capacity shape drift")
    capacity_current, capacity_total = (
        int(token.replace(",", "")) for token in capacity_match.groups()
    )
    if (
        capacity_current != listed["capacity_current"]
        or capacity_total != listed["capacity_total"]
    ):
        raise JeongeupContractError(f"course {identity}: list/detail capacity drift")
    raw_status = fields["접수상태"]
    if raw_status != listed["raw_status"]:
        raise JeongeupContractError(f"course {identity}: list/detail status drift")
    status = str(listed["status"])
    if status == "SCHEDULED" and not cutoff < apply_start:
        raise JeongeupContractError(f"course {identity}: scheduled status/date disagreement")
    if status == "OPEN" and not apply_start <= cutoff <= apply_end:
        raise JeongeupContractError(f"course {identity}: open status/date disagreement")
    if raw_status == "교육중" and not event_start <= cutoff <= event_end:
        raise JeongeupContractError(f"course {identity}: in-progress status/date disagreement")
    if raw_status == "교육종료" and event_end >= cutoff:
        raise JeongeupContractError(f"course {identity}: ended status/date disagreement")

    control_present, application_url = _detail_controls(soup, listed)
    if control_present != bool(listed["list_application_control"]):
        raise JeongeupContractError(f"course {identity}: list/detail control drift")
    schedule = fields["교육시간"]
    target = fields["교육대상"]
    fee = fields["수강료/재료비"]
    if not schedule or not target or not venue:
        raise JeongeupContractError(f"course {identity}: safe detail field missing")
    safe_values = (str(listed["title"]), branch.name, schedule, target, venue, fee)
    if any(
        _PHONE.search(value) or _EMAIL.search(value) or _RESIDENT_ID.search(value)
        for value in safe_values
    ):
        raise JeongeupContractError(f"course {identity}: allowlisted field contains PII")

    event_period = f"{event_start.isoformat()} ~ {event_end.isoformat()}"
    apply_period = f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
    detail_url = str(listed["detail_url"])
    return {
        "provider": JEONGEUP_PROVIDER,
        "provider_course_id": f"{JEONGEUP_PROVIDER}:re:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": branch.name,
        "branch_code": branch.code,
        "branch_url": branch.url,
        "preserve_branch": True,
        "category": "교육/강좌",
        "program_type": "교육",
        "raw_url": detail_url,
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if status == "OPEN" else "INFO_ONLY",
        "application_method": "온라인",
        "application_methods": ["온라인"],
        "reservation_available": control_present,
        "status": status,
        "raw_status": raw_status,
        "fee": fee,
        "fee_amount": _fee_amount(fee),
        "material_fee": "",
        "material_fee_amount": None,
        "period": event_period,
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": apply_period,
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "schedule_raw": schedule,
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_remaining": max(capacity_total - capacity_current, 0),
        "target": target,
        "venue": venue,
        "venue_name": venue,
        "room": venue,
        "facility_name": branch.name,
        "address": "",
        "venue_address": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": JEONGEUP_PARSER,
        "municipality_code": JEONGEUP_MUNICIPALITY_CODE,
        "municipality_full_name": JEONGEUP_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_branch": branch.name,
            "source_branch_menu": branch.list_menu,
            "source_page": int(listed["page"]),
            "source_position": int(listed["position"]),
            "source_status": raw_status,
            "source_apply_period": apply_period,
            "source_education_period": event_period,
            "source_schedule": schedule,
            "source_target": target,
            "source_venue": venue,
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "detail_verified": True,
            "application_control_present": control_present,
            "application_control_verified": True,
            "application_form_submitted": False,
            "application_endpoint_fetched": False,
            "reservation_lookup_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "discarded_detail_fields": list(JEONGEUP_DISCARDED_DETAIL_FIELDS),
            "address_policy": "venue_name_only_no_verified_street_address",
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden PII/free-text key")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields allowlist exceeded")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "branch_url"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload) or _RESIDENT_ID.search(payload):
        errors.append("PII persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail persisted")
    if row.get("address") or row.get("venue_address"):
        errors.append("unverified street address persisted")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
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


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _clean(row.get("title")).casefold(),
        _clean(row.get("branch")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _clean(row.get("venue_name")),
    )


def _initial_meta() -> dict[str, Any]:
    return {
        "municipality_code": JEONGEUP_MUNICIPALITY_CODE,
        "municipality_full_name": JEONGEUP_MUNICIPALITY_NAME,
        "owner_provider": JEONGEUP_PROVIDER,
        "canonical_provider": JEONGEUP_PROVIDER,
        "canonical_candidate_id": JEONGEUP_CANONICAL_CANDIDATE_ID,
        "review_candidate_id": JEONGEUP_REVIEW_SITEMAP_CANDIDATE_ID,
        "canonical_url": JEONGEUP_CANONICAL_URL,
        "canonical_url_sha256": JEONGEUP_CANONICAL_URL_SHA256,
        "candidate_audit": {
            key: dict(value) for key, value in JEONGEUP_CANDIDATE_AUDIT.items()
        },
        "provider_decision": (
            "new owner: no active Jeongeup course incumbent; exclude the redirected "
            "culture sitemap and aggregate the four official education branches"
        ),
        "existing_active_owner_count": 0,
        "owner_boundaries": [dict(item) for item in JEONGEUP_OWNER_BOUNDARIES],
        "ownership_scope": JEONGEUP_OWNERSHIP_SCOPE,
        "parser": JEONGEUP_PARSER,
        "page_size": JEONGEUP_PAGE_SIZE,
        "branch_count": len(JEONGEUP_BRANCHES),
        "branch_urls": {branch.name: branch.url for branch in JEONGEUP_BRANCHES},
        "pagination_boundary_mode": "per_branch_advertised_last_plus_clamped_overflow",
        "recommended_max_pages": JEONGEUP_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": JEONGEUP_RECOMMENDED_DETAIL_LIMIT,
        "recommended_max_workers": JEONGEUP_RECOMMENDED_MAX_WORKERS,
        "recommended_timeout_seconds": 30,
        "fetch_attempts": JEONGEUP_FETCH_ATTEMPTS,
        "max_html_bytes": JEONGEUP_MAX_HTML_BYTES,
        "live_audit_baseline": dict(JEONGEUP_LIVE_AUDIT_BASELINE),
        "address_policy": "detail venue name only; no verified street address",
        "pii_policy": (
            "discard instructor/manager/phone/homepage/content/files/images and "
            "never submit the inline application form or fetch lookup/attachments"
        ),
        "source_requests": 0,
        "request_attempts": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "application_endpoints_called": 0,
        "application_forms_submitted": 0,
        "reservation_lookup_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "advertised_total": 0,
        "advertised_last_pages": {},
        "data_pages": 0,
        "page_counts": {},
        "overflow_pages": {},
        "overflow_clamp_verified": False,
        "page1_rechecked": False,
        "last_pages_rechecked": False,
        "overflow_rechecked": False,
        "boundary_rechecks": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "returned_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "branch_boundaries_complete": False,
        "details_complete": False,
        "privacy_violations": 0,
        "semantic_duplicate_count": 0,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "configured_collection_error": "",
    }


def _fetch_parsed_page(
    session: Any,
    branch: JeongeupBranch,
    page: int,
    timeout: int,
    fetcher: Fetcher,
) -> tuple[dict[str, Any], int]:
    soup, attempts = _fetch_soup(session, _list_url(branch, page), timeout, fetcher)
    return _parse_list_page(soup, branch, page), attempts


def collect_jeongeup_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = JEONGEUP_RECOMMENDED_MAX_PAGES,
    detail_limit: int = JEONGEUP_RECOMMENDED_DETAIL_LIMIT,
    max_workers: int = JEONGEUP_RECOMMENDED_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, stable, privacy-safe Jeongeup education snapshot."""

    meta = _initial_meta()
    if not is_jeongeup_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match exact Jeongeup complete education owner"
        )
        return [], JEONGEUP_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], JEONGEUP_PARSER, meta
        session_factory = _raw_session
    try:
        cutoff = _today(today)
        if any(
            isinstance(value, bool) or int(value) < 1
            for value in (timeout, max_pages, max_workers)
        ):
            raise ValueError("timeout, max_pages, and max_workers must be positive integers")
        if isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("detail_limit must be a non-negative integer")
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], JEONGEUP_PARSER, meta

    current_fetcher = fetcher or _request
    main_session = session_factory()
    try:
        first_pages: dict[str, dict[str, Any]] = {}
        for branch in JEONGEUP_BRANCHES:
            parsed, attempts = _fetch_parsed_page(
                main_session, branch, 1, int(timeout), current_fetcher
            )
            meta["source_requests"] += 1
            meta["list_requests"] += 1
            meta["request_attempts"] += attempts
            if parsed["current_page"] != 1:
                raise JeongeupContractError(
                    f"{branch.name}: canonical first page did not render page one"
                )
            first_pages[branch.list_menu] = parsed

        total_pages = sum(int(page["advertised_last"]) for page in first_pages.values())
        if total_pages > int(max_pages):
            meta["source_cap_reached"] = True
            raise JeongeupContractError(
                f"advertised aggregate pages {total_pages} exceed max_pages {max_pages}"
            )

        pages: dict[tuple[str, int], dict[str, Any]] = {
            (menu, 1): parsed for menu, parsed in first_pages.items()
        }

        def fetch_list_worker(
            branch: JeongeupBranch, page: int
        ) -> tuple[str, int, dict[str, Any], int]:
            worker_session = session_factory()
            try:
                parsed, worker_attempts = _fetch_parsed_page(
                    worker_session, branch, page, int(timeout), current_fetcher
                )
                return branch.list_menu, page, parsed, worker_attempts
            finally:
                close_worker = getattr(worker_session, "close", None)
                if callable(close_worker):
                    close_worker()

        page_jobs = [
            (branch, page)
            for branch in JEONGEUP_BRANCHES
            for page in range(
                2, int(first_pages[branch.list_menu]["advertised_last"]) + 1
            )
        ]
        if page_jobs:
            with ThreadPoolExecutor(max_workers=min(int(max_workers), len(page_jobs))) as executor:
                futures = [executor.submit(fetch_list_worker, *job) for job in page_jobs]
                for future in as_completed(futures):
                    menu, page, parsed, worker_attempts = future.result()
                    pages[(menu, page)] = parsed
                    meta["source_requests"] += 1
                    meta["list_requests"] += 1
                    meta["request_attempts"] += worker_attempts

        ordered_by_branch: dict[str, list[dict[str, Any]]] = {}
        overflow_by_branch: dict[str, dict[str, Any]] = {}
        listed: list[dict[str, Any]] = []
        branch_audit: dict[str, dict[str, Any]] = {}
        for branch in JEONGEUP_BRANCHES:
            first = first_pages[branch.list_menu]
            last = int(first["advertised_last"])
            total = int(first["advertised_total"])
            ordered = [pages[(branch.list_menu, page)] for page in range(1, last + 1)]
            for page_number, parsed in enumerate(ordered, 1):
                if (
                    parsed["current_page"] != page_number
                    or int(parsed["advertised_last"]) != last
                    or int(parsed["advertised_total"]) != total
                ):
                    raise JeongeupContractError(
                        f"{branch.name}: advertised pagination contract drift"
                    )
            for parsed in ordered[:-1]:
                if len(parsed["rows"]) != JEONGEUP_PAGE_SIZE:
                    raise JeongeupContractError(f"{branch.name}: non-final page not full")
            if not 1 <= len(ordered[-1]["rows"]) <= JEONGEUP_PAGE_SIZE:
                raise JeongeupContractError(f"{branch.name}: final page size drift")
            if (last - 1) * JEONGEUP_PAGE_SIZE + len(ordered[-1]["rows"]) != total:
                raise JeongeupContractError(f"{branch.name}: page boundary total drift")

            overflow_page = last + 1
            overflow, attempts = _fetch_parsed_page(
                main_session, branch, overflow_page, int(timeout), current_fetcher
            )
            meta["source_requests"] += 1
            meta["list_requests"] += 1
            meta["request_attempts"] += attempts
            if (
                overflow["current_page"] is not None
                or _page_signature(overflow) != _page_signature(ordered[-1])
            ):
                raise JeongeupContractError(
                    f"{branch.name}: post-boundary page did not clamp exactly"
                )
            ordered_by_branch[branch.list_menu] = ordered
            overflow_by_branch[branch.list_menu] = overflow
            branch_rows = [row for parsed in ordered for row in parsed["rows"]]
            listed.extend(branch_rows)
            branch_audit[branch.name] = {
                "list_menu": branch.list_menu,
                "detail_menu": branch.detail_menu,
                "url": branch.url,
                "advertised_total": total,
                "advertised_last_page": last,
                "page_counts": [len(parsed["rows"]) for parsed in ordered],
                "overflow_page": overflow_page,
                "overflow_current_marker": None,
                "result_open_count": int(first["result_open_count"]),
                "result_closed_count": int(first["result_closed_count"]),
                "source_status_counts": dict(
                    Counter(str(row["raw_status"]) for row in branch_rows)
                ),
            }

        identities = [str(row["identity"]) for row in listed]
        advertised_total = sum(
            int(first_pages[branch.list_menu]["advertised_total"])
            for branch in JEONGEUP_BRANCHES
        )
        if len(listed) != advertised_total or len(identities) != len(set(identities)):
            raise JeongeupContractError("RE identity set is incomplete or duplicated")

        current = [row for row in listed if row["event_end"] >= cutoff]
        if len(current) > int(detail_limit):
            meta["source_cap_reached"] = True
            raise JeongeupContractError(
                f"detail_limit {detail_limit} below required {len(current)}"
            )

        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "advertised_total": advertised_total,
                "advertised_last_pages": {
                    branch.name: int(first_pages[branch.list_menu]["advertised_last"])
                    for branch in JEONGEUP_BRANCHES
                },
                "data_pages": total_pages,
                "page_counts": {
                    branch.name: [
                        len(parsed["rows"])
                        for parsed in ordered_by_branch[branch.list_menu]
                    ]
                    for branch in JEONGEUP_BRANCHES
                },
                "overflow_pages": {
                    branch.name: int(first_pages[branch.list_menu]["advertised_last"]) + 1
                    for branch in JEONGEUP_BRANCHES
                },
                "overflow_clamp_verified": True,
                "branch_audit": branch_audit,
                "source_rows": len(listed),
                "source_total": len(listed),
                "source_status_counts": dict(
                    Counter(str(row["raw_status"]) for row in listed)
                ),
                "source_branch_counts": dict(
                    Counter(row["branch"].name for row in listed)
                ),
                "source_identity_numeric_min": min(
                    int(identity.removeprefix("RE")) for identity in identities
                ),
                "source_identity_numeric_max": max(
                    int(identity.removeprefix("RE")) for identity in identities
                ),
                "current_source_count": len(current),
                "expired_source_count": len(listed) - len(current),
                "current_source_branch_counts": dict(
                    Counter(row["branch"].name for row in current)
                ),
                "current_source_status_counts": dict(
                    Counter(str(row["raw_status"]) for row in current)
                ),
                "pagination_complete": True,
                "branch_boundaries_complete": True,
            }
        )

        def fetch_detail_worker(
            item: Mapping[str, Any],
        ) -> tuple[dict[str, Any], int]:
            worker_session = session_factory()
            try:
                soup, worker_attempts = _fetch_soup(
                    worker_session,
                    str(item["detail_url"]),
                    int(timeout),
                    current_fetcher,
                )
                return _parse_detail(item, soup, cutoff), worker_attempts
            finally:
                close_worker = getattr(worker_session, "close", None)
                if callable(close_worker):
                    close_worker()

        detail_results: list[tuple[dict[str, Any], int]] = []
        if current:
            with ThreadPoolExecutor(max_workers=min(int(max_workers), len(current))) as executor:
                futures = [executor.submit(fetch_detail_worker, item) for item in current]
                for future in as_completed(futures):
                    row, worker_attempts = future.result()
                    detail_results.append((row, worker_attempts))
                    meta["source_requests"] += 1
                    meta["detail_pages"] += 1
                    meta["request_attempts"] += worker_attempts
        rows = [row for row, _ in detail_results]

        for branch in JEONGEUP_BRANCHES:
            ordered = ordered_by_branch[branch.list_menu]
            last = int(ordered[0]["advertised_last"])
            first_recheck, attempts = _fetch_parsed_page(
                main_session, branch, 1, int(timeout), current_fetcher
            )
            meta["source_requests"] += 1
            meta["list_requests"] += 1
            meta["request_attempts"] += attempts
            meta["boundary_rechecks"] += 1
            if _page_signature(first_recheck) != _page_signature(ordered[0]):
                raise JeongeupContractError(f"{branch.name}: page-one stability failed")

            last_recheck, attempts = _fetch_parsed_page(
                main_session, branch, last, int(timeout), current_fetcher
            )
            meta["source_requests"] += 1
            meta["list_requests"] += 1
            meta["request_attempts"] += attempts
            meta["boundary_rechecks"] += 1
            if _page_signature(last_recheck) != _page_signature(ordered[-1]):
                raise JeongeupContractError(f"{branch.name}: last-page stability failed")

            overflow_recheck, attempts = _fetch_parsed_page(
                main_session, branch, last + 1, int(timeout), current_fetcher
            )
            meta["source_requests"] += 1
            meta["list_requests"] += 1
            meta["request_attempts"] += attempts
            meta["boundary_rechecks"] += 1
            if _page_signature(overflow_recheck) != _page_signature(
                overflow_by_branch[branch.list_menu]
            ):
                raise JeongeupContractError(f"{branch.name}: overflow stability failed")

        meta["page1_rechecked"] = True
        meta["last_pages_rechecked"] = True
        meta["overflow_rechecked"] = True

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _dedupe)(rows))
        expected_ids = {
            f"{JEONGEUP_PROVIDER}:re:{item['identity']}" for item in current
        }
        if len(rows) != len(current) or {
            str(row.get("provider_course_id")) for row in rows
        } != expected_ids:
            raise JeongeupContractError("dedupe changed the current RE identity set")
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        meta["privacy_violations"] = len(privacy_errors)
        if privacy_errors:
            raise JeongeupContractError("; ".join(privacy_errors[:5]))
        semantic_counts = Counter(_semantic_key(row) for row in rows)
        semantic_duplicates = sum(
            count - 1 for count in semantic_counts.values() if count > 1
        )
        meta["semantic_duplicate_count"] = semantic_duplicates
        if semantic_duplicates:
            raise JeongeupContractError("semantic duplicate current courses detected")

        meta.update(
            {
                "returned_count": len(rows),
                "details_complete": meta["detail_pages"] == len(current),
                "status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "raw_status_counts": dict(
                    Counter(str(row["raw_status"]) for row in rows)
                ),
                "branch_counts": dict(Counter(str(row["branch"]) for row in rows)),
                "category_counts": dict(Counter(str(row["category"]) for row in rows)),
                "venue_counts": dict(Counter(str(row["venue_name"]) for row in rows)),
                "application_control_count": sum(
                    bool(row["raw_fields"]["application_control_present"])
                    for row in rows
                ),
                "actionable_application_count": sum(
                    bool(row["application_url"]) for row in rows
                ),
                "no_current_data": not rows,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return rows, JEONGEUP_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], JEONGEUP_PARSER, meta
    finally:
        close = getattr(main_session, "close", None)
        if callable(close):
            close()


collect = collect_jeongeup_education


__all__ = [
    "JEONGEUP_BRANCHES",
    "JEONGEUP_CANONICAL_CANDIDATE_ID",
    "JEONGEUP_CANONICAL_URL",
    "JEONGEUP_CANONICAL_URL_SHA256",
    "JEONGEUP_CANDIDATE_AUDIT",
    "JEONGEUP_LIVE_AUDIT_BASELINE",
    "JEONGEUP_MUNICIPALITY_CODE",
    "JEONGEUP_OWNER_BOUNDARIES",
    "JEONGEUP_PARSER",
    "JEONGEUP_PROVIDER",
    "JeongeupContractError",
    "collect",
    "collect_jeongeup_education",
    "is_jeongeup_education_target",
    "is_target",
]
