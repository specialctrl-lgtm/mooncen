"""Fail-closed collector for Hwacheon-gun's official education catalogue.

The coverage candidates for Hwacheon point at a sports notice board and the
Gangwon education-library site.  Neither is the municipality-wide catalogue.
The official replacement is Hwacheon Lifelong Education's complete lecture
list.  It is a POST-only list with a stable public ``lectureSeq`` identity and
GET-addressable detail pages.

This collector validates the complete unfiltered list, its immediate empty
sentinel and a stable page-one copy.  It independently validates the site's
official operation-date overlap filter for the crawl date, including that the
filtered rows are an exact subset of the unfiltered list.  Every filtered
detail is then checked for its source place and course-bound application
control.  Instructor/contact data, notices, descriptions, attachments,
application payloads, login data and source HTML are never persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


HWACHEON_PROVIDER = "MUNI_HCEDU_IHC_GO_KR_77C64AF6"
HWACHEON_CANONICAL_CANDIDATE_ID = "MUNI_IR_9500D672F3F2"
HWACHEON_MUNICIPALITY_CODE = "5179000000"
HWACHEON_MUNICIPALITY_NAME = "강원특별자치도 화천군"
HWACHEON_HOST = "hcedu.ihc.go.kr"
HWACHEON_LIST_PATH = "/portal/enrolment/lecture"
HWACHEON_DETAIL_PATH = "/portal/enrolment/lectureView"
HWACHEON_CANONICAL_URL = f"https://{HWACHEON_HOST}{HWACHEON_LIST_PATH}"
HWACHEON_PAGE_SIZE = 10
HWACHEON_FETCH_ATTEMPTS = 2
HWACHEON_MAX_WORKERS = 16
HWACHEON_MAX_HTML_BYTES = 3_000_000
HWACHEON_FILTER_END = "9999-12-31"
HWACHEON_PARSER = (
    "hwacheon_official_unfiltered_education+all_pages+empty_sentinel+"
    "stable_page1+official_operation_overlap_partition+current_details+"
    "course_bound_login_application_controls+source_places+pii_allowlist"
)
HWACHEON_OWNERSHIP_SCOPE = (
    "hwacheon_official_complete_lifelong_youth_family_and_culture_education"
)

HWACHEON_SPORT_NOTICE_URL = "https://ihcsport.or.kr:449/board/notice2"
HWACHEON_NEWS_NOTICE_URL = "http:" "//kceftimes.or.kr/?p=116081"
HWACHEON_LIBRARY_MAIN_URL = "https://lib.gwe.go.kr/hwlib/main"
HWACHEON_LIBRARY_PROGRAM_URL = (
    "https://lib.gwe.go.kr/hwlib/menu/3130/lecture-event/list/all"
)
HWACHEON_GENERAL_HOMEPAGE_URL = "https://www.ihc.go.kr/"
HWACHEON_YOUTH_NOTICE_URL = (
    "https://www.ihc.go.kr/www/selectBbsNttList.do?bbsNo=51&key=270"
)

HWACHEON_EXCLUDED_CANDIDATE_IDS = frozenset(
    {
        "MUNI_IR_CFFAAE7FD965",
        "MUNI_IR_F6578EDB38BF",
        "MUNI_IR_DB2B3B149783",
        "MUNI_IR_60F5B84EA67B",
        "MUNI_IR_1280E2FAFC4B",
    }
)
HWACHEON_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    HWACHEON_CANONICAL_CANDIDATE_ID: {
        "decision": "include_new_official_complete_catalogue_owner",
        "provider": HWACHEON_PROVIDER,
        "url": HWACHEON_CANONICAL_URL,
        "owner": HWACHEON_PROVIDER,
        "reason": "official structured municipality-wide lecture catalogue",
    },
    "MUNI_IR_CFFAAE7FD965": {
        "decision": "excluded_sports_notice_board_not_course_catalogue",
        "provider": "MUNI_IHCSPORT_OR_KR_449_11A3E043",
        "url": HWACHEON_SPORT_NOTICE_URL,
        "owner": "",
        "reason": "sports-association notices, not a structured application list",
    },
    "MUNI_IR_F6578EDB38BF": {
        "decision": "excluded_third_party_historical_notice_with_attachment",
        "provider": "MUNI_KCEFTIMES_OR_KR_F223548A",
        "url": HWACHEON_NEWS_NOTICE_URL,
        "owner": "",
        "reason": "third-party 2020 single notice whose attachment may contain PII",
    },
    "MUNI_IR_DB2B3B149783": {
        "decision": "excluded_separate_ready_education_library_owner",
        "provider": "MUNI_LIB_GWE_GO_KR_8D8033C9",
        "url": HWACHEON_LIBRARY_MAIN_URL,
        "owner": "MUNI_LIB_GWE_GO_KR_8D8033C9",
        "reason": "separate Gangwon education-library catalogue already collected",
    },
    "MUNI_IR_60F5B84EA67B": {
        "decision": "excluded_general_county_homepage",
        "provider": "MUNI_WWW_IHC_GO_KR_C2433826",
        "url": HWACHEON_GENERAL_HOMEPAGE_URL,
        "owner": "",
        "reason": "general municipal homepage, not a course catalogue",
    },
    "MUNI_IR_1280E2FAFC4B": {
        "decision": "excluded_youth_notice_board_not_application_catalogue",
        "provider": "MUNI_WWW_IHC_GO_KR_96F4A8D3",
        "url": HWACHEON_YOUTH_NOTICE_URL,
        "owner": "",
        "reason": "county youth BBS notices, not the complete structured list",
    },
}

HWACHEON_PROVIDER_AUDIT: Mapping[str, Mapping[str, str]] = {
    value["provider"]: {
        "decision": value["decision"],
        "url": value["url"],
        "reason": value["reason"],
    }
    for value in HWACHEON_CANDIDATE_AUDIT.values()
}

HWACHEON_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "municipal_owner_before_audit": 0,
    "coverage_review_candidate_count": 2,
    "raw_candidate_count": 5,
    "canonical_url": HWACHEON_CANONICAL_URL,
    "unfiltered_total": 1165,
    "page_size": 10,
    "advertised_pages": 117,
    "immediate_empty_page": 118,
    "page_one_stable": True,
    "unique_source_identities": 1165,
    "duplicate_source_identities": 0,
    "unfiltered_status_counts": {
        "기관자체모집": 15,
        "모집중": 24,
        "수강완료": 931,
        "수강중": 123,
        "운영대기": 63,
        "폐강": 9,
    },
    "official_overlap_filter_from": "2026-07-21",
    "official_overlap_filter_to": HWACHEON_FILTER_END,
    "official_overlap_total": 211,
    "official_overlap_pages": 22,
    "official_overlap_immediate_empty_page": 23,
    "official_overlap_status_counts": {
        "모집중": 24,
        "수강중": 123,
        "운영대기": 63,
        "폐강": 1,
    },
    "details_verified": 211,
    "cancelled_overlap_rows": 1,
    "non_cancelled_rows_returned": 210,
    "source_place_variants": 56,
    "source_institution_variants": 9,
    "course_bound_application_controls": 211,
    "actionable_application_controls": 24,
    "separate_library_live_rows": 7,
    "conclusion": (
        "add the official lifelong-education catalogue as a new municipal "
        "owner; keep the education library as a separate existing owner"
    ),
}

HWACHEON_PII_FIELDS_DISCARDED = (
    "강사명",
    "문의",
    "유의사항",
    "강의내용",
    "강의계획서 및 첨부파일",
    "로그인/회원 정보",
    "신청 form payload",
    "source HTML",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, Optional[Mapping[str, str]], int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_PAGER_RE = re.compile(r"^(\d+)\s*/\s*(\d+)$")
_LIST_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)"
)
_FLEX_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:년|[-./])\s*(\d{1,2})\s*"
    r"(?:월|[-./])\s*(\d{1,2})\s*(?:일)?(?!\d)"
)
_CAPACITY_RE = re.compile(r"^(\d{1,7})\s*/\s*(\d{1,7})$")
_DETAIL_CAPACITY_RE = re.compile(
    r"접수인원\s*([\d,]+)명.*?모집\s*([\d,]+)명.*?대기\s*([\d,]+)명"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LIST_HEADERS = (
    "번호",
    "수강대상",
    "강좌명",
    "접수기간",
    "운영기간",
    "접수/정원",
    "상태",
)
_LIST_CAPTION = (
    "수강대상, 강좌명, 접수기간, 운영기간, 인원/정원, 상태를 "
    "안내하는 강좌목록입니다."
)
_DETAIL_LABELS = (
    (
        "강좌명",
        "교육대상",
        "강좌분야",
        "교육장소",
        "운영기간",
        "수강료",
        "유의사항",
        "문의",
    ),
    ("접수방법", "접수기간", "모집인원", "문의"),
    ("강사명", "강의내용", "강의계획서"),
)
_SOURCE_STATUS_MAP: Mapping[str, str] = {
    "모집대기": "SCHEDULED",
    "모집중": "OPEN",
    "운영대기": "CLOSED",
    "수강중": "CLOSED",
    "수강완료": "CLOSED",
    "기관자체모집": "OPEN",
    "폐강": "CANCELLED",
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "list_number",
        "source_status",
        "source_target",
        "source_category",
        "source_place",
        "education_institution",
        "source_period",
        "source_list_period",
        "source_application_period",
        "source_capacity_current",
        "source_capacity_total",
        "source_waiting_total",
        "source_fee",
        "source_application_method",
        "official_filter_from",
        "official_filter_to",
        "official_filtered_overlap",
        "service_family",
        "detail_verified",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
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
        "notice",
        "attachments",
        "attachment_urls",
        "detail_pairs",
        "detail_description",
        "source_html",
        "raw_html",
        "login_payload",
        "application_payload",
    }
)


class HwacheonContractError(ValueError):
    """Raised when Hwacheon's reviewed public source contract changes."""


