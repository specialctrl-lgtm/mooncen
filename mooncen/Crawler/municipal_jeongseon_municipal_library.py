"""Fail-closed collector for the Jeongseon County Library culture ledger.

The public web page renders from a first-party JSON catalogue.  The catalogue
contains stable numeric event identities, complete pagination, application and
operation periods, capacity and the fields needed to derive the public receipt
state.  Only the read-only list/detail API is used; login, application,
application history, cancellation, attachment and member routes are forbidden.
"""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests


JEONGSEON_MUNICIPAL_LIBRARY_PROVIDER = "MUNI_LIB_JEONGSEON_GO_KR_DD359707"
JEONGSEON_MUNICIPAL_LIBRARY_CANDIDATE_ID = "MUNI_IR_866D058F0D5F"
JEONGSEON_MUNICIPAL_LIBRARY_CODE = "5177000000"
JEONGSEON_MUNICIPAL_LIBRARY_NAME = "강원특별자치도 정선군"
JEONGSEON_MUNICIPAL_LIBRARY_BRANCH = "정선군립도서관"
JEONGSEON_MUNICIPAL_LIBRARY_ADDRESS = "강원특별자치도 정선군 정선읍 녹송2길 26"
JEONGSEON_MUNICIPAL_LIBRARY_HOST = "lib.jeongseon.go.kr"
JEONGSEON_MUNICIPAL_LIBRARY_URL = "https://lib.jeongseon.go.kr/culture/list?menuIds=10%2C21"
JEONGSEON_MUNICIPAL_LIBRARY_API_PATH = "/1/api/culture/events"
JEONGSEON_MUNICIPAL_LIBRARY_DETAIL_PATH = "/culture/events"
JEONGSEON_MUNICIPAL_LIBRARY_PAGE_SIZE = 10
JEONGSEON_MUNICIPAL_LIBRARY_MAX_JSON_BYTES = 2_000_000
JEONGSEON_MUNICIPAL_LIBRARY_PARSER = (
    "jeongseon_county_library_first_party_json+all_pages+empty_sentinel+"
    "stable_boundaries+all_current_details+safe_field_allowlist"
)
JEONGSEON_MUNICIPAL_LIBRARY_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-23",
    "canonical_url": JEONGSEON_MUNICIPAL_LIBRARY_URL,
    "public_api": f"https://{JEONGSEON_MUNICIPAL_LIBRARY_HOST}{JEONGSEON_MUNICIPAL_LIBRARY_API_PATH}",
    "owner": JEONGSEON_MUNICIPAL_LIBRARY_BRANCH,
    "official_address": JEONGSEON_MUNICIPAL_LIBRARY_ADDRESS,
    "source_rows": 82,
    "current_rows": 6,
    "current_identities": [254, 253, 252, 251, 250, 242],
    "source_identity_sha256": "98c7495786ae6ac3c31df2e16c6cdd88959daefe75ebbdfb0209776fe25594ab",
    "ownership_decision": "independent_municipal_library_culture_owner",
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_POSITIVE = re.compile(r"[1-9]\d*")
_DATETIME = re.compile(r"(20\d{2})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")
_SAFE_SOURCE_KEYS = frozenset(
    {
        "id",
        "libraryId",
        "programType",
        "lectureLocation",
        "subTitle",
        "title",
        "applicationStartDate",
        "applicationEndDate",
        "eventStartDate",
        "eventEndDate",
        "price",
        "participantTarget",
        "participantAgeLimit",
        "participantCount",
        "participantCountLimit",
        "waitApplicationCount",
        "waitApplicationCountLimit",
        "applyYn",
        "operType",
        "operDt",
    }
)


class JeongseonMunicipalLibraryContractError(ValueError):
    pass


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


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


def _now(value: Optional[datetime | str]) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if value:
        return datetime.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)


def _normalized_owner_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme != "https"
        or parsed.hostname != JEONGSEON_MUNICIPAL_LIBRARY_HOST
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
    ):
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return f"https://{JEONGSEON_MUNICIPAL_LIBRARY_HOST}{parsed.path}" + (f"?{query}" if query else "")


def is_jeongseon_municipal_library_target(target: Any) -> bool:
    return _clean(_target_value(target, "provider")) == JEONGSEON_MUNICIPAL_LIBRARY_PROVIDER and _normalized_owner_url(
        _target_value(target, "url")
    ) == _normalized_owner_url(JEONGSEON_MUNICIPAL_LIBRARY_URL)


