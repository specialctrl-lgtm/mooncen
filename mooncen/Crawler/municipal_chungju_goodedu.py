"""Fail-closed collector for Chungju's official integrated education catalogue.

The ``key=63`` route is the unfiltered catalogue owned by Chungju City.  The
older ``key=11&searchGroupNo=1`` route contains only the regular lifelong-
learning institution and is therefore an exact subset, not a second owner.

The source accepts ``pageUnit=1000``.  A snapshot is returned only when every
declared page, the immediate empty sentinel page, every source identity, and
every current/future detail page satisfy the source contracts below.  Any
partial response is returned as an empty result so stale cleanup cannot run on
an incomplete catalogue.

This module intentionally does not import ``Crawler_MunicipalYaml``.  The
shared router injects its managed session factory and row deduper.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import html
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


CHUNGJU_GOODEDU_PROVIDER = "MUNI_GOODEDU_CHUNGJU_GO_KR_66F13E51"
CHUNGJU_GOODEDU_SUBSET_PROVIDER = "MUNI_GOODEDU_CHUNGJU_GO_KR_9A2BFEC2"
CHUNGJU_GOODEDU_HOST = "goodedu.chungju.go.kr"
CHUNGJU_GOODEDU_LIST_PATH = "/edu/selectEdcLctreList.do"
CHUNGJU_GOODEDU_DETAIL_PATH = "/edu/selectEdcLctreView.do"
CHUNGJU_GOODEDU_APPLICATION_PATH = "/edu/addEdcLctreReqestView.do"
CHUNGJU_GOODEDU_KEY = "63"
CHUNGJU_GOODEDU_URL = (
    "https://goodedu.chungju.go.kr/edu/selectEdcLctreList.do?key=63"
)
CHUNGJU_GOODEDU_SUBSET_URL = (
    "https://goodedu.chungju.go.kr/edu/selectEdcLctreList.do?"
    "key=11&searchGroupNo=1"
)
CHUNGJU_GOODEDU_PAGE_SIZE = 1000
CHUNGJU_GOODEDU_MAX_WORKERS = 6
CHUNGJU_GOODEDU_FETCH_ATTEMPTS = 2
CHUNGJU_GOODEDU_PARSER = (
    "chungju_goodedu_key63_complete_pages+sentinel+current_detail"
)
CHUNGJU_MUNICIPALITY_CODE = "4313000000"
CHUNGJU_MUNICIPALITY_NAME = "충청북도 충주시"
CHUNGJU_OWNERSHIP_SCOPE = (
    "chungju_goodedu_key63_all_institutions_current_future"
)
CHUNGJU_GOODEDU_REGULAR_INSTITUTION = "평생학습관 정규강좌"
CHUNGJU_GOODEDU_SPECIAL_INSTITUTION = "평생학습관 특별강좌"
CHUNGJU_GOODEDU_INSTITUTION_LOCATIONS = {
    "건국대학교 글로컬캠퍼스 부설 평생교육원": "konkuk_lifelong",
    "한국교통대학교 부설 평생교육원": "ut_lifelong",
}
CHUNGJU_GOODEDU_LOCATIONS: Mapping[str, Mapping[str, Any]] = {
    "main": {
        "name": "충주시평생학습관 본관",
        "address": "충청북도 충주시 사직산6길 20",
        "lat": 36.9694819,
        "lon": 127.9269675,
        "source_url": "https://goodedu.chungju.go.kr/edu/index.do",
    },
    "yeonsu": {
        "name": "충주시평생학습관 연수동 분관",
        "address": "충청북도 충주시 연수서편2길 11",
        "lat": 36.990328,
        "lon": 127.9303622,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectBbsNttView.do?"
            "bbsNo=1&key=39&nttNo=4179"
        ),
    },
    "hoam": {
        "name": "충주시평생학습관 호암직동 분관",
        "address": "충청북도 충주시 호암토성3길 5",
        "lat": 36.953015,
        "lon": 127.9335816,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectBbsNttView.do?"
            "bbsNo=1&key=39&nttNo=4179"
        ),
    },
    "seochungju": {
        "name": "충주시평생학습관 서충주 분관",
        "address": "충청북도 충주시 주덕읍 화개4길 14",
        "lat": 37.0035566,
        "lon": 127.8224037,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectBbsNttView.do?"
            "bbsNo=3&key=41&nttNo=4176"
        ),
    },
    "geumneung": {
        "name": "충주시평생학습관 금릉동 분관",
        "address": "충청북도 충주시 팽고리산길 45",
        "lat": 36.9968341,
        "lon": 127.922639,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectBbsNttView.do?"
            "bbsNo=1&key=39&nttNo=4179"
        ),
    },
    "international_martial_arts": {
        "name": "유네스코국제무예센터",
        "address": "충청북도 충주시 옻갓길 73",
        "lat": 36.9918927,
        "lon": 127.9087899,
        "source_url": "https://www.unescoicm.org/",
    },
    "seochungju_library": {
        "name": "서충주도서관",
        "address": "충청북도 충주시 중앙탑면 원앙4길 48",
        "lat": 37.0161021,
        "lon": 127.8302928,
        "source_url": (
            "https://lib.chungju.go.kr/web/menu/10011/contents/40027/contents.do"
        ),
    },
    "dalcheon_admin": {
        "name": "달천동행정복지센터",
        "address": "충청북도 충주시 충원대로 211",
        "lat": 36.9477448,
        "lon": 127.9011189,
        "source_url": "https://www.chungju.go.kr/dong/index.do?key=1748",
    },
    "seochungju_living_culture": {
        "name": "서충주생활문화센터",
        "address": "충청북도 충주시 중앙탑면 기업도시로 237-2",
        "lat": 37.0166126,
        "lon": 127.8217101,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectBbsNttView.do?"
            "bbsNo=1&key=39&nttNo=4147"
        ),
    },
    "hoam_arts": {
        "name": "호암예술관",
        "address": "충청북도 충주시 중원대로 3306",
        "lat": 36.9631233,
        "lon": 127.9259385,
        "source_url": "https://www.chungju.go.kr/",
    },
    "konkuk_lifelong": {
        "name": "건국대학교 글로컬캠퍼스 부설 평생교육원",
        "address": "충청북도 충주시 충원대로 268",
        "lat": 36.9494179,
        "lon": 127.9075321,
        "source_url": "https://elife.kku.ac.kr/",
    },
    "ut_lifelong": {
        "name": "한국교통대학교 부설 평생교육원",
        "address": "충청북도 충주시 대소원면 대학로 50",
        "lat": 36.9690564,
        "lon": 127.8708642,
        "source_url": "https://www.ut.ac.kr/",
    },
    "mokhaeng_glory_book": {
        "name": "목행동 평생학습센터(글로리북카페)",
        "address": "충청북도 충주시 행정3길 40",
        "lat": 37.0124844,
        "lon": 127.9172211,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectEdcLctreView.do?"
            "edcLctreNo=5994&key=63"
        ),
    },
    "jihyeon_culture_platform": {
        "name": "지현문화플랫폼",
        "address": "충청북도 충주시 지곡6길 53-4",
        "lat": 36.9672067,
        "lon": 127.9316455,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectEdcLctreView.do?"
            "edcLctreNo=5996&key=63"
        ),
    },
    "gyohyeon_living_culture": {
        "name": "교현동 평생학습센터(충주생활문화센터)",
        "address": "충청북도 충주시 교동1길 15-5",
        "lat": 36.9743936,
        "lon": 127.9355357,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectEdcLctreView.do?"
            "edcLctreNo=6000&key=63"
        ),
    },
    "eomjeong_dream_library": {
        "name": "엄정면 평생학습센터(엄정꿈터도서관)",
        "address": "충청북도 충주시 엄정면 자바위길 8",
        "lat": 37.0869914,
        "lon": 127.9153487,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectEdcLctreView.do?"
            "edcLctreNo=6001&key=63"
        ),
    },
    "noeun_community": {
        "name": "노은면 평생학습센터(노은면 어울림센터)",
        "address": "충청북도 충주시 노은면 연하중앙길 50",
        "lat": 37.0467107,
        "lon": 127.7573387,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectEdcLctreView.do?"
            "edcLctreNo=6003&key=63"
        ),
    },
    "daesowon_mom_garden": {
        "name": "대소원면 평생학습센터(엄마의 정원)",
        "address": "충청북도 충주시 대소원면 첨단산업로 161",
        "lat": 36.9860148,
        "lon": 127.8326536,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectEdcLctreView.do?"
            "edcLctreNo=6006&key=63"
        ),
    },
    "seochungju_youth": {
        "name": "서충주청소년문화의집",
        "address": "충청북도 충주시 중앙탑면 기업도시로 237-3",
        "lat": 37.0170726,
        "lon": 127.8222393,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectEdcLctreView.do?"
            "edcLctreNo=6009&key=63"
        ),
    },
    "suanbo_small_library": {
        "name": "수안보작은도서관",
        "address": "충청북도 충주시 수안보면 물탕2길 17",
        "lat": 36.846978,
        "lon": 127.990876,
        "source_url": (
            "https://goodedu.chungju.go.kr/edu/selectEdcLctreView.do?"
            "edcLctreNo=6010&key=63"
        ),
    },
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d+")
_COUNT_RE = re.compile(r"총\s*게시물\s*([\d,]+)\s*개")
_PAGE_RE = re.compile(r"페이지\s*(\d+)\s*/\s*(\d+)")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_COUNT_VALUE_RE = re.compile(r"([\d,]+)")

_LIST_HEADERS = (
    "번호",
    "강좌명/교육시간",
    "선발방식",
    "교육기관/수강료",
    "신청/교육기간",
    "상태",
)
_SOURCE_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
}
_CURRENT_SOURCE_STATUSES = frozenset(
    {"접수중", "접수대기", "접수마감", "교육중"}
)
_ENDED_SOURCE_STATUSES = frozenset({"교육종료"})


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


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


def is_chungju_goodedu_target(target: Any) -> bool:
    """Match only the canonical, unfiltered ``key=63`` owner route."""

    parsed = urlparse(_target_url(target))
    return bool(
        _provider(target) == CHUNGJU_GOODEDU_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == CHUNGJU_GOODEDU_HOST
        and parsed.port is None
        and parsed.path == CHUNGJU_GOODEDU_LIST_PATH
        and parse_qsl(parsed.query, keep_blank_values=True)
        == [("key", CHUNGJU_GOODEDU_KEY)]
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_chungju_goodedu_target


def chungju_goodedu_list_url(page: Any) -> str:
    raw = _clean(page)
    if not _IDENTITY_RE.fullmatch(raw) or int(raw) < 1:
        return ""
    return f"https://{CHUNGJU_GOODEDU_HOST}{CHUNGJU_GOODEDU_LIST_PATH}?" + urlencode(
        {
            "key": CHUNGJU_GOODEDU_KEY,
            "pageUnit": CHUNGJU_GOODEDU_PAGE_SIZE,
            "pageIndex": int(raw),
        }
    )


def chungju_goodedu_detail_url(identity: Any) -> str:
    raw = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw):
        return ""
    return f"https://{CHUNGJU_GOODEDU_HOST}{CHUNGJU_GOODEDU_DETAIL_PATH}?" + urlencode(
        {"edcLctreNo": raw, "key": CHUNGJU_GOODEDU_KEY}
    )


def chungju_goodedu_application_url(identity: Any) -> str:
    raw = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw):
        return ""
    return (
        f"https://{CHUNGJU_GOODEDU_HOST}{CHUNGJU_GOODEDU_APPLICATION_PATH}?"
        + urlencode({"edcLctreNo": raw, "key": CHUNGJU_GOODEDU_KEY})
    )


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_soup(response: Any) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError("HTTP redirects are not accepted")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _parallel_fetch(
    items: list[tuple[Any, str]],
    *,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, BeautifulSoup], list[str]]:
    fetched: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def thread_session() -> Any:
        current = getattr(local, "session", None)
        if current is None:
            current = session_factory()
            local.session = current
            with sessions_lock:
                sessions.append(current)
        return current

    def one(item: tuple[Any, str]) -> tuple[Any, Optional[BeautifulSoup], str]:
        key, url = item
        last_error = ""
        for attempt in range(CHUNGJU_GOODEDU_FETCH_ATTEMPTS):
            try:
                response = thread_session().get(
                    url,
                    timeout=timeout,
                    allow_redirects=False,
                )
                return key, _response_soup(response), ""
            except Exception as exc:
                last_error = type(exc).__name__
                if attempt + 1 < CHUNGJU_GOODEDU_FETCH_ATTEMPTS:
                    time.sleep(0.15 * (2**attempt))
        return key, None, f"{key}: fetch {last_error}"

    if not items:
        return fetched, errors
    workers = min(
        max(1, int(max_workers)),
        CHUNGJU_GOODEDU_MAX_WORKERS,
        len(items),
    )
    try:
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="chungju-goodedu"
        ) as pool:
            for key, soup, error in pool.map(one, items):
                if soup is not None:
                    fetched[key] = soup
                if error:
                    errors.append(error)
    finally:
        for current in sessions:
            _close_quietly(current)
    return fetched, errors


def _counter(soup: BeautifulSoup) -> Optional[tuple[int, int, int]]:
    values = [_clean(node.get_text(" ", strip=True)) for node in soup.select("div.category .board_dot")]
    totals = {
        int(match.group(1).replace(",", ""))
        for value in values
        for match in [_COUNT_RE.fullmatch(value)]
        if match is not None
    }
    pages = {
        (int(match.group(1)), int(match.group(2)))
        for value in values
        for match in [_PAGE_RE.fullmatch(value)]
        if match is not None
    }
    if len(totals) != 1 or len(pages) != 1:
        return None
    displayed, advertised = pages.pop()
    return totals.pop(), displayed, advertised


def _form_value(form: Any, name: str) -> Optional[str]:
    node = form.find(attrs={"name": name})
    if node is None:
        return None
    if getattr(node, "name", "") != "select":
        return _clean(node.get("value"))
    option = node.select_one("option[selected]") or node.select_one("option")
    return _clean(option.get("value")) if option is not None else None


def _list_form_valid(soup: BeautifulSoup) -> bool:
    form = soup.select_one("form#searchVO[name='searchForm']")
    if form is None or _clean(form.get("method")).lower() != "get":
        return False
    action = urlparse(urljoin(CHUNGJU_GOODEDU_URL, _clean(form.get("action"))))
    if (
        (action.hostname or "").lower() != CHUNGJU_GOODEDU_HOST
        or action.path.split(";", 1)[0] != CHUNGJU_GOODEDU_LIST_PATH
    ):
        return False
    expected = {
        "key": CHUNGJU_GOODEDU_KEY,
        "searchGroupNo": "",
        "searchInsttNo": "",
        "searchCtgryNo": "",
        "searchProgrsSttus": "",
        "searchLctreNm": "",
    }
    return all(_form_value(form, name) == value for name, value in expected.items())


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return result


def _count(value: Any) -> Optional[int]:
    match = _COUNT_VALUE_RE.search(_clean(value))
    return int(match.group(1).replace(",", "")) if match else None


def _branch_code(branch: Any) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"CHUNGJU_GOODEDU_BRANCH_{digest}"


def chungju_goodedu_location(
    source_institution: Any,
    title: Any,
    venue_name: Any,
) -> Optional[dict[str, Any]]:
    """Resolve known Chungju catalogue rows to their physical facility."""

    institution = _clean(source_institution)
    title_text = _clean(title)
    venue_text = _clean(venue_name)
    combined = f"{title_text} {venue_text}"
    location_key = CHUNGJU_GOODEDU_INSTITUTION_LOCATIONS.get(institution, "")

    if not location_key and institution == CHUNGJU_GOODEDU_REGULAR_INSTITUTION:
        if "국제무예" in combined:
            location_key = "international_martial_arts"
        elif "연수동" in combined:
            location_key = "yeonsu"
        elif "호암직동" in combined:
            location_key = "hoam"
        elif "서충주" in combined:
            location_key = "seochungju"
        elif "금릉동" in combined:
            location_key = "geumneung"
        elif "(본관)" in title_text:
            location_key = "main"
    elif not location_key and institution == CHUNGJU_GOODEDU_SPECIAL_INSTITUTION:
        if "호암예술관" in venue_text:
            location_key = "hoam_arts"
        elif "호암직동" in venue_text:
            location_key = "hoam"
        elif "목행동" in venue_text or "글로리북카페" in venue_text:
            location_key = "mokhaeng_glory_book"
        elif "지현문화플랫폼" in venue_text:
            location_key = "jihyeon_culture_platform"
        elif (
            "교현동" in venue_text
            or (
                "충주생활문화센터" in venue_text
                and "서충주생활문화센터" not in venue_text
            )
        ):
            location_key = "gyohyeon_living_culture"
        elif "엄정면" in venue_text or "엄정면꿈터도서관" in venue_text:
            location_key = "eomjeong_dream_library"
        elif "노은면" in venue_text or "노은면어울림센터" in venue_text:
            location_key = "noeun_community"
        elif "엄마의 정원" in venue_text:
            location_key = "daesowon_mom_garden"
        elif "서충주청소년문화의집" in venue_text:
            location_key = "seochungju_youth"
        elif "수안보" in venue_text and "도서관" in venue_text:
            location_key = "suanbo_small_library"
        elif "서충주생활문화센터" in venue_text:
            location_key = "seochungju_living_culture"
        elif "서충주도서관" in venue_text:
            location_key = "seochungju_library"
        elif "달천동" in venue_text and "행정복지센터" in venue_text:
            location_key = "dalcheon_admin"
        elif "평생학습" in venue_text and "본관" in venue_text:
            location_key = "main"

    location = CHUNGJU_GOODEDU_LOCATIONS.get(location_key)
    if not location:
        return None
    result = dict(location)
    result["key"] = location_key
    result["branch_code"] = _branch_code(result["name"])
    return result


def _apply_chungju_goodedu_location(row: dict[str, Any]) -> bool:
    raw_fields = row.setdefault("raw_fields", {})
    source_institution = _clean(raw_fields.get("source_institution"))
    location = chungju_goodedu_location(
        source_institution,
        row.get("title"),
        row.get("venue_name"),
    )
    if not location:
        return False

    address = _clean(location["address"])
    source_url = _clean(location["source_url"])
    row.update(
        {
            "branch": location["name"],
            "branch_code": location["branch_code"],
            "branch_url": source_url,
            "venue_address": address,
            "address": address,
            "branch_lat": location["lat"],
            "branch_lon": location["lon"],
            "branch_address_source": "OFFICIAL_CHUNGJU_LOCATION_CATALOG",
            "branch_coordinate_source": "NAVER_LOCAL_SEARCH_BY_OFFICIAL_ADDRESS",
            "branch_location_confidence": 100,
            "branch_location_verified": True,
            "branch_location_query": source_url,
            "preserve_branch": True,
        }
    )
    raw_fields["resolved_location_key"] = location["key"]
    raw_fields["resolved_location_source"] = source_url
    return True


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if value not in (None, "", [], {})
    }


def _parse_href_identity(
    href: Any,
    *,
    expected_path: str,
) -> tuple[str, str]:
    parsed = urlparse(urljoin(CHUNGJU_GOODEDU_URL, _clean(href)))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != CHUNGJU_GOODEDU_HOST
        or parsed.port is not None
        or parsed.path != expected_path
        or parsed.fragment
    ):
        return "", "unexpected Chungju course link route"
    query = parse_qs(parsed.query, keep_blank_values=True)
    identities = query.get("edcLctreNo") or []
    keys = query.get("key") or []
    if (
        len(identities) != 1
        or not _IDENTITY_RE.fullmatch(identities[0])
        or keys != [CHUNGJU_GOODEDU_KEY]
    ):
        return "", "malformed Chungju course link identity/key"
    return identities[0], ""


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    page: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    tables = soup.select("table.bbs_default_list")
    if len(tables) != 1:
        return rows, [f"page {page}: expected one course table"]
    table = tables[0]
    caption = table.select_one("caption")
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if caption is None or _clean(caption.get_text(" ", strip=True)) != "강좌안내 목록":
        errors.append(f"page {page}: unexpected course table caption")
    if headers != _LIST_HEADERS:
        errors.append(f"page {page}: unexpected course table headers")

    for source_row in table.select("tbody tr"):
        detail_link = source_row.select_one(
            "a[href*='selectEdcLctreView.do'][href*='edcLctreNo=']"
        )
        if detail_link is None:
            empty_text = _clean(source_row.get_text(" ", strip=True))
            if empty_text == "등록된 교육강좌가 없습니다.":
                continue
            errors.append(f"page {page}: non-course table row")
            continue
        cells = source_row.select("td")
        if len(cells) != 6:
            errors.append(f"page {page}: course row does not have six cells")
            continue
        sequence_raw = _clean(cells[0].get_text(" ", strip=True)).replace(",", "")
        if not _IDENTITY_RE.fullmatch(sequence_raw):
            errors.append(f"page {page}: malformed list sequence")
            continue
        identity, identity_error = _parse_href_identity(
            detail_link.get("href"), expected_path=CHUNGJU_GOODEDU_DETAIL_PATH
        )
        if identity_error:
            errors.append(f"page {page}: {identity_error}")
            continue
        title_parts = [_clean(value) for value in detail_link.stripped_strings if _clean(value)]
        title = title_parts[0] if title_parts else ""
        schedule = " ".join(title_parts[1:])
        institution_fee = [
            _clean(value) for value in cells[3].stripped_strings if _clean(value)
        ]
        institution = institution_fee[0] if institution_fee else ""
        fee = institution_fee[-1] if len(institution_fee) >= 2 else ""
        period_text = _clean(cells[4].get_text(" ", strip=True))
        period_dates = _dates(period_text)
        status_values = {
            _clean(value)
            for value in cells[5].stripped_strings
            if _clean(value) in _SOURCE_STATUS_MAP
        }
        source_status = status_values.pop() if len(status_values) == 1 else ""
        selection = _clean(cells[2].get_text(" ", strip=True))
        row_errors: list[str] = []
        if not title:
            row_errors.append("empty title")
        if not institution or not fee:
            row_errors.append("empty institution/fee")
        if not selection:
            row_errors.append("empty selection method")
        if "접수" not in period_text or "교육" not in period_text or len(period_dates) != 4:
            row_errors.append("invalid application/education period")
        elif period_dates[3] < period_dates[2]:
            row_errors.append("reversed education period")
        if source_status not in _SOURCE_STATUS_MAP:
            row_errors.append("unknown source status")

        application_url = ""
        application_link = cells[5].select_one(
            "a[href*='addEdcLctreReqestView.do'][href*='edcLctreNo=']"
        )
        if application_link is not None:
            application_identity, application_error = _parse_href_identity(
                application_link.get("href"),
                expected_path=CHUNGJU_GOODEDU_APPLICATION_PATH,
            )
            if application_error or application_identity != identity:
                row_errors.append("application link identity mismatch")
            else:
                application_url = chungju_goodedu_application_url(identity)
        if source_status == "접수중" and not application_url:
            row_errors.append("open course has no application link")
        if source_status != "접수중" and application_url:
            row_errors.append("non-open course unexpectedly has an application link")

        if row_errors:
            errors.extend(f"{identity or '?'}: {message}" for message in row_errors)
            continue
        apply_start, apply_end, start, end = period_dates
        raw_url = chungju_goodedu_detail_url(identity)
        row: dict[str, Any] = {
            "provider": CHUNGJU_GOODEDU_PROVIDER,
            "provider_course_id": (
                f"{CHUNGJU_GOODEDU_PROVIDER}:lecture:{identity}"
            ),
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "branch": institution,
            "branch_code": _branch_code(institution),
            "provider_organizer": institution,
            "category": "평생학습",
            "program_type": "강좌",
            "raw_url": raw_url,
            "application_url": application_url,
            "application_type": (
                "ONLINE_RESERVATION" if application_url else "INFO_ONLY"
            ),
            "status": _SOURCE_STATUS_MAP[source_status],
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
            "apply_start": apply_start.isoformat(),
            "apply_end": apply_end.isoformat(),
            "schedule_raw": schedule,
            "fee": fee,
            "description": title,
            "source_group": "municipal_reservation",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "raw_fields": {
                "identity": identity,
                "list_page": page,
                "list_sequence": int(sequence_raw),
                "source_status": source_status,
                "selection_method": selection,
                "source_institution": institution,
                "list_fee": fee,
                "list_period_text": period_text,
                "list_schedule": schedule,
                "historical_reversed_apply_period": apply_end < apply_start,
            },
        }
        rows.append(row)
    return rows, errors


def _table_pairs(table: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row in table.select("tr"):
        for heading in row.select("th[scope='row']"):
            value = heading.find_next_sibling("td")
            if value is not None:
                pairs[_clean(heading.get_text(" ", strip=True))] = _clean(
                    value.get_text(" ", strip=True)
                )
    return pairs


def _caption(table: Any) -> str:
    value = table.select_one("caption")
    return _clean(value.get_text(" ", strip=True)) if value is not None else ""


def _validate_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    errors: list[str] = []
    course_tables = [
        table
        for table in soup.select("table.bbs_view")
        if _caption(table) == "강좌 상세보기"
    ]
    if len(course_tables) != 1:
        return [f"{identity}: expected one course detail table"]
    pairs = _table_pairs(course_tables[0])
    required = (
        "강좌명",
        "교육대상",
        "접수기간",
        "교육기간",
        "교육시간",
        "접수방식",
        "선발방식",
        "교육장소",
        "수강료",
    )
    missing = [label for label in required if not pairs.get(label)]
    if missing:
        errors.append(f"{identity}: missing detail fields {','.join(missing)}")
        return errors
    if pairs["강좌명"] != _clean(row.get("title")):
        errors.append(f"{identity}: detail/list title mismatch")
    apply_dates = _dates(pairs["접수기간"])
    education_dates = _dates(pairs["교육기간"])
    expected_apply = [
        date.fromisoformat(_clean(row.get("apply_start"))),
        date.fromisoformat(_clean(row.get("apply_end"))),
    ]
    expected_education = [
        date.fromisoformat(_clean(row.get("start_date"))),
        date.fromisoformat(_clean(row.get("end_date"))),
    ]
    if apply_dates != expected_apply:
        errors.append(f"{identity}: detail/list application period mismatch")
    if education_dates != expected_education:
        errors.append(f"{identity}: detail/list education period mismatch")
    if pairs["선발방식"] != _clean(row.get("raw_fields", {}).get("selection_method")):
        errors.append(f"{identity}: detail/list selection method mismatch")
    if pairs["수강료"] != _clean(row.get("fee")):
        errors.append(f"{identity}: detail/list fee mismatch")

    method = pairs["접수방식"]
    if not any(token in method for token in ("온라인", "방문", "전화", "현장")):
        errors.append(f"{identity}: unknown application method")
    institution_tables = [
        table
        for table in soup.select("table.bbs_view")
        if _caption(table) == "기관 상세정보"
    ]
    if len(institution_tables) > 1:
        errors.append(f"{identity}: multiple institution detail tables")
    institution_pairs = _table_pairs(institution_tables[0]) if institution_tables else {}
    detail_institution = institution_pairs.get("기관명", "")
    if detail_institution and detail_institution != _clean(row.get("branch")):
        errors.append(f"{identity}: detail/list institution mismatch")

    instructor_tables = [
        table
        for table in soup.select("table.bbs_view")
        if _caption(table) == "강사 상세정보"
    ]
    if len(instructor_tables) != 1:
        errors.append(f"{identity}: expected one instructor detail table")
    instructor_pairs = _table_pairs(instructor_tables[0]) if instructor_tables else {}

    capacity_total = _count(
        pairs.get("추첨인원") or pairs.get("모집인원") or pairs.get("정원")
    )
    capacity_current = _count(pairs.get("신청인원"))
    description_values = [
        pairs.get("강의개요", ""),
        pairs.get("참고사항", ""),
        pairs.get("교재 및 참고자료", ""),
    ]
    description = " ".join(
        dict.fromkeys(value for value in description_values if value)
    )
    row.update(
        {
            "target": pairs["교육대상"],
            "venue_name": pairs["교육장소"],
            "schedule_raw": _clean(
                " ".join(
                    value
                    for value in (
                        row.get("schedule_raw"),
                        pairs["교육시간"],
                    )
                    if _clean(value)
                )
            ),
            "application_method_raw": method,
            "description": description or _clean(row.get("title")),
            "instructor": instructor_pairs.get("강사명", ""),
            "capacity": capacity_total,
            "capacity_total": capacity_total,
            "capacity_current": capacity_current,
        }
    )
    if "온라인" in method:
        # Only the source's real addEdcLctreReqestView link proves that this
        # course can currently accept an application. A detail page is never
        # substituted for that application endpoint.
        row["application_type"] = (
            "ONLINE_RESERVATION"
            if _clean(row.get("application_url"))
            else "INFO_ONLY"
        )
    else:
        row["application_type"] = "OFFLINE_APPLY"
    row.setdefault("raw_fields", {}).update(
        {
            "detail_pairs": pairs,
            "instructor_pairs": instructor_pairs,
            "institution_pairs": institution_pairs,
        }
    )
    source_institution = _clean(
        row.get("raw_fields", {}).get("source_institution")
    )
    if (
        source_institution
        in {
            CHUNGJU_GOODEDU_REGULAR_INSTITUTION,
            CHUNGJU_GOODEDU_SPECIAL_INSTITUTION,
        }
        or source_institution in CHUNGJU_GOODEDU_INSTITUTION_LOCATIONS
    ) and not _apply_chungju_goodedu_location(row):
        errors.append(f"{identity}: unrecognized physical education location")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str, *, source_cap_reached: bool = False) -> dict[str, Any]:
    return {
        "pages": 0,
        "main_discovery_pages": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": source_cap_reached,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_chungju_goodedu_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 400,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = CHUNGJU_GOODEDU_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Chungju education snapshot."""

    if not is_chungju_goodedu_target(target):
        return [], CHUNGJU_GOODEDU_PARSER, _failure(
            "target does not match the canonical Chungju key=63 route"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], CHUNGJU_GOODEDU_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory

    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        cutoff = _today(today)
        workers = min(
            max(1, int(max_workers)), CHUNGJU_GOODEDU_MAX_WORKERS
        )
    except (TypeError, ValueError):
        return [], CHUNGJU_GOODEDU_PARSER, _failure(
            "max_pages/detail_limit/max_workers/today are invalid"
        )
    if allowed_pages < 1:
        return [], CHUNGJU_GOODEDU_PARSER, _failure(
            "max_pages cap allows 0 of at least 2 required list requests",
            source_cap_reached=True,
        )

    errors: list[str] = []
    source_cap_reached = False
    page_soups, fetch_errors = _parallel_fetch(
        [(1, chungju_goodedu_list_url(1))],
        session_factory=session_factory,
        timeout=timeout,
        max_workers=1,
    )
    errors.extend(fetch_errors)
    source_total = 0
    data_pages = 0
    required_list_requests = 0

    first = page_soups.get(1)
    if first is None:
        errors.append("missing first catalogue page")
    else:
        if not _list_form_valid(first):
            errors.append("page 1: malformed or filtered list form")
        contract = _counter(first)
        if contract is None:
            errors.append("page 1: missing unambiguous catalogue counter")
        else:
            source_total, displayed_page, advertised_last = contract
            data_pages = max(1, math.ceil(source_total / CHUNGJU_GOODEDU_PAGE_SIZE))
            required_list_requests = data_pages + 1
            if displayed_page != 1 or advertised_last != data_pages:
                errors.append("page 1: advertised pagination is inconsistent")
            if required_list_requests > allowed_pages:
                source_cap_reached = True
                errors.append(
                    f"max_pages cap allows {allowed_pages} of "
                    f"{required_list_requests} required list requests"
                )

    if not errors:
        remaining, remaining_errors = _parallel_fetch(
            [
                (page, chungju_goodedu_list_url(page))
                for page in range(2, required_list_requests + 1)
            ],
            session_factory=session_factory,
            timeout=timeout,
            max_workers=workers,
        )
        page_soups.update(remaining)
        errors.extend(remaining_errors)

    listed_rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    if not errors:
        for page in range(1, required_list_requests + 1):
            soup = page_soups.get(page)
            if soup is None:
                errors.append(f"page {page}: missing list response")
                continue
            if not _list_form_valid(soup):
                errors.append(f"page {page}: malformed or filtered list form")
            contract = _counter(soup)
            if contract != (source_total, page, data_pages):
                errors.append(f"page {page}: catalogue counter changed")
            parsed, page_errors = _parse_list_page(soup, page=page)
            page_counts[page] = len(parsed)
            errors.extend(page_errors)
            if page <= data_pages:
                listed_rows.extend(parsed)

        for page in range(1, data_pages):
            if page_counts.get(page) != CHUNGJU_GOODEDU_PAGE_SIZE:
                errors.append(f"page {page}: non-terminal page is not full")
        expected_terminal = source_total - CHUNGJU_GOODEDU_PAGE_SIZE * (data_pages - 1)
        if source_total == 0:
            expected_terminal = 0
        if page_counts.get(data_pages) != expected_terminal:
            errors.append("terminal page row count mismatch")
        if page_counts.get(data_pages + 1) != 0:
            errors.append("immediate post-total sentinel page is not empty")
        if len(listed_rows) != source_total:
            errors.append(
                f"declared total {source_total} != parsed rows {len(listed_rows)}"
            )

    identities = [
        _clean(row.get("raw_fields", {}).get("identity")) for row in listed_rows
    ]
    duplicate_identity_count = len(identities) - len(set(identities))
    if duplicate_identity_count:
        errors.append(f"{duplicate_identity_count} duplicate source identities")
    raw_urls = [_clean(row.get("raw_url")) for row in listed_rows]
    duplicate_url_count = len(raw_urls) - len(set(raw_urls))
    if duplicate_url_count:
        errors.append(f"{duplicate_url_count} duplicate canonical detail URLs")
    expected_sequences = list(range(source_total, 0, -1))
    actual_sequences = [
        int(row.get("raw_fields", {}).get("list_sequence") or 0)
        for row in listed_rows
    ]
    if actual_sequences != expected_sequences:
        errors.append("list sequence is not a complete descending source range")

    current_rows: list[dict[str, Any]] = []
    expired_count = 0
    status_date_mismatch_count = 0
    historical_reversed_apply_period_count = 0
    for row in listed_rows:
        source_status = _clean(row.get("raw_fields", {}).get("source_status"))
        try:
            end = date.fromisoformat(_clean(row.get("end_date")))
        except ValueError:
            errors.append(
                f"{_clean(row.get('provider_course_id'))}: invalid end date"
            )
            continue
        if end < cutoff:
            expired_count += 1
            if bool(
                row.get("raw_fields", {}).get("historical_reversed_apply_period")
            ):
                historical_reversed_apply_period_count += 1
            if source_status not in _ENDED_SOURCE_STATUSES:
                status_date_mismatch_count += 1
        else:
            current_rows.append(row)
            if bool(
                row.get("raw_fields", {}).get("historical_reversed_apply_period")
            ):
                errors.append(
                    f"{_clean(row.get('provider_course_id'))}: "
                    "current course has reversed application period"
                )
            if source_status not in _CURRENT_SOURCE_STATUSES:
                status_date_mismatch_count += 1
    if status_date_mismatch_count:
        errors.append(
            f"{status_date_mismatch_count} source status/end-date classification mismatches"
        )

    list_complete = bool(
        not errors
        and len(page_soups) == required_list_requests
        and len(listed_rows) == source_total
    )
    required_details = len(current_rows)
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    if required_details > allowed_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of "
            f"{required_details} required current/future details"
        )
    elif list_complete and current_rows:
        detail_attempts = required_details
        detail_soups, detail_fetch_errors = _parallel_fetch(
            [
                (
                    _clean(row.get("raw_fields", {}).get("identity")),
                    _clean(row.get("raw_url")),
                )
                for row in current_rows
            ],
            session_factory=session_factory,
            timeout=timeout,
            max_workers=workers,
        )
        detail_errors.extend(detail_fetch_errors)
        rows_by_identity = {
            _clean(row.get("raw_fields", {}).get("identity")): row
            for row in current_rows
        }
        for identity, soup in detail_soups.items():
            item_errors = _validate_detail(rows_by_identity[identity], soup)
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                detail_pages += 1
    errors.extend(detail_errors)

    details_complete = bool(
        list_complete
        and detail_attempts == required_details
        and detail_pages == required_details
        and not detail_errors
    )
    result: list[dict[str, Any]] = []
    if list_complete and details_complete and not errors:
        deduper = dedupe_rows or _dedupe_default
        result = list(deduper([_clean_row(row) for row in current_rows]))
        if len(result) != len(current_rows):
            errors.append(
                f"dedupe changed complete row count {len(current_rows)} to {len(result)}"
            )
            result = []

    snapshot_complete = bool(list_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []
    source_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in listed_rows
    )
    current_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in current_rows
    )
    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    meta = {
        "pages": len(page_soups),
        "main_discovery_pages": 1 if first is not None else 0,
        "list_requests": len(page_soups),
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "source_total": source_total,
        "source_rows": len(listed_rows),
        "data_pages": data_pages,
        "required_list_requests": required_list_requests,
        "sentinel_page": data_pages + 1 if data_pages else 0,
        "page_counts": page_counts,
        "expired_count": expired_count,
        "current_count": len(current_rows),
        "returned_count": len(result),
        "branch_count": len(branch_counts),
        "branch_counts": dict(branch_counts),
        "source_status_counts": dict(source_status_counts),
        "current_status_counts": dict(current_status_counts),
        "duplicate_count": duplicate_identity_count,
        "duplicate_url_count": duplicate_url_count,
        "status_date_mismatch_count": status_date_mismatch_count,
        "historical_reversed_apply_period_count": (
            historical_reversed_apply_period_count
        ),
        "discovered_links": len(listed_rows),
        "reservation_discovery_links": sum(
            bool(row.get("application_url")) for row in result
        ),
        "pagination_detected": data_pages > 1,
        "pagination_complete": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "no_current_data": bool(snapshot_complete and not current_rows),
        "no_current_reason": (
            "all complete Chungju key=63 catalogue courses have ended"
            if snapshot_complete and not current_rows
            else ""
        ),
        "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        "ownership_scope": CHUNGJU_OWNERSHIP_SCOPE,
        "ownership_aliases": [CHUNGJU_GOODEDU_SUBSET_URL],
        "superseded_providers": [CHUNGJU_GOODEDU_SUBSET_PROVIDER],
    }
    return result, CHUNGJU_GOODEDU_PARSER, meta


