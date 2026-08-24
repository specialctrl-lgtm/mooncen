"""Fail-closed collector for Mungyeong youth-culture education lectures.

The official Mungyeong reservation site exposes the complete youth-culture
lecture catalogue through one POST-paginated list.  It does not expose a row
total and requests beyond the declared last page are clamped back to page one,
so a post-boundary page cannot prove exhaustion.  This collector instead
requires every declared page and then verifies the site's official empty
search response with a fixed no-match term.  Every current/future row must also
pass its detail-page contract before any snapshot is published.

The module intentionally does not import ``Crawler_MunicipalYaml``.  The
shared router must inject its managed soup fetcher and SafeSession factory.
TLS verification is never disabled and any cap, page, row, detail, identity,
or application-contract failure returns an empty snapshot.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


MUNGYEONG_MUNICIPALITY_CODE = "4728000000"
MUNGYEONG_MUNICIPALITY_NAME = "경상북도 문경시"
MUNGYEONG_CANDIDATE_ID = "MUNI_IR_3D76A819B980"

MUNGYEONG_YOUTH_PROVIDER = "MUNI_WWW_GBMG_GO_KR_E3F4EA45"
MUNGYEONG_YOUTH_HOST = "www.gbmg.go.kr"
MUNGYEONG_YOUTH_MENU_ID = "0109020000"
MUNGYEONG_YOUTH_LIST_PATH = "/reservation/youthCulture/lecture/list.do"
MUNGYEONG_YOUTH_DETAIL_PATH = "/reservation/youthCulture/lecture/view.do"
MUNGYEONG_YOUTH_ENROLL_PATH = "/reservation/youthCulture/enroll/write.do"
MUNGYEONG_YOUTH_LIST_URL = (
    f"https://{MUNGYEONG_YOUTH_HOST}{MUNGYEONG_YOUTH_LIST_PATH}"
    f"?mId={MUNGYEONG_YOUTH_MENU_ID}"
)
MUNGYEONG_YOUTH_BRANCH = "문경시 청소년문화의집"
MUNGYEONG_YOUTH_ADDRESS = "경상북도 문경시 신흥로 11"
MUNGYEONG_YOUTH_DEFAULT_TARGET = "초등 3학년 이상 청소년"
MUNGYEONG_YOUTH_EMPTY_SENTINEL_TERM = "문경수집빈결과검증"
MUNGYEONG_YOUTH_PARSER = (
    "mungyeong_youth_complete_declared_pages+official_empty_search_sentinel+"
    "current_detail_fail_closed"
)
MUNGYEONG_YOUTH_RETIRED_UNPREFIXED_URL = (
    "https://www.gbmg.go.kr/youthCulture/lecture/list.do?mId=0109020000"
)


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_PAGE_RE = re.compile(r"현재\s*페이지\s*(\d+)\s*/\s*전체\s*페이지\s*(\d+)")
_CAPACITY_RE = re.compile(
    r"^(\d{1,5})\s*/\s*(\d{1,5})"
    r"(?:\s*\(\s*(\d{1,5})\s*/\s*(\d{1,5})\s*\))?$"
)
_LIST_HEADERS = (
    "구분",
    "교육명",
    "신청기간",
    "교육기간",
    "접수자/정원 (예비자/정원)",
    "상태",
)
_DETAIL_REQUIRED_LABELS = frozenset(
    {
        "신청기간",
        "교육기간",
        "교육시간",
        "강의장소",
        "신청현황",
        "진행상태",
        "교육내용 소개",
    }
)
_NO_DATA_TEXTS = frozenset(
    {
        "등록된 교육 자료가 없습니다.",
        "검색된 교육 자료가 없습니다.",
        "등록된 자료가 없습니다.",
        "데이터가 없습니다.",
    }
)
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "신청가능": "OPEN",
    "대기자접수중": "WAITING",
    "대기접수중": "WAITING",
    "대기자접수": "WAITING",
    "접수예정": "SCHEDULED",
    "신청예정": "SCHEDULED",
    "신청대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "신청마감": "CLOSED",
    "교육중": "CLOSED",
    "운영중": "CLOSED",
    "종료": "CLOSED",
    "교육종료": "CLOSED",
    "교육완료": "CLOSED",
    "폐강": "CANCELLED",
    "취소": "CANCELLED",
}


class MungyeongContractError(ValueError):
    """Raised when the official response no longer matches the audited contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).lower())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _normalize_range(value: Any) -> str:
    text = _clean(value)
    text = _DATE_RE.sub(
        lambda match: (
            f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        ),
        text,
    )
    return _clean(re.sub(r"\s*~\s*", " ~ ", text))