@dataclass
class _ListPage:
    rows: list[dict[str, Any]]
    total: int
    current_page: int
    last_page: int
    empty_sentinel: bool
    errors: list[str]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ",
        html.unescape(str(value or ""))
        .replace("\xa0", " ")
        .replace("\u200b", ""),
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


def _compare_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return ""
    host = parsed.hostname.lower()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return f"{parsed.scheme.lower()}://{host}{parsed.path}" + (
        f"?{query}" if query else ""
    )


def is_hwacheon_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == HWACHEON_PROVIDER
        and _compare_url(_target_value(target, "url"))
        == _compare_url(HWACHEON_CANONICAL_URL)
    )


def is_hwacheon_excluded_candidate(target: Any) -> bool:
    candidate_id = _clean(_target_value(target, "candidate_id"))
    compared = _compare_url(_target_value(target, "url"))
    return candidate_id in HWACHEON_EXCLUDED_CANDIDATE_IDS or compared in {
        _compare_url(HWACHEON_SPORT_NOTICE_URL),
        _compare_url(HWACHEON_NEWS_NOTICE_URL),
        _compare_url(HWACHEON_LIBRARY_MAIN_URL),
        _compare_url(HWACHEON_LIBRARY_PROGRAM_URL),
        _compare_url(HWACHEON_GENERAL_HOMEPAGE_URL),
        _compare_url(HWACHEON_YOUTH_NOTICE_URL),
    }


def is_hwacheon_separate_library_target(target: Any) -> bool:
    return _compare_url(_target_value(target, "url")) in {
        _compare_url(HWACHEON_LIBRARY_MAIN_URL),
        _compare_url(HWACHEON_LIBRARY_PROGRAM_URL),
    }


