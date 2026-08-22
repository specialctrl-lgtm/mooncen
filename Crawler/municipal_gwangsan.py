"""Fail-closed collector for Gwangsan-gu's official education catalogue.

The official ``배우랑께`` portal exposes one complete course inventory at
``/lecture.cs``.  The portal root, ``index.cs`` carousel, menu URL and
Gwangsan Academy page are discovery views or strict subsets of that inventory
and must not own additional snapshots.

The list is a large historical catalogue.  This collector publishes only
courses whose education end date is today or later, but first proves the
declared total against every explicitly requested thousand-row page and the
immediately following empty sentinel.  The portal's documented form route
honours ``pageUnit``; using it keeps the complete-history proof practical
without relying on unsafe date-order assumptions.  Every publishable row is
then checked against its detail page.  Two historical source typos are
corrected only when their exact course identity and raw date fingerprint still
match the audited values.

This module deliberately does not import ``Crawler_MunicipalYaml`` so the
shared router can import it without creating a cycle.  Production callers
must inject the router's managed fetcher and SafeSession factory.  TLS
certificate verification is never disabled.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import contextvars
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString


GWANGSAN_PROVIDER = "MUNI_EDU_GWANGSAN_GO_KR_C778CD6A"
GWANGSAN_CANDIDATE_ID = "MUNI_IR_35D0DAC7F15D"
GWANGSAN_URL = "https://edu.gwangsan.go.kr/"
GWANGSAN_HOST = "edu.gwangsan.go.kr"
GWANGSAN_LIST_PATH = "/lecture.cs"
GWANGSAN_LIST_URL = f"https://{GWANGSAN_HOST}{GWANGSAN_LIST_PATH}"
GWANGSAN_PAGE_SIZE = 1000
GWANGSAN_PAGE_UNIT_KEY = "pageUnit"
GWANGSAN_SESSION_REQUEST_LIMIT = 150
GWANGSAN_DETAIL_WORKERS = 2
GWANGSAN_PARSER = (
    "gwangsan_baeurangkke_complete_history+page_unit_1000+"
    "empty_sentinel+current_detail"
)
GWANGSAN_MUNICIPALITY_CODE = "1233000000"
GWANGSAN_MUNICIPALITY_NAME = "전남광주통합특별시 광산구"

# Exact historical upstream mistakes found in both list/detail content.  The
# correction is intentionally inert as soon as the publisher fixes a value.
# key -> (field, exact raw range, corrected start, corrected end)
GWANGSAN_DATE_CORRECTIONS: Mapping[str, tuple[str, str, str, str]] = {
    "8255": (
        "education",
        "2424.09.28 ~ 2424.09.28",
        "2024-09-28",
        "2024-09-28",
    ),
    "2986": (
        "application",
        "2022.09.14 ~ 6022.09.16",
        "2022-09-14",
        "2022-09-16",
    ),
}

# Audited aliases/subsets and non-course search candidates.  Shared target
# configuration can bind these constants without rediscovering ownership.
GWANGSAN_DUPLICATE_ALIAS_URLS = (
    "https://edu.gwangsan.go.kr/index.cs",
    "https://edu.gwangsan.go.kr/lecture.cs?m=3",
)
GWANGSAN_DUPLICATE_SUBSET_URLS = (
    "https://edu.gwangsan.go.kr/academy.cs?m=130",
)
GWANGSAN_NOTICE_PROVIDER = "MUNI_WWW_GWANGSAN_GO_KR_D16CCB12"
GWANGSAN_NOTICE_URL = (
    "https://www.gwangsan.go.kr/notList.do?pageId=www13&searchNotSe=05"
)
GWANGSAN_DISCOVERY_PROVIDER = "MUNI_WWW_GWANGSAN_GO_KR_9F6EA046"
GWANGSAN_DISCOVERY_URL = "https://www.gwangsan.go.kr/?os_type=pc"
GWANGSAN_CONTACT_PROVIDER = "MUNI_WWW_GWANGSAN_GO_KR_954EE2EA"
GWANGSAN_CONTACT_URL = (
    "https://www.gwangsan.go.kr/contentsView.do?pageId=www511"
)
GWANGSAN_NON_COURSE_URLS = (
    GWANGSAN_NOTICE_URL,
    GWANGSAN_DISCOVERY_URL,
    GWANGSAN_CONTACT_URL,
)


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_TOTAL_RE = re.compile(r"^([\d,]+)\s*건의\s*강좌가\s*검색되었습니다\.$")
_BRANCH_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
_LIST_HEADERS = (
    "상태",
    "강좌명/교육기관",
    "접수/교육기간",
    "교육대상",
    "신청/정원",
    "수강료/재료비",
)
_LIST_QUERY_KEYS = frozenset(
    {
        "act",
        "id",
        "m",
        "signReceiptState",
        "searchMoney",
        "AgencyClassification",
        "pageIndex",
        "searchCondition",
        "searchEducationTime",
        "searchAgencyId",
        "searchKeyword",
        "teacherId",
    }
)
_SUMMARY_REQUIRED_LABELS = frozenset(
    {
        "교육기관",
        "접수기간",
        "교육기간",
        "교육장소",
        "접수방법",
        "신청인원/정원",
    }
)
_DETAIL_REQUIRED_LABELS = frozenset(
    {
        "강좌분류",
        "교육기간",
        "교육시간",
        "교육장소",
        "교육대상",
        "수강료",
        "교육문의",
        "교육내용",
    }
)
_AGENCY_REQUIRED_LABELS = frozenset({"교육기관", "전화번호", "주소"})
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "교육예정": "CLOSED",
    "교육중": "CLOSED",
    "교육완료": "CLOSED",
    "폐강": "CANCELLED",
    "취소": "CANCELLED",
}
_DETAIL_STATUS_LABELS = frozenset(_STATUS_MAP)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).lower())


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


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _Requester:
    """Rotate injected managed sessions below the shared request budget."""

    def __init__(
        self,
        fetcher: Fetcher,
        session_factory: SessionFactory,
        timeout: int,
    ) -> None:
        self.fetcher = fetcher
        self.session_factory = session_factory
        self.timeout = timeout
        self.current: Any = None
        self.current_calls = 0
        self.calls = 0
        self.sessions = 0

    def get(self, url: str) -> BeautifulSoup:
        if self.current is None or self.current_calls >= GWANGSAN_SESSION_REQUEST_LIMIT:
            _close_quietly(self.current)
            self.current = self.session_factory()
            self.current_calls = 0
            self.sessions += 1
        headers = getattr(self.current, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
                }
            )
        self.current_calls += 1
        self.calls += 1
        return _coerce_soup(self.fetcher(self.current, url, self.timeout))

    def close(self) -> None:
        _close_quietly(self.current)
        self.current = None


def _failure(reason: str) -> dict[str, Any]:
    return {
        "parser": GWANGSAN_PARSER,
        "pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "duplicate_count": 0,
        "duplicate_url_count": 0,
        "semantic_duplicate_count": 0,
        "detail_errors": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": reason,
    }


def is_gwangsan_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return bool(
        _provider(target) == GWANGSAN_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GWANGSAN_HOST
        and parsed.port is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_gwangsan_target


def gwangsan_list_url(page: Any = 1) -> str:
    raw_page = _clean(page)
    if not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    query: list[tuple[str, int]] = [
        (GWANGSAN_PAGE_UNIT_KEY, GWANGSAN_PAGE_SIZE)
    ]
    if int(raw_page) > 1:
        query.append(("pageIndex", int(raw_page)))
    return f"{GWANGSAN_LIST_URL}?{urlencode(query)}"


def gwangsan_detail_url(identity: Any) -> str:
    raw_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"{GWANGSAN_LIST_URL}?" + urlencode(
        (("act", "view"), ("id", raw_identity))
    )


def gwangsan_application_url(identity: Any) -> str:
    raw_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"{GWANGSAN_LIST_URL}?" + urlencode(
        (("act", "signRequest"), ("id", raw_identity))
    )


def _total(soup: BeautifulSoup) -> Optional[int]:
    node = soup.select_one(".page-info")
    match = _TOTAL_RE.fullmatch(_clean(node.get_text(" ", strip=True))) if node else None
    return int(match.group(1).replace(",", "")) if match else None


def _query_page_values(soup: BeautifulSoup) -> set[int]:
    result: set[int] = set()
    for anchor in soup.select("a[href]"):
        query = parse_qs(
            urlparse(urljoin(GWANGSAN_LIST_URL, _clean(anchor.get("href")))).query
        )
        values = query.get("pageIndex") or []
        if len(values) == 1 and values[0].isdigit() and int(values[0]) > 0:
            result.add(int(values[0]))
    return result


def _strict_single(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _link_identity(href: Any, page: int) -> tuple[str, str]:
    parsed = urlparse(urljoin(GWANGSAN_LIST_URL, _clean(href)))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != GWANGSAN_HOST
        or parsed.port is not None
        or parsed.path != GWANGSAN_LIST_PATH
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return "", "course link escaped canonical route"
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) - _LIST_QUERY_KEYS:
        return "", "course link has unexpected query keys"
    identity = _strict_single(query, "id")
    page_value = _strict_single(query, "pageIndex")
    if (
        _strict_single(query, "act") != "view"
        or not _IDENTITY_RE.fullmatch(identity)
        or (page_value and page_value != str(page))
    ):
        return "", "course link identity/page contract changed"
    return identity, ""


def _date_range(
    identity: str,
    field: str,
    value: Any,
) -> tuple[Optional[date], Optional[date], bool]:
    raw = _clean(value)
    correction = GWANGSAN_DATE_CORRECTIONS.get(identity)
    if correction and correction[0] == field and raw == correction[1]:
        return date.fromisoformat(correction[2]), date.fromisoformat(correction[3]), True
    values: list[date] = []
    for match in _DATE_RE.finditer(raw):
        try:
            values.append(date(*(int(part) for part in match.groups())))
        except ValueError:
            continue
    if len(values) != 2 or values[1] < values[0]:
        return None, None, False
    return values[0], values[1], False


def _period(start: Optional[date], end: Optional[date]) -> str:
    if start is None or end is None:
        return ""
    return f"{start.isoformat()} ~ {end.isoformat()}"


def _status(value: Any) -> str:
    return _STATUS_MAP.get(_clean(value), "")


def _branch_code(branch: Any) -> str:
    digest = hashlib.sha1(_normalized(branch).encode("utf-8")).hexdigest()[:10].upper()
    return f"GWANGSAN_BRANCH_{digest}"


def _number_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    numbers = [int(raw.replace(",", "")) for raw in re.findall(r"[\d,]+", _clean(value))]
    if len(numbers) < 2:
        return None, None
    return numbers[0], numbers[1]


def _direct_text(node: Any) -> str:
    if node is None:
        return ""
    return _clean(
        " ".join(
            _clean(child)
            for child in node.children
            if isinstance(child, NavigableString) and _clean(child)
        )
    )


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("branch")),
        _clean(row.get("period")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("target")),
    )


def _source_duplicate_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Strong signature for duplicate official publications after detail audit."""

    return _semantic_signature(row) + (
        _clean(row.get("apply_period")),
        _normalized(row.get("category")),
        _normalized(row.get("status")),
        _normalized(row.get("fee")),
        _clean(row.get("capacity_total")),
        _normalized(row.get("venue_name")),
        _normalized(row.get("venue_address")),
        _normalized(row.get("instructor")),
        _normalized(row.get("contact")),
        _normalized(row.get("application_method_raw")),
        _normalized(row.get("description")),
    )


