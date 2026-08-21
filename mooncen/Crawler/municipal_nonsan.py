"""Fail-closed collector for Nonsan OnDam lifelong-learning courses.

The old Nonsan target is a single press release.  The authoritative municipal
catalogue is the OnDam institution partition (``organ=41``) on the official
lifelong-learning portal.  This collector walks the declared catalogue, proves
an empty post-last page and stable boundaries, and verifies every current or
future course against its public detail page.  Applicant lists, instructors,
contacts, attachments, and free-form descriptions are never requested or
persisted.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


NONSAN_PROVIDER = "MUNI_LLLCITY_NONSAN_GO_KR_4109B04C"
NONSAN_CANDIDATE_ID = "MUNI_IR_DDE96C22B096"
NONSAN_MUNICIPALITY_CODE = "4423000000"
NONSAN_MUNICIPALITY_NAME = "충청남도 논산시"
NONSAN_HOST = "lllcity.nonsan.go.kr"
NONSAN_LIST_PATH = "/prog/educate/kor/sub01_01_01_01/list.do"
NONSAN_DETAIL_PATH = "/prog/educate/kor/sub01_01_01_01/view.do"
NONSAN_ORGAN = "41"
NONSAN_PAGE_SIZE = 10
NONSAN_MAX_HTML_BYTES = 4_000_000
NONSAN_CANONICAL_URL = (
    f"https://{NONSAN_HOST}{NONSAN_LIST_PATH}?"
    + urlencode((("organ", NONSAN_ORGAN),))
)
NONSAN_PARSER = (
    "nonsan_ondam_complete_declared_catalogue+all_pages+empty_post_last+"
    "stable_first_last+current_details+identity_bound_application_control+"
    "single_official_institution_branch+applicant_list_exclusion+pii_allowlist"
)

_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_TOTAL = re.compile(r"총\s*([\d,]+)\s*개의\s*등록된\s*강좌")
_CAPACITY = re.compile(r"(\d+)명\s*/\s*(\d+)명")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS = {"접수중": "OPEN", "접수예정": "SCHEDULED", "접수마감": "CLOSED"}
# Both list and detail publish this impossible 2026 -> 2025 period.  The row is
# retained in source accounting but quarantined from current results; no date
# correction is inferred.
NONSAN_REVERSED_PERIOD_ANOMALIES = {
    "1730": ("2026-05-07", "2025-07-23"),
}
NONSAN_LONG_PERIOD_ANOMALIES = {
    "1755": ("2025-05-09", "2026-08-01"),
}
_SAFE_RAW = frozenset(
    {
        "identity",
        "source_page",
        "source_status",
        "source_application_method",
        "source_category",
        "source_institution",
        "source_education_period",
        "source_application_period",
        "detail_verified",
        "applicant_check_control_excluded",
        "application_control_present",
        "service_family",
    }
)
_FORBIDDEN = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
        "image_url",
    }
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class NonsanContractError(ValueError):
    """Raised when the audited official catalogue contract changes."""


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_nonsan_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != NONSAN_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == NONSAN_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == NONSAN_LIST_PATH
        and query == [("organ", NONSAN_ORGAN)]
        and not parsed.fragment
    )


is_target = is_nonsan_education_target


def nonsan_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    query = [("organ", NONSAN_ORGAN)]
    if page > 1:
        query.append(("pageIndex", str(page)))
    return f"https://{NONSAN_HOST}{NONSAN_LIST_PATH}?{urlencode(query)}"


def nonsan_detail_url(identity: str) -> str:
    if not _IDENTITY.fullmatch(str(identity)):
        raise ValueError("invalid course identity")
    return (
        f"https://{NONSAN_HOST}{NONSAN_DETAIL_PATH}?"
        + urlencode((("organ", NONSAN_ORGAN), ("eduNo", str(identity))))
    )


def _session() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any, requested_url: str) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise NonsanContractError(f"unexpected HTTP status {status}")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location"):
        raise NonsanContractError("redirect response is not accepted")
    final_url = str(getattr(value, "url", requested_url) or requested_url)
    parsed = urlparse(final_url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != NONSAN_HOST:
        raise NonsanContractError("response left the official host")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if not content:
        raise NonsanContractError("empty official response")
    if len(content) > NONSAN_MAX_HTML_BYTES:
        raise NonsanContractError("HTML size cap exceeded")
    return BeautifulSoup(content, "lxml")


def _soup(
    url: str,
    timeout: int,
    factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != NONSAN_HOST:
        raise NonsanContractError("non-canonical request refused")
    last_error: Optional[Exception] = None
    for _attempt in range(3):
        current = factory()
        try:
            return _coerce_soup(fetcher(current, url, timeout), url)
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
        finally:
            close = getattr(current, "close", None)
            if callable(close):
                close()
    assert last_error is not None
    raise last_error


def _dates(value: str, identity: str, field: str) -> tuple[date, date]:
    found = _DATE.findall(value)
    if len(found) != 2:
        raise NonsanContractError(f"course {identity}: {field} changed")
    start, end = (date.fromisoformat(item) for item in found)
    if end < start:
        if (
            "education period" not in field
            or NONSAN_REVERSED_PERIOD_ANOMALIES.get(identity)
            != (start.isoformat(), end.isoformat())
        ):
            raise NonsanContractError(f"course {identity}: reversed {field}")
    return start, end


def _without_label(node: Any) -> str:
    clone = BeautifulSoup(str(node), "lxml")
    label = clone.select_one("b")
    if label is not None:
        label.decompose()
    return _clean(clone.get_text(" ", strip=True))


def _list_fields(card: Any, identity: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in card.select(".info > li"):
        label = item.select_one("b")
        key = _clean(label.get_text(" ", strip=True) if label else "")
        if not key or key in result:
            raise NonsanContractError(f"course {identity}: list field labels changed")
        result[key] = _without_label(item)
    required = {"교육기관", "교육장소", "접수기간", "교육기간", "교육시간", "신청/정원"}
    if not required <= set(result):
        raise NonsanContractError(f"course {identity}: list fields missing")
    return result


def _parse_card(card: Any, page: int) -> dict[str, Any]:
    anchor = card.select_one(".tit a[href*='view.do'][href*='eduNo=']")
    if anchor is None:
        raise NonsanContractError(f"page {page}: course identity missing")
    url = urljoin(nonsan_list_url(page), _clean(anchor.get("href")))
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    identity = (query.get("eduNo") or [""])[0]
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != NONSAN_HOST
        or parsed.path != NONSAN_DETAIL_PATH
        or not _IDENTITY.fullmatch(identity)
        or query.get("organ") != [NONSAN_ORGAN]
    ):
        raise NonsanContractError(f"page {page}: invalid course identity")
    title = _clean(anchor.get_text(" ", strip=True))
    category_node = card.select_one(".tit .cate")
    category = _clean(category_node.get_text(" ", strip=True) if category_node else "")
    status_node = card.select_one(".state_btn b")
    method_node = card.select_one(".state_btn .typeC")
    source_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
    method = _clean(method_node.get_text(" ", strip=True) if method_node else "")
    if not title or not category or source_status not in _STATUS or not method:
        raise NonsanContractError(f"course {identity}: title/category/status changed")
    fields = _list_fields(card, identity)
    start, end = _dates(fields["교육기간"], identity, "education period")
    apply_start, apply_end = _dates(fields["접수기간"], identity, "application period")
    capacity = _CAPACITY.search(fields["신청/정원"])
    if capacity is None:
        raise NonsanContractError(f"course {identity}: capacity changed")
    period_anomaly = end < start or NONSAN_LONG_PERIOD_ANOMALIES.get(identity) == (
        start.isoformat(),
        end.isoformat(),
    )
    if (end - start).days > 365 and identity not in NONSAN_LONG_PERIOD_ANOMALIES:
        raise NonsanContractError(f"course {identity}: unrecognized long education period")
    return {
        "identity": identity,
        "page": page,
        "title": title,
        "category": category,
        "source_status": source_status,
        "status": _STATUS[source_status],
        "method": method,
        "institution": fields["교육기관"],
        "venue": fields["교육장소"],
        "apply_period": fields["접수기간"],
        "apply_start": apply_start,
        "apply_end": apply_end,
        "period": fields["교육기간"],
        "start": start,
        "end": end,
        "period_anomaly": period_anomaly,
        "schedule": fields["교육시간"],
        "capacity_current": int(capacity.group(1)),
        "capacity_total": int(capacity.group(2)),
    }


def _parse_page(soup: BeautifulSoup, requested: int) -> dict[str, Any]:
    total_node = soup.select_one(".total_chk")
    match = _TOTAL.search(_clean(total_node.get_text(" ", strip=True) if total_node else ""))
    if match is None:
        raise NonsanContractError(f"page {requested}: declared total missing")
    total = int(match.group(1).replace(",", ""))
    last = max(1, math.ceil(total / NONSAN_PAGE_SIZE))
    cards = soup.select(".courses_wrap > .list")
    rows = [_parse_card(card, requested) for card in cards]
    if requested <= last:
        expected = min(NONSAN_PAGE_SIZE, total - ((requested - 1) * NONSAN_PAGE_SIZE))
        if len(rows) != expected:
            raise NonsanContractError(
                f"page {requested}: expected {expected} courses, found {len(rows)}"
            )
    elif rows:
        raise NonsanContractError("post-last page is not structurally empty")
    identities = [row["identity"] for row in rows]
    if len(identities) != len(set(identities)):
        raise NonsanContractError(f"page {requested}: duplicate identities")
    return {"requested": requested, "total": total, "last": last, "rows": rows}


def _signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        page["total"],
        page["last"],
        tuple(
            (row["identity"], row["title"], row["start"], row["end"], row["source_status"])
            for row in page["rows"]
        ),
    )


def _detail_fields(table: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in table.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index + 1 < len(cells):
            if cells[index].name != "th" or cells[index + 1].name != "td":
                raise NonsanContractError("detail table cell pairing changed")
            key = _clean(cells[index].get_text(" ", strip=True))
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if key in result and result[key] != value:
                raise NonsanContractError(f"conflicting detail field {key}")
            result[key] = value
            index += 2
    return result


def _controls(root: Any, identity: str, detail_url: str) -> tuple[tuple[str, ...], int]:
    applications: list[str] = []
    applicant_checks = 0
    for anchor in root.select("a[href]"):
        text = _clean(anchor.get_text(" ", strip=True))
        href = urljoin(detail_url, _clean(anchor.get("href")))
        parsed = urlparse(href)
        if (parsed.hostname or "").lower() != NONSAN_HOST:
            if "신청" in text:
                raise NonsanContractError("application control left official host")
            continue
        query = parse_qs(parsed.query)
        if "/educate_reserve/" not in parsed.path:
            continue
        if parsed.path.endswith("/allList.do"):
            if query.get("eduNo") != [identity]:
                raise NonsanContractError("applicant-check identity drift")
            applicant_checks += 1
            continue
        if parsed.path.endswith("/myList.do"):
            continue
        if "신청" in text:
            if query.get("eduNo") != [identity]:
                raise NonsanContractError("application-control identity drift")
            applications.append(href)
    if applicant_checks != 1:
        raise NonsanContractError("applicant-check control shape changed")
    return tuple(dict.fromkeys(applications)), applicant_checks


def _detail(listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date) -> dict[str, Any]:
    root = soup.select_one("#contents")
    if root is None:
        raise NonsanContractError(f"course {listed['identity']}: detail root missing")
    tables = [table for table in root.select("table.tbl_basic") if "강좌명" in table.get_text()]
    if len(tables) != 1:
        raise NonsanContractError(f"course {listed['identity']}: detail table changed")
    fields = _detail_fields(tables[0])
    required = {"강좌명", "교육기간", "교육시간", "접수기간", "교육장소", "정원", "교육대상", "수강료", "교육기관"}
    if not required <= set(fields):
        raise NonsanContractError(f"course {listed['identity']}: detail fields missing")
    identity = str(listed["identity"])
    status_node = root.select_one(".state_btn b")
    method_node = root.select_one(".state_btn .typeC")
    detail_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
    detail_method = _clean(method_node.get_text(" ", strip=True) if method_node else "")
    detail_start, detail_end = _dates(fields["교육기간"], identity, "detail education period")
    apply_start, apply_end = _dates(fields["접수기간"], identity, "detail application period")
    capacity = re.fullmatch(r"(\d+)명", fields["정원"])
    if (
        fields["강좌명"] != listed["title"]
        or detail_status != listed["source_status"]
        or detail_method != listed["method"]
        or fields["교육기관"] != listed["institution"]
        or fields["교육장소"] != listed["venue"]
        or (detail_start, detail_end) != (listed["start"], listed["end"])
        or (apply_start, apply_end) != (listed["apply_start"], listed["apply_end"])
        or fields["교육시간"] != listed["schedule"]
        or capacity is None
        or int(capacity.group(1)) != listed["capacity_total"]
        or detail_end < cutoff
    ):
        raise NonsanContractError(f"course {identity}: list/detail identity drift")
    detail_url = nonsan_detail_url(identity)
    controls, applicant_checks = _controls(root, identity, detail_url)
    status = str(listed["status"])
    if status == "OPEN" and len(controls) != 1:
        raise NonsanContractError(f"course {identity}: open application control changed")
    if status != "OPEN" and controls:
        raise NonsanContractError(f"course {identity}: inactive application control exposed")
    application_url = controls[0] if controls else ""
    period = f"{detail_start.isoformat()} ~ {detail_end.isoformat()}"
    row = {
        "provider": NONSAN_PROVIDER,
        "provider_course_id": f"{NONSAN_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": listed["title"],
        "description": listed["title"],
        "branch": listed["institution"],
        "branch_code": "NONSAN_LIFELONG_ONDAM",
        "preserve_branch": True,
        "category": listed["category"],
        "program_type": "교육",
        "raw_url": detail_url,
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if application_url else "INFO_ONLY",
        "application_method": listed["method"],
        "reservation_available": bool(application_url),
        "status": status,
        "fee": fields["수강료"],
        "period": period,
        "start_date": detail_start.isoformat(),
        "end_date": detail_end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "schedule_raw": listed["schedule"],
        "capacity": fields["정원"],
        "capacity_current": listed["capacity_current"],
        "capacity_total": listed["capacity_total"],
        "target": fields["교육대상"],
        "venue": listed["venue"],
        "venue_name": listed["venue"],
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": NONSAN_PARSER,
        "municipality_code": NONSAN_MUNICIPALITY_CODE,
        "municipality_full_name": NONSAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": listed["page"],
            "source_status": listed["source_status"],
            "source_application_method": listed["method"],
            "source_category": listed["category"],
            "source_institution": listed["institution"],
            "source_education_period": listed["period"],
            "source_application_period": listed["apply_period"],
            "detail_verified": True,
            "applicant_check_control_excluded": applicant_checks == 1,
            "application_control_present": bool(application_url),
            "service_family": "education",
        },
    }
    return row


def _privacy(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN:
        errors.append("forbidden detail/PII key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW:
        errors.append("raw field allowlist exceeded")
    public = {
        key: row.get(key)
        for key in ("title", "description", "branch", "fee", "target", "venue", "venue_name", "application_method", "raw_fields")
    }
    payload = repr(public)
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("contact data persisted")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = str(row["provider_course_id"])
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


def collect_nonsan_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "municipality_code": NONSAN_MUNICIPALITY_CODE,
        "owner_provider": NONSAN_PROVIDER,
        "canonical_url": NONSAN_CANONICAL_URL,
        "parser": NONSAN_PARSER,
        "list_requests": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }
    if not is_nonsan_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Nonsan OnDam owner"
        return [], NONSAN_PARSER, meta
    try:
        cutoff = _today(today)
        if (
            isinstance(timeout, bool)
            or int(timeout) < 1
            or isinstance(max_pages, bool)
            or int(max_pages) < 1
            or isinstance(detail_limit, bool)
            or int(detail_limit) < 0
        ):
            raise ValueError("invalid collection limits")
    except Exception as exc:
        meta.update({"source_cap_reached": True, "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}"})
        return [], NONSAN_PARSER, meta
    factory, current_fetcher = session_factory or _session, fetcher or _request
    try:
        first = _parse_page(_soup(nonsan_list_url(1), int(timeout), factory, current_fetcher), 1)
        meta["list_requests"] = 1
        required = int(first["last"]) + 3
        if required > int(max_pages):
            raise NonsanContractError(f"max_pages {max_pages} below required {required}")
        pages: dict[int, Mapping[str, Any]] = {1: first}
        for page in range(2, int(first["last"]) + 1):
            pages[page] = _parse_page(_soup(nonsan_list_url(page), int(timeout), factory, current_fetcher), page)
            meta["list_requests"] += 1
        sentinel_page = int(first["last"]) + 1
        sentinel = _parse_page(_soup(nonsan_list_url(sentinel_page), int(timeout), factory, current_fetcher), sentinel_page)
        first_check = _parse_page(_soup(nonsan_list_url(1), int(timeout), factory, current_fetcher), 1)
        last_check = _parse_page(_soup(nonsan_list_url(int(first["last"])), int(timeout), factory, current_fetcher), int(first["last"]))
        meta["list_requests"] += 3
        if sentinel["rows"]:
            raise NonsanContractError("post-last structural empty sentinel failed")
        if _signature(first_check) != _signature(first):
            raise NonsanContractError("first page stability recheck failed")
        if _signature(last_check) != _signature(pages[int(first["last"])]):
            raise NonsanContractError("last page stability recheck failed")
        if any(page["total"] != first["total"] or page["last"] != first["last"] for page in pages.values()):
            raise NonsanContractError("catalogue boundary drift")
        listed = [row for page in range(1, int(first["last"]) + 1) for row in pages[page]["rows"]]
        identities = [row["identity"] for row in listed]
        if len(listed) != first["total"] or len(identities) != len(set(identities)):
            raise NonsanContractError("declared catalogue is incomplete")
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["source_cap_reached"] = "max_pages" in meta["configured_collection_error"]
        return [], NONSAN_PARSER, meta

    anomalies = [row for row in listed if row["period_anomaly"]]
    current_all = [
        row
        for row in listed
        if not row["period_anomaly"] and row["end"] >= cutoff
    ]
    cancelled = [row for row in current_all if "폐강" in row["title"]]
    test_rows = [row for row in current_all if row["title"].lower().startswith("[test]")]
    current = [
        row
        for row in current_all
        if "폐강" not in row["title"]
        and not row["title"].lower().startswith("[test]")
    ]
    meta.update(
        {
            "cutoff": cutoff.isoformat(),
            "declared_total": first["total"],
            "source_rows": len(listed),
            "source_total": len(listed),
            "data_pages": first["last"],
            "empty_sentinel_page": int(first["last"]) + 1,
            "boundary_rechecks": 2,
            "current_source_count": len(current_all),
            "current_education_count": len(current),
            "excluded_cancelled_count": len(cancelled),
            "excluded_test_record_count": len(test_rows),
            "expired_count": len(listed) - len(current_all) - len(anomalies),
            "period_anomaly_count": len(anomalies),
            "quarantined_period_anomaly_ids": [row["identity"] for row in anomalies],
            "source_status_counts": dict(Counter(row["source_status"] for row in listed)),
            "source_category_counts": dict(Counter(row["category"] for row in listed)),
            "source_institution_counts": dict(Counter(row["institution"] for row in listed)),
            "pagination_complete": True,
        }
    )
    if len(current) > int(detail_limit):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": f"detail_limit {detail_limit} below required {len(current)}",
            }
        )
        return [], NONSAN_PARSER, meta
    rows: list[dict[str, Any]] = []
    try:
        for item in current:
            rows.append(
                _detail(
                    item,
                    _soup(nonsan_detail_url(str(item["identity"])), int(timeout), factory, current_fetcher),
                    cutoff,
                )
            )
            meta["detail_pages"] += 1
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], NONSAN_PARSER, meta
    rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
    rows = list((dedupe_rows or _dedupe)(rows))
    privacy_errors = [error for row in rows for error in _privacy(row)]
    if privacy_errors or len(rows) != len(current):
        meta["configured_collection_error"] = "; ".join(privacy_errors[:5]) or "dedupe changed complete current identity set"
        return [], NONSAN_PARSER, meta
    meta.update(
        {
            "returned_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "branch_counts": dict(Counter(row["branch"] for row in rows)),
            "application_control_count": sum(bool(row["application_url"]) for row in rows),
            "applicant_check_controls_excluded": len(rows),
            "identity_duplicate_count": 0,
            "pii_payload_persisted": False,
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not rows,
        }
    )
    return rows, NONSAN_PARSER, meta


collect = collect_nonsan_education


__all__ = [
    "NONSAN_CANDIDATE_ID",
    "NONSAN_CANONICAL_URL",
    "NONSAN_DETAIL_PATH",
    "NONSAN_HOST",
    "NONSAN_LIST_PATH",
    "NONSAN_MUNICIPALITY_CODE",
    "NONSAN_MUNICIPALITY_NAME",
    "NONSAN_ORGAN",
    "NONSAN_PARSER",
    "NONSAN_PROVIDER",
    "NONSAN_LONG_PERIOD_ANOMALIES",
    "NONSAN_REVERSED_PERIOD_ANOMALIES",
    "NonsanContractError",
    "collect_nonsan_education",
    "is_nonsan_education_target",
    "nonsan_detail_url",
    "nonsan_list_url",
]
