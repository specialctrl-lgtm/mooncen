"""Fail-closed collector for Gyeonggi Share's experience/visit ledger.

The first-party Gyeonggi Share service publishes a canonical, reservation-
available ``체험/견학`` list and identity-bound public information details.
Only those list/detail GET routes are requested.  NetFunnel submission,
``/lecture/apply``, login/auth/member/applicant/PII, attachments, images and
downloads are deliberately outside the request allowlist.

Municipal attribution comes only from the address passed to the official
``f_mapPop`` control inside ``.conHeader .dataBody``.  The list's displayed
area and municipality-like words in a title are retained solely as audit
signals.  Unknown addresses and changed list/detail identities fail the whole
snapshot closed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag
import requests


GYEONGGI_SHARE_EXPERIENCE_PROVIDER = "MUNI_SHARE_GG_GO_KR_8F92DD55"
GYEONGGI_SHARE_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_1D78897D7A59"
GYEONGGI_SHARE_EXPERIENCE_HOST = "share.gg.go.kr"
GYEONGGI_SHARE_EXPERIENCE_LIST_PATH = "/experience/list"
GYEONGGI_SHARE_EXPERIENCE_DETAIL_PATH = "/lecture/view"
GYEONGGI_SHARE_EXPERIENCE_URL = (
    "https://share.gg.go.kr/experience/list?eshare=1&c1=32034&c3=20"
)
GYEONGGI_SHARE_EXPERIENCE_PAGE_SIZE = 12
GYEONGGI_SHARE_EXPERIENCE_MAX_BYTES = 3_000_000
GYEONGGI_SHARE_EXPERIENCE_OWNERSHIP_SCOPE = (
    "gyeonggi_share_canonical_reservation_available_experience_visit_ledger_"
    "with_public_detail_map_addresses"
)
GYEONGGI_SHARE_EXPERIENCE_PARSER = (
    "gyeonggi_share_canonical_experience_visit_ledger+declared_total_and_pages+"
    "exact_empty_post_last_sentinel+stable_first_last_sentinel_rechecks+"
    "all_source_public_details+conheader_databody_exact_fields_only+"
    "official_f_mapPop_address_municipality_allowlist+list_and_title_region_"
    "anomaly_audit+operation_currentness+locked_experience+provider_prefixed_id+"
    "application_url_suppressed+list_detail_get_only+no_netfunnel_apply_login_"
    "auth_identity_applicant_member_pii_attachment_image_or_download_calls"
)

GYEONGGI_SHARE_EXPERIENCE_LIVE_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_total": 24,
    "data_pages": 2,
    "sentinel_page": 3,
    "current_count": 24,
    "detail_pages": 24,
    "source_status_counts": {"신청가능": 18, "대기접수": 2, "접수마감": 4},
    "municipality_counts": {
        "4161000000": 12,
        "4137000000": 2,
        "4183000000": 6,
        "4155000000": 1,
        "4148000000": 1,
        "4157000000": 1,
        "4182000000": 1,
    },
    "title_address_region_anomalies": {
        "58338": {"title_region": "안성시", "venue_region": "양평군"}
    },
}


@dataclass(frozen=True)
class _Municipality:
    code: str
    sigungu: str

    @property
    def full_name(self) -> str:
        return f"경기도 {self.sigungu}"


# The 31 first-level Gyeonggi city/county names are the complete address
# allowlist.  District names are not inferred from titles or list labels.
GYEONGGI_SHARE_EXPERIENCE_MUNICIPALITIES: Mapping[str, _Municipality] = {
    "수원시": _Municipality("4111000000", "수원시"),
    "성남시": _Municipality("4113000000", "성남시"),
    "의정부시": _Municipality("4115000000", "의정부시"),
    "안양시": _Municipality("4117000000", "안양시"),
    "부천시": _Municipality("4119000000", "부천시"),
    "광명시": _Municipality("4121000000", "광명시"),
    "평택시": _Municipality("4122000000", "평택시"),
    "동두천시": _Municipality("4125000000", "동두천시"),
    "안산시": _Municipality("4127000000", "안산시"),
    "고양시": _Municipality("4128000000", "고양시"),
    "과천시": _Municipality("4129000000", "과천시"),
    "구리시": _Municipality("4131000000", "구리시"),
    "남양주시": _Municipality("4136000000", "남양주시"),
    "오산시": _Municipality("4137000000", "오산시"),
    "시흥시": _Municipality("4139000000", "시흥시"),
    "군포시": _Municipality("4141000000", "군포시"),
    "의왕시": _Municipality("4143000000", "의왕시"),
    "하남시": _Municipality("4145000000", "하남시"),
    "용인시": _Municipality("4146000000", "용인시"),
    "파주시": _Municipality("4148000000", "파주시"),
    "이천시": _Municipality("4150000000", "이천시"),
    "안성시": _Municipality("4155000000", "안성시"),
    "김포시": _Municipality("4157000000", "김포시"),
    "화성시": _Municipality("4159000000", "화성시"),
    "광주시": _Municipality("4161000000", "광주시"),
    "양주시": _Municipality("4163000000", "양주시"),
    "포천시": _Municipality("4165000000", "포천시"),
    "여주시": _Municipality("4167000000", "여주시"),
    "연천군": _Municipality("4180000000", "연천군"),
    "가평군": _Municipality("4182000000", "가평군"),
    "양평군": _Municipality("4183000000", "양평군"),
}

GYEONGGI_SHARE_EXPERIENCE_COVERED_MUNICIPALITIES = (
    {"code": "4137000000", "sido": "경기도", "sigungu": "오산시", "full_name": "경기도 오산시"},
    {"code": "4148000000", "sido": "경기도", "sigungu": "파주시", "full_name": "경기도 파주시"},
    {"code": "4155000000", "sido": "경기도", "sigungu": "안성시", "full_name": "경기도 안성시"},
    {"code": "4157000000", "sido": "경기도", "sigungu": "김포시", "full_name": "경기도 김포시"},
    {"code": "4161000000", "sido": "경기도", "sigungu": "광주시", "full_name": "경기도 광주시"},
    {"code": "4182000000", "sido": "경기도", "sigungu": "가평군", "full_name": "경기도 가평군"},
    {"code": "4183000000", "sido": "경기도", "sigungu": "양평군", "full_name": "경기도 양평군"},
)


class GyeonggiShareExperienceContractError(RuntimeError):
    """Raised when the audited public experience contract changes."""


@dataclass(frozen=True)
class _ListRow:
    identity: str
    title: str
    list_area: str
    institution: str
    subcategory: str
    booking_method: str
    selection_method: str
    list_fee: str
    wait_notice: str
    page: int
    position: int


@dataclass(frozen=True)
class _ListPage:
    page: int
    total: int
    last_page: int
    rows: tuple[_ListRow, ...]
    exact_empty: bool


@dataclass(frozen=True)
class _Detail:
    title: str
    detail_area: str
    institution: str
    subcategory: str
    source_status: str
    status: str
    target: str
    apply_start: datetime
    apply_end: datetime
    event_start: date
    event_end: date
    weekday: str
    schedule: str
    fee: str
    venue_address: str
    venue_name: str
    municipality: _Municipality
    application_controls: int
    related_dl_ignored: int


SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d{0,11}")
_TOTAL_RE = re.compile(r"[1-9]\d*|0")
_ONCLICK_RE = re.compile(
    r"(?:javascript:)?goNetFunnelSubmit2\(\s*'eduView'\s*,\s*"
    r"'/lecture/view'\s*,\s*'(?P<identity>[1-9]\d{0,11})'\s*,\s*"
    r"'32034'\s*,\s*'20'\s*\)\s*;?"
)
_APPLY_PERIOD_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})"
)
_EVENT_PERIOD_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})\s*/\s*"
    r"([^/]+)\s*/\s*([0-2]\d:[0-5]\d\s*~\s*[0-2]\d:[0-5]\d)"
)
_MAP_RE = re.compile(
    r"f_mapPop\(\s*'(?P<address>[^']+)'\s*,\s*"
    r"'(?P<latitude>-?\d+(?:\.\d+)?)'\s*,\s*"
    r"'(?P<longitude>-?\d+(?:\.\d+)?)'\s*\)\s*;?"
)
_ADDRESS_RE = re.compile(r"(?:경기|경기도)\s+([가-힣]+(?:시|군))(?:\s+|$)")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_DETAIL_FIELDS = frozenset({"구분", "이용대상", "신청기간", "교육기간", "요금", "교육장소"})
_LIST_INFO_FIELDS = frozenset({"예약방법", "선별방법"})
_STATUS_MAP: Mapping[str, str] = {
    "신청가능": "OPEN",
    "대기접수": "OPEN",
    "접수마감": "CLOSED",
    "신청예정": "SCHEDULED",
}
_NON_PROGRAM_TITLE_MARKERS = (
    "공지사항",
    "휴관안내",
    "시설안내",
    "운영안내",
    "대관안내",
    "채용공고",
    "입찰공고",
    "테스트",
    "test",
)
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "applicant",
        "applicant_name",
        "member",
        "member_id",
        "user",
        "user_id",
        "phone",
        "email",
        "contact",
        "manager",
        "attachment",
        "attachments",
        "download",
        "latitude",
        "longitude",
        "raw_html",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise GyeonggiShareExperienceContractError(f"{field} must be positive")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GyeonggiShareExperienceContractError(f"{field} must be positive") from exc
    if result < 1:
        raise GyeonggiShareExperienceContractError(f"{field} must be positive")
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _canonical_target_url(value: Any) -> bool:
    try:
        parsed = urlparse(_clean(value))
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GYEONGGI_SHARE_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GYEONGGI_SHARE_EXPERIENCE_LIST_PATH
        and query == [("eshare", "1"), ("c1", "32034"), ("c3", "20")]
        and not parsed.params
        and not parsed.fragment
    )


def is_gyeonggi_share_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")).upper()
        == GYEONGGI_SHARE_EXPERIENCE_PROVIDER
        and _canonical_target_url(_target_value(target, "url"))
    )


is_target = is_gyeonggi_share_experience_target


def gyeonggi_share_experience_list_url(page: Any = 1) -> str:
    current = _positive(page, "page")
    if current == 1:
        return GYEONGGI_SHARE_EXPERIENCE_URL
    return (
        f"https://{GYEONGGI_SHARE_EXPERIENCE_HOST}"
        f"{GYEONGGI_SHARE_EXPERIENCE_LIST_PATH}?"
        + urlencode(
            (
                ("curPage", str(current)),
                ("eshare", "1"),
                ("c1", "32034"),
                ("c3", "20"),
            )
        )
    )


def gyeonggi_share_experience_detail_url(identity: Any) -> str:
    clean_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(clean_identity):
        raise GyeonggiShareExperienceContractError("invalid public detail identity")
    return (
        f"https://{GYEONGGI_SHARE_EXPERIENCE_HOST}"
        f"{GYEONGGI_SHARE_EXPERIENCE_DETAIL_PATH}?"
        + urlencode((("eshare", "1"), ("id", clean_identity)))
    )


def _request_kind(method: Any, url: Any) -> str:
    if _clean(method).upper() != "GET":
        raise GyeonggiShareExperienceContractError(
            "only audited public list/detail GET requests are allowed"
        )
    try:
        parsed = urlparse(_clean(url))
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise GyeonggiShareExperienceContractError("invalid request URL") from exc
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GYEONGGI_SHARE_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.fragment
    ):
        raise GyeonggiShareExperienceContractError("request escaped official host")
    if parsed.path == GYEONGGI_SHARE_EXPERIENCE_LIST_PATH:
        if query == [("eshare", "1"), ("c1", "32034"), ("c3", "20")]:
            return "list"
        if (
            len(query) == 4
            and query[0][0] == "curPage"
            and query[1:] == [("eshare", "1"), ("c1", "32034"), ("c3", "20")]
        ):
            _positive(query[0][1], "page")
            return "list"
    if (
        parsed.path == GYEONGGI_SHARE_EXPERIENCE_DETAIL_PATH
        and len(query) == 2
        and query[0] == ("eshare", "1")
        and query[1][0] == "id"
        and _IDENTITY_RE.fullmatch(query[1][1])
    ):
        return "detail"
    raise GyeonggiShareExperienceContractError(
        "NetFunnel/apply/login/auth/member/applicant/PII/attachment/download route blocked"
    )


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_soup(response: Any, expected_url: str, expected_kind: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise GyeonggiShareExperienceContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise GyeonggiShareExperienceContractError("redirected response refused")
    final_url = _clean(getattr(response, "url", ""))
    if final_url:
        if _request_kind("GET", final_url) != expected_kind:
            raise GyeonggiShareExperienceContractError("response request kind changed")
        if parse_qsl(urlparse(final_url).query, keep_blank_values=True) != parse_qsl(
            urlparse(expected_url).query, keep_blank_values=True
        ):
            raise GyeonggiShareExperienceContractError("response identity changed")
    content = getattr(response, "content", None)
    if content is None:
        content = str(getattr(response, "text", "")).encode("utf-8")
    if not isinstance(content, (bytes, bytearray)):
        raise GyeonggiShareExperienceContractError("response body is not bytes")
    payload = bytes(content)
    if not payload or len(payload) > GYEONGGI_SHARE_EXPERIENCE_MAX_BYTES:
        raise GyeonggiShareExperienceContractError("response body size outside contract")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GyeonggiShareExperienceContractError("response is not strict UTF-8") from exc
    if "\x00" in text:
        raise GyeonggiShareExperienceContractError("response contains NUL bytes")
    soup = BeautifulSoup(text, "html.parser")
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "경기공유서비스":
        raise GyeonggiShareExperienceContractError("official document title changed")
    return soup


class _Requester:
    def __init__(
        self,
        session_factory: SessionFactory,
        timeout: int,
        meta: dict[str, Any],
    ) -> None:
        self.session = session_factory()
        self.timeout = timeout
        self.meta = meta

    def __enter__(self) -> _Requester:
        return self

    def __exit__(self, *_args: Any) -> None:
        _close(self.session)

    def soup(self, url: str) -> BeautifulSoup:
        kind = _request_kind("GET", url)
        self.meta["physical_requests"] += 1
        self.meta[f"{kind}_requests"] += 1
        response = self.session.get(url, timeout=self.timeout, allow_redirects=False)
        return _response_soup(response, url, kind)


def _single_text(root: Tag | BeautifulSoup, selector: str, field: str) -> str:
    nodes = root.select(selector)
    if len(nodes) != 1:
        raise GyeonggiShareExperienceContractError(f"{field} selector cardinality changed")
    value = _clean(nodes[0].get_text(" ", strip=True))
    if not value:
        raise GyeonggiShareExperienceContractError(f"{field} became empty")
    return value


def _input_contract(soup: BeautifulSoup) -> None:
    expected = {
        "#search-category": "32034",
        "#searchCategory": "20",
        "#reservAvailable": "0",
        "#all": "1",
    }
    for selector, value in expected.items():
        nodes = soup.select(selector)
        if len(nodes) != 1 or _clean(nodes[0].get("value")) != value:
            raise GyeonggiShareExperienceContractError("canonical list filter changed")
    if not soup.select_one("#reservAvailable[checked]") or soup.select_one("#all[checked]"):
        raise GyeonggiShareExperienceContractError("reservation-available filter changed")


def _declared_total(soup: BeautifulSoup) -> int:
    nodes = soup.select(".lineR em.tgg1")
    if len(nodes) != 1:
        raise GyeonggiShareExperienceContractError("declared total marker changed")
    value = _clean(nodes[0].get_text(" ", strip=True)).replace(",", "")
    if not _TOTAL_RE.fullmatch(value):
        raise GyeonggiShareExperienceContractError("declared total is invalid")
    return int(value)


def _is_cookie_bootstrap_shell(soup: BeautifulSoup) -> bool:
    """Recognize the host's exact first-party cookie-setting empty shell."""

    _input_contract(soup)
    nodes = soup.select(".lineR em.tgg1")
    return bool(
        len(nodes) == 1
        and not _clean(nodes[0].get_text(" ", strip=True))
        and not soup.select(".service-list, .service-card-list, .paging")
    )


