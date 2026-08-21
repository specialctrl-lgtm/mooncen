"""Read-only, fail-closed collector for the RDA museum reservation centre.

The agricultural-science museum notice board is not a reservation catalogue:
it mixes closures, operating notices, result announcements, and occasional
programme publicity.  This collector therefore reads only the official
reservation-centre introduction, booking form, and the form's public
JavaScript contract.  It never calls the approved-reservation calendar,
occupancy checks, applicant checks, or the application submission endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from Crawler.Crawler_MunicipalYaml import CrawlTarget, dedupe_rows, fetch_soup, session
from utils import clean_text


RDA_PROVIDER = "RURAL_DEVELOPMENT_ADMINISTRATION"
RDA_HOST = "www.rda.go.kr"
RDA_RESERVATION_PATH = "/aehBoard/aehBoardCenterIns.do"
RDA_RESERVATION_URL = f"https://{RDA_HOST}{RDA_RESERVATION_PATH}"
RDA_INTRO_PATH = "/aehBoard/aehBoardCenterIntro.do"
RDA_INTRO_URL = f"https://{RDA_HOST}{RDA_INTRO_PATH}"
RDA_FORM_SCRIPT_PATH = "/js/uiux2025/aeh/ati/ati_reservationCenterIns.js"
RDA_BRANCH = "농촌진흥청 농업과학관"
RDA_BRANCH_CODE = "rda-agricultural-science-museum"
RDA_MUNICIPALITY_CODE = "5211300000"
RDA_MUNICIPALITY_NAME = "전북특별자치도 전주시 덕진구"
RDA_PARSER = (
    "rda_reservation_center_get_catalogue+public_schedule_contract+"
    "no_calendar_occupancy_or_application_calls"
)

RDA_WEEKDAY_TIMES = (
    "10:00",
    "11:00",
    "13:00",
    "14:00",
    "15:00",
    "16:00",
)
RDA_SATURDAY_TIMES = ("15:30",)
RDA_SUNDAY_TIMES = ("10:30", "14:30")

_EXPECTED_PROGRAMS = {
    "자유": "자유관람",
    "전시": "전시해설",
}
_PROHIBITED_ENDPOINTS = (
    "/aehBoard/ati_reservationCalenderAjax.do",
    "/aehBoard/reserverCheck.do",
    "/aehBoard/reserverCheck2.do",
    "/aehBoard/reserverCheck3.do",
    "/aehBoard/addReserveCenter.do",
)
_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}\Z")


class RdaReservationContractError(ValueError):
    """The public booking form no longer matches the reviewed contract."""


@dataclass(frozen=True)
class RdaReservationContract:
    start_date: date
    end_date: date
    holidays: frozenset[date]
    weekday_times: tuple[str, ...]
    saturday_times: tuple[str, ...]
    sunday_times: tuple[str, ...]
    weekend_capacity: int
    free_visit_minimum_group_size: int
    script_url: str


def _compact(value: Any) -> str:
    return _SPACE_RE.sub("", clean_text(value))


def _validate_target(target: CrawlTarget) -> None:
    parsed = urlparse(clean_text(target.url))
    if (
        target.provider != RDA_PROVIDER
        or parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != RDA_HOST
        or parsed.port is not None
        or parsed.path != RDA_RESERVATION_PATH
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("target is not the canonical RDA reservation-centre GET form")


def _page_text(soup: Any) -> str:
    return _SPACE_RE.sub(" ", soup.get_text(" ", strip=True)).strip()


def _inline_script_text(soup: Any) -> str:
    return "\n".join(
        script.get_text("\n", strip=False)
        for script in soup.select("script:not([src])")
    )


def _single_script_date(script_text: str, variable: str) -> date:
    values = re.findall(
        rf"\bvar\s+{re.escape(variable)}\s*=\s*['\"]"
        r"(20\d{2}-\d{2}-\d{2})['\"]\s*;",
        script_text,
    )
    if len(values) != 1:
        raise RdaReservationContractError(
            f"expected one public {variable} assignment, found {len(values)}"
        )
    try:
        return date.fromisoformat(values[0])
    except ValueError as exc:
        raise RdaReservationContractError(
            f"invalid public {variable}: {values[0]!r}"
        ) from exc


def _booking_script_url(form_soup: Any) -> str:
    urls: list[str] = []
    for node in form_soup.select("script[src]"):
        raw_url = clean_text(node.get("src"))
        absolute_url = urljoin(RDA_RESERVATION_URL, raw_url)
        parsed = urlparse(absolute_url)
        if parsed.path != RDA_FORM_SCRIPT_PATH:
            continue
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").rstrip(".").lower() != RDA_HOST
            or parsed.port is not None
            or parsed.params
            or parsed.fragment
        ):
            raise RdaReservationContractError("booking script escaped the official host")
        if parsed.query and not re.fullmatch(r"ver=\d{8,20}", parsed.query):
            raise RdaReservationContractError("unexpected booking script query")
        urls.append(absolute_url)
    if len(urls) != 1:
        raise RdaReservationContractError(
            f"expected one official booking script, found {len(urls)}"
        )
    return urls[0]


def _program_contract(form_soup: Any) -> None:
    form = form_soup.select_one("form#reserveForm")
    if form is None:
        raise RdaReservationContractError("booking form #reserveForm is missing")

    programmes: dict[str, str] = {}
    for control in form.select("input[type='radio'][name='program_term'][value]"):
        value = clean_text(control.get("value"))
        control_id = clean_text(control.get("id"))
        label = form_soup.select_one(f'label[for="{control_id}"]') if control_id else None
        programmes[value] = clean_text(label.get_text(" ", strip=True)) if label else ""
    if programmes != _EXPECTED_PROGRAMS:
        raise RdaReservationContractError(
            f"unexpected reservation programme catalogue: {programmes!r}"
        )


def _display_time(value: str) -> str:
    match = re.fullmatch(
        r"(오전|오후)\s*(\d{1,2})시(?:\s*(\d{1,2})분)?",
        clean_text(value),
    )
    if not match:
        raise RdaReservationContractError(f"unexpected reservation time: {value!r}")
    hour = int(match.group(2))
    minute = int(match.group(3) or 0)
    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        raise RdaReservationContractError(f"invalid reservation time: {value!r}")
    if match.group(1) == "오전":
        hour = 0 if hour == 12 else hour
    else:
        hour = hour if hour == 12 else hour + 12
    return f"{hour:02d}:{minute:02d}"


def _form_weekday_times(form_soup: Any) -> tuple[str, ...]:
    controls = form_soup.select("form#reserveForm select#time[name='time']")
    if len(controls) != 1:
        raise RdaReservationContractError(
            f"expected one public session selector, found {len(controls)}"
        )
    values = tuple(
        _display_time(clean_text(option.get("value")))
        for option in controls[0].select("option[value]")
        if clean_text(option.get("value"))
    )
    if values != RDA_WEEKDAY_TIMES:
        raise RdaReservationContractError(
            f"weekday session contract changed: {values!r}"
        )
    return values


def _read_only_script_text(client: Any, url: str, *, timeout: int) -> str:
    """Fetch the public JavaScript asset with GET only and strict bounds."""

    response = client.get(url, timeout=timeout)
    response.raise_for_status()
    content = bytes(response.content or b"")
    if not content:
        raise RdaReservationContractError("empty booking JavaScript response")
    if len(content) > 1_000_000:
        raise RdaReservationContractError("booking JavaScript exceeded the size limit")
    content_type = clean_text(response.headers.get("content-type")).lower()
    if "javascript" not in content_type:
        raise RdaReservationContractError(
            f"unexpected booking JavaScript content type: {content_type!r}"
        )
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RdaReservationContractError(
            "booking JavaScript is not valid UTF-8"
        ) from exc


def _javascript_session_block(script_text: str, number: int) -> str:
    compact = re.sub(r"\s+", "", script_text)
    match = re.search(
        rf"if\(no=={number}\)\{{(?P<body>.*?)\}}"
        r"(?=if\(no==\d+\)|\}functiontypechange\(|\}functionsmtAlert\(|$)",
        compact,
    )
    if not match:
        raise RdaReservationContractError(
            f"public session rule doAgeSet({number}) is missing"
        )
    return match.group("body")


def _javascript_times(script_text: str, number: int) -> tuple[str, ...]:
    block = _javascript_session_block(script_text, number)
    raw_values = re.findall(
        r"<optionvalue=[\"']([^\"']+)[\"']>",
        block,
    )
    return tuple(_display_time(value) for value in raw_values if clean_text(value))


def _javascript_contract(script_text: str) -> tuple[frozenset[date], tuple[str, ...], tuple[str, ...], tuple[str, ...], int]:
    compact = re.sub(r"\s+", "", script_text)

    day_rule = re.search(
        r"if\(type==[\"']자유[\"']\)\{(?P<free>.*?)\}else\{(?P<guided>.*?)\}",
        compact,
    )
    if not day_rule:
        raise RdaReservationContractError("public programme weekday rules are missing")
    if set(re.findall(r"day!=(\d)", day_rule.group("free"))) != {"0", "1", "6"}:
        raise RdaReservationContractError("free-visit weekday rule changed")
    if set(re.findall(r"day!=(\d)", day_rule.group("guided"))) != {"1"}:
        raise RdaReservationContractError("guided-tour weekday rule changed")

    dispatch = re.search(
        r"if\(programTerm==[\"']전시[\"']&&day==[\"']6[\"']\)"
        r"\{doAgeSet\(4\);?\}elseif\(programTerm==[\"']전시[\"']&&"
        r"day==[\"']0[\"']\)\{doAgeSet\(5\);?\}else\{doAgeSet\(6\);?\}",
        compact,
    )
    if not dispatch:
        raise RdaReservationContractError("public weekend session dispatch changed")

    holiday_matches = re.findall(
        r"varholiDays=(\[[^;]*\]);",
        compact,
    )
    if len(holiday_matches) != 1:
        raise RdaReservationContractError("public holiday contract is missing or ambiguous")
    try:
        raw_holidays = json.loads(holiday_matches[0].replace("'", '"'))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RdaReservationContractError("public holiday contract is malformed") from exc
    if not isinstance(raw_holidays, list) or any(
        not isinstance(value, str) or not _DATE_RE.fullmatch(value)
        for value in raw_holidays
    ):
        raise RdaReservationContractError("public holiday values are malformed")
    try:
        holidays = frozenset(date.fromisoformat(value) for value in raw_holidays)
    except ValueError as exc:
        raise RdaReservationContractError("public holiday date is invalid") from exc

    weekday_times = _javascript_times(script_text, 6)
    saturday_times = _javascript_times(script_text, 4)
    sunday_times = _javascript_times(script_text, 5)
    if weekday_times != RDA_WEEKDAY_TIMES:
        raise RdaReservationContractError("JavaScript weekday sessions changed")
    if saturday_times != RDA_SATURDAY_TIMES:
        raise RdaReservationContractError("JavaScript Saturday sessions changed")
    if sunday_times != RDA_SUNDAY_TIMES:
        raise RdaReservationContractError("JavaScript Sunday sessions changed")

    capacity_matches = re.findall(r"varmaxCount=(\d+);", compact)
    capacities = {int(value) for value in capacity_matches}
    if capacities != {20}:
        raise RdaReservationContractError(
            f"unexpected public weekend capacity contract: {capacities!r}"
        )
    return holidays, weekday_times, saturday_times, sunday_times, 20


def _intro_weekend_contract(intro_soup: Any) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
    compact = _compact(_page_text(intro_soup))
    summary = re.search(
        r"주말전시해설은총(\d+)회진행되며,?정원은(\d+)명\(1회당\)입니다",
        compact,
    )
    schedule = re.search(
        r"토(\d{1,2})시(\d{1,2})분,?일(\d{1,2})시(\d{1,2})분,?"
        r"(\d{1,2})시(\d{1,2})분",
        compact,
    )
    if not summary or not schedule:
        raise RdaReservationContractError("official weekend guide summary is missing")
    advertised_count = int(summary.group(1))
    capacity = int(summary.group(2))
    saturday = (f"{int(schedule.group(1)):02d}:{int(schedule.group(2)):02d}",)
    sunday = (
        f"{int(schedule.group(3)):02d}:{int(schedule.group(4)):02d}",
        f"{int(schedule.group(5)):02d}:{int(schedule.group(6)):02d}",
    )
    if advertised_count != len(saturday) + len(sunday):
        raise RdaReservationContractError("weekend guide count/session mismatch")
    return capacity, saturday, sunday


def _minimum_group_size(form_soup: Any) -> int:
    compact = _compact(_page_text(form_soup))
    match = re.search(r"자유관람은단체\((\d+)인이상\)인경우만예약", compact)
    if not match:
        raise RdaReservationContractError("free-visit minimum group size is missing")
    return int(match.group(1))


def parse_rda_reservation_contract(
    intro_soup: Any,
    form_soup: Any,
    script_text: str,
    *,
    today: date,
) -> RdaReservationContract:
    """Validate the three public GET resources as one atomic contract."""

    _program_contract(form_soup)
    form_weekday_times = _form_weekday_times(form_soup)
    script_url = _booking_script_url(form_soup)
    inline_script = _inline_script_text(form_soup)
    start_date = _single_script_date(inline_script, "startDay")
    end_date = _single_script_date(inline_script, "untilDay")
    if start_date != today:
        raise RdaReservationContractError(
            f"public reservation window starts at {start_date}, expected {today}"
        )
    window_days = (end_date - start_date).days
    if not 30 <= window_days <= 93:
        raise RdaReservationContractError(
            f"unexpected public reservation window length: {window_days} days"
        )

    holidays, weekday_times, saturday_times, sunday_times, script_capacity = (
        _javascript_contract(script_text)
    )
    intro_capacity, intro_saturday, intro_sunday = _intro_weekend_contract(intro_soup)
    if form_weekday_times != weekday_times:
        raise RdaReservationContractError("form/JavaScript weekday session mismatch")
    if intro_saturday != saturday_times or intro_sunday != sunday_times:
        raise RdaReservationContractError("introduction/JavaScript weekend session mismatch")
    if intro_capacity != script_capacity:
        raise RdaReservationContractError("introduction/JavaScript capacity mismatch")

    minimum_group_size = _minimum_group_size(form_soup)
    if minimum_group_size != 10:
        raise RdaReservationContractError(
            f"unexpected free-visit minimum group size: {minimum_group_size}"
        )
    return RdaReservationContract(
        start_date=start_date,
        end_date=end_date,
        holidays=holidays,
        weekday_times=weekday_times,
        saturday_times=saturday_times,
        sunday_times=sunday_times,
        weekend_capacity=intro_capacity,
        free_visit_minimum_group_size=minimum_group_size,
        script_url=script_url,
    )


def _session_id(program_code: str, session_date: date, session_time: str) -> str:
    seed = f"{RDA_PROVIDER}|{program_code}|{session_date.isoformat()}|{session_time}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _session_row(
    target: CrawlTarget,
    contract: RdaReservationContract,
    *,
    program_code: str,
    program_value: str,
    program_label: str,
    session_date: date,
    session_time: str,
    capacity_total: int | None,
) -> dict[str, Any]:
    day = session_date.isoformat()
    is_free_visit = program_code == "free_visit"
    row: dict[str, Any] = {
        "provider": target.provider,
        "provider_course_id": _session_id(program_code, session_date, session_time),
        "title": f"농업과학관 {program_label}",
        "branch": target.branch or RDA_BRANCH,
        "branch_code": RDA_BRANCH_CODE,
        "preserve_branch": True,
        "category": program_label,
        "category_raw": program_label,
        "collection_category": "박물관/과학관",
        "domain_category": "박물관/과학관",
        "source_group": "museum_science",
        "operator_type": "국립/공공기관",
        "collection_type": "official_reservation_form_get",
        "service_group": "체험",
        "service_group_policy": "locked",
        "program_type": "관람" if is_free_visit else "전시해설",
        "municipality_code": RDA_MUNICIPALITY_CODE,
        "municipality_full_name": RDA_MUNICIPALITY_NAME,
        "municipality_region_verified": True,
        "raw_url": RDA_RESERVATION_URL,
        # Occupancy is checked only by POST endpoints in the browser.  We do
        # not call them and therefore must not represent these slots as OPEN.
        "status": "SCHEDULED",
        "status_raw": "공개 예약폼 회차(잔여 정원 미조회)",
        "reservation_available": False,
        "application_type": "EXTERNAL_FORM",
        "discovery_status": "official_reservation_slot_occupancy_unchecked",
        "period": f"{day} ~ {day}",
        "schedule_raw": f"{day} {session_time}",
        "start_date": day,
        "end_date": day,
        "venue_name": target.branch or RDA_BRANCH,
        "target": (
            f"{contract.free_visit_minimum_group_size}인 이상 단체"
            if is_free_visit
            else ""
        ),
        "description": (
            "농촌진흥청 농업과학관 공식 예약센터가 공개한 "
            f"{program_label} 선택 가능 일자·회차입니다. "
            "신청·점유 확인 API를 호출하지 않아 잔여 여부는 표시하지 않습니다."
        ),
        "raw_fields": {
            "parser": RDA_PARSER,
            "program_code": program_code,
            "program_value": program_value,
            "program_label": program_label,
            "session_date": day,
            "session_time": session_time,
            "public_window_start": contract.start_date.isoformat(),
            "public_window_end": contract.end_date.isoformat(),
            "occupancy_checked": False,
            "approved_reservation_calendar_called": False,
            "application_endpoint_called": False,
        },
    }
    if capacity_total is not None:
        row["capacity"] = f"{capacity_total}명"
        row["capacity_total"] = capacity_total
    if is_free_visit:
        row["raw_fields"]["minimum_group_size"] = (
            contract.free_visit_minimum_group_size
        )
    return row


def _contract_rows(
    target: CrawlTarget,
    contract: RdaReservationContract,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current = contract.start_date
    while current <= contract.end_date:
        if current in contract.holidays:
            current += timedelta(days=1)
            continue
        weekday = current.weekday()  # Monday=0, Sunday=6

        if weekday in {1, 2, 3, 4}:  # Tuesday-Friday
            for session_time in contract.weekday_times:
                rows.append(
                    _session_row(
                        target,
                        contract,
                        program_code="free_visit",
                        program_value="자유",
                        program_label="자유관람",
                        session_date=current,
                        session_time=session_time,
                        capacity_total=None,
                    )
                )

        guided_times: tuple[str, ...] = ()
        guided_capacity: int | None = None
        if weekday in {1, 2, 3, 4}:
            guided_times = contract.weekday_times
        elif weekday == 5:
            guided_times = contract.saturday_times
            guided_capacity = contract.weekend_capacity
        elif weekday == 6:
            guided_times = contract.sunday_times
            guided_capacity = contract.weekend_capacity
        for session_time in guided_times:
            rows.append(
                _session_row(
                    target,
                    contract,
                    program_code="guided_tour",
                    program_value="전시",
                    program_label="전시해설",
                    session_date=current,
                    session_time=session_time,
                    capacity_total=guided_capacity,
                )
            )
        current += timedelta(days=1)
    return rows


def collect_rda_agricultural_science_programs(
    target: CrawlTarget,
    *,
    timeout: int,
    max_pages: int,
    today: date | None = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete public rolling reservation schedule with GETs."""

    _validate_target(target)
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    reference_date = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    client = session()
    intro_soup = fetch_soup(client, RDA_INTRO_URL, timeout=timeout)
    form_soup = fetch_soup(client, RDA_RESERVATION_URL, timeout=timeout)
    script_url = _booking_script_url(form_soup)
    script_text = _read_only_script_text(client, script_url, timeout=timeout)
    contract = parse_rda_reservation_contract(
        intro_soup,
        form_soup,
        script_text,
        today=reference_date,
    )
    rows = dedupe_rows(_contract_rows(target, contract))
    if not rows:
        raise RdaReservationContractError("validated reservation window produced no sessions")

    free_visit_count = sum(
        row["raw_fields"]["program_code"] == "free_visit" for row in rows
    )
    guided_tour_count = len(rows) - free_visit_count
    capacity_count = sum(row.get("capacity_total") is not None for row in rows)
    meta = {
        "pages": 2,
        "asset_pages": 1,
        "detail_pages": 0,
        "discovered_links": len(rows),
        "pagination_detected": False,
        "pagination_exhausted": True,
        "pagination_complete": True,
        "snapshot_complete": True,
        "recursion_depth": 0,
        "source_program_count": len(_EXPECTED_PROGRAMS),
        "source_rows": len(rows),
        "session_count": len(rows),
        "free_visit_session_count": free_visit_count,
        "guided_tour_session_count": guided_tour_count,
        "sessions_with_public_capacity": capacity_count,
        "window_start": contract.start_date.isoformat(),
        "window_end": contract.end_date.isoformat(),
        "public_holiday_count": len(contract.holidays),
        "read_only_get_endpoints": [
            RDA_INTRO_URL,
            RDA_RESERVATION_URL,
            contract.script_url,
        ],
        "excluded_post_or_private_endpoints": list(_PROHIBITED_ENDPOINTS),
        "approved_reservation_calendar_called": False,
        "occupancy_endpoints_called": False,
        "application_endpoint_called": False,
        "configured_collection_error": "",
        "no_current_data": False,
        "no_current_reason": "",
    }
    return rows, RDA_PARSER, meta


__all__ = [
    "RDA_BRANCH",
    "RDA_INTRO_URL",
    "RDA_MUNICIPALITY_CODE",
    "RDA_MUNICIPALITY_NAME",
    "RDA_PARSER",
    "RDA_PROVIDER",
    "RDA_RESERVATION_URL",
    "RdaReservationContract",
    "RdaReservationContractError",
    "collect_rda_agricultural_science_programs",
    "parse_rda_reservation_contract",
]
