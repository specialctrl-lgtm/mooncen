"""Fail-closed collector for Pohang City's lifelong-learning catalogue.

The official course menu is one server-rendered catalogue at
``/page/classm/class_list.php``.  Search discoveries for ``sc_cl_dbr=BL``
and ``sc_cl_dbr=LC`` are only institution filters, the site root is a
navigation shell, and ``post_view.php`` is the community notice board.  This
module therefore owns the unfiltered course list as the sole canonical
provider and records the other discoveries as non-executing aliases.

The catalogue publishes a declared total, ten rows per page, a continuous
descending display sequence, six mutually exclusive institution groups, and
an empty page immediately after the declared final page.  A snapshot is only
returned when all of those independent contracts agree, page one is unchanged
after the crawl, every current/future row agrees with its detail page, and the
anonymous application control is understood.  Historic malformed dates are
accepted only when their displayed years prove that they ended before the
reference year; they are counted as source anomalies and are never emitted.

Only an explicit allow-list is retained in ``raw_fields``.  Instructor names,
contact names/numbers, free-form descriptions, and downloaded plan content are
deliberately neither returned nor logged.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import math
import re
from threading import Lock, local
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


POHANG_PROVIDER = "MUNI_LIFETIMEEDU_POHANG_GO_KR_4D8BE3DA"
POHANG_CANDIDATE_ID = "MUNI_IR_F65D6D320381"
POHANG_CANONICAL_URL = (
    "https://lifetimeedu.pohang.go.kr/page/classm/class_list.php"
)
POHANG_BASE_URL = "https://lifetimeedu.pohang.go.kr"
POHANG_HOST = "lifetimeedu.pohang.go.kr"
POHANG_LIST_PATH = "/page/classm/class_list.php"
POHANG_DETAIL_PATH = "/page/classm/class_view.php"
POHANG_LOGIN_PATH = "/page/loginm/login.php"
POHANG_PAGE_SIZE = 10
POHANG_MAX_WORKERS = 6
POHANG_PARSER = (
    "pohang_lifelong_unfiltered_six_partition_catalogue+"
    "continuous_sequence+empty_sentinel+page1_recheck+current_detail"
)

POHANG_CITY_CODE = "4711000000"
POHANG_NAMGU_CODE = "4711100000"
POHANG_BUKGU_CODE = "4711300000"
POHANG_COVERED_MUNICIPALITIES: tuple[dict[str, str], ...] = (
    {
        "code": POHANG_CITY_CODE,
        "sido": "경상북도",
        "sigungu": "포항시",
        "full_name": "경상북도 포항시",
    },
    {
        "code": POHANG_NAMGU_CODE,
        "sido": "경상북도",
        "sigungu": "포항시 남구",
        "full_name": "경상북도 포항시 남구",
    },
    {
        "code": POHANG_BUKGU_CODE,
        "sido": "경상북도",
        "sigungu": "포항시 북구",
        "full_name": "경상북도 포항시 북구",
    },
)
POHANG_MUNICIPALITY_NAMES = {
    item["code"]: item["full_name"] for item in POHANG_COVERED_MUNICIPALITIES
}


@dataclass(frozen=True)
class PohangSourceGroup:
    code: str
    label: str
    badge: str
    badge_class: str


POHANG_SOURCE_GROUPS: tuple[PohangSourceGroup, ...] = (
    PohangSourceGroup("BL", "뱃머리평생교육관", "뱃머리", "prow"),
    PohangSourceGroup("LC", "여성문화관", "여성", "female"),
    PohangSourceGroup("CC", "복합문화센터(덕업관/호동관/대도관)", "복합", "complex"),
    PohangSourceGroup("PL", "평생교육기관(공공)", "공공", "public"),
    PohangSourceGroup("CL", "평생교육기관(민간)", "민간", "civil"),
    PohangSourceGroup(
        "RC",
        "포항시 부서별 교육정보 평생학습센터",
        "포항시 부서별 교육정보",
        "dong",
    ),
)
_GROUP_BY_CODE = {item.code: item for item in POHANG_SOURCE_GROUPS}
_GROUP_BY_BADGE = {
    (item.badge_class, item.badge): item for item in POHANG_SOURCE_GROUPS
}


@dataclass(frozen=True)
class PohangAlias:
    provider: str
    candidate_id: str
    url: str
    ownership: str
    reason: str


POHANG_NON_EXECUTING_ALIASES: tuple[PohangAlias, ...] = (
    PohangAlias(
        "MUNI_LIFETIMEEDU_POHANG_GO_KR_BADCC25B",
        "MUNI_IR_96BEB7DEA61A",
        "https://lifetimeedu.pohang.go.kr/",
        "excluded_discovery_shell",
        "site root is navigation and does not own a course catalogue",
    ),
    PohangAlias(
        "MUNI_LIFETIMEEDU_POHANG_GO_KR_67F22341",
        "MUNI_IR_CF038A477DED",
        f"{POHANG_CANONICAL_URL}?sc_cl_dbr=LC",
        "subset",
        "women's culture centre institution filter of the canonical list",
    ),
    PohangAlias(
        "MUNI_LIFETIMEEDU_POHANG_GO_KR_B50BAC11",
        "MUNI_IR_B6D892048A9E",
        f"{POHANG_CANONICAL_URL}?sc_cl_dbr=BL&sc_cl_subj=HEL",
        "subset",
        "Baetmeori health-topic filter of the canonical list",
    ),
    PohangAlias(
        "MUNI_LIFETIMEEDU_POHANG_GO_KR_876C964E",
        "MUNI_IR_D35054FBBF2F",
        "https://lifetimeedu.pohang.go.kr/page/post_view.php?sk=6943945318",
        "excluded_notice",
        "community notice detail is not a structured course record",
    ),
)
POHANG_OWNERSHIP_ALIAS_URLS = tuple(
    item.url for item in POHANG_NON_EXECUTING_ALIASES if item.ownership == "subset"
)
POHANG_EXCLUDED_ALIAS_URLS = tuple(
    item.url for item in POHANG_NON_EXECUTING_ALIASES if item.ownership != "subset"
)


POHANG_RAW_FIELD_ALLOWLIST = frozenset(
    {
        "parser",
        "source_sequence",
        "source_identity",
        "source_group",
        "source_badge",
        "source_status",
        "source_topic",
        "list_institution",
        "list_target",
        "list_apply_period",
        "list_capacity",
        "detail_institution",
        "municipality_evidence",
        "application_control",
        "official_period_anomaly",
    }
)

_INSTITUTION_OPTIONS = (
    ("", "전체"),
    *((item.code, item.label) for item in POHANG_SOURCE_GROUPS),
)
_LIST_HEADERS = (
    "번호",
    "교육기관",
    "강좌명",
    "교육주제",
    "신청/교육기관/교육대상",
    "신청인원/모집인원",
    "강좌상태",
)
_DETAIL_REQUIRED = frozenset(
    {
        "교육기관",
        "교육주제",
        "교육대상",
        "수강료",
        "재료비",
        "교육시간",
        "접수기간",
        "교육기간",
        "모집인원",
        "선발방식",
        "지역제한",
        "담당부서",
    }
)
_STATUS_MAP = {
    "접수전": "SCHEDULED",
    "접수중": "OPEN",
    "접수완료": "CLOSED",
    "조기마감": "CLOSED",
    "폐강": "CLOSED",
}
_NO_DATA_TEXT = "자료가 없습니다."

_NAMGU_UNITS = frozenset(
    {
        "구룡포읍",
        "연일읍",
        "오천읍",
        "대송면",
        "동해면",
        "장기면",
        "호미곶면",
        "상대동",
        "해도동",
        "송도동",
        "청림동",
        "제철동",
        "효곡동",
        "대이동",
    }
)
_BUKGU_UNITS = frozenset(
    {
        "흥해읍",
        "신광면",
        "청하면",
        "송라면",
        "기계면",
        "죽장면",
        "기북면",
        "중앙동",
        "양학동",
        "죽도동",
        "용흥동",
        "우창동",
        "두호동",
        "장량동",
        "환여동",
    }
)
_FACILITY_ADDRESSES = {
    "뱃머리평생교육관": "경상북도 포항시 남구 뱃머리길 39 (상도동)",
    "여성문화관": "경상북도 포항시 북구 새천년대로 933번길 20 (우현동)",
    "복합문화센터(덕업관)": "경상북도 포항시 남구 뱃머리길 39번길 26",
    "복합문화센터(호동관)": "경상북도 포항시 남구 철강로 388 (호동)",
    "복합문화센터(대도관)": "경상북도 포항시 남구 상공로 46번길 13 (상대동)",
}

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d{0,11}\Z")
_DATE_RANGE_RE = re.compile(
    r"(?P<sy>20\d{2})[-./](?P<sm>\d{1,2})[-./](?P<sd>\d{1,2})\s*~\s*"
    r"(?P<ey>20\d{2})[-./](?P<em>\d{1,2})[-./](?P<ed>\d{1,2})"
)
_DATETIME_RANGE_RE = re.compile(
    r"(?P<sy>20\d{2})[-./](?P<sm>\d{1,2})[-./](?P<sd>\d{1,2})"
    r"\s*\[\s*(?P<sh>[0-2]?\d):(?P<smin>[0-5]\d)\s*\]\s*~\s*"
    r"(?P<ey>20\d{2})[-./](?P<em>\d{1,2})[-./](?P<ed>\d{1,2})"
    r"\s*\[\s*(?P<eh>[0-2]?\d):(?P<emin>[0-5]\d)\s*\]"
)
_LIST_PERIOD_RE = re.compile(
    r"(?:기간/대상\s*)?-\s*신청\s*:\s*(?P<apply>.*?)\s*"
    r"-\s*교육\s*:\s*(?P<education>.*?)\s*"
    r"-\s*대상\s*:\s*(?P<target>.*)\Z"
)
_LIST_COUNT_RE = re.compile(
    r"총\s*([\d,]+)건\s*\[\s*([\d,]+)\s*/\s*([\d,]+)\s*페이지\s*\]"
)
_CAPACITY_RE = re.compile(r"([\d,]+)\s*/\s*([\d,]+)\Z")


class PohangContractError(ValueError):
    """The official Pohang source no longer matches the audited contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(
        f"{POHANG_PROVIDER}|{_clean(branch)}".encode("utf-8")
    ).hexdigest()[:12]
    return f"POHANG_BRANCH_{digest}"[:50]


