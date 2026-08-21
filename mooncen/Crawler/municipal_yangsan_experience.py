"""Fail-closed collector for Yangsan's official experience calendar ledger.

The city booking portal exposes eleven first-party ``ia/tour`` programme
partitions in its audited ``체험·견학`` navigation.  Nine are genuine public
experience/visit programmes.  Museum volunteer recruitment and a stopped
one-off puppet performance are accounted for but excluded.

Only public programme pages, anonymous monthly attendance aggregates, and
non-bookable-day metadata are requested.  The daily endpoint is deliberately
blocked because its payload contains applicant names and organisation names.
Application, secured/login, history, cancellation, attachment, and member
endpoints are also never requested.  Every programme page, every month in the
official rolling booking horizon, and the first/last month boundaries are
reconciled.  Any contract drift returns no rows.
"""

from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YANGSAN_EXPERIENCE_PROVIDER = "MUNI_WWW_YANGSAN_GO_KR_42320610"
YANGSAN_EXPERIENCE_URL = (
    "https://www.yangsan.go.kr/booking/ia/tour/prm0140/140/calendar.do"
    "?mid=0204000000"
)
YANGSAN_EXPERIENCE_HOST = "www.yangsan.go.kr"
YANGSAN_EXPERIENCE_MUNICIPALITY_CODE = "4833000000"
YANGSAN_EXPERIENCE_MUNICIPALITY_NAME = "경상남도 양산시"
YANGSAN_EXPERIENCE_PARSER = (
    "yangsan_official_ia_tour_menu_partitions+rolling_month_boundaries+"
    "anonymous_monthly_aggregates+stable_program_and_first_last_months+"
    "volunteer_stopped_performance_exclusion+locked_experience+"
    "no_daily_applicant_application_login_pii_fetch"
)
YANGSAN_EXPERIENCE_OWNERSHIP_SCOPE = (
    "yangsan_integrated_booking_internal_ia_tour_experience_partitions"
)


@dataclass(frozen=True)
class YangsanExperienceProgram:
    mid: str
    prm: str
    master: str
    menu_label: str
    name: str
    branch: str
    page_title: str
    heading: str
    included: bool = True
    exclusion_reason: str = ""

    @property
    def page_path(self) -> str:
        return f"/booking/ia/tour/{self.prm}/{self.master}/calendar.do"

    @property
    def page_url(self) -> str:
        return (
            f"https://{YANGSAN_EXPERIENCE_HOST}{self.page_path}?"
            f"{urlencode({'mid': self.mid})}"
        )

    @property
    def meta_path(self) -> str:
        return (
            f"/booking/ia/tour/{self.prm}/{self.master}/"
            "all/not/bookable/day/list.do"
        )

    @property
    def monthly_path(self) -> str:
        return (
            f"/booking/ia/tour/{self.prm}/{self.master}/"
            "calendar/monthly/list.do"
        )

    @property
    def application_path(self) -> str:
        return (
            f"/booking/ia/tour/{self.prm}/{self.master}/app/apply.do"
        )


YANGSAN_EXPERIENCE_PROGRAMS: tuple[YangsanExperienceProgram, ...] = (
    YangsanExperienceProgram(
        "0204000000",
        "prm0140",
        "140",
        "재난안전체험교육",
        "재난안전체험교육",
        "시민안전체험관",
        "재난안전체험교육 | 시민안전체험관 | 체험·견학 | 홈페이지",
        "재난안전체험교육",
    ),
    YangsanExperienceProgram(
        "0203010000",
        "prm0123",
        "123",
        "신청하기",
        "어린이건강체험관",
        "어린이건강체험관",
        "신청하기 | 어린이건강체험관 | 체험·견학 | 홈페이지",
        "신청하기",
    ),
    YangsanExperienceProgram(
        "0205010000",
        "prm0127",
        "127",
        "전시 단체관람",
        "전시 단체관람",
        "양산시립독립기념관",
        "전시 단체관람 | 독립기념관 | 체험·견학 | 홈페이지",
        "전시 단체관람",
    ),
    YangsanExperienceProgram(
        "0205020000",
        "prm0126",
        "126",
        "어린이 역사체험실 단체 이용",
        "어린이 역사체험실 단체 이용",
        "양산시립독립기념관",
        "어린이 역사체험실 단체 이용 | 독립기념관 | 체험·견학 | 홈페이지",
        "어린이 역사체험실 단체 이용",
    ),
    YangsanExperienceProgram(
        "0206010000",
        "prm0129",
        "129",
        "어린이박물관 단체관람 예약",
        "어린이박물관 단체관람 예약",
        "양산시립박물관",
        "어린이박물관 단체관람 예약 | 시립박물관 | 체험·견학 | 홈페이지",
        "어린이박물관 단체관람 예약",
    ),
    YangsanExperienceProgram(
        "0206020000",
        "prm0132",
        "132",
        "시립박물관 단체 전시관람 예약",
        "시립박물관 단체 전시관람 예약",
        "양산시립박물관",
        "시립박물관 단체 전시관람 예약 | 시립박물관 | 체험·견학 | 홈페이지",
        "시립박물관 단체 전시관람 예약",
    ),
    YangsanExperienceProgram(
        "0206030000",
        "prm0133",
        "133",
        "시립박물관 봉사활동 신청",
        "시립박물관 봉사활동 신청",
        "양산시립박물관",
        "시립박물관 봉사활동 신청 | 시립박물관 | 체험·견학 | 홈페이지",
        "시립박물관 봉사활동 신청",
        False,
        "volunteer_recruitment",
    ),
    YangsanExperienceProgram(
        "0207010000",
        "prm0134",
        "134",
        "신청하기",
        "양산시상하수도사업소 물홍보관",
        "양산시상하수도사업소 물홍보관",
        "신청하기 | 물홍보관 | 체험·견학 | 홈페이지",
        "신청하기",
    ),
    YangsanExperienceProgram(
        "0208010000",
        "prm0149",
        "149",
        "미취학아동 구강보건교육",
        "미취학아동(7세) 구강보건교육",
        "양산시보건소",
        "미취학아동 구강보건교육 | 보건소 | 체험·견학 | 홈페이지",
        "미취학아동 구강보건교육",
    ),
    YangsanExperienceProgram(
        "0208020000",
        "prm0137",
        "137",
        "세계 금연의 날 및 구강보건의 날 기념 인형극 공연",
        "세계 금연의 날 및 구강보건의 날 기념 인형극 공연",
        "양산시보건소",
        "세계 금연의 날 및 구강보건의 날 기념 인형극 공연 | 보건소 | 체험·견학 | 홈페이지",
        "세계 금연의 날 및 구강보건의 날 기념 인형극 공연",
        False,
        "stopped_performance",
    ),
    YangsanExperienceProgram(
        "0209010000",
        "prm0139",
        "139",
        "신청하기",
        "양산시 어린이교통공원 교통교육",
        "양산시 어린이교통공원",
        "신청하기 | 어린이교통공원 교통교육 | 체험·견학 | 홈페이지",
        "신청하기",
    ),
)

