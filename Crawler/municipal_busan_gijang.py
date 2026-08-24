"""Atomic education collector for Busan Gijang-gun.

The canonical district owner is Gijang's complete online-course archive.  Four
censuses of 부산평생학습 office ``OFFICE_00002631`` are used only to prove
and suppress exact district ``idx`` republications while retaining future
native ``LEARNING_*`` courses.  부산광역시 통합예약 is restricted to the exact
Gijang resident-council partition (``srchGugun=3`` and office ``33``).

Every advertised page, the immediate empty sentinel, stable boundaries and
all current/future safe details are mandatory.  Application forms, applicant
lists, instructor/contact values, enrolment values, attachments and free-form
descriptions are never fetched or read.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from Crawler import municipal_busan_lifelong as _lifelong
from utils.outbound_http import DEFAULT_MAX_RESPONSE_BYTES, SafeSession


BUSAN_GIJANG_PROVIDER = "MUNI_WWW_GIJANG_GO_KR_592C4B5E"
BUSAN_GIJANG_CANDIDATE_ID = "MUNI_IR_AC76FCFD5281"
BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER
BUSAN_GIJANG_MUNICIPALITY_CODE = "2671000000"
BUSAN_GIJANG_MUNICIPALITY_NAME = "부산광역시 기장군"

BUSAN_GIJANG_HOST = "www.gijang.go.kr"
BUSAN_GIJANG_PATH = "/lll/index.gijang"
BUSAN_GIJANG_MENU = "DOM_000000702008000000"
BUSAN_GIJANG_CANONICAL_URL = (
    f"https://{BUSAN_GIJANG_HOST}{BUSAN_GIJANG_PATH}?"
    + urlencode({"menuCd": BUSAN_GIJANG_MENU})
)
BUSAN_GIJANG_URL = BUSAN_GIJANG_CANONICAL_URL
BUSAN_GIJANG_PAGE_SIZE = 10

BUSAN_LIFELONG_GIJANG_OFFICE = "OFFICE_00002631"
BUSAN_LIFELONG_GIJANG_OFFICE_NAME = "기장군청"
# Managed production sessions keep the default 8 MiB body cap and scope only
# the total timeout to 120 seconds.  At 950
# rows the largest audited response is 7,844,210 bytes.  The upstream query is
# not deterministically ordered at page boundaries, so two page-unit variants
# are reconciled twice below instead of trusting any single page pair.
BUSAN_LIFELONG_PAGE_UNITS = (900, 950)
BUSAN_LIFELONG_PAGE_SIZE = max(BUSAN_LIFELONG_PAGE_UNITS)

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_GIJANG_GUGUN = "3"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_GIJANG_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_GIJANG_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)

BUSAN_GIJANG_FETCH_ATTEMPTS = 3
BUSAN_GIJANG_MAX_WORKERS = 20
BUSAN_GIJANG_MAX_HTML_BYTES = DEFAULT_MAX_RESPONSE_BYTES
BUSAN_GIJANG_PARSER = (
    "gijang_complete_online_courses_169_pages+empty_sentinel+stable_boundaries+"
    "current_detail_strict_allowlist+lifelong_office00002631_pageunit900_950_four_censuses+"
    "two_independent_union_signatures+sequence_complete+exact_external_idx_normalized_period_suppression+"
    "native_learning_preservation+busan_city_gugun3_office33_complete+"
    "pii_never_read+atomic_three_ledger_snapshot"
)
BUSAN_GIJANG_OWNERSHIP_SCOPE = (
    "gijang_complete_district_lifelong_native_platform_and_exact_resident_council_education"
)

BUSAN_GIJANG_CANDIDATE_IDS: Mapping[str, str] = {
    "canonical_complete_owner": BUSAN_GIJANG_CANDIDATE_ID,
    "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
    "busan_resident_councils": "MUNI_IR_6E08DDCBB806",
}

BUSAN_GIJANG_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_GIJANG_PROVIDER: {
        "decision": "retain_complete_district_owner",
        "candidate_id": BUSAN_GIJANG_CANDIDATE_ID,
        "canonical_url": BUSAN_GIJANG_CANONICAL_URL,
        "identity_rule": "exact numeric idx",
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "suppress_exact_external_idx_duplicates_keep_native_learning_ids",
        "candidate_id": BUSAN_GIJANG_CANDIDATE_IDS["busan_lifelong_federation"],
        "office_code": BUSAN_LIFELONG_GIJANG_OFFICE,
        "identity_rule": "external Gijang idx plus title and normalized periods",
    },
    "OFFICIAL_BUSAN_CITY_RESIDENT_RESERVATION": {
        "decision": "collect_exact_gijang_resident_council_partition",
        "url": BUSAN_CITY_GIJANG_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_GIJANG_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
    },
    "PRIVATE_BOUNDARY": {
        "decision": "never_fetch_or_read",
        "reason": (
            "application/account pages, instructor/contact/enrolment values, "
            "attachments and free-form detail values can contain PII"
        ),
    },
}

# Exact training records are counted in the source census but are not public
# education.  Title binding prevents an identity from being silently reused.
_AUDITED_LOCAL_NON_COURSES: Mapping[str, tuple[str, str]] = {
    "2021": ("[테스트]수강신청방법 연습", "application_training_test"),
    "1894": ("[테스트]수강신청방법 연습", "application_training_test"),
    "1847": ("수강신청 테스트(연습용)", "application_training_test"),
}
_AUDITED_NATIVE_NON_COURSES: Mapping[str, tuple[str, str]] = {
    "LEARNING_00087619": ("테스트1", "federation_training_test"),
}

BUSAN_GIJANG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "district_rows": 1690,
    "district_data_pages": 169,
    "district_page_size": 10,
    "district_sentinel_page": 170,
    "district_unique_ids": 1690,
    "district_status_counts": {"교육완료": 1678, "접수중": 7, "접수마감": 5},
    "district_reversed_education_period_rows": 1,
    "district_reversed_application_period_rows": 4,
    "district_current_rows": 12,
    "district_current_publishable_rows": 10,
    "platform_rows": 1687,
    "platform_data_pages": 2,
    "platform_external_rows": 1686,
    "platform_external_unique_idx": 1686,
    "platform_native_rows": 1,
    "platform_current_external_rows": 8,
    "platform_current_native_rows": 1,
    "platform_audited_native_non_course_rows": 1,
    "resident_rows": 0,
    "resident_data_pages": 1,
    "atomic_current_rows": 10,
    "platform_raw_semantic_censuses": 4,
    "platform_reconciled_pairwise_signatures": 2,
    "required_list_requests": 187,
    "required_detail_requests": 10,
    "complete_network_requests": 197,
}


class BusanGijangContractError(ValueError):
    """Raised when an audited Gijang source contract changes."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Parser = Callable[[BeautifulSoup, str], Any]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)(?:0\d{1,2}[- ]?)?\d{3,4}[- ]\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LOCAL_STATUS_MAP = {"접수중": "OPEN", "접수마감": "CLOSED", "교육완료": "CLOSED"}
_CITY_STATUS_MAP = {"접수중": "OPEN", "대기중": "SCHEDULED", "대기접수": "OPEN", "접수마감": "CLOSED"}
_CITY_ACTION_RE = re.compile(
    r"fn_viewProgrm\(\s*['\"]([0-9]+)['\"]\s*,\s*['\"]([0-9]+)['\"]\s*\)\s*;?\s*return\s+false\s*;?"
)
_LOCAL_LABELS = (
    "신청기간",
    "교육기간",
    "교육장소",
    "모집인원(신청/대기)",
    "접수인원(신청/대기)",
)
_LOCAL_DETAIL_LABELS = (
    "강사명", "수강대상", "교육과정", "강의실", "교육기간", "교육시간",
    "총 교육시간", "요일", "접수방법", "신청상태", "재료비", "수강료",
    "연락처", "접수기간", "모집인원", "접수인원", "강좌소개", "참고사항", "첨부파일",
)
_LOCAL_DETAIL_SAFE = frozenset(
    {"수강대상", "교육과정", "강의실", "교육기간", "교육시간", "요일", "접수방법", "신청상태", "재료비", "수강료", "접수기간"}
)
_PLATFORM_DETAIL_SAFE = frozenset(
    {"교육대상", "교육장소", "교육기간", "교육시간", "수강료", "재료비", "일반모집기간", "모집방법", "신청상태", "교육상태"}
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", html.unescape(str(value or "")).replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True) if node is not None else "")


def _one(values: Sequence[Any], label: str) -> Any:
    if len(values) != 1:
        raise BusanGijangContractError(f"expected one {label}")
    return values[0]


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _normal_path(value: str) -> str:
    return re.sub(r"/{2,}", "/", value or "/")


def _compare_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https" or not parsed.hostname or parsed.port is not None
        or parsed.username or parsed.password or parsed.params or parsed.fragment
    ):
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return f"https://{parsed.hostname.rstrip('.').lower()}{_normal_path(parsed.path)}" + (f"?{query}" if query else "")


