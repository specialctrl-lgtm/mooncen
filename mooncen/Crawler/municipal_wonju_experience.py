"""Fail-closed collector for Wonju's official experience/visit ledger.

The official integrated-reservation page exposes one complete ``experience``
catalogue and five institution partitions.  The collector reconciles every
partition against the aggregate catalogue, walks all declared pages, checks an
exact empty post-last page, and rechecks the aggregate boundaries before it
publishes a snapshot.

Only public list and public programme-detail GET requests are allowlisted.
Calendar, application, login, applicant, attachment, download, and other
PII-bearing endpoints are never requested.  Identity-bound calendar controls
may be recorded from a public detail page without following them.
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
from bs4 import BeautifulSoup, Tag


WONJU_EXPERIENCE_PROVIDER = "MUNI_YEYAK_WONJU_GO_KR_602E7F89"
WONJU_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_C2063F2BEB37"
WONJU_EXPERIENCE_HOST = "yeyak.wonju.go.kr"
WONJU_EXPERIENCE_LIST_PATH = "/www/selectTnExprnRceptListU.do"
WONJU_EXPERIENCE_DETAIL_PATH = "/www/viewTnExprnRceptU.do"
WONJU_EXPERIENCE_CALENDAR_PATH = "/www/selectTnExprnRceptCalU.do"
WONJU_EXPERIENCE_MENU_KEY = "99"
WONJU_EXPERIENCE_URL = (
    f"https://{WONJU_EXPERIENCE_HOST}{WONJU_EXPERIENCE_LIST_PATH}?key="
    f"{WONJU_EXPERIENCE_MENU_KEY}"
)
WONJU_EXPERIENCE_MUNICIPALITY_CODE = "5113000000"
WONJU_EXPERIENCE_MUNICIPALITY_NAME = "강원특별자치도 원주시"
WONJU_EXPERIENCE_PAGE_SIZE = 9
WONJU_EXPERIENCE_MAX_HTML_BYTES = 3_000_000
WONJU_EXPERIENCE_PARSER = (
    "wonju_official_experience_complete_ledger+five_institution_partitions+"
    "declared_pages+exact_empty_post_last+stable_first_final+all_current_"
    "public_details+rolling_period_support+identity_bound_calendar_controls_"
    "no_fetch+locked_experience+no_login_application_attachment_or_pii_calls+"
    "atomic_snapshot"
)
WONJU_EXPERIENCE_OWNERSHIP_SCOPE = (
    "wonju_integrated_reservation_official_experience_visit_complete_ledger"
)

WONJU_EXPERIENCE_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_total": 8,
    "source_pages": 1,
    "sentinel_page": 2,
    "current_count": 7,
    "expired_count": 1,
    "detail_pages": 7,
    "institution_totals": {
        "8": 1,
        "1": 2,
        "2": 2,
        "21": 1,
        "25": 2,
    },
}


@dataclass(frozen=True)
class WonjuExperienceBranch:
    code: str
    name: str
    navigation_label: str


WONJU_EXPERIENCE_BRANCHES: tuple[WonjuExperienceBranch, ...] = (
    WonjuExperienceBranch("8", "원주시청", "원주시청"),
    WonjuExperienceBranch("1", "도시정보센터", "도시정보센터"),
    WonjuExperienceBranch("2", "원주산악자전거파크", "산악자전거"),
    WonjuExperienceBranch(
        "21", "남원주건강생활지원센터", "남원주건강생활지원센터"
    ),
    WonjuExperienceBranch("25", "영상미디어센터", "영상미디어센터"),
)
WONJU_EXPERIENCE_BRANCH_BY_CODE = {
    branch.code: branch for branch in WONJU_EXPERIENCE_BRANCHES
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Fetcher = Callable[[Any, str, int], Any]

_SPACE = re.compile(r"\s+")
_POSITIVE = re.compile(r"[1-9]\d*")
_TOTAL = re.compile(
    r"총게시물\s*:\s*([\d,]+)\s*건\s*페이지\s*:\s*([\d,]+)\s*/\s*([\d,]+)"
)
_DATE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_PHONE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
}
_ROLLING_PERIOD_MARKERS = ("신청일 기준", "상시", "연중")
_REQUIRED_INFO_FIELDS = frozenset({"장소", "요일", "기간"})
_REQUIRED_DETAIL_FIELDS = frozenset(
    {
        "접수기관",
        "프로그램명",
        "장소",
        "주소",
        "운영요일",
        "운영기간",
        "담당자/문의전화",
        "첨부파일",
    }
)
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "staff",
        "teacher",
        "instructor",
        "description",
        "content",
        "attachment",
        "attachments",
        "applicant",
        "raw_html",
    }
)


class WonjuExperienceContractError(ValueError):
    """Raised whenever the audited public-source contract changes."""


@dataclass(frozen=True)
class _ListRow:
    identity: str
    title: str
    source_status: str
    institution: str
    fee: str
    venue: str
    weekdays: str
    period: str
    start: Optional[date]
    end: Optional[date]
    page: int


@dataclass(frozen=True)
class _ListPage:
    page: int
    total: int
    last: int
    rows: tuple[_ListRow, ...]


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _parse_url(url: Any) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(_clean(url))
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    if len(pairs) != len(query):
        raise WonjuExperienceContractError("duplicate query key")
    if parsed.username or parsed.password or parsed.params or parsed.fragment:
        raise WonjuExperienceContractError("unsafe URL authority or fragment")
    try:
        if parsed.port is not None:
            raise WonjuExperienceContractError("explicit port is forbidden")
    except ValueError as exc:
        raise WonjuExperienceContractError("invalid URL port") from exc
    return parsed, query


def _request_kind(method: str, url: str) -> str:
    parsed, query = _parse_url(url)
    if (
        method != "GET"
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() != WONJU_EXPERIENCE_HOST
    ):
        raise WonjuExperienceContractError("request boundary changed")
    if parsed.path == WONJU_EXPERIENCE_LIST_PATH:
        if query.get("key") != WONJU_EXPERIENCE_MENU_KEY:
            raise WonjuExperienceContractError("experience menu key changed")
        allowed = {"key"}
        if "si1" in query:
            allowed.add("si1")
            if query["si1"] not in WONJU_EXPERIENCE_BRANCH_BY_CODE:
                raise WonjuExperienceContractError("unknown institution partition")
        if "pageIndex" in query:
            allowed.add("pageIndex")
            if not _POSITIVE.fullmatch(query["pageIndex"]):
                raise WonjuExperienceContractError("invalid public list page")
        if set(query) != allowed:
            raise WonjuExperienceContractError("list query is not allowlisted")
        return "list"
    if parsed.path == WONJU_EXPERIENCE_DETAIL_PATH:
        if (
            set(query) != {"progrmNo", "key"}
            or query.get("key") != WONJU_EXPERIENCE_MENU_KEY
            or not _POSITIVE.fullmatch(query.get("progrmNo", ""))
        ):
            raise WonjuExperienceContractError("detail identity changed")
        return "detail"
    raise WonjuExperienceContractError(
        "calendar/application/login/applicant/attachment/PII route refused"
    )


def _same_request_url(left: str, right: str) -> bool:
    left_parsed, left_query = _parse_url(left)
    right_parsed, right_query = _parse_url(right)
    return bool(
        left_parsed.scheme == right_parsed.scheme
        and left_parsed.hostname == right_parsed.hostname
        and left_parsed.path == right_parsed.path
        and left_query == right_query
    )


def is_wonju_experience_target(target: Any) -> bool:
    try:
        return bool(
            _clean(_target_value(target, "provider")) == WONJU_EXPERIENCE_PROVIDER
            and _same_request_url(
                _clean(_target_value(target, "url")), WONJU_EXPERIENCE_URL
            )
        )
    except WonjuExperienceContractError:
        return False


is_target = is_wonju_experience_target


def wonju_experience_list_url(
    page: int = 1, branch: Optional[WonjuExperienceBranch] = None
) -> str:
    if not isinstance(page, int) or page < 1:
        raise WonjuExperienceContractError("invalid experience list page")
    if branch is not None and branch not in WONJU_EXPERIENCE_BRANCHES:
        raise WonjuExperienceContractError("invalid experience branch")
    query: list[tuple[str, Any]] = [("key", WONJU_EXPERIENCE_MENU_KEY)]
    if branch is not None:
        query.append(("si1", branch.code))
    if page > 1:
        query.append(("pageIndex", page))
    return (
        f"https://{WONJU_EXPERIENCE_HOST}{WONJU_EXPERIENCE_LIST_PATH}?"
        + urlencode(query)
    )


def wonju_experience_detail_url(identity: Any) -> str:
    identity = _clean(identity)
    if not _POSITIVE.fullmatch(identity):
        raise WonjuExperienceContractError("invalid experience identity")
    return (
        f"https://{WONJU_EXPERIENCE_HOST}{WONJU_EXPERIENCE_DETAIL_PATH}?"
        + urlencode(
            (("progrmNo", identity), ("key", WONJU_EXPERIENCE_MENU_KEY))
        )
    )


def wonju_experience_calendar_url(identity: Any) -> str:
    identity = _clean(identity)
    if not _POSITIVE.fullmatch(identity):
        raise WonjuExperienceContractError("invalid experience identity")
    return (
        f"https://{WONJU_EXPERIENCE_HOST}{WONJU_EXPERIENCE_CALENDAR_PATH}?"
        + urlencode(
            (("progrmNo", identity), ("key", WONJU_EXPERIENCE_MENU_KEY))
        )
    )


def _calendar_identity(url: str) -> str:
    parsed, query = _parse_url(url)
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == WONJU_EXPERIENCE_HOST
        and parsed.path == WONJU_EXPERIENCE_CALENDAR_PATH
        and set(query) == {"progrmNo", "key"}
        and query.get("key") == WONJU_EXPERIENCE_MENU_KEY
        and _POSITIVE.fullmatch(query.get("progrmNo", ""))
    ):
        raise WonjuExperienceContractError("application control escaped calendar")
    return query["progrmNo"]


def _default_session() -> requests.Session:
    current = requests.Session()
    current.trust_env = False
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    return current.get(url, timeout=timeout, allow_redirects=False)


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
        kind = _request_kind("GET", url)
        self.meta["logical_requests"] += 1
        self.meta[f"{kind}_requests"] += 1
        response = self.fetcher(self.session, url, self.timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise WonjuExperienceContractError(f"HTTP {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise WonjuExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(
            str(key).lower() == "location" and value
            for key, value in headers.items()
        ):
            raise WonjuExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and not _same_request_url(final_url, url):
            raise WonjuExperienceContractError("response URL changed")
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
            raise WonjuExperienceContractError("unexpected content type")
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > WONJU_EXPERIENCE_MAX_HTML_BYTES:
            raise WonjuExperienceContractError("empty or oversized response")
        soup = BeautifulSoup(body, "html.parser")
        text = _clean(soup.get_text(" ", strip=True))[:5000].lower()
        if any(
            token in text
            for token in ("access denied", "request rejected", "captcha")
        ):
            raise WonjuExperienceContractError("source access restriction detected")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _dates(period: str) -> tuple[Optional[date], Optional[date]]:
    values: list[date] = []
    for year, month, day in _DATE.findall(period):
        values.append(date(int(year), int(month), int(day)))
    if len(values) == 2:
        if values[1] < values[0]:
            raise WonjuExperienceContractError("operation period is reversed")
        return values[0], values[1]
    if not values and any(marker in period for marker in _ROLLING_PERIOD_MARKERS):
        return None, None
    raise WonjuExperienceContractError("operation period shape changed")


def _card_info(card: Tag) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in card.select(".info > .info_item"):
        label_node = item.select_one(".info_sub")
        if label_node is None:
            raise WonjuExperienceContractError("card field label missing")
        label = _clean(label_node.get_text(" ", strip=True))
        value = _clean(item.get_text(" ", strip=True))
        if not value.startswith(label):
            raise WonjuExperienceContractError("card field binding changed")
        value = _clean(value[len(label) :])
        if not label or not value or label in result:
            raise WonjuExperienceContractError("card field changed")
        result[label] = value
    if set(result) != _REQUIRED_INFO_FIELDS:
        raise WonjuExperienceContractError("card field schema changed")
    return result


def _detail_identity(href: str) -> str:
    url = urljoin(WONJU_EXPERIENCE_URL, href)
    parsed, query = _parse_url(url)
    allowed = {"progrmNo", "key"}
    if "si1" in query:
        allowed.add("si1")
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == WONJU_EXPERIENCE_HOST
        and parsed.path == WONJU_EXPERIENCE_DETAIL_PATH
        and set(query) == allowed
        and query.get("key") == WONJU_EXPERIENCE_MENU_KEY
        and _POSITIVE.fullmatch(query.get("progrmNo", ""))
        and (
            "si1" not in query
            or query["si1"] in WONJU_EXPERIENCE_BRANCH_BY_CODE
        )
    ):
        raise WonjuExperienceContractError("card detail route changed")
    return query["progrmNo"]


def _parse_card(card: Tag, page: int) -> _ListRow:
    anchors = card.select("a.thumbnail_anchor[href]")
    if len(anchors) != 1:
        raise WonjuExperienceContractError("card detail anchor changed")
    identity = _detail_identity(_clean(anchors[0].get("href")))
    title_node = card.select_one(".thumbnail_sub")
    status_node = card.select_one(".stat")
    institution_node = card.select_one(".place")
    fee_node = card.select_one(".price")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    source_status = _clean(
        status_node.get_text(" ", strip=True) if status_node else ""
    )
    institution = _clean(
        institution_node.get_text(" ", strip=True) if institution_node else ""
    )
    fee = _clean(fee_node.get_text(" ", strip=True) if fee_node else "")
    if not title or not institution or not fee or source_status not in _STATUS_MAP:
        raise WonjuExperienceContractError("required card value changed")
    info = _card_info(card)
    start, end = _dates(info["기간"])
    return _ListRow(
        identity=identity,
        title=title,
        source_status=source_status,
        institution=institution,
        fee=fee,
        venue=info["장소"],
        weekdays=info["요일"],
        period=info["기간"],
        start=start,
        end=end,
        page=page,
    )


def _parse_list_page(soup: BeautifulSoup, page: int) -> _ListPage:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    root = soup.select_one(".program.program_list.experience_list")
    if "체험/견학 신청(전체)" not in title or root is None:
        raise WonjuExperienceContractError("official experience catalogue changed")
    total_node = root.select_one(".small")
    matched = _TOTAL.fullmatch(
        _clean(total_node.get_text(" ", strip=True) if total_node else "")
    )
    if matched is None:
        raise WonjuExperienceContractError("declared total/page marker changed")
    total, observed_page, last = (
        int(value.replace(",", "")) for value in matched.groups()
    )
    if observed_page != page or last < 1:
        raise WonjuExperienceContractError("declared current page changed")
    expected_last = max(1, math.ceil(total / WONJU_EXPERIENCE_PAGE_SIZE))
    if last != expected_last:
        raise WonjuExperienceContractError("declared page count changed")
    cards = root.select("ul.thumbnail_list > li.thumbnail_item")
    expected_rows = (
        min(
            WONJU_EXPERIENCE_PAGE_SIZE,
            max(0, total - ((page - 1) * WONJU_EXPERIENCE_PAGE_SIZE)),
        )
        if page <= last
        else 0
    )
    if len(cards) != expected_rows:
        raise WonjuExperienceContractError("declared list row count changed")
    rows = tuple(_parse_card(card, page) for card in cards)
    if len({row.identity for row in rows}) != len(rows):
        raise WonjuExperienceContractError("duplicate identity on list page")
    return _ListPage(page=page, total=total, last=last, rows=rows)


def _validate_branch_registry(soup: BeautifulSoup) -> None:
    observed: dict[str, set[str]] = {
        branch.code: set() for branch in WONJU_EXPERIENCE_BRANCHES
    }
    for anchor in soup.select("a[href*='selectTnExprnRceptListU.do'][href]"):
        try:
            parsed, query = _parse_url(
                urljoin(WONJU_EXPERIENCE_URL, _clean(anchor.get("href")))
            )
        except WonjuExperienceContractError:
            continue
        code = query.get("si1", "")
        if (
            parsed.hostname == WONJU_EXPERIENCE_HOST
            and parsed.path == WONJU_EXPERIENCE_LIST_PATH
            and code in observed
        ):
            observed[code].add(_clean(anchor.get_text(" ", strip=True)))
    missing = [
        branch.code
        for branch in WONJU_EXPERIENCE_BRANCHES
        if branch.navigation_label not in observed[branch.code]
    ]
    if missing:
        raise WonjuExperienceContractError(
            f"official institution registry changed: {','.join(missing)}"
        )


def _row_signature(row: _ListRow) -> tuple[Any, ...]:
    return (
        row.identity,
        row.title,
        row.source_status,
        row.institution,
        row.fee,
        row.venue,
        row.weekdays,
        row.period,
    )


def _page_signature(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.page,
        page.total,
        page.last,
        tuple(_row_signature(row) for row in page.rows),
    )


def _detail_pairs(root: Tag) -> tuple[dict[str, str], Tag]:
    matches: list[tuple[dict[str, str], Tag]] = []
    for table in root.select("table"):
        caption = table.select_one("caption")
        if caption is None or "체험견학 정보" not in _clean(
            caption.get_text(" ", strip=True)
        ):
            continue
        pairs: dict[str, str] = {}
        for row in table.select("tr"):
            headings = row.find_all("th", recursive=False)
            values = row.find_all("td", recursive=False)
            if len(headings) != 1 or len(values) != 1:
                raise WonjuExperienceContractError("detail table binding changed")
            key = _clean(headings[0].get_text(" ", strip=True))
            value = _clean(values[0].get_text(" ", strip=True))
            if not key or key in pairs:
                raise WonjuExperienceContractError("detail table field changed")
            pairs[key] = value
        matches.append((pairs, table))
    if len(matches) != 1:
        raise WonjuExperienceContractError("experience detail table changed")
    pairs, table = matches[0]
    if not _REQUIRED_DETAIL_FIELDS.issubset(pairs):
        raise WonjuExperienceContractError("required detail field changed")
    return pairs, table


def _application_controls(root: Tag, identity: str) -> tuple[str, int]:
    observed: set[str] = set()
    count = 0
    for anchor in root.select("a[href]"):
        href = _clean(anchor.get("href"))
        absolute = urljoin(WONJU_EXPERIENCE_URL, href)
        parsed = urlparse(absolute)
        if parsed.path != WONJU_EXPERIENCE_CALENDAR_PATH:
            continue
        count += 1
        if _calendar_identity(absolute) != identity:
            raise WonjuExperienceContractError("calendar control identity drift")
        observed.add(wonju_experience_calendar_url(identity))
    if len(observed) > 1:
        raise WonjuExperienceContractError("calendar control route drift")
    return (next(iter(observed), ""), count)


def _parse_detail(
    soup: BeautifulSoup,
    listed: _ListRow,
    branch: WonjuExperienceBranch,
) -> dict[str, Any]:
    root = soup.select_one(".program.program_view.experience_view")
    if root is None:
        raise WonjuExperienceContractError("official experience detail changed")
    heading = root.select_one(".view_topbox .topbox_sub")
    if _clean(heading.get_text(" ", strip=True) if heading else "") != listed.title:
        raise WonjuExperienceContractError("detail title drift")
    pairs, table = _detail_pairs(root)
    if (
        pairs["프로그램명"] != listed.title
        or pairs["접수기관"] != listed.institution
        or pairs["장소"] != listed.venue
        or pairs["운영요일"] != listed.weekdays
        or pairs["운영기간"] != listed.period
    ):
        raise WonjuExperienceContractError("list/detail identity field drift")
    detail_start, detail_end = _dates(pairs["운영기간"])
    if (detail_start, detail_end) != (listed.start, listed.end):
        raise WonjuExperienceContractError("list/detail operation date drift")
    application_url, application_controls = _application_controls(
        root, listed.identity
    )
    normalized_status = _STATUS_MAP[listed.source_status]
    if normalized_status == "OPEN" and not application_url:
        raise WonjuExperienceContractError("open programme lost calendar control")
    attachment_control_count = len(table.select("td a[href]"))
    return {
        "provider": WONJU_EXPERIENCE_PROVIDER,
        "municipality_code": WONJU_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": WONJU_EXPERIENCE_MUNICIPALITY_NAME,
        "provider_course_id": (
            f"{WONJU_EXPERIENCE_PROVIDER}:experience:{listed.identity}"
        ),
        "source_course_id": f"experience:{listed.identity}",
        "title": listed.title,
        "branch": branch.name,
        "branch_code": branch.code,
        "branch_url": wonju_experience_list_url(branch=branch),
        "preserve_branch": True,
        "category": f"원주시 체험·견학/{branch.name}",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
        "program_type": "체험",
        "source_status": listed.source_status,
        "status": normalized_status,
        "reservation_available": bool(
            normalized_status == "OPEN" and application_url
        ),
        "period": listed.period,
        "start_date": listed.start,
        "end_date": listed.end,
        "schedule_raw": listed.weekdays,
        "venue_name": listed.venue,
        "address": pairs["주소"],
        "fee": listed.fee,
        "application_url": application_url if normalized_status == "OPEN" else "",
        "raw_url": wonju_experience_detail_url(listed.identity),
        "raw_fields": {
            "parser": WONJU_EXPERIENCE_PARSER,
            "official_programme_number": listed.identity,
            "official_institution_code": branch.code,
            "official_institution": listed.institution,
            "official_source_status": listed.source_status,
            "list_page": listed.page,
            "rolling_operation_period": listed.end is None,
            "calendar_controls_observed_not_called": application_controls,
            "attachment_controls_observed_not_called": attachment_control_count,
        },
    }


def _is_current(row: _ListRow, cutoff: date) -> bool:
    if row.end is not None:
        return row.end >= cutoff
    return _STATUS_MAP[row.source_status] in {"OPEN", "SCHEDULED"}


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key).lower()
                child_path = f"{path}.{key}" if path else str(key)
                if key_text in _FORBIDDEN_OUTPUT_KEYS:
                    errors.append(f"forbidden key {child_path}")
                walk(child, child_path)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and (_PHONE.search(value) or _EMAIL.search(value)):
            errors.append(f"PII value in {path}")

    walk(row, "")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _meta() -> dict[str, Any]:
    return {
        "errors": [],
        "error_kind": "",
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "pagination_complete": False,
        "partitions_complete": False,
        "details_complete": False,
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "calendar_endpoint_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
    }


def collect_wonju_experience_courses(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session,
    dedupe_rows: DedupeRows = _dedupe_default,
    fetcher: Fetcher = _default_fetcher,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future snapshot of official experiences."""

    meta = _meta()
    if not is_wonju_experience_target(target):
        meta["errors"] = ["target does not match the canonical experience route"]
        meta["error_kind"] = "contract"
        return [], WONJU_EXPERIENCE_PARSER, meta
    if timeout < 1 or max_pages < 1 or detail_limit < 0:
        meta["errors"] = ["invalid collection limits"]
        meta["error_kind"] = "contract"
        return [], WONJU_EXPERIENCE_PARSER, meta

    cutoff = _today(today)
    requester = _Requester(session_factory, fetcher, timeout, meta)
    try:
        first_soup = requester.soup(wonju_experience_list_url(1))
        _validate_branch_registry(first_soup)
        first = _parse_list_page(first_soup, 1)
        if first.last > max_pages:
            raise WonjuExperienceContractError(
                "declared catalogue exceeds max_pages"
            )
        aggregate_pages = [first]
        for page_number in range(2, first.last + 1):
            page = _parse_list_page(
                requester.soup(wonju_experience_list_url(page_number)),
                page_number,
            )
            if page.total != first.total or page.last != first.last:
                raise WonjuExperienceContractError("aggregate declared total drift")
            aggregate_pages.append(page)
        aggregate_rows = [row for page in aggregate_pages for row in page.rows]
        if len(aggregate_rows) != first.total:
            raise WonjuExperienceContractError("aggregate row union incomplete")
        aggregate_by_id = {row.identity: row for row in aggregate_rows}
        if len(aggregate_by_id) != first.total:
            raise WonjuExperienceContractError("aggregate identity union changed")

        sentinel_number = first.last + 1
        sentinel = _parse_list_page(
            requester.soup(wonju_experience_list_url(sentinel_number)),
            sentinel_number,
        )
        if (
            sentinel.total != first.total
            or sentinel.last != first.last
            or sentinel.rows
        ):
            raise WonjuExperienceContractError("post-last page is not exact empty")
        stable_first = _parse_list_page(
            requester.soup(wonju_experience_list_url(1)), 1
        )
        stable_last = (
            stable_first
            if first.last == 1
            else _parse_list_page(
                requester.soup(wonju_experience_list_url(first.last)), first.last
            )
        )
        stable_sentinel = _parse_list_page(
            requester.soup(wonju_experience_list_url(sentinel_number)),
            sentinel_number,
        )
        if (
            _page_signature(stable_first) != _page_signature(first)
            or _page_signature(stable_last)
            != _page_signature(aggregate_pages[-1])
            or _page_signature(stable_sentinel) != _page_signature(sentinel)
        ):
            raise WonjuExperienceContractError("aggregate boundary stability changed")

        branch_for_identity: dict[str, WonjuExperienceBranch] = {}
        branch_totals: dict[str, int] = {}
        branch_pages: dict[str, int] = {}
        for branch in WONJU_EXPERIENCE_BRANCHES:
            branch_first = _parse_list_page(
                requester.soup(wonju_experience_list_url(1, branch)), 1
            )
            if branch_first.last > max_pages:
                raise WonjuExperienceContractError(
                    f"institution {branch.code} exceeds max_pages"
                )
            pages = [branch_first]
            for page_number in range(2, branch_first.last + 1):
                page = _parse_list_page(
                    requester.soup(
                        wonju_experience_list_url(page_number, branch)
                    ),
                    page_number,
                )
                if (
                    page.total != branch_first.total
                    or page.last != branch_first.last
                ):
                    raise WonjuExperienceContractError(
                        f"institution {branch.code} declared total drift"
                    )
                pages.append(page)
            rows = [row for page in pages for row in page.rows]
            if len(rows) != branch_first.total:
                raise WonjuExperienceContractError(
                    f"institution {branch.code} row union incomplete"
                )
            for row in rows:
                aggregate = aggregate_by_id.get(row.identity)
                if aggregate is None or _row_signature(row) != _row_signature(
                    aggregate
                ):
                    raise WonjuExperienceContractError(
                        f"institution {branch.code} row escaped aggregate"
                    )
                if row.identity in branch_for_identity:
                    raise WonjuExperienceContractError(
                        "institution partitions overlap"
                    )
                branch_for_identity[row.identity] = branch
            branch_totals[branch.code] = branch_first.total
            branch_pages[branch.code] = branch_first.last
        if set(branch_for_identity) != set(aggregate_by_id) or sum(
            branch_totals.values()
        ) != first.total:
            raise WonjuExperienceContractError(
                "five institution partitions do not reconcile"
            )

        current_rows = [row for row in aggregate_rows if _is_current(row, cutoff)]
        if len(current_rows) > detail_limit:
            raise WonjuExperienceContractError(
                "detail limit truncates current/future catalogue"
            )
        output: list[dict[str, Any]] = []
        for listed in current_rows:
            output.append(
                _parse_detail(
                    requester.soup(wonju_experience_detail_url(listed.identity)),
                    listed,
                    branch_for_identity[listed.identity],
                )
            )
        privacy = [error for row in output for error in _privacy_errors(row)]
        if privacy:
            raise WonjuExperienceContractError(
                f"PII/output allowlist violation: {privacy[0]}"
            )
        deduped = list(dedupe_rows(output))
        if len(deduped) != len(output):
            raise WonjuExperienceContractError("dedupe changed complete output")

        source_status_counts = Counter(row.source_status for row in aggregate_rows)
        status_counts = Counter(row["status"] for row in deduped)
        current_branch_counts = Counter(row["branch_code"] for row in deduped)
        application_control_count = sum(
            int(row["raw_fields"]["calendar_controls_observed_not_called"])
            for row in deduped
        )
        meta.update(
            {
                "source_total": first.total,
                "source_pages": first.last,
                "sentinel_page": sentinel_number,
                "institution_count": len(WONJU_EXPERIENCE_BRANCHES),
                "institution_totals": branch_totals,
                "institution_pages": branch_pages,
                "source_status_counts": dict(sorted(source_status_counts.items())),
                "current_count": len(current_rows),
                "expired_count": len(aggregate_rows) - len(current_rows),
                "returned_count": len(deduped),
                "detail_pages": len(current_rows),
                "status_counts": dict(sorted(status_counts.items())),
                "current_institution_counts": {
                    branch.code: current_branch_counts.get(branch.code, 0)
                    for branch in WONJU_EXPERIENCE_BRANCHES
                },
                "application_control_count": application_control_count,
                "source_identity_sha256": _identity_hash(aggregate_by_id),
                "current_identity_sha256": _identity_hash(
                    row.identity for row in current_rows
                ),
                "cutoff": cutoff.isoformat(),
                "no_current_data": not deduped,
                "duplicate_count": 0,
                "pagination_complete": True,
                "partitions_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, WONJU_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta["errors"] = [f"{type(exc).__name__}: {exc}"]
        meta["error_kind"] = (
            "contract"
            if isinstance(exc, WonjuExperienceContractError)
            else "network_or_parse"
        )
        return [], WONJU_EXPERIENCE_PARSER, meta
    finally:
        requester.close()


collect = collect_wonju_experience_courses


__all__ = [name for name in globals() if name.startswith("WONJU_EXPERIENCE_")] + [
    "WonjuExperienceBranch",
    "WonjuExperienceContractError",
    "collect",
    "collect_wonju_experience_courses",
    "is_target",
    "is_wonju_experience_target",
    "wonju_experience_calendar_url",
    "wonju_experience_detail_url",
    "wonju_experience_list_url",
]