def _range_dates(value: Any) -> tuple[date, date]:
    matches = list(_DATE_RE.finditer(_clean(value)))
    if len(matches) != 2:
        raise MungyeongContractError("date range must contain exactly two dates")
    parsed = [
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        for match in matches
    ]
    if parsed[1] < parsed[0]:
        raise MungyeongContractError("date range ends before it starts")
    return parsed[0], parsed[1]


def _education_period(value: Any) -> tuple[str, str, date, date]:
    text = _normalize_range(value)
    schedule = ""
    match = re.search(r"\(([^()]*)\)\s*$", text)
    if match:
        schedule = _clean(match.group(1))
        text = _clean(text[: match.start()])
    start, end = _range_dates(text)
    return text, schedule, start, end


def _capacity(value: Any) -> tuple[int, int, Optional[int], Optional[int]]:
    text = _clean(value).replace(",", "")
    match = _CAPACITY_RE.fullmatch(text)
    if not match:
        raise MungyeongContractError("invalid applicant/capacity value")
    current, total, wait_current, wait_total = match.groups()
    if int(total) <= 0:
        raise MungyeongContractError("capacity total must be positive")
    return (
        int(current),
        int(total),
        int(wait_current) if wait_current is not None else None,
        int(wait_total) if wait_total is not None else None,
    )


def _status(value: Any) -> str:
    source = re.sub(r"\s+", "", _clean(value))
    status = _STATUS_MAP.get(source, "")
    if not status:
        raise MungyeongContractError(f"unknown source status: {source or '<empty>'}")
    return status


def _page_numbers(soup: BeautifulSoup) -> tuple[int, int]:
    node = soup.select_one("p.page_num")
    match = _PAGE_RE.search(_clean(node.get_text(" ", strip=True) if node else ""))
    if not match:
        raise MungyeongContractError("missing current/total page marker")
    current, total = int(match.group(1)), int(match.group(2))
    if current < 1 or total < 1 or current > total:
        raise MungyeongContractError("invalid current/total page marker")
    return current, total


def _canonical_list_form(soup: BeautifulSoup, expected_page: int) -> Any:
    form = soup.select_one("form#listForm")
    if form is None or _clean(form.get("method")).lower() != "post":
        raise MungyeongContractError("missing official POST list form")
    action = urlparse(_clean(form.get("action")))
    if (
        action.path != MUNGYEONG_YOUTH_LIST_PATH
        or parse_qs(action.query, keep_blank_values=True)
        != {"mId": [MUNGYEONG_YOUTH_MENU_ID]}
        or action.fragment
    ):
        raise MungyeongContractError("list form action changed")
    page_input = form.select_one('input[name="currentPageNo"]')
    if _clean(page_input.get("value") if page_input else "") != str(expected_page):
        raise MungyeongContractError("list form currentPageNo does not match page marker")
    keyword_type = form.select_one('[name="keywordType"]')
    keyword = form.select_one('[name="keyword"]')
    if keyword_type is None or keyword is None:
        raise MungyeongContractError("list search controls are missing")
    return form