def is_busan_gijang_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == BUSAN_GIJANG_PROVIDER
        and _compare_url(_target_value(target, "url")) == _compare_url(BUSAN_GIJANG_CANONICAL_URL)
    )


is_target = is_busan_gijang_education_target


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise BusanGijangContractError(f"invalid {label}")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BusanGijangContractError(f"invalid {label}") from exc
    if result < 1:
        raise BusanGijangContractError(f"invalid {label}")
    return result


def busan_gijang_list_url(page: int = 1) -> str:
    current = _positive_int(page, "district page")
    return f"https://{BUSAN_GIJANG_HOST}{BUSAN_GIJANG_PATH}?" + urlencode(
        (
            ("menuCd", BUSAN_GIJANG_MENU), ("mode", "list"),
            ("pageIndex", current), ("searchCategory", ""),
            ("searchCondition", "LECT_NM"), ("searchKeyword", ""),
        )
    )


def busan_gijang_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not value.isdigit() or int(value) < 1:
        raise BusanGijangContractError("invalid district identity")
    return f"https://{BUSAN_GIJANG_HOST}{BUSAN_GIJANG_PATH}?" + urlencode(
        (("menuCd", BUSAN_GIJANG_MENU), ("idx", value), ("mode", "view"))
    )


def busan_gijang_platform_list_url(
    page: int = 1, page_unit: int = BUSAN_LIFELONG_PAGE_SIZE
) -> str:
    current = _positive_int(page, "platform page")
    unit = _positive_int(page_unit, "platform page unit")
    if unit not in BUSAN_LIFELONG_PAGE_UNITS:
        raise BusanGijangContractError("unaudited platform page unit")
    payload = _lifelong._list_payload(BUSAN_LIFELONG_GIJANG_OFFICE, current)
    payload["pageUnit"] = str(unit)
    return _lifelong.BUSAN_LIFELONG_LIST_URL + "?" + urlencode(payload)


def busan_gijang_city_list_url(page: int = 1) -> str:
    current = _positive_int(page, "city page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (("curPage", current), ("srchGugun", BUSAN_CITY_GIJANG_GUGUN), ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE))
    )


def busan_gijang_city_detail_url(group_id: Any, program_id: Any) -> str:
    group, program = _clean(group_id), _clean(program_id)
    if not group.isdigit() or not program.isdigit():
        raise BusanGijangContractError("invalid city identity")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def canonical_busan_gijang_identity(value: Any) -> str:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https" or (parsed.hostname or "").rstrip(".").lower() != BUSAN_GIJANG_HOST
        or parsed.port is not None or parsed.username or parsed.password or parsed.path != BUSAN_GIJANG_PATH
        or parsed.params or parsed.fragment or set(query) != {"menuCd", "idx", "mode"}
        or query.get("menuCd") != [BUSAN_GIJANG_MENU] or query.get("mode") != ["view"]
        or len(query.get("idx", [])) != 1 or not query["idx"][0].isdigit()
    ):
        return ""
    return query["idx"][0]


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError as exc:
            raise BusanGijangContractError("invalid date") from exc
    return result


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
    })
    return session


def busan_gijang_session_factory() -> SafeSession:
    """Return the managed session with the audited large-response allowance."""

    session = SafeSession(total_timeout_seconds=120)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
    })
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
                raise BusanGijangContractError(f"max_requests cap {self.maximum} exhausted")
            self.count += 1


def _response_soup(response: Any, requested_url: str) -> tuple[BeautifulSoup, str]:
    if isinstance(response, BeautifulSoup):
        return response, requested_url
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError("redirected response")
    final_url = _clean(getattr(response, "url", "")) or requested_url
    requested, final = urlparse(requested_url), urlparse(final_url)
    if (
        final.scheme.lower() != "https" or (final.hostname or "").rstrip(".").lower() != (requested.hostname or "").rstrip(".").lower()
        or final.port is not None or final.username or final.password or _normal_path(final.path) != _normal_path(requested.path)
        or final.params or final.fragment
    ):
        raise ValueError("response URL changed scope")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    size = len(content) if isinstance(content, bytes) else len(str(content).encode("utf-8"))
    if size > BUSAN_GIJANG_MAX_HTML_BYTES:
        raise ValueError("source HTML exceeds safety limit")
    return BeautifulSoup(content, "lxml"), final_url


@dataclass
class _FetchResult:
    value: Any
    retries: int
    sessions: int


def _fetch_parsed(
    url: str, parser: Parser, *, fetcher: Fetcher, session_factory: SessionFactory,
    timeout: int, sleeper: Sleeper, budget: _RequestBudget,
) -> _FetchResult:
    session = session_factory()
    messages: list[str] = []
    try:
        for attempt in range(1, BUSAN_GIJANG_FETCH_ATTEMPTS + 1):
            try:
                budget.take()
                soup, final = _response_soup(fetcher(session, url, timeout), url)
                return _FetchResult(parser(soup, final), attempt - 1, 1)
            except Exception as exc:
                messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
                if attempt < BUSAN_GIJANG_FETCH_ATTEMPTS:
                    sleeper(min(0.25 * attempt, 0.75))
        raise BusanGijangContractError("; ".join(messages))
    finally:
        _close_quietly(session)


def _fetch_many(
    items: Sequence[tuple[Any, str, Parser]], *, fetcher: Fetcher,
    session_factory: SessionFactory, timeout: int, sleeper: Sleeper,
    budget: _RequestBudget, max_workers: int,
) -> tuple[dict[Any, Any], int, int]:
    values: dict[Any, Any] = {}
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

    def one(item: tuple[Any, str, Parser]) -> tuple[Any, Any, int]:
        key, url, parser = item
        messages: list[str] = []
        for attempt in range(1, BUSAN_GIJANG_FETCH_ATTEMPTS + 1):
            try:
                budget.take()
                soup, final = _response_soup(fetcher(thread_session(), url, timeout), url)
                return key, parser(soup, final), attempt - 1
            except Exception as exc:
                messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
                if attempt < BUSAN_GIJANG_FETCH_ATTEMPTS:
                    sleeper(min(0.25 * attempt, 0.75))
        raise BusanGijangContractError("; ".join(messages))

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(one, item): item[0] for item in items}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    result_key, value, retry_count = future.result()
                    values[result_key] = value
                    retries += retry_count
                except Exception as exc:
                    errors.append(f"{key}: {type(exc).__name__}: {_clean(exc)}")
    finally:
        for session in sessions:
            _close_quietly(session)
    if errors:
        raise BusanGijangContractError("; ".join(sorted(errors)))
    return values, retries, len(sessions)


def _exact_query_url(final_url: str, host: str, path: str) -> dict[str, list[str]]:
    parsed = urlparse(final_url)
    if (
        parsed.scheme.lower() != "https" or (parsed.hostname or "").rstrip(".").lower() != host
        or parsed.port is not None or parsed.username or parsed.password or parsed.path != path
        or parsed.params or parsed.fragment
    ):
        raise BusanGijangContractError("response URL left exact owner")
    return parse_qs(parsed.query, keep_blank_values=True)


def _local_last_page(soup: BeautifulSoup) -> int:
    roots = soup.select("div.pagination")
    if len(roots) != 1:
        raise BusanGijangContractError("district pagination changed")
    links = roots[0].select(":scope > a.last01[onclick]")
    if len(links) != 1:
        raise BusanGijangContractError("district final-page control changed")
    match = re.fullmatch(r"linkPage\(\s*([0-9]+)\s*\);\s*return\s+false;?", _clean(links[0].get("onclick")))
    if not match or int(match.group(1)) < 1:
        raise BusanGijangContractError("invalid district final page")
    return int(match.group(1))


def _local_form_contract(soup: BeautifulSoup, final_url: str, page: int) -> None:
    query = _exact_query_url(final_url, BUSAN_GIJANG_HOST, BUSAN_GIJANG_PATH)
    expected = {
        "menuCd": [BUSAN_GIJANG_MENU], "mode": ["list"], "pageIndex": [str(page)],
        "searchCategory": [""], "searchCondition": ["LECT_NM"], "searchKeyword": [""],
    }
    if query != expected:
        raise BusanGijangContractError("district list response query changed")
    if _text(_one(soup.select("title"), "district title")) != "평생학습정보 > 온라인 수강신청":
        raise BusanGijangContractError("district list title changed")
    form = _one(soup.select("form#listForm[name='listForm']"), "district list form")
    if _clean(form.get("method")).casefold() != "get" or _clean(form.get("action")):
        raise BusanGijangContractError("district list form changed")
    required = {key: values[0] for key, values in expected.items()}
    for name, value in required.items():
        field = _one(form.select(f"input[name='{name}']"), f"district {name}")
        if _clean(field.get("value")) != value:
            raise BusanGijangContractError(f"district form {name} changed")


