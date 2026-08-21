"""Fail-closed collector for Yanggu Lifelong Learning Center courses.

Yanggu publishes one official semester page rather than a paginated catalogue.
The page repeats every course in three independent views: an all-course table,
an application-state table, and an adult/child partition.  This collector only
publishes a snapshot when those views agree exactly, a second page snapshot is
unchanged, and every currently open application page retains its course
identity and public application control.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YANGGU_PROVIDER = "MUNI_YANGGU_GO_KR_19704EDA"
YANGGU_CANDIDATE_ID = "MUNI_IR_03B472D50913"
YANGGU_URL = "https://yanggu.go.kr/lll/yglll/index.do"
YANGGU_HOST = "yanggu.go.kr"
YANGGU_PATH = "/lll/yglll/index.do"
YANGGU_BASE_URL = "https://yanggu.go.kr"
YANGGU_PROGRAM_PATH = "/lll/yglll/pageview.do"
YANGGU_PROGRAM_URL = (
    f"{YANGGU_BASE_URL}{YANGGU_PROGRAM_PATH}?"
    + urlencode((('url', 'sub02a'), ('keyvalue', 'sub02')))
)
YANGGU_MUNICIPALITY_CODE = "5180000000"
YANGGU_MUNICIPALITY_NAME = "강원특별자치도 양구군"
YANGGU_BRANCH = "양구군 평생학습관"
YANGGU_ADDRESS = "강원특별자치도 양구군 양구읍 박수근로 286-5"
YANGGU_PHONE = "033-480-2421"
YANGGU_SESSION_REQUEST_LIMIT = 100
YANGGU_PARSER = (
    "yanggu_lifelong_complete_single_page+"
    "cross_table_partitions+open_application_details"
)

HtmlFetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_CAPACITY_RE = re.compile(r"([1-9]\d*)\s*/\s*(\d+)")
_SEMESTER_RANGE_RE = re.compile(
    r"(?P<term>상반기|하반기)\s*"
    r"(?P<sm>\d{1,2})\.\s*(?P<sd>\d{1,2})\.\((?P<sw>[월화수목금토일])\)\s*"
    r"~\s*(?P<em>\d{1,2})\.\s*(?P<ed>\d{1,2})\.\((?P<ew>[월화수목금토일])\)"
)
_APPLICATION_RANGE_RE = re.compile(
    r"(?P<sm>\d{1,2})\.\s*(?P<sd>\d{1,2})\.\((?P<sw>[월화수목금토일])\)\s*"
    r"~\s*(?P<em>\d{1,2})\.\s*(?P<ed>\d{1,2})\.\((?P<ew>[월화수목금토일])\)\s*"
    r"(?P<sh>[01]\d|2[0-3]):(?P<smin>[0-5]\d)\s*"
    r"~\s*(?P<eh>[01]\d|2[0-3]):(?P<emin>[0-5]\d)"
)
_WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}

_TOTAL_TABLE = "example8ad"
_RECRUITING_TABLE = "example8ae"
_ONGOING_TABLE = "example8af"
_SHORT_TABLE = "example8ag"
_ADULT_TABLE = "example8ah"
_CHILD_TABLE = "example8ai"
_TABLE_IDS = (
    _TOTAL_TABLE,
    _RECRUITING_TABLE,
    _ONGOING_TABLE,
    _SHORT_TABLE,
    _ADULT_TABLE,
    _CHILD_TABLE,
)
_TOTAL_HEADERS = (
    "번호",
    "분류",
    "구분",
    "강좌명",
    "강사명",
    "정원",
    "대상",
    "시간",
    "교육장소",
    "강의계획서",
    "수강신청",
)
_STATE_HEADERS = _TOTAL_HEADERS[:5] + ("정원/신청",) + _TOTAL_HEADERS[6:]


class YangguContractError(ValueError):
    """The official Yanggu source no longer matches the reviewed contract."""


@dataclass(frozen=True)
class YangguSemester:
    name: str
    start: date
    end: date
    apply_start_at: str
    apply_end_at: str


@dataclass(frozen=True)
class YangguProgramSnapshot:
    semester: YangguSemester
    rows: tuple[dict[str, Any], ...]
    table_counts: Mapping[str, int]
    fingerprint: tuple[Any, ...]


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def is_yanggu_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != YANGGU_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname == YANGGU_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == YANGGU_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_yanggu_target


def yanggu_program_url() -> str:
    return YANGGU_PROGRAM_URL


def yanggu_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return f"{YANGGU_BASE_URL}{YANGGU_PROGRAM_PATH}?" + urlencode(
        (("url", "sub09ak"), ("keyvalue", "sub09"), ("idx", value))
    ) + "#Book1"


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": YANGGU_URL,
        }
    )
    return session


def _strict_response(response: Any) -> Any:
    if int(getattr(response, "status_code", 0)) != 200:
        raise YangguContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise YangguContractError("redirect response is not accepted")
    if not getattr(response, "content", b""):
        raise YangguContractError("empty HTTP response")
    return response


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return _strict_response(session.get(url, timeout=timeout, allow_redirects=False))


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("HTML fetcher returned neither HTML nor a response")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _program_link_owned(anchor: Any, *, fragment: str) -> bool:
    parsed = urlparse(urljoin(YANGGU_URL, _clean(anchor.get("href"))))
    return (
        parsed.scheme == "https"
        and parsed.hostname == YANGGU_HOST
        and parsed.path == YANGGU_PROGRAM_PATH
        and parse_qsl(parsed.query, keep_blank_values=True)
        == [("url", "sub02a"), ("keyvalue", "sub02")]
        and parsed.fragment == fragment
        and "수강신청" in _clean(anchor.get_text(" ", strip=True))
    )


def _validate_landing(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != YANGGU_BRANCH:
        raise YangguContractError("landing ownership title changed")
    links = [
        anchor
        for anchor in soup.select("a[href]")
        if _program_link_owned(anchor, fragment="Book2")
    ]
    if not links:
        raise YangguContractError("landing no longer links to the official course table")


def _validate_program_ownership(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != YANGGU_BRANCH:
        raise YangguContractError("program page ownership title changed")
    og_titles = soup.select("meta[property='og:title']")
    if len(og_titles) != 1 or YANGGU_BRANCH not in _clean(og_titles[0].get("content")):
        raise YangguContractError("program page OpenGraph ownership changed")
    if not any(
        _program_link_owned(anchor, fragment="Book2")
        for anchor in soup.select("a[href]")
    ):
        raise YangguContractError("program navigation ownership changed")


def _direct_cells(row: Any) -> list[Any]:
    return row.select(":scope > th, :scope > td")


def _table_headers(table: Any) -> tuple[str, ...]:
    rows = table.select("thead > tr")
    if len(rows) != 1:
        raise YangguContractError(f"table {table.get('id')} header structure changed")
    return tuple(_clean(cell.get_text(" ", strip=True)) for cell in _direct_cells(rows[0]))


def _identity_from_application(anchor: Any) -> str:
    parsed = urlparse(urljoin(YANGGU_PROGRAM_URL, _clean(anchor.get("href"))))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != YANGGU_HOST
        or parsed.path != YANGGU_PROGRAM_PATH
        or query[:2] != [("url", "sub09ak"), ("keyvalue", "sub09")]
        or len(query) != 3
        or query[2][0] != "idx"
        or parsed.fragment != "Book1"
        or not _IDENTITY_RE.fullmatch(query[2][1])
        or _clean(anchor.get_text(" ", strip=True)) != "수강신청"
    ):
        raise YangguContractError("course application identity URL changed")
    return query[2][1]


def _plan_marker(cell: Any, identity: str) -> str:
    text = _clean(cell.get_text(" ", strip=True))
    anchors = cell.select("a[href]")
    if text == "없음" and not anchors:
        return "none"
    if len(anchors) != 1 or not text:
        raise YangguContractError(f"course {identity} plan marker changed")
    parsed = urlparse(urljoin(YANGGU_PROGRAM_URL, _clean(anchors[0].get("href"))))
    if (
        parsed.scheme != "https"
        or parsed.hostname != YANGGU_HOST
        or parsed.path != "/lll/yglll/bbs_download.do"
        or parse_qs(parsed.query, keep_blank_values=True) != {"dwnfilea": [identity]}
        or parsed.fragment
    ):
        raise YangguContractError(f"course {identity} plan identity changed")
    return f"download:{identity}:{text}"


def _parse_table(table: Any, table_id: str) -> tuple[dict[str, Any], ...]:
    expected_headers = _TOTAL_HEADERS if table_id == _TOTAL_TABLE else _STATE_HEADERS
    if _table_headers(table) != expected_headers:
        raise YangguContractError(f"table {table_id} headers changed")
    parsed_rows: list[dict[str, Any]] = []
    for expected_number, tr in enumerate(table.select("tbody > tr")):
        cells = tr.select(":scope > td")
        if len(cells) != len(expected_headers):
            raise YangguContractError(f"table {table_id} row width changed")
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        if values[0] != str(expected_number):
            raise YangguContractError(f"table {table_id} row numbering changed")
        anchors = cells[10].select("a[href]")
        if len(anchors) != 1:
            raise YangguContractError(f"table {table_id} application control changed")
        identity = _identity_from_application(anchors[0])
        if not all(values[index] for index in (1, 2, 3, 4, 5, 7, 8, 9, 10)):
            raise YangguContractError(f"course {identity} has an empty required field")
        if table_id == _TOTAL_TABLE:
            if not values[5].isdigit() or int(values[5]) <= 0:
                raise YangguContractError(f"course {identity} capacity changed")
            capacity_total = int(values[5])
            capacity_current: Optional[int] = None
        else:
            match = _CAPACITY_RE.fullmatch(values[5])
            if match is None:
                raise YangguContractError(f"course {identity} capacity/application changed")
            capacity_total, capacity_current = map(int, match.groups())
        plan = _plan_marker(cells[9], identity)
        parsed_rows.append(
            {
                "identity": identity,
                "classification": values[1],
                "category": values[2],
                "title": values[3],
                "instructor": values[4],
                "capacity_total": capacity_total,
                "capacity_current": capacity_current,
                "target": values[6] or values[1],
                "target_source": values[6],
                "schedule": values[7],
                "venue": values[8],
                "plan": plan,
                "application_url": yanggu_application_url(identity),
            }
        )
    identities = [row["identity"] for row in parsed_rows]
    if len(identities) != len(set(identities)):
        raise YangguContractError(f"table {table_id} contains duplicate course identities")
    return tuple(parsed_rows)


def _row_contract(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row["classification"],
        row["category"],
        row["title"],
        row["instructor"],
        row["capacity_total"],
        row["target_source"],
        row["schedule"],
        row["venue"],
        row["plan"],
        row["application_url"],
    )


def _dated(year: int, month: str, day: str, weekday: str, field: str) -> date:
    try:
        value = date(year, int(month), int(day))
    except ValueError as exc:
        raise YangguContractError(f"{field} date is invalid") from exc
    if value.weekday() != _WEEKDAYS[weekday]:
        raise YangguContractError(f"{field} weekday does not match the official date")
    return value


def _semester_ranges(value: str, year: int) -> dict[str, tuple[date, date]]:
    result: dict[str, tuple[date, date]] = {}
    for match in _SEMESTER_RANGE_RE.finditer(value):
        term = match.group("term")
        start = _dated(year, match.group("sm"), match.group("sd"), match.group("sw"), term)
        end = _dated(year, match.group("em"), match.group("ed"), match.group("ew"), term)
        if end < start or term in result:
            raise YangguContractError("semester operation range changed")
        result[term] = (start, end)
    if set(result) != {"상반기", "하반기"}:
        raise YangguContractError("both semester operation ranges are required")
    return result


def _application_range(value: str, year: int, field: str) -> tuple[str, str]:
    match = _APPLICATION_RANGE_RE.fullmatch(value)
    if match is None:
        raise YangguContractError(f"{field} application range changed")
    start = _dated(year, match.group("sm"), match.group("sd"), match.group("sw"), field)
    end = _dated(year, match.group("em"), match.group("ed"), match.group("ew"), field)
    start_at = f"{start.isoformat()} {match.group('sh')}:{match.group('smin')}"
    end_at = f"{end.isoformat()} {match.group('eh')}:{match.group('emin')}"
    if end_at < start_at:
        raise YangguContractError(f"{field} application range is reversed")
    return start_at, end_at


def _semester_contract(
    soup: BeautifulSoup,
    total_table: Any,
    cutoff: date,
) -> YangguSemester:
    years = {
        int(value)
        for value in _YEAR_RE.findall(_clean(total_table.get_text(" ", strip=True)))
    }
    if len(years) != 1:
        raise YangguContractError("course plan year is missing or ambiguous")
    year = next(iter(years))
    overview_tables = soup.select("table.table-bordered.f18.text-center.bg_fff")
    if len(overview_tables) != 2:
        raise YangguContractError("semester overview table structure changed")
    operation_rows = overview_tables[0].select("tbody > tr")
    if len(operation_rows) != 4:
        raise YangguContractError("semester operation overview changed")
    operation: dict[str, str] = {}
    for row in operation_rows:
        cells = _direct_cells(row)
        if len(cells) != 2:
            raise YangguContractError("semester operation row changed")
        key, value = (_clean(cell.get_text(" ", strip=True)) for cell in cells)
        if key in operation:
            raise YangguContractError("duplicate semester operation field")
        operation[key] = value
    if set(operation) != {"프로그램 운영시간", "수강료", "학습시간", "면제대상"}:
        raise YangguContractError("semester operation fields changed")
    if (
        "대면강좌 40,000원" not in operation["수강료"]
        or "비대면 강좌 20,000원" not in operation["수강료"]
    ):
        raise YangguContractError("semester tuition policy changed")
    operations = _semester_ranges(operation["프로그램 운영시간"], year)

    schedule_rows = overview_tables[1].select("tbody > tr")
    if len(schedule_rows) != 7:
        raise YangguContractError("semester recruitment overview changed")
    header = tuple(_clean(cell.get_text(" ", strip=True)) for cell in _direct_cells(schedule_rows[0]))
    if header != ("구분", "상반기 기간 및 시간", "하반기 기간 및 시간", "비고"):
        raise YangguContractError("semester recruitment headers changed")
    recruitment = None
    for row in schedule_rows[1:]:
        cells = _direct_cells(row)
        if len(cells) == 4 and _clean(cells[0].get_text(" ", strip=True)) == "모집기간":
            if recruitment is not None:
                raise YangguContractError("duplicate recruitment row")
            recruitment = (
                _application_range(_clean(cells[1].get_text(" ", strip=True)), year, "상반기"),
                _application_range(_clean(cells[2].get_text(" ", strip=True)), year, "하반기"),
            )
    if recruitment is None:
        raise YangguContractError("semester recruitment period is missing")
    choices = [
        YangguSemester("상반기", *operations["상반기"], *recruitment[0]),
        YangguSemester("하반기", *operations["하반기"], *recruitment[1]),
    ]
    future = [semester for semester in choices if semester.end >= cutoff]
    return min(future, key=lambda item: item.end) if future else max(
        choices, key=lambda item: item.end
    )


def _snapshot_fingerprint(
    semester: YangguSemester,
    tables: Mapping[str, tuple[dict[str, Any], ...]],
) -> tuple[Any, ...]:
    return (
        semester,
        tuple(
            (
                table_id,
                tuple(
                    (
                        row["identity"],
                        _row_contract(row),
                        row["capacity_current"],
                    )
                    for row in tables[table_id]
                ),
            )
            for table_id in _TABLE_IDS
        ),
    )


def _branch_code() -> str:
    branch = f"{YANGGU_MUNICIPALITY_NAME} · {YANGGU_BRANCH}"
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    return f"{YANGGU_PROVIDER}:CENTER:{digest}"[:100]


def _parse_program_page(
    soup: BeautifulSoup,
    target: Any,
    cutoff: date,
) -> YangguProgramSnapshot:
    _validate_program_ownership(soup)
    tables: dict[str, tuple[dict[str, Any], ...]] = {}
    nodes: dict[str, Any] = {}
    for table_id in _TABLE_IDS:
        matches = soup.select(f"table#{table_id}")
        if len(matches) != 1:
            raise YangguContractError(f"expected exactly one table {table_id}")
        nodes[table_id] = matches[0]
        tables[table_id] = _parse_table(matches[0], table_id)
    if tables[_SHORT_TABLE]:
        raise YangguContractError("unaudited short-course table is non-empty")

    total = {row["identity"]: row for row in tables[_TOTAL_TABLE]}
    recruiting = {row["identity"]: row for row in tables[_RECRUITING_TABLE]}
    ongoing = {row["identity"]: row for row in tables[_ONGOING_TABLE]}
    adult = {row["identity"]: row for row in tables[_ADULT_TABLE]}
    child = {row["identity"]: row for row in tables[_CHILD_TABLE]}
    if set(recruiting) & set(ongoing) or set(recruiting) | set(ongoing) != set(total):
        raise YangguContractError("application-state tables do not partition all courses")
    if set(adult) & set(child) or set(adult) | set(child) != set(total):
        raise YangguContractError("adult/child tables do not partition all courses")
    if any(row["classification"] != "성인대상" for row in adult.values()):
        raise YangguContractError("adult table contains another classification")
    if any(row["classification"] != "아동대상" for row in child.values()):
        raise YangguContractError("child table contains another classification")

    state = {**{identity: "OPEN" for identity in recruiting}, **{identity: "CLOSED" for identity in ongoing}}
    categories = {**adult, **child}
    for identity, base in total.items():
        state_row = recruiting.get(identity) or ongoing.get(identity)
        category_row = categories[identity]
        if state_row is None:
            raise YangguContractError(f"course {identity} is missing a state row")
        if _row_contract(base) != _row_contract(state_row):
            raise YangguContractError(f"course {identity} differs in the state table")
        if _row_contract(base) != _row_contract(category_row):
            raise YangguContractError(f"course {identity} differs in the category table")
        if state_row["capacity_current"] != category_row["capacity_current"]:
            raise YangguContractError(f"course {identity} application count differs")

    semester = _semester_contract(soup, nodes[_TOTAL_TABLE], cutoff)
    branch = f"{YANGGU_MUNICIPALITY_NAME} · {YANGGU_BRANCH}"
    rows: list[dict[str, Any]] = []
    for identity, base in total.items():
        state_row = recruiting.get(identity) or ongoing[identity]
        capacity_current = int(state_row["capacity_current"])
        remote = "비대면" in " ".join(
            (base["title"], base["schedule"], base["venue"])
        )
        open_now = state[identity] == "OPEN"
        rows.append(
            {
                "provider": YANGGU_PROVIDER,
                "provider_course_id": f"{YANGGU_PROVIDER}:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": base["title"],
                "branch": branch,
                "branch_code": _branch_code(),
                "period": f"{semester.start.isoformat()} ~ {semester.end.isoformat()}",
                "start_date": semester.start.isoformat(),
                "end_date": semester.end.isoformat(),
                "apply_period": f"{semester.apply_start_at} ~ {semester.apply_end_at}",
                "apply_start_date": semester.apply_start_at[:10],
                "apply_end_date": semester.apply_end_at[:10],
                "status": state[identity],
                "category": base["category"],
                "program_type": "평생학습 강좌",
                "domain_category": "교육",
                "collection_category": "공공예약",
                "collection_type": "complete_single_page+cross_table_validation",
                "source_group": "municipal_reservation",
                "operator_type": "지자체/공공기관",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "instructor": base["instructor"],
                "schedule_raw": base["schedule"],
                "target": base["target"],
                "room": base["venue"],
                "venue": base["venue"],
                "address": YANGGU_ADDRESS,
                "phone": YANGGU_PHONE,
                "description": "",
                "price": 20000 if remote else 40000,
                "price_text": "비대면 강좌 20,000원" if remote else "대면강좌 40,000원",
                "capacity_total": base["capacity_total"],
                "capacity_current": capacity_current,
                "capacity_remaining": max(0, base["capacity_total"] - capacity_current),
                "application_method": "인터넷 접수 후 추첨",
                "application_methods": ["온라인", "추첨"],
                "reservation_available": open_now,
                "application_url": base["application_url"] if open_now else "",
                "application_type": "ONLINE_RESERVATION" if open_now else "",
                "raw_url": base["application_url"],
                "source_url": _clean(_target_value(target, "url")),
                "raw_fields": {
                    "idx": identity,
                    "semester": semester.name,
                    "classification": base["classification"],
                    "category": base["category"],
                    "target_source": base["target_source"],
                    "plan_marker": base["plan"],
                    "source_status_table": (
                        "모집중인 강좌" if open_now else "교육중인 강좌"
                    ),
                    "capacity_can_include_waitlist": capacity_current > base["capacity_total"],
                    "data_plane": "cross_validated_html_tables",
                },
            }
        )
    return YangguProgramSnapshot(
        semester=semester,
        rows=tuple(rows),
        table_counts={table_id: len(tables[table_id]) for table_id in _TABLE_IDS},
        fingerprint=_snapshot_fingerprint(semester, tables),
    )


def _validate_application_detail(soup: BeautifulSoup, identity: str) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != YANGGU_BRANCH:
        raise YangguContractError(f"course {identity} application ownership changed")
    forms = soup.select("form[name='join_form']")
    if len(forms) != 1:
        raise YangguContractError(f"course {identity} application form changed")
    form = forms[0]
    action = urlparse(urljoin(YANGGU_PROGRAM_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "post"
        or action.scheme != "https"
        or action.hostname != YANGGU_HOST
        or action.path != YANGGU_PROGRAM_PATH
        or parse_qsl(action.query, keep_blank_values=True)
        != [("url", "sub09am"), ("keyvalue", "sub09")]
        or action.fragment != "Book1"
    ):
        raise YangguContractError(f"course {identity} application form action changed")
    idx_nodes = form.select("input[name='idx']")
    join_nodes = form.select("input[name='join']")
    first_accept = form.select("input[name='tap_accept1'][value='1']")
    second_accept = form.select("input[name='tap_accept2'][value='1']")
    submits = form.select("button[type='submit']")
    if (
        len(idx_nodes) != 1
        or _clean(idx_nodes[0].get("value")) != identity
        or len(join_nodes) != 1
        or _clean(join_nodes[0].get("value")) != "1"
        or len(first_accept) != 1
        or len(second_accept) != 1
        or len(submits) != 1
        or "수강신청" not in _clean(submits[0].get_text(" ", strip=True))
    ):
        raise YangguContractError(f"course {identity} application identity/control changed")


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str, **extra: Any) -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "landing_requests": 0,
        "program_requests": 0,
        "program_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "unique_id_count": 0,
        "duplicate_count": 0,
        "expired_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": message,
        **extra,
    }


def collect_yanggu_lifelong_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 1,
    detail_limit: int = 100,
    *,
    fetcher: Optional[HtmlFetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 1,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future Yanggu lifelong-course snapshot."""

    if not is_yanggu_target(target):
        return [], YANGGU_PARSER, _failure(
            "target does not match the exact reviewed Yanggu Lifelong Learning URL"
        )
    try:
        page_cap = int(max_pages)
        detail_cap = int(detail_limit)
        requested_workers = max(1, int(max_workers))
    except (TypeError, ValueError):
        return [], YANGGU_PARSER, _failure("collection limits are not integers")
    if page_cap < 1:
        return [], YANGGU_PARSER, _failure(
            "max_pages cap does not allow the canonical page",
            source_cap_reached=True,
        )
    if detail_cap < 0:
        return [], YANGGU_PARSER, _failure(
            "detail_limit cap is negative", source_cap_reached=True
        )

    current_fetcher = fetcher or _default_fetcher
    current_session_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    cutoff = _today(today)
    sessions: list[Any] = []
    active_session: Any = None
    active_requests = 0

    def next_session() -> Any:
        nonlocal active_session, active_requests
        if active_session is None or active_requests >= YANGGU_SESSION_REQUEST_LIMIT:
            if active_session is not None:
                _close_quietly(active_session)
            active_session = current_session_factory()
            active_requests = 0
            sessions.append(active_session)
        active_requests += 1
        return active_session

    landing_requests = 0
    program_requests = 0
    program_rechecks = 0
    detail_attempts = 0
    detail_pages = 0
    source_cap_reached = False
    snapshot: Optional[YangguProgramSnapshot] = None
    errors: list[str] = []

    try:
        try:
            landing = _coerce_soup(current_fetcher(next_session(), YANGGU_URL, timeout))
            landing_requests += 1
            _validate_landing(landing)
        except Exception as exc:
            errors.append(f"landing: {type(exc).__name__}: {_clean(exc)}")
        if errors:
            requests_made = landing_requests
            return [], YANGGU_PARSER, _failure(
                "; ".join(errors),
                pages=requests_made,
                request_count=requests_made,
                landing_requests=landing_requests,
            )

        try:
            program = _coerce_soup(
                current_fetcher(next_session(), YANGGU_PROGRAM_URL, timeout)
            )
            program_requests += 1
            snapshot = _parse_program_page(program, target, cutoff)
        except Exception as exc:
            errors.append(f"program: {type(exc).__name__}: {_clean(exc)}")
        if errors or snapshot is None:
            requests_made = landing_requests + program_requests
            return [], YANGGU_PARSER, _failure(
                "; ".join(errors) or "program snapshot is missing",
                pages=requests_made,
                request_count=requests_made,
                landing_requests=landing_requests,
                program_requests=program_requests,
            )

        all_rows = list(snapshot.rows)
        identities = [row["raw_fields"]["idx"] for row in all_rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"canonical rows contain {duplicate_count} duplicate identities")
        current_rows = [
            row for row in all_rows if date.fromisoformat(row["end_date"]) >= cutoff
        ]
        open_current_rows = [row for row in current_rows if row["status"] == "OPEN"]
        if detail_cap < len(open_current_rows):
            source_cap_reached = True
            errors.append(
                f"detail_limit cap {detail_cap} is below required "
                f"{len(open_current_rows)} open application details"
            )

        if not errors:
            detail_attempts = len(open_current_rows)
            for row in open_current_rows:
                identity = row["raw_fields"]["idx"]
                try:
                    detail = _coerce_soup(
                        current_fetcher(next_session(), row["raw_url"], timeout)
                    )
                    _validate_application_detail(detail, identity)
                    detail_pages += 1
                except Exception as exc:
                    errors.append(
                        f"course {identity} application detail: "
                        f"{type(exc).__name__}: {_clean(exc)}"
                    )

        try:
            recheck = _coerce_soup(
                current_fetcher(next_session(), YANGGU_PROGRAM_URL, timeout)
            )
            program_requests += 1
            program_rechecks += 1
            rechecked = _parse_program_page(recheck, target, cutoff)
            if rechecked.fingerprint != snapshot.fingerprint:
                raise YangguContractError("program page changed during traversal")
        except Exception as exc:
            errors.append(
                f"program recheck changed or invalid: "
                f"{type(exc).__name__}: {_clean(exc)}"
            )

        cleaned = list(current_rows)
        if not errors:
            try:
                deduped = list(current_dedupe(cleaned))
            except Exception as exc:
                errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                deduped = []
            if len(deduped) != len(cleaned):
                errors.append(
                    f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}"
                )
            cleaned = deduped

        details_complete = (
            not source_cap_reached
            and detail_pages == len(open_current_rows)
            and detail_attempts == len(open_current_rows)
        )
        snapshot_complete = (
            not errors
            and program_rechecks == 1
            and duplicate_count == 0
            and details_complete
        )
        if not snapshot_complete:
            cleaned = []

        request_count = landing_requests + program_requests + detail_attempts
        status_counts = Counter(row["status"] for row in current_rows)
        category_counts = Counter(row["category"] for row in current_rows)
        classification_counts = Counter(
            row["raw_fields"]["classification"] for row in current_rows
        )
        meta: dict[str, Any] = {
            "pages": 1,
            "request_count": request_count,
            "landing_requests": landing_requests,
            "program_requests": program_requests,
            "program_rechecks": program_rechecks,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "source_rows": len(all_rows),
            "validated_count": len(all_rows),
            "valid_count": len(all_rows),
            "invalid_count": 0,
            "unique_id_count": len(set(identities)),
            "duplicate_count": duplicate_count,
            "expired_count": len(all_rows) - len(current_rows),
            "current_count": len(current_rows),
            "open_current_count": len(open_current_rows),
            "returned_count": len(cleaned),
            "semester": snapshot.semester.name,
            "semester_start": snapshot.semester.start.isoformat(),
            "semester_end": snapshot.semester.end.isoformat(),
            "table_counts": dict(snapshot.table_counts),
            "current_status_counts": dict(status_counts),
            "category_counts": dict(category_counts),
            "classification_counts": dict(classification_counts),
            "capacity_total": sum(row["capacity_total"] for row in current_rows),
            "application_count": sum(row["capacity_current"] for row in current_rows),
            "waitlist_or_over_capacity_count": sum(
                row["capacity_current"] > row["capacity_total"]
                for row in current_rows
            ),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in cleaned
            ),
            "pagination_detected": False,
            "pagination_complete": snapshot_complete,
            "pagination_exhausted": snapshot_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "recursion_depth": 0,
            "network_concurrency": 1,
            "requested_max_workers": requested_workers,
            "no_current_data": snapshot_complete and not current_rows,
            "no_current_reason": (
                "the complete Yanggu semester table has no current/future courses"
                if snapshot_complete and not current_rows
                else ""
            ),
        }
        if errors:
            meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return cleaned, YANGGU_PARSER, meta
    finally:
        for session in sessions:
            _close_quietly(session)


collect_yanggu_target = collect_yanggu_lifelong_courses
collect = collect_yanggu_lifelong_courses


__all__ = [
    "YANGGU_BASE_URL",
    "YANGGU_ADDRESS",
    "YANGGU_BRANCH",
    "YANGGU_CANDIDATE_ID",
    "YANGGU_HOST",
    "YANGGU_MUNICIPALITY_CODE",
    "YANGGU_MUNICIPALITY_NAME",
    "YANGGU_PARSER",
    "YANGGU_PATH",
    "YANGGU_PROGRAM_PATH",
    "YANGGU_PROGRAM_URL",
    "YANGGU_PROVIDER",
    "YANGGU_PHONE",
    "YANGGU_URL",
    "YangguContractError",
    "YangguProgramSnapshot",
    "YangguSemester",
    "collect",
    "collect_yanggu_lifelong_courses",
    "collect_yanggu_target",
    "is_target",
    "is_yanggu_target",
    "yanggu_application_url",
    "yanggu_program_url",
]
