"""Fail-closed collector for Geumsan Youth Training Center experiences.

The official programme ledger mixes hands-on summer specials with sports,
certification courses, tests, and other education records.  This collector
therefore traverses the complete historical ledger, partitions records by
their structured operation dates, and emits only active one-day programmes
whose title and public detail description jointly prove the audited
hands-on-special contract.

Only the public list and public ``mode=V`` detail routes are requestable.
The ``mode=W`` application control is identity-checked in already-fetched
detail HTML, but is never requested or persisted as an application URL.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


GEUMSAN_EXPERIENCE_PROVIDER = "MUNI_WWW_GEUMSAN_GO_KR_E2508DF6"
GEUMSAN_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_3A0D9D3DC53D"
GEUMSAN_EXPERIENCE_HOST = "www.geumsan.go.kr"
GEUMSAN_EXPERIENCE_LIST_PATH = "/youthcenter/html/sub02/0202.html"
GEUMSAN_EXPERIENCE_DETAIL_PATH = "/site/youthcenter/html/sub02/0202.html"
GEUMSAN_EXPERIENCE_APPLICATION_PATH = GEUMSAN_EXPERIENCE_LIST_PATH
GEUMSAN_EXPERIENCE_URL = (
    f"https://{GEUMSAN_EXPERIENCE_HOST}{GEUMSAN_EXPERIENCE_LIST_PATH}"
)
GEUMSAN_EXPERIENCE_PAGE_SIZE = 10
GEUMSAN_EXPERIENCE_MAX_PAGES = 50
GEUMSAN_EXPERIENCE_MAX_HTML_BYTES = 3_000_000
GEUMSAN_EXPERIENCE_MUNICIPALITY_CODE = "4471000000"
GEUMSAN_EXPERIENCE_MUNICIPALITY_NAME = "충청남도 금산군"
GEUMSAN_EXPERIENCE_BRANCH = "금산군 청소년수련관"
GEUMSAN_EXPERIENCE_PARSER = (
    "geumsan_youth_training_center_complete_historical_programme_ledger+"
    "declared_last_page+complete_10_item_pages+exact_post_last_clamp_identity_"
    "and_rowset_sentinel_substitute+stable_first_last_clamp_rechecks+"
    "operation_date_current_future_partition+active_one_day_special_title_"
    "and_detail_content_hands_on_classifier+all_relevant_public_details+"
    "identity_bound_mode_w_application_control_observed_no_follow_no_expose+"
    "locked_experience+no_login_auth_member_application_applicant_identity_"
    "file_attachment_download_post_or_pii_calls"
)
GEUMSAN_EXPERIENCE_OWNERSHIP_SCOPE = (
    "geumsan_youth_training_center_active_current_future_hands_on_specials"
)

_LIST_PAGE_TITLE = "금산군 목록 > 프로그램신청 > 상설프로그램 > 금산군 청소년수련관"
_DETAIL_PAGE_TITLE = "금산군 보기 > 프로그램신청 > 상설프로그램 > 금산군 청소년수련관"
_IDENTITY_RE = re.compile(r"[1-9]\d{0,11}")
_DATE_PERIOD_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})"
)
_DATETIME_PERIOD_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})"
)
_CAPACITY_RE = re.compile(r"(\d[\d,]*)\s*명?\s*/\s*(\d[\d,]*)\s*명?")
_WAITLIST_RE = re.compile(
    r"\(?\s*대기\s*:\s*(\d[\d,]*)\s*명?\s*/\s*(\d[\d,]*)\s*명?\s*\)?"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- .()]?\d{3,4}[- .]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_HANDSON_TITLE_RE = re.compile(r"(?:DIY|만들기)", re.I)
_LIST_FIELDS = frozenset(
    {
        "운영주체",
        "교육기간",
        "교육시간",
        "접수기간",
        "신청/정원",
        "교육장소",
        "교육대상",
        "교육주기",
    }
)
_DETAIL_FIELDS = _LIST_FIELDS | {"문의", "신청방법"}
_SOURCE_STATES = {"교육대기", "교육중", "교육종료"}
_SOURCE_ACCEPT = {"접수예정", "접수중", "대기접수", "접수마감"}
_SOURCE_METHODS = {"인터넷", "혼합", "전화", "방문", "기타", ""}
_SOURCE_CATEGORIES = {
    "문화예술",
    "인문교양",
    "직업능력 향상교육",
    "시민참여교육",
    "성인문해교육",
    "학력보완교육",
    "",
}
_ACTIVE_ACCEPT = {"접수중", "대기접수"}
_EXCLUSION_MARKERS = (
    "테스트",
    "공지",
    "알림",
    "모집",
    "공연",
    "축제",
    "행사",
    "대관",
    "대여",
    "자격증",
    "농구",
    "요가",
    "ITQ",
)
_DETAIL_CONTENT_MARKERS = ("금산군 청소년수련관", "특강", "프로그램")
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_page",
        "source_status",
        "source_education_state",
        "source_category",
        "source_method",
        "source_operator",
        "source_venue",
        "source_program_period",
        "source_application_period",
        "source_schedule",
        "source_target",
        "source_capacity_current",
        "source_capacity_total",
        "source_waitlist_current",
        "source_waitlist_total",
        "detail_verified",
        "hands_on_title_evidence",
        "hands_on_content_evidence",
        "application_control_present",
        "application_endpoint_not_requested",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "contact",
        "email",
        "manager",
        "instructor",
        "applicant",
        "member",
        "attachment",
        "download_url",
        "detail_description",
        "source_html",
        "raw_html",
    }
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GeumsanExperienceContractError(RuntimeError):
    """Raised when the audited public-source contract changes."""


@dataclass(frozen=True)
class _ListRow:
    identity: str
    page: int
    title: str
    education_state: str
    source_status: str
    method: str
    category: str
    operator: str
    start_date: Optional[date]
    end_date: Optional[date]
    education_time: str
    apply_period: str
    capacity_text: str
    venue: str
    target: str
    schedule: str
    detail_url: str


@dataclass(frozen=True)
class _ListPage:
    requested: int
    observed: int
    last: int
    rows: tuple[_ListRow, ...]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).casefold()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_geumsan_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == GEUMSAN_EXPERIENCE_PROVIDER
        and _clean(_target_value(target, "url")) == GEUMSAN_EXPERIENCE_URL
    )


is_target = is_geumsan_experience_target


def geumsan_experience_list_url(page: int) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    return f"{GEUMSAN_EXPERIENCE_URL}?{urlencode((('GotoPage', str(page)),))}"


def geumsan_experience_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError("invalid Geumsan experience identity")
    return (
        f"https://{GEUMSAN_EXPERIENCE_HOST}{GEUMSAN_EXPERIENCE_DETAIL_PATH}?"
        f"{urlencode((('mode', 'V'), ('mng_no', value)))}"
    )


def _canonical_key(url: str) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    parsed = urlparse(_clean(url))
    return (
        parsed.scheme.lower(),
        parsed.netloc.lower() + parsed.path,
        tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
    )


def _request_kind(url: str) -> str:
    parsed = urlparse(_clean(url))
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise GeumsanExperienceContractError("malformed official request query") from exc
    if not (
        parsed.scheme == "https"
        and parsed.hostname == GEUMSAN_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        raise GeumsanExperienceContractError("unsafe official request URL")
    if parsed.path == GEUMSAN_EXPERIENCE_LIST_PATH:
        if (
            len(query) == 1
            and query[0][0] == "GotoPage"
            and _IDENTITY_RE.fullmatch(query[0][1])
        ):
            return "list"
    if parsed.path == GEUMSAN_EXPERIENCE_DETAIL_PATH:
        values = dict(query)
        identity = values.get("mng_no", "")
        if (
            len(query) == 2
            and sorted(query) == [("mng_no", identity), ("mode", "V")]
            and _IDENTITY_RE.fullmatch(identity)
        ):
            return "detail"
    raise GeumsanExperienceContractError(
        "request is outside the public list/detail GET allowlist"
    )


def _default_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 municipal-course-crawler/1.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


class _Requester:
    def __init__(
        self,
        session_factory: SessionFactory,
        fetcher: Fetcher,
        timeout: int,
        meta: dict[str, Any],
    ) -> None:
        self.session = session_factory()
        self.fetcher = fetcher
        self.timeout = timeout
        self.meta = meta

    def soup(self, url: str) -> BeautifulSoup:
        kind = _request_kind(url)
        self.meta["logical_requests"] += 1
        self.meta[f"{kind}_requests"] += 1
        response = self.fetcher(self.session, url, self.timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise GeumsanExperienceContractError(f"unexpected HTTP status {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise GeumsanExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(str(key).lower() == "location" and value for key, value in headers.items()):
            raise GeumsanExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and _canonical_key(final_url) != _canonical_key(url):
            raise GeumsanExperienceContractError("official response URL changed")
        content_type = _clean(
            next(
                (
                    value
                    for key, value in headers.items()
                    if str(key).lower() == "content-type"
                ),
                "text/html",
            )
        ).lower()
        if "html" not in content_type:
            raise GeumsanExperienceContractError("official response is not HTML")
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > GEUMSAN_EXPERIENCE_MAX_HTML_BYTES:
            raise GeumsanExperienceContractError("empty or oversized official response")
        soup = BeautifulSoup(body, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        expected_title = _LIST_PAGE_TITLE if kind == "list" else _DETAIL_PAGE_TITLE
        if title != expected_title:
            raise GeumsanExperienceContractError("official page title changed")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise GeumsanExperienceContractError("invalid audit date") from exc


def _page_from_href(href: Any, label: str) -> int:
    parsed = urlparse(_clean(href))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.path
        or parsed.fragment
        or len(query) != 1
        or query[0][0] != "GotoPage"
        or _IDENTITY_RE.fullmatch(query[0][1]) is None
    ):
        raise GeumsanExperienceContractError(f"{label} pagination link changed")
    return int(query[0][1])


def _pagination(soup: BeautifulSoup, requested: int) -> tuple[int, int]:
    active = soup.select(".pagination li.active a.page-link[href]")
    last_nodes = soup.select('.pagination a.page-link[aria-label="last"][href]')
    if len(active) != 1 or len(last_nodes) != 1:
        raise GeumsanExperienceContractError(
            f"page {requested}: pagination boundary controls changed"
        )
    observed = _page_from_href(active[0].get("href"), "active")
    last = _page_from_href(last_nodes[0].get("href"), "last")
    if last < 1 or observed != min(requested, last):
        raise GeumsanExperienceContractError(
            f"page {requested}: observed/declared pagination boundary changed"
        )
    return observed, last


def _detail_identity(href: Any) -> str:
    parsed = urlparse(urljoin(GEUMSAN_EXPERIENCE_URL, _clean(href)))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(query)
    identity = values.get("mng_no", "")
    if not (
        parsed.scheme == "https"
        and parsed.hostname == GEUMSAN_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GEUMSAN_EXPERIENCE_DETAIL_PATH
        and not parsed.fragment
        and len(query) == 2
        and sorted(query) == [("mng_no", identity), ("mode", "V")]
        and _IDENTITY_RE.fullmatch(identity)
    ):
        raise GeumsanExperienceContractError("public detail identity link changed")
    return identity


def _heading(node: Any, context: str) -> tuple[str, str]:
    title_node = node.select_one(".in_top .tit")
    state_node = title_node.select_one(".cond") if title_node else None
    state = _clean(state_node.get_text(" ", strip=True) if state_node else "")
    if title_node is None or state_node is None:
        raise GeumsanExperienceContractError(f"{context}: title structure changed")
    clone = BeautifulSoup(str(title_node), "html.parser")
    clone_state = clone.select_one(".cond")
    if clone_state is not None:
        clone_state.decompose()
    title = _clean(clone.get_text(" ", strip=True))
    if not title or state not in _SOURCE_STATES:
        raise GeumsanExperienceContractError(f"{context}: title/state changed")
    if _PHONE_RE.search(title) or _EMAIL_RE.search(title):
        raise GeumsanExperienceContractError(f"{context}: contact-like title refused")
    return title, state


def _accept(node: Any, context: str) -> tuple[str, str]:
    accept = node.select_one(".accept")
    status_node = accept.select_one("span") if accept else None
    method_node = accept.find("em", recursive=False) if accept else None
    status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
    method = _clean(method_node.get_text(" ", strip=True) if method_node else "")
    if status not in _SOURCE_ACCEPT or method not in _SOURCE_METHODS:
        raise GeumsanExperienceContractError(
            f"{context}: acceptance state/method changed"
        )
    return status, method


def _category(node: Any, context: str) -> str:
    category_node = node.select_one(".in_top .cate")
    category = _clean(category_node.get_text(" ", strip=True) if category_node else "")
    if category not in _SOURCE_CATEGORIES:
        raise GeumsanExperienceContractError(f"{context}: category changed")
    return category


def _structured_fields(
    node: Any,
    *,
    expected: frozenset[str],
    discarded: frozenset[str] = frozenset(),
    require_exact: bool = True,
    context: str,
) -> dict[str, str]:
    values: dict[str, str] = {}
    labels: set[str] = set()
    for item in node.select(".list_con > li"):
        label_node = item.find("span", recursive=False)
        if label_node is None:
            raise GeumsanExperienceContractError(f"{context}: field label missing")
        label = _clean(label_node.get_text(" ", strip=True)).rstrip(":").strip()
        if not label or label in labels or label not in expected:
            raise GeumsanExperienceContractError(f"{context}: field set changed")
        labels.add(label)
        if label in discarded:
            continue
        value_nodes = item.find_all("em", recursive=False)
        value = _clean(value_nodes[0].get_text(" ", strip=True)) if value_nodes else ""
        if _PHONE_RE.search(value) or _EMAIL_RE.search(value):
            raise GeumsanExperienceContractError(
                f"{context}: contact-like data entered safe field {label}"
            )
        values[label] = value
    if (require_exact and labels != set(expected)) or (not require_exact and not labels):
        raise GeumsanExperienceContractError(f"{context}: field contract changed")
    return values


def _date_period(value: str, identity: str, field: str) -> tuple[date, date]:
    match = _DATE_PERIOD_RE.fullmatch(_clean(value))
    if match is None:
        raise GeumsanExperienceContractError(f"{identity}: {field} period changed")
    try:
        start = date.fromisoformat(match[1])
        end = date.fromisoformat(match[2])
    except ValueError as exc:
        raise GeumsanExperienceContractError(f"{identity}: invalid {field} period") from exc
    if end < start:
        raise GeumsanExperienceContractError(f"{identity}: reversed {field} period")
    return start, end


def _datetime_period(value: str, identity: str) -> tuple[str, str]:
    match = _DATETIME_PERIOD_RE.fullmatch(_clean(value))
    if match is None:
        raise GeumsanExperienceContractError(
            f"{identity}: application period changed"
        )
    start = f"{match[1]} {match[2]}"
    end = f"{match[3]} {match[4]}"
    try:
        if datetime.fromisoformat(end) < datetime.fromisoformat(start):
            raise GeumsanExperienceContractError(
                f"{identity}: reversed application period"
            )
    except ValueError as exc:
        raise GeumsanExperienceContractError(
            f"{identity}: invalid application period"
        ) from exc
    return start, end


def _parse_list_row(anchor: Any, page: int) -> _ListRow:
    identity = _detail_identity(anchor.get("href"))
    context = f"page {page} programme {identity}"
    title, education_state = _heading(anchor, context)
    source_status, method = _accept(anchor, context)
    category = _category(anchor, context)
    fields = _structured_fields(
        anchor,
        expected=_LIST_FIELDS,
        require_exact=False,
        context=context,
    )
    period = fields.get("교육기간", "")
    if period:
        start, end = _date_period(period, identity, "operation")
    elif education_state == "교육종료" and source_status == "접수마감":
        # One audited legacy test record is deliberately sparse.  It remains
        # part of exact historical cardinality but can never enter the dated
        # current/future partition or detail-candidate set.
        start = end = None
    else:
        raise GeumsanExperienceContractError(
            f"{identity}: active programme operation period missing"
        )
    return _ListRow(
        identity=identity,
        page=page,
        title=title,
        education_state=education_state,
        source_status=source_status,
        method=method,
        category=category,
        operator=fields.get("운영주체", ""),
        start_date=start,
        end_date=end,
        education_time=fields.get("교육시간", ""),
        apply_period=fields.get("접수기간", ""),
        capacity_text=fields.get("신청/정원", ""),
        venue=fields.get("교육장소", ""),
        target=fields.get("교육대상", ""),
        schedule=fields.get("교육주기", ""),
        detail_url=geumsan_experience_detail_url(identity),
    )


def _parse_list_page(soup: BeautifulSoup, requested: int) -> _ListPage:
    observed, last = _pagination(soup, requested)
    roots = soup.select(".program_con")
    if len(roots) != 1:
        raise GeumsanExperienceContractError(
            f"page {requested}: programme ledger root changed"
        )
    anchors = roots[0].find_all("a", href=True, recursive=False)
    rows = tuple(_parse_list_row(anchor, observed) for anchor in anchors)
    if observed < last and len(rows) != GEUMSAN_EXPERIENCE_PAGE_SIZE:
        raise GeumsanExperienceContractError(
            f"page {requested}: non-final row count changed"
        )
    if observed == last and not (1 <= len(rows) <= GEUMSAN_EXPERIENCE_PAGE_SIZE):
        raise GeumsanExperienceContractError(
            f"page {requested}: final row count changed"
        )
    identities = [row.identity for row in rows]
    if len(identities) != len(set(identities)):
        raise GeumsanExperienceContractError(
            f"page {requested}: duplicate programme identities"
        )
    return _ListPage(requested, observed, last, rows)


def _page_identity_sequence(page: _ListPage) -> tuple[str, ...]:
    return tuple(row.identity for row in page.rows)


def _page_rowset_signature(page: _ListPage) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.identity,
            row.title,
            row.education_state,
            row.source_status,
            row.method,
            row.category,
            row.operator,
            row.start_date,
            row.end_date,
            row.education_time,
            row.apply_period,
            row.capacity_text,
            row.venue,
            row.target,
            row.schedule,
        )
        for row in page.rows
    )


def _same_title(list_title: str, detail_title: str) -> bool:
    if list_title.endswith("..."):
        prefix = list_title[:-3].rstrip().rstrip("&").rstrip()
        return bool(prefix and _normalized(detail_title).startswith(_normalized(prefix)))
    return _normalized(list_title) == _normalized(detail_title)


def _same_public_field(field: str, listed: Any, detailed: Any) -> bool:
    if field == "교육대상":
        listed = re.sub(r"\s*>\s*$", "", _clean(listed))
        detailed = re.sub(r"\s*>\s*$", "", _clean(detailed))
    return _normalized(listed) == _normalized(detailed)


def _list_hands_on_decision(row: _ListRow) -> tuple[bool, str]:
    evidence = _clean(row.title)
    exclusion = next((marker for marker in _EXCLUSION_MARKERS if marker in evidence), "")
    if exclusion:
        return False, f"excluded:{exclusion}"
    if row.source_status not in _ACTIVE_ACCEPT:
        return False, "inactive_acceptance"
    if row.education_state not in {"교육대기", "교육중"}:
        return False, "inactive_education_state"
    if row.start_date is None or row.end_date is None:
        return False, "missing_dated_period"
    if row.end_date < row.start_date:
        return False, "invalid_period"
    if row.category != "문화예술":
        return False, "wrong_category"
    if row.operator != GEUMSAN_EXPERIENCE_BRANCH or row.venue != GEUMSAN_EXPERIENCE_BRANCH:
        return False, "wrong_owner_or_venue"
    if row.start_date != row.end_date:
        return False, "not_one_day"
    if not evidence.startswith("[특강]"):
        return False, "not_audited_special"
    marker = _HANDSON_TITLE_RE.search(evidence)
    if marker is None:
        return False, "no_explicit_hands_on_title_marker"
    return True, marker.group(0)


def _detail_content(soup: BeautifulSoup, identity: str) -> tuple[str, str]:
    tables = soup.select("#txt .table-responsive table.table")
    if len(tables) != 1:
        raise GeumsanExperienceContractError(
            f"{identity}: detail description table changed"
        )
    labels: set[str] = set()
    fee = ""
    description = ""
    for label_node in tables[0].select("td.td_row"):
        label = _clean(label_node.get_text(" ", strip=True))
        if not label or label in labels:
            raise GeumsanExperienceContractError(
                f"{identity}: duplicate detail table label"
            )
        labels.add(label)
        value_node = label_node.find_next_sibling("td")
        if value_node is None:
            raise GeumsanExperienceContractError(
                f"{identity}: detail table value changed"
            )
        if label == "수강료":
            fee = _clean(value_node.get_text(" ", strip=True))
        elif label == "강좌 상세설명":
            description = _clean(value_node.get_text(" ", strip=True))
        # 담당강사 is intentionally not read.
    if labels != {"담당강사", "수강료", "강좌 상세설명"} or not description:
        raise GeumsanExperienceContractError(
            f"{identity}: detail table contract changed"
        )
    if _PHONE_RE.search(description) or _EMAIL_RE.search(description):
        raise GeumsanExperienceContractError(
            f"{identity}: contact-like detail description refused"
        )
    return description, fee


def _content_hands_on_decision(content: str) -> tuple[bool, str]:
    missing = [marker for marker in _DETAIL_CONTENT_MARKERS if marker not in content]
    if missing:
        return False, "missing:" + ",".join(missing)
    return True, "청소년수련관 여름특강 프로그램"


def _application_control_present(soup: BeautifulSoup, identity: str) -> bool:
    controls: list[Any] = []
    for anchor in soup.select("#txt .text-right.mt_30 a.btn[href]"):
        text = _clean(anchor.get_text(" ", strip=True))
        parsed = urlparse(urljoin(GEUMSAN_EXPERIENCE_URL, _clean(anchor.get("href"))))
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if "강좌신청" in text or ("mode", "W") in query:
            controls.append(anchor)
    if len(controls) != 1:
        raise GeumsanExperienceContractError(
            f"{identity}: application control count changed"
        )
    anchor = controls[0]
    parsed = urlparse(urljoin(GEUMSAN_EXPERIENCE_URL, _clean(anchor.get("href"))))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if not (
        parsed.scheme == "https"
        and parsed.hostname == GEUMSAN_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GEUMSAN_EXPERIENCE_APPLICATION_PATH
        and not parsed.fragment
        and len(query) == 2
        and sorted(query) == [("edu_mng_no", identity), ("mode", "W")]
        and _clean(anchor.get_text(" ", strip=True)) == "강좌신청"
    ):
        raise GeumsanExperienceContractError(
            f"{identity}: application control identity/path changed"
        )
    return True


def _capacity(value: str, identity: str) -> tuple[int, int]:
    match = _CAPACITY_RE.fullmatch(_clean(value))
    if match is None:
        raise GeumsanExperienceContractError(f"{identity}: capacity changed")
    current = int(match[1].replace(",", ""))
    total = int(match[2].replace(",", ""))
    if current < 0 or total < 1:
        raise GeumsanExperienceContractError(f"{identity}: invalid capacity")
    return current, total


def _waitlist(node: Any, identity: str) -> tuple[int, int]:
    capacity_item = next(
        (
            item
            for item in node.select(".list_con > li")
            if _clean(
                item.find("span", recursive=False).get_text(" ", strip=True)
                if item.find("span", recursive=False)
                else ""
            ).rstrip(":").strip()
            == "신청/정원"
        ),
        None,
    )
    if capacity_item is None:
        raise GeumsanExperienceContractError(f"{identity}: capacity item changed")
    values = capacity_item.find_all("em", recursive=False)
    wait_text = _clean(values[1].get_text(" ", strip=True)) if len(values) > 1 else ""
    if not wait_text:
        return 0, 0
    match = _WAITLIST_RE.fullmatch(wait_text)
    if match is None:
        raise GeumsanExperienceContractError(f"{identity}: waitlist changed")
    return int(match[1].replace(",", "")), int(match[2].replace(",", ""))


def _parse_detail(listed: _ListRow, soup: BeautifulSoup) -> dict[str, Any]:
    roots = soup.select("#txt .program_view")
    if len(roots) != 1:
        raise GeumsanExperienceContractError(
            f"{listed.identity}: public detail root changed"
        )
    root = roots[0]
    title, education_state = _heading(root, f"{listed.identity}: detail")
    source_status, method = _accept(root, f"{listed.identity}: detail")
    category = _category(root, f"{listed.identity}: detail")
    if not (
        _same_title(listed.title, title)
        and education_state == listed.education_state
        and source_status == listed.source_status
        and method == listed.method
        and category == listed.category
    ):
        raise GeumsanExperienceContractError(
            f"{listed.identity}: list/detail identity drift"
        )
    fields = _structured_fields(
        root,
        expected=_DETAIL_FIELDS,
        discarded=frozenset({"문의"}),
        context=f"{listed.identity}: detail",
    )
    listed_fields = {
        "운영주체": listed.operator,
        "교육기간": f"{listed.start_date.isoformat()} ~ {listed.end_date.isoformat()}",
        "교육시간": listed.education_time,
        "접수기간": listed.apply_period,
        "신청/정원": listed.capacity_text,
        "교육장소": listed.venue,
        "교육대상": listed.target,
        "교육주기": listed.schedule,
    }
    for field, listed_value in listed_fields.items():
        if not _same_public_field(field, listed_value, fields.get(field)):
            raise GeumsanExperienceContractError(
                f"{listed.identity}: list/detail {field} drift"
            )
    if fields.get("신청방법") != method:
        raise GeumsanExperienceContractError(
            f"{listed.identity}: application method drift"
        )
    list_ok, title_evidence = _list_hands_on_decision(listed)
    content, fee = _detail_content(soup, listed.identity)
    content_ok, content_evidence = _content_hands_on_decision(content)
    if not list_ok or not content_ok:
        raise GeumsanExperienceContractError(
            f"{listed.identity}: detail no longer proves audited hands-on special"
        )
    application_control = _application_control_present(soup, listed.identity)
    if method != "인터넷" or source_status not in _ACTIVE_ACCEPT:
        raise GeumsanExperienceContractError(
            f"{listed.identity}: active online application contract changed"
        )
    start, end = _date_period(fields["교육기간"], listed.identity, "detail operation")
    apply_start, apply_end = _datetime_period(fields["접수기간"], listed.identity)
    capacity_current, capacity_total = _capacity(
        fields["신청/정원"], listed.identity
    )
    waitlist_current, waitlist_total = _waitlist(root, listed.identity)
    row: dict[str, Any] = {
        "provider": GEUMSAN_EXPERIENCE_PROVIDER,
        "provider_course_id": f"{GEUMSAN_EXPERIENCE_PROVIDER}:programme:{listed.identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": GEUMSAN_EXPERIENCE_BRANCH,
        "branch_code": "GEUMSAN_YOUTH_TRAINING_CENTER",
        "preserve_branch": True,
        "category": "문화예술 체험",
        "program_type": "교육·체험",
        "raw_url": listed.detail_url,
        "application_url": "",
        "application_type": "ONLINE_RESERVATION_SENSITIVE_ROUTE_NOT_EXPOSED",
        "application_method": "온라인 신청 경로 미노출",
        "application_methods": ["인터넷"],
        "reservation_available": True,
        "status": "OPEN",
        "fee": fee,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule_raw": fields["교육주기"] or fields["교육시간"],
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_current": waitlist_current,
        "waitlist_total": waitlist_total,
        "target": fields["교육대상"],
        "venue": fields["교육장소"],
        "venue_name": fields["교육장소"],
        "address": "",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "collection_type": GEUMSAN_EXPERIENCE_PARSER,
        "municipality_code": GEUMSAN_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": GEUMSAN_EXPERIENCE_MUNICIPALITY_NAME,
        "municipality_full_name": GEUMSAN_EXPERIENCE_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": listed.identity,
            "source_page": listed.page,
            "source_status": source_status,
            "source_education_state": education_state,
            "source_category": category,
            "source_method": method,
            "source_operator": fields["운영주체"],
            "source_venue": fields["교육장소"],
            "source_program_period": f"{start.isoformat()} ~ {end.isoformat()}",
            "source_application_period": f"{apply_start} ~ {apply_end}",
            "source_schedule": fields["교육주기"],
            "source_target": fields["교육대상"],
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "source_waitlist_current": waitlist_current,
            "source_waitlist_total": waitlist_total,
            "detail_verified": True,
            "hands_on_title_evidence": title_evidence,
            "hands_on_content_evidence": content_evidence,
            "application_control_present": application_control,
            "application_endpoint_not_requested": True,
            "service_family": "experience",
        },
    }
    return row


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden detail/PII key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw field allowlist exceeded")
    payload = repr(row)
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("contact data persisted")
    if row.get("application_url"):
        errors.append("application route persisted")
    if row.get("service_group") != "체험" or row.get("service_group_policy") != "locked":
        errors.append("experience classification escaped lock")
    return errors


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        re.sub(r"[^0-9a-z가-힣]+", "", _clean(row.get("title")).casefold()),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _clean(row.get("venue_name")),
    )


def collect_geumsan_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = GEUMSAN_EXPERIENCE_MAX_PAGES,
    detail_limit: int = 20,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Geumsan hands-on snapshot."""

    cutoff = _audit_date(today)
    meta: dict[str, Any] = {
        "municipality_code": GEUMSAN_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": GEUMSAN_EXPERIENCE_MUNICIPALITY_NAME,
        "owner_provider": GEUMSAN_EXPERIENCE_PROVIDER,
        "candidate_id": GEUMSAN_EXPERIENCE_CANDIDATE_ID,
        "parser": GEUMSAN_EXPERIENCE_PARSER,
        "ownership_scope": GEUMSAN_EXPERIENCE_OWNERSHIP_SCOPE,
        "cutoff": cutoff.isoformat(),
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "auth_endpoint_requests": 0,
        "member_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "identity_endpoint_requests": 0,
        "file_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "post_requests": 0,
        "pii_endpoint_requests": 0,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "errors": [],
        "configured_collection_error": "",
    }
    requester: Optional[_Requester] = None
    try:
        if not is_geumsan_experience_target(target):
            raise GeumsanExperienceContractError(
                "target is not the canonical Geumsan experience owner"
            )
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise GeumsanExperienceContractError("invalid collector limits")
        requester = _Requester(
            session_factory or _default_session,
            fetcher or _default_fetcher,
            timeout,
            meta,
        )
        first = _parse_list_page(
            requester.soup(geumsan_experience_list_url(1)), 1
        )
        required_boundary = first.last + 1
        if required_boundary > max_pages:
            meta["source_cap_reached"] = True
            raise GeumsanExperienceContractError(
                f"max_pages {max_pages} below required {required_boundary} "
                "including the clamp sentinel substitute"
            )
        pages: dict[int, _ListPage] = {1: first}
        for page_number in range(2, first.last + 1):
            parsed = _parse_list_page(
                requester.soup(geumsan_experience_list_url(page_number)),
                page_number,
            )
            if parsed.last != first.last or parsed.observed != page_number:
                raise GeumsanExperienceContractError(
                    f"page {page_number}: declared boundary changed"
                )
            pages[page_number] = parsed

        clamp_number = first.last + 1
        clamp = _parse_list_page(
            requester.soup(geumsan_experience_list_url(clamp_number)),
            clamp_number,
        )
        last_page = pages[first.last]
        if clamp.observed != first.last or clamp.last != first.last:
            raise GeumsanExperienceContractError(
                "post-last clamp did not resolve to the exact last page"
            )
        if _page_identity_sequence(clamp) != _page_identity_sequence(last_page):
            raise GeumsanExperienceContractError(
                "post-last clamp identity sequence differs from last page"
            )
        if _page_rowset_signature(clamp) != _page_rowset_signature(last_page):
            raise GeumsanExperienceContractError(
                "post-last clamp row set differs from last page"
            )

        for page_number, expected in (
            (1, pages[1]),
            (first.last, last_page),
            (clamp_number, clamp),
        ):
            rechecked = _parse_list_page(
                requester.soup(geumsan_experience_list_url(page_number)),
                page_number,
            )
            if (
                rechecked.observed != expected.observed
                or rechecked.last != expected.last
                or _page_identity_sequence(rechecked)
                != _page_identity_sequence(expected)
                or _page_rowset_signature(rechecked)
                != _page_rowset_signature(expected)
            ):
                raise GeumsanExperienceContractError(
                    f"page {page_number}: stability recheck changed"
                )

        listed = [
            row
            for page_number in range(1, first.last + 1)
            for row in pages[page_number].rows
        ]
        expected_total = (
            (first.last - 1) * GEUMSAN_EXPERIENCE_PAGE_SIZE
            + len(last_page.rows)
        )
        if len(listed) != expected_total:
            raise GeumsanExperienceContractError(
                "pager boundary and all-page source rows differ"
            )
        identities = [row.identity for row in listed]
        if len(identities) != len(set(identities)):
            raise GeumsanExperienceContractError(
                "duplicate identities across complete source pages"
            )
        current_future = [
            row
            for row in listed
            if row.end_date is not None and row.end_date >= cutoff
        ]
        decisions = [(row, _list_hands_on_decision(row)) for row in current_future]
        current_experience = [row for row, decision in decisions if decision[0]]
        current_non_experience = [row for row, decision in decisions if not decision[0]]
        if len(current_experience) > detail_limit:
            meta["source_cap_reached"] = True
            raise GeumsanExperienceContractError(
                "detail_limit would create a partial current experience snapshot"
            )

        output: list[dict[str, Any]] = []
        for listed_row in current_experience:
            detail = requester.soup(listed_row.detail_url)
            row = _parse_detail(listed_row, detail)
            privacy = _privacy_errors(row)
            if privacy:
                raise GeumsanExperienceContractError(
                    f"{listed_row.identity}: {'; '.join(privacy)}"
                )
            output.append(row)

        signatures = [_semantic_signature(row) for row in output]
        if len(signatures) != len(set(signatures)):
            raise GeumsanExperienceContractError(
                "returned snapshot contains semantic duplicate experiences"
            )
        before_dedupe = len(output)
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        if len(output) != before_dedupe:
            raise GeumsanExperienceContractError(
                "external dedupe removed identity-verified official rows"
            )

        meta.update(
            {
                "pages": first.last,
                "data_pages": first.last,
                "declared_last_page": first.last,
                "source_total": len(listed),
                "source_rows": len(listed),
                "page_counts": {
                    page_number: len(page.rows)
                    for page_number, page in pages.items()
                },
                "post_last_clamp_page": clamp_number,
                "post_last_clamp_observed_page": clamp.observed,
                "post_last_clamp_count": len(clamp.rows),
                "clamp_sentinel_substitute": True,
                "clamp_identity_sequence_verified": True,
                "clamp_rowset_verified": True,
                "boundary_rechecks": 3,
                "current_future_count": len(current_future),
                "current_source_count": len(current_future),
                "current_experience_count": len(current_experience),
                "excluded_non_experience_current_count": len(current_non_experience),
                "expired_count": len(listed) - len(current_future),
                "detail_attempts": len(current_experience),
                "detail_verified": len(current_experience),
                "source_status_counts": dict(
                    Counter(row.source_status for row in listed)
                ),
                "current_source_status_counts": dict(
                    Counter(row.source_status for row in current_future)
                ),
                "status_counts": dict(
                    Counter(str(row["status"]) for row in output)
                ),
                "application_control_count": sum(
                    bool(row["raw_fields"]["application_control_present"])
                    for row in output
                ),
                "application_url_persisted_count": sum(
                    bool(row.get("application_url")) for row in output
                ),
                "reservation_available_count": sum(
                    bool(row.get("reservation_available")) for row in output
                ),
                "municipality_counts": {
                    GEUMSAN_EXPERIENCE_MUNICIPALITY_CODE: len(output)
                },
                "semantic_duplicate_count": 0,
                "returned_count": len(output),
                "output_rows": len(output),
                "pagination_complete": True,
                "details_complete": True,
                "application_controls_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not output,
                "no_current_reason": (
                    f"{cutoff.isoformat()} 기준 공식 원장에 반환 가능한 활성 체험이 없음"
                    if not output
                    else ""
                ),
            }
        )
        return output, GEUMSAN_EXPERIENCE_PARSER, meta
    except Exception as exc:
        message = f"{type(exc).__name__}: {_clean(exc)}"
        meta.update(
            {
                "errors": [message],
                "configured_collection_error": message,
                "returned_count": 0,
                "output_rows": 0,
                "pagination_complete": False,
                "details_complete": False,
                "application_controls_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
            }
        )
        return [], GEUMSAN_EXPERIENCE_PARSER, meta
    finally:
        if requester is not None:
            requester.close()


collect = collect_geumsan_experience


__all__ = [
    "GEUMSAN_EXPERIENCE_PROVIDER",
    "GEUMSAN_EXPERIENCE_CANDIDATE_ID",
    "GEUMSAN_EXPERIENCE_HOST",
    "GEUMSAN_EXPERIENCE_LIST_PATH",
    "GEUMSAN_EXPERIENCE_DETAIL_PATH",
    "GEUMSAN_EXPERIENCE_APPLICATION_PATH",
    "GEUMSAN_EXPERIENCE_URL",
    "GEUMSAN_EXPERIENCE_PAGE_SIZE",
    "GEUMSAN_EXPERIENCE_MUNICIPALITY_CODE",
    "GEUMSAN_EXPERIENCE_MUNICIPALITY_NAME",
    "GEUMSAN_EXPERIENCE_BRANCH",
    "GEUMSAN_EXPERIENCE_PARSER",
    "GEUMSAN_EXPERIENCE_OWNERSHIP_SCOPE",
    "GeumsanExperienceContractError",
    "geumsan_experience_list_url",
    "geumsan_experience_detail_url",
    "is_geumsan_experience_target",
    "collect_geumsan_experience",
    "collect",
]