def hwacheon_list_payload(
    page: Any = 1,
    *,
    search_from_date: Any = "",
    search_to_date: Any = "",
) -> dict[str, str]:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return {}
    from_text, to_text = _clean(search_from_date), _clean(search_to_date)
    for value in (from_text, to_text):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                return {}
    if bool(from_text) != bool(to_text):
        return {}
    if from_text and from_text > to_text:
        return {}
    return {
        "mode": "LIST",
        "pageIndex": str(page),
        "lectureSeq": "0",
        "target": "",
        "classification": "",
        "place": "",
        "searchFromDate": from_text,
        "searchToDate": to_text,
        "receiptStartDate": "",
        "receiptEndDate": "",
        "searchKeyword": "",
    }


def hwacheon_detail_url(identity: Any) -> str:
    identity_text = _clean(identity)
    if not _IDENTITY_RE.fullmatch(identity_text):
        return ""
    return f"https://{HWACHEON_HOST}{HWACHEON_DETAIL_PATH}?" + urlencode(
        {"lectureSeq": identity_text}
    )


def _default_session_factory() -> requests.Session:
    value = requests.Session()
    value.verify = True
    value.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://hcedu.ihc.go.kr/portal/)"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return value


def _default_fetcher(
    session: Any,
    url: str,
    payload: Optional[Mapping[str, str]],
    timeout: int,
) -> BeautifulSoup:
    if payload is None:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        expected_path = HWACHEON_DETAIL_PATH
    else:
        response = session.post(
            url, data=dict(payload), timeout=timeout, allow_redirects=True
        )
        expected_path = HWACHEON_LIST_PATH
    response.raise_for_status()
    final = urlparse(_clean(getattr(response, "url", url)))
    if (
        final.scheme.lower() != "https"
        or final.hostname != HWACHEON_HOST
        or final.username
        or final.password
        or final.port
        or final.fragment
        or final.path.split(";", 1)[0] != expected_path
    ):
        raise ValueError("response left the official Hwacheon HTTPS scope")
    if "html" not in _clean(response.headers.get("Content-Type")).lower():
        raise ValueError("response is not HTML")
    content = response.content
    if len(content) > HWACHEON_MAX_HTML_BYTES:
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
        if len(value) > HWACHEON_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > HWACHEON_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return _coerce_soup(bytes(content))
    raise TypeError("fetcher must return HTML, bytes, a response, or BeautifulSoup")


