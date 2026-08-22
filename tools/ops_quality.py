from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
import http.client
import json
import math
import re
import threading
import time
from typing import Any, Iterable
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


QUALITY_CONTRACT_VERSION = "2.0"
QUALITY_RULE_VERSION = "2026-07-18.1"
SEOUL = ZoneInfo("Asia/Seoul")

# The Ops console must describe the scopes exposed by the running public API,
# not whichever (possibly newer) classifier happens to be in the local worktree.
# This contract identifies the backend source currently deployed on n100.
DEPLOYED_SCOPE_CONTRACT = "n100-2026-07-03"
DEPLOYED_COURSES_SHA256 = "2be38f9c44856c1ea436e21de07e73aa3df6fbf4c1bdb5837387f7dd8e9e0c6d"
PRODUCTION_COURSES_API_ORIGIN = "https://mooncen.kr"
PRODUCTION_COURSES_API_PATH = "/api/courses/"
PRODUCTION_COURSES_API_URL = f"{PRODUCTION_COURSES_API_ORIGIN}{PRODUCTION_COURSES_API_PATH}"
PRODUCTION_PROVIDERS_API_PATH = "/api/branches/providers"
PRODUCTION_PROVIDERS_API_URL = f"{PRODUCTION_COURSES_API_ORIGIN}{PRODUCTION_PROVIDERS_API_PATH}"
PRODUCTION_COURSES_API_SCOPES = ("experience", "education")
# Use the least mutable ordering exposed by the deployed public API.  The
# default ``latest`` order includes created/updated timestamps, so crawler
# writes can move rows between OFFSET pages while Ops is taking its snapshot.
# ``deadline`` orders by apply/end dates and finally the immutable course ID;
# the bounded reconciliation passes below still verify the complete ID union
# before publishing anything.
PRODUCTION_COURSES_API_SORT = "deadline"
PRODUCTION_COURSES_TIME_GROUPS = ("morning", "afternoon", "evening", "unknown")
PRODUCTION_COURSES_AGE_GROUPS = (
    "INFANT",
    "TODDLER",
    "CHILD",
    "TEEN",
    "ADULT",
    "SENIOR",
    "ALL",
    "UNKNOWN",
)
PRODUCTION_COURSES_PAGE_SIZE = 100
PRODUCTION_COURSES_MAX_PAGES = 50
PRODUCTION_COURSES_MAX_TOTAL_PER_SCOPE = 5_000
PRODUCTION_COURSES_MAX_PAGE_BYTES = 8 * 1024 * 1024
PRODUCTION_COURSES_REQUEST_TIMEOUT_SECONDS = 45
PRODUCTION_COURSES_REQUEST_ATTEMPTS = 2
PRODUCTION_COURSES_MAX_REQUESTS_PER_REFRESH = 100
# The public API orders by mutable fields, so OFFSET pages from one traversal
# can overlap.  Reconcile at most three complete traversals per incomplete
# scope, retaining rows by stable course ID across traversals.
PRODUCTION_COURSES_MAX_SCOPE_RECONCILIATION_PASSES = 4
PRODUCTION_COURSES_SCOPE_RECONCILIATION_WORKERS = 3
# Retained for compatibility with the allowlisted provider/partition helpers.
# The authoritative dataset path below intentionally does not use them.
PRODUCTION_COURSES_MAX_PROVIDER_RECONCILIATION_PASSES = 2
PRODUCTION_COURSES_PROVIDER_BATCH_WORKERS = 3
PRODUCTION_COURSES_CACHE_TTL_SECONDS = 900
PRODUCTION_COURSES_ERROR_BACKOFF_SECONDS = 120
_PRODUCTION_COURSES_CACHE_LOCK = threading.Lock()
_PRODUCTION_COURSES_CACHE_CONDITION = threading.Condition(_PRODUCTION_COURSES_CACHE_LOCK)
_PRODUCTION_COURSES_CACHE: dict[str, Any] = {
    "data": None,
    "expires_at": 0.0,
    "source_status": "cold",
    "last_error": "",
    "refreshing": False,
    "retry_after": 0.0,
    "last_stage": "",
    "last_request_count": 0,
}
_DEPLOYED_CULTURE_CENTER_PROVIDERS = frozenset(
    {
        "HOMEPLUS",
        "LOTTE",
        "EMART",
        "HYUNDAI_DEPT",
        "GALLERIA",
        "AK_PLAZA",
        "ELAND_RETAIL",
        "SHINSEGAE_ACADEMY",
        "LOTTE_MART",
    }
)
_DEPLOYED_EXPERIENCE_CATEGORY_NAMES = (
    "체험",
    "교육체험",
    "교육·체험",
    "체험·견학",
    "체험/견학",
    "체험행사",
    "견학/야외",
    "박물관",
    "과학관",
    "미술관",
    "박물관/과학관",
    "수목원/생태",
    "자연·생태",
    "예술/공연",
    "예술공연",
    "전시",
    "공연",
    "문화행사",
    "관람",
)
_DEPLOYED_EXPERIENCE_SOURCE_GROUPS = frozenset(
    {
        "museum_science",
        "science_museum",
        "museum",
        "arts_culture",
        "national_institution",
    }
)
_DEPLOYED_SERVICE_GROUP_EXPERIENCE = "체험"

COLLECTED_COURSE_STATES = frozenset({"active", "all", "inactive"})
PRODUCTION_COURSE_SCOPES = ("culture", "experience", "education", "unmanaged")
COLLECTED_COURSE_SCOPES = frozenset({"all", *PRODUCTION_COURSE_SCOPES})
COLLECTED_COURSE_VIEWS = frozenset(
    {"all", "incomplete", "invalid", "reception_ready", "reception_missing"}
)

CORE_FIELD_WEIGHTS: dict[str, int] = {
    "title": 20,
    "branch": 15,
    "source_url": 10,
    "status": 10,
    "period": 15,
    "schedule": 15,
    "target": 10,
    "description": 5,
}

DIMENSION_WEIGHTS: dict[str, int] = {
    "completeness": 30,
    "freshness": 25,
    "validity": 20,
    "actionability": 15,
    "registration": 10,
}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
PROVIDER_STATUS_ORDER = {"critical": 0, "warning": 1, "healthy": 2}
NOT_READY_TARGET_STATUSES = {
    "blocked",
    "candidate",
    "needs_discovery",
    "needs_parser",
    "partial",
}


class ProductionCourseSourceError(RuntimeError):
    """The fixed production course feed could not be validated completely."""

    def __init__(self, message: str, *, stage: str = "", request_count: int = 0):
        super().__init__(message)
        self.stage = stage
        self.request_count = int(request_count)


class _ProductionRequestBudget:
    def __init__(self, limit: int = PRODUCTION_COURSES_MAX_REQUESTS_PER_REFRESH):
        self.limit = int(limit)
        self.used = 0
        self._lock = threading.Lock()

    def consume(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                raise ProductionCourseSourceError("production course refresh exceeded its request budget")
            self.used += 1


def _production_stage_error(
    stage: str,
    budget: _ProductionRequestBudget,
    exc: Exception,
) -> ProductionCourseSourceError:
    message = str(exc) if isinstance(exc, ProductionCourseSourceError) else "production source stage failed"
    return ProductionCourseSourceError(
        message,
        stage=stage,
        request_count=budget.used,
    )


class _RejectProductionRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def _read_production_json_response(
    request: urllib.request.Request,
    *,
    budget: _ProductionRequestBudget | None,
    max_bytes: int,
    source_name: str,
) -> bytes:
    """Read one allowlisted response with one transport-only retry.

    Each network attempt consumes the shared refresh budget. HTTP, content,
    size, and later payload validation failures remain fail-closed and are
    never retried.
    """

    opener = urllib.request.build_opener(_RejectProductionRedirects())
    for attempt in range(PRODUCTION_COURSES_REQUEST_ATTEMPTS):
        if budget is not None:
            budget.consume()
        try:
            with opener.open(  # noqa: S310 - callers use fixed HTTPS allowlists.
                request,
                timeout=PRODUCTION_COURSES_REQUEST_TIMEOUT_SECONDS,
            ) as response:
                if int(getattr(response, "status", 0) or 0) != 200:
                    raise ProductionCourseSourceError(
                        f"{source_name} returned a non-200 response"
                    )
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if not content_type.startswith("application/json"):
                    raise ProductionCourseSourceError(f"{source_name} did not return JSON")
                raw = response.read(max_bytes + 1)
        except ProductionCourseSourceError:
            raise
        except urllib.error.HTTPError as exc:
            # HTTPError is also a URLError, but an HTTP response is not a
            # transient transport failure and must not be retried.
            raise ProductionCourseSourceError(
                f"{source_name} returned a non-200 response"
            ) from exc
        except (
            TimeoutError,
            OSError,
            urllib.error.URLError,
            http.client.HTTPException,
        ) as exc:
            if attempt + 1 < PRODUCTION_COURSES_REQUEST_ATTEMPTS:
                continue
            raise ProductionCourseSourceError(f"{source_name} request failed") from exc
        if not isinstance(raw, bytes):
            raise ProductionCourseSourceError(f"{source_name} returned an invalid response body")
        if len(raw) > max_bytes:
            raise ProductionCourseSourceError(f"{source_name} exceeded the size limit")
        return raw
    raise AssertionError("production request attempts exhausted without a result")


def _production_courses_page_url(
    scope: str,
    page: int,
    *,
    provider: str = "",
    include_inactive: bool = True,
    time_groups: str = "",
    age_groups: str = "",
) -> str:
    if scope not in PRODUCTION_COURSES_API_SCOPES:
        raise ValueError("production scope must be experience or education")
    if not 1 <= int(page) <= PRODUCTION_COURSES_MAX_PAGES:
        raise ValueError("production page is outside the bounded range")
    provider_values = list(
        dict.fromkeys(
            value.strip().upper()
            for value in str(provider or "").split(",")
            if value.strip()
        )
    )
    if len(provider_values) > 50 or any(
        not re.fullmatch(r"[A-Z0-9_.:-]{1,100}", value) for value in provider_values
    ):
        raise ValueError("invalid production provider")
    normalized_provider = ",".join(provider_values)

    time_group_values = list(
        dict.fromkeys(
            value.strip().lower()
            for value in str(time_groups or "").split(",")
            if value.strip()
        )
    )
    if any(value not in PRODUCTION_COURSES_TIME_GROUPS for value in time_group_values):
        raise ValueError("invalid production time groups")
    normalized_time_groups = ",".join(time_group_values)

    age_group_values = list(
        dict.fromkeys(
            value.strip().upper()
            for value in str(age_groups or "").split(",")
            if value.strip()
        )
    )
    if any(value not in PRODUCTION_COURSES_AGE_GROUPS for value in age_group_values):
        raise ValueError("invalid production age groups")
    normalized_age_groups = ",".join(age_group_values)

    query_values = {
        "scope": scope,
        "page": int(page),
        "size": PRODUCTION_COURSES_PAGE_SIZE,
        "include_inactive": "true" if include_inactive else "false",
        "sort": PRODUCTION_COURSES_API_SORT,
    }
    if normalized_provider:
        query_values["provider"] = normalized_provider
    if normalized_time_groups:
        query_values["time_groups"] = normalized_time_groups
    if normalized_age_groups:
        query_values["age_groups"] = normalized_age_groups
    query = urllib.parse.urlencode(query_values)
    url = urllib.parse.urlunsplit(
        ("https", "mooncen.kr", PRODUCTION_COURSES_API_PATH, query, "")
    )
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "mooncen.kr"
        or parsed.port not in (None, 443)
        or parsed.path != PRODUCTION_COURSES_API_PATH
    ):
        raise ProductionCourseSourceError("production course source URL is not allowlisted")
    return url


