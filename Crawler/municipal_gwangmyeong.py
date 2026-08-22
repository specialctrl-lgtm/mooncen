"""Fail-closed education/experience collector for Gwangmyeong City's ILMS.

``lll.gm.go.kr`` is a navigation shell.  Its course shortcut opens the
municipal catalogue at ``sugang.gm.go.kr``.  That catalogue is also the
official aggregator for the municipal libraries, youth foundation, women's
centre, upcycling centre, culture centre and a small number of other public
education operators.  Those linked records therefore belong to this one
provider and must not be scheduled again under the linked source sites.

The collector validates the complete learning archive, the institution
directory, the (currently empty) video catalogue and all three culture-centre
course categories.  It derives changing culture-centre page boundaries from
their public pagination, checks declared totals where available, reads every
page, probes an immediate post-last sentinel and rechecks page one.  The
culture-centre catalogue is authoritative across term rollovers: stale ILMS
links to an older culture-centre list are audited as mirrors and replaced by
the current item identities.  A linked library record is excluded only after
two reads both return the library's exact public tombstone; every other
current education record must have a course-bound public detail.

Application URLs are emitted only when the bound detail exposes an explicit
public application control.  Exact experience rows are emitted only when the
course-bound detail publishes a fixed Gwangmyeong street address.  Facility
access, notices/events, performances, address-less experiences, persistent
child-ledger tombstones and test records are audited but excluded.

Only a small allowlist of course fields is returned.  Applicant lists,
instructor/contact data, attachments, descriptions and source HTML are never
persisted.  Any incomplete contract returns an empty result.
"""

from __future__ import annotations

import base64
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GWANGMYEONG_PROVIDER = "MUNI_SUGANG_GM_GO_KR_F136DD19"
GWANGMYEONG_LEGACY_PROVIDER = "MUNI_LLL_GM_GO_KR_24781D42"
GWANGMYEONG_CANONICAL_CANDIDATE_ID = "MUNI_IR_9D9E061C5309"
GWANGMYEONG_HOST = "sugang.gm.go.kr"
GWANGMYEONG_LEARNING_LIST_PATH = "/ilms/learning/learningList.do"
GWANGMYEONG_LEARNING_DETAIL_PATH = "/ilms/learning/learningDetail.do"
GWANGMYEONG_OFFICE_LIST_PATH = "/ilms/learning/officeList.do"
GWANGMYEONG_MEDIA_LIST_PATH = "/ilms/media/learningList.do"
GWANGMYEONG_CANONICAL_URL = (
    f"https://{GWANGMYEONG_HOST}{GWANGMYEONG_LEARNING_LIST_PATH}"
)
GWANGMYEONG_OFFICE_URL = (
    f"https://{GWANGMYEONG_HOST}{GWANGMYEONG_OFFICE_LIST_PATH}"
)
GWANGMYEONG_MEDIA_URL = (
    f"https://{GWANGMYEONG_HOST}{GWANGMYEONG_MEDIA_LIST_PATH}"
)
GWANGMYEONG_LANDING_URL = "https://lll.gm.go.kr/"
GWANGMYEONG_CIVIC_UNIVERSITY_URL = (
    "https://lll.gm.go.kr/index.do?menu_id=00005120"
)
GWANGMYEONG_GMCC_LIST_URL = "https://www.gmcc.or.kr/product_new/list.php"
GWANGMYEONG_GMCC_CATEGORIES: tuple[str, ...] = ("01", "02", "03")
GWANGMYEONG_GMCC_CATEGORY_URLS: tuple[str, ...] = tuple(
    f"{GWANGMYEONG_GMCC_LIST_URL}?ca_id={category}&page=1"
    for category in GWANGMYEONG_GMCC_CATEGORIES
)
GWANGMYEONG_PAGE_SIZE = 500
GWANGMYEONG_OFFICE_PAGE_SIZE = 100
GWANGMYEONG_MEDIA_PAGE_SIZE = 24
GWANGMYEONG_MAX_WORKERS = 6
GWANGMYEONG_FETCH_BATCH_ATTEMPTS = 2
GWANGMYEONG_MUNICIPALITY_CODE = "4121000000"
GWANGMYEONG_MUNICIPALITY_NAME = "경기도 광명시"
GWANGMYEONG_PARSER = (
    "gwangmyeong_ilms_single_owner+office73_of_declared74+"
    "global_learning_archive+gmcc3_authoritative_rollover+empty_media+"
    "partition_non_education+persistent_library_tombstones+"
    "audited_closed_external_http500_tombstones+official_branches+sentinels+"
    "page1_rechecks+current_bound_details+pii_allowlist"
)

GWANGMYEONG_LIBRARY_BRANCH_BY_SITE_CODE = {
    "ST01": "하안도서관",
    "ST02": "광명도서관",
    "ST03": "철산도서관",
    "ST04": "소하도서관",
    "ST05": "충현도서관",
    "ST06": "연서도서관",
    "ST50": "작은도서관",
}
GWANGMYEONG_YOUTH_BRANCHES = frozenset(
    {
        "광명시청소년수련관",
        "해냄청소년활동센터",
        "오름청소년활동센터",
        "나름청소년활동센터",
        "디딤청소년활동센터",
        "푸름청소년활동센터",
        "청소년상담복지센터",
        "청소년지원센터 꿈드림",
        "청소년진로진학지원센터",
        "청소년미디어센터",
        "청소년예술창작소",
    }
)


@dataclass(frozen=True)
class GwangmyeongOffice:
    code: str
    name: str


# Exact public directory membership observed 2026-07-22.  The page advertises
# 74 records but renders 73.  Its JSP explicitly suppresses one
# test/development institution; two historical rows identify it as
# ``평생학습원 테스트 기관``.  The server can move an institution between its
# featured and ordinary groups without changing its identity, so source order
# is checked for stability within one snapshot but is not part of this
# long-lived membership contract.
GWANGMYEONG_EXPECTED_OFFICES: tuple[GwangmyeongOffice, ...] = (
    GwangmyeongOffice("OFFICE_00002156", "광명시평생학습원"),
    GwangmyeongOffice("OFFICE_00002420", "광명시 1.5ºC 기후의병 지원센터"),
    GwangmyeongOffice("OFFICE_00002530", "광명시 고혈압·당뇨병 등록교육센터"),
    GwangmyeongOffice("API_OFFICE_00000020", "광명시도서관"),
    GwangmyeongOffice("API_OFFICE_00000030", "광명청소년재단"),
    GwangmyeongOffice("OFFICE_00002160", "도시재생지원센터"),
    GwangmyeongOffice("API_OFFICE_00000040", "업사이클아트센터"),
    GwangmyeongOffice("OFFICE_00002183", "철산1동주민자치회"),
    GwangmyeongOffice("OFFICE_00002170", "철산3동주민자치회"),
    GwangmyeongOffice("API_OFFICE_00000010", "여성비전센터"),
    GwangmyeongOffice("OFFICE_00002233", "인생플러스센터"),
    GwangmyeongOffice("OFFICE_00002580", "광명1동주민자치회"),
    GwangmyeongOffice("OFFICE_00002162", "광명2동주민자치회"),
    GwangmyeongOffice("OFFICE_00002231", "광명3동주민자치회"),
    GwangmyeongOffice("OFFICE_00002182", "광명4동주민자치회"),
    GwangmyeongOffice("OFFICE_00002181", "광명5동주민자치회"),
    GwangmyeongOffice("OFFICE_00002146", "광명6동주민자치회"),
    GwangmyeongOffice("OFFICE_00002164", "광명7동주민자치회"),
    GwangmyeongOffice("OFFICE_00002330", "광명건강생활지원센터"),
    GwangmyeongOffice("OFFICE_00002520", "광명문화예술교육지원센터"),
    GwangmyeongOffice("OFFICE_00002144", "광명문화원"),
    GwangmyeongOffice("OFFICE_00002550", "광명시 공익활동지원센터"),
    GwangmyeongOffice("OFFICE_00002410", "광명시 학교복합시설"),
    GwangmyeongOffice("OFFICE_00002173", "광명시공공급식지원센터"),
    GwangmyeongOffice("OFFICE_00002570", "광명시민인권센터"),
    GwangmyeongOffice("OFFICE_00002581", "광명시이노베이션센터"),
    GwangmyeongOffice("OFFICE_00002300", "광명시정신건강복지센터"),
    GwangmyeongOffice("OFFICE_00002154", "광명시치매안심센터"),
    GwangmyeongOffice("OFFICE_00002176", "광명시환경교육센터"),
    GwangmyeongOffice("OFFICE_00002600", "도시농업과"),
    GwangmyeongOffice("OFFICE_00002610", "도시재생과"),
    GwangmyeongOffice("OFFICE_00002175", "디지털혁신교육센터"),
    GwangmyeongOffice("OFFICE_00002166", "마을자치센터"),
    GwangmyeongOffice("OFFICE_00002371", "사회적경제센터"),
    GwangmyeongOffice("OFFICE_00002232", "새싹 작은도서관"),
    GwangmyeongOffice("OFFICE_00002177", "소하1동주민자치회"),
    GwangmyeongOffice("OFFICE_00002149", "소하2동주민자치회"),
    GwangmyeongOffice("OFFICE_00002240", "소하건강생활지원센터"),
    GwangmyeongOffice("OFFICE_00002235", "스마트인력개발센터"),
    GwangmyeongOffice("OFFICE_00002151", "일직동주민자치회"),
    GwangmyeongOffice("OFFICE_00002230", "정원도시과"),
    GwangmyeongOffice("OFFICE_00002184", "철산2동주민자치회"),
    GwangmyeongOffice("OFFICE_00002163", "철산4동주민자치회"),
    GwangmyeongOffice("OFFICE_00002560", "철산건강생활지원센터"),
    GwangmyeongOffice("OFFICE_00002145", "하안1동주민자치회"),
    GwangmyeongOffice("OFFICE_00002153", "하안2동주민자치회"),
    GwangmyeongOffice("OFFICE_00002152", "하안3동주민자치회"),
    GwangmyeongOffice("OFFICE_00002161", "하안4동주민자치회"),
    GwangmyeongOffice("OFFICE_00002157", "학온동주민자치회"),
    GwangmyeongOffice("OFFICE_00002540", "한국폴리텍대학 광명융합기술교육원"),
    GwangmyeongOffice("OFFICE_00002500", "광명문화재단"),
    GwangmyeongOffice("OFFICE_00002450", "광명시 자살예방센터"),
    GwangmyeongOffice("OFFICE_00002172", "광명시가족센터"),
    GwangmyeongOffice("OFFICE_00002451", "광명시민체육관"),
    GwangmyeongOffice("OFFICE_00002430", "광명시보건소"),
    GwangmyeongOffice("OFFICE_00002380", "광명시여성가족과"),
    GwangmyeongOffice("OFFICE_00002147", "광명시육아종합지원센터"),
    GwangmyeongOffice("OFFICE_00002165", "광명시치매안심센터(분소)"),
    GwangmyeongOffice("OFFICE_00002141", "광명장애인종합복지관"),
    GwangmyeongOffice("OFFICE_00002140", "광명종합사회복지관"),
    GwangmyeongOffice("OFFICE_00002490", "광명텃밭보급소"),
    GwangmyeongOffice("OFFICE_00002234", "교육청소년과"),
    GwangmyeongOffice("OFFICE_00002510", "기형도문학관"),
    GwangmyeongOffice("OFFICE_00002470", "노인건강증진센터"),
    GwangmyeongOffice("OFFICE_00002158", "소하노인종합복지관"),
    GwangmyeongOffice("OFFICE_00002440", "시민건강증진센터"),
    GwangmyeongOffice("OFFICE_00002178", "오리서원"),
    GwangmyeongOffice("OFFICE_00002640", "자치분권과"),
    GwangmyeongOffice("OFFICE_00002150", "철산종합사회복지관"),
    GwangmyeongOffice("OFFICE_00002143", "청년동"),
    GwangmyeongOffice("OFFICE_00002630", "청춘곳간"),
    GwangmyeongOffice("OFFICE_00002171", "하안노인복지관"),
    GwangmyeongOffice("OFFICE_00002155", "하안종합사회복지관"),
)
GWANGMYEONG_OFFICE_BY_CODE = {
    office.code: office for office in GWANGMYEONG_EXPECTED_OFFICES
}
GWANGMYEONG_OFFICE_BY_NAME = {
    office.name: office for office in GWANGMYEONG_EXPECTED_OFFICES
}
GWANGMYEONG_HIDDEN_TEST_OFFICE_NAME = "평생학습원 테스트 기관"
GWANGMYEONG_OFFICE_DECLARED_TOTAL = len(GWANGMYEONG_EXPECTED_OFFICES) + 1

