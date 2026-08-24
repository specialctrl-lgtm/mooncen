"""Exact public-GET collector for Pohang Buk-gu infant forest experiences.

The Gyeongsangbuk-do Forest Resources Development Institute publishes a
server-rendered monthly calendar for its infant forest programme.  The same
page fixes the operating season (March through November), venue, audience,
capacity, hours and a nine-month hands-on activity registry.  Calendar action
controls provide one stable date identity and public availability state per
operating weekday.

Only canonical calendar GET requests are allowed.  The calendar's POST form
and its JavaScript reservation/check controls are inspected in already-fetched
HTML, but are never submitted, followed or exposed as application URLs.
Login, authentication, member, applicant, identity, file, attachment,
download and PII endpoints are outside the request allowlist.
"""

from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


POHANG_FOREST_EXPERIENCE_PROVIDER = "MUNI_WWW_GB_GO_KR_26B3732E"
POHANG_FOREST_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_CCA28172C30A"
POHANG_FOREST_EXPERIENCE_HOST = "www.gb.go.kr"
POHANG_FOREST_EXPERIENCE_PATH = "/Main/forest/page.do"
POHANG_FOREST_EXPERIENCE_MENU_ID = "16262"
POHANG_FOREST_EXPERIENCE_URL = (
    f"https://{POHANG_FOREST_EXPERIENCE_HOST}{POHANG_FOREST_EXPERIENCE_PATH}"
    f"?mnu_uid={POHANG_FOREST_EXPERIENCE_MENU_ID}"
)
POHANG_FOREST_EXPERIENCE_OPERATION_MONTHS = tuple(range(3, 12))
POHANG_FOREST_EXPERIENCE_MAX_HTML_BYTES = 2_000_000
POHANG_FOREST_EXPERIENCE_MUNICIPALITY_CODE = "4711300000"
POHANG_FOREST_EXPERIENCE_MUNICIPALITY_NAME = "경상북도 포항시 북구"
POHANG_FOREST_EXPERIENCE_BRANCH = "경상북도수목원 유아숲체험장"
POHANG_FOREST_EXPERIENCE_VENUE = "경상북도수목원"
POHANG_FOREST_EXPERIENCE_ADDRESS = (
    "경상북도 포항시 북구 죽장면 수목원로 647"
)
POHANG_FOREST_EXPERIENCE_PARSER = (
    "gyeongbuk_forest_infant_experience_declared_march_november_calendar+"
    "current_through_last_operation_month_gets+semantic_december_post_last+"
    "stable_first_last_sentinel_rechecks+complete_calendar_day_identities+"
    "identity_bound_javascript_controls_observed_no_submit_no_follow+"
    "nine_month_hands_on_activity_registry+fixed_pohang_bukgu_venue+"
    "weekend_and_generic_out_of_season_shell_exclusion+locked_experience+"
    "no_post_application_login_auth_member_applicant_identity_file_attachment_"
    "download_or_pii_calls"
)
POHANG_FOREST_EXPERIENCE_OWNERSHIP_SCOPE = (
    "gyeongbuk_forest_resources_infant_forest_current_year_march_november_"
    "dated_experiences_at_pohang_bukgu_arboretum"
)