def _production_courses_api_page(
    scope: str,
    page: int,
    *,
    provider: str = "",
    include_inactive: bool = True,
    time_groups: str = "",
    age_groups: str = "",
    budget: _ProductionRequestBudget | None = None,
) -> dict[str, Any]:
    """Read one bounded page from the single fixed production HTTPS origin."""

    request = urllib.request.Request(
        _production_courses_page_url(
            scope,
            page,
            provider=provider,
            include_inactive=include_inactive,
            time_groups=time_groups,
            age_groups=age_groups,
        ),
        headers={
            "Accept": "application/json",
            "User-Agent": "mooncen-ops-console/production-scope-sync",
        },
        method="GET",
    )
    raw = _read_production_json_response(
        request,
        budget=budget,
        max_bytes=PRODUCTION_COURSES_MAX_PAGE_BYTES,
        source_name="production course source",
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionCourseSourceError("production course source returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ProductionCourseSourceError("production course source response must be an object")
    return payload


def _production_provider_catalog(
    budget: _ProductionRequestBudget | None = None,
) -> list[str]:
    """Fetch the fixed production provider superset used for exact batching."""

    request = urllib.request.Request(
        PRODUCTION_PROVIDERS_API_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "mooncen-ops-console/production-scope-sync",
        },
        method="GET",
    )
    raw = _read_production_json_response(
        request,
        budget=budget,
        max_bytes=1024 * 1024,
        source_name="production provider source",
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionCourseSourceError("production provider source returned invalid JSON") from exc
    if not isinstance(payload, list) or not 1 <= len(payload) <= 500:
        raise ProductionCourseSourceError("production provider source response is invalid")
    providers: list[str] = []
    for row in payload:
        provider = str((row or {}).get("provider") or "").strip().upper() if isinstance(row, dict) else ""
        if not re.fullmatch(r"[A-Z0-9_.:-]{1,100}", provider):
            raise ProductionCourseSourceError("production provider source contains an invalid provider")
        providers.append(provider)
    if len(set(providers)) != len(providers):
        raise ProductionCourseSourceError("production provider source contains duplicates")
    return sorted(providers)


def _strict_page_integer(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductionCourseSourceError(f"production course source {key} is invalid")
    return value


def _validate_production_courses_page(
    scope: str,
    requested_page: int,
    payload: dict[str, Any],
    *,
    expected_total: int | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    total = _strict_page_integer(payload, "total")
    page = _strict_page_integer(payload, "page")
    size = _strict_page_integer(payload, "size")
    items = payload.get("items")
    if not 0 <= total <= PRODUCTION_COURSES_MAX_TOTAL_PER_SCOPE:
        raise ProductionCourseSourceError("production course source total exceeded the safety limit")
    if page != requested_page or size != PRODUCTION_COURSES_PAGE_SIZE:
        raise ProductionCourseSourceError("production course source pagination metadata is inconsistent")
    if expected_total is not None and total != expected_total:
        raise ProductionCourseSourceError("production course source total changed during pagination")
    if not isinstance(items, list) or len(items) > PRODUCTION_COURSES_PAGE_SIZE:
        raise ProductionCourseSourceError("production course source items are invalid")
    pages = max(1, math.ceil(total / PRODUCTION_COURSES_PAGE_SIZE))
    expected_count = (
        PRODUCTION_COURSES_PAGE_SIZE
        if requested_page < pages
        else total - (pages - 1) * PRODUCTION_COURSES_PAGE_SIZE
    )
    if len(items) != expected_count:
        raise ProductionCourseSourceError("production course source page length is inconsistent")
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ProductionCourseSourceError("production course source item must be an object")
        course_id = item.get("id")
        provider = item.get("provider")
        if not isinstance(course_id, str) or not course_id.strip() or len(course_id) > 100:
            raise ProductionCourseSourceError("production course source item id is invalid")
        if not isinstance(provider, str) or not provider.strip() or len(provider) > 100:
            raise ProductionCourseSourceError("production course source provider is invalid")
        if not isinstance(item.get("is_active"), bool):
            raise ProductionCourseSourceError("production course source active flag is invalid")
        normalized.append(dict(item))
    return total, normalized


def _fetch_production_course_dataset_via_provider_partitions() -> dict[str, Any]:
    """Legacy provider-partition fetcher retained for helper compatibility.

    The production provider endpoint is a complete branches/courses union, so
    it supplies a stable provider superset without paging through a moving
    course ordering. Disjoint provider batches are split until they fit on one
    page. An individually large provider is partitioned by the API's mutually
    exclusive time and age buckets before any bounded filtered reconciliation.
    Final totals, IDs, and active-only totals must all agree before publication.
    """

    budget = _ProductionRequestBudget()
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            first_futures = {
                scope: executor.submit(_production_courses_api_page, scope, 1, budget=budget)
                for scope in PRODUCTION_COURSES_API_SCOPES
            }
            providers_future = executor.submit(_production_provider_catalog, budget)
            first_payloads = {scope: future.result() for scope, future in first_futures.items()}
            provider_catalog = providers_future.result()
    except Exception as exc:
        raise _production_stage_error("bootstrap", budget, exc) from exc

    totals: dict[str, int] = {}
    for scope in PRODUCTION_COURSES_API_SCOPES:
        total, _items = _validate_production_courses_page(scope, 1, first_payloads[scope])
        page_count = max(1, math.ceil(total / PRODUCTION_COURSES_PAGE_SIZE))
        if page_count > PRODUCTION_COURSES_MAX_PAGES:
            raise ProductionCourseSourceError("production course source exceeded the page limit")
        totals[scope] = total

    def provider_batches() -> list[list[str]]:
        return [provider_catalog[index : index + 20] for index in range(0, len(provider_catalog), 20)]

    def validate_provider_membership(rows: list[dict[str, Any]], providers: list[str]) -> None:
        allowed = set(providers)
        if any(str(row.get("provider") or "").strip().upper() not in allowed for row in rows):
            raise ProductionCourseSourceError("production course source provider filter leaked rows")

    def fetch_single_large_provider(
        scope: str,
        provider: str,
        expected_total: int,
        *,
        time_groups: str = "",
        age_groups: str = "",
    ) -> list[dict[str, Any]]:
        reconciled_by_id: dict[str, dict[str, Any]] = {}
        for _pass in range(PRODUCTION_COURSES_MAX_PROVIDER_RECONCILIATION_PASSES):
            first = _production_courses_api_page(
                scope,
                1,
                provider=provider,
                time_groups=time_groups,
                age_groups=age_groups,
                budget=budget,
            )
            total, first_rows = _validate_production_courses_page(
                scope,
                1,
                first,
                expected_total=expected_total,
            )
            page_count = max(1, math.ceil(total / PRODUCTION_COURSES_PAGE_SIZE))
            if page_count > PRODUCTION_COURSES_MAX_PAGES:
                raise ProductionCourseSourceError("production provider exceeded the page limit")
            pass_rows = list(first_rows)
            for page in range(2, page_count + 1):
                payload = _production_courses_api_page(
                    scope,
                    page,
                    provider=provider,
                    time_groups=time_groups,
                    age_groups=age_groups,
                    budget=budget,
                )
                _total, page_rows = _validate_production_courses_page(
                    scope,
                    page,
                    payload,
                    expected_total=expected_total,
                )
                pass_rows.extend(page_rows)
            validate_provider_membership(pass_rows, [provider])
            # OFFSET pages can overlap while production rows move. Refresh rows
            # seen again and retain previously seen IDs, but never guess which
            # row to discard if the stable advertised total is exceeded.
            reconciled_by_id.update({str(row["id"]): row for row in pass_rows})
            if len(reconciled_by_id) > expected_total:
                raise ProductionCourseSourceError(
                    f"production provider reconciliation exceeded its total for {provider}"
                )
            if len(reconciled_by_id) == expected_total:
                return list(reconciled_by_id.values())
        raise ProductionCourseSourceError(
            f"production provider reconciliation did not reach its total for {provider}"
        )

    def fetch_partitioned_provider(
        scope: str,
        provider: str,
        expected_total: int,
        *,
        partition: str,
        time_groups: str = "",
    ) -> list[dict[str, Any]]:
        if partition == "time":
            buckets = PRODUCTION_COURSES_TIME_GROUPS
        elif partition == "age":
            buckets = PRODUCTION_COURSES_AGE_GROUPS
        else:
            raise AssertionError("unknown production provider partition")

        probes: list[tuple[str, int, list[dict[str, Any]]]] = []
        for bucket in buckets:
            child_time_groups = bucket if partition == "time" else time_groups
            child_age_groups = bucket if partition == "age" else ""
            payload = _production_courses_api_page(
                scope,
                1,
                provider=provider,
                time_groups=child_time_groups,
                age_groups=child_age_groups,
                budget=budget,
            )
            child_total, child_first_rows = _validate_production_courses_page(
                scope,
                1,
                payload,
            )
            validate_provider_membership(child_first_rows, [provider])
            if len({str(row["id"]) for row in child_first_rows}) != len(child_first_rows):
                raise ProductionCourseSourceError(
                    f"production provider {partition} child returned duplicate ids for {provider}"
                )
            probes.append((bucket, child_total, child_first_rows))

        if sum(child_total for _bucket, child_total, _rows in probes) != expected_total:
            raise ProductionCourseSourceError(
                f"production provider {partition} child totals do not match parent total for {provider}"
            )

        combined: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for bucket, child_total, child_first_rows in probes:
            if child_total <= PRODUCTION_COURSES_PAGE_SIZE:
                child_rows = child_first_rows
            elif partition == "time":
                child_rows = fetch_partitioned_provider(
                    scope,
                    provider,
                    child_total,
                    partition="age",
                    time_groups=bucket,
                )
            else:
                child_rows = fetch_single_large_provider(
                    scope,
                    provider,
                    child_total,
                    time_groups=time_groups,
                    age_groups=bucket,
                )

            validate_provider_membership(child_rows, [provider])
            child_ids = {str(row["id"]) for row in child_rows}
            if len(child_rows) != child_total or len(child_ids) != child_total:
                raise ProductionCourseSourceError(
                    f"production provider {partition} child rows do not match its total for {provider}"
                )
            if seen_ids.intersection(child_ids):
                raise ProductionCourseSourceError(
                    f"production provider {partition} children returned overlapping ids for {provider}"
                )
            seen_ids.update(child_ids)
            combined.extend(child_rows)

        if len(combined) != expected_total or len(seen_ids) != expected_total:
            raise ProductionCourseSourceError(
                f"production provider {partition} partition is incomplete for {provider}"
            )
        return combined

    def fetch_provider_batch(scope: str, providers: list[str]) -> list[dict[str, Any]]:
        provider_filter = ",".join(providers)
        payload = _production_courses_api_page(
            scope,
            1,
            provider=provider_filter,
            budget=budget,
        )
        total, rows = _validate_production_courses_page(scope, 1, payload)
        validate_provider_membership(rows, providers)
        if total <= PRODUCTION_COURSES_PAGE_SIZE:
            if len({str(row["id"]) for row in rows}) != total:
                raise ProductionCourseSourceError("production provider batch returned duplicate ids")
            return rows
        if len(providers) > 1:
            midpoint = max(1, len(providers) // 2)
            return fetch_provider_batch(scope, providers[:midpoint]) + fetch_provider_batch(
                scope,
                providers[midpoint:],
            )
        return fetch_partitioned_provider(
            scope,
            providers[0],
            total,
            partition="time",
        )

    batch_jobs = [
        (scope, batch)
        for scope in PRODUCTION_COURSES_API_SCOPES
        for batch in provider_batches()
    ]

    def fetch_batch_job(job: tuple[str, list[str]]) -> tuple[str, list[dict[str, Any]]]:
        scope, batch = job
        return scope, fetch_provider_batch(scope, batch)

    exact_rows: dict[str, list[dict[str, Any]]] = {
        scope: [] for scope in PRODUCTION_COURSES_API_SCOPES
    }
    try:
        with ThreadPoolExecutor(
            max_workers=min(PRODUCTION_COURSES_PROVIDER_BATCH_WORKERS, len(batch_jobs))
        ) as executor:
            for scope, rows in executor.map(fetch_batch_job, batch_jobs):
                exact_rows[scope].extend(rows)
    except Exception as exc:
        raise _production_stage_error("provider_batches", budget, exc) from exc

    try:
        with ThreadPoolExecutor(max_workers=len(PRODUCTION_COURSES_API_SCOPES)) as executor:
            active_payloads = dict(
                zip(
                    PRODUCTION_COURSES_API_SCOPES,
                    executor.map(
                        lambda scope: _production_courses_api_page(
                            scope,
                            1,
                            include_inactive=False,
                            budget=budget,
                        ),
                        PRODUCTION_COURSES_API_SCOPES,
                    ),
                )
            )
    except Exception as exc:
        raise _production_stage_error("active_totals", budget, exc) from exc

    seen_ids: set[str] = set()
    scopes: dict[str, dict[str, Any]] = {}
    try:
        for scope in PRODUCTION_COURSES_API_SCOPES:
            rows = exact_rows[scope]
            if len(rows) != totals[scope]:
                raise ProductionCourseSourceError("production provider totals do not match the scope total")
            unique_ids = {str(row["id"]) for row in rows}
            if len(unique_ids) != len(rows) or seen_ids.intersection(unique_ids):
                raise ProductionCourseSourceError("production course source returned a duplicate course id")
            seen_ids.update(unique_ids)
            active_total, _active_page = _validate_production_courses_page(
                scope,
                1,
                active_payloads[scope],
            )
            active = sum(1 for row in rows if row["is_active"])
            if active != active_total:
                raise ProductionCourseSourceError("production active rows do not match the public scope total")
            scopes[scope] = {
                "scope": scope,
                "total": len(rows),
                "active": active,
                "inactive": len(rows) - active,
                "items": rows,
            }
    except Exception as exc:
        raise _production_stage_error("final_validation", budget, exc) from exc

    fetched_at = datetime.now(SEOUL).isoformat()
    return {
        "ok": True,
        "source": "production_public_api",
        "source_url": PRODUCTION_COURSES_API_URL,
        "source_status": "ok",
        "authoritative": True,
        "fetched_at": fetched_at,
        "request_count": budget.used,
        "scopes": scopes,
    }


def _fetch_production_course_dataset_uncached() -> dict[str, Any]:
    """Reconcile the two production scopes across bounded global passes.

    The deployed public API uses a moving OFFSET order.  One traversal can
    therefore repeat IDs across pages even though every page has valid
    metadata and length.  Page one bootstraps both scopes concurrently, the
    remainder of pass one completes each traversal, and only scopes whose ID
    union is still incomplete are traversed again.  A scope is published only
    when its stable-ID union exactly matches the advertised total within four
    passes and agrees with the separate active-only public total.

    Provider catalog/partition helpers remain available for compatibility but
    are intentionally not part of this authoritative path.
    """

    budget = _ProductionRequestBudget()

    def fetch_jobs(
        jobs: list[tuple[str, int]],
        *,
        stage: str,
        include_inactive: bool = True,
    ) -> list[tuple[str, int, dict[str, Any]]]:
        if not jobs:
            return []

        def fetch(job: tuple[str, int]) -> tuple[str, int, dict[str, Any]]:
            scope, page = job
            payload = _production_courses_api_page(
                scope,
                page,
                include_inactive=include_inactive,
                budget=budget,
            )
            return scope, page, payload

        try:
            with ThreadPoolExecutor(
                max_workers=min(
                    PRODUCTION_COURSES_SCOPE_RECONCILIATION_WORKERS,
                    len(jobs),
                )
            ) as executor:
                return list(executor.map(fetch, jobs))
        except Exception as exc:
            raise _production_stage_error(stage, budget, exc) from exc

    bootstrap_results = fetch_jobs(
        [(scope, 1) for scope in PRODUCTION_COURSES_API_SCOPES],
        stage="bootstrap",
    )

    totals: dict[str, int] = {}
    page_counts: dict[str, int] = {}
    rows_by_id: dict[str, dict[str, dict[str, Any]]] = {
        scope: {} for scope in PRODUCTION_COURSES_API_SCOPES
    }

    def merge_page(
        scope: str,
        page: int,
        payload: dict[str, Any],
        *,
        stage: str,
    ) -> None:
        try:
            total, rows = _validate_production_courses_page(
                scope,
                page,
                payload,
                expected_total=totals.get(scope),
            )
            if scope not in totals:
                page_count = max(1, math.ceil(total / PRODUCTION_COURSES_PAGE_SIZE))
                if page_count > PRODUCTION_COURSES_MAX_PAGES:
                    raise ProductionCourseSourceError(
                        "production course source exceeded the page limit"
                    )
                totals[scope] = total
                page_counts[scope] = page_count
            scope_rows = rows_by_id[scope]
            for row in rows:
                scope_rows[str(row["id"])] = row
            if len(scope_rows) > totals[scope]:
                raise ProductionCourseSourceError(
                    f"production scope reconciliation exceeded its total for {scope}"
                )
        except Exception as exc:
            raise _production_stage_error(stage, budget, exc) from exc

    for scope, page, payload in bootstrap_results:
        merge_page(scope, page, payload, stage="bootstrap")

    # Pass one reuses the two bootstrapped first pages and requests only their
    # remaining pages.  Later passes are complete traversals, but only for
    # scopes that have not yet reconciled to their stable advertised total.
    first_pass_jobs = [
        (scope, page)
        for scope in PRODUCTION_COURSES_API_SCOPES
        for page in range(2, page_counts[scope] + 1)
    ]
    for scope, page, payload in fetch_jobs(
        first_pass_jobs,
        stage="scope_reconciliation_pass_1",
    ):
        merge_page(
            scope,
            page,
            payload,
            stage="scope_reconciliation_pass_1",
        )

    for pass_number in range(2, PRODUCTION_COURSES_MAX_SCOPE_RECONCILIATION_PASSES + 1):
        incomplete_scopes = [
            scope
            for scope in PRODUCTION_COURSES_API_SCOPES
            if len(rows_by_id[scope]) < totals[scope]
        ]
        if not incomplete_scopes:
            break
        stage = f"scope_reconciliation_pass_{pass_number}"
        pass_jobs = [
            (scope, page)
            for scope in incomplete_scopes
            for page in range(1, page_counts[scope] + 1)
        ]
        for scope, page, payload in fetch_jobs(pass_jobs, stage=stage):
            merge_page(scope, page, payload, stage=stage)

    incomplete = [
        scope
        for scope in PRODUCTION_COURSES_API_SCOPES
        if len(rows_by_id[scope]) != totals[scope]
    ]
    if incomplete:
        detail = ", ".join(
            f"{scope} ({len(rows_by_id[scope])}/{totals[scope]})"
            for scope in incomplete
        )
        raise ProductionCourseSourceError(
            "production scope reconciliation did not reach its total after "
            f"{PRODUCTION_COURSES_MAX_SCOPE_RECONCILIATION_PASSES} passes: {detail}",
            stage="scope_reconciliation",
            request_count=budget.used,
        )

    active_results = fetch_jobs(
        [(scope, 1) for scope in PRODUCTION_COURSES_API_SCOPES],
        stage="active_totals",
        include_inactive=False,
    )
    active_payloads = {scope: payload for scope, _page, payload in active_results}

    seen_ids: set[str] = set()
    scopes: dict[str, dict[str, Any]] = {}
    try:
        for scope in PRODUCTION_COURSES_API_SCOPES:
            rows = list(rows_by_id[scope].values())
            if len(rows) != totals[scope]:
                raise ProductionCourseSourceError(
                    "production scope rows do not match the advertised total"
                )
            unique_ids = set(rows_by_id[scope])
            if seen_ids.intersection(unique_ids):
                raise ProductionCourseSourceError(
                    "production course source returned a cross-scope duplicate course id"
                )
            seen_ids.update(unique_ids)
            active_total, _active_page = _validate_production_courses_page(
                scope,
                1,
                active_payloads[scope],
            )
            active = sum(1 for row in rows if row["is_active"])
            if active != active_total:
                raise ProductionCourseSourceError(
                    "production active rows do not match the public scope total"
                )
            scopes[scope] = {
                "scope": scope,
                "total": len(rows),
                "active": active,
                "inactive": len(rows) - active,
                "items": rows,
            }
    except Exception as exc:
        raise _production_stage_error("final_validation", budget, exc) from exc

    fetched_at = datetime.now(SEOUL).isoformat()
    return {
        "ok": True,
        "source": "production_public_api",
        "source_url": PRODUCTION_COURSES_API_URL,
        "source_status": "ok",
        "authoritative": True,
        "fetched_at": fetched_at,
        "request_count": budget.used,
        "scopes": scopes,
    }


def invalidate_production_course_cache(*, clear_failure_backoff: bool = False) -> None:
    with _PRODUCTION_COURSES_CACHE_CONDITION:
        retry_after = float(_PRODUCTION_COURSES_CACHE.get("retry_after") or 0.0)
        preserve_failure = not clear_failure_backoff and retry_after > time.monotonic()
        _PRODUCTION_COURSES_CACHE.update(
            data=None,
            expires_at=0.0,
            source_status="error" if preserve_failure else "cold",
            last_error=(
                str(_PRODUCTION_COURSES_CACHE.get("last_error") or "") if preserve_failure else ""
            ),
            retry_after=retry_after if preserve_failure else 0.0,
            last_stage=(
                str(_PRODUCTION_COURSES_CACHE.get("last_stage") or "") if preserve_failure else ""
            ),
            last_request_count=(
                int(_PRODUCTION_COURSES_CACHE.get("last_request_count") or 0)
                if preserve_failure
                else 0
            ),
        )


def get_production_course_dataset(*, force: bool = False) -> dict[str, Any]:
    """Return the shared, fully validated experience/education production feed."""

    now = time.monotonic()
    with _PRODUCTION_COURSES_CACHE_CONDITION:
        cached = _PRODUCTION_COURSES_CACHE.get("data")
        if not force and isinstance(cached, dict) and now < float(
            _PRODUCTION_COURSES_CACHE.get("expires_at") or 0.0
        ):
            return cached
        if not force and now < float(_PRODUCTION_COURSES_CACHE.get("retry_after") or 0.0):
            raise ProductionCourseSourceError("production course source is in failure backoff")
        if _PRODUCTION_COURSES_CACHE.get("refreshing"):
            deadline = time.monotonic() + 185
            while _PRODUCTION_COURSES_CACHE.get("refreshing") and time.monotonic() < deadline:
                _PRODUCTION_COURSES_CACHE_CONDITION.wait(timeout=0.5)
            cached = _PRODUCTION_COURSES_CACHE.get("data")
            if isinstance(cached, dict) and time.monotonic() < float(
                _PRODUCTION_COURSES_CACHE.get("expires_at") or 0.0
            ):
                return cached
            raise ProductionCourseSourceError("production course source refresh did not complete")
        _PRODUCTION_COURSES_CACHE["refreshing"] = True
        _PRODUCTION_COURSES_CACHE["source_status"] = "refreshing"
    try:
        data = _fetch_production_course_dataset_uncached()
    except Exception as exc:
        with _PRODUCTION_COURSES_CACHE_CONDITION:
            _PRODUCTION_COURSES_CACHE.update(
                data=None,
                expires_at=0.0,
                source_status="error",
                last_error=type(exc).__name__,
                refreshing=False,
                retry_after=time.monotonic() + PRODUCTION_COURSES_ERROR_BACKOFF_SECONDS,
                last_stage=str(getattr(exc, "stage", "") or "unknown"),
                last_request_count=int(getattr(exc, "request_count", 0) or 0),
            )
            _PRODUCTION_COURSES_CACHE_CONDITION.notify_all()
        if isinstance(exc, ProductionCourseSourceError):
            raise
        raise ProductionCourseSourceError("production course source refresh failed") from exc
    with _PRODUCTION_COURSES_CACHE_CONDITION:
        _PRODUCTION_COURSES_CACHE.update(
            data=data,
            expires_at=time.monotonic() + PRODUCTION_COURSES_CACHE_TTL_SECONDS,
            source_status="ok",
            last_error="",
            refreshing=False,
            retry_after=0.0,
            last_stage="complete",
            last_request_count=int(data.get("request_count") or 0),
        )
        _PRODUCTION_COURSES_CACHE_CONDITION.notify_all()
        return data


def production_course_cache_status() -> dict[str, Any]:
    """Expose non-secret cache/source health without triggering a network call."""

    if not _PRODUCTION_COURSES_CACHE_LOCK.acquire(blocking=False):
        return {
            "source": "production_public_api",
            "source_url": PRODUCTION_COURSES_API_URL,
            "source_status": "refreshing",
            "authoritative": True,
            "fetched_at": None,
            "cache_fresh": False,
            "active_totals": {},
            "last_error": "",
            "retry_after_seconds": 0,
            "last_stage": "refreshing",
            "last_request_count": 0,
        }
    try:
        data = _PRODUCTION_COURSES_CACHE.get("data")
        scopes = data.get("scopes") if isinstance(data, dict) else {}
        return {
            "source": "production_public_api",
            "source_url": PRODUCTION_COURSES_API_URL,
            "source_status": str(_PRODUCTION_COURSES_CACHE.get("source_status") or "cold"),
            "authoritative": True,
            "fetched_at": data.get("fetched_at") if isinstance(data, dict) else None,
            "cache_fresh": bool(
                isinstance(data, dict)
                and time.monotonic() < float(_PRODUCTION_COURSES_CACHE.get("expires_at") or 0.0)
            ),
            "active_totals": {
                scope: int((scopes.get(scope) or {}).get("active") or 0)
                for scope in PRODUCTION_COURSES_API_SCOPES
            },
            "last_error": str(_PRODUCTION_COURSES_CACHE.get("last_error") or ""),
            "retry_after_seconds": max(
                0,
                int(float(_PRODUCTION_COURSES_CACHE.get("retry_after") or 0.0) - time.monotonic()),
            ),
            "last_stage": str(_PRODUCTION_COURSES_CACHE.get("last_stage") or ""),
            "last_request_count": int(_PRODUCTION_COURSES_CACHE.get("last_request_count") or 0),
        }
    finally:
        _PRODUCTION_COURSES_CACHE_LOCK.release()


def _normalize_production_scope(scope: str | None, *, allow_all: bool = True) -> str:
    normalized = str(scope or "all").strip().lower()
    allowed = COLLECTED_COURSE_SCOPES if allow_all else frozenset(PRODUCTION_COURSE_SCOPES)
    if normalized not in allowed:
        labels = ", ".join(sorted(allowed))
        raise ValueError(f"scope must be one of: {labels}")
    return normalized


@lru_cache(maxsize=None)
def production_scope_predicate_sql(scope: str) -> str:
    """Compile the deployed production API's scope predicate for alias ``c``."""

    normalized = _normalize_production_scope(scope)
    if normalized == "all":
        return "TRUE"

    from sqlalchemy import literal_column, select
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.orm import aliased

    from backend import models

    course_alias = aliased(models.Course, name="c")
    expression = _deployed_production_scope_filter(normalized, course_alias)
    statement = select(literal_column("1")).select_from(course_alias).where(expression)
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    marker = "\nWHERE "
    if marker not in compiled:
        raise RuntimeError("failed to compile the production scope predicate")
    return compiled.split(marker, 1)[1]


def _deployed_production_scope_filter(scope_key: str, course_model):
    """Mirror the scope partition in the backend currently running on n100.

    The deployed July 3 backend predates the local public-education and locked
    service-group guards.  Its three public scopes form a complete partition:
    culture, then non-culture experience, then every remaining row as
    education.  Keep this compatibility predicate separate from the local API
    implementation so an un-deployed backend edit cannot silently change Ops.
    """

    from sqlalchemy import and_, false, func, or_, select

    from backend import models

    def column_matches_value(column, value):
        return and_(column.isnot(None), column == value)

    def column_in_values(column, values):
        return and_(column.isnot(None), column.in_(tuple(values)))

    def culture_import_provider(column):
        return and_(
            column.isnot(None),
            func.upper(func.left(column, 8)) == "CULTURE_",
        )

    def branch_has(predicate):
        return (
            select(1)
            .select_from(models.Branch)
            .where(models.Branch.id == course_model.branch_id, predicate)
            .correlate(course_model)
            .exists()
        )

    culture_scope = column_in_values(
        course_model.provider,
        _DEPLOYED_CULTURE_CENTER_PROVIDERS,
    )
    experience_scope = or_(
        column_matches_value(course_model.provider, "CULTURE_FACILITY"),
        culture_import_provider(course_model.provider),
        column_matches_value(course_model.service_group, _DEPLOYED_SERVICE_GROUP_EXPERIENCE),
        column_in_values(course_model.collection_category, _DEPLOYED_EXPERIENCE_CATEGORY_NAMES),
        column_in_values(course_model.domain_category, _DEPLOYED_EXPERIENCE_CATEGORY_NAMES),
        column_in_values(course_model.ai_category, _DEPLOYED_EXPERIENCE_CATEGORY_NAMES),
        column_in_values(
            course_model.source_group,
            tuple(sorted(_DEPLOYED_EXPERIENCE_SOURCE_GROUPS)),
        ),
        branch_has(
            or_(
                column_matches_value(models.Branch.provider, "CULTURE_FACILITY"),
                culture_import_provider(models.Branch.provider),
                models.Branch.facility_source.isnot(None),
                column_matches_value(
                    models.Branch.facility_service_group,
                    _DEPLOYED_SERVICE_GROUP_EXPERIENCE,
                ),
                column_matches_value(
                    models.Branch.facility_collection_category,
                    _DEPLOYED_SERVICE_GROUP_EXPERIENCE,
                ),
                column_in_values(
                    models.Branch.facility_category,
                    _DEPLOYED_EXPERIENCE_CATEGORY_NAMES,
                ),
                column_in_values(
                    models.Branch.facility_type,
                    _DEPLOYED_EXPERIENCE_CATEGORY_NAMES,
                ),
            )
        ),
    )

    normalized_scope = str(scope_key or "").strip().lower()
    if normalized_scope in {"provider", "culture"}:
        return culture_scope
    if normalized_scope == "experience":
        return and_(~culture_scope, experience_scope)
    if normalized_scope == "education":
        return and_(~culture_scope, ~experience_scope)
    if normalized_scope == "unmanaged":
        # The deployed API has no fourth partition: education is the fallback.
        return false()
    raise ValueError("scope must be provider, culture, experience, education, or unmanaged")


def _render_provider_metrics_sql() -> str:
    replacements = {
        "__CULTURE_SCOPE__": production_scope_predicate_sql("culture"),
        "__EXPERIENCE_SCOPE__": production_scope_predicate_sql("experience"),
        "__EDUCATION_SCOPE__": production_scope_predicate_sql("education"),
    }
    sql = PROVIDER_METRICS_SQL
    for placeholder, predicate in replacements.items():
        sql = sql.replace(placeholder, predicate)
    return sql


def percent(part: int | float, total: int | float) -> float | None:
    denominator = float(total or 0)
    if denominator <= 0:
        return None
    return round(float(part or 0) / denominator * 100, 1)


def _score_label(score: float | None) -> str:
    if score is None:
        return "not_applicable"
    if score >= 90:
        return "healthy"
    if score >= 70:
        return "warning"
    return "critical"


def _weighted_score(values: dict[str, float | None], weights: dict[str, int]) -> float | None:
    applicable = [(key, value) for key, value in values.items() if value is not None and key in weights]
    weight_total = sum(weights[key] for key, _value in applicable)
    if weight_total <= 0:
        return None
    return round(sum(float(value) * weights[key] for key, value in applicable) / weight_total, 1)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _dimension(
    key: str,
    label: str,
    score: float | None,
    numerator: int,
    denominator: int,
    description: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "score": score,
        "status": _score_label(score),
        "numerator": numerator,
        "denominator": denominator,
        "description": description,
    }


def _issue(
    *,
    scope: str,
    provider: str,
    provider_label: str,
    code: str,
    severity: str,
    title: str,
    summary: str,
    action: str,
    affected: int = 0,
    total: int = 0,
    latest_seen: str | None = None,
    sample_supported: bool = False,
    sla_days: int | None = None,
) -> dict[str, Any]:
    issue_id = f"{scope}:{provider}:{code}"
    result = {
        "id": issue_id,
        "fingerprint": issue_id,
        "rule_key": code,
        "rule_version": QUALITY_RULE_VERSION,
        "status": "open",
        "scope": scope,
        "provider": provider,
        "provider_label": provider_label,
        "code": code,
        "severity": severity,
        "title": title,
        "summary": summary,
        "action": action,
        "affected": affected,
        "total": total,
        "rate": percent(affected, total),
        "latest_seen": latest_seen,
        "sample_supported": sample_supported,
    }
    if sla_days is not None:
        result["sla_days"] = int(sla_days)
    return result


def _infer_scope(metric: dict[str, Any], catalog: dict[str, Any]) -> tuple[str, list[str]]:
    production_scope = str(metric.get("production_scope") or "").strip().lower()
    if production_scope in PRODUCTION_COURSE_SCOPES:
        return production_scope, [production_scope]

    service_groups = [str(value or "").strip() for value in metric.get("service_groups") or []]
    # Legacy/non-DB callers do not carry production_scope. Keep their previous
    # fallback, but DB-backed metrics must always use the authoritative predicate.
    observed_scopes: set[str] = set()
    for service_group in service_groups:
        if "체험" in service_group:
            observed_scopes.add("experience")
        if any(token in service_group for token in ("교육", "공공강좌", "평생")):
            observed_scopes.add("education")
    if observed_scopes:
        scopes = [scope for scope in ("experience", "education") if scope in observed_scopes]
        return scopes[0], scopes

    scopes = sorted({str(item) for item in catalog.get("scopes") or [] if item})
    if scopes:
        primary = "culture" if "culture" in scopes else scopes[0]
        return primary, scopes

    service_group_text = " ".join(service_groups)
    # Culture is an explicit configured scope. A DB-only provider labelled
    # "문화센터" must not silently enter the retail culture-center denominator.
    if "문화센터" in service_group_text:
        return "unmanaged", ["unmanaged"]
    return "unmanaged", ["unmanaged"]


def summarize_provider(metric: dict[str, Any], catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = catalog or {}
    provider = str(metric.get("provider") or catalog.get("provider") or "").upper()
    label = str(catalog.get("label") or metric.get("label") or provider)
    urls = sorted({str(value).strip() for value in catalog.get("urls") or [] if str(value).strip()})
    catalog_url = str(catalog.get("url") or "").strip()
    if catalog_url and catalog_url not in urls:
        urls.insert(0, catalog_url)
    scope, scopes = _infer_scope(metric, catalog)
    target_statuses = sorted({str(value or "").strip().lower() for value in catalog.get("target_statuses") or [] if value})
    configured = bool(catalog.get("configured"))
    if not configured:
        lifecycle = "observed"
    elif "ready" in target_statuses:
        lifecycle = "production"
    elif "blocked" in target_statuses:
        lifecycle = "blocked"
    else:
        lifecycle = "onboarding"
    freshness_sla_days = 7 if scope == "culture" else 30
    active = _as_int(metric.get("active"))
    total = _as_int(metric.get("total"))

    field_counts = {
        "title": _as_int(metric.get("title_count")),
        "branch": _as_int(metric.get("branch_count")),
        "source_url": _as_int(metric.get("source_url_count")),
        "status": _as_int(metric.get("status_count")),
        "period": _as_int(metric.get("period_count")),
        "schedule": _as_int(metric.get("schedule_count")),
        "target": _as_int(metric.get("target_count")),
        "description": _as_int(metric.get("description_count")),
    }
    field_rates = {key: percent(value, active) for key, value in field_counts.items()}
    completeness = _weighted_score(field_rates, CORE_FIELD_WEIGHTS)

    seen_7d = _as_int(metric.get("seen_7d"))
    seen_sla = seen_7d if freshness_sla_days == 7 else _as_int(metric.get("seen_30d"))
    freshness = percent(seen_sla, active)
    invalid_any = _as_int(metric.get("invalid_any"))
    validity = percent(max(active - invalid_any, 0), active)
    action_path = _as_int(metric.get("action_path_count"))
    actionable_population = _as_int(metric.get("actionable_population"))
    actionability = percent(action_path, actionable_population)

    alert_population = _as_int(metric.get("alert_population"))
    alert_start_ready = _as_int(metric.get("alert_start_ready"))
    alert_candidate_population = _as_int(metric.get("alert_candidate_population"))
    alert_future_ready = _as_int(metric.get("alert_future_ready"))
    registration = percent(alert_future_ready, alert_candidate_population) if scope == "culture" else None
    dimension_values = {
        "completeness": completeness,
        "freshness": freshness,
        "validity": validity,
        "actionability": actionability,
        "registration": registration,
    }
    overall_score = _weighted_score(dimension_values, DIMENSION_WEIGHTS)
    latest_seen = _iso(metric.get("latest_seen"))
    issues: list[dict[str, Any]] = []

    if active <= 0:
        if lifecycle not in {"onboarding", "blocked"}:
            issues.append(
                _issue(
                    scope=scope,
                    provider=provider,
                    provider_label=label,
                    code="NO_ACTIVE_DATA",
                    severity="critical" if lifecycle == "production" else "warning",
                    title="활성 데이터 없음",
                    summary=f"전체 {total:,}건이 있지만 활성 강좌가 없습니다." if total else "수집된 강좌가 없습니다.",
                    action="대상 URL, 파서, 최신 크롤러 실행 결과를 확인하세요.",
                    affected=0,
                    total=0,
                    latest_seen=latest_seen,
                )
            )
    else:
        if seen_sla == 0:
            issues.append(
                _issue(
                    scope=scope,
                    provider=provider,
                    provider_label=label,
                    code="STALE_DATA",
                    severity="critical" if scope == "culture" and lifecycle == "production" else "warning",
                    title=f"최근 {freshness_sla_days}일 수집 데이터 없음",
                    summary=f"활성 {active:,}건이 모두 {freshness_sla_days}일 신선도 SLA를 벗어났습니다.",
                    action="크롤러 실행 여부와 last_seen_at 갱신 경로를 확인하세요.",
                    affected=active,
                    total=active,
                    latest_seen=latest_seen,
                    sample_supported=True,
                    sla_days=freshness_sla_days,
                )
            )
        elif freshness is not None and freshness < 80:
            stale = max(active - seen_sla, 0)
            issues.append(
                _issue(
                    scope=scope,
                    provider=provider,
                    provider_label=label,
                    code="STALE_DATA",
                    severity="warning",
                    title="신선도 SLA 미달",
                    summary=f"활성 데이터 중 {stale:,}건이 {freshness_sla_days}일 이상 확인되지 않았습니다.",
                    action="지점별 누락과 부분 수집 여부를 확인하세요.",
                    affected=stale,
                    total=active,
                    latest_seen=latest_seen,
                    sample_supported=True,
                    sla_days=freshness_sla_days,
                )
            )

        if completeness is not None and completeness < 85:
            missing_core = max(active - _as_int(metric.get("core_complete_count")), 0)
            issues.append(
                _issue(
                    scope=scope,
                    provider=provider,
                    provider_label=label,
                    code="CORE_COMPLETENESS",
                    severity="critical" if completeness < 55 and lifecycle == "production" else "warning",
                    title="핵심 필드 완전성 미달",
                    summary=f"가중 완전성 {completeness:.1f}점, 핵심 필드 전체 충족 {_as_int(metric.get('core_complete_count')):,}/{active:,}건입니다.",
                    action="낮은 필드를 확인해 파서와 정규화 저장 경로를 수정하세요.",
                    affected=missing_core,
                    total=active,
                    latest_seen=latest_seen,
                    sample_supported=True,
                )
            )

        if invalid_any > 0:
            invalid_rate = percent(invalid_any, active) or 0
            issues.append(
                _issue(
                    scope=scope,
                    provider=provider,
                    provider_label=label,
                    code="INVALID_DATA",
                    severity="critical" if invalid_rate >= 5 else "warning",
                    title="유효성 오류 발견",
                    summary=f"날짜 역전, 상태 불일치, 손상 텍스트 또는 잘못된 URL이 {invalid_any:,}건입니다.",
                    action="오류 유형별 샘플을 확인하고 원천 파서 또는 상태 갱신을 보정하세요.",
                    affected=invalid_any,
                    total=active,
                    latest_seen=latest_seen,
                    sample_supported=True,
                )
            )

        if actionability is not None and actionability < 80:
            missing_action = max(actionable_population - action_path, 0)
            issues.append(
                _issue(
                    scope=scope,
                    provider=provider,
                    provider_label=label,
                    code="ACTION_PATH_MISSING",
                    severity="critical" if scope == "culture" and actionability < 30 else "warning",
                    title="신청 경로 부족",
                    summary=f"신청 가능한 데이터 중 URL 또는 명시적 방식이 {action_path:,}/{actionable_population:,}건에만 있습니다.",
                    action="raw_url을 신청 URL로 간주하지 말고 실제 신청 경로 또는 오프라인 방식을 저장하세요.",
                    affected=missing_action,
                    total=actionable_population,
                    latest_seen=latest_seen,
                    sample_supported=True,
                )
            )

        if scope == "culture" and alert_population > 0 and alert_start_ready < alert_population:
            missing_registration = max(alert_population - alert_start_ready, 0)
            known_rate = percent(alert_start_ready, alert_population) or 0
            issues.append(
                _issue(
                    scope=scope,
                    provider=provider,
                    provider_label=label,
                    code="REGISTRATION_SCHEDULE_MISSING",
                    severity="critical" if known_rate < 50 else "warning",
                    title="접수 시작 알람 준비 미달",
                    summary=f"접수 알람 대상 중 시작일 보유가 {alert_start_ready:,}/{alert_population:,}건입니다.",
                    action="접수 시작 시각, 회원 유형, 채널과 출처 원문을 수집하세요.",
                    affected=missing_registration,
                    total=alert_population,
                    latest_seen=latest_seen,
                    sample_supported=True,
                )
            )

        if scope == "culture":
            apply_start_count = _as_int(metric.get("apply_start_count"))
            reception_rate = percent(apply_start_count, active) or 0
            if reception_rate < 80:
                missing_reception = max(active - apply_start_count, 0)
                issues.append(
                    _issue(
                        scope=scope,
                        provider=provider,
                        provider_label=label,
                        code="RECEPTION_DATA_MISSING",
                        severity="critical" if reception_rate < 50 else "warning",
                        title="접수 일정 수집률 미달",
                        summary=f"활성 강좌 중 접수 시작일 보유가 {apply_start_count:,}/{active:,}건입니다.",
                        action="강좌 또는 지점·학기 접수 원천을 수집하고 정규화·UPSERT 경로까지 연결하세요.",
                        affected=missing_reception,
                        total=active,
                        latest_seen=latest_seen,
                        sample_supported=True,
                    )
                )
            apply_raw_count = _as_int(metric.get("apply_raw_count"))
            if apply_raw_count < active:
                issues.append(
                    _issue(
                        scope=scope,
                        provider=provider,
                        provider_label=label,
                        code="RECEPTION_RAW_MISSING",
                        severity="warning",
                        title="접수 원문 보존 미달",
                        summary=f"회원 유형·채널·시각 검증용 접수 원문이 {apply_raw_count:,}/{active:,}건입니다.",
                        action="파싱 전 원문, 출처 URL, 관측시각을 함께 저장하세요.",
                        affected=max(active - apply_raw_count, 0),
                        total=active,
                        latest_seen=latest_seen,
                        sample_supported=True,
                    )
                )

    not_ready = [status for status in target_statuses if status in NOT_READY_TARGET_STATUSES]
    if not_ready:
        issues.append(
            _issue(
                scope=scope,
                provider=provider,
                provider_label=label,
                code="TARGET_NOT_READY",
                severity="critical" if "blocked" in not_ready else "warning",
                title="수집 대상 상태 확인 필요",
                summary=f"설정 상태: {', '.join(not_ready)}",
                action="대상 상태를 ready로 바꾸기 전에 실수집과 품질 게이트를 통과시키세요.",
                affected=len(not_ready),
                total=max(_as_int(catalog.get("target_count")), len(target_statuses)),
                latest_seen=latest_seen,
            )
        )

    declared_scopes = sorted({str(value) for value in catalog.get("declared_scopes") or [] if value})
    if len(declared_scopes) > 1:
        issues.append(
            _issue(
                scope=scope,
                provider=provider,
                provider_label=label,
                code="MULTI_SCOPE_PROVIDER",
                severity="warning",
                title="여러 Scope 데이터 귀속 불명확",
                summary=f"하나의 provider가 {', '.join(declared_scopes)} target을 함께 사용합니다.",
                action="target별 provider identity 또는 course source identity를 분리하세요.",
                affected=active,
                total=active,
                latest_seen=latest_seen,
            )
        )

    latest_run = catalog.get("latest_run") or {}
    run_status = str(latest_run.get("status") or "").lower()
    if run_status in {"failed", "stopped"}:
        issues.append(
            _issue(
                scope=scope,
                provider=provider,
                provider_label=label,
                code="CRAWLER_FAILED",
                severity="critical",
                title="최근 크롤러 실행 실패",
                summary=str(latest_run.get("error_message") or run_status)[:240],
                action="실패 로그를 확인하고 재실행 전 파서·접속 조건을 검증하세요.",
                latest_seen=latest_seen,
            )
        )

    target_count = _as_int(catalog.get("target_count"))
    target_run_count = _as_int(catalog.get("target_run_count"))
    if lifecycle == "production" and target_count > 0 and target_run_count < target_count:
        issues.append(
            _issue(
                scope=scope,
                provider=provider,
                provider_label=label,
                code="RUN_TELEMETRY_MISSING",
                severity="warning",
                title="크롤러 실행 이력 연결 누락",
                summary=f"설정 target 중 실행 이력이 연결된 대상은 {target_run_count:,}/{target_count:,}개입니다.",
                action="runner가 설정의 canonical target_key를 crawler_run_log에 기록하도록 맞추세요.",
                affected=max(target_count - target_run_count, 0),
                total=target_count,
                latest_seen=latest_seen,
            )
        )

    if not configured and total > 0:
        issues.append(
            _issue(
                scope=scope,
                provider=provider,
                provider_label=label,
                code="UNMANAGED_PROVIDER",
                severity="warning",
                title="등록되지 않은 DB Provider",
                summary="DB 데이터는 있지만 현재 수집 대상 레지스트리에 없습니다.",
                action="provider를 target 레지스트리에 연결하거나 폐기 데이터로 분류하세요.",
                affected=total,
                total=total,
                latest_seen=latest_seen,
            )
        )

    issue_severities = {issue["severity"] for issue in issues}
    status = "critical" if "critical" in issue_severities else "warning" if "warning" in issue_severities else "healthy"
    dimensions = {
        "completeness": _dimension(
            "completeness",
            "핵심 완전성",
            completeness,
            _as_int(metric.get("core_complete_count")),
            active,
            "제목·지점·출처·상태·기간·일정·대상·설명의 가중 채움률",
        ),
        "freshness": _dimension(
            "freshness", f"{freshness_sla_days}일 신선도", freshness, seen_sla, active, f"last_seen_at이 {freshness_sla_days}일 이내인 활성 강좌 비율"
        ),
        "validity": _dimension(
            "validity", "유효성", validity, max(active - invalid_any, 0), active, "날짜·상태·URL·식별자·문자열 오류가 없는 비율"
        ),
        "actionability": _dimension(
            "actionability", "신청 가능성", actionability, action_path, actionable_population, "정보 전용·차단 대상을 제외하고 실제 신청 URL 또는 신청 방식을 가진 비율"
        ),
        "registration": _dimension(
            "registration",
            "접수 알람 준비",
            registration,
            alert_future_ready,
            alert_candidate_population,
            "접수일이 없거나 미래인 대상 중 실제 미래 apply_start를 가진 비율(문화센터 전용)",
        ),
    }
    return {
        "provider": provider,
        "label": label,
        "url": catalog_url or (urls[0] if urls else ""),
        "urls": urls,
        "scope": scope,
        "scopes": scopes,
        "status": status,
        "overall_score": overall_score,
        "total": total,
        "active": active,
        "branches": _as_int(metric.get("branches")),
        "configured": configured,
        "target_count": _as_int(catalog.get("target_count")),
        "target_run_count": target_run_count,
        "target_statuses": target_statuses,
        "lifecycle": lifecycle,
        "freshness_sla_days": freshness_sla_days,
        "latest_seen": latest_seen,
        "seen_24h": _as_int(metric.get("seen_24h")),
        "seen_7d": seen_7d,
        "seen_30d": _as_int(metric.get("seen_30d")),
        "seen_sla": seen_sla,
        "core_complete_count": _as_int(metric.get("core_complete_count")),
        "invalid_count": invalid_any,
        "action_path_count": action_path,
        "actionable_population": actionable_population,
        "application_url_count": _as_int(metric.get("application_url_count")),
        "alert_population": alert_population,
        "alert_start_ready": alert_start_ready,
        "alert_both_ready": _as_int(metric.get("alert_both_ready")),
        "alert_candidate_population": alert_candidate_population,
        "alert_future_ready": alert_future_ready,
        "alert_future_both_ready": _as_int(metric.get("alert_future_both_ready")),
        "apply_start_count": _as_int(metric.get("apply_start_count")),
        "apply_end_count": _as_int(metric.get("apply_end_count")),
        "apply_both_count": _as_int(metric.get("apply_both_count")),
        "apply_raw_count": _as_int(metric.get("apply_raw_count")),
        "field_counts": field_counts,
        "field_rates": field_rates,
        "dimensions": dimensions,
        "issues": issues,
        "latest_run": latest_run,
        "invalid_breakdown": {
            key: _as_int(metric.get(key))
            for key in (
                "invalid_period",
                "invalid_apply_period",
                "apply_after_class_start",
                "status_past_apply_end",
                "invalid_source_url",
                "invalid_application_url",
                "corrupt_title",
                "missing_identity",
            )
        },
    }


def _aggregate_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    keys = {
        "total",
        "active",
        "branches",
        "seen_24h",
        "seen_7d",
        "seen_30d",
        "core_complete_count",
        "invalid_any",
        "action_path_count",
        "actionable_population",
        "application_url_count",
        "alert_population",
        "alert_start_ready",
        "alert_both_ready",
        "alert_candidate_population",
        "alert_future_ready",
        "alert_future_both_ready",
        "apply_start_count",
        "apply_end_count",
        "apply_both_count",
        "apply_raw_count",
        "title_count",
        "branch_count",
        "source_url_count",
        "status_count",
        "period_count",
        "schedule_count",
        "target_count",
        "description_count",
    }
    result = {key: 0 for key in keys}
    for row in rows:
        for key in keys:
            result[key] += _as_int(row.get(key))
    return result


def _scope_summary(
    scope: str,
    providers: list[dict[str, Any]],
    metric_rows: list[dict[str, Any]],
    *,
    registration_metric_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    provider_names = {row["provider"] for row in providers}
    aggregate = _aggregate_metrics(row for row in metric_rows if str(row.get("provider") or "").upper() in provider_names)
    active = aggregate["active"]
    fields = {
        "title": percent(aggregate["title_count"], active),
        "branch": percent(aggregate["branch_count"], active),
        "source_url": percent(aggregate["source_url_count"], active),
        "status": percent(aggregate["status_count"], active),
        "period": percent(aggregate["period_count"], active),
        "schedule": percent(aggregate["schedule_count"], active),
        "target": percent(aggregate["target_count"], active),
        "description": percent(aggregate["description_count"], active),
    }
    completeness = _weighted_score(fields, CORE_FIELD_WEIGHTS)
    freshness_numerator = sum(_as_int((row.get("dimensions") or {}).get("freshness", {}).get("numerator")) for row in providers)
    freshness_denominator = sum(_as_int((row.get("dimensions") or {}).get("freshness", {}).get("denominator")) for row in providers)
    freshness = percent(freshness_numerator, freshness_denominator)
    validity = percent(max(active - aggregate["invalid_any"], 0), active)
    actionability = percent(aggregate["action_path_count"], aggregate["actionable_population"])
    registration_provider_names = {
        row["provider"] for row in providers if "culture" in (row.get("scopes") or [])
    }
    registration_rows = metric_rows if registration_metric_rows is None else registration_metric_rows
    registration_aggregate = _aggregate_metrics(
        row for row in registration_rows if str(row.get("provider") or "").upper() in registration_provider_names
    )
    registration = (
        percent(registration_aggregate["alert_future_ready"], registration_aggregate["alert_candidate_population"])
        if scope in {"all", "culture"}
        else None
    )
    dimension_values = {
        "completeness": completeness,
        "freshness": freshness,
        "validity": validity,
        "actionability": actionability,
        "registration": registration,
    }
    configured = [row for row in providers if row.get("configured")]
    configured_with_data = [row for row in configured if _as_int(row.get("active")) > 0]
    status_counts = {key: sum(1 for row in providers if row.get("status") == key) for key in PROVIDER_STATUS_ORDER}
    dimensions = {
        "completeness": _dimension("completeness", "핵심 완전성", completeness, aggregate["core_complete_count"], active, "활성 강좌의 핵심 필드 품질"),
        "freshness": _dimension("freshness", "SLA 신선도", freshness, freshness_numerator, freshness_denominator, "문화센터 7일, 그 외 운영 Provider 30일 이내 확인된 활성 강좌"),
        "validity": _dimension("validity", "유효성", validity, max(active - aggregate["invalid_any"], 0), active, "구조·상태 오류가 없는 활성 강좌"),
        "actionability": _dimension("actionability", "신청 가능성", actionability, aggregate["action_path_count"], aggregate["actionable_population"], "정보 전용·차단 대상을 제외하고 신청 경로가 명시된 활성 강좌"),
        "registration": _dimension("registration", "접수 알람 준비", registration, registration_aggregate["alert_future_ready"], registration_aggregate["alert_candidate_population"], "현재부터 예약 가능한 미래 접수 시작 이벤트"),
    }
    overall_score = _weighted_score(dimension_values, DIMENSION_WEIGHTS)
    return {
        "scope": scope,
        "status": "critical" if status_counts["critical"] else "warning" if status_counts["warning"] else "healthy",
        "overall_score": overall_score,
        "provider_count": len(providers),
        "configured_provider_count": len(configured),
        "configured_with_active_data": len(configured_with_data),
        "provider_coverage": percent(len(configured_with_data), len(configured)),
        "status_counts": status_counts,
        "total_courses": aggregate["total"],
        "active_courses": active,
        "stale_7d": max(active - aggregate["seen_7d"], 0),
        "stale_sla": max(freshness_denominator - freshness_numerator, 0),
        "invalid_courses": aggregate["invalid_any"],
        "action_path_missing": max(aggregate["actionable_population"] - aggregate["action_path_count"], 0),
        "alert_population": registration_aggregate["alert_population"],
        "alert_start_ready": registration_aggregate["alert_start_ready"],
        "alert_candidate_population": registration_aggregate["alert_candidate_population"],
        "alert_future_ready": registration_aggregate["alert_future_ready"],
        "dimensions": dimensions,
        "field_rates": fields,
    }


def build_snapshot(
    catalog_rows: Iterable[dict[str, Any]],
    metric_rows: Iterable[dict[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    catalogs = {str(row.get("provider") or "").upper(): dict(row) for row in catalog_rows if row.get("provider")}
    raw_metric_rows = [dict(row) for row in metric_rows if row.get("provider")]
    has_partitioned_metrics = any(
        str(row.get("production_scope") or "").strip().lower() == "all"
        for row in raw_metric_rows
    )

    if has_partitioned_metrics:
        aggregate_metrics = {
            str(row.get("provider") or "").upper(): row
            for row in raw_metric_rows
            if str(row.get("production_scope") or "").strip().lower() == "all"
        }
        scoped_metrics: dict[str, dict[str, dict[str, Any]]] = {
            scope: {
                str(row.get("provider") or "").upper(): row
                for row in raw_metric_rows
                if str(row.get("production_scope") or "").strip().lower() == scope
                and int(row.get("active") or 0) > 0
            }
            for scope in PRODUCTION_COURSE_SCOPES
        }
        observed_providers = {
            provider
            for metrics_by_provider in scoped_metrics.values()
            for provider in metrics_by_provider
        }
        provider_names = sorted(set(catalogs) | set(aggregate_metrics) | observed_providers)
        providers = [
            summarize_provider(
                aggregate_metrics.get(provider, {"provider": provider}),
                catalogs.get(provider),
            )
            for provider in provider_names
        ]

        scope_provider_rows: dict[str, list[dict[str, Any]]] = {}
        for scope in PRODUCTION_COURSE_SCOPES:
            scope_names = set(scoped_metrics[scope])
            for provider, catalog in catalogs.items():
                configured_scopes = {
                    str(value or "").strip().lower()
                    for value in catalog.get("scopes") or []
                }
                if provider not in observed_providers and scope in configured_scopes:
                    scope_names.add(provider)
            scope_provider_rows[scope] = [
                summarize_provider(
                    scoped_metrics[scope].get(
                        provider,
                        {"provider": provider, "production_scope": scope},
                    ),
                    catalogs.get(provider),
                )
                for provider in sorted(scope_names)
            ]

        rows_by_provider: dict[str, list[dict[str, Any]]] = {}
        for rows in scope_provider_rows.values():
            for row in rows:
                rows_by_provider.setdefault(str(row.get("provider") or ""), []).append(row)
        status_rank = {"critical": 0, "warning": 1, "healthy": 2}
        for provider in providers:
            partition_rows = rows_by_provider.get(str(provider.get("provider") or ""), [])
            if not partition_rows:
                continue
            exact_scopes = [
                scope
                for scope in PRODUCTION_COURSE_SCOPES
                if any(row.get("scope") == scope for row in partition_rows)
            ]
            provider["scope"] = exact_scopes[0] if exact_scopes else "unmanaged"
            provider["scopes"] = exact_scopes or ["unmanaged"]
            partition_issues = {
                str(issue.get("id") or ""): issue
                for row in partition_rows
                for issue in row.get("issues") or []
                if issue.get("id")
            }
            provider["issues"] = list(partition_issues.values())
            provider["status"] = min(
                (str(row.get("status") or "healthy") for row in partition_rows),
                key=lambda value: status_rank.get(value, 9),
                default=str(provider.get("status") or "healthy"),
            )
    else:
        aggregate_metrics = {
            str(row.get("provider") or "").upper(): row for row in raw_metric_rows
        }
        provider_names = sorted(set(catalogs) | set(aggregate_metrics))
        providers = [
            summarize_provider(
                aggregate_metrics.get(provider, {"provider": provider}),
                catalogs.get(provider),
            )
            for provider in provider_names
        ]
        scope_provider_rows = {
            scope: [row for row in providers if scope in (row.get("scopes") or [])]
            for scope in PRODUCTION_COURSE_SCOPES
        }

    providers.sort(
        key=lambda row: (
            PROVIDER_STATUS_ORDER.get(str(row.get("status")), 9),
            float(row.get("overall_score") if row.get("overall_score") is not None else -1),
            -_as_int(row.get("active")),
            str(row.get("provider")),
        )
    )
    issue_rows = (
        [row for rows in scope_provider_rows.values() for row in rows]
        if has_partitioned_metrics
        else providers
    )
    issues_by_id = {
        str(issue.get("id") or ""): issue
        for provider in issue_rows
        for issue in provider.get("issues") or []
        if issue.get("id")
    }
    issues = list(issues_by_id.values())
    issues.sort(
        key=lambda row: (
            SEVERITY_ORDER.get(str(row.get("severity")), 9),
            -_as_int(row.get("affected")),
            str(row.get("provider")),
            str(row.get("code")),
        )
    )
    aggregate_metric_rows = list(aggregate_metrics.values())
    scopes: dict[str, Any] = {
        "all": _scope_summary(
            "all",
            providers,
            aggregate_metric_rows,
            registration_metric_rows=(
                list(scoped_metrics["culture"].values())
                if has_partitioned_metrics
                else None
            ),
        )
    }
    for scope in PRODUCTION_COURSE_SCOPES:
        scoped = scope_provider_rows[scope]
        scope_metric_rows = (
            list(scoped_metrics[scope].values())
            if has_partitioned_metrics
            else aggregate_metric_rows
        )
        scopes[scope] = _scope_summary(scope, scoped, scope_metric_rows)

    generated = generated_at or datetime.now(SEOUL)
    source_dates = [str(row.get("latest_seen") or "") for row in providers if row.get("latest_seen")]
    public_providers = []
    for provider in providers:
        item = dict(provider)
        item["issue_ids"] = [str(issue.get("id") or "") for issue in item.pop("issues", [])]
        public_providers.append(item)
    public_scope_providers: dict[str, list[dict[str, Any]]] = {}
    for scope, rows in scope_provider_rows.items():
        public_scope_providers[scope] = []
        for row in rows:
            item = dict(row)
            item["issue_ids"] = [str(issue.get("id") or "") for issue in item.pop("issues", [])]
            public_scope_providers[scope].append(item)
    return {
        "ok": True,
        "contract_version": QUALITY_CONTRACT_VERSION,
        "rule_version": QUALITY_RULE_VERSION,
        "evaluation_run_id": f"quality-{generated.strftime('%Y%m%dT%H%M%S%z')}",
        "generated_at": generated.isoformat(),
        "source_data_at": max(source_dates) if source_dates else None,
        "partial_errors": [],
        "sla": {"fresh_within_days": 7, "warning_score": 90, "critical_score": 70},
        "summary": scopes["all"],
        "scopes": scopes,
        "providers": public_providers,
        "scope_providers": public_scope_providers,
        "issues": issues,
        "definitions": [
            {"key": "population", "label": "평가 모집단", "description": "필드·신선도·유효성은 is_active=true 강좌만 평가합니다. 과거 비활성 데이터는 점수에 섞지 않습니다."},
            {"key": "completeness", "label": "핵심 완전성", "description": "제목 20, 지점 15, 출처 10, 상태 10, 기간 15, 일정 15, 대상 10, 설명 5의 가중 채움률입니다."},
            {"key": "freshness", "label": "7일 신선도", "description": "활성 강좌 중 last_seen_at이 최근 7일 이내인 비율입니다."},
            {"key": "validity", "label": "유효성", "description": "날짜 역전, 접수일-수업일 모순, 상태-마감 불일치, 잘못된 URL·식별자, 손상 제목이 없는 비율입니다."},
            {"key": "actionability", "label": "신청 가능성", "description": "명시적 application_url 또는 방문·전화 등 확인 가능한 오프라인 신청 원문을 보유한 비율입니다. application_type이나 raw_url만으로는 신청 경로로 인정하지 않습니다."},
            {"key": "registration", "label": "접수 알람 준비", "description": "향후·예정 문화센터 강좌 중 apply_start가 있는 비율입니다. 대상이 0건이면 0점이 아니라 N/A입니다."},
            {"key": "status", "label": "Provider 상태", "description": "치명 이슈가 하나라도 있으면 critical, 경고 이슈가 있으면 warning입니다. 평균 점수가 오류를 가리지 않습니다."},
        ],
    }


PROVIDER_METRICS_SQL = r"""
WITH scoped_courses AS (
    SELECT
        c.*,
        CASE
            WHEN (__CULTURE_SCOPE__) THEN 'culture'
            WHEN (__EXPERIENCE_SCOPE__) THEN 'experience'
            WHEN (__EDUCATION_SCOPE__) THEN 'education'
            ELSE 'unmanaged'
        END AS production_scope
    FROM courses c
)
SELECT
    c.provider,
    CASE
        WHEN GROUPING(c.production_scope) = 1 THEN 'all'
        ELSE c.production_scope
    END AS production_scope,
    ARRAY_REMOVE(ARRAY_AGG(DISTINCT NULLIF(c.service_group, '')), NULL) AS service_groups,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE) AS active,
    COUNT(DISTINCT c.branch_id) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE) AS branches,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND NULLIF(btrim(c.title), '') IS NOT NULL) AS title_count,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND c.branch_id IS NOT NULL) AS branch_count,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND NULLIF(btrim(c.raw_url), '') IS NOT NULL) AS source_url_count,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND NULLIF(btrim(c.status), '') IS NOT NULL) AS status_count,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND c.start_date IS NOT NULL AND c.end_date IS NOT NULL) AS period_count,
    COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND (
            NULLIF(btrim(c.schedule_raw), '') IS NOT NULL
            OR (c.schedule_dates IS NOT NULL AND jsonb_typeof(c.schedule_dates) = 'array' AND jsonb_array_length(c.schedule_dates) > 0)
          )
    ) AS schedule_count,
    COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND (
            NULLIF(btrim(c.target), '') IS NOT NULL
            OR c.target_age_group IS NOT NULL
            OR c.target_min_age IS NOT NULL
            OR c.target_max_age IS NOT NULL
          )
    ) AS target_count,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND NULLIF(btrim(c.description), '') IS NOT NULL) AS description_count,
    COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND NULLIF(btrim(c.title), '') IS NOT NULL
          AND c.branch_id IS NOT NULL
          AND NULLIF(btrim(c.raw_url), '') IS NOT NULL
          AND NULLIF(btrim(c.status), '') IS NOT NULL
          AND c.start_date IS NOT NULL
          AND c.end_date IS NOT NULL
          AND (
            NULLIF(btrim(c.schedule_raw), '') IS NOT NULL
            OR (c.schedule_dates IS NOT NULL AND jsonb_typeof(c.schedule_dates) = 'array' AND jsonb_array_length(c.schedule_dates) > 0)
          )
          AND (
            NULLIF(btrim(c.target), '') IS NOT NULL
            OR c.target_age_group IS NOT NULL
            OR c.target_min_age IS NOT NULL
            OR c.target_max_age IS NOT NULL
          )
          AND NULLIF(btrim(c.description), '') IS NOT NULL
    ) AS core_complete_count,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND c.last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours') AS seen_24h,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND c.last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '7 days') AS seen_7d,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND c.last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '30 days') AS seen_30d,
    MAX(c.last_seen_at) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE) AS latest_seen,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND c.start_date IS NOT NULL AND c.end_date IS NOT NULL AND c.end_date < c.start_date) AS invalid_period,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND c.apply_start IS NOT NULL AND c.apply_end IS NOT NULL AND c.apply_end < c.apply_start) AS invalid_apply_period,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND c.apply_start IS NOT NULL AND c.start_date IS NOT NULL AND c.apply_start > c.start_date) AS apply_after_class_start,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND upper(COALESCE(c.status, '')) IN ('OPEN', 'DEADLINE') AND c.apply_end < CURRENT_DATE) AS status_past_apply_end,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND NULLIF(btrim(c.raw_url), '') IS NOT NULL AND c.raw_url !~* '^https?://') AS invalid_source_url,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND NULLIF(btrim(c.application_url), '') IS NOT NULL AND c.application_url !~* '^https?://') AS invalid_application_url,
    COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND c.title IS NOT NULL
          AND (position('�' in c.title) > 0 OR position('??' in c.title) > 0 OR c.title ~ '[ÃÂ]')
    ) AS corrupt_title,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND NULLIF(btrim(c.provider_course_id), '') IS NULL) AS missing_identity,
    COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND (
            (c.start_date IS NOT NULL AND c.end_date IS NOT NULL AND c.end_date < c.start_date)
            OR (c.apply_start IS NOT NULL AND c.apply_end IS NOT NULL AND c.apply_end < c.apply_start)
            OR (c.apply_start IS NOT NULL AND c.start_date IS NOT NULL AND c.apply_start > c.start_date)
            OR (upper(COALESCE(c.status, '')) IN ('OPEN', 'DEADLINE') AND c.apply_end < CURRENT_DATE)
            OR (NULLIF(btrim(c.raw_url), '') IS NOT NULL AND c.raw_url !~* '^https?://')
            OR (NULLIF(btrim(c.application_url), '') IS NOT NULL AND c.application_url !~* '^https?://')
            OR (c.title IS NOT NULL AND (position('�' in c.title) > 0 OR position('??' in c.title) > 0 OR c.title ~ '[ÃÂ]'))
            OR NULLIF(btrim(c.provider_course_id), '') IS NULL
          )
    ) AS invalid_any,
    COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND upper(COALESCE(c.application_type, '')) NOT IN ('INFO_ONLY', 'NO_COURSE_DATA', 'BLOCKED_OR_JS_ONLY')
          AND (
            NULLIF(btrim(c.application_url), '') IS NOT NULL
            OR (
              upper(COALESCE(c.application_type, '')) = 'OFFLINE_APPLY'
              AND COALESCE(c.application_method_raw, '') ~ '(방문|전화|현장|유선|이메일|전자우편|팩스|FAX|구글[ ]*폼|네이버[ ]*폼)'
            )
          )
    ) AS action_path_count,
    COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND upper(COALESCE(c.application_type, '')) NOT IN ('INFO_ONLY', 'NO_COURSE_DATA', 'BLOCKED_OR_JS_ONLY')
    ) AS actionable_population,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND NULLIF(btrim(c.application_url), '') IS NOT NULL) AS application_url_count,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND c.apply_start IS NOT NULL) AS apply_start_count,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND c.apply_end IS NOT NULL) AS apply_end_count,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND c.apply_start IS NOT NULL AND c.apply_end IS NOT NULL) AS apply_both_count,
    COUNT(*) FILTER (WHERE COALESCE(c.is_active, FALSE) IS TRUE AND NULLIF(btrim(c.apply_period_raw), '') IS NOT NULL) AS apply_raw_count,
    COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND (
            upper(COALESCE(c.status, '')) = 'SCHEDULED'
            OR c.start_date >= CURRENT_DATE
            OR (c.start_date IS NULL AND c.end_date >= CURRENT_DATE)
          )
    ) AS alert_population,
    COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND (
            upper(COALESCE(c.status, '')) = 'SCHEDULED'
            OR c.start_date >= CURRENT_DATE
            OR (c.start_date IS NULL AND c.end_date >= CURRENT_DATE)
          )
          AND c.apply_start IS NOT NULL
    ) AS alert_start_ready,
    COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND (
            upper(COALESCE(c.status, '')) = 'SCHEDULED'
            OR c.start_date >= CURRENT_DATE
            OR (c.start_date IS NULL AND c.end_date >= CURRENT_DATE)
          )
          AND c.apply_start IS NOT NULL
          AND c.apply_end IS NOT NULL
    ) AS alert_both_ready
    , COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND (
            upper(COALESCE(c.status, '')) = 'SCHEDULED'
            OR c.start_date >= CURRENT_DATE
            OR (c.start_date IS NULL AND c.end_date >= CURRENT_DATE)
          )
          AND (c.apply_start IS NULL OR c.apply_start >= CURRENT_DATE)
    ) AS alert_candidate_population
    , COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND (
            upper(COALESCE(c.status, '')) = 'SCHEDULED'
            OR c.start_date >= CURRENT_DATE
            OR (c.start_date IS NULL AND c.end_date >= CURRENT_DATE)
          )
          AND c.apply_start >= CURRENT_DATE
    ) AS alert_future_ready
    , COUNT(*) FILTER (
        WHERE COALESCE(c.is_active, FALSE) IS TRUE
          AND (
            upper(COALESCE(c.status, '')) = 'SCHEDULED'
            OR c.start_date >= CURRENT_DATE
            OR (c.start_date IS NULL AND c.end_date >= CURRENT_DATE)
          )
          AND c.apply_start >= CURRENT_DATE
          AND c.apply_end IS NOT NULL
    ) AS alert_future_both_ready
FROM scoped_courses c
WHERE NULLIF(btrim(c.provider), '') IS NOT NULL
GROUP BY GROUPING SETS ((c.provider), (c.provider, c.production_scope))
ORDER BY c.provider, production_scope
"""


def fetch_provider_metrics() -> list[dict[str, Any]]:
    from DB.db_utils import get_db_cursor

    with get_db_cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '5s'")
        cursor.execute(_render_provider_metrics_sql())
        return [dict(row) for row in cursor.fetchall()]


_SAMPLE_BASE = """
    SELECT
        c.id::text AS id,
        c.provider,
        c.provider_course_id,
        c.title,
        b.name AS branch,
        c.status,
        c.start_date,
        c.end_date,
        c.apply_start,
        c.apply_end,
        c.apply_period_raw,
        c.schedule_raw,
        c.schedule_dates,
        c.target,
        c.target_age_group,
        c.target_min_age,
        c.target_max_age,
        c.category_raw,
        c.collection_category,
        c.domain_category,
        c.ai_category,
        c.source_group,
        c.operator_type,
        c.service_group,
        c.program_type,
        c.branch_id::text AS branch_id,
        NULLIF(btrim(c.description), '') IS NOT NULL AS description_present,
        c.application_type,
        c.application_method_raw,
        c.application_url,
        c.raw_url,
        c.last_seen_at,
        (
          upper(COALESCE(c.status, '')) IN ('OPEN', 'DEADLINE')
          AND c.apply_end < CURRENT_DATE
        ) AS status_past_apply_end,
        (
          c.title IS NOT NULL
          AND (position('�' in c.title) > 0 OR position('??' in c.title) > 0 OR c.title ~ '[ÃÂ]')
        ) AS corrupt_title,
        CASE WHEN c.last_seen_at IS NULL THEN NULL
             ELSE FLOOR(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - c.last_seen_at)) / 86400)::integer
        END AS age_days
    FROM courses c
    LEFT JOIN branches b ON b.id = c.branch_id
    WHERE c.provider = %s
      AND COALESCE(c.is_active, FALSE) IS TRUE
"""

_CORE_COMPLETE_PREDICATE = """
    NULLIF(btrim(c.title), '') IS NOT NULL
    AND c.branch_id IS NOT NULL
    AND NULLIF(btrim(c.raw_url), '') IS NOT NULL
    AND NULLIF(btrim(c.status), '') IS NOT NULL
    AND c.start_date IS NOT NULL
    AND c.end_date IS NOT NULL
    AND (
      NULLIF(btrim(c.schedule_raw), '') IS NOT NULL
      OR (c.schedule_dates IS NOT NULL AND jsonb_typeof(c.schedule_dates) = 'array' AND jsonb_array_length(c.schedule_dates) > 0)
    )
    AND (
      NULLIF(btrim(c.target), '') IS NOT NULL
      OR c.target_age_group IS NOT NULL
      OR c.target_min_age IS NOT NULL
      OR c.target_max_age IS NOT NULL
    )
    AND NULLIF(btrim(c.description), '') IS NOT NULL
"""

_INVALID_PREDICATE = """
    (c.start_date IS NOT NULL AND c.end_date IS NOT NULL AND c.end_date < c.start_date)
    OR (c.apply_start IS NOT NULL AND c.apply_end IS NOT NULL AND c.apply_end < c.apply_start)
    OR (c.apply_start IS NOT NULL AND c.start_date IS NOT NULL AND c.apply_start > c.start_date)
    OR (upper(COALESCE(c.status, '')) IN ('OPEN', 'DEADLINE') AND c.apply_end < CURRENT_DATE)
    OR (NULLIF(btrim(c.raw_url), '') IS NOT NULL AND c.raw_url !~* '^https?://')
    OR (NULLIF(btrim(c.application_url), '') IS NOT NULL AND c.application_url !~* '^https?://')
    OR (c.title IS NOT NULL AND (position('�' in c.title) > 0 OR position('??' in c.title) > 0 OR c.title ~ '[ÃÂ]'))
    OR NULLIF(btrim(c.provider_course_id), '') IS NULL
"""

SAMPLE_PREDICATES = {
    "STALE_DATA": "c.last_seen_at IS NULL OR c.last_seen_at < CURRENT_TIMESTAMP - INTERVAL '7 days'",
    "CORE_COMPLETENESS": f"NOT ({_CORE_COMPLETE_PREDICATE})",
    "INVALID_DATA": f"({_INVALID_PREDICATE})",
    "ACTION_PATH_MISSING": """
        upper(COALESCE(c.application_type, '')) NOT IN ('INFO_ONLY', 'NO_COURSE_DATA', 'BLOCKED_OR_JS_ONLY')
        AND
        NULLIF(btrim(c.application_url), '') IS NULL
        AND NOT (
          upper(COALESCE(c.application_type, '')) = 'OFFLINE_APPLY'
          AND COALESCE(c.application_method_raw, '') ~ '(방문|전화|현장|유선|이메일|전자우편|팩스|FAX|구글[ ]*폼|네이버[ ]*폼)'
        )
    """,
    "REGISTRATION_SCHEDULE_MISSING": """
        (
          upper(COALESCE(c.status, '')) = 'SCHEDULED'
          OR c.start_date >= CURRENT_DATE
          OR (c.start_date IS NULL AND c.end_date >= CURRENT_DATE)
        )
        AND c.apply_start IS NULL
    """,
    "RECEPTION_DATA_MISSING": "c.apply_start IS NULL",
    "RECEPTION_RAW_MISSING": "NULLIF(btrim(c.apply_period_raw), '') IS NULL",
}


def _core_missing_fields(row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not str(row.get("title") or "").strip():
        missing.append("title")
    if not row.get("branch_id"):
        missing.append("branch")
    if not str(row.get("raw_url") or "").strip():
        missing.append("source_url")
    if not str(row.get("status") or "").strip():
        missing.append("status")
    if row.get("start_date") is None or row.get("end_date") is None:
        missing.append("period")
    schedule_dates = row.get("schedule_dates")
    if not str(row.get("schedule_raw") or "").strip() and not (
        isinstance(schedule_dates, list) and bool(schedule_dates)
    ):
        missing.append("schedule")
    if not any(
        row.get(key) not in (None, "")
        for key in ("target", "target_age_group", "target_min_age", "target_max_age")
    ):
        missing.append("target")
    if not row.get("description_present"):
        missing.append("description")
    return missing


_CORE_MISSING_CODES = {
    "title": "TITLE_MISSING",
    "branch": "BRANCH_MISSING",
    "source_url": "SOURCE_URL_MISSING",
    "status": "STATUS_MISSING",
    "period": "COURSE_PERIOD_INCOMPLETE",
    "schedule": "SCHEDULE_MISSING",
    "target": "TARGET_MISSING",
    "description": "DESCRIPTION_MISSING",
}


def _invalid_violation_codes(row: dict[str, Any]) -> list[str]:
    invalid: list[str] = []
    start_date, end_date = row.get("start_date"), row.get("end_date")
    apply_start, apply_end = row.get("apply_start"), row.get("apply_end")
    if start_date is not None and end_date is not None and end_date < start_date:
        invalid.append("COURSE_PERIOD_REVERSED")
    if apply_start is not None and apply_end is not None and apply_end < apply_start:
        invalid.append("RECEPTION_PERIOD_REVERSED")
    if apply_start is not None and start_date is not None and apply_start > start_date:
        invalid.append("RECEPTION_AFTER_CLASS_START")
    if row.get("status_past_apply_end"):
        invalid.append("STATUS_PAST_APPLY_END")
    if str(row.get("raw_url") or "").strip() and not str(row.get("raw_url") or "").lower().startswith(
        ("http://", "https://")
    ):
        invalid.append("SOURCE_URL_INVALID")
    if str(row.get("application_url") or "").strip() and not str(
        row.get("application_url") or ""
    ).lower().startswith(("http://", "https://")):
        invalid.append("APPLICATION_URL_INVALID")
    if row.get("corrupt_title"):
        invalid.append("CORRUPT_TITLE")
    if not str(row.get("provider_course_id") or "").strip():
        invalid.append("COURSE_IDENTITY_MISSING")
    return invalid


def _sample_violation_codes(row: dict[str, Any], issue_code: str) -> list[str]:
    if issue_code == "STALE_DATA":
        return ["NEVER_SEEN" if row.get("last_seen_at") is None else "FRESHNESS_SLA_EXCEEDED"]
    if issue_code == "ACTION_PATH_MISSING":
        return ["APPLICATION_PATH_MISSING"]
    if issue_code in {"REGISTRATION_SCHEDULE_MISSING", "RECEPTION_DATA_MISSING"}:
        return ["APPLY_START_MISSING"]
    if issue_code == "RECEPTION_RAW_MISSING":
        return ["APPLY_PERIOD_RAW_MISSING"]
    if issue_code == "CORE_COMPLETENESS":
        missing = [_CORE_MISSING_CODES[field] for field in _core_missing_fields(row)]
        return missing or ["CORE_FIELD_INCOMPLETE"]
    if issue_code == "INVALID_DATA":
        return _invalid_violation_codes(row) or ["DATA_CONSISTENCY_ERROR"]
    return [issue_code]


def fetch_issue_samples(
    provider: str,
    issue_code: str,
    limit: int = 50,
    offset: int = 0,
    sla_days: int = 7,
    scope: str = "all",
) -> list[dict[str, Any]]:
    provider = str(provider or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_.:-]{1,100}", provider):
        raise ValueError("invalid provider")
    issue_code = str(issue_code or "").strip().upper()
    predicate = SAMPLE_PREDICATES.get(issue_code)
    if not predicate:
        raise ValueError("unsupported issue code")
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, min(int(offset or 0), 1_000_000))
    sla_days = max(1, min(int(sla_days or 7), 90))
    scope = _normalize_production_scope(scope)
    params: list[Any] = [provider]
    if issue_code == "STALE_DATA":
        predicate = "c.last_seen_at IS NULL OR c.last_seen_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')"
        params.append(sla_days)
    params.extend([limit, offset])
    from DB.db_utils import get_db_cursor

    with get_db_cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '5s'")
        cursor.execute(
            f"{_SAMPLE_BASE} AND ({production_scope_predicate_sql(scope)}) AND ({predicate}) "
            "ORDER BY c.last_seen_at ASC NULLS FIRST, c.id LIMIT %s OFFSET %s",
            params,
        )
        rows = [dict(row) for row in cursor.fetchall()]
    for row in rows:
        row["operational_scope"] = scope
        row["violation_codes"] = _sample_violation_codes(row, issue_code)
        for key in ("start_date", "end_date", "apply_start", "apply_end", "last_seen_at"):
            row[key] = _iso(row.get(key))
    return rows


_COLLECTED_COURSE_SELECT = """
    SELECT
        c.id::text AS id,
        c.provider,
        c.provider_course_id,
        c.title,
        b.name AS branch,
        c.branch_id::text AS branch_id,
        COALESCE(c.is_active, FALSE) AS is_active,
        c.status,
        c.start_date,
        c.end_date,
        c.apply_start,
        c.apply_end,
        c.apply_period_raw,
        c.schedule_raw,
        c.schedule_dates,
        c.target,
        c.target_age_group,
        c.target_min_age,
        c.target_max_age,
        c.service_group,
        c.program_type,
        c.category_raw,
        c.collection_category,
        c.domain_category,
        c.ai_category,
        c.source_group,
        c.operator_type,
        c.standard_category_label,
        c.fee::text AS fee,
        c.venue_name,
        c.application_type,
        c.application_method_raw,
        c.application_url,
        c.raw_url,
        LEFT(c.description, 500) AS description_excerpt,
        NULLIF(btrim(c.description), '') IS NOT NULL AS description_present,
        c.last_seen_at,
        c.updated_at,
        c.apply_start >= CURRENT_DATE AS reception_ready,
        (
          upper(COALESCE(c.status, '')) IN ('OPEN', 'DEADLINE')
          AND c.apply_end < CURRENT_DATE
        ) AS status_past_apply_end,
        (
          c.title IS NOT NULL
          AND (position('�' in c.title) > 0 OR position('??' in c.title) > 0 OR c.title ~ '[ÃÂ]')
        ) AS corrupt_title,
        CASE WHEN c.last_seen_at IS NULL THEN NULL
             ELSE FLOOR(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - c.last_seen_at)) / 86400)::integer
        END AS age_days
    FROM courses c
    LEFT JOIN branches b ON b.id = c.branch_id
"""

_COLLECTED_STATE_PREDICATES = {
    "active": "COALESCE(c.is_active, FALSE) IS TRUE",
    "all": "TRUE",
    "inactive": "COALESCE(c.is_active, FALSE) IS FALSE",
}

_COLLECTED_VIEW_PREDICATES = {
    "all": "TRUE",
    "incomplete": f"NOT ({_CORE_COMPLETE_PREDICATE})",
    "invalid": f"({_INVALID_PREDICATE})",
    "reception_ready": "c.apply_start >= CURRENT_DATE",
    "reception_missing": "c.apply_start IS NULL",
}


def _validate_collected_course_query(
    provider: str,
    state: str,
    view: str,
    query: str,
    limit: int,
    offset: int,
) -> tuple[str, str, str, str, int, int]:
    normalized_provider = str(provider or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_.:-]{1,100}", normalized_provider):
        raise ValueError("invalid provider")
    normalized_state = str(state or "").strip().lower()
    if normalized_state not in COLLECTED_COURSE_STATES:
        raise ValueError("state must be active, all, or inactive")
    normalized_view = str(view or "").strip().lower()
    if normalized_view not in COLLECTED_COURSE_VIEWS:
        raise ValueError(
            "view must be all, incomplete, invalid, reception_ready, or reception_missing"
        )
    normalized_query = str(query or "").strip()
    if len(normalized_query) > 100 or re.search(r"[\x00-\x1f\x7f]", normalized_query):
        raise ValueError("query must be at most 100 characters without control characters")
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be between 1 and 100") from exc
    if not 1 <= normalized_limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    try:
        normalized_offset = int(offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("offset must be between 0 and 1000000") from exc
    if not 0 <= normalized_offset <= 1_000_000:
        raise ValueError("offset must be between 0 and 1000000")
    return (
        normalized_provider,
        normalized_state,
        normalized_view,
        normalized_query,
        normalized_limit,
        normalized_offset,
    )


def _normalize_collected_course(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    missing_fields = _core_missing_fields(item)
    present_weight = sum(weight for field, weight in CORE_FIELD_WEIGHTS.items() if field not in missing_fields)
    field_score = round(present_weight / sum(CORE_FIELD_WEIGHTS.values()) * 100, 1)
    invalid_codes = _invalid_violation_codes(item)
    violation_codes = [_CORE_MISSING_CODES[field] for field in missing_fields]
    violation_codes.extend(invalid_codes)
    if item.get("apply_start") is None:
        violation_codes.append("APPLY_START_MISSING")
    if not str(item.get("apply_period_raw") or "").strip():
        violation_codes.append("APPLY_PERIOD_RAW_MISSING")
    item.update(
        field_score=field_score,
        missing_fields=missing_fields,
        violation_codes=list(dict.fromkeys(violation_codes)),
        is_incomplete=bool(missing_fields),
        is_invalid=bool(invalid_codes),
        reception_ready=bool(item.get("reception_ready")),
    )
    for key in ("start_date", "end_date", "apply_start", "apply_end", "last_seen_at", "updated_at"):
        item[key] = _iso(item.get(key))
    return item


def production_scope_source_metadata(
    dataset: dict[str, Any],
    scope: str,
) -> dict[str, Any]:
    scope = _normalize_production_scope(scope, allow_all=False)
    if scope not in PRODUCTION_COURSES_API_SCOPES:
        raise ValueError("production API metadata is available only for experience or education")
    scope_data = (dataset.get("scopes") or {}).get(scope) or {}
    return {
        "kind": "production_public_api",
        "url": PRODUCTION_COURSES_API_URL,
        "status": "ok",
        "authoritative": True,
        "fetched_at": dataset.get("fetched_at"),
        "scope": scope,
        "active_courses": int(scope_data.get("active") or 0),
        "archived_courses": int(scope_data.get("total") or 0),
        "inactive_courses": int(scope_data.get("inactive") or 0),
        "quality_metrics_available": False,
    }


def production_scope_provider_rows(
    scope: str,
    dataset: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    scope = _normalize_production_scope(scope, allow_all=False)
    if scope not in PRODUCTION_COURSES_API_SCOPES:
        raise ValueError("production provider rows are available only for experience or education")
    dataset = dataset or get_production_course_dataset()
    scope_rows = list(((dataset.get("scopes") or {}).get(scope) or {}).get("items") or [])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in scope_rows:
        grouped.setdefault(str(row.get("provider") or "").strip().upper(), []).append(row)
    result: list[dict[str, Any]] = []
    for provider, rows in grouped.items():
        active = sum(1 for row in rows if row.get("is_active") is True)
        if active <= 0:
            continue
        latest_seen = max(
            (str(row.get("last_seen_at") or "") for row in rows),
            default="",
        ) or None
        label = next(
            (str(row.get("provider_label") or "").strip() for row in rows if row.get("provider_label")),
            provider,
        )
        result.append(
            {
                "provider": provider,
                "label": label,
                "scope": scope,
                "scopes": [scope],
                "production_scope": scope,
                "total": len(rows),
                "active": active,
                "inactive": len(rows) - active,
                "branches": len(
                    {
                        str(row.get("branch_id") or ((row.get("branch") or {}).get("id") if isinstance(row.get("branch"), dict) else ""))
                        for row in rows
                        if row.get("branch_id") or isinstance(row.get("branch"), dict)
                    }
                ),
                "latest_seen": latest_seen,
                "overall_score": None,
                "status": "not_evaluated",
                "dimensions": {},
                "configured": None,
                "quality_metrics_available": False,
                "source": production_scope_source_metadata(dataset, scope),
            }
        )
    return sorted(result, key=lambda row: (-int(row["active"]), str(row["provider"])))


def production_scope_summary(
    scope: str,
    dataset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scope = _normalize_production_scope(scope, allow_all=False)
    if scope not in PRODUCTION_COURSES_API_SCOPES:
        raise ValueError("production summary is available only for experience or education")
    dataset = dataset or get_production_course_dataset()
    scope_data = (dataset.get("scopes") or {}).get(scope) or {}
    providers = production_scope_provider_rows(scope, dataset)
    return {
        "scope": scope,
        "overall_score": None,
        "status": "not_evaluated",
        "quality_metrics_available": False,
        "provider_count": len(providers),
        "providers_with_active_data": len(providers),
        "total_courses": int(scope_data.get("total") or 0),
        "active_courses": int(scope_data.get("active") or 0),
        "inactive_courses": int(scope_data.get("inactive") or 0),
        "dimensions": {},
        "status_counts": {"not_evaluated": len(providers)},
        "source": production_scope_source_metadata(dataset, scope),
    }


def _production_row_age_days(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        seen_at = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if seen_at.tzinfo is None:
            seen_at = seen_at.replace(tzinfo=SEOUL)
        return max(0, int((datetime.now(SEOUL) - seen_at.astimezone(SEOUL)).total_seconds() // 86400))
    except ValueError:
        return None


def _normalize_production_collected_course(row: dict[str, Any], scope: str) -> dict[str, Any]:
    item = dict(row)
    branch = item.get("branch") if isinstance(item.get("branch"), dict) else {}
    description = str(item.get("description") or "").strip()
    today = datetime.now(SEOUL).date().isoformat()
    apply_start = str(item.get("apply_start") or "").strip()
    apply_end = str(item.get("apply_end") or "").strip()
    status = str(item.get("status") or "").strip().upper()
    title = str(item.get("title") or "")
    item.update(
        branch=str(branch.get("name") or "").strip() or None,
        branch_id=str(item.get("branch_id") or branch.get("id") or "").strip() or None,
        standard_category_label=item.get("standard_category"),
        description_excerpt=description[:500] or None,
        description_present=bool(description),
        updated_at=item.get("change_detected_at") or item.get("last_seen_at"),
        reception_ready=bool(apply_start and apply_start >= today),
        status_past_apply_end=bool(status in {"OPEN", "DEADLINE"} and apply_end and apply_end < today),
        corrupt_title="�" in title,
        age_days=_production_row_age_days(item.get("last_seen_at")),
    )
    normalized = _normalize_collected_course(item)
    normalized["operational_scope"] = scope
    normalized["data_source"] = "production_public_api"
    return normalized


def fetch_production_collected_courses(
    provider: str,
    state: str = "active",
    view: str = "all",
    query: str = "",
    limit: int = 25,
    offset: int = 0,
    scope: str = "education",
) -> dict[str, Any]:
    provider, state, view, query, limit, offset = _validate_collected_course_query(
        provider, state, view, query, limit, offset
    )
    scope = _normalize_production_scope(scope, allow_all=False)
    if scope not in PRODUCTION_COURSES_API_SCOPES:
        raise ValueError("production courses require experience or education scope")
    dataset = get_production_course_dataset()
    raw_rows = list(((dataset.get("scopes") or {}).get(scope) or {}).get("items") or [])
    rows = [
        _normalize_production_collected_course(row, scope)
        for row in raw_rows
        if str(row.get("provider") or "").strip().upper() == provider
    ]
    if state == "active":
        rows = [row for row in rows if row.get("is_active") is True]
    elif state == "inactive":
        rows = [row for row in rows if row.get("is_active") is False]
    if view == "incomplete":
        rows = [row for row in rows if row.get("is_incomplete")]
    elif view == "invalid":
        rows = [row for row in rows if row.get("is_invalid")]
    elif view == "reception_ready":
        rows = [row for row in rows if row.get("reception_ready")]
    elif view == "reception_missing":
        rows = [row for row in rows if not row.get("apply_start")]
    if query:
        folded = query.casefold()
        rows = [
            row
            for row in rows
            if folded
            in " ".join(
                str(row.get(key) or "")
                for key in ("title", "branch", "provider_course_id", "venue_name")
            ).casefold()
        ]
    total = len(rows)
    page_rows = rows[offset : offset + limit]
    return {
        "provider": provider,
        "scope": scope,
        "state": state,
        "view": view,
        "query": query,
        "total": total,
        "count": len(page_rows),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page_rows) < total,
        "next_offset": offset + len(page_rows) if offset + len(page_rows) < total else None,
        "items": page_rows,
        "source": production_scope_source_metadata(dataset, scope),
    }


def fetch_collected_courses(
    provider: str,
    state: str = "active",
    view: str = "all",
    query: str = "",
    limit: int = 25,
    offset: int = 0,
    scope: str = "all",
) -> dict[str, Any]:
    """Return a validated, read-only page of normalized collected course rows."""

    provider, state, view, query, limit, offset = _validate_collected_course_query(
        provider, state, view, query, limit, offset
    )
    scope = _normalize_production_scope(scope)
    predicates = [
        "c.provider = %s",
        production_scope_predicate_sql(scope),
        _COLLECTED_STATE_PREDICATES[state],
        _COLLECTED_VIEW_PREDICATES[view],
    ]
    params: list[Any] = [provider]
    if query:
        predicates.append(
            """
            (
              position(lower(%s) in lower(COALESCE(c.title, ''))) > 0
              OR position(lower(%s) in lower(COALESCE(b.name, ''))) > 0
              OR position(lower(%s) in lower(COALESCE(c.provider_course_id, ''))) > 0
              OR position(lower(%s) in lower(COALESCE(c.venue_name, ''))) > 0
            )
            """
        )
        params.extend([query, query, query, query])
    where_sql = " AND ".join(f"({predicate})" for predicate in predicates)
    count_sql = f"""
        SELECT COUNT(*)::integer AS total
        FROM courses c
        LEFT JOIN branches b ON b.id = c.branch_id
        WHERE {where_sql}
    """
    rows_sql = f"""
        {_COLLECTED_COURSE_SELECT}
        WHERE {where_sql}
        ORDER BY c.last_seen_at DESC NULLS LAST, c.updated_at DESC NULLS LAST, c.id
        LIMIT %s OFFSET %s
    """

    from DB.db_utils import get_db_cursor

    with get_db_cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '5s'")
        cursor.execute(count_sql, params)
        count_row = cursor.fetchone()
        total = _as_int(count_row.get("total") if count_row else 0)
        cursor.execute(rows_sql, [*params, limit, offset])
        rows = [_normalize_collected_course(dict(row)) for row in cursor.fetchall()]
        for row in rows:
            row["operational_scope"] = scope
    return {
        "provider": provider,
        "scope": scope,
        "state": state,
        "view": view,
        "query": query,
        "total": total,
        "count": len(rows),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        "items": rows,
    }