def _identity_number(row: Mapping[str, Any]) -> int:
    identity = _clean(row.get("provider_course_id")).rsplit(":", 1)[-1]
    return int(identity) if _IDENTITY_RE.fullmatch(identity) else -1


def _remove_source_duplicates(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_source_duplicate_signature(row), []).append(row)

    retained_ids: set[int] = set()
    groups: list[dict[str, Any]] = []
    for values in grouped.values():
        canonical = max(values, key=_identity_number)
        retained_ids.add(id(canonical))
        if len(values) > 1:
            removed = sorted(
                (
                    _clean(row.get("provider_course_id")).rsplit(":", 1)[-1]
                    for row in values
                    if row is not canonical
                ),
                key=int,
                reverse=True,
            )
            groups.append(
                {
                    "kept": _clean(canonical.get("provider_course_id")).rsplit(
                        ":", 1
                    )[-1],
                    "removed": removed,
                }
            )
    groups.sort(key=lambda item: int(item["kept"]), reverse=True)
    return [row for row in rows if id(row) in retained_ids], groups


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            result.append(row)
            seen.add(identity)
    return result


def _parse_list_page(
    soup: BeautifulSoup,
    provider: str,
    page: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    table = soup.select_one("table.listtable_3")
    if table is None:
        return [], [f"page {page}: missing course table"]
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _LIST_HEADERS:
        return [], [f"page {page}: course table headers changed"]

    result: list[dict[str, Any]] = []
    errors: list[str] = []
    previous_identity = ""
    action_identities: list[str] = []
    for index, tr in enumerate(table.select("tbody > tr"), start=1):
        classes = set(tr.get("class") or [])
        if "act" in classes:
            anchor = tr.select_one('a[href*="act=view"][href*="id="]')
            if anchor is None:
                errors.append(f"page {page} row {index}: malformed detail action row")
                continue
            identity, link_error = _link_identity(anchor.get("href"), page)
            if link_error or not previous_identity or identity != previous_identity:
                errors.append(f"page {page} row {index}: action/detail identity mismatch")
            else:
                action_identities.append(identity)
            continue

        anchor = tr.select_one('ul.c-title a[href*="act=view"][href*="id="]')
        if anchor is None:
            text = _clean(tr.get_text(" ", strip=True))
            if text and not any(label in text for label in ("검색 결과가 없습니다", "강좌가 없습니다")):
                errors.append(f"page {page} row {index}: unexpected non-course row")
            continue
        identity, link_error = _link_identity(anchor.get("href"), page)
        if link_error:
            errors.append(f"page {page} row {index}: {link_error}")
            continue
        previous_identity = identity
        cells = tr.find_all("td", recursive=False)
        if len(cells) != len(_LIST_HEADERS):
            errors.append(f"page {page} {identity}: expected six data cells")
            continue
        title_items = cells[1].select("ul.c-title > li")
        title = _clean(anchor.get_text(" ", strip=True))
        if len(title_items) != 3 or not title:
            errors.append(f"page {page} {identity}: malformed title/agency block")
            continue
        category = _clean(title_items[0].get_text(" ", strip=True))
        source_branch = _clean(title_items[2].get_text(" ", strip=True))
        branch_match = _BRANCH_RE.fullmatch(source_branch)
        if not branch_match:
            errors.append(f"page {page} {identity}: malformed dong/agency branch")
            continue
        dong = _clean(branch_match.group(1))
        branch = _clean(branch_match.group(2))

        source_statuses = [
            _clean(node.get_text(" ", strip=True)) for node in cells[0].select("span")
        ]
        normalized_status = _status(source_statuses[0] if source_statuses else "")
        if not normalized_status:
            errors.append(f"page {page} {identity}: unknown source status")
            continue

        raw_periods: dict[str, str] = {}
        for node in cells[2].select("p"):
            label_node = node.select_one("span")
            label = _clean(label_node.get_text(" ", strip=True)) if label_node else ""
            text = _clean(node.get_text(" ", strip=True))
            value = _clean(text[len(label) :]) if label and text.startswith(label) else ""
            if label:
                raw_periods[label] = value
        if set(raw_periods) != {"접수", "교육", "시간"}:
            errors.append(f"page {page} {identity}: period labels changed")
            continue
        apply_start, apply_end, apply_corrected = _date_range(
            identity, "application", raw_periods["접수"]
        )
        start, end, education_corrected = _date_range(
            identity, "education", raw_periods["교육"]
        )
        if None in {apply_start, apply_end, start, end}:
            errors.append(f"page {page} {identity}: invalid date ranges")
            continue

        capacity_current, capacity_total = _number_pair(
            cells[4].get_text(" ", strip=True)
        )
        if capacity_current is None or capacity_total is None:
            errors.append(f"page {page} {identity}: malformed capacity")
            continue
        fees = [_clean(node.get_text(" ", strip=True)) for node in cells[5].select("span")]
        fee = " / ".join(value for value in fees if value) or _clean(
            cells[5].get_text(" ", strip=True)
        )
        raw_url = gwangsan_detail_url(identity)
        result.append(
            {
                "provider": provider,
                "provider_course_id": f"{provider}:lecture:{identity}",
                "title": title,
                "branch": branch,
                "branch_code": _branch_code(branch),
                "category": category or "교육",
                "raw_url": raw_url,
                "reservation_available": False,
                "status": normalized_status,
                "fee": fee or "별도 안내",
                "period": _period(start, end),
                "apply_period": _period(apply_start, apply_end),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "apply_start_date": apply_start.isoformat(),
                "apply_end_date": apply_end.isoformat(),
                "schedule_raw": raw_periods["시간"] or "별도 안내",
                "target": _clean(cells[3].get_text(" ", strip=True)) or "전체",
                "capacity": _clean(cells[4].get_text(" ", strip=True)),
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "venue_name": branch,
                "room": branch,
                "description": title,
                "application_method_raw": "",
                "application_type": "INFORMATION_ONLY",
                "municipality_code": GWANGSAN_MUNICIPALITY_CODE,
                "municipality_name": GWANGSAN_MUNICIPALITY_NAME,
                "region": "전남광주통합특별시",
                "collection_type": "static_html+detail_html",
                "source_group": "municipal_integrated_reservation",
                "raw_fields": {
                    "parser": GWANGSAN_PARSER,
                    "source_statuses": source_statuses,
                    "source_branch": source_branch,
                    "dong": dong,
                    "source_periods": raw_periods,
                    "application_date_corrected": apply_corrected,
                    "education_date_corrected": education_corrected,
                    "clear_application_url": True,
                },
            }
        )

    if action_identities != [
        _clean(row.get("provider_course_id")).rsplit(":", 1)[-1] for row in result
    ]:
        errors.append(f"page {page}: course/action row pairing changed")
    return result, errors


def _pairs_from_dl(container: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if container is None:
        return result
    for dl in container.select("dl"):
        dt = dl.find("dt", recursive=False)
        dd = dl.find("dd", recursive=False)
        if dt is not None and dd is not None:
            result[_clean(dt.get_text(" ", strip=True))] = _clean(
                dd.get_text(" ", strip=True)
            )
    return result


def _pairs_from_table(table: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if table is None:
        return result
    for tr in table.select("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2 and cells[0].name == "th":
            result[_clean(cells[0].get_text(" ", strip=True))] = _clean(
                " ".join(cell.get_text(" ", strip=True) for cell in cells[1:])
            )
    return result


def _application_identity(href: Any, identity: str) -> bool:
    parsed = urlparse(urljoin(GWANGSAN_LIST_URL, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GWANGSAN_HOST
        and parsed.port is None
        and parsed.path == GWANGSAN_LIST_PATH
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
        and set(query) == {"act", "id"}
        and _strict_single(query, "act") == "signRequest"
        and _strict_single(query, "id") == identity
    )


def _detail_contract(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    identity = _clean(row.get("provider_course_id")).rsplit(":", 1)[-1]
    errors: list[str] = []
    summary = soup.select_one(".list_view.lecture .view_info")
    if summary is None:
        return [f"{identity}: missing detail summary"]
    heading = summary.select_one("h4")
    detail_title = _direct_text(heading)
    if _normalized(detail_title) != _normalized(row.get("title")):
        errors.append(f"{identity}: detail/list title mismatch")
    detail_statuses = [
        _clean(node.get_text(" ", strip=True)) for node in heading.select("span")
    ] if heading is not None else []
    if (
        not detail_statuses
        or len(detail_statuses) > 2
        or len(detail_statuses) != len(set(detail_statuses))
        or any(label not in _DETAIL_STATUS_LABELS for label in detail_statuses)
    ):
        errors.append(f"{identity}: unknown detail status")
    source_statuses = row.get("raw_fields", {}).get("source_statuses", [])
    if (
        detail_statuses
        and source_statuses
        and row.get("status") in {"OPEN", "SCHEDULED", "CANCELLED"}
        and detail_statuses[0] != source_statuses[0]
    ):
        errors.append(f"{identity}: detail/list application status mismatch")

    summary_pairs = _pairs_from_dl(summary)
    missing_summary = _SUMMARY_REQUIRED_LABELS - set(summary_pairs)
    if missing_summary:
        errors.append(f"{identity}: missing summary labels {sorted(missing_summary)}")
        return errors
    if _normalized(summary_pairs["교육기관"]) != _normalized(row.get("branch")):
        errors.append(f"{identity}: detail/list agency mismatch")
    apply_start, apply_end, apply_corrected = _date_range(
        identity, "application", summary_pairs["접수기간"]
    )
    start, end, education_corrected = _date_range(
        identity, "education", summary_pairs["교육기간"]
    )
    if _period(apply_start, apply_end) != _clean(row.get("apply_period")):
        errors.append(f"{identity}: detail application period mismatch")
    if _period(start, end) != _clean(row.get("period")):
        errors.append(f"{identity}: detail education period mismatch")

    tables = soup.select("table.res_table")
    if len(tables) < 2:
        errors.append(f"{identity}: missing detail/agency tables")
        return errors
    details = _pairs_from_table(tables[0])
    agency = _pairs_from_table(tables[1])
    missing_details = _DETAIL_REQUIRED_LABELS - set(details)
    missing_agency = _AGENCY_REQUIRED_LABELS - set(agency)
    if missing_details:
        errors.append(f"{identity}: missing detail labels {sorted(missing_details)}")
    if missing_agency:
        errors.append(f"{identity}: missing agency labels {sorted(missing_agency)}")
    if errors:
        return errors

    table_start, table_end, table_corrected = _date_range(
        identity, "education", details["교육기간"]
    )
    if _period(table_start, table_end) != _clean(row.get("period")):
        errors.append(f"{identity}: detail table education period mismatch")
    if not _normalized(details["강좌분류"]).startswith(
        _normalized(row.get("category"))
    ):
        errors.append(f"{identity}: detail category mismatch")
    if _normalized(details["교육시간"]) != _normalized(row.get("schedule_raw")):
        errors.append(f"{identity}: detail schedule mismatch")
    if not _normalized(agency["교육기관"]).startswith(_normalized(row.get("branch"))):
        errors.append(f"{identity}: detail agency table mismatch")

    application_links = summary.select('a.btn-sign[href*="act=signRequest"]')
    if len(application_links) > 1:
        errors.append(f"{identity}: multiple application links")
    application_url = ""
    if application_links:
        if not _application_identity(application_links[0].get("href"), identity):
            errors.append(f"{identity}: application URL contract changed")
        else:
            application_url = gwangsan_application_url(identity)
    application_method = summary_pairs["접수방법"]

    capacity_current, capacity_total = _number_pair(summary_pairs["신청인원/정원"])
    if capacity_current is None or capacity_total is None:
        errors.append(f"{identity}: malformed detail capacity")
    capacity_full = bool(
        capacity_current is not None
        and capacity_total is not None
        and capacity_total > 0
        and capacity_current >= capacity_total
    )
    application_link_suppressed_full = bool(
        row.get("status") == "OPEN"
        and "온라인" in application_method
        and not application_url
        and capacity_full
    )
    if (
        row.get("status") == "OPEN"
        and "온라인" in application_method
        and not application_url
        and not application_link_suppressed_full
    ):
        errors.append(f"{identity}: open online course has no application URL")
    row["capacity_current"] = capacity_current
    row["capacity_total"] = capacity_total
    row["capacity"] = summary_pairs["신청인원/정원"]
    row["venue_name"] = details["교육장소"] or summary_pairs["교육장소"]
    row["room"] = row["venue_name"]
    row["target"] = details["교육대상"] or row["target"]
    row["fee"] = details["수강료"] or row["fee"]
    row["description"] = details["교육내용"] or row["title"]
    row["instructor"] = details.get("강사", "")
    row["contact"] = details["교육문의"]
    row["venue_address"] = agency["주소"]
    row["application_method_raw"] = application_method
    row["application_type"] = (
        "ONLINE_RESERVATION"
        if application_url or "온라인" in application_method
        else "OFFLINE_APPLICATION"
        if any(label in application_method for label in ("방문", "전화", "이메일", "현장"))
        else "INFORMATION_ONLY"
    )
    row["reservation_available"] = bool(
        row.get("status") == "OPEN" and application_url
    )
    if application_url:
        row["application_url"] = application_url
        row["raw_fields"].pop("clear_application_url", None)
    row["raw_fields"].update(
        {
            "detail_statuses": detail_statuses,
            "summary_pairs": summary_pairs,
            "detail_pairs": details,
            "agency_pairs": agency,
            "canonical_application_url": application_url,
            "detail_capacity_full": capacity_full,
            "application_link_suppressed_full": application_link_suppressed_full,
            "detail_application_date_corrected": apply_corrected,
            "detail_education_date_corrected": education_corrected,
            "detail_table_education_date_corrected": table_corrected,
        }
    )
    return errors


def _audit_detail_batch(
    indexed_rows: list[tuple[int, dict[str, Any]]],
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
) -> tuple[int, int, int, list[tuple[int, list[str]]]]:
    """Audit one bounded detail partition with its own managed session."""

    requester = _Requester(fetcher, session_factory, timeout)
    detail_pages = 0
    outcomes: list[tuple[int, list[str]]] = []
    try:
        for index, row in indexed_rows:
            try:
                soup = requester.get(_clean(row.get("raw_url")))
                detail_pages += 1
                outcomes.append((index, _detail_contract(row, soup)))
            except Exception as exc:
                outcomes.append(
                    (
                        index,
                        [
                            f"{_clean(row.get('provider_course_id'))}: "
                            f"detail fetch {type(exc).__name__}"
                        ],
                    )
                )
        return requester.calls, requester.sessions, detail_pages, outcomes
    finally:
        requester.close()


def collect_gwangsan_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 600,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    detail_workers: int = GWANGSAN_DETAIL_WORKERS,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if not is_gwangsan_target(target):
        return [], GWANGSAN_PARSER, _failure(
            "target does not match the canonical Gwangsan education portal anchor"
        )
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], GWANGSAN_PARSER, _failure(
                "managed fetcher and session_factory injection are required"
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    allowed_detail_workers = min(
        GWANGSAN_DETAIL_WORKERS,
        max(1, int(detail_workers)),
    )
    requester = _Requester(fetcher, session_factory, timeout)
    errors: list[str] = []
    page_soups: dict[int, BeautifulSoup] = {}
    page_counts: dict[int, int] = {}
    all_rows: list[dict[str, Any]] = []
    source_total = 0
    required_list_requests = 0
    expected_last = 0
    detail_pages = 0
    detail_errors = 0
    detail_request_count = 0
    detail_session_count = 0
    detail_workers_used = 0
    source_cap_reached = False
    try:
        try:
            first = requester.get(gwangsan_list_url(1))
            page_soups[1] = first
        except Exception as exc:
            errors.append(f"page 1: fetch {type(exc).__name__}")
            first = None
        if first is not None:
            parsed_total = _total(first)
            if parsed_total is None:
                errors.append("page 1: missing official course total")
            else:
                source_total = parsed_total
                expected_last = max(1, math.ceil(source_total / GWANGSAN_PAGE_SIZE))
                advertised_last = max(_query_page_values(first) or {1})
                if advertised_last != expected_last:
                    errors.append(
                        f"advertised page {advertised_last} != expected {expected_last}"
                    )
                required_list_requests = expected_last + 1
                if required_list_requests > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap allows {allowed_pages} of "
                        f"{required_list_requests} required list requests"
                    )

        if not errors:
            for page in range(2, expected_last + 2):
                try:
                    page_soups[page] = requester.get(gwangsan_list_url(page))
                except Exception as exc:
                    errors.append(f"page {page}: fetch {type(exc).__name__}")
                    break

        if not errors:
            for page in range(1, expected_last + 2):
                soup = page_soups.get(page)
                if soup is None:
                    errors.append(f"page {page}: missing fetched page")
                    continue
                if _total(soup) != source_total:
                    errors.append(f"page {page}: official total changed")
                rows, parse_errors = _parse_list_page(
                    soup, GWANGSAN_PROVIDER, page
                )
                errors.extend(parse_errors)
                page_counts[page] = len(rows)
                if page <= expected_last:
                    expected_count = (
                        0
                        if source_total == 0
                        else min(
                            GWANGSAN_PAGE_SIZE,
                            source_total - (page - 1) * GWANGSAN_PAGE_SIZE,
                        )
                    )
                    if len(rows) != expected_count:
                        errors.append(
                            f"page {page}: expected {expected_count} rows, got {len(rows)}"
                        )
                    all_rows.extend(rows)
                elif rows:
                    errors.append("post-boundary sentinel page is not empty")

        if len(all_rows) != source_total:
            errors.append(
                f"declared total {source_total} != parsed rows {len(all_rows)}"
            )
        identities = [_clean(row.get("provider_course_id")) for row in all_rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate provider course identities")
        urls = [_clean(row.get("raw_url")) for row in all_rows]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate canonical course URLs")

        current_rows: list[dict[str, Any]] = []
        expired_count = 0
        for row in all_rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
                continue
            if end < cutoff:
                expired_count += 1
            else:
                current_rows.append(row)
        if len(current_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_rows)} required current/future details"
            )

        if not errors and current_rows:
            # Close the list connection before opening the two bounded detail
            # connections.  Each worker owns and closes its own managed
            # session; rows stay in source order because outcomes are sorted
            # by their original index before errors are committed.
            requester.close()
            detail_workers_used = min(allowed_detail_workers, len(current_rows))
            partitions: list[list[tuple[int, dict[str, Any]]]] = [
                [] for _ in range(detail_workers_used)
            ]
            for index, row in enumerate(current_rows):
                partitions[index % detail_workers_used].append((index, row))

            outcomes: list[tuple[int, list[str]]] = []
            with ThreadPoolExecutor(max_workers=detail_workers_used) as pool:
                future_batches = {}
                for batch in partitions:
                    # Preserve outbound safety/request-budget context in each
                    # worker.  The collector's explicit page/detail caps remain
                    # the authoritative aggregate request bound.
                    context = contextvars.copy_context()
                    future = pool.submit(
                        context.run,
                        _audit_detail_batch,
                        batch,
                        fetcher,
                        session_factory,
                        timeout,
                    )
                    future_batches[future] = batch
                for future in as_completed(future_batches):
                    batch = future_batches[future]
                    try:
                        calls, sessions, pages, batch_outcomes = future.result()
                        detail_request_count += calls
                        detail_session_count += sessions
                        detail_pages += pages
                        outcomes.extend(batch_outcomes)
                    except Exception as exc:
                        for index, row in batch:
                            outcomes.append(
                                (
                                    index,
                                    [
                                        f"{_clean(row.get('provider_course_id'))}: "
                                        f"detail worker {type(exc).__name__}"
                                    ],
                                )
                            )
            for _index, row_errors in sorted(outcomes, key=lambda item: item[0]):
                detail_errors += len(row_errors)
                errors.extend(row_errors)

        signatures = [_semantic_signature(row) for row in current_rows]
        semantic_candidate_duplicate_count = len(signatures) - len(set(signatures))
        canonical_rows = current_rows
        semantic_duplicate_groups: list[dict[str, Any]] = []
        if not errors:
            canonical_rows, semantic_duplicate_groups = _remove_source_duplicates(
                current_rows
            )
        semantic_duplicate_count = sum(
            len(group["removed"]) for group in semantic_duplicate_groups
        )

        result: list[dict[str, Any]] = []
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper(canonical_rows))
            if len(result) != len(canonical_rows):
                errors.append(
                    "dedupe changed canonical row count "
                    f"{len(canonical_rows)} to {len(result)}"
                )
                result = []

        branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        dong_counts = Counter(
            _clean(row.get("raw_fields", {}).get("dong")) for row in current_rows
        )
        status_counts = Counter(_clean(row.get("status")) for row in current_rows)
        returned_branch_counts = Counter(
            _clean(row.get("branch")) for row in canonical_rows
        )
        returned_status_counts = Counter(
            _clean(row.get("status")) for row in canonical_rows
        )
        source_status_counts = Counter(
            "|".join(row.get("raw_fields", {}).get("source_statuses", []))
            for row in all_rows
        )
        correction_count = sum(
            bool(row.get("raw_fields", {}).get("application_date_corrected"))
            + bool(row.get("raw_fields", {}).get("education_date_corrected"))
            for row in all_rows
        )
        snapshot_complete = not errors
        meta = {
            "parser": GWANGSAN_PARSER,
            "pages": len(page_soups),
            "list_requests": len(page_soups),
            "request_count": requester.calls + detail_request_count,
            "session_count": requester.sessions + detail_session_count,
            "list_request_count": requester.calls,
            "detail_request_count": detail_request_count,
            "detail_session_count": detail_session_count,
            "detail_workers": detail_workers_used,
            "detail_pages": detail_pages,
            "source_total": source_total,
            "source_rows": len(all_rows),
            "required_list_requests": required_list_requests,
            "data_pages": expected_last,
            "sentinel_page": expected_last + 1 if expected_last else 0,
            "page_counts": page_counts,
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "deduplicated_current_count": len(canonical_rows),
            "returned_count": len(result),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "returned_branch_counts": dict(returned_branch_counts),
            "dong_count": len(dong_counts),
            "dong_counts": dict(dong_counts),
            "status_counts": dict(status_counts),
            "returned_status_counts": dict(returned_status_counts),
            "source_status_counts": dict(source_status_counts),
            "date_correction_count": correction_count,
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_duplicate_count": semantic_duplicate_count,
            "semantic_candidate_duplicate_count": (
                semantic_candidate_duplicate_count
            ),
            "semantic_duplicate_groups": semantic_duplicate_groups,
            "detail_errors": detail_errors,
            "discovered_links": len(all_rows),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "pagination_detected": expected_last > 1,
            "pagination_complete": bool(
                snapshot_complete and len(page_soups) == required_list_requests
            ),
            "details_complete": bool(
                snapshot_complete and detail_pages == len(current_rows)
            ),
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "all complete Gwangsan education catalogue courses have ended"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
        }
        if errors:
            return [], GWANGSAN_PARSER, meta
        return result, GWANGSAN_PARSER, meta
    finally:
        requester.close()


collect = collect_gwangsan_education_courses


__all__ = [
    "GWANGSAN_CANDIDATE_ID",
    "GWANGSAN_CONTACT_PROVIDER",
    "GWANGSAN_CONTACT_URL",
    "GWANGSAN_DATE_CORRECTIONS",
    "GWANGSAN_DETAIL_WORKERS",
    "GWANGSAN_DISCOVERY_PROVIDER",
    "GWANGSAN_DISCOVERY_URL",
    "GWANGSAN_DUPLICATE_ALIAS_URLS",
    "GWANGSAN_DUPLICATE_SUBSET_URLS",
    "GWANGSAN_HOST",
    "GWANGSAN_LIST_PATH",
    "GWANGSAN_LIST_URL",
    "GWANGSAN_MUNICIPALITY_CODE",
    "GWANGSAN_MUNICIPALITY_NAME",
    "GWANGSAN_NON_COURSE_URLS",
    "GWANGSAN_NOTICE_PROVIDER",
    "GWANGSAN_NOTICE_URL",
    "GWANGSAN_PAGE_SIZE",
    "GWANGSAN_PARSER",
    "GWANGSAN_PROVIDER",
    "GWANGSAN_SESSION_REQUEST_LIMIT",
    "GWANGSAN_URL",
    "collect",
    "collect_gwangsan_education_courses",
    "gwangsan_application_url",
    "gwangsan_detail_url",
    "gwangsan_list_url",
    "is_gwangsan_target",
    "is_target",
]
