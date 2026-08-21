"""Fail-closed collector for Changwon City's integrated education catalogue.

The education navigation of ``일상플러스 통합예약`` is a fixed fan-out of
25 leaf catalogues.  No single leaf owns the complete municipal catalogue and
several previously discovered targets are either one-leaf/detail subsets or a
practice-only page.  This module therefore owns the audited fan-out as one
canonical provider.

The site clamps an out-of-range ``cpage`` to the final page instead of
returning an empty sentinel.  Every leaf is consequently read through its
declared final page and once more at ``last + 1``.  The repeated page must have
the final active-page marker and the exact same course/content signature.
Every archive row is then checked against its detail page before the
education-end-date filter is applied.  Any pagination, identity, detail, or
application-control mismatch fails the whole snapshot closed.

This module deliberately does not import ``Crawler_MunicipalYaml``.  The
shared router injects its managed HTML fetcher and session factory, avoiding a
cycle and preserving the project's request/TLS controls.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
import time
from threading import Lock, local
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


CHANGWON_PROVIDER = "MUNI_WWW_CHANGWON_GO_KR_74865AEB"
CHANGWON_CANDIDATE_ID = "MUNI_IR_702CBCF43B17"
CHANGWON_CANONICAL_URL = (
    "https://www.changwon.go.kr/booking/10030/10036.web"
)
CHANGWON_HOST = "www.changwon.go.kr"
CHANGWON_PAGE_SIZE = 15
CHANGWON_MAX_WORKERS = 8
CHANGWON_CRAWL_DELAY_SECONDS = 10.0
CHANGWON_PARSER = (
    "changwon_fixed_25_leaf_fanout+declared_pages+clamped_last_recheck+"
    "all_detail_contracts+education_end_filter"
)

CHANGWON_MUNICIPALITY_CODE = "4812000000"
CHANGWON_MUNICIPALITY_NAME = "경상남도 창원시"
CHANGWON_COVERED_MUNICIPALITIES: tuple[dict[str, str], ...] = (
    {
        "code": "4812000000",
        "sido": "경상남도",
        "sigungu": "창원시",
        "full_name": "경상남도 창원시",
    },
    {
        "code": "4812100000",
        "sido": "경상남도",
        "sigungu": "창원시 의창구",
        "full_name": "경상남도 창원시 의창구",
    },
    {
        "code": "4812300000",
        "sido": "경상남도",
        "sigungu": "창원시 성산구",
        "full_name": "경상남도 창원시 성산구",
    },
    {
        "code": "4812500000",
        "sido": "경상남도",
        "sigungu": "창원시 마산합포구",
        "full_name": "경상남도 창원시 마산합포구",
    },
    {
        "code": "4812700000",
        "sido": "경상남도",
        "sigungu": "창원시 마산회원구",
        "full_name": "경상남도 창원시 마산회원구",
    },
    {
        "code": "4812900000",
        "sido": "경상남도",
        "sigungu": "창원시 진해구",
        "full_name": "경상남도 창원시 진해구",
    },
)
CHANGWON_MUNICIPALITY_NAMES = {
    item["code"]: item["full_name"] for item in CHANGWON_COVERED_MUNICIPALITIES
}
CHANGWON_DISTRICT_CODES = {
    "의창구": "4812100000",
    "성산구": "4812300000",
    "마산합포구": "4812500000",
    "마산회원구": "4812700000",
    "진해구": "4812900000",
}


@dataclass(frozen=True)
class ChangwonLeaf:
    code: str
    group: str
    name: str
    path: str
    district: str = ""

    @property
    def url(self) -> str:
        return f"https://{CHANGWON_HOST}{self.path}"


CHANGWON_LEAVES: tuple[ChangwonLeaf, ...] = (
    ChangwonLeaf("it_uichang", "정보화·IT", "의창구", "/booking/10030/10036/10123.web", "의창구"),
    ChangwonLeaf("it_seongsan", "정보화·IT", "성산구", "/booking/10030/10036/10124.web", "성산구"),
    ChangwonLeaf("it_masanhappo", "정보화·IT", "마산합포구", "/booking/10030/10036/10125.web", "마산합포구"),
    ChangwonLeaf("it_masanhoewon", "정보화·IT", "마산회원구", "/booking/10030/10036/10126.web", "마산회원구"),
    ChangwonLeaf("it_jinhae", "정보화·IT", "진해구", "/booking/10030/10036/10127.web", "진해구"),
    ChangwonLeaf("resident_uichang", "주민자치센터", "의창구", "/booking/10030/10037/10044.web", "의창구"),
    ChangwonLeaf("resident_seongsan", "주민자치센터", "성산구", "/booking/10030/10037/10045.web", "성산구"),
    ChangwonLeaf("resident_masanhappo", "주민자치센터", "마산합포구", "/booking/10030/10037/10046.web", "마산합포구"),
    ChangwonLeaf("resident_masanhoewon", "주민자치센터", "마산회원구", "/booking/10030/10037/10047.web", "마산회원구"),
    ChangwonLeaf("resident_jinhae", "주민자치센터", "진해구", "/booking/10030/10037/10048.web", "진해구"),
    # The navigation href is /10038.web, which permanently resolves the
    # actual catalogue to this stable child route.
    ChangwonLeaf("citizen", "시민교양", "시민교양", "/booking/10030/10038/10153.web"),
    ChangwonLeaf("moonshin", "문화예술", "문신미술관", "/booking/10030/10039/10052.web"),
    ChangwonLeaf("masan_literature", "문화예술", "마산문학관", "/booking/10030/10039/10054.web"),
    ChangwonLeaf("masan_music", "문화예술", "마산음악관", "/booking/10030/10039/10055.web"),
    ChangwonLeaf("masan_museum", "문화예술", "마산박물관", "/booking/10030/10039/10056.web"),
    ChangwonLeaf("junam", "문화예술", "주남저수지", "/booking/10030/10039/10057.web"),
    ChangwonLeaf("ungcheon", "문화예술", "웅천도요지전시관", "/booking/10030/10039/10168.web"),
    ChangwonLeaf("arts_education", "문화예술", "창원문화예술교육센터", "/booking/10030/10039/10327.web"),
    ChangwonLeaf("humanities_city", "문화예술", "인문도시지원사업", "/booking/10030/10039/10388.web"),
    ChangwonLeaf("citizen_experience", "문화예술", "시민체험프로그램", "/booking/10030/10039/10450.web"),
    ChangwonLeaf("haengam", "문화예술", "행암문예마루", "/booking/10030/10039/10491.web"),
    ChangwonLeaf("women_masan", "여성회관", "여성회관 마산관", "/booking/10030/10040.web"),
    ChangwonLeaf("mom_center", "가족", "창원맘커뮤니티센터", "/booking/10030/10347.web"),
    ChangwonLeaf("pet_village", "반려동물", "펫빌리지 반려동물 문화센터", "/booking/10030/10408.web"),
    ChangwonLeaf("lifelong", "평생학습", "창원시 평생학습관", "/booking/10030/10489.web"),
)
CHANGWON_LEAF_BY_PATH = {leaf.path: leaf for leaf in CHANGWON_LEAVES}


@dataclass(frozen=True)
class ChangwonAlias:
    provider: str
    url: str
    reason: str
    ownership: str = "subset"


CHANGWON_NON_EXECUTING_ALIASES: tuple[ChangwonAlias, ...] = (
    ChangwonAlias(
        "MUNI_WWW_CHANGWON_GO_KR_2B9F3D84",
        "https://www.changwon.go.kr/booking/10030/10036/10126.web?stypeTerm=rcept&cpage=1&stype=title&gubunCd=FAC001&regionCd=5670184&lectureId=LT003261",
        "no-amode query noise on the canonical 마산회원구 IT leaf",
    ),
    ChangwonAlias(
        "MUNI_WWW_CHANGWON_GO_KR_91257ACE",
        "https://www.changwon.go.kr/booking/10030/10036/10127.web",
        "canonical 진해구 IT leaf subset",
    ),
    ChangwonAlias(
        "MUNI_WWW_CHANGWON_GO_KR_CC9D014E",
        "https://www.changwon.go.kr/booking/10030/10036/10126.web?stypeTerm=rcept&cpage=1&stype=title&gubunCd=FAC001&regionCd=5670184&lectureId=LT002047",
        "second no-amode query variant of the canonical 마산회원구 IT leaf",
    ),
    ChangwonAlias(
        "MUNI_WWW_CHANGWON_GO_KR_631A6F76",
        "https://www.changwon.go.kr/booking/10030/10039/10327.web?amode=view&lectureId=LT002500&cpage=1&fcd=F017",
        "one archived detail from the 창원문화예술교육센터 leaf",
    ),
    ChangwonAlias(
        "MUNI_WWW_CHANGWON_GO_KR_F0729895",
        "https://www.changwon.go.kr/booking/10030/10036/10124.web?amode=view&lectureId=LT003483",
        "one archived detail from the 성산구 IT leaf",
    ),
    ChangwonAlias(
        "MUNI_WWW_CHANGWON_GO_KR_C06A834D",
        "https://www.changwon.go.kr/booking/10030/10036/10409.web",
        "practice-only 수강 신청 연습 page absent from the official 25-leaf education menu",
        ownership="excluded_training",
    ),
)
CHANGWON_OWNERSHIP_ALIAS_URLS = tuple(
    item.url for item in CHANGWON_NON_EXECUTING_ALIASES if item.ownership == "subset"
)
CHANGWON_EXCLUDED_TRAINING_URLS = tuple(
    item.url
    for item in CHANGWON_NON_EXECUTING_ALIASES
    if item.ownership == "excluded_training"
)


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class ChangwonHostPacer:
    """Serialize requests to the Changwon host across sibling collectors."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_request_at = 0.0

    def wait(
        self,
        interval_seconds: float,
        *,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        interval = max(0.0, float(interval_seconds))
        if interval == 0:
            return
        with self._lock:
            now = float(monotonic_fn())
            delay = max(0.0, self._next_request_at - now)
            if delay:
                sleep_fn(delay)
                now = float(monotonic_fn())
            self._next_request_at = max(now, self._next_request_at) + interval


CHANGWON_HOST_PACER = ChangwonHostPacer()


def changwon_paced_fetcher(
    fetcher: Fetcher,
    *,
    delay_seconds: float,
    pacer: ChangwonHostPacer = CHANGWON_HOST_PACER,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Fetcher:
    """Wrap a managed fetcher with the official host-wide crawl delay."""

    def paced(current_session: Any, url: str, timeout: int) -> Any:
        pacer.wait(
            delay_seconds,
            monotonic_fn=monotonic_fn,
            sleep_fn=sleep_fn,
        )
        return fetcher(current_session, url, timeout)

    return paced

_SPACE_RE = re.compile(r"\s+")
_LECTURE_ID_RE = re.compile(r"LT\d{6,12}\Z")
_DATE_RANGE_RE = re.compile(
    r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\s*~\s*"
    r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\Z"
)
_DATETIME_RANGE_RE = re.compile(
    r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\s+"
    r"([01]?\d|2[0-3]):([0-5]\d)\s*~\s*"
    r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\s+"
    r"([01]?\d|2[0-3]):([0-5]\d)\Z"
)
_COUNT_RE = re.compile(r"([\d,]+)\s*명")
_LIST_REQUIRED_KEYS = frozenset(
    {
        "교육과정",
        "접수일시",
        "교육기간",
        "요일시간",
        "신청대상자",
        "정원/대기정원",
        "신청현황",
        "수강료",
        "교육장소",
        "강사명",
    }
)
_DETAIL_REQUIRED_KEYS = frozenset(
    {
        "시설구분",
        "교육과정",
        "교육대상",
        "접수일시",
        "교육기간",
        "요일시간",
        "승인방식",
        "정원/대기정원",
        "신청현황",
        "수강료",
        "강사명",
        "재료비",
        "교육장소",
    }
)
_SOURCE_STATUSES = frozenset({"접수중", "인원마감", "접수마감", "접수대기"})
_NO_DATA_TEXT = "등록된 자료가 없습니다."
_DETAIL_QUERY_KEYS = frozenset(
    {"amode", "lectureId", "gubunCd", "regionCd", "fcd"}
)
_APPLICATION_QUERY_KEYS = _DETAIL_QUERY_KEYS


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


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


def _exact_https_url(value: Any, expected_path: str) -> bool:
    parsed = urlparse(_clean(value))
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == CHANGWON_HOST
        and parsed.port is None
        and parsed.path == expected_path
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_changwon_education_target(target: Any) -> bool:
    return _provider(target) == CHANGWON_PROVIDER and _exact_https_url(
        _target_url(target), "/booking/10030/10036.web"
    )


is_target = is_changwon_education_target


def changwon_list_url(leaf: ChangwonLeaf, page: Any = 1) -> str:
    raw_page = _clean(page)
    if leaf not in CHANGWON_LEAVES or not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    page_number = int(raw_page)
    return leaf.url + (f"?{urlencode({'cpage': page_number})}" if page_number > 1 else "")


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    return current_session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 200))
    if 300 <= status < 400:
        raise ValueError("HTTP redirects are not accepted")
    raise_for_status = getattr(value, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise ValueError("empty HTTP response")
    return BeautifulSoup(content, "lxml")


def _fetch(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    if not url:
        raise ValueError("empty fetch URL")
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _single_query(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _safe_leaf_href(value: Any, leaf: ChangwonLeaf, base_url: str) -> tuple[str, str]:
    parsed = urlparse(urljoin(base_url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query(query, "lectureId")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != CHANGWON_HOST
        or parsed.port is not None
        or parsed.path != leaf.path
        or not set(query).issubset(_DETAIL_QUERY_KEYS | {"cpage"})
        or _single_query(query, "amode") != "view"
        or not _LECTURE_ID_RE.fullmatch(identity)
        or any(len(values) != 1 for values in query.values())
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return "", ""
    pairs: list[tuple[str, str]] = [("amode", "view"), ("lectureId", identity)]
    for key in ("gubunCd", "regionCd", "fcd"):
        current = _single_query(query, key)
        if current:
            pairs.append((key, current))
    return identity, f"https://{CHANGWON_HOST}{leaf.path}?{urlencode(pairs)}"


def _page_contract(soup: BeautifulSoup, leaf: ChangwonLeaf) -> tuple[int, int]:
    roots = soup.select(".pagination")
    if len(roots) != 1:
        return 0, 0
    nodes = roots[0].select(".pages > .m")
    if not nodes:
        return 0, 0
    pages: list[int] = []
    active: list[int] = []
    for node in nodes:
        raw = _clean(node.get_text(" ", strip=True))
        if not raw.isdigit() or int(raw) < 1:
            return 0, 0
        page = int(raw)
        pages.append(page)
        if "on" in (node.get("class") or []):
            active.append(page)
            continue
        anchors = node.select("a[href]")
        if len(anchors) != 1:
            return 0, 0
        parsed = urlparse(urljoin(leaf.url, _clean(anchors[0].get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").rstrip(".").lower() != CHANGWON_HOST
            or parsed.path != leaf.path
            or _single_query(query, "cpage") != str(page)
            or parsed.params
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            return 0, 0
    last = max(pages)
    if pages != list(range(1, last + 1)) or len(active) != 1:
        return 0, 0
    return last, active[0]


def _pairs(container: Any, list_class: str) -> Optional[dict[str, str]]:
    result: dict[str, str] = {}
    nodes = container.select(f"{list_class} .di")
    if not nodes:
        return None
    for node in nodes:
        left = node.select_one(".dt")
        right = node.select_one(".dd")
        if left is None or right is None:
            return None
        key = _clean(left.get_text(" ", strip=True)).rstrip(":").strip()
        value = _clean(right.get_text(" ", strip=True))
        if not key or key in result:
            return None
        result[key] = value
    return result


def _status_contract(node: Any) -> str:
    values = node.select(".w1c1 .g1")
    if len(values) != 1:
        return ""
    value = _clean(values[0].get_text(" ", strip=True))
    classes = set(values[0].get("class") or [])
    if value not in _SOURCE_STATUSES:
        return ""
    expected = "s1" if value == "접수중" else "s3" if value == "접수대기" else "s2"
    return value if expected in classes else ""


def _branch_code(municipality_code: str) -> str:
    digest = hashlib.sha1(
        f"{CHANGWON_PROVIDER}|{municipality_code}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"CHANGWON_BRANCH_{digest}"


def _base_row(
    target: Any,
    leaf: ChangwonLeaf,
    identity: str,
    raw_url: str,
    title: str,
    status: str,
    page: int,
    pairs: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "provider": _provider(target),
        "provider_course_id": f"{_provider(target)}:changwon:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "program_type": f"{leaf.group} / {leaf.name}",
        "category": "교육·강좌",
        "branch": CHANGWON_MUNICIPALITY_NAME,
        "branch_code": _branch_code(CHANGWON_MUNICIPALITY_CODE),
        "branch_url": CHANGWON_CANONICAL_URL,
        "preserve_branch": True,
        "raw_url": raw_url,
        "application_url": "",
        "reservation_available": False,
        "status": "CLOSED",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "municipality_code": CHANGWON_MUNICIPALITY_CODE,
        "municipality_full_name": CHANGWON_MUNICIPALITY_NAME,
        "collection_type": "complete_fixed_fanout+clamped_page_recheck+detail_html",
        "description": _clean(" ".join(pairs.values())),
        "target": pairs.get("신청대상자", ""),
        "schedule_raw": pairs.get("요일시간", ""),
        "venue_name": pairs.get("교육장소", ""),
        "fee": pairs.get("수강료", ""),
        "capacity": pairs.get("정원/대기정원", ""),
        "raw_fields": {
            "parser": CHANGWON_PARSER,
            "leaf_code": leaf.code,
            "leaf_group": leaf.group,
            "leaf_name": leaf.name,
            "leaf_path": leaf.path,
            "leaf_page": page,
            "detail_id": identity,
            "list_title": title,
            "list_status": status,
            "list_pairs": dict(pairs),
        },
    }


def _parse_list_page(
    target: Any,
    leaf: ChangwonLeaf,
    soup: BeautifulSoup,
    *,
    page: int,
    source_url: str,
) -> tuple[list[dict[str, Any]], bool, int]:
    roots = soup.select(".cp31edu1list1 > ul")
    if len(roots) != 1:
        return [], False, 1
    cards = roots[0].find_all("li", recursive=False)
    if len(cards) == 1 and _clean(cards[0].get_text(" ", strip=True)) == _NO_DATA_TEXT:
        return [], True, 0
    rows: list[dict[str, Any]] = []
    malformed = 0
    for card in cards:
        anchors = card.select("a.tg1[href]")
        figure_anchors = card.select("a.figs[href]")
        if len(anchors) != 1 or len(figure_anchors) != 1:
            malformed += 1
            continue
        identity, raw_url = _safe_leaf_href(anchors[0].get("href"), leaf, source_url)
        figure_identity, figure_url = _safe_leaf_href(
            figure_anchors[0].get("href"), leaf, source_url
        )
        title_node = anchors[0].select_one(".h1")
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
        status = _status_contract(card)
        pairs = _pairs(card, ".cp31dlist1")
        fee_badges = card.select("a.tg1 .cate")
        methods = card.select(".g2s .g2")
        if (
            not identity
            or identity != figure_identity
            or raw_url != figure_url
            or not title
            or not status
            or pairs is None
            or not _LIST_REQUIRED_KEYS.issubset(pairs)
            or len(fee_badges) != 1
        ):
            malformed += 1
            continue
        fee_badge = _clean(fee_badges[0].get_text(" ", strip=True))
        if fee_badge not in {"무료", "유료"}:
            malformed += 1
            continue
        if (fee_badge == "무료") != (_normalized(pairs["수강료"]) == "무료"):
            malformed += 1
            continue
        row = _base_row(target, leaf, identity, raw_url, title, status, page, pairs)
        row["raw_fields"] = {
            **row["raw_fields"],
            "fee_badge": fee_badge,
            "application_method": _clean(
                " / ".join(node.get_text(" ", strip=True) for node in methods)
            ),
            "application_methods": [
                _clean(node.get_text(" ", strip=True)) for node in methods
            ],
        }
        rows.append(row)
    return rows, False, malformed


def _page_signature(rows: Iterable[Mapping[str, Any]], no_data: bool) -> tuple[Any, ...]:
    return (
        bool(no_data),
        tuple(
            (
                _clean(row.get("raw_fields", {}).get("detail_id")),
                _normalized(row.get("title")),
                _normalized(row.get("raw_fields", {}).get("list_status")),
                tuple(
                    sorted(
                        (
                            _normalized(key),
                            _normalized(value),
                        )
                        for key, value in row.get("raw_fields", {})
                        .get("list_pairs", {})
                        .items()
                    )
                ),
            )
            for row in rows
        ),
    )


def _date_range(value: Any) -> tuple[Optional[date], Optional[date], str]:
    match = _DATE_RANGE_RE.fullmatch(_clean(value))
    if match is None:
        return None, None, ""
    try:
        start = date(*(int(item) for item in match.groups()[:3]))
        end = date(*(int(item) for item in match.groups()[3:]))
    except ValueError:
        return None, None, ""
    if end < start:
        return None, None, ""
    return start, end, f"{start.isoformat()} ~ {end.isoformat()}"


def _datetime_range(value: Any) -> tuple[Optional[datetime], Optional[datetime], str]:
    match = _DATETIME_RANGE_RE.fullmatch(_clean(value))
    if match is None:
        return None, None, ""
    raw = [int(item) for item in match.groups()]
    try:
        start = datetime(raw[0], raw[1], raw[2], raw[3], raw[4])
        end = datetime(raw[5], raw[6], raw[7], raw[8], raw[9])
    except ValueError:
        return None, None, ""
    if end < start:
        return None, None, ""
    return (
        start,
        end,
        f"{start.strftime('%Y-%m-%d %H:%M')} ~ {end.strftime('%Y-%m-%d %H:%M')}",
    )


def _capacity_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    values = [int(item.replace(",", "")) for item in _COUNT_RE.findall(_clean(value))]
    if len(values) != 2 or any(item < 0 for item in values):
        return None, None
    return values[0], values[1]


def _application_counts(value: Any) -> tuple[Optional[int], Optional[int]]:
    values = [int(item.replace(",", "")) for item in _COUNT_RE.findall(_clean(value))]
    if not values or any(item < 0 for item in values):
        return None, None
    return values[0], sum(values[1:])


def _detail_pairs(soup: BeautifulSoup) -> tuple[Optional[Any], Optional[dict[str, str]]]:
    roots = soup.select(".cp31edu1view1")
    if len(roots) != 1:
        return None, None
    pairs = _pairs(roots[0], ".cp31dlist2")
    return roots[0], pairs


def _facility_contract(soup: BeautifulSoup) -> tuple[str, str, str]:
    panes = soup.select("#tabs1pane4")
    if not panes:
        return "", "", ""
    if len(panes) != 1:
        return "", "", "__INVALID__"
    boxes = panes[0].select(".detail1box")
    if not boxes:
        return "", "", ""
    if len(boxes) != 1:
        return "", "", "__INVALID__"
    name_node = boxes[0].select_one("h4.h1")
    name = _clean(name_node.get_text(" ", strip=True)) if name_node else ""
    address = ""
    phone = ""
    for node in boxes[0].select("li"):
        text = _clean(node.get_text(" ", strip=True))
        if text.startswith("주소 :"):
            address = _clean(text.partition(":")[2])
        elif text.startswith("연락처 :"):
            phone = _clean(text.partition(":")[2])
    return name, address, phone


def _description(soup: BeautifulSoup) -> str:
    panes = soup.select("#tabs1pane1")
    if len(panes) != 1:
        return ""
    clone = BeautifulSoup(str(panes[0]), "lxml")
    for hidden in clone.select(".blind"):
        hidden.decompose()
    return _clean(clone.get_text(" ", strip=True))


def _safe_application_url(value: Any, leaf: ChangwonLeaf, identity: str, base_url: str) -> str:
    parsed = urlparse(urljoin(base_url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != CHANGWON_HOST
        or parsed.port is not None
        or parsed.path != leaf.path
        or set(query) - _APPLICATION_QUERY_KEYS
        or any(len(values) != 1 for values in query.values())
        or _single_query(query, "amode") != "agree"
        or _single_query(query, "lectureId") != identity
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return ""
    pairs: list[tuple[str, str]] = [("amode", "agree")]
    for key in ("gubunCd", "regionCd", "fcd"):
        current = _single_query(query, key)
        if current:
            pairs.append((key, current))
    pairs.append(("lectureId", identity))
    return f"https://{CHANGWON_HOST}{leaf.path}?{urlencode(pairs)}"


def _application_contract(
    root: Any,
    leaf: ChangwonLeaf,
    identity: str,
    base_url: str,
) -> tuple[str, str, int]:
    safe: dict[str, str] = {}
    unsafe = 0
    for node in root.select(".infomenu1 a, .infomenu1 button, .infomenu1 input"):
        text = _clean(
            " ".join(
                value
                for value in (
                    node.get_text(" ", strip=True),
                    _clean(node.get("value")),
                    _clean(node.get("title")),
                )
                if value
            )
        )
        labels = [label for label in ("예약하기", "대기접수") if label in text]
        if len(labels) != 1:
            continue
        href = _clean(node.get("href"))
        if not href:
            continue
        resolved = _safe_application_url(href, leaf, identity, base_url)
        if resolved:
            safe[resolved] = labels[0]
        else:
            unsafe += 1
    if len(safe) == 1 and unsafe == 0:
        url, label = next(iter(safe.items()))
        return url, label, 0
    return "", "", unsafe + (len(safe) if len(safe) > 1 else 0)


def _tokens(value: Any) -> list[str]:
    text = _clean(value)
    return [token for token in CHANGWON_DISTRICT_CODES if token in text]


def _municipality_assignment(
    leaf: ChangwonLeaf,
    *,
    facility_address: str,
    facility_name: str,
    facility_type: str,
    venue: str,
    title: str,
) -> tuple[str, str, str, str]:
    fields = (
        ("facility_address", facility_address),
        ("facility_name", facility_name),
        ("facility_type", facility_type),
        ("venue", venue),
        ("title", title),
    )
    if leaf.district:
        conflicts = {
            token
            for _field, value in fields[:3]
            for token in _tokens(value)
            if token != leaf.district
        }
        if conflicts:
            return "", "", "conflict", ",".join(sorted(conflicts))
        code = CHANGWON_DISTRICT_CODES[leaf.district]
        return code, CHANGWON_MUNICIPALITY_NAMES[code], "official_district_leaf", leaf.path

    for field, value in fields:
        matches = _tokens(value)
        if len(matches) == 1:
            code = CHANGWON_DISTRICT_CODES[matches[0]]
            return code, CHANGWON_MUNICIPALITY_NAMES[code], field, value
        if len(matches) > 1:
            return (
                CHANGWON_MUNICIPALITY_CODE,
                CHANGWON_MUNICIPALITY_NAME,
                f"{field}_citywide",
                value,
            )
    return (
        CHANGWON_MUNICIPALITY_CODE,
        CHANGWON_MUNICIPALITY_NAME,
        "citywide_no_district_evidence",
        leaf.path,
    )


def _same_field(left: Any, right: Any) -> bool:
    return _normalized(left) == _normalized(right)


def _enrich_detail(
    row: dict[str, Any],
    leaf: ChangwonLeaf,
    soup: BeautifulSoup,
    reference_day: date,
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("detail_id"))
    root, pairs = _detail_pairs(soup)
    if root is None or pairs is None:
        return [f"detail {identity}: detail view contract mismatch"]
    missing = sorted(_DETAIL_REQUIRED_KEYS - set(pairs))
    if missing:
        return [f"detail {identity}: missing detail keys {','.join(missing)}"]
    title_nodes = root.select(".w1c2 h3.h1")
    detail_title = _clean(title_nodes[0].get_text(" ", strip=True)) if len(title_nodes) == 1 else ""
    detail_status = _status_contract(root)
    list_pairs = row.get("raw_fields", {}).get("list_pairs", {})
    if not detail_title or not _normalized(row.get("title")).startswith(_normalized(detail_title)):
        return [f"detail {identity}: detail/list title mismatch"]
    if detail_status != _clean(row.get("raw_fields", {}).get("list_status")):
        return [f"detail {identity}: detail/list status mismatch"]
    comparisons = (
        ("교육과정", "교육과정"),
        ("신청대상자", "교육대상"),
        ("접수일시", "접수일시"),
        ("교육기간", "교육기간"),
        ("요일시간", "요일시간"),
        ("정원/대기정원", "정원/대기정원"),
        ("수강료", "수강료"),
        ("교육장소", "교육장소"),
    )
    for list_key, detail_key in comparisons:
        if not _same_field(list_pairs.get(list_key), pairs.get(detail_key)):
            return [f"detail {identity}: detail/list {detail_key} mismatch"]

    # Instructor text is descriptive and can be edited after the catalogue card
    # was rendered.  The official catalogue also keeps the ``강사명`` field in
    # both views for courses where no instructor is published, but renders its
    # value blank.  Keep the field-key contract strict, accept only a matching
    # blank/blank omission, and retain the detail value as authoritative for
    # non-empty descriptive drift.  Identity, dates, schedule, venue and every
    # other core comparison above remain fail-closed.
    list_instructor = _clean(list_pairs.get("강사명"))
    detail_instructor = _clean(pairs.get("강사명"))
    instructor_drift: dict[str, str] = {}
    instructor_contract = "published"
    if not list_instructor and not detail_instructor:
        instructor_contract = "official_blank_in_list_and_detail"
    elif not list_instructor or not detail_instructor:
        return [f"detail {identity}: one-sided empty instructor value"]
    elif not _same_field(list_instructor, detail_instructor):
        instructor_drift = {
            "field": "강사명",
            "list_value": list_instructor,
            "detail_value": detail_instructor,
            "authority": "detail",
            "evidence": "identity and all core list/detail fields matched",
        }

    education_start, education_end, period = _date_range(pairs["교육기간"])
    apply_start, apply_end, apply_period = _datetime_range(pairs["접수일시"])
    if None in {education_start, education_end, apply_start, apply_end}:
        return [f"detail {identity}: invalid education/application period"]
    assert education_start is not None and education_end is not None
    assert apply_start is not None and apply_end is not None
    capacity_total, wait_capacity = _capacity_pair(pairs["정원/대기정원"])
    detail_current, detail_wait_current = _application_counts(pairs["신청현황"])
    list_current, list_wait_current = _application_counts(
        list_pairs.get("신청현황")
    )
    if None in {
        capacity_total,
        wait_capacity,
        detail_current,
        detail_wait_current,
        list_current,
        list_wait_current,
    }:
        return [f"detail {identity}: invalid capacity/application counts"]

    assert capacity_total is not None and wait_capacity is not None
    assert detail_current is not None and detail_wait_current is not None
    assert list_current is not None and list_wait_current is not None
    allowed_capacity = capacity_total + wait_capacity
    detail_application_total = detail_current + detail_wait_current
    list_application_total = list_current + list_wait_current
    official_over_capacity: dict[str, Any] = {}
    if (
        detail_application_total > allowed_capacity
        or list_application_total > allowed_capacity
    ):
        if (
            education_end < reference_day
            and detail_status in {"접수마감", "인원마감"}
            and _same_field(
                list_pairs.get("정원/대기정원"), pairs.get("정원/대기정원")
            )
            and list_application_total == detail_application_total
        ):
            official_over_capacity = {
                "detail_id": identity,
                "source_status": detail_status,
                "education_end": education_end.isoformat(),
                "capacity_total_including_wait": allowed_capacity,
                "published_application_total": detail_application_total,
                "excess": detail_application_total - allowed_capacity,
                "evidence": "identical official list/detail closed archive values",
            }
        else:
            return [f"detail {identity}: application count exceeds capacity plus waitlist"]

    application_url, application_label, unsafe_controls = _application_contract(
        root, leaf, identity, _clean(row.get("raw_url"))
    )
    if unsafe_controls:
        return [f"detail {identity}: ambiguous or unsafe application control"]
    source_status = detail_status
    application_active = (
        source_status == "접수중"
        and apply_start.date() <= reference_day <= apply_end.date()
    )
    methods = [
        _clean(value)
        for value in row.get("raw_fields", {}).get("application_methods", [])
        if _clean(value)
    ]
    if source_status == "접수중" and not application_active:
        return [f"detail {identity}: open status lies outside its application period"]
    if source_status == "접수중" and not application_url and "인터넷" in methods:
        return [f"detail {identity}: internet-open status lacks an active application control"]
    if source_status != "접수중" and application_url:
        return [f"detail {identity}: closed/scheduled status exposes reservation URL"]
    if source_status == "접수중":
        normalized_status = "OPEN"
    elif source_status == "접수대기":
        normalized_status = "SCHEDULED"
    else:
        normalized_status = "CLOSED"
    if application_url:
        application_type = "ONLINE_RESERVATION"
    elif normalized_status == "OPEN" and "전화" in methods:
        application_type = "PHONE_APPLY"
    elif normalized_status == "OPEN" and "방문" in methods:
        application_type = "VISIT_APPLY"
    else:
        application_type = "INFORMATION_ONLY"

    facility_name, facility_address, phone = _facility_contract(soup)
    if phone == "__INVALID__":
        return [f"detail {identity}: facility pane contract mismatch"]
    municipality_code, municipality_name, evidence_field, evidence_value = (
        _municipality_assignment(
            leaf,
            facility_address=facility_address,
            facility_name=facility_name,
            facility_type=pairs["시설구분"],
            venue=pairs["교육장소"],
            title=detail_title,
        )
    )
    if not municipality_code:
        return [f"detail {identity}: district evidence conflicts with official leaf"]

    row.update(
        {
            "title": detail_title,
            "period": period,
            "start_date": education_start.isoformat(),
            "end_date": education_end.isoformat(),
            "apply_period": apply_period,
            "apply_start": apply_start.strftime("%Y-%m-%d %H:%M"),
            "apply_end": apply_end.strftime("%Y-%m-%d %H:%M"),
            "target": pairs["교육대상"],
            "schedule_raw": pairs["요일시간"],
            "venue_name": pairs["교육장소"],
            "venue_address": facility_address,
            "fee": pairs["수강료"],
            "material_fee": pairs["재료비"],
            "capacity": pairs["정원/대기정원"],
            "capacity_total": capacity_total,
            "wait_capacity_total": wait_capacity,
            "capacity_current": detail_current,
            "wait_capacity_current": detail_wait_current,
            "approval_method": pairs["승인방식"],
            "instructor": pairs["강사명"],
            "phone": phone,
            "status": normalized_status,
            "application_url": application_url if normalized_status == "OPEN" else "",
            "reservation_available": bool(
                application_url and normalized_status == "OPEN"
            ),
            "application_type": application_type,
            "branch": municipality_name,
            "branch_code": _branch_code(municipality_code),
            "municipality_code": municipality_code,
            "municipality_full_name": municipality_name,
        }
    )
    description = _description(soup)
    if description:
        row["description"] = description
    row["raw_fields"] = {
        **row["raw_fields"],
        "list_title": _clean(row.get("raw_fields", {}).get("list_title")),
        "detail_pairs": pairs,
        "facility_name": facility_name,
        "facility_address": facility_address,
        "list_application_current": list_current,
        "list_wait_current": list_wait_current,
        "detail_application_current": detail_current,
        "detail_wait_current": detail_wait_current,
        "application_count_drift": {
            "main": detail_current - list_current,
            "wait": detail_wait_current - list_wait_current,
        },
        "instructor_contract": instructor_contract,
        "detail_authoritative_drift": instructor_drift,
        "official_over_capacity": official_over_capacity,
        "municipality_evidence": {
            "field": evidence_field,
            "value": evidence_value,
            "code": municipality_code,
            "full_name": municipality_name,
        },
        "application_control_present": bool(application_url),
        "application_control_label": application_label,
    }
    return []


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("period")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("venue_name")),
    )


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "leaf_count": len(CHANGWON_LEAVES),
        "source_count": len(CHANGWON_LEAVES),
        "required_list_requests": 0,
        "declared_pages_by_leaf": {},
        "clamp_pages": {},
        "page_counts": {},
        "source_counts": {},
        "source_group_counts": {},
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "expired_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "duplicate_count": 0,
        "duplicate_identity_count": 0,
        "duplicate_url_count": 0,
        "semantic_duplicate_count": 0,
        "source_status_counts": {},
        "municipality_counts": {},
        "current_municipality_counts": {},
        "municipality_evidence_counts": {},
        "official_over_capacity_count": 0,
        "official_over_capacity_ids": [],
        "detail_authoritative_drift_count": 0,
        "detail_authoritative_drift_ids": [],
        "application_open_count": 0,
        "reservation_discovery_links": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "recursion_depth": 0,
        "configured_collection_error": "",
    }


def collect_changwon_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 200,
    detail_limit: int = 2000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = CHANGWON_MAX_WORKERS,
    crawl_delay_seconds: float = 0.0,
    pacer: ChangwonHostPacer = CHANGWON_HOST_PACER,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Changwon education snapshot."""

    meta = _base_meta()
    if not is_changwon_education_target(target):
        meta["configured_collection_error"] = (
            "target is not the canonical Changwon education landing"
        )
        return [], CHANGWON_PARSER, meta
    if fetcher is None or session_factory is None:
        meta["configured_collection_error"] = (
            "managed fetcher and session_factory injection are required"
        )
        return [], CHANGWON_PARSER, meta
    if (
        max_pages < len(CHANGWON_LEAVES) * 2
        or detail_limit < 0
        or max_workers < 1
        or crawl_delay_seconds < 0
    ):
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "max_pages/detail_limit/max_workers are invalid for fixed fan-out"
        )
        return [], CHANGWON_PARSER, meta

    fetcher = changwon_paced_fetcher(
        fetcher,
        delay_seconds=crawl_delay_seconds,
        pacer=pacer,
        monotonic_fn=monotonic_fn,
        sleep_fn=sleep_fn,
    )

    reference_day = _today(today)
    errors: list[str] = []
    current_session: Any = None
    detail_sessions: list[Any] = []
    session_lock = Lock()
    first_pages: dict[str, BeautifulSoup] = {}
    declarations: dict[str, int] = {}
    all_rows: list[dict[str, Any]] = []
    try:
        current_session = session_factory()
        for leaf in CHANGWON_LEAVES:
            soup = _fetch(fetcher, current_session, changwon_list_url(leaf, 1), timeout)
            first_pages[leaf.code] = soup
            declared_last, active = _page_contract(soup, leaf)
            if declared_last < 1 or active != 1:
                errors.append(f"{leaf.code}: first-page pagination contract mismatch")
                continue
            declarations[leaf.code] = declared_last
            meta["declared_pages_by_leaf"][leaf.code] = declared_last
            meta["clamp_pages"][leaf.code] = declared_last + 1
            meta["pagination_detected"] = bool(
                meta["pagination_detected"] or declared_last > 1
            )

        if len(declarations) != len(CHANGWON_LEAVES):
            errors.append("fixed 25-leaf fan-out discovery is incomplete")
        required = sum(last + 1 for last in declarations.values())
        meta["required_list_requests"] = required
        if required > max_pages:
            meta["source_cap_reached"] = True
            errors.append(
                f"max_pages cap allows {max_pages} of {required} required list requests"
            )

        if not errors:
            for leaf in CHANGWON_LEAVES:
                declared_last = declarations[leaf.code]
                source_rows: list[dict[str, Any]] = []
                final_signature: tuple[Any, ...] = ()
                for page in range(1, declared_last + 2):
                    source_url = changwon_list_url(leaf, page)
                    soup = first_pages[leaf.code] if page == 1 else _fetch(
                        fetcher, current_session, source_url, timeout
                    )
                    meta["pages"] += 1
                    observed_last, active = _page_contract(soup, leaf)
                    expected_active = page if page <= declared_last else declared_last
                    if observed_last != declared_last or active != expected_active:
                        errors.append(
                            f"{leaf.code} page {page}: pagination marker/last-page mismatch"
                        )
                    rows, no_data, malformed = _parse_list_page(
                        target, leaf, soup, page=page, source_url=source_url
                    )
                    meta["page_counts"][f"{leaf.code}:{page}"] = len(rows)
                    if malformed:
                        errors.append(
                            f"{leaf.code} page {page}: {malformed} malformed catalogue rows"
                        )
                    if page < declared_last and len(rows) != CHANGWON_PAGE_SIZE:
                        errors.append(
                            f"{leaf.code} page {page}: expected {CHANGWON_PAGE_SIZE} rows before final page"
                        )
                    if page == declared_last and not rows and not no_data:
                        errors.append(f"{leaf.code}: final page is neither data nor no-data")
                    signature = _page_signature(rows, no_data)
                    if page == declared_last:
                        final_signature = signature
                    elif page == declared_last + 1 and signature != final_signature:
                        errors.append(
                            f"{leaf.code}: out-of-range page does not repeat final-page signature"
                        )
                    if page <= declared_last:
                        source_rows.extend(rows)
                meta["source_counts"][leaf.code] = len(source_rows)
                all_rows.extend(source_rows)

        meta["source_total"] = meta["source_rows"] = len(all_rows)
        group_counts = Counter(
            _clean(row.get("raw_fields", {}).get("leaf_group")) for row in all_rows
        )
        meta["source_group_counts"] = dict(sorted(group_counts.items()))
        source_statuses = Counter(
            _clean(row.get("raw_fields", {}).get("list_status")) for row in all_rows
        )
        meta["source_status_counts"] = dict(sorted(source_statuses.items()))
        identities = [_clean(row.get("raw_fields", {}).get("detail_id")) for row in all_rows]
        course_ids = [_clean(row.get("provider_course_id")) for row in all_rows]
        raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
        meta["duplicate_identity_count"] = len(identities) - len(set(identities))
        meta["duplicate_count"] = len(course_ids) - len(set(course_ids))
        meta["duplicate_url_count"] = len(raw_urls) - len(set(raw_urls))
        if meta["duplicate_identity_count"]:
            errors.append(f"{meta['duplicate_identity_count']} duplicate lecture identities")
        if meta["duplicate_count"]:
            errors.append(f"{meta['duplicate_count']} duplicate provider course ids")
        if meta["duplicate_url_count"]:
            errors.append(f"{meta['duplicate_url_count']} duplicate detail URLs")
        if detail_limit < len(all_rows):
            meta["source_cap_reached"] = True
            errors.append(
                f"detail_limit allows {detail_limit} of {len(all_rows)} required details"
            )

        if not errors and all_rows:
            tls = local()

            def detail_task(index: int, row: dict[str, Any]) -> tuple[int, list[str]]:
                worker_session = getattr(tls, "session", None)
                if worker_session is None:
                    worker_session = session_factory()
                    tls.session = worker_session
                    with session_lock:
                        detail_sessions.append(worker_session)
                leaf = CHANGWON_LEAF_BY_PATH[
                    _clean(row.get("raw_fields", {}).get("leaf_path"))
                ]
                try:
                    soup = _fetch(
                        fetcher, worker_session, _clean(row.get("raw_url")), timeout
                    )
                    return index, _enrich_detail(row, leaf, soup, reference_day)
                except Exception as exc:
                    return index, [
                        f"detail {row['raw_fields']['detail_id']}: fetch/parse failed ({type(exc).__name__})"
                    ]

            results: dict[int, list[str]] = {}
            workers = min(max_workers, len(all_rows))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(detail_task, index, row): index
                    for index, row in enumerate(all_rows)
                }
                meta["detail_attempts"] = len(futures)
                for future in as_completed(futures):
                    index, detail_errors = future.result()
                    results[index] = detail_errors
            for index in range(len(all_rows)):
                detail_errors = results.get(index) or []
                if detail_errors:
                    meta["detail_errors"] += 1
                    errors.extend(detail_errors)
                else:
                    meta["detail_pages"] += 1

        current_rows = [
            row
            for row in all_rows
            if row.get("end_date")
            and date.fromisoformat(_clean(row.get("end_date"))) >= reference_day
        ]
        meta["current_count"] = len(current_rows)
        meta["expired_count"] = len(all_rows) - len(current_rows)
        municipality_counts = Counter(
            _clean(row.get("municipality_full_name")) for row in all_rows
        )
        meta["municipality_counts"] = dict(sorted(municipality_counts.items()))
        current_municipality_counts = Counter(
            _clean(row.get("municipality_full_name")) for row in current_rows
        )
        meta["current_municipality_counts"] = dict(
            sorted(current_municipality_counts.items())
        )
        evidence_counts = Counter(
            _clean(
                row.get("raw_fields", {})
                .get("municipality_evidence", {})
                .get("field")
            )
            for row in all_rows
        )
        meta["municipality_evidence_counts"] = dict(sorted(evidence_counts.items()))
        official_over_capacity_ids = sorted(
            _clean(row.get("raw_fields", {}).get("detail_id"))
            for row in all_rows
            if row.get("raw_fields", {}).get("official_over_capacity")
        )
        meta["official_over_capacity_ids"] = official_over_capacity_ids
        meta["official_over_capacity_count"] = len(official_over_capacity_ids)
        detail_authoritative_drift_ids = sorted(
            _clean(row.get("raw_fields", {}).get("detail_id"))
            for row in all_rows
            if row.get("raw_fields", {}).get("detail_authoritative_drift")
        )
        meta["detail_authoritative_drift_ids"] = detail_authoritative_drift_ids
        meta["detail_authoritative_drift_count"] = len(
            detail_authoritative_drift_ids
        )
        meta["application_open_count"] = sum(
            row.get("status") == "OPEN" and bool(row.get("application_url"))
            for row in current_rows
        )
        meta["reservation_discovery_links"] = meta["application_open_count"]

        if not errors:
            semantic_counts = Counter(_semantic_key(row) for row in current_rows)
            meta["semantic_duplicate_count"] = sum(
                count - 1 for count in semantic_counts.values() if count > 1
            )
            if meta["semantic_duplicate_count"]:
                errors.append(
                    f"{meta['semantic_duplicate_count']} semantic duplicate courses"
                )
            if dedupe_rows is not None and not errors:
                deduped = list(dedupe_rows(current_rows))
                if len(deduped) != len(current_rows):
                    errors.append(
                        "dedupe changed complete row count "
                        f"{len(current_rows)} to {len(deduped)}"
                    )
                else:
                    current_rows = deduped

        meta["pagination_complete"] = (
            meta["pages"] == meta["required_list_requests"]
            and len(declarations) == len(CHANGWON_LEAVES)
            and not meta["source_cap_reached"]
            and not any(
                token in error
                for error in errors
                for token in ("page", "pagination", "fan-out", "catalogue row")
            )
        )
        meta["details_complete"] = (
            meta["detail_pages"] == len(all_rows)
            and meta["detail_errors"] == 0
            and not meta["source_cap_reached"]
        )
        meta["snapshot_complete"] = (
            not errors
            and meta["pagination_complete"]
            and meta["details_complete"]
            and meta["duplicate_count"] == 0
            and meta["duplicate_identity_count"] == 0
            and meta["duplicate_url_count"] == 0
            and meta["semantic_duplicate_count"] == 0
        )
        meta["no_current_data"] = meta["snapshot_complete"] and not current_rows
        if meta["no_current_data"]:
            meta["no_current_reason"] = (
                "the complete official Changwon 25-leaf fan-out has no current/future courses"
                if all_rows
                else "the complete official Changwon 25-leaf fan-out is empty"
            )
        meta["configured_collection_error"] = "; ".join(errors)
        return (
            current_rows if meta["snapshot_complete"] else [],
            CHANGWON_PARSER,
            meta,
        )
    except Exception as exc:
        errors.append(f"fixed fan-out fetch/parse failed ({type(exc).__name__})")
        meta["configured_collection_error"] = "; ".join(errors)
        return [], CHANGWON_PARSER, meta
    finally:
        _close_quietly(current_session)
        for detail_session in detail_sessions:
            _close_quietly(detail_session)


collect = collect_changwon_education_courses


__all__ = [
    "CHANGWON_CANDIDATE_ID",
    "CHANGWON_CANONICAL_URL",
    "CHANGWON_CRAWL_DELAY_SECONDS",
    "CHANGWON_COVERED_MUNICIPALITIES",
    "CHANGWON_EXCLUDED_TRAINING_URLS",
    "CHANGWON_HOST",
    "CHANGWON_LEAVES",
    "CHANGWON_MUNICIPALITY_CODE",
    "CHANGWON_MUNICIPALITY_NAME",
    "CHANGWON_NON_EXECUTING_ALIASES",
    "CHANGWON_OWNERSHIP_ALIAS_URLS",
    "CHANGWON_PAGE_SIZE",
    "CHANGWON_PARSER",
    "CHANGWON_PROVIDER",
    "CHANGWON_HOST_PACER",
    "ChangwonHostPacer",
    "ChangwonAlias",
    "ChangwonLeaf",
    "changwon_list_url",
    "collect",
    "collect_changwon_education_courses",
    "changwon_paced_fetcher",
    "is_changwon_education_target",
    "is_target",
]
