"""Fail-closed collector for Yeongcheon's Oriental Village experiences.

The official Yeongcheon integrated-reservation portal exposes two independent
public calendars under its experience/visit category: footbath and herbal
experience.  The month page itself is the complete public record used here.
It exposes only a date-level reservation status; the day/application controls
are deliberately not followed.

Only HTTPS POST requests to the two audited *list* routes are allowed.  Login,
application, identity, applicant, attachment, download, and every other route
fail closed.  A snapshot is published only after both partitions reach an
exact empty month sentinel and the first/final/sentinel boundaries remain
stable on recheck.
"""

from __future__ import annotations

from calendar import monthrange
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


YEONGCHEON_EXPERIENCE_PROVIDER = "MUNI_WWW_YC_GO_KR_829A0EA1"
YEONGCHEON_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_B9028639CBE0"
YEONGCHEON_EXPERIENCE_HOST = "www.yc.go.kr"
YEONGCHEON_EXPERIENCE_URL = (
    "https://www.yc.go.kr/yeyak/oriental/footbath/list.do?mId=0307020000"
)
YEONGCHEON_EXPERIENCE_MUNICIPALITY_CODE = "4723000000"
YEONGCHEON_EXPERIENCE_MUNICIPALITY_NAME = "경상북도 영천시"
YEONGCHEON_EXPERIENCE_BRANCH = "영천한의마을"
YEONGCHEON_EXPERIENCE_ADDRESS = "경상북도 영천시 천문로 485"
YEONGCHEON_EXPERIENCE_MAX_HTML_BYTES = 1_000_000
YEONGCHEON_EXPERIENCE_PARSER = (
    "yeongcheon_oriental_village_two_experience_calendars+"
    "current_until_exact_empty_month_sentinel+stable_first_last_sentinel+"
    "date_status_identity+public_month_list_post_only+locked_experience+"
    "day_application_controls_observed_not_called+"
    "no_login_identity_applicant_attachment_download_or_pii_calls+"
    "atomic_snapshot"
)
YEONGCHEON_EXPERIENCE_OWNERSHIP_SCOPE = (
    "yeongcheon_integrated_reservation_oriental_village_two_experience_calendars"
)


@dataclass(frozen=True)
class YeongcheonExperiencePartition:
    code: str
    label: str
    path: str
    menu_id: str

    @property
    def url(self) -> str:
        return (
            f"https://{YEONGCHEON_EXPERIENCE_HOST}{self.path}?"
            + urlencode((("mId", self.menu_id),))
        )


YEONGCHEON_EXPERIENCE_PARTITIONS: tuple[
    YeongcheonExperiencePartition, ...
] = (
    YeongcheonExperiencePartition(
        "footbath",
        "족욕체험",
        "/yeyak/oriental/footbath/list.do",
        "0307020000",
    ),
    YeongcheonExperiencePartition(
        "herbal",
        "한방체험",
        "/yeyak/oriental/experience/list.do",
        "0307030000",
    ),
)
YEONGCHEON_EXPERIENCE_PARTITION_BY_CODE = {
    partition.code: partition
    for partition in YEONGCHEON_EXPERIENCE_PARTITIONS
}
YEONGCHEON_EXPERIENCE_PARTITION_BY_PATH = {
    partition.path: partition
    for partition in YEONGCHEON_EXPERIENCE_PARTITIONS
}