def is_pohang_lifelong_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != POHANG_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname == POHANG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == POHANG_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_pohang_lifelong_target


def pohang_list_url(page: int = 1, source_group: str = "") -> str:
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if source_group and source_group not in _GROUP_BY_CODE:
        raise ValueError("unknown Pohang source group")
    query: list[tuple[str, str | int]] = []
    if source_group:
        query.append(("sc_cl_dbr", source_group))
    if page != 1:
        query.append(("page", page))
    return POHANG_CANONICAL_URL + (f"?{urlencode(query)}" if query else "")


def pohang_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise ValueError("invalid Pohang course identity")
    return f"{POHANG_BASE_URL}{POHANG_DETAIL_PATH}?{urlencode({'id_no': value})}"


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": POHANG_CANONICAL_URL,
        }
    )
    return session


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    status = getattr(value, "status_code", None)
    if status is not None and int(status) != 200:
        raise PohangContractError(f"unexpected HTTP status {status}")
    if getattr(value, "headers", {}).get("Location"):
        raise PohangContractError("redirect response is not accepted")
    content = getattr(value, "content", None)
    if not content:
        raise PohangContractError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _fetch(
    fetcher: Optional[Fetcher], session: Any, url: str, timeout: int
) -> BeautifulSoup:
    if fetcher is None:
        response = session.get(url, timeout=timeout, allow_redirects=False)
        value: Any = response
    else:
        value = fetcher(session, url, timeout)
    return _coerce_soup(value)


