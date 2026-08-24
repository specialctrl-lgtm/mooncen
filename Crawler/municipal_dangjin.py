"""Fail-closed collector for Dangjin citizen information education.

The promoted target owns one five-page catalogue.  Generic municipal link
discovery can escape that catalogue through global reservation navigation, so
this collector follows only the reviewed list pagination and course details.
It publishes current/future courses only after the source total, empty
sentinel, page-one stability, detail identities, and required fields agree.
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
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


DANGJIN_PROVIDER = "MUNI_WWW_DANGJIN_GO_KR_3C378AA6"
DANGJIN_CANONICAL_URL = (
    "https://www.dangjin.go.kr/prog/reprsntInfrmEdu/kor/sub05_07_01/list.do"
)
DANGJIN_HOST = "www.dangjin.go.kr"
DANGJIN_LIST_PATH = "/prog/reprsntInfrmEdu/kor/sub05_07_01/list.do"
DANGJIN_DETAIL_PATH = "/prog/reprsntInfrmEdu/kor/sub05_07_01/view.do"
DANGJIN_MUNICIPALITY_CODE = "4427000000"
DANGJIN_MUNICIPALITY_NAME = "충청남도 당진시"
DANGJIN_PARSER = (
    "dangjin_citizen_information_education_complete_pages+empty_sentinel+"
    "stable_recheck+current_details+identity_crosscheck"
)
DANGJIN_MAX_WORKERS = 6
DANGJIN_PAGE_SIZE = 10

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_TOTAL_RE = re.compile(r"총게시물\s*:\s*(\d+)\s*건")
_DATE_RANGE_RE = re.compile(
    r"^\s*(20\d{2})[.-](\d{1,2})[.-](\d{1,2})\s*~\s*"
    r"(20\d{2})[.-](\d{1,2})[.-](\d{1,2})\s*$"
)
_KOREAN_DATE_RANGE_RE = re.compile(
    r"^\s*(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일\s*~\s*"
    r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일\s*$"
)
_TIME_RANGE_RE = re.compile(
    r"^\s*([0-2]?\d):([0-5]\d)\s*~\s*([0-2]?\d):([0-5]\d)\s*$"
)
_CAPACITY_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
_LIST_HEADERS = (
    "번호",
    "교육과목",
    "교육기간",
    "교육시간",
    "접수기간",
    "접수자/정원",
    "상태",
)
_DETAIL_KEYS = (
    "접수기간",
    "교육대상",
    "교육기간",
    "교육시간",
    "교육장소",
    "교육비",
    "전화문의",
    "교육인원",
    "강의계획서",
    "기타",
)
_STATUS_MAP = {
    "접수중": "OPEN",
    "신청중": "OPEN",
    "접수가능": "OPEN",
    "접수예정": "SCHEDULED",
    "신청예정": "SCHEDULED",
    "접수완료": "CLOSED",
    "신청완료": "CLOSED",
    "접수마감": "CLOSED",
    "교육완료": "CLOSED",
}


class DangjinContractError(ValueError):
    """The official source no longer matches the reviewed contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_dangjin_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")).upper() != DANGJIN_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == DANGJIN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == DANGJIN_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_dangjin_target


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": DANGJIN_CANONICAL_URL,
        }
    )
    return current


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    response = current.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise DangjinContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise DangjinContractError("redirect response is not accepted")
    if not getattr(response, "content", b""):
        raise DangjinContractError("empty HTTP response")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("HTML fetcher returned neither HTML nor a response")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _page_url(page_index: int) -> str:
    if page_index == 1:
        return DANGJIN_CANONICAL_URL
    return f"{DANGJIN_CANONICAL_URL}?{urlencode({'pageIndex': page_index})}"


