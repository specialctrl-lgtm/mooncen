"""Complete current/future collector for Suwon's integrated reservation catalogue.

The public list has two relevant server-side status scopes (72 and 73).  The
first column is a continuous descending source number and therefore acts as a
declared total for each scope.  This collector verifies that sequence, accepts
both official identity shapes (``seqNo`` and ``eduMstSeq``), filters by the
education end date, and requires every returned detail page before publishing
a snapshot.  The official ``q_categoryCode=81`` ledger is reconciled against
the same complete provider snapshot so its ``답사·체험`` rows are classified at
course level without splitting provider identity or stale-write ownership.

Twelve historical rows currently contain an official, reversed application
period.  They are already ended courses and are counted as source defects, but
do not invalidate the current snapshot.  The same defect on a current/future
row is fail-closed.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
from html import unescape
import math
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


SUWON_PROVIDER = "SUWON_RESERV_EDUCATION"
SUWON_URL = "https://www.suwon.go.kr/web/reserv/edu/list.do"
SUWON_HOST = "www.suwon.go.kr"
SUWON_LIST_PATH = "/web/reserv/edu/list.do"
SUWON_DETAIL_PATH = "/web/reserv/edu/view.do"
SUWON_APPLICATION_PATH = "/web/reserv/edu/reservForm.do"
SUWON_STATUS_SCOPES = ("72", "73")
SUWON_EXPERIENCE_CATEGORY_CODE = "81"
SUWON_EXPERIENCE_CATEGORY_NAME = "답사·체험"
SUWON_PAGE_SIZE = 100
SUWON_MAX_DETAIL_WORKERS = 8
SUWON_PARSER = "suwon_integrated_education_complete_current_future+detail"
SUWON_MUNICIPALITY_CODE = "4111000000"
SUWON_MUNICIPALITY_NAME = "경기도 수원시"
SUWON_DISTRICT_MUNICIPALITIES = {
    "장안구": ("4111100000", "경기도 수원시 장안구"),
    "권선구": ("4111300000", "경기도 수원시 권선구"),
    "팔달구": ("4111500000", "경기도 수원시 팔달구"),
    "영통구": ("4111700000", "경기도 수원시 영통구"),
}

# Some official course details name an exact venue/institution but omit the map
# address. Keep this allowlist deliberately narrow and pair-bound. The two
# locations were checked against current Suwon-owned public pages:
# - 수원여성인력개발센터: 수원시 영통구 반달로7번길 40
# - 영흥수목원: 수원시 영통구 영통로 435
_SUWON_EXACT_LOCATION_DISTRICTS = {
    ("수원여성인력개발센터", "수원여성인력개발센터"): (
        "영통구",
        "https://www.suwon.go.kr/web/board/BD_board.view.do?bbsCd=1042&seq=20260223143504437",
    ),
    ("영흥수목원", "영흥수목원"): (
        "영통구",
        "https://www.suwon.go.kr/sw-www/sw-visitsuwon/sw-visitsuwon-01/sw-visitsuwon-01-06/sw-visitsuwon-01-06-02.jsp",
    ),
}

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_APPLICATION_CONTROL_RE = re.compile(
    r"\s*jsForm\(\s*['\"](?P<identity>\d+)['\"]\s*,\s*"
    r"['\"](?P<status>[A-Z]{2})['\"]\s*\)\s*;?\s*"
)
_LOCATION_COORDINATE_RE = re.compile(
    r"\bvar\s+latitude\s*=\s*(?P<lat>-?\d+(?:\.\d+)?)\s*,"
    r".*?\blongitude\s*=\s*(?P<lon>-?\d+(?:\.\d+)?)\s*;",
    re.DOTALL,
)
_LOCATION_ADDRESS_RE = re.compile(
    r"<em>\s*주소\s*:\s*</em>\s*"
    r"<span[^>]*class\s*=\s*['\"]text['\"][^>]*>"
    r"(?P<address>.*?)</span>",
    re.DOTALL | re.IGNORECASE,
)
_STATUS_MAP = {
    "접수중": "OPEN",
    "대기접수": "WAITLIST",
    "접수준비": "SCHEDULED",
    "접수마감": "CLOSED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _venue_name(value: Any) -> str:
    return _clean(re.sub(r"^\s*\d+\s*[.)]\s*", "", _clean(value)))


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_suwon_reservation_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == SUWON_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SUWON_HOST
        and parsed.port is None
        and parsed.path == SUWON_LIST_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_suwon_reservation_target


def suwon_list_url(status_code: Any, page: Any) -> str:
    status = _clean(status_code)
    raw_page = _clean(page)
    if (
        status not in SUWON_STATUS_SCOPES
        or not _IDENTITY_RE.fullmatch(raw_page)
        or int(raw_page) < 1
    ):
        return ""
    return f"https://{SUWON_HOST}{SUWON_LIST_PATH}?" + urlencode(
        {
            "q_rowPerPage": SUWON_PAGE_SIZE,
            "q_progressStatusCd": status,
            "q_currPage": int(raw_page),
        }
    )


def suwon_category_list_url(
    status_code: Any,
    page: Any,
    category_code: Any = SUWON_EXPERIENCE_CATEGORY_CODE,
) -> str:
    """Build the one audited course-category ledger URL.

    Category 81 is the official ``답사·체험`` filter.  Restricting this helper
    to that exact code prevents an arbitrary query from becoming category
    evidence.
    """

    status = _clean(status_code)
    raw_page = _clean(page)
    category = _clean(category_code)
    if (
        status not in SUWON_STATUS_SCOPES
        or category != SUWON_EXPERIENCE_CATEGORY_CODE
        or not _IDENTITY_RE.fullmatch(raw_page)
        or int(raw_page) < 1
    ):
        return ""
    return f"https://{SUWON_HOST}{SUWON_LIST_PATH}?" + urlencode(
        {
            "q_rowPerPage": SUWON_PAGE_SIZE,
            "q_progressStatusCd": status,
            "q_categoryCode": category,
            "q_currPage": int(raw_page),
        }
    )


def suwon_detail_url(identity_kind: Any, identity: Any) -> str:
    kind = _clean(identity_kind)
    raw_identity = _clean(identity)
    if kind not in {"seqNo", "eduMstSeq"} or not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{SUWON_HOST}{SUWON_DETAIL_PATH}?" + urlencode(
        {kind: raw_identity}
    )


def suwon_application_url(identity: Any, status_type: Any = "AA") -> str:
    raw_identity = _clean(identity)
    raw_status_type = _clean(status_type)
    if (
        not _IDENTITY_RE.fullmatch(raw_identity)
        or raw_status_type not in {"AA", "SB"}
    ):
        return ""
    return f"https://{SUWON_HOST}{SUWON_APPLICATION_PATH}?" + urlencode(
        {"seqNo": raw_identity, "statusType": raw_status_type}
    )


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
    return current_session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 200))
    if 300 <= status < 400:
        raise ValueError("HTTP redirects are not accepted")
    raise_for_status = getattr(value, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise ValueError("empty HTTP response")
    return BeautifulSoup(content, "lxml")


def _fetch(
    fetcher: Fetcher,
    current_session: Any,
    url: str,
    timeout: int,
) -> BeautifulSoup:
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            result.append(
                date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            )
        except ValueError:
            continue
    return result


def _format_range(start: date, end: date) -> str:
    return f"{start.isoformat()} ~ {end.isoformat()}"


def _capacity(value: Any) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    numbers = [int(token.replace(",", "")) for token in re.findall(r"\d[\d,]*", _clean(value))]
    if len(numbers) < 2:
        return None, None, None, None
    return (
        numbers[0],
        numbers[1],
        numbers[2] if len(numbers) > 2 else None,
        numbers[3] if len(numbers) > 3 else None,
    )


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"SUWON_EDU_BRANCH_{digest}"


def _clean_location_address(value: Any, venue_name: str) -> str:
    address = _clean(unescape(str(value or "")))
    if not address:
        return ""

    venue = _clean(venue_name)
    if venue and address.endswith(venue):
        address = _clean(address[: -len(venue)])

    # The official popup sometimes concatenates a facility label immediately
    # after a complete parenthesized address.
    match = re.match(
        r"^(?P<address>.+?\([^()]+\))(?P<label>[가-힣A-Za-z].+)$",
        address,
    )
    if match and re.search(r"\d", match.group("address")):
        address = _clean(match.group("address"))
    return address


def _official_map_location(
    soup: BeautifulSoup,
) -> tuple[str, str, Optional[float], Optional[float]]:
    location_node = soup.select_one(".location_text")
    venue_name = (
        _venue_name(location_node.get_text(" ", strip=True))
        if location_node is not None
        else ""
    )
    container = location_node.find_parent("td") if location_node is not None else None
    script_nodes = (container or soup).find_all("script")
    script_text = "\n".join(
        str(node.string or node.get_text(" ", strip=False) or "")
        for node in script_nodes
    )

    address_match = _LOCATION_ADDRESS_RE.search(script_text)
    address = _clean_location_address(
        address_match.group("address") if address_match else "",
        venue_name,
    )

    coordinate_match = _LOCATION_COORDINATE_RE.search(script_text)
    if not coordinate_match:
        return venue_name, address, None, None
    try:
        lat = float(coordinate_match.group("lat"))
        lon = float(coordinate_match.group("lon"))
    except (TypeError, ValueError):
        return venue_name, address, None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return venue_name, address, None, None
    return venue_name, address, lat, lon


def _propagate_official_locations(
    rows: list[dict[str, Any]],
) -> list[str]:
    evidence: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for row in rows:
        branch_code = _clean(row.get("branch_code"))
        lat = row.get("branch_lat")
        lon = row.get("branch_lon")
        if not branch_code or lat is None or lon is None:
            continue
        current = {
            "address": _clean(row.get("branch_address")),
            "lat": float(lat),
            "lon": float(lon),
            "query": _clean(row.get("branch_location_query")),
        }
        previous = evidence.get(branch_code)
        if previous and (
            abs(previous["lat"] - current["lat"]) > 0.00001
            or abs(previous["lon"] - current["lon"]) > 0.00001
        ):
            errors.append(
                f"{branch_code}: conflicting official venue coordinates"
            )
            continue
        if previous:
            if len(current["address"]) > len(previous["address"]):
                previous["address"] = current["address"]
            if len(current["query"]) > len(previous["query"]):
                previous["query"] = current["query"]
        else:
            evidence[branch_code] = current

    for row in rows:
        location = evidence.get(_clean(row.get("branch_code")))
        if not location or row.get("branch_lat") is not None:
            continue
        address = location["address"]
        if address:
            row.update(
                {
                    "address": address,
                    "branch_address": address,
                    "venue_address": address,
                    "branch_address_source": "SUWON_OFFICIAL_DETAIL_MAP",
                }
            )
        row.update(
            {
                "branch_lat": location["lat"],
                "branch_lon": location["lon"],
                "branch_coordinate_source": "SUWON_OFFICIAL_DETAIL_MAP",
                "branch_location_confidence": 100,
                "branch_location_verified": True,
                "branch_location_query": location["query"]
                or address
                or _clean(row.get("venue_name")),
            }
        )
        raw = row.get("raw_fields")
        if isinstance(raw, dict):
            raw["official_location_inherited"] = True
    return errors


def _assign_suwon_municipality(row: dict[str, Any]) -> tuple[str, str]:
    """Assign one exact Suwon district from structured official evidence.

    A single district token in the official detail-map address wins. For the
    two current exact venue/institution pairs whose course pages omit an
    address, the audited Suwon-owned location registry above is used. Any
    missing, changed, or ambiguous evidence deliberately remains at parent
    Suwon instead of guessing from titles or free text.
    """

    address = _clean(row.get("venue_address"))
    matched_districts = [
        district
        for district in SUWON_DISTRICT_MUNICIPALITIES
        if district in address
    ]
    evidence_source_url = ""
    if len(matched_districts) == 1:
        district = matched_districts[0]
        evidence_kind = "official_detail_map_address"
        evidence_value = address
    else:
        venue = _venue_name(row.get("venue_name") or row.get("room"))
        institution = _clean(row.get("provider_organizer"))
        exact_location = _SUWON_EXACT_LOCATION_DISTRICTS.get(
            (venue, institution)
        )
        if not matched_districts and exact_location:
            district, evidence_source_url = exact_location
            evidence_kind = "official_exact_venue_institution_registry"
            evidence_value = f"{venue}|{institution}"
        else:
            district = ""
            evidence_kind = (
                "conservative_parent_ambiguous_address"
                if len(matched_districts) > 1
                else "conservative_parent_no_exact_district_evidence"
            )
            evidence_value = address or "|".join(
                value
                for value in (
                    _venue_name(row.get("venue_name") or row.get("room")),
                    _clean(row.get("provider_organizer")),
                )
                if value
            )

    if district:
        municipality_code, municipality_full_name = (
            SUWON_DISTRICT_MUNICIPALITIES[district]
        )
    else:
        municipality_code = SUWON_MUNICIPALITY_CODE
        municipality_full_name = SUWON_MUNICIPALITY_NAME

    row.update(
        {
            "municipality_code": municipality_code,
            "municipality_full_name": municipality_full_name,
            "municipality_region_verified": True,
        }
    )
    raw = row.get("raw_fields")
    if isinstance(raw, dict):
        raw["municipality_resolution"] = {
            "kind": evidence_kind,
            "evidence": evidence_value,
            "municipality_code": municipality_code,
            "municipality_full_name": municipality_full_name,
            **(
                {"source_url": evidence_source_url}
                if evidence_source_url
                else {}
            ),
        }
    return municipality_code, evidence_kind


def _parse_identity(current_url: str, link: Any) -> tuple[str, str, str]:
    parsed = urlparse(urljoin(current_url, _clean(link.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    seq_no = _clean((query.get("seqNo") or [""])[0])
    edu_mst_seq = _clean((query.get("eduMstSeq") or [""])[0])
    identities = [("seqNo", seq_no), ("eduMstSeq", edu_mst_seq)]
    present = [(kind, identity) for kind, identity in identities if identity]
    if (
        parsed.scheme.lower() not in {"", "https"}
        or (parsed.hostname and parsed.hostname.lower() != SUWON_HOST)
        or parsed.path != SUWON_DETAIL_PATH
        or len(present) != 1
        or not _IDENTITY_RE.fullmatch(present[0][1])
    ):
        return "", "", ""
    kind, identity = present[0]
    return kind, identity, suwon_detail_url(kind, identity)


def _parse_list_page(
    target: Any,
    soup: BeautifulSoup,
    *,
    status_code: str,
    page: int,
    category_code: str = "",
) -> tuple[list[dict[str, Any]], int]:
    current_url = (
        suwon_category_list_url(status_code, page, category_code)
        if category_code
        else suwon_list_url(status_code, page)
    )
    rows: list[dict[str, Any]] = []
    malformed = 0
    for tr in soup.select("table tbody tr"):
        link = tr.select_one("a.title[href*='/reserv/edu/view.do']")
        if link is None:
            continue
        cells = [
            _clean(cell.get_text(" ", strip=True))
            for cell in tr.find_all("td", recursive=False)
        ]
        kind, identity, raw_url = _parse_identity(current_url, link)
        title = _clean(link.get_text(" ", strip=True))
        link_category_values = [
            _clean(value)
            for value in parse_qs(
                urlparse(urljoin(current_url, _clean(link.get("href")))).query,
                keep_blank_values=True,
            ).get("q_categoryCode", [])
            if _clean(value)
        ]
        source_number = int(cells[0]) if cells and cells[0].isdigit() else 0
        dates = _date_tokens(cells[2] if len(cells) > 2 else "")
        raw_status = _clean(cells[7] if len(cells) > 7 else "")
        if (
            len(cells) < 8
            or not source_number
            or not title
            or not raw_url
            or (category_code and link_category_values != [category_code])
            or len(dates) != 4
            or raw_status not in _STATUS_MAP
        ):
            malformed += 1
            continue
        apply_start, apply_end, start, end = dates
        if end < start:
            malformed += 1
            continue
        apply_valid = apply_end >= apply_start
        capacity_current, capacity_total, wait_current, wait_total = _capacity(cells[5])
        venue = _venue_name(cells[6])
        row: dict[str, Any] = {
            "provider": _provider(target),
            "provider_course_id": f"{_provider(target)}:{'seq' if kind == 'seqNo' else 'edu'}:{identity}"[:100],
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "program_type": "교육·강좌",
            "category": "교육·강좌",
            "branch": _clean(_target_value(target, "branch")) or "수원시 통합예약",
            "branch_code": "SUWON_RESERV_EDUCATION",
            "branch_url": SUWON_URL,
            "preserve_branch": True,
            "raw_url": raw_url,
            "status": _STATUS_MAP[raw_status],
            "period": _format_range(start, end),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "schedule_raw": cells[3],
            "target": cells[4],
            "room": venue,
            "venue_name": venue,
            "capacity": cells[5],
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "waitlist_current": wait_current,
            "waitlist_total": wait_total,
            "reservation_available": False,
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": SUWON_MUNICIPALITY_CODE,
            "municipality_full_name": SUWON_MUNICIPALITY_NAME,
            "collection_type": "complete_status_scopes+detail_html",
            "description": _clean(" ".join(cells[2:8])),
            "raw_fields": {
                "parser": SUWON_PARSER,
                "identity_kind": kind,
                "identity": identity,
                "source_number": source_number,
                "status_code": status_code,
                "source_status": raw_status,
                "application_period_valid": apply_valid,
                "list_venue": venue,
                "list_cells": cells,
                **(
                    {
                        "official_category_filter": {
                            "code": category_code,
                            "name": SUWON_EXPERIENCE_CATEGORY_NAME,
                            "list_url": current_url,
                            "source_number": source_number,
                        }
                    }
                    if category_code
                    else {}
                ),
            },
        }
        if apply_valid:
            row.update(
                {
                    "apply_period": _format_range(apply_start, apply_end),
                    "apply_start": apply_start.isoformat(),
                    "apply_end": apply_end.isoformat(),
                }
            )
        else:
            row["raw_fields"].update(
                {
                    "invalid_apply_start": apply_start.isoformat(),
                    "invalid_apply_end": apply_end.isoformat(),
                }
            )
        rows.append(row)
    return rows, malformed


def _is_official_empty_list(soup: BeautifulSoup) -> bool:
    rows = soup.select("table tbody tr")
    if len(rows) != 1:
        return False
    cell = rows[0].select_one("td.no-data[colspan='8']")
    return bool(
        cell is not None
        and _clean(cell.get_text(" ", strip=True)) == "데이터가 존재하지 않습니다."
    )


def _apply_experience_category_inventory(
    rows: list[dict[str, Any]],
    category_rows: list[dict[str, Any]],
) -> list[str]:
    """Reconcile category 81 with the unfiltered canonical provider ledger."""

    errors: list[str] = []
    rows_by_identity = {
        _clean(row.get("provider_course_id")): row
        for row in rows
        if _clean(row.get("provider_course_id"))
    }
    category_by_identity: dict[str, dict[str, Any]] = {}
    for category_row in category_rows:
        provider_course_id = _clean(category_row.get("provider_course_id"))
        if not provider_course_id or provider_course_id in category_by_identity:
            errors.append(
                f"category {SUWON_EXPERIENCE_CATEGORY_CODE}: duplicate or empty identity"
            )
            continue
        category_by_identity[provider_course_id] = category_row
        canonical = rows_by_identity.get(provider_course_id)
        if canonical is None:
            errors.append(
                f"category {SUWON_EXPERIENCE_CATEGORY_CODE}: identity {provider_course_id} missing from canonical ledger"
            )
            continue
        canonical_cells = [
            _clean(value)
            for value in canonical.get("raw_fields", {}).get("list_cells", [])[1:]
        ]
        category_cells = [
            _clean(value)
            for value in category_row.get("raw_fields", {}).get("list_cells", [])[1:]
        ]
        if not canonical_cells or canonical_cells != category_cells:
            errors.append(
                f"category {SUWON_EXPERIENCE_CATEGORY_CODE}: identity {provider_course_id} row mismatch"
            )

    if errors:
        return errors

    for provider_course_id, row in rows_by_identity.items():
        category_row = category_by_identity.get(provider_course_id)
        raw = row.get("raw_fields")
        if not isinstance(raw, dict):
            errors.append(f"{provider_course_id}: missing raw fields for category evidence")
            continue
        if category_row is None:
            raw["official_experience_category"] = {
                "code": SUWON_EXPERIENCE_CATEGORY_CODE,
                "name": SUWON_EXPERIENCE_CATEGORY_NAME,
                "matched": False,
            }
            continue
        category_filter = category_row.get("raw_fields", {}).get(
            "official_category_filter",
            {},
        )
        if (
            category_filter.get("code") != SUWON_EXPERIENCE_CATEGORY_CODE
            or category_filter.get("name") != SUWON_EXPERIENCE_CATEGORY_NAME
        ):
            errors.append(f"{provider_course_id}: invalid official category evidence")
            continue
        row.update(
            {
                "program_type": "체험",
                "category": SUWON_EXPERIENCE_CATEGORY_NAME,
                "domain_category": "체험·견학",
                "service_group": "체험",
            }
        )
        raw["official_experience_category"] = {
            **category_filter,
            "matched": True,
            "identity_kind": category_row["raw_fields"]["identity_kind"],
            "identity": category_row["raw_fields"]["identity"],
            "canonical_row_verified": True,
            "detail_title_verified": False,
        }
    return errors


def _pairs(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for tr in soup.select("table tr"):
        pending = ""
        for cell in tr.find_all(["th", "td"], recursive=False):
            if cell.name == "th":
                pending = _clean(cell.get_text(" ", strip=True))
            elif pending:
                value = _clean(cell.get_text(" ", strip=True))
                if pending not in result or not result[pending]:
                    result[pending] = value
                pending = ""
    return result


def _application_control(
    soup: BeautifulSoup,
    identity: str,
    expected_status_type: str,
) -> tuple[str, bool]:
    saw_js_form = False
    for element in soup.select("[onclick]"):
        onclick = _clean(element.get("onclick"))
        if "jsForm" not in onclick:
            continue
        saw_js_form = True
        match = _APPLICATION_CONTROL_RE.fullmatch(onclick)
        if (
            match
            and match.group("identity") == identity
            and match.group("status") == expected_status_type
        ):
            return suwon_application_url(identity, expected_status_type), True
    return "", saw_js_form


def _detail_errors(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    raw = row["raw_fields"]
    identity = _clean(raw.get("identity"))
    kind = _clean(raw.get("identity_kind"))
    errors: list[str] = []
    titles = {_clean(node.get_text(" ", strip=True)) for node in soup.select(".title")}
    detail_title_verified = _clean(row.get("title")) in titles
    if not detail_title_verified:
        errors.append(f"{kind} {identity}: detail/list title mismatch")
    category_evidence = raw.get("official_experience_category")
    if isinstance(category_evidence, dict) and category_evidence.get("matched") is True:
        category_evidence["detail_title_verified"] = detail_title_verified

    pairs = _pairs(soup)
    required_keys = ("접수방법", "교육일정", "접수기간", "교육기관", "교육장소")
    missing_keys = [key for key in required_keys if key not in pairs]
    if missing_keys:
        errors.append(f"{kind} {identity}: missing detail keys {','.join(missing_keys)}")

    period_dates = _date_tokens(pairs.get("교육일정"))
    apply_dates = _date_tokens(pairs.get("접수기간"))
    if len(period_dates) != 2 or period_dates[1] < period_dates[0]:
        errors.append(f"{kind} {identity}: invalid detail education period")
    elif _format_range(period_dates[0], period_dates[1]) != _clean(row.get("period")):
        errors.append(f"{kind} {identity}: detail/list education period mismatch")
    if len(apply_dates) != 2 or apply_dates[1] < apply_dates[0]:
        errors.append(f"{kind} {identity}: invalid detail application period")
    elif _format_range(apply_dates[0], apply_dates[1]) != _clean(row.get("apply_period")):
        errors.append(f"{kind} {identity}: detail/list application period mismatch")

    institution = _clean(pairs.get("교육기관"))
    detail_venue = _clean(pairs.get("교육장소"))
    list_venue = _venue_name(raw.get("list_venue"))
    official_venue, venue_address, venue_lat, venue_lon = _official_map_location(
        soup
    )
    venue = official_venue or list_venue or detail_venue
    if not institution:
        errors.append(f"{kind} {identity}: empty education institution")
    if (
        not detail_venue
        or (list_venue and list_venue not in detail_venue)
        or (official_venue and list_venue and official_venue != list_venue)
    ):
        errors.append(f"{kind} {identity}: detail/list venue mismatch")
    if not venue:
        errors.append(f"{kind} {identity}: empty education venue")

    reservable_status = row.get("status") in {"OPEN", "WAITLIST"}
    expected_status_type = "SB" if row.get("status") == "WAITLIST" else "AA"
    application_url, saw_js_form = _application_control(
        soup,
        identity,
        expected_status_type,
    )
    if kind == "seqNo" and reservable_status and not application_url:
        errors.append(f"{kind} {identity}: reservable course has no canonical application control")
    if saw_js_form and not application_url:
        errors.append(f"{kind} {identity}: mismatched or malformed application control")

    row.update(
        {
            "branch": venue or row.get("branch"),
            "branch_code": (
                _branch_code(venue)
                if venue
                else row.get("branch_code")
            ),
            "provider_organizer": institution,
            "venue_name": venue,
            "room": venue,
            "application_method_raw": pairs.get("접수방법", ""),
            "target": _clean(pairs.get("교육대상")) or row.get("target"),
            "schedule_raw": _clean(
                " ".join(
                    value
                    for value in (pairs.get("교육요일"), pairs.get("교육시간"))
                    if value
                )
            )
            or row.get("schedule_raw"),
            "fee": _clean(pairs.get("비용") or pairs.get("수강료")),
            "material_fee": _clean(pairs.get("재료비") or pairs.get("수강료 안내")),
            "phone": _clean(pairs.get("문의처") or pairs.get("담당자")),
            "reservation_available": bool(application_url and reservable_status),
        }
    )
    basic_info = dict(row.get("basic_info") or {})
    basic_info.update(
        {
            "location_role": "course_venue",
            "education_institution": institution,
        }
    )
    row["basic_info"] = basic_info
    if venue_address:
        row.update(
            {
                "address": venue_address,
                "branch_address": venue_address,
                "venue_address": venue_address,
                "branch_address_source": "SUWON_OFFICIAL_DETAIL_MAP",
            }
        )
    if venue_lat is not None and venue_lon is not None:
        row.update(
            {
                "branch_lat": venue_lat,
                "branch_lon": venue_lon,
                "branch_coordinate_source": "SUWON_OFFICIAL_DETAIL_MAP",
                "branch_location_confidence": 100,
                "branch_location_verified": True,
                "branch_location_query": venue_address or venue,
            }
        )
    if application_url and reservable_status:
        row["application_url"] = application_url
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        raw["clear_application_url"] = True
    raw.update(
        {
            "detail_pairs": pairs,
            "education_institution": institution,
            "official_location": {
                key: value
                for key, value in {
                    "venue_name": official_venue,
                    "venue_address": venue_address,
                    "lat": venue_lat,
                    "lon": venue_lon,
                }.items()
                if value not in (None, "")
            },
            "canonical_application_control": bool(application_url),
        }
    )
    return errors


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
        "detail_pages": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "current_count": 0,
        "returned_count": 0,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_suwon_reservation_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = 10,
    detail_limit: int = 300,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 6,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one all-or-nothing current/future Suwon provider snapshot."""

    if not is_suwon_reservation_target(target):
        return [], SUWON_PARSER, _failure("target does not match the canonical Suwon provider route")
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], SUWON_PARSER, _failure("managed fetcher and session_factory injection are required")
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory
    assert fetcher is not None
    assert session_factory is not None

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    source_totals: dict[str, int] = {}
    source_pages: dict[str, int] = {}
    category_rows: list[dict[str, Any]] = []
    category_totals: dict[str, int] = {}
    category_pages: dict[str, int] = {}
    category_errors: list[str] = []
    first_pages: dict[str, BeautifulSoup] = {}
    list_requests = 0
    category_requests = 0
    malformed_count = 0
    source_cap_reached = False

    list_session: Any = None
    try:
        list_session = session_factory()
        for status_code in SUWON_STATUS_SCOPES:
            if list_requests >= allowed_pages:
                source_cap_reached = True
                errors.append("max_pages cap cannot inspect every official status scope")
                break
            soup = _fetch(
                fetcher,
                list_session,
                suwon_list_url(status_code, 1),
                timeout,
            )
            list_requests += 1
            first_pages[status_code] = soup
            parsed_rows, malformed = _parse_list_page(
                target, soup, status_code=status_code, page=1
            )
            malformed_count += malformed
            if malformed or not parsed_rows:
                errors.append(
                    f"status {status_code}: malformed first page ({malformed}) or no rows"
                )
                continue
            total = int(parsed_rows[0]["raw_fields"]["source_number"])
            pages = math.ceil(total / SUWON_PAGE_SIZE)
            source_totals[status_code] = total
            source_pages[status_code] = pages
            expected_numbers = list(
                range(total, max(0, total - SUWON_PAGE_SIZE), -1)
            )
            actual_numbers = [
                int(row["raw_fields"]["source_number"]) for row in parsed_rows
            ]
            if actual_numbers != expected_numbers:
                errors.append(f"status {status_code}: first-page source numbering mismatch")
            rows.extend(parsed_rows)

        required_list_requests = sum(source_pages.values())
        if len(source_pages) != len(SUWON_STATUS_SCOPES):
            errors.append("not every official status scope declared a total")
        elif required_list_requests > allowed_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of {required_list_requests} required list requests"
            )
        else:
            for status_code in SUWON_STATUS_SCOPES:
                total = source_totals[status_code]
                for page in range(2, source_pages[status_code] + 1):
                    soup = _fetch(
                        fetcher,
                        list_session,
                        suwon_list_url(status_code, page),
                        timeout,
                    )
                    list_requests += 1
                    parsed_rows, malformed = _parse_list_page(
                        target, soup, status_code=status_code, page=page
                    )
                    malformed_count += malformed
                    expected_start = total - (page - 1) * SUWON_PAGE_SIZE
                    expected_end = max(0, expected_start - SUWON_PAGE_SIZE)
                    expected_numbers = list(range(expected_start, expected_end, -1))
                    actual_numbers = [
                        int(row["raw_fields"]["source_number"])
                        for row in parsed_rows
                    ]
                    if malformed or actual_numbers != expected_numbers:
                        errors.append(
                            f"status {status_code}: page {page} source numbering mismatch or malformed rows"
                        )
                    rows.extend(parsed_rows)

        base_required_list_requests = sum(source_pages.values())
        base_listing_complete = (
            not errors
            and len(source_pages) == len(SUWON_STATUS_SCOPES)
            and list_requests == base_required_list_requests
            and len(rows) == sum(source_totals.values())
        )
        if base_listing_complete:
            for status_code in SUWON_STATUS_SCOPES:
                if list_requests >= allowed_pages:
                    source_cap_reached = True
                    category_errors.append(
                        "max_pages cap cannot inspect every official experience category scope"
                    )
                    break
                soup = _fetch(
                    fetcher,
                    list_session,
                    suwon_category_list_url(status_code, 1),
                    timeout,
                )
                list_requests += 1
                category_requests += 1
                parsed_rows, malformed = _parse_list_page(
                    target,
                    soup,
                    status_code=status_code,
                    page=1,
                    category_code=SUWON_EXPERIENCE_CATEGORY_CODE,
                )
                malformed_count += malformed
                if malformed:
                    category_errors.append(
                        f"category {SUWON_EXPERIENCE_CATEGORY_CODE} status {status_code}: malformed first page ({malformed})"
                    )
                    continue
                if not parsed_rows:
                    if not _is_official_empty_list(soup):
                        category_errors.append(
                            f"category {SUWON_EXPERIENCE_CATEGORY_CODE} status {status_code}: unrecognized empty first page"
                        )
                        continue
                    category_totals[status_code] = 0
                    category_pages[status_code] = 1
                    continue
                total = int(parsed_rows[0]["raw_fields"]["source_number"])
                pages = math.ceil(total / SUWON_PAGE_SIZE)
                category_totals[status_code] = total
                category_pages[status_code] = pages
                expected_numbers = list(
                    range(total, max(0, total - SUWON_PAGE_SIZE), -1)
                )
                actual_numbers = [
                    int(row["raw_fields"]["source_number"])
                    for row in parsed_rows
                ]
                if actual_numbers != expected_numbers:
                    category_errors.append(
                        f"category {SUWON_EXPERIENCE_CATEGORY_CODE} status {status_code}: first-page source numbering mismatch"
                    )
                category_rows.extend(parsed_rows)

            if len(category_pages) != len(SUWON_STATUS_SCOPES):
                category_errors.append(
                    "not every official experience category scope declared a total"
                )
            else:
                required_with_category = (
                    base_required_list_requests + sum(category_pages.values())
                )
                if required_with_category > allowed_pages:
                    source_cap_reached = True
                    category_errors.append(
                        f"max_pages cap allows {allowed_pages} of {required_with_category} required list requests"
                    )
                else:
                    for status_code in SUWON_STATUS_SCOPES:
                        total = category_totals[status_code]
                        for page in range(2, category_pages[status_code] + 1):
                            soup = _fetch(
                                fetcher,
                                list_session,
                                suwon_category_list_url(status_code, page),
                                timeout,
                            )
                            list_requests += 1
                            category_requests += 1
                            parsed_rows, malformed = _parse_list_page(
                                target,
                                soup,
                                status_code=status_code,
                                page=page,
                                category_code=SUWON_EXPERIENCE_CATEGORY_CODE,
                            )
                            malformed_count += malformed
                            expected_start = total - (page - 1) * SUWON_PAGE_SIZE
                            expected_end = max(
                                0,
                                expected_start - SUWON_PAGE_SIZE,
                            )
                            expected_numbers = list(
                                range(expected_start, expected_end, -1)
                            )
                            actual_numbers = [
                                int(row["raw_fields"]["source_number"])
                                for row in parsed_rows
                            ]
                            if malformed or actual_numbers != expected_numbers:
                                category_errors.append(
                                    f"category {SUWON_EXPERIENCE_CATEGORY_CODE} status {status_code}: page {page} source numbering mismatch or malformed rows"
                                )
                            category_rows.extend(parsed_rows)
    except Exception as exc:
        errors.append(f"list fetch {type(exc).__name__}")
    finally:
        _close_quietly(list_session)

    errors.extend(category_errors)
    declared_total = sum(source_totals.values())
    category_declared_total = sum(category_totals.values())
    base_required_list_requests = sum(source_pages.values())
    category_inventory_required = (
        len(source_pages) == len(SUWON_STATUS_SCOPES)
        and list_requests >= base_required_list_requests
        and len(rows) == declared_total
        and not any(
            error.startswith("status ")
            or error.startswith("not every official status")
            for error in errors
        )
    )
    required_list_requests = base_required_list_requests
    if category_inventory_required:
        required_list_requests += (
            sum(category_pages.values())
            if len(category_pages) == len(SUWON_STATUS_SCOPES)
            else len(SUWON_STATUS_SCOPES)
        )
    identities = [_clean(row.get("provider_course_id")) for row in rows]
    duplicate_count = len(identities) - len(set(identities))
    if duplicate_count:
        errors.append(f"{duplicate_count} duplicate identities across official status scopes")
    if len(source_totals) == len(SUWON_STATUS_SCOPES) and len(rows) != declared_total:
        errors.append(f"official status scopes declared {declared_total}, parsed {len(rows)}")
    category_identities = [
        _clean(row.get("provider_course_id")) for row in category_rows
    ]
    category_duplicate_count = len(category_identities) - len(
        set(category_identities)
    )
    if category_duplicate_count:
        errors.append(
            f"{category_duplicate_count} duplicate identities across official experience category scopes"
        )
    if (
        len(category_totals) == len(SUWON_STATUS_SCOPES)
        and len(category_rows) != category_declared_total
    ):
        errors.append(
            f"official experience category scopes declared {category_declared_total}, parsed {len(category_rows)}"
        )
    category_complete = (
        category_inventory_required
        and not category_errors
        and len(category_pages) == len(SUWON_STATUS_SCOPES)
        and category_requests == sum(category_pages.values())
        and len(category_rows) == category_declared_total
        and not category_duplicate_count
    )
    if category_complete:
        category_reconciliation_errors = _apply_experience_category_inventory(
            rows,
            category_rows,
        )
        errors.extend(category_reconciliation_errors)
        category_complete = not category_reconciliation_errors

    current_rows: list[dict[str, Any]] = []
    expired_count = 0
    historical_invalid_apply_period_count = 0
    current_invalid_apply_period_count = 0
    for row in rows:
        end = date.fromisoformat(_clean(row.get("end_date")))
        apply_valid = bool(row["raw_fields"].get("application_period_valid"))
        if end < cutoff:
            expired_count += 1
            if not apply_valid:
                historical_invalid_apply_period_count += 1
            continue
        if not apply_valid:
            current_invalid_apply_period_count += 1
            errors.append(
                f"{_clean(row.get('provider_course_id'))}: current application period is reversed"
            )
            continue
        current_rows.append(row)

    list_complete = (
        not errors
        and category_complete
        and len(source_pages) == len(SUWON_STATUS_SCOPES)
        and list_requests == required_list_requests
        and len(rows) == declared_total
    )
    required_details = len(current_rows)
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    municipality_counts: Counter[str] = Counter()
    municipality_resolution_counts: Counter[str] = Counter()
    if allowed_details < required_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} required details"
        )
    elif list_complete and current_rows:
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

        def enrich(row: dict[str, Any]) -> tuple[bool, list[str]]:
            try:
                soup = _fetch(fetcher, thread_session(), _clean(row.get("raw_url")), timeout)
                return True, _detail_errors(row, soup)
            except Exception as exc:
                return False, [
                    f"{_clean(row.get('provider_course_id'))}: detail fetch {type(exc).__name__}"
                ]

        detail_attempts = required_details
        workers = min(SUWON_MAX_DETAIL_WORKERS, max(1, int(max_workers)), required_details)
        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="suwon-detail") as pool:
                results = list(pool.map(enrich, current_rows))
        finally:
            for value in sessions:
                _close_quietly(value)
        detail_pages = sum(success for success, _item_errors in results)
        detail_errors = [error for _success, item_errors in results for error in item_errors]
        detail_errors.extend(_propagate_official_locations(current_rows))
        if detail_pages == required_details:
            for row in current_rows:
                municipality_code, evidence_kind = _assign_suwon_municipality(row)
                municipality_counts[municipality_code] += 1
                municipality_resolution_counts[evidence_kind] += 1

    errors.extend(detail_errors)
    details_complete = (
        detail_attempts == required_details
        and detail_pages == required_details
        and not detail_errors
    )
    cleaned = [_clean_row(row) for row in current_rows]
    dedupe = dedupe_rows or _dedupe_default
    if list_complete and details_complete:
        try:
            deduped = list(dedupe(cleaned))
        except Exception as exc:
            errors.append(f"dedupe failed {type(exc).__name__}")
            deduped = []
        if len(deduped) != len(cleaned):
            errors.append(f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}")
        cleaned = deduped

    snapshot_complete = list_complete and details_complete and not errors
    if not snapshot_complete:
        cleaned = []
    status_counts = Counter(_clean(row.get("status")) for row in current_rows)
    branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
    identity_kind_counts = Counter(
        _clean(row.get("raw_fields", {}).get("identity_kind")) for row in rows
    )
    domain_category_counts = Counter(
        _clean(row.get("domain_category")) for row in current_rows
    )
    meta: dict[str, Any] = {
        "pages": list_requests,
        "list_requests": list_requests,
        "required_list_requests": required_list_requests,
        "max_pages": allowed_pages,
        "page_unit": SUWON_PAGE_SIZE,
        "source_total": declared_total,
        "source_totals": source_totals,
        "source_pages": source_pages,
        "experience_category_code": SUWON_EXPERIENCE_CATEGORY_CODE,
        "experience_category_name": SUWON_EXPERIENCE_CATEGORY_NAME,
        "experience_category_requests": category_requests,
        "experience_source_total": category_declared_total,
        "experience_source_totals": category_totals,
        "experience_source_pages": category_pages,
        "experience_category_complete": category_complete,
        "discovered_links": len(set(identities)),
        "identity_kind_counts": dict(identity_kind_counts),
        "malformed_count": malformed_count,
        "duplicate_count": duplicate_count,
        "expired_count": expired_count,
        "historical_invalid_apply_period_count": historical_invalid_apply_period_count,
        "current_invalid_apply_period_count": current_invalid_apply_period_count,
        "current_count": len(current_rows),
        "experience_current_count": domain_category_counts.get("체험·견학", 0),
        "education_current_count": domain_category_counts.get("교육·강좌", 0),
        "domain_category_counts": dict(domain_category_counts),
        "returned_count": len(cleaned),
        "required_detail_count": required_details,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "pagination_detected": sum(source_pages.values()) > len(SUWON_STATUS_SCOPES),
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "status_counts": dict(status_counts),
        "branch_count": len(branch_counts),
        "branch_counts": dict(branch_counts),
        "municipality_counts": dict(municipality_counts),
        "municipality_resolution_counts": dict(municipality_resolution_counts),
        "parent_municipality_fallback_count": municipality_counts.get(
            SUWON_MUNICIPALITY_CODE,
            0,
        ),
        "reservation_discovery_links": sum(
            bool(row.get("application_url")) for row in current_rows
        ),
        "no_current_data": snapshot_complete and not current_rows,
        "no_current_reason": (
            "all official Suwon education and experience rows have ended"
            if snapshot_complete and not current_rows
            else ""
        ),
    }
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
    return cleaned, SUWON_PARSER, meta


collect_suwon_target = collect_suwon_reservation_education


__all__ = [
    "SUWON_APPLICATION_PATH",
    "SUWON_DETAIL_PATH",
    "SUWON_DISTRICT_MUNICIPALITIES",
    "SUWON_EXPERIENCE_CATEGORY_CODE",
    "SUWON_EXPERIENCE_CATEGORY_NAME",
    "SUWON_HOST",
    "SUWON_LIST_PATH",
    "SUWON_MAX_DETAIL_WORKERS",
    "SUWON_MUNICIPALITY_CODE",
    "SUWON_MUNICIPALITY_NAME",
    "SUWON_PAGE_SIZE",
    "SUWON_PARSER",
    "SUWON_PROVIDER",
    "SUWON_STATUS_SCOPES",
    "SUWON_URL",
    "collect_suwon_reservation_education",
    "collect_suwon_target",
    "is_suwon_reservation_target",
    "is_target",
    "suwon_application_url",
    "suwon_category_list_url",
    "suwon_detail_url",
    "suwon_list_url",
]
