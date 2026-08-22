"""Fail-closed collector for Jinju City's internal education catalogue.

Jinju's integrated-reservation education navigation has twelve authoritative
internal ``전체강좌`` leaves.  A thirteenth education-menu item is the toy-bank
play classroom and the library/exhibition links leave the platform; neither
belongs to this education owner.  The twelve leaves are therefore traversed as
one fixed fan-out and must never be scheduled as independent providers.

The site exposes no reliable total counter.  Completeness is instead proved by
the declared final page, ten-row intermediate pages, an immediately empty
``last + 1`` sentinel, and a stable first-page boundary recheck for every leaf.
Only current/future rows are detailed and returned.  A missing page, malformed
identity, status/date drift, non-Jinju facility, or spoofed application control
fails the entire snapshot closed.

This module intentionally does not import ``Crawler_MunicipalYaml``.  The
shared router must inject its managed fetcher and session factory.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from threading import Lock, local
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


JINJU_PROVIDER = "MUNI_WWW_JINJU_GO_KR_AC4F2628"
JINJU_CANDIDATE_ID = "MUNI_IR_69C7C0BA6431"
JINJU_CANONICAL_URL = (
    "https://www.jinju.go.kr/yeyak/08870/08878/09652.web"
)
JINJU_HOST = "www.jinju.go.kr"
JINJU_PAGE_SIZE = 10
JINJU_MAX_WORKERS = 8
JINJU_MUNICIPALITY_CODE = "4817000000"
JINJU_MUNICIPALITY_NAME = "경상남도 진주시"
JINJU_PARSER = (
    "jinju_fixed_12_internal_education_leaves+declared_pages+empty_sentinel+"
    "stable_first_page+current_detail+real_application_control"
)


@dataclass(frozen=True)
class JinjuLeaf:
    code: str
    name: str
    path: str

    @property
    def url(self) -> str:
        return f"https://{JINJU_HOST}{self.path}"


JINJU_EDUCATION_LEAVES: tuple[JinjuLeaf, ...] = (
    JinjuLeaf("info", "정보화교육", "/yeyak/08870/08878/09652.web"),
    JinjuLeaf("lifelong", "평생학습관", "/yeyak/08870/08880/09651.web"),
    JinjuLeaf("future", "진주미래인재학습지원센터", "/yeyak/08870/08881/08962.web"),
    JinjuLeaf("childcare", "육아종합지원센터", "/yeyak/08870/08882/09650.web"),
    JinjuLeaf("youth", "청소년수련관", "/yeyak/08870/09570/09655.web"),
    JinjuLeaf("agri", "농업기술센터", "/yeyak/08870/10080/10081.web"),
    JinjuLeaf("sangpyeong", "상평복합문화센터", "/yeyak/08870/10255/10256.web"),
    JinjuLeaf("heritage", "전수교육관", "/yeyak/08870/10300/10302.web"),
    JinjuLeaf("tea", "진주 차문화 홍보관", "/yeyak/08870/10345/10346.web"),
    JinjuLeaf("cpr", "심폐소생술 등 응급 처치교육", "/yeyak/08870/10353/10354.web"),
    JinjuLeaf("namsung", "진주 남성당 교육관", "/yeyak/08870/10416/10417.web"),
    JinjuLeaf("forest", "산림정원과 특강", "/yeyak/08870/10361/10419.web"),
)
JINJU_LEAF_BY_CODE = {leaf.code: leaf for leaf in JINJU_EDUCATION_LEAVES}
JINJU_LEAF_BY_PATH = {leaf.path: leaf for leaf in JINJU_EDUCATION_LEAVES}


@dataclass(frozen=True)
class JinjuAlias:
    provider: str
    url: str
    reason: str
    ownership: str = "subset"


JINJU_NON_EXECUTING_ALIASES: tuple[JinjuAlias, ...] = (
    JinjuAlias(
        "MUNI_WWW_JINJU_GO_KR_170613AA",
        "https://www.jinju.go.kr/yeyak/08870/08878/10012.web",
        "시민교양강좌 is one sub-leaf of the canonical information-education archive",
    ),
    JinjuAlias(
        "MUNI_WWW_JINJU_GO_KR_5DF28B13",
        "https://www.jinju.go.kr/yeyak/08870/08882/09650.web",
        "existing manual target is the canonical childcare aggregate leaf only",
    ),
    JinjuAlias(
        "MUNI_WWW_JINJU_GO_KR_5DF28B13",
        "https://www.jinju.go.kr/yeyak/08870/08880/09651.web?amode=view&lecture=L_AB00000000809",
        "the same legacy provider id originated from one lifelong-learning detail URL",
    ),
    JinjuAlias(
        "MUNI_WWW_JINJU_GO_KR_33F25517",
        "https://www.jinju.go.kr/yeyak/08870/10080/10081.web",
        "deprecated target is the canonical agriculture aggregate leaf only",
    ),
    JinjuAlias(
        "MUNI_WWW_JINJU_GO_KR_CC4D7F07",
        "https://www.jinju.go.kr/yeyak/08870/09630/09653.web?gubunCd=FAC_005&cpage=1",
        "toy-bank play classroom is a separately owned play/experience catalogue",
        ownership="excluded_toybank",
    ),
    JinjuAlias(
        "MUNI_WWW_JINJU_GO_KR_E44EC221",
        "https://www.jinju.go.kr/",
        "city-home discovery shell, not a course catalogue",
        ownership="excluded_discovery",
    ),
    JinjuAlias(
        "MUNI_WWW_JINJU_GO_KR_9110306D",
        "https://www.jinju.go.kr/tour.web",
        "tourism portal, not the integrated education catalogue",
        ownership="excluded_non_education",
    ),
)
JINJU_OWNERSHIP_ALIAS_URLS = tuple(
    item.url for item in JINJU_NON_EXECUTING_ALIASES if item.ownership == "subset"
)
JINJU_EXCLUDED_TOYBANK_URLS = tuple(
    item.url
    for item in JINJU_NON_EXECUTING_ALIASES
    if item.ownership == "excluded_toybank"
)


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_LECTURE_RE = re.compile(r"L(?:_[A-Z]{2,8})?\d{8,14}\Z")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
_GUBUN_CODE_RE = re.compile(r"FAC_\d{3}\Z")
_FCD_CODE_RE = re.compile(r"F\d{3}\Z")
_SOURCE_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "정원마감": "CLOSED",
    "대기접수": "WAITLIST",
    "접수마감": "CLOSED",
    "홍보중": "SCHEDULED",
}
_LIST_REQUIRED_FIELDS = frozenset(
    {
        "교육구분",
        "신청대상",
        "교육기간",
        "요일시간",
        "선발방식",
        "정원/접수인원/대기자정원",
        "신청현황",
        "수강료",
    }
)
_LIST_APPLICATION_FIELDS = ("접수일시", "신청기간")
_DETAIL_FIELDS: Mapping[str, str] = {
    "facilities": "시설구분",
    "curriculum": "교육과정",
    "edu": "교육구분",
    "target": "신청대상",
    "receipt": "접수일시",
    "period": "교육기간",
    "dayhour": "요일시간",
    "selection": "선발방식",
    "quota": "정원/접수인원/대기자정원",
    "application": "신청현황",
    "tuition": "수강료",
    "reception": "접수처",
}
_METHOD_LABELS = ("인터넷", "방문", "전화")


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _single_query(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return _clean(values[0]) if len(values) == 1 else ""


def is_jinju_education_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == JINJU_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == JINJU_HOST
        and parsed.port is None
        and parsed.path == urlparse(JINJU_CANONICAL_URL).path
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_jinju_education_target


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def jinju_list_url(leaf: JinjuLeaf | str, page: int = 1, facility_code: str = "") -> str:
    item = JINJU_LEAF_BY_CODE.get(leaf) if isinstance(leaf, str) else leaf
    if item is None or not isinstance(page, int) or page < 1:
        return ""
    facility_code = _clean(facility_code)
    if facility_code and not (
        _GUBUN_CODE_RE.fullmatch(facility_code)
        or _FCD_CODE_RE.fullmatch(facility_code)
    ):
        return ""
    if page == 1:
        return item.url
    query: list[tuple[str, Any]] = []
    if facility_code:
        query.append(
            ("gubunCd" if facility_code.startswith("FAC_") else "fcd", facility_code)
        )
    query.append(("cpage", page))
    return f"{item.url}?{urlencode(query)}"


def jinju_detail_url(leaf: JinjuLeaf | str, identity: Any) -> str:
    item = JINJU_LEAF_BY_CODE.get(leaf) if isinstance(leaf, str) else leaf
    value = _clean(identity)
    if item is None or not _LECTURE_RE.fullmatch(value):
        return ""
    return f"{item.url}?{urlencode((('amode', 'view'), ('lecture', value)))}"


def jinju_application_url(leaf: JinjuLeaf | str, identity: Any) -> str:
    item = JINJU_LEAF_BY_CODE.get(leaf) if isinstance(leaf, str) else leaf
    value = _clean(identity)
    if item is None or not _LECTURE_RE.fullmatch(value):
        return ""
    return f"{item.url}?{urlencode((('amode', 'ins'), ('lecture', value)))}"


def _as_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if hasattr(value, "raise_for_status"):
        value.raise_for_status()
    text = getattr(value, "text", value)
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if not isinstance(text, str):
        raise TypeError("fetcher did not return HTML")
    return BeautifulSoup(text, "html.parser")


def _close_quietly(value: Any) -> None:
    try:
        if value is not None and callable(getattr(value, "close", None)):
            value.close()
    except Exception:
        pass


def _parallel_fetch(
    items: list[tuple[Any, str]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, BeautifulSoup], list[str]]:
    if not items:
        return {}, []
    tls = local()
    sessions: list[Any] = []
    lock = Lock()

    def task(key: Any, url: str) -> tuple[Any, Optional[BeautifulSoup], str]:
        session = getattr(tls, "session", None)
        if session is None:
            session = session_factory()
            tls.session = session
            with lock:
                sessions.append(session)
        try:
            return key, _as_soup(fetcher(session, url, timeout)), ""
        except Exception as exc:
            return key, None, f"{key}: fetch failed ({type(exc).__name__})"

    soups: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    workers = min(max_workers, len(items))
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(task, key, url) for key, url in items]
            for future in as_completed(futures):
                key, soup, error = future.result()
                if error:
                    errors.append(error)
                elif soup is not None:
                    soups[key] = soup
    finally:
        for session in sessions:
            _close_quietly(session)
    return soups, errors


def _safe_href(value: Any, leaf: JinjuLeaf, base_url: str, amode: str) -> tuple[str, str]:
    parsed = urlparse(urljoin(base_url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query(query, "lecture")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != JINJU_HOST
        or parsed.port is not None
        or parsed.path != leaf.path
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or set(query) != {"amode", "lecture"}
        or _single_query(query, "amode") != amode
        or not _LECTURE_RE.fullmatch(identity)
    ):
        return "", ""
    canonical = (
        jinju_detail_url(leaf, identity)
        if amode == "view"
        else jinju_application_url(leaf, identity)
    )
    return identity, canonical


def _pagination_state(
    soup: BeautifulSoup, leaf: JinjuLeaf
) -> tuple[int, int, str, bool]:
    pagers = soup.select(".pagination")
    if not pagers:
        return 1, 1, "", True
    if len(pagers) != 1:
        return 0, 0, "", False
    pages: set[int] = set()
    codes: set[str] = set()
    valid = True
    for anchor in pagers[0].select("a[href]"):
        parsed = urlparse(urljoin(leaf.url, _clean(anchor.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if "cpage" not in query:
            continue
        raw_page = _single_query(query, "cpage")
        gubun_code = _single_query(query, "gubunCd")
        fcd_code = _single_query(query, "fcd")
        code = gubun_code or fcd_code
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").rstrip(".").lower() != JINJU_HOST
            or parsed.port is not None
            or parsed.path != leaf.path
            or parsed.params
            or parsed.fragment
            or parsed.username
            or parsed.password
            or not set(query).issubset({"cpage", "gubunCd", "fcd"})
            or (gubun_code and fcd_code)
            or not raw_page.isdigit()
            or int(raw_page) < 1
            or (
                gubun_code
                and not _GUBUN_CODE_RE.fullmatch(gubun_code)
            )
            or (fcd_code and not _FCD_CODE_RE.fullmatch(fcd_code))
        ):
            valid = False
            continue
        pages.add(int(raw_page))
        if code:
            codes.add(code)
    active_nodes = pagers[0].select(".m.on")
    active = 0
    if len(active_nodes) == 1:
        raw_active = _clean(active_nodes[0].get_text(" ", strip=True))
        active = int(raw_active) if raw_active.isdigit() else 0
        if active:
            pages.add(active)
    last_anchor = pagers[0].select_one(".m.last a[href*='cpage']")
    declared = max(pages) if pages else active
    if last_anchor is not None:
        query = parse_qs(urlparse(urljoin(leaf.url, last_anchor.get("href"))).query)
        raw_last = _single_query(query, "cpage")
        if not raw_last.isdigit():
            valid = False
        else:
            declared = int(raw_last)
    if len(codes) > 1 or declared < 1 or active < 1:
        valid = False
    return declared, active, next(iter(codes), ""), valid


def _pairs(container: Any) -> Optional[dict[str, str]]:
    pairs: dict[str, str] = {}
    nodes = container.select(".cp31dlist1 li.di") if container else []
    if not nodes:
        return None
    for node in nodes:
        dt = node.select_one(".dt")
        dd = node.select_one(".dd")
        key = _clean(dt.get_text(" ", strip=True)).rstrip(":").strip() if dt else ""
        value = _clean(dd.get_text(" ", strip=True)) if dd else ""
        if not key or not value or key in pairs:
            return None
        pairs[key] = value
    return pairs


def _methods(container: Any) -> Optional[dict[str, bool]]:
    nodes = container.select(".g2s a.g2") if container else []
    if len(nodes) != len(_METHOD_LABELS):
        return None
    result: dict[str, bool] = {}
    for node in nodes:
        label = _clean(node.get_text(" ", strip=True))
        if label not in _METHOD_LABELS or label in result:
            return None
        result[label] = "disabled" not in set(node.get("class") or [])
    return result if set(result) == set(_METHOD_LABELS) else None


def _date_pair(value: Any) -> tuple[Optional[date], Optional[date]]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        return None, None
    try:
        return tuple(date(*(int(part) for part in raw)) for raw in matches)  # type: ignore[return-value]
    except ValueError:
        return None, None


def _application_period(pairs: Mapping[str, str]) -> tuple[str, str]:
    present = [key for key in _LIST_APPLICATION_FIELDS if _clean(pairs.get(key))]
    if len(present) != 1:
        return "", ""
    key = present[0]
    return key, _clean(pairs[key])


def _branch_code(facility_name: Any) -> str:
    digest = hashlib.sha1(
        f"{JINJU_PROVIDER}|{_normalized(facility_name)}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"JINJU_BRANCH_{digest}"


def _base_row(
    target: Any,
    leaf: JinjuLeaf,
    *,
    identity: str,
    title: str,
    source_status: str,
    raw_url: str,
    pairs: Mapping[str, str],
    methods: Mapping[str, bool],
    page: int,
) -> dict[str, Any]:
    application_label, application_period = _application_period(pairs)
    apply_start, apply_end = _date_pair(application_period)
    start, end = _date_pair(pairs["교육기간"])
    return {
        "provider": _provider(target),
        "provider_course_id": f"{_provider(target)}:jinju:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "program_type": f"교육·강좌 / {leaf.name}",
        "category": "교육·강좌",
        "branch": JINJU_MUNICIPALITY_NAME,
        "branch_code": _branch_code(JINJU_MUNICIPALITY_NAME),
        "branch_url": JINJU_CANONICAL_URL,
        "preserve_branch": True,
        "raw_url": raw_url,
        "application_url": "",
        "reservation_available": False,
        "status": _SOURCE_STATUS_MAP[source_status],
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": application_period,
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": _clean(pairs["요일시간"]),
        "target": _clean(pairs["신청대상"]),
        "fee": _clean(pairs["수강료"]),
        "capacity": _clean(pairs["정원/접수인원/대기자정원"]),
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "municipality_code": JINJU_MUNICIPALITY_CODE,
        "municipality_full_name": JINJU_MUNICIPALITY_NAME,
        "collection_type": "complete_fixed_fanout+empty_sentinel+current_detail",
        "description": _clean(
            " / ".join(
                (
                    pairs["교육구분"],
                    pairs["신청대상"],
                    pairs["요일시간"],
                )
            )
        ),
        "raw_fields": {
            "parser": JINJU_PARSER,
            "identity": identity,
            "leaf_code": leaf.code,
            "leaf_name": leaf.name,
            "leaf_path": leaf.path,
            "source_page": page,
            "source_status": source_status,
            "education_category": _clean(pairs["교육구분"]),
            "list_application_label": application_label,
            "list_application_period": application_period,
            "list_education_period": _clean(pairs["교육기간"]),
            "list_application_count": _clean(pairs["신청현황"]),
            "application_methods": dict(methods),
            "reversed_application_period": apply_end < apply_start,
            "reversed_education_period": end < start,
        },
    }


def _parse_list_page(
    target: Any,
    leaf: JinjuLeaf,
    soup: BeautifulSoup,
    *,
    page: int,
    source_url: str,
) -> tuple[list[dict[str, Any]], bool, int]:
    roots = soup.select(".cp31edu1list1 > ul")
    if not roots:
        return [], True, 0
    if len(roots) != 1:
        return [], False, 1
    cards = roots[0].find_all("li", recursive=False)
    rows: list[dict[str, Any]] = []
    malformed = 0
    for card in cards:
        title_links = card.select("a.tg1[href]")
        image_links = card.select("a.figs[href]")
        if not title_links and not image_links:
            if len(cards) == 1:
                return [], True, 0
            malformed += 1
            continue
        if len(title_links) != 1 or len(image_links) != 1:
            malformed += 1
            continue
        identity, raw_url = _safe_href(
            title_links[0].get("href"), leaf, source_url, "view"
        )
        image_identity, image_url = _safe_href(
            image_links[0].get("href"), leaf, source_url, "view"
        )
        title_node = title_links[0].select_one(".t1")
        status_nodes = title_links[0].select("em.g1")
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
        source_status = (
            _clean(status_nodes[0].get_text(" ", strip=True))
            if len(status_nodes) == 1
            else ""
        )
        pairs = _pairs(card)
        methods = _methods(card)
        application_label, application_period = (
            _application_period(pairs) if pairs else ("", "")
        )
        dates = _date_pair(pairs.get("교육기간")) if pairs else (None, None)
        apply_dates = _date_pair(application_period)
        if (
            not identity
            or identity != image_identity
            or raw_url != image_url
            or not title
            or source_status not in _SOURCE_STATUS_MAP
            or pairs is None
            or not _LIST_REQUIRED_FIELDS.issubset(pairs)
            or not application_label
            or methods is None
            or None in dates
            or None in apply_dates
        ):
            malformed += 1
            continue
        rows.append(
            _base_row(
                target,
                leaf,
                identity=identity,
                title=title,
                source_status=source_status,
                raw_url=raw_url,
                pairs=pairs,
                methods=methods,
                page=page,
            )
        )
    return rows, not rows and not malformed, malformed


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _normalized(row.get("title")),
            _clean(row.get("raw_fields", {}).get("source_status")),
            _clean(row.get("apply_start")),
            _clean(row.get("apply_end")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
        )
        for row in rows
    )


def _detail_field(root: Any, class_name: str, expected_label: str) -> tuple[str, bool]:
    aliases = {expected_label}
    if class_name == "receipt":
        aliases.add("신청기간")
    candidates: list[tuple[Any, str]] = []
    for node in root.select(f".cp31dlist2 li.di.{class_name}") if root else []:
        dt = node.select_one(".dt")
        label = _clean(dt.get_text(" ", strip=True)).rstrip(":").strip() if dt else ""
        if class_name == "selection":
            if any(label.startswith(alias) for alias in aliases):
                candidates.append((node, label))
        elif label in aliases:
            candidates.append((node, label))
    if len(candidates) != 1:
        return "", False
    node, label = candidates[0]
    if class_name == "selection":
        matched = next(alias for alias in aliases if label.startswith(alias))
        value = _clean(label[len(matched) :].lstrip(" :"))
        return value, bool(value)
    dd = node.select_one(".dd")
    value = _clean(dd.get_text(" ", strip=True)) if dd else ""
    return value, bool(value)


def _safe_facility(root: Any) -> tuple[str, str, bool]:
    panes = root.select("#tabs1pane4 .cp31dlist3") if root else []
    if len(panes) != 1:
        return "", "", False
    safe: dict[str, str] = {}
    for node in panes[0].select("li.di"):
        dt = node.select_one(".dt")
        dd = node.select_one(".dd")
        key = _clean(dt.get_text(" ", strip=True)).rstrip(":").strip() if dt else ""
        if key not in {"시설명", "주소"}:
            continue
        if not dd or key in safe:
            return "", "", False
        safe[key] = _clean(dd.get_text(" ", strip=True))
    return safe.get("시설명", ""), safe.get("주소", ""), set(safe) == {"시설명", "주소"}


def _enrich_detail(
    row: dict[str, Any], leaf: JinjuLeaf, soup: BeautifulSoup, cutoff: date
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    errors: list[str] = []
    roots = soup.select(".cp31edu1view1")
    root = roots[0] if len(roots) == 1 else None
    title_nodes = root.select(".hg1 h3.h1") if root else []
    status_nodes = root.select(".hg1 em.g1") if root else []
    detail_title = _clean(title_nodes[0].get_text(" ", strip=True)) if len(title_nodes) == 1 else ""
    detail_status = _clean(status_nodes[0].get_text(" ", strip=True)) if len(status_nodes) == 1 else ""
    if _normalized(detail_title) != _normalized(row.get("title")):
        errors.append(f"{identity}: detail/list title mismatch")
    if detail_status != _clean(row.get("raw_fields", {}).get("source_status")):
        errors.append(f"{identity}: detail/list status mismatch")

    values: dict[str, str] = {}
    for class_name, label in _DETAIL_FIELDS.items():
        value, valid = _detail_field(root, class_name, label)
        if not valid:
            errors.append(f"{identity}: missing or malformed detail {class_name}")
        values[class_name] = value
    detail_apply = _date_pair(values.get("receipt"))
    detail_period = _date_pair(values.get("period"))
    list_apply = (
        date.fromisoformat(_clean(row.get("apply_start"))),
        date.fromisoformat(_clean(row.get("apply_end"))),
    )
    list_period = (
        date.fromisoformat(_clean(row.get("start_date"))),
        date.fromisoformat(_clean(row.get("end_date"))),
    )
    if detail_apply != list_apply:
        errors.append(f"{identity}: detail/list application period mismatch")
    if detail_period != list_period:
        errors.append(f"{identity}: detail/list education period mismatch")
    if detail_apply[0] is None or detail_apply[1] is None:
        errors.append(f"{identity}: invalid detail application period")
    elif detail_apply[1] < detail_apply[0] and list_period[1] >= cutoff:
        errors.append(f"{identity}: current course has reversed application period")
    if detail_period[0] is None or detail_period[1] is None:
        errors.append(f"{identity}: invalid detail education period")
    elif detail_period[1] < detail_period[0] and max(detail_period) >= cutoff:
        errors.append(f"{identity}: current course has reversed education period")

    comparisons = {
        "edu": row.get("raw_fields", {}).get("education_category"),
        "target": row.get("target"),
        "dayhour": row.get("schedule_raw"),
        "quota": row.get("capacity"),
        "application": row.get("raw_fields", {}).get("list_application_count"),
        "tuition": row.get("fee"),
    }
    for key, expected in comparisons.items():
        if _normalized(values.get(key)) != _normalized(expected):
            errors.append(f"{identity}: detail/list {key} mismatch")

    detail_methods = _methods(root)
    list_methods = row.get("raw_fields", {}).get("application_methods")
    if detail_methods is None or detail_methods != list_methods:
        errors.append(f"{identity}: detail/list application methods mismatch")

    facility_full, facility_address, facility_valid = _safe_facility(soup)
    if not facility_valid or not facility_full or not facility_address:
        errors.append(f"{identity}: missing safe facility evidence")
    if _normalized(facility_full) != _normalized(values.get("facilities")):
        errors.append(f"{identity}: facility evidence mismatch")
    if "진주시" not in _clean(facility_address):
        errors.append(f"{identity}: facility address is outside Jinju")
    facility_name = _clean(re.split(r"\s*>\s*", facility_full)[-1])
    if not facility_name:
        errors.append(f"{identity}: empty facility branch")

    controls = soup.select("#body_content a#btn-reserve")
    application_url = ""
    application_label = ""
    if len(controls) > 1:
        errors.append(f"{identity}: duplicate application controls")
    elif len(controls) == 1:
        application_label = _clean(controls[0].get_text(" ", strip=True))
        control_identity, application_url = _safe_href(
            controls[0].get("href"), leaf, row.get("raw_url", ""), "ins"
        )
        if control_identity != identity or not application_url or not application_label:
            errors.append(f"{identity}: invalid application control")
    status = _clean(row.get("status"))
    internet_enabled = bool((detail_methods or {}).get("인터넷"))
    if application_url and (status not in {"OPEN", "WAITLIST"} or not internet_enabled):
        errors.append(f"{identity}: inactive course exposes an application control")
    if status in {"OPEN", "WAITLIST"} and internet_enabled and not application_url:
        errors.append(f"{identity}: internet-open course lacks an application control")

    if errors:
        return errors
    row["branch"] = facility_name
    row["branch_code"] = _branch_code(facility_full)
    row["venue_name"] = facility_name
    row["application_url"] = application_url
    row["reservation_available"] = bool(application_url)
    if application_url:
        row["application_type"] = (
            "WAITLIST_APPLY" if status == "WAITLIST" else "INTERNET_APPLY"
        )
    row["raw_fields"] = {
        **row["raw_fields"],
        "detail_status": detail_status,
        "curriculum": values["curriculum"],
        "facility_category": values["facilities"],
        "facility_name": facility_full,
        "facility_address": facility_address,
        "reception_place": values["reception"],
        "detail_application_period": values["receipt"],
        "detail_education_period": values["period"],
        "detail_application_methods": detail_methods,
        "application_control_present": bool(application_url),
        "application_control_label": application_label,
        "municipality_evidence": {
            "field": "시설 주소",
            "value": facility_address,
            "code": JINJU_MUNICIPALITY_CODE,
            "full_name": JINJU_MUNICIPALITY_NAME,
        },
    }
    return []


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _clean(row.get("period")),
        _normalized(row.get("schedule_raw")),
        _normalized(row.get("target")),
        _normalized(row.get("venue_name")),
    )


def _base_meta() -> dict[str, Any]:
    alias_counts = Counter(item.provider for item in JINJU_NON_EXECUTING_ALIASES)
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "leaf_count": len(JINJU_EDUCATION_LEAVES),
        "source_count": len(JINJU_EDUCATION_LEAVES),
        "declared_pages_by_leaf": {},
        "facility_codes_by_leaf": {},
        "page_counts": {},
        "source_counts": {},
        "source_total": 0,
        "source_rows": 0,
        "current_candidate_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "historical_reversed_application_count": 0,
        "historical_reversed_education_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "duplicate_count": 0,
        "duplicate_identity_count": 0,
        "duplicate_url_count": 0,
        "semantic_duplicate_count": 0,
        "source_status_counts": {},
        "current_source_status_counts": {},
        "branch_count": 0,
        "branch_counts": {},
        "municipality_counts": {},
        "application_open_count": 0,
        "reservation_discovery_links": 0,
        "sentinel_count": 0,
        "stable_recheck_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "recursion_depth": 0,
        "ownership_aliases": list(JINJU_OWNERSHIP_ALIAS_URLS),
        "excluded_toybank_urls": list(JINJU_EXCLUDED_TOYBANK_URLS),
        "duplicate_alias_provider_count": sum(
            count - 1 for count in alias_counts.values() if count > 1
        ),
        "configured_collection_error": "",
    }


def collect_jinju_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 400,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = JINJU_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Jinju internal-education snapshot."""

    meta = _base_meta()
    if not is_jinju_education_target(target):
        meta["configured_collection_error"] = (
            "target is not the canonical Jinju internal education provider route"
        )
        return [], JINJU_PARSER, meta
    if fetcher is None or session_factory is None:
        meta["configured_collection_error"] = (
            "managed fetcher and session_factory injection are required"
        )
        return [], JINJU_PARSER, meta
    if max_pages < len(JINJU_EDUCATION_LEAVES) or detail_limit < 0 or max_workers < 1:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "max_pages/detail_limit/max_workers are invalid for the fixed fan-out"
        )
        return [], JINJU_PARSER, meta

    cutoff = _today(today)
    errors: list[str] = []
    first_items = [((leaf.code, 1), leaf.url) for leaf in JINJU_EDUCATION_LEAVES]
    page_soups, fetch_errors = _parallel_fetch(
        first_items,
        fetcher=fetcher,
        session_factory=session_factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    declarations: dict[str, int] = {}
    facility_codes: dict[str, str] = {}
    first_rows: dict[str, list[dict[str, Any]]] = {}

    for leaf in JINJU_EDUCATION_LEAVES:
        soup = page_soups.get((leaf.code, 1))
        if soup is None:
            errors.append(f"{leaf.code}: missing first catalogue page")
            continue
        declared, active, facility_code, valid = _pagination_state(soup, leaf)
        parsed, no_data, malformed = _parse_list_page(
            target, leaf, soup, page=1, source_url=leaf.url
        )
        if not valid or active != 1:
            errors.append(f"{leaf.code}: first-page pagination contract mismatch")
        if no_data or not parsed:
            errors.append(f"{leaf.code}: authoritative archive first page is empty")
        if malformed:
            errors.append(f"{leaf.code}: first page has {malformed} malformed rows")
        declarations[leaf.code] = declared
        facility_codes[leaf.code] = facility_code
        first_rows[leaf.code] = parsed
        meta["declared_pages_by_leaf"][leaf.code] = declared
        meta["facility_codes_by_leaf"][leaf.code] = facility_code
        meta["pagination_detected"] = bool(meta["pagination_detected"] or declared > 1)

    if len(declarations) != len(JINJU_EDUCATION_LEAVES):
        errors.append("fixed 12-leaf fan-out discovery is incomplete")
    required = sum(last + 2 for last in declarations.values())
    meta["required_list_requests"] = required
    if required > max_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {max_pages} of {required} required list requests"
        )

    if not errors:
        remaining: list[tuple[Any, str]] = []
        for leaf in JINJU_EDUCATION_LEAVES:
            code = facility_codes[leaf.code]
            for page in range(2, declarations[leaf.code] + 1):
                remaining.append(
                    ((leaf.code, page), jinju_list_url(leaf, page, code))
                )
            sentinel_page = declarations[leaf.code] + 1
            remaining.append(
                (
                    (leaf.code, sentinel_page),
                    jinju_list_url(leaf, sentinel_page, code),
                )
            )
        fetched, more_errors = _parallel_fetch(
            remaining,
            fetcher=fetcher,
            session_factory=session_factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        page_soups.update(fetched)
        errors.extend(more_errors)

    all_rows: list[dict[str, Any]] = []
    first_signatures: dict[str, tuple[Any, ...]] = {}
    if not errors:
        for leaf in JINJU_EDUCATION_LEAVES:
            source_rows: list[dict[str, Any]] = []
            declared = declarations[leaf.code]
            code = facility_codes[leaf.code]
            for page in range(1, declared + 1):
                soup = page_soups.get((leaf.code, page))
                if soup is None:
                    errors.append(f"{leaf.code}: page {page} is missing")
                    continue
                source_url = leaf.url if page == 1 else jinju_list_url(leaf, page, code)
                parsed, no_data, malformed = _parse_list_page(
                    target, leaf, soup, page=page, source_url=source_url
                )
                observed_last, active, observed_code, valid = _pagination_state(soup, leaf)
                if (
                    not valid
                    or active != page
                    or observed_last != declared
                    or observed_code != code
                ):
                    errors.append(
                        f"{leaf.code} page {page}: pagination marker/declaration changed"
                    )
                if malformed:
                    errors.append(
                        f"{leaf.code} page {page}: {malformed} malformed catalogue rows"
                    )
                if no_data or not parsed:
                    errors.append(f"{leaf.code} page {page}: unexpected empty data page")
                if page < declared and len(parsed) != JINJU_PAGE_SIZE:
                    errors.append(
                        f"{leaf.code} page {page}: expected {JINJU_PAGE_SIZE} rows before final page"
                    )
                if page == declared and not 1 <= len(parsed) <= JINJU_PAGE_SIZE:
                    errors.append(f"{leaf.code}: invalid final-page row count")
                meta["page_counts"][f"{leaf.code}:{page}"] = len(parsed)
                source_rows.extend(parsed)
                if page == 1:
                    first_signatures[leaf.code] = _page_signature(parsed)

            sentinel_page = declared + 1
            sentinel = page_soups.get((leaf.code, sentinel_page))
            if sentinel is None:
                errors.append(f"{leaf.code}: missing empty sentinel page")
            else:
                sentinel_url = jinju_list_url(leaf, sentinel_page, code)
                parsed, _no_data, malformed = _parse_list_page(
                    target,
                    leaf,
                    sentinel,
                    page=sentinel_page,
                    source_url=sentinel_url,
                )
                if parsed or malformed or sentinel.select(".pagination .m.on"):
                    errors.append(f"{leaf.code}: sentinel page is not empty")
                else:
                    meta["sentinel_count"] += 1
            meta["source_counts"][leaf.code] = len(source_rows)
            all_rows.extend(source_rows)

    recheck_soups: dict[Any, BeautifulSoup] = {}
    if not errors:
        recheck_items = [
            ((leaf.code, "recheck"), leaf.url) for leaf in JINJU_EDUCATION_LEAVES
        ]
        recheck_soups, recheck_errors = _parallel_fetch(
            recheck_items,
            fetcher=fetcher,
            session_factory=session_factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        errors.extend(recheck_errors)
        for leaf in JINJU_EDUCATION_LEAVES:
            soup = recheck_soups.get((leaf.code, "recheck"))
            if soup is None:
                errors.append(f"{leaf.code}: missing stable first-page recheck")
                continue
            declared, active, code, valid = _pagination_state(soup, leaf)
            parsed, no_data, malformed = _parse_list_page(
                target, leaf, soup, page=1, source_url=leaf.url
            )
            if (
                not valid
                or active != 1
                or declared != declarations[leaf.code]
                or code != facility_codes[leaf.code]
                or no_data
                or malformed
                or _page_signature(parsed) != first_signatures[leaf.code]
            ):
                errors.append(f"{leaf.code}: first page changed during traversal")
            else:
                meta["stable_recheck_count"] += 1

    meta["list_requests"] = meta["pages"] = len(page_soups) + len(recheck_soups)
    meta["source_total"] = meta["source_rows"] = len(all_rows)
    source_statuses = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in all_rows
    )
    meta["source_status_counts"] = dict(sorted(source_statuses.items()))
    identities = [_clean(row.get("raw_fields", {}).get("identity")) for row in all_rows]
    course_ids = [_clean(row.get("provider_course_id")) for row in all_rows]
    raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
    meta["duplicate_identity_count"] = len(identities) - len(set(identities))
    meta["duplicate_count"] = len(course_ids) - len(set(course_ids))
    meta["duplicate_url_count"] = len(raw_urls) - len(set(raw_urls))
    if meta["duplicate_identity_count"]:
        errors.append(f"{meta['duplicate_identity_count']} duplicate source identities")
    if meta["duplicate_count"]:
        errors.append(f"{meta['duplicate_count']} duplicate provider course ids")
    if meta["duplicate_url_count"]:
        errors.append(f"{meta['duplicate_url_count']} duplicate canonical detail URLs")

    current_rows: list[dict[str, Any]] = []
    for row in all_rows:
        end = date.fromisoformat(_clean(row.get("end_date")))
        current = end >= cutoff
        reversed_apply = bool(row.get("raw_fields", {}).get("reversed_application_period"))
        reversed_education = bool(row.get("raw_fields", {}).get("reversed_education_period"))
        if current and reversed_apply:
            errors.append(
                f"{row['raw_fields']['identity']}: current course has reversed application period"
            )
        if current and reversed_education:
            errors.append(
                f"{row['raw_fields']['identity']}: current course has reversed education period"
            )
        if current:
            current_rows.append(row)
        else:
            if reversed_apply:
                meta["historical_reversed_application_count"] += 1
            if reversed_education:
                meta["historical_reversed_education_count"] += 1
    meta["current_candidate_count"] = len(current_rows)
    meta["expired_count"] = len(all_rows) - len(current_rows)
    current_statuses = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in current_rows
    )
    meta["current_source_status_counts"] = dict(sorted(current_statuses.items()))

    if detail_limit < len(current_rows):
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit allows {detail_limit} of {len(current_rows)} required details"
        )

    detail_errors: list[str] = []
    if not errors and current_rows:
        detail_items = [
            (
                _clean(row.get("raw_fields", {}).get("identity")),
                _clean(row.get("raw_url")),
            )
            for row in current_rows
        ]
        detail_soups, detail_fetch_errors = _parallel_fetch(
            detail_items,
            fetcher=fetcher,
            session_factory=session_factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        meta["detail_attempts"] = len(detail_items)
        for row in current_rows:
            identity = _clean(row.get("raw_fields", {}).get("identity"))
            soup = detail_soups.get(identity)
            if soup is None:
                continue
            leaf = JINJU_LEAF_BY_CODE[_clean(row.get("raw_fields", {}).get("leaf_code"))]
            item_errors = _enrich_detail(row, leaf, soup, cutoff)
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                meta["detail_pages"] += 1
    meta["detail_errors"] = len(detail_errors)
    errors.extend(detail_errors)

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
        try:
            deduped = list(dedupe_rows(current_rows))
        except Exception as exc:
            errors.append(f"dedupe failed ({type(exc).__name__})")
            deduped = []
        if len(deduped) != len(current_rows):
            errors.append(
                f"dedupe changed complete row count {len(current_rows)} to {len(deduped)}"
            )
        else:
            current_rows = deduped

    meta["pagination_complete"] = (
        not meta["source_cap_reached"]
        and meta["list_requests"] == meta["required_list_requests"]
        and meta["sentinel_count"] == len(JINJU_EDUCATION_LEAVES)
        and meta["stable_recheck_count"] == len(JINJU_EDUCATION_LEAVES)
        and not any(
            token in error
            for error in errors
            for token in ("page", "pagination", "sentinel", "fan-out", "catalogue")
        )
    )
    meta["details_complete"] = (
        meta["detail_attempts"] == len(current_rows)
        and meta["detail_pages"] == len(current_rows)
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
    if meta["snapshot_complete"]:
        meta["current_count"] = meta["returned_count"] = len(current_rows)
        branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        meta["branch_count"] = len(branch_counts)
        meta["branch_counts"] = dict(sorted(branch_counts.items()))
        meta["municipality_counts"] = {JINJU_MUNICIPALITY_NAME: len(current_rows)}
        meta["application_open_count"] = sum(
            bool(row.get("application_url")) for row in current_rows
        )
        meta["reservation_discovery_links"] = meta["application_open_count"]
    else:
        current_rows = []
    meta["no_current_data"] = meta["snapshot_complete"] and not current_rows
    if meta["no_current_data"]:
        meta["no_current_reason"] = (
            "the complete official Jinju 12-leaf internal education archive has no current/future courses"
        )
    meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
    return current_rows, JINJU_PARSER, meta


collect = collect_jinju_education_courses


__all__ = [
    "JINJU_CANDIDATE_ID",
    "JINJU_CANONICAL_URL",
    "JINJU_EDUCATION_LEAVES",
    "JINJU_EXCLUDED_TOYBANK_URLS",
    "JINJU_HOST",
    "JINJU_MAX_WORKERS",
    "JINJU_MUNICIPALITY_CODE",
    "JINJU_MUNICIPALITY_NAME",
    "JINJU_NON_EXECUTING_ALIASES",
    "JINJU_OWNERSHIP_ALIAS_URLS",
    "JINJU_PAGE_SIZE",
    "JINJU_PARSER",
    "JINJU_PROVIDER",
    "JinjuAlias",
    "JinjuLeaf",
    "collect",
    "collect_jinju_education_courses",
    "is_jinju_education_target",
    "is_target",
    "jinju_application_url",
    "jinju_detail_url",
    "jinju_list_url",
]
