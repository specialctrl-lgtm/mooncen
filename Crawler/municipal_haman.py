"""Fail-closed collector for Haman-gun's complete municipal education owner.

Haman's real course owner is split across four public integrated-reservation
catalogues (lifelong learning, digital education, social welfare, and the
women's centre) plus one currently empty literature-centre catalogue.  The
existing ready ``HAMAN_WELFARE_LIFELONG_COURSE`` provider is retained and
expanded to this complete, globally disjoint ``idx`` namespace.

Two search candidates are static information pages and ``yeyak.web`` is only
an aggregate shell whose JavaScript points back to the same catalogues.  A
download result is an attachment to a welfare notice, not a course ledger.
Those aliases must not execute as additional providers.

The site clamps ``cpage=last+1`` to the last page instead of returning an
empty page.  Every snapshot therefore proves the declared last page, the
exact immediate clamp, every current/future public detail, and first/final/
clamp stability.  Login, application, applicant, attachment/download, and
free-text endpoints are never requested; contact, instructor, attachment,
and free-text values are never persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, TypeVar
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


HAMAN_PROVIDER = "HAMAN_WELFARE_LIFELONG_COURSE"
HAMAN_STATIC_DIRECTIONS_PROVIDER = "MUNI_WWW_HAMAN_GO_KR_77A20E62"
HAMAN_STATIC_INTRO_PROVIDER = "MUNI_WWW_HAMAN_GO_KR_CF8A83B7"
HAMAN_AGGREGATE_PROVIDER = "MUNI_WWW_HAMAN_GO_KR_8FBD0B4C"
HAMAN_MUNICIPALITY_CODE = "4873000000"
HAMAN_MUNICIPALITY_NAME = "경상남도 함안군"

HAMAN_HOST = "www.haman.go.kr"
HAMAN_CANONICAL_URL = f"https://{HAMAN_HOST}/02697/02708.web"
HAMAN_CANONICAL_URL_SHA256 = (
    "22fa43ae97a9df593dd321cf7c83b1bab72d9a548ae483359325696cbb3e2b4e"
)
HAMAN_STATIC_DIRECTIONS_URL = f"https://{HAMAN_HOST}/01246/01250/01286.web"
HAMAN_STATIC_INTRO_URL = f"https://{HAMAN_HOST}/01246/01250/01285.web"
HAMAN_AGGREGATE_URL = f"https://{HAMAN_HOST}/yeyak.web"
HAMAN_DOWNLOAD_CANDIDATE_URL = (
    f"https://{HAMAN_HOST}/board/download.do?idx=17199854&fnum=1&gcode=6185"
)
HAMAN_STATIC_DIRECTIONS_CANDIDATE_ID = "MUNI_IR_CCB7EEF83A5B"
HAMAN_STATIC_INTRO_CANDIDATE_ID = "MUNI_IR_C98327BE4079"
HAMAN_AGGREGATE_CANDIDATE_ID = "MUNI_IR_6AB89B685EC0"
HAMAN_DOWNLOAD_CANDIDATE_ID = "MUNI_IR_4CB27B2D92BF"

HAMAN_PAGE_SIZE = 10
HAMAN_RECOMMENDED_MAX_PAGES = 100
HAMAN_RECOMMENDED_DETAIL_LIMIT = 100
HAMAN_MAX_WORKERS = 4
HAMAN_FETCH_ATTEMPTS = 2
HAMAN_MAX_HTML_BYTES = 2_000_000
HAMAN_PARSER = (
    "haman_integrated_complete_current_education+five_exact_catalogues+"
    "declared_pagination_and_exact_post_last_clamp+global_idx_disjointness+"
    "county_resident_target_partition+all_current_public_details+"
    "stable_first_final_clamp+application_login_applicant_attachment_"
    "download_no_fetch+pii_allowlist"
)
HAMAN_OWNERSHIP_SCOPE = (
    "haman_gun_integrated_reservation_complete_municipal_education_ledger"
)


class HamanContractError(ValueError):
    """Raised when the official Haman source violates its audited contract."""


@dataclass(frozen=True)
class HamanRoute:
    key: str
    path: str
    title: str
    selector_name: str
    selector_value: str
    allowed_branches: tuple[str, ...]

    def list_url(self, page: int) -> str:
        pairs: list[tuple[str, str]] = []
        if self.selector_name:
            pairs.append((self.selector_name, self.selector_value))
        pairs.append(("cpage", str(page)))
        return f"https://{HAMAN_HOST}{self.path}?{urlencode(pairs)}"

    def target_list_url(self, page: int, target_value: str) -> str:
        pairs: list[tuple[str, str]] = []
        if self.selector_name:
            pairs.append((self.selector_name, self.selector_value))
        pairs.extend((("lecTarget", target_value), ("cpage", str(page))))
        return f"https://{HAMAN_HOST}{self.path}?{urlencode(pairs)}"


HAMAN_ROUTES: tuple[HamanRoute, ...] = (
    HamanRoute(
        "lifelong",
        "/02697/02705.web",
        "평생학습관",
        "agencys",
        "AGENCY001,AGENCY024",
        ("함안군평생교육원", "평생학습센터"),
    ),
    HamanRoute(
        "digital",
        "/02697/02707.web",
        "정보화교육",
        "agency",
        "AGENCY003",
        ("군민정보화교육",),
    ),
    HamanRoute(
        "social_welfare",
        "/02697/02708.web",
        "종합사회복지관",
        "agency",
        "AGENCY005",
        ("종합사회복지관",),
    ),
    HamanRoute(
        "women",
        "/02697/02709.web",
        "여성센터",
        "agency",
        "AGENCY006",
        ("여성센터",),
    ),
    HamanRoute(
        "literature",
        "/02697/06826.web",
        "복합문학관",
        "",
        "",
        ("복합문학관",),
    ),
)
HAMAN_ROUTE_BY_KEY = {route.key: route for route in HAMAN_ROUTES}
HAMAN_ROUTE_BY_PATH = {route.path: route for route in HAMAN_ROUTES}

HAMAN_BRANCH_CODES: Mapping[str, str] = {
    "함안군평생교육원": "HAMAN_LIFELONG_INSTITUTE",
    "평생학습센터": "HAMAN_LIFELONG_CENTER",
    "군민정보화교육": "HAMAN_DIGITAL_EDUCATION",
    "종합사회복지관": "HAMAN_GENERAL_SOCIAL_WELFARE_CENTER",
    "여성센터": "HAMAN_WOMEN_CENTER",
    "복합문학관": "HAMAN_LITERATURE_COMPLEX",
}

HAMAN_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    HAMAN_STATIC_DIRECTIONS_CANDIDATE_ID: {
        "provider": HAMAN_STATIC_DIRECTIONS_PROVIDER,
        "url": HAMAN_STATIC_DIRECTIONS_URL,
        "decision": "disable_static_directions_page; current_course_identity_count_zero",
    },
    HAMAN_STATIC_INTRO_CANDIDATE_ID: {
        "provider": HAMAN_STATIC_INTRO_PROVIDER,
        "url": HAMAN_STATIC_INTRO_URL,
        "decision": "disable_static_lifelong_introduction_page; course_identity_count_zero",
    },
    HAMAN_AGGREGATE_CANDIDATE_ID: {
        "provider": HAMAN_AGGREGATE_PROVIDER,
        "url": HAMAN_AGGREGATE_URL,
        "decision": "disable_dynamic_current_widget_shell; exact subset routes owned_by_retained_provider",
    },
    HAMAN_DOWNLOAD_CANDIDATE_ID: {
        "provider": HAMAN_AGGREGATE_PROVIDER,
        "url": HAMAN_DOWNLOAD_CANDIDATE_URL,
        "decision": "exclude_notice_attachment; never_fetch_download_as_course_source",
    },
}
HAMAN_PROVIDER_ALIAS_AUDIT: Mapping[str, Mapping[str, str]] = {
    HAMAN_STATIC_DIRECTIONS_PROVIDER: {
        "url": HAMAN_STATIC_DIRECTIONS_URL,
        "state": "disabled",
        "canonical_provider": HAMAN_PROVIDER,
        "reason": "static directions/information page without course identities",
    },
    HAMAN_STATIC_INTRO_PROVIDER: {
        "url": HAMAN_STATIC_INTRO_URL,
        "state": "disabled",
        "canonical_provider": HAMAN_PROVIDER,
        "reason": "static introduction page without course identities",
    },
    HAMAN_AGGREGATE_PROVIDER: {
        "url": HAMAN_AGGREGATE_URL,
        "state": "superseded",
        "canonical_provider": HAMAN_PROVIDER,
        "reason": (
            "aggregate JavaScript widget routes into the same four ledgers; provider "
            "hash also originated from a notice-download URL"
        ),
    },
}
HAMAN_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "url": HAMAN_CANONICAL_URL,
        "decision": "retain_existing_ready_provider_and_expand_to_all_catalogues",
    },
    {
        "url": HAMAN_AGGREGATE_URL,
        "decision": "exclude_noncanonical_current_widget_subset_shell",
    },
    {
        "url": HAMAN_DOWNLOAD_CANDIDATE_URL,
        "decision": "exclude_single_attachment_and_never_request_it",
    },
)

HAMAN_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "cutoff": "2026-07-23",
    "source_total": 1736,
    "current_total": 51,
    "route_source_counts": {
        "lifelong": 482,
        "digital": 432,
        "social_welfare": 470,
        "women": 352,
        "literature": 0,
    },
    "route_current_counts": {
        "lifelong": 9,
        "digital": 0,
        "social_welfare": 26,
        "women": 16,
        "literature": 0,
    },
    "route_pages": {
        "lifelong": 49,
        "digital": 44,
        "social_welfare": 47,
        "women": 36,
        "literature": 1,
    },
    "route_final_sizes": {
        "lifelong": 2,
        "digital": 2,
        "social_welfare": 10,
        "women": 2,
        "literature": 0,
    },
    "source_branch_counts": {
        "함안군평생교육원": 312,
        "평생학습센터": 170,
        "군민정보화교육": 432,
        "종합사회복지관": 470,
        "여성센터": 352,
    },
    "current_branch_counts": {
        "평생학습센터": 9,
        "종합사회복지관": 26,
        "여성센터": 16,
    },
    "current_raw_status_counts": {"접수완료": 19, "강좌중": 32},
    "source_raw_status_counts": {
        "접수완료": 421,
        "강좌완료": 1282,
        "기관문의": 1,
        "강좌중": 32,
    },
    "status_counts": {"CLOSED": 51},
    "current_ids": [
        "2628", "2626", "2625", "2623", "2622", "2618", "2612", "2558", "2557",
        "2587", "2586", "2585", "2584", "2581", "2580", "2579", "2578", "2577",
        "2576", "2575", "2574", "2573", "2572", "2571", "2569", "2568", "2567",
        "2566", "2565", "2564", "2563", "2562", "2561", "2560", "2559", "2608",
        "2607", "2606", "2605", "2604", "2603", "2602", "2601", "2600", "2599",
        "2598", "2597", "2596", "2595", "2594", "2593",
    ],
    "historical_unknown_periods": 13,
    "historical_unknown_reception_periods": 5,
    "expired_dated_source": 1672,
    "attachment_links_discarded": 8,
    "teacher_blocks_discarded": 26,
    "free_text_tabs_discarded": 102,
    "application_control_count": 0,
    "list_requests": 197,
    "detail_requests": 51,
    "source_requests": 248,
    "two_snapshot_requests": 496,
}

HAMAN_FIELDS_NEVER_PERSISTED = (
    "문의처·전화번호·이메일",
    "강사명·성별·이력·강사 이미지",
    "교육강좌 안내·유의사항 등 자유서술 본문",
    "파일 다운로드·첨부파일·이미지 URL",
    "로그인·본인인증·신청자 form payload",
)

# Audited legacy source defects have no reliable end date.  They are retained in the
# full-ledger proof but can never be emitted as current rows.
HAMAN_HISTORICAL_PERIOD_EXCEPTIONS: Mapping[str, str] = {
    "1648": "~",
    "1550": "~",
    "1549": "~",
    "1546": "~",
    "1451": "~",
    "12": "2018.03.06~",
    "15": "2018.03.06~2017.12.15",
    "8": "2018.03.06~2017.12.15",
    "5": "2018.03.06~2017.12.15",
    "3": "2018.03.06~2017.12.15",
    "1": "2018.03.06~2017.12.15",
    "235": "2108.08.06~2018.12.21",
    "234": "2018.08.06~2018.08.221",
}
HAMAN_REPAIRED_HISTORICAL_PERIODS: Mapping[str, tuple[str, date, date]] = {
    "102": ("2018.0306~2018.06.15", date(2018, 3, 6), date(2018, 6, 15)),
}
HAMAN_HISTORICAL_RECEPTION_PERIOD_EXCEPTIONS: Mapping[str, str] = {
    "15": "2018.02.06~2017.08.25",
    "8": "2018.02.06~2017.08.25",
    "5": "2018.02.06~2017.08.25",
    "3": "2018.02.06~2017.08.25",
    "1": "",
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
T = TypeVar("T")

_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE = re.compile(r"(?<!\d)(\d{4})[.](\d{1,2})[.](\d{1,2})(?!\d)")
_COUNT_PAIR = re.compile(r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)\s*명$")
_COUNT_SINGLE = re.compile(r"^(\d[\d,]*)\s*명$")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}")
_RESIDENT_ID = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_LIST_LABELS = (
    "교육기관", "수강료", "교육기간", "접수기간", "강좌요일",
    "접수방법", "강좌시간", "선별방법", "교육장소", "신청/정원",
)
_DETAIL_LABELS = (
    "분류", "수강료", "교육기관", "접수기간", "교육기간", "접수방법",
    "강좌요일", "선별방법", "강좌시간", "교육장소", "정원", "신청인원",
    "부대비용", "문의처",
)
_STATUS_MAP = {
    "접수예정": "SCHEDULED",
    "강좌중": "CLOSED",
    "접수완료": "CLOSED",
    "강좌완료": "CLOSED",
    "기관문의": "CLOSED",
}
_STATE_OPTIONS = (("", "신청가능여부"), ("12", "가능"), ("5", "종료"))
_DIVISION_OPTIONS = (
    ("", "강좌분류"), ("10", "기초문해교육"), ("20", "학력보완교육"),
    ("30", "직업능력교육"), ("40", "문화예술교육"),
    ("50", "인문교양교육"), ("60", "시민참여교육"),
)
_TARGET_OPTIONS = (
    ("", "교육대상"), ("0", "함안군민"), ("1", "유아"),
    ("2", "초등학생(1~3)"), ("3", "초등학생(4~6)"),
    ("4", "초등학생(1~6)"), ("5", "중학생"), ("6", "고등학생"),
    ("7", "대학생"), ("8", "일반성인"), ("9", "노인"),
    ("10", "장애우"), ("11", "청소년"), ("12", "관외"), ("99", "기타"),
)
_TARGET_LABELS = dict(_TARGET_OPTIONS)
_CURRENT_TARGET_VALUE = "0"
_CURRENT_TARGET_LABEL = _TARGET_LABELS[_CURRENT_TARGET_VALUE]
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity", "source_route", "source_page", "source_position",
        "source_status", "source_category", "source_branch", "source_fee",
        "source_period", "source_apply_period", "source_weekdays", "source_time",
        "source_application_method", "source_selection_method", "source_room",
        "source_capacity_current", "source_capacity_total", "source_material_fee",
        "list_identity_verified", "route_identity_disjoint_verified",
        "detail_identity_verified", "detail_structured_fields_verified",
        "detail_back_binding_verified", "application_control_present",
        "application_endpoint_fetched", "login_endpoint_fetched",
        "applicant_endpoint_fetched", "attachment_endpoint_fetched",
        "download_endpoint_fetched", "application_form_submitted",
        "free_text_persisted", "discarded_fields", "service_family",
        "source_target", "target_filter_verified",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone", "email", "contact", "instructor", "teacher", "manager",
        "attachments", "attachment_url", "download_url", "image_url", "body",
        "content_html", "guide", "notice", "applicant_name", "resident_number",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _strict_target_url(value: str) -> bool:
    parsed = urlparse(_clean(value))
    return (
        parsed.scheme == "https"
        and parsed.netloc == HAMAN_HOST
        and parsed.path == "/02697/02708.web"
        and parsed.params == parsed.query == parsed.fragment == ""
    )


def is_haman_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == HAMAN_PROVIDER
        and _strict_target_url(_clean(_target_value(target, "url")))
    )


is_target = is_haman_education_target


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


def _unique_query(url: str) -> tuple[Any, list[tuple[str, str]], dict[str, str]]:
    parsed = urlparse(url)
    try:
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=8,
        )
    except ValueError as exc:
        raise HamanContractError("malformed query string") from exc
    if len(pairs) != len({key for key, _ in pairs}):
        raise HamanContractError("duplicate query key")
    return parsed, pairs, dict(pairs)


def _validate_fetch_url(url: str) -> tuple[str, HamanRoute]:
    parsed, pairs, values = _unique_query(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != HAMAN_HOST
        or parsed.params
        or parsed.fragment
    ):
        raise HamanContractError("request escaped exact Haman HTTPS host")
    route = HAMAN_ROUTE_BY_PATH.get(parsed.path)
    if route is None:
        raise HamanContractError("request escaped audited course list/detail paths")
    if values.get("amode") == "view":
        expected = {"amode", "idx", "cpage"}
        if route.selector_name:
            expected.add(route.selector_name)
        if set(values) != expected or not _IDENTITY.fullmatch(values.get("idx", "")):
            raise HamanContractError("detail query binding drift")
        if not _IDENTITY.fullmatch(values.get("cpage", "")):
            raise HamanContractError("detail source-page binding drift")
        if route.selector_name and values.get(route.selector_name) != route.selector_value:
            raise HamanContractError("detail route selector drift")
        return "detail", route
    expected = {"cpage"}
    if route.selector_name:
        expected.add(route.selector_name)
    kind = "list"
    if "lecTarget" in values:
        expected.add("lecTarget")
        if values["lecTarget"] not in _TARGET_LABELS or not values["lecTarget"]:
            raise HamanContractError("target-list filter value drift")
        kind = "target_list"
    if set(values) != expected or not _IDENTITY.fullmatch(values.get("cpage", "")):
        raise HamanContractError("list query binding drift")
    if route.selector_name and values.get(route.selector_name) != route.selector_value:
        raise HamanContractError("list route selector drift")
    return kind, route


def _same_response_url(actual: str, expected: str) -> bool:
    left, right = urlparse(actual), urlparse(expected)
    return (
        left.scheme == right.scheme
        and left.netloc == right.netloc
        and left.path == right.path
        and left.params == right.params == ""
        and left.fragment == right.fragment == ""
        and parse_qsl(left.query, keep_blank_values=True)
        == parse_qsl(right.query, keep_blank_values=True)
    )


def _validate_owner_shell(soup: BeautifulSoup, route: HamanRoute) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if route.title not in title or "통합예약시스템" not in title:
        raise HamanContractError(f"{route.key}: owner/title shell drift")
    footer = soup.select_one("#author1 address")
    if footer is None or _clean(footer.get_text(" ", strip=True)) != (
        "(52043) 경상남도 함안군 가야읍 말산로 1 (함안군청)"
    ):
        raise HamanContractError(f"{route.key}: official reservation footer drift")


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
) -> tuple[BeautifulSoup, int, str, HamanRoute]:
    kind, route = _validate_fetch_url(url)
    last_error: Optional[BaseException] = None
    for attempt in range(1, HAMAN_FETCH_ATTEMPTS + 1):
        try:
            response = fetcher(session, url, timeout)
            status = int(getattr(response, "status_code", 0))
            if status != 200:
                raise requests.RequestException(f"HTTP {status}")
            if getattr(response, "history", []):
                raise HamanContractError("redirect history is not allowed")
            if not _same_response_url(_clean(getattr(response, "url", "")), url):
                raise HamanContractError("response URL drift")
            content = getattr(response, "content", b"")
            if not isinstance(content, (bytes, bytearray)):
                raise HamanContractError("response body is not bytes")
            if not content or len(content) > HAMAN_MAX_HTML_BYTES:
                raise HamanContractError("response body size outside audited bounds")
            try:
                html = bytes(content).decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise HamanContractError("response is not strict UTF-8") from exc
            soup = BeautifulSoup(html, "html.parser")
            _validate_owner_shell(soup, route)
            return soup, attempt, kind, route
        except HamanContractError as exc:
            retryable_shell_drift = any(
                marker in str(exc)
                for marker in (
                    "owner/title shell drift",
                    "official reservation footer drift",
                )
            )
            if retryable_shell_drift and attempt < HAMAN_FETCH_ATTEMPTS:
                last_error = exc
                continue
            raise
        except requests.RequestException as exc:
            last_error = exc
    raise HamanContractError(f"request failed after retries: {_clean(last_error)}")


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


def _option_registry(select: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(option.get("value", "")), _clean(option.get_text(" ", strip=True)))
        for option in select.find_all("option", recursive=False)
    )


def _validate_search_form(
    soup: BeautifulSoup,
    requested_url: str,
    route: HamanRoute,
    target_value: str = "",
) -> None:
    form = soup.select_one("form#listForm")
    if form is None or _clean(form.get("method")).lower() != "get":
        raise HamanContractError(f"{route.key}: list form missing/drifted")
    expected_action = urlparse(requested_url).path + "?" + urlparse(requested_url).query
    if _clean(form.get("action")) != expected_action:
        raise HamanContractError(f"{route.key}: list form action/request binding drift")
    hidden_pairs = [
        (_clean(node.get("name")), str(node.get("value", "")))
        for node in form.select('input[type="hidden"][name]')
    ]
    if len(hidden_pairs) != 2 or dict(hidden_pairs) != {"cpage": "1", "stype": ""}:
        raise HamanContractError(f"{route.key}: list hidden fields drift")
    text_input = form.select_one('input[name="sstring"]')
    if text_input is None or str(text_input.get("value", "")) != "":
        raise HamanContractError(f"{route.key}: unfiltered keyword binding drift")
    registries = {
        "lecState": _STATE_OPTIONS,
        "lecDivLvl1": _DIVISION_OPTIONS,
        "lecTarget": _TARGET_OPTIONS,
    }
    for name, expected in registries.items():
        select = form.select_one(f'select[name="{name}"]')
        if select is None or _option_registry(select) != expected:
            raise HamanContractError(f"{route.key}: {name} registry drift")
        selected = [
            str(option.get("value", ""))
            for option in select.find_all("option", recursive=False)
            if option.has_attr("selected")
        ]
        expected_selected = target_value if name == "lecTarget" else ""
        if selected != [expected_selected]:
            raise HamanContractError(f"{route.key}: unexpected active search filter")


def _parse_date_parts(match: Sequence[str], identity: str) -> date:
    try:
        return date(*(int(part) for part in match))
    except ValueError as exc:
        raise HamanContractError(f"course {identity}: invalid source date") from exc


def _parse_event_period(
    value: str,
    identity: str,
) -> tuple[Optional[date], Optional[date], str]:
    cleaned = _clean(value)
    repaired = HAMAN_REPAIRED_HISTORICAL_PERIODS.get(identity)
    if repaired is not None:
        raw, start, end = repaired
        if cleaned != raw:
            raise HamanContractError(f"course {identity}: repaired period source changed")
        return start, end, "audited_historical_repair"
    exception = HAMAN_HISTORICAL_PERIOD_EXCEPTIONS.get(identity)
    if exception is not None:
        if cleaned != exception:
            raise HamanContractError(f"course {identity}: historical period exception changed")
        return None, None, "audited_unknown_historical_period"
    matches = _DATE.findall(cleaned)
    if len(matches) != 2:
        raise HamanContractError(f"course {identity}: education period shape drift")
    start, end = (_parse_date_parts(match, identity) for match in matches)
    if start > end:
        raise HamanContractError(f"course {identity}: reversed education period")
    return start, end, "exact"


def _parse_date_range(value: str, identity: str, label: str) -> tuple[date, date]:
    matches = _DATE.findall(_clean(value))
    if len(matches) != 2:
        raise HamanContractError(f"course {identity}: {label} date shape drift")
    start, end = (_parse_date_parts(match, identity) for match in matches)
    if start > end:
        raise HamanContractError(f"course {identity}: reversed {label} period")
    return start, end


def _parse_reception_period(
    value: str,
    identity: str,
) -> tuple[Optional[date], Optional[date], str]:
    cleaned = _clean(value)
    exception = HAMAN_HISTORICAL_RECEPTION_PERIOD_EXCEPTIONS.get(identity)
    if exception is not None:
        if cleaned != exception:
            raise HamanContractError(
                f"course {identity}: historical reception exception changed"
            )
        return None, None, "audited_unknown_historical_reception_period"
    start, end = _parse_date_range(cleaned, identity, "reception")
    return start, end, "exact"


def _structured_pairs(node: Any, labels: tuple[str, ...], identity: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    order: list[str] = []
    for item in node.select(":scope > li"):
        key = item.select_one(":scope > .t1")
        value = item.select_one(":scope > .t2")
        if key is None or value is None:
            raise HamanContractError(f"course {identity}: structured pair shape drift")
        label = _clean(key.get_text(" ", strip=True))
        if label in pairs:
            raise HamanContractError(f"course {identity}: duplicate structured label")
        pairs[label] = _clean(value.get_text(" ", strip=True))
        order.append(label)
    if tuple(order) != labels:
        raise HamanContractError(f"course {identity}: structured label order/set drift")
    return pairs


def _parse_capacity_pair(value: str, identity: str) -> tuple[int, int]:
    match = _COUNT_PAIR.fullmatch(_clean(value))
    if match is None:
        raise HamanContractError(f"course {identity}: list capacity shape drift")
    return tuple(int(part.replace(",", "")) for part in match.groups())  # type: ignore[return-value]


def _parse_capacity_single(value: str, identity: str, label: str) -> int:
    match = _COUNT_SINGLE.fullmatch(_clean(value))
    if match is None:
        raise HamanContractError(f"course {identity}: detail {label} shape drift")
    return int(match.group(1).replace(",", ""))


def _validate_detail_href(
    list_url: str,
    href: str,
    route: HamanRoute,
    page: int,
) -> tuple[str, str]:
    absolute = urljoin(list_url, href)
    parsed, _, values = _unique_query(absolute)
    expected = {"amode", "idx", "cpage"}
    if route.selector_name:
        expected.add(route.selector_name)
    if (
        parsed.scheme != "https"
        or parsed.netloc != HAMAN_HOST
        or parsed.path != route.path
        or parsed.params
        or parsed.fragment
        or set(values) != expected
        or values.get("amode") != "view"
        or not _IDENTITY.fullmatch(values.get("idx", ""))
        or values.get("cpage") != str(page)
        or (
            route.selector_name
            and values.get(route.selector_name) != route.selector_value
        )
    ):
        raise HamanContractError(f"{route.key}: detail href identity/route binding drift")
    return values["idx"], absolute


def _parse_card(
    anchor: Any,
    *,
    route: HamanRoute,
    requested_page: int,
    position: int,
    list_url: str,
) -> dict[str, Any]:
    identity, detail_url = _validate_detail_href(
        list_url, str(anchor.get("href", "")), route, requested_page
    )
    status_node = anchor.select_one(":scope > span.cate[data-category]")
    title_node = anchor.select_one(":scope > strong.h1")
    fields_node = anchor.select_one(":scope > ul.tg1")
    if status_node is None or title_node is None or fields_node is None:
        raise HamanContractError(f"course {identity}: list card structure drift")
    status = _clean(status_node.get_text(" ", strip=True))
    if status != _clean(status_node.get("data-category")) or status not in _STATUS_MAP:
        raise HamanContractError(f"course {identity}: source status drift")
    title = _clean(title_node.get_text(" ", strip=True))
    if not title:
        raise HamanContractError(f"course {identity}: empty title")
    fields = _structured_pairs(fields_node, _LIST_LABELS, identity)
    branch = fields["교육기관"]
    if branch not in route.allowed_branches or branch not in HAMAN_BRANCH_CODES:
        raise HamanContractError(f"course {identity}: route/official branch disagreement")
    event_start, event_end, period_quality = _parse_event_period(
        fields["교육기간"], identity
    )
    apply_start, apply_end, apply_period_quality = _parse_reception_period(
        fields["접수기간"], identity
    )
    capacity_current, capacity_total = _parse_capacity_pair(fields["신청/정원"], identity)
    return {
        "identity": identity,
        "route": route.key,
        "page": requested_page,
        "position": position,
        "title": title,
        "raw_status": status,
        "branch": branch,
        "fee": fields["수강료"],
        "event_period": fields["교육기간"],
        "event_start": event_start,
        "event_end": event_end,
        "period_quality": period_quality,
        "apply_period": fields["접수기간"],
        "apply_start": apply_start,
        "apply_end": apply_end,
        "apply_period_quality": apply_period_quality,
        "weekdays": fields["강좌요일"],
        "application_method": fields["접수방법"],
        "time": fields["강좌시간"],
        "selection_method": fields["선별방법"],
        "room": fields["교육장소"],
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "detail_url": detail_url,
    }


def _parse_pager(
    soup: BeautifulSoup,
    route: HamanRoute,
    target_value: str = "",
) -> tuple[int, int]:
    pager = soup.select_one('div.pagination.bdt0[title="페이지 수 매기기"]')
    if pager is None:
        raise HamanContractError(f"{route.key}: pagination missing")
    current_nodes = pager.select(".pages .m.on a[title]")
    if len(current_nodes) != 1:
        raise HamanContractError(f"{route.key}: active pagination cardinality drift")
    match = re.fullmatch(r"현재\s+([1-9]\d*)\s+페이지", _clean(current_nodes[0].get("title")))
    if match is None:
        raise HamanContractError(f"{route.key}: active pagination title drift")
    current = int(match.group(1))
    visible_numbers: list[int] = []
    for anchor in pager.select(".pages .m a[title]"):
        title = _clean(anchor.get("title"))
        visible_match = re.fullmatch(r"(?:현재\s+)?([1-9]\d*)\s+페이지", title)
        if visible_match is None:
            raise HamanContractError(f"{route.key}: visible pagination title drift")
        number = int(visible_match.group(1))
        visible_numbers.append(number)
        href = _clean(anchor.get("href"))
        if number == current:
            if title != f"현재 {current} 페이지" or href:
                raise HamanContractError(f"{route.key}: active pagination binding drift")
        else:
            if not href:
                raise HamanContractError(f"{route.key}: visible pagination href missing")
            base_url = (
                route.target_list_url(current, target_value)
                if target_value
                else route.list_url(current)
            )
            absolute = urljoin(base_url, href)
            kind, href_route = _validate_fetch_url(absolute)
            _, _, values = _unique_query(absolute)
            expected_kind = "target_list" if target_value else "list"
            if (
                kind != expected_kind
                or href_route != route
                or values.get("cpage") != str(number)
                or values.get("lecTarget", "") != target_value
            ):
                raise HamanContractError(f"{route.key}: visible pagination route drift")
    if (
        not visible_numbers
        or len(visible_numbers) != len(set(visible_numbers))
        or visible_numbers != sorted(visible_numbers)
        or current not in visible_numbers
    ):
        raise HamanContractError(f"{route.key}: visible pagination sequence drift")
    last_anchor = pager.select_one('.last a[title="맨끝 페이지"]')
    if last_anchor is None:
        raise HamanContractError(f"{route.key}: last-page control missing")
    href = _clean(last_anchor.get("href"))
    declared_last = max(visible_numbers)
    if href:
        base_url = (
            route.target_list_url(current, target_value)
            if target_value
            else route.list_url(current)
        )
        absolute = urljoin(base_url, href)
        kind, href_route = _validate_fetch_url(absolute)
        _, _, values = _unique_query(absolute)
        expected_kind = "target_list" if target_value else "list"
        if (
            kind != expected_kind
            or href_route != route
            or values.get("lecTarget", "") != target_value
        ):
            raise HamanContractError(f"{route.key}: last-page route drift")
        declared_last = int(values["cpage"])
    return current, declared_last


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    route: HamanRoute,
    requested_page: int,
    requested_url: str,
) -> dict[str, Any]:
    _validate_search_form(soup, requested_url, route)
    current_page, pager_last = _parse_pager(soup, route)
    ledger = soup.select_one(".edu1list")
    if ledger is None:
        raise HamanContractError(f"{route.key}: course ledger missing")
    anchors = ledger.select(":scope > ul > li > a[href]")
    empty_node = ledger.select_one(":scope > p")
    if anchors and empty_node is not None:
        raise HamanContractError(f"{route.key}: mixed list/empty ledger")
    if not anchors:
        if empty_node is None or _clean(empty_node.get_text(" ", strip=True)) != "등록된 강좌가 없습니다.":
            raise HamanContractError(f"{route.key}: exact empty sentinel drift")
        rows: list[dict[str, Any]] = []
        empty = True
    else:
        rows = [
            _parse_card(
                anchor,
                route=route,
                requested_page=requested_page,
                position=position,
                list_url=requested_url,
            )
            for position, anchor in enumerate(anchors, 1)
        ]
        empty = False
    return {
        "route": route.key,
        "requested_page": requested_page,
        "current_page": current_page,
        "pager_last": pager_last,
        "empty": empty,
        "rows": rows,
    }


def _target_identity_from_href(
    requested_url: str,
    href: Any,
    route: HamanRoute,
    requested_page: int,
    target_value: str,
) -> str:
    absolute = urljoin(requested_url, _clean(href))
    parsed, _, values = _unique_query(absolute)
    expected = {"amode", "idx", "cpage", "lecTarget"}
    if route.selector_name:
        expected.add(route.selector_name)
    if (
        parsed.scheme != "https"
        or parsed.netloc != HAMAN_HOST
        or parsed.path != route.path
        or parsed.params
        or parsed.fragment
        or set(values) != expected
        or values.get("amode") != "view"
        or not _IDENTITY.fullmatch(values.get("idx", ""))
        or values.get("cpage") != str(requested_page)
        or values.get("lecTarget") != target_value
        or (
            route.selector_name
            and values.get(route.selector_name) != route.selector_value
        )
    ):
        raise HamanContractError(
            f"{route.key}: target-filter detail identity drift"
        )
    return values["idx"]


def _parse_target_page(
    soup: BeautifulSoup,
    *,
    route: HamanRoute,
    requested_page: int,
    requested_url: str,
    target_value: str,
) -> dict[str, Any]:
    _validate_search_form(
        soup,
        requested_url,
        route,
        target_value=target_value,
    )
    current_page, pager_last = _parse_pager(
        soup,
        route,
        target_value=target_value,
    )
    ledger = soup.select_one(".edu1list")
    if ledger is None:
        raise HamanContractError(
            f"{route.key}: target-filter course ledger missing"
        )
    anchors = ledger.select(":scope > ul > li > a[href]")
    empty_node = ledger.select_one(":scope > p")
    if anchors and empty_node is not None:
        raise HamanContractError(
            f"{route.key}: mixed target-filter list/empty ledger"
        )
    if not anchors:
        if (
            empty_node is None
            or _clean(empty_node.get_text(" ", strip=True))
            != "등록된 강좌가 없습니다."
        ):
            raise HamanContractError(
                f"{route.key}: target-filter exact empty drift"
            )
        identities: list[str] = []
        empty = True
    else:
        identities = [
            _target_identity_from_href(
                requested_url,
                anchor.get("href"),
                route,
                requested_page,
                target_value,
            )
            for anchor in anchors
        ]
        empty = False
    if len(identities) != len(set(identities)):
        raise HamanContractError(
            f"{route.key}: duplicate target-filter identity"
        )
    return {
        "route": route.key,
        "requested_page": requested_page,
        "current_page": current_page,
        "pager_last": pager_last,
        "empty": empty,
        "target_value": target_value,
        "identities": identities,
    }


def _target_page_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        page["route"],
        page["current_page"],
        page["pager_last"],
        page["empty"],
        page["target_value"],
        tuple(page["identities"]),
    )


def _target_page_ledger_signature(
    page: Mapping[str, Any],
) -> tuple[Any, ...]:
    return (
        page["route"],
        page["pager_last"],
        page["empty"],
        page["target_value"],
        tuple(page["identities"]),
    )


def _validate_target_route_pages(
    route: HamanRoute,
    pages: Sequence[Mapping[str, Any]],
    declared_last: int,
) -> set[str]:
    if len(pages) != declared_last:
        raise HamanContractError(
            f"{route.key}: incomplete target-filter pages"
        )
    identities: list[str] = []
    for number, page in enumerate(pages, 1):
        if (
            page["requested_page"] != number
            or page["current_page"] != number
            or page["pager_last"] != declared_last
        ):
            raise HamanContractError(
                f"{route.key}: target-filter page binding drift"
            )
        page_identities = list(page["identities"])
        if number < declared_last and len(page_identities) != HAMAN_PAGE_SIZE:
            raise HamanContractError(
                f"{route.key}: target-filter pre-final page size drift"
            )
        if number == declared_last and not 0 <= len(page_identities) <= HAMAN_PAGE_SIZE:
            raise HamanContractError(
                f"{route.key}: target-filter final page size drift"
            )
        identities.extend(page_identities)
    if len(identities) != len(set(identities)):
        raise HamanContractError(
            f"{route.key}: target-filter identity overlap"
        )
    return set(identities)


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row["identity"], row["title"], row["raw_status"], row["branch"],
        row["fee"], row["event_period"], row["event_start"], row["event_end"],
        row["period_quality"], row["apply_period"], row["apply_start"],
        row["apply_end"], row["apply_period_quality"], row["weekdays"],
        row["application_method"],
        row["time"], row["selection_method"], row["room"],
        row["capacity_current"], row["capacity_total"], row["detail_url"],
    )


def _page_data_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        page["route"], page["pager_last"], page["empty"],
        tuple(_row_signature(row) for row in page["rows"]),
    )


def _page_ledger_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    """Compare clamped ledger data while ignoring request-scoped back bindings."""

    return (
        page["route"],
        page["pager_last"],
        page["empty"],
        tuple(
            (
                row["identity"], row["title"], row["raw_status"], row["branch"],
                row["fee"], row["event_period"], row["event_start"],
                row["event_end"], row["period_quality"], row["apply_period"],
                row["apply_start"], row["apply_end"],
                row["apply_period_quality"], row["weekdays"],
                row["application_method"], row["time"],
                row["selection_method"], row["room"],
                row["capacity_current"], row["capacity_total"],
            )
            for row in page["rows"]
        ),
    )


def _validate_route_pages(
    route: HamanRoute,
    pages: Sequence[Mapping[str, Any]],
    declared_last: int,
) -> list[dict[str, Any]]:
    if len(pages) != declared_last:
        raise HamanContractError(f"{route.key}: incomplete declared pages")
    for number, page in enumerate(pages, 1):
        if page["requested_page"] != number or page["current_page"] != number:
            raise HamanContractError(f"{route.key}: requested/active page disagreement")
        if int(page["pager_last"]) != declared_last:
            raise HamanContractError(f"{route.key}: declared last page drift")
        expected_size = HAMAN_PAGE_SIZE if number < declared_last else len(page["rows"])
        if number < declared_last and len(page["rows"]) != expected_size:
            raise HamanContractError(f"{route.key}: pre-final page size drift")
        if number == declared_last:
            if route.key == "literature":
                if page["rows"] or not page["empty"] or declared_last != 1:
                    raise HamanContractError("literature catalogue is no longer exact empty")
            elif not (1 <= len(page["rows"]) <= HAMAN_PAGE_SIZE) or page["empty"]:
                raise HamanContractError(f"{route.key}: final page size drift")
    rows = [dict(row) for page in pages for row in page["rows"]]
    identities = [str(row["identity"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise HamanContractError(f"{route.key}: duplicate identity")
    return rows


def _parse_detail(soup: BeautifulSoup, expected: Mapping[str, Any]) -> dict[str, Any]:
    identity = str(expected["identity"])
    route = HAMAN_ROUTE_BY_KEY[str(expected["route"])]
    detail = soup.select_one(".edu1view")
    if detail is None:
        raise HamanContractError(f"course {identity}: detail container missing")
    status_node = detail.select_one(":scope > .hg1 > .cate[data-category]")
    title_node = detail.select_one(":scope > .hg1 > .h1")
    fields_node = detail.select_one(":scope > ul.tg1")
    if status_node is None or title_node is None or fields_node is None:
        raise HamanContractError(f"course {identity}: detail header/fields drift")
    status = _clean(status_node.get_text(" ", strip=True))
    title = _clean(title_node.get_text(" ", strip=True))
    if status != _clean(status_node.get("data-category")):
        raise HamanContractError(f"course {identity}: detail status attribute drift")
    fields = _structured_pairs(fields_node, _DETAIL_LABELS, identity)
    detail_start, detail_end, quality = _parse_event_period(fields["교육기간"], identity)
    apply_start, apply_end = _parse_date_range(fields["접수기간"], identity, "detail reception")
    capacity_total = _parse_capacity_single(fields["정원"], identity, "capacity")
    capacity_current = _parse_capacity_single(fields["신청인원"], identity, "applicant count")
    comparisons = (
        title == expected["title"],
        status == expected["raw_status"],
        fields["교육기관"] == expected["branch"],
        fields["수강료"] == expected["fee"],
        fields["교육기간"] == expected["event_period"],
        detail_start == expected["event_start"],
        detail_end == expected["event_end"],
        quality == expected["period_quality"],
        fields["접수기간"] == expected["apply_period"],
        apply_start == expected["apply_start"],
        apply_end == expected["apply_end"],
        fields["강좌요일"] == expected["weekdays"],
        fields["접수방법"] == expected["application_method"],
        fields["강좌시간"] == expected["time"],
        fields["선별방법"] == expected["selection_method"],
        fields["교육장소"] == expected["room"],
        capacity_current == expected["capacity_current"],
        capacity_total == expected["capacity_total"],
    )
    if not all(comparisons):
        raise HamanContractError(f"course {identity}: list/detail structured data drift")
    buttons = detail.select(":scope > .btns > a")
    back_buttons = [
        button
        for button in buttons
        if _clean(button.get_text(" ", strip=True)) == "목록으로"
    ]
    if len(back_buttons) != 1:
        raise HamanContractError(f"course {identity}: list-return control drift")
    application_control_count = len(buttons) - 1
    back_url = urljoin(
        str(expected["detail_url"]), str(back_buttons[0].get("href", ""))
    )
    parsed, _, values = _unique_query(back_url)
    expected_back = {"cpage": str(expected["page"])}
    if route.selector_name:
        expected_back[route.selector_name] = route.selector_value
    if (
        parsed.scheme != "https"
        or parsed.netloc != HAMAN_HOST
        or parsed.path != route.path
        or parsed.params
        or parsed.fragment
        or values != expected_back
    ):
        raise HamanContractError(f"course {identity}: detail back/list binding drift")
    tabs = detail.select(":scope > .tabs1cont > .tabs1pane")
    if len(tabs) != 2:
        raise HamanContractError(f"course {identity}: free-text/instructor tab boundary drift")
    return {
        "identity": identity,
        "category": fields["분류"],
        "material_fee": fields["부대비용"],
        "attachment_count": len(
            detail.select('a[href*="/Download.do"], a[href*="/download.do"]')
        ),
        "teacher_count": len(detail.select(".teacher1")),
        "discarded_tab_count": len(tabs),
        "application_control_count": application_control_count,
        "back_url": back_url,
    }


def _fee_amount(value: str) -> Optional[int]:
    cleaned = _clean(value)
    if cleaned in {"무료", "0", "0원", "없음"}:
        return 0
    match = re.fullmatch(r"(\d[\d,]*)\s*(?:원)?", cleaned)
    return int(match.group(1).replace(",", "")) if match else None


def _methods(value: str) -> list[str]:
    return [part for part in (_clean(item) for item in value.split(",")) if part]


def _output_row(row: Mapping[str, Any]) -> dict[str, Any]:
    detail = row["detail"]
    identity = str(row["identity"])
    event_start: date = row["event_start"]
    event_end: date = row["event_end"]
    apply_start: date = row["apply_start"]
    apply_end: date = row["apply_end"]
    branch = str(row["branch"])
    methods = _methods(str(row["application_method"]))
    period = f"{event_start.isoformat()} ~ {event_end.isoformat()}"
    apply_period = f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
    capacity_current = int(row["capacity_current"])
    capacity_total = int(row["capacity_total"])
    return {
        "provider": HAMAN_PROVIDER,
        "provider_course_id": f"{HAMAN_PROVIDER}:idx:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": str(row["title"]),
        "description": str(row["title"]),
        "branch": branch,
        "branch_code": HAMAN_BRANCH_CODES[branch],
        "branch_url": HAMAN_ROUTE_BY_KEY[str(row["route"])].list_url(1),
        "preserve_branch": True,
        "category": "교육/강좌",
        "program_type": "교육",
        "raw_url": str(row["detail_url"]),
        "application_url": "",
        "application_type": "INFO_ONLY",
        "application_method": ", ".join(methods),
        "application_methods": methods,
        "reservation_available": False,
        "status": _STATUS_MAP[str(row["raw_status"])],
        "raw_status": str(row["raw_status"]),
        "fee": str(row["fee"]),
        "fee_amount": _fee_amount(str(row["fee"])),
        "material_fee": str(detail["material_fee"]),
        "material_fee_amount": _fee_amount(str(detail["material_fee"])),
        "period": period,
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": apply_period,
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "schedule_raw": _clean(f"{row['weekdays']} {row['time']}"),
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_remaining": max(capacity_total - capacity_current, 0),
        "target": str(row["target"]),
        "venue": branch,
        "venue_name": branch,
        "room": str(row["room"]),
        "facility_name": branch,
        "address": "",
        "venue_address": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": HAMAN_PARSER,
        "municipality_code": HAMAN_MUNICIPALITY_CODE,
        "municipality_full_name": HAMAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_route": str(row["route"]),
            "source_page": int(row["page"]),
            "source_position": int(row["position"]),
            "source_status": str(row["raw_status"]),
            "source_category": str(detail["category"]),
            "source_branch": branch,
            "source_fee": str(row["fee"]),
            "source_period": period,
            "source_apply_period": apply_period,
            "source_weekdays": str(row["weekdays"]),
            "source_time": str(row["time"]),
            "source_application_method": str(row["application_method"]),
            "source_selection_method": str(row["selection_method"]),
            "source_room": str(row["room"]),
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "source_material_fee": str(detail["material_fee"]),
            "source_target": str(row["target"]),
            "list_identity_verified": True,
            "target_filter_verified": True,
            "route_identity_disjoint_verified": True,
            "detail_identity_verified": True,
            "detail_structured_fields_verified": True,
            "detail_back_binding_verified": True,
            "application_control_present": bool(detail["application_control_count"]),
            "application_endpoint_fetched": False,
            "login_endpoint_fetched": False,
            "applicant_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "download_endpoint_fetched": False,
            "application_form_submitted": False,
            "free_text_persisted": False,
            "discarded_fields": list(HAMAN_FIELDS_NEVER_PERSISTED),
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden PII/free-text key")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields allowlist exceeded")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "branch_url"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload) or _RESIDENT_ID.search(payload):
        errors.append("PII-like value escaped structured allowlist")
    return errors


def _initial_meta() -> dict[str, Any]:
    return {
        "provider": HAMAN_PROVIDER,
        "provider_decision": (
            "retain ready welfare/lifelong provider and expand it to the complete "
            "five-route municipal education owner"
        ),
        "canonical_url": HAMAN_CANONICAL_URL,
        "canonical_url_sha256": HAMAN_CANONICAL_URL_SHA256,
        "parser": HAMAN_PARSER,
        "ownership_scope": HAMAN_OWNERSHIP_SCOPE,
        "municipality_code": HAMAN_MUNICIPALITY_CODE,
        "municipality_full_name": HAMAN_MUNICIPALITY_NAME,
        "page_size": HAMAN_PAGE_SIZE,
        "recommended_max_pages": HAMAN_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": HAMAN_RECOMMENDED_DETAIL_LIMIT,
        "pagination_boundary_mode": "declared_last_plus_exact_immediate_last_page_clamp",
        "source_requests": 0,
        "list_requests": 0,
        "target_filter_requests": 0,
        "detail_requests": 0,
        "request_attempts": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "application_form_submissions": 0,
        "source_total_count": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "historical_unknown_period_count": 0,
        "historical_unknown_reception_period_count": 0,
        "row_count": 0,
        "detail_pages": 0,
        "route_identity_overlap_count": 0,
        "attachment_links_discarded": 0,
        "teacher_blocks_discarded": 0,
        "free_text_tabs_discarded": 0,
        "pagination_complete": False,
        "post_last_clamp_verified": False,
        "route_identity_disjoint": False,
        "details_complete": False,
        "stable_boundary_recheck": False,
        "privacy_boundary_complete": False,
        "semantic_quality_passed": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
        "current_target_value": _CURRENT_TARGET_VALUE,
        "current_target_label": _CURRENT_TARGET_LABEL,
    }


def collect_haman_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = HAMAN_RECOMMENDED_MAX_PAGES,
    detail_limit: int = HAMAN_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, stable, privacy-safe Haman education snapshot."""

    meta = _initial_meta()
    if not is_haman_education_target(target):
        meta["configured_collection_error"] = "target does not match exact retained Haman owner"
        return [], HAMAN_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], HAMAN_PARSER, meta
        session_factory = _raw_session
    try:
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ValueError("timeout must be a positive integer")
        if (
            isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or not 1 <= max_pages <= HAMAN_RECOMMENDED_MAX_PAGES
        ):
            raise ValueError(
                f"max_pages must be between 1 and {HAMAN_RECOMMENDED_MAX_PAGES}"
            )
        if (
            isinstance(detail_limit, bool)
            or not isinstance(detail_limit, int)
            or not 0 <= detail_limit <= HAMAN_RECOMMENDED_DETAIL_LIMIT
        ):
            raise ValueError(
                "detail_limit must be between 0 and "
                f"{HAMAN_RECOMMENDED_DETAIL_LIMIT}"
            )
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], HAMAN_PARSER, meta

    current_fetcher = fetcher or _request
    try:
        main_session = session_factory()
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"{type(exc).__name__}: session_factory failed: {_clean(exc)}"
        )
        return [], HAMAN_PARSER, meta

    def fetch_list(session: Any, route: HamanRoute, page: int) -> tuple[dict[str, Any], int]:
        url = route.list_url(page)
        soup, attempts, kind, actual_route = _fetch_soup(
            session, url, timeout, current_fetcher
        )
        if kind != "list" or actual_route != route:
            raise HamanContractError("list request/route classification drift")
        return (
            _parse_list_page(
                soup,
                route=route,
                requested_page=page,
                requested_url=url,
            ),
            attempts,
        )

    def fetch_target_list(
        session: Any,
        route: HamanRoute,
        page: int,
    ) -> tuple[dict[str, Any], int]:
        url = route.target_list_url(page, _CURRENT_TARGET_VALUE)
        soup, attempts, kind, actual_route = _fetch_soup(
            session,
            url,
            timeout,
            current_fetcher,
        )
        if kind != "target_list" or actual_route != route:
            raise HamanContractError(
                "target-list request/route classification drift"
            )
        return (
            _parse_target_page(
                soup,
                route=route,
                requested_page=page,
                requested_url=url,
                target_value=_CURRENT_TARGET_VALUE,
            ),
            attempts,
        )

    def fetch_detail(session: Any, row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        soup, attempts, kind, route = _fetch_soup(
            session, str(row["detail_url"]), timeout, current_fetcher
        )
        if kind != "detail" or route.key != row["route"]:
            raise HamanContractError("detail request/route classification drift")
        return _parse_detail(soup, row), attempts

    def parallel_batches(
        items: Sequence[T],
        worker_item: Callable[[Any, T], tuple[Any, int]],
    ) -> list[tuple[Any, int]]:
        if not items:
            return []
        if fetcher is not None or len(items) == 1:
            return [worker_item(main_session, item) for item in items]
        chunks = [list(items[index::HAMAN_MAX_WORKERS]) for index in range(HAMAN_MAX_WORKERS)]

        def worker(chunk: list[T]) -> list[tuple[Any, int]]:
            session = session_factory()
            try:
                return [worker_item(session, item) for item in chunk]
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()

        indexed: dict[int, tuple[Any, int]] = {}
        with ThreadPoolExecutor(max_workers=HAMAN_MAX_WORKERS) as executor:
            futures = {
                executor.submit(worker, chunk): index
                for index, chunk in enumerate(chunks)
                if chunk
            }
            chunk_results: dict[int, list[tuple[Any, int]]] = {}
            for future in as_completed(futures):
                chunk_results[futures[future]] = future.result()
        for chunk_index, chunk in enumerate(chunks):
            for offset, result in enumerate(chunk_results.get(chunk_index, [])):
                original_index = chunk_index + offset * HAMAN_MAX_WORKERS
                indexed[original_index] = result
        return [indexed[index] for index in range(len(items))]

    def account_list(results: Sequence[tuple[Any, int]]) -> None:
        meta["source_requests"] += len(results)
        meta["list_requests"] += len(results)
        meta["request_attempts"] += sum(attempts for _, attempts in results)

    def account_detail(results: Sequence[tuple[Any, int]]) -> None:
        meta["source_requests"] += len(results)
        meta["detail_requests"] += len(results)
        meta["request_attempts"] += sum(attempts for _, attempts in results)

    def account_target(results: Sequence[tuple[Any, int]]) -> None:
        meta["source_requests"] += len(results)
        meta["list_requests"] += len(results)
        meta["target_filter_requests"] += len(results)
        meta["request_attempts"] += sum(
            attempts for _, attempts in results
        )

    try:
        first_results = [fetch_list(main_session, route, 1) for route in HAMAN_ROUTES]
        account_list(first_results)
        first_pages = {
            route.key: result[0] for route, result in zip(HAMAN_ROUTES, first_results)
        }
        declared_last: dict[str, int] = {}
        remaining_items: list[tuple[HamanRoute, int]] = []
        for route in HAMAN_ROUTES:
            last = int(first_pages[route.key]["pager_last"])
            if last < 1 or last > max_pages:
                raise HamanContractError(
                    f"source cap: {route.key} last page {last} exceeds max_pages {max_pages}"
                )
            declared_last[route.key] = last
            remaining_items.extend((route, page) for page in range(2, last + 1))

        remaining_results = parallel_batches(
            remaining_items,
            lambda session, item: fetch_list(session, item[0], item[1]),
        )
        account_list(remaining_results)
        pages_by_route: dict[str, list[dict[str, Any]]] = {
            route.key: [first_pages[route.key]] for route in HAMAN_ROUTES
        }
        for (route, _), (page, _) in zip(remaining_items, remaining_results):
            pages_by_route[route.key].append(page)

        source_by_route: dict[str, list[dict[str, Any]]] = {}
        for route in HAMAN_ROUTES:
            source_by_route[route.key] = _validate_route_pages(
                route,
                pages_by_route[route.key],
                declared_last[route.key],
            )

        overflow_items = [
            (route, declared_last[route.key] + 1) for route in HAMAN_ROUTES
        ]
        overflow_results = parallel_batches(
            overflow_items,
            lambda session, item: fetch_list(session, item[0], item[1]),
        )
        account_list(overflow_results)
        overflow_by_route: dict[str, dict[str, Any]] = {}
        for (route, _), (overflow, _) in zip(overflow_items, overflow_results):
            final = pages_by_route[route.key][-1]
            if (
                overflow["current_page"] != declared_last[route.key]
                or _page_ledger_signature(overflow) != _page_ledger_signature(final)
            ):
                raise HamanContractError(
                    f"{route.key}: immediate post-last page did not clamp exactly"
                )
            overflow_by_route[route.key] = overflow

        membership: dict[str, str] = {}
        overlap_count = 0
        listed: list[dict[str, Any]] = []
        for route in HAMAN_ROUTES:
            for row in source_by_route[route.key]:
                identity = str(row["identity"])
                if identity in membership:
                    overlap_count += 1
                    raise HamanContractError(
                        f"route identity overlap at {identity}: "
                        f"{membership[identity]} and {route.key}"
                    )
                membership[identity] = route.key
                listed.append(row)

        current_rows = [
            row
            for row in listed
            if row["event_end"] is not None and row["event_end"] >= cutoff
        ]
        if len(current_rows) > detail_limit:
            raise HamanContractError(
                f"source cap: {len(current_rows)} current details exceed detail_limit {detail_limit}"
            )

        target_first_results = [
            fetch_target_list(main_session, route, 1)
            for route in HAMAN_ROUTES
        ]
        account_target(target_first_results)
        target_first_pages = {
            route.key: result[0]
            for route, result in zip(HAMAN_ROUTES, target_first_results)
        }
        target_declared_last: dict[str, int] = {}
        target_remaining_items: list[tuple[HamanRoute, int]] = []
        for route in HAMAN_ROUTES:
            last = int(target_first_pages[route.key]["pager_last"])
            if last < 1 or last > max_pages:
                raise HamanContractError(
                    f"source cap: {route.key} target-filter last page "
                    f"{last} exceeds max_pages {max_pages}"
                )
            target_declared_last[route.key] = last
            target_remaining_items.extend(
                (route, page) for page in range(2, last + 1)
            )

        target_remaining_results = parallel_batches(
            target_remaining_items,
            lambda session, item: fetch_target_list(
                session,
                item[0],
                item[1],
            ),
        )
        account_target(target_remaining_results)
        target_pages_by_route: dict[str, list[dict[str, Any]]] = {
            route.key: [target_first_pages[route.key]]
            for route in HAMAN_ROUTES
        }
        for (route, _), (page, _) in zip(
            target_remaining_items,
            target_remaining_results,
        ):
            target_pages_by_route[route.key].append(page)

        target_membership: set[str] = set()
        for route in HAMAN_ROUTES:
            route_membership = _validate_target_route_pages(
                route,
                target_pages_by_route[route.key],
                target_declared_last[route.key],
            )
            overlap = target_membership & route_membership
            if overlap:
                raise HamanContractError(
                    "target-filter identity crossed route boundary at "
                    f"{sorted(overlap)[0]}"
                )
            target_membership.update(route_membership)
        source_identities = set(membership)
        if not target_membership <= source_identities:
            raise HamanContractError(
                "target-filter identity escaped complete source ledger"
            )
        current_identities = {
            str(row["identity"]) for row in current_rows
        }
        missing_target_ids = current_identities - target_membership
        if missing_target_ids:
            raise HamanContractError(
                "current course is not proven by the county-resident target "
                f"partition: {sorted(missing_target_ids)[0]}"
            )
        for row in current_rows:
            row["target"] = _CURRENT_TARGET_LABEL

        target_overflow_items = [
            (route, target_declared_last[route.key] + 1)
            for route in HAMAN_ROUTES
        ]
        target_overflow_results = parallel_batches(
            target_overflow_items,
            lambda session, item: fetch_target_list(
                session,
                item[0],
                item[1],
            ),
        )
        account_target(target_overflow_results)
        target_overflow_by_route: dict[str, dict[str, Any]] = {}
        for (route, _), (overflow, _) in zip(
            target_overflow_items,
            target_overflow_results,
        ):
            final = target_pages_by_route[route.key][-1]
            if (
                overflow["current_page"]
                != target_declared_last[route.key]
                or _target_page_ledger_signature(overflow)
                != _target_page_ledger_signature(final)
            ):
                raise HamanContractError(
                    f"{route.key}: target-filter post-last page did not "
                    "clamp exactly"
                )
            target_overflow_by_route[route.key] = overflow

        detail_results = parallel_batches(
            current_rows,
            lambda session, row: fetch_detail(session, row),
        )
        account_detail(detail_results)
        for row, (parsed, _) in zip(current_rows, detail_results):
            row["detail"] = parsed

        recheck_items: list[tuple[HamanRoute, int]] = []
        for route in HAMAN_ROUTES:
            last = declared_last[route.key]
            recheck_items.extend(((route, 1), (route, last), (route, last + 1)))
        recheck_results = parallel_batches(
            recheck_items,
            lambda session, item: fetch_list(session, item[0], item[1]),
        )
        account_list(recheck_results)
        for index, route in enumerate(HAMAN_ROUTES):
            first_recheck = recheck_results[index * 3][0]
            final_recheck = recheck_results[index * 3 + 1][0]
            overflow_recheck = recheck_results[index * 3 + 2][0]
            if (
                first_recheck["current_page"] != 1
                or final_recheck["current_page"] != declared_last[route.key]
                or overflow_recheck["current_page"] != declared_last[route.key]
                or _page_data_signature(first_recheck)
                != _page_data_signature(pages_by_route[route.key][0])
                or _page_data_signature(final_recheck)
                != _page_data_signature(pages_by_route[route.key][-1])
                or _page_data_signature(overflow_recheck)
                != _page_data_signature(overflow_by_route[route.key])
            ):
                raise HamanContractError(
                    f"{route.key}: source boundaries changed during detail collection"
                )

        target_recheck_items: list[tuple[HamanRoute, int]] = []
        for route in HAMAN_ROUTES:
            last = target_declared_last[route.key]
            target_recheck_items.extend(
                ((route, 1), (route, last), (route, last + 1))
            )
        target_recheck_results = parallel_batches(
            target_recheck_items,
            lambda session, item: fetch_target_list(
                session,
                item[0],
                item[1],
            ),
        )
        account_target(target_recheck_results)
        for index, route in enumerate(HAMAN_ROUTES):
            first_recheck = target_recheck_results[index * 3][0]
            final_recheck = target_recheck_results[index * 3 + 1][0]
            overflow_recheck = target_recheck_results[index * 3 + 2][0]
            if (
                first_recheck["current_page"] != 1
                or final_recheck["current_page"]
                != target_declared_last[route.key]
                or overflow_recheck["current_page"]
                != target_declared_last[route.key]
                or _target_page_signature(first_recheck)
                != _target_page_signature(
                    target_pages_by_route[route.key][0]
                )
                or _target_page_signature(final_recheck)
                != _target_page_signature(
                    target_pages_by_route[route.key][-1]
                )
                or _target_page_signature(overflow_recheck)
                != _target_page_signature(
                    target_overflow_by_route[route.key]
                )
            ):
                raise HamanContractError(
                    f"{route.key}: target-filter boundaries changed during "
                    "detail collection"
                )

        rows = [_output_row(row) for row in current_rows]
        failures = [error for row in rows for error in _privacy_errors(row)]
        if failures:
            raise HamanContractError("; ".join(sorted(set(failures))))
        before_ids = {str(row["provider_course_id"]) for row in rows}
        if dedupe_rows is not None:
            rows = [dict(row) for row in dedupe_rows(rows)]
        after_ids = [str(row.get("provider_course_id", "")) for row in rows]
        if len(after_ids) != len(set(after_ids)) or set(after_ids) != before_ids:
            raise HamanContractError("dedupe_rows changed complete identity cardinality")
        failures = [error for row in rows for error in _privacy_errors(row)]
        if failures:
            raise HamanContractError("; ".join(sorted(set(failures))))

        unknown_periods = sum(row["event_end"] is None for row in listed)
        unknown_reception_periods = sum(
            row["apply_end"] is None for row in listed
        )
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "source_total_count": len(listed),
                "current_source_count": len(current_rows),
                "expired_source_count": len(listed) - len(current_rows) - unknown_periods,
                "historical_unknown_period_count": unknown_periods,
                "historical_unknown_reception_period_count": unknown_reception_periods,
                "row_count": len(rows),
                "detail_pages": len(detail_results),
                "route_source_counts": {
                    route.key: len(source_by_route[route.key]) for route in HAMAN_ROUTES
                },
                "route_current_counts": dict(
                    Counter(str(row["route"]) for row in current_rows)
                )
                | {
                    route.key: sum(row["route"] == route.key for row in current_rows)
                    for route in HAMAN_ROUTES
                },
                "route_pages": declared_last,
                "route_final_sizes": {
                    route.key: len(pages_by_route[route.key][-1]["rows"])
                    for route in HAMAN_ROUTES
                },
                "source_branch_counts": dict(
                    Counter(str(row["branch"]) for row in listed)
                ),
                "current_branch_counts": dict(
                    Counter(str(row["branch"]) for row in current_rows)
                ),
                "source_raw_status_counts": dict(
                    Counter(str(row["raw_status"]) for row in listed)
                ),
                "current_raw_status_counts": dict(
                    Counter(str(row["raw_status"]) for row in current_rows)
                ),
                "status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "current_ids": [str(row["identity"]) for row in current_rows],
                "route_identity_union_count": len(membership),
                "route_identity_overlap_count": overlap_count,
                "target_filter_value": _CURRENT_TARGET_VALUE,
                "target_filter_label": _CURRENT_TARGET_LABEL,
                "target_filter_identity_count": len(target_membership),
                "target_filter_current_match_count": len(
                    current_identities & target_membership
                ),
                "target_filter_missing_current_count": len(
                    missing_target_ids
                ),
                "target_filter_route_pages": target_declared_last,
                "target_filter_full_page_requests": sum(
                    target_declared_last.values()
                ),
                "target_filter_post_last_requests": len(
                    target_overflow_items
                ),
                "target_filter_recheck_requests": len(
                    target_recheck_items
                ),
                "application_control_count": sum(
                    int(parsed["application_control_count"])
                    for parsed, _ in detail_results
                ),
                "attachment_links_discarded": sum(
                    int(parsed["attachment_count"]) for parsed, _ in detail_results
                ),
                "teacher_blocks_discarded": sum(
                    int(parsed["teacher_count"]) for parsed, _ in detail_results
                ),
                "free_text_tabs_discarded": sum(
                    int(parsed["discarded_tab_count"]) for parsed, _ in detail_results
                ),
                "full_page_requests": sum(declared_last.values()),
                "post_last_requests": len(HAMAN_ROUTES),
                "full_recheck_requests": len(recheck_items),
                "pagination_complete": True,
                "post_last_clamp_verified": True,
                "route_identity_disjoint": True,
                "details_complete": True,
                "stable_boundary_recheck": True,
                "privacy_boundary_complete": True,
                "semantic_quality_passed": True,
                "snapshot_complete": True,
                "no_current_data": not rows,
                "configured_collection_error": "",
            }
        )
        return rows, HAMAN_PARSER, meta
    except Exception as exc:
        if "source cap:" in _clean(exc):
            meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["snapshot_complete"] = False
        meta["semantic_quality_passed"] = False
        return [], HAMAN_PARSER, meta
    finally:
        close = getattr(main_session, "close", None)
        if callable(close):
            close()