def _validate_ownership(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "당진시청" not in title or "시민정보화교육" not in title:
        raise DangjinContractError("official page title changed")


def _parse_date_range(value: Any) -> tuple[date, date]:
    text = _clean(value)
    match = _DATE_RANGE_RE.fullmatch(text) or _KOREAN_DATE_RANGE_RE.fullmatch(text)
    if not match:
        raise DangjinContractError(f"unexpected date range: {text!r}")
    parts = [int(part) for part in match.groups()]
    start = date(parts[0], parts[1], parts[2])
    end = date(parts[3], parts[4], parts[5])
    if start > end:
        raise DangjinContractError(f"reversed date range: {text!r}")
    return start, end


def _normalize_time_range(value: Any) -> str:
    text = _clean(value)
    match = _TIME_RANGE_RE.fullmatch(text)
    if not match:
        raise DangjinContractError(f"unexpected education time: {text!r}")
    start_hour, start_minute, end_hour, end_minute = (int(part) for part in match.groups())
    if start_hour > 23 or end_hour > 23:
        raise DangjinContractError(f"invalid education time: {text!r}")
    return (
        f"{start_hour:02d}:{start_minute:02d} ~ "
        f"{end_hour:02d}:{end_minute:02d}"
    )


def _detail_identity(value: Any) -> tuple[str, str]:
    parsed = urlparse(urljoin(DANGJIN_CANONICAL_URL, _clean(value)))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != DANGJIN_HOST
        or parsed.path != DANGJIN_DETAIL_PATH
        or len(query) != 1
        or query[0][0] != "schedule_seq"
        or not query[0][1].isdigit()
        or parsed.params
        or parsed.fragment
    ):
        raise DangjinContractError("course detail identity changed")
    return query[0][1], parsed.geturl()


def _find_list_table(soup: BeautifulSoup) -> Any:
    for table in soup.select("table"):
        caption = _clean(
            table.caption.get_text(" ", strip=True) if table.caption else ""
        )
        headers = tuple(
            _clean(node.get_text(" ", strip=True))
            for node in table.select("thead tr:first-child th")
        )
        if "시민정보화교육 목록" in caption or headers == _LIST_HEADERS:
            if headers != _LIST_HEADERS:
                raise DangjinContractError(
                    f"list headers changed: {headers!r}"
                )
            return table
    raise DangjinContractError("official course list table is missing")


def _source_total(soup: BeautifulSoup) -> int:
    total_node = soup.select_one(".board_total")
    text = _clean(total_node.get_text(" ", strip=True) if total_node else "")
    match = _TOTAL_RE.search(text)
    if not match:
        raise DangjinContractError("source total is missing")
    return int(match.group(1))


def _parse_list_page(
    soup: BeautifulSoup,
    page_index: int,
) -> tuple[list[dict[str, Any]], int]:
    _validate_ownership(soup)
    source_total = _source_total(soup)
    table = _find_list_table(soup)
    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        if len(cells) == 1 and "등록된" in _clean(cells[0].get_text(" ", strip=True)):
            continue
        if len(cells) != len(_LIST_HEADERS):
            raise DangjinContractError(
                f"page {page_index} row width changed: {len(cells)}"
            )
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        if not values[0].isdigit():
            raise DangjinContractError(
                f"page {page_index} sequence changed: {values[0]!r}"
            )
        anchor = cells[1].select_one("a[href]")
        if anchor is None or _clean(anchor.get_text(" ", strip=True)) != values[1]:
            raise DangjinContractError("course title/detail link changed")
        schedule_seq, detail_url = _detail_identity(anchor.get("href"))
        start_date, end_date = _parse_date_range(values[2])
        apply_start, apply_end = _parse_date_range(values[4])
        schedule_raw = _normalize_time_range(values[3])
        capacity_match = _CAPACITY_RE.fullmatch(values[5])
        if not capacity_match:
            raise DangjinContractError(
                f"unexpected capacity: {values[5]!r}"
            )
        capacity_current, capacity_total = (
            int(part) for part in capacity_match.groups()
        )
        if capacity_current > capacity_total:
            raise DangjinContractError("current applications exceed capacity")
        status = _STATUS_MAP.get(values[6])
        if status is None:
            raise DangjinContractError(f"unexpected source status: {values[6]!r}")
        rows.append(
            {
                "source_sequence": int(values[0]),
                "schedule_seq": schedule_seq,
                "title": values[1],
                "period": f"{start_date.isoformat()} ~ {end_date.isoformat()}",
                "start_date": start_date,
                "end_date": end_date,
                "schedule_raw": schedule_raw,
                "apply_period": (
                    f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
                ),
                "apply_start": apply_start,
                "apply_end": apply_end,
                "capacity": f"{capacity_current}/{capacity_total}",
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "capacity_remaining": capacity_total - capacity_current,
                "status": status,
                "status_raw": values[6],
                "raw_url": detail_url,
                "category_raw": "시민정보화교육",
                "source_page": page_index,
            }
        )
    return rows, source_total