def _fetch_parse_many(
    items: Iterable[
        tuple[
            Any,
            str,
            Optional[Mapping[str, str]],
            Callable[[BeautifulSoup], Any],
        ]
    ],
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
        key: Any,
        url: str,
        payload: Optional[Mapping[str, str]],
        parser: Callable[[BeautifulSoup], Any],
    ) -> tuple[Any, Any]:
        last_error: Optional[Exception] = None
        for _attempt in range(HWACHEON_FETCH_ATTEMPTS):
            session = session_factory()
            try:
                raw = fetcher(session, url, payload, timeout)
                return key, parser(_coerce_soup(raw))
            except Exception as exc:
                last_error = exc
            finally:
                _close_quietly(session)
        assert last_error is not None
        raise last_error

    values: dict[Any, Any] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        futures = {
            executor.submit(worker, key, url, payload, parser): key
            for key, url, payload, parser in tasks
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                result_key, result = future.result()
                values[result_key] = result
            except Exception as exc:
                errors.append(f"{key}: {_clean(exc)}")
    return values, errors


def _control_value(form: Any, name: str) -> tuple[int, str]:
    controls = form.select(f"[name='{name}']")
    if len(controls) != 1:
        return len(controls), ""
    control = controls[0]
    if control.name == "select":
        selected = control.select("option[selected]")
        if selected:
            return 1, _clean(selected[0].get("value"))
        first = control.select_one("option")
        return 1, _clean(first.get("value")) if first else ""
    return 1, _clean(control.get("value"))


def _list_form_errors(
    soup: BeautifulSoup,
    expected_page: int,
    expected_from: str,
    expected_to: str,
) -> list[str]:
    forms = soup.select("form#searchForm")
    if len(forms) != 1:
        return [f"page {expected_page}: list form missing or duplicated"]
    form = forms[0]
    errors: list[str] = []
    action_text = _clean(form.get("action"))
    action = urlparse(urljoin(HWACHEON_CANONICAL_URL, action_text))
    if (
        _clean(form.get("method")).lower() != "post"
        or action.scheme != "https"
        or action.hostname != HWACHEON_HOST
        or action.path.split(";", 1)[0] != HWACHEON_LIST_PATH
        or action.fragment
    ):
        errors.append(f"page {expected_page}: list form method/action changed")
    for name, wanted in (
        ("mode", "LIST"),
        ("pageIndex", str(expected_page)),
        ("lectureSeq", "0"),
        ("searchFromDate", expected_from),
        ("searchToDate", expected_to),
        ("receiptStartDate", ""),
        ("receiptEndDate", ""),
        ("searchKeyword", ""),
    ):
        count, actual = _control_value(form, name)
        if count != 1 or actual != wanted:
            errors.append(f"page {expected_page}: form field {name} changed")
    for name in ("target", "classification", "place"):
        count, actual = _control_value(form, name)
        if count != 1 or actual:
            errors.append(f"page {expected_page}: filter {name} is not unfiltered")
    status_controls = form.select("[name='searchStatus']")
    status_values = {_clean(control.get("value")) for control in status_controls}
    if status_values != {"RECRUIT", "RECEIPT", "TAKING", "COMPLETE", "OFFLINE"}:
        errors.append(f"page {expected_page}: status filter options changed")
    if any(control.has_attr("checked") for control in status_controls):
        errors.append(f"page {expected_page}: status filter is not unfiltered")
    return errors


def _date_pair(value: Any, field: str, *, flexible: bool = False) -> tuple[date, date]:
    pattern = _FLEX_DATE_RE if flexible else _LIST_DATE_RE
    matches = pattern.findall(_clean(value))
    if len(matches) != 2:
        raise HwacheonContractError(f"{field}: expected exactly two dates")
    result: list[date] = []
    for year, month, day_value in matches:
        try:
            result.append(date(int(year), int(month), int(day_value)))
        except ValueError as exc:
            raise HwacheonContractError(f"{field}: invalid calendar date") from exc
    if result[0] > result[1]:
        raise HwacheonContractError(f"{field}: reversed dates")
    return result[0], result[1]


def _capacity(value: Any) -> tuple[int, int]:
    match = _CAPACITY_RE.fullmatch(_clean(value).replace(",", ""))
    if not match:
        raise HwacheonContractError("list capacity contract changed")
    return int(match.group(1)), int(match.group(2))


def _parse_list_row(tr: Any, expected_page: int) -> dict[str, Any]:
    identity = _clean(tr.get("data-lecture-seq"))
    if not _IDENTITY_RE.fullmatch(identity):
        raise HwacheonContractError("course source identity changed")
    cells = tr.find_all("td", recursive=False)
    if len(cells) != 7:
        raise HwacheonContractError("course row cell count changed")
    values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
    if not values[0].isdigit() or int(values[0]) < 1:
        raise HwacheonContractError("course list number changed")
    title_controls = cells[2].select(".btn-view")
    status_controls = cells[6].select(".btn-view")
    if len(title_controls) != 1 or len(status_controls) != 1:
        raise HwacheonContractError("course detail controls changed")
    title = _clean(title_controls[0].get_text(" ", strip=True))
    status = _clean(status_controls[0].get_text(" ", strip=True))
    if title != values[2] or not title:
        raise HwacheonContractError("course title changed or is empty")
    if status != values[6] or status not in _SOURCE_STATUS_MAP:
        raise HwacheonContractError("course public status changed")
    for control in (title_controls[0], status_controls[0]):
        if control.name == "a" and _clean(control.get("href")) != "#":
            raise HwacheonContractError("course detail control destination changed")
    if not values[1] or not values[3] or not values[4]:
        raise HwacheonContractError("required list field is empty")
    apply_start, apply_end = _date_pair(values[3], "application period")
    current, total = _capacity(values[5])
    return {
        "identity": identity,
        "number": int(values[0]),
        "target": values[1],
        "title": title,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "source_application_period": values[3],
        "source_list_period": values[4],
        "capacity_current": current,
        "capacity_total": total,
        "source_status": status,
        "list_page": expected_page,
        "raw_url": hwacheon_detail_url(identity),
    }


def _parse_list(
    soup: BeautifulSoup,
    expected_page: int,
    expected_from: str,
    expected_to: str,
) -> _ListPage:
    errors = _list_form_errors(
        soup, expected_page, expected_from, expected_to
    )
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if not all(value in page_title for value in ("강좌목록", "화천군평생교육", "수강신청")):
        errors.append(f"page {expected_page}: official list title changed")
    pager_nodes = soup.select(".pager-num")
    current_page, advertised_last = -1, -1
    if len(pager_nodes) != 1:
        errors.append(f"page {expected_page}: pager count missing or duplicated")
    else:
        match = _PAGER_RE.fullmatch(
            _clean(pager_nodes[0].get_text(" ", strip=True))
        )
        if not match:
            errors.append(f"page {expected_page}: pager format changed")
        else:
            current_page, advertised_last = map(int, match.groups())
            if current_page != expected_page:
                errors.append(f"page {expected_page}: pager current page changed")
    tables = soup.select("table.skinTb.width768")
    rows: list[dict[str, Any]] = []
    empty_sentinel = False
    if len(tables) != 1:
        errors.append(f"page {expected_page}: course table missing or duplicated")
    else:
        table = tables[0]
        caption = _clean(
            table.caption.get_text(" ", strip=True) if table.caption else ""
        )
        if caption != _LIST_CAPTION:
            errors.append(f"page {expected_page}: course table caption changed")
        headers = tuple(
            _clean(node.get_text(" ", strip=True))
            for node in table.select("thead th")
        )
        if headers != _LIST_HEADERS:
            errors.append(f"page {expected_page}: course table headers changed")
        body_rows = table.select("tbody > tr")
        data_rows = [row for row in body_rows if row.has_attr("data-lecture-seq")]
        non_data = [row for row in body_rows if not row.has_attr("data-lecture-seq")]
        empty_sentinel = not data_rows and (
            not non_data
            or (
                len(non_data) == 1
                and len(non_data[0].find_all("td", recursive=False)) == 1
                and "없" in _clean(non_data[0].get_text(" ", strip=True))
            )
        )
        if data_rows and non_data:
            errors.append(f"page {expected_page}: mixed data/sentinel rows")
        for index, tr in enumerate(data_rows, 1):
            try:
                rows.append(_parse_list_row(tr, expected_page))
            except Exception as exc:
                errors.append(f"page {expected_page} row {index}: {_clean(exc)}")
    active = [
        _clean(node.get_text(" ", strip=True))
        for node in soup.select(".pager-link.active")
    ]
    if rows and active != [str(expected_page)]:
        errors.append(f"page {expected_page}: active-page indicator changed")
    if not rows and active:
        errors.append(f"page {expected_page}: empty page has an active-page marker")
    if rows:
        total = rows[0]["number"] + (expected_page - 1) * HWACHEON_PAGE_SIZE
        last_page = max(1, math.ceil(total / HWACHEON_PAGE_SIZE))
        if advertised_last != last_page:
            errors.append(f"page {expected_page}: advertised last page changed")
        numbers = [row["number"] for row in rows]
        if numbers != list(range(numbers[0], numbers[0] - len(numbers), -1)):
            errors.append(f"page {expected_page}: list number sequence changed")
    else:
        total = 0 if expected_page == 1 and empty_sentinel else -1
        if advertised_last in {0, 1} and total == 0:
            last_page = 1
        else:
            last_page = advertised_last
        if not empty_sentinel:
            errors.append(f"page {expected_page}: empty sentinel changed")
    return _ListPage(
        rows=rows,
        total=total,
        current_page=current_page,
        last_page=last_page,
        empty_sentinel=empty_sentinel,
        errors=errors,
    )


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _clean(row.get("identity")),
        _clean(row.get("title")),
        _clean(row.get("target")),
        _clean(row.get("source_application_period")),
        _clean(row.get("source_list_period")),
        int(row.get("capacity_current", -1)),
        int(row.get("capacity_total", -1)),
        _clean(row.get("source_status")),
    )


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(_row_signature(row) for row in rows)