_PAGE_TITLE = "유아숲체험 < 숲아카데미 < 산림문화체험센터< 산림"
_CALENDAR_CAPTION = (
    "숲해설 예약 월간일정 표로 요일, 날짜 별 예약불가, 예약하기, "
    "예약확인 정보를 나타냄"
)
_PROGRAM_CAPTION = (
    "유아숲체험프로그램을 월, 프로그램명, 활동명, 놀이체험, 준비물 "
    "순으로 나타낸 표"
)
_VENUE_TEXT = (
    "숲해설 장소 : 경상북도수목원"
    "(경상북도 포항시 북구 죽장면 수목원로 647)"
)
_PUBLIC_FACTS = (
    "운영기간 : 3월 ~ 11월",
    "체험형 프로그램",
    "모집인원 : 20명 이내 (20명 초과시 사전협의)",
    "운영시간 : 10 : 00 ~ 17 : 00 (2시간 프로그램)",
    "내 용 : 숲관찰, 숲속놀이, 자연물 만들기 체험 등",
    "참 가 비 : 무료(체험재료 무료제공)",
)
_HANDS_ON_MARKERS = (
    "만들기",
    "꾸미기",
    "만들어 먹기",
    "피자",
    "되어보기",
    "구슬치기",
    "팽이 돌리기",
    "발로 자치기",
)
_MONTH_HEADING_RE = re.compile(r"(20\d{2})년\s+(0[1-9]|1[0-2])월")
_PROGRAM_MONTH_RE = re.compile(r"(1[0-2]|[1-9])월")
_WRITER_RE = re.compile(
    r"javascript:writer\((?P<mode>[24]),\s*(?P<identity>20\d{6})\);"
)
_YEAR_RE = re.compile(r"20\d{2}")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_year",
        "source_month",
        "source_date",
        "source_status",
        "source_control_mode",
        "source_program_title",
        "hands_on_evidence",
        "venue_basis",
        "calendar_identity_verified",
        "application_control_present",
        "application_form_not_submitted",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "contact",
        "email",
        "manager",
        "instructor",
        "applicant",
        "member",
        "attachment",
        "download_url",
        "reservation_url",
    }
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class PohangForestExperienceContractError(RuntimeError):
    """Raised when the audited official calendar contract changes."""


@dataclass(frozen=True)
class _Program:
    month: int
    title: str
    activity: str
    play: str
    materials: str
    hands_on_evidence: str


@dataclass(frozen=True)
class _CalendarDay:
    value: date
    source_status: str
    status: str
    control_mode: str
    application_control: bool


@dataclass(frozen=True)
class _MonthPage:
    year: int
    month: int
    days: tuple[_CalendarDay, ...]
    programs: tuple[_Program, ...]
    venue: str


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_pohang_forest_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider"))
        == POHANG_FOREST_EXPERIENCE_PROVIDER
        and _clean(_target_value(target, "url"))
        == POHANG_FOREST_EXPERIENCE_URL
    )


is_target = is_pohang_forest_experience_target


def pohang_forest_experience_month_url(year: int, month: int) -> str:
    if (
        not isinstance(year, int)
        or isinstance(year, bool)
        or _YEAR_RE.fullmatch(str(year)) is None
    ):
        raise ValueError("year must be a four-digit current-era year")
    if not isinstance(month, int) or isinstance(month, bool) or not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    query = (
        ("certification", ""),
        ("initMonth", str(month)),
        ("initYear", str(year)),
        ("mnu_uid", POHANG_FOREST_EXPERIENCE_MENU_ID),
    )
    return (
        f"https://{POHANG_FOREST_EXPERIENCE_HOST}"
        f"{POHANG_FOREST_EXPERIENCE_PATH}?{urlencode(query)}"
    )


def _canonical_key(url: str) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    parsed = urlparse(_clean(url))
    return (
        parsed.scheme.lower(),
        parsed.netloc.lower() + parsed.path,
        tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
    )


def _request_kind(url: str) -> str:
    parsed = urlparse(_clean(url))
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise PohangForestExperienceContractError(
            "malformed official calendar query"
        ) from exc
    values = dict(query)
    if not (
        parsed.scheme == "https"
        and parsed.hostname == POHANG_FOREST_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == POHANG_FOREST_EXPERIENCE_PATH
        and not parsed.fragment
        and len(query) == 4
        and set(values)
        == {"certification", "initMonth", "initYear", "mnu_uid"}
        and values["certification"] == ""
        and values["mnu_uid"] == POHANG_FOREST_EXPERIENCE_MENU_ID
        and _YEAR_RE.fullmatch(values["initYear"])
        and values["initYear"] == str(int(values["initYear"]))
        and values["initMonth"] == str(int(values["initMonth"]))
        and 1 <= int(values["initMonth"]) <= 12
    ):
        raise PohangForestExperienceContractError(
            "request is outside the public calendar GET allowlist"
        )
    return "list"