def _parse_card(card: Tag, page: int, position: int) -> _ListRow:
    anchors = card.select(":scope > a[onclick]")
    if len(anchors) != 1 or _clean(anchors[0].get("href")) != "#":
        raise GyeonggiShareExperienceContractError("list identity control changed")
    match = _ONCLICK_RE.fullmatch(_clean(anchors[0].get("onclick")))
    if not match:
        raise GyeonggiShareExperienceContractError("NetFunnel detail identity changed")
    identity = match.group("identity")
    state_nodes = card.select(".service-card .state-div > .state")
    if len(state_nodes) != 2:
        raise GyeonggiShareExperienceContractError("list state vocabulary changed")
    list_area = _clean(state_nodes[0].get_text(" ", strip=True))
    if list_area not in GYEONGGI_SHARE_EXPERIENCE_MUNICIPALITIES:
        raise GyeonggiShareExperienceContractError("unknown displayed municipality")
    if _clean(state_nodes[1].get_text(" ", strip=True)) != "직접예약":
        raise GyeonggiShareExperienceContractError("non-direct reservation card entered ledger")
    title = _single_text(card, ".service-card .title", "list title")
    if any(marker in title.lower() for marker in _NON_PROGRAM_TITLE_MARKERS):
        raise GyeonggiShareExperienceContractError("notice/facility/test title entered ledger")
    recommendation = card.select(".service-card .recom-list-txt01 > span")
    if len(recommendation) != 2:
        raise GyeonggiShareExperienceContractError("list institution/category changed")
    institution = _clean(recommendation[0].get_text(" ", strip=True))
    subcategory = _clean(recommendation[1].get_text(" ", strip=True))
    if not institution or not subcategory or "체험" not in subcategory:
        raise GyeonggiShareExperienceContractError("card is not an exact experience programme")
    info: dict[str, str] = {}
    for item in card.select(".article-list-body .info-list"):
        labels = item.select(":scope > dt")
        values = item.select(":scope > dd")
        if len(labels) != 1 or len(values) != 1:
            raise GyeonggiShareExperienceContractError("list information shape changed")
        label = _clean(labels[0].get_text(" ", strip=True))
        value = _clean(values[0].get_text(" ", strip=True))
        if not label or not value or label in info:
            raise GyeonggiShareExperienceContractError("list information value changed")
        info[label] = value
    if frozenset(info) != _LIST_INFO_FIELDS:
        raise GyeonggiShareExperienceContractError("list information vocabulary changed")
    fee_nodes = card.select(".article-list-body .info-box > ul > li")
    if not fee_nodes:
        raise GyeonggiShareExperienceContractError("list fee marker changed")
    list_fee = _clean(fee_nodes[0].get_text(" ", strip=True))
    wait_notice = " | ".join(
        value
        for value in (_clean(node.get_text(" ", strip=True)) for node in fee_nodes[1:])
        if value
    )
    if not list_fee:
        raise GyeonggiShareExperienceContractError("list fee became empty")
    return _ListRow(
        identity=identity,
        title=title,
        list_area=list_area,
        institution=institution,
        subcategory=subcategory,
        booking_method=info["예약방법"],
        selection_method=info["선별방법"],
        list_fee=list_fee,
        wait_notice=wait_notice,
        page=page,
        position=position,
    )


