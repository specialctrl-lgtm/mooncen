"""Fail-closed collectors for Cheongdo-gun's municipal education ledgers.

The two review candidates do not own public course identities.  The old
``open.content`` course URL is now a 404 page, while ``apply/pwd.do`` is an
applicant lookup form that asks for a name, birth date, and telephone number.
Their already deployed provider identifiers are therefore retained only for
the real sibling ledgers to which production has already been retargeted.

Cheongdo's integrated reservation service has five visible education menus.
Only the lifelong-learning, youth, and women's ledgers belong to this
municipal education scope.  Museum education and children's-library courses
remain with their existing cultural-facility owners.  The obsolete
agricultural menu currently redirects to an HTTP 500 page and is not a
course source.

Each selected owner is collected independently.  Every declared data page,
the immediate empty sentinel, and stable first/last/sentinel boundaries are
verified.  Only non-completed rows receive detail requests.  Application
controls are identity-bound, but application, applicant lookup, attachment,
login, and write endpoints are never requested.  Contacts, instructors,
attachments, free-text bodies, and applicant data are not persisted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


CHEONGDO_MUNICIPALITY_CODE = "4782000000"
CHEONGDO_MUNICIPALITY_NAME = "경상북도 청도군"
CHEONGDO_HOST = "www.cheongdo.go.kr"

# Preserve the two provider identifiers that already own production rows.
CHEONGDO_LIFELONG_PROVIDER = "MUNI_WWW_CHEONGDO_GO_KR_0AE7DACF"
CHEONGDO_WOMEN_PROVIDER = "MUNI_WWW_CHEONGDO_GO_KR_9BD015B5"
CHEONGDO_YOUTH_PROVIDER = "MUNI_WWW_CHEONGDO_GO_KR_4F44CA8E"

CHEONGDO_COURSE_INFO_CANDIDATE_ID = "MUNI_IR_94536B8913FA"
CHEONGDO_COURSE_INFO_CANDIDATE_PROVIDER = "MUNI_WWW_CHEONGDO_GO_KR_BC006C1A"
CHEONGDO_PASSWORD_CANDIDATE_ID = "MUNI_IR_C20C987CE8CC"
CHEONGDO_LIFELONG_CANDIDATE_ID = "MUNI_IR_0FF9FE5775A0"
CHEONGDO_YOUTH_CANDIDATE_ID = "MUNI_IR_7CDB0137A369"
CHEONGDO_WOMEN_CANDIDATE_ID = "MUNI_IR_CB102AC651D6"

CHEONGDO_COURSE_INFO_CANDIDATE_URL = (
    "https://www.cheongdo.go.kr/open.content/edu/program/course.info/"
)
CHEONGDO_PASSWORD_CANDIDATE_URL = (
    "https://www.cheongdo.go.kr/reservation/edu/2/apply/pwd.do?mid=0203040000"
)
CHEONGDO_MAIN_URL = "https://www.cheongdo.go.kr/reservation/main.do"

CHEONGDO_PAGE_SIZE = 20
CHEONGDO_RECOMMENDED_MAX_PAGES = 20
CHEONGDO_RECOMMENDED_DETAIL_LIMIT = 100
CHEONGDO_FETCH_ATTEMPTS = 2
CHEONGDO_MAX_HTML_BYTES = 3_000_000
CHEONGDO_PARSER = (
    "cheongdo_exact_per_institution_education_ledger+declared_pager+"
    "immediate_empty_sentinel+stable_first_last_sentinel+noncompleted_"
    "detail_identity_binding+post_application_control_no_fetch+pii_allowlist"
)


class CheongdoContractError(RuntimeError):
    """Raised when the audited public Cheongdo contract has changed."""


@dataclass(frozen=True)
class CheongdoLedger:
    key: str
    provider: str
    candidate_id: str
    aidx: str
    mid: str
    cidx: str
    branch: str
    existing_owner: bool

    @property
    def path(self) -> str:
        return f"/reservation/edu/{self.aidx}/lecture/list.do"

    @property
    def detail_path(self) -> str:
        return f"/reservation/edu/{self.aidx}/lecture/view.do"

    @property
    def url(self) -> str:
        return f"https://{CHEONGDO_HOST}{self.path}?mid={self.mid}"

    @property
    def application_path(self) -> str:
        return f"/reservation/edu/{self.aidx}/apply/write.do"

    @property
    def applicant_list_path(self) -> str:
        return f"/reservation/edu/{self.aidx}/apply/list.do"


CHEONGDO_LEDGERS: tuple[CheongdoLedger, ...] = (
    CheongdoLedger(
        "lifelong",
        CHEONGDO_LIFELONG_PROVIDER,
        CHEONGDO_LIFELONG_CANDIDATE_ID,
        "16",
        "0201010000",
        "57",
        "평생학습교육",
        True,
    ),
    CheongdoLedger(
        "youth",
        CHEONGDO_YOUTH_PROVIDER,
        CHEONGDO_YOUTH_CANDIDATE_ID,
        "3",
        "0202010000",
        "44",
        "청소년교육강좌",
        False,
    ),
    CheongdoLedger(
        "women",
        CHEONGDO_WOMEN_PROVIDER,
        CHEONGDO_WOMEN_CANDIDATE_ID,
        "2",
        "0203040000",
        "58",
        "여성교육강좌",
        True,
    ),
)
CHEONGDO_LEDGER_BY_PROVIDER = {item.provider: item for item in CHEONGDO_LEDGERS}
CHEONGDO_LEDGER_BY_KEY = {item.key: item for item in CHEONGDO_LEDGERS}

CHEONGDO_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    CHEONGDO_COURSE_INFO_CANDIDATE_ID: {
        "provider": CHEONGDO_COURSE_INFO_CANDIDATE_PROVIDER,
        "url": CHEONGDO_COURSE_INFO_CANDIDATE_URL,
        "decision": "reject_http_404_without_repeated_course_identity_ledger",
        "checked_status": 404,
        "redirects": 0,
    },
    CHEONGDO_PASSWORD_CANDIDATE_ID: {
        "provider": CHEONGDO_WOMEN_PROVIDER,
        "url": CHEONGDO_PASSWORD_CANDIDATE_URL,
        "decision": "reject_applicant_lookup_pii_form_and_retain_provider_on_womens_ledger",
        "checked_status": 200,
        "redirects": 0,
        "pii_fields": ("searchName", "searchBirthday", "searchTel1", "searchTel2", "searchTel3"),
    },
    CHEONGDO_LIFELONG_CANDIDATE_ID: {
        "provider": CHEONGDO_LIFELONG_PROVIDER,
        "url": CHEONGDO_LEDGERS[0].url,
        "decision": "retain_existing_provider_on_canonical_lifelong_ledger",
    },
    CHEONGDO_YOUTH_CANDIDATE_ID: {
        "provider": CHEONGDO_YOUTH_PROVIDER,
        "url": CHEONGDO_LEDGERS[1].url,
        "decision": "create_missing_official_youth_education_owner",
    },
    CHEONGDO_WOMEN_CANDIDATE_ID: {
        "provider": CHEONGDO_WOMEN_PROVIDER,
        "url": CHEONGDO_LEDGERS[2].url,
        "decision": "retain_existing_provider_on_canonical_womens_ledger",
    },
}

CHEONGDO_NON_EXECUTING_ALIASES: tuple[Mapping[str, str], ...] = (
    {
        "url": "https://www.cheongdo.go.kr/reservation/",
        "decision": "redirects_once_to_navigation_home_without_complete_identity_ledger",
    },
    {
        "url": "https://www.cheongdo.go.kr/reservation/edu/16/lecture/list.do?mid=0203040000",
        "decision": "homepage_card_has_wrong_womens_mid_but_aidx_16_is_lifelong_owner",
    },
    {
        "url": "https://www.cheongdo.go.kr/reservation/edu/5/lecture/list.do?mid=0208120000",
        "decision": "obsolete_agricultural_education_menu_returns_http_500",
    },
)

CHEONGDO_SEPARATE_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "provider": "CULTURE_MUSEUM_618CA2DF06",
        "name": "청도박물관",
        "url": "https://www.cheongdo.go.kr/reservation/edu/4/lecture/list.do?mid=0205090000",
        "decision": "separate_museum_education_owner",
    },
    {
        "provider": "CULTURE_PUBLIC_LIBRARY_A9B3CA5F75",
        "name": "청도어린이도서관",
        "url": "https://www.cheongdo.go.kr/reservation/edu/17/lecture/list.do?mid=0207010000",
        "decision": "stale_integrated_subset_of_separate_library_program_owner",
    },
    {
        "provider": "CULTURE_PUBLIC_LIBRARY_A9B3CA5F75",
        "name": "청도어린이도서관",
        "url": "https://lib.cheongdo.go.kr/cd/module/teach/index.do?menu_idx=362",
        "decision": "authoritative_separate_library_program_ledger",
    },
    {
        "provider": "CULTURE_PUBLIC_LIBRARY_25F264CCDD",
        "name": "경상북도교육청 청도도서관",
        "url": "https://www.gbelib.kr/cd",
        "decision": "separate_education_office_library_owner",
    },
    {
        "provider": "CULTURE_CULTURE_FOUNDATION_ED0C7C38D4",
        "name": "청도우리정신문화재단",
        "url": "https://www.cdws.or.kr/open.content/ko",
        "decision": "separate_culture_and_experience_owner",
    },
    {
        "provider": "SPORTS_*",
        "name": "청도군 공공체육시설",
        "url": "https://www.cheongdo.go.kr/reservation/contents.do?mid=0103010100",
        "decision": "separate_sports_and_facility_reservation_owner_family",
    },
    {
        "provider": "",
        "name": "청도군 문화관광 예약",
        "url": "https://www.cheongdo.go.kr/reservation/contents.do?mid=0502030000",
        "decision": "separate_culture_tour_guide_reservation_service",
    },
)

CHEONGDO_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "checked_on": "2026-07-23",
    "included": {
        "lifelong": {
            "data_pages": 1,
            "sentinel_page": 2,
            "source_rows": 11,
            "source_status_counts": {"교육중": 6, "접수마감": 4, "추가접수중": 1},
            "current_rows": 11,
            "detail_pages": 11,
            "application_controls": 1,
        },
        "youth": {
            "data_pages": 1,
            "sentinel_page": 2,
            "source_rows": 1,
            "source_status_counts": {"교육완료": 1},
            "current_rows": 0,
            "detail_pages": 0,
            "application_controls": 0,
        },
        "women": {
            "checked_on": "2026-07-28",
            "data_pages": 0,
            "sentinel_page": 2,
            "source_rows": 0,
            "source_status_counts": {},
            "current_rows": 0,
            "detail_pages": 0,
            "application_controls": 0,
        },
    },
    "separate_integrated_ledgers": {
        "museum": {"source_rows": 16, "current_rows": 0},
        "children_library": {"source_rows": 4, "current_rows": 0},
    },
}

CHEONGDO_RECOMMENDED_OVERRIDE: Mapping[str, Any] = {
    "code": CHEONGDO_MUNICIPALITY_CODE,
    "full_name": CHEONGDO_MUNICIPALITY_NAME,
    "provider_decision": "retain_two_existing_owners_and_add_missing_youth_owner",
    "targets": tuple(
        {
            "provider": ledger.provider,
            "candidate_id": ledger.candidate_id,
            "url": ledger.url,
            "name": f"청도군 {ledger.branch}",
            "branch": ledger.branch,
            "existing_owner": ledger.existing_owner,
            "crawler": "Crawler.municipal_cheongdo:collect_cheongdo_education",
        }
        for ledger in CHEONGDO_LEDGERS
    ),
    "rejected_candidate_ids": (
        CHEONGDO_COURSE_INFO_CANDIDATE_ID,
        CHEONGDO_PASSWORD_CANDIDATE_ID,
    ),
}


@dataclass(frozen=True)
class _ListedCourse:
    identity: str
    title: str
    source_status: str
    method: str
    apply_period: str
    schedule_raw: str
    online_capacity_current: Optional[int]
    online_capacity_total: Optional[int]
    waitlist_current: Optional[int]
    waitlist_total: Optional[int]
    detail_url: str
    page: int


@dataclass(frozen=True)
class _Page:
    requested_page: int
    advertised_last: int
    rows: tuple[_ListedCourse, ...]
    empty: bool
    application_action: str
    applicant_list_action: str


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_DATE_RANGE_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s*~\s*(20\d{2})-(\d{2})-(\d{2})$"
)
_DATETIME_RANGE_RE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})$"
)
_PAIR_RE = re.compile(r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)$")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[-\s)]?\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_FORBIDDEN_PATH_RE = re.compile(
    r"/(?:apply|login|member|mypage|file|download|docviewer)(?:/|$)|pwd\.do$",
    re.IGNORECASE,
)
_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수중(후보인원)": "OPEN",
    "추가접수중": "OPEN",
    "접수마감": "CLOSED",
    "교육중": "CLOSED",
    "교육완료": "CLOSED",
}
_EXPECTED_STATUS_FILTERS = (
    "상태(전체)",
    "접수대기",
    "접수중",
    "접수마감",
    "접수중(후보인원)",
    "추가접수중",
    "교육중",
    "교육완료",
)
_DISCARDED_DETAIL_FIELDS = frozenset({"준비물", "강사명", "문의처", "첨부파일", "추첨"})
_SAFE_RAW_FIELDS = frozenset(
    {
        "parser",
        "identity",
        "ledger_key",
        "source_page",
        "source_status",
        "source_method",
        "source_online_capacity_current",
        "source_online_capacity_total",
        "source_waitlist_current",
        "source_waitlist_total",
        "onsite_capacity_total",
        "onsite_capacity_current",
        "extra_apply_period",
        "detail_verified",
        "list_detail_binding",
        "application_control_present",
        "application_control_verified",
        "application_endpoint_requested",
        "applicant_lookup_requested",
        "attachment_endpoint_requested",
        "discarded_detail_fields",
        "privacy_policy",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "contact",
        "contacts",
        "email",
        "instructor",
        "instructor_name",
        "manager",
        "manager_name",
        "attachments",
        "attachment_urls",
        "body",
        "course_content",
        "applicant_name",
        "applicant_phone",
        "password",
        "source_html",
        "raw_html",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _exact_url(value: str, expected: str) -> bool:
    try:
        parsed = urlparse(value)
        wanted = urlparse(expected)
        return bool(
            value == expected
            and parsed.scheme == "https"
            and parsed.hostname == CHEONGDO_HOST
            and parsed.port is None
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and parsed.path == wanted.path
            and parse_qs(parsed.query, keep_blank_values=True)
            == parse_qs(wanted.query, keep_blank_values=True)
        )
    except ValueError:
        return False


def cheongdo_target_ledger(target: Any) -> Optional[CheongdoLedger]:
    provider = _clean(_target_value(target, "provider"))
    ledger = CHEONGDO_LEDGER_BY_PROVIDER.get(provider)
    if ledger is None:
        return None
    return ledger if _exact_url(_clean(_target_value(target, "url")), ledger.url) else None


def is_cheongdo_education_target(target: Any) -> bool:
    """Return true only for one exact, provider-bound municipal ledger."""

    return cheongdo_target_ledger(target) is not None


is_target = is_cheongdo_education_target


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(ZoneInfo("Asia/Seoul")).date()
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return current


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=True)


def _list_url(ledger: CheongdoLedger, page: int) -> str:
    if page < 1:
        raise CheongdoContractError("page must be positive")
    return f"https://{CHEONGDO_HOST}{ledger.path}?{urlencode({'mid': ledger.mid, 'page': page})}"


def _detail_url(ledger: CheongdoLedger, identity: str) -> str:
    if not _IDENTITY_RE.fullmatch(identity):
        raise CheongdoContractError("invalid course identity")
    return (
        f"https://{CHEONGDO_HOST}{ledger.detail_path}?"
        f"{urlencode({'mid': ledger.mid, 'idx': identity})}"
    )


def _guard_read_url(url: str, ledger: CheongdoLedger) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != CHEONGDO_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
        or _FORBIDDEN_PATH_RE.search(parsed.path)
    ):
        raise CheongdoContractError(f"unsafe read URL: {url}")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == ledger.path:
        if set(query) != {"mid", "page"} or query["mid"] != [ledger.mid]:
            raise CheongdoContractError("unsafe list query")
        page = _clean(query["page"][0])
        if not page.isdigit() or int(page) < 1:
            raise CheongdoContractError("unsafe list page")
        return
    if parsed.path == ledger.detail_path:
        if set(query) != {"mid", "idx"} or query["mid"] != [ledger.mid]:
            raise CheongdoContractError("unsafe detail query")
        if not _IDENTITY_RE.fullmatch(_clean(query["idx"][0])):
            raise CheongdoContractError("unsafe detail identity")
        return
    raise CheongdoContractError(f"path outside owner read allowlist: {parsed.path}")


def _coerce_soup(result: Any, requested_url: str, ledger: CheongdoLedger) -> BeautifulSoup:
    final_url = requested_url
    if isinstance(result, BeautifulSoup):
        soup = result
        size = len(str(result).encode("utf-8"))
    elif isinstance(result, (str, bytes)):
        payload = result if isinstance(result, bytes) else result.encode("utf-8")
        size = len(payload)
        soup = BeautifulSoup(result, "html.parser")
    else:
        status = int(getattr(result, "status_code", 200))
        if status != 200:
            raise CheongdoContractError(f"HTTP {status} for {requested_url}")
        final_url = _clean(getattr(result, "url", requested_url)) or requested_url
        payload = getattr(result, "content", b"")
        if not payload:
            payload = str(getattr(result, "text", "")).encode("utf-8")
        size = len(payload)
        soup = BeautifulSoup(payload, "html.parser")
    if size > CHEONGDO_MAX_HTML_BYTES:
        raise CheongdoContractError("response exceeds HTML byte limit")
    _guard_read_url(final_url, ledger)
    return soup


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
    ledger: CheongdoLedger,
    meta: dict[str, Any],
) -> BeautifulSoup:
    _guard_read_url(url, ledger)
    meta["logical_requests"] += 1
    error: Optional[Exception] = None
    for _ in range(CHEONGDO_FETCH_ATTEMPTS):
        meta["physical_attempts"] += 1
        try:
            return _coerce_soup(fetcher(session, url, timeout), url, ledger)
        except Exception as exc:  # retry transport and contract failures, then fail closed
            error = exc
    if isinstance(error, CheongdoContractError):
        raise error
    raise CheongdoContractError(f"request failed for {url}: {_clean(error)}") from error


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True) if node is not None else "")


def _parse_pair(value: str, label: str) -> tuple[Optional[int], Optional[int]]:
    text = _clean(value)
    if text in {"", "-"}:
        return None, None
    match = _PAIR_RE.fullmatch(text)
    if not match:
        raise CheongdoContractError(f"invalid {label}: {text}")
    current, total = (int(part.replace(",", "")) for part in match.groups())
    if current < 0 or total < 0 or current > total:
        raise CheongdoContractError(f"impossible {label}: {text}")
    return current, total


def _form_action(soup: BeautifulSoup, selector: str, expected_path: str, ledger: CheongdoLedger) -> str:
    forms = soup.select(selector)
    if len(forms) != 1:
        raise CheongdoContractError(f"expected one form: {selector}")
    form = forms[0]
    if _clean(form.get("method")).lower() != "post":
        raise CheongdoContractError(f"form method changed: {selector}")
    action = urljoin(ledger.url, _clean(form.get("action")))
    parsed = urlparse(action)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != CHEONGDO_HOST
        or parsed.path != expected_path
        or query != {"mid": [ledger.mid]}
        or parsed.fragment
    ):
        raise CheongdoContractError(f"unsafe form action: {selector}")
    return action


def _parse_list_page(soup: BeautifulSoup, ledger: CheongdoLedger, page: int) -> _Page:
    title = _text(soup.title)
    if ledger.branch not in title or "교육/강좌" not in title:
        raise CheongdoContractError(f"{ledger.key}: wrong list owner title: {title}")
    form = soup.select_one("form#listForm")
    if form is None:
        raise CheongdoContractError(f"{ledger.key}: missing list form")
    action = urljoin(ledger.url, _clean(form.get("action")))
    parsed_action = urlparse(action)
    if parsed_action.path != ledger.path or parse_qs(parsed_action.query) != {"mid": [ledger.mid]}:
        raise CheongdoContractError(f"{ledger.key}: list form action changed")
    values = {
        name: _clean((form.select_one(f'[name="{name}"]') or {}).get("value"))
        for name in ("page", "aIdx", "cIdx")
    }
    if values != {"page": str(page), "aIdx": ledger.aidx, "cIdx": ledger.cidx}:
        raise CheongdoContractError(f"{ledger.key}: list identity fields changed: {values}")
    statuses = tuple(_text(node) for node in form.select('select[name="state"] option'))
    if statuses != _EXPECTED_STATUS_FILTERS:
        raise CheongdoContractError(f"{ledger.key}: status vocabulary changed")
    application_action = _form_action(
        soup, "form#postListForm", ledger.application_path, ledger
    )
    applicant_list_action = _form_action(
        soup, "form#applyListForm", ledger.applicant_list_path, ledger
    )
    tables = form.select("table.woman-edu-list")
    if len(tables) != 1:
        raise CheongdoContractError(f"{ledger.key}: list table count changed")
    table = tables[0]
    headers = tuple(_text(node) for node in table.select("thead th"))
    expected_fragments = ("과목번호", "강좌명", "접수기간", "교육시간", "모집인원", "후보인원", "추첨인원", "상태")
    if len(headers) != 8 or any(fragment not in value for fragment, value in zip(expected_fragments, headers)):
        raise CheongdoContractError(f"{ledger.key}: list columns changed")

    rows: list[_ListedCourse] = []
    for tr in table.select("tbody tr"):
        link = tr.select_one("a[data-view-btn][data-bc-idx]")
        if link is None:
            continue
        cells = tr.select("td")
        if len(cells) != 8:
            raise CheongdoContractError(f"{ledger.key}: row column count changed")
        identity = _clean(link.get("data-bc-idx"))
        if not _IDENTITY_RE.fullmatch(identity):
            raise CheongdoContractError(f"{ledger.key}: invalid row identity")
        title_text = _text(link)
        method = _text(tr.select_one("em.cate"))
        if not title_text or method not in {"선착순", "추첨"}:
            raise CheongdoContractError(f"{ledger.key}:{identity}: invalid title/method")
        apply_period = _text(tr.select_one(".list-date01"))
        if not _DATETIME_RANGE_RE.fullmatch(apply_period):
            raise CheongdoContractError(f"{ledger.key}:{identity}: invalid apply period")
        schedule_parts = [_text(node) for node in tr.select(".list-date02 span")]
        if len(schedule_parts) != 2 or not all(schedule_parts):
            raise CheongdoContractError(f"{ledger.key}:{identity}: schedule shape changed")
        source_status = _text(tr.select_one(".list-state"))
        if source_status not in _STATUS_MAP:
            raise CheongdoContractError(f"{ledger.key}:{identity}: unknown status {source_status}")
        capacity_current, capacity_total = _parse_pair(
            _text(tr.select_one(".list-people01")), "online capacity"
        )
        wait_current, wait_total = _parse_pair(
            _text(tr.select_one(".list-people02")), "waitlist capacity"
        )
        rows.append(
            _ListedCourse(
                identity=identity,
                title=title_text,
                source_status=source_status,
                method=method,
                apply_period=apply_period,
                schedule_raw=" · ".join(schedule_parts),
                online_capacity_current=capacity_current,
                online_capacity_total=capacity_total,
                waitlist_current=wait_current,
                waitlist_total=wait_total,
                detail_url=_detail_url(ledger, identity),
                page=page,
            )
        )
    identities = [item.identity for item in rows]
    if len(identities) != len(set(identities)):
        raise CheongdoContractError(f"{ledger.key}: duplicate identity within page")

    body = _text(table.select_one("tbody"))
    empty = not rows
    if empty != ("등록된 강좌가 없습니다" in body):
        raise CheongdoContractError(f"{ledger.key}: invalid empty sentinel shape")
    page_numbers: list[int] = []
    for node in soup.select(".bod_page a, .bod_page span"):
        text = _text(node)
        if text.isdigit():
            page_numbers.append(int(text))
    advertised_last = max(page_numbers) if page_numbers else (1 if rows else 0)
    if rows and (advertised_last < page or advertised_last < 1):
        raise CheongdoContractError(f"{ledger.key}: invalid advertised pager")
    return _Page(
        requested_page=page,
        advertised_last=advertised_last,
        rows=tuple(rows),
        empty=empty,
        application_action=application_action,
        applicant_list_action=applicant_list_action,
    )


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.requested_page,
        page.advertised_last,
        page.empty,
        tuple(
            (
                item.identity,
                item.title,
                item.source_status,
                item.apply_period,
                item.schedule_raw,
                item.online_capacity_current,
                item.online_capacity_total,
                item.waitlist_current,
                item.waitlist_total,
            )
            for item in page.rows
        ),
        page.application_action,
        page.applicant_list_action,
    )


def _detail_pairs(soup: BeautifulSoup, ledger: CheongdoLedger, identity: str) -> dict[str, str]:
    boxes = soup.select("#ajaxContent")
    if len(boxes) != 1:
        raise CheongdoContractError(f"{ledger.key}:{identity}: missing ajax detail")
    output: dict[str, str] = {}
    for dl in boxes[0].select(".detail-top > dl"):
        dt = dl.select_one("dt")
        dd = dl.select_one("dd")
        key, value = _text(dt), _text(dd)
        if not key or key in output:
            raise CheongdoContractError(f"{ledger.key}:{identity}: duplicate/empty detail label")
        output[key] = value
    required = {
        "접수기간",
        "추가접수기간",
        "교육일시",
        "교육시간",
        "교육장소",
        "교육대상",
        "수강료",
        "재료비",
        "준비물",
        "강사명",
        "문의처",
        "첨부파일",
        "정원",
        "신청현황",
        "결제방식",
        "추첨",
    }
    if set(output) != required:
        raise CheongdoContractError(f"{ledger.key}:{identity}: detail vocabulary changed")
    return output


def _detail_title(soup: BeautifulSoup, method: str) -> str:
    node = soup.select_one("#ajaxContent > .title")
    if node is None:
        return ""
    clone = BeautifulSoup(str(node), "html.parser")
    marker = clone.select_one("em.cate")
    if marker is not None:
        if _text(marker) != f"{method} 강좌":
            return ""
        marker.decompose()
    return _text(clone)


def _date_period(value: str, label: str) -> tuple[str, date, date]:
    match = _DATE_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise CheongdoContractError(f"invalid {label}: {_clean(value)}")
    parts = [int(item) for item in match.groups()]
    try:
        start = date(parts[0], parts[1], parts[2])
        end = date(parts[3], parts[4], parts[5])
    except ValueError as exc:
        raise CheongdoContractError(f"invalid {label}: {_clean(value)}") from exc
    if end < start:
        raise CheongdoContractError(f"reversed {label}")
    return f"{start.isoformat()} ~ {end.isoformat()}", start, end


def _capacity_detail(value: str, identity: str) -> tuple[int, int, int]:
    compact = _clean(value)
    match = re.fullmatch(
        r"(\d[\d,]*)명\s*\(온라인:(\d[\d,]*)명/현장\s*(\d[\d,]*)명\)\s*/\s*후보\s*(\d[\d,]*)명",
        compact,
    )
    if not match:
        raise CheongdoContractError(f"{identity}: detail capacity shape changed")
    total, online, onsite, wait = (int(item.replace(",", "")) for item in match.groups())
    if total != online + onsite:
        raise CheongdoContractError(f"{identity}: capacity components do not reconcile")
    return total, onsite, wait


def _application_counts(value: str, identity: str) -> tuple[int, int, int]:
    match = re.fullmatch(
        r"온라인\s*:\s*(\d[\d,]*)명\s*/\s*현장\s*:\s*(\d[\d,]*)명\s*/\s*후보\s*:\s*(\d[\d,]*)명",
        _clean(value),
    )
    if not match:
        raise CheongdoContractError(f"{identity}: application count shape changed")
    return tuple(int(item.replace(",", "")) for item in match.groups())  # type: ignore[return-value]


def _parse_detail(
    soup: BeautifulSoup,
    ledger: CheongdoLedger,
    listed: _ListedCourse,
    cutoff: date,
) -> dict[str, Any]:
    form = soup.select_one("#ajaxContent form#viewForm")
    identity_node = form.select_one('input[name="idx"]') if form else None
    if identity_node is None or _clean(identity_node.get("value")) != listed.identity:
        raise CheongdoContractError(f"{ledger.key}:{listed.identity}: detail identity mismatch")
    action_path = urlparse(urljoin(ledger.url, _clean(form.get("action")))).path
    # The source intentionally contains the historic `lecutre` misspelling.
    if action_path != f"/reservation/edu/{ledger.aidx}/lecutre/view.do":
        raise CheongdoContractError(f"{ledger.key}:{listed.identity}: detail form action changed")
    if _detail_title(soup, listed.method) != listed.title:
        raise CheongdoContractError(f"{ledger.key}:{listed.identity}: list/detail title mismatch")
    pairs = _detail_pairs(soup, ledger, listed.identity)
    if pairs["접수기간"] != listed.apply_period:
        raise CheongdoContractError(f"{ledger.key}:{listed.identity}: apply period mismatch")
    if pairs["교육시간"] not in listed.schedule_raw:
        raise CheongdoContractError(f"{ledger.key}:{listed.identity}: schedule mismatch")
    period, event_start, event_end = _date_period(pairs["교육일시"], "education period")
    if event_end < cutoff:
        raise CheongdoContractError(f"{ledger.key}:{listed.identity}: noncompleted row is expired")
    capacity_total, onsite_total, detail_wait_total = _capacity_detail(
        pairs["정원"], listed.identity
    )
    online_current, onsite_current, wait_current = _application_counts(
        pairs["신청현황"], listed.identity
    )
    if (
        listed.online_capacity_total is None
        or listed.online_capacity_current is None
        or listed.online_capacity_total + onsite_total != capacity_total
        or listed.online_capacity_current != online_current
        or onsite_current > onsite_total
        or wait_current > detail_wait_total
    ):
        raise CheongdoContractError(f"{ledger.key}:{listed.identity}: list/detail capacity mismatch")
    controls = [
        node
        for node in soup.select("#ajaxContent a, #ajaxContent button")
        if _text(node) == "신청하기"
    ]
    if len(controls) > 1:
        raise CheongdoContractError(f"{ledger.key}:{listed.identity}: multiple application controls")
    control = bool(controls)
    if control:
        node = controls[0]
        if (
            _clean(node.get("data-req-form-id")) != "postListForm"
            or _clean(node.get("data-req-get-p-l-idx")) != listed.identity
            or _clean(node.get("data-req-merge-form-id")) != "listForm"
            or "yhLib.inline.post" not in _clean(node.get("onclick"))
        ):
            raise CheongdoContractError(f"{ledger.key}:{listed.identity}: unsafe application control")
    if control and listed.source_status not in {"접수중", "접수중(후보인원)", "추가접수중"}:
        raise CheongdoContractError(f"{ledger.key}:{listed.identity}: control/status mismatch")
    return {
        "period": period,
        "event_start": event_start,
        "event_end": event_end,
        "room": pairs["교육장소"],
        "target": pairs["교육대상"],
        "fee": pairs["수강료"],
        "material_fee": pairs["재료비"],
        "extra_apply_period": pairs["추가접수기간"],
        "capacity_total": capacity_total,
        "capacity_current": online_current + onsite_current,
        "onsite_capacity_total": onsite_total,
        "onsite_capacity_current": onsite_current,
        "waitlist_current": wait_current,
        "waitlist_total": detail_wait_total,
        "application_control": control,
    }


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def _row(
    target: Any,
    ledger: CheongdoLedger,
    listed: _ListedCourse,
    detail: Mapping[str, Any],
) -> dict[str, Any]:
    control = bool(detail["application_control"])
    extra = _target_extra(target)
    apply_match = _DATETIME_RANGE_RE.fullmatch(listed.apply_period)
    if apply_match is None:
        raise CheongdoContractError("validated apply period disappeared")
    output: dict[str, Any] = {
        "provider": ledger.provider,
        "provider_course_id": f"{ledger.provider}:lecture:{ledger.aidx}:{listed.identity}",
        "title": listed.title,
        "branch": ledger.branch,
        "branch_code": f"{ledger.provider}:edu:{ledger.aidx}",
        "preserve_branch": True,
        "branch_url": ledger.url,
        "raw_url": listed.detail_url,
        # The public list is intentionally used instead of exposing/fetching the POST form.
        "application_url": ledger.url if control else "",
        "application_type": "ONLINE_FORM" if control else "INFO_ONLY_DISABLED_CONTROL",
        "application_method_raw": "온라인 신청 (신청하기)" if control else "접수 비활성",
        "reservation_available": control,
        "status": _STATUS_MAP[listed.source_status],
        "raw_status": listed.source_status,
        "period": _clean(detail["period"]),
        "start_date": detail["event_start"].isoformat(),
        "end_date": detail["event_end"].isoformat(),
        "apply_period": listed.apply_period,
        "apply_start_date": apply_match.group(1),
        "apply_end_date": apply_match.group(3),
        "schedule_raw": listed.schedule_raw,
        "target": _clean(detail["target"]),
        "capacity": f"{detail['capacity_current']} / {detail['capacity_total']}",
        "capacity_current": int(detail["capacity_current"]),
        "capacity_total": int(detail["capacity_total"]),
        "waitlist_current": int(detail["waitlist_current"]),
        "waitlist_total": int(detail["waitlist_total"]),
        "fee": _clean(detail["fee"]),
        "material_fee": _clean(detail["material_fee"]),
        "room": _clean(detail["room"]),
        "venue_name": _clean(detail["room"]),
        "address": "",
        "venue_address": "",
        "category": ledger.branch,
        "collection_category": _clean(extra.get("collection_category") or "공공예약"),
        "domain_category": _clean(extra.get("domain_category") or "교육·강좌"),
        "operator_type": _clean(extra.get("operator_type") or "지자체/공공기관"),
        "source_group": _clean(extra.get("source_group") or "municipal_reservation"),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "static_html+detail_html",
        "program_type": "교육",
        "municipality_code": CHEONGDO_MUNICIPALITY_CODE,
        "municipality_name": CHEONGDO_MUNICIPALITY_NAME,
        "municipality_full_name": CHEONGDO_MUNICIPALITY_NAME,
        "description": listed.title,
        "raw_fields": {
            "parser": CHEONGDO_PARSER,
            "identity": listed.identity,
            "ledger_key": ledger.key,
            "source_page": listed.page,
            "source_status": listed.source_status,
            "source_method": listed.method,
            "source_online_capacity_current": listed.online_capacity_current,
            "source_online_capacity_total": listed.online_capacity_total,
            "source_waitlist_current": listed.waitlist_current,
            "source_waitlist_total": listed.waitlist_total,
            "onsite_capacity_total": int(detail["onsite_capacity_total"]),
            "onsite_capacity_current": int(detail["onsite_capacity_current"]),
            "extra_apply_period": _clean(detail["extra_apply_period"]),
            "detail_verified": True,
            "list_detail_binding": "idx+title+apply_period+schedule+capacity",
            "application_control_present": control,
            "application_control_verified": True,
            "application_endpoint_requested": False,
            "applicant_lookup_requested": False,
            "attachment_endpoint_requested": False,
            "discarded_detail_fields": tuple(sorted(_DISCARDED_DETAIL_FIELDS)),
            "privacy_policy": "allowlisted_structured_fields_only",
        },
    }
    errors = _privacy_errors(output)
    if errors:
        raise CheongdoContractError("; ".join(errors))
    return output


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden PII/free-text row key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields allowlist exceeded")
    public_payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "branch_url", "raw_fields"}
        }
    )
    if _PHONE_RE.search(public_payload) or _EMAIL_RE.search(public_payload) or _RESIDENT_RE.search(public_payload):
        errors.append("PII persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-text description persisted")
    if row.get("address") or row.get("venue_address"):
        errors.append("unverified address persisted")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _initial_meta(ledger: Optional[CheongdoLedger]) -> dict[str, Any]:
    return {
        "municipality_code": CHEONGDO_MUNICIPALITY_CODE,
        "municipality_full_name": CHEONGDO_MUNICIPALITY_NAME,
        "owner_provider": ledger.provider if ledger else "",
        "canonical_provider": ledger.provider if ledger else "",
        "canonical_candidate_id": ledger.candidate_id if ledger else "",
        "canonical_url": ledger.url if ledger else "",
        "ledger_key": ledger.key if ledger else "",
        "official_branch": ledger.branch if ledger else "",
        "existing_owner": ledger.existing_owner if ledger else False,
        "parser": CHEONGDO_PARSER,
        "candidate_audit": {key: dict(value) for key, value in CHEONGDO_CANDIDATE_AUDIT.items()},
        "owner_boundaries": [dict(value) for value in CHEONGDO_SEPARATE_OWNER_BOUNDARIES],
        "recommended_max_pages": CHEONGDO_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": CHEONGDO_RECOMMENDED_DETAIL_LIMIT,
        "recommended_timeout_seconds": 30,
        "max_html_bytes": CHEONGDO_MAX_HTML_BYTES,
        "live_audit_baseline": dict(CHEONGDO_LIVE_AUDIT_BASELINE),
        "logical_requests": 0,
        "physical_attempts": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "data_pages": 0,
        "page_counts": [],
        "advertised_last_page": 0,
        "sentinel_page": 0,
        "sentinel_verified": False,
        "page1_rechecked": False,
        "last_page_rechecked": False,
        "sentinel_rechecked": False,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "returned_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "privacy_violations": 0,
        "application_endpoints_called": 0,
        "applicant_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "configured_collection_error": "",
    }


def _fetch_page(
    session: Any,
    ledger: CheongdoLedger,
    page: int,
    timeout: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
) -> _Page:
    soup = _fetch_soup(session, _list_url(ledger, page), timeout, fetcher, ledger, meta)
    meta["list_requests"] += 1
    return _parse_list_page(soup, ledger, page)


def collect_cheongdo_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = CHEONGDO_RECOMMENDED_MAX_PAGES,
    detail_limit: int = CHEONGDO_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete provider-bound Cheongdo education snapshot."""

    ledger = cheongdo_target_ledger(target)
    meta = _initial_meta(ledger)
    if ledger is None:
        meta["configured_collection_error"] = "target does not match an exact Cheongdo education owner"
        return [], CHEONGDO_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], CHEONGDO_PARSER, meta
        session_factory = _default_session_factory
    try:
        cutoff = _today(today)
        if any(isinstance(value, bool) or int(value) < 1 for value in (timeout, max_pages)):
            raise ValueError("timeout and max_pages must be positive integers")
        if isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("detail_limit must be a non-negative integer")
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], CHEONGDO_PARSER, meta

    current_fetcher = fetcher or _request
    session = session_factory()
    try:
        first = _fetch_page(session, ledger, 1, int(timeout), current_fetcher, meta)
        if first.empty:
            if first.rows or first.advertised_last != 0:
                raise CheongdoContractError("canonical empty ledger shape changed")
            advertised_last = 0
            pages: list[_Page] = []
            sentinel_number = 2
        else:
            advertised_last = first.advertised_last
            if advertised_last > int(max_pages):
                meta["source_cap_reached"] = True
                raise CheongdoContractError("advertised pagination exceeds max_pages")
            pages = [first]
            for number in range(2, advertised_last + 1):
                current = _fetch_page(session, ledger, number, int(timeout), current_fetcher, meta)
                if current.empty or current.advertised_last != advertised_last:
                    raise CheongdoContractError("data page boundary changed")
                pages.append(current)
            for page in pages[:-1]:
                if len(page.rows) != CHEONGDO_PAGE_SIZE:
                    raise CheongdoContractError("non-final page is not a full 20-row page")
            if not 1 <= len(pages[-1].rows) <= CHEONGDO_PAGE_SIZE:
                raise CheongdoContractError("final page size changed")
            sentinel_number = advertised_last + 1
        sentinel = _fetch_page(
            session, ledger, sentinel_number, int(timeout), current_fetcher, meta
        )
        if not sentinel.empty or sentinel.rows or sentinel.advertised_last != 0:
            raise CheongdoContractError("immediate post-boundary sentinel changed")
        listed = [item for page in pages for item in page.rows]
        identities = [item.identity for item in listed]
        if len(identities) != len(set(identities)):
            raise CheongdoContractError("course identity repeated across pages")
        contract_pages = (pages or [first]) + [sentinel]
        if any(page.application_action != first.application_action for page in contract_pages):
            raise CheongdoContractError("application form action drifted across pages")
        if any(page.applicant_list_action != first.applicant_list_action for page in contract_pages):
            raise CheongdoContractError("applicant form action drifted across pages")
        current_listed = [item for item in listed if item.source_status != "교육완료"]
        if len(current_listed) > int(detail_limit):
            meta["source_cap_reached"] = True
            raise CheongdoContractError(
                f"detail_limit {detail_limit} below required {len(current_listed)}"
            )
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "data_pages": len(pages),
                "page_counts": [len(page.rows) for page in pages],
                "advertised_last_page": advertised_last,
                "sentinel_page": sentinel_number,
                "sentinel_verified": True,
                "source_rows": len(listed),
                "source_status_counts": dict(Counter(item.source_status for item in listed)),
                "current_source_count": len(current_listed),
                "expired_source_count": len(listed) - len(current_listed),
                "pagination_complete": True,
                "application_action_verified_not_fetched": first.application_action,
                "applicant_list_action_verified_not_fetched": first.applicant_list_action,
            }
        )

        rows: list[dict[str, Any]] = []
        for listed_course in current_listed:
            detail_soup = _fetch_soup(
                session,
                listed_course.detail_url,
                int(timeout),
                current_fetcher,
                ledger,
                meta,
            )
            meta["detail_pages"] += 1
            parsed_detail = _parse_detail(detail_soup, ledger, listed_course, cutoff)
            rows.append(_row(target, ledger, listed_course, parsed_detail))

        first_recheck = _fetch_page(session, ledger, 1, int(timeout), current_fetcher, meta)
        if _page_signature(first_recheck) != _page_signature(first):
            raise CheongdoContractError("page-one stability recheck failed")
        meta["page1_rechecked"] = True
        if advertised_last <= 1:
            meta["last_page_rechecked"] = True
        else:
            last_recheck = _fetch_page(
                session, ledger, advertised_last, int(timeout), current_fetcher, meta
            )
            if _page_signature(last_recheck) != _page_signature(pages[-1]):
                raise CheongdoContractError("last-page stability recheck failed")
            meta["last_page_rechecked"] = True
        sentinel_recheck = _fetch_page(
            session, ledger, sentinel_number, int(timeout), current_fetcher, meta
        )
        if _page_signature(sentinel_recheck) != _page_signature(sentinel):
            raise CheongdoContractError("sentinel stability recheck failed")
        meta["sentinel_rechecked"] = True

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _dedupe)(rows))
        expected_ids = {
            f"{ledger.provider}:lecture:{ledger.aidx}:{item.identity}"
            for item in current_listed
        }
        if len(rows) != len(current_listed) or {
            _clean(row.get("provider_course_id")) for row in rows
        } != expected_ids:
            raise CheongdoContractError("dedupe changed the current identity set")
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        if privacy_errors:
            meta["privacy_violations"] = len(privacy_errors)
            raise CheongdoContractError("; ".join(privacy_errors[:5]))
        meta.update(
            {
                "returned_count": len(rows),
                "details_complete": meta["detail_pages"] == len(current_listed),
                "status_counts": dict(Counter(_clean(row["status"]) for row in rows)),
                "branch_counts": dict(Counter(_clean(row["branch"]) for row in rows)),
                "application_control_count": sum(bool(row["reservation_available"]) for row in rows),
                "no_current_data": not rows,
                "no_current_reason": (
                    "canonical institution ledger is stably empty"
                    if not listed
                    else "all canonical source rows are completed"
                    if not rows
                    else ""
                ),
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return rows, CHEONGDO_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], CHEONGDO_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_cheongdo_education
