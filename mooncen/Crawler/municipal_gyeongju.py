"""Fail-closed collector for Gyeongju City's complete municipal education owner.

The municipality exposes two non-overlapping, official course ledgers on the
same host:

* ``/reserve/lecture/list.jsp`` is the public-service-reservation ``강좌``
  ledger.  It includes youth, culture, sports and municipal camp courses.
* ``/gjlll/main/lecture/index.do?menu_idx=126`` is the regular
  Gyeongju Lifelong Learning Family Center ledger.

The discovery candidate that led to this audit is a single, expired
``특성화 프로그램(사업)`` grant-application detail.  It is not a canonical
course list.  The related special-program list and the multi-owner directory
of external lifelong-learning institutions are audited as empty adjuncts but
are not promoted as independent owners.

Every published snapshot proves the two list boundaries, stable first/last
edges, the official category and branch partitions of the integrated ledger,
and every current/future detail.  Reservation/application URLs are validated
but never fetched.  Instructor, manager, phone, attachments, images and
free-text bodies are deliberately outside the returned allowlist.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GYEONGJU_PROVIDER = "MUNI_WWW_GYEONGJU_GO_KR_ADA8A467"
GYEONGJU_DISCOVERY_CANDIDATE_ID = "MUNI_IR_2FE200C041D5"
GYEONGJU_CANONICAL_CANDIDATE_ID = "MUNI_IR_4E1F48CB4B18"
GYEONGJU_CANONICAL_DERIVED_PROVIDER = "MUNI_WWW_GYEONGJU_GO_KR_BCB069F8"
GYEONGJU_MUNICIPALITY_CODE = "4713000000"
GYEONGJU_MUNICIPALITY_NAME = "경상북도 경주시"

GYEONGJU_HOST = "www.gyeongju.go.kr"
GYEONGJU_INTEGRATED_PATH = "/reserve/lecture/list.jsp"
GYEONGJU_INTEGRATED_URL = f"https://{GYEONGJU_HOST}{GYEONGJU_INTEGRATED_PATH}"
GYEONGJU_LIFELONG_LIST_PATH = "/gjlll/main/lecture/index.do"
GYEONGJU_LIFELONG_DETAIL_PATH = "/gjlll/main/lecture/view.do"
GYEONGJU_LIFELONG_URL = (
    f"https://{GYEONGJU_HOST}{GYEONGJU_LIFELONG_LIST_PATH}?menu_idx=126"
)
GYEONGJU_SPECIAL_PATH = "/gjlll/main/lecture/otherLects.do"
GYEONGJU_SPECIAL_URL = f"https://{GYEONGJU_HOST}{GYEONGJU_SPECIAL_PATH}?menu_idx=203"
GYEONGJU_INSTITUTION_PATH = "/gjlll/main/lecture/indexEngn.do"
GYEONGJU_INSTITUTION_URL = (
    f"https://{GYEONGJU_HOST}{GYEONGJU_INSTITUTION_PATH}?menu_idx=125"
)
GYEONGJU_DISCOVERY_DETAIL_URL = (
    f"https://{GYEONGJU_HOST}{GYEONGJU_LIFELONG_DETAIL_PATH}"
    "?lect_no=20264583&menu_idx=203"
)
GYEONGJU_RESERVATION_HOME_URL = f"https://{GYEONGJU_HOST}/reserve/"
GYEONGJU_EXPERIENCE_URL = f"https://{GYEONGJU_HOST}/reserve/exp_facilities/list.jsp"
GYEONGJU_DATA_API_URL = "https://www.data.go.kr/data/15109406/openapi.do"

GYEONGJU_PAGE_SIZE = 10
GYEONGJU_DEFAULT_MAX_PAGES = 80
GYEONGJU_DEFAULT_DETAIL_LIMIT = 500
GYEONGJU_MAX_WORKERS = 3
GYEONGJU_FETCH_ATTEMPTS = 2
GYEONGJU_MAX_HTML_BYTES = 3_000_000
GYEONGJU_LIFELONG_BRANCH = "경주시평생학습가족관"
GYEONGJU_LIFELONG_ADDRESS = "경상북도 경주시 북성로 87"
GYEONGJU_PARSER = (
    "gyeongju_integrated_complete_education+lifelong_complete_education+"
    "consecutive_empty_sentinels+stable_edges+category_branch_census+"
    "all_current_details+lifelong_capacity_and_application_phases+"
    "pre_detail_test_exclusion+application_control_no_form_fetch+pii_allowlist"
)
GYEONGJU_OWNERSHIP_SCOPE = (
    "official_gyeongju_integrated_courses_plus_municipal_lifelong_courses"
)

GYEONGJU_CANDIDATE_DECISIONS: Mapping[str, str] = {
    GYEONGJU_CANONICAL_CANDIDATE_ID: (
        "retarget_incumbent_provider_to_complete_integrated_lecture_ledger"
    ),
    GYEONGJU_DISCOVERY_CANDIDATE_ID: (
        "exclude_single_expired_special_grant_detail; audited_by_canonical_owner"
    ),
}

GYEONGJU_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, str]] = {
    "integrated_courses": {
        "url": GYEONGJU_INTEGRATED_URL,
        "decision": "include_as_canonical_complete_municipal_course_ledger",
    },
    "regular_lifelong_courses": {
        "url": GYEONGJU_LIFELONG_URL,
        "decision": "include_as_non_overlapping_municipal_lifelong_section",
    },
    "special_program_discovery_detail": {
        "url": GYEONGJU_DISCOVERY_DETAIL_URL,
        "decision": "exclude_single_expired_grant_application_detail_not_a_list",
    },
    "external_institution_directory": {
        "url": GYEONGJU_INSTITUTION_URL,
        "decision": "exclude_multi_owner_directory; audit_empty_boundary_only",
    },
    "experience_reservation": {
        "url": GYEONGJU_EXPERIENCE_URL,
        "decision": "exclude_separate_experience_owner",
    },
    "public_data_metadata": {
        "url": GYEONGJU_DATA_API_URL,
        "decision": "exclude_metadata_page_not_course_ledger",
    },
}


@dataclass(frozen=True)
class GyeongjuBranch:
    member_id: str
    source_name: str
    navigation_name: str
    address: str


GYEONGJU_BRANCHES: tuple[GyeongjuBranch, ...] = (
    GyeongjuBranch(
        "B0000006",
        "청소년수련관",
        "청소년수련관",
        "경상북도 경주시 알천북로 131",
    ),
    GyeongjuBranch(
        "B0000025",
        "안강청소년문화의집",
        "안강청소년문화의집",
        "경상북도 경주시 안강읍 안현로 1476",
    ),
    GyeongjuBranch(
        "B0000011",
        "외동읍민체육회관",
        "외동배드민턴클럽",
        "경상북도 경주시 외동읍 신기앞길 67-62",
    ),
    GyeongjuBranch(
        "B0000027",
        "외동생활체육공원",
        "외동생활체육공원",
        "경상북도 경주시 외동읍 입실리 297",
    ),
    GyeongjuBranch(
        "B0000031",
        "북천체육시설",
        "북천체육시설",
        "경상북도 경주시 구황동 883-99",
    ),
    GyeongjuBranch(
        "B0000034",
        "경주화랑마을 방탈출",
        "화랑마을캠프",
        "경상북도 경주시 석현로 123",
    ),
    GyeongjuBranch(
        "B0000037",
        "경주시여성행복드림센터",
        "경주시여성행복드림센터 생활문화센터",
        "경상북도 경주시 용황로14길 36",
    ),
)

GYEONGJU_EMPTY_ACTIVE_BRANCH = GyeongjuBranch(
    "B0000032",
    "불국체육센터",
    "불국체육센터",
    "경상북도 경주시 불국신택지2길 29",
)

GYEONGJU_CATEGORY_FILTERS: Mapping[str, str] = {
    "LNG": "어학",
    "ENT": "예능",
    "INT": "취미",
    "JOB": "취업교육",
    "COM": "컴퓨터",
    "ETC": "기타",
    "SPT": "스포츠",
}

GYEONGJU_PII_FIELDS_NEVER_PERSISTED = (
    "강사",
    "담당자",
    "문의전화",
    "첨부파일",
    "강좌소개 자유문",
    "신청자 정보",
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_INTEGRATED_ID_RE = re.compile(r"L[0-9]{7}")
_LIFELONG_ID_RE = re.compile(r"[1-9][0-9]{5,11}")
_DOT_DATE_RE = re.compile(r"(?<![0-9])(20[0-9]{2})[.]([0-9]{2})[.]([0-9]{2})(?![0-9])")
_DASH_DATE_RE = re.compile(r"(?<![0-9])(20[0-9]{2})-([0-9]{2})-([0-9]{2})(?![0-9])")
_DOT_DATETIME_RE = re.compile(
    r"(?<![0-9])(20[0-9]{2})[.]([0-9]{2})[.]([0-9]{2})\s+"
    r"([0-9]{2}):([0-9]{2})(?![0-9])"
)
_PHONE_RE = re.compile(
    r"(?<![0-9])(?:0[0-9]{1,2}[-.)\s]+[0-9]{3,4}[-.\s]+[0-9]{4}|"
    r"01[016789][-.\s]?[0-9]{3,4}[-.\s]?[0-9]{4})(?![0-9])"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<![0-9])[0-9]{6}\s*[- ]\s*[1-4][0-9]{6}(?![0-9])")
_TEST_TITLE_RE = re.compile(
    r"(?:기능|기늠)\s*(?:점검|정검).*(?:확인용|테스트)|"
    r"테스트\s*강좌|[xX]?테스트.*신청\s*[xX]|신청\s*금지",
    re.IGNORECASE,
)

_INTEGRATED_HEADERS = ("강좌명", "장소", "대상", "교육기간", "상태")
_LIFELONG_HEADERS = ("번호", "강좌정보", "신청/교육 기간", "상태")
_INTEGRATED_STATUS_MAP: Mapping[str, str] = {
    "예약준비중": "SCHEDULED",
    "예약하기": "OPEN",
    "대기자접수": "WAITING",
    "온라인완료": "CLOSED",
    "교육중": "CLOSED",
    "교육전": "CLOSED",
}
_INTEGRATED_CONTROL_STATUSES = frozenset({"예약준비중", "예약하기", "대기자접수"})
_LIFELONG_STATUS_MAP: Mapping[tuple[str, ...], str] = {
    ("접수완료", "교육 종료"): "CLOSED",
    ("접수완료", "교육중"): "CLOSED",
    ("접수완료", "접수완료"): "CLOSED",
    ("신청하기", "접수완료"): "OPEN",
    ("접수전", "교육전"): "SCHEDULED",
    ("접수전",): "SCHEDULED",
    ("2차신청 준비중", "접수완료"): "SCHEDULED",
    ("2차신청 준비중",): "SCHEDULED",
    ("교육중",): "CLOSED",
    ("교육 종료",): "CLOSED",
    ("폐강",): "CANCELLED",
}
_LIFELONG_DETAIL_STATUS_MAP: Mapping[tuple[str, ...], tuple[str, ...]] = {
    ("접수완료", "교육 종료"): ("접수완료", "교육 종료"),
    ("접수완료", "교육중"): ("접수완료", "교육중"),
    ("접수완료", "접수완료"): ("접수중",),
    ("신청하기", "접수완료"): ("접수중",),
    ("접수전", "교육전"): ("접수전", "교육전"),
    ("접수전",): ("접수전", "교육전"),
    ("2차신청 준비중", "접수완료"): ("2차신청 준비중",),
    ("교육중",): ("교육중",),
    ("교육 종료",): ("교육 종료",),
    ("폐강",): ("폐강",),
}
_INTEGRATED_DETAIL_LABELS = (
    "강좌명",
    "교육구분",
    "교육대상",
    "정원",
    "예약방법",
    "접수일자",
    "교육기간",
    "교육시간",
    "수강료",
    "강사",
    "문의전화",
    "담당자",
    "교육장소",
    "붙임문서 dt>",
)
_LIFELONG_DETAIL_CORE_LABELS = (
    "교육기관",
    "신청 기간 (인터넷접수)",
    "신청방법",
    "강좌분류",
    "교육 기간",
    "교육 요일",
    "교육 시간",
    "수강료",
    "재료비",
    "교육대상",
    "성별제한",
    "교육장소",
    "강사",
    "담당팀",
    "문의전화",
    "강의목표",
    "강좌개요",
    "강의교재",
    "강좌안내",
)
_LIFELONG_DETAIL_OPTIONAL_PRIORITY_LABEL = "우선 접수기간 (우선 대상자 접수)"
_LIFELONG_DETAIL_CAPACITY_LABELS = ("모집인원", "신청현황")
_LIFELONG_CAPACITY_RE = re.compile(
    r"^\s*([0-9][0-9,]*)명\s*/\s*([0-9][0-9,]*)명\s*$"
)
_LIFELONG_DETAIL_TOTAL_RE = re.compile(
    r"^\s*모집\s*:\s*([0-9][0-9,]*)명(?:\s|$)"
)
_LIFELONG_DETAIL_CURRENT_RE = re.compile(
    r"^\s*신청\s*:\s*([0-9][0-9,]*)명(?:\s|$)"
)
_LIFELONG_LIST_APPLICATION_RE = re.compile(
    r"request\(\s*['\"]([1-9][0-9]{5,11})['\"]\s*,"
)


class GyeongjuContractError(ValueError):
    """Raised whenever a source no longer satisfies the audited contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _strict_target_url(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GYEONGJU_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GYEONGJU_INTEGRATED_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def is_gyeongju_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")).upper() == GYEONGJU_PROVIDER
        and _strict_target_url(_target_value(target, "url"))
    )


