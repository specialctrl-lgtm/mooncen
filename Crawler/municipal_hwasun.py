"""Fail-closed collector for Hwasun County's lifelong-learning catalogue.

The executable owner is the county's structured ``lll/edu.do`` ledger.  The
county home page and ``memCheck`` route are discovery/login aliases, not
additional catalogues.  Hwasun Culture and Tourism Foundation is a separate
arts owner, while the Foresttrip result is accommodation/recreation and is
outside the municipal education ledger.

The source is not ordered monotonically by education end date.  In the live
2026-07-28 audit, current rows 263, 261, 260 and 259 were on page 2 after
expired rows.
For that reason every declared page, the immediate empty post-last page, and
stable first/last rechecks are mandatory before current rows are selected.

Detail pages contain three ``le_v_table`` elements.  The third table is an
applicant roster (name/contact data); it is structurally detached before any
text is read.  Only explicitly allowlisted cells in the first two tables are
read.  Inquiry, instructor, attachment, home-page and free-form education
content cells are never read or persisted.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import math
import re
import ssl
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


HWASUN_PROVIDER = "MUNI_WWW_HWASUN_GO_KR_830A293C"
HWASUN_MUNICIPALITY_CODE = "1276000000"
HWASUN_MUNICIPALITY_NAME = "전남광주통합특별시 화순군"
HWASUN_BRANCH = "화순군청"
HWASUN_HOST = "www.hwasun.go.kr"
HWASUN_CANONICAL_URL = (
    "https://www.hwasun.go.kr/lll/edu.do?S=lll&M=010101000000"
)
HWASUN_HOME_PAGE_ALIAS_URL = "https://www.hwasun.go.kr/index.do?S=S01"
HWASUN_MEMCHECK_PII_URL = (
    "https://www.hwasun.go.kr/lll/edu.do?"
    "S=lll&M=010102000000&act=memCheck"
)

HWASUN_HFCT_PROVIDER = "MUNI_WWW_HFCT_OR_KR_05C08858"
HWASUN_HFCT_CANDIDATE_ID = "MUNI_IR_1905FCE539DC"
HWASUN_HFCT_URL = "https://www.hfct.or.kr/edu.do?S=S01&M=0502010000"
HWASUN_FORESTTRIP_PROVIDER = "MUNI_WWW_FORESTTRIP_GO_KR_56B5B367"
HWASUN_FORESTTRIP_CANDIDATE_ID = "MUNI_IR_4DCBC86F6244"
HWASUN_FORESTTRIP_URL = (
    "https://www.foresttrip.go.kr/indvz/main.do?hmpgId=ID02030098"
)

HWASUN_PAGE_SIZE = 10
HWASUN_FETCH_ATTEMPTS = 4
HWASUN_MAX_HTML_BYTES = 4_000_000
HWASUN_PARSER = (
    "hwasun_lifelong_declared_total_all_pages+empty_post_last+"
    "stable_first_last+non_monotonic_end_date_current_details+"
    "closed_application_control+roster_never_read+pii_allowlist"
)
HWASUN_OWNERSHIP_SCOPE = "hwasun_county_lifelong_learning_course_ledger"

HWASUN_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    HWASUN_PROVIDER: {
        "decision": "canonical_municipal_lifelong_owner",
        "canonical_url": HWASUN_CANONICAL_URL,
        "home_page_alias": HWASUN_HOME_PAGE_ALIAS_URL,
        "pii_login_alias": HWASUN_MEMCHECK_PII_URL,
        "operator": "화순군청",
    },
    HWASUN_HFCT_PROVIDER: {
        "decision": "keep_separate_arts_foundation_owner",
        "candidate_id": HWASUN_HFCT_CANDIDATE_ID,
        "url": HWASUN_HFCT_URL,
        "operator": "화순군문화관광재단",
        "reason": "foundation arts programmes have a separate operator and ledger",
    },
    HWASUN_FORESTTRIP_PROVIDER: {
        "decision": "exclude_accommodation_and_recreation_result",
        "candidate_id": HWASUN_FORESTTRIP_CANDIDATE_ID,
        "url": HWASUN_FORESTTRIP_URL,
        "operator": "백아산자연휴양림",
        "reason": "Foresttrip is an accommodation/recreation booking service",
    },
}

HWASUN_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-28",
    "canonical_url": HWASUN_CANONICAL_URL,
    "declared_total": 260,
    "data_pages": 26,
    "page_counts": {page: 10 for page in range(1, 27)},
    "empty_sentinel_page": 27,
    "first_last_rechecks": 2,
    "unique_identities": 260,
    "source_status_counts": {"모집중": 5, "모집마감": 255},
    "current_or_future_rows": 14,
    "current_details_verified": 14,
    "current_ids": (
        "274", "273", "272", "271", "269", "270", "268",
        "267", "266", "265", "263", "261", "260", "259",
    ),
    "page_2_current_ids_found_by_full_scan": ("263", "261", "260", "259"),
    "current_after_first_expired_ids": ("263", "261", "260", "259"),
    "current_status_counts": {"모집중": 5, "모집마감": 9},
    "current_branch_counts": {"화순군청": 14},
    "initial_current_count_assumption": 9,
    "corrected_current_count": 14,
    "correction_reason": (
        "education end dates are non-monotonic; IDs 263, 261, 260 and 259 "
        "occur on page 2 after expired rows and cannot be found by early stopping"
    ),
    "conclusion": (
        "scan all 26 pages before date filtering; verify all 14 current "
        "details and keep HFCT/Foresttrip outside this owner"
    ),
}

HWASUN_PII_FIELDS_NEVER_READ = (
    "문의사항 value",
    "강사명 value",
    "첨부파일 value",
    "홈페이지 value",
    "교육내용 value",
    "수강신청현황 applicant roster",
    "신청자 이름",
    "신청자 연락처",
    "memCheck payload",
    "source HTML persistence",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]


class HwasunContractError(ValueError):
    """Raised when the audited Hwasun source contract changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport/block response without retaining response text."""


_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_TOTAL_RE = re.compile(r"^(?P<total>[\d,]+)건$")
_APPLICATION_PERIOD_RE = re.compile(
    r"^신청기간\s*:\s*(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_EDUCATION_PERIOD_RE = re.compile(
    r"^교육기간\s*:\s*(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_PLAIN_PERIOD_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_MODERN_COUNT_RE = re.compile(
    r"^신청/정원\s+(?P<current>\d+)명\s*/\s*(?P<capacity>\d+)명$"
)
_ARCHIVE_COUNT_RE = re.compile(r"^정원\s+(?P<capacity>\d+)명$")
_DETAIL_COUNT_RE = re.compile(
    r"^(?P<current>\d+)명\s*/\s*(?P<capacity>\d+)명$"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIST_CAPTION = (
    "평생학습 강좌 정보를 번호, 강좌명/신청기간/교육기간, 교육기관, "
    "교육대상, 신청/정원, 수강료/재료비, 모집현황 순으로 안내하는 표입니다."
)
_LIST_HEADERS = (
    "모집현황",
    "강좌명/신청기간/교육기간",
    "교육기관",
    "교육대상",
    "신청/정원",
    "수강료/재료비",
)
_DETAIL_SCHEDULE_CAPTION = (
    "모집일정, 강의일정, 교육시간, 교육기관, 접수방법, 모집인원/정원, "
    "문의사항 정보를 제공하는 표입니다."
)
_DETAIL_SAFE_CAPTION = (
    "강좌상세정보를 강좌분류, 교육대상, 수강료, 강사명, 교육내용, "
    "첨부파일, 홈페이지, 기타안내 순으로 안내하는 표입니다."
)
_SCHEDULE_ALLOWED = frozenset(
    {"모집기간", "교육기간", "교육시간", "교육기관", "접수방법", "모집인원/정원"}
)
_SCHEDULE_SKIPPED = frozenset({"대기인원/정원", "문의사항"})
_DETAIL_ALLOWED = frozenset({"강좌분류", "교육대상", "수강료 / 재료비"})
_DETAIL_SKIPPED = frozenset({"강사명", "첨부파일", "홈페이지", "교육내용", "기타안내"})
_STATUS_CONTRACT: Mapping[str, tuple[str, str]] = {
    "모집중": ("OPEN", "tag_01"),
    "모집마감": ("CLOSED", "tag_02"),
}

_ALLOWED_RAW_KEYS = frozenset(
    {
        "parser",
        "source_identity",
        "source_page",
        "source_category",
        "source_status",
        "source_application_period",
        "source_education_period",
        "source_application_method",
        "source_education_time",
        "source_institution",
        "source_target",
        "source_fee",
        "detail_verified",
        "list_detail_identity_verified",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
        "applicant_roster_structurally_discarded",
        "service_family",
    }
)
_ALLOWED_ROW_KEYS = frozenset(
    {
        "provider",
        "provider_course_id",
        "prefer_incoming_provider_course_id",
        "title",
        "description",
        "branch",
        "branch_code",
        "preserve_branch",
        "category",
        "program_type",
        "raw_url",
        "application_url",
        "application_type",
        "application_method_raw",
        "reservation_available",
        "status",
        "fee",
        "period",
        "start_date",
        "end_date",
        "apply_period",
        "apply_start_date",
        "apply_end_date",
        "schedule_raw",
        "target",
        "capacity_current",
        "capacity_total",
        "venue_name",
        "collection_category",
        "domain_category",
        "operator_type",
        "source_group",
        "collection_type",
        "municipality_code",
        "municipality_name",
        "raw_fields",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    if not isinstance(node, Tag):
        return ""
    return _clean(node.get_text(" ", strip=True))


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("today must be a date, datetime, ISO date string, or None")


def is_hwasun_education_target(target: Any) -> bool:
    """Return true only for the exact provider and canonical query order."""

    return (
        _clean(_target_value(target, "provider")) == HWASUN_PROVIDER
        and _clean(_target_value(target, "url")) == HWASUN_CANONICAL_URL
    )


def is_hwasun_home_page_alias_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == HWASUN_PROVIDER
        and _clean(_target_value(target, "url")) == HWASUN_HOME_PAGE_ALIAS_URL
    )


def is_hwasun_memcheck_pii_alias_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == HWASUN_PROVIDER
        and _clean(_target_value(target, "url")) == HWASUN_MEMCHECK_PII_URL
    )


def is_hwasun_separate_or_excluded_owner_target(target: Any) -> bool:
    pair = (
        _clean(_target_value(target, "provider")),
        _clean(_target_value(target, "url")),
    )
    return pair in {
        (HWASUN_HFCT_PROVIDER, HWASUN_HFCT_URL),
        (HWASUN_FORESTTRIP_PROVIDER, HWASUN_FORESTTRIP_URL),
    }


def hwasun_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise HwasunContractError("page must be a positive integer")
    if page == 1:
        return HWASUN_CANONICAL_URL
    return (
        f"{HWASUN_CANONICAL_URL}&nPage={page}"
        f"&pageCnt={HWASUN_PAGE_SIZE}&search="
    )


def hwasun_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise HwasunContractError("detail identity must be a positive integer")
    return f"{HWASUN_CANONICAL_URL}&act=detail&list_no={value}"


def hwasun_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise HwasunContractError("application identity must be a positive integer")
    return f"{HWASUN_CANONICAL_URL}&act=memForm&list_no={value}"


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.hwasun.go.kr/lll/",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _payload_bytes(value: Any) -> bytes:
    status = getattr(value, "status_code", 200)
    if status != 200:
        raise _TransientFetchError(f"HTTP {status}")
    if isinstance(value, BeautifulSoup):
        return value.encode("utf-8")
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    elif hasattr(value, "content"):
        payload = bytes(value.content)
    else:
        raise _TransientFetchError("response has no HTML payload")
    if not payload or len(payload) > HWASUN_MAX_HTML_BYTES:
        raise _TransientFetchError("empty or oversized HTML payload")
    head = payload[:16_384].lower()
    if b"request blocked" in head or b"access denied" in head:
        raise _TransientFetchError("server returned a request-block page")
    return payload


def _close_quietly(value: Any) -> None:
    try:
        if value is not None and hasattr(value, "close"):
            value.close()
    except Exception:
        pass


class _Client:
    """Sequential browser-like client that rebuilds its session on failure."""

    def __init__(
        self,
        *,
        timeout: int,
        attempts: int,
        session_factory: SessionFactory,
        fetcher: Fetcher,
        sleeper: Sleeper,
    ) -> None:
        self.timeout = timeout
        self.attempts = attempts
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.sleeper = sleeper
        self.session: Any = None
        self.http_attempts = 0
        self.retry_count = 0
        self.sessions_created = 0

    def _session(self) -> Any:
        if self.session is None:
            self.session = self.session_factory()
            self.sessions_created += 1
        return self.session

    def get(self, url: str) -> BeautifulSoup:
        last_type = "unknown"
        for attempt in range(1, self.attempts + 1):
            try:
                session = self._session()
                self.http_attempts += 1
                response = self.fetcher(session, url, self.timeout)
                payload = _payload_bytes(response)
                return BeautifulSoup(payload, "html.parser")
            except (
                _TransientFetchError,
                requests.RequestException,
                ssl.SSLError,
                ConnectionError,
                TimeoutError,
                OSError,
            ) as exc:
                last_type = type(exc).__name__
                _close_quietly(self.session)
                self.session = None
                if attempt >= self.attempts:
                    break
                self.retry_count += 1
                self.sleeper(min(2.0, 0.35 * attempt))
        raise HwasunContractError(
            f"fetch failed after {self.attempts} attempts ({last_type})"
        )

    def close(self) -> None:
        _close_quietly(self.session)
        self.session = None


def _one(nodes: Iterable[Any], label: str) -> Any:
    values = list(nodes)
    if len(values) != 1:
        raise HwasunContractError(f"expected one {label}, found {len(values)}")
    return values[0]


def _validate_page_shell(soup: BeautifulSoup) -> None:
    title = _one(soup.select("title"), "document title")
    if _text(title) != "화순군 평생학습관":
        raise HwasunContractError("document title changed")
    form = _one(soup.select("form[name='insForm']"), "catalogue form")
    if _clean(form.get("method")).lower() != "post":
        raise HwasunContractError("catalogue form method changed")
    if _clean(form.get("action")) != "/lll/edu.do?S=lll&M=010101000000":
        raise HwasunContractError("catalogue form action/query order changed")
    expected = {"pageCnt": "10", "S": "lll", "M": "010101000000"}
    for name, value in expected.items():
        node = _one(form.select(f"input[name='{name}']"), f"form field {name}")
        if _clean(node.get("value")) != value:
            raise HwasunContractError(f"catalogue form field {name} changed")
    for name in ("keyField2", "keyField"):
        _one(form.select(f"select[name='{name}']"), f"form select {name}")
    _one(form.select("input[name='search']"), "search input")


def _parse_total(soup: BeautifulSoup) -> int:
    node = _one(soup.select("span.fb3"), "declared total")
    match = _TOTAL_RE.fullmatch(_text(node))
    if not match:
        raise HwasunContractError("declared total format changed")
    total = int(match.group("total").replace(",", ""))
    if total < 1:
        raise HwasunContractError("declared total must be positive")
    return total


def _period(value: str, pattern: re.Pattern[str], label: str) -> tuple[str, str]:
    match = pattern.fullmatch(_clean(value))
    if not match:
        raise HwasunContractError(f"{label} format changed")
    start = match.group("start")
    end = match.group("end")
    if date.fromisoformat(end) < date.fromisoformat(start):
        raise HwasunContractError(f"{label} is reversed")
    return start, end


def _labelled_value(cell: Tag, expected: str, *, allow_empty: bool) -> str:
    parts = [_clean(value) for value in cell.stripped_strings]
    if not parts or parts[0] != expected:
        raise HwasunContractError(f"list field {expected} changed")
    value = _clean(" ".join(parts[1:]))
    if not value and not allow_empty:
        raise HwasunContractError(f"list field {expected} is empty")
    return value


def _identity_from_detail_href(value: Any) -> str:
    href = _clean(value)
    prefix = "/lll/edu.do?S=lll&M=010101000000&act=detail&list_no="
    if not href.startswith(prefix):
        raise HwasunContractError("detail link/query order changed")
    identity = href[len(prefix):]
    if not _IDENTITY_RE.fullmatch(identity) or href != prefix + identity:
        raise HwasunContractError("detail link identity changed")
    return identity


def _parse_list_row(row: Tag, page: int) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 6:
        raise HwasunContractError(f"page {page}: list row must have six cells")

    status_node = _one(cells[0].select("span"), "list status")
    source_status = _text(status_node)
    contract = _STATUS_CONTRACT.get(source_status)
    if not contract:
        raise HwasunContractError(f"page {page}: unaudited status {source_status}")
    status, expected_class = contract
    if expected_class not in set(status_node.get("class", ())):
        raise HwasunContractError(f"page {page}: status class changed")

    anchor = _one(cells[1].select("a[href]"), "course detail link")
    identity = _identity_from_detail_href(anchor.get("href"))
    parts = [_clean(value) for value in anchor.stripped_strings]
    if len(parts) != 4 or not parts[0] or not parts[1]:
        raise HwasunContractError(f"course {identity}: list title block changed")
    category, title, apply_text, education_text = parts
    apply_start, apply_end = _period(
        apply_text, _APPLICATION_PERIOD_RE, f"course {identity} application period"
    )
    start, end = _period(
        education_text, _EDUCATION_PERIOD_RE, f"course {identity} education period"
    )

    branch = _labelled_value(cells[2], "교육기관", allow_empty=True)
    target = _labelled_value(cells[3], "교육대상", allow_empty=True)
    count_text = _text(cells[4])
    count_match = _MODERN_COUNT_RE.fullmatch(count_text)
    if count_match:
        capacity_current: Optional[int] = int(count_match.group("current"))
        capacity_total = int(count_match.group("capacity"))
    else:
        archive_match = _ARCHIVE_COUNT_RE.fullmatch(count_text)
        if not archive_match:
            raise HwasunContractError(f"course {identity}: capacity format changed")
        capacity_current = None
        capacity_total = int(archive_match.group("capacity"))
    # Historical count-only rows can legitimately advertise ``정원 0명``.
    # Current rows are detail-verified below and retain their stronger
    # list/detail cardinality contract.
    if capacity_total < 0 or (
        capacity_current is not None and capacity_current < 0
    ):
        raise HwasunContractError(f"course {identity}: invalid capacity")
    fee = _labelled_value(cells[5], "수강료/재료비", allow_empty=False)
    detail_url = urljoin(HWASUN_CANONICAL_URL, _clean(anchor.get("href")))
    if detail_url != hwasun_detail_url(identity):
        raise HwasunContractError(f"course {identity}: detail URL changed")

    return {
        "identity": identity,
        "source_page": page,
        "source_status": source_status,
        "status": status,
        "category": category,
        "title": title,
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "start_date": start,
        "end_date": end,
        "branch": branch,
        "target": target,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "fee": fee,
        "detail_url": detail_url,
        "_source_end_date": date.fromisoformat(end),
    }


def _parse_list_page(
    soup: BeautifulSoup,
    page: int,
    *,
    expected_total: Optional[int] = None,
    sentinel: bool = False,
) -> tuple[int, tuple[dict[str, Any], ...]]:
    _validate_page_shell(soup)
    total = _parse_total(soup)
    if expected_total is not None and total != expected_total:
        raise HwasunContractError(f"page {page}: declared total changed")
    table = _one(soup.select("table.le_list_table"), "course list table")
    caption = _one(table.select("caption"), "course list caption")
    if _text(caption) != _LIST_CAPTION:
        raise HwasunContractError("course list caption changed")
    headers = tuple(_text(node) for node in table.select("thead th"))
    if headers != _LIST_HEADERS:
        raise HwasunContractError("course list columns changed")
    body = _one(table.select("tbody"), "course list body")
    tr_nodes = body.find_all("tr", recursive=False)
    if not tr_nodes:
        raise HwasunContractError(f"page {page}: list body is empty without sentinel")

    if sentinel:
        if len(tr_nodes) != 1:
            raise HwasunContractError("post-last sentinel has multiple rows")
        cells = tr_nodes[0].find_all("td", recursive=False)
        if len(cells) != 1 or _text(cells[0]) != "강좌 정보가 없습니다.":
            raise HwasunContractError("post-last sentinel marker changed")
        if soup.select("div.pagination a.active"):
            raise HwasunContractError("post-last sentinel advertises an active page")
        return total, ()

    if any(
        len(row.find_all("td", recursive=False)) == 1
        and _text(row.find("td")) == "강좌 정보가 없습니다."
        for row in tr_nodes
    ):
        raise HwasunContractError(f"page {page}: unexpected empty marker")
    active = _one(soup.select("div.pagination a.active"), "active page marker")
    if _text(active) != str(page):
        raise HwasunContractError(f"page {page}: active page marker changed")
    rows = tuple(_parse_list_row(row, page) for row in tr_nodes)
    return total, rows


def _list_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("source_status")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("branch")),
            _clean(row.get("target")),
            _clean(row.get("fee")),
            row.get("capacity_current"),
            row.get("capacity_total"),
        )
        for row in rows
    )


def _safe_table_fields(
    table: Tag,
    *,
    expected_caption: str,
    allowed: frozenset[str],
    skipped: frozenset[str],
    identity: str,
) -> dict[str, str]:
    caption = _one(table.select("caption"), f"course {identity} detail caption")
    if _text(caption) != expected_caption:
        raise HwasunContractError(f"course {identity}: detail caption changed")
    result: dict[str, str] = {}
    for tr in table.select("tr"):
        th = tr.find("th", recursive=False)
        td = tr.find("td", recursive=False)
        if th is None or td is None:
            raise HwasunContractError(f"course {identity}: detail row structure changed")
        label = _text(th)
        if label in result:
            raise HwasunContractError(f"course {identity}: duplicate detail field {label}")
        if label in allowed:
            value = _text(td)
            if not value:
                raise HwasunContractError(f"course {identity}: safe field {label} is empty")
            result[label] = value
        elif label not in skipped:
            raise HwasunContractError(f"course {identity}: unknown detail field {label}")
        # Deliberately do not access the td text for skipped labels.
    missing = allowed - set(result)
    if missing:
        raise HwasunContractError(
            f"course {identity}: required safe detail fields missing ({', '.join(sorted(missing))})"
        )
    return result


def _validate_closed_control(soup: BeautifulSoup, identity: str) -> tuple[str, bool, str]:
    box = _one(soup.select("div.btn_box"), f"course {identity} control box")
    anchors = box.find_all("a", recursive=False)
    if len(anchors) != 2:
        raise HwasunContractError(f"course {identity}: closed controls changed")
    closed, listing = anchors
    if (
        _text(closed) != "모집마감"
        or _clean(closed.get("href")) != "javascript:void(0);"
        or "wait" not in set(closed.get("class", ()))
    ):
        raise HwasunContractError(f"course {identity}: closed status control changed")
    if (
        _text(listing) != "목록"
        or _clean(listing.get("href")) != "/lll/edu.do?S=lll&M=010101000000"
    ):
        raise HwasunContractError(f"course {identity}: list return control changed")
    return "", False, "closed_wait_control_on_identity_bound_detail"


def _validate_open_control(soup: BeautifulSoup, identity: str) -> tuple[str, bool, str]:
    box = _one(soup.select("div.btn_box"), f"course {identity} control box")
    anchors = box.find_all("a", recursive=False)
    if len(anchors) != 2:
        raise HwasunContractError(f"course {identity}: open controls changed")
    application, listing = anchors
    expected_url = hwasun_application_url(identity)
    if (
        _text(application) != "신청하기"
        or _clean(application.get("href")) != expected_url
        or "apply" not in set(application.get("class", ()))
    ):
        raise HwasunContractError(
            f"course {identity}: identity-bound application control changed"
        )
    if (
        _text(listing) != "목록"
        or _clean(listing.get("href")) != "/lll/edu.do?S=lll&M=010101000000"
    ):
        raise HwasunContractError(f"course {identity}: list return control changed")
    return expected_url, True, "open_mem_form_control_bound_to_official_list_no"


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    return f"HWASUN_{digest}"


def _application_type(method: str) -> str:
    compact = re.sub(r"\s+", "", method)
    if "온라인" in compact:
        return "ONLINE_RESERVATION"
    if any(token in compact for token in ("방문", "전화", "이메일")):
        return "OFFLINE_APPLY"
    return "INFO_ONLY"


def _parse_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    if _clean(listed.get("detail_url")) != hwasun_detail_url(identity):
        raise HwasunContractError(f"course {identity}: detail request identity changed")

    tables = list(soup.select("table.le_v_table"))
    if len(tables) != 3:
        raise HwasunContractError(f"course {identity}: expected three detail tables")
    roster = tables[2]
    if roster.find_parent("div", class_="bbox") is None:
        raise HwasunContractError(f"course {identity}: roster boundary changed")
    roster.extract()  # Must precede every text access outside the safe tables.

    title_node = _one(soup.select("div.le_v_title p"), f"course {identity} title")
    detail_title = _text(title_node)
    if detail_title != _clean(listed.get("title")):
        raise HwasunContractError(f"course {identity}: list/detail title differs")
    state_node = _one(
        soup.select("div.le_v_title span.state"), f"course {identity} detail state"
    )
    if _text(state_node) != _clean(listed.get("source_status")):
        raise HwasunContractError(f"course {identity}: list/detail state differs")
    status = _clean(listed.get("status"))
    if status not in {"OPEN", "CLOSED"}:
        raise HwasunContractError(f"course {identity}: unaudited detail status")

    schedule = _safe_table_fields(
        tables[0],
        expected_caption=_DETAIL_SCHEDULE_CAPTION,
        allowed=_SCHEDULE_ALLOWED,
        skipped=_SCHEDULE_SKIPPED,
        identity=identity,
    )
    safe = _safe_table_fields(
        tables[1],
        expected_caption=_DETAIL_SAFE_CAPTION,
        allowed=_DETAIL_ALLOWED,
        skipped=_DETAIL_SKIPPED,
        identity=identity,
    )

    apply_start, apply_end = _period(
        schedule["모집기간"], _PLAIN_PERIOD_RE, f"course {identity} detail application period"
    )
    start, end = _period(
        schedule["교육기간"], _PLAIN_PERIOD_RE, f"course {identity} detail education period"
    )
    expected_pairs = (
        (apply_start, _clean(listed.get("apply_start_date")), "application start"),
        (apply_end, _clean(listed.get("apply_end_date")), "application end"),
        (start, _clean(listed.get("start_date")), "education start"),
        (end, _clean(listed.get("end_date")), "education end"),
        (schedule["교육기관"], _clean(listed.get("branch")), "institution"),
        (safe["강좌분류"], _clean(listed.get("category")), "category"),
        (safe["교육대상"], _clean(listed.get("target")), "target"),
        (safe["수강료 / 재료비"], _clean(listed.get("fee")), "fee"),
    )
    for actual, expected, label in expected_pairs:
        if _clean(actual) != _clean(expected):
            raise HwasunContractError(f"course {identity}: list/detail {label} differs")
    if not schedule["교육기관"]:
        raise HwasunContractError(f"course {identity}: current institution is empty")

    count_match = _DETAIL_COUNT_RE.fullmatch(schedule["모집인원/정원"])
    if not count_match:
        raise HwasunContractError(f"course {identity}: detail capacity format changed")
    capacity_current = int(count_match.group("current"))
    capacity_total = int(count_match.group("capacity"))
    if (
        listed.get("capacity_current") is None
        or capacity_current != listed.get("capacity_current")
        or capacity_total != listed.get("capacity_total")
    ):
        raise HwasunContractError(f"course {identity}: list/detail capacity differs")

    if status == "OPEN":
        application_url, control_present, control_contract = _validate_open_control(
            soup, identity
        )
    else:
        application_url, control_present, control_contract = _validate_closed_control(
            soup, identity
        )
    method = schedule["접수방법"]
    application_kind = _application_type(method)
    if application_kind == "INFO_ONLY":
        raise HwasunContractError(f"course {identity}: application method changed")
    branch = schedule["교육기관"]
    raw_fields = {
        "parser": HWASUN_PARSER,
        "source_identity": identity,
        "source_page": int(listed.get("source_page") or 0),
        "source_category": safe["강좌분류"],
        "source_status": _clean(listed.get("source_status")),
        "source_application_period": schedule["모집기간"],
        "source_education_period": schedule["교육기간"],
        "source_application_method": method,
        "source_education_time": schedule["교육시간"],
        "source_institution": branch,
        "source_target": safe["교육대상"],
        "source_fee": safe["수강료 / 재료비"],
        "detail_verified": True,
        "list_detail_identity_verified": True,
        "application_control_present": control_present,
        "application_control_contract": control_contract,
        "application_control_verified": True,
        "applicant_roster_structurally_discarded": True,
        "service_family": "education",
    }
    return {
        "provider": HWASUN_PROVIDER,
        "provider_course_id": identity,
        "prefer_incoming_provider_course_id": True,
        "title": detail_title,
        "description": detail_title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "preserve_branch": True,
        "category": safe["강좌분류"],
        "program_type": "교육",
        "raw_url": hwasun_detail_url(identity),
        "application_url": application_url,
        "application_type": application_kind,
        "application_method_raw": method,
        "reservation_available": control_present,
        "status": status,
        "fee": safe["수강료 / 재료비"],
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "schedule_raw": schedule["교육시간"],
        "target": safe["교육대상"],
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "venue_name": branch,
        "collection_category": "공공교육",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_education",
        "collection_type": HWASUN_PARSER,
        "municipality_code": HWASUN_MUNICIPALITY_CODE,
        "municipality_name": HWASUN_MUNICIPALITY_NAME,
        "raw_fields": raw_fields,
    }


def _walk_values(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield _clean(key), child
            yield from _walk_values(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield "", child


def _validate_output(row: Mapping[str, Any]) -> None:
    unexpected = set(row) - _ALLOWED_ROW_KEYS
    if unexpected:
        raise HwasunContractError(
            "unexpected persisted fields: " + ", ".join(sorted(unexpected))
        )
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping):
        raise HwasunContractError("raw_fields must be a mapping")
    raw_unexpected = set(raw) - _ALLOWED_RAW_KEYS
    if raw_unexpected:
        raise HwasunContractError(
            "unexpected persisted raw fields: " + ", ".join(sorted(raw_unexpected))
        )
    forbidden_tokens = (
        "instructor", "teacher", "contact", "phone", "email", "attachment",
        "강사", "문의", "연락처", "첨부", "교육내용", "roster_html", "source_html",
    )
    for key, value in _walk_values(row):
        lowered = key.casefold()
        if any(token.casefold() in lowered for token in forbidden_tokens):
            raise HwasunContractError(f"forbidden persisted key: {key}")
        if isinstance(value, str) and (
            _PHONE_RE.search(value) or _EMAIL_RE.search(value)
        ):
            raise HwasunContractError("PII-like value reached persisted output")
    if row.get("description") != row.get("title"):
        raise HwasunContractError("free-form description reached output")
    if raw.get("service_family") != "education":
        raise HwasunContractError("non-education row reached output")


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            raise HwasunContractError("duplicate output identity")
        seen.add(identity)
        result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "http_attempts": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "network_concurrency": 1,
        "declared_total": 0,
        "data_pages": 0,
        "page_counts": {},
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "roster_sections_discarded": 0,
        "returned_count": 0,
        "identity_duplicate_count": 0,
        "raw_url_duplicate_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "non_monotonic_end_date_detected": False,
        "current_after_first_expired_ids": [],
        "page_2_or_later_current_ids": [],
        "configured_collection_error": "",
        "municipality_code": HWASUN_MUNICIPALITY_CODE,
        "municipality_name": HWASUN_MUNICIPALITY_NAME,
        "canonical_url": HWASUN_CANONICAL_URL,
        "ownership_scope": HWASUN_OWNERSHIP_SCOPE,
    }


def collect_hwasun_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 200,
    today: Optional[date | datetime | str] = None,
    fetch_attempts: int = HWASUN_FETCH_ATTEMPTS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    sleeper: Optional[Sleeper] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, current/future Hwasun education snapshot."""

    meta = _base_meta()
    if not is_hwasun_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact canonical Hwasun owner/query order"
        )
        return [], HWASUN_PARSER, meta
    integers = (timeout, max_pages, detail_limit, fetch_attempts)
    if (
        any(isinstance(value, bool) or not isinstance(value, int) for value in integers)
        or timeout < 1
        or max_pages < 1
        or detail_limit < 0
        or not 1 <= fetch_attempts <= 8
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], HWASUN_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], HWASUN_PARSER, meta

    client = _Client(
        timeout=timeout,
        attempts=fetch_attempts,
        session_factory=session_factory or _default_session_factory,
        fetcher=fetcher or _default_fetcher,
        sleeper=sleeper or time.sleep,
    )

    def update_network_meta() -> None:
        meta.update(
            {
                "http_attempts": client.http_attempts,
                "network_retry_count": client.retry_count,
                "sessions_created": client.sessions_created,
            }
        )

    try:
        first_total, first_rows = _parse_list_page(client.get(hwasun_list_url(1)), 1)
        meta.update({"list_requests": 1, "pages": 1, "declared_total": first_total})
        last_page = math.ceil(first_total / HWASUN_PAGE_SIZE)
        required_list_requests = last_page + 3
        meta["required_list_requests"] = required_list_requests
        if required_list_requests > max_pages:
            meta["source_cap_reached"] = True
            raise HwasunContractError(
                f"max_pages cap allows {max_pages} of {required_list_requests} required list requests"
            )

        page_rows: dict[int, tuple[dict[str, Any], ...]] = {1: first_rows}
        expected_final_count = first_total - (last_page - 1) * HWASUN_PAGE_SIZE
        if len(first_rows) != (expected_final_count if last_page == 1 else HWASUN_PAGE_SIZE):
            raise HwasunContractError("page 1 row count disagrees with declared total")
        for page in range(2, last_page + 1):
            _, rows = _parse_list_page(
                client.get(hwasun_list_url(page)),
                page,
                expected_total=first_total,
            )
            expected = expected_final_count if page == last_page else HWASUN_PAGE_SIZE
            if len(rows) != expected:
                raise HwasunContractError(
                    f"page {page}: row count {len(rows)} != declared {expected}"
                )
            page_rows[page] = rows
            meta["list_requests"] += 1
            meta["pages"] += 1

        sentinel_page = last_page + 1
        _, sentinel_rows = _parse_list_page(
            client.get(hwasun_list_url(sentinel_page)),
            sentinel_page,
            expected_total=first_total,
            sentinel=True,
        )
        if sentinel_rows:
            raise HwasunContractError("immediate post-last sentinel is not empty")
        meta.update(
            {
                "list_requests": meta["list_requests"] + 1,
                "pages": meta["pages"] + 1,
                "sentinel_requests": 1,
            }
        )

        _, first_recheck = _parse_list_page(
            client.get(hwasun_list_url(1)), 1, expected_total=first_total
        )
        _, last_recheck = _parse_list_page(
            client.get(hwasun_list_url(last_page)),
            last_page,
            expected_total=first_total,
        )
        meta.update(
            {
                "list_requests": meta["list_requests"] + 2,
                "pages": meta["pages"] + 2,
                "stability_rechecks": 2,
            }
        )
        if _list_signature(first_recheck) != _list_signature(first_rows):
            raise HwasunContractError("first-page stability recheck changed")
        if _list_signature(last_recheck) != _list_signature(page_rows[last_page]):
            raise HwasunContractError("last-page stability recheck changed")

        listed = [
            row
            for page in range(1, last_page + 1)
            for row in page_rows[page]
        ]
        identities = [_clean(row.get("identity")) for row in listed]
        duplicates = len(identities) - len(set(identities))
        meta["identity_duplicate_count"] = duplicates
        if len(listed) != first_total:
            raise HwasunContractError(
                f"parsed {len(listed)} rows but source declared {first_total}"
            )
        if duplicates:
            raise HwasunContractError(f"{duplicates} duplicate official identities")
        page_counts = {page: len(page_rows[page]) for page in page_rows}
        meta.update(
            {
                "data_pages": last_page,
                "page_counts": page_counts,
                "source_rows": len(listed),
                "pagination_complete": (
                    meta["list_requests"] == required_list_requests
                    and meta["sentinel_requests"] == 1
                    and meta["stability_rechecks"] == 2
                ),
            }
        )
        if not meta["pagination_complete"]:
            raise HwasunContractError("complete pagination contract was not satisfied")

        current_rows: list[dict[str, Any]] = []
        current_after_expired: list[str] = []
        expired_seen = False
        for row in listed:
            if row["_source_end_date"] >= cutoff:
                current_rows.append(row)
                if expired_seen:
                    current_after_expired.append(_clean(row.get("identity")))
            else:
                expired_seen = True
        page_2_or_later = [
            _clean(row.get("identity"))
            for row in current_rows
            if int(row.get("source_page") or 0) >= 2
        ]
        meta.update(
            {
                "current_source_count": len(current_rows),
                "expired_count": len(listed) - len(current_rows),
                "non_monotonic_end_date_detected": bool(current_after_expired),
                "current_after_first_expired_ids": current_after_expired,
                "page_2_or_later_current_ids": page_2_or_later,
            }
        )
        if len(current_rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise HwasunContractError(
                f"detail_limit cap allows {detail_limit} of {len(current_rows)} required details"
            )

        meta["detail_attempts"] = len(current_rows)
        detailed: list[dict[str, Any]] = []
        for listed_row in current_rows:
            identity = _clean(listed_row.get("identity"))
            try:
                parsed = _parse_detail(
                    listed_row, client.get(hwasun_detail_url(identity))
                )
            except Exception:
                meta["detail_errors"] += 1
                raise
            _validate_output(parsed)
            detailed.append(parsed)
            meta["detail_pages"] += 1
            meta["pages"] += 1
            meta["roster_sections_discarded"] += 1

        meta["details_complete"] = (
            meta["detail_attempts"] == meta["detail_pages"] == len(current_rows)
            and meta["detail_errors"] == 0
        )
        meta["application_controls_complete"] = bool(
            meta["details_complete"]
            and all(
                row["raw_fields"].get("application_control_verified") is True
                for row in detailed
            )
        )
        raw_urls = [_clean(row.get("raw_url")) for row in detailed]
        raw_url_duplicates = len(raw_urls) - len(set(raw_urls))
        meta["raw_url_duplicate_count"] = raw_url_duplicates
        if raw_url_duplicates:
            raise HwasunContractError(
                f"{raw_url_duplicates} duplicate current detail URLs"
            )

        deduper = dedupe_rows or _dedupe_default
        result = list(deduper(detailed))
        expected_ids = [_clean(row.get("provider_course_id")) for row in detailed]
        result_ids = [_clean(row.get("provider_course_id")) for row in result]
        if result_ids != expected_ids:
            raise HwasunContractError("dedupe changed official identity/order cardinality")

        snapshot_complete = bool(
            meta["pagination_complete"]
            and meta["details_complete"]
            and meta["application_controls_complete"]
        )
        if not snapshot_complete:
            raise HwasunContractError("complete snapshot contract was not satisfied")
        meta.update(
            {
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "returned_count": len(result),
                "no_current_data": not current_rows,
                "no_current_reason": (
                    "the complete official catalogue has no current/future courses"
                    if not current_rows
                    else ""
                ),
                "source_status_counts": dict(
                    Counter(_clean(row.get("source_status")) for row in listed)
                ),
                "current_status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "current_branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "current_detail_ids": result_ids,
                "semantic_duplicate_policy": "preserve_distinct_official_list_no",
                "municipality_coverage": [HWASUN_MUNICIPALITY_CODE],
                "owner_boundary_audit": {
                    key: dict(value) for key, value in HWASUN_OWNER_BOUNDARY_AUDIT.items()
                },
                "discovery_audit": dict(HWASUN_DISCOVERY_AUDIT),
                "pii_fields_never_read": list(HWASUN_PII_FIELDS_NEVER_READ),
                "pii_payload_persisted": False,
            }
        )
        update_network_meta()
        return result, HWASUN_PARSER, meta
    except Exception as exc:
        update_network_meta()
        meta["configured_collection_error"] = (
            f"{type(exc).__name__}: {_clean(exc)[:500]}"
        )
        meta["returned_count"] = 0
        return [], HWASUN_PARSER, meta
    finally:
        client.close()


collect = collect_hwasun_education


__all__ = [
    "HWASUN_BRANCH",
    "HWASUN_CANONICAL_URL",
    "HWASUN_DISCOVERY_AUDIT",
    "HWASUN_FETCH_ATTEMPTS",
    "HWASUN_FORESTTRIP_CANDIDATE_ID",
    "HWASUN_FORESTTRIP_PROVIDER",
    "HWASUN_FORESTTRIP_URL",
    "HWASUN_HFCT_CANDIDATE_ID",
    "HWASUN_HFCT_PROVIDER",
    "HWASUN_HFCT_URL",
    "HWASUN_HOME_PAGE_ALIAS_URL",
    "HWASUN_MEMCHECK_PII_URL",
    "HWASUN_MUNICIPALITY_CODE",
    "HWASUN_MUNICIPALITY_NAME",
    "HWASUN_OWNER_BOUNDARY_AUDIT",
    "HWASUN_OWNERSHIP_SCOPE",
    "HWASUN_PAGE_SIZE",
    "HWASUN_PARSER",
    "HWASUN_PII_FIELDS_NEVER_READ",
    "HWASUN_PROVIDER",
    "HwasunContractError",
    "collect",
    "collect_hwasun_education",
    "hwasun_application_url",
    "hwasun_detail_url",
    "hwasun_list_url",
    "is_hwasun_education_target",
    "is_hwasun_home_page_alias_target",
    "is_hwasun_memcheck_pii_alias_target",
    "is_hwasun_separate_or_excluded_owner_target",
]
