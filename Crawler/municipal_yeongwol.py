"""Fail-closed collector for Yeongwol-gun's official lifelong courses.

The promotion candidates contain two filters of the same catalogue (night
classes and vocational education), a portal homepage, an institution
directory, an unrelated expired donation detail, and a separate provincial
education-library source.  This module owns only the official, unfiltered
Yeongwol lifelong-course catalogue.

Every advertised page, the immediate empty page after the last page, and a
second copy of page one are required before a snapshot can be returned.  Each
current or future row is then checked against its course-bound detail page.
Instructor/contact fields, addresses, attachments, free-form descriptions,
and source HTML are deliberately not persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YEONGWOL_PROVIDER = "MUNI_LLL_YW_GO_KR_EF1034A0"
YEONGWOL_CANONICAL_CANDIDATE_ID = "MUNI_IR_C8CD97987323"
YEONGWOL_HOST = "lll.yw.go.kr"
YEONGWOL_MUNICIPALITY_CODE = "5175000000"
YEONGWOL_MUNICIPALITY_NAME = "강원특별자치도 영월군"
YEONGWOL_LIST_PATH = "/ywedu/courseList.do"
YEONGWOL_DETAIL_PATH = "/ywedu/courseView.do"
YEONGWOL_CANONICAL_URL = (
    f"https://{YEONGWOL_HOST}{YEONGWOL_LIST_PATH}?key=241&searchCnd=srcTitle"
)
YEONGWOL_EQUIVALENT_UNFILTERED_URL = (
    f"https://{YEONGWOL_HOST}{YEONGWOL_LIST_PATH}?key=241"
)
YEONGWOL_PAGE_SIZE = 10
YEONGWOL_FETCH_ATTEMPTS = 2
YEONGWOL_MAX_WORKERS = 12
YEONGWOL_MAX_HTML_BYTES = 2_000_000
YEONGWOL_PARSER = (
    "yeongwol_official_unfiltered_lifelong_courses+all_pages+empty_sentinel+"
    "stable_page1+current_details+course_bound_application_control+pii_allowlist"
)
YEONGWOL_OWNERSHIP_SCOPE = (
    "yeongwol_official_unfiltered_lifelong_course_catalogue"
)

YEONGWOL_NIGHT_SUBSET_URL = (
    f"https://{YEONGWOL_HOST}{YEONGWOL_LIST_PATH}?key=241&ptimeYn=N&pageIndex=1"
)
YEONGWOL_CAREER_SUBSET_URL = (
    f"https://{YEONGWOL_HOST}{YEONGWOL_LIST_PATH}?"
    "key=241&srcEdu=&srcField=&srcCategory=FEISJ03&searchCnd=srcTitle"
)
YEONGWOL_PORTAL_URL = f"https://{YEONGWOL_HOST}/ywedu/index.do"
YEONGWOL_INSTITUTION_DIRECTORY_URL = (
    f"https://{YEONGWOL_HOST}/ywedu/edcList.do?key=196"
)
YEONGWOL_LIBRARY_HOMEPAGE_URL = "https://lib.gwe.go.kr/ywlib/main"
YEONGWOL_LIBRARY_LIST_URL = (
    "https://lib.gwe.go.kr/ywlib/menu/2700/lecture-event/list/all"
)
YEONGWOL_DONATION_DETAIL_URL = (
    "https://www.nanumkorea.go.kr/ctrr/ctrrView.do?sn=2265"
)

# The first three providers are generated wrappers for the same Yeongwol
# source.  B5652F48 is the deterministic identity of the equivalent key-only
# unfiltered URL and is retained as a prospective ownership alias.
YEONGWOL_ALIAS_PROVIDERS = frozenset(
    {
        "MUNI_LLL_YW_GO_KR_022DBD52",
        "MUNI_LLL_YW_GO_KR_55D3935B",
        "MUNI_LLL_YW_GO_KR_DCB266C5",
        "MUNI_LLL_YW_GO_KR_B5652F48",
    }
)
YEONGWOL_ALIAS_CANDIDATE_IDS = frozenset(
    {"MUNI_IR_3ED99D175BC0", "MUNI_IR_5CDB62B96391"}
)
YEONGWOL_EXCLUDED_CANDIDATE_IDS = frozenset(
    {"MUNI_IR_CB6DEBAAEBD3", "MUNI_IR_F2564D73C044"}
)

YEONGWOL_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_IR_0CA08FDF06B7": {
        "decision": "separate_library_owner_homepage_alias",
        "provider": "MUNI_LIB_GWE_GO_KR_90FD6E6A",
        "url": YEONGWOL_LIBRARY_HOMEPAGE_URL,
        "owner": "MUNI_LIB_GWE_GO_KR_90FD6E6A",
        "reason": (
            "separate education-office library catalogue; one expired raw row "
            "and zero current/future rows on the audit date"
        ),
    },
    "MUNI_IR_3ED99D175BC0": {
        "decision": "subset_category_alias",
        "provider": "MUNI_LLL_YW_GO_KR_55D3935B",
        "url": YEONGWOL_CAREER_SUBSET_URL,
        "owner": YEONGWOL_PROVIDER,
        "reason": "srcCategory=FEISJ03 contains only vocational education",
    },
    "MUNI_IR_5CDB62B96391": {
        "decision": "subset_time_alias",
        "provider": "MUNI_LLL_YW_GO_KR_DCB266C5",
        "url": YEONGWOL_NIGHT_SUBSET_URL,
        "owner": YEONGWOL_PROVIDER,
        "reason": "ptimeYn=N contains only night classes",
    },
    "MUNI_IR_CB6DEBAAEBD3": {
        "decision": "excluded_expired_donation_detail",
        "provider": "MUNI_WWW_NANUMKOREA_GO_KR_FFDE14C8",
        "url": YEONGWOL_DONATION_DETAIL_URL,
        "owner": "",
        "reason": "expired 1365 donation-fundraising detail, not a course list",
    },
    "MUNI_IR_F2564D73C044": {
        "decision": "excluded_education_institution_directory",
        "provider": "MUNI_LLL_YW_GO_KR_022DBD52",
        "url": YEONGWOL_INSTITUTION_DIRECTORY_URL,
        "owner": "",
        "reason": "76 institutions across eight pages and no course records",
    },
}

YEONGWOL_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": YEONGWOL_CANONICAL_URL,
    "canonical_candidate_id": YEONGWOL_CANONICAL_CANDIDATE_ID,
    "unfiltered_historical_rows": 889,
    "unfiltered_pages": 89,
    "immediate_empty_page": 90,
    "current_or_future_rows": 1,
    "current_details_verified": 1,
    "source_status_counts": {"접수예정": 0, "접수중": 0, "접수마감": 889},
    "night_subset_rows": 324,
    "career_subset_rows": 100,
    "night_career_intersection_rows": 39,
    "portal_featured_rows": 4,
    "institution_directory_rows": 76,
    "institution_directory_pages": 8,
    "library_raw_rows": 1,
    "library_current_or_future_rows": 0,
    "conclusion": "one unfiltered owner supersedes three partial generic wrappers",
}

YEONGWOL_PII_FIELDS_DISCARDED = (
    "강사명",
    "담당자",
    "연락처",
    "주소",
    "교육내용",
    "비고",
    "첨부파일",
    "재료비",
    "자격증발급비",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_SHORT_DATE_RE = re.compile(r"(?<!\d)(\d{2})-(\d{2})-(\d{2})(?!\d)")
_LONG_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:년|[-./])\s*(\d{1,2})\s*"
    r"(?:월|[-./])\s*(\d{1,2})\s*(?:일)?(?!\d)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
}
_INFO_FIELDS = ("교육시간", "접수인원", "교육기관", "대상")
_COURSE_REQUIRED_FIELDS = frozenset(
    {
        "강좌명",
        "분야",
        "교육대상",
        "교육장소",
        "지역",
        "모집인원",
        "접수기간",
        "교육기간",
        "교육시간",
        "수강료",
    }
)
_INSTITUTION_REQUIRED_FIELDS = frozenset({"교육기관"})
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "source_status",
        "source_category",
        "source_period",
        "source_schedule",
        "source_capacity_current",
        "source_capacity_total",
        "education_institution",
        "source_target",
        "source_application_period",
        "source_venue",
        "source_region",
        "source_fee",
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
        "contact",
        "contact_name",
        "phone",
        "email",
        "address",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
        "material_fee",
    }
)


class YeongwolContractError(ValueError):
    """Raised when the official source no longer satisfies its contract."""


@dataclass
class _ListPage:
    rows: list[dict[str, Any]]
    total: int
    last: int
    errors: list[str]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _clean(value).casefold(), flags=re.UNICODE)


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
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


def _compare_url(value: Any, *, drop_paging: bool = False) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.fragment
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        return ""
    pairs = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not (drop_paging and key.casefold() in {"pageindex", "pageunit"})
    ]
    query = urlencode(sorted(pairs))
    return f"https://{parsed.hostname.lower()}{parsed.path}" + (
        f"?{query}" if query else ""
    )


def is_yeongwol_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == YEONGWOL_PROVIDER
        and _compare_url(_target_value(target, "url"))
        == _compare_url(YEONGWOL_CANONICAL_URL)
    )


def is_yeongwol_excluded_candidate(target: Any) -> bool:
    candidate_id = _clean(_target_value(target, "candidate_id"))
    compared = _compare_url(_target_value(target, "url"), drop_paging=True)
    return candidate_id in YEONGWOL_EXCLUDED_CANDIDATE_IDS or compared in {
        _compare_url(YEONGWOL_INSTITUTION_DIRECTORY_URL, drop_paging=True),
        _compare_url(YEONGWOL_DONATION_DETAIL_URL, drop_paging=True),
    }


def is_yeongwol_owned_alias_target(target: Any) -> bool:
    if is_yeongwol_excluded_candidate(target):
        return False
    provider = _clean(_target_value(target, "provider"))
    candidate_id = _clean(_target_value(target, "candidate_id"))
    compared = _compare_url(_target_value(target, "url"), drop_paging=True)
    return bool(
        provider in YEONGWOL_ALIAS_PROVIDERS
        or candidate_id in YEONGWOL_ALIAS_CANDIDATE_IDS
        or compared
        in {
            _compare_url(YEONGWOL_NIGHT_SUBSET_URL, drop_paging=True),
            _compare_url(YEONGWOL_CAREER_SUBSET_URL, drop_paging=True),
            _compare_url(YEONGWOL_PORTAL_URL, drop_paging=True),
            _compare_url(
                YEONGWOL_EQUIVALENT_UNFILTERED_URL, drop_paging=True
            ),
        }
    )


def is_yeongwol_separate_library_target(target: Any) -> bool:
    candidate_id = _clean(_target_value(target, "candidate_id"))
    provider = _clean(_target_value(target, "provider"))
    compared = _compare_url(_target_value(target, "url"), drop_paging=True)
    return bool(
        candidate_id == "MUNI_IR_0CA08FDF06B7"
        or provider == "MUNI_LIB_GWE_GO_KR_90FD6E6A"
        or compared
        in {
            _compare_url(YEONGWOL_LIBRARY_HOMEPAGE_URL, drop_paging=True),
            _compare_url(YEONGWOL_LIBRARY_LIST_URL, drop_paging=True),
        }
    )


def yeongwol_list_url(page: Any = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return ""
    if page == 1:
        return YEONGWOL_CANONICAL_URL
    return (
        f"https://{YEONGWOL_HOST}{YEONGWOL_LIST_PATH}?"
        + urlencode(
            {"key": "241", "searchCnd": "srcTitle", "pageIndex": page}
        )
    )


def yeongwol_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not re.fullmatch(r"\d+", value):
        return ""
    return (
        f"https://{YEONGWOL_HOST}{YEONGWOL_DETAIL_PATH}?"
        + urlencode({"key": "241", "course": value})
    )


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://lll.yw.go.kr/)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    final = urlparse(_clean(getattr(response, "url", url)))
    if final.scheme.lower() != "https" or final.hostname != YEONGWOL_HOST:
        raise ValueError("response left the official HTTPS host")
    if final.username or final.password or final.port or final.fragment:
        raise ValueError("response URL is not canonical HTTPS")
    content_type = _clean(response.headers.get("Content-Type")).lower()
    if "html" not in content_type:
        raise ValueError("response is not HTML")
    content = response.content
    if len(content) > YEONGWOL_MAX_HTML_BYTES:
        raise ValueError("HTML response exceeded the bounded size limit")
    return BeautifulSoup(content, "html.parser")


def _close_quietly(value: Any) -> None:
    try:
        close = getattr(value, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > YEONGWOL_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > YEONGWOL_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return _coerce_soup(bytes(content))
    raise TypeError("fetcher must return HTML, bytes, a response, or BeautifulSoup")


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
        for _attempt in range(YEONGWOL_FETCH_ATTEMPTS):
            session = session_factory()
            try:
                return key, parser(_coerce_soup(fetcher(session, url, timeout)))
            except Exception as exc:
                last_error = exc
            finally:
                _close_quietly(session)
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


def _single_text(root: Any, selector: str, field: str) -> str:
    nodes = root.select(selector) if root is not None else []
    if len(nodes) != 1:
        raise YeongwolContractError(f"{field}: expected one node")
    value = _clean(nodes[0].get_text(" ", strip=True))
    if not value:
        raise YeongwolContractError(f"{field}: empty")
    return value


def _form_value(form: Any, name: str) -> tuple[int, str]:
    nodes = form.select(f'[name="{name}"]') if form is not None else []
    if len(nodes) != 1:
        return len(nodes), ""
    node = nodes[0]
    if node.name == "select":
        selected = node.select("option[selected]")
        option = selected[0] if len(selected) == 1 else node.select_one("option")
        return 1, _clean(option.get("value") if option is not None else "")
    return 1, _clean(node.get("value"))


def _list_form_errors(soup: BeautifulSoup, page: int) -> list[str]:
    forms = soup.select("form[name='bbsNttSearchForm'].boardSearchForm")
    if len(forms) != 1:
        return [f"page {page}: search form missing or duplicated"]
    form = forms[0]
    errors: list[str] = []
    action = urlparse(urljoin(YEONGWOL_CANONICAL_URL, _clean(form.get("action"))))
    if _clean(form.get("method")).lower() != "get" or (
        action.scheme,
        action.hostname,
        action.path,
        action.query,
        action.fragment,
    ) != ("https", YEONGWOL_HOST, YEONGWOL_LIST_PATH, "", ""):
        errors.append(f"page {page}: search form method/action changed")
    for name, expected in (
        ("key", "241"),
        ("srcTitle", ""),
        ("srcEduName", ""),
        ("searchCnd", "srcTitle"),
        ("searchKrwd", ""),
        ("srcCategory", ""),
    ):
        count, value = _form_value(form, name)
        if count != 1 or value != expected:
            errors.append(f"page {page}: unfiltered form field {name} changed")
    checkboxes = form.select("input[type='checkbox'][name]")
    expected_names = {
        "eduTagetYn",
        "weekDayYn",
        "ptimeYn",
        "appStatusYn",
        "priceYn",
        "recruitmentYn",
        "localYn",
        "edcCategoryYn",
    }
    names = {_clean(node.get("name")) for node in checkboxes}
    if names != expected_names or any(node.has_attr("checked") for node in checkboxes):
        errors.append(f"page {page}: unfiltered checkbox contract changed")
    return errors


def _short_date_pair(value: Any, field: str) -> tuple[date, date, str]:
    text = _clean(value)
    matches = list(_SHORT_DATE_RE.finditer(text))
    if len(matches) != 2:
        raise YeongwolContractError(f"{field}: expected exactly two short dates")
    dates: list[date] = []
    for match in matches:
        try:
            dates.append(
                date(2000 + int(match.group(1)), int(match.group(2)), int(match.group(3)))
            )
        except ValueError as exc:
            raise YeongwolContractError(f"{field}: invalid calendar date") from exc
    if dates[0] > dates[1]:
        raise YeongwolContractError(f"{field}: reversed dates")
    schedule = _clean(text[matches[-1].end() :]).strip("~ ")
    return dates[0], dates[1], schedule


def _long_date_pair(value: Any, field: str) -> tuple[date, date]:
    matches = _LONG_DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise YeongwolContractError(f"{field}: expected exactly two dates")
    dates: list[date] = []
    for year, month, day_value in matches:
        try:
            dates.append(date(int(year), int(month), int(day_value)))
        except ValueError as exc:
            raise YeongwolContractError(f"{field}: invalid calendar date") from exc
    if dates[0] > dates[1]:
        raise YeongwolContractError(f"{field}: reversed dates")
    return dates[0], dates[1]


def _integers(value: Any, field: str) -> list[int]:
    values = [int(item.replace(",", "")) for item in re.findall(r"[\d,]+", _clean(value))]
    if not values:
        raise YeongwolContractError(f"{field}: integer missing")
    return values


def _schedule_signature(value: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    text = _clean(value)
    days = tuple(re.findall(r"([월화수목금토일])(?:요일)?", text))
    times: list[str] = []
    for hour, minute in re.findall(
        r"(?<!\d)(\d{1,2})(?:\s*시\s*|\s*:\s*)(\d{2})(?:\s*분)?",
        text,
    ):
        if int(hour) > 23 or int(minute) > 59:
            raise YeongwolContractError("education schedule has invalid time")
        times.append(f"{int(hour):02d}:{int(minute):02d}")
    if not days or len(times) != 2:
        raise YeongwolContractError("education schedule structure changed")
    return days, tuple(times)


def _info_pairs(card: Any) -> dict[str, str]:
    items = card.select("ul.edu_info > li")
    if len(items) != len(_INFO_FIELDS):
        raise YeongwolContractError("list info field count changed")
    pairs: dict[str, str] = {}
    order: list[str] = []
    for item in items:
        labels = item.select(":scope > .info_title")
        values = item.select(":scope > .info_text")
        if len(labels) != 1 or len(values) != 1:
            raise YeongwolContractError("list info label/value structure changed")
        label = _clean(labels[0].get_text(" ", strip=True))
        value = _clean(values[0].get_text(" ", strip=True))
        if not label or label in pairs:
            raise YeongwolContractError("list info field empty or duplicated")
        order.append(label)
        pairs[label] = value
    if tuple(order) != _INFO_FIELDS:
        raise YeongwolContractError("list info field order changed")
    return pairs


def _parse_identity_url(value: Any) -> str:
    parsed = urlparse(urljoin(YEONGWOL_CANONICAL_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != YEONGWOL_HOST
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
        or parsed.path != YEONGWOL_DETAIL_PATH
        or set(query) != {"key", "course"}
        or query.get("key") != ["241"]
        or len(query.get("course", [])) != 1
        or not re.fullmatch(r"\d+", query["course"][0])
    ):
        raise YeongwolContractError("course detail identity URL changed")
    return query["course"][0]


def _base_row(
    *,
    identity: str,
    page: int,
    title: str,
    category: str,
    source_status: str,
    start: date,
    end: date,
    schedule: str,
    capacity_current: int,
    capacity_total: int,
    institution: str,
    target: str,
) -> dict[str, Any]:
    normalized_status = _STATUS_MAP[source_status]
    branch = institution or "영월군 평생학습"
    return {
        "provider": YEONGWOL_PROVIDER,
        "provider_course_id": f"{YEONGWOL_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": f"yeongwol:{_normalized(branch)}",
        "preserve_branch": True,
        "provider_organizer": institution,
        "category": category,
        "program_type": "교육",
        "raw_url": yeongwol_detail_url(identity),
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
        "apply_period": "",
        "apply_start": "",
        "apply_end": "",
        "schedule_raw": schedule,
        "capacity": f"{capacity_current}/{capacity_total}",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "target": target,
        "venue": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": YEONGWOL_PARSER,
        "municipality_code": YEONGWOL_MUNICIPALITY_CODE,
        "municipality_full_name": YEONGWOL_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": page,
            "source_status": source_status,
            "source_category": category,
            "source_period": f"{start.isoformat()} ~ {end.isoformat()}",
            "source_schedule": schedule,
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "education_institution": institution,
            "source_target": target,
            "source_application_period": "",
            "source_venue": "",
            "source_region": "",
            "source_fee": "",
            "service_family": "education",
            "list_control_contract": "",
            "application_control_present": False,
            "application_control_contract": "",
            "application_control_verified": False,
            "detail_verified": False,
        },
    }


def _parse_card(card: Any, page: int, cutoff: date) -> dict[str, Any]:
    wraps = card.select(":scope > .edu_wrap")
    if len(wraps) != 1:
        raise YeongwolContractError("course card wrapper changed")
    wrap = wraps[0]
    status_node = wrap.select(":scope > .edu_state")
    category_node = wrap.select(":scope > .edu_type")
    title_nodes = wrap.select(":scope > a.edu_title[href]")
    info_nodes = wrap.select(":scope > ul.edu_info")
    buttons = card.select(":scope > a.edu_btn")
    if any(len(nodes) != 1 for nodes in (status_node, category_node, title_nodes, info_nodes, buttons)):
        raise YeongwolContractError("course card direct-child structure changed")
    identity = _parse_identity_url(title_nodes[0].get("href"))
    title = _clean(title_nodes[0].get_text(" ", strip=True))
    category = _clean(category_node[0].get_text(" ", strip=True))
    source_status = _clean(status_node[0].get_text(" ", strip=True))
    if not title or not category:
        raise YeongwolContractError(f"course {identity}: title/category empty")
    if source_status not in _STATUS_MAP:
        raise YeongwolContractError(f"course {identity}: unknown application status")
    pairs = _info_pairs(wrap)
    start, end, schedule = _short_date_pair(
        pairs["교육시간"], f"course {identity} education period"
    )
    capacity_values = _integers(
        pairs["접수인원"], f"course {identity} capacity"
    )
    if len(capacity_values) != 2:
        raise YeongwolContractError(f"course {identity}: capacity pair changed")
    capacity_current, capacity_total = capacity_values
    institution = _clean(pairs["교육기관"])
    target = _clean(pairs["대상"])
    current = end >= cutoff
    if current and (
        not schedule
        or not institution
        or not target
        or capacity_total < 1
        or capacity_current < 0
        or capacity_current > capacity_total
    ):
        raise YeongwolContractError(f"course {identity}: current course field invalid")
    # Historical rows are used only to prove catalogue cardinality and may
    # contain legacy free-form schedules.  Persisted current/future rows must
    # satisfy the normalized weekday/time contract.
    if current and schedule:
        _schedule_signature(schedule)

    button = buttons[0]
    button_text = _clean(button.get_text(" ", strip=True))
    button_href = _clean(button.get("href"))
    button_onclick = _clean(button.get("onclick"))
    normalized_status = _STATUS_MAP[source_status]
    if normalized_status == "CLOSED":
        if (
            button_text != "강좌 신청 마감"
            or button_href != "#"
            or button_onclick
        ):
            raise YeongwolContractError(f"course {identity}: closed control changed")
        control_contract = "closed_list_button_hash_no_handler"
    elif normalized_status == "SCHEDULED":
        if "예정" not in button_text or button_href != "#" or button_onclick:
            raise YeongwolContractError(f"course {identity}: scheduled control changed")
        control_contract = "scheduled_list_button_hash_no_handler"
    else:
        if not ({"신청", "접수"} & set(re.findall(r"신청|접수", button_text))):
            raise YeongwolContractError(f"course {identity}: open control label changed")
        control_contract = "open_list_label_untrusted_until_detail"
    row = _base_row(
        identity=identity,
        page=page,
        title=title,
        category=category,
        source_status=source_status,
        start=start,
        end=end,
        schedule=schedule,
        capacity_current=capacity_current,
        capacity_total=capacity_total,
        institution=institution,
        target=target,
    )
    row["raw_fields"]["list_control_contract"] = control_contract
    return row


def _parse_list(soup: BeautifulSoup, page: int, cutoff: date) -> _ListPage:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "평생학습강좌(★수강신청★) - 영월군 평생학습 평생교육":
        errors.append(f"page {page}: official catalogue title changed")
    roots = soup.select(".p-wrap.bbs.bbs__list")
    if len(roots) != 1:
        return _ListPage([], 0, 1, [f"page {page}: catalogue root missing or duplicated"])
    root = roots[0]
    totals = root.select(".small em[data-mask]")
    if len(totals) != 1 or not re.fullmatch(r"[\d,]+", _clean(totals[0].get_text(" ", strip=True))):
        total = 0
        errors.append(f"page {page}: advertised total missing or duplicated")
    else:
        total = int(_clean(totals[0].get_text(" ", strip=True)).replace(",", ""))
    last = max(1, math.ceil(total / YEONGWOL_PAGE_SIZE))
    errors.extend(_list_form_errors(soup, page))

    last_links = root.select(".p-pagination a.nextEnd[href]")
    if total > YEONGWOL_PAGE_SIZE:
        linked_last = []
        for anchor in last_links:
            query = parse_qs(urlparse(_clean(anchor.get("href"))).query)
            linked_last.extend(query.get("pageIndex", []))
        if linked_last != [str(last)]:
            errors.append(f"page {page}: advertised last-page navigation changed")

    cards = root.select("ul.edu_list > li.edu_item")
    empty_nodes = root.select(".p-empty")
    if cards and empty_nodes:
        errors.append(f"page {page}: rows and empty sentinel coexist")
    if not cards and (
        len(empty_nodes) != 1
        or _clean(empty_nodes[0].get_text(" ", strip=True))
        != "등록된 게시물이 없습니다."
    ):
        errors.append(f"page {page}: empty-page sentinel changed")
    rows: list[dict[str, Any]] = []
    for index, card in enumerate(cards, start=1):
        try:
            rows.append(_parse_card(card, page, cutoff))
        except Exception as exc:
            errors.append(f"page {page} card {index}: {_clean(exc)}")
    return _ListPage(rows, total, last, errors)


def _detail_table(table: Any, expected_heading: str) -> tuple[dict[str, str], list[str]]:
    rows = table.select("tr")
    if not rows:
        return {}, [f"{expected_heading}: table is empty"]
    heading_cells = rows[0].find_all(["th", "td"], recursive=False)
    if (
        len(heading_cells) != 1
        or heading_cells[0].name != "th"
        or _clean(heading_cells[0].get_text(" ", strip=True)) != expected_heading
    ):
        return {}, [f"{expected_heading}: table heading changed"]
    pairs: dict[str, str] = {}
    errors: list[str] = []
    for row in rows[1:]:
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells or len(cells) % 2:
            errors.append(f"{expected_heading}: label/value row changed")
            continue
        for index in range(0, len(cells), 2):
            label_cell, value_cell = cells[index : index + 2]
            if label_cell.name != "th" or value_cell.name != "td":
                errors.append(f"{expected_heading}: label/value order changed")
                continue
            label = _clean(label_cell.get_text(" ", strip=True))
            value = _clean(value_cell.get_text(" ", strip=True))
            if not label or label in pairs:
                errors.append(f"{expected_heading}: field empty or duplicated")
            else:
                pairs[label] = value
    return pairs, errors


def _application_url(control: Any, identity: str) -> str:
    if control.name != "a":
        return ""
    value = urljoin(yeongwol_detail_url(identity), _clean(control.get("href")))
    parsed = urlparse(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != YEONGWOL_HOST
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
        or not parsed.path.startswith("/ywedu/")
        or parsed.path in {YEONGWOL_LIST_PATH, YEONGWOL_DETAIL_PATH}
        or set(query) != {"key", "course"}
        or query.get("key") != ["241"]
        or query.get("course") != [identity]
    ):
        return ""
    return value


def _fee(value: Any) -> tuple[str, int]:
    text = _clean(value)
    if text == "무료":
        return text, 0
    values = _integers(text, "course fee")
    if len(values) != 1 or "원" not in text:
        raise YeongwolContractError("course fee structure changed")
    return text, values[0]


def _validate_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> tuple[dict[str, Any], list[str]]:
    row = dict(listed)
    row["raw_fields"] = dict(listed["raw_fields"])
    identity = _clean(row["raw_fields"]["identity"])
    label = f"course {identity} detail"
    errors: list[str] = []
    roots = soup.select("#contents")
    if len(roots) != 1:
        return row, [f"{label}: root missing or duplicated"]
    root = roots[0]
    tables = root.select("table.bbs_default.view")
    if len(tables) != 2:
        return row, [f"{label}: expected two detail tables"]
    course, course_errors = _detail_table(tables[0], "강좌정보")
    institution, institution_errors = _detail_table(tables[1], "교육기관 정보")
    errors.extend(f"{label}: {item}" for item in course_errors + institution_errors)
    if not _COURSE_REQUIRED_FIELDS <= set(course):
        errors.append(f"{label}: required course fields changed")
    if not _INSTITUTION_REQUIRED_FIELDS <= set(institution):
        errors.append(f"{label}: required institution fields changed")

    if _COURSE_REQUIRED_FIELDS <= set(course) and _INSTITUTION_REQUIRED_FIELDS <= set(institution):
        expected = row["raw_fields"]
        for field, actual, wanted in (
            ("강좌명", course["강좌명"], row["title"]),
            ("분야", course["분야"], expected["source_category"]),
            ("교육대상", course["교육대상"], expected["source_target"]),
            ("교육기관", institution["교육기관"], expected["education_institution"]),
        ):
            if _clean(actual) != _clean(wanted):
                errors.append(f"{label}: {field} list/detail mismatch")
        try:
            detail_start, detail_end = _long_date_pair(
                course["교육기간"], f"{label} education period"
            )
            if (detail_start.isoformat(), detail_end.isoformat()) != (
                row["start_date"],
                row["end_date"],
            ):
                errors.append(f"{label}: education period list/detail mismatch")
        except Exception as exc:
            errors.append(_clean(exc))
        try:
            if _schedule_signature(course["교육시간"]) != _schedule_signature(
                expected["source_schedule"]
            ):
                errors.append(f"{label}: education schedule list/detail mismatch")
        except Exception as exc:
            errors.append(_clean(exc))
        try:
            capacity = _integers(course["모집인원"], f"{label} capacity")
            if len(capacity) < 2 or capacity[:2] != [
                row["capacity_current"],
                row["capacity_total"],
            ]:
                errors.append(f"{label}: capacity list/detail mismatch")
        except Exception as exc:
            errors.append(_clean(exc))
        try:
            apply_start, apply_end = _long_date_pair(
                course["접수기간"], f"{label} application period"
            )
            row["apply_period"] = _clean(course["접수기간"])
            row["apply_start"] = apply_start.isoformat()
            row["apply_end"] = apply_end.isoformat()
            row["raw_fields"]["source_application_period"] = row["apply_period"]
        except Exception as exc:
            apply_start = apply_end = None
            errors.append(_clean(exc))
        try:
            fee_text, fee_amount = _fee(course["수강료"])
            row["fee"] = fee_text
            row["fee_amount"] = fee_amount
            row["raw_fields"]["source_fee"] = fee_text
        except Exception as exc:
            errors.append(_clean(exc))
        row["venue"] = _clean(course["교육장소"])
        row["raw_fields"]["source_venue"] = row["venue"]
        row["raw_fields"]["source_region"] = _clean(course["지역"])

        status = _clean(row["status"])
        if status == "OPEN" and (
            apply_start is None or apply_end is None or not (apply_start <= cutoff <= apply_end)
        ):
            errors.append(f"{label}: open status/application period mismatch")
        if status == "SCHEDULED" and (
            apply_start is None or cutoff > apply_start
        ):
            errors.append(f"{label}: scheduled status/application period mismatch")

    back = root.select("a.bbs_btn.list[href]")
    if len(back) != 1 or _clean(back[0].get_text(" ", strip=True)) != "목록":
        errors.append(f"{label}: list control changed")
    else:
        back_url = urlparse(urljoin(row["raw_url"], _clean(back[0].get("href"))))
        back_query = parse_qs(back_url.query, keep_blank_values=True)
        if (
            back_url.scheme != "https"
            or back_url.hostname != YEONGWOL_HOST
            or back_url.path != YEONGWOL_LIST_PATH
            or back_query.get("key") != ["241"]
        ):
            errors.append(f"{label}: list control destination changed")

    controls = root.select("a.bbs_btn:not(.list), button.bbs_btn:not(.list), input.bbs_btn:not(.list)")
    status = _clean(row["status"])
    if status == "OPEN":
        if len(controls) != 1:
            errors.append(f"{label}: open course has no unique application control")
        else:
            control_text = _clean(
                controls[0].get("value") or controls[0].get_text(" ", strip=True)
            )
            application_url = _application_url(controls[0], identity)
            if (
                not application_url
                or not re.search(r"신청|접수", control_text)
            ):
                errors.append(f"{label}: application control is not safely course-bound")
            else:
                row["application_url"] = application_url
                row["application_type"] = "ONLINE_RESERVATION"
                row["application_method"] = "온라인"
                row["application_methods"] = ["온라인"]
                row["reservation_available"] = True
                row["raw_fields"]["application_control_present"] = True
                row["raw_fields"]["application_control_contract"] = (
                    "official_https_same_host_course_bound_anchor"
                )
    elif controls:
        errors.append(f"{label}: inactive course exposes application control")
    else:
        row["raw_fields"]["application_control_contract"] = (
            "inactive_detail_has_no_application_control"
        )
    row["raw_fields"]["application_control_verified"] = not errors
    row["raw_fields"]["detail_verified"] = not errors
    return row, errors


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
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
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr(
        {key: value for key, value in row.items() if key not in {"raw_url", "application_url"}}
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


def collect_yeongwol_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 120,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = YEONGWOL_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Yeongwol education snapshot."""

    meta = _base_meta()
    if not is_yeongwol_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Yeongwol owner"
        )
        return [], YEONGWOL_PARSER, meta
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
        return [], YEONGWOL_PARSER, meta
    try:
        cutoff = _today(today)
    except ValueError as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], YEONGWOL_PARSER, meta

    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    errors: list[str] = []
    initial, initial_fetch_errors = _fetch_parse_many(
        [
            (
                ("list", 1, "data"),
                yeongwol_list_url(1),
                lambda soup: _parse_list(soup, 1, cutoff),
            )
        ],
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(initial_fetch_errors)
    meta["pages"] += len(initial)
    meta["list_requests"] += len(initial)
    first = initial.get(("list", 1, "data"))
    if not isinstance(first, _ListPage):
        errors.append("page 1: response missing")
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], YEONGWOL_PARSER, meta
    errors.extend(first.errors)
    total, last = first.total, first.last
    required_list_requests = last + 2
    meta.update(
        {
            "source_total": total,
            "declared_pages": last,
            "required_list_requests": required_list_requests,
        }
    )
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
        return [], YEONGWOL_PARSER, meta
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], YEONGWOL_PARSER, meta

    items: list[tuple[Any, str, Callable[[BeautifulSoup], Any]]] = []
    for page in range(2, last + 1):
        items.append(
            (
                ("list", page, "data"),
                yeongwol_list_url(page),
                lambda soup, current_page=page: _parse_list(
                    soup, current_page, cutoff
                ),
            )
        )
    items.extend(
        [
            (
                ("list", last + 1, "sentinel"),
                yeongwol_list_url(last + 1),
                lambda soup, current_page=last + 1: _parse_list(
                    soup, current_page, cutoff
                ),
            ),
            (
                ("list", 1, "recheck"),
                yeongwol_list_url(1),
                lambda soup: _parse_list(soup, 1, cutoff),
            ),
        ]
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
    meta["sentinel_requests"] = int(("list", last + 1, "sentinel") in remaining)
    meta["stability_rechecks"] = int(("list", 1, "recheck") in remaining)

    all_rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    signatures: dict[int, tuple[tuple[Any, ...], ...]] = {}
    for page in range(1, last + 1):
        parsed = first if page == 1 else remaining.get(("list", page, "data"))
        if not isinstance(parsed, _ListPage):
            errors.append(f"page {page}: response missing")
            continue
        errors.extend(parsed.errors)
        if (parsed.total, parsed.last) != (total, last):
            errors.append(f"page {page}: total/last changed")
        expected = (
            YEONGWOL_PAGE_SIZE
            if page < last
            else total - (last - 1) * YEONGWOL_PAGE_SIZE
        )
        if total == 0:
            expected = 0
        if len(parsed.rows) != expected:
            errors.append(
                f"page {page}: expected {expected} rows, got {len(parsed.rows)}"
            )
        page_counts[page] = len(parsed.rows)
        signatures[page] = _page_signature(parsed.rows)
        all_rows.extend(parsed.rows)

    sentinel = remaining.get(("list", last + 1, "sentinel"))
    if not isinstance(sentinel, _ListPage):
        errors.append(f"page {last + 1}: empty sentinel response missing")
    else:
        errors.extend(sentinel.errors)
        if (sentinel.total, sentinel.last) != (total, last):
            errors.append(f"page {last + 1}: sentinel total/last changed")
        if sentinel.rows:
            errors.append(f"page {last + 1}: sentinel is not empty")
    recheck = remaining.get(("list", 1, "recheck"))
    if not isinstance(recheck, _ListPage):
        errors.append("page 1: stability response missing")
    else:
        errors.extend(recheck.errors)
        if (
            (recheck.total, recheck.last) != (total, last)
            or _page_signature(recheck.rows) != signatures.get(1, ())
        ):
            errors.append("page-one stability recheck changed")

    identities = [_clean(row["raw_fields"]["identity"]) for row in all_rows]
    identity_duplicate_count = len(identities) - len(set(identities))
    if identity_duplicate_count:
        errors.append(f"{identity_duplicate_count} duplicate official identities")
    semantic_counter = Counter(
        (
            _normalized(row["title"]),
            _clean(row["start_date"]),
            _clean(row["end_date"]),
        )
        for row in all_rows
    )
    current_rows = [
        row
        for row in all_rows
        if date.fromisoformat(_clean(row["end_date"])) >= cutoff
    ]
    list_complete = bool(
        not errors
        and len(all_rows) == total
        and meta["list_requests"] == required_list_requests
        and meta["sentinel_requests"] == 1
        and meta["stability_rechecks"] == 1
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
                detail_errors.append(f"course {identity}: detail response missing")
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
                # A caller-supplied deduper is outside this module's trust
                # boundary and may mutate rows.  Re-apply the privacy contract
                # to the exact objects that would be returned.
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
    meta.update(
        {
            "ownership_scope": YEONGWOL_OWNERSHIP_SCOPE,
            "canonical_url": YEONGWOL_CANONICAL_URL,
            "page_counts": page_counts,
            "source_rows": len(all_rows),
            "current_source_count": len(current_rows),
            "expired_count": len(all_rows) - len(current_rows),
            "identity_duplicate_count": identity_duplicate_count,
            "semantic_duplicate_group_count": sum(
                count > 1 for count in semantic_counter.values()
            ),
            "semantic_duplicate_excess_rows": sum(
                max(0, count - 1) for count in semantic_counter.values()
            ),
            "semantic_duplicate_policy": "preserve_distinct_official_course_identity",
            "branch_counts": dict(Counter(_clean(row["branch"]) for row in result)),
            "status_counts": dict(Counter(_clean(row["status"]) for row in result)),
            "source_status_counts": dict(
                Counter(_clean(row["raw_fields"]["source_status"]) for row in all_rows)
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
                "the complete official catalogue has no current/future courses"
                if snapshot_complete and not current_rows
                else ""
            ),
            "municipality_coverage": [YEONGWOL_MUNICIPALITY_CODE],
            "candidate_audit": {
                key: dict(value) for key, value in YEONGWOL_CANDIDATE_AUDIT.items()
            },
            "discovery_audit": dict(YEONGWOL_DISCOVERY_AUDIT),
            "alias_providers": sorted(YEONGWOL_ALIAS_PROVIDERS),
            "pii_fields_discarded": list(YEONGWOL_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, YEONGWOL_PARSER, meta


collect = collect_yeongwol_education


__all__ = [
    "YEONGWOL_ALIAS_CANDIDATE_IDS",
    "YEONGWOL_ALIAS_PROVIDERS",
    "YEONGWOL_CANONICAL_CANDIDATE_ID",
    "YEONGWOL_CANONICAL_URL",
    "YEONGWOL_CANDIDATE_AUDIT",
    "YEONGWOL_CAREER_SUBSET_URL",
    "YEONGWOL_DISCOVERY_AUDIT",
    "YEONGWOL_DONATION_DETAIL_URL",
    "YEONGWOL_EQUIVALENT_UNFILTERED_URL",
    "YEONGWOL_EXCLUDED_CANDIDATE_IDS",
    "YEONGWOL_INSTITUTION_DIRECTORY_URL",
    "YEONGWOL_LIBRARY_HOMEPAGE_URL",
    "YEONGWOL_LIBRARY_LIST_URL",
    "YEONGWOL_MUNICIPALITY_CODE",
    "YEONGWOL_MUNICIPALITY_NAME",
    "YEONGWOL_NIGHT_SUBSET_URL",
    "YEONGWOL_PAGE_SIZE",
    "YEONGWOL_PARSER",
    "YEONGWOL_PII_FIELDS_DISCARDED",
    "YEONGWOL_PORTAL_URL",
    "YEONGWOL_PROVIDER",
    "YeongwolContractError",
    "collect",
    "collect_yeongwol_education",
    "is_yeongwol_education_target",
    "is_yeongwol_excluded_candidate",
    "is_yeongwol_owned_alias_target",
    "is_yeongwol_separate_library_target",
    "yeongwol_detail_url",
    "yeongwol_list_url",
]