def _local_row(source: Tag, *, page: int, position: int) -> dict[str, Any]:
    category = _text(_one(source.select(":scope > dt > span"), "district category"))
    body = _one(source.find_all("dd", recursive=False), "district card body")
    status = _text(_one(body.select(":scope > div > span"), "district status"))
    if status not in _LOCAL_STATUS_MAP:
        raise BusanGijangContractError(f"unknown district status {status!r}")
    link = _one(body.find_all("a", recursive=False), "district course link")
    identity = canonical_busan_gijang_identity(urljoin(BUSAN_GIJANG_CANONICAL_URL, _clean(link.get("href"))))
    title = _text(_one(link.select(":scope > p.tit"), "district course title"))
    if not identity or not title or not category:
        raise BusanGijangContractError("district course identity/title changed")
    items = _one(link.find_all("ul", recursive=False), "district card values").find_all("li", recursive=False)
    if len(items) != len(_LOCAL_LABELS):
        raise BusanGijangContractError("district card field count changed")
    values: dict[str, str] = {}
    for item, expected_label in zip(items, _LOCAL_LABELS):
        label = _text(_one(item.find_all("b", recursive=False), "district card label"))
        value = _text(_one(item.find_all("span", recursive=False), "district card value"))
        if label != expected_label or not value:
            raise BusanGijangContractError("district card field changed")
        values[label] = value
    apply_dates, education_dates = _dates(values["신청기간"]), _dates(values["교육기간"])
    if len(apply_dates) != 2 or len(education_dates) != 2:
        raise BusanGijangContractError("district card date ranges changed")
    raw_apply_start, raw_apply_end = apply_dates
    raw_start, raw_end = education_dates
    apply_start, apply_end = sorted(apply_dates)
    start, end = sorted(education_dates)
    if not re.fullmatch(r"[0-9,]+/[0-9,]+명", values["모집인원(신청/대기)"]) or not re.fullmatch(
        r"[0-9,]+/[0-9,]+명", values["접수인원(신청/대기)"]
    ):
        raise BusanGijangContractError("district aggregate capacity changed")
    return {
        "provider": BUSAN_GIJANG_PROVIDER,
        "provider_course_id": f"{BUSAN_GIJANG_PROVIDER}:course:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title, "description": title,
        "branch": BUSAN_GIJANG_MUNICIPALITY_NAME,
        "branch_code": BUSAN_GIJANG_MUNICIPALITY_CODE,
        "municipality_code": BUSAN_GIJANG_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_GIJANG_MUNICIPALITY_NAME,
        "sido": "부산광역시", "sigungu": "기장군",
        "provider_organizer": BUSAN_LIFELONG_GIJANG_OFFICE_NAME,
        "venue_name": values["교육장소"],
        "category": category, "program_type": "교육/강좌",
        "raw_url": busan_gijang_detail_url(identity),
        "application_url": "", "application_type": "INFO_ONLY",
        "reservation_available": False, "status": _LOCAL_STATUS_MAP[status],
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "apply_start": apply_start.isoformat(), "apply_end": apply_end.isoformat(),
        "schedule_raw": "", "fee": "", "capacity": "", "target": "",
        "collection_category": "공공예약", "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관", "source_group": "municipal_reservation",
        "service_group": "공공강좌", "service_group_policy": "locked",
        "collection_type": "complete_html_pages+current_detail_allowlist",
        "raw_fields": {
            "parser": BUSAN_GIJANG_PARSER, "source_catalog": "gijang_complete_online_courses",
            "source_identity": identity, "source_page": page, "source_position": position,
            "source_status": status, "source_category": category,
            "source_reversed_application_period": raw_apply_end < raw_apply_start,
            "source_reversed_education_period": raw_end < raw_start,
            "source_apply_start": raw_apply_start.isoformat(), "source_apply_end": raw_apply_end.isoformat(),
            "source_period_start": raw_start.isoformat(), "source_period_end": raw_end.isoformat(),
            "aggregate_enrolment_values_not_persisted": True, "detail_verified": False,
            "application_form_fetched": False, "applicant_list_fetched": False,
            "service_family": "education",
        },
    }


