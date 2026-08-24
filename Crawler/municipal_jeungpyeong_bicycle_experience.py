"""Fail-closed collector for Jeungpyeong Bicycle Park experience slots.

The official Jeungpyeong-gun information page defines the March-December
operating season and the 10:00/13:00 programme times.  Its public reservation
calendar exposes only two read-only JSON routes: the declared time ledger and
monthly calendar.  This collector reads those routes plus the two public HTML
pages.  It never opens the application form, reservation lookup, attachment,
download, login, authentication, applicant, or member routes.

The complete current-year operating-season tail is collected, followed by the
next January off-season sentinel.  The official detail page, calendar shell,
time ledger, first month, last month, and sentinel are rechecked before rows
are published.  Weekends and explicit closures are not programme instances;
future weekday cells marked ``예정`` are retained as scheduled slots.
"""

from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import hashlib
import json
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


JEUNGPYEONG_BICYCLE_PROVIDER = "MUNI_WWW_JP_GO_KR_8A39DB83"
JEUNGPYEONG_BICYCLE_CANDIDATE_ID = "MUNI_IR_402917A23300"
JEUNGPYEONG_BICYCLE_HOST = "www.jp.go.kr"
JEUNGPYEONG_BICYCLE_URL = (
    "https://www.jp.go.kr/kor/prog/bcyclParkResve/sub05_03_09_02/main.do"
)
JEUNGPYEONG_BICYCLE_INFO_URL = "https://www.jp.go.kr/kor/sub05_03_09_01.do"
JEUNGPYEONG_BICYCLE_TIME_URL = (
    "https://www.jp.go.kr/kor/prog/bcyclParkResve/sub05_03_09_02/getTime.do"
)
JEUNGPYEONG_BICYCLE_CALENDAR_URL = (
    "https://www.jp.go.kr/kor/prog/bcyclParkResve/sub05_03_09_02/getCalendar.do"
)
JEUNGPYEONG_BICYCLE_MUNICIPALITY_CODE = "4374500000"
JEUNGPYEONG_BICYCLE_MUNICIPALITY_NAME = "충청북도 증평군"
JEUNGPYEONG_BICYCLE_BRANCH = "증평군 어린이자전거 교통안전교육장"
JEUNGPYEONG_BICYCLE_BRANCH_CODE = "JEUNGPYEONG_BICYCLE_PARK"
JEUNGPYEONG_BICYCLE_ADDRESS = "충청북도 증평군 증평읍 남하용강로 16"
JEUNGPYEONG_BICYCLE_CAPACITY = 40
JEUNGPYEONG_BICYCLE_OPEN_MONTH = 3
JEUNGPYEONG_BICYCLE_CLOSE_MONTH = 12
JEUNGPYEONG_BICYCLE_MAX_BYTES = 2_000_000
JEUNGPYEONG_BICYCLE_PARSER = (
    "jeungpyeong_bicycle_park_complete_current_season_experience_slots+"
    "official_march_december_boundary+all_month_days+next_january_offseason_sentinel+"
    "stable_info_shell_times_first_last_sentinel+exact_calendar_semantics+"
    "weekend_closure_exclusion+canonical_open_application_landing_only+safe_route_allowlist+"
    "no_write_lookup_login_auth_member_applicant_file_attachment_download_or_pii_calls"
)

_ALLOWED_TYPES = frozenset({"A", "B", "C", "D"})
_FORBIDDEN_ROUTE_MARKERS = (
    "/write.do",
    "/list.do",
    "/login",
    "/auth",
    "/member",
    "/applicant",
    "/application",
    "/file",
    "/attachment",
    "/download",
)
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "applicant",
        "applicantName",
        "birthDate",
        "email",
        "memberId",
        "name",
        "phone",
        "residentNumber",
    }
)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_PHONE_RE = re.compile(r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)")
_MONTH_RE = re.compile(r"^\d{6}$")
_CAPACITY_STATE_RE = re.compile(r"^(\d{1,3}),([A-Z])$")


