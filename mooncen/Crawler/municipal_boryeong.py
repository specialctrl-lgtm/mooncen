"""Fail-closed collector for Boryeong City's official education ledgers.

The canonical owner is the city's lifelong-learning course ledger.  The same
municipal owner also publishes a complete, all-branch library programme ledger
under ``/lib``.  This collector walks every declared page of both ledgers,
proves an empty post-last boundary, rechecks the first and last pages, and
validates every current/future education record against its detail and
identity-bound application control.

The Chungcheongnam-do municipality-directory result is not a course ledger.
Likewise, the city homepage highlights, notice boards, virtual-host aliases,
and individual library tabs are mirrors or subsets rather than independent
owners.  Library subscription entitlements and performances are intentionally
excluded from the education scope.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BORYEONG_PROVIDER = "MUNI_WWW_BRCN_GO_KR_9A0DF147"
BORYEONG_CANDIDATE_ID = "MUNI_IR_77425B1A4952"
BORYEONG_REJECTED_CANDIDATE_ID = "MUNI_IR_AE82ACA20618"
BORYEONG_MUNICIPALITY_CODE = "4418000000"
BORYEONG_MUNICIPALITY_NAME = "충청남도 보령시"

BORYEONG_HOST = "www.brcn.go.kr"
BORYEONG_LIFE_LIST_PATH = "/life/edu/comp/sub02_02_04/list.do"
BORYEONG_LIFE_DETAIL_PATH = "/life/edu/comp/sub02_02_04/view.do"
BORYEONG_LIFE_APPLICATION_PATH = "/life/edu/comp/sub02_02_04/form.do"
BORYEONG_LIBRARY_PATH = "/lib/front/index.php"
BORYEONG_CANONICAL_URL = f"https://{BORYEONG_HOST}{BORYEONG_LIFE_LIST_PATH}"
BORYEONG_LIBRARY_URL = (
    f"https://{BORYEONG_HOST}{BORYEONG_LIBRARY_PATH}?"
    "g_page=event&m_page=event02&siteCode=TOL"
)

BORYEONG_MAX_HTML_BYTES = 3_000_000
BORYEONG_MAX_WORKERS = 5
BORYEONG_PARSER = (
    "boryeong_lifelong_plus_all_branch_library_ledgers+"
    "all_declared_pages+empty_post_last_boundaries+stable_first_last+"
    "current_details+identity_bound_application_controls+"
    "education_only+exact_facility_branches+alias_subset_dedup+pii_allowlist"
)

BORYEONG_EXCLUDED_OFFICIAL_SOURCES: tuple[dict[str, str], ...] = (
    {
        "source": "https://www.chungnam.go.kr/cnportal/main/contents.do?menuNo=5100139",
        "candidate_id": BORYEONG_REJECTED_CANDIDATE_ID,
        "reason": "provincial_municipality_directory_not_course_ledger",
    },
    {
        "source": "https://www.brcn.go.kr/life.do",
        "reason": "homepage_highlights_are_subset_of_lifelong_ledger",
    },
    {
        "source": "https://www.brcn.go.kr/lib/front/index.php",
        "reason": "homepage_highlights_are_subset_of_library_ledger",
    },
    {
        "source": "https://www.brcn.go.kr/lib/front/index.php?g_page=event&m_page=event02&siteCode=ST01",
        "reason": "branch_tab_is_subset_of_all_branch_TOL_ledger",
    },
    {
        "source": (
            "brcn.go.kr / health.brcn.go.kr / tour.brcn.go.kr / "
            "farm.brcn.go.kr / forest.brcn.go.kr"
        ),
        "reason": "virtual_host_aliases_return_identical_city_ledgers",
    },
    {
        "source": "Boryeong education-office libraries",
        "reason": "separate_Chungcheongnamdo_Office_of_Education_owner",
    },
)

LIBRARY_BRANCHES: Mapping[str, str] = {
    "시립": "보령시립도서관",
    "죽정": "죽정도서관",
    "주산": "주산도서관",
    "오천": "오천작은도서관",
    "대천항": "대천항작은도서관",
    "문화의전당": "문화의전당작은도서관",
    "청라애": "청라애작은도서관",
    "성주고을": "성주고을작은도서관",
    "주교": "주교작은도서관",
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class BoryeongContractError(ValueError):
    """Raised when an official source no longer satisfies its audited shape."""


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE = re.compile(r"(?<!\d)(20\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)")
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-4]):([0-5]\d)(?!\d)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LIBRARY_CAPACITY = re.compile(
    r"^(.*?)\s+(\d+)\s*\(\s*(\d+)\s*\)\s*/\s*(\d+)\s*$"
)
_LIFE_CAPACITY = re.compile(r"^(\d+)\s*\(\s*(\d+)\s*\)$")
_NON_EDUCATION = re.compile(r"구독권|공연")

_LIFE_HEADERS = (
    "과정명",
    "기수",
    "교육기간",
    "교육시간",
    "접수인원(신청자)",
    "신청",
)
_LIBRARY_HEADERS = (
    "번호",
    "분류",
    "교육명",
    "대상 정원(대기)/접수현황",
    "접수기간",
    "수강기간",
    "상태",
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_ledger",
        "source_page",
        "source_status",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_venue",
        "source_detail_venue",
        "source_target",
        "source_fee",
        "target_evidence",
        "fee_evidence",
        "generation",
        "detail_verified",
        "application_control_present",
        "application_control_verified",
        "education_scope_verified",
        "service_family",
    }
)
_FORBIDDEN_FIELDS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "instructor",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
        "image_url",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlparse(url).query, keep_blank_values=True, strict_parsing=True)


def is_boryeong_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != BORYEONG_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    try:
        port = parsed.port
        query = _query(parsed.geturl())
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == BORYEONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == BORYEONG_LIFE_LIST_PATH
        and not query
        and not parsed.fragment
    )


is_target = is_boryeong_education_target


def _session() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return value


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _single_query_map(url: str) -> dict[str, str]:
    pairs = _query(url)
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate query key")
    return dict(pairs)


def _allowed_fetch_url(url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
        query = _single_query_map(url)
    except ValueError:
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == BORYEONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        return False
    if parsed.path == BORYEONG_LIFE_LIST_PATH:
        return not query or bool(
            set(query) == {"pageIndex"}
            and _IDENTITY.fullmatch(query["pageIndex"])
        )
    if parsed.path == BORYEONG_LIFE_DETAIL_PATH:
        return bool(
            set(query) == {"edu_idx"} and _IDENTITY.fullmatch(query["edu_idx"])
        )
    if parsed.path != BORYEONG_LIBRARY_PATH:
        return False
    required = {"g_page": "event", "m_page": "event02", "siteCode": "TOL"}
    if any(query.get(key) != expected for key, expected in required.items()):
        return False
    action = query.get("act", "")
    if not action:
        if not set(query) <= {*required, "page"}:
            return False
        return "page" not in query or bool(_IDENTITY.fullmatch(query["page"]))
    if action != "lecture_view" or set(query) != {
        *required,
        "act",
        "lgCode",
        "leCode",
    }:
        return False
    return bool(
        _IDENTITY.fullmatch(query["lgCode"])
        and _IDENTITY.fullmatch(query["leCode"])
    )


def _soup(
    url: str,
    timeout: int,
    factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    if not _allowed_fetch_url(url):
        raise BoryeongContractError(f"refusing URL outside audited owner: {url}")
    session = factory()
    try:
        response = fetcher(session, url, timeout)
        status_code = int(getattr(response, "status_code", 0) or 0)
        if 300 <= status_code < 400:
            raise BoryeongContractError("redirect responses are not followed")
        response.raise_for_status()
        final_url = _clean(getattr(response, "url", url))
        if not _allowed_fetch_url(final_url):
            raise BoryeongContractError(f"unexpected redirect: {final_url}")
        content = bytes(response.content)
        if not content or len(content) > BORYEONG_MAX_HTML_BYTES:
            raise BoryeongContractError("empty or oversized HTML response")
        return BeautifulSoup(content, "html.parser", from_encoding="utf-8")
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


def _dates(value: Any) -> tuple[date, ...]:
    result: list[date] = []
    for year, month, day in _DATE.findall(_clean(value)):
        result.append(date(int(year), int(month), int(day)))
    return tuple(result)


def _times(value: Any) -> tuple[str, ...]:
    return tuple(f"{int(hour):02d}:{minute}" for hour, minute in _TIME.findall(_clean(value)))


def _date_range(value: Any, *, label: str) -> tuple[date, date]:
    values = _dates(value)
    if len(values) == 1:
        return values[0], values[0]
    if len(values) != 2 or values[1] < values[0]:
        raise BoryeongContractError(f"{label}: invalid date range")
    return values[0], values[1]


def _life_list_url(page: int) -> str:
    if page == 1:
        return BORYEONG_CANONICAL_URL
    return f"{BORYEONG_CANONICAL_URL}?{urlencode({'pageIndex': page})}"


def _life_detail_url(identity: str) -> str:
    return (
        f"https://{BORYEONG_HOST}{BORYEONG_LIFE_DETAIL_PATH}?"
        f"{urlencode({'edu_idx': identity})}"
    )


def _library_list_url(page: int) -> str:
    params: list[tuple[str, Any]] = [
        ("g_page", "event"),
        ("m_page", "event02"),
        ("siteCode", "TOL"),
    ]
    if page != 1:
        params.append(("page", page))
    return f"https://{BORYEONG_HOST}{BORYEONG_LIBRARY_PATH}?{urlencode(params)}"


def _library_detail_url(group: str, identity: str) -> str:
    return (
        f"https://{BORYEONG_HOST}{BORYEONG_LIBRARY_PATH}?"
        + urlencode(
            {
                "g_page": "event",
                "m_page": "event02",
                "siteCode": "TOL",
                "act": "lecture_view",
                "lgCode": group,
                "leCode": identity,
            }
        )
    )


def _canonical_library_application(group: str, identity: str) -> str:
    return (
        f"https://{BORYEONG_HOST}{BORYEONG_LIBRARY_PATH}?"
        + urlencode(
            {
                "g_page": "event",
                "m_page": "event02",
                "siteCode": "TOL",
                "act": "lecture_receive_form",
                "lgCode": group,
                "leCode": identity,
            }
        )
    )


def _identity_from_link(
    href: str,
    *,
    path: str,
    identity_key: str,
) -> str:
    parsed = urlparse(urljoin(f"https://{BORYEONG_HOST}/", href))
    try:
        query = _single_query_map(parsed.geturl())
        port = parsed.port
    except ValueError as exc:
        raise BoryeongContractError("malformed identity link") from exc
    identity = query.get(identity_key, "")
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == BORYEONG_HOST
        and port is None
        and parsed.path == path
        and _IDENTITY.fullmatch(identity)
    ):
        raise BoryeongContractError("identity link left audited owner or shape")
    return identity


def _declared_page_values(soup: BeautifulSoup, key: str) -> set[int]:
    result = {1}
    for anchor in soup.select("a[href]"):
        absolute = urljoin(f"https://{BORYEONG_HOST}/", anchor.get("href", ""))
        try:
            values = _single_query_map(absolute)
        except ValueError:
            continue
        raw = values.get(key, "")
        if _IDENTITY.fullmatch(raw):
            result.add(int(raw))
    return result


def _life_status(value: str) -> str:
    if value in {"접수중", "신청중", "모집중"}:
        return "OPEN"
    if value in {"마감", "접수마감", "신청마감"}:
        return "CLOSED"
    if value in {"접수예정", "접수대기", "신청예정", "모집예정"}:
        return "SCHEDULED"
    raise BoryeongContractError(f"unknown lifelong status: {value}")


def _table_headers(table: Any) -> tuple[str, ...]:
    row = table.select_one("thead tr") or table.select_one("tr")
    return tuple(_clean(cell.get_text(" ", strip=True)) for cell in row.select("th"))


def _parse_life_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    tables = soup.select("table")
    matching = [table for table in tables if _table_headers(table) == _LIFE_HEADERS]
    if len(matching) != 1:
        raise BoryeongContractError("lifelong course table/header changed")
    rows: list[dict[str, Any]] = []
    unknown_rows: list[str] = []
    for tr in matching[0].select("tbody tr"):
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select("td")]
        links = tr.select(f"a[href*='{BORYEONG_LIFE_DETAIL_PATH}'][href*='edu_idx=']")
        if not links:
            text = _clean(tr.get_text(" ", strip=True))
            if text and text != "데이터가 없습니다":
                unknown_rows.append(text)
            continue
        if len(cells) != 6:
            raise BoryeongContractError("lifelong row width changed")
        identities = {
            _identity_from_link(
                link.get("href", ""),
                path=BORYEONG_LIFE_DETAIL_PATH,
                identity_key="edu_idx",
            )
            for link in links
        }
        if len(identities) != 1:
            raise BoryeongContractError("lifelong row identity drift")
        capacity = _LIFE_CAPACITY.fullmatch(cells[4])
        if not capacity:
            raise BoryeongContractError("lifelong capacity shape changed")
        start, end = _date_range(cells[2], label="lifelong education period")
        rows.append(
            {
                "identity": identities.pop(),
                "title": cells[0],
                "generation": cells[1],
                "education_period": cells[2],
                "schedule": cells[3],
                "capacity_total": int(capacity.group(1)),
                "capacity_current": int(capacity.group(2)),
                "source_status": cells[5],
                "status": _life_status(cells[5]),
                "start": start,
                "end": end,
                "page": page,
            }
        )
    if unknown_rows:
        raise BoryeongContractError("unparsed lifelong table row")
    return {
        "rows": rows,
        "declared_pages": max({page, *_declared_page_values(soup, "pageIndex")}),
    }


def _library_link_identity(href: str, expected_action: str) -> tuple[str, str]:
    parsed = urlparse(urljoin(f"https://{BORYEONG_HOST}/lib/front/", href))
    try:
        query = _single_query_map(parsed.geturl())
        port = parsed.port
    except ValueError as exc:
        raise BoryeongContractError("malformed library identity link") from exc
    group, identity = query.get("lgCode", ""), query.get("leCode", "")
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == BORYEONG_HOST
        and port is None
        and parsed.path == BORYEONG_LIBRARY_PATH
        and query.get("g_page") == "event"
        and query.get("m_page") == "event02"
        and query.get("act") == expected_action
        and _IDENTITY.fullmatch(group)
        and _IDENTITY.fullmatch(identity)
    ):
        raise BoryeongContractError("library identity link changed")
    return group, identity


def _library_status(tr: Any, status_text: str, group: str, identity: str) -> tuple[str, str]:
    controls = tr.select("a[href*='act=lecture_receive_form']")
    result_links = tr.select("a[href*='act=lecture_result_view']")
    for link in result_links:
        if _library_link_identity(link.get("href", ""), "lecture_result_view") != (
            group,
            identity,
        ):
            raise BoryeongContractError("library result control identity drift")
    if len(controls) > 1:
        raise BoryeongContractError("multiple library application controls")
    if controls:
        control = controls[0]
        if _library_link_identity(control.get("href", ""), "lecture_receive_form") != (
            group,
            identity,
        ):
            raise BoryeongContractError("library application identity drift")
        text = _clean(control.get_text(" ", strip=True))
        if text not in {"신청하기", "대기자신청"} or text not in status_text:
            raise BoryeongContractError("library actionable status/control drift")
        return "OPEN", text
    reduced = _clean(status_text.replace("접수확인", ""))
    if reduced in {"접수마감", "신청마감", "마감"}:
        return "CLOSED", ""
    if reduced in {"접수예정", "접수대기", "대기중", "신청예정", "모집예정"}:
        return "SCHEDULED", ""
    raise BoryeongContractError(f"unknown library status: {status_text}")


def _parse_library_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    tables = soup.select("table")
    matching = [table for table in tables if _table_headers(table) == _LIBRARY_HEADERS]
    if len(matching) != 1:
        raise BoryeongContractError("library programme table/header changed")
    rows: list[dict[str, Any]] = []
    unknown_rows: list[str] = []
    for tr in matching[0].select("tbody tr"):
        cells = [_clean(cell.get_text(" ", strip=True)) for cell in tr.select("td")]
        detail_links = tr.select("a[href*='act=lecture_view']")
        if not detail_links:
            text = _clean(tr.get_text(" ", strip=True))
            if text and "등록된 데이터가 없습니다" not in text:
                unknown_rows.append(text)
            continue
        if len(cells) != 7 or len(detail_links) != 1:
            raise BoryeongContractError("library row width/detail identity changed")
        try:
            number = int(cells[0])
        except ValueError as exc:
            raise BoryeongContractError("library row number changed") from exc
        branch = LIBRARY_BRANCHES.get(cells[1], "")
        if not branch:
            raise BoryeongContractError(f"unknown library branch: {cells[1]}")
        group, identity = _library_link_identity(
            detail_links[0].get("href", ""), "lecture_view"
        )
        capacity = _LIBRARY_CAPACITY.fullmatch(cells[3])
        if not capacity:
            raise BoryeongContractError("library audience/capacity shape changed")
        apply_start, apply_end = _date_range(cells[4], label="library apply period")
        start, end = _date_range(cells[5], label="library education period")
        status, control_text = _library_status(tr, cells[6], group, identity)
        rows.append(
            {
                "number": number,
                "group": group,
                "identity": identity,
                "title": cells[2],
                "branch_label": cells[1],
                "branch": branch,
                "target": _clean(capacity.group(1)),
                "capacity_total": int(capacity.group(2)),
                "wait_capacity": int(capacity.group(3)),
                "capacity_current": int(capacity.group(4)),
                "apply_period": cells[4],
                "apply_start": apply_start,
                "apply_end": apply_end,
                "education_period": cells[5],
                "schedule": cells[5],
                "source_status": _clean(cells[6].replace("접수확인", "")),
                "status": status,
                "control_text": control_text,
                "start": start,
                "end": end,
                "page": page,
                "education_exclusion": (
                    "subscription_or_performance_not_education"
                    if _NON_EDUCATION.search(cells[2])
                    else ""
                ),
            }
        )
    if unknown_rows:
        raise BoryeongContractError("unparsed library table row")
    return {
        "rows": rows,
        "declared_pages": max({page, *_declared_page_values(soup, "page")}),
    }


def _page_signature(page: Mapping[str, Any], ledger: str) -> tuple[Any, ...]:
    if ledger == "life":
        return tuple(
            (
                row["identity"],
                row["title"],
                row["generation"],
                row["education_period"],
                row["schedule"],
                row["capacity_total"],
                row["capacity_current"],
                row["source_status"],
            )
            for row in page["rows"]
        )
    return tuple(
        (
            row["number"],
            row["group"],
            row["identity"],
            row["title"],
            row["branch_label"],
            row["apply_period"],
            row["education_period"],
            row["source_status"],
            row["control_text"],
        )
        for row in page["rows"]
    )


def _detail_fields(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = tr.select("th,td")
        index = 0
        while index + 1 < len(cells):
            if cells[index].name == "th" and cells[index + 1].name == "td":
                key = _clean(cells[index].get_text(" ", strip=True))
                value = _clean(cells[index + 1].get_text(" ", strip=True))
                if key:
                    if key in result and result[key] != value:
                        raise BoryeongContractError(f"duplicate detail field: {key}")
                    result[key] = value
                index += 2
            else:
                index += 1
    return result


def _library_detail_fields(soup: BeautifulSoup) -> dict[str, str]:
    expected = {"대상", "정원", "대상인원", "대기인원", "재료비", "계획서"}
    result: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = tr.select("th,td")
        for index in range(0, len(cells) - 1, 2):
            key = _clean(cells[index].get_text(" ", strip=True))
            if key not in expected:
                continue
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if key in result and result[key] != value:
                raise BoryeongContractError(f"duplicate library detail field: {key}")
            result[key] = value
    return result


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    return f"BORYEONG_BRANCH_{digest}"


def _base_row(
    listed: Mapping[str, Any],
    *,
    ledger: str,
    identity: str,
    title: str,
    branch: str,
    raw_url: str,
    application_url: str,
    target: str,
    fee: str,
    category: str,
    apply_start: Optional[date],
    apply_end: Optional[date],
    source_detail_venue: str,
) -> dict[str, Any]:
    status = str(listed["status"])
    open_control = status == "OPEN" and application_url != raw_url
    application_method = (
        "온라인" if open_control else ("접수예정" if status == "SCHEDULED" else "접수마감")
    )
    source_apply_period = _clean(listed.get("apply_period"))
    row: dict[str, Any] = {
        "provider": BORYEONG_PROVIDER,
        "provider_course_id": f"{BORYEONG_PROVIDER}:{ledger}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": category,
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": application_url,
        "application_type": "ONLINE_APPLICATION" if open_control else "INFO_ONLY",
        "application_method": application_method,
        "application_methods": [application_method],
        "reservation_available": open_control,
        "status": status,
        "fee": fee,
        "period": f"{listed['start'].isoformat()} ~ {listed['end'].isoformat()}",
        "start_date": listed["start"].isoformat(),
        "end_date": listed["end"].isoformat(),
        "apply_period": (
            f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
            if apply_start is not None and apply_end is not None
            else ""
        ),
        "schedule_raw": _clean(listed["schedule"]),
        "capacity": f"{listed['capacity_total']}명",
        "capacity_current": int(listed["capacity_current"]),
        "capacity_total": int(listed["capacity_total"]),
        "target": target,
        "venue": branch,
        "venue_name": branch,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": BORYEONG_PARSER,
        "municipality_code": BORYEONG_MUNICIPALITY_CODE,
        "municipality_full_name": BORYEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_ledger": ledger,
            "source_page": int(listed["page"]),
            "source_status": _clean(listed["source_status"]),
            "source_apply_period": source_apply_period,
            "source_education_period": _clean(listed["education_period"]),
            "source_schedule": _clean(listed["schedule"]),
            "source_venue": branch,
            "source_detail_venue": source_detail_venue,
            "source_target": target,
            "source_fee": fee,
            "target_evidence": (
                "official_detail_or_list"
                if target != "대상 별도 안내"
                else "official_lifelong_detail_omits_target"
            ),
            "fee_evidence": (
                "official_detail"
                if fee != "요금 별도 안내"
                else "official_detail_omits_fee"
            ),
            "generation": _clean(listed.get("generation")),
            "detail_verified": True,
            "application_control_present": open_control,
            "application_control_verified": True,
            "education_scope_verified": True,
            "service_family": "education",
        },
    }
    return row


def _validate_life_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = str(listed["identity"])
    fields = _detail_fields(soup)
    required = {"과정명", "교육기간", "교육시간", "교육장소", "접수기간", "접수인원", "신청자"}
    if not required <= set(fields):
        raise BoryeongContractError(f"lifelong {identity}: required detail fields missing")
    expected_title = f"{listed['title']} - {listed['generation']}"
    if fields["과정명"] != expected_title:
        raise BoryeongContractError(f"lifelong {identity}: title identity drift")
    if _dates(fields["교육기간"]) != (listed["start"], listed["end"]):
        raise BoryeongContractError(f"lifelong {identity}: education date drift")
    if _times(fields["교육시간"]) != _times(listed["schedule"]):
        raise BoryeongContractError(f"lifelong {identity}: schedule drift")
    try:
        capacity_total = int(re.sub(r"\D", "", fields["접수인원"]))
        capacity_current = int(re.sub(r"\D", "", fields["신청자"]))
    except ValueError as exc:
        raise BoryeongContractError(f"lifelong {identity}: detail capacity changed") from exc
    if (capacity_total, capacity_current) != (
        listed["capacity_total"],
        listed["capacity_current"],
    ):
        raise BoryeongContractError(f"lifelong {identity}: capacity drift")
    apply_start, apply_end = _date_range(fields["접수기간"], label="lifelong apply period")
    venue = fields["교육장소"]
    if "평생학습관" in venue:
        branch = "보령시평생학습관"
    elif venue == "남대천 공유주방(수산길 27)":
        branch = "남대천 공유주방"
    else:
        raise BoryeongContractError(f"lifelong {identity}: unexpected facility")
    controls = soup.select(
        f"a[href*='{BORYEONG_LIFE_APPLICATION_PATH}'][href*='edu_idx=']"
    )
    identities = {
        _identity_from_link(
            control.get("href", ""),
            path=BORYEONG_LIFE_APPLICATION_PATH,
            identity_key="edu_idx",
        )
        for control in controls
    }
    if listed["status"] == "OPEN":
        if len(controls) != 1 or identities != {identity}:
            raise BoryeongContractError(f"lifelong {identity}: application identity drift")
        application_url = (
            f"https://{BORYEONG_HOST}{BORYEONG_LIFE_APPLICATION_PATH}?"
            f"{urlencode({'edu_idx': identity})}"
        )
    else:
        if controls:
            raise BoryeongContractError(f"lifelong {identity}: closed/scheduled action exposed")
        application_url = _life_detail_url(identity)
    listed_with_apply = dict(listed)
    listed_with_apply["apply_period"] = fields["접수기간"]
    return _base_row(
        listed_with_apply,
        ledger="life",
        identity=identity,
        title=str(listed["title"]),
        branch=branch,
        raw_url=_life_detail_url(identity),
        application_url=application_url,
        target="대상 별도 안내",
        fee="요금 별도 안내",
        category="평생학습",
        apply_start=apply_start,
        apply_end=apply_end,
        source_detail_venue=venue,
    )


def _label_value(text: str, label: str, next_labels: tuple[str, ...]) -> str:
    starts = text.find(f"{label} :")
    if starts < 0:
        return ""
    starts += len(f"{label} :")
    ends = [text.find(f"{item} :", starts) for item in next_labels]
    ends = [value for value in ends if value >= 0]
    return _clean(text[starts : min(ends) if ends else len(text)])


def _assert_library_venue(branch: str, venue: str, identity: str) -> None:
    if not venue:
        return
    detected = {
        full
        for short, full in LIBRARY_BRANCHES.items()
        if short in venue or full in venue
    }
    if detected and detected != {branch}:
        raise BoryeongContractError(f"library {identity}: detail facility drift")


def _validate_library_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup
) -> dict[str, Any]:
    group, identity = str(listed["group"]), str(listed["identity"])
    headings = [_clean(value.get_text(" ", strip=True)) for value in soup.select("h3")]
    if headings.count(str(listed["title"])) != 1:
        raise BoryeongContractError(f"library {group}/{identity}: title identity drift")
    info_blocks = [
        _clean(value.get_text(" ", strip=True))
        for value in soup.select("dl")
        if "접수 기간" in value.get_text() and "강좌 기간" in value.get_text()
    ]
    if len(info_blocks) != 1:
        raise BoryeongContractError(f"library {group}/{identity}: detail period block missing")
    info = info_blocks[0]
    apply_period = _label_value(info, "접수 기간", ("강좌 기간",))
    education_period = _label_value(info, "강좌 기간", ("강좌 일시",))
    schedule = _label_value(info, "강좌 일시", ("강좌 장소", "수업계획안"))
    venue = _label_value(info, "강좌 장소", ("수업계획안",))
    if _dates(apply_period) != (listed["apply_start"], listed["apply_end"]):
        raise BoryeongContractError(f"library {group}/{identity}: apply date drift")
    expected_dates = (listed["start"],) if listed["start"] == listed["end"] else (
        listed["start"],
        listed["end"],
    )
    if _dates(education_period) != expected_dates:
        raise BoryeongContractError(f"library {group}/{identity}: education date drift")
    if _times(schedule) != _times(listed["education_period"]):
        raise BoryeongContractError(f"library {group}/{identity}: schedule drift")
    fields = _library_detail_fields(soup)
    try:
        detail_total = int(re.sub(r"\D", "", fields["대상인원"]))
        detail_wait = int(re.sub(r"\D", "", fields["대기인원"]))
    except (KeyError, ValueError) as exc:
        raise BoryeongContractError(f"library {group}/{identity}: capacity fields changed") from exc
    if (detail_total, detail_wait) != (
        listed["capacity_total"],
        listed["wait_capacity"],
    ):
        raise BoryeongContractError(f"library {group}/{identity}: capacity drift")
    if fields.get("대상") and _clean(fields["대상"]) != _clean(listed["target"]):
        raise BoryeongContractError(f"library {group}/{identity}: target drift")
    _assert_library_venue(str(listed["branch"]), venue, identity)
    controls = soup.select("a[href*='act=lecture_receive_form']")
    if len(controls) > 1:
        raise BoryeongContractError(f"library {group}/{identity}: multiple detail controls")
    if controls:
        found = _library_link_identity(
            controls[0].get("href", ""), "lecture_receive_form"
        )
        text = _clean(controls[0].get_text(" ", strip=True))
        if found != (group, identity) or text != listed["control_text"]:
            raise BoryeongContractError(f"library {group}/{identity}: application identity drift")
    if listed["status"] == "OPEN":
        if len(controls) != 1:
            raise BoryeongContractError(f"library {group}/{identity}: actionable control missing")
        application_url = _canonical_library_application(group, identity)
    else:
        if controls:
            raise BoryeongContractError(
                f"library {group}/{identity}: closed/scheduled action exposed"
            )
        application_url = _library_detail_url(group, identity)
    return _base_row(
        listed,
        ledger="library",
        identity=f"{group}:{identity}",
        title=str(listed["title"]),
        branch=str(listed["branch"]),
        raw_url=_library_detail_url(group, identity),
        application_url=application_url,
        target=str(listed["target"]),
        fee=_clean(fields.get("재료비")) or "요금 별도 안내",
        category="도서관",
        apply_start=listed["apply_start"],
        apply_end=listed["apply_end"],
        source_detail_venue=venue,
    )


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_FIELDS:
        errors.append("forbidden detail/PII key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw field allowlist exceeded")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail persisted")
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
        return datetime.now().astimezone().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def collect_boryeong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 200,
    today: Optional[date | datetime | str] = None,
    max_workers: int = BORYEONG_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "municipality_code": BORYEONG_MUNICIPALITY_CODE,
        "owner_provider": BORYEONG_PROVIDER,
        "candidate_id": BORYEONG_CANDIDATE_ID,
        "canonical_url": BORYEONG_CANONICAL_URL,
        "secondary_canonical_url": BORYEONG_LIBRARY_URL,
        "parser": BORYEONG_PARSER,
        "source_scope_contract": "lifelong_and_all_branch_library_complete_ledgers",
        "rejected_candidate_id": BORYEONG_REJECTED_CANDIDATE_ID,
        "rejected_candidate_reason": "provincial_municipality_directory_not_course_ledger",
        "excluded_official_sources": list(BORYEONG_EXCLUDED_OFFICIAL_SOURCES),
        "source_requests": 0,
        "boundary_rechecks": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "education_excluded_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "stable_first_last": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }
    if not is_boryeong_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Boryeong lifelong owner"
        )
        return [], BORYEONG_PARSER, meta
    try:
        cutoff = _today(today)
        if any(
            isinstance(value, bool) or int(value) < 1
            for value in (timeout, max_pages, max_workers)
        ) or isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("invalid limits")
    except Exception as exc:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], BORYEONG_PARSER, meta

    factory, current_fetcher = session_factory or _session, fetcher or _request
    workers = min(int(max_workers), BORYEONG_MAX_WORKERS)

    def fetch_page(ledger: str, page: int) -> dict[str, Any]:
        if ledger == "life":
            return _parse_life_page(
                _soup(_life_list_url(page), int(timeout), factory, current_fetcher),
                page,
            )
        return _parse_library_page(
            _soup(_library_list_url(page), int(timeout), factory, current_fetcher),
            page,
        )

    try:
        with ThreadPoolExecutor(max_workers=min(workers, 2)) as pool:
            first_futures = {
                pool.submit(fetch_page, "life", 1): "life",
                pool.submit(fetch_page, "library", 1): "library",
            }
            first: dict[str, dict[str, Any]] = {
                ledger: future.result() for future, ledger in first_futures.items()
            }
        meta["source_requests"] = 2
        page_counts = {
            ledger: int(first[ledger]["declared_pages"])
            for ledger in ("life", "library")
        }
        if any(value < 1 for value in page_counts.values()):
            raise BoryeongContractError("invalid declared page count")
        if any(value > int(max_pages) for value in page_counts.values()):
            meta["source_cap_reached"] = True
            raise BoryeongContractError(
                f"max_pages {max_pages} below declared {page_counts}"
            )
        pages: dict[str, dict[int, dict[str, Any]]] = {
            "life": {1: first["life"]},
            "library": {1: first["library"]},
        }
        tasks = [
            (ledger, page)
            for ledger in ("life", "library")
            for page in range(2, page_counts[ledger] + 1)
        ]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(fetch_page, ledger, page): (ledger, page)
                for ledger, page in tasks
            }
            for future in as_completed(futures):
                ledger, page = futures[future]
                pages[ledger][page] = future.result()
        meta["source_requests"] += len(tasks)
        for ledger in ("life", "library"):
            if any(not pages[ledger][page]["rows"] for page in pages[ledger]):
                raise BoryeongContractError(f"{ledger}: empty declared page")
            for page in pages[ledger].values():
                if page["declared_pages"] != page_counts[ledger]:
                    raise BoryeongContractError(f"{ledger}: pagination declaration drift")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            boundary_futures = {
                pool.submit(fetch_page, ledger, page_counts[ledger] + 1): (
                    ledger,
                    "post_last",
                )
                for ledger in ("life", "library")
            }
            for ledger in ("life", "library"):
                boundary_futures[
                    pool.submit(fetch_page, ledger, 1)
                ] = (ledger, "first")
                boundary_futures[
                    pool.submit(fetch_page, ledger, page_counts[ledger])
                ] = (ledger, "last")
            boundaries = {
                marker: future.result()
                for future, marker in boundary_futures.items()
            }
        meta["source_requests"] += len(boundary_futures)
        meta["boundary_rechecks"] = 4
        for ledger in ("life", "library"):
            if boundaries[(ledger, "post_last")]["rows"]:
                raise BoryeongContractError(f"{ledger}: post-last page is not empty")
            if _page_signature(boundaries[(ledger, "first")], ledger) != _page_signature(
                pages[ledger][1], ledger
            ):
                raise BoryeongContractError(f"{ledger}: first-page stability failed")
            if _page_signature(boundaries[(ledger, "last")], ledger) != _page_signature(
                pages[ledger][page_counts[ledger]], ledger
            ):
                raise BoryeongContractError(f"{ledger}: last-page stability failed")

        life_rows = [
            row
            for page in range(1, page_counts["life"] + 1)
            for row in pages["life"][page]["rows"]
        ]
        library_rows = [
            row
            for page in range(1, page_counts["library"] + 1)
            for row in pages["library"][page]["rows"]
        ]
        life_ids = [row["identity"] for row in life_rows]
        library_ids = [(row["group"], row["identity"]) for row in library_rows]
        if len(life_ids) != len(set(life_ids)):
            raise BoryeongContractError("duplicate lifelong identity across pages")
        if len(library_ids) != len(set(library_ids)):
            raise BoryeongContractError("duplicate library identity across pages")
        numbers = [int(row["number"]) for row in library_rows]
        if numbers != list(range(numbers[0], numbers[-1] - 1, -1)):
            raise BoryeongContractError("library ledger row-number continuity failed")
        if page_counts["library"] != max(
            max(page["declared_pages"] for page in pages["library"].values()),
            math.ceil(len(library_rows) / 10),
        ):
            raise BoryeongContractError("library declared-page completeness failed")

        education_excluded = [
            row for row in library_rows if row["education_exclusion"]
        ]
        current_life = [row for row in life_rows if row["end"] >= cutoff]
        current_library = [
            row
            for row in library_rows
            if row["end"] >= cutoff and not row["education_exclusion"]
        ]
        current = [("life", row) for row in current_life] + [
            ("library", row) for row in current_library
        ]
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], BORYEONG_PARSER, meta

    meta.update(
        {
            "cutoff": cutoff.isoformat(),
            "declared_pages": page_counts,
            "source_rows_by_ledger": {
                "life": len(life_rows),
                "library": len(library_rows),
            },
            "source_rows": len(life_rows) + len(library_rows),
            "source_total": len(life_rows) + len(library_rows),
            "education_excluded_count": len(education_excluded),
            "education_exclusion_counts": dict(
                Counter(row["education_exclusion"] for row in education_excluded)
            ),
            "current_source_count_by_ledger": {
                "life": len(current_life),
                "library": len(current_library),
            },
            "current_source_count": len(current),
            "pagination_complete": True,
            "stable_first_last": True,
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
        return [], BORYEONG_PARSER, meta

    def fetch_detail(item: tuple[str, Mapping[str, Any]]) -> dict[str, Any]:
        ledger, listed = item
        if ledger == "life":
            identity = str(listed["identity"])
            return _validate_life_detail(
                listed,
                _soup(_life_detail_url(identity), int(timeout), factory, current_fetcher),
            )
        group, identity = str(listed["group"]), str(listed["identity"])
        return _validate_library_detail(
            listed,
            _soup(
                _library_detail_url(group, identity),
                int(timeout),
                factory,
                current_fetcher,
            ),
        )

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_detail, item): item for item in current}
        for future in as_completed(futures):
            ledger, listed = futures[future]
            identity = (
                str(listed["identity"])
                if ledger == "life"
                else f"{listed['group']}:{listed['identity']}"
            )
            try:
                rows.append(future.result())
                meta["detail_pages"] += 1
            except Exception as exc:
                errors.append(f"{ledger}/{identity}: {type(exc).__name__}: {_clean(exc)}")
    meta["source_requests"] += len(current)
    if errors:
        meta["configured_collection_error"] = "; ".join(sorted(errors)[:5])
        return [], BORYEONG_PARSER, meta
    rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
    rows = list((dedupe_rows or _dedupe)(rows))
    privacy = [error for row in rows for error in _privacy_errors(row)]
    if privacy or len(rows) != len(current):
        meta["configured_collection_error"] = (
            "; ".join(privacy[:5]) or "dedupe changed identity set"
        )
        return [], BORYEONG_PARSER, meta
    meta.update(
        {
            "returned_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "branch_counts": dict(Counter(row["branch"] for row in rows)),
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not rows,
        }
    )
    return rows, BORYEONG_PARSER, meta


collect = collect_boryeong_education