GWANGMYEONG_OWNERSHIP_ALIAS_URLS: tuple[str, ...] = (
    GWANGMYEONG_LANDING_URL,
    GWANGMYEONG_OFFICE_URL,
    GWANGMYEONG_MEDIA_URL,
    *GWANGMYEONG_GMCC_CATEGORY_URLS,
)
GWANGMYEONG_ALLOWED_EXTERNAL_HOSTS = frozenset(
    {
        "docs.google.com",
        "dream.kopo.ac.kr",
        "forms.gle",
        "gmlib.gm.go.kr",
        "naver.me",
        "woman.gm.go.kr",
        "gmyouth.or.kr",
        "upcycle.gm.go.kr",
        "www.gmcf.or.kr",
        "www.gmcc.or.kr",
        "www.gmai.kr",
        "www.gmsocial.or.kr",
        "www.kopo.ac.kr",
    }
)
GWANGMYEONG_ALLOWED_LEARNING_TYPES = frozenset({"", "오프라인 강좌"})
# Upstream data-entry defects whose year is reversed in the official list.
# Keep this exact: repaired or newly malformed rows must stop the snapshot for
# a fresh audit instead of silently normalising an open-ended anomaly set.
GWANGMYEONG_KNOWN_REVERSED_PERIOD_SEQUENCES = frozenset({3787, 3785, 3783, 918})
GWANGMYEONG_KNOWN_CLOSED_EXTERNAL_HTTP500 = {
    "https://gmyouth.or.kr/www/viewLectureWebView.do?key=846&lectureNo=5706": {
        "title": "오름픽[오름 올림픽] '위기탈출 생존수영' 참가자 모집",
        "branch": "광명청소년재단",
        "period": "2026-08-05 ~ 2026-08-05",
        "apply_period": "2026-07-25 ~ 2026-07-28",
        "source_status": "선착순 교육대기 접수마감",
    }
}


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2}|\d{2})\s*(?:[./-]|년)\s*(\d{1,2})\s*(?:[./-]|월)\s*(\d{1,2})\s*일?(?!\d)"
)
_TOTAL_RE = re.compile(
    r"총\s*([0-9,]+)\s*건\s*\(\s*([0-9,]+)\s*/\s*([0-9,]+)\s*페이지"
)
_INTERNAL_ACTION_RE = re.compile(
    r"fn_learning_detail\(\s*['\"](LEARNING_[A-Za-z0-9_-]+)['\"]"
)
_EXTERNAL_ACTION_RE = re.compile(
    r"fn_learning_ex_detail\(\s*['\"](https://[^'\"]+)['\"]"
)
_OFFICE_ACTION_RE = re.compile(
    r"fn_learning_list\(\s*['\"]((?:API_)?OFFICE_[A-Za-z0-9_-]+)['\"]"
)
_CIVIC_ACTION_RE = re.compile(
    r"siblingDomainRedirect\(\s*['\"](/index[.]do[?]menu_id=00005120)['\"]"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_FACILITY_RE = re.compile(r"체력\s*단련실|자유\s*탁구")
_PERFORMANCE_RE = re.compile(r"뮤지컬|음악회|인형극|콘서트|공연|상영회")
_NOTICE_RE = re.compile(r"공지|사전\s*안내|예약\s*안내")
_EVENT_RE = re.compile(r"축제|페스티벌|박람회|행사")
_GWANGMYEONG_ADDRESS_RE = re.compile(
    r"(?:경기(?:도)?\s+)?광명시\s+[^,\n]{0,120}?(?:로|길|동)\s*\d"
)


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


def _exact_url(value: Any, host: str, path: str) -> bool:
    parsed = urlparse(_clean(value))
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == host
        and parsed.port is None
        and parsed.path == path
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_gwangmyeong_education_target(target: Any) -> bool:
    return _provider(target) in {
        GWANGMYEONG_PROVIDER,
        GWANGMYEONG_LEGACY_PROVIDER,
    } and _exact_url(
        _target_url(target), GWANGMYEONG_HOST, GWANGMYEONG_LEARNING_LIST_PATH
    )


def is_gwangmyeong_ownership_alias_target(target: Any) -> bool:
    return _target_url(target) in GWANGMYEONG_OWNERSHIP_ALIAS_URLS


is_target = is_gwangmyeong_education_target


def gwangmyeong_learning_list_url(page: Any = 1) -> str:
    raw = _clean(page)
    if not raw.isdigit() or int(raw) < 1:
        return ""
    return GWANGMYEONG_CANONICAL_URL + "?" + urlencode(
        {"pageIndex": int(raw), "pageUnit": GWANGMYEONG_PAGE_SIZE}
    )


def gwangmyeong_office_list_url(page: Any = 1) -> str:
    raw = _clean(page)
    if not raw.isdigit() or int(raw) < 1:
        return ""
    return GWANGMYEONG_OFFICE_URL + "?" + urlencode(
        {"pageIndex": int(raw), "office_pageUnit": GWANGMYEONG_OFFICE_PAGE_SIZE}
    )


def gwangmyeong_media_list_url(page: Any = 1) -> str:
    raw = _clean(page)
    if not raw.isdigit() or int(raw) < 1:
        return ""
    return GWANGMYEONG_MEDIA_URL + "?" + urlencode(
        {
            "pageIndex": int(raw),
            "pageUnit": GWANGMYEONG_MEDIA_PAGE_SIZE,
            "search_sort_order": "DESC",
        }
    )


def gwangmyeong_learning_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not re.fullmatch(r"LEARNING_[A-Za-z0-9_-]+", value):
        return ""
    return (
        f"https://{GWANGMYEONG_HOST}{GWANGMYEONG_LEARNING_DETAIL_PATH}?"
        + urlencode({"lng_id": value})
    )


def gwangmyeong_gmcc_list_url(category: Any, page: Any = 1) -> str:
    category_value = _clean(category)
    page_value = _clean(page)
    if category_value not in GWANGMYEONG_GMCC_CATEGORIES:
        return ""
    if not page_value.isdigit() or int(page_value) < 1:
        return ""
    return GWANGMYEONG_GMCC_LIST_URL + "?" + urlencode(
        {"ca_id": category_value, "page": int(page_value)}
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


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    last_error: Optional[Exception] = None
    for _attempt in range(3):
        try:
            response = current.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _coerce_soup(value: Any, *, expected_hosts: Optional[set[str]] = None) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    final_url = _clean(getattr(value, "url", ""))
    if final_url:
        parsed = urlparse(final_url)
        host = (parsed.hostname or "").rstrip(".").lower()
        if (
            parsed.scheme.lower() != "https"
            or parsed.username
            or parsed.password
            or parsed.port is not None
            or (expected_hosts is not None and host not in expected_hosts)
        ):
            raise ValueError("unsafe final response URL")
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _fetch_many(
    items: list[tuple[Any, str]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    expected_hosts: Optional[set[str]] = None,
) -> tuple[dict[Any, BeautifulSoup], list[str]]:
    if not items:
        return {}, []
    workers = max(1, min(int(max_workers or 1), len(items)))
    chunks: list[list[tuple[Any, str]]] = [[] for _ in range(workers)]
    for index, item in enumerate(items):
        chunks[index % workers].append(item)

    def run(chunk: list[tuple[Any, str]]) -> tuple[dict[Any, BeautifulSoup], list[str]]:
        values: dict[Any, BeautifulSoup] = {}
        errors: list[str] = []
        current = session_factory()
        try:
            for key, url in chunk:
                last_error: Optional[Exception] = None
                for attempt in range(GWANGMYEONG_FETCH_BATCH_ATTEMPTS):
                    try:
                        response = fetcher(current, url, timeout)
                        values[key] = _coerce_soup(
                            response, expected_hosts=expected_hosts
                        )
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt + 1 < GWANGMYEONG_FETCH_BATCH_ATTEMPTS:
                            _close_quietly(current)
                            current = session_factory()
                if last_error is not None:
                    errors.append(
                        f"{key}: {type(last_error).__name__}: {_clean(last_error)}"
                    )
        finally:
            _close_quietly(current)
        return values, errors

    results: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run, chunk) for chunk in chunks if chunk]
        for future in as_completed(futures):
            values, current_errors = future.result()
            results.update(values)
            errors.extend(current_errors)
    return results, errors


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        full_year = int(year) if len(year) == 4 else 2000 + int(year)
        try:
            result.append(date(full_year, int(month), int(day)))
        except ValueError:
            return []
    return result


def _total(soup: BeautifulSoup) -> tuple[int, int, int]:
    match = _TOTAL_RE.search(_clean(soup.get_text(" ", strip=True)))
    if not match:
        raise ValueError("declared total/page marker missing")
    total, current, last = (
        int(value.replace(",", "")) for value in match.groups()
    )
    if total < 0 or current < 1 or last < 1:
        raise ValueError("invalid declared total/page marker")
    return total, current, last


def _parse_offices(
    soup: BeautifulSoup,
) -> tuple[list[GwangmyeongOffice], list[str]]:
    offices: list[GwangmyeongOffice] = []
    errors: list[str] = []
    seen: set[str] = set()
    for item in soup.select("ul.e_lst_type01 > li"):
        link = item.select_one("a[onclick*='fn_learning_list']")
        name_node = item.select_one("strong")
        if link is None or name_node is None:
            errors.append("institution directory has malformed card")
            continue
        match = _OFFICE_ACTION_RE.search(_clean(link.get("onclick")))
        code = match.group(1) if match else ""
        name = _clean(name_node.get_text(" ", strip=True))
        if not code or not name or code in seen:
            errors.append("institution directory has invalid/duplicate identity")
            continue
        checkbox = item.select_one("input.check_arr")
        if checkbox is None or _clean(checkbox.get("value")) != code:
            errors.append(f"{code}: institution checkbox identity mismatch")
        seen.add(code)
        offices.append(GwangmyeongOffice(code, name))
    return offices, errors


def _office_contract_matches(offices: Iterable[GwangmyeongOffice]) -> bool:
    values = list(offices)
    return bool(
        len(values) == len(GWANGMYEONG_EXPECTED_OFFICES)
        and len(set(values)) == len(values)
        and set(values) == set(GWANGMYEONG_EXPECTED_OFFICES)
    )


def _safe_external_url(value: str) -> str:
    parsed = urlparse(_clean(value))
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in GWANGMYEONG_ALLOWED_EXTERNAL_HOSTS
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return ""
    return parsed.geturl()


def _partition(title: str, office: str) -> str:
    if office == GWANGMYEONG_HIDDEN_TEST_OFFICE_NAME:
        return "development"
    if title.startswith("[접수 테스트"):
        return "development"
    if "테스트" in title and (
        "접수불가" in title or "신청하지 마세요" in title or title.endswith("테스트")
    ):
        return "development"
    if _FACILITY_RE.search(title):
        return "facility"
    if _NOTICE_RE.search(title):
        return "notice"
    if _EVENT_RE.search(title):
        return "event"
    if title.startswith("[오픈시네마]") or _PERFORMANCE_RE.search(title):
        return "performance"
    if "체험" in title:
        return "experience"
    return "education"


def _status(value: str) -> str:
    text = _clean(value)
    if "취소" in text or "폐강" in text:
        return "CANCELLED"
    if "접수중" in text or "대기접수" in text:
        return "OPEN"
    if "예정" in text:
        return "SCHEDULED"
    if "대기" in text and "접수마감" not in text and "마감" not in text:
        return "SCHEDULED"
    return "CLOSED"


def _legacy_course_id(sequence: int) -> str:
    return f"{GWANGMYEONG_PROVIDER}:learning:{sequence}"


def _stable_identity_token(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()[:24]


def _course_id(
    *,
    action_kind: str,
    identity: str,
    raw_url: str,
    title: str,
    office: str,
    start: date,
    end: date,
) -> str:
    if action_kind == "internal":
        stable_identity = identity
    elif action_kind == "external":
        external_key = raw_url
        if raw_url.startswith(f"{GWANGMYEONG_GMCC_LIST_URL}?"):
            external_key = "\x1f".join(
                (raw_url, title, office, start.isoformat(), end.isoformat())
            )
        stable_identity = _stable_identity_token(external_key)
    else:
        source_key = "\x1f".join(
            (title, office, start.isoformat(), end.isoformat())
        )
        stable_identity = _stable_identity_token(source_key)
    return (
        f"{GWANGMYEONG_PROVIDER}:learning:{action_kind}:{stable_identity}"
    )


def _page_signature(rows: list[dict[str, Any]]) -> str:
    values = [
        "\x1f".join(
            (
                str(row["raw_fields"]["list_sequence"]),
                _clean(row.get("title")),
                _clean(row.get("branch")),
                _clean(row.get("raw_url")),
            )
        )
        for row in rows
    ]
    return hashlib.sha256("\x1e".join(values).encode("utf-8")).hexdigest()


def _parse_learning_page(
    soup: BeautifulSoup, *, page: int
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    tables = [table for table in soup.select("table.lecture") if table.select("thead th")]
    if len(tables) != 1:
        return [], [f"learning page {page}: expected one course table"]
    table = tables[0]
    headings = [_clean(node.get_text(" ", strip=True)) for node in table.select("thead th")]
    required = ("번호", "강좌명", "강좌유형", "교육기간", "신청기간", "상태", "보기")
    if len(headings) != 7 or any(
        token not in headings[index] for index, token in enumerate(required)
    ):
        errors.append(f"learning page {page}: unexpected table headers")

    for source_row in table.select("tbody tr"):
        cells = source_row.select("td")
        title_link = source_row.select_one("td.subject a[onclick]")
        if title_link is None:
            empty = _clean(source_row.get_text(" ", strip=True))
            if not empty or ("등록" in empty and "없" in empty):
                continue
            errors.append(f"learning page {page}: non-course table row")
            continue
        if len(cells) != 7:
            errors.append(f"learning page {page}: course row is not seven columns")
            continue
        sequence_raw = _clean(cells[0].get_text(" ", strip=True)).replace(",", "")
        title_node = title_link.select_one(".tit")
        office_node = title_link.select_one(".org")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        office = _clean(office_node.get_text(" ", strip=True) if office_node else "")
        learning_type = _clean(cells[2].get_text(" ", strip=True))
        onclick = _clean(title_link.get("onclick"))
        internal_match = _INTERNAL_ACTION_RE.search(onclick)
        external_match = _EXTERNAL_ACTION_RE.search(onclick)
        civic_match = _CIVIC_ACTION_RE.search(onclick)
        row_errors: list[str] = []
        if not sequence_raw.isdigit() or int(sequence_raw) < 1:
            row_errors.append("invalid source sequence")
            sequence = 0
        else:
            sequence = int(sequence_raw)
        if not title:
            row_errors.append("empty course title")
        if (
            office not in GWANGMYEONG_OFFICE_BY_NAME
            and office != GWANGMYEONG_HIDDEN_TEST_OFFICE_NAME
        ):
            row_errors.append(f"unknown source institution {office!r}")
        if learning_type not in GWANGMYEONG_ALLOWED_LEARNING_TYPES:
            row_errors.append(f"unknown learning type {learning_type!r}")

        action_count = sum(
            match is not None
            for match in (internal_match, external_match, civic_match)
        )
        if action_count != 1:
            row_errors.append("missing/ambiguous course-bound action")
            identity = ""
            raw_url = ""
            action_kind = ""
        elif internal_match:
            identity = internal_match.group(1)
            raw_url = gwangmyeong_learning_detail_url(identity)
            action_kind = "internal"
        elif external_match:
            raw_url = _safe_external_url(external_match.group(1))
            identity = f"external:{sequence}"
            action_kind = "external"
            if not raw_url:
                row_errors.append("unsafe/unowned external detail URL")
        else:
            identity = f"civic:{sequence}"
            raw_url = GWANGMYEONG_CIVIC_UNIVERSITY_URL
            action_kind = "civic_university"

        period_dates = _dates(cells[3].get_text(" ", strip=True))
        reversed_period = False
        if len(period_dates) != 2:
            row_errors.append("education period is not exactly two ordered dates")
            start = end = date.min
        else:
            start, end = period_dates
            if end < start:
                if sequence not in GWANGMYEONG_KNOWN_REVERSED_PERIOD_SEQUENCES:
                    row_errors.append("unexpected reversed education period")
                start, end = sorted((start, end))
                reversed_period = True
        apply_dates = _dates(cells[4].get_text(" ", strip=True))
        if len(apply_dates) % 2:
            row_errors.append("application period has unexpected date count")
        apply_period = (
            f"{apply_dates[-2].isoformat()} ~ {apply_dates[-1].isoformat()}"
            if len(apply_dates) >= 2
            else ""
        )
        source_status = _clean(cells[5].get_text(" ", strip=True))
        if not source_status:
            row_errors.append("empty source status")
        if row_errors:
            errors.extend(
                f"learning page {page} sequence {sequence_raw or '?'}: {message}"
                for message in row_errors
            )
            continue
        partition = _partition(title, office)
        rows.append(
            {
                "provider": GWANGMYEONG_PROVIDER,
                "provider_course_id": _course_id(
                    action_kind=action_kind,
                    identity=identity,
                    raw_url=raw_url,
                    title=title,
                    office=office,
                    start=start,
                    end=end,
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": office,
                "branch_code": (
                    GWANGMYEONG_OFFICE_BY_NAME[office].code
                    if office in GWANGMYEONG_OFFICE_BY_NAME
                    else "HIDDEN_TEST_OFFICE"
                ),
                "category": "교육",
                "raw_url": raw_url,
                "application_url": "",
                "status": _status(source_status),
                "fee": "별도 안내",
                "period": f"{start.isoformat()} ~ {end.isoformat()}",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "apply_period": apply_period,
                "schedule_raw": _clean(cells[3].get_text(" ", strip=True)),
                "target": "",
                "capacity": _clean(cells[4].get_text(" ", strip=True)),
                "raw_fields": {
                    "catalogue": "learning",
                    "identity": identity,
                    "legacy_provider_course_id": _legacy_course_id(sequence),
                    "list_sequence": sequence,
                    "source_learning_type": learning_type,
                    "source_status": source_status,
                    "action_kind": action_kind,
                    "partition": partition,
                    "source_period_reversed": reversed_period,
                },
            }
        )
    return rows, errors


def _parse_media_page(
    soup: BeautifulSoup, *, page: int
) -> tuple[list[dict[str, Any]], list[str]]:
    links = soup.select("a[onclick*='fn_detail']")
    if links:
        return [], [
            f"media page {page}: video catalogue activated; parser review required"
        ]
    return [], []


def _title_key(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).lower()


def _contains_course_title(body: str, title: str) -> bool:
    body_key = _title_key(body)
    title_key = _title_key(title)
    if len(title_key) < 2:
        return False
    if title_key in body_key:
        return True
    # External systems occasionally remove a word such as ``교실`` or insert
    # a season label while retaining the same bound query identity.
    compact = title_key.replace("교실", "").replace("강좌", "")
    if len(compact) >= 6 and compact in body_key:
        return True
    return SequenceMatcher(None, title_key, body_key[: max(len(title_key) * 3, 60)]).ratio() >= 0.72


def _detail_dates_match(row: Mapping[str, Any], body: str) -> bool:
    values = set(_dates(body))
    try:
        start = date.fromisoformat(_clean(row.get("start_date")))
        end = date.fromisoformat(_clean(row.get("end_date")))
    except ValueError:
        return False
    return start in values and end in values


def _gmlib_tombstone(row: Mapping[str, Any], soup: BeautifulSoup) -> bool:
    """Recognise only the library's exact public deleted-course response."""

    parsed = urlparse(_clean(row.get("raw_url")))
    if (parsed.hostname or "").lower() != "gmlib.gm.go.kr":
        return False
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    scripts = " ".join(_clean(node.get_text(" ", strip=True)) for node in soup.select("script"))
    return bool(
        title == "알림 페이지"
        and re.search(
            r"alert\s*\(\s*['\"]등록된 강좌가 없습니다[.]\s*['\"]\s*\)",
            scripts,
        )
        and re.search(r"history[.]go\s*\(\s*-1\s*\)", scripts)
    )


def _known_closed_external_http500_tombstone(
    row: Mapping[str, Any], fetch_errors: Iterable[str]
) -> bool:
    """Match an audited closed row whose bound external detail is broken."""

    raw_url = _clean(row.get("raw_url"))
    expected = GWANGMYEONG_KNOWN_CLOSED_EXTERNAL_HTTP500.get(raw_url)
    raw_fields = row.get("raw_fields", {})
    if expected is None:
        return False
    if (
        row.get("status") != "CLOSED"
        or _clean(raw_fields.get("action_kind")) != "external"
        or _clean(row.get("title")) != expected["title"]
        or _clean(row.get("branch")) != expected["branch"]
        or _clean(row.get("period")) != expected["period"]
        or _clean(row.get("apply_period")) != expected["apply_period"]
        or _clean(raw_fields.get("source_status")) != expected["source_status"]
    ):
        return False
    prefix = f"{raw_url}: HTTPError: 500 "
    return any(_clean(error).startswith(prefix) for error in fetch_errors)


def _labelled_table_value(soup: BeautifulSoup, label: str) -> str:
    for heading in soup.select("th"):
        if _clean(heading.get_text(" ", strip=True)) != label:
            continue
        value = heading.find_next_sibling("td")
        if value is not None:
            return _clean(value.get_text(" ", strip=True))
    return ""


def _labelled_detail_value(soup: BeautifulSoup, *labels: str) -> str:
    expected = {_clean(label) for label in labels if _clean(label)}
    for heading in soup.select("dt, th"):
        if _clean(heading.get_text(" ", strip=True)).rstrip(":") not in expected:
            continue
        value = heading.find_next_sibling(["dd", "td"])
        if value is not None:
            return _clean(value.get_text(" ", strip=True))
    return ""


def _set_experience_fixed_location(
    row: dict[str, Any], soup: BeautifulSoup
) -> None:
    if _clean(row.get("raw_fields", {}).get("partition")) != "experience":
        return
    location = _labelled_detail_value(
        soup,
        "교육장소",
        "강좌 장소",
        "강좌장소",
        "행사장소",
        "장소",
    )
    raw_fields = row.setdefault("raw_fields", {})
    if not location or not _GWANGMYEONG_ADDRESS_RE.search(location):
        raw_fields["experience_fixed_venue_verified"] = False
        raw_fields["experience_exclusion_reason"] = "fixed_gwangmyeong_address_missing"
        return
    row.update(
        {
            "venue_name": location,
            "venue_address": location,
            "municipality_code": GWANGMYEONG_MUNICIPALITY_CODE,
            "municipality_name": GWANGMYEONG_MUNICIPALITY_NAME,
        }
    )
    raw_fields["experience_fixed_venue_verified"] = True


def _lock_programme_scope(row: dict[str, Any]) -> None:
    partition = _clean(row.get("raw_fields", {}).get("partition"))
    common = {
        "collection_category": "공공예약",
        "source_group": "municipal_reservation",
        "service_group_policy": "locked",
        "classification_locked": True,
        "municipality_code": GWANGMYEONG_MUNICIPALITY_CODE,
        "municipality_name": GWANGMYEONG_MUNICIPALITY_NAME,
    }
    if partition == "experience":
        row.update(
            {
                **common,
                "category": "체험",
                "category_raw": "체험",
                "program_type": "체험",
                "domain_category": "체험·견학",
                "service_group": "체험",
            }
        )
        return
    row.update(
        {
            **common,
            "category": "교육",
            "category_raw": "교육",
            "program_type": "교육",
            "domain_category": "교육·강좌",
            "service_group": "공공강좌",
        }
    )


def _set_official_external_branch(
    row: dict[str, Any], soup: BeautifulSoup
) -> list[str]:
    """Replace aggregate office labels with course-bound official branches."""

    raw_url = _clean(row.get("raw_url"))
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    body = _clean(soup.get_text(" ", strip=True))
    sequence = row.get("raw_fields", {}).get("list_sequence")
    if host == "gmlib.gm.go.kr":
        site_code = _clean((parse_qs(parsed.query).get("siteCode") or [""])[0])
        branch = GWANGMYEONG_LIBRARY_BRANCH_BY_SITE_CODE.get(site_code, "")
        if not branch or branch not in body:
            return [f"sequence {sequence}: unknown library branch {site_code!r}"]
        row["branch"] = branch
        row["branch_code"] = f"API_OFFICE_00000020:{site_code}"
    elif host == "gmyouth.or.kr":
        branch = _labelled_table_value(soup, "기관명")
        if branch not in GWANGMYEONG_YOUTH_BRANCHES:
            return [f"sequence {sequence}: unknown youth branch {branch!r}"]
        row["branch"] = branch
        row["branch_code"] = f"API_OFFICE_00000030:{branch}"
    return []


def _public_application_control(
    row: Mapping[str, Any], soup: BeautifulSoup
) -> str:
    if _clean(row.get("status")) != "OPEN":
        return ""
    raw_url = _clean(row.get("raw_url"))
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    scope = soup.select_one("#contents, #content, main") or soup.body or soup
    for node in scope.select("a, button, input[type='submit'], input[type='button']"):
        text = _clean(node.get("value") or node.get_text(" ", strip=True))
        href = _clean(node.get("href"))
        onclick = _clean(node.get("onclick"))
        blob = f"{text} {href} {onclick}"
        if host == GWANGMYEONG_HOST:
            if "fn_learning_apply" in onclick or node.get("id") == "learning_aply_btn":
                return raw_url
            continue
        if host == "gmlib.gm.go.kr":
            query = parse_qs(parsed.query)
            course_code = _clean((query.get("leCode") or [""])[0])
            if course_code and course_code in blob and (
                "LOGIN" in text.upper() or "신청" in text
            ):
                return raw_url
            continue
        if host == "woman.gm.go.kr":
            course_code = _clean((parse_qs(parsed.query).get("eduNo") or [""])[0])
            if course_code and course_code in blob and "신청" in text:
                return raw_url
            continue
        if host == "upcycle.gm.go.kr":
            if text == "신청하기":
                return raw_url
            continue
        if host == "gmyouth.or.kr":
            if text == "프로그램 신청" and node.name == "button":
                return raw_url
            continue
        if host == "www.gmcc.or.kr":
            if "수강신청" in text or text == "신청하기":
                return raw_url
            continue
        if host == "www.gmai.kr" and text in {"교육 신청", "지원하기"}:
            absolute = urljoin(raw_url, href)
            return absolute if _safe_external_url(absolute) else raw_url
    return ""


def _validate_internal_detail(
    row: dict[str, Any], soup: BeautifulSoup
) -> list[str]:
    errors: list[str] = []
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    input_identity = soup.select_one("input[name='lng_id']")
    if input_identity is not None and _clean(input_identity.get("value")) != identity:
        errors.append(f"{identity}: internal detail identity mismatch")
    heading = soup.select_one("h2.enrolTit, h2")
    heading_text = _clean(heading.get_text(" ", strip=True) if heading else "")
    if not _contains_course_title(heading_text, _clean(row.get("title"))):
        errors.append(f"{identity}: internal detail title mismatch")
    body = _clean(soup.get_text(" ", strip=True))
    if not _detail_dates_match(row, body):
        errors.append(f"{identity}: internal detail period mismatch")
    row["application_url"] = _public_application_control(row, soup)
    if not errors:
        _set_experience_fixed_location(row, soup)
    return errors


def _validate_civic_university_detail(
    row: dict[str, Any], soup: BeautifulSoup
) -> list[str]:
    sequence = row.get("raw_fields", {}).get("list_sequence")
    body = _clean(soup.get_text(" ", strip=True))
    year_match = re.search(r"(20\d{2})", _clean(row.get("title")))
    expected_year = year_match.group(1) if year_match else ""
    if (
        "광명자치대학 입학신청" not in body
        or "광명자치대학" not in _clean(row.get("title"))
        or not expected_year
        or f"{expected_year}년 광명자치대학" not in body
    ):
        return [f"sequence {sequence}: civic-university detail mismatch"]
    # The current page publishes the recruitment period, capacity and status;
    # it does not repeat the education end date from ILMS.
    apply_period = _clean(row.get("apply_period"))
    apply_dates = _dates(apply_period)
    if apply_dates and not all(value in set(_dates(body)) for value in apply_dates):
        return [f"sequence {sequence}: civic-university application period mismatch"]
    row["application_url"] = _public_application_control(row, soup)
    return []


def _validate_external_detail(
    row: dict[str, Any], soup: BeautifulSoup
) -> list[str]:
    sequence = row.get("raw_fields", {}).get("list_sequence")
    raw_url = _clean(row.get("raw_url"))
    host = (urlparse(raw_url).hostname or "").lower()
    body = _clean(soup.get_text(" ", strip=True))
    if host == "www.gmai.kr":
        required = ("AI", "AX", "엔지니어", "교육기간")
        title_ok = all(token in body for token in required)
        start = date.fromisoformat(_clean(row.get("start_date")))
        end = date.fromisoformat(_clean(row.get("end_date")))
        date_ok = start in set(_dates(body)) and any(
            token in body
            for token in (
                end.strftime("%Y.%m.%d"),
                end.strftime("%Y-%m-%d"),
                end.strftime("%m.%d"),
            )
        )
    else:
        title_ok = _contains_course_title(body, _clean(row.get("title")))
        date_ok = _detail_dates_match(row, body)
    if not title_ok:
        return [f"sequence {sequence}: external detail title mismatch ({host})"]
    row.setdefault("raw_fields", {})["detail_period_match"] = date_ok
    if (
        host == "www.gmai.kr"
        or _clean(row.get("raw_fields", {}).get("partition")) == "experience"
    ) and not date_ok:
        return [f"sequence {sequence}: external detail period mismatch ({host})"]
    branch_errors = _set_official_external_branch(row, soup)
    if branch_errors:
        return branch_errors
    row["application_url"] = _public_application_control(row, soup)
    _set_experience_fixed_location(row, soup)
    return []


def _validate_detail_row(
    row: dict[str, Any], soup: BeautifulSoup
) -> list[str]:
    kind = _clean(row.get("raw_fields", {}).get("action_kind"))
    if kind == "internal":
        return _validate_internal_detail(row, soup)
    if kind == "civic_university":
        return _validate_civic_university_detail(row, soup)
    return _validate_external_detail(row, soup)


def _gmcc_cards(soup: BeautifulSoup) -> list[tuple[str, str, set[date]]]:
    candidates: list[tuple[str, str, set[date]]] = []
    seen: set[str] = set()
    for link in soup.select("a[href*='item.php'][href*='it_id=']"):
        href = _safe_external_url(
            urljoin(
                "https://www.gmcc.or.kr/product_new/list.php",
                _clean(link.get("href")),
            )
        )
        if not href or href in seen:
            continue
        seen.add(href)
        text = _clean(link.get_text(" ", strip=True))
        candidates.append((href, text, set(_dates(text))))
    return candidates


def _gmcc_page_signature(soup: BeautifulSoup) -> str:
    values = [href for href, _text, _dates_value in _gmcc_cards(soup)]
    return hashlib.sha256("\x1e".join(values).encode("utf-8")).hexdigest()


def _gmcc_advertised_last(soup: BeautifulSoup) -> int:
    pages = {1}
    for link in soup.select("a[href*='product_new/list.php']"):
        for value in re.findall(r"[?&]page=(\d+)", _clean(link.get("href"))):
            pages.add(int(value))
    return max(pages)


def _resolve_gmcc_details(
    rows: list[dict[str, Any]],
    soups: BeautifulSoup | Iterable[BeautifulSoup],
    *,
    allow_unresolved: bool = False,
) -> tuple[dict[int, str], list[str]]:
    soup_values = [soups] if isinstance(soups, BeautifulSoup) else list(soups)
    candidates: list[tuple[str, str, set[date]]] = []
    seen_candidates: set[str] = set()
    for soup in soup_values:
        for candidate in _gmcc_cards(soup):
            if candidate[0] not in seen_candidates:
                seen_candidates.add(candidate[0])
                candidates.append(candidate)
    result: dict[int, str] = {}
    errors: list[str] = []
    used: set[str] = set()
    for row in rows:
        sequence = int(row["raw_fields"]["list_sequence"])
        title_key = _title_key(row.get("title"))
        start = date.fromisoformat(_clean(row.get("start_date")))
        end = date.fromisoformat(_clean(row.get("end_date")))
        ranked: list[tuple[float, str]] = []
        for href, text, dates in candidates:
            if href in used or start not in dates or end not in dates:
                continue
            candidate_title = text.split("접수기간", 1)[0]
            candidate_key = _title_key(candidate_title)
            score = (
                1.0
                if title_key == candidate_key
                else SequenceMatcher(None, title_key, candidate_key).ratio()
            )
            if title_key in candidate_key or candidate_key in title_key:
                score = max(score, 0.95)
            ranked.append((score, href))
        ranked.sort(reverse=True)
        if not ranked or ranked[0][0] < 0.72:
            if not allow_unresolved:
                errors.append(
                    f"sequence {sequence}: culture-centre detail could not be resolved"
                )
            continue
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            errors.append(f"sequence {sequence}: ambiguous culture-centre detail")
            continue
        result[sequence] = ranked[0][1]
        used.add(ranked[0][1])
    return result, errors


def _parse_gmcc_special_rows(
    soups: Iterable[BeautifulSoup],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    office = GWANGMYEONG_OFFICE_BY_NAME.get("광명문화원")
    branch_code = office.code if office else "OFFICE_00002144"
    for soup in soups:
        for raw_url, text, values in _gmcc_cards(soup):
            if raw_url in seen:
                errors.append("duplicate culture-centre special-course identity")
                continue
            seen.add(raw_url)
            title = _clean(text.split("접수기간", 1)[0])
            dates = _dates(text)
            query = parse_qs(urlparse(raw_url).query)
            identity = _clean((query.get("it_id") or [""])[0])
            if not title or not identity or len(dates) != 4:
                errors.append("malformed culture-centre special-course card")
                continue
            apply_start, apply_end, start, end = dates
            if apply_end < apply_start or end < start:
                errors.append(f"gmcc {identity}: invalid date order")
                continue
            schedule_match = re.search(
                r"수업시간\s*(.*?)\s*강사명", text
            )
            schedule = _clean(schedule_match.group(1) if schedule_match else "")
            if "수강신청 예정" in text:
                source_status = "접수예정"
            elif "신청마감" in text or "수강신청 마감" in text:
                source_status = "신청마감"
            elif "수강신청 가능" in text or "접수중" in text:
                source_status = "접수중"
            else:
                errors.append(f"gmcc {identity}: unknown application status")
                continue
            category_value = _clean(
                (parse_qs(urlparse(raw_url).query).get("ca_id") or [""])[0]
            )
            catalogue = (
                "gmcc_special" if category_value == "03" else "gmcc_culture_school"
            )
            rows.append(
                {
                    "provider": GWANGMYEONG_PROVIDER,
                    "provider_course_id": f"{GWANGMYEONG_PROVIDER}:gmcc:{identity}",
                    "title": title,
                    "branch": "광명문화원",
                    "branch_code": branch_code,
                    "category": "교육",
                    "raw_url": raw_url,
                    "application_url": "",
                    "status": _status(source_status),
                    "fee": "별도 안내",
                    "period": f"{start.isoformat()} ~ {end.isoformat()}",
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "apply_period": (
                        f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
                    ),
                    "schedule_raw": schedule,
                    "target": "",
                    "capacity": "",
                    "raw_fields": {
                        "catalogue": catalogue,
                        "identity": identity,
                        "list_sequence": f"gmcc:{identity}",
                        "source_learning_type": (
                            "외부 공식 특강"
                            if category_value == "03"
                            else "외부 공식 문화학교"
                        ),
                        "source_status": source_status,
                        "action_kind": "external",
                        "partition": "education",
                        "source_period_reversed": False,
                    },
                }
            )
    return rows, errors


def _audit_gmcc_catalogue(
    regular_rows: list[dict[str, Any]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    max_requests: Optional[int] = None,
) -> tuple[dict[int, str], list[dict[str, Any]], dict[str, Any], list[str], int]:
    categories = GWANGMYEONG_GMCC_CATEGORIES
    minimum_requests = len(categories) * 3
    audit_meta: dict[str, Any] = {
        "gmcc_required_list_requests": minimum_requests,
        "gmcc_list_requests": 0,
        "gmcc_advertised_last_pages": {},
        "gmcc_category_counts": {},
        "gmcc_regular_catalogue_count": 0,
        "gmcc_regular_ilms_count": len(regular_rows),
        "gmcc_regular_resolved_count": 0,
        "gmcc_regular_stale_mirror_count": 0,
        "gmcc_official_catalogue_count": 0,
        "gmcc_special_catalogue_count": 0,
        "gmcc_source_cap_reached": False,
    }
    request_budget = (
        None if max_requests is None else max(0, int(max_requests))
    )
    if request_budget is not None and request_budget < len(categories):
        audit_meta["gmcc_source_cap_reached"] = True
        return (
            {},
            [],
            audit_meta,
            [
                f"culture-centre request cap allows {request_budget} of at least "
                f"{minimum_requests} required requests"
            ],
            0,
        )

    bootstrap_items = [
        ((category, 1, "data"), gwangmyeong_gmcc_list_url(category, 1))
        for category in categories
    ]
    fetched, fetch_errors = _fetch_many(
        bootstrap_items,
        fetcher=fetcher,
        session_factory=session_factory,
        timeout=timeout,
        max_workers=max_workers,
        expected_hosts={"www.gmcc.or.kr"},
    )
    errors = list(fetch_errors)
    advertised_last_pages: dict[str, int] = {}
    for category in categories:
        page_one = fetched.get((category, 1, "data"))
        if page_one is None:
            errors.append(f"gmcc category {category}: missing page one")
            continue
        last = _gmcc_advertised_last(page_one)
        if last < 1:
            errors.append(f"gmcc category {category}: invalid advertised last page")
            continue
        advertised_last_pages[category] = last

    required_requests = sum(
        advertised_last_pages.get(category, 1) + 2 for category in categories
    )
    audit_meta["gmcc_required_list_requests"] = required_requests
    audit_meta["gmcc_advertised_last_pages"] = dict(advertised_last_pages)
    if errors:
        audit_meta["gmcc_list_requests"] = len(fetched)
        return {}, [], audit_meta, errors, len(fetched)
    if request_budget is not None and required_requests > request_budget:
        audit_meta["gmcc_source_cap_reached"] = True
        audit_meta["gmcc_list_requests"] = len(fetched)
        errors.append(
            f"culture-centre request cap allows {request_budget} of "
            f"{required_requests} required requests"
        )
        return {}, [], audit_meta, errors, len(fetched)

    remaining_items: list[tuple[Any, str]] = []
    for category, last in advertised_last_pages.items():
        remaining_items.extend(
            (
                (category, page, "data"),
                gwangmyeong_gmcc_list_url(category, page),
            )
            for page in range(2, last + 1)
        )
        remaining_items.extend(
            [
                (
                    (category, last + 1, "sentinel"),
                    gwangmyeong_gmcc_list_url(category, last + 1),
                ),
                (
                    (category, 1, "recheck"),
                    gwangmyeong_gmcc_list_url(category, 1),
                ),
            ]
        )
    remaining, remaining_errors = _fetch_many(
        remaining_items,
        fetcher=fetcher,
        session_factory=session_factory,
        timeout=timeout,
        max_workers=max_workers,
        expected_hosts={"www.gmcc.or.kr"},
    )
    fetched.update(remaining)
    errors.extend(remaining_errors)
    category_soups: dict[str, list[BeautifulSoup]] = defaultdict(list)
    category_counts: dict[str, int] = {}
    all_urls: list[str] = []
    for category, last in advertised_last_pages.items():
        data_soups: list[BeautifulSoup] = []
        for page in range(1, last + 1):
            soup = fetched.get((category, page, "data"))
            if soup is None:
                errors.append(f"gmcc category {category} page {page}: missing response")
                continue
            cards = _gmcc_cards(soup)
            if not cards:
                errors.append(f"gmcc category {category} page {page}: empty data page")
            for raw_url, _text, _dates_value in cards:
                query_category = _clean(
                    (parse_qs(urlparse(raw_url).query).get("ca_id") or [""])[0]
                )
                if query_category != category:
                    errors.append(
                        f"gmcc category {category} page {page}: card category mismatch"
                    )
                all_urls.append(raw_url)
            data_soups.append(soup)
        category_soups[category] = data_soups
        category_counts[category] = sum(len(_gmcc_cards(soup)) for soup in data_soups)
        page_one = fetched.get((category, 1, "data"))
        sentinel = fetched.get((category, last + 1, "sentinel"))
        recheck = fetched.get((category, 1, "recheck"))
        if page_one is None or sentinel is None or recheck is None:
            errors.append(f"gmcc category {category}: missing sentinel/recheck")
        else:
            if _gmcc_advertised_last(page_one) != last:
                errors.append(f"gmcc category {category}: advertised last page changed")
            if _gmcc_cards(sentinel):
                errors.append(
                    f"gmcc category {category}: immediate post-last page is not empty"
                )
            if _gmcc_page_signature(page_one) != _gmcc_page_signature(recheck):
                errors.append(f"gmcc category {category}: page-one recheck changed")
    if len(all_urls) != len(set(all_urls)):
        errors.append("gmcc catalogue contains duplicate course identities")

    resolved, resolve_errors = _resolve_gmcc_details(
        regular_rows,
        [
            soup
            for category in categories[:2]
            for soup in category_soups.get(category, [])
        ],
        allow_unresolved=True,
    )
    errors.extend(resolve_errors)
    official_menu_rows, special_errors = _parse_gmcc_special_rows(
        [
            soup
            for category in categories
            for soup in category_soups.get(category, [])
        ]
    )
    errors.extend(special_errors)
    audit_meta.update(
        {
            "gmcc_list_requests": len(fetched),
            "gmcc_category_counts": category_counts,
            "gmcc_regular_catalogue_count": (
                category_counts.get("01", 0) + category_counts.get("02", 0)
            ),
            "gmcc_regular_resolved_count": len(resolved),
            "gmcc_regular_stale_mirror_count": len(regular_rows) - len(resolved),
            "gmcc_official_catalogue_count": len(official_menu_rows),
            "gmcc_special_catalogue_count": sum(
                row.get("raw_fields", {}).get("catalogue") == "gmcc_special"
                for row in official_menu_rows
            ),
        }
    )
    return resolved, official_menu_rows, audit_meta, errors, len(fetched)


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    forbidden_keys = {
        "description",
        "instructor",
        "contact",
        "phone",
        "email",
        "applicants",
        "attachments",
        "source_html",
    }
    if forbidden_keys.intersection(row):
        errors.append(f"{row.get('provider_course_id')}: forbidden output field")
    for key, value in row.items():
        if key in {"raw_url", "application_url", "period", "apply_period", "schedule_raw"}:
            continue
        if isinstance(value, str) and (_PHONE_RE.search(value) or _EMAIL_RE.search(value)):
            errors.append(f"{row.get('provider_course_id')}: contact data leaked via {key}")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def collect_gwangmyeong_education_courses(
    target: Any,
    timeout: int = 35,
    max_pages: int = 200,
    detail_limit: int = 1000,
    *,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GWANGMYEONG_MAX_WORKERS,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current education/experience snapshot or no rows."""

    meta: dict[str, Any] = {
        "pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "detail_attempts": 0,
        "detail_semantic_retry_attempts": 0,
        "detail_semantic_retry_recovered": 0,
        "discovered_links": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "municipality_code": GWANGMYEONG_MUNICIPALITY_CODE,
        "municipality_name": GWANGMYEONG_MUNICIPALITY_NAME,
        "canonical_candidate_id": GWANGMYEONG_CANONICAL_CANDIDATE_ID,
        "canonical_url": GWANGMYEONG_CANONICAL_URL,
        "ownership_alias_urls": list(GWANGMYEONG_OWNERSHIP_ALIAS_URLS),
        "configured_collection_error": "",
    }
    errors: list[str] = []
    if not is_gwangmyeong_education_target(target):
        meta["configured_collection_error"] = "non-canonical Gwangmyeong target"
        return [], GWANGMYEONG_PARSER, meta
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        request_timeout = max(1, int(timeout))
        workers = max(1, int(max_workers))
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "invalid collection limits"
        return [], GWANGMYEONG_PARSER, meta

    cutoff = _today(today)
    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    official_hosts = {
        GWANGMYEONG_HOST,
        "lll.gm.go.kr",
        *GWANGMYEONG_ALLOWED_EXTERNAL_HOSTS,
    }
    bootstrap_items = [
        (("learning", 1, "data"), gwangmyeong_learning_list_url(1)),
        (("office", 1, "data"), gwangmyeong_office_list_url(1)),
        (("media", 1, "data"), gwangmyeong_media_list_url(1)),
    ]
    fetched, fetch_errors = _fetch_many(
        bootstrap_items,
        fetcher=current_fetcher,
        session_factory=current_factory,
        timeout=request_timeout,
        max_workers=workers,
        expected_hosts=official_hosts,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(fetched)
    meta["list_requests"] += len(fetched)
    learning_soup = fetched.get(("learning", 1, "data"))
    office_soup = fetched.get(("office", 1, "data"))
    media_soup = fetched.get(("media", 1, "data"))
    if learning_soup is None or office_soup is None or media_soup is None:
        errors.append("missing bootstrap catalogue response")
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], GWANGMYEONG_PARSER, meta

    try:
        learning_total, learning_current, learning_last = _total(learning_soup)
        office_total, office_current, office_last = _total(office_soup)
        media_total, media_current, media_last = _total(media_soup)
    except ValueError as exc:
        errors.append(_clean(exc))
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], GWANGMYEONG_PARSER, meta
    if learning_current != 1 or office_current != 1 or media_current != 1:
        errors.append("bootstrap catalogue is not page one")
    if learning_last != max(1, math.ceil(learning_total / GWANGMYEONG_PAGE_SIZE)):
        errors.append("learning advertised last page mismatch")
    if office_last != 1 or office_total != GWANGMYEONG_OFFICE_DECLARED_TOTAL:
        errors.append("institution declared total/page contract changed")
    if media_last != 1 or media_total != 0:
        errors.append("video catalogue activated or total/page contract changed")

    offices, office_errors = _parse_offices(office_soup)
    errors.extend(office_errors)
    if not _office_contract_matches(offices):
        errors.append("official institution directory changed")
    meta.update(
        {
            "office_declared_total": office_total,
            "office_count": len(offices),
            "office_hidden_count": office_total - len(offices),
            "media_source_total": media_total,
            "declared_learning_pages": learning_last,
            "declared_media_pages": media_last,
        }
    )

    first_learning_rows, first_learning_errors = _parse_learning_page(
        learning_soup, page=1
    )
    first_media_rows, first_media_errors = _parse_media_page(media_soup, page=1)
    errors.extend(first_learning_errors)
    errors.extend(first_media_errors)
    required_page_requests = (
        3
        + (learning_last - 1)
        + 1  # learning sentinel
        + 1  # learning page-one recheck
        + 1  # office sentinel
        + 1  # office page-one recheck
        + 1  # media sentinel
        + 1  # media page-one recheck
    )
    meta["required_page_requests"] = required_page_requests
    if required_page_requests > allowed_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of "
            f"{required_page_requests} required source requests"
        )
    if errors:
        meta.update(
            {
                "source_total": learning_total,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return [], GWANGMYEONG_PARSER, meta

    remaining_items: list[tuple[Any, str]] = [
        (("learning", page, "data"), gwangmyeong_learning_list_url(page))
        for page in range(2, learning_last + 1)
    ]
    remaining_items.extend(
        [
            (
                ("learning", learning_last + 1, "sentinel"),
                gwangmyeong_learning_list_url(learning_last + 1),
            ),
            (("learning", 1, "recheck"), gwangmyeong_learning_list_url(1)),
            (("office", 2, "sentinel"), gwangmyeong_office_list_url(2)),
            (("office", 1, "recheck"), gwangmyeong_office_list_url(1)),
            (("media", 2, "sentinel"), gwangmyeong_media_list_url(2)),
            (("media", 1, "recheck"), gwangmyeong_media_list_url(1)),
        ]
    )
    remaining, remaining_errors = _fetch_many(
        remaining_items,
        fetcher=current_fetcher,
        session_factory=current_factory,
        timeout=request_timeout,
        max_workers=workers,
        expected_hosts=official_hosts,
    )
    fetched.update(remaining)
    errors.extend(remaining_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)

    office_sentinel = fetched.get(("office", 2, "sentinel"))
    office_recheck = fetched.get(("office", 1, "recheck"))
    if office_sentinel is None or office_recheck is None:
        errors.append("missing institution sentinel/recheck")
    else:
        try:
            sentinel_marker = _total(office_sentinel)
            recheck_marker = _total(office_recheck)
        except ValueError as exc:
            errors.append(f"office sentinel/recheck: {_clean(exc)}")
        else:
            sentinel_offices, sentinel_errors = _parse_offices(office_sentinel)
            recheck_offices, recheck_errors = _parse_offices(office_recheck)
            errors.extend(sentinel_errors)
            errors.extend(recheck_errors)
            if sentinel_marker != (office_total, 2, 1) or sentinel_offices:
                errors.append("institution immediate post-last page is not empty")
            if recheck_marker != (office_total, 1, 1) or recheck_offices != offices:
                errors.append("institution page-one recheck changed")

    learning_rows: list[dict[str, Any]] = []
    learning_page_counts: dict[int, int] = {}
    learning_signatures: dict[int, str] = {}
    for page in range(1, learning_last + 1):
        soup = learning_soup if page == 1 else fetched.get(("learning", page, "data"))
        if soup is None:
            errors.append(f"learning page {page}: missing response")
            continue
        try:
            marker = _total(soup)
        except ValueError as exc:
            errors.append(f"learning page {page}: {_clean(exc)}")
            continue
        if marker != (learning_total, page, learning_last):
            errors.append(f"learning page {page}: total/page marker changed")
        if page == 1:
            rows = first_learning_rows
        else:
            rows, row_errors = _parse_learning_page(soup, page=page)
            errors.extend(row_errors)
        expected_count = (
            GWANGMYEONG_PAGE_SIZE
            if page < learning_last
            else learning_total - GWANGMYEONG_PAGE_SIZE * (learning_last - 1)
        )
        if learning_total == 0:
            expected_count = 0
        if len(rows) != expected_count:
            errors.append(f"learning page {page}: row count mismatch")
        expected_sequences = list(
            range(
                learning_total - GWANGMYEONG_PAGE_SIZE * (page - 1),
                learning_total - GWANGMYEONG_PAGE_SIZE * (page - 1) - len(rows),
                -1,
            )
        )
        actual_sequences = [row["raw_fields"]["list_sequence"] for row in rows]
        if actual_sequences != expected_sequences:
            errors.append(f"learning page {page}: source sequence gap/reorder")
        learning_page_counts[page] = len(rows)
        learning_signatures[page] = _page_signature(rows)
        learning_rows.extend(rows)
    if len(learning_rows) != learning_total:
        errors.append("learning declared total does not match parsed rows")
    if len({row["provider_course_id"] for row in learning_rows}) != len(learning_rows):
        errors.append("duplicate course identity")
    nonempty_signatures = [
        learning_signatures[page]
        for page in range(1, learning_last + 1)
        if learning_page_counts.get(page)
    ]
    if len(nonempty_signatures) != len(set(nonempty_signatures)):
        errors.append("duplicate non-empty learning page signature")

    learning_sentinel = fetched.get(
        ("learning", learning_last + 1, "sentinel")
    )
    learning_recheck = fetched.get(("learning", 1, "recheck"))
    if learning_sentinel is None or learning_recheck is None:
        errors.append("missing learning sentinel/recheck")
    else:
        sentinel_rows, sentinel_errors = _parse_learning_page(
            learning_sentinel, page=learning_last + 1
        )
        recheck_rows, recheck_errors = _parse_learning_page(
            learning_recheck, page=1
        )
        errors.extend(sentinel_errors)
        errors.extend(recheck_errors)
        try:
            sentinel_marker = _total(learning_sentinel)
            recheck_marker = _total(learning_recheck)
        except ValueError as exc:
            errors.append(f"learning sentinel/recheck: {_clean(exc)}")
        else:
            if sentinel_marker != (
                learning_total,
                learning_last + 1,
                learning_last,
            ) or sentinel_rows:
                errors.append("learning immediate post-last page is not empty")
            if recheck_marker != (learning_total, 1, learning_last) or (
                _page_signature(recheck_rows) != learning_signatures.get(1)
            ):
                errors.append("learning page-one recheck changed")

    media_sentinel = fetched.get(("media", 2, "sentinel"))
    media_recheck = fetched.get(("media", 1, "recheck"))
    if media_sentinel is None or media_recheck is None:
        errors.append("missing media sentinel/recheck")
    else:
        sentinel_rows, sentinel_errors = _parse_media_page(media_sentinel, page=2)
        recheck_rows, recheck_errors = _parse_media_page(media_recheck, page=1)
        errors.extend(sentinel_errors)
        errors.extend(recheck_errors)
        try:
            sentinel_marker = _total(media_sentinel)
            recheck_marker = _total(media_recheck)
        except ValueError as exc:
            errors.append(f"media sentinel/recheck: {_clean(exc)}")
        else:
            if sentinel_marker != (0, 2, 1) or sentinel_rows:
                errors.append("media immediate post-last page is not empty")
            if recheck_marker != (0, 1, 1) or recheck_rows != first_media_rows:
                errors.append("media page-one recheck changed")

    source_offices = Counter(_clean(row.get("branch")) for row in learning_rows)
    unknown_source_offices = set(source_offices) - set(GWANGMYEONG_OFFICE_BY_NAME) - {
        GWANGMYEONG_HIDDEN_TEST_OFFICE_NAME
    }
    if unknown_source_offices:
        errors.append("learning archive contains an unknown institution")
    reversed_sequences = {
        int(row["raw_fields"]["list_sequence"])
        for row in learning_rows
        if row.get("raw_fields", {}).get("source_period_reversed")
    }
    if reversed_sequences != set(GWANGMYEONG_KNOWN_REVERSED_PERIOD_SEQUENCES):
        errors.append("known source period-anomaly set changed")
    partition_counts = Counter(
        _clean(row.get("raw_fields", {}).get("partition")) for row in learning_rows
    )
    current_rows = [
        row
        for row in learning_rows
        if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
    ]
    current_partition_counts = Counter(
        _clean(row.get("raw_fields", {}).get("partition")) for row in current_rows
    )
    eligible_rows = [
        row
        for row in current_rows
        if row.get("raw_fields", {}).get("partition")
        in {"education", "experience"}
    ]
    ilms_current_education_count = sum(
        row.get("raw_fields", {}).get("partition") == "education"
        for row in eligible_rows
    )
    ilms_current_experience_count = sum(
        row.get("raw_fields", {}).get("partition") == "experience"
        for row in eligible_rows
    )
    gmcc_audit_errors: list[str] = []
    gmcc_official_current_rows: list[dict[str, Any]] = []
    gmcc_office_rows = [
        row
        for row in eligible_rows
        if row.get("raw_fields", {}).get("partition") == "education"
        and _clean(row.get("branch")) == "광명문화원"
    ]
    gmcc_generic_rows = [
        row
        for row in gmcc_office_rows
        if (urlparse(_clean(row.get("raw_url"))).hostname or "").lower()
        == "www.gmcc.or.kr"
        and urlparse(_clean(row.get("raw_url"))).path == "/product_new/list.php"
    ]
    replaced_ids: set[str] = set()
    if not errors:
        gmcc_budget = allowed_pages - int(meta["required_page_requests"])
        if gmcc_budget < len(GWANGMYEONG_GMCC_CATEGORIES):
            meta["source_cap_reached"] = True
            errors.append(
                f"max_pages cap leaves {max(0, gmcc_budget)} requests for "
                "the culture-centre catalogue"
            )
        else:
            (
                gmcc_resolved,
                gmcc_official_menu_rows,
                gmcc_meta,
                current_gmcc_errors,
                gmcc_request_count,
            ) = _audit_gmcc_catalogue(
                gmcc_office_rows,
                fetcher=current_fetcher,
                session_factory=current_factory,
                timeout=request_timeout,
                max_workers=workers,
                max_requests=gmcc_budget,
            )
            meta.update(gmcc_meta)
            meta["required_page_requests"] += int(
                gmcc_meta.get("gmcc_required_list_requests", 0)
            )
            meta["pages"] += gmcc_request_count
            meta["list_requests"] += gmcc_request_count
            gmcc_audit_errors.extend(current_gmcc_errors)
            if gmcc_meta.get("gmcc_source_cap_reached"):
                meta["source_cap_reached"] = True
                errors.extend(current_gmcc_errors)
            gmcc_official_current_rows = [
                row
                for row in gmcc_official_menu_rows
                if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
            ]
            resolved_sequences = set(gmcc_resolved)
            gmcc_duplicate_rows = [
                row
                for row in gmcc_office_rows
                if int(row["raw_fields"]["list_sequence"])
                in resolved_sequences
            ]
            replaced_ids = {
                _clean(row.get("provider_course_id"))
                for row in [*gmcc_generic_rows, *gmcc_duplicate_rows]
            }
            # ILMS can expose the new culture-centre term as internal details
            # while retaining the previous term as generic external links.
            # Audit every culture-centre ILMS row against the official item
            # catalogue, remove both resolved duplicates and stale generic
            # mirrors, preserve unrelated internal programmes, then emit each
            # official item identity exactly once.
            eligible_rows = [
                row
                for row in eligible_rows
                if _clean(row.get("provider_course_id")) not in replaced_ids
            ]
            eligible_rows.extend(gmcc_official_current_rows)
            meta["gmcc_special_current_extra_count"] = len(
                [
                    row
                    for row in gmcc_official_current_rows
                    if row.get("raw_fields", {}).get("catalogue") == "gmcc_special"
                ]
            )
            meta["gmcc_official_current_count"] = len(gmcc_official_current_rows)
            meta["gmcc_ilms_office_count"] = len(gmcc_office_rows)
            meta["gmcc_ilms_generic_count"] = len(gmcc_generic_rows)
            meta["gmcc_ilms_duplicate_count"] = len(gmcc_duplicate_rows)
            meta["gmcc_ilms_stale_generic_count"] = sum(
                int(row["raw_fields"]["list_sequence"])
                not in resolved_sequences
                for row in gmcc_generic_rows
            )
            meta["gmcc_ilms_unique_internal_count"] = (
                len(gmcc_office_rows) - len(replaced_ids)
            )
            meta["gmcc_replaced_ilms_current_count"] = len(replaced_ids)
            meta["gmcc_current_extra_count"] = max(
                0, len(gmcc_official_current_rows) - len(gmcc_resolved)
            )
    list_complete = bool(
        not errors
        and len(learning_rows) == learning_total
        and _office_contract_matches(offices)
        and media_total == 0
    )
    required_details = len(eligible_rows)
    meta["detail_attempts"] = required_details
    if required_details > allowed_details:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} required details"
        )

    detail_errors: list[str] = list(gmcc_audit_errors)
    detail_pages = 0
    semantic_retry_rows: dict[str, dict[str, Any]] = {}
    initial_library_tombstones: set[str] = set()
    persistent_library_tombstones: set[str] = set()
    persistent_closed_external_http500_tombstones: set[str] = set()
    if list_complete and not errors:
        rows_by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in eligible_rows:
            rows_by_url[_clean(row.get("raw_url"))].append(row)
        detail_items = [(url, url) for url in rows_by_url]
        details, initial_detail_fetch_errors = _fetch_many(
            detail_items,
            fetcher=current_fetcher,
            session_factory=current_factory,
            timeout=request_timeout,
            max_workers=workers,
            expected_hosts=official_hosts,
        )
        meta["pages"] += len(details)
        accepted_initial_fetch_error_urls: set[str] = set()

        for url, rows_for_url in rows_by_url.items():
            soup = details.get(url)
            if soup is None:
                if rows_for_url and all(
                    _known_closed_external_http500_tombstone(
                        row, initial_detail_fetch_errors
                    )
                    for row in rows_for_url
                ):
                    persistent_closed_external_http500_tombstones.update(
                        _clean(row.get("provider_course_id"))
                        for row in rows_for_url
                    )
                    accepted_initial_fetch_error_urls.add(url)
                    continue
                detail_errors.extend(
                    f"sequence {row['raw_fields']['list_sequence']}: missing detail response"
                    for row in rows_for_url
                )
                continue
            for row in rows_for_url:
                item_errors = _validate_detail_row(row, soup)
                if item_errors:
                    retry_key = _clean(row.get("provider_course_id"))
                    semantic_retry_rows[retry_key] = row
                    if _gmlib_tombstone(row, soup):
                        initial_library_tombstones.add(retry_key)
                else:
                    detail_pages += 1
        detail_errors.extend(
            error
            for error in initial_detail_fetch_errors
            if not any(
                _clean(error).startswith(f"{url}: ")
                for url in accepted_initial_fetch_error_urls
            )
        )

        # A few linked public systems intermittently return a generic HTTP-200
        # shell while updating a course.  Re-read only semantic failures once;
        # persistent shells still fail the whole atomic snapshot.
        meta["detail_semantic_retry_attempts"] = len(semantic_retry_rows)
        if semantic_retry_rows:
            retry_items = [
                (sequence, _clean(row.get("raw_url")))
                for sequence, row in semantic_retry_rows.items()
            ]
            retry_details, retry_fetch_errors = _fetch_many(
                retry_items,
                fetcher=current_fetcher,
                session_factory=current_factory,
                timeout=request_timeout,
                max_workers=workers,
                expected_hosts=official_hosts,
            )
            detail_errors.extend(retry_fetch_errors)
            meta["pages"] += len(retry_details)
            recovered = 0
            for retry_key, row in semantic_retry_rows.items():
                soup = retry_details.get(retry_key)
                if soup is None:
                    detail_errors.append(
                        f"{retry_key}: missing semantic-retry response"
                    )
                    continue
                item_errors = _validate_detail_row(row, soup)
                if item_errors:
                    if (
                        retry_key in initial_library_tombstones
                        and _gmlib_tombstone(row, soup)
                    ):
                        persistent_library_tombstones.add(retry_key)
                    else:
                        detail_errors.extend(item_errors)
                else:
                    recovered += 1
                    detail_pages += 1
            meta["detail_semantic_retry_recovered"] = recovered
    errors.extend(detail_errors)
    meta["detail_pages"] = detail_pages
    persistent_tombstones = (
        persistent_library_tombstones
        | persistent_closed_external_http500_tombstones
    )
    verified_detail_count = detail_pages + len(persistent_tombstones)
    details_complete = bool(
        list_complete
        and verified_detail_count == required_details
        and not detail_errors
        and required_details <= allowed_details
    )

    if persistent_tombstones:
        eligible_rows = [
            row
            for row in eligible_rows
            if _clean(row.get("provider_course_id"))
            not in persistent_tombstones
        ]

    unfixed_experience_rows = [
        row
        for row in eligible_rows
        if row.get("raw_fields", {}).get("partition") == "experience"
        and not row.get("raw_fields", {}).get("experience_fixed_venue_verified")
    ]
    eligible_rows = [
        row
        for row in eligible_rows
        if row.get("raw_fields", {}).get("partition") != "experience"
        or row.get("raw_fields", {}).get("experience_fixed_venue_verified")
    ]
    for row in eligible_rows:
        _lock_programme_scope(row)

    result: list[dict[str, Any]] = []
    if list_complete and details_complete and not errors:
        for row in eligible_rows:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper(eligible_rows))
            if len(result) != len(eligible_rows):
                errors.append(
                    f"dedupe changed complete row count {len(eligible_rows)} to {len(result)}"
                )
                result = []
    snapshot_complete = bool(list_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status"))
        for row in learning_rows
    )
    type_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_learning_type"))
        for row in learning_rows
    )
    external_hosts = Counter(
        (urlparse(_clean(row.get("raw_url"))).hostname or "").lower()
        for row in learning_rows
        if row.get("raw_fields", {}).get("action_kind") == "external"
    )
    meta.update(
        {
            "source_total": learning_total,
            "source_rows": len(learning_rows),
            "learning_page_counts": learning_page_counts,
            "office_source_counts": dict(source_offices),
            "source_type_counts": dict(type_counts),
            "source_status_counts": dict(status_counts),
            "external_source_host_counts": dict(external_hosts),
            "source_partition_counts": dict(partition_counts),
            "source_reversed_period_sequences": sorted(reversed_sequences),
            "current_partition_counts": dict(current_partition_counts),
            "expired_count": len(learning_rows) - len(current_rows),
            "current_source_count": len(current_rows),
            "ilms_current_education_count": ilms_current_education_count,
            "ilms_current_experience_count": ilms_current_experience_count,
            "current_education_count": sum(
                row.get("raw_fields", {}).get("partition") == "education"
                for row in eligible_rows
            ),
            "current_experience_count": sum(
                row.get("raw_fields", {}).get("partition") == "experience"
                for row in eligible_rows
            ),
            "fixed_venue_experience_count": sum(
                row.get("raw_fields", {}).get("experience_fixed_venue_verified") is True
                for row in eligible_rows
            ),
            "excluded_unfixed_experience_count": len(unfixed_experience_rows),
            "excluded_unfixed_experience_reason_counts": dict(
                Counter(
                    _clean(row.get("raw_fields", {}).get("experience_exclusion_reason"))
                    for row in unfixed_experience_rows
                )
            ),
            "official_menu_current_source_count": (
                len(current_rows)
                - len(replaced_ids)
                + len(gmcc_official_current_rows)
            ),
            "returned_count": len(result),
            "excluded_current_count": (
                len(current_rows)
                - ilms_current_education_count
                - ilms_current_experience_count
                + len(replaced_ids)
                + len(persistent_tombstones)
                + len(unfixed_experience_rows)
            ),
            "partition_excluded_current_count": (
                len(current_rows)
                - ilms_current_education_count
                - ilms_current_experience_count
            ),
            "persistent_library_tombstone_count": len(
                persistent_library_tombstones
            ),
            "persistent_library_tombstone_sequences": sorted(
                int(row["raw_fields"]["list_sequence"])
                for row in current_rows
                if _clean(row.get("provider_course_id"))
                in persistent_library_tombstones
            ),
            "persistent_closed_external_http500_tombstone_count": len(
                persistent_closed_external_http500_tombstones
            ),
            "persistent_closed_external_http500_tombstone_sequences": sorted(
                int(row["raw_fields"]["list_sequence"])
                for row in current_rows
                if _clean(row.get("provider_course_id"))
                in persistent_closed_external_http500_tombstones
            ),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "validated_application_control_count": sum(
                bool(row.get("application_url")) for row in eligible_rows
            ),
            "validated_detail_period_mismatch_count": sum(
                row.get("raw_fields", {}).get("detail_period_match") is False
                for row in eligible_rows
            ),
            "detail_verified_count": verified_detail_count,
            "detail_failed_course_count": required_details - verified_detail_count,
            "unique_detail_errors": len(dict.fromkeys(detail_errors)),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "pagination_detected": learning_last > 1,
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "no_current_data": bool(snapshot_complete and not eligible_rows),
            "no_current_reason": (
                "all completely audited Gwangmyeong programmes have ended"
                if snapshot_complete and not eligible_rows
                else ""
            ),
            "detail_errors": len(detail_errors),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, GWANGMYEONG_PARSER, meta


collect = collect_gwangmyeong_education_courses


__all__ = [
    "GWANGMYEONG_ALLOWED_EXTERNAL_HOSTS",
    "GWANGMYEONG_CANONICAL_CANDIDATE_ID",
    "GWANGMYEONG_CANONICAL_URL",
    "GWANGMYEONG_EXPECTED_OFFICES",
    "GWANGMYEONG_GMCC_CATEGORIES",
    "GWANGMYEONG_GMCC_CATEGORY_URLS",
    "GWANGMYEONG_LEGACY_PROVIDER",
    "GWANGMYEONG_GMCC_LIST_URL",
    "GWANGMYEONG_HIDDEN_TEST_OFFICE_NAME",
    "GWANGMYEONG_KNOWN_CLOSED_EXTERNAL_HTTP500",
    "GWANGMYEONG_LANDING_URL",
    "GWANGMYEONG_MEDIA_URL",
    "GWANGMYEONG_MUNICIPALITY_CODE",
    "GWANGMYEONG_MUNICIPALITY_NAME",
    "GWANGMYEONG_OFFICE_DECLARED_TOTAL",
    "GWANGMYEONG_OFFICE_URL",
    "GWANGMYEONG_OWNERSHIP_ALIAS_URLS",
    "GWANGMYEONG_PAGE_SIZE",
    "GWANGMYEONG_PARSER",
    "GWANGMYEONG_PROVIDER",
    "GwangmyeongOffice",
    "collect",
    "collect_gwangmyeong_education_courses",
    "gwangmyeong_gmcc_list_url",
    "gwangmyeong_learning_detail_url",
    "gwangmyeong_learning_list_url",
    "gwangmyeong_media_list_url",
    "gwangmyeong_office_list_url",
    "is_gwangmyeong_education_target",
    "is_gwangmyeong_ownership_alias_target",
    "is_target",
]
