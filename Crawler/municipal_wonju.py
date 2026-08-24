"""Fail-closed collectors for Wonju's two independent public education owners.

The Wonju municipal reservation platform and the Gangwon Office of Education's
Wonju Education Culture Center are separate catalogues.  Existing providers are
retained for both owners; the disabled reservation-home alias must not execute as
another provider.

Only exact public list and detail GETs are allowed.  Application, login, my-page,
registration-check, attachment/download, warning, and applicant endpoints are
observed as controls but are never requested.  Contact, instructor, application
payload, attachment URL, image URL, and free-text content are never persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, TypeVar
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString


WONJU_MUNICIPAL_PROVIDER = "MUNI_WWW_WONJU_GO_KR_56B0C690"
WONJU_GWE_PROVIDER = "MUNI_LIB_GWE_GO_KR_5D9C27C1"
WONJU_DISABLED_HOME_ALIAS_PROVIDER = "MUNI_YEYAK_WONJU_GO_KR_5627044F"
WONJU_MUNICIPALITY_CODE = "5113000000"
WONJU_MUNICIPALITY_NAME = "강원특별자치도 원주시"

WONJU_MUNICIPAL_HOST = "yeyak.wonju.go.kr"
WONJU_MUNICIPAL_PATH = "/www/eduLectureAllWebList.do"
WONJU_MUNICIPAL_DETAIL_PATH = "/www/eduLectureWebView.do"
WONJU_MUNICIPAL_URL = f"https://{WONJU_MUNICIPAL_HOST}{WONJU_MUNICIPAL_PATH}?key=74"
WONJU_HOME_ALIAS_URL = f"https://{WONJU_MUNICIPAL_HOST}/"

WONJU_GWE_HOST = "lib.gwe.go.kr"
WONJU_GWE_LIST_PATH = "/wjecc/menu/4555/lecture-event/list/all"
WONJU_GWE_DETAIL_PREFIX = "/wjecc/menu/4555/lecture-event/"
WONJU_GWE_URL = f"https://{WONJU_GWE_HOST}{WONJU_GWE_LIST_PATH}"

WONJU_MUNICIPAL_PARSER = (
    "wonju_municipal_education_complete_75_pages+exact_page76_empty+"
    "all_current_details+official_institution_branches+stable_boundaries+"
    "application_login_mypage_attachment_no_fetch+pii_allowlist"
)
WONJU_GWE_PARSER = (
    "wonju_gwe_wjecc_complete_zero_based_pages+exact_page6_empty+"
    "all_current_details+training_shell_exclusion+stable_boundaries+"
    "application_login_registration_attachment_no_fetch+pii_allowlist"
)

WONJU_PAGE_SIZE = 8
WONJU_GWE_PAGE_SIZE = 10
WONJU_MAX_PAGES = 100
WONJU_MAX_DETAILS = 500
WONJU_RUNNER_MAX_PAGES = 2_000
WONJU_RUNNER_MAX_DETAILS = 3_000
WONJU_MAX_WORKERS = 4
WONJU_FETCH_ATTEMPTS = 2
WONJU_MAX_HTML_BYTES = 2_000_000

WONJU_GWE_TRAINING_ID = "8648"
WONJU_GWE_TRAINING_TITLE = "【수강신청 연습하기】"

WONJU_OWNER_AUDIT: Mapping[str, Mapping[str, str]] = {
    WONJU_MUNICIPAL_PROVIDER: {
        "url": WONJU_MUNICIPAL_URL,
        "state": "retain_and_replace_bounded_generic_collection",
        "owner": "wonju_municipal_integrated_reservation_education",
    },
    WONJU_GWE_PROVIDER: {
        "url": WONJU_GWE_URL,
        "state": "retain_and_replace_off_by_one_generic_collection",
        "owner": "gangwon_education_office_wonju_education_culture_center",
    },
    WONJU_DISABLED_HOME_ALIAS_PROVIDER: {
        "url": WONJU_HOME_ALIAS_URL,
        "state": "disabled_duplicate_discovery_shell",
        "canonical_provider": WONJU_MUNICIPAL_PROVIDER,
    },
}

WONJU_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "municipal_cutoff": "2026-07-23",
    "gwe_cutoff": "2026-07-28",
    "municipal": {
        "source": 597,
        "pages": 75,
        "final_size": 5,
        "current": 378,
        "branches": 21,
        "current_details": 378,
        "application_control_identities": 18,
    },
    "gwe": {
        "source": 51,
        "pages": 6,
        "final_size": 1,
        "date_current": 9,
        "excluded_training": 1,
        "emitted_current": 8,
        "current_details": 9,
        "application_control_identities_before_exclusion": 6,
    },
    "raw_identity_overlap": 0,
    "exact_title_overlap": 0,
}


class WonjuContractError(ValueError):
    """Raised when a live Wonju owner violates its audited public contract."""


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
T = TypeVar("T")

_SPACE = re.compile(r"\s+")
_ID = re.compile(r"^[1-9]\d*$")
_DATE_DASH = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_DATE_DOT = re.compile(r"(?<!\d)(\d{4})[.](\d{2})[.](\d{2})(?!\d)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}")
_RESIDENT_ID = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_MUNICIPAL_STATUS = {
    "접수중": "OPEN",
    "대기자접수": "WAITING",
    "추가모집": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "운영중": "CLOSED",
    "종료": "ENDED",
    "폐강": "CANCELLED",
}
_GWE_STATUS = {
    "접수중": "OPEN",
    "대기자접수": "WAITING",
    "접수예정": "SCHEDULED",
    "신청마감": "CLOSED",
    "종료": "ENDED",
}

_MUNICIPAL_DETAIL_SCHEMAS = frozenset(
    {
        (
            "운영기관", "년도/기수", "카테고리", "과목", "대상", "장소", "주소",
            "접수기간", "운영기간", "운영시간", "운영요일", "모집/신청",
            "이용요금", "교재비", "재료비", "신청방법", "선발방식", "문의전화",
            "강의계획서 정보제공",
        ),
        (
            "운영기관", "년도/기수", "카테고리", "과목", "대상", "장소", "주소",
            "접수기간", "운영기간", "운영시간", "운영요일", "모집/신청",
            "이용요금", "교재비", "재료비", "교재명", "신청방법", "선발방식",
            "문의전화", "강의계획서 정보제공",
        ),
        (
            "운영기관", "년도/기수", "카테고리", "과목", "대상", "장소", "주소",
            "접수기간", "운영기간", "운영시간", "운영요일", "모집/신청",
            "대기모집인원", "이용요금", "교재비", "재료비", "신청방법", "선발방식",
            "문의전화", "강의계획서 정보제공",
        ),
        (
            "운영기관", "년도/기수", "카테고리", "과목", "대상", "장소", "주소",
            "접수기간", "운영기간", "운영시간", "운영요일", "모집/신청",
            "대기모집인원", "이용요금", "교재비", "재료비", "교재명", "신청방법",
            "선발방식", "문의전화", "강의계획서 정보제공",
        ),
        (
            "운영기관", "년도/기수", "카테고리", "과목", "대상", "장소", "주소",
            "접수기간", "운영기간", "운영시간", "운영요일", "모집/신청",
            "이용요금", "기본할인", "특별할인", "교재비", "재료비", "신청방법",
            "선발방식", "문의전화", "강의계획서 정보제공",
        ),
    }
)
_GWE_DETAIL_SCHEMA = (
    "강사명", "도서관", "운영기간", "운영시간", "신청방법", "신청기간",
    "신청자격", "신청대상", "모집인원", "준비물", "재료비", "참가비", "장소",
)
_GWE_LIST_LABELS = ("신청기간", "운영기간", "신청대상", "모집방법", "모집인원")
_MUNICIPAL_LIST_LABELS = ("장소", "대상", "접수", "운영", "신청/정원(대기)")

_SAFE_RAW_FIELDS = frozenset(
    {
        "identity", "source_owner", "source_page", "source_position", "source_status",
        "source_region", "source_branch", "source_branch_code", "source_category",
        "source_period", "source_apply_period", "source_time", "source_weekdays",
        "source_application_method", "source_selection_method", "source_room",
        "source_capacity_current", "source_capacity_total", "source_wait_current",
        "source_wait_total", "source_fee", "source_material_fee", "source_target",
        "list_identity_verified", "detail_identity_verified", "detail_fields_verified",
        "application_control_present", "application_endpoint_fetched",
        "login_endpoint_fetched", "mypage_endpoint_fetched",
        "registration_endpoint_fetched", "attachment_endpoint_fetched",
        "download_endpoint_fetched", "applicant_endpoint_fetched",
        "application_form_submitted", "free_text_persisted", "discarded_fields",
        "non_user_apply_allowed", "service_family",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "phone", "email", "contact", "instructor", "teacher", "manager", "attachments",
        "attachment_url", "download_url", "image_url", "body", "content_html", "guide",
        "notice", "applicant_name", "resident_number",
    }
)
_DISCARDED_FIELDS = (
    "문의전화·대표전화·이메일", "강사명", "강의계획서·첨부파일·이미지 URL",
    "상세 자유서술·준비물·교재명", "신청자·로그인·마이페이지·등록확인 payload",
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _exact_url(value: str, expected: str) -> bool:
    left, right = urlparse(_clean(value)), urlparse(expected)
    return (
        left.scheme == right.scheme
        and left.netloc == right.netloc
        and left.path == right.path
        and left.params == right.params == ""
        and left.fragment == right.fragment == ""
        and parse_qsl(left.query, keep_blank_values=True)
        == parse_qsl(right.query, keep_blank_values=True)
    )


def owner_for_target(target: Any) -> str:
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    if provider == WONJU_MUNICIPAL_PROVIDER and _exact_url(url, WONJU_MUNICIPAL_URL):
        return "municipal"
    if provider == WONJU_GWE_PROVIDER and _exact_url(url, WONJU_GWE_URL):
        return "gwe"
    return ""


def is_wonju_education_target(target: Any) -> bool:
    return bool(owner_for_target(target))


is_target = is_wonju_education_target


def municipal_list_url(page: int) -> str:
    return f"https://{WONJU_MUNICIPAL_HOST}{WONJU_MUNICIPAL_PATH}?" + urlencode(
        (("key", "74"), ("pageUnit", str(WONJU_PAGE_SIZE)), ("searchCnd", "all"), ("pageIndex", str(page)))
    )


def gwe_list_url(page_index: int) -> str:
    if page_index == 0:
        return WONJU_GWE_URL
    return f"{WONJU_GWE_URL}?{urlencode((('page', str(page_index)),))}"


def _raw_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _query(url: str) -> tuple[Any, list[tuple[str, str]], dict[str, str]]:
    parsed = urlparse(url)
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
    except ValueError as exc:
        raise WonjuContractError("malformed query") from exc
    if len(pairs) != len({key for key, _ in pairs}):
        raise WonjuContractError("duplicate query key")
    return parsed, pairs, dict(pairs)


def _validate_fetch_url(url: str, owner: str) -> str:
    parsed, pairs, values = _query(url)
    expected_host = WONJU_MUNICIPAL_HOST if owner == "municipal" else WONJU_GWE_HOST
    if parsed.scheme != "https" or parsed.netloc != expected_host or parsed.params or parsed.fragment:
        raise WonjuContractError("request escaped exact HTTPS owner")
    if owner == "municipal":
        if parsed.path == WONJU_MUNICIPAL_PATH:
            if set(values) != {"key", "pageUnit", "searchCnd", "pageIndex"}:
                raise WonjuContractError("municipal list query drift")
            if values["key"] != "74" or values["pageUnit"] != "8" or values["searchCnd"] != "all":
                raise WonjuContractError("municipal unfiltered list binding drift")
            if not _ID.fullmatch(values["pageIndex"]):
                raise WonjuContractError("municipal page binding drift")
            return "list"
        if parsed.path == WONJU_MUNICIPAL_DETAIL_PATH:
            if set(values) != {"key", "prgNo", "pageUnit", "pageIndex", "searchCnd"}:
                raise WonjuContractError("municipal detail query drift")
            if (
                values["key"] != "74"
                or values["pageUnit"] != "8"
                or values["searchCnd"] != "all"
                or not _ID.fullmatch(values["pageIndex"])
                or not _ID.fullmatch(values["prgNo"])
            ):
                raise WonjuContractError("municipal detail identity/page binding drift")
            return "detail"
        raise WonjuContractError("request escaped municipal list/detail paths")
    if parsed.path == WONJU_GWE_LIST_PATH:
        if pairs:
            if set(values) != {"page"} or not values["page"].isdigit() or int(values["page"]) < 1:
                raise WonjuContractError("GWE zero-based page binding drift")
        return "list"
    if parsed.path.startswith(WONJU_GWE_DETAIL_PREFIX):
        suffix = parsed.path[len(WONJU_GWE_DETAIL_PREFIX):]
        if pairs or not _ID.fullmatch(suffix):
            raise WonjuContractError("GWE detail identity binding drift")
        return "detail"
    raise WonjuContractError("request escaped GWE list/detail paths")


def _validate_owner_shell(soup: BeautifulSoup, owner: str) -> None:
    if owner == "municipal":
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        body = soup.body
        address = soup.select_one("footer address")
        if (
            "원주시 통합예약플랫폼" not in title
            or body is None
            or body.get("id") != "www"
            or "page74" not in (body.get("class") or [])
            or address is None
            or "강원특별자치도 원주시 시청로1" not in _clean(address.get_text(" ", strip=True))
        ):
            raise WonjuContractError("municipal official owner shell drift")
        return
    address = soup.select_one("footer .footer_info address, .footer_info address")
    text = _clean(address.get_text(" ", strip=True) if address else "")
    if "강원특별자치도 원주시 북원로 2312" not in text or "원주교육문화관" not in text:
        raise WonjuContractError("GWE official owner shell drift")


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
    owner: str,
) -> tuple[BeautifulSoup, int, str]:
    kind = _validate_fetch_url(url, owner)
    last_error: Optional[BaseException] = None
    for attempt in range(1, WONJU_FETCH_ATTEMPTS + 1):
        try:
            response = fetcher(session, url, timeout)
            if int(getattr(response, "status_code", 0)) != 200:
                raise requests.RequestException(f"HTTP {getattr(response, 'status_code', 0)}")
            if getattr(response, "history", []):
                raise WonjuContractError("redirect history is not allowed")
            if not _exact_url(_clean(getattr(response, "url", "")), url):
                raise WonjuContractError("response URL drift")
            content = getattr(response, "content", b"")
            if not isinstance(content, (bytes, bytearray)) or not content or len(content) > WONJU_MAX_HTML_BYTES:
                raise WonjuContractError("response size outside audited bounds")
            try:
                html = bytes(content).decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise WonjuContractError("response is not strict UTF-8") from exc
            soup = BeautifulSoup(html, "html.parser")
            _validate_owner_shell(soup, owner)
            return soup, attempt, kind
        except WonjuContractError:
            raise
        except requests.RequestException as exc:
            last_error = exc
    raise WonjuContractError(f"request failed after retries: {_clean(last_error)}")


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Asia/Seoul")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("today must be date, datetime, ISO date string, or None")


def _dates(value: str, pattern: re.Pattern[str], identity: str, label: str) -> tuple[date, date]:
    matches = pattern.findall(_clean(value))
    if len(matches) != 2:
        raise WonjuContractError(f"{identity}: {label} date range drift")
    try:
        start, end = (date(*(int(part) for part in match)) for match in matches)
    except ValueError as exc:
        raise WonjuContractError(f"{identity}: {label} invalid date") from exc
    if start > end:
        raise WonjuContractError(f"{identity}: {label} reversed date range")
    return start, end


def _direct_pairs(node: Any, key_selector: str, value_selector: str, identity: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    keys = node.select(key_selector)
    values = node.select(value_selector)
    if len(keys) != len(values):
        raise WonjuContractError(f"{identity}: structured pair cardinality drift")
    for key, value in zip(keys, values):
        pairs.append((_clean(key.get_text(" ", strip=True)), _clean(value.get_text(" ", strip=True))))
    if len({key for key, _ in pairs}) != len(pairs):
        raise WonjuContractError(f"{identity}: duplicate structured label")
    return pairs


def _municipal_detail_href(base_url: str, href: str, page: int) -> tuple[str, str]:
    absolute = urljoin(base_url, href)
    if _validate_fetch_url(absolute, "municipal") != "detail":
        raise WonjuContractError("municipal card did not bind a detail")
    _, _, values = _query(absolute)
    if values["pageIndex"] != str(page):
        raise WonjuContractError("municipal detail source-page drift")
    return values["prgNo"], absolute


def _municipal_info(item: Any, identity: str) -> dict[str, str]:
    pairs: list[tuple[str, str]] = []
    for node in item.select(
        ":scope > a.thumbnail_anchor > .thumbnail_content > .info > .info_item"
    ):
        label = node.select_one(":scope > .info_sub")
        if label is None:
            raise WonjuContractError(f"{identity}: municipal list label missing")
        name = _clean(label.get_text(" ", strip=True))
        label.extract()
        pairs.append((name, _clean(node.get_text(" ", strip=True))))
    if tuple(key for key, _ in pairs) != _MUNICIPAL_LIST_LABELS:
        raise WonjuContractError(f"{identity}: municipal list fields drift")
    return dict(pairs)


def _municipal_card(item: Any, page: int, position: int, list_url: str) -> dict[str, Any]:
    anchor = item.select_one(":scope > a.thumbnail_anchor[href]")
    title_node = item.select_one(".thumbnail_sub")
    status_node = item.select_one(".stat")
    region_node = item.select_one(".place")
    price_node = item.select_one(".price")
    if any(node is None for node in (anchor, title_node, status_node, region_node, price_node)):
        raise WonjuContractError("municipal list card structure drift")
    identity, detail_url = _municipal_detail_href(list_url, str(anchor.get("href", "")), page)
    title = _clean(title_node.get_text(" ", strip=True))
    status = _clean(status_node.get_text(" ", strip=True))
    if not title or status not in _MUNICIPAL_STATUS:
        raise WonjuContractError(f"{identity}: municipal title/status drift")
    fields = _municipal_info(item, identity)
    event_start, event_end = _dates(fields["운영"], _DATE_DASH, identity, "operation")
    apply_start, apply_end = _dates(fields["접수"], _DATE_DASH, identity, "application")
    capacity_match = re.fullmatch(
        r"(\d[\d,]*)\s*/\s*(\d[\d,]*)\s*[(](\d[\d,]*)\s*/\s*(\d[\d,]*)?[)]",
        fields["신청/정원(대기)"],
    )
    if capacity_match is None:
        raise WonjuContractError(f"{identity}: municipal list capacity drift")
    counts = tuple(int((value or "0").replace(",", "")) for value in capacity_match.groups())
    return {
        "owner": "municipal", "identity": identity, "page": page, "position": position,
        "title": title, "raw_status": status, "region": _clean(region_node.get_text(" ", strip=True)),
        "fee": _clean(price_node.get_text(" ", strip=True)), "room": fields["장소"],
        "target": fields["대상"], "event_period": fields["운영"],
        "event_start": event_start, "event_end": event_end, "apply_period": fields["접수"],
        "apply_start": apply_start, "apply_end": apply_end,
        "list_capacity_current": counts[0], "list_capacity_total": counts[1],
        "list_wait_current": counts[2], "list_wait_total": counts[3], "detail_url": detail_url,
    }


def _municipal_institutions(soup: BeautifulSoup) -> dict[str, str]:
    select = soup.select_one('form.search.detail select[name="schInstt"]')
    if select is None:
        raise WonjuContractError("municipal institution registry missing")
    pairs = [
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in select.find_all("option", recursive=False)
    ]
    if not pairs or pairs[0][0] != "" or len(pairs) != len({value for value, _ in pairs}):
        raise WonjuContractError("municipal institution registry drift")
    registry = {value: text for value, text in pairs[1:]}
    if not registry or any(not _ID_INSTITUTION.fullmatch(value) or not text for value, text in registry.items()):
        raise WonjuContractError("municipal institution registry values drift")
    return registry


_ID_INSTITUTION = re.compile(r"^IN\d{8}$")


def _parse_municipal_page(soup: BeautifulSoup, page: int, url: str) -> dict[str, Any]:
    count = soup.select_one(".bbs_page .item.count em")
    page_info = soup.select_one(".bbs_page .item.page em")
    last = soup.select_one(".p-page .next-end[href]")
    if count is None or page_info is None or last is None:
        raise WonjuContractError("municipal count/pagination contract missing")
    if not _clean(count.get_text()).isdigit():
        raise WonjuContractError("municipal total count drift")
    match = re.fullmatch(r"([1-9]\d*)/([1-9]\d*)", _clean(page_info.get_text()))
    if match is None or int(match.group(1)) != page:
        raise WonjuContractError("municipal requested/displayed page drift")
    _, _, last_values = _query(urljoin(url, str(last.get("href", ""))))
    if (
        _validate_fetch_url(urljoin(url, str(last.get("href", ""))), "municipal") != "list"
        or int(last_values["pageIndex"]) != int(match.group(2))
    ):
        raise WonjuContractError("municipal last-page control drift")
    cards = soup.select("ul.thumbnail_list > li.thumbnail_item, .thumbnail_list li.thumbnail_item")
    # The live template does not always name the wrapping list; exact card class remains stable.
    if not cards:
        cards = soup.select("li.thumbnail_item")
    empty_nodes = soup.select(".p-empty > .inner > .tit")
    if cards and empty_nodes:
        raise WonjuContractError("municipal mixed list/empty page")
    rows = [
        _municipal_card(card, page, position, url)
        for position, card in enumerate(cards, 1)
    ]
    empty = not rows
    if empty and (
        len(empty_nodes) != 2
        or any(_clean(node.get_text(" ", strip=True)) != "검색하신 내용을 찾을 수 없습니다." for node in empty_nodes)
    ):
        raise WonjuContractError("municipal exact empty sentinel drift")
    active = soup.select(".p-page__link.active")
    if page <= int(match.group(2)):
        if len(active) != 1 or _clean(active[0].get_text()) != str(page):
            raise WonjuContractError("municipal active page drift")
    elif active:
        raise WonjuContractError("municipal sentinel unexpectedly has active page")
    return {
        "owner": "municipal", "page": page, "total": int(count.get_text(strip=True)),
        "last": int(match.group(2)), "rows": rows, "empty": empty,
        "institutions": _municipal_institutions(soup),
    }


def _table_pairs(table: Any, identity: str) -> tuple[tuple[str, ...], dict[str, str]]:
    pairs: list[tuple[str, str]] = []
    for row in table.select(":scope > tbody > tr"):
        th = row.select_one(":scope > th")
        td = row.select_one(":scope > td")
        if th is None or td is None:
            raise WonjuContractError(f"{identity}: detail row structure drift")
        pairs.append((_clean(th.get_text(" ", strip=True)), _clean(td.get_text(" ", strip=True))))
    labels = tuple(key for key, _ in pairs)
    if len(labels) != len(set(labels)):
        raise WonjuContractError(f"{identity}: duplicate detail label")
    return labels, dict(pairs)


def _municipal_branch_code(registry: Mapping[str, str], branch: str, identity: str) -> str:
    matches = [code for code, name in registry.items() if name == branch]
    if len(matches) != 1:
        raise WonjuContractError(f"{identity}: official institution registry disagreement")
    return matches[0]


def _municipal_detail(
    soup: BeautifulSoup,
    expected: Mapping[str, Any],
    registry: Mapping[str, str],
) -> dict[str, Any]:
    identity = str(expected["identity"])
    root = soup.select_one(".program.program_view.edu")
    status_node = root.select_one(":scope > .view_topbox > .stat") if root else None
    title_node = root.select_one(".view_topbox .topbox_sub") if root else None
    table = root.select_one("table.table.type2") if root else None
    if root is None or status_node is None or title_node is None or table is None:
        raise WonjuContractError(f"{identity}: municipal detail structure drift")
    status = _clean(status_node.get_text(" ", strip=True))
    title = _clean(title_node.get_text(" ", strip=True))
    labels, fields = _table_pairs(table, identity)
    if labels not in _MUNICIPAL_DETAIL_SCHEMAS:
        raise WonjuContractError(f"{identity}: municipal detail schema drift")
    event_start, event_end = _dates(fields["운영기간"], _DATE_DASH, identity, "detail operation")
    apply_start, apply_end = _dates(fields["접수기간"], _DATE_DASH, identity, "detail application")
    if (
        title != expected["title"]
        or status != expected["raw_status"]
        or event_start != expected["event_start"]
        or event_end != expected["event_end"]
        or apply_start != expected["apply_start"]
        or apply_end != expected["apply_end"]
        or fields["대상"] != expected["target"]
        or fields["장소"] != expected["room"]
    ):
        raise WonjuContractError(f"{identity}: municipal list/detail disagreement")
    branch = fields["운영기관"]
    branch_code = _municipal_branch_code(registry, branch, identity)
    capacity_text = _clean(fields["모집/신청"])
    capacity_match = re.fullmatch(
        r"모집인원\s*:\s*(\d[\d,]*)\s*명,?\s*(?:신청인원\s*:\s*(\d[\d,]*)\s*명|"
        r"온라인신청\s*:\s*(\d[\d,]*)\s*명,?\s*오프라인신청\s*:\s*(\d[\d,]*)\s*명)",
        capacity_text,
    )
    if capacity_match is None:
        raise WonjuContractError(f"{identity}: municipal detail capacity drift")
    capacity_total = int(capacity_match.group(1).replace(",", ""))
    if capacity_match.group(2) is not None:
        capacity_current = int(capacity_match.group(2).replace(",", ""))
    else:
        capacity_current = sum(
            int(value.replace(",", "")) for value in capacity_match.groups()[2:]
        )
    wait_current = wait_total = 0
    if "대기모집인원" in fields:
        wait_match = re.search(
            r"대기모집인원\s*:\s*(\d[\d,]*)명.*대기신청인원\s*:\s*(\d[\d,]*)",
            fields["대기모집인원"],
        )
        if wait_match is None:
            raise WonjuContractError(f"{identity}: municipal waiting capacity drift")
        wait_total, wait_current = (int(value.replace(",", "")) for value in wait_match.groups())
    apply_links = root.select('a[href*="eduApplicantWebAgree.do"]')
    if len(apply_links) not in {0, 2}:
        raise WonjuContractError(f"{identity}: municipal application control cardinality drift")
    for anchor in apply_links:
        absolute = urljoin(str(expected["detail_url"]), str(anchor.get("href", "")))
        parsed, _, values = _query(absolute)
        if (
            parsed.scheme != "https"
            or parsed.netloc != WONJU_MUNICIPAL_HOST
            or parsed.path != "/www/eduApplicantWebAgree.do"
            or parsed.params
            or parsed.fragment
            or values != {"key": "74", "prgNo": identity}
            or _clean(anchor.get("onclick")) != "fn_aplcnt(this.href); return false;"
        ):
            raise WonjuContractError(f"{identity}: municipal application endpoint binding drift")
    mypage_links = root.select('a[href*="eduApplicantMypageList.do"]')
    if len(mypage_links) != 2:
        raise WonjuContractError(f"{identity}: municipal my-page control drift")
    actionable = status in {"접수중", "대기자접수", "추가모집"}
    method = fields["신청방법"]
    if apply_links and (not actionable or "온라인" not in method):
        raise WonjuContractError(f"{identity}: municipal online control/status drift")
    if actionable and not apply_links and "온라인" in method:
        raise WonjuContractError(f"{identity}: municipal actionable online control missing")
    downloads = root.select('a[href*="downloadEduLectureFile.do"]')
    return {
        "identity": identity, "branch": branch, "branch_code": branch_code,
        "category": fields["카테고리"], "subject": fields["과목"], "address": fields["주소"],
        "period": fields["운영기간"], "apply_period": fields["접수기간"],
        "time": fields["운영시간"], "weekdays": fields["운영요일"],
        "capacity_total": capacity_total, "capacity_current": capacity_current,
        "wait_total": wait_total, "wait_current": wait_current, "fee": fields["이용요금"],
        "material_fee": fields["재료비"], "application_method": method,
        "selection_method": fields["선발방식"], "application_control_count": len(apply_links),
        "mypage_control_count": len(mypage_links), "attachment_count": len(downloads),
        "discarded_phone_present": bool(fields["문의전화"]),
    }


def _gwe_detail_href(base_url: str, href: str) -> tuple[str, str]:
    absolute = urljoin(base_url, href)
    if _validate_fetch_url(absolute, "gwe") != "detail":
        raise WonjuContractError("GWE card did not bind a detail")
    return urlparse(absolute).path.rsplit("/", 1)[-1], absolute


def _gwe_capacity(value: str, identity: str) -> tuple[int, int, int, int]:
    match = re.search(
        r"(\d[\d,]*)\s*/\s*(\d[\d,]*).*?대기자\s*:\s*(\d[\d,]*)\s*/\s*(\d[\d,]*)",
        _clean(value),
    )
    if match is None:
        raise WonjuContractError(f"{identity}: GWE capacity drift")
    current, total, wait_current, wait_total = (
        int(value.replace(",", "")) for value in match.groups()
    )
    return current, total, wait_current, wait_total


def _gwe_card(item: Any, page: int, position: int, list_url: str) -> dict[str, Any]:
    title_anchor = item.select_one(".lecture_item__title > a[href]")
    branch_node = item.select_one(".lecture_item__library")
    controls = item.select(".lecture_item__button > button")
    registration_controls = [
        control
        for control in controls
        if "registrationCheckButton" in (control.get("class") or [])
    ]
    primary_controls = [
        control
        for control in controls
        if "registrationCheckButton" not in (control.get("class") or [])
    ]
    if (
        title_anchor is None
        or branch_node is None
        or len(registration_controls) != 1
        or len(primary_controls) > 1
    ):
        raise WonjuContractError("GWE list card structure drift")
    identity, detail_url = _gwe_detail_href(list_url, str(title_anchor.get("href", "")))
    title = _clean(title_anchor.get_text(" ", strip=True))
    registration = registration_controls[0]
    if not title:
        raise WonjuContractError(f"{identity}: GWE title/status drift")
    registration_title = _clean(registration.get("data-event-title"))
    if (
        _clean(registration.get("data-event-id")) != identity
        or registration_title != title
        or not _clean(registration.get("data-category-name"))
    ):
        raise WonjuContractError(f"{identity}: GWE registration-check binding drift")
    pairs = _direct_pairs(item, ".lecture_item__info > dt", ".lecture_item__info > dd", identity)
    if tuple(key for key, _ in pairs) != _GWE_LIST_LABELS:
        raise WonjuContractError(f"{identity}: GWE list field schema drift")
    fields = dict(pairs)
    event_start, event_end = _dates(fields["운영기간"], _DATE_DOT, identity, "GWE operation")
    apply_start, apply_end = _dates(fields["신청기간"], _DATE_DOT, identity, "GWE application")
    current, total, wait_current, wait_total = _gwe_capacity(fields["모집인원"], identity)
    status_inferred = not primary_controls
    if status_inferred:
        if current < total or wait_current < wait_total:
            raise WonjuContractError(f"{identity}: GWE missing status control without full capacity")
        status = "신청마감"
        open_control = False
    else:
        primary = primary_controls[0]
        status = _clean(primary.get_text(" ", strip=True))
        if status not in _GWE_STATUS:
            raise WonjuContractError(f"{identity}: GWE title/status drift")
        expected_action_class = {
            "접수중": "applyStatusButton",
            "대기자접수": "reserveStatusApplyButton",
        }.get(status)
        open_control = expected_action_class is not None
        if open_control:
            if (
                primary.has_attr("disabled")
                or expected_action_class not in (primary.get("class") or [])
            ):
                raise WonjuContractError(f"{identity}: GWE application control/status drift")
            if (
                _clean(primary.get("data-event-id")) != identity
                or _clean(primary.get("data-event-title")) != title
            ):
                raise WonjuContractError(f"{identity}: GWE application identity drift")
        elif not primary.has_attr("disabled"):
            raise WonjuContractError(f"{identity}: GWE disabled status control drift")
    return {
        "owner": "gwe", "identity": identity, "page": page, "position": position,
        "title": title, "raw_status": status, "branch": _clean(branch_node.get_text(" ", strip=True)),
        "category": _clean(registration.get("data-category-name")),
        "event_period": fields["운영기간"], "event_start": event_start, "event_end": event_end,
        "apply_period": fields["신청기간"], "apply_start": apply_start, "apply_end": apply_end,
        "target": fields["신청대상"], "application_method": fields["모집방법"],
        "capacity_current": current, "capacity_total": total,
        "wait_current": wait_current, "wait_total": wait_total,
        "status_inferred_from_full_capacity": status_inferred,
        "list_application_control": open_control, "detail_url": detail_url,
    }


def _parse_gwe_page(soup: BeautifulSoup, page: int, url: str) -> dict[str, Any]:
    count = soup.select_one(".lecture_result_top__count > strong")
    if count is None or not _clean(count.get_text()).isdigit():
        raise WonjuContractError("GWE total count drift")
    total = int(_clean(count.get_text()))
    declared_last = max(1, (total + WONJU_GWE_PAGE_SIZE - 1) // WONJU_GWE_PAGE_SIZE)
    items = soup.select(".lecture_result_list > li.lecture_item")
    empty_node = soup.select_one(".lecture_result_list > li.no_data")
    if items and empty_node is not None:
        raise WonjuContractError("GWE mixed list/empty page")
    rows = [_gwe_card(item, page, position, url) for position, item in enumerate(items, 1)]
    if not rows and (
        empty_node is None
        or _clean(empty_node.get_text(" ", strip=True)) != "조회되는 문화강좌가 없습니다."
    ):
        raise WonjuContractError("GWE exact empty sentinel drift")
    current = soup.select(".paging_container > .current[data-page-no]")
    buttons = soup.select(".paging_container > button.goPage[data-page-no]")
    if page < declared_last:
        if len(current) != 1 or _clean(current[0].get("data-page-no")) != str(page):
            raise WonjuContractError("GWE active zero-based page drift")
        values = [(int(current[0]["data-page-no"]), int(_clean(current[0].get_text())))] + [
            (int(button["data-page-no"]), int(_clean(button.get_text()))) for button in buttons
        ]
        if sorted(values) != [(index, index + 1) for index in range(declared_last)]:
            raise WonjuContractError("GWE visible pager registry drift")
    elif current or buttons:
        raise WonjuContractError("GWE sentinel unexpectedly has pager")
    return {"owner": "gwe", "page": page, "total": total, "last": declared_last, "rows": rows, "empty": not rows}


def _direct_title(node: Any) -> str:
    return _clean(" ".join(str(child) for child in node.contents if isinstance(child, NavigableString)))


def _gwe_detail(soup: BeautifulSoup, expected: Mapping[str, Any]) -> dict[str, Any]:
    identity = str(expected["identity"])
    root = soup.select_one("article.lecture_detail")
    title_node = root.select_one(":scope > .lecture_detail__title") if root else None
    status_node = title_node.select_one(":scope > span > .lecture_detail__status") if title_node else None
    fields_node = root.select_one(":scope > .lecture_detail__info > dl.lecture_detail__dl") if root else None
    if root is None or title_node is None or fields_node is None:
        raise WonjuContractError(f"{identity}: GWE detail structure drift")
    title = _direct_title(title_node)
    if status_node is None:
        if not expected.get("status_inferred_from_full_capacity"):
            raise WonjuContractError(f"{identity}: GWE detail status drift")
        status = "신청마감"
    else:
        status = _clean(status_node.get_text(" ", strip=True))
    pairs = _direct_pairs(fields_node, ":scope > dt", ":scope > dd", identity)
    if tuple(key for key, _ in pairs) != _GWE_DETAIL_SCHEMA:
        raise WonjuContractError(f"{identity}: GWE detail schema drift")
    fields = dict(pairs)
    event_start, event_end = _dates(fields["운영기간"], _DATE_DOT, identity, "GWE detail operation")
    apply_start, apply_end = _dates(fields["신청기간"], _DATE_DOT, identity, "GWE detail application")
    if (
        title != expected["title"]
        or status != expected["raw_status"]
        or fields["도서관"] != expected["branch"]
        or event_start != expected["event_start"]
        or event_end != expected["event_end"]
        or apply_start != expected["apply_start"]
        or apply_end != expected["apply_end"]
        or fields["신청대상"] != expected["target"]
    ):
        raise WonjuContractError(f"{identity}: GWE list/detail disagreement")
    current, total, wait_current, wait_total = _gwe_capacity(fields["모집인원"], identity)
    if (current, total, wait_current, wait_total) != (
        expected["capacity_current"], expected["capacity_total"],
        expected["wait_current"], expected["wait_total"],
    ):
        raise WonjuContractError(f"{identity}: GWE list/detail capacity disagreement")
    container = root.find_next_sibling("div", class_="btn_container")
    apply_controls = container.select(".btn_ico_apply[data-event-id]") if container else []
    if len(apply_controls) not in {0, 1}:
        raise WonjuContractError(f"{identity}: GWE detail application cardinality drift")
    if bool(apply_controls) != bool(expected["list_application_control"]):
        raise WonjuContractError(f"{identity}: GWE list/detail application control disagreement")
    non_user_allowed = False
    if apply_controls:
        button = apply_controls[0]
        non_user = _clean(button.get("data-non-user-apply-yn"))
        expected_button_id = {
            "접수중": "applyButton",
            "대기자접수": "reserveApplyButton",
        }.get(status)
        if (
            _clean(button.get("id")) != expected_button_id
            or expected_button_id is None
            or _clean(button.get("data-event-id")) != identity
            or _clean(button.get("data-event-title")) != title
            or non_user not in {"Y", "N"}
        ):
            raise WonjuContractError(f"{identity}: GWE detail application identity drift")
        non_user_allowed = non_user == "Y"
    return {
        "identity": identity, "branch": fields["도서관"], "period": fields["운영기간"],
        "apply_period": fields["신청기간"], "time": fields["운영시간"],
        "application_method": fields["신청방법"], "target": fields["신청대상"],
        "capacity_current": current, "capacity_total": total,
        "wait_current": wait_current, "wait_total": wait_total,
        "material_fee": fields["재료비"], "fee": fields["참가비"], "room": fields["장소"],
        "application_control_count": len(apply_controls),
        "non_user_apply_allowed": non_user_allowed,
        "attachment_count": len(root.select("button.btnDownload[data-id]")),
        "discarded_content_blocks": len(root.select(":scope > .lecture_detail__content")),
    }


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        row.get(key)
        for key in (
            "identity", "title", "raw_status", "event_period", "apply_period", "target",
            "capacity_current", "capacity_total", "wait_current", "wait_total", "detail_url",
        )
    )


def _page_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return (page["owner"], page["total"], page["last"], page["empty"], tuple(_row_signature(row) for row in page["rows"]))


def _fee_amount(value: str) -> Optional[int]:
    cleaned = _clean(value)
    if cleaned in {"", "-", "무료", "없음", "해당없음", "0", "0원", "0 원"}:
        return 0
    match = re.fullmatch(r"(\d[\d,]*)\s*원", cleaned)
    return int(match.group(1).replace(",", "")) if match else None


def _municipal_output(row: Mapping[str, Any]) -> dict[str, Any]:
    detail = row["detail"]
    status = _MUNICIPAL_STATUS[str(row["raw_status"])]
    has_control = bool(detail["application_control_count"])
    actionable = status in {"OPEN", "WAITING"}
    offline = actionable and not has_control
    application_type = "ONLINE_RESERVATION" if has_control else "OFFLINE_APPLY" if offline else "INFO_ONLY"
    branch_code = str(detail["branch_code"])
    branch_url = f"https://{WONJU_MUNICIPAL_HOST}{WONJU_MUNICIPAL_PATH}?" + urlencode(
        (("key", "74"), ("schInstt", branch_code), ("pageUnit", "8"), ("searchCnd", "all"), ("pageIndex", "1"))
    )
    return {
        "provider": WONJU_MUNICIPAL_PROVIDER,
        "provider_course_id": f"{WONJU_MUNICIPAL_PROVIDER}:prgNo:{row['identity']}",
        "prefer_incoming_provider_course_id": True,
        "title": str(row["title"]), "description": str(row["title"]),
        "branch": str(detail["branch"]), "branch_code": branch_code, "branch_url": branch_url,
        "preserve_branch": True, "category": str(detail["category"]), "program_type": "교육",
        "raw_url": str(row["detail_url"]),
        "application_url": str(row["detail_url"]) if actionable else "",
        "application_type": application_type, "application_method": str(detail["application_method"]),
        "application_methods": [str(detail["application_method"])],
        "reservation_available": actionable, "status": status, "raw_status": str(row["raw_status"]),
        "fee": str(detail["fee"]), "fee_amount": _fee_amount(str(detail["fee"])),
        "material_fee": str(detail["material_fee"]),
        "material_fee_amount": _fee_amount(str(detail["material_fee"])),
        "period": str(detail["period"]), "start_date": row["event_start"].isoformat(),
        "end_date": row["event_end"].isoformat(), "apply_period": str(detail["apply_period"]),
        "apply_start_date": row["apply_start"].isoformat(),
        "apply_end_date": row["apply_end"].isoformat(),
        "schedule_raw": _clean(f"{detail['weekdays']} {detail['time']}"),
        "capacity": f"{detail['capacity_total']}명", "capacity_current": int(detail["capacity_current"]),
        "capacity_total": int(detail["capacity_total"]),
        "capacity_remaining": max(int(detail["capacity_total"]) - int(detail["capacity_current"]), 0),
        "target": str(row["target"]), "venue": str(detail["branch"]),
        "venue_name": str(detail["branch"]), "room": str(row["room"]),
        "facility_name": str(detail["branch"]), "address": str(detail["address"]),
        "venue_address": str(detail["address"]), "collection_category": "공공예약",
        "domain_category": "교육·강좌", "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation", "service_group": "공공강좌",
        "service_group_policy": "locked", "collection_type": WONJU_MUNICIPAL_PARSER,
        "municipality_code": WONJU_MUNICIPALITY_CODE,
        "municipality_full_name": WONJU_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": str(row["identity"]), "source_owner": "municipal",
            "source_page": int(row["page"]), "source_position": int(row["position"]),
            "source_status": str(row["raw_status"]), "source_region": str(row["region"]),
            "source_branch": str(detail["branch"]), "source_branch_code": branch_code,
            "source_category": str(detail["category"]), "source_period": str(detail["period"]),
            "source_apply_period": str(detail["apply_period"]), "source_time": str(detail["time"]),
            "source_weekdays": str(detail["weekdays"]),
            "source_application_method": str(detail["application_method"]),
            "source_selection_method": str(detail["selection_method"]),
            "source_room": str(row["room"]),
            "source_capacity_current": int(detail["capacity_current"]),
            "source_capacity_total": int(detail["capacity_total"]),
            "source_wait_current": int(detail["wait_current"]),
            "source_wait_total": int(detail["wait_total"]), "source_fee": str(detail["fee"]),
            "source_material_fee": str(detail["material_fee"]), "source_target": str(row["target"]),
            "list_identity_verified": True, "detail_identity_verified": True,
            "detail_fields_verified": True, "application_control_present": has_control,
            "application_endpoint_fetched": False, "login_endpoint_fetched": False,
            "mypage_endpoint_fetched": False, "registration_endpoint_fetched": False,
            "attachment_endpoint_fetched": False, "download_endpoint_fetched": False,
            "applicant_endpoint_fetched": False, "application_form_submitted": False,
            "free_text_persisted": False, "discarded_fields": list(_DISCARDED_FIELDS),
            "non_user_apply_allowed": False, "service_family": "education",
        },
    }


def _gwe_output(row: Mapping[str, Any]) -> dict[str, Any]:
    detail = row["detail"]
    status = _GWE_STATUS[str(row["raw_status"])]
    active = bool(detail["application_control_count"]) and status in {"OPEN", "WAITING"}
    application_type = (
        "ONLINE_RESERVATION" if active and detail["non_user_apply_allowed"]
        else "ONLINE_LOGIN_REQUIRED" if active else "INFO_ONLY"
    )
    fee = str(detail["fee"])
    if fee == "-":
        fee = "무료"
    return {
        "provider": WONJU_GWE_PROVIDER,
        "provider_course_id": f"{WONJU_GWE_PROVIDER}:event:{row['identity']}",
        "prefer_incoming_provider_course_id": True, "title": str(row["title"]),
        "description": str(row["title"]), "branch": str(detail["branch"]),
        "branch_code": "wjecc", "branch_url": WONJU_GWE_URL, "preserve_branch": True,
        "category": str(row["category"]), "program_type": "교육",
        "raw_url": str(row["detail_url"]), "application_url": str(row["detail_url"]) if active else "",
        "application_type": application_type, "application_method": str(detail["application_method"]),
        "application_methods": [str(detail["application_method"])],
        "reservation_available": active, "status": status, "raw_status": str(row["raw_status"]),
        "fee": fee, "fee_amount": _fee_amount(fee), "material_fee": str(detail["material_fee"]),
        "material_fee_amount": _fee_amount(str(detail["material_fee"])),
        "period": str(detail["period"]), "start_date": row["event_start"].isoformat(),
        "end_date": row["event_end"].isoformat(), "apply_period": str(detail["apply_period"]),
        "apply_start_date": row["apply_start"].isoformat(),
        "apply_end_date": row["apply_end"].isoformat(), "schedule_raw": str(detail["time"]),
        "capacity": f"{detail['capacity_total']}명", "capacity_current": int(detail["capacity_current"]),
        "capacity_total": int(detail["capacity_total"]),
        "capacity_remaining": max(int(detail["capacity_total"]) - int(detail["capacity_current"]), 0),
        "target": str(detail["target"]), "venue": str(detail["branch"]),
        "venue_name": str(detail["branch"]), "room": str(detail["room"]),
        "facility_name": str(detail["branch"]),
        "address": "강원특별자치도 원주시 북원로 2312 (단계동) 원주교육문화관",
        "venue_address": "강원특별자치도 원주시 북원로 2312 (단계동) 원주교육문화관",
        "collection_category": "도서관", "domain_category": "도서관",
        "operator_type": "교육청/도서관", "source_group": "library",
        "service_group": "공공강좌", "service_group_policy": "locked",
        "collection_type": WONJU_GWE_PARSER, "municipality_code": WONJU_MUNICIPALITY_CODE,
        "municipality_full_name": WONJU_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": str(row["identity"]), "source_owner": "gwe",
            "source_page": int(row["page"]), "source_position": int(row["position"]),
            "source_status": str(row["raw_status"]), "source_branch": str(detail["branch"]),
            "source_branch_code": "wjecc", "source_category": str(row["category"]),
            "source_period": str(detail["period"]), "source_apply_period": str(detail["apply_period"]),
            "source_time": str(detail["time"]),
            "source_application_method": str(detail["application_method"]),
            "source_room": str(detail["room"]),
            "source_capacity_current": int(detail["capacity_current"]),
            "source_capacity_total": int(detail["capacity_total"]),
            "source_wait_current": int(detail["wait_current"]),
            "source_wait_total": int(detail["wait_total"]), "source_fee": fee,
            "source_material_fee": str(detail["material_fee"]),
            "source_target": str(detail["target"]), "list_identity_verified": True,
            "detail_identity_verified": True, "detail_fields_verified": True,
            "application_control_present": active, "application_endpoint_fetched": False,
            "login_endpoint_fetched": False, "mypage_endpoint_fetched": False,
            "registration_endpoint_fetched": False, "attachment_endpoint_fetched": False,
            "download_endpoint_fetched": False, "applicant_endpoint_fetched": False,
            "application_form_submitted": False, "free_text_persisted": False,
            "discarded_fields": list(_DISCARDED_FIELDS),
            "non_user_apply_allowed": bool(detail["non_user_apply_allowed"]),
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_KEYS:
        errors.append("forbidden PII/free-text key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields allowlist exceeded")
    payload = repr({key: value for key, value in row.items() if key not in {"raw_url", "branch_url", "application_url"}})
    if _PHONE.search(payload) or _EMAIL.search(payload) or _RESIDENT_ID.search(payload):
        errors.append("PII-like value escaped allowlist")
    return errors


def _initial_meta(owner: str) -> dict[str, Any]:
    provider = WONJU_MUNICIPAL_PROVIDER if owner == "municipal" else WONJU_GWE_PROVIDER
    parser = WONJU_MUNICIPAL_PARSER if owner == "municipal" else WONJU_GWE_PARSER
    canonical = WONJU_MUNICIPAL_URL if owner == "municipal" else WONJU_GWE_URL
    return {
        "provider": provider, "owner": owner, "canonical_url": canonical,
        "canonical_url_sha256": hashlib.sha256(canonical.encode()).hexdigest(), "parser": parser,
        "municipality_code": WONJU_MUNICIPALITY_CODE,
        "municipality_full_name": WONJU_MUNICIPALITY_NAME,
        "source_requests": 0, "list_requests": 0, "detail_requests": 0,
        "request_attempts": 0, "application_endpoint_requests": 0,
        "login_endpoint_requests": 0, "mypage_endpoint_requests": 0,
        "registration_endpoint_requests": 0, "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0, "applicant_endpoint_requests": 0,
        "application_form_submissions": 0, "source_total_count": 0,
        "date_current_source_count": 0, "excluded_training_count": 0, "row_count": 0,
        "detail_pages": 0, "pagination_complete": False, "post_last_empty_verified": False,
        "details_complete": False, "stable_boundary_recheck": False,
        "privacy_boundary_complete": False, "semantic_quality_passed": False,
        "snapshot_complete": False, "source_cap_reached": False, "no_current_data": False,
        "configured_collection_error": "",
    }


def collect_wonju_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = WONJU_MAX_PAGES,
    detail_limit: int = WONJU_MAX_DETAILS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    owner = owner_for_target(target)
    parser = WONJU_MUNICIPAL_PARSER if owner == "municipal" else WONJU_GWE_PARSER
    meta = _initial_meta(owner or "municipal")
    if not owner:
        meta["configured_collection_error"] = "target does not match an exact retained Wonju owner"
        return [], parser, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], parser, meta
        session_factory = _raw_session
    try:
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ValueError("timeout must be a positive integer")
        if (
            isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or not 1 <= max_pages <= WONJU_RUNNER_MAX_PAGES
        ):
            raise ValueError(f"max_pages must be between 1 and {WONJU_RUNNER_MAX_PAGES}")
        if (
            isinstance(detail_limit, bool)
            or not isinstance(detail_limit, int)
            or not 0 <= detail_limit <= WONJU_RUNNER_MAX_DETAILS
        ):
            raise ValueError(
                f"detail_limit must be between 0 and {WONJU_RUNNER_MAX_DETAILS}"
            )
        effective_max_pages = min(max_pages, WONJU_MAX_PAGES)
        effective_detail_limit = min(detail_limit, WONJU_MAX_DETAILS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], parser, meta

    current_fetcher = fetcher or _request
    main_session: Any = None

    def parallel(items: Sequence[T], worker: Callable[[Any, T], tuple[Any, int]]) -> list[tuple[Any, int]]:
        if not items:
            return []
        if fetcher is not None or len(items) == 1:
            return [worker(main_session, item) for item in items]
        chunks = [list(items[index::WONJU_MAX_WORKERS]) for index in range(WONJU_MAX_WORKERS)]

        def run_chunk(chunk: list[T]) -> list[tuple[Any, int]]:
            session = session_factory()
            try:
                return [worker(session, item) for item in chunk]
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()

        completed: dict[int, list[tuple[Any, int]]] = {}
        with ThreadPoolExecutor(max_workers=WONJU_MAX_WORKERS) as executor:
            futures = {executor.submit(run_chunk, chunk): index for index, chunk in enumerate(chunks) if chunk}
            for future in as_completed(futures):
                completed[futures[future]] = future.result()
        indexed: dict[int, tuple[Any, int]] = {}
        for chunk_index, results in completed.items():
            for offset, result in enumerate(results):
                indexed[chunk_index + offset * WONJU_MAX_WORKERS] = result
        return [indexed[index] for index in range(len(items))]

    def account(results: Sequence[tuple[Any, int]], kind: str) -> None:
        meta["source_requests"] += len(results)
        meta[f"{kind}_requests"] += len(results)
        meta["request_attempts"] += sum(attempts for _, attempts in results)

    try:
        main_session = session_factory()

        def fetch_page(session: Any, page: int) -> tuple[dict[str, Any], int]:
            url = municipal_list_url(page) if owner == "municipal" else gwe_list_url(page)
            soup, attempts, kind = _fetch_soup(session, url, timeout, current_fetcher, owner)
            if kind != "list":
                raise WonjuContractError("list request classified as detail")
            parsed = _parse_municipal_page(soup, page, url) if owner == "municipal" else _parse_gwe_page(soup, page, url)
            return parsed, attempts

        first_index = 1 if owner == "municipal" else 0
        first_results = [fetch_page(main_session, first_index)]
        account(first_results, "list")
        first = first_results[0][0]
        last = int(first["last"])
        if last > effective_max_pages:
            raise WonjuContractError(
                "source cap: declared last page "
                f"{last} exceeds effective max_pages {effective_max_pages}"
            )
        remaining_indices = list(range(first_index + 1, first_index + last))
        remaining = parallel(remaining_indices, fetch_page)
        account(remaining, "list")
        pages = [first] + [result for result, _ in remaining]
        total = int(first["total"])
        expected_final = total - (last - 1) * (WONJU_PAGE_SIZE if owner == "municipal" else WONJU_GWE_PAGE_SIZE)
        if not 1 <= expected_final <= (WONJU_PAGE_SIZE if owner == "municipal" else WONJU_GWE_PAGE_SIZE):
            raise WonjuContractError("declared total/page arithmetic drift")
        for offset, page in enumerate(pages):
            expected_page = first_index + offset
            expected_size = expected_final if offset == last - 1 else (WONJU_PAGE_SIZE if owner == "municipal" else WONJU_GWE_PAGE_SIZE)
            if (
                page["page"] != expected_page
                or page["last"] != last
                or page["total"] != total
                or page["empty"]
                or len(page["rows"]) != expected_size
            ):
                raise WonjuContractError("complete page ledger contract drift")
        listed = [dict(row) for page in pages for row in page["rows"]]
        ids = [str(row["identity"]) for row in listed]
        if len(listed) != total or len(ids) != len(set(ids)):
            raise WonjuContractError("source total or identity uniqueness drift")
        if owner == "municipal":
            registry = first["institutions"]
            if any(page["institutions"] != registry for page in pages):
                raise WonjuContractError("municipal institution registry changed across pages")
        else:
            registry = {}
        sentinel_index = first_index + last
        sentinel_results = [fetch_page(main_session, sentinel_index)]
        account(sentinel_results, "list")
        sentinel = sentinel_results[0][0]
        if (
            sentinel["page"] != sentinel_index
            or sentinel["last"] != last
            or sentinel["total"] != total
            or not sentinel["empty"]
            or sentinel["rows"]
        ):
            raise WonjuContractError("immediate post-last empty sentinel drift")
        current_rows = [row for row in listed if row["event_end"] >= cutoff]
        if len(current_rows) > effective_detail_limit:
            raise WonjuContractError(
                "source cap: "
                f"{len(current_rows)} current details exceed effective detail_limit "
                f"{effective_detail_limit}"
            )

        def fetch_detail(session: Any, row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
            soup, attempts, kind = _fetch_soup(
                session, str(row["detail_url"]), timeout, current_fetcher, owner
            )
            if kind != "detail":
                raise WonjuContractError("detail request classified as list")
            parsed = _municipal_detail(soup, row, registry) if owner == "municipal" else _gwe_detail(soup, row)
            return parsed, attempts

        detail_results = parallel(current_rows, fetch_detail)
        account(detail_results, "detail")
        for row, (detail, _) in zip(current_rows, detail_results):
            row["detail"] = detail

        recheck_indices = [first_index, first_index + last - 1, sentinel_index]
        rechecks = parallel(recheck_indices, fetch_page)
        account(rechecks, "list")
        originals = [pages[0], pages[-1], sentinel]
        if any(_page_signature(result) != _page_signature(original) for (result, _), original in zip(rechecks, originals)):
            raise WonjuContractError("source boundaries changed during detail collection")

        excluded_training = 0
        if owner == "gwe":
            training = [row for row in current_rows if str(row["identity"]) == WONJU_GWE_TRAINING_ID]
            if len(training) != 1 or str(training[0]["title"]) != WONJU_GWE_TRAINING_TITLE:
                raise WonjuContractError("GWE audited training shell identity/title drift")
            excluded_training = 1
            emitted_source = [row for row in current_rows if str(row["identity"]) != WONJU_GWE_TRAINING_ID]
        else:
            emitted_source = current_rows
        rows = [(_municipal_output(row) if owner == "municipal" else _gwe_output(row)) for row in emitted_source]
        failures = [error for row in rows for error in _privacy_errors(row)]
        if failures:
            raise WonjuContractError("; ".join(sorted(set(failures))))
        before_ids = {str(row["provider_course_id"]) for row in rows}
        if dedupe_rows is not None:
            rows = [dict(row) for row in dedupe_rows(rows)]
        after_ids = [str(row.get("provider_course_id", "")) for row in rows]
        if len(after_ids) != len(set(after_ids)) or set(after_ids) != before_ids:
            raise WonjuContractError("dedupe_rows changed complete identity cardinality")
        failures = [error for row in rows for error in _privacy_errors(row)]
        if failures:
            raise WonjuContractError("; ".join(sorted(set(failures))))

        source_payload = [
            [str(row["identity"]), str(row["title"]), str(row["raw_status"]), str(row["event_period"])]
            for row in listed
        ]
        meta.update(
            {
                "cutoff": cutoff.isoformat(), "source_total_count": len(listed),
                "date_current_source_count": len(current_rows),
                "expired_source_count": len(listed) - len(current_rows),
                "excluded_training_count": excluded_training, "row_count": len(rows),
                "detail_pages": len(detail_results), "declared_pages": last,
                "pages": last, "discovered_links": len(listed),
                "pagination_detected": last > 1,
                "final_page_size": len(pages[-1]["rows"]),
                "source_status_counts": dict(Counter(str(row["raw_status"]) for row in listed)),
                "current_status_counts": dict(Counter(str(row["raw_status"]) for row in current_rows)),
                "normalized_status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "current_ids": [str(row["identity"]) for row in emitted_source],
                "source_identity_sha256": hashlib.sha256(
                    json.dumps(source_payload, ensure_ascii=False, separators=(",", ":")).encode()
                ).hexdigest(),
                "branch_counts": dict(Counter(str(row["branch"]) for row in rows)),
                "branch_count": len({str(row["branch"]) for row in rows}),
                "application_control_identities": sum(
                    bool(row["detail"]["application_control_count"]) for row in current_rows
                ),
                "application_controls_observed": sum(
                    int(row["detail"]["application_control_count"]) for row in current_rows
                ),
                "attachment_controls_discarded": sum(
                    int(row["detail"]["attachment_count"]) for row in current_rows
                ),
                "discarded_contact_values": (
                    sum(bool(row["detail"]["discarded_phone_present"]) for row in current_rows)
                    if owner == "municipal" else 0
                ),
                "full_page_requests": last, "post_last_requests": 1,
                "boundary_recheck_requests": 3, "pagination_complete": True,
                "post_last_empty_verified": True, "details_complete": True,
                "stable_boundary_recheck": True, "privacy_boundary_complete": True,
                "semantic_quality_passed": True, "snapshot_complete": True,
                "no_current_data": not rows, "configured_collection_error": "",
            }
        )
        return rows, parser, meta
    except Exception as exc:
        if "source cap:" in _clean(exc):
            meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["snapshot_complete"] = False
        meta["semantic_quality_passed"] = False
        return [], parser, meta
    finally:
        close = getattr(main_session, "close", None)
        if callable(close):
            close()


collect = collect_wonju_education


__all__ = [
    "WONJU_DISABLED_HOME_ALIAS_PROVIDER", "WONJU_GWE_PARSER", "WONJU_GWE_PROVIDER",
    "WONJU_GWE_TRAINING_ID", "WONJU_GWE_TRAINING_TITLE", "WONJU_GWE_URL",
    "WONJU_HOME_ALIAS_URL", "WONJU_LIVE_AUDIT_BASELINE", "WONJU_MAX_DETAILS",
    "WONJU_MAX_PAGES", "WONJU_RUNNER_MAX_DETAILS", "WONJU_RUNNER_MAX_PAGES",
    "WONJU_MUNICIPALITY_CODE", "WONJU_MUNICIPALITY_NAME",
    "WONJU_MUNICIPAL_PARSER", "WONJU_MUNICIPAL_PROVIDER", "WONJU_MUNICIPAL_URL",
    "WONJU_OWNER_AUDIT", "WonjuContractError", "collect", "collect_wonju_education",
    "gwe_list_url", "is_target", "is_wonju_education_target", "municipal_list_url",
    "owner_for_target",
]
