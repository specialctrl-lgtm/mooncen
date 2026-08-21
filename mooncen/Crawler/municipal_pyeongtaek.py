"""Complete, fail-closed collectors for Pyeongtaek education catalogues.

The city operates two distinct lifelong-learning catalogues under one owner:
the term-based ``regular/program`` catalogue (split into north, south, and
west learning spaces) and the continuously offered ``eduProgram`` catalogue.
The Pyeongtaek municipal libraries (PTLIB) and the Gyeonggi Office of
Education Pyeongtaek Library are separate institutions and therefore remain
separate providers.

Each collector walks every numbered page plus an immediate empty sentinel,
validates the table schema and source identities, and fetches every detail
page for a current or future course.  Any cap, fetch, schema, identity, or
detail failure suppresses the complete snapshot rather than publishing a
partial one.  This module intentionally is not wired into the shared router;
the router must inject its managed session factory when promotion is approved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


PYEONGTAEK_MUNICIPALITY_CODE = "4122000000"
PYEONGTAEK_MUNICIPALITY_NAME = "경기도 평택시"

PYEONGTAEK_LIFELONG_PROVIDER = "MUNI_WWW_PYEONGTAEK_GO_KR_54DAD706"
PYEONGTAEK_LIFELONG_CANDIDATE_ID = "MUNI_IR_32B64F132447"
PYEONGTAEK_LIFELONG_INSTRUCTION_URL = (
    "https://www.pyeongtaek.go.kr/learning/contents.do?mid=0201010000"
)
PYEONGTAEK_REGULAR_URL = (
    "https://www.pyeongtaek.go.kr/learning/regular/program/list.do?"
    "mid=0201020000"
)
PYEONGTAEK_REGULAR_DETAIL_URL = (
    "https://www.pyeongtaek.go.kr/learning/regular/program/view.do?"
    "mid=0201020000"
)
PYEONGTAEK_ONGOING_URL = (
    "https://www.pyeongtaek.go.kr/learning/eduProgram/list.do?"
    "mid=0202010000"
)
PYEONGTAEK_ONGOING_DETAIL_URL = (
    "https://www.pyeongtaek.go.kr/learning/eduProgram/view.do?"
    "mid=0202010000"
)
PYEONGTAEK_LIFELONG_PARSER = (
    "pyeongtaek_regular_three_branches+ongoing_complete_catalogue+"
    "empty_sentinels+current_details+official_regular_capacity_omission+"
    "shared_learning_space_locations"
)

PTLIB_PROVIDER = "MUNI_WWW_PTLIB_GO_KR_D9537B1F"
PTLIB_CANDIDATE_ID = "MUNI_IR_171B1AE0E156"
PTLIB_URL = (
    "https://www.ptlib.go.kr/intro/menu/10025/program/30025/"
    "lectureList.do"
)
PTLIB_DETAIL_URL = (
    "https://www.ptlib.go.kr/intro/menu/10025/program/30025/"
    "lectureDetail.do"
)
PTLIB_PARSER = "ptlib_complete_catalogue+empty_sentinel+current_details"

PYEONGTAEK_GOE_PROVIDER = "MUNI_LIB_GOE_GO_KR_9D32284E"
PYEONGTAEK_GOE_CANDIDATE_ID = "MUNI_IR_51A415005905"
PYEONGTAEK_GOE_ROOT_URL = "https://lib.goe.go.kr/pt/index.do"
PYEONGTAEK_GOE_BRANCH = "경기도교육청평택도서관"
PYEONGTAEK_GOE_ADDRESS = "경기도 평택시 서정북로125번길 103"
PYEONGTAEK_GOE_PARSER = (
    "goe_pyeongtaek_three_teach_catalogues+empty_sentinels+current_details"
)

PYEONGTAEK_REGULAR_PAGE_SIZE = 50
PYEONGTAEK_ONGOING_PAGE_SIZE = 20
PTLIB_PAGE_SIZE = 50
GOE_PAGE_SIZE = 10
SESSION_REQUEST_LIMIT = 100


@dataclass(frozen=True)
class RegularBranch:
    code: str
    key: str
    name: str
    address: str


REGULAR_BRANCHES: tuple[RegularBranch, ...] = (
    RegularBranch(
        "1", "북부", "북부학습공간",
        "경기도 평택시 이충로 84-6, 평생학습센터 3층",
    ),
    RegularBranch(
        "2", "남부", "남부학습공간",
        "경기도 평택시 평택5로 220, 남부복지타운 2층",
    ),
    RegularBranch(
        "3", "서부", "서부학습공간",
        "경기도 평택시 안중읍 서동대로 1557, 서부복지타운 3층",
    ),
)


@dataclass(frozen=True)
class GoeSource:
    key: str
    menu_idx: str
    large_code: str
    category: str


GOE_SOURCES: tuple[GoeSource, ...] = (
    GoeSource("lifelong", "31", "50", "평생교육강좌"),
    GoeSource("reading", "36", "55", "독서문화행사"),
    GoeSource("parent", "120", "65", "학부모독서아카데미"),
)


PTLIB_BRANCHES: Mapping[str, tuple[str, str]] = {
    "배다리": ("MJ", "배다리도서관"),
    "비전": ("MA", "비전도서관"),
    "팽성": ("MB", "팽성도서관"),
    "안중": ("MC", "안중도서관"),
    "초록": ("MD", "지산초록도서관"),
    "지산초록": ("MD", "지산초록도서관"),
    "장당": ("MF", "장당도서관"),
    "오성": ("ME", "오성도서관"),
    "진위": ("MG", "진위도서관"),
    "세교": ("MH", "세교도서관"),
    "청북": ("BE", "청북도서관"),
    "매봉": ("BA", "매봉작은도서관"),
    "송탄": ("BB", "송탄작은도서관"),
    "서정": ("BC", "서정작은도서관"),
    "포승": ("BD", "포승작은도서관"),
    "한국근현대음악관": ("MK", "한국근현대음악관"),
}


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_REGULAR_TERM_RE = re.compile(r"(20\d{2})년\s*제?\s*(\d+)기")
_REGULAR_ID_RE = re.compile(r"goView\('([^']+)'\)")
_ONGOING_ID_RE = re.compile(r"(?:[?&]eIdx=|goView\(')(\d+)")
_PTLIB_ID_RE = re.compile(r"fnDetail\('([^']+)'\)")
_PAGE_RE = re.compile(r"(?:goPage|fnList)\((\d+)\)")

_REGULAR_HEADERS = (
    "번호", "기수(학습공간)", "강좌명(접수기간)", "과목유형",
    "교육시간", "수강료", "신청현황(신청/정원)",
)
_ONGOING_HEADERS = (
    "번호", "사업명", "강좌명", "장소", "교육기간", "시간/요일",
    "확정인원 /신청인원 /정원", "신청방식",
)
_PTLIB_HEADERS = (
    "기관명", "프로그램명", "강좌기간", "대상", "온라인 (신청/정원)", "상태",
)
_GOE_HEADERS = ("강좌명", "접수인원", "강좌기간", "접수기간", "접수상태")

_REGULAR_DETAIL_REQUIRED = {
    "강좌명", "학습공간", "기수", "교육장소", "과목유형", "교육대상",
    "교육일정", "교육시간/요일", "수강료", "상태",
}
_ONGOING_DETAIL_REQUIRED = {
    "강좌명", "교육기간", "접수기간", "강의시간", "대상", "정원",
    "신청인원", "확정인원", "기관", "장소", "수강료", "접수방법",
}
_PTLIB_DETAIL_REQUIRED = {
    "문화행사명", "기관명", "장소", "모집기간", "교육기간", "시간",
    "대상",
}
_GOE_DETAIL_REQUIRED = {
    "강의 분류", "강의 설명", "강의장소", "강사명", "강의대상", "접수기간",
    "강의기간", "강의시간", "강의요일", "모집방식", "현재 참여 / 모집",
    "현재 대기자 / 대기자",
}

_EMPTY_TOKENS = ("검색결과가 없습니다", "검색 결과가 없습니다", "등록된 프로그램이 없습니다")


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return _SPACE_RE.sub(" ", str(value).replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True)) if node is not None else ""


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value)[:10])


def _date_range(value: Any) -> tuple[Optional[date], Optional[date]]:
    found = [date(int(y), int(m), int(d)) for y, m, d in _DATE_RE.findall(_clean(value))]
    if not found:
        return None, None
    return found[0], found[-1]


def _normal_period(value: Any) -> str:
    text = _clean(value)
    return _DATE_RE.sub(
        lambda match: (
            f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-"
            f"{int(match.group(3)):02d}"
        ),
        text,
    )


def _is_current(value: Any, cutoff: date) -> bool:
    _, end = _date_range(value)
    if end is None:
        raise ValueError(f"course period has no parseable date: {_clean(value)!r}")
    return end >= cutoff


def _headers(table: Any) -> tuple[str, ...]:
    return tuple(_text(node) for node in table.select("thead th"))


def _is_empty(soup: BeautifulSoup) -> bool:
    text = _text(soup)
    return any(token in text for token in _EMPTY_TOKENS)


def _pairs(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for tr in soup.select("table tr"):
        children = tr.find_all(["th", "td"], recursive=False)
        for index, node in enumerate(children):
            if node.name != "th":
                continue
            value_node = next(
                (candidate for candidate in children[index + 1 :] if candidate.name == "td"),
                None,
            )
            if value_node is None:
                continue
            key = _text(node).replace("(*)", "").strip()
            value = _text(value_node)
            if key and (value or key not in result):
                result[key] = value
    return result


def _status(value: Any) -> str:
    text = _clean(value)
    if any(token in text for token in ("대기자접수", "대기자신청")):
        return "WAITING"
    if any(token in text for token in ("접수예정", "신청예정", "신청대기")):
        return "SCHEDULED"
    if any(token in text for token in ("접수중", "신청중", "수강신청", "추가접수")):
        return "OPEN"
    if any(token in text for token in ("마감", "종료", "교육중", "신청완료")):
        return "CLOSED"
    return "UNKNOWN"


def _application_url(raw_url: str, status: str) -> str:
    return raw_url if status in {"OPEN", "WAITING", "SCHEDULED"} else ""


def _provider(target: Any) -> str:
    return _clean(getattr(target, "provider", ""))


def _target_url(target: Any) -> str:
    return _clean(getattr(target, "url", ""))


def _target_branch(target: Any) -> str:
    return _clean(getattr(target, "branch", "")) or PYEONGTAEK_MUNICIPALITY_NAME


def _strict_url(value: Any, host: str, paths: set[str]) -> bool:
    parsed = urlparse(_clean(value))
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == host
        and parsed.port is None
        and parsed.path in paths
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_pyeongtaek_lifelong_target(target: Any) -> bool:
    return _provider(target) == PYEONGTAEK_LIFELONG_PROVIDER and _strict_url(
        _target_url(target),
        "www.pyeongtaek.go.kr",
        {
            "/learning/contents.do",
            "/learning/regular/program/list.do",
            "/learning/eduProgram/list.do",
        },
    )


def is_ptlib_target(target: Any) -> bool:
    return _provider(target) == PTLIB_PROVIDER and _strict_url(
        _target_url(target),
        "www.ptlib.go.kr",
        {"/intro/menu/10025/program/30025/lectureList.do"},
    )


def is_pyeongtaek_goe_target(target: Any) -> bool:
    return _provider(target) == PYEONGTAEK_GOE_PROVIDER and _strict_url(
        _target_url(target),
        "lib.goe.go.kr",
        {"/pt/index.do", "/pt/module/teach/index.do"},
    )


def is_target(target: Any) -> bool:
    return (
        is_pyeongtaek_lifelong_target(target)
        or is_ptlib_target(target)
        or is_pyeongtaek_goe_target(target)
    )


def regular_list_url(branch_code: str, page: int) -> str:
    return PYEONGTAEK_REGULAR_URL + "&" + urlencode(
        {
            "searchEduPlace": branch_code,
            "pageUnit": str(PYEONGTAEK_REGULAR_PAGE_SIZE),
            "page": str(page),
        }
    )


def regular_detail_url(identity: str) -> str:
    return PYEONGTAEK_REGULAR_DETAIL_URL + "&" + urlencode({"idx": identity})


def ongoing_list_url(page: int) -> str:
    return PYEONGTAEK_ONGOING_URL + "&" + urlencode({"category": "", "page": str(page)})


def ongoing_detail_url(identity: str) -> str:
    return PYEONGTAEK_ONGOING_DETAIL_URL + "&" + urlencode({"eIdx": identity})


def ptlib_list_url(page: int) -> str:
    return PTLIB_URL + "?" + urlencode(
        {
            "manageCd": "ALL",
            "currentPageNo": str(page),
            "recordCountPerPage": str(PTLIB_PAGE_SIZE),
        }
    )


def ptlib_detail_url(identity: str) -> str:
    return PTLIB_DETAIL_URL + "?" + urlencode({"lectureIdx": identity})


def goe_list_url(source: GoeSource, page: int) -> str:
    return "https://lib.goe.go.kr/pt/module/teach/index.do?" + urlencode(
        {
            "menu_idx": source.menu_idx,
            "search_large_code": source.large_code,
            "viewPage": str(page),
        }
    )


def goe_detail_url(source: GoeSource, item: Mapping[str, str]) -> str:
    return "https://lib.goe.go.kr/pt/module/teach/detail.do?" + urlencode(
        {
            "group_idx": item["group_idx"],
            "category_idx": item["category_idx"],
            "teach_idx": item["identity"],
            "viewPage": "1",
            "large_code": source.large_code,
            "search_group_idx": "",
            "menu_idx": source.menu_idx,
            "search_large_code": source.large_code,
            "search_mid_code": "",
            "search_type": "teach_name",
            "search_text": "",
            "search_status": "",
        }
    )


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


def _response_soup(response: Any) -> BeautifulSoup:
    if int(getattr(response, "status_code", 0)) != 200:
        raise ValueError(f"unexpected HTTP status {getattr(response, 'status_code', 0)}")
    if getattr(response, "history", None):
        raise ValueError("redirected HTML is not accepted")
    body = getattr(response, "content", None)
    if body is None:
        body = getattr(response, "text", None)
    if not body:
        raise ValueError("empty HTML response")
    return BeautifulSoup(body, "lxml")


class _Client:
    def __init__(
        self,
        session_factory: SessionFactory,
        fetcher: Optional[Fetcher],
        timeout: int,
    ) -> None:
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.timeout = timeout
        self.current: Any = None
        self.session_requests = 0
        self.sessions_created = 0
        self.physical_requests = 0

    def _rotate(self) -> None:
        close = getattr(self.current, "close", None)
        if callable(close):
            close()
        self.current = self.session_factory()
        self.session_requests = 0
        self.sessions_created += 1
        headers = getattr(self.current, "headers", None)
        if hasattr(headers, "update"):
            headers.update({"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})

    def get(self, url: str) -> BeautifulSoup:
        if self.current is None or self.session_requests >= SESSION_REQUEST_LIMIT:
            self._rotate()
        self.session_requests += 1
        self.physical_requests += 1
        if self.fetcher is not None:
            value = self.fetcher(self.current, url, self.timeout)
            if not hasattr(value, "select"):
                raise ValueError("managed fetcher did not return parsed HTML")
            return value
        response = self.current.get(url, timeout=self.timeout, allow_redirects=False)
        return _response_soup(response)

    def close(self) -> None:
        close = getattr(self.current, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _failure(message: str, source_count: int) -> dict[str, Any]:
    return {
        "pages": 0,
        "data_pages": 0,
        "sentinel_pages": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_count": source_count,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _finish_rows(
    rows: list[dict[str, Any]], dedupe_rows: Optional[DedupeRows]
) -> list[dict[str, Any]]:
    result = list((dedupe_rows or _dedupe)(rows))
    if len(result) != len(rows):
        raise ValueError("semantic dedupe changed a source-complete snapshot")
    return result


def _base_row(
    target: Any,
    namespace: str,
    identity: str,
    title: str,
    raw_url: str,
) -> dict[str, Any]:
    provider = _provider(target)
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:{namespace}:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": _clean(title),
        "raw_url": raw_url,
        "application_url": raw_url,
        "collection_type": "static_html",
        "program_type": "강좌",
        "domain_category": "교육",
    }


def _regular_global_contract(soup: BeautifulSoup) -> tuple[str, str]:
    text = _text(soup.select_one("#contents") or soup)
    term_match = _REGULAR_TERM_RE.search(text)
    marker = text.find("교육기간")
    dates = _DATE_RE.findall(text[marker : marker + 180]) if marker >= 0 else []
    if term_match is None or len(dates) < 2:
        raise ValueError("regular catalogue term banner is missing")
    start = f"{int(dates[0][0]):04d}-{int(dates[0][1]):02d}-{int(dates[0][2]):02d}"
    end = f"{int(dates[1][0]):04d}-{int(dates[1][1]):02d}-{int(dates[1][2]):02d}"
    return f"{term_match.group(1)}-{int(term_match.group(2))}", f"{start} ~ {end}"


def _regular_rows(
    soup: BeautifulSoup,
    branch: RegularBranch,
) -> list[dict[str, str]]:
    table = soup.select_one("table")
    if table is None or _headers(table) != _REGULAR_HEADERS:
        raise ValueError(f"regular {branch.key} list headers changed")
    result: list[dict[str, str]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        link = tr.select_one("a[onclick*='goView']")
        if len(cells) < 6 or link is None:
            continue
        number_node = tr.find("th")
        match = _REGULAR_ID_RE.search(_clean(link.get("onclick")))
        if number_node is None or match is None:
            raise ValueError("regular list row identity is missing")
        title_node = link.find("strong")
        apply_node = link.find("small")
        session = _text(cells[0])
        if branch.key not in session:
            raise ValueError(f"regular {branch.key} filter returned another branch")
        result.append(
            {
                "number": _text(number_node),
                "identity": match.group(1),
                "session": session,
                "title": _text(title_node or link),
                "apply_period": _normal_period(_text(apply_node).strip("() ")),
                "category": _text(cells[2]),
                "schedule": _text(cells[3]),
                "fee": _text(cells[4]),
                "capacity_status": _text(cells[5]),
            }
        )
    return result


def _ongoing_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
    table = soup.select_one("table")
    if table is None or _headers(table) != _ONGOING_HEADERS:
        raise ValueError("ongoing education list headers changed")
    result: list[dict[str, str]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        link = tr.select_one("a[href*='eIdx'], a[onclick*='goView']")
        if len(cells) < 7 or link is None:
            continue
        number_node = tr.find("th")
        identity_match = _ONGOING_ID_RE.search(
            _clean(link.get("href")) + " " + _clean(link.get("onclick"))
        )
        if number_node is None or identity_match is None:
            raise ValueError("ongoing education row identity is missing")
        status_node = cells[1].select_one(".stat-bagde")
        result.append(
            {
                "number": _text(number_node),
                "identity": identity_match.group(1),
                "business": _text(cells[0]),
                "title": _text(link),
                "status": _text(status_node),
                "venue": _text(cells[2]),
                "period": _normal_period(_text(cells[3])),
                "schedule": _text(cells[4]),
                "capacity": _text(cells[5]),
                "application_method": _text(cells[6]),
            }
        )
    return result


def _scan_numbered_source(
    client: _Client,
    *,
    first_soup: BeautifulSoup,
    first_rows: list[dict[str, str]],
    page_size: int,
    make_url: Callable[[int], str],
    parse_rows: Callable[[BeautifulSoup], list[dict[str, str]]],
    remaining_pages: Callable[[], int],
    use_page: Callable[[], None],
) -> tuple[list[dict[str, str]], int, int]:
    if not first_rows:
        raise ValueError("numbered catalogue page one is unexpectedly empty")
    try:
        total = int(first_rows[0]["number"].replace(",", ""))
    except (KeyError, ValueError) as exc:
        raise ValueError("numbered catalogue total is invalid") from exc
    data_pages = math.ceil(total / page_size)
    required = data_pages + 1
    if remaining_pages() < required - 1:
        raise ValueError(
            f"max_pages cap cannot prove {required} list pages including sentinel"
        )
    all_rows = list(first_rows)
    for page in range(2, data_pages + 1):
        use_page()
        all_rows.extend(parse_rows(client.get(make_url(page))))
    use_page()
    sentinel = client.get(make_url(data_pages + 1))
    sentinel_rows = parse_rows(sentinel)
    # Both Pyeongtaek City numbered catalogues retain the exact table schema
    # but leave ``tbody`` structurally empty beyond their last page.
    if sentinel_rows:
        raise ValueError("immediate numbered-page sentinel is not empty")
    expected_numbers = list(range(total, 0, -1))
    actual_numbers = [int(item["number"].replace(",", "")) for item in all_rows]
    if actual_numbers != expected_numbers:
        raise ValueError("numbered catalogue rows are incomplete or reordered")
    identities = [item["identity"] for item in all_rows]
    if len(identities) != len(set(identities)):
        raise ValueError("numbered catalogue has duplicate source identities")
    return all_rows, data_pages, 1


def _ongoing_branch(
    item: Mapping[str, str],
    pairs: Mapping[str, str],
) -> tuple[str, str, str]:
    text = " ".join((item.get("title", ""), item.get("venue", ""), pairs.get("장소", "")))
    for branch in REGULAR_BRANCHES:
        if branch.key in text:
            return branch.name, f"regular:{branch.code}", branch.address
    owner = _clean(pairs.get("기관")) or "평택시 평생학습과"
    if "평생학습과" in owner:
        owner = "평택시 평생학습과"
    return owner, "ongoing:city", ""


def collect_pyeongtaek_lifelong_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 40,
    detail_limit: int = 300,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect all current/future regular and ongoing lifelong courses."""

    if not is_pyeongtaek_lifelong_target(target):
        return [], PYEONGTAEK_LIFELONG_PARSER, _failure(
            "target does not match the canonical Pyeongtaek lifelong route", 4
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], PYEONGTAEK_LIFELONG_PARSER, _failure(
                "managed session_factory injection is required", 4
            )
        session_factory = _default_session_factory
    try:
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        cutoff = _today(today)
        if allowed_pages < 0 or allowed_details < 0:
            raise ValueError
    except (TypeError, ValueError):
        return [], PYEONGTAEK_LIFELONG_PARSER, _failure(
            "max_pages/detail_limit/today are invalid", 4
        )

    client = _Client(session_factory, fetcher, timeout)
    errors: list[str] = []
    list_requests = 0
    data_pages = 0
    sentinel_pages = 0
    detail_attempts = 0
    detail_pages = 0
    source_totals: dict[str, int] = {}
    candidates: list[tuple[str, Any, dict[str, str], str]] = []

    def remaining() -> int:
        return allowed_pages - list_requests

    def take_page() -> None:
        nonlocal list_requests
        if list_requests >= allowed_pages:
            raise ValueError("max_pages cap reached before pagination sentinel")
        list_requests += 1

    try:
        regular_ids: set[str] = set()
        term_contract: Optional[tuple[str, str]] = None
        for branch in REGULAR_BRANCHES:
            take_page()
            first = client.get(regular_list_url(branch.code, 1))
            contract = _regular_global_contract(first)
            if term_contract is None:
                term_contract = contract
            elif contract != term_contract:
                raise ValueError("regular branch term banners disagree")
            parsed_first = _regular_rows(first, branch)
            branch_rows, branch_data_pages, branch_sentinels = _scan_numbered_source(
                client,
                first_soup=first,
                first_rows=parsed_first,
                page_size=PYEONGTAEK_REGULAR_PAGE_SIZE,
                make_url=lambda page, code=branch.code: regular_list_url(code, page),
                parse_rows=lambda soup, current=branch: _regular_rows(soup, current),
                remaining_pages=remaining,
                use_page=take_page,
            )
            data_pages += branch_data_pages
            sentinel_pages += branch_sentinels
            source_totals[f"regular:{branch.key}"] = len(branch_rows)
            overlap = regular_ids.intersection(item["identity"] for item in branch_rows)
            if overlap:
                raise ValueError("regular branch filters overlap by source identity")
            regular_ids.update(item["identity"] for item in branch_rows)
            assert term_contract is not None
            term, period = term_contract
            if _is_current(period, cutoff):
                for item in branch_rows:
                    if item["session"].startswith(term + " "):
                        candidates.append(("regular", branch, item, period))

        take_page()
        ongoing_first = client.get(ongoing_list_url(1))
        ongoing_rows, ongoing_data_pages, ongoing_sentinels = _scan_numbered_source(
            client,
            first_soup=ongoing_first,
            first_rows=_ongoing_rows(ongoing_first),
            page_size=PYEONGTAEK_ONGOING_PAGE_SIZE,
            make_url=ongoing_list_url,
            parse_rows=_ongoing_rows,
            remaining_pages=remaining,
            use_page=take_page,
        )
        data_pages += ongoing_data_pages
        sentinel_pages += ongoing_sentinels
        source_totals["ongoing"] = len(ongoing_rows)
        for item in ongoing_rows:
            if _is_current(item["period"], cutoff):
                candidates.append(("ongoing", None, item, item["period"]))

        identity_keys = [(kind, item["identity"]) for kind, _, item, _ in candidates]
        if len(identity_keys) != len(set(identity_keys)):
            raise ValueError("current lifelong source identities are duplicated")
        if len(candidates) > allowed_details:
            raise ValueError(
                f"detail_limit {allowed_details} is below required {len(candidates)}"
            )

        rows: list[dict[str, Any]] = []
        for kind, branch, item, list_period in candidates:
            detail_attempts += 1
            identity = item["identity"]
            raw_url = (
                regular_detail_url(identity)
                if kind == "regular"
                else ongoing_detail_url(identity)
            )
            soup = client.get(raw_url)
            pairs = _pairs(soup)
            required = (
                _REGULAR_DETAIL_REQUIRED if kind == "regular" else _ONGOING_DETAIL_REQUIRED
            )
            missing = required.difference(pairs)
            if missing:
                raise ValueError(
                    f"{kind} detail {identity} fields changed: {sorted(missing)}"
                )
            detail_pages += 1
            title = pairs["강좌명"]
            if _clean(title) != _clean(item["title"]):
                raise ValueError(f"{kind} detail {identity} title mismatch")
            if kind == "regular":
                assert isinstance(branch, RegularBranch)
                if branch.key not in pairs["학습공간"]:
                    raise ValueError(f"regular detail {identity} branch mismatch")
                period = _normal_period(pairs["교육일정"])
                if not _is_current(period, cutoff):
                    raise ValueError(f"regular current-term detail {identity} is expired")
                status = _status(pairs["상태"])
                capacity = _clean(
                    pairs.get("인원(신청/정원)")
                    or pairs.get("인원(신청/인원)")
                )
                if not capacity:
                    capacity_match = re.search(
                        r"[\d,]+\s*/\s*[\d,]+",
                        item.get("capacity_status", ""),
                    )
                    capacity = _clean(
                        capacity_match.group(0) if capacity_match else ""
                    )
                row = _base_row(target, "regular", identity, title, raw_url)
                row.update(
                    {
                        "branch": branch.name,
                        "branch_code": f"regular:{branch.code}",
                        "preserve_branch": True,
                        "category": pairs["과목유형"],
                        "period": period,
                        "apply_period": item["apply_period"],
                        "schedule_raw": pairs["교육시간/요일"],
                        "target": pairs["교육대상"],
                        "capacity": capacity,
                        "status": status,
                        "fee": pairs["수강료"],
                        "venue_name": pairs["교육장소"],
                        "venue_address": branch.address,
                        "address": branch.address,
                        "collection_category": "평생학습 정기교육",
                        "source_group": "lifelong_learning",
                        "application_url": _application_url(raw_url, status),
                        "raw_fields": {
                            "parser": PYEONGTAEK_LIFELONG_PARSER,
                            "source": "regular",
                            "source_id": identity,
                            "session": item["session"],
                            "list_period": list_period,
                            "detail_pairs": pairs,
                            "capacity_omitted": not bool(capacity),
                        },
                    }
                )
            else:
                period = _normal_period(pairs["교육기간"])
                if not _is_current(period, cutoff):
                    raise ValueError(f"ongoing detail {identity} period mismatch")
                branch_name, branch_code, branch_address = _ongoing_branch(
                    item,
                    pairs,
                )
                status = _status(item["status"])
                row = _base_row(target, "ongoing", identity, title, raw_url)
                row.update(
                    {
                        "branch": branch_name,
                        "branch_code": branch_code,
                        "preserve_branch": True,
                        "category": item["business"],
                        "period": period,
                        "apply_period": _normal_period(pairs["접수기간"]),
                        "schedule_raw": pairs["강의시간"],
                        "target": pairs["대상"],
                        "capacity": (
                            f"확정 {pairs['확정인원']} / 신청 {pairs['신청인원']} / "
                            f"정원 {pairs['정원']}"
                        ),
                        "status": status,
                        "fee": pairs["수강료"],
                        "venue_name": pairs["장소"],
                        "venue_address": branch_address,
                        "address": branch_address,
                        "collection_category": "평생학습 상시교육",
                        "source_group": "lifelong_learning",
                        "application_url": _application_url(raw_url, status),
                        "raw_fields": {
                            "parser": PYEONGTAEK_LIFELONG_PARSER,
                            "source": "ongoing",
                            "source_id": identity,
                            "business": item["business"],
                            "application_method": pairs["접수방법"],
                            "detail_pairs": pairs,
                        },
                    }
                )
            rows.append(row)

        result = _finish_rows(rows, dedupe_rows)
        meta = {
            "pages": list_requests,
            "data_pages": data_pages,
            "sentinel_pages": sentinel_pages,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "source_count": 4,
            "source_total": sum(source_totals.values()),
            "source_rows": sum(source_totals.values()),
            "source_totals": source_totals,
            "current_count": len(candidates),
            "returned_count": len(result),
            "pagination_complete": True,
            "details_complete": detail_pages == len(candidates),
            "snapshot_complete": detail_pages == len(candidates),
            "no_current_data": not result,
            "configured_collection_error": "",
            "sessions_created": client.sessions_created,
            "physical_requests": client.physical_requests,
            "semantic_duplicate_count": 0,
        }
        return result, PYEONGTAEK_LIFELONG_PARSER, meta
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        meta = _failure("; ".join(errors), 4)
        meta.update(
            {
                "pages": list_requests,
                "data_pages": data_pages,
                "sentinel_pages": sentinel_pages,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "source_total": sum(source_totals.values()),
                "source_rows": sum(source_totals.values()),
                "source_totals": source_totals,
                "current_count": len(candidates),
                "sessions_created": client.sessions_created,
                "physical_requests": client.physical_requests,
            }
        )
        return [], PYEONGTAEK_LIFELONG_PARSER, meta
    finally:
        client.close()