def _lecture_table(soup: BeautifulSoup) -> Any:
    matches: list[Any] = []
    for table in soup.select("table"):
        headers = tuple(
            _clean(cell.get_text(" ", strip=True)) for cell in table.select("thead th")
        )
        if headers == _LIST_HEADERS:
            matches.append(table)
    if len(matches) != 1:
        raise MungyeongContractError(
            f"expected one official lecture table, got {len(matches)}"
        )
    return matches[0]


def _no_data_row(row: Any) -> bool:
    cells = row.select("td")
    return bool(
        len(cells) == 1
        and _clean(cells[0].get("colspan")) == "6"
        and _clean(cells[0].get_text(" ", strip=True)) in _NO_DATA_TEXTS
    )


def _list_contract(
    soup: BeautifulSoup,
    expected_page: int,
) -> tuple[Any, list[Any], bool, int]:
    current, total = _page_numbers(soup)
    if current != expected_page:
        raise MungyeongContractError(
            f"requested page {expected_page} returned page {current}"
        )
    form = _canonical_list_form(soup, expected_page)
    table = _lecture_table(soup)
    rows = table.select("tbody tr")
    no_data = False
    if rows and all(_no_data_row(row) for row in rows):
        no_data = True
        rows = []
    elif any(_no_data_row(row) for row in rows):
        raise MungyeongContractError("no-data row was mixed with lecture rows")
    elif not rows:
        # The live official search endpoint represents a clean empty result as
        # an empty tbody with otherwise intact form/page/table contracts.
        no_data = True
    return form, rows, no_data, total


def _response_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    status = getattr(value, "status_code", 200)
    if status != 200:
        raise MungyeongContractError(f"unexpected HTTP status {status}")
    history = getattr(value, "history", ()) or ()
    if history:
        raise MungyeongContractError("redirected response is not canonical")
    if hasattr(value, "raise_for_status"):
        value.raise_for_status()
    if not getattr(value, "encoding", None) or str(value.encoding).lower() == "iso-8859-1":
        apparent = getattr(value, "apparent_encoding", None)
        if apparent:
            value.encoding = apparent
    body = getattr(value, "text", "")
    if not _clean(body):
        raise MungyeongContractError("empty HTML response")
    return BeautifulSoup(body, "lxml")


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    return current.get(url, timeout=timeout, allow_redirects=False)


def is_mungyeong_youth_lecture_url(url: Any) -> bool:
    parsed = urlparse(_clean(url))
    return bool(
        parsed.scheme == "https"
        and parsed.netloc == MUNGYEONG_YOUTH_HOST
        and parsed.path == MUNGYEONG_YOUTH_LIST_PATH
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True)
        == {"mId": [MUNGYEONG_YOUTH_MENU_ID]}
    )


def is_mungyeong_youth_lecture_target(target: Any) -> bool:
    if isinstance(target, str):
        return is_mungyeong_youth_lecture_url(target)
    return bool(
        _clean(_target_value(target, "provider")) == MUNGYEONG_YOUTH_PROVIDER
        and is_mungyeong_youth_lecture_url(_target_value(target, "url"))
    )


def canonical_mungyeong_youth_urls(lecture_idx: Any) -> tuple[str, str]:
    idx = _clean(lecture_idx)
    if not re.fullmatch(r"\d{1,12}", idx):
        return "", ""
    detail_query = urlencode({"mId": MUNGYEONG_YOUTH_MENU_ID, "idx": idx})
    enroll_query = urlencode(
        {"mId": MUNGYEONG_YOUTH_MENU_ID, "lectureIdx": idx}
    )
    return (
        f"https://{MUNGYEONG_YOUTH_HOST}{MUNGYEONG_YOUTH_DETAIL_PATH}"
        f"?{detail_query}",
        f"https://{MUNGYEONG_YOUTH_HOST}{MUNGYEONG_YOUTH_ENROLL_PATH}"
        f"?{enroll_query}",
    )


