"""Fail-closed collector for Guri City's official experience catalogues.

Guri exposes eleven fixed public institution ledgers under ``selectWebEdcList``.
The list pages contain the complete current/future programme records and each
record has a public calendar detail.  This collector never follows reservation
steps, login, applicant, attachment, roster, or personal-information routes.
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
from bs4 import BeautifulSoup


GURI_EXPERIENCE_PROVIDER = "MUNI_WWW_GURI_GO_KR_E0C65498"
GURI_EXPERIENCE_HOST = "www.guri.go.kr"
GURI_EXPERIENCE_LIST_PATH = "/reserve/selectWebEdcList.do"
GURI_EXPERIENCE_DETAIL_PATH = "/reserve/selectWebEdcCalendar.do"
GURI_EXPERIENCE_MUNICIPALITY_CODE = "4131000000"
GURI_EXPERIENCE_MUNICIPALITY_NAME = "경기도 구리시"
GURI_EXPERIENCE_PAGE_SIZE = 10
GURI_EXPERIENCE_MAX_HTML_BYTES = 5_000_000
GURI_EXPERIENCE_PARSER = (
    "guri_experience_fixed_eleven_institutions+declared_totals+all_pages+"
    "exact_empty_post_last_sentinels+stable_boundaries+all_current_future_"
    "public_calendar_details+locked_experience+no_reservation_login_attachment_"
    "applicant_or_pii_calls"
)


@dataclass(frozen=True)
class GuriExperienceSource:
    code: str
    branch: str
    menu_key: str
    auth_site: str


GURI_EXPERIENCE_SOURCES: tuple[GuriExperienceSource, ...] = (
    GuriExperienceSource("insect_ecology", "곤충생태관", "3888", "AUTE01"),
    GuriExperienceSource(
        "jangja_ecology", "장자호수생태체험관", "3889", "AUTE02"
    ),
    GuriExperienceSource("safety", "안전체험관", "4012", "AUTE05"),
    GuriExperienceSource("public_health", "구리시보건소", "5172", "AUTE04"),
    GuriExperienceSource(
        "sutaek_health", "수택건강생활지원센터", "8601", "AUTE12"
    ),
    GuriExperienceSource(
        "children_health", "어린이 건강체험관", "5522", "AUTE07"
    ),
    GuriExperienceSource(
        "resource_circulation",
        "구리시자원순환교육센터",
        "6137",
        "AUTE08",
    ),
    GuriExperienceSource(
        "goguryeo_blacksmith", "고구려대장간마을", "3890", "AUTE03"
    ),
    GuriExperienceSource("culture_tourism", "문화관광", "4032", "AUTE06"),
    GuriExperienceSource("pet_care", "반려돌봄센터", "6916", "AUTE10"),
    GuriExperienceSource("children_forest", "유아숲체험", "7976", "AUTE11"),
)
GURI_EXPERIENCE_URL = (
    "https://www.guri.go.kr/reserve/"
    "selectWebEdcList.do?key=3888&searchAuthSite=AUTE01"
)

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Fetcher = Callable[[Any, str, int], Any]

_SPACE = re.compile(r"\s+")
_POSITIVE = re.compile(r"[1-9]\d*")
_NONNEGATIVE = re.compile(r"\d+")
_DATE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
}
_CARD_FIELDS = frozenset(
    {"프로그램 구분", "대상", "정원수", "신청기간", "프로그램 기간", "문의"}
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
        "image_url",
        "applicant",
        "raw_html",
    }
)


class GuriExperienceContractError(RuntimeError):
    """Raised when an audited public source changes unexpectedly."""


@dataclass(frozen=True)
class _ListRow:
    source: GuriExperienceSource
    identity: str
    title: str
    source_status: str
    program_type: str
    target: str
    capacity: int
    apply_period: str
    apply_start: date
    apply_end: date
    period: str
    start: date
    end: date
    page: int


@dataclass(frozen=True)
class _ListPage:
    source: GuriExperienceSource
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


def is_guri_experience_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == GURI_EXPERIENCE_PROVIDER
        and _clean(_target_value(target, "url")) == GURI_EXPERIENCE_URL
    )


is_target = is_guri_experience_target


def guri_experience_list_url(
    source: GuriExperienceSource, page: int = 1
) -> str:
    if source not in GURI_EXPERIENCE_SOURCES or not isinstance(page, int) or page < 1:
        raise GuriExperienceContractError("invalid experience list source/page")
    query: list[tuple[str, Any]] = [
        ("key", source.menu_key),
        ("searchAuthSite", source.auth_site),
    ]
    if page > 1:
        query.append(("pageIndex", page))
    return (
        f"https://{GURI_EXPERIENCE_HOST}{GURI_EXPERIENCE_LIST_PATH}?"
        + urlencode(query)
    )


def guri_experience_detail_url(
    source: GuriExperienceSource, identity: Any
) -> str:
    identity = _clean(identity)
    if source not in GURI_EXPERIENCE_SOURCES or not _POSITIVE.fullmatch(identity):
        raise GuriExperienceContractError("invalid experience detail identity")
    return (
        f"https://{GURI_EXPERIENCE_HOST}{GURI_EXPERIENCE_DETAIL_PATH}?"
        + urlencode(
            (
                ("key", source.menu_key),
                ("edcNo", identity),
                ("searchAuthSite", source.auth_site),
            )
        )
    )


def _query(url: str) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(_clean(url))
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(pairs)
    if len(pairs) != len(values):
        raise GuriExperienceContractError("duplicate query key")
    if parsed.username or parsed.password or parsed.fragment or parsed.params:
        raise GuriExperienceContractError("unsafe URL authority or fragment")
    try:
        if parsed.port is not None:
            raise GuriExperienceContractError("explicit port is forbidden")
    except ValueError as exc:
        raise GuriExperienceContractError("invalid URL port") from exc
    return parsed, values


def _source_for_query(query: Mapping[str, str]) -> GuriExperienceSource:
    matches = [
        source
        for source in GURI_EXPERIENCE_SOURCES
        if query.get("key") == source.menu_key
        and query.get("searchAuthSite") == source.auth_site
    ]
    if len(matches) != 1:
        raise GuriExperienceContractError("experience source identity changed")
    return matches[0]


def _request_kind(method: str, url: str) -> str:
    parsed, query = _query(url)
    if (
        method != "GET"
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() != GURI_EXPERIENCE_HOST
    ):
        raise GuriExperienceContractError("request boundary changed")
    if parsed.path == GURI_EXPERIENCE_LIST_PATH:
        allowed = (
            {"key", "searchAuthSite"}
            if "pageIndex" not in query
            else {"key", "searchAuthSite", "pageIndex"}
        )
        if set(query) != allowed or (
            "pageIndex" in query and not _POSITIVE.fullmatch(query["pageIndex"])
        ):
            raise GuriExperienceContractError("list query is not allowlisted")
        _source_for_query(query)
        return "list"
    if parsed.path == GURI_EXPERIENCE_DETAIL_PATH:
        if set(query) != {"key", "edcNo", "searchAuthSite"}:
            raise GuriExperienceContractError("detail query is not allowlisted")
        _source_for_query(query)
        if not _POSITIVE.fullmatch(query.get("edcNo", "")):
            raise GuriExperienceContractError("detail identity changed")
        return "detail"
    raise GuriExperienceContractError("route is not allowlisted")


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
            raise GuriExperienceContractError(f"HTTP {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise GuriExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(
            str(key).lower() == "location" and value
            for key, value in headers.items()
        ):
            raise GuriExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and _query(final_url) != _query(url):
            raise GuriExperienceContractError("response URL changed")
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
            raise GuriExperienceContractError("unexpected content type")
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > GURI_EXPERIENCE_MAX_HTML_BYTES:
            raise GuriExperienceContractError("empty or oversized response")
        soup = BeautifulSoup(body, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        text = _clean(soup.get_text(" ", strip=True))[:4000].lower()
        if any(
            token in f"{title.lower()} {text}"
            for token in ("access denied", "request rejected", "captcha")
        ):
            raise GuriExperienceContractError("source access restriction detected")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _validate_source_registry(soup: BeautifulSoup) -> None:
    observed: dict[str, tuple[str, str]] = {}
    for anchor in soup.select(".tab_list a[href*='selectWebEdcList.do']"):
        href = _clean(anchor.get("href"))
        if not href:
            continue
        absolute = urljoin(
            f"https://{GURI_EXPERIENCE_HOST}/reserve/", href
        )
        parsed, query = _query(absolute)
        if parsed.path != GURI_EXPERIENCE_LIST_PATH:
            continue
        try:
            source = _source_for_query(query)
        except GuriExperienceContractError:
            continue
        value = (source.menu_key, _clean(anchor.get_text(" ", strip=True)))
        previous = observed.get(source.auth_site)
        if previous is not None and previous != value:
            raise GuriExperienceContractError("source registry is ambiguous")
        observed[source.auth_site] = value
    expected = {
        source.auth_site: (source.menu_key, source.branch)
        for source in GURI_EXPERIENCE_SOURCES
    }
    if observed != expected:
        raise GuriExperienceContractError("official eleven-source registry changed")


def _date_pair(value: str, field: str) -> tuple[date, date]:
    found: list[date] = []
    for match in _DATE.finditer(_clean(value)):
        try:
            found.append(date(*(int(item) for item in match.groups())))
        except ValueError as exc:
            raise GuriExperienceContractError(f"invalid {field} date") from exc
    if len(found) != 2 or found[0] > found[1]:
        raise GuriExperienceContractError(f"invalid {field} period")
    return found[0], found[1]


def _card_pairs(card: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in card.select(".temp_contactbox li"):
        label = item.select_one(".title")
        value = item.select_one(".text")
        if label is None or value is None:
            raise GuriExperienceContractError("card field pairing changed")
        key = _clean(label.get_text(" ", strip=True))
        if not key or key in result:
            raise GuriExperienceContractError("duplicate card field")
        result[key] = _clean(value.get_text(" ", strip=True))
    if set(result) != _CARD_FIELDS:
        raise GuriExperienceContractError("card field registry changed")
    return result


def _parse_list_page(
    soup: BeautifulSoup, source: GuriExperienceSource, page: int
) -> _ListPage:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != f"{source.branch} - 구리시 통합예약포털":
        raise GuriExperienceContractError(
            f"{source.code} page {page}: title changed"
        )
    heading = [_clean(node.get_text(" ", strip=True)) for node in soup.select("h2:not(.skip)")]
    if not heading or heading[0] != source.branch:
        raise GuriExperienceContractError(
            f"{source.code} page {page}: heading changed"
        )
    form = soup.select_one("form[name='bbsNttSearchForm']")
    if (
        form is None
        or _clean(form.get("method")).upper() != "GET"
        or _clean(form.get("action")) != "./selectWebEdcList.do"
    ):
        raise GuriExperienceContractError(
            f"{source.code} page {page}: public search form changed"
        )
    key = form.select_one("input[name='key']")
    if key is None or _clean(key.get("value")) != source.menu_key:
        raise GuriExperienceContractError(
            f"{source.code} page {page}: public search key changed"
        )
    page_text = _clean(
        soup.select_one(".bbs_page").get_text(" ", strip=True)
        if soup.select_one(".bbs_page")
        else ""
    )
    match = re.fullmatch(
        r"총게시물\s*:\s*([\d,]+)\s*건\s*페이지\s*:\s*(\d+)\s*/\s*(\d+)\s*"
        r"\*접수중인 프로그램만 표시됩니다\.",
        page_text,
    )
    if match is None:
        raise GuriExperienceContractError(
            f"{source.code} page {page}: pager changed"
        )
    total = int(match.group(1).replace(",", ""))
    declared_page = int(match.group(2))
    last = int(match.group(3))
    expected_last = max(1, math.ceil(total / GURI_EXPERIENCE_PAGE_SIZE))
    if declared_page != page or last != expected_last:
        raise GuriExperienceContractError(
            f"{source.code} page {page}: pager boundary changed"
        )
    rows: list[_ListRow] = []
    for card in soup.select(".facility_box_list .facility_item"):
        identities = {
            _clean(node.get("value"))
            for node in card.select("input[name='edcNo']")
            if _clean(node.get("value"))
        }
        if len(identities) != 1:
            raise GuriExperienceContractError(
                f"{source.code} page {page}: card identity changed"
            )
        identity = identities.pop()
        if not _POSITIVE.fullmatch(identity):
            raise GuriExperienceContractError(
                f"{source.code} page {page}: invalid card identity"
            )
        status_node = card.select_one(".facility_title .category")
        title_node = card.select_one(".facility_title .tit")
        source_status = _clean(
            status_node.get_text(" ", strip=True) if status_node else ""
        )
        title_text = _clean(
            title_node.get_text(" ", strip=True) if title_node else ""
        )
        if source_status not in _STATUS_MAP or not title_text:
            raise GuriExperienceContractError(
                f"course {identity}: title/status changed"
            )
        expected_detail = guri_experience_detail_url(source, identity)
        href = _clean(title_node.get("href"))
        absolute_href = urljoin(
            f"https://{GURI_EXPERIENCE_HOST}/reserve/", href
        )
        if _STATUS_MAP[source_status] == "OPEN":
            if _query(absolute_href) != _query(expected_detail):
                raise GuriExperienceContractError(
                    f"course {identity}: open calendar link changed"
                )
        elif href not in {"#", "#n"} and _query(absolute_href) != _query(
            expected_detail
        ):
            raise GuriExperienceContractError(
                f"course {identity}: calendar link changed"
            )
        fields = _card_pairs(card)
        if not _NONNEGATIVE.fullmatch(fields["정원수"]):
            raise GuriExperienceContractError(
                f"course {identity}: capacity changed"
            )
        apply_start, apply_end = _date_pair(fields["신청기간"], "application")
        start, end = _date_pair(fields["프로그램 기간"], "programme")
        rows.append(
            _ListRow(
                source=source,
                identity=identity,
                title=title_text,
                source_status=source_status,
                program_type=fields["프로그램 구분"],
                target=fields["대상"],
                capacity=int(fields["정원수"]),
                apply_period=fields["신청기간"],
                apply_start=apply_start,
                apply_end=apply_end,
                period=fields["프로그램 기간"],
                start=start,
                end=end,
                page=page,
            )
        )
    if page <= last:
        expected_rows = (
            0
            if total == 0
            else GURI_EXPERIENCE_PAGE_SIZE
            if page < last
            else total % GURI_EXPERIENCE_PAGE_SIZE or GURI_EXPERIENCE_PAGE_SIZE
        )
    else:
        expected_rows = 0
    if len(rows) != expected_rows:
        raise GuriExperienceContractError(
            f"{source.code} page {page}: row count changed"
        )
    if len({row.identity for row in rows}) != len(rows):
        raise GuriExperienceContractError(
            f"{source.code} page {page}: duplicate identities"
        )
    return _ListPage(source, page, total, last, tuple(rows))


def _page_signature(value: _ListPage) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.identity,
            row.title,
            row.source_status,
            row.program_type,
            row.target,
            row.capacity,
            row.apply_period,
            row.period,
        )
        for row in value.rows
    )


def _parse_detail(soup: BeautifulSoup, listed: _ListRow) -> dict[str, Any]:
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if page_title != f"{listed.source.branch} - 구리시 통합예약포털":
        raise GuriExperienceContractError(
            f"course {listed.identity}: detail page title changed"
        )
    headings = [
        _clean(node.get_text(" ", strip=True))
        for node in soup.select("h2:not(.skip)")
    ]
    if not headings or headings[0] != listed.source.branch:
        raise GuriExperienceContractError(
            f"course {listed.identity}: detail branch changed"
        )
    programme_titles = [
        _clean(node.get_text(" ", strip=True))
        for node in soup.select(".schedule_box .s_title")
    ]
    if programme_titles != [listed.title]:
        raise GuriExperienceContractError(
            f"course {listed.identity}: calendar title binding changed"
        )
    raw_url = guri_experience_detail_url(listed.source, listed.identity)
    return {
        "provider": GURI_EXPERIENCE_PROVIDER,
        "municipality_code": GURI_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": GURI_EXPERIENCE_MUNICIPALITY_NAME,
        "provider_course_id": (
            f"{GURI_EXPERIENCE_PROVIDER}:experience:"
            f"{listed.source.auth_site}:{listed.identity}"
        ),
        "source_course_id": (
            f"experience:{listed.source.auth_site}:{listed.identity}"
        ),
        "title": listed.title,
        "branch": listed.source.branch,
        "preserve_branch": True,
        "category": f"구리시 체험/견학/{listed.program_type}",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
        "program_type": listed.program_type or "체험",
        "source_status": listed.source_status,
        "status": _STATUS_MAP[listed.source_status],
        "reservation_available": _STATUS_MAP[listed.source_status] == "OPEN",
        "period": listed.period,
        "start_date": listed.start.isoformat(),
        "end_date": listed.end.isoformat(),
        "apply_period": listed.apply_period,
        "apply_start_date": listed.apply_start.isoformat(),
        "apply_end_date": listed.apply_end.isoformat(),
        "target": listed.target,
        "venue_name": listed.source.branch,
        "application_url": "",
        "capacity_total": listed.capacity,
        "raw_url": raw_url,
        "raw_fields": {
            "parser": GURI_EXPERIENCE_PARSER,
            "edc_no": listed.identity,
            "auth_site": listed.source.auth_site,
            "official_branch": listed.source.branch,
            "official_program_type": listed.program_type,
            "official_source_status": listed.source_status,
            "list_page": listed.page,
            "calendar_detail_verified": True,
            "reservation_steps_observed_not_called": True,
        },
    }


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
        "details_complete": False,
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "reservation_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
    }


def collect_guri_experience_courses(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 60,
    detail_limit: int = 400,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session,
    dedupe_rows: DedupeRows = _dedupe_default,
    fetcher: Fetcher = _default_fetcher,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return an atomic snapshot of all eleven official experience ledgers."""

    meta = _meta()
    if not is_guri_experience_target(target):
        meta["errors"] = ["target does not match the canonical experience route"]
        meta["error_kind"] = "contract"
        return [], GURI_EXPERIENCE_PARSER, meta
    if timeout < 1 or max_pages < 1 or detail_limit < 0:
        meta["errors"] = ["invalid collection limits"]
        meta["error_kind"] = "contract"
        return [], GURI_EXPERIENCE_PARSER, meta

    cutoff = _today(today)
    requester = _Requester(session_factory, fetcher, timeout, meta)
    try:
        all_rows: list[_ListRow] = []
        source_summaries: dict[str, dict[str, Any]] = {}
        global_pages = 0
        for source_index, source in enumerate(GURI_EXPERIENCE_SOURCES):
            first_soup = requester.soup(guri_experience_list_url(source, 1))
            if source_index == 0:
                _validate_source_registry(first_soup)
            first = _parse_list_page(first_soup, source, 1)
            if first.last > max_pages:
                raise GuriExperienceContractError(
                    f"{source.code}: declared pages exceed collection limit"
                )
            pages = [first]
            for page in range(2, first.last + 1):
                value = _parse_list_page(
                    requester.soup(guri_experience_list_url(source, page)),
                    source,
                    page,
                )
                if value.total != first.total or value.last != first.last:
                    raise GuriExperienceContractError(
                        f"{source.code}: declared total drift"
                    )
                pages.append(value)
            source_rows = [row for value in pages for row in value.rows]
            if len(source_rows) != first.total or len(
                {row.identity for row in source_rows}
            ) != first.total:
                raise GuriExperienceContractError(
                    f"{source.code}: complete identity union changed"
                )
            sentinel_page = first.last + 1
            sentinel = _parse_list_page(
                requester.soup(guri_experience_list_url(source, sentinel_page)),
                source,
                sentinel_page,
            )
            if sentinel.total != first.total or sentinel.last != first.last or sentinel.rows:
                raise GuriExperienceContractError(
                    f"{source.code}: post-last sentinel changed"
                )
            stable_first = _parse_list_page(
                requester.soup(guri_experience_list_url(source, 1)), source, 1
            )
            stable_last = _parse_list_page(
                requester.soup(guri_experience_list_url(source, first.last)),
                source,
                first.last,
            )
            stable_sentinel = _parse_list_page(
                requester.soup(guri_experience_list_url(source, sentinel_page)),
                source,
                sentinel_page,
            )
            if (
                _page_signature(stable_first) != _page_signature(first)
                or _page_signature(stable_last) != _page_signature(pages[-1])
                or _page_signature(stable_sentinel) != _page_signature(sentinel)
            ):
                raise GuriExperienceContractError(
                    f"{source.code}: list boundary stability changed"
                )
            all_rows.extend(source_rows)
            global_pages += first.last
            source_summaries[source.code] = {
                "branch": source.branch,
                "source_total": first.total,
                "pages": first.last,
                "sentinel_page": sentinel_page,
            }

        composite_ids = {
            (row.source.auth_site, row.identity) for row in all_rows
        }
        if len(composite_ids) != len(all_rows):
            raise GuriExperienceContractError("global composite identities changed")
        current_rows = [row for row in all_rows if row.end >= cutoff]
        if len(current_rows) > detail_limit:
            raise GuriExperienceContractError(
                "detail limit truncates the current/future catalogue"
            )
        output: list[dict[str, Any]] = []
        for listed in current_rows:
            output.append(
                _parse_detail(
                    requester.soup(
                        guri_experience_detail_url(
                            listed.source, listed.identity
                        )
                    ),
                    listed,
                )
            )
        privacy = [error for row in output for error in _privacy_errors(row)]
        if privacy:
            raise GuriExperienceContractError(
                f"PII/output allowlist violation: {privacy[0]}"
            )
        deduped = list(dedupe_rows(output))
        if len(deduped) != len(output):
            raise GuriExperienceContractError("dedupe changed complete output")

        source_status_counts = Counter(row.source_status for row in all_rows)
        status_counts = Counter(row["status"] for row in deduped)
        current_branch_counts = Counter(row["branch"] for row in deduped)
        meta.update(
            {
                "source_count": len(GURI_EXPERIENCE_SOURCES),
                "source_total": len(all_rows),
                "source_pages": global_pages,
                "source_summaries": source_summaries,
                "source_status_counts": dict(sorted(source_status_counts.items())),
                "current_source_count": len(current_rows),
                "expired_count": len(all_rows) - len(current_rows),
                "returned_count": len(deduped),
                "detail_pages": len(current_rows),
                "status_counts": dict(sorted(status_counts.items())),
                "branch_counts": {
                    source.branch: current_branch_counts.get(source.branch, 0)
                    for source in GURI_EXPERIENCE_SOURCES
                },
                "zero_branch_count": sum(
                    not current_branch_counts.get(source.branch, 0)
                    for source in GURI_EXPERIENCE_SOURCES
                ),
                "source_identity_sha256": _identity_hash(
                    f"{row.source.auth_site}:{row.identity}" for row in all_rows
                ),
                "current_identity_sha256": _identity_hash(
                    f"{row.source.auth_site}:{row.identity}"
                    for row in current_rows
                ),
                "cutoff": cutoff.isoformat(),
                "duplicate_count": 0,
                "semantic_duplicate_count": 0,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, GURI_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta["errors"] = [f"{type(exc).__name__}: {exc}"]
        meta["error_kind"] = (
            "contract"
            if isinstance(exc, GuriExperienceContractError)
            else "network_or_parse"
        )
        return [], GURI_EXPERIENCE_PARSER, meta
    finally:
        requester.close()


collect = collect_guri_experience_courses


__all__ = [name for name in globals() if name.startswith("GURI_EXPERIENCE_")] + [
    "GuriExperienceContractError",
    "GuriExperienceSource",
    "collect",
    "collect_guri_experience_courses",
    "guri_experience_detail_url",
    "guri_experience_list_url",
    "is_guri_experience_target",
]