class JeungpyeongBicycleContractError(RuntimeError):
    """Raised when the public calendar no longer matches its audited contract."""


@dataclass(frozen=True)
class _CalendarDay:
    service_date: date
    weekday: int
    source_type: str
    close_type: str
    slots: tuple[tuple[str, int, str], ...]


SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]], Iterable[dict[str, Any]]], list[dict[str, Any]]]


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        if key in target:
            return target.get(key)
        extra = target.get("extra")
        return extra.get(key) if isinstance(extra, Mapping) else None
    value = getattr(target, key, None)
    if value is not None:
        return value
    extra = getattr(target, "extra", None)
    return extra.get(key) if isinstance(extra, Mapping) else None


def is_jeungpyeong_bicycle_experience_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == JEUNGPYEONG_BICYCLE_PROVIDER
        and _clean(_target_value(target, "url")) == JEUNGPYEONG_BICYCLE_URL
    )


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise JeungpyeongBicycleContractError("invalid cutoff date") from exc


def _session() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity",
            "User-Agent": "Mozilla/5.0 (compatible; MooncenCrawler/1.0)",
        }
    )
    return current


def _request_kind(method: str, url: str, payload: Optional[Mapping[str, Any]]) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != JEUNGPYEONG_BICYCLE_HOST:
        raise JeungpyeongBicycleContractError("request escaped exact official host")
    lowered = parsed.path.lower()
    if any(marker in lowered for marker in _FORBIDDEN_ROUTE_MARKERS):
        raise JeungpyeongBicycleContractError("forbidden application or identity route")
    normalized_method = method.upper()
    if normalized_method == "GET" and url in {
        JEUNGPYEONG_BICYCLE_URL,
        JEUNGPYEONG_BICYCLE_INFO_URL,
    }:
        if payload:
            raise JeungpyeongBicycleContractError("HTML request unexpectedly has payload")
        return "html"
    if normalized_method == "POST" and url == JEUNGPYEONG_BICYCLE_TIME_URL:
        if payload:
            raise JeungpyeongBicycleContractError("time request payload changed")
        return "json"
    if normalized_method == "POST" and url == JEUNGPYEONG_BICYCLE_CALENDAR_URL:
        if not isinstance(payload, Mapping) or set(payload) != {"yearMonth"}:
            raise JeungpyeongBicycleContractError("calendar payload shape changed")
        if not _MONTH_RE.fullmatch(_clean(payload.get("yearMonth"))):
            raise JeungpyeongBicycleContractError("calendar month is invalid")
        return "json"
    raise JeungpyeongBicycleContractError("request route is outside exact public allowlist")


def _request(
    session: Any,
    method: str,
    url: str,
    payload: Optional[Mapping[str, Any]],
    timeout: int,
) -> Any:
    kind = _request_kind(method, url, payload)
    kwargs: dict[str, Any] = {"timeout": timeout, "allow_redirects": False}
    if method.upper() == "POST":
        kwargs["data"] = dict(payload or {})
    response = session.request(method.upper(), url, **kwargs)
    if getattr(response, "status_code", None) != 200:
        raise JeungpyeongBicycleContractError(
            f"public source returned HTTP {getattr(response, 'status_code', None)}"
        )
    response_url = _clean(getattr(response, "url", url))
    if response_url and response_url != url:
        raise JeungpyeongBicycleContractError("public source redirected outside exact route")
    content = getattr(response, "content", b"")
    if isinstance(content, bytes) and len(content) > JEUNGPYEONG_BICYCLE_MAX_BYTES:
        raise JeungpyeongBicycleContractError("public response exceeds byte limit")
    content_type = _clean(getattr(response, "headers", {}).get("content-type")).lower()
    if kind == "json":
        # The audited municipal JSON handlers currently declare text/html even
        # though their bodies are strict JSON.  Only these two exact allowlisted
        # routes receive this compatibility exception.
        if content_type and "json" not in content_type and "text/html" not in content_type:
            raise JeungpyeongBicycleContractError("JSON route content type changed")
        try:
            return response.json()
        except Exception as exc:
            raise JeungpyeongBicycleContractError("invalid public JSON response") from exc
    if content_type and "html" not in content_type:
        raise JeungpyeongBicycleContractError("HTML route content type changed")
    return str(getattr(response, "text", ""))


