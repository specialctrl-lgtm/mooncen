"""Fail-closed collector for Shinan Family Center's course catalogue.

The one registered Shinan discovery candidate is not a municipal course
owner.  It is the provincial JNTLE secondary aggregate, and its current
Shinan filter mixes County, Family Center, museum and agricultural sources.
The current identity-bearing application ledger is instead the independently
owned Shinan Family Center catalogue.

The Family Center pager currently understates its own boundary: it advertises
three pages while a fourth data page is reachable.  This collector therefore
walks consecutive pages through a twice-confirmed empty sentinel, then
rechecks the first and last data pages.  Every current/future row is verified
against the CSRF-protected detail JSON API and the login-gated application
control.  The applicant modal and login endpoint are never requested.

Only allowlisted course facts are emitted.  Images, attachments, free-form
programme bodies, contacts, member state, CSRF/session values, application
payloads and source HTML are discarded.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import json
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


SHINAN_FAMILY_PROVIDER = "MUNI_SHINAN_FAMILYNET_OR_KR_EEF98418"
SHINAN_JNTLE_CANDIDATE_ID = "MUNI_IR_0D249B7F6BB8"
SHINAN_JNTLE_PROVIDER = "MUNI_WWW_JNTLE_KR_AF261C0C"
SHINAN_MUNICIPALITY_CODE = "1287000000"
SHINAN_MUNICIPALITY_NAME = "전남광주통합특별시 신안군"
SHINAN_FAMILY_BRANCH = "신안군 가족센터"

SHINAN_FAMILY_HOST = "shinan.familynet.or.kr"
SHINAN_FAMILY_LIST_PATH = (
    "/center/lay1/program/S295T322C451/recruitReceipt/list.do"
)
SHINAN_FAMILY_DETAIL_PATH = (
    "/center/lay1/program/S295T322C451/recruitReceipt/view.do"
)
SHINAN_FAMILY_VIEW_API_PATH = "/recruitReceipt/getView.do"
SHINAN_FAMILY_LOGIN_PATH = (
    "/center/lay4/program/S295T409C410/member/login.do"
)
SHINAN_FAMILY_LIST_URL = (
    f"https://{SHINAN_FAMILY_HOST}{SHINAN_FAMILY_LIST_PATH}"
)
SHINAN_FAMILY_VIEW_API_URL = (
    f"https://{SHINAN_FAMILY_HOST}{SHINAN_FAMILY_VIEW_API_PATH}"
)

SHINAN_JNTLE_URL = (
    "https://www.jntle.kr/main/uDamoaLecture/1?queryType=4691"
)
SHINAN_MUSEUM_EDUCATION_URL = (
    "https://www.shinan.go.kr/home/museum/education/education_02"
)
SHINAN_AGRICULTURAL_EDUCATION_URL = (
    "https://www.shinan.go.kr/home/jares/community/community_04/page.wscms"
)
SHINAN_AGRICULTURAL_NOTICE_URL = (
    "https://www.shinan.go.kr/home/jares/community/community_01/page.wscms"
)
SHINAN_LIBRARY_URL = "https://www.shinan.go.kr/home/library/"
SHINAN_LIBRARY_EVENTS_URL = (
    "https://www.shinan.go.kr/home/library/menu5/menu5_01/page.wscms"
)
SHINAN_RETIRED_IT_EDUCATION_URLS = (
    "https://www.shinan.go.kr/home/www/takepart/takepart_11/page.wscms",
    (
        "https://www.shinan.go.kr/home/www/takepart/takepart_11/"
        "takepart_01_04/page.wscms"
    ),
)

SHINAN_PAGE_SIZE = 5
SHINAN_MAX_PAGES = 40
SHINAN_MAX_WORKERS = 6
SHINAN_MAX_HTML_BYTES = 2_000_000
SHINAN_MAX_JSON_BYTES = 1_000_000
SHINAN_PARSER = (
    "shinan_family_center_complete_catalogue+walk_to_double_empty_sentinel+"
    "stable_first_last+current_detail_json+identity_bound_login_gate+"
    "pii_allowlist"
)

SHINAN_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    SHINAN_JNTLE_CANDIDATE_ID: {
        "provider": SHINAN_JNTLE_PROVIDER,
        "url": SHINAN_JNTLE_URL,
        "decision": "exclude_provincial_secondary_aggregate",
        "owner": "(재)전남인재평생교육진흥원",
        "reason": (
            "46 ended rows mix four owners and nine rows have a false detail link"
        ),
    }
}

SHINAN_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    SHINAN_FAMILY_PROVIDER: {
        "decision": "new_separate_family_center_course_owner",
        "exact_branch": SHINAN_FAMILY_BRANCH,
        "catalogues": (SHINAN_FAMILY_LIST_URL,),
    },
    "CULTURE_ART_MUSEUM_6E895697A3": {
        "decision": "keep_separate_museum_facility_owner",
        "exact_branch": "저녁노을미술관",
        "catalogues": (SHINAN_MUSEUM_EDUCATION_URL,),
    },
    "shinan_county": {
        "decision": "keep_separate_county_notice_owner",
        "exact_branch": "신안군청",
        "catalogues": (),
    },
    "shinan_agricultural_extension": {
        "decision": "keep_separate_agricultural_notice_owner",
        "exact_branch": "신안군 농업기술센터",
        "catalogues": (),
    },
    "shinan_library": {
        "decision": "keep_separate_library_owner_without_current_catalogue",
        "exact_branch": "신안군립도서관",
        "catalogues": (),
    },
}

SHINAN_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "coverage_state": "review",
    "coverage_candidate_count": 4,
    "coverage_eligible_candidate_count": 1,
    "coverage_excluded_candidate_count": 3,
    "jntle_total": 46,
    "jntle_page_counts": [15, 15, 15, 1],
    "jntle_empty_sentinel_page": 5,
    "jntle_status_counts": {"종료": 46},
    "jntle_current_or_future": 0,
    "jntle_link_owner_counts": {
        "shinan.familynet.or.kr": 21,
        "www.shinan.go.kr": 13,
        "www.jntle.kr/false": 9,
        "jares.shinan.go.kr_stale_hostname": 3,
    },
    "family_center_canonical_url": SHINAN_FAMILY_LIST_URL,
    "family_center_total": 18,
    "family_center_page_counts": [5, 5, 5, 3],
    "family_center_declared_pager_max": 3,
    "family_center_observed_data_pages": 4,
    "family_center_empty_sentinel_page": 5,
    "family_center_status_counts": {"접수중": 18},
    "family_center_current_or_future": 11,
    "family_center_ended_but_still_open_quarantined": 7,
    "museum_total": 22,
    "museum_page_counts": [15, 7],
    "museum_empty_sentinel_page": 3,
    "museum_current_or_future": 1,
    "museum_current_application_control": "꿈길/이메일 안내; 직접 제어 없음",
    "agricultural_dedicated_board_total": 255,
    "agricultural_dedicated_board_pages": 17,
    "agricultural_dedicated_board_latest_date": "2022-11-21",
    "agricultural_dedicated_board_current_or_future": 0,
    "agricultural_current_posts_location": SHINAN_AGRICULTURAL_NOTICE_URL,
    "library_current_course_rows": 0,
    "library_events_calendar_rows": 0,
    "retired_it_education_routes_are_not_found": True,
    "conclusion": (
        "exclude JNTLE as secondary provenance; schedule the independently "
        "owned Family Center ledger and retain museum, agriculture, library "
        "and county notices outside its owner boundary"
    ),
}

SHINAN_PII_FIELDS_DISCARDED = (
    "programme body and guide text",
    "phone/email/contact/staff data",
    "images and image alternative-text payloads",
    "attachments and download URLs",
    "member/application state",
    "CSRF and session identifiers",
    "login and applicant form payloads",
    "source HTML and raw JSON",
)

_STATUS_MAP: Mapping[str, tuple[str, str]] = {
    "접수중": ("OPEN", "c0"),
    "접수예정": ("SCHEDULED", "c1"),
    "접수마감": ("CLOSED", "c2"),
    "진행중": ("CLOSED", "c3"),
    "완료": ("CLOSED", "c4"),
}

_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"^[1-9]\d*$")
_SEND_RE = re.compile(
    r"^\s*send\(\s*'(?P<identity>[1-9]\d*)'\s*,.*,"
    r"\s*'(?P<fork>web|center)'\s*\)\s*;?\s*$",
    re.DOTALL,
)
_DATE_RANGE_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_DATETIME_RANGE_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})$"
)
_EPISODE_DATETIME_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})$"
)
_ROUNDS_RE = re.compile(r"^총\s*(?P<count>[1-9]\d*)회$")
_CSRF_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[\s().-]*\d{3,4}[\s.-]*\d{4}|"
    r"0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_ALLOWED_ROW_KEYS = frozenset(
    {
        "provider",
        "provider_course_id",
        "prefer_incoming_provider_course_id",
        "title",
        "description",
        "branch",
        "branch_code",
        "preserve_branch",
        "category",
        "program_type",
        "raw_url",
        "application_url",
        "application_type",
        "application_method_raw",
        "reservation_available",
        "status",
        "fee",
        "period",
        "start_date",
        "end_date",
        "apply_period",
        "apply_start_date",
        "apply_end_date",
        "schedule_raw",
        "target",
        "capacity_current",
        "capacity_total",
        "capacity_wait_total",
        "venue_name",
        "collection_category",
        "domain_category",
        "operator_type",
        "source_group",
        "service_group",
        "collection_type",
        "municipality_code",
        "municipality_name",
        "raw_fields",
    }
)
_ALLOWED_RAW_KEYS = frozenset(
    {
        "parser",
        "source_identity",
        "source_page",
        "source_status",
        "source_list_event_period",
        "source_api_event_period",
        "source_episode_count",
        "list_period_projection_verified",
        "schedule_evidence",
        "fee_evidence",
        "detail_verified",
        "detail_api_verified",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "staff",
        "member",
        "csrf",
        "session",
        "attachment",
        "attachments",
        "images",
        "program_body",
        "program_detail",
        "source_html",
        "raw_html",
        "raw_json",
        "application_payload",
        "applicant",
    }
)


class ShinanContractError(ValueError):
    """Raised when the live Family Center source violates its contract."""


@dataclass(frozen=True)
class _ListPage:
    page: int
    rows: tuple[dict[str, Any], ...]
    declared_pager_max: int
    empty_marker: bool


SessionFactory = Callable[[], Any]
HtmlFetcher = Callable[[Any, str, int], Any]
JsonFetcher = Callable[[Any, str, str, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _safe_port(parsed: Any) -> Optional[int]:
    try:
        return parsed.port
    except ValueError:
        return -1


def _validate_public_url(
    value: str,
    *,
    path: str,
    query_keys: frozenset[str],
) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != SHINAN_FAMILY_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.fragment
        or parsed.path != path
        or frozenset(parse_qs(parsed.query, keep_blank_values=True)) != query_keys
    ):
        raise ShinanContractError(f"non-canonical Family Center URL: {value!r}")
    return parsed.geturl()


def shinan_family_list_url(page: int) -> str:
    if not isinstance(page, int) or page < 1:
        raise ShinanContractError(f"invalid list page: {page!r}")
    return f"{SHINAN_FAMILY_LIST_URL}?{urlencode({'rows': SHINAN_PAGE_SIZE, 'cpage': page})}"


def shinan_family_detail_url(identity: str) -> str:
    identity = _clean(identity)
    if not _POSITIVE_ID_RE.fullmatch(identity):
        raise ShinanContractError(f"invalid Family Center identity: {identity!r}")
    return (
        f"https://{SHINAN_FAMILY_HOST}{SHINAN_FAMILY_DETAIL_PATH}?"
        f"{urlencode({'seq': identity})}"
    )


def _new_session() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; Mooncen-Shinan-Audit/1.0)",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_html_fetcher(current: Any, url: str, timeout: int) -> Any:
    return current.get(url, timeout=timeout, allow_redirects=False)


def _default_json_fetcher(
    current: Any,
    url: str,
    identity: str,
    csrf: str,
    timeout: int,
) -> Any:
    return current.post(
        url,
        json={"seq": identity},
        headers={
            "X-CSRF-TOKEN": csrf,
            "Origin": f"https://{SHINAN_FAMILY_HOST}",
            "Referer": shinan_family_detail_url(identity),
        },
        timeout=timeout,
        allow_redirects=False,
    )


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", {}) or {}
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return _clean(value)
    return ""


def _response_bytes(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, bytearray):
        return bytes(content)
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text.encode("utf-8")
    return b""


def _validate_transport(
    response: Any,
    expected_url: str,
    *,
    content_type: str,
    max_bytes: int,
) -> bytes:
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise ShinanContractError(
            f"{expected_url}: HTTP {getattr(response, 'status_code', None)!r}"
        )
    if tuple(getattr(response, "history", ()) or ()):
        raise ShinanContractError(f"{expected_url}: redirects are not permitted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url != expected_url:
        raise ShinanContractError(
            f"{expected_url}: unexpected final URL {final_url!r}"
        )
    actual_type = _header(response, "Content-Type").lower()
    if content_type not in actual_type:
        raise ShinanContractError(
            f"{expected_url}: unexpected Content-Type {actual_type!r}"
        )
    body = _response_bytes(response)
    if not body or len(body) > max_bytes:
        raise ShinanContractError(
            f"{expected_url}: invalid body size {len(body)}"
        )
    return body


def _fetch_soup(
    current: Any,
    url: str,
    timeout: int,
    fetcher: HtmlFetcher,
) -> BeautifulSoup:
    body = _validate_transport(
        fetcher(current, url, timeout),
        url,
        content_type="text/html",
        max_bytes=SHINAN_MAX_HTML_BYTES,
    )
    try:
        text = body.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ShinanContractError(f"{url}: invalid UTF-8 HTML") from exc
    return BeautifulSoup(text, "html.parser")


def _fetch_json(
    current: Any,
    identity: str,
    csrf: str,
    timeout: int,
    fetcher: JsonFetcher,
) -> Mapping[str, Any]:
    response = fetcher(
        current,
        SHINAN_FAMILY_VIEW_API_URL,
        identity,
        csrf,
        timeout,
    )
    body = _validate_transport(
        response,
        SHINAN_FAMILY_VIEW_API_URL,
        content_type="application/json",
        max_bytes=SHINAN_MAX_JSON_BYTES,
    )
    try:
        payload = json.loads(body.decode("utf-8-sig", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShinanContractError(f"detail {identity}: invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ShinanContractError(f"detail {identity}: JSON root is not an object")
    return payload


def _parse_date_range(value: str, *, identity: str) -> tuple[date, date]:
    match = _DATE_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise ShinanContractError(f"course {identity}: invalid event period {value!r}")
    start = date.fromisoformat(match.group("start"))
    end = date.fromisoformat(match.group("end"))
    if start > end:
        raise ShinanContractError(f"course {identity}: reversed event period")
    return start, end


def _parse_datetime_range(
    value: str,
    *,
    identity: str,
) -> tuple[datetime, datetime]:
    match = _DATETIME_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise ShinanContractError(
            f"course {identity}: invalid reception period {value!r}"
        )
    start = datetime.strptime(match.group("start"), "%Y-%m-%d %H:%M")
    end = datetime.strptime(match.group("end"), "%Y-%m-%d %H:%M")
    if start > end:
        raise ShinanContractError(f"course {identity}: reversed reception period")
    return start, end


def _parse_send(anchor: Any, expected_fork: str, *, label: str) -> str:
    if anchor is None:
        raise ShinanContractError(f"{label}: missing identity-bound control")
    match = _SEND_RE.fullmatch(_clean(anchor.get("onclick")))
    if not match or match.group("fork") != expected_fork:
        raise ShinanContractError(f"{label}: invalid send() control")
    return match.group("identity")


def _parse_card(card: Any, page: int) -> dict[str, Any]:
    title_anchor = card.select_one(".txt > .tit a[onclick]")
    identity = _parse_send(title_anchor, "web", label=f"page {page} title")
    title = _clean(title_anchor.get_text(" ", strip=True))
    if not title or len(title) > 300 or _PHONE_RE.search(title) or _EMAIL_RE.search(title):
        raise ShinanContractError(f"course {identity}: invalid title")

    fields: dict[str, str] = {}
    for paragraph in card.select(".txt > ul > li p"):
        label_node = paragraph.find("b")
        if label_node is None:
            continue
        label = _clean(label_node.get_text(" ", strip=True))
        whole = _clean(paragraph.get_text(" ", strip=True))
        value = _clean(whole[len(label) :]) if whole.startswith(label) else ""
        if label == "진행장소" and value.endswith("오시는길"):
            value = _clean(value[: -len("오시는길")])
        if label in fields or label not in {"회차정보", "행사기간", "접수기간", "진행장소"}:
            raise ShinanContractError(f"course {identity}: unexpected/duplicate field {label!r}")
        fields[label] = value
    if frozenset(fields) != {"회차정보", "행사기간", "접수기간", "진행장소"}:
        raise ShinanContractError(f"course {identity}: incomplete list fields")

    rounds_match = _ROUNDS_RE.fullmatch(fields["회차정보"])
    if not rounds_match:
        raise ShinanContractError(f"course {identity}: invalid round count")
    rounds = int(rounds_match.group("count"))
    event_start, event_end = _parse_date_range(fields["행사기간"], identity=identity)
    apply_start, apply_end = _parse_datetime_range(
        fields["접수기간"], identity=identity
    )
    venue = fields["진행장소"]
    if not venue or len(venue) > 500 or _PHONE_RE.search(venue) or _EMAIL_RE.search(venue):
        raise ShinanContractError(f"course {identity}: invalid venue")

    region_node = card.select_one(".util > .loc")
    region = _clean(region_node.get_text(" ", strip=True) if region_node else "")
    if region != "전남광주 > 신안군":
        raise ShinanContractError(f"course {identity}: wrong owner region {region!r}")
    state = card.select_one(".util > .state")
    status_node = state.find("span") if state else None
    source_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
    if source_status not in _STATUS_MAP:
        raise ShinanContractError(f"course {identity}: unknown status {source_status!r}")
    status, expected_class = _STATUS_MAP[source_status]
    classes = set(status_node.get("class", ()))
    if classes != {expected_class}:
        raise ShinanContractError(f"course {identity}: status class mismatch")

    apply_anchor = state.find("a", string=lambda value: _clean(value) == "신청하기") if state else None
    control_identity = ""
    if apply_anchor is not None:
        control_identity = _parse_send(
            apply_anchor,
            "center",
            label=f"course {identity} application",
        )
        if control_identity != identity:
            raise ShinanContractError(f"course {identity}: application identity mismatch")
    if status == "OPEN" and not control_identity:
        raise ShinanContractError(f"course {identity}: OPEN control missing")

    return {
        "identity": identity,
        "title": title,
        "page": page,
        "rounds": rounds,
        "event_start": event_start,
        "event_end": event_end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "venue": venue,
        "source_status": source_status,
        "status": status,
        "application_control": bool(control_identity),
        "raw_url": shinan_family_detail_url(identity),
    }


def _parse_list_page(soup: BeautifulSoup, page: int) -> _ListPage:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "신안군 가족센터>프로그램안내>프로그램신청":
        raise ShinanContractError(f"page {page}: wrong list title {title!r}")
    form = soup.select_one("form#searchForm")
    if form is None:
        raise ShinanContractError(f"page {page}: search form missing")
    action = urljoin(SHINAN_FAMILY_LIST_URL, _clean(form.get("action")))
    if action != SHINAN_FAMILY_LIST_URL or _clean(form.get("method")).lower() != "get":
        raise ShinanContractError(f"page {page}: search form contract changed")

    def form_value(name: str) -> str:
        node = form.select_one(f"[name={name}]")
        return _clean(node.get("value")) if node else ""

    if (
        form_value("rows") != str(SHINAN_PAGE_SIZE)
        or form_value("cpage") != str(page)
        or form_value("area") != "A016"
        or form_value("area_detail") != "D197"
        or form.select_one("input[name=status]") is None
    ):
        raise ShinanContractError(f"page {page}: owner/pagination form values changed")
    programme_list = soup.select_one(".program_list > ul")
    if programme_list is None:
        raise ShinanContractError(f"page {page}: programme list missing")
    cards = programme_list.select(":scope > li.clearfix")
    rows = tuple(_parse_card(card, page) for card in cards)
    if len(rows) > SHINAN_PAGE_SIZE:
        raise ShinanContractError(f"page {page}: page-size overflow")
    pager_pages: list[int] = []
    for anchor in soup.select("a[href*='cpage=']"):
        parsed = urlparse(urljoin(SHINAN_FAMILY_LIST_URL, anchor.get("href")))
        if (parsed.hostname or "").lower() != SHINAN_FAMILY_HOST:
            raise ShinanContractError(f"page {page}: off-host pager")
        value = (parse_qs(parsed.query).get("cpage") or [""])[0]
        if _POSITIVE_ID_RE.fullmatch(value):
            pager_pages.append(int(value))
    declared = max(pager_pages, default=1)
    empty_text = _clean(programme_list.get_text(" ", strip=True))
    empty_marker = not rows and empty_text == "프로그램 목록이 존재하지 않습니다."
    if not rows and not empty_marker:
        raise ShinanContractError(f"page {page}: ambiguous empty list")
    return _ListPage(page, rows, declared, empty_marker)


def _page_signature(page: _ListPage) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row["identity"],
            row["title"],
            row["source_status"],
            row["event_start"],
            row["event_end"],
            row["apply_start"],
            row["apply_end"],
            row["venue"],
            row["application_control"],
        )
        for row in page.rows
    )


def _integer(value: Any, label: str, identity: str, *, maximum: int = 1_000_000) -> int:
    raw = _clean(value)
    if not re.fullmatch(r"\d+", raw):
        raise ShinanContractError(f"detail {identity}: invalid {label} {value!r}")
    result = int(raw)
    if result > maximum:
        raise ShinanContractError(f"detail {identity}: {label} exceeds cap")
    return result


def _episode_schedule(
    payload: Mapping[str, Any],
    *,
    identity: str,
    rounds: int,
) -> tuple[tuple[datetime, datetime], ...]:
    episodes = payload.get("episode")
    if not isinstance(episodes, list) or not episodes:
        raise ShinanContractError(f"detail {identity}: episode schedule missing")
    if len(episodes) > rounds:
        raise ShinanContractError(f"detail {identity}: episode count exceeds list rounds")
    result: list[tuple[datetime, datetime]] = []
    for index, episode in enumerate(episodes, start=1):
        if not isinstance(episode, Mapping):
            raise ShinanContractError(f"detail {identity}: invalid episode object")
        if _clean(episode.get("episode")) != str(index):
            raise ShinanContractError(f"detail {identity}: episode order changed")
        match = _EPISODE_DATETIME_RE.fullmatch(_clean(episode.get("episode_dt")))
        if not match:
            raise ShinanContractError(f"detail {identity}: episode datetime changed")
        start = datetime.strptime(match.group("start"), "%Y-%m-%d %H:%M")
        end = datetime.strptime(match.group("end"), "%Y-%m-%d %H:%M")
        if end < start:
            raise ShinanContractError(f"detail {identity}: reversed episode datetime")
        result.append((start, end))
    return tuple(result)


def _schedule_text(
    episodes: tuple[tuple[datetime, datetime], ...],
    *,
    rounds: int,
) -> str:
    time_ranges: list[str] = []
    for start, end in episodes:
        value = f"{start:%H:%M}~{end:%H:%M}"
        if value == "00:00~00:00" or value in time_ranges:
            continue
        time_ranges.append(value)
    if not time_ranges:
        return f"총 {rounds}회 · 시간 별도 안내"
    return f"총 {rounds}회 · {', '.join(time_ranges)}"


def _parse_detail_shell(soup: BeautifulSoup, identity: str) -> str:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "신안군 가족센터>프로그램안내>프로그램신청":
        raise ShinanContractError(f"detail {identity}: wrong page title")
    if soup.select_one(".program_view") is None:
        raise ShinanContractError(f"detail {identity}: programme shell missing")
    seq_node = soup.select_one("input[name=familynet_pg_no]")
    if seq_node is None or _clean(seq_node.get("value")) != identity:
        raise ShinanContractError(f"detail {identity}: shell identity mismatch")
    csrf_node = soup.select_one("meta[name=_csrf]")
    csrf = _clean(csrf_node.get("content")) if csrf_node else ""
    if not _CSRF_RE.fullmatch(csrf):
        raise ShinanContractError(f"detail {identity}: invalid CSRF contract")
    area = soup.select_one("input[name=area]")
    area_detail = soup.select_one("input[name=area_detail]")
    if (
        area is None
        or _clean(area.get("value")) != "A016"
        or area_detail is None
        or _clean(area_detail.get("value")) != "D197"
    ):
        raise ShinanContractError(f"detail {identity}: owner codes changed")
    apply_button = soup.select_one("#applyBtn")
    if (
        apply_button is None
        or _clean(apply_button.get_text(" ", strip=True)) != "신청하기"
        or _clean(apply_button.get("href"))
        != "javascript:applysMethods.modal.openApply();"
    ):
        raise ShinanContractError(f"detail {identity}: login-gated control missing")
    login = soup.select_one(f"a[href='{SHINAN_FAMILY_LOGIN_PATH}']")
    if login is None or _clean(login.get_text(" ", strip=True)) != "로그인":
        raise ShinanContractError(f"detail {identity}: login gate changed")
    scripts = "\n".join(node.get_text("\n", strip=False) for node in soup.select("script"))
    if (
        SHINAN_FAMILY_VIEW_API_PATH not in scripts
        or "/recruitReceipt/loginCheck.do" not in scripts
    ):
        raise ShinanContractError(f"detail {identity}: application/API script changed")
    return csrf


def _parse_detail_payload(
    payload: Mapping[str, Any],
    listed: Mapping[str, Any],
) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    view = payload.get("view")
    if not isinstance(view, Mapping):
        raise ShinanContractError(f"detail {identity}: view object missing")
    if payload.get("apply_yn") is not False:
        raise ShinanContractError(f"detail {identity}: anonymous application state changed")
    if _clean(view.get("familynet_pg_no")) != identity:
        raise ShinanContractError(f"detail {identity}: API identity mismatch")
    if _clean(view.get("title")) != _clean(listed.get("title")):
        raise ShinanContractError(f"detail {identity}: title mismatch")
    if _clean(view.get("area")) != "A016" or _clean(view.get("area_detail")) != "D197":
        raise ShinanContractError(f"detail {identity}: API owner codes changed")
    if _clean(view.get("area_nm")) != "전남광주" or _clean(view.get("area_detail_nm")) != "신안군":
        raise ShinanContractError(f"detail {identity}: API owner names changed")

    event_start = date.fromisoformat(_clean(view.get("program_start_date")))
    event_end = date.fromisoformat(_clean(view.get("program_end_date")))
    rounds = int(listed.get("rounds") or 0)
    episodes = _episode_schedule(payload, identity=identity, rounds=rounds)
    episode_start = min(start.date() for start, _ in episodes)
    episode_end = max(end.date() for _, end in episodes)
    if (event_start, event_end) != (episode_start, episode_end):
        raise ShinanContractError(f"detail {identity}: API/episode date mismatch")
    apply_start = datetime.strptime(
        _clean(view.get("reception_date_start_time")), "%Y-%m-%d %H:%M"
    )
    apply_end = datetime.strptime(
        _clean(view.get("reception_date_end_time")), "%Y-%m-%d %H:%M"
    )
    listed_start = listed.get("event_start")
    listed_end = listed.get("event_end")
    episode_boundaries = {
        boundary.date()
        for episode in episodes
        for boundary in episode
    }
    list_projection_verified = (
        isinstance(listed_start, date)
        and isinstance(listed_end, date)
        and event_start <= listed_start <= listed_end <= event_end
        and listed_start in episode_boundaries
        and listed_end in episode_boundaries
    )
    if not list_projection_verified:
        raise ShinanContractError(
            f"detail {identity}: list period is not an episode-bound API projection"
        )
    if apply_start != listed.get("apply_start") or apply_end != listed.get("apply_end"):
        raise ShinanContractError(f"detail {identity}: list/API reception date mismatch")
    source_status = _clean(view.get("program_status_nm"))
    if source_status != _clean(listed.get("source_status")):
        raise ShinanContractError(f"detail {identity}: list/API status mismatch")

    place1 = _clean(view.get("program_place1"))
    place2 = _clean(view.get("program_place2"))
    if not place1 or not place2 or _clean(f"{place1} {place2}") != _clean(listed.get("venue")):
        raise ShinanContractError(f"detail {identity}: list/API venue mismatch")
    target = _clean(view.get("participation_target"))
    if not target or len(target) > 500 or _PHONE_RE.search(target) or _EMAIL_RE.search(target):
        raise ShinanContractError(f"detail {identity}: invalid target")
    capacity_current = _integer(view.get("curr_apply_seq"), "current capacity", identity)
    capacity_total = _integer(view.get("recruit_personal"), "capacity", identity)
    capacity_wait_total = _integer(view.get("waiting_personal"), "wait capacity", identity)
    if capacity_total < 1 or capacity_current > capacity_total + capacity_wait_total:
        raise ShinanContractError(f"detail {identity}: impossible capacity")

    row = {
        "provider": SHINAN_FAMILY_PROVIDER,
        "provider_course_id": f"family_center:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(listed.get("title")),
        "description": _clean(listed.get("title")),
        "branch": SHINAN_FAMILY_BRANCH,
        "branch_code": "shinan_family_center",
        "preserve_branch": True,
        "category": "교육",
        "program_type": "가족센터 프로그램",
        "raw_url": shinan_family_detail_url(identity),
        "application_url": shinan_family_detail_url(identity),
        "application_type": "온라인",
        "application_method_raw": "온라인 신청(로그인)",
        "reservation_available": listed.get("status") == "OPEN",
        "status": _clean(listed.get("status")),
        "fee": "요금 별도 안내",
        "period": f"{event_start.isoformat()} ~ {event_end.isoformat()}",
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": (
            f"{apply_start.strftime('%Y-%m-%d %H:%M')} ~ "
            f"{apply_end.strftime('%Y-%m-%d %H:%M')}"
        ),
        "apply_start_date": apply_start.strftime("%Y-%m-%d %H:%M"),
        "apply_end_date": apply_end.strftime("%Y-%m-%d %H:%M"),
        "schedule_raw": _schedule_text(episodes, rounds=rounds),
        "target": target,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_wait_total": capacity_wait_total,
        "venue_name": place2,
        "collection_category": "education",
        "domain_category": "교육",
        "operator_type": "가족센터",
        "source_group": "municipal_family_center",
        "service_group": "education",
        "collection_type": "course",
        "municipality_code": SHINAN_MUNICIPALITY_CODE,
        "municipality_name": SHINAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": SHINAN_PARSER,
            "source_identity": identity,
            "source_page": int(listed.get("page") or 0),
            "source_status": source_status,
            "source_list_event_period": (
                f"{listed_start.isoformat()} ~ {listed_end.isoformat()}"
            ),
            "source_api_event_period": (
                f"{event_start.isoformat()} ~ {event_end.isoformat()}"
            ),
            "source_episode_count": len(episodes),
            "list_period_projection_verified": list_projection_verified,
            "schedule_evidence": "detail_api_episode_dt",
            "fee_evidence": "official_family_center_payload_omits_fee",
            "detail_verified": True,
            "detail_api_verified": True,
            "application_control_present": bool(listed.get("application_control")),
            "application_control_contract": "login_gated_modal_not_requested",
            "application_control_verified": True,
        },
    }
    _validate_emitted_row(row)
    return row


def _validate_emitted_row(row: Mapping[str, Any]) -> None:
    unknown = frozenset(row) - _ALLOWED_ROW_KEYS
    if unknown:
        raise ShinanContractError(f"emitted row has unknown keys: {sorted(unknown)}")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or frozenset(raw) - _ALLOWED_RAW_KEYS:
        raise ShinanContractError("emitted row has unsafe raw_fields")
    lowered = {str(key).lower() for key in row}
    if lowered & _FORBIDDEN_KEYS:
        raise ShinanContractError("emitted row contains forbidden fields")
    text_values: list[str] = []
    for key, value in row.items():
        if key in {"raw_url", "application_url"}:
            continue
        if isinstance(value, str):
            text_values.append(value)
        elif isinstance(value, Mapping):
            text_values.extend(str(item) for item in value.values())
    combined = " ".join(text_values)
    if _PHONE_RE.search(combined) or _EMAIL_RE.search(combined):
        raise ShinanContractError("emitted row leaked contact data")


def is_shinan_education_target(target: Any) -> bool:
    """Return true only for the audited Family Center owner and catalogue."""

    return (
        _clean(_target_value(target, "provider")) == SHINAN_FAMILY_PROVIDER
        and _clean(_target_value(target, "url")) == SHINAN_FAMILY_LIST_URL
    )


is_target = is_shinan_education_target


def collect_shinan_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = SHINAN_MAX_PAGES,
    detail_limit: int = 100,
    workers: int = SHINAN_MAX_WORKERS,
    cutoff: Optional[date] = None,
    session_factory: Optional[SessionFactory] = None,
    html_fetcher: Optional[HtmlFetcher] = None,
    json_fetcher: Optional[JsonFetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete, current/future Family Center snapshot.

    A contract failure returns no rows and records ``configured_collection_error``
    in metadata.  Caps never authorize a partial snapshot.
    """

    audit_date = cutoff or date.today()
    factory = session_factory or _new_session
    current_html_fetcher = html_fetcher or _default_html_fetcher
    current_json_fetcher = json_fetcher or _default_json_fetcher
    meta: dict[str, Any] = {
        "municipality_code": SHINAN_MUNICIPALITY_CODE,
        "owner_provider": SHINAN_FAMILY_PROVIDER,
        "canonical_url": SHINAN_FAMILY_LIST_URL,
        "parser": SHINAN_PARSER,
        "cutoff": audit_date.isoformat(),
        "pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "detail_api_requests": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "source_cap_reached": False,
        "applicant_form_requests": 0,
        "login_requests": 0,
    }
    try:
        if _clean(_target_value(target, "provider")) != SHINAN_FAMILY_PROVIDER:
            raise ShinanContractError("target provider does not own the Family Center ledger")
        target_url = _clean(_target_value(target, "url"))
        _validate_public_url(
            target_url,
            path=SHINAN_FAMILY_LIST_PATH,
            query_keys=frozenset(),
        )
        if target_url != SHINAN_FAMILY_LIST_URL:
            raise ShinanContractError("target URL is not the canonical catalogue")
        if timeout < 1 or max_pages < 1 or detail_limit < 0 or workers < 1:
            raise ShinanContractError("invalid collector limits")

        current = factory()
        data_pages: dict[int, _ListPage] = {}
        sentinel: Optional[_ListPage] = None
        try:
            for page_number in range(1, max_pages + 1):
                parsed = _parse_list_page(
                    _fetch_soup(
                        current,
                        shinan_family_list_url(page_number),
                        timeout,
                        current_html_fetcher,
                    ),
                    page_number,
                )
                meta["list_requests"] += 1
                if parsed.rows:
                    data_pages[page_number] = parsed
                    continue
                sentinel = parsed
                break
            if sentinel is None:
                meta["source_cap_reached"] = True
                raise ShinanContractError(
                    f"max_pages cap {max_pages} reached before an empty sentinel"
                )

            first_recheck = _parse_list_page(
                _fetch_soup(
                    current,
                    shinan_family_list_url(1),
                    timeout,
                    current_html_fetcher,
                ),
                1,
            )
            meta["list_requests"] += 1
            last_number = max(data_pages, default=1)
            last_recheck = _parse_list_page(
                _fetch_soup(
                    current,
                    shinan_family_list_url(last_number),
                    timeout,
                    current_html_fetcher,
                ),
                last_number,
            )
            meta["list_requests"] += 1
            sentinel_recheck = _parse_list_page(
                _fetch_soup(
                    current,
                    shinan_family_list_url(sentinel.page),
                    timeout,
                    current_html_fetcher,
                ),
                sentinel.page,
            )
            meta["list_requests"] += 1
        finally:
            close = getattr(current, "close", None)
            if callable(close):
                close()

        first = data_pages.get(1, sentinel)
        last = data_pages.get(max(data_pages), sentinel) if data_pages else sentinel
        if (
            _page_signature(first_recheck) != _page_signature(first)
            or _page_signature(last_recheck) != _page_signature(last)
            or not sentinel.empty_marker
            or sentinel.rows
            or not sentinel_recheck.empty_marker
            or sentinel_recheck.rows
        ):
            raise ShinanContractError("first/last/sentinel stability recheck changed")
        if data_pages and sorted(data_pages) != list(range(1, max(data_pages) + 1)):
            raise ShinanContractError("non-consecutive data pages")
        for page_number, parsed in data_pages.items():
            if page_number < max(data_pages) and len(parsed.rows) != SHINAN_PAGE_SIZE:
                raise ShinanContractError(f"page {page_number}: premature short page")
            if page_number == max(data_pages) and not (1 <= len(parsed.rows) <= SHINAN_PAGE_SIZE):
                raise ShinanContractError("invalid last-page size")

        listed = [
            row
            for page_number in sorted(data_pages)
            for row in data_pages[page_number].rows
        ]
        identities = [_clean(row.get("identity")) for row in listed]
        if len(identities) != len(set(identities)):
            raise ShinanContractError("duplicate source identities across pages")
        numeric_identities = [int(value) for value in identities]
        if numeric_identities != sorted(numeric_identities, reverse=True):
            raise ShinanContractError("source identities are not in stable descending order")
        observed_pages = len(data_pages)
        declared_max = max(
            (parsed.declared_pager_max for parsed in data_pages.values()),
            default=sentinel.declared_pager_max,
        )
        if declared_max > observed_pages:
            raise ShinanContractError("declared pager points beyond the empty sentinel")

        current_listed = [row for row in listed if row["event_end"] >= audit_date]
        expired_listed = [row for row in listed if row["event_end"] < audit_date]
        for row in current_listed:
            if row["status"] == "OPEN" and not (
                row["apply_start"].date() <= audit_date <= row["apply_end"].date()
            ):
                raise ShinanContractError(
                    f"course {row['identity']}: OPEN reception-date contradiction"
                )
        meta.update(
            {
                "pages": observed_pages,
                "data_pages": observed_pages,
                "page_counts": {
                    page: len(parsed.rows) for page, parsed in sorted(data_pages.items())
                },
                "empty_sentinel_page": sentinel.page,
                "declared_pager_max": declared_max,
                "pagination_declared_underflow": max(0, observed_pages - declared_max),
                "pagination_detected": observed_pages > 1,
                "source_rows": len(listed),
                "source_total": len(listed),
                "source_status_counts": dict(
                    Counter(row["source_status"] for row in listed)
                ),
                "current_source_count": len(current_listed),
                "expired_source_count": len(expired_listed),
                "expired_but_open_quarantined": sum(
                    row["status"] == "OPEN" for row in expired_listed
                ),
                "identity_duplicate_count": len(identities) - len(set(identities)),
                "stability_rechecks": 3,
            }
        )
        if len(current_listed) > detail_limit:
            meta["source_cap_reached"] = True
            raise ShinanContractError(
                f"detail_limit cap allows {detail_limit} of "
                f"{len(current_listed)} required current details"
            )

        detailed: dict[str, dict[str, Any]] = {}
        detail_errors: list[str] = []

        def fetch_detail(listed_row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
            identity = _clean(listed_row.get("identity"))
            detail_session = factory()
            try:
                shell = _fetch_soup(
                    detail_session,
                    shinan_family_detail_url(identity),
                    timeout,
                    current_html_fetcher,
                )
                csrf = _parse_detail_shell(shell, identity)
                payload = _fetch_json(
                    detail_session,
                    identity,
                    csrf,
                    timeout,
                    current_json_fetcher,
                )
                return identity, _parse_detail_payload(payload, listed_row)
            finally:
                close = getattr(detail_session, "close", None)
                if callable(close):
                    close()

        if current_listed:
            with ThreadPoolExecutor(max_workers=min(workers, len(current_listed))) as pool:
                futures = {pool.submit(fetch_detail, row): row for row in current_listed}
                for future in as_completed(futures):
                    identity = _clean(futures[future].get("identity"))
                    try:
                        result_identity, row = future.result()
                        detailed[result_identity] = row
                    except Exception as exc:  # fail closed after all workers settle
                        detail_errors.append(f"detail {identity}: {exc}")
            meta["detail_pages"] = len(current_listed)
            meta["detail_api_requests"] = len(current_listed)
        if detail_errors or len(detailed) != len(current_listed):
            raise ShinanContractError("; ".join(sorted(detail_errors)) or "detail loss")

        output = [detailed[row["identity"]] for row in current_listed]
        if len({row["provider_course_id"] for row in output}) != len(output):
            raise ShinanContractError("duplicate emitted provider_course_id")
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        meta.update(
            {
                "pagination_complete": True,
                "detail_attempts": len(current_listed),
                "detail_verified": len(current_listed),
                "application_controls_verified": sum(
                    bool(row["reservation_available"]) for row in output
                ),
                "output_rows": len(output),
                "configured_collection_error": "",
            }
        )
        return output, SHINAN_PARSER, meta
    except (ShinanContractError, requests.RequestException, ValueError, TypeError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        meta["pagination_complete"] = False
        meta["output_rows"] = 0
        return [], SHINAN_PARSER, meta


__all__ = [
    "SHINAN_AGRICULTURAL_EDUCATION_URL",
    "SHINAN_AGRICULTURAL_NOTICE_URL",
    "SHINAN_CANDIDATE_AUDIT",
    "SHINAN_DISCOVERY_AUDIT",
    "SHINAN_FAMILY_BRANCH",
    "SHINAN_FAMILY_DETAIL_PATH",
    "SHINAN_FAMILY_HOST",
    "SHINAN_FAMILY_LIST_PATH",
    "SHINAN_FAMILY_LIST_URL",
    "SHINAN_FAMILY_PROVIDER",
    "SHINAN_FAMILY_VIEW_API_URL",
    "SHINAN_JNTLE_CANDIDATE_ID",
    "SHINAN_JNTLE_PROVIDER",
    "SHINAN_JNTLE_URL",
    "SHINAN_LIBRARY_EVENTS_URL",
    "SHINAN_LIBRARY_URL",
    "SHINAN_MUNICIPALITY_CODE",
    "SHINAN_MUNICIPALITY_NAME",
    "SHINAN_MUSEUM_EDUCATION_URL",
    "SHINAN_OWNER_BOUNDARY_AUDIT",
    "SHINAN_PARSER",
    "SHINAN_PII_FIELDS_DISCARDED",
    "SHINAN_RETIRED_IT_EDUCATION_URLS",
    "ShinanContractError",
    "collect_shinan_education",
    "shinan_family_detail_url",
    "shinan_family_list_url",
]
