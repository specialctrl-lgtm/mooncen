"""Atomic education collector for Busan Gangseo-gu.

The search candidate points at the district lifelong-learning ``vacation``
sub-ledger.  The canonical owner is the site's unfiltered ``allprogram``
ledger.  Collection covers every advertised page, the immediate empty
sentinel, stable first/final boundaries and every current/future detail.

Busan Lifelong Learning office ``OFFICE_00002686`` currently republishes the
latest 100 district detail URLs.  Those rows are suppressed only after exact
identity and immutable-field matching.  Future native ``LEARNING_*`` rows are
kept.  The Busan integrated-reservation resident-council partition is fixed to
``srchGugun=1`` and ``srchResveInsttCd=33``; it is currently empty but remains
inside the atomic source contract.

Application forms, applicant lists, instructors, contacts, attachments and
free-form detail values are never fetched or read.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

from Crawler import municipal_busan_lifelong as _lifelong


BUSAN_GANGSEO_PROVIDER = "MUNI_LLL_BSGANGSEO_GO_KR_0691B6EB"
BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER
BUSAN_GANGSEO_MUNICIPALITY_CODE = "2644000000"
BUSAN_GANGSEO_MUNICIPALITY_NAME = "부산광역시 강서구"

BUSAN_GANGSEO_HOST = "lll.bsgangseo.go.kr"
BUSAN_GANGSEO_PATH = "/html/index.php"
BUSAN_GANGSEO_REGISTERED_URL = (
    f"https://{BUSAN_GANGSEO_HOST}{BUSAN_GANGSEO_PATH}?pCode=vacation"
)
BUSAN_GANGSEO_CANONICAL_URL = (
    f"https://{BUSAN_GANGSEO_HOST}{BUSAN_GANGSEO_PATH}?pCode=allprogram"
)
BUSAN_GANGSEO_PAGE_SIZE = 10

BUSAN_LIFELONG_GANGSEO_OFFICE = "OFFICE_00002686"
BUSAN_LIFELONG_GANGSEO_OFFICE_NAME = "강서구청"
BUSAN_LIFELONG_PAGE_SIZE = 100

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_GANGSEO_GUGUN = "1"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_GANGSEO_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_GANGSEO_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)

BUSAN_GANGSEO_FETCH_ATTEMPTS = 3
BUSAN_GANGSEO_MAX_WORKERS = 12
BUSAN_GANGSEO_MAX_HTML_BYTES = 12_000_000
BUSAN_GANGSEO_PARSER = (
    "busan_gangseo_allprogram_complete_pages+empty_sentinel+stable_boundaries+"
    "current_detail_allowlist+lifelong_office00002686_two_stable_censuses+"
    "exact_external_idx_duplicate_suppression+native_learning_preservation+"
    "busan_city_gugun1_office33_complete+identity_bound_apply_no_form_fetch+"
    "pii_allowlist+atomic_three_ledger_snapshot"
)
BUSAN_GANGSEO_OWNERSHIP_SCOPE = (
    "gangseo_complete_lifelong_education_resident_councils_and_native_platform"
)

BUSAN_GANGSEO_CANDIDATE_IDS: Mapping[str, str] = {
    "registered_vacation_subledger": "MUNI_IR_7D728474F4D1",
    "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
    "wrong_owner_city_sports": "MUNI_IR_6B5FF686F683",
    "wrong_owner_city_search": "MUNI_IR_3B15FC3AFAA1",
}

BUSAN_GANGSEO_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_GANGSEO_PROVIDER: {
        "decision": "retarget_vacation_subledger_to_complete_allprogram_owner",
        "candidate_id": BUSAN_GANGSEO_CANDIDATE_IDS[
            "registered_vacation_subledger"
        ],
        "registered_url": BUSAN_GANGSEO_REGISTERED_URL,
        "canonical_url": BUSAN_GANGSEO_CANONICAL_URL,
        "identity_rule": "numeric idx",
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "suppress_exact_external_idx_duplicates_keep_native_learning_ids",
        "candidate_id": BUSAN_GANGSEO_CANDIDATE_IDS["busan_lifelong_federation"],
        "office_code": BUSAN_LIFELONG_GANGSEO_OFFICE,
        "identity_rule": "external idx belongs to district; LEARNING_* stays native",
    },
    "OFFICIAL_BUSAN_CITY_RESIDENT_RESERVATION": {
        "decision": "audit_exact_gangseo_resident_council_partition",
        "url": BUSAN_CITY_GANGSEO_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_GANGSEO_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
    },
    "CITYWIDE_SPORTS_AND_SEARCH_RESULTS": {
        "decision": "exclude_wrong_owner",
        "candidate_ids": (
            BUSAN_GANGSEO_CANDIDATE_IDS["wrong_owner_city_sports"],
            BUSAN_GANGSEO_CANDIDATE_IDS["wrong_owner_city_search"],
        ),
        "reason": "Busan city-operated facilities are not Gangseo-gu-owned ledgers",
    },
}

BUSAN_GANGSEO_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "district_rows": 1016,
    "district_data_pages": 102,
    "district_sentinel_page": 103,
    "district_current_rows": 18,
    "district_status_counts": {
        "교육마감": 998,
        "교육중": 8,
        "접수마감": 4,
        "대기접수": 3,
        "접수중": 3,
    },
    "platform_rows": 100,
    "platform_external_duplicate_rows": 100,
    "platform_native_rows": 0,
    "platform_current_external_rows": 17,
    "resident_rows": 0,
    "atomic_current_rows": 18,
}


class BusanGangseoContractError(ValueError):
    """Raised when an audited Gangseo source contract changes."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Probe = Callable[[BeautifulSoup], None]

_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
_LOCAL_STATUS_MAP = {
    "접수중": "OPEN",
    "대기접수": "OPEN",
    "접수대기": "SCHEDULED",
    "예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "교육중": "CLOSED",
    "교육마감": "CLOSED",
    "교육완료": "CLOSED",
    "마감": "CLOSED",
}
_CITY_STATUS_MAP = {
    "접수중": "OPEN",
    "대기중": "SCHEDULED",
    "대기접수": "OPEN",
    "접수마감": "CLOSED",
}
_CITY_ACTION_RE = re.compile(
    r"fn_viewProgrm\(\s*['\"]([0-9]+)['\"]\s*,\s*['\"]([0-9]+)['\"]\s*\)\s*;?\s*return\s+false\s*;?"
)
_PHONE_RE = re.compile(r"(?<!\d)(?:0\d{1,2}[- ]?)?\d{3,4}[- ]\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True) if node is not None else "")


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _normal_path(value: str) -> str:
    return re.sub(r"/{2,}", "/", value or "/")


def _compare_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
    ):
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return (
        f"https://{parsed.hostname.rstrip('.').lower()}{_normal_path(parsed.path)}"
        + (f"?{query}" if query else "")
    )


def is_busan_gangseo_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != BUSAN_GANGSEO_PROVIDER:
        return False
    compared = _compare_url(_target_value(target, "url"))
    return compared in {
        _compare_url(BUSAN_GANGSEO_REGISTERED_URL),
        _compare_url(BUSAN_GANGSEO_CANONICAL_URL),
    }