def _default_session() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 municipal-course-crawler/1.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    return current.get(url, timeout=timeout, allow_redirects=False)


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

    def soup(self, url: str) -> BeautifulSoup:
        kind = _request_kind(url)
        self.meta["logical_requests"] += 1
        self.meta["get_requests"] += 1
        self.meta[f"{kind}_requests"] += 1
        response = self.fetcher(self.session, url, self.timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise PohangForestExperienceContractError(
                f"unexpected HTTP status {status}"
            )
        if tuple(getattr(response, "history", ()) or ()):
            raise PohangForestExperienceContractError(
                "redirect history is forbidden"
            )
        headers = getattr(response, "headers", {}) or {}
        if any(
            str(key).lower() == "location" and value
            for key, value in headers.items()
        ):
            raise PohangForestExperienceContractError(
                "redirect location is forbidden"
            )
        final_url = _clean(getattr(response, "url", ""))
        if final_url and _canonical_key(final_url) != _canonical_key(url):
            raise PohangForestExperienceContractError(
                "official response URL changed"
            )
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
            raise PohangForestExperienceContractError(
                "official response is not HTML"
            )
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > POHANG_FOREST_EXPERIENCE_MAX_HTML_BYTES:
            raise PohangForestExperienceContractError(
                "empty or oversized official response"
            )
        soup = BeautifulSoup(body, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title != _PAGE_TITLE:
            raise PohangForestExperienceContractError(
                "official page title changed"
            )
        return soup

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise PohangForestExperienceContractError("invalid audit date") from exc


def _calendar_form(soup: BeautifulSoup) -> None:
    forms = soup.select(
        f'form#calendarFrm[method="post"][action="page.do?mnu_uid='
        f'{POHANG_FOREST_EXPERIENCE_MENU_ID}"]'
    )
    if len(forms) != 1:
        raise PohangForestExperienceContractError(
            "calendar application form contract changed"
        )
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in forms[0].select('input[type="hidden"][name]')
    }
    if hidden != {
        "cmd": "1",
        "re_date": "",
        "re_type": "2",
        "site_name": "farm",
        "certification": "",
    }:
        raise PohangForestExperienceContractError(
            "calendar application form fields changed"
        )
    facts = _clean(forms[0].get_text(" ", strip=True))
    if any(fact not in facts for fact in _PUBLIC_FACTS):
        raise PohangForestExperienceContractError(
            "public infant-forest programme facts changed"
        )


def _program_registry(soup: BeautifulSoup) -> tuple[_Program, ...]:
    tables = [
        table
        for table in soup.select("table.tbl_st1")
        if _clean(
            table.caption.get_text(" ", strip=True) if table.caption else ""
        )
        == _PROGRAM_CAPTION
    ]
    if len(tables) != 1:
        raise PohangForestExperienceContractError(
            "hands-on activity registry changed"
        )
    programs: list[_Program] = []
    for row in tables[0].select("tbody > tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 5:
            raise PohangForestExperienceContractError(
                "hands-on activity registry row changed"
            )
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        month_match = _PROGRAM_MONTH_RE.fullmatch(values[0])
        if month_match is None or not all(values[:4]):
            raise PohangForestExperienceContractError(
                "hands-on activity registry identity changed"
            )
        month = int(month_match[1])
        evidence_text = _clean(" ".join(values[1:4]))
        markers = [marker for marker in _HANDS_ON_MARKERS if marker in evidence_text]
        if not markers:
            raise PohangForestExperienceContractError(
                f"month {month}: hands-on evidence changed"
            )
        programs.append(
            _Program(
                month,
                values[1],
                values[2],
                values[3],
                values[4],
                ",".join(markers[:3]),
            )
        )
    if tuple(program.month for program in programs) != (
        POHANG_FOREST_EXPERIENCE_OPERATION_MONTHS
    ):
        raise PohangForestExperienceContractError(
            "declared March-November programme registry changed"
        )
    return tuple(programs)


def _validate_neighbor(
    anchor: Any,
    *,
    expected_year: int,
    expected_month: int,
    context: str,
) -> None:
    if anchor is None:
        raise PohangForestExperienceContractError(
            f"{context} calendar neighbor control missing"
        )
    parsed = urlparse(
        urljoin(POHANG_FOREST_EXPERIENCE_URL, _clean(anchor.get("href")))
    )
    query = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(query)
    if not (
        parsed.scheme == "https"
        and parsed.hostname == POHANG_FOREST_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == POHANG_FOREST_EXPERIENCE_PATH
        and not parsed.fragment
        and len(query) == 4
        and set(values)
        == {"mnu_uid", "initYear", "initMonth", "certification"}
        and values["mnu_uid"] == POHANG_FOREST_EXPERIENCE_MENU_ID
        and values["certification"] == ""
        and values["initYear"] == str(expected_year)
        and values["initMonth"] == str(expected_month)
    ):
        raise PohangForestExperienceContractError(
            f"{context} calendar neighbor identity changed"
        )


def _day_state(cell: Any, value: date) -> _CalendarDay:
    states = [
        ("OPEN", cell.select_one(".msg_res_01")),
        ("UNAVAILABLE", cell.select_one(".msg_res_02")),
        ("CLOSED", cell.select_one(".msg_res_03")),
    ]
    present = [(name, node) for name, node in states if node is not None]
    if len(present) > 1:
        raise PohangForestExperienceContractError(
            f"{value.isoformat()}: multiple calendar states"
        )
    if not present:
        if cell.select("a[href]"):
            raise PohangForestExperienceContractError(
                f"{value.isoformat()}: unclassified calendar action"
            )
        return _CalendarDay(value, "", "", "", False)

    source_status, node = present[0]
    text = _clean(node.get_text(" ", strip=True))
    anchors = node.select("a[href]")
    if source_status == "UNAVAILABLE":
        if text != "예약불가" or any(_clean(a.get("href")) != "#" for a in anchors):
            raise PohangForestExperienceContractError(
                f"{value.isoformat()}: unavailable control changed"
            )
        return _CalendarDay(value, source_status, "", "", False)

    if len(anchors) != 1:
        raise PohangForestExperienceContractError(
            f"{value.isoformat()}: calendar action count changed"
        )
    match = _WRITER_RE.fullmatch(_clean(anchors[0].get("href")))
    expected_mode = "4" if source_status == "OPEN" else "2"
    expected_label = "예약하기" if source_status == "OPEN" else "예약확인"
    support = cell.select_one(
        ".msg_days_on" if source_status == "OPEN" else ".msg_days_off"
    )
    expected_support = "전체예약가능" if source_status == "OPEN" else "3일전"
    if not (
        match
        and match["mode"] == expected_mode
        and match["identity"] == value.strftime("%Y%m%d")
        and text == expected_label
        and support is not None
        and _clean(support.get_text(" ", strip=True)) == expected_support
    ):
        raise PohangForestExperienceContractError(
            f"{value.isoformat()}: calendar action identity/status changed"
        )
    return _CalendarDay(
        value,
        source_status,
        "OPEN" if source_status == "OPEN" else "CLOSED",
        expected_mode,
        True,
    )


def _calendar_days(root: Any, year: int, month: int) -> tuple[_CalendarDay, ...]:
    tables = root.select(".schedule_table > table")
    if len(tables) != 1 or _clean(
        tables[0].caption.get_text(" ", strip=True) if tables[0].caption else ""
    ) != _CALENDAR_CAPTION:
        raise PohangForestExperienceContractError(
            f"{year}-{month:02d}: calendar table changed"
        )
    cells: dict[int, Any] = {}
    for cell in tables[0].select("tbody td"):
        day_nodes = cell.select(":scope > span.date:not(.other_month)")
        if not day_nodes:
            continue
        if len(day_nodes) != 1:
            raise PohangForestExperienceContractError(
                f"{year}-{month:02d}: duplicate calendar date node"
            )
        day_text = _clean(day_nodes[0].get_text(" ", strip=True))
        if not day_text.isdigit():
            raise PohangForestExperienceContractError(
                f"{year}-{month:02d}: invalid calendar day"
            )
        day = int(day_text)
        if day in cells:
            raise PohangForestExperienceContractError(
                f"{year}-{month:02d}: duplicate calendar day"
            )
        cells[day] = cell
    last_day = calendar.monthrange(year, month)[1]
    if tuple(sorted(cells)) != tuple(range(1, last_day + 1)):
        raise PohangForestExperienceContractError(
            f"{year}-{month:02d}: complete calendar-day boundary changed"
        )
    return tuple(
        _day_state(cells[day], date(year, month, day))
        for day in range(1, last_day + 1)
    )


def _parse_month_page(
    soup: BeautifulSoup,
    *,
    expected_year: int,
    expected_month: int,
) -> _MonthPage:
    _calendar_form(soup)
    programs = _program_registry(soup)
    roots = soup.select("#schedule_wrap")
    if len(roots) != 1:
        raise PohangForestExperienceContractError(
            "official calendar root changed"
        )
    root = roots[0]
    heading = root.select_one(".schedule_control > h2")
    match = _MONTH_HEADING_RE.fullmatch(
        _clean(heading.get_text(" ", strip=True) if heading else "")
    )
    if (
        match is None
        or int(match[1]) != expected_year
        or int(match[2]) != expected_month
    ):
        raise PohangForestExperienceContractError(
            "official calendar month heading changed"
        )
    # The official JSP keeps the displayed year unchanged and emits month 13
    # from December (and month 0 from January).  These controls are contract
    # evidence only: the collector never follows an out-of-range neighbor.
    previous = (expected_year, expected_month - 1)
    following = (expected_year, expected_month + 1)
    _validate_neighbor(
        root.select_one(".schedule_control > a.sch_btn_l[href]"),
        expected_year=previous[0],
        expected_month=previous[1],
        context="previous",
    )
    _validate_neighbor(
        root.select_one(".schedule_control > a.sch_btn_r[href]"),
        expected_year=following[0],
        expected_month=following[1],
        context="next",
    )
    venue_nodes = [
        node
        for node in root.find_all("span", recursive=False)
        if _clean(node.get_text(" ", strip=True)).startswith("숲해설 장소")
    ]
    venue = _clean(
        venue_nodes[0].get_text(" ", strip=True) if len(venue_nodes) == 1 else ""
    )
    if venue != _VENUE_TEXT:
        raise PohangForestExperienceContractError(
            "fixed Pohang Buk-gu forest venue changed"
        )
    days = _calendar_days(root, expected_year, expected_month)
    return _MonthPage(expected_year, expected_month, days, programs, venue)


def _page_signature(page: _MonthPage) -> tuple[Any, ...]:
    return (
        page.year,
        page.month,
        page.venue,
        tuple(
            (
                day.value,
                day.source_status,
                day.status,
                day.control_mode,
                day.application_control,
            )
            for day in page.days
        ),
        tuple(
            (
                program.month,
                program.title,
                program.activity,
                program.play,
                program.materials,
                program.hands_on_evidence,
            )
            for program in page.programs
        ),
    )


def _row(day: _CalendarDay, program: _Program) -> dict[str, Any]:
    identity = day.value.strftime("%Y%m%d")
    title = f"{program.title} 유아숲체험 ({day.value.isoformat()})"
    return {
        "provider": POHANG_FOREST_EXPERIENCE_PROVIDER,
        "provider_course_id": (
            f"{POHANG_FOREST_EXPERIENCE_PROVIDER}:forest:{identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": f"{program.title} - {program.hands_on_evidence}",
        "branch": POHANG_FOREST_EXPERIENCE_BRANCH,
        "branch_code": "GYEONGBUK_ARBORETUM_INFANT_FOREST",
        "preserve_branch": True,
        "category": "유아숲 체험",
        "program_type": "자연·생태 체험",
        "raw_url": pohang_forest_experience_month_url(
            day.value.year, day.value.month
        ),
        "application_url": "",
        "application_type": (
            "ONLINE_RESERVATION_SENSITIVE_FORM_NOT_EXPOSED"
            if day.status == "OPEN"
            else "INFO_ONLY"
        ),
        "application_method": (
            "공식 달력 신청 control 확인(경로 미노출)"
            if day.status == "OPEN"
            else "예약 확인 단계"
        ),
        "reservation_available": day.status == "OPEN",
        "status": day.status,
        "fee": "무료",
        "period": day.value.isoformat(),
        "start_date": day.value.isoformat(),
        "end_date": day.value.isoformat(),
        "apply_period": "",
        "schedule_raw": "10:00 ~ 17:00 중 2시간 프로그램",
        "capacity": "20명 이내",
        "capacity_total": 20,
        "target": "만 4세 ~ 6세(도내 유치원·어린이집 등)",
        "venue": POHANG_FOREST_EXPERIENCE_VENUE,
        "venue_name": POHANG_FOREST_EXPERIENCE_VENUE,
        "address": POHANG_FOREST_EXPERIENCE_ADDRESS,
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "operator_type": "광역자치단체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "collection_type": POHANG_FOREST_EXPERIENCE_PARSER,
        "municipality_code": POHANG_FOREST_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": POHANG_FOREST_EXPERIENCE_MUNICIPALITY_NAME,
        "municipality_full_name": POHANG_FOREST_EXPERIENCE_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_year": day.value.year,
            "source_month": day.value.month,
            "source_date": day.value.isoformat(),
            "source_status": day.source_status,
            "source_control_mode": day.control_mode,
            "source_program_title": program.title,
            "hands_on_evidence": program.hands_on_evidence,
            "venue_basis": "공식 월별 달력 하단 숲해설 장소",
            "calendar_identity_verified": True,
            "application_control_present": day.application_control,
            "application_form_not_submitted": True,
            "service_family": "experience",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden application/detail key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw field allowlist exceeded")
    if row.get("application_url"):
        errors.append("application form URL persisted")
    payload = repr(row)
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("contact data persisted")
    if row.get("municipality_code") != POHANG_FOREST_EXPERIENCE_MUNICIPALITY_CODE:
        errors.append("venue escaped Pohang Buk-gu mapping")
    return errors


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _clean(row.get("start_date")),
        _clean(row.get("raw_fields", {}).get("source_program_title")),
        _clean(row.get("venue_name")),
    )


def _months_for_cutoff(cutoff: date) -> tuple[int, ...]:
    if cutoff.month > POHANG_FOREST_EXPERIENCE_OPERATION_MONTHS[-1]:
        return ()
    first = max(cutoff.month, POHANG_FOREST_EXPERIENCE_OPERATION_MONTHS[0])
    return tuple(range(first, POHANG_FOREST_EXPERIENCE_OPERATION_MONTHS[-1] + 1))


def collect_pohang_forest_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 120,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one exact current-year Pohang Buk-gu forest snapshot."""

    cutoff = _audit_date(today)
    data_months = _months_for_cutoff(cutoff)
    sentinel_month = 12
    boundary_months = tuple(
        dict.fromkeys(
            (
                *((data_months[0], data_months[-1]) if data_months else ()),
                sentinel_month,
            )
        )
    )
    required_requests = len(data_months) + 1 + len(boundary_months)
    meta: dict[str, Any] = {
        "municipality_code": POHANG_FOREST_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": POHANG_FOREST_EXPERIENCE_MUNICIPALITY_NAME,
        "owner_provider": POHANG_FOREST_EXPERIENCE_PROVIDER,
        "candidate_id": POHANG_FOREST_EXPERIENCE_CANDIDATE_ID,
        "parser": POHANG_FOREST_EXPERIENCE_PARSER,
        "ownership_scope": POHANG_FOREST_EXPERIENCE_OWNERSHIP_SCOPE,
        "cutoff": cutoff.isoformat(),
        "logical_requests": 0,
        "get_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "post_requests": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "auth_endpoint_requests": 0,
        "member_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "identity_endpoint_requests": 0,
        "file_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pagination_complete": False,
        "calendar_complete": False,
        "activity_registry_complete": False,
        "venue_mapping_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "errors": [],
        "configured_collection_error": "",
    }
    requester: Optional[_Requester] = None
    try:
        if not is_pohang_forest_experience_target(target):
            raise PohangForestExperienceContractError(
                "target is not the canonical Pohang forest-experience owner"
            )
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise PohangForestExperienceContractError("invalid collector limits")
        if required_requests > max_pages:
            meta["source_cap_reached"] = True
            raise PohangForestExperienceContractError(
                f"max_pages {max_pages} below required {required_requests} "
                "month and boundary GETs"
            )
        requester = _Requester(
            session_factory or _default_session,
            fetcher or _default_fetcher,
            timeout,
            meta,
        )
        pages: dict[int, _MonthPage] = {}
        for month in (*data_months, sentinel_month):
            pages[month] = _parse_month_page(
                requester.soup(
                    pohang_forest_experience_month_url(cutoff.year, month)
                ),
                expected_year=cutoff.year,
                expected_month=month,
            )
        for month in boundary_months:
            rechecked = _parse_month_page(
                requester.soup(
                    pohang_forest_experience_month_url(cutoff.year, month)
                ),
                expected_year=cutoff.year,
                expected_month=month,
            )
            if _page_signature(rechecked) != _page_signature(pages[month]):
                raise PohangForestExperienceContractError(
                    f"month {month}: stability recheck changed"
                )

        registry_signature = tuple(
            (
                program.month,
                program.title,
                program.activity,
                program.play,
                program.materials,
                program.hands_on_evidence,
            )
            for program in pages[sentinel_month].programs
        )
        for page in pages.values():
            if tuple(
                (
                    program.month,
                    program.title,
                    program.activity,
                    program.play,
                    program.materials,
                    program.hands_on_evidence,
                )
                for program in page.programs
            ) != registry_signature:
                raise PohangForestExperienceContractError(
                    "hands-on activity registry changed across month GETs"
                )
        programs = {
            program.month: program for program in pages[sentinel_month].programs
        }
        if sentinel_month in programs:
            raise PohangForestExperienceContractError(
                "December entered declared March-November operation registry"
            )

        source_days: list[_CalendarDay] = []
        excluded_weekends = 0
        blank_current_days: list[str] = []
        month_counts: dict[int, int] = {}
        for month in data_months:
            current_days = [
                day for day in pages[month].days if day.value >= cutoff
            ]
            accepted: list[_CalendarDay] = []
            for day in current_days:
                if day.source_status == "UNAVAILABLE":
                    if day.value.weekday() < 5:
                        raise PohangForestExperienceContractError(
                            f"{day.value.isoformat()}: weekday became unavailable"
                        )
                    excluded_weekends += 1
                    continue
                if not day.status:
                    blank_current_days.append(day.value.isoformat())
                    continue
                accepted.append(day)
            if blank_current_days:
                raise PohangForestExperienceContractError(
                    "current operating weekdays lack public calendar state: "
                    + ",".join(blank_current_days[:3])
                )
            month_counts[month] = len(accepted)
            source_days.extend(accepted)
        if len(source_days) > detail_limit:
            meta["source_cap_reached"] = True
            raise PohangForestExperienceContractError(
                "detail_limit would create a partial dated experience snapshot"
            )

        output = [_row(day, programs[day.value.month]) for day in source_days]
        privacy = [error for row in output for error in _privacy_errors(row)]
        if privacy:
            raise PohangForestExperienceContractError(
                "; ".join(sorted(set(privacy)))
            )
        signatures = [_semantic_signature(row) for row in output]
        if len(signatures) != len(set(signatures)):
            raise PohangForestExperienceContractError(
                "returned snapshot contains semantic duplicate experiences"
            )
        before_dedupe = len(output)
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        if len(output) != before_dedupe:
            raise PohangForestExperienceContractError(
                "external dedupe removed exact official date identities"
            )

        sentinel_shell_controls = sum(
            day.application_control for day in pages[sentinel_month].days
        )
        meta.update(
            {
                "year": cutoff.year,
                "data_months": list(data_months),
                "data_month_count": len(data_months),
                "month_counts": month_counts,
                "sentinel_month": sentinel_month,
                "sentinel_program_count": 0,
                "sentinel_shell_control_count": sentinel_shell_controls,
                "semantic_post_last_sentinel": True,
                "generic_calendar_shell_outside_declared_operation_excluded": True,
                "boundary_rechecks": len(boundary_months),
                "declared_operation_months": list(
                    POHANG_FOREST_EXPERIENCE_OPERATION_MONTHS
                ),
                "activity_registry_month_count": len(programs),
                "source_total": len(source_days),
                "source_rows": len(source_days),
                "current_source_count": len(source_days),
                "excluded_weekend_count": excluded_weekends,
                "detail_attempts": 0,
                "detail_verified": 0,
                "status_counts": dict(
                    sorted(Counter(day.status for day in source_days).items())
                ),
                "application_control_count": sum(
                    day.application_control for day in source_days
                ),
                "application_url_persisted_count": sum(
                    bool(row.get("application_url")) for row in output
                ),
                "reservation_available_count": sum(
                    bool(row.get("reservation_available")) for row in output
                ),
                "municipality_counts": {
                    POHANG_FOREST_EXPERIENCE_MUNICIPALITY_CODE: len(output)
                },
                "semantic_duplicate_count": 0,
                "returned_count": len(output),
                "output_rows": len(output),
                "pagination_complete": True,
                "calendar_complete": True,
                "activity_registry_complete": True,
                "venue_mapping_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not output,
                "no_current_reason": (
                    f"{cutoff.isoformat()} 기준 공식 3~11월 운영 원장에 "
                    "현재·향후 유아숲체험 날짜가 없음"
                    if not output
                    else ""
                ),
            }
        )
        return output, POHANG_FOREST_EXPERIENCE_PARSER, meta
    except Exception as exc:
        message = f"{type(exc).__name__}: {_clean(exc)}"
        meta.update(
            {
                "errors": [message],
                "configured_collection_error": message,
                "returned_count": 0,
                "output_rows": 0,
                "pagination_complete": False,
                "calendar_complete": False,
                "activity_registry_complete": False,
                "venue_mapping_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
            }
        )
        return [], POHANG_FOREST_EXPERIENCE_PARSER, meta
    finally:
        if requester is not None:
            requester.close()


collect = collect_pohang_forest_experience


__all__ = [
    "POHANG_FOREST_EXPERIENCE_PROVIDER",
    "POHANG_FOREST_EXPERIENCE_CANDIDATE_ID",
    "POHANG_FOREST_EXPERIENCE_HOST",
    "POHANG_FOREST_EXPERIENCE_PATH",
    "POHANG_FOREST_EXPERIENCE_MENU_ID",
    "POHANG_FOREST_EXPERIENCE_URL",
    "POHANG_FOREST_EXPERIENCE_OPERATION_MONTHS",
    "POHANG_FOREST_EXPERIENCE_MUNICIPALITY_CODE",
    "POHANG_FOREST_EXPERIENCE_MUNICIPALITY_NAME",
    "POHANG_FOREST_EXPERIENCE_BRANCH",
    "POHANG_FOREST_EXPERIENCE_VENUE",
    "POHANG_FOREST_EXPERIENCE_ADDRESS",
    "POHANG_FOREST_EXPERIENCE_PARSER",
    "POHANG_FOREST_EXPERIENCE_OWNERSHIP_SCOPE",
    "PohangForestExperienceContractError",
    "pohang_forest_experience_month_url",
    "is_pohang_forest_experience_target",
    "collect_pohang_forest_experience",
    "collect",
]
