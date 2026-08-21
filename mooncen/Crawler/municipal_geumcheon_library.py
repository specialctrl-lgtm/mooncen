"""Fail-closed collector for Geumcheon-gu Library education programs.

The provider follows the municipal target convention used by
``tools/promote_municipal_integrated_reservation_targets.py``: the canonical
URL's host plus the first eight uppercase hexadecimal characters of its SHA-1.
For ``GEUMCHEON_LIBRARY_URL`` that is
``MUNI_GEUMCHEONLIB_SEOUL_KR_E6151FD4``.

This module is intentionally isolated from ``Crawler_MunicipalYaml``.  A
parent crawler can inject its managed HTTP session, HTML fetch helper, and row
deduplicator without creating a circular import.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import ipaddress
import math
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GEUMCHEON_LIBRARY_PROVIDER = "MUNI_GEUMCHEONLIB_SEOUL_KR_E6151FD4"
GEUMCHEON_LIBRARY_URL = (
    "https://geumcheonlib.seoul.kr/geumcheonlib/uce/programList.do?selfId=1090"
)
GEUMCHEON_LIBRARY_HOST = "geumcheonlib.seoul.kr"
GEUMCHEON_LIBRARY_LIST_PATH = "/geumcheonlib/uce/programList.do"
GEUMCHEON_LIBRARY_DETAIL_PATH = "/geumcheonlib/uce/programDetail.do"
GEUMCHEON_LIBRARY_SELF_ID = "1090"
GEUMCHEON_LIBRARY_INST_CD = "INST0000"
GEUMCHEON_LIBRARY_INST_GB = "1"
GEUMCHEON_LIBRARY_BBS_ID = "PGM_000000000001"
# Keep each official list response below the shared SafeSession 20-second
# connect/read ceiling.  The source accepts this bounded page size and declares
# all seven pages for the current 3,127-row corpus, so completeness remains
# independently reconcilable without weakening the outbound HTTP guard.
GEUMCHEON_LIBRARY_PAGE_SIZE = 500
GEUMCHEON_LIBRARY_PARSER = "geumcheon_library_current_future_complete+detail"
GEUMCHEON_LIBRARY_MUNICIPALITY_CODE = "1154500000"
GEUMCHEON_LIBRARY_MUNICIPALITY_NAME = "서울특별시 금천구"
GEUMCHEON_LIBRARY_MAX_WORKERS = 8

# The class is the source's stable institution marker.  It is deliberately
# used for ``branch`` instead of the detail's room/venue text (for example,
# "2층 강의실" or "온라인 ZOOM").
GEUMCHEON_LIBRARY_BRANCHES: Mapping[str, str] = {
    "ds": "독산도서관",
    "gs": "가산도서관",
    "gnr": "금나래도서관",
    "sh": "시흥도서관",
    "s_1": "책이든거리작은도서관",
    "s_2": "참새작은도서관",
    "s_3": "청개구리작은도서관",
    "s_4": "꿈씨어린이작은도서관",
    "s_5": "도란도란작은도서관",
    "s_6": "해오름작은도서관",
    "s_7": "미래향기작은도서관",
    "s_8": "맑은누리작은도서관",
    "s_9": "꿈꾸는작은도서관",
    "s_10": "행궁마을작은도서관",
    "s_11": "책달샘숲속작은도서관",
    "s_12": "금천가산퍼블릭 디자인작은도서관",
}
GEUMCHEON_LIBRARY_LOCATIONS: Mapping[str, Mapping[str, Any]] = {
    "s_6": {
        "address": "서울특별시 금천구 시흥대로123길 11, 4층",
        "lat": 37.47019,
        "lon": 126.89702,
        "source_url": (
            "https://geumcheonlib.seoul.kr/geumcheonlib/uce/content/"
            "contentList.do?selfId=1123"
        ),
    },
}

GEUMCHEON_LIBRARY_STATUS_MAP: Mapping[str, str] = {
    "준비중": "SCHEDULED",
    "접수중": "OPEN",
    "대기신청": "WAITING",
    "접수마감": "CLOSED",
    "강좌진행": "CLOSED",
    "강좌종료": "CLOSED",
}
GEUMCHEON_LIBRARY_DETAIL_STATUSES = frozenset(
    status for status in GEUMCHEON_LIBRARY_STATUS_MAP if status != "강좌종료"
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DECLARATION_RE = re.compile(
    r"전체\s*([\d,]+)\s*개\s*\(\s*페이지\s*(\d+)\s*/\s*(\d+)\s*\)"
)
_DATE_RE = re.compile(
    r"(?<!\d)(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)"
)
_DATETIME_RE = re.compile(
    r"(?<!\d)(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
    r"(?:\s+(\d{1,2}):(\d{2}))?"
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def is_geumcheon_library_target(target: Any) -> bool:
    """Match only the exact provider-owned canonical route."""

    return (
        _provider(target) == GEUMCHEON_LIBRARY_PROVIDER
        and _target_url(target) == GEUMCHEON_LIBRARY_URL
    )


is_target = is_geumcheon_library_target


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


def _default_fetcher(current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        raise ValueError("empty HTTP response")
    return BeautifulSoup(response.content, "lxml")


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher did not return HTML or BeautifulSoup")
    return BeautifulSoup(content, "lxml")


def _fetch(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def geumcheon_library_list_url(page_no: int) -> str:
    query = urlencode(
        (
            ("selfId", GEUMCHEON_LIBRARY_SELF_ID),
            ("pageSize", str(GEUMCHEON_LIBRARY_PAGE_SIZE)),
            ("pageNo", str(max(1, int(page_no)))),
        )
    )
    return f"https://{GEUMCHEON_LIBRARY_HOST}{GEUMCHEON_LIBRARY_LIST_PATH}?{query}"


def geumcheon_library_detail_url(program_id: str) -> str:
    identity = _clean(program_id)
    if not identity.isdigit():
        return ""
    query = urlencode(
        (
            ("headerId", ""),
            ("selfId", GEUMCHEON_LIBRARY_SELF_ID),
            ("INST_CD", GEUMCHEON_LIBRARY_INST_CD),
            ("INST_GB", GEUMCHEON_LIBRARY_INST_GB),
            ("idxNo", identity),
            ("bbsId", GEUMCHEON_LIBRARY_BBS_ID),
        )
    )
    return f"https://{GEUMCHEON_LIBRARY_HOST}{GEUMCHEON_LIBRARY_DETAIL_PATH}?{query}"


def _safe_official_detail_url(value: Any, program_id: str) -> str:
    candidate = _clean(value)
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() != GEUMCHEON_LIBRARY_HOST
        or parsed.path != GEUMCHEON_LIBRARY_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return ""
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    ):
        return ""
    expected = {
        "headerId": [""],
        "selfId": [GEUMCHEON_LIBRARY_SELF_ID],
        "INST_CD": [GEUMCHEON_LIBRARY_INST_CD],
        "INST_GB": [GEUMCHEON_LIBRARY_INST_GB],
        "idxNo": [_clean(program_id)],
        "bbsId": [GEUMCHEON_LIBRARY_BBS_ID],
    }
    if parse_qs(parsed.query, keep_blank_values=True) != expected:
        return ""
    return candidate


def _page_declaration(soup: BeautifulSoup) -> Optional[tuple[int, int, int]]:
    node = soup.select_one(".board_page")
    match = _DECLARATION_RE.search(_clean(node.get_text(" ", strip=True) if node else ""))
    if not match:
        return None
    return (
        int(match.group(1).replace(",", "")),
        int(match.group(2)),
        int(match.group(3)),
    )


def _date_range(value: Any) -> tuple[str, str, str]:
    tokens = _DATE_RE.findall(_clean(value))
    if len(tokens) < 2:
        return "", "", ""
    try:
        start = date(*(int(part) for part in tokens[0]))
        end = date(*(int(part) for part in tokens[1]))
    except ValueError:
        return "", "", ""
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), _clean(value)


def _datetime_range(value: Any) -> tuple[str, str]:
    tokens = _DATETIME_RE.findall(_clean(value))
    if len(tokens) < 2:
        return "", ""
    normalized: list[str] = []
    for year, month, day, hour, minute in tokens[:2]:
        try:
            stamp = datetime(
                int(year), int(month), int(day), int(hour or 0), int(minute or 0)
            )
        except ValueError:
            return "", ""
        normalized.append(stamp.strftime("%Y-%m-%d %H:%M"))
    if normalized[1] < normalized[0]:
        return "", ""
    return normalized[0], normalized[1]


def _capacity(value: Any) -> tuple[Optional[int], Optional[int]]:
    match = re.fullmatch(r"\s*([\d,]+)\s*/\s*([\d,]+)\s*", _clean(value))
    if not match:
        return None, None
    return int(match.group(1).replace(",", "")), int(
        match.group(2).replace(",", "")
    )


def _stable_branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"GEUMCHEONLIB_{digest}"


def _branch_from_anchor(anchor: Any) -> tuple[str, str]:
    classes = [_clean(value) for value in (anchor.get("class") or [])]
    markers = [value for value in classes if value in GEUMCHEON_LIBRARY_BRANCHES]
    if len(markers) != 1:
        return "", ""
    marker = markers[0]
    return GEUMCHEON_LIBRARY_BRANCHES[marker], marker


def _base_row(
    target: Any,
    *,
    program_id: str,
    title: str,
    branch: str,
    branch_class: str,
    detail_url: str,
) -> dict[str, Any]:
    provider = _provider(target)
    row = {
        "provider": provider,
        "provider_course_id": f"{provider}:program:{program_id}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(title),
        "branch": _clean(branch),
        "branch_code": _stable_branch_code(branch),
        "preserve_branch": True,
        "branch_url": GEUMCHEON_LIBRARY_URL,
        "program_type": "강좌",
        "category": "교육·강좌",
        "raw_url": detail_url,
        "reservation_available": False,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "municipality_code": GEUMCHEON_LIBRARY_MUNICIPALITY_CODE,
        "municipality_full_name": GEUMCHEON_LIBRARY_MUNICIPALITY_NAME,
        "collection_type": "complete_numbered_pages+detail_html",
        "raw_fields": {
            "parser": GEUMCHEON_LIBRARY_PARSER,
            "program_id": program_id,
            "bbs_id": GEUMCHEON_LIBRARY_BBS_ID,
            "branch_class": branch_class,
            "detail_required": True,
            "detail_valid": False,
        },
    }
    location = GEUMCHEON_LIBRARY_LOCATIONS.get(branch_class)
    if location:
        address = _clean(location.get("address"))
        row.update(
            {
                "address": address,
                "venue_address": address,
                "branch_address_source": "OFFICIAL_GEUMCHEON_LIBRARY_DIRECTORY",
                "branch_lat": location.get("lat"),
                "branch_lon": location.get("lon"),
                "branch_coordinate_source": "NAVER_LOCAL_SEARCH",
                "branch_location_confidence": 100,
                "branch_location_verified": True,
                "branch_location_query": _clean(location.get("source_url")),
            }
        )
    return row


def _list_records(
    target: Any,
    soup: BeautifulSoup,
    *,
    page_no: int,
) -> tuple[list[dict[str, Any]], int, int]:
    body = soup.select_one(".notice_wrap.pro_table table.board tbody")
    if body is None:
        return [], 1, 0
    records: list[dict[str, Any]] = []
    invalid = 0
    exposed = 0
    for tr in body.find_all("tr", recursive=False):
        cells = tr.find_all("td", recursive=False)
        if not cells and not _clean(tr.get_text(" ", strip=True)):
            continue
        exposed += 1
        title_cell = tr.select_one("td.notice_title")
        anchor = title_cell.select_one("a[name='go_detail']") if title_cell else None
        id_node = title_cell.select_one("input[id='PGM_IDX']") if title_cell else None
        bbs_node = title_cell.select_one("input[id='PGM_BBS_ID']") if title_cell else None
        program_id = _clean(id_node.get("value") if id_node else "")
        bbs_id = _clean(bbs_node.get("value") if bbs_node else "")
        title = _clean(anchor.get_text(" ", strip=True) if anchor else "")
        status_raw = _clean(cells[-1].get_text(" ", strip=True) if cells else "")
        if (
            len(cells) != 7
            or not program_id.isdigit()
            or not title
            or bbs_id != GEUMCHEON_LIBRARY_BBS_ID
            or status_raw not in GEUMCHEON_LIBRARY_STATUS_MAP
        ):
            invalid += 1
            continue
        record: dict[str, Any] = {
            "program_id": program_id,
            "source_status": status_raw,
            "title": title,
            "page_no": page_no,
        }
        if status_raw in GEUMCHEON_LIBRARY_DETAIL_STATUSES:
            branch, branch_class = _branch_from_anchor(anchor)
            target_text = _clean(cells[1].get_text(" ", strip=True))
            apply_period_raw = _clean(cells[2].get_text(" ", strip=True))
            capacity_raw = _clean(cells[3].get_text(" ", strip=True))
            fee = _clean(cells[4].get_text(" ", strip=True))
            material_fee = _clean(cells[5].get_text(" ", strip=True))
            apply_start, apply_end = _datetime_range(apply_period_raw)
            capacity_current, capacity_total = _capacity(capacity_raw)
            detail_url = geumcheon_library_detail_url(program_id)
            if (
                not branch
                or not branch_class
                or not target_text
                or not apply_start
                or not apply_end
                or capacity_current is None
                or capacity_total is None
                or not fee
                or not material_fee
                or not detail_url
            ):
                invalid += 1
                continue
            row = _base_row(
                target,
                program_id=program_id,
                title=title,
                branch=branch,
                branch_class=branch_class,
                detail_url=detail_url,
            )
            row.update(
                {
                    "status": GEUMCHEON_LIBRARY_STATUS_MAP[status_raw],
                    "target": target_text,
                    "apply_period": apply_period_raw,
                    "apply_start": apply_start,
                    "apply_end": apply_end,
                    "capacity": capacity_raw,
                    "capacity_current": capacity_current,
                    "capacity_total": capacity_total,
                    "capacity_remaining": max(0, capacity_total - capacity_current),
                    "fee": fee,
                    "material_fee": material_fee,
                }
            )
            row["raw_fields"].update(
                {
                    "source_status": status_raw,
                    "page_no": page_no,
                    "list_apply_period": apply_period_raw,
                    "list_target": target_text,
                    "list_capacity": capacity_raw,
                    "list_fee": fee,
                    "list_material_fee": material_fee,
                }
            )
            record["row"] = row
        records.append(record)
    return records, invalid, exposed


def _pairs_from_table(table: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if table is None:
        return pairs
    for tr in table.select("tr"):
        key = ""
        for cell in tr.find_all(["th", "td"], recursive=False):
            if cell.name == "th":
                key = _clean(cell.get_text(" ", strip=True))
            elif key:
                pairs[key] = _clean(cell.get_text(" ", strip=True))
                key = ""
    return pairs


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    for table in soup.select("table"):
        pairs = _pairs_from_table(table)
        if "강좌명" in pairs and "상태" in pairs and "모집기간" in pairs:
            return pairs
    return {}


def _application_control(soup: BeautifulSoup) -> Any:
    controls = []
    for node in soup.select("a.part2"):
        text = _clean(node.get_text(" ", strip=True))
        href = _clean(node.get("href"))
        onclick = _clean(node.get("onclick"))
        if (
            text == "수강신청"
            and href.lower() == "javascript:void(0);"
            and re.search(r"\bfn_select_Usr\s*\(", onclick)
        ):
            controls.append(node)
    return controls[0] if len(controls) == 1 else None


def _description(soup: BeautifulSoup) -> str:
    box = soup.select_one(".borderBox")
    if box is None:
        return ""
    text = _clean(box.get_text(" ", strip=True))
    return _clean(text.partition("강사소개")[0])


def _enrich_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    raw_fields = row.setdefault("raw_fields", {})
    identity = _clean(raw_fields.get("program_id"))
    pairs = _detail_pairs(soup)
    required = (
        "강좌명",
        "상태",
        "강좌장소",
        "대상",
        "모집정원",
        "강좌기간",
        "강좌시간",
        "모집기간",
        "수강료",
        "교재 및 재료비",
    )
    missing = [key for key in required if not _clean(pairs.get(key))]
    if missing:
        errors.append(f"program {identity}: missing detail fields {','.join(missing)}")

    detail_title = _clean(pairs.get("강좌명"))
    detail_status = _clean(pairs.get("상태"))
    if detail_title != _clean(row.get("title")):
        errors.append(f"program {identity}: detail/list title mismatch")
    if detail_status != _clean(raw_fields.get("source_status")):
        errors.append(f"program {identity}: detail/list status mismatch")
    if detail_status not in GEUMCHEON_LIBRARY_STATUS_MAP:
        errors.append(f"program {identity}: unknown detail status")

    detail_target = _clean(pairs.get("대상"))
    if detail_target != _clean(row.get("target")):
        errors.append(f"program {identity}: detail/list target mismatch")
    detail_apply_start, detail_apply_end = _datetime_range(pairs.get("모집기간"))
    if (detail_apply_start, detail_apply_end) != (
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
    ):
        errors.append(f"program {identity}: detail/list application period mismatch")
    detail_current, detail_total = _capacity(pairs.get("모집정원"))
    if (detail_current, detail_total) != (
        row.get("capacity_current"),
        row.get("capacity_total"),
    ):
        errors.append(f"program {identity}: detail/list capacity mismatch")
    if _clean(pairs.get("수강료")) != _clean(row.get("fee")):
        errors.append(f"program {identity}: detail/list fee mismatch")
    if _clean(pairs.get("교재 및 재료비")) != _clean(row.get("material_fee")):
        errors.append(f"program {identity}: detail/list material fee mismatch")

    start_date, end_date, period = _date_range(pairs.get("강좌기간"))
    if not start_date or not end_date:
        errors.append(f"program {identity}: malformed course period")
    else:
        row.update(
            {
                "start_date": start_date,
                "end_date": end_date,
                "period": period,
                "schedule_raw": _clean(pairs.get("강좌시간")),
                "room": _clean(pairs.get("강좌장소")),
                "venue_name": _clean(pairs.get("강좌장소")),
            }
        )
        sessions_match = re.search(r"\(([\d,]+)\s*회\)", _clean(pairs.get("강좌기간")))
        if sessions_match:
            row["sessions"] = int(sessions_match.group(1).replace(",", ""))
    instructor = _clean(pairs.get("강사"))
    if instructor:
        row["instructor"] = instructor
    description = _description(soup)
    if description:
        row["description"] = description

    source_status = _clean(raw_fields.get("source_status"))
    open_for_application = source_status in {"접수중", "대기신청"}
    control = _application_control(soup)
    safe_detail_url = _safe_official_detail_url(row.get("raw_url"), identity)
    if open_for_application:
        if control is None:
            errors.append(f"program {identity}: open status has no exact application control")
        if not safe_detail_url:
            errors.append(f"program {identity}: application destination is not safe official detail")
        if control is not None and safe_detail_url:
            row["reservation_available"] = True
            row["application_url"] = safe_detail_url
            row["application_type"] = "ONLINE_RESERVATION"
    else:
        if control is not None:
            errors.append(f"program {identity}: closed/scheduled status exposes application control")
        row["reservation_available"] = False
        row.pop("application_url", None)
        raw_fields["clear_application_url"] = True

    raw_fields.update(
        {
            "detail_pairs": pairs,
            "application_control": control is not None,
            "detail_valid": not errors,
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
) -> tuple[int, int, int, list[str], bool]:
    required_count = len(rows)
    allowed = max(0, int(detail_limit))
    selected = rows[:allowed]
    capped = len(selected) < required_count
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
        identity = _clean(row.get("raw_fields", {}).get("program_id"))
        try:
            soup = _fetch(fetcher, current_session(), _clean(row.get("raw_url")), timeout)
        except Exception as exc:
            return False, [f"program {identity}: detail fetch {type(exc).__name__}"]
        return True, _enrich_detail(row, soup)

    results: list[tuple[bool, list[str]]] = []
    try:
        if selected:
            workers = min(
                GEUMCHEON_LIBRARY_MAX_WORKERS,
                max(1, int(max_workers)),
                len(selected),
            )
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="geumcheon-library-detail"
            ) as pool:
                results = list(pool.map(enrich, selected))
    finally:
        for value in sessions:
            _close_quietly(value)
    fetched = sum(success for success, _item_errors in results)
    errors = [error for _success, item_errors in results for error in item_errors]
    return required_count, len(selected), fetched, errors, capped


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if value not in (None, "", [], {})
    }


def _meta(
    *,
    rows: list[dict[str, Any]],
    pages: int,
    declared_total: int,
    declared_pages: int,
    discovered: int,
    candidate_count: int,
    ended_count: int,
    expired_count: int,
    invalid_count: int,
    duplicate_count: int,
    status_counts: Mapping[str, int],
    list_complete: bool,
    detail_required_count: int,
    detail_attempts: int,
    detail_pages: int,
    detail_errors: list[str],
    source_cap_reached: bool,
    errors: list[str],
) -> dict[str, Any]:
    unique_errors = list(dict.fromkeys([*errors, *detail_errors]))
    details_complete = (
        detail_attempts == detail_required_count
        and detail_pages == detail_required_count
        and not detail_errors
    )
    snapshot_complete = list_complete and details_complete and not unique_errors
    no_current_data = snapshot_complete and not rows
    result: dict[str, Any] = {
        "pages": pages,
        "declared_pages": declared_pages,
        "detail_pages": detail_pages,
        "detail_attempts": detail_attempts,
        "detail_required_count": detail_required_count,
        "required_detail_count": detail_required_count,
        "detail_errors": len(detail_errors),
        "pagination_detected": declared_pages > 1,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "full_snapshot_required": True,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "total_count": declared_total,
        "discovered_links": discovered,
        "candidate_count": candidate_count,
        "ended_count": ended_count,
        "expired_count": expired_count,
        "invalid_count": invalid_count,
        "duplicate_count": duplicate_count,
        "current_count": len(rows),
        "source_status_counts": dict(status_counts),
        "branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
        "no_current_data": no_current_data,
        "no_current_reason": (
            "official non-ended/current-future library education list is empty"
            if no_current_data
            else ""
        ),
    }
    if unique_errors:
        shown = unique_errors[:50]
        message = "; ".join(shown)
        if len(unique_errors) > len(shown):
            message += f"; ... {len(unique_errors) - len(shown)} more errors"
        result["configured_collection_error"] = message
    return result


def collect_geumcheon_library_courses(
    target: Any,
    timeout: int = 25,
    max_pages: int = 10,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete official list and enrich every non-ended program.

    The list declaration and every official ID must reconcile before
    ``snapshot_complete`` can be true.  Only detail-validated courses whose
    end date is on or after the KST cutoff are returned.
    """

    errors: list[str] = []
    if not is_geumcheon_library_target(target):
        errors.append("target does not match the provider-owned canonical Geumcheon library route")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    status_counts: Counter[str] = Counter()
    pages = 0
    declared_total = 0
    declared_pages = 0
    exposed_total = 0
    invalid = 0
    duplicates = 0
    ended_count = 0
    source_cap_reached = False
    primary_session: Any = None

    try:
        if not errors:
            primary_session = make_session()
            try:
                first_soup = _fetch(
                    fetch, primary_session, geumcheon_library_list_url(1), timeout
                )
            except Exception as exc:
                errors.append(f"page 1 fetch {type(exc).__name__}")
                first_soup = None
            if first_soup is not None:
                declaration = _page_declaration(first_soup)
                if declaration is None:
                    errors.append("page 1 total/page declaration is missing or malformed")
                    declared_pages = 1
                else:
                    declared_total, current_page, declared_pages = declaration
                    if current_page != 1:
                        errors.append(f"first page declares current page {current_page}")
                    expected_pages = max(
                        1, math.ceil(declared_total / GEUMCHEON_LIBRARY_PAGE_SIZE)
                    )
                    if declared_pages != expected_pages:
                        errors.append(
                            f"source declares {declared_pages} pages for {declared_total} rows; expected {expected_pages}"
                        )
                allowed_pages = max(1, int(max_pages))
                if declared_pages > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap reached after {allowed_pages} of {declared_pages} declared pages"
                    )
                for page_no in range(1, min(declared_pages, allowed_pages) + 1):
                    if page_no == 1:
                        soup = first_soup
                    else:
                        try:
                            soup = _fetch(
                                fetch,
                                primary_session,
                                geumcheon_library_list_url(page_no),
                                timeout,
                            )
                        except Exception as exc:
                            errors.append(f"page {page_no} fetch {type(exc).__name__}")
                            break
                    pages += 1
                    if _page_declaration(soup) != (
                        declared_total,
                        page_no,
                        declared_pages,
                    ):
                        errors.append(f"page {page_no} total/page declaration changed")
                    records, page_invalid, page_exposed = _list_records(
                        target, soup, page_no=page_no
                    )
                    invalid += page_invalid
                    exposed_total += page_exposed
                    expected_rows = min(
                        GEUMCHEON_LIBRARY_PAGE_SIZE,
                        max(
                            0,
                            declared_total
                            - ((page_no - 1) * GEUMCHEON_LIBRARY_PAGE_SIZE),
                        ),
                    )
                    if page_exposed != expected_rows:
                        errors.append(
                            f"page {page_no} exposed {page_exposed} rows; expected {expected_rows}"
                        )
                    for record in records:
                        identity = _clean(record.get("program_id"))
                        if identity in seen:
                            duplicates += 1
                            continue
                        seen.add(identity)
                        source_status = _clean(record.get("source_status"))
                        status_counts[source_status] += 1
                        if source_status == "강좌종료":
                            ended_count += 1
                            continue
                        row = record.get("row")
                        if isinstance(row, dict):
                            candidates.append(row)
    finally:
        _close_quietly(primary_session)

    if invalid:
        errors.append(f"{invalid} program list rows were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate official program IDs crossed pages")
    if len(seen) != declared_total:
        errors.append(
            f"declared total {declared_total} does not match {len(seen)} unique program IDs"
        )
    if exposed_total != declared_total:
        errors.append(
            f"declared total {declared_total} does not match {exposed_total} exposed rows"
        )
    list_complete = (
        not errors
        and pages == declared_pages
        and len(seen) == declared_total
        and exposed_total == declared_total
        and invalid == 0
        and duplicates == 0
    )

    (
        detail_required_count,
        detail_attempts,
        detail_pages,
        detail_errors,
        detail_capped,
    ) = _parallel_details(
        candidates,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {detail_attempts} of {detail_required_count} required detail pages"
        )

    expired_count = 0
    valid_rows: list[dict[str, Any]] = []
    for row in candidates:
        if row.get("raw_fields", {}).get("detail_valid") is not True:
            continue
        end_text = _clean(row.get("end_date"))
        try:
            end_date = date.fromisoformat(end_text)
        except ValueError:
            continue
        if end_date < cutoff:
            expired_count += 1
            continue
        valid_rows.append(_clean_row(row))

    if dedupe_rows is not None:
        try:
            valid_rows = list(dedupe_rows(valid_rows))
        except Exception as exc:
            errors.append(f"dedupe_rows {type(exc).__name__}")

    meta = _meta(
        rows=valid_rows,
        pages=pages,
        declared_total=declared_total,
        declared_pages=declared_pages,
        discovered=len(seen),
        candidate_count=len(candidates),
        ended_count=ended_count,
        expired_count=expired_count,
        invalid_count=invalid,
        duplicate_count=duplicates,
        status_counts=status_counts,
        list_complete=list_complete,
        detail_required_count=detail_required_count,
        detail_attempts=detail_attempts,
        detail_pages=detail_pages,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
    )
    return valid_rows, GEUMCHEON_LIBRARY_PARSER, meta


collect_geumcheon_library_target = collect_geumcheon_library_courses


__all__ = [
    "GEUMCHEON_LIBRARY_BBS_ID",
    "GEUMCHEON_LIBRARY_BRANCHES",
    "GEUMCHEON_LIBRARY_DETAIL_PATH",
    "GEUMCHEON_LIBRARY_HOST",
    "GEUMCHEON_LIBRARY_LIST_PATH",
    "GEUMCHEON_LIBRARY_PAGE_SIZE",
    "GEUMCHEON_LIBRARY_PARSER",
    "GEUMCHEON_LIBRARY_PROVIDER",
    "GEUMCHEON_LIBRARY_URL",
    "collect_geumcheon_library_courses",
    "collect_geumcheon_library_target",
    "geumcheon_library_detail_url",
    "geumcheon_library_list_url",
    "is_geumcheon_library_target",
    "is_target",
]