is_target = is_busan_gangseo_education_target


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise BusanGangseoContractError(f"invalid {label}")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BusanGangseoContractError(f"invalid {label}") from exc
    if result < 1:
        raise BusanGangseoContractError(f"invalid {label}")
    return result


def busan_gangseo_list_url(page: int = 1) -> str:
    current = _positive_int(page, "district page")
    query: list[tuple[str, Any]] = [("pCode", "allprogram")]
    if current != 1:
        query.append(("pg", current))
    return f"https://{BUSAN_GANGSEO_HOST}{BUSAN_GANGSEO_PATH}?" + urlencode(query)


def busan_gangseo_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not value.isdigit() or int(value) < 1:
        raise BusanGangseoContractError("invalid district identity")
    return f"https://{BUSAN_GANGSEO_HOST}{BUSAN_GANGSEO_PATH}?" + urlencode(
        (("pCode", "allprogram"), ("mode", "lec.view"), ("idx", value))
    )


def busan_gangseo_platform_list_url(page: int = 1) -> str:
    current = _positive_int(page, "platform page")
    payload = _lifelong._list_payload(BUSAN_LIFELONG_GANGSEO_OFFICE, current)
    payload["pageUnit"] = str(BUSAN_LIFELONG_PAGE_SIZE)
    return _lifelong.BUSAN_LIFELONG_LIST_URL + "?" + urlencode(payload)


def busan_gangseo_city_list_url(page: int = 1) -> str:
    current = _positive_int(page, "city page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (
            ("curPage", current),
            ("srchGugun", BUSAN_CITY_GANGSEO_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )


def busan_gangseo_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _clean(group_id)
    program = _clean(program_id)
    if not group.isdigit() or not program.isdigit():
        raise BusanGangseoContractError("invalid city identity")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def canonical_busan_gangseo_identity(value: Any) -> str:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_GANGSEO_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_GANGSEO_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"pCode", "mode", "idx"}
        or query.get("pCode") != ["allprogram"]
        or query.get("mode") != ["lec.view"]
        or len(query.get("idx", [])) != 1
        or not query["idx"][0].isdigit()
    ):
        return ""
    return query["idx"][0]


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _RequestBudget:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.count = 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.count >= self.maximum:
                raise BusanGangseoContractError(
                    f"max_requests cap {self.maximum} exhausted"
                )
            self.count += 1


def _response_soup(response: Any, requested_url: str) -> tuple[BeautifulSoup, str]:
    if isinstance(response, BeautifulSoup):
        return response, requested_url
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError("redirected source response")
    final_url = _clean(getattr(response, "url", "")) or requested_url
    requested = urlparse(requested_url)
    final = urlparse(final_url)
    if (
        final.scheme.lower() != "https"
        or (final.hostname or "").rstrip(".").lower()
        != (requested.hostname or "").rstrip(".").lower()
        or final.port is not None
        or final.username
        or final.password
        or _normal_path(final.path) != _normal_path(requested.path)
        or final.params
        or final.fragment
    ):
        raise ValueError("source response URL changed scope")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    size = len(content) if isinstance(content, bytes) else len(
        str(content).encode("utf-8")
    )
    if size > BUSAN_GANGSEO_MAX_HTML_BYTES:
        raise ValueError("source HTML exceeds safety limit")
    return BeautifulSoup(content, "lxml"), final_url


@dataclass
class _FetchResult:
    values: dict[Any, tuple[BeautifulSoup, str]]
    errors: list[str]
    retries: int
    sessions: int


def _fetch_many(
    items: Sequence[tuple[Any, str, Probe]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
) -> _FetchResult:
    values: dict[Any, tuple[BeautifulSoup, str]] = {}
    errors: list[str] = []
    retries = 0
    sessions: list[Any] = []
    local = threading.local()
    lock = threading.Lock()

    def thread_session() -> Any:
        current = getattr(local, "session", None)
        if current is None:
            current = session_factory()
            local.session = current
            with lock:
                sessions.append(current)
        return current

    def one(item: tuple[Any, str, Probe]) -> tuple[Any, tuple[BeautifulSoup, str], int]:
        key, url, probe = item
        messages: list[str] = []
        for attempt in range(1, BUSAN_GANGSEO_FETCH_ATTEMPTS + 1):
            try:
                budget.take()
                soup, final_url = _response_soup(
                    fetcher(thread_session(), url, timeout), url
                )
                probe(soup)
                return key, (soup, final_url), attempt - 1
            except Exception as exc:
                messages.append(
                    f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}"
                )
                if attempt < BUSAN_GANGSEO_FETCH_ATTEMPTS:
                    sleeper(min(0.25 * attempt, 0.75))
        raise ValueError("; ".join(messages))

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(one, item): item[0] for item in items}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    result_key, result, retry_count = future.result()
                    values[result_key] = result
                    retries += retry_count
                except Exception as exc:
                    errors.append(f"{key}: {type(exc).__name__}: {_clean(exc)}")
    finally:
        for session in sessions:
            _close_quietly(session)
    return _FetchResult(values, errors, retries, len(sessions))


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError:
            return []
    return result


def _query_one(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key, [])
    return _clean(values[0]) if len(values) == 1 else ""


def _local_last_page(
    soup: BeautifulSoup, expected_last: Optional[int] = None
) -> int:
    links = soup.select("a.lastpage[href]")
    # The source omits the redundant "last" control while already on the
    # final block.  Its descending global sequence below still proves the
    # supplied first-page boundary.
    if not links and expected_last is not None:
        return expected_last
    if len(links) != 1:
        raise BusanGangseoContractError("missing or ambiguous district last page")
    parsed = urlparse(urljoin(BUSAN_GANGSEO_CANONICAL_URL, links[0].get("href")))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_GANGSEO_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_GANGSEO_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"pCode", "pg"}
        or query.get("pCode") != ["allprogram"]
    ):
        raise BusanGangseoContractError("unsafe district last-page control")
    raw = _query_one(query, "pg")
    if not raw.isdigit() or int(raw) < 1:
        raise BusanGangseoContractError("invalid district last page")
    return int(raw)


