"""Fail-closed collectors for Gangseo-gu's current education sources.

The two supported routes deliberately use different collection contracts:

* the Gangseo information-education site exposes a complete numbered HTML
  history and rotating ``lecDetSn`` detail tokens;
* the Gangseo public-library site exposes a bounded JSON API whose stable
  identity is the ``(leCode, leLGCode)`` pair.

No database, scheduler, or shared crawler configuration is imported here.  A
parent crawler can safely dispatch into this module and inject HTTP/session and
deduplication behaviour for tests or orchestration.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import html
import json
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GANGSEO_INFORMATION_PROVIDER = "GANGSEO_RESERVATION"
GANGSEO_INFORMATION_URL = "https://www.gangseo.seoul.kr/reserve/re010202"
GANGSEO_INFORMATION_HOST = "www.gangseo.seoul.kr"
GANGSEO_INFORMATION_LIST_PATH = "/reserve/re010202"
GANGSEO_INFORMATION_DETAIL_PATH = "/reserve/re010202/view"
GANGSEO_INFORMATION_PAGE_SIZE = 10
GANGSEO_INFORMATION_PARSER = "gangseo_information_complete_pages_current_future+detail"
GANGSEO_INFORMATION_CRAWL_DELAY_SECONDS = 10.0
GANGSEO_TRANSIENT_FETCH_ATTEMPTS = 2
GANGSEO_TRANSIENT_RETRY_BACKOFF_SECONDS = 0.25

GANGSEO_LIBRARY_PROVIDER = "MUNI_LIB_GANGSEO_SEOUL_KR_520A90A3"
GANGSEO_LIBRARY_URL = "https://lib.gangseo.seoul.kr/LibProgramApply?libCode=TOL"
GANGSEO_LIBRARY_HOST = "lib.gangseo.seoul.kr"
GANGSEO_LIBRARY_CANONICAL_PATH = "/LibProgramApply"
GANGSEO_LIBRARY_API_URL = (
    "https://lib.gangseo.seoul.kr/service/culturalLecture/list"
)
GANGSEO_LIBRARY_DETAIL_API_URL = (
    "https://lib.gangseo.seoul.kr/service/culturalLecture/detail"
)
GANGSEO_LIBRARY_DETAIL_URL = "https://lib.gangseo.seoul.kr/LibProgramDetail"
GANGSEO_LIBRARY_PAGE_SIZE = 1000
GANGSEO_LIBRARY_PARSER = "gangseo_library_complete_api_current_future+detail"

GANGSEO_MUNICIPALITY_CODE = "1150000000"
GANGSEO_MUNICIPALITY_NAME = "서울특별시 강서구"
GANGSEO_MAX_DETAIL_WORKERS = 8

GANGSEO_PROVIDERS = frozenset(
    (GANGSEO_INFORMATION_PROVIDER, GANGSEO_LIBRARY_PROVIDER)
)
GANGSEO_CANONICAL_URLS = {
    GANGSEO_INFORMATION_PROVIDER: GANGSEO_INFORMATION_URL,
    GANGSEO_LIBRARY_PROVIDER: GANGSEO_LIBRARY_URL,
}

GANGSEO_LIBRARY_BRANCHES = {
    "AA": "강서영어도서관",
    "AB": "곰달래도서관",
    "AC": "길꽃어린이도서관",
    "AD": "꿈꾸는어린이도서관",
    "AE": "푸른들청소년도서관",
    "AF": "우장산숲속도서관",
    "AG": "등빛도서관",
    "BG": "가양도서관",
}

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{2}|\d{4})[.\-/](?P<month>\d{1,2})[.\-/](?P<day>\d{1,2})(?!\d)"
)


class GangseoHostPacer:
    """Serialize live requests so the official host-wide Crawl-delay is met."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def wait(
        self,
        interval_seconds: float,
        *,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        interval = max(0.0, float(interval_seconds))
        if interval == 0:
            return
        with self._lock:
            now = float(monotonic_fn())
            delay = max(0.0, self._next_request_at - now)
            if delay:
                sleep_fn(delay)
                now = float(monotonic_fn())
            self._next_request_at = max(now, self._next_request_at) + interval


GANGSEO_INFORMATION_HOST_PACER = GangseoHostPacer()


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\s\u200b]+", "", _clean(value)).casefold()


def _strip_html(value: Any) -> str:
    raw = html.unescape(str(value or ""))
    if "<" not in raw and ">" not in raw:
        return _clean(raw)
    return _clean(BeautifulSoup(raw, "lxml").get_text(" ", strip=True))


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _target_signature(target: Any) -> tuple[str, str]:
    return _provider(target), _target_url(target)


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not getattr(response, "content", b""):
        raise ValueError("empty HTTP response")
    return response


