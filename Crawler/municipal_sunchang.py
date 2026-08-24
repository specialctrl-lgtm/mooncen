"""Fail-closed collector for Sunchang-gun's lifelong-education ledger.

The discovery candidate for Sunchang points at ``/chief`` (the mayor's
office), not at a course catalogue.  Sunchang County's official home page
links to the separately hosted Sunchang Lifelong Learning Center.  Its
``002009000000`` board is the complete municipal lifelong-education owner.

The canonical unfiltered board is authoritative.  It currently contains two
populated audience categories and two empty categories; the category pager
incorrectly drops its own filter, so this collector builds category URLs
directly and reconciles their identity union with the unfiltered ledger.
Every non-empty page and the immediate empty ``자료가 없습니다`` sentinel are
read.  The first, last, sentinel, and all pages holding current/future rows are
then re-read before an atomic snapshot is published.

Only current/future details are requested.  Detail pages expose a login-bound
POST application contract.  The collector verifies the identity binding but
never calls that endpoint, login/join pages, applicant lookups, attachments,
or PII-bearing forms.  Free-text bodies, embedded images, instructor data,
contacts, and attachments are never retained.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


SUNCHANG_PROVIDER = "MUNI_WWW_SCEDULIFE_CO_KR_C6522638"
SUNCHANG_BAD_CANDIDATE_ID = "MUNI_IR_E5BA14D4974B"
SUNCHANG_CANONICAL_CANDIDATE_ID = "MUNI_IR_9B5BDF51ED91"
SUNCHANG_MUNICIPALITY_CODE = "5277000000"
SUNCHANG_MUNICIPALITY_NAME = "전북특별자치도 순창군"

SUNCHANG_HOST = "www.scedulife.co.kr"
SUNCHANG_LIST_PATH = "/scedu/board/list"
SUNCHANG_DETAIL_PATH = "/scedu/board/view"
SUNCHANG_APPLICATION_WRITE_PATH = "/api/scedu/reserve/eduApply"
SUNCHANG_MENU_ID = "002009000000"
SUNCHANG_PAGE_SIZE = 12
SUNCHANG_RECOMMENDED_MAX_PAGES = 30
SUNCHANG_RECOMMENDED_DETAIL_LIMIT = 100
SUNCHANG_MAX_HTML_BYTES = 3_000_000

SUNCHANG_BAD_CANDIDATE_URL = "https://www.sunchang.go.kr/chief"
SUNCHANG_CANONICAL_URL = (
    "https://www.scedulife.co.kr/scedu/board/list?"
    "menuId=002009000000&page=1&category="
)
SUNCHANG_OFFICIAL_HOME_EVIDENCE_URL = "https://www.sunchang.go.kr/"
SUNCHANG_BRANCH_URL = (
    "https://www.scedulife.co.kr/scedu/subContent?contentSeq=87"
)
SUNCHANG_BRANCH = "순창평생학습관"
SUNCHANG_BRANCH_ADDRESS = (
    "전북특별자치도 순창군 순창읍 민속마을길 6-5"
)

SUNCHANG_BAD_NORMALIZED_SHA1 = (
    "a1f0661d1595880c85582d2bbc4775fb9bc4fe02"
)
SUNCHANG_BAD_NORMALIZED_SHA256 = (
    "e5ba14d4974b7c481e2ad11126133c494ea84f486fbac0ea1a8ecfe00bb5fa26"
)
SUNCHANG_CANONICAL_NORMALIZED_SHA1 = (
    "c65226381a865bca60d540062e6f98733a3b2356"
)
SUNCHANG_CANONICAL_NORMALIZED_SHA256 = (
    "9b5bdf51ed917e76201dfcda8475d149c1309e07d639a5f28335d219cab9dc8d"
)

SUNCHANG_PARSER = (
    "sunchang_scedu_002009_complete_unfiltered_html+direct_audience_"
    "category_reconciliation+immediate_empty_sentinel+descending_"
    "article_identity+current_future_detail_only+identity_bound_login_"
    "post_contract_no_write_fetch+stable_current_boundaries+pii_allowlist"
)

SUNCHANG_CATEGORIES: Mapping[str, str] = {
    "1": "유·초 학교대상",
    "2": "학생 대상",
    "3": "성인 대상",
    "4": "누구나",
}
SUNCHANG_CATEGORY_PAGE_TITLES: Mapping[str, str] = {
    "": "교육프로그램신청",
    "1": "유·초 학교대상 교육신청",
    "2": "학생 대상 교육신청",
    "3": "성인 대상 교육신청",
    "4": "누구나 교육신청",
}
SUNCHANG_DETAIL_CATEGORY_LABELS: Mapping[str, str] = {
    "1": "유·초학교대상",
    "2": "학생대상",
    "3": "성인대상",
    "4": "누구나",
}
SUNCHANG_STATUS_FILTERS = (
    "전체",
    "접수중",
    "교육중",
    "접수예정",
    "접수마감",
    "교육마감",
)
SUNCHANG_SOURCE_STATUS: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "교육중": "CLOSED",
    "접수마감": "CLOSED",
    "교육마감": "CLOSED",
}

SUNCHANG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    SUNCHANG_BAD_CANDIDATE_ID: {
        "decision": "reject_wrong_mayor_site",
        "provider": "MUNI_WWW_SUNCHANG_GO_KR_A1F0661D",
        "url": SUNCHANG_BAD_CANDIDATE_URL,
        "owner": "",
        "reason": "official_mayor_landing_without_course_identity_rows",
    },
    SUNCHANG_CANONICAL_CANDIDATE_ID: {
        "decision": "create_new_official_lifelong_education_owner",
        "provider": SUNCHANG_PROVIDER,
        "url": SUNCHANG_CANONICAL_URL,
        "owner": SUNCHANG_PROVIDER,
        "reason": "county_linked_complete_lifelong_course_identity_ledger",
    },
}

SUNCHANG_NON_EXECUTING_ALIASES: tuple[Mapping[str, Any], ...] = (
    {
        "url": "https://www.scedulife.co.kr/",
        "reason": "redirecting_home_embeds_same_131_identities_without_pagination_contract",
        "owner": SUNCHANG_PROVIDER,
    },
    {
        "url": (
            "https://www.scedulife.co.kr/scedu/board/list?"
            "menuId=002009000000&page=1&articleCategory="
        ),
        "reason": "legacy_empty_articleCategory_alias_of_canonical_board",
        "owner": SUNCHANG_PROVIDER,
    },
    {
        "url_pattern": SUNCHANG_CANONICAL_URL + "&tab=<state>",
        "reason": "state_filtered_subset_of_canonical_ledger",
        "owner": SUNCHANG_PROVIDER,
    },
    {
        "url_pattern": SUNCHANG_CANONICAL_URL + "<1-4>",
        "reason": "audience_subset_whose_pager_drops_filter; reconciled_not_scheduled",
        "owner": SUNCHANG_PROVIDER,
    },
    {
        "url": (
            "https://www.scedulife.co.kr/scedu/board/list?"
            "menuId=002009000000&page=1&category=5"
        ),
        "reason": "education_information_page_without_course_identities",
        "owner": SUNCHANG_PROVIDER,
    },
    {
        "url": (
            "https://www.scedulife.co.kr/scedu/board/list?"
            "menuId=001003000000&page=1&articleCategory="
        ),
        "reason": "notice_board_not_course_identity_ledger",
        "owner": SUNCHANG_PROVIDER,
    },
    {
        "url": (
            "https://www.scedulife.co.kr/scedu/board/list?"
            "menuId=002007000000&page=1&articleCategory="
        ),
        "reason": "facility_rental_ledger_not_education",
        "owner": SUNCHANG_PROVIDER,
    },
)

SUNCHANG_SEPARATE_OWNER_BOUNDARIES: tuple[Mapping[str, Any], ...] = (
    {
        "provider": "CULTURE_CULTURE_FOUNDATION_3A09706846",
        "name": "순창발효관광재단 / 순창발효테마파크",
        "url": (
            "https://yeyak.sunchang.go.kr/themePark.webMber?"
            "insttCode=A000000001&detailClCode=04&tabIndex=1"
        ),
        "reason": "separate_integrated_reservation_experience_owner_not_lifelong_courses",
    },
    {
        "provider": "CULTURE_PUBLIC_LIBRARY_1B6144A9DB",
        "name": "순창군립도서관",
        "url": "https://lib.sunchang.go.kr",
        "reason": "separate_public_library_program_owner",
    },
    {
        "provider": "CULTURE_PUBLIC_LIBRARY_8700CFFC50",
        "name": "전북특별자치도교육청순창도서관",
        "url": "https://lib.jbe.go.kr/sclib",
        "reason": "separate_education_office_library_program_owner",
    },
    {
        "provider": "",
        "name": "순창군 가족센터 군민사회교육",
        "url": (
            "https://www.sunchang.go.kr/board/post/view.do?"
            "boardUid=ff8080819a2f0e3b019a6c0c17ba1fd9&"
            "menuUid=ff8080819a2f0e3b019a5d1b0c40164a&"
            "postUid=4028a6f09f4b44cb019f6998d965254d"
        ),
        "reason": "separate_offline_family_center_campaign_notice_not_reservation_ledger",
    },
)

SUNCHANG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-23",
    "official_home_evidence_url": SUNCHANG_OFFICIAL_HOME_EVIDENCE_URL,
    "canonical_url": SUNCHANG_CANONICAL_URL,
    "historical_rows": 131,
    "canonical_pages": 11,
    "empty_sentinel_page": 12,
    "globally_unique_article_seqs": 131,
    "first_article_seq": "1017",
    "last_article_seq": "415",
    "category_counts": {"1": 0, "2": 0, "3": 47, "4": 84},
    "category_pages": {"1": 0, "2": 0, "3": 4, "4": 7},
    "source_status_counts": {"교육마감": 131},
    "current_or_future_rows": 0,
    "home_embedded_duplicate_identities": 131,
    "historical_details_sampled": ("1017", "996"),
    "application_contract": "login_bound_POST_/api/scedu/reserve/eduApply",
    "application_endpoint_called": False,
    "official_branch": SUNCHANG_BRANCH,
    "official_branch_address": SUNCHANG_BRANCH_ADDRESS,
}

SUNCHANG_RECOMMENDED_OVERRIDE: Mapping[str, Any] = {
    "code": SUNCHANG_MUNICIPALITY_CODE,
    "full_name": SUNCHANG_MUNICIPALITY_NAME,
    "provider": SUNCHANG_PROVIDER,
    "provider_decision": "reject_chief_candidate_and_create_new_lifelong_owner",
    "candidates": (
        {
            "status": "candidate",
            "score": 100,
            "candidate_id": SUNCHANG_CANONICAL_CANDIDATE_ID,
            "title": "순창평생학습관 교육프로그램신청",
            "url": SUNCHANG_CANONICAL_URL,
            "evidence_urls": (
                SUNCHANG_OFFICIAL_HOME_EVIDENCE_URL,
                SUNCHANG_CANONICAL_URL,
            ),
        },
        {
            "status": "excluded",
            "candidate_id": SUNCHANG_BAD_CANDIDATE_ID,
            "url": SUNCHANG_BAD_CANDIDATE_URL,
            "reason": "wrong_mayor_site",
        },
    ),
}

SUNCHANG_RECOMMENDED_TARGET: Mapping[str, Any] = {
    "provider": SUNCHANG_PROVIDER,
    "url": SUNCHANG_CANONICAL_URL,
    "crawler_module": "Crawler.municipal_sunchang",
    "crawler_callable": "collect",
    "municipality_code": SUNCHANG_MUNICIPALITY_CODE,
    "collection_category": "공공예약",
    "domain_category": "교육·강좌",
    "source_group": "municipal_reservation",
    "operator_type": "지자체/공공기관",
    "max_pages": SUNCHANG_RECOMMENDED_MAX_PAGES,
    "detail_limit": SUNCHANG_RECOMMENDED_DETAIL_LIMIT,
}


class SunchangContractError(RuntimeError):
    """Raised when the audited official source no longer matches its contract."""


@dataclass(frozen=True)
class _ListedCourse:
    identity: str
    title: str
    category_code: str
    category_name: str
    source_status: str
    detail_url: str
    apply_period: str
    schedule: str
    period: str
    venue: str
    apply_start: date
    apply_end: date
    event_start: date
    event_end: date
    applied_count: int
    capacity_total: int
    page: int


@dataclass(frozen=True)
class _ListPage:
    requested_page: int
    category_filter: str
    rows: tuple[_ListedCourse, ...]
    empty: bool
    pager_numbers: tuple[int, ...]
    active_page: Optional[int]
    has_next_group: bool


Getter = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_PERIOD_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})$"
)
_CAPACITY_RE = re.compile(r"^(\d+)\s*명$")
_DETAIL_CAPACITY_RE = re.compile(
    r"^신청\s*(\d+)명\s*/\s*정원\s*(\d+)명$"
)
_WAIT_CAPACITY_RE = re.compile(
    r"^대기\s*(\d+)명\s*/\s*대기정원\s*(\d+)명$"
)
_PHONE_RE = re.compile(
    r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_APPLY_ID_RE = re.compile(
    r"var\s+articleSeq\s*=\s*[\"']([1-9]\d*)[\"']\s*;"
)
_APPLY_URL_RE = re.compile(
    r"url\s*:\s*[\"'](/api/scedu/reserve/eduApply)[\"']"
)
_APPLY_METHOD_RE = re.compile(r"type\s*:\s*[\"']POST[\"']", re.IGNORECASE)
_APPLY_DATA_RE = re.compile(
    r"data\s*:\s*\{\s*articleSeq\s*:\s*articleSeq\s*\}"
)

_LIST_FIELDS = ("모집기간", "운영시간", "운영기간", "교육장소")
_DETAIL_REQUIRED_FIELDS = (
    "운영기간",
    "운영시간",
    "교육장소",
    "교육대상",
    "모집기간",
    "예약인원",
    "대기인원",
)
_ACTIVE_SUBMENU = ("교육프로그램신청", "교육안내", "시설예약")


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def is_sunchang_education_target(target: Any) -> bool:
    """Return true only for the new owner and exact canonical first page."""

    parsed = urlparse(_target_url(target))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        _provider(target) == SUNCHANG_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SUNCHANG_HOST
        and parsed.port is None
        and not parsed.username
        and not parsed.password
        and not parsed.params
        and not parsed.fragment
        and parsed.path == SUNCHANG_LIST_PATH
        and set(query) == {"menuId", "page", "category"}
        and query["menuId"] == [SUNCHANG_MENU_ID]
        and query["page"] == ["1"]
        and query["category"] == [""]
    )


is_target = is_sunchang_education_target


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
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _default_getter(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=True)


def _close_quietly(session: Any) -> None:
    try:
        session.close()
    except Exception:
        pass


def _coerce_soup(result: Any, requested_url: str) -> BeautifulSoup:
    final_url = requested_url
    if isinstance(result, BeautifulSoup):
        soup = result
    elif isinstance(result, str):
        if len(result.encode("utf-8")) > SUNCHANG_MAX_HTML_BYTES:
            raise SunchangContractError(f"HTML exceeds byte cap for {requested_url}")
        soup = BeautifulSoup(result, "html.parser")
    elif isinstance(result, bytes):
        if len(result) > SUNCHANG_MAX_HTML_BYTES:
            raise SunchangContractError(f"HTML exceeds byte cap for {requested_url}")
        soup = BeautifulSoup(result, "html.parser")
    else:
        status = int(getattr(result, "status_code", 200))
        if status != 200:
            raise SunchangContractError(f"HTTP {status} for {requested_url}")
        final_url = _clean(getattr(result, "url", requested_url)) or requested_url
        content = getattr(result, "content", None)
        if isinstance(content, bytes):
            if len(content) > SUNCHANG_MAX_HTML_BYTES:
                raise SunchangContractError(
                    f"HTML exceeds byte cap for {requested_url}"
                )
            soup = BeautifulSoup(content, "html.parser")
        else:
            text = str(getattr(result, "text", ""))
            if len(text.encode("utf-8")) > SUNCHANG_MAX_HTML_BYTES:
                raise SunchangContractError(
                    f"HTML exceeds byte cap for {requested_url}"
                )
            soup = BeautifulSoup(text, "html.parser")

    parsed = urlparse(final_url)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != SUNCHANG_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
    ):
        raise SunchangContractError(f"unexpected redirect target: {final_url}")
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "순창평생학습관":
        raise SunchangContractError(
            f"unexpected or missing page title for {requested_url}: {title}"
        )
    return soup


def _get_soup(
    session: Any,
    getter: Getter,
    url: str,
    timeout: int,
) -> BeautifulSoup:
    return _coerce_soup(getter(session, url, timeout), url)


def _list_url(page: int, category: str = "") -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise SunchangContractError("page must be a positive integer")
    if category not in {"", *SUNCHANG_CATEGORIES}:
        raise SunchangContractError(f"unknown category filter: {category}")
    return (
        f"https://{SUNCHANG_HOST}{SUNCHANG_LIST_PATH}?"
        + urlencode(
            {
                "menuId": SUNCHANG_MENU_ID,
                "page": str(page),
                "category": category,
            }
        )
    )


def _parse_period(value: str, *, label: str) -> tuple[date, date]:
    match = _PERIOD_RE.fullmatch(_clean(value))
    if not match:
        raise SunchangContractError(f"invalid {label}: {value}")
    try:
        start = date(int(match[1]), int(match[2]), int(match[3]))
        end = date(int(match[4]), int(match[5]), int(match[6]))
    except ValueError as exc:
        raise SunchangContractError(f"invalid {label}: {value}") from exc
    if end < start:
        raise SunchangContractError(f"reversed {label}: {value}")
    return start, end


def _field_pairs(items: Iterable[Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for item in items:
        label_node = item.select_one("strong, b")
        if label_node is None:
            raise SunchangContractError("field row is missing its label")
        label = _clean(label_node.get_text(" ", strip=True))
        full = _clean(item.get_text(" ", strip=True))
        if not full.startswith(label):
            raise SunchangContractError(f"field label is not a prefix: {label}")
        value = _clean(full[len(label) :])
        if not label or not value or label in output:
            raise SunchangContractError(f"invalid or duplicate field: {label}")
        output[label] = value
    return output


def _parse_detail_href(href: str, requested_page: int) -> tuple[str, str, str]:
    absolute = urljoin(f"https://{SUNCHANG_HOST}", _clean(href))
    parsed = urlparse(absolute)
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected_keys = {
        "menuId",
        "page",
        "category",
        "searchMode",
        "searchTxt",
        "articleSeq",
    }
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != SUNCHANG_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != SUNCHANG_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != expected_keys
        or query.get("menuId") != [SUNCHANG_MENU_ID]
        or query.get("page") != [str(requested_page)]
        or query.get("searchMode") != [""]
        or query.get("searchTxt") != [""]
    ):
        raise SunchangContractError(f"unsafe or malformed detail link: {href}")
    category = query.get("category", [""])[0]
    identity = query.get("articleSeq", [""])[0]
    if category not in SUNCHANG_CATEGORIES or not _IDENTITY_RE.fullmatch(identity):
        raise SunchangContractError(f"invalid detail identity/category: {href}")
    return absolute, category, identity


def _parse_list_row(anchor: Any, requested_page: int) -> _ListedCourse:
    detail_url, category_code, identity = _parse_detail_href(
        str(anchor.get("href") or ""), requested_page
    )
    title_node = anchor.select_one(".edu_tit > strong")
    status_node = anchor.select_one(".edu_tit > span")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    source_status = _clean(
        status_node.get_text(" ", strip=True) if status_node else ""
    )
    if not title:
        raise SunchangContractError(f"article {identity}: missing title")
    if source_status not in SUNCHANG_SOURCE_STATUS:
        raise SunchangContractError(
            f"article {identity}: unknown source status {source_status}"
        )

    fields = _field_pairs(anchor.select(".edu_list dl > dt li"))
    if tuple(fields) != _LIST_FIELDS:
        raise SunchangContractError(
            f"article {identity}: list fields changed: {tuple(fields)}"
        )
    apply_start, apply_end = _parse_period(
        fields["모집기간"], label="application period"
    )
    event_start, event_end = _parse_period(
        fields["운영기간"], label="education period"
    )

    capacity_nodes = anchor.select(".edu_list dl > dd b")
    if len(capacity_nodes) != 2:
        raise SunchangContractError(
            f"article {identity}: expected applicant/capacity pair"
        )
    capacity_values: list[int] = []
    for node in capacity_nodes:
        value = _clean(node.get_text(" ", strip=True))
        match = _CAPACITY_RE.fullmatch(value)
        if not match:
            raise SunchangContractError(
                f"article {identity}: invalid capacity value {value}"
            )
        capacity_values.append(int(match[1]))
    applied_count, capacity_total = capacity_values
    if capacity_total < 1 or applied_count > capacity_total:
        raise SunchangContractError(
            f"article {identity}: impossible applicant/capacity pair"
        )

    return _ListedCourse(
        identity=identity,
        title=title,
        category_code=category_code,
        category_name=SUNCHANG_CATEGORIES[category_code],
        source_status=source_status,
        detail_url=detail_url,
        apply_period=fields["모집기간"],
        schedule=fields["운영시간"],
        period=fields["운영기간"],
        venue=fields["교육장소"],
        apply_start=apply_start,
        apply_end=apply_end,
        event_start=event_start,
        event_end=event_end,
        applied_count=applied_count,
        capacity_total=capacity_total,
        page=requested_page,
    )


def _parse_list_page(
    soup: BeautifulSoup,
    category: str,
    requested_page: int,
) -> _ListPage:
    if category not in {"", *SUNCHANG_CATEGORIES}:
        raise SunchangContractError(f"unknown category filter: {category}")

    heading = soup.select_one(".subTitle .titSubject")
    if _clean(heading.get_text(" ", strip=True) if heading else "") != (
        SUNCHANG_CATEGORY_PAGE_TITLES[category]
    ):
        raise SunchangContractError(
            f"category {category or 'all'} page {requested_page}: wrong heading"
        )
    submenu = tuple(
        _clean(node.get_text(" ", strip=True))
        for node in soup.select(".snb .category > ul > li > a")
    )
    if submenu != _ACTIVE_SUBMENU:
        raise SunchangContractError(f"active reservation submenu changed: {submenu}")
    filters = tuple(
        _clean(node.get_text(" ", strip=True))
        for node in soup.select(".inner > ul.cate > li > a > span")
    )
    if filters != SUNCHANG_STATUS_FILTERS:
        raise SunchangContractError(f"status filter vocabulary changed: {filters}")

    form = soup.find("form", attrs={"name": "schForm"})
    if form is None:
        raise SunchangContractError("education search form is missing")
    menu_input = form.select_one('input[name="menuId"]')
    page_input = form.select_one('input[name="page"]')
    search_mode = form.select_one('select[name="searchMode"]')
    search_text = form.select_one('input[name="searchTxt"]')
    options = tuple(
        (
            _clean(option.get("value")),
            _clean(option.get_text(" ", strip=True)),
        )
        for option in (search_mode.select("option") if search_mode else ())
    )
    if (
        menu_input is None
        or _clean(menu_input.get("value")) != SUNCHANG_MENU_ID
        or page_input is None
        or _clean(page_input.get("value")) != str(requested_page)
        or search_text is None
        or options != (("subject", "교육명"), ("tmpCol12", "강사명"))
    ):
        raise SunchangContractError("education search form contract changed")

    container = soup.select_one(".program_list")
    if container is None:
        raise SunchangContractError("education program container is missing")
    anchors = container.select('a[href*="articleSeq="]')
    rows = tuple(_parse_list_row(anchor, requested_page) for anchor in anchors)
    identities = [row.identity for row in rows]
    if len(identities) != len(set(identities)):
        raise SunchangContractError(
            f"category {category or 'all'} page {requested_page}: duplicate identity"
        )
    if category and any(row.category_code != category for row in rows):
        raise SunchangContractError(
            f"category {category} page {requested_page}: foreign category row"
        )

    container_text = _clean(container.get_text(" ", strip=True))
    empty = not rows
    if empty and container_text != "자료가 없습니다":
        raise SunchangContractError(
            f"category {category or 'all'} page {requested_page}: "
            "missing exact empty sentinel"
        )
    if rows and "자료가 없습니다" in container_text:
        raise SunchangContractError("rows and empty sentinel appeared together")

    pager = soup.select_one(".board_pager")
    if pager is None:
        raise SunchangContractError("board pager is missing")
    pager_numbers = tuple(
        int(value)
        for value in (
            _clean(node.get_text(" ", strip=True))
            for node in pager.select("a")
        )
        if value.isdigit()
    )
    active_nodes = pager.select("a.active")
    if len(active_nodes) > 1:
        raise SunchangContractError("multiple active pager entries")
    active_page: Optional[int] = None
    if active_nodes:
        value = _clean(active_nodes[0].get_text(" ", strip=True))
        if not value.isdigit():
            raise SunchangContractError("non-numeric active pager entry")
        active_page = int(value)
    if rows and active_page != requested_page:
        raise SunchangContractError(
            f"page {requested_page}: active pager says {active_page}"
        )
    if empty and active_page is not None:
        raise SunchangContractError("empty sentinel unexpectedly has an active page")

    return _ListPage(
        requested_page=requested_page,
        category_filter=category,
        rows=rows,
        empty=empty,
        pager_numbers=pager_numbers,
        active_page=active_page,
        has_next_group=bool(pager.select_one("a.next")),
    )


def _row_core_signature(row: _ListedCourse) -> tuple[Any, ...]:
    return (
        row.identity,
        row.title,
        row.category_code,
        row.source_status,
        row.apply_period,
        row.schedule,
        row.period,
        row.venue,
        row.applied_count,
        row.capacity_total,
    )


def _page_signature(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.requested_page,
        page.category_filter,
        tuple(_row_core_signature(row) for row in page.rows),
        page.empty,
        page.pager_numbers,
        page.active_page,
        page.has_next_group,
    )


def _walk_ledger(
    get_page: Callable[[str, int], _ListPage],
    category: str,
    max_pages: int,
) -> tuple[list[_ListedCourse], dict[int, _ListPage], int, int]:
    pages: dict[int, _ListPage] = {}
    rows: list[_ListedCourse] = []
    sentinel_page = 0
    for page_number in range(1, max_pages + 1):
        parsed = get_page(category, page_number)
        pages[page_number] = parsed
        if parsed.empty:
            sentinel_page = page_number
            break
        rows.extend(parsed.rows)
    if sentinel_page == 0:
        raise SunchangContractError(
            f"category {category or 'all'}: max_pages reached before empty sentinel"
        )

    last_page = sentinel_page - 1
    if last_page == 0:
        if pages[sentinel_page].pager_numbers:
            raise SunchangContractError(
                f"category {category}: empty ledger advertises numeric pages"
            )
    else:
        for page_number in range(1, last_page):
            if len(pages[page_number].rows) != SUNCHANG_PAGE_SIZE:
                raise SunchangContractError(
                    f"category {category or 'all'} page {page_number}: "
                    "non-final page is not full"
                )
        final_count = len(pages[last_page].rows)
        if not 1 <= final_count <= SUNCHANG_PAGE_SIZE:
            raise SunchangContractError(
                f"category {category or 'all'}: invalid final-page row count"
            )
        if pages[last_page].has_next_group:
            raise SunchangContractError(
                f"category {category or 'all'}: final page still advertises next group"
            )
        sentinel_numbers = pages[sentinel_page].pager_numbers
        if not sentinel_numbers or max(sentinel_numbers) != last_page:
            raise SunchangContractError(
                f"category {category or 'all'}: sentinel does not advertise exact last page"
            )

    identities = [int(row.identity) for row in rows]
    if identities != sorted(identities, reverse=True):
        raise SunchangContractError(
            f"category {category or 'all'}: article identities are not descending"
        )
    if len(identities) != len(set(identities)):
        raise SunchangContractError(
            f"category {category or 'all'}: repeated article identity"
        )
    return rows, pages, last_page, sentinel_page


def _normalized_category_label(value: str) -> str:
    return _clean(value).replace(" ", "")


def _detail_fields(item: Any) -> dict[str, str]:
    fields = _field_pairs(item.select(":scope > ul > li"))
    if tuple(fields) != _DETAIL_REQUIRED_FIELDS:
        raise SunchangContractError(
            f"detail field vocabulary changed: {tuple(fields)}"
        )
    return fields


def _parse_detail(
    listed: _ListedCourse,
    soup: BeautifulSoup,
) -> dict[str, Any]:
    item = soup.select_one(".board_view .basic .item")
    if item is None:
        raise SunchangContractError(f"article {listed.identity}: detail item missing")
    title_node = item.select_one(".title > strong")
    source_status_node = item.select_one(".title .state span[data-label]")
    category_node = item.select_one(".title .state span.cate")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    source_status = _clean(
        source_status_node.get("data-label") if source_status_node else ""
    )
    category_label = _normalized_category_label(
        category_node.get_text(" ", strip=True) if category_node else ""
    )
    if title != listed.title:
        raise SunchangContractError(
            f"article {listed.identity}: list/detail title mismatch"
        )
    if source_status != listed.source_status:
        raise SunchangContractError(
            f"article {listed.identity}: list/detail status mismatch"
        )
    if category_label != SUNCHANG_DETAIL_CATEGORY_LABELS[listed.category_code]:
        raise SunchangContractError(
            f"article {listed.identity}: list/detail category mismatch"
        )

    fields = _detail_fields(item)
    comparisons = {
        "운영기간": listed.period,
        "운영시간": listed.schedule,
        "교육장소": listed.venue,
        "모집기간": listed.apply_period,
    }
    for label, expected in comparisons.items():
        if fields[label] != expected:
            raise SunchangContractError(
                f"article {listed.identity}: list/detail {label} mismatch"
            )
    if _normalized_category_label(fields["교육대상"]) != category_label:
        raise SunchangContractError(
            f"article {listed.identity}: detail target/category mismatch"
        )

    capacity_match = _DETAIL_CAPACITY_RE.fullmatch(fields["예약인원"])
    waiting_match = _WAIT_CAPACITY_RE.fullmatch(fields["대기인원"])
    if not capacity_match or not waiting_match:
        raise SunchangContractError(
            f"article {listed.identity}: detail capacity contract changed"
        )
    detail_applied = int(capacity_match[1])
    detail_capacity = int(capacity_match[2])
    wait_count = int(waiting_match[1])
    wait_capacity = int(waiting_match[2])
    if (
        (detail_applied, detail_capacity)
        != (listed.applied_count, listed.capacity_total)
        or wait_count > wait_capacity
    ):
        raise SunchangContractError(
            f"article {listed.identity}: inconsistent capacity values"
        )

    form_identity = soup.select_one('form#frm input[name="articleSeq"]')
    if (
        form_identity is None
        or _clean(form_identity.get("value")) != listed.identity
    ):
        raise SunchangContractError(
            f"article {listed.identity}: detail form identity mismatch"
        )
    scripts = "\n".join(node.get_text("\n") for node in soup.select("script"))
    apply_ids = _APPLY_ID_RE.findall(scripts)
    apply_urls = _APPLY_URL_RE.findall(scripts)
    contract_verified = bool(
        apply_ids == [listed.identity]
        and apply_urls == [SUNCHANG_APPLICATION_WRITE_PATH]
        and _APPLY_METHOD_RE.search(scripts)
        and _APPLY_DATA_RE.search(scripts)
        and re.search(r"function\s+fn_apply\s*\(\s*\)", scripts)
    )
    if not contract_verified:
        raise SunchangContractError(
            f"article {listed.identity}: application script contract changed"
        )

    controls = [
        node
        for node in item.select(".board_btns a, .board_btns button")
        if re.fullmatch(
            r"(?:javascript:)?\s*fn_apply\s*\(\s*\)\s*;?",
            _clean(node.get("onclick") or node.get("href")),
            re.IGNORECASE,
        )
    ]
    if len(controls) > 1:
        raise SunchangContractError(
            f"article {listed.identity}: multiple application controls"
        )
    control_present = bool(controls)
    if control_present and SUNCHANG_SOURCE_STATUS[listed.source_status] != "OPEN":
        raise SunchangContractError(
            f"article {listed.identity}: application control on non-open state"
        )

    return {
        "target": fields["교육대상"],
        "wait_count": wait_count,
        "wait_capacity": wait_capacity,
        "application_contract_verified": True,
        "application_control_present": control_present,
    }


def _normalized_status(listed: _ListedCourse, cutoff: date) -> str:
    status = SUNCHANG_SOURCE_STATUS[listed.source_status]
    if listed.source_status == "교육마감" and listed.event_end >= cutoff:
        raise SunchangContractError(
            f"article {listed.identity}: premature education-ended state"
        )
    if listed.source_status != "교육마감" and listed.event_end < cutoff:
        raise SunchangContractError(
            f"article {listed.identity}: stale non-ended state"
        )
    return status


def _course_row(
    listed: _ListedCourse,
    detail: Mapping[str, Any],
    cutoff: date,
) -> dict[str, Any]:
    status = _normalized_status(listed, cutoff)
    control = bool(detail["application_control_present"])
    output: dict[str, Any] = {
        "provider": SUNCHANG_PROVIDER,
        "provider_course_id": (
            f"{SUNCHANG_PROVIDER}:education:{listed.category_code}:{listed.identity}"
        ),
        "title": listed.title,
        "branch": SUNCHANG_BRANCH,
        "branch_code": f"{SUNCHANG_PROVIDER}:scedu",
        "preserve_branch": True,
        "branch_url": SUNCHANG_BRANCH_URL,
        "raw_url": listed.detail_url,
        "application_url": listed.detail_url,
        "application_type": (
            "ONLINE_LOGIN_REQUIRED"
            if control
            else "ONLINE_APPLICATION_CONTROL_INACTIVE"
        ),
        "application_method_raw": (
            "회원 로그인 후 온라인 신청"
            if control
            else "현재 신청 버튼 없음"
        ),
        "reservation_available": bool(status == "OPEN" and control),
        "status": status,
        "period": listed.period,
        "apply_period": listed.apply_period,
        "schedule_raw": listed.schedule,
        "start_date": listed.event_start.isoformat(),
        "end_date": listed.event_end.isoformat(),
        "apply_start_date": listed.apply_start.isoformat(),
        "apply_end_date": listed.apply_end.isoformat(),
        "target": _clean(detail["target"]),
        "capacity": f"{listed.capacity_total}명",
        "capacity_total": listed.capacity_total,
        "capacity_remaining": listed.capacity_total - listed.applied_count,
        "fee": "",
        "venue_name": listed.venue,
        "room": listed.venue,
        "address": SUNCHANG_BRANCH_ADDRESS,
        "venue_address": "",
        "category": listed.category_name,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "static_html+detail_html",
        "program_type": "교육",
        "municipality_code": SUNCHANG_MUNICIPALITY_CODE,
        "municipality_name": SUNCHANG_MUNICIPALITY_NAME,
        "municipality_full_name": SUNCHANG_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": SUNCHANG_PARSER,
            "identity": listed.identity,
            "source_page": listed.page,
            "source_category_code": listed.category_code,
            "source_category_name": listed.category_name,
            "source_status": listed.source_status,
            "source_apply_period": listed.apply_period,
            "source_education_period": listed.period,
            "source_schedule": listed.schedule,
            "source_venue": listed.venue,
            "applied_count": listed.applied_count,
            "capacity_total": listed.capacity_total,
            "wait_count": int(detail["wait_count"]),
            "wait_capacity": int(detail["wait_capacity"]),
            "detail_verified": True,
            "application_control_present": control,
            "application_contract_verified": bool(
                detail["application_contract_verified"]
            ),
            "application_endpoint_method": "POST",
            "application_endpoint_fetched": False,
            "login_page_fetched": False,
            "join_page_fetched": False,
            "pii_form_fetched": False,
            "branch_basis": "official lifelong-learning-center owner/footer",
        },
    }
    return output


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    forbidden_fragments = (
        "html",
        "body",
        "description",
        "content",
        "instructor",
        "teacher",
        "phone",
        "email",
        "attachment",
        "applicant",
        "member",
        "login_id",
    )
    for key in row:
        lowered = str(key).lower()
        if any(fragment in lowered for fragment in forbidden_fragments):
            errors.append(f"forbidden top-level field {key}")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping):
        errors.append("raw_fields missing")
    else:
        for key in raw:
            lowered = str(key).lower()
            if any(
                fragment in lowered
                for fragment in (
                    "html",
                    "body",
                    "description",
                    "content",
                    "instructor",
                    "teacher",
                    "phone",
                    "email",
                    "attachment",
                    "applicant_name",
                    "member_id",
                )
            ):
                errors.append(f"forbidden raw field {key}")
    public_text = " ".join(
        _clean(row.get(key))
        for key in (
            "title",
            "branch",
            "target",
            "venue_name",
            "room",
            "schedule_raw",
        )
    )
    if _PHONE_RE.search(public_text):
        errors.append("public row contains a phone number")
    if _EMAIL_RE.search(public_text):
        errors.append("public row contains an email address")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            continue
        seen.add(identity)
        output.append(row)
    return output


def _snapshot_hash(rows: Iterable[_ListedCourse]) -> str:
    payload = json.dumps(
        [_row_core_signature(row) for row in rows],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _base_meta() -> dict[str, Any]:
    return {
        "municipality_code": SUNCHANG_MUNICIPALITY_CODE,
        "municipality_name": SUNCHANG_MUNICIPALITY_NAME,
        "owner_provider": SUNCHANG_PROVIDER,
        "owner_branch": SUNCHANG_BRANCH,
        "canonical_url": SUNCHANG_CANONICAL_URL,
        "parser": SUNCHANG_PARSER,
        "ownership_scope": "scedu_menu_002009000000_complete_lifelong_course_ledger",
        "official_categories": dict(SUNCHANG_CATEGORIES),
        "candidate_audit": {
            key: dict(value) for key, value in SUNCHANG_CANDIDATE_AUDIT.items()
        },
        "non_executing_aliases": [
            dict(value) for value in SUNCHANG_NON_EXECUTING_ALIASES
        ],
        "separate_owner_boundaries": [
            dict(value) for value in SUNCHANG_SEPARATE_OWNER_BOUNDARIES
        ],
        "recommended_override": dict(SUNCHANG_RECOMMENDED_OVERRIDE),
        "recommended_target": dict(SUNCHANG_RECOMMENDED_TARGET),
        "recommended_max_pages": SUNCHANG_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": SUNCHANG_RECOMMENDED_DETAIL_LIMIT,
        "source_requests": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "canonical_pages": 0,
        "empty_sentinel_page": 0,
        "category_pages": {},
        "category_sentinel_pages": {},
        "boundary_rechecks": 0,
        "current_list_pages_rechecked": [],
        "application_endpoint_calls": 0,
        "login_page_calls": 0,
        "join_page_calls": 0,
        "pii_form_calls": 0,
        "pagination_complete": False,
        "category_reconciliation_complete": False,
        "details_complete": False,
        "sentinel_rechecked": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }


def collect_sunchang_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = SUNCHANG_RECOMMENDED_MAX_PAGES,
    detail_limit: int = SUNCHANG_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    getter: Optional[Getter] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, stable current/future Sunchang snapshot."""

    meta = _base_meta()
    if not is_sunchang_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact new Sunchang lifelong-education owner"
        )
        return [], SUNCHANG_PARSER, meta
    try:
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
        ):
            raise ValueError("timeout/max_pages/detail_limit caps are invalid")
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], SUNCHANG_PARSER, meta

    factory = session_factory or _default_session_factory
    current_getter = getter or _default_getter
    session = factory()

    def get_page(category: str, page_number: int) -> _ListPage:
        parsed = _parse_list_page(
            _get_soup(
                session,
                current_getter,
                _list_url(page_number, category),
                timeout,
            ),
            category,
            page_number,
        )
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        return parsed

    try:
        canonical, canonical_pages, last_page, sentinel_page = _walk_ledger(
            get_page, "", max_pages
        )
        if not canonical:
            raise SunchangContractError(
                "canonical historical ledger unexpectedly became empty"
            )
        identities = [row.identity for row in canonical]
        if len(identities) != len(set(identities)):
            raise SunchangContractError("canonical identity set is not unique")

        category_pages: dict[str, int] = {}
        category_sentinels: dict[str, int] = {}
        filtered_by_identity: dict[str, _ListedCourse] = {}
        category_counts: dict[str, int] = {}
        for category in SUNCHANG_CATEGORIES:
            filtered, _pages, category_last, category_sentinel = _walk_ledger(
                get_page, category, max_pages
            )
            category_pages[category] = category_last
            category_sentinels[category] = category_sentinel
            category_counts[category] = len(filtered)
            for row in filtered:
                if row.identity in filtered_by_identity:
                    raise SunchangContractError(
                        f"article {row.identity}: appears in multiple audience categories"
                    )
                filtered_by_identity[row.identity] = row

        canonical_by_identity = {row.identity: row for row in canonical}
        if set(filtered_by_identity) != set(canonical_by_identity):
            raise SunchangContractError(
                "audience category ledgers do not equal the canonical ledger"
            )
        for identity, canonical_row in canonical_by_identity.items():
            if _row_core_signature(filtered_by_identity[identity]) != (
                _row_core_signature(canonical_row)
            ):
                raise SunchangContractError(
                    f"article {identity}: category/canonical row mismatch"
                )

        current = [row for row in canonical if row.event_end >= cutoff]
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "source_rows": len(canonical),
                "current_source_count": len(current),
                "expired_source_count": len(canonical) - len(current),
                "canonical_pages": last_page,
                "empty_sentinel_page": sentinel_page,
                "category_pages": category_pages,
                "category_sentinel_pages": category_sentinels,
                "source_category_counts": dict(
                    Counter(row.category_code for row in canonical)
                ),
                "category_reconciliation_counts": category_counts,
                "source_status_counts": dict(
                    Counter(row.source_status for row in canonical)
                ),
                "current_source_status_counts": dict(
                    Counter(row.source_status for row in current)
                ),
                "first_identity": identities[0],
                "last_identity": identities[-1],
                "snapshot_sha256": _snapshot_hash(canonical),
                "pagination_complete": True,
                "category_reconciliation_complete": True,
            }
        )
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise SunchangContractError(
                f"detail_limit {detail_limit} below required {len(current)}"
            )

        rows: list[dict[str, Any]] = []
        for listed in current:
            detail_soup = _get_soup(
                session,
                current_getter,
                listed.detail_url,
                timeout,
            )
            meta["source_requests"] += 1
            meta["detail_pages"] += 1
            detail = _parse_detail(listed, detail_soup)
            rows.append(_course_row(listed, detail, cutoff))

        stability_pages = sorted(
            {1, last_page, sentinel_page} | {row.page for row in current}
        )
        for page_number in stability_pages:
            rechecked = get_page("", page_number)
            meta["boundary_rechecks"] += 1
            if _page_signature(rechecked) != _page_signature(
                canonical_pages[page_number]
            ):
                raise SunchangContractError(
                    f"canonical page {page_number}: stability recheck failed"
                )
        meta["current_list_pages_rechecked"] = stability_pages
        meta["sentinel_rechecked"] = sentinel_page in stability_pages

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _dedupe)(rows))
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        if privacy_errors:
            raise SunchangContractError("; ".join(privacy_errors[:5]))
        if len(rows) != len(current):
            raise SunchangContractError(
                "dedupe changed the complete current article identity set"
            )

        meta.update(
            {
                "returned_count": len(rows),
                "status_counts": dict(Counter(row["status"] for row in rows)),
                "branch_counts": dict(Counter(row["branch"] for row in rows)),
                "application_control_count": sum(
                    bool(row["raw_fields"]["application_control_present"])
                    for row in rows
                ),
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not rows,
            }
        )
        return rows, SUNCHANG_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], SUNCHANG_PARSER, meta
    finally:
        _close_quietly(session)


