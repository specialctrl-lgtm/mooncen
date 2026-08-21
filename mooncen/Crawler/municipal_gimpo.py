"""Fail-closed collector for Gimpo City's current education catalogue.

Gimpo's former integrated-reservation education list is now a frozen archive:
the complete 1,773-row route ends in 2024, while its institution-filter URLs
are strict subsets of that archive.  Current lifelong-learning and all
resident-centre (``가까이배움터``) courses are owned by the official branded
GSEEK portal instead.  This collector therefore publishes only the branded
GSEEK catalogue and exposes the old routes as audited exclusions.

The landing page declares the catalogue total.  The GSEEK JSON endpoint
returns nine rows per range, so a snapshot is published only after every
declared range, the immediate post-total sentinel, and every current/future
detail page have been validated.  Managed sessions are rotated below the
shared SafeSession request budget.  Certificate verification is never
disabled.

This module does not import ``Crawler_MunicipalYaml`` so that the shared
router can import it without creating a cycle.  Production callers must inject
the router's managed session factory and dedupe helper.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
import hashlib
import html
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GIMPO_PROVIDER = "MUNI_GIMPO_GSEEK_KR_6685FD9C"
GIMPO_URL = "https://gimpo.gseek.kr/user/course/offline/list"
GIMPO_HOST = "gimpo.gseek.kr"
GIMPO_LIST_PATH = "/user/course/offline/list"
GIMPO_DETAIL_PATH = "/user/course/offline/view"
GIMPO_API_URL = "https://gimpo.gseek.kr/user/course/offline/list/search"
GIMPO_API_HOST = "gimpo.gseek.kr"
GIMPO_REGION_CODE = "4157000000"
GIMPO_REGION_NAME = "김포시"
GIMPO_PRIMARY_CO_SPONSOR_ID = "G000003"
GIMPO_EXCLUDED_SHARED_CO_SPONSOR_ID = "G000001"
GIMPO_PAGE_SIZE = 9
GIMPO_SESSION_REQUEST_LIMIT = 150
GIMPO_PARSER = "gimpo_gseek_complete_ranges+immediate_sentinel+current_detail"
GIMPO_MUNICIPALITY_CODE = "4157000000"
GIMPO_MUNICIPALITY_NAME = "경기도 김포시"

# Audited exclusions.  The first URL is the complete legacy archive; the
# remaining filtered routes are strict subsets.  The index/mobile routes are
# discovery shells and do not own course rows.
GIMPO_LEGACY_ARCHIVE_PROVIDER = "MUNI_WWW_GIMPO_GO_KR_83984E5D"
GIMPO_LEGACY_ARCHIVE_URL = (
    "https://www.gimpo.go.kr/reserve/webEdcLctreList.do?key=112&rep=1"
)
GIMPO_LEGACY_SUBSET_PROVIDERS = (
    "MUNI_WWW_GIMPO_GO_KR_550D23F1",
)
GIMPO_LEGACY_SUBSET_URLS = (
    "https://www.gimpo.go.kr/reserve/webEdcLctreList.do?key=112&rep=1&searchInsttCode=120050",
    "https://www.gimpo.go.kr/reserve/webEdcLctreList.do?key=112&rep=1&searchInsttCode=120071",
)
GIMPO_DISCOVERY_SHELL_PROVIDER = "MUNI_WWW_GIMPO_GO_KR_6341E241"
GIMPO_DISCOVERY_SHELL_URLS = (
    "https://www.gimpo.go.kr/reserve/index.do",
    "https://m.gimpo.go.kr/reserve/index.do",
    "https://m1.gimpo.go.kr/reserve/index.do",
)

GIMPO_RESIDENT_CENTRES = frozenset(
    {
        "통진읍",
        "고촌읍",
        "양촌읍",
        "대곶면",
        "월곶면",
        "하성면",
        "김포본동",
        "장기본동",
        "사우동",
        "풍무동",
        "장기동",
        "구래동",
        "마산동",
        "운양동",
    }
)
GIMPO_AGGREGATE_RESIDENT_BRANCH = "읍면동 가까이배움터"
GIMPO_LOCAL_REGIONS = GIMPO_RESIDENT_CENTRES | {GIMPO_REGION_NAME}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d+")
_LANDING_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*개의\s*강좌")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_RESIDENT_TITLE_RE = re.compile(r"^\[([^\]]+?)(?:\s+단기특강)?\]")
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


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_gimpo_education_target(target: Any) -> bool:
    """Match only the exact provider-owned branded landing route."""

    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == GIMPO_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GIMPO_HOST
        and parsed.port is None
        and parsed.path == GIMPO_LIST_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_gimpo_education_target


def gimpo_detail_url(subject_id: Any, cycle_id: Any) -> str:
    subject = _clean(subject_id)
    cycle = _clean(cycle_id)
    if not _IDENTITY_RE.fullmatch(subject) or not _IDENTITY_RE.fullmatch(cycle):
        return ""
    return f"https://{GIMPO_HOST}{GIMPO_DETAIL_PATH}?" + urlencode(
        {"s_sbjct_sn": subject, "s_sbjct_cycl_sn": cycle}
    )


def gimpo_api_range(page: Any) -> tuple[int, int]:
    raw_page = _clean(page)
    if not _IDENTITY_RE.fullmatch(raw_page) or int(raw_page) < 1:
        return (0, 0)
    start = (int(raw_page) - 1) * GIMPO_PAGE_SIZE + 1
    return start, start + GIMPO_PAGE_SIZE


def gimpo_sentinel_range(total: Any) -> tuple[int, int]:
    raw_total = _clean(total).replace(",", "")
    if not _IDENTITY_RE.fullmatch(raw_total) or int(raw_total) < 0:
        return (0, 0)
    start = int(raw_total) + 1
    return start, start + GIMPO_PAGE_SIZE


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


def _landing_total(soup: BeautifulSoup) -> Optional[int]:
    matches = _LANDING_TOTAL_RE.findall(_clean(soup.get_text(" ", strip=True)))
    values = {int(value.replace(",", "")) for value in matches}
    if len(values) != 1:
        return None
    region = soup.select_one("input#s_resion_cd1[name='s_resion_cd1']")
    sponsor = soup.select_one("input[name='ARK_CO_SPRVSN_ID']")
    if (
        region is None
        or _clean(region.get("value")) != GIMPO_REGION_CODE
        or sponsor is None
        or _clean(sponsor.get("value")) != GIMPO_PRIMARY_CO_SPONSOR_ID
    ):
        return None
    return values.pop()


def _integer(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    if not raw or not _IDENTITY_RE.fullmatch(raw):
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
    return f"GIMPO_GSEEK_BRANCH_{digest}"


def _resolved_branch(source_branch: str, title: str, source_region: str) -> tuple[str, str]:
    source = _clean(source_branch)
    if source != GIMPO_AGGREGATE_RESIDENT_BRANCH:
        return source, ""
    match = _RESIDENT_TITLE_RE.match(_clean(title))
    centre = _clean(match.group(1)) if match else ""
    if centre not in GIMPO_RESIDENT_CENTRES or centre != _clean(source_region):
        return "", "aggregate resident-centre row has no audited title prefix"
    return f"{centre} 가까이배움터", ""


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _api_row(target: Any, item: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    subject = _clean(item.get("d_sbjct_sn"))
    cycle = _clean(item.get("d_sbjct_cycl_sn"))
    identity = f"{subject}:{cycle}"
    title = _clean(item.get("d_sbjct_nm"))
    source_branch = _clean(item.get("d_edu_gvmnfc"))
    source_region = _clean(item.get("d_rgn"))
    branch, branch_error = _resolved_branch(source_branch, title, source_region)
    source_status = _clean(item.get("d_recrut_stts_nm"))
    start = _source_date(item.get("d_edu_bgng_dt"))
    end = _source_date(item.get("d_edu_end_dt"))
    sponsor = _clean(item.get("d_co_sprvsn_id"))
    single_day_flag = _clean(item.get("d_is_single_day_course"))

    if not _IDENTITY_RE.fullmatch(subject) or not _IDENTITY_RE.fullmatch(cycle):
        errors.append("non-numeric subject/cycle identity")
    if not title:
        errors.append(f"course {identity}: empty title")
    if not source_branch or not branch:
        errors.append(f"course {identity}: empty or unresolved education institution")
    if branch_error:
        errors.append(f"course {identity}: {branch_error}")
    if source_region not in GIMPO_LOCAL_REGIONS:
        errors.append(f"course {identity}: non-Gimpo local region")
    if sponsor != GIMPO_PRIMARY_CO_SPONSOR_ID:
        errors.append(f"course {identity}: unaudited Gimpo co-sponsor/branch ownership")
    if _clean(item.get("d_sbjct_type_cd_id")) != "OF":
        errors.append(f"course {identity}: not an offline education course")
    if source_status not in _STATUS_MAP:
        errors.append(f"course {identity}: unknown recruitment status")
    if start is None or end is None or end < start:
        errors.append(f"course {identity}: invalid education date range")
    if single_day_flag not in {"Y", "N"}:
        errors.append(f"course {identity}: invalid single-day source flag")
    elif start is not None and end is not None and ((start == end) != (single_day_flag == "Y")):
        errors.append(f"course {identity}: single-day source flag/date mismatch")

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
    category = " > ".join(
        value
        for value in (
            _clean(item.get("d_clsf_depth1_nm")),
            _clean(item.get("d_clsf_depth2_nm")),
            _clean(item.get("d_clsf_depth3_nm")),
        )
        if value
    )
    capacity_total = _integer(item.get("d_edu_nope"))
    capacity_current = _integer(item.get("d_aply_cnt"))
    raw_url = gimpo_detail_url(subject, cycle)
    period = f"{start.isoformat()} ~ {end.isoformat()}" if start and end else ""
    row: dict[str, Any] = {
        "provider": GIMPO_PROVIDER,
        "provider_course_id": f"{GIMPO_PROVIDER}:course:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "provider_organizer": source_branch,
        "category": category or "평생학습",
        "raw_url": raw_url,
        "status": _STATUS_MAP.get(source_status, ""),
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
        "instructor": _clean(item.get("d_instr_nm")),
        "application_method_raw": _clean(item.get("d_stdnt_chice_mthd_cd_nm")),
        "collection_category": "교육",
        "domain_category": "평생학습",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_integrated_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "json_api+detail_html",
        "program_type": "강좌",
        "region": GIMPO_MUNICIPALITY_NAME,
        "municipality_code": GIMPO_MUNICIPALITY_CODE,
        "municipality_full_name": GIMPO_MUNICIPALITY_NAME,
        "reservation_available": False,
        "raw_fields": {
            "parser": GIMPO_PARSER,
            "subject_id": subject,
            "cycle_id": cycle,
            "source_status": source_status,
            "source_branch": source_branch,
            "resolved_branch": branch,
            "source_region": source_region,
            "co_sponsor_id": sponsor,
            "registration_timestamp": _clean(item.get("d_reg_dt")),
            "source_item": dict(item),
        },
    }
    return _clean_row(row), errors


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
    if not detail_period and pairs.get("학습일자"):
        single_dates = _date_tokens(pairs.get("학습일자"))
        if (
            len(single_dates) == 1
            and _clean(raw_fields.get("source_item", {}).get("d_is_single_day_course")) == "Y"
        ):
            detail_start = single_dates[0].isoformat()
            detail_end = detail_start
            detail_period = f"{detail_start} ~ {detail_end}"
    if detail_period != _clean(row.get("period")):
        errors.append(f"course {identity}: detail/list education period mismatch")

    apply_source = pairs.get("신청기간") or pairs.get("일반신청기간")
    apply_start, apply_end, apply_period = _date_range(apply_source)
    if not apply_period:
        errors.append(f"course {identity}: missing detail application period")
    priority_start, priority_end, priority_period = _date_range(
        pairs.get("우선신청기간")
    )
    if pairs.get("우선신청기간") and not priority_period:
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

    parsed_url = urlparse(_clean(row.get("raw_url")))
    expected_return = parsed_url.path + "?" + parsed_url.query
    if input_value("p_return_url") != expected_return:
        errors.append(f"course {identity}: malformed login return URL")

    markup = str(soup)
    if "/user/course/cert/checkCi" not in markup or "/user/course/aply" not in markup:
        errors.append(f"course {identity}: missing canonical application flow")
    apply_control = container.select_one(".btn-course-box .btn-course-apply")
    if apply_control is None:
        errors.append(f"course {identity}: missing application control")
    classes = apply_control.get("class") or [] if apply_control is not None else []
    disabled = bool(
        apply_control is not None
        and (
            "disabled" in classes
            or apply_control.has_attr("disabled")
            or _clean(apply_control.get("aria-disabled")).lower() == "true"
        )
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
            "apply_start": apply_start,
            "apply_end": apply_end,
            "priority_apply_period": priority_period,
            "priority_apply_start": priority_start,
            "priority_apply_end": priority_end,
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
            "detail_priority_apply_start": priority_start,
            "detail_priority_apply_end": priority_end,
        }
    )
    return errors


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("branch")),
        _clean(row.get("period")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("venue_name")),
        _normalized(row.get("instructor")),
    )


def _dedupe_reopened_rounds(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int, list[str]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_semantic_key(row)].append(row)
    semantic_duplicates = sum(len(group) - 1 for group in groups.values() if len(group) > 1)
    result: list[dict[str, Any]] = []
    removed = 0
    errors: list[str] = []
    for key, group in groups.items():
        if len(group) == 1:
            result.append(group[0])
            continue
        ranked: list[tuple[str, int, int, dict[str, Any]]] = []
        for row in group:
            raw_fields = row.get("raw_fields", {})
            registered = _clean(raw_fields.get("registration_timestamp"))
            subject = _clean(raw_fields.get("subject_id"))
            cycle = _clean(raw_fields.get("cycle_id"))
            if (
                not re.fullmatch(r"\d{14}", registered)
                or not subject.isdigit()
                or not cycle.isdigit()
            ):
                errors.append("semantic duplicate group lacks a strict registration identity")
                ranked = []
                break
            ranked.append((registered, int(subject), int(cycle), row))
        if not ranked:
            continue
        ranked.sort(key=lambda item: item[:3], reverse=True)
        if len(ranked) > 1 and ranked[0][:3] == ranked[1][:3]:
            errors.append("semantic duplicate group has an ambiguous latest registration")
            continue
        keeper = ranked[0][3]
        duplicate_ids = [
            _clean(item[3].get("provider_course_id")) for item in ranked[1:]
        ]
        keeper.setdefault("raw_fields", {})["reopened_duplicate_round_ids"] = duplicate_ids
        keeper["raw_fields"]["reopened_duplicate_round_count"] = len(duplicate_ids)
        result.append(keeper)
        removed += len(duplicate_ids)
    result.sort(
        key=lambda row: (
            _clean(row.get("start_date")),
            _clean(row.get("title")),
            _clean(row.get("provider_course_id")),
        )
    )
    return result, semantic_duplicates, removed, errors


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
        "detail_attempts": 0,
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


def collect_gimpo_education_courses(
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
    """Return one complete current/future Gimpo education snapshot."""

    if not is_gimpo_education_target(target):
        return [], GIMPO_PARSER, _failure(
            "target does not match the canonical Gimpo GSEEK provider route"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], GIMPO_PARSER, _failure("managed session_factory injection is required")
        session_factory = _default_session_factory

    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        cutoff = _today(today)
    except (TypeError, ValueError):
        return [], GIMPO_PARSER, _failure("max_pages/detail_limit/today are invalid")

    errors: list[str] = []
    source_cap_reached = False
    current_session: Any = None
    session_requests = GIMPO_SESSION_REQUEST_LIMIT
    sessions_created = 0
    physical_requests = 0
    landing_soup: Optional[BeautifulSoup] = None
    page_payloads: dict[int, list[Any]] = {}
    rows: list[dict[str, Any]] = []
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    source_total = 0
    data_pages = 0
    required_list_requests = 0
    sentinel_start = 0

    def new_session() -> None:
        nonlocal current_session, session_requests, sessions_created
        _close_quietly(current_session)
        current_session = session_factory()
        sessions_created += 1
        session_requests = 0
        headers = getattr(current_session, "headers", None)
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

    def ensure_session() -> None:
        if current_session is None or session_requests >= GIMPO_SESSION_REQUEST_LIMIT:
            new_session()

    def get_soup(url: str) -> BeautifulSoup:
        nonlocal session_requests, physical_requests
        last_error: Optional[Exception] = None
        for attempt in range(2):
            ensure_session()
            session_requests += 1
            physical_requests += 1
            try:
                return _response_soup(
                    current_session.get(url, timeout=timeout, allow_redirects=False)
                )
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    new_session()
                    time.sleep(0.1)
        assert last_error is not None
        raise last_error

    def post_range(start: int, end: int) -> list[Any]:
        nonlocal session_requests, physical_requests
        last_error: Optional[Exception] = None
        for attempt in range(2):
            ensure_session()
            session_requests += 1
            physical_requests += 1
            try:
                response = current_session.post(
                    GIMPO_API_URL,
                    data={
                        "s_sort_by": "1",
                        "s_row_start": str(start),
                        "s_row_end": str(end),
                        "resion": GIMPO_REGION_CODE,
                    },
                    timeout=timeout,
                    allow_redirects=False,
                    headers={
                        "Referer": GIMPO_URL,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                return _response_json(response)
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    new_session()
                    time.sleep(0.1)
        assert last_error is not None
        raise last_error

    try:
        try:
            landing_soup = get_soup(GIMPO_URL)
        except Exception as exc:
            errors.append(f"landing page: fetch {type(exc).__name__}")

        if landing_soup is not None:
            declared = _landing_total(landing_soup)
            if declared is None or declared < 1:
                errors.append("landing page: missing unambiguous Gimpo catalogue contract")
            else:
                source_total = declared
                data_pages = math.ceil(source_total / GIMPO_PAGE_SIZE)
                required_list_requests = data_pages + 1
                sentinel_start = source_total + 1
                if required_list_requests > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap allows {allowed_pages} of "
                        f"{required_list_requests} required API range requests"
                    )

        if not errors:
            for page in range(1, data_pages + 1):
                start, end = gimpo_api_range(page)
                try:
                    page_payloads[page] = post_range(start, end)
                except Exception as exc:
                    errors.append(f"API range {page}: fetch {type(exc).__name__}")
                    break
            if not errors:
                start, end = gimpo_sentinel_range(source_total)
                try:
                    page_payloads[data_pages + 1] = post_range(start, end)
                except Exception as exc:
                    errors.append(f"API sentinel: fetch {type(exc).__name__}")

        malformed_count = 0
        if not errors:
            for page in range(1, data_pages + 1):
                payload = page_payloads.get(page, [])
                expected = min(
                    GIMPO_PAGE_SIZE,
                    source_total - (page - 1) * GIMPO_PAGE_SIZE,
                )
                if len(payload) != expected:
                    errors.append(
                        f"API range {page}: expected {expected} rows, got {len(payload)}"
                    )
                for item in payload:
                    if not isinstance(item, Mapping):
                        malformed_count += 1
                        errors.append(f"API range {page}: non-object course row")
                        continue
                    if _integer(item.get("d_total_cnt")) != source_total:
                        errors.append(f"API range {page}: catalogue total changed")
                    row, row_errors = _api_row(target, item)
                    rows.append(row)
                    malformed_count += len(row_errors)
                    errors.extend(row_errors)
            if page_payloads.get(data_pages + 1):
                errors.append("API immediate post-total sentinel range is not empty")
            if len(rows) != source_total:
                errors.append(f"declared total {source_total} != parsed rows {len(rows)}")

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
                end_date = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
                continue
            if end_date < cutoff:
                expired_count += 1
            else:
                current_rows.append(row)

        if len(current_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(current_rows)} required current/future details"
            )

        if not errors:
            for row in current_rows:
                detail_attempts += 1
                try:
                    detail_soup = get_soup(_clean(row.get("raw_url")))
                    row_errors = _detail_contract(row, detail_soup)
                    if row_errors:
                        detail_errors += len(row_errors)
                        errors.extend(row_errors)
                    else:
                        detail_pages += 1
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: "
                        f"detail fetch {type(exc).__name__}"
                    )

        canonical_rows: list[dict[str, Any]] = []
        semantic_duplicate_count = 0
        duplicate_rounds_removed = 0
        if not errors:
            (
                canonical_rows,
                semantic_duplicate_count,
                duplicate_rounds_removed,
                semantic_errors,
            ) = _dedupe_reopened_rounds(current_rows)
            errors.extend(semantic_errors)

        result: list[dict[str, Any]] = []
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in canonical_rows]))
            if len(result) != len(canonical_rows):
                errors.append(
                    f"dedupe changed complete row count {len(canonical_rows)} to {len(result)}"
                )
                result = []

        source_branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        branch_counts = Counter(_clean(row.get("branch")) for row in result)
        source_status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status")) for row in result
        )
        status_counts = Counter(_clean(row.get("status")) for row in result)
        sponsor_counts = Counter(
            _clean(row.get("raw_fields", {}).get("co_sponsor_id")) for row in rows
        )
        snapshot_complete = not errors
        pagination_complete = bool(
            snapshot_complete
            and len(page_payloads) == required_list_requests
            and not page_payloads.get(data_pages + 1)
            and len(rows) == source_total
        )
        details_complete = bool(
            snapshot_complete
            and detail_attempts == len(current_rows)
            and detail_pages == len(current_rows)
            and detail_errors == 0
        )
        meta = {
            "pages": len(page_payloads),
            "main_discovery_pages": 1 if landing_soup is not None else 0,
            "list_requests": len(page_payloads),
            "physical_requests": physical_requests,
            "sessions_created": sessions_created,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_errors": detail_errors,
            "source_total": source_total,
            "source_rows": len(rows),
            "data_pages": data_pages,
            "required_list_requests": required_list_requests,
            "sentinel_page": data_pages + 1 if data_pages else 0,
            "sentinel_start": sentinel_start,
            "page_counts": {
                page: len(payload) for page, payload in page_payloads.items()
            },
            "malformed_count": malformed_count,
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "source_branch_counts": dict(source_branch_counts),
            "status_counts": dict(status_counts),
            "source_status_counts": dict(source_status_counts),
            "co_sponsor_counts": dict(sponsor_counts),
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_duplicate_count": semantic_duplicate_count,
            "duplicate_rounds_removed": duplicate_rounds_removed,
            "discovered_links": len(rows),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "pagination_detected": data_pages > 1,
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "all complete Gimpo GSEEK catalogue courses have ended"
                if snapshot_complete and not current_rows
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
            "ownership_region_code": GIMPO_REGION_CODE,
            "ownership_primary_co_sponsor_id": GIMPO_PRIMARY_CO_SPONSOR_ID,
            "ownership_excluded_shared_co_sponsor_id": GIMPO_EXCLUDED_SHARED_CO_SPONSOR_ID,
        }
        if errors:
            return [], GIMPO_PARSER, meta
        return result, GIMPO_PARSER, meta
    finally:
        _close_quietly(current_session)


collect = collect_gimpo_education_courses


__all__ = [
    "GIMPO_AGGREGATE_RESIDENT_BRANCH",
    "GIMPO_API_URL",
    "GIMPO_DISCOVERY_SHELL_PROVIDER",
    "GIMPO_DISCOVERY_SHELL_URLS",
    "GIMPO_LEGACY_ARCHIVE_PROVIDER",
    "GIMPO_LEGACY_ARCHIVE_URL",
    "GIMPO_LEGACY_SUBSET_PROVIDERS",
    "GIMPO_LEGACY_SUBSET_URLS",
    "GIMPO_MUNICIPALITY_CODE",
    "GIMPO_MUNICIPALITY_NAME",
    "GIMPO_PAGE_SIZE",
    "GIMPO_PARSER",
    "GIMPO_PRIMARY_CO_SPONSOR_ID",
    "GIMPO_PROVIDER",
    "GIMPO_REGION_CODE",
    "GIMPO_RESIDENT_CENTRES",
    "GIMPO_SESSION_REQUEST_LIMIT",
    "GIMPO_URL",
    "collect",
    "collect_gimpo_education_courses",
    "gimpo_api_range",
    "gimpo_detail_url",
    "gimpo_sentinel_range",
    "is_gimpo_education_target",
    "is_target",
]
