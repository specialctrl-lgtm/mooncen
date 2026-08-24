"""Fail-closed collector for Cheongju's municipal lifelong-learning catalogue.

The public ``P0401`` page is the citywide entry point for every internal
education category.  Its backing JSON endpoint also contains thousands of
ended rows and externally linked catalogues, so an unfiltered crawl is neither
an operationally bounded snapshot nor a safe ownership boundary.  This module
queries the four official current/future lifecycle states with
``lctreTy=inner``, validates every declared page and composite course identity,
enriches every retained row from the detail and schedule endpoints, and then
rechecks the complete state slices before publishing anything.

The three narrower URLs audited before the citywide entry point are explicit
non-executing aliases.  They are exact subsets of this provider and must never
be scheduled as independent providers.

This module intentionally does not import ``Crawler_MunicipalYaml``.  The
shared router can inject its managed session factory after importing this
module without creating a cycle.  No request disables TLS verification, and
no source payload containing instructor contact data is persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


CHEONGJU_PROVIDER = "MUNI_LLL_CHEONGJU_GO_KR_DA1AAEA1"
CHEONGJU_CANDIDATE_ID = "MUNI_IR_0D93EB9222E3"
CHEONGJU_URL = "https://lll.cheongju.go.kr/papp/P0401"
CHEONGJU_HOST = "lll.cheongju.go.kr"
CHEONGJU_PATH = "/papp/P0401"
CHEONGJU_API_ROOT = f"https://{CHEONGJU_HOST}/info"
CHEONGJU_PAGING_URL = f"{CHEONGJU_API_ROOT}/lctre/request/paging"
CHEONGJU_DETAIL_URL = f"{CHEONGJU_API_ROOT}/lctre/request"
CHEONGJU_SCHEDULE_URL = f"{CHEONGJU_API_ROOT}/schdul"
CHEONGJU_PAGE_SIZE = 50
CHEONGJU_MAX_WORKERS = 8
CHEONGJU_PARSER = (
    "cheongju_inner_active_states_complete_pages+composite_ids+"
    "detail_schedule+stable_recheck+pii_allowlist"
)

CHEONGJU_MUNICIPALITY_CODE = "4311000000"
CHEONGJU_MUNICIPALITY_NAME = "충청북도 청주시"
CHEONGJU_COVERED_MUNICIPALITIES: tuple[dict[str, str], ...] = (
    {
        "code": "4311000000",
        "sido": "충청북도",
        "sigungu": "청주시",
        "full_name": "충청북도 청주시",
    },
    {
        "code": "4311100000",
        "sido": "충청북도",
        "sigungu": "청주시 상당구",
        "full_name": "충청북도 청주시 상당구",
    },
    {
        "code": "4311200000",
        "sido": "충청북도",
        "sigungu": "청주시 서원구",
        "full_name": "충청북도 청주시 서원구",
    },
    {
        "code": "4311300000",
        "sido": "충청북도",
        "sigungu": "청주시 흥덕구",
        "full_name": "충청북도 청주시 흥덕구",
    },
    {
        "code": "4311400000",
        "sido": "충청북도",
        "sigungu": "청주시 청원구",
        "full_name": "충청북도 청주시 청원구",
    },
)
CHEONGJU_MUNICIPALITY_NAMES = {
    item["code"]: item["full_name"] for item in CHEONGJU_COVERED_MUNICIPALITIES
}
CHEONGJU_BRANCH_ADDRESSES: Mapping[str, str] = {
    "평생학습관 - 본관": "청주시 흥덕구 월명로 13번길 52 (복대동 61-1)",
    "평생학습관 - 상당분관": "청주시 상당구 단재로 395 (지북동 208-1)",
}


@dataclass(frozen=True)
class CheongjuAlias:
    provider: str
    url: str
    reason: str


CHEONGJU_NON_EXECUTING_ALIASES: tuple[CheongjuAlias, ...] = (
    CheongjuAlias(
        "MUNI_LLL_CHEONGJU_GO_KR_42262CCC",
        "https://lll.cheongju.go.kr/papp/P020101",
        "regular-program subset; the runtime view is P020104",
    ),
    CheongjuAlias(
        "MUNI_LLL_CHEONGJU_GO_KR_50067AE6",
        "https://lll.cheongju.go.kr/papp/P020202",
        "lifelong-centre subset of the citywide internal catalogue",
    ),
    CheongjuAlias(
        "MUNI_LLL_CHEONGJU_GO_KR_A90C7827",
        "https://lll.cheongju.go.kr/ccu/capp/C1202",
        "citizen-university subset of the citywide internal catalogue",
    ),
)
CHEONGJU_OWNERSHIP_ALIAS_URLS = tuple(item.url for item in CHEONGJU_NON_EXECUTING_ALIASES)


@dataclass(frozen=True)
class CheongjuStatusScope:
    code: str
    name: str
    normalized_status: str


CHEONGJU_STATUS_SCOPES: tuple[CheongjuStatusScope, ...] = (
    CheongjuStatusScope("C0120002", "접수대기", "SCHEDULED"),
    CheongjuStatusScope("C0120003", "접수진행", "OPEN"),
    CheongjuStatusScope("C0120004", "접수마감", "CLOSED"),
    CheongjuStatusScope("C0120005", "교육중", "CLOSED"),
)


@dataclass(frozen=True)
class CheongjuCategoryRoute:
    code: str
    name: str
    path: str
    supports_course_fragment: bool = True


# C0020013 is a public information page rather than a Vue course-list view;
# those rows therefore link to the category landing without an invalid hash.
CHEONGJU_CATEGORY_ROUTES: Mapping[str, CheongjuCategoryRoute] = {
    item.code: item
    for item in (
        CheongjuCategoryRoute("C0020001", "정규프로그램", "/papp/P020104"),
        CheongjuCategoryRoute("C0020014", "배움더하기", "/papp/P020104"),
        CheongjuCategoryRoute("C0020003", "청주아카데미", "/papp/P020301"),
        CheongjuCategoryRoute("C0020004", "평생학습센터", "/papp/P020202"),
        CheongjuCategoryRoute("C0020005", "테마특강", "/papp/P020303"),
        CheongjuCategoryRoute("C0020006", "청주시민학교", "/papp/P020304"),
        CheongjuCategoryRoute("C0020007", "신중년 인생설계", "/papp/P020306"),
        CheongjuCategoryRoute("C0020008", "온라인홈공방", "/papp/P020302"),
        CheongjuCategoryRoute("C0020009", "딩동! 찾아가는평생학습", "/papp/P020503"),
        CheongjuCategoryRoute("C0020002", "무엇이든배움터", "/papp/P0206"),
        CheongjuCategoryRoute("C0020010", "심신프리-해봄", "/papp/P020307"),
        CheongjuCategoryRoute("C0020011", "엄지톡톡 디지털동행특강", "/papp/P020308"),
        CheongjuCategoryRoute("C0020012", "시민대학", "/ccu/capp/C1202"),
        CheongjuCategoryRoute(
            "C0020013", "성인문해", "/papp/P020305", supports_course_fragment=False
        ),
    )
}


class CheongjuContractError(ValueError):
    """Raised when the live API no longer satisfies the audited contract."""


JsonGetter = Callable[[Any, str, Mapping[str, Any], int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")
_BATCH_RE = re.compile(r"[A-Za-z0-9_-]{0,20}\Z")
_PII_KEYS = frozenset(
    {
        "moblphon",
        "mobile",
        "mobilenumber",
        "phone",
        "phonenumber",
        "tel",
        "telno",
        "telephone",
        "email",
        "emailaddress",
        "userid",
        "usernm",
        "username",
        "changeuser",
        "password",
        "residentnumber",
        "rrn",
    }
)
_PHONE_VALUE_RE = re.compile(r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)")
_EMAIL_VALUE_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass(frozen=True, order=True)
class CheongjuCourseKey:
    lecture_seq: int
    batch_type: str
    additional_order: int

    @property
    def token(self) -> str:
        batch = self.batch_type or "base"
        return f"{self.lecture_seq}:{batch}:{self.additional_order}"

    @property
    def detail_params(self) -> dict[str, Any]:
        return {
            "lctreSeq": self.lecture_seq,
            "batchTy": self.batch_type,
            "aditOrdr": self.additional_order,
        }


@dataclass(frozen=True)
class _ListedCourse:
    key: CheongjuCourseKey
    scope: CheongjuStatusScope
    page: int
    position: int
    item: Mapping[str, Any]
    title: str
    category_code: str
    category_name: str
    start_date: date
    end_date: date
    apply_start: date
    apply_end: date


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean(value).casefold())


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
        try:
            return date.fromisoformat(_clean(value))
        except ValueError as exc:
            raise CheongjuContractError("today is not an ISO date") from exc
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _canonical_url_match(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower() == CHEONGJU_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == CHEONGJU_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def is_cheongju_target(target: Any) -> bool:
    """Accept only the reviewed provider and exact citywide P0401 URL."""

    return _provider(target) == CHEONGJU_PROVIDER and _canonical_url_match(
        _target_url(target)
    )


is_target = is_cheongju_target


def cheongju_alias_for_target(target: Any) -> Optional[CheongjuAlias]:
    provider = _provider(target)
    url = _target_url(target)
    return next(
        (
            alias
            for alias in CHEONGJU_NON_EXECUTING_ALIASES
            if provider == alias.provider and url == alias.url
        ),
        None,
    )


def is_cheongju_alias_target(target: Any) -> bool:
    return cheongju_alias_for_target(target) is not None


def is_cheongju_source_target(target: Any) -> bool:
    return is_cheongju_target(target) or is_cheongju_alias_target(target)


def cheongju_alias_metadata(target: Any) -> dict[str, Any]:
    alias = cheongju_alias_for_target(target)
    if alias is None:
        return {}
    return {
        "non_executing_alias": True,
        "execution_enabled": False,
        "alias_provider": alias.provider,
        "alias_url": alias.url,
        "duplicate_of": CHEONGJU_PROVIDER,
        "canonical_provider": CHEONGJU_PROVIDER,
        "canonical_url": CHEONGJU_URL,
        "alias_reason": alias.reason,
    }


def sanitize_cheongju_payload(value: Any) -> Any:
    """Recursively remove contact/account fields and redact scalar PII.

    Rows use a positive allowlist and never persist source payloads.  This
    helper is intentionally exported as a defence-in-depth contract for any
    future diagnostic metadata.
    """

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if _normalized_key(key) in _PII_KEYS:
                continue
            result[str(key)] = sanitize_cheongju_payload(child)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize_cheongju_payload(child) for child in value]
    if isinstance(value, str):
        return _EMAIL_VALUE_RE.sub(
            "[redacted-email]", _PHONE_VALUE_RE.sub("[redacted-phone]", value)
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clean(value)


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


def _strict_response(response: Any) -> Any:
    if int(getattr(response, "status_code", 0)) != 200:
        raise CheongjuContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "history", ()) or getattr(response, "headers", {}).get(
        "Location"
    ):
        raise CheongjuContractError("redirect response is not accepted")
    content_type = _clean(getattr(response, "headers", {}).get("Content-Type")).lower()
    if content_type and "json" not in content_type:
        raise CheongjuContractError("response content type is not JSON")
    loader = getattr(response, "json", None)
    if not callable(loader):
        raise CheongjuContractError("response has no JSON loader")
    return response


def _default_json_getter(
    current_session: Any,
    url: str,
    params: Mapping[str, Any],
    timeout: int,
) -> Any:
    return _strict_response(
        current_session.get(
            url,
            params=dict(params),
            headers={
                "Referer": CHEONGJU_URL,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=timeout,
            allow_redirects=False,
        )
    )


def _coerce_json(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    result = _strict_response(value).json()
    if not isinstance(result, Mapping):
        raise CheongjuContractError("JSON response is not an object")
    return result


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _Requester:
    def __init__(
        self,
        session_factory: SessionFactory,
        json_getter: JsonGetter,
        timeout: int,
    ) -> None:
        self.session = session_factory()
        self.json_getter = json_getter
        self.timeout = timeout
        self.calls = 0

    def get(self, url: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls += 1
        return _coerce_json(
            self.json_getter(self.session, url, dict(params), self.timeout)
        )

    def close(self) -> None:
        _close_quietly(self.session)


def _envelope(payload: Mapping[str, Any], *, endpoint: str) -> None:
    if payload.get("code") not in (None, 200):
        raise CheongjuContractError(f"{endpoint} returned code {payload.get('code')!r}")
    errors = payload.get("errors")
    if errors not in (None, [], {}):
        raise CheongjuContractError(f"{endpoint} returned errors")


def _integer(value: Any, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheongjuContractError(f"{field} is not an integer")
    if value < (1 if positive else 0):
        raise CheongjuContractError(f"{field} is out of range")
    return value


def _iso_date(value: Any, field: str) -> date:
    text = _clean(value)
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text):
        raise CheongjuContractError(f"{field} is not an ISO date")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CheongjuContractError(f"{field} is not a valid date") from exc


def _time(value: Any, field: str) -> str:
    text = _clean(value)
    if not _TIME_RE.fullmatch(text):
        raise CheongjuContractError(f"{field} is not an HH:MM time")
    return text


def _code_name(value: Any, field: str, *, allow_blank: bool = False) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise CheongjuContractError(f"{field} is not an object")
    code = _clean(value.get("code") or value.get("value"))
    name = _clean(value.get("name") or value.get("valueName"))
    if not allow_blank and (not code or not name):
        raise CheongjuContractError(f"{field} code/name is empty")
    return code, name


def _course_key(item: Mapping[str, Any]) -> CheongjuCourseKey:
    sequence = _integer(item.get("lctreSeq"), "lctreSeq", positive=True)
    batch = _clean(item.get("batchTy"))
    if not _BATCH_RE.fullmatch(batch):
        raise CheongjuContractError("batchTy is malformed")
    order = _integer(item.get("aditOrdr"), "aditOrdr")
    return CheongjuCourseKey(sequence, batch, order)


def _validate_list_item(
    item: Any,
    scope: CheongjuStatusScope,
    *,
    page: int,
    position: int,
) -> _ListedCourse:
    if not isinstance(item, Mapping):
        raise CheongjuContractError("list item is not an object")
    required = {
        "lctreSeq",
        "batchTy",
        "aditOrdr",
        "lctreNm",
        "beginDe",
        "endDe",
        "beginTm",
        "endTm",
        "rceptBeginDe",
        "rceptBeginTm",
        "rceptEndDe",
        "rceptEndTm",
        "amount",
        "psncpa",
        "applyCnt",
        "edcSe",
        "result",
    }
    missing = sorted(required.difference(item))
    if missing:
        raise CheongjuContractError(f"list item missing keys {missing!r}")
    key = _course_key(item)
    title = _clean(item.get("lctreNm"))
    if not title:
        raise CheongjuContractError(f"course {key.token} title is empty")
    category_code, category_name = _code_name(item.get("edcSe"), "edcSe")
    route = CHEONGJU_CATEGORY_ROUTES.get(category_code)
    if route is None:
        raise CheongjuContractError(
            f"course {key.token} has unmapped education type {category_code!r}"
        )
    if route.name != category_name:
        raise CheongjuContractError(
            f"course {key.token} education type name changed for {category_code}"
        )
    result = item.get("result")
    if not isinstance(result, Mapping) or _clean(result.get("lctreTy")) != "inner":
        raise CheongjuContractError(f"course {key.token} is not internally owned")
    status_code, status_name = _code_name(result.get("lctreSttus"), "lctreSttus")
    if status_code != scope.code or status_name != scope.name:
        raise CheongjuContractError(f"course {key.token} status/scope mismatch")
    start = _iso_date(item.get("beginDe"), "beginDe")
    end = _iso_date(item.get("endDe"), "endDe")
    apply_start = _iso_date(item.get("rceptBeginDe"), "rceptBeginDe")
    apply_end = _iso_date(item.get("rceptEndDe"), "rceptEndDe")
    if end < start or apply_end < apply_start:
        raise CheongjuContractError(f"course {key.token} has a reversed date range")
    _time(item.get("beginTm"), "beginTm")
    _time(item.get("endTm"), "endTm")
    _time(item.get("rceptBeginTm"), "rceptBeginTm")
    _time(item.get("rceptEndTm"), "rceptEndTm")
    _integer(item.get("amount"), "amount")
    _integer(item.get("psncpa"), "psncpa")
    _integer(item.get("applyCnt"), "applyCnt")
    return _ListedCourse(
        key=key,
        scope=scope,
        page=page,
        position=position,
        item=item,
        title=title,
        category_code=category_code,
        category_name=category_name,
        start_date=start,
        end_date=end,
        apply_start=apply_start,
        apply_end=apply_end,
    )


def _scope_params(scope: CheongjuStatusScope, page: int) -> dict[str, Any]:
    return {
        "lctreTy": "inner",
        "lctreSttus": scope.code,
        "page": page,
        "size": CHEONGJU_PAGE_SIZE,
    }


def _validate_page(
    payload: Mapping[str, Any],
    scope: CheongjuStatusScope,
    page: int,
) -> tuple[list[Any], int, int]:
    _envelope(payload, endpoint="paging")
    paging = payload.get("paging")
    values = payload.get("dataList")
    if not isinstance(paging, Mapping) or not isinstance(values, list):
        raise CheongjuContractError(f"scope {scope.code} page {page} payload is malformed")
    total_pages = _integer(paging.get("totalPages"), "paging.totalPages")
    total_elements = _integer(paging.get("totalElements"), "paging.totalElements")
    declared_page = _integer(paging.get("page"), "paging.page", positive=True)
    size = _integer(paging.get("size"), "paging.size", positive=True)
    first = paging.get("first")
    last = paging.get("last")
    if size != CHEONGJU_PAGE_SIZE or declared_page != page:
        raise CheongjuContractError(f"scope {scope.code} page declaration changed")
    expected_pages = math.ceil(total_elements / CHEONGJU_PAGE_SIZE)
    if total_pages != expected_pages:
        raise CheongjuContractError(f"scope {scope.code} total page/count mismatch")
    expected_last_page = max(1, total_pages)
    if first is not (page == 1) or last is not (page == expected_last_page):
        raise CheongjuContractError(f"scope {scope.code} first/last flags mismatch")
    if page > expected_last_page:
        raise CheongjuContractError(f"scope {scope.code} returned an out-of-range page")
    if total_pages == 0:
        expected_rows = 0
    elif page < total_pages:
        expected_rows = CHEONGJU_PAGE_SIZE
    else:
        expected_rows = total_elements - CHEONGJU_PAGE_SIZE * (total_pages - 1)
    if len(values) != expected_rows:
        raise CheongjuContractError(
            f"scope {scope.code} page {page} exposes {len(values)} rows, expected {expected_rows}"
        )
    return values, total_pages, total_elements


def _collect_status_snapshot(
    requester: _Requester,
    max_pages: int,
) -> tuple[list[_ListedCourse], dict[str, dict[str, int]]]:
    rows: list[_ListedCourse] = []
    declarations: dict[str, dict[str, int]] = {}
    for scope in CHEONGJU_STATUS_SCOPES:
        payload = requester.get(CHEONGJU_PAGING_URL, _scope_params(scope, 1))
        values, total_pages, total_elements = _validate_page(payload, scope, 1)
        if total_pages > max_pages:
            raise CheongjuContractError(
                f"scope {scope.code} requires {total_pages} pages above max_pages={max_pages}"
            )
        scope_rows: list[_ListedCourse] = [
            _validate_list_item(value, scope, page=1, position=index)
            for index, value in enumerate(values, start=1)
        ]
        for page in range(2, total_pages + 1):
            payload = requester.get(CHEONGJU_PAGING_URL, _scope_params(scope, page))
            page_values, repeated_pages, repeated_total = _validate_page(
                payload, scope, page
            )
            if repeated_pages != total_pages or repeated_total != total_elements:
                raise CheongjuContractError(
                    f"scope {scope.code} declarations changed during pagination"
                )
            scope_rows.extend(
                _validate_list_item(value, scope, page=page, position=index)
                for index, value in enumerate(page_values, start=1)
            )
        if len(scope_rows) != total_elements:
            raise CheongjuContractError(
                f"scope {scope.code} collected {len(scope_rows)} rows, expected {total_elements}"
            )
        keys = [row.key for row in scope_rows]
        if len(keys) != len(set(keys)):
            raise CheongjuContractError(f"scope {scope.code} has duplicate composite IDs")
        rows.extend(scope_rows)
        declarations[scope.code] = {
            "total_pages": total_pages,
            "total_elements": total_elements,
            "requests": max(1, total_pages),
        }
    all_keys = [row.key for row in rows]
    if len(all_keys) != len(set(all_keys)):
        raise CheongjuContractError("status scopes overlap on a composite course ID")
    return rows, declarations


def _snapshot_fingerprint(rows: Iterable[_ListedCourse]) -> str:
    values: list[dict[str, Any]] = []
    for row in rows:
        item = row.item
        result = item.get("result") if isinstance(item.get("result"), Mapping) else {}
        values.append(
            {
                "key": row.key.token,
                "scope": row.scope.code,
                "page": row.page,
                "position": row.position,
                "title": row.title,
                "category": row.category_code,
                "status": _clean((result.get("lctreSttus") or {}).get("code"))
                if isinstance(result.get("lctreSttus"), Mapping)
                else "",
                "period": [
                    _clean(item.get("beginDe")),
                    _clean(item.get("endDe")),
                    _clean(item.get("beginTm")),
                    _clean(item.get("endTm")),
                ],
                "apply": [
                    _clean(item.get("rceptBeginDe")),
                    _clean(item.get("rceptEndDe")),
                    _clean(item.get("rceptBeginTm")),
                    _clean(item.get("rceptEndTm")),
                ],
                "amount": item.get("amount"),
                "capacity": item.get("psncpa"),
            }
        )
    encoded = json.dumps(
        sorted(values, key=lambda value: (value["scope"], value["page"], value["position"])),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _category_url(record: _ListedCourse) -> str:
    route = CHEONGJU_CATEGORY_ROUTES[record.category_code]
    base = f"https://{CHEONGJU_HOST}{route.path}"
    if not route.supports_course_fragment:
        return base
    key = record.key
    if not key.batch_type and key.additional_order == 0:
        return f"{base}#{key.lecture_seq}"
    if key.batch_type == "adit" and key.additional_order > 0:
        return f"{base}#{key.lecture_seq}!adit{key.additional_order}"
    raise CheongjuContractError(
        f"course {key.token} has no audited public fragment shape"
    )


def cheongju_course_url(
    education_type_code: str,
    lecture_seq: int,
    batch_type: str = "",
    additional_order: int = 0,
) -> str:
    """Build the audited public category/detail URL for a composite identity."""

    route = CHEONGJU_CATEGORY_ROUTES.get(_clean(education_type_code))
    if route is None:
        return ""
    try:
        key = CheongjuCourseKey(
            _integer(lecture_seq, "lecture_seq", positive=True),
            _clean(batch_type),
            _integer(additional_order, "additional_order"),
        )
    except CheongjuContractError:
        return ""
    if not _BATCH_RE.fullmatch(key.batch_type):
        return ""
    base = f"https://{CHEONGJU_HOST}{route.path}"
    if not route.supports_course_fragment:
        return base
    if not key.batch_type and key.additional_order == 0:
        return f"{base}#{key.lecture_seq}"
    if key.batch_type == "adit" and key.additional_order > 0:
        return f"{base}#{key.lecture_seq}!adit{key.additional_order}"
    return ""


def _html_text(value: Any) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    return _clean(BeautifulSoup(str(value), "lxml").get_text(" ", strip=True))


def _optional_code_name(value: Any) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        return "", ""
    return (
        _clean(value.get("code") or value.get("value")),
        _clean(value.get("name") or value.get("valueName")),
    )


def _detail_data(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    _envelope(payload, endpoint="detail")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise CheongjuContractError("detail data is not an object")
    return data


def _validate_detail(record: _ListedCourse, detail: Mapping[str, Any]) -> None:
    key = _course_key(detail)
    if key != record.key:
        raise CheongjuContractError(f"course {record.key.token} detail identity mismatch")
    if _clean(detail.get("lctreNm")) != record.title:
        raise CheongjuContractError(f"course {record.key.token} detail title mismatch")
    expected = {
        "beginDe": _clean(record.item.get("beginDe")),
        "endDe": _clean(record.item.get("endDe")),
        "beginTm": _clean(record.item.get("beginTm")),
        "endTm": _clean(record.item.get("endTm")),
        "rceptBeginDe": _clean(record.item.get("rceptBeginDe")),
        "rceptEndDe": _clean(record.item.get("rceptEndDe")),
        "rceptBeginTm": _clean(record.item.get("rceptBeginTm")),
        "rceptEndTm": _clean(record.item.get("rceptEndTm")),
    }
    for field, value in expected.items():
        if _clean(detail.get(field)) != value:
            raise CheongjuContractError(
                f"course {record.key.token} detail/list {field} mismatch"
            )
    status_code, status_name = _code_name(detail.get("lctreStep"), "lctreStep")
    if status_code != record.scope.code or status_name != record.scope.name:
        raise CheongjuContractError(f"course {record.key.token} detail status mismatch")
    if _integer(detail.get("amount"), "detail.amount") != _integer(
        record.item.get("amount"), "list.amount"
    ):
        raise CheongjuContractError(f"course {record.key.token} fee mismatch")
    if _integer(detail.get("psncpa"), "detail.psncpa") != _integer(
        record.item.get("psncpa"), "list.psncpa"
    ):
        raise CheongjuContractError(f"course {record.key.token} capacity mismatch")
    _integer(detail.get("applyCnt"), "detail.applyCnt")
    _time(detail.get("beginTm"), "detail.beginTm")
    _time(detail.get("endTm"), "detail.endTm")
    _time(detail.get("rceptBeginTm"), "detail.rceptBeginTm")
    _time(detail.get("rceptEndTm"), "detail.rceptEndTm")


def _schedule_dates(
    payload: Mapping[str, Any],
    record: _ListedCourse,
) -> list[str]:
    _envelope(payload, endpoint="schedule")
    values = payload.get("dataList")
    if not isinstance(values, list) or not values:
        raise CheongjuContractError(
            f"course {record.key.token} schedule is empty or malformed"
        )
    dates: list[date] = []
    schedule_ids: set[int] = set()
    for index, value in enumerate(values, start=1):
        if not isinstance(value, Mapping):
            raise CheongjuContractError(
                f"course {record.key.token} schedule row is malformed"
            )
        schedule_id = _integer(
            value.get("schdulSeq"), f"schedule[{index}].schdulSeq", positive=True
        )
        if schedule_id in schedule_ids:
            raise CheongjuContractError(
                f"course {record.key.token} schedule row identity is duplicated"
            )
        schedule_ids.add(schedule_id)
        target_lecture = _integer(
            value.get("trgetLctre"), f"schedule[{index}].trgetLctre", positive=True
        )
        if target_lecture != record.key.lecture_seq:
            raise CheongjuContractError(
                f"course {record.key.token} schedule target identity mismatches"
            )
        current = _iso_date(value.get("edcDe"), "schedule.edcDe")
        dates.append(current)
    # The official API can expose legitimate timetable rows just outside the
    # declared lecture period (live row 6974 starts three days before beginDe).
    # Preserve every identified schedule row and report that source anomaly;
    # rejecting or clipping it would make the required timetable incomplete.
    return [value.isoformat() for value in sorted(dates)]


def _branch(record: _ListedCourse, detail: Mapping[str, Any]) -> str:
    center = _clean(detail.get("cnterNm") or record.item.get("cnterNm"))
    if center:
        return center
    list_result = record.item.get("result")
    list_institution = (
        _clean(list_result.get("insttNm")) if isinstance(list_result, Mapping) else ""
    )
    if list_institution and list_institution != "외부":
        return list_institution
    institution = detail.get("edcInstt")
    institution_name = (
        _clean(institution.get("name") or institution.get("valueName"))
        if isinstance(institution, Mapping)
        else ""
    )
    if institution_name and institution_name != "외부":
        return institution_name
    room = detail.get("lctrum")
    room_section = (
        _clean((room.get("lctrumSe") or {}).get("name"))
        if isinstance(room, Mapping) and isinstance(room.get("lctrumSe"), Mapping)
        else ""
    )
    if room_section == "본관":
        return "평생학습관 - 본관"
    if room_section == "상당분관":
        return "평생학습관 - 상당분관"
    venue = _clean(detail.get("lctrumCenter"))
    if "충북대학교" in venue:
        return "충북대학교"
    if "영운동 어울림센터" in record.title:
        return "영운동 어울림센터"
    if "본관" in record.title:
        return "평생학습관 - 본관"
    return _clean(_target_value(record.item, "branch")) or CHEONGJU_MUNICIPALITY_NAME


def _municipality(
    record: _ListedCourse,
    detail: Mapping[str, Any],
    branch: str,
) -> tuple[str, str]:
    evidence = " ".join(
        (
            branch,
            _clean(detail.get("cnterNm")),
            _clean(detail.get("lctrumCenter")),
            _clean((detail.get("edcInstt") or {}).get("etc"))
            if isinstance(detail.get("edcInstt"), Mapping)
            else "",
            _clean((detail.get("lctrum") or {}).get("lctrumNm"))
            if isinstance(detail.get("lctrum"), Mapping)
            else "",
            record.title,
        )
    )
    # Official row 7043 currently labels 내수평생학습센터 as 상당구.  The
    # public centre page identifies 내수 as 청원구, so exact centre evidence
    # takes precedence over the row's inconsistent area code.
    if any(token in evidence for token in ("내수", "오창")):
        code = "4311400000"
    elif "충북대학교" in evidence:
        code = "4311200000"
    elif "흥덕구" in evidence or "평생학습관 - 본관" in evidence:
        code = "4311300000"
    elif "상당구" in evidence or "상당분관" in evidence or "영운동" in evidence:
        code = "4311100000"
    elif "서원구" in evidence:
        code = "4311200000"
    elif "청원구" in evidence:
        code = "4311400000"
    else:
        area_code, _area_name = _optional_code_name(
            detail.get("area") or record.item.get("area")
        )
        code = f"{area_code}00000" if re.fullmatch(r"4311[1-4]", area_code) else ""
        if code not in CHEONGJU_MUNICIPALITY_NAMES:
            code = CHEONGJU_MUNICIPALITY_CODE
    return code, CHEONGJU_MUNICIPALITY_NAMES[code]


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"{CHEONGJU_PROVIDER}:BRANCH:{digest}"[:100]


def _instructor_names(detail: Mapping[str, Any]) -> str:
    result: list[str] = []
    values = detail.get("instrctrs")
    if values is None:
        values = []
    if not isinstance(values, list):
        raise CheongjuContractError("instrctrs is not a list")
    for value in values:
        if not isinstance(value, Mapping):
            raise CheongjuContractError("instructor row is malformed")
        name = _clean(value.get("instrctrNm"))
        if not name and isinstance(value.get("instrctrCode"), Mapping):
            name = _clean(
                value["instrctrCode"].get("name")
                or value["instrctrCode"].get("valueName")
            )
        name = _clean(sanitize_cheongju_payload(name))
        if name and name not in result:
            result.append(name)
    return ", ".join(result)


def _course_target(detail: Mapping[str, Any]) -> str:
    explicit = _clean(detail.get("edcTrget"))
    if explicit:
        return explicit
    minimum = detail.get("ageMin")
    maximum = detail.get("ageMax")
    minimum_age = (
        minimum if isinstance(minimum, int) and not isinstance(minimum, bool) and minimum > 0 else None
    )
    maximum_age = (
        maximum if isinstance(maximum, int) and not isinstance(maximum, bool) and maximum > 0 else None
    )
    if minimum_age is not None and maximum_age is not None:
        return f"{minimum_age}세 이상 ~ {maximum_age}세 이하"
    if minimum_age is not None:
        return f"{minimum_age}세 이상"
    if maximum_age is not None:
        return f"{maximum_age}세 이하"
    return ""


def _row_from_detail(
    target: Any,
    record: _ListedCourse,
    detail: Mapping[str, Any],
    schedule_dates: list[str],
) -> dict[str, Any]:
    _validate_detail(record, detail)
    raw_url = _category_url(record)
    route = CHEONGJU_CATEGORY_ROUTES[record.category_code]
    application_url = (
        raw_url
        if record.scope.normalized_status == "OPEN" and route.supports_course_fragment
        else ""
    )
    branch = _branch(record, detail)
    municipality_code, municipality_name = _municipality(record, detail, branch)
    institution = detail.get("edcInstt") if isinstance(detail.get("edcInstt"), Mapping) else {}
    room = detail.get("lctrum") if isinstance(detail.get("lctrum"), Mapping) else {}
    venue_name = _clean(
        detail.get("lctrumCenter")
        or room.get("lctrumNm")
        or institution.get("name")
        or branch
    )
    venue_address = _clean(institution.get("etc")) or CHEONGJU_BRANCH_ADDRESSES.get(
        branch, ""
    )
    day_code, day_name = _optional_code_name(detail.get("edcDow"))
    begin_time = _time(detail.get("beginTm"), "detail.beginTm")
    end_time = _time(detail.get("endTm"), "detail.endTm")
    schedule_raw = _clean(
        f"{day_name or day_code} {begin_time}~{end_time}"
    )
    description = _html_text(detail.get("referMatter"))
    description_source = "detail_refer_matter"
    if not description:
        description_source = "generated_from_validated_detail"
        description = _clean(
            " | ".join(
                value
                for value in (
                    record.title,
                    record.category_name,
                    branch,
                    f"{record.start_date.isoformat()} ~ {record.end_date.isoformat()}",
                    schedule_raw,
                    _clean(detail.get("edcTrget")),
                )
                if value
            )
        )
    description = _clean(sanitize_cheongju_payload(description))
    selection_code, selection_name = _optional_code_name(detail.get("slctnMthd"))
    institution_seq = institution.get("seq")
    room_seq = room.get("lctrumSeq") or room.get("seq")
    business_code, business_name = _optional_code_name(
        detail.get("bsnsTy") or record.item.get("bsnsTy")
    )
    area_code, area_name = _optional_code_name(
        detail.get("area") or record.item.get("area")
    )
    course_target = _course_target(detail)
    apply_current = _integer(detail.get("applyCnt"), "detail.applyCnt")
    capacity_total = _integer(detail.get("psncpa"), "detail.psncpa")
    fee = _integer(detail.get("amount"), "detail.amount")
    material_fee = (
        _integer(detail.get("matrlCt"), "detail.matrlCt")
        if isinstance(detail.get("matrlCt"), int)
        else None
    )
    fee_text = "무료" if fee == 0 else f"{fee:,}원"
    material_fee_text = (
        "무료" if material_fee == 0 else f"{material_fee:,}원"
        if material_fee is not None
        else None
    )
    provider = _provider(target)
    raw_fields = sanitize_cheongju_payload(
        {
            "parser": CHEONGJU_PARSER,
            "candidate_id": CHEONGJU_CANDIDATE_ID,
            "composite_identity": record.key.token,
            "lecture_seq": record.key.lecture_seq,
            "batch_type": record.key.batch_type,
            "additional_order": record.key.additional_order,
            "source_scope": record.scope.code,
            "source_status_code": record.scope.code,
            "source_status_name": record.scope.name,
            "education_type_code": record.category_code,
            "education_type_name": record.category_name,
            "area_code": area_code,
            "area_name": area_name,
            "business_type_code": business_code,
            "business_type_name": business_name,
            "selection_method_code": selection_code,
            "selection_method_name": selection_name,
            "institution_seq": institution_seq,
            "institution_name": _clean(institution.get("name")),
            "room_seq": room_seq,
            "room_name": _clean(room.get("lctrumNm")),
            "list_page": record.page,
            "schedule_count": len(schedule_dates),
            "schedule_period_anomaly_count": sum(
                not record.start_date <= date.fromisoformat(value) <= record.end_date
                for value in schedule_dates
            ),
            "fee_amount": fee,
            "material_fee_amount": material_fee,
            "target_age_min": detail.get("ageMin")
            if isinstance(detail.get("ageMin"), int)
            and not isinstance(detail.get("ageMin"), bool)
            else None,
            "target_age_max": detail.get("ageMax")
            if isinstance(detail.get("ageMax"), int)
            and not isinstance(detail.get("ageMax"), bool)
            else None,
            "description_source": description_source,
        }
    )
    return {
        "provider": provider,
        "provider_course_id": (
            f"{provider}:lctre:{record.key.token}"[:100]
        ),
        "prefer_incoming_provider_course_id": True,
        "title": record.title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "branch_url": CHEONGJU_URL,
        "category": record.category_name,
        "program_type": "강좌",
        "raw_url": raw_url,
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if application_url else "INFO_ONLY",
        "application_method_raw": selection_name,
        "reservation_available": bool(application_url),
        "status": record.scope.normalized_status,
        "fee": fee_text,
        "material_fee": material_fee_text,
        "period": f"{record.start_date.isoformat()} ~ {record.end_date.isoformat()}",
        "start_date": record.start_date.isoformat(),
        "end_date": record.end_date.isoformat(),
        "apply_period": f"{record.apply_start.isoformat()} {_time(detail.get('rceptBeginTm'), 'detail.rceptBeginTm')} ~ {record.apply_end.isoformat()} {_time(detail.get('rceptEndTm'), 'detail.rceptEndTm')}",
        "apply_start": record.apply_start.isoformat(),
        "apply_end": record.apply_end.isoformat(),
        "schedule_raw": schedule_raw,
        "schedule_dates": schedule_dates,
        "sessions": len(schedule_dates),
        "target": course_target,
        "instructor": _instructor_names(detail),
        "capacity": f"{apply_current}/{capacity_total}",
        "capacity_current": apply_current,
        "capacity_total": capacity_total,
        "venue_name": venue_name,
        "venue_address": venue_address,
        "address": venue_address,
        "description": description,
        "collection_category": "평생학습",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": CHEONGJU_PARSER,
        "municipality_code": municipality_code,
        "municipality_full_name": municipality_name,
        "raw_fields": raw_fields,
    }


def _detail_bundle(
    target: Any,
    record: _ListedCourse,
    *,
    session_factory: SessionFactory,
    json_getter: JsonGetter,
    timeout: int,
) -> tuple[Optional[dict[str, Any]], int, int, str]:
    session = session_factory()
    calls = 0
    schedule_calls = 0
    try:
        calls += 1
        detail_payload = _coerce_json(
            json_getter(session, CHEONGJU_DETAIL_URL, record.key.detail_params, timeout)
        )
        detail = _detail_data(detail_payload)
        _validate_detail(record, detail)
        calls += 1
        schedule_calls += 1
        schedule_payload = _coerce_json(
            json_getter(
                session,
                CHEONGJU_SCHEDULE_URL,
                {"trgetLctre": record.key.lecture_seq},
                timeout,
            )
        )
        dates = _schedule_dates(schedule_payload, record)
        return _row_from_detail(target, record, detail, dates), calls, schedule_calls, ""
    except Exception as exc:
        return (
            None,
            calls,
            schedule_calls,
            f"course {record.key.token}: {type(exc).__name__}: {_clean(exc)}",
        )
    finally:
        _close_quietly(session)


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str, **extra: Any) -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "list_requests": 0,
        "list_recheck_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "schedule_attempts": 0,
        "schedule_pages": 0,
        "source_total": 0,
        "unique_id_count": 0,
        "composite_id_count": 0,
        "duplicate_count": 0,
        "expired_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "schedule_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "canonical_provider": CHEONGJU_PROVIDER,
        "canonical_url": CHEONGJU_URL,
        "ownership_aliases": list(CHEONGJU_OWNERSHIP_ALIAS_URLS),
        "covered_municipalities": [dict(item) for item in CHEONGJU_COVERED_MUNICIPALITIES],
        "configured_collection_error": message,
        **extra,
    }


def collect_cheongju_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 1200,
    detail_limit: int = 1200,
    *,
    json_getter: Optional[JsonGetter] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 6,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future internal Cheongju snapshot."""

    alias = cheongju_alias_for_target(target)
    if alias is not None:
        metadata = cheongju_alias_metadata(target)
        return [], CHEONGJU_PARSER, _failure(
            "non-executing duplicate alias; use the canonical P0401 provider",
            **metadata,
        )
    if not is_cheongju_target(target):
        return [], CHEONGJU_PARSER, _failure(
            "target does not match the exact reviewed Cheongju P0401 provider"
        )
    try:
        page_cap = int(max_pages)
        detail_cap = int(detail_limit)
        workers = max(1, min(CHEONGJU_MAX_WORKERS, int(max_workers)))
        cutoff = _today(today)
    except (TypeError, ValueError, CheongjuContractError) as exc:
        return [], CHEONGJU_PARSER, _failure(
            f"invalid collection arguments: {type(exc).__name__}: {_clean(exc)}"
        )
    if page_cap < 1 or detail_cap < 0:
        return [], CHEONGJU_PARSER, _failure(
            "collection caps are invalid", source_cap_reached=True
        )

    current_session_factory = session_factory or _default_session_factory
    current_json_getter = json_getter or _default_json_getter
    current_dedupe = dedupe_rows or _dedupe_default
    requester = _Requester(current_session_factory, current_json_getter, timeout)
    initial_rows: list[_ListedCourse] = []
    declarations: dict[str, dict[str, int]] = {}
    recheck_declarations: dict[str, dict[str, int]] = {}
    recheck_rows: list[_ListedCourse] = []
    list_requests = 0
    list_recheck_requests = 0
    detail_attempts = 0
    detail_pages = 0
    schedule_attempts = 0
    schedule_pages = 0
    detail_request_calls = 0
    errors: list[str] = []
    source_cap_reached = False
    rows: list[dict[str, Any]] = []

    try:
        try:
            initial_rows, declarations = _collect_status_snapshot(requester, page_cap)
            list_requests = requester.calls
        except Exception as exc:
            errors.append(f"initial status snapshot: {type(exc).__name__}: {_clean(exc)}")
            list_requests = requester.calls
        if errors:
            return [], CHEONGJU_PARSER, _failure(
                "; ".join(errors),
                pages=sum(value.get("total_pages", 0) for value in declarations.values()),
                request_count=requester.calls,
                list_requests=list_requests,
                source_cap_reached="max_pages" in errors[0],
            )

        identities = [record.key for record in initial_rows]
        duplicate_count = len(identities) - len(set(identities))
        current_records = [record for record in initial_rows if record.end_date >= cutoff]
        expired_count = len(initial_rows) - len(current_records)
        if duplicate_count:
            errors.append(f"initial snapshot has {duplicate_count} duplicate composite IDs")
        if detail_cap < len(current_records):
            source_cap_reached = True
            errors.append(
                f"detail_limit={detail_cap} is below required current rows={len(current_records)}"
            )

        if not errors and current_records:
            detail_attempts = len(current_records)
            with ThreadPoolExecutor(
                max_workers=min(workers, len(current_records)),
                thread_name_prefix="cheongju-detail",
            ) as pool:
                futures = {
                    pool.submit(
                        _detail_bundle,
                        target,
                        record,
                        session_factory=current_session_factory,
                        json_getter=current_json_getter,
                        timeout=timeout,
                    ): record
                    for record in current_records
                }
                indexed: dict[CheongjuCourseKey, dict[str, Any]] = {}
                for future in as_completed(futures):
                    record = futures[future]
                    try:
                        row, calls, schedule_calls, error = future.result()
                    except Exception as exc:
                        row, calls, schedule_calls, error = (
                            None,
                            0,
                            0,
                            f"course {record.key.token}: {type(exc).__name__}: {_clean(exc)}",
                        )
                    detail_request_calls += calls
                    schedule_attempts += schedule_calls
                    if error:
                        errors.append(error)
                    elif row is not None:
                        indexed[record.key] = row
                        detail_pages += 1
                        schedule_pages += 1
                rows = [indexed[record.key] for record in current_records if record.key in indexed]

        try:
            before = requester.calls
            recheck_rows, recheck_declarations = _collect_status_snapshot(
                requester, page_cap
            )
            list_recheck_requests = requester.calls - before
            if declarations != recheck_declarations:
                raise CheongjuContractError("status declarations changed during traversal")
            if _snapshot_fingerprint(initial_rows) != _snapshot_fingerprint(recheck_rows):
                raise CheongjuContractError("status rows changed during traversal")
        except Exception as exc:
            errors.append(f"status snapshot recheck: {type(exc).__name__}: {_clean(exc)}")

        if not errors:
            try:
                deduped = list(current_dedupe(rows))
            except Exception as exc:
                errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                deduped = []
            if len(deduped) != len(rows):
                errors.append(
                    f"dedupe changed complete row count {len(rows)} to {len(deduped)}"
                )
            rows = deduped

        pagination_complete = (
            not any("snapshot" in error and "detail" not in error for error in errors)
            and declarations == recheck_declarations
            and bool(recheck_declarations)
        )
        details_complete = (
            not source_cap_reached
            and detail_attempts == len(current_records)
            and detail_pages == len(current_records)
        )
        schedule_complete = (
            not source_cap_reached
            and schedule_attempts == len(current_records)
            and schedule_pages == len(current_records)
        )
        snapshot_complete = (
            not errors
            and duplicate_count == 0
            and pagination_complete
            and details_complete
            and schedule_complete
            and len(rows) == len(current_records)
        )
        if not snapshot_complete:
            rows = []

        status_source_counts = Counter(record.scope.name for record in current_records)
        normalized_status_counts = Counter(
            record.scope.normalized_status for record in current_records
        )
        category_counts = Counter(record.category_name for record in current_records)
        branch_counts = Counter(row.get("branch") for row in rows)
        municipality_counts = Counter(row.get("municipality_code") for row in rows)
        schedule_period_anomaly_count = sum(
            int((row.get("raw_fields") or {}).get("schedule_period_anomaly_count") or 0)
            for row in rows
        )
        request_count = requester.calls + detail_request_calls
        source_total = len(initial_rows)
        meta: dict[str, Any] = {
            "pages": sum(value["total_pages"] for value in declarations.values()),
            "request_count": request_count,
            "list_requests": list_requests,
            "list_recheck_requests": list_recheck_requests,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "schedule_attempts": schedule_attempts,
            "schedule_pages": schedule_pages,
            "source_total": source_total,
            "unique_id_count": len(set(identities)),
            "composite_id_count": len(set(identities)),
            "duplicate_count": duplicate_count,
            "expired_count": expired_count,
            "current_count": len(current_records),
            "returned_count": len(rows),
            "declared_status_scopes": declarations,
            "status_source_counts": dict(status_source_counts),
            "normalized_status_counts": dict(normalized_status_counts),
            "education_type_counts": dict(category_counts),
            "branch_counts": dict(branch_counts),
            "current_municipality_counts": dict(municipality_counts),
            "schedule_period_anomaly_count": schedule_period_anomaly_count,
            "pagination_detected": any(
                value["total_pages"] > 1 for value in declarations.values()
            ),
            "pagination_complete": pagination_complete,
            "pagination_exhausted": pagination_complete,
            "details_complete": details_complete,
            "schedule_complete": schedule_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "network_concurrency": workers,
            "no_current_data": snapshot_complete and not current_records,
            "no_current_reason": (
                "all four complete internal lifecycle scopes contain no current/future education"
                if snapshot_complete and not current_records
                else ""
            ),
            "canonical_provider": CHEONGJU_PROVIDER,
            "canonical_url": CHEONGJU_URL,
            "ownership_scope": "cheongju_all_internal_active_state_current_future_education",
            "ownership_aliases": list(CHEONGJU_OWNERSHIP_ALIAS_URLS),
            "non_executing_alias_providers": [
                item.provider for item in CHEONGJU_NON_EXECUTING_ALIASES
            ],
            "covered_municipalities": [
                dict(item) for item in CHEONGJU_COVERED_MUNICIPALITIES
            ],
            "external_catalogue_rows_included": 0,
            "pii_payload_persisted": False,
        }
        if errors:
            meta["configured_collection_error"] = "; ".join(
                dict.fromkeys(errors)
            )
        return rows, CHEONGJU_PARSER, meta
    finally:
        requester.close()