def _validate_partition_pages(
    *,
    label: str,
    first: _ListPage,
    remaining: Mapping[Any, Any],
    errors: list[str],
) -> tuple[list[dict[str, Any]], dict[int, int], int, int]:
    total, last = first.total, first.last_page
    rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    first_signature = _page_signature(first.rows)
    for page in range(1, last + 1):
        parsed = first if page == 1 else remaining.get((label, page, "data"))
        if not isinstance(parsed, _ListPage):
            errors.append(f"{label} page {page}: response missing")
            continue
        errors.extend(parsed.errors)
        if parsed.total != total or parsed.last_page != last:
            errors.append(f"{label} page {page}: total/last changed")
        expected = (
            HWACHEON_PAGE_SIZE
            if page < last
            else max(0, total - (last - 1) * HWACHEON_PAGE_SIZE)
        )
        if len(parsed.rows) != expected:
            errors.append(
                f"{label} page {page}: expected {expected} rows, "
                f"got {len(parsed.rows)}"
            )
        expected_first = total - (page - 1) * HWACHEON_PAGE_SIZE
        numbers = [row["number"] for row in parsed.rows]
        if numbers != list(
            range(expected_first, expected_first - len(parsed.rows), -1)
        ):
            errors.append(f"{label} page {page}: global list number sequence changed")
        page_counts[page] = len(parsed.rows)
        rows.extend(parsed.rows)
    sentinel = remaining.get((label, last + 1, "sentinel"))
    if not isinstance(sentinel, _ListPage):
        errors.append(f"{label} sentinel response missing")
    elif (
        sentinel.errors
        or sentinel.current_page != last + 1
        or sentinel.last_page != last
        or sentinel.rows
        or not sentinel.empty_sentinel
    ):
        errors.extend(sentinel.errors)
        errors.append(f"{label} immediate empty sentinel changed")
    recheck = remaining.get((label, 1, "recheck"))
    if not isinstance(recheck, _ListPage):
        errors.append(f"{label} page-one recheck missing")
    elif (
        recheck.errors
        or recheck.total != total
        or recheck.last_page != last
        or _page_signature(recheck.rows) != first_signature
    ):
        errors.extend(recheck.errors)
        errors.append(f"{label} page-one stability recheck changed")
    return rows, page_counts, int(isinstance(sentinel, _ListPage)), int(
        isinstance(recheck, _ListPage)
    )


def _detail_values(
    soup: BeautifulSoup,
) -> tuple[list[list[Any]], list[str]]:
    groups = soup.select(".info_list .skinTb")
    errors: list[str] = []
    if len(groups) != 3:
        return [], ["detail field groups missing or duplicated"]
    values: list[list[Any]] = []
    for group_index, (group, expected_labels) in enumerate(
        zip(groups, _DETAIL_LABELS), 1
    ):
        rows = group.select(".skinTb-tr")
        labels: list[str] = []
        group_values: list[Any] = []
        for row in rows:
            headings = row.select(":scope > .skinTb-th")
            cells = row.select(":scope > .skinTb-td")
            if len(headings) != 1 or len(cells) != 1:
                errors.append(
                    f"detail group {group_index}: label/value structure changed"
                )
                continue
            labels.append(_clean(headings[0].get_text(" ", strip=True)))
            group_values.append(cells[0])
        if tuple(labels) != expected_labels:
            errors.append(f"detail group {group_index}: field set/order changed")
        values.append(group_values)
    return values, errors


def _optional_operation_dates(value: Any) -> tuple[str, str]:
    matches = _FLEX_DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        return "", ""
    try:
        values = [date(int(y), int(m), int(d)) for y, m, d in matches]
    except ValueError:
        return "", ""
    if values[0] > values[1]:
        return "", ""
    return values[0].isoformat(), values[1].isoformat()


def _fee(value: Any) -> tuple[str, int]:
    text = _clean(value)
    if not text:
        raise HwacheonContractError("course fee is empty")
    if text in {"무료", "없음", "0", "0원"} or text.startswith("무료"):
        return text, 0
    match = re.search(r"([\d,]+)\s*원", text)
    if match:
        return text, int(match.group(1).replace(",", ""))
    if re.fullmatch(r"[\d,]+", text):
        return text, int(text.replace(",", ""))
    # Some institutions publish prose-only material-cost guidance.  Preserve
    # that official text and leave the normalized numeric amount unknown/zero.
    return text, 0


def _institution_from_place(value: Any) -> str:
    place = _clean(value)
    match = re.fullmatch(r"([^()]+?)\s*\((.+)\)", place)
    return _clean(match.group(1)) if match else place