class _ThreadSessions:
    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory
        self._local = local()
        self._lock = Lock()
        self._sessions: list[Any] = []

    def get(self) -> Any:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._factory()
            self._local.session = session
            with self._lock:
                self._sessions.append(session)
        return session

    def close(self) -> None:
        for session in reversed(self._sessions):
            close = getattr(session, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _table_headers(table: Any) -> tuple[str, ...]:
    return tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))


def _institution_options(soup: BeautifulSoup) -> tuple[tuple[str, str], ...]:
    select = soup.select_one('form[name="search"] select[name="sc_cl_dbr"]')
    if select is None:
        return ()
    return tuple(
        (_clean(node.get("value")), _clean(node.get_text(" ", strip=True)))
        for node in select.select("option")
    )


def _page_contract(soup: BeautifulSoup) -> tuple[int, int, int]:
    count_nodes = soup.select(".list_num")
    if len(count_nodes) != 1:
        raise PohangContractError("expected one catalogue count marker")
    match = _LIST_COUNT_RE.fullmatch(_clean(count_nodes[0].get_text(" ", strip=True)))
    if not match:
        raise PohangContractError("catalogue count marker changed")
    total, current, declared_last = (
        int(value.replace(",", "")) for value in match.groups()
    )
    expected_last = max(1, math.ceil(total / POHANG_PAGE_SIZE))
    if declared_last != expected_last:
        raise PohangContractError(
            f"declared last page {declared_last} != expected {expected_last}"
        )
    table_nodes = soup.select("table.bbs_list_table")
    if len(table_nodes) != 1 or _table_headers(table_nodes[0]) != _LIST_HEADERS:
        raise PohangContractError("catalogue table/header contract changed")
    return total, current, declared_last


def _active_page(soup: BeautifulSoup) -> Optional[int]:
    nodes = soup.select(".board_list_paging a.active")
    if not nodes:
        return None
    if len(nodes) != 1:
        raise PohangContractError("ambiguous active pagination marker")
    text = _clean(nodes[0].get_text(" ", strip=True))
    if not text.isdigit():
        raise PohangContractError("non-numeric active pagination marker")
    return int(text)


def _safe_source_range(
    raw: Any, reference_day: date
) -> tuple[str, str, str, str]:
    """Return start/end/period/anomaly, allowing only provably old bad dates."""

    value = _clean(raw)
    match = _DATE_RANGE_RE.fullmatch(value)
    if not match:
        # Historic rows include compact ``YYYYMMDD`` typos.  The calendar
        # value is not trusted, but two displayed years strictly before the
        # reference year are sufficient to prove that the row cannot be
        # current/future.
        years = [int(item) for item in re.findall(r"20\d{2}", value)]
        if len(years) >= 2 and max(years) < reference_day.year:
            return "", f"{max(years):04d}-12-31", value, "unparseable_expired_range"
        raise PohangContractError(f"invalid education range: {value}")
    values = {key: int(item) for key, item in match.groupdict().items()}
    try:
        start = date(values["sy"], values["sm"], values["sd"])
        end = date(values["ey"], values["em"], values["ed"])
    except ValueError:
        if max(values["sy"], values["ey"]) < reference_day.year:
            return (
                "",
                f"{values['ey']:04d}-12-31",
                value,
                "invalid_calendar_expired_range",
            )
        raise PohangContractError(f"invalid current-year education range: {value}")
    if start > end:
        if end < reference_day:
            return "", end.isoformat(), value, "reversed_expired_range"
        raise PohangContractError(f"reversed current/future education range: {value}")
    period = f"{start.isoformat()} ~ {end.isoformat()}"
    return start.isoformat(), end.isoformat(), period, ""


def _plain_date_range(raw: Any) -> tuple[str, str, str]:
    value = _clean(raw)
    if not value or value == "~":
        return "", "", ""
    match = _DATE_RANGE_RE.fullmatch(value)
    if not match:
        return "", "", ""
    values = {key: int(item) for key, item in match.groupdict().items()}
    try:
        start = date(values["sy"], values["sm"], values["sd"])
        end = date(values["ey"], values["em"], values["ed"])
    except ValueError:
        return "", "", ""
    if start > end:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _datetime_range(raw: Any) -> tuple[datetime, datetime, str]:
    value = _clean(raw)
    match = _DATETIME_RANGE_RE.fullmatch(value)
    if not match:
        raise PohangContractError(f"invalid detail application range: {value}")
    values = {key: int(item) for key, item in match.groupdict().items()}

    def build(prefix: str) -> datetime:
        hour = values[f"{prefix}h"]
        if hour > 24 or (hour == 24 and values[f"{prefix}min"] != 0):
            raise ValueError("invalid hour")
        result = datetime(
            values[f"{prefix}y"],
            values[f"{prefix}m"],
            values[f"{prefix}d"],
            min(hour, 23),
            values[f"{prefix}min"],
        )
        if hour == 24:
            result = result.replace(hour=0) + timedelta(days=1)
        return result

    try:
        start = build("s")
        end = build("e")
    except ValueError as exc:
        raise PohangContractError(f"invalid detail application range: {value}") from exc
    if start > end:
        raise PohangContractError(f"reversed detail application range: {value}")
    return start, end, f"{start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M}"


def _detail_date_range(raw: Any) -> tuple[date, date, str]:
    value = _clean(raw)
    match = _DATE_RANGE_RE.fullmatch(value)
    if not match:
        raise PohangContractError(f"invalid detail education range: {value}")
    values = {key: int(item) for key, item in match.groupdict().items()}
    try:
        start = date(values["sy"], values["sm"], values["sd"])
        end = date(values["ey"], values["em"], values["ed"])
    except ValueError as exc:
        raise PohangContractError(f"invalid detail education range: {value}") from exc
    if start > end:
        raise PohangContractError(f"reversed detail education range: {value}")
    return start, end, f"{start.isoformat()} ~ {end.isoformat()}"