def _local_row(
    source_row: Tag, *, page: int, position: int
) -> dict[str, Any]:
    cells = source_row.find_all("td", recursive=False)
    if len(cells) != 7:
        raise BusanGangseoContractError("district course row column count changed")
    sequence = _text(cells[1]).replace(",", "")
    if not sequence.isdigit() or int(sequence) < 1:
        raise BusanGangseoContractError("district list sequence changed")
    links = cells[2].select("a[href]")
    if len(links) != 1:
        raise BusanGangseoContractError("district title link changed")
    link = links[0]
    title_node = link.select_one(".ptit")
    title = _text(title_node)
    identity = canonical_busan_gangseo_identity(
        urljoin(BUSAN_GANGSEO_CANONICAL_URL, _clean(link.get("href")))
    )
    if not title or not identity:
        raise BusanGangseoContractError("district title/identity changed")
    all_dates = _dates(_text(cells[4]))
    if len(all_dates) != 4:
        raise BusanGangseoContractError("district list date ranges changed")
    apply_start, apply_end, start, end = all_dates
    if apply_end < apply_start or end < start:
        raise BusanGangseoContractError("district list date range reversed")
    status = _text(cells[6])
    if status not in _LOCAL_STATUS_MAP:
        raise BusanGangseoContractError(f"unknown district status {status!r}")

    fee = ""
    venue = ""
    for span in cells[3].select("span.ptime"):
        marker = _text(span.select_one(".pmark"))
        clone = BeautifulSoup(str(span), "lxml")
        for node in clone.select(".pmark"):
            node.extract()
        value = _text(clone)
        if marker == "부담":
            fee = value
        elif marker == "장소":
            venue = value
        else:
            raise BusanGangseoContractError("unknown district fee/venue label")
    if not venue:
        raise BusanGangseoContractError("district venue is empty")
    capacity_text = _text(cells[5])
    # A source defect renders zero applicants as an empty numerator (``/8``).
    capacity_parts = re.fullmatch(r"\s*([0-9,]*)\s*/\s*([0-9,]+)\s*", capacity_text)
    if not capacity_parts:
        raise BusanGangseoContractError("district capacity changed")
    capacity = capacity_parts.group(2).replace(",", "") + "명"
    raw_url = busan_gangseo_detail_url(identity)
    normalized = _LOCAL_STATUS_MAP[status]
    return {
        "provider": BUSAN_GANGSEO_PROVIDER,
        "provider_course_id": f"{BUSAN_GANGSEO_PROVIDER}:course:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": BUSAN_GANGSEO_MUNICIPALITY_NAME,
        "branch_code": BUSAN_GANGSEO_MUNICIPALITY_CODE,
        "municipality_code": BUSAN_GANGSEO_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_GANGSEO_MUNICIPALITY_NAME,
        "sido": "부산광역시",
        "sigungu": "강서구",
        "provider_organizer": "부산광역시 강서구",
        "venue_name": venue,
        "category": "평생학습",
        "program_type": "교육/강좌",
        "raw_url": raw_url,
        "application_url": "",
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "status": normalized,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": "",
        "fee": fee,
        "capacity": capacity,
        "target": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "complete_html_pages+current_detail_allowlist",
        "raw_fields": {
            "parser": BUSAN_GANGSEO_PARSER,
            "source_catalog": "gangseo_allprogram",
            "source_identity": identity,
            "source_page": page,
            "source_position": position,
            "source_sequence": int(sequence),
            "source_status": status,
            "detail_verified": False,
            "application_form_fetched": False,
            "applicant_list_fetched": False,
            "contact_value_never_read": True,
            "instructor_value_never_read": True,
            "free_form_detail_never_read": True,
            "service_family": "education",
        },
    }


def _parse_local_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    if "전체 강좌보기" not in _text(soup.select_one("title")):
        raise BusanGangseoContractError("district list title changed")
    last = _local_last_page(soup, expected_last)
    if expected_last is not None and last != expected_last:
        raise BusanGangseoContractError("district advertised last page changed")
    tables = soup.select("table.tbl-type01")
    if len(tables) != 1:
        raise BusanGangseoContractError("district course table changed")
    table = tables[0]
    headings = [_text(node) for node in table.select("thead th")]
    required = ("번호", "강의정보", "신청기간/교육기간", "현황")
    if not all(any(token in heading for heading in headings) for token in required):
        raise BusanGangseoContractError("district course headings changed")
    body = table.select_one("tbody")
    source_rows = body.find_all("tr", recursive=False) if body is not None else []
    rows = [
        _local_row(row, page=page, position=position)
        for position, row in enumerate(source_rows, 1)
        if row.select_one("a[href*='mode=lec.view']") is not None
    ]
    if page > last:
        if page != last + 1 or rows:
            raise BusanGangseoContractError("district sentinel changed")
        total = expected_total or 0
        return rows, total, last
    if not rows or len(rows) > BUSAN_GANGSEO_PAGE_SIZE:
        raise BusanGangseoContractError("district page row count changed")
    if page < last and len(rows) != BUSAN_GANGSEO_PAGE_SIZE:
        raise BusanGangseoContractError("district intermediate page is short")
    first_sequence = int(rows[0]["raw_fields"]["source_sequence"])
    total = expected_total if expected_total is not None else first_sequence
    if total < 1 or last != math.ceil(total / BUSAN_GANGSEO_PAGE_SIZE):
        raise BusanGangseoContractError("district total/last-page mismatch")
    expected_sequences = list(
        range(
            total - BUSAN_GANGSEO_PAGE_SIZE * (page - 1),
            max(0, total - BUSAN_GANGSEO_PAGE_SIZE * page),
            -1,
        )
    )
    actual_sequences = [row["raw_fields"]["source_sequence"] for row in rows]
    if actual_sequences != expected_sequences:
        raise BusanGangseoContractError("district list sequence is incomplete")
    return rows, total, last


def _safe_detail_pairs(table: Tag) -> tuple[dict[str, str], set[str]]:
    safe_labels = {
        "교육기관",
        "교육분야",
        "교육시간",
        "학습대상",
        "교육분류",
        "교육장소",
        "교육대상",
        "신청기간",
        "교육기간",
        "교육요일/횟수",
        "수강인원",
        "수강자부담",
        "신청상태",
    }
    skipped_labels = {"강사정보", "문의전화"}
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    institution_occurrences = 0
    for row in table.select("tbody > tr"):
        children = [node for node in row.find_all(("th", "td"), recursive=False)]
        index = 0
        while index < len(children):
            heading = children[index]
            if heading.name != "th" or index + 1 >= len(children):
                index += 1
                continue
            value = children[index + 1]
            if value.name != "td":
                raise BusanGangseoContractError("district detail field layout changed")
            label = _text(heading)
            if label == "교육기관":
                institution_occurrences += 1
                if institution_occurrences == 2:
                    # The template repeats the venue under an incorrectly
                    # labelled second 교육기관 field.  교육장소 already owns
                    # that value, so the duplicate is deliberately not read.
                    skipped.add("교육기관(장소중복)")
                    index += 2
                    continue
                if institution_occurrences > 2:
                    raise BusanGangseoContractError(
                        "district education-institution fields changed"
                    )
            if label in safe or label in skipped:
                raise BusanGangseoContractError("duplicate district detail field")
            if label in safe_labels:
                safe[label] = _text(value)
            elif label in skipped_labels:
                skipped.add(label)
            elif label:
                # Spacer rows contain td only.  A new labelled value is not
                # silently read because it may contain PII or free-form text.
                raise BusanGangseoContractError(
                    f"unknown district detail field {label!r}"
                )
            index += 2
    required = {
        "교육기관",
        "교육분야",
        "학습대상",
        "교육분류",
        "교육장소",
        "교육대상",
        "신청기간",
        "교육기간",
        "수강인원",
        "수강자부담",
        "신청상태",
    }
    expected_skipped = skipped_labels | {"교육기관(장소중복)"}
    if (
        not required.issubset(safe)
        or skipped != expected_skipped
        or institution_occurrences != 2
    ):
        raise BusanGangseoContractError("district safe-detail boundary changed")
    return safe, skipped