def _validate_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> tuple[Optional[dict[str, Any]], list[str]]:
    identity = _clean(listed["identity"])
    label = f"course {identity} detail"
    errors: list[str] = []
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if not all(value in page_title for value in ("강좌보기", "화천군평생교육", "수강신청")):
        errors.append(f"{label}: official detail title changed")
    forms = soup.select("form#lectureViewForm")
    form = forms[0] if len(forms) == 1 else None
    if form is None:
        errors.append(f"{label}: course-bound detail form missing or duplicated")
    else:
        action_text = _clean(form.get("action"))
        action = urlparse(urljoin(hwacheon_detail_url(identity), action_text))
        if (
            _clean(form.get("method")).lower() != "post"
            or action.scheme != "https"
            or action.hostname != HWACHEON_HOST
            or action.path.split(";", 1)[0] != HWACHEON_DETAIL_PATH
            or action.fragment
        ):
            errors.append(f"{label}: detail form method/action changed")
        controls = form.select("input[name='lectureSeq']")
        if len(controls) != 1 or _clean(controls[0].get("value")) != identity:
            errors.append(f"{label}: detail form course identity changed")
    groups, group_errors = _detail_values(soup)
    errors.extend(f"{label}: {error}" for error in group_errors)
    if len(groups) != 3 or any(
        len(values) != len(labels)
        for values, labels in zip(groups, _DETAIL_LABELS)
    ):
        return None, errors
    safe = [[_clean(cell.get_text(" ", strip=True)) for cell in group] for group in groups]
    course, receipt, _discarded = safe
    if course[0] != _clean(listed["title"]):
        errors.append(f"{label}: list/detail title mismatch")
    if course[1] != _clean(listed["target"]):
        errors.append(f"{label}: list/detail target mismatch")
    if not course[2] or not course[4] or not receipt[0]:
        errors.append(f"{label}: required detail field is empty")
    try:
        list_apply = (listed["apply_start"], listed["apply_end"])
        detail_apply = _date_pair(
            receipt[1], f"{label} application period", flexible=True
        )
        if detail_apply != list_apply:
            errors.append(f"{label}: list/detail application dates mismatch")
    except Exception as exc:
        errors.append(_clean(exc))
        detail_apply = (cutoff, cutoff)
    state_nodes = groups[1][1].select(".stateBtn")
    detail_state = (
        _clean(state_nodes[0].get_text(" ", strip=True))
        if len(state_nodes) == 1
        else ""
    )
    source_status = _clean(listed["source_status"])
    if detail_state != source_status:
        errors.append(f"{label}: list/detail public status mismatch")
    match = _DETAIL_CAPACITY_RE.search(receipt[2].replace("\xa0", " "))
    if not match:
        errors.append(f"{label}: detail capacity contract changed")
        detail_current = detail_total = waiting_total = 0
    else:
        detail_current, detail_total, waiting_total = (
            int(value.replace(",", "")) for value in match.groups()
        )
        if (
            detail_current != int(listed["capacity_current"])
            or detail_total != int(listed["capacity_total"])
        ):
            errors.append(f"{label}: list/detail capacity mismatch")
    try:
        fee_text, fee_amount = _fee(course[5])
    except Exception as exc:
        errors.append(f"{label}: {_clean(exc)}")
        fee_text, fee_amount = "", 0
    application_controls = soup.select("a.application-btn")
    for control in application_controls:
        if (
            _clean(control.get_text(" ", strip=True)) != "수강신청"
            or _clean(control.get("href")) != "#"
        ):
            errors.append(f"{label}: application control changed")
    login_gate = any(
        "application-btn" in script.get_text()
        and "/portal/service/login" in script.get_text()
        for script in soup.select("script")
    )
    online_open = source_status == "모집중"
    external_open = source_status == "기관자체모집"
    # The product renders the same login-gated button for every overlap row,
    # including closed and cancelled rows.  Public status, not mere button
    # presence, controls reservation availability.  The hidden lectureSeq is
    # nevertheless checked above for every one of those controls.
    if len(application_controls) != 1:
        errors.append(f"{label}: course-bound application control count changed")
    if not login_gate:
        errors.append(f"{label}: public login application gate changed")
    if online_open and "온라인" not in receipt[0]:
        errors.append(f"{label}: open online application method changed")
    if errors:
        return None, errors
    operation_start, operation_end = _optional_operation_dates(course[4])
    place = course[3]
    institution = _institution_from_place(place)
    branch_hash = hashlib.sha1(place.encode("utf-8")).hexdigest()[:12]
    actionable = online_open or external_open
    application_url = hwacheon_detail_url(identity) if online_open else ""
    row = {
        "provider": HWACHEON_PROVIDER,
        "provider_course_id": f"{HWACHEON_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(listed["title"]),
        "description": _clean(listed["title"]),
        "branch": place,
        "branch_code": f"hwacheon:place:{branch_hash}",
        "preserve_branch": True,
        "provider_organizer": institution,
        "category": course[2],
        "program_type": "교육",
        "raw_url": hwacheon_detail_url(identity),
        "application_url": application_url,
        "application_type": (
            "ONLINE_RESERVATION"
            if online_open
            else "OFFLINE_APPLICATION" if external_open else "INFO_ONLY"
        ),
        "application_method": receipt[0],
        "application_methods": [receipt[0]],
        "reservation_available": actionable,
        "status": _SOURCE_STATUS_MAP[source_status],
        "fee": fee_text,
        "fee_amount": fee_amount,
        "period": course[4],
        "start_date": operation_start,
        "end_date": operation_end,
        "apply_period": (
            f"{detail_apply[0].isoformat()} ~ {detail_apply[1].isoformat()}"
        ),
        "apply_start": detail_apply[0].isoformat(),
        "apply_end": detail_apply[1].isoformat(),
        "schedule_raw": _clean(listed["source_list_period"]),
        "capacity": f"{detail_current}/{detail_total}",
        "capacity_current": detail_current,
        "capacity_total": detail_total,
        "waiting_total": waiting_total,
        "target": course[1],
        "venue": place,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": HWACHEON_PARSER,
        "municipality_code": HWACHEON_MUNICIPALITY_CODE,
        "municipality_full_name": HWACHEON_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": int(listed["list_page"]),
            "list_number": int(listed["number"]),
            "source_status": source_status,
            "source_target": course[1],
            "source_category": course[2],
            "source_place": place,
            "education_institution": institution,
            "source_period": course[4],
            "source_list_period": _clean(listed["source_list_period"]),
            "source_application_period": receipt[1],
            "source_capacity_current": detail_current,
            "source_capacity_total": detail_total,
            "source_waiting_total": waiting_total,
            "source_fee": fee_text,
            "source_application_method": receipt[0],
            "official_filter_from": cutoff.isoformat(),
            "official_filter_to": HWACHEON_FILTER_END,
            "official_filtered_overlap": True,
            "service_family": "education",
            "detail_verified": True,
            "application_control_present": bool(application_controls),
            "application_control_contract": (
                "detail_form:lectureSeq+application-btn+login-gate;"
                "availability_from_public_status"
            ),
            "application_control_verified": True,
        },
    }
    return row, []


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail/application keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
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
        "unfiltered_list_requests": 0,
        "current_filter_list_requests": 0,
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


