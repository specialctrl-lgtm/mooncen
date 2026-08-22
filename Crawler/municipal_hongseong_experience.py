"""Fail-closed collector for Hongseong's Lee Ung-no House experiences.

The official county reservation site exposes a historical, paged education
ledger for Lee Ung-no House.  This owner contains genuine hands-on art
experiences as well as education-shaped records, so current/future rows are
classified from their public programme text before they are emitted as
``체험``.  Every current experience is checked against its public detail page.

Only the public list and view GET routes are requestable.  The application,
login, member, applicant, identity, attachment, download and PII routes are
outside the request allowlist.  Application controls are validated in the
already-fetched detail HTML but are never followed or persisted as URLs.
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


HONGSEONG_EXPERIENCE_PROVIDER = "MUNI_WWW_HONGSEONG_GO_KR_44482E32"
HONGSEONG_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_3335E549E98D"
HONGSEONG_EXPERIENCE_HOST = "www.hongseong.go.kr"
HONGSEONG_EXPERIENCE_LIST_PATH = "/prog/educate/02/yeyak/sub01_05/list.do"
HONGSEONG_EXPERIENCE_DETAIL_PATH = "/prog/educate/02/yeyak/sub01_05/view.do"
HONGSEONG_EXPERIENCE_APPLICATION_PATH = (
    "/prog/educate/reserve/02/yeyak/sub01_05/write.do"
)
HONGSEONG_EXPERIENCE_URL = (
    f"https://{HONGSEONG_EXPERIENCE_HOST}{HONGSEONG_EXPERIENCE_LIST_PATH}"
)
HONGSEONG_EXPERIENCE_PAGE_SIZE = 10
HONGSEONG_EXPERIENCE_MAX_PAGES = 50
HONGSEONG_EXPERIENCE_MAX_HTML_BYTES = 3_000_000
HONGSEONG_EXPERIENCE_MUNICIPALITY_CODE = "4480000000"
HONGSEONG_EXPERIENCE_MUNICIPALITY_NAME = "충청남도 홍성군"
HONGSEONG_EXPERIENCE_BRANCH = "이응노의 집"
HONGSEONG_EXPERIENCE_PARSER = (
    "hongseong_lee_ung_no_house_historical_programme_ledger+"
    "pager_last_boundary+complete_10_item_pages+exact_empty_post_last_sentinel+"
    "stable_first_last_sentinel+all_current_experience_public_details+"
    "hands_on_classifier+title_structured_date_anomaly_quarantine+"
    "identity_bound_application_controls_no_follow+locked_experience+"
    "no_login_member_application_applicant_identity_attachment_download_or_pii_calls"
)
HONGSEONG_EXPERIENCE_OWNERSHIP_SCOPE = (
    "hongseong_lee_ung_no_house_current_future_hands_on_experiences"
)

_PAGE_TITLE = "이응노의 집 교육신청 > 교육/강좌 > 홍성군 통합예약시스템"
_NO_DATA_TEXT = "등록된 교육프로그램이 없습니다."
_LIST_FIELDS = frozenset({"운영기간", "장소", "프로그램내용", "문의전화"})
_DETAIL_FIELDS = frozenset({"운영기간", "접수기간", "장소", "문의전화", "신청현황"})
_IDENTITY_RE = re.compile(r"[1-9]\d{0,11}")
_DATE_PERIOD_RE = re.compile(
    r"(20\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])\s*~\s*"
    r"(20\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])"
)
_APPLICATION_PERIOD_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})부터\s*"
    r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})까지"
)
_CAPACITY_RE = re.compile(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)")
_TITLE_DATE_RE = re.compile(
    r"^\s*[\[(]\s*(1[0-2]|0?[1-9])\s*/\s*([0-2]?\d|3[01])"
    r"(?:\s*,[^\])]+)?\s*[\])]"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_EXPERIENCE_MARKERS = (
    "체험",
    "만들",
    "제작",
    "실습",
    "조색",
    "염색",
    "공예",
    "요리",
    "그리기",
    "키링",
    "도자",
    "가죽",
    "드로잉",
    "콜라주",
    "판화",
)
_EXCLUSION_MARKERS = (
    "공지",
    "알림",
    "채용",
    "위원회",
    "공연",
    "축제",
    "행사 안내",
    "시설대관",
    "시설대여",
    "물품대여",
)
_INACTIVE_STATES = {
    "신청마감": "CLOSED",
    "접수마감": "CLOSED",
    "접수예정": "SCHEDULED",
}
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
        "title_date_consistent",
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
    }
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class HongseongExperienceContractError(RuntimeError):
    """Raised when the audited public-source contract changes."""


@dataclass(frozen=True)
class _ListRow:
    identity: str
    page: int
    title: str
    start_date: date
    end_date: date
    venue: str
    programme_text: str
    detail_url: str
    is_experience: bool


@dataclass(frozen=True)
class _ListPage:
    page: int
    last: int
    rows: tuple[_ListRow, ...]
    sentinel: bool = False


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_hongseong_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == HONGSEONG_EXPERIENCE_PROVIDER
        and _clean(_target_value(target, "url")) == HONGSEONG_EXPERIENCE_URL
    )


is_target = is_hongseong_experience_target


def hongseong_experience_list_url(page: int) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    return (
        f"{HONGSEONG_EXPERIENCE_URL}?"
        f"{urlencode({'pageIndex': str(page)})}"
    )


def hongseong_experience_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise ValueError("invalid Hongseong experience identity")
    return (
        f"https://{HONGSEONG_EXPERIENCE_HOST}{HONGSEONG_EXPERIENCE_DETAIL_PATH}?"
        f"{urlencode({'eduNo': value})}"
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
        and parsed.hostname == HONGSEONG_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        raise HongseongExperienceContractError("unsafe official request URL")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.path == HONGSEONG_EXPERIENCE_LIST_PATH:
        if (
            len(query) == 1
            and query[0][0] == "pageIndex"
            and _IDENTITY_RE.fullmatch(query[0][1])
        ):
            return "list"
    if parsed.path == HONGSEONG_EXPERIENCE_DETAIL_PATH:
        if (
            len(query) == 1
            and query[0][0] == "eduNo"
            and _IDENTITY_RE.fullmatch(query[0][1])
        ):
            return "detail"
    raise HongseongExperienceContractError(
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
            raise HongseongExperienceContractError(f"unexpected HTTP status {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise HongseongExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(str(key).lower() == "location" and value for key, value in headers.items()):
            raise HongseongExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and _canonical_key(final_url) != _canonical_key(url):
            raise HongseongExperienceContractError("official response URL changed")
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
            raise HongseongExperienceContractError("official response is not HTML")
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > HONGSEONG_EXPERIENCE_MAX_HTML_BYTES:
            raise HongseongExperienceContractError("empty or oversized official response")
        soup = BeautifulSoup(body, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title != _PAGE_TITLE:
            raise HongseongExperienceContractError("official page title changed")
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
        raise HongseongExperienceContractError("invalid audit date") from exc


def _period(value: str, *, identity: str, field: str) -> tuple[date, date]:
    match = _DATE_PERIOD_RE.fullmatch(_clean(value))
    if match is None:
        raise HongseongExperienceContractError(
            f"{identity}: {field} period changed"
        )
    try:
        start = date(int(match[1]), int(match[2]), int(match[3]))
        end = date(int(match[4]), int(match[5]), int(match[6]))
    except ValueError as exc:
        raise HongseongExperienceContractError(
            f"{identity}: invalid {field} period"
        ) from exc
    if end < start:
        raise HongseongExperienceContractError(f"{identity}: reversed {field} period")
    return start, end


def _field_rows(nodes: Iterable[Any], *, expected: frozenset[str], context: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for node in nodes:
        label_node = node.find(["em", "strong"], recursive=False)
        if label_node is None:
            raise HongseongExperienceContractError(f"{context}: unlabeled field")
        label = _clean(label_node.get_text(" ", strip=True))
        clone = BeautifulSoup(str(node), "html.parser")
        clone_label = clone.find(["em", "strong"])
        if clone_label is not None:
            clone_label.decompose()
        value = _clean(clone.get_text(" ", strip=True))
        if not label or label in fields:
            raise HongseongExperienceContractError(f"{context}: duplicate field")
        fields[label] = value
    if set(fields) != expected:
        raise HongseongExperienceContractError(f"{context}: field contract changed")
    return fields


def _list_identity_link(href: Any, page: int) -> tuple[str, str]:
    parsed = urlparse(urljoin(HONGSEONG_EXPERIENCE_URL, _clean(href)))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(query)
    identity = values.get("eduNo", "")
    if not (
        parsed.scheme == "https"
        and parsed.hostname == HONGSEONG_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == HONGSEONG_EXPERIENCE_DETAIL_PATH
        and not parsed.fragment
        and len(query) == 3
        and set(values) == {"pageIndex", "eduNo", "kind"}
        and values["pageIndex"] == str(page)
        and _IDENTITY_RE.fullmatch(identity)
        and values["kind"] == ""
    ):
        raise HongseongExperienceContractError("public list identity link changed")
    return identity, hongseong_experience_detail_url(identity)


def _hands_on_decision(title: str, programme_text: str) -> tuple[bool, str]:
    evidence = _clean(f"{title} {programme_text}")
    exclusion = next((marker for marker in _EXCLUSION_MARKERS if marker in evidence), "")
    if exclusion:
        return False, f"excluded:{exclusion}"
    marker = next((marker for marker in _EXPERIENCE_MARKERS if marker in evidence), "")
    if not marker:
        return False, "no_hands_on_marker"
    return True, marker


def _parse_list_row(card: Any, page: int) -> _ListRow:
    subject = card.select_one(":scope > div.info_lst > strong.subject")
    fields_root = card.select_one(":scope > div.info_lst > ul")
    detail_links = card.select(
        f'a[href*="{HONGSEONG_EXPERIENCE_DETAIL_PATH}"]'
    )
    if subject is None or fields_root is None or len(detail_links) != 1:
        raise HongseongExperienceContractError(f"page {page}: programme card changed")
    title = _clean(subject.get_text(" ", strip=True))
    identity, detail_url = _list_identity_link(detail_links[0].get("href"), page)
    if not title:
        raise HongseongExperienceContractError(f"{identity}: blank programme title")
    fields = _field_rows(
        fields_root.find_all("li", recursive=False),
        expected=_LIST_FIELDS,
        context=f"{identity}: list",
    )
    start, end = _period(fields["운영기간"], identity=identity, field="list operation")
    venue = fields["장소"]
    programme_text = fields["프로그램내용"]
    # The historical ledger contains a few legacy rows with blank public
    # venue/content fields.  They remain in exact source accounting, but the
    # hands-on decision below keeps them outside the current experience set.
    is_experience, _evidence = _hands_on_decision(title, programme_text)
    return _ListRow(
        identity,
        page,
        title,
        start,
        end,
        venue,
        programme_text,
        detail_url,
        is_experience,
    )


def _pager_last(soup: BeautifulSoup, page: int) -> int:
    pagers = soup.select("#txt div.pagination")
    if len(pagers) != 1:
        raise HongseongExperienceContractError(f"page {page}: pager changed")
    pager = pagers[0]
    active = pager.select_one("li.page-item.active > a.page-link")
    if active is None or _clean(active.get_text(" ", strip=True)) != str(page):
        raise HongseongExperienceContractError(f"page {page}: active pager changed")
    anchors = pager.select('a.page-link[aria-label="last"][href]')
    if len(anchors) != 1:
        raise HongseongExperienceContractError(f"page {page}: last-page control changed")
    parsed = urlparse(urljoin(hongseong_experience_list_url(page), _clean(anchors[0].get("href"))))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if not (
        parsed.scheme == "https"
        and parsed.hostname == HONGSEONG_EXPERIENCE_HOST
        and parsed.path == HONGSEONG_EXPERIENCE_LIST_PATH
        and len(query) == 1
        and query[0][0] == "pageIndex"
        and _IDENTITY_RE.fullmatch(query[0][1])
    ):
        raise HongseongExperienceContractError(f"page {page}: last-page href changed")
    last = int(query[0][1])
    if last < page:
        raise HongseongExperienceContractError(f"page {page}: invalid last-page boundary")
    return last


def _parse_list_page(
    soup: BeautifulSoup,
    page: int,
    *,
    expected_last: Optional[int] = None,
) -> _ListPage:
    root = soup.select_one("#txt > ul#foodstay_lst")
    no_data = soup.select("#txt > div#edu_lst.center")
    if expected_last is not None and page == expected_last + 1:
        text = _clean(no_data[0].get_text(" ", strip=True)) if len(no_data) == 1 else ""
        if (
            root is not None
            or text != _NO_DATA_TEXT
            or soup.select_one("#txt div.pagination") is not None
            or soup.select(f'a[href*="{HONGSEONG_EXPERIENCE_DETAIL_PATH}"]')
        ):
            raise HongseongExperienceContractError("immediate empty sentinel changed")
        return _ListPage(page, expected_last, (), True)
    if root is None or no_data:
        raise HongseongExperienceContractError(f"page {page}: programme list root changed")
    last = _pager_last(soup, page)
    if expected_last is not None and last != expected_last:
        raise HongseongExperienceContractError(f"page {page}: pager boundary changed")
    cards = root.select(":scope > li.item")
    rows = tuple(_parse_list_row(card, page) for card in cards)
    expected_count = HONGSEONG_EXPERIENCE_PAGE_SIZE if page < last else None
    if page > last or (expected_count is not None and len(rows) != expected_count):
        raise HongseongExperienceContractError(f"page {page}: row boundary changed")
    if page == last and not (1 <= len(rows) <= HONGSEONG_EXPERIENCE_PAGE_SIZE):
        raise HongseongExperienceContractError("last data-page row boundary changed")
    identities = [row.identity for row in rows]
    if len(identities) != len(set(identities)):
        raise HongseongExperienceContractError(f"page {page}: duplicate identities")
    return _ListPage(page, last, rows)


def _page_signature(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.page,
        page.last,
        page.sentinel,
        tuple(
            (
                row.identity,
                row.title,
                row.start_date,
                row.end_date,
                row.venue,
                row.programme_text,
            )
            for row in page.rows
        ),
    )


def _application_period(value: str, identity: str) -> tuple[str, str]:
    match = _APPLICATION_PERIOD_RE.fullmatch(_clean(value))
    if match is None:
        raise HongseongExperienceContractError(
            f"{identity}: detail application period changed"
        )
    try:
        start = datetime.fromisoformat(f"{match[1]}T{match[2]}")
        end = datetime.fromisoformat(f"{match[3]}T{match[4]}")
    except ValueError as exc:
        raise HongseongExperienceContractError(
            f"{identity}: invalid application period"
        ) from exc
    if end < start:
        raise HongseongExperienceContractError(f"{identity}: reversed application period")
    return f"{match[1]} {match[2]}", f"{match[3]} {match[4]}"


def _detail_section(root: Any, label: str, identity: str) -> str:
    headings = [
        heading
        for heading in root.select("h3.h3")
        if _clean(heading.get_text(" ", strip=True)) == label
    ]
    if len(headings) != 1:
        raise HongseongExperienceContractError(f"{identity}: {label} section changed")
    value = headings[0].find_next_sibling("div", class_="edu_vmore2")
    if value is None:
        raise HongseongExperienceContractError(f"{identity}: {label} body changed")
    return _clean(value.get_text(" ", strip=True))


def _application_state(root: Any, identity: str) -> tuple[str, bool, str]:
    action = root.select_one("p.text-right")
    if action is None:
        raise HongseongExperienceContractError(f"{identity}: action area changed")
    application_links = action.select("a.btn-primary[href]")
    if application_links:
        if len(application_links) != 1:
            raise HongseongExperienceContractError(
                f"{identity}: application control count changed"
            )
        anchor = application_links[0]
        parsed = urlparse(urljoin(HONGSEONG_EXPERIENCE_URL, _clean(anchor.get("href"))))
        query = parse_qsl(parsed.query, keep_blank_values=True)
        values = dict(query)
        if not (
            parsed.scheme == "https"
            and parsed.hostname == HONGSEONG_EXPERIENCE_HOST
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and parsed.path == HONGSEONG_EXPERIENCE_APPLICATION_PATH
            and not parsed.fragment
            and len(query) == 2
            and set(values) == {"eduNo", "kind"}
            and values["eduNo"] == identity
            and values["kind"] == ""
            and _clean(anchor.get_text(" ", strip=True)) == "신청하기"
        ):
            raise HongseongExperienceContractError(
                f"{identity}: application identity/path changed"
            )
        return "OPEN", True, "신청하기"
    inactive = action.select("span.btn.btn-primary > em")
    if len(inactive) != 1:
        raise HongseongExperienceContractError(
            f"{identity}: inactive application state changed"
        )
    label = _clean(inactive[0].get_text(" ", strip=True))
    status = _INACTIVE_STATES.get(label)
    if status is None:
        raise HongseongExperienceContractError(
            f"{identity}: unknown application state {label!r}"
        )
    return status, False, label


def _title_date_anomaly(title: str, start: date, end: date) -> str:
    match = _TITLE_DATE_RE.match(title)
    if match is None:
        return ""
    title_month = int(match[1])
    title_day = int(match[2])
    if (title_month, title_day) != (start.month, start.day):
        return "title_round_date_differs_from_structured_start"
    if start != end:
        return "single_title_round_date_conflicts_with_structured_date_range"
    return ""


def _row_from_detail(
    listed: _ListRow,
    soup: BeautifulSoup,
) -> tuple[dict[str, Any], str, bool, str]:
    roots = soup.select("#txt > div.foodstaywrap")
    if len(roots) != 1:
        raise HongseongExperienceContractError(
            f"{listed.identity}: public detail root changed"
        )
    root = roots[0]
    heading = root.select_one(":scope > h2.h2")
    if heading is None or _clean(heading.get_text(" ", strip=True)) != listed.title:
        raise HongseongExperienceContractError(
            f"{listed.identity}: list/detail title mismatch"
        )
    info = root.select_one("#edu_view .foodstay_info > ul.list-1st")
    if info is None:
        raise HongseongExperienceContractError(
            f"{listed.identity}: public detail information changed"
        )
    fields = _field_rows(
        info.find_all("li", recursive=False),
        expected=_DETAIL_FIELDS,
        context=f"{listed.identity}: detail",
    )
    start, end = _period(
        fields["운영기간"], identity=listed.identity, field="detail operation"
    )
    if (start, end, fields["장소"]) != (
        listed.start_date,
        listed.end_date,
        listed.venue,
    ):
        raise HongseongExperienceContractError(
            f"{listed.identity}: list/detail programme identity drift"
        )
    apply_start, apply_end = _application_period(fields["접수기간"], listed.identity)
    capacity = _CAPACITY_RE.fullmatch(fields["신청현황"])
    if capacity is None:
        raise HongseongExperienceContractError(
            f"{listed.identity}: capacity contract changed"
        )
    capacity_current = int(capacity[1].replace(",", ""))
    capacity_total = int(capacity[2].replace(",", ""))
    if capacity_total < 1 or capacity_current < 0:
        raise HongseongExperienceContractError(
            f"{listed.identity}: invalid public capacity"
        )
    programme_text = _detail_section(root, "프로그램내용", listed.identity)
    _detail_section(root, "상세정보", listed.identity)
    if programme_text != listed.programme_text:
        raise HongseongExperienceContractError(
            f"{listed.identity}: list/detail programme text drift"
        )
    is_experience, hands_on_evidence = _hands_on_decision(listed.title, programme_text)
    if not listed.is_experience or not is_experience:
        raise HongseongExperienceContractError(
            f"{listed.identity}: detail no longer proves a hands-on experience"
        )
    status, application_control, source_status = _application_state(root, listed.identity)
    anomaly = _title_date_anomaly(listed.title, start, end)
    period = f"{start.isoformat()} ~ {end.isoformat()}"
    apply_period = f"{apply_start} ~ {apply_end}"
    row: dict[str, Any] = {
        "provider": HONGSEONG_EXPERIENCE_PROVIDER,
        "provider_course_id": (
            f"{HONGSEONG_EXPERIENCE_PROVIDER}:edu:{listed.identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": listed.title,
        "description": listed.title,
        "branch": HONGSEONG_EXPERIENCE_BRANCH,
        "branch_code": "HONGSEONG_LEE_UNG_NO_HOUSE",
        "preserve_branch": True,
        "category": "미술 체험",
        "program_type": "교육·체험",
        "raw_url": listed.detail_url,
        "application_url": "",
        "application_type": (
            "ONLINE_RESERVATION_SENSITIVE_ROUTE_NOT_EXPOSED"
            if status == "OPEN"
            else "INFO_ONLY"
        ),
        "application_method": "온라인 신청" if status == "OPEN" else "접수 종료",
        "reservation_available": status == "OPEN",
        "status": status,
        "fee": "",
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": apply_period,
        "schedule_raw": "",
        "capacity": str(capacity_total),
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "target": "",
        "venue": listed.venue,
        "venue_name": listed.venue,
        "address": "",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "collection_type": HONGSEONG_EXPERIENCE_PARSER,
        "municipality_code": HONGSEONG_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": HONGSEONG_EXPERIENCE_MUNICIPALITY_NAME,
        "municipality_full_name": HONGSEONG_EXPERIENCE_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": listed.identity,
            "source_page": listed.page,
            "source_status": source_status,
            "source_program_period": period,
            "source_application_period": apply_period,
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "venue_basis": "identity-verified public list/detail 장소",
            "detail_verified": True,
            "hands_on_evidence": hands_on_evidence,
            "title_date_consistent": not anomaly,
            "application_control_present": application_control,
            "application_endpoint_not_requested": True,
            "service_family": "experience",
        },
    }
    return row, anomaly, application_control, source_status


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
        _clean(row.get("end_date")),
        _clean(row.get("venue_name")),
    )


def collect_hongseong_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = HONGSEONG_EXPERIENCE_MAX_PAGES,
    detail_limit: int = 20,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Hongseong experience snapshot."""

    cutoff = _audit_date(today)
    meta: dict[str, Any] = {
        "municipality_code": HONGSEONG_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": HONGSEONG_EXPERIENCE_MUNICIPALITY_NAME,
        "owner_provider": HONGSEONG_EXPERIENCE_PROVIDER,
        "candidate_id": HONGSEONG_EXPERIENCE_CANDIDATE_ID,
        "parser": HONGSEONG_EXPERIENCE_PARSER,
        "ownership_scope": HONGSEONG_EXPERIENCE_OWNERSHIP_SCOPE,
        "cutoff": cutoff.isoformat(),
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "member_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "identity_endpoint_requests": 0,
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
        if not is_hongseong_experience_target(target):
            raise HongseongExperienceContractError(
                "target is not the canonical Hongseong experience owner"
            )
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise HongseongExperienceContractError("invalid collector limits")
        requester = _Requester(
            session_factory or _default_session,
            fetcher or _default_fetcher,
            timeout,
            meta,
        )
        first = _parse_list_page(
            requester.soup(hongseong_experience_list_url(1)), 1
        )
        required_pages = first.last + 1
        if required_pages > max_pages:
            meta["source_cap_reached"] = True
            raise HongseongExperienceContractError(
                f"max_pages {max_pages} below required {required_pages} including sentinel"
            )
        pages: dict[int, _ListPage] = {1: first}
        for page_number in range(2, first.last + 1):
            pages[page_number] = _parse_list_page(
                requester.soup(hongseong_experience_list_url(page_number)),
                page_number,
                expected_last=first.last,
            )
        sentinel_number = first.last + 1
        sentinel = _parse_list_page(
            requester.soup(hongseong_experience_list_url(sentinel_number)),
            sentinel_number,
            expected_last=first.last,
        )
        for page_number, expected in (
            (1, pages[1]),
            (first.last, pages[first.last]),
            (sentinel_number, sentinel),
        ):
            rechecked = _parse_list_page(
                requester.soup(hongseong_experience_list_url(page_number)),
                page_number,
                expected_last=(first.last if page_number != 1 else None),
            )
            if _page_signature(rechecked) != _page_signature(expected):
                raise HongseongExperienceContractError(
                    f"page {page_number}: stability recheck changed"
                )

        listed = [
            row
            for page_number in range(1, first.last + 1)
            for row in pages[page_number].rows
        ]
        expected_total = (
            (first.last - 1) * HONGSEONG_EXPERIENCE_PAGE_SIZE
            + len(pages[first.last].rows)
        )
        if len(listed) != expected_total:
            raise HongseongExperienceContractError(
                "pager boundary and all-page source rows differ"
            )
        identities = [row.identity for row in listed]
        if len(identities) != len(set(identities)):
            raise HongseongExperienceContractError(
                "duplicate identities across complete source pages"
            )
        current_source = [row for row in listed if row.end_date >= cutoff]
        current_experience = [row for row in current_source if row.is_experience]
        current_non_experience = [row for row in current_source if not row.is_experience]
        if len(current_experience) > detail_limit:
            meta["source_cap_reached"] = True
            raise HongseongExperienceContractError(
                "detail_limit would create a partial current experience snapshot"
            )

        output: list[dict[str, Any]] = []
        anomalies: list[dict[str, str]] = []
        verified_statuses: list[str] = []
        application_controls = 0
        for listed_row in current_experience:
            detail = requester.soup(listed_row.detail_url)
            row, anomaly, application_control, source_status = _row_from_detail(
                listed_row, detail
            )
            privacy = _privacy_errors(row)
            if privacy:
                raise HongseongExperienceContractError(
                    f"{listed_row.identity}: {'; '.join(privacy)}"
                )
            verified_statuses.append(str(row["status"]))
            application_controls += int(application_control)
            if anomaly:
                anomalies.append(
                    {
                        "identity": listed_row.identity,
                        "reason": anomaly,
                        "source_status": source_status,
                    }
                )
                continue
            output.append(row)

        signatures = [_semantic_signature(row) for row in output]
        if len(signatures) != len(set(signatures)):
            raise HongseongExperienceContractError(
                "returned snapshot contains semantic duplicate experiences"
            )
        before_dedupe = len(output)
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        if len(output) != before_dedupe:
            raise HongseongExperienceContractError(
                "external dedupe removed identity-verified official rows"
            )

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
                "experience_source_count": sum(row.is_experience for row in listed),
                "current_source_count": len(current_source),
                "current_experience_count": len(current_experience),
                "expired_count": len(listed) - len(current_source),
                "excluded_non_experience_current_count": len(current_non_experience),
                "title_date_anomaly_count": len(anomalies),
                "title_date_anomalies": anomalies,
                "excluded_count": len(current_non_experience) + len(anomalies),
                "detail_attempts": len(current_experience),
                "detail_verified": len(current_experience),
                "verified_source_status_counts": dict(Counter(verified_statuses)),
                "status_counts": dict(Counter(str(row["status"]) for row in output)),
                "application_control_count": application_controls,
                "application_url_persisted_count": sum(
                    bool(row.get("application_url")) for row in output
                ),
                "reservation_available_count": sum(
                    bool(row.get("reservation_available")) for row in output
                ),
                "municipality_counts": {
                    HONGSEONG_EXPERIENCE_MUNICIPALITY_CODE: len(output)
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
                    f"{cutoff.isoformat()} 기준 공식 원장에 반환 가능한 현재·향후 체험이 없음"
                    if not output
                    else ""
                ),
            }
        )
        return output, HONGSEONG_EXPERIENCE_PARSER, meta
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
        return [], HONGSEONG_EXPERIENCE_PARSER, meta
    finally:
        if requester is not None:
            requester.close()


collect = collect_hongseong_experience


__all__ = [
    "HONGSEONG_EXPERIENCE_PROVIDER",
    "HONGSEONG_EXPERIENCE_CANDIDATE_ID",
    "HONGSEONG_EXPERIENCE_HOST",
    "HONGSEONG_EXPERIENCE_LIST_PATH",
    "HONGSEONG_EXPERIENCE_DETAIL_PATH",
    "HONGSEONG_EXPERIENCE_APPLICATION_PATH",
    "HONGSEONG_EXPERIENCE_URL",
    "HONGSEONG_EXPERIENCE_PAGE_SIZE",
    "HONGSEONG_EXPERIENCE_MUNICIPALITY_CODE",
    "HONGSEONG_EXPERIENCE_MUNICIPALITY_NAME",
    "HONGSEONG_EXPERIENCE_PARSER",
    "HONGSEONG_EXPERIENCE_OWNERSHIP_SCOPE",
    "HongseongExperienceContractError",
    "hongseong_experience_list_url",
    "hongseong_experience_detail_url",
    "is_hongseong_experience_target",
    "collect_hongseong_experience",
    "collect",
]
