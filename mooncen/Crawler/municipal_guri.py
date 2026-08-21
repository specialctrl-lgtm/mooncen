"""Fail-closed collectors for Guri City's two distinct education catalogues.

The branded GSEEK portal owns the lifelong-learning catalogue.  Its landing
page declares the total while a JSON endpoint on ``www.gseek.kr`` serves nine
rows per range request.  Separately, Guri's municipal reservation portal owns
17 internal education sources, including all eight resident centres.  The old
lifelong menu is only the stable aggregate anchor and migration notice; it is
not treated as a one-row course source.  Both collectors prove their complete
inventory, read every page plus boundary sentinels, and validate every current
or future detail before publishing an all-or-nothing snapshot.

The module intentionally stays independent from ``Crawler_MunicipalYaml`` so
the shared router can import it without a cycle.  Production callers must
inject that router's managed ``session`` factory and ``dedupe_rows`` helper.
No request disables certificate verification.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GURI_GSEEK_PROVIDER = "MUNI_GURI_GSEEK_KR_2E5F409F"
GURI_GSEEK_URL = "https://guri.gseek.kr/user/course/offline/list"
GURI_GSEEK_HOST = "guri.gseek.kr"
GURI_GSEEK_LIST_PATH = "/user/course/offline/list"
GURI_GSEEK_DETAIL_PATH = "/user/course/offline/view"
GURI_GSEEK_API_URL = "https://www.gseek.kr/user/course/offline/list/search"
GURI_GSEEK_API_HOST = "www.gseek.kr"
GURI_GSEEK_REGION_CODE = "4131000000"
GURI_GSEEK_CO_SPONSOR_ID = "G000008"
GURI_GSEEK_PAGE_SIZE = 9
GURI_GSEEK_PARSER = "guri_gseek_complete_ranges+sentinel+current_detail"
GURI_MUNICIPALITY_CODE = "4131000000"
GURI_MUNICIPALITY_NAME = "경기도 구리시"

# This page is an expired one-row migration notice, not a second catalogue.
GURI_LEGACY_RESERVE_PROVIDER = "MUNI_WWW_GURI_GO_KR_E0C65498"
GURI_LEGACY_RESERVE_URL = (
    "https://www.guri.go.kr/reserve/selectGuriUserCourseList.do?"
    "key=3861&searchEduInstSe=INSTSE01"
)
GURI_RESERVE_PROVIDER = GURI_LEGACY_RESERVE_PROVIDER
GURI_RESERVE_URL = GURI_LEGACY_RESERVE_URL
GURI_RESERVE_HOST = "www.guri.go.kr"
GURI_RESERVE_LIST_PATH = "/reserve/selectGuriUserCourseList.do"
GURI_RESERVE_DETAIL_PATH = "/reserve/selectGuriUserCourseView.do"
GURI_RESERVE_APPLICATION_PATH = "/reserve/addGuriUserCourseRegistView.do"
GURI_RESERVE_PAGE_SIZE = 15
GURI_RESERVE_PARSER = (
    "guri_reserve_complete_source_inventory+pages+sentinels+current_detail"
)
# The official list and detail both publish an impossible 2025 end year for
# this 2026 Q3, three-month programme.  Correct it only while the exact raw
# fingerprint remains unchanged; any upstream edit falls back to fail-closed.
GURI_RESERVE_DATE_CORRECTIONS: Mapping[str, tuple[str, str, str]] = {
    "2046": ("2026-07-01", "2025-09-30", "2026-09-30"),
}


@dataclass(frozen=True)
class GuriReserveSource:
    code: str
    name: str
    menu_key: str
    institution_code: str
    education_key: str = ""


GURI_RESERVE_SOURCES: tuple[GuriReserveSource, ...] = (
    GuriReserveSource("resident_galmae", "갈매동 주민자치센터", "3863", "INSTSE02", "5"),
    GuriReserveSource("resident_donggu", "동구동 주민자치센터", "3870", "INSTSE02", "7"),
    GuriReserveSource("resident_inchang", "인창동 주민자치센터", "3871", "INSTSE02", "8"),
    GuriReserveSource("resident_gyomun1", "교문1동 주민자치센터", "3872", "INSTSE02", "9"),
    GuriReserveSource("resident_gyomun2", "교문2동 주민자치센터", "3873", "INSTSE02", "10"),
    GuriReserveSource("resident_sutaek1", "수택1동 주민자치센터", "3874", "INSTSE02", "11"),
    GuriReserveSource("resident_sutaek2", "수택2동 주민자치센터", "3875", "INSTSE02", "12"),
    GuriReserveSource("resident_sutaek3", "수택3동 주민자치센터", "3876", "INSTSE02", "13"),
    GuriReserveSource("jangja_ecology", "장자호수생태체험관", "3865", "INSTSE08"),
    GuriReserveSource("youth_sexuality", "구리시 청소년성문화센터", "3867", "INSTSE05"),
    GuriReserveSource("women_happiness", "여성행복센터", "3868", "INSTSE06"),
    GuriReserveSource("public_health", "보건소", "3912", "INSTSE09"),
    GuriReserveSource("sutaek_health", "수택건강생활지원센터", "8602", "INSTSE17"),
    GuriReserveSource("insect_ecology", "곤충생태관", "3864", "INSTSE03"),
    GuriReserveSource("resource_circulation", "구리시자원순환교육센터", "6136", "INSTSE12"),
    GuriReserveSource("future_education", "구리미래교육협력지구", "6158", "INSTSE13", "24"),
    GuriReserveSource("pet_care", "반려돌봄센터", "6596", "INSTSE15"),
)

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d+")
_LANDING_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*개의\s*강좌")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_OPEN_SOURCE_STATUSES = frozenset({"모집중", "마감임박", "대기접수", "추가접수"})
_STATUS_MAP: Mapping[str, str] = {
    "모집중": "OPEN",
    "마감임박": "OPEN",
    "대기접수": "OPEN",
    "추가접수": "OPEN",
    "모집예정": "SCHEDULED",
    "마감": "CLOSED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).lower())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _branch_default(target: Any) -> str:
    return _clean(_target_value(target, "branch")) or GURI_MUNICIPALITY_NAME


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_guri_gseek_target(target: Any) -> bool:
    """Accept only the exact provider-owned branded GSEEK landing route."""

    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == GURI_GSEEK_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GURI_GSEEK_HOST
        and parsed.port is None
        and parsed.path == GURI_GSEEK_LIST_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_guri_gseek_target


def is_guri_legacy_redirect_target(target: Any) -> bool:
    """Identify the retired integrated-reservation migration notice exactly."""

    return (
        _provider(target) == GURI_LEGACY_RESERVE_PROVIDER
        and _target_url(target) == GURI_LEGACY_RESERVE_URL
    )


def is_guri_reserve_target(target: Any) -> bool:
    """Use the retired lifelong page as the exact aggregate portal anchor."""

    return is_guri_legacy_redirect_target(target)


def guri_reserve_list_url(source: GuriReserveSource, page: Any = 1) -> str:
    raw_page = _clean(page)
    if source not in GURI_RESERVE_SOURCES:
        return ""
    if not _IDENTITY_RE.fullmatch(raw_page) or int(raw_page) < 1:
        return ""
    query: list[tuple[str, Any]] = [
        ("key", source.menu_key),
        ("searchEduInstSe", source.institution_code),
    ]
    if source.education_key:
        query.append(("searchEduKey", source.education_key))
    query.extend((("pageUnit", GURI_RESERVE_PAGE_SIZE), ("pageIndex", int(raw_page))))
    return f"https://{GURI_RESERVE_HOST}{GURI_RESERVE_LIST_PATH}?" + urlencode(query)


def guri_reserve_detail_url(source: GuriReserveSource, identity: Any) -> str:
    raw_identity = _clean(identity)
    if source not in GURI_RESERVE_SOURCES or not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    query: list[tuple[str, Any]] = [
        ("key", source.menu_key),
        ("searchEduInstSe", source.institution_code),
    ]
    if source.education_key:
        query.append(("searchEduKey", source.education_key))
    query.append(("lctreRcritKey", raw_identity))
    return f"https://{GURI_RESERVE_HOST}{GURI_RESERVE_DETAIL_PATH}?" + urlencode(query)


def guri_gseek_detail_url(subject_id: Any, cycle_id: Any) -> str:
    subject = _clean(subject_id)
    cycle = _clean(cycle_id)
    if not _IDENTITY_RE.fullmatch(subject) or not _IDENTITY_RE.fullmatch(cycle):
        return ""
    return f"https://{GURI_GSEEK_HOST}{GURI_GSEEK_DETAIL_PATH}?" + urlencode(
        {"s_sbjct_sn": subject, "s_sbjct_cycl_sn": cycle}
    )


def guri_gseek_api_range(page: Any) -> tuple[int, int]:
    raw_page = _clean(page)
    if not _IDENTITY_RE.fullmatch(raw_page) or int(raw_page) < 1:
        return (0, 0)
    start = (int(raw_page) - 1) * GURI_GSEEK_PAGE_SIZE + 1
    return start, start + GURI_GSEEK_PAGE_SIZE


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_status(response: Any) -> int:
    try:
        return int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        return 0


def _response_soup(response: Any) -> BeautifulSoup:
    status = _response_status(response)
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError("HTTP redirects are not accepted")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _response_json(response: Any) -> list[Any]:
    status = _response_status(response)
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError("HTTP redirects are not accepted")
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("GSEEK API response is not a JSON list")
    return payload


def _get_soup(current: Any, url: str, timeout: int) -> BeautifulSoup:
    return _response_soup(
        current.get(url, timeout=timeout, allow_redirects=False)
    )


def _post_api_page(current: Any, page: int, timeout: int) -> list[Any]:
    start, end = guri_gseek_api_range(page)
    if not start:
        raise ValueError("invalid GSEEK API page")
    response = current.post(
        GURI_GSEEK_API_URL,
        data={
            "s_sort_by": "1",
            "s_row_start": str(start),
            "s_row_end": str(end),
            "resion": GURI_GSEEK_REGION_CODE,
        },
        timeout=timeout,
        allow_redirects=False,
        headers={
            "Referer": GURI_GSEEK_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    return _response_json(response)


def _with_retry(operation: Callable[[], Any]) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.25)
    assert last_error is not None
    raise last_error


def _landing_total(soup: BeautifulSoup) -> Optional[int]:
    matches = _LANDING_TOTAL_RE.findall(_clean(soup.get_text(" ", strip=True)))
    values = {int(value.replace(",", "")) for value in matches}
    if len(values) != 1:
        return None
    return values.pop()


def _integer(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    if not raw or not re.fullmatch(r"\d+", raw):
        return None
    return int(raw)


def _source_date(value: Any) -> Optional[date]:
    match = _DATE_RE.fullmatch(_clean(value))
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for parts in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(*(int(part) for part in parts)))
        except ValueError:
            continue
    return result


def _date_range(value: Any) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if len(values) != 2 or values[1] < values[0]:
        return "", "", ""
    start, end = values
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _money(value: Any) -> str:
    amount = _integer(value)
    if amount is None:
        return _clean(value)
    return "무료" if amount == 0 else f"{amount:,}원"


def _branch_code(branch: Any) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"GURI_GSEEK_BRANCH_{digest}"


def _pairs(container: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if container is None:
        return result
    for dl in container.select("dl"):
        pending = ""
        for node in dl.find_all(["dt", "dd"], recursive=False):
            if node.name == "dt":
                pending = _clean(node.get_text(" ", strip=True))
            elif pending:
                result[pending] = _clean(node.get_text(" ", strip=True))
                pending = ""
    return result


def _api_row(target: Any, item: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    subject = _clean(item.get("d_sbjct_sn"))
    cycle = _clean(item.get("d_sbjct_cycl_sn"))
    title = _clean(item.get("d_sbjct_nm"))
    branch = _clean(item.get("d_edu_gvmnfc"))
    source_status = _clean(item.get("d_recrut_stts_nm"))
    start = _source_date(item.get("d_edu_bgng_dt"))
    end = _source_date(item.get("d_edu_end_dt"))
    identity = f"{subject}:{cycle}"

    if not _IDENTITY_RE.fullmatch(subject) or not _IDENTITY_RE.fullmatch(cycle):
        errors.append("non-numeric subject/cycle identity")
    if not title:
        errors.append(f"course {identity}: empty title")
    if not branch:
        errors.append(f"course {identity}: empty education institution")
    if _clean(item.get("d_rgn")) != "구리시":
        errors.append(f"course {identity}: non-Guri region")
    if _clean(item.get("d_co_sprvsn_id")) != GURI_GSEEK_CO_SPONSOR_ID:
        errors.append(f"course {identity}: non-Guri co-sponsor")
    if _clean(item.get("d_sbjct_type_cd_id")) != "OF":
        errors.append(f"course {identity}: not an offline education course")
    if source_status not in _STATUS_MAP:
        errors.append(f"course {identity}: unknown recruitment status")
    if start is None or end is None or end < start:
        errors.append(f"course {identity}: invalid education date range")

    raw_url = guri_gseek_detail_url(subject, cycle)
    category = " > ".join(
        value
        for value in (
            _clean(item.get("d_clsf_depth1_nm")),
            _clean(item.get("d_clsf_depth2_nm")),
            _clean(item.get("d_clsf_depth3_nm")),
        )
        if value
    )
    weekday = _clean(item.get("d_edu_wday_cd_nm"))
    start_time = _clean(item.get("d_edu_start_time"))
    end_time = _clean(item.get("d_edu_end_time"))
    schedule = " ".join(
        value
        for value in (
            f"매주 {weekday}" if weekday else "",
            f"{start_time} ~ {end_time}" if start_time and end_time else start_time or end_time,
        )
        if value
    )
    capacity_total = _integer(item.get("d_edu_nope"))
    capacity_current = _integer(item.get("d_aply_cnt"))
    status = _STATUS_MAP.get(source_status, "")
    period = f"{start.isoformat()} ~ {end.isoformat()}" if start and end else ""
    row: dict[str, Any] = {
        "provider": GURI_GSEEK_PROVIDER,
        "provider_course_id": f"{GURI_GSEEK_PROVIDER}:course:{identity}",
        "title": title,
        "branch": branch or _branch_default(target),
        "branch_code": _branch_code(branch or _branch_default(target)),
        "provider_organizer": branch,
        "category": category or "평생학습",
        "raw_url": raw_url,
        "status": status,
        "period": period,
        "start_date": start.isoformat() if start else "",
        "end_date": end.isoformat() if end else "",
        "schedule_raw": schedule,
        "target": _clean(item.get("d_sbjct_trgt_nm_1")),
        "fee": _money(item.get("d_sbjct_amt")),
        "material_fee": _money(item.get("d_prepar_cmdty_amt")),
        "capacity": capacity_total,
        "capacity_total": capacity_total,
        "capacity_current": capacity_current,
        "description": _clean(item.get("d_sbjct_intrd_cn")) or title,
        "application_method_raw": _clean(item.get("d_stdnt_chice_mthd_cd_nm")),
        "collection_category": "교육",
        "domain_category": "평생학습",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_integrated_reservation",
        "collection_type": "json_api+detail_html",
        "program_type": "강좌",
        "region": GURI_MUNICIPALITY_NAME,
        "reservation_available": False,
        "raw_fields": {
            "parser": GURI_GSEEK_PARSER,
            "subject_id": subject,
            "cycle_id": cycle,
            "source_status": source_status,
            "source_branch": branch,
            "source_region": _clean(item.get("d_rgn")),
            "co_sponsor_id": _clean(item.get("d_co_sprvsn_id")),
            "source_item": dict(item),
        },
    }
    return _clean_row(row), errors


def _detail_contract(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    raw_fields = row.get("raw_fields", {})
    subject = _clean(raw_fields.get("subject_id"))
    cycle = _clean(raw_fields.get("cycle_id"))
    identity = f"{subject}:{cycle}"
    container = soup.select_one("div.course-detail-container")
    if container is None:
        return [f"course {identity}: missing detail container"]

    def input_value(name: str) -> str:
        node = soup.select_one(f"input[name='{name}']")
        return _clean(node.get("value")) if node is not None else ""

    if input_value("s_sbjct_sn") != subject or input_value("s_sbjct_cycl_sn") != cycle:
        errors.append(f"course {identity}: detail identity mismatch")

    title_node = container.select_one("h2.course-title")
    detail_title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
    if _normalized(detail_title) != _normalized(row.get("title")):
        errors.append(f"course {identity}: detail/list title mismatch")

    branch_nodes = container.select("section.key-course-info .tag-field")
    detail_branches = [_clean(node.get_text(" ", strip=True)) for node in branch_nodes]
    if _clean(raw_fields.get("source_branch")) not in detail_branches:
        errors.append(f"course {identity}: detail/list branch mismatch")

    status_node = container.select_one("section.key-course-info .tag-item-xs")
    detail_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
    source_status = _clean(raw_fields.get("source_status"))
    if detail_status != source_status:
        errors.append(f"course {identity}: detail/list status mismatch")

    pairs = _pairs(container.select_one("section.key-course-info"))
    detail_start, detail_end, detail_period = _date_range(pairs.get("학습기간"))
    if detail_period != _clean(row.get("period")):
        errors.append(f"course {identity}: detail/list education period mismatch")
    # GSEEK exposes a single ``신청기간`` for ordinary programmes, while the
    # learning-centre catalogue splits it into ``우선신청기간`` and
    # ``일반신청기간``.  The general window is the canonical apply period; the
    # priority window is retained separately when present.
    apply_source = pairs.get("신청기간") or pairs.get("일반신청기간")
    apply_start, apply_end, apply_period = _date_range(apply_source)
    if not apply_period:
        errors.append(f"course {identity}: missing detail application period")
    priority_apply_start, priority_apply_end, priority_apply_period = _date_range(
        pairs.get("우선신청기간")
    )
    if pairs.get("우선신청기간") and not priority_apply_period:
        errors.append(f"course {identity}: malformed priority application period")

    detail_schedule = _clean(pairs.get("교육시간"))
    for token in (
        _clean(raw_fields.get("source_item", {}).get("d_edu_start_time")),
        _clean(raw_fields.get("source_item", {}).get("d_edu_end_time")),
    ):
        if token and token not in detail_schedule:
            errors.append(f"course {identity}: detail/list education time mismatch")
            break

    detail_capacity = _integer(re.sub(r"[^\d,]", "", pairs.get("모집인원", "")))
    if row.get("capacity_total") is not None and detail_capacity != row.get("capacity_total"):
        errors.append(f"course {identity}: detail/list capacity mismatch")

    expected_return = urlparse(_clean(row.get("raw_url"))).path + "?" + urlparse(
        _clean(row.get("raw_url"))
    ).query
    if input_value("p_return_url") != expected_return:
        errors.append(f"course {identity}: malformed login return URL")

    markup = str(soup)
    if "/user/course/cert/checkCi" not in markup or "/user/course/aply" not in markup:
        errors.append(f"course {identity}: missing canonical application flow")
    apply_control = container.select_one(".btn-course-box .btn-course-apply")
    if apply_control is None:
        errors.append(f"course {identity}: missing application control")
    disabled = bool(
        apply_control is not None and "disabled" in (apply_control.get("class") or [])
    )
    is_open = source_status in _OPEN_SOURCE_STATUSES
    if disabled == is_open:
        errors.append(f"course {identity}: status/application control mismatch")

    venue = _clean(pairs.get("교육장소"))
    if not venue:
        errors.append(f"course {identity}: missing education venue")

    description_node = container.select_one(".course-desc")
    description = (
        _clean(description_node.get_text(" ", strip=True))
        if description_node is not None
        else ""
    )
    row.update(
        {
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "priority_apply_period": priority_apply_period,
            "priority_apply_start_date": priority_apply_start,
            "priority_apply_end_date": priority_apply_end,
            "schedule_raw": detail_schedule or row.get("schedule_raw"),
            "target": _clean(pairs.get("교육대상")) or row.get("target"),
            "venue_name": venue,
            "venue_address": venue,
            "room": venue,
            "description": description or row.get("description"),
            "reservation_available": bool(is_open and not disabled),
        }
    )
    if is_open and not disabled:
        row["application_url"] = _clean(row.get("raw_url"))
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        raw_fields["clear_application_url"] = True
    raw_fields.update(
        {
            "detail_pairs": pairs,
            "detail_status": detail_status,
            "detail_application_control": bool(apply_control),
            "detail_application_disabled": disabled,
            "detail_start": detail_start,
            "detail_end": detail_end,
            "detail_apply_start": apply_start,
            "detail_apply_end": apply_end,
            "detail_priority_apply_start": priority_apply_start,
            "detail_priority_apply_end": priority_apply_end,
        }
    )
    return errors


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "main_discovery_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_guri_gseek_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 30,
    detail_limit: int = 100,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete current/future snapshot of Guri-owned GSEEK courses."""

    if not is_guri_gseek_target(target):
        return [], GURI_GSEEK_PARSER, _failure(
            "target does not match the canonical Guri GSEEK provider route"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], GURI_GSEEK_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    errors: list[str] = []
    source_cap_reached = False
    current: Any = None
    landing_soup: Optional[BeautifulSoup] = None
    page_payloads: dict[int, list[Any]] = {}
    rows: list[dict[str, Any]] = []
    detail_pages = 0
    total = 0
    data_pages = 0
    required_list_requests = 0
    try:
        current = session_factory()
        headers = getattr(current, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                }
            )
        try:
            landing_soup = _with_retry(
                lambda: _get_soup(current, GURI_GSEEK_URL, timeout)
            )
        except Exception as exc:
            errors.append(f"landing page: fetch {type(exc).__name__}")

        if landing_soup is not None:
            declared = _landing_total(landing_soup)
            if declared is None:
                errors.append("landing page: missing unambiguous catalogue total")
            else:
                total = declared
                data_pages = math.ceil(total / GURI_GSEEK_PAGE_SIZE)
                required_list_requests = data_pages + 1
                if required_list_requests > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap allows {allowed_pages} of "
                        f"{required_list_requests} required API range requests"
                    )

        if not errors:
            for page in range(1, required_list_requests + 1):
                try:
                    page_payloads[page] = _with_retry(
                        lambda page=page: _post_api_page(current, page, timeout)
                    )
                except Exception as exc:
                    errors.append(f"API range {page}: fetch {type(exc).__name__}")
                    break

        if not errors:
            for page in range(1, data_pages + 1):
                payload = page_payloads.get(page, [])
                expected = min(
                    GURI_GSEEK_PAGE_SIZE,
                    total - (page - 1) * GURI_GSEEK_PAGE_SIZE,
                )
                if len(payload) != expected:
                    errors.append(
                        f"API range {page}: expected {expected} rows, got {len(payload)}"
                    )
                for item in payload:
                    if not isinstance(item, Mapping):
                        errors.append(f"API range {page}: non-object course row")
                        continue
                    item_total = _integer(item.get("d_total_cnt"))
                    if item_total != total:
                        errors.append(f"API range {page}: catalogue total changed")
                    row, row_errors = _api_row(target, item)
                    rows.append(row)
                    errors.extend(row_errors)
            if page_payloads.get(required_list_requests):
                errors.append("API sentinel range after declared total is not empty")
            if len(rows) != total:
                errors.append(f"declared total {total} != parsed rows {len(rows)}")

        identities = [_clean(row.get("provider_course_id")) for row in rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate provider course identities")
        urls = [_clean(row.get("raw_url")) for row in rows]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate canonical course URLs")

        current_rows: list[dict[str, Any]] = []
        expired_count = 0
        for row in rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(
                    f"{_clean(row.get('provider_course_id'))}: invalid end date"
                )
                continue
            if end < cutoff:
                expired_count += 1
            else:
                current_rows.append(row)

        semantic_signatures = [
            (
                _normalized(row.get("title")),
                _normalized(row.get("branch")),
                _clean(row.get("period")),
                _normalized(row.get("schedule_raw")),
            )
            for row in current_rows
        ]
        semantic_duplicate_count = len(semantic_signatures) - len(
            set(semantic_signatures)
        )
        if semantic_duplicate_count:
            errors.append(
                f"{semantic_duplicate_count} duplicate current semantic course signatures"
            )

        if len(current_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_rows)} required current/future details"
            )

        detail_errors = 0
        if not errors:
            for row in current_rows:
                try:
                    soup = _with_retry(
                        lambda row=row: _get_soup(
                            current, _clean(row.get("raw_url")), timeout
                        )
                    )
                    detail_pages += 1
                    row_errors = _detail_contract(row, soup)
                    detail_errors += len(row_errors)
                    errors.extend(row_errors)
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: "
                        f"detail fetch {type(exc).__name__}"
                    )

        result: list[dict[str, Any]] = []
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in current_rows]))
            if len(result) != len(current_rows):
                errors.append(
                    f"dedupe changed complete row count {len(current_rows)} to {len(result)}"
                )
                result = []

        branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        status_counts = Counter(_clean(row.get("status")) for row in current_rows)
        snapshot_complete = not errors
        meta = {
            "pages": len(page_payloads),
            "main_discovery_pages": 1 if landing_soup is not None else 0,
            "list_requests": len(page_payloads),
            "detail_pages": detail_pages,
            "source_total": total,
            "source_rows": len(rows),
            "data_pages": data_pages,
            "sentinel_page": required_list_requests,
            "page_counts": {
                page: len(payload) for page, payload in page_payloads.items()
            },
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_duplicate_count": semantic_duplicate_count,
            "detail_errors": detail_errors,
            "discovered_links": len(rows),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "pagination_detected": data_pages > 1,
            "pagination_complete": bool(
                snapshot_complete
                and len(page_payloads) == required_list_requests
                and not page_payloads.get(required_list_requests)
            ),
            "details_complete": bool(
                snapshot_complete and detail_pages == len(current_rows)
            ),
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "all complete Guri GSEEK catalogue courses have ended"
                if snapshot_complete and not current_rows
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
            "ownership_region_code": GURI_GSEEK_REGION_CODE,
            "ownership_co_sponsor_id": GURI_GSEEK_CO_SPONSOR_ID,
        }
        if errors:
            return [], GURI_GSEEK_PARSER, meta
        return result, GURI_GSEEK_PARSER, meta
    finally:
        _close_quietly(current)


