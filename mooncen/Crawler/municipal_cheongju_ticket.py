"""Fail-closed collector for Cheongju City's integrated reservation catalogues.

The official search shell links to two independent public catalogues: the
education/lecture list and the tourism/experience list.  This collector owns
only those exact list and detail routes.  Notice boards, application forms,
applicant calendars, login, identity verification, and reservation write
endpoints are deliberately outside the request boundary.

Both lists expose a fixed eight rows per page.  A successful snapshot requires
all declared pages, an empty page immediately after the declared final page,
globally unique source identities, every current internal detail, and stable
first/final boundaries after detail traversal.  The two camping rows that the
official experience list delegates to the city's separate campground host are
kept as an exact, reviewed list-only allowlist and are never fetched here.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


CHEONGJU_TICKET_PROVIDER = "MUNI_TICKET_CHEONGJU_GO_KR_72C8D1D9"
CHEONGJU_TICKET_HOST = "ticket.cheongju.go.kr"
CHEONGJU_TICKET_ROOT = "https://ticket.cheongju.go.kr"
CHEONGJU_TICKET_SEARCH_URL = f"{CHEONGJU_TICKET_ROOT}/www/search.do"
CHEONGJU_TICKET_EDUCATION_URL = (
    f"{CHEONGJU_TICKET_ROOT}/www/selectEduLctreWebList.do?key=19"
)
CHEONGJU_TICKET_EXPERIENCE_URL = (
    f"{CHEONGJU_TICKET_ROOT}/www/selectExprnWebList.do?key=8"
)
CHEONGJU_TICKET_EDUCATION_CANDIDATE_ID = "MUNI_IR_6D409AE5503A"
CHEONGJU_TICKET_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_228F90626599"
CHEONGJU_TICKET_PAGE_SIZE = 8
CHEONGJU_TICKET_MAX_WORKERS = 4

CHEONGJU_TICKET_EDUCATION_PARSER = (
    "cheongju_ticket_education_all_declared_pages+empty_post_last+"
    "stable_boundaries+all_current_details+district_addresses+"
    "identity_bound_apply_no_fetch+notice_routes_excluded"
)
CHEONGJU_TICKET_EXPERIENCE_PARSER = (
    "cheongju_ticket_experience_all_declared_pages+empty_post_last+"
    "stable_boundaries+all_current_internal_details+two_external_camping_refs+"
    "district_addresses+identity_bound_apply_no_fetch+notice_routes_excluded"
)

CHEONGJU_TICKET_MUNICIPALITY_CODE = "4311000000"
CHEONGJU_TICKET_MUNICIPALITY_NAME = "충청북도 청주시"
CHEONGJU_TICKET_COVERED_MUNICIPALITIES: tuple[dict[str, str], ...] = (
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
CHEONGJU_TICKET_MUNICIPALITY_NAMES = {
    row["code"]: row["full_name"] for row in CHEONGJU_TICKET_COVERED_MUNICIPALITIES
}
CHEONGJU_TICKET_DISTRICT_CODES = {
    "상당구": "4311100000",
    "서원구": "4311200000",
    "흥덕구": "4311300000",
    "청원구": "4311400000",
}


class CheongjuTicketContractError(ValueError):
    """Raised when a public catalogue no longer matches the audited contract."""


@dataclass(frozen=True)
class CheongjuTicketCatalogue:
    kind: str
    name: str
    canonical_url: str
    list_path: str
    key: str
    detail_path: str
    identity_param: str
    period_field: str
    fee_field: str
    application_path: str
    parser: str
    candidate_id: str
    domain_category: str
    service_group: str
    program_type: str


CHEONGJU_TICKET_EDUCATION = CheongjuTicketCatalogue(
    kind="education",
    name="교육·강좌",
    canonical_url=CHEONGJU_TICKET_EDUCATION_URL,
    list_path="/www/selectEduLctreWebList.do",
    key="19",
    detail_path="/www/selectEduLctreWebView.do",
    identity_param="lctreNo",
    period_field="운영기간",
    fee_field="이용요금",
    application_path="/www/eduAplctAgreWebView.do",
    parser=CHEONGJU_TICKET_EDUCATION_PARSER,
    candidate_id=CHEONGJU_TICKET_EDUCATION_CANDIDATE_ID,
    domain_category="교육·강좌",
    service_group="공공강좌",
    program_type="강좌",
)
CHEONGJU_TICKET_EXPERIENCE = CheongjuTicketCatalogue(
    kind="experience",
    name="관광·체험",
    canonical_url=CHEONGJU_TICKET_EXPERIENCE_URL,
    list_path="/www/selectExprnWebList.do",
    key="8",
    detail_path="/www/selectExprnWebView.do",
    identity_param="exprnNo",
    period_field="체험기간",
    fee_field="체험요금",
    application_path="/www/exprnApplCalendarWebView.do",
    parser=CHEONGJU_TICKET_EXPERIENCE_PARSER,
    candidate_id=CHEONGJU_TICKET_EXPERIENCE_CANDIDATE_ID,
    domain_category="체험·견학",
    service_group="체험",
    program_type="체험",
)
CHEONGJU_TICKET_CATALOGUES = (
    CHEONGJU_TICKET_EDUCATION,
    CHEONGJU_TICKET_EXPERIENCE,
)


@dataclass(frozen=True)
class CheongjuTicketExternalReference:
    key: str
    url: str
    address: str
    municipality_code: str


CHEONGJU_TICKET_EXTERNAL_EXPERIENCES: Mapping[
    str, CheongjuTicketExternalReference
] = {
    "https://munam.cheongju.go.kr/index.jsp": CheongjuTicketExternalReference(
        key="munam-campground",
        url="https://munam.cheongju.go.kr/index.jsp",
        address="충청북도 청주시 흥덕구 원평동 76-1 문암생태공원캠핑장",
        municipality_code="4311300000",
    ),
    "https://munam.cheongju.go.kr/ochang/index.jsp": CheongjuTicketExternalReference(
        key="ochang-campground",
        url="https://munam.cheongju.go.kr/ochang/index.jsp",
        address="충청북도 청주시 청원구 오창읍 미래지로 68 오창미래지농촌테마공원캠핑장",
        municipality_code="4311400000",
    ),
}


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_COUNT_RE = re.compile(
    r"총\s*:\s*([\d,]+)\s*건\s*/\s*페이지\s*(\d+)\s*/\s*(\d+)\Z"
)
_DATE_RE = re.compile(r"20\d{2}[-.]\d{2}[-.]\d{2}")
_PHONE_VALUE_RE = re.compile(r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)")
_EMAIL_VALUE_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RRN_VALUE_RE = re.compile(r"(?<!\d)\d{6}[ -]?[1-4]\d{6}(?!\d)")
_STATUS_MAP = {
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "추가모집": "OPEN",
    "대기자접수": "OPEN",
    "접수마감": "CLOSED",
    "운영중": "CLOSED",
    "종료": "CLOSED",
    "폐강": "CLOSED",
}
_APPLICATION_STATUSES = frozenset({"접수예정", "접수중", "추가모집", "대기자접수"})
_TERMINAL_STATUSES = frozenset({"종료", "폐강"})
_LIST_LABELS = frozenset({"장소", "대상", "접수", "운영"})
_COMMON_DETAIL_FIELDS = frozenset(
    {"운영기관", "대상", "장소", "주소", "접수기간", "선별방법", "예약방법"}
)


@dataclass(frozen=True)
class _ListedReservation:
    catalogue: CheongjuTicketCatalogue
    identity: str
    source_id: Optional[int]
    external_key: str
    page: int
    position: int
    title: str
    source_status: str
    status: str
    institution: str
    fee_label: str
    venue: str
    target: str
    apply_start: date
    apply_end: date
    start_date: date
    end_date: date
    raw_url: str

    def current_on(self, cutoff: date) -> bool:
        return self.end_date >= cutoff and self.source_status not in _TERMINAL_STATUSES


@dataclass(frozen=True)
class _ListSnapshot:
    rows: tuple[_ListedReservation, ...]
    total: int
    total_pages: int
    requests: int
    boundaries: Mapping[int, str]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider")).upper()


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
            raise CheongjuTicketContractError("today is not an ISO date") from exc
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _exact_catalogue_url(value: Any, catalogue: CheongjuTicketCatalogue) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
    except ValueError:
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == CHEONGJU_TICKET_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == catalogue.list_path
        and not parsed.params
        and not parsed.fragment
        and query == {"key": [catalogue.key]}
    )


def cheongju_ticket_catalogue_for_target(
    target: Any,
) -> Optional[CheongjuTicketCatalogue]:
    if _provider(target) != CHEONGJU_TICKET_PROVIDER:
        return None
    return next(
        (
            catalogue
            for catalogue in CHEONGJU_TICKET_CATALOGUES
            if _exact_catalogue_url(_target_url(target), catalogue)
        ),
        None,
    )


def is_cheongju_ticket_target(target: Any) -> bool:
    return cheongju_ticket_catalogue_for_target(target) is not None


is_target = is_cheongju_ticket_target


def cheongju_ticket_list_url(catalogue: CheongjuTicketCatalogue, page: Any) -> str:
    raw_page = _clean(page)
    if catalogue not in CHEONGJU_TICKET_CATALOGUES or not raw_page.isdigit():
        return ""
    page_number = int(raw_page)
    if page_number < 1:
        return ""
    params: list[tuple[str, Any]] = [
        ("key", catalogue.key),
        ("pageUnit", CHEONGJU_TICKET_PAGE_SIZE),
        ("searchCnd", "all"),
    ]
    if catalogue.kind == "education":
        params.append(("searchOperYn", "Y"))
    params.extend((("viewMode", "card"), ("pageIndex", page_number)))
    return f"{CHEONGJU_TICKET_ROOT}{catalogue.list_path}?{urlencode(params)}"


def cheongju_ticket_detail_url(
    catalogue: CheongjuTicketCatalogue, source_id: Any
) -> str:
    raw_id = _clean(source_id)
    if (
        catalogue not in CHEONGJU_TICKET_CATALOGUES
        or not raw_id.isdigit()
        or int(raw_id) < 1
    ):
        return ""
    params = (("key", catalogue.key), (catalogue.identity_param, int(raw_id)), ("viewMode", "card"))
    return f"{CHEONGJU_TICKET_ROOT}{catalogue.detail_path}?{urlencode(params)}"


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


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    return current_session.get(
        url,
        headers={"Referer": CHEONGJU_TICKET_SEARCH_URL},
        timeout=timeout,
        allow_redirects=False,
    )


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise CheongjuTicketContractError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 0))
    if status != 200:
        raise CheongjuTicketContractError(f"unexpected HTTP status {status}")
    if getattr(value, "history", ()) or getattr(value, "headers", {}).get("Location"):
        raise CheongjuTicketContractError("redirect responses are not accepted")
    content_type = _clean(getattr(value, "headers", {}).get("Content-Type")).lower()
    if content_type and "html" not in content_type:
        raise CheongjuTicketContractError("response content type is not HTML")
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise CheongjuTicketContractError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _single_query(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name)
    if not isinstance(values, list) or len(values) != 1:
        raise CheongjuTicketContractError(f"detail query {name} is missing or repeated")
    return _clean(values[0])


def _iso_dates(value: Any, field: str) -> tuple[date, date]:
    values = _DATE_RE.findall(_clean(value))
    if len(values) != 2:
        raise CheongjuTicketContractError(f"{field} is not one complete date range")
    try:
        start, end = (date.fromisoformat(item.replace(".", "-")) for item in values)
    except ValueError as exc:
        raise CheongjuTicketContractError(f"{field} contains an invalid date") from exc
    if end < start:
        raise CheongjuTicketContractError(f"{field} is reversed")
    return start, end


def _normalized_phrase(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


def _safe_description(value: Any) -> str:
    text = _clean(value)
    text = _EMAIL_VALUE_RE.sub("[redacted-email]", text)
    text = _PHONE_VALUE_RE.sub("[redacted-phone]", text)
    return _RRN_VALUE_RE.sub("[redacted-id]", text)


def _page_declaration(wrapper: Any) -> tuple[int, int, int]:
    count = wrapper.select_one(".dataCount")
    if count is None:
        raise CheongjuTicketContractError("list page has no declared count")
    match = _COUNT_RE.fullmatch(_clean(count.get_text(" ", strip=True)))
    if match is None:
        raise CheongjuTicketContractError("list count declaration changed")
    total, page, total_pages = (int(value.replace(",", "")) for value in match.groups())
    if total < 1 or page < 1 or total_pages != math.ceil(total / CHEONGJU_TICKET_PAGE_SIZE):
        raise CheongjuTicketContractError("list total/page declaration is inconsistent")
    return total, page, total_pages


def _internal_identity(
    href: str,
    catalogue: CheongjuTicketCatalogue,
    page: int,
) -> Optional[int]:
    parsed = urlparse(href)
    try:
        port = parsed.port
    except ValueError as exc:
        raise CheongjuTicketContractError("detail link port is malformed") from exc
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == CHEONGJU_TICKET_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == catalogue.detail_path
        and not parsed.params
        and not parsed.fragment
    ):
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    allowed = {"key", catalogue.identity_param, "viewMode"}
    if catalogue.kind == "experience":
        allowed.update({"pageUnit", "pageIndex", "searchCnd"})
    if set(query) - allowed:
        raise CheongjuTicketContractError("detail link contains unaudited query fields")
    if _single_query(query, "key") != catalogue.key:
        raise CheongjuTicketContractError("detail link key changed")
    if _single_query(query, "viewMode") != "card":
        raise CheongjuTicketContractError("detail link viewMode changed")
    raw_identity = _single_query(query, catalogue.identity_param)
    if not raw_identity.isdigit() or int(raw_identity) < 1:
        raise CheongjuTicketContractError("detail identity is malformed")
    if catalogue.kind == "experience" and "pageIndex" in query:
        if (
            _single_query(query, "pageUnit") != str(CHEONGJU_TICKET_PAGE_SIZE)
            or _single_query(query, "pageIndex") != str(page)
            or _single_query(query, "searchCnd") != "all"
        ):
            raise CheongjuTicketContractError("experience detail context changed")
    return int(raw_identity)


def _parse_list_page(
    soup: BeautifulSoup,
    catalogue: CheongjuTicketCatalogue,
    requested_page: int,
) -> tuple[list[_ListedReservation], int, int]:
    wrappers = soup.select(".listWrap.thumbnail.show")
    if len(wrappers) != 1:
        raise CheongjuTicketContractError("expected one visible thumbnail list")
    wrapper = wrappers[0]
    total, declared_page, total_pages = _page_declaration(wrapper)
    if declared_page != requested_page:
        raise CheongjuTicketContractError("list returned a different page")
    outer = wrapper.find("ul", recursive=False)
    cards = outer.find_all("li", recursive=False) if outer is not None else []
    if requested_page > total_pages and cards:
        # The live service renders one inert ``li.noDataList`` on the page
        # immediately after the declared final page.  It is an empty-state
        # marker, not a reservation card.  Keep this exception deliberately
        # narrow so a notice/application/unknown route can never be mistaken
        # for a harmless sentinel.
        is_exact_empty_state = (
            len(cards) == 1
            and set(cards[0].get("class", [])) == {"noDataList"}
            and not cards[0].find("a", href=True)
            and not cards[0].select_one(".title, .option, .prgInformation")
        )
        if is_exact_empty_state:
            cards = []
    if requested_page <= total_pages:
        expected = (
            CHEONGJU_TICKET_PAGE_SIZE
            if requested_page < total_pages
            else total - CHEONGJU_TICKET_PAGE_SIZE * (total_pages - 1)
        )
    else:
        expected = 0
    if len(cards) != expected:
        raise CheongjuTicketContractError(
            f"page {requested_page} exposes {len(cards)} rows, expected {expected}"
        )

    rows: list[_ListedReservation] = []
    for position, card in enumerate(cards, start=1):
        anchors = card.find_all("a", href=True, recursive=False)
        if len(anchors) != 1:
            raise CheongjuTicketContractError("list card has no single direct detail link")
        anchor = anchors[0]
        href = urljoin(f"{CHEONGJU_TICKET_ROOT}/www/", _clean(anchor.get("href")))
        source_id = _internal_identity(href, catalogue, requested_page)
        external_key = ""
        if source_id is None:
            external = CHEONGJU_TICKET_EXTERNAL_EXPERIENCES.get(href)
            if catalogue.kind != "experience" or external is None:
                raise CheongjuTicketContractError(
                    f"catalogue row points to an unaudited route: {href}"
                )
            external_key = external.key
            identity = f"external:{external.key}"
            raw_url = external.url
        else:
            identity = f"{catalogue.kind}:{source_id}"
            raw_url = cheongju_ticket_detail_url(catalogue, source_id)

        title_node = anchor.select_one(".title")
        status_node = anchor.select_one(".option .stateType")
        institution_node = anchor.select_one(".option .organ")
        fee_node = anchor.select_one(".option .pay")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        source_status = _clean(
            status_node.get_text(" ", strip=True) if status_node else ""
        )
        institution = _clean(
            institution_node.get_text(" ", strip=True) if institution_node else ""
        )
        fee_label = _clean(fee_node.get_text(" ", strip=True) if fee_node else "")
        if not title or not institution or source_status not in _STATUS_MAP:
            raise CheongjuTicketContractError("list card identity/status text changed")
        if fee_label not in {"무료", "유료"}:
            raise CheongjuTicketContractError("list card fee class changed")

        fields: dict[str, str] = {}
        for item in anchor.select(".prgInformation > li"):
            label_node = item.find("span")
            label = _clean(label_node.get_text(" ", strip=True) if label_node else "")
            if not label or label in fields:
                raise CheongjuTicketContractError("list information label is missing/repeated")
            if label_node is not None:
                label_node.extract()
            fields[label] = _clean(item.get_text(" ", strip=True))
        if set(fields) != _LIST_LABELS or not all(fields.values()):
            raise CheongjuTicketContractError("list information fields changed")
        apply_start, apply_end = _iso_dates(fields["접수"], "list reception period")
        start_date, end_date = _iso_dates(fields["운영"], "list operation period")
        rows.append(
            _ListedReservation(
                catalogue=catalogue,
                identity=identity,
                source_id=source_id,
                external_key=external_key,
                page=requested_page,
                position=position,
                title=title,
                source_status=source_status,
                status=_STATUS_MAP[source_status],
                institution=institution,
                fee_label=fee_label,
                venue=fields["장소"],
                target=fields["대상"],
                apply_start=apply_start,
                apply_end=apply_end,
                start_date=start_date,
                end_date=end_date,
                raw_url=raw_url,
            )
        )
    return rows, total, total_pages


def _list_fingerprint(rows: Iterable[_ListedReservation]) -> str:
    text = "\n".join(
        "|".join(
            (
                row.identity,
                row.title,
                row.source_status,
                row.institution,
                row.start_date.isoformat(),
                row.end_date.isoformat(),
                row.apply_start.isoformat(),
                row.apply_end.isoformat(),
            )
        )
        for row in rows
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Requester:
    def __init__(self, session_factory: SessionFactory, fetcher: Fetcher, timeout: int):
        self.session = session_factory()
        self.fetcher = fetcher
        self.timeout = timeout
        self.calls = 0

    def get(self, url: str) -> BeautifulSoup:
        self.calls += 1
        return _coerce_soup(self.fetcher(self.session, url, self.timeout))

    def close(self) -> None:
        _close_quietly(self.session)


def _collect_list_snapshot(
    requester: _Requester,
    catalogue: CheongjuTicketCatalogue,
    max_pages: int,
) -> _ListSnapshot:
    first_rows, total, total_pages = _parse_list_page(
        requester.get(cheongju_ticket_list_url(catalogue, 1)), catalogue, 1
    )
    required_requests = total_pages + 1
    if required_requests > max_pages:
        raise CheongjuTicketContractError(
            f"catalogue needs {required_requests} pages including the empty sentinel, "
            f"above max_pages={max_pages}"
        )
    page_rows: dict[int, list[_ListedReservation]] = {1: first_rows}
    all_rows = list(first_rows)
    for page in range(2, total_pages + 2):
        rows, repeated_total, repeated_pages = _parse_list_page(
            requester.get(cheongju_ticket_list_url(catalogue, page)),
            catalogue,
            page,
        )
        if repeated_total != total or repeated_pages != total_pages:
            raise CheongjuTicketContractError("list declarations changed during pagination")
        page_rows[page] = rows
        if page <= total_pages:
            all_rows.extend(rows)
    if page_rows[total_pages + 1]:
        raise CheongjuTicketContractError("page after declared final page is not empty")
    identities = [row.identity for row in all_rows]
    if len(all_rows) != total or len(set(identities)) != total:
        raise CheongjuTicketContractError("complete list count/identity contract failed")
    boundary_pages = {1, total_pages, total_pages + 1}
    return _ListSnapshot(
        rows=tuple(all_rows),
        total=total,
        total_pages=total_pages,
        requests=required_requests,
        boundaries={page: _list_fingerprint(page_rows[page]) for page in boundary_pages},
    )


def _recheck_boundaries(
    requester: _Requester,
    catalogue: CheongjuTicketCatalogue,
    snapshot: _ListSnapshot,
) -> int:
    calls_before = requester.calls
    for page, fingerprint in snapshot.boundaries.items():
        rows, total, total_pages = _parse_list_page(
            requester.get(cheongju_ticket_list_url(catalogue, page)), catalogue, page
        )
        if total != snapshot.total or total_pages != snapshot.total_pages:
            raise CheongjuTicketContractError("list declarations changed during detail traversal")
        if _list_fingerprint(rows) != fingerprint:
            raise CheongjuTicketContractError(
                f"list boundary page {page} changed during detail traversal"
            )
    return requester.calls - calls_before


def _detail_fields(main: Any) -> dict[str, str]:
    tables = main.select("h4.noLine + .itemWrap table")
    if len(tables) != 1:
        raise CheongjuTicketContractError("detail has no single information table")
    fields: dict[str, str] = {}
    for row in tables[0].select("tbody tr"):
        heading = row.find("th")
        value = row.find("td")
        if heading is None or value is None:
            raise CheongjuTicketContractError("detail table row is malformed")
        key = _clean(heading.get_text(" ", strip=True))
        if not key or key in fields:
            raise CheongjuTicketContractError("detail table label is missing/repeated")
        fields[key] = _clean(value.get_text(" ", strip=True))
    return fields


def _municipality_from_address(address: str) -> tuple[str, str]:
    matches = [code for district, code in CHEONGJU_TICKET_DISTRICT_CODES.items() if district in address]
    if len(matches) != 1:
        raise CheongjuTicketContractError("detail address has no single Cheongju district")
    code = matches[0]
    return code, CHEONGJU_TICKET_MUNICIPALITY_NAMES[code]


def _application_control(
    main: Any,
    record: _ListedReservation,
) -> tuple[bool, int]:
    catalogue = record.catalogue
    links: set[str] = set()
    notice_links = 0
    for anchor in main.find_all("a", href=True):
        href = urljoin(f"{CHEONGJU_TICKET_ROOT}/www/", _clean(anchor.get("href")))
        parsed = urlparse(href)
        if "selectBbs" in parsed.path:
            notice_links += 1
        if _clean(anchor.get_text(" ", strip=True)) != "신청하기":
            continue
        query = parse_qs(parsed.query, keep_blank_values=True)
        if not (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == CHEONGJU_TICKET_HOST
            and parsed.path == catalogue.application_path
            and not parsed.params
            and not parsed.fragment
            and _single_query(query, "key") == catalogue.key
            and _single_query(query, catalogue.identity_param) == str(record.source_id)
        ):
            raise CheongjuTicketContractError("application control is not bound to detail identity")
        links.add(href)
    expected = record.source_status in _APPLICATION_STATUSES
    if expected != (len(links) == 1):
        raise CheongjuTicketContractError("application control/status contract changed")
    return expected, notice_links


def _detail_description(main: Any, record: _ListedReservation) -> tuple[str, str]:
    heading = next(
        (node for node in main.find_all("h4") if _clean(node.get_text(" ", strip=True)) == "상세내용"),
        None,
    )
    if heading is None or heading.find_next_sibling() is None:
        raise CheongjuTicketContractError("detail description section is missing")
    description = _safe_description(
        heading.find_next_sibling().get_text(" ", strip=True)
    )
    if description:
        return description, "official_detail"
    return (
        _clean(
            f"{record.title} | {record.institution} | {record.venue} | "
            f"{record.start_date.isoformat()} ~ {record.end_date.isoformat()}"
        ),
        "generated_from_validated_detail",
    )


def _capacity(fields: Mapping[str, str], catalogue: CheongjuTicketCatalogue) -> tuple[Optional[int], Optional[int]]:
    field = "모집인원" if catalogue.kind == "education" else "모집수"
    value = _clean(fields.get(field))
    values = [int(item.replace(",", "")) for item in re.findall(r"\d[\d,]*", value)]
    if not values:
        return None, None
    if catalogue.kind == "education":
        total = values[0]
        current = values[1] if len(values) > 1 else None
        return current, total
    return None, values[0]


def _branch_code(branch: str, municipality_code: str) -> str:
    identity = f"{_clean(municipality_code)}|{_clean(branch)}"
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12].upper()
    # MunicipalDbWriter stores branch_code in 50 characters. Keep the district
    # and the complete digest inside that boundary so same-named operators in
    # different Cheongju districts cannot share one branch region.
    return f"CHEONGJU_{_clean(municipality_code)}_{digest}"[:50]


def _row_from_detail(
    target: Any,
    record: _ListedReservation,
    soup: BeautifulSoup,
) -> tuple[dict[str, Any], int]:
    main = soup.select_one("#contents")
    if main is None:
        raise CheongjuTicketContractError("detail main content is missing")
    title_nodes = main.select(".viewProgram.simpleInformation .title strong")
    status_nodes = main.select(".viewProgram.simpleInformation .stateType")
    if len(title_nodes) != 1 or not status_nodes:
        raise CheongjuTicketContractError("detail title/status structure changed")
    if _clean(title_nodes[0].get_text(" ", strip=True)) != record.title:
        raise CheongjuTicketContractError("detail/list title mismatch")
    if _clean(status_nodes[0].get_text(" ", strip=True)) != record.source_status:
        raise CheongjuTicketContractError("detail/list status mismatch")
    if main.find("form") is not None:
        raise CheongjuTicketContractError("detail unexpectedly embeds a form")

    fields = _detail_fields(main)
    catalogue = record.catalogue
    required = set(_COMMON_DETAIL_FIELDS)
    required.update({catalogue.period_field, catalogue.fee_field})
    if catalogue.kind == "education":
        required.update({"강좌명", "모집인원", "운영요일", "운영시간"})
    else:
        required.add("모집수")
    missing = sorted(required.difference(fields))
    if missing:
        raise CheongjuTicketContractError(f"detail is missing required fields {missing!r}")
    if catalogue.kind == "education" and fields["강좌명"] != record.title:
        raise CheongjuTicketContractError("education table title mismatch")
    for key, listed in (
        ("운영기관", record.institution),
        ("장소", record.venue),
        ("대상", record.target),
    ):
        if _normalized_phrase(fields[key]) != _normalized_phrase(listed):
            raise CheongjuTicketContractError(f"detail/list {key} mismatch")
    start_date, end_date = _iso_dates(fields[catalogue.period_field], "detail operation period")
    apply_start, apply_end = _iso_dates(fields["접수기간"], "detail reception period")
    if (start_date, end_date) != (record.start_date, record.end_date):
        raise CheongjuTicketContractError("detail/list operation period mismatch")
    if (apply_start, apply_end) != (record.apply_start, record.apply_end):
        raise CheongjuTicketContractError("detail/list reception period mismatch")
    detail_fee = fields[catalogue.fee_field]
    if (record.fee_label == "무료") != (detail_fee == "무료"):
        raise CheongjuTicketContractError("detail/list fee class mismatch")

    address = fields["주소"]
    if not address:
        raise CheongjuTicketContractError("detail address is empty")
    municipality_code, municipality_name = _municipality_from_address(address)
    has_application, notice_links = _application_control(main, record)
    description, description_source = _detail_description(main, record)
    current_capacity, total_capacity = _capacity(fields, catalogue)
    branch = fields["운영기관"]
    schedule_raw = _clean(
        " ".join(
            value
            for value in (fields.get("운영요일"), fields.get("운영시간"))
            if value
        )
    )
    if not schedule_raw:
        schedule_raw = f"{start_date.isoformat()} ~ {end_date.isoformat()}"
    raw_url = cheongju_ticket_detail_url(catalogue, record.source_id)
    provider = _provider(target)
    raw_fields = {
        "parser": catalogue.parser,
        "candidate_id": catalogue.candidate_id,
        "source_identity": record.identity,
        "source_id": record.source_id,
        "source_status": record.source_status,
        "source_page": record.page,
        "source_position": record.position,
        "source_fee_class": record.fee_label,
        "application_control_present": has_application,
        "application_endpoint_fetched": False,
        "notice_links_excluded": notice_links,
        "description_source": description_source,
        "full_detail_contract": True,
    }
    return (
        {
            "provider": provider,
            "provider_course_id": f"{provider}:ticket:{record.identity}"[:100],
            "prefer_incoming_provider_course_id": True,
            "title": record.title,
            "branch": branch,
            "branch_code": _branch_code(branch, municipality_code),
            "preserve_branch": True,
            "branch_url": catalogue.canonical_url,
            "category": catalogue.domain_category,
            "program_type": catalogue.program_type,
            "raw_url": raw_url,
            "application_url": raw_url if has_application else "",
            "application_type": "ONLINE_RESERVATION" if has_application else "INFO_ONLY",
            "application_method_raw": fields.get("예약방법", ""),
            "reservation_available": has_application,
            "status": record.status,
            "fee": detail_fee,
            "period": f"{start_date.isoformat()} ~ {end_date.isoformat()}",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "apply_period": fields["접수기간"],
            "apply_start": apply_start.isoformat(),
            "apply_end": apply_end.isoformat(),
            "schedule_raw": schedule_raw,
            "target": fields["대상"],
            "capacity": (
                f"{current_capacity}/{total_capacity}"
                if current_capacity is not None and total_capacity is not None
                else f"정원 {total_capacity}명"
                if total_capacity is not None
                else ""
            ),
            "capacity_current": current_capacity,
            "capacity_total": total_capacity,
            "venue_name": fields["장소"],
            "venue_address": address,
            "address": address,
            "description": description,
            "collection_category": "공공예약",
            "domain_category": catalogue.domain_category,
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": catalogue.service_group,
            "service_group_policy": "locked",
            "collection_type": catalogue.parser,
            "municipality_code": municipality_code,
            "municipality_full_name": municipality_name,
            "raw_fields": raw_fields,
        },
        notice_links,
    )


def _external_row(target: Any, record: _ListedReservation) -> dict[str, Any]:
    external = next(
        (
            value
            for value in CHEONGJU_TICKET_EXTERNAL_EXPERIENCES.values()
            if value.key == record.external_key
        ),
        None,
    )
    if external is None:
        raise CheongjuTicketContractError("external row is not in the exact allowlist")
    municipality_name = CHEONGJU_TICKET_MUNICIPALITY_NAMES[external.municipality_code]
    provider = _provider(target)
    has_application = record.source_status in _APPLICATION_STATUSES
    branch = record.title
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:ticket:{record.identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": record.title,
        "branch": branch,
        "branch_code": _branch_code(branch, external.municipality_code),
        "preserve_branch": True,
        "branch_url": external.url,
        "category": record.catalogue.domain_category,
        "program_type": record.catalogue.program_type,
        "raw_url": external.url,
        "application_url": external.url if has_application else "",
        "application_type": "EXTERNAL_RESERVATION" if has_application else "INFO_ONLY",
        "reservation_available": has_application,
        "status": record.status,
        "fee": record.fee_label,
        "period": f"{record.start_date.isoformat()} ~ {record.end_date.isoformat()}",
        "start_date": record.start_date.isoformat(),
        "end_date": record.end_date.isoformat(),
        "apply_period": f"{record.apply_start.isoformat()} ~ {record.apply_end.isoformat()}",
        "apply_start": record.apply_start.isoformat(),
        "apply_end": record.apply_end.isoformat(),
        "schedule_raw": f"{record.start_date.isoformat()} ~ {record.end_date.isoformat()}",
        "target": record.target,
        "venue_name": record.venue,
        "venue_address": external.address,
        "address": external.address,
        "description": (
            f"청주시 통합예약 관광·체험 전체 목록의 공식 외부 예약 항목 | "
            f"{record.title} | {record.venue}"
        ),
        "collection_category": "공공예약",
        "domain_category": record.catalogue.domain_category,
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": record.catalogue.service_group,
        "service_group_policy": "locked",
        "collection_type": record.catalogue.parser,
        "municipality_code": external.municipality_code,
        "municipality_full_name": municipality_name,
        "raw_fields": {
            "parser": record.catalogue.parser,
            "candidate_id": record.catalogue.candidate_id,
            "source_identity": record.identity,
            "source_status": record.source_status,
            "source_page": record.page,
            "source_position": record.position,
            "external_reference": True,
            "external_reference_key": external.key,
            "external_endpoint_fetched": False,
            "application_endpoint_fetched": False,
            "full_detail_contract": False,
            "detail_contract": "official_list_row_plus_fixed_official_facility_metadata",
        },
    }


def _detail_bundle(
    target: Any,
    record: _ListedReservation,
    *,
    session_factory: SessionFactory,
    fetcher: Fetcher,
    timeout: int,
) -> tuple[Optional[dict[str, Any]], int, int, str]:
    session = session_factory()
    try:
        url = cheongju_ticket_detail_url(record.catalogue, record.source_id)
        soup = _coerce_soup(fetcher(session, url, timeout))
        row, notice_links = _row_from_detail(target, record, soup)
        return row, 1, notice_links, ""
    except Exception as exc:
        return (
            None,
            1,
            0,
            f"{record.identity}: {type(exc).__name__}: {_clean(exc)}",
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


def _failure(
    catalogue: Optional[CheongjuTicketCatalogue], message: str, **extra: Any
) -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "list_requests": 0,
        "list_recheck_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_total": 0,
        "unique_id_count": 0,
        "expired_count": 0,
        "cancelled_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "canonical_provider": CHEONGJU_TICKET_PROVIDER,
        "canonical_url": catalogue.canonical_url if catalogue else "",
        "catalogue_kind": catalogue.kind if catalogue else "",
        "covered_municipalities": [
            dict(row) for row in CHEONGJU_TICKET_COVERED_MUNICIPALITIES
        ],
        "notice_board_requests": 0,
        "application_endpoint_requests": 0,
        "authentication_endpoint_requests": 0,
        "configured_collection_error": message,
        **extra,
    }


def collect_cheongju_ticket_reservations(
    target: Any,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 700,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = CHEONGJU_TICKET_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one exact Cheongju integrated-reservation catalogue."""

    catalogue = cheongju_ticket_catalogue_for_target(target)
    parser = catalogue.parser if catalogue else CHEONGJU_TICKET_EDUCATION_PARSER
    if catalogue is None:
        return [], parser, _failure(
            None, "target does not match an exact reviewed Cheongju ticket catalogue"
        )
    try:
        page_cap = int(max_pages)
        detail_cap = int(detail_limit)
        workers = max(1, min(CHEONGJU_TICKET_MAX_WORKERS, int(max_workers)))
        cutoff = _today(today)
    except (TypeError, ValueError, CheongjuTicketContractError) as exc:
        return [], parser, _failure(
            catalogue,
            f"invalid collection arguments: {type(exc).__name__}: {_clean(exc)}",
        )
    if page_cap < 1 or detail_cap < 0:
        return [], parser, _failure(
            catalogue, "collection caps are invalid", source_cap_reached=True
        )

    current_session_factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    current_dedupe = dedupe_rows or _dedupe_default
    requester = _Requester(current_session_factory, current_fetcher, timeout)
    snapshot: Optional[_ListSnapshot] = None
    current_records: list[_ListedReservation] = []
    list_recheck_requests = 0
    detail_attempts = 0
    detail_pages = 0
    detail_requests = 0
    notice_links_excluded = 0
    errors: list[str] = []
    source_cap_reached = False
    rows: list[dict[str, Any]] = []
    try:
        try:
            snapshot = _collect_list_snapshot(requester, catalogue, page_cap)
        except Exception as exc:
            errors.append(f"initial list snapshot: {type(exc).__name__}: {_clean(exc)}")
        if snapshot is None:
            return [], parser, _failure(
                catalogue,
                "; ".join(errors),
                request_count=requester.calls,
                list_requests=requester.calls,
                source_cap_reached="max_pages" in errors[0],
            )

        identities = [record.identity for record in snapshot.rows]
        current_records = [record for record in snapshot.rows if record.current_on(cutoff)]
        expired_count = sum(record.end_date < cutoff for record in snapshot.rows)
        cancelled_count = sum(
            record.source_status in _TERMINAL_STATUSES and record.end_date >= cutoff
            for record in snapshot.rows
        )
        internal_current = [record for record in current_records if record.source_id is not None]
        external_current = [record for record in current_records if record.source_id is None]
        if len(internal_current) > detail_cap:
            source_cap_reached = True
            errors.append(
                f"detail_limit={detail_cap} is below required internal current rows={len(internal_current)}"
            )

        indexed: dict[str, dict[str, Any]] = {}
        if not errors and internal_current:
            detail_attempts = len(internal_current)
            with ThreadPoolExecutor(
                max_workers=min(workers, len(internal_current)),
                thread_name_prefix="cheongju-ticket-detail",
            ) as pool:
                futures = {
                    pool.submit(
                        _detail_bundle,
                        target,
                        record,
                        session_factory=current_session_factory,
                        fetcher=current_fetcher,
                        timeout=timeout,
                    ): record
                    for record in internal_current
                }
                for future in as_completed(futures):
                    record = futures[future]
                    try:
                        row, calls, notice_count, error = future.result()
                    except Exception as exc:
                        row, calls, notice_count, error = (
                            None,
                            0,
                            0,
                            f"{record.identity}: {type(exc).__name__}: {_clean(exc)}",
                        )
                    detail_requests += calls
                    notice_links_excluded += notice_count
                    if error:
                        errors.append(error)
                    elif row is not None:
                        indexed[record.identity] = row
                        detail_pages += 1
        if not errors:
            for record in external_current:
                try:
                    indexed[record.identity] = _external_row(target, record)
                except Exception as exc:
                    errors.append(
                        f"{record.identity}: {type(exc).__name__}: {_clean(exc)}"
                    )
            rows = [indexed[record.identity] for record in current_records if record.identity in indexed]

        try:
            list_recheck_requests = _recheck_boundaries(
                requester, catalogue, snapshot
            )
        except Exception as exc:
            errors.append(f"list boundary recheck: {type(exc).__name__}: {_clean(exc)}")

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

        details_complete = (
            not source_cap_reached
            and detail_attempts == len(internal_current)
            and detail_pages == len(internal_current)
        )
        pagination_complete = not any(
            error.startswith("initial list") or error.startswith("list boundary")
            for error in errors
        )
        snapshot_complete = (
            not errors
            and pagination_complete
            and details_complete
            and len(rows) == len(current_records)
            and len(set(identities)) == snapshot.total
        )
        if not snapshot_complete:
            rows = []

        municipality_counts = Counter(row.get("municipality_code") for row in rows)
        branch_counts = Counter(row.get("branch") for row in rows)
        status_counts = Counter(record.source_status for record in current_records)
        meta: dict[str, Any] = {
            "pages": snapshot.total_pages,
            "request_count": requester.calls + detail_requests,
            "list_requests": snapshot.requests,
            "list_recheck_requests": list_recheck_requests,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "source_total": snapshot.total,
            "internal_source_total": sum(record.source_id is not None for record in snapshot.rows),
            "external_source_total": sum(record.source_id is None for record in snapshot.rows),
            "unique_id_count": len(set(identities)),
            "expired_count": expired_count,
            "cancelled_count": cancelled_count,
            "current_count": len(current_records),
            "current_internal_count": len(internal_current),
            "current_external_count": len(external_current),
            "returned_count": len(rows),
            "declared_total": snapshot.total,
            "declared_total_pages": snapshot.total_pages,
            "post_last_empty": pagination_complete,
            "status_source_counts": dict(status_counts),
            "branch_counts": dict(branch_counts),
            "current_municipality_counts": dict(municipality_counts),
            "pagination_detected": snapshot.total_pages > 1,
            "pagination_complete": pagination_complete,
            "pagination_exhausted": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "network_concurrency": workers,
            "no_current_data": snapshot_complete and not current_records,
            "no_current_reason": (
                f"the complete {catalogue.name} catalogue has no current/future rows"
                if snapshot_complete and not current_records
                else ""
            ),
            "canonical_provider": CHEONGJU_TICKET_PROVIDER,
            "canonical_url": catalogue.canonical_url,
            "catalogue_kind": catalogue.kind,
            "ownership_scope": (
                f"cheongju_ticket_{catalogue.kind}_complete_public_catalogue_current_future"
            ),
            "official_entry_url": CHEONGJU_TICKET_SEARCH_URL,
            "covered_municipalities": [
                dict(row) for row in CHEONGJU_TICKET_COVERED_MUNICIPALITIES
            ],
            "existing_lifelong_provider": "MUNI_LLL_CHEONGJU_GO_KR_DA1AAEA1",
            "cross_source_identity_namespace": True,
            "validated_exact_title_overlap_at_2026_08_05": 0,
            "notice_links_excluded": notice_links_excluded,
            "notice_board_requests": 0,
            "application_endpoint_requests": 0,
            "authentication_endpoint_requests": 0,
            "applicant_payload_persisted": False,
            "external_reference_urls": [
                record.raw_url for record in external_current
            ],
        }
        if errors:
            meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return rows, parser, meta
    finally:
        requester.close()