collect = collect_sunchang_education


__all__ = [
    "SUNCHANG_BAD_CANDIDATE_ID",
    "SUNCHANG_BAD_CANDIDATE_URL",
    "SUNCHANG_BAD_NORMALIZED_SHA1",
    "SUNCHANG_BAD_NORMALIZED_SHA256",
    "SUNCHANG_BRANCH",
    "SUNCHANG_BRANCH_ADDRESS",
    "SUNCHANG_CANONICAL_CANDIDATE_ID",
    "SUNCHANG_CANONICAL_NORMALIZED_SHA1",
    "SUNCHANG_CANONICAL_NORMALIZED_SHA256",
    "SUNCHANG_CANONICAL_URL",
    "SUNCHANG_CANDIDATE_AUDIT",
    "SUNCHANG_CATEGORIES",
    "SUNCHANG_DISCOVERY_AUDIT",
    "SUNCHANG_NON_EXECUTING_ALIASES",
    "SUNCHANG_PARSER",
    "SUNCHANG_PROVIDER",
    "SUNCHANG_RECOMMENDED_DETAIL_LIMIT",
    "SUNCHANG_RECOMMENDED_MAX_PAGES",
    "SUNCHANG_RECOMMENDED_OVERRIDE",
    "SUNCHANG_RECOMMENDED_TARGET",
    "SUNCHANG_SEPARATE_OWNER_BOUNDARIES",
    "SunchangContractError",
    "collect",
    "collect_sunchang_education",
    "is_sunchang_education_target",
    "is_target",
]