_PROGRAM_BY_MID = {program.mid: program for program in YANGSAN_EXPERIENCE_PROGRAMS}
_PROGRAM_BY_PAGE_PATH = {
    program.page_path: program for program in YANGSAN_EXPERIENCE_PROGRAMS
}
_PROGRAM_BY_META_PATH = {
    program.meta_path: program for program in YANGSAN_EXPERIENCE_PROGRAMS
}
_PROGRAM_BY_MONTHLY_PATH = {
    program.monthly_path: program for program in YANGSAN_EXPERIENCE_PROGRAMS
}

YANGSAN_EXPERIENCE_LIVE_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "directory_programs": 11,
    "included_programs": 9,
    "excluded_programs": 2,
    "month_partitions": 28,
    "current_rows": 254,
    "open_rows": 246,
    "closed_rows": 8,
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_TEXT_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_PROGRAM_SCRIPT_RE = re.compile(
    r"yh[.]ia\s*=\s*\{\s*"
    r"prmName\s*:\s*\"(?P<name>[^\"]+)\"\s*,\s*"
    r"masterIdx\s*:\s*\"(?P<master>\d+)\"\s*,\s*"
    r"prmUrl\s*:\s*\"(?P<prm>prm\d+)\"\s*,\s*"
    r"masterCloseYn\s*:\s*\"(?P<closed>[YN])\"\s*"
    r"\}",
    re.DOTALL,
)


class YangsanExperienceContractError(ValueError):
    """Raised when the audited public experience contract changes."""


@dataclass(frozen=True)
class ProgramPageContract:
    program: YangsanExperienceProgram
    closed: bool
    directory: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MonthConfig:
    before: int
    till: int
    headcount: int
    fee: int
    weekdays: tuple[str, ...]
    times: tuple[tuple[str, str, str], ...]

    @property
    def time_ids(self) -> frozenset[str]:
        return frozenset(item[0] for item in self.times)