collect = collect_chungju_goodedu_courses


__all__ = [
    "CHUNGJU_GOODEDU_APPLICATION_PATH",
    "CHUNGJU_GOODEDU_DETAIL_PATH",
    "CHUNGJU_GOODEDU_KEY",
    "CHUNGJU_GOODEDU_INSTITUTION_LOCATIONS",
    "CHUNGJU_GOODEDU_LIST_PATH",
    "CHUNGJU_GOODEDU_MAX_WORKERS",
    "CHUNGJU_GOODEDU_PAGE_SIZE",
    "CHUNGJU_GOODEDU_PARSER",
    "CHUNGJU_GOODEDU_PROVIDER",
    "CHUNGJU_GOODEDU_LOCATIONS",
    "CHUNGJU_GOODEDU_REGULAR_INSTITUTION",
    "CHUNGJU_GOODEDU_SPECIAL_INSTITUTION",
    "CHUNGJU_GOODEDU_SUBSET_PROVIDER",
    "CHUNGJU_GOODEDU_SUBSET_URL",
    "CHUNGJU_GOODEDU_URL",
    "CHUNGJU_MUNICIPALITY_CODE",
    "CHUNGJU_MUNICIPALITY_NAME",
    "CHUNGJU_OWNERSHIP_SCOPE",
    "chungju_goodedu_application_url",
    "chungju_goodedu_detail_url",
    "chungju_goodedu_list_url",
    "chungju_goodedu_location",
    "collect",
    "collect_chungju_goodedu_courses",
    "is_chungju_goodedu_target",
    "is_target",
]
