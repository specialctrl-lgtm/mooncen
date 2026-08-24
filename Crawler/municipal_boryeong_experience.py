"""Fail-closed collector for Boryeong Wood Culture Center experiences.

The City of Boryeong publishes the reservation-system provenance page while
K-GUIDE serves the public programme ledger linked by that page.  This
collector owns the exact city provenance URL, but requests only K-GUIDE's
public list and detail GET routes.  It never requests the calendar, booking,
login, member, applicant, identity, file, attachment, download, or PII routes.

Completeness is the equality of the declared list total, direct programme
cards, unique card identities, the page's ``g_magic`` identity registry, and
the server-rendered detail slides.  A second complete list GET must be stable.
Each detail slide is also bound to its list identity by position and the exact
operation-period, schedule, and fee tuple.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


BORYEONG_EXPERIENCE_PROVENANCE_URL = "https://www.brcn.go.kr/woodedu/sub04_02.do"
BORYEONG_EXPERIENCE_PROVIDER = "MUNI_WWW_BRCN_GO_KR_4C580A38"
BORYEONG_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_9A45E8615C8B"
BORYEONG_EXPERIENCE_HOST = "www.kguide.kr"
BORYEONG_EXPERIENCE_LIST_PATH = "/svc/list"
BORYEONG_EXPERIENCE_DETAIL_PATH = "/svc/detail"
BORYEONG_EXPERIENCE_COMPANY_CODE = "3138301612"
BORYEONG_EXPERIENCE_SHOP_CODE = "313830161201"
BORYEONG_EXPERIENCE_ROOT = "brwoodcec"
BORYEONG_EXPERIENCE_URL = BORYEONG_EXPERIENCE_PROVENANCE_URL
BORYEONG_EXPERIENCE_MUNICIPALITY_CODE = "4418000000"
BORYEONG_EXPERIENCE_MUNICIPALITY_NAME = "충청남도 보령시"
BORYEONG_EXPERIENCE_BRANCH = "보령목재문화체험장"
BORYEONG_EXPERIENCE_MAX_PROGRAMS = 200
BORYEONG_EXPERIENCE_MAX_HTML_BYTES = 3_000_000
BORYEONG_EXPERIENCE_PARSER = (
    "boryeong_wood_culture_official_provenance+kguide_public_get_only+"
    "declared_cards_unique_ids_g_magic_detail_slides_equal+"
    "stable_complete_list_recheck+position_bound_operation_schedule_fee+"
    "all_detail_hands_on_and_100_percent_reservation_evidence+"
    "locked_experience+venue_only_address_suppressed+"
    "reservation_unavailable_without_calendar_or_application_calls+"
    "no_calendar_application_login_member_applicant_identity_file_attachment_"
    "download_or_pii_calls"
)
BORYEONG_EXPERIENCE_OWNERSHIP_SCOPE = "boryeong_wood_culture_center_current_or_ongoing_hands_on_experience_ledger"

_PAGE_TITLE = "K-GUIDE"
_SERVICE_HEADING = "보령목재문화체험장 예약시스템"
_TOTAL_RE = re.compile(r"([0-9][0-9,]*)\s+Listed")
_IDENTITY_RE = re.compile(r"[1-9]\d{0,11}")
_GO_RE = re.compile(r"javascript:go\('([1-9]\d{0,11})', '([0-9]+)'\)")
_MAGIC_RE = re.compile(r"\bvar\s+g_magic\s*=\s*'([^']*)'\s*;")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_CATEGORIES = frozenset({"유아체험", "일반체험", "심화체험", "기타"})
_STATUS_MAP = {
    "예약중": "OPEN",
    "예약예정": "SCHEDULED",
    "예약마감": "CLOSED",
}
_DETAIL_TABLE_REQUIRED = frozenset({"운영기간", "이용시간", "이용정원", "이용요금"})
_DETAIL_TABLE_ALLOWED = _DETAIL_TABLE_REQUIRED | {"소요시간"}
_DETAIL_INFO_FIELDS = frozenset({"개요", "이용 시 주의사항", "예약 시 주의사항", "모바일 티켓", "환불정책", "이용장소"})
_VENUE_BY_CATEGORY = {
    "유아체험": "보령목재문화체험장 2층 유아체험실 (보령무궁화수목원 내 위치)",
    "일반체험": "보령목재문화체험장 1층 일반체험실 (보령무궁화수목원 내 위치)",
    "심화체험": "보령목재문화체험장 1층 심화체험실 (보령무궁화수목원 내 위치)",
    "기타": "보령목재문화체험장 1층 일반체험실 (보령무궁화수목원 내 위치)",
}
_NON_PROGRAM_MARKERS = (
    "공지",
    "알림",
    "안내사항",
    "채용",
    "입찰",
    "시설대관",
    "시설대여",
    "물품대여",
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_position",
        "source_category",
        "source_status",
        "source_operation_period",
        "source_schedule",
        "source_fee",
        "source_capacity",
        "detail_verified",
        "registry_verified",
        "hands_on_evidence",
        "venue_basis",
        "ongoing_operation",
        "application_endpoint_not_requested",
        "calendar_endpoint_not_requested",
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
        "rounds",
        "remaining",
        "reservation_url",
        "attachment",
        "download_url",
    }
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class BoryeongExperienceContractError(RuntimeError):
    """Raised when the audited public-source contract changes."""


@dataclass(frozen=True)
class _Card:
    identity: str
    position: int
    category: str
    title: str
    source_status: str
    operation: str
    schedule: str
    fee: str


@dataclass(frozen=True)
class _Ledger:
    declared: int
    registry: tuple[str, ...]
    cards: tuple[_Card, ...]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_boryeong_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == BORYEONG_EXPERIENCE_PROVIDER
        and _clean(_target_value(target, "url")) == BORYEONG_EXPERIENCE_URL
    )


is_target = is_boryeong_experience_target


def boryeong_experience_list_url() -> str:
    query = (
        ("company_code", BORYEONG_EXPERIENCE_COMPANY_CODE),
        ("shop_code", BORYEONG_EXPERIENCE_SHOP_CODE),
    )
    return f"https://{BORYEONG_EXPERIENCE_HOST}{BORYEONG_EXPERIENCE_LIST_PATH}?{urlencode(query)}"


def _validated_registry(identities: Iterable[Any]) -> tuple[str, ...]:
    registry = tuple(_clean(value) for value in identities)
    if not registry or len(registry) > BORYEONG_EXPERIENCE_MAX_PROGRAMS:
        raise ValueError("invalid Boryeong experience registry size")
    if any(_IDENTITY_RE.fullmatch(value) is None for value in registry):
        raise ValueError("invalid Boryeong experience identity")
    if len(set(registry)) != len(registry):
        raise ValueError("duplicate Boryeong experience registry identity")
    return registry


def boryeong_experience_detail_url(identities: Iterable[Any], *, position: int = 0) -> str:
    registry = _validated_registry(identities)
    if not isinstance(position, int) or isinstance(position, bool):
        raise ValueError("position must be an integer")
    if position < 0 or position >= len(registry):
        raise ValueError("position is outside the Boryeong experience registry")
    query = (
        ("idx", registry[position]),
        ("ids", ",".join(registry)),
        ("page", str(position)),
        ("root", BORYEONG_EXPERIENCE_ROOT),
    )
    return f"https://{BORYEONG_EXPERIENCE_HOST}{BORYEONG_EXPERIENCE_DETAIL_PATH}?{urlencode(query, safe=',')}"


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
        and parsed.hostname == BORYEONG_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        raise BoryeongExperienceContractError("unsafe K-GUIDE request URL")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(query)
    if parsed.path == BORYEONG_EXPERIENCE_LIST_PATH:
        if (
            len(query) == 2
            and set(values) == {"company_code", "shop_code"}
            and values["company_code"] == BORYEONG_EXPERIENCE_COMPANY_CODE
            and values["shop_code"] == BORYEONG_EXPERIENCE_SHOP_CODE
        ):
            return "list"
    if parsed.path == BORYEONG_EXPERIENCE_DETAIL_PATH:
        if len(query) == 4 and set(values) == {"idx", "ids", "page", "root"}:
            try:
                registry = _validated_registry(values["ids"].split(","))
                position = int(values["page"])
            except (TypeError, ValueError) as exc:
                raise BoryeongExperienceContractError("invalid public detail registry") from exc
            if (
                values["page"] == str(position)
                and 0 <= position < len(registry)
                and values["idx"] == registry[position]
                and values["root"] == BORYEONG_EXPERIENCE_ROOT
            ):
                return "detail"
    raise BoryeongExperienceContractError("request is outside the K-GUIDE public list/detail GET allowlist")


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
            raise BoryeongExperienceContractError(f"unexpected HTTP status {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise BoryeongExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(str(key).lower() == "location" and value for key, value in headers.items()):
            raise BoryeongExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and _canonical_key(final_url) != _canonical_key(url):
            raise BoryeongExperienceContractError("K-GUIDE response URL changed")
        content_type = _clean(
            next(
                (value for key, value in headers.items() if str(key).lower() == "content-type"),
                "text/html",
            )
        ).lower()
        if "html" not in content_type:
            raise BoryeongExperienceContractError("K-GUIDE response is not HTML")
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > BORYEONG_EXPERIENCE_MAX_HTML_BYTES:
            raise BoryeongExperienceContractError("empty or oversized K-GUIDE response")
        soup = BeautifulSoup(body, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        headings = [_clean(node.get_text(" ", strip=True)) for node in soup.select("h1")]
        if title != _PAGE_TITLE or headings.count(_SERVICE_HEADING) != 1:
            raise BoryeongExperienceContractError("K-GUIDE service ownership heading changed")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _parse_registry(soup: BeautifulSoup) -> tuple[str, ...]:
    matches: list[str] = []
    for script in soup.find_all("script"):
        text = script.string or script.get_text(" ", strip=False)
        matches.extend(_MAGIC_RE.findall(text or ""))
    if len(matches) != 1:
        raise BoryeongExperienceContractError("g_magic registry declaration changed")
    try:
        return _validated_registry(matches[0].split(","))
    except ValueError as exc:
        raise BoryeongExperienceContractError("invalid g_magic registry") from exc


def _safe_text(value: str, context: str) -> str:
    text = _clean(value)
    if not text:
        raise BoryeongExperienceContractError(f"empty {context}")
    if _PHONE_RE.search(text) or _EMAIL_RE.search(text):
        raise BoryeongExperienceContractError(f"PII entered safe {context}")
    return text


def _parse_list(soup: BeautifulSoup) -> _Ledger:
    totals = soup.select("div.num")
    if len(totals) != 1:
        raise BoryeongExperienceContractError("declared list total control changed")
    match = _TOTAL_RE.fullmatch(_clean(totals[0].get_text(" ", strip=True)))
    if match is None:
        raise BoryeongExperienceContractError("declared list total changed")
    declared = int(match.group(1).replace(",", ""))
    if declared < 1 or declared > BORYEONG_EXPERIENCE_MAX_PROGRAMS:
        raise BoryeongExperienceContractError("declared list total is outside safety cap")

    roots = soup.select("div.pg_list")
    if len(roots) != 1:
        raise BoryeongExperienceContractError("programme-list root changed")
    direct_lists = roots[0].find_all("ul", recursive=False)
    if len(direct_lists) != 1:
        raise BoryeongExperienceContractError("programme-list container changed")
    cards: list[_Card] = []
    for expected_position, node in enumerate(direct_lists[0].find_all("li", recursive=False)):
        anchors = node.find_all("a", recursive=False)
        if len(anchors) != 1:
            raise BoryeongExperienceContractError("programme-card action changed")
        href = _clean(anchors[0].get("href"))
        go = _GO_RE.fullmatch(href)
        category_node = node.select_one(".thum_img > .sort")
        title_node = node.select_one(".pg_info > .pg_tit")
        status_node = node.select_one(".pg_info > .status")
        info_lists = node.select(".pg_info > ul")
        if go is None or not all((category_node, title_node, status_node)):
            raise BoryeongExperienceContractError("programme-card shape changed")
        if len(info_lists) != 1:
            raise BoryeongExperienceContractError("programme-card summary changed")
        summaries = info_lists[0].find_all("li", recursive=False)
        if len(summaries) != 3:
            raise BoryeongExperienceContractError("programme-card operation/schedule/fee tuple changed")
        identity, position_text = go.groups()
        if int(position_text) != expected_position:
            raise BoryeongExperienceContractError("programme-card registry position changed")
        category = _safe_text(category_node.get_text(" ", strip=True), "source category")
        title = _safe_text(title_node.get_text(" ", strip=True), "programme title")
        source_status = _safe_text(status_node.get_text(" ", strip=True), "source status")
        if category not in _CATEGORIES:
            raise BoryeongExperienceContractError(f"{identity}: unknown experience category {category!r}")
        if source_status not in _STATUS_MAP:
            raise BoryeongExperienceContractError(f"{identity}: unknown source status {source_status!r}")
        if any(marker in title for marker in _NON_PROGRAM_MARKERS):
            raise BoryeongExperienceContractError(f"{identity}: notice/information shell entered programme ledger")
        if not any(marker in title for marker in ("체험", "만들기", "목공")):
            raise BoryeongExperienceContractError(f"{identity}: title does not identify a hands-on programme")
        operation, schedule, fee = (
            _safe_text(value.get_text(" ", strip=True), "programme summary") for value in summaries
        )
        cards.append(
            _Card(
                identity,
                expected_position,
                category,
                title,
                source_status,
                operation,
                schedule,
                fee,
            )
        )

    registry = _parse_registry(soup)
    identities = tuple(card.identity for card in cards)
    if not (declared == len(cards) == len(set(identities)) == len(registry) and identities == registry):
        raise BoryeongExperienceContractError("declared total, cards, unique identities, and g_magic registry differ")
    return _Ledger(declared, registry, tuple(cards))


def _ledger_signature(ledger: _Ledger) -> tuple[Any, ...]:
    return (
        ledger.declared,
        ledger.registry,
        tuple(
            (
                card.identity,
                card.position,
                card.category,
                card.title,
                card.source_status,
                card.operation,
                card.schedule,
                card.fee,
            )
            for card in ledger.cards
        ),
    )


def _table_fields(slide: Any, identity: str) -> dict[str, str]:
    roots = slide.select(":scope > .program_detail")
    if len(roots) != 1:
        raise BoryeongExperienceContractError(f"{identity}: detail programme table changed")
    fields: dict[str, str] = {}
    for row in roots[0].select("tr"):
        heading = row.find("th", recursive=False)
        value = row.find("td", recursive=False)
        if heading is None or value is None:
            raise BoryeongExperienceContractError(f"{identity}: malformed detail table row")
        name = _clean(heading.get_text(" ", strip=True))
        if name not in _DETAIL_TABLE_ALLOWED or name in fields:
            raise BoryeongExperienceContractError(f"{identity}: detail table field contract changed")
        fields[name] = _safe_text(value.get_text(" ", strip=True), f"{identity} detail {name}")
    if not _DETAIL_TABLE_REQUIRED <= set(fields) or not set(fields) <= _DETAIL_TABLE_ALLOWED:
        raise BoryeongExperienceContractError(f"{identity}: required detail table fields changed")
    return fields


def _info_fields(slide: Any, identity: str) -> dict[str, str]:
    roots = slide.select(":scope > .program_info")
    if len(roots) != 1:
        raise BoryeongExperienceContractError(f"{identity}: detail information root changed")
    fields: dict[str, str] = {}
    for heading in roots[0].select(":scope > p.tit_h3"):
        name = _clean(heading.get_text(" ", strip=True))
        if name not in _DETAIL_INFO_FIELDS or name in fields:
            raise BoryeongExperienceContractError(f"{identity}: detail information field contract changed")
        value = heading.find_next_sibling("div", class_="con_text")
        if value is None:
            raise BoryeongExperienceContractError(f"{identity}: detail information value changed")
        fields[name] = _clean(value.get_text(" ", strip=True))
    if set(fields) != _DETAIL_INFO_FIELDS:
        raise BoryeongExperienceContractError(f"{identity}: required detail information fields changed")
    return fields


def _hands_on_evidence(category: str, overview: str, identity: str) -> str:
    marker = "2. 프로그램 소개"
    if marker not in overview:
        raise BoryeongExperienceContractError(f"{identity}: programme-introduction marker changed")
    introduction = overview.split(marker, 1)[1].split("3.", 1)[0].strip()
    if not introduction or any(value in introduction for value in _NON_PROGRAM_MARKERS):
        raise BoryeongExperienceContractError(f"{identity}: programme introduction is not a hands-on programme")
    required = {
        "유아체험": (("만들기",), ("나무",), ("체험",)),
        "일반체험": (("나무",), ("공구",), ("제작",), ("체험",)),
        "심화체험": (("수공구", "목공기계"), ("제작",), ("체험",)),
        "기타": (("CNC",), ("코딩",), ("목공",), ("체험",)),
    }[category]
    if any(not any(value in introduction for value in alternatives) for alternatives in required):
        raise BoryeongExperienceContractError(f"{identity}: category-specific hands-on evidence changed")
    return f"{category}:프로그램 소개 직접 만들기·제작·목공 체험 검증"


def _row_from_slide(card: _Card, slide: Any) -> dict[str, Any]:
    table = _table_fields(slide, card.identity)
    info = _info_fields(slide, card.identity)
    detail_schedule = table["이용시간"]
    if "소요시간" in table:
        detail_schedule = f"{detail_schedule} ({table['소요시간']})"
    if (table["운영기간"], detail_schedule, table["이용요금"]) != (
        card.operation,
        card.schedule,
        card.fee,
    ):
        raise BoryeongExperienceContractError(f"{card.identity}: list/detail operation schedule fee identity drift")
    venue = _safe_text(info["이용장소"], f"{card.identity} venue")
    expected_venue = _VENUE_BY_CATEGORY[card.category]
    if venue != expected_venue:
        raise BoryeongExperienceContractError(f"{card.identity}: category/detail venue contract changed")
    if "100% 예약제" not in info["이용 시 주의사항"]:
        raise BoryeongExperienceContractError(f"{card.identity}: ongoing reservation evidence changed")
    hands_on = _hands_on_evidence(card.category, info["개요"], card.identity)
    normalized_status = _STATUS_MAP[card.source_status]
    single_url = boryeong_experience_detail_url((card.identity,))
    return {
        "provider": BORYEONG_EXPERIENCE_PROVIDER,
        "provider_course_id": (f"{BORYEONG_EXPERIENCE_PROVIDER}:experience:{card.identity}"),
        "prefer_incoming_provider_course_id": True,
        "title": card.title,
        "description": card.title,
        "branch": BORYEONG_EXPERIENCE_BRANCH,
        "branch_code": "BORYEONG_WOOD_CULTURE_CENTER",
        "preserve_branch": True,
        "category": card.category,
        "program_type": "목공 체험",
        "raw_url": single_url,
        "application_url": "",
        "application_type": "INFO_ONLY",
        "application_method": "예약 정보만 제공",
        "reservation_available": False,
        "status": normalized_status,
        "fee": table["이용요금"],
        "period": table["운영기간"],
        "start_date": "",
        "end_date": "",
        "apply_period": "",
        "schedule_raw": table["이용시간"],
        "capacity": table["이용정원"],
        "target": "",
        "venue": venue,
        "venue_name": venue,
        "address": "",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "collection_type": BORYEONG_EXPERIENCE_PARSER,
        "municipality_code": BORYEONG_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": BORYEONG_EXPERIENCE_MUNICIPALITY_NAME,
        "municipality_full_name": BORYEONG_EXPERIENCE_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": card.identity,
            "list_position": card.position,
            "source_category": card.category,
            "source_status": card.source_status,
            "source_operation_period": table["운영기간"],
            "source_schedule": table["이용시간"],
            "source_fee": table["이용요금"],
            "source_capacity": table["이용정원"],
            "detail_verified": True,
            "registry_verified": True,
            "hands_on_evidence": hands_on,
            "venue_basis": "K-GUIDE identity-bound public detail 이용장소",
            "ongoing_operation": True,
            "application_endpoint_not_requested": True,
            "calendar_endpoint_not_requested": True,
            "service_family": "experience",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden reservation/detail key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw field allowlist exceeded")
    if row.get("application_url") or row.get("reservation_available") is not False:
        errors.append("reservation exposure contract changed")
    if row.get("address") != "":
        errors.append("conflicting source address was persisted")
    payload = repr(row)
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("contact data persisted")
    return errors


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        re.sub(r"[^0-9a-z가-힣]+", "", _clean(row.get("title")).casefold()),
        _clean(row.get("venue_name")),
        _clean(row.get("raw_fields", {}).get("source_schedule")),
    )


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise BoryeongExperienceContractError("invalid audit date") from exc


def collect_boryeong_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 2,
    detail_limit: int = BORYEONG_EXPERIENCE_MAX_PROGRAMS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/ongoing Boryeong experience snapshot."""

    meta: dict[str, Any] = {
        "municipality_code": BORYEONG_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": BORYEONG_EXPERIENCE_MUNICIPALITY_NAME,
        "owner_provider": BORYEONG_EXPERIENCE_PROVIDER,
        "candidate_id": BORYEONG_EXPERIENCE_CANDIDATE_ID,
        "official_provenance_url": BORYEONG_EXPERIENCE_PROVENANCE_URL,
        "public_data_url": boryeong_experience_list_url(),
        "parser": BORYEONG_EXPERIENCE_PARSER,
        "ownership_scope": BORYEONG_EXPERIENCE_OWNERSHIP_SCOPE,
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "official_provenance_requests": 0,
        "calendar_endpoint_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "member_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "identity_endpoint_requests": 0,
        "file_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pagination_complete": False,
        "registry_complete": False,
        "details_complete": False,
        "stable_recheck_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "errors": [],
        "configured_collection_error": "",
    }
    requester: Optional[_Requester] = None
    try:
        meta["cutoff"] = _audit_date(today).isoformat()
        if not is_boryeong_experience_target(target):
            raise BoryeongExperienceContractError("target is not the canonical Boryeong experience owner")
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise BoryeongExperienceContractError("invalid collector limits")
        requester = _Requester(
            session_factory or _default_session,
            fetcher or _default_fetcher,
            timeout,
            meta,
        )
        first = _parse_list(requester.soup(boryeong_experience_list_url()))
        meta["declared_total"] = first.declared
        meta["source_total"] = len(first.cards)
        meta["unique_identity_count"] = len(set(first.registry))
        meta["registry_count"] = len(first.registry)
        meta["identity_sha256"] = hashlib.sha256("|".join(first.registry).encode("utf-8")).hexdigest()
        meta["category_counts"] = dict(sorted(Counter(card.category for card in first.cards).items()))
        meta["pagination_mode"] = "single_declared_registry_no_pagination"
        meta["pagination_complete"] = True
        meta["registry_complete"] = True
        if detail_limit < first.declared:
            meta["source_cap_reached"] = True
            raise BoryeongExperienceContractError(
                f"detail_limit {detail_limit} truncates required registry {first.declared}"
            )

        detail_url = boryeong_experience_detail_url(first.registry)
        detail_soup = requester.soup(detail_url)
        slides = detail_soup.select("div.swiper-slide")
        meta["detail_slide_count"] = len(slides)
        if len(slides) != first.declared:
            raise BoryeongExperienceContractError("declared total, identity registry, and detail slide count differ")
        all_rows = [_row_from_slide(card, slide) for card, slide in zip(first.cards, slides, strict=True)]
        meta["detail_verified"] = len(all_rows)
        meta["details_complete"] = len(all_rows) == first.declared

        stable = _parse_list(requester.soup(boryeong_experience_list_url()))
        if _ledger_signature(stable) != _ledger_signature(first):
            raise BoryeongExperienceContractError("complete K-GUIDE list changed during stable recheck")
        meta["stable_recheck_complete"] = True

        errors = [error for row in all_rows for error in _privacy_errors(row)]
        if errors:
            raise BoryeongExperienceContractError("; ".join(sorted(set(errors))))
        provider_ids = [str(row["provider_course_id"]) for row in all_rows]
        meta["duplicate_count"] = len(provider_ids) - len(set(provider_ids))
        semantic_counts = Counter(_semantic_signature(row) for row in all_rows)
        meta["semantic_duplicate_count"] = sum(count - 1 for count in semantic_counts.values() if count > 1)
        if meta["duplicate_count"] or meta["semantic_duplicate_count"]:
            raise BoryeongExperienceContractError("duplicate programme identity entered complete registry")
        current_rows = [row for row in all_rows if row["status"] != "CLOSED"]
        if dedupe_rows is not None:
            deduped = list(dedupe_rows(current_rows))
            if len(deduped) != len(current_rows):
                raise BoryeongExperienceContractError("external dedupe removed complete identity-verified rows")
            current_rows = deduped
        meta["current_or_ongoing_count"] = len(current_rows)
        meta["closed_count"] = len(all_rows) - len(current_rows)
        meta["returned_count"] = len(current_rows)
        meta["normalized_status_counts"] = dict(sorted(Counter(str(row["status"]) for row in current_rows).items()))
        meta["municipality_counts"] = {BORYEONG_EXPERIENCE_MUNICIPALITY_CODE: len(current_rows)}
        meta["reservation_available_count"] = 0
        meta["snapshot_complete"] = bool(
            meta["pagination_complete"]
            and meta["registry_complete"]
            and meta["details_complete"]
            and meta["stable_recheck_complete"]
            and meta["duplicate_count"] == 0
            and meta["semantic_duplicate_count"] == 0
        )
        meta["full_snapshot_validated"] = meta["snapshot_complete"]
        meta["no_current_data"] = meta["snapshot_complete"] and not current_rows
        return current_rows, BORYEONG_EXPERIENCE_PARSER, meta
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        meta["errors"] = [message]
        meta["configured_collection_error"] = message
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], BORYEONG_EXPERIENCE_PARSER, meta
    finally:
        if requester is not None:
            requester.close()


collect = collect_boryeong_experience


__all__ = [
    "BORYEONG_EXPERIENCE_CANDIDATE_ID",
    "BORYEONG_EXPERIENCE_PARSER",
    "BORYEONG_EXPERIENCE_PROVENANCE_URL",
    "BORYEONG_EXPERIENCE_PROVIDER",
    "BORYEONG_EXPERIENCE_URL",
    "boryeong_experience_detail_url",
    "boryeong_experience_list_url",
    "collect_boryeong_experience",
    "is_boryeong_experience_target",
]