def _parse_local_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    identity = _clean(parent.get("raw_fields", {}).get("source_identity"))
    if _compare_url(final_url) != _compare_url(busan_gangseo_detail_url(identity)):
        raise BusanGangseoContractError("district detail response scope changed")
    if "전체 강좌보기" not in _text(soup.select_one("title")):
        raise BusanGangseoContractError("district detail title changed")
    tables = soup.select("table.tbl-type02")
    if len(tables) != 1:
        raise BusanGangseoContractError("district detail table changed")
    table = tables[0]
    heading = table.select_one("thead .p-tit-box > .tit.b")
    if _text(heading) != _clean(parent.get("title")):
        raise BusanGangseoContractError("district list/detail title mismatch")
    safe, skipped = _safe_detail_pairs(table)
    education_dates = _dates(safe["교육기간"])
    application_dates = _dates(safe["신청기간"])
    if len(education_dates) != 2 or len(application_dates) != 2:
        raise BusanGangseoContractError("district detail date ranges changed")
    if [value.isoformat() for value in education_dates] != [
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ] or [value.isoformat() for value in application_dates] != [
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ]:
        raise BusanGangseoContractError("district list/detail dates mismatch")
    source_status = safe["신청상태"]
    if source_status != _clean(parent.get("raw_fields", {}).get("source_status")):
        raise BusanGangseoContractError("district list/detail status mismatch")
    controls = []
    for link in table.select("thead a[href]"):
        parsed = urlparse(urljoin(final_url, _clean(link.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query.get("mode") != ["lec.app"]:
            continue
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").rstrip(".").lower() != BUSAN_GANGSEO_HOST
            or parsed.port is not None
            or parsed.username
            or parsed.password
            or parsed.path != BUSAN_GANGSEO_PATH
            or parsed.params
            or parsed.fragment
            or set(query) != {"pCode", "mode", "lec_idx"}
            or query.get("pCode") != ["allprogram"]
            or query.get("lec_idx") != [identity]
            or _text(link) != "신청하기"
        ):
            raise BusanGangseoContractError("unsafe district application control")
        controls.append(link)
    if len(controls) > 1:
        raise BusanGangseoContractError("multiple district application controls")
    normalized = _LOCAL_STATUS_MAP[source_status]
    active = normalized == "OPEN"
    if active and len(controls) != 1:
        raise BusanGangseoContractError("open district row lacks application control")
    closed_control_retained = bool(not active and controls)
    if closed_control_retained and source_status != "접수마감":
        raise BusanGangseoContractError("closed district row retained application control")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": (
                "WAITLIST_APPLY"
                if active and source_status == "대기접수"
                else "ONLINE_RESERVATION" if active else "INFO_ONLY"
            ),
            "reservation_available": active,
            "status": normalized,
            "venue_name": safe["교육장소"],
            "target": safe["교육대상"],
            "fee": safe["수강자부담"],
            "category": safe["교육분류"] or parent.get("category"),
            "schedule_raw": safe.get("교육요일/횟수", ""),
        }
    )
    result["raw_fields"] = {
        **parent.get("raw_fields", {}),
        "detail_verified": True,
        "detail_application_control": active,
        "closed_application_control_retained": closed_control_retained,
        "detail_source_status": source_status,
        "contact_value_never_read": "문의전화" in skipped,
        "instructor_value_never_read": "강사정보" in skipped,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
        "free_form_detail_never_read": True,
    }
    return result


def _platform_office() -> _lifelong.BusanOffice:
    office = _lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(
        BUSAN_LIFELONG_GANGSEO_OFFICE
    )
    if office is None or office.name != BUSAN_LIFELONG_GANGSEO_OFFICE_NAME:
        raise BusanGangseoContractError("platform Gangseo office changed")
    if (
        office.ownership != "duplicate_dedicated_gangseo_owner"
        or office.municipality_code
        or office.municipality_name
    ):
        raise BusanGangseoContractError("platform Gangseo ownership changed")
    return _lifelong.BusanOffice(
        office.code,
        office.name,
        BUSAN_GANGSEO_MUNICIPALITY_CODE,
        BUSAN_GANGSEO_MUNICIPALITY_NAME,
        "municipal",
    )


def _parse_platform_page(
    soup: BeautifulSoup, *, page: int, expected_last: Optional[int] = None
) -> tuple[list[dict[str, Any]], int]:
    office = _platform_office()
    errors = _lifelong._form_errors(soup, office, page)
    last, last_errors = _lifelong._advertised_last(soup)
    errors.extend(last_errors)
    if expected_last is not None and last != expected_last:
        errors.append("platform last page changed")
    rows, row_errors = _lifelong._parse_list_page(soup, office=office, page=page)
    errors.extend(row_errors)
    if errors:
        raise BusanGangseoContractError("; ".join(errors))
    if last != 1:
        raise BusanGangseoContractError("platform Gangseo census no longer one page")
    if page == 1 and len(rows) > BUSAN_LIFELONG_PAGE_SIZE:
        raise BusanGangseoContractError("platform Gangseo page exceeds cap")
    if page == 2 and rows:
        raise BusanGangseoContractError("platform Gangseo sentinel is not empty")
    return rows, last


def _platform_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = sorted(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("apply_start")),
            _clean(row.get("apply_end")),
        )
        for row in rows
    )
    return hashlib.sha256(
        repr(values).encode("utf-8")
    ).hexdigest()