def municipal_library_api_url(page: int) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise JeongseonMunicipalLibraryContractError("invalid one-based page")
    query = [
        ("currentPageNo", page),
        ("pageSize", JEONGSEON_MUNICIPAL_LIBRARY_PAGE_SIZE),
        ("recordCountPerPage", JEONGSEON_MUNICIPAL_LIBRARY_PAGE_SIZE),
        ("searchType", ""),
        ("searchKeyword", ""),
        ("searchOrder", ""),
        ("kindOfEvent", "all"),
    ]
    return f"https://{JEONGSEON_MUNICIPAL_LIBRARY_HOST}{JEONGSEON_MUNICIPAL_LIBRARY_API_PATH}?{urlencode(query)}"


def municipal_library_detail_api_url(identity: Any) -> str:
    value = _clean(identity)
    if not _POSITIVE.fullmatch(value):
        raise JeongseonMunicipalLibraryContractError("invalid event identity")
    return f"https://{JEONGSEON_MUNICIPAL_LIBRARY_HOST}{JEONGSEON_MUNICIPAL_LIBRARY_API_PATH}/{value}"


def municipal_library_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _POSITIVE.fullmatch(value):
        raise JeongseonMunicipalLibraryContractError("invalid event identity")
    return (
        f"https://{JEONGSEON_MUNICIPAL_LIBRARY_HOST}{JEONGSEON_MUNICIPAL_LIBRARY_DETAIL_PATH}/{value}?menuIds=10%2C21"
    )


def _request_kind(url: Any) -> str:
    parsed = urlparse(_clean(url))
    if (
        parsed.scheme != "https"
        or parsed.hostname != JEONGSEON_MUNICIPAL_LIBRARY_HOST
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
    ):
        raise JeongseonMunicipalLibraryContractError("request left the county-library host")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if parsed.path == JEONGSEON_MUNICIPAL_LIBRARY_API_PATH:
        expected = {
            "currentPageNo",
            "pageSize",
            "recordCountPerPage",
            "searchType",
            "searchKeyword",
            "searchOrder",
            "kindOfEvent",
        }
        if (
            len(query) == 7
            and set(query) == expected
            and _POSITIVE.fullmatch(query["currentPageNo"])
            and query["pageSize"] == "10"
            and query["recordCountPerPage"] == "10"
            and query["searchType"] == query["searchKeyword"] == query["searchOrder"] == ""
            and query["kindOfEvent"] == "all"
        ):
            return "list"
    suffix = parsed.path.removeprefix(JEONGSEON_MUNICIPAL_LIBRARY_API_PATH + "/")
    if parsed.path.startswith(JEONGSEON_MUNICIPAL_LIBRARY_API_PATH + "/") and _POSITIVE.fullmatch(suffix) and not query:
        return "detail"
    raise JeongseonMunicipalLibraryContractError("only public culture list/detail API is allowlisted")


def _session() -> requests.Session:
    current = requests.Session()
    current.trust_env = False
    current.headers.update(
        {"User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0)", "Accept": "application/json"}
    )
    return current


def _fetch(session: Any, url: str, timeout: int) -> Any:
    _request_kind(url)
    return session.get(url, timeout=timeout, allow_redirects=False)