def _ptlib_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
    table = soup.select_one("table")
    if table is None or _headers(table) != _PTLIB_HEADERS:
        raise ValueError("PTLIB list headers changed")
    result: list[dict[str, str]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        link = tr.select_one("a[onclick*='fnDetail']")
        if len(cells) < 6 or link is None:
            continue
        match = _PTLIB_ID_RE.search(_clean(link.get("onclick")))
        if match is None:
            raise ValueError("PTLIB row identity is missing")
        result.append(
            {
                "identity": match.group(1),
                "branch": _text(cells[0]),
                "title": _text(link),
                "period": _normal_period(_text(cells[2])),
                "target": _text(cells[3]),
                "capacity": _text(cells[4]),
                "status": _text(cells[5]),
            }
        )
    return result


def _ptlib_declared_last(soup: BeautifulSoup) -> int:
    last = soup.select_one(".paging a.last, .paging a.btn-paging.last")
    match = _PAGE_RE.search(_clean(last.get("href"))) if last is not None else None
    if match:
        return int(match.group(1))
    return 1


def _ptlib_branch(label: str) -> tuple[str, str]:
    if label not in PTLIB_BRANCHES:
        raise ValueError(f"unknown PTLIB branch label: {label!r}")
    return PTLIB_BRANCHES[label]


def collect_ptlib_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 60,
    detail_limit: int = 120,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete PTLIB catalogue and enrich every live course."""

    if not is_ptlib_target(target):
        return [], PTLIB_PARSER, _failure("target does not match the canonical PTLIB route", 1)
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], PTLIB_PARSER, _failure("managed session_factory injection is required", 1)
        session_factory = _default_session_factory
    try:
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        cutoff = _today(today)
        if allowed_pages < 0 or allowed_details < 0:
            raise ValueError
    except (TypeError, ValueError):
        return [], PTLIB_PARSER, _failure("max_pages/detail_limit/today are invalid", 1)

    client = _Client(session_factory, fetcher, timeout)
    list_requests = 0
    data_pages = 0
    sentinel_pages = 0
    detail_attempts = 0
    detail_pages = 0
    source_rows: list[dict[str, str]] = []
    candidates: list[dict[str, str]] = []
    try:
        if allowed_pages < 2:
            raise ValueError("max_pages must include page one and an empty sentinel")
        first = client.get(ptlib_list_url(1))
        list_requests += 1
        declared_last = _ptlib_declared_last(first)
        if allowed_pages < declared_last + 1:
            raise ValueError(
                f"max_pages {allowed_pages} is below required {declared_last + 1}"
            )
        first_rows = _ptlib_rows(first)
        if not first_rows:
            raise ValueError("PTLIB page one is unexpectedly empty")
        source_rows.extend(first_rows)
        for page in range(2, declared_last + 1):
            parsed = _ptlib_rows(client.get(ptlib_list_url(page)))
            list_requests += 1
            if not parsed:
                raise ValueError(f"PTLIB declared data page {page} is empty")
            if page < declared_last and len(parsed) != PTLIB_PAGE_SIZE:
                raise ValueError(f"PTLIB page {page} ended before declared last page")
            source_rows.extend(parsed)
        sentinel = client.get(ptlib_list_url(declared_last + 1))
        list_requests += 1
        sentinel_rows = _ptlib_rows(sentinel)
        # PTLIB renders an exact, schema-bearing table with an empty ``tbody``
        # beyond the last page; unlike the other two portals it has no textual
        # no-result label on that sentinel response.
        if sentinel_rows:
            raise ValueError("PTLIB immediate sentinel is not empty")
        data_pages = declared_last
        sentinel_pages = 1
        identities = [item["identity"] for item in source_rows]
        if len(identities) != len(set(identities)):
            raise ValueError("PTLIB source identities are duplicated")
        candidates = [item for item in source_rows if _is_current(item["period"], cutoff)]
        if len(candidates) > allowed_details:
            raise ValueError(
                f"detail_limit {allowed_details} is below required {len(candidates)}"
            )

        rows: list[dict[str, Any]] = []
        for item in candidates:
            identity = item["identity"]
            raw_url = ptlib_detail_url(identity)
            detail_attempts += 1
            pairs = _pairs(client.get(raw_url))
            missing = _PTLIB_DETAIL_REQUIRED.difference(pairs)
            if missing:
                raise ValueError(f"PTLIB detail {identity} fields changed: {sorted(missing)}")
            detail_pages += 1
            if _clean(pairs["문화행사명"]) != _clean(item["title"]):
                raise ValueError(f"PTLIB detail {identity} title mismatch")
            code, expected_branch = _ptlib_branch(item["branch"])
            if _clean(pairs["기관명"]) != expected_branch:
                raise ValueError(f"PTLIB detail {identity} branch mismatch")
            period = _normal_period(pairs["교육기간"])
            if not _is_current(period, cutoff):
                raise ValueError(f"PTLIB detail {identity} period mismatch")
            # PTLIB changes the field label with the supported channel
            # (온라인접수/방문접수), and informational programmes may expose
            # no application channel at all.  The catalogue status remains
            # authoritative in that last case.
            reception = next(
                (
                    value
                    for key, value in pairs.items()
                    if key.endswith("접수") and key != "모집기간"
                ),
                "",
            )
            status = _status(reception or item["status"])
            capacity_match = re.search(r"([\d,]+\s*/\s*[\d,]+)", reception)
            row = _base_row(target, "lecture", identity, pairs["문화행사명"], raw_url)
            row.update(
                {
                    "branch": expected_branch,
                    "branch_code": code,
                    "branch_url": PTLIB_URL,
                    "preserve_branch": True,
                    "category": "도서관 문화행사",
                    "period": period,
                    "apply_period": _normal_period(pairs["모집기간"]),
                    "schedule_raw": pairs["시간"],
                    "target": pairs["대상"],
                    "capacity": _clean(capacity_match.group(1) if capacity_match else item["capacity"]),
                    "status": status,
                    "fee": "",
                    "venue_name": pairs["장소"],
                    "collection_category": "도서관",
                    "source_group": "library",
                    "operator_type": "도서관",
                    "application_url": _application_url(raw_url, status),
                    "raw_fields": {
                        "parser": PTLIB_PARSER,
                        "source_id": identity,
                        "list_branch": item["branch"],
                        "detail_pairs": pairs,
                    },
                }
            )
            rows.append(row)
        result = _finish_rows(rows, dedupe_rows)
        return result, PTLIB_PARSER, {
            "pages": list_requests,
            "data_pages": data_pages,
            "sentinel_pages": sentinel_pages,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "source_count": 1,
            "source_total": len(source_rows),
            "source_rows": len(source_rows),
            "current_count": len(candidates),
            "returned_count": len(result),
            "pagination_complete": True,
            "details_complete": detail_pages == len(candidates),
            "snapshot_complete": detail_pages == len(candidates),
            "no_current_data": not result,
            "configured_collection_error": "",
            "sessions_created": client.sessions_created,
            "physical_requests": client.physical_requests,
            "semantic_duplicate_count": 0,
        }
    except Exception as exc:
        meta = _failure(f"{type(exc).__name__}: {exc}", 1)
        meta.update(
            {
                "pages": list_requests,
                "data_pages": data_pages,
                "sentinel_pages": sentinel_pages,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "source_total": len(source_rows),
                "source_rows": len(source_rows),
                "current_count": len(candidates),
                "sessions_created": client.sessions_created,
                "physical_requests": client.physical_requests,
            }
        )
        return [], PTLIB_PARSER, meta
    finally:
        client.close()


def _goe_rows(soup: BeautifulSoup, source: GoeSource) -> list[dict[str, str]]:
    table = soup.select_one("table")
    if table is None or _headers(table) != _GOE_HEADERS:
        raise ValueError(f"GOE {source.key} list headers changed")
    result: list[dict[str, str]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        link = tr.select_one("a.detail-btn")
        if len(cells) < 5 or link is None:
            continue
        identity = _clean(link.get("keyvalue3"))
        group_idx = _clean(link.get("keyvalue1"))
        category_idx = _clean(link.get("keyvalue2"))
        large_code = _clean(link.get("keyvalue5"))
        if not identity or not group_idx or large_code != source.large_code:
            raise ValueError(f"GOE {source.key} row identity contract changed")
        target_node = tr.select_one("dd.con")
        result.append(
            {
                "identity": identity,
                "group_idx": group_idx,
                "category_idx": category_idx or "0",
                "title": _text(link),
                "target": _text(target_node).removeprefix("대상 :").strip(),
                "capacity": _text(cells[1]),
                "period": _normal_period(_text(cells[2])),
                "apply_period": _normal_period(_text(cells[3])),
                "status": _text(cells[4]),
            }
        )
    return result


def _goe_declared_last(soup: BeautifulSoup) -> int:
    pages = []
    for node in soup.select("#cms_paging a[keyvalue]"):
        value = _clean(node.get("keyvalue"))
        if value.isdigit():
            pages.append(int(value))
    return max(pages, default=1)


def collect_pyeongtaek_goe_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 30,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect all current/future GOE Pyeongtaek Library teach catalogues."""

    if not is_pyeongtaek_goe_target(target):
        return [], PYEONGTAEK_GOE_PARSER, _failure(
            "target does not match the canonical GOE Pyeongtaek Library route", 3
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], PYEONGTAEK_GOE_PARSER, _failure(
                "managed session_factory injection is required", 3
            )
        session_factory = _default_session_factory
    try:
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        cutoff = _today(today)
        if allowed_pages < 0 or allowed_details < 0:
            raise ValueError
    except (TypeError, ValueError):
        return [], PYEONGTAEK_GOE_PARSER, _failure(
            "max_pages/detail_limit/today are invalid", 3
        )

    client = _Client(session_factory, fetcher, timeout)
    list_requests = 0
    data_pages = 0
    sentinel_pages = 0
    detail_attempts = 0
    detail_pages = 0
    source_totals: dict[str, int] = {}
    candidates: list[tuple[GoeSource, dict[str, str]]] = []
    try:
        all_ids: set[str] = set()
        for source in GOE_SOURCES:
            if list_requests >= allowed_pages:
                raise ValueError("max_pages cap reached before GOE page one")
            first = client.get(goe_list_url(source, 1))
            list_requests += 1
            first_rows = _goe_rows(first, source)
            if not first_rows:
                raise ValueError(f"GOE {source.key} page one is unexpectedly empty")
            last_page = _goe_declared_last(first)
            if allowed_pages - list_requests < last_page:
                raise ValueError(
                    f"max_pages cannot prove GOE {source.key} pages and sentinel"
                )
            source_rows = list(first_rows)
            for page in range(2, last_page + 1):
                parsed = _goe_rows(client.get(goe_list_url(source, page)), source)
                list_requests += 1
                if not parsed:
                    raise ValueError(f"GOE {source.key} declared page {page} is empty")
                source_rows.extend(parsed)
            sentinel = client.get(goe_list_url(source, last_page + 1))
            list_requests += 1
            if _goe_rows(sentinel, source) or not _is_empty(sentinel):
                raise ValueError(f"GOE {source.key} immediate sentinel is not empty")
            data_pages += last_page
            sentinel_pages += 1
            source_totals[source.key] = len(source_rows)
            ids = [item["identity"] for item in source_rows]
            if len(ids) != len(set(ids)) or all_ids.intersection(ids):
                raise ValueError("GOE teach catalogues overlap by source identity")
            all_ids.update(ids)
            for item in source_rows:
                if _is_current(item["period"], cutoff):
                    candidates.append((source, item))
        if len(candidates) > allowed_details:
            raise ValueError(
                f"detail_limit {allowed_details} is below required {len(candidates)}"
            )

        rows: list[dict[str, Any]] = []
        for source, item in candidates:
            identity = item["identity"]
            raw_url = goe_detail_url(source, item)
            detail_attempts += 1
            pairs = _pairs(client.get(raw_url))
            missing = _GOE_DETAIL_REQUIRED.difference(pairs)
            if missing:
                raise ValueError(
                    f"GOE detail {identity} fields changed: {sorted(missing)}"
                )
            detail_pages += 1
            period = _normal_period(pairs["강의기간"])
            if not _is_current(period, cutoff):
                raise ValueError(f"GOE detail {identity} period mismatch")
            status = _status(item["status"])
            row = _base_row(target, source.key, identity, item["title"], raw_url)
            row.update(
                {
                    "branch": PYEONGTAEK_GOE_BRANCH,
                    "branch_code": f"goe:{source.large_code}",
                    "branch_url": PYEONGTAEK_GOE_ROOT_URL,
                    "preserve_branch": True,
                    "category": pairs["강의 분류"] or source.category,
                    "period": period,
                    "apply_period": _normal_period(pairs["접수기간"]),
                    "schedule_raw": _clean(
                        f"{pairs['강의요일']} {pairs['강의시간']}"
                    ),
                    "target": pairs["강의대상"],
                    "capacity": _clean(
                        f"참여 {pairs['현재 참여 / 모집']} / "
                        f"대기 {pairs['현재 대기자 / 대기자']}"
                    ),
                    "status": status,
                    "fee": pairs["준비물 및 재료비"],
                    "venue_name": pairs["강의장소"],
                    "venue_address": PYEONGTAEK_GOE_ADDRESS,
                    "address": PYEONGTAEK_GOE_ADDRESS,
                    "description": pairs["강의 설명"],
                    "collection_category": "도서관",
                    "source_group": "library",
                    "operator_type": "교육청도서관",
                    "application_url": _application_url(raw_url, status),
                    "raw_fields": {
                        "parser": PYEONGTAEK_GOE_PARSER,
                        "source": source.key,
                        "source_id": identity,
                        "group_idx": item["group_idx"],
                        "category_idx": item["category_idx"],
                        "detail_pairs": pairs,
                    },
                }
            )
            rows.append(row)
        result = _finish_rows(rows, dedupe_rows)
        return result, PYEONGTAEK_GOE_PARSER, {
            "pages": list_requests,
            "data_pages": data_pages,
            "sentinel_pages": sentinel_pages,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "source_count": 3,
            "source_total": sum(source_totals.values()),
            "source_rows": sum(source_totals.values()),
            "source_totals": source_totals,
            "current_count": len(candidates),
            "returned_count": len(result),
            "pagination_complete": True,
            "details_complete": detail_pages == len(candidates),
            "snapshot_complete": detail_pages == len(candidates),
            "no_current_data": not result,
            "configured_collection_error": "",
            "sessions_created": client.sessions_created,
            "physical_requests": client.physical_requests,
            "semantic_duplicate_count": 0,
        }
    except Exception as exc:
        meta = _failure(f"{type(exc).__name__}: {exc}", 3)
        meta.update(
            {
                "pages": list_requests,
                "data_pages": data_pages,
                "sentinel_pages": sentinel_pages,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "source_total": sum(source_totals.values()),
                "source_rows": sum(source_totals.values()),
                "source_totals": source_totals,
                "current_count": len(candidates),
                "sessions_created": client.sessions_created,
                "physical_requests": client.physical_requests,
            }
        )
        return [], PYEONGTAEK_GOE_PARSER, meta
    finally:
        client.close()


def collect_pyeongtaek_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 60,
    detail_limit: int = 300,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if is_pyeongtaek_lifelong_target(target):
        return collect_pyeongtaek_lifelong_courses(
            target, timeout, max_pages, detail_limit, **kwargs
        )
    if is_ptlib_target(target):
        return collect_ptlib_courses(target, timeout, max_pages, detail_limit, **kwargs)
    if is_pyeongtaek_goe_target(target):
        return collect_pyeongtaek_goe_courses(
            target, timeout, max_pages, detail_limit, **kwargs
        )
    return [], "pyeongtaek_unknown_target", _failure(
        "target does not match a canonical Pyeongtaek education route", 0
    )


collect = collect_pyeongtaek_courses


__all__ = [
    "GOE_SOURCES",
    "PYEONGTAEK_GOE_ADDRESS",
    "PYEONGTAEK_GOE_BRANCH",
    "PYEONGTAEK_GOE_CANDIDATE_ID",
    "PYEONGTAEK_GOE_PARSER",
    "PYEONGTAEK_GOE_PROVIDER",
    "PYEONGTAEK_GOE_ROOT_URL",
    "PYEONGTAEK_LIFELONG_CANDIDATE_ID",
    "PYEONGTAEK_LIFELONG_INSTRUCTION_URL",
    "PYEONGTAEK_LIFELONG_PARSER",
    "PYEONGTAEK_LIFELONG_PROVIDER",
    "PYEONGTAEK_MUNICIPALITY_CODE",
    "PYEONGTAEK_MUNICIPALITY_NAME",
    "PYEONGTAEK_ONGOING_URL",
    "PYEONGTAEK_REGULAR_URL",
    "PTLIB_CANDIDATE_ID",
    "PTLIB_PARSER",
    "PTLIB_PROVIDER",
    "PTLIB_URL",
    "REGULAR_BRANCHES",
    "collect",
    "collect_ptlib_courses",
    "collect_pyeongtaek_courses",
    "collect_pyeongtaek_goe_courses",
    "collect_pyeongtaek_lifelong_courses",
    "goe_detail_url",
    "goe_list_url",
    "is_pyeongtaek_goe_target",
    "is_pyeongtaek_lifelong_target",
    "is_ptlib_target",
    "is_target",
    "ongoing_detail_url",
    "ongoing_list_url",
    "ptlib_detail_url",
    "ptlib_list_url",
    "regular_detail_url",
    "regular_list_url",
]
