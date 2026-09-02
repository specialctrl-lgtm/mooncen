"""Fail-closed collector for Donghae City's regular lifelong courses.

The official catalogue defaults to the current year and exposes ten rows per
page without a textual total.  Its descending public row numbers and its
last-page navigation together form the cardinality contract.  This adapter
reads every advertised page, an immediately empty post-last page, and stable
copies of both boundary pages before returning anything.

Only courses whose education end date is today or later are persisted.  Each
one is checked against its identity-bound detail page, including the public
application state.  Instructor names, contacts, descriptions, attachments,
application payloads, and source HTML are deliberately never retained.

``www.dh.go.kr`` needs OpenSSL security level 1 to negotiate TLS, but still
offers a CA-valid TLS 1.2 connection.  The scoped adapter below changes only
the cipher security level; certificate and hostname verification remain on.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import math
import re
import ssl
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


DONGHAE_PROVIDER = "MUNI_WWW_DH_GO_KR_1A4CE8CA"
DONGHAE_SUPERSEDED_PROVIDER = "MUNI_WWW_DH_GO_KR_1197E1BB"
DONGHAE_CANONICAL_CANDIDATE_ID = "MUNI_IR_E30297FCCB25"
DONGHAE_MUNICIPALITY_CODE = "5117000000"
DONGHAE_MUNICIPALITY_NAME = "강원특별자치도 동해시"
DONGHAE_HOST = "www.dh.go.kr"
DONGHAE_LIST_PATH = "/lifelong/selectEduLctreWebList.do"
DONGHAE_DETAIL_PATH = "/lifelong/selectEduLctreWebView.do"
DONGHAE_APPLICATION_PATH = "/lifelong/addEduLctreReqstWebView.do"
DONGHAE_BRANCH = "동해시 평생학습관"
DONGHAE_BRANCH_CODE = "donghae:lifelong-center"
DONGHAE_PAGE_SIZE = 10
DONGHAE_MAX_WORKERS = 8
DONGHAE_FETCH_ATTEMPTS = 2
DONGHAE_MAX_HTML_BYTES = 2_000_000
DONGHAE_PARSER = (
    "donghae_four_lifelong_catalogues+descending_ordinal_all_pages+"
    "audited_public_terminal_gaps+empty_post_last_pages+"
    "stable_first_last_boundaries+current_details+"
    "identity_bound_application_controls+verified_legacy_tls+pii_allowlist"
)


@dataclass(frozen=True)
class DonghaeCatalogue:
    code: str
    name: str
    key: str
    info_no: str

    @property
    def canonical_url(self) -> str:
        return (
            f"https://{DONGHAE_HOST}{DONGHAE_LIST_PATH}?"
            + urlencode((("key", self.key), ("eduInfoNo", self.info_no)))
        )


DONGHAE_CATALOGUES: tuple[DonghaeCatalogue, ...] = (
    DonghaeCatalogue("regular", "정기교육", "1060", "1"),
    DonghaeCatalogue("digital", "시민정보화교육", "1064", "2"),
    DonghaeCatalogue("special", "특성화/기획강좌", "1067", "3"),
    DonghaeCatalogue("university", "동해시민대학", "1926", "4"),
)
_CATALOGUE_BY_CODE = {item.code: item for item in DONGHAE_CATALOGUES}
_CATALOGUE_BY_PAIR = {(item.key, item.info_no): item for item in DONGHAE_CATALOGUES}
DONGHAE_CANONICAL_URL = DONGHAE_CATALOGUES[0].canonical_url
DONGHAE_OWNERSHIP_ALIAS_URLS: tuple[str, ...] = tuple(
    item.canonical_url for item in DONGHAE_CATALOGUES[1:]
)
DONGHAE_OWNERSHIP_SCOPE = "donghae_lifelong_education_all_four_catalogues"

DONGHAE_PII_FIELDS_DISCARDED = (
    "강사명",
    "강좌소개",
    "강의계획서",
    "첨부파일",
    "연락처",
    "담당자",
    "source_html",
    "application_payload",
    "applicant_fields",
)

_HEADERS = (
    "번호",
    "과정",
    "강좌명",
    "접수기간/교육기간",
    "교육요일 및 시간",
    "선발방법",
    "신청/모집 (예비자)",
    "접수상태",
)
_STATUS_MAP: Mapping[str, tuple[str, str]] = {
    "접수예정": ("SCHEDULED", "n1"),
    "접수중": ("OPEN", "n2"),
    "접수마감": ("CLOSED", "n3"),
}
_DETAIL_REQUIRED_FIELDS = frozenset(
    {
        "접수기간",
        "접수현황",
        "선발방법",
        "교육기간",
        "교육시간",
        "교육장",
        "수강료",
    }
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_catalogue",
        "source_catalogue_name",
        "source_key",
        "source_info_no",
        "list_page",
        "source_ordinal",
        "source_title_core",
        "source_shift",
        "source_category",
        "source_status",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "source_selection_method",
        "source_capacity_current",
        "source_capacity_total",
        "source_waitlist_current",
        "source_waitlist_total",
        "source_fee",
        "source_venue",
        "service_family",
        "list_control_contract",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
        "detail_verified",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "teacher",
        "manager",
        "staff",
        "contact",
        "phone",
        "email",
        "attachments",
        "attachment_urls",
        "source_html",
        "raw_html",
        "application_payload",
        "applicant",
    }
)
_SPACE_RE = re.compile(r"\s+")
_ISO_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class DonghaeContractError(ValueError):
    """Raised when an official page no longer satisfies its public contract."""


@dataclass(frozen=True)
class _ListPage:
    rows: list[dict[str, Any]]
    linked_last: int
    errors: list[str]


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _canonical_public_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
        or parsed.params
    ):
        return ""
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if len(pairs) != len({key for key, _value in pairs}):
        return ""
    query = urlencode(sorted(pairs))
    return f"https://{parsed.hostname.rstrip('.').lower()}{parsed.path or '/'}" + (
        f"?{query}" if query else ""
    )


def is_donghae_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == DONGHAE_PROVIDER
        and _canonical_public_url(_target_value(target, "url"))
        == _canonical_public_url(DONGHAE_CANONICAL_URL)
    )


def is_target(target: Any) -> bool:
    return is_donghae_education_target(target)


def _cutoff(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise ValueError("today must be an ISO date") from exc


def _catalogue(value: Any = "regular") -> DonghaeCatalogue:
    if isinstance(value, DonghaeCatalogue) and value in DONGHAE_CATALOGUES:
        return value
    code = _clean(value)
    if code in _CATALOGUE_BY_CODE:
        return _CATALOGUE_BY_CODE[code]
    raise ValueError("unknown Donghae education catalogue")


def donghae_list_url(
    page: int, year: int, catalogue: Any = "regular"
) -> str:
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
        or isinstance(year, bool)
        or not isinstance(year, int)
        or not 2000 <= year <= 2100
    ):
        raise ValueError("page/year out of range")
    source = _catalogue(catalogue)
    return f"https://{DONGHAE_HOST}{DONGHAE_LIST_PATH}?" + urlencode(
        (
            ("key", source.key),
            ("year", str(year)),
            ("eduClassNo", ""),
            ("eduCtgryNo", ""),
            ("eduInfoNo", source.info_no),
            ("rceptSttus", ""),
            ("eduPoolSj", ""),
            ("pageUnit", str(DONGHAE_PAGE_SIZE)),
            ("pageIndex", str(page)),
        )
    )


def donghae_detail_url(identity: Any, catalogue: Any = "regular") -> str:
    value = _clean(identity)
    if not re.fullmatch(r"\d+", value):
        raise ValueError("invalid Donghae lecture identity")
    source = _catalogue(catalogue)
    return f"https://{DONGHAE_HOST}{DONGHAE_DETAIL_PATH}?" + urlencode(
        (("key", source.key), ("eduLctreNo", value))
    )


class _DonghaeLegacyTLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = _donghae_tls_context()
        super().init_poolmanager(*args, **kwargs)


def _donghae_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("DEFAULT:@SECLEVEL=1")
    context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    return context


def donghae_session_factory() -> requests.Session:
    current = requests.Session()
    current.trust_env = False
    current.mount(f"https://{DONGHAE_HOST}/", _DonghaeLegacyTLSAdapter())
    current.headers.update(
        {
            "User-Agent": "mooncen-donghae-education/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
            "Referer": DONGHAE_CANONICAL_URL,
        }
    )
    return current


def _allowed_request_url(url: str) -> bool:
    parsed = urlparse(_clean(url))
    if (
        parsed.scheme != "https"
        or parsed.hostname != DONGHAE_HOST
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
        or parsed.params
    ):
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == DONGHAE_DETAIL_PATH:
        return bool(
            set(query) == {"key", "eduLctreNo"}
            and len(query.get("key", [])) == 1
            and query["key"][0]
            in {catalogue.key for catalogue in DONGHAE_CATALOGUES}
            and len(query.get("eduLctreNo", [])) == 1
            and re.fullmatch(r"\d+", query["eduLctreNo"][0])
        )
    if parsed.path != DONGHAE_LIST_PATH:
        return False
    expected_keys = {
        "key",
        "year",
        "eduClassNo",
        "eduCtgryNo",
        "eduInfoNo",
        "rceptSttus",
        "eduPoolSj",
        "pageUnit",
        "pageIndex",
    }
    return bool(
        set(query) == expected_keys
        and len(query.get("key", [])) == 1
        and len(query.get("eduInfoNo", [])) == 1
        and (query["key"][0], query["eduInfoNo"][0]) in _CATALOGUE_BY_PAIR
        and query.get("eduClassNo") == [""]
        and query.get("eduCtgryNo") == [""]
        and query.get("rceptSttus") == [""]
        and query.get("eduPoolSj") == [""]
        and query.get("pageUnit") == [str(DONGHAE_PAGE_SIZE)]
        and len(query.get("year", [])) == 1
        and re.fullmatch(r"20\d{2}", query["year"][0])
        and len(query.get("pageIndex", [])) == 1
        and re.fullmatch(r"[1-9]\d*", query["pageIndex"][0])
    )


def _default_fetcher(session: Any, url: str, timeout: int) -> BeautifulSoup:
    if not _allowed_request_url(url):
        raise DonghaeContractError("request URL escaped the Donghae allowlist")
    response = session.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise DonghaeContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "history", None):
        raise DonghaeContractError("redirect history is not accepted")
    if _clean((getattr(response, "headers", {}) or {}).get("Location")):
        raise DonghaeContractError("redirect response is not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and _canonical_public_url(final_url) != _canonical_public_url(url):
        raise DonghaeContractError("final response URL changed")
    content = getattr(response, "content", b"")
    if not content or len(content) > DONGHAE_MAX_HTML_BYTES:
        raise DonghaeContractError("empty or oversized HTML response")
    content_type = _clean(
        (getattr(response, "headers", {}) or {}).get("Content-Type")
    ).lower()
    if content_type and "html" not in content_type:
        raise DonghaeContractError("response is not HTML")
    return BeautifulSoup(content, "lxml")


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if not encoded or len(encoded) > DONGHAE_MAX_HTML_BYTES:
            raise DonghaeContractError("empty or oversized fixture HTML")
        return BeautifulSoup(value, "lxml")
    if isinstance(value, (bytes, bytearray)):
        if not value or len(value) > DONGHAE_MAX_HTML_BYTES:
            raise DonghaeContractError("empty or oversized fixture HTML")
        return BeautifulSoup(bytes(value), "lxml")
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return _coerce_soup(content)
    raise TypeError("fetcher must return HTML, bytes, response, or BeautifulSoup")


def _close_quietly(value: Any) -> None:
    try:
        value.close()
    except Exception:
        pass


def _fetch_parse_many(
    items: Iterable[tuple[Any, str, Callable[[BeautifulSoup], Any]]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, Any], list[str]]:
    tasks = list(items)
    if not tasks:
        return {}, []

    def worker(
        key: Any, url: str, parser: Callable[[BeautifulSoup], Any]
    ) -> tuple[Any, Any]:
        last_error: Optional[Exception] = None
        for _attempt in range(DONGHAE_FETCH_ATTEMPTS):
            current = session_factory()
            try:
                return key, parser(_coerce_soup(fetcher(current, url, timeout)))
            except Exception as exc:
                last_error = exc
            finally:
                _close_quietly(current)
        raise RuntimeError(_clean(last_error))

    results: dict[Any, Any] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        futures = {
            executor.submit(worker, key, url, parser): key
            for key, url, parser in tasks
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                result_key, value = future.result()
                results[result_key] = value
            except Exception as exc:
                errors.append(f"{key}: {_clean(exc)}")
    return results, errors


def _selected_value(form: Any, name: str) -> tuple[int, str]:
    nodes = form.select(f'[name="{name}"]') if form is not None else []
    if len(nodes) != 1:
        return len(nodes), ""
    node = nodes[0]
    if node.name == "select":
        selected = node.select(":scope > option[selected]")
        if len(selected) != 1:
            return 1, ""
        return 1, _clean(selected[0].get("value"))
    return 1, _clean(node.get("value"))


def _list_form_errors(
    soup: BeautifulSoup, page: int, year: int, source: DonghaeCatalogue
) -> list[str]:
    forms = soup.select("form[name='bbsNttSearchForm']")
    if len(forms) != 1:
        return [f"page {page}: catalogue search form missing or duplicated"]
    form = forms[0]
    action = urlparse(urljoin(DONGHAE_CANONICAL_URL, _clean(form.get("action"))))
    errors: list[str] = []
    if _clean(form.get("method")).lower() != "get" or (
        action.scheme,
        action.hostname,
        action.path,
        action.query,
        action.fragment,
    ) != ("https", DONGHAE_HOST, DONGHAE_LIST_PATH, "", ""):
        errors.append(f"page {page}: catalogue form method/action changed")
    for name, expected in (
        ("key", source.key),
        ("year", str(year)),
        ("eduClassNo", ""),
        ("eduCtgryNo", ""),
        ("rceptSttus", ""),
        ("eduPoolSj", ""),
        ("eduInfoNo", source.info_no),
    ):
        count, actual = _selected_value(form, name)
        if count != 1 or actual != expected:
            errors.append(f"page {page}: unfiltered form field {name} changed")
    return errors


def _date_pair(value: Any, field: str) -> tuple[date, date]:
    matches = _ISO_DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise DonghaeContractError(f"{field}: expected exactly two dates")
    result: list[date] = []
    for year, month, day_value in matches:
        try:
            result.append(date(int(year), int(month), int(day_value)))
        except ValueError as exc:
            raise DonghaeContractError(f"{field}: invalid calendar date") from exc
    if result[0] > result[1]:
        raise DonghaeContractError(f"{field}: reversed dates")
    return result[0], result[1]


def _detail_identity(value: Any, source: DonghaeCatalogue) -> str:
    parsed = urlparse(urljoin(source.canonical_url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != DONGHAE_HOST
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
        or parsed.path != DONGHAE_DETAIL_PATH
        or set(query) != {"key", "eduLctreNo"}
        or query.get("key") != [source.key]
        or len(query.get("eduLctreNo", [])) != 1
        or not re.fullmatch(r"\d+", query["eduLctreNo"][0])
    ):
        raise DonghaeContractError("course detail identity URL changed")
    return query["eduLctreNo"][0]


def _parse_row(
    tr: Any, page: int, source: DonghaeCatalogue
) -> dict[str, Any]:
    cells = tr.find_all("td", recursive=False)
    if len(cells) != len(_HEADERS):
        raise DonghaeContractError("course row column count changed")
    ordinal_text = _clean(cells[0].get_text(" ", strip=True))
    if not re.fullmatch(r"\d+", ordinal_text):
        raise DonghaeContractError("course ordinal is not numeric")
    ordinal = int(ordinal_text)
    category = _clean(cells[1].get_text(" ", strip=True))
    title_links = cells[2].select(":scope > a[href]")
    if len(title_links) != 1:
        raise DonghaeContractError("course title/detail control changed")
    title_link = title_links[0]
    identity = _detail_identity(title_link.get("href"), source)
    shifts = title_link.select(":scope > span.two_shift")
    titles = title_link.select(":scope > span.table_row-title")
    if len(shifts) != 1 or len(titles) != 1:
        raise DonghaeContractError(f"course {identity}: title structure changed")
    shift = _clean(shifts[0].get_text(" ", strip=True))
    title_core = _clean(titles[0].get_text(" ", strip=True))
    if shift not in {"주간", "야간"} or not title_core or not category:
        raise DonghaeContractError(f"course {identity}: title/category is invalid")
    title = f"{shift} {title_core}"

    period_nodes = cells[3].select(":scope > p")
    register_nodes = cells[3].select(":scope > p.register_date")
    education_nodes = cells[3].select(":scope > p.education_date")
    if len(period_nodes) != 2 or len(register_nodes) != 1 or len(education_nodes) != 1:
        raise DonghaeContractError(f"course {identity}: period structure changed")
    apply_start, apply_end = _date_pair(
        register_nodes[0].get_text(" ", strip=True),
        f"course {identity} application period",
    )
    start, end = _date_pair(
        education_nodes[0].get_text(" ", strip=True),
        f"course {identity} education period",
    )
    schedule = _clean(cells[4].get_text(" ", strip=True))
    selection_method = _clean(cells[5].get_text(" ", strip=True))
    if not schedule or selection_method not in {"추첨", "선착순"}:
        raise DonghaeContractError(f"course {identity}: schedule/method changed")

    capacity_selectors = (
        ".request_num",
        ".recruit_num",
        ".pre-req_num",
        ".pre-rec_num",
    )
    capacity: list[int] = []
    for selector in capacity_selectors:
        nodes = cells[6].select(selector)
        value = _clean(nodes[0].get_text(" ", strip=True)) if len(nodes) == 1 else ""
        if not re.fullmatch(r"[\d,]+", value):
            raise DonghaeContractError(f"course {identity}: capacity structure changed")
        capacity.append(int(value.replace(",", "")))
    current_count, total_count, wait_current, wait_total = capacity
    if min(capacity) < 0 or total_count < 1:
        raise DonghaeContractError(f"course {identity}: capacity values invalid")

    status_controls = cells[7].select(":scope > a.p-btn[href]")
    if len(status_controls) != 1:
        raise DonghaeContractError(f"course {identity}: status control changed")
    status_control = status_controls[0]
    source_status = _clean(status_control.get_text(" ", strip=True))
    if source_status not in _STATUS_MAP:
        raise DonghaeContractError(f"course {identity}: unknown status")
    normalized_status, expected_class = _STATUS_MAP[source_status]
    if expected_class not in (status_control.get("class") or []):
        raise DonghaeContractError(f"course {identity}: status class mismatch")
    if _detail_identity(status_control.get("href"), source) != identity:
        raise DonghaeContractError(f"course {identity}: status/detail identity mismatch")

    return {
        "provider": DONGHAE_PROVIDER,
        "provider_course_id": f"{DONGHAE_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": DONGHAE_BRANCH,
        "branch_code": DONGHAE_BRANCH_CODE,
        "preserve_branch": True,
        "provider_organizer": DONGHAE_BRANCH,
        "category": category,
        "program_type": "교육",
        "raw_url": donghae_detail_url(identity, source),
        "application_url": "",
        "application_type": "INFO_ONLY",
        "application_method": "",
        "application_methods": [],
        "reservation_available": False,
        "status": normalized_status,
        "fee": "",
        "fee_amount": 0,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": schedule,
        "capacity": f"{current_count}/{total_count}",
        "capacity_current": current_count,
        "capacity_total": total_count,
        "target": "동해시민",
        "venue": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": DONGHAE_PARSER,
        "municipality_code": DONGHAE_MUNICIPALITY_CODE,
        "municipality_full_name": DONGHAE_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_catalogue": source.code,
            "source_catalogue_name": source.name,
            "source_key": source.key,
            "source_info_no": source.info_no,
            "list_page": page,
            "source_ordinal": ordinal,
            "source_title_core": title_core,
            "source_shift": shift,
            "source_category": category,
            "source_status": source_status,
            "source_apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
            "source_education_period": f"{start.isoformat()} ~ {end.isoformat()}",
            "source_schedule": schedule,
            "source_selection_method": selection_method,
            "source_capacity_current": current_count,
            "source_capacity_total": total_count,
            "source_waitlist_current": wait_current,
            "source_waitlist_total": wait_total,
            "source_fee": "",
            "source_venue": "",
            "service_family": "education",
            "list_control_contract": "same_identity_detail_anchor+status_class",
            "application_control_present": False,
            "application_control_contract": "",
            "application_control_verified": False,
            "detail_verified": False,
        },
    }


def _linked_last(root: Any, source: DonghaeCatalogue) -> int:
    nodes = root.select(".p-pagination a.next-end[href]")
    if len(nodes) != 1:
        raise DonghaeContractError("last-page navigation missing or duplicated")
    parsed = urlparse(urljoin(source.canonical_url, _clean(nodes[0].get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    values = query.get("pageIndex", [])
    if (
        parsed.scheme != "https"
        or parsed.hostname != DONGHAE_HOST
        or parsed.path != DONGHAE_LIST_PATH
        or query.get("key") != [source.key]
        or query.get("eduInfoNo") != [source.info_no]
        or len(values) != 1
        or not re.fullmatch(r"[1-9]\d*", values[0])
    ):
        raise DonghaeContractError("last-page navigation URL changed")
    return int(values[0])


def _parse_list(
    soup: BeautifulSoup,
    page: int,
    year: int,
    source: DonghaeCatalogue,
) -> _ListPage:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "수강신청 - 평생학습관":
        errors.append(f"page {page}: official catalogue title changed")
    errors.extend(_list_form_errors(soup, page, year, source))
    tables = soup.select("#contents table.p-table.simple")
    if len(tables) != 1:
        return _ListPage([], 0, errors + [f"page {page}: course table missing or duplicated"])
    table = tables[0]
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _HEADERS:
        errors.append(f"page {page}: course table headers changed")
    try:
        last = _linked_last(table.find_parent(id="contents") or soup, source)
    except Exception as exc:
        last = 0
        errors.append(f"page {page}: {_clean(exc)}")

    body_rows = table.select("tbody > tr")
    empty_nodes = table.select("tbody > tr > td.empty[colspan='8']")
    course_rows = [row for row in body_rows if len(row.find_all("td", recursive=False)) == 8]
    if empty_nodes:
        if (
            len(body_rows) != 1
            or len(empty_nodes) != 1
            or _clean(empty_nodes[0].get_text(" ", strip=True))
            != "등록된 강좌 목록이 없습니다."
        ):
            errors.append(f"page {page}: empty sentinel structure changed")
        return _ListPage([], last, errors)
    if len(course_rows) != len(body_rows):
        errors.append(f"page {page}: unexpected non-course table row")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(course_rows, start=1):
        try:
            rows.append(_parse_row(row, page, source))
        except Exception as exc:
            errors.append(f"page {page} row {index}: {_clean(exc)}")
    return _ListPage(rows, last, errors)


def _detail_fields(table: Any) -> tuple[dict[str, str], list[str]]:
    pairs: dict[str, str] = {}
    errors: list[str] = []
    for index, tr in enumerate(table.select("tbody > tr"), start=1):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) != 2 or cells[0].name != "th" or cells[1].name != "td":
            errors.append(f"detail row {index}: label/value structure changed")
            continue
        label = _clean(cells[0].get_text(" ", strip=True))
        value = _clean(cells[1].get_text(" ", strip=True))
        if not label or label in pairs:
            errors.append(f"detail row {index}: label empty or duplicated")
        else:
            pairs[label] = value
    return pairs, errors


def _application_url(
    control: Any, identity: str, source: DonghaeCatalogue
) -> str:
    value = urljoin(
        donghae_detail_url(identity, source), _clean(control.get("href"))
    )
    parsed = urlparse(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected_keys = {
        "key",
        "eduInfoNo",
        "eduCtgryNo",
        "eduClassNo",
        "eduLctreNo",
        "eduPoolNo",
    }
    if (
        parsed.scheme != "https"
        or parsed.hostname != DONGHAE_HOST
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
        or parsed.path != DONGHAE_APPLICATION_PATH
        or set(query) != expected_keys
        or query.get("key") != [source.key]
        or query.get("eduInfoNo") != [source.info_no]
        or query.get("eduLctreNo") != [identity]
        or any(
            len(query.get(name, [])) != 1
            or not re.fullmatch(r"\d+", query[name][0])
            for name in ("eduCtgryNo", "eduClassNo", "eduPoolNo")
        )
    ):
        return ""
    return value


def _fee(value: Any) -> tuple[str, int]:
    text = _clean(value)
    if text == "무료":
        return text, 0
    values = [int(item.replace(",", "")) for item in re.findall(r"[\d,]+", text)]
    if "원" not in text or len(values) != 1:
        raise DonghaeContractError("course fee structure changed")
    return text, values[0]


def _validate_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> tuple[dict[str, Any], list[str]]:
    row = dict(listed)
    row["raw_fields"] = dict(listed["raw_fields"])
    raw = row["raw_fields"]
    identity = _clean(raw["identity"])
    try:
        source = _catalogue(raw.get("source_catalogue"))
    except ValueError:
        return row, [f"course {identity} detail: unknown source catalogue"]
    if raw.get("source_key") != source.key or raw.get("source_info_no") != source.info_no:
        return row, [f"course {identity} detail: source catalogue identity changed"]
    label = f"course {identity} detail"
    errors: list[str] = []
    roots = soup.select("#contents .program.lecture_apply.view .p-wrap.bbs.bbs__view")
    if len(roots) != 1:
        return row, [f"{label}: official detail root missing or duplicated"]
    root = roots[0]
    top_nodes = root.select(":scope > .top_box")
    tables = root.select(":scope > table.p-table.block")
    if len(top_nodes) != 1 or len(tables) != 1:
        return row, [f"{label}: top box/table structure changed"]
    top = top_nodes[0]

    category_nodes = top.select(":scope > .lc_subject")
    title_nodes = top.select(":scope > .title")
    shift_nodes = top.select(":scope > .two_shift")
    status_nodes = top.select(":scope > .p-btn")
    if any(len(nodes) != 1 for nodes in (category_nodes, title_nodes, shift_nodes, status_nodes)):
        errors.append(f"{label}: identity heading structure changed")
    else:
        category = _clean(category_nodes[0].get_text(" ", strip=True)).strip("[] ")
        source_status = _clean(status_nodes[0].get_text(" ", strip=True))
        expected_class = _STATUS_MAP.get(source_status, ("", ""))[1]
        if (
            category != raw["source_category"]
            or _clean(title_nodes[0].get_text(" ", strip=True))
            != raw["source_title_core"]
            or _clean(shift_nodes[0].get_text(" ", strip=True)) != raw["source_shift"]
            or source_status != raw["source_status"]
            or not expected_class
            or expected_class not in (status_nodes[0].get("class") or [])
        ):
            errors.append(f"{label}: list/detail heading or status mismatch")

    fields, field_errors = _detail_fields(tables[0])
    errors.extend(f"{label}: {item}" for item in field_errors)
    if not _DETAIL_REQUIRED_FIELDS <= set(fields):
        errors.append(f"{label}: required fields changed")
    if _DETAIL_REQUIRED_FIELDS <= set(fields):
        try:
            apply_start, apply_end = _date_pair(fields["접수기간"], f"{label} application period")
            if (apply_start.isoformat(), apply_end.isoformat()) != (
                row["apply_start"],
                row["apply_end"],
            ):
                errors.append(f"{label}: application period list/detail mismatch")
            row["apply_period"] = fields["접수기간"]
        except Exception as exc:
            apply_start = apply_end = None
            errors.append(_clean(exc))
        try:
            start, end = _date_pair(fields["교육기간"], f"{label} education period")
            if (start.isoformat(), end.isoformat()) != (
                row["start_date"],
                row["end_date"],
            ):
                errors.append(f"{label}: education period list/detail mismatch")
        except Exception as exc:
            errors.append(_clean(exc))
        if _normalized(fields["교육시간"]) != _normalized(raw["source_schedule"]):
            errors.append(f"{label}: education schedule list/detail mismatch")
        if _clean(fields["선발방법"]) != raw["source_selection_method"]:
            errors.append(f"{label}: selection method list/detail mismatch")

        capacity_strong = tables[0].select("tr th")
        status_row = next(
            (
                th.find_parent("tr")
                for th in capacity_strong
                if _clean(th.get_text(" ", strip=True)) == "접수현황"
            ),
            None,
        )
        values = [
            int(_clean(node.get_text(" ", strip=True)).replace(",", ""))
            for node in (status_row.select("td strong") if status_row is not None else [])
            if re.fullmatch(r"[\d,]+", _clean(node.get_text(" ", strip=True)))
        ]
        expected_capacity = [
            raw["source_capacity_current"],
            raw["source_capacity_total"],
            raw["source_waitlist_current"],
            raw["source_waitlist_total"],
        ]
        if values != expected_capacity:
            errors.append(f"{label}: capacity list/detail mismatch")
        try:
            fee_text, fee_amount = _fee(fields["수강료"])
            row["fee"] = fee_text
            row["fee_amount"] = fee_amount
            raw["source_fee"] = fee_text
        except Exception as exc:
            errors.append(_clean(exc))
        venue = _clean(fields["교육장"])
        if not venue:
            errors.append(f"{label}: education venue empty")
        row["venue"] = venue
        raw["source_venue"] = venue

        if row["status"] == "OPEN" and (
            apply_start is None
            or apply_end is None
            or not apply_start <= cutoff <= apply_end
        ):
            errors.append(f"{label}: open status/application period mismatch")
        if row["status"] == "SCHEDULED" and (
            apply_start is None or cutoff > apply_start
        ):
            errors.append(f"{label}: scheduled status/application period mismatch")

    controls = top.select(":scope > .top_btn > a.p-button.application[href]")
    if row["status"] == "OPEN":
        if len(controls) != 1 or "신청" not in _clean(controls[0].get_text(" ", strip=True)):
            errors.append(f"{label}: open course has no unique application control")
        else:
            application_url = _application_url(controls[0], identity, source)
            if not application_url:
                errors.append(f"{label}: application control is not safely identity-bound")
            else:
                row["application_url"] = application_url
                row["application_type"] = "ONLINE_RESERVATION"
                row["application_method"] = "온라인"
                row["application_methods"] = ["온라인"]
                row["reservation_available"] = True
                raw["application_control_present"] = True
                raw["application_control_contract"] = (
                    "official_https_same_host_lecture_bound_anchor"
                )
    elif controls:
        errors.append(f"{label}: inactive course exposes application control")
    else:
        raw["application_control_contract"] = (
            "inactive_detail_has_no_application_control"
        )
    raw["application_control_verified"] = not errors
    raw["detail_verified"] = not errors
    return row, errors


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            int(row.get("raw_fields", {}).get("source_ordinal") or 0),
            _clean(row.get("title")),
            _clean(row.get("period")),
            _clean(row.get("raw_fields", {}).get("source_status")),
            int(row.get("capacity_current") or 0),
            int(row.get("capacity_total") or 0),
        )
        for row in rows
    )


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url"}
        }
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("arbitrary detail description persisted")
    if _clean(row.get("raw_fields", {}).get("service_family")) != "education":
        errors.append("non-education row reached education persistence")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "configured_collection_error": error,
    }


def collect_donghae_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 40,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = DONGHAE_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future snapshot of all four catalogues."""

    meta = _base_meta()
    if not is_donghae_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Donghae education owner"
        )
        return [], DONGHAE_PARSER, meta
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages < 1
        or isinstance(detail_limit, bool)
        or not isinstance(detail_limit, int)
        or detail_limit < 0
        or isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    "invalid timeout/max_pages/detail_limit/max_workers cap"
                ),
            }
        )
        return [], DONGHAE_PARSER, meta
    try:
        cutoff = _cutoff(today)
    except ValueError as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], DONGHAE_PARSER, meta

    factory = session_factory or donghae_session_factory
    current_fetcher = fetcher or _default_fetcher
    errors: list[str] = []
    first_tasks = [
        (
            ("list", source.code, 1, "data"),
            donghae_list_url(1, cutoff.year, source),
            lambda soup, current=source: _parse_list(
                soup, 1, cutoff.year, current
            ),
        )
        for source in DONGHAE_CATALOGUES
    ]
    first_values, first_fetch_errors = _fetch_parse_many(
        first_tasks,
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(first_fetch_errors)
    meta["pages"] += len(first_values)
    meta["list_requests"] += len(first_values)

    states: dict[str, dict[str, Any]] = {}
    source_totals: dict[str, int] = {}
    source_pages: dict[str, int] = {}
    source_required_requests: dict[str, int] = {}
    for source in DONGHAE_CATALOGUES:
        first = first_values.get(("list", source.code, 1, "data"))
        if not isinstance(first, _ListPage):
            errors.append(f"{source.code} page 1: response missing")
            continue
        errors.extend(f"{source.code}: {item}" for item in first.errors)
        total = (
            int(first.rows[0]["raw_fields"]["source_ordinal"])
            if first.rows
            else 0
        )
        last = first.linked_last
        expected_last = max(1, math.ceil(total / DONGHAE_PAGE_SIZE))
        if last != expected_last:
            errors.append(
                f"{source.code} page 1: last-page link {last} "
                f"disagrees with ordinal total {total}"
            )
        stability_count = 2 if last > 1 else 1
        required = last + 1 + stability_count
        states[source.code] = {
            "source": source,
            "first": first,
            "total": total,
            "last": last,
            "stability_count": stability_count,
            "required": required,
        }
        source_totals[source.code] = total
        source_pages[source.code] = last
        source_required_requests[source.code] = required

    required_list_requests = sum(source_required_requests.values())
    meta.update(
        {
            "catalogue_year": cutoff.year,
            "source_totals": source_totals,
            "source_total": sum(source_totals.values()),
            "catalogue_data_pages": source_pages,
            "declared_pages": sum(source_pages.values()),
            "catalogue_required_list_requests": source_required_requests,
            "required_list_requests": required_list_requests,
        }
    )
    if len(states) != len(DONGHAE_CATALOGUES):
        errors.append("not all four catalogue first pages were available")
    if required_list_requests > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of "
                    f"{required_list_requests} required list requests"
                ),
            }
        )
        return [], DONGHAE_PARSER, meta
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], DONGHAE_PARSER, meta

    items: list[tuple[Any, str, Callable[[BeautifulSoup], Any]]] = []
    for state in states.values():
        source = state["source"]
        last = int(state["last"])
        for page in range(2, last + 1):
            items.append(
                (
                    ("list", source.code, page, "data"),
                    donghae_list_url(page, cutoff.year, source),
                    lambda soup, current_page=page, current=source: _parse_list(
                        soup, current_page, cutoff.year, current
                    ),
                )
            )
        items.extend(
            [
                (
                    ("list", source.code, last + 1, "sentinel"),
                    donghae_list_url(last + 1, cutoff.year, source),
                    lambda soup, current_page=last + 1, current=source: _parse_list(
                        soup, current_page, cutoff.year, current
                    ),
                ),
                (
                    ("list", source.code, 1, "recheck"),
                    donghae_list_url(1, cutoff.year, source),
                    lambda soup, current=source: _parse_list(
                        soup, 1, cutoff.year, current
                    ),
                ),
            ]
        )
        if last > 1:
            items.append(
                (
                    ("list", source.code, last, "recheck"),
                    donghae_list_url(last, cutoff.year, source),
                    lambda soup, current_page=last, current=source: _parse_list(
                        soup, current_page, cutoff.year, current
                    ),
                )
            )

    remaining, fetch_errors = _fetch_parse_many(
        items,
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)

    all_rows: list[dict[str, Any]] = []
    rows_by_catalogue: dict[str, list[dict[str, Any]]] = {}
    page_counts: dict[str, dict[int, int]] = {}
    official_unlisted_counts: dict[str, int] = {}
    sentinel_count = 0
    stability_count = 0
    for state in states.values():
        source = state["source"]
        first = state["first"]
        total = int(state["total"])
        last = int(state["last"])
        source_rows: list[dict[str, Any]] = []
        source_page_counts: dict[int, int] = {}
        signatures: dict[int, tuple[tuple[Any, ...], ...]] = {}
        official_unlisted_count = 0
        for page in range(1, last + 1):
            parsed = (
                first
                if page == 1
                else remaining.get(("list", source.code, page, "data"))
            )
            if not isinstance(parsed, _ListPage):
                errors.append(f"{source.code} page {page}: response missing")
                continue
            errors.extend(f"{source.code}: {item}" for item in parsed.errors)
            if parsed.linked_last != last:
                errors.append(
                    f"{source.code} page {page}: last-page navigation changed"
                )
            expected_count = min(
                DONGHAE_PAGE_SIZE,
                max(0, total - (page - 1) * DONGHAE_PAGE_SIZE),
            )
            terminal_gap = bool(
                page == last
                and last > 1
                and expected_count > 0
                and not parsed.rows
            )
            if terminal_gap:
                official_unlisted_count = expected_count
            elif len(parsed.rows) != expected_count:
                errors.append(
                    f"{source.code} page {page}: expected {expected_count} rows, "
                    f"got {len(parsed.rows)}"
                )
            high = total - (page - 1) * DONGHAE_PAGE_SIZE
            expected_ordinals = (
                []
                if terminal_gap
                else list(range(high, high - expected_count, -1))
            )
            actual_ordinals = [
                int(row["raw_fields"]["source_ordinal"])
                for row in parsed.rows
            ]
            if actual_ordinals != expected_ordinals:
                errors.append(
                    f"{source.code} page {page}: descending ordinal contract changed"
                )
            source_page_counts[page] = len(parsed.rows)
            signatures[page] = _page_signature(parsed.rows)
            source_rows.extend(parsed.rows)

        sentinel = remaining.get(
            ("list", source.code, last + 1, "sentinel")
        )
        if not isinstance(sentinel, _ListPage):
            errors.append(
                f"{source.code} page {last + 1}: empty sentinel response missing"
            )
        else:
            sentinel_count += 1
            errors.extend(f"{source.code}: {item}" for item in sentinel.errors)
            if sentinel.linked_last != last or sentinel.rows:
                errors.append(
                    f"{source.code} page {last + 1}: sentinel is not structurally empty"
                )
        for page in ({1, last} if last > 1 else {1}):
            recheck = remaining.get(
                ("list", source.code, page, "recheck")
            )
            if not isinstance(recheck, _ListPage):
                errors.append(
                    f"{source.code} page {page}: stability response missing"
                )
            else:
                stability_count += 1
                errors.extend(f"{source.code}: {item}" for item in recheck.errors)
                if (
                    recheck.linked_last != last
                    or _page_signature(recheck.rows) != signatures.get(page, ())
                ):
                    errors.append(
                        f"{source.code} page {page}: stability recheck changed"
                    )
        rows_by_catalogue[source.code] = source_rows
        page_counts[source.code] = source_page_counts
        official_unlisted_counts[source.code] = official_unlisted_count
        all_rows.extend(source_rows)

    meta["sentinel_requests"] = sentinel_count
    meta["stability_rechecks"] = stability_count
    identities = [_clean(row["raw_fields"]["identity"]) for row in all_rows]
    identity_duplicate_count = len(identities) - len(set(identities))
    if identity_duplicate_count:
        errors.append(
            f"{identity_duplicate_count} duplicate official identities across catalogues"
        )
    current_rows = [
        row
        for row in all_rows
        if date.fromisoformat(_clean(row["end_date"])) >= cutoff
    ]
    expected_stability = sum(
        int(state["stability_count"]) for state in states.values()
    )
    list_complete = bool(
        not errors
        and len(all_rows) + sum(official_unlisted_counts.values())
        == sum(source_totals.values())
        and meta["list_requests"] == required_list_requests
        and sentinel_count == len(DONGHAE_CATALOGUES)
        and stability_count == expected_stability
    )
    if len(current_rows) > detail_limit:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {detail_limit} of "
            f"{len(current_rows)} required current details"
        )

    detailed_rows: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    if list_complete and not errors:
        detail_items = [
            (
                ("detail", _clean(row["raw_fields"]["identity"])),
                _clean(row["raw_url"]),
                lambda soup, current=dict(row): _validate_detail(
                    current, soup, cutoff
                ),
            )
            for row in current_rows
        ]
        meta["detail_attempts"] = len(detail_items)
        details, detail_fetch_errors = _fetch_parse_many(
            detail_items,
            fetcher=current_fetcher,
            session_factory=factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        meta["pages"] += len(details)
        for listed in current_rows:
            identity = _clean(listed["raw_fields"]["identity"])
            value = details.get(("detail", identity))
            if not isinstance(value, tuple) or len(value) != 2:
                detail_errors.append(
                    f"course {identity}: detail response missing"
                )
                continue
            detailed, item_errors = value
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                detailed_rows.append(detailed)
                meta["detail_pages"] += 1
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        list_complete
        and meta["detail_attempts"] == len(current_rows)
        and meta["detail_pages"] == len(current_rows)
        and not detail_errors
    )
    application_controls_complete = bool(
        details_complete
        and all(
            bool(row["raw_fields"].get("application_control_verified"))
            for row in detailed_rows
        )
    )
    result: list[dict[str, Any]] = []
    if details_complete and application_controls_complete and not errors:
        for row in detailed_rows:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(detailed_rows))
            except Exception as exc:
                errors.append(f"dedupe failed: {_clean(exc)}")
            if len(result) != len(detailed_rows):
                errors.append(
                    "dedupe changed official identity cardinality "
                    f"{len(detailed_rows)} to {len(result)}"
                )
                result = []
            else:
                for row in result:
                    errors.extend(_privacy_errors(row))
                if errors:
                    result = []
    snapshot_complete = bool(
        list_complete
        and details_complete
        and application_controls_complete
        and not errors
    )
    if not snapshot_complete:
        result = []

    current_by_catalogue = Counter(
        _clean(row["raw_fields"]["source_catalogue"])
        for row in current_rows
    )
    returned_by_catalogue = Counter(
        _clean(row["raw_fields"]["source_catalogue"])
        for row in result
    )
    catalogue_snapshots: dict[str, dict[str, Any]] = {}
    for source in DONGHAE_CATALOGUES:
        source_rows = rows_by_catalogue.get(source.code, [])
        catalogue_snapshots[source.code] = {
            "name": source.name,
            "canonical_url": source.canonical_url,
            "source_total": len(source_rows),
            "advertised_total": source_totals.get(source.code, 0),
            "official_unlisted_count": official_unlisted_counts.get(
                source.code, 0
            ),
            "data_pages": source_pages.get(source.code, 0),
            "page_counts": page_counts.get(source.code, {}),
            "required_list_requests": source_required_requests.get(source.code, 0),
            "current_source_count": current_by_catalogue[source.code],
            "expired_count": len(source_rows) - current_by_catalogue[source.code],
            "returned_count": returned_by_catalogue[source.code],
            "source_status_counts": dict(
                Counter(
                    _clean(row["raw_fields"]["source_status"])
                    for row in source_rows
                )
            ),
        }
    meta.update(
        {
            "ownership_scope": DONGHAE_OWNERSHIP_SCOPE,
            "canonical_url": DONGHAE_CANONICAL_URL,
            "ownership_alias_urls": list(DONGHAE_OWNERSHIP_ALIAS_URLS),
            "catalogue_snapshots": catalogue_snapshots,
            "page_counts": page_counts,
            "source_rows": len(all_rows),
            "official_unlisted_counts": official_unlisted_counts,
            "official_unlisted_count": sum(official_unlisted_counts.values()),
            "current_source_count": len(current_rows),
            "expired_count": len(all_rows) - len(current_rows),
            "current_source_counts": {
                source.code: current_by_catalogue[source.code]
                for source in DONGHAE_CATALOGUES
            },
            "returned_counts": {
                source.code: returned_by_catalogue[source.code]
                for source in DONGHAE_CATALOGUES
            },
            "identity_duplicate_count": identity_duplicate_count,
            "source_status_counts": dict(
                Counter(
                    _clean(row["raw_fields"]["source_status"])
                    for row in all_rows
                )
            ),
            "current_status_counts": dict(
                Counter(_clean(row["status"]) for row in result)
            ),
            "branch_counts": dict(
                Counter(_clean(row["branch"]) for row in result)
            ),
            "venue_counts": dict(
                Counter(_clean(row["venue"]) for row in result)
            ),
            "online_open_count": sum(
                row.get("reservation_available") is True for row in result
            ),
            "application_control_count": sum(
                bool(row["raw_fields"].get("application_control_present"))
                for row in detailed_rows
            ),
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "application_controls_complete": application_controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "the four complete official year catalogues have no current/future courses"
                if snapshot_complete and not current_rows
                else ""
            ),
            "municipality_coverage": [DONGHAE_MUNICIPALITY_CODE],
            "superseded_provider": DONGHAE_SUPERSEDED_PROVIDER,
            "verified_tls": True,
            "tls_profile": (
                "CA+hostname verification with OpenSSL security level 1"
            ),
            "pii_fields_discarded": list(DONGHAE_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, DONGHAE_PARSER, meta


collect = collect_donghae_education


__all__ = [
    "DONGHAE_BRANCH",
    "DONGHAE_CANONICAL_CANDIDATE_ID",
    "DONGHAE_CANONICAL_URL",
    "DONGHAE_CATALOGUES",
    "DONGHAE_MUNICIPALITY_CODE",
    "DONGHAE_MUNICIPALITY_NAME",
    "DONGHAE_PARSER",
    "DONGHAE_PROVIDER",
    "DONGHAE_OWNERSHIP_ALIAS_URLS",
    "DONGHAE_SUPERSEDED_PROVIDER",
    "DonghaeCatalogue",
    "collect",
    "collect_donghae_education",
    "donghae_detail_url",
    "donghae_list_url",
    "donghae_session_factory",
    "is_donghae_education_target",
    "is_target",
]