def _row_from_list(target: Any, row: Any) -> dict[str, Any]:
    cells = [_clean(cell.get_text(" ", strip=True)) for cell in row.select("td")]
    if len(cells) != 6:
        raise MungyeongContractError(f"lecture row has {len(cells)} cells")
    link = row.select_one('a[data-button="view"][data-idx]')
    idx = _clean(link.get("data-idx") if link else "")
    title = _clean(link.get_text(" ", strip=True) if link else "")
    raw_url, _enroll_url = canonical_mungyeong_youth_urls(idx)
    if not raw_url or not title or len(_normalized(title)) < 2:
        raise MungyeongContractError("lecture row has invalid identity/title")
    if not cells[0]:
        raise MungyeongContractError(f"lecture {idx} has no source category")

    apply_period = _normalize_range(cells[2])
    apply_start, apply_end = _range_dates(apply_period)
    period, schedule, start, end = _education_period(cells[3])
    if not schedule:
        raise MungyeongContractError(f"lecture {idx} has no list schedule")
    capacity_current, capacity_total, wait_current, wait_total = _capacity(cells[4])
    source_status = cells[5]
    status = _status(source_status)
    extra = _target_extra(target)

    return {
        "provider": MUNGYEONG_YOUTH_PROVIDER,
        "provider_course_id": f"{MUNGYEONG_YOUTH_PROVIDER}:lecture:{idx}",
        "title": title,
        "branch": MUNGYEONG_YOUTH_BRANCH,
        "branch_code": MUNGYEONG_YOUTH_PROVIDER,
        "preserve_branch": True,
        "branch_url": MUNGYEONG_YOUTH_LIST_URL,
        "category": cells[0],
        "raw_url": raw_url,
        "application_url": "",
        "application_type": "",
        "application_method_raw": "온라인 신청",
        "reservation_available": False,
        "status": status,
        "period": period,
        "apply_period": apply_period,
        "schedule_raw": schedule,
        "start_date": start,
        "end_date": end,
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "target": MUNGYEONG_YOUTH_DEFAULT_TARGET,
        "capacity": capacity_total,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_remaining": max(0, capacity_total - capacity_current),
        "waitlist_current": wait_current,
        "waitlist_total": wait_total,
        "venue_name": MUNGYEONG_YOUTH_BRANCH,
        "venue_address": MUNGYEONG_YOUTH_ADDRESS,
        "address": MUNGYEONG_YOUTH_ADDRESS,
        "description": " | ".join(cells),
        "collection_category": _clean(
            extra.get("collection_category") or "공공예약"
        ),
        "domain_category": _clean(extra.get("domain_category") or "교육·강좌"),
        "operator_type": _clean(
            extra.get("operator_type") or "지자체/공공기관"
        ),
        "source_group": _clean(
            extra.get("source_group") or "municipal_reservation"
        ),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "static_html+detail_html",
        "program_type": "강좌",
        "municipality_code": MUNGYEONG_MUNICIPALITY_CODE,
        "municipality_name": MUNGYEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": MUNGYEONG_YOUTH_PARSER,
            "lecture_idx": idx,
            "menu_id": MUNGYEONG_YOUTH_MENU_ID,
            "list_cells": cells,
            "list_source_status": source_status,
        },
    }