def _info_proof(html: str) -> tuple[str, ...]:
    soup = BeautifulSoup(html, "html.parser")
    text = _clean(soup.get_text(" ", strip=True))
    required = (
        "자전거 교통안전교육장",
        "3월~12월",
        "10:00",
        "13:00",
        "자전거타기 실습",
        "남하용강로 16",
    )
    if not all(value in text for value in required):
        raise JeungpyeongBicycleContractError("official programme detail proof changed")
    return required


def _shell_proof(html: str) -> tuple[str, ...]:
    soup = BeautifulSoup(html, "html.parser")
    text = _clean(soup.get_text(" ", strip=True))
    form = soup.select_one("form#searchForm")
    action = _clean(form.get("action")) if form else ""
    required_routes = (
        "/kor/prog/bcyclParkResve/sub05_03_09_02/getTime.do",
        "/kor/prog/bcyclParkResve/sub05_03_09_02/getCalendar.do",
    )
    if not all(value in html for value in required_routes):
        raise JeungpyeongBicycleContractError("public calendar route proof changed")
    if not action.endswith("/write.do") or "예약정원(40명)" not in text:
        raise JeungpyeongBicycleContractError("calendar capacity/application proof changed")
    return (*required_routes, action, "예약정원(40명)")


def _parse_times(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise JeungpyeongBicycleContractError("time ledger is empty or malformed")
    times: list[str] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"resve_time"}:
            raise JeungpyeongBicycleContractError("time ledger shape changed")
        current = _clean(item.get("resve_time"))
        try:
            parsed = time.fromisoformat(current)
        except ValueError as exc:
            raise JeungpyeongBicycleContractError("invalid programme time") from exc
        if parsed.second or parsed.microsecond:
            raise JeungpyeongBicycleContractError("programme time precision changed")
        times.append(parsed.strftime("%H:%M"))
    if tuple(times) != ("10:00", "13:00"):
        raise JeungpyeongBicycleContractError("official programme times changed")
    return tuple(times)


def _parse_slot_marker(value: Any) -> tuple[int, str]:
    match = _CAPACITY_STATE_RE.fullmatch(_clean(value))
    if not match:
        raise JeungpyeongBicycleContractError("calendar slot marker changed")
    capacity_marker = int(match.group(1))
    if not 0 <= capacity_marker <= JEUNGPYEONG_BICYCLE_CAPACITY:
        raise JeungpyeongBicycleContractError("calendar capacity marker is out of range")
    return capacity_marker, match.group(2)