def _pager_contract(soup: BeautifulSoup, page: int, last_page: int) -> None:
    pagers = soup.select(".paging")
    if len(pagers) != 1:
        raise GyeonggiShareExperienceContractError("data-page pager changed")
    active = pagers[0].select(":scope > a.active")
    if len(active) != 1 or _clean(active[0].get_text(" ", strip=True)) != str(page):
        raise GyeonggiShareExperienceContractError("active page marker changed")
    last_links = pagers[0].select(":scope > a.ico.last[href]")
    if len(last_links) != 1:
        raise GyeonggiShareExperienceContractError("last page link changed")
    expected_query = parse_qsl(
        urlparse(gyeonggi_share_experience_list_url(last_page)).query,
        keep_blank_values=True,
    )
    actual = urlparse(_clean(last_links[0].get("href")))
    if actual.path or parse_qsl(actual.query, keep_blank_values=True) != expected_query:
        raise GyeonggiShareExperienceContractError("declared last page link changed")


def _parse_list_page(soup: BeautifulSoup, page: int) -> _ListPage:
    _input_contract(soup)
    total = _declared_total(soup)
    last_page = max(1, math.ceil(total / GYEONGGI_SHARE_EXPERIENCE_PAGE_SIZE))
    cards = soup.select(".service-card-list > li")
    if page > last_page:
        if cards or soup.select(".service-list, .service-card-list, .paging"):
            raise GyeonggiShareExperienceContractError("post-last page is not exactly empty")
        return _ListPage(page, total, last_page, (), True)
    if total == 0:
        if page != 1 or cards or soup.select(".service-list, .service-card-list, .paging"):
            raise GyeonggiShareExperienceContractError("zero-result first page changed")
        return _ListPage(page, total, last_page, (), True)
    if not cards:
        raise GyeonggiShareExperienceContractError("declared data page became empty")
    _pager_contract(soup, page, last_page)
    rows = tuple(
        _parse_card(card, page, position)
        for position, card in enumerate(cards, start=1)
    )
    return _ListPage(page, total, last_page, rows, False)