def _reserve_source_signature(source: GuriReserveSource) -> tuple[str, str, str]:
    return source.menu_key, source.institution_code, source.education_key


def _reserve_inventory(soup: BeautifulSoup) -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for anchor in soup.select(
        "a.depth_text[href], a.tab_anchor[href], a.tab_depth2_anchor[href]"
    ):
        parsed = urlparse(anchor.get("href") or "")
        if parsed.path != GURI_RESERVE_LIST_PATH:
            continue
        query = parse_qs(parsed.query, keep_blank_values=True)
        key = _clean((query.get("key") or [""])[0])
        institution = _clean((query.get("searchEduInstSe") or [""])[0])
        education_key = _clean((query.get("searchEduKey") or [""])[0])
        if not key or not institution or institution == "INSTSE01":
            continue
        result.add((key, institution, education_key))
    return result


def _reserve_summary(soup: BeautifulSoup) -> Optional[tuple[int, int, int]]:
    text = _clean(soup.get_text(" ", strip=True))
    matches = re.findall(
        r"총게시물\s*:\s*([\d,]+)\s*건\s*페이지\s*:\s*(\d+)\s*/\s*(\d+)",
        text,
    )
    values = {
        (int(total.replace(",", "")), int(current), int(last))
        for total, current, last in matches
    }
    if len(values) != 1:
        return None
    return values.pop()