def _list_fingerprint(rows: Iterable[Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            row["schedule_seq"],
            row["title"],
            row["start_date"],
            row["end_date"],
            row["schedule_raw"],
            row["apply_start"],
            row["apply_end"],
            row["capacity_current"],
            row["capacity_total"],
            row["status"],
        )
        for row in rows
    )


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    _validate_ownership(soup)
    table = soup.select_one("#content table.basic_table")
    if table is None:
        raise DangjinContractError("course detail table is missing")
    pairs: dict[str, str] = {}
    for tr in table.select("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(cells):
            if cells[index].name != "th" or index + 1 >= len(cells):
                raise DangjinContractError("course detail row structure changed")
            key = _clean(cells[index].get_text(" ", strip=True))
            value_node = cells[index + 1]
            if value_node.name != "td" or key in pairs:
                raise DangjinContractError("course detail labels changed")
            pairs[key] = _clean(value_node.get_text(" ", strip=True))
            index += 2
    if tuple(pairs) != _DETAIL_KEYS:
        raise DangjinContractError(
            f"course detail keys changed: {tuple(pairs)!r}"
        )
    return pairs


def _detail_title(soup: BeautifulSoup) -> str:
    for heading in soup.select("#content h2"):
        value = _clean(heading.get_text(" ", strip=True))
        if value and value != "정보 변경 내역":
            return value
    raise DangjinContractError("course detail title is missing")


def _normalize_fee(value: Any) -> str:
    text = _clean(value)
    if not text or text in {"없음", "0", "0원", "무료"}:
        # This official catalogue defines citizen information courses as free;
        # some rows leave the education-fee cell empty while others say 없음.
        return "무료"
    return text


def _parse_detail(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
) -> dict[str, Any]:
    pairs = _detail_pairs(soup)
    if _detail_title(soup) != listed["title"]:
        raise DangjinContractError("course detail title does not match the list")
    start_date, end_date = _parse_date_range(pairs["교육기간"])
    apply_start, apply_end = _parse_date_range(pairs["접수기간"])
    schedule_raw = _normalize_time_range(pairs["교육시간"])
    if (
        start_date != listed["start_date"]
        or end_date != listed["end_date"]
        or apply_start != listed["apply_start"]
        or apply_end != listed["apply_end"]
        or schedule_raw != listed["schedule_raw"]
    ):
        raise DangjinContractError("course list/detail schedule mismatch")
    if not pairs["교육인원"].isdigit():
        raise DangjinContractError("course detail capacity changed")
    if int(pairs["교육인원"]) != listed["capacity_total"]:
        raise DangjinContractError("course list/detail capacity mismatch")
    target = pairs["교육대상"]
    venue = pairs["교육장소"]
    if not target or not venue:
        raise DangjinContractError("course detail target or venue is missing")
    branch_digest = hashlib.sha256(venue.encode("utf-8")).hexdigest()[:12].upper()
    return {
        **listed,
        "branch": venue,
        "branch_name": venue,
        "branch_code": f"DANGJIN_INFO_EDU_{branch_digest}",
        "venue_name": venue,
        "target": target,
        "fee": _normalize_fee(pairs["교육비"]),
        "contact": pairs["전화문의"],
        "description": pairs["기타"],
        "category": "디지털·사진",
        "category_raw": "시민정보화교육",
        "municipality_code": DANGJIN_MUNICIPALITY_CODE,
        "municipality_name": DANGJIN_MUNICIPALITY_NAME,
    }


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("schedule_seq"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "detail_pages": 0,
        "detail_attempts": 0,
        "list_rechecks": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "pagination_exhausted": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "page_cap_reached": False,
        "detail_cap_reached": False,
        "recursion_depth": 0,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": DANGJIN_MUNICIPALITY_CODE,
        "municipality_name": DANGJIN_MUNICIPALITY_NAME,
        "canonical_url": DANGJIN_CANONICAL_URL,
    }


def collect_dangjin_information_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = DANGJIN_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future Dangjin information-course snapshot."""

    meta = _base_meta()
    if not is_dangjin_target(target):
        meta["configured_collection_error"] = (
            "target is not the exact Dangjin canonical provider/URL"
        )
        return [], DANGJIN_PARSER, meta
    try:
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        workers = max(1, min(int(max_workers or 1), DANGJIN_MAX_WORKERS))
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "collection limits are invalid"
        return [], DANGJIN_PARSER, meta

    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    cutoff = _today(today)
    sessions: list[Any] = []
    errors: list[str] = []

    def new_session() -> Any:
        value = current_factory()
        sessions.append(value)
        return value

    def fetch_soup(current: Any, url: str) -> BeautifulSoup:
        return _coerce_soup(current_fetcher(current, url, timeout))

    base_session = new_session()
    source_rows: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    collected: list[dict[str, Any]] = []
    first_fingerprint: tuple[Any, ...] = ()
    try:
        try:
            first_rows, source_total = _parse_list_page(
                fetch_soup(base_session, DANGJIN_CANONICAL_URL),
                1,
            )
            meta["source_total"] = source_total
            expected_pages = max(1, math.ceil(source_total / DANGJIN_PAGE_SIZE))
            required_pages = expected_pages + 1
            meta["pagination_detected"] = expected_pages > 1
            if page_cap < required_pages:
                meta["source_cap_reached"] = True
                meta["page_cap_reached"] = True
                raise DangjinContractError(
                    f"max_pages cap {page_cap} is below required {required_pages}"
                )
            source_rows.extend(first_rows)
            first_fingerprint = _list_fingerprint(first_rows)
            for page_index in range(2, expected_pages + 1):
                page_rows, page_total = _parse_list_page(
                    fetch_soup(base_session, _page_url(page_index)),
                    page_index,
                )
                if page_total != source_total:
                    raise DangjinContractError("source total changed across pages")
                source_rows.extend(page_rows)
            sentinel_rows, sentinel_total = _parse_list_page(
                fetch_soup(base_session, _page_url(expected_pages + 1)),
                expected_pages + 1,
            )
            meta["pages"] = required_pages
            if sentinel_total != source_total or sentinel_rows:
                raise DangjinContractError("immediate empty page sentinel changed")
            if len(source_rows) != source_total:
                raise DangjinContractError(
                    f"source total mismatch {len(source_rows)} != {source_total}"
                )
            identities = [row["schedule_seq"] for row in source_rows]
            sequences = [row["source_sequence"] for row in source_rows]
            if len(set(identities)) != source_total:
                raise DangjinContractError("duplicate schedule identity in source")
            if len(set(sequences)) != source_total:
                raise DangjinContractError("duplicate source sequence in source")
        except Exception as exc:
            errors.append(f"list: {type(exc).__name__}: {_clean(exc)}")

        meta["source_rows"] = len(source_rows)
        current_rows = [
            row for row in source_rows if row["end_date"] >= cutoff
        ]
        meta["current_count"] = len(current_rows)
        meta["expired_count"] = len(source_rows) - len(current_rows)
        if detail_cap < len(current_rows):
            meta["source_cap_reached"] = True
            meta["detail_cap_reached"] = True
            errors.append(
                f"detail_limit cap {detail_cap} is below required {len(current_rows)}"
            )

        if not errors and current_rows:
            meta["detail_attempts"] = len(current_rows)

            def fetch_detail(row: Mapping[str, Any]) -> dict[str, Any]:
                current = new_session()
                try:
                    return _parse_detail(
                        fetch_soup(current, str(row["raw_url"])),
                        row,
                    )
                finally:
                    _close_quietly(current)

            by_identity: dict[str, dict[str, Any]] = {}
            future_to_row = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for row in current_rows:
                    future_to_row[pool.submit(fetch_detail, row)] = row
                for future in as_completed(future_to_row):
                    row = future_to_row[future]
                    try:
                        by_identity[row["schedule_seq"]] = future.result()
                        meta["detail_pages"] += 1
                    except Exception as exc:
                        errors.append(
                            f"detail {row['schedule_seq']}: "
                            f"{type(exc).__name__}: {_clean(exc)}"
                        )
            collected = [
                by_identity[row["schedule_seq"]]
                for row in current_rows
                if row["schedule_seq"] in by_identity
            ]

        if source_rows and not errors:
            try:
                recheck_rows, recheck_total = _parse_list_page(
                    fetch_soup(base_session, DANGJIN_CANONICAL_URL),
                    1,
                )
                meta["list_rechecks"] = 1
                if (
                    recheck_total != meta["source_total"]
                    or _list_fingerprint(recheck_rows) != first_fingerprint
                ):
                    raise DangjinContractError(
                        "course list changed during traversal"
                    )
            except Exception as exc:
                errors.append(f"recheck: {type(exc).__name__}: {_clean(exc)}")

        if not errors:
            try:
                deduped = list(current_dedupe(collected))
                if len(deduped) != len(collected):
                    raise DangjinContractError(
                        f"dedupe changed complete count "
                        f"{len(collected)} to {len(deduped)}"
                    )
                collected = deduped
            except Exception as exc:
                errors.append(f"dedupe: {type(exc).__name__}: {_clean(exc)}")

        details_complete = (
            not meta["detail_cap_reached"]
            and meta["detail_attempts"] == len(current_rows)
            and meta["detail_pages"] == len(current_rows)
        )
        snapshot_complete = (
            not errors
            and not meta["source_cap_reached"]
            and meta["list_rechecks"] == 1
            and len(source_rows) == meta["source_total"]
            and details_complete
            and len(collected) == len(current_rows)
        )
        if not snapshot_complete:
            collected = []
        meta.update(
            {
                "returned_count": len(collected),
                "status_counts": dict(
                    Counter(row["status"] for row in collected)
                ),
                "pagination_complete": snapshot_complete,
                "pagination_exhausted": snapshot_complete,
                "details_complete": details_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": snapshot_complete,
                "network_concurrency": workers,
                "no_current_data": snapshot_complete and not current_rows,
                "no_current_reason": (
                    "all official Dangjin citizen information courses have ended"
                    if snapshot_complete and not current_rows
                    else ""
                ),
            }
        )
        if errors:
            meta["configured_collection_error"] = "; ".join(
                dict.fromkeys(errors)
            )
        return collected, DANGJIN_PARSER, meta
    finally:
        for current in sessions:
            _close_quietly(current)


collect_dangjin_target = collect_dangjin_information_courses
collect = collect_dangjin_information_courses


__all__ = [
    "DANGJIN_CANONICAL_URL",
    "DANGJIN_DETAIL_PATH",
    "DANGJIN_HOST",
    "DANGJIN_LIST_PATH",
    "DANGJIN_MAX_WORKERS",
    "DANGJIN_MUNICIPALITY_CODE",
    "DANGJIN_MUNICIPALITY_NAME",
    "DANGJIN_PAGE_SIZE",
    "DANGJIN_PARSER",
    "DANGJIN_PROVIDER",
    "DangjinContractError",
    "collect",
    "collect_dangjin_information_courses",
    "collect_dangjin_target",
    "is_dangjin_target",
    "is_target",
]