def _parse_local_page(
    soup: BeautifulSoup, final_url: str, *, page: int, expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    _local_form_contract(soup, final_url, page)
    last = _local_last_page(soup)
    if expected_last is not None and last != expected_last:
        raise BusanGijangContractError("district final page changed")
    cards = soup.select("div.pro_applylist > dl")
    if page <= last:
        if len(cards) != BUSAN_GIJANG_PAGE_SIZE:
            raise BusanGijangContractError("district data page size changed")
    elif page == last + 1:
        if cards:
            raise BusanGijangContractError("district sentinel is not empty")
        return [], last
    else:
        raise BusanGijangContractError("district request passed sentinel")
    return [_local_row(card, page=page, position=position) for position, card in enumerate(cards, 1)], last


def _local_detail_values(table: Tag) -> tuple[list[str], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for row in table.select(":scope > tbody > tr"):
        children = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(children):
            heading = children[index]
            if heading.name != "th" or index + 1 >= len(children) or children[index + 1].name != "td":
                raise BusanGijangContractError("district detail field structure changed")
            label = _text(heading)
            if label in labels:
                raise BusanGijangContractError("duplicate district detail field")
            labels.append(label)
            value_node = children[index + 1]
            if label in _LOCAL_DETAIL_SAFE:
                safe[label] = _text(value_node)
            else:
                skipped.add(label)
            index += 2
    if tuple(labels) != _LOCAL_DETAIL_LABELS:
        raise BusanGijangContractError("district detail fields changed")
    required_skipped = {"강사명", "총 교육시간", "연락처", "모집인원", "접수인원", "강좌소개", "참고사항", "첨부파일"}
    if not required_skipped.issubset(skipped):
        raise BusanGijangContractError("district private detail boundary changed")
    return labels, safe, skipped


def _parse_local_detail(soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_identity"))
    query = _exact_query_url(final_url, BUSAN_GIJANG_HOST, BUSAN_GIJANG_PATH)
    if query != {"menuCd": [BUSAN_GIJANG_MENU], "idx": [identity], "mode": ["view"]}:
        raise BusanGijangContractError("district detail response identity changed")
    root = _one(soup.select("div#conts.conts"), "district detail root")
    title = _text(_one(root.find_all("h3", recursive=False), "district detail heading"))
    if title != _clean(parent.get("title")):
        raise BusanGijangContractError("district list/detail title mismatch")
    table = _one(root.select(":scope > table.tbl_lll.Tbody"), "district detail table")
    _labels, safe, skipped = _local_detail_values(table)
    if any(not safe.get(label) for label in _LOCAL_DETAIL_SAFE):
        raise BusanGijangContractError("empty district safe detail value")
    education = _dates(safe["교육기간"])
    application = _dates(safe["접수기간"])
    if [item.isoformat() for item in sorted(education)] != [_clean(parent.get("start_date")), _clean(parent.get("end_date"))] or [item.isoformat() for item in sorted(application)] != [_clean(parent.get("apply_start")), _clean(parent.get("apply_end"))]:
        raise BusanGijangContractError("district list/detail dates mismatch")
    source_status = safe["신청상태"]
    if source_status != _clean(raw.get("source_status")):
        raise BusanGijangContractError("district list/detail status mismatch")
    controls = root.select(":scope > div.taC > a.application")
    if len(controls) > 1:
        raise BusanGijangContractError("multiple district application controls")
    active = source_status == "접수중"
    if active:
        control = _one(controls, "district application control")
        if (
            _text(control) != "신청하기" or _clean(control.get("href")) != "#"
            or _clean(control.get("onclick")) != "return false;"
            or _clean(control.get("data-idx")) != identity
            or _clean(control.get("data-menucd")) != BUSAN_GIJANG_MENU
        ):
            raise BusanGijangContractError("district application control changed")
    elif controls:
        raise BusanGijangContractError("closed district detail exposes application control")
    result = dict(parent)
    result.update({
        "application_url": _clean(parent.get("raw_url")) if active else "",
        "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
        "reservation_available": active,
        "target": safe["수강대상"], "venue_name": safe["강의실"],
        "fee": safe["수강료"], "schedule_raw": f"{safe['요일']} {safe['교육시간']}",
        "application_method_raw": safe["접수방법"],
    })
    result["raw_fields"] = {
        **raw, "detail_verified": True, "detail_source_status": source_status,
        "detail_application_control": active,
        "instructor_value_never_read": "강사명" in skipped,
        "contact_value_never_read": "연락처" in skipped,
        "enrolment_values_never_read": {"모집인원", "접수인원"}.issubset(skipped),
        "attachments_never_read": "첨부파일" in skipped,
        "free_form_values_never_read": {"강좌소개", "참고사항"}.issubset(skipped),
        "application_form_fetched": False, "applicant_list_fetched": False,
    }
    return result


def _platform_office() -> _lifelong.BusanOffice:
    """Return the dedicated owner contract independent of shared-registry state."""
    shared = _lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(
        BUSAN_LIFELONG_GIJANG_OFFICE
    )
    if (
        shared is None
        or shared.name != BUSAN_LIFELONG_GIJANG_OFFICE_NAME
        or shared.municipality_code
        or shared.municipality_name
        or shared.ownership != "duplicate_dedicated_gijang_owner"
    ):
        raise BusanGijangContractError("lifelong Gijang office ownership changed")
    return _lifelong.BusanOffice(
        BUSAN_LIFELONG_GIJANG_OFFICE,
        BUSAN_LIFELONG_GIJANG_OFFICE_NAME,
        municipality_code=BUSAN_GIJANG_MUNICIPALITY_CODE,
        municipality_name=BUSAN_GIJANG_MUNICIPALITY_NAME,
        ownership="duplicate_dedicated_gijang_owner",
    )


def _parse_platform_page(
    soup: BeautifulSoup, final_url: str, *, page: int,
    page_unit: int = BUSAN_LIFELONG_PAGE_SIZE,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    expected_query = {
        key: [value]
        for key, value in _lifelong._list_payload(
            BUSAN_LIFELONG_GIJANG_OFFICE, page
        ).items()
    }
    expected_query["pageUnit"] = [str(page_unit)]
    if _exact_query_url(
        final_url,
        _lifelong.BUSAN_LIFELONG_HOST,
        _lifelong.BUSAN_LIFELONG_LIST_PATH,
    ) != expected_query:
        raise BusanGijangContractError("platform list response query changed")
    office = _platform_office()
    errors = _lifelong._form_errors(soup, office, page)
    if errors:
        raise BusanGijangContractError("; ".join(errors))
    last, errors = _lifelong._advertised_last(soup)
    if errors:
        raise BusanGijangContractError("; ".join(errors))
    if expected_last is not None and last != expected_last:
        raise BusanGijangContractError("platform final page changed")
    rows, errors = _lifelong._parse_list_page(soup, office=office, page=page)
    if errors:
        raise BusanGijangContractError("; ".join(errors))
    if page <= last and not rows:
        raise BusanGijangContractError("platform data page became empty")
    if page == last + 1 and rows:
        raise BusanGijangContractError("platform sentinel is not empty")
    if page > last + 1:
        raise BusanGijangContractError("platform request passed sentinel")
    return rows, last


def _platform_signature(rows: Sequence[Mapping[str, Any]]) -> Counter[tuple[str, ...]]:
    return Counter(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("raw_fields", {}).get("identity_kind")),
            _clean(row.get("title")), _clean(row.get("start_date")),
            _clean(row.get("end_date")), _clean(row.get("apply_start")),
            _clean(row.get("apply_end")), _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    )


def _platform_logical_identity(row: Mapping[str, Any]) -> str:
    kind = _clean(row.get("raw_fields", {}).get("identity_kind"))
    if kind == "external":
        return f"external:{_platform_external_idx(row)}"
    if kind == "internal":
        identity = _clean(row.get("raw_fields", {}).get("identity"))
        if not re.fullmatch(r"LEARNING_[A-Za-z0-9_-]+", identity):
            raise BusanGijangContractError("invalid native platform identity")
        return f"internal:{identity}"
    raise BusanGijangContractError("unexpected platform identity family")


def _platform_semantic_value(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _platform_logical_identity(row), _clean(row.get("title")),
        _clean(row.get("start_date")), _clean(row.get("end_date")),
        _clean(row.get("apply_start")), _clean(row.get("apply_end")),
        _clean(row.get("raw_fields", {}).get("source_status")),
    )


def _platform_raw_census(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, tuple[tuple[str, ...], Mapping[str, Any]]], int, int]:
    """Validate one raw census and retain one exact copy per logical ID.

    The source assigns every display sequence exactly once but can repeat an
    identity at the page boundary and omit another identity in that request.
    Conflicting copies are never reconciled.
    """

    sequences = sorted(
        int(row.get("raw_fields", {}).get("list_sequence") or 0) for row in rows
    )
    if not sequences or sequences != list(range(1, len(rows) + 1)):
        raise BusanGijangContractError("platform global sequence is not continuous")
    unique: dict[str, tuple[tuple[str, ...], Mapping[str, Any]]] = {}
    for row in rows:
        semantic = _platform_semantic_value(row)
        identity = semantic[0]
        previous = unique.get(identity)
        if previous is not None and previous[0] != semantic:
            raise BusanGijangContractError("platform repeated identity changed semantics")
        unique.setdefault(identity, (semantic, row))
    return unique, len(rows), len(rows) - len(unique)


def _reconcile_platform_pair(
    left: Mapping[str, tuple[tuple[str, ...], Mapping[str, Any]]],
    right: Mapping[str, tuple[tuple[str, ...], Mapping[str, Any]]],
    *,
    declared_total: int,
) -> tuple[dict[str, tuple[tuple[str, ...], Mapping[str, Any]]], str]:
    merged = dict(left)
    for identity, value in right.items():
        previous = merged.get(identity)
        if previous is not None and previous[0] != value[0]:
            raise BusanGijangContractError("platform census semantics changed")
        merged.setdefault(identity, value)
    if len(merged) != declared_total:
        raise BusanGijangContractError(
            "platform pairwise census union does not repair every boundary slot"
        )
    signature = hashlib.sha256(
        repr(sorted(value[0] for value in merged.values())).encode("utf-8")
    ).hexdigest()
    return merged, signature


def _platform_external_idx(row: Mapping[str, Any]) -> str:
    raw = row.get("raw_fields", {})
    if _clean(raw.get("identity_kind")) != "external":
        raise BusanGijangContractError("platform row is not external")
    identity = canonical_busan_gijang_identity(row.get("raw_url"))
    if not identity:
        raise BusanGijangContractError("platform external row left Gijang scope")
    return identity


def _prove_platform_duplicate(
    row: Mapping[str, Any], district_by_id: Mapping[str, Mapping[str, Any]],
) -> str:
    identity = _platform_external_idx(row)
    district = district_by_id.get(identity)
    if district is None:
        raise BusanGijangContractError("platform external idx absent from district census")
    fields = ("title", "start_date", "end_date", "apply_start", "apply_end")
    if any(_clean(row.get(key)) != _clean(district.get(key)) for key in fields):
        raise BusanGijangContractError("platform external row does not exactly prove district ownership")
    return identity


def _platform_native_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    if raw.get("identity_kind") != "internal" or not re.fullmatch(r"LEARNING_[A-Za-z0-9_-]+", identity):
        raise BusanGijangContractError("invalid native platform identity")
    result = dict(row)
    result.update({
        "provider": BUSAN_GIJANG_PROVIDER,
        "provider_course_id": f"{BUSAN_GIJANG_PROVIDER}:lifelong:{identity}",
        "prefer_incoming_provider_course_id": True,
        "branch": BUSAN_LIFELONG_GIJANG_OFFICE_NAME,
        "branch_code": "gijang-lifelong-office00002631", "preserve_branch": True,
        "provider_organizer": BUSAN_LIFELONG_GIJANG_OFFICE_NAME,
        "municipality_code": BUSAN_GIJANG_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_GIJANG_MUNICIPALITY_NAME,
        "sido": "부산광역시", "sigungu": "기장군",
        "collection_category": "공공예약", "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관", "source_group": "municipal_reservation",
        "service_group": "공공강좌", "service_group_policy": "locked",
        "collection_type": "complete_shared_office_census+native_current_detail_allowlist",
    })
    result["raw_fields"] = {
        **raw, "parser": BUSAN_GIJANG_PARSER,
        "source_catalog": "busan_lifelong_gijang_native",
        "source_provider": BUSAN_LIFELONG_PROVIDER, "detail_verified": False,
        "application_form_fetched": False, "applicant_list_fetched": False,
    }
    return result


def _platform_detail_values(soup: BeautifulSoup) -> tuple[list[str], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in soup.select("div.form_group dl"):
        label = _text(_one(definition.find_all("dt", recursive=False), "platform detail label"))
        value = _one(definition.find_all("dd", recursive=False), "platform detail value")
        if label in labels:
            raise BusanGijangContractError("duplicate platform detail field")
        labels.append(label)
        if label in _PLATFORM_DETAIL_SAFE:
            safe[label] = _text(value)
        else:
            skipped.add(label)
    required_labels = {
        "회차명", "강좌분류", "교육대상", "문의전화", "교육장소", "총 교육시간",
        "교육기간", "교육시간", "수강료", "재료비", "접수인원", "우선모집기간",
        "우선모집인원", "일반모집기간", "일반모집인원", "모집방법", "신청상태",
        "교육상태", "강좌소개", "강좌소개 첨부파일", "강사", "강의계획서",
        "결제방법", "주의사항", "검색키워드", "강좌제한",
    }
    if set(labels) != required_labels:
        raise BusanGijangContractError("platform detail fields changed")
    required_skipped = {
        "문의전화", "접수인원", "우선모집인원", "일반모집인원", "강좌소개",
        "강좌소개 첨부파일", "강사", "강의계획서", "주의사항", "검색키워드", "강좌제한",
    }
    if not required_skipped.issubset(skipped):
        raise BusanGijangContractError("platform private detail boundary changed")
    return labels, safe, skipped


def _parse_platform_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any],
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    query = _exact_query_url(final_url, _lifelong.BUSAN_LIFELONG_HOST, _lifelong.BUSAN_LIFELONG_DETAIL_PATH)
    if query != {"lng_id": [identity]}:
        raise BusanGijangContractError("platform detail identity changed")
    for name, expected in (("lng_id", identity), ("inst_id", BUSAN_LIFELONG_GIJANG_OFFICE)):
        values = {_clean(node.get("value")) for node in soup.select(f"input[name='{name}']")}
        if values != {expected}:
            raise BusanGijangContractError(f"platform detail {name} changed")
    heading = _one(soup.select("h2.enrolTit"), "platform detail heading")
    prefix = _text(_one(heading.select(":scope > span"), "platform office prefix"))
    if prefix != f"[{BUSAN_LIFELONG_GIJANG_OFFICE_NAME}]":
        raise BusanGijangContractError("platform detail office changed")
    title = _clean(" ".join(str(child) for child in heading.children if isinstance(child, NavigableString)))
    if title != _clean(parent.get("title")):
        raise BusanGijangContractError("platform list/detail title mismatch")
    _labels, safe, skipped = _platform_detail_values(soup)
    required_safe = {"교육대상", "교육장소", "교육기간", "교육시간", "수강료", "재료비", "일반모집기간", "모집방법", "신청상태", "교육상태"}
    if any(label not in safe for label in required_safe):
        raise BusanGijangContractError("platform safe detail value missing")
    education = _dates(safe["교육기간"])
    application = _dates(safe["일반모집기간"])
    if [item.isoformat() for item in sorted(education)] != [_clean(parent.get("start_date")), _clean(parent.get("end_date"))] or [item.isoformat() for item in sorted(application)] != [_clean(parent.get("apply_start")), _clean(parent.get("apply_end"))]:
        raise BusanGijangContractError("platform list/detail dates mismatch")
    controls = soup.select("#learning_aply_btn")
    if len(controls) > 1:
        raise BusanGijangContractError("multiple platform application controls")
    control_label = _text(controls[0]) if controls else ""
    active = bool(
        controls and "접수중" in safe["신청상태"]
        and _clean(controls[0].get("onclick")) == "fn_learning_apply(); return false;"
        and control_label in {"일반모집신청", "대기자신청", "우선모집신청"}
    )
    if controls and not active:
        raise BusanGijangContractError("platform application control changed")
    result = dict(parent)
    result.update({
        "application_url": _clean(parent.get("raw_url")) if active else "",
        "application_type": "WAITLIST_APPLY" if active and control_label == "대기자신청" else "ONLINE_RESERVATION" if active else "INFO_ONLY",
        "reservation_available": active,
        "status": "OPEN" if active else "SCHEDULED" if "접수대기" in safe["신청상태"] else "CLOSED",
        "target": safe["교육대상"], "venue_name": safe["교육장소"],
        "fee": safe["수강료"], "schedule_raw": safe["교육시간"],
        "application_method_raw": safe["모집방법"],
    })
    result["raw_fields"] = {
        **raw, "detail_verified": True, "detail_source_status": safe["신청상태"],
        "detail_application_control": control_label,
        "contact_value_never_read": "문의전화" in skipped,
        "enrolment_values_never_read": {"접수인원", "우선모집인원", "일반모집인원"}.issubset(skipped),
        "instructor_value_never_read": "강사" in skipped,
        "attachments_never_read": {"강좌소개 첨부파일", "강의계획서"}.issubset(skipped),
        "free_form_values_never_read": {"강좌소개", "주의사항", "검색키워드", "강좌제한"}.issubset(skipped),
        "application_form_fetched": False, "applicant_list_fetched": False,
    }
    return result


def _city_last_page(soup: BeautifulSoup, final_url: str, *, page: int, expected_last: Optional[int] = None) -> int:
    query = _exact_query_url(final_url, BUSAN_CITY_HOST, BUSAN_CITY_LIST_PATH)
    if query != {"curPage": [str(page)], "srchGugun": [BUSAN_CITY_GIJANG_GUGUN], "srchResveInsttCd": [BUSAN_CITY_RESIDENT_OFFICE]}:
        raise BusanGijangContractError("city list response query changed")
    if _text(_one(soup.select("title"), "city list title")) != "강좌/교육 : 부산광역시 통합예약":
        raise BusanGijangContractError("city list title changed")
    form = _one(soup.select("form#srchForm[name='srchForm']"), "city search form")
    page_field = _one(form.select("input[name='curPage']"), "city page field")
    if _clean(form.get("method")).casefold() != "get" or urlparse(_clean(form.get("action"))).path != "/lctre" or _clean(page_field.get("value")) != str(page):
        raise BusanGijangContractError("city search form changed")
    for name, expected in (("srchGugun", BUSAN_CITY_GIJANG_GUGUN), ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE)):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanGijangContractError(f"city {name} filter changed")
    end_links = soup.select("div.paginate > a.pgEnd[href]")
    roots = soup.select("ul.reserveList")
    if not end_links and not roots:
        empty = soup.select("div.reserveListWrap > div.txtCenter")
        if len(empty) != 1 or _text(empty[0]) != "등록된 강좌가 없습니다.":
            raise BusanGijangContractError("city empty partition changed")
        return expected_last or 1
    if len(end_links) != 1:
        raise BusanGijangContractError("city final-page control changed")
    parsed = urlparse(urljoin(BUSAN_CITY_GIJANG_URL, _clean(end_links[0].get("href"))))
    end_query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != BUSAN_CITY_HOST
        or parsed.path not in {BUSAN_CITY_LIST_PATH, BUSAN_CITY_LIST_PATH + ".do"}
        or parsed.port is not None or parsed.username or parsed.password or parsed.params or parsed.fragment
        or set(end_query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or end_query.get("srchGugun") != [BUSAN_CITY_GIJANG_GUGUN]
        or end_query.get("srchResveInsttCd") != [BUSAN_CITY_RESIDENT_OFFICE]
        or len(end_query.get("curPage", [])) != 1 or not end_query["curPage"][0].isdigit()
    ):
        raise BusanGijangContractError("unsafe city final-page control")
    return int(end_query["curPage"][0])


def _definition_pairs(root: Tag, *, skipped_labels: set[str]) -> tuple[dict[str, str], set[str]]:
    headings = root.find_all("dt", recursive=False)
    values = root.find_all("dd", recursive=False)
    if len(headings) != len(values):
        raise BusanGijangContractError("city card fields changed")
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for heading, value in zip(headings, values):
        label = _text(heading)
        if label in safe or label in skipped:
            raise BusanGijangContractError("duplicate city card field")
        if label in skipped_labels:
            skipped.add(label)
        else:
            safe[label] = _text(value)
    return safe, skipped


def _parse_city_page(
    soup: BeautifulSoup, final_url: str, *, page: int, expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    last = _city_last_page(soup, final_url, page=page, expected_last=expected_last)
    if expected_last is not None and last != expected_last:
        raise BusanGijangContractError("city final page changed")
    roots = soup.select("ul.reserveList")
    if page > last:
        if page != last + 1 or roots:
            raise BusanGijangContractError("city sentinel changed")
        return [], last
    if len(roots) > 1:
        raise BusanGijangContractError("multiple city course lists")
    rows: list[dict[str, Any]] = []
    items = roots[0].find_all("li", recursive=False) if roots else []
    for position, item in enumerate(items, 1):
        link = _one(item.select(":scope > a.reserveItem[onclick]"), "city course link")
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanGijangContractError("city identity action changed")
        group_id, program_id = action.groups()
        title_node = _one(link.select(".infoBox > .tit"), "city course title")
        title = _text(title_node)
        if not title or _clean(title_node.get("title")) != title:
            raise BusanGijangContractError("city course title changed")
        source_status = _text(_one(link.select(".statusMark"), "city status"))
        if source_status not in _CITY_STATUS_MAP:
            raise BusanGijangContractError("unknown city status")
        definitions = _one(link.select(".infoBox > dl"), "city card values")
        safe, skipped = _definition_pairs(definitions, skipped_labels={"문의"})
        if set(safe) != {"기관", "대상", "장소", "일자", "방법"} or skipped != {"문의"}:
            raise BusanGijangContractError("city card field boundary changed")
        if not safe["기관"].startswith("기장군 ") or not safe["기관"].endswith(" 주민자치회"):
            raise BusanGijangContractError("city course left Gijang owner")
        values = _dates(safe["일자"])
        if len(values) != 4 or values[1] < values[0] or values[3] < values[2]:
            raise BusanGijangContractError("city card dates changed")
        apply_start, apply_end, start, end = values
        identity = f"{group_id}:{program_id}"
        rows.append({
            "provider": BUSAN_GIJANG_PROVIDER,
            "provider_course_id": f"{BUSAN_GIJANG_PROVIDER}:reserve:{identity}",
            "prefer_incoming_provider_course_id": True,
            "title": title, "description": title, "branch": safe["기관"],
            "branch_code": f"gijang-reserve-{group_id}", "preserve_branch": True,
            "municipality_code": BUSAN_GIJANG_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_GIJANG_MUNICIPALITY_NAME,
            "sido": "부산광역시", "sigungu": "기장군", "provider_organizer": safe["기관"],
            "venue_name": safe["장소"], "category": "주민자치프로그램", "program_type": "교육/강좌",
            "raw_url": busan_gijang_city_detail_url(group_id, program_id),
            "application_url": "", "application_type": "INFO_ONLY", "reservation_available": False,
            "status": _CITY_STATUS_MAP[source_status], "fee": "", "capacity": "",
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
            "apply_start": apply_start.isoformat(), "apply_end": apply_end.isoformat(),
            "schedule_raw": "", "target": safe["대상"], "application_method_raw": safe["방법"],
            "collection_category": "공공예약", "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관", "source_group": "municipal_reservation",
            "service_group": "공공강좌", "service_group_policy": "locked",
            "collection_type": "complete_html_pages+current_detail_allowlist",
            "raw_fields": {
                "parser": BUSAN_GIJANG_PARSER, "source_catalog": "busan_reserve_gijang_resident_councils",
                "source_identity": identity, "source_group_id": group_id, "source_program_id": program_id,
                "source_page": page, "source_position": position, "source_status": source_status,
                "source_application_method": safe["방법"], "detail_verified": False,
                "inquiry_value_never_read": True, "application_form_fetched": False,
                "applicant_list_fetched": False, "service_family": "education",
            },
        })
    if page < last and len(rows) != 10:
        raise BusanGijangContractError("city intermediate page is short")
    if page == last and last > 1 and not 1 <= len(rows) <= 10:
        raise BusanGijangContractError("city final page changed")
    return rows, last


def _parse_city_detail(soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    expected = busan_gijang_city_detail_url(raw.get("source_group_id"), raw.get("source_program_id"))
    if _compare_url(final_url) != _compare_url(expected):
        raise BusanGijangContractError("city detail response scope changed")
    form = _one(soup.select("form#viewForm"), "city detail form")
    heading = _one(form.select("div.contHeader > h3.titPage"), "city detail heading")
    if _clean(parent.get("title")) not in _text(heading):
        raise BusanGijangContractError("city list/detail title mismatch")
    source_status = _text(_one(heading.select(".statusMark"), "city detail status"))
    expected_status = "대기자접수" if _clean(raw.get("source_status")) == "대기접수" else _clean(raw.get("source_status"))
    if source_status != expected_status:
        raise BusanGijangContractError("city list/detail status mismatch")
    info = _one(form.select("div.reserveStateInfo"), "city safe detail values")
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    allowed = {"운영기간", "신청기간", "신청방법", "운영기관", "대상", "수강료", "요일 /시간"}
    for definition in info.find_all("dl", recursive=False):
        label = _text(_one(definition.find_all("dt", recursive=False), "city detail label"))
        value = _one(definition.find_all("dd", recursive=False), "city detail value")
        if label in {"문의전화", "첨부파일"}:
            skipped.add(label)
        elif label in allowed:
            safe[label] = _text(value)
        else:
            raise BusanGijangContractError(f"unknown city detail field {label!r}")
    if "문의전화" not in skipped or set(safe) != allowed:
        raise BusanGijangContractError("city detail field boundary changed")
    education, application = _dates(safe["운영기간"]), _dates(safe["신청기간"])
    if [item.isoformat() for item in education] != [_clean(parent.get("start_date")), _clean(parent.get("end_date"))] or [item.isoformat() for item in application] != [_clean(parent.get("apply_start")), _clean(parent.get("apply_end"))]:
        raise BusanGijangContractError("city list/detail dates mismatch")
    if safe["신청방법"] != _clean(raw.get("source_application_method")) or safe["운영기관"] != _clean(parent.get("branch")) or safe["대상"] != _clean(parent.get("target")):
        raise BusanGijangContractError("city list/detail safe values mismatch")
    controls = form.select("div.reserveBtnWrap > a.btnTypeXL")
    if len(controls) > 1:
        raise BusanGijangContractError("multiple city application controls")
    status = _CITY_STATUS_MAP[_clean(raw.get("source_status"))]
    active = status == "OPEN" and "온라인" in safe["신청방법"]
    if active and not controls:
        raise BusanGijangContractError("open online city row lacks control")
    result = dict(parent)
    result.update({
        "application_url": _clean(parent.get("raw_url")) if active else "",
        "application_type": "WAITLIST_APPLY" if active and raw.get("source_status") == "대기접수" else "ONLINE_RESERVATION" if active else "OFFLINE_APPLY" if status == "OPEN" else "INFO_ONLY",
        "reservation_available": active, "fee": safe["수강료"], "schedule_raw": safe["요일 /시간"],
    })
    result["raw_fields"] = {
        **raw, "detail_verified": True, "detail_source_status": source_status,
        "detail_application_control": _text(controls[0]) if controls else "",
        "inquiry_value_never_read": True, "attachments_never_read": "첨부파일" in skipped,
        "free_form_detail_never_read": True, "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _signature(rows: Sequence[Mapping[str, Any]], identity_key: str) -> str:
    values = sorted(
        (
            _clean(row.get("raw_fields", {}).get(identity_key)), _clean(row.get("title")),
            _clean(row.get("start_date")), _clean(row.get("end_date")),
            _clean(row.get("apply_start")), _clean(row.get("apply_end")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    )
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _pii_key(value: Any) -> bool:
    lowered = _clean(value).casefold()
    return any(token in lowered for token in (
        "phone", "telephone", "email", "instructor", "teacher", "강사", "전화",
        "메일", "applicant", "contact", "enrolment", "enrollment",
    ))


def _sanitize_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    redactions = 0

    def walk(value: Any, key: str = "") -> Any:
        nonlocal redactions
        if _pii_key(key):
            redactions += 1
            return None
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for child_key, child_value in value.items():
                cleaned = walk(child_value, _clean(child_key))
                if cleaned is not None:
                    result[str(child_key)] = cleaned
            return result
        if isinstance(value, (list, tuple, set)):
            result_list = []
            for child in value:
                cleaned = walk(child, key)
                if cleaned is not None:
                    result_list.append(cleaned)
            return result_list
        if isinstance(value, str):
            text = _clean(value)
            text, first = _PHONE_RE.subn("[redacted]", text)
            text, second = _EMAIL_RE.subn("[redacted]", text)
            redactions += first + second
            return text
        return value

    sanitized = walk(dict(row))
    if not isinstance(sanitized, dict):
        raise BusanGijangContractError("row sanitizer changed type")
    return sanitized, redactions


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "provider": BUSAN_GIJANG_PROVIDER,
        "candidate_id": BUSAN_GIJANG_CANDIDATE_ID,
        "canonical_url": BUSAN_GIJANG_CANONICAL_URL,
        "municipality_code": BUSAN_GIJANG_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_GIJANG_MUNICIPALITY_NAME,
        "ownership_scope": BUSAN_GIJANG_OWNERSHIP_SCOPE,
        "discovery_audit": dict(BUSAN_GIJANG_DISCOVERY_AUDIT),
        "pages": 0, "detail_pages": 0, "list_requests": 0,
        "sentinel_requests": 0, "stability_rechecks": 0,
        "network_requests": 0, "network_retry_count": 0, "sessions_created": 0,
        "pagination_detected": False, "pagination_complete": False,
        "details_complete": False, "snapshot_complete": False,
        "atomic_union_complete": False, "source_cap_reached": False,
        "no_current_data": False, "no_current_reason": "",
        "configured_collection_error": "",
    }


def collect_busan_gijang_education(
    target: Any,
    timeout: int = 35,
    max_pages: int = 220,
    detail_limit: int = 60,
    max_requests: int = 260,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_GIJANG_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one fail-closed current/future Gijang education snapshot."""

    meta = _base_meta()
    if not is_busan_gijang_education_target(target):
        meta["configured_collection_error"] = "target does not match the exact canonical Gijang education owner"
        return [], BUSAN_GIJANG_PARSER, meta
    try:
        limits = (timeout, max_pages, detail_limit, max_requests, max_workers)
        if any(isinstance(value, bool) for value in limits):
            raise ValueError("boolean limits are invalid")
        request_timeout = max(1, int(timeout))
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        request_cap = max(0, int(max_requests))
        workers = min(max(1, int(max_workers)), BUSAN_GIJANG_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_GIJANG_PARSER, meta
    if page_cap < 1 or request_cap < 3:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "caps cannot inspect all three ledgers"
        return [], BUSAN_GIJANG_PARSER, meta

    fetch = fetcher or _default_fetcher
    factory = session_factory or _default_session_factory
    budget = _RequestBudget(request_cap)

    def account(result: _FetchResult, *, list_phase: bool) -> Any:
        meta["network_retry_count"] += result.retries
        meta["sessions_created"] += result.sessions
        meta["network_requests"] = budget.count
        if list_phase:
            meta["list_requests"] += 1
            meta["pages"] += 1
        return result.value

    def fetch_one(url: str, parser: Parser, *, list_phase: bool) -> Any:
        return account(
            _fetch_parsed(
                url, parser, fetcher=fetch, session_factory=factory,
                timeout=request_timeout, sleeper=sleeper, budget=budget,
            ),
            list_phase=list_phase,
        )

    def fetch_batch(items: Sequence[tuple[Any, str, Parser]], *, list_phase: bool) -> dict[Any, Any]:
        if not items:
            return {}
        values, retries, sessions = _fetch_many(
            items, fetcher=fetch, session_factory=factory, timeout=request_timeout,
            sleeper=sleeper, budget=budget, max_workers=workers,
        )
        meta["network_retry_count"] += retries
        meta["sessions_created"] += sessions
        meta["network_requests"] = budget.count
        if list_phase:
            meta["list_requests"] += len(items)
            meta["pages"] += len(items)
        return values

    try:
        # District's complete 10-row archive pages.
        first_local, local_last = fetch_one(
            busan_gijang_list_url(1),
            lambda soup, final: _parse_local_page(soup, final, page=1),
            list_phase=True,
        )
        if local_last > page_cap:
            raise BusanGijangContractError(f"max_pages cap allows {page_cap} of {local_last} district pages")
        local_pages: dict[int, list[dict[str, Any]]] = {1: first_local}
        local_pages.update(fetch_batch([
            (
                page, busan_gijang_list_url(page),
                lambda soup, final, p=page: _parse_local_page(
                    soup, final, page=p, expected_last=local_last,
                )[0],
            )
            for page in range(2, local_last + 1)
        ], list_phase=True))
        local_empty, _ = fetch_one(
            busan_gijang_list_url(local_last + 1),
            lambda soup, final: _parse_local_page(
                soup, final, page=local_last + 1, expected_last=local_last,
            ),
            list_phase=True,
        )
        meta["sentinel_requests"] += 1
        if local_empty:
            raise BusanGijangContractError("district sentinel returned rows")
        local_boundaries = sorted({1, local_last})
        local_rechecks = fetch_batch([
            (
                page, busan_gijang_list_url(page),
                lambda soup, final, p=page: _parse_local_page(
                    soup, final, page=p, expected_last=local_last,
                )[0],
            )
            for page in local_boundaries
        ], list_phase=True)
        meta["stability_rechecks"] += len(local_boundaries)
        for page in local_boundaries:
            if _signature(local_rechecks[page], "source_identity") != _signature(local_pages[page], "source_identity"):
                raise BusanGijangContractError("district boundary page changed")
        local_rows = [row for page in range(1, local_last + 1) for row in local_pages[page]]
        local_by_id: dict[str, dict[str, Any]] = {}
        for row in local_rows:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            if identity in local_by_id:
                raise BusanGijangContractError("duplicate district idx")
            local_by_id[identity] = row
        if len(local_rows) != local_last * BUSAN_GIJANG_PAGE_SIZE:
            raise BusanGijangContractError("district complete count changed")
        local_exclusions: Counter[str] = Counter()
        publishable_local: list[dict[str, Any]] = []
        for identity, row in local_by_id.items():
            exclusion = _AUDITED_LOCAL_NON_COURSES.get(identity)
            if exclusion is None:
                publishable_local.append(row)
                continue
            expected_title, reason = exclusion
            if _clean(row.get("title")) != expected_title:
                raise BusanGijangContractError("audited district non-course identity changed title")
            local_exclusions[reason] += 1

        # The production SafeSession keeps its default 8 MiB response cap and
        # scopes only the total request timeout to 120 seconds.  pageUnit900
        # and 950 stay below that boundary, but the upstream SQL order is
        # unstable where each pair of pages meets.  Each page-unit variant is
        # therefore completed twice with its own sentinel.  The first 900+950
        # union and the independent second 900+950 union must each recover
        # exactly N semantic IDs and produce the same aggregate signature.
        platform_maps: list[
            dict[str, tuple[tuple[str, ...], Mapping[str, Any]]]
        ] = []
        platform_raw_rows: list[list[dict[str, Any]]] = []
        platform_raw_duplicate_counts: list[int] = []
        platform_census_page_units = list(BUSAN_LIFELONG_PAGE_UNITS) * 2
        platform_declared_total = 0
        platform_last = 0
        for census_index, census_page_unit in enumerate(
            platform_census_page_units
        ):
            if census_index == 0:
                first_rows, current_last = fetch_one(
                    busan_gijang_platform_list_url(1, census_page_unit),
                    lambda soup, final: _parse_platform_page(
                        soup,
                        final,
                        page=1,
                        page_unit=census_page_unit,
                    ),
                    list_phase=True,
                )
                if current_last > page_cap:
                    raise BusanGijangContractError(
                        f"max_pages cap allows {page_cap} of {current_last} platform pages"
                    )
                if current_last < 1:
                    raise BusanGijangContractError("invalid platform final page")
                page_values: dict[int, list[dict[str, Any]]] = {1: first_rows}
                remaining = fetch_batch(
                    [
                        (
                            page,
                            busan_gijang_platform_list_url(
                                page, census_page_unit
                            ),
                            lambda soup, final, p=page: _parse_platform_page(
                                soup,
                                final,
                                page=p,
                                page_unit=census_page_unit,
                                expected_last=current_last,
                            )[0],
                        )
                        for page in range(2, current_last + 2)
                    ],
                    list_phase=True,
                )
                page_values.update(
                    {
                        page: value
                        for page, value in remaining.items()
                        if page <= current_last
                    }
                )
                empty = remaining[current_last + 1]
                platform_last = current_last
            else:
                values = fetch_batch(
                    [
                        (
                            page,
                            busan_gijang_platform_list_url(
                                page, census_page_unit
                            ),
                            lambda soup, final, p=page: _parse_platform_page(
                                soup,
                                final,
                                page=p,
                                page_unit=census_page_unit,
                                expected_last=platform_last,
                            )[0],
                        )
                        for page in range(1, platform_last + 2)
                    ],
                    list_phase=True,
                )
                page_values = {
                    page: values[page] for page in range(1, platform_last + 1)
                }
                empty = values[platform_last + 1]
            meta["sentinel_requests"] += 1
            if empty:
                raise BusanGijangContractError("platform sentinel returned rows")
            census_rows = [
                row
                for page in range(1, platform_last + 1)
                for row in page_values[page]
            ]
            logical, declared_total, duplicate_count = _platform_raw_census(
                census_rows
            )
            if platform_declared_total and declared_total != platform_declared_total:
                raise BusanGijangContractError("platform declared total changed")
            platform_declared_total = declared_total
            platform_maps.append(logical)
            platform_raw_rows.append(census_rows)
            platform_raw_duplicate_counts.append(duplicate_count)
        meta["stability_rechecks"] += 3
        reconciled_01, signature_01 = _reconcile_platform_pair(
            platform_maps[0],
            platform_maps[1],
            declared_total=platform_declared_total,
        )
        reconciled_23, signature_23 = _reconcile_platform_pair(
            platform_maps[2],
            platform_maps[3],
            declared_total=platform_declared_total,
        )
        if signature_01 != signature_23 or {
            key: value[0] for key, value in reconciled_01.items()
        } != {key: value[0] for key, value in reconciled_23.items()}:
            raise BusanGijangContractError(
                "platform reconciled aggregate signatures changed"
            )
        platform_rows = [
            reconciled_01[key][1] for key in sorted(reconciled_01)
        ]
        external_rows = [row for row in platform_rows if row.get("raw_fields", {}).get("identity_kind") == "external"]
        native_source = [row for row in platform_rows if row.get("raw_fields", {}).get("identity_kind") == "internal"]
        if len(external_rows) + len(native_source) != len(platform_rows):
            raise BusanGijangContractError("unexpected platform identity family")
        external_ids = [_prove_platform_duplicate(row, local_by_id) for row in external_rows]
        if len(external_ids) != len(set(external_ids)):
            raise BusanGijangContractError("duplicate platform external idx")
        native_rows = [_platform_native_row(row) for row in native_source]
        native_exclusions: Counter[str] = Counter()
        publishable_native: list[dict[str, Any]] = []
        for row in native_rows:
            identity = _clean(row.get("raw_fields", {}).get("identity"))
            exclusion = _AUDITED_NATIVE_NON_COURSES.get(identity)
            if exclusion is None:
                publishable_native.append(row)
                continue
            expected_title, reason = exclusion
            if _clean(row.get("title")) != expected_title:
                raise BusanGijangContractError("audited platform non-course identity changed title")
            native_exclusions[reason] += 1

        # Exact 부산시 resident-council partition, currently a proved empty ledger.
        city_first, city_last = fetch_one(
            busan_gijang_city_list_url(1),
            lambda soup, final: _parse_city_page(soup, final, page=1),
            list_phase=True,
        )
        if city_last > page_cap:
            raise BusanGijangContractError(f"max_pages cap allows {page_cap} of {city_last} city pages")
        city_pages: dict[int, list[dict[str, Any]]] = {1: city_first}
        city_pages.update(fetch_batch([
            (
                page, busan_gijang_city_list_url(page),
                lambda soup, final, p=page: _parse_city_page(
                    soup, final, page=p, expected_last=city_last,
                )[0],
            )
            for page in range(2, city_last + 1)
        ], list_phase=True))
        city_empty, _ = fetch_one(
            busan_gijang_city_list_url(city_last + 1),
            lambda soup, final: _parse_city_page(
                soup, final, page=city_last + 1, expected_last=city_last,
            ),
            list_phase=True,
        )
        meta["sentinel_requests"] += 1
        if city_empty:
            raise BusanGijangContractError("city sentinel returned rows")
        city_boundaries = sorted({1, city_last})
        city_rechecks = fetch_batch([
            (
                page, busan_gijang_city_list_url(page),
                lambda soup, final, p=page: _parse_city_page(
                    soup, final, page=p, expected_last=city_last,
                )[0],
            )
            for page in city_boundaries
        ], list_phase=True)
        meta["stability_rechecks"] += len(city_boundaries)
        for page in city_boundaries:
            if _signature(city_rechecks[page], "source_identity") != _signature(city_pages[page], "source_identity"):
                raise BusanGijangContractError("city boundary page changed")
        city_rows = [row for page in range(1, city_last + 1) for row in city_pages[page]]
        city_ids = [_clean(row.get("provider_course_id")) for row in city_rows]
        if len(city_ids) != len(set(city_ids)):
            raise BusanGijangContractError("duplicate city identity")

        local_current = [row for row in publishable_local if date.fromisoformat(row["end_date"]) >= cutoff]
        native_current = [row for row in publishable_native if date.fromisoformat(row["end_date"]) >= cutoff]
        city_current = [row for row in city_rows if date.fromisoformat(row["end_date"]) >= cutoff]
        current = local_current + native_current + city_current
        if len(current) > detail_cap:
            raise BusanGijangContractError(f"detail_limit cap allows {detail_cap} of {len(current)} current details")
        detail_items: list[tuple[Any, str, Parser]] = []
        for row in local_current:
            detail_items.append((
                _clean(row["provider_course_id"]), _clean(row["raw_url"]),
                lambda soup, final, parent=row: _parse_local_detail(soup, final, parent),
            ))
        for row in native_current:
            detail_items.append((
                _clean(row["provider_course_id"]), _clean(row["raw_url"]),
                lambda soup, final, parent=row: _parse_platform_detail(soup, final, parent),
            ))
        for row in city_current:
            detail_items.append((
                _clean(row["provider_course_id"]), _clean(row["raw_url"]),
                lambda soup, final, parent=row: _parse_city_detail(soup, final, parent),
            ))
        enriched_by_id = fetch_batch(detail_items, list_phase=False)
        meta["detail_pages"] = len(detail_items)
        enriched = [enriched_by_id[_clean(row["provider_course_id"])] for row in current]
        safe_rows: list[dict[str, Any]] = []
        privacy_redactions = 0
        for row in enriched:
            safe, count = _sanitize_row(row)
            safe_rows.append(safe)
            privacy_redactions += count
        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(safe_rows))
        before_ids = [_clean(row.get("provider_course_id")) for row in safe_rows]
        after_ids = [_clean(row.get("provider_course_id")) for row in result]
        if len(result) != len(safe_rows) or Counter(after_ids) != Counter(before_ids) or len(after_ids) != len(set(after_ids)):
            raise BusanGijangContractError("dedupe changed complete identity set")

        unique_source_rows = len(publishable_local) + len(publishable_native) + len(city_rows)
        meta.update({
            "network_requests": budget.count,
            "required_list_requests": meta["list_requests"],
            "required_detail_requests": len(detail_items),
            "district_source_rows": len(local_rows), "district_data_pages": local_last,
            "district_page_counts": {page: len(rows) for page, rows in local_pages.items()},
            "district_unique_ids": len(local_by_id),
            "district_status_counts": dict(Counter(_clean(row.get("raw_fields", {}).get("source_status")) for row in local_rows)),
            "district_reversed_education_period_rows": sum(bool(row.get("raw_fields", {}).get("source_reversed_education_period")) for row in local_rows),
            "district_reversed_application_period_rows": sum(bool(row.get("raw_fields", {}).get("source_reversed_application_period")) for row in local_rows),
            "district_excluded_non_course_rows": sum(local_exclusions.values()),
            "district_exclusion_counts": dict(local_exclusions),
            "district_current_count": sum(date.fromisoformat(row["end_date"]) >= cutoff for row in local_rows),
            "district_publishable_current_count": len(local_current),
            "platform_source_rows": len(platform_rows), "platform_data_pages": platform_last,
            "platform_page_units": platform_census_page_units,
            "platform_raw_semantic_censuses": len(platform_raw_rows),
            "platform_raw_census_row_counts": [len(rows) for rows in platform_raw_rows],
            "platform_raw_census_duplicate_identity_counts": platform_raw_duplicate_counts,
            "platform_reconciled_pairwise_signatures": 2,
            "platform_reconciled_identity_count": len(platform_rows),
            "platform_reconciled_signature": signature_01,
            "platform_external_duplicate_rows": len(external_rows),
            "platform_external_unique_idx": len(set(external_ids)),
            "platform_native_rows": len(native_rows),
            "platform_native_current_count": sum(date.fromisoformat(row["end_date"]) >= cutoff for row in native_rows),
            "platform_excluded_native_non_course_rows": sum(native_exclusions.values()),
            "platform_exclusion_counts": dict(native_exclusions),
            "city_source_rows": len(city_rows), "city_data_pages": city_last,
            "city_current_count": len(city_current),
            "source_total": len(local_rows) + len(platform_rows) + len(city_rows),
            "duplicate_source_rows": len(external_rows),
            "unique_education_source_rows": unique_source_rows,
            "current_source_count": len(current), "returned_count": len(result),
            "expired_count": unique_source_rows - len(current),
            "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
            "application_control_count": sum(bool(row.get("reservation_available")) for row in result),
            "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
            "privacy_redactions": privacy_redactions,
            "pagination_detected": local_last > 1 or city_last > 1,
            "pagination_complete": True, "details_complete": True,
            "snapshot_complete": True, "atomic_union_complete": True,
            "source_cap_reached": False, "no_current_data": not result,
            "no_current_reason": "all unique education rows ended before the crawl date" if not result else "",
            "configured_collection_error": "",
        })
        return result, BUSAN_GIJANG_PARSER, meta
    except Exception as exc:
        meta["network_requests"] = budget.count
        message = _clean(exc)
        if "cap" in message:
            meta["source_cap_reached"] = True
        meta["configured_collection_error"] = message or exc.__class__.__name__
        return [], BUSAN_GIJANG_PARSER, meta


collect_courses = collect_busan_gijang_education


__all__ = [
    "BUSAN_GIJANG_PROVIDER", "BUSAN_GIJANG_CANDIDATE_ID", "BUSAN_LIFELONG_PROVIDER",
    "BUSAN_GIJANG_MUNICIPALITY_CODE", "BUSAN_GIJANG_MUNICIPALITY_NAME",
    "BUSAN_GIJANG_URL", "BUSAN_GIJANG_CANONICAL_URL", "BUSAN_CITY_GIJANG_URL",
    "BUSAN_LIFELONG_GIJANG_OFFICE", "BUSAN_GIJANG_PARSER", "BUSAN_GIJANG_OWNERSHIP_SCOPE",
    "BUSAN_GIJANG_CANDIDATE_IDS", "BUSAN_GIJANG_OWNER_BOUNDARY_AUDIT",
    "BUSAN_GIJANG_DISCOVERY_AUDIT", "BusanGijangContractError",
    "is_busan_gijang_education_target", "is_target", "busan_gijang_list_url",
    "busan_gijang_detail_url", "busan_gijang_platform_list_url",
    "busan_gijang_city_list_url", "busan_gijang_city_detail_url",
    "canonical_busan_gijang_identity", "busan_gijang_session_factory",
    "collect_busan_gijang_education", "collect_courses",
]