def _reserve_status(value: Any) -> str:
    source = _clean(value)
    if source in {"접수중", "대기자접수"}:
        return "OPEN"
    if source == "접수예정":
        return "SCHEDULED"
    if source == "접수마감":
        return "CLOSED"
    return ""


def _reserve_capacity(value: Any) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    text = _clean(value)
    main = re.search(r"신청\s*(\d+)\s*명\s*/\s*모집정원\s*(\d+)\s*명", text)
    if not main:
        main = re.search(r"(\d+)\s*/\s*(\d+)", text)
    wait = re.search(r"대기신청\s*(\d+)\s*명\s*/\s*대기정원\s*(\d+)\s*명", text)
    return (
        int(main.group(1)) if main else None,
        int(main.group(2)) if main else None,
        int(wait.group(1)) if wait else None,
        int(wait.group(2)) if wait else None,
    )


def _table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for table_row in soup.select("table tr"):
        pending = ""
        for cell in table_row.find_all(["th", "td"], recursive=False):
            if cell.name == "th":
                pending = _clean(cell.get_text(" ", strip=True))
            elif pending:
                result[pending] = _clean(cell.get_text(" ", strip=True))
                pending = ""
    return result


def _reserve_branch_code(branch: Any) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"GURI_RESERVE_BRANCH_{digest}"