def _parse_calendar(value: Any, year: int, month: int, times: tuple[str, ...]) -> tuple[_CalendarDay, ...]:
    expected_days = calendar.monthrange(year, month)[1]
    if not isinstance(value, Mapping) or set(value) != {str(day) for day in range(1, expected_days + 1)}:
        raise JeungpyeongBicycleContractError("calendar does not contain the exact month day set")
    result: list[_CalendarDay] = []
    for day_number in range(1, expected_days + 1):
        item = value[str(day_number)]
        if not isinstance(item, Mapping):
            raise JeungpyeongBicycleContractError("calendar day is not a mapping")
        service_date = date(year, month, day_number)
        if _clean(item.get("DT")) != service_date.isoformat():
            raise JeungpyeongBicycleContractError("calendar date identity changed")
        if _clean(item.get("DD")) != f"{day_number:02d}":
            raise JeungpyeongBicycleContractError("calendar day label changed")
        if _clean(item.get("length")) != str(expected_days):
            raise JeungpyeongBicycleContractError("calendar month length changed")
        expected_weekday = (service_date.weekday() + 1) % 7
        try:
            source_weekday = int(_clean(item.get("D")))
        except ValueError as exc:
            raise JeungpyeongBicycleContractError("calendar weekday is invalid") from exc
        if source_weekday != expected_weekday:
            raise JeungpyeongBicycleContractError("calendar weekday/date mismatch")
        source_type = _clean(item.get("type"))
        if source_type not in _ALLOWED_TYPES:
            raise JeungpyeongBicycleContractError("unknown calendar source type")
        raw_slots = item.get("nmpr")
        slots: list[tuple[str, int, str]] = []
        if raw_slots is not None:
            if not isinstance(raw_slots, Mapping) or set(raw_slots) != set(times):
                raise JeungpyeongBicycleContractError("calendar time-slot set changed")
            for programme_time in times:
                capacity_marker, state_marker = _parse_slot_marker(raw_slots[programme_time])
                if source_type == "D" and (
                    capacity_marker != JEUNGPYEONG_BICYCLE_CAPACITY or state_marker != "D"
                ):
                    raise JeungpyeongBicycleContractError("scheduled slot marker changed")
                if source_type == "A" and capacity_marker == JEUNGPYEONG_BICYCLE_CAPACITY and state_marker != "D":
                    raise JeungpyeongBicycleContractError("open slot marker changed")
                slots.append((programme_time, capacity_marker, state_marker))
        elif source_type in {"A", "D"}:
            raise JeungpyeongBicycleContractError("active/scheduled day lost its time slots")
        result.append(
            _CalendarDay(
                service_date=service_date,
                weekday=source_weekday,
                source_type=source_type,
                close_type=_clean(item.get("closeType")),
                slots=tuple(slots),
            )
        )
    return tuple(result)


def _calendar_fingerprint(days: Iterable[_CalendarDay]) -> str:
    values = [
        {
            "date": item.service_date.isoformat(),
            "weekday": item.weekday,
            "type": item.source_type,
            "slots": item.slots,
        }
        for item in days
    ]
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _status(day: _CalendarDay, capacity_marker: int, state_marker: str) -> tuple[str, str, bool]:
    if day.source_type == "D":
        return "SCHEDULED", "예정", False
    if day.source_type == "C":
        return "CLOSED", "마감", False
    if capacity_marker == JEUNGPYEONG_BICYCLE_CAPACITY:
        return "OPEN", "예약가능", True
    if state_marker == "S":
        return "CLOSED", "예약완료", False
    return "CLOSED", "예약대기", False


def _end_time(programme_time: str) -> str:
    start = datetime.combine(date(2000, 1, 1), time.fromisoformat(programme_time))
    return (start + timedelta(minutes=90)).strftime("%H:%M")


