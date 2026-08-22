"""Fail-closed collector for the official Goesan lifelong-course ledger."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


GOESAN_PROVIDER = "MUNI_WWW_GOESAN_GO_KR_EAE2C3E3"
GOESAN_MUNICIPALITY_CODE = "4376000000"
GOESAN_MUNICIPALITY_NAME = "충청북도 괴산군"
GOESAN_HOST = "www.goesan.go.kr"
GOESAN_LIST_PATH = "/gslll/GslllEduList.do"
GOESAN_DETAIL_PATH = "/gslll/GslllEduView.do"
GOESAN_CANONICAL_URL = f"https://{GOESAN_HOST}{GOESAN_LIST_PATH}?key=1894"
GOESAN_PAGE_SIZE = 10
GOESAN_MAX_WORKERS = 5
GOESAN_MAX_HTML_BYTES = 3_000_000
GOESAN_PARSER = (
    "goesan_official_lifelong_all_declared_pages+empty_post_last+stable_first_last+"
    "current_details+stable_list_bound_empty_detail_fallback+"
    "identity_bound_application_controls+facility_branch+pii_allowlist"
)
GOESAN_BRANCH_LOCATIONS = {
    "괴산군평생학습관": {
        "address": "충청북도 괴산군 괴산읍 읍내로 184, 괴산군립도서관 3층",
        "source_url": "https://www.goesan.go.kr/gslll/contents.do?key=1891",
    },
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GoesanContractError(ValueError):
    pass


@dataclass(frozen=True)
class _Page:
    page: int
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE = re.compile(r"20\d{2}-\d{1,2}-\d{1,2}")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS = {"신청중": "OPEN", "접수중": "OPEN", "신청예정": "SCHEDULED", "접수예정": "SCHEDULED", "신청마감": "CLOSED", "접수마감": "CLOSED"}
_SAFE_RAW = frozenset({"identity", "list_page", "source_status", "source_apply_period", "source_education_period", "source_schedule", "source_institution", "detail_verified", "detail_unavailable_official_shell", "identity_binding", "application_control_present", "service_family"})
_FORBIDDEN = frozenset({"phone", "email", "contact", "instructor", "attachments", "attachment_urls", "detail_description", "source_html", "raw_html"})


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_goesan_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != GOESAN_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
        query = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GOESAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GOESAN_LIST_PATH
        and query == [("key", "1894")]
        and not parsed.fragment
    )


is_target = is_goesan_education_target


def _session() -> requests.Session:
    value = requests.Session()
    value.headers.update({"User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)", "Accept": "text/html,application/xhtml+xml"})
    return value


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=True)


def _soup(url: str, timeout: int, factory: SessionFactory, fetcher: Fetcher) -> BeautifulSoup:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != GOESAN_HOST:
        raise GoesanContractError("non-canonical URL refused")
    last: Optional[Exception] = None
    for _ in range(2):
        session = factory()
        try:
            response = fetcher(session, url, timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", response)).encode("utf-8")
            if len(content) > GOESAN_MAX_HTML_BYTES:
                raise GoesanContractError("HTML size cap exceeded")
            final = urlparse(str(getattr(response, "url", url)))
            if final.scheme != "https" or (final.hostname or "").lower() != GOESAN_HOST:
                raise GoesanContractError("redirect outside official host")
            return BeautifulSoup(content, "html.parser")
        except (requests.RequestException, TimeoutError) as exc:
            last = exc
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()
    assert last is not None
    raise last


def _list_url(page: int) -> str:
    return f"https://{GOESAN_HOST}{GOESAN_LIST_PATH}?{urlencode({'key': '1894', 'pageIndex': page})}"


def _detail_url(identity: str) -> str:
    return f"https://{GOESAN_HOST}{GOESAN_DETAIL_PATH}?{urlencode({'key': '1894', 'cnteduNo': identity})}"


def _fields(root: Any, selector: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in root.select(selector):
        label = item.select_one("strong")
        if label is None:
            continue
        key = _clean(label.get_text(" ", strip=True))
        clone = BeautifulSoup(str(item), "html.parser")
        for strong in clone.select("strong"):
            strong.decompose()
        result[key] = _clean(clone.get_text(" ", strip=True)).lstrip(":").strip()
    return result


def _period(value: str, identity: str) -> tuple[date, date]:
    tokens = _DATE.findall(value)
    if len(tokens) < 2:
        raise GoesanContractError(f"course {identity}: education period missing")
    start, end = date.fromisoformat(tokens[0]), date.fromisoformat(tokens[-1])
    if end < start:
        raise GoesanContractError(f"course {identity}: reversed education period")
    return start, end


def _parse_page(soup: BeautifulSoup, page: int) -> _Page:
    marker = soup.select_one(".board_info")
    if marker is None:
        raise GoesanContractError(f"page {page}: count marker missing")
    text = _clean(marker.get_text(" ", strip=True))
    numbers = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", text)]
    if len(numbers) < 3 or numbers[1] != page:
        raise GoesanContractError(f"page {page}: pager contract changed")
    total, _, last = numbers[:3]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in soup.select("ul.tb_edu > li.item"):
        anchor = item.select_one("a[href*='GslllEduView.do'][href*='cnteduNo=']")
        if anchor is None:
            raise GoesanContractError(f"page {page}: identity link missing")
        href = urljoin(GOESAN_CANONICAL_URL, anchor.get("href", ""))
        identity = (parse_qs(urlparse(href).query).get("cnteduNo") or [""])[0]
        if not _IDENTITY.fullmatch(identity) or identity in seen:
            raise GoesanContractError(f"page {page}: invalid/duplicate identity")
        seen.add(identity)
        title = _clean(anchor.get_text(" ", strip=True))
        fields = _fields(item, "ul.list > li")
        if not title or not {"운영기관", "교육기간", "접수기간"} <= set(fields):
            raise GoesanContractError(f"course {identity}: required list fields missing")
        start, end = _period(fields["교육기간"], identity)
        status_text = _clean((item.select_one(".btn_edu") or item).get_text(" ", strip=True))
        source_status = next((key for key in _STATUS if key in status_text), "")
        if not source_status:
            raise GoesanContractError(f"course {identity}: status missing")
        rows.append({"identity": identity, "title": title, "fields": fields, "start": start, "end": end, "source_status": source_status, "list_page": page})
    if page <= last and len(rows) > GOESAN_PAGE_SIZE:
        raise GoesanContractError(f"page {page}: page-size contract changed")
    if page > last and rows:
        raise GoesanContractError(f"page {page}: non-empty sentinel")
    return _Page(page, total, last, tuple(rows))


def _signature(page: _Page) -> tuple[Any, ...]:
    return page.total, page.last, tuple((row["identity"], row["title"], row["source_status"], row["end"]) for row in page.rows)


def _branch(institution: str) -> str:
    value = _PHONE.sub("", _clean(institution)).strip(" ()-/")
    if value == "평생학습관":
        return "괴산군평생학습관"
    return f"괴산군 {value}" if value else "괴산군평생학습관"


def _branch_code(value: str) -> str:
    return "GOESAN_" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:12].upper()


def _official_empty_detail_shell(soup: BeautifulSoup) -> bool:
    """Recognise the exact empty shell currently returned for one listed course.

    The requested identity remains bound by the stable canonical list link.  An
    arbitrary error/notice document must not be promoted to a course row.
    """

    titles = soup.select("head > title")
    body = soup.body
    styles = tuple(link.get("href") for link in soup.select("head > link[rel='stylesheet']"))
    scripts = tuple(script.get("src") for script in soup.select("head > script[src]"))
    return bool(
        len(titles) == 1
        and _clean(titles[0].get_text(" ", strip=True)) == "괴산군청"
        and body is not None
        and not _clean(body.get_text(" ", strip=True))
        and body.find(True) is None
        and styles
        == (
            "/site/common/css/style.css",
            "/site/cyber/css/style.css",
            "/site/sport/css/style.css",
            "/site/rfarm/css/style.css",
        )
        and scripts == ("/site/common/js/jquery-1.11.3.min.js",)
        and not soup.select("meta[http-equiv], form, .bbs_edu_view")
    )


def _course_row(
    listed: Mapping[str, Any],
    fields: Mapping[str, str],
    *,
    source_status: str,
    control_present: bool,
    detail_verified: bool,
) -> dict[str, Any]:
    identity = str(listed["identity"])
    start, end = _period(fields["교육기간"], identity)
    status = _STATUS[source_status]
    capacity_values = [int(x) for x in re.findall(r"\d+", fields.get("신청/정원", ""))]
    current = capacity_values[0] if len(capacity_values) >= 2 else None
    capacity = capacity_values[1] if len(capacity_values) >= 2 else (capacity_values[0] if capacity_values else None)
    branch = _branch(fields["운영기관"])
    raw_url = _detail_url(identity)
    row = {
        "provider": GOESAN_PROVIDER,
        "provider_course_id": f"{GOESAN_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": listed["title"],
        "description": listed["title"],
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": "평생학습",
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": raw_url if control_present else "",
        "application_type": "ONLINE_RESERVATION_LOGIN_REQUIRED" if control_present else "INFO_ONLY",
        "application_method": _clean(fields["접수방법"]),
        "application_methods": [_clean(fields["접수방법"])],
        "reservation_available": status == "OPEN" and control_present,
        "status": status,
        "fee": "",
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": _clean(fields["접수기간"]),
        "schedule_raw": _clean(fields.get("교육시간", "")),
        "capacity": f"{capacity}명" if capacity is not None else "",
        "capacity_current": current,
        "capacity_total": capacity,
        "target": _clean(fields.get("교육대상", "")),
        "venue": branch,
        "venue_name": branch,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GOESAN_PARSER,
        "municipality_code": GOESAN_MUNICIPALITY_CODE,
        "municipality_full_name": GOESAN_MUNICIPALITY_NAME,
        "raw_fields": {"identity": identity, "list_page": listed["list_page"], "source_status": source_status, "source_apply_period": fields["접수기간"], "source_education_period": fields["교육기간"], "source_schedule": fields.get("교육시간", ""), "source_institution": branch, "detail_verified": detail_verified, "detail_unavailable_official_shell": not detail_verified, "identity_binding": "stable_list_and_detail_title" if detail_verified else "stable_canonical_list_link", "application_control_present": control_present, "service_family": "education"},
    }
    location = GOESAN_BRANCH_LOCATIONS.get(branch)
    if location:
        address = _clean(location["address"])
        row.update(
            {
                "address": address,
                "venue_address": address,
                "branch_address_source": "OFFICIAL_GOESAN_LIFELONG_DIRECTIONS",
                "branch_location_confidence": 100,
                "branch_location_verified": True,
                "branch_location_query": _clean(location["source_url"]),
            }
        )
    return row


def _detail(listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date) -> dict[str, Any]:
    identity = str(listed["identity"])
    root = soup.select_one(".bbs_edu_view")
    if root is None:
        if not _official_empty_detail_shell(soup):
            raise GoesanContractError(f"course {identity}: detail identity drift")
        fields = listed.get("fields")
        required = {"운영기관", "교육기간", "접수기간", "접수방법", "신청/정원", "교육대상", "교육시간"}
        if not isinstance(fields, Mapping) or not required <= set(fields):
            raise GoesanContractError(f"course {identity}: list fallback fields missing")
        start, end = _period(fields["교육기간"], identity)
        if (start, end) != (listed["start"], listed["end"]) or end < cutoff:
            raise GoesanContractError(f"course {identity}: list fallback period drift")
        source_status = _clean(listed.get("source_status"))
        if source_status not in _STATUS:
            raise GoesanContractError(f"course {identity}: list fallback status drift")
        return _course_row(
            listed,
            fields,
            source_status=source_status,
            control_present=False,
            detail_verified=False,
        )
    title_node = root.select_one("h3")
    if _clean(title_node.get_text(" ", strip=True) if title_node else "") != listed["title"]:
        raise GoesanContractError(f"course {identity}: detail identity drift")
    fields = _fields(root, "ul.edu_con > li")
    if not {"운영기관", "교육기간", "접수기간", "접수방법"} <= set(fields):
        raise GoesanContractError(f"course {identity}: detail fields missing")
    start, end = _period(fields["교육기간"], identity)
    if (start, end) != (listed["start"], listed["end"]) or end < cutoff:
        raise GoesanContractError(f"course {identity}: detail period drift")
    status_text = _clean((root.select_one(".edu_btn") or root).get_text(" ", strip=True))
    source_status = next((key for key in _STATUS if key in status_text), "")
    if not source_status or _STATUS[source_status] != _STATUS[listed["source_status"]]:
        raise GoesanContractError(f"course {identity}: detail status drift")
    status = _STATUS[source_status]
    control = soup.select_one(f"a[href*='cnteduNo={identity}'][href*='Apply'], a[onclick*='{identity}'][onclick*='Apply']")
    if status == "OPEN" and control is None:
        raise GoesanContractError(f"course {identity}: open application control missing")
    return _course_row(
        listed,
        fields,
        source_status=source_status,
        control_present=control is not None,
        detail_verified=True,
    )


def _privacy(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN:
        errors.append("forbidden detail/PII key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW:
        errors.append("raw field allowlist exceeded")
    payload = repr({k: v for k, v in row.items() if k not in {"raw_url", "application_url"}})
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("contact data persisted")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = row["provider_course_id"]
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


def collect_goesan_education(target: Any, *, timeout: int = 30, max_pages: int = 40, detail_limit: int = 200, today: Optional[date | datetime | str] = None, max_workers: int = GOESAN_MAX_WORKERS, session_factory: Optional[SessionFactory] = None, fetcher: Optional[Fetcher] = None, dedupe_rows: Optional[DedupeRows] = None) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {"municipality_code": GOESAN_MUNICIPALITY_CODE, "owner_provider": GOESAN_PROVIDER, "canonical_url": GOESAN_CANONICAL_URL, "parser": GOESAN_PARSER, "list_requests": 0, "detail_pages": 0, "source_rows": 0, "current_source_count": 0, "returned_count": 0, "pagination_complete": False, "snapshot_complete": False, "source_cap_reached": False, "configured_collection_error": ""}
    if not is_goesan_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Goesan owner"
        return [], GOESAN_PARSER, meta
    try:
        cutoff = _today(today)
        if any(isinstance(x, bool) or int(x) < 1 for x in (timeout, max_pages, max_workers)) or isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("invalid limits")
    except Exception as exc:
        meta.update({"source_cap_reached": True, "configured_collection_error": _clean(exc)})
        return [], GOESAN_PARSER, meta
    factory, current_fetcher = session_factory or _session, fetcher or _request
    workers = min(int(max_workers), GOESAN_MAX_WORKERS)
    try:
        first = _parse_page(_soup(_list_url(1), int(timeout), factory, current_fetcher), 1)
        meta["list_requests"] = 1
        required = first.last + 3
        if required > int(max_pages):
            raise GoesanContractError(f"max_pages {max_pages} below required {required}")
        jobs = [("data", p) for p in range(2, first.last + 1)] + [("sentinel", first.last + 1), ("first", 1), ("last", first.last)]
        pages: dict[int, _Page] = {1: first}
        checks: dict[str, _Page] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(lambda p=page: _parse_page(_soup(_list_url(p), int(timeout), factory, current_fetcher), p)): (kind, page) for kind, page in jobs}
            for future in as_completed(futures):
                kind, page = futures[future]
                parsed = future.result()
                meta["list_requests"] += 1
                if kind == "data":
                    pages[page] = parsed
                else:
                    checks[kind] = parsed
        if set(pages) != set(range(1, first.last + 1)):
            raise GoesanContractError("data page missing")
        listed = [row for p in range(1, first.last + 1) for row in pages[p].rows]
        if any(pages[p].total != first.total or pages[p].last != first.last for p in pages):
            raise GoesanContractError("catalogue boundary drift")
        if any(len(pages[p].rows) != GOESAN_PAGE_SIZE for p in range(1, first.last)):
            raise GoesanContractError("short non-final page")
        if len({row["identity"] for row in listed}) != first.total:
            raise GoesanContractError("advertised total does not match unique identities")
        if checks.get("sentinel") is None or checks["sentinel"].rows:
            raise GoesanContractError("empty sentinel missing")
        if checks.get("first") is None or _signature(checks["first"]) != _signature(first):
            raise GoesanContractError("first page recheck failed")
        if checks.get("last") is None or _signature(checks["last"]) != _signature(pages[first.last]):
            raise GoesanContractError("last page recheck failed")
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["source_cap_reached"] = "max_pages" in meta["configured_collection_error"]
        return [], GOESAN_PARSER, meta
    current = [row for row in listed if row["end"] >= cutoff]
    meta.update({"cutoff": cutoff.isoformat(), "source_rows": len(listed), "source_total": first.total, "data_pages": first.last, "empty_sentinel_page": first.last + 1, "current_source_count": len(current), "expired_count": len(listed) - len(current), "pagination_complete": True})
    if len(current) > int(detail_limit):
        meta.update({"source_cap_reached": True, "configured_collection_error": f"detail_limit {detail_limit} below required {len(current)}"})
        return [], GOESAN_PARSER, meta
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(lambda item=item: _detail(item, _soup(_detail_url(item["identity"]), int(timeout), factory, current_fetcher), cutoff)): item["identity"] for item in current}
        for future in as_completed(futures):
            try:
                rows.append(future.result())
                meta["detail_pages"] += 1
            except Exception as exc:
                errors.append(f"{futures[future]}: {type(exc).__name__}: {_clean(exc)}")
    if errors:
        meta["configured_collection_error"] = "; ".join(errors[:5])
        return [], GOESAN_PARSER, meta
    rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
    rows = list((dedupe_rows or _dedupe)(rows))
    privacy = [error for row in rows for error in _privacy(row)]
    if privacy or len(rows) != len(current):
        meta["configured_collection_error"] = "; ".join(privacy[:5]) or "dedupe changed identity set"
        return [], GOESAN_PARSER, meta
    detail_fallback_count = sum(
        bool(row.get("raw_fields", {}).get("detail_unavailable_official_shell"))
        for row in rows
    )
    meta.update({"returned_count": len(rows), "status_counts": dict(Counter(row["status"] for row in rows)), "branch_counts": dict(Counter(row["branch"] for row in rows)), "detail_verified_count": len(rows) - detail_fallback_count, "detail_unavailable_official_shell_count": detail_fallback_count, "identity_binding_complete": all(bool(row.get("raw_fields", {}).get("identity_binding")) for row in rows), "snapshot_complete": True, "full_snapshot_validated": True, "no_current_data": not rows})
    return rows, GOESAN_PARSER, meta


collect = collect_goesan_education