def _reserve_education_range(
    identity: str,
    value: Any,
) -> tuple[str, str, str, bool]:
    values = _date_tokens(value)
    if len(values) == 2 and values[1] >= values[0]:
        start, end = values
        return (
            start.isoformat(),
            end.isoformat(),
            f"{start.isoformat()} ~ {end.isoformat()}",
            False,
        )
    correction = GURI_RESERVE_DATE_CORRECTIONS.get(identity)
    if len(values) == 2 and correction is not None:
        raw_start, raw_end, corrected_end = correction
        if values[0].isoformat() == raw_start and values[1].isoformat() == raw_end:
            return raw_start, corrected_end, f"{raw_start} ~ {corrected_end}", True
    return "", "", "", False


def _reserve_parse_list_page(
    source: GuriReserveSource,
    soup: BeautifulSoup,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for table_row in soup.select("table tbody tr"):
        cells = table_row.find_all("td", recursive=False)
        if not cells:
            continue
        if len(cells) == 1 and cells[0].get("colspan"):
            # The portal renders one colspan placeholder row for an empty
            # result page, including every out-of-range sentinel.
            continue
        if len(cells) != 8:
            errors.append(f"{source.code}: malformed eight-column course row")
            continue
        link = cells[1].select_one("a.subject[href]")
        if link is None:
            errors.append(f"{source.code}: course row has no canonical detail link")
            continue
        parsed_link = urlparse(link.get("href") or "")
        query = parse_qs(parsed_link.query, keep_blank_values=True)
        identity = _clean((query.get("lctreRcritKey") or [""])[0])
        link_key = _clean((query.get("key") or [""])[0])
        link_institution = _clean((query.get("searchEduInstSe") or [""])[0])
        link_education_key = _clean((query.get("searchEduKey") or [""])[0])
        if (
            parsed_link.path not in {
                GURI_RESERVE_DETAIL_PATH,
                "./selectGuriUserCourseView.do",
                "selectGuriUserCourseView.do",
            }
            or not _IDENTITY_RE.fullmatch(identity)
            or link_key != source.menu_key
            or link_institution != source.institution_code
            or link_education_key != source.education_key
        ):
            errors.append(f"{source.code}: malformed or cross-source detail identity")
            continue
        title = _clean(link.get_text(" ", strip=True))
        branch_node = cells[1].find("span")
        branch = _clean(branch_node.get_text(" ", strip=True)) if branch_node else ""
        if not title:
            errors.append(f"{source.code}:{identity}: empty title")
        if branch != source.name:
            errors.append(f"{source.code}:{identity}: source branch mismatch")

        apply_start, apply_end, apply_period = _date_range(
            _clean(
                cells[2].select_one(".acc_date").get_text(" ", strip=True)
                if cells[2].select_one(".acc_date")
                else ""
            )
        )
        start, end, period, date_corrected = _reserve_education_range(
            identity,
            _clean(
                cells[2].select_one(".edu_date").get_text(" ", strip=True)
                if cells[2].select_one(".edu_date")
                else ""
            ),
        )
        if not apply_period:
            errors.append(f"{source.code}:{identity}: invalid list application period")
        if not period:
            errors.append(f"{source.code}:{identity}: invalid list education period")
        source_status = _clean(cells[7].get_text(" ", strip=True))
        status = _reserve_status(source_status)
        if not status:
            errors.append(f"{source.code}:{identity}: unknown list status")
        current_capacity, total_capacity, wait_current, wait_total = _reserve_capacity(
            cells[5].get_text(" ", strip=True)
        )
        raw_url = guri_reserve_detail_url(source, identity)
        row: dict[str, Any] = {
            "provider": GURI_RESERVE_PROVIDER,
            "provider_course_id": f"{GURI_RESERVE_PROVIDER}:course:{identity}",
            "title": title,
            "branch": branch,
            "branch_code": _reserve_branch_code(branch),
            "provider_organizer": branch,
            "category": "구리시 통합예약 교육강좌",
            "raw_url": raw_url,
            "status": status,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "period": period,
            "start_date": start,
            "end_date": end,
            "schedule_raw": _clean(cells[3].get_text(" ", strip=True)),
            "capacity": total_capacity,
            "capacity_total": total_capacity,
            "capacity_current": current_capacity,
            "waitlist_total": wait_total,
            "waitlist_current": wait_current,
            "application_method_raw": _clean(cells[6].get_text(" ", strip=True)),
            "description": _clean(table_row.get_text(" ", strip=True)),
            "collection_category": "교육",
            "domain_category": "공공예약",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_integrated_reservation",
            "collection_type": "static_html+detail_html",
            "program_type": "강좌",
            "region": GURI_MUNICIPALITY_NAME,
            "reservation_available": False,
            "raw_fields": {
                "parser": GURI_RESERVE_PARSER,
                "course_id": identity,
                "source_code": source.code,
                "source_key": source.menu_key,
                "source_institution_code": source.institution_code,
                "source_education_key": source.education_key,
                "source_status": source_status,
                "source_branch": branch,
                "source_date_corrected": date_corrected,
                "source_date_correction": (
                    GURI_RESERVE_DATE_CORRECTIONS.get(identity)
                    if date_corrected
                    else None
                ),
            },
        }
        rows.append(_clean_row(row))
    return rows, errors


def _reserve_application_url(
    source: GuriReserveSource,
    identity: str,
    soup: BeautifulSoup,
) -> tuple[str, list[str]]:
    errors: list[str] = []
    urls: set[str] = set()
    for anchor in soup.select("a[href*='addGuriUserCourseRegistView.do']"):
        parsed = urlparse(anchor.get("href") or "")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.path not in {
                GURI_RESERVE_APPLICATION_PATH,
                "./addGuriUserCourseRegistView.do",
                "addGuriUserCourseRegistView.do",
            }
            or _clean((query.get("key") or [""])[0]) != source.menu_key
            or _clean((query.get("searchEduInstSe") or [""])[0])
            != source.institution_code
            or _clean((query.get("searchEduKey") or [""])[0])
            != source.education_key
            or _clean((query.get("lctreRcritKey") or [""])[0]) != identity
        ):
            errors.append(f"{source.code}:{identity}: malformed application URL")
            continue
        canonical = f"https://{GURI_RESERVE_HOST}{GURI_RESERVE_APPLICATION_PATH}?" + urlencode(
            tuple(
                pair
                for pair in (
                    ("key", source.menu_key),
                    ("searchEduInstSe", source.institution_code),
                    ("searchEduKey", source.education_key),
                    ("lctreRcritKey", identity),
                )
                if pair[1]
            )
        )
        urls.add(canonical)
    if len(urls) > 1:
        errors.append(f"{source.code}:{identity}: conflicting application URLs")
    if not urls and "addGuriUserCourseRegistView.do" in str(soup):
        # Before the reception window, pages without a caution popup omit the
        # concrete anchor although the official JSP application route remains
        # present.  Construct the same identity-bound route used by sibling
        # courses; a live unauthenticated GET returns the portal's login guard.
        urls.add(
            f"https://{GURI_RESERVE_HOST}{GURI_RESERVE_APPLICATION_PATH}?"
            + urlencode(
                tuple(
                    pair
                    for pair in (
                        ("key", source.menu_key),
                        ("searchEduInstSe", source.institution_code),
                        ("searchEduKey", source.education_key),
                        ("lctreRcritKey", identity),
                    )
                    if pair[1]
                )
            )
        )
    return (next(iter(urls)) if len(urls) == 1 else ""), errors