@dataclass(frozen=True)
class MonthSnapshot:
    year: int
    month: int
    config: MonthConfig
    blocked: tuple[tuple[str, tuple[str, ...]], ...]
    occupancy: tuple[tuple[str, int, int], ...]

    @property
    def blocked_map(self) -> dict[str, frozenset[str]]:
        return {day: frozenset(values) for day, values in self.blocked}

    @property
    def occupancy_map(self) -> dict[str, int]:
        return {day: total for day, _count, total in self.occupancy}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider")).upper()


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _positive(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise YangsanExperienceContractError(
            f"{name} must be a positive integer"
        ) from exc
    if result < 1:
        raise YangsanExperienceContractError(f"{name} must be a positive integer")
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _parse_date(value: Any, field: str) -> date:
    text = _clean(value)
    if not _DATE_TEXT_RE.fullmatch(text):
        raise YangsanExperienceContractError(f"invalid {field} date")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise YangsanExperienceContractError(f"invalid {field} date") from exc


def _exact_target_url(value: str) -> bool:
    got = urlparse(value)
    wanted = urlparse(YANGSAN_EXPERIENCE_URL)
    return bool(
        got.scheme == "https"
        and got.hostname == wanted.hostname
        and got.port is None
        and got.path == wanted.path
        and parse_qs(got.query, keep_blank_values=True)
        == parse_qs(wanted.query, keep_blank_values=True)
        and not got.params
        and not got.fragment
        and not got.username
        and not got.password
    )


def is_yangsan_experience_target(target: Any) -> bool:
    return _provider(target) == YANGSAN_EXPERIENCE_PROVIDER and _exact_target_url(
        _target_url(target)
    )


is_target = is_yangsan_experience_target


def yangsan_experience_meta_url(
    program: YangsanExperienceProgram, year: int, month: int
) -> str:
    if program not in YANGSAN_EXPERIENCE_PROGRAMS or not 2000 <= int(year) <= 2200:
        raise YangsanExperienceContractError("invalid programme/month identity")
    if not 1 <= int(month) <= 12:
        raise YangsanExperienceContractError("invalid month")
    return (
        f"https://{YANGSAN_EXPERIENCE_HOST}{program.meta_path}?"
        f"{urlencode({'year': int(year), 'month': int(month)})}"
    )


def yangsan_experience_monthly_url(
    program: YangsanExperienceProgram, year: int, month: int
) -> str:
    if program not in YANGSAN_EXPERIENCE_PROGRAMS or not 2000 <= int(year) <= 2200:
        raise YangsanExperienceContractError("invalid programme/month identity")
    if not 1 <= int(month) <= 12:
        raise YangsanExperienceContractError("invalid month")
    last = calendar.monthrange(int(year), int(month))[1]
    return (
        f"https://{YANGSAN_EXPERIENCE_HOST}{program.monthly_path}?"
        f"{urlencode({'start': f'{int(year):04d}-{int(month):02d}-01', 'end': f'{int(year):04d}-{int(month):02d}-{last:02d}'})}"
    )


def _validate_public_url(
    value: str,
) -> tuple[str, YangsanExperienceProgram, Optional[tuple[int, int]]]:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != YANGSAN_EXPERIENCE_HOST
        or parsed.port is not None
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise YangsanExperienceContractError("request escaped the audited public host")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path in _PROGRAM_BY_PAGE_PATH:
        program = _PROGRAM_BY_PAGE_PATH[parsed.path]
        if query != {"mid": [program.mid]}:
            raise YangsanExperienceContractError("unexpected programme page query")
        return "page", program, None
    if parsed.path in _PROGRAM_BY_META_PATH:
        program = _PROGRAM_BY_META_PATH[parsed.path]
        if set(query) != {"year", "month"} or any(
            len(values) != 1 for values in query.values()
        ):
            raise YangsanExperienceContractError("unexpected metadata query")
        try:
            year = int(query["year"][0])
            month = int(query["month"][0])
        except ValueError as exc:
            raise YangsanExperienceContractError("invalid metadata month") from exc
        if not 2000 <= year <= 2200 or not 1 <= month <= 12:
            raise YangsanExperienceContractError("invalid metadata month")
        return "meta", program, (year, month)
    if parsed.path in _PROGRAM_BY_MONTHLY_PATH:
        program = _PROGRAM_BY_MONTHLY_PATH[parsed.path]
        if set(query) != {"start", "end"} or any(
            len(values) != 1 for values in query.values()
        ):
            raise YangsanExperienceContractError("unexpected monthly query")
        start = _parse_date(query["start"][0], "monthly start")
        end = _parse_date(query["end"][0], "monthly end")
        expected_end = calendar.monthrange(start.year, start.month)[1]
        if start.day != 1 or end != date(start.year, start.month, expected_end):
            raise YangsanExperienceContractError("monthly query is not one whole month")
        return "monthly", program, (start.year, start.month)
    raise YangsanExperienceContractError(
        "daily, application, login, history, PII, or unrelated endpoint blocked"
    )


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return session


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _Runner:
    def __init__(self, session_factory: SessionFactory, timeout: int):
        self._factory = session_factory
        self._timeout = _positive(timeout, "timeout")
        self._session: Any = None
        self.requests = 0
        self.page_requests = 0
        self.meta_requests = 0
        self.monthly_requests = 0

    def __enter__(self) -> "_Runner":
        self._session = self._factory()
        if self._session is None:
            raise YangsanExperienceContractError("session factory returned no session")
        return self

    def __exit__(self, *_args: Any) -> None:
        _close(self._session)

    def _response(self, url: str, referer: str) -> tuple[Any, str]:
        response = self._session.get(
            url,
            timeout=self._timeout,
            allow_redirects=False,
            headers={"Referer": referer} if referer else None,
        )
        self.requests += 1
        try:
            status = int(getattr(response, "status_code", 200))
        except (TypeError, ValueError):
            status = 0
        if status != 200:
            raise YangsanExperienceContractError(f"unexpected HTTP status {status}")
        if getattr(response, "history", None):
            raise YangsanExperienceContractError("redirects are not accepted")
        final_url = _clean(getattr(response, "url", ""))
        if final_url:
            final = urlparse(final_url)
            expected = urlparse(url)
            final_path = final.path.split(";jsessionid", 1)[0]
            if (
                final.scheme != "https"
                or final.hostname != expected.hostname
                or final_path != expected.path
            ):
                raise YangsanExperienceContractError(
                    "response escaped the audited public endpoint"
                )
        return response, url

    def page(self, url: str, *, referer: str = "") -> BeautifulSoup:
        kind, _program, _month = _validate_public_url(url)
        if kind != "page":
            raise YangsanExperienceContractError("non-page routed to page loader")
        response, _ = self._response(url, referer)
        content = getattr(response, "content", b"")
        text = (
            bytes(content).decode("utf-8", errors="replace")
            if content
            else str(getattr(response, "text", "") or "")
        )
        if not text:
            raise YangsanExperienceContractError("empty programme page")
        self.page_requests += 1
        return BeautifulSoup(text, "lxml")

    def json(
        self, url: str, expected_kind: str, *, referer: str = ""
    ) -> tuple[Mapping[str, Any], YangsanExperienceProgram, tuple[int, int]]:
        kind, program, month = _validate_public_url(url)
        if kind != expected_kind or month is None:
            raise YangsanExperienceContractError("JSON endpoint kind changed")
        response, _ = self._response(url, referer)
        try:
            value = response.json()
        except Exception as exc:
            raise YangsanExperienceContractError("public JSON response changed") from exc
        if not isinstance(value, Mapping):
            raise YangsanExperienceContractError("public JSON root changed")
        if kind == "meta":
            self.meta_requests += 1
        else:
            self.monthly_requests += 1
        return value, program, month


def _directory_contract(soup: BeautifulSoup) -> tuple[tuple[str, str], ...]:
    containers = soup.select("li#lnb_03")
    if len(containers) != 1:
        raise YangsanExperienceContractError("experience navigation root changed")
    observed: dict[str, str] = {}
    for link in containers[0].select("a[data-menu-type='P'][data-menu-id]"):
        mid = _clean(link.get("data-menu-id"))
        label = _clean(link.get_text(" ", strip=True))
        url = _clean(link.get("data-menu-url"))
        if mid in observed:
            raise YangsanExperienceContractError("duplicate experience menu identity")
        if url != f"/booking/contents.do?mid={mid}":
            raise YangsanExperienceContractError("experience menu URL changed")
        observed[mid] = label
    expected = {program.mid: program.menu_label for program in YANGSAN_EXPERIENCE_PROGRAMS}
    if observed != expected:
        raise YangsanExperienceContractError("experience programme directory changed")
    return tuple((mid, observed[mid]) for mid in expected)


def _program_page_contract(
    soup: BeautifulSoup, program: YangsanExperienceProgram
) -> ProgramPageContract:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != program.page_title:
        raise YangsanExperienceContractError(f"{program.master}: page title changed")
    headings = [_clean(node.get_text(" ", strip=True)) for node in soup.select("h3")]
    if headings.count(program.heading) != 1:
        raise YangsanExperienceContractError(f"{program.master}: heading changed")
    scripts = "\n".join(node.get_text(" ", strip=False) for node in soup.select("script"))
    matches = list(_PROGRAM_SCRIPT_RE.finditer(scripts))
    if len(matches) != 1:
        raise YangsanExperienceContractError(f"{program.master}: programme metadata changed")
    values = matches[0].groupdict()
    if (
        _clean(values["name"]) != program.name
        or values["master"] != program.master
        or values["prm"] != program.prm
    ):
        raise YangsanExperienceContractError(f"{program.master}: programme identity drift")
    monthly_literal = f'url: "/booking/ia/tour/{program.prm}/{program.master}/calendar/monthly/list.do"'
    if monthly_literal not in scripts:
        raise YangsanExperienceContractError(f"{program.master}: monthly endpoint changed")
    forms = soup.select("form#applyForm[name='applyForm']")
    if len(forms) != 1:
        raise YangsanExperienceContractError(f"{program.master}: application form changed")
    form = forms[0]
    action = urlparse(_clean(form.get("action")))
    if (
        _clean(form.get("method")).lower() != "post"
        or action.path != program.application_path
        or parse_qs(action.query, keep_blank_values=True) != {"mid": [program.mid]}
    ):
        raise YangsanExperienceContractError(
            f"{program.master}: application form identity changed"
        )
    hidden = {
        (_clean(node.get("id")), _clean(node.get("name")))
        for node in form.select("input[type='hidden']")
    }
    if hidden != {("appDate", "APP_DATE"), ("timeIdx", "TIME_IDX")}:
        raise YangsanExperienceContractError(
            f"{program.master}: application parameter contract changed"
        )
    directory = _directory_contract(soup)
    return ProgramPageContract(program, values["closed"] == "Y", directory)


def _int_field(value: Any, field: str, *, minimum: int = 0) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise YangsanExperienceContractError(f"invalid {field}") from exc
    if result < minimum:
        raise YangsanExperienceContractError(f"invalid {field}")
    return result


def _parse_meta(
    value: Mapping[str, Any],
    program: YangsanExperienceProgram,
    year: int,
    month: int,
) -> tuple[MonthConfig, tuple[tuple[str, tuple[str, ...]], ...]]:
    if value.get("success") is not True:
        raise YangsanExperienceContractError(f"{program.master}: metadata unsuccessful")
    raw_tour = value.get("tour")
    raw_rows = value.get("list")
    if not isinstance(raw_tour, Mapping) or not isinstance(raw_rows, list):
        raise YangsanExperienceContractError(f"{program.master}: metadata root changed")
    before = _int_field(raw_tour.get("bookableBefore"), "bookableBefore", minimum=1)
    till = _int_field(raw_tour.get("bookableTill"), "bookableTill")
    headcount = _int_field(raw_tour.get("headcnt"), "headcnt", minimum=1)
    fee = _int_field(raw_tour.get("fee"), "fee")
    if before > 366 or till > before or fee != 0 or _clean(raw_tour.get("mIdx")) != program.master:
        raise YangsanExperienceContractError(f"{program.master}: tour configuration changed")
    raw_weekdays = raw_tour.get("bookableDaysOfWeekList")
    if not isinstance(raw_weekdays, list):
        raise YangsanExperienceContractError(f"{program.master}: weekdays changed")
    weekdays = tuple(_clean(item) for item in raw_weekdays)
    if (
        not weekdays
        or len(weekdays) != len(set(weekdays))
        or set(weekdays) - {str(item) for item in range(7)}
    ):
        raise YangsanExperienceContractError(f"{program.master}: weekdays changed")
    raw_times = raw_tour.get("timeSetList")
    if not isinstance(raw_times, list) or not raw_times:
        raise YangsanExperienceContractError(f"{program.master}: time sets changed")
    times: list[tuple[str, str, str]] = []
    for item in raw_times:
        if not isinstance(item, Mapping):
            raise YangsanExperienceContractError(f"{program.master}: time set changed")
        identity = _clean(item.get("idx"))
        start = _clean(item.get("startTime"))
        end = _clean(item.get("endTime"))
        if (
            not identity.isdigit()
            or not _TIME_RE.fullmatch(start)
            or not _TIME_RE.fullmatch(end)
            or start >= end
            or _clean(item.get("mIdx")) != program.master
            or _clean(item.get("delYn")) != "N"
        ):
            raise YangsanExperienceContractError(f"{program.master}: time set changed")
        times.append((identity, start, end))
    if len({item[0] for item in times}) != len(times):
        raise YangsanExperienceContractError(f"{program.master}: duplicate time identity")
    config = MonthConfig(before, till, headcount, fee, weekdays, tuple(times))

    blocked: dict[str, set[str]] = {}
    block_row_ids: set[str] = set()
    for item in raw_rows:
        if not isinstance(item, Mapping):
            raise YangsanExperienceContractError(f"{program.master}: block row changed")
        day = _parse_date(item.get("dt"), "blocked")
        raw_ids = item.get("timeIdxList")
        row_id = _clean(item.get("idx"))
        if (
            day.year != year
            or day.month != month
            or not isinstance(raw_ids, list)
            or not row_id.isdigit()
            or row_id in block_row_ids
            or _clean(item.get("mIdx")) != program.master
            or _clean(item.get("delYn")) != "N"
        ):
            raise YangsanExperienceContractError(f"{program.master}: blocked month changed")
        identities = tuple(sorted(_clean(value) for value in raw_ids))
        if (
            not identities
            or len(identities) != len(set(identities))
            or set(identities) - set(config.time_ids)
        ):
            raise YangsanExperienceContractError(f"{program.master}: block identities changed")
        block_row_ids.add(row_id)
        blocked.setdefault(day.isoformat(), set()).update(identities)
    return config, tuple(
        (day, tuple(sorted(identities)))
        for day, identities in sorted(blocked.items())
    )


def _parse_monthly(
    value: Mapping[str, Any],
    program: YangsanExperienceProgram,
    year: int,
    month: int,
) -> tuple[tuple[str, int, int], ...]:
    if value.get("success") is not True or not isinstance(value.get("list"), list):
        raise YangsanExperienceContractError(f"{program.master}: monthly root changed")
    _int_field(value.get("totalCnt"), "monthly totalCnt")
    result: dict[str, tuple[int, int]] = {}
    for item in value["list"]:
        if not isinstance(item, Mapping):
            raise YangsanExperienceContractError(f"{program.master}: monthly row changed")
        start = _parse_date(item.get("SDATE"), "monthly SDATE")
        end = _parse_date(item.get("EDATE"), "monthly EDATE")
        count = _int_field(item.get("CNT"), "monthly CNT")
        total = _int_field(item.get("TOTAL_CNT"), "monthly TOTAL_CNT")
        if start != end or start.year != year or start.month != month:
            raise YangsanExperienceContractError(f"{program.master}: monthly date changed")
        if start.isoformat() in result:
            raise YangsanExperienceContractError(f"{program.master}: duplicate monthly date")
        result[start.isoformat()] = (count, total)
    return tuple((day, *result[day]) for day in sorted(result))


def _month_sequence(start: date, end: date) -> tuple[tuple[int, int], ...]:
    current = date(start.year, start.month, 1)
    result: list[tuple[int, int]] = []
    while current <= end:
        result.append((current.year, current.month))
        current = date(
            current.year + int(current.month == 12),
            current.month % 12 + 1,
            1,
        )
    return tuple(result)


def _load_month(
    runner: _Runner,
    program: YangsanExperienceProgram,
    year: int,
    month: int,
) -> MonthSnapshot:
    meta_url = yangsan_experience_meta_url(program, year, month)
    monthly_url = yangsan_experience_monthly_url(program, year, month)
    raw_meta, got_program, got_month = runner.json(
        meta_url, "meta", referer=program.page_url
    )
    if got_program != program or got_month != (year, month):
        raise YangsanExperienceContractError("metadata route identity changed")
    config, blocked = _parse_meta(raw_meta, program, year, month)
    raw_monthly, got_program, got_month = runner.json(
        monthly_url, "monthly", referer=program.page_url
    )
    if got_program != program or got_month != (year, month):
        raise YangsanExperienceContractError("monthly route identity changed")
    occupancy = _parse_monthly(raw_monthly, program, year, month)
    return MonthSnapshot(year, month, config, blocked, occupancy)


def _build_program_rows(
    program: YangsanExperienceProgram,
    snapshots: Mapping[tuple[int, int], MonthSnapshot],
    cutoff: date,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    first = next(iter(snapshots.values()))
    config = first.config
    horizon_end = cutoff + timedelta(days=config.before)
    open_start = cutoff + timedelta(days=config.till)
    blocked: dict[str, frozenset[str]] = {}
    occupancy: dict[str, int] = {}
    for snapshot in snapshots.values():
        blocked.update(snapshot.blocked_map)
        occupancy.update(snapshot.occupancy_map)
    rows: list[dict[str, Any]] = []
    counters = Counter()
    current = cutoff
    full_capacity = config.headcount * len(config.times)
    while current <= horizon_end:
        day_text = current.isoformat()
        weekday = str((current.weekday() + 1) % 7)
        blocked_ids = blocked.get(day_text, frozenset())
        proof_before_window = current < open_start and day_text in occupancy
        if weekday not in config.weekdays:
            current += timedelta(days=1)
            continue
        if blocked_ids == config.time_ids:
            counters["fully_blocked_dates"] += 1
            current += timedelta(days=1)
            continue
        if current < open_start and not proof_before_window:
            current += timedelta(days=1)
            continue
        occupied = occupancy.get(day_text, 0)
        if occupied > full_capacity:
            raise YangsanExperienceContractError(
                f"{program.master}: monthly occupancy exceeds full capacity"
            )
        open_for_application = current >= open_start and occupied < full_capacity
        available_times = [
            (identity, start, end)
            for identity, start, end in config.times
            if identity not in blocked_ids
        ]
        schedule = ", ".join(f"{start}~{end}" for _identity, start, end in available_times)
        application_start = current - timedelta(days=config.before)
        application_end = current - timedelta(days=config.till)
        row = {
            "provider": YANGSAN_EXPERIENCE_PROVIDER,
            "provider_course_id": (
                f"yangsan-experience:{program.master}:{day_text}"
            ),
            "prefer_incoming_provider_course_id": True,
            "title": f"{program.name} ({day_text})",
            "branch": program.branch,
            "branch_code": f"YANGSAN_EXP_{program.master}",
            "preserve_branch": True,
            "category": program.name,
            "category_raw": f"체험·견학/{program.menu_label}",
            "raw_url": program.page_url,
            "application_url": program.page_url if open_for_application else "",
            "status": "OPEN" if open_for_application else "CLOSED",
            "fee": "무료",
            "period": f"{day_text} ~ {day_text}",
            "start_date": day_text,
            "end_date": day_text,
            "apply_period": (
                f"{application_start.isoformat()} ~ {application_end.isoformat()}"
            ),
            "schedule_raw": schedule,
            "target": "프로그램별 신청 안내 참조",
            "eligibility_raw": "프로그램별 신청 안내 참조",
            "capacity": f"{occupied}/{full_capacity}",
            "capacity_current": occupied,
            "capacity_total": full_capacity,
            "capacity_remaining": max(full_capacity - occupied, 0),
            "room": program.branch,
            "venue_name": program.branch,
            "collection_category": "공공예약",
            "domain_category": "체험·견학",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "체험",
            "service_group_policy": "locked",
            "collection_type": YANGSAN_EXPERIENCE_PARSER,
            "program_type": "체험",
            "application_type": (
                "ONLINE_RESERVATION" if open_for_application else "INFO_ONLY"
            ),
            "reservation_available": open_for_application,
            "municipality_code": YANGSAN_EXPERIENCE_MUNICIPALITY_CODE,
            "municipality_name": YANGSAN_EXPERIENCE_MUNICIPALITY_NAME,
            "sido": "경상남도",
            "sigungu": "양산시",
            "raw_fields": {
                "parser": YANGSAN_EXPERIENCE_PARSER,
                "mid": program.mid,
                "prm": program.prm,
                "master_idx": program.master,
                "programme_date": day_text,
                "bookable_before_days": config.before,
                "bookable_till_days": config.till,
                "headcount_per_time": config.headcount,
                "time_set_count": len(config.times),
                "blocked_time_set_count": len(blocked_ids),
                "monthly_anonymous_occupancy": occupied,
                "proof_before_current_booking_window": proof_before_window,
                "application_control_verified": open_for_application,
                "daily_applicant_endpoint_called": False,
                "application_endpoint_called": False,
                "pii_payload_persisted": False,
            },
        }
        rows.append(row)
        counters["open_rows" if open_for_application else "closed_rows"] += 1
        if proof_before_window:
            counters["booked_before_window_rows"] += 1
        if blocked_ids:
            counters["partially_blocked_rows"] += 1
        current += timedelta(days=1)
    return rows, dict(counters)


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "program_page_requests": 0,
        "meta_requests": 0,
        "monthly_requests": 0,
        "detail_pages": 0,
        "source_total": 0,
        "current_count": 0,
        "returned_count": 0,
        "directory_complete": False,
        "month_boundaries_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "daily_applicant_endpoint_calls": 0,
        "application_endpoint_calls": 0,
        "login_endpoint_calls": 0,
        "pii_payload_persisted": False,
        "configured_collection_error": message,
        "ownership_scope": YANGSAN_EXPERIENCE_OWNERSHIP_SCOPE,
        "municipality_code": YANGSAN_EXPERIENCE_MUNICIPALITY_CODE,
    }


def collect_yangsan_experience_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 30,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future Yangsan experience-date snapshot."""

    if not is_yangsan_experience_target(target):
        return [], YANGSAN_EXPERIENCE_PARSER, _failure(
            "target does not match the audited Yangsan experience owner"
        )

    page_contracts: dict[str, ProgramPageContract] = {}
    month_snapshots: dict[str, dict[tuple[int, int], MonthSnapshot]] = {}
    page_stable = False
    month_stable = False
    source_cap_reached = False
    runner: Optional[_Runner] = None
    try:
        allowed_months = _positive(max_pages, "max_pages")
        allowed_details = _positive(detail_limit, "detail_limit")
        cutoff = _today(today)
        if len(YANGSAN_EXPERIENCE_PROGRAMS) > allowed_details:
            source_cap_reached = True
            raise YangsanExperienceContractError(
                f"detail_limit cap allows {allowed_details} of "
                f"{len(YANGSAN_EXPERIENCE_PROGRAMS)} required programme pages"
            )
        with _Runner(session_factory or _default_session_factory, timeout) as active_runner:
            runner = active_runner
            for program in YANGSAN_EXPERIENCE_PROGRAMS:
                soup = runner.page(
                    program.page_url,
                    referer=YANGSAN_EXPERIENCE_URL if program.master != "140" else "",
                )
                page_contracts[program.master] = _program_page_contract(soup, program)
            directories = {contract.directory for contract in page_contracts.values()}
            if len(directories) != 1:
                raise YangsanExperienceContractError(
                    "programme pages disagree on the experience directory"
                )

            max_month_count = 0
            for program in YANGSAN_EXPERIENCE_PROGRAMS:
                contract = page_contracts[program.master]
                if not program.included or contract.closed:
                    continue
                first_meta_raw, got_program, got_month = runner.json(
                    yangsan_experience_meta_url(program, cutoff.year, cutoff.month),
                    "meta",
                    referer=program.page_url,
                )
                if got_program != program or got_month != (cutoff.year, cutoff.month):
                    raise YangsanExperienceContractError("initial metadata identity changed")
                config, blocked = _parse_meta(
                    first_meta_raw, program, cutoff.year, cutoff.month
                )
                months = _month_sequence(
                    cutoff, cutoff + timedelta(days=config.before)
                )
                max_month_count = max(max_month_count, len(months))
                if len(months) > allowed_months:
                    source_cap_reached = True
                    raise YangsanExperienceContractError(
                        f"max_pages cap allows {allowed_months} of "
                        f"{len(months)} required months for {program.master}"
                    )
                first_monthly_raw, got_program, got_month = runner.json(
                    yangsan_experience_monthly_url(program, cutoff.year, cutoff.month),
                    "monthly",
                    referer=program.page_url,
                )
                if got_program != program or got_month != (cutoff.year, cutoff.month):
                    raise YangsanExperienceContractError("initial monthly identity changed")
                first = MonthSnapshot(
                    cutoff.year,
                    cutoff.month,
                    config,
                    blocked,
                    _parse_monthly(
                        first_monthly_raw, program, cutoff.year, cutoff.month
                    ),
                )
                snapshots = {(cutoff.year, cutoff.month): first}
                for year, month in months[1:]:
                    snapshots[(year, month)] = _load_month(
                        runner, program, year, month
                    )
                if len({snapshot.config for snapshot in snapshots.values()}) != 1:
                    raise YangsanExperienceContractError(
                        f"{program.master}: rolling configuration changed across months"
                    )
                month_snapshots[program.master] = snapshots

            for program in YANGSAN_EXPERIENCE_PROGRAMS:
                observed = _program_page_contract(
                    runner.page(program.page_url, referer=YANGSAN_EXPERIENCE_URL),
                    program,
                )
                if observed != page_contracts[program.master]:
                    raise YangsanExperienceContractError(
                        f"{program.master}: programme page changed during snapshot"
                    )
            page_stable = True

            boundary_rechecks: dict[str, bool] = {}
            for program in YANGSAN_EXPERIENCE_PROGRAMS:
                snapshots = month_snapshots.get(program.master)
                if not snapshots:
                    continue
                boundaries = {next(iter(snapshots)), next(reversed(snapshots))}
                for year, month in sorted(boundaries):
                    observed = _load_month(runner, program, year, month)
                    key = f"{program.master}:{year:04d}-{month:02d}"
                    boundary_rechecks[key] = observed == snapshots[(year, month)]
                    if not boundary_rechecks[key]:
                        raise YangsanExperienceContractError(
                            f"{key}: month boundary changed during snapshot"
                        )
            month_stable = True

            output: list[dict[str, Any]] = []
            program_counts: dict[str, int] = {}
            aggregate_counters: Counter[str] = Counter()
            for program in YANGSAN_EXPERIENCE_PROGRAMS:
                snapshots = month_snapshots.get(program.master)
                if not snapshots:
                    continue
                rows, counters = _build_program_rows(program, snapshots, cutoff)
                output.extend(rows)
                program_counts[program.name] = len(rows)
                aggregate_counters.update(counters)
            identities = [row["provider_course_id"] for row in output]
            if len(identities) != len(set(identities)):
                raise YangsanExperienceContractError("duplicate experience-date identities")
            result = list((dedupe_rows or _dedupe_default)(output))
            if [row["provider_course_id"] for row in result] != identities:
                raise YangsanExperienceContractError(
                    "dedupe changed a complete ordered snapshot"
                )

            excluded = {
                program.name: program.exclusion_reason
                for program in YANGSAN_EXPERIENCE_PROGRAMS
                if not program.included
            }
            closed_included = [
                program.name
                for program in YANGSAN_EXPERIENCE_PROGRAMS
                if program.included and page_contracts[program.master].closed
            ]
            month_partition_count = sum(
                len(snapshots) for snapshots in month_snapshots.values()
            )
            meta = {
                "pages": max_month_count,
                "detail_pages": len(YANGSAN_EXPERIENCE_PROGRAMS),
                "program_page_requests": runner.page_requests,
                "meta_requests": runner.meta_requests,
                "monthly_requests": runner.monthly_requests,
                "physical_requests": runner.requests,
                "directory_program_count": len(YANGSAN_EXPERIENCE_PROGRAMS),
                "included_program_count": sum(
                    program.included for program in YANGSAN_EXPERIENCE_PROGRAMS
                ),
                "excluded_program_count": len(excluded),
                "excluded_programs": excluded,
                "closed_included_programs": closed_included,
                "month_partition_count": month_partition_count,
                "boundary_recheck_count": len(boundary_rechecks),
                "boundary_rechecks": boundary_rechecks,
                "source_total": len(output),
                "source_rows": len(output),
                "current_count": len(output),
                "returned_count": len(result),
                "program_counts": program_counts,
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "application_control_count": sum(
                    bool(row.get("reservation_available")) for row in result
                ),
                "fully_blocked_date_count": aggregate_counters["fully_blocked_dates"],
                "partially_blocked_row_count": aggregate_counters[
                    "partially_blocked_rows"
                ],
                "booked_before_window_row_count": aggregate_counters[
                    "booked_before_window_rows"
                ],
                "directory_complete": True,
                "program_pages_stable": page_stable,
                "month_boundaries_complete": month_stable,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "source_cap_reached": False,
                "no_current_data": not result,
                "no_current_reason": (
                    "complete official rolling experience ledgers have no current rows"
                    if not result
                    else ""
                ),
                "daily_applicant_endpoint_calls": 0,
                "application_endpoint_calls": 0,
                "login_endpoint_calls": 0,
                "history_endpoint_calls": 0,
                "attachment_endpoint_calls": 0,
                "pii_payload_persisted": False,
                "configured_collection_error": "",
                "ownership_scope": YANGSAN_EXPERIENCE_OWNERSHIP_SCOPE,
                "municipality_code": YANGSAN_EXPERIENCE_MUNICIPALITY_CODE,
                "covered_municipalities": [
                    {
                        "code": YANGSAN_EXPERIENCE_MUNICIPALITY_CODE,
                        "sido": "경상남도",
                        "sigungu": "양산시",
                        "full_name": YANGSAN_EXPERIENCE_MUNICIPALITY_NAME,
                    }
                ],
            }
            return result, YANGSAN_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta = _failure(f"{type(exc).__name__}: {_clean(exc)}")
        meta.update(
            {
                "pages": max(
                    (len(snapshots) for snapshots in month_snapshots.values()),
                    default=0,
                ),
                "program_page_requests": runner.page_requests if runner else 0,
                "meta_requests": runner.meta_requests if runner else 0,
                "monthly_requests": runner.monthly_requests if runner else 0,
                "detail_pages": len(page_contracts),
                "source_total": 0,
                "directory_complete": len(page_contracts)
                == len(YANGSAN_EXPERIENCE_PROGRAMS),
                "program_pages_stable": page_stable,
                "month_boundaries_complete": month_stable,
                "source_cap_reached": source_cap_reached,
            }
        )
        return [], YANGSAN_EXPERIENCE_PARSER, meta


collect = collect_yangsan_experience_courses


__all__ = [
    "YANGSAN_EXPERIENCE_PROVIDER",
    "YANGSAN_EXPERIENCE_URL",
    "YANGSAN_EXPERIENCE_HOST",
    "YANGSAN_EXPERIENCE_MUNICIPALITY_CODE",
    "YANGSAN_EXPERIENCE_MUNICIPALITY_NAME",
    "YANGSAN_EXPERIENCE_PARSER",
    "YANGSAN_EXPERIENCE_PROGRAMS",
    "YANGSAN_EXPERIENCE_LIVE_BASELINE",
    "YangsanExperienceContractError",
    "YangsanExperienceProgram",
    "collect_yangsan_experience_courses",
    "is_yangsan_experience_target",
    "yangsan_experience_meta_url",
    "yangsan_experience_monthly_url",
]