def _pairs_from_tables(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row in soup.select("table tr"):
        header = row.find("th")
        cell = row.find("td")
        if header is None or cell is None:
            continue
        key = _clean(header.get_text(" ", strip=True))
        value = _clean(cell.get_text(" ", strip=True))
        if key in pairs and pairs[key] != value:
            raise MungyeongContractError(f"conflicting detail label {key}")
        pairs[key] = value
    return pairs


def _target_from_description(description: str) -> str:
    match = re.search(
        r"((?:초등|중등|고등)\s*\d+\s*학년\s*이상(?:\s*청소년)?)",
        description,
    )
    return _clean(match.group(1)) if match else MUNGYEONG_YOUTH_DEFAULT_TARGET


def _money(description: str, label: str) -> Optional[int]:
    match = re.search(rf"{re.escape(label)}\s*([\d,]+)\s*원", description)
    return int(match.group(1).replace(",", "")) if match else None


def _course_fee(description: str) -> tuple[int | str, str, bool]:
    participant_fee = _money(description, "자부담")
    if participant_fee is not None:
        return participant_fee, "description:자부담", False
    if re.search(
        r"(?:체험비|수강료|교육비)\s*(?:가|는|은)?\s*(?:전액\s*)?무료",
        description,
    ):
        return "무료", "description:explicit_free", False
    return "공식 페이지 미기재", "official_source_unspecified", True


def _detail_into_row(row: dict[str, Any], soup: BeautifulSoup) -> None:
    idx = _clean(row.get("raw_fields", {}).get("lecture_idx"))
    matching_titles = [
        node
        for node in soup.select("h4")
        if _normalized(node.get_text(" ", strip=True)) == _normalized(row.get("title"))
    ]
    if len(matching_titles) != 1:
        raise MungyeongContractError(
            f"lecture {idx} detail title did not match exactly once"
        )

    pairs = _pairs_from_tables(soup)
    missing = sorted(_DETAIL_REQUIRED_LABELS - pairs.keys())
    if missing:
        raise MungyeongContractError(
            f"lecture {idx} detail labels missing: {','.join(missing)}"
        )

    apply_period = _normalize_range(pairs["신청기간"])
    apply_start, apply_end = _range_dates(apply_period)
    period, embedded_schedule, start, end = _education_period(pairs["교육기간"])
    schedule = _clean(pairs["교육시간"])
    if embedded_schedule or not schedule:
        raise MungyeongContractError(
            f"lecture {idx} detail education period/time contract changed"
        )
    if (
        apply_period != row["apply_period"]
        or period != row["period"]
        or schedule != row["schedule_raw"]
    ):
        raise MungyeongContractError(
            f"lecture {idx} list/detail date or schedule mismatch"
        )

    capacity_current, capacity_total, wait_current, wait_total = _capacity(
        pairs["신청현황"]
    )
    if capacity_total != row["capacity_total"] or wait_total != row["waitlist_total"]:
        raise MungyeongContractError(
            f"lecture {idx} list/detail capacity definition mismatch"
        )
    source_status = pairs["진행상태"]
    status = _status(source_status)
    room = _clean(pairs["강의장소"])
    if not room:
        raise MungyeongContractError(f"lecture {idx} detail room is empty")

    if status in {"OPEN", "WAITING"}:
        button = soup.select_one('a[data-button="write"][data-idx]')
        if _clean(button.get("data-idx") if button else "") != idx:
            raise MungyeongContractError(
                f"lecture {idx} bookable detail has no matching application control"
            )
        script_text = "\n".join(
            node.get_text(" ", strip=True) for node in soup.select("script")
        )
        if (
            "/reservation/youthCulture/enroll/" not in script_text
            or "lectureIdx" not in script_text
        ):
            raise MungyeongContractError(
                f"lecture {idx} application route contract changed"
            )

    description = _clean(pairs["교육내용 소개"])
    fee, fee_source, fee_source_omission = _course_fee(description)
    _detail_url, enroll_url = canonical_mungyeong_youth_urls(idx)
    reservation_available = status in {"OPEN", "WAITING"}
    row.update(
        {
            "status": status,
            "period": period,
            "apply_period": apply_period,
            "schedule_raw": schedule,
            "start_date": start,
            "end_date": end,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "room": room,
            "description": description,
            "target": _target_from_description(description),
            "material_fee": _money(description, "재료비"),
            "fee": fee,
            "capacity": capacity_total,
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "capacity_remaining": max(0, capacity_total - capacity_current),
            "waitlist_current": wait_current,
            "waitlist_total": wait_total,
            "reservation_available": reservation_available,
            "application_url": enroll_url if reservation_available else "",
            "application_type": (
                "ONLINE_RESERVATION" if reservation_available else ""
            ),
        }
    )
    row["raw_fields"].update(
        {
            "detail_pairs": pairs,
            "detail_source_status": source_status,
            "fee_source": fee_source,
        }
    )
    if fee_source_omission:
        row["raw_fields"]["fee_source_omission"] = True


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("room")),
        _normalized(row.get("category")),
        _normalized(row.get("description")),
    )