def _reserve_detail_contract(
    source: GuriReserveSource,
    row: dict[str, Any],
    soup: BeautifulSoup,
) -> tuple[list[str], bool]:
    errors: list[str] = []
    identity = _clean(row.get("raw_fields", {}).get("course_id"))
    title_node = soup.select_one(".title_area .tit_text")
    status_node = soup.select_one(".title_area .acc_btn")
    detail_title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
    detail_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
    if _normalized(detail_title) != _normalized(row.get("title")):
        errors.append(f"{source.code}:{identity}: detail/list title mismatch")
    if detail_status != _clean(row.get("raw_fields", {}).get("source_status")):
        errors.append(f"{source.code}:{identity}: detail/list status mismatch")

    pairs = _table_pairs(soup)
    detail_apply_start, detail_apply_end, detail_apply_period = _date_range(
        pairs.get("접수기간")
    )
    detail_start, detail_end, detail_period, detail_date_corrected = (
        _reserve_education_range(identity, pairs.get("교육기간"))
    )
    if detail_apply_period != _clean(row.get("apply_period")):
        errors.append(f"{source.code}:{identity}: detail/list application period mismatch")
    if detail_period != _clean(row.get("period")):
        errors.append(f"{source.code}:{identity}: detail/list education period mismatch")

    current_capacity, total_capacity, wait_current, wait_total = _reserve_capacity(
        pairs.get("접수현황")
    )
    # Visit-only programmes publish their physical class capacity in the list
    # but intentionally report an online reservation capacity of zero in the
    # detail table.  Internet-capable rows must match; visit-only rows retain
    # the list capacity and preserve the online counters as audit evidence.
    online_application = "인터넷" in _clean(row.get("application_method_raw"))
    if online_application:
        for field, detail_value in (
            ("capacity_current", current_capacity),
            ("capacity_total", total_capacity),
            ("waitlist_current", wait_current),
            ("waitlist_total", wait_total),
        ):
            if row.get(field) is not None and detail_value != row.get(field):
                errors.append(f"{source.code}:{identity}: detail/list {field} mismatch")

    application_url, application_errors = _reserve_application_url(
        source, identity, soup
    )
    errors.extend(application_errors)
    status = _clean(row.get("status"))
    actionable_or_future = status in {"OPEN", "SCHEDULED"}
    online_actionable = actionable_or_future and "인터넷" in _clean(
        row.get("application_method_raw")
    )
    if online_actionable and not application_url:
        errors.append(f"{source.code}:{identity}: status/application URL mismatch")

    venue = _clean(pairs.get("교육장"))
    if not venue:
        errors.append(f"{source.code}:{identity}: missing education venue")
    description = _clean(
        " ".join(
            value
            for value in (pairs.get("강의소개"), pairs.get("유의사항"))
            if value
        )
    )
    row.update(
        {
            "apply_period": detail_apply_period or row.get("apply_period"),
            "apply_start_date": detail_apply_start or row.get("apply_start_date"),
            "apply_end_date": detail_apply_end or row.get("apply_end_date"),
            "period": detail_period or row.get("period"),
            "start_date": detail_start or row.get("start_date"),
            "end_date": detail_end or row.get("end_date"),
            "schedule_raw": _clean(pairs.get("교육시간")) or row.get("schedule_raw"),
            "target": _clean(pairs.get("교육대상")),
            "fee": _money(pairs.get("수강료")),
            "room": venue,
            "venue_name": venue,
            "description": description or row.get("description"),
            "phone": _clean(pairs.get("문의전화")),
            "instructor": _clean(pairs.get("강사명")),
            "reservation_available": bool(
                status == "OPEN" and online_actionable and application_url
            ),
        }
    )
    if application_url and online_actionable:
        row["application_url"] = application_url
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        row["raw_fields"]["clear_application_url"] = True
    row["raw_fields"].update(
        {
            "detail_pairs": pairs,
            "detail_status": detail_status,
            "canonical_application_url": application_url,
            "detail_online_capacity_current": current_capacity,
            "detail_online_capacity_total": total_capacity,
            "detail_online_waitlist_current": wait_current,
            "detail_online_waitlist_total": wait_total,
            "detail_date_corrected": detail_date_corrected,
        }
    )
    explicit_test = (
        _normalized(detail_title) in {"테스트", "테스트용"}
        and "테스트" in _clean(description)
    )
    return errors, explicit_test