def _identity_from_href(raw_href: Any) -> str:
    href = _clean(raw_href)
    parsed = urlparse(urljoin(POHANG_BASE_URL, href))
    try:
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or parsed.hostname != POHANG_HOST
        or port is not None
        or parsed.path != POHANG_DETAIL_PATH
        or parsed.fragment
    ):
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) - {"id_no", "page", "sc_cl_dbr", "sc_cl_subj", "sc_cl_ds", "search_txt"}:
        return ""
    identity = (query.get("id_no") or [""])[0]
    return identity if _IDENTITY_RE.fullmatch(identity) else ""


def _group_from_cell(cell: Any) -> Optional[PohangSourceGroup]:
    spans = cell.select("span.type")
    if len(spans) != 1:
        return None
    classes = [item for item in spans[0].get("class", []) if item != "type"]
    if len(classes) != 1:
        return None
    return _GROUP_BY_BADGE.get(
        (classes[0], _clean(spans[0].get_text(" ", strip=True)))
    )


def _cell_value(cell: Any) -> str:
    clone_soup = BeautifulSoup(str(cell), "lxml")
    clone = clone_soup.find("td")
    if clone is None:
        return ""
    for node in clone.select("span.m_th"):
        node.decompose()
    return _clean(clone.get_text(" ", strip=True))


