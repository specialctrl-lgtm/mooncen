"""Fail-closed collector for Okcheon County's official integrated education ledger."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


OKCHEON_PROVIDER = "MUNI_WWW_OC_GO_KR_0B5AD0D4"
OKCHEON_MUNICIPALITY_CODE = "4373000000"
OKCHEON_MUNICIPALITY_NAME = "충청북도 옥천군"
OKCHEON_HOST = "www.oc.go.kr"
OKCHEON_LIST_PATH = "/edulife/selectTnCnteduProgrmListU.do"
OKCHEON_DETAIL_PATH = "/edulife/viewTnCnteduProgrmU.do"
OKCHEON_APPLY_PATH = "/edulife/addTnCnteduProgrmApplcntViewU.do"
OKCHEON_CANONICAL_URL = (
    f"https://{OKCHEON_HOST}{OKCHEON_LIST_PATH}?key=3890&si2=2"
)
OKCHEON_PAGE_SIZE = 10
OKCHEON_MAX_WORKERS = 12
OKCHEON_MAX_HTML_BYTES = 3_000_000
OKCHEON_PARSER = (
    "okcheon_official_integrated_complete_catalogue+all_declared_pages+"
    "exact_last_page_clamp+stable_first_last+current_details+"
    "identity_bound_application_controls+facility_branches+"
    "locked_education_experience_semantics+pii_allowlist"
)

_EXPERIENCE_COURSE_GROUPS = frozenset({"화목한 원데이 클래스"})
_EXPERIENCE_EXACT_TITLES = {
    "2491": "청소년수련관 (여름방학) 원-데이 눈이 번쩍 AI 클래스",
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class OkcheonContractError(ValueError):
    """Raised when the audited official source contract changes."""


@dataclass(frozen=True)
class _Page:
    requested: int
    observed: int
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE = re.compile(r"20\d{2}-\d{1,2}-\d{1,2}")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LEADING_VENUE_NOTE = re.compile(r"^(?:\[[^\]]+\]\s*)+")
_MARKER = re.compile(
    r"총\s*([\d,]+)\s*건\s*\[\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\]"
)
_SAFE_RAW = frozenset(
    {
        "identity",
        "list_page",
        "source_status",
        "source_methods",
        "source_institution",
        "source_course_group",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "detail_verified",
        "application_control_present",
        "service_family",
    }
)
_FORBIDDEN = frozenset(
    {
        "phone",
        "email",
        "contact",
        "instructor",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)
_OPEN_METHODS = {"온라인 수강신청", "수강신청", "방문신청"}


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_okcheon_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != OKCHEON_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (ValueError, TypeError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == OKCHEON_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == OKCHEON_LIST_PATH
        and sorted(pairs) == [("key", "3890"), ("si2", "2")]
        and not parsed.fragment
    )


is_target = is_okcheon_education_target


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
    status = getattr(value, "status_code", 200)
    if int(status) != 200:
        raise OkcheonContractError(f"unexpected HTTP status {status}")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location"):
        raise OkcheonContractError("redirect response is not accepted")
    final_url = str(getattr(value, "url", requested_url) or requested_url)
    final = urlparse(final_url)
    if final.scheme != "https" or (final.hostname or "").lower() != OKCHEON_HOST:
        raise OkcheonContractError("response left the official host")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if not content:
        raise OkcheonContractError("empty official response")
    if len(content) > OKCHEON_MAX_HTML_BYTES:
        raise OkcheonContractError("HTML size cap exceeded")
    return BeautifulSoup(content, "lxml")


def _soup(
    url: str,
    timeout: int,
    factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != OKCHEON_HOST:
        raise OkcheonContractError("non-canonical request refused")
    last_error: Optional[Exception] = None
    for _attempt in range(2):
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


def okcheon_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    params: list[tuple[str, Any]] = [("key", "3890"), ("si2", "2")]
    if page > 1:
        params.append(("cpn", page))
    return f"https://{OKCHEON_HOST}{OKCHEON_LIST_PATH}?{urlencode(params)}"


def okcheon_detail_url(identity: str) -> str:
    if not _IDENTITY.fullmatch(str(identity)):
        raise ValueError("invalid programme identity")
    return (
        f"https://{OKCHEON_HOST}{OKCHEON_DETAIL_PATH}?"
        + urlencode(
            (
                ("progrmNo", identity),
                ("si2", "2"),
                ("sc5", "정상"),
                ("key", "3890"),
            )
        )
    )


def _period(value: str, identity: str, field: str) -> tuple[date, date]:
    values = _DATE.findall(value)
    if len(values) < 2:
        raise OkcheonContractError(f"course {identity}: {field} missing")
    start, end = date.fromisoformat(values[0]), date.fromisoformat(values[-1])
    if end < start:
        raise OkcheonContractError(f"course {identity}: reversed {field}")
    return start, end


def _source_period(value: str, identity: str) -> tuple[date, date, bool]:
    values = _DATE.findall(value)
    if len(values) < 2:
        raise OkcheonContractError(f"course {identity}: education period missing")
    first, second = date.fromisoformat(values[0]), date.fromisoformat(values[-1])
    return min(first, second), max(first, second), second < first


def _source_status(methods: Iterable[str]) -> str:
    values = {_clean(value) for value in methods if _clean(value)}
    if not values:
        raise OkcheonContractError("course status missing")
    if "신청대기" in values:
        return "SCHEDULED"
    if values <= {"교육대기", "교육중", "교육종료"}:
        return "CLOSED"
    if values & _OPEN_METHODS:
        return "OPEN"
    if all("마감" in value for value in values):
        return "CLOSED"
    raise OkcheonContractError(f"unknown course status: {sorted(values)}")


def _parse_page(soup: BeautifulSoup, requested: int) -> _Page:
    marker_node = soup.select_one(".row .small")
    marker = _clean(marker_node.get_text(" ", strip=True) if marker_node else "")
    match = _MARKER.search(marker)
    if match is None:
        raise OkcheonContractError(f"page {requested}: declared count marker missing")
    total = int(match.group(1).replace(",", ""))
    observed, last = int(match.group(2)), int(match.group(3))
    expected_last = max(1, math.ceil(total / OKCHEON_PAGE_SIZE))
    if last != expected_last:
        raise OkcheonContractError(f"page {requested}: declared last page drift")
    if requested <= last and observed != requested:
        raise OkcheonContractError(f"page {requested}: observed page {observed}")
    if requested > last and observed != last:
        raise OkcheonContractError(f"page {requested}: exact last-page clamp missing")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tr in soup.select("table.p-table tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 7:
            raise OkcheonContractError(f"page {requested}: programme row shape changed")
        anchor = cells[2].select_one(
            "a[href*='viewTnCnteduProgrmU.do'][href*='progrmNo=']"
        )
        if anchor is None:
            raise OkcheonContractError(f"page {requested}: programme identity missing")
        href = urljoin(OKCHEON_CANONICAL_URL, anchor.get("href", ""))
        identity = (parse_qs(urlparse(href).query).get("progrmNo") or [""])[0]
        displayed_identity = _clean(cells[0].get_text(" ", strip=True))
        if (
            not _IDENTITY.fullmatch(identity)
            or identity != displayed_identity
            or identity in seen
        ):
            raise OkcheonContractError(f"page {requested}: invalid/duplicate identity")
        seen.add(identity)
        title = _clean(anchor.get_text(" ", strip=True))
        institution_values = [_clean(value) for value in cells[1].stripped_strings]
        if not title or not institution_values:
            raise OkcheonContractError(f"course {identity}: title/institution missing")
        apply_text = _clean(cells[3].get_text(" ", strip=True))
        education_text = _clean(cells[4].get_text(" ", strip=True))
        start, end, period_anomaly = _source_period(education_text, identity)
        methods = tuple(
            _clean(node.get_text(" ", strip=True))
            for node in cells[6].select(".btn")
            if _clean(node.get_text(" ", strip=True))
        )
        status = _source_status(methods)
        rows.append(
            {
                "identity": identity,
                "title": title,
                "institution": institution_values[0],
                "course_group": institution_values[-1],
                "apply_text": apply_text,
                "education_text": education_text,
                "start": start,
                "end": end,
                "capacity_text": _clean(cells[5].get_text(" ", strip=True)),
                "methods": methods,
                "status": status,
                "official_period_anomaly": period_anomaly,
                "list_page": observed,
            }
        )
    expected_rows = OKCHEON_PAGE_SIZE if observed < last else total % OKCHEON_PAGE_SIZE
    if expected_rows == 0:
        expected_rows = OKCHEON_PAGE_SIZE
    if len(rows) != expected_rows:
        raise OkcheonContractError(
            f"page {requested}: expected {expected_rows} rows, found {len(rows)}"
        )
    return _Page(requested, observed, total, last, tuple(rows))


def _signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple(
            (
                row["identity"],
                row["title"],
                row["status"],
                row["end"],
            )
            for row in page.rows
        ),
    )


def _detail_fields(root: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in root.select(".desc_item"):
        label = item.select_one(".desc_title")
        value = item.select_one(".desc_text")
        if label is None or value is None:
            continue
        key = _clean(label.get_text(" ", strip=True))
        text = _clean(value.get_text(" ", strip=True))
        if key in result and result[key] != text:
            raise OkcheonContractError(f"conflicting detail field {key}")
        result[key] = text
    return result


def _application_control(soup: BeautifulSoup, identity: str) -> str:
    candidates: list[str] = []
    for anchor in soup.select("a[href*='addTnCnteduProgrmApplcntViewU.do']"):
        href = urljoin(OKCHEON_CANONICAL_URL, anchor.get("href", ""))
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        if (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == OKCHEON_HOST
            and parsed.path == OKCHEON_APPLY_PATH
            and query.get("progrmNo") == [identity]
            and query.get("key") == ["3890"]
        ):
            candidates.append(href)
    if len(candidates) > 1:
        raise OkcheonContractError(f"course {identity}: duplicate application controls")
    return candidates[0] if candidates else ""


def _branch(value: str, institution: str) -> str:
    venue = _PHONE.sub("", _EMAIL.sub("", _clean(value))).strip(" ,-/")
    venue = _LEADING_VENUE_NOTE.sub("", venue).strip(" ,-/")
    if not venue:
        venue = _clean(institution)
    if not venue:
        raise OkcheonContractError("facility branch missing")
    return venue


def _branch_code(value: str) -> str:
    # Keep the opaque identifier alphabetic so a numeric SHA-1 run can never be
    # mistaken for a Korean phone number by the row-level PII guard.
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12].upper()
    return "OKCHEON_" + digest.translate(str.maketrans("0123456789", "GHIJKLMNOP"))


def _service_family(identity: str, title: str, course_group: str) -> str:
    """Classify only explicit official experience semantics in the mixed ledger."""

    clean_title = _clean(title)
    clean_group = _clean(course_group)
    exact_title = _EXPERIENCE_EXACT_TITLES.get(_clean(identity))
    if clean_group in _EXPERIENCE_COURSE_GROUPS or "체험" in clean_title:
        return "experience"
    if exact_title is not None and clean_title == exact_title:
        return "experience"
    return "education"


def _detail(listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date) -> dict[str, Any]:
    identity = str(listed["identity"])
    root = soup.select_one(".edu_program_item")
    title_node = root.select_one(".category_title") if root else None
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if root is None or title != listed["title"]:
        raise OkcheonContractError(f"course {identity}: detail identity drift")
    fields = _detail_fields(root)
    required = {"신청기간", "교육기간", "교육시간", "교육장소", "교육대상", "수강료", "신청/정원"}
    if not required <= set(fields):
        raise OkcheonContractError(f"course {identity}: structured detail fields missing")
    start, end = _period(fields["교육기간"], identity, "detail education period")
    if (start, end) != (listed["start"], listed["end"]) or end < cutoff:
        raise OkcheonContractError(f"course {identity}: detail period drift")
    methods = tuple(
        _clean(node.get_text(" ", strip=True))
        for node in root.select(".category_bdg .category")
        if _clean(node.get_text(" ", strip=True))
    )
    status = _source_status(methods)
    if status != listed["status"]:
        raise OkcheonContractError(f"course {identity}: detail status drift")
    application_url = _application_control(soup, identity)
    online_open = bool(set(methods) & {"온라인 수강신청", "수강신청"})
    visit_open = "방문신청" in methods
    if online_open and not application_url:
        raise OkcheonContractError(f"course {identity}: online application control missing")
    if application_url and not online_open:
        raise OkcheonContractError(f"course {identity}: unexpected application control")

    capacity_match = re.search(
        r"전체\s*:\s*(\d+)\s*/\s*(\d+)", fields["신청/정원"]
    )
    visit_capacity_match = re.search(
        r"방문접수\s*정원\s*:\s*(\d+)", fields["신청/정원"]
    )
    if capacity_match is not None:
        current, capacity = int(capacity_match.group(1)), int(capacity_match.group(2))
    elif visit_capacity_match is not None:
        current, capacity = None, int(visit_capacity_match.group(1))
    else:
        raise OkcheonContractError(f"course {identity}: total capacity missing")
    branch = _branch(fields["교육장소"], str(listed["institution"]))
    raw_url = okcheon_detail_url(identity)
    if application_url:
        application_type = "ONLINE_RESERVATION_LOGIN_REQUIRED"
    elif visit_open:
        application_type = "OFFLINE_VISIT"
    else:
        application_type = "INFO_ONLY"
    method_text = ", ".join(methods)
    family = _service_family(
        identity,
        str(listed["title"]),
        str(listed["course_group"]),
    )
    is_experience = family == "experience"
    row = {
        "provider": OKCHEON_PROVIDER,
        "provider_course_id": f"{OKCHEON_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": listed["title"],
        "description": listed["title"],
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": "옥천 체험" if is_experience else "평생학습",
        "program_type": "체험" if is_experience else "교육",
        "raw_url": raw_url,
        "application_url": application_url,
        "application_type": application_type,
        "application_method": method_text,
        "application_methods": list(methods),
        "reservation_available": bool(application_url),
        "status": status,
        "fee": fields["수강료"],
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": fields["신청기간"],
        "schedule_raw": fields["교육시간"],
        "capacity": f"{capacity}명",
        "capacity_current": current,
        "capacity_total": capacity,
        "target": fields["교육대상"],
        "venue": branch,
        "venue_name": branch,
        "collection_category": "공공예약",
        "domain_category": "체험·견학" if is_experience else "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험" if is_experience else "공공강좌",
        "service_group_policy": "locked",
        "service_family": family,
        "collection_type": OKCHEON_PARSER,
        "municipality_code": OKCHEON_MUNICIPALITY_CODE,
        "municipality_full_name": OKCHEON_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": listed["list_page"],
            "source_status": status,
            "source_methods": list(methods),
            "source_institution": listed["institution"],
            "source_course_group": listed["course_group"],
            "source_apply_period": fields["신청기간"],
            "source_education_period": fields["교육기간"],
            "source_schedule": fields["교육시간"],
            "detail_verified": True,
            "application_control_present": bool(application_url),
            "service_family": family,
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
    payload = repr(
        {key: value for key, value in row.items() if key not in {"raw_url", "application_url"}}
    )
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


def collect_okcheon_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 200,
    detail_limit: int = 300,
    today: Optional[date | datetime | str] = None,
    max_workers: int = OKCHEON_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "municipality_code": OKCHEON_MUNICIPALITY_CODE,
        "owner_provider": OKCHEON_PROVIDER,
        "canonical_url": OKCHEON_CANONICAL_URL,
        "parser": OKCHEON_PARSER,
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
    if not is_okcheon_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Okcheon owner"
        return [], OKCHEON_PARSER, meta
    try:
        cutoff = _today(today)
        if any(
            isinstance(value, bool) or int(value) < 1
            for value in (timeout, max_pages, max_workers)
        ) or isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("invalid collection limits")
    except Exception as exc:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": _clean(exc),
            }
        )
        return [], OKCHEON_PARSER, meta
    factory, current_fetcher = session_factory or _session, fetcher or _request
    workers = min(int(max_workers), OKCHEON_MAX_WORKERS)
    try:
        first = _parse_page(
            _soup(okcheon_list_url(1), int(timeout), factory, current_fetcher), 1
        )
        meta["list_requests"] = 1
        required_requests = first.last + 3
        if required_requests > int(max_pages):
            raise OkcheonContractError(
                f"max_pages {max_pages} below required {required_requests}"
            )
        jobs = [("data", page) for page in range(2, first.last + 1)] + [
            ("clamp", first.last + 1),
            ("first", 1),
            ("last", first.last),
        ]
        pages: dict[int, _Page] = {1: first}
        checks: dict[str, _Page] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    lambda page=page: _parse_page(
                        _soup(
                            okcheon_list_url(page),
                            int(timeout),
                            factory,
                            current_fetcher,
                        ),
                        page,
                    )
                ): (kind, page)
                for kind, page in jobs
            }
            for future in as_completed(futures):
                kind, page = futures[future]
                parsed = future.result()
                meta["list_requests"] += 1
                if kind == "data":
                    pages[page] = parsed
                else:
                    checks[kind] = parsed
        if set(pages) != set(range(1, first.last + 1)):
            raise OkcheonContractError("data page missing")
        if any(
            page.total != first.total or page.last != first.last
            for page in pages.values()
        ):
            raise OkcheonContractError("catalogue boundary drift")
        listed = [
            row
            for page in range(1, first.last + 1)
            for row in pages[page].rows
        ]
        if len(listed) != first.total or len({row["identity"] for row in listed}) != first.total:
            raise OkcheonContractError("declared total does not match unique identities")
        clamp = checks.get("clamp")
        if (
            clamp is None
            or clamp.observed != first.last
            or _signature(clamp) != _signature(pages[first.last])
        ):
            raise OkcheonContractError("post-last exact clamp verification failed")
        if checks.get("first") is None or _signature(checks["first"]) != _signature(first):
            raise OkcheonContractError("first page recheck failed")
        if checks.get("last") is None or _signature(checks["last"]) != _signature(pages[first.last]):
            raise OkcheonContractError("last page recheck failed")
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["source_cap_reached"] = "max_pages" in meta["configured_collection_error"]
        return [], OKCHEON_PARSER, meta

    period_anomalies = [row for row in listed if row["official_period_anomaly"]]
    unsafe_period_anomalies = [
        row
        for row in period_anomalies
        if row["status"] != "CLOSED" or row["end"] >= cutoff
    ]
    if unsafe_period_anomalies:
        meta["configured_collection_error"] = (
            "unsafe current/non-terminal reversed official period: "
            + ",".join(row["identity"] for row in unsafe_period_anomalies[:5])
        )
        return [], OKCHEON_PARSER, meta
    current = [row for row in listed if row["end"] >= cutoff]
    meta.update(
        {
            "cutoff": cutoff.isoformat(),
            "source_rows": len(listed),
            "source_total": first.total,
            "data_pages": first.last,
            "post_last_clamp_page": first.last + 1,
            "boundary_rechecks": 2,
            "current_source_count": len(current),
            "expired_count": len(listed) - len(current),
            "expired_period_anomaly_count": len(period_anomalies),
            "source_status_counts": dict(Counter(row["status"] for row in listed)),
            "pagination_complete": True,
        }
    )
    if len(current) > int(detail_limit):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit {detail_limit} below required {len(current)}"
                ),
            }
        )
        return [], OKCHEON_PARSER, meta

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                lambda item=item: _detail(
                    item,
                    _soup(
                        okcheon_detail_url(item["identity"]),
                        int(timeout),
                        factory,
                        current_fetcher,
                    ),
                    cutoff,
                )
            ): item["identity"]
            for item in current
        }
        for future in as_completed(futures):
            try:
                rows.append(future.result())
                meta["detail_pages"] += 1
            except Exception as exc:
                errors.append(
                    f"{futures[future]}: {type(exc).__name__}: {_clean(exc)}"
                )
    if errors:
        meta["configured_collection_error"] = "; ".join(errors[:5])
        return [], OKCHEON_PARSER, meta
    rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
    rows = list((dedupe_rows or _dedupe)(rows))
    privacy = [error for row in rows for error in _privacy(row)]
    if privacy or len(rows) != len(current):
        meta["configured_collection_error"] = (
            "; ".join(privacy[:5]) or "dedupe changed the complete identity set"
        )
        return [], OKCHEON_PARSER, meta
    meta.update(
        {
            "returned_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "education_rows": sum(
                row["service_family"] == "education" for row in rows
            ),
            "experience_rows": sum(
                row["service_family"] == "experience" for row in rows
            ),
            "classification_complete": all(
                row["service_family"] in {"education", "experience"}
                for row in rows
            ),
            "branch_counts": dict(Counter(row["branch"] for row in rows)),
            "application_control_count": sum(
                1 for row in rows if row["application_url"]
            ),
            "offline_application_count": sum(
                1 for row in rows if row["application_type"] == "OFFLINE_VISIT"
            ),
            "identity_duplicate_count": 0,
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not rows,
        }
    )
    return rows, OKCHEON_PARSER, meta


collect = collect_okcheon_education