def _json(value: Any, requested_url: str) -> Mapping[str, Any]:
    status = int(getattr(value, "status_code", 200) or 0)
    if status != 200 or getattr(value, "history", None):
        raise JeongseonMunicipalLibraryContractError(f"HTTP/redirect failure ({status})")
    headers = getattr(value, "headers", {}) or {}
    if any(str(key).lower() == "location" and item for key, item in headers.items()):
        raise JeongseonMunicipalLibraryContractError("redirect location is forbidden")
    final_url = _clean(getattr(value, "url", ""))
    if final_url and final_url != requested_url:
        raise JeongseonMunicipalLibraryContractError("response URL changed")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if isinstance(content, str):
        content = content.encode("utf-8")
    body = bytes(content)
    if not body or len(body) > JEONGSEON_MUNICIPAL_LIBRARY_MAX_JSON_BYTES:
        raise JeongseonMunicipalLibraryContractError("empty or oversized JSON")
    try:
        parsed = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JeongseonMunicipalLibraryContractError("invalid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise JeongseonMunicipalLibraryContractError("JSON root is not an object")
    return parsed


def _dt(value: Any, field: str) -> datetime:
    match = _DATETIME.fullmatch(_clean(value))
    if not match:
        raise JeongseonMunicipalLibraryContractError(f"invalid {field}")
    parts = [int(item or 0) for item in match.groups()]
    return datetime(*parts[:5], parts[5])


def _nonnegative(value: Any, field: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise JeongseonMunicipalLibraryContractError(f"invalid {field}")
    return value


def _safe_item(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise JeongseonMunicipalLibraryContractError("event is not an object")
    missing = {
        "id",
        "libraryId",
        "programType",
        "title",
        "applicationStartDate",
        "applicationEndDate",
        "eventStartDate",
        "eventEndDate",
        "participantTarget",
        "participantCountLimit",
        "applyYn",
    } - set(raw)
    if missing or raw.get("libraryId") != 1 or raw.get("programType") not in {"LECTURE", "EVENT"}:
        raise JeongseonMunicipalLibraryContractError("event required fields/owner changed")
    identity = raw.get("id")
    if isinstance(identity, bool) or not isinstance(identity, int) or identity < 1:
        raise JeongseonMunicipalLibraryContractError("event identity changed")
    values = {key: raw.get(key) for key in _SAFE_SOURCE_KEYS}
    if raw.get("applyYn") == "N" and raw.get("applicationStartDate") is None and raw.get("applicationEndDate") is None:
        values["apply_start"] = None
        values["apply_end"] = None
    else:
        values["apply_start"] = _dt(raw["applicationStartDate"], "application start")
        values["apply_end"] = _dt(raw["applicationEndDate"], "application end")
    values["start"] = _dt(raw["eventStartDate"], "event start")
    values["end"] = _dt(raw["eventEndDate"], "event end")
    if (values["apply_start"] is not None and values["apply_start"] > values["apply_end"]) or values["start"] > values[
        "end"
    ]:
        raise JeongseonMunicipalLibraryContractError("reversed event period")
    if not _clean(raw.get("title")) or not _clean(raw.get("lectureLocation")):
        raise JeongseonMunicipalLibraryContractError("event title/venue is empty")
    if raw.get("applyYn") not in {"Y", "N"}:
        raise JeongseonMunicipalLibraryContractError("event apply flag changed")
    for field in ("participantCount", "participantCountLimit", "waitApplicationCount", "waitApplicationCountLimit"):
        values[field] = _nonnegative(raw.get(field), field)
    # The official ledger intentionally records approved over-capacity guests
    # for a few past events, so participantCount is not clamped to the public
    # quota.  Every capacity component must still be a bounded non-negative int.
    return values


def _page(document: Mapping[str, Any], requested_page: int) -> tuple[int, int, list[dict[str, Any]]]:
    pagination = document.get("pagination")
    items = document.get("items")
    if not isinstance(pagination, Mapping) or not isinstance(items, list):
        raise JeongseonMunicipalLibraryContractError("pagination/items shell changed")
    if (
        pagination.get("currentPageNo") != requested_page
        or pagination.get("recordCountPerPage") != 10
        or pagination.get("libraryId") != 1
    ):
        raise JeongseonMunicipalLibraryContractError("pagination owner/page changed")
    total = pagination.get("totalRecordCount")
    pages = pagination.get("totalPageCount")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or isinstance(pages, bool)
        or not isinstance(pages, int)
        or pages != math.ceil(total / 10)
    ):
        raise JeongseonMunicipalLibraryContractError("pagination totals changed")
    return total, pages, [_safe_item(item) for item in items]


def _signature(rows: list[Mapping[str, Any]]) -> list[tuple[Any, ...]]:
    return [(row["id"], row["title"], row["applicationStartDate"], row["eventEndDate"]) for row in rows]


def _row(item: Mapping[str, Any], current_time: datetime) -> dict[str, Any]:
    identity = str(item["id"])
    if item["applyYn"] != "Y":
        source_status, status, available = "신청없음", "CLOSED", False
    elif current_time < item["apply_start"]:
        source_status, status, available = "신청예정", "UPCOMING", False
    elif current_time <= item["apply_end"]:
        source_status, status, available = "접수중", "OPEN", True
    else:
        source_status, status, available = "신청마감", "CLOSED", False
    event_kind = _clean(item["programType"])
    domain = "교육·강좌" if event_kind == "LECTURE" else "체험·견학"
    service = "공공강좌" if event_kind == "LECTURE" else "체험"
    fee = _clean(item.get("price")) or "무료"
    detail_url = municipal_library_detail_url(identity)
    return {
        "provider": JEONGSEON_MUNICIPAL_LIBRARY_PROVIDER,
        "provider_course_id": f"jeongseon-library:{identity}",
        "title": _clean(item["title"]),
        "branch": JEONGSEON_MUNICIPAL_LIBRARY_BRANCH,
        "branch_code": "county-library",
        "municipality_code": JEONGSEON_MUNICIPAL_LIBRARY_CODE,
        "municipality_name": JEONGSEON_MUNICIPAL_LIBRARY_NAME,
        "venue_name": _clean(item["lectureLocation"]),
        "venue_address": JEONGSEON_MUNICIPAL_LIBRARY_ADDRESS,
        "category": _clean(item.get("subTitle")) or ("강좌" if event_kind == "LECTURE" else "문화행사"),
        "program_type": "도서관 강좌" if event_kind == "LECTURE" else "도서관 문화행사",
        "collection_category": "공공예약",
        "domain_category": domain,
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": service,
        "service_group_policy": "locked",
        "classification_locked": True,
        "raw_url": detail_url,
        "application_url": detail_url if available else "",
        "application_type": "ONLINE_RESERVATION",
        "source_status": source_status,
        "status": status,
        "reservation_available": available,
        "period": f"{item['start'].date().isoformat()} ~ {item['end'].date().isoformat()}",
        "start_date": item["start"].date().isoformat(),
        "end_date": item["end"].date().isoformat(),
        "apply_period": (
            f"{item['apply_start'].isoformat(timespec='minutes')} ~ {item['apply_end'].isoformat(timespec='minutes')}"
            if item["apply_start"] is not None
            else ""
        ),
        "apply_start_date": item["apply_start"].date().isoformat() if item["apply_start"] is not None else "",
        "apply_end_date": item["apply_end"].date().isoformat() if item["apply_end"] is not None else "",
        "schedule_raw": _clean(
            " ".join(filter(None, [str(item.get("operType") or ""), str(item.get("operDt") or "")]))
        ),
        "target": _clean(item["participantTarget"]),
        "fee": fee,
        "capacity_current": item["participantCount"],
        "capacity_total": item["participantCountLimit"],
        "capacity_wait_current": item["waitApplicationCount"],
        "capacity_wait_total": item["waitApplicationCountLimit"],
        "description": _clean(item["title"]),
        "raw_fields": {
            "source_identity": identity,
            "source_program_type": event_kind,
            "source_status": source_status,
            "source_apply_yn": item["applyYn"],
            "detail_verified": True,
        },
    }


def collect_jeongseon_municipal_library(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 30,
    detail_limit: int = 300,
    today: Optional[date | datetime | str] = None,
    now: Optional[datetime | str] = None,
    session_factory: SessionFactory = _session,
    fetcher: Fetcher = _fetch,
    dedupe_fn: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "canonical_url": JEONGSEON_MUNICIPAL_LIBRARY_URL,
        "municipality_coverage": [JEONGSEON_MUNICIPAL_LIBRARY_CODE],
        "discovery_audit": dict(JEONGSEON_MUNICIPAL_LIBRARY_DISCOVERY_AUDIT),
        "snapshot_complete": False,
        "returned_count": 0,
        "configured_collection_error": "",
    }
    if not is_jeongseon_municipal_library_target(target):
        meta["configured_collection_error"] = "target is not the registered Jeongseon municipal-library owner"
        return [], JEONGSEON_MUNICIPAL_LIBRARY_PARSER, meta
    if max_pages < 2 or detail_limit < 0 or timeout < 1:
        meta["configured_collection_error"] = "invalid bounded collection limits"
        return [], JEONGSEON_MUNICIPAL_LIBRARY_PARSER, meta
    session = session_factory()
    list_requests = detail_requests = 0
    try:

        def get(url: str) -> Mapping[str, Any]:
            nonlocal list_requests, detail_requests
            kind = _request_kind(url)
            if kind == "list":
                list_requests += 1
            else:
                detail_requests += 1
            return _json(fetcher(session, url, timeout), url)

        total, data_pages, first = _page(get(municipal_library_api_url(1)), 1)
        if total < 1 or not first or data_pages + 1 > max_pages:
            raise JeongseonMunicipalLibraryContractError("empty source or max_pages cap prevents complete audit")
        pages = [first]
        for page in range(2, data_pages + 1):
            page_total, page_count, rows = _page(get(municipal_library_api_url(page)), page)
            if (page_total, page_count) != (total, data_pages) or not rows:
                raise JeongseonMunicipalLibraryContractError("pagination drift before declared boundary")
            pages.append(rows)
        sentinel_total, sentinel_pages, sentinel = _page(get(municipal_library_api_url(data_pages + 1)), data_pages + 1)
        first2_total, first2_pages, first2 = _page(get(municipal_library_api_url(1)), 1)
        last2_total, last2_pages, last2 = _page(get(municipal_library_api_url(data_pages)), data_pages)
        if (
            (sentinel_total, sentinel_pages) != (total, data_pages)
            or sentinel
            or (first2_total, first2_pages) != (total, data_pages)
            or _signature(first2) != _signature(first)
            or (last2_total, last2_pages) != (total, data_pages)
            or _signature(last2) != _signature(pages[-1])
        ):
            raise JeongseonMunicipalLibraryContractError("first/last/sentinel stability changed")
        source = [item for values in pages for item in values]
        identities = [str(item["id"]) for item in source]
        if len(source) != total or len(identities) != len(set(identities)):
            raise JeongseonMunicipalLibraryContractError("source total/order/identity cardinality changed")
        cutoff = _today(today)
        current = [item for item in source if item["end"].date() >= cutoff]
        if len(current) > detail_limit:
            raise JeongseonMunicipalLibraryContractError(
                f"detail_limit cap allows {detail_limit} of {len(current)} current details"
            )
        verified: list[dict[str, Any]] = []
        for item in current:
            detail = _safe_item(get(municipal_library_detail_api_url(item["id"])))
            safe_compare = (
                "id",
                "libraryId",
                "programType",
                "title",
                "applicationStartDate",
                "applicationEndDate",
                "eventStartDate",
                "eventEndDate",
                "participantTarget",
                "participantCountLimit",
                "applyYn",
            )
            if any(detail[key] != item[key] for key in safe_compare):
                raise JeongseonMunicipalLibraryContractError(f"detail {item['id']}: list/detail mismatch")
            verified.append(detail)
        rows = [_row(item, _now(now)) for item in verified]
        if dedupe_fn is not None:
            deduped = list(dedupe_fn(rows))
            if len(deduped) != len(rows):
                raise JeongseonMunicipalLibraryContractError("dedupe changed official identity cardinality")
            rows = deduped
        identity_hash = hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest()
        branch_counts = {JEONGSEON_MUNICIPAL_LIBRARY_BRANCH: len(source)}
        meta.update(
            {
                "source_rows": len(source),
                "source_total": total,
                "current_source_count": len(current),
                "expired_source_count": len(source) - len(current),
                "returned_count": len(rows),
                "data_pages": data_pages,
                "page_counts": {i + 1: len(values) for i, values in enumerate(pages)},
                "empty_sentinel_page": data_pages + 1,
                "empty_sentinel_verified": True,
                "stability_rechecks": 2,
                "details_complete": len(verified) == len(current),
                "detail_verified": len(verified),
                "pagination_complete": True,
                "snapshot_complete": len(verified) == len(current),
                "full_snapshot_validated": len(verified) == len(current),
                "no_current_data": not rows,
                "no_current_reason": ("complete owner ledger has no current/future courses" if not rows else ""),
                "source_identity_sha256": identity_hash,
                "source_branch_counts": branch_counts,
                "list_requests": list_requests,
                "detail_requests": detail_requests,
                "application_endpoint_requests": 0,
                "pii_values_persisted": 0,
                "configured_collection_error": "",
            }
        )
        return rows, JEONGSEON_MUNICIPAL_LIBRARY_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "snapshot_complete": False,
                "returned_count": 0,
                "list_requests": list_requests,
                "detail_requests": detail_requests,
                "application_endpoint_requests": 0,
                "pii_values_persisted": 0,
                "configured_collection_error": _clean(exc),
            }
        )
        return [], JEONGSEON_MUNICIPAL_LIBRARY_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_jeongseon_municipal_library

__all__ = [name for name in globals() if name.startswith("JEONGSEON_MUNICIPAL_LIBRARY_")] + [
    "JeongseonMunicipalLibraryContractError",
    "collect",
    "collect_jeongseon_municipal_library",
    "is_jeongseon_municipal_library_target",
    "municipal_library_api_url",
    "municipal_library_detail_api_url",
    "municipal_library_detail_url",
]
