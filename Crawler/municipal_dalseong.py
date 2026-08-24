"""Fail-closed education collector for Dalseong-gun integrated reservation.

The Dalseong Facilities Corporation portal does not expose one combined data
endpoint.  Four dated course catalogues use POST pagination, the fossil museum
uses a second GET catalogue/detail contract, and two swimming matrices require
selecting every advertised programme before the server reveals whether a
monthly course exists.  This collector exhausts all three shapes, verifies the
immediate empty page and page-one stability, and discards the complete snapshot
when any source, identity, detail, ownership, or cap contract changes.

The canonical promoted target is the search candidate that already points at
the Dalseong Culture Center catalogue.  Related branches are owned by the same
official integrated-reservation navigation and are collected under that one
provider, with their real facility names preserved.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


DALSEONG_PROVIDER = "MUNI_YEYAK_DSSISEOL_OR_KR_8334ABCD"
DALSEONG_CANDIDATE_ID = "MUNI_IR_6A55E4969590"
DALSEONG_URL = "https://yeyak.dssiseol.or.kr/index.do?menu_id=00005155"
DALSEONG_HOST = "yeyak.dssiseol.or.kr"
DALSEONG_PATH = "/index.do"
DALSEONG_MUNICIPALITY_CODE = "2771000000"
DALSEONG_MUNICIPALITY_NAME = "대구광역시 달성군"
DALSEONG_PARSER = (
    "dalseong_integrated_four_post_catalogues+fossil_programmes+"
    "sport_matrix_selection+complete_sentinels+current_details"
)
DALSEONG_MAX_WORKERS = 8
DALSEONG_SESSION_REQUEST_LIMIT = 70

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Requester = Callable[[Any, str, str, int, Optional[Mapping[str, str]]], Any]


@dataclass(frozen=True)
class CourseSource:
    key: str
    menu_id: str
    inst_id: str
    branch: str
    branch_contract: str
    headers: tuple[str, ...]

    @property
    def url(self) -> str:
        return f"https://{DALSEONG_HOST}{DALSEONG_PATH}?menu_id={self.menu_id}"

    @property
    def list_action(self) -> str:
        return (
            f"/index.do?menu_id={self.menu_id}&menu_link="
            f"/dss/yeyak/dssCulture/listViewCultureClass.do?instId={self.inst_id}"
        )

    @property
    def detail_action(self) -> str:
        return (
            f"/index.do?menu_id={self.menu_id}&menu_link="
            f"/dss/yeyak/dssCulture/viewCultureClass.do?instId={self.inst_id}"
        )


_STANDARD_HEADERS = (
    "번호",
    "강좌명",
    "요일",
    "교육시간",
    "수강기간",
    "모집인원(명)",
    "상태",
    "온라인등록",
)
_GYM_HEADERS = _STANDARD_HEADERS[:5] + ("신청/모집인원(명)",) + _STANDARD_HEADERS[6:]
_TECH_HEALTH_HEADERS = (
    "번호",
    "강좌명",
    "요일",
    "수강기간",
    "모집인원(명)",
    "상태",
    "온라인등록",
)

COURSE_SOURCES = (
    CourseSource(
        "women_culture",
        "00004951",
        "DSS_INST_00000001",
        "달성군 여성문화복지센터",
        "달성군여성문화복지센터",
        _STANDARD_HEADERS,
    ),
    CourseSource(
        "dalseong_culture",
        "00005155",
        "DSS_INST_00000002",
        "달성문화센터",
        "달성문화센터",
        _STANDARD_HEADERS,
    ),
    CourseSource(
        "citizen_gym",
        "00005211",
        "DSS_INST_00000004",
        "달성군민체육관",
        "달성군민체육관",
        _GYM_HEADERS,
    ),
    CourseSource(
        "techno_health",
        "00006502",
        "DSS_INST_00000050",
        "달성테크노스포츠센터",
        "달성테크노스포츠센터",
        _TECH_HEALTH_HEADERS,
    ),
)
_COURSE_BY_KEY = {source.key: source for source in COURSE_SOURCES}


@dataclass(frozen=True)
class MuseumSource:
    key: str
    menu_id: str
    label: str
    dated: bool

    @property
    def url(self) -> str:
        return f"https://{DALSEONG_HOST}{DALSEONG_PATH}?menu_id={self.menu_id}"


MUSEUM_SOURCES = (
    MuseumSource("fossil_weekend", "00007350", "주말개인교육", True),
    MuseumSource("fossil_special", "00007341", "특강", True),
    MuseumSource("fossil_group", "00007333", "평일단체교육", False),
)
_MUSEUM_BY_KEY = {source.key: source for source in MUSEUM_SOURCES}
FOSSIL_BRANCH = "달성화석박물관"
FOSSIL_INST_ID = "DSS_INST_00000070"
FOSSIL_DETAIL_PATH = "/dss/yeyak/msm/listViewMsmRsvtDetail.do"


@dataclass(frozen=True)
class SportMatrixSource:
    key: str
    menu_id: str
    result_menu_id: str
    inst_id: str
    branch: str
    form_id: str
    result_path: str

    @property
    def url(self) -> str:
        return f"https://{DALSEONG_HOST}{DALSEONG_PATH}?menu_id={self.menu_id}"

    @property
    def result_action(self) -> str:
        return f"/index.do?menu_id={self.result_menu_id}&menu_link={self.result_path}?instId={self.inst_id}"


SPORT_MATRIX_SOURCES = (
    SportMatrixSource(
        "national_sports",
        "00005196",
        "00005198",
        "DSS_INST_00000003",
        "달성 국민체육센터",
        "dssDailyPtVO",
        "/dss/manage/ptCrs/listViewDailyPtCrs.do",
    ),
    SportMatrixSource(
        "techno_swimming",
        "00006533",
        "00006533",
        "DSS_INST_00000050",
        "달성테크노스포츠센터",
        "dssCultureVO",
        "/dss/yeyak/dssCulture/listViewSportsClassCrs.do",
    ),
)

_SPACE_RE = re.compile(r"\s+")
_COURSE_CONTROL_RE = re.compile(
    r"fnCrsInfo\(\s*['\"](GDS_\d{8})['\"]\s*,\s*"
    r"['\"](CRS_\d{8})['\"]"
)
_MUSEUM_CONTROL_RE = re.compile(r"fnDetail\(\s*['\"](MSM_PRGRM_\d{8})['\"]")
_SPORT_CONTROL_RE = re.compile(
    r"fnSearchCrs\(\s*['\"](ONLN_RSVT_CLSF_\d{8})['\"]\s*,\s*"
    r"['\"]([^'\"]+)['\"]"
)
_SHORT_DATE_RANGE_RE = re.compile(
    r"^\s*(?:교\s*)?(\d{2})\.(\d{2})\.(\d{2})\s*~\s*"
    r"(\d{2})\.(\d{2})\.(\d{2})\s*$"
)
_LONG_DATE_RANGE_RE = re.compile(
    r"^\s*(20\d{2})[.-](\d{2})[.-](\d{2})\s*~\s*"
    r"(20\d{2})[.-](\d{2})[.-](\d{2})\s*$"
)
_SCRIPT_DATE_RE = re.compile(r"fnSetDate\(\s*['\"](20\d{6})['\"]")
_INTEGER_RE = re.compile(r"\d[\d,]*")
_TIME_RANGE_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*~\s*(\d{1,2}):(\d{2})\s*$")
_TITLE_TIME_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2})(?!\d)")
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "현장접수": "OPEN",
    "접수대기": "SCHEDULED",
    "온라인 접수마감": "CLOSED",
    "접수마감": "CLOSED",
    "마감": "CLOSED",
    "폐강": "CLOSED",
}
_EMPTY_SENTINEL = "There is no registered data. Please choose another seach keyword"
_EMPTY_SENTINELS = frozenset({_EMPTY_SENTINEL, "등록된 자료가 없습니다."})


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).lower()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_dalseong_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != DALSEONG_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname == DALSEONG_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == DALSEONG_PATH
        and not parsed.params
        and query == {"menu_id": ["00005155"]}
        and not parsed.fragment
    )


is_target = is_dalseong_target


def course_list_url(source: CourseSource | str) -> str:
    item = _COURSE_BY_KEY[source] if isinstance(source, str) else source
    return f"https://{DALSEONG_HOST}{item.list_action}"


def course_detail_request_url(source: CourseSource | str) -> str:
    item = _COURSE_BY_KEY[source] if isinstance(source, str) else source
    return f"https://{DALSEONG_HOST}{item.detail_action}"


def course_post_data(
    source: CourseSource | str,
    page: int,
    *,
    gds_id: str = "",
    crs_id: str = "",
) -> dict[str, str]:
    if isinstance(source, str):
        _COURSE_BY_KEY[source]
    return {
        "searchCondition": "",
        "crs_id": _clean(crs_id),
        "gds_id": _clean(gds_id),
        "gds_clsf_dcd": "DSS_0001_01",
        "apply_ch": "",
        "bfr_searchCondition": "",
        "searchKeyword": "",
        "pageIndex": str(max(1, int(page))),
    }


def museum_list_url(source: MuseumSource | str, page: int = 1) -> str:
    item = _MUSEUM_BY_KEY[source] if isinstance(source, str) else source
    return f"https://{DALSEONG_HOST}{DALSEONG_PATH}?" + urlencode(
        (("menu_id", item.menu_id), ("pageIndex", str(max(1, int(page)))))
    )


def museum_detail_url(source: MuseumSource | str, identity: str) -> str:
    item = _MUSEUM_BY_KEY[source] if isinstance(source, str) else source
    return f"https://{DALSEONG_HOST}{DALSEONG_PATH}?" + urlencode(
        (
            ("inst_id", FOSSIL_INST_ID),
            ("msm_prgrm_id", _clean(identity)),
            ("msm_prgrm_dcd", ""),
            ("rsvt_clsf_dcd", "RSVT_CLSF_DCD_11"),
            ("menu_link", FOSSIL_DETAIL_PATH),
            ("menu_id", item.menu_id),
        )
    )


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 0))
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(value, "history", None):
        raise ValueError("redirected response is not accepted")
    if _clean(getattr(value, "headers", {}).get("Location")):
        raise ValueError("redirect location is not accepted")
    body = getattr(value, "content", None)
    if body is None:
        body = getattr(value, "text", None)
    if not body:
        raise ValueError("empty HTTP response")
    return BeautifulSoup(body, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _system_owned(soup: BeautifulSoup) -> bool:
    title = _normalized(soup.title.get_text(" ", strip=True) if soup.title else "")
    return "달성군시설관리공단통합예약시스템" in title


def _empty_sentinel(soup: BeautifulSoup) -> bool:
    matches = [node for node in soup.find_all(string=True) if _clean(node) in _EMPTY_SENTINELS]
    if len(matches) == 1:
        return True
    content = soup.select_one("#content")
    content_text = _clean(content.get_text(" ", strip=True) if content else "")
    return sum(content_text.count(value) for value in _EMPTY_SENTINELS) == 1


def _form_hidden(form: Any) -> dict[str, str]:
    return {
        _clean(item.get("name")): _clean(item.get("value"))
        for item in form.select("input[name]")
        if _clean(item.get("name"))
    }


def _form_action_menu(form: Any) -> str:
    parsed = urlparse(_clean(form.get("action")))
    return (parse_qs(parsed.query, keep_blank_values=True).get("menu_id") or [""])[0]


def _pagination_numbers(soup: BeautifulSoup, function_name: str) -> list[int]:
    pattern = re.compile(rf"{re.escape(function_name)}\(\s*(\d+)")
    values: list[int] = []
    for item in soup.select(".pagination a[onclick]"):
        match = pattern.search(_clean(item.get("onclick")))
        if match:
            values.append(int(match.group(1)))
    return values


def _course_page_contract(
    soup: BeautifulSoup,
    source: CourseSource,
    requested_page: int,
    *,
    expected_total: Optional[int] = None,
    sentinel: bool = False,
) -> tuple[int, list[Any]]:
    page_branch = source.branch.replace("달성군 ", "")
    if not _system_owned(soup) or _normalized(page_branch) not in _normalized(
        soup.select_one("#content").get_text(" ", strip=True) if soup.select_one("#content") else ""
    ):
        raise ValueError("course page ownership mismatch")
    forms = soup.select("form#dssCultureVO")
    if len(forms) != 1 or _clean(forms[0].get("method")).lower() != "post":
        raise ValueError("course page form contract changed")
    form = forms[0]
    if _form_action_menu(form) != source.menu_id:
        raise ValueError("course page form menu changed")
    hidden = _form_hidden(form)
    if hidden.get("pageIndex") != str(requested_page):
        raise ValueError("course page identity mismatch")
    html = str(soup)
    if source.list_action not in html or source.detail_action not in html:
        raise ValueError("course page controller contract changed")
    tables = []
    for table in soup.select("table"):
        headers = tuple(_clean(item.get_text(" ", strip=True)) for item in table.select("thead th"))
        if headers == source.headers:
            tables.append(table)
    if len(tables) != 1:
        raise ValueError("course catalogue table contract changed")
    rows = tables[0].select("tbody > tr")
    if sentinel:
        if not _empty_sentinel(soup) or len(rows) != 1:
            raise ValueError("course sentinel contract changed")
        cells = rows[0].find_all("td", recursive=False)
        if (
            len(cells) != 1
            or _clean(cells[0].get_text(" ", strip=True)) not in _EMPTY_SENTINELS
            or _clean(cells[0].get("colspan")) != "99"
        ):
            raise ValueError("course sentinel row contract changed")
        return expected_total or requested_page - 1, []
    numbers = _pagination_numbers(soup, "fn_search")
    total = max(numbers or [1])
    if expected_total is not None and total != expected_total:
        raise ValueError("course declared page count changed during traversal")
    strong = {_clean(item.get_text(" ", strip=True)) for item in soup.select(".pagination strong")}
    if strong != {str(requested_page)}:
        raise ValueError("course current-page marker changed")
    return total, rows


def _short_range(value: str) -> tuple[date, date]:
    match = _SHORT_DATE_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise ValueError("course education date range changed")
    values = [int(item) for item in match.groups()]
    start = date(2000 + values[0], values[1], values[2])
    end = date(2000 + values[3], values[4], values[5])
    if end < start:
        raise ValueError("course education date range is reversed")
    return start, end


def _long_range(value: str) -> tuple[date, date]:
    match = _LONG_DATE_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise ValueError("detail date range changed")
    values = [int(item) for item in match.groups()]
    start = date(values[0], values[1], values[2])
    end = date(values[3], values[4], values[5])
    if end < start:
        raise ValueError("detail date range is reversed")
    return start, end


def _capacity(value: str, *, allow_zero_total: bool = False) -> tuple[Optional[int], int]:
    values = [int(item.replace(",", "")) for item in _INTEGER_RE.findall(_clean(value))]
    if values == [0] and allow_zero_total:
        return None, 0
    if len(values) == 1 and values[0] > 0:
        return None, values[0]
    if len(values) == 2 and 0 <= values[0] <= values[1] and values[1] > 0:
        return values[0], values[1]
    raise ValueError("course capacity contract changed")


def _time_range(value: str) -> str:
    match = _TIME_RANGE_RE.fullmatch(_clean(value))
    if match is None:
        raise ValueError("course time range changed")
    start_hour, start_minute, end_hour, end_minute = (int(item) for item in match.groups())
    if not (
        0 <= start_hour <= 23
        and 0 <= end_hour <= 23
        and 0 <= start_minute <= 59
        and 0 <= end_minute <= 59
        and (end_hour, end_minute) > (start_hour, start_minute)
    ):
        raise ValueError("course time range is invalid")
    return f"{start_hour:02d}:{start_minute:02d} ~ {end_hour:02d}:{end_minute:02d}"


def _title_time_range(title: str) -> str:
    matches = _TITLE_TIME_RE.findall(_clean(title))
    if len(matches) != 1:
        raise ValueError("course title time contract changed")
    return _time_range(matches[0])


def _course_row(target: Any, source: CourseSource, tr: Any, page: int) -> dict[str, Any]:
    cells = tr.find_all("td", recursive=False)
    if len(cells) != len(source.headers):
        raise ValueError("course row cell count changed")
    values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
    fields = dict(zip(source.headers, values))
    number = fields["번호"]
    title_cell = cells[source.headers.index("강좌명")]
    title_anchor = title_cell.select_one("a[onclick*='fnCrsInfo']")
    if title_anchor is None:
        raise ValueError("course title control missing")
    title = _clean(title_anchor.get_text(" ", strip=True))
    match = _COURSE_CONTROL_RE.search(_clean(title_anchor.get("onclick")))
    if not number.isdigit() or not title or _normalized(title) != _normalized(fields["강좌명"]) or match is None:
        raise ValueError("course stable identity/title contract changed")
    gds_id, crs_id = match.groups()
    start, end = _short_range(fields["수강기간"])
    source_status = fields["상태"]
    status = _STATUS_MAP.get(source_status)
    if status is None:
        raise ValueError(f"unknown course status: {source_status}")
    online_control = _clean(fields["온라인등록"])
    if not online_control:
        raise ValueError("course registration control missing")
    zero_capacity_addon = (
        source.key == "techno_health"
        and status == "CLOSED"
        and source_status == "접수마감"
        and online_control == "현장접수"
        and _normalized(title).endswith("샤워포함")
    )
    capacity_current, capacity_total = _capacity(
        fields.get("신청/모집인원(명)") or fields.get("모집인원(명)") or "",
        allow_zero_total=zero_capacity_addon,
    )
    list_schedule = _clean(fields.get("교육시간", ""))
    schedule = "" if list_schedule in {"", "-"} else _time_range(list_schedule)
    extra = _target_value(target, "extra")
    extra = extra if isinstance(extra, Mapping) else {}
    available = status == "OPEN"
    application_type = ""
    if source_status == "현장접수":
        application_type = "OFFLINE_REGISTRATION"
    elif available:
        application_type = "ONLINE_RESERVATION"
    row: dict[str, Any] = {
        "provider": DALSEONG_PROVIDER,
        "provider_course_id": f"{DALSEONG_PROVIDER}:course:{crs_id}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": source.branch,
        "branch_code": source.key,
        "preserve_branch": True,
        "branch_url": source.url,
        "raw_url": f"{source.url}#course-{crs_id}",
        "application_url": source.url if available and application_type == "ONLINE_RESERVATION" else "",
        "application_type": application_type,
        "reservation_available": available,
        "status": status,
        "period": _clean(fields["수강기간"]).removeprefix("교 "),
        "start_date": start,
        "end_date": end,
        "schedule": schedule,
        "schedule_raw": " ".join(part for part in (fields["요일"], schedule) if part),
        "capacity": capacity_total,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_remaining": (max(0, capacity_total - capacity_current) if capacity_current is not None else None),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_category": _clean(extra.get("collection_category")) or "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "municipality_code": DALSEONG_MUNICIPALITY_CODE,
        "municipality_name": DALSEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "candidate_id": DALSEONG_CANDIDATE_ID,
            "source_kind": source.key,
            "source_number": number,
            "source_page": page,
            "gds_id": gds_id,
            "crs_id": crs_id,
            "source_status": source_status,
            "online_control": online_control,
            "excluded_non_course_addon": zero_capacity_addon,
            "list_schedule_missing": list_schedule in {"", "-"},
            "list_fields": fields,
        },
    }
    return row


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    tables = []
    for table in soup.select("table"):
        caption = table.select_one("caption")
        if caption is not None and "예약하기 상세" in _clean(caption.get_text(" ", strip=True)):
            tables.append(table)
    if len(tables) != 1:
        raise ValueError("course detail table contract changed")
    pairs: dict[str, str] = {}
    for tr in tables[0].select("tr"):
        header = tr.find("th", recursive=False)
        cell = tr.find("td", recursive=False)
        if header is None or cell is None:
            continue
        key = _clean(header.get_text(" ", strip=True))
        if not key or key in pairs:
            raise ValueError("course detail labels changed")
        pairs[key] = _clean(cell.get_text(" ", strip=True))
    return pairs


def _enrich_course_detail(row: dict[str, Any], source: CourseSource, soup: BeautifulSoup) -> None:
    if not _system_owned(soup):
        raise ValueError("course detail ownership mismatch")
    titles = soup.select("#content h2.reserve_tt")
    if len(titles) != 1 or _normalized(titles[0].get_text(" ", strip=True)) != _normalized(row["title"]):
        raise ValueError("course detail title mismatch")
    pairs = _detail_pairs(soup)
    required = {
        "강좌분류",
        "이용가능한 요일",
        "수강료",
        "모집인원",
        "교육장소",
        "교육기간",
        "교육시간",
        "강사",
        "강좌소개",
    }
    if not required.issubset(pairs) or _normalized(source.branch_contract) not in _normalized(pairs["강좌분류"]):
        raise ValueError("course detail field/branch contract changed")
    start, end = _long_range(pairs["교육기간"])
    if start != row["start_date"] or end != row["end_date"]:
        raise ValueError("course detail/list date mismatch")
    _, detail_capacity = _capacity(pairs["모집인원"])
    if detail_capacity != row["capacity_total"]:
        raise ValueError("course detail/list capacity mismatch")
    detail_time = _time_range(pairs["교육시간"])
    if row.get("schedule"):
        if row["schedule"] != detail_time:
            raise ValueError("course detail/list time mismatch")
    else:
        title_time = _title_time_range(row["title"])
        row["raw_fields"].update(
            {
                "title_schedule": title_time,
                "title_detail_schedule_mismatch": title_time != detail_time,
            }
        )
    row.update(
        {
            "fee": pairs["수강료"],
            "room": pairs["교육장소"] or source.branch,
            "venue_name": pairs["교육장소"] or source.branch,
            "category": pairs["강좌분류"],
            "schedule": detail_time,
            "schedule_raw": " ".join(part for part in (pairs["이용가능한 요일"], detail_time) if part),
            "instructor": pairs["강사"],
            "target": pairs.get("교육대상") or "공식 페이지 미기재",
            "phone": pairs.get("문의전화", ""),
            "description": pairs["강좌소개"],
        }
    )
    row["raw_fields"].update(
        {
            "detail_pairs": pairs,
            "detail_identity_verified": True,
            "target_source_omission": not _clean(pairs.get("교육대상")),
            "venue_source_omission": not _clean(pairs.get("교육장소")),
        }
    )


def _museum_total(soup: BeautifulSoup) -> int:
    return max(_pagination_numbers(soup, "fnPageSearch") or [1])


def _museum_page_contract(
    soup: BeautifulSoup,
    source: MuseumSource,
    requested_page: int,
    *,
    expected_total: Optional[int] = None,
    sentinel: bool = False,
) -> tuple[int, list[Any]]:
    if not _system_owned(soup) or _normalized(FOSSIL_BRANCH) not in _normalized(
        soup.select_one("#content").get_text(" ", strip=True) if soup.select_one("#content") else ""
    ):
        raise ValueError("museum page ownership mismatch")
    forms = soup.select("form#dssMsmRsvtVO")
    detail_forms = soup.select("form#detailVO")
    if len(forms) != 1 or len(detail_forms) != 1:
        raise ValueError("museum list form contract changed")
    hidden = _form_hidden(forms[0])
    detail_hidden = _form_hidden(detail_forms[0])
    if (
        hidden.get("menu_id") != source.menu_id
        or hidden.get("pageIndex") != str(requested_page)
        or detail_hidden.get("menu_id") != source.menu_id
        or FOSSIL_INST_ID not in str(soup)
        or FOSSIL_DETAIL_PATH not in str(soup)
    ):
        raise ValueError("museum list identity/controller changed")
    cards = soup.select("#content h3.tit a[onclick*='fnDetail']")
    if sentinel:
        if cards or not _empty_sentinel(soup):
            raise ValueError("museum sentinel contract changed")
        return expected_total or requested_page - 1, []
    total = _museum_total(soup)
    if expected_total is not None and total != expected_total:
        raise ValueError("museum page count changed during traversal")
    strong = {_clean(item.get_text(" ", strip=True)) for item in soup.select(".pagination strong")}
    if strong and strong != {str(requested_page)}:
        raise ValueError("museum current-page marker changed")
    if not cards and not _empty_sentinel(soup):
        raise ValueError("museum empty page lacks official sentinel")
    return total, cards


def _museum_row(target: Any, source: MuseumSource, anchor: Any, page: int) -> dict[str, Any]:
    title = _clean(anchor.get_text(" ", strip=True))
    match = _MUSEUM_CONTROL_RE.search(_clean(anchor.get("onclick")))
    card = anchor.find_parent("li")
    if not title or match is None or card is None:
        raise ValueError("museum programme identity/title changed")
    identity = match.group(1)
    status_nodes = card.select(".type_wrap .type01")
    if len(status_nodes) != 1:
        raise ValueError("museum programme status changed")
    source_status = _clean(status_nodes[0].get_text(" ", strip=True))
    status = _STATUS_MAP.get(source_status)
    if status is None:
        raise ValueError(f"unknown museum status: {source_status}")
    pairs: dict[str, str] = {}
    for item in card.select("ul.info li"):
        key = item.select_one(".con_l")
        value = item.select_one(".con_r")
        if key is not None and value is not None:
            pairs[_clean(key.get_text(" ", strip=True))] = _clean(value.get_text(" ", strip=True))
    script_dates = _SCRIPT_DATE_RE.findall(str(card.select_one("ul.period")) if card.select_one("ul.period") else "")
    start: Optional[date] = None
    end: Optional[date] = None
    apply_start: Optional[date] = None
    apply_end: Optional[date] = None
    if source.dated:
        if len(script_dates) != 4:
            raise ValueError("dated museum programme period changed")
        parsed = [date(int(v[:4]), int(v[4:6]), int(v[6:8])) for v in script_dates]
        apply_start, apply_end, start, end = parsed
        if apply_end < apply_start or end < start:
            raise ValueError("museum programme range is reversed")
    elif script_dates:
        raise ValueError("undated museum programme unexpectedly exposes partial dates")
    extra = _target_value(target, "extra")
    extra = extra if isinstance(extra, Mapping) else {}
    available = status == "OPEN"
    raw_url = museum_detail_url(source, identity)
    return {
        "provider": DALSEONG_PROVIDER,
        "provider_course_id": f"{DALSEONG_PROVIDER}:museum:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": FOSSIL_BRANCH,
        "branch_code": source.key,
        "preserve_branch": True,
        "raw_url": raw_url,
        "application_url": raw_url if available else "",
        "application_type": "PHONE_RESERVATION"
        if source.key == "fossil_group" and available
        else ("ONLINE_RESERVATION" if available else ""),
        "reservation_available": available,
        "status": status,
        "period": f"{start.isoformat()} ~ {end.isoformat()}" if start and end else "",
        "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}" if apply_start and apply_end else "",
        "start_date": start,
        "end_date": end,
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "schedule_raw": pairs.get("교육요일", ""),
        "target": pairs.get("이용대상", ""),
        "category": "교육·강좌",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_category": _clean(extra.get("collection_category")) or "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "municipality_code": DALSEONG_MUNICIPALITY_CODE,
        "municipality_name": DALSEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "candidate_id": DALSEONG_CANDIDATE_ID,
            "source_kind": source.key,
            "source_page": page,
            "msm_prgrm_id": identity,
            "source_status": source_status,
            "list_pairs": pairs,
            "undated_current_status": not source.dated,
        },
    }


def _museum_detail_pairs(view: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in view.select("ul.period li, ul.info li"):
        key = item.select_one(".con_l")
        value = item.select_one(".con_r")
        if key is not None and value is not None:
            label = _clean(key.get_text(" ", strip=True))
            if not label or label in pairs:
                raise ValueError("museum detail labels changed")
            pairs[label] = _clean(value.get_text(" ", strip=True))
    return pairs


def _enrich_museum_detail(row: dict[str, Any], source: MuseumSource, soup: BeautifulSoup) -> None:
    if not _system_owned(soup):
        raise ValueError("museum detail ownership mismatch")
    views = soup.select("#content .fossil_view")
    forms = soup.select("form#dssMsmRsvtVO")
    if len(views) != 1 or len(forms) != 1:
        raise ValueError("museum detail structure changed")
    view = views[0]
    hidden = _form_hidden(forms[0])
    identity = _clean(row["raw_fields"]["msm_prgrm_id"])
    if (
        hidden.get("inst_id") != FOSSIL_INST_ID
        or hidden.get("msm_prgrm_id") != identity
        or hidden.get("menu_id") != source.menu_id
    ):
        raise ValueError("museum detail identity mismatch")
    title_nodes = view.select("h3.tit")
    status_nodes = view.select(".tit_wrap .type_wrap .type01")
    if (
        len(title_nodes) != 1
        or _normalized(title_nodes[0].get_text(" ", strip=True)) != _normalized(row["title"])
        or len(status_nodes) != 1
        or _clean(status_nodes[0].get_text(" ", strip=True)) != row["raw_fields"]["source_status"]
    ):
        raise ValueError("museum detail title/status mismatch")
    pairs = _museum_detail_pairs(view)
    required = {"문의전화", "교육요일", "이용대상", "교육장소", "교육비용"}
    if not required.issubset(pairs):
        raise ValueError("museum detail fields changed")
    if source.dated:
        apply_start, apply_end = _long_range(pairs.get("접수기간", ""))
        start, end = _long_range(pairs.get("교육기간", ""))
        if (
            apply_start != row["apply_start_date"]
            or apply_end != row["apply_end_date"]
            or start != row["start_date"]
            or end != row["end_date"]
        ):
            raise ValueError("museum detail/list period mismatch")
    elif "접수기간" in pairs or "교육기간" in pairs:
        raise ValueError("undated museum detail exposes partial date contract")
    amount = _clean(hidden.get("ntsl_amt"))
    if not amount.isdigit():
        raise ValueError("museum fee identity changed")
    description_node = view.select_one(".details_cn .con_r")
    description = _clean(description_node.get_text(" ", strip=True) if description_node else "")
    if not description:
        raise ValueError("museum detail description missing")
    if source.key == "fossil_group" and "유선으로 신청" not in description:
        raise ValueError("museum group phone-application contract changed")
    row.update(
        {
            "phone": pairs["문의전화"].replace(" ", ""),
            "room": pairs["교육장소"],
            "venue_name": pairs["교육장소"],
            "fee": f"{int(amount):,}원",
            "description": description,
            "schedule_raw": pairs["교육요일"],
            "target": pairs["이용대상"],
            "category": "교육·강좌",
        }
    )
    row["raw_fields"].update({"detail_pairs": pairs, "detail_identity_verified": True})


def _sport_landing_contract(soup: BeautifulSoup, source: SportMatrixSource) -> list[tuple[str, str]]:
    if not _system_owned(soup) or _normalized(source.branch) not in _normalized(
        soup.select_one("#content").get_text(" ", strip=True) if soup.select_one("#content") else ""
    ):
        raise ValueError("sport matrix ownership mismatch")
    forms = soup.select(f"form#{source.form_id}")
    if len(forms) != 1 or _clean(forms[0].get("method")).lower() != "post":
        raise ValueError("sport matrix form contract changed")
    hidden = _form_hidden(forms[0])
    if hidden.get("searchCondition") != "DSS_0035_02" or source.result_action not in str(soup):
        raise ValueError("sport matrix controller changed")
    values: list[tuple[str, str]] = []
    for anchor in soup.select("a[onclick*='fnSearchCrs']"):
        match = _SPORT_CONTROL_RE.search(_clean(anchor.get("onclick")))
        if match:
            values.append((match.group(1), _clean(match.group(2))))
    unique = list(dict.fromkeys(values))
    if len(unique) != len(values) or not unique:
        raise ValueError("sport matrix classification identities changed")
    return unique


def _sport_selection_empty(soup: BeautifulSoup, source: SportMatrixSource) -> None:
    if not _system_owned(soup) or _normalized(source.branch) not in _normalized(
        soup.select_one("#content").get_text(" ", strip=True) if soup.select_one("#content") else ""
    ):
        raise ValueError("sport selection ownership mismatch")
    controls = soup.select(
        "#content a[onclick*='fnSearchCrsDeatail'], "
        "#content a[onclick*='fnRegistCrsAply'], "
        "#content a[onclick*='fnCrsInfo']"
    )
    active_tables = []
    for table in soup.select("#content table"):
        headers = {_clean(item.get_text(" ", strip=True)) for item in table.select("thead th")}
        if {"프로그램명", "온라인등록"}.issubset(headers):
            active_tables.append(table)
    if controls or active_tables:
        raise ValueError("sport selection now exposes unparsed active courses")


def _sport_post_data(classification: tuple[str, str]) -> dict[str, str]:
    identity, name = classification
    return {
        "searchCondition": "DSS_0035_02",
        "searchCondition2": "",
        "bfr_searchCondition": "DSS_0035_02",
        "prgrm_nm": name,
        "onln_rsvt_clsf_id": identity,
        "gds_id": "",
        "crs_id": "",
        "ntsl_no": "",
        "searchKeyword": "",
    }


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _normalized(row.get("branch")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("room")),
        _normalized(row.get("instructor")),
        _normalized(row.get("fee")),
        _clean(row.get("capacity_total")),
    )


def _dedupe_semantic(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_semantic_key(row), []).append(row)

    result: list[dict[str, Any]] = []
    duplicate_count = 0
    for values in grouped.values():
        winner = max(values, key=lambda item: _clean(item.get("provider_course_id")))
        losers = sorted(_clean(item.get("provider_course_id")) for item in values if item is not winner)
        if losers:
            duplicate_count += len(losers)
            raw_fields = winner.setdefault("raw_fields", {})
            raw_fields["semantic_duplicate_provider_course_ids"] = losers
        result.append(winner)
    return result, duplicate_count


def _failure_meta(message: str, *, source_cap_reached: bool = False) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "detail_required_count": 0,
        "source_total": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": source_cap_reached,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_dalseong_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 120,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    requester: Optional[Requester] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = DALSEONG_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Dalseong education snapshot."""

    if not is_dalseong_target(target):
        return (
            [],
            DALSEONG_PARSER,
            _failure_meta("target does not match the exact Dalseong integrated-reservation candidate"),
        )
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    cutoff = _today(today)
    current_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    request_lock = threading.Lock()
    local = threading.local()
    physical_requests = 0

    def thread_session() -> Any:
        value = getattr(local, "session", None)
        count = int(getattr(local, "request_count", 0))
        if value is None or count >= DALSEONG_SESSION_REQUEST_LIMIT:
            if value is not None:
                _close_quietly(value)
            value = current_factory()
            local.session = value
            local.request_count = 0
            with sessions_lock:
                sessions.append(value)
        local.request_count = int(getattr(local, "request_count", 0)) + 1
        return value

    def request_soup(
        method: str,
        url: str,
        data: Optional[Mapping[str, str]] = None,
    ) -> BeautifulSoup:
        nonlocal physical_requests
        session = thread_session()
        with request_lock:
            physical_requests += 1
        if requester is not None:
            value = requester(session, method, url, timeout, data)
        elif method == "GET" and fetcher is not None:
            value = fetcher(session, url, timeout)
        elif method == "GET":
            value = session.get(url, timeout=timeout, allow_redirects=False)
        else:
            value = session.post(
                url,
                data=dict(data or {}),
                timeout=timeout,
                allow_redirects=False,
                headers={"Referer": DALSEONG_URL},
            )
        return _coerce_soup(value)

    errors: list[str] = []
    source_cap_reached = False
    list_requests = 0
    sentinel_requests = 0
    recheck_requests = 0
    course_pages: dict[tuple[str, int], list[dict[str, Any]]] = {}
    course_totals: dict[str, int] = {}
    museum_pages: dict[tuple[str, int], list[dict[str, Any]]] = {}
    museum_totals: dict[str, int] = {}
    sport_classifications: dict[str, list[tuple[str, str]]] = {}

    try:
        # The canonical target is also the Dalseong Culture Center page one.
        first_soups: dict[str, BeautifulSoup] = {}
        try:
            first_soups["dalseong_culture"] = request_soup("GET", DALSEONG_URL)
            list_requests += 1
        except Exception as exc:
            errors.append(f"canonical target: fetch {type(exc).__name__}")

        def fetch_course_one(source: CourseSource) -> tuple[str, Optional[BeautifulSoup], str]:
            if source.key == "dalseong_culture":
                return source.key, first_soups.get(source.key), ""
            try:
                return source.key, request_soup("GET", source.url), ""
            except Exception as exc:
                return source.key, None, f"fetch {type(exc).__name__}"

        workers = min(max(1, int(max_workers)), DALSEONG_MAX_WORKERS, len(COURSE_SOURCES))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dalseong-course-one") as pool:
            course_ones = list(pool.map(fetch_course_one, COURSE_SOURCES))
        list_requests += sum(1 for key, _, _ in course_ones if key != "dalseong_culture")
        for key, soup, fetch_error in course_ones:
            source = _COURSE_BY_KEY[key]
            if fetch_error or soup is None:
                errors.append(f"{source.branch} page 1: {fetch_error or 'empty response'}")
                continue
            try:
                total, trs = _course_page_contract(soup, source, 1)
                course_totals[key] = total
                course_pages[(key, 1)] = [_course_row(target, source, tr, 1) for tr in trs]
            except Exception as exc:
                errors.append(f"{source.branch} page 1: {type(exc).__name__}: {_clean(exc)}")

        def fetch_museum_one(source: MuseumSource) -> tuple[str, Optional[BeautifulSoup], str]:
            try:
                return source.key, request_soup("GET", museum_list_url(source, 1)), ""
            except Exception as exc:
                return source.key, None, f"fetch {type(exc).__name__}"

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="dalseong-museum-one") as pool:
            museum_ones = list(pool.map(fetch_museum_one, MUSEUM_SOURCES))
        list_requests += len(museum_ones)
        for key, soup, fetch_error in museum_ones:
            source = _MUSEUM_BY_KEY[key]
            if fetch_error or soup is None:
                errors.append(f"{source.label} page 1: {fetch_error or 'empty response'}")
                continue
            try:
                total, anchors = _museum_page_contract(soup, source, 1)
                museum_totals[key] = total
                museum_pages[(key, 1)] = [_museum_row(target, source, anchor, 1) for anchor in anchors]
            except Exception as exc:
                errors.append(f"{source.label} page 1: {type(exc).__name__}: {_clean(exc)}")

        def fetch_sport_landing(source: SportMatrixSource) -> tuple[str, Optional[BeautifulSoup], str]:
            try:
                return source.key, request_soup("GET", source.url), ""
            except Exception as exc:
                return source.key, None, f"fetch {type(exc).__name__}"

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="dalseong-sport-one") as pool:
            sport_ones = list(pool.map(fetch_sport_landing, SPORT_MATRIX_SOURCES))
        list_requests += len(sport_ones)
        for key, soup, fetch_error in sport_ones:
            source = next(item for item in SPORT_MATRIX_SOURCES if item.key == key)
            if fetch_error or soup is None:
                errors.append(f"{source.branch} matrix: {fetch_error or 'empty response'}")
                continue
            try:
                sport_classifications[key] = _sport_landing_contract(soup, source)
            except Exception as exc:
                errors.append(f"{source.branch} matrix: {type(exc).__name__}: {_clean(exc)}")

        if not errors:
            required_page_budget = (
                sum(total + 2 for total in course_totals.values())
                + sum(total + 2 for total in museum_totals.values())
                + len(SPORT_MATRIX_SOURCES) * 2
                + sum(len(items) for items in sport_classifications.values())
            )
            if allowed_pages < required_page_budget:
                source_cap_reached = True
                errors.append(
                    f"max_pages cap {allowed_pages} is below {required_page_budget} required list/sentinel/recheck requests"
                )

        if not errors:
            course_tasks = [
                (source, page, page == course_totals[source.key] + 1)
                for source in COURSE_SOURCES
                for page in range(2, course_totals[source.key] + 2)
            ]

            def fetch_course_task(
                task: tuple[CourseSource, int, bool],
            ) -> tuple[CourseSource, int, bool, Optional[BeautifulSoup], str]:
                source, page, sentinel = task
                try:
                    return (
                        source,
                        page,
                        sentinel,
                        request_soup("POST", course_list_url(source), course_post_data(source, page)),
                        "",
                    )
                except Exception as exc:
                    return source, page, sentinel, None, f"fetch {type(exc).__name__}"

            workers = min(max(1, int(max_workers)), DALSEONG_MAX_WORKERS, max(1, len(course_tasks)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dalseong-course-list") as pool:
                course_results = list(pool.map(fetch_course_task, course_tasks))
            for source, page, sentinel, soup, fetch_error in course_results:
                if sentinel:
                    sentinel_requests += 1
                else:
                    list_requests += 1
                if fetch_error or soup is None:
                    errors.append(f"{source.branch} page {page}: {fetch_error or 'empty response'}")
                    continue
                try:
                    _, trs = _course_page_contract(
                        soup,
                        source,
                        page,
                        expected_total=course_totals[source.key],
                        sentinel=sentinel,
                    )
                    if not sentinel:
                        course_pages[(source.key, page)] = [_course_row(target, source, tr, page) for tr in trs]
                except Exception as exc:
                    errors.append(f"{source.branch} page {page}: {type(exc).__name__}: {_clean(exc)}")

            museum_tasks = [
                (source, page, page == museum_totals[source.key] + 1)
                for source in MUSEUM_SOURCES
                for page in range(2, museum_totals[source.key] + 2)
            ]

            def fetch_museum_task(
                task: tuple[MuseumSource, int, bool],
            ) -> tuple[MuseumSource, int, bool, Optional[BeautifulSoup], str]:
                source, page, sentinel = task
                try:
                    return source, page, sentinel, request_soup("GET", museum_list_url(source, page)), ""
                except Exception as exc:
                    return source, page, sentinel, None, f"fetch {type(exc).__name__}"

            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="dalseong-museum-list") as pool:
                museum_results = list(pool.map(fetch_museum_task, museum_tasks))
            for source, page, sentinel, soup, fetch_error in museum_results:
                if sentinel:
                    sentinel_requests += 1
                else:
                    list_requests += 1
                if fetch_error or soup is None:
                    errors.append(f"{source.label} page {page}: {fetch_error or 'empty response'}")
                    continue
                try:
                    _, anchors = _museum_page_contract(
                        soup,
                        source,
                        page,
                        expected_total=museum_totals[source.key],
                        sentinel=sentinel,
                    )
                    if not sentinel:
                        museum_pages[(source.key, page)] = [
                            _museum_row(target, source, anchor, page) for anchor in anchors
                        ]
                except Exception as exc:
                    errors.append(f"{source.label} page {page}: {type(exc).__name__}: {_clean(exc)}")

            sport_tasks = [
                (source, classification)
                for source in SPORT_MATRIX_SOURCES
                for classification in sport_classifications[source.key]
            ]

            def fetch_sport_selection(
                task: tuple[SportMatrixSource, tuple[str, str]],
            ) -> tuple[SportMatrixSource, tuple[str, str], Optional[BeautifulSoup], str]:
                source, classification = task
                try:
                    return (
                        source,
                        classification,
                        request_soup(
                            "POST",
                            f"https://{DALSEONG_HOST}{source.result_action}",
                            _sport_post_data(classification),
                        ),
                        "",
                    )
                except Exception as exc:
                    return source, classification, None, f"fetch {type(exc).__name__}"

            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dalseong-sport-select") as pool:
                sport_results = list(pool.map(fetch_sport_selection, sport_tasks))
            list_requests += len(sport_results)
            for source, classification, soup, fetch_error in sport_results:
                if fetch_error or soup is None:
                    errors.append(f"{source.branch} selection {classification[0]}: {fetch_error or 'empty response'}")
                    continue
                try:
                    _sport_selection_empty(soup, source)
                except Exception as exc:
                    errors.append(f"{source.branch} selection {classification[0]}: {type(exc).__name__}: {_clean(exc)}")

        # Every page-one identity is re-read after the complete traversal.
        if not errors:

            def recheck_course(source: CourseSource) -> tuple[CourseSource, Optional[BeautifulSoup], str]:
                try:
                    return source, request_soup("POST", course_list_url(source), course_post_data(source, 1)), ""
                except Exception as exc:
                    return source, None, f"fetch {type(exc).__name__}"

            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="dalseong-course-recheck") as pool:
                course_rechecks = list(pool.map(recheck_course, COURSE_SOURCES))
            recheck_requests += len(course_rechecks)
            for source, soup, fetch_error in course_rechecks:
                if fetch_error or soup is None:
                    errors.append(f"{source.branch} page 1 recheck: {fetch_error}")
                    continue
                try:
                    _, trs = _course_page_contract(soup, source, 1, expected_total=course_totals[source.key])
                    checked = [_course_row(target, source, tr, 1) for tr in trs]
                    before = [row["provider_course_id"] for row in course_pages[(source.key, 1)]]
                    after = [row["provider_course_id"] for row in checked]
                    if before != after:
                        raise ValueError("page one changed during traversal")
                except Exception as exc:
                    errors.append(f"{source.branch} page 1 recheck: {type(exc).__name__}: {_clean(exc)}")

            def recheck_museum(source: MuseumSource) -> tuple[MuseumSource, Optional[BeautifulSoup], str]:
                try:
                    return source, request_soup("GET", museum_list_url(source, 1)), ""
                except Exception as exc:
                    return source, None, f"fetch {type(exc).__name__}"

            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="dalseong-museum-recheck") as pool:
                museum_rechecks = list(pool.map(recheck_museum, MUSEUM_SOURCES))
            recheck_requests += len(museum_rechecks)
            for source, soup, fetch_error in museum_rechecks:
                if fetch_error or soup is None:
                    errors.append(f"{source.label} page 1 recheck: {fetch_error}")
                    continue
                try:
                    _, anchors = _museum_page_contract(soup, source, 1, expected_total=museum_totals[source.key])
                    checked = [_museum_row(target, source, anchor, 1) for anchor in anchors]
                    before = [row["provider_course_id"] for row in museum_pages[(source.key, 1)]]
                    after = [row["provider_course_id"] for row in checked]
                    if before != after:
                        raise ValueError("page one changed during traversal")
                except Exception as exc:
                    errors.append(f"{source.label} page 1 recheck: {type(exc).__name__}: {_clean(exc)}")

            def recheck_sport(source: SportMatrixSource) -> tuple[SportMatrixSource, Optional[BeautifulSoup], str]:
                try:
                    return source, request_soup("GET", source.url), ""
                except Exception as exc:
                    return source, None, f"fetch {type(exc).__name__}"

            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="dalseong-sport-recheck") as pool:
                sport_rechecks = list(pool.map(recheck_sport, SPORT_MATRIX_SOURCES))
            recheck_requests += len(sport_rechecks)
            for source, soup, fetch_error in sport_rechecks:
                if fetch_error or soup is None:
                    errors.append(f"{source.branch} matrix recheck: {fetch_error}")
                    continue
                try:
                    checked = _sport_landing_contract(soup, source)
                    if checked != sport_classifications[source.key]:
                        raise ValueError("classification identities changed during traversal")
                except Exception as exc:
                    errors.append(f"{source.branch} matrix recheck: {type(exc).__name__}: {_clean(exc)}")

        all_course_rows = [
            row
            for source in COURSE_SOURCES
            for page in range(1, course_totals.get(source.key, 0) + 1)
            for row in course_pages.get((source.key, page), [])
        ]
        all_museum_rows = [
            row
            for source in MUSEUM_SOURCES
            for page in range(1, museum_totals.get(source.key, 0) + 1)
            for row in museum_pages.get((source.key, page), [])
        ]
        all_rows = all_course_rows + all_museum_rows
        identities = [_clean(row.get("provider_course_id")) for row in all_rows]
        duplicate_identity_count = len(identities) - len(set(identities))
        if not identities or any(not identity for identity in identities):
            errors.append("one or more Dalseong source rows lacks a stable identity")
        if duplicate_identity_count:
            errors.append(f"duplicate stable identities across Dalseong sources: {duplicate_identity_count}")

        current_rows: list[dict[str, Any]] = []
        expired_count = 0
        excluded_addon_count = 0
        for row in all_rows:
            if row.get("raw_fields", {}).get("excluded_non_course_addon"):
                excluded_addon_count += 1
                continue
            end = row.get("end_date")
            if end is None:
                if row.get("raw_fields", {}).get("undated_current_status") and row.get("status") in {
                    "OPEN",
                    "SCHEDULED",
                }:
                    current_rows.append(row)
                else:
                    errors.append(
                        f"undated row lacks explicit current-status contract: {row.get('provider_course_id')}"
                    )
            elif isinstance(end, date) and end >= cutoff:
                current_rows.append(row)
            else:
                expired_count += 1

        detail_required_count = len(current_rows)
        if allowed_details < detail_required_count:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap {allowed_details} is below {detail_required_count} required current details"
            )

        detail_pages = 0
        detail_errors: list[str] = []
        detailed_rows: list[dict[str, Any]] = []
        if not errors and current_rows:

            def fetch_detail(row: dict[str, Any]) -> tuple[dict[str, Any], bool, str]:
                raw = row.get("raw_fields", {})
                source_kind = _clean(raw.get("source_kind"))
                try:
                    if source_kind in _COURSE_BY_KEY:
                        source = _COURSE_BY_KEY[source_kind]
                        soup = request_soup(
                            "POST",
                            course_detail_request_url(source),
                            course_post_data(
                                source,
                                int(raw.get("source_page") or 1),
                                gds_id=_clean(raw.get("gds_id")),
                                crs_id=_clean(raw.get("crs_id")),
                            ),
                        )
                        _enrich_course_detail(row, source, soup)
                    elif source_kind in _MUSEUM_BY_KEY:
                        source = _MUSEUM_BY_KEY[source_kind]
                        soup = request_soup("GET", _clean(row.get("raw_url")))
                        _enrich_museum_detail(row, source, soup)
                    else:
                        raise ValueError("unknown detail source")
                    return row, True, ""
                except Exception as exc:
                    return row, False, f"{row.get('provider_course_id')}: {type(exc).__name__}: {_clean(exc)}"

            workers = min(max(1, int(max_workers)), DALSEONG_MAX_WORKERS, detail_required_count)
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dalseong-detail") as pool:
                detail_results = list(pool.map(fetch_detail, current_rows))
            for row, success, error in detail_results:
                detail_pages += int(success)
                if success:
                    detailed_rows.append(row)
                else:
                    detail_errors.append(error)
        errors.extend(detail_errors)
        if len(detailed_rows) != detail_required_count:
            errors.append(f"detail current count {len(detailed_rows)} != required {detail_required_count}")

        cleaned, semantic_duplicate_count = _dedupe_semantic(detailed_rows)
        if not errors:
            try:
                deduped = list(current_dedupe(cleaned))
            except Exception as exc:
                errors.append(f"dedupe failed {type(exc).__name__}")
                deduped = []
            if len(deduped) != len(cleaned):
                errors.append(f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}")
            cleaned = deduped

        expected_course_pages = sum(course_totals.values())
        expected_museum_pages = sum(museum_totals.values())
        pagination_complete = (
            not errors
            and len(course_pages) == expected_course_pages
            and len(museum_pages) == expected_museum_pages
            and sentinel_requests == len(COURSE_SOURCES) + len(MUSEUM_SOURCES)
            and recheck_requests == len(COURSE_SOURCES) + len(MUSEUM_SOURCES) + len(SPORT_MATRIX_SOURCES)
        )
        details_complete = (
            not detail_errors
            and not source_cap_reached
            and detail_pages == detail_required_count
            and len(detailed_rows) == detail_required_count
        )
        snapshot_complete = pagination_complete and details_complete and not errors
        if not snapshot_complete:
            cleaned = []

        branch_counts = Counter(_clean(row.get("branch")) for row in detailed_rows)
        source_counts = Counter(_clean(row.get("raw_fields", {}).get("source_kind")) for row in all_rows)
        current_source_counts = Counter(_clean(row.get("raw_fields", {}).get("source_kind")) for row in detailed_rows)
        status_counts = Counter(_clean(row.get("raw_fields", {}).get("source_status")) for row in all_rows)
        pagination_requests = max(0, physical_requests - detail_required_count)
        meta = {
            "pages": pagination_requests,
            "request_count": physical_requests,
            "list_pages": expected_course_pages + expected_museum_pages,
            "list_requests": list_requests,
            "sentinel_requests": sentinel_requests,
            "recheck_requests": recheck_requests,
            "detail_pages": detail_pages,
            "detail_required_count": detail_required_count,
            "source_total": len(all_rows),
            "course_source_total": len(all_course_rows),
            "museum_source_total": len(all_museum_rows),
            "current_count": len(detailed_rows),
            "returned_count": len(cleaned),
            "expired_count": expired_count,
            "excluded_non_course_addon_count": excluded_addon_count,
            "undated_current_count": sum(row.get("end_date") is None for row in detailed_rows),
            "duplicate_identity_count": duplicate_identity_count,
            "semantic_duplicate_count": semantic_duplicate_count,
            "title_detail_schedule_mismatch_count": sum(
                bool(row.get("raw_fields", {}).get("title_detail_schedule_mismatch")) for row in detailed_rows
            ),
            "course_page_counts": dict(sorted(course_totals.items())),
            "museum_page_counts": dict(sorted(museum_totals.items())),
            "source_counts": dict(sorted(source_counts.items())),
            "current_source_counts": dict(sorted(current_source_counts.items())),
            "branch_counts": dict(sorted(branch_counts.items())),
            "status_counts": dict(sorted(status_counts.items())),
            "sport_matrix_classification_counts": {
                key: len(values) for key, values in sorted(sport_classifications.items())
            },
            "sport_matrix_active_count": 0,
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": snapshot_complete and not cleaned,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
        return cleaned, DALSEONG_PARSER, meta
    finally:
        for session in sessions:
            _close_quietly(session)


collect = collect_dalseong_education_courses
