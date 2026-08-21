"""Fail-closed collector for Changwon City's integrated experience catalogue.

The official experience navigation is a fixed fan-out of 23 local catalogue
leaves.  It uses ``expId`` identities and a calendar-backed reservation flow,
which is different from the ``lectureId`` education catalogue.  This module
collects the catalogue and detail pages only; it deliberately never calls the
applicant calendar, agreement, authentication, or application endpoints.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from Crawler.municipal_changwon import (
    CHANGWON_DISTRICT_CODES,
    CHANGWON_HOST,
    CHANGWON_HOST_PACER,
    CHANGWON_MUNICIPALITY_CODE,
    CHANGWON_MUNICIPALITY_NAME,
    CHANGWON_MUNICIPALITY_NAMES,
    CHANGWON_PROVIDER,
    ChangwonHostPacer,
    changwon_paced_fetcher,
)


CHANGWON_EXPERIENCE_CANONICAL_URL = (
    "https://www.changwon.go.kr/booking/10031.web"
)
CHANGWON_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_12B0EBF0ADD7"
CHANGWON_EXPERIENCE_PAGE_SIZE = 10
CHANGWON_EXPERIENCE_PARSER = (
    "changwon_fixed_23_experience_leaf_fanout+declared_pages+"
    "clamped_last_recheck+all_detail_contracts+ongoing_or_end_filter+"
    "no_calendar_or_application_calls"
)


@dataclass(frozen=True)
class ChangwonExperienceLeaf:
    code: str
    group: str
    name: str
    path: str
    district: str = ""
    pagination_fcd: str = ""

    @property
    def url(self) -> str:
        return f"https://{CHANGWON_HOST}{self.path}"


CHANGWON_EXPERIENCE_LEAVES: tuple[ChangwonExperienceLeaf, ...] = (
    ChangwonExperienceLeaf("moonshin", "문화예술", "문신미술관", "/booking/10031/10062/10331.web", "마산합포구"),
    ChangwonExperienceLeaf("masan_museum", "문화예술", "마산박물관", "/booking/10031/10062/10111.web", "마산합포구"),
    ChangwonExperienceLeaf("ungcheon", "문화예술", "웅천도요지전시관", "/booking/10031/10062/10063.web", "진해구"),
    ChangwonExperienceLeaf("naval_culture", "문화예술", "군항문화탐방", "/booking/10031/10062/10065.web", "진해구"),
    ChangwonExperienceLeaf("modern_history", "문화예술", "근대문화역사길투어", "/booking/10031/10062/10066.web", "진해구"),
    ChangwonExperienceLeaf("arts_education", "문화예술", "창원문화예술교육센터", "/booking/10031/10062/10343.web", "의창구"),
    ChangwonExperienceLeaf("changdong_art", "문화예술", "창동예술촌", "/booking/10031/10062/10390.web", "마산합포구"),
    ChangwonExperienceLeaf("children_theme", "문화예술", "어린이테마체험존", "/booking/10031/10062/10467.web", "마산회원구"),
    ChangwonExperienceLeaf(
        "burim_craft",
        "문화예술",
        "부림창작공예촌",
        "/booking/10031/10062/10468.web",
        "마산합포구",
        pagination_fcd="F051",
    ),
    ChangwonExperienceLeaf("march_15", "문화예술", "3·15의거 발원지 기념관", "/booking/10031/10062/10469.web", "마산합포구"),
    ChangwonExperienceLeaf("healing_forest", "관광", "창원편백 치유의 숲", "/booking/10031/10067/10071.web", "진해구"),
    ChangwonExperienceLeaf("junam", "관광", "주남저수지", "/booking/10031/10067/10072.web", "의창구"),
    ChangwonExperienceLeaf("arboretum", "관광", "창원수목원", "/booking/10031/10067/10073.web", "성산구"),
    ChangwonExperienceLeaf("sweet_persimmon", "관광", "단감테마공원", "/booking/10031/10067/10074.web", "의창구"),
    ChangwonExperienceLeaf("chrysanthemum", "관광", "국화축제 가족체험", "/booking/10031/10067/10387.web", "마산합포구"),
    ChangwonExperienceLeaf("recycling_center", "환경", "창원시재활용센터", "/booking/10031/10068/10248.web", "진해구"),
    ChangwonExperienceLeaf("recycling_complex", "환경", "창원재활용종합단지", "/booking/10031/10068/10075.web", "성산구"),
    ChangwonExperienceLeaf("chilseo_water", "환경", "칠서정수장", "/booking/10031/10068/10076.web"),
    ChangwonExperienceLeaf("daesan_water", "환경", "대산정수장", "/booking/10031/10068/10077.web", "의창구"),
    ChangwonExperienceLeaf("seokdong_water", "환경", "석동정수장", "/booking/10031/10068/10078.web", "진해구"),
    ChangwonExperienceLeaf("sewerage", "환경", "하수도사업소", "/booking/10031/10068/10079.web", "마산합포구"),
    ChangwonExperienceLeaf("jinhae_health", "건강/안전", "진해보건소", "/booking/10031/10069/10081.web", "진해구"),
    ChangwonExperienceLeaf("changwon_health", "건강/안전", "창원보건소 건강증진센터", "/booking/10031/10069/10332.web", "의창구"),
)
CHANGWON_EXPERIENCE_LEAF_BY_PATH = {
    leaf.path: leaf for leaf in CHANGWON_EXPERIENCE_LEAVES
}


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_EXPERIENCE_ID_RE = re.compile(r"EX\d{6,12}\Z")
_DATE_RANGE_RE = re.compile(
    r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\s*~\s*"
    r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\Z"
)
_LIST_REQUIRED_KEYS = frozenset({"시설명", "접수일시", "운영기간", "장소"})
_DETAIL_REQUIRED_KEYS = frozenset(
    {"시설구분", "대상자", "접수기간", "운영기간", "장소", "승인방식"}
)
_SOURCE_STATUSES = frozenset({"접수중", "접수마감", "접수대기", "인원마감"})
_NO_DATA_TEXT = "등록된 자료가 없습니다."
_DETAIL_QUERY_KEYS = frozenset({"amode", "expId", "fcd", "cpage"})


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


def is_changwon_experience_target(target: Any) -> bool:
    return _provider(target) == CHANGWON_PROVIDER and _exact_https_url(
        _target_url(target), "/booking/10031.web"
    )


is_target = is_changwon_experience_target


def changwon_experience_list_url(
    leaf: ChangwonExperienceLeaf, page: Any = 1
) -> str:
    raw_page = _clean(page)
    if (
        leaf not in CHANGWON_EXPERIENCE_LEAVES
        or not raw_page.isdigit()
        or int(raw_page) < 1
    ):
        return ""
    page_number = int(raw_page)
    if page_number == 1:
        return leaf.url
    query = []
    if leaf.pagination_fcd:
        query.append(("fcd", leaf.pagination_fcd))
    query.append(("cpage", str(page_number)))
    return f"{leaf.url}?{urlencode(query)}"


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


def _fetch(
    fetcher: Fetcher, current_session: Any, url: str, timeout: int
) -> BeautifulSoup:
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


def _safe_leaf_href(
    value: Any, leaf: ChangwonExperienceLeaf, base_url: str
) -> tuple[str, str]:
    parsed = urlparse(urljoin(base_url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query(query, "expId")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != CHANGWON_HOST
        or parsed.port is not None
        or parsed.path != leaf.path
        or not set(query).issubset(_DETAIL_QUERY_KEYS)
        or _single_query(query, "amode") != "view"
        or not _EXPERIENCE_ID_RE.fullmatch(identity)
        or any(len(values) != 1 for values in query.values())
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return "", ""
    pairs: list[tuple[str, str]] = [("amode", "view"), ("expId", identity)]
    fcd = _single_query(query, "fcd")
    if fcd:
        pairs.append(("fcd", fcd))
    return identity, f"https://{CHANGWON_HOST}{leaf.path}?{urlencode(pairs)}"


def _page_contract(
    soup: BeautifulSoup, leaf: ChangwonExperienceLeaf
) -> tuple[int, int]:
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
        expected_query = {"cpage"}
        if leaf.pagination_fcd:
            expected_query.add("fcd")
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").rstrip(".").lower() != CHANGWON_HOST
            or parsed.path != leaf.path
            or set(query) != expected_query
            or _single_query(query, "cpage") != str(page)
            or (
                bool(leaf.pagination_fcd)
                and _single_query(query, "fcd") != leaf.pagination_fcd
            )
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
    leaf: ChangwonExperienceLeaf,
    identity: str,
    raw_url: str,
    title: str,
    status: str,
    page: int,
    pairs: Mapping[str, str],
    fee_badge: str,
    methods: list[str],
) -> dict[str, Any]:
    return {
        "provider": _provider(target),
        "provider_course_id": f"{_provider(target)}:changwon-exp:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "program_type": f"{leaf.group} / {leaf.name}",
        "category": "체험·견학",
        "branch": CHANGWON_MUNICIPALITY_NAME,
        "branch_code": _branch_code(CHANGWON_MUNICIPALITY_CODE),
        "branch_url": CHANGWON_EXPERIENCE_CANONICAL_URL,
        "preserve_branch": True,
        "raw_url": raw_url,
        "application_url": "",
        "reservation_available": False,
        "status": "CLOSED",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "municipality_code": CHANGWON_MUNICIPALITY_CODE,
        "municipality_full_name": CHANGWON_MUNICIPALITY_NAME,
        "collection_type": (
            "complete_fixed_experience_fanout+clamped_page_recheck+detail_html"
        ),
        "description": _clean(" ".join(pairs.values())),
        "target": "",
        "venue_name": pairs.get("장소", ""),
        "fee": fee_badge,
        "raw_fields": {
            "parser": CHANGWON_EXPERIENCE_PARSER,
            "leaf_code": leaf.code,
            "leaf_group": leaf.group,
            "leaf_name": leaf.name,
            "leaf_path": leaf.path,
            "leaf_page": page,
            "detail_id": identity,
            "list_title": title,
            "list_status": status,
            "list_pairs": dict(pairs),
            "fee_badge": fee_badge,
            "application_methods": methods,
            "application_method": " / ".join(methods),
        },
    }


def _parse_list_page(
    target: Any,
    leaf: ChangwonExperienceLeaf,
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
        badges = card.select("a.tg1 .cate")
        methods = [
            _clean(node.get_text(" ", strip=True)) for node in card.select(".g2s .g2")
        ]
        if (
            not identity
            or identity != figure_identity
            or raw_url != figure_url
            or not title
            or not status
            or pairs is None
            or set(pairs) != _LIST_REQUIRED_KEYS
            or len(badges) != 1
            or any(not method for method in methods)
        ):
            malformed += 1
            continue
        fee_badge = _clean(badges[0].get_text(" ", strip=True))
        if fee_badge not in {"무료", "유료"}:
            malformed += 1
            continue
        rows.append(
            _base_row(
                target,
                leaf,
                identity,
                raw_url,
                title,
                status,
                page,
                pairs,
                fee_badge,
                methods,
            )
        )
    return rows, False, malformed


def _page_signature(
    rows: Iterable[Mapping[str, Any]], no_data: bool
) -> tuple[Any, ...]:
    return (
        bool(no_data),
        tuple(
            (
                _clean(row.get("raw_fields", {}).get("detail_id")),
                _normalized(row.get("title")),
                _normalized(row.get("raw_fields", {}).get("list_status")),
                tuple(
                    sorted(
                        (_normalized(key), _normalized(value))
                        for key, value in row.get("raw_fields", {})
                        .get("list_pairs", {})
                        .items()
                    )
                ),
            )
            for row in rows
        ),
    )


def _date_period(
    value: Any,
) -> tuple[Optional[date], Optional[date], str, bool]:
    text = _clean(value)
    if text == "상시":
        return None, None, "상시", True
    match = _DATE_RANGE_RE.fullmatch(text)
    if match is None:
        return None, None, "", False
    try:
        start = date(*(int(item) for item in match.groups()[:3]))
        end = date(*(int(item) for item in match.groups()[3:]))
    except ValueError:
        return None, None, "", False
    if end < start:
        return None, None, "", False
    return start, end, f"{start.isoformat()} ~ {end.isoformat()}", False


def _same_field(left: Any, right: Any) -> bool:
    return _normalized(left) == _normalized(right)


def _detail_pairs(
    soup: BeautifulSoup,
) -> tuple[Optional[Any], Optional[dict[str, str]]]:
    roots = soup.select(".cp31edu1view1")
    if len(roots) != 1:
        return None, None
    pairs = _pairs(roots[0], ".cp31dlist2")
    return roots[0], pairs


def _facility_contract(soup: BeautifulSoup) -> tuple[str, str, str, bool]:
    panes = soup.select("#tabs1pane3")
    if len(panes) != 1:
        return "", "", "", False
    boxes = panes[0].select(".detail1box")
    if len(boxes) != 1:
        return "", "", "", False
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
    if not name:
        return "", "", "", False
    return name, address, phone, True


def _description(soup: BeautifulSoup) -> str:
    panes = soup.select("#tabs1pane1")
    if len(panes) != 1:
        return ""
    clone = BeautifulSoup(str(panes[0]), "lxml")
    for hidden in clone.select(".blind"):
        hidden.decompose()
    return _clean(clone.get_text(" ", strip=True))


def _district_tokens(value: Any) -> list[str]:
    text = _clean(value)
    return [token for token in CHANGWON_DISTRICT_CODES if token in text]


def _municipality_assignment(
    leaf: ChangwonExperienceLeaf,
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
    all_tokens = {
        token for _field, value in fields for token in _district_tokens(value)
    }
    if len(all_tokens) > 1:
        return "", "", "conflict", ",".join(sorted(all_tokens))
    if all_tokens:
        district = next(iter(all_tokens))
        if leaf.district and district != leaf.district:
            return "", "", "conflict", f"{leaf.district}!={district}"
        code = CHANGWON_DISTRICT_CODES[district]
        for field, value in fields:
            if district in _district_tokens(value):
                return code, CHANGWON_MUNICIPALITY_NAMES[code], field, value
    if leaf.district:
        code = CHANGWON_DISTRICT_CODES[leaf.district]
        return code, CHANGWON_MUNICIPALITY_NAMES[code], "official_leaf_fallback", leaf.path
    return (
        CHANGWON_MUNICIPALITY_CODE,
        CHANGWON_MUNICIPALITY_NAME,
        "citywide_no_district_evidence",
        leaf.path,
    )


def _reservation_contract(root: Any, soup: BeautifulSoup) -> tuple[bool, int]:
    controls = []
    unsafe = 0
    for node in root.select("a.reserve1[href]"):
        label = _clean(node.get_text(" ", strip=True))
        href = _clean(node.get("href"))
        if label == "예약하기" and href == "#tabs1pane4":
            controls.append(node)
        elif "예약" in label:
            unsafe += 1
    panes = soup.select("#tabs1pane4")
    if len(controls) == 1 and len(panes) == 1 and unsafe == 0:
        return True, 0
    if not controls and not panes and unsafe == 0:
        return False, 0
    return False, unsafe + len(controls) + len(panes)


def _enrich_detail(
    row: dict[str, Any],
    leaf: ChangwonExperienceLeaf,
    soup: BeautifulSoup,
    reference_day: date,
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("detail_id"))
    root, pairs = _detail_pairs(soup)
    if root is None or pairs is None:
        return [f"detail {identity}: detail view contract mismatch"]
    missing = sorted(_DETAIL_REQUIRED_KEYS - set(pairs))
    extra = sorted(set(pairs) - _DETAIL_REQUIRED_KEYS - {"첨부파일"})
    if missing or extra:
        return [
            f"detail {identity}: detail key contract mismatch "
            f"missing={','.join(missing)} extra={','.join(extra)}"
        ]
    title_nodes = root.select(".w1c2 h3.h1")
    detail_title = (
        _clean(title_nodes[0].get_text(" ", strip=True))
        if len(title_nodes) == 1
        else ""
    )
    detail_status = _status_contract(root)
    list_pairs = row.get("raw_fields", {}).get("list_pairs", {})
    if not detail_title or not _normalized(row.get("title")).startswith(
        _normalized(detail_title)
    ):
        return [f"detail {identity}: detail/list title mismatch"]
    if detail_status != _clean(row.get("raw_fields", {}).get("list_status")):
        return [f"detail {identity}: detail/list status mismatch"]
    comparisons = (
        ("접수일시", "접수기간"),
        ("운영기간", "운영기간"),
        ("장소", "장소"),
    )
    for list_key, detail_key in comparisons:
        if not _same_field(list_pairs.get(list_key), pairs.get(detail_key)):
            return [f"detail {identity}: detail/list {detail_key} mismatch"]
    if not _normalized(pairs["시설구분"]).startswith(
        _normalized(list_pairs.get("시설명"))
    ):
        return [f"detail {identity}: detail/list 시설구분 mismatch"]

    start, end, period, ongoing = _date_period(pairs["운영기간"])
    apply_start, apply_end, apply_period, apply_ongoing = _date_period(
        pairs["접수기간"]
    )
    if not period or not apply_period:
        return [f"detail {identity}: invalid operation/application period"]
    is_current = ongoing or bool(end and end >= reference_day)
    application_active = apply_ongoing or bool(
        apply_start and apply_end and apply_start <= reference_day <= apply_end
    )
    reservation_tab, unsafe_controls = _reservation_contract(root, soup)
    if unsafe_controls:
        return [f"detail {identity}: ambiguous reservation tab contract"]
    methods = [
        _clean(value)
        for value in row.get("raw_fields", {}).get("application_methods", [])
        if _clean(value)
    ]
    if detail_status == "접수중" and (not is_current or not application_active):
        return [f"detail {identity}: open status lies outside published periods"]
    if detail_status == "접수중" and "인터넷" in methods and not reservation_tab:
        return [f"detail {identity}: internet-open status lacks reservation tab"]

    # The official facility templates keep the exact reservation tab/calendar
    # shell on some closed (and potentially scheduled) details.  The source
    # status badge remains authoritative: a tab alone never makes a non-open
    # item reservable.  Structural drift still fails closed above through the
    # paired-control/pane contract and unsafe_controls.

    facility_name, facility_address, phone, facility_ok = _facility_contract(soup)
    if not facility_ok:
        return [f"detail {identity}: facility pane contract mismatch"]
    municipality_code, municipality_name, evidence_field, evidence_value = (
        _municipality_assignment(
            leaf,
            facility_address=facility_address,
            facility_name=facility_name,
            facility_type=pairs["시설구분"],
            venue=pairs["장소"],
            title=detail_title,
        )
    )
    if not municipality_code:
        return [f"detail {identity}: district evidence conflicts with official leaf"]

    if detail_status == "접수중":
        normalized_status = "OPEN"
    elif detail_status == "접수대기":
        normalized_status = "SCHEDULED"
    else:
        normalized_status = "CLOSED"
    online = normalized_status == "OPEN" and "인터넷" in methods and reservation_tab
    if online:
        application_type = "ONLINE_RESERVATION"
    elif normalized_status == "OPEN" and "전화" in methods:
        application_type = "PHONE_APPLY"
    elif normalized_status == "OPEN" and "방문" in methods:
        application_type = "VISIT_APPLY"
    else:
        application_type = "INFORMATION_ONLY"

    row.update(
        {
            "title": detail_title,
            "period": period,
            "start_date": start.isoformat() if start else "",
            "end_date": end.isoformat() if end else "",
            "apply_period": apply_period,
            "apply_start": apply_start.isoformat() if apply_start else "",
            "apply_end": apply_end.isoformat() if apply_end else "",
            "target": pairs["대상자"],
            "venue_name": pairs["장소"],
            "venue_address": facility_address,
            "approval_method": pairs["승인방식"],
            "phone": phone,
            "status": normalized_status,
            "application_url": _clean(row.get("raw_url")) if online else "",
            "reservation_available": bool(online),
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
        "detail_pairs": pairs,
        "facility_name": facility_name,
        "facility_address": facility_address,
        "ongoing_operation": ongoing,
        "ongoing_application": apply_ongoing,
        "current_by_operation_period": is_current,
        "reservation_tab_present": reservation_tab,
        "calendar_endpoint_called": False,
        "municipality_evidence": {
            "field": evidence_field,
            "value": evidence_value,
            "code": municipality_code,
            "full_name": municipality_name,
        },
    }
    return []


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("period")),
        _normalized(row.get("venue_name")),
    )


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "leaf_count": len(CHANGWON_EXPERIENCE_LEAVES),
        "source_count": len(CHANGWON_EXPERIENCE_LEAVES),
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
        "ongoing_count": 0,
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
        "application_open_count": 0,
        "reservation_discovery_links": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "calendar_requests": 0,
        "application_requests": 0,
        "recursion_depth": 0,
        "configured_collection_error": "",
    }


def collect_changwon_experience_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 100,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    crawl_delay_seconds: float = 0.0,
    pacer: ChangwonHostPacer = CHANGWON_HOST_PACER,
    monotonic_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Changwon experience snapshot."""

    meta = _base_meta()
    if not is_changwon_experience_target(target):
        meta["configured_collection_error"] = (
            "target is not the canonical Changwon experience landing"
        )
        return [], CHANGWON_EXPERIENCE_PARSER, meta
    if fetcher is None or session_factory is None:
        meta["configured_collection_error"] = (
            "managed fetcher and session_factory injection are required"
        )
        return [], CHANGWON_EXPERIENCE_PARSER, meta
    if (
        max_pages < len(CHANGWON_EXPERIENCE_LEAVES) * 2
        or detail_limit < 0
        or crawl_delay_seconds < 0
    ):
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "max_pages/detail_limit/crawl_delay are invalid for fixed fan-out"
        )
        return [], CHANGWON_EXPERIENCE_PARSER, meta

    effective_fetcher = changwon_paced_fetcher(
        fetcher,
        delay_seconds=crawl_delay_seconds,
        pacer=pacer,
        monotonic_fn=monotonic_fn,
        sleep_fn=sleep_fn,
    )
    reference_day = _today(today)
    errors: list[str] = []
    current_session: Any = None
    first_pages: dict[str, BeautifulSoup] = {}
    declarations: dict[str, int] = {}
    all_rows: list[dict[str, Any]] = []
    try:
        current_session = session_factory()
        for leaf in CHANGWON_EXPERIENCE_LEAVES:
            soup = _fetch(
                effective_fetcher,
                current_session,
                changwon_experience_list_url(leaf, 1),
                timeout,
            )
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

        if len(declarations) != len(CHANGWON_EXPERIENCE_LEAVES):
            errors.append("fixed 23-leaf experience fan-out discovery is incomplete")
        required = sum(last + 1 for last in declarations.values())
        meta["required_list_requests"] = required
        if required > max_pages:
            meta["source_cap_reached"] = True
            errors.append(
                f"max_pages cap allows {max_pages} of {required} required list requests"
            )

        if not errors:
            for leaf in CHANGWON_EXPERIENCE_LEAVES:
                declared_last = declarations[leaf.code]
                source_rows: list[dict[str, Any]] = []
                final_signature: tuple[Any, ...] = ()
                for page in range(1, declared_last + 2):
                    source_url = changwon_experience_list_url(leaf, page)
                    soup = (
                        first_pages[leaf.code]
                        if page == 1
                        else _fetch(
                            effective_fetcher,
                            current_session,
                            source_url,
                            timeout,
                        )
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
                    if page < declared_last and len(rows) != CHANGWON_EXPERIENCE_PAGE_SIZE:
                        errors.append(
                            f"{leaf.code} page {page}: expected "
                            f"{CHANGWON_EXPERIENCE_PAGE_SIZE} rows before final page"
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
        meta["source_group_counts"] = dict(
            sorted(
                Counter(
                    _clean(row.get("raw_fields", {}).get("leaf_group"))
                    for row in all_rows
                ).items()
            )
        )
        meta["source_status_counts"] = dict(
            sorted(
                Counter(
                    _clean(row.get("raw_fields", {}).get("list_status"))
                    for row in all_rows
                ).items()
            )
        )
        identities = [
            _clean(row.get("raw_fields", {}).get("detail_id")) for row in all_rows
        ]
        course_ids = [_clean(row.get("provider_course_id")) for row in all_rows]
        raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
        meta["duplicate_identity_count"] = len(identities) - len(set(identities))
        meta["duplicate_count"] = len(course_ids) - len(set(course_ids))
        meta["duplicate_url_count"] = len(raw_urls) - len(set(raw_urls))
        if meta["duplicate_identity_count"]:
            errors.append(
                f"{meta['duplicate_identity_count']} duplicate experience identities"
            )
        if meta["duplicate_count"]:
            errors.append(f"{meta['duplicate_count']} duplicate provider course ids")
        if meta["duplicate_url_count"]:
            errors.append(f"{meta['duplicate_url_count']} duplicate detail URLs")
        if detail_limit < len(all_rows):
            meta["source_cap_reached"] = True
            errors.append(
                f"detail_limit allows {detail_limit} of {len(all_rows)} required details"
            )

        if not errors:
            meta["detail_attempts"] = len(all_rows)
            for row in all_rows:
                identity = _clean(row.get("raw_fields", {}).get("detail_id"))
                leaf = CHANGWON_EXPERIENCE_LEAF_BY_PATH[
                    _clean(row.get("raw_fields", {}).get("leaf_path"))
                ]
                try:
                    soup = _fetch(
                        effective_fetcher,
                        current_session,
                        _clean(row.get("raw_url")),
                        timeout,
                    )
                    detail_errors = _enrich_detail(
                        row, leaf, soup, reference_day
                    )
                except Exception as exc:
                    detail_errors = [
                        f"detail {identity}: fetch/parse failed ({type(exc).__name__})"
                    ]
                if detail_errors:
                    meta["detail_errors"] += 1
                    errors.extend(detail_errors)
                else:
                    meta["detail_pages"] += 1

        current_rows = [
            row
            for row in all_rows
            if row.get("raw_fields", {}).get("current_by_operation_period")
        ]
        meta["current_count"] = len(current_rows)
        meta["expired_count"] = len(all_rows) - len(current_rows)
        meta["ongoing_count"] = sum(
            bool(row.get("raw_fields", {}).get("ongoing_operation"))
            for row in all_rows
        )
        meta["municipality_counts"] = dict(
            sorted(
                Counter(
                    _clean(row.get("municipality_full_name")) for row in all_rows
                ).items()
            )
        )
        meta["current_municipality_counts"] = dict(
            sorted(
                Counter(
                    _clean(row.get("municipality_full_name")) for row in current_rows
                ).items()
            )
        )
        meta["municipality_evidence_counts"] = dict(
            sorted(
                Counter(
                    _clean(
                        row.get("raw_fields", {})
                        .get("municipality_evidence", {})
                        .get("field")
                    )
                    for row in all_rows
                ).items()
            )
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
                    f"{meta['semantic_duplicate_count']} semantic duplicate experiences"
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
            and len(declarations) == len(CHANGWON_EXPERIENCE_LEAVES)
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
                "the complete official Changwon 23-leaf experience fan-out has no current/future experiences"
                if all_rows
                else "the complete official Changwon 23-leaf experience fan-out is empty"
            )
        meta["configured_collection_error"] = "; ".join(errors)
        return (
            current_rows if meta["snapshot_complete"] else [],
            CHANGWON_EXPERIENCE_PARSER,
            meta,
        )
    except Exception as exc:
        errors.append(f"fixed experience fan-out fetch/parse failed ({type(exc).__name__})")
        meta["configured_collection_error"] = "; ".join(errors)
        return [], CHANGWON_EXPERIENCE_PARSER, meta
    finally:
        _close_quietly(current_session)


collect = collect_changwon_experience_courses


__all__ = [
    "CHANGWON_EXPERIENCE_CANDIDATE_ID",
    "CHANGWON_EXPERIENCE_CANONICAL_URL",
    "CHANGWON_EXPERIENCE_LEAF_BY_PATH",
    "CHANGWON_EXPERIENCE_LEAVES",
    "CHANGWON_EXPERIENCE_PAGE_SIZE",
    "CHANGWON_EXPERIENCE_PARSER",
    "ChangwonExperienceLeaf",
    "changwon_experience_list_url",
    "collect_changwon_experience_courses",
    "is_changwon_experience_target",
]