def _list_rows(
    target: Any,
    soup: BeautifulSoup,
    *,
    page: int,
    source_url: str,
    reference_day: date,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    table = soup.select_one("table.bbs_list_table")
    if table is None:
        return [], [f"page {page}: catalogue table is absent"]
    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        sequence_text = _clean(cells[0].get_text(" ", strip=True))
        sequence_match = re.search(r"(\d+)\Z", sequence_text)
        if not sequence_match:
            if len(cells) == 1 and _clean(tr.get_text(" ", strip=True)) == _NO_DATA_TEXT:
                continue
            errors.append(f"page {page}: non-numeric source sequence")
            continue
        sequence = int(sequence_match.group(1))
        if len(cells) != 7:
            errors.append(f"page {page} row {sequence}: expected seven cells")
            continue
        links = cells[2].select("a.subject[href]")
        identity = _identity_from_href(links[0].get("href")) if len(links) == 1 else ""
        title = _clean(links[0].get_text(" ", strip=True)) if len(links) == 1 else ""
        group = _group_from_cell(cells[1])
        status_nodes = cells[6].select("span.attend")
        source_status = (
            _clean(status_nodes[0].get_text(" ", strip=True))
            if len(status_nodes) == 1
            else ""
        )
        raw_period = _clean(cells[4].get_text(" ", strip=True))
        period_match = _LIST_PERIOD_RE.fullmatch(raw_period)
        if not identity or not title:
            errors.append(f"page {page} row {sequence}: missing identity/title")
            continue
        if group is None:
            errors.append(f"page {page} row {sequence}: unknown institution badge")
            continue
        if period_match is None:
            errors.append(f"page {page} row {sequence}: period cell contract changed")
            continue
        try:
            start, end, period, anomaly = _safe_source_range(
                period_match.group("education"), reference_day
            )
        except PohangContractError as exc:
            errors.append(f"page {page} row {sequence}: {exc}")
            continue
        is_current = date.fromisoformat(end) >= reference_day
        normalized_status = _STATUS_MAP.get(source_status, "")
        if not normalized_status and is_current:
            errors.append(
                f"page {page} row {sequence}: unknown current status {source_status}"
            )
            continue
        apply_start, apply_end, apply_period = _plain_date_range(
            period_match.group("apply")
        )
        target_text = _clean(period_match.group("target"))
        capacity_value = _cell_value(cells[5])
        capacity_match = _CAPACITY_RE.search(capacity_value.replace("정원", "").strip())
        capacity_current = (
            int(capacity_match.group(1).replace(",", "")) if capacity_match else None
        )
        capacity_total = (
            int(capacity_match.group(2).replace(",", "")) if capacity_match else None
        )
        if is_current and (capacity_current is None or capacity_total is None):
            errors.append(f"page {page} row {sequence}: invalid current capacity")
            continue
        if is_current and not apply_period:
            errors.append(f"page {page} row {sequence}: incomplete current application period")
            continue
        detail_url = pohang_detail_url(identity)
        raw_fields: dict[str, Any] = {
            "parser": POHANG_PARSER,
            "source_sequence": sequence,
            "source_identity": identity,
            "source_group": group.code,
            "source_badge": group.badge,
            "source_status": source_status,
            "source_topic": _cell_value(cells[3]),
            "list_institution": _cell_value(cells[1]),
            "list_target": target_text,
            "list_apply_period": apply_period or _clean(period_match.group("apply")),
            "list_capacity": capacity_value,
            "official_period_anomaly": anomaly,
        }
        rows.append(
            {
                "provider": POHANG_PROVIDER,
                "provider_course_id": f"{POHANG_PROVIDER}:course:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "category": "education",
                "raw_url": detail_url,
                "application_url": "",
                "reservation_available": False,
                "application_type": "INFORMATION_ONLY",
                "status": normalized_status,
                "period": period,
                "start_date": start,
                "end_date": end,
                "apply_period": apply_period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "target": target_text,
                "fee": "",
                "material_fee": "",
                "schedule_raw": "",
                "capacity": capacity_total,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "venue_name": "",
                "venue_address": "",
                "branch": "",
                "branch_code": "",
                "municipality_code": "",
                "municipality_full_name": "",
                "provider_organizer": "포항시 평생학습원",
                "collection_type": "complete_html_pages+current_detail",
                "raw_fields": raw_fields,
                "_source_url": source_url,
            }
        )
    return rows, errors


def _detail_pairs(table: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for tr in table.select("tbody tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) == 2 and cells[0].name == "th" and "v_subject" not in (
            cells[0].get("class") or []
        ):
            key = _clean(cells[0].get_text(" ", strip=True))
            if key in result:
                raise PohangContractError(f"duplicate detail key {key}")
            result[key] = _clean(cells[1].get_text(" ", strip=True))
    return result


def _detail_heading(table: Any) -> tuple[str, str]:
    nodes = table.select("th.v_subject")
    if len(nodes) != 1:
        return "", ""
    clone_soup = BeautifulSoup(str(nodes[0]), "lxml")
    clone = clone_soup.select_one("th.v_subject")
    if clone is None:
        return "", ""
    statuses = clone.select("span.attend")
    status = _clean(statuses[0].get_text(" ", strip=True)) if len(statuses) == 1 else ""
    for node in clone.select("span.attend, span.essential"):
        node.decompose()
    return _clean(clone.get_text(" ", strip=True)), status


def _detail_institution(table: Any) -> tuple[str, Optional[PohangSourceGroup]]:
    key = table.find("th", string=lambda value: value and _clean(value) == "교육기관")
    if key is None:
        return "", None
    cell = key.find_next_sibling("td")
    if cell is None:
        return "", None
    group = _group_from_cell(cell)
    clone_soup = BeautifulSoup(str(cell), "lxml")
    clone = clone_soup.find("td")
    if clone is None:
        return "", group
    for node in clone.select("span.type"):
        node.decompose()
    return _clean(clone.get_text(" ", strip=True)), group


def _municipality_assignment(
    source_group: str, institution: str, title: str
) -> tuple[str, str, str, str, dict[str, str]]:
    if source_group == "BL":
        branch = "뱃머리평생교육관"
        return (
            POHANG_NAMGU_CODE,
            POHANG_MUNICIPALITY_NAMES[POHANG_NAMGU_CODE],
            branch,
            _FACILITY_ADDRESSES[branch],
            {"basis": "official_BL_facility_address", "value": _FACILITY_ADDRESSES[branch]},
        )
    if source_group == "LC":
        branch = "여성문화관"
        return (
            POHANG_BUKGU_CODE,
            POHANG_MUNICIPALITY_NAMES[POHANG_BUKGU_CODE],
            branch,
            _FACILITY_ADDRESSES[branch],
            {"basis": "official_LC_facility_address", "value": _FACILITY_ADDRESSES[branch]},
        )
    if source_group == "CC":
        values = []
        for token, branch in (
            ("덕업", "복합문화센터(덕업관)"),
            ("호동", "복합문화센터(호동관)"),
            ("대도", "복합문화센터(대도관)"),
        ):
            if token in institution or token in title:
                values.append(branch)
        values = list(dict.fromkeys(values))
        if len(values) != 1:
            raise PohangContractError("complex-centre detail lacks one exact official branch")
        branch = values[0]
        return (
            POHANG_NAMGU_CODE,
            POHANG_MUNICIPALITY_NAMES[POHANG_NAMGU_CODE],
            branch,
            _FACILITY_ADDRESSES[branch],
            {"basis": "official_CC_facility_address", "value": _FACILITY_ADDRESSES[branch]},
        )

    evidence_text = f"{institution} {title}"
    nam_units = sorted(unit for unit in _NAMGU_UNITS if unit in evidence_text)
    buk_units = sorted(unit for unit in _BUKGU_UNITS if unit in evidence_text)
    if nam_units and buk_units:
        raise PohangContractError("institution contains conflicting Nam-gu/Buk-gu evidence")
    branch = _clean(institution.split("/", 1)[0]) or _GROUP_BY_CODE[source_group].label
    if nam_units:
        code = POHANG_NAMGU_CODE
        basis = "official_administrative_unit"
        value = ",".join(nam_units)
    elif buk_units:
        code = POHANG_BUKGU_CODE
        basis = "official_administrative_unit"
        value = ",".join(buk_units)
    else:
        code = POHANG_CITY_CODE
        basis = "official_platform_citywide_institution"
        value = institution
    return (
        code,
        POHANG_MUNICIPALITY_NAMES[code],
        branch,
        "",
        {"basis": basis, "value": value},
    )


def _application_contract(
    soup: BeautifulSoup, table: Any, identity: str
) -> tuple[str, str]:
    content_login = soup.select("a.login_btn[href]")
    for node in content_login:
        parsed = urlparse(urljoin(POHANG_BASE_URL, _clean(node.get("href"))))
        if parsed.hostname != POHANG_HOST or parsed.path != POHANG_LOGIN_PATH:
            raise PohangContractError(f"detail {identity}: unsafe login control")
    suspicious: list[str] = []
    for node in table.find_all_next(["a", "button", "form"]):
        label = _clean(node.get_text(" ", strip=True))
        raw = _clean(node.get("href") or node.get("action") or node.get("onclick"))
        if not label and not raw:
            continue
        if any(token in label for token in ("수강신청", "신청하기", "접수하기")):
            resolved = urljoin(POHANG_BASE_URL, raw) if raw else ""
            parsed = urlparse(resolved)
            if parsed.path not in {POHANG_LIST_PATH, POHANG_LOGIN_PATH}:
                suspicious.append(resolved or label)
    if suspicious:
        raise PohangContractError(
            f"detail {identity}: unreviewed application control {suspicious[0]}"
        )
    if content_login:
        text = _clean(soup.get_text(" ", strip=True))
        if "로그인하지 않았습니다" not in text or "로그인바랍니다" not in text:
            raise PohangContractError(f"detail {identity}: login-gate text changed")
        return "LOGIN_REQUIRED", "generic official login gate"
    return "INFORMATION_ONLY", "no anonymous application control"


def _integer(value: Any) -> Optional[int]:
    match = re.search(r"([\d,]+)", _clean(value))
    return int(match.group(1).replace(",", "")) if match else None


def _enrich_detail(
    row: dict[str, Any], soup: BeautifulSoup, reference_day: date
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("source_identity"))
    tables = soup.select("table.bbs_view_table")
    if len(tables) != 1:
        return [f"detail {identity}: expected one course detail table"]
    table = tables[0]
    try:
        pairs = _detail_pairs(table)
        missing = sorted(_DETAIL_REQUIRED - set(pairs))
        if missing:
            raise PohangContractError(f"missing detail keys {','.join(missing)}")
        detail_title, detail_status = _detail_heading(table)
        if _normalized(detail_title) != _normalized(row.get("title")):
            raise PohangContractError("detail/list title mismatch")
        if detail_status != _clean(row.get("raw_fields", {}).get("source_status")):
            raise PohangContractError("detail/list status mismatch")
        if detail_status not in _STATUS_MAP:
            raise PohangContractError(f"unknown detail status {detail_status}")
        detail_institution, detail_group = _detail_institution(table)
        source_group = _clean(row.get("raw_fields", {}).get("source_group"))
        if detail_group is None or detail_group.code != source_group:
            raise PohangContractError("detail/list institution-group mismatch")
        if not detail_institution:
            raise PohangContractError("empty detail institution")
        topic = _clean(row.get("raw_fields", {}).get("source_topic"))
        if _normalized(pairs["교육주제"]) != _normalized(topic):
            raise PohangContractError("detail/list topic mismatch")
        list_target = _clean(row.get("raw_fields", {}).get("list_target"))
        if list_target:
            # The list appends a short age/sex label while the detail inserts
            # the configured age range before the same qualifier (and may
            # omit the honorific ``만``).  The controlled target category is
            # the exact text before the first parenthesis in both views.
            list_target_category = _clean(list_target.split("(", 1)[0])
            detail_target_category = _clean(pairs["교육대상"].split("(", 1)[0])
            if _normalized(detail_target_category) != _normalized(
                list_target_category
            ):
                raise PohangContractError("detail/list target-category mismatch")
        education_start, education_end, period = _detail_date_range(pairs["교육기간"])
        if (
            education_start.isoformat() != _clean(row.get("start_date"))
            or education_end.isoformat() != _clean(row.get("end_date"))
            or period != _clean(row.get("period"))
        ):
            raise PohangContractError("detail/list education period mismatch")
        apply_start, apply_end, apply_period = _datetime_range(pairs["접수기간"])
        list_apply_start = _clean(row.get("apply_start"))
        list_apply_end = _clean(row.get("apply_end"))
        if (
            apply_start.date().isoformat() != list_apply_start
            or (apply_end - timedelta(microseconds=1)).date().isoformat()
            != list_apply_end
        ):
            raise PohangContractError("detail/list application period mismatch")
        capacity_total = _integer(pairs["모집인원"])
        if capacity_total is None or capacity_total != row.get("capacity_total"):
            raise PohangContractError("detail/list capacity mismatch")
        application_control, control_evidence = _application_contract(
            soup, table, identity
        )
        status = _STATUS_MAP[detail_status]
        if status == "OPEN" and not (
            apply_start.date() <= reference_day <= apply_end.date()
        ):
            raise PohangContractError("open status lies outside application period")
        if status == "SCHEDULED" and apply_end.date() < reference_day:
            raise PohangContractError("scheduled status has an expired application period")
        code, municipality, branch, address, evidence = _municipality_assignment(
            source_group, detail_institution, detail_title
        )
    except PohangContractError as exc:
        return [f"detail {identity}: {exc}"]

    row.update(
        {
            "title": detail_title,
            "status": status,
            "period": period,
            "start_date": education_start.isoformat(),
            "end_date": education_end.isoformat(),
            "apply_period": apply_period,
            "apply_start": apply_start.strftime("%Y-%m-%d %H:%M"),
            "apply_end": apply_end.strftime("%Y-%m-%d %H:%M"),
            "target": pairs["교육대상"],
            "fee": pairs["수강료"],
            "material_fee": pairs["재료비"],
            "schedule_raw": pairs["교육시간"],
            "capacity": capacity_total,
            "capacity_total": capacity_total,
            "venue_name": detail_institution,
            "venue_address": address,
            "branch": branch,
            "branch_code": _branch_code(branch),
            "municipality_code": code,
            "municipality_full_name": municipality,
            "application_url": "",
            "reservation_available": False,
            "application_type": (
                "ONLINE_LOGIN_REQUIRED"
                if status in {"OPEN", "SCHEDULED"}
                and application_control == "LOGIN_REQUIRED"
                else "INFORMATION_ONLY"
            ),
        }
    )
    row["raw_fields"] = {
        **row["raw_fields"],
        "detail_institution": detail_institution,
        "municipality_evidence": {**evidence, "code": code, "full_name": municipality},
        "application_control": {
            "type": application_control,
            "evidence": control_evidence,
        },
    }
    row.pop("_source_url", None)
    if set(row["raw_fields"]) - POHANG_RAW_FIELD_ALLOWLIST:
        return [f"detail {identity}: raw-field allow-list violation"]
    return []


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row.get("raw_fields", {}).get("source_sequence"),
            row.get("raw_fields", {}).get("source_identity"),
            _clean(row.get("title")),
            _clean(row.get("end_date")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    )


def _default_dedupe(rows: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = _clean(row.get("provider_course_id"))
        if key and key not in seen:
            seen.add(key)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "declared_pages": 0,
        "sentinel_page": 0,
        "required_list_requests": 0,
        "page_counts": {},
        "source_total": 0,
        "source_rows": 0,
        "source_group_counts": {},
        "partition_declared_counts": {},
        "source_status_counts": {},
        "current_count": 0,
        "expired_count": 0,
        "current_status_counts": {},
        "municipality_counts": {},
        "branch_counts": {},
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "duplicate_count": 0,
        "duplicate_identity_count": 0,
        "duplicate_url_count": 0,
        "period_anomaly_count": 0,
        "period_anomaly_ids": [],
        "login_required_count": 0,
        "reservation_discovery_links": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "partitions_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "ownership_alias_providers": [
            item.provider for item in POHANG_NON_EXECUTING_ALIASES
        ],
    }


def collect_pohang_lifelong_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 1200,
    detail_limit: int = 1000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = POHANG_MAX_WORKERS,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Pohang lifelong-learning snapshot."""

    meta = _base_meta()
    if not is_pohang_lifelong_target(target):
        meta["configured_collection_error"] = (
            "target is not the canonical Pohang lifelong-learning list"
        )
        return [], POHANG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = (
                "managed session_factory injection is required"
            )
            return [], POHANG_PARSER, meta
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        worker_count = max(1, min(int(max_workers), POHANG_MAX_WORKERS))
        reference_day = _today(today)
    except (TypeError, ValueError):
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], POHANG_PARSER, meta

    errors: list[str] = []
    main_session: Any = None
    page_pool = _ThreadSessions(session_factory)
    detail_pool = _ThreadSessions(session_factory)
    first_soup: Optional[BeautifulSoup] = None
    first_rows: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    declared_last = 0
    source_total = 0
    required_requests = 0
    try:
        main_session = session_factory()
        try:
            first_soup = _fetch(
                fetcher, main_session, pohang_list_url(1), timeout
            )
            if _institution_options(first_soup) != _INSTITUTION_OPTIONS:
                errors.append("official six-group institution menu changed")
            source_total, active, declared_last = _page_contract(first_soup)
            if active != 1 or _active_page(first_soup) != 1:
                errors.append("first-page pagination marker mismatch")
            first_rows, first_errors = _list_rows(
                target,
                first_soup,
                page=1,
                source_url=pohang_list_url(1),
                reference_day=reference_day,
            )
            errors.extend(first_errors)
            meta["declared_pages"] = declared_last
            meta["sentinel_page"] = declared_last + 1
            meta["pagination_detected"] = declared_last > 1
        except Exception as exc:
            errors.append(f"first page fetch/parse failed ({type(exc).__name__})")

        partition_counts: dict[str, int] = {}
        if first_soup is not None and not errors:
            for group in POHANG_SOURCE_GROUPS:
                try:
                    soup = _fetch(
                        fetcher,
                        main_session,
                        pohang_list_url(1, group.code),
                        timeout,
                    )
                    total, current, last = _page_contract(soup)
                    expected_last = max(1, math.ceil(total / POHANG_PAGE_SIZE))
                    if current != 1 or last != expected_last:
                        raise PohangContractError("partition first-page marker mismatch")
                    selected = soup.select(
                        f'select[name="sc_cl_dbr"] option[value="{group.code}"][selected]'
                    )
                    if len(selected) != 1:
                        raise PohangContractError("partition filter is not reflected")
                    partition_counts[group.code] = total
                except Exception as exc:
                    errors.append(
                        f"partition {group.code} fetch/parse failed ({type(exc).__name__})"
                    )
            meta["partition_declared_counts"] = dict(partition_counts)
            if len(partition_counts) != len(POHANG_SOURCE_GROUPS):
                errors.append("six-group partition declaration is incomplete")
            elif sum(partition_counts.values()) != source_total:
                errors.append("six-group partition totals do not equal unfiltered total")

        if declared_last:
            required_requests = declared_last + 8
            meta["required_list_requests"] = required_requests
        if required_requests > allowed_pages:
            meta["source_cap_reached"] = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of {required_requests} required list requests"
            )

        page_rows: dict[int, list[dict[str, Any]]] = {1: first_rows}
        if not errors:
            pages = list(range(2, declared_last + 2))

            def fetch_page(page: int) -> tuple[int, BeautifulSoup]:
                return page, _fetch(
                    fetcher, page_pool.get(), pohang_list_url(page), timeout
                )

            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {executor.submit(fetch_page, page): page for page in pages}
                for future in as_completed(futures):
                    page = futures[future]
                    try:
                        observed_page, soup = future.result()
                        total, current, last = _page_contract(soup)
                        if observed_page != page or total != source_total or last != declared_last:
                            raise PohangContractError("page declaration changed during crawl")
                        expected_active = page if page <= declared_last else None
                        if current != page or _active_page(soup) != expected_active:
                            raise PohangContractError("page/active marker mismatch")
                        parsed, page_errors = _list_rows(
                            target,
                            soup,
                            page=page,
                            source_url=pohang_list_url(page),
                            reference_day=reference_day,
                        )
                        errors.extend(page_errors)
                        expected = (
                            min(
                                POHANG_PAGE_SIZE,
                                source_total - (page - 1) * POHANG_PAGE_SIZE,
                            )
                            if page <= declared_last
                            else 0
                        )
                        if len(parsed) != expected:
                            errors.append(
                                f"page {page}: expected {expected} rows, got {len(parsed)}"
                            )
                        page_rows[page] = parsed
                        meta["page_counts"][page] = len(parsed)
                    except Exception as exc:
                        errors.append(
                            f"page {page} fetch/parse failed ({type(exc).__name__})"
                        )

        if not errors:
            try:
                recheck = _fetch(
                    fetcher, main_session, pohang_list_url(1), timeout
                )
                total, current, last = _page_contract(recheck)
                recheck_rows, recheck_errors = _list_rows(
                    target,
                    recheck,
                    page=1,
                    source_url=pohang_list_url(1),
                    reference_day=reference_day,
                )
                errors.extend(recheck_errors)
                if (
                    total != source_total
                    or current != 1
                    or last != declared_last
                    or _page_signature(recheck_rows) != _page_signature(first_rows)
                ):
                    errors.append("page one changed during complete catalogue crawl")
            except Exception as exc:
                errors.append(f"page-one recheck failed ({type(exc).__name__})")

        meta["pages"] = required_requests if not errors else len(meta["page_counts"])
        if page_rows:
            all_rows = [
                row
                for page in range(1, declared_last + 1)
                for row in page_rows.get(page, [])
            ]
        meta["source_total"] = source_total
        meta["source_rows"] = len(all_rows)
        sequences = [
            int(row.get("raw_fields", {}).get("source_sequence") or 0)
            for row in all_rows
        ]
        identities = [
            _clean(row.get("raw_fields", {}).get("source_identity"))
            for row in all_rows
        ]
        course_ids = [_clean(row.get("provider_course_id")) for row in all_rows]
        raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
        meta["duplicate_identity_count"] = len(identities) - len(set(identities))
        meta["duplicate_count"] = len(course_ids) - len(set(course_ids))
        meta["duplicate_url_count"] = len(raw_urls) - len(set(raw_urls))
        if sequences != list(range(source_total, 0, -1)):
            errors.append("source numbering is not continuous from declared total to one")
        if len(all_rows) != source_total:
            errors.append(
                f"declared total {source_total} != parsed rows {len(all_rows)}"
            )
        if meta["duplicate_identity_count"]:
            errors.append("duplicate official course identities")
        if meta["duplicate_count"] or meta["duplicate_url_count"]:
            errors.append("duplicate provider course IDs/detail URLs")
        group_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_group")) for row in all_rows
        )
        meta["source_group_counts"] = dict(sorted(group_counts.items()))
        if dict(group_counts) != partition_counts:
            errors.append("parsed group counts do not equal official partition declarations")
        source_statuses = Counter(
            _clean(row.get("raw_fields", {}).get("source_status")) for row in all_rows
        )
        meta["source_status_counts"] = dict(sorted(source_statuses.items()))
        anomaly_ids = [
            _clean(row.get("raw_fields", {}).get("source_identity"))
            for row in all_rows
            if _clean(row.get("raw_fields", {}).get("official_period_anomaly"))
        ]
        meta["period_anomaly_ids"] = anomaly_ids
        meta["period_anomaly_count"] = len(anomaly_ids)

        current_rows = [
            row
            for row in all_rows
            if row.get("end_date")
            and date.fromisoformat(_clean(row.get("end_date"))) >= reference_day
        ]
        meta["current_count"] = len(current_rows)
        meta["expired_count"] = len(all_rows) - len(current_rows)
        if len(current_rows) > allowed_details:
            meta["source_cap_reached"] = True
            errors.append(
                f"detail_limit allows {allowed_details} of {len(current_rows)} required current details"
            )

        if not errors:
            def fetch_detail(row: dict[str, Any]) -> tuple[dict[str, Any], BeautifulSoup]:
                return row, _fetch(
                    fetcher,
                    detail_pool.get(),
                    _clean(row.get("raw_url")),
                    timeout,
                )

            enriched: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(fetch_detail, row): row for row in current_rows
                }
                meta["detail_attempts"] = len(futures)
                for future in as_completed(futures):
                    parent = futures[future]
                    identity = _clean(
                        parent.get("raw_fields", {}).get("source_identity")
                    )
                    try:
                        row, soup = future.result()
                        detail_errors = _enrich_detail(row, soup, reference_day)
                        if detail_errors:
                            meta["detail_errors"] += 1
                            errors.extend(detail_errors)
                        else:
                            meta["detail_pages"] += 1
                            enriched.append(row)
                    except Exception as exc:
                        meta["detail_errors"] += 1
                        errors.append(
                            f"detail {identity}: fetch/parse failed ({type(exc).__name__})"
                        )
            current_rows = enriched

        result: list[dict[str, Any]] = []
        if not errors:
            deduper = dedupe_rows or _default_dedupe
            result = list(deduper(current_rows))
            if len(result) != len(current_rows):
                errors.append("dedupe changed an already unique official snapshot")
                result = []
        result.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )
        meta["current_status_counts"] = dict(
            sorted(Counter(_clean(row.get("status")) for row in result).items())
        )
        meta["municipality_counts"] = dict(
            sorted(
                Counter(
                    _clean(row.get("municipality_full_name")) for row in result
                ).items()
            )
        )
        meta["branch_counts"] = dict(
            sorted(Counter(_clean(row.get("branch")) for row in result).items())
        )
        meta["login_required_count"] = sum(
            row.get("application_type") == "ONLINE_LOGIN_REQUIRED" for row in result
        )
        meta["reservation_discovery_links"] = sum(
            bool(row.get("application_url")) for row in result
        )
        meta["pagination_complete"] = (
            not errors
            and len(all_rows) == source_total
            and page_rows.get(declared_last + 1) == []
            and sequences == list(range(source_total, 0, -1))
        )
        meta["partitions_complete"] = (
            not errors
            and dict(group_counts) == partition_counts
            and sum(partition_counts.values()) == source_total
        )
        meta["details_complete"] = (
            not errors
            and meta["detail_pages"] == meta["current_count"]
            and meta["detail_errors"] == 0
        )
        meta["snapshot_complete"] = (
            not errors
            and meta["pagination_complete"]
            and meta["partitions_complete"]
            and meta["details_complete"]
            and meta["duplicate_count"] == 0
            and meta["duplicate_identity_count"] == 0
            and meta["duplicate_url_count"] == 0
            and not meta["source_cap_reached"]
        )
        meta["no_current_data"] = meta["snapshot_complete"] and not result
        if meta["no_current_data"]:
            meta["no_current_reason"] = (
                "all complete Pohang lifelong-learning courses have ended"
            )
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return (
            result if meta["snapshot_complete"] else [],
            POHANG_PARSER,
            meta,
        )
    finally:
        page_pool.close()
        detail_pool.close()
        close = getattr(main_session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
