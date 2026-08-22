"""Complete current/future education collector for Jincheon County.

The old configured URL is restricted to one eup/myeon.  This collector owns
the official unfiltered catalogue (``menukey=3236``), reads a bounded date
window plus the server's explicit ``education in progress`` view, verifies
stable pagination and every retained detail, and drops all contact/free-form
content.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


JINCHEON_PROVIDER = "MUNI_WWW_JINCHEON_GO_KR_081643A9"
JINCHEON_CANDIDATE_ID = "MUNI_IR_61C78478AF91"
JINCHEON_OLD_FILTERED_PROVIDER = "MUNI_WWW_JINCHEON_GO_KR_1CD1E7D2"
JINCHEON_MUNICIPALITY_CODE = "4375000000"
JINCHEON_MUNICIPALITY_NAME = "충청북도 진천군"
JINCHEON_HOST = "www.jincheon.go.kr"
JINCHEON_PATH = "/jclll/sub.do"
JINCHEON_CANONICAL_URL = f"https://{JINCHEON_HOST}{JINCHEON_PATH}?menukey=3236"
JINCHEON_PAGE_SIZE = 10
JINCHEON_MAX_WORKERS = 6
JINCHEON_MAX_HTML_BYTES = 4_000_000
JINCHEON_PARSER = (
    "jincheon_complete_date_window+ongoing_scope+declared_page_advisory+"
    "empty_sentinel_discovery+stable_actual_boundaries+current_details+application_state_variants+"
    "identity_bound_login_control+facility_branches+pii_allowlist"
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class JincheonContractError(ValueError):
    """Raised when the official catalogue no longer satisfies its contract."""


@dataclass(frozen=True)
class _Page:
    scope: str
    page: int
    total: int
    displayed_page: int
    last_page: int
    rows: tuple[dict[str, Any], ...]


_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_DATE_RE = re.compile(r"20\d{2}[.-]\d{1,2}[.-]\d{1,2}")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS_MAP = {
    "신청중": "OPEN",
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "신청예정": "SCHEDULED",
    "신청준비": "SCHEDULED",
    "신청마감": "CLOSED",
    "접수마감": "CLOSED",
    "비신청": "CLOSED",
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_scope",
        "source_status",
        "source_education_status",
        "source_application_period",
        "source_education_period",
        "source_schedule",
        "source_target",
        "source_fee",
        "source_venue",
        "source_institution",
        "detail_verified",
        "application_control_present",
        "service_family",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "contact_name",
        "instructor",
        "instructor_name",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_jincheon_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != JINCHEON_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == JINCHEON_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == JINCHEON_PATH
        and parse_qs(parsed.query, keep_blank_values=True) == {"menukey": ["3236"]}
        and not parsed.params
        and not parsed.fragment
    )


is_target = is_jincheon_education_target


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=True)


def _fetch_soup(
    url: str,
    *,
    timeout: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != JINCHEON_HOST:
        raise JincheonContractError("refusing a non-canonical Jincheon URL")
    last_error: Optional[Exception] = None
    for _attempt in range(3):
        session = session_factory()
        try:
            response = fetcher(session, url, timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            body = getattr(response, "content", None)
            if body is None:
                body = str(getattr(response, "text", response)).encode("utf-8")
            if len(body) > JINCHEON_MAX_HTML_BYTES:
                raise JincheonContractError("HTML response exceeded the size limit")
            final = urlparse(str(getattr(response, "url", url)))
            if final.scheme != "https" or (final.hostname or "").lower() != JINCHEON_HOST:
                raise JincheonContractError("request redirected outside the official host")
            return BeautifulSoup(body, "html.parser")
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()
    assert last_error is not None
    raise last_error


def _scope_url(scope: str, page: int, cutoff: date) -> str:
    params: list[tuple[str, str]] = [("menukey", "3236"), ("mode", "list")]
    if scope == "window":
        params.extend(
            [
                ("cnteduBgnde", (cutoff - timedelta(days=370)).strftime("%Y.%m.%d")),
                ("cnteduEndde", "2099.12.31"),
            ]
        )
    elif scope == "ongoing":
        params.append(("searchCnd", "CND04"))
    else:
        raise JincheonContractError(f"unknown catalogue scope: {scope}")
    params.append(("pageIndex", str(page)))
    return f"https://{JINCHEON_HOST}{JINCHEON_PATH}?{urlencode(params)}"


def _detail_url(identity: str) -> str:
    return (
        f"https://{JINCHEON_HOST}{JINCHEON_PATH}?"
        f"{urlencode({'menukey': '3236', 'mode': 'view', 'cnteduNo': identity})}"
    )


def _field_map(root: Any, selector: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in root.select(selector):
        label = item.select_one("strong")
        if label is None:
            continue
        key = _clean(label.get_text(" ", strip=True))
        clone = BeautifulSoup(str(item), "html.parser")
        for strong in clone.select("strong"):
            strong.decompose()
        value = _clean(clone.get_text(" ", strip=True)).lstrip(":").strip()
        if key:
            fields[key] = value
    return fields


def _dates(value: str, *, identity: str, field: str) -> tuple[date, date]:
    tokens = _DATE_RE.findall(value)
    if len(tokens) < 2:
        raise JincheonContractError(f"course {identity}: {field} date range missing")
    parsed = [date.fromisoformat(token.rstrip(".").replace(".", "-")) for token in tokens]
    if parsed[-1] < parsed[0]:
        raise JincheonContractError(f"course {identity}: {field} date range reversed")
    return parsed[0], parsed[-1]


def _parse_page(soup: BeautifulSoup, scope: str, page: int) -> _Page:
    counter = soup.select_one(".bbs_count")
    if counter is None:
        raise JincheonContractError(f"{scope} page {page}: count marker missing")
    strong_numbers = [int(_clean(x.get_text()).replace(",", "")) for x in counter.select("strong")]
    last_match = re.search(r"/\s*(\d+)", _clean(counter.get_text(" ", strip=True)))
    if len(strong_numbers) != 2 or last_match is None:
        raise JincheonContractError(f"{scope} page {page}: pager contract changed")
    total, displayed = strong_numbers
    last = int(last_match.group(1))
    if displayed != page or last < 1:
        raise JincheonContractError(f"{scope} page {page}: pager identity changed")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in soup.select("ul.tb_edu > li.item"):
        anchor = item.select_one("a.tit[href*='cnteduNo=']")
        if anchor is None:
            raise JincheonContractError(f"{scope} page {page}: course identity missing")
        href = urljoin(JINCHEON_CANONICAL_URL, anchor.get("href", ""))
        identity = (parse_qs(urlparse(href).query).get("cnteduNo") or [""])[0]
        if not _IDENTITY_RE.fullmatch(identity) or identity in seen:
            raise JincheonContractError(f"{scope} page {page}: invalid/duplicate identity")
        seen.add(identity)
        title = _clean(anchor.get_text(" ", strip=True))
        fields = _field_map(item, "ul.list > li")
        if not title or "교육기간" not in fields or "운영기관" not in fields:
            raise JincheonContractError(f"course {identity}: required list fields missing")
        start, end = _dates(fields["교육기간"], identity=identity, field="education")
        badges = [_clean(x.get_text(" ", strip=True)) for x in item.select(".btn_edu a, .btn_edu span")]
        source_status = next((x for x in badges if x in _STATUS_MAP), "")
        education_status = next((x for x in badges if x.startswith("교육")), "")
        if not source_status:
            raise JincheonContractError(f"course {identity}: application status missing")
        rows.append(
            {
                "identity": identity,
                "title": title,
                "fields": fields,
                "badges": badges,
                "source_status": source_status,
                "education_status": education_status,
                "start": start,
                "end": end,
                "scope": scope,
            }
        )
    if len(rows) > JINCHEON_PAGE_SIZE:
        raise JincheonContractError(f"{scope} page {page}: page size exceeded")
    return _Page(scope, page, total, displayed, last, tuple(rows))


def _signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last_page,
        tuple((r["identity"], r["title"], r["source_status"], r["end"].isoformat()) for r in page.rows),
    )


def _branch(value: str) -> str:
    value = _PHONE_RE.sub("", _clean(value)).replace("☏", "").strip(" ()-/")
    return value or "진천군평생학습관"


def _branch_code(value: str) -> str:
    return "JINCHEON_" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:12].upper()


def _capacity(value: str) -> tuple[Optional[int], Optional[int]]:
    nums = [int(x) for x in re.findall(r"\d+", value)]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return None, nums[0]
    return None, None


def _detail_row(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    cutoff: date,
) -> dict[str, Any]:
    identity = str(listed["identity"])
    root = soup.select_one(".bbs_edu_view")
    if root is None:
        raise JincheonContractError(f"course {identity}: detail root missing")
    title_node = root.select_one("h3.conH1")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if title != listed["title"]:
        raise JincheonContractError(f"course {identity}: detail identity/title drift")
    fields = _field_map(root, "ul.edu_con > li")
    for key in ("운영기관", "교육기간", "교육장소", "신청방법"):
        if key not in fields:
            raise JincheonContractError(f"course {identity}: detail field {key} missing")
    start, end = _dates(fields["교육기간"], identity=identity, field="education")
    if start != listed["start"] or end != listed["end"] or end < cutoff:
        raise JincheonContractError(f"course {identity}: detail education range drift")
    status_badges = [_clean(x.get_text(" ", strip=True)) for x in root.select(".edu_btn span")]
    source_status = next((x for x in status_badges if x in _STATUS_MAP), "")
    if not source_status or _STATUS_MAP[source_status] != _STATUS_MAP[listed["source_status"]]:
        raise JincheonContractError(f"course {identity}: detail status drift")
    status = _STATUS_MAP[source_status]
    control = soup.select_one("a.btnM_red[onclick*='location.href'][onclick*='/member/index.do']")
    control_present = control is not None
    if status == "OPEN" and not control_present:
        raise JincheonContractError(f"course {identity}: open course lost its login-bound control")
    if status != "OPEN" and control_present:
        raise JincheonContractError(f"course {identity}: non-open course gained an application control")
    institution = _branch(fields["운영기관"])
    source_venue = _clean(fields["교육장소"])
    venue = source_venue or institution
    application_method = _clean(fields["신청방법"])
    applied, capacity = _capacity(fields.get("신청/정원", ""))
    source_fee = _clean(fields.get("교육비", ""))
    fee = source_fee or "요금 별도 안내"
    apply_period = _clean(fields.get("접수기간", ""))
    apply_dates = _DATE_RE.findall(apply_period)
    if len(apply_dates) >= 2:
        apply_start, apply_end = _dates(apply_period, identity=identity, field="application")
    else:
        apply_start = apply_end = None
    if status == "OPEN" and (
        apply_start is None or not apply_start <= cutoff <= apply_end
    ):
        raise JincheonContractError(f"course {identity}: open status/application dates disagree")
    if status == "SCHEDULED" and (
        apply_start is None or apply_start < cutoff
    ):
        raise JincheonContractError(f"course {identity}: scheduled status/application dates disagree")
    source_schedule = _clean(fields.get("교육요일/교육시간", ""))
    source_target = _clean(fields.get("교육대상", ""))
    raw_url = _detail_url(identity)
    row: dict[str, Any] = {
        "provider": JINCHEON_PROVIDER,
        "provider_course_id": f"{JINCHEON_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": institution,
        "branch_code": _branch_code(institution),
        "preserve_branch": True,
        "category": "평생학습",
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": raw_url,
        "application_type": "ONLINE_RESERVATION_LOGIN_REQUIRED" if control_present else "INFO_ONLY",
        "application_method": application_method,
        "application_methods": [application_method] if application_method else [],
        "reservation_available": bool(status == "OPEN" and control_present),
        "status": status,
        "fee": fee,
        "fee_amount": 0 if fee == "무료" else None,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": apply_period,
        "apply_start": apply_start.isoformat() if apply_start is not None else "",
        "apply_end": apply_end.isoformat() if apply_end is not None else "",
        "schedule_raw": source_schedule or "시간 별도 안내",
        "capacity": f"{capacity}명" if capacity is not None else "",
        "capacity_current": applied,
        "capacity_total": capacity,
        "target": source_target or "대상 별도 안내",
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": JINCHEON_PARSER,
        "municipality_code": JINCHEON_MUNICIPALITY_CODE,
        "municipality_full_name": JINCHEON_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_scope": listed["scope"],
            "source_status": source_status,
            "source_education_status": listed["education_status"],
            "source_application_period": apply_period,
            "source_education_period": fields["교육기간"],
            "source_schedule": source_schedule,
            "source_target": source_target,
            "source_fee": source_fee,
            "source_venue": source_venue,
            "source_institution": institution,
            "detail_verified": True,
            "application_control_present": control_present,
            "service_family": "education",
        },
    }
    return row


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the allowlist")
    payload = repr({k: v for k, v in row.items() if k not in {"raw_url", "application_url"}})
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form description persisted")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
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


def collect_jincheon_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 50,
    detail_limit: int = 200,
    today: Optional[date | datetime | str] = None,
    max_workers: int = JINCHEON_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one fail-closed current/future Jincheon education snapshot."""

    meta: dict[str, Any] = {
        "municipality_code": JINCHEON_MUNICIPALITY_CODE,
        "owner_provider": JINCHEON_PROVIDER,
        "canonical_candidate_id": JINCHEON_CANDIDATE_ID,
        "canonical_url": JINCHEON_CANONICAL_URL,
        "parser": JINCHEON_PARSER,
        "pages": 0,
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
    if not is_jincheon_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Jincheon owner"
        return [], JINCHEON_PARSER, meta
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in (timeout, max_pages, max_workers)) or isinstance(detail_limit, bool) or not isinstance(detail_limit, int) or detail_limit < 0:
        meta.update({"source_cap_reached": True, "configured_collection_error": "invalid collection limits"})
        return [], JINCHEON_PARSER, meta
    try:
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], JINCHEON_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    workers = min(max_workers, JINCHEON_MAX_WORKERS)
    all_listed: list[dict[str, Any]] = []
    scope_meta: dict[str, Any] = {}

    def fetch(scope: str, page: int) -> _Page:
        soup = _fetch_soup(
            _scope_url(scope, page, cutoff),
            timeout=timeout,
            session_factory=factory,
            fetcher=current_fetcher,
        )
        return _parse_page(soup, scope, page)

    try:
        for scope in ("window", "ongoing"):
            if meta["list_requests"] >= max_pages:
                raise JincheonContractError(
                    f"max_pages cap reached before {scope} first page"
                )
            first = fetch(scope, 1)
            meta["list_requests"] += 1
            meta["pages"] += 1
            declared_jobs = list(range(2, first.last_page + 1))
            minimum_remaining = len(declared_jobs) + 3
            if meta["list_requests"] + minimum_remaining > max_pages:
                raise JincheonContractError(
                    f"max_pages cap allows {max_pages} list requests; "
                    f"{scope} requires at least {minimum_remaining + 1}"
                )
            pages: dict[int, _Page] = {1: first}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(fetch, scope, page): page
                    for page in declared_jobs
                }
                for future in as_completed(futures):
                    page = futures[future]
                    parsed = future.result()
                    meta["list_requests"] += 1
                    meta["pages"] += 1
                    pages[page] = parsed

            probe_page = first.last_page + 1
            sentinel: Optional[_Page] = None
            while sentinel is None:
                if meta["list_requests"] + 3 > max_pages:
                    raise JincheonContractError(
                        f"max_pages cap reached before {scope} empty sentinel"
                    )
                probed = fetch(scope, probe_page)
                meta["list_requests"] += 1
                meta["pages"] += 1
                if probed.total != first.total or probed.last_page != first.last_page:
                    raise JincheonContractError(f"{scope} catalogue boundary drift")
                if probed.rows:
                    pages[probe_page] = probed
                    probe_page += 1
                else:
                    sentinel = probed

            actual_last = max(pages)
            with ThreadPoolExecutor(max_workers=min(workers, 2)) as pool:
                first_future = pool.submit(fetch, scope, 1)
                last_future = pool.submit(fetch, scope, actual_last)
                first_recheck = first_future.result()
                last_recheck = last_future.result()
            meta["list_requests"] += 2
            meta["pages"] += 2

            for page in range(1, actual_last + 1):
                if page not in pages:
                    raise JincheonContractError(f"{scope} data page {page} missing")
                parsed = pages[page]
                if parsed.total != first.total or parsed.last_page != first.last_page:
                    raise JincheonContractError(f"{scope} catalogue boundary drift")
                if page < actual_last and len(parsed.rows) != JINCHEON_PAGE_SIZE:
                    raise JincheonContractError(f"{scope} page {page}: short non-final page")
                all_listed.extend(dict(row) for row in parsed.rows)
            if _signature(first_recheck) != _signature(first):
                raise JincheonContractError(f"{scope}: first-page stability check failed")
            if _signature(last_recheck) != _signature(pages[actual_last]):
                raise JincheonContractError(f"{scope}: last-page stability check failed")
            scope_rows = [
                row
                for p in range(1, actual_last + 1)
                for row in pages[p].rows
            ]
            visible = len(scope_rows)
            visible_unique = len({row["identity"] for row in scope_rows})
            if visible_unique != visible:
                raise JincheonContractError(f"{scope}: duplicate identities across data pages")
            if visible_unique < first.total:
                raise JincheonContractError(
                    f"{scope}: advertised total {first.total} exceeds unique visible {visible_unique}"
                )
            scope_meta[scope] = {
                "source_total": first.total,
                "visible_rows": visible,
                "advertised_total_delta": visible_unique - first.total,
                "cross_page_duplicate_count": 0,
                "declared_data_pages": first.last_page,
                "data_pages": actual_last,
                "overflow_data_pages": actual_last - first.last_page,
                "page_counts": {str(p): len(pages[p].rows) for p in range(1, actual_last + 1)},
                "empty_sentinel_page": sentinel.page,
            }
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        if "max_pages" in meta["configured_collection_error"]:
            meta["source_cap_reached"] = True
        return [], JINCHEON_PARSER, meta

    identities: dict[str, dict[str, Any]] = {}
    for item in all_listed:
        old = identities.get(item["identity"])
        if old is not None:
            comparable = (old["title"], old["start"], old["end"], old["source_status"])
            incoming = (item["title"], item["start"], item["end"], item["source_status"])
            if comparable != incoming:
                meta["configured_collection_error"] = f"course {item['identity']}: scope duplicate drift"
                return [], JINCHEON_PARSER, meta
            continue
        identities[item["identity"]] = item
    current = [item for item in identities.values() if item["end"] >= cutoff]
    meta.update(
        {
            "cutoff": cutoff.isoformat(),
            "catalogues": scope_meta,
            "source_rows": len(identities),
            "raw_scope_rows": len(all_listed),
            "scope_duplicate_count": len(all_listed) - len(identities),
            "current_source_count": len(current),
            "expired_count": len(identities) - len(current),
            "pagination_complete": True,
        }
    )
    if len(current) > detail_limit:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": f"detail_limit {detail_limit} below required {len(current)}",
            }
        )
        return [], JINCHEON_PARSER, meta

    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    def fetch_detail(item: dict[str, Any]) -> dict[str, Any]:
        soup = _fetch_soup(
            _detail_url(item["identity"]),
            timeout=timeout,
            session_factory=factory,
            fetcher=current_fetcher,
        )
        return _detail_row(item, soup, cutoff)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_detail, item): item["identity"] for item in current}
        for future in as_completed(futures):
            try:
                rows.append(future.result())
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(f"{futures[future]}: {type(exc).__name__}: {_clean(exc)}")
    if errors:
        meta["configured_collection_error"] = "; ".join(errors[:5])
        return [], JINCHEON_PARSER, meta
    rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"], row["title"]))
    deduper = dedupe_rows or _dedupe_default
    rows = list(deduper(rows))
    privacy = [error for row in rows for error in _privacy_errors(row)]
    if privacy or len(rows) != len(current):
        meta["configured_collection_error"] = "; ".join(privacy[:5]) or "dedupe changed the complete identity set"
        return [], JINCHEON_PARSER, meta
    meta.update(
        {
            "returned_count": len(rows),
            "detail_verified": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "branch_counts": dict(Counter(row["branch"] for row in rows)),
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not rows,
        }
    )
    return rows, JINCHEON_PARSER, meta


collect = collect_jincheon_education


__all__ = [
    "JINCHEON_PROVIDER",
    "JINCHEON_CANDIDATE_ID",
    "JINCHEON_OLD_FILTERED_PROVIDER",
    "JINCHEON_MUNICIPALITY_CODE",
    "JINCHEON_CANONICAL_URL",
    "JINCHEON_PARSER",
    "JincheonContractError",
    "is_jincheon_education_target",
    "is_target",
    "collect_jincheon_education",
    "collect",
]