def _paced_fetcher(
    fetcher: Fetcher,
    *,
    delay_seconds: float,
    pacer: GangseoHostPacer = GANGSEO_INFORMATION_HOST_PACER,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Fetcher:
    def paced(current_session: Any, url: str, timeout: int) -> Any:
        pacer.wait(
            delay_seconds,
            monotonic_fn=monotonic_fn,
            sleep_fn=sleep_fn,
        )
        return fetcher(current_session, url, timeout)

    return paced


def _retrying_fetcher(
    fetcher: Fetcher,
    *,
    attempts: int = GANGSEO_TRANSIENT_FETCH_ATTEMPTS,
    backoff_seconds: float = GANGSEO_TRANSIENT_RETRY_BACKOFF_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Fetcher:
    """Match the municipal fetch policy for transient transport failures only."""

    allowed_attempts = max(1, int(attempts))

    def retrying(current_session: Any, url: str, timeout: int) -> Any:
        for attempt in range(allowed_attempts):
            try:
                return fetcher(current_session, url, timeout)
            except (requests.Timeout, requests.ConnectionError):
                if attempt + 1 >= allowed_attempts:
                    raise
                sleep_fn(max(0.0, float(backoff_seconds)) * (2**attempt))
        raise AssertionError("unreachable transient retry state")

    return retrying


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        text = getattr(value, "text", None)
        if text is None:
            raise TypeError("fetcher did not return HTML or BeautifulSoup")
        content = text
    return BeautifulSoup(content, "lxml")


def _coerce_json(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    json_method = getattr(value, "json", None)
    if callable(json_method):
        decoded = json_method()
    elif isinstance(value, (str, bytes, bytearray)):
        decoded = json.loads(value)
    else:
        content = getattr(value, "content", None)
        if content is None:
            raise TypeError("fetcher did not return JSON")
        decoded = json.loads(content)
    if not isinstance(decoded, Mapping):
        raise TypeError("JSON response is not an object")
    return decoded


def _fetch_html(
    fetcher: Fetcher, current_session: Any, url: str, timeout: int
) -> BeautifulSoup:
    return _coerce_soup(fetcher(current_session, url, timeout))


def _fetch_json(
    fetcher: Fetcher, current_session: Any, url: str, timeout: int
) -> Mapping[str, Any]:
    return _coerce_json(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _today(value: Optional[date | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _date_tokens(value: Any) -> list[date]:
    parsed: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        year = int(match.group("year"))
        if year < 100:
            year += 2000
        try:
            parsed.append(date(year, int(match.group("month")), int(match.group("day"))))
        except ValueError:
            continue
    return parsed


def _single_date(value: Any) -> str:
    tokens = _date_tokens(value)
    return tokens[0].isoformat() if tokens else ""


def _date_range(value: Any) -> tuple[str, str, str]:
    tokens = _date_tokens(value)
    if len(tokens) < 2 or tokens[1] < tokens[0]:
        return "", "", ""
    start, end = tokens[0], tokens[1]
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _as_int(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return None


def _stable_branch_code(provider: str, branch: str) -> str:
    digest = hashlib.sha1(
        f"{_clean(provider)}|{_normalized(branch)}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"GANGSEO_BRANCH_{digest}"


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if value not in (None, "", [], {})
    }


def _base_row(
    target: Any,
    *,
    provider_course_id: str,
    title: str,
    branch: str,
    raw_url: str,
    parser: str,
) -> dict[str, Any]:
    provider = _provider(target)
    return {
        "provider": provider,
        "provider_course_id": provider_course_id[:100],
        "prefer_incoming_provider_course_id": True,
        "title": _clean(title),
        "branch": _clean(branch),
        "branch_code": _stable_branch_code(provider, branch),
        "preserve_branch": True,
        "branch_url": _target_url(target),
        "program_type": "강좌",
        "category": "교육·강좌",
        "raw_url": raw_url,
        "reservation_available": False,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "municipality_code": GANGSEO_MUNICIPALITY_CODE,
        "municipality_full_name": GANGSEO_MUNICIPALITY_NAME,
        "raw_fields": {"parser": parser},
    }


def is_gangseo_information_target(target: Any) -> bool:
    provider, raw_url = _target_signature(target)
    parsed = urlparse(raw_url)
    return (
        provider == GANGSEO_INFORMATION_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGSEO_INFORMATION_HOST
        and parsed.path == GANGSEO_INFORMATION_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def is_gangseo_library_target(target: Any) -> bool:
    provider, raw_url = _target_signature(target)
    parsed = urlparse(raw_url)
    return (
        provider == GANGSEO_LIBRARY_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGSEO_LIBRARY_HOST
        and parsed.path == GANGSEO_LIBRARY_CANONICAL_PATH
        and not parsed.params
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True) == {"libCode": ["TOL"]}
    )


def is_gangseo_target(target: Any) -> bool:
    return is_gangseo_information_target(target) or is_gangseo_library_target(target)


is_target = is_gangseo_target


def gangseo_information_list_url(page_index: int) -> str:
    query = urlencode({"curPage": max(1, int(page_index))})
    return f"{GANGSEO_INFORMATION_URL}?{query}"


def _information_detail_url(value: Any) -> str:
    candidate = urljoin(GANGSEO_INFORMATION_URL, _clean(value))
    parsed = urlparse(candidate)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not (
        parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGSEO_INFORMATION_HOST
        and parsed.path == GANGSEO_INFORMATION_DETAIL_PATH
        and not parsed.params
        and not parsed.fragment
        and len(query.get("lecDetSn", [])) == 1
        and _clean(query["lecDetSn"][0])
    ):
        return ""
    return candidate


def gangseo_library_list_url(page_index: int) -> str:
    query = urlencode(
        (
            ("libCode", "TOL"),
            ("pageSize", str(GANGSEO_LIBRARY_PAGE_SIZE)),
            ("pageIndex", str(max(1, int(page_index)))),
            ("leLName", ""),
        )
    )
    return f"{GANGSEO_LIBRARY_API_URL}?{query}"


def gangseo_library_detail_api_url(le_code: Any, le_lg_code: Any) -> str:
    identity = _clean(le_code)
    group_identity = _clean(le_lg_code)
    if not identity.isdigit() or not group_identity.isdigit():
        return ""
    query = urlencode((("leCode", identity), ("leLGCode", group_identity)))
    return f"{GANGSEO_LIBRARY_DETAIL_API_URL}?{query}"


def gangseo_library_detail_url(le_code: Any, le_lg_code: Any) -> str:
    identity = _clean(le_code)
    group_identity = _clean(le_lg_code)
    if not identity.isdigit() or not group_identity.isdigit():
        return ""
    query = urlencode((("leCode", identity), ("leLGCode", group_identity)))
    return f"{GANGSEO_LIBRARY_DETAIL_URL}?{query}"


def _pairs_from_tables(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for table in soup.select("table"):
        for tr in table.select("tr"):
            key = ""
            for cell in tr.find_all(["th", "td"], recursive=False):
                if cell.name == "th":
                    key = _clean(cell.get_text(" ", strip=True))
                    if _normalized(key) == _normalized("기수"):
                        key = "기수"
                elif key:
                    value = _clean(cell.get_text(" ", strip=True))
                    if key not in pairs or not pairs[key]:
                        pairs[key] = value
                    key = ""
    return pairs


def _capacity_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    numbers = [
        int(token.replace(",", ""))
        for token in re.findall(r"\d[\d,]*", _clean(value))
    ]
    if len(numbers) < 2:
        return None, None
    # The Gangseo information table is explicitly labelled 정원/신청인원.
    return numbers[1], numbers[0]


def _status(value: Any) -> str:
    raw = _clean(value)
    if any(token in raw for token in ("접수중", "신청가능", "모집중")):
        return "OPEN"
    if any(token in raw for token in ("대기자", "대기신청")):
        return "WAITLIST"
    if any(
        token in raw
        for token in ("접수예정", "신청예정", "접수대기", "신청대기", "대기중")
    ):
        return "SCHEDULED"
    if any(token in raw for token in ("마감", "종료", "완료")):
        return "CLOSED"
    return ""


def _information_page_declaration(
    soup: BeautifulSoup,
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    text = _clean(soup.get_text(" ", strip=True))
    combined_patterns = (
        r"총\s*([\d,]+)\s*건.*?\(?\s*(\d+)\s*/\s*(\d+)\s*페이지",
        r"전체\s*([\d,]+)\s*건.*?\(?\s*(\d+)\s*/\s*(\d+)\s*페이지",
    )
    for pattern in combined_patterns:
        match = re.search(pattern, text)
        if match:
            return (
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(1).replace(",", "")),
            )

    total_match = re.search(r"(?:총|전체)\s*([\d,]+)\s*건", text)
    page_match = re.search(r"(\d+)\s*/\s*(\d+)\s*(?:페이지|page)", text, re.I)
    if total_match and page_match:
        return (
            int(page_match.group(1)),
            int(page_match.group(2)),
            int(total_match.group(1).replace(",", "")),
        )
    return None, None, None


def _information_table(soup: BeautifulSoup) -> tuple[Any, dict[str, int]]:
    aliases = {
        "generation": ("기수",),
        "title": ("강좌명", "교육명"),
        "venue": ("교육장소", "장소"),
        "period": ("교육기간",),
        "schedule": ("교육시간", "교육일시"),
        "target": ("대상",),
        "capacity": ("정원/신청인원", "정원신청인원"),
        "status": ("상태", "접수상태"),
    }
    for table in soup.select("table"):
        header_row = table.select_one("thead tr")
        if header_row is None:
            header_row = next(
                (tr for tr in table.select("tr") if tr.find("th") is not None),
                None,
            )
        if header_row is None:
            continue
        headers = [
            _normalized(cell.get_text(" ", strip=True))
            for cell in header_row.find_all(["th", "td"], recursive=False)
        ]
        positions: dict[str, int] = {}
        for field, names in aliases.items():
            for index, header in enumerate(headers):
                if any(_normalized(name) in header for name in names):
                    positions[field] = index
                    break
        if all(field in positions for field in aliases):
            return table, positions
    return None, {}


def _information_stable_identity(
    generation: str,
    title: str,
    venue: str,
    period: str,
    schedule: str,
) -> tuple[str, tuple[str, ...]]:
    components = tuple(
        _clean(value) for value in (generation, title, venue, period, schedule)
    )
    canonical = "|".join(_normalized(value) for value in components)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24].upper()
    return digest, components


def _information_list_rows(
    target: Any,
    soup: BeautifulSoup,
    *,
    page_index: int,
) -> tuple[list[dict[str, Any]], int, int]:
    table, positions = _information_table(soup)
    if table is None:
        return [], 1, 0
    rows: list[dict[str, Any]] = []
    invalid = 0
    exposed = 0
    body_rows = table.select("tbody tr") or [
        tr for tr in table.select("tr") if tr.find("td") is not None
    ]
    for tr in body_rows:
        cells = tr.find_all(["th", "td"], recursive=False)
        text = _clean(tr.get_text(" ", strip=True))
        if not text or any(
            token in text
            for token in ("등록된 강좌가 없습니다", "조회된 자료가 없습니다", "검색 결과가 없습니다")
        ):
            continue
        exposed += 1
        if not cells or max(positions.values()) >= len(cells):
            invalid += 1
            continue
        values = {
            field: _clean(cells[index].get_text(" ", strip=True))
            for field, index in positions.items()
        }
        title_cell = cells[positions["title"]]
        link = title_cell.select_one("a[href]") or tr.select_one(
            f"a[href*='{GANGSEO_INFORMATION_DETAIL_PATH}']"
        )
        detail_url = _information_detail_url(link.get("href") if link else "")
        start_date, end_date, period = _date_range(values["period"])
        status = _status(values["status"])
        digest, components = _information_stable_identity(
            values["generation"],
            values["title"],
            values["venue"],
            period or values["period"],
            values["schedule"],
        )
        if not all(
            (
                values["generation"],
                values["title"],
                values["venue"],
                values["schedule"],
                values["target"],
                start_date,
                end_date,
                status,
                detail_url,
            )
        ):
            invalid += 1
            continue
        provider = _provider(target)
        row = _base_row(
            target,
            provider_course_id=f"{provider}:stable:{digest}",
            title=values["title"],
            branch=values["venue"],
            raw_url=detail_url,
            parser=GANGSEO_INFORMATION_PARSER,
        )
        capacity_current, capacity_total = _capacity_pair(values["capacity"])
        row.update(
            {
                "status": status,
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
                "schedule_raw": values["schedule"],
                "target": values["target"],
                "venue_name": values["venue"],
                "room": values["venue"],
                "capacity": values["capacity"],
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "description": text,
                "collection_type": "complete_numbered_pages+detail_html",
            }
        )
        row["raw_fields"].update(
            {
                "page_index": page_index,
                "generation": values["generation"],
                "list_period_raw": values["period"],
                "list_schedule_raw": values["schedule"],
                "list_target_raw": values["target"],
                "source_status": values["status"],
                "stable_identity_components": list(components),
                "stable_identity_hash": digest,
                "rotating_lecDetSn_ignored_for_identity": True,
            }
        )
        rows.append(row)
    return rows, invalid, exposed


def _information_detail_title(soup: BeautifulSoup, expected: str) -> str:
    selectors = (
        ".view-title",
        ".view_title",
        ".board-view-title",
        ".subject",
        "h1",
        "h2",
        "h3",
        "h4",
    )
    candidates: list[str] = []
    for selector in selectors:
        for node in soup.select(selector):
            candidate = _clean(node.get_text(" ", strip=True))
            if candidate:
                candidates.append(candidate)
                if _normalized(candidate) == _normalized(expected):
                    return candidate
    for selector in ("meta[property='og:title'][content]", "meta[name='title'][content]"):
        node = soup.select_one(selector)
        candidate = _clean(node.get("content") if node else "")
        if candidate:
            candidates.append(candidate)
            if _normalized(candidate) == _normalized(expected):
                return candidate
    return candidates[0] if candidates else ""


def _information_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    identity = _clean(row.get("provider_course_id"))
    fields = _pairs_from_tables(soup)
    required_nonempty = (
        "교육장소",
        "기수",
        "모집기간",
        "대상",
        "교육기간",
        "교육시간",
        "교육인원",
    )
    missing = [key for key in required_nonempty if not _clean(fields.get(key))]
    if missing:
        errors.append(f"{identity}: missing detail fields {','.join(missing)}")

    detail_title = _information_detail_title(soup, _clean(row.get("title")))
    if _normalized(detail_title) != _normalized(row.get("title")):
        errors.append(f"{identity}: detail/list title mismatch")
    if _normalized(fields.get("교육장소")) != _normalized(row.get("venue_name")):
        errors.append(f"{identity}: detail/list venue mismatch")
    if _normalized(fields.get("기수")) != _normalized(
        row.get("raw_fields", {}).get("generation")
    ):
        errors.append(f"{identity}: detail/list generation mismatch")

    detail_start, detail_end, detail_period = _date_range(fields.get("교육기간"))
    if (
        not detail_period
        or detail_start != _clean(row.get("start_date"))
        or detail_end != _clean(row.get("end_date"))
    ):
        errors.append(f"{identity}: detail/list education period mismatch")
    if _normalized(fields.get("교육시간")) != _normalized(row.get("schedule_raw")):
        errors.append(f"{identity}: detail/list education time mismatch")
    if _normalized(fields.get("대상")) != _normalized(row.get("target")):
        errors.append(f"{identity}: detail/list target mismatch")

    apply_start, apply_end, apply_period = _date_range(fields.get("모집기간"))
    if not apply_period:
        errors.append(f"{identity}: malformed detail reception period")
    fee = next(
        (
            _clean(fields.get(key))
            for key in ("수강료", "교육비", "비용")
            if _clean(fields.get(key))
        ),
        "요금 별도 안내",
    )
    row.update(
        {
            "period": detail_period or row.get("period"),
            "start_date": detail_start or row.get("start_date"),
            "end_date": detail_end or row.get("end_date"),
            "apply_period": apply_period,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "schedule_raw": _clean(fields.get("교육시간")) or row.get("schedule_raw"),
            "target": _clean(fields.get("대상")) or row.get("target"),
            "fee": fee,
            "capacity": _clean(fields.get("교육인원")) or row.get("capacity"),
            "instructor": _clean(fields.get("강사명")),
            "selection_method_raw": _clean(fields.get("선정방법")),
            "session_count_raw": _clean(fields.get("강의횟수")),
            "notice": _clean(fields.get("비고")),
        }
    )
    is_open = _clean(row.get("status")) in {"OPEN", "WAITLIST"}
    reservation_control = soup.select_one(
        "a[href*='apply'], a[href*='req'], a[onclick*='apply'], "
        "button[onclick*='apply'], input[type='submit']"
    )
    row["reservation_available"] = bool(is_open and reservation_control is not None)
    if row["reservation_available"]:
        row["application_url"] = _clean(row.get("raw_url"))
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row["raw_fields"]["clear_application_url"] = True
    description_node = soup.select_one(
        ".view-content, .view_cont, .board-view-content, .bbs_view_cont"
    )
    if description_node is not None:
        description = _clean(description_node.get_text(" ", strip=True))
        if description:
            row["description"] = description
    row["raw_fields"].update(
        {
            "detail_pairs": fields,
            "detail_identity_verified": not errors,
            "fee_evidence": (
                "official_detail_label"
                if any(_clean(fields.get(key)) for key in ("수강료", "교육비", "비용"))
                else "official_list_and_detail_omit_fee"
            ),
        }
    )
    return errors


def _parallel_details(
    rows: list[dict[str, Any]],
    *,
    timeout: int,
    detail_limit: int,
    max_workers: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    parser: Callable[[dict[str, Any], Any], list[str]],
    fetch_json: bool,
    url_getter: Callable[[dict[str, Any]], str],
    thread_prefix: str,
) -> tuple[int, int, int, list[str], bool]:
    required_count = len(rows)
    allowed = max(0, int(detail_limit))
    selected = rows[:allowed]
    capped = allowed < required_count
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def current_session() -> Any:
        value = getattr(local, "session", None)
        if value is None:
            value = session_factory()
            local.session = value
            with sessions_lock:
                sessions.append(value)
        return value

    def enrich(row: dict[str, Any]) -> tuple[bool, list[str]]:
        identity = _clean(row.get("provider_course_id"))
        url = _clean(url_getter(row))
        if not url:
            return False, [f"{identity}: missing safe provider detail URL"]
        try:
            if fetch_json:
                payload: Any = _fetch_json(fetcher, current_session(), url, timeout)
            else:
                payload = _fetch_html(fetcher, current_session(), url, timeout)
            return True, parser(row, payload)
        except Exception as exc:
            return False, [f"{identity}: detail fetch {type(exc).__name__}"]

    results: list[tuple[bool, list[str]]] = []
    try:
        if selected:
            workers = min(
                GANGSEO_MAX_DETAIL_WORKERS,
                max(1, int(max_workers)),
                len(selected),
            )
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix=thread_prefix
            ) as pool:
                results = list(pool.map(enrich, selected))
    finally:
        for value in sessions:
            _close_quietly(value)
    detail_pages = sum(success for success, _errors in results)
    errors = [error for _success, item in results for error in item]
    return required_count, len(selected), detail_pages, errors, capped


def _apply_injected_dedupe(
    rows: list[dict[str, Any]],
    dedupe_rows: Optional[DedupeRows],
    errors: list[str],
) -> list[dict[str, Any]]:
    if dedupe_rows is None:
        result = rows
    else:
        try:
            result = list(dedupe_rows(rows))
        except Exception as exc:
            errors.append(f"dedupe hook failed with {type(exc).__name__}")
            return rows
    identities = [_clean(row.get("provider_course_id")) for row in result]
    if any(not identity for identity in identities):
        errors.append("dedupe output contains a row without provider_course_id")
    if len(set(identities)) != len(identities):
        errors.append("dedupe output contains duplicate provider_course_id values")
    return result


def _finish_meta(
    *,
    rows: list[dict[str, Any]],
    pages: int,
    list_complete: bool,
    detail_required_count: int,
    detail_attempts: int,
    detail_pages: int,
    detail_errors: list[str],
    source_cap_reached: bool,
    errors: list[str],
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    details_complete = (
        detail_attempts == detail_required_count
        and detail_pages == detail_required_count
        and not detail_errors
    )
    all_errors = list(dict.fromkeys([*errors, *detail_errors]))
    snapshot_complete = bool(list_complete and details_complete and not all_errors)
    no_current_data = snapshot_complete and not rows
    meta: dict[str, Any] = {
        "pages": pages,
        "detail_pages": detail_pages,
        "detail_attempts": detail_attempts,
        "detail_required_count": detail_required_count,
        "required_detail_count": detail_required_count,
        "detail_errors": len(detail_errors),
        "pagination_detected": pages > 1,
        "pagination_complete": bool(list_complete),
        "pagination_exhausted": bool(list_complete),
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "current_count": len(rows),
        "returned_count": len(rows),
        "no_current_data": no_current_data,
        "no_current_reason": (
            "official current/future education list is empty" if no_current_data else ""
        ),
        "branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
    }
    if extra:
        meta.update(extra)
    if all_errors:
        meta["configured_collection_error"] = "; ".join(all_errors)
    return meta


def collect_gangseo_information(
    target: Any,
    timeout: int = 20,
    max_pages: int = 100,
    detail_limit: int = 2000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | str] = None,
    max_workers: int = 6,
    dedupe_rows: Optional[DedupeRows] = None,
    crawl_delay_seconds: Optional[float] = None,
    pacer: GangseoHostPacer = GANGSEO_INFORMATION_HOST_PACER,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect all declared information-education pages, then current details."""

    errors: list[str] = []
    if not is_gangseo_information_target(target):
        errors.append(
            "target does not match the exact provider-owned Gangseo information canonical"
        )
    if int(max_pages) < 1:
        errors.append("max_pages must allow at least page 1")
    if crawl_delay_seconds is not None and float(crawl_delay_seconds) < 0:
        errors.append("crawl_delay_seconds must not be negative")
    live_fetch = fetcher is None
    effective_crawl_delay = (
        GANGSEO_INFORMATION_CRAWL_DELAY_SECONDS
        if live_fetch and crawl_delay_seconds is None
        else max(0.0, float(crawl_delay_seconds or 0.0))
    )
    fetch = fetcher or _default_fetcher
    if effective_crawl_delay:
        fetch = _paced_fetcher(
            fetch,
            delay_seconds=effective_crawl_delay,
            pacer=pacer,
            monotonic_fn=monotonic_fn,
            sleep_fn=sleep_fn,
        )
    if live_fetch:
        fetch = _retrying_fetcher(fetch, sleep_fn=sleep_fn)
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    listed_current: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages = 0
    exposed_total = 0
    invalid = 0
    duplicates = 0
    expired_count = 0
    declared_total = 0
    declared_pages = 0
    source_cap_reached = False
    primary_session: Any = None

    try:
        if not errors:
            primary_session = make_session()
            try:
                first_soup = _fetch_html(
                    fetch,
                    primary_session,
                    gangseo_information_list_url(1),
                    timeout,
                )
            except Exception as exc:
                errors.append(f"page 1 fetch {type(exc).__name__}")
                first_soup = None
            if first_soup is not None:
                current_page, total_pages, total_count = _information_page_declaration(
                    first_soup
                )
                if current_page != 1 or total_pages is None or total_count is None:
                    errors.append("page 1 total/page declaration is missing or malformed")
                else:
                    declared_pages = int(total_pages)
                    declared_total = int(total_count)
                    expected_pages = max(
                        1, math.ceil(declared_total / GANGSEO_INFORMATION_PAGE_SIZE)
                    )
                    if declared_pages != expected_pages:
                        errors.append(
                            f"declared {declared_pages} pages but total {declared_total} requires {expected_pages}"
                        )
                    allowed_pages = int(max_pages)
                    if declared_pages > allowed_pages:
                        source_cap_reached = True
                        errors.append(
                            f"max_pages cap reached after {allowed_pages} of {declared_pages} declared pages"
                        )
                    for page_index in range(1, min(declared_pages, allowed_pages) + 1):
                        if page_index == 1:
                            soup = first_soup
                        else:
                            try:
                                soup = _fetch_html(
                                    fetch,
                                    primary_session,
                                    gangseo_information_list_url(page_index),
                                    timeout,
                                )
                            except Exception as exc:
                                errors.append(
                                    f"page {page_index} fetch {type(exc).__name__}"
                                )
                                break
                        pages += 1
                        declaration = _information_page_declaration(soup)
                        if declaration != (page_index, declared_pages, declared_total):
                            errors.append(f"page {page_index} total/page declaration changed")
                        page_rows, page_invalid, page_exposed = _information_list_rows(
                            target, soup, page_index=page_index
                        )
                        invalid += page_invalid
                        exposed_total += page_exposed
                        expected_rows = min(
                            GANGSEO_INFORMATION_PAGE_SIZE,
                            max(
                                0,
                                declared_total
                                - (page_index - 1) * GANGSEO_INFORMATION_PAGE_SIZE,
                            ),
                        )
                        if page_exposed != expected_rows:
                            errors.append(
                                f"page {page_index} exposed {page_exposed} rows; expected {expected_rows}"
                            )
                        for row in page_rows:
                            identity = _clean(row.get("provider_course_id"))
                            if identity in seen:
                                duplicates += 1
                                continue
                            seen.add(identity)
                            try:
                                end_date = date.fromisoformat(_clean(row.get("end_date")))
                            except ValueError:
                                invalid += 1
                                continue
                            if end_date < cutoff:
                                expired_count += 1
                                continue
                            listed_current.append(row)
    finally:
        _close_quietly(primary_session)

    if invalid:
        errors.append(f"{invalid} information list rows were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate stable information identities crossed pages")
    if declared_total != len(seen):
        errors.append(
            f"declared total {declared_total} does not match {len(seen)} unique stable identities"
        )
    list_complete = bool(
        not errors
        and pages == declared_pages
        and exposed_total == declared_total
        and declared_pages > 0
        and not invalid
        and not duplicates
    )

    (
        detail_required_count,
        detail_attempts,
        detail_pages,
        detail_errors,
        detail_capped,
    ) = _parallel_details(
        listed_current,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
        parser=_information_detail,
        fetch_json=False,
        url_getter=lambda row: _information_detail_url(row.get("raw_url")),
        thread_prefix="gangseo-information-detail",
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {detail_attempts} of {detail_required_count} required detail pages"
        )
    rows = [_clean_row(row) for row in listed_current]
    rows = _apply_injected_dedupe(rows, dedupe_rows, errors)
    required_fields = (
        "target",
        "fee",
        "start_date",
        "end_date",
        "venue_name",
        "category",
        "schedule_raw",
    )
    required_field_counts = {
        field: sum(bool(_clean(row.get(field))) for row in rows)
        for field in required_fields
    }
    missing_required = {
        field: len(rows) - count
        for field, count in required_field_counts.items()
        if count != len(rows)
    }
    if rows and missing_required:
        errors.append(f"required output fields absent {missing_required}")
    meta = _finish_meta(
        rows=rows,
        pages=pages,
        list_complete=list_complete,
        detail_required_count=detail_required_count,
        detail_attempts=detail_attempts,
        detail_pages=detail_pages,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
        extra={
            "source_url": GANGSEO_INFORMATION_URL,
            "provider": GANGSEO_INFORMATION_PROVIDER,
            "parser": GANGSEO_INFORMATION_PARSER,
            "page_size": GANGSEO_INFORMATION_PAGE_SIZE,
            "total_pages": declared_pages,
            "declared_pages": declared_pages,
            "total_count": declared_total,
            "declared_total": declared_total,
            "raw_row_count": exposed_total,
            "unique_count": len(seen),
            "stable_hash_count": len(seen),
            "current_future_count_raw": len(listed_current),
            "expired_count": expired_count,
            "invalid_count": invalid,
            "duplicate_count": duplicates,
            "rotating_detail_tokens_ignored": True,
            "required_field_counts": required_field_counts,
            "crawl_delay_seconds": effective_crawl_delay,
            "transient_fetch_attempts": (
                GANGSEO_TRANSIENT_FETCH_ATTEMPTS if live_fetch else 1
            ),
        },
    )
    return rows, GANGSEO_INFORMATION_PARSER, meta


def _library_page(payload: Mapping[str, Any]) -> tuple[
    Optional[int], Optional[int], Optional[int], Optional[int], list[Mapping[str, Any]]
]:
    if _clean(payload.get("status")).upper() not in {"OK", "SUCCESS"}:
        return None, None, None, None, []
    container = payload.get("data")
    if not isinstance(container, Mapping):
        return None, None, None, None, []
    raw_items = container.get("data")
    if not isinstance(raw_items, list):
        raw_items = container.get("list")
    if not isinstance(raw_items, list):
        raw_items = container.get("items")
    if not isinstance(raw_items, list):
        return None, None, None, None, []
    items = [item for item in raw_items if isinstance(item, Mapping)]
    total_count = _as_int(container.get("totalCount"))
    total_pages = _as_int(container.get("totalPage"))
    if total_pages is None:
        total_pages = _as_int(container.get("totalPages"))
    page_index = _as_int(container.get("pageIndex"))
    page_size = _as_int(container.get("pageSize"))
    return page_index, total_pages, total_count, page_size, items


def _library_branch(library_code: str, venue: str) -> str:
    code = _clean(library_code).upper()
    if code in GANGSEO_LIBRARY_BRANCHES:
        return GANGSEO_LIBRARY_BRANCHES[code]
    if code not in {"TOL", "ZA"}:
        return ""
    cleaned = _clean(venue)
    match = re.search(
        r"([가-힣A-Za-z0-9][가-힣A-Za-z0-9·\- ]{0,45}?도서관)", cleaned
    )
    return _clean(match.group(1)) if match else ""


def _first_value(item: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in item and item.get(name) not in (None, ""):
            return item.get(name)
    return ""


def _library_capacity(item: Mapping[str, Any]) -> tuple[Optional[int], Optional[int], Optional[int]]:
    total = _as_int(
        _first_value(item, "leNum", "leLimitCnt", "leMaxCnt", "capacity")
    )
    current = _as_int(
        _first_value(item, "leTakeNum", "leApplyCnt", "leNowCnt", "applyCount")
    )
    waitlist = _as_int(
        _first_value(item, "leWaitNum", "leWaitingCnt", "waitCount")
    )
    return current, total, waitlist


def _library_list_row(
    target: Any,
    item: Mapping[str, Any],
    *,
    page_index: int,
) -> tuple[Optional[dict[str, Any]], str]:
    le_code = _clean(item.get("leCode"))
    le_lg_code = _clean(item.get("leLGCode"))
    library_code = _clean(_first_value(item, "lgLib", "libCode")).upper()
    title = _clean(item.get("leLName"))
    start_date = _single_date(item.get("leOpenSDateFmt"))
    end_date = _single_date(item.get("leOpenEDateFmt"))
    apply_start = _single_date(item.get("leTakeSDateFmt"))
    apply_end = _single_date(item.get("leTakeEDateFmt"))
    venue = _clean(item.get("leArea"))
    schedule = _strip_html(_first_value(item, "leBeginTime", "leTime", "schedule"))
    source_status = _clean(
        _first_value(item, "lectureStatusName", "leStatusName", "statusName")
    )
    status = _status(source_status)
    branch = _library_branch(library_code, venue)
    raw_url = gangseo_library_detail_url(le_code, le_lg_code)
    detail_api_url = gangseo_library_detail_api_url(le_code, le_lg_code)
    if not all(
        (
            le_code.isdigit(),
            le_lg_code.isdigit(),
            library_code,
            title,
            start_date,
            end_date,
            status,
            branch,
            raw_url,
            detail_api_url,
        )
    ):
        return None, f"invalid library identity or required fields {le_code}:{le_lg_code}"
    provider = _provider(target)
    provider_course_id = f"{provider}:lecture:{le_code}:{le_lg_code}"
    row = _base_row(
        target,
        provider_course_id=provider_course_id,
        title=title,
        branch=branch,
        raw_url=raw_url,
        parser=GANGSEO_LIBRARY_PARSER,
    )
    capacity_current, capacity_total, waitlist_total = _library_capacity(item)
    row.update(
        {
            "status": status,
            "period": f"{start_date} ~ {end_date}",
            "start_date": start_date,
            "end_date": end_date,
            "apply_period": (
                f"{apply_start} ~ {apply_end}" if apply_start and apply_end else ""
            ),
            "apply_start": apply_start,
            "apply_end": apply_end,
            "schedule_raw": schedule,
            "fee": _clean(_first_value(item, "leMoney", "lePrice", "leFee", "fee")),
            "target": _clean(_first_value(item, "leTarget", "leObject", "target")),
            "venue_name": venue or branch,
            "room": venue,
            "instructor": _clean(_first_value(item, "leTeacher", "leLecturer")),
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "waitlist_total": waitlist_total,
            "reservation_available": status in {"OPEN", "WAITLIST"},
            "collection_type": "complete_json_pages+detail_json",
        }
    )
    if row["reservation_available"]:
        row["application_url"] = raw_url
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row["raw_fields"]["clear_application_url"] = True
    row["raw_fields"].update(
        {
            "page_index": page_index,
            "leCode": le_code,
            "leLGCode": le_lg_code,
            "lgLib": library_code,
            "libNameS": _clean(item.get("libNameS")),
            "list_venue": venue,
            "list_schedule": schedule,
            "venue_fallback_to_branch": not bool(venue),
            "reversed_operation_period": end_date < start_date,
            "source_status": source_status,
            "detail_api_url": detail_api_url,
            "stable_identity_pair": [le_code, le_lg_code],
        }
    )
    return row, ""


def _library_detail_data(payload: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    if _clean(payload.get("status")).upper() not in {"OK", "SUCCESS"}:
        return None
    data = payload.get("data")
    return data if isinstance(data, Mapping) else None


def _library_detail(
    row: dict[str, Any], payload: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    raw_fields = row.get("raw_fields", {})
    identity = f"{_clean(raw_fields.get('leCode'))}:{_clean(raw_fields.get('leLGCode'))}"
    data = _library_detail_data(payload)
    if data is None:
        return [f"library {identity}: malformed detail response"]
    detail_le_code = _clean(data.get("leCode"))
    detail_lg_code = _clean(data.get("leLGCode"))
    if (
        detail_le_code != _clean(raw_fields.get("leCode"))
        or detail_lg_code != _clean(raw_fields.get("leLGCode"))
    ):
        errors.append(f"library {identity}: detail identity mismatch")
    detail_title = _clean(data.get("leLName"))
    if _normalized(detail_title) != _normalized(row.get("title")):
        errors.append(f"library {identity}: detail/list title mismatch")
    detail_start = _single_date(data.get("leOpenSDateFmt"))
    detail_end = _single_date(data.get("leOpenEDateFmt"))
    if (
        detail_start != _clean(row.get("start_date"))
        or detail_end != _clean(row.get("end_date"))
    ):
        errors.append(f"library {identity}: detail/list operation period mismatch")
    detail_venue = _clean(data.get("leArea"))
    list_venue = _clean(raw_fields.get("list_venue"))
    if list_venue and _normalized(detail_venue) != _normalized(list_venue):
        errors.append(f"library {identity}: detail/list venue mismatch")
    detail_schedule = _strip_html(
        _first_value(data, "leBeginTime", "leTime", "schedule")
    )
    if detail_schedule and _normalized(detail_schedule) != _normalized(
        row.get("schedule_raw")
    ):
        errors.append(f"library {identity}: detail/list schedule mismatch")

    apply_start = _single_date(data.get("leTakeSDateFmt"))
    apply_end = _single_date(data.get("leTakeEDateFmt"))
    capacity_current, capacity_total, waitlist_total = _library_capacity(data)
    row.update(
        {
            "period": (
                f"{detail_start} ~ {detail_end}"
                if detail_start and detail_end
                else row.get("period")
            ),
            "start_date": detail_start or row.get("start_date"),
            "end_date": detail_end or row.get("end_date"),
            "apply_period": (
                f"{apply_start} ~ {apply_end}"
                if apply_start and apply_end
                else row.get("apply_period")
            ),
            "apply_start": apply_start or row.get("apply_start"),
            "apply_end": apply_end or row.get("apply_end"),
            "schedule_raw": detail_schedule or row.get("schedule_raw"),
            "venue_name": detail_venue or row.get("venue_name"),
            "room": detail_venue or row.get("room"),
            "target": _clean(
                _first_value(data, "leTarget", "leObject", "target")
            )
            or row.get("target"),
            "instructor": _clean(
                _first_value(data, "leTeacher", "leLecturer")
            )
            or row.get("instructor"),
            "capacity_current": (
                capacity_current
                if capacity_current is not None
                else row.get("capacity_current")
            ),
            "capacity_total": (
                capacity_total
                if capacity_total is not None
                else row.get("capacity_total")
            ),
            "waitlist_total": (
                waitlist_total
                if waitlist_total is not None
                else row.get("waitlist_total")
            ),
            "fee": _clean(
                _first_value(data, "leMoney", "lePrice", "leFee", "fee")
            )
            or row.get("fee"),
            "contact": _clean(
                _first_value(data, "leTel", "lePhone", "contact")
            ),
        }
    )
    descriptions = [
        _strip_html(_first_value(data, "leContent", "leContents", "content")),
        _strip_html(_first_value(data, "leNote", "leRemark", "note")),
    ]
    description = " ".join(value for value in descriptions if value)
    if description:
        row["description"] = description
    row["raw_fields"].update(
        {
            "detail_identity_verified": not errors,
            "detail_library_code": _clean(
                _first_value(data, "lgLib", "libCode")
            ).upper(),
        }
    )
    return errors


def _library_logical_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("venue_name")),
    )


def _dedupe_library_tol_za(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    groups: dict[tuple[str, ...], list[int]] = {}
    for index, row in enumerate(rows):
        library_code = _clean(row.get("raw_fields", {}).get("lgLib")).upper()
        if library_code in {"TOL", "ZA"}:
            groups.setdefault(_library_logical_key(row), []).append(index)

    dropped: set[int] = set()
    for indexes in groups.values():
        codes = {
            _clean(rows[index].get("raw_fields", {}).get("lgLib")).upper()
            for index in indexes
        }
        if not {"TOL", "ZA"}.issubset(codes):
            continue
        # ZA is the specific small-library feed; TOL is its aggregate alias.
        preferred = [
            index
            for index in indexes
            if _clean(rows[index].get("raw_fields", {}).get("lgLib")).upper()
            == "ZA"
        ]
        keeper_index = min(
            preferred or indexes,
            key=lambda index: (
                int(_clean(rows[index]["raw_fields"].get("leCode")) or "0"),
                int(_clean(rows[index]["raw_fields"].get("leLGCode")) or "0"),
            ),
        )
        aliases: list[dict[str, str]] = []
        for index in indexes:
            if index == keeper_index:
                continue
            dropped.add(index)
            raw = rows[index].get("raw_fields", {})
            aliases.append(
                {
                    "leCode": _clean(raw.get("leCode")),
                    "leLGCode": _clean(raw.get("leLGCode")),
                    "lgLib": _clean(raw.get("lgLib")),
                }
            )
        rows[keeper_index].setdefault("raw_fields", {})[
            "logical_duplicate_aliases"
        ] = aliases
        rows[keeper_index]["raw_fields"]["logical_dedupe_preferred"] = "ZA"
    return [row for index, row in enumerate(rows) if index not in dropped], len(dropped)


def collect_gangseo_library(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 2000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete Gangseo library API and all retained details."""

    errors: list[str] = []
    if not is_gangseo_library_target(target):
        errors.append(
            "target does not match the exact provider-owned Gangseo library canonical"
        )
    if int(max_pages) < 1:
        errors.append("max_pages must allow at least page 1")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    listed_current: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    pages = 0
    exposed_total = 0
    invalid = 0
    duplicates = 0
    expired_count = 0
    archived_unresolved_count = 0
    archived_reversed_period_count = 0
    declared_total = 0
    declared_pages = 0
    source_cap_reached = False
    primary_session: Any = None

    try:
        if not errors:
            primary_session = make_session()
            try:
                first_payload = _fetch_json(
                    fetch,
                    primary_session,
                    gangseo_library_list_url(1),
                    timeout,
                )
            except Exception as exc:
                errors.append(f"library page 1 fetch {type(exc).__name__}")
                first_payload = None
            if first_payload is not None:
                declaration = _library_page(first_payload)
                current_page, total_pages, total_count, page_size, first_items = declaration
                if (
                    current_page != 1
                    or total_pages is None
                    or total_count is None
                    or page_size != GANGSEO_LIBRARY_PAGE_SIZE
                ):
                    errors.append("library page 1 declaration is missing or malformed")
                else:
                    declared_pages = int(total_pages)
                    declared_total = int(total_count)
                    expected_pages = max(
                        1, math.ceil(declared_total / GANGSEO_LIBRARY_PAGE_SIZE)
                    )
                    if declared_pages != expected_pages:
                        errors.append(
                            f"library declares {declared_pages} pages but total {declared_total} requires {expected_pages}"
                        )
                    allowed_pages = int(max_pages)
                    if declared_pages > allowed_pages:
                        source_cap_reached = True
                        errors.append(
                            f"max_pages cap reached after {allowed_pages} of {declared_pages} library pages"
                        )
                    for page_index in range(1, min(declared_pages, allowed_pages) + 1):
                        if page_index == 1:
                            payload = first_payload
                            items = first_items
                            page_declaration = declaration
                        else:
                            try:
                                payload = _fetch_json(
                                    fetch,
                                    primary_session,
                                    gangseo_library_list_url(page_index),
                                    timeout,
                                )
                            except Exception as exc:
                                errors.append(
                                    f"library page {page_index} fetch {type(exc).__name__}"
                                )
                                break
                            page_declaration = _library_page(payload)
                            items = page_declaration[4]
                        pages += 1
                        if page_declaration[:4] != (
                            page_index,
                            declared_pages,
                            declared_total,
                            GANGSEO_LIBRARY_PAGE_SIZE,
                        ):
                            errors.append(
                                f"library page {page_index} declaration changed"
                            )
                        exposed_total += len(items)
                        expected_rows = min(
                            GANGSEO_LIBRARY_PAGE_SIZE,
                            max(
                                0,
                                declared_total
                                - (page_index - 1) * GANGSEO_LIBRARY_PAGE_SIZE,
                            ),
                        )
                        if len(items) != expected_rows:
                            errors.append(
                                f"library page {page_index} exposed {len(items)} rows; expected {expected_rows}"
                            )
                        for item in items:
                            raw_identity = (
                                _clean(item.get("leCode")),
                                _clean(item.get("leLGCode")),
                            )
                            if raw_identity in seen:
                                duplicates += 1
                                continue
                            seen.add(raw_identity)
                            raw_start = _single_date(item.get("leOpenSDateFmt"))
                            raw_end = _single_date(item.get("leOpenEDateFmt"))
                            raw_end_day: Optional[date] = None
                            if raw_end:
                                try:
                                    raw_end_day = date.fromisoformat(raw_end)
                                except ValueError:
                                    raw_end_day = None
                            if (
                                raw_start
                                and raw_end
                                and raw_end_day is not None
                                and raw_end_day < cutoff
                                and raw_end < raw_start
                            ):
                                archived_reversed_period_count += 1
                                expired_count += 1
                                continue
                            row, row_error = _library_list_row(
                                target, item, page_index=page_index
                            )
                            if row is None:
                                if raw_end_day is not None and raw_end_day < cutoff:
                                    archived_unresolved_count += 1
                                    expired_count += 1
                                    continue
                                invalid += 1
                                if row_error:
                                    errors.append(row_error)
                                continue
                            try:
                                end_date = date.fromisoformat(_clean(row.get("end_date")))
                            except ValueError:
                                invalid += 1
                                continue
                            if end_date < cutoff:
                                expired_count += 1
                                continue
                            if row.get("raw_fields", {}).get(
                                "reversed_operation_period"
                            ):
                                invalid += 1
                                errors.append(
                                    "reversed current library operation period "
                                    f"{raw_identity[0]}:{raw_identity[1]}"
                                )
                                continue
                            listed_current.append(row)
    finally:
        _close_quietly(primary_session)

    if invalid:
        errors.append(f"{invalid} library list rows were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate library identity pairs crossed pages")
    if declared_total != len(seen):
        errors.append(
            f"library declared total {declared_total} does not match {len(seen)} unique identity pairs"
        )
    list_complete = bool(
        not errors
        and pages == declared_pages
        and exposed_total == declared_total
        and declared_pages > 0
        and not invalid
        and not duplicates
    )

    (
        detail_required_count,
        detail_attempts,
        detail_pages,
        detail_errors,
        detail_capped,
    ) = _parallel_details(
        listed_current,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
        parser=_library_detail,
        fetch_json=True,
        url_getter=lambda row: row.get("raw_fields", {}).get("detail_api_url", ""),
        thread_prefix="gangseo-library-detail",
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {detail_attempts} of {detail_required_count} required library detail pages"
        )

    logical_rows, logical_duplicate_count = _dedupe_library_tol_za(listed_current)
    rows = [_clean_row(row) for row in logical_rows]
    rows = _apply_injected_dedupe(rows, dedupe_rows, errors)
    meta = _finish_meta(
        rows=rows,
        pages=pages,
        list_complete=list_complete,
        detail_required_count=detail_required_count,
        detail_attempts=detail_attempts,
        detail_pages=detail_pages,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
        extra={
            "source_url": GANGSEO_LIBRARY_URL,
            "api_url": GANGSEO_LIBRARY_API_URL,
            "provider": GANGSEO_LIBRARY_PROVIDER,
            "parser": GANGSEO_LIBRARY_PARSER,
            "page_size": GANGSEO_LIBRARY_PAGE_SIZE,
            "total_pages": declared_pages,
            "declared_pages": declared_pages,
            "total_count": declared_total,
            "declared_total": declared_total,
            "raw_row_count": exposed_total,
            "unique_count": len(seen),
            "current_future_count_raw": len(listed_current),
            "logical_current_count": len(logical_rows),
            "expired_count": expired_count,
            "archived_unresolved_count": archived_unresolved_count,
            "archived_reversed_period_count": archived_reversed_period_count,
            "invalid_count": invalid,
            "duplicate_count": duplicates,
            "logical_duplicate_count": logical_duplicate_count,
            "deduplicated_count": logical_duplicate_count,
            "detail_identity": "leCode+leLGCode",
            "missing_schedule_count": sum(
                not bool(_clean(row.get("schedule_raw"))) for row in rows
            ),
            "missing_venue_count": sum(
                not bool(_clean(row.get("venue_name"))) for row in rows
            ),
        },
    )
    return rows, GANGSEO_LIBRARY_PARSER, meta


def _invalid_dispatch_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "detail_pages": 0,
        "detail_attempts": 0,
        "detail_required_count": 0,
        "details_complete": True,
        "pagination_complete": False,
        "pagination_exhausted": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "current_count": 0,
        "returned_count": 0,
        "no_current_data": False,
        "configured_collection_error": (
            "target does not match an exact provider-owned Gangseo canonical"
        ),
    }


def collect_gangseo_current_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = 100,
    detail_limit: int = 2000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Dispatch only the two exact Gangseo education canonicals."""

    common = {
        "timeout": timeout,
        "max_pages": max_pages,
        "detail_limit": detail_limit,
        "fetcher": fetcher,
        "session_factory": session_factory,
        "today": today,
        "max_workers": max_workers,
        "dedupe_rows": dedupe_rows,
    }
    if is_gangseo_information_target(target):
        return collect_gangseo_information(target, **common)
    if is_gangseo_library_target(target):
        return collect_gangseo_library(target, **common)
    return [], "", _invalid_dispatch_meta()


collect_gangseo = collect_gangseo_current_education
collect_gangseo_integrated = collect_gangseo_current_education