collect = collect_cheongju_ticket_reservations
collect_cheongju_ticket = collect_cheongju_ticket_reservations


__all__ = [
    "CHEONGJU_TICKET_CATALOGUES",
    "CHEONGJU_TICKET_COVERED_MUNICIPALITIES",
    "CHEONGJU_TICKET_EDUCATION",
    "CHEONGJU_TICKET_EDUCATION_CANDIDATE_ID",
    "CHEONGJU_TICKET_EDUCATION_PARSER",
    "CHEONGJU_TICKET_EDUCATION_URL",
    "CHEONGJU_TICKET_EXPERIENCE",
    "CHEONGJU_TICKET_EXPERIENCE_CANDIDATE_ID",
    "CHEONGJU_TICKET_EXPERIENCE_PARSER",
    "CHEONGJU_TICKET_EXPERIENCE_URL",
    "CHEONGJU_TICKET_EXTERNAL_EXPERIENCES",
    "CHEONGJU_TICKET_HOST",
    "CHEONGJU_TICKET_MAX_WORKERS",
    "CHEONGJU_TICKET_PAGE_SIZE",
    "CHEONGJU_TICKET_PROVIDER",
    "CHEONGJU_TICKET_ROOT",
    "CHEONGJU_TICKET_SEARCH_URL",
    "CheongjuTicketCatalogue",
    "CheongjuTicketContractError",
    "CheongjuTicketExternalReference",
    "cheongju_ticket_catalogue_for_target",
    "cheongju_ticket_detail_url",
    "cheongju_ticket_list_url",
    "collect",
    "collect_cheongju_ticket",
    "collect_cheongju_ticket_reservations",
    "is_cheongju_ticket_target",
    "is_target",
]