is_target = is_gyeongju_education_target


def gyeongju_integrated_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    if page == 1:
        return GYEONGJU_INTEGRATED_URL
    return f"{GYEONGJU_INTEGRATED_URL}?{urlencode({'mem_id': '', 'pg': page})}"


def gyeongju_integrated_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _INTEGRATED_ID_RE.fullmatch(value):
        raise ValueError("invalid integrated Gyeongju course identity")
    return f"{GYEONGJU_INTEGRATED_URL}?" + urlencode(
        {"prc": "detail", "lec_id": value, "mem_id": "", "pg": ""}
    )


def gyeongju_integrated_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _INTEGRATED_ID_RE.fullmatch(value):
        raise ValueError("invalid integrated Gyeongju course identity")
    return f"{GYEONGJU_INTEGRATED_URL}?" + urlencode(
        {"prc": "rsvinfo", "lec_id": value, "mem_id": "", "pg": ""}
    )


def gyeongju_lifelong_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    if page == 1:
        return GYEONGJU_LIFELONG_URL
    return f"https://{GYEONGJU_HOST}{GYEONGJU_LIFELONG_LIST_PATH}?" + urlencode(
        {
            "menu_idx": "126",
            "viewPage": page,
            "rowCount": GYEONGJU_PAGE_SIZE,
            "program_type": "A2000",
        }
    )


def gyeongju_lifelong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _LIFELONG_ID_RE.fullmatch(value):
        raise ValueError("invalid lifelong Gyeongju course identity")
    return f"https://{GYEONGJU_HOST}{GYEONGJU_LIFELONG_DETAIL_PATH}?" + urlencode(
        {"lect_no": value, "menu_idx": "126"}
    )


def gyeongju_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return session


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _same_url(actual: str, expected: str) -> bool:
    left = urlparse(actual)
    right = urlparse(expected)
    try:
        left_port = left.port
        right_port = right.port
        left_query = parse_qs(left.query, keep_blank_values=True, strict_parsing=True)
        right_query = parse_qs(right.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    return bool(
        left.scheme == right.scheme == "https"
        and (left.hostname or "").rstrip(".").lower()
        == (right.hostname or "").rstrip(".").lower()
        == GYEONGJU_HOST
        and left_port == right_port is None
        and left.username is None
        and left.password is None
        and right.username is None
        and right.password is None
        and left.path == right.path
        and not left.params
        and not right.params
        and not left.fragment
        and not right.fragment
        and left_query == right_query
    )


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
) -> tuple[BeautifulSoup, int]:
    last_error: Optional[Exception] = None
    for attempt in range(GYEONGJU_FETCH_ATTEMPTS):
        try:
            response = fetcher(session, url, timeout)
            status = int(getattr(response, "status_code", 0) or 0)
            if status != 200:
                raise GyeongjuContractError(f"unexpected HTTP status {status}")
            if getattr(response, "history", None):
                raise GyeongjuContractError("HTTP redirects are not accepted")
            final_url = _clean(getattr(response, "url", ""))
            if final_url and not _same_url(final_url, url):
                raise GyeongjuContractError("response URL escaped the requested official route")
            content = getattr(response, "content", None)
            if content is None:
                text = getattr(response, "text", "")
                content = str(text).encode("utf-8")
            if not content:
                raise GyeongjuContractError("empty HTML response")
            if len(content) > GYEONGJU_MAX_HTML_BYTES:
                raise GyeongjuContractError("HTML response exceeds safety limit")
            return BeautifulSoup(content, "lxml"), attempt
        except Exception as exc:  # retry transport/HTTP failures, never publish a partial page
            last_error = exc
    assert last_error is not None
    raise last_error


def _dates(value: Any, pattern: re.Pattern[str]) -> tuple[date, ...]:
    result: list[date] = []
    for match in pattern.finditer(_clean(value)):
        result.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
    return tuple(result)


def _dot_datetimes(value: Any) -> tuple[datetime, ...]:
    result: list[datetime] = []
    for match in _DOT_DATETIME_RE.finditer(_clean(value)):
        result.append(
            datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5)),
            )
        )
    return tuple(result)


def _canonical_internal_href(
    href: Any,
    *,
    path: str,
    expected_query: Mapping[str, list[str]],
) -> str:
    absolute = urljoin(GYEONGJU_INTEGRATED_URL, _clean(href))
    parsed = urlparse(absolute)
    try:
        port = parsed.port
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError) as exc:
        raise GyeongjuContractError("malformed internal course URL") from exc
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GYEONGJU_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == path
        and not parsed.params
        and not parsed.fragment
        and query == dict(expected_query)
    ):
        raise GyeongjuContractError("course control escaped its identity-bound route")
    return absolute


def _declared_integrated_total(soup: BeautifulSoup) -> int:
    counter = soup.select("span.prdc_num")
    if len(counter) != 1:
        raise GyeongjuContractError("integrated declared-total marker changed")
    strong = counter[0].select("strong")
    if len(strong) != 1 or not _clean(strong[0].get_text()).replace(",", "").isdigit():
        raise GyeongjuContractError("integrated declared total is invalid")
    if _clean(counter[0].get_text(" ", strip=True)).split()[0] != "시설":
        raise GyeongjuContractError("integrated declared-total label changed")
    return int(_clean(strong[0].get_text()).replace(",", ""))


def _integrated_categories(soup: BeautifulSoup) -> Mapping[str, str]:
    anchors = soup.select("ul.choice_tab > li > a")
    if not anchors or _clean(anchors[0].get_text(" ", strip=True)) != "전체":
        raise GyeongjuContractError("integrated category selector changed")
    found = {
        _clean(anchor.get("id")): _clean(anchor.get_text(" ", strip=True))
        for anchor in anchors[1:]
    }
    if found != dict(GYEONGJU_CATEGORY_FILTERS):
        raise GyeongjuContractError("integrated category vocabulary changed")
    return found