def _list_fingerprint(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.total,
        page.last_page,
        tuple(
            (
                row.identity,
                row.title,
                row.list_area,
                row.institution,
                row.subcategory,
                row.booking_method,
                row.selection_method,
                row.list_fee,
                row.wait_notice,
            )
            for row in page.rows
        ),
    )


def _parse_apply_period(value: str) -> tuple[datetime, datetime]:
    match = _APPLY_PERIOD_RE.fullmatch(_clean(value))
    if not match:
        raise GyeonggiShareExperienceContractError("application period changed")
    try:
        start = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M")
        end = datetime.strptime(match.group(2), "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise GyeonggiShareExperienceContractError("invalid application datetime") from exc
    if start > end:
        raise GyeonggiShareExperienceContractError("reversed application period")
    return start, end


def _parse_event_period(value: str) -> tuple[date, date, str, str]:
    match = _EVENT_PERIOD_RE.fullmatch(_clean(value))
    if not match:
        raise GyeonggiShareExperienceContractError("education period changed")
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
    except ValueError as exc:
        raise GyeonggiShareExperienceContractError("invalid education date") from exc
    if start > end:
        raise GyeonggiShareExperienceContractError("reversed education period")
    weekday = _clean(match.group(3))
    schedule = _clean(match.group(4)).replace(" ", "")
    if not weekday:
        raise GyeonggiShareExperienceContractError("education weekday became empty")
    return start, end, weekday, schedule


def _address_municipality(address: str) -> _Municipality:
    match = _ADDRESS_RE.match(_clean(address))
    if not match:
        raise GyeonggiShareExperienceContractError("official map address is not in Gyeonggi")
    municipality = GYEONGGI_SHARE_EXPERIENCE_MUNICIPALITIES.get(match.group(1))
    if municipality is None:
        raise GyeonggiShareExperienceContractError("unknown official map municipality")
    return municipality


def _detail_pairs(body: Tag) -> dict[str, tuple[str, Tag]]:
    pairs: dict[str, tuple[str, Tag]] = {}
    items = body.select(":scope > ul > li")
    if not items:
        raise GyeonggiShareExperienceContractError("detail dataBody became empty")
    for item in items:
        labels = item.select(":scope > .lineL")
        values = item.select(":scope > .txt")
        if len(labels) != 1 or len(values) != 1:
            raise GyeonggiShareExperienceContractError("detail field shape changed")
        label = _clean(labels[0].get_text(" ", strip=True))
        value = _clean(values[0].get_text(" ", strip=True))
        if not label or not value or label in pairs:
            raise GyeonggiShareExperienceContractError("detail field value changed")
        pairs[label] = (value, values[0])
    if frozenset(pairs) != _DETAIL_FIELDS:
        raise GyeonggiShareExperienceContractError("detail field vocabulary changed")
    return pairs


def _title_region(title: str) -> str:
    matches = [
        name
        for name in GYEONGGI_SHARE_EXPERIENCE_MUNICIPALITIES
        if name in title or name[:-1] in title
    ]
    if not matches:
        return ""
    longest = max(len(name[:-1]) for name in matches)
    winners = {name for name in matches if len(name[:-1]) == longest}
    return next(iter(winners)) if len(winners) == 1 else ""


def _parse_detail(soup: BeautifulSoup, row: _ListRow) -> _Detail:
    headers = soup.select(".conHeader")
    if len(headers) != 1:
        raise GyeonggiShareExperienceContractError("public detail conHeader changed")
    header = headers[0]
    subcategory = _single_text(header, ".title-secondary", "detail subcategory")
    title = _single_text(header, ".title-primary", "detail title")
    data_title = _single_text(header, ".dataHead .tit", "detail data title")
    detail_area = _single_text(header, ".conheadWrap .txt1", "detail displayed area")
    institution = _single_text(header, ".conheadWrap .txt2", "detail institution")
    source_status = _single_text(header, ".headCon .option-text", "detail status")
    if source_status not in _STATUS_MAP:
        raise GyeonggiShareExperienceContractError("unknown public detail status")
    if title != row.title or data_title != row.title:
        raise GyeonggiShareExperienceContractError("list/detail programme identity mismatch")
    if institution != row.institution or subcategory != row.subcategory:
        raise GyeonggiShareExperienceContractError("list/detail owner/category mismatch")
    if detail_area not in GYEONGGI_SHARE_EXPERIENCE_MUNICIPALITIES:
        raise GyeonggiShareExperienceContractError("unknown detail displayed municipality")
    bodies = header.select(".headCon > .dataBox .dataBody")
    if len(bodies) != 1:
        raise GyeonggiShareExperienceContractError("detail conHeader dataBody changed")
    pairs = _detail_pairs(bodies[0])
    category = pairs["구분"][0]
    if category != f"체험/견학({subcategory})":
        raise GyeonggiShareExperienceContractError("detail is not exact experience/visit")
    target = pairs["이용대상"][0]
    apply_start, apply_end = _parse_apply_period(pairs["신청기간"][0])
    event_start, event_end, weekday, schedule = _parse_event_period(
        pairs["교육기간"][0]
    )
    fee = pairs["요금"][0]
    if fee != row.list_fee:
        raise GyeonggiShareExperienceContractError("list/detail fee mismatch")
    venue_value, venue_node = pairs["교육장소"]
    map_buttons = venue_node.select(":scope > button.aLink.map[onclick]")
    if len(map_buttons) != 1:
        raise GyeonggiShareExperienceContractError("official map control changed")
    map_match = _MAP_RE.fullmatch(_clean(map_buttons[0].get("onclick")))
    if not map_match:
        raise GyeonggiShareExperienceContractError("official f_mapPop contract changed")
    venue_address = _clean(map_match.group("address"))
    visible_place = venue_value.removesuffix("지도보기").strip()
    if not (
        visible_place == venue_address
        or visible_place.startswith(venue_address + " ")
    ):
        raise GyeonggiShareExperienceContractError("visible/map address mismatch")
    venue_name = visible_place[len(venue_address) :].strip()
    if not venue_name:
        raise GyeonggiShareExperienceContractError("official venue name became empty")
    municipality = _address_municipality(venue_address)
    application_controls = len(
        soup.select(
            "a[href*='/lecture/apply'], form[action*='/lecture/apply'], "
            "[onclick*='/lecture/apply'], [onclick*='eduApply']"
        )
    )
    related_dl_ignored = len(soup.select("dl"))
    return _Detail(
        title=title,
        detail_area=detail_area,
        institution=institution,
        subcategory=subcategory,
        source_status=source_status,
        status=_STATUS_MAP[source_status],
        target=target,
        apply_start=apply_start,
        apply_end=apply_end,
        event_start=event_start,
        event_end=event_end,
        weekday=weekday,
        schedule=schedule,
        fee=fee,
        venue_address=venue_address,
        venue_name=venue_name,
        municipality=municipality,
        application_controls=application_controls,
        related_dl_ignored=related_dl_ignored,
    )


def _output_row(source: _ListRow, detail: _Detail) -> dict[str, Any]:
    identity = source.identity
    municipality = detail.municipality
    detail_url = gyeonggi_share_experience_detail_url(identity)
    return {
        "provider": GYEONGGI_SHARE_EXPERIENCE_PROVIDER,
        "provider_course_id": (
            f"{GYEONGGI_SHARE_EXPERIENCE_PROVIDER}:experience:{identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "source_course_id": identity,
        "title": detail.title,
        "branch": detail.institution,
        "branch_code": "GGSHARE_"
        + hashlib.sha256(detail.institution.encode("utf-8")).hexdigest()[:16].upper(),
        "preserve_branch": True,
        "provider_organizer": detail.institution,
        "raw_url": detail_url,
        "source_url": GYEONGGI_SHARE_EXPERIENCE_URL,
        "application_url": "",
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "status": detail.status,
        "course_status": detail.status,
        "source_status": detail.source_status,
        "start_date": detail.event_start.isoformat(),
        "end_date": detail.event_end.isoformat(),
        "period": f"{detail.event_start.isoformat()} ~ {detail.event_end.isoformat()}",
        "apply_start": detail.apply_start.isoformat(sep=" ", timespec="minutes"),
        "apply_end": detail.apply_end.isoformat(sep=" ", timespec="minutes"),
        "apply_period": (
            f"{detail.apply_start.isoformat(sep=' ', timespec='minutes')} ~ "
            f"{detail.apply_end.isoformat(sep=' ', timespec='minutes')}"
        ),
        "schedule_raw": detail.schedule,
        "target": detail.target,
        "target_audience": detail.target,
        "fee": detail.fee,
        "location": detail.venue_name,
        "venue_name": detail.venue_name,
        "address": detail.venue_address,
        "venue_address": detail.venue_address,
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "category": f"체험·견학 > {detail.subcategory}",
        "operator_type": "광역자치단체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "program_type": "체험",
        "program_type_source": "official_gyeonggi_share_experience_menu",
        "classification_locked": True,
        "collection_type": GYEONGGI_SHARE_EXPERIENCE_PARSER,
        "municipality_code": municipality.code,
        "municipality_name": municipality.full_name,
        "municipality_full_name": municipality.full_name,
        "region": municipality.full_name,
        "sido": "경기도",
        "sigungu": municipality.sigungu,
        "raw_fields": {
            "parser": GYEONGGI_SHARE_EXPERIENCE_PARSER,
            "identity": identity,
            "source_page": source.page,
            "source_position": source.position,
            "source_list_area": source.list_area,
            "source_detail_area": detail.detail_area,
            "source_subcategory": detail.subcategory,
            "source_booking_method": source.booking_method,
            "source_selection_method": source.selection_method,
            "source_wait_notice": source.wait_notice,
            "source_weekday": detail.weekday,
            "source_status": detail.source_status,
            "detail_verified": True,
            "venue_mapping_basis": "official_conHeader_f_mapPop_address_allowlist",
            "list_area_used_for_venue": False,
            "title_region_used_for_venue": False,
            "netfunnel_submit_called": False,
            "application_endpoint_called": False,
            "pii_fields_omitted": True,
        },
    }


def _privacy_errors(value: Any, path: str = "row") -> list[str]:
    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _FORBIDDEN_OUTPUT_KEYS:
                errors.append(f"{path}.{key}: forbidden output key")
            errors.extend(_privacy_errors(item, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            errors.extend(_privacy_errors(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        if _PHONE_RE.search(value) or _EMAIL_RE.search(value) or _RESIDENT_ID_RE.search(value):
            errors.append(f"{path}: PII-shaped output")
    return errors


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str = "", *, cutoff: str = "") -> dict[str, Any]:
    return {
        "provider": GYEONGGI_SHARE_EXPERIENCE_PROVIDER,
        "ownership_scope": GYEONGGI_SHARE_EXPERIENCE_OWNERSHIP_SCOPE,
        "cutoff": cutoff,
        "source_total": 0,
        "source_rows": 0,
        "source_current_count": 0,
        "source_expired_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "data_pages": 0,
        "sentinel_page": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "detail_pages": 0,
        "physical_requests": 0,
        "cookie_bootstrap_shell_requests": 0,
        "application_endpoint_requests": 0,
        "netfunnel_submit_calls": 0,
        "login_auth_identity_applicant_member_pii_endpoint_requests": 0,
        "attachment_image_download_endpoint_requests": 0,
        "unsafe_endpoint_calls": 0,
        "pii_payload_persisted": False,
        "pagination_complete": False,
        "details_complete": False,
        "venue_mapping_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_gyeonggi_share_experience(
    target: Any,
    timeout: int = 20,
    max_pages: int = 10,
    detail_limit: int = 50,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future Gyeonggi Share experience snapshot."""

    if not is_gyeonggi_share_experience_target(target):
        return [], GYEONGGI_SHARE_EXPERIENCE_PARSER, _failure(
            "target does not match the exact Gyeonggi Share experience owner"
        )
    try:
        allowed_list_requests = _positive(max_pages, "max_pages")
        allowed_details = _positive(detail_limit, "detail_limit")
        cutoff = _today(today)
    except (TypeError, ValueError, GyeonggiShareExperienceContractError) as exc:
        return [], GYEONGGI_SHARE_EXPERIENCE_PARSER, _failure(
            f"invalid collection limits or date: {exc}"
        )
    factory = session_factory or _default_session_factory
    meta = _failure(cutoff=cutoff.isoformat())
    try:
        with _Requester(factory, _positive(timeout, "timeout"), meta) as requester:
            first_soup = requester.soup(gyeonggi_share_experience_list_url(1))
            if _is_cookie_bootstrap_shell(first_soup):
                meta["cookie_bootstrap_shell_requests"] = 1
                first_soup = requester.soup(gyeonggi_share_experience_list_url(1))
                if _is_cookie_bootstrap_shell(first_soup):
                    raise GyeonggiShareExperienceContractError(
                        "canonical list remained an empty cookie bootstrap shell"
                    )
            first = _parse_list_page(first_soup, 1)
            data_pages = first.last_page
            required_list_requests = (
                data_pages + 4 + int(meta["cookie_bootstrap_shell_requests"])
            )
            if required_list_requests > allowed_list_requests:
                raise GyeonggiShareExperienceContractError(
                    f"max_pages permits {allowed_list_requests} of "
                    f"{required_list_requests} required list requests"
                )
            pages: dict[int, _ListPage] = {1: first}
            for page in range(2, data_pages + 1):
                current = _parse_list_page(
                    requester.soup(gyeonggi_share_experience_list_url(page)), page
                )
                if current.total != first.total or current.last_page != data_pages:
                    raise GyeonggiShareExperienceContractError("declared pagination changed")
                pages[page] = current
            sentinel_page = data_pages + 1
            sentinel = _parse_list_page(
                requester.soup(gyeonggi_share_experience_list_url(sentinel_page)),
                sentinel_page,
            )
            if (
                sentinel.total != first.total
                or sentinel.last_page != data_pages
                or not sentinel.exact_empty
                or sentinel.rows
            ):
                raise GyeonggiShareExperienceContractError("post-last sentinel changed")

            page_counts = {page: len(value.rows) for page, value in pages.items()}
            if first.total:
                for page in range(1, data_pages):
                    if page_counts.get(page) != GYEONGGI_SHARE_EXPERIENCE_PAGE_SIZE:
                        raise GyeonggiShareExperienceContractError("non-terminal page is not full")
                expected_terminal = first.total - (
                    GYEONGGI_SHARE_EXPERIENCE_PAGE_SIZE * (data_pages - 1)
                )
                if page_counts.get(data_pages) != expected_terminal:
                    raise GyeonggiShareExperienceContractError(
                        "terminal page count differs from declared total"
                    )
            elif page_counts != {1: 0}:
                raise GyeonggiShareExperienceContractError("zero-result pagination changed")
            source_rows = [
                row for page in range(1, data_pages + 1) for row in pages[page].rows
            ]
            if len(source_rows) != first.total:
                raise GyeonggiShareExperienceContractError("parsed rows differ from declared total")
            identities = [row.identity for row in source_rows]
            if len(set(identities)) != len(identities):
                raise GyeonggiShareExperienceContractError("duplicate public programme identity")
            if len(source_rows) > allowed_details:
                meta["source_cap_reached"] = True
                raise GyeonggiShareExperienceContractError(
                    "detail_limit truncates the complete public source"
                )

            details: dict[str, _Detail] = {}
            application_controls = 0
            related_dl_ignored = 0
            for source in source_rows:
                detail = _parse_detail(
                    requester.soup(gyeonggi_share_experience_detail_url(source.identity)),
                    source,
                )
                details[source.identity] = detail
                application_controls += detail.application_controls
                related_dl_ignored += detail.related_dl_ignored

            first_check = _parse_list_page(
                requester.soup(gyeonggi_share_experience_list_url(1)), 1
            )
            last_check = _parse_list_page(
                requester.soup(gyeonggi_share_experience_list_url(data_pages)),
                data_pages,
            )
            sentinel_check = _parse_list_page(
                requester.soup(gyeonggi_share_experience_list_url(sentinel_page)),
                sentinel_page,
            )
            if _list_fingerprint(first_check) != _list_fingerprint(pages[1]):
                raise GyeonggiShareExperienceContractError("first list page changed")
            if _list_fingerprint(last_check) != _list_fingerprint(pages[data_pages]):
                raise GyeonggiShareExperienceContractError("last list page changed")
            if (
                _list_fingerprint(sentinel_check) != _list_fingerprint(sentinel)
                or not sentinel_check.exact_empty
            ):
                raise GyeonggiShareExperienceContractError("sentinel list page changed")

            current_sources = [
                source
                for source in source_rows
                if details[source.identity].event_end >= cutoff
            ]
            expired_sources = [
                source
                for source in source_rows
                if details[source.identity].event_end < cutoff
            ]
            outputs = [_output_row(source, details[source.identity]) for source in current_sources]
            privacy_errors = [error for row in outputs for error in _privacy_errors(row)]
            if privacy_errors:
                raise GyeonggiShareExperienceContractError(privacy_errors[0])
            dedupe = dedupe_rows or _dedupe_default
            result = list(dedupe(outputs))
            if len(result) != len(outputs):
                raise GyeonggiShareExperienceContractError("output dedupe changed cardinality")
            if any(bool(row["application_url"]) != row["reservation_available"] for row in result):
                raise GyeonggiShareExperienceContractError("application URL contract changed")

            list_detail_anomalies: list[dict[str, str]] = []
            title_address_anomalies: list[dict[str, str]] = []
            for source in source_rows:
                detail = details[source.identity]
                if source.list_area != detail.municipality.sigungu or detail.detail_area != detail.municipality.sigungu:
                    list_detail_anomalies.append(
                        {
                            "identity": source.identity,
                            "list_area": source.list_area,
                            "detail_area": detail.detail_area,
                            "venue_region": detail.municipality.sigungu,
                        }
                    )
                title_region = _title_region(source.title)
                if title_region and title_region != detail.municipality.sigungu:
                    title_address_anomalies.append(
                        {
                            "identity": source.identity,
                            "title_region": title_region,
                            "venue_region": detail.municipality.sigungu,
                        }
                    )

            status_counts = Counter(row["status"] for row in result)
            source_status_counts = Counter(
                details[source.identity].source_status for source in source_rows
            )
            municipality_counts = Counter(row["municipality_code"] for row in result)
            meta.update(
                {
                    "source_total": first.total,
                    "source_rows": len(source_rows),
                    "source_current_count": len(current_sources),
                    "source_expired_count": len(expired_sources),
                    "current_count": len(current_sources),
                    "returned_count": len(result),
                    "data_pages": data_pages,
                    "sentinel_page": sentinel_page,
                    "page_counts": page_counts,
                    "detail_pages": len(details),
                    "detail_verified": len(details),
                    "source_status_counts": dict(sorted(source_status_counts.items())),
                    "status_counts": dict(sorted(status_counts.items())),
                    "municipality_counts": dict(sorted(municipality_counts.items())),
                    "returned_municipality_count": len(municipality_counts),
                    "source_identity_sha256": _identity_hash(identities),
                    "output_identity_sha256": _identity_hash(
                        _clean(row.get("provider_course_id")) for row in result
                    ),
                    "application_controls_observed_not_called": application_controls,
                    "related_course_dl_nodes_ignored": related_dl_ignored,
                    "list_detail_address_region_anomalies": list_detail_anomalies,
                    "list_detail_address_region_anomaly_count": len(list_detail_anomalies),
                    "title_address_region_anomalies": title_address_anomalies,
                    "title_address_region_anomaly_count": len(title_address_anomalies),
                    "unknown_address_count": 0,
                    "stable_first_page": True,
                    "stable_last_page": True,
                    "stable_sentinel_page": True,
                    "pagination_complete": True,
                    "details_complete": len(details) == len(source_rows),
                    "venue_mapping_complete": True,
                    "snapshot_complete": True,
                    "full_snapshot_validated": True,
                    "no_current_data": not result,
                    "configured_collection_error": "",
                }
            )
            return result, GYEONGGI_SHARE_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "pagination_complete": False,
                "details_complete": False,
                "venue_mapping_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "configured_collection_error": str(exc),
            }
        )
        return [], GYEONGGI_SHARE_EXPERIENCE_PARSER, meta


collect = collect_gyeonggi_share_experience


__all__ = [
    "GYEONGGI_SHARE_EXPERIENCE_CANDIDATE_ID",
    "GYEONGGI_SHARE_EXPERIENCE_COVERED_MUNICIPALITIES",
    "GYEONGGI_SHARE_EXPERIENCE_LIVE_BASELINE",
    "GYEONGGI_SHARE_EXPERIENCE_MUNICIPALITIES",
    "GYEONGGI_SHARE_EXPERIENCE_OWNERSHIP_SCOPE",
    "GYEONGGI_SHARE_EXPERIENCE_PARSER",
    "GYEONGGI_SHARE_EXPERIENCE_PROVIDER",
    "GYEONGGI_SHARE_EXPERIENCE_URL",
    "GyeonggiShareExperienceContractError",
    "collect",
    "collect_gyeonggi_share_experience",
    "gyeonggi_share_experience_detail_url",
    "gyeonggi_share_experience_list_url",
    "is_gyeonggi_share_experience_target",
    "is_target",
]