collect_cheongju_target = collect_cheongju_education_courses
collect = collect_cheongju_education_courses


__all__ = [
    "CHEONGJU_API_ROOT",
    "CHEONGJU_CANDIDATE_ID",
    "CHEONGJU_CATEGORY_ROUTES",
    "CHEONGJU_COVERED_MUNICIPALITIES",
    "CHEONGJU_DETAIL_URL",
    "CHEONGJU_HOST",
    "CHEONGJU_MAX_WORKERS",
    "CHEONGJU_MUNICIPALITY_CODE",
    "CHEONGJU_MUNICIPALITY_NAME",
    "CHEONGJU_NON_EXECUTING_ALIASES",
    "CHEONGJU_OWNERSHIP_ALIAS_URLS",
    "CHEONGJU_PAGE_SIZE",
    "CHEONGJU_PAGING_URL",
    "CHEONGJU_PARSER",
    "CHEONGJU_PATH",
    "CHEONGJU_PROVIDER",
    "CHEONGJU_SCHEDULE_URL",
    "CHEONGJU_STATUS_SCOPES",
    "CHEONGJU_URL",
    "CheongjuAlias",
    "CheongjuCategoryRoute",
    "CheongjuContractError",
    "CheongjuCourseKey",
    "CheongjuStatusScope",
    "cheongju_alias_for_target",
    "cheongju_alias_metadata",
    "cheongju_course_url",
    "collect",
    "collect_cheongju_education_courses",
    "collect_cheongju_target",
    "is_cheongju_alias_target",
    "is_cheongju_source_target",
    "is_cheongju_target",
    "is_target",
    "sanitize_cheongju_payload",
]
