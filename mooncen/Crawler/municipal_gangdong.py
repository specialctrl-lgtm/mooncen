"""Fail-closed collectors for Gangdong-gu's official education sources.

The municipality publishes seven independent providers.  The library provider
itself declares several non-overlapping programme sections:

* the Gangdong online-application portal (event/reception and resident IT
  classroom branches);
* the Gangdong public-health education board; and
* the Gangdong lifelong-learning programme board;
* every declared Gangdong public-library programme section; and
* the Gangdong 50 Plus Centre education catalogue;
* every public course term declared by the Gangdong Future-On portal; and
* the Gangdong resident-centre programme catalogue.

This module owns only those exact canonical routes.  It deliberately has no
dependency on ``Crawler_MunicipalYaml`` so the main municipal dispatcher can
inject its guarded HTTP session, fetch helper and row deduplicator without
creating an import cycle.

The health board intentionally removes detail links while a programme is in
``접수대기`` or after reception closes.  Those rows remain independently
identifiable by the board's continuous source number and are publishable from
the complete numbered list.  Whenever the source exposes a detail URL, detail
identity, dates and venue are mandatory.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from difflib import SequenceMatcher
import hashlib
import ipaddress
import math
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GANGDONG_RESERVE_PROVIDER = "MUNI_WWW_GANGDONG_GO_KR_EBC10BD8"
GANGDONG_RESERVE_URL = (
    "https://www.gangdong.go.kr/web/newreserve/reserve/list?basicType=reserveType_01"
)
GANGDONG_RESERVE_HOST = "www.gangdong.go.kr"
GANGDONG_RESERVE_LIST_PATH = "/web/newreserve/reserve/list"
GANGDONG_RESERVE_DETAIL_PATH = "/web/newreserve/reserve/view"
GANGDONG_COMEDU_DETAIL_PREFIX = "/web/comedu/eduProgram/"
GANGDONG_RESERVE_PAGE_SIZE = 10
GANGDONG_RESERVE_BRANCHES: tuple[tuple[str, str], ...] = (
    ("reserveType_01", "event"),
    ("RESIDENTCOMEDU", "comedu"),
)
GANGDONG_RESERVE_PARSER = (
    "gangdong_online_reservation_event_education+resident_comedu_complete+detail"
)

GANGDONG_HEALTH_PROVIDER = "MUNI_HEALTH_GANGDONG_GO_KR_50454384"
GANGDONG_HEALTH_URL = (
    "https://health.gangdong.go.kr/health/site/main/program/user/GD50000400"
)
GANGDONG_HEALTH_HOST = "health.gangdong.go.kr"
GANGDONG_HEALTH_LIST_PATH = "/health/site/main/program/user/GD50000400"
GANGDONG_HEALTH_DETAIL_PATH = "/health/site/main/program/view"
GANGDONG_HEALTH_PAGE_SIZE = 10
GANGDONG_HEALTH_PARSER = "gangdong_health_education_complete+available_detail"

GANGDONG_LLL_PROVIDER = "MUNI_LLL_GANGDONG_GO_KR_E8F6E943"
GANGDONG_LLL_URL = (
    "https://lll.gangdong.go.kr/program/ProgramBoardList.do?menucode=84"
)
GANGDONG_LLL_HOST = "lll.gangdong.go.kr"
GANGDONG_LLL_LIST_PATH = "/program/ProgramBoardList.do"
GANGDONG_LLL_DETAIL_PATH = "/program/ProgramClassroomView.do"
GANGDONG_LLL_MENU_CODE = "84"
GANGDONG_LLL_PAGE_SIZE = 6
GANGDONG_LLL_STATES: tuple[tuple[str, str, str], ...] = (
    ("eYet", "접수중", "OPEN"),
    ("eIng", "교육진행", "CLOSED"),
)
GANGDONG_LLL_PARSER = "gangdong_lifelong_active_states_complete+detail"

GANGDONG_LIBRARY_PROVIDER = "MUNI_WWW_GDLIBRARY_OR_KR_7E7ADF81"
GANGDONG_LIBRARY_URL = (
    "https://www.gdlibrary.or.kr/ch/menu/447/tmpr/lctr-evnt/reading?searchHmpg=1"
)
GANGDONG_LIBRARY_HOST = "www.gdlibrary.or.kr"
GANGDONG_LIBRARY_PAGE_SIZE = 10
# These are the programme sections declared by the official library navigation.
# ``special`` deliberately renders its complete catalogue without pagination.
GANGDONG_LIBRARY_SECTIONS: tuple[tuple[str, str, str, bool], ...] = (
    ("reading", "447", "reading", True),
    ("special", "446", "special", False),
    ("reading_club", "448", "reading-club", True),
    ("book_festival", "412", "book-festival", True),
    ("itbookin", "418", "itbookin", True),
)
GANGDONG_LIBRARY_NAMES = frozenset(
    ("중앙", "숲속", "성내", "해공", "강일", "암사", "천호", "둔촌", "작은", "통합")
)
GANGDONG_LIBRARY_BRANCHES = {
    "중앙": "강동중앙도서관",
    "숲속": "강동숲속도서관",
    "성내": "성내도서관",
    "해공": "해공도서관",
    "강일": "강일도서관",
    "암사": "암사도서관",
    "천호": "천호도서관",
    "둔촌": "둔촌도서관",
    "작은": "강동구립작은도서관",
    "통합": "강동구립도서관 통합",
}
GANGDONG_LIBRARY_LOCATIONS: Mapping[str, Mapping[str, Any]] = {
    "중앙": {
        "address": "서울특별시 강동구 양재대로84길 63",
        "lat": 37.5243543,
        "lon": 127.1369589,
    },
    "숲속": {
        "address": "서울특별시 강동구 구천면로 587",
        "lat": 37.5512062,
        "lon": 127.1640657,
    },
    "성내": {
        "address": "서울특별시 강동구 성안로 106-1",
        "lat": 37.5328621,
        "lon": 127.1333521,
    },
    "해공": {
        "address": "서울특별시 강동구 올림픽로 702",
        "lat": 37.5439013,
        "lon": 127.1255669,
    },
    "강일": {
        "address": "서울특별시 강동구 아리수로93길 9-14 4,5층",
        "lat": 37.5650589,
        "lon": 127.173758,
    },
    "암사": {
        "address": "서울특별시 강동구 고덕로20길 42",
        "lat": 37.5528442,
        "lon": 127.1333484,
    },
    "천호": {
        "address": "서울특별시 강동구 성안로31마길 1",
        "lat": 37.5405812,
        "lon": 127.1340989,
    },
    "둔촌": {
        "address": "서울특별시 강동구 동남로49길 21-8",
        "lat": 37.5315801,
        "lon": 127.1483305,
    },
}
GANGDONG_LIBRARY_LOCATION_SOURCE = (
    "https://www.gdlibrary.or.kr/ch/menu/421/smart-lib"
)
GANGDONG_LIBRARY_PARSER = (
    "gangdong_library_all_declared_program_sections_complete+detail"
)

GANGDONG_50PLUS_PROVIDER = "MUNI_WWW_50PLUS_OR_KR_65A625B6"
GANGDONG_50PLUS_URL = "https://www.50plus.or.kr/gdc/education.do"
GANGDONG_50PLUS_HOST = "www.50plus.or.kr"
GANGDONG_50PLUS_LIST_PATH = "/gdc/education.do"
GANGDONG_50PLUS_DETAIL_PATH = "/gdc/education-detail.do"
GANGDONG_50PLUS_PAGE_SIZE = 10
GANGDONG_50PLUS_PARSER = "gangdong_50plus_education_complete+detail"

GANGDONG_SLC_PROVIDER = "MUNI_SLC_GANGDONG_OR_KR_A54F60C1"
GANGDONG_SLC_URL = "https://slc.gangdong.or.kr/?m1=page&menu_id=176"
GANGDONG_SLC_HOST = "slc.gangdong.or.kr"
GANGDONG_SLC_MENU_PATH = "/api/menu/getMenu"
GANGDONG_SLC_LIST_PATH = "/api/learning/getCourseSummaryList"
GANGDONG_SLC_DETAIL_PATH = "/api/learning/getCourse"
GANGDONG_SLC_PAGE_SIZE = 100
# menu_id, term_id, menu key, parent menu, menu title.  These are the seven
# structured public-course boards declared by the official Future-On menu.
# Counselling and VOD-only boards are deliberately outside this course scope.
GANGDONG_SLC_TERMS: tuple[tuple[int, int, str, int, str], ...] = (
    (176, 2, "new_smart_campus_course", 175, "프로그램 신청"),
    (208, 6, "new_program_operation_case_course", 178, "프로그램 신청"),
    (209, 7, "new_parent_academy_course", 179, "프로그램 신청"),
    (210, 5, "new_career_further_education_course", 180, "설명회/박람회 신청"),
    (211, 4, "new_after_school_program_course", 181, "프로그램 신청"),
    (220, 21, "", 219, "프로그램 신청"),
    (35, 9, "new_sangpang_course", 23, "상상팡팡 프로그램 신청"),
)
GANGDONG_SLC_PARSER = "gangdong_future_on_all_declared_course_terms_complete+detail"

GANGDONG_JUMIN_PROVIDER = "MUNI_JUMIN_GANGDONG_GO_KR_935D7DD2"
GANGDONG_JUMIN_URL = (
    "https://jumin.gangdong.go.kr/program/ProgramBoardList.do?menucode=84"
)
GANGDONG_JUMIN_HOST = "jumin.gangdong.go.kr"
GANGDONG_JUMIN_LIST_PATH = "/program/ProgramBoardList.do"
GANGDONG_JUMIN_DETAIL_PATH = "/program/ProgramClassroomView.do"
GANGDONG_JUMIN_MENU_CODE = "84"
GANGDONG_JUMIN_PAGE_SIZE = 5
GANGDONG_JUMIN_PARSER = (
    "gangdong_resident_centres_complete+scheduled_status+all_current_details+"
    "source_unspecified_target"
)

GANGDONG_MUNICIPALITY_CODE = "1174000000"
GANGDONG_MUNICIPALITY_NAME = "서울특별시 강동구"
GANGDONG_MAX_DETAIL_WORKERS = 8

GANGDONG_PROVIDERS = frozenset(
    (
        GANGDONG_RESERVE_PROVIDER,
        GANGDONG_HEALTH_PROVIDER,
        GANGDONG_LLL_PROVIDER,
        GANGDONG_LIBRARY_PROVIDER,
        GANGDONG_50PLUS_PROVIDER,
        GANGDONG_SLC_PROVIDER,
        GANGDONG_JUMIN_PROVIDER,
    )
)
GANGDONG_CANONICAL_URLS = {
    GANGDONG_RESERVE_PROVIDER: GANGDONG_RESERVE_URL,
    GANGDONG_HEALTH_PROVIDER: GANGDONG_HEALTH_URL,
    GANGDONG_LLL_PROVIDER: GANGDONG_LLL_URL,
    GANGDONG_LIBRARY_PROVIDER: GANGDONG_LIBRARY_URL,
    GANGDONG_50PLUS_PROVIDER: GANGDONG_50PLUS_URL,
    GANGDONG_SLC_PROVIDER: GANGDONG_SLC_URL,
    GANGDONG_JUMIN_PROVIDER: GANGDONG_JUMIN_URL,
}

Fetcher = Callable[[Any, str, int], Any]
JsonPoster = Callable[[Any, str, Mapping[str, str], int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{2}|\d{4})\s*(?:[.\-/]|년)\s*"
    r"(?P<month>\d{1,2})\s*(?:[.\-/]|월)\s*"
    r"(?P<day>\d{1,2})\s*(?:일)?(?!\d)"
)
_EVENT_ID_RE = re.compile(r"^\d+$")
_COMEDU_ID_RE = re.compile(r"^\d+$")
_LLL_ID_RE = re.compile(r"fn_view\(\s*['\"](?P<id>\d+)['\"]\s*\)", re.I)
_NON_EDUCATION_EVENT_TOKENS = (
    "응시료 지원사업",
    "행정체험단",
    "지원금 신청",
    "보조금 신청",
    "대여 신청",
)
_EDUCATION_EVENT_TOKENS = (
    "교육",
    "교실",
    "강좌",
    "특강",
    "서당",
    "체험",
    "교류",
    "프로그램",
    "강습",
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        raise ValueError("empty HTTP response")
    parsed = urlparse(url)
    # The 50 Plus server currently returns deliberately tolerated legacy HTML
    # whose unclosed tags make lxml discard the catalogue table.  Python's
    # standards-based parser retains the official table and detail content.
    parser = (
        "html.parser"
        if parsed.hostname == GANGDONG_50PLUS_HOST
        and parsed.path in {GANGDONG_50PLUS_LIST_PATH, GANGDONG_50PLUS_DETAIL_PATH}
        else "lxml"
    )
    return BeautifulSoup(response.content, parser)


def _default_json_poster(
    current_session: Any,
    url: str,
    data: Mapping[str, str],
    timeout: int,
) -> Mapping[str, Any]:
    response = current_session.post(
        url,
        data=dict(data),
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise TypeError("JSON endpoint did not return an object")
    return payload


def _coerce_json(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    method = getattr(value, "json", None)
    if not callable(method):
        raise TypeError("JSON poster did not return a mapping or response")
    payload = method()
    if not isinstance(payload, Mapping):
        raise TypeError("JSON response is not an object")
    return payload


def _post_json(
    poster: JsonPoster,
    current_session: Any,
    url: str,
    data: Mapping[str, str],
    timeout: int,
) -> Mapping[str, Any]:
    return _coerce_json(poster(current_session, url, data, timeout))


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher did not return HTML or BeautifulSoup")
    return BeautifulSoup(content, "lxml")


def _fetch(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        year = int(match.group("year"))
        if year < 100:
            year += 2000
        try:
            result.append(date(year, int(match.group("month")), int(match.group("day"))))
        except ValueError:
            continue
    return result


def _date_range(value: Any, *, allow_single: bool = False) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if not values or (len(values) < 2 and not allow_single):
        return "", "", ""
    start = values[0]
    end = values[1] if len(values) >= 2 else start
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _capacity(value: Any) -> tuple[Optional[int], Optional[int]]:
    match = re.search(r"([\d,]+)\s*/\s*([\d,]+)", _clean(value))
    if not match:
        return None, None
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


def _stable_branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"GANGDONG_BRANCH_{digest}"


def _public_http_url(value: Any, *, base_url: str = "") -> str:
    candidate = urljoin(base_url, _clean(value))
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return ""
    return candidate


def _table_pairs(table: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if table is None:
        return pairs
    for tr in table.select("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        for index, cell in enumerate(cells):
            if cell.name != "th" or index + 1 >= len(cells):
                continue
            value = cells[index + 1]
            if value.name == "td":
                pairs[_clean(cell.get_text(" ", strip=True))] = _clean(
                    value.get_text(" ", strip=True)
                )
    return pairs


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _base_row(
    target: Any,
    *,
    identity_kind: str,
    identity: str,
    title: str,
    raw_url: str,
    parser: str,
) -> dict[str, Any]:
    provider = _provider(target)
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:{identity_kind}:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": _clean(title),
        "branch_url": _target_url(target),
        "program_type": "강좌",
        "category": "교육·강좌",
        "raw_url": raw_url,
        "reservation_available": False,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "municipality_code": GANGDONG_MUNICIPALITY_CODE,
        "municipality_full_name": GANGDONG_MUNICIPALITY_NAME,
        "raw_fields": {"parser": parser},
    }


def _set_branch(row: dict[str, Any], branch: str) -> None:
    value = _clean(branch)
    row.update(
        {
            "branch": value,
            "branch_code": _stable_branch_code(value),
            "venue_name": value,
            "preserve_branch": True,
        }
    )


def _target_signature(target: Any) -> tuple[str, str]:
    return _provider(target), _target_url(target)


def is_gangdong_reserve_target(target: Any) -> bool:
    provider, raw_url = _target_signature(target)
    parsed = urlparse(raw_url)
    return (
        provider == GANGDONG_RESERVE_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGDONG_RESERVE_HOST
        and parsed.path == GANGDONG_RESERVE_LIST_PATH
        and not parsed.params
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True)
        == {"basicType": ["reserveType_01"]}
    )


def is_gangdong_health_target(target: Any) -> bool:
    provider, raw_url = _target_signature(target)
    parsed = urlparse(raw_url)
    return (
        provider == GANGDONG_HEALTH_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGDONG_HEALTH_HOST
        and parsed.path == GANGDONG_HEALTH_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def is_gangdong_lll_target(target: Any) -> bool:
    provider, raw_url = _target_signature(target)
    parsed = urlparse(raw_url)
    return (
        provider == GANGDONG_LLL_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGDONG_LLL_HOST
        and parsed.path == GANGDONG_LLL_LIST_PATH
        and not parsed.params
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True)
        == {"menucode": [GANGDONG_LLL_MENU_CODE]}
    )


def is_gangdong_library_target(target: Any) -> bool:
    provider, raw_url = _target_signature(target)
    parsed = urlparse(raw_url)
    return (
        provider == GANGDONG_LIBRARY_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGDONG_LIBRARY_HOST
        and parsed.path == "/ch/menu/447/tmpr/lctr-evnt/reading"
        and not parsed.params
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True) == {"searchHmpg": ["1"]}
    )


def is_gangdong_50plus_target(target: Any) -> bool:
    provider, raw_url = _target_signature(target)
    parsed = urlparse(raw_url)
    return (
        provider == GANGDONG_50PLUS_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGDONG_50PLUS_HOST
        and parsed.path == GANGDONG_50PLUS_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def is_gangdong_slc_target(target: Any) -> bool:
    provider, raw_url = _target_signature(target)
    parsed = urlparse(raw_url)
    return (
        provider == GANGDONG_SLC_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGDONG_SLC_HOST
        and parsed.path == "/"
        and not parsed.params
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True)
        == {"m1": ["page"], "menu_id": ["176"]}
    )


def is_gangdong_jumin_target(target: Any) -> bool:
    provider, raw_url = _target_signature(target)
    parsed = urlparse(raw_url)
    return (
        provider == GANGDONG_JUMIN_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == GANGDONG_JUMIN_HOST
        and parsed.path == GANGDONG_JUMIN_LIST_PATH
        and not parsed.params
        and not parsed.fragment
        and parse_qs(parsed.query, keep_blank_values=True)
        == {"menucode": [GANGDONG_JUMIN_MENU_CODE]}
    )


def is_gangdong_target(target: Any) -> bool:
    return (
        is_gangdong_reserve_target(target)
        or is_gangdong_health_target(target)
        or is_gangdong_lll_target(target)
        or is_gangdong_library_target(target)
        or is_gangdong_50plus_target(target)
        or is_gangdong_slc_target(target)
        or is_gangdong_jumin_target(target)
    )


is_target = is_gangdong_target


def gangdong_reserve_list_url(basic_type: str, page: int) -> str:
    query = urlencode((('basicType', _clean(basic_type)), ('cp', str(max(1, int(page))))))
    return f"https://{GANGDONG_RESERVE_HOST}{GANGDONG_RESERVE_LIST_PATH}?{query}"


def gangdong_reserve_detail_url(basic_id: str) -> str:
    identity = _clean(basic_id)
    if not _EVENT_ID_RE.fullmatch(identity):
        return ""
    query = urlencode((('basicId', identity), ('basicType', 'reserveType_01')))
    return f"https://{GANGDONG_RESERVE_HOST}{GANGDONG_RESERVE_DETAIL_PATH}?{query}"


def gangdong_comedu_detail_url(program_id: str) -> str:
    identity = _clean(program_id)
    if not _COMEDU_ID_RE.fullmatch(identity):
        return ""
    return f"https://{GANGDONG_RESERVE_HOST}{GANGDONG_COMEDU_DETAIL_PREFIX}{identity}"


def gangdong_health_list_url(page: int) -> str:
    if int(page) <= 1:
        return GANGDONG_HEALTH_URL
    query = urlencode((('cp', str(int(page))), ('listType', 'list')))
    return f"https://{GANGDONG_HEALTH_HOST}{GANGDONG_HEALTH_LIST_PATH}?{query}"


def gangdong_health_detail_url(pg_seq: str) -> str:
    identity = _clean(pg_seq)
    if not identity.isdigit():
        return ""
    return f"https://{GANGDONG_HEALTH_HOST}{GANGDONG_HEALTH_DETAIL_PATH}?{urlencode({'pgSeq': identity})}"


def gangdong_lll_list_url(state_code: str, page: int) -> str:
    query = urlencode(
        (
            ("menucode", GANGDONG_LLL_MENU_CODE),
            ("search_type", "aca"),
            ("search_status", _clean(state_code)),
            ("pageIndex", str(max(1, int(page)))),
            ("search_key", ""),
            ("search_word", ""),
        )
    )
    return f"https://{GANGDONG_LLL_HOST}{GANGDONG_LLL_LIST_PATH}?{query}"


def gangdong_lll_detail_url(program_id: str) -> str:
    identity = _clean(program_id)
    if not identity.isdigit():
        return ""
    query = urlencode((('menucode', GANGDONG_LLL_MENU_CODE), ('gn_seq', identity)))
    return f"https://{GANGDONG_LLL_HOST}{GANGDONG_LLL_DETAIL_PATH}?{query}"


def _library_section(section_key: str) -> tuple[str, str, str, bool]:
    key = _clean(section_key)
    for spec in GANGDONG_LIBRARY_SECTIONS:
        if spec[0] == key:
            return spec
    raise ValueError(f"unknown Gangdong library section: {key}")


def gangdong_library_list_url(section_key: str, page: int) -> str:
    _key, menu, slug, _paginated = _library_section(section_key)
    base = f"https://{GANGDONG_LIBRARY_HOST}/ch/menu/{menu}/tmpr/lctr-evnt/{slug}"
    query: list[tuple[str, str]] = [("searchHmpg", "1")]
    if int(page) > 1:
        query.append(("page", str(int(page))))
    return f"{base}?{urlencode(query)}"


def gangdong_library_detail_url(section_key: str, program_id: str) -> str:
    _key, menu, slug, _paginated = _library_section(section_key)
    identity = _clean(program_id)
    if not identity.isdigit():
        return ""
    path = f"/ch/menu/{menu}/tmpr/lctr-evnt/{slug}/{identity}"
    return f"https://{GANGDONG_LIBRARY_HOST}{path}?{urlencode({'searchHmpg': '1'})}"


def gangdong_50plus_list_url(page: int) -> str:
    if int(page) <= 1:
        return GANGDONG_50PLUS_URL
    return (
        f"https://{GANGDONG_50PLUS_HOST}{GANGDONG_50PLUS_LIST_PATH}"
        f"?{urlencode({'page': str(int(page))})}"
    )


def gangdong_50plus_detail_url(program_id: str) -> str:
    identity = _clean(program_id)
    if not identity.isdigit():
        return ""
    return (
        f"https://{GANGDONG_50PLUS_HOST}{GANGDONG_50PLUS_DETAIL_PATH}"
        f"?{urlencode({'id': identity})}"
    )


def gangdong_slc_menu_api_url() -> str:
    return f"https://{GANGDONG_SLC_HOST}{GANGDONG_SLC_MENU_PATH}"


def gangdong_slc_list_api_url() -> str:
    return f"https://{GANGDONG_SLC_HOST}{GANGDONG_SLC_LIST_PATH}"


def gangdong_slc_detail_api_url() -> str:
    return f"https://{GANGDONG_SLC_HOST}{GANGDONG_SLC_DETAIL_PATH}"


def gangdong_slc_detail_url(menu_id: str | int, term_id: str | int, course_id: str | int) -> str:
    values = [_clean(menu_id), _clean(term_id), _clean(course_id)]
    if any(not value.isdigit() for value in values):
        return ""
    query = urlencode(
        (
            ("m1", "sub_briefing_detail"),
            ("menu_id", values[0]),
            ("term_id", values[1]),
            ("course_id", values[2]),
        )
    )
    return f"https://{GANGDONG_SLC_HOST}/?{query}"


def gangdong_jumin_list_url(page: int) -> str:
    query = urlencode(
        (
            ("menucode", GANGDONG_JUMIN_MENU_CODE),
            ("pageIndex", str(max(1, int(page)))),
        )
    )
    return f"https://{GANGDONG_JUMIN_HOST}{GANGDONG_JUMIN_LIST_PATH}?{query}"


def gangdong_jumin_detail_url(program_id: str) -> str:
    identity = _clean(program_id)
    if not identity.isdigit():
        return ""
    query = urlencode(
        (("menucode", GANGDONG_JUMIN_MENU_CODE), ("gn_seq", identity))
    )
    return f"https://{GANGDONG_JUMIN_HOST}{GANGDONG_JUMIN_DETAIL_PATH}?{query}"


def _page_count(soup: BeautifulSoup, parameter: str) -> int:
    pages = [1]
    for anchor in soup.select("a[href]"):
        query = parse_qs(urlparse(_clean(anchor.get("href"))).query)
        for value in query.get(parameter, []):
            if value.isdigit():
                pages.append(int(value))
    return max(pages)


def _reserve_list_rows(
    target: Any,
    soup: BeautifulSoup,
    *,
    basic_type: str,
    source_kind: str,
    page: int,
) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    exposed = 0
    for anchor in soup.select(".repla-lists > ul > li > a.bis"):
        exposed += 1
        title = _clean(anchor.get("title"))
        number_node = anchor.select_one(".no")
        number_text = _clean(number_node.get_text(" ", strip=True) if number_node else "").rstrip(".")
        status_node = anchor.select_one(".state")
        source_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
        values = [
            _clean(node.get_text(" ", strip=True))
            for node in anchor.select(".comp-list > li")
        ]
        apply_start, apply_end, apply_period = _date_range(values[0] if values else "")
        start_date, end_date, period = _date_range(values[1] if len(values) > 1 else "", allow_single=True)
        href = _public_http_url(anchor.get("href"), base_url=GANGDONG_RESERVE_URL)
        identity = ""
        raw_url = ""
        if source_kind == "event":
            parsed = urlparse(href)
            query = parse_qs(parsed.query, keep_blank_values=True)
            identity = query.get("basicId", [""])[0]
            expected_query = {
                "basicId": [identity],
                "cp": [str(page)],
                "basicType": [basic_type],
            }
            if (
                parsed.scheme.lower() != "https"
                or parsed.netloc.lower() != GANGDONG_RESERVE_HOST
                or parsed.path != GANGDONG_RESERVE_DETAIL_PATH
                or query != expected_query
            ):
                identity = ""
            raw_url = gangdong_reserve_detail_url(identity)
        else:
            parsed = urlparse(href)
            match = re.fullmatch(r"/web/comedu/eduProgram/(\d+)", parsed.path)
            identity = match.group(1) if match else ""
            if (
                parsed.scheme.lower() != "https"
                or parsed.netloc.lower() != GANGDONG_RESERVE_HOST
                or parsed.query
                or parsed.fragment
            ):
                identity = ""
            raw_url = gangdong_comedu_detail_url(identity)
        status = {"준비중": "SCHEDULED", "접수중": "OPEN", "마감": "CLOSED"}.get(source_status, "")
        if (
            not number_text.isdigit()
            or not title
            or not identity
            or not raw_url
            or not status
            or not period
        ):
            invalid += 1
            continue
        row = _base_row(
            target,
            identity_kind=source_kind,
            identity=identity,
            title=title,
            raw_url=raw_url,
            parser=GANGDONG_RESERVE_PARSER,
        )
        row.update(
            {
                "status": status,
                "reservation_available": status == "OPEN",
                "start_date": start_date,
                "end_date": end_date,
                "period": period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "apply_period": apply_period,
            }
        )
        row["raw_fields"].update(
            {
                "source_kind": source_kind,
                "source_id": identity,
                "list_number": int(number_text),
                "list_page": page,
                "source_status": source_status,
                "list_title": title,
                "list_period": period,
                "list_apply_period": apply_period,
                "detail_required": True,
            }
        )
        rows.append(row)
    return rows, invalid, exposed


def _education_event(title: str, body: str) -> bool:
    comparable = _clean(title)
    if any(token in comparable for token in _NON_EDUCATION_EVENT_TOKENS):
        return False
    evidence = f"{comparable} {_clean(body)}"
    return any(token in evidence for token in _EDUCATION_EVENT_TOKENS)


def _normalize_event_branch(value: Any) -> str:
    branch = _clean(value)
    # Responsive content tables sometimes flatten the minute component from
    # an adjacent time cell (``10:00``/``12:30``) into the venue value.
    branch = re.sub(r"^(?:00|30)\s+(?=[가-힣A-Za-z])", "", branch)
    branch = re.sub(r"\s*[-·:]+\s*$", "", branch)
    branch = re.sub(
        r"\s*\([^)]*(?:로|길|시|구|동|번지|층)[^)]*\)\s*$", "", branch
    )
    branch = re.sub(r"(\d)\s+(층|동|호)\b", r"\1\2", branch)
    branch = re.sub(r"(?<=[가-힣])\s+(\d+동)\b", r"\1", branch)
    branch = re.sub(r"\s*·\s*", "·", branch)
    return _clean(branch)[:100]


def _event_branch(title: str, body: str) -> str:
    text = _clean(body)
    label_pattern = re.compile(
        r"(?:교육\s*장소|교류\s*장소|운영\s*장소|장\s*소|방문\s*도시)\s*[:：]\s*"
        r"(?P<value>.{2,140}?)"
        r"(?=\s+(?:신청\s*대상|참여\s*대상|교류\s*인원|모집\s*대상|모집\s*인원|"
        r"교육\s*내용|교류\s*내용|내\s*용|주\s*차|프로그램|참가비|신청\s*기간|"
        r"문\s*의|문의사항|[○◦■▪□※♣▶])|$)"
    )
    matches = list(label_pattern.finditer(text))
    value = _clean(matches[-1].group("value")) if matches else ""
    if "온라인" in value or "화상회의" in value:
        return "온라인 화상회의"
    if value:
        value = re.sub(r"\s*\([^)]*(?:로|길|시|구|동|번지|층)[^)]*\)\s*$", "", value).strip()
        if "방문" in title and not any(suffix in value for suffix in ("센터", "관", "장")):
            return _normalize_event_branch(f"{value} 문화체험")
        return _normalize_event_branch(value)
    if "강일보건지소" in text or "강일보건지소" in title:
        return "강일보건지소"
    suffix_pattern = re.compile(
        r"([가-힣A-Za-z0-9·]+(?:\s+[가-힣A-Za-z0-9·]+){0,7}\s*"
        r"(?:클라이밍짐|윈드서핑장|조정카누경기장|경정공원|주민센터|보건지소|"
        r"교육센터|문화센터|평생학습관|도서관|대강당|다목적실)(?:\s*\d+\s*호)?)"
    )
    candidates = [_clean(match.group(1)) for match in suffix_pattern.finditer(text)]
    candidates = [candidate for candidate in candidates if len(candidate) <= 100]
    if candidates:
        return _normalize_event_branch(candidates[0])
    if "온라인" in text and "교류" in title:
        return "온라인 화상회의"
    return ""


def _compact_event_label(value: Any) -> str:
    return re.sub(r"[\s·:/]+", "", _clean(value))


def _event_table_values(body_node: Any, headers: set[str]) -> list[str]:
    if body_node is None:
        return []
    normalized_headers = {_compact_event_label(header) for header in headers}
    found: list[str] = []
    for table in body_node.select("table"):
        active_rowspans: dict[int, tuple[int, str]] = {}
        value_columns: set[int] = set()
        for tr in table.select("tr"):
            row_values: dict[int, str] = {}
            next_rowspans: dict[int, tuple[int, str]] = {}
            for column, (remaining, value) in active_rowspans.items():
                row_values[column] = value
                if remaining > 1:
                    next_rowspans[column] = (remaining - 1, value)

            column = 0
            for cell in tr.find_all(["th", "td"], recursive=False):
                while column in row_values:
                    column += 1
                try:
                    colspan = max(1, int(cell.get("colspan", 1)))
                except (TypeError, ValueError):
                    colspan = 1
                try:
                    rowspan = max(1, int(cell.get("rowspan", 1)))
                except (TypeError, ValueError):
                    rowspan = 1
                value = _clean(cell.get_text(" ", strip=True))
                for offset in range(colspan):
                    logical_column = column + offset
                    row_values[logical_column] = value
                    if rowspan > 1:
                        next_rowspans[logical_column] = (rowspan - 1, value)
                column += colspan
            active_rowspans = next_rowspans

            declared_columns = (
                {
                    column
                    for column, value in row_values.items()
                    if _compact_event_label(value) in normalized_headers
                }
                if len(row_values) >= 3
                else set()
            )
            if declared_columns:
                value_columns = declared_columns
                continue
            if not value_columns:
                continue
            for value_column in sorted(value_columns):
                value = _clean(row_values.get(value_column))
                if (
                    value
                    and value not in {"-", "없음", "해당없음"}
                    and _compact_event_label(value) not in normalized_headers
                ):
                    found.append(value)
    return list(dict.fromkeys(found))


def _event_table_branch(body_node: Any) -> str:
    values = _event_table_values(
        body_node,
        {"교육장소", "행사장소", "운영장소", "교류장소", "장소"},
    )
    normalized = [_normalize_event_branch(value) for value in values]
    normalized = list(dict.fromkeys(value for value in normalized if value))
    return _normalize_event_branch(" / ".join(normalized)) if normalized else ""


def _event_labeled_value(body: str, labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(labels)
    stop_labels = (
        r"신청\s*대상|참여\s*대상|모집\s*대상|대\s*상|"
        r"모집\s*기간|신청\s*기간|신청\s*방법|모집\s*인원|"
        r"교육\s*내용|교류\s*내용|운영\s*정보|교육\s*장소|"
        r"교류\s*장소|운영\s*장소|장\s*소|"
        r"수\s*강\s*료|참\s*가\s*비|참여\s*비용|문\s*의"
    )
    match = re.search(
        rf"(?:{label_pattern})\s*[:：]\s*"
        rf"(?P<value>.{{1,300}}?)"
        rf"(?=\s+(?:{stop_labels})\s*[:：]|\s*[▪▶□○◦■※♣]\s+|"
        rf"\s*·\s+(?=(?:{stop_labels})\s*[:：])|$)",
        _clean(body),
    )
    if not match:
        return ""
    value = _clean(match.group("value")).strip(" -·")
    while value.endswith("(") and value.count("(") > value.count(")"):
        value = value[:-1].rstrip()
    return value


def _event_target(body: str) -> tuple[str, str]:
    value = _event_labeled_value(
        body,
        (
            r"신청\s*대상",
            r"참여\s*대상",
            r"모집\s*대상",
            r"대\s*상",
        ),
    )
    if value:
        return value, "detail_body_label"
    return "대상 별도 안내", "official_detail_omits_program_target"


def _event_fee(body_node: Any, body: str) -> tuple[str, str]:
    value = _event_labeled_value(
        body,
        (
            r"수\s*강\s*료",
            r"참\s*가\s*비",
            r"참여\s*비용",
            r"참가\s*비용",
        ),
    )
    if value:
        return value, "detail_body_label"
    unlabelled_free = re.search(
        r"(?:참여\s*비용|참가\s*비용)\s+(무료|유료)\b",
        _clean(body),
    )
    if unlabelled_free:
        return _clean(unlabelled_free.group(1)), "detail_body_phrase"
    table_values = _event_table_values(
        body_node,
        {"수강료", "참가비", "참여비용", "비용"},
    )
    if table_values:
        return " / ".join(table_values)[:300], "detail_schedule_table"
    return "요금 별도 안내", "official_detail_omits_program_fee"


def _event_schedule(
    pairs: dict[str, str],
    body_node: Any,
    body: str,
) -> tuple[str, str]:
    sessions: list[str] = []
    for key, value in pairs.items():
        if not re.fullmatch(r"\d+회차", key):
            continue
        schedule = re.split(
            r"\s+(?:모집\s*인원|참가\s*인원|현재\s*신청\s*인원)\s*[:：]",
            _clean(value),
            maxsplit=1,
        )[0]
        if schedule:
            sessions.append(schedule)
    sessions = list(dict.fromkeys(sessions))

    table_values = _event_table_values(
        body_node,
        {
            "교육일시",
            "운영시간",
            "교류일시",
            "방문일시",
            "운영기간",
            "수업기간",
            "일시",
            "시간",
        },
    )

    value = _event_labeled_value(
        body,
        (
            r"교육\s*일정",
            r"교육\s*일시",
            r"운영\s*일시",
            r"교류\s*일시",
            r"방문\s*일시",
            r"수업\s*기간",
            r"일\s*시",
        ),
    )
    candidate_groups = (
        (sessions, "detail_session_rows"),
        (table_values, "detail_schedule_table"),
        ([value] if value else [], "detail_body_label"),
    )
    time_pattern = re.compile(r"\d{1,2}\s*:\s*\d{2}")
    date_pattern = re.compile(
        r"(?:\d{4}\s*[./-]\s*\d{1,2}|\d{1,2}\s*[./]\s*\d{1,2}|"
        r"\d{1,2}\s*월\s*\d{1,2})"
    )
    for require_time in (True, False):
        for candidates, evidence in candidate_groups:
            relevant = [
                candidate
                for candidate in candidates
                if time_pattern.search(candidate)
                or (not require_time and date_pattern.search(candidate))
            ]
            if relevant and (
                not require_time or any(time_pattern.search(item) for item in relevant)
            ):
                return " / ".join(dict.fromkeys(relevant))[:500], evidence
    return "시간 별도 안내", "official_detail_omits_exact_time"


def _reserve_event_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    table = soup.select_one("#con > .table01 > table") or soup.select_one("#con table")
    pairs = _table_pairs(table)
    detail_title = _clean(pairs.get("행사제목"))
    apply_start, apply_end, apply_period = _date_range(pairs.get("접수기간"))
    start_date, end_date, period = _date_range(pairs.get("교육·행사 일시"), allow_single=True)
    if apply_period != _clean(row.get("apply_period")):
        errors.append("event detail reception period differs from list")
    if period != _clean(row.get("period")):
        errors.append("event detail education period differs from list")
    body_node = soup.select_one("#con .basicContent")
    body = _clean(body_node.get_text(" ", strip=True) if body_node else "")
    if not body:
        errors.append("event detail body is missing")
    education = _education_event(f"{row.get('title', '')} {detail_title}", body)
    row["raw_fields"]["education_event"] = education
    if not education:
        row["raw_fields"]["excluded_non_education"] = True
        return errors
    if detail_title != _clean(row.get("title")):
        errors.append("event detail title differs from list")
    table_branch = _event_table_branch(body_node)
    branch = table_branch or _event_branch(detail_title, body)
    if not branch:
        errors.append("event detail venue/branch is missing")
    else:
        _set_branch(row, branch)
        row["raw_fields"]["venue_evidence"] = (
            "detail_schedule_table" if table_branch else "detail_body_text"
        )
    if body:
        row["description"] = body[:5000]
    target, target_evidence = _event_target(body)
    fee, fee_evidence = _event_fee(body_node, body)
    schedule_raw, schedule_evidence = _event_schedule(pairs, body_node, body)
    row.update(
        {
            "start_date": start_date or row.get("start_date"),
            "end_date": end_date or row.get("end_date"),
            "period": period or row.get("period"),
            "apply_start": apply_start or row.get("apply_start"),
            "apply_end": apply_end or row.get("apply_end"),
            "apply_period": apply_period or row.get("apply_period"),
            "target": target,
            "fee": fee,
            "schedule_raw": schedule_raw,
        }
    )
    row["raw_fields"].update(
        {
            "session_count": sum(
                1 for key in pairs if re.fullmatch(r"\d+회차", key)
            ),
            "target_evidence": target_evidence,
            "fee_evidence": fee_evidence,
            "schedule_evidence": schedule_evidence,
        }
    )
    return errors


def _comparable_comedu_title(value: Any) -> str:
    result = _clean(re.sub(r"^\s*\d+\s*[.]\s*", "", _clean(value)))
    result = re.sub(r"^\s*(?:\((?:일일|일일특강)\)|\[경진 대비반\]|\(국민행복IT경진대회 대비반\)|\(현장체험 특화과정\))\s*", "", result)
    result = re.sub(r"\s*\(2022\)\s*$", "", result)
    return _clean(result).casefold()


def _comedu_branch(source_branch: str, detail_text: str) -> str:
    branch = _clean(source_branch)
    if branch == "기타":
        match = re.search(
            r"교육장\s*찾아가는\s*길\s*[-:]\s*(.+?)(?=\s*[□※]|$)",
            _clean(detail_text),
        )
        if match:
            return _clean(match.group(1))[:100]
    if branch in {"강일동", "성내2동", "암사동"}:
        return f"{branch} 교육장"
    return branch


def _reserve_comedu_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    table = soup.select_one("table")
    pairs = _table_pairs(table)
    source_branch = _clean(pairs.get("교육장"))
    apply_start, apply_end, apply_period = _date_range(pairs.get("접수기간/상태"))
    start_date, end_date, period = _date_range(pairs.get("교육기간"), allow_single=True)
    detail_text = _clean(pairs.get("상세정보"))
    title_match = re.search(r"강좌명\s*[:：]\s*(.+?)(?=\s*[□※]|$)", detail_text)
    detail_title = _clean(title_match.group(1)) if title_match else ""
    title_evidence = "detail_course_name"
    if not detail_title and _comparable_comedu_title(source_branch) == _comparable_comedu_title(row.get("title")):
        # The official digital-counselling records intentionally leave both
        # descriptive cells empty; their classroom label is also the declared
        # programme title and is therefore the only detail-page title evidence.
        detail_title = source_branch
        title_evidence = "detail_classroom_label"
    list_comparable = _comparable_comedu_title(row.get("title"))
    detail_comparable = _comparable_comedu_title(detail_title)
    title_similarity = (
        SequenceMatcher(None, list_comparable, detail_comparable).ratio()
        if list_comparable and detail_comparable
        else 0.0
    )
    if title_similarity < 0.90:
        errors.append("resident IT detail title differs from list")
    if not source_branch:
        errors.append("resident IT detail classroom is missing")
    if apply_period != _clean(row.get("apply_period")):
        errors.append("resident IT detail reception period differs from list")
    if period != _clean(row.get("period")):
        errors.append("resident IT detail education period differs from list")
    branch = _comedu_branch(source_branch, detail_text)
    if not branch:
        errors.append("resident IT normalized classroom is missing")
    else:
        _set_branch(row, branch)
    current, capacity = _capacity(pairs.get("수강인원"))
    fee = _clean(pairs.get("수강료")) or "요금 별도 안내"
    target = _clean(pairs.get("교육대상") or pairs.get("대상")) or "대상 별도 안내"
    schedule = _clean(pairs.get("요일 및 시간")) or "시간 별도 안내"
    row.update(
        {
            "start_date": start_date or row.get("start_date"),
            "end_date": end_date or row.get("end_date"),
            "period": period or row.get("period"),
            "apply_start": apply_start or row.get("apply_start"),
            "apply_end": apply_end or row.get("apply_end"),
            "apply_period": apply_period or row.get("apply_period"),
            "schedule": schedule,
            "schedule_raw": schedule,
            "price": fee,
            "fee": fee,
            "target": target,
            "contact": _clean(pairs.get("연락처")),
            "capacity": capacity,
            "enrolled": current,
            "description": _clean(pairs.get("강좌안내") or detail_text)[:5000],
        }
    )
    row["raw_fields"].update(
        {
            "source_classroom": source_branch,
            "detail_title": detail_title,
            "detail_title_evidence": title_evidence,
            "title_similarity": round(title_similarity, 4),
            "target_evidence": (
                "detail_table" if target != "대상 별도 안내" else "official_detail_omits_target"
            ),
            "fee_evidence": (
                "detail_table" if fee != "요금 별도 안내" else "official_detail_omits_fee"
            ),
        }
    )
    return errors


def _parallel_details(
    rows: list[dict[str, Any]],
    *,
    timeout: int,
    detail_limit: int,
    max_workers: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    parser_for: Callable[[dict[str, Any]], Callable[[dict[str, Any], BeautifulSoup], list[str]]],
) -> tuple[int, int, int, list[str], bool]:
    required = [
        row for row in rows if row.get("raw_fields", {}).get("detail_required", True) is not False
    ]
    allowed = max(0, int(detail_limit))
    selected = required[:allowed]
    capped = allowed < len(required)
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def current_session() -> Any:
        value = getattr(local, "session", None)
        if value is None:
            value = session_factory()
            local.session = value
            with sessions_lock:
                sessions.append(value)
        return value

    def enrich(row: dict[str, Any]) -> tuple[bool, list[str]]:
        identity = _clean(row.get("provider_course_id"))
        try:
            soup = _fetch(fetcher, current_session(), _clean(row.get("raw_url")), timeout)
            return True, [
                f"{identity}: {error}" for error in parser_for(row)(row, soup)
            ]
        except Exception as exc:
            return False, [f"{identity}: detail fetch {type(exc).__name__}"]

    results: list[tuple[bool, list[str]]] = []
    try:
        if selected:
            workers = min(GANGDONG_MAX_DETAIL_WORKERS, max(1, int(max_workers)), len(selected))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gangdong-detail") as pool:
                results = list(pool.map(enrich, selected))
    finally:
        for value in sessions:
            _close_quietly(value)
    pages = sum(success for success, _errors in results)
    errors = [error for _success, item_errors in results for error in item_errors]
    return len(required), len(selected), pages, errors, capped


def _finish_meta(
    *,
    rows: list[dict[str, Any]],
    candidate_count: int,
    pages: int,
    total_pages: int,
    list_complete: bool,
    detail_required_count: int,
    detail_attempts: int,
    detail_pages: int,
    detail_exempt_count: int,
    detail_errors: list[str],
    source_cap_reached: bool,
    errors: list[str],
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    details_complete = (
        detail_attempts == detail_required_count
        and detail_pages == detail_required_count
        and not detail_errors
    )
    all_errors = list(dict.fromkeys([*errors, *detail_errors]))
    snapshot_complete = list_complete and details_complete and not all_errors
    output_count = len(rows) if snapshot_complete else 0
    no_current_data = snapshot_complete and not rows
    meta: dict[str, Any] = {
        "pages": pages,
        "total_pages": total_pages,
        "detail_pages": detail_pages,
        "detail_attempts": detail_attempts,
        "detail_required_count": detail_required_count,
        "required_detail_count": detail_required_count,
        "detail_exempt_count": detail_exempt_count,
        "detail_errors": len(detail_errors),
        "pagination_detected": total_pages > 1,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "candidate_count": candidate_count,
        "current_count": output_count,
        "no_current_data": no_current_data,
        "no_current_reason": "official current/future education list is empty" if no_current_data else "",
        "branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)) if snapshot_complete else {},
    }
    if extra:
        meta.update(extra)
    if all_errors:
        meta["configured_collection_error"] = "; ".join(all_errors)
    return meta


def collect_gangdong_reserve(
    target: Any,
    timeout: int = 20,
    max_pages: int = 50,
    detail_limit: int = 200,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete event-education and resident-IT branches."""

    errors: list[str] = []
    if not is_gangdong_reserve_target(target):
        errors.append("target does not match the canonical Gangdong reservation source")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    candidates: list[dict[str, Any]] = []
    all_seen_ids: set[tuple[str, str]] = set()
    pages = 0
    total_pages = 0
    source_total = 0
    exposed_total = 0
    invalid = 0
    duplicates = 0
    expired = 0
    source_cap_reached = False
    session: Any = None
    branch_metrics: dict[str, dict[str, int]] = {}

    try:
        if not errors:
            session = make_session()
            for basic_type, source_kind in GANGDONG_RESERVE_BRANCHES:
                try:
                    first = _fetch(fetch, session, gangdong_reserve_list_url(basic_type, 1), timeout)
                except Exception as exc:
                    errors.append(f"{source_kind} page 1 fetch {type(exc).__name__}")
                    continue
                first_rows, first_invalid, first_exposed = _reserve_list_rows(
                    target, first, basic_type=basic_type, source_kind=source_kind, page=1
                )
                branch_total = max(
                    (int(row["raw_fields"]["list_number"]) for row in first_rows),
                    default=0,
                )
                declared_pages = _page_count(first, "cp")
                expected_pages = max(1, math.ceil(branch_total / GANGDONG_RESERVE_PAGE_SIZE))
                if not branch_total or declared_pages != expected_pages:
                    errors.append(
                        f"{source_kind} declares {declared_pages} pages for numbered total {branch_total}"
                    )
                allowed_pages = max(1, int(max_pages))
                if declared_pages > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"{source_kind} max_pages cap reached after {allowed_pages} of {declared_pages} pages"
                    )
                branch_rows: list[dict[str, Any]] = []
                branch_exposed = 0
                branch_invalid = 0
                visited = 0
                for page in range(1, min(declared_pages, allowed_pages) + 1):
                    if page == 1:
                        soup = first
                        current_rows, current_invalid, current_exposed = (
                            first_rows,
                            first_invalid,
                            first_exposed,
                        )
                    else:
                        try:
                            soup = _fetch(
                                fetch,
                                session,
                                gangdong_reserve_list_url(basic_type, page),
                                timeout,
                            )
                        except Exception as exc:
                            errors.append(f"{source_kind} page {page} fetch {type(exc).__name__}")
                            break
                        current_rows, current_invalid, current_exposed = _reserve_list_rows(
                            target,
                            soup,
                            basic_type=basic_type,
                            source_kind=source_kind,
                            page=page,
                        )
                    visited += 1
                    pages += 1
                    branch_exposed += current_exposed
                    branch_invalid += current_invalid
                    expected_rows = min(
                        GANGDONG_RESERVE_PAGE_SIZE,
                        max(0, branch_total - ((page - 1) * GANGDONG_RESERVE_PAGE_SIZE)),
                    )
                    if current_exposed != expected_rows:
                        errors.append(
                            f"{source_kind} page {page} exposes {current_exposed}; expected {expected_rows}"
                        )
                    branch_rows.extend(current_rows)
                numbers = [int(row["raw_fields"]["list_number"]) for row in branch_rows]
                if numbers != list(range(branch_total, 0, -1)):
                    errors.append(f"{source_kind} list numbers are not continuous")
                for row in branch_rows:
                    identity = (source_kind, _clean(row["raw_fields"].get("source_id")))
                    if identity in all_seen_ids:
                        duplicates += 1
                        continue
                    all_seen_ids.add(identity)
                    if date.fromisoformat(_clean(row.get("end_date"))) < cutoff:
                        expired += 1
                        continue
                    candidates.append(row)
                branch_metrics[source_kind] = {
                    "total": branch_total,
                    "pages": declared_pages,
                    "visited": visited,
                    "exposed": branch_exposed,
                    "invalid": branch_invalid,
                }
                total_pages += declared_pages
                source_total += branch_total
                exposed_total += branch_exposed
                invalid += branch_invalid
    finally:
        _close_quietly(session)

    if invalid:
        errors.append(f"{invalid} reservation rows were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate reservation IDs crossed branches/pages")
    if source_total != len(all_seen_ids):
        errors.append(f"numbered source total {source_total} differs from {len(all_seen_ids)} unique IDs")
    list_complete = (
        not errors
        and pages == total_pages
        and exposed_total == source_total
        and not invalid
        and not duplicates
    )

    def parser_for(row: dict[str, Any]) -> Callable[[dict[str, Any], BeautifulSoup], list[str]]:
        return _reserve_event_detail if row["raw_fields"].get("source_kind") == "event" else _reserve_comedu_detail

    required, attempts, detail_pages, detail_errors, detail_capped = _parallel_details(
        candidates,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
        parser_for=parser_for,
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(f"detail_limit cap allows {attempts} of {required} required detail pages")
    rows = [
        _clean_row(row)
        for row in candidates
        if not row.get("raw_fields", {}).get("excluded_non_education")
    ]
    excluded_non_education = len(candidates) - len(rows)
    if dedupe_rows is not None:
        rows = list(dedupe_rows(rows))
    meta = _finish_meta(
        rows=rows,
        candidate_count=len(candidates),
        pages=pages,
        total_pages=total_pages,
        list_complete=list_complete,
        detail_required_count=required,
        detail_attempts=attempts,
        detail_pages=detail_pages,
        detail_exempt_count=0,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
        extra={
            "total_count": source_total,
            "source_total": source_total,
            "discovered_links": len(all_seen_ids),
            "exposed_rows": exposed_total,
            "expired_count": expired,
            "invalid_count": invalid,
            "duplicate_count": duplicates,
            "excluded_non_education": excluded_non_education,
            "source_branches": branch_metrics,
        },
    )
    if not meta["snapshot_complete"]:
        rows = []
    return rows, GANGDONG_RESERVE_PARSER, meta


_HEALTH_STATUS_MAP = {"접수대기": "SCHEDULED", "접수중": "OPEN", "접수종료": "CLOSED"}


def _health_list_rows(target: Any, soup: BeautifulSoup, *, page: int) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    exposed = 0
    table = soup.select_one("table")
    if table is None:
        return rows, 1, exposed
    for tr in table.select("tbody > tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        exposed += 1
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        if len(values) < 6:
            invalid += 1
            continue
        number, category, title, education_raw, apply_raw, source_status = values[:6]
        start_date, end_date, period = _date_range(education_raw, allow_single=True)
        apply_start, apply_end, apply_period = _date_range(apply_raw)
        status = _HEALTH_STATUS_MAP.get(source_status, "")
        href_node = cells[5].select_one("a[href]")
        href = _clean(href_node.get("href") if href_node else "")
        pg_seq = ""
        raw_url = GANGDONG_HEALTH_URL
        detail_required = False
        if href and not href.lower().startswith("javascript"):
            candidate = _public_http_url(href, base_url=GANGDONG_HEALTH_URL)
            parsed = urlparse(candidate)
            query = parse_qs(parsed.query, keep_blank_values=True)
            pg_seq = query.get("pgSeq", [""])[0]
            if (
                parsed.scheme.lower() != "https"
                or parsed.netloc.lower() != GANGDONG_HEALTH_HOST
                or parsed.path != GANGDONG_HEALTH_DETAIL_PATH
                or query != {"pgSeq": [pg_seq]}
                or not pg_seq.isdigit()
            ):
                pg_seq = ""
            raw_url = gangdong_health_detail_url(pg_seq)
            detail_required = bool(raw_url)
        if (
            not number.isdigit()
            or not category
            or not title
            or not period
            or not apply_period
            or not status
            or (source_status == "접수중" and not detail_required)
        ):
            invalid += 1
            continue
        row = _base_row(
            target,
            identity_kind="number",
            identity=number,
            title=title,
            raw_url=raw_url,
            parser=GANGDONG_HEALTH_PARSER,
        )
        _set_branch(row, "강동구 보건소")
        row.update(
            {
                "status": status,
                "reservation_available": status == "OPEN",
                "start_date": start_date,
                "end_date": end_date,
                "period": period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "apply_period": apply_period,
            }
        )
        row["raw_fields"].update(
            {
                "source_kind": "health",
                "source_number": int(number),
                "pg_seq": pg_seq,
                "program_category": category,
                "source_status": source_status,
                "list_page": page,
                "detail_required": detail_required,
            }
        )
        rows.append(row)
    return rows, invalid, exposed


def _health_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    pairs = _table_pairs(soup.select_one("table"))
    detail_title = _clean(pairs.get("프로그램명"))
    start_date, end_date, period = _date_range(pairs.get("교육일"), allow_single=True)
    apply_start, apply_end, apply_period = _date_range(pairs.get("접수기간"))
    if detail_title != _clean(row.get("title")):
        errors.append("health detail title differs from list")
    if period != _clean(row.get("period")):
        errors.append("health detail education date differs from list")
    if apply_period != _clean(row.get("apply_period")):
        errors.append("health detail reception period differs from list")
    branch = _clean(pairs.get("장소"))
    if not branch:
        errors.append("health detail venue is missing")
    else:
        _set_branch(row, branch)
    row.update(
        {
            "start_date": start_date or row.get("start_date"),
            "end_date": end_date or row.get("end_date"),
            "period": period or row.get("period"),
            "apply_start": apply_start or row.get("apply_start"),
            "apply_end": apply_end or row.get("apply_end"),
            "apply_period": apply_period or row.get("apply_period"),
            "target": _clean(pairs.get("대상")),
            "price": _clean(pairs.get("비용")),
            "contact": _clean(pairs.get("문의처")),
            "description": _clean(pairs.get("교육 내용"))[:5000],
        }
    )
    capacity_text = _clean(pairs.get("선발인원"))
    if capacity_text.replace(",", "").isdigit():
        row["capacity"] = int(capacity_text.replace(",", ""))
    return errors


def collect_gangdong_health(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    errors: list[str] = []
    if not is_gangdong_health_target(target):
        errors.append("target does not match the canonical Gangdong health education source")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    listed: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    seen_pg_seq: set[str] = set()
    pages = 0
    total_pages = 0
    source_total = 0
    exposed = 0
    invalid = 0
    duplicates = 0
    expired = 0
    source_cap_reached = False
    session: Any = None
    try:
        if not errors:
            session = make_session()
            try:
                first = _fetch(fetch, session, gangdong_health_list_url(1), timeout)
            except Exception as exc:
                errors.append(f"health page 1 fetch {type(exc).__name__}")
                first = None
            if first is not None:
                first_rows, first_invalid, first_exposed = _health_list_rows(target, first, page=1)
                source_total = max(
                    (int(row["raw_fields"]["source_number"]) for row in first_rows), default=0
                )
                total_pages = _page_count(first, "cp")
                expected_pages = max(1, math.ceil(source_total / GANGDONG_HEALTH_PAGE_SIZE))
                if not source_total or total_pages != expected_pages:
                    errors.append(
                        f"health declares {total_pages} pages for numbered total {source_total}"
                    )
                allowed_pages = max(1, int(max_pages))
                if total_pages > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"health max_pages cap reached after {allowed_pages} of {total_pages} pages"
                    )
                all_rows: list[dict[str, Any]] = []
                for page in range(1, min(total_pages, allowed_pages) + 1):
                    if page == 1:
                        current_rows, current_invalid, current_exposed = (
                            first_rows,
                            first_invalid,
                            first_exposed,
                        )
                    else:
                        try:
                            soup = _fetch(fetch, session, gangdong_health_list_url(page), timeout)
                        except Exception as exc:
                            errors.append(f"health page {page} fetch {type(exc).__name__}")
                            break
                        current_rows, current_invalid, current_exposed = _health_list_rows(
                            target, soup, page=page
                        )
                    pages += 1
                    exposed += current_exposed
                    invalid += current_invalid
                    expected_rows = min(
                        GANGDONG_HEALTH_PAGE_SIZE,
                        max(0, source_total - ((page - 1) * GANGDONG_HEALTH_PAGE_SIZE)),
                    )
                    if current_exposed != expected_rows:
                        errors.append(
                            f"health page {page} exposes {current_exposed}; expected {expected_rows}"
                        )
                    all_rows.extend(current_rows)
                numbers = [int(row["raw_fields"]["source_number"]) for row in all_rows]
                if numbers != list(range(source_total, 0, -1)):
                    errors.append("health list numbers are not continuous")
                for row in all_rows:
                    number = int(row["raw_fields"]["source_number"])
                    pg_seq = _clean(row["raw_fields"].get("pg_seq"))
                    if number in seen_numbers or (pg_seq and pg_seq in seen_pg_seq):
                        duplicates += 1
                        continue
                    seen_numbers.add(number)
                    if pg_seq:
                        seen_pg_seq.add(pg_seq)
                    if date.fromisoformat(_clean(row.get("end_date"))) < cutoff:
                        expired += 1
                        continue
                    listed.append(row)
    finally:
        _close_quietly(session)
    if invalid:
        errors.append(f"{invalid} health rows were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate health identities crossed pages")
    if source_total != len(seen_numbers):
        errors.append(f"health source total {source_total} differs from {len(seen_numbers)} numbers")
    list_complete = (
        not errors
        and pages == total_pages
        and exposed == source_total
        and not invalid
        and not duplicates
    )
    required, attempts, detail_pages, detail_errors, detail_capped = _parallel_details(
        listed,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
        parser_for=lambda _row: _health_detail,
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(f"detail_limit cap allows {attempts} of {required} health detail pages")
    rows = [_clean_row(row) for row in listed]
    if dedupe_rows is not None:
        rows = list(dedupe_rows(rows))
    meta = _finish_meta(
        rows=rows,
        candidate_count=len(listed),
        pages=pages,
        total_pages=total_pages,
        list_complete=list_complete,
        detail_required_count=required,
        detail_attempts=attempts,
        detail_pages=detail_pages,
        detail_exempt_count=len(listed) - required,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
        extra={
            "total_count": source_total,
            "source_total": source_total,
            "discovered_links": len(seen_pg_seq),
            "discovered_numbers": len(seen_numbers),
            "exposed_rows": exposed,
            "expired_count": expired,
            "invalid_count": invalid,
            "duplicate_count": duplicates,
        },
    )
    if not meta["snapshot_complete"]:
        rows = []
    return rows, GANGDONG_HEALTH_PARSER, meta


def _lll_list_rows(
    target: Any,
    soup: BeautifulSoup,
    *,
    state_code: str,
    expected_status: str,
    normalized_status: str,
    page: int,
) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    exposed = 0
    for tr in soup.select("table tbody > tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        exposed += 1
        number = _clean(cells[0].get_text(" ", strip=True)) if cells else ""
        title_node = tr.select_one("td.td_title .tit")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        identity_node = tr.select_one("[onclick*='fn_view']")
        identity_match = _LLL_ID_RE.search(
            _clean(identity_node.get("onclick") if identity_node else "")
        )
        identity = identity_match.group("id") if identity_match else ""
        date_node = tr.select_one("td.td_date")
        date_text = _clean(date_node.get_text(" ", strip=True) if date_node else "")
        values = _date_tokens(date_text)
        apply_period = ""
        period = ""
        apply_start = apply_end = start_date = end_date = ""
        if len(values) >= 4:
            apply_start, apply_end = values[0].isoformat(), values[1].isoformat()
            start_date, end_date = values[2].isoformat(), values[3].isoformat()
            apply_period = f"{apply_start} ~ {apply_end}"
            period = f"{start_date} ~ {end_date}"
        source_status = _clean(
            tr.select_one("td.td_status").get_text(" ", strip=True)
            if tr.select_one("td.td_status")
            else ""
        )
        current, capacity = _capacity(
            tr.select_one("td.td_limit").get_text(" ", strip=True)
            if tr.select_one("td.td_limit")
            else ""
        )
        if (
            not number.isdigit()
            or not title
            or not identity
            or not period
            or not apply_period
            or source_status != expected_status
        ):
            invalid += 1
            continue
        row = _base_row(
            target,
            identity_kind="program",
            identity=identity,
            title=title,
            raw_url=gangdong_lll_detail_url(identity),
            parser=GANGDONG_LLL_PARSER,
        )
        row.update(
            {
                "status": normalized_status,
                "reservation_available": normalized_status == "OPEN",
                "start_date": start_date,
                "end_date": end_date,
                "period": period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "apply_period": apply_period,
                "capacity": capacity,
                "enrolled": current,
            }
        )
        row["raw_fields"].update(
            {
                "source_kind": "lifelong",
                "source_id": identity,
                "state_code": state_code,
                "source_status": source_status,
                "list_number": int(number),
                "list_page": page,
                "detail_required": True,
            }
        )
        rows.append(row)
    return rows, invalid, exposed


def _lll_empty_state_page(soup: BeautifulSoup) -> bool:
    if _clean(soup.title.get_text(" ", strip=True) if soup.title else "") != (
        "서울특별시 강동구 평생학습관"
    ):
        return False
    expected_headers = (
        "번호",
        "이미지",
        "강의명",
        "기간",
        "정원",
        "조회수",
        "상태",
    )
    matching_tables = [
        table
        for table in soup.select("table")
        if tuple(
            _clean(node.get_text(" ", strip=True))
            for node in table.select("thead th")
        )
        == expected_headers
    ]
    if len(matching_tables) != 1:
        return False
    body = matching_tables[0].select_one("tbody")
    return bool(
        body is not None
        and not body.find_all("tr", recursive=False)
        and not soup.select(".paginate [onclick]")
    )


def _lll_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    table = soup.select_one(".tbl_wrap.view table") or soup.select_one("table")
    pairs = _table_pairs(table)
    detail_title = _clean(pairs.get("강의명"))
    apply_start, apply_end, apply_period = _date_range(pairs.get("접수 기간"))
    start_date, end_date, period = _date_range(pairs.get("강의 기간"), allow_single=True)
    if detail_title != _clean(row.get("title")):
        errors.append("lifelong detail title differs from list")
    if apply_period != _clean(row.get("apply_period")):
        errors.append("lifelong detail reception period differs from list")
    if period != _clean(row.get("period")):
        errors.append("lifelong detail education period differs from list")
    venue = _clean(pairs.get("교육 장소"))
    if not venue:
        errors.append("lifelong detail venue is missing")
    else:
        _set_branch(row, venue)
        row["venue_name"] = venue
    current, capacity = _capacity(pairs.get("신청 현황"))
    row.update(
        {
            "start_date": start_date or row.get("start_date"),
            "end_date": end_date or row.get("end_date"),
            "period": period or row.get("period"),
            "apply_start": apply_start or row.get("apply_start"),
            "apply_end": apply_end or row.get("apply_end"),
            "apply_period": apply_period or row.get("apply_period"),
            "schedule_raw": _clean(pairs.get("강의 시간")),
            "fee": _clean(pairs.get("수강료")) or "요금 별도 안내",
            "target": "대상 별도 안내",
            "capacity": capacity if capacity is not None else row.get("capacity"),
            "enrolled": current if current is not None else row.get("enrolled"),
            "description": _clean(row.get("title")),
        }
    )
    row["raw_fields"].update(
        {
            "target_evidence": "official_list_and_detail_omit_target",
            "contact_discarded": True,
            "free_form_description_discarded": True,
        }
    )
    return errors


def collect_gangdong_lll(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    errors: list[str] = []
    if not is_gangdong_lll_target(target):
        errors.append("target does not match the canonical Gangdong lifelong source")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    listed: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages = 0
    total_pages = 0
    source_total = 0
    exposed = 0
    invalid = 0
    duplicates = 0
    expired = 0
    source_cap_reached = False
    session: Any = None
    state_metrics: dict[str, dict[str, int]] = {}
    try:
        if not errors:
            session = make_session()
            for state_code, expected_status, normalized_status in GANGDONG_LLL_STATES:
                try:
                    first = _fetch(fetch, session, gangdong_lll_list_url(state_code, 1), timeout)
                except Exception as exc:
                    errors.append(f"lifelong {state_code} page 1 fetch {type(exc).__name__}")
                    continue
                first_rows, first_invalid, first_exposed = _lll_list_rows(
                    target,
                    first,
                    state_code=state_code,
                    expected_status=expected_status,
                    normalized_status=normalized_status,
                    page=1,
                )
                state_total = max(
                    (int(row["raw_fields"]["list_number"]) for row in first_rows), default=0
                )
                state_pages = 1
                for node in first.select(".paginate [onclick]"):
                    match = re.search(r"linkPage\(\s*(\d+)\s*\)", _clean(node.get("onclick")))
                    if match:
                        state_pages = max(state_pages, int(match.group(1)))
                expected_pages = max(1, math.ceil(state_total / GANGDONG_LLL_PAGE_SIZE))
                empty_state = (
                    state_total == 0
                    and first_invalid == 0
                    and first_exposed == 0
                    and state_pages == 1
                    and _lll_empty_state_page(first)
                )
                if not empty_state and (
                    not state_total or state_pages != expected_pages
                ):
                    errors.append(
                        f"lifelong {state_code} declares {state_pages} pages for numbered total {state_total}"
                    )
                allowed_pages = max(1, int(max_pages))
                if state_pages > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"lifelong {state_code} max_pages cap reached after {allowed_pages} of {state_pages} pages"
                    )
                state_rows: list[dict[str, Any]] = []
                state_exposed = 0
                state_invalid = 0
                visited = 0
                for page in range(1, min(state_pages, allowed_pages) + 1):
                    if page == 1:
                        current_rows, current_invalid, current_exposed = (
                            first_rows,
                            first_invalid,
                            first_exposed,
                        )
                    else:
                        try:
                            soup = _fetch(
                                fetch, session, gangdong_lll_list_url(state_code, page), timeout
                            )
                        except Exception as exc:
                            errors.append(
                                f"lifelong {state_code} page {page} fetch {type(exc).__name__}"
                            )
                            break
                        current_rows, current_invalid, current_exposed = _lll_list_rows(
                            target,
                            soup,
                            state_code=state_code,
                            expected_status=expected_status,
                            normalized_status=normalized_status,
                            page=page,
                        )
                    pages += 1
                    visited += 1
                    state_exposed += current_exposed
                    state_invalid += current_invalid
                    expected_rows = min(
                        GANGDONG_LLL_PAGE_SIZE,
                        max(0, state_total - ((page - 1) * GANGDONG_LLL_PAGE_SIZE)),
                    )
                    if current_exposed != expected_rows:
                        errors.append(
                            f"lifelong {state_code} page {page} exposes {current_exposed}; expected {expected_rows}"
                        )
                    state_rows.extend(current_rows)
                numbers = [int(row["raw_fields"]["list_number"]) for row in state_rows]
                if numbers != list(range(state_total, 0, -1)):
                    errors.append(f"lifelong {state_code} numbers are not continuous")
                for row in state_rows:
                    identity = _clean(row["raw_fields"].get("source_id"))
                    if identity in seen:
                        duplicates += 1
                        continue
                    seen.add(identity)
                    if date.fromisoformat(_clean(row.get("end_date"))) < cutoff:
                        expired += 1
                        continue
                    listed.append(row)
                total_pages += state_pages
                source_total += state_total
                exposed += state_exposed
                invalid += state_invalid
                state_metrics[state_code] = {
                    "total": state_total,
                    "pages": state_pages,
                    "visited": visited,
                    "exposed": state_exposed,
                    "invalid": state_invalid,
                    "structural_empty": int(empty_state),
                }
    finally:
        _close_quietly(session)
    if invalid:
        errors.append(f"{invalid} lifelong rows were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate lifelong IDs crossed active states")
    if source_total != len(seen):
        errors.append(f"lifelong source total {source_total} differs from {len(seen)} IDs")
    list_complete = (
        not errors
        and pages == total_pages
        and exposed == source_total
        and not invalid
        and not duplicates
    )
    required, attempts, detail_pages, detail_errors, detail_capped = _parallel_details(
        listed,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
        parser_for=lambda _row: _lll_detail,
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(f"detail_limit cap allows {attempts} of {required} lifelong detail pages")
    rows = [_clean_row(row) for row in listed]
    if dedupe_rows is not None:
        rows = list(dedupe_rows(rows))
    meta = _finish_meta(
        rows=rows,
        candidate_count=len(listed),
        pages=pages,
        total_pages=total_pages,
        list_complete=list_complete,
        detail_required_count=required,
        detail_attempts=attempts,
        detail_pages=detail_pages,
        detail_exempt_count=0,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
        extra={
            "total_count": source_total,
            "source_total": source_total,
            "discovered_links": len(seen),
            "exposed_rows": exposed,
            "expired_count": expired,
            "invalid_count": invalid,
            "duplicate_count": duplicates,
            "active_states": state_metrics,
        },
    )
    if not meta["snapshot_complete"]:
        rows = []
    return rows, GANGDONG_LLL_PARSER, meta


def _parallel_page_soups(
    tasks: list[tuple[str, int, str]],
    *,
    timeout: int,
    max_workers: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> tuple[dict[tuple[str, int], BeautifulSoup], list[str]]:
    """Fetch deterministic list-page tasks with one guarded session per worker."""

    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def current_session() -> Any:
        value = getattr(local, "session", None)
        if value is None:
            value = session_factory()
            local.session = value
            with sessions_lock:
                sessions.append(value)
        return value

    def fetch_one(task: tuple[str, int, str]) -> tuple[str, int, Optional[BeautifulSoup], str]:
        section, page, url = task
        try:
            return section, page, _fetch(fetcher, current_session(), url, timeout), ""
        except Exception as exc:
            return section, page, None, f"{section} page {page} fetch {type(exc).__name__}"

    results: list[tuple[str, int, Optional[BeautifulSoup], str]] = []
    try:
        if tasks:
            workers = min(GANGDONG_MAX_DETAIL_WORKERS, max(1, int(max_workers)), len(tasks))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gangdong-list") as pool:
                results = list(pool.map(fetch_one, tasks))
    finally:
        for value in sessions:
            _close_quietly(value)
    soups = {
        (section, page): soup
        for section, page, soup, error in results
        if soup is not None and not error
    }
    errors = [error for _section, _page, _soup, error in results if error]
    return soups, errors


_LIBRARY_STATUS_MAP = {
    "접수중": "OPEN",
    "대기접수": "WAITLIST",
    "접수예정": "SCHEDULED",
    "신청마감": "CLOSED",
    "접수마감": "CLOSED",
    "마감": "CLOSED",
    "종료": "CLOSED",
}


def _library_declared_pages(soup: BeautifulSoup) -> int:
    values = [
        int(node.get("data-page-no"))
        for node in soup.select("[data-page-no]")
        if _clean(node.get("data-page-no")).isdigit()
    ]
    return max(values or [1])


def _library_branch(library: str, venue: str) -> str:
    base = GANGDONG_LIBRARY_BRANCHES.get(_clean(library), "")
    place = _clean(venue)
    if not place or place == "-":
        return base
    # The official ItBookIn card for the 2026-08-30 Gangil programme contains
    # the Korean keyboard sequence for "강일도서관" without IME conversion.
    if place.lower() == "rkddlfehtjrhks":
        return "강일도서관"
    if place.startswith("중앙도서관"):
        place = f"강동{place}"
    elif place.startswith("숲속도서관"):
        place = f"강동{place}"
    if "도서관" in place:
        return place
    return _clean(f"{base} {place}")


def _set_library_branch(row: dict[str, Any], library: str, venue: str) -> None:
    library_key = _clean(library)
    _set_branch(row, _library_branch(library_key, venue))
    location = GANGDONG_LIBRARY_LOCATIONS.get(library_key)
    if not location:
        return
    address = _clean(location.get("address"))
    row.update(
        {
            "address": address,
            "venue_address": address,
            "branch_address_source": "OFFICIAL_GANGDONG_LIBRARY_DIRECTORY",
            "branch_lat": location.get("lat"),
            "branch_lon": location.get("lon"),
            "branch_coordinate_source": "GOOGLE_PLACES_TEXT_SEARCH",
            "branch_location_confidence": 100,
            "branch_location_verified": True,
            "branch_location_query": GANGDONG_LIBRARY_LOCATION_SOURCE,
        }
    )


def _library_list_rows(
    target: Any,
    soup: BeautifulSoup,
    *,
    section_key: str,
    page: int,
    cutoff: Optional[date] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    cards = soup.select(".program-list .result-box")
    _key, menu, slug, _paginated = _library_section(section_key)
    expected_prefix = f"/ch/menu/{menu}/tmpr/lctr-evnt/{slug}/"
    for card in cards:
        anchor = card if getattr(card, "name", "") == "a" else card.select_one(".info-area a.name[href]")
        title_node = card.select_one(".info-area .name")
        library_node = card.select_one(".info-area .library")
        status_node = card.select_one(".img-area .status") or card.select_one(".status")
        value_nodes = card.select(".info-area li .text")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        library = _clean(library_node.get_text(" ", strip=True) if library_node else "")
        status_text = _clean(status_node.get_text(" ", strip=True) if status_node else "")
        href = _public_http_url(anchor.get("href") if anchor else "", base_url=GANGDONG_LIBRARY_URL)
        parsed = urlparse(href)
        identity = parsed.path[len(expected_prefix) :] if parsed.path.startswith(expected_prefix) else ""
        schedule_text = _clean(value_nodes[0].get_text(" ", strip=True)) if value_nodes else ""
        target_text = _clean(value_nodes[1].get_text(" ", strip=True)) if len(value_nodes) > 1 else ""
        venue = _clean(value_nodes[2].get_text(" ", strip=True)) if len(value_nodes) > 2 else ""
        apply_text = _clean(value_nodes[3].get_text(" ", strip=True)) if len(value_nodes) > 3 else ""
        start_date, end_date, period = _date_range(schedule_text)
        historical_malformed_period = False
        schedule_dates = _date_tokens(schedule_text)
        effective_cutoff = cutoff or _today(None)
        # Four archived cards on the official source have their two endpoints
        # reversed.  They still count toward source completeness, but may only
        # be tolerated after both endpoints are safely historical.  The same
        # defect on a current/future card remains fail-closed.
        if (
            not start_date
            and len(schedule_dates) >= 2
            and max(schedule_dates[:2]) < effective_cutoff
        ):
            historical_malformed_period = True
            start_date = min(schedule_dates[:2]).isoformat()
            end_date = max(schedule_dates[:2]).isoformat()
            period = schedule_text
        apply_start, apply_end, apply_period = _date_range(apply_text)
        if (
            not title
            or library not in GANGDONG_LIBRARY_NAMES
            or status_text not in _LIBRARY_STATUS_MAP
            or parsed.scheme != "https"
            or parsed.netloc.lower() != GANGDONG_LIBRARY_HOST
            or not identity.isdigit()
            or not start_date
            or len(value_nodes) < 3
        ):
            invalid += 1
            continue
        raw_url = gangdong_library_detail_url(section_key, identity)
        row = _base_row(
            target,
            identity_kind=section_key,
            identity=identity,
            title=title,
            raw_url=raw_url,
            parser=GANGDONG_LIBRARY_PARSER,
        )
        normalized_status = _LIBRARY_STATUS_MAP[status_text]
        row.update(
            {
                "status": normalized_status,
                "status_text": status_text,
                "reservation_available": normalized_status in {"OPEN", "WAITLIST"},
                "start_date": start_date,
                "end_date": end_date,
                "period": period,
                "target": target_text,
            }
        )
        if apply_start:
            row.update(
                {
                    "apply_start": apply_start,
                    "apply_end": apply_end,
                    "apply_period": apply_period,
                }
            )
        _set_library_branch(row, library, venue)
        row["raw_fields"].update(
            {
                "source_section": section_key,
                "source_id": identity,
                "list_page": page,
                "source_library": library,
                "source_venue": venue,
                "source_status": status_text,
                "detail_required": True,
                "historical_malformed_period": historical_malformed_period,
            }
        )
        rows.append(row)
    return rows, invalid, len(cards)


def _library_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    info = soup.select_one(".program-detail .info-area")
    if info is None:
        return ["library detail container missing"]
    title_node = info.select_one("h4.title")
    library_node = info.select_one(".library")
    detail_title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    detail_library = _clean(library_node.get_text(" ", strip=True) if library_node else "")
    pairs: dict[str, list[str]] = {}
    for item in info.select("li"):
        key_node = item.select_one(".title")
        value_node = item.select_one(".text")
        key = _clean(key_node.get_text(" ", strip=True) if key_node else "").replace(" ", "")
        value = _clean(value_node.get_text(" ", strip=True) if value_node else "")
        if key and value:
            pairs.setdefault(key, []).append(value)
    schedule = (pairs.get("일정") or [""])[0]
    apply_text = (pairs.get("접수기간") or [""])[0]
    venue = (pairs.get("장소") or [""])[0]
    detail_start, detail_end, _detail_period = _date_range(schedule)
    apply_start, apply_end, apply_period = _date_range(apply_text)
    if not detail_title or detail_title != _clean(row.get("title")):
        errors.append("library detail title mismatch")
    if detail_library != _clean(row.get("raw_fields", {}).get("source_library")):
        errors.append("library detail branch label mismatch")
    if (detail_start, detail_end) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ):
        errors.append("library detail education period mismatch")
    list_venue = _clean(row.get("raw_fields", {}).get("source_venue"))
    if not venue or venue != list_venue:
        errors.append("library detail venue mismatch")
    list_apply_start = _clean(row.get("apply_start"))
    list_apply_end = _clean(row.get("apply_end"))
    if list_apply_start and (apply_start, apply_end) != (list_apply_start, list_apply_end):
        errors.append("library detail reception period mismatch")

    description_node = soup.select_one(".program-detail .middle-area .content-text")
    description = _clean(description_node.get_text(" ", strip=True) if description_node else "")
    if not description and description_node is not None:
        description = _clean(" ".join(_clean(image.get("alt")) for image in description_node.select("img[alt]")))
    row.update(
        {
            "start_date": detail_start or row.get("start_date"),
            "end_date": detail_end or row.get("end_date"),
            "target": (pairs.get("대상") or [row.get("target", "")])[0],
            "description": description[:5000],
        }
    )
    if apply_start:
        row.update(
            {
                "apply_start": apply_start,
                "apply_end": apply_end,
                "apply_period": apply_period,
            }
    )
    if venue:
        _set_library_branch(row, detail_library, venue)
    prices = [*(pairs.get("수강료") or []), *(pairs.get("재료비") or [])]
    if prices:
        row["price"] = " / ".join(value for value in prices if value and value != "-") or "무료"
    for value in pairs.get("모집인원", []):
        enrolled, capacity = _capacity(value)
        if capacity is not None:
            row["enrolled"] = enrolled
            row["capacity"] = capacity
            break
        match = re.search(r"([\d,]+)\s*명", value)
        if match and int(match.group(1).replace(",", "")) > 0:
            row["capacity"] = int(match.group(1).replace(",", ""))
    row["raw_fields"].update(
        {
            "detail_title": detail_title,
            "detail_library": detail_library,
            "detail_venue": venue,
            "venue_evidence": "library_label" if venue == "-" else "detail",
        }
    )
    return errors


def collect_gangdong_library(
    target: Any,
    timeout: int = 20,
    max_pages: int = 300,
    detail_limit: int = 200,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    errors: list[str] = []
    if not is_gangdong_library_target(target):
        errors.append("target does not match the canonical Gangdong library programme source")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    allowed_pages = max(1, int(max_pages))
    source_cap_reached = False
    first_soups: dict[str, BeautifulSoup] = {}
    declared: dict[str, int] = {}
    session: Any = None
    try:
        if not errors:
            session = make_session()
            for section_key, _menu, _slug, paginated in GANGDONG_LIBRARY_SECTIONS:
                try:
                    soup = _fetch(fetch, session, gangdong_library_list_url(section_key, 1), timeout)
                except Exception as exc:
                    errors.append(f"{section_key} page 1 fetch {type(exc).__name__}")
                    continue
                first_soups[section_key] = soup
                pages = _library_declared_pages(soup) if paginated else 1
                declared[section_key] = pages
                if pages > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"{section_key} max_pages cap reached after {allowed_pages} of {pages} pages"
                    )
    finally:
        _close_quietly(session)

    tasks: list[tuple[str, int, str]] = []
    for section_key, _menu, _slug, paginated in GANGDONG_LIBRARY_SECTIONS:
        total = declared.get(section_key, 0)
        for page in range(2, min(total, allowed_pages) + 1):
            tasks.append((section_key, page, gangdong_library_list_url(section_key, page)))
        if paginated and total and total <= allowed_pages:
            tasks.append((section_key, total + 1, gangdong_library_list_url(section_key, total + 1)))
    fetched, fetch_errors = _parallel_page_soups(
        tasks,
        timeout=timeout,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
    )
    errors.extend(fetch_errors)

    listed: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    pages = 0
    total_pages = sum(declared.values())
    sentinel_pages = 0
    exposed = 0
    invalid = 0
    duplicates = 0
    expired = 0
    section_metrics: dict[str, dict[str, Any]] = {}
    for section_key, _menu, _slug, paginated in GANGDONG_LIBRARY_SECTIONS:
        total = declared.get(section_key, 0)
        section_exposed = 0
        section_invalid = 0
        section_visited = 0
        section_rows: list[dict[str, Any]] = []
        for page in range(1, min(total, allowed_pages) + 1):
            soup = first_soups.get(section_key) if page == 1 else fetched.get((section_key, page))
            if soup is None:
                continue
            current_rows, current_invalid, current_exposed = _library_list_rows(
                target, soup, section_key=section_key, page=page, cutoff=cutoff
            )
            pages += 1
            section_visited += 1
            section_exposed += current_exposed
            section_invalid += current_invalid
            section_rows.extend(current_rows)
            if paginated and total > 1:
                if page < total and current_exposed != GANGDONG_LIBRARY_PAGE_SIZE:
                    errors.append(
                        f"{section_key} page {page} exposes {current_exposed}; "
                        f"expected {GANGDONG_LIBRARY_PAGE_SIZE}"
                    )
                if page == total and not (1 <= current_exposed <= GANGDONG_LIBRARY_PAGE_SIZE):
                    errors.append(f"{section_key} final page exposes {current_exposed}")
            elif paginated and current_exposed > GANGDONG_LIBRARY_PAGE_SIZE:
                errors.append(f"{section_key} single page exposes {current_exposed}")
        if paginated and total and total <= allowed_pages:
            sentinel = fetched.get((section_key, total + 1))
            if sentinel is not None:
                sentinel_pages += 1
                sentinel_count = len(sentinel.select(".program-list .result-box"))
                if sentinel_count:
                    errors.append(
                        f"{section_key} page {total + 1} exposes {sentinel_count} after declared end"
                    )
        for row in section_rows:
            identity = _clean(row.get("raw_fields", {}).get("source_id"))
            if identity in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(identity)
            if date.fromisoformat(_clean(row.get("end_date"))) < cutoff:
                expired += 1
                continue
            listed.append(row)
        exposed += section_exposed
        invalid += section_invalid
        section_metrics[section_key] = {
            "pages": total,
            "visited": section_visited,
            "exposed": section_exposed,
            "invalid": section_invalid,
            "paginated": paginated,
        }
    if invalid:
        errors.append(f"{invalid} library programme rows were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate library IDs crossed declared sections/pages")
    if exposed != len(seen_ids) + duplicates + invalid:
        errors.append("library exposed-card accounting mismatch")
    expected_sentinels = sum(
        1
        for section_key, _menu, _slug, paginated in GANGDONG_LIBRARY_SECTIONS
        if paginated and declared.get(section_key, 0) <= allowed_pages
    )
    list_complete = (
        not errors
        and pages == total_pages
        and sentinel_pages == expected_sentinels
        and not invalid
        and not duplicates
    )
    required, attempts, detail_pages, detail_errors, detail_capped = _parallel_details(
        listed,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
        parser_for=lambda _row: _library_detail,
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(f"detail_limit cap allows {attempts} of {required} library detail pages")
    rows = [_clean_row(row) for row in listed]
    if dedupe_rows is not None:
        rows = list(dedupe_rows(rows))
    meta = _finish_meta(
        rows=rows,
        candidate_count=len(listed),
        pages=pages,
        total_pages=total_pages,
        list_complete=list_complete,
        detail_required_count=required,
        detail_attempts=attempts,
        detail_pages=detail_pages,
        detail_exempt_count=0,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
        extra={
            "total_count": len(seen_ids),
            "source_total": len(seen_ids),
            "discovered_links": len(seen_ids),
            "exposed_rows": exposed,
            "expired_count": expired,
            "invalid_count": invalid,
            "duplicate_count": duplicates,
            "sentinel_pages": sentinel_pages,
            "source_sections": section_metrics,
        },
    )
    if not meta["snapshot_complete"]:
        rows = []
    return rows, GANGDONG_LIBRARY_PARSER, meta


_FIFTYPLUS_STATUS_MAP = {
    "수강신청": "OPEN",
    "대기신청": "WAITLIST",
    "신청예정": "SCHEDULED",
    "신청마감": "CLOSED",
    "접수마감": "CLOSED",
    "마감": "CLOSED",
}


def _50plus_cell_value(cell: Any) -> str:
    value = _clean(cell.get_text(" ", strip=True))
    label = cell.select_one("label")
    prefix = _clean(label.get_text(" ", strip=True) if label else "")
    if prefix and value.startswith(prefix):
        return _clean(value[len(prefix) :])
    return value


def _50plus_page_number(href: Any) -> int:
    parsed = urlparse(_clean(href))
    values = parse_qs(parsed.query, keep_blank_values=True).get("page", [])
    return int(values[0]) if len(values) == 1 and values[0].isdigit() else 0


def _50plus_list_rows(
    target: Any, soup: BeautifulSoup, *, page: int
) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    source_rows = soup.select(".campus-course-list-table tbody tr")
    for tr in source_rows:
        cells = tr.find_all("td", recursive=False)
        anchor = tr.select_one("a[href*='education-detail.do?id=']")
        values = [_50plus_cell_value(cell) for cell in cells]
        href = _public_http_url(anchor.get("href") if anchor else "", base_url=GANGDONG_50PLUS_URL)
        parsed = urlparse(href)
        query = parse_qs(parsed.query, keep_blank_values=True)
        identity_values = query.get("id", [])
        identity = identity_values[0] if len(identity_values) == 1 else ""
        status_text = _clean(anchor.get_text(" ", strip=True) if anchor else "")
        apply_start, apply_end, apply_period = _date_range(values[4] if len(values) > 4 else "")
        start_date, end_date, period = _date_range(values[5] if len(values) > 5 else "")
        capacity_text = values[8].replace(",", "") if len(values) > 8 else ""
        if (
            len(values) < 10
            or values[0] != "강동센터"
            or not values[3]
            or not apply_start
            or not start_date
            or status_text not in _FIFTYPLUS_STATUS_MAP
            or parsed.scheme != "https"
            or parsed.netloc.lower() != GANGDONG_50PLUS_HOST
            or parsed.path != GANGDONG_50PLUS_DETAIL_PATH
            or not identity.isdigit()
            or not capacity_text.isdigit()
        ):
            invalid += 1
            continue
        raw_url = gangdong_50plus_detail_url(identity)
        row = _base_row(
            target,
            identity_kind="education",
            identity=identity,
            title=values[3],
            raw_url=raw_url,
            parser=GANGDONG_50PLUS_PARSER,
        )
        normalized_status = _FIFTYPLUS_STATUS_MAP[status_text]
        row.update(
            {
                "status": normalized_status,
                "status_text": status_text,
                "reservation_available": normalized_status in {"OPEN", "WAITLIST"},
                "start_date": start_date,
                "end_date": end_date,
                "period": period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "apply_period": apply_period,
                "instructor": values[6],
                "price": values[7],
                "capacity": int(capacity_text),
            }
        )
        _set_branch(row, "강동50플러스센터")
        row["raw_fields"].update(
            {
                "source_id": identity,
                "list_page": page,
                "source_institution": values[0],
                "source_middle_category": values[1],
                "source_category": values[2],
                "source_status": status_text,
                "detail_required": True,
            }
        )
        rows.append(row)
    return rows, invalid, len(source_rows)


def _50plus_branch(venue: str) -> str:
    value = _clean(venue)
    if not value:
        return ""
    if "강동50플러스센터" in value:
        return value
    return _clean(f"강동50플러스센터 {value}")


def _50plus_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    title_node = soup.select_one("h2.show-title")
    content = soup.select_one(".course-content")
    detail_title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if not detail_title or detail_title != _clean(row.get("title")):
        errors.append("50plus detail title mismatch")
    if content is None:
        return [*errors, "50plus detail course content missing"]
    content_text = _clean(content.get_text(" ", strip=True))
    detail_dates = {value.isoformat() for value in _date_tokens(content_text)}
    start_date = _clean(row.get("start_date"))
    end_date = _clean(row.get("end_date"))
    if start_date not in detail_dates or end_date not in detail_dates:
        errors.append("50plus detail education period mismatch")
    venue = ""
    for tr in content.select("table tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        key = _clean(cells[0].get_text(" ", strip=True)).replace(" ", "")
        if key == "교육장소":
            venue = _clean(cells[1].get_text(" ", strip=True))
            break
    if not venue or venue == "-":
        errors.append("50plus detail education venue missing")
    else:
        _set_branch(row, _50plus_branch(venue))
    row["description"] = content_text[:5000]
    row["raw_fields"].update(
        {
            "detail_title": detail_title,
            "detail_venue": venue,
            "detail_date_evidence": sorted(detail_dates),
        }
    )
    return errors


def collect_gangdong_50plus(
    target: Any,
    timeout: int = 20,
    max_pages: int = 30,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    errors: list[str] = []
    if not is_gangdong_50plus_target(target):
        errors.append("target does not match the canonical Gangdong 50plus education source")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    allowed_pages = max(1, int(max_pages))
    source_cap_reached = False
    known_soups: dict[int, BeautifulSoup] = {}
    total_pages = 0
    session: Any = None
    try:
        if not errors:
            session = make_session()
            current_page = 1
            try:
                current = _fetch(fetch, session, gangdong_50plus_list_url(1), timeout)
            except Exception as exc:
                errors.append(f"50plus page 1 fetch {type(exc).__name__}")
                current = None
            while current is not None:
                known_soups[current_page] = current
                next_anchor = current.select_one(".pagination-btn-group a.next[href]")
                if next_anchor is None:
                    linked = [
                        _50plus_page_number(anchor.get("href"))
                        for anchor in current.select(".pagination-btn-group a[href]")
                    ]
                    total_pages = max([current_page, *linked])
                    break
                boundary = _50plus_page_number(next_anchor.get("href"))
                if boundary <= current_page:
                    errors.append("50plus pagination boundary is not increasing")
                    break
                if boundary > allowed_pages:
                    total_pages = boundary
                    source_cap_reached = True
                    errors.append(
                        f"50plus max_pages cap reached before pagination boundary {boundary}"
                    )
                    break
                current_page = boundary
                try:
                    current = _fetch(
                        fetch, session, gangdong_50plus_list_url(current_page), timeout
                    )
                except Exception as exc:
                    errors.append(f"50plus page {current_page} fetch {type(exc).__name__}")
                    current = None
    finally:
        _close_quietly(session)
    if total_pages > allowed_pages:
        source_cap_reached = True
        errors.append(
            f"50plus max_pages cap reached after {allowed_pages} of {total_pages} pages"
        )
    tasks = [
        ("50plus", page, gangdong_50plus_list_url(page))
        for page in range(1, min(total_pages, allowed_pages) + 1)
        if page not in known_soups
    ]
    if total_pages and total_pages <= allowed_pages:
        tasks.append(("50plus", total_pages + 1, gangdong_50plus_list_url(total_pages + 1)))
    fetched, fetch_errors = _parallel_page_soups(
        tasks,
        timeout=timeout,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
    )
    errors.extend(fetch_errors)
    for (_section, page), soup in fetched.items():
        known_soups[page] = soup

    listed: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    pages = 0
    exposed = 0
    invalid = 0
    duplicates = 0
    expired = 0
    for page in range(1, min(total_pages, allowed_pages) + 1):
        soup = known_soups.get(page)
        if soup is None:
            continue
        current_rows, current_invalid, current_exposed = _50plus_list_rows(
            target, soup, page=page
        )
        pages += 1
        exposed += current_exposed
        invalid += current_invalid
        if page < total_pages and current_exposed != GANGDONG_50PLUS_PAGE_SIZE:
            errors.append(
                f"50plus page {page} exposes {current_exposed}; expected {GANGDONG_50PLUS_PAGE_SIZE}"
            )
        if page == total_pages and not (1 <= current_exposed <= GANGDONG_50PLUS_PAGE_SIZE):
            errors.append(f"50plus final page exposes {current_exposed}")
        for row in current_rows:
            identity = _clean(row.get("raw_fields", {}).get("source_id"))
            if identity in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(identity)
            if date.fromisoformat(_clean(row.get("end_date"))) < cutoff:
                expired += 1
                continue
            listed.append(row)
    sentinel_pages = 0
    if total_pages and total_pages <= allowed_pages:
        sentinel = known_soups.get(total_pages + 1)
        if sentinel is not None:
            sentinel_pages = 1
            sentinel_count = len(sentinel.select(".campus-course-list-table tbody tr"))
            if sentinel_count:
                errors.append(
                    f"50plus page {total_pages + 1} exposes {sentinel_count} after declared end"
                )
    if invalid:
        errors.append(f"{invalid} 50plus rows were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate 50plus IDs crossed pages")
    if exposed != len(seen_ids) + duplicates + invalid:
        errors.append("50plus exposed-row accounting mismatch")
    list_complete = (
        not errors
        and pages == total_pages
        and sentinel_pages == 1
        and not invalid
        and not duplicates
    )
    required, attempts, detail_pages, detail_errors, detail_capped = _parallel_details(
        listed,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
        parser_for=lambda _row: _50plus_detail,
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(f"detail_limit cap allows {attempts} of {required} 50plus detail pages")
    rows = [_clean_row(row) for row in listed]
    if dedupe_rows is not None:
        rows = list(dedupe_rows(rows))
    meta = _finish_meta(
        rows=rows,
        candidate_count=len(listed),
        pages=pages,
        total_pages=total_pages,
        list_complete=list_complete,
        detail_required_count=required,
        detail_attempts=attempts,
        detail_pages=detail_pages,
        detail_exempt_count=0,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
        extra={
            "total_count": len(seen_ids),
            "source_total": len(seen_ids),
            "discovered_links": len(seen_ids),
            "exposed_rows": exposed,
            "expired_count": expired,
            "invalid_count": invalid,
            "duplicate_count": duplicates,
            "sentinel_pages": sentinel_pages,
        },
    )
    if not meta["snapshot_complete"]:
        rows = []
    return rows, GANGDONG_50PLUS_PARSER, meta


_JUMIN_STATUS_MAP = {
    "모집대기": "SCHEDULED",
    "모집중": "OPEN",
    "모집마감": "CLOSED",
}


def _jumin_declared_pages(soup: BeautifulSoup) -> int:
    pages = [1]
    for node in soup.select(".wrap_paging [onclick]"):
        match = re.search(r"linkPage\(\s*(\d+)\s*\)", _clean(node.get("onclick")))
        if match:
            pages.append(int(match.group(1)))
    return max(pages)


def _jumin_capacity(value: Any) -> tuple[Optional[int], Optional[int]]:
    text = _clean(value)
    current_match = re.search(r"([\d,]+)\s*명?\s*/", text)
    capacity_match = re.search(r"/\s*([\d,]+)\s*명?", text)
    current = int(current_match.group(1).replace(",", "")) if current_match else None
    capacity = int(capacity_match.group(1).replace(",", "")) if capacity_match else None
    return current, capacity


def _jumin_detail_branch(value: Any) -> str:
    parts = [_clean(part) for part in str(value or "").split("/") if _clean(part)]
    if len(parts) >= 2 and parts[1].startswith(parts[0]):
        parts = parts[1:]
    return _clean(" ".join(parts))


def _jumin_list_rows(
    target: Any,
    soup: BeautifulSoup,
    *,
    page: int,
) -> tuple[list[dict[str, Any]], int, int, int]:
    rows: list[dict[str, Any]] = []
    invalid = 0
    exposed = 0
    placeholders = 0
    for card in soup.select(".bbs-program_w li.group"):
        title_node = card.select_one(".tit strong")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        identity_node = card.select_one(".tit [onclick*='fn_view']")
        identity_match = _LLL_ID_RE.search(
            _clean(identity_node.get("onclick") if identity_node else "")
        )
        identity = identity_match.group("id") if identity_match else ""
        venue_node = card.select_one(".place")
        venue = _clean(venue_node.get_text(" ", strip=True) if venue_node else "")
        list_fields: dict[str, str] = {}
        for node in card.select("ul.sort > li"):
            label = node.select_one(".t")
            value = node.select_one(".cont")
            if label is not None and value is not None:
                list_fields[_clean(label.get_text(" ", strip=True))] = _clean(
                    value.get_text(" ", strip=True)
                )
        right_values = [
            _clean(node.get_text(" ", strip=True))
            for node in card.select(".r.pc_only strong")
        ]
        owner = right_values[0] if len(right_values) >= 1 else ""
        source_status = right_values[-1] if len(right_values) >= 2 else ""
        if not any((identity, title, venue, list_fields, owner, source_status)):
            placeholders += 1
            continue
        exposed += 1
        apply_start, apply_end, apply_period = _date_range(
            list_fields.get("신청기간")
        )
        start_date, end_date, period = _date_range(list_fields.get("교육기간"))
        normalized_status = _JUMIN_STATUS_MAP.get(source_status, "")
        enrolled, capacity = _jumin_capacity(list_fields.get("신청인원 / 정원"))
        category_node = card.select_one(".tit .label")
        category = _clean(
            category_node.get_text(" ", strip=True) if category_node else ""
        )
        raw_url = gangdong_jumin_detail_url(identity)
        if (
            not identity
            or not title
            or not raw_url
            or not venue
            or not owner
            or owner not in venue
            or not category
            or not normalized_status
            or not apply_period
            or not period
            or not _clean(list_fields.get("교육시간"))
        ):
            invalid += 1
            continue
        row = _base_row(
            target,
            identity_kind="resident_program",
            identity=identity,
            title=title,
            raw_url=raw_url,
            parser=GANGDONG_JUMIN_PARSER,
        )
        row.update(
            {
                "status": normalized_status,
                "reservation_available": normalized_status == "OPEN",
                "start_date": start_date,
                "end_date": end_date,
                "period": period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "apply_period": apply_period,
                "schedule": _clean(list_fields.get("교육시간")),
                "schedule_raw": _clean(list_fields.get("교육시간")),
                "capacity": capacity,
                "enrolled": enrolled,
                "category": category,
            }
        )
        _set_branch(row, venue)
        row["raw_fields"].update(
            {
                "source_kind": "resident_centre",
                "source_id": identity,
                "source_status": source_status,
                "source_category": category,
                "source_owner": owner,
                "source_venue": venue,
                "list_page": page,
                "detail_required": True,
            }
        )
        rows.append(row)
    return rows, invalid, exposed, placeholders


def _jumin_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    identity = _clean(row.get("raw_fields", {}).get("source_id"))
    hidden_identity = soup.select_one("form#searchForm input[name='gn_seq']")
    detail_identity = _clean(hidden_identity.get("value") if hidden_identity else "")
    title_node = soup.select_one("form#searchForm .top h5.t strong")
    detail_title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    pairs: dict[str, str] = {}
    for item in soup.select("form#searchForm .group .box.grey li.item"):
        label = item.select_one(".dt")
        value = item.select_one(".dd")
        if label is not None and value is not None:
            pairs[_clean(label.get_text(" ", strip=True))] = _clean(
                value.get_text(" ", strip=True)
            )
    apply_start, apply_end, apply_period = _date_range(pairs.get("접수기간"))
    start_date, end_date, period = _date_range(pairs.get("교육기간"))
    detail_branch = _jumin_detail_branch(pairs.get("강의지역"))
    if detail_identity != identity:
        errors.append("resident-centre detail identity mismatch")
    if detail_title != _clean(row.get("title")):
        errors.append("resident-centre detail title mismatch")
    if period != _clean(row.get("period")):
        errors.append("resident-centre detail education period mismatch")
    if apply_period != _clean(row.get("apply_period")):
        errors.append("resident-centre detail reception period mismatch")
    if _clean(pairs.get("교육시간")) != _clean(row.get("schedule")):
        errors.append("resident-centre detail schedule mismatch")
    if detail_branch != _clean(row.get("branch")):
        errors.append("resident-centre detail branch mismatch")
    if not detail_branch:
        errors.append("resident-centre detail branch missing")
    else:
        _set_branch(row, detail_branch)
    description = ""
    for group in soup.select("form#searchForm .group"):
        heading = group.select_one("h5.tit-st1")
        if _clean(heading.get_text(" ", strip=True) if heading else "") == "프로그램 상세 내용":
            body = group.select_one(".box.grey")
            description = _clean(body.get_text(" ", strip=True) if body else "")[:5000]
            break
    row.update(
        {
            "start_date": start_date or row.get("start_date"),
            "end_date": end_date or row.get("end_date"),
            "period": period or row.get("period"),
            "apply_start": apply_start or row.get("apply_start"),
            "apply_end": apply_end or row.get("apply_end"),
            "apply_period": apply_period or row.get("apply_period"),
            "schedule": _clean(pairs.get("교육시간")) or row.get("schedule"),
            "schedule_raw": _clean(pairs.get("교육시간"))
            or row.get("schedule_raw"),
            "price": _clean(pairs.get("강의료")),
            "fee": _clean(pairs.get("강의료")) or "요금 별도 안내",
            "target": _clean(pairs.get("모집대상")) or "대상 별도 안내",
            "instructor": _clean(pairs.get("강사명")),
            "description": description,
        }
    )
    row["raw_fields"].update(
        {
            "detail_identity": detail_identity,
            "detail_title": detail_title,
            "detail_region": _clean(pairs.get("강의지역")),
            "target_source": (
                "detail_모집대상"
                if _clean(pairs.get("모집대상"))
                else "official_detail_omits_target"
            ),
            "fee_source": (
                "detail_강의료"
                if _clean(pairs.get("강의료"))
                else "official_detail_omits_fee"
            ),
        }
    )
    return errors


def collect_gangdong_jumin(
    target: Any,
    timeout: int = 20,
    max_pages: int = 100,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    errors: list[str] = []
    if not is_gangdong_jumin_target(target):
        errors.append("target does not match the canonical Gangdong resident-centre source")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    first: Optional[BeautifulSoup] = None
    session: Any = None
    try:
        if not errors:
            session = make_session()
            first = _fetch(fetch, session, gangdong_jumin_list_url(1), timeout)
    except Exception as exc:
        errors.append(f"resident-centre page 1 fetch {type(exc).__name__}")
    finally:
        _close_quietly(session)
    total_pages = _jumin_declared_pages(first) if first is not None else 0
    allowed_pages = max(1, int(max_pages))
    source_cap_reached = total_pages > allowed_pages
    if source_cap_reached:
        errors.append(
            f"resident-centre max_pages cap reached after {allowed_pages} of {total_pages} pages"
        )
    tasks = [
        ("jumin", page, gangdong_jumin_list_url(page))
        for page in range(2, min(total_pages, allowed_pages) + 1)
    ]
    if total_pages and not source_cap_reached:
        tasks.append(
            ("jumin", total_pages + 1, gangdong_jumin_list_url(total_pages + 1))
        )
    soups, fetch_errors = _parallel_page_soups(
        tasks,
        timeout=timeout,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
    )
    errors.extend(fetch_errors)
    if first is not None:
        soups[("jumin", 1)] = first
    listed: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    pages = 0
    exposed = 0
    invalid = 0
    duplicates = 0
    expired = 0
    source_placeholders = 0
    for page in range(1, min(total_pages, allowed_pages) + 1):
        soup = soups.get(("jumin", page))
        if soup is None:
            continue
        pages += 1
        current_rows, current_invalid, current_exposed, placeholders = _jumin_list_rows(
            target, soup, page=page
        )
        exposed += current_exposed
        invalid += current_invalid
        source_placeholders += placeholders
        if placeholders:
            errors.append(f"resident-centre page {page} exposes {placeholders} empty placeholders")
        if page < total_pages and current_exposed != GANGDONG_JUMIN_PAGE_SIZE:
            errors.append(
                f"resident-centre page {page} exposes {current_exposed}; expected {GANGDONG_JUMIN_PAGE_SIZE}"
            )
        if page == total_pages and not (1 <= current_exposed <= GANGDONG_JUMIN_PAGE_SIZE):
            errors.append(f"resident-centre final page exposes {current_exposed}")
        for row in current_rows:
            identity = _clean(row.get("raw_fields", {}).get("source_id"))
            if identity in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(identity)
            if date.fromisoformat(_clean(row.get("end_date"))) < cutoff:
                expired += 1
                continue
            listed.append(row)
    sentinel_pages = 0
    sentinel_placeholders = 0
    if total_pages and not source_cap_reached:
        sentinel = soups.get(("jumin", total_pages + 1))
        if sentinel is not None:
            sentinel_pages = 1
            _rows, sentinel_invalid, sentinel_exposed, sentinel_placeholders = (
                _jumin_list_rows(target, sentinel, page=total_pages + 1)
            )
            if sentinel_invalid or sentinel_exposed:
                errors.append(
                    f"resident-centre page {total_pages + 1} exposes data after declared end"
                )
    if invalid:
        errors.append(f"{invalid} resident-centre rows were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate resident-centre IDs crossed pages")
    if exposed != len(seen_ids) + duplicates + invalid:
        errors.append("resident-centre exposed-row accounting mismatch")
    list_complete = (
        not errors
        and pages == total_pages
        and sentinel_pages == 1
        and not invalid
        and not duplicates
    )
    required, attempts, detail_pages, detail_errors, detail_capped = _parallel_details(
        listed,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
        parser_for=lambda _row: _jumin_detail,
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {attempts} of {required} resident-centre detail pages"
        )
    rows = [_clean_row(row) for row in listed]
    if dedupe_rows is not None:
        rows = list(dedupe_rows(rows))
    meta = _finish_meta(
        rows=rows,
        candidate_count=len(listed),
        pages=pages,
        total_pages=total_pages,
        list_complete=list_complete,
        detail_required_count=required,
        detail_attempts=attempts,
        detail_pages=detail_pages,
        detail_exempt_count=0,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
        extra={
            "total_count": len(seen_ids),
            "source_total": len(seen_ids),
            "discovered_links": len(seen_ids),
            "exposed_rows": exposed,
            "expired_count": expired,
            "invalid_count": invalid,
            "duplicate_count": duplicates,
            "sentinel_pages": sentinel_pages,
            "source_placeholders": source_placeholders,
            "sentinel_placeholders": sentinel_placeholders,
        },
    )
    if not meta["snapshot_complete"]:
        rows = []
    return rows, GANGDONG_JUMIN_PARSER, meta


_SLC_STATUS_MAP = {
    1: "SCHEDULED",
    2: "OPEN",
    3: "CLOSED",
    4: "CLOSED",
    5: "CLOSED",
    6: "CLOSED",
    7: "CLOSED",
}


def _integer(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slc_html_text(value: Any) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    return _clean(BeautifulSoup(raw, "lxml").get_text(" ", strip=True))


def _slc_date(value: Any) -> str:
    text = _clean(value)
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[ T]|$)", text)
    if not match:
        return ""
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return ""


def _slc_envelope(
    payload: Mapping[str, Any],
    *,
    cutoff: date,
    require_source_date: bool = True,
) -> tuple[Optional[Mapping[str, Any]], list[str]]:
    errors: list[str] = []
    if _integer(payload.get("code")) != 10000:
        errors.append(f"Future-On API code {_clean(payload.get('code')) or 'missing'}")
    body = payload.get("body")
    if not isinstance(body, Mapping):
        errors.append("Future-On API body is not an object")
        body = None
    if require_source_date:
        source_date = _slc_date(payload.get("current_date"))
        if source_date != cutoff.isoformat():
            errors.append(
                f"Future-On source date {source_date or 'missing'} differs from {cutoff.isoformat()}"
            )
    return body, errors


def _slc_list_payload(term_id: int, page: int) -> dict[str, str]:
    return {
        "termId": str(term_id),
        "termIsAvailable": "1",
        "isAvailable": "1",
        "page": str(max(1, int(page))),
        "count": str(GANGDONG_SLC_PAGE_SIZE),
        "includeAttributeList": "1",
        "adminPage": "0",
        "studentPage": "0",
        "statusCodeList": "1,2,3,4,5,6,7",
    }


def _slc_detail_payload(term_id: int, course_id: str) -> dict[str, str]:
    return {
        "termId": str(term_id),
        "id": _clean(course_id),
        "includeAttributeList": "1",
        "isAvailable": "1",
    }


def _slc_targets(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    names = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        if _clean(item.get("attribute_category_code")) != "USER_TYPE":
            continue
        name = _clean(item.get("attribute_name"))
        if name and name not in names:
            names.append(name)
    return ", ".join(names)


def _slc_course_row(
    target: Any,
    item: Mapping[str, Any],
    *,
    menu_id: int,
    term_id: int,
    page: int,
) -> tuple[Optional[dict[str, Any]], list[str]]:
    errors: list[str] = []
    identity_value = _integer(item.get("id"))
    identity = str(identity_value) if identity_value is not None and identity_value > 0 else ""
    source_term = _integer(item.get("term_id"))
    title = _clean(item.get("service_title"))
    start_date = _slc_date(item.get("start_date"))
    end_date = _slc_date(item.get("end_date"))
    apply_start = _slc_date(item.get("registration_start_date"))
    apply_end = _slc_date(item.get("registration_end_date"))
    status_code = _integer(item.get("status_code"))
    normalized_status = _SLC_STATUS_MAP.get(status_code or -1, "")
    raw_url = gangdong_slc_detail_url(menu_id, term_id, identity)
    if not identity:
        errors.append("Future-On course ID missing")
    if source_term != term_id:
        errors.append("Future-On term ID mismatch")
    if not title:
        errors.append("Future-On service title missing")
    if not start_date or not end_date or end_date < start_date:
        errors.append("Future-On education period malformed")
    if bool(apply_start) != bool(apply_end) or (
        apply_start and apply_end and apply_end < apply_start
    ):
        errors.append("Future-On reception period malformed")
    if not normalized_status:
        errors.append("Future-On status code unknown")
    if not raw_url:
        errors.append("Future-On public detail URL malformed")
    if errors:
        return None, errors
    properties = item.get("properties")
    if not isinstance(properties, Mapping):
        properties = {}
    capacity = _integer(item.get("max_student_count"))
    student_count = _integer(item.get("student_count"))
    auditing_count = _integer(item.get("auditing_count"))
    enrolled_values = [value for value in (student_count, auditing_count) if value is not None]
    enrolled = sum(enrolled_values) if enrolled_values else None
    period = f"{start_date} ~ {end_date}"
    apply_period = (
        f"{apply_start} ~ {apply_end}" if apply_start and apply_end else ""
    )
    row = _base_row(
        target,
        identity_kind="future_on_course",
        identity=identity,
        title=title,
        raw_url=raw_url,
        parser=GANGDONG_SLC_PARSER,
    )
    row.update(
        {
            "status": normalized_status,
            "reservation_available": normalized_status == "OPEN",
            "start_date": start_date,
            "end_date": end_date,
            "period": period,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "apply_period": apply_period,
            "capacity": capacity,
            "enrolled": enrolled,
            "price": _clean(item.get("price")),
            "target": _slc_targets(item.get("attribute_list")),
        }
    )
    row["raw_fields"].update(
        {
            "source_kind": "future_on",
            "source_id": identity,
            "menu_id": menu_id,
            "term_id": term_id,
            "term_name": _clean(item.get("term_name")),
            "source_status_code": status_code,
            "source_location": _slc_html_text(properties.get("location")),
            "source_institution": _clean(
                item.get("course_code_institution_name") or item.get("institution_name")
            ),
            "list_page": page,
            "detail_required": True,
        }
    )
    return row, []


def _slc_branch(location: str, title: str, course_details: str) -> str:
    place = _clean(location)
    if place:
        place = re.sub(r"^장소\s*:\s*", "", place)
        place = re.split(r"\s+주소\s*:\s*", place, maxsplit=1)[0]
        place = re.sub(
            r"\s*\(\s*[^)]*(?:로|길|면로)\s*\d+[^)]*\)\s*$", "", place
        )
        return _clean(place)[:100]
    details = _clean(course_details)
    if "실시간 온라인" in details:
        return "온라인 실시간"
    match = re.match(r"^\[([^\]]+)\]", _clean(title))
    if match:
        candidate = _clean(match.group(1))
        if candidate.endswith(("센터", "도서관", "미술관", "박물관", "회관")):
            return candidate[:100]
    return ""


def _slc_detail(
    row: dict[str, Any],
    payload: Mapping[str, Any],
    cutoff: date,
) -> list[str]:
    body, errors = _slc_envelope(payload, cutoff=cutoff)
    if body is None:
        return errors
    identity = _clean(row.get("raw_fields", {}).get("source_id"))
    term_id = _integer(row.get("raw_fields", {}).get("term_id"))
    detail_identity = _integer(body.get("id"))
    detail_term = _integer(body.get("term_id"))
    detail_title = _clean(body.get("service_title"))
    start_date = _slc_date(body.get("start_date"))
    end_date = _slc_date(body.get("end_date"))
    apply_start = _slc_date(body.get("registration_start_date"))
    apply_end = _slc_date(body.get("registration_end_date"))
    detail_status = _integer(body.get("status_code"))
    if str(detail_identity or "") != identity:
        errors.append("Future-On detail identity mismatch")
    if detail_term != term_id:
        errors.append("Future-On detail term mismatch")
    if detail_title != _clean(row.get("title")):
        errors.append("Future-On detail title mismatch")
    if f"{start_date} ~ {end_date}" != _clean(row.get("period")):
        errors.append("Future-On detail education period mismatch")
    expected_apply = (
        f"{apply_start} ~ {apply_end}" if apply_start and apply_end else ""
    )
    if expected_apply != _clean(row.get("apply_period")):
        errors.append("Future-On detail reception period mismatch")
    if detail_status != _integer(row.get("raw_fields", {}).get("source_status_code")):
        errors.append("Future-On detail status mismatch")
    properties = body.get("properties")
    if not isinstance(properties, Mapping):
        properties = {}
        errors.append("Future-On detail properties missing")
    location = _slc_html_text(properties.get("location"))
    if location != _clean(row.get("raw_fields", {}).get("source_location")):
        errors.append("Future-On detail location differs from list")
    course_details = _slc_html_text(properties.get("course_details"))
    branch = _slc_branch(location, detail_title, course_details)
    if not branch:
        errors.append("Future-On detail branch/online venue missing")
    else:
        _set_branch(row, branch)
    capacity = _integer(body.get("max_student_count"))
    student_count = _integer(body.get("student_count"))
    auditing_count = _integer(body.get("auditing_count"))
    enrolled_values = [value for value in (student_count, auditing_count) if value is not None]
    row.update(
        {
            "start_date": start_date or row.get("start_date"),
            "end_date": end_date or row.get("end_date"),
            "period": f"{start_date} ~ {end_date}" if start_date and end_date else row.get("period"),
            "apply_start": apply_start,
            "apply_end": apply_end,
            "apply_period": expected_apply,
            "capacity": capacity,
            "enrolled": sum(enrolled_values) if enrolled_values else None,
            "price": _clean(body.get("price")),
            "target": _slc_targets(body.get("attribute_list")),
            "contact": _slc_html_text(properties.get("contact")),
            "description": course_details[:5000],
        }
    )
    row["raw_fields"].update(
        {
            "detail_identity": detail_identity,
            "detail_term_id": detail_term,
            "detail_title": detail_title,
            "detail_location": location,
            "venue_evidence": (
                "location"
                if location
                else "detail_online_text"
                if branch == "온라인 실시간"
                else "detail_title_prefix"
            ),
        }
    )
    return errors


def _parallel_slc_details(
    rows: list[dict[str, Any]],
    *,
    timeout: int,
    detail_limit: int,
    max_workers: int,
    poster: JsonPoster,
    session_factory: SessionFactory,
    cutoff: date,
) -> tuple[int, int, int, list[str], bool]:
    required = [
        row for row in rows if row.get("raw_fields", {}).get("detail_required", True) is not False
    ]
    allowed = max(0, int(detail_limit))
    selected = required[:allowed]
    capped = allowed < len(required)
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def current_session() -> Any:
        value = getattr(local, "session", None)
        if value is None:
            value = session_factory()
            local.session = value
            with sessions_lock:
                sessions.append(value)
        return value

    def enrich(row: dict[str, Any]) -> tuple[bool, list[str]]:
        raw = row.get("raw_fields", {})
        identity = _clean(raw.get("source_id"))
        term_id = _integer(raw.get("term_id"))
        try:
            payload = _post_json(
                poster,
                current_session(),
                gangdong_slc_detail_api_url(),
                _slc_detail_payload(term_id or 0, identity),
                timeout,
            )
            return True, _slc_detail(row, payload, cutoff)
        except Exception as exc:
            return False, [
                f"{_clean(row.get('provider_course_id'))}: Future-On detail fetch {type(exc).__name__}"
            ]

    results: list[tuple[bool, list[str]]] = []
    try:
        if selected:
            workers = min(GANGDONG_MAX_DETAIL_WORKERS, max(1, int(max_workers)), len(selected))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gangdong-slc-detail") as pool:
                results = list(pool.map(enrich, selected))
    finally:
        for value in sessions:
            _close_quietly(value)
    pages = sum(success for success, _errors in results)
    detail_errors = [error for _success, item in results for error in item]
    return len(required), len(selected), pages, detail_errors, capped


def collect_gangdong_slc(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    json_poster: Optional[JsonPoster] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    del fetcher  # The official source is a JSON API; kept for dispatcher parity.
    errors: list[str] = []
    if not is_gangdong_slc_target(target):
        errors.append("target does not match the canonical Gangdong Future-On source")
    poster = json_poster or _default_json_poster
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    allowed_pages = max(1, int(max_pages))
    listed: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    pages = 0
    total_pages = 0
    declared_total = 0
    exposed = 0
    invalid = 0
    duplicates = 0
    expired = 0
    sentinel_pages = 0
    menu_declarations = 0
    source_cap_reached = False
    term_metrics: dict[str, dict[str, int]] = {}
    session: Any = None
    try:
        if not errors:
            session = make_session()
            for menu_id, term_id, menu_key, parent_id, menu_title in GANGDONG_SLC_TERMS:
                try:
                    menu_payload = _post_json(
                        poster,
                        session,
                        gangdong_slc_menu_api_url(),
                        {"id": str(menu_id), "isAvailable": "1"},
                        timeout,
                    )
                    menu_body, menu_errors = _slc_envelope(
                        menu_payload, cutoff=cutoff, require_source_date=False
                    )
                except Exception as exc:
                    errors.append(f"Future-On menu {menu_id} fetch {type(exc).__name__}")
                    menu_body, menu_errors = None, []
                errors.extend(f"menu {menu_id}: {error}" for error in menu_errors)
                if menu_body is not None:
                    if (
                        _integer(menu_body.get("id")) != menu_id
                        or _integer(menu_body.get("parent_id")) != parent_id
                        or _clean(menu_body.get("title")) != menu_title
                        or _integer(menu_body.get("is_available")) != 1
                        or _integer(menu_body.get("is_deleted")) != 0
                        or (menu_key and _clean(menu_body.get("key")) != menu_key)
                    ):
                        errors.append(f"Future-On menu {menu_id} declaration mismatch")
                    else:
                        menu_declarations += 1
                term_rows: list[dict[str, Any]] = []
                term_exposed = 0
                term_invalid = 0
                term_pages = 0
                term_visited = 0
                term_sentinel = 0
                total_count: Optional[int] = None
                for page in range(1, allowed_pages + 1):
                    if total_count is not None and page > term_pages:
                        break
                    try:
                        payload = _post_json(
                            poster,
                            session,
                            gangdong_slc_list_api_url(),
                            _slc_list_payload(term_id, page),
                            timeout,
                        )
                        body, response_errors = _slc_envelope(payload, cutoff=cutoff)
                    except Exception as exc:
                        errors.append(
                            f"Future-On term {term_id} page {page} fetch {type(exc).__name__}"
                        )
                        break
                    errors.extend(
                        f"term {term_id} page {page}: {error}" for error in response_errors
                    )
                    if body is None:
                        break
                    response_total = _integer(body.get("total_count"))
                    items = body.get("list")
                    if response_total is None or response_total < 0 or not isinstance(items, list):
                        errors.append(f"Future-On term {term_id} page {page} malformed body")
                        break
                    if total_count is None:
                        total_count = response_total
                        term_pages = max(1, math.ceil(total_count / GANGDONG_SLC_PAGE_SIZE))
                        total_pages += term_pages
                        declared_total += total_count
                        if term_pages > allowed_pages:
                            source_cap_reached = True
                            errors.append(
                                f"Future-On term {term_id} max_pages cap reached after {allowed_pages} of {term_pages} pages"
                            )
                    elif response_total != total_count:
                        errors.append(f"Future-On term {term_id} total changed across pages")
                    expected_rows = min(
                        GANGDONG_SLC_PAGE_SIZE,
                        max(0, (total_count or 0) - ((page - 1) * GANGDONG_SLC_PAGE_SIZE)),
                    )
                    if len(items) != expected_rows:
                        errors.append(
                            f"Future-On term {term_id} page {page} exposes {len(items)}; expected {expected_rows}"
                        )
                    pages += 1
                    term_visited += 1
                    term_exposed += len(items)
                    exposed += len(items)
                    for item in items:
                        if not isinstance(item, Mapping):
                            invalid += 1
                            term_invalid += 1
                            continue
                        row, row_errors = _slc_course_row(
                            target,
                            item,
                            menu_id=menu_id,
                            term_id=term_id,
                            page=page,
                        )
                        if row_errors or row is None:
                            invalid += 1
                            term_invalid += 1
                            continue
                        term_rows.append(row)
                if total_count is not None and term_pages <= allowed_pages:
                    try:
                        sentinel_payload = _post_json(
                            poster,
                            session,
                            gangdong_slc_list_api_url(),
                            _slc_list_payload(term_id, term_pages + 1),
                            timeout,
                        )
                        sentinel_body, sentinel_errors = _slc_envelope(
                            sentinel_payload, cutoff=cutoff
                        )
                    except Exception as exc:
                        errors.append(
                            f"Future-On term {term_id} sentinel fetch {type(exc).__name__}"
                        )
                        sentinel_body, sentinel_errors = None, []
                    errors.extend(
                        f"term {term_id} sentinel: {error}" for error in sentinel_errors
                    )
                    if sentinel_body is not None:
                        sentinel_items = sentinel_body.get("list", [])
                        sentinel_total = _integer(sentinel_body.get("total_count"))
                        if (
                            sentinel_total not in {0, total_count}
                            or not isinstance(sentinel_items, list)
                        ):
                            errors.append(f"Future-On term {term_id} sentinel malformed")
                        elif sentinel_items:
                            errors.append(
                                f"Future-On term {term_id} exposes {len(sentinel_items)} rows after declared end"
                            )
                        else:
                            sentinel_pages += 1
                            term_sentinel = 1
                for row in term_rows:
                    identity = _clean(row.get("raw_fields", {}).get("source_id"))
                    if identity in seen_ids:
                        duplicates += 1
                        continue
                    seen_ids.add(identity)
                    if date.fromisoformat(_clean(row.get("end_date"))) < cutoff:
                        expired += 1
                        continue
                    listed.append(row)
                term_metrics[str(term_id)] = {
                    "menu_id": menu_id,
                    "total": total_count or 0,
                    "pages": term_pages,
                    "visited": term_visited,
                    "exposed": term_exposed,
                    "invalid": term_invalid,
                    "sentinel": term_sentinel,
                }
    finally:
        _close_quietly(session)
    if menu_declarations != len(GANGDONG_SLC_TERMS):
        errors.append(
            f"Future-On validated {menu_declarations} of {len(GANGDONG_SLC_TERMS)} declared course menus"
        )
    if invalid:
        errors.append(f"{invalid} Future-On course rows were malformed")
    if duplicates:
        errors.append(f"{duplicates} duplicate Future-On IDs crossed declared terms")
    if exposed != declared_total:
        errors.append(
            f"Future-On exposed {exposed} rows for declared total {declared_total}"
        )
    if declared_total != len(seen_ids) + duplicates + invalid:
        errors.append("Future-On source-total identity accounting mismatch")
    list_complete = (
        not errors
        and pages == total_pages
        and sentinel_pages == len(GANGDONG_SLC_TERMS)
        and not invalid
        and not duplicates
    )
    required, attempts, detail_pages, detail_errors, detail_capped = (
        _parallel_slc_details(
            listed,
            timeout=timeout,
            detail_limit=detail_limit,
            max_workers=max_workers,
            poster=poster,
            session_factory=make_session,
            cutoff=cutoff,
        )
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {attempts} of {required} Future-On detail pages"
        )
    rows = [_clean_row(row) for row in listed]
    if dedupe_rows is not None:
        rows = list(dedupe_rows(rows))
    meta = _finish_meta(
        rows=rows,
        candidate_count=len(listed),
        pages=pages,
        total_pages=total_pages,
        list_complete=list_complete,
        detail_required_count=required,
        detail_attempts=attempts,
        detail_pages=detail_pages,
        detail_exempt_count=0,
        detail_errors=detail_errors,
        source_cap_reached=source_cap_reached,
        errors=errors,
        extra={
            "total_count": declared_total,
            "source_total": declared_total,
            "discovered_links": len(seen_ids),
            "exposed_rows": exposed,
            "expired_count": expired,
            "invalid_count": invalid,
            "duplicate_count": duplicates,
            "sentinel_pages": sentinel_pages,
            "menu_declarations": menu_declarations,
            "source_terms": term_metrics,
        },
    )
    if not meta["snapshot_complete"]:
        rows = []
    return rows, GANGDONG_SLC_PARSER, meta


def collect_gangdong_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 300,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    json_poster: Optional[JsonPoster] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    kwargs = {
        "timeout": timeout,
        "max_pages": max_pages,
        "detail_limit": detail_limit,
        "fetcher": fetcher,
        "session_factory": session_factory,
        "today": today,
        "max_workers": max_workers,
        "dedupe_rows": dedupe_rows,
    }
    if is_gangdong_reserve_target(target):
        return collect_gangdong_reserve(target, **kwargs)
    if is_gangdong_health_target(target):
        return collect_gangdong_health(target, **kwargs)
    if is_gangdong_lll_target(target):
        return collect_gangdong_lll(target, **kwargs)
    if is_gangdong_library_target(target):
        return collect_gangdong_library(target, **kwargs)
    if is_gangdong_50plus_target(target):
        return collect_gangdong_50plus(target, **kwargs)
    if is_gangdong_slc_target(target):
        return collect_gangdong_slc(target, json_poster=json_poster, **kwargs)
    if is_gangdong_jumin_target(target):
        return collect_gangdong_jumin(target, **kwargs)
    meta = _finish_meta(
        rows=[],
        candidate_count=0,
        pages=0,
        total_pages=0,
        list_complete=False,
        detail_required_count=0,
        detail_attempts=0,
        detail_pages=0,
        detail_exempt_count=0,
        detail_errors=[],
        source_cap_reached=False,
        errors=["target does not match an exact Gangdong education source"],
    )
    return [], "gangdong_target_mismatch", meta


collect = collect_gangdong_courses
