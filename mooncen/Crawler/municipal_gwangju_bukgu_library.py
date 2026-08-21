"""Fail-closed collector for the Gwangju Buk-gu integrated library catalogue.

The official source is one aggregate ledger shared by five Buk-gu libraries.
The facility registry contains five providers pointing at the same web site;
only the Jungheung provider is the executable owner here.  The other four
facility providers are non-executing aliases whose exact branch names remain
on individual course rows.

Every declared ten-row page, the immediate empty post-last page, and stable
first/last rechecks are required.  Only current/future courses are opened.
Detail parsing is deliberately limited to the first course-information
section and the identity-bound navigation form.  The free-form article and
the approval/waiting applicant rosters are structurally detached without
reading their text.  Instructor values are also skipped.

The Gwangju metropolitan reservation service and the Gwangju Cultural
Foundation are different operators and remain separate owners.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


GWANGJU_BUKGU_LIBRARY_PROVIDER = "CULTURE_PUBLIC_LIBRARY_E720241268"
GWANGJU_BUKGU_LIBRARY_MUNICIPALITY_CODE = "1230000000"
GWANGJU_BUKGU_LIBRARY_MUNICIPALITY_NAME = "전남광주통합특별시 북구"
GWANGJU_BUKGU_LIBRARY_HOST = "lib.bukgu.gwangju.kr"
GWANGJU_BUKGU_LIBRARY_PATH = "/main/cultureReq.do"
GWANGJU_BUKGU_LIBRARY_PID = "0401"
GWANGJU_BUKGU_LIBRARY_CANONICAL_URL = (
    "https://lib.bukgu.gwangju.kr/main/cultureReq.do?PID=0401"
)
GWANGJU_BUKGU_LIBRARY_PAGE_SIZE = 10
GWANGJU_BUKGU_LIBRARY_FETCH_ATTEMPTS = 2
GWANGJU_BUKGU_LIBRARY_MAX_HTML_BYTES = 2_000_000
GWANGJU_BUKGU_LIBRARY_PARSER = (
    "gwangju_bukgu_library_declared_total_all_pages+empty_post_last+"
    "stable_first_last+current_details+five_exact_branches+"
    "identity_bound_login_control+applicant_roster_never_read+pii_allowlist"
)
GWANGJU_BUKGU_LIBRARY_OWNERSHIP_SCOPE = (
    "gwangju_bukgu_integrated_library_five_branch_course_ledger"
)

GWANGJU_METROPOLITAN_BOOKING_PROVIDER = "MUNI_WWW_GWANGJU_GO_KR_82EF77CD"
GWANGJU_METROPOLITAN_BOOKING_CANDIDATE_ID = "MUNI_IR_9A23E8B5B35F"
GWANGJU_METROPOLITAN_BOOKING_URL = (
    "https://www.gwangju.go.kr/reserve/bookingList.do?"
    "pageId=reserve1&searchCate1=A&searchCate2=A03"
)
GWANGJU_CULTURAL_FOUNDATION_PROVIDER = "MUNI_WWW_GJCF_OR_KR_F9585EF3"
GWANGJU_CULTURAL_FOUNDATION_CANDIDATE_ID = "MUNI_IR_61D91EBA841D"
GWANGJU_CULTURAL_FOUNDATION_URL = (
    "https://www.gjcf.or.kr/cf/cultureart/list/calendar.do"
)

GWANGJU_BUKGU_LIBRARY_BRANCH_FILTERS: tuple[tuple[str, str], ...] = (
    ("", "전체"),
    ("J", "중흥도서관"),
    ("I", "일곡도서관"),
    ("U", "운암도서관"),
    ("Y", "양산도서관"),
    ("S", "신용도서관"),
)
GWANGJU_BUKGU_LIBRARY_BRANCHES = tuple(
    label for code, label in GWANGJU_BUKGU_LIBRARY_BRANCH_FILTERS if code
)
_BRANCH_FILTER_BY_NAME = {
    label: code for code, label in GWANGJU_BUKGU_LIBRARY_BRANCH_FILTERS if code
}


@dataclass(frozen=True)
class GwangjuBukguLibraryFacilityAlias:
    provider: str
    registry_name: str
    exact_branch: str
    registry_url: str
    execution_enabled: bool
    decision: str


GWANGJU_BUKGU_LIBRARY_FACILITY_ALIASES: tuple[
    GwangjuBukguLibraryFacilityAlias, ...
] = (
    GwangjuBukguLibraryFacilityAlias(
        GWANGJU_BUKGU_LIBRARY_PROVIDER,
        "중흥도서관",
        "중흥도서관",
        "https://lib.bukgu.gwangju.kr",
        True,
        "canonical aggregate owner; retarget registry seed to the course ledger",
    ),
    GwangjuBukguLibraryFacilityAlias(
        "CULTURE_PUBLIC_LIBRARY_791F2D10E0",
        "광주북구일곡도서관",
        "일곡도서관",
        "https://lib.bukgu.gwangju.kr/main.do",
        False,
        "same-site branch alias; duplicate of the aggregate ledger",
    ),
    GwangjuBukguLibraryFacilityAlias(
        "CULTURE_PUBLIC_LIBRARY_C78844E97B",
        "광주북구운암도서관",
        "운암도서관",
        "https://lib.bukgu.gwangju.kr/main.do",
        False,
        "same-site branch alias; duplicate of the aggregate ledger",
    ),
    GwangjuBukguLibraryFacilityAlias(
        "CULTURE_PUBLIC_LIBRARY_CC4691655C",
        "양산도서관",
        "양산도서관",
        "https://lib.bukgu.gwangju.kr/main.do",
        False,
        "same-site branch alias; duplicate of the aggregate ledger",
    ),
    GwangjuBukguLibraryFacilityAlias(
        "CULTURE_PUBLIC_LIBRARY_54FEC4AF2F",
        "신용도서관",
        "신용도서관",
        "https://lib.bukgu.gwangju.kr/main.do",
        False,
        "same-site branch alias; duplicate of the aggregate ledger",
    ),
)
GWANGJU_BUKGU_LIBRARY_NON_EXECUTING_ALIASES = tuple(
    item
    for item in GWANGJU_BUKGU_LIBRARY_FACILITY_ALIASES
    if not item.execution_enabled
)
GWANGJU_BUKGU_LIBRARY_ALIAS_PROVIDERS = frozenset(
    item.provider for item in GWANGJU_BUKGU_LIBRARY_NON_EXECUTING_ALIASES
)

GWANGJU_BUKGU_LIBRARY_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    GWANGJU_BUKGU_LIBRARY_PROVIDER: {
        "decision": "canonical_owner_for_all_five_library_branches",
        "operator": "전남광주통합특별시 북구통합도서관",
        "canonical_url": GWANGJU_BUKGU_LIBRARY_CANONICAL_URL,
        "exact_branches": GWANGJU_BUKGU_LIBRARY_BRANCHES,
        "non_executing_alias_providers": tuple(
            item.provider for item in GWANGJU_BUKGU_LIBRARY_NON_EXECUTING_ALIASES
        ),
    },
    GWANGJU_METROPOLITAN_BOOKING_PROVIDER: {
        "decision": "keep_separate_metropolitan_reservation_owner",
        "candidate_id": GWANGJU_METROPOLITAN_BOOKING_CANDIDATE_ID,
        "operator": "광주광역시",
        "url": GWANGJU_METROPOLITAN_BOOKING_URL,
        "reason": "A03 is a metropolitan booking category, not the district library ledger",
    },
    GWANGJU_CULTURAL_FOUNDATION_PROVIDER: {
        "decision": "keep_separate_cultural_foundation_owner",
        "candidate_id": GWANGJU_CULTURAL_FOUNDATION_CANDIDATE_ID,
        "operator": "광주문화재단",
        "url": GWANGJU_CULTURAL_FOUNDATION_URL,
        "reason": "foundation calendar ownership does not transfer to Buk-gu",
    },
}

GWANGJU_BUKGU_LIBRARY_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": GWANGJU_BUKGU_LIBRARY_CANONICAL_URL,
    "declared_total": 1530,
    "data_pages": 153,
    "page_counts": {page: 10 for page in range(1, 154)},
    "empty_sentinel_page": 154,
    "unique_identities": 1530,
    "first_page_recheck_stable": True,
    "source_status_counts": {
        "접수대기중": 6,
        "접수중": 9,
        "대기자접수중": 2,
        "접수마감": 1513,
    },
    "source_branch_counts": {
        "중흥도서관": 304,
        "양산도서관": 281,
        "신용도서관": 211,
        "일곡도서관": 373,
        "운암도서관": 361,
    },
    "current_or_future_rows": 26,
    "current_details_verified": 26,
    "current_status_counts": {
        "접수대기중": 6,
        "접수중": 9,
        "대기자접수중": 2,
        "접수마감": 9,
    },
    "current_branch_counts": {
        "중흥도서관": 12,
        "양산도서관": 2,
        "신용도서관": 5,
        "일곡도서관": 4,
        "운암도서관": 3,
    },
    "detail_instructor_fields_skipped": 14,
    "applicant_roster_sections_skipped": 26,
    "conclusion": (
        "execute one aggregate owner, preserve five exact branches, and keep "
        "the metropolitan and foundation catalogues outside this owner"
    ),
}

GWANGJU_BUKGU_LIBRARY_PII_FIELDS_NEVER_READ = (
    "강사명 값",
    "상세 자유본문",
    "신청승인 명단",
    "대기 명단",
    "신청자 이름/계정/연락처",
    "login form payload",
    "source HTML persistence",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GwangjuBukguLibraryContractError(ValueError):
    """Raised when the audited integrated-library contract changes."""


_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"^[1-9]\d*$")
_TOTAL_RE = re.compile(r"^전체\s*:\s*(?P<total>[\d,]+)건$")
_PERIOD_RE = re.compile(
    r"^강좌기간\s*:\s*(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_DETAIL_PERIOD_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})"
    r"(?:\s+(?P<schedule>.+))?$"
)
_APPLY_PERIOD_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s+"
    r"(?P<start_hour>[01]\d|2[0-3])시\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})\s+"
    r"(?P<end_hour>[01]\d|2[0-3])시$"
)
_CAPACITY_RE = re.compile(
    r"^(?P<capacity>\d+)명\s*\(대기\s*:\s*(?P<wait>\d+)명\)$"
)
_CURRENT_RE = re.compile(r"^(?P<current>\d+)명$")
_WAIT_CURRENT_RE = re.compile(r"^\((?P<current>\d+)명\)$")
_DETAIL_CAPACITY_RE = re.compile(
    r"^(?P<capacity>\d+)명\s*\((?P<wait>\d+)\)명$"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010|0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_PAGE_TITLE = (
    "전남광주통합특별시 북구통합도서관 - 문화마당 - 온라인 신청 및 조회"
)
_LIST_CAPTION = "강좌 프로그램 리스트"
_LIST_HEADERS = ("번호", "강좌정보", "강좌대상/인원", "현재접수현황", "상태")
_SOURCE_STATUS_MAP: Mapping[str, str] = {
    "접수대기중": "SCHEDULED",
    "접수중": "OPEN",
    "대기자접수중": "OPEN",
    "접수마감": "CLOSED",
}
_DETAIL_REQUIRED_FIELDS = frozenset(
    {
        "강좌대상",
        "수강기간",
        "접수시간",
        "수강인원(인터넷/대기)",
        "장소",
        "비용",
    }
)
_DETAIL_SKIPPED_FIELDS = frozenset({"강사명"})
_SAFE_RAW_FIELDS = frozenset(
    {
        "source_identity",
        "source_page",
        "source_number",
        "source_state",
        "source_branch_filter",
        "source_application_start",
        "source_application_end",
        "source_capacity",
        "source_capacity_current",
        "source_waitlist",
        "source_waitlist_current",
        "source_waitlist_mode",
        "list_schema_verified",
        "detail_schema_verified",
        "list_detail_verified",
        "application_control_present",
        "application_control_actionable",
        "login_gate_verified",
        "applicant_roster_skipped",
        "article_body_skipped",
        "instructor_value_skipped",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "teacher",
        "phone",
        "email",
        "contact",
        "article_body",
        "content",
        "applicants",
        "applicant_names",
        "approval_roster",
        "waiting_roster",
        "source_html",
        "form_payload",
    }
)
_ALLOWED_ROW_KEYS = frozenset(
    {
        "provider",
        "provider_course_id",
        "prefer_incoming_provider_course_id",
        "title",
        "branch",
        "branch_code",
        "preserve_branch",
        "provider_organizer",
        "period",
        "start_date",
        "end_date",
        "apply_period",
        "apply_start_date",
        "apply_end_date",
        "status",
        "category",
        "program_type",
        "domain_category",
        "collection_category",
        "collection_type",
        "source_group",
        "operator_type",
        "service_group",
        "service_group_policy",
        "target",
        "schedule_raw",
        "room",
        "venue",
        "fee",
        "capacity",
        "capacity_current",
        "capacity_total",
        "capacity_remaining",
        "waitlist_current",
        "waitlist_total",
        "application_method",
        "application_methods",
        "reservation_available",
        "application_url",
        "application_type",
        "raw_url",
        "source_url",
        "description",
        "raw_fields",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    if node is None:
        return ""
    return _clean(node.get_text(" ", strip=True))


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise ValueError("today must be an ISO date") from exc


def _query_parts(value: Any) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(_clean(value))
    query: dict[str, str] = {}
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if key in query:
            raise GwangjuBukguLibraryContractError(
                "duplicate URL query parameter"
            )
        query[key] = item
    return parsed, query


def _safe_origin(value: Any) -> tuple[Any, dict[str, str]]:
    try:
        parsed, query = _query_parts(value)
        port = parsed.port
    except (ValueError, GwangjuBukguLibraryContractError) as exc:
        raise GwangjuBukguLibraryContractError("malformed URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != GWANGJU_BUKGU_LIBRARY_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != GWANGJU_BUKGU_LIBRARY_PATH
        or parsed.params
        or parsed.fragment
    ):
        raise GwangjuBukguLibraryContractError(
            "URL escaped the audited library origin"
        )
    return parsed, query


def _is_canonical_url(value: Any) -> bool:
    try:
        _parsed, query = _safe_origin(value)
    except GwangjuBukguLibraryContractError:
        return False
    return query == {"PID": GWANGJU_BUKGU_LIBRARY_PID}


def is_gwangju_bukgu_library_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider"))
        == GWANGJU_BUKGU_LIBRARY_PROVIDER
        and _is_canonical_url(_target_value(target, "url"))
    )


is_target = is_gwangju_bukgu_library_target


def gwangju_bukgu_library_alias_for_target(
    target: Any,
) -> Optional[GwangjuBukguLibraryFacilityAlias]:
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url")).rstrip("/")
    for alias in GWANGJU_BUKGU_LIBRARY_FACILITY_ALIASES:
        if provider == alias.provider and url == alias.registry_url.rstrip("/"):
            return alias
    return None


def is_gwangju_bukgu_library_alias_target(target: Any) -> bool:
    alias = gwangju_bukgu_library_alias_for_target(target)
    return bool(alias is not None and not alias.execution_enabled)


def gwangju_bukgu_library_alias_metadata(target: Any) -> dict[str, Any]:
    alias = gwangju_bukgu_library_alias_for_target(target)
    if alias is None:
        return {}
    return {
        "facility_registry_alias": True,
        "execution_enabled": False,
        "alias_provider": alias.provider,
        "registry_name": alias.registry_name,
        "exact_branch": alias.exact_branch,
        "alias_url": alias.registry_url,
        "duplicate_of": GWANGJU_BUKGU_LIBRARY_PROVIDER,
        "canonical_provider": GWANGJU_BUKGU_LIBRARY_PROVIDER,
        "canonical_url": GWANGJU_BUKGU_LIBRARY_CANONICAL_URL,
        "reason": alias.decision,
    }


def gwangju_bukgu_library_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if page == 1:
        return GWANGJU_BUKGU_LIBRARY_CANONICAL_URL
    return GWANGJU_BUKGU_LIBRARY_CANONICAL_URL + "&" + urlencode(
        (("searchText", ""), ("iType", ""), ("page", str(page)))
    )


def gwangju_bukgu_library_detail_url(identity: Any, page: int = 1) -> str:
    identity_text = _clean(identity)
    if (
        not _POSITIVE_ID_RE.fullmatch(identity_text)
        or isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
    ):
        raise ValueError("identity/page are invalid")
    return GWANGJU_BUKGU_LIBRARY_CANONICAL_URL + "&" + urlencode(
        (
            ("action", "View"),
            ("idx", identity_text),
            ("page", str(page)),
            ("searchType", ""),
            ("searchText", ""),
        )
    )


def _validate_detail_url(value: Any, page: int) -> str:
    _parsed, query = _safe_origin(value)
    if set(query) != {
        "PID",
        "action",
        "idx",
        "page",
        "searchType",
        "searchText",
    }:
        raise GwangjuBukguLibraryContractError("detail query changed")
    identity = query.get("idx", "")
    if (
        query.get("PID") != GWANGJU_BUKGU_LIBRARY_PID
        or query.get("action") != "View"
        or query.get("page") != str(page)
        or query.get("searchType") != ""
        or query.get("searchText") != ""
        or not _POSITIVE_ID_RE.fullmatch(identity)
    ):
        raise GwangjuBukguLibraryContractError("detail identity/query changed")
    canonical = gwangju_bukgu_library_detail_url(identity, page)
    if _query_parts(canonical)[1] != query:
        raise GwangjuBukguLibraryContractError("detail URL is not canonical")
    return identity


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": GWANGJU_BUKGU_LIBRARY_CANONICAL_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise GwangjuBukguLibraryContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise GwangjuBukguLibraryContractError("HTTP redirect is not accepted")
    content = getattr(response, "content", b"")
    if not content:
        raise GwangjuBukguLibraryContractError("empty HTTP response")
    if len(content) > GWANGJU_BUKGU_LIBRARY_MAX_HTML_BYTES:
        raise GwangjuBukguLibraryContractError(
            "HTTP response exceeded HTML byte cap"
        )
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        content = value
    elif isinstance(value, str):
        content = value.encode("utf-8")
    else:
        content = getattr(value, "content", None)
        if content is None:
            text = getattr(value, "text", None)
            content = text.encode("utf-8") if isinstance(text, str) else None
        if content is None:
            raise TypeError("fetcher returned neither HTML nor response")
    if not content:
        raise GwangjuBukguLibraryContractError("empty HTML")
    if len(content) > GWANGJU_BUKGU_LIBRARY_MAX_HTML_BYTES:
        raise GwangjuBukguLibraryContractError("HTML exceeded byte cap")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _Client:
    def __init__(
        self,
        *,
        timeout: int,
        fetcher: Fetcher,
        session_factory: SessionFactory,
    ) -> None:
        self.timeout = timeout
        self.fetcher = fetcher
        self.session = session_factory()
        self.requests = 0
        self.sessions_created = 1

    def get(self, url: str) -> BeautifulSoup:
        last_error: Optional[Exception] = None
        for _attempt in range(GWANGJU_BUKGU_LIBRARY_FETCH_ATTEMPTS):
            try:
                self.requests += 1
                return _coerce_soup(
                    self.fetcher(self.session, url, self.timeout)
                )
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def close(self) -> None:
        _close_quietly(self.session)


def _one(nodes: Iterable[Any], label: str) -> Any:
    result = list(nodes)
    if len(result) != 1:
        raise GwangjuBukguLibraryContractError(f"{label} changed")
    return result[0]


def _validate_title(soup: BeautifulSoup) -> None:
    title = _one(soup.select("head > title"), "document title")
    if _text(title) != _PAGE_TITLE:
        raise GwangjuBukguLibraryContractError("document title changed")


def _validate_search_form(soup: BeautifulSoup) -> None:
    form = _one(soup.select("form#searchForm"), "search form")
    if (
        _clean(form.get("method")).lower() != "post"
        or urljoin(GWANGJU_BUKGU_LIBRARY_CANONICAL_URL, form.get("action", ""))
        != GWANGJU_BUKGU_LIBRARY_CANONICAL_URL
    ):
        raise GwangjuBukguLibraryContractError("search form changed")
    fields = [
        (_clean(node.get("name")), _clean(node.get("value")))
        for node in form.find_all("input")
    ]
    if fields and fields[0][0] == "CSRFToken":
        if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", fields[0][1]) is None:
            raise GwangjuBukguLibraryContractError("search CSRF token changed")
        fields = fields[1:]
    if fields != [("searchType", ""), ("searchText", "")]:
        raise GwangjuBukguLibraryContractError("search fields changed")


def _validate_branch_tabs(soup: BeautifulSoup) -> None:
    nodes = soup.select("article a.linkColor[href]")
    actual: list[tuple[str, str]] = []
    for node in nodes:
        label = _text(node)
        if label not in {item[1] for item in GWANGJU_BUKGU_LIBRARY_BRANCH_FILTERS}:
            continue
        absolute = urljoin(
            GWANGJU_BUKGU_LIBRARY_CANONICAL_URL, _clean(node.get("href"))
        )
        actual.append((label, absolute))
    expected = []
    for code, label in GWANGJU_BUKGU_LIBRARY_BRANCH_FILTERS:
        url = GWANGJU_BUKGU_LIBRARY_CANONICAL_URL
        if code:
            url += "&" + urlencode((("iType", code),))
        expected.append((label, url))
    if actual != expected:
        raise GwangjuBukguLibraryContractError("five-library tabs changed")


def _parse_total(soup: BeautifulSoup) -> int:
    nodes = soup.select("article div.row > div.col.col-3")
    matches = []
    for node in nodes:
        match = _TOTAL_RE.fullmatch(_text(node))
        if match:
            matches.append(int(match.group("total").replace(",", "")))
    if len(matches) != 1 or matches[0] < 1:
        raise GwangjuBukguLibraryContractError("declared total changed")
    return matches[0]


def _direct_text_parts(node: Tag) -> list[str]:
    return [
        _clean(child)
        for child in node.children
        if isinstance(child, NavigableString) and _clean(child)
    ]


def _parse_list_row(row: Tag, page: int) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != len(_LIST_HEADERS):
        raise GwangjuBukguLibraryContractError(
            f"page {page} list cell count changed"
        )
    number_text = _text(cells[0])
    if not _POSITIVE_ID_RE.fullmatch(number_text):
        raise GwangjuBukguLibraryContractError(
            f"page {page} source number changed"
        )

    program = cells[1]
    branch_node = _one(
        [
            node
            for node in program.find_all("span", recursive=False)
            if "label" in (node.get("class") or [])
            and len(node.get("class") or []) == 2
            and re.fullmatch(
                r"label-lecture\d+",
                next(
                    (
                        item
                        for item in (node.get("class") or [])
                        if item != "label"
                    ),
                    "",
                ),
            )
        ],
        f"page {page} branch label",
    )
    title_node = _one(
        program.select(":scope > a.title[href]"), f"page {page} title link"
    )
    period_node = _one(
        program.select(":scope > p.desc"), f"page {page} period"
    )
    branch = _text(branch_node)
    if branch not in GWANGJU_BUKGU_LIBRARY_BRANCHES:
        raise GwangjuBukguLibraryContractError(
            f"page {page} branch is not one of the five exact names"
        )
    title = _text(title_node)
    if not title:
        raise GwangjuBukguLibraryContractError(f"page {page} title is empty")
    period_match = _PERIOD_RE.fullmatch(_text(period_node))
    if not period_match:
        raise GwangjuBukguLibraryContractError(
            f"page {page} course period changed"
        )
    start = period_match.group("start")
    end = period_match.group("end")
    try:
        start_day = date.fromisoformat(start)
        end_day = date.fromisoformat(end)
    except ValueError as exc:
        raise GwangjuBukguLibraryContractError(
            f"page {page} course date is invalid"
        ) from exc
    if end_day < start_day:
        raise GwangjuBukguLibraryContractError(
            f"page {page} course period is reversed"
        )

    raw_url = urljoin(
        GWANGJU_BUKGU_LIBRARY_CANONICAL_URL, _clean(title_node.get("href"))
    )
    identity = _validate_detail_url(raw_url, page)
    status_node = _one(
        cells[4].select(":scope > a.title[href]"), f"page {page} status link"
    )
    status_url = urljoin(
        GWANGJU_BUKGU_LIBRARY_CANONICAL_URL, _clean(status_node.get("href"))
    )
    if _query_parts(status_url)[1] != _query_parts(raw_url)[1]:
        raise GwangjuBukguLibraryContractError(
            f"page {page} status/detail identity mismatch"
        )
    source_state = _text(status_node)
    if source_state not in _SOURCE_STATUS_MAP:
        raise GwangjuBukguLibraryContractError(
            f"page {page} source state changed"
        )

    target_parts = list(cells[2].stripped_strings)
    current_parts = list(cells[3].stripped_strings)
    target_parts = [_clean(item) for item in target_parts]
    current_parts = [_clean(item) for item in current_parts]
    if len(target_parts) != 2 or not target_parts[0]:
        raise GwangjuBukguLibraryContractError(
            f"page {page} target/capacity shape changed"
        )
    capacity_match = _CAPACITY_RE.fullmatch(target_parts[1])
    if not capacity_match or len(current_parts) not in {1, 2}:
        raise GwangjuBukguLibraryContractError(
            f"page {page} capacity shape changed"
        )
    current_match = _CURRENT_RE.fullmatch(current_parts[0])
    waitlist = int(capacity_match.group("wait"))
    wait_current_match = (
        _WAIT_CURRENT_RE.fullmatch(current_parts[1])
        if len(current_parts) == 2
        else None
    )
    if (
        not current_match
        or (waitlist > 0 and not wait_current_match)
        or (waitlist == 0 and len(current_parts) == 2 and not wait_current_match)
    ):
        raise GwangjuBukguLibraryContractError(
            f"page {page} current-capacity shape changed"
        )
    capacity = int(capacity_match.group("capacity"))
    capacity_current = int(current_match.group("current"))
    waitlist_current = (
        int(wait_current_match.group("current")) if wait_current_match else 0
    )
    return {
        "identity": identity,
        "source_number": int(number_text),
        "source_page": page,
        "title": title,
        "branch": branch,
        "target": target_parts[0],
        "start_date": start,
        "end_date": end,
        "start_day": start_day,
        "end_day": end_day,
        "source_state": source_state,
        "status": _SOURCE_STATUS_MAP[source_state],
        "capacity": capacity,
        "capacity_current": capacity_current,
        "waitlist": waitlist,
        "waitlist_current": waitlist_current,
        "raw_url": raw_url,
    }


def _parse_list_page(
    soup: BeautifulSoup, page: int
) -> tuple[int, list[dict[str, Any]]]:
    _validate_title(soup)
    _validate_search_form(soup)
    _validate_branch_tabs(soup)
    total = _parse_total(soup)
    table = _one(soup.select("article table"), f"page {page} list table")
    caption = _one(table.find_all("caption", recursive=False), "list caption")
    if _text(caption) != _LIST_CAPTION:
        raise GwangjuBukguLibraryContractError("list caption changed")
    headers = tuple(_text(node) for node in table.select("thead > tr > th"))
    if headers != _LIST_HEADERS:
        raise GwangjuBukguLibraryContractError("list headers changed")
    tbody = _one(table.find_all("tbody", recursive=False), "list tbody")
    rows = [
        _parse_list_row(row, page)
        for row in tbody.find_all("tr", recursive=False)
    ]
    if rows:
        active = _one(
            soup.select("article a.btn.btn-white.active"),
            f"page {page} active pager",
        )
        if _text(active) != str(page):
            raise GwangjuBukguLibraryContractError(
                f"page {page} active pager changed"
            )
    return total, rows


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    payload = repr(
        [
            (
                _clean(row.get("identity")),
                int(row.get("source_number") or 0),
                _clean(row.get("title")),
                _clean(row.get("branch")),
                _clean(row.get("end_date")),
                _clean(row.get("source_state")),
            )
            for row in rows
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_detail_form(form: Tag, identity: str) -> None:
    if (
        _clean(form.get("method")).lower() != "post"
        or urljoin(GWANGJU_BUKGU_LIBRARY_CANONICAL_URL, form.get("action", ""))
        != GWANGJU_BUKGU_LIBRARY_CANONICAL_URL
    ):
        raise GwangjuBukguLibraryContractError("detail navigation form changed")
    fields: dict[str, str] = {}
    # Only the form's direct navigation fields are safe.  Never descend into
    # boardRead, where the server renders applicant rosters below the course.
    for node in form.find_all("input", recursive=False):
        name = _clean(node.get("name"))
        if not name or name in fields:
            raise GwangjuBukguLibraryContractError(
                "detail navigation fields changed"
            )
        if _clean(node.get("type")).lower() != "hidden":
            raise GwangjuBukguLibraryContractError(
                "detail navigation form exposed a personal field"
            )
        fields[name] = _clean(node.get("value"))
    csrf_token = fields.pop("CSRFToken", "")
    if csrf_token and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", csrf_token) is None:
        raise GwangjuBukguLibraryContractError(
            "detail navigation CSRF token changed"
        )
    if fields != {
        "iType": "",
        "searchText": "",
        "idx": identity,
        "action": "Next",
    }:
        raise GwangjuBukguLibraryContractError(
            "detail navigation identity changed"
        )


def _discard_forbidden_detail_sections(
    root: Tag,
) -> tuple[Tag, Tag, bool, bool]:
    """Detach unsafe content structurally without reading its descendants."""

    sections = root.find_all("section", recursive=False)
    if len(sections) != 3:
        raise GwangjuBukguLibraryContractError("detail section boundary changed")
    if (
        sections[0].get("class") != ["styleguide"]
        or sections[1].get("class") != ["articleBody"]
        or sections[2].get("class") != ["styleguide"]
    ):
        raise GwangjuBukguLibraryContractError("detail section classes changed")
    safe_information = sections[0]
    article = sections[1]
    applicant_roster = sections[2]
    article.extract()
    applicant_roster.extract()
    return safe_information, root, True, True


def _allowed_detail_value(item: Tag, label: str) -> str:
    if label not in _DETAIL_REQUIRED_FIELDS:
        raise GwangjuBukguLibraryContractError(
            "attempted to read a non-allowlisted detail value"
        )
    values = _direct_text_parts(item)
    if len(values) != 1:
        raise GwangjuBukguLibraryContractError(
            f"detail field {label} changed"
        )
    return values[0].lstrip(":").strip()


def _parse_detail(
    parent: Mapping[str, Any], soup: BeautifulSoup, target: Any
) -> tuple[dict[str, Any], int]:
    identity = _clean(parent.get("identity"))
    _validate_title(soup)
    form = _one(soup.select("form#writeForm"), f"course {identity} form")
    _validate_detail_form(form, identity)
    root = _one(
        form.select(":scope > div.boardRead"), f"course {identity} detail root"
    )
    safe_section, safe_root, article_skipped, roster_skipped = (
        _discard_forbidden_detail_sections(root)
    )

    title_parts = [
        _clean(child)
        for child in safe_root.children
        if isinstance(child, NavigableString) and _clean(child)
    ]
    if len(title_parts) != 1 or title_parts[0] != _clean(parent.get("title")):
        raise GwangjuBukguLibraryContractError(
            f"course {identity} detail title changed"
        )
    branch_node = _one(
        safe_root.find_all("span", recursive=False),
        f"course {identity} branch",
    )
    branch = _text(branch_node)
    if (
        branch != _clean(parent.get("branch"))
        or branch not in GWANGJU_BUKGU_LIBRARY_BRANCHES
    ):
        raise GwangjuBukguLibraryContractError(
            f"course {identity} detail branch changed"
        )

    fields: dict[str, str] = {}
    skipped_instructor = 0
    items = safe_section.select(
        ":scope > div > div.row > div.col > ul > li"
    )
    if not items:
        raise GwangjuBukguLibraryContractError(
            f"course {identity} safe detail fields are empty"
        )
    for item in items:
        label_node = _one(
            item.find_all("strong", recursive=False),
            f"course {identity} detail label",
        )
        label = _text(label_node)
        if label in _DETAIL_SKIPPED_FIELDS:
            skipped_instructor += 1
            continue
        if label not in _DETAIL_REQUIRED_FIELDS or label in fields:
            raise GwangjuBukguLibraryContractError(
                f"course {identity} detail labels changed"
            )
        fields[label] = _allowed_detail_value(item, label)
    if set(fields) != _DETAIL_REQUIRED_FIELDS or skipped_instructor > 1:
        raise GwangjuBukguLibraryContractError(
            f"course {identity} detail field set changed"
        )

    period_match = _DETAIL_PERIOD_RE.fullmatch(fields["수강기간"])
    apply_match = _APPLY_PERIOD_RE.fullmatch(fields["접수시간"])
    capacity_match = _DETAIL_CAPACITY_RE.fullmatch(
        fields["수강인원(인터넷/대기)"]
    )
    if not period_match or not apply_match or not capacity_match:
        raise GwangjuBukguLibraryContractError(
            f"course {identity} detail date/capacity format changed"
        )
    if (
        period_match.group("start") != parent.get("start_date")
        or period_match.group("end") != parent.get("end_date")
        or int(capacity_match.group("capacity"))
        != int(parent.get("capacity") or 0)
        or int(capacity_match.group("wait"))
        != int(parent.get("waitlist") or 0)
        or fields["강좌대상"] != _clean(parent.get("target"))
    ):
        raise GwangjuBukguLibraryContractError(
            f"course {identity} list/detail values differ"
        )
    apply_start = (
        f"{apply_match.group('start')} "
        f"{apply_match.group('start_hour')}:00"
    )
    apply_end = (
        f"{apply_match.group('end')} {apply_match.group('end_hour')}:00"
    )
    try:
        datetime.fromisoformat(apply_start)
        datetime.fromisoformat(apply_end)
    except ValueError as exc:
        raise GwangjuBukguLibraryContractError(
            f"course {identity} application period is invalid"
        ) from exc
    if apply_end < apply_start:
        raise GwangjuBukguLibraryContractError(
            f"course {identity} application period is reversed"
        )

    footer = _one(
        safe_root.find_all("footer", recursive=False),
        f"course {identity} footer",
    )
    controls = footer.find_all(["button", "a"], recursive=False)
    if len(controls) != 2:
        raise GwangjuBukguLibraryContractError(
            f"course {identity} controls changed"
        )
    login_button, list_link = controls
    if (
        login_button.name != "button"
        or login_button.get("id") != "nloginBtn"
        or login_button.get("class") != ["btn", "btn-edit"]
        or _clean(login_button.get("type")).lower() != "button"
        or _text(login_button) != "신청하기"
        or list_link.name != "a"
        or list_link.get("class") != ["btn", "btn-gray"]
        or _text(list_link) != "목록"
    ):
        raise GwangjuBukguLibraryContractError(
            f"course {identity} controls changed"
        )
    list_destination = urljoin(
        GWANGJU_BUKGU_LIBRARY_CANONICAL_URL, _clean(list_link.get("href"))
    )
    _parsed, list_query = _safe_origin(list_destination)
    if list_query != {
        "PID": GWANGJU_BUKGU_LIBRARY_PID,
        "iType": "",
        "searchText": "",
    }:
        raise GwangjuBukguLibraryContractError(
            f"course {identity} return link changed"
        )
    gate_scripts = []
    for script in soup.find_all("script", src=False):
        script_text = script.string
        if isinstance(script_text, str) and "nloginBtn" in script_text:
            gate_scripts.append(script_text)
    if len(gate_scripts) != 1:
        raise GwangjuBukguLibraryContractError(
            f"course {identity} login control script changed"
        )
    compact_script = re.sub(r"\s+", "", gate_scripts[0])
    if (
        '$("#nloginBtn").click(function(){' not in compact_script
        or 'location.href="/main/login.do?PID=9901";'
        not in compact_script
    ):
        raise GwangjuBukguLibraryContractError(
            f"course {identity} login destination changed"
        )

    status = _clean(parent.get("status"))
    actionable = status == "OPEN"
    waitlist_mode = _clean(parent.get("source_state")) == "대기자접수중"
    raw_url = _clean(parent.get("raw_url"))
    capacity = int(parent.get("capacity") or 0)
    capacity_current = int(parent.get("capacity_current") or 0)
    row = {
        "provider": GWANGJU_BUKGU_LIBRARY_PROVIDER,
        "provider_course_id": (
            f"{GWANGJU_BUKGU_LIBRARY_PROVIDER}:{identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": _clean(parent.get("title")),
        "branch": branch,
        "branch_code": _BRANCH_FILTER_BY_NAME[branch],
        "preserve_branch": True,
        "provider_organizer": branch,
        "period": (
            f"{parent.get('start_date')} ~ {parent.get('end_date')}"
        ),
        "start_date": _clean(parent.get("start_date")),
        "end_date": _clean(parent.get("end_date")),
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start_date": apply_match.group("start"),
        "apply_end_date": apply_match.group("end"),
        "status": status,
        "category": "교육",
        "program_type": "도서관 문화프로그램",
        "domain_category": "교육",
        "collection_category": "교육·체험",
        "collection_type": (
            "official_complete_declared_total_table+safe_detail_allowlist"
        ),
        "source_group": "library",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "target": _clean(parent.get("target")),
        "schedule_raw": _clean(period_match.group("schedule")),
        "room": fields["장소"],
        "venue": fields["장소"],
        "fee": fields["비용"],
        "capacity": capacity,
        "capacity_current": capacity_current,
        "capacity_total": capacity,
        "capacity_remaining": max(0, capacity - capacity_current),
        "waitlist_current": int(parent.get("waitlist_current") or 0),
        "waitlist_total": int(parent.get("waitlist") or 0),
        "application_method": "온라인 신청(로그인 필요)",
        "application_methods": ["온라인"],
        "reservation_available": actionable,
        "application_url": raw_url if actionable else "",
        "application_type": "ONLINE_RESERVATION" if actionable else "",
        "raw_url": raw_url,
        "source_url": _clean(_target_value(target, "url")),
        "description": _clean(parent.get("title")),
        "raw_fields": {
            "source_identity": identity,
            "source_page": int(parent.get("source_page") or 0),
            "source_number": int(parent.get("source_number") or 0),
            "source_state": _clean(parent.get("source_state")),
            "source_branch_filter": _BRANCH_FILTER_BY_NAME[branch],
            "source_application_start": apply_start,
            "source_application_end": apply_end,
            "source_capacity": capacity,
            "source_capacity_current": capacity_current,
            "source_waitlist": int(parent.get("waitlist") or 0),
            "source_waitlist_current": int(
                parent.get("waitlist_current") or 0
            ),
            "source_waitlist_mode": waitlist_mode,
            "list_schema_verified": True,
            "detail_schema_verified": True,
            "list_detail_verified": True,
            "application_control_present": True,
            "application_control_actionable": actionable,
            "login_gate_verified": True,
            "applicant_roster_skipped": roster_skipped,
            "article_body_skipped": article_skipped,
            "instructor_value_skipped": bool(skipped_instructor),
        },
    }
    _validate_persisted_row(row)
    return row, skipped_instructor


def _walk_values(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for child in value:
            yield from _walk_values(child)
    elif isinstance(value, str):
        yield value


def _validate_persisted_row(row: Mapping[str, Any]) -> None:
    unknown = set(row) - _ALLOWED_ROW_KEYS
    if unknown:
        raise GwangjuBukguLibraryContractError(
            f"persisted row exposed unknown fields: {sorted(unknown)}"
        )
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or set(raw) != _SAFE_RAW_FIELDS:
        raise GwangjuBukguLibraryContractError(
            "raw_fields escaped the positive allowlist"
        )
    nested_keys: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                nested_keys.add(_clean(key).casefold())
                visit(child)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for child in value:
                visit(child)

    visit(row)
    if nested_keys & _FORBIDDEN_PERSISTED_KEYS:
        raise GwangjuBukguLibraryContractError(
            "persisted row contains a forbidden PII key"
        )
    for value in _walk_values(row):
        if _PHONE_RE.search(value) or _EMAIL_RE.search(value):
            raise GwangjuBukguLibraryContractError(
                "persisted row contains a contact value"
            )


def _default_dedupe(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "sessions_created": 0,
        "source_count": 1,
        "source_total": 0,
        "source_rows": 0,
        "page_counts": {},
        "data_pages": 0,
        "declared_total_pages": 0,
        "sentinel_page": 0,
        "sentinel_pages": 0,
        "required_list_requests": 0,
        "list_requests": 0,
        "list_rechecks": 0,
        "current_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_status_counts": {},
        "normalized_status_counts": {},
        "source_branch_counts": {},
        "current_branch_counts": {},
        "application_control_count": 0,
        "actionable_application_count": 0,
        "applicant_roster_sections_skipped": 0,
        "article_body_sections_skipped": 0,
        "instructor_values_skipped": 0,
        "identity_duplicate_count": 0,
        "semantic_duplicate_count": 0,
        "capacity_overflow_count": 0,
        "waitlist_overflow_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "pii_boundaries_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
    }


def _failure(message: str, **updates: Any) -> dict[str, Any]:
    meta = _base_meta()
    meta.update(updates)
    meta["configured_collection_error"] = message
    return meta


def collect_gwangju_bukgu_library_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 160,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete current/future five-library snapshot."""

    if not is_gwangju_bukgu_library_target(target):
        return [], GWANGJU_BUKGU_LIBRARY_PARSER, _failure(
            "target does not match the canonical Buk-gu library owner/list"
        )
    try:
        timeout_value = int(timeout)
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        cutoff = _today(today)
        if (
            isinstance(timeout, bool)
            or isinstance(max_pages, bool)
            or isinstance(detail_limit, bool)
            or timeout_value <= 0
            or allowed_pages < 2
            or allowed_details < 0
        ):
            raise ValueError
    except (TypeError, ValueError):
        return [], GWANGJU_BUKGU_LIBRARY_PARSER, _failure(
            "timeout/max_pages/detail_limit/today are invalid"
        )

    try:
        client = _Client(
            timeout=timeout_value,
            fetcher=fetcher or _default_fetcher,
            session_factory=session_factory or _default_session_factory,
        )
    except Exception as exc:
        return [], GWANGJU_BUKGU_LIBRARY_PARSER, _failure(
            f"client setup: {type(exc).__name__}: {exc}"
        )

    errors: list[str] = []
    source_cap_reached = False
    all_rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    source_total = 0
    total_pages = 0
    sentinel_page = 0
    sentinel_pages = 0
    list_requests = 0
    list_rechecks = 0
    first_rows: list[dict[str, Any]] = []
    last_rows: list[dict[str, Any]] = []
    detail_attempts = 0
    detail_pages = 0
    instructor_skips = 0
    detailed_rows: list[dict[str, Any]] = []

    try:
        try:
            first = client.get(GWANGJU_BUKGU_LIBRARY_CANONICAL_URL)
            list_requests += 1
            source_total, first_rows = _parse_list_page(first, 1)
            if len(first_rows) != min(
                GWANGJU_BUKGU_LIBRARY_PAGE_SIZE, source_total
            ):
                raise GwangjuBukguLibraryContractError(
                    "first page row count differs from declared total"
                )
            total_pages = math.ceil(
                source_total / GWANGJU_BUKGU_LIBRARY_PAGE_SIZE
            )
            sentinel_page = total_pages + 1
            all_rows.extend(first_rows)
            page_counts[1] = len(first_rows)
        except Exception as exc:
            errors.append(f"page 1: {type(exc).__name__}: {exc}")

        required_list_requests = total_pages + 3 if total_pages else 0
        if not errors and sentinel_page > allowed_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages}; sentinel page "
                f"{sentinel_page} is required"
            )

        if not errors:
            for page in range(2, total_pages + 1):
                try:
                    soup = client.get(gwangju_bukgu_library_list_url(page))
                    list_requests += 1
                    declared, rows = _parse_list_page(soup, page)
                    if declared != source_total:
                        raise GwangjuBukguLibraryContractError(
                            f"page {page} declared total drifted"
                        )
                    expected = (
                        GWANGJU_BUKGU_LIBRARY_PAGE_SIZE
                        if page < total_pages
                        else source_total
                        - GWANGJU_BUKGU_LIBRARY_PAGE_SIZE * (total_pages - 1)
                    )
                    if len(rows) != expected:
                        raise GwangjuBukguLibraryContractError(
                            f"page {page} expected {expected} rows, got "
                            f"{len(rows)}"
                        )
                    all_rows.extend(rows)
                    page_counts[page] = len(rows)
                    if page == total_pages:
                        last_rows = rows
                except Exception as exc:
                    errors.append(
                        f"page {page}: {type(exc).__name__}: {exc}"
                    )
                    break
        if total_pages == 1:
            last_rows = first_rows

        if not errors:
            try:
                sentinel = client.get(
                    gwangju_bukgu_library_list_url(sentinel_page)
                )
                list_requests += 1
                declared, rows = _parse_list_page(sentinel, sentinel_page)
                if declared != source_total or rows:
                    raise GwangjuBukguLibraryContractError(
                        "immediate post-last page is not the empty sentinel"
                    )
                sentinel_pages = 1
            except Exception as exc:
                errors.append(
                    f"sentinel page {sentinel_page}: "
                    f"{type(exc).__name__}: {exc}"
                )

        if not errors:
            try:
                first_recheck = client.get(
                    GWANGJU_BUKGU_LIBRARY_CANONICAL_URL
                )
                list_requests += 1
                declared, rows = _parse_list_page(first_recheck, 1)
                if (
                    declared != source_total
                    or _page_signature(rows) != _page_signature(first_rows)
                ):
                    raise GwangjuBukguLibraryContractError(
                        "page-one recheck changed"
                    )
                list_rechecks += 1
            except Exception as exc:
                errors.append(
                    f"page-one recheck: {type(exc).__name__}: {exc}"
                )

        if not errors:
            try:
                last_recheck = client.get(
                    gwangju_bukgu_library_list_url(total_pages)
                )
                list_requests += 1
                declared, rows = _parse_list_page(last_recheck, total_pages)
                if (
                    declared != source_total
                    or _page_signature(rows) != _page_signature(last_rows)
                ):
                    raise GwangjuBukguLibraryContractError(
                        "last-page recheck changed"
                    )
                list_rechecks += 1
            except Exception as exc:
                errors.append(
                    f"last-page recheck: {type(exc).__name__}: {exc}"
                )

        if not errors:
            if len(all_rows) != source_total:
                errors.append(
                    f"declared total {source_total} differs from parsed "
                    f"{len(all_rows)}"
                )
            else:
                for offset, row in enumerate(all_rows):
                    expected_number = source_total - offset
                    if row.get("source_number") != expected_number:
                        errors.append(
                            f"source numbering changed at offset {offset}"
                        )
                        break

        identities = [_clean(row.get("identity")) for row in all_rows]
        identity_duplicates = len(identities) - len(set(identities))
        if identity_duplicates:
            errors.append(
                f"{identity_duplicates} duplicate source identities"
            )
        source_branches = Counter(
            _clean(row.get("branch")) for row in all_rows
        )
        if all_rows and set(source_branches) != set(
            GWANGJU_BUKGU_LIBRARY_BRANCHES
        ):
            errors.append("source no longer contains all five exact branches")

        current_parents = [
            row for row in all_rows if row.get("end_day") >= cutoff
        ]
        expired_count = len(all_rows) - len(current_parents)
        if len(current_parents) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_parents)} required current/future details"
            )

        if not errors:
            for parent in current_parents:
                detail_attempts += 1
                try:
                    detail = client.get(_clean(parent.get("raw_url")))
                    row, skipped = _parse_detail(parent, detail, target)
                    detailed_rows.append(row)
                    detail_pages += 1
                    instructor_skips += skipped
                except Exception as exc:
                    errors.append(
                        f"course {parent.get('identity')} detail/control: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    break

        result: list[dict[str, Any]] = []
        if not errors:
            deduper = dedupe_rows or _default_dedupe
            result = list(deduper(detailed_rows))
            if len(result) != len(detailed_rows):
                errors.append(
                    f"dedupe changed complete row count {len(detailed_rows)} "
                    f"to {len(result)}"
                )
                result = []
            else:
                try:
                    for row in result:
                        _validate_persisted_row(row)
                except Exception as exc:
                    errors.append(
                        f"persisted row validation: {type(exc).__name__}: {exc}"
                    )
                    result = []
        result.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("branch")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )

        semantic_keys = [
            (
                re.sub(r"\W+", "", _clean(row.get("title")).casefold()),
                _clean(row.get("branch")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
            )
            for row in result
        ]
        semantic_duplicates = len(semantic_keys) - len(set(semantic_keys))
        snapshot_complete = not errors
        pagination_complete = bool(
            snapshot_complete
            and total_pages > 0
            and len(page_counts) == total_pages
            and len(all_rows) == source_total
            and sentinel_pages == 1
            and list_rechecks == 2
            and list_requests == required_list_requests
        )
        details_complete = bool(
            snapshot_complete
            and detail_attempts == len(current_parents)
            and detail_pages == len(current_parents)
        )
        controls_complete = bool(
            details_complete
            and all(
                row.get("raw_fields", {}).get(
                    "application_control_present"
                )
                and row.get("raw_fields", {}).get("login_gate_verified")
                and bool(row.get("reservation_available"))
                == bool(
                    row.get("raw_fields", {}).get(
                        "application_control_actionable"
                    )
                )
                for row in detailed_rows
            )
        )
        pii_boundaries_complete = bool(
            details_complete
            and all(
                row.get("raw_fields", {}).get("applicant_roster_skipped")
                and row.get("raw_fields", {}).get("article_body_skipped")
                for row in detailed_rows
            )
        )
        full_snapshot_validated = bool(
            snapshot_complete
            and pagination_complete
            and details_complete
            and controls_complete
            and pii_boundaries_complete
        )
        source_states = Counter(
            _clean(row.get("source_state")) for row in all_rows
        )
        normalized_states = Counter(
            _clean(row.get("status")) for row in all_rows
        )
        current_branches = Counter(
            _clean(row.get("branch")) for row in detailed_rows
        )
        meta = _base_meta()
        meta.update(
            {
                "pages": client.requests,
                "request_count": client.requests,
                "sessions_created": client.sessions_created,
                "source_total": source_total,
                "source_rows": len(all_rows),
                "page_counts": page_counts,
                "data_pages": len(page_counts),
                "declared_total_pages": total_pages,
                "sentinel_page": sentinel_page,
                "sentinel_pages": sentinel_pages,
                "required_list_requests": required_list_requests,
                "list_requests": list_requests,
                "list_rechecks": list_rechecks,
                "current_count": len(current_parents),
                "expired_count": expired_count,
                "returned_count": len(result),
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "source_status_counts": dict(source_states),
                "normalized_status_counts": dict(normalized_states),
                "source_branch_counts": dict(source_branches),
                "current_branch_counts": dict(current_branches),
                "application_control_count": sum(
                    bool(
                        row.get("raw_fields", {}).get(
                            "application_control_present"
                        )
                    )
                    for row in detailed_rows
                ),
                "actionable_application_count": sum(
                    bool(row.get("reservation_available"))
                    for row in detailed_rows
                ),
                "applicant_roster_sections_skipped": sum(
                    bool(
                        row.get("raw_fields", {}).get(
                            "applicant_roster_skipped"
                        )
                    )
                    for row in detailed_rows
                ),
                "article_body_sections_skipped": sum(
                    bool(
                        row.get("raw_fields", {}).get("article_body_skipped")
                    )
                    for row in detailed_rows
                ),
                "instructor_values_skipped": instructor_skips,
                "identity_duplicate_count": identity_duplicates,
                "semantic_duplicate_count": semantic_duplicates,
                "capacity_overflow_count": sum(
                    int(row.get("capacity_current") or 0)
                    > int(row.get("capacity") or 0)
                    for row in all_rows
                ),
                "waitlist_overflow_count": sum(
                    int(row.get("waitlist_current") or 0)
                    > int(row.get("waitlist") or 0)
                    for row in all_rows
                ),
                "pagination_detected": total_pages > 1,
                "pagination_complete": pagination_complete,
                "details_complete": details_complete,
                "application_controls_complete": controls_complete,
                "pii_boundaries_complete": pii_boundaries_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": full_snapshot_validated,
                "source_cap_reached": source_cap_reached,
                "no_current_data": bool(
                    snapshot_complete and not current_parents
                ),
                "no_current_reason": (
                    "the complete Buk-gu library catalogue has no "
                    "current/future rows"
                    if snapshot_complete and not current_parents
                    else ""
                ),
                "configured_collection_error": "; ".join(errors),
            }
        )
        if errors:
            return [], GWANGJU_BUKGU_LIBRARY_PARSER, meta
        return result, GWANGJU_BUKGU_LIBRARY_PARSER, meta
    finally:
        client.close()


collect = collect_gwangju_bukgu_library_education


__all__ = [
    "GWANGJU_BUKGU_LIBRARY_ALIAS_PROVIDERS",
    "GWANGJU_BUKGU_LIBRARY_BRANCHES",
    "GWANGJU_BUKGU_LIBRARY_BRANCH_FILTERS",
    "GWANGJU_BUKGU_LIBRARY_CANONICAL_URL",
    "GWANGJU_BUKGU_LIBRARY_DISCOVERY_AUDIT",
    "GWANGJU_BUKGU_LIBRARY_FACILITY_ALIASES",
    "GWANGJU_BUKGU_LIBRARY_HOST",
    "GWANGJU_BUKGU_LIBRARY_MUNICIPALITY_CODE",
    "GWANGJU_BUKGU_LIBRARY_MUNICIPALITY_NAME",
    "GWANGJU_BUKGU_LIBRARY_NON_EXECUTING_ALIASES",
    "GWANGJU_BUKGU_LIBRARY_OWNER_BOUNDARY_AUDIT",
    "GWANGJU_BUKGU_LIBRARY_OWNERSHIP_SCOPE",
    "GWANGJU_BUKGU_LIBRARY_PARSER",
    "GWANGJU_BUKGU_LIBRARY_PII_FIELDS_NEVER_READ",
    "GWANGJU_BUKGU_LIBRARY_PROVIDER",
    "GWANGJU_CULTURAL_FOUNDATION_CANDIDATE_ID",
    "GWANGJU_CULTURAL_FOUNDATION_PROVIDER",
    "GWANGJU_METROPOLITAN_BOOKING_CANDIDATE_ID",
    "GWANGJU_METROPOLITAN_BOOKING_PROVIDER",
    "GwangjuBukguLibraryContractError",
    "GwangjuBukguLibraryFacilityAlias",
    "collect",
    "collect_gwangju_bukgu_library_education",
    "gwangju_bukgu_library_alias_for_target",
    "gwangju_bukgu_library_alias_metadata",
    "gwangju_bukgu_library_detail_url",
    "gwangju_bukgu_library_list_url",
    "is_gwangju_bukgu_library_alias_target",
    "is_gwangju_bukgu_library_target",
    "is_target",
]
