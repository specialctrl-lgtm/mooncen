"""Fail-closed collector for Gimhae City's official education ledger.

The configured ``/yes/05560.web`` provider is the incumbent public-reservation
owner, but that URL is only a redirecting navigation alias.  Collection is
retargeted in-memory to the official course identity ledger at
``/yes/05560/05835/05836.web``.  The separate ``/edu.web`` provider is a
promotional lifelong-learning home page whose generic crawl overlaps a small,
identity-free subset of the reservation ledger; it is deliberately excluded.

All advertised pages, the server's exact repeated-final-page clamp sentinel,
and stable first/final/sentinel rechecks are required.  Every current/future
public detail is identity-checked.  Reservation, login, attachment, image,
telephone and other PII-bearing endpoints are inspected only as inert controls
and are never requested or persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GIMHAE_PROVIDER = "MUNI_WWW_GIMHAE_GO_KR_48CF9E63"
GIMHAE_EXCLUDED_DISCOVERY_PROVIDER = "MUNI_WWW_GIMHAE_GO_KR_8FB5FCC3"
GIMHAE_CANDIDATE_ID = "MUNI_IR_F72AF57E5C19"
GIMHAE_EXCLUDED_CANDIDATE_ID = "MUNI_IR_A330C2A277DD"
GIMHAE_MUNICIPALITY_CODE = "4825000000"
GIMHAE_MUNICIPALITY_NAME = "경상남도 김해시"

GIMHAE_HOST = "www.gimhae.go.kr"
GIMHAE_DISCOVERY_PATH = "/yes/05560.web"
GIMHAE_DISCOVERY_URL = f"https://{GIMHAE_HOST}{GIMHAE_DISCOVERY_PATH}"
GIMHAE_EXCLUDED_DISCOVERY_PATH = "/edu.web"
GIMHAE_EXCLUDED_DISCOVERY_URL = (
    f"https://{GIMHAE_HOST}{GIMHAE_EXCLUDED_DISCOVERY_PATH}"
)
GIMHAE_LEDGER_PATH = "/yes/05560/05835/05836.web"
GIMHAE_LEDGER_URL = f"https://{GIMHAE_HOST}{GIMHAE_LEDGER_PATH}"
GIMHAE_PAGE_SIZE = 9
GIMHAE_MAX_PAGES = 100
GIMHAE_MAX_DETAILS = 700
GIMHAE_MAX_HTML_BYTES = 4_000_000
GIMHAE_DETAIL_WORKERS = 8

GIMHAE_PARSER = (
    "gimhae_yes_incumbent+official_cssno_identity_ledger+all_advertised_pages+"
    "exact_repeated_final_clamp_sentinel+stable_first_final_sentinel+"
    "all_current_future_details+official_contact_branches+no_private_endpoints+"
    "bounded_single_deleted_year_repair+pii_allowlist"
)

GIMHAE_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "reservation_owner": {
        "provider": GIMHAE_PROVIDER,
        "candidate_id": GIMHAE_CANDIDATE_ID,
        "configured_url": GIMHAE_DISCOVERY_URL,
        "canonical_ledger": GIMHAE_LEDGER_URL,
        "decision": "retain_incumbent_and_retarget_to_official_cssno_ledger",
    },
    "lifelong_discovery_duplicate": {
        "provider": GIMHAE_EXCLUDED_DISCOVERY_PROVIDER,
        "candidate_id": GIMHAE_EXCLUDED_CANDIDATE_ID,
        "url": GIMHAE_EXCLUDED_DISCOVERY_URL,
        "decision": "exclude_promotional_generic_subset_without_application_identity",
    },
    "global_identity": {
        "decision": "one_namespaced_owner_identity_per_official_cssno",
        "overlap_policy": "promotional discovery rows never enter the owner union",
    },
}

GIMHAE_SEARCH_TYPE_REGISTRY = (
    ("all_nm", "전체"),
    ("course_nm", "강좌명"),
    ("inst_nm", "강사명"),
    ("fac_nm", "시설명"),
)
GIMHAE_STATE_REGISTRY = (
    ("", "전체"),
    ("ing", "접수중"),
    ("full", "정원마감"),
    ("yet", "홍보중"),
)
GIMHAE_TARGET_REGISTRY = (
    ("", "전체"),
    ("01", "영아"),
    ("02", "유아"),
    ("03", "초등"),
    ("04", "청소년"),
    ("05", "성인"),
    ("06", "노인"),
)
GIMHAE_METHOD_REGISTRY = (
    ("", "전체"),
    ("tel", "전화접수"),
    ("web", "인터넷접수"),
    ("visit", "방문접수"),
)

GIMHAE_BRANCH_REGISTRY = frozenset(
    {
        "AI정책과",
        "구산사회복지관",
        "기적의도서관",
        "김해시여성센터",
        "김해시청소년센터",
        "김해시청(부원동)",
        "김해시",
        "김해어린이영어도서관",
        "내외동 주민자치센터",
        "내외문화의집",
        "농업기술센터 농업기술과",
        "농업기술센터 농업기술지원과",
        "부원동 주민자치센터",
        "부원동행정복지센터",
        "북부동",
        "안동문화의집",
        "서부청소년센터",
        "율하도서관",
        "장유1동 주민자치센터",
        "장유2동",
        "장유2동 주민자치센터",
        "장유3동 주민자치센터",
        "장유3동(강좌,시설대관)",
        "장유3동주민자치센터",
        "장유3동주민자치위원회",
        "장유3동주민자치회",
        "장유도서관",
        "장유출장소",
        "정보통신과",
        "직장맘지원센터",
        "진영한빛도서관",
        "책읽는도시",
        "청소년상담복지센터",
        "청소년수련관",
        "평생학습관",
        "화정글샘",
        "화정생활문화센터",
        "화정생활문화센터 어울림",
        "칠암도서관",
    }
)
GIMHAE_BRANCH_ALIASES: Mapping[str, str] = {
    "여성센터1": "김해시여성센터",
}

_STATUS_MAP = {
    "접수중": "OPEN",
    "대기자접수중": "OPEN",
    "정원마감": "CLOSED",
    "접수마감": "CLOSED",
    "홍보중": "SCHEDULED",
}
_DETAIL_REQUIRED = frozenset(
    {
        "접수기간",
        "교육기간",
        "요일시간",
        "대상",
        "장소",
        "강사명",
        "수강료",
        "접수인원 / 총인원",
        "이용문의",
    }
)
_DETAIL_OPTIONAL = frozenset({"재료비 / 교재비"})
_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[.-](\d{2})[.-](\d{2})(?!\d)")
_DATE_TOKEN_RE = re.compile(r"(?<!\d)(\d{3,4})[.-](\d{2})[.-](\d{2})(?!\d)")
_IDENTITY_RE = re.compile(r"^[1-9]\d{0,11}$")
_INTEGER_RE = re.compile(r"\d[\d,]*")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GimhaeContractError(RuntimeError):
    """Raised when the audited public Gimhae contract changes."""


@dataclass(frozen=True)
class _Page:
    number: int
    reported: int
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _strict_url(value: Any, path: str, query: list[tuple[str, str]]) -> bool:
    parsed = urlparse(_clean(value))
    try:
        actual_query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GIMHAE_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == path
        and actual_query == query
        and not parsed.fragment
    )


def is_gimhae_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != GIMHAE_PROVIDER:
        return False
    url = _target_value(target, "url")
    return _strict_url(url, GIMHAE_DISCOVERY_PATH, []) or _strict_url(
        url, GIMHAE_LEDGER_PATH, []
    )


is_target = is_gimhae_target


def gimhae_list_url(page: int) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    return f"{GIMHAE_LEDGER_URL}?{urlencode([('cpage', str(page))])}"


def gimhae_detail_url(identity: Any, page: int) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise ValueError("invalid Gimhae course identity")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    query = [("amode", "view"), ("cssno", value), ("cpage", str(page))]
    return f"{GIMHAE_LEDGER_URL}?{urlencode(query)}"


def gimhae_source_identity(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise ValueError("invalid Gimhae course identity")
    return f"{GIMHAE_PROVIDER}:course:{value}"


def gimhae_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://www.gimhae.go.kr/yes/main.web",
        }
    )
    return session


def _default_fetcher(session: Any, method: str, url: str, *, timeout: int) -> Any:
    if method != "GET":
        raise GimhaeContractError("only audited public GET routes are allowed")
    return session.get(url, timeout=timeout, allow_redirects=False)


def _allowed_request(method: str, url: str) -> bool:
    if method != "GET":
        return False
    parsed = urlparse(url)
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GIMHAE_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GIMHAE_LEDGER_PATH
        and not parsed.fragment
    ):
        return False
    if len(query) == 1:
        return query[0][0] == "cpage" and query[0][1].isdigit() and int(query[0][1]) >= 1
    return bool(
        len(query) == 3
        and query[0] == ("amode", "view")
        and query[1][0] == "cssno"
        and _IDENTITY_RE.fullmatch(query[1][1])
        and query[2][0] == "cpage"
        and query[2][1].isdigit()
        and int(query[2][1]) >= 1
    )


def _coerce_soup(response: Any, requested_url: str) -> BeautifulSoup:
    status = int(getattr(response, "status_code", 200))
    if status != 200:
        raise GimhaeContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise GimhaeContractError("redirect history is not accepted")
    headers = getattr(response, "headers", {}) or {}
    if headers.get("Location") or headers.get("location"):
        raise GimhaeContractError("redirect response is not accepted")
    content_type = _clean(headers.get("Content-Type") or headers.get("content-type"))
    if content_type and "html" not in content_type.lower():
        raise GimhaeContractError("official response is not HTML")
    final_url = _clean(getattr(response, "url", requested_url) or requested_url)
    if final_url != requested_url:
        raise GimhaeContractError("official response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        content = str(getattr(response, "text", response)).encode("utf-8")
    if not content:
        raise GimhaeContractError("empty official response")
    if len(content) > GIMHAE_MAX_HTML_BYTES:
        raise GimhaeContractError("HTML size cap exceeded")
    return BeautifulSoup(content, "html.parser")


def _select_options(select: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (_clean(node.get("value")), _clean(node.get_text(" ", strip=True)))
        for node in (select.select("option") if select else ())
    )


def _list_contract(soup: BeautifulSoup, requested_page: int) -> tuple[Any, int, int, int]:
    forms = soup.select("form#listForm[name='listForm']")
    if len(forms) != 1 or _clean(forms[0].get("method")).upper() != "GET":
        raise GimhaeContractError(f"page {requested_page}: list form changed")
    form = forms[0]
    action = urlparse(urljoin(GIMHAE_LEDGER_URL, _clean(form.get("action"))))
    if action.path != GIMHAE_LEDGER_PATH or parse_qsl(
        action.query, keep_blank_values=True, strict_parsing=True
    ) != [("cpage", str(requested_page))]:
        raise GimhaeContractError(f"page {requested_page}: list action changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[name]")
    }
    expected_hidden = {
        "cpage": "1",
        "oby": "",
        "sstring": "",
        "regStartDate": "",
        "regEndDate": "",
        "startDt": "",
        "endDt": "",
    }
    if {key: hidden.get(key) for key in expected_hidden} != expected_hidden:
        raise GimhaeContractError(f"page {requested_page}: unfiltered scope changed")
    registries = (
        ("stype", GIMHAE_SEARCH_TYPE_REGISTRY),
        ("lecState", GIMHAE_STATE_REGISTRY),
        ("targetCd", GIMHAE_TARGET_REGISTRY),
        ("appMethod", GIMHAE_METHOD_REGISTRY),
    )
    for name, expected in registries:
        if _select_options(form.select_one(f"select[name='{name}']")) != expected:
            raise GimhaeContractError(f"page {requested_page}: {name} registry changed")
    info_nodes = soup.select(".infomenu1 .info1")
    if len(info_nodes) != 1:
        raise GimhaeContractError(f"page {requested_page}: advertised count missing")
    info = _clean(info_nodes[0].get_text(" ", strip=True))
    match = re.fullmatch(
        r"총\s*([\d,]+)\s*건의 게시물이 있습니다[.]\s*[(]\s*(\d+)\s*/\s*(\d+)\s*페이지[)]",
        info,
    )
    if match is None:
        raise GimhaeContractError(f"page {requested_page}: advertised count changed")
    total, reported, last = (int(value.replace(",", "")) for value in match.groups())
    if total < 1 or last != math.ceil(total / GIMHAE_PAGE_SIZE):
        raise GimhaeContractError("advertised total/page count is inconsistent")
    expected_reported = min(requested_page, last)
    if reported != expected_reported:
        raise GimhaeContractError(f"page {requested_page}: reported page changed")
    return form, total, reported, last


def _two_dates(value: Any, identity: str, field: str) -> tuple[date, date]:
    cleaned = _clean(value)
    matches = _DATE_RE.findall(cleaned)
    if len(matches) != 2:
        tokens = _DATE_TOKEN_RE.findall(cleaned)
        canonical_years = [year for year, _month, _day in tokens if len(year) == 4]
        short_years = [year for year, _month, _day in tokens if len(year) == 3]
        if len(tokens) != 2 or len(canonical_years) != 1 or len(short_years) != 1:
            raise GimhaeContractError(
                f"course {identity}: {field} must contain two dates"
            )
        canonical_year = canonical_years[0]
        short_year = short_years[0]
        deleted_digit_forms = {
            canonical_year[:index] + canonical_year[index + 1 :]
            for index in range(len(canonical_year))
        }
        if (
            not canonical_year.startswith("20")
            or short_year not in deleted_digit_forms
        ):
            raise GimhaeContractError(
                f"course {identity}: {field} must contain two dates"
            )
        matches = [
            (canonical_year if year == short_year else year, month, day)
            for year, month, day in tokens
        ]
    try:
        start, end = (date(int(y), int(m), int(d)) for y, m, d in matches)
    except ValueError:
        raise GimhaeContractError(f"course {identity}: {field} must contain two dates")
    if len(_DATE_RE.findall(cleaned)) not in {1, 2}:
        raise GimhaeContractError(f"course {identity}: {field} must contain two dates")
    if end < start:
        raise GimhaeContractError(f"course {identity}: reversed {field}")
    return start, end


def _prefixed(value: str, prefix: str, identity: str) -> str:
    if not value.startswith(prefix):
        raise GimhaeContractError(f"course {identity}: list field {prefix!r} changed")
    result = _clean(value[len(prefix) :])
    if not result:
        raise GimhaeContractError(f"course {identity}: empty list field {prefix!r}")
    return result


def _parse_card(card: Any, page: int) -> dict[str, Any]:
    anchors = card.select(":scope > div.w1 > a.a1[href]")
    if len(anchors) != 1:
        raise GimhaeContractError(f"page {page}: course card control changed")
    anchor = anchors[0]
    href = _clean(anchor.get("href"))
    parsed = urlparse(urljoin(gimhae_list_url(page), href))
    query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if len(query) != 3 or query[0] != ("amode", "view") or query[1][0] != "cssno" or query[2] != (
        "cpage",
        str(page),
    ):
        raise GimhaeContractError(f"page {page}: detail link contract changed")
    identity = query[1][1]
    if not _IDENTITY_RE.fullmatch(identity):
        raise GimhaeContractError(f"page {page}: invalid official identity")
    expected_url = gimhae_detail_url(identity, page)
    if not _strict_url(expected_url, GIMHAE_LEDGER_PATH, query):
        raise GimhaeContractError(f"course {identity}: unsafe detail URL")
    titles = anchor.select(":scope div.tg1 > strong.t1")
    states = anchor.select(":scope div.tg1 > b.g1")
    values = [_clean(node.get_text(" ", strip=True)) for node in anchor.select(":scope ul.lst1 > li.li1")]
    if len(titles) != 1 or len(states) != 1 or len(values) != 6:
        raise GimhaeContractError(f"course {identity}: card structure changed")
    title = _clean(titles[0].get_text(" ", strip=True))
    source_status = _clean(states[0].get_text(" ", strip=True))
    status = _STATUS_MAP.get(source_status)
    if not title or status is None:
        raise GimhaeContractError(f"course {identity}: title/status changed")
    apply_period = _prefixed(values[0], "접수기간 :", identity)
    event_period = _prefixed(values[1], "교육기간 :", identity)
    schedule = _prefixed(values[2], "요일시간 :", identity)
    capacity_text = _prefixed(values[3], "접수인원/총인원 :", identity)
    target = _prefixed(values[4], "대상 :", identity)
    method = _prefixed(values[5], "접수방법 :", identity)
    apply_start, apply_end = _two_dates(apply_period, identity, "application period")
    event_start, event_end = _two_dates(event_period, identity, "education period")
    capacities = [int(value.replace(",", "")) for value in _INTEGER_RE.findall(capacity_text)]
    if len(capacities) != 2:
        raise GimhaeContractError(f"course {identity}: list capacity changed")
    return {
        "identity": identity,
        "source_identity": gimhae_source_identity(identity),
        "page": page,
        "detail_url": expected_url,
        "title": title,
        "source_status": source_status,
        "status": status,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "event_start": event_start,
        "event_end": event_end,
        "event_period_source": event_period,
        "schedule": schedule,
        "applicants": capacities[0],
        "capacity": capacities[1],
        "target": target,
        "method": method,
    }


def _parse_page(soup: BeautifulSoup, page: int, known_total: int | None = None) -> _Page:
    _form, total, reported, last = _list_contract(soup, page)
    if known_total is not None and total != known_total:
        raise GimhaeContractError(f"page {page}: advertised total drift")
    containers = soup.select("ul.even-grid.evenmix-123")
    if len(containers) != 1:
        raise GimhaeContractError(f"page {page}: card ledger changed")
    cards = containers[0].select(":scope > li.column")
    rows = tuple(_parse_card(card, page) for card in cards)
    expected = min(GIMHAE_PAGE_SIZE, total - (reported - 1) * GIMHAE_PAGE_SIZE)
    if len(rows) != expected:
        raise GimhaeContractError(f"page {page}: expected {expected} cards, found {len(rows)}")
    identities = [str(row["identity"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise GimhaeContractError(f"page {page}: duplicate official identities")
    return _Page(page, reported, total, last, rows)


def _row_signature(row: Mapping[str, Any], *, ignore_page: bool = False) -> tuple[Any, ...]:
    return (
        row["source_identity"],
        row["title"],
        row["source_status"],
        row["apply_start"],
        row["apply_end"],
        row["event_start"],
        row["event_end"],
        row["schedule"],
        row["capacity"],
        row["target"],
        row["method"],
        None if ignore_page else row["detail_url"],
    )


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (page.total, page.last, page.reported, tuple(_row_signature(row) for row in page.rows))


def _detail_fields(root: Any, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    lists = root.select(":scope > div.even-grid div.cp20dlist1 > ul.dl1")
    if len(lists) != 1:
        raise GimhaeContractError(f"course {identity}: primary detail list changed")
    for item in lists[0].select(":scope > li.di"):
        labels = item.select(":scope > b.dt")
        values = item.select(":scope > span.dd")
        if len(labels) != 1 or len(values) != 1:
            raise GimhaeContractError(f"course {identity}: detail field structure changed")
        label = _clean(labels[0].get_text(" ", strip=True))
        value = _clean(values[0].get_text(" ", strip=True))
        if not label or label in fields:
            raise GimhaeContractError(f"course {identity}: conflicting detail field")
        fields[label] = value
    allowed = _DETAIL_REQUIRED | _DETAIL_OPTIONAL
    if not _DETAIL_REQUIRED <= set(fields) or not set(fields) <= allowed:
        missing = sorted(_DETAIL_REQUIRED - set(fields))
        extra = sorted(set(fields) - allowed)
        raise GimhaeContractError(
            f"course {identity}: detail fields changed missing={missing} extra={extra}"
        )
    return fields


def _branch_from_contact(root: Any, fields: Mapping[str, str], identity: str) -> str:
    contacts = []
    for item in root.select(":scope > div.even-grid .cp20dlist1 > ul.dl1 > li.di"):
        label = item.select_one(":scope > b.dt")
        if label is not None and _clean(label.get_text(" ", strip=True)) == "이용문의":
            contacts.append(item)
    if len(contacts) != 1:
        raise GimhaeContractError(f"course {identity}: contact branch structure changed")
    branch = fields["이용문의"]
    phone_controls = contacts[0].select("a[href^='tel:']")
    if not phone_controls:
        raise GimhaeContractError(f"course {identity}: telephone control missing")
    for control in phone_controls:
        branch = branch.replace(_clean(control.get_text(" ", strip=True)), "")
    branch = branch.strip(" ,")
    branch = GIMHAE_BRANCH_ALIASES.get(branch, branch)
    if branch not in GIMHAE_BRANCH_REGISTRY:
        raise GimhaeContractError(f"course {identity}: unaudited official branch {branch!r}")
    return branch


def _parse_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], dict[str, int]]:
    identity = str(listed["identity"])
    roots = soup.select("div.cp20view1")
    if len(roots) != 1:
        raise GimhaeContractError(f"course {identity}: detail root changed")
    root = roots[0]
    titles = root.select(":scope > div.hg1 > h2.h1")
    states = root.select(":scope > div.hg1 > b.g1")
    if len(titles) != 1 or len(states) != 1:
        raise GimhaeContractError(f"course {identity}: detail title/status structure changed")
    if (
        _clean(titles[0].get_text(" ", strip=True)) != listed["title"]
        or _clean(states[0].get_text(" ", strip=True)) != listed["source_status"]
    ):
        raise GimhaeContractError(f"course {identity}: list/detail title/status drift")
    fields = _detail_fields(root, identity)
    apply_start, apply_end = _two_dates(fields["접수기간"], identity, "detail application period")
    event_start, event_end = _two_dates(fields["교육기간"], identity, "detail education period")
    counts = [int(value.replace(",", "")) for value in _INTEGER_RE.findall(fields["접수인원 / 총인원"])]
    if not (
        (apply_start, apply_end) == (listed["apply_start"], listed["apply_end"])
        and (event_start, event_end) == (listed["event_start"], listed["event_end"])
        and _compact(fields["요일시간"]) == _compact(listed["schedule"])
        and _compact(fields["대상"]) == _compact(listed["target"])
        and len(counts) == 2
        and counts[0] >= 0
        and counts[1] == listed["capacity"]
    ):
        raise GimhaeContractError(f"course {identity}: list/detail identity drift")
    branch = _branch_from_contact(root, fields, identity)
    application_controls = root.select("a[href*='amode=ins'][href*='cssno=']")
    offline_controls = [
        control
        for control in root.select("a[href='#'][onclick]")
        if _clean(control.get_text(" ", strip=True)) == "예약하기"
    ]
    offline_application = False
    if listed["status"] == "OPEN":
        if len(application_controls) == 1 and not offline_controls:
            action = urljoin(str(listed["detail_url"]), _clean(application_controls[0].get("href")))
            query = parse_qsl(urlparse(action).query, keep_blank_values=True, strict_parsing=True)
            if query != [
                ("amode", "ins"),
                ("cssno", identity),
                ("cpage", str(listed["page"])),
            ]:
                raise GimhaeContractError(f"course {identity}: reservation identity drift")
        elif not application_controls and len(offline_controls) == 1:
            method = _clean(listed["method"])
            onclick = _compact(offline_controls[0].get("onclick"))
            expected = _compact(
                "alert('선택하신 강좌는 인터넷 접수를 받지 않습니다.');return false;"
            )
            if (
                "인터넷" in method
                or not any(value in method for value in ("전화", "방문"))
                or onclick != expected
            ):
                raise GimhaeContractError(
                    f"course {identity}: offline reservation notice changed"
                )
            offline_application = True
        else:
            raise GimhaeContractError(f"course {identity}: open reservation control changed")
    elif application_controls or offline_controls:
        raise GimhaeContractError(f"course {identity}: closed/scheduled detail exposes reservation")
    attachment_controls = root.select(
        "a[href*='download'],a[href*='attach'],a[href*='cmsfile']"
    )
    # Course images are outside the allowlisted data request surface.  A future
    # attachment control inside the identity detail must trigger review.
    if attachment_controls:
        raise GimhaeContractError(f"course {identity}: attachment control appeared")
    capacity = int(listed["capacity"])
    period = f"{event_start.isoformat()} ~ {event_end.isoformat()}"
    branch_code = "GIMHAE_" + hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    material_fee = fields.get("재료비 / 교재비", "")
    row = {
        "provider": GIMHAE_PROVIDER,
        "provider_course_id": str(listed["source_identity"]),
        "prefer_incoming_provider_course_id": True,
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": branch,
        "branch_code": branch_code,
        "preserve_branch": True,
        "category": "교육·강좌",
        "program_type": "교육",
        "raw_url": str(listed["detail_url"]),
        "application_url": "",
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "status": str(listed["status"]),
        "raw_status": str(listed["source_status"]),
        "period": period,
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": fields["요일시간"],
        "fee": fields["수강료"],
        "material_fee": material_fee,
        "capacity": str(capacity),
        "capacity_current": counts[0],
        "capacity_total": capacity,
        "capacity_remaining": max(capacity - counts[0], 0),
        "target": fields["대상"],
        "venue": fields["장소"],
        "venue_name": fields["장소"],
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GIMHAE_PARSER,
        "municipality_code": GIMHAE_MUNICIPALITY_CODE,
        "municipality_name": GIMHAE_MUNICIPALITY_NAME,
        "municipality_full_name": GIMHAE_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": int(listed["page"]),
            "source_status": str(listed["source_status"]),
            "source_application_method": str(listed["method"]),
            "list_event_period_source": str(listed["event_period_source"]),
            "detail_event_period_source": fields["교육기간"],
            "source_year_repair": (
                len(_DATE_RE.findall(str(listed["event_period_source"]))) == 1
                or len(_DATE_RE.findall(fields["교육기간"])) == 1
            ),
            "detail_verified": True,
            "list_capacity_current": int(listed["applicants"]),
            "detail_capacity_current": counts[0],
            "capacity_changed_during_collection": (
                counts[0] != int(listed["applicants"])
            ),
            "reservation_control_count": len(application_controls),
            "offline_application_notice": offline_application,
            "reservation_endpoint_not_requested": True,
            "application_endpoint_not_requested": True,
            "login_endpoint_not_requested": True,
            "attachment_endpoint_not_requested": True,
            "image_endpoint_not_requested": True,
            "telephone_endpoint_not_requested": True,
            "sensitive_detail_fields_discarded": 2,
            "branch_basis": "official detail 이용문의 institution",
        },
    }
    return row, {
        "reservation_controls": len(application_controls),
        "offline_application_controls": int(offline_application),
        "capacity_changes": int(counts[0] != int(listed["applicants"])),
        "sensitive": 2,
        "attachments": len(attachment_controls),
    }


def _base_meta(cutoff: date) -> dict[str, Any]:
    return {
        "provider": GIMHAE_PROVIDER,
        "municipality_code": GIMHAE_MUNICIPALITY_CODE,
        "municipality_name": GIMHAE_MUNICIPALITY_NAME,
        "configured_alias": GIMHAE_DISCOVERY_URL,
        "canonical_url": GIMHAE_LEDGER_URL,
        "parser": GIMHAE_PARSER,
        "cutoff": cutoff.isoformat(),
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "reservation_endpoint_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "file_download_endpoint_requests": 0,
        "image_endpoint_requests": 0,
        "telephone_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_values_persisted": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
        "candidate_id": GIMHAE_CANDIDATE_ID,
        "excluded_candidate_id": GIMHAE_EXCLUDED_CANDIDATE_ID,
        "owner_boundary_audit": {
            key: dict(value) for key, value in GIMHAE_OWNER_BOUNDARY_AUDIT.items()
        },
    }


def _loader(
    meta: dict[str, Any], session: Any, fetcher: Fetcher, timeout: int
) -> Callable[[str, str], BeautifulSoup]:
    lock = threading.Lock()

    def load(url: str, kind: str) -> BeautifulSoup:
        if not _allowed_request("GET", url):
            raise GimhaeContractError("refusing request outside audited Gimhae routes")
        with lock:
            meta["list_requests" if kind == "list" else "detail_pages"] += 1
            meta["logical_requests"] += 1
        last_error: Optional[BaseException] = None
        for attempt in range(2):
            with lock:
                meta["physical_requests"] += 1
            try:
                response = fetcher(session, "GET", url, timeout=timeout)
                status = int(getattr(response, "status_code", 200))
                if status in {429, 500, 502, 503, 504} and attempt == 0:
                    with lock:
                        meta["request_retry_count"] += 1
                    continue
                return _coerce_soup(response, url)
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 0:
                    with lock:
                        meta["request_retry_count"] += 1
                    continue
                raise
        raise GimhaeContractError(f"request failed: {last_error}")

    return load


def _privacy_violations(rows: Iterable[Mapping[str, Any]]) -> int:
    forbidden = {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "teacher",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
    count = 0
    for row in rows:
        count += len(set(row) & forbidden)
        raw = row.get("raw_fields")
        if isinstance(raw, Mapping):
            count += len(set(raw) & forbidden)
        payload = repr(row)
        count += len(_PHONE_RE.findall(payload)) + len(_EMAIL_RE.findall(payload))
    return count


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def collect_gimhae_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = GIMHAE_MAX_PAGES,
    detail_limit: int = GIMHAE_MAX_DETAILS,
    detail_workers: int = GIMHAE_DETAIL_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    try:
        cutoff = _audit_date(today)
    except (TypeError, ValueError):
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
        meta = _base_meta(cutoff)
        meta["configured_collection_error"] = "today is invalid"
        return [], GIMHAE_PARSER, meta
    meta = _base_meta(cutoff)
    if not is_gimhae_target(target):
        meta["configured_collection_error"] = "target does not match Gimhae reservation owner"
        return [], GIMHAE_PARSER, meta
    try:
        request_timeout = int(timeout)
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        workers = int(detail_workers)
        if (
            request_timeout < 1
            or allowed_pages < 1
            or allowed_details < 0
            or workers < 1
            or workers > 12
        ):
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/detail_workers are invalid"
        )
        return [], GIMHAE_PARSER, meta
    session: Any = None
    pages: dict[int, _Page] = {}
    listed: list[dict[str, Any]] = []
    try:
        session = (session_factory or gimhae_session_factory)()
        load = _loader(meta, session, fetcher or _default_fetcher, request_timeout)
        first = _parse_page(load(gimhae_list_url(1), "list"), 1)
        pages[1] = first
        sentinel_page = first.last + 1
        boundary_pages = set((1, first.last, sentinel_page))
        required_list_requests = first.last + 1 + len(boundary_pages)
        meta["required_list_requests"] = required_list_requests
        if required_list_requests > allowed_pages:
            meta["source_cap_reached"] = True
            raise GimhaeContractError(
                f"max_pages cap allows {allowed_pages} of {required_list_requests} required list requests"
            )
        for page in range(2, first.last + 1):
            pages[page] = _parse_page(
                load(gimhae_list_url(page), "list"), page, first.total
            )
        sentinel = _parse_page(
            load(gimhae_list_url(sentinel_page), "list"),
            sentinel_page,
            first.total,
        )
        final = pages[first.last]
        if tuple(_row_signature(row, ignore_page=True) for row in sentinel.rows) != tuple(
            _row_signature(row, ignore_page=True) for row in final.rows
        ):
            raise GimhaeContractError("immediate post-last clamp sentinel changed")
        for page in range(1, first.last + 1):
            listed.extend(dict(row) for row in pages[page].rows)
        if len(listed) != first.total:
            raise GimhaeContractError("advertised total differs from full row union")
        source_ids = [str(row["source_identity"]) for row in listed]
        if len(source_ids) != len(set(source_ids)):
            raise GimhaeContractError("duplicate official identities across pages")
        current = [row for row in listed if row["event_end"] >= cutoff]
        expired = [row for row in listed if row["event_end"] < cutoff]
        if len(current) > allowed_details:
            meta["source_cap_reached"] = True
            raise GimhaeContractError(
                f"detail_limit cap allows {allowed_details} of {len(current)} current details"
            )

        def detail_one(row: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
            soup = load(str(row["detail_url"]), "detail")
            return _parse_detail(row, soup)

        parsed_details: list[tuple[dict[str, Any], dict[str, int]]]
        if workers == 1 or len(current) < 2:
            parsed_details = [detail_one(row) for row in current]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                parsed_details = list(pool.map(detail_one, current))
        output = [row for row, _counters in parsed_details]
        discarded = Counter()
        for _row, counters in parsed_details:
            discarded.update(counters)
        rechecks: dict[str, bool] = {}
        originals = {1: pages[1], first.last: final, sentinel_page: sentinel}
        for page, original in originals.items():
            observed = _parse_page(
                load(gimhae_list_url(page), "list"), page, first.total
            )
            stable = _page_signature(observed) == _page_signature(original)
            rechecks[str(page)] = stable
            if not stable:
                raise GimhaeContractError(f"page {page}: boundary stability recheck changed")
        deduped = list((dedupe_rows or _default_dedupe)(output))
        if len(deduped) != len(output):
            raise GimhaeContractError(
                f"dedupe changed complete row count {len(output)} to {len(deduped)}"
            )
        privacy = _privacy_violations(deduped)
        meta["pii_values_persisted"] = privacy
        if privacy:
            raise GimhaeContractError(f"{privacy} PII allowlist violations")
        deduped.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )
        meta.update(
            {
                "pages": first.last,
                "data_pages": first.last,
                "page_counts": {page: len(value.rows) for page, value in pages.items()},
                "source_total": first.total,
                "source_rows": len(listed),
                "clamp_sentinel_page": sentinel_page,
                "clamp_sentinel_rows": len(sentinel.rows),
                "boundary_rechecks": rechecks,
                "boundary_recheck_count": len(rechecks),
                "current_source_count": len(current),
                "expired_count": len(expired),
                "detail_verified": len(output),
                "returned_count": len(deduped),
                "source_status_counts": dict(Counter(row["source_status"] for row in current)),
                "status_counts": dict(Counter(row["status"] for row in deduped)),
                "branch_counts": dict(Counter(row["branch"] for row in deduped)),
                "source_identity_count": len(source_ids),
                "source_identity_sha256": hashlib.sha256(
                    "\n".join(sorted(source_ids)).encode("utf-8")
                ).hexdigest(),
                "reservation_control_count": discarded["reservation_controls"],
                "offline_application_notice_count": discarded[
                    "offline_application_controls"
                ],
                "capacity_change_count": discarded["capacity_changes"],
                "sensitive_detail_fields_discarded": discarded["sensitive"],
                "attachment_fields_discarded": discarded["attachments"],
                "excluded_discovery_provider": GIMHAE_EXCLUDED_DISCOVERY_PROVIDER,
                "excluded_discovery_overlap": "generic subset; no stable application identity",
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, GIMHAE_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "source_rows": len(listed),
                "returned_count": 0,
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], GIMHAE_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


collect = collect_gimhae_education


__all__ = [
    "GIMHAE_BRANCH_REGISTRY",
    "GIMHAE_CANDIDATE_ID",
    "GIMHAE_DISCOVERY_URL",
    "GIMHAE_EXCLUDED_CANDIDATE_ID",
    "GIMHAE_EXCLUDED_DISCOVERY_PROVIDER",
    "GIMHAE_EXCLUDED_DISCOVERY_URL",
    "GIMHAE_LEDGER_URL",
    "GIMHAE_METHOD_REGISTRY",
    "GIMHAE_MUNICIPALITY_CODE",
    "GIMHAE_MUNICIPALITY_NAME",
    "GIMHAE_OWNER_BOUNDARY_AUDIT",
    "GIMHAE_PAGE_SIZE",
    "GIMHAE_PARSER",
    "GIMHAE_PROVIDER",
    "GIMHAE_SEARCH_TYPE_REGISTRY",
    "GIMHAE_STATE_REGISTRY",
    "GIMHAE_TARGET_REGISTRY",
    "GimhaeContractError",
    "collect",
    "collect_gimhae_education",
    "gimhae_detail_url",
    "gimhae_list_url",
    "gimhae_session_factory",
    "gimhae_source_identity",
    "is_gimhae_target",
    "is_target",
]