def collect_guri_reserve_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 60,
    detail_limit: int = 400,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect every non-GSEEK education branch in Guri's reservation portal."""

    if not is_guri_reserve_target(target):
        return [], GURI_RESERVE_PARSER, _failure(
            "target does not match the canonical Guri reservation aggregate anchor"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], GURI_RESERVE_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    errors: list[str] = []
    source_cap_reached = False
    current: Any = None
    inventory_soup: Optional[BeautifulSoup] = None
    resident_inventory_soup: Optional[BeautifulSoup] = None
    page_soups: dict[tuple[str, int], BeautifulSoup] = {}
    source_summaries: dict[str, tuple[int, int]] = {}
    all_rows: list[dict[str, Any]] = []
    detail_pages = 0
    detail_errors = 0
    explicit_test_count = 0
    required_list_requests = 0
    try:
        current = session_factory()
        headers = getattr(current, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                }
            )
        try:
            inventory_soup = _with_retry(
                lambda: _get_soup(current, GURI_RESERVE_URL, timeout)
            )
        except Exception as exc:
            errors.append(f"source inventory: fetch {type(exc).__name__}")

        # The retired lifelong page exposes the ten top-level internal
        # catalogues.  The resident-centre page is the official second-level
        # inventory for all eight dong branches, so both documents are needed
        # to prove the complete 17-source set.
        if inventory_soup is not None:
            try:
                first_source = GURI_RESERVE_SOURCES[0]
                resident_inventory_soup = _with_retry(
                    lambda: _get_soup(
                        current, guri_reserve_list_url(first_source, 1), timeout
                    )
                )
                page_soups[(first_source.code, 1)] = resident_inventory_soup
            except Exception as exc:
                errors.append(f"resident source inventory: fetch {type(exc).__name__}")

        expected_inventory = {
            _reserve_source_signature(source) for source in GURI_RESERVE_SOURCES
        }
        discovered_inventory: set[tuple[str, str, str]] = set()
        if inventory_soup is not None:
            discovered_inventory = _reserve_inventory(inventory_soup)
            if resident_inventory_soup is not None:
                discovered_inventory.update(_reserve_inventory(resident_inventory_soup))
            if discovered_inventory != expected_inventory:
                missing = len(expected_inventory - discovered_inventory)
                unexpected = len(discovered_inventory - expected_inventory)
                errors.append(
                    f"source inventory changed: {missing} missing, {unexpected} unexpected"
                )

        minimum_list_requests = len(GURI_RESERVE_SOURCES) * 2
        if allowed_pages < minimum_list_requests:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of at least "
                f"{minimum_list_requests} required source pages and sentinels"
            )

        if not errors:
            for source in GURI_RESERVE_SOURCES:
                soup = page_soups.get((source.code, 1))
                if soup is None:
                    try:
                        soup = _with_retry(
                            lambda source=source: _get_soup(
                                current, guri_reserve_list_url(source, 1), timeout
                            )
                        )
                        page_soups[(source.code, 1)] = soup
                    except Exception as exc:
                        errors.append(f"{source.code} page 1: fetch {type(exc).__name__}")
                        continue
                summary = _reserve_summary(soup)
                if summary is None:
                    errors.append(f"{source.code} page 1: missing source total/page marker")
                    continue
                total, current_page, advertised_last = summary
                expected_last = max(1, math.ceil(total / GURI_RESERVE_PAGE_SIZE))
                if current_page != 1 or advertised_last != expected_last:
                    errors.append(f"{source.code} page 1: inconsistent pagination marker")
                source_summaries[source.code] = (total, advertised_last)

        if len(source_summaries) == len(GURI_RESERVE_SOURCES):
            required_list_requests = sum(
                advertised_last + 1
                for _total, advertised_last in source_summaries.values()
            )
            if required_list_requests > allowed_pages:
                source_cap_reached = True
                errors.append(
                    f"max_pages cap allows {allowed_pages} of "
                    f"{required_list_requests} required source pages and sentinels"
                )

        if not errors:
            for source in GURI_RESERVE_SOURCES:
                _total, advertised_last = source_summaries[source.code]
                for page in range(2, advertised_last + 2):
                    try:
                        page_soups[(source.code, page)] = _with_retry(
                            lambda source=source, page=page: _get_soup(
                                current, guri_reserve_list_url(source, page), timeout
                            )
                        )
                    except Exception as exc:
                        errors.append(
                            f"{source.code} page {page}: fetch {type(exc).__name__}"
                        )
                        break

        page_counts: dict[str, dict[int, int]] = {}
        source_totals: dict[str, int] = {}
        if not errors:
            for source in GURI_RESERVE_SOURCES:
                total, advertised_last = source_summaries[source.code]
                source_totals[source.code] = total
                page_counts[source.code] = {}
                for page in range(1, advertised_last + 2):
                    soup = page_soups.get((source.code, page))
                    if soup is None:
                        errors.append(f"{source.code} page {page}: missing fetched page")
                        continue
                    summary = _reserve_summary(soup)
                    if summary is None:
                        errors.append(f"{source.code} page {page}: missing pagination marker")
                        continue
                    page_total, current_page, page_last = summary
                    if (
                        page_total != total
                        or current_page != page
                        or page_last != advertised_last
                    ):
                        errors.append(f"{source.code} page {page}: pagination changed")
                    parsed_rows, parse_errors = _reserve_parse_list_page(source, soup)
                    errors.extend(parse_errors)
                    page_counts[source.code][page] = len(parsed_rows)
                    if page <= advertised_last:
                        expected_count = (
                            0
                            if total == 0
                            else min(
                                GURI_RESERVE_PAGE_SIZE,
                                total - (page - 1) * GURI_RESERVE_PAGE_SIZE,
                            )
                        )
                        if len(parsed_rows) != expected_count:
                            errors.append(
                                f"{source.code} page {page}: expected {expected_count} "
                                f"rows, got {len(parsed_rows)}"
                            )
                        all_rows.extend(parsed_rows)
                    elif parsed_rows:
                        errors.append(f"{source.code}: sentinel page is not empty")

        declared_total = sum(source_totals.values())
        if source_totals and len(all_rows) != declared_total:
            errors.append(
                f"declared aggregate total {declared_total} != parsed rows {len(all_rows)}"
            )
        identities = [_clean(row.get("provider_course_id")) for row in all_rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate provider course identities")
        urls = [_clean(row.get("raw_url")) for row in all_rows]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate canonical course URLs")

        current_rows: list[dict[str, Any]] = []
        expired_count = 0
        for row in all_rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
                continue
            if end < cutoff:
                expired_count += 1
            else:
                current_rows.append(row)

        if len(current_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_rows)} required current/future details"
            )

        publish_rows: list[dict[str, Any]] = []
        if not errors:
            source_by_code = {source.code: source for source in GURI_RESERVE_SOURCES}
            for row in current_rows:
                source_code = _clean(row.get("raw_fields", {}).get("source_code"))
                source = source_by_code[source_code]
                try:
                    soup = _with_retry(
                        lambda row=row: _get_soup(
                            current, _clean(row.get("raw_url")), timeout
                        )
                    )
                    detail_pages += 1
                    row_errors, explicit_test = _reserve_detail_contract(
                        source, row, soup
                    )
                    detail_errors += len(row_errors)
                    errors.extend(row_errors)
                    if explicit_test:
                        explicit_test_count += 1
                        row["raw_fields"]["excluded_reason"] = "explicit_source_test_course"
                    else:
                        publish_rows.append(row)
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: "
                        f"detail fetch {type(exc).__name__}"
                    )

        semantic_signatures = [
            (
                _normalized(row.get("title")),
                _normalized(row.get("branch")),
                _clean(row.get("period")),
                _normalized(row.get("schedule_raw")),
            )
            for row in publish_rows
        ]
        semantic_duplicate_count = len(semantic_signatures) - len(
            set(semantic_signatures)
        )
        if semantic_duplicate_count:
            errors.append(
                f"{semantic_duplicate_count} duplicate current semantic course signatures"
            )

        result: list[dict[str, Any]] = []
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in publish_rows]))
            if len(result) != len(publish_rows):
                errors.append(
                    f"dedupe changed complete row count {len(publish_rows)} to {len(result)}"
                )
                result = []

        branch_counts = Counter(_clean(row.get("branch")) for row in publish_rows)
        status_counts = Counter(_clean(row.get("status")) for row in publish_rows)
        current_source_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_code"))
            for row in current_rows
        )
        snapshot_complete = not errors
        meta = {
            "pages": len(page_soups),
            "main_discovery_pages": sum(
                value is not None
                for value in (inventory_soup, resident_inventory_soup)
            ),
            "list_requests": len(page_soups),
            "detail_pages": detail_pages,
            "source_count": len(GURI_RESERVE_SOURCES),
            "inventory_count": len(discovered_inventory),
            "source_totals": source_totals,
            "source_total": declared_total,
            "source_rows": len(all_rows),
            "required_list_requests": required_list_requests,
            "page_counts": page_counts,
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "explicit_test_excluded_count": explicit_test_count,
            "returned_count": len(result),
            "current_source_counts": dict(current_source_counts),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_duplicate_count": semantic_duplicate_count,
            "detail_errors": detail_errors,
            "discovered_links": len(all_rows),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "pagination_detected": any(
                advertised_last > 1
                for _total, advertised_last in source_summaries.values()
            ),
            "pagination_complete": bool(
                snapshot_complete and len(page_soups) == required_list_requests
            ),
            "details_complete": bool(
                snapshot_complete and detail_pages == len(current_rows)
            ),
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "all complete Guri reservation education courses have ended"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
        }
        if errors:
            return [], GURI_RESERVE_PARSER, meta
        return result, GURI_RESERVE_PARSER, meta
    finally:
        _close_quietly(current)


