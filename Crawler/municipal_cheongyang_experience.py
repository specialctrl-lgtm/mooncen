"""Exact public-GET collector for Cheongyang museum hands-on experiences.

The Baekje Culture Experience Museum publishes a small historical experience
ledger with a declared total, public detail pages and an immediate empty page
after the last data page.  Only current/future rows are detailed and emitted.

One audited wait-list row explicitly says that its structured date is a
virtual date created only for wait-list registration.  Such rows are retained
in source accounting but quarantined as ``virtual_waitlist_schedule`` rather
than being misrepresented as real experience sessions.

Only the public list and detail GET routes are allowed.  Reservation, login,
member, applicant, identity, file, attachment, download and PII routes are
never requested or exposed as application URLs.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


CHEONGYANG_EXPERIENCE_PROVIDER = "MUNI_WWW_CHEONGYANG_GO_KR_4308166C"
CHEONGYANG_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_8B34537DE697"
CHEONGYANG_EXPERIENCE_HOST = "www.cheongyang.go.kr"
CHEONGYANG_EXPERIENCE_LIST_PATH = "/prog/experCate/museum/sub04_02/list.do"
CHEONGYANG_EXPERIENCE_DETAIL_PATH = "/prog/experCate/museum/sub04_02/view.do"
CHEONGYANG_EXPERIENCE_APPLICATION_PATH = (
    "/prog/experReservation/museum/sub04_02/write.do"
)
CHEONGYANG_EXPERIENCE_APPLICANT_PATH = (
    "/prog/experReservation/museum/sub04_02/list.do"
)
CHEONGYANG_EXPERIENCE_URL = (
    f"https://{CHEONGYANG_EXPERIENCE_HOST}{CHEONGYANG_EXPERIENCE_LIST_PATH}"
)
CHEONGYANG_EXPERIENCE_PAGE_SIZE = 5
CHEONGYANG_EXPERIENCE_MAX_HTML_BYTES = 3_000_000
CHEONGYANG_EXPERIENCE_MUNICIPALITY_CODE = "4479000000"
CHEONGYANG_EXPERIENCE_MUNICIPALITY_NAME = "충청남도 청양군"
CHEONGYANG_EXPERIENCE_BRANCH = "백제문화체험박물관"
CHEONGYANG_EXPERIENCE_PARSER = (
    "cheongyang_baekje_museum_declared_historical_experience_ledger+"
    "complete_5_item_pages+exact_empty_post_last_sentinel+"
    "stable_first_last_sentinel+all_current_public_details+"
    "identity_period_status_fixed_venue_contract+hands_on_detail_lock+"
    "virtual_waitlist_schedule_quarantine+locked_experience+"
    "application_controls_observed_not_followed+"
    "no_login_member_application_applicant_identity_file_attachment_download_or_pii_calls"
)
CHEONGYANG_EXPERIENCE_OWNERSHIP_SCOPE = (
    "cheongyang_baekje_culture_experience_museum_current_future_sessions"
)

_PAGE_TITLE = "교육/체험 예약 > 교육/체험 >"
_TOTAL_RE = re.compile(r"-\s*총\s*([\d,]+)\s*건\s*등록되어\s*있습니다\.")
_IDENTITY_RE = re.compile(r"[1-9]\d{0,11}")
_DATE_PERIOD_RE = re.compile(
    r"(20\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])\s*~\s*"
    r"(20\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])"
)
_CAPACITY_RE = re.compile(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)\s*명")
_TOTAL_CAPACITY_RE = re.compile(r"(\d[\d,]*)\s*명")
_ROUND_TITLE_RE = re.compile(
    r"\((1[0-2]|0?[1-9])월\s*([0-2]?\d|3[01])일\s*"
    r"(2[0-3]|[01]?\d)시\s*~\s*(2[0-3]|[01]?\d)시\)"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_LIST_FIELDS = frozenset({"운영기간", "대상", "체험비", "정원"})
_DETAIL_FIELDS = frozenset(
    {"운영기간", "신청기간", "대상", "체험비", "신청/정원", "운영장소", "문의"}
)
_STATUS_MAP = {
    "접수중": "OPEN",
    "접수종료": "CLOSED",
    "접수예정": "SCHEDULED",
}
_HANDS_ON_MARKERS = ("체험 활동", "만들기", "직접 만들", "공방", "제작")
_EXCLUSION_MARKERS = ("공지", "알림", "시설대관", "시설대여", "공연")
_VIRTUAL_SCHEDULE_MARKERS = (
    "임의로 설정된 가상의 일정",
    "특정 회차 및 날짜 지정이 불가능",
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_page",
        "source_status",
        "source_program_period",
        "source_application_period",
        "source_capacity_current",
        "source_capacity_total",
        "venue_basis",
        "detail_verified",
        "hands_on_evidence",
        "virtual_waitlist_schedule",
        "application_control_present",
        "applicant_control_present",
        "application_and_applicant_endpoints_not_requested",
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
    }
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class CheongyangExperienceContractError(RuntimeError):
    """Raised when the audited public-source contract changes."""


@dataclass(frozen=True)
class _ListRow:
    identity: str
    page: int
    title: str
    source_status: str
    status: str
    start_date: date
    end_date: date
    target: str
    fee: str
    capacity_total: int
    detail_url: str


@dataclass(frozen=True)
class _ListPage:
    page: int
    total: int
    last: int
    rows: tuple[_ListRow, ...]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_cheongyang_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == CHEONGYANG_EXPERIENCE_PROVIDER
        and _clean(_target_value(target, "url")) == CHEONGYANG_EXPERIENCE_URL
    )


is_target = is_cheongyang_experience_target


def cheongyang_experience_list_url(page: int) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    return f"{CHEONGYANG_EXPERIENCE_URL}?{urlencode({'pageIndex': str(page)})}"


def cheongyang_experience_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise ValueError("invalid Cheongyang experience identity")
    return (
        f"https://{CHEONGYANG_EXPERIENCE_HOST}{CHEONGYANG_EXPERIENCE_DETAIL_PATH}?"
        f"{urlencode({'exper_no': value})}"
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
    if not (
        parsed.scheme == "https"
        and parsed.hostname == CHEONGYANG_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        raise CheongyangExperienceContractError("unsafe official request URL")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.path == CHEONGYANG_EXPERIENCE_LIST_PATH:
        if (
            len(query) == 1
            and query[0][0] == "pageIndex"
            and _IDENTITY_RE.fullmatch(query[0][1])
        ):
            return "list"
    if parsed.path == CHEONGYANG_EXPERIENCE_DETAIL_PATH:
        if (
            len(query) == 1
            and query[0][0] == "exper_no"
            and _IDENTITY_RE.fullmatch(query[0][1])
        ):
            return "detail"
    raise CheongyangExperienceContractError(
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
            raise CheongyangExperienceContractError(f"unexpected HTTP status {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise CheongyangExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(str(key).lower() == "location" and value for key, value in headers.items()):
            raise CheongyangExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and _canonical_key(final_url) != _canonical_key(url):
            raise CheongyangExperienceContractError("official response URL changed")
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
            raise CheongyangExperienceContractError("official response is not HTML")
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > CHEONGYANG_EXPERIENCE_MAX_HTML_BYTES:
            raise CheongyangExperienceContractError("empty or oversized official response")
        soup = BeautifulSoup(body, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title != _PAGE_TITLE:
            raise CheongyangExperienceContractError("official page title changed")
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
        raise CheongyangExperienceContractError("invalid audit date") from exc


def _period(value: str, identity: str, field: str) -> tuple[date, date]:
    match = _DATE_PERIOD_RE.fullmatch(_clean(value))
    if match is None:
        raise CheongyangExperienceContractError(f"{identity}: {field} period changed")
    try:
        start = date(int(match[1]), int(match[2]), int(match[3]))
        end = date(int(match[4]), int(match[5]), int(match[6]))
    except ValueError as exc:
        raise CheongyangExperienceContractError(
            f"{identity}: invalid {field} period"
        ) from exc
    if end < start:
        raise CheongyangExperienceContractError(f"{identity}: reversed {field} period")
    return start, end


def _field_rows(nodes: Iterable[Any], expected: frozenset[str], context: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for node in nodes:
        label_node = node.find("b", recursive=False)
        if label_node is None:
            raise CheongyangExperienceContractError(f"{context}: unlabeled field")
        label = _clean(label_node.get_text(" ", strip=True))
        clone = BeautifulSoup(str(node), "html.parser")
        clone_label = clone.find("b")
        if clone_label is not None:
            clone_label.decompose()
        value = _clean(clone.get_text(" ", strip=True))
        if not label or label in fields:
            raise CheongyangExperienceContractError(f"{context}: duplicate field")
        fields[label] = value
    if set(fields) != expected:
        raise CheongyangExperienceContractError(f"{context}: field contract changed")
    return fields


def _total_and_form(soup: BeautifulSoup, page: int) -> int:
    forms = soup.select(
        f'#txt form[method="post"][action="{CHEONGYANG_EXPERIENCE_LIST_PATH}"]'
    )
    if len(forms) != 1:
        raise CheongyangExperienceContractError(f"page {page}: list form changed")
    hidden = forms[0].select_one('input[name="pageIndex"][type="hidden"]')
    options = tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in forms[0].select('select[name="searchCondition"] > option[value]')
    )
    if (
        hidden is None
        or _clean(hidden.get("value")) != str(page)
        or options != (("subject", "체험명"), ("descript", "체험내용"))
    ):
        raise CheongyangExperienceContractError(f"page {page}: search scope changed")
    nodes = soup.select("#txt span.count_num")
    match = _TOTAL_RE.fullmatch(
        _clean(nodes[0].get_text(" ", strip=True)) if len(nodes) == 1 else ""
    )
    if match is None:
        raise CheongyangExperienceContractError(f"page {page}: declared total changed")
    return int(match[1].replace(",", ""))


def _list_identity_links(card: Any, page: int) -> tuple[str, str]:
    links = card.select(
        f'a[href*="{CHEONGYANG_EXPERIENCE_DETAIL_PATH}"]'
    )
    if len(links) != 2:
        raise CheongyangExperienceContractError(f"page {page}: detail controls changed")
    keys: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for link in links:
        parsed = urlparse(urljoin(CHEONGYANG_EXPERIENCE_URL, _clean(link.get("href"))))
        query = tuple(parse_qsl(parsed.query, keep_blank_values=True))
        if not (
            parsed.scheme == "https"
            and parsed.hostname == CHEONGYANG_EXPERIENCE_HOST
            and parsed.path == CHEONGYANG_EXPERIENCE_DETAIL_PATH
            and not parsed.fragment
        ):
            raise CheongyangExperienceContractError(
                f"page {page}: detail path changed"
            )
        keys.append((parsed.path, query))
    if keys[0] != keys[1]:
        raise CheongyangExperienceContractError(
            f"page {page}: duplicate detail controls disagree"
        )
    values = dict(keys[0][1])
    identity = values.get("exper_no", "")
    # The live owner has always emitted pageIndex=1, including page two.
    if not (
        len(keys[0][1]) == 2
        and set(values) == {"exper_no", "pageIndex"}
        and _IDENTITY_RE.fullmatch(identity)
        and values["pageIndex"] == "1"
    ):
        raise CheongyangExperienceContractError(
            f"page {page}: detail identity query changed"
        )
    return identity, cheongyang_experience_detail_url(identity)


def _parse_list_row(card: Any, page: int) -> _ListRow:
    status_node = card.select_one("figcaption b.p_tit > span.cat")
    title_node = card.select_one("figcaption b.p_tit > a[href]")
    fields_root = card.select_one("ul.info")
    if status_node is None or title_node is None or fields_root is None:
        raise CheongyangExperienceContractError(f"page {page}: experience card changed")
    identity, detail_url = _list_identity_links(card, page)
    title = _clean(title_node.get_text(" ", strip=True))
    source_status = _clean(status_node.get_text(" ", strip=True))
    status = _STATUS_MAP.get(source_status)
    if not title or status is None:
        raise CheongyangExperienceContractError(
            f"{identity}: title or public status changed"
        )
    if any(marker in title for marker in _EXCLUSION_MARKERS):
        # Excluded titles remain in exact source accounting.  Current rows are
        # detailed and classified before output, so no notice is emitted.
        pass
    fields = _field_rows(
        fields_root.find_all("li", recursive=False),
        _LIST_FIELDS,
        f"{identity}: list",
    )
    start, end = _period(fields["운영기간"], identity, "list operation")
    capacity = _TOTAL_CAPACITY_RE.fullmatch(fields["정원"])
    if capacity is None:
        raise CheongyangExperienceContractError(f"{identity}: list capacity changed")
    return _ListRow(
        identity,
        page,
        title,
        source_status,
        status,
        start,
        end,
        fields["대상"],
        fields["체험비"],
        int(capacity[1].replace(",", "")),
        detail_url,
    )


def _parse_list_page(soup: BeautifulSoup, page: int) -> _ListPage:
    total = _total_and_form(soup, page)
    last = max(1, math.ceil(total / CHEONGYANG_EXPERIENCE_PAGE_SIZE))
    roots = soup.select("#txt div.res_lst.special.bigcon > ul.sdisplay_list")
    if len(roots) != 1:
        raise CheongyangExperienceContractError(f"page {page}: list root changed")
    cards = roots[0].select(":scope > li")
    rows = tuple(_parse_list_row(card, page) for card in cards)
    expected = (
        min(
            CHEONGYANG_EXPERIENCE_PAGE_SIZE,
            total - ((page - 1) * CHEONGYANG_EXPERIENCE_PAGE_SIZE),
        )
        if page <= last and total
        else 0
    )
    if len(rows) != expected:
        raise CheongyangExperienceContractError(
            f"page {page}: declared total differs from identity rows"
        )
    identities = [row.identity for row in rows]
    if len(identities) != len(set(identities)):
        raise CheongyangExperienceContractError(f"page {page}: duplicate identities")
    return _ListPage(page, total, last, rows)


def _page_signature(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple(
            (
                row.identity,
                row.title,
                row.source_status,
                row.start_date,
                row.end_date,
                row.target,
                row.capacity_total,
            )
            for row in page.rows
        ),
    )


def _application_controls(
    root: Any,
    listed: _ListRow,
) -> tuple[bool, bool]:
    calendar_links = root.select("table.schcal_tbl a.ov[href]")
    if listed.status == "OPEN":
        if len(calendar_links) != 1:
            raise CheongyangExperienceContractError(
                f"{listed.identity}: application control count changed"
            )
        parsed = urlparse(
            urljoin(CHEONGYANG_EXPERIENCE_URL, _clean(calendar_links[0].get("href")))
        )
        query = parse_qsl(parsed.query, keep_blank_values=True)
        values = dict(query)
        if not (
            parsed.scheme == "https"
            and parsed.netloc == CHEONGYANG_EXPERIENCE_HOST
            and parsed.path == CHEONGYANG_EXPERIENCE_APPLICATION_PATH
            and not parsed.fragment
            and len(query) == 2
            and set(values) == {"exper_no", "exper_date"}
            and values["exper_no"] == listed.identity
            and values["exper_date"] == listed.start_date.isoformat()
            and "신청가능" in _clean(calendar_links[0].get_text(" ", strip=True))
        ):
            raise CheongyangExperienceContractError(
                f"{listed.identity}: application identity/path changed"
            )
    elif calendar_links:
        raise CheongyangExperienceContractError(
            f"{listed.identity}: inactive row gained application control"
        )
    applicant_controls: list[Any] = []
    for anchor in root.select("a.bn.bn_list[href]"):
        parsed = urlparse(
            urljoin(CHEONGYANG_EXPERIENCE_URL, _clean(anchor.get("href")))
        )
        if parsed.path == CHEONGYANG_EXPERIENCE_APPLICANT_PATH:
            applicant_controls.append((anchor, parsed))
    if len(applicant_controls) != 1:
        raise CheongyangExperienceContractError(
            f"{listed.identity}: applicant control changed"
        )
    _anchor, applicant_url = applicant_controls[0]
    applicant_query = parse_qsl(applicant_url.query, keep_blank_values=True)
    applicant_values = dict(applicant_query)
    if not (
        applicant_url.scheme == "https"
        and applicant_url.netloc == CHEONGYANG_EXPERIENCE_HOST
        and applicant_url.path == CHEONGYANG_EXPERIENCE_APPLICANT_PATH
        and not applicant_url.fragment
        and len(applicant_query) == 2
        and set(applicant_values) == {"exper_no", "pageIndex"}
        and applicant_values["exper_no"] == listed.identity
        and applicant_values["pageIndex"] == "1"
    ):
        raise CheongyangExperienceContractError(
            f"{listed.identity}: applicant control changed"
        )
    return bool(calendar_links), True


def _round_schedule(title: str, start: date, identity: str) -> str:
    match = _ROUND_TITLE_RE.search(title)
    if match is None:
        return ""
    month, day, start_hour, end_hour = (int(value) for value in match.groups())
    if (month, day) != (start.month, start.day) or end_hour <= start_hour:
        raise CheongyangExperienceContractError(
            f"{identity}: title round and structured operation date disagree"
        )
    return f"{start_hour:02d}:00 ~ {end_hour:02d}:00"


def _hands_on_evidence(title: str, detail_text: str) -> str:
    evidence = _clean(f"{title} {detail_text}")
    if any(marker in evidence for marker in _EXCLUSION_MARKERS):
        return ""
    markers = [marker for marker in _HANDS_ON_MARKERS if marker in evidence]
    if "만들기" not in markers and "직접 만들" not in markers and "제작" not in markers:
        return ""
    return ",".join(markers[:3])


def _row_from_detail(
    listed: _ListRow,
    soup: BeautifulSoup,
) -> tuple[dict[str, Any], str, bool, bool]:
    roots = soup.select("#txt div.res_lst.special.bigcon.detail")
    if len(roots) != 1:
        raise CheongyangExperienceContractError(
            f"{listed.identity}: detail root changed"
        )
    detail_root = roots[0]
    cards = detail_root.select(":scope > ul.sdisplay_list > li")
    if len(cards) != 1:
        raise CheongyangExperienceContractError(
            f"{listed.identity}: detail card changed"
        )
    card = cards[0]
    status_node = card.select_one("figcaption b.p_tit > span.cat")
    title_node = card.select_one('figcaption b.p_tit > a[name="subject"]')
    fields_root = card.select_one("ul.info")
    if status_node is None or title_node is None or fields_root is None:
        raise CheongyangExperienceContractError(
            f"{listed.identity}: detail identity fields changed"
        )
    if (
        _clean(status_node.get_text(" ", strip=True)) != listed.source_status
        or _clean(title_node.get_text(" ", strip=True)) != listed.title
    ):
        raise CheongyangExperienceContractError(
            f"{listed.identity}: list/detail title or status drift"
        )
    fields = _field_rows(
        fields_root.find_all("li", recursive=False),
        _DETAIL_FIELDS,
        f"{listed.identity}: detail",
    )
    start, end = _period(fields["운영기간"], listed.identity, "detail operation")
    apply_start, apply_end = _period(
        fields["신청기간"], listed.identity, "detail application"
    )
    capacity = _CAPACITY_RE.fullmatch(fields["신청/정원"])
    if capacity is None:
        raise CheongyangExperienceContractError(
            f"{listed.identity}: detail capacity changed"
        )
    capacity_current = int(capacity[1].replace(",", ""))
    capacity_total = int(capacity[2].replace(",", ""))
    if (
        (start, end) != (listed.start_date, listed.end_date)
        or fields["대상"] != listed.target
        or fields["체험비"] != listed.fee
        or capacity_total != listed.capacity_total
        or not fields["운영장소"]
    ):
        raise CheongyangExperienceContractError(
            f"{listed.identity}: list/detail programme identity drift"
        )
    headings = [
        heading
        for heading in soup.select("#txt h3")
        if _clean(heading.get_text(" ", strip=True)) == "체험내용"
    ]
    detail_text_node = soup.select_one("#txt p.caption_detail")
    if len(headings) != 1 or detail_text_node is None:
        raise CheongyangExperienceContractError(
            f"{listed.identity}: hands-on detail section changed"
        )
    detail_text = _clean(detail_text_node.get_text(" ", strip=True))
    virtual = all(marker in detail_text for marker in _VIRTUAL_SCHEDULE_MARKERS)
    hands_on = _hands_on_evidence(listed.title, detail_text)
    application_control, applicant_control = _application_controls(soup, listed)
    schedule = _round_schedule(listed.title, start, listed.identity)
    period = f"{start.isoformat()} ~ {end.isoformat()}"
    apply_period = f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
    row: dict[str, Any] = {
        "provider": CHEONGYANG_EXPERIENCE_PROVIDER,
        "provider_course_id": (
            f"{CHEONGYANG_EXPERIENCE_PROVIDER}:exper:{listed.identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": listed.title,
        "description": listed.title,
        "branch": CHEONGYANG_EXPERIENCE_BRANCH,
        "branch_code": "CHEONGYANG_BAEKJE_CULTURE_EXPERIENCE_MUSEUM",
        "preserve_branch": True,
        "category": "역사·공예 체험",
        "program_type": "교육·체험",
        "raw_url": listed.detail_url,
        "application_url": "",
        "application_type": (
            "ONLINE_RESERVATION_SENSITIVE_ROUTE_NOT_EXPOSED"
            if listed.status == "OPEN" and not virtual
            else "INFO_ONLY"
        ),
        "application_method": "온라인 신청" if listed.status == "OPEN" else "접수 종료",
        "reservation_available": listed.status == "OPEN" and not virtual,
        "status": listed.status,
        "fee": listed.fee,
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": apply_period,
        "schedule_raw": schedule,
        "capacity": str(capacity_total),
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "target": listed.target,
        "venue": fields["운영장소"],
        "venue_name": fields["운영장소"],
        "address": "",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "collection_type": CHEONGYANG_EXPERIENCE_PARSER,
        "municipality_code": CHEONGYANG_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": CHEONGYANG_EXPERIENCE_MUNICIPALITY_NAME,
        "municipality_full_name": CHEONGYANG_EXPERIENCE_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": listed.identity,
            "source_page": listed.page,
            "source_status": listed.source_status,
            "source_program_period": period,
            "source_application_period": apply_period,
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "venue_basis": "identity-verified public detail 운영장소",
            "detail_verified": True,
            "hands_on_evidence": hands_on,
            "virtual_waitlist_schedule": virtual,
            "application_control_present": application_control,
            "applicant_control_present": applicant_control,
            "application_and_applicant_endpoints_not_requested": True,
            "service_family": "experience",
        },
    }
    reason = (
        "virtual_waitlist_schedule"
        if virtual
        else "non_hands_on_programme"
        if not hands_on
        else ""
    )
    return row, reason, application_control, applicant_control


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
    return errors


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        re.sub(r"[^0-9a-z가-힣]+", "", _clean(row.get("title")).casefold()),
        _clean(row.get("start_date")),
        _clean(row.get("schedule_raw")),
        _clean(row.get("venue_name")),
    )


def collect_cheongyang_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 20,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future museum experience snapshot."""

    cutoff = _audit_date(today)
    meta: dict[str, Any] = {
        "municipality_code": CHEONGYANG_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": CHEONGYANG_EXPERIENCE_MUNICIPALITY_NAME,
        "owner_provider": CHEONGYANG_EXPERIENCE_PROVIDER,
        "candidate_id": CHEONGYANG_EXPERIENCE_CANDIDATE_ID,
        "parser": CHEONGYANG_EXPERIENCE_PARSER,
        "ownership_scope": CHEONGYANG_EXPERIENCE_OWNERSHIP_SCOPE,
        "cutoff": cutoff.isoformat(),
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "application_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "member_endpoint_requests": 0,
        "identity_endpoint_requests": 0,
        "file_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "errors": [],
        "configured_collection_error": "",
    }
    requester: Optional[_Requester] = None
    try:
        if not is_cheongyang_experience_target(target):
            raise CheongyangExperienceContractError(
                "target is not the canonical Cheongyang experience owner"
            )
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise CheongyangExperienceContractError("invalid collector limits")
        requester = _Requester(
            session_factory or _default_session,
            fetcher or _default_fetcher,
            timeout,
            meta,
        )
        first = _parse_list_page(
            requester.soup(cheongyang_experience_list_url(1)), 1
        )
        required_pages = first.last + 1
        if required_pages > max_pages:
            meta["source_cap_reached"] = True
            raise CheongyangExperienceContractError(
                f"max_pages {max_pages} below required {required_pages} including sentinel"
            )
        pages: dict[int, _ListPage] = {1: first}
        for page_number in range(2, first.last + 1):
            page = _parse_list_page(
                requester.soup(cheongyang_experience_list_url(page_number)),
                page_number,
            )
            if (page.total, page.last) != (first.total, first.last):
                raise CheongyangExperienceContractError(
                    f"page {page_number}: declared boundary changed"
                )
            pages[page_number] = page
        sentinel_number = first.last + 1
        sentinel = _parse_list_page(
            requester.soup(cheongyang_experience_list_url(sentinel_number)),
            sentinel_number,
        )
        if (
            sentinel.rows
            or (sentinel.total, sentinel.last) != (first.total, first.last)
        ):
            raise CheongyangExperienceContractError("immediate empty sentinel changed")
        for page_number, expected in (
            (1, pages[1]),
            (first.last, pages[first.last]),
            (sentinel_number, sentinel),
        ):
            rechecked = _parse_list_page(
                requester.soup(cheongyang_experience_list_url(page_number)),
                page_number,
            )
            if _page_signature(rechecked) != _page_signature(expected):
                raise CheongyangExperienceContractError(
                    f"page {page_number}: stability recheck changed"
                )

        listed = [
            row
            for page_number in range(1, first.last + 1)
            for row in pages[page_number].rows
        ]
        if len(listed) != first.total:
            raise CheongyangExperienceContractError(
                "declared total and all-page source rows differ"
            )
        identities = [row.identity for row in listed]
        if len(identities) != len(set(identities)):
            raise CheongyangExperienceContractError(
                "duplicate identities across complete source pages"
            )
        current = [row for row in listed if row.end_date >= cutoff]
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise CheongyangExperienceContractError(
                "detail_limit would create a partial current snapshot"
            )

        output: list[dict[str, Any]] = []
        excluded: list[dict[str, str]] = []
        verified_statuses: list[str] = []
        application_controls = 0
        applicant_controls = 0
        for listed_row in current:
            detail = requester.soup(listed_row.detail_url)
            row, reason, application_control, applicant_control = _row_from_detail(
                listed_row, detail
            )
            privacy = _privacy_errors(row)
            if privacy:
                raise CheongyangExperienceContractError(
                    f"{listed_row.identity}: {'; '.join(privacy)}"
                )
            verified_statuses.append(str(row["status"]))
            application_controls += int(application_control)
            applicant_controls += int(applicant_control)
            if reason:
                excluded.append(
                    {
                        "identity": listed_row.identity,
                        "reason": reason,
                        "source_status": listed_row.source_status,
                    }
                )
                continue
            output.append(row)

        signatures = [_semantic_signature(row) for row in output]
        if len(signatures) != len(set(signatures)):
            raise CheongyangExperienceContractError(
                "returned snapshot contains semantic duplicate experiences"
            )
        before_dedupe = len(output)
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        if len(output) != before_dedupe:
            raise CheongyangExperienceContractError(
                "external dedupe removed identity-verified official rows"
            )

        reasons = Counter(item["reason"] for item in excluded)
        meta.update(
            {
                "pages": first.last,
                "data_pages": first.last,
                "source_total": len(listed),
                "source_rows": len(listed),
                "page_counts": {
                    page_number: len(page.rows)
                    for page_number, page in pages.items()
                },
                "sentinel_page": sentinel_number,
                "sentinel_count": 0,
                "boundary_rechecks": 3,
                "source_status_counts": dict(Counter(row.source_status for row in listed)),
                "current_source_count": len(current),
                "expired_count": len(listed) - len(current),
                "detail_attempts": len(current),
                "detail_verified": len(current),
                "verified_source_status_counts": dict(Counter(verified_statuses)),
                "excluded_count": len(excluded),
                "excluded_reason_counts": dict(reasons),
                "excluded_rows": excluded,
                "virtual_waitlist_schedule_count": reasons.get(
                    "virtual_waitlist_schedule", 0
                ),
                "status_counts": dict(Counter(str(row["status"]) for row in output)),
                "application_control_count": application_controls,
                "applicant_control_count": applicant_controls,
                "application_url_persisted_count": sum(
                    bool(row.get("application_url")) for row in output
                ),
                "reservation_available_count": sum(
                    bool(row.get("reservation_available")) for row in output
                ),
                "municipality_counts": {
                    CHEONGYANG_EXPERIENCE_MUNICIPALITY_CODE: len(output)
                },
                "semantic_duplicate_count": 0,
                "returned_count": len(output),
                "output_rows": len(output),
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not output,
                "no_current_reason": (
                    f"{cutoff.isoformat()} 기준 공식 원장에 실제 일정이 확인된 현재·향후 체험이 없음"
                    if not output
                    else ""
                ),
            }
        )
        return output, CHEONGYANG_EXPERIENCE_PARSER, meta
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
                "snapshot_complete": False,
                "full_snapshot_validated": False,
            }
        )
        return [], CHEONGYANG_EXPERIENCE_PARSER, meta
    finally:
        if requester is not None:
            requester.close()


collect = collect_cheongyang_experience


__all__ = [
    "CHEONGYANG_EXPERIENCE_PROVIDER",
    "CHEONGYANG_EXPERIENCE_CANDIDATE_ID",
    "CHEONGYANG_EXPERIENCE_HOST",
    "CHEONGYANG_EXPERIENCE_LIST_PATH",
    "CHEONGYANG_EXPERIENCE_DETAIL_PATH",
    "CHEONGYANG_EXPERIENCE_APPLICATION_PATH",
    "CHEONGYANG_EXPERIENCE_APPLICANT_PATH",
    "CHEONGYANG_EXPERIENCE_URL",
    "CHEONGYANG_EXPERIENCE_PAGE_SIZE",
    "CHEONGYANG_EXPERIENCE_MUNICIPALITY_CODE",
    "CHEONGYANG_EXPERIENCE_MUNICIPALITY_NAME",
    "CHEONGYANG_EXPERIENCE_PARSER",
    "CHEONGYANG_EXPERIENCE_OWNERSHIP_SCOPE",
    "CheongyangExperienceContractError",
    "cheongyang_experience_list_url",
    "cheongyang_experience_detail_url",
    "is_cheongyang_experience_target",
    "collect_cheongyang_experience",
    "collect",
]