def _platform_native_row(source: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(source)
    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    row.update(
        {
            "provider": BUSAN_GANGSEO_PROVIDER,
            "provider_course_id": f"{BUSAN_GANGSEO_PROVIDER}:platform:{identity}",
            "branch": BUSAN_GANGSEO_MUNICIPALITY_NAME,
            "branch_code": BUSAN_GANGSEO_MUNICIPALITY_CODE,
            "municipality_code": BUSAN_GANGSEO_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_GANGSEO_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "강서구",
            "provider_organizer": BUSAN_LIFELONG_GANGSEO_OFFICE_NAME,
            "source_group": "municipal_reservation",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
        }
    )
    row["raw_fields"] = {
        **raw,
        "parser": BUSAN_GANGSEO_PARSER,
        "source_catalog": "busan_lifelong_gangseo_native",
        "service_family": "education",
    }
    return row


def _same_owner_fields(platform: Mapping[str, Any], owner: Mapping[str, Any]) -> bool:
    core_matches = all(
        _clean(platform.get(key)) == _clean(owner.get(key))
        for key in ("title", "start_date", "end_date", "apply_start")
    )
    if not core_matches:
        return False
    platform_end = _clean(platform.get("apply_end"))
    owner_end = _clean(owner.get("apply_end"))
    # The federation snapshot can lag a district-side reception extension.
    # It may end earlier, but may never invent a later deadline.
    return bool(platform_end and owner_end and platform_end <= owner_end)


def _city_last_page(
    soup: BeautifulSoup, page: int, expected_last: Optional[int] = None
) -> int:
    title = _text(soup.select_one("title"))
    if title != "강좌/교육 : 부산광역시 통합예약":
        raise BusanGangseoContractError("Busan city list title changed")
    forms = soup.select("form#srchForm[name='srchForm']")
    if len(forms) != 1:
        raise BusanGangseoContractError("Busan city search form changed")
    form = forms[0]
    page_field = form.select_one("input[name='curPage']")
    if (
        _clean(form.get("method")).lower() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
        or _clean(page_field.get("value") if page_field else "") != str(page)
    ):
        raise BusanGangseoContractError("Busan city search form changed")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_GANGSEO_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanGangseoContractError(f"Busan city {name} filter changed")
    end_links = soup.select("div.paginate > a.pgEnd[href]")
    roots = soup.select("ul.reserveList")
    # An exact empty partition renders neither list nor pager, including for
    # its immediate sentinel request.
    if not end_links and not roots:
        return expected_last or 1
    if len(end_links) != 1:
        raise BusanGangseoContractError("Busan city last-page control changed")
    parsed = urlparse(urljoin(BUSAN_CITY_GANGSEO_URL, end_links[0].get("href")))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_CITY_HOST
        or parsed.path not in {BUSAN_CITY_LIST_PATH, BUSAN_CITY_LIST_PATH + ".do"}
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
        or set(query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or query.get("srchGugun") != [BUSAN_CITY_GANGSEO_GUGUN]
        or query.get("srchResveInsttCd") != [BUSAN_CITY_RESIDENT_OFFICE]
    ):
        raise BusanGangseoContractError("unsafe Busan city last-page control")
    raw = _query_one(query, "curPage")
    if not raw.isdigit() or int(raw) < 1:
        raise BusanGangseoContractError("invalid Busan city last page")
    return int(raw)


def _definition_pairs(root: Tag, *, skip: set[str]) -> tuple[dict[str, str], set[str]]:
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    headings = root.find_all("dt", recursive=False)
    values = root.find_all("dd", recursive=False)
    if len(headings) != len(values):
        raise BusanGangseoContractError("Busan city card fields changed")
    for heading, value in zip(headings, values):
        label = _text(heading)
        if label in safe or label in skipped:
            raise BusanGangseoContractError("duplicate Busan city card field")
        if label in skip:
            skipped.add(label)
        else:
            safe[label] = _text(value)
    return safe, skipped


def _parse_city_page(
    soup: BeautifulSoup, *, page: int, expected_last: Optional[int] = None
) -> tuple[list[dict[str, Any]], int]:
    last = _city_last_page(soup, page, expected_last)
    if expected_last is not None and last != expected_last:
        raise BusanGangseoContractError("Busan city last page changed")
    roots = soup.select("ul.reserveList")
    if page > last:
        if page != last + 1 or roots:
            raise BusanGangseoContractError("Busan city sentinel changed")
        return [], last
    if len(roots) > 1:
        raise BusanGangseoContractError("multiple Busan city lists")
    items = roots[0].find_all("li", recursive=False) if roots else []
    rows: list[dict[str, Any]] = []
    for position, item in enumerate(items, 1):
        links = item.select(":scope > a.reserveItem[onclick]")
        if len(links) != 1:
            raise BusanGangseoContractError("Busan city card link changed")
        link = links[0]
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanGangseoContractError("Busan city identity action changed")
        group_id, program_id = action.groups()
        title_node = link.select_one(".infoBox > .tit")
        title = _text(title_node)
        if not title or _clean(title_node.get("title") if title_node else "") != title:
            raise BusanGangseoContractError("Busan city card title changed")
        source_status = _text(link.select_one(".statusMark"))
        if source_status not in _CITY_STATUS_MAP:
            raise BusanGangseoContractError("unknown Busan city status")
        definitions = link.select_one(".infoBox > dl")
        if definitions is None:
            raise BusanGangseoContractError("Busan city card fields missing")
        safe, skipped = _definition_pairs(definitions, skip={"문의"})
        required = {"기관", "대상", "장소", "일자", "방법"}
        if set(safe) != required or skipped != {"문의"}:
            raise BusanGangseoContractError("Busan city card field boundary changed")
        if "주민자치" not in safe["기관"]:
            raise BusanGangseoContractError("Busan city row left resident owner")
        values = _dates(safe["일자"])
        if len(values) != 4:
            raise BusanGangseoContractError("Busan city card dates changed")
        apply_start, apply_end, start, end = values
        if apply_end < apply_start or end < start:
            raise BusanGangseoContractError("Busan city card dates reversed")
        raw_url = busan_gangseo_city_detail_url(group_id, program_id)
        rows.append(
            {
                "provider": BUSAN_GANGSEO_PROVIDER,
                "provider_course_id": (
                    f"{BUSAN_GANGSEO_PROVIDER}:reserve:{group_id}:{program_id}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": safe["기관"],
                "branch_code": f"gangseo-reserve-{group_id}",
                "preserve_branch": True,
                "municipality_code": BUSAN_GANGSEO_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_GANGSEO_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "강서구",
                "provider_organizer": safe["기관"],
                "venue_name": safe["장소"],
                "category": "주민자치프로그램",
                "program_type": "교육/강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": _CITY_STATUS_MAP[source_status],
                "period": f"{start.isoformat()} ~ {end.isoformat()}",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
                "apply_start": apply_start.isoformat(),
                "apply_end": apply_end.isoformat(),
                "schedule_raw": "",
                "fee": "",
                "capacity": "",
                "target": safe["대상"],
                "application_method_raw": safe["방법"],
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_GANGSEO_PARSER,
                    "source_catalog": "busan_reserve_gangseo_resident_councils",
                    "source_identity": f"{group_id}:{program_id}",
                    "source_group_id": group_id,
                    "source_program_id": program_id,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_application_method": safe["방법"],
                    "detail_verified": False,
                    "inquiry_value_never_read": True,
                    "application_form_fetched": False,
                    "service_family": "education",
                },
            }
        )
    if page < last and len(rows) != 10:
        raise BusanGangseoContractError("Busan city intermediate page is short")
    if page == last and last > 1 and not 1 <= len(rows) <= 10:
        raise BusanGangseoContractError("Busan city final page changed")
    return rows, last


def _parse_city_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    expected = busan_gangseo_city_detail_url(
        raw.get("source_group_id"), raw.get("source_program_id")
    )
    if _compare_url(final_url) != _compare_url(expected):
        raise BusanGangseoContractError("Busan city detail response scope changed")
    forms = soup.select("form#viewForm")
    if len(forms) != 1:
        raise BusanGangseoContractError("Busan city detail form changed")
    form = forms[0]
    heading = form.select_one("div.contHeader > h3.titPage")
    if heading is None or _clean(parent.get("title")) not in _text(heading):
        raise BusanGangseoContractError("Busan city list/detail title mismatch")
    status = _text(heading.select_one(".statusMark"))
    if status != _clean(raw.get("source_status")):
        raise BusanGangseoContractError("Busan city list/detail status mismatch")
    info = form.select_one("div.reserveStateInfo")
    if info is None:
        raise BusanGangseoContractError("Busan city safe detail values missing")
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    allowed = {
        "운영기간",
        "신청기간",
        "신청방법",
        "운영기관",
        "대상",
        "수강료",
        "요일 /시간",
    }
    for definition in info.find_all("dl", recursive=False):
        label = _text(definition.find("dt", recursive=False))
        value_node = definition.find("dd", recursive=False)
        if label in {"문의전화", "첨부파일"}:
            skipped.add(label)
        elif label in allowed:
            safe[label] = _text(value_node)
        elif label:
            raise BusanGangseoContractError(
                f"unknown Busan city detail field {label!r}"
            )
    if "문의전화" not in skipped:
        raise BusanGangseoContractError("Busan city inquiry boundary changed")
    education_dates = _dates(safe.get("운영기간"))
    application_dates = _dates(safe.get("신청기간"))
    if [value.isoformat() for value in education_dates] != [
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ] or [value.isoformat() for value in application_dates] != [
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ]:
        raise BusanGangseoContractError("Busan city list/detail dates mismatch")
    controls = form.select("div.reserveBtnWrap > a.btnTypeXL")
    if len(controls) > 1:
        raise BusanGangseoContractError("multiple Busan city application controls")
    active = _CITY_STATUS_MAP[status] == "OPEN" and "온라인" in safe.get(
        "신청방법", ""
    )
    if active and not controls:
        raise BusanGangseoContractError("open Busan city row lacks control")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
            "reservation_available": active,
            "fee": safe.get("수강료", ""),
            "schedule_raw": safe.get("요일 /시간", ""),
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_application_control": bool(controls),
        "inquiry_value_never_read": True,
        "attachments_never_read": "첨부파일" in skipped,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
    }
    return result