def _integrated_navigation(soup: BeautifulSoup) -> Mapping[str, str]:
    found: dict[str, str] = {}
    for anchor in soup.select("div.snb_list li.snb_depth > a[href]"):
        parsed = urlparse(urljoin(GYEONGJU_INTEGRATED_URL, _clean(anchor.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path != GYEONGJU_INTEGRATED_PATH or set(query) != {"mem_id"}:
            raise GyeongjuContractError("integrated branch navigation route changed")
        member = _clean(query["mem_id"][0])
        if not re.fullmatch(r"B[0-9]{7}", member) or member in found:
            raise GyeongjuContractError("integrated branch navigation identity changed")
        found[member] = _clean(anchor.get_text(" ", strip=True))
    expected = {
        branch.member_id: branch.navigation_name
        for branch in (*GYEONGJU_BRANCHES, GYEONGJU_EMPTY_ACTIVE_BRANCH)
        if branch.member_id != "B0000027"
    }
    if found != expected:
        raise GyeongjuContractError("integrated active branch navigation changed")
    return found


def _branch_for_source_name(value: Any) -> GyeongjuBranch:
    name = _clean(value)
    matches = [branch for branch in GYEONGJU_BRANCHES if branch.source_name == name]
    if len(matches) != 1:
        raise GyeongjuContractError(f"unknown or ambiguous integrated branch {name!r}")
    return matches[0]


def _integrated_row(tr: Any, page: int) -> dict[str, Any]:
    cells = tr.find_all("td", recursive=False)
    if len(cells) != 5:
        raise GyeongjuContractError(f"integrated page {page}: row schema changed")
    detail = cells[0].select_one("a[href]")
    if detail is None:
        raise GyeongjuContractError(f"integrated page {page}: missing detail identity")
    href = _clean(detail.get("href"))
    parsed_href = urlparse(urljoin(GYEONGJU_INTEGRATED_URL, href))
    query = parse_qs(parsed_href.query, keep_blank_values=True)
    if set(query) != {"prc", "lec_id", "mem_id", "pg"} or query.get("prc") != ["detail"]:
        raise GyeongjuContractError(f"integrated page {page}: malformed detail route")
    identity = _clean(query.get("lec_id", [""])[0])
    if not _INTEGRATED_ID_RE.fullmatch(identity):
        raise GyeongjuContractError(f"integrated page {page}: invalid course identity")
    detail_url = _canonical_internal_href(
        href,
        path=GYEONGJU_INTEGRATED_PATH,
        expected_query={"prc": ["detail"], "lec_id": [identity], "mem_id": [""], "pg": [""]},
    )
    title = _clean(detail.get_text(" ", strip=True))
    branch = _clean(cells[1].get_text(" ", strip=True))
    branch_contract = _branch_for_source_name(branch)
    target = _clean(cells[2].get_text(" ", strip=True))
    period = _clean(cells[3].get_text(" ", strip=True))
    event_dates = _dates(period, _DOT_DATE_RE)
    if len(event_dates) != 2 or event_dates[1] < event_dates[0]:
        raise GyeongjuContractError(f"integrated course {identity}: invalid education period")
    source_status = _clean(cells[4].get_text(" ", strip=True))
    if source_status not in _INTEGRATED_STATUS_MAP:
        raise GyeongjuContractError(f"integrated course {identity}: unknown status {source_status!r}")
    control = cells[4].select_one("a[href]")
    application_url = ""
    if source_status in _INTEGRATED_CONTROL_STATUSES:
        if control is None or _clean(control.get_text(" ", strip=True)) != source_status:
            raise GyeongjuContractError(f"integrated course {identity}: application control missing")
        application_url = _canonical_internal_href(
            control.get("href"),
            path=GYEONGJU_INTEGRATED_PATH,
            expected_query={"prc": ["rsvinfo"], "lec_id": [identity], "mem_id": [""], "pg": [""]},
        )
    elif control is not None:
        raise GyeongjuContractError(f"integrated course {identity}: inactive status exposes control")
    return {
        "ledger": "integrated",
        "identity": identity,
        "title": title,
        "branch": branch,
        "member_id": branch_contract.member_id,
        "target": target,
        "period": period,
        "event_start": event_dates[0],
        "event_end": event_dates[1],
        "source_status": source_status,
        "status": _INTEGRATED_STATUS_MAP[source_status],
        "raw_url": detail_url,
        "application_url": application_url,
        "source_page": page,
    }


def _parse_integrated_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    page_title = soup.select_one("#page_title h3")
    if page_title is None or _clean(page_title.get_text(" ", strip=True)) != "강좌":
        raise GyeongjuContractError(f"integrated page {page}: page identity changed")
    table = soup.select_one("table.table_list")
    if table is None:
        raise GyeongjuContractError(f"integrated page {page}: course table missing")
    headers = tuple(_clean(cell.get_text(" ", strip=True)) for cell in table.select("thead th"))
    if headers != _INTEGRATED_HEADERS:
        raise GyeongjuContractError(f"integrated page {page}: table headers changed")
    total = _declared_integrated_total(soup)
    body_rows = table.select("tbody > tr")
    course_rows = [tr for tr in body_rows if tr.select_one("td.lecture01 a[href]") is not None]
    if not course_rows:
        if len(body_rows) != 1:
            raise GyeongjuContractError(f"integrated page {page}: invalid empty sentinel")
        cells = body_rows[0].find_all("td", recursive=False)
        if len(cells) != 1 or _clean(cells[0].get_text(" ", strip=True)) != "강좌 정보가 없습니다.":
            raise GyeongjuContractError(f"integrated page {page}: empty sentinel text changed")
        rows: tuple[dict[str, Any], ...] = ()
    else:
        if len(course_rows) != len(body_rows):
            raise GyeongjuContractError(f"integrated page {page}: mixed course/sentinel rows")
        rows = tuple(_integrated_row(tr, page) for tr in course_rows)
    active = soup.select_one("div.pgnate a.on")
    displayed_page = int(_clean(active.get_text())) if active and _clean(active.get_text()).isdigit() else None
    last = soup.select_one("div.pgnate a.p_last[href]")
    last_hint: Optional[int] = None
    if last is not None:
        query = parse_qs(urlparse(urljoin(GYEONGJU_INTEGRATED_URL, _clean(last.get("href")))).query)
        value = _clean(query.get("pg", [""])[0])
        if value.isdigit():
            last_hint = int(value)
    return {
        "page": page,
        "total": total,
        "displayed_page": displayed_page,
        "last_hint": last_hint,
        "rows": rows,
    }


def _integrated_signature(parsed: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        parsed.get("total"),
        parsed.get("displayed_page"),
        tuple(
            (
                row["identity"],
                row["title"],
                row["branch"],
                row["period"],
                row["source_status"],
                row["application_url"],
            )
            for row in parsed.get("rows", ())
        ),
    )


def _filter_total(soup: BeautifulSoup, *, category: Optional[str] = None, member: Optional[str] = None) -> int:
    total = _declared_integrated_total(soup)
    if category is not None:
        selected = soup.select("ul.choice_tab a.on")
        hidden = soup.select_one("form#frmFormSearch input[name=selItemKind]")
        if (
            len(selected) != 1
            or _clean(selected[0].get("id")) != category
            or _clean(selected[0].get_text(" ", strip=True)) != GYEONGJU_CATEGORY_FILTERS[category]
            or hidden is None
            or _clean(hidden.get("value")) != category
        ):
            raise GyeongjuContractError(f"integrated category filter {category}: binding changed")
    if member is not None:
        for anchor in soup.select("table.table_list tbody a[href*='lec_id=']"):
            query = parse_qs(urlparse(urljoin(GYEONGJU_INTEGRATED_URL, _clean(anchor.get("href")))).query, keep_blank_values=True)
            if query.get("mem_id") != [member]:
                raise GyeongjuContractError(f"integrated member filter {member}: row escaped partition")
    return total


def _lifelong_period_values(cell: Any, identity: str) -> dict[str, str]:
    periods: dict[str, str] = {}
    for paragraph in cell.select(":scope > p"):
        labels = paragraph.select(":scope > span.cate")
        if not labels:
            raise GyeongjuContractError(
                f"lifelong course {identity}: period label missing"
            )
        for label in labels:
            key = _clean(label.get_text(" ", strip=True))
            if not key or key in periods:
                raise GyeongjuContractError(
                    f"lifelong course {identity}: duplicate period label"
                )
            values: list[str] = []
            for sibling in label.next_siblings:
                sibling_name = _clean(getattr(sibling, "name", "")).lower()
                sibling_classes = {
                    _clean(item) for item in (sibling.get("class") or [])
                } if sibling_name else set()
                if sibling_name == "span" and "cate" in sibling_classes:
                    break
                if sibling_name == "br":
                    continue
                if sibling_name:
                    value = _clean(sibling.get_text(" ", strip=True))
                else:
                    value = _clean(str(sibling))
                if value:
                    values.append(value)
            periods[key] = _clean(" ".join(values))
    return periods


def _lifelong_row(tr: Any, page: int) -> dict[str, Any]:
    cells = tr.find_all("td", recursive=False)
    if len(cells) != 4:
        raise GyeongjuContractError(f"lifelong page {page}: row schema changed")
    sequence_text = _clean(cells[0].get_text(" ", strip=True)).replace(",", "")
    if not sequence_text.isdigit() or int(sequence_text) < 1:
        raise GyeongjuContractError(f"lifelong page {page}: invalid row sequence")
    anchor = cells[1].select_one("a.tit[onclick]")
    if anchor is None:
        raise GyeongjuContractError(f"lifelong page {page}: detail identity missing")
    match = re.fullmatch(r"\s*viewLecture\('([1-9][0-9]{5,11})'\)\s*", _clean(anchor.get("onclick")))
    if match is None:
        raise GyeongjuContractError(f"lifelong page {page}: detail onclick changed")
    identity = match.group(1)
    title_value = _clean(anchor.get_text(" ", strip=True))
    functional_test_record = bool(_TEST_TITLE_RE.search(title_value))
    info: dict[str, str] = {}
    for item in cells[1].select("ul.info_util > li"):
        label = item.select_one("span.tit")
        value = item.select_one("span.cont")
        if label is None or value is None:
            raise GyeongjuContractError(f"lifelong course {identity}: list info schema changed")
        key = _clean(label.get_text(" ", strip=True))
        if key in info:
            raise GyeongjuContractError(f"lifelong course {identity}: duplicate list label")
        info[key] = _clean(value.get_text(" ", strip=True))
    legacy_info_labels = (
        "교육기관",
        "교육 요일",
        "교육 시간",
        "수강료",
        "접수방법",
    )
    capacity_info_labels = (
        "교육기관",
        "교육 요일",
        "교육 시간",
        "수강료",
        "신청 / 모집",
        "접수방법",
    )
    if tuple(info) not in {legacy_info_labels, capacity_info_labels}:
        raise GyeongjuContractError(f"lifelong course {identity}: list labels changed")
    capacity_current: Optional[int] = None
    capacity_total: Optional[int] = None
    if "신청 / 모집" in info:
        capacity_match = _LIFELONG_CAPACITY_RE.fullmatch(info["신청 / 모집"])
        if capacity_match is None:
            raise GyeongjuContractError(
                f"lifelong course {identity}: list capacity changed"
            )
        capacity_current, capacity_total = (
            int(value.replace(",", "")) for value in capacity_match.groups()
        )
        if capacity_total < 1 or capacity_current > capacity_total:
            raise GyeongjuContractError(
                f"lifelong course {identity}: list capacity is impossible"
            )
    periods = _lifelong_period_values(cells[2], identity)
    allowed_period_labels = {
        ("신청기간", "교육기간"),
        ("신청기간", "우선접수", "교육기간"),
        ("신청기간", "1차 접수", "교육기간"),
        ("신청기간", "1차 접수", "우선접수", "교육기간"),
        ("신청기간", "2차 접수", "교육기간"),
    }
    functional_period_labels = {
        "신청기간",
        "1차 접수",
        "2차 접수",
        "3차 접수",
        "우선접수",
        "방문접수",
        "교육기간",
    }
    functional_period_contract = bool(
        functional_test_record
        and tuple(periods)[:1] == ("신청기간",)
        and tuple(periods)[-1:] == ("교육기간",)
        and set(periods) <= functional_period_labels
    )
    if tuple(periods) not in allowed_period_labels and not functional_period_contract:
        raise GyeongjuContractError(f"lifelong course {identity}: period schema changed")
    apply_values = _dot_datetimes(periods["신청기간"].replace("-", "."))
    if len(apply_values) != 2 or apply_values[1] < apply_values[0]:
        raise GyeongjuContractError(f"lifelong course {identity}: invalid application period")
    first_phase = (
        _dot_datetimes(periods["1차 접수"].replace("-", "."))
        if "1차 접수" in periods
        else ()
    )
    if not functional_test_record and "1차 접수" in periods:
        if (
            len(first_phase) != 2
            or first_phase[1] < first_phase[0]
            or first_phase[0] < apply_values[0]
            or first_phase[1] > apply_values[1]
        ):
            raise GyeongjuContractError(
                f"lifelong course {identity}: invalid first application phase"
            )
    second_phase = (
        _dot_datetimes(periods["2차 접수"].replace("-", "."))
        if "2차 접수" in periods
        else ()
    )
    if not functional_test_record and "2차 접수" in periods:
        if (
            len(second_phase) != 2
            or second_phase[1] < second_phase[0]
            or second_phase[0] < apply_values[0]
            or second_phase[1] > apply_values[1]
        ):
            raise GyeongjuContractError(
                f"lifelong course {identity}: invalid second application phase"
            )
    third_phase = (
        _dot_datetimes(periods["3차 접수"].replace("-", "."))
        if "3차 접수" in periods
        else ()
    )
    visit_phase = (
        _dot_datetimes(periods["방문접수"].replace("-", "."))
        if "방문접수" in periods
        else ()
    )
    priority_dates = (
        _dates(periods["우선접수"], _DASH_DATE_RE)
        if "우선접수" in periods
        else ()
    )
    if not functional_test_record and "우선접수" in periods:
        if (
            len(priority_dates) != 2
            or priority_dates[1] < priority_dates[0]
            or priority_dates[0] < apply_values[0].date()
            or priority_dates[1] > apply_values[1].date()
        ):
            raise GyeongjuContractError(
                f"lifelong course {identity}: invalid priority application phase"
            )
    event_dates = _dates(periods["교육기간"], _DASH_DATE_RE)
    if len(event_dates) != 2 or event_dates[1] < event_dates[0]:
        raise GyeongjuContractError(f"lifelong course {identity}: invalid education period")
    status_tokens = tuple(_clean(item.get_text(" ", strip=True)) for item in cells[3].select("span.attend"))
    normalized_status = _LIFELONG_STATUS_MAP.get(status_tokens)
    if (
        normalized_status is None
        and functional_test_record
        and len(status_tokens) == 1
        and re.fullmatch(r"[1-9][0-9]*차신청 준비중", status_tokens[0])
    ):
        normalized_status = "SCHEDULED"
    if (
        normalized_status is None
        and functional_test_record
        and status_tokens == ("신청하기",)
    ):
        normalized_status = "OPEN"
    if normalized_status is None:
        raise GyeongjuContractError(f"lifelong course {identity}: unknown status {status_tokens!r}")
    list_controls = [
        node
        for node in cells[3].select("[onclick]")
        if _clean(node.get("onclick"))
    ]
    if normalized_status == "OPEN":
        control_match = (
            _LIFELONG_LIST_APPLICATION_RE.search(
                _clean(list_controls[0].get("onclick"))
            )
            if len(list_controls) == 1
            else None
        )
        if (
            control_match is None
            or control_match.group(1) != identity
            or _clean(list_controls[0].get_text(" ", strip=True)) != "신청하기"
        ):
            raise GyeongjuContractError(
                f"lifelong course {identity}: list application control changed"
            )
    elif list_controls:
        raise GyeongjuContractError(
            f"lifelong course {identity}: inactive row exposes onclick control"
        )
    if cells[3].select_one("a[href],button:not([disabled])") is not None:
        raise GyeongjuContractError(f"lifelong course {identity}: audited inactive row exposes control")
    return {
        "ledger": "lifelong",
        "sequence": int(sequence_text),
        "identity": identity,
        "title": title_value,
        "institution": info["교육기관"],
        "weekday": info["교육 요일"],
        "schedule": info["교육 시간"],
        "fee": info["수강료"],
        "application_method": info["접수방법"],
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "apply_period": periods["신청기간"],
        "apply_start": apply_values[0],
        "apply_end": apply_values[1],
        "period": periods["교육기간"],
        "priority_apply_period": periods.get("우선접수", ""),
        "first_apply_period": periods.get("1차 접수", ""),
        "second_apply_period": periods.get("2차 접수", ""),
        "third_apply_period": periods.get("3차 접수", ""),
        "visit_apply_period": periods.get("방문접수", ""),
        "priority_apply_start": priority_dates[0] if len(priority_dates) == 2 else None,
        "priority_apply_end": priority_dates[1] if len(priority_dates) == 2 else None,
        "first_apply_start": first_phase[0] if len(first_phase) == 2 else None,
        "first_apply_end": first_phase[1] if len(first_phase) == 2 else None,
        "second_apply_start": second_phase[0] if len(second_phase) == 2 else None,
        "second_apply_end": second_phase[1] if len(second_phase) == 2 else None,
        "third_apply_start": third_phase[0] if len(third_phase) == 2 else None,
        "third_apply_end": third_phase[1] if len(third_phase) == 2 else None,
        "visit_apply_start": visit_phase[0] if len(visit_phase) == 2 else None,
        "visit_apply_end": visit_phase[1] if len(visit_phase) == 2 else None,
        "event_start": event_dates[0],
        "event_end": event_dates[1],
        "source_status_tokens": status_tokens,
        "source_status": "|".join(status_tokens),
        "status": normalized_status,
        "raw_url": gyeongju_lifelong_detail_url(identity),
        "application_url": (
            gyeongju_lifelong_detail_url(identity)
            if normalized_status == "OPEN"
            else ""
        ),
        "functional_test_record": functional_test_record,
        "source_page": page,
    }


def _parse_lifelong_page(soup: BeautifulSoup, page: int) -> dict[str, Any]:
    title = soup.select_one("#page_tit_id")
    if title is None or _clean(title.get_text(" ", strip=True)) != "평생학습 강좌":
        raise GyeongjuContractError(f"lifelong page {page}: page identity changed")
    form = soup.select_one("form#lectureManagement")
    table = soup.select_one("table.apply_list_tbl")
    if form is None or table is None:
        raise GyeongjuContractError(f"lifelong page {page}: form/table missing")
    hidden = {
        _clean(item.get("name")): _clean(item.get("value"))
        for item in form.select("input[name]")
        if _clean(item.get("name")) in {"menu_idx", "rowCount", "program_type", "viewPage"}
    }
    if hidden.get("menu_idx") != "126" or hidden.get("rowCount") != str(GYEONGJU_PAGE_SIZE) or hidden.get("program_type") != "A2000":
        raise GyeongjuContractError(f"lifelong page {page}: owner filter binding changed")
    headers = tuple(_clean(cell.get_text(" ", strip=True)) for cell in table.select("thead th"))
    if headers != _LIFELONG_HEADERS:
        raise GyeongjuContractError(f"lifelong page {page}: table headers changed")
    rows = tuple(_lifelong_row(tr, page) for tr in table.select("tbody > tr"))
    scripts = " ".join(script.get_text(" ", strip=True) for script in soup.select("#cms_paging ~ script, #cms_paging + script"))
    match = re.search(r"totalPageCount\s*=\s*'([0-9]+)'", scripts)
    if match is None:
        match = re.search(r"totalPageCount\s*=\s*'([0-9]+)'", str(soup))
    if match is None:
        raise GyeongjuContractError(f"lifelong page {page}: advertised last page missing")
    advertised_last = int(match.group(1))
    active = soup.select_one("#cms_paging a.active")
    displayed = int(_clean(active.get_text())) if active and _clean(active.get_text()).isdigit() else None
    return {
        "page": page,
        "advertised_last": advertised_last,
        "displayed_page": displayed,
        "rows": rows,
    }


def _lifelong_signature(parsed: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        parsed.get("advertised_last"),
        parsed.get("displayed_page"),
        tuple(
            (
                row["sequence"],
                row["identity"],
                row["title"],
                row["period"],
                row["apply_period"],
                row["first_apply_period"],
                row["second_apply_period"],
                row["priority_apply_period"],
                row["capacity_current"],
                row["capacity_total"],
                row["source_status"],
                row["application_url"],
                row["functional_test_record"],
            )
            for row in parsed.get("rows", ())
        ),
    )


def _detail_pairs(container: Any) -> tuple[tuple[str, str], ...]:
    tags = container.find_all(["dt", "dd"], recursive=False)
    if not tags or len(tags) % 2:
        raise GyeongjuContractError("detail definition list changed")
    pairs: list[tuple[str, str]] = []
    for index in range(0, len(tags), 2):
        if tags[index].name != "dt" or tags[index + 1].name != "dd":
            raise GyeongjuContractError("detail definition pairing changed")
        pairs.append(
            (
                _clean(tags[index].get_text(" ", strip=True)),
                _clean(tags[index + 1].get_text(" ", strip=True)),
            )
        )
    return tuple(pairs)


def _capacity(value: Any) -> tuple[int, Optional[int]]:
    text = _clean(value).replace(",", "")
    values = [int(item) for item in re.findall(r"[0-9]+", text)]
    if not values or values[0] < 1:
        raise GyeongjuContractError("invalid course capacity")
    remaining = values[1] if len(values) > 1 else None
    if remaining is not None and not 0 <= remaining <= values[0]:
        raise GyeongjuContractError("invalid remaining capacity")
    return values[0], remaining


def _safe_row(row: Mapping[str, Any], discarded: Iterable[str]) -> None:
    forbidden_keys = {"phone", "email", "contact", "instructor", "teacher", "manager", "applicant"}
    if forbidden_keys.intersection(row):
        raise GyeongjuContractError("private field key escaped output allowlist")
    serialized = repr(row)
    if _PHONE_RE.search(serialized) or _EMAIL_RE.search(serialized) or _RESIDENT_ID_RE.search(serialized):
        raise GyeongjuContractError("private contact/identity value escaped output allowlist")
    for value in discarded:
        clean = _clean(value)
        if len(clean) >= 2 and clean not in {"강사미정", "미정", "-"} and clean in serialized:
            raise GyeongjuContractError("discarded personal detail escaped output allowlist")


def _integrated_detail_row(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    cutoff: date,
) -> tuple[dict[str, Any], str]:
    identity = _clean(listed["identity"])
    detail_list = soup.select_one("dl.lecture_dl01")
    if detail_list is None:
        raise GyeongjuContractError(f"integrated course {identity}: detail list missing")
    pairs_tuple = _detail_pairs(detail_list)
    if tuple(key for key, _ in pairs_tuple) != _INTEGRATED_DETAIL_LABELS:
        raise GyeongjuContractError(f"integrated course {identity}: detail labels changed")
    pairs = dict(pairs_tuple)
    comparisons = {
        "강좌명": listed["title"],
        "교육대상": listed["target"],
    }
    for label, expected in comparisons.items():
        if _normalized(pairs[label]) != _normalized(expected):
            raise GyeongjuContractError(f"integrated course {identity}: list/detail {label} mismatch")
    detail_dates = _dates(pairs["교육기간"], _DOT_DATE_RE)
    if detail_dates != (listed["event_start"], listed["event_end"]):
        raise GyeongjuContractError(f"integrated course {identity}: list/detail education period mismatch")
    apply_values = _dot_datetimes(pairs["접수일자"])
    if len(apply_values) != 2 or apply_values[1] < apply_values[0]:
        raise GyeongjuContractError(f"integrated course {identity}: invalid detail application period")
    status = _clean(listed["status"])
    source_status = _clean(listed["source_status"])
    if status == "SCHEDULED" and not cutoff <= apply_values[0].date():
        raise GyeongjuContractError(f"integrated course {identity}: scheduled status contradicts application date")
    if status in {"OPEN", "WAITING"} and not apply_values[0].date() <= cutoff <= apply_values[1].date():
        raise GyeongjuContractError(f"integrated course {identity}: active status contradicts application date")
    if source_status == "교육중" and not listed["event_start"] <= cutoff <= listed["event_end"]:
        raise GyeongjuContractError(f"integrated course {identity}: 교육중 contradicts education period")
    if source_status == "교육전" and not cutoff < listed["event_start"]:
        raise GyeongjuContractError(f"integrated course {identity}: 교육전 contradicts education period")
    notice = soup.select_one("p.lecture_notice")
    notice_text = _clean(notice.get_text(" ", strip=True)) if notice is not None else ""
    expected_notice = {
        "예약준비중": "예약 준비중",
        "예약하기": "",
        "대기자접수": "",
        "온라인완료": "온라인 예약정원이 모두예약",
        "교육중": "현재 교육중",
        "교육전": "현재 교육전",
    }[source_status]
    if expected_notice and expected_notice not in notice_text:
        raise GyeongjuContractError(f"integrated course {identity}: detail status sentinel changed")
    if not expected_notice and notice_text:
        raise GyeongjuContractError(f"integrated course {identity}: active detail unexpectedly has notice")
    capacity, remaining = _capacity(pairs["정원"])
    branch = _branch_for_source_name(listed["branch"])
    venue = _clean(pairs["교육장소"])
    if not venue or not _normalized(venue).startswith(_normalized(branch.source_name)):
        raise GyeongjuContractError(f"integrated course {identity}: venue escaped official branch")
    category = _clean(pairs["교육구분"])
    if category not in set(GYEONGJU_CATEGORY_FILTERS.values()):
        raise GyeongjuContractError(f"integrated course {identity}: unknown category")
    application_url = _clean(listed["application_url"])
    if status == "OPEN":
        application_type = "ONLINE_RESERVATION_LOGIN_REQUIRED"
    elif status == "WAITING":
        application_type = "ONLINE_WAITLIST_LOGIN_REQUIRED"
    elif status == "SCHEDULED":
        application_type = "ONLINE_RESERVATION_SCHEDULED"
    else:
        application_type = "INFO_ONLY"
    if status == "CLOSED" and application_url:
        raise GyeongjuContractError(f"integrated course {identity}: closed row retained application URL")
    fee = _clean(pairs["수강료"]) or "요금 별도 안내"
    schedule = _clean(pairs["교육시간"]) or "시간 별도 안내"
    description = " | ".join(
        part for part in (category, _clean(pairs["예약방법"]), fee) if part
    )
    row: dict[str, Any] = {
        "provider": GYEONGJU_PROVIDER,
        "provider_course_id": f"{GYEONGJU_PROVIDER}:reserve:{identity}",
        "title": _clean(listed["title"]),
        "branch": branch.source_name,
        "organizer": branch.source_name,
        "provider_organizer": branch.source_name,
        "branch_code": f"{GYEONGJU_PROVIDER}:reserve:{branch.member_id}",
        "preserve_branch": True,
        "branch_url": f"{GYEONGJU_INTEGRATED_URL}?{urlencode({'mem_id': branch.member_id})}",
        "raw_url": _clean(listed["raw_url"]),
        "application_url": application_url,
        "application_type": application_type,
        "application_method_raw": _clean(pairs["예약방법"]),
        "reservation_available": bool(application_url and status in {"OPEN", "WAITING"}),
        "status": status,
        "raw_status": source_status,
        "category": category,
        "fee": fee,
        "period": f"{listed['event_start'].isoformat()} ~ {listed['event_end'].isoformat()}",
        "apply_period": (
            f"{apply_values[0].strftime('%Y-%m-%d %H:%M')} ~ "
            f"{apply_values[1].strftime('%Y-%m-%d %H:%M')}"
        ),
        "schedule_raw": schedule,
        "start_date": listed["event_start"].isoformat(),
        "end_date": listed["event_end"].isoformat(),
        "apply_start_date": apply_values[0].date().isoformat(),
        "apply_end_date": apply_values[1].date().isoformat(),
        "target": _clean(listed["target"]),
        "capacity": capacity,
        "capacity_total": capacity,
        "capacity_remaining": remaining,
        "venue_name": venue,
        "room": venue,
        "address": branch.address,
        "venue_address": branch.address,
        "description": description,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "static_html+detail_html",
        "program_type": "교육",
        "municipality_code": GYEONGJU_MUNICIPALITY_CODE,
        "municipality_name": GYEONGJU_MUNICIPALITY_NAME,
        "municipality_full_name": GYEONGJU_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": GYEONGJU_PARSER,
            "ledger": "integrated_reservation_lecture",
            "identity": identity,
            "source_page": int(listed["source_page"]),
            "source_member_id": branch.member_id,
            "source_category": category,
            "source_status": source_status,
            "source_fee_missing": not bool(_clean(pairs["수강료"])),
            "source_schedule_missing": not bool(_clean(pairs["교육시간"])),
            "detail_verified": True,
            "application_control_verified": bool(application_url),
            "application_endpoint_fetched": False,
            "instructor_discarded": True,
            "manager_discarded": True,
            "contact_discarded": True,
            "attachments_discarded": True,
            "free_text_discarded": True,
        },
    }
    _safe_row(row, (pairs["강사"], pairs["문의전화"], pairs["담당자"]))
    exclusion = "functional_test_record" if _TEST_TITLE_RE.search(row["title"]) else ""
    return row, exclusion


def _lifelong_detail_values(soup: BeautifulSoup) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for item in soup.select("#apply_bbs .view_util_box ul.info_util > li"):
        label = item.select_one(":scope > span.tit")
        value = item.select_one(":scope > span.cont")
        if label is not None and value is not None:
            pairs.append(
                (
                    _clean(label.get_text(" ", strip=True)),
                    _clean(value.get_text(" ", strip=True)),
                )
            )
    return tuple(pairs)


def _lifelong_detail_row(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
) -> tuple[dict[str, Any], str]:
    identity = _clean(listed["identity"])
    form = soup.select_one("form#lectureOne")
    title_node = soup.select_one("#apply_bbs .view_tit_box p.tit")
    if form is None or title_node is None:
        raise GyeongjuContractError(f"lifelong course {identity}: detail identity missing")
    identity_input = form.select_one("input[name=lect_no]")
    type_input = form.select_one("input[name=program_type]")
    if (
        identity_input is None
        or _clean(identity_input.get("value")) != identity
        or type_input is None
        or _clean(type_input.get("value")) != "A2000"
    ):
        raise GyeongjuContractError(f"lifelong course {identity}: detail binding changed")
    if _normalized(title_node.get_text(" ", strip=True)) != _normalized(listed["title"]):
        raise GyeongjuContractError(f"lifelong course {identity}: list/detail title mismatch")
    detail_status = tuple(
        _clean(item.get_text(" ", strip=True))
        for item in soup.select("#apply_bbs .view_tit_box span.attend")
    )
    source_status_tokens = tuple(listed["source_status_tokens"])
    expected_detail_status = _LIFELONG_DETAIL_STATUS_MAP.get(source_status_tokens)
    if expected_detail_status is None or detail_status != expected_detail_status:
        raise GyeongjuContractError(f"lifelong course {identity}: list/detail status mismatch")

    detail_pairs = list(_lifelong_detail_values(soup))
    attachment_value = ""
    if detail_pairs and detail_pairs[-1][0] == "첨부파일":
        attachment_value = detail_pairs.pop()[1]
    labels = tuple(key for key, _ in detail_pairs)
    if len(labels) != len(set(labels)) or labels[:2] != _LIFELONG_DETAIL_CORE_LABELS[:2]:
        raise GyeongjuContractError(f"lifelong course {identity}: detail labels changed")
    cursor = 2
    detail_priority_value = ""
    if (
        cursor < len(detail_pairs)
        and detail_pairs[cursor][0] == _LIFELONG_DETAIL_OPTIONAL_PRIORITY_LABEL
    ):
        detail_priority_value = detail_pairs[cursor][1]
        cursor += 1
    detail_has_capacity = (
        tuple(label for label, _ in detail_pairs[cursor : cursor + 2])
        == _LIFELONG_DETAIL_CAPACITY_LABELS
    )
    if detail_has_capacity:
        cursor += 2
    if tuple(label for label, _ in detail_pairs[cursor:]) != _LIFELONG_DETAIL_CORE_LABELS[2:]:
        raise GyeongjuContractError(f"lifelong course {identity}: detail labels changed")

    list_has_priority = bool(_clean(listed.get("priority_apply_period")))
    if bool(detail_priority_value) != list_has_priority:
        raise GyeongjuContractError(
            f"lifelong course {identity}: priority application labels mismatch"
        )
    list_has_capacity = (
        listed.get("capacity_current") is not None
        and listed.get("capacity_total") is not None
    )
    if detail_has_capacity != list_has_capacity:
        raise GyeongjuContractError(
            f"lifelong course {identity}: capacity labels mismatch"
        )

    pairs = dict(detail_pairs)
    comparisons = {
        "교육기관": listed["institution"],
        "신청방법": listed["application_method"],
        "교육 요일": listed["weekday"],
        "교육 시간": listed["schedule"],
        "수강료": listed["fee"],
    }
    for label, expected in comparisons.items():
        if _normalized(pairs[label]) != _normalized(expected):
            raise GyeongjuContractError(f"lifelong course {identity}: list/detail {label} mismatch")
    detail_apply_values = _dot_datetimes(
        pairs["신청 기간 (인터넷접수)"].replace("-", ".")
    )
    expected_detail_start = (
        listed.get("second_apply_start")
        if _clean(listed.get("second_apply_period"))
        else listed["apply_start"]
    )
    if (
        len(detail_apply_values) != 3
        or detail_apply_values[0] != expected_detail_start
        or detail_apply_values[2] != detail_apply_values[0]
        or detail_apply_values[1] < detail_apply_values[0]
        or detail_apply_values[1] > listed["apply_end"]
    ):
        raise GyeongjuContractError(
            f"lifelong course {identity}: list/detail 신청 기간 (인터넷접수) mismatch"
        )
    detail_apply_start, detail_apply_end = detail_apply_values[:2]
    if _clean(listed.get("first_apply_period")):
        if (
            detail_apply_start != listed.get("first_apply_start")
            or detail_apply_end != listed.get("first_apply_end")
        ):
            raise GyeongjuContractError(
                f"lifelong course {identity}: first application phase mismatch"
            )
    elif _clean(listed.get("second_apply_period")):
        if (
            detail_apply_start != listed.get("second_apply_start")
            or detail_apply_end != listed.get("second_apply_end")
        ):
            raise GyeongjuContractError(
                f"lifelong course {identity}: second application phase mismatch"
            )
    elif list_has_priority:
        if (
            detail_apply_start.date() != listed.get("priority_apply_start")
            or detail_apply_end.date() != listed.get("priority_apply_end")
        ):
            raise GyeongjuContractError(
                f"lifelong course {identity}: priority application phase mismatch"
            )
    elif detail_apply_end != listed["apply_end"]:
        raise GyeongjuContractError(
            f"lifelong course {identity}: overall application phase mismatch"
        )

    if list_has_priority:
        detail_priority_dates = _dates(detail_priority_value, _DASH_DATE_RE)
        if (
            len(detail_priority_dates) != 3
            or detail_priority_dates[0] != listed.get("priority_apply_start")
            or detail_priority_dates[1] != listed.get("priority_apply_end")
            or detail_priority_dates[2] != detail_priority_dates[0]
        ):
            raise GyeongjuContractError(
                f"lifelong course {identity}: priority application detail mismatch"
            )

    if list_has_capacity:
        detail_total_match = _LIFELONG_DETAIL_TOTAL_RE.search(pairs["모집인원"])
        detail_current_match = _LIFELONG_DETAIL_CURRENT_RE.search(pairs["신청현황"])
        if (
            detail_total_match is None
            or detail_current_match is None
            or int(detail_total_match.group(1).replace(",", ""))
            != int(listed["capacity_total"])
            or int(detail_current_match.group(1).replace(",", ""))
            != int(listed["capacity_current"])
        ):
            raise GyeongjuContractError(
                f"lifelong course {identity}: list/detail capacity mismatch"
            )

    detail_event_dates = _dates(pairs["교육 기간"], _DASH_DATE_RE)
    if (
        not pairs["교육 기간"].startswith(_clean(listed["period"]))
        or detail_event_dates != (listed["event_start"], listed["event_end"])
    ):
        raise GyeongjuContractError(
            f"lifelong course {identity}: list/detail 교육 기간 mismatch"
        )
    branch = _clean(pairs["교육기관"])
    if branch != GYEONGJU_LIFELONG_BRANCH:
        raise GyeongjuContractError(f"lifelong course {identity}: official branch changed")
    venue = re.sub(r"\s*지도보기\s*$", "", _clean(pairs["교육장소"]))
    if not venue:
        raise GyeongjuContractError(f"lifelong course {identity}: education venue missing")

    application_controls = soup.select("#apply_bbs .top_area .apply_btn")
    normalized_status = _clean(listed["status"])
    if normalized_status == "SCHEDULED":
        second_phase_scheduled = source_status_tokens == (
            "2차신청 준비중",
            "접수완료",
        )
        if second_phase_scheduled:
            if (
                len(application_controls) != 1
                or application_controls[0].name != "a"
                or _clean(application_controls[0].get_text(" ", strip=True))
                != "2차신청 준비중"
                or _clean(application_controls[0].get("href"))
                != "javascript:void(0);"
                or _clean(application_controls[0].get("onclick"))
            ):
                raise GyeongjuContractError(
                    f"lifelong course {identity}: scheduled second-phase control changed"
                )
        elif application_controls:
            raise GyeongjuContractError(
                f"lifelong course {identity}: scheduled row exposes application control"
            )
    else:
        expected_control = {
            "OPEN": "신청하기",
            "CLOSED": "수강신청 마감",
            "CANCELLED": "폐강",
        }.get(normalized_status)
        if (
            expected_control is None
            or len(application_controls) != 1
            or _clean(application_controls[0].get_text(" ", strip=True))
            != expected_control
            or application_controls[0].name != "a"
            or _clean(application_controls[0].get("href")) != "javascript:void(0);"
        ):
            raise GyeongjuContractError(
                f"lifelong course {identity}: application control changed"
            )
        onclick = _clean(application_controls[0].get("onclick"))
        if normalized_status == "OPEN":
            control_match = _LIFELONG_LIST_APPLICATION_RE.search(onclick)
            if control_match is None or control_match.group(1) != identity:
                raise GyeongjuContractError(
                    f"lifelong course {identity}: detail application identity changed"
                )
        elif onclick:
            raise GyeongjuContractError(
                f"lifelong course {identity}: inactive control became actionable"
            )
    description = " | ".join(
        part
        for part in (
            _clean(pairs["강좌분류"]),
            _clean(pairs["신청방법"]),
            _clean(pairs["수강료"]),
            _clean(pairs["재료비"]),
        )
        if part
    )
    row: dict[str, Any] = {
        "provider": GYEONGJU_PROVIDER,
        "provider_course_id": f"{GYEONGJU_PROVIDER}:lifelong:{identity}",
        "title": _clean(listed["title"]),
        "branch": branch,
        "organizer": branch,
        "provider_organizer": branch,
        "branch_code": f"{GYEONGJU_PROVIDER}:lifelong:A2000",
        "preserve_branch": True,
        "branch_url": GYEONGJU_LIFELONG_URL,
        "raw_url": _clean(listed["raw_url"]),
        "application_url": (
            _clean(listed["raw_url"]) if normalized_status == "OPEN" else ""
        ),
        "application_type": (
            "ONLINE_RESERVATION" if normalized_status == "OPEN" else "INFO_ONLY"
        ),
        "application_method_raw": _clean(listed["application_method"]),
        "reservation_available": normalized_status == "OPEN",
        "status": normalized_status,
        "raw_status": _clean(listed["source_status"]),
        "category": _clean(pairs["강좌분류"]) or "평생학습",
        "fee": _clean(pairs["수강료"]) or "요금 별도 안내",
        "material_fee": _clean(pairs["재료비"]),
        "period": f"{listed['event_start'].isoformat()} ~ {listed['event_end'].isoformat()}",
        "apply_period": (
            f"{listed['apply_start'].strftime('%Y-%m-%d %H:%M')} ~ "
            f"{listed['apply_end'].strftime('%Y-%m-%d %H:%M')}"
        ),
        "schedule_raw": (
            " ".join(
                part
                for part in (
                    _clean(listed["weekday"]),
                    _clean(listed["schedule"]),
                )
                if part
            )
            or "시간 별도 안내"
        ),
        "start_date": listed["event_start"].isoformat(),
        "end_date": listed["event_end"].isoformat(),
        "apply_start_date": listed["apply_start"].date().isoformat(),
        "apply_end_date": listed["apply_end"].date().isoformat(),
        "target": _clean(pairs["교육대상"]),
        "venue_name": venue,
        "room": venue,
        "address": GYEONGJU_LIFELONG_ADDRESS,
        "venue_address": GYEONGJU_LIFELONG_ADDRESS,
        "description": description,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "static_html+detail_html",
        "program_type": "교육",
        "municipality_code": GYEONGJU_MUNICIPALITY_CODE,
        "municipality_name": GYEONGJU_MUNICIPALITY_NAME,
        "municipality_full_name": GYEONGJU_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": GYEONGJU_PARSER,
            "ledger": "lifelong_regular_A2000",
            "identity": identity,
            "source_page": int(listed["source_page"]),
            "source_status": _clean(listed["source_status"]),
            "source_category": _clean(pairs["강좌분류"]),
            "source_fee_missing": not bool(_clean(pairs["수강료"])),
            "source_schedule_missing": not bool(
                _clean(listed["weekday"]) or _clean(listed["schedule"])
            ),
            "source_capacity_current": listed.get("capacity_current"),
            "source_capacity_total": listed.get("capacity_total"),
            "source_first_apply_period": _clean(listed.get("first_apply_period")),
            "source_second_apply_period": _clean(
                listed.get("second_apply_period")
            ),
            "source_priority_apply_period": _clean(
                listed.get("priority_apply_period")
            ),
            "detail_verified": True,
            "application_control_verified": True,
            "application_endpoint_fetched": False,
            "instructor_discarded": True,
            "team_discarded": True,
            "contact_discarded": True,
            "attachments_discarded": True,
            "free_text_discarded": True,
        },
    }
    if list_has_capacity:
        row.update(
            {
                "capacity": int(listed["capacity_total"]),
                "capacity_current": int(listed["capacity_current"]),
                "capacity_total": int(listed["capacity_total"]),
            }
        )
    _safe_row(row, (pairs["강사"], pairs["문의전화"], attachment_value))
    exclusion = "cancelled_course" if listed["status"] == "CANCELLED" else ""
    return row, exclusion


def _empty_adjunct_page(
    soup: BeautifulSoup,
    *,
    title: str,
    menu_idx: str,
    special: bool,
) -> tuple[Any, ...]:
    page_title = soup.select_one("#page_tit_id")
    form = soup.select_one("form#lectureManagement")
    table = soup.select_one("table.apply_list_tbl")
    if (
        page_title is None
        or _clean(page_title.get_text(" ", strip=True)) != title
        or form is None
        or table is None
    ):
        raise GyeongjuContractError(f"adjunct {menu_idx}: page identity changed")
    headers = tuple(_clean(cell.get_text(" ", strip=True)) for cell in table.select("thead th"))
    if headers != _LIFELONG_HEADERS or table.select("tbody > tr"):
        raise GyeongjuContractError(f"adjunct {menu_idx}: expected audited empty ledger changed")
    menu = form.select_one("input[name=menu_idx]")
    view_page = form.select_one("input[name=viewPage]")
    if menu is None or _clean(menu.get("value")) != menu_idx or view_page is None:
        raise GyeongjuContractError(f"adjunct {menu_idx}: form binding changed")
    if special:
        program = form.select_one("input[name=program_type]")
        if (
            program is None
            or _clean(program.get("value")) != "A2005"
        ):
            raise GyeongjuContractError("special-program empty sentinel changed")
    active = soup.select_one("#cms_paging a.active")
    return (
        _clean(page_title.get_text(" ", strip=True)),
        _clean(active.get_text(" ", strip=True)) if active else "",
        headers,
    )


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _semantic_duplicate_count(rows: Iterable[Mapping[str, Any]]) -> int:
    grouped: dict[tuple[str, str, str, str], list[str]] = {}
    for row in rows:
        signature = (
            _normalized(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _normalized(row.get("branch")),
        )
        grouped.setdefault(signature, []).append(
            _clean(row.get("raw_fields", {}).get("ledger"))
        )
    return sum(len(set(ledgers)) > 1 for ledgers in grouped.values())


def _same_ledger_semantic_duplicate_count(
    rows: Iterable[Mapping[str, Any]],
) -> int:
    signatures = [
        (
            _normalized(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _normalized(row.get("branch")),
            _clean(row.get("raw_fields", {}).get("ledger")),
        )
        for row in rows
    ]
    return len(signatures) - len(set(signatures))


def _base_meta(cutoff: date) -> dict[str, Any]:
    return {
        "municipality_code": GYEONGJU_MUNICIPALITY_CODE,
        "municipality_name": GYEONGJU_MUNICIPALITY_NAME,
        "owner_provider": GYEONGJU_PROVIDER,
        "canonical_url": GYEONGJU_INTEGRATED_URL,
        "candidate_id": GYEONGJU_CANONICAL_CANDIDATE_ID,
        "discovery_candidate_id": GYEONGJU_DISCOVERY_CANDIDATE_ID,
        "candidate_decisions": dict(GYEONGJU_CANDIDATE_DECISIONS),
        "ownership_scope": GYEONGJU_OWNERSHIP_SCOPE,
        "parser": GYEONGJU_PARSER,
        "cutoff": cutoff.isoformat(),
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "detail_attempts": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "application_endpoint_requests": 0,
        "applicant_list_requests": 0,
        "attachment_requests": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "output_rows": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "privacy_violations": 0,
        "duplicate_source_id_count": 0,
        "semantic_duplicate_count": 0,
        "configured_collection_error": "",
        "no_current_data": False,
        "no_current_reason": "",
        "official_evidence_urls": [
            GYEONGJU_INTEGRATED_URL,
            GYEONGJU_LIFELONG_URL,
            GYEONGJU_DISCOVERY_DETAIL_URL,
        ],
        "separate_owners": [
            {
                "url": GYEONGJU_EXPERIENCE_URL,
                "relationship": "separate official experience-reservation ledger",
            },
            {
                "url": GYEONGJU_INSTITUTION_URL,
                "relationship": "multi-owner external-institution course directory",
            },
        ],
    }


def collect_gyeongju_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = GYEONGJU_DEFAULT_MAX_PAGES,
    detail_limit: int = GYEONGJU_DEFAULT_DETAIL_LIMIT,
    *,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
    max_workers: int = GYEONGJU_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect both complete municipal Gyeongju education ledgers."""

    cutoff = _audit_date(today)
    meta = _base_meta(cutoff)
    session: Any = None
    physical_requests = 0

    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], GYEONGJU_PARSER, meta
        session_factory = gyeongju_session_factory
    html_fetcher = fetcher or _default_fetcher

    def tracked(current: Any, url: str) -> BeautifulSoup:
        nonlocal physical_requests
        meta["list_requests"] = int(meta["list_requests"]) + 1
        soup, retries = _fetch_soup(current, url, timeout, html_fetcher)
        physical_requests += 1 + retries
        meta["request_retry_count"] = int(meta["request_retry_count"]) + retries
        return soup

    try:
        if not is_gyeongju_education_target(target):
            raise GyeongjuContractError("target is not the canonical Gyeongju education owner")
        if (
            not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or timeout < 1
            or not isinstance(max_pages, int)
            or isinstance(max_pages, bool)
            or max_pages < 1
            or not isinstance(detail_limit, int)
            or isinstance(detail_limit, bool)
            or detail_limit < 0
            or not isinstance(max_workers, int)
            or isinstance(max_workers, bool)
            or not 1 <= max_workers <= GYEONGJU_MAX_WORKERS
        ):
            raise GyeongjuContractError("invalid collector limits")

        session = session_factory()

        integrated_pages: dict[int, dict[str, Any]] = {}
        integrated_sentinel: Optional[dict[str, Any]] = None
        first_integrated_soup: Optional[BeautifulSoup] = None
        for page in range(1, max_pages + 2):
            soup = tracked(session, gyeongju_integrated_list_url(page))
            if page == 1:
                first_integrated_soup = soup
            parsed = _parse_integrated_page(soup, page)
            if not parsed["rows"]:
                integrated_sentinel = parsed
                break
            if page > max_pages:
                meta["source_cap_reached"] = True
                raise GyeongjuContractError("max_pages reached before integrated empty boundary")
            integrated_pages[page] = parsed
        if integrated_sentinel is None or first_integrated_soup is None:
            meta["source_cap_reached"] = True
            raise GyeongjuContractError("integrated empty boundary was not reached")
        if not integrated_pages:
            raise GyeongjuContractError("integrated canonical ledger unexpectedly has no courses")
        data_pages = len(integrated_pages)
        if sorted(integrated_pages) != list(range(1, data_pages + 1)):
            raise GyeongjuContractError("integrated pages are not consecutive")
        for number, parsed in integrated_pages.items():
            count = len(parsed["rows"])
            if number < data_pages and count != GYEONGJU_PAGE_SIZE:
                raise GyeongjuContractError(f"integrated page {number}: premature short page")
            if not 1 <= count <= GYEONGJU_PAGE_SIZE or parsed["displayed_page"] != number:
                raise GyeongjuContractError(f"integrated page {number}: page boundary changed")
        if integrated_sentinel["page"] != data_pages + 1 or integrated_sentinel["displayed_page"] is not None:
            raise GyeongjuContractError("integrated sentinel is not the immediate empty page")
        integrated_rows = [
            row for page in range(1, data_pages + 1) for row in integrated_pages[page]["rows"]
        ]
        integrated_total = int(integrated_pages[1]["total"])
        if any(int(page["total"]) != integrated_total for page in (*integrated_pages.values(), integrated_sentinel)):
            raise GyeongjuContractError("integrated declared total changed across pages")
        if len(integrated_rows) != integrated_total:
            raise GyeongjuContractError("integrated declared total does not match parsed rows")
        integrated_ids = [_clean(row["identity"]) for row in integrated_rows]
        if len(integrated_ids) != len(set(integrated_ids)):
            raise GyeongjuContractError("duplicate integrated course identities")
        if any(row["event_end"] < cutoff for row in integrated_rows):
            raise GyeongjuContractError("integrated current ledger contains an expired course")

        _integrated_categories(first_integrated_soup)
        navigation = _integrated_navigation(first_integrated_soup)
        category_totals: dict[str, int] = {}
        for code in GYEONGJU_CATEGORY_FILTERS:
            url = f"{GYEONGJU_INTEGRATED_URL}?{urlencode({'selItemKind': code})}"
            category_totals[code] = _filter_total(tracked(session, url), category=code)
        if sum(category_totals.values()) != integrated_total:
            raise GyeongjuContractError("integrated category partitions do not sum to the complete ledger")

        branch_totals: dict[str, int] = {}
        audited_branches = (*GYEONGJU_BRANCHES, GYEONGJU_EMPTY_ACTIVE_BRANCH)
        for branch in audited_branches:
            url = f"{GYEONGJU_INTEGRATED_URL}?{urlencode({'mem_id': branch.member_id})}"
            branch_totals[branch.member_id] = _filter_total(
                tracked(session, url), member=branch.member_id
            )
        source_branch_counts = Counter(_clean(row["branch"]) for row in integrated_rows)
        for branch in GYEONGJU_BRANCHES:
            if branch_totals[branch.member_id] != source_branch_counts[branch.source_name]:
                raise GyeongjuContractError(f"integrated branch partition {branch.member_id} changed")
        if branch_totals[GYEONGJU_EMPTY_ACTIVE_BRANCH.member_id] != 0:
            raise GyeongjuContractError("empty active branch now contains unaudited courses")
        if sum(branch_totals.values()) != integrated_total:
            raise GyeongjuContractError("integrated branch partitions do not sum to the complete ledger")

        integrated_rechecks: dict[int, bool] = {}
        for page in (1, data_pages, data_pages + 1):
            check = _parse_integrated_page(
                tracked(session, gyeongju_integrated_list_url(page)), page
            )
            expected = integrated_sentinel if page == data_pages + 1 else integrated_pages[page]
            integrated_rechecks[page] = _integrated_signature(check) == _integrated_signature(expected)
            if not integrated_rechecks[page]:
                raise GyeongjuContractError(f"integrated page {page}: stability recheck changed")

        first_lifelong = _parse_lifelong_page(
            tracked(session, gyeongju_lifelong_list_url(1)), 1
        )
        lifelong_last = int(first_lifelong["advertised_last"])
        if lifelong_last > max_pages:
            meta["source_cap_reached"] = True
            raise GyeongjuContractError("max_pages is below lifelong advertised boundary")
        lifelong_pages: dict[int, dict[str, Any]] = {1: first_lifelong}
        for page in range(2, lifelong_last + 1):
            lifelong_pages[page] = _parse_lifelong_page(
                tracked(session, gyeongju_lifelong_list_url(page)), page
            )
        lifelong_sentinel_page = lifelong_last + 1
        lifelong_sentinel = _parse_lifelong_page(
            tracked(session, gyeongju_lifelong_list_url(lifelong_sentinel_page)),
            lifelong_sentinel_page,
        )
        for number, parsed in lifelong_pages.items():
            count = len(parsed["rows"])
            if parsed["advertised_last"] != lifelong_last or parsed["displayed_page"] != number:
                raise GyeongjuContractError(f"lifelong page {number}: pagination binding changed")
            if number < lifelong_last and count != GYEONGJU_PAGE_SIZE:
                raise GyeongjuContractError(f"lifelong page {number}: premature short page")
            if not 1 <= count <= GYEONGJU_PAGE_SIZE:
                raise GyeongjuContractError(f"lifelong page {number}: invalid row count")
        if (
            lifelong_sentinel["rows"]
            or lifelong_sentinel["advertised_last"] != lifelong_last
            or lifelong_sentinel["displayed_page"] is not None
        ):
            raise GyeongjuContractError("lifelong immediate empty sentinel changed")
        lifelong_rows = [
            row for page in range(1, lifelong_last + 1) for row in lifelong_pages[page]["rows"]
        ]
        sequences = [int(row["sequence"]) for row in lifelong_rows]
        if not sequences or sequences != list(range(sequences[0], 0, -1)):
            raise GyeongjuContractError("lifelong row-number census is incomplete")
        if sequences[0] != len(lifelong_rows):
            raise GyeongjuContractError("lifelong row-number total does not match parsed rows")
        lifelong_ids = [_clean(row["identity"]) for row in lifelong_rows]
        if len(lifelong_ids) != len(set(lifelong_ids)):
            raise GyeongjuContractError("duplicate lifelong course identities")
        lifelong_rechecks: dict[int, bool] = {}
        for page in (1, lifelong_last, lifelong_sentinel_page):
            check = _parse_lifelong_page(
                tracked(session, gyeongju_lifelong_list_url(page)), page
            )
            expected = lifelong_sentinel if page == lifelong_sentinel_page else lifelong_pages[page]
            lifelong_rechecks[page] = _lifelong_signature(check) == _lifelong_signature(expected)
            if not lifelong_rechecks[page]:
                raise GyeongjuContractError(f"lifelong page {page}: stability recheck changed")

        adjunct_signatures: dict[str, tuple[Any, ...]] = {}
        adjunct_specs = (
            (
                "special_program_application",
                GYEONGJU_SPECIAL_URL,
                GYEONGJU_SPECIAL_PATH,
                "203",
                "특성화 프로그램(사업)",
                True,
            ),
            (
                "external_institution_directory",
                GYEONGJU_INSTITUTION_URL,
                GYEONGJU_INSTITUTION_PATH,
                "125",
                "관내 평생교육기관 강좌",
                False,
            ),
        )
        for key, first_url, path, menu_idx, title, special in adjunct_specs:
            first = _empty_adjunct_page(
                tracked(session, first_url), title=title, menu_idx=menu_idx, special=special
            )
            second_url = f"https://{GYEONGJU_HOST}{path}?" + urlencode(
                {"menu_idx": menu_idx, "viewPage": 2, "rowCount": GYEONGJU_PAGE_SIZE}
            )
            second = _empty_adjunct_page(
                tracked(session, second_url), title=title, menu_idx=menu_idx, special=special
            )
            recheck = _empty_adjunct_page(
                tracked(session, first_url), title=title, menu_idx=menu_idx, special=special
            )
            if first != recheck or second[2] != first[2]:
                raise GyeongjuContractError(f"adjunct {key}: empty boundary changed")
            adjunct_signatures[key] = first

        all_current_lifelong = [
            row for row in lifelong_rows if row["event_end"] >= cutoff
        ]
        lifelong_test_current = [
            row for row in all_current_lifelong if row["functional_test_record"]
        ]
        current_lifelong = [
            row for row in all_current_lifelong if not row["functional_test_record"]
        ]
        detail_sources = [*integrated_rows, *current_lifelong]
        if len(detail_sources) > detail_limit:
            meta["source_cap_reached"] = True
            raise GyeongjuContractError("detail_limit would create a partial current snapshot")

        def detail_task(listed: Mapping[str, Any]) -> tuple[str, dict[str, Any], str, int]:
            current = session_factory()
            try:
                soup, retries = _fetch_soup(
                    current, _clean(listed["raw_url"]), timeout, html_fetcher
                )
                if listed["ledger"] == "integrated":
                    row, exclusion = _integrated_detail_row(listed, soup, cutoff)
                else:
                    row, exclusion = _lifelong_detail_row(listed, soup)
                return _clean(listed["identity"]), row, exclusion, retries
            finally:
                _close_quietly(current)

        detail_results: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
        detail_errors: list[str] = []
        detail_retries = 0
        meta["detail_attempts"] = len(detail_sources)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(detail_task, row): row for row in detail_sources}
            for future in as_completed(futures):
                listed = futures[future]
                key = (_clean(listed["ledger"]), _clean(listed["identity"]))
                try:
                    identity, row, exclusion, retries = future.result()
                    detail_results[(key[0], identity)] = (row, exclusion)
                    detail_retries += retries
                except Exception as exc:
                    detail_errors.append(
                        f"{key[0]} {key[1]}: {type(exc).__name__}: {_clean(exc)}"
                    )
        meta["detail_pages"] = len(detail_results)
        meta["request_retry_count"] = int(meta["request_retry_count"]) + detail_retries
        physical_requests += len(detail_sources) + detail_retries
        if detail_errors or len(detail_results) != len(detail_sources):
            sample = "; ".join(detail_errors[:5])
            raise GyeongjuContractError(
                f"current detail snapshot incomplete ({len(detail_results)}/{len(detail_sources)}): {sample}"
            )

        integrated_detailed = [
            detail_results[("integrated", _clean(row["identity"]))]
            for row in integrated_rows
        ]
        lifelong_detailed = [
            detail_results[("lifelong", _clean(row["identity"]))]
            for row in current_lifelong
        ]
        detail_category_counts = Counter(
            _clean(row["raw_fields"]["source_category"])
            for row, _ in integrated_detailed
        )
        expected_category_counts = {
            GYEONGJU_CATEGORY_FILTERS[code]: total for code, total in category_totals.items()
        }
        if detail_category_counts != Counter(expected_category_counts):
            raise GyeongjuContractError("integrated detail categories do not match selector census")

        exclusions = Counter(
            {"functional_test_record": len(lifelong_test_current)}
            if lifelong_test_current
            else {}
        )
        exclusions.update(
            exclusion
            for _, exclusion in (*integrated_detailed, *lifelong_detailed)
            if exclusion
        )
        output = [
            row
            for row, exclusion in (*integrated_detailed, *lifelong_detailed)
            if not exclusion
        ]
        required_fields = (
            "target",
            "fee",
            "period",
            "venue_name",
            "category",
            "schedule_raw",
        )
        required_field_counts = {
            field: sum(bool(_clean(row.get(field))) for row in output)
            for field in required_fields
        }
        incomplete_fields = {
            field: len(output) - count
            for field, count in required_field_counts.items()
            if count != len(output)
        }
        if incomplete_fields:
            raise GyeongjuContractError(
                f"required output fields are incomplete: {incomplete_fields}"
            )
        identity_count = len({row["provider_course_id"] for row in output})
        if identity_count != len(output):
            raise GyeongjuContractError("duplicate output provider identities")
        semantic_duplicates = _semantic_duplicate_count(output)
        same_ledger_semantic_duplicates = _same_ledger_semantic_duplicate_count(
            output
        )
        if semantic_duplicates:
            raise GyeongjuContractError("semantic duplicates remain across Gyeongju ledgers")
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        else:
            output = _default_dedupe(output)
        if len(output) != identity_count:
            raise GyeongjuContractError("external dedupe unexpectedly removed a unique course")

        status_counts = Counter(_clean(row["status"]) for row in output)
        returned_ledger_counts = Counter(_clean(row["raw_fields"]["ledger"]) for row in output)
        output_branch_counts = Counter(_clean(row["branch"]) for row in output)
        source_status_counts = {
            "integrated": dict(Counter(_clean(row["source_status"]) for row in integrated_rows)),
            "lifelong": dict(Counter(_clean(row["source_status"]) for row in lifelong_rows)),
        }
        privacy_violations = sum(
            1
            for row in output
            if _PHONE_RE.search(repr(row))
            or _EMAIL_RE.search(repr(row))
            or _RESIDENT_ID_RE.search(repr(row))
        )
        if privacy_violations:
            raise GyeongjuContractError("PII leaked from output rows")

        source_total = integrated_total + len(lifelong_rows)
        current_source_count = len(integrated_rows) + len(all_current_lifelong)
        meta.update(
            {
                "pages": data_pages + lifelong_last,
                "data_pages": data_pages + lifelong_last,
                "page_counts": {
                    "integrated": {
                        page: len(parsed["rows"]) for page, parsed in integrated_pages.items()
                    },
                    "lifelong": {
                        page: len(parsed["rows"]) for page, parsed in lifelong_pages.items()
                    },
                },
                "empty_boundary_pages": {
                    "integrated": data_pages + 1,
                    "lifelong": lifelong_sentinel_page,
                    "special_program_application": 2,
                    "external_institution_directory": 2,
                },
                "stable_rechecks": {
                    "integrated": integrated_rechecks,
                    "lifelong": lifelong_rechecks,
                    "adjuncts": {key: True for key in adjunct_signatures},
                },
                "source_totals": {
                    "integrated": integrated_total,
                    "lifelong": len(lifelong_rows),
                    "special_program_application": 0,
                    "external_institution_directory": 0,
                },
                "source_total": source_total,
                "source_rows": source_total,
                "discovered_links": source_total,
                "reservation_discovery_links": sum(
                    bool(row["application_url"]) for row in output
                ),
                "pagination_detected": data_pages > 1 or lifelong_last > 1,
                "current_counts": {
                    "integrated": len(integrated_rows),
                    "lifelong": len(all_current_lifelong),
                },
                "current_source_count": current_source_count,
                "expired_source_count": len(lifelong_rows) - len(all_current_lifelong),
                "all_source_status_counts": source_status_counts,
                "current_source_status_counts": {
                    "integrated": dict(Counter(_clean(row["source_status"]) for row in integrated_rows)),
                    "lifelong": dict(
                        Counter(
                            _clean(row["source_status"])
                            for row in all_current_lifelong
                        )
                    ),
                },
                "category_counts": expected_category_counts,
                "source_branch_counts": dict(source_branch_counts),
                "branch_partition_counts": branch_totals,
                "navigation_branch_names": navigation,
                "branch_addresses": {
                    **{branch.source_name: branch.address for branch in GYEONGJU_BRANCHES},
                    GYEONGJU_LIFELONG_BRANCH: GYEONGJU_LIFELONG_ADDRESS,
                },
                "excluded_current_counts": dict(exclusions),
                "cancelled_current_count": exclusions.get("cancelled_course", 0),
                "test_current_count": exclusions.get("functional_test_record", 0),
                "returned_by_ledger": dict(returned_ledger_counts),
                "branch_counts": dict(output_branch_counts),
                "status_counts": dict(status_counts),
                "application_control_count": sum(
                    bool(row["application_url"])
                    for row, _ in (*integrated_detailed, *lifelong_detailed)
                ),
                "actionable_application_count": sum(
                    bool(row["reservation_available"]) for row in output
                ),
                "application_endpoint_requests": 0,
                "applicant_list_requests": 0,
                "attachment_requests": 0,
                "duplicate_source_id_count": 0,
                "semantic_duplicate_count": semantic_duplicates,
                "same_ledger_semantic_duplicate_count": (
                    same_ledger_semantic_duplicates
                ),
                "privacy_violations": privacy_violations,
                "required_field_counts": required_field_counts,
                "returned_count": len(output),
                "output_rows": len(output),
                "logical_requests": int(meta["list_requests"]) + int(meta["detail_attempts"]),
                "physical_requests": physical_requests,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not output,
                "no_current_reason": (
                    "공식 통합예약·평생학습 교육 원장에 현재/향후 정상 강좌가 없음"
                    if not output
                    else ""
                ),
            }
        )
        return output, GYEONGJU_PARSER, meta
    except Exception as exc:  # every drift/network error fails closed
        meta.update(
            {
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
                "returned_count": 0,
                "output_rows": 0,
                "logical_requests": int(meta.get("list_requests") or 0)
                + int(meta.get("detail_attempts") or 0),
                "physical_requests": physical_requests,
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
            }
        )
        return [], GYEONGJU_PARSER, meta
    finally:
        _close_quietly(session)


collect = collect_gyeongju_education


__all__ = [
    "GYEONGJU_BRANCHES",
    "GYEONGJU_CANONICAL_CANDIDATE_ID",
    "GYEONGJU_CANONICAL_DERIVED_PROVIDER",
    "GYEONGJU_CANDIDATE_DECISIONS",
    "GYEONGJU_CATEGORY_FILTERS",
    "GYEONGJU_DISCOVERY_CANDIDATE_ID",
    "GYEONGJU_DISCOVERY_DETAIL_URL",
    "GYEONGJU_HOST",
    "GYEONGJU_INSTITUTION_URL",
    "GYEONGJU_INTEGRATED_PATH",
    "GYEONGJU_INTEGRATED_URL",
    "GYEONGJU_LIFELONG_ADDRESS",
    "GYEONGJU_LIFELONG_BRANCH",
    "GYEONGJU_LIFELONG_URL",
    "GYEONGJU_MUNICIPALITY_CODE",
    "GYEONGJU_MUNICIPALITY_NAME",
    "GYEONGJU_OWNER_BOUNDARY_AUDIT",
    "GYEONGJU_PARSER",
    "GYEONGJU_PII_FIELDS_NEVER_PERSISTED",
    "GYEONGJU_PROVIDER",
    "GYEONGJU_SPECIAL_URL",
    "GyeongjuContractError",
    "collect_gyeongju_education",
    "gyeongju_integrated_application_url",
    "gyeongju_integrated_detail_url",
    "gyeongju_integrated_list_url",
    "gyeongju_lifelong_detail_url",
    "gyeongju_lifelong_list_url",
    "gyeongju_session_factory",
    "is_gyeongju_education_target",
]