def _row(
    day: _CalendarDay,
    programme_time: str,
    capacity_marker: int,
    state_marker: str,
) -> dict[str, Any]:
    status, source_status, available = _status(day, capacity_marker, state_marker)
    iso_date = day.service_date.isoformat()
    audience = "5세 이상 어린이~초등학교 1~3학년"
    if programme_time == "13:00":
        audience += ", 성인"
    identity = f"{day.service_date:%Y%m%d}-{programme_time.replace(':', '')}"
    return {
        "provider": JEUNGPYEONG_BICYCLE_PROVIDER,
        "provider_course_id": f"{JEUNGPYEONG_BICYCLE_PROVIDER}:slot:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": f"자전거 교통안전 체험교육 ({programme_time})",
        "branch": JEUNGPYEONG_BICYCLE_BRANCH,
        "branch_code": JEUNGPYEONG_BICYCLE_BRANCH_CODE,
        "preserve_branch": True,
        "provider_organizer": "증평군",
        "venue": JEUNGPYEONG_BICYCLE_BRANCH,
        "venue_name": JEUNGPYEONG_BICYCLE_BRANCH,
        "address": JEUNGPYEONG_BICYCLE_ADDRESS,
        "region_sido": "충청북도",
        "region_sigungu": "증평군",
        "region_full_name": JEUNGPYEONG_BICYCLE_MUNICIPALITY_NAME,
        "municipality_code": JEUNGPYEONG_BICYCLE_MUNICIPALITY_CODE,
        "municipality_full_name": JEUNGPYEONG_BICYCLE_MUNICIPALITY_NAME,
        "category": "자전거 교통안전 체험교육",
        "category_raw": "자전거공원 예약",
        "program_type": "체험",
        "target": audience,
        "raw_url": JEUNGPYEONG_BICYCLE_URL,
        "source_url": JEUNGPYEONG_BICYCLE_URL,
        "application_url": JEUNGPYEONG_BICYCLE_URL if available else "",
        "application_type": "WEB" if available else "INFO_ONLY",
        "reservation_available": available,
        "status": status,
        "status_raw": source_status,
        "fee": "무료",
        "period": iso_date,
        "start_date": iso_date,
        "end_date": iso_date,
        "start_time": programme_time,
        "end_time": _end_time(programme_time),
        "capacity": str(JEUNGPYEONG_BICYCLE_CAPACITY),
        "description": "이론·시청각 안전교육과 자전거 주행 실습으로 구성된 90분 무료 체험교육",
        "source_group": "municipal_reservation",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "service_group": "체험",
        "service_group_policy": "locked",
        "service_family": "experience",
        "operator_type": "지자체/공공기관",
        "collection_type": JEUNGPYEONG_BICYCLE_PARSER,
        "raw_fields": {
            "service_date": iso_date,
            "programme_time": programme_time,
            "calendar_type": day.source_type,
            "capacity_marker": capacity_marker,
            "state_marker": state_marker,
            "source_status": source_status,
            "classification_locked": True,
            "service_family": "experience",
            "official_detail_verified": True,
            "official_time_ledger_verified": True,
        },
    }