collect = collect_haman_education


__all__ = [
    "HAMAN_AGGREGATE_CANDIDATE_ID", "HAMAN_AGGREGATE_PROVIDER",
    "HAMAN_AGGREGATE_URL", "HAMAN_BRANCH_CODES", "HAMAN_CANONICAL_URL",
    "HAMAN_CANONICAL_URL_SHA256", "HAMAN_CANDIDATE_AUDIT",
    "HAMAN_DOWNLOAD_CANDIDATE_ID", "HAMAN_DOWNLOAD_CANDIDATE_URL",
    "HAMAN_FIELDS_NEVER_PERSISTED", "HAMAN_HISTORICAL_PERIOD_EXCEPTIONS",
    "HAMAN_HISTORICAL_RECEPTION_PERIOD_EXCEPTIONS",
    "HAMAN_LIVE_AUDIT_BASELINE", "HAMAN_MUNICIPALITY_CODE",
    "HAMAN_MUNICIPALITY_NAME", "HAMAN_OWNER_BOUNDARIES",
    "HAMAN_OWNERSHIP_SCOPE", "HAMAN_PARSER", "HAMAN_PROVIDER",
    "HAMAN_PROVIDER_ALIAS_AUDIT", "HAMAN_RECOMMENDED_DETAIL_LIMIT",
    "HAMAN_RECOMMENDED_MAX_PAGES", "HAMAN_REPAIRED_HISTORICAL_PERIODS",
    "HAMAN_ROUTES", "HAMAN_STATIC_DIRECTIONS_CANDIDATE_ID",
    "HAMAN_STATIC_DIRECTIONS_PROVIDER", "HAMAN_STATIC_DIRECTIONS_URL",
    "HAMAN_STATIC_INTRO_CANDIDATE_ID", "HAMAN_STATIC_INTRO_PROVIDER",
    "HAMAN_STATIC_INTRO_URL", "HamanContractError", "collect",
    "collect_haman_education", "is_haman_education_target", "is_target",
]
