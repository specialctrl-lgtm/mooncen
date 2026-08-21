"""Fail-closed collector for Anseong's official lifelong-learning platform.

The platform has one authoritative, institution-unfiltered personal-course
catalogue and a separate group-course catalogue.  Institution URLs such as
``eduInsttNo=7`` and ``eduInsttNo=69`` are only subsets of the personal
catalogue and must not be scheduled as independent providers: doing so both
misses other institutions and duplicates courses from the canonical list.

Both catalogues are scanned through every advertised page and one empty
post-boundary sentinel.  Historical rows prove pagination completeness, while
only current/future rows are detailed and returned.  Anseong sometimes creates
a second record for an additional application round of the exact same class;
after detail validation those records are collapsed by the real class
signature and the newest application round is retained.

This module deliberately does not import ``Crawler_MunicipalYaml``.  The main
router must inject its managed fetcher and session factory.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


ANSEONG_PROVIDER = "MUNI_WWW_ANSEONG_GO_KR_5751E139"
ANSEONG_HOST = "www.anseong.go.kr"
ANSEONG_MID = "1400000000"
ANSEONG_MUNICIPALITY_CODE = "4155000000"
ANSEONG_MUNICIPALITY_NAME = "경기도 안성시"
ANSEONG_PAGE_SIZE = 10
ANSEONG_MAX_WORKERS = 2
ANSEONG_FETCH_ATTEMPTS = 6
ANSEONG_PARSER = (
    "anseong_all_institutions_personal+group_complete_pages+sentinel+"
    "current_detail+application_round_dedupe"
)

ANSEONG_PERSONAL_LIST_PATH = (
    "/edu/portal/edu/eduLctre/selectEduLctreWebList.do"
)
ANSEONG_PERSONAL_DETAIL_PATH = (
    "/edu/portal/edu/eduLctre/eduLctreView.do"
)
ANSEONG_GROUP_LIST_PATH = (
    "/edu/portal/edu/eduGroupLctre/selectEduGroupLctreWebList.do"
)
ANSEONG_GROUP_DETAIL_PATH = (
    "/edu/portal/edu/eduGroupLctre/eduGroupLctreView.do"
)
ANSEONG_URL = (
    "https://www.anseong.go.kr"
    f"{ANSEONG_PERSONAL_LIST_PATH}?mId={ANSEONG_MID}&searchTxt="
)
ANSEONG_GROUP_URL = (
    "https://www.anseong.go.kr"
    f"{ANSEONG_GROUP_LIST_PATH}?mId={ANSEONG_MID}&searchTxt="
)


@dataclass(frozen=True)
class AnseongSource:
    key: str
    list_path: str
    detail_path: str
    identity_param: str
    course_kind: str
    headers: tuple[str, ...]


ANSEONG_PERSONAL_SOURCE = AnseongSource(
    key="personal",
    list_path=ANSEONG_PERSONAL_LIST_PATH,
    detail_path=ANSEONG_PERSONAL_DETAIL_PATH,
    identity_param="eduLctreNo",
    course_kind="개인강좌",
    headers=(
        "번호",
        "지역",
        "교육강좌명",
        "접수기간 /시간",
        "선발방법",
        "신청/모집 (대기자)",
        "교육기간 교육요일/시간",
        "수강료",
        "접수방법",
        "접수상태",
    ),
)
ANSEONG_GROUP_SOURCE = AnseongSource(
    key="group",
    list_path=ANSEONG_GROUP_LIST_PATH,
    detail_path=ANSEONG_GROUP_DETAIL_PATH,
    identity_param="eduGroupLctreNo",
    course_kind="단체강좌",
    headers=(
        "번호",
        "지역",
        "교육강좌명",
        "접수기간 /시간",
        "신청/모집",
        "교육일시",
        "수강료",
        "접수방법",
        "접수상태",
    ),
)
ANSEONG_SOURCES = (ANSEONG_PERSONAL_SOURCE, ANSEONG_GROUP_SOURCE)
_SOURCE_BY_KEY = {source.key: source for source in ANSEONG_SOURCES}

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)")
_COUNTER_RE = re.compile(
    r"전체\s*([\d,]+)\s*건\s*\[\s*(\d+)\s*/\s*(\d+)페이지\s*\]"
)
_BRANCH_RE = re.compile(
    r"^(?P<region>.+?)\s*\((?P<branch>.+)\)\s*$"
)
_LIST_CAPACITY_RE = re.compile(
    r"^(?P<current>-|[\d,]+)\s*/\s*(?P<total>[\d,]+)"
    r"(?:\s+(?P<wait_current>-|[\d,]+)\s*/\s*(?P<wait_total>[\d,]+))?$"
)
_DETAIL_CAPACITY_RE = re.compile(
    r"^신청\s*(?P<current>-|[\d,]+)\s*명\s*/\s*"
    r"모집인원\s*(?P<total>[\d,]+)\s*명"
    r"(?:\s*\(\s*대기신청\s*(?P<wait_current>-|[\d,]+)\s*명\s*/\s*"
    r"대기정원\s*(?P<wait_total>[\d,]+)\s*명\s*\))?$"
)
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "대기접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
}
_EMPTY_MESSAGE = "등록된 정보가 없습니다."


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _single_query_value(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def is_anseong_target(target: Any) -> bool:
    """Return true only for the unfiltered, provider-owned canonical route."""

    parsed = urlparse(_target_url(target))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        _provider(target) == ANSEONG_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == ANSEONG_HOST
        and parsed.port is None
        and parsed.path == ANSEONG_PERSONAL_LIST_PATH
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
        and set(query) == {"mId", "searchTxt"}
        and _single_query_value(query, "mId") == ANSEONG_MID
        and _single_query_value(query, "searchTxt") == ""
    )


is_target = is_anseong_target


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def anseong_list_url(source_key: Any, page: Any = 1) -> str:
    source = _SOURCE_BY_KEY.get(_clean(source_key))
    raw_page = _clean(page)
    if source is None or not _IDENTITY_RE.fullmatch(raw_page) or int(raw_page) < 1:
        return ""
    values: list[tuple[str, Any]] = [("mId", ANSEONG_MID), ("searchTxt", "")]
    if int(raw_page) != 1:
        values.append(("page", int(raw_page)))
    return f"https://{ANSEONG_HOST}{source.list_path}?" + urlencode(values)


def anseong_detail_url(source_key: Any, identity: Any) -> str:
    source = _SOURCE_BY_KEY.get(_clean(source_key))
    raw_identity = _clean(identity)
    if source is None or not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{ANSEONG_HOST}{source.detail_path}?" + urlencode(
        (("mId", ANSEONG_MID), (source.identity_param, raw_identity))
    )


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


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    return current_session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise ValueError("empty HTTP response")
    return BeautifulSoup(content, "lxml")


def _fetch(
    fetcher: Fetcher, current_session: Any, url: str, timeout: int
) -> BeautifulSoup:
    if not url:
        raise ValueError("empty fetch URL")
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _parallel_fetch(
    items: list[tuple[Any, str]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, BeautifulSoup], list[str]]:
    fetched: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def thread_session() -> Any:
        value = getattr(local, "session", None)
        if value is None:
            value = session_factory()
            local.session = value
            with sessions_lock:
                sessions.append(value)
        return value

    def one(item: tuple[Any, str]) -> tuple[Any, Optional[BeautifulSoup], str]:
        key, url = item
        last_error = ""
        for attempt in range(ANSEONG_FETCH_ATTEMPTS):
            try:
                return key, _fetch(fetcher, thread_session(), url, timeout), ""
            except Exception as exc:
                last_error = type(exc).__name__
                if attempt + 1 < ANSEONG_FETCH_ATTEMPTS:
                    time.sleep(min(4.0, 0.25 * (2**attempt)))
        return key, None, f"{key}: fetch {last_error}"

    if not items:
        return fetched, errors
    workers = min(max(1, int(max_workers)), ANSEONG_MAX_WORKERS, len(items))
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="anseong") as pool:
            for key, soup, error in pool.map(one, items):
                if soup is not None:
                    fetched[key] = soup
                if error:
                    errors.append(error)
    finally:
        for current in sessions:
            _close_quietly(current)
    return fetched, errors


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return result


def _period(value: Any, *, single_allowed: bool = False) -> tuple[str, str, str]:
    values = _dates(value)
    if single_allowed and len(values) == 1:
        values.append(values[0])
    if len(values) != 2 or values[1] < values[0]:
        return "", "", ""
    start, end = values
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _capacity_pairs(
    value: Any, pattern: re.Pattern[str]
) -> list[tuple[Optional[int], int]]:
    match = pattern.fullmatch(_clean(value))
    if match is None:
        return []

    def count(name: str) -> Optional[int]:
        raw = match.group(name)
        if raw in (None, "-"):
            return None
        return int(raw.replace(",", ""))

    current = count("current")
    total = count("total")
    if total is None:
        return []
    result = [(current, total)]
    wait_total = count("wait_total")
    if wait_total is not None:
        result.append((count("wait_current"), wait_total))
    return result


def _counter(soup: BeautifulSoup) -> Optional[tuple[int, int, int]]:
    nodes = soup.select("p.page_num")
    if len(nodes) != 1:
        return None
    match = _COUNTER_RE.fullmatch(_clean(nodes[0].get_text(" ", strip=True)))
    if match is None:
        return None
    return (
        int(match.group(1).replace(",", "")),
        int(match.group(2)),
        int(match.group(3)),
    )


def _list_form_valid(soup: BeautifulSoup, source: AnseongSource) -> bool:
    forms = soup.select("form#list")
    if len(forms) != 1:
        return False
    form = forms[0]
    action = urlparse(urljoin(f"https://{ANSEONG_HOST}", _clean(form.get("action"))))
    mid = form.select_one("input[name=mId]")
    page = form.select_one("input[name=page]")
    return (
        action.scheme == "https"
        and action.hostname == ANSEONG_HOST
        and action.path == source.list_path
        and mid is not None
        and _clean(mid.get("value")) == ANSEONG_MID
        and page is not None
        and _IDENTITY_RE.fullmatch(_clean(page.get("value"))) is not None
    )


def _headers(table: Any) -> tuple[str, ...]:
    row = table.select_one("thead tr") if table is not None else None
    if row is None:
        return ()
    return tuple(
        _clean(cell.get_text(" ", strip=True))
        for cell in row.find_all("th", recursive=False)
    )


def _identity_from_href(
    source: AnseongSource, value: Any
) -> tuple[str, str]:
    parsed = urlparse(urljoin(f"https://{ANSEONG_HOST}", _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query_value(query, source.identity_param)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != ANSEONG_HOST
        or parsed.port is not None
        or parsed.path != source.detail_path
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or _single_query_value(query, "mId") != ANSEONG_MID
        or not _IDENTITY_RE.fullmatch(identity)
    ):
        return "", ""
    return identity, anseong_detail_url(source.key, identity)


def _branch(value: Any) -> tuple[str, str]:
    text = _clean(value)
    match = _BRANCH_RE.fullmatch(text)
    if match is None:
        return "", ""
    branch = _clean(match.group("branch"))
    depth = 0
    for character in branch:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                return "", ""
    if depth:
        return "", ""
    return branch, _clean(match.group("region"))


def _branch_code(branch: Any) -> str:
    digest = hashlib.sha1(_normalized(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"ANSEONG_BRANCH_{digest}"


def _schedule(value: Any) -> str:
    text = _clean(value)
    for match in list(_DATE_RE.finditer(text))[:2]:
        text = text.replace(match.group(0), "", 1)
    return _clean(text.strip(" ~/-"))


def _base_row(
    target: Any,
    source: AnseongSource,
    *,
    identity: str,
    title: str,
    branch: str,
    region: str,
    raw_url: str,
    source_status: str,
    method: str,
    start: str,
    end: str,
    period: str,
    apply_start: str,
    apply_end: str,
    apply_period: str,
    schedule: str,
    fee: str,
    page: int,
    values: list[str],
) -> dict[str, Any]:
    return {
        "provider": _provider(target),
        "provider_course_id": f"{_provider(target)}:{source.key}:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "program_type": "교육·강좌",
        "category": "교육·강좌",
        "branch": branch,
        "branch_code": _branch_code(branch),
        "branch_url": ANSEONG_URL,
        "preserve_branch": True,
        "raw_url": raw_url,
        "status": _STATUS_MAP[source_status],
        "period": period,
        "start_date": start,
        "end_date": end,
        "apply_period": apply_period,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule_raw": schedule,
        "fee": fee,
        "reservation_available": False,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "municipality_code": ANSEONG_MUNICIPALITY_CODE,
        "municipality_full_name": ANSEONG_MUNICIPALITY_NAME,
        "collection_type": "complete_pages+sentinel+current_detail",
        "description": _clean(" ".join(values[2:])),
        "raw_fields": {
            "parser": ANSEONG_PARSER,
            "source_kind": source.key,
            "source_course_kind": source.course_kind,
            "identity": identity,
            "source_page": page,
            "source_status": source_status,
            "source_method": method,
            "source_region": region,
            "source_branch": branch,
            "list_cells": values,
        },
    }


def _parse_list_page(
    target: Any,
    source: AnseongSource,
    soup: BeautifulSoup,
    *,
    page: int,
) -> tuple[list[dict[str, Any]], int]:
    tables = soup.select("table.bod_maintain")
    if len(tables) != 1 or _headers(tables[0]) != source.headers:
        return [], 1
    parsed: list[dict[str, Any]] = []
    malformed = 0
    for tr in tables[0].select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) == 1 and _clean(cells[0].get_text(" ", strip=True)) == _EMPTY_MESSAGE:
            continue
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        link = cells[2].select_one(f"a[href*='{source.identity_param}=']") if len(cells) > 2 else None
        identity, raw_url = _identity_from_href(source, link.get("href") if link else "")
        title = _clean(link.get_text(" ", strip=True)) if link else ""
        branch, region = _branch(values[1] if len(values) > 1 else "")
        source_status = values[-1] if values else ""
        method = values[-2] if len(values) > 1 else ""

        if source.key == "personal":
            apply_start, apply_end, apply_period = _period(values[3] if len(values) > 3 else "")
            start, end, period = _period(values[6] if len(values) > 6 else "")
            schedule = _schedule(values[6] if len(values) > 6 else "")
            capacity_pairs = _capacity_pairs(
                values[5] if len(values) > 5 else "", _LIST_CAPACITY_RE
            )
            fee = values[7] if len(values) > 7 else ""
        else:
            apply_start, apply_end, apply_period = _period(values[3] if len(values) > 3 else "")
            start, end, period = _period(
                values[5] if len(values) > 5 else "", single_allowed=True
            )
            schedule = _schedule(values[5] if len(values) > 5 else "")
            capacity_pairs = _capacity_pairs(
                values[4] if len(values) > 4 else "", _LIST_CAPACITY_RE
            )
            fee = values[6] if len(values) > 6 else ""

        if (
            len(values) != len(source.headers)
            or not identity
            or not title
            or not branch
            or not region
            or not raw_url
            or source_status not in _STATUS_MAP
            or method not in {"인터넷", "방문"}
            or not start
            or not apply_start
            or len(capacity_pairs) not in ({1, 2} if source.key == "personal" else {1})
        ):
            malformed += 1
            continue
        row = _base_row(
            target,
            source,
            identity=identity,
            title=title,
            branch=branch,
            region=region,
            raw_url=raw_url,
            source_status=source_status,
            method=method,
            start=start,
            end=end,
            period=period,
            apply_start=apply_start,
            apply_end=apply_end,
            apply_period=apply_period,
            schedule=schedule,
            fee=fee,
            page=page,
            values=values,
        )
        row["capacity_current"] = capacity_pairs[0][0]
        row["capacity_total"] = capacity_pairs[0][1]
        row["capacity"] = capacity_pairs[0][1]
        if len(capacity_pairs) == 2:
            row["waitlist_current"] = capacity_pairs[1][0]
            row["waitlist_total"] = capacity_pairs[1][1]
        parsed.append(row)
    return parsed, malformed


def _detail_root(soup: BeautifulSoup) -> Any:
    roots = [
        root
        for root in soup.select(".learning_wrap.view_wrap .learning_content")
        if root.select_one(".bod_title") is not None
        and root.select_one(".bod_write") is not None
    ]
    return roots[0] if len(roots) == 1 else None


def _detail_pairs(root: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if root is None:
        return result
    for dl in root.select(".bod_write dl"):
        heading = dl.find("dt", recursive=False)
        value = dl.find("dd", recursive=False)
        key = _clean(heading.get_text(" ", strip=True)) if heading else ""
        if key and value is not None and key not in result:
            result[key] = _clean(value.get_text(" ", strip=True))
    return result


def _validate_detail(
    row: dict[str, Any], source: AnseongSource, soup: BeautifulSoup
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    errors: list[str] = []
    root = _detail_root(soup)
    title_node = root.select_one(".bod_title .bod_subject") if root else None
    status_node = root.select_one(".bod_title .bod_state_type") if root else None
    title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
    detail_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
    if title != _clean(row.get("title")):
        errors.append(f"{source.key}:{identity}: detail/list title mismatch")
    if detail_status not in _STATUS_MAP:
        errors.append(f"{source.key}:{identity}: unknown detail status")

    pairs = _detail_pairs(root)
    common_required = {
        "접수기간",
        "접수현황",
        "교육시간",
        "강사명",
        "수강료",
        "문의전화",
    }
    if source.key == "personal":
        required = common_required | {"선발방법", "교육기간", "교육장"}
        period_key = "교육기간"
        venue_key = "교육장"
        single_period = False
    else:
        required = common_required | {"교육일시", "단체교육장"}
        period_key = "교육일시"
        venue_key = "단체교육장"
        single_period = True
    missing = sorted(required - set(pairs))
    if missing:
        errors.append(
            f"{source.key}:{identity}: missing detail fields {','.join(missing)}"
        )
        return errors

    detail_apply_start, detail_apply_end, detail_apply_period = _period(
        pairs.get("접수기간")
    )
    detail_start, detail_end, detail_period = _period(
        pairs.get(period_key), single_allowed=single_period
    )
    if (
        detail_apply_start != _clean(row.get("apply_start"))
        or detail_apply_end != _clean(row.get("apply_end"))
        or detail_apply_period != _clean(row.get("apply_period"))
    ):
        errors.append(f"{source.key}:{identity}: application period mismatch")
    if (
        detail_start != _clean(row.get("start_date"))
        or detail_end != _clean(row.get("end_date"))
        or detail_period != _clean(row.get("period"))
    ):
        errors.append(f"{source.key}:{identity}: education period mismatch")

    capacity_pairs = _capacity_pairs(
        pairs.get("접수현황"), _DETAIL_CAPACITY_RE
    )
    if len(capacity_pairs) not in ({1, 2} if source.key == "personal" else {1}):
        errors.append(f"{source.key}:{identity}: malformed detail capacity")
    else:
        detail_current, detail_total = capacity_pairs[0]
        list_current = row.get("capacity_current")
        if detail_total != row.get("capacity_total") or (
            list_current is not None and detail_current != list_current
        ):
            errors.append(f"{source.key}:{identity}: detail/list capacity mismatch")
        elif detail_current is not None:
            # A dash in a visit-only list means the live count is omitted there;
            # the official detail page supplies the actual application count.
            row["capacity_current"] = detail_current

    source_status = _clean(row.get("raw_fields", {}).get("source_status"))
    source_method = _clean(row.get("raw_fields", {}).get("source_method"))
    gate = root.select_one(".bod_title .bod_btn") if root else None
    gate_text = _clean(gate.get_text(" ", strip=True)) if gate else ""
    if (
        source_method == "인터넷"
        and source_status in {"접수중", "대기접수중"}
        and "로그인" not in gate_text
    ):
        errors.append(f"{source.key}:{identity}: missing official login application gate")

    venue = _clean(pairs.get(venue_key))
    instructor = _clean(pairs.get("강사명"))
    description = _clean(
        " ".join(
            value
            for value in (pairs.get("강좌소개"), pairs.get("유의사항"))
            if value
        )
    )
    row.update(
        {
            "schedule_raw": _clean(pairs.get("교육시간")) or row.get("schedule_raw"),
            "room": venue,
            "venue_name": venue,
            "instructor": instructor,
            "phone": _clean(pairs.get("문의전화")),
            "fee": _clean(pairs.get("수강료")) or row.get("fee"),
            "description": description or row.get("description"),
            "reservation_available": (
                source_method == "인터넷"
                and source_status in {"접수중", "대기접수중"}
            ),
        }
    )
    if source_method == "인터넷" and source_status in {
        "접수중",
        "대기접수중",
        "접수예정",
    }:
        row["application_url"] = _clean(row.get("raw_url"))
        row["application_type"] = "ONLINE_RESERVATION"
    elif source_method == "방문":
        row["application_type"] = "IN_PERSON"
        row["raw_fields"]["clear_application_url"] = True
    else:
        row["raw_fields"]["clear_application_url"] = True
    row["raw_fields"].update(
        {
            "detail_status": detail_status,
            "detail_status_differs": detail_status != source_status,
            "detail_pairs": pairs,
            "application_gate": gate_text,
        }
    )
    return errors


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("branch")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("venue_name")),
        _normalized(row.get("instructor")),
    )


def _identity_number(row: Mapping[str, Any]) -> int:
    raw = _clean(row.get("raw_fields", {}).get("identity"))
    return int(raw) if raw.isdigit() else 0


def _collapse_application_rounds(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_semantic_key(row)].append(row)
    collapsed: list[dict[str, Any]] = []
    duplicate_groups = 0
    duplicate_rows = 0
    for values in grouped.values():
        selected = max(
            values,
            key=lambda row: (
                _clean(row.get("apply_start")),
                _clean(row.get("apply_end")),
                _identity_number(row),
            ),
        )
        if len(values) > 1:
            duplicate_groups += 1
            duplicate_rows += len(values) - 1
            selected["raw_fields"]["duplicate_application_round_ids"] = sorted(
                (
                    f"{_clean(row.get('raw_fields', {}).get('source_kind'))}:"
                    f"{_clean(row.get('raw_fields', {}).get('identity'))}"
                    for row in values
                    if row is not selected
                )
            )
        collapsed.append(selected)
    collapsed.sort(
        key=lambda row: (
            _clean(row.get("start_date")),
            _clean(row.get("apply_start")),
            _identity_number(row),
        ),
        reverse=True,
    )
    return collapsed, duplicate_groups, duplicate_rows


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "current_candidate_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_anseong_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 300,
    detail_limit: int = 400,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 2,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete, institution-unfiltered Anseong education snapshot."""

    if not is_anseong_target(target):
        return [], ANSEONG_PARSER, _failure(
            "target does not match the canonical unfiltered Anseong provider route"
        )
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], ANSEONG_PARSER, _failure(
                "managed fetcher and session_factory injection are required"
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory
    assert fetcher is not None
    assert session_factory is not None

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    errors: list[str] = []
    source_cap_reached = False

    first_items = [
        ((source.key, 1), anseong_list_url(source.key, 1))
        for source in ANSEONG_SOURCES
    ]
    page_soups, first_errors = _parallel_fetch(
        first_items,
        fetcher=fetcher,
        session_factory=session_factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(first_errors)

    source_totals: dict[str, int] = {}
    source_pages: dict[str, int] = {}
    required_by_source: dict[str, int] = {}
    for source in ANSEONG_SOURCES:
        soup = page_soups.get((source.key, 1))
        contract = _counter(soup) if soup is not None else None
        if soup is None:
            errors.append(f"{source.key}: missing first catalogue page")
            continue
        if not _list_form_valid(soup, source):
            errors.append(f"{source.key}: malformed first-page list form")
        if contract is None:
            errors.append(f"{source.key}: missing source counter")
            continue
        total, displayed_page, advertised_last = contract
        calculated_last = max(1, math.ceil(total / ANSEONG_PAGE_SIZE))
        if displayed_page != 1 or advertised_last != calculated_last:
            errors.append(f"{source.key}: advertised pagination is inconsistent")
        source_totals[source.key] = total
        source_pages[source.key] = advertised_last
        required_by_source[source.key] = advertised_last + 1

    required_list_requests = sum(required_by_source.values())
    if required_list_requests > allowed_pages:
        source_cap_reached = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of "
            f"{required_list_requests} required list requests"
        )

    if not errors:
        remaining = [
            ((source.key, page), anseong_list_url(source.key, page))
            for source in ANSEONG_SOURCES
            for page in range(2, required_by_source[source.key] + 1)
        ]
        fetched, fetch_errors = _parallel_fetch(
            remaining,
            fetcher=fetcher,
            session_factory=session_factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        page_soups.update(fetched)
        errors.extend(fetch_errors)

    rows: list[dict[str, Any]] = []
    page_counts: dict[str, dict[int, int]] = {}
    malformed_count = 0
    if not errors:
        for source in ANSEONG_SOURCES:
            counts: dict[int, int] = {}
            total = source_totals[source.key]
            last = source_pages[source.key]
            for page in range(1, last + 2):
                soup = page_soups.get((source.key, page))
                if soup is None:
                    errors.append(f"{source.key}: page {page} is missing")
                    continue
                if not _list_form_valid(soup, source):
                    errors.append(f"{source.key}: page {page} list form is malformed")
                contract = _counter(soup)
                if contract is None or contract[0] != total or contract[2] != last:
                    errors.append(f"{source.key}: page {page} source counter changed")
                parsed, malformed = _parse_list_page(
                    target, source, soup, page=page
                )
                malformed_count += malformed
                counts[page] = len(parsed)
                if malformed:
                    errors.append(
                        f"{source.key}: page {page} has {malformed} malformed rows"
                    )
                if page <= last:
                    rows.extend(parsed)
            expected_terminal = total - ANSEONG_PAGE_SIZE * (last - 1)
            if total == 0:
                expected_terminal = 0
            for page in range(1, last):
                if counts.get(page) != ANSEONG_PAGE_SIZE:
                    errors.append(f"{source.key}: page {page} is not full")
            if counts.get(last) != expected_terminal:
                errors.append(f"{source.key}: terminal page row count mismatch")
            if counts.get(last + 1) != 0:
                errors.append(f"{source.key}: sentinel page is not empty")
            page_counts[source.key] = counts

    identities = [
        (
            _clean(row.get("raw_fields", {}).get("source_kind")),
            _clean(row.get("raw_fields", {}).get("identity")),
        )
        for row in rows
    ]
    raw_urls = [_clean(row.get("raw_url")) for row in rows]
    duplicate_identity_count = len(identities) - len(set(identities))
    duplicate_url_count = len(raw_urls) - len(set(raw_urls))
    if duplicate_identity_count:
        errors.append(f"{duplicate_identity_count} duplicate source identities")
    if duplicate_url_count:
        errors.append(f"{duplicate_url_count} duplicate canonical detail URLs")
    for source in ANSEONG_SOURCES:
        parsed_count = sum(
            1
            for row in rows
            if row.get("raw_fields", {}).get("source_kind") == source.key
        )
        if source.key in source_totals and parsed_count != source_totals[source.key]:
            errors.append(
                f"{source.key}: declared {source_totals[source.key]}, parsed {parsed_count}"
            )

    current_candidates: list[dict[str, Any]] = []
    expired_count = 0
    for row in rows:
        try:
            end = date.fromisoformat(_clean(row.get("end_date")))
        except ValueError:
            errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
            continue
        if end < cutoff:
            expired_count += 1
        else:
            current_candidates.append(row)

    list_complete = (
        not errors
        and len(page_soups) == required_list_requests
        and len(rows) == sum(source_totals.values())
    )
    required_details = len(current_candidates)
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    if allowed_details < required_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} required details"
        )
    elif list_complete and current_candidates:
        detail_attempts = required_details
        detail_items = [
            (
                (
                    _clean(row.get("raw_fields", {}).get("source_kind")),
                    _clean(row.get("raw_fields", {}).get("identity")),
                ),
                _clean(row.get("raw_url")),
            )
            for row in current_candidates
        ]
        detail_soups, detail_fetch_errors = _parallel_fetch(
            detail_items,
            fetcher=fetcher,
            session_factory=session_factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        row_by_key = {
            (
                _clean(row.get("raw_fields", {}).get("source_kind")),
                _clean(row.get("raw_fields", {}).get("identity")),
            ): row
            for row in current_candidates
        }
        for key, soup in detail_soups.items():
            source = _SOURCE_BY_KEY[key[0]]
            item_errors = _validate_detail(row_by_key[key], source, soup)
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                detail_pages += 1

    errors.extend(detail_errors)
    details_complete = (
        list_complete
        and detail_attempts == required_details
        and detail_pages == required_details
        and not detail_errors
    )

    collapsed: list[dict[str, Any]] = []
    duplicate_application_groups = 0
    duplicate_application_rows = 0
    if details_complete:
        collapsed, duplicate_application_groups, duplicate_application_rows = (
            _collapse_application_rounds(current_candidates)
        )
    cleaned = [_clean_row(row) for row in collapsed]
    dedupe = dedupe_rows or _dedupe_default
    if list_complete and details_complete:
        try:
            deduped = list(dedupe(cleaned))
        except Exception as exc:
            errors.append(f"dedupe failed {type(exc).__name__}")
            deduped = []
        if len(deduped) != len(cleaned):
            errors.append(
                f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}"
            )
        cleaned = deduped

    snapshot_complete = list_complete and details_complete and not errors
    if not snapshot_complete:
        cleaned = []

    branch_counts = Counter(_clean(row.get("branch")) for row in collapsed)
    candidate_branch_counts = Counter(
        _clean(row.get("branch")) for row in current_candidates
    )
    status_counts = Counter(_clean(row.get("status")) for row in collapsed)
    source_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status"))
        for row in current_candidates
    )
    method_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_method"))
        for row in collapsed
    )
    source_kind_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_kind")) for row in collapsed
    )
    reservation_links = sum(bool(row.get("application_url")) for row in collapsed)
    detail_status_difference_count = sum(
        bool(row.get("raw_fields", {}).get("detail_status_differs"))
        for row in current_candidates
    )
    meta: dict[str, Any] = {
        "pages": len(page_soups),
        "list_requests": len(page_soups),
        "required_list_requests": required_list_requests,
        "max_pages": allowed_pages,
        "page_unit": ANSEONG_PAGE_SIZE,
        "source_total": sum(source_totals.values()),
        "personal_source_total": source_totals.get("personal", 0),
        "group_source_total": source_totals.get("group", 0),
        "personal_source_pages": source_pages.get("personal", 0),
        "group_source_pages": source_pages.get("group", 0),
        "page_counts": page_counts,
        "source_rows": len(rows),
        "discovered_links": len(set(identities)),
        "duplicate_count": duplicate_identity_count,
        "duplicate_url_count": duplicate_url_count,
        "malformed_count": malformed_count,
        "expired_count": expired_count,
        "current_candidate_count": len(current_candidates),
        "duplicate_application_group_count": duplicate_application_groups,
        "duplicate_application_round_count": duplicate_application_rows,
        "current_count": len(collapsed),
        "returned_count": len(cleaned),
        "required_detail_count": required_details,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "detail_status_difference_count": detail_status_difference_count,
        "pagination_detected": any(value > 1 for value in source_pages.values()),
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "status_counts": dict(status_counts),
        "source_status_counts": dict(source_status_counts),
        "method_counts": dict(method_counts),
        "source_kind_counts": dict(source_kind_counts),
        "branch_count": len(branch_counts),
        "branch_counts": dict(branch_counts),
        "candidate_branch_count": len(candidate_branch_counts),
        "candidate_branch_counts": dict(candidate_branch_counts),
        "reservation_discovery_links": reservation_links,
        "no_current_data": snapshot_complete and not collapsed,
        "no_current_reason": (
            "all official Anseong personal and group courses have ended"
            if snapshot_complete and not collapsed
            else ""
        ),
    }
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
    return cleaned, ANSEONG_PARSER, meta


collect_anseong_target = collect_anseong_education_courses


__all__ = [
    "ANSEONG_FETCH_ATTEMPTS",
    "ANSEONG_GROUP_DETAIL_PATH",
    "ANSEONG_GROUP_LIST_PATH",
    "ANSEONG_GROUP_SOURCE",
    "ANSEONG_GROUP_URL",
    "ANSEONG_HOST",
    "ANSEONG_MAX_WORKERS",
    "ANSEONG_MID",
    "ANSEONG_MUNICIPALITY_CODE",
    "ANSEONG_MUNICIPALITY_NAME",
    "ANSEONG_PAGE_SIZE",
    "ANSEONG_PARSER",
    "ANSEONG_PERSONAL_DETAIL_PATH",
    "ANSEONG_PERSONAL_LIST_PATH",
    "ANSEONG_PERSONAL_SOURCE",
    "ANSEONG_PROVIDER",
    "ANSEONG_SOURCES",
    "ANSEONG_URL",
    "anseong_detail_url",
    "anseong_list_url",
    "collect_anseong_education_courses",
    "collect_anseong_target",
    "is_anseong_target",
    "is_target",
]
