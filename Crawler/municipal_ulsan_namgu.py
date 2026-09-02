"""Fail-closed collectors for Ulsan Nam-gu lifelong and library lectures.

The official Nam-gu sites use the same Egov-style, ten-row history catalogue.
Both catalogues declare the source row count and last page, keep stable numeric
``nttId`` detail identities, and return a genuine empty page immediately after
the declared last page.  A snapshot is therefore published only after every
declared page, the overrun sentinel, and every current/future (or undated)
detail have passed their source-specific contracts.

This module is deliberately independent of ``Crawler_MunicipalYaml``.  The
shared router must inject its managed fetcher and SafeSession factory.  It does
not mutate YAML, the generated registry, or persistence state.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


ULSAN_NAMGU_MUNICIPALITY_CODE = "3114000000"
ULSAN_NAMGU_MUNICIPALITY_NAME = "울산광역시 남구"

ULSAN_NAMGU_LIFELONG_CANDIDATE_ID = "MUNI_IR_A4B63464C899"
ULSAN_NAMGU_LIFELONG_PROVIDER = "MUNI_WWW_ULSANNAMGU_GO_KR_A846A0A3"
ULSAN_NAMGU_LIFELONG_LIST_URL = (
    "https://www.ulsannamgu.go.kr/edu/board/edu999Lecture/list.do"
)
ULSAN_NAMGU_LIFELONG_PARSER = (
    "ulsan_namgu_lifelong_complete_declared_pages+overrun_empty_sentinel+"
    "current_and_undated_detail_fail_closed+"
    "identity_bound_name_check_application_control_no_auth_fetch+"
    "allowlisted_labeled_costs_no_free_text"
)

ULSAN_NAMGU_LIBRARY_CANDIDATE_ID = "MUNI_IR_0EA9080F8206"
ULSAN_NAMGU_LIBRARY_PROVIDER = "MUNI_WWW_ULSANNAMGU_GO_KR_254055C7"
ULSAN_NAMGU_LIBRARY_LIST_URL = (
    "https://www.ulsannamgu.go.kr/library/board/libLecture/list.do"
)
ULSAN_NAMGU_LIBRARY_PARSER = (
    "ulsan_namgu_library_complete_declared_pages+overrun_empty_sentinel+"
    "current_and_undated_detail_fail_closed"
)

ULSAN_NAMGU_HOST = "www.ulsannamgu.go.kr"
PAGE_SIZE = 10
EMPTY_SENTINEL_TEXT = "등록(검색)된 게시글이 없습니다."
# Both official boards currently emit colspan=8.  The library table has nine
# headers, so deriving this value from the header count would reject its real
# empty-page marker.
EMPTY_SENTINEL_COLSPAN = "8"


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_LIST_RANGE_RE = re.compile(
    r"^(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})\s*~\s*"
    r"(?:(20\d{2})\s*[./-]\s*)?(\d{1,2})\s*[./-]\s*(\d{1,2})$"
)
_FULL_RANGE_RE = re.compile(
    r"^(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})"
    r"(?:\s+(\d{1,2}):(\d{2}))?\s*~\s*"
    r"(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})"
    r"(?:\s+(\d{1,2}):(\d{2}))?$"
)
_CAPACITY_RE = re.compile(r"^(\d{1,6})\s*/\s*(\d{1,6})\s*명?$")
_STATUS_MAP: Mapping[str, str] = {
    "접수": "OPEN",
    "접수중": "OPEN",
    "신청": "OPEN",
    "신청가능": "OPEN",
    "접수하기": "OPEN",
    "접수전": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "마감": "CLOSED",
    "접수마감": "CLOSED",
    "신청마감": "CLOSED",
}


class UlsanNamguContractError(ValueError):
    """Raised when an official response no longer matches the audited source."""


@dataclass(frozen=True)
class _Source:
    key: str
    provider: str
    candidate_id: str
    list_url: str
    list_path: str
    detail_path: str
    bbs_id: str
    parser: str
    headers: tuple[str, ...]


LIFELONG_SOURCE = _Source(
    key="lifelong",
    provider=ULSAN_NAMGU_LIFELONG_PROVIDER,
    candidate_id=ULSAN_NAMGU_LIFELONG_CANDIDATE_ID,
    list_url=ULSAN_NAMGU_LIFELONG_LIST_URL,
    list_path="/edu/board/edu999Lecture/list.do",
    detail_path="/edu/board/edu999Lecture/view.do",
    bbs_id="edu999Lecture",
    parser=ULSAN_NAMGU_LIFELONG_PARSER,
    headers=(
        "번호",
        "교육명",
        "교육기간",
        "모집기간",
        "모집인원",
        "교육장소",
        "접수방법",
        "상태",
    ),
)

LIBRARY_SOURCE = _Source(
    key="library",
    provider=ULSAN_NAMGU_LIBRARY_PROVIDER,
    candidate_id=ULSAN_NAMGU_LIBRARY_CANDIDATE_ID,
    list_url=ULSAN_NAMGU_LIBRARY_LIST_URL,
    list_path="/library/board/libLecture/list.do",
    detail_path="/library/board/libLecture/view.do",
    bbs_id="libLecture",
    parser=ULSAN_NAMGU_LIBRARY_PARSER,
    headers=(
        "도서관명",
        "번호",
        "교육명",
        "교육기간",
        "모집기간",
        "모집인원",
        "교육장소",
        "대상",
        "상태",
    ),
)

SOURCES = (LIFELONG_SOURCE, LIBRARY_SOURCE)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).lower())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _source_for_url(url: Any) -> Optional[_Source]:
    parsed = urlparse(_clean(url))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != ULSAN_NAMGU_HOST
        or parsed.port is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None
    return next((source for source in SOURCES if parsed.path == source.list_path), None)


def source_for_target(target: Any) -> Optional[_Source]:
    source = _source_for_url(_target_value(target, "url"))
    if source is None or _clean(_target_value(target, "provider")) != source.provider:
        return None
    return source


def is_ulsan_namgu_lifelong_url(url: Any) -> bool:
    return _source_for_url(url) == LIFELONG_SOURCE


def is_ulsan_namgu_library_url(url: Any) -> bool:
    return _source_for_url(url) == LIBRARY_SOURCE


def is_ulsan_namgu_target(target: Any) -> bool:
    return source_for_target(target) is not None


def _page_url(source: _Source, page: int) -> str:
    if page < 1:
        raise UlsanNamguContractError("page must be positive")
    if page == 1:
        return source.list_url
    return f"{source.list_url}?{urlencode({'page': page})}"


def _detail_url(source: _Source, identity: Any) -> str:
    value = _clean(identity)
    if not value.isdigit() or len(value) > 12:
        raise UlsanNamguContractError("nttId must be a bounded numeric identity")
    return f"https://{ULSAN_NAMGU_HOST}{source.detail_path}?{urlencode({'nttId': value})}"


def _application_url(source: _Source, identity: Any) -> str:
    if source != LIBRARY_SOURCE:
        return _detail_url(source, identity)
    value = _clean(identity)
    if not value.isdigit() or len(value) > 12:
        raise UlsanNamguContractError("nttId must be a bounded numeric identity")
    return (
        f"https://{ULSAN_NAMGU_HOST}/library/board/"
        f"libLecture-{value}/write.do?nttDiv="
    )


def _response_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise UlsanNamguContractError(f"unexpected HTTP status {status}")
    if getattr(value, "history", ()):
        raise UlsanNamguContractError("redirected response is not canonical")
    if hasattr(value, "raise_for_status"):
        value.raise_for_status()
    if not getattr(value, "encoding", None) or str(value.encoding).lower() == "iso-8859-1":
        apparent = getattr(value, "apparent_encoding", None)
        if apparent:
            value.encoding = apparent
    body = getattr(value, "text", "")
    if not _clean(body) and getattr(value, "content", b""):
        body = value.content.decode("utf-8")
    if not _clean(body):
        raise UlsanNamguContractError("empty HTML response")
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


def _default_fetcher(session_obj: Any, url: str, timeout: int) -> Any:
    return session_obj.get(url, timeout=timeout, allow_redirects=False)


def _list_range(value: Any, *, allow_empty: bool = False) -> tuple[str, Optional[date], Optional[date]]:
    text = _clean(value)
    if not text and allow_empty:
        return "", None, None
    match = _LIST_RANGE_RE.fullmatch(text)
    if not match:
        raise UlsanNamguContractError(f"invalid list date range: {text or '<empty>'}")
    start_year, start_month, start_day, end_year, end_month, end_day = match.groups()
    start = date(int(start_year), int(start_month), int(start_day))
    year = int(end_year or start_year)
    end = date(year, int(end_month), int(end_day))
    if end < start and not end_year:
        end = date(year + 1, int(end_month), int(end_day))
    if end < start:
        raise UlsanNamguContractError("date range ends before it starts")
    return f"{start.isoformat()} ~ {end.isoformat()}", start, end


def _full_range(value: Any) -> tuple[str, date, date, Optional[datetime], Optional[datetime]]:
    text = _clean(value)
    match = _FULL_RANGE_RE.fullmatch(text)
    if not match:
        raise UlsanNamguContractError(f"invalid detail date range: {text or '<empty>'}")
    values = match.groups()
    start = date(int(values[0]), int(values[1]), int(values[2]))
    end = date(int(values[5]), int(values[6]), int(values[7]))
    if end < start:
        raise UlsanNamguContractError("detail date range ends before it starts")
    start_dt = None
    end_dt = None
    if values[3] is not None or values[4] is not None or values[8] is not None or values[9] is not None:
        if None in (values[3], values[4], values[8], values[9]):
            raise UlsanNamguContractError("detail date range has incomplete time values")
        start_dt = datetime.combine(start, datetime.min.time()).replace(
            hour=int(values[3]), minute=int(values[4]), tzinfo=ZoneInfo("Asia/Seoul")
        )
        end_dt = datetime.combine(end, datetime.min.time()).replace(
            hour=int(values[8]), minute=int(values[9]), tzinfo=ZoneInfo("Asia/Seoul")
        )
        if end_dt < start_dt:
            raise UlsanNamguContractError("detail datetime range ends before it starts")
    return f"{start.isoformat()} ~ {end.isoformat()}", start, end, start_dt, end_dt


def _capacity(value: Any, *, allow_zero_total: bool = False) -> tuple[int, int]:
    match = _CAPACITY_RE.fullmatch(_clean(value).replace(",", ""))
    if not match:
        raise UlsanNamguContractError("invalid current/capacity value")
    current, total = int(match.group(1)), int(match.group(2))
    if total <= 0 and not allow_zero_total:
        raise UlsanNamguContractError("capacity total must be positive")
    return current, total


def _status(value: Any) -> str:
    source = re.sub(r"\s+", "", _clean(value))
    result = _STATUS_MAP.get(source, "")
    if not result:
        raise UlsanNamguContractError(f"unknown source status: {source or '<empty>'}")
    return result


def _form_contract(source: _Source, soup: BeautifulSoup, expected_page: int) -> None:
    matches = []
    for form in soup.select("form"):
        action = urlparse(urljoin(source.list_url, _clean(form.get("action"))))
        bbs = form.select_one('input[name="bbsId"]')
        if action.path == source.list_path and _clean(bbs.get("value") if bbs else "") == source.bbs_id:
            matches.append(form)
    if len(matches) != 1:
        raise UlsanNamguContractError(f"expected one official list form, got {len(matches)}")
    form = matches[0]
    if _clean(form.get("method")).lower() != "post":
        raise UlsanNamguContractError("official list form is no longer POST")
    page_input = form.select_one('input[name="pageIndex"]')
    if _clean(page_input.get("value") if page_input else "") != str(expected_page):
        raise UlsanNamguContractError("list form pageIndex does not match requested page")


def _summary(source: _Source, soup: BeautifulSoup) -> tuple[int, int, int]:
    text = _clean(soup.get_text(" ", strip=True))
    if source == LIFELONG_SOURCE:
        match = re.search(
            r"Total\s*:\s*(\d+)\s*건\s*,?\s*현재\s*:\s*(\d+)page\s*/\s*(\d+)page",
            text,
        )
    else:
        match = re.search(
            r"총게시물\s*:\s*(\d+)\s*/\s*페이지\s*:\s*(\d+)\s*/\s*(\d+)",
            text,
        )
    if not match:
        raise UlsanNamguContractError("missing official total/current-page marker")
    total, current, pages = map(int, match.groups())
    expected_pages = max(1, math.ceil(total / PAGE_SIZE))
    if current < 1 or pages != expected_pages:
        raise UlsanNamguContractError("invalid official total/current-page marker")
    return total, current, pages


def _lecture_table(source: _Source, soup: BeautifulSoup) -> Any:
    matches = []
    for table in soup.select("table"):
        headers = tuple(_clean(cell.get_text(" ", strip=True)) for cell in table.select("tr:first-child th"))
        if headers == source.headers:
            matches.append(table)
    if len(matches) != 1:
        raise UlsanNamguContractError(f"expected one official lecture table, got {len(matches)}")
    return matches[0]


def _page_contract(
    source: _Source,
    soup: BeautifulSoup,
    expected_page: int,
    *,
    overrun: bool = False,
) -> tuple[list[Any], int, int]:
    total, current, pages = _summary(source, soup)
    if current != expected_page:
        raise UlsanNamguContractError(
            f"requested page {expected_page} returned page {current}"
        )
    _form_contract(source, soup, expected_page)
    table = _lecture_table(source, soup)
    rows = [row for row in table.select("tr")[1:] if row.find_all("td", recursive=False)]
    if overrun:
        if expected_page != pages + 1 or len(rows) != 1:
            raise UlsanNamguContractError("post-boundary page is not the official empty sentinel")
        cells = rows[0].find_all("td", recursive=False)
        if (
            len(cells) != 1
            or _clean(cells[0].get("colspan")) != EMPTY_SENTINEL_COLSPAN
            or _clean(cells[0].get_text(" ", strip=True)) != EMPTY_SENTINEL_TEXT
        ):
            raise UlsanNamguContractError("post-boundary page is not the official empty sentinel")
        return [], total, pages
    if expected_page > pages:
        raise UlsanNamguContractError("data page exceeds declared last page")
    expected_rows = min(PAGE_SIZE, max(0, total - (expected_page - 1) * PAGE_SIZE))
    if len(rows) != expected_rows:
        raise UlsanNamguContractError(
            f"page {expected_page} has {len(rows)} rows, expected {expected_rows}"
        )
    return rows, total, pages


def _canonical_detail_identity(source: _Source, href: Any) -> tuple[str, str]:
    parsed = urlparse(urljoin(source.list_url, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != ULSAN_NAMGU_HOST
        or parsed.port is not None
        or parsed.path != source.detail_path
        or parsed.params
        or parsed.fragment
        or set(query) != {"nttId"}
        or len(query["nttId"]) != 1
        or not query["nttId"][0].isdigit()
        or len(query["nttId"][0]) > 12
    ):
        raise UlsanNamguContractError("lecture detail identity contract changed")
    identity = query["nttId"][0]
    return identity, _detail_url(source, identity)


def _img_alts(cell: Any) -> list[str]:
    return [_clean(node.get("alt")) for node in cell.select("img[alt]") if _clean(node.get("alt"))]


def _base_row(
    target: Any,
    source: _Source,
    *,
    number: str,
    identity: str,
    title: str,
    raw_url: str,
    period: str,
    start: Optional[date],
    end: Optional[date],
    apply_period: str,
    apply_start: date,
    apply_end: date,
    capacity_current: int,
    capacity_total: int,
    room: str,
    source_status: str,
    status: str,
) -> dict[str, Any]:
    extra = _target_value(target, "extra")
    extra = extra if isinstance(extra, Mapping) else {}
    return {
        "provider": source.provider,
        "provider_course_id": f"{source.provider}:lecture:{identity}",
        "title": title,
        "branch": ULSAN_NAMGU_MUNICIPALITY_NAME,
        "branch_code": source.provider,
        "preserve_branch": True,
        "raw_url": raw_url,
        "application_url": "",
        "application_type": "",
        "reservation_available": False,
        "status": status,
        "period": period,
        "apply_period": apply_period,
        "start_date": start,
        "end_date": end,
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "capacity": capacity_total,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_remaining": max(0, capacity_total - capacity_current),
        "room": room,
        "venue_name": room,
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_category": _clean(extra.get("collection_category")) or "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "municipality_code": ULSAN_NAMGU_MUNICIPALITY_CODE,
        "municipality_name": ULSAN_NAMGU_MUNICIPALITY_NAME,
        "raw_fields": {
            "candidate_id": source.candidate_id,
            "source_kind": source.key,
            "source_number": number,
            "ntt_id": identity,
            "list_source_status": source_status,
            "period_missing": start is None,
        },
    }


def _row_from_list(
    target: Any,
    source: _Source,
    row: Any,
    cutoff: date,
) -> dict[str, Any]:
    cells = row.find_all(["th", "td"], recursive=False)
    if len(cells) != len(source.headers):
        raise UlsanNamguContractError("lecture row cell count changed")
    values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
    if source == LIFELONG_SOURCE:
        number, title, period_raw, apply_raw, capacity_raw, room = values[:6]
        methods = _img_alts(cells[6])
        statuses = _img_alts(cells[7])
        branch_short = "남구"
        target_text = ""
        title_cell = cells[1]
    else:
        branch_short, number, title, period_raw, apply_raw, capacity_raw, room, target_text = values[:8]
        methods = ["온라인"]
        statuses = _img_alts(cells[8])
        title_cell = cells[2]
    if not number.isdigit() or not title or not room:
        raise UlsanNamguContractError("lecture row is missing number/title/room")
    if len(statuses) != 1:
        raise UlsanNamguContractError("lecture row must expose exactly one image-alt status")
    anchor = title_cell.select_one("a[href]")
    if anchor is None or _normalized(anchor.get_text(" ", strip=True)) != _normalized(title):
        raise UlsanNamguContractError("lecture title/detail link contract changed")
    identity, raw_url = _canonical_detail_identity(source, anchor.get("href"))
    period, start, end = _list_range(period_raw, allow_empty=True)
    apply_period, apply_start, apply_end = _list_range(apply_raw)
    source_status = statuses[0]
    status = _status(source_status)
    historical_capacity_anomaly = bool(
        end is not None and end < cutoff and status == "CLOSED"
    )
    capacity_current, capacity_total = _capacity(
        capacity_raw,
        allow_zero_total=historical_capacity_anomaly,
    )
    result = _base_row(
        target,
        source,
        number=number,
        identity=identity,
        title=title,
        raw_url=raw_url,
        period=period,
        start=start,
        end=end,
        apply_period=apply_period,
        apply_start=apply_start,
        apply_end=apply_end,
        capacity_current=capacity_current,
        capacity_total=capacity_total,
        room=room,
        source_status=source_status,
        status=status,
    )
    result["target"] = target_text
    result["application_methods"] = methods
    result["raw_fields"].update(
        {
            "list_branch": branch_short,
            "list_period_raw": period_raw,
            "list_apply_period_raw": apply_raw,
            "list_capacity_raw": capacity_raw,
            "historical_capacity_anomaly": bool(
                historical_capacity_anomaly and capacity_total <= 0
            ),
            "list_application_methods": methods,
        }
    )
    return result


def _detail_pairs(source: _Source, soup: BeautifulSoup) -> dict[str, str]:
    required = (
        {
            "학습방명",
            "강좌명",
            "교육기관",
            "교육대상",
            "교육방법",
            "강사",
            "접수방법",
            "접수 / 모집인원",
            "모집기간",
            "접수여부",
            "교육기간",
            "교육요일",
            "교육시간",
            "교육장소",
            "문의전화",
            "교육내용",
        }
        if source == LIFELONG_SOURCE
        else {
            "강좌명",
            "도서관",
            "강사명",
            "대상",
            "신청인원 / 정원",
            "대기인원 / 정원",
            "강의기간",
            "강의시간",
            "교육장소",
            "재료비",
            "교육내용",
        }
    )
    matches: list[dict[str, str]] = []
    for table in soup.select("table"):
        pairs: dict[str, str] = {}
        duplicate = False
        for th in table.select("th"):
            label = _clean(th.get_text(" ", strip=True))
            value_node = th.find_next_sibling("td")
            if not label or value_node is None:
                continue
            if label in pairs:
                duplicate = True
                break
            pairs[label] = _clean(value_node.get_text(" ", strip=True))
        if not duplicate and required.issubset(pairs):
            matches.append(pairs)
    if len(matches) != 1:
        raise UlsanNamguContractError(f"expected one official detail table, got {len(matches)}")
    return matches[0]


def _lifelong_detail_status(
    soup: BeautifulSoup,
    identity: str,
) -> tuple[str, str]:
    controls = []
    for node in soup.select("a[onclick], button[onclick]"):
        text = _clean(node.get_text(" ", strip=True))
        if text in _STATUS_MAP:
            controls.append((node, text))
    if len(controls) != 1:
        raise UlsanNamguContractError("lifelong detail must expose one status/application control")
    node, text = controls[0]
    result = _status(text)
    onclick = _clean(node.get("onclick"))
    application_contract = "non_open_control"
    if result == "OPEN":
        form = soup.select_one('form[name="frm"] input[name="nttId"]')
        form_bound = form is not None and _clean(form.get("value")) == identity
        script = "\n".join(
            item.get_text(" ", strip=True)
            for item in soup.select("script:not([src])")
        )
        legacy_contract = bool(
            re.fullmatch(r"goLectureCheck\(\)\s*;?", onclick)
            and "selectLectureCheck.do" in script
            and form_bound
        )
        current_contract = bool(
            re.fullmatch(
                rf"goNameCheck\(\s*['\"]edu999Lecture-{re.escape(identity)}['\"]\s*,"
                r"\s*['\"]edu['\"]\s*\)\s*;\s*return\s+false\s*;?",
                onclick,
            )
            and form_bound
            and sum(
                urlparse(
                    urljoin(f"https://{ULSAN_NAMGU_HOST}/", _clean(source.get("src")))
                ).geturl()
                == f"https://{ULSAN_NAMGU_HOST}/edu/js/EgovCommon.js"
                for source in soup.select("script[src]")
            )
            == 1
        )
        if not (legacy_contract or current_contract):
            raise UlsanNamguContractError("open lifelong detail application contract changed")
        application_contract = (
            "identity_bound_name_check_gate"
            if current_contract
            else "legacy_identity_bound_lecture_check"
        )
    elif result == "CLOSED" and "return false" not in onclick:
        raise UlsanNamguContractError("closed lifelong detail control contract changed")
    return result, application_contract


def _library_application(source: _Source, soup: BeautifulSoup, identity: str) -> str:
    expected_path = f"/library/board/libLecture-{identity}/write.do"
    matches = []
    for node in soup.select("a[onclick]"):
        if _clean(node.get_text(" ", strip=True)) != "접수하기":
            continue
        onclick = _clean(node.get("onclick"))
        if expected_path in onclick and "nttDiv=" in onclick:
            matches.append(node)
    if len(matches) > 1:
        raise UlsanNamguContractError("library detail exposes duplicate application controls")
    return _application_url(source, identity) if matches else ""


def _methods(value: Any) -> list[str]:
    return sorted({_clean(item) for item in re.split(r"[,/]", _clean(value)) if _clean(item)})


_COST_VALUE_PATTERN = (
    r"(?:무료|없음|(?:\d+\s*회\s*(?:[/·]\s*)?)?"
    r"(?:\d+\s*만\s*원|[\d,]+\s*원))"
)
_TUITION_LABEL_PATTERNS = (("수강료", r"수\s*강\s*료"),)
_MATERIAL_LABEL_PATTERNS = (
    ("교재·재료비", r"교재\s*[,·/]\s*재료비"),
    ("교재·구독료", r"교재\s*[,·/]\s*구독료"),
    ("교재비", r"교재비"),
    ("재료비", r"(?<![가-힣,·/])재료비"),
)


def _labeled_cost(
    value: Any, label_patterns: tuple[tuple[str, str], ...]
) -> tuple[str, str, str]:
    text = _clean(value)
    for label, label_pattern in label_patterns:
        matches = [
            (label, _clean(match.group("value")), _clean(match.group(0)))
            for match in re.finditer(
                rf"(?P<label>{label_pattern})\s*[:：]\s*"
                rf"(?P<value>{_COST_VALUE_PATTERN})",
                text,
            )
        ]
        unique = list(dict.fromkeys(matches))
        if len(unique) > 1:
            raise UlsanNamguContractError(
                f"conflicting {label} values in education content"
            )
        if unique:
            return unique[0]
    return "", "", ""


def _lifelong_costs(value: Any) -> tuple[str, str, dict[str, str]]:
    tuition_label, tuition_value, tuition_evidence = _labeled_cost(
        value, _TUITION_LABEL_PATTERNS
    )
    material_label, material_value, material_evidence = _labeled_cost(
        value, _MATERIAL_LABEL_PATTERNS
    )
    fee = (
        f"{tuition_label} {tuition_value}"
        if tuition_label
        else "요금 별도 안내"
    )
    material_fee = (
        f"{material_label} {material_value}" if material_label else ""
    )
    return fee, material_fee, {
        "fee_source": (
            "education_content_labeled_tuition"
            if tuition_label
            else "official_detail_omits_tuition"
        ),
        "fee_evidence": tuition_evidence,
        "material_fee_source": (
            "education_content_labeled_material_cost"
            if material_label
            else "official_detail_omits_labeled_material_cost"
        ),
        "material_fee_evidence": material_evidence,
    }


def _lifelong_detail_evidence(pairs: Mapping[str, str]) -> dict[str, str]:
    allowed = (
        "학습방명",
        "강좌명",
        "교육기관",
        "교육대상",
        "교육방법",
        "접수방법",
        "접수 / 모집인원",
        "모집기간",
        "접수여부",
        "교육기간",
        "교육요일",
        "교육시간",
        "교육장소",
    )
    return {key: _clean(pairs.get(key)) for key in allowed}


def _enrich_lifelong(row: dict[str, Any], soup: BeautifulSoup, cutoff: date) -> bool:
    pairs = _detail_pairs(LIFELONG_SOURCE, soup)
    identity = row["raw_fields"]["ntt_id"]
    if _normalized(pairs["강좌명"]) != _normalized(row["title"]):
        raise UlsanNamguContractError(f"lecture {identity} detail title mismatch")
    detail_status, application_contract = _lifelong_detail_status(soup, identity)
    period_value = pairs["교육기간"]
    undated = period_value in {"", "~"}
    if undated:
        if not row["raw_fields"]["period_missing"]:
            raise UlsanNamguContractError(f"lecture {identity} detail lost its education period")
        apply_period, apply_start, apply_end, apply_start_dt, apply_end_dt = _full_range(pairs["모집기간"])
        if (
            detail_status != "CLOSED"
            or row["status"] != "CLOSED"
            or apply_end >= cutoff
        ):
            raise UlsanNamguContractError(f"lecture {identity} undated row is not provably expired")
        row["raw_fields"].update(
            {
                "detail_fields": _lifelong_detail_evidence(pairs),
                "detail_source_status": "마감",
                "application_control_contract": application_contract,
                "authentication_gate_fetched": False,
                "undated_expired_evidence": True,
                "detail_apply_start_at": apply_start_dt.isoformat() if apply_start_dt else "",
                "detail_apply_end_at": apply_end_dt.isoformat() if apply_end_dt else "",
            }
        )
        return False

    period, start, end, _start_dt, _end_dt = _full_range(period_value)
    apply_period, apply_start, apply_end, apply_start_dt, apply_end_dt = _full_range(pairs["모집기간"])
    if row["end_date"] is not None and (start != row["start_date"] or end != row["end_date"]):
        raise UlsanNamguContractError(f"lecture {identity} list/detail education period mismatch")
    if apply_start != row["apply_start_date"] or apply_end != row["apply_end_date"]:
        raise UlsanNamguContractError(f"lecture {identity} list/detail application period mismatch")
    if _clean(pairs["교육장소"]) != _clean(row["room"]):
        raise UlsanNamguContractError(f"lecture {identity} list/detail room mismatch")
    current, total = _capacity(pairs["접수 / 모집인원"])
    if total != row["capacity_total"]:
        raise UlsanNamguContractError(f"lecture {identity} list/detail capacity mismatch")
    list_methods = sorted(row.get("application_methods") or [])
    detail_methods = _methods(pairs["접수방법"])
    if list_methods != detail_methods:
        raise UlsanNamguContractError(f"lecture {identity} list/detail application methods mismatch")
    if detail_status != row["status"]:
        raise UlsanNamguContractError(f"lecture {identity} list/detail status changed during crawl")
    institution = _clean(pairs["교육기관"])
    if not institution:
        raise UlsanNamguContractError(f"lecture {identity} detail institution is empty")
    branch = f"{ULSAN_NAMGU_MUNICIPALITY_NAME} · {institution}"
    schedule = _clean(f"{pairs['교육요일']} {pairs['교육시간']}")
    fee, material_fee, cost_evidence = _lifelong_costs(pairs["교육내용"])
    application_url = _application_url(LIFELONG_SOURCE, identity) if detail_status == "OPEN" else ""
    row.update(
        {
            "branch": branch,
            "branch_code": f"{LIFELONG_SOURCE.provider}:{_normalized(institution)}",
            "period": period,
            "apply_period": apply_period,
            "start_date": start,
            "end_date": end,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "status": detail_status,
            "target": pairs["교육대상"],
            "category": pairs["학습방명"],
            "instructor": pairs["강사"],
            "schedule_raw": schedule,
            "fee": fee,
            "material_fee": material_fee,
            "description": row["title"],
            "phone": pairs["문의전화"],
            "capacity_current": current,
            "capacity_remaining": max(0, total - current),
            "reservation_available": detail_status == "OPEN",
            "application_url": application_url,
            "application_type": "ONLINE_RESERVATION" if application_url else "",
        }
    )
    row["raw_fields"].update(
        {
            "detail_fields": _lifelong_detail_evidence(pairs),
            "detail_source_status": next(
                (_clean(node.get_text(" ", strip=True)) for node in soup.select("a[onclick],button[onclick]") if _clean(node.get_text(" ", strip=True)) in _STATUS_MAP),
                "",
            ),
            "application_control_contract": application_contract,
            "authentication_gate_fetched": False,
            "detail_apply_start_at": apply_start_dt.isoformat() if apply_start_dt else "",
            "detail_apply_end_at": apply_end_dt.isoformat() if apply_end_dt else "",
            **cost_evidence,
        }
    )
    return end >= cutoff


def _library_branch(value: Any) -> str:
    short = _clean(value)
    mapping = {
        "도산": "도산도서관",
        "신복": "신복도서관",
        "옥현": "옥현도서관",
        "월봉": "월봉도서관",
        "철새": "철새마을도서관",
    }
    return mapping.get(short, short if short.endswith("도서관") else f"{short}도서관")


def _enrich_library(row: dict[str, Any], soup: BeautifulSoup, cutoff: date) -> bool:
    pairs = _detail_pairs(LIBRARY_SOURCE, soup)
    identity = row["raw_fields"]["ntt_id"]
    if _normalized(pairs["강좌명"]) != _normalized(row["title"]):
        raise UlsanNamguContractError(f"lecture {identity} detail title mismatch")
    expected_branch = _library_branch(row["raw_fields"]["list_branch"])
    if _clean(pairs["도서관"]) != expected_branch:
        raise UlsanNamguContractError(f"lecture {identity} list/detail library mismatch")
    period_value = pairs["강의기간"]
    application_url = _library_application(LIBRARY_SOURCE, soup, identity)
    if period_value in {"", "~"}:
        if not row["raw_fields"]["period_missing"]:
            raise UlsanNamguContractError(f"lecture {identity} detail lost its education period")
        if row["status"] != "CLOSED" or application_url or row["apply_end_date"] >= cutoff:
            raise UlsanNamguContractError(f"lecture {identity} undated row is not provably expired")
        row["raw_fields"].update(
            {
                "detail_pairs": pairs,
                "undated_expired_evidence": True,
            }
        )
        return False
    period, start, end, _start_dt, _end_dt = _full_range(period_value)
    if row["end_date"] is not None and (start != row["start_date"] or end != row["end_date"]):
        raise UlsanNamguContractError(f"lecture {identity} list/detail education period mismatch")
    if _clean(pairs["교육장소"]) != _clean(row["room"]):
        raise UlsanNamguContractError(f"lecture {identity} list/detail room mismatch")
    if _clean(pairs["대상"]) != _clean(row["target"]):
        raise UlsanNamguContractError(f"lecture {identity} list/detail target mismatch")
    current, total = _capacity(pairs["신청인원 / 정원"])
    wait_current, wait_total = _capacity(
        pairs["대기인원 / 정원"],
        allow_zero_total=True,
    )
    if wait_total == 0 and wait_current != 0:
        raise UlsanNamguContractError("capacity total must be positive")
    if total != row["capacity_total"]:
        raise UlsanNamguContractError(f"lecture {identity} list/detail capacity mismatch")
    if row["status"] == "OPEN" and not application_url:
        raise UlsanNamguContractError(f"lecture {identity} open row has no application control")
    if row["status"] != "OPEN" and application_url:
        raise UlsanNamguContractError(f"lecture {identity} non-open row exposes application control")
    branch = f"{ULSAN_NAMGU_MUNICIPALITY_NAME} · {expected_branch}"
    row.update(
        {
            "branch": branch,
            "branch_code": f"{LIBRARY_SOURCE.provider}:{_normalized(expected_branch)}",
            "period": period,
            "start_date": start,
            "end_date": end,
            "category": "도서관강좌",
            "instructor": pairs["강사명"],
            "schedule_raw": pairs["강의시간"],
            "description": pairs["교육내용"],
            "material_note": pairs["재료비"],
            "capacity_current": current,
            "capacity_remaining": max(0, total - current),
            "waitlist_current": wait_current,
            "waitlist_total": wait_total,
            "reservation_available": bool(application_url),
            "application_url": application_url,
            "application_type": "ONLINE_RESERVATION" if application_url else "",
        }
    )
    row["raw_fields"].update({"detail_pairs": pairs})
    return end >= cutoff


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("branch")),
        _normalized(row.get("room")),
        _normalized(row.get("target")),
    )


def _failure(source: Optional[_Source], reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "parser": source.parser if source else "ulsan_namgu_exact_source_router",
        "pages": 0,
        "data_pages": 0,
        "total_pages": 0,
        "required_list_requests": 0,
        "list_requests": 0,
        "request_count": 0,
        "source_rows": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "duplicate_count": 0,
        "duplicate_url_count": 0,
        "semantic_candidate_duplicate_count": 0,
        "expired_count": 0,
        "undated_count": 0,
        "undated_expired_count": 0,
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


def collect_ulsan_namgu_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 120,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Ulsan Nam-gu source snapshot."""

    source = source_for_target(target)
    if source is None:
        meta = _failure(None, "target provider/url does not match an exact Ulsan Nam-gu source")
        return [], meta["parser"], meta
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], source.parser, _failure(
                source, "managed fetcher and session_factory injection are required"
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory

    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    if allowed_pages < 1:
        return [], source.parser, _failure(
            source,
            "max_pages cap does not allow the first list request",
            source_cap_reached=True,
        )

    cutoff = _today(today)
    errors: list[str] = []
    candidates: list[dict[str, Any]] = []
    pages = 0
    data_pages = 0
    total_pages = 0
    source_total = 0
    list_requests = 0
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    invalid_count = 0
    duplicate_count = 0
    duplicate_url_count = 0
    semantic_candidate_duplicate_count = 0
    source_cap_reached = False
    empty_sentinel_verified = False
    undated_expired_count = 0
    session_obj = session_factory()

    try:
        page_payloads: list[tuple[int, list[Any]]] = []
        try:
            first = _response_soup(fetcher(session_obj, source.list_url, timeout))
            list_requests += 1
            pages += 1
            data_pages += 1
            first_rows, source_total, total_pages = _page_contract(source, first, 1)
            page_payloads.append((1, first_rows))
        except Exception as exc:
            errors.append(f"page 1: {type(exc).__name__}: {_clean(exc)}")

        required_list_requests = total_pages + 1 if total_pages else 0
        if not errors and required_list_requests > allowed_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of {required_list_requests} required list/sentinel requests"
            )

        if not errors:
            for page in range(2, total_pages + 1):
                try:
                    soup = _response_soup(fetcher(session_obj, _page_url(source, page), timeout))
                    list_requests += 1
                    pages += 1
                    data_pages += 1
                    rows, declared_total, declared_pages = _page_contract(source, soup, page)
                    if declared_total != source_total or declared_pages != total_pages:
                        raise UlsanNamguContractError("declared total/page count changed during crawl")
                    page_payloads.append((page, rows))
                except Exception as exc:
                    errors.append(f"page {page}: {type(exc).__name__}: {_clean(exc)}")
                    break

        if not errors:
            try:
                sentinel_page = total_pages + 1
                sentinel = _response_soup(
                    fetcher(session_obj, _page_url(source, sentinel_page), timeout)
                )
                list_requests += 1
                pages += 1
                _rows, declared_total, declared_pages = _page_contract(
                    source, sentinel, sentinel_page, overrun=True
                )
                if declared_total != source_total or declared_pages != total_pages:
                    raise UlsanNamguContractError("empty sentinel total/page count changed")
                empty_sentinel_verified = True
            except Exception as exc:
                errors.append(f"empty sentinel: {type(exc).__name__}: {_clean(exc)}")

        if not errors:
            expected_number = source_total
            for page, source_rows in page_payloads:
                for source_row in source_rows:
                    try:
                        parsed = _row_from_list(target, source, source_row, cutoff)
                        if int(parsed["raw_fields"]["source_number"]) != expected_number:
                            raise UlsanNamguContractError(
                                f"source number sequence expected {expected_number}"
                            )
                        expected_number -= 1
                        candidates.append(parsed)
                    except Exception as exc:
                        invalid_count += 1
                        errors.append(
                            f"page {page}: malformed lecture row: {type(exc).__name__}: {_clean(exc)}"
                        )
            if expected_number != 0 or len(candidates) != source_total:
                errors.append(
                    f"complete source count mismatch: parsed={len(candidates)} declared={source_total}"
                )

        identities = [_clean(row.get("provider_course_id")) for row in candidates]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate provider course identities")
        urls = [_clean(row.get("raw_url")) for row in candidates]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate canonical detail URLs")

        current_rows = [
            row
            for row in candidates
            if isinstance(row.get("end_date"), date) and row["end_date"] >= cutoff
        ]
        expired_count = sum(
            isinstance(row.get("end_date"), date) and row["end_date"] < cutoff
            for row in candidates
        )
        undated_rows = [row for row in candidates if row.get("end_date") is None]
        detail_rows = [*current_rows, *undated_rows]
        if len(detail_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of {len(detail_rows)} required current/undated details"
            )

        if not errors:
            resolved_current: list[dict[str, Any]] = []
            for row in detail_rows:
                detail_attempts += 1
                try:
                    soup = _response_soup(fetcher(session_obj, _clean(row["raw_url"]), timeout))
                    keep = (
                        _enrich_lifelong(row, soup, cutoff)
                        if source == LIFELONG_SOURCE
                        else _enrich_library(row, soup, cutoff)
                    )
                    if keep:
                        resolved_current.append(row)
                    elif row["raw_fields"].get("undated_expired_evidence"):
                        undated_expired_count += 1
                    else:
                        raise UlsanNamguContractError("detail did not resolve row lifecycle")
                    detail_pages += 1
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: detail {type(exc).__name__}: {_clean(exc)}"
                    )
            current_rows = resolved_current

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
                    errors.append("downstream dedupe changed complete canonical snapshot count")
                    result = []
                else:
                    result = deduped

        snapshot_complete = not errors
        if not snapshot_complete:
            result = []
        no_current_data = bool(snapshot_complete and not result)
        no_current_reason = (
            "all complete official catalogue lectures are expired"
            if no_current_data and candidates
            else "official catalogue and verified overrun sentinel are empty"
            if no_current_data
            else ""
        )
        branch_counts = Counter(_clean(row.get("branch")) for row in result)
        status_counts = Counter(_clean(row.get("status")) for row in result)
        meta = {
            "parser": source.parser,
            "candidate_id": source.candidate_id,
            "source_kind": source.key,
            "pages": pages,
            "data_pages": data_pages,
            "total_pages": total_pages,
            "required_list_requests": required_list_requests,
            "list_requests": list_requests,
            "request_count": list_requests + detail_attempts,
            "source_rows": len(candidates),
            "declared_source_rows": source_total,
            "valid_count": len(candidates),
            "invalid_count": invalid_count,
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_candidate_duplicate_count": semantic_candidate_duplicate_count,
            "expired_count": expired_count + undated_expired_count,
            "undated_count": len(undated_rows),
            "undated_expired_count": undated_expired_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "detail_candidates": len(detail_rows),
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_errors": detail_errors,
            "empty_sentinel_verified": empty_sentinel_verified,
            "pagination_detected": total_pages > 1,
            "pagination_complete": bool(
                snapshot_complete
                and data_pages == total_pages
                and empty_sentinel_verified
            ),
            "details_complete": bool(snapshot_complete and detail_pages == len(detail_rows)),
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "reservation_discovery_links": sum(bool(row.get("application_url")) for row in result),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "no_current_data": no_current_data,
            "no_current_reason": no_current_reason,
            "configured_collection_error": "; ".join(errors),
        }
        return result, source.parser, meta
    finally:
        close = getattr(session_obj, "close", None)
        if callable(close):
            close()