def is_guri_education_target(target: Any) -> bool:
    return is_guri_gseek_target(target) or is_guri_reserve_target(target)


def collect_guri_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 60,
    detail_limit: int = 400,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if is_guri_gseek_target(target):
        return collect_guri_gseek_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    if is_guri_reserve_target(target):
        return collect_guri_reserve_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    return [], GURI_RESERVE_PARSER, _failure(
        "target does not match a canonical Guri education provider route"
    )


is_target = is_guri_education_target
collect = collect_guri_education_courses


__all__ = [
    "GURI_GSEEK_API_URL",
    "GURI_GSEEK_CO_SPONSOR_ID",
    "GURI_GSEEK_PAGE_SIZE",
    "GURI_GSEEK_PARSER",
    "GURI_GSEEK_PROVIDER",
    "GURI_GSEEK_REGION_CODE",
    "GURI_GSEEK_URL",
    "GURI_LEGACY_RESERVE_PROVIDER",
    "GURI_LEGACY_RESERVE_URL",
    "GURI_MUNICIPALITY_CODE",
    "GURI_MUNICIPALITY_NAME",
    "GURI_RESERVE_PAGE_SIZE",
    "GURI_RESERVE_PARSER",
    "GURI_RESERVE_PROVIDER",
    "GURI_RESERVE_SOURCES",
    "GURI_RESERVE_URL",
    "GuriReserveSource",
    "collect",
    "collect_guri_education_courses",
    "collect_guri_gseek_courses",
    "collect_guri_reserve_courses",
    "guri_gseek_api_range",
    "guri_gseek_detail_url",
    "guri_reserve_detail_url",
    "guri_reserve_list_url",
    "is_guri_education_target",
    "is_guri_gseek_target",
    "is_guri_legacy_redirect_target",
    "is_guri_reserve_target",
    "is_target",
]