def _signature(rows: Sequence[Mapping[str, Any]], identity_key: str) -> str:
    return hashlib.sha256(
        repr(
            [
                (
                    _clean(row.get("raw_fields", {}).get(identity_key)),
                    _clean(row.get("title")),
                    _clean(row.get("start_date")),
                    _clean(row.get("end_date")),
                    _clean(row.get("raw_fields", {}).get("source_status")),
                )
                for row in rows
            ]
        ).encode("utf-8")
    ).hexdigest()


def _unique(
    rows: Sequence[Mapping[str, Any]], *, identity_key: str, label: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = _clean(row.get("raw_fields", {}).get(identity_key))
        if not identity or identity in result:
            raise BusanGangseoContractError(f"{label} duplicate identity")
        result[identity] = dict(row)
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise BusanGangseoContractError("invalid today") from exc


def _sanitize_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    redactions = 0
    pii_keys = ("phone", "telephone", "mobile", "email", "contact", "instructor")

    def visit(value: Any) -> Any:
        nonlocal redactions
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if any(token in str(key).casefold() for token in pii_keys):
                    redactions += 1
                    continue
                result[str(key)] = visit(item)
            return result
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, tuple):
            return tuple(visit(item) for item in value)
        if isinstance(value, str):
            current, first = _PHONE_RE.subn("[redacted]", value)
            current, second = _EMAIL_RE.subn("[redacted]", current)
            redactions += first + second
            return current
        return value

    return visit(row), redactions


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "source_rows": 0,
        "source_total": 0,
        "unique_education_source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "source_cap_reached": False,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "atomic_union_complete": False,
        "configured_collection_error": "",
    }