YEONGCHEON_EXPERIENCE_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_count": 104,
    "expired_count": 6,
    "current_count": 98,
    "open_count": 94,
    "closed_count": 4,
    "partition_counts": {"footbath": 49, "herbal": 49},
    "last_nonempty_month": "2026-09",
    "sentinel_month": "2026-10",
    "application_endpoint_requests": 0,
    "pii_endpoint_requests": 0,
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, Mapping[str, str], int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_DATE_CONTROL = re.compile(
    r"viewTheDay\('(?P<date>20\d{2}-\d{2}-\d{2})'\)\s*;?\s*"
    r"return\s+false\s*;?",
    re.IGNORECASE,
)
_MONTH_HEADING = re.compile(r"(?P<year>20\d{2})\s*[.년-]\s*(?P<month>\d{1,2})")
_STATUS_OPEN = "예약가능"
_STATUS_CLOSED = "예약불가"
_STATUS_ENDED = "예약종료"
_STATUS_HOLIDAY = "휴관일"
_RESERVATION_STATUSES = (_STATUS_OPEN, _STATUS_CLOSED, _STATUS_ENDED)
_ALL_DAY_LABELS = frozenset((*_RESERVATION_STATUSES, _STATUS_HOLIDAY))
_WEEKDAYS = ("일", "월", "화", "수", "목", "금", "토")
_SAFE_DETAIL_HEADERS = ("예약시간", "예약가능 여부", "관리")
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "staff",
        "applicant",
        "member",
        "attachment",
        "attachments",
        "download",
        "raw_html",
        "description",
        "content",
    }
)
_PHONE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class YeongcheonExperienceContractError(ValueError):
    """Raised when the audited public-calendar contract changes."""


@dataclass(frozen=True)
class _CalendarSlot:
    partition: YeongcheonExperiencePartition
    service_date: date
    source_status: str
    calendar_url: str

    @property
    def identity(self) -> str:
        return f"{self.partition.code}:{self.service_date.isoformat()}"


@dataclass(frozen=True)
class _CalendarPage:
    partition: YeongcheonExperiencePartition
    year: int
    month: int
    slots: tuple[_CalendarSlot, ...]
    holiday_dates: tuple[date, ...]
    unscheduled_dates: tuple[date, ...]
    day_control_count: int


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _parse_url(value: Any) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(_clean(value))
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    if len(pairs) != len(query):
        raise YeongcheonExperienceContractError("duplicate query key")
    if parsed.username or parsed.password or parsed.params or parsed.fragment:
        raise YeongcheonExperienceContractError("unsafe URL authority or fragment")
    try:
        if parsed.port is not None:
            raise YeongcheonExperienceContractError("explicit port is forbidden")
    except ValueError as exc:
        raise YeongcheonExperienceContractError("invalid URL port") from exc
    return parsed, query


def _same_url(left: str, right: str) -> bool:
    left_parsed, left_query = _parse_url(left)
    right_parsed, right_query = _parse_url(right)
    return bool(
        left_parsed.scheme == right_parsed.scheme
        and (left_parsed.hostname or "").lower()
        == (right_parsed.hostname or "").lower()
        and left_parsed.path == right_parsed.path
        and left_query == right_query
    )


def _request_partition(
    method: str,
    url: str,
    data: Mapping[str, str],
) -> YeongcheonExperiencePartition:
    parsed, query = _parse_url(url)
    partition = YEONGCHEON_EXPERIENCE_PARTITION_BY_PATH.get(parsed.path)
    if (
        method != "POST"
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() != YEONGCHEON_EXPERIENCE_HOST
        or partition is None
        or query != {"mId": partition.menu_id}
        or set(data) != {"year", "month"}
        or not re.fullmatch(r"20\d{2}", _clean(data.get("year")))
    ):
        raise YeongcheonExperienceContractError(
            "login/application/identity/applicant/attachment/download/PII route refused"
        )
    try:
        month = int(_clean(data.get("month")))
    except ValueError as exc:
        raise YeongcheonExperienceContractError("invalid calendar month") from exc
    if not 1 <= month <= 12 or _clean(data.get("month")) != str(month):
        raise YeongcheonExperienceContractError("invalid calendar month")
    return partition


def is_yeongcheon_experience_target(target: Any) -> bool:
    try:
        return bool(
            _clean(_target_value(target, "provider"))
            == YEONGCHEON_EXPERIENCE_PROVIDER
            and _same_url(
                _clean(_target_value(target, "url")),
                YEONGCHEON_EXPERIENCE_URL,
            )
        )
    except YeongcheonExperienceContractError:
        return False


is_target = is_yeongcheon_experience_target


def _default_session() -> requests.Session:
    current = requests.Session()
    current.trust_env = False
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_fetcher(
    current: Any,
    url: str,
    data: Mapping[str, str],
    timeout: int,
) -> Any:
    return current.post(
        url,
        data=dict(data),
        timeout=timeout,
        allow_redirects=False,
        verify=True,
    )


