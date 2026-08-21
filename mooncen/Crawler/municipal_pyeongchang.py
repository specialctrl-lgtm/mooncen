"""Fail-closed collector for Pyeongchang-gun's lifelong-course catalogue.

The three promotion-review candidates are a county portal mirror, a general
homepage, and a one-off 2025 lifelong-learning-voucher notice.  None is a
course catalogue.  The official replacement catalogue is the unfiltered
``apply-info-general`` endpoint under ``/pcedu/lifestudy``.

The collector validates every advertised list page, the immediate empty
sentinel, a second copy of page one, every current/future internal detail, and
the course-bound mobile-authentication handoff for every open online course.
One audited row is an explicit link to the separate Gangwon education-library
catalogue; it is counted for source-boundary completeness but excluded from
this municipal owner.  Instructor/contact fields, authentication tokens,
emails, attachments, free-form descriptions, and source HTML are never
persisted.
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


PYEONGCHANG_PROVIDER = "MUNI_PC_GO_KR_D2194516"
PYEONGCHANG_CANONICAL_CANDIDATE_ID = "MUNI_IR_6EE93CC4D5DA"
PYEONGCHANG_HOST = "pc.go.kr"
PYEONGCHANG_MUNICIPALITY_CODE = "5176000000"
PYEONGCHANG_MUNICIPALITY_NAME = "강원특별자치도 평창군"
PYEONGCHANG_LIST_PATH = (
    "/pcedu/lifestudy/apply/apply-info/apply-info-general"
)
PYEONGCHANG_CANONICAL_URL = (
    f"https://{PYEONGCHANG_HOST}{PYEONGCHANG_LIST_PATH}"
)
PYEONGCHANG_AUTH_PATH = "/pcedu/egf/bp/auth/authForm"
PYEONGCHANG_AUTH_MENU_PATH = (
    "/lifestudy/apply/apply-info/apply-info-general"
)
PYEONGCHANG_PAGE_SIZE = 10
PYEONGCHANG_FETCH_ATTEMPTS = 2
PYEONGCHANG_MAX_WORKERS = 16
PYEONGCHANG_MAX_HTML_BYTES = 3_000_000
PYEONGCHANG_PARSER = (
    "pyeongchang_official_unfiltered_lifelong_courses+all_pages+"
    "empty_sentinel+stable_page1+current_details+course_bound_mobile_auth+"
    "external_library_boundary+pii_allowlist"
)
PYEONGCHANG_OWNERSHIP_SCOPE = (
    "pyeongchang_official_internal_general_lifelong_course_catalogue"
)

PYEONGCHANG_GUNBO_PORTAL_URL = "https://gunbo.pc.go.kr/portal"
PYEONGCHANG_GENERAL_HOMEPAGE_URL = "https://www.pc.go.kr/"
PYEONGCHANG_VOUCHER_NOTICE_URL = (
    "https://www.pc.go.kr/portal/government/government-news/"
    "government-news-agency?articleSeq=316071"
)
PYEONGCHANG_DEPRECATED_FACILITY_URL = (
    "https://reserve.pc.go.kr/pcreserve/reserve/sport"
)
PYEONGCHANG_EXTERNAL_LIBRARY_URL = (
    "https://lib.gwe.go.kr/pclib/menu/3218/lecture-event/list/all"
)
PYEONGCHANG_EXTERNAL_LIBRARY_TITLE = (
    "[평창교육도서관] 10주 완성! 대바늘 감성소품(성인)"
)

PYEONGCHANG_DISCOVERY_ALIAS_URLS = (
    "https://pc.go.kr/pcedu",
    "https://pc.go.kr/pcedu/lifestudy",
    "https://pc.go.kr/pcedu/lifestudy/apply",
    "https://pc.go.kr/pcedu/lifestudy/apply/apply-info",
    "https://www.pc.go.kr/pcedu/lifestudy/apply",
    "https://www.pc.go.kr/pcedu/lifestudy/apply/apply-info/apply-info-general",
)
PYEONGCHANG_EXCLUDED_CANDIDATE_IDS = frozenset(
    {
        "MUNI_IR_3B62B91FA6F1",
        "MUNI_IR_4781E25AD1C4",
        "MUNI_IR_614EF1EC613E",
    }
)

PYEONGCHANG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_IR_3B62B91FA6F1": {
        "decision": "excluded_general_county_portal_mirror",
        "provider": "MUNI_GUNBO_PC_GO_KR_77264068",
        "url": PYEONGCHANG_GUNBO_PORTAL_URL,
        "owner": "",
        "reason": "general county portal with no structured course rows",
    },
    "MUNI_IR_4781E25AD1C4": {
        "decision": "excluded_general_county_homepage",
        "provider": "MUNI_WWW_PC_GO_KR_8749C4C8",
        "url": PYEONGCHANG_GENERAL_HOMEPAGE_URL,
        "owner": "",
        "reason": "general homepage/splash page, not a course catalogue",
    },
    "MUNI_IR_614EF1EC613E": {
        "decision": "excluded_single_voucher_notice",
        "provider": "MUNI_WWW_PC_GO_KR_B11A1ACA",
        "url": PYEONGCHANG_VOUCHER_NOTICE_URL,
        "owner": "",
        "reason": (
            "one 2025 lifelong-learning-voucher notice, not a course list; "
            "provider identity also drifted to a deprecated facility target"
        ),
    },
}

PYEONGCHANG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": PYEONGCHANG_CANONICAL_URL,
    "canonical_candidate_id": PYEONGCHANG_CANONICAL_CANDIDATE_ID,
    "advertised_rows": 563,
    "advertised_pages": 57,
    "immediate_empty_page": 58,
    "internal_rows": 562,
    "external_library_reference_rows": 1,
    "unique_source_identities": 563,
    "duplicate_source_identities": 0,
    "page_one_stable": True,
    "source_status_counts": {
        "접수대기": 0,
        "접수중": 78,
        "접수마감": 7,
        "교육중": 21,
        "교육마감": 457,
    },
    "current_or_future_internal_rows": 106,
    "current_details_verified": 106,
    "open_online_rows": 78,
    "course_bound_mobile_auth_verified": 78,
    "current_application_method_counts": {
        "온라인예약": 103,
        "전화접수": 2,
        "온라인예약,방문접수": 1,
    },
    "conclusion": (
        "new pcedu catalogue is complete; exclude one explicit external "
        "education-library reference and the separate facility category"
    ),
}

PYEONGCHANG_PII_FIELDS_DISCARDED = (
    "강사명",
    "전화번호",
    "강의내용",
    "첨부파일",
    "이메일",
    "휴대폰 본인인증 입력",
    "reqInfo 인증 토큰",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_DATETIME_RE = re.compile(
    r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})"
    r"(?:\s+(\d{2}):(\d{2}):(\d{2}))?(?!\d)"
)
_VIEW_RE = re.compile(r"javascript:fnView\('(?P<identity>GJLI\d+)'\);")
_PAGE_RE = re.compile(r"^\s*linkPage\((\d+)\);return\s+false;\s*$")
_TOTAL_RE = re.compile(
    r"총\s*([\d,]+)\s*건의\s*강좌가\s*있습니다\.\s*"
    r"\((\d+)/(\d+)페이지\)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
    "교육중": "CLOSED",
    "교육마감": "CLOSED",
}
_LIST_INFO_FIELDS: Mapping[str, str] = {
    "lecture-target": "대상",
    "lecture-accept-date": "접수기간",
    "lecture-accept-close": "접수마감",
    "lecture-capacity": "정원/모집/대기",
    "lecture-education-date": "교육기간",
}
_DETAIL_FIELDS = frozenset(
    {
        "주관기관",
        "교육기간",
        "분야",
        "교육시간",
        "교육대상",
        "교육장소",
        "지역",
        "접수방법",
        "강사명",
        "접수기간",
        "수강료",
        "접수현황",
        "전화번호",
    }
)
_APPLICATION_METHOD_TOKENS = frozenset({"온라인예약", "방문접수", "전화접수"})
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "source_row_number",
        "source_site",
        "source_status",
        "source_category",
        "source_period",
        "source_application_period",
        "source_application_close",
        "source_schedule",
        "source_capacity_total",
        "source_capacity_current",
        "source_waiting_current",
        "education_institution",
        "source_target",
        "source_venue",
        "source_region",
        "source_fee",
        "source_application_method",
        "service_family",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
        "mobile_auth_verified",
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
        "auth_token",
        "reqInfo",
    }
)


class PyeongchangContractError(ValueError):
    """Raised when the official source no longer satisfies its contract."""


@dataclass
class _ListPage:
    rows: list[dict[str, Any]]
    total: int
    current_page: int
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
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
    ):
        return ""
    pairs = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not (
            drop_paging
            and key.casefold()
            in {
                "pageindex",
                "pageviewtype",
                "searchcondition",
                "searchkeyword",
                "mode",
            }
        )
    ]
    query = urlencode(sorted(pairs))
    return f"https://{parsed.hostname.lower()}{parsed.path}" + (
        f"?{query}" if query else ""
    )


def is_pyeongchang_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == PYEONGCHANG_PROVIDER
        and _compare_url(_target_value(target, "url"))
        == PYEONGCHANG_CANONICAL_URL
    )


def is_pyeongchang_excluded_candidate(target: Any) -> bool:
    candidate_id = _clean(_target_value(target, "candidate_id"))
    compared = _compare_url(_target_value(target, "url"), drop_paging=True)
    return candidate_id in PYEONGCHANG_EXCLUDED_CANDIDATE_IDS or compared in {
        _compare_url(PYEONGCHANG_GUNBO_PORTAL_URL, drop_paging=True),
        _compare_url(PYEONGCHANG_GENERAL_HOMEPAGE_URL, drop_paging=True),
        _compare_url(PYEONGCHANG_VOUCHER_NOTICE_URL, drop_paging=True),
    }


def is_pyeongchang_discovery_alias_target(target: Any) -> bool:
    compared = _compare_url(_target_value(target, "url"), drop_paging=True)
    return bool(
        compared
        and compared
        in {
            _compare_url(value, drop_paging=True)
            for value in PYEONGCHANG_DISCOVERY_ALIAS_URLS
        }
    )


def is_pyeongchang_separate_facility_target(target: Any) -> bool:
    return _compare_url(
        _target_value(target, "url"), drop_paging=True
    ) == _compare_url(PYEONGCHANG_DEPRECATED_FACILITY_URL, drop_paging=True)


def pyeongchang_list_url(page: Any = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return ""
    if page == 1:
        return PYEONGCHANG_CANONICAL_URL
    return PYEONGCHANG_CANONICAL_URL + "?" + urlencode(
        {
            "mode": "list",
            "pageViewType": "list",
            "searchCondition": "LECTURE_NAME",
            "searchKeyword": "",
            "pageIndex": page,
        }
    )


def _course_url(identity: Any, mode: str, page: Any = 1) -> str:
    value = _clean(identity)
    if (
        not re.fullmatch(r"GJLI\d+", value)
        or mode not in {"view", "form"}
        or isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
    ):
        return ""
    return PYEONGCHANG_CANONICAL_URL + "?" + urlencode(
        {
            "courseNo": value,
            "mode": mode,
            "pageIndex": page,
            "pageViewType": "list",
            "searchCondition": "LECTURE_NAME",
        }
    )


def pyeongchang_detail_url(identity: Any, page: Any = 1) -> str:
    return _course_url(identity, "view", page)


def pyeongchang_application_url(identity: Any, page: Any = 1) -> str:
    return _course_url(identity, "form", page)


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://pc.go.kr/pcedu/)"
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
    if (
        final.scheme.lower() != "https"
        or final.hostname != PYEONGCHANG_HOST
        or final.username
        or final.password
        or final.port
        or final.fragment
        or not final.path.startswith("/pcedu/")
    ):
        raise ValueError("response left the official Pyeongchang HTTPS scope")
    content_type = _clean(response.headers.get("Content-Type")).lower()
    if "html" not in content_type:
        raise ValueError("response is not HTML")
    content = response.content
    if len(content) > PYEONGCHANG_MAX_HTML_BYTES:
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
        if len(value) > PYEONGCHANG_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > PYEONGCHANG_MAX_HTML_BYTES:
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
        for _attempt in range(PYEONGCHANG_FETCH_ATTEMPTS):
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


def _date_pair(value: Any, field: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise PyeongchangContractError(f"{field}: expected exactly two dates")
    values: list[date] = []
    for year, month, day_value in matches:
        try:
            values.append(date(int(year), int(month), int(day_value)))
        except ValueError as exc:
            raise PyeongchangContractError(f"{field}: invalid calendar date") from exc
    if values[0] > values[1]:
        raise PyeongchangContractError(f"{field}: reversed dates")
    return values[0], values[1]


def _integers(value: Any, field: str) -> list[int]:
    values = [int(item.replace(",", "")) for item in re.findall(r"[\d,]+", _clean(value))]
    if not values:
        raise PyeongchangContractError(f"{field}: integer missing")
    return values


def _form_value(form: Any, name: str) -> tuple[int, str]:
    nodes = form.select(f'[name="{name}"]') if form is not None else []
    if len(nodes) != 1:
        return len(nodes), ""
    return 1, _clean(nodes[0].get("value"))


def _list_form_errors(soup: BeautifulSoup, page: int) -> list[str]:
    forms = soup.select("form#eduCourseForm[name='eduCourseForm']")
    if len(forms) != 1:
        return [f"page {page}: search form missing or duplicated"]
    form = forms[0]
    errors: list[str] = []
    action = urlparse(urljoin(PYEONGCHANG_CANONICAL_URL, _clean(form.get("action"))))
    if _clean(form.get("method")).lower() != "post" or (
        action.scheme,
        action.hostname,
        action.path,
        action.query,
        action.fragment,
    ) != (
        "https",
        PYEONGCHANG_HOST,
        PYEONGCHANG_LIST_PATH,
        "",
        "",
    ):
        errors.append(f"page {page}: search form method/action changed")
    for name, expected in (
        ("pageIndex", str(page)),
        ("searchCondition", "LECTURE_NAME"),
        ("pageViewType", "list"),
        ("courseNo", ""),
        ("mode", ""),
        ("searchKeyword", ""),
        ("studyStartDate", ""),
        ("studyEndDate", ""),
    ):
        count, value = _form_value(form, name)
        if count != 1 or value != expected:
            errors.append(f"page {page}: unfiltered form field {name} changed")
    all_names = {"allField", "allTarget", "allAgency", "allArea", "allStatus"}
    all_controls = form.select("input[type='checkbox'][name^='all']")
    if (
        {_clean(node.get("name")) for node in all_controls} != all_names
        or any(not node.has_attr("checked") for node in all_controls)
    ):
        errors.append(f"page {page}: all-filter checkbox contract changed")
    item_names = {"fieldList", "targetList", "agencyList", "areaList", "statusList"}
    item_controls = [
        node
        for node in form.select("input[type='checkbox'][name]")
        if _clean(node.get("name")) not in all_names
    ]
    seen_names = {_clean(node.get("name")) for node in item_controls}
    if (
        seen_names != item_names
        or any(node.has_attr("checked") for node in item_controls)
        or any(not _clean(node.get("value")) for node in item_controls)
    ):
        errors.append(f"page {page}: unfiltered item checkbox contract changed")
    return errors


def _list_info_pairs(cell: Any) -> dict[str, str]:
    nodes = cell.select(":scope > em.lecture-info")
    if len(nodes) != len(_LIST_INFO_FIELDS):
        raise PyeongchangContractError("list info field count changed")
    pairs: dict[str, str] = {}
    for node in nodes:
        class_names = set(node.get("class") or []) - {"lecture-info"}
        if len(class_names) != 1:
            raise PyeongchangContractError("list info class contract changed")
        class_name = next(iter(class_names))
        expected_label = _LIST_INFO_FIELDS.get(class_name)
        labels = node.select(":scope > span")
        if expected_label is None or len(labels) != 1:
            raise PyeongchangContractError("list info label structure changed")
        label = _clean(labels[0].get_text(" ", strip=True)).rstrip(": ")
        clone = BeautifulSoup(str(node), "html.parser").select_one("em")
        clone_label = clone.select_one(":scope > span") if clone else None
        if clone_label is not None:
            clone_label.extract()
        value = _clean(clone.get_text(" ", strip=True) if clone else "")
        if label != expected_label or label in pairs:
            raise PyeongchangContractError("list info label/order changed")
        pairs[label] = value
    if tuple(pairs) != tuple(_LIST_INFO_FIELDS.values()):
        raise PyeongchangContractError("list info field order changed")
    return pairs


def _split_site(value: Any) -> tuple[str, str]:
    parts = [_clean(item) for item in _clean(value).split(">")]
    # 180 historical rows legitimately carry only the catalogue label, while
    # the newer rows append the administering agency after ``>``.  Both use
    # an internal GJLI identity; do not invent a county agency for the older
    # records merely because the suffix was absent.
    if parts == ["평생학습관"]:
        return parts[0], parts[0]
    if len(parts) != 2 or parts[0] != "평생학습관" or not parts[1]:
        raise PyeongchangContractError("course site/agency contract changed")
    return parts[0], parts[1]


def _base_row(
    *,
    identity: str,
    page: int,
    row_number: int,
    site: str,
    agency: str,
    title: str,
    source_status: str,
    pairs: Mapping[str, str],
    start: date,
    end: date,
    apply_start: date,
    apply_end: date,
    capacity_total: int,
    capacity_current: int,
    waiting_current: int,
) -> dict[str, Any]:
    return {
        "provider": PYEONGCHANG_PROVIDER,
        "provider_course_id": f"{PYEONGCHANG_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": agency,
        "branch_code": f"pyeongchang:{_normalized(agency)}",
        "preserve_branch": True,
        "provider_organizer": agency,
        "category": "",
        "program_type": "교육",
        "raw_url": pyeongchang_detail_url(identity, page),
        "application_url": "",
        "application_type": "INFO_ONLY",
        "application_method": "",
        "application_methods": [],
        "reservation_available": False,
        "status": _STATUS_MAP[source_status],
        "fee": "",
        "fee_amount": 0,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": _clean(pairs["접수기간"]),
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": "",
        "capacity": f"{capacity_current}/{capacity_total}",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waiting_current": waiting_current,
        "target": _clean(pairs["대상"]),
        "venue": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": PYEONGCHANG_PARSER,
        "municipality_code": PYEONGCHANG_MUNICIPALITY_CODE,
        "municipality_full_name": PYEONGCHANG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": page,
            "source_row_number": row_number,
            "source_site": site,
            "source_status": source_status,
            "source_category": "",
            "source_period": _clean(pairs["교육기간"]),
            "source_application_period": _clean(pairs["접수기간"]),
            "source_application_close": _clean(pairs["접수마감"]),
            "source_schedule": "",
            "source_capacity_total": capacity_total,
            "source_capacity_current": capacity_current,
            "source_waiting_current": waiting_current,
            "education_institution": agency,
            "source_target": _clean(pairs["대상"]),
            "source_venue": "",
            "source_region": "",
            "source_fee": "",
            "source_application_method": "",
            "service_family": "education",
            "application_control_present": False,
            "application_control_contract": "",
            "application_control_verified": False,
            "mobile_auth_verified": False,
            "detail_verified": False,
        },
    }


def _parse_list_row(
    row: Any,
    *,
    page: int,
    expected_number: int,
    cutoff: date,
) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 3:
        raise PyeongchangContractError("course row cell count changed")
    number_text = _clean(cells[0].get_text(" ", strip=True))
    if not number_text.isdigit() or int(number_text) != expected_number:
        raise PyeongchangContractError("course row sequence changed")
    links = cells[1].select(":scope > a[href]")
    if len(links) != 1:
        raise PyeongchangContractError("course row link missing or duplicated")
    link = links[0]
    title_nodes = link.select(":scope > b")
    site_nodes = link.select(":scope > em.lecture-site-name")
    if len(title_nodes) != 1 or len(site_nodes) != 1:
        raise PyeongchangContractError("course title/site structure changed")
    title = _clean(title_nodes[0].get_text(" ", strip=True))
    site = _clean(site_nodes[0].get_text(" ", strip=True))
    if not title:
        raise PyeongchangContractError("course title is empty")
    _site_label, agency = _split_site(site)
    pairs = _list_info_pairs(cells[1])
    source_status = _clean(cells[2].get_text(" ", strip=True))
    states = cells[2].select(":scope > span.state-bx")
    if len(states) != 1 or source_status not in _STATUS_MAP:
        raise PyeongchangContractError("course public status changed")
    start, end = _date_pair(pairs["교육기간"], "education period")
    apply_start, apply_end = _date_pair(pairs["접수기간"], "application period")
    close_dates = _DATE_RE.findall(_clean(pairs["접수마감"]))
    if len(close_dates) != 1 or date(*map(int, close_dates[0])) != apply_end:
        raise PyeongchangContractError("application close/list period mismatch")
    capacity = _integers(pairs["정원/모집/대기"], "capacity")
    if len(capacity) != 3:
        raise PyeongchangContractError("capacity triple changed")
    capacity_total, capacity_current, waiting_current = capacity
    if capacity_total < 0 or capacity_current < 0 or waiting_current < 0:
        raise PyeongchangContractError("negative capacity")
    current = end >= cutoff
    if current and (
        not _clean(pairs["대상"])
        or capacity_total < 1
        or capacity_current > capacity_total
    ):
        raise PyeongchangContractError("current course list field invalid")
    if source_status == "접수중" and not (apply_start <= cutoff <= apply_end):
        raise PyeongchangContractError("open status/application dates mismatch")
    if source_status == "접수대기" and cutoff > apply_start:
        raise PyeongchangContractError("scheduled status/application dates mismatch")
    if source_status == "교육중" and not (start <= cutoff <= end):
        raise PyeongchangContractError("education-in-progress dates mismatch")
    if source_status == "교육마감" and end >= cutoff:
        raise PyeongchangContractError("ended status/education dates mismatch")

    href = _clean(link.get("href"))
    match = _VIEW_RE.fullmatch(href)
    if match is None:
        external = _compare_url(href)
        if (
            external != PYEONGCHANG_EXTERNAL_LIBRARY_URL
            or title != PYEONGCHANG_EXTERNAL_LIBRARY_TITLE
            or agency != "평창교육도서관"
            or _clean(link.get("target")) != "_blank"
        ):
            raise PyeongchangContractError("unknown external course ownership")
        return {
            "external_reference": True,
            "identity": external,
            "title": title,
            "list_page": page,
            "source_row_number": int(number_text),
            "source_status": source_status,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "raw_url": external,
        }
    identity = match.group("identity")
    return _base_row(
        identity=identity,
        page=page,
        row_number=int(number_text),
        site=site,
        agency=agency,
        title=title,
        source_status=source_status,
        pairs=pairs,
        start=start,
        end=end,
        apply_start=apply_start,
        apply_end=apply_end,
        capacity_total=capacity_total,
        capacity_current=capacity_current,
        waiting_current=waiting_current,
    )


def _parse_list(soup: BeautifulSoup, page: int, cutoff: date) -> _ListPage:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    expected_title = (
        "일반강좌정보 - 목록 | 평창군 평생학습관 > 강좌신청 > "
        "강좌정보 > 일반강좌정보"
    )
    if title != expected_title:
        errors.append(f"page {page}: official catalogue title changed")
    summaries = _TOTAL_RE.findall(_clean(soup.get_text(" ", strip=True)))
    if len(summaries) != 1:
        total, current_page, last = 0, page, 1
        errors.append(f"page {page}: advertised total/page summary changed")
    else:
        total = int(summaries[0][0].replace(",", ""))
        current_page = int(summaries[0][1])
        last = int(summaries[0][2])
        if current_page != page or last != max(1, math.ceil(total / PYEONGCHANG_PAGE_SIZE)):
            errors.append(f"page {page}: advertised page boundary changed")
    errors.extend(_list_form_errors(soup, page))
    roots = soup.select(".lecture-list")
    tables = soup.select(".lecture-list > table.skinTb.width1000")
    bodies = soup.select(".lecture-list > table.skinTb.width1000 > tbody")
    if len(roots) != 1 or len(tables) != 1 or len(bodies) != 1:
        return _ListPage([], total, current_page, last, errors + [f"page {page}: list table changed"])
    body_rows = bodies[0].find_all("tr", recursive=False)
    empty_rows = []
    course_rows = []
    for row in body_rows:
        cells = row.find_all("td", recursive=False)
        if (
            len(cells) == 1
            and _clean(cells[0].get("colspan")) == "3"
            and _clean(cells[0].get_text(" ", strip=True)) == "등록된 내용이 없습니다."
        ):
            empty_rows.append(row)
        else:
            course_rows.append(row)
    if course_rows and empty_rows:
        errors.append(f"page {page}: rows and empty sentinel coexist")
    if not course_rows and len(empty_rows) != 1:
        errors.append(f"page {page}: empty sentinel changed")
    if course_rows:
        active = soup.select(".pager-link.active")
        if (
            len(active) != 1
            or _clean(active[0].get_text(" ", strip=True)) != str(page)
            or _clean(active[0].get("onclick")) != "return false;"
        ):
            errors.append(f"page {page}: active pagination marker changed")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(course_rows):
        try:
            rows.append(
                _parse_list_row(
                    row,
                    page=page,
                    expected_number=total - (page - 1) * PYEONGCHANG_PAGE_SIZE - index,
                    cutoff=cutoff,
                )
            )
        except Exception as exc:
            errors.append(f"page {page} row {index + 1}: {_clean(exc)}")
    return _ListPage(rows, total, current_page, last, errors)


def _detail_pairs(root: Any) -> tuple[dict[str, str], list[str]]:
    nodes = root.select("dl.list-dl1.v2") if root is not None else []
    if len(nodes) != 1:
        return {}, ["detail field list missing or duplicated"]
    children = [
        child
        for child in nodes[0].children
        if getattr(child, "name", None) in {"dt", "dd"}
    ]
    if len(children) % 2:
        return {}, ["detail label/value count changed"]
    pairs: dict[str, str] = {}
    errors: list[str] = []
    for index in range(0, len(children), 2):
        label_node, value_node = children[index : index + 2]
        if label_node.name != "dt" or value_node.name != "dd":
            errors.append("detail label/value order changed")
            continue
        label = _clean(label_node.get_text(" ", strip=True))
        value = _clean(value_node.get_text(" ", strip=True))
        if not label or label in pairs:
            errors.append("detail field empty or duplicated")
        else:
            pairs[label] = value
    if set(pairs) != _DETAIL_FIELDS:
        errors.append("detail field set changed")
    return pairs, errors


def _detail_form_errors(root: Any, identity: str, page: int) -> list[str]:
    forms = root.select("form#viewForm[name='viewForm']") if root is not None else []
    if len(forms) != 1:
        return ["detail identity form missing or duplicated"]
    form = forms[0]
    errors: list[str] = []
    action = urlparse(urljoin(PYEONGCHANG_CANONICAL_URL, _clean(form.get("action"))))
    if _clean(form.get("method")).lower() != "post" or (
        action.scheme,
        action.hostname,
        action.path,
        action.query,
    ) != ("https", PYEONGCHANG_HOST, PYEONGCHANG_LIST_PATH, ""):
        errors.append("detail identity form method/action changed")
    for name, expected in (
        ("pageIndex", str(page)),
        ("searchCondition", "LECTURE_NAME"),
        ("searchKeyword", ""),
        ("courseNo", identity),
        ("pageViewType", "list"),
        ("studyStartDate", ""),
        ("studyEndDate", ""),
        ("mode", "list"),
    ):
        count, value = _form_value(form, name)
        if count != 1 or value != expected:
            errors.append(f"detail identity field {name} changed")
    return errors


def _application_methods(value: Any) -> tuple[str, list[str]]:
    text = _clean(value)
    tokens = [_clean(item) for item in text.split(",") if _clean(item)]
    if not tokens or len(tokens) != len(set(tokens)) or not set(tokens) <= _APPLICATION_METHOD_TOKENS:
        raise PyeongchangContractError("application method changed")
    normalized = []
    if "온라인예약" in tokens:
        normalized.append("온라인")
    if "방문접수" in tokens:
        normalized.append("방문")
    if "전화접수" in tokens:
        normalized.append("전화")
    return text, normalized


def _fee(value: Any) -> tuple[str, int]:
    text = _clean(value)
    if text == "무료":
        return text, 0
    values = _integers(text, "course fee")
    if len(values) != 1 or "원" not in text:
        raise PyeongchangContractError("course fee changed")
    return text, values[0]


def _validate_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> tuple[dict[str, Any], list[str]]:
    row = dict(listed)
    row["raw_fields"] = dict(listed["raw_fields"])
    identity = _clean(row["raw_fields"]["identity"])
    page = int(row["raw_fields"]["list_page"])
    label = f"course {identity} detail"
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    expected_title = (
        "일반강좌정보 - 상세 | 평창군 평생학습관 > 강좌신청 > "
        "강좌정보 > 일반강좌정보"
    )
    if title != expected_title:
        errors.append(f"{label}: official detail title changed")
    roots = soup.select("#contentsArea")
    if len(roots) != 1:
        return row, [f"{label}: root missing or duplicated"]
    root = roots[0]
    headings = root.select(":scope > .class-title-bx > h4")
    if len(headings) != 1:
        errors.append(f"{label}: heading missing or duplicated")
        detail_title = detail_status = ""
    else:
        status_nodes = headings[0].select(":scope > .state-bx")
        detail_status = _clean(
            status_nodes[0].get_text(" ", strip=True)
            if len(status_nodes) == 1
            else ""
        )
        clone = BeautifulSoup(str(headings[0]), "html.parser").select_one("h4")
        clone_status = clone.select_one(":scope > .state-bx") if clone else None
        if clone_status is not None:
            clone_status.extract()
        detail_title = _clean(clone.get_text(" ", strip=True) if clone else "")
    if detail_title != _clean(row["title"]):
        errors.append(f"{label}: title list/detail mismatch")
    if detail_status != _clean(row["raw_fields"]["source_status"]):
        errors.append(f"{label}: status list/detail mismatch")
    # The official page keeps the identity form as a direct ``body`` child;
    # ``#contentsArea`` is its sibling, not its descendant.
    errors.extend(f"{label}: {item}" for item in _detail_form_errors(soup, identity, page))
    pairs, pair_errors = _detail_pairs(root)
    errors.extend(f"{label}: {item}" for item in pair_errors)
    if set(pairs) == _DETAIL_FIELDS:
        expected = row["raw_fields"]
        for field, actual, wanted in (
            ("주관기관", pairs["주관기관"], expected["education_institution"]),
            ("교육기간", pairs["교육기간"], expected["source_period"]),
            ("교육대상", pairs["교육대상"], expected["source_target"]),
        ):
            if _clean(actual) != _clean(wanted):
                errors.append(f"{label}: {field} list/detail mismatch")
        try:
            detail_apply = _date_pair(pairs["접수기간"], f"{label} application period")
            if tuple(item.isoformat() for item in detail_apply) != (
                row["apply_start"],
                row["apply_end"],
            ):
                errors.append(f"{label}: application dates list/detail mismatch")
        except Exception as exc:
            errors.append(_clean(exc))
        try:
            capacity = _integers(pairs["접수현황"], f"{label} capacity")
            if capacity != [
                row["capacity_total"],
                row["capacity_current"],
                row["waiting_current"],
            ]:
                errors.append(f"{label}: capacity list/detail mismatch")
        except Exception as exc:
            errors.append(_clean(exc))
        try:
            method_text, methods = _application_methods(pairs["접수방법"])
            row["application_method"] = method_text
            row["application_methods"] = methods
            row["raw_fields"]["source_application_method"] = method_text
        except Exception as exc:
            methods = []
            errors.append(_clean(exc))
        try:
            fee_text, fee_amount = _fee(pairs["수강료"])
            row["fee"] = fee_text
            row["fee_amount"] = fee_amount
            row["raw_fields"]["source_fee"] = fee_text
        except Exception as exc:
            errors.append(_clean(exc))
        row["category"] = _clean(pairs["분야"])
        row["schedule_raw"] = _clean(pairs["교육시간"])
        row["venue"] = _clean(pairs["교육장소"])
        row["raw_fields"].update(
            {
                "source_category": row["category"],
                "source_schedule": row["schedule_raw"],
                "source_venue": row["venue"],
                "source_region": _clean(pairs["지역"]),
            }
        )
        # Three live current/future courses intentionally publish no venue
        # (GJLI1108/GJLI1081/GJLI1006).  Keep that absence explicit; category
        # and schedule remain mandatory and no location may be invented.
        if not row["category"] or not row["schedule_raw"]:
            errors.append(f"{label}: current detail field is empty")

        online_open = row["status"] == "OPEN" and "온라인" in methods
        controls = root.select(":scope > .class-title-bx > a.btn-education-applicant")
        if online_open:
            if (
                len(controls) != 1
                or _clean(controls[0].get_text(" ", strip=True)) != "신청하기"
                or _clean(controls[0].get("href")) != "javascript:;"
            ):
                errors.append(f"{label}: open online application control changed")
            else:
                scripts = "\n".join(
                    node.get_text("\n") for node in soup.select("script:not([src])")
                )
                compact_script = re.sub(r"\s+", "", scripts)
                if (
                    '$('.replace(" ", "")
                    + '".btn-education-applicant").click(function(event){constform=document.getElementById("viewForm");form.mode.value="form";form.submit();});'
                ) not in compact_script:
                    errors.append(f"{label}: application submit handler changed")
                row["application_url"] = pyeongchang_application_url(identity, page)
                row["application_type"] = "ONLINE_RESERVATION"
                row["reservation_available"] = True
                row["raw_fields"]["application_control_present"] = True
                row["raw_fields"]["application_control_contract"] = (
                    "viewForm.courseNo+mode.form+mobile_auth_iframe"
                )
        elif controls:
            errors.append(f"{label}: inactive/offline course exposes online control")
        elif row["status"] == "OPEN":
            row["application_type"] = "OFFLINE_APPLICATION"
            row["reservation_available"] = True
            row["raw_fields"]["application_control_contract"] = (
                "public_open_status+audited_offline_method"
            )
            row["raw_fields"]["application_control_verified"] = not errors
        else:
            row["raw_fields"]["application_control_contract"] = (
                "inactive_detail_has_no_application_control"
            )
            row["raw_fields"]["application_control_verified"] = not errors
            row["raw_fields"]["mobile_auth_verified"] = True
    row["raw_fields"]["detail_verified"] = not errors
    return row, errors


def _validate_application_page(
    soup: BeautifulSoup, identity: str
) -> tuple[str, list[str]]:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    expected_title = (
        "일반강좌정보 본인인증 | 평창군 평생학습관 > 강좌신청 > "
        "강좌정보 > 일반강좌정보"
    )
    if title != expected_title:
        errors.append("application authentication-page title changed")
    frames = soup.select("#contentsArea iframe.confirmIframe[src]")
    if len(frames) != 1:
        return "", errors + ["application authentication iframe changed"]
    frame_url = urljoin(PYEONGCHANG_CANONICAL_URL, _clean(frames[0].get("src")))
    parsed = urlparse(frame_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != PYEONGCHANG_HOST
        or parsed.path != PYEONGCHANG_AUTH_PATH
        or parsed.fragment
        or query
        != {"menuPath": [PYEONGCHANG_AUTH_MENU_PATH], "mode": ["form"]}
    ):
        errors.append("application authentication iframe URL changed")
    return frame_url, errors


def _validate_auth_iframe(soup: BeautifulSoup, identity: str) -> list[str]:
    errors: list[str] = []
    text = _clean(soup.get_text(" ", strip=True))
    if "휴대폰 본인인증" not in text:
        errors.append("mobile-auth public explanation changed")
    next_forms = soup.select("form#nextForm[name='nextForm']")
    if len(next_forms) != 1:
        errors.append("mobile-auth next form missing or duplicated")
    else:
        form = next_forms[0]
        action = urlparse(urljoin(PYEONGCHANG_CANONICAL_URL, _clean(form.get("action"))))
        if _clean(form.get("method")).lower() != "post" or (
            action.scheme,
            action.hostname,
            action.path,
            action.query,
        ) != ("https", PYEONGCHANG_HOST, PYEONGCHANG_LIST_PATH, ""):
            errors.append("mobile-auth next form action changed")
        count, value = _form_value(form, "courseNo")
        if count != 1 or value != identity:
            errors.append("mobile-auth course identity mismatch")
    request_forms = soup.select("form[name='reqPCCForm']")
    if len(request_forms) != 1 or len(request_forms[0].select("input[name='reqInfo']")) != 1:
        errors.append("mobile-auth provider request contract changed")
    controls = soup.select("a.btn-self-certification")
    if len(controls) != 1 or _clean(controls[0].get_text(" ", strip=True)) != "인증하기":
        errors.append("mobile-auth control changed")
    return errors


def _fetch_validate_auth_many(
    rows: Iterable[dict[str, Any]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[str, list[str]], list[str], int]:
    tasks = list(rows)
    if not tasks:
        return {}, [], 0

    def worker(row: dict[str, Any]) -> tuple[str, list[str]]:
        identity = _clean(row["raw_fields"]["identity"])
        last_error: Optional[Exception] = None
        for _attempt in range(PYEONGCHANG_FETCH_ATTEMPTS):
            session = session_factory()
            try:
                application = _coerce_soup(
                    fetcher(session, _clean(row["application_url"]), timeout)
                )
                frame_url, errors = _validate_application_page(application, identity)
                if not frame_url:
                    return identity, errors
                auth = _coerce_soup(fetcher(session, frame_url, timeout))
                errors.extend(_validate_auth_iframe(auth, identity))
                return identity, errors
            except Exception as exc:
                last_error = exc
            finally:
                _close_quietly(session)
        raise RuntimeError(_clean(last_error))

    results: dict[str, list[str]] = {}
    fetch_errors: list[str] = []
    pages = 0
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        futures = {executor.submit(worker, row): row for row in tasks}
        for future in as_completed(futures):
            row = futures[future]
            identity = _clean(row["raw_fields"]["identity"])
            try:
                result_identity, errors = future.result()
                results[result_identity] = errors
                pages += 2
            except Exception as exc:
                fetch_errors.append(f"course {identity} auth: {_clean(exc)}")
    return results, fetch_errors, pages


def _row_identity(row: Mapping[str, Any]) -> str:
    if row.get("external_reference"):
        return f"external:{_clean(row.get('raw_url'))}"
    return _clean(row.get("raw_fields", {}).get("identity"))


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            _row_identity(row),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(
                row.get("source_status")
                if row.get("external_reference")
                else row.get("raw_fields", {}).get("source_status")
            ),
        )
        for row in rows
    )


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/auth/detail keys persisted")
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
        "auth_attempts": 0,
        "auth_pages": 0,
        "auth_verified": 0,
        "auth_errors": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "configured_collection_error": error,
    }


def collect_pyeongchang_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = PYEONGCHANG_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Pyeongchang education snapshot."""

    meta = _base_meta()
    if not is_pyeongchang_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Pyeongchang owner"
        )
        return [], PYEONGCHANG_PARSER, meta
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
        return [], PYEONGCHANG_PARSER, meta
    try:
        cutoff = _today(today)
    except ValueError as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], PYEONGCHANG_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    errors: list[str] = []

    initial, fetch_errors = _fetch_parse_many(
        [
            (
                ("list", 1, "data"),
                pyeongchang_list_url(1),
                lambda soup: _parse_list(soup, 1, cutoff),
            )
        ],
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(initial)
    meta["list_requests"] += len(initial)
    first = initial.get(("list", 1, "data"))
    if not isinstance(first, _ListPage):
        errors.append("page 1: response missing")
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], PYEONGCHANG_PARSER, meta
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
        return [], PYEONGCHANG_PARSER, meta
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], PYEONGCHANG_PARSER, meta

    items: list[tuple[Any, str, Callable[[BeautifulSoup], Any]]] = []
    for page in range(2, last + 1):
        items.append(
            (
                ("list", page, "data"),
                pyeongchang_list_url(page),
                lambda soup, current_page=page: _parse_list(
                    soup, current_page, cutoff
                ),
            )
        )
    items.extend(
        [
            (
                ("list", last + 1, "sentinel"),
                pyeongchang_list_url(last + 1),
                lambda soup, current_page=last + 1: _parse_list(
                    soup, current_page, cutoff
                ),
            ),
            (
                ("list", 1, "recheck"),
                pyeongchang_list_url(1),
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
        if (parsed.total, parsed.current_page, parsed.last) != (total, page, last):
            errors.append(f"page {page}: total/page/last changed")
        expected = (
            PYEONGCHANG_PAGE_SIZE
            if page < last
            else total - (last - 1) * PYEONGCHANG_PAGE_SIZE
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
        if (sentinel.total, sentinel.current_page, sentinel.last) != (
            total,
            last + 1,
            last,
        ):
            errors.append(f"page {last + 1}: sentinel boundary changed")
        if sentinel.rows:
            errors.append(f"page {last + 1}: sentinel is not empty")
    recheck = remaining.get(("list", 1, "recheck"))
    if not isinstance(recheck, _ListPage):
        errors.append("page 1: stability response missing")
    else:
        errors.extend(recheck.errors)
        if (
            (recheck.total, recheck.current_page, recheck.last) != (total, 1, last)
            or _page_signature(recheck.rows) != signatures.get(1, ())
        ):
            errors.append("page-one stability recheck changed")

    source_identities = [_row_identity(row) for row in all_rows]
    identity_duplicate_count = len(source_identities) - len(set(source_identities))
    if identity_duplicate_count:
        errors.append(f"{identity_duplicate_count} duplicate source identities")
    external_rows = [row for row in all_rows if row.get("external_reference")]
    internal_rows = [row for row in all_rows if not row.get("external_reference")]
    if len(external_rows) != 1:
        errors.append(
            f"external library boundary changed from 1 to {len(external_rows)}"
        )
    current_external = [
        row
        for row in external_rows
        if date.fromisoformat(_clean(row["end_date"])) >= cutoff
    ]
    current_rows = [
        row
        for row in internal_rows
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

    online_open_rows = [
        row
        for row in detailed_rows
        if row["status"] == "OPEN"
        and row["application_type"] == "ONLINE_RESERVATION"
    ]
    auth_errors: list[str] = []
    if details_complete:
        meta["auth_attempts"] = len(online_open_rows)
        auth_results, auth_fetch_errors, auth_pages = _fetch_validate_auth_many(
            online_open_rows,
            fetcher=current_fetcher,
            session_factory=factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        meta["pages"] += auth_pages
        meta["auth_pages"] = auth_pages
        auth_errors.extend(auth_fetch_errors)
        for row in online_open_rows:
            identity = _clean(row["raw_fields"]["identity"])
            item_errors = auth_results.get(identity)
            if item_errors is None:
                auth_errors.append(f"course {identity}: auth response missing")
            elif item_errors:
                auth_errors.extend(
                    f"course {identity}: {item}" for item in item_errors
                )
            else:
                row["raw_fields"]["mobile_auth_verified"] = True
                row["raw_fields"]["application_control_verified"] = True
                meta["auth_verified"] += 1
    errors.extend(auth_errors)
    meta["auth_errors"] = len(auth_errors)
    application_controls_complete = bool(
        details_complete
        and meta["auth_attempts"] == len(online_open_rows)
        and meta["auth_verified"] == len(online_open_rows)
        and not auth_errors
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
    semantic_counter = Counter(
        (
            _normalized(row["title"]),
            _clean(row["start_date"]),
            _clean(row["end_date"]),
        )
        for row in internal_rows
    )
    meta.update(
        {
            "ownership_scope": PYEONGCHANG_OWNERSHIP_SCOPE,
            "canonical_url": PYEONGCHANG_CANONICAL_URL,
            "page_counts": page_counts,
            "source_rows": len(all_rows),
            "internal_source_count": len(internal_rows),
            "external_reference_count": len(external_rows),
            "external_reference_urls": [
                _clean(row["raw_url"]) for row in external_rows
            ],
            "external_current_count": len(current_external),
            "current_source_count": len(current_rows),
            "expired_internal_count": len(internal_rows) - len(current_rows),
            "identity_duplicate_count": identity_duplicate_count,
            "semantic_duplicate_group_count": sum(
                count > 1 for count in semantic_counter.values()
            ),
            "semantic_duplicate_excess_rows": sum(
                max(0, count - 1) for count in semantic_counter.values()
            ),
            "semantic_duplicate_policy": "preserve_distinct_official_GJLI_identity",
            "branch_counts": dict(Counter(_clean(row["branch"]) for row in result)),
            "status_counts": dict(Counter(_clean(row["status"]) for row in result)),
            "source_status_counts": dict(
                Counter(
                    _clean(
                        row.get("source_status")
                        if row.get("external_reference")
                        else row["raw_fields"]["source_status"]
                    )
                    for row in all_rows
                )
            ),
            "application_method_counts": dict(
                Counter(_clean(row["application_method"]) for row in result)
            ),
            "online_open_count": sum(
                row.get("reservation_available") is True
                and row.get("application_type") == "ONLINE_RESERVATION"
                for row in result
            ),
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "application_controls_complete": application_controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "the complete internal catalogue has no current/future courses"
                if snapshot_complete and not current_rows
                else ""
            ),
            "municipality_coverage": [PYEONGCHANG_MUNICIPALITY_CODE],
            "candidate_audit": {
                key: dict(value)
                for key, value in PYEONGCHANG_CANDIDATE_AUDIT.items()
            },
            "discovery_audit": dict(PYEONGCHANG_DISCOVERY_AUDIT),
            "pii_fields_discarded": list(PYEONGCHANG_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, PYEONGCHANG_PARSER, meta


collect = collect_pyeongchang_education


__all__ = [
    "PYEONGCHANG_AUTH_MENU_PATH",
    "PYEONGCHANG_AUTH_PATH",
    "PYEONGCHANG_CANONICAL_CANDIDATE_ID",
    "PYEONGCHANG_CANONICAL_URL",
    "PYEONGCHANG_CANDIDATE_AUDIT",
    "PYEONGCHANG_DEPRECATED_FACILITY_URL",
    "PYEONGCHANG_DISCOVERY_ALIAS_URLS",
    "PYEONGCHANG_DISCOVERY_AUDIT",
    "PYEONGCHANG_EXCLUDED_CANDIDATE_IDS",
    "PYEONGCHANG_EXTERNAL_LIBRARY_TITLE",
    "PYEONGCHANG_EXTERNAL_LIBRARY_URL",
    "PYEONGCHANG_GENERAL_HOMEPAGE_URL",
    "PYEONGCHANG_GUNBO_PORTAL_URL",
    "PYEONGCHANG_HOST",
    "PYEONGCHANG_LIST_PATH",
    "PYEONGCHANG_MUNICIPALITY_CODE",
    "PYEONGCHANG_MUNICIPALITY_NAME",
    "PYEONGCHANG_PAGE_SIZE",
    "PYEONGCHANG_PARSER",
    "PYEONGCHANG_PII_FIELDS_DISCARDED",
    "PYEONGCHANG_PROVIDER",
    "PYEONGCHANG_VOUCHER_NOTICE_URL",
    "PyeongchangContractError",
    "collect",
    "collect_pyeongchang_education",
    "is_pyeongchang_discovery_alias_target",
    "is_pyeongchang_education_target",
    "is_pyeongchang_excluded_candidate",
    "is_pyeongchang_separate_facility_target",
    "pyeongchang_application_url",
    "pyeongchang_detail_url",
    "pyeongchang_list_url",
]