def collect_busan_gangseo_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 150,
    detail_limit: int = 100,
    max_requests: int = 350,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_GANGSEO_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Gangseo education snapshot."""

    meta = _base_meta()
    if not is_busan_gangseo_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact Gangseo education owner"
        )
        return [], BUSAN_GANGSEO_PARSER, meta
    try:
        if any(
            isinstance(value, bool)
            for value in (timeout, max_pages, detail_limit, max_requests, max_workers)
        ):
            raise ValueError("boolean limits are invalid")
        request_timeout = max(1, int(timeout))
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        request_cap = max(0, int(max_requests))
        workers = min(max(1, int(max_workers)), BUSAN_GANGSEO_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_GANGSEO_PARSER, meta
    if page_cap < 3 or request_cap < 3:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "caps do not allow first-ledger requests"
        return [], BUSAN_GANGSEO_PARSER, meta

    fetch = fetcher or _default_fetcher
    factory = session_factory or _default_session_factory
    budget = _RequestBudget(request_cap)

    def run_jobs(
        jobs: Sequence[tuple[Any, str, Probe]], *, list_phase: bool
    ) -> _FetchResult:
        result = _fetch_many(
            jobs,
            fetcher=fetch,
            session_factory=factory,
            timeout=request_timeout,
            max_workers=min(workers, max(1, len(jobs))),
            sleeper=sleeper,
            budget=budget,
        )
        meta["network_retry_count"] += result.retries
        meta["sessions_created"] += result.sessions
        meta["network_requests"] = budget.count
        if list_phase:
            meta["list_requests"] += len(result.values)
            meta["pages"] += len(result.values)
        return result

    first_jobs: list[tuple[Any, str, Probe]] = [
        (
            ("local", 1),
            busan_gangseo_list_url(1),
            lambda soup: _parse_local_page(soup, page=1),
        ),
        (
            ("platform", "first"),
            busan_gangseo_platform_list_url(1),
            lambda soup: _parse_platform_page(soup, page=1),
        ),
        (
            ("city", 1),
            busan_gangseo_city_list_url(1),
            lambda soup: _parse_city_page(soup, page=1),
        ),
    ]
    first = run_jobs(first_jobs, list_phase=True)
    if first.errors or len(first.values) != 3:
        meta["configured_collection_error"] = "; ".join(first.errors) or (
            "missing one or more first-ledger responses"
        )
        return [], BUSAN_GANGSEO_PARSER, meta
    try:
        first_local, local_total, local_last = _parse_local_page(
            first.values[("local", 1)][0], page=1
        )
        first_platform, platform_last = _parse_platform_page(
            first.values[("platform", "first")][0], page=1
        )
        first_city, city_last = _parse_city_page(
            first.values[("city", 1)][0], page=1
        )
        required_list = (local_last + 1) + 3 + (city_last + 1) + 4
        if required_list > page_cap:
            raise BusanGangseoContractError(
                f"max_pages cap allows {page_cap} of {required_list} required pages"
            )
        if required_list > request_cap:
            raise BusanGangseoContractError("max_requests below list census floor")
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"first-page contract: {_clean(exc)}"
        return [], BUSAN_GANGSEO_PARSER, meta

    remaining_jobs: list[tuple[Any, str, Probe]] = []
    for page in range(2, local_last + 2):
        remaining_jobs.append(
            (
                ("local", page),
                busan_gangseo_list_url(page),
                lambda soup, page=page: _parse_local_page(
                    soup,
                    page=page,
                    expected_total=local_total,
                    expected_last=local_last,
                ),
            )
        )
    remaining_jobs.extend(
        (
            (
                ("platform", "second"),
                busan_gangseo_platform_list_url(1),
                lambda soup: _parse_platform_page(
                    soup, page=1, expected_last=platform_last
                ),
            ),
            (
                ("platform", "sentinel"),
                busan_gangseo_platform_list_url(2),
                lambda soup: _parse_platform_page(
                    soup, page=2, expected_last=platform_last
                ),
            ),
        )
    )
    for page in range(2, city_last + 2):
        remaining_jobs.append(
            (
                ("city", page),
                busan_gangseo_city_list_url(page),
                lambda soup, page=page: _parse_city_page(
                    soup, page=page, expected_last=city_last
                ),
            )
        )
    remaining = run_jobs(remaining_jobs, list_phase=True)
    if remaining.errors or len(remaining.values) != len(remaining_jobs):
        meta["configured_collection_error"] = "; ".join(remaining.errors) or (
            "missing complete ledger/sentinel response"
        )
        return [], BUSAN_GANGSEO_PARSER, meta

    try:
        local_pages: dict[int, list[dict[str, Any]]] = {1: first_local}
        for page in range(2, local_last + 2):
            rows, _, _ = _parse_local_page(
                remaining.values[("local", page)][0],
                page=page,
                expected_total=local_total,
                expected_last=local_last,
            )
            local_pages[page] = rows
        if local_pages[local_last + 1]:
            raise BusanGangseoContractError("district sentinel is not empty")
        local_rows = [
            row for page in range(1, local_last + 1) for row in local_pages[page]
        ]
        if len(local_rows) != local_total:
            raise BusanGangseoContractError("district rows differ from total")
        local_by_id = _unique(
            local_rows, identity_key="source_identity", label="district census"
        )

        second_platform, _ = _parse_platform_page(
            remaining.values[("platform", "second")][0],
            page=1,
            expected_last=platform_last,
        )
        sentinel_platform, _ = _parse_platform_page(
            remaining.values[("platform", "sentinel")][0],
            page=2,
            expected_last=platform_last,
        )
        if sentinel_platform or _platform_signature(first_platform) != _platform_signature(
            second_platform
        ):
            raise BusanGangseoContractError("platform census changed")
        _unique(first_platform, identity_key="identity", label="platform census")

        city_pages: dict[int, list[dict[str, Any]]] = {1: first_city}
        for page in range(2, city_last + 2):
            rows, _ = _parse_city_page(
                remaining.values[("city", page)][0],
                page=page,
                expected_last=city_last,
            )
            city_pages[page] = rows
        if city_pages[city_last + 1]:
            raise BusanGangseoContractError("Busan city sentinel is not empty")
        city_rows = [
            row for page in range(1, city_last + 1) for row in city_pages[page]
        ]
        _unique(city_rows, identity_key="source_identity", label="city census")
    except Exception as exc:
        meta["configured_collection_error"] = f"complete census: {_clean(exc)}"
        return [], BUSAN_GANGSEO_PARSER, meta

    try:
        external_rows: list[dict[str, Any]] = []
        external_application_lag_rows = 0
        native_rows: list[dict[str, Any]] = []
        for row in first_platform:
            raw = row.get("raw_fields", {})
            kind = _clean(raw.get("identity_kind"))
            if kind == "external":
                identity = canonical_busan_gangseo_identity(raw.get("identity"))
                owner = local_by_id.get(identity)
                if owner is None or not _same_owner_fields(row, owner):
                    raise BusanGangseoContractError(
                        "platform external row is not an exact district duplicate"
                    )
                if _clean(row.get("apply_end")) != _clean(owner.get("apply_end")):
                    external_application_lag_rows += 1
                external_rows.append(dict(row))
            elif kind == "internal":
                native_rows.append(_platform_native_row(row))
            else:
                raise BusanGangseoContractError(
                    f"unsupported platform identity kind {kind!r}"
                )
        cutoff_iso = cutoff.isoformat()
        local_current = [
            row for row in local_rows if _clean(row.get("end_date")) >= cutoff_iso
        ]
        native_current = [
            row for row in native_rows if _clean(row.get("end_date")) >= cutoff_iso
        ]
        city_current = [
            row for row in city_rows if _clean(row.get("end_date")) >= cutoff_iso
        ]
        current_rows = [*local_current, *native_current, *city_current]
        if len(current_rows) > detail_cap:
            raise BusanGangseoContractError(
                f"detail_limit cap allows {detail_cap} of {len(current_rows)} details"
            )
        extra_native_bootstrap = 1 if native_current else 0
        required_requests = (
            meta["list_requests"] + len(current_rows) + 4 + extra_native_bootstrap
        )
        if required_requests > request_cap:
            raise BusanGangseoContractError(
                f"max_requests cap {request_cap} cannot finish {required_requests} requests"
            )
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = (
            f"ownership/current partition: {_clean(exc)}"
        )
        return [], BUSAN_GANGSEO_PARSER, meta

    detail_jobs: list[tuple[Any, str, Probe]] = []
    for row in local_current:
        identity = _clean(row.get("raw_fields", {}).get("source_identity"))
        url = busan_gangseo_detail_url(identity)
        detail_jobs.append(
            (
                ("detail", "local", identity),
                url,
                lambda soup, row=row, url=url: _parse_local_detail(soup, url, row),
            )
        )
    for row in city_current:
        raw = row.get("raw_fields", {})
        group_id = _clean(raw.get("source_group_id"))
        program_id = _clean(raw.get("source_program_id"))
        url = busan_gangseo_city_detail_url(group_id, program_id)
        detail_jobs.append(
            (
                ("detail", "city", group_id, program_id),
                url,
                lambda soup, row=row, url=url: _parse_city_detail(soup, url, row),
            )
        )
    details = run_jobs(detail_jobs, list_phase=False)
    meta["detail_attempts"] = len(detail_jobs) + len(native_current)
    meta["detail_errors"] = len(details.errors)
    if details.errors or len(details.values) != len(detail_jobs):
        meta["configured_collection_error"] = "; ".join(details.errors) or (
            "missing one or more current/future details"
        )
        return [], BUSAN_GANGSEO_PARSER, meta
    try:
        enriched: list[dict[str, Any]] = []
        for row in local_current:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            soup, final_url = details.values[("detail", "local", identity)]
            enriched.append(_parse_local_detail(soup, final_url, row))
        for row in city_current:
            raw = row.get("raw_fields", {})
            key = (
                "detail",
                "city",
                _clean(raw.get("source_group_id")),
                _clean(raw.get("source_program_id")),
            )
            soup, final_url = details.values[key]
            enriched.append(_parse_city_detail(soup, final_url, row))
        if native_current:
            if fetcher is not None:
                raise BusanGangseoContractError(
                    "synthetic native detail transport must use a session fixture"
                )
            # Internal details require a same-session list bootstrap.  Reuse
            # the audited platform helper; it POSTs only the list/detail routes.
            native_details, native_errors, bootstraps = _lifelong._parallel_detail_fetch(
                native_current,
                session_factory=factory,
                timeout=request_timeout,
                max_workers=min(workers, len(native_current)),
            )
            budget.take()
            for _ in native_current:
                budget.take()
            meta["network_requests"] = budget.count
            meta["sessions_created"] += min(len(native_current), 1)
            if native_errors or len(native_details) != len(native_current):
                raise BusanGangseoContractError(
                    "; ".join(native_errors) or "missing native platform details"
                )
            for row in native_current:
                identity = _clean(row.get("raw_fields", {}).get("identity"))
                soup, _ = native_details[identity]
                errors = _lifelong._validate_internal_detail(row, soup)
                if errors:
                    raise BusanGangseoContractError("; ".join(errors))
                enriched.append(row)
            meta["platform_detail_bootstraps"] = bootstraps
    except Exception as exc:
        meta["detail_errors"] += 1
        meta["configured_collection_error"] = f"detail contract: {_clean(exc)}"
        return [], BUSAN_GANGSEO_PARSER, meta

    recheck_jobs: list[tuple[Any, str, Probe]] = [
        (
            ("recheck", "local", "first"),
            busan_gangseo_list_url(1),
            lambda soup: _parse_local_page(
                soup, page=1, expected_total=local_total, expected_last=local_last
            ),
        ),
        (
            ("recheck", "local", "last"),
            busan_gangseo_list_url(local_last),
            lambda soup: _parse_local_page(
                soup,
                page=local_last,
                expected_total=local_total,
                expected_last=local_last,
            ),
        ),
        (
            ("recheck", "city", "first"),
            busan_gangseo_city_list_url(1),
            lambda soup: _parse_city_page(soup, page=1, expected_last=city_last),
        ),
        (
            ("recheck", "city", "last"),
            busan_gangseo_city_list_url(city_last),
            lambda soup: _parse_city_page(
                soup, page=city_last, expected_last=city_last
            ),
        ),
    ]
    rechecks = run_jobs(recheck_jobs, list_phase=True)
    meta["stability_rechecks"] = len(rechecks.values)
    if rechecks.errors or len(rechecks.values) != 4:
        meta["configured_collection_error"] = "; ".join(rechecks.errors) or (
            "missing boundary stability rechecks"
        )
        return [], BUSAN_GANGSEO_PARSER, meta
    try:
        local_first_check, _, _ = _parse_local_page(
            rechecks.values[("recheck", "local", "first")][0],
            page=1,
            expected_total=local_total,
            expected_last=local_last,
        )
        local_last_check, _, _ = _parse_local_page(
            rechecks.values[("recheck", "local", "last")][0],
            page=local_last,
            expected_total=local_total,
            expected_last=local_last,
        )
        city_first_check, _ = _parse_city_page(
            rechecks.values[("recheck", "city", "first")][0],
            page=1,
            expected_last=city_last,
        )
        city_last_check, _ = _parse_city_page(
            rechecks.values[("recheck", "city", "last")][0],
            page=city_last,
            expected_last=city_last,
        )
        if (
            _signature(local_first_check, "source_identity")
            != _signature(local_pages[1], "source_identity")
            or _signature(local_last_check, "source_identity")
            != _signature(local_pages[local_last], "source_identity")
            or _signature(city_first_check, "source_identity")
            != _signature(city_pages[1], "source_identity")
            or _signature(city_last_check, "source_identity")
            != _signature(city_pages[city_last], "source_identity")
        ):
            raise BusanGangseoContractError("first/final boundary changed")
    except Exception as exc:
        meta["configured_collection_error"] = f"stability recheck: {_clean(exc)}"
        return [], BUSAN_GANGSEO_PARSER, meta

    safe_rows: list[dict[str, Any]] = []
    privacy_redactions = 0
    for row in enriched:
        safe, count = _sanitize_row(row)
        safe_rows.append(safe)
        privacy_redactions += count
    deduper = dedupe_rows or _default_dedupe
    result = list(deduper(safe_rows))
    if len(result) != len(safe_rows):
        meta["configured_collection_error"] = (
            f"dedupe changed atomic row count {len(safe_rows)} to {len(result)}"
        )
        return [], BUSAN_GANGSEO_PARSER, meta

    unique_source_rows = len(local_rows) + len(native_rows) + len(city_rows)
    meta.update(
        {
            "network_requests": budget.count,
            "required_list_requests": meta["list_requests"],
            "sentinel_requests": 3,
            "district_source_rows": len(local_rows),
            "district_data_pages": local_last,
            "district_page_counts": {
                page: len(rows)
                for page, rows in local_pages.items()
                if page <= local_last
            },
            "district_current_count": len(local_current),
            "platform_source_rows": len(first_platform),
            "platform_native_rows": len(native_rows),
            "platform_native_current_count": len(native_current),
            "platform_external_duplicate_rows": len(external_rows),
            "platform_external_application_period_lag_rows": (
                external_application_lag_rows
            ),
            "platform_external_unmatched_rows": 0,
            "platform_semantic_censuses": 2,
            "city_source_rows": len(city_rows),
            "city_data_pages": city_last,
            "city_current_count": len(city_current),
            "source_total": len(local_rows) + len(first_platform) + len(city_rows),
            "source_rows": len(local_rows) + len(first_platform) + len(city_rows),
            "unique_education_source_rows": unique_source_rows,
            "current_source_count": len(enriched),
            "expired_count": unique_source_rows - len(enriched),
            "non_current_count": unique_source_rows - len(enriched),
            "returned_count": len(result),
            "detail_pages": len(enriched),
            "detail_errors": 0,
            "application_control_count": sum(
                bool(row.get("reservation_available")) for row in result
            ),
            "closed_application_control_retained_count": sum(
                bool(
                    row.get("raw_fields", {}).get(
                        "closed_application_control_retained"
                    )
                )
                for row in result
            ),
            "status_counts": dict(
                Counter(_clean(row.get("status")) for row in result)
            ),
            "branch_counts": dict(
                Counter(_clean(row.get("branch")) for row in result)
            ),
            "duplicate_source_identity_count": len(external_rows),
            "privacy_redactions": privacy_redactions,
            "pagination_detected": True,
            "pagination_complete": True,
            "details_complete": True,
            "snapshot_complete": True,
            "atomic_union_complete": True,
            "source_cap_reached": False,
            "configured_collection_error": "",
        }
    )
    return result, BUSAN_GANGSEO_PARSER, meta


collect_courses = collect_busan_gangseo_education


__all__ = [
    "BUSAN_GANGSEO_PROVIDER",
    "BUSAN_LIFELONG_PROVIDER",
    "BUSAN_GANGSEO_MUNICIPALITY_CODE",
    "BUSAN_GANGSEO_MUNICIPALITY_NAME",
    "BUSAN_GANGSEO_REGISTERED_URL",
    "BUSAN_GANGSEO_CANONICAL_URL",
    "BUSAN_CITY_GANGSEO_URL",
    "BUSAN_LIFELONG_GANGSEO_OFFICE",
    "BUSAN_GANGSEO_PARSER",
    "BUSAN_GANGSEO_OWNERSHIP_SCOPE",
    "BUSAN_GANGSEO_CANDIDATE_IDS",
    "BUSAN_GANGSEO_OWNER_BOUNDARY_AUDIT",
    "BUSAN_GANGSEO_DISCOVERY_AUDIT",
    "BusanGangseoContractError",
    "is_busan_gangseo_education_target",
    "is_target",
    "busan_gangseo_list_url",
    "busan_gangseo_detail_url",
    "busan_gangseo_platform_list_url",
    "busan_gangseo_city_list_url",
    "busan_gangseo_city_detail_url",
    "canonical_busan_gangseo_identity",
    "collect_busan_gangseo_education",
    "collect_courses",
]