class _Requester:
    def __init__(
        self,
        session_factory: SessionFactory,
        fetcher: Fetcher,
        timeout: int,
        meta: dict[str, Any],
    ) -> None:
        self.session = session_factory()
        self.fetcher = fetcher
        self.timeout = timeout
        self.meta = meta

    def soup(
        self,
        partition: YeongcheonExperiencePartition,
        year: int,
        month: int,
    ) -> BeautifulSoup:
        data = {"year": str(year), "month": str(month)}
        _request_partition("POST", partition.url, data)
        self.meta["logical_requests"] += 1
        self.meta["list_requests"] += 1
        response = self.fetcher(self.session, partition.url, data, self.timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise YeongcheonExperienceContractError(f"HTTP {status}")
        if tuple(getattr(response, "history", ()) or ()):
            raise YeongcheonExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(
            str(key).lower() == "location" and value
            for key, value in headers.items()
        ):
            raise YeongcheonExperienceContractError("redirect location is forbidden")
        final_url = _clean(getattr(response, "url", ""))
        if final_url and not _same_url(final_url, partition.url):
            raise YeongcheonExperienceContractError("response URL changed")
        content_type = _clean(
            next(
                (
                    value
                    for key, value in headers.items()
                    if str(key).lower() == "content-type"
                ),
                "text/html",
            )
        ).lower()
        if "html" not in content_type:
            raise YeongcheonExperienceContractError("non-HTML calendar response")
        content = getattr(response, "content", None)
        if content is None:
            text = str(getattr(response, "text", ""))
            content = text.encode("utf-8")
        else:
            content = bytes(content)
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise YeongcheonExperienceContractError(
                    "calendar is not strict UTF-8"
                ) from exc
        if not content or len(content) > YEONGCHEON_EXPERIENCE_MAX_HTML_BYTES:
            raise YeongcheonExperienceContractError("calendar response size changed")
        if "Web firewall" in text or "웹 방화벽" in text:
            raise YeongcheonExperienceContractError("web firewall response")
        soup = BeautifulSoup(text, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        expected_title = (
            f"{partition.label} | 영천한의마을 | 체험/견학 | 영천 통합예약"
        )
        if title != expected_title:
            raise YeongcheonExperienceContractError("official page title changed")
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _validate_list_form(
    soup: BeautifulSoup,
    partition: YeongcheonExperiencePartition,
) -> None:
    forms = soup.select("form#listForm")
    if len(forms) != 1:
        raise YeongcheonExperienceContractError("calendar list form changed")
    form = forms[0]
    action = _clean(form.get("action"))
    parsed, query = _parse_url(
        f"https://{YEONGCHEON_EXPERIENCE_HOST}{action}"
        if action.startswith("/")
        else action
    )
    if (
        _clean(form.get("method")).lower() != "post"
        or parsed.path != partition.path
        or query != {"mId": partition.menu_id}
    ):
        raise YeongcheonExperienceContractError("calendar list form boundary changed")
    names = {
        _clean(control.get("name"))
        for control in form.select("input[name], select[name]")
    }
    required = {"year", "month", "date", "rsvDate", "rsvTime", "selectedDate"}
    if not required.issubset(names):
        raise YeongcheonExperienceContractError("calendar list form fields changed")


def _validate_safe_day_table(soup: BeautifulSoup) -> None:
    tables = soup.select("table.tbl.ycherb")
    if len(tables) != 1:
        raise YeongcheonExperienceContractError("public day summary table changed")
    table = tables[0]
    headers = tuple(
        _clean(header.get_text(" ", strip=True))
        for header in table.select("thead th")
    )
    if headers != _SAFE_DETAIL_HEADERS:
        raise YeongcheonExperienceContractError(
            "applicant/PII column appeared in public day summary"
        )
    for row in table.select("tbody tr"):
        if len(row.find_all(["th", "td"], recursive=False)) != len(
            _SAFE_DETAIL_HEADERS
        ):
            raise YeongcheonExperienceContractError(
                "public day summary column count changed"
            )
    if table.select("a[href*='login'], a[href*='apply'], a[href*='applicant']"):
        raise YeongcheonExperienceContractError(
            "unsafe action appeared in public day summary"
        )


def _month_from_heading(root: Tag) -> tuple[int, int]:
    heading = root.select_one(".calendarHead")
    match = _MONTH_HEADING.search(
        _clean(heading.get_text(" ", strip=True) if heading else "")
    )
    if match is None:
        raise YeongcheonExperienceContractError("calendar month heading changed")
    return int(match.group("year")), int(match.group("month"))


def _date_from_cell(td: Tag, year: int, month: int) -> Optional[date]:
    text = _clean(td.get_text(" ", strip=True))
    match = re.match(r"(?P<day>\d{1,2})(?:\s|$)", text)
    if match is None:
        return None
    day = int(match.group("day"))
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise YeongcheonExperienceContractError("invalid calendar day") from exc


def _parse_calendar_page(
    soup: BeautifulSoup,
    *,
    partition: YeongcheonExperiencePartition,
    year: int,
    month: int,
) -> _CalendarPage:
    _validate_list_form(soup, partition)
    _validate_safe_day_table(soup)
    roots = soup.select(".calendar.ycherb")
    if len(roots) != 1:
        raise YeongcheonExperienceContractError("calendar root changed")
    root = roots[0]
    if _month_from_heading(root) != (year, month):
        raise YeongcheonExperienceContractError("requested calendar month changed")
    tables = root.find_all("table")
    if len(tables) != 1:
        raise YeongcheonExperienceContractError("calendar table changed")
    table = tables[0]
    caption = _clean(
        table.caption.get_text(" ", strip=True) if table.caption else ""
    )
    if partition.label not in caption or "현황" not in caption:
        raise YeongcheonExperienceContractError("calendar caption changed")
    headers = tuple(
        _clean(header.get_text(" ", strip=True))
        for header in table.select("thead th")
    )
    if headers != _WEEKDAYS:
        raise YeongcheonExperienceContractError("calendar weekday header changed")
    cells = table.select("tbody td")
    if len(cells) not in {28, 35, 42}:
        raise YeongcheonExperienceContractError(
            "calendar grid is not four, five, or six complete weeks"
        )

    observed_dates: set[date] = set()
    slots: list[_CalendarSlot] = []
    holiday_dates: list[date] = []
    unscheduled_dates: list[date] = []
    day_control_count = 0
    for td in cells:
        service_date = _date_from_cell(td, year, month)
        if service_date is None:
            if _clean(td.get_text(" ", strip=True)):
                raise YeongcheonExperienceContractError(
                    "out-of-month calendar cell is not empty"
                )
            continue
        if service_date in observed_dates:
            raise YeongcheonExperienceContractError("duplicate calendar date")
        observed_dates.add(service_date)
        text = _clean(td.get_text(" ", strip=True))
        labels = [label for label in _ALL_DAY_LABELS if label in text]
        if len(labels) > 1:
            raise YeongcheonExperienceContractError("ambiguous calendar status")
        label = labels[0] if labels else ""
        controls = td.select("a[onclick]")
        if label in _RESERVATION_STATUSES:
            if len(controls) != 1:
                raise YeongcheonExperienceContractError(
                    "reservation day control changed"
                )
            control = controls[0]
            match = _DATE_CONTROL.fullmatch(_clean(control.get("onclick")))
            if (
                match is None
                or match.group("date") != service_date.isoformat()
                or _clean(control.get("href")) != "#"
                or _clean(control.get_text(" ", strip=True)) != label
            ):
                raise YeongcheonExperienceContractError(
                    "reservation day identity changed"
                )
            day_control_count += 1
            slots.append(
                _CalendarSlot(
                    partition=partition,
                    service_date=service_date,
                    source_status=label,
                    calendar_url=partition.url,
                )
            )
        elif label == _STATUS_HOLIDAY:
            if controls:
                raise YeongcheonExperienceContractError(
                    "holiday unexpectedly has a day control"
                )
            holiday_dates.append(service_date)
        else:
            if controls:
                raise YeongcheonExperienceContractError(
                    "unscheduled day unexpectedly has a control"
                )
            unscheduled_dates.append(service_date)

    expected_dates = {
        date(year, month, day)
        for day in range(1, monthrange(year, month)[1] + 1)
    }
    if observed_dates != expected_dates:
        raise YeongcheonExperienceContractError("calendar dates are incomplete")
    return _CalendarPage(
        partition=partition,
        year=year,
        month=month,
        slots=tuple(slots),
        holiday_dates=tuple(holiday_dates),
        unscheduled_dates=tuple(unscheduled_dates),
        day_control_count=day_control_count,
    )


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _page_signature(page: _CalendarPage) -> tuple[Any, ...]:
    return (
        tuple(
            (slot.identity, slot.source_status, slot.calendar_url)
            for slot in page.slots
        ),
        tuple(value.isoformat() for value in page.holiday_dates),
        tuple(value.isoformat() for value in page.unscheduled_dates),
        page.day_control_count,
    )


def _identity_hash(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def _slot_row(slot: _CalendarSlot) -> dict[str, Any]:
    normalized_status = "OPEN" if slot.source_status == _STATUS_OPEN else "CLOSED"
    identity = slot.identity
    return {
        "provider": YEONGCHEON_EXPERIENCE_PROVIDER,
        "municipality_code": YEONGCHEON_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": YEONGCHEON_EXPERIENCE_MUNICIPALITY_NAME,
        "provider_course_id": (
            f"{YEONGCHEON_EXPERIENCE_PROVIDER}:oriental-village:{identity}"
        ),
        "source_course_id": f"oriental-village:{identity}",
        "title": (
            f"{YEONGCHEON_EXPERIENCE_BRANCH} {slot.partition.label} "
            f"({slot.service_date.isoformat()})"
        ),
        "branch": YEONGCHEON_EXPERIENCE_BRANCH,
        "branch_code": f"oriental-{slot.partition.code}",
        "branch_url": YEONGCHEON_EXPERIENCE_URL,
        "preserve_branch": True,
        "category": "영천 통합예약/체험·견학/영천한의마을",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "classification_locked": True,
        "operator_type": "지자체/공공기관",
        "program_type": "체험",
        "source_status": slot.source_status,
        "status": normalized_status,
        # The public calendar exposes availability but no safe, direct application URL.
        # Keep this false so downstream clients never present an unusable apply action.
        "reservation_available": False,
        "period": slot.service_date.isoformat(),
        "start_date": slot.service_date,
        "end_date": slot.service_date,
        "schedule_raw": (
            f"{slot.service_date.isoformat()} / {slot.partition.label}"
        ),
        "venue_name": YEONGCHEON_EXPERIENCE_BRANCH,
        "address": YEONGCHEON_EXPERIENCE_ADDRESS,
        "capacity": "",
        "application_url": "",
        "raw_url": slot.calendar_url,
        "raw_fields": {
            "parser": YEONGCHEON_EXPERIENCE_PARSER,
            "official_slot_identity": identity,
            "official_reservation_partition": slot.partition.code,
            "official_reservation_partition_label": slot.partition.label,
            "official_source_status": slot.source_status,
            "official_calendar_month": slot.service_date.strftime("%Y-%m"),
            "public_month_calendar_record": True,
            "separate_public_detail_endpoint": False,
            "day_control_observed_not_called": True,
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key).lower()
                child_path = f"{path}.{key}" if path else str(key)
                if key_text in _FORBIDDEN_OUTPUT_KEYS:
                    errors.append(f"forbidden key {child_path}")
                walk(child, child_path)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str):
            if path.lower().endswith("url"):
                if value and not any(
                    _same_url(value, partition.url)
                    for partition in YEONGCHEON_EXPERIENCE_PARTITIONS
                ):
                    errors.append(f"non-allowlisted URL in {path}")
            elif _PHONE.search(value) or _EMAIL.search(value):
                errors.append(f"PII value in {path}")

    walk(row, "")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _meta() -> dict[str, Any]:
    return {
        "errors": [],
        "error_kind": "",
        "configured_collection_error": "",
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "pagination_complete": False,
        "partitions_complete": False,
        "details_complete": False,
        "logical_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "identity_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
    }


def collect_yeongcheon_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 24,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session,
    dedupe_rows: DedupeRows = _dedupe_default,
    fetcher: Fetcher = _default_fetcher,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return an atomic current/future snapshot of both experience calendars."""

    meta = _meta()
    if not is_yeongcheon_experience_target(target):
        meta["errors"] = ["target does not match the canonical experience calendar"]
        meta["error_kind"] = "contract"
        meta["configured_collection_error"] = meta["errors"][0]
        return [], YEONGCHEON_EXPERIENCE_PARSER, meta
    if timeout < 1 or max_pages < 2 or detail_limit < 0:
        meta["errors"] = ["invalid collection limits"]
        meta["error_kind"] = "contract"
        meta["configured_collection_error"] = meta["errors"][0]
        return [], YEONGCHEON_EXPERIENCE_PARSER, meta

    cutoff = _today(today)
    requester = _Requester(session_factory, fetcher, timeout, meta)
    try:
        pages: dict[tuple[str, int, int], _CalendarPage] = {}
        partition_months: dict[str, list[tuple[int, int]]] = {}
        sentinel_months: dict[str, tuple[int, int]] = {}
        for partition in YEONGCHEON_EXPERIENCE_PARTITIONS:
            year, month = cutoff.year, cutoff.month
            months: list[tuple[int, int]] = []
            found_nonempty = False
            empty_before_data = 0
            for _ in range(max_pages):
                page = _parse_calendar_page(
                    requester.soup(partition, year, month),
                    partition=partition,
                    year=year,
                    month=month,
                )
                pages[(partition.code, year, month)] = page
                months.append((year, month))
                if page.slots:
                    found_nonempty = True
                    empty_before_data = 0
                elif found_nonempty:
                    sentinel_months[partition.code] = (year, month)
                    break
                else:
                    empty_before_data += 1
                    if empty_before_data == 2:
                        sentinel_months[partition.code] = (year, month)
                        break
                year, month = _next_month(year, month)
            if partition.code not in sentinel_months:
                raise YeongcheonExperienceContractError(
                    "max_pages truncated the exact empty month sentinel"
                )
            partition_months[partition.code] = months

        # Recheck current, final non-empty, and exact empty sentinel pages.
        stability_keys: set[tuple[str, int, int]] = set()
        for partition in YEONGCHEON_EXPERIENCE_PARTITIONS:
            months = partition_months[partition.code]
            stability_keys.add((partition.code, *months[0]))
            nonempty = [
                (year, month)
                for year, month in months
                if pages[(partition.code, year, month)].slots
            ]
            if nonempty:
                stability_keys.add((partition.code, *nonempty[-1]))
            stability_keys.add(
                (partition.code, *sentinel_months[partition.code])
            )
        for code, year, month in sorted(stability_keys):
            partition = YEONGCHEON_EXPERIENCE_PARTITION_BY_CODE[code]
            rechecked = _parse_calendar_page(
                requester.soup(partition, year, month),
                partition=partition,
                year=year,
                month=month,
            )
            if _page_signature(rechecked) != _page_signature(
                pages[(code, year, month)]
            ):
                raise YeongcheonExperienceContractError(
                    "calendar boundary changed during collection"
                )

        all_slots = [
            slot
            for partition in YEONGCHEON_EXPERIENCE_PARTITIONS
            for year, month in partition_months[partition.code]
            for slot in pages[(partition.code, year, month)].slots
        ]
        current_slots = [
            slot for slot in all_slots if slot.service_date >= cutoff
        ]
        identities = [slot.identity for slot in current_slots]
        if len(identities) != len(set(identities)):
            raise YeongcheonExperienceContractError(
                "calendar partitions overlap or duplicate identities"
            )
        if len(current_slots) > detail_limit:
            raise YeongcheonExperienceContractError(
                "detail_limit truncates the current/future calendar ledger"
            )
        ordered = sorted(
            current_slots,
            key=lambda slot: (slot.service_date, slot.partition.code),
        )
        output = [_slot_row(slot) for slot in ordered]
        privacy = [error for row in output for error in _privacy_errors(row)]
        if privacy:
            raise YeongcheonExperienceContractError(
                f"PII/output allowlist violation: {privacy[0]}"
            )
        deduped = list(dedupe_rows(output))
        if len(deduped) != len(output):
            raise YeongcheonExperienceContractError("dedupe changed complete output")

        status_counts = Counter(row["status"] for row in deduped)
        source_status_counts = Counter(
            slot.source_status for slot in current_slots
        )
        partition_counts = Counter(
            slot.partition.code for slot in current_slots
        )
        month_counts = Counter(
            slot.service_date.strftime("%Y-%m") for slot in current_slots
        )
        last_nonempty = {
            partition.code: next(
                (
                    f"{year:04d}-{month:02d}"
                    for year, month in reversed(
                        partition_months[partition.code]
                    )
                    if pages[(partition.code, year, month)].slots
                ),
                "",
            )
            for partition in YEONGCHEON_EXPERIENCE_PARTITIONS
        }
        meta.update(
            {
                "provider": YEONGCHEON_EXPERIENCE_PROVIDER,
                "municipality_code": YEONGCHEON_EXPERIENCE_MUNICIPALITY_CODE,
                "ownership_scope": YEONGCHEON_EXPERIENCE_OWNERSHIP_SCOPE,
                "cutoff": cutoff.isoformat(),
                "partition_count": len(YEONGCHEON_EXPERIENCE_PARTITIONS),
                "partition_codes": [
                    partition.code
                    for partition in YEONGCHEON_EXPERIENCE_PARTITIONS
                ],
                "calendar_partition_pages": sum(
                    len(months) for months in partition_months.values()
                ),
                "stable_boundary_pages": len(stability_keys),
                "partition_months": {
                    code: [f"{year:04d}-{month:02d}" for year, month in months]
                    for code, months in partition_months.items()
                },
                "last_nonempty_month": last_nonempty,
                "sentinel_month": {
                    code: f"{year:04d}-{month:02d}"
                    for code, (year, month) in sentinel_months.items()
                },
                "source_slot_count": len(all_slots),
                "expired_slot_count": len(all_slots) - len(current_slots),
                "current_count": len(current_slots),
                "returned_count": len(deduped),
                "source_status_counts": dict(sorted(source_status_counts.items())),
                "status_counts": dict(sorted(status_counts.items())),
                "partition_counts": {
                    partition.code: partition_counts.get(partition.code, 0)
                    for partition in YEONGCHEON_EXPERIENCE_PARTITIONS
                },
                "month_counts": dict(sorted(month_counts.items())),
                "day_controls_observed_not_called": sum(
                    page.day_control_count for page in pages.values()
                ),
                "source_identity_sha256": _identity_hash(
                    slot.identity for slot in all_slots
                ),
                "current_identity_sha256": _identity_hash(identities),
                "duplicate_count": 0,
                "no_current_data": not deduped,
                "calendar_has_no_pagination": True,
                "separate_public_detail_endpoint": False,
                "safe_public_month_lists_complete": True,
                "pagination_complete": True,
                "partitions_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, YEONGCHEON_EXPERIENCE_PARSER, meta
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        meta["errors"] = [message]
        meta["error_kind"] = (
            "contract"
            if isinstance(exc, YeongcheonExperienceContractError)
            else "network_or_parse"
        )
        meta["configured_collection_error"] = message
        return [], YEONGCHEON_EXPERIENCE_PARSER, meta
    finally:
        requester.close()


collect = collect_yeongcheon_experience


__all__ = [
    name for name in globals() if name.startswith("YEONGCHEON_EXPERIENCE_")
] + [
    "YeongcheonExperienceContractError",
    "YeongcheonExperiencePartition",
    "collect",
    "collect_yeongcheon_experience",
    "is_target",
    "is_yeongcheon_experience_target",
]