def collect_hwacheon_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 200,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = HWACHEON_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Hwacheon education snapshot."""

    meta = _base_meta()
    if not is_hwacheon_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Hwacheon owner"
        )
        return [], HWACHEON_PARSER, meta
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
        return [], HWACHEON_PARSER, meta
    try:
        cutoff = _today(today)
    except ValueError as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], HWACHEON_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    current_from = cutoff.isoformat()
    errors: list[str] = []

    first_items = []
    for label, from_date, to_date in (
        ("all", "", ""),
        ("current", current_from, HWACHEON_FILTER_END),
    ):
        payload = hwacheon_list_payload(
            1, search_from_date=from_date, search_to_date=to_date
        )
        first_items.append(
            (
                (label, 1, "data"),
                HWACHEON_CANONICAL_URL,
                payload,
                lambda soup, current_label=label, current_from_date=from_date,
                current_to_date=to_date: _parse_list(
                    soup, 1, current_from_date, current_to_date
                ),
            )
        )
    first_values, fetch_errors = _fetch_parse_many(
        first_items,
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(first_values)
    meta["list_requests"] += len(first_values)
    meta["unfiltered_list_requests"] += int(("all", 1, "data") in first_values)
    meta["current_filter_list_requests"] += int(
        ("current", 1, "data") in first_values
    )
    first_all = first_values.get(("all", 1, "data"))
    first_current = first_values.get(("current", 1, "data"))
    if not isinstance(first_all, _ListPage):
        errors.append("unfiltered page 1: response missing")
    if not isinstance(first_current, _ListPage):
        errors.append("current-filter page 1: response missing")
    if errors or not isinstance(first_all, _ListPage) or not isinstance(
        first_current, _ListPage
    ):
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], HWACHEON_PARSER, meta
    errors.extend(first_all.errors)
    errors.extend(first_current.errors)
    required_list_requests = (
        first_all.last_page + 2 + first_current.last_page + 2
    )
    meta.update(
        {
            "source_total": first_all.total,
            "declared_pages": first_all.last_page,
            "current_filter_total": first_current.total,
            "current_filter_declared_pages": first_current.last_page,
            "required_list_requests": required_list_requests,
            "official_filter_from": current_from,
            "official_filter_to": HWACHEON_FILTER_END,
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
        return [], HWACHEON_PARSER, meta
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], HWACHEON_PARSER, meta

    remaining_items = []
    for label, first, from_date, to_date in (
        ("all", first_all, "", ""),
        ("current", first_current, current_from, HWACHEON_FILTER_END),
    ):
        for page in range(2, first.last_page + 1):
            payload = hwacheon_list_payload(
                page, search_from_date=from_date, search_to_date=to_date
            )
            remaining_items.append(
                (
                    (label, page, "data"),
                    HWACHEON_CANONICAL_URL,
                    payload,
                    lambda soup, current_page=page, current_from_date=from_date,
                    current_to_date=to_date: _parse_list(
                        soup,
                        current_page,
                        current_from_date,
                        current_to_date,
                    ),
                )
            )
        for page, purpose in ((first.last_page + 1, "sentinel"), (1, "recheck")):
            payload = hwacheon_list_payload(
                page, search_from_date=from_date, search_to_date=to_date
            )
            remaining_items.append(
                (
                    (label, page, purpose),
                    HWACHEON_CANONICAL_URL,
                    payload,
                    lambda soup, current_page=page, current_from_date=from_date,
                    current_to_date=to_date: _parse_list(
                        soup,
                        current_page,
                        current_from_date,
                        current_to_date,
                    ),
                )
            )
    remaining, fetch_errors = _fetch_parse_many(
        remaining_items,
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)
    all_remaining_count = sum(key[0] == "all" for key in remaining)
    meta["unfiltered_list_requests"] += all_remaining_count
    meta["current_filter_list_requests"] += len(remaining) - all_remaining_count

    all_rows, all_page_counts, sentinels, rechecks = _validate_partition_pages(
        label="all",
        first=first_all,
        remaining=remaining,
        errors=errors,
    )
    meta["sentinel_requests"] += sentinels
    meta["stability_rechecks"] += rechecks
    current_rows, current_page_counts, sentinels, rechecks = (
        _validate_partition_pages(
            label="current",
            first=first_current,
            remaining=remaining,
            errors=errors,
        )
    )
    meta["sentinel_requests"] += sentinels
    meta["stability_rechecks"] += rechecks

    all_ids = [_clean(row["identity"]) for row in all_rows]
    all_duplicate_count = len(all_ids) - len(set(all_ids))
    if all_duplicate_count:
        errors.append(f"{all_duplicate_count} duplicate unfiltered source identities")
    current_ids = [_clean(row["identity"]) for row in current_rows]
    current_duplicate_count = len(current_ids) - len(set(current_ids))
    if current_duplicate_count:
        errors.append(
            f"{current_duplicate_count} duplicate current-filter source identities"
        )
    all_by_id = {_clean(row["identity"]): row for row in all_rows}
    for row in current_rows:
        identity = _clean(row["identity"])
        original = all_by_id.get(identity)
        if original is None:
            errors.append(
                f"current-filter identity {identity} is absent from all-source"
            )
        elif _row_signature(original) != _row_signature(row):
            errors.append(
                f"current-filter identity {identity} differs from all-source"
            )
    list_complete = bool(
        not errors
        and len(all_rows) == first_all.total
        and len(current_rows) == first_current.total
        and meta["list_requests"] == required_list_requests
        and meta["sentinel_requests"] == 2
        and meta["stability_rechecks"] == 2
    )
    if len(current_rows) > detail_limit:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {detail_limit} of "
            f"{len(current_rows)} required current-filter details"
        )

    validated: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    if list_complete and not errors:
        detail_items = [
            (
                ("detail", _clean(row["identity"])),
                _clean(row["raw_url"]),
                None,
                lambda soup, listed=dict(row): _validate_detail(
                    listed, soup, cutoff
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
            identity = _clean(listed["identity"])
            value = details.get(("detail", identity))
            if not isinstance(value, tuple) or len(value) != 2:
                detail_errors.append(f"course {identity}: detail response missing")
                continue
            row, item_errors = value
            if item_errors:
                detail_errors.extend(item_errors)
            elif not isinstance(row, dict):
                detail_errors.append(f"course {identity}: validated row missing")
            else:
                meta["detail_pages"] += 1
                validated.append(row)
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
            row["raw_fields"]["application_control_verified"] is True
            for row in validated
        )
    )
    non_cancelled = [row for row in validated if row["status"] != "CANCELLED"]
    cancelled = [row for row in validated if row["status"] == "CANCELLED"]

    result: list[dict[str, Any]] = []
    if details_complete and application_controls_complete and not errors:
        for row in non_cancelled:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(non_cancelled))
            except Exception as exc:
                errors.append(f"dedupe failed: {_clean(exc)}")
            if len(result) != len(non_cancelled):
                errors.append(
                    "dedupe changed official identity cardinality "
                    f"{len(non_cancelled)} to {len(result)}"
                )
                result = []
            else:
                result_ids = [_clean(row.get("provider_course_id")) for row in result]
                if len(result_ids) != len(set(result_ids)):
                    errors.append("dedupe output contains duplicate course identities")
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
            "ownership_scope": HWACHEON_OWNERSHIP_SCOPE,
            "canonical_url": HWACHEON_CANONICAL_URL,
            "page_counts": all_page_counts,
            "current_filter_page_counts": current_page_counts,
            "source_rows": len(all_rows),
            "current_filter_rows": len(current_rows),
            "cancelled_current_filter_count": len(cancelled),
            "current_source_count": len(non_cancelled),
            "identity_duplicate_count": all_duplicate_count,
            "partition_identity_duplicate_count": current_duplicate_count,
            "branch_counts": dict(Counter(_clean(row["branch"]) for row in result)),
            "source_place_counts": dict(
                Counter(_clean(row["branch"]) for row in validated)
            ),
            "institution_counts": dict(
                Counter(
                    _clean(row["raw_fields"]["education_institution"])
                    for row in result
                )
            ),
            "source_institution_counts": dict(
                Counter(
                    _clean(row["raw_fields"]["education_institution"])
                    for row in validated
                )
            ),
            "status_counts": dict(Counter(_clean(row["status"]) for row in result)),
            "source_status_counts": dict(
                Counter(_clean(row["source_status"]) for row in all_rows)
            ),
            "current_source_status_counts": dict(
                Counter(
                    _clean(row["raw_fields"]["source_status"])
                    for row in validated
                )
            ),
            "application_method_counts": dict(
                Counter(_clean(row["application_method"]) for row in result)
            ),
            "public_application_control_count": sum(
                bool(row["raw_fields"]["application_control_present"])
                for row in result
            ),
            "actionable_application_control_count": sum(
                bool(row["reservation_available"]) for row in result
            ),
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "application_controls_complete": application_controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not non_cancelled),
            "no_current_reason": (
                "official operation-date overlap filter has no non-cancelled courses"
                if snapshot_complete and not non_cancelled
                else ""
            ),
            "municipality_coverage": [HWACHEON_MUNICIPALITY_CODE],
            "candidate_audit": {
                key: dict(value)
                for key, value in HWACHEON_CANDIDATE_AUDIT.items()
            },
            "provider_audit": {
                key: dict(value) for key, value in HWACHEON_PROVIDER_AUDIT.items()
            },
            "discovery_audit": dict(HWACHEON_DISCOVERY_AUDIT),
            "separate_library_boundary": {
                "provider": "MUNI_LIB_GWE_GO_KR_8D8033C9",
                "url": HWACHEON_LIBRARY_PROGRAM_URL,
                "included_in_municipal_result": False,
            },
            "pii_fields_discarded": list(HWACHEON_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, HWACHEON_PARSER, meta


collect = collect_hwacheon_education


__all__ = [
    "HWACHEON_CANONICAL_CANDIDATE_ID",
    "HWACHEON_CANONICAL_URL",
    "HWACHEON_CANDIDATE_AUDIT",
    "HWACHEON_DETAIL_PATH",
    "HWACHEON_DISCOVERY_AUDIT",
    "HWACHEON_EXCLUDED_CANDIDATE_IDS",
    "HWACHEON_FILTER_END",
    "HWACHEON_GENERAL_HOMEPAGE_URL",
    "HWACHEON_HOST",
    "HWACHEON_LIBRARY_MAIN_URL",
    "HWACHEON_LIBRARY_PROGRAM_URL",
    "HWACHEON_LIST_PATH",
    "HWACHEON_MUNICIPALITY_CODE",
    "HWACHEON_MUNICIPALITY_NAME",
    "HWACHEON_NEWS_NOTICE_URL",
    "HWACHEON_PAGE_SIZE",
    "HWACHEON_PARSER",
    "HWACHEON_PII_FIELDS_DISCARDED",
    "HWACHEON_PROVIDER",
    "HWACHEON_PROVIDER_AUDIT",
    "HWACHEON_SPORT_NOTICE_URL",
    "HWACHEON_YOUTH_NOTICE_URL",
    "HwacheonContractError",
    "collect",
    "collect_hwacheon_education",
    "hwacheon_detail_url",
    "hwacheon_list_payload",
    "is_hwacheon_education_target",
    "is_hwacheon_excluded_candidate",
    "is_hwacheon_separate_library_target",
]