def _failure(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "parser": MUNGYEONG_YOUTH_PARSER,
        "pages": 0,
        "data_pages": 0,
        "total_pages": 0,
        "list_requests": 0,
        "request_count": 0,
        "source_rows": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "duplicate_count": 0,
        "duplicate_url_count": 0,
        "semantic_candidate_duplicate_count": 0,
        "expired_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "detail_candidates": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "empty_sentinel_verified": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": reason,
        **extra,
    }


def collect_mungyeong_youth_culture_lectures(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 50,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Mungyeong youth lecture snapshot."""

    if not is_mungyeong_youth_lecture_target(target):
        return [], MUNGYEONG_YOUTH_PARSER, _failure(
            "target provider/url does not match the canonical Mungyeong youth anchor"
        )
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], MUNGYEONG_YOUTH_PARSER, _failure(
                "managed fetcher and session_factory injection are required"
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory

    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    if allowed_pages < 1:
        return [], MUNGYEONG_YOUTH_PARSER, _failure(
            "max_pages cap does not allow the first list request",
            source_cap_reached=True,
        )

    cutoff = _today(today)
    errors: list[str] = []
    candidates: list[dict[str, Any]] = []
    pages = 0
    data_pages = 0
    total_pages = 0
    list_requests = 0
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    invalid_count = 0
    duplicate_count = 0
    duplicate_url_count = 0
    source_cap_reached = False
    empty_sentinel_verified = False
    no_data_pages = 0
    session_obj = session_factory()

    try:
        try:
            first = _response_soup(
                fetcher(session_obj, MUNGYEONG_YOUTH_LIST_URL, timeout)
            )
            list_requests += 1
            pages += 1
            data_pages += 1
            _form, first_rows, first_empty, total_pages = _list_contract(first, 1)
        except Exception as exc:
            errors.append(f"page 1: {type(exc).__name__}: {_clean(exc)}")
            first_rows = []
            first_empty = False

        required_list_requests = total_pages + 1 if total_pages else 0
        if not errors and required_list_requests > allowed_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of "
                f"{required_list_requests} required list/sentinel requests"
            )

        page_payloads: list[tuple[int, list[Any], bool]] = []
        if not errors:
            page_payloads.append((1, first_rows, first_empty))
            for page in range(2, total_pages + 1):
                try:
                    response = session_obj.post(
                        MUNGYEONG_YOUTH_LIST_URL,
                        data={
                            "currentPageNo": str(page),
                            "keywordType": "0",
                            "keyword": "",
                        },
                        headers={
                            "Referer": MUNGYEONG_YOUTH_LIST_URL,
                            "Origin": f"https://{MUNGYEONG_YOUTH_HOST}",
                        },
                        timeout=timeout,
                        allow_redirects=False,
                    )
                    list_requests += 1
                    pages += 1
                    data_pages += 1
                    soup = _response_soup(response)
                    _form, rows, no_data, declared_total = _list_contract(
                        soup, page
                    )
                    if declared_total != total_pages:
                        raise MungyeongContractError(
                            f"declared total changed from {total_pages} to {declared_total}"
                        )
                    page_payloads.append((page, rows, no_data))
                except Exception as exc:
                    errors.append(
                        f"page {page}: {type(exc).__name__}: {_clean(exc)}"
                    )
                    break

        if not errors:
            for page, source_rows, no_data in page_payloads:
                if no_data:
                    no_data_pages += 1
                    if not (page == 1 and total_pages == 1 and not source_rows):
                        errors.append(
                            f"page {page}: empty page is inconsistent with declared pagination"
                        )
                    continue
                for source_row in source_rows:
                    try:
                        candidates.append(_row_from_list(target, source_row))
                    except Exception as exc:
                        invalid_count += 1
                        errors.append(
                            f"page {page}: malformed lecture row: "
                            f"{type(exc).__name__}: {_clean(exc)}"
                        )

        identities = [_clean(row.get("provider_course_id")) for row in candidates]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate provider course identities")
        urls = [_clean(row.get("raw_url")) for row in candidates]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate canonical detail URLs")

        if not errors:
            try:
                response = session_obj.post(
                    MUNGYEONG_YOUTH_LIST_URL,
                    data={
                        "currentPageNo": "1",
                        "keywordType": "1",
                        "keyword": MUNGYEONG_YOUTH_EMPTY_SENTINEL_TERM,
                    },
                    headers={
                        "Referer": MUNGYEONG_YOUTH_LIST_URL,
                        "Origin": f"https://{MUNGYEONG_YOUTH_HOST}",
                    },
                    timeout=timeout,
                    allow_redirects=False,
                )
                list_requests += 1
                pages += 1
                sentinel = _response_soup(response)
                sentinel_form, sentinel_rows, sentinel_empty, sentinel_total = (
                    _list_contract(sentinel, 1)
                )
                keyword = sentinel_form.select_one('[name="keyword"]')
                selected_type = sentinel_form.select_one(
                    '[name="keywordType"] option[selected]'
                )
                if (
                    _clean(keyword.get("value") if keyword else "")
                    != MUNGYEONG_YOUTH_EMPTY_SENTINEL_TERM
                    or _clean(selected_type.get("value") if selected_type else "")
                    != "1"
                ):
                    raise MungyeongContractError(
                        "empty search sentinel was not reflected by the source"
                    )
                if sentinel_total != 1 or sentinel_rows or not sentinel_empty:
                    raise MungyeongContractError(
                        "official empty search sentinel returned lecture rows"
                    )
                empty_sentinel_verified = True
            except Exception as exc:
                errors.append(
                    f"empty sentinel: {type(exc).__name__}: {_clean(exc)}"
                )

        current_rows: list[dict[str, Any]] = []
        expired_count = 0
        for row in candidates:
            end = row.get("end_date")
            if not isinstance(end, date):
                errors.append(
                    f"{_clean(row.get('provider_course_id'))}: invalid end date"
                )
            elif end < cutoff:
                expired_count += 1
            else:
                current_rows.append(row)

        if len(current_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_rows)} required current/future details"
            )

        if not errors:
            for row in current_rows:
                detail_attempts += 1
                try:
                    soup = _response_soup(
                        fetcher(session_obj, _clean(row.get("raw_url")), timeout)
                    )
                    _detail_into_row(row, soup)
                    if row["end_date"] < cutoff:
                        raise MungyeongContractError(
                            "detail unexpectedly changed course to expired"
                        )
                    detail_pages += 1
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: detail "
                        f"{type(exc).__name__}: {_clean(exc)}"
                    )

        semantic_counts = Counter(_semantic_signature(row) for row in current_rows)
        semantic_candidate_duplicate_count = sum(
            count - 1 for count in semantic_counts.values() if count > 1
        )
        if semantic_candidate_duplicate_count:
            errors.append(
                f"{semantic_candidate_duplicate_count} semantic duplicate lecture rows"
            )

        result: list[dict[str, Any]] = []
        if not errors:
            result = list(current_rows)
            if dedupe_rows is not None:
                deduped = list(dedupe_rows(result))
                if len(deduped) != len(result):
                    errors.append(
                        "downstream dedupe changed complete canonical snapshot count"
                    )
                    result = []
                else:
                    result = deduped

        snapshot_complete = not errors
        if not snapshot_complete:
            result = []
        no_current_data = bool(snapshot_complete and not result)
        if no_current_data and not candidates:
            no_current_reason = "official catalogue and verified search sentinel are empty"
        elif no_current_data and expired_count == len(candidates):
            no_current_reason = "all complete official catalogue lectures are expired"
        else:
            no_current_reason = ""

        branch_counts = Counter(_clean(row.get("branch")) for row in result)
        status_counts = Counter(_clean(row.get("status")) for row in result)
        meta = {
            "parser": MUNGYEONG_YOUTH_PARSER,
            "pages": pages,
            "data_pages": data_pages,
            "total_pages": total_pages,
            "list_requests": list_requests,
            "required_list_requests": total_pages + 1 if total_pages else 0,
            "request_count": list_requests + detail_attempts,
            "source_rows": len(candidates),
            "valid_count": len(candidates),
            "invalid_count": invalid_count,
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_candidate_duplicate_count": (
                semantic_candidate_duplicate_count
            ),
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "detail_candidates": len(current_rows),
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_errors": detail_errors,
            "no_data_pages": no_data_pages,
            "empty_sentinel_verified": empty_sentinel_verified,
            "pagination_detected": total_pages > 1,
            "pagination_complete": bool(
                snapshot_complete
                and data_pages == total_pages
                and empty_sentinel_verified
            ),
            "details_complete": bool(
                snapshot_complete and detail_pages == len(current_rows)
            ),
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "no_current_data": no_current_data,
            "no_current_reason": no_current_reason,
            "configured_collection_error": "; ".join(errors),
        }
        return result, MUNGYEONG_YOUTH_PARSER, meta
    finally:
        close = getattr(session_obj, "close", None)
        if callable(close):
            close()


def collect_from_url(
    target: Any,
    timeout: int = 20,
    max_depth: int = 0,
    max_pages: int = 20,
    detail_limit: int = 50,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    del max_depth
    return collect_mungyeong_youth_culture_lectures(
        target,
        timeout=timeout,
        max_pages=max_pages,
        detail_limit=detail_limit,
        **kwargs,
    )


crawl_mungyeong_youth_culture_lectures = (
    collect_mungyeong_youth_culture_lectures
)
collect = collect_mungyeong_youth_culture_lectures
is_target = is_mungyeong_youth_lecture_target


__all__ = [
    "MUNGYEONG_CANDIDATE_ID",
    "MUNGYEONG_MUNICIPALITY_CODE",
    "MUNGYEONG_MUNICIPALITY_NAME",
    "MUNGYEONG_YOUTH_ADDRESS",
    "MUNGYEONG_YOUTH_BRANCH",
    "MUNGYEONG_YOUTH_DEFAULT_TARGET",
    "MUNGYEONG_YOUTH_DETAIL_PATH",
    "MUNGYEONG_YOUTH_EMPTY_SENTINEL_TERM",
    "MUNGYEONG_YOUTH_ENROLL_PATH",
    "MUNGYEONG_YOUTH_HOST",
    "MUNGYEONG_YOUTH_LIST_PATH",
    "MUNGYEONG_YOUTH_LIST_URL",
    "MUNGYEONG_YOUTH_MENU_ID",
    "MUNGYEONG_YOUTH_PARSER",
    "MUNGYEONG_YOUTH_PROVIDER",
    "MUNGYEONG_YOUTH_RETIRED_UNPREFIXED_URL",
    "MungyeongContractError",
    "canonical_mungyeong_youth_urls",
    "collect",
    "collect_from_url",
    "collect_mungyeong_youth_culture_lectures",
    "crawl_mungyeong_youth_culture_lectures",
    "is_mungyeong_youth_lecture_target",
    "is_mungyeong_youth_lecture_url",
    "is_target",
]