def collect_from_url(
    target: Any,
    timeout: int = 20,
    max_depth: int = 0,
    max_pages: int = 120,
    detail_limit: int = 100,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    del max_depth
    return collect_ulsan_namgu_courses(
        target,
        timeout=timeout,
        max_pages=max_pages,
        detail_limit=detail_limit,
        **kwargs,
    )


collect = collect_ulsan_namgu_courses
is_target = is_ulsan_namgu_target


__all__ = [
    "LIBRARY_SOURCE",
    "LIFELONG_SOURCE",
    "ULSAN_NAMGU_HOST",
    "ULSAN_NAMGU_LIBRARY_CANDIDATE_ID",
    "ULSAN_NAMGU_LIBRARY_LIST_URL",
    "ULSAN_NAMGU_LIBRARY_PARSER",
    "ULSAN_NAMGU_LIBRARY_PROVIDER",
    "ULSAN_NAMGU_LIFELONG_CANDIDATE_ID",
    "ULSAN_NAMGU_LIFELONG_LIST_URL",
    "ULSAN_NAMGU_LIFELONG_PARSER",
    "ULSAN_NAMGU_LIFELONG_PROVIDER",
    "ULSAN_NAMGU_MUNICIPALITY_CODE",
    "ULSAN_NAMGU_MUNICIPALITY_NAME",
    "UlsanNamguContractError",
    "collect",
    "collect_from_url",
    "collect_ulsan_namgu_courses",
    "is_target",
    "is_ulsan_namgu_library_url",
    "is_ulsan_namgu_lifelong_url",
    "is_ulsan_namgu_target",
    "source_for_target",
]