def _contains_forbidden_output(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in _FORBIDDEN_OUTPUT_KEYS or _contains_forbidden_output(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_output(child) for child in value)
    if isinstance(value, str):
        return bool(_EMAIL_RE.search(value) or _PHONE_RE.search(value))
    return False


def _output_fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        "|".join(
            (
                _clean(row.get("provider_course_id")),
                _clean(row.get("status")),
                "1" if row.get("reservation_available") else "0",
            )
        )
        for row in rows
    ]
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def collect_jeungpyeong_bicycle_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 12,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "municipality_code": JEUNGPYEONG_BICYCLE_MUNICIPALITY_CODE,
        "owner_provider": JEUNGPYEONG_BICYCLE_PROVIDER,
        "canonical_url": JEUNGPYEONG_BICYCLE_URL,
        "ownership_evidence_url": JEUNGPYEONG_BICYCLE_INFO_URL,
        "parser": JEUNGPYEONG_BICYCLE_PARSER,
        "calendar_pages": 0,
        "data_months": 0,
        "sentinel_pages": 0,
        "stable_rechecks": 0,
        "static_detail_pages": 0,
        "time_ledger_pages": 0,
        "source_day_count": 0,
        "current_source_day_count": 0,
        "excluded_day_count": 0,
        "excluded_reason_counts": {},
        "returned_count": 0,
        "experience_rows": 0,
        "status_counts": {},
        "application_urls": 0,
        "write_endpoint_requests": 0,
        "lookup_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "auth_endpoint_requests": 0,
        "member_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "file_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "duplicate_count": 0,
        "classification_complete": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "configured_collection_error": "",
        "errors": [],
    }
    if not is_jeungpyeong_bicycle_experience_target(target):
        meta["configured_collection_error"] = "target/provider failed exact contract"
        return [], JEUNGPYEONG_BICYCLE_PARSER, meta
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in (timeout, max_pages, detail_limit)
    ):
        meta["configured_collection_error"] = "invalid collection limits"
        return [], JEUNGPYEONG_BICYCLE_PARSER, meta

    cutoff = _today(today)
    first_month = max(JEUNGPYEONG_BICYCLE_OPEN_MONTH, cutoff.month)
    source_months = list(range(first_month, JEUNGPYEONG_BICYCLE_CLOSE_MONTH + 1))
    if max_pages < len(source_months) + 1:
        meta["configured_collection_error"] = "max_pages truncates operating season and sentinel"
        return [], JEUNGPYEONG_BICYCLE_PARSER, meta

    session = (session_factory or _session)()
    try:
        info_html = _request(session, "GET", JEUNGPYEONG_BICYCLE_INFO_URL, None, timeout)
        info_proof = _info_proof(info_html)
        shell_html = _request(session, "GET", JEUNGPYEONG_BICYCLE_URL, None, timeout)
        shell_proof = _shell_proof(shell_html)
        meta["static_detail_pages"] = 2

        times = _parse_times(
            _request(session, "POST", JEUNGPYEONG_BICYCLE_TIME_URL, {}, timeout)
        )
        meta["time_ledger_pages"] += 1

        month_days: dict[int, tuple[_CalendarDay, ...]] = {}
        for month in source_months:
            value = _request(
                session,
                "POST",
                JEUNGPYEONG_BICYCLE_CALENDAR_URL,
                {"yearMonth": f"{cutoff.year}{month:02d}"},
                timeout,
            )
            meta["calendar_pages"] += 1
            month_days[month] = _parse_calendar(value, cutoff.year, month, times)

        sentinel_year = cutoff.year + 1
        sentinel = _parse_calendar(
            _request(
                session,
                "POST",
                JEUNGPYEONG_BICYCLE_CALENDAR_URL,
                {"yearMonth": f"{sentinel_year}01"},
                timeout,
            ),
            sentinel_year,
            1,
            times,
        )
        meta["calendar_pages"] += 1
        if any(item.source_type != "D" for item in sentinel):
            raise JeungpyeongBicycleContractError("off-season sentinel became active")
        meta["sentinel_pages"] = 1

        info_check = _info_proof(
            _request(session, "GET", JEUNGPYEONG_BICYCLE_INFO_URL, None, timeout)
        )
        shell_check = _shell_proof(
            _request(session, "GET", JEUNGPYEONG_BICYCLE_URL, None, timeout)
        )
        times_check = _parse_times(
            _request(session, "POST", JEUNGPYEONG_BICYCLE_TIME_URL, {}, timeout)
        )
        meta["static_detail_pages"] += 2
        meta["time_ledger_pages"] += 1

        first_check = _parse_calendar(
            _request(
                session,
                "POST",
                JEUNGPYEONG_BICYCLE_CALENDAR_URL,
                {"yearMonth": f"{cutoff.year}{source_months[0]:02d}"},
                timeout,
            ),
            cutoff.year,
            source_months[0],
            times,
        )
        last_check = _parse_calendar(
            _request(
                session,
                "POST",
                JEUNGPYEONG_BICYCLE_CALENDAR_URL,
                {"yearMonth": f"{cutoff.year}{source_months[-1]:02d}"},
                timeout,
            ),
            cutoff.year,
            source_months[-1],
            times,
        )
        sentinel_check = _parse_calendar(
            _request(
                session,
                "POST",
                JEUNGPYEONG_BICYCLE_CALENDAR_URL,
                {"yearMonth": f"{sentinel_year}01"},
                timeout,
            ),
            sentinel_year,
            1,
            times,
        )
        meta["calendar_pages"] += 3
        if (
            info_check != info_proof
            or shell_check != shell_proof
            or times_check != times
            or _calendar_fingerprint(first_check) != _calendar_fingerprint(month_days[source_months[0]])
            or _calendar_fingerprint(last_check) != _calendar_fingerprint(month_days[source_months[-1]])
            or _calendar_fingerprint(sentinel_check) != _calendar_fingerprint(sentinel)
        ):
            raise JeungpyeongBicycleContractError("stable source boundary recheck changed")
        meta["stable_rechecks"] = 6
        meta["data_months"] = len(source_months)
        meta["pagination_complete"] = True
        meta["details_complete"] = True

        all_days = [item for month in source_months for item in month_days[month]]
        meta["source_day_count"] = len(all_days)
        meta["current_source_day_count"] = sum(item.service_date >= cutoff for item in all_days)
        excluded: Counter[str] = Counter()
        candidates: list[dict[str, Any]] = []
        for day in all_days:
            if day.service_date < cutoff:
                excluded["before_cutoff"] += 1
                continue
            if day.weekday in {0, 6}:
                excluded["weekend_facility_closure"] += 1
                continue
            if day.source_type == "B":
                excluded["explicit_calendar_closure"] += 1
                continue
            if day.source_type == "C":
                slots = tuple((programme_time, 0, "C") for programme_time in times)
            else:
                slots = day.slots
            for programme_time, capacity_marker, state_marker in slots:
                candidates.append(_row(day, programme_time, capacity_marker, state_marker))

        result = list(dedupe_rows([], candidates) if dedupe_rows else candidates)
        identities = [_clean(row.get("provider_course_id")) for row in result]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count or any(not value.startswith(f"{JEUNGPYEONG_BICYCLE_PROVIDER}:") for value in identities):
            raise JeungpyeongBicycleContractError("slot identity contract changed")
        if len(result) != len(candidates):
            raise JeungpyeongBicycleContractError("dedupe changed complete slot ledger")
        if any(bool(row.get("application_url")) != bool(row.get("reservation_available")) for row in result):
            raise JeungpyeongBicycleContractError("application URL/availability contract changed")
        if any(_contains_forbidden_output(row) for row in result):
            raise JeungpyeongBicycleContractError("PII/contact data reached output")
        if not result or any(row.get("service_family") != "experience" for row in result):
            raise JeungpyeongBicycleContractError("experience classification contract changed")

        meta["excluded_day_count"] = sum(excluded.values())
        meta["excluded_reason_counts"] = dict(excluded)
        meta["returned_count"] = len(result)
        meta["experience_rows"] = len(result)
        meta["status_counts"] = dict(Counter(_clean(row.get("status")) for row in result))
        meta["application_urls"] = sum(bool(row.get("application_url")) for row in result)
        meta["duplicate_count"] = duplicate_count
        meta["source_identity_hash"] = hashlib.sha256(
            "\n".join(_calendar_fingerprint(month_days[month]) for month in source_months).encode("utf-8")
        ).hexdigest()
        meta["output_identity_hash"] = _output_fingerprint(result)
        meta["classification_complete"] = (
            meta["excluded_day_count"]
            + len({row["start_date"] for row in result})
            == len(all_days)
        )
        meta["snapshot_complete"] = bool(
            meta["pagination_complete"]
            and meta["details_complete"]
            and meta["classification_complete"]
            and meta["sentinel_pages"] == 1
            and meta["stable_rechecks"] == 6
            and meta["experience_rows"] > 0
        )
        meta["full_snapshot_validated"] = meta["snapshot_complete"]
        if not meta["snapshot_complete"]:
            raise JeungpyeongBicycleContractError("complete snapshot proof failed")
        return result, JEUNGPYEONG_BICYCLE_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["errors"] = [meta["configured_collection_error"]]
        meta["returned_count"] = 0
        meta["experience_rows"] = 0
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], JEUNGPYEONG_BICYCLE_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_jeungpyeong_bicycle_experience


__all__ = [
    "JEUNGPYEONG_BICYCLE_CALENDAR_URL",
    "JEUNGPYEONG_BICYCLE_CANDIDATE_ID",
    "JEUNGPYEONG_BICYCLE_INFO_URL",
    "JEUNGPYEONG_BICYCLE_PARSER",
    "JEUNGPYEONG_BICYCLE_PROVIDER",
    "JEUNGPYEONG_BICYCLE_TIME_URL",
    "JEUNGPYEONG_BICYCLE_URL",
    "JeungpyeongBicycleContractError",
    "collect",
    "collect_jeungpyeong_bicycle_experience",
    "is_jeungpyeong_bicycle_experience_target",
]
