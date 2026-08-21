"""Fail-closed collector for Seongnam Museum's current experience programmes.

The first-party programme ledger publishes stable programme identities, declared
pagination, programme/application periods, status, venue, audience and a public
detail document.  This collector requests only that GET list/detail contract.
It never follows or requests the separate application, login, reservation,
identity, applicant, member, attachment, image or download routes.

The museum host currently requires a legacy cipher policy.  The adapter lowers
only OpenSSL's cipher security level while retaining certificate verification
and hostname checking.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
import ssl
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag
import requests
from requests.adapters import HTTPAdapter


SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER = "MUNI_MUSEUM_SEONGNAM_GO_KR_58E7A57F"
SEONGNAM_MUSEUM_EXPERIENCE_HOST = "museum.seongnam.go.kr"
SEONGNAM_MUSEUM_EXPERIENCE_PATH = "/shm/contents/shm-ingProgram.do"
SEONGNAM_MUSEUM_EXPERIENCE_URL = f"https://{SEONGNAM_MUSEUM_EXPERIENCE_HOST}{SEONGNAM_MUSEUM_EXPERIENCE_PATH}"
SEONGNAM_MUSEUM_EXPERIENCE_MUNICIPALITY_CODE = "4113100000"
SEONGNAM_MUSEUM_EXPERIENCE_MUNICIPALITY_NAME = "경기도 성남시 수정구"
SEONGNAM_MUSEUM_EXPERIENCE_BRANCH = "성남시박물관"
SEONGNAM_MUSEUM_EXPERIENCE_BRANCH_CODE = "SEONGNAM_CITY_MUSEUM"
SEONGNAM_MUSEUM_EXPERIENCE_ADDRESS = "경기도 성남시 수정구 희망로 475"
SEONGNAM_MUSEUM_EXPERIENCE_PARSER = (
    "seongnam_museum_current_experience_complete_pages+exact_empty_post_last+"
    "stable_first_last_sentinel+all_current_public_details+structured_field_allowlist+"
    "verified_legacy_tls+locked_experience+no_application_login_identity_applicant_"
    "member_attachment_download_or_pii_calls"
)
SEONGNAM_MUSEUM_EXPERIENCE_OWNERSHIP_SCOPE = "seongnam_city_museum_complete_current_future_hands_on_programme_ledger"
SEONGNAM_MUSEUM_EXPERIENCE_LIVE_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_total": 6,
    "data_pages": 1,
    "sentinel_page": 2,
    "current_count": 6,
    "open_count": 5,
    "scheduled_count": 1,
}

_MAX_HTML_BYTES = 3_000_000
_SPACE_RE = re.compile(r"\s+")
_TOTAL_RE = re.compile(r"전체목록\s*:\s*([\d,]+)\s*건")
_VIEW_RE = re.compile(r"fn_goView\(\s*['\"]([1-9]\d{0,11})['\"]\s*\)\s*;?")
_GO_URL_RE = re.compile(r"goUrl\(\s*['\"]([^'\"]+)['\"]\s*\)\s*;?")
_IDENTITY_RE = re.compile(r"[1-9]\d{0,11}")
_DATE_PERIOD_RE = re.compile(r"\A\s*(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})\s*\Z")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_STATUS_MAP = {
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "교육중": "CLOSED",
    "접수마감": "CLOSED",
}
_LIST_FIELDS = frozenset({"대상", "교육기간", "신청기간"})
_DETAIL_FIELDS = frozenset(
    {
        "신청기간",
        "교육기간",
        "교육시간",
        "교육인원",
        "교육대상",
        "교육비",
        "교육장소",
        "문의",
    }
)
_EXPERIENCE_TITLE_MARKERS = (
    "체험",
    "만들기",
    "공예",
    "워크숍",
    "실습",
    "탐방",
    "해설",
    "로봇",
    "로보틱스",
    "코딩",
    "3d펜",
    "게임",
    "자율주행",
)
_NON_PROGRAM_TITLE_MARKERS = (
    "테스트",
    "test",
    "공지",
    "휴관",
    "운영안내",
    "시설안내",
    "대관",
    "위원",
    "동아리",
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
        "attachment",
        "download",
    }
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class SeongnamMuseumExperienceContractError(RuntimeError):
    """Raised when the audited public programme contract changes."""


@dataclass(frozen=True)
class _ListRow:
    identity: str
    title: str
    source_status: str
    venue: str
    target: str
    event_period: str
    event_start: date
    event_end: date
    apply_period: str
    apply_start: date
    apply_end: date
    list_application_url: str
    page: int


@dataclass(frozen=True)
class _ListPage:
    page: int
    total: int
    last: int
    rows: tuple[_ListRow, ...]
    empty_placeholder: bool


@dataclass(frozen=True)
class _Detail:
    title: str
    source_status: str
    fields: Mapping[str, str]
    application_url: str
    application_kind: str


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider")).upper()


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise SeongnamMuseumExperienceContractError(f"{field} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SeongnamMuseumExperienceContractError(f"{field} must be a positive integer") from exc
    if result < 1:
        raise SeongnamMuseumExperienceContractError(f"{field} must be a positive integer")
    return result


def _exact_target_url(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == SEONGNAM_MUSEUM_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.path == SEONGNAM_MUSEUM_EXPERIENCE_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_seongnam_museum_experience_target(target: Any) -> bool:
    return bool(
        _provider(target) == SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER and _exact_target_url(_target_value(target, "url"))
    )


is_target = is_seongnam_museum_experience_target


def seongnam_museum_experience_list_url(page: int) -> str:
    page_number = _positive_int(page, "page")
    return f"{SEONGNAM_MUSEUM_EXPERIENCE_URL}?{urlencode({'page': page_number})}"


def seongnam_museum_experience_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise SeongnamMuseumExperienceContractError("invalid programme identity")
    return f"{SEONGNAM_MUSEUM_EXPERIENCE_URL}?{urlencode({'schM': 'view', 'id': value})}"


def _request_kind(url: str) -> str:
    parsed = urlparse(_clean(url))
    if not (
        parsed.scheme == "https"
        and parsed.hostname == SEONGNAM_MUSEUM_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.path == SEONGNAM_MUSEUM_EXPERIENCE_PATH
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        raise SeongnamMuseumExperienceContractError("request escaped the audited public host/path")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) == {"page"} and len(query["page"]) == 1:
        _positive_int(query["page"][0], "page")
        return "list"
    if set(query) == {"schM", "id"} and query["schM"] == ["view"] and len(query["id"]) == 1:
        if not _IDENTITY_RE.fullmatch(query["id"][0]):
            raise SeongnamMuseumExperienceContractError("invalid detail identity")
        return "detail"
    raise SeongnamMuseumExperienceContractError("private or unrelated endpoint blocked")


class _VerifiedLegacyTLSAdapter(HTTPAdapter):
    @staticmethod
    def context() -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
        return context

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self.context()
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        proxy_kwargs["ssl_context"] = self.context()
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def configure_seongnam_museum_verified_session(current: requests.Session) -> requests.Session:
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    current.mount(
        f"https://{SEONGNAM_MUSEUM_EXPERIENCE_HOST}/",
        _VerifiedLegacyTLSAdapter(max_retries=0),
    )
    return current


def _default_session_factory() -> requests.Session:
    return configure_seongnam_museum_verified_session(requests.Session())


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    return current.get(url, timeout=timeout, allow_redirects=False)


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
        raise SeongnamMuseumExperienceContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise SeongnamMuseumExperienceContractError("redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url:
        if _request_kind(final_url) != expected_kind:
            raise SeongnamMuseumExperienceContractError("response changed request kind")
        if parse_qs(urlparse(final_url).query, keep_blank_values=True) != parse_qs(
            urlparse(expected_url).query,
            keep_blank_values=True,
        ):
            raise SeongnamMuseumExperienceContractError("response identity changed")
    content = getattr(response, "content", None)
    if content is None:
        content = str(getattr(response, "text", "")).encode("utf-8")
    if not isinstance(content, (bytes, bytearray)):
        raise SeongnamMuseumExperienceContractError("response body is not bytes")
    payload = bytes(content)
    if not payload or len(payload) > _MAX_HTML_BYTES:
        raise SeongnamMuseumExperienceContractError("response body size outside contract")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SeongnamMuseumExperienceContractError("response is not strict UTF-8") from exc
    if "\x00" in text:
        raise SeongnamMuseumExperienceContractError("response contains NUL bytes")
    soup = BeautifulSoup(text, "html.parser")
    if _clean(soup.title.get_text(" ", strip=True) if soup.title else "") != "성남시 박물관":
        raise SeongnamMuseumExperienceContractError("official page title changed")
    return soup


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
        return _response_soup(self.fetcher(self.session, url, self.timeout), url, kind)

    def close(self) -> None:
        _close(self.session)


def _parse_period(value: Any, field: str) -> tuple[str, date, date]:
    raw = _clean(value)
    match = _DATE_PERIOD_RE.fullmatch(raw)
    if not match:
        raise SeongnamMuseumExperienceContractError(f"invalid {field} period")
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
    except ValueError as exc:
        raise SeongnamMuseumExperienceContractError(f"impossible {field} date") from exc
    if start > end:
        raise SeongnamMuseumExperienceContractError(f"reversed {field} period")
    return f"{start.isoformat()} ~ {end.isoformat()}", start, end


def _field_pairs(items: Iterable[Tag], label_selector: str, value_selector: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in items:
        label_node = item.select_one(label_selector)
        value_node = item.select_one(value_selector)
        if label_node is None or value_node is None:
            raise SeongnamMuseumExperienceContractError("structured field shape changed")
        label = _clean(label_node.get_text(" ", strip=True))
        value = _clean(value_node.get_text(" ", strip=True))
        if not label or not value or label in pairs:
            raise SeongnamMuseumExperienceContractError("structured field label/value changed")
        pairs[label] = value
    return pairs


def _application_control(value: str) -> tuple[str, str]:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme == "https"
        and parsed.hostname == "sugang.seongnam.go.kr"
        and parsed.port is None
        and parsed.path == "/ilms/learning/learningList.do"
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        return value, "OFFICIAL_PUBLIC_LIST"
    if (
        parsed.scheme == "https"
        and parsed.hostname == SEONGNAM_MUSEUM_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        return "", "INFO_ONLY"
    raise SeongnamMuseumExperienceContractError("application control escaped audited public destinations")


def _parse_list_card(card: Tag, page: int) -> _ListRow:
    links = card.select("a[onclick]")
    identities = []
    for link in links:
        match = _VIEW_RE.fullmatch(_clean(link.get("onclick")))
        if match:
            identities.append(match.group(1))
    if len(identities) != 1:
        raise SeongnamMuseumExperienceContractError("programme identity control changed")
    statuses = [_clean(node.get_text(" ", strip=True)) for node in card.select("span.flag_kind")]
    if len(statuses) != 2 or len(set(statuses)) != 1 or statuses[0] not in _STATUS_MAP:
        raise SeongnamMuseumExperienceContractError("programme status contract changed")
    title_node = card.select_one(".top_box p.tit")
    venue_node = card.select_one(".top_box p.s_tit")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    venue = _clean(venue_node.get_text(" ", strip=True) if venue_node else "")
    if not title or len(title) > 300 or not venue.startswith(SEONGNAM_MUSEUM_EXPERIENCE_BRANCH):
        raise SeongnamMuseumExperienceContractError("programme title/venue changed")
    if _PHONE_RE.search(title) or _EMAIL_RE.search(title):
        raise SeongnamMuseumExperienceContractError("programme title contains contact data")
    pairs = _field_pairs(card.select("ul.middle_list > li"), ".lt_tit", ".lt_txt")
    if set(pairs) != _LIST_FIELDS:
        raise SeongnamMuseumExperienceContractError("list field vocabulary changed")
    event_period, event_start, event_end = _parse_period(pairs["교육기간"], "education")
    apply_period, apply_start, apply_end = _parse_period(pairs["신청기간"], "application")
    buttons = card.select(".btn_wrap button[onclick]")
    list_application_url = ""
    if len(buttons) > 1:
        raise SeongnamMuseumExperienceContractError("multiple list application controls")
    if buttons:
        match = _GO_URL_RE.fullmatch(_clean(buttons[0].get("onclick")))
        if not match:
            raise SeongnamMuseumExperienceContractError("list application control changed")
        list_application_url, _kind = _application_control(match.group(1))
    return _ListRow(
        identity=identities[0],
        title=title,
        source_status=statuses[0],
        venue=venue,
        target=pairs["대상"],
        event_period=event_period,
        event_start=event_start,
        event_end=event_end,
        apply_period=apply_period,
        apply_start=apply_start,
        apply_end=apply_end,
        list_application_url=list_application_url,
        page=page,
    )


def _parse_list_page(soup: BeautifulSoup, page: int) -> _ListPage:
    total_nodes = soup.select(".total_box > p.total")
    last_nodes = soup.select(".program_board-wrap .total_num")
    if len(total_nodes) != 1 or len(last_nodes) != 1:
        raise SeongnamMuseumExperienceContractError("declared pagination controls changed")
    total_match = _TOTAL_RE.fullmatch(_clean(total_nodes[0].get_text(" ", strip=True)))
    if not total_match:
        raise SeongnamMuseumExperienceContractError("declared total changed")
    total = int(total_match.group(1).replace(",", ""))
    last = _positive_int(_clean(last_nodes[0].get_text(" ", strip=True)), "last page")
    cards = soup.select("ul.program_list > li")
    empty_placeholder = bool(
        len(cards) == 1
        and _clean(cards[0].get_text(" ", strip=True)) == "준비중입니다."
        and not cards[0].select("a[onclick]")
    )
    rows: tuple[_ListRow, ...]
    if empty_placeholder:
        rows = ()
    else:
        if not cards:
            raise SeongnamMuseumExperienceContractError("programme list disappeared")
        rows = tuple(_parse_list_card(card, page) for card in cards)
    return _ListPage(
        page=page,
        total=total,
        last=last,
        rows=rows,
        empty_placeholder=empty_placeholder,
    )


def _parse_detail(soup: BeautifulSoup) -> _Detail:
    roots = soup.select(".academic_event-wrap .notice_img-view")
    if len(roots) != 1:
        raise SeongnamMuseumExperienceContractError("public detail root changed")
    root = roots[0]
    title_node = root.select_one(".img_txt-wrap > p.img_tit")
    statuses = root.select(".img_txt-wrap > span.flag_kind")
    if title_node is None or len(statuses) != 1:
        raise SeongnamMuseumExperienceContractError("public detail title/status changed")
    title = _clean(title_node.get_text(" ", strip=True))
    source_status = _clean(statuses[0].get_text(" ", strip=True))
    if not title or source_status not in _STATUS_MAP:
        raise SeongnamMuseumExperienceContractError("public detail identity/status invalid")
    fields = _field_pairs(root.select(".txt_box .box_inner > ul > li"), "p.tit", "p.txt")
    if set(fields) != _DETAIL_FIELDS:
        raise SeongnamMuseumExperienceContractError("detail field vocabulary changed")
    controls = root.select(".btn_wrap.type02 a")
    if len(controls) != 1 or _clean(controls[0].get_text(" ", strip=True)) != "교육프로그램 신청하기":
        raise SeongnamMuseumExperienceContractError("public application control changed")
    application_url, application_kind = _application_control(_clean(controls[0].get("href")))
    return _Detail(
        title=title,
        source_status=source_status,
        fields=fields,
        application_url=application_url,
        application_kind=application_kind,
    )


def _list_signature(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        page.empty_placeholder,
        tuple(
            (
                row.identity,
                row.title,
                row.source_status,
                row.venue,
                row.target,
                row.event_period,
                row.apply_period,
                row.list_application_url,
            )
            for row in page.rows
        ),
    )


def _is_experience(row: _ListRow) -> bool:
    lowered = row.title.casefold()
    return not any(marker in lowered for marker in _NON_PROGRAM_TITLE_MARKERS) and any(
        marker in lowered for marker in _EXPERIENCE_TITLE_MARKERS
    )


def _capacity(value: str) -> Optional[int]:
    match = re.fullmatch(r"([\d,]+)\s*명", _clean(value))
    return int(match.group(1).replace(",", "")) if match else None


def _schedule_days(value: str) -> list[str]:
    return [day for day in "월화수목금토일" if f"{day}요일" in value]


def _row_from_detail(listed: _ListRow, detail: _Detail) -> dict[str, Any]:
    if detail.title != listed.title or detail.source_status != listed.source_status:
        raise SeongnamMuseumExperienceContractError(f"detail identity mismatch for {listed.identity}")
    detail_event_period, event_start, event_end = _parse_period(detail.fields["교육기간"], "detail education")
    detail_apply_period, apply_start, apply_end = _parse_period(detail.fields["신청기간"], "detail application")
    if (
        detail_event_period != listed.event_period
        or event_start != listed.event_start
        or event_end != listed.event_end
        or detail_apply_period != listed.apply_period
        or apply_start != listed.apply_start
        or apply_end != listed.apply_end
        or detail.fields["교육대상"] != listed.target
        or detail.fields["교육장소"] != listed.venue
    ):
        raise SeongnamMuseumExperienceContractError(f"detail/list fields disagree for {listed.identity}")
    if listed.list_application_url and listed.list_application_url != detail.application_url:
        raise SeongnamMuseumExperienceContractError(f"application control mismatch for {listed.identity}")
    normalized_status = _STATUS_MAP[listed.source_status]
    actionable_url = detail.application_url if normalized_status == "OPEN" else ""
    row: dict[str, Any] = {
        "provider": SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER,
        "provider_course_id": (f"{SEONGNAM_MUSEUM_EXPERIENCE_PROVIDER}:experience:{listed.identity}"),
        "source_course_id": listed.identity,
        "title": listed.title,
        "branch": SEONGNAM_MUSEUM_EXPERIENCE_BRANCH,
        "branch_code": SEONGNAM_MUSEUM_EXPERIENCE_BRANCH_CODE,
        "branch_url": SEONGNAM_MUSEUM_EXPERIENCE_URL,
        "preserve_branch": True,
        "address": SEONGNAM_MUSEUM_EXPERIENCE_ADDRESS,
        "municipality_code": SEONGNAM_MUSEUM_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_full_name": SEONGNAM_MUSEUM_EXPERIENCE_MUNICIPALITY_NAME,
        "region_sido": "경기도",
        "region_sigungu": "성남시 수정구",
        "category": "성남시박물관 체험·교육프로그램",
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
        "reservation_available": bool(actionable_url),
        "period": listed.event_period,
        "start_date": listed.event_start.isoformat(),
        "end_date": listed.event_end.isoformat(),
        "apply_period": listed.apply_period,
        "apply_start_date": listed.apply_start.isoformat(),
        "apply_end_date": listed.apply_end.isoformat(),
        "schedule_raw": detail.fields["교육시간"],
        "schedule_days": _schedule_days(detail.fields["교육시간"]),
        "target": listed.target,
        "venue_name": listed.venue,
        "venue_address": SEONGNAM_MUSEUM_EXPERIENCE_ADDRESS,
        "fee": 0 if detail.fields["교육비"] == "무료" else detail.fields["교육비"],
        "capacity_total": _capacity(detail.fields["교육인원"]),
        "application_url": actionable_url,
        "application_type": "WEBSITE" if actionable_url else "INFO_ONLY",
        "raw_url": seongnam_museum_experience_detail_url(listed.identity),
        "description": "",
        "image_url": "",
        "raw_fields": {
            "parser": SEONGNAM_MUSEUM_EXPERIENCE_PARSER,
            "official_programme_id": listed.identity,
            "official_source_status": listed.source_status,
            "official_application_control": detail.application_kind,
            "list_page": listed.page,
            "structured_detail_verified": True,
            "free_text_excluded": True,
        },
    }
    return row


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key).casefold()
                child_path = f"{path}.{key}" if path else str(key)
                if key_text in _FORBIDDEN_OUTPUT_KEYS:
                    errors.append(f"forbidden key {child_path}")
                walk(child, child_path)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and (_PHONE_RE.search(value) or _EMAIL_RE.search(value)):
            errors.append(f"contact value in {path}")

    walk(row, "")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def _meta() -> dict[str, Any]:
    return {
        "errors": [],
        "configured_collection_error": "",
        "error_kind": "",
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "pagination_complete": False,
        "details_complete": False,
        "source_cap_reached": False,
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "application_endpoint_requests": 0,
        "reservation_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "auth_endpoint_requests": 0,
        "identity_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "member_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_payload_persisted": False,
        "verified_legacy_tls": True,
    }


def collect_seongnam_museum_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session_factory,
    fetcher: Fetcher = _default_fetcher,
    dedupe_rows: DedupeRows = _dedupe_default,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future snapshot of museum experiences."""

    meta = _meta()
    if not is_seongnam_museum_experience_target(target):
        message = "target does not match the canonical Seongnam Museum programme route"
        meta.update(errors=[message], configured_collection_error=message, error_kind="contract")
        return [], SEONGNAM_MUSEUM_EXPERIENCE_PARSER, meta
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or timeout < 1
        or not isinstance(max_pages, int)
        or isinstance(max_pages, bool)
        or max_pages < 1
        or not isinstance(detail_limit, int)
        or isinstance(detail_limit, bool)
        or detail_limit < 0
    ):
        message = "invalid collection limits"
        meta.update(errors=[message], configured_collection_error=message, error_kind="contract")
        return [], SEONGNAM_MUSEUM_EXPERIENCE_PARSER, meta

    cutoff = _today(today)
    requester = _Requester(session_factory, fetcher, timeout, meta)
    try:
        first = _parse_list_page(requester.soup(seongnam_museum_experience_list_url(1)), 1)
        if first.last > max_pages:
            meta["source_cap_reached"] = True
            raise SeongnamMuseumExperienceContractError("declared pages exceed max_pages")
        pages = [first]
        for page_number in range(2, first.last + 1):
            page = _parse_list_page(
                requester.soup(seongnam_museum_experience_list_url(page_number)),
                page_number,
            )
            if page.total != first.total or page.last != first.last or page.empty_placeholder:
                raise SeongnamMuseumExperienceContractError("declared pagination changed")
            pages.append(page)
        source_rows = [row for page in pages for row in page.rows]
        if len(source_rows) != first.total or len({row.identity for row in source_rows}) != first.total:
            raise SeongnamMuseumExperienceContractError("complete programme identity union changed")

        sentinel_number = first.last + 1
        sentinel = _parse_list_page(
            requester.soup(seongnam_museum_experience_list_url(sentinel_number)),
            sentinel_number,
        )
        if (
            sentinel.total != first.total
            or sentinel.last != first.last
            or sentinel.rows
            or not sentinel.empty_placeholder
        ):
            raise SeongnamMuseumExperienceContractError("post-last page is not exact empty")

        stable_first = _parse_list_page(requester.soup(seongnam_museum_experience_list_url(1)), 1)
        stable_last = (
            stable_first
            if first.last == 1
            else _parse_list_page(
                requester.soup(seongnam_museum_experience_list_url(first.last)),
                first.last,
            )
        )
        stable_sentinel = _parse_list_page(
            requester.soup(seongnam_museum_experience_list_url(sentinel_number)),
            sentinel_number,
        )
        if _list_signature(stable_first) != _list_signature(first):
            raise SeongnamMuseumExperienceContractError("first list page changed during snapshot")
        if _list_signature(stable_last) != _list_signature(pages[-1]):
            raise SeongnamMuseumExperienceContractError("last list page changed during snapshot")
        if _list_signature(stable_sentinel) != _list_signature(sentinel):
            raise SeongnamMuseumExperienceContractError("sentinel page changed during snapshot")

        current = [row for row in source_rows if row.event_end >= cutoff]
        experience = [row for row in current if _is_experience(row)]
        excluded_non_experience = len(current) - len(experience)
        if len(experience) > detail_limit:
            meta["source_cap_reached"] = True
            raise SeongnamMuseumExperienceContractError("detail_limit truncates current experience details")

        output: list[dict[str, Any]] = []
        for listed in experience:
            detail = _parse_detail(requester.soup(seongnam_museum_experience_detail_url(listed.identity)))
            row = _row_from_detail(listed, detail)
            privacy = _privacy_errors(row)
            if privacy:
                raise SeongnamMuseumExperienceContractError("; ".join(privacy))
            output.append(row)
        output = list(dedupe_rows(output))
        if len(output) != len(experience):
            raise SeongnamMuseumExperienceContractError("dedupe changed programme identity cardinality")

        status_counts = Counter(row["status"] for row in output)
        meta.update(
            {
                "source_total": first.total,
                "source_rows": len(source_rows),
                "data_pages": first.last,
                "pages": first.last,
                "sentinel_page": sentinel_number,
                "sentinel_count": 0,
                "current_source_count": len(current),
                "current_count": len(experience),
                "expired_count": len(source_rows) - len(current),
                "excluded_non_experience_count": excluded_non_experience,
                "returned_count": len(output),
                "detail_verified": len(output),
                "detail_pages": len(output),
                "status_counts": dict(status_counts),
                "source_identity_sha256": _identity_hash(row.identity for row in source_rows),
                "output_identity_sha256": _identity_hash(row["provider_course_id"] for row in output),
                "stable_first_page": True,
                "stable_last_page": True,
                "stable_sentinel_page": True,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return output, SEONGNAM_MUSEUM_EXPERIENCE_PARSER, meta
    except Exception as exc:
        message = _clean(exc) or exc.__class__.__name__
        meta["errors"] = [message]
        meta["configured_collection_error"] = message
        meta["error_kind"] = "contract" if isinstance(exc, SeongnamMuseumExperienceContractError) else "request"
        return [], SEONGNAM_MUSEUM_EXPERIENCE_PARSER, meta
    finally:
        requester.close()


collect = collect_seongnam_museum_experience
