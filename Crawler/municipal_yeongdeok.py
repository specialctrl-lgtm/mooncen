"""Fail-closed collector for Yeongdeok Public Library education courses.

The retained provider already points at the official Gyeongbuk education
office library catalogue.  The public catalogue is an unpaginated current
ledger.  Completeness is therefore proved by reconciling its child/adult,
four programme-group, and seven application-state partitions, followed by an
exact full-ledger stability recheck and every current detail page.

Application controls are bound to the compound group/category/teach identity,
but the applicant form, attachments, instructor fields, contact data, and
free-form descriptions are never fetched or persisted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YEONGDEOK_PROVIDER = "MUNI_WWW_GBELIB_KR_04DB1B82"
YEONGDEOK_CANONICAL_CANDIDATE_ID = "MUNI_IR_EA5631037EDD"
YEONGDEOK_REJECTED_CANDIDATE_ID = "MUNI_IR_8725BF87D1CC"
YEONGDEOK_MUNICIPALITY_CODE = "4777000000"
YEONGDEOK_MUNICIPALITY_NAME = "경상북도 영덕군"
YEONGDEOK_BRANCH = "경상북도교육청 영덕도서관"
YEONGDEOK_BRANCH_CODE = "GBELIB_YEONGDEOK_LIBRARY"
YEONGDEOK_BRANCH_ADDRESS = "경북 영덕군 영덕읍 영덕로 201"

YEONGDEOK_HOST = "www.gbelib.kr"
YEONGDEOK_LIST_PATH = "/yd/module/teach/index.do"
YEONGDEOK_DETAIL_PATH = "/yd/module/teach/detail.do"
YEONGDEOK_APPLICATION_PATH = "/yd/module/teach/student/edit.do"
YEONGDEOK_APPLICATION_SAVE_PATH = "/yd/module/teach/student/save.do"
YEONGDEOK_MENU_IDX = "173"
YEONGDEOK_LARGE_CATEGORY_IDX = "18"
YEONGDEOK_HOMEPAGE_ID = "h18"
YEONGDEOK_CANONICAL_URL = (
    f"https://{YEONGDEOK_HOST}{YEONGDEOK_LIST_PATH}?"
    f"menu_idx={YEONGDEOK_MENU_IDX}&searchCate1={YEONGDEOK_LARGE_CATEGORY_IDX}"
)
YEONGDEOK_CANONICAL_URL_SHA256 = (
    "ea5631037edd76e6b2c39a548c263dc0ee6b50fa94fcc2bb496caa7056558eeb"
)
YEONGDEOK_RECOMMENDED_MAX_PAGES = 2
YEONGDEOK_RECOMMENDED_DETAIL_LIMIT = 50
YEONGDEOK_MAX_HTML_BYTES = 2_000_000
YEONGDEOK_FETCH_ATTEMPTS = 2
YEONGDEOK_PARSER = (
    "yeongdeok_gbelib_complete_current_ledger+child_adult_partition+"
    "four_group_partition+seven_status_partition+stable_full_recheck+"
    "all_current_details+compound_identity_application_controls_no_fetch+"
    "attachment_instructor_contact_and_free_text_exclusion"
)
YEONGDEOK_OWNERSHIP_SCOPE = (
    "gyeongbuk_education_office_yeongdeok_library_complete_current_education_ledger"
)


class YeongdeokContractError(ValueError):
    """Raised when the official source no longer satisfies its audited contract."""


@dataclass(frozen=True)
class _Filter:
    kind: str
    code: str
    label: str
    menu_idx: str = YEONGDEOK_MENU_IDX
    search_age: str = ""


YEONGDEOK_AGE_FILTERS: tuple[_Filter, ...] = (
    _Filter("age", "11", "어린이 프로그램", menu_idx="46", search_age="11"),
    _Filter("age", "13", "성인 프로그램", menu_idx="48", search_age="13"),
)
YEONGDEOK_GROUP_FILTERS: tuple[_Filter, ...] = (
    _Filter("group", "5", "상반기 평생교육강좌"),
    _Filter("group", "6", "여름방학특강 평생교육강좌"),
    _Filter("group", "7", "하반기 평생교육강좌"),
    _Filter("group", "8", "겨울방학특강 평생교육강좌"),
)
YEONGDEOK_STATUS_FILTERS: tuple[_Filter, ...] = (
    _Filter("status", "0", "수강신청"),
    _Filter("status", "1", "대기자 신청"),
    _Filter("status", "2", "신청완료"),
    _Filter("status", "3", "대기자 신청완료"),
    _Filter("status", "4", "접수마감"),
    _Filter("status", "5", "정원마감"),
    _Filter("status", "6", "신청대기"),
)

YEONGDEOK_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    YEONGDEOK_CANONICAL_CANDIDATE_ID: {
        "provider": YEONGDEOK_PROVIDER,
        "url": YEONGDEOK_CANONICAL_URL,
        "url_sha256": YEONGDEOK_CANONICAL_URL_SHA256,
        "decision": "keep_existing_provider_and_promote_complete_current_library_owner",
    },
    YEONGDEOK_REJECTED_CANDIDATE_ID: {
        "provider": "MUNI_WWW_YDCT_ORG_9BB7628B",
        "url": "https://www.ydct.org/board/notice/download/315/1093",
        "decision": "exclude_binary_notice_attachment_without_course_identity_ledger",
    },
}

YEONGDEOK_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "url": "https://www.ydct.org/",
        "decision": "separate_culture_tourism_foundation_program_owner",
    },
    {
        "url": "https://www.yd.go.kr/",
        "decision": "county_homepage_without_this_course_identity_ledger",
    },
    {
        "url": (
            "https://www.gbelib.kr/yd/module/teach/index.do?"
            "menu_idx=246&searchCate1=16"
        ),
        "decision": "separate_library_culture_event_owner",
    },
)

YEONGDEOK_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "cutoff": "2026-07-23",
    "source_rows": 5,
    "current_rows": 5,
    "detail_pages": 5,
    "list_requests": 15,
    "source_requests": 20,
    "status_counts": {"OPEN": 2, "WAITLIST": 2, "CLOSED": 1},
    "age_counts": {"11": 5, "13": 0},
    "group_counts": {"5": 0, "6": 5, "7": 0, "8": 0},
    "status_filter_counts": {
        "0": 2,
        "1": 2,
        "2": 0,
        "3": 0,
        "4": 0,
        "5": 1,
        "6": 0,
    },
    "application_controls": 4,
    "teach_ids": ["15719", "15720", "15721", "15722", "15723"],
    "requests_per_snapshot": 20,
    "two_snapshot_requests": 40,
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_ISO_DATE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CAPACITY_TOTALS = re.compile(
    r"^온라인\s*(\d[\d,]*)명\s*,?\s*\(\s*후보자\s*(\d[\d,]*)명\s*\)$"
)
_CAPACITY_CURRENT = re.compile(
    r"^온라인\s*:\s*(\d[\d,]*)\s*/\s*(\d[\d,]*)\s*"
    r"\(\s*후보자\s*:\s*(\d[\d,]*)\s*/\s*(\d[\d,]*)\s*\)$"
)
_DETAIL_CAPACITY = re.compile(r"^(\d[\d,]*)\s*명\s*/\s*(\d[\d,]*)\s*명$")

_SAFE_LIST_LABELS = frozenset(
    {
        "접수기간",
        "장소",
        "강좌일",
        "모집인원",
        "접수현황",
        "모집대상",
        "준비물 및 재료비",
        "학년제한",
    }
)
_DISCARDED_LIST_LABELS = frozenset({"강사명", "강의계획서"})
_SAFE_DETAIL_LABELS = frozenset(
    {
        "강의 분류",
        "강의장소",
        "준비물 및 재료비",
        "강의대상",
        "학년제한",
        "접수기간",
        "강의기간(*)",
        "강의시간",
        "강의요일",
        "현재 참여 / 모집",
        "현재 대기자 / 대기자",
    }
)
_DISCARDED_DETAIL_LABELS = frozenset({"강의 설명", "강사명", "강의계획서"})
_STATUS_MAP = {
    "수강신청": "OPEN",
    "대기자신청": "WAITLIST",
    "신청완료": "CLOSED",
    "대기자신청완료": "CLOSED",
    "정원마감": "CLOSED",
    "접수마감": "CLOSED",
    "신청대기": "SCHEDULED",
}
_ACTIVE_CONTROL = {"수강신청": "1", "대기자신청": "2"}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "group_idx",
        "category_idx",
        "large_category_idx",
        "source_category",
        "source_status",
        "detail_status",
        "source_apply_period",
        "source_event_period",
        "source_schedule",
        "source_target",
        "source_venue",
        "source_grade_limit",
        "detail_verified",
        "application_control_present",
        "application_control_verified",
        "application_form_endpoint_fetched",
        "application_save_endpoint_fetched",
        "application_endpoint_fetched",
        "attachment_endpoint_fetched",
        "pii_endpoint_fetched",
        "discarded_fields",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "contacts",
        "instructor",
        "instructor_name",
        "attachments",
        "attachment_urls",
        "course_content",
        "detail_description",
        "source_html",
        "raw_html",
        "applicant_name",
        "applicant_phone",
        "password",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlparse(url).query, keep_blank_values=True, strict_parsing=True)


def is_yeongdeok_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != YEONGDEOK_PROVIDER:
        return False
    url = _clean(_target_value(target, "url"))
    if url != YEONGDEOK_CANONICAL_URL:
        return False
    try:
        parsed = urlparse(url)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == YEONGDEOK_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == YEONGDEOK_LIST_PATH
        and query
        == [
            ("menu_idx", YEONGDEOK_MENU_IDX),
            ("searchCate1", YEONGDEOK_LARGE_CATEGORY_IDX),
        ]
        and not parsed.fragment
    )


is_target = is_yeongdeok_education_target


def _raw_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _list_url(filter_value: Optional[_Filter] = None) -> str:
    values: list[tuple[str, str]] = [
        ("menu_idx", filter_value.menu_idx if filter_value else YEONGDEOK_MENU_IDX),
        ("searchCate1", YEONGDEOK_LARGE_CATEGORY_IDX),
    ]
    if filter_value:
        if filter_value.kind == "age":
            values.append(("searchAge", filter_value.search_age))
        elif filter_value.kind == "group":
            values.append(("group_idx", filter_value.code))
        elif filter_value.kind == "status":
            values.append(("teach_status", filter_value.code))
        else:
            raise ValueError("unsupported Yeongdeok filter")
    return f"https://{YEONGDEOK_HOST}{YEONGDEOK_LIST_PATH}?{urlencode(values)}"


def _detail_url(group_idx: str, category_idx: str, teach_idx: str) -> str:
    if not (_IDENTITY.fullmatch(group_idx) and category_idx.isdigit() and _IDENTITY.fullmatch(teach_idx)):
        raise ValueError("invalid Yeongdeok compound course identity")
    return f"https://{YEONGDEOK_HOST}{YEONGDEOK_DETAIL_PATH}?" + urlencode(
        (
            ("group_idx", group_idx),
            ("category_idx", category_idx),
            ("teach_idx", teach_idx),
            ("large_category_idx", YEONGDEOK_LARGE_CATEGORY_IDX),
            ("menu_idx", YEONGDEOK_MENU_IDX),
            ("searchCate1", YEONGDEOK_LARGE_CATEGORY_IDX),
        )
    )


def _application_url(
    group_idx: str,
    category_idx: str,
    teach_idx: str,
    apply_status: str,
) -> str:
    _detail_url(group_idx, category_idx, teach_idx)
    if apply_status not in {"1", "2"}:
        raise ValueError("invalid Yeongdeok application state")
    return f"https://{YEONGDEOK_HOST}{YEONGDEOK_APPLICATION_PATH}?" + urlencode(
        (
            ("editMode", "ADD"),
            ("homepage_id", YEONGDEOK_HOMEPAGE_ID),
            ("group_idx", group_idx),
            ("category_idx", category_idx),
            ("teach_idx", teach_idx),
            ("large_category_idx", YEONGDEOK_LARGE_CATEGORY_IDX),
            ("black_yn", ""),
            ("apply_status", apply_status),
            ("menu_idx", YEONGDEOK_MENU_IDX),
            ("searchCate1", YEONGDEOK_LARGE_CATEGORY_IDX),
        )
    )


def _allowed_request_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
        pairs = _query(url)
    except ValueError:
        return False
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == YEONGDEOK_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    ):
        return False
    if parsed.path == YEONGDEOK_LIST_PATH:
        allowed = {_list_url()}
        allowed.update(_list_url(item) for item in YEONGDEOK_AGE_FILTERS)
        allowed.update(_list_url(item) for item in YEONGDEOK_GROUP_FILTERS)
        allowed.update(_list_url(item) for item in YEONGDEOK_STATUS_FILTERS)
        return url in allowed
    if parsed.path != YEONGDEOK_DETAIL_PATH or len(pairs) != 6:
        return False
    values = dict(pairs)
    return bool(
        [name for name, _ in pairs]
        == [
            "group_idx",
            "category_idx",
            "teach_idx",
            "large_category_idx",
            "menu_idx",
            "searchCate1",
        ]
        and _IDENTITY.fullmatch(values.get("group_idx", ""))
        and values.get("category_idx", "").isdigit()
        and _IDENTITY.fullmatch(values.get("teach_idx", ""))
        and values.get("large_category_idx") == YEONGDEOK_LARGE_CATEGORY_IDX
        and values.get("menu_idx") == YEONGDEOK_MENU_IDX
        and values.get("searchCate1") == YEONGDEOK_LARGE_CATEGORY_IDX
    )


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
) -> tuple[BeautifulSoup, int]:
    if not _allowed_request_url(url):
        raise YeongdeokContractError("request left the audited list/detail allowlist")
    last_error: Optional[Exception] = None
    for attempt in range(1, YEONGDEOK_FETCH_ATTEMPTS + 1):
        try:
            response = fetcher(session, url, timeout)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            status = int(getattr(response, "status_code", 200))
            if status != 200:
                raise YeongdeokContractError(f"unexpected HTTP status {status}")
            if getattr(response, "history", None):
                raise YeongdeokContractError("redirect is not allowed")
            final_url = _clean(getattr(response, "url", url)) or url
            if final_url != url or not _allowed_request_url(final_url):
                raise YeongdeokContractError("response URL changed")
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", response)).encode("utf-8")
            if not isinstance(content, (bytes, bytearray)):
                content = bytes(content)
            if not content or len(content) > YEONGDEOK_MAX_HTML_BYTES:
                raise YeongdeokContractError("empty or oversized official HTML")
            try:
                html = bytes(content).decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise YeongdeokContractError("official page is no longer strict UTF-8") from exc
            soup = BeautifulSoup(html, "html.parser")
            title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
            footer = _clean(soup.select_one("#footer").get_text(" ", strip=True) if soup.select_one("#footer") else "")
            expected_section = (
                "평생강좌신청"
                if dict(_query(url)).get("menu_idx") in {"46", "48"}
                else "평생학습강좌신청"
            )
            if (
                not title.startswith(YEONGDEOK_BRANCH)
                or expected_section not in title
                or YEONGDEOK_BRANCH not in footer
                or YEONGDEOK_BRANCH_ADDRESS not in footer
            ):
                raise YeongdeokContractError("official owner name/address evidence missing")
            return soup, attempt
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return (
            value.astimezone(ZoneInfo("Asia/Seoul")).date()
            if value.tzinfo
            else value.date()
        )
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("today must be an ISO date")


def _dates(value: str, *, label: str) -> tuple[date, date]:
    matches = list(_ISO_DATE.finditer(_clean(value)))
    if len(matches) != 2:
        raise YeongdeokContractError(f"{label}: exact two-date range missing")
    try:
        parsed = tuple(date(int(m.group(1)), int(m.group(2)), int(m.group(3))) for m in matches)
    except ValueError as exc:
        raise YeongdeokContractError(f"{label}: invalid date") from exc
    if parsed[1] < parsed[0]:
        raise YeongdeokContractError(f"{label}: reversed date range")
    return parsed[0], parsed[1]


def _times(value: str, *, label: str) -> tuple[str, str]:
    matches = list(_TIME.finditer(_clean(value)))
    if len(matches) != 2:
        raise YeongdeokContractError(f"{label}: exact two-time range missing")
    return tuple(f"{int(m.group(1)):02d}:{m.group(2)}" for m in matches)  # type: ignore[return-value]


def _safe_item_fields(item: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    labels: list[str] = []
    for li in item.select("ul.con2 > li"):
        label_node = li.select_one("label")
        if label_node is None:
            raise YeongdeokContractError("course list field label missing")
        label = _clean(label_node.get_text(" ", strip=True))
        if label in labels or label not in _SAFE_LIST_LABELS | _DISCARDED_LIST_LABELS:
            raise YeongdeokContractError(f"course list fieldset changed: {label}")
        labels.append(label)
        if label in _SAFE_LIST_LABELS:
            container = label_node.parent
            value = _clean(container.get_text(" ", strip=True))
            label_text = _clean(label_node.get_text(" ", strip=True))
            value = _clean(value[len(label_text) :]).lstrip(":").strip()
            fields[label] = value
    if set(labels) != _SAFE_LIST_LABELS | _DISCARDED_LIST_LABELS:
        raise YeongdeokContractError("course list required fields changed")
    if any(not fields.get(label) for label in _SAFE_LIST_LABELS):
        raise YeongdeokContractError("course list safe field is empty")
    return fields


def _selected_value(soup: BeautifulSoup, selector: str) -> str:
    nodes = soup.select(selector)
    if len(nodes) != 1:
        raise YeongdeokContractError(f"filter control missing: {selector}")
    selected = nodes[0].select("option[selected]")
    if len(selected) != 1:
        raise YeongdeokContractError(f"filter selection changed: {selector}")
    return _clean(selected[0].get("value"))


def _form_contract(soup: BeautifulSoup, filter_value: Optional[_Filter]) -> None:
    menu_idx = filter_value.menu_idx if filter_value else YEONGDEOK_MENU_IDX
    forms = soup.select("form#teach")
    if (
        len(forms) != 1
        or _clean(forms[0].get("method")).upper() != "POST"
        or _clean(forms[0].get("action")) != YEONGDEOK_APPLICATION_SAVE_PATH
    ):
        raise YeongdeokContractError("applicant form boundary changed")
    expected = {
        "group_idx": (
            filter_value.code
            if filter_value is not None and filter_value.kind == "group"
            else "0"
        ),
        "teach_idx": "0",
        "menu_idx": menu_idx,
        "category_idx": "0",
        "large_category_idx": "0",
        "searchCate1": YEONGDEOK_LARGE_CATEGORY_IDX,
    }
    actual = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in forms[0].select("input[type='hidden'][name]")
    }
    if any(actual.get(key) != value for key, value in expected.items()):
        raise YeongdeokContractError("applicant form identity binding changed")

    search_forms = soup.select("form#search_teach")
    if (
        len(search_forms) != 1
        or _clean(search_forms[0].get("method")).upper() != "GET"
        or _clean(search_forms[0].get("action")) != "index.do"
    ):
        raise YeongdeokContractError("search form boundary changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in search_forms[0].select("input[type='hidden'][name]")
    }
    if hidden.get("menu_idx") != menu_idx or hidden.get("searchCate1") != YEONGDEOK_LARGE_CATEGORY_IDX:
        raise YeongdeokContractError("search form owner binding changed")
    expected_group = (
        filter_value.code
        if filter_value is not None and filter_value.kind == "group"
        else ""
    )
    expected_status = (
        filter_value.code
        if filter_value is not None and filter_value.kind == "status"
        else ""
    )
    if (
        _selected_value(soup, "form#search_teach select#group_idx") != expected_group
        or _selected_value(soup, "form#search_teach select#teach_status") != expected_status
    ):
        raise YeongdeokContractError("requested partition selection changed")


def _option_registry(soup: BeautifulSoup, selector: str) -> tuple[tuple[str, str], ...]:
    nodes = soup.select(selector)
    if len(nodes) != 1:
        raise YeongdeokContractError(f"filter registry missing: {selector}")
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in nodes[0].select("option")
    )


def _registries_contract(soup: BeautifulSoup) -> None:
    if _option_registry(soup, "form#search_teach select#group_idx") != (
        ("", "전체 보기"),
        ("8", "겨울방학특강 평생교육강좌"),
        ("7", "하반기 평생교육강좌"),
        ("6", "여름방학특강 평생교육강좌"),
        ("5", "상반기 평생교육강좌"),
    ):
        raise YeongdeokContractError("programme group registry changed")
    if _option_registry(soup, "form#search_teach select#teach_status") != (
        ("", "전체 보기"),
        ("0", "수강신청"),
        ("1", "대기자 신청"),
        ("2", "신청완료"),
        ("3", "대기자 신청완료"),
        ("5", "정원마감"),
        ("4", "접수마감"),
        ("6", "신청대기"),
    ):
        raise YeongdeokContractError("application status registry changed")
    expected_age_urls = {_list_url(), *(_list_url(item) for item in YEONGDEOK_AGE_FILTERS)}
    age_urls = {
        "https://www.gbelib.kr" + _clean(node.get("href"))
        for node in soup.select("a[href*='/yd/module/teach/index.do'][href*='searchCate1=18']")
        if _clean(node.get_text(" ", strip=True)) in {"전체", "어린이 프로그램", "성인 프로그램"}
    }
    if age_urls != expected_age_urls:
        raise YeongdeokContractError("age partition navigation changed")
    if soup.select(".paging, .pagination, .paginate, a[href*='viewPage=']"):
        raise YeongdeokContractError("current ledger unexpectedly became paginated")


def _control(item: Any, identity: Mapping[str, str]) -> tuple[str, str, bool]:
    controls = item.select("div.stat > a")
    if len(controls) != 1:
        raise YeongdeokContractError(f"course {identity['teach_idx']}: status control changed")
    node = controls[0]
    text = _clean(node.get_text(" ", strip=True)).replace(" ", "")
    if text not in _STATUS_MAP:
        raise YeongdeokContractError(f"course {identity['teach_idx']}: unknown status {text}")
    status = _STATUS_MAP[text]
    if text in _ACTIVE_CONTROL:
        expected = {
            "keyvalue1": YEONGDEOK_HOMEPAGE_ID,
            "keyvalue2": identity["group_idx"],
            "keyvalue3": identity["category_idx"],
            "keyvalue4": identity["teach_idx"],
            "keyvalue5": YEONGDEOK_LARGE_CATEGORY_IDX,
            "keyvalue6": "",
            "apply_status": _ACTIVE_CONTROL[text],
        }
        if (
            frozenset(node.get("class") or ()) != frozenset({"btn", "btn1", "add"})
            or _clean(node.get("href"))
            or any(_clean(node.get(key)) != value for key, value in expected.items())
        ):
            raise YeongdeokContractError(f"course {identity['teach_idx']}: application identity drift")
        return text, status, True
    if (
        _clean(node.get("href")) != "javascript:void(0);"
        or frozenset(node.get("class") or ()) != frozenset({"btn"})
    ):
        raise YeongdeokContractError(f"course {identity['teach_idx']}: inactive control changed")
    return text, status, False


def _parse_list(
    soup: BeautifulSoup,
    filter_value: Optional[_Filter],
    *,
    validate_registries: bool,
) -> list[dict[str, Any]]:
    _form_contract(soup, filter_value)
    if validate_registries:
        _registries_contract(soup)
    wrappers = soup.select("#list_mode")
    if len(wrappers) != 1:
        raise YeongdeokContractError("current ledger wrapper changed")
    items = wrappers[0].select(":scope > .item")
    empty_nodes = wrappers[0].select(":scope > .nodata")
    if not items:
        # The live GBELIB implementation renders an exactly empty #list_mode
        # for a zero-result partition.  Do not infer emptiness from unrelated
        # page text or silently accept a new child structure.
        if (
            empty_nodes
            or _clean(wrappers[0].get_text(" ", strip=True))
            or wrappers[0].find(True, recursive=False) is not None
        ):
            raise YeongdeokContractError("empty filter sentinel changed")
        return []
    if empty_nodes:
        raise YeongdeokContractError("rows and empty sentinel appeared together")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sequence, item in enumerate(items, 1):
        details = item.select(".op_title a.detail-btn")
        if len(details) != 2:
            raise YeongdeokContractError("course detail controls changed")
        identity_values = {
            (
                _clean(node.get("keyvalue1")),
                _clean(node.get("keyvalue2")),
                _clean(node.get("keyvalue3")),
                _clean(node.get("keyvalue4")),
            )
            for node in details
        }
        if len(identity_values) != 1:
            raise YeongdeokContractError("course detail controls disagree")
        group_idx, category_idx, teach_idx, large_category_idx = identity_values.pop()
        if (
            not _IDENTITY.fullmatch(group_idx)
            or not category_idx.isdigit()
            or not _IDENTITY.fullmatch(teach_idx)
            or large_category_idx != YEONGDEOK_LARGE_CATEGORY_IDX
            or teach_idx in seen
        ):
            raise YeongdeokContractError("invalid or duplicate compound course identity")
        seen.add(teach_idx)
        title_nodes = [node for node in details if "btn" not in (node.get("class") or [])]
        if len(title_nodes) != 1 or _clean(title_nodes[0].get("href")):
            raise YeongdeokContractError(f"course {teach_idx}: title control changed")
        title = _clean(title_nodes[0].get_text(" ", strip=True))
        category_nodes = item.select(".op_title > span.ca")
        if len(category_nodes) != 1:
            raise YeongdeokContractError(f"course {teach_idx}: category changed")
        category = _clean(category_nodes[0].get_text(" ", strip=True))
        if not title or not category or _PHONE.search(title) or _EMAIL.search(title):
            raise YeongdeokContractError(f"course {teach_idx}: unsafe title/category")
        fields = _safe_item_fields(item)
        apply_start, apply_end = _dates(fields["접수기간"], label=f"course {teach_idx} application")
        start, end = _dates(fields["강좌일"], label=f"course {teach_idx} event")
        event_times = _times(fields["강좌일"], label=f"course {teach_idx} event")
        totals = _CAPACITY_TOTALS.fullmatch(fields["모집인원"])
        currents = _CAPACITY_CURRENT.fullmatch(fields["접수현황"])
        if totals is None or currents is None:
            raise YeongdeokContractError(f"course {teach_idx}: capacity shape changed")
        capacity_total, waitlist_total = (int(value.replace(",", "")) for value in totals.groups())
        capacity_current, current_total, waitlist_current, current_wait_total = (
            int(value.replace(",", "")) for value in currents.groups()
        )
        if (capacity_total, waitlist_total) != (current_total, current_wait_total):
            raise YeongdeokContractError(f"course {teach_idx}: capacity totals disagree")
        identity = {
            "group_idx": group_idx,
            "category_idx": category_idx,
            "teach_idx": teach_idx,
        }
        source_status, status, active = _control(item, identity)
        rows.append(
            {
                **identity,
                "large_category_idx": large_category_idx,
                "sequence": sequence,
                "title": title,
                "category": category,
                "source_status": source_status,
                "status": status,
                "application_control_present": active,
                "apply_status": _ACTIVE_CONTROL.get(source_status, ""),
                "apply_start": apply_start,
                "apply_end": apply_end,
                "apply_period": fields["접수기간"],
                "start": start,
                "end": end,
                "event_period": fields["강좌일"],
                "event_times": event_times,
                "venue": fields["장소"],
                "target": fields["모집대상"],
                "material_fee": fields["준비물 및 재료비"],
                "grade_limit": fields["학년제한"],
                "capacity_total": capacity_total,
                "capacity_current": capacity_current,
                "waitlist_capacity": waitlist_total,
                "waitlist_current": waitlist_current,
            }
        )
    return rows


def _row_signature(row: Mapping[str, Any], *, include_sequence: bool) -> tuple[Any, ...]:
    return (
        row["teach_idx"],
        row["group_idx"],
        row["category_idx"],
        row["large_category_idx"],
        row["sequence"] if include_sequence else None,
        row["title"],
        row["category"],
        row["source_status"],
        row["status"],
        row["application_control_present"],
        row["apply_status"],
        row["apply_start"].isoformat(),
        row["apply_end"].isoformat(),
        row["apply_period"],
        row["start"].isoformat(),
        row["end"].isoformat(),
        row["event_period"],
        tuple(row["event_times"]),
        row["venue"],
        row["target"],
        row["material_fee"],
        row["grade_limit"],
        row["capacity_current"],
        row["capacity_total"],
        row["waitlist_current"],
        row["waitlist_capacity"],
    )


def _signature(rows: Iterable[Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(_row_signature(row, include_sequence=True) for row in rows)


def _detail_fields(soup: BeautifulSoup, teach_idx: str) -> dict[str, str]:
    tables = soup.select("table#teach_table.tstyle.nohead")
    if len(tables) != 1:
        raise YeongdeokContractError(f"course {teach_idx}: detail table changed")
    values: dict[str, set[str]] = {}
    labels: set[str] = set()
    allowed = _SAFE_DETAIL_LABELS | _DISCARDED_DETAIL_LABELS
    for tr in tables[0].select("tr"):
        children = tr.find_all(["th", "td"], recursive=False)
        if len(children) % 2:
            raise YeongdeokContractError(f"course {teach_idx}: detail pairing changed")
        for index in range(0, len(children), 2):
            if children[index].name != "th" or children[index + 1].name != "td":
                raise YeongdeokContractError(f"course {teach_idx}: detail pairing changed")
            label = _clean(children[index].get_text(" ", strip=True))
            if label not in allowed:
                raise YeongdeokContractError(f"course {teach_idx}: detail fieldset changed: {label}")
            labels.add(label)
            if label in _SAFE_DETAIL_LABELS:
                value = _clean(children[index + 1].get_text(" ", strip=True))
                if value:
                    values.setdefault(label, set()).add(value)
    if not allowed <= labels:
        raise YeongdeokContractError(f"course {teach_idx}: required detail fields missing")
    result: dict[str, str] = {}
    for label in _SAFE_DETAIL_LABELS:
        options = values.get(label, set())
        if len(options) != 1:
            raise YeongdeokContractError(f"course {teach_idx}: empty/conflicting detail field {label}")
        result[label] = next(iter(options))
    return result


def _parse_detail(listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date) -> dict[str, Any]:
    teach_idx = str(listed["teach_idx"])
    headings = soup.select(".teach_top > h3")
    if len(headings) != 1 or _clean(headings[0].get_text(" ", strip=True)) != listed["title"]:
        raise YeongdeokContractError(f"course {teach_idx}: list/detail title drift")
    fields = _detail_fields(soup, teach_idx)
    if (
        fields["강의 분류"] != listed["category"]
        or fields["강의장소"] != listed["venue"]
        or fields["강의대상"] != listed["target"]
        or fields["준비물 및 재료비"] != listed["material_fee"]
        or fields["학년제한"] != listed["grade_limit"]
    ):
        raise YeongdeokContractError(f"course {teach_idx}: list/detail safe fields drift")
    detail_start, detail_end = _dates(fields["강의기간(*)"], label=f"course {teach_idx} detail event")
    detail_apply_start, detail_apply_end = _dates(fields["접수기간"], label=f"course {teach_idx} detail application")
    if (
        (detail_start, detail_end) != (listed["start"], listed["end"])
        or (detail_apply_start, detail_apply_end) != (listed["apply_start"], listed["apply_end"])
        or _times(fields["강의시간"], label=f"course {teach_idx} detail schedule") != listed["event_times"]
    ):
        raise YeongdeokContractError(f"course {teach_idx}: list/detail dates drift")
    current = _DETAIL_CAPACITY.fullmatch(fields["현재 참여 / 모집"])
    waiting = _DETAIL_CAPACITY.fullmatch(fields["현재 대기자 / 대기자"])
    if current is None or waiting is None:
        raise YeongdeokContractError(f"course {teach_idx}: detail capacity shape changed")
    if (
        tuple(int(value.replace(",", "")) for value in current.groups())
        != (listed["capacity_current"], listed["capacity_total"])
        or tuple(int(value.replace(",", "")) for value in waiting.groups())
        != (listed["waitlist_current"], listed["waitlist_capacity"])
    ):
        raise YeongdeokContractError(f"course {teach_idx}: list/detail capacity drift")
    back_buttons = soup.select("div.sbtn > a#back-btn.btn")
    if (
        len(back_buttons) != 1
        or frozenset(back_buttons[0].get("class") or ()) != frozenset({"btn"})
        or _clean(back_buttons[0].get("href"))
        or _clean(back_buttons[0].get_text(" ", strip=True)) != "목록으로"
    ):
        raise YeongdeokContractError(f"course {teach_idx}: detail back control changed")
    buttons = [node for node in soup.select("div.sbtn > a.btn") if _clean(node.get("id")) != "back-btn"]
    if len(buttons) != 1:
        raise YeongdeokContractError(f"course {teach_idx}: detail status control changed")
    detail_status = _clean(buttons[0].get_text(" ", strip=True)).replace(" ", "")
    if detail_status != listed["source_status"]:
        raise YeongdeokContractError(f"course {teach_idx}: list/detail status drift")
    active = bool(listed["application_control_present"])
    if active:
        if (
            frozenset(buttons[0].get("class") or ())
            != frozenset({"btn", "btn1", "apply-btn"})
            or _clean(buttons[0].get("href"))
            or _clean(buttons[0].get("apply_status")) != listed["apply_status"]
        ):
            raise YeongdeokContractError(f"course {teach_idx}: detail application control drift")
    elif (
        _clean(buttons[0].get("href")) != "javascript:void(0);"
        or frozenset(buttons[0].get("class") or ()) != frozenset({"btn"})
    ):
        raise YeongdeokContractError(f"course {teach_idx}: detail inactive control drift")
    status = str(listed["status"])
    if status in {"OPEN", "WAITLIST"} and not (
        listed["apply_start"] <= cutoff <= listed["apply_end"]
    ):
        raise YeongdeokContractError(f"course {teach_idx}: active status/date disagreement")
    if status == "SCHEDULED" and cutoff >= listed["apply_start"]:
        raise YeongdeokContractError(f"course {teach_idx}: scheduled status/date disagreement")
    raw_url = _detail_url(str(listed["group_idx"]), str(listed["category_idx"]), teach_idx)
    application_url = (
        _application_url(
            str(listed["group_idx"]),
            str(listed["category_idx"]),
            teach_idx,
            str(listed["apply_status"]),
        )
        if active
        else ""
    )
    days = _clean(fields["강의요일"])
    schedule = f"{days} {fields['강의시간']}" if days else fields["강의시간"]
    return {
        "provider": YEONGDEOK_PROVIDER,
        "provider_course_id": f"{YEONGDEOK_PROVIDER}:teach:{teach_idx}",
        "prefer_incoming_provider_course_id": True,
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": YEONGDEOK_BRANCH,
        "branch_code": YEONGDEOK_BRANCH_CODE,
        "branch_url": YEONGDEOK_CANONICAL_URL,
        "preserve_branch": True,
        "category": str(listed["category"]),
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": application_url,
        "application_type": (
            "WAITLIST_APPLY"
            if status == "WAITLIST"
            else "ONLINE_RESERVATION"
            if status == "OPEN"
            else "INFO_ONLY"
        ),
        "application_method": "온라인",
        "application_methods": ["온라인"],
        "reservation_available": active,
        "status": status,
        "raw_status": detail_status,
        "fee": "",
        "fee_amount": None,
        "material_fee": str(listed["material_fee"]),
        "material_fee_amount": None,
        "period": fields["강의기간(*)"],
        "start_date": listed["start"].isoformat(),
        "end_date": listed["end"].isoformat(),
        "apply_period": fields["접수기간"],
        "apply_start": listed["apply_start"].isoformat(),
        "apply_end": listed["apply_end"].isoformat(),
        "apply_start_date": listed["apply_start"].isoformat(),
        "apply_end_date": listed["apply_end"].isoformat(),
        "schedule_raw": schedule,
        "capacity": f"{listed['capacity_total']}명",
        "capacity_current": int(listed["capacity_current"]),
        "capacity_total": int(listed["capacity_total"]),
        "waitlist_current": int(listed["waitlist_current"]),
        "waitlist_capacity": int(listed["waitlist_capacity"]),
        "target": str(listed["target"]),
        "venue": str(listed["venue"]),
        "room": str(listed["venue"]),
        "venue_name": YEONGDEOK_BRANCH,
        "venue_address": YEONGDEOK_BRANCH_ADDRESS,
        "address": YEONGDEOK_BRANCH_ADDRESS,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "교육청/도서관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": YEONGDEOK_PARSER,
        "municipality_code": YEONGDEOK_MUNICIPALITY_CODE,
        "municipality_full_name": YEONGDEOK_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": teach_idx,
            "group_idx": str(listed["group_idx"]),
            "category_idx": str(listed["category_idx"]),
            "large_category_idx": YEONGDEOK_LARGE_CATEGORY_IDX,
            "source_category": str(listed["category"]),
            "source_status": str(listed["source_status"]),
            "detail_status": detail_status,
            "source_apply_period": fields["접수기간"],
            "source_event_period": fields["강의기간(*)"],
            "source_schedule": schedule,
            "source_target": str(listed["target"]),
            "source_venue": str(listed["venue"]),
            "source_grade_limit": str(listed["grade_limit"]),
            "detail_verified": True,
            "application_control_present": active,
            "application_control_verified": True,
            "application_form_endpoint_fetched": False,
            "application_save_endpoint_fetched": False,
            "application_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "pii_endpoint_fetched": False,
            "discarded_fields": sorted(_DISCARDED_LIST_LABELS | _DISCARDED_DETAIL_LABELS),
            "service_family": "education",
        },
    }


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    forbidden = _FORBIDDEN_ROW_KEYS.intersection(row)
    if forbidden:
        errors.append(f"forbidden fields: {sorted(forbidden)}")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded privacy allowlist")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail content persisted")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "venue_address", "address"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("PII-like contact data persisted")
    return errors


def _partition_ids(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    return {str(row["teach_idx"]) for row in rows}


def _reconcile_partition(
    canonical: Iterable[Mapping[str, Any]],
    partitions: Mapping[str, list[dict[str, Any]]],
    *,
    label: str,
) -> None:
    canonical_rows = list(canonical)
    canonical_by_id = {str(row["teach_idx"]): row for row in canonical_rows}
    canonical_ids = set(canonical_by_id)
    seen: set[str] = set()
    for code, rows in partitions.items():
        ids = _partition_ids(rows)
        if not ids <= canonical_ids or seen.intersection(ids):
            raise YeongdeokContractError(f"{label} partition {code} overlaps or escapes canonical ledger")
        for row in rows:
            identity = str(row["teach_idx"])
            if _row_signature(row, include_sequence=False) != _row_signature(
                canonical_by_id[identity], include_sequence=False
            ):
                raise YeongdeokContractError(
                    f"{label} partition {code} changed course {identity}"
                )
            if label == "group" and str(row["group_idx"]) != code:
                raise YeongdeokContractError(
                    f"group partition {code} contains another programme group"
                )
            if label == "status":
                expected_status = next(
                    item.label.replace(" ", "")
                    for item in YEONGDEOK_STATUS_FILTERS
                    if item.code == code
                )
                if str(row["source_status"]) != expected_status:
                    raise YeongdeokContractError(
                        f"status partition {code} contains another application state"
                    )
        seen.update(ids)
    if seen != canonical_ids:
        raise YeongdeokContractError(f"{label} partitions do not cover canonical ledger")


def _initial_meta() -> dict[str, Any]:
    return {
        "municipality_code": YEONGDEOK_MUNICIPALITY_CODE,
        "municipality_full_name": YEONGDEOK_MUNICIPALITY_NAME,
        "owner_provider": YEONGDEOK_PROVIDER,
        "canonical_provider": YEONGDEOK_PROVIDER,
        "canonical_candidate_id": YEONGDEOK_CANONICAL_CANDIDATE_ID,
        "canonical_url": YEONGDEOK_CANONICAL_URL,
        "canonical_url_sha256": YEONGDEOK_CANONICAL_URL_SHA256,
        "provider_decision": (
            "keep existing provider and replace its generic three-row parser with "
            "the exact complete-current GBELIB owner contract"
        ),
        "existing_active_owner_count": 1,
        "incumbent_generated_parser": "gbelib_library_teach",
        "ownership_scope": YEONGDEOK_OWNERSHIP_SCOPE,
        "candidate_audit": {key: dict(value) for key, value in YEONGDEOK_CANDIDATE_AUDIT.items()},
        "owner_boundaries": [dict(value) for value in YEONGDEOK_OWNER_BOUNDARIES],
        "parser": YEONGDEOK_PARSER,
        "boundary_mode": "unpaginated current ledger plus three exact partition censuses and stable recheck",
        "recommended_max_pages": YEONGDEOK_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": YEONGDEOK_RECOMMENDED_DETAIL_LIMIT,
        "recommended_timeout_seconds": 30,
        "fetch_attempts": YEONGDEOK_FETCH_ATTEMPTS,
        "max_html_bytes": YEONGDEOK_MAX_HTML_BYTES,
        "live_audit_baseline": dict(YEONGDEOK_LIVE_AUDIT_BASELINE),
        "pii_policy": (
            "persist only public course fields; never fetch applicant edit/save, "
            "attachment download, instructor, contact, or free-text payloads"
        ),
        "address_policy": "verified official library footer address",
        "source_requests": 0,
        "request_attempts": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "boundary_rechecks": 0,
        "application_endpoints_called": 0,
        "application_save_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "pii_endpoints_called": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "returned_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "age_partition_complete": False,
        "group_partition_complete": False,
        "status_partition_complete": False,
        "partition_overlap_count": 0,
        "full_ledger_rechecked_after_details": False,
        "details_complete": False,
        "privacy_violations": 0,
        "semantic_duplicate_count": 0,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "configured_collection_error": "",
    }


def collect_yeongdeok_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = YEONGDEOK_RECOMMENDED_MAX_PAGES,
    detail_limit: int = YEONGDEOK_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, stable, privacy-safe current Yeongdeok snapshot."""

    meta = _initial_meta()
    if not is_yeongdeok_education_target(target):
        meta["configured_collection_error"] = "target does not match exact retained Yeongdeok owner"
        return [], YEONGDEOK_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], YEONGDEOK_PARSER, meta
        session_factory = _raw_session
    try:
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
        ):
            raise ValueError("timeout/max_pages/detail_limit are invalid")
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], YEONGDEOK_PARSER, meta
    if max_pages < 2:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "YeongdeokContractError: max_pages must allow initial and stable recheck"
        )
        return [], YEONGDEOK_PARSER, meta
    current_fetcher = fetcher or _default_fetcher
    session = session_factory()

    def fetch_list(filter_value: Optional[_Filter], *, registries: bool = False) -> list[dict[str, Any]]:
        url = _list_url(filter_value)
        soup, attempts = _fetch_soup(session, url, timeout, current_fetcher)
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        meta["request_attempts"] += attempts
        return _parse_list(
            soup,
            filter_value,
            validate_registries=registries,
        )

    try:
        canonical = fetch_list(None, registries=True)
        canonical_ids = _partition_ids(canonical)
        if len(canonical_ids) != len(canonical):
            raise YeongdeokContractError("canonical ledger duplicated teach identities")

        age_partitions = {item.code: fetch_list(item) for item in YEONGDEOK_AGE_FILTERS}
        group_partitions = {item.code: fetch_list(item) for item in YEONGDEOK_GROUP_FILTERS}
        status_partitions = {item.code: fetch_list(item) for item in YEONGDEOK_STATUS_FILTERS}
        _reconcile_partition(canonical, age_partitions, label="age")
        _reconcile_partition(canonical, group_partitions, label="group")
        _reconcile_partition(canonical, status_partitions, label="status")

        current = [row for row in canonical if row["end"] >= cutoff]
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise YeongdeokContractError(
                f"detail_limit {detail_limit} below required {len(current)}"
            )
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "source_rows": len(canonical),
                "source_total": len(canonical),
                "source_identity_count": len(canonical_ids),
                "source_teach_ids": sorted(canonical_ids, key=int),
                "source_identity_numeric_min": min(map(int, canonical_ids)) if canonical_ids else None,
                "source_identity_numeric_max": max(map(int, canonical_ids)) if canonical_ids else None,
                "current_source_count": len(current),
                "expired_source_count": len(canonical) - len(current),
                "source_status_counts": dict(Counter(str(row["source_status"]) for row in canonical)),
                "source_category_counts": dict(Counter(str(row["category"]) for row in canonical)),
                "current_source_status_counts": dict(
                    Counter(str(row["source_status"]) for row in current)
                ),
                "age_filter_counts": {key: len(value) for key, value in age_partitions.items()},
                "group_filter_counts": {key: len(value) for key, value in group_partitions.items()},
                "status_filter_counts": {key: len(value) for key, value in status_partitions.items()},
                "age_partition_union_count": len(
                    set().union(*(_partition_ids(rows) for rows in age_partitions.values()))
                ),
                "group_partition_union_count": len(
                    set().union(*(_partition_ids(rows) for rows in group_partitions.values()))
                ),
                "status_partition_union_count": len(
                    set().union(*(_partition_ids(rows) for rows in status_partitions.values()))
                ),
                "partition_overlap_count": 0,
                "empty_partition_count": sum(
                    not rows
                    for partitions in (age_partitions, group_partitions, status_partitions)
                    for rows in partitions.values()
                ),
                "age_partition_complete": True,
                "group_partition_complete": True,
                "status_partition_complete": True,
                "pagination_complete": True,
            }
        )

        rows: list[dict[str, Any]] = []
        for listed in current:
            url = _detail_url(
                str(listed["group_idx"]),
                str(listed["category_idx"]),
                str(listed["teach_idx"]),
            )
            soup, attempts = _fetch_soup(session, url, timeout, current_fetcher)
            meta["source_requests"] += 1
            meta["detail_pages"] += 1
            meta["request_attempts"] += attempts
            rows.append(_parse_detail(listed, soup, cutoff))

        # Re-read the complete ledger only after every current detail.  This
        # turns the list, all partitions, and all details into one atomic
        # observation rather than proving stability before the slowest phase.
        recheck = fetch_list(None, registries=True)
        meta["boundary_rechecks"] += 1
        if _signature(recheck) != _signature(canonical):
            raise YeongdeokContractError("full current ledger stability recheck changed")
        meta["full_ledger_rechecked_after_details"] = True

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _dedupe)(rows))
        expected_ids = {
            f"{YEONGDEOK_PROVIDER}:teach:{row['teach_idx']}" for row in current
        }
        if len(rows) != len(current) or {
            str(row.get("provider_course_id")) for row in rows
        } != expected_ids:
            raise YeongdeokContractError("dedupe changed the current teach identity set")
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        meta["privacy_violations"] = len(privacy_errors)
        if privacy_errors:
            raise YeongdeokContractError("; ".join(privacy_errors[:5]))
        semantic_counts = Counter(
            (
                _clean(row.get("title")).casefold(),
                _clean(row.get("branch")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
                _clean(row.get("venue")),
            )
            for row in rows
        )
        semantic_duplicates = sum(count - 1 for count in semantic_counts.values() if count > 1)
        meta["semantic_duplicate_count"] = semantic_duplicates
        if semantic_duplicates:
            raise YeongdeokContractError("semantic duplicate current courses detected")
        meta.update(
            {
                "returned_count": len(rows),
                "status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "raw_status_counts": dict(Counter(str(row["raw_status"]) for row in rows)),
                "branch_counts": dict(Counter(str(row["branch"]) for row in rows)),
                "category_counts": dict(Counter(str(row["category"]) for row in rows)),
                "application_control_count": sum(bool(row["reservation_available"]) for row in rows),
                "actionable_application_count": sum(bool(row["reservation_available"]) for row in rows),
                "no_current_data": not rows,
                "details_complete": meta["detail_pages"] == len(current),
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return rows, YEONGDEOK_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], YEONGDEOK_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_yeongdeok_education


__all__ = [
    "YEONGDEOK_PROVIDER",
    "YEONGDEOK_CANONICAL_CANDIDATE_ID",
    "YEONGDEOK_REJECTED_CANDIDATE_ID",
    "YEONGDEOK_MUNICIPALITY_CODE",
    "YEONGDEOK_MUNICIPALITY_NAME",
    "YEONGDEOK_BRANCH",
    "YEONGDEOK_BRANCH_ADDRESS",
    "YEONGDEOK_CANONICAL_URL",
    "YEONGDEOK_CANONICAL_URL_SHA256",
    "YEONGDEOK_RECOMMENDED_MAX_PAGES",
    "YEONGDEOK_RECOMMENDED_DETAIL_LIMIT",
    "YEONGDEOK_PARSER",
    "YEONGDEOK_CANDIDATE_AUDIT",
    "YEONGDEOK_OWNER_BOUNDARIES",
    "YEONGDEOK_LIVE_AUDIT_BASELINE",
    "YeongdeokContractError",
    "collect",
    "collect_yeongdeok_education",
    "is_target",
    "is_yeongdeok_education_target",
]
