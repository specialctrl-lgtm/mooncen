"""Fail-closed collector for Jeongseon Education Library programmes.

The county lifelong-learning application at ``edu.jeongseon.go.kr`` is no
longer serving its catalogue.  This is a separate, currently live public
owner operated by the Gangwon State Office of Education.  It publishes a
bounded programme ledger with stable numeric identities, application and
operation periods, capacity and an explicit receipt state.

Only the public list and detail pages are requested.  Registration checks,
applications, login, attachments and applicant APIs are deliberately outside
the request allowlist and no detail HTML or free-form applicant data is kept.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


JEONGSEON_LIBRARY_PROVIDER = "MUNI_LIB_GWE_GO_KR_20A09F24"
JEONGSEON_LIBRARY_CANDIDATE_ID = "MUNI_IR_3819FB30B5B2"
JEONGSEON_LIBRARY_MUNICIPALITY_CODE = "5177000000"
JEONGSEON_LIBRARY_MUNICIPALITY_NAME = "강원특별자치도 정선군"
JEONGSEON_LIBRARY_BRANCH = "정선교육도서관"
JEONGSEON_LIBRARY_ADDRESS = "강원특별자치도 정선군 정선읍 봉양4길 9"
JEONGSEON_LIBRARY_HOST = "lib.gwe.go.kr"
JEONGSEON_LIBRARY_LIST_PATH = "/jslib/menu/3388/lecture-event/list/all"
JEONGSEON_LIBRARY_LIST_URL = f"https://{JEONGSEON_LIBRARY_HOST}{JEONGSEON_LIBRARY_LIST_PATH}"
JEONGSEON_LIBRARY_DETAIL_PREFIX = "/jslib/menu/3388/lecture-event/"
JEONGSEON_LIBRARY_PAGE_SIZE = 10
JEONGSEON_LIBRARY_MAX_HTML_BYTES = 2_000_000
JEONGSEON_LIBRARY_PARSER = (
    "jeongseon_gwe_programmes+zero_based_all_pages+exact_total+empty_sentinel+"
    "stable_boundaries+all_current_details+pii_allowlist"
)

JEONGSEON_LIBRARY_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-23",
    "canonical_url": JEONGSEON_LIBRARY_LIST_URL,
    "owner": JEONGSEON_LIBRARY_BRANCH,
    "official_address": JEONGSEON_LIBRARY_ADDRESS,
    "live_source_rows": 6,
    "live_current_rows": 6,
    "live_identity_boundaries": ["9178", "8971"],
    "live_empty_sentinel_page": 1,
    "live_receipt_states": ["신청마감"],
    "legacy_lifelong_source": "https://edu.jeongseon.go.kr/lecture?quarter=",
    "legacy_lifelong_live_result": "HTTP 404",
    "ownership_decision": "independent_provincial_education_library_owner",
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"[1-9]\d*")
_DATE = re.compile(r"(?<!\d)(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SOURCE_STATES = frozenset({"접수예정", "접수중", "대기자접수", "신청마감", "종료"})
_OPEN_STATES = frozenset({"접수중", "대기자접수"})
_SAFE_RAW_FIELDS = frozenset(
    {
        "source_identity",
        "source_status",
        "source_category",
        "source_application_method",
        "source_capacity",
        "source_waiting_capacity",
        "detail_verified",
    }
)


class JeongseonLibraryContractError(ValueError):
    """Raised when the official public ledger no longer matches its contract."""


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", html.unescape(str(value or "")).replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    return date.fromisoformat(_clean(value))


def _comparison_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme != "https"
        or parsed.hostname != JEONGSEON_LIBRARY_HOST
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
    ):
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return f"https://{JEONGSEON_LIBRARY_HOST}{parsed.path}" + (f"?{query}" if query else "")


def is_jeongseon_library_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == JEONGSEON_LIBRARY_PROVIDER
        and _comparison_url(_target_value(target, "url")) == JEONGSEON_LIBRARY_LIST_URL
    )


def jeongseon_library_list_url(page: int = 0) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        raise JeongseonLibraryContractError("invalid zero-based list page")
    return JEONGSEON_LIBRARY_LIST_URL if page == 0 else (f"{JEONGSEON_LIBRARY_LIST_URL}?{urlencode({'page': page})}")


def jeongseon_library_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY.fullmatch(value):
        raise JeongseonLibraryContractError("invalid programme identity")
    return f"https://{JEONGSEON_LIBRARY_HOST}{JEONGSEON_LIBRARY_DETAIL_PREFIX}{value}"


def _request_kind(url: Any) -> str:
    parsed = urlparse(_clean(url))
    try:
        port = parsed.port
    except ValueError as exc:
        raise JeongseonLibraryContractError("invalid URL port") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != JEONGSEON_LIBRARY_HOST
        or parsed.username
        or parsed.password
        or port
        or parsed.fragment
        or parsed.params
    ):
        raise JeongseonLibraryContractError("request left the exact library host")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.path == JEONGSEON_LIBRARY_LIST_PATH:
        if not query:
            return "list"
        if len(query) == 1 and query[0][0] == "page" and query[0][1].isdigit():
            return "list"
    if (
        parsed.path.startswith(JEONGSEON_LIBRARY_DETAIL_PREFIX)
        and _IDENTITY.fullmatch(parsed.path[len(JEONGSEON_LIBRARY_DETAIL_PREFIX) :])
        and not query
    ):
        return "detail"
    raise JeongseonLibraryContractError("request route is not public-list/detail allowlisted")


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.trust_env = False
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    _request_kind(url)
    return session.get(url, timeout=timeout, allow_redirects=False)


def _soup(value: Any, requested_url: str) -> BeautifulSoup:
    status = int(getattr(value, "status_code", 200) or 0)
    if status != 200:
        raise JeongseonLibraryContractError(f"HTTP {status}")
    if getattr(value, "history", None):
        raise JeongseonLibraryContractError("redirect history is forbidden")
    headers = getattr(value, "headers", {}) or {}
    if any(str(key).lower() == "location" and item for key, item in headers.items()):
        raise JeongseonLibraryContractError("redirect location is forbidden")
    final_url = _clean(getattr(value, "url", ""))
    if final_url and _comparison_url(final_url) != _comparison_url(requested_url):
        raise JeongseonLibraryContractError("response URL changed")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if isinstance(content, str):
        content = content.encode("utf-8")
    body = bytes(content)
    if not body or len(body) > JEONGSEON_LIBRARY_MAX_HTML_BYTES:
        raise JeongseonLibraryContractError("empty or oversized HTML")
    parsed = BeautifulSoup(body, "html.parser")
    title = _clean(parsed.title.get_text(" ", strip=True) if parsed.title else "")
    if title != "프로그램신청":
        raise JeongseonLibraryContractError(f"wrong page title {title!r}")
    return parsed


class _Requester:
    def __init__(self, session_factory: SessionFactory, fetcher: Fetcher, timeout: int):
        self.session = session_factory()
        self.fetcher = fetcher
        self.timeout = timeout
        self.list_requests = 0
        self.detail_requests = 0

    def get(self, url: str) -> BeautifulSoup:
        kind = _request_kind(url)
        if kind == "list":
            self.list_requests += 1
        else:
            self.detail_requests += 1
        return _soup(self.fetcher(self.session, url, self.timeout), url)

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _pairs(root: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for container in root.select("dl"):
        children = container.find_all(["dt", "dd"], recursive=False)
        for index in range(len(children) - 1):
            if children[index].name != "dt" or children[index + 1].name != "dd":
                continue
            key = _clean(children[index].get_text(" ", strip=True)).rstrip(":")
            value = _clean(children[index + 1].get_text(" ", strip=True))
            if key in result:
                raise JeongseonLibraryContractError(f"duplicate field {key!r}")
            result[key] = value
    return result


def _date_pair(value: Any, field: str) -> tuple[date, date]:
    found = _DATE.findall(_clean(value))
    if len(found) != 2:
        raise JeongseonLibraryContractError(f"{field}: expected exactly two dates")
    values = [date(int(year), int(month), int(day_value)) for year, month, day_value in found]
    if values[0] > values[1]:
        raise JeongseonLibraryContractError(f"{field}: reversed dates")
    return values[0], values[1]


def _capacity(value: Any) -> tuple[int, int, int, int]:
    text = _clean(value).replace(",", "")
    main = re.search(r"(\d{1,6})\s*/\s*(\d{1,6})", text)
    waiting = re.search(r"대기자\s*:\s*(\d{1,6})\s*/\s*(\d{1,6})", text)
    if not main or not waiting:
        raise JeongseonLibraryContractError("capacity contract changed")
    current, total = int(main.group(1)), int(main.group(2))
    wait_current, wait_total = int(waiting.group(1)), int(waiting.group(2))
    if current > total or wait_current > wait_total or total < 1:
        raise JeongseonLibraryContractError("invalid capacity")
    return current, total, wait_current, wait_total


def _page(soup: BeautifulSoup, page: int) -> tuple[int, list[dict[str, Any]], bool]:
    count_node = soup.select_one(".lecture_result_top__count > strong")
    root = soup.select_one("ul.lecture_result_list")
    if count_node is None or root is None or not _clean(count_node.get_text()).isdigit():
        raise JeongseonLibraryContractError(f"page {page}: total/list shell changed")
    total = int(_clean(count_node.get_text()))
    items = root.select(":scope > li.lecture_item")
    no_data = root.select(":scope > li.no_data")
    if not items:
        marker = _clean(no_data[0].get_text(" ", strip=True)) if len(no_data) == 1 else ""
        if marker != "조회되는 문화강좌가 없습니다.":
            raise JeongseonLibraryContractError(f"page {page}: ambiguous empty sentinel")
        return total, [], True
    if no_data:
        raise JeongseonLibraryContractError(f"page {page}: rows and empty marker coexist")
    rows: list[dict[str, Any]] = []
    for item in items:
        link = item.select_one(".lecture_item__title a[href]")
        branch = _clean(
            item.select_one(".lecture_item__library").get_text(" ", strip=True)
            if item.select_one(".lecture_item__library")
            else ""
        )
        if link is None or branch != JEONGSEON_LIBRARY_BRANCH:
            raise JeongseonLibraryContractError("programme identity/owner changed")
        url = urljoin(JEONGSEON_LIBRARY_LIST_URL, _clean(link.get("href")))
        if _request_kind(url) != "detail":
            raise JeongseonLibraryContractError("programme detail route changed")
        identity = urlparse(url).path.rsplit("/", 1)[-1]
        pairs = _pairs(item)
        required = {"신청기간", "운영기간", "신청대상", "모집방법", "모집인원"}
        if not required <= set(pairs):
            raise JeongseonLibraryContractError(f"programme {identity}: list fields changed")
        state = item.select_one(".lecture_item__button > button:first-of-type")
        source_status = _clean(state.get_text(" ", strip=True) if state else "")
        if source_status not in _SOURCE_STATES:
            raise JeongseonLibraryContractError(f"programme {identity}: status changed")
        category_node = item.select_one("[data-category-name]")
        category = _clean(category_node.get("data-category-name") if category_node else "")
        apply_start, apply_end = _date_pair(pairs["신청기간"], "application period")
        start, end = _date_pair(pairs["운영기간"], "operation period")
        current, capacity, wait_current, wait_total = _capacity(pairs["모집인원"])
        rows.append(
            {
                "identity": identity,
                "title": _clean(link.get_text(" ", strip=True)),
                "url": url,
                "branch": branch,
                "category": category,
                "source_status": source_status,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "start": start,
                "end": end,
                "target": pairs["신청대상"],
                "method": pairs["모집방법"],
                "capacity_current": current,
                "capacity_total": capacity,
                "wait_current": wait_current,
                "wait_total": wait_total,
            }
        )
    return total, rows, False


def _detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = _clean(listed["identity"])
    root = soup.select_one("article.lecture_detail")
    heading = soup.select_one(".lecture_detail__title")
    if (
        root is None
        or heading is None
        or not _clean(heading.get_text(" ", strip=True)).startswith(_clean(listed["title"]))
    ):
        raise JeongseonLibraryContractError(f"detail {identity}: title/shell changed")
    pairs = _pairs(root)
    required = {
        "도서관",
        "운영기간",
        "운영시간",
        "신청방법",
        "신청기간",
        "신청대상",
        "모집인원",
        "재료비",
        "참가비",
        "장소",
    }
    if not required <= set(pairs) or pairs["도서관"] != JEONGSEON_LIBRARY_BRANCH:
        raise JeongseonLibraryContractError(f"detail {identity}: fields/owner changed")
    if (
        _date_pair(pairs["신청기간"], "detail application period") != (listed["apply_start"], listed["apply_end"])
        or _date_pair(pairs["운영기간"], "detail operation period") != (listed["start"], listed["end"])
        or _clean(pairs["신청대상"]) != _clean(listed["target"])
    ):
        raise JeongseonLibraryContractError(f"detail {identity}: list/detail mismatch")
    current, total, wait_current, wait_total = _capacity(pairs["모집인원"])
    if (current, total, wait_current, wait_total) != (
        listed["capacity_current"],
        listed["capacity_total"],
        listed["wait_current"],
        listed["wait_total"],
    ):
        raise JeongseonLibraryContractError(f"detail {identity}: capacity mismatch")
    venue = _clean(pairs["장소"])
    if not venue or _PHONE.search(venue) or _EMAIL.search(venue):
        raise JeongseonLibraryContractError(f"detail {identity}: unsafe venue")
    material = _clean(pairs["재료비"])
    participation = _clean(pairs["참가비"])
    charge_parts = []
    if material not in {"", "-", "없음", "무료"}:
        charge_parts.append(f"재료비 {material}")
    if participation not in {"", "-", "없음", "무료"}:
        charge_parts.append(f"참가비 {participation}")
    source_status = _clean(listed["source_status"])
    normalized_status = (
        "OPEN" if source_status in _OPEN_STATES else "UPCOMING" if source_status == "접수예정" else "CLOSED"
    )
    row = {
        "provider": JEONGSEON_LIBRARY_PROVIDER,
        "provider_course_id": f"gwe-jslib:{identity}",
        "title": _clean(listed["title"]),
        "branch": JEONGSEON_LIBRARY_BRANCH,
        "branch_code": "jslib",
        "municipality_code": JEONGSEON_LIBRARY_MUNICIPALITY_CODE,
        "municipality_name": JEONGSEON_LIBRARY_MUNICIPALITY_NAME,
        "venue_name": venue,
        "venue_address": JEONGSEON_LIBRARY_ADDRESS,
        "category": _clean(listed["category"]) or "교육도서관 프로그램",
        "program_type": "교육도서관 프로그램",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "교육청/도서관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "classification_locked": True,
        "raw_url": _clean(listed["url"]),
        "application_url": _clean(listed["url"]) if source_status in _OPEN_STATES else "",
        "application_type": "ONLINE_RESERVATION",
        "source_status": source_status,
        "status": normalized_status,
        "reservation_available": source_status in _OPEN_STATES,
        "period": f"{listed['start'].isoformat()} ~ {listed['end'].isoformat()}",
        "start_date": listed["start"].isoformat(),
        "end_date": listed["end"].isoformat(),
        "apply_period": f"{listed['apply_start'].isoformat()} ~ {listed['apply_end'].isoformat()}",
        "apply_start_date": listed["apply_start"].isoformat(),
        "apply_end_date": listed["apply_end"].isoformat(),
        "schedule_raw": _clean(pairs["운영시간"]),
        "target": _clean(listed["target"]),
        "fee": " / ".join(charge_parts) if charge_parts else "무료",
        "application_method_raw": _clean(pairs["신청방법"]),
        "capacity_current": current,
        "capacity_total": total,
        "capacity_wait_current": wait_current,
        "capacity_wait_total": wait_total,
        "description": _clean(listed["title"]),
        "raw_fields": {
            "source_identity": identity,
            "source_status": source_status,
            "source_category": _clean(listed["category"]),
            "source_application_method": _clean(pairs["신청방법"]),
            "source_capacity": f"{current}/{total}",
            "source_waiting_capacity": f"{wait_current}/{wait_total}",
            "detail_verified": True,
        },
    }
    if set(row["raw_fields"]) - _SAFE_RAW_FIELDS:
        raise JeongseonLibraryContractError("unsafe raw field escaped allowlist")
    return row


def collect_jeongseon_library(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 200,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    session_factory: SessionFactory = _default_session_factory,
    fetcher: Fetcher = _default_fetcher,
    dedupe_fn: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "canonical_url": JEONGSEON_LIBRARY_LIST_URL,
        "municipality_coverage": [JEONGSEON_LIBRARY_MUNICIPALITY_CODE],
        "discovery_audit": dict(JEONGSEON_LIBRARY_DISCOVERY_AUDIT),
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "snapshot_complete": False,
        "configured_collection_error": "",
    }
    if not is_jeongseon_library_target(target):
        meta["configured_collection_error"] = "target is not the registered Jeongseon library owner"
        return [], JEONGSEON_LIBRARY_PARSER, meta
    if max_pages < 2 or detail_limit < 0 or max_workers < 1 or timeout < 1:
        meta["configured_collection_error"] = "invalid bounded collection limits"
        return [], JEONGSEON_LIBRARY_PARSER, meta

    requester = _Requester(session_factory, fetcher, timeout)
    try:
        first_total, first_rows, first_empty = _page(requester.get(jeongseon_library_list_url(0)), 0)
        if first_empty or first_total < 1:
            raise JeongseonLibraryContractError("canonical ledger unexpectedly empty")
        data_pages = math.ceil(first_total / JEONGSEON_LIBRARY_PAGE_SIZE)
        if data_pages + 1 > max_pages:
            raise JeongseonLibraryContractError(f"max_pages cap allows {max_pages - 1} of {data_pages} data pages")
        pages = [first_rows]
        for page in range(1, data_pages):
            total, rows, empty = _page(requester.get(jeongseon_library_list_url(page)), page)
            if total != first_total or empty:
                raise JeongseonLibraryContractError("total/page drift before declared boundary")
            pages.append(rows)
        sentinel_total, sentinel_rows, sentinel_empty = _page(
            requester.get(jeongseon_library_list_url(data_pages)), data_pages
        )
        recheck_total, recheck_rows, recheck_empty = _page(requester.get(jeongseon_library_list_url(0)), 0)
        sentinel2_total, sentinel2_rows, sentinel2_empty = _page(
            requester.get(jeongseon_library_list_url(data_pages)), data_pages
        )

        def signature(rows: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
            return [(row["identity"], row["title"], row["source_status"]) for row in rows]

        if (
            sentinel_total != first_total
            or sentinel_rows
            or not sentinel_empty
            or recheck_total != first_total
            or recheck_empty
            or signature(recheck_rows) != signature(first_rows)
            or sentinel2_total != first_total
            or sentinel2_rows
            or not sentinel2_empty
        ):
            raise JeongseonLibraryContractError("first/sentinel stability check failed")
        source = [row for values in pages for row in values]
        identities = [_clean(row["identity"]) for row in source]
        if len(source) != first_total or len(identities) != len(set(identities)):
            raise JeongseonLibraryContractError("declared total or identity cardinality changed")
        if [int(item) for item in identities] != sorted((int(item) for item in identities), reverse=True):
            raise JeongseonLibraryContractError("programme identities lost descending order")
        cutoff = _today(today)
        current = [row for row in source if row["end"] >= cutoff]
        if len(current) > detail_limit:
            raise JeongseonLibraryContractError(
                f"detail_limit cap allows {detail_limit} of {len(current)} current details"
            )

        def fetch_detail(listed: Mapping[str, Any]) -> dict[str, Any]:
            return _detail(listed, requester.get(jeongseon_library_detail_url(listed["identity"])))

        if len(current) > 1 and max_workers > 1:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(current))) as pool:
                rows = list(pool.map(fetch_detail, current))
        else:
            rows = [fetch_detail(item) for item in current]
        if dedupe_fn is not None:
            deduped = list(dedupe_fn(rows))
            if len(deduped) != len(rows):
                raise JeongseonLibraryContractError("dedupe changed official identity cardinality")
            rows = deduped
        identity_hash = hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest()
        meta.update(
            {
                "source_rows": len(source),
                "source_total": first_total,
                "current_source_count": len(current),
                "expired_source_count": len(source) - len(current),
                "returned_count": len(rows),
                "data_pages": data_pages,
                "page_counts": {index: len(values) for index, values in enumerate(pages)},
                "empty_sentinel_page": data_pages,
                "empty_sentinel_verified": True,
                "stability_rechecks": 2,
                "detail_verified": len(rows),
                "details_complete": len(rows) == len(current),
                "pagination_complete": True,
                "snapshot_complete": len(rows) == len(current),
                "full_snapshot_validated": len(rows) == len(current),
                "no_current_data": not rows,
                "no_current_reason": ("complete owner ledger has no current/future courses" if not rows else ""),
                "source_identity_sha256": identity_hash,
                "source_branch_counts": {JEONGSEON_LIBRARY_BRANCH: len(source)},
                "list_requests": requester.list_requests,
                "detail_requests": requester.detail_requests,
                "applicant_endpoint_requests": 0,
                "pii_values_persisted": 0,
                "configured_collection_error": "",
            }
        )
        return rows, JEONGSEON_LIBRARY_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "returned_count": 0,
                "snapshot_complete": False,
                "list_requests": requester.list_requests,
                "detail_requests": requester.detail_requests,
                "applicant_endpoint_requests": 0,
                "pii_values_persisted": 0,
                "configured_collection_error": _clean(exc),
            }
        )
        return [], JEONGSEON_LIBRARY_PARSER, meta
    finally:
        requester.close()


collect = collect_jeongseon_library

__all__ = [
    "JEONGSEON_LIBRARY_ADDRESS",
    "JEONGSEON_LIBRARY_BRANCH",
    "JEONGSEON_LIBRARY_CANDIDATE_ID",
    "JEONGSEON_LIBRARY_DISCOVERY_AUDIT",
    "JEONGSEON_LIBRARY_LIST_URL",
    "JEONGSEON_LIBRARY_MUNICIPALITY_CODE",
    "JEONGSEON_LIBRARY_PARSER",
    "JEONGSEON_LIBRARY_PROVIDER",
    "JeongseonLibraryContractError",
    "collect",
    "collect_jeongseon_library",
    "is_jeongseon_library_target",
    "jeongseon_library_detail_url",
    "jeongseon_library_list_url",
]
