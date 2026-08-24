"""Fail-closed collector for Jeonju Lifelong Learning Center courses.

The public site exposes seven sibling catalogues (``Program21`` through
``Program27``).  Search results have historically pointed at one catalogue or
at a single ``do=sinform`` application page, neither of which represents a
complete snapshot.  This collector always fans out over the seven official
catalogues, verifies each declared total and final page, requests the immediate
out-of-range empty sentinel, and validates every list identity against its
detail page before filtering by education end date.

The detail page may contain instructor and contact information.  Those values,
the free-form detail body, and attachment names are intentionally neither
returned nor copied into ``raw_fields``.  Only an explicit, course-bound
``do=sinform`` control is accepted as an application URL.  When authentication
suppresses that control, an exact public login gate plus the official online
application method can authorize the already-validated detail URL as the
login-required entry point.

This module intentionally does not import ``Crawler_MunicipalYaml``.  The
shared router injects its managed fetcher/session factory and deduper, avoiding
an import cycle and preserving the common transport policy.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Comment, NavigableString


JEONJU_PROVIDER = "MUNI_E_JEONJU_GO_KR_00EEA994"
JEONJU_CANONICAL_CANDIDATE_ID = "MUNI_IR_4AB296DC50C6"
JEONJU_CANONICAL_URL = "https://e.jeonju.go.kr/main/menu?gc=Program27"
JEONJU_HOST = "e.jeonju.go.kr"
JEONJU_PATH = "/main/menu"
JEONJU_PAGE_SIZE = 12
JEONJU_SESSION_REQUEST_LIMIT = 40
JEONJU_BRANCH = "전주시평생학습관"
JEONJU_MUNICIPALITY_CODE = "5211000000"
JEONJU_WANSAN_CODE = "5211100000"
JEONJU_DEOKJIN_CODE = "5211300000"
JEONJU_MUNICIPALITY_NAMES: dict[str, str] = {
    JEONJU_MUNICIPALITY_CODE: "전북특별자치도 전주시",
    JEONJU_WANSAN_CODE: "전북특별자치도 전주시 완산구",
    JEONJU_DEOKJIN_CODE: "전북특별자치도 전주시 덕진구",
}
JEONJU_PARSER = (
    "jeonju_fixed_program21_27_fanout+declared_totals+empty_sentinels+"
    "all_details+education_end_filter"
)


@dataclass(frozen=True)
class JeonjuCatalogue:
    gc: str
    name: str


JEONJU_CATALOGUES: tuple[JeonjuCatalogue, ...] = (
    JeonjuCatalogue("Program21", "시민강좌"),
    JeonjuCatalogue("Program22", "쌈지교실"),
    JeonjuCatalogue("Program23", "인문학"),
    JeonjuCatalogue("Program24", "50+ 플랫폼"),
    JeonjuCatalogue("Program25", "모두배움터"),
    JeonjuCatalogue("Program26", "기타"),
    JeonjuCatalogue("Program27", "열린시민강좌"),
)
JEONJU_CATALOGUE_BY_GC = {item.gc: item for item in JEONJU_CATALOGUES}

# These are ownership aliases only.  Production should execute the canonical
# provider once; individual catalogue/application targets must be superseded.
JEONJU_OWNERSHIP_ALIAS_URLS: tuple[str, ...] = tuple(
    f"https://{JEONJU_HOST}{JEONJU_PATH}?gc={item.gc}"
    for item in JEONJU_CATALOGUES
    if item.gc != "Program27"
) + (
    "https://e.jeonju.go.kr/main/menu?gc=Program21&do=sinform&program_id=VEf19BwyjPhb&page=2&psin_id=VEf19BxOWb9m",
    "https://e.jeonju.go.kr/main/menu?gc=Program21&do=sinform&program_id=61dvCEeyhOX68a3fb38&page=2&psin_id=DX7C4wqjI4L68a3fb38",
    "https://e.jeonju.go.kr/main/menu?gc=Program27&do=sinform&program_id=v2DydWQ9hoz68f095ee&psin_id=jYnehySDQOI68f095ee",
)
JEONJU_ALIAS_PROVIDERS: tuple[str, ...] = (
    "MUNI_E_JEONJU_GO_KR_0E0825AA",
    "MUNI_E_JEONJU_GO_KR_C0BC5586",
    "MUNI_E_JEONJU_GO_KR_ED75116F",
    "MUNI_E_JEONJU_GO_KR_91CFC934",
)
JEONJU_EXCLUDED_NON_COURSE_URLS: tuple[str, ...] = (
    "https://e.jeonju.go.kr/",
    "https://e.jeonju.go.kr/main/menu?gc=NOTICE",
    "https://e.jeonju.go.kr/main/menu?gc=869USCX&sca=%ED%9D%AC%EB%A7%9D%ED%95%99%EA%B5%90",
)


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


_SPACE_RE = re.compile(r"\s+")
_PROGRAM_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,80}")
_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2}|\d{2})\s*[./-]\s*"
    r"(?P<month>\d{1,2})\s*[./-]\s*(?P<day>\d{1,2})(?!\d)"
)
_APPLICATION_LABELS = frozenset(
    {"신청하기", "접수하기", "온라인신청", "수강신청", "대기자접수", "대기자신청"}
)
_OPEN_SOURCE_STATUSES = frozenset({"신청하기", "접수중", "신청가능"})
_WAIT_SOURCE_STATUSES = frozenset({"대기자접수", "대기자신청"})
_SCHEDULED_SOURCE_STATUSES = frozenset({"접수예정", "신청예정"})
_CLOSED_SOURCE_STATUSES = frozenset(
    {"접수마감", "정원마감", "일부정원마감", "종료", "접수종료", "신청마감"}
)
_DETAIL_REQUIRED_KEYS = frozenset(
    {
        "대상",
        "진행기간",
        "신청기간",
        "수강료",
        "정원",
        "문의",
        "신청방법",
        "신청가능",
    }
)


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


def _single_query(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _exact_catalogue_url(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == JEONJU_HOST
        and parsed.port is None
        and parsed.path == JEONJU_PATH
        and set(query) == {"gc"}
        and _single_query(query, "gc") in JEONJU_CATALOGUE_BY_GC
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_jeonju_education_target(target: Any) -> bool:
    return _provider(target) == JEONJU_PROVIDER and _exact_catalogue_url(
        _target_url(target)
    )


def is_jeonju_ownership_alias_target(target: Any) -> bool:
    return _target_url(target) in JEONJU_OWNERSHIP_ALIAS_URLS


def is_jeonju_excluded_non_course_target(target: Any) -> bool:
    return _target_url(target) in JEONJU_EXCLUDED_NON_COURSE_URLS


is_target = is_jeonju_education_target


def jeonju_list_url(catalogue: JeonjuCatalogue, page: Any = 1) -> str:
    raw_page = _clean(page)
    if catalogue not in JEONJU_CATALOGUES or not raw_page.isdigit():
        return ""
    page_number = int(raw_page)
    if page_number < 1:
        return ""
    query: list[tuple[str, str]] = [("gc", catalogue.gc)]
    if page_number > 1:
        query.extend((('do', 'list'), ('page', str(page_number))))
    return f"https://{JEONJU_HOST}{JEONJU_PATH}?{urlencode(query)}"


def jeonju_detail_url(
    catalogue: JeonjuCatalogue,
    program_id: Any,
    page: Any = 1,
) -> str:
    identity = _clean(program_id)
    raw_page = _clean(page)
    if (
        catalogue not in JEONJU_CATALOGUES
        or not _PROGRAM_ID_RE.fullmatch(identity)
        or not raw_page.isdigit()
        or int(raw_page) < 1
    ):
        return ""
    query: list[tuple[str, str]] = [("gc", catalogue.gc), ("do", "view")]
    if int(raw_page) > 1:
        # The site's controller requires the originating page for rows beyond
        # page one; omitting it redirects to the application route.
        query.append(("page", str(int(raw_page))))
    query.append(("program_id", identity))
    return f"https://{JEONJU_HOST}{JEONJU_PATH}?{urlencode(query)}"


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
    fetcher: Fetcher,
    current_session: Any,
    url: str,
    timeout: int,
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


def _date_values(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        raw_year = int(match.group("year"))
        year = 2000 + raw_year if raw_year < 100 else raw_year
        parsed = date(year, int(match.group("month")), int(match.group("day")))
        result.append(parsed)
    return result


def _date_range(value: Any) -> tuple[date, date]:
    values = _date_values(value)
    if not values:
        raise ValueError("date range has no date")
    start = values[0]
    end = values[-1]
    if end < start:
        raise ValueError("date range is reversed")
    return start, end


def _capacity(value: Any) -> int:
    match = re.search(r"(?<!\d)(\d{1,6})(?!\d)", _clean(value).replace(",", ""))
    if not match:
        raise ValueError("capacity has no integer")
    return int(match.group(1))


def _source_status(value: Any) -> str:
    source = _clean(value)
    if source in _OPEN_SOURCE_STATUSES:
        return "OPEN"
    if source in _WAIT_SOURCE_STATUSES:
        return "WAITING"
    if source in _SCHEDULED_SOURCE_STATUSES:
        return "SCHEDULED"
    if source in _CLOSED_SOURCE_STATUSES:
        return "CLOSED"
    return ""


def _page_contract(soup: BeautifulSoup) -> tuple[int, int, int] | None:
    totals = soup.select(".ginfo_box .ginfo > span, .ginfo > span")
    if len(totals) != 1:
        return None
    total_text = _clean(totals[0].get_text(" ", strip=True)).replace(",", "")
    if not total_text.isdigit():
        return None
    total = int(total_text)
    last = max(1, math.ceil(total / JEONJU_PAGE_SIZE))
    page_box = soup.select_one(".page_box")
    if last == 1:
        if page_box and any(
            _clean(node.get_text(" ", strip=True)).isdigit()
            for node in page_box.select("a")
        ):
            return None
        return total, last, 1
    if page_box is None:
        return None
    numeric = {
        int(text)
        for text in (
            _clean(node.get_text(" ", strip=True)) for node in page_box.select("a")
        )
        if text.isdigit()
    }
    last_links = page_box.select("a.parrow04[href]")
    if len(last_links) != 1:
        return None
    last_query = parse_qs(
        urlparse(_clean(last_links[0].get("href"))).query,
        keep_blank_values=True,
    )
    advertised_last = _single_query(last_query, "page")
    active_nodes = page_box.select("a.on")
    if len(active_nodes) != 1:
        return None
    active_text = _clean(active_nodes[0].get_text(" ", strip=True))
    if (
        not active_text.isdigit()
        or int(active_text) not in numeric
        or not advertised_last.isdigit()
        or int(advertised_last) != last
        or any(value < 1 or value > last for value in numeric)
    ):
        return None
    return total, last, int(active_text)


def _empty_sentinel(soup: BeautifulSoup) -> bool:
    return not (
        soup.select("ul.class_list_wrap > li")
        or soup.select(".ginfo_box .ginfo > span, .ginfo > span")
        or soup.select(".page_box")
    )


def _card_title(card: Any) -> tuple[str, str, str]:
    node = card.select_one(".tit")
    if node is None:
        return "", "", ""
    category_node = node.select_one(".cate")
    category = _clean(category_node.get_text(" ", strip=True) if category_node else "")
    category = category.strip("[]")
    fee_node = node.select_one("[class*='program_ptype']")
    fee_type = _clean(fee_node.get_text(" ", strip=True) if fee_node else "")
    direct = _clean(
        " ".join(
            str(child)
            for child in node.children
            if isinstance(child, NavigableString) and not isinstance(child, Comment)
        )
    )
    if direct:
        # One archived title is double-escaped by the official template
        # (``&amp;amp;`` in markup, ``&amp;`` after HTML parsing).
        return _clean(html.unescape(direct)), category, fee_type
    clone = BeautifulSoup(str(node), "lxml")
    for child in clone.select(".cate, [class*='program_ptype'], [class*='capacity']"):
        child.extract()
    return _clean(html.unescape(clone.get_text(" ", strip=True))), category, fee_type


def _card_pairs(card: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for node in card.select(".txt p"):
        text = _clean(node.get_text(" ", strip=True))
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        key = _clean(key)
        if key:
            pairs[key] = _clean(value)
    return pairs


def _safe_detail_href(
    value: Any,
    catalogue: JeonjuCatalogue,
    base_url: str,
) -> tuple[str, str]:
    parsed = urlparse(urljoin(base_url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query(query, "program_id")
    expected = {"gc", "do", "program_id"}
    page = _single_query(query, "page")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != JEONJU_HOST
        or parsed.port is not None
        or parsed.path != JEONJU_PATH
        or frozenset(query) not in {
            frozenset(expected),
            frozenset(expected | {"page"}),
        }
        or _single_query(query, "gc") != catalogue.gc
        or _single_query(query, "do") != "view"
        or not _PROGRAM_ID_RE.fullmatch(identity)
        or ("page" in query and (not page.isdigit() or int(page) < 1))
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return "", ""
    return jeonju_detail_url(catalogue, identity, page or 1), identity


def _parse_list_page(
    target: Any,
    catalogue: JeonjuCatalogue,
    soup: BeautifulSoup,
    *,
    page: int,
    source_url: str,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    provider = _provider(target)
    for card in soup.select("ul.class_list_wrap > li"):
        link = card.select_one("a.Fix_ListBtns[href]")
        raw_url, identity = _safe_detail_href(
            link.get("href") if link else "", catalogue, source_url
        )
        title, category, fee_type = _card_title(card)
        pairs = _card_pairs(card)
        status_node = card.select_one(".btn span")
        status_text = _clean(
            status_node.get_text(" ", strip=True) if status_node else ""
        )
        status = _source_status(status_text)
        required = {"진행기간", "신청기간", "대상", "정원", "신청가능"}
        if (
            not raw_url
            or not identity
            or not title
            or not fee_type
            or not status
            or not required.issubset(pairs)
        ):
            malformed += 1
            continue
        try:
            start, end = _date_range(pairs["진행기간"])
            apply_start, apply_end = _date_range(pairs["신청기간"])
            capacity_total = _capacity(pairs["정원"])
        except (TypeError, ValueError):
            malformed += 1
            continue
        row = {
            "provider": provider,
            "provider_course_id": f"{provider}:program:{identity}"[:100],
            "title": title,
            "branch": JEONJU_BRANCH,
            "branch_code": _branch_code(JEONJU_BRANCH),
            "preserve_branch": True,
            "branch_url": JEONJU_CANONICAL_URL,
            "category": category or catalogue.name,
            "category_raw": category,
            "raw_url": raw_url,
            "application_url": "",
            "reservation_available": False,
            "application_type": "INFO_ONLY",
            "status": status,
            "period": pairs["진행기간"],
            "apply_period": pairs["신청기간"],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_start": apply_start.isoformat(),
            "apply_end": apply_end.isoformat(),
            "schedule_raw": "",
            "target": pairs["대상"],
            "capacity": pairs["정원"],
            "capacity_total": capacity_total,
            "fee": fee_type,
            "application_method_raw": "",
            "program_type": "강좌",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "source_group": "municipal_reservation",
            "operator_type": "지자체/공공기관",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "raw_fields": {
                "parser": JEONJU_PARSER,
                "source_gc": catalogue.gc,
                "source_name": catalogue.name,
                "program_id": identity,
                "list_page": page,
                "source_url": source_url,
                "list_status": status_text,
                "list_category": category,
                "list_fee_type": fee_type,
            },
        }
        rows.append(row)
    return rows, malformed


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for node in soup.select(".class_view_wrap .cont > dl"):
        key_node = node.find("dt")
        value_node = node.find("dd")
        key = _clean(key_node.get_text(" ", strip=True) if key_node else "")
        value = _clean(value_node.get_text(" ", strip=True) if value_node else "")
        if key:
            pairs[key] = value
    return pairs


def _application_url(
    candidate: Any,
    catalogue: JeonjuCatalogue,
    identity: str,
    base_url: str,
) -> str:
    parsed = urlparse(urljoin(base_url, _clean(candidate)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    required = {"gc", "do", "program_id", "psin_id"}
    allowed = required | {"page"}
    psin_id = _single_query(query, "psin_id")
    page = _single_query(query, "page")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != JEONJU_HOST
        or parsed.port is not None
        or parsed.path != JEONJU_PATH
        or not required.issubset(query)
        or not set(query).issubset(allowed)
        or _single_query(query, "gc") != catalogue.gc
        or _single_query(query, "do") != "sinform"
        or _single_query(query, "program_id") != identity
        or not _PROGRAM_ID_RE.fullmatch(psin_id)
        or ("page" in query and (not page.isdigit() or int(page) < 1))
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return ""
    values: list[tuple[str, str]] = [
        ("gc", catalogue.gc),
        ("do", "sinform"),
        ("program_id", identity),
    ]
    if page:
        values.append(("page", page))
    values.append(("psin_id", psin_id))
    return f"https://{JEONJU_HOST}{JEONJU_PATH}?{urlencode(values)}"


def _application_controls(
    soup: BeautifulSoup,
    catalogue: JeonjuCatalogue,
    identity: str,
    detail_url: str,
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for node in soup.select("a[href], button[formaction]"):
        label = _clean(node.get_text(" ", strip=True))
        if label not in _APPLICATION_LABELS:
            continue
        candidate = node.get("href") or node.get("formaction")
        safe = _application_url(candidate, catalogue, identity, detail_url)
        if safe:
            found.append((label, safe))
    for node in soup.select("[onclick]"):
        label = _clean(node.get_text(" ", strip=True))
        if label not in _APPLICATION_LABELS:
            continue
        onclick = _clean(node.get("onclick"))
        for match in re.finditer(r"['\"]([^'\"]*do=sinform[^'\"]*)['\"]", onclick):
            safe = _application_url(match.group(1), catalogue, identity, detail_url)
            if safe:
                found.append((label, safe))
    for form in soup.select("form[action]"):
        submit_labels = {
            _clean(node.get("value") or node.get_text(" ", strip=True))
            for node in form.select("button, input[type='submit']")
        }
        labels = sorted(submit_labels & _APPLICATION_LABELS)
        if not labels:
            continue
        action = urljoin(detail_url, _clean(form.get("action")))
        parsed = urlparse(action)
        values = parse_qs(parsed.query, keep_blank_values=True)
        for name in ("gc", "do", "program_id", "page", "psin_id"):
            controls = form.select(f"input[name='{name}']")
            if len(controls) == 1:
                values[name] = [_clean(controls[0].get("value"))]
        candidate = parsed._replace(query=urlencode(values, doseq=True)).geturl()
        safe = _application_url(candidate, catalogue, identity, detail_url)
        if safe:
            found.append((labels[0], safe))
    unique: dict[str, str] = {}
    for label, url in found:
        unique.setdefault(url, label)
    return [(label, url) for url, label in unique.items()]


def _public_login_gate(soup: BeautifulSoup) -> bool:
    nodes = [
        node
        for node in soup.select("a[href]")
        if _clean(node.get_text(" ", strip=True)) == "로그인"
    ]
    if not nodes:
        return False
    for node in nodes:
        parsed = urlparse(urljoin(JEONJU_CANONICAL_URL, _clean(node.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if not (
            parsed.scheme.lower() == "https"
            and (parsed.hostname or "").rstrip(".").lower() == JEONJU_HOST
            and parsed.port is None
            and parsed.path == JEONJU_PATH
            and query == {"gc": ["LOGIN"]}
            and not parsed.params
            and not parsed.fragment
            and not parsed.username
            and not parsed.password
        ):
            return False
    return True


def _municipality(address: Any) -> tuple[str, str, str]:
    value = _clean(address)
    if "완산구" in value:
        code = JEONJU_WANSAN_CODE
        evidence = "detail_venue_address"
    elif "덕진구" in value:
        code = JEONJU_DEOKJIN_CODE
        evidence = "detail_venue_address"
    elif "전주시" in value:
        code = JEONJU_MUNICIPALITY_CODE
        evidence = "detail_venue_address"
    elif not value:
        code = JEONJU_MUNICIPALITY_CODE
        evidence = "official_operator_no_detail_venue"
    else:
        return "", "", ""
    return code, JEONJU_MUNICIPALITY_NAMES[code], evidence


def _branch_code(branch: Any) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"{JEONJU_PROVIDER}:BRANCH:{digest}"[:100]


def _fee_matches(list_fee_type: Any, detail_fee: Any) -> bool:
    list_value = _clean(list_fee_type)
    detail_value = _clean(detail_fee).replace(",", "")
    if list_value == "무료":
        return "무료" in detail_value or detail_value in {"0", "0원"}
    if list_value == "유료":
        amount = re.search(r"(?<!\d)(\d+)(?!\d)", detail_value)
        return "무료" not in detail_value and bool(amount and int(amount.group(1)) > 0)
    return False


def _enrich_detail(
    row: dict[str, Any],
    catalogue: JeonjuCatalogue,
    soup: BeautifulSoup,
    reference_day: date,
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("program_id"))
    errors: list[str] = []
    title_nodes = soup.select(".class_view_wrap .inner > p.tit")
    if len(title_nodes) != 1:
        return [f"{catalogue.gc}/{identity}: expected one detail title"]
    title_node = title_nodes[0]
    strong = title_node.select_one("strong")
    detail_title = _clean(strong.get_text(" ", strip=True) if strong else "")
    status_node = title_node.select_one("span[class*='state']")
    detail_source_status = _clean(
        status_node.get_text(" ", strip=True) if status_node else ""
    )
    detail_status = _source_status(detail_source_status)
    if _normalized(detail_title) != _normalized(row.get("title")):
        errors.append(f"{catalogue.gc}/{identity}: detail/list title mismatch")
    if not detail_status or detail_status != row.get("status"):
        errors.append(f"{catalogue.gc}/{identity}: detail/list status mismatch")

    pairs = _detail_pairs(soup)
    missing = sorted(_DETAIL_REQUIRED_KEYS - set(pairs))
    if missing:
        errors.append(
            f"{catalogue.gc}/{identity}: missing required detail fields {','.join(missing)}"
        )
        return errors
    try:
        detail_start, detail_end = _date_range(pairs["진행기간"])
        apply_start, apply_end = _date_range(pairs["신청기간"])
        capacity_total = _capacity(pairs["정원"])
    except (TypeError, ValueError):
        errors.append(f"{catalogue.gc}/{identity}: invalid detail date/capacity")
        return errors
    if (detail_start.isoformat(), detail_end.isoformat()) != (
        row.get("start_date"), row.get("end_date")
    ):
        errors.append(f"{catalogue.gc}/{identity}: detail/list education period mismatch")
    if (apply_start.isoformat(), apply_end.isoformat()) != (
        row.get("apply_start"), row.get("apply_end")
    ):
        errors.append(f"{catalogue.gc}/{identity}: detail/list application period mismatch")
    if capacity_total != row.get("capacity_total"):
        errors.append(f"{catalogue.gc}/{identity}: detail/list capacity mismatch")
    if _normalized(pairs.get("대상")) != _normalized(row.get("target")):
        errors.append(f"{catalogue.gc}/{identity}: detail/list target mismatch")
    list_category = _clean(row.get("raw_fields", {}).get("list_category"))
    detail_category = _clean(pairs.get("강좌분류"))
    if bool(list_category) != bool(detail_category) or (
        list_category and _normalized(list_category) != _normalized(detail_category)
    ):
        errors.append(f"{catalogue.gc}/{identity}: detail/list category mismatch")
    if not _fee_matches(row.get("raw_fields", {}).get("list_fee_type"), pairs["수강료"]):
        errors.append(f"{catalogue.gc}/{identity}: detail/list fee mismatch")

    address = _clean(pairs.get("교육장 주소"))
    if not address and catalogue.gc != "Program27":
        errors.append(f"{catalogue.gc}/{identity}: detail venue address is missing")
    municipality_code, municipality_name, evidence_field = _municipality(address)
    if not municipality_code:
        errors.append(f"{catalogue.gc}/{identity}: venue municipality is unsupported")

    controls = _application_controls(
        soup, catalogue, identity, _clean(row.get("raw_url"))
    )
    status = _clean(row.get("status"))
    application_method = _clean(pairs.get("신청방법"))
    auth_suppressed_control = bool(
        status in {"OPEN", "WAITING"}
        and not controls
        and "온라인" in application_method
        and _public_login_gate(soup)
    )
    if status in {"OPEN", "WAITING"} and not controls and not auth_suppressed_control:
        errors.append(f"{catalogue.gc}/{identity}: active source has no application control")
    if status in {"CLOSED", "SCHEDULED"} and controls:
        errors.append(f"{catalogue.gc}/{identity}: inactive source exposes application control")
    if status in {"OPEN", "WAITING"}:
        if not (apply_start <= reference_day <= apply_end):
            errors.append(f"{catalogue.gc}/{identity}: active status is outside application period")
        if detail_end < reference_day:
            errors.append(f"{catalogue.gc}/{identity}: active status belongs to expired education")
    elif status == "SCHEDULED" and apply_start <= reference_day:
        errors.append(f"{catalogue.gc}/{identity}: scheduled status has already reached application period")

    application_url = (
        controls[0][1]
        if controls
        else _clean(row.get("raw_url"))
        if auth_suppressed_control
        else ""
    )
    application_label = (
        controls[0][0]
        if controls
        else "로그인 후 신청"
        if auth_suppressed_control
        else ""
    )
    schedule = _clean(
        " ".join(
            value
            for value in (pairs.get("강의일시"), pairs.get("강의기간"))
            if _clean(value)
        )
    )
    row.update(
        {
            "status": status,
            "application_url": application_url,
            "reservation_available": bool(
                application_url and status in {"OPEN", "WAITING"}
            ),
            "application_type": (
                "ONLINE_RESERVATION_LOGIN_REQUIRED"
                if auth_suppressed_control
                else "WAITLIST_APPLY"
                if application_url and status == "WAITING"
                else "ONLINE_RESERVATION"
                if application_url
                else "INFO_ONLY"
            ),
            "application_method_raw": application_method,
            "period": pairs["진행기간"],
            "apply_period": pairs["신청기간"],
            "schedule_raw": schedule or "시간 별도 안내",
            "target": pairs["대상"],
            "capacity": pairs["정원"],
            "capacity_total": capacity_total,
            "fee": pairs["수강료"],
            "municipality_code": municipality_code,
            "municipality_full_name": municipality_name,
            "venue_name": JEONJU_BRANCH if address else "",
            "venue_address": address,
            "address": address,
            "description": _clean(row.get("title")),
        }
    )
    row["raw_fields"] = {
        **row.get("raw_fields", {}),
        "detail_verified": not errors,
        "detail_status": detail_source_status,
        "detail_has_category": bool(detail_category),
        "detail_has_venue_address": bool(address),
        "municipality_evidence": {
            "field": evidence_field,
            "value": address or JEONJU_MUNICIPALITY_NAMES[JEONJU_MUNICIPALITY_CODE],
            "code": municipality_code,
            "full_name": municipality_name,
        },
        "application_control_present": bool(controls),
        "application_control_count": len(controls),
        "application_control_label": application_label,
        "application_control_contract": (
            "public_course_bound_control"
            if controls
            else "auth_suppressed_public_detail_entry"
            if auth_suppressed_control
            else "inactive_no_control"
        ),
        "application_login_gate_verified": auth_suppressed_control,
        "schedule_contract": (
            "official_detail_fields"
            if schedule
            else "official_detail_omits_schedule"
        ),
    }
    return errors


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _normalized(row.get("venue_address") or row.get("branch")),
    )


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "catalogue_count": len(JEONJU_CATALOGUES),
        "required_list_requests": 0,
        "declared_totals": {},
        "declared_pages": {},
        "sentinel_pages": {},
        "page_counts": {},
        "source_counts": {},
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "expired_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "sessions_created": 0,
        "request_retry_count": 0,
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
        "recursion_depth": 0,
        "configured_collection_error": "",
    }


def collect_jeonju_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 100,
    detail_limit: int = 1000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Jeonju education snapshot."""

    meta = _base_meta()
    if not is_jeonju_education_target(target):
        meta["configured_collection_error"] = (
            "target is not the canonical Jeonju lifelong-learning catalogue"
        )
        return [], JEONJU_PARSER, meta
    if fetcher is None or session_factory is None:
        meta["configured_collection_error"] = (
            "managed fetcher and session_factory injection are required"
        )
        return [], JEONJU_PARSER, meta
    if max_pages < len(JEONJU_CATALOGUES) or detail_limit < 0:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "max_pages/detail_limit are invalid for fixed seven-catalogue fan-out"
        )
        return [], JEONJU_PARSER, meta

    reference_day = _today(today)
    errors: list[str] = []
    current_session: Any = None
    session_requests = 0
    first_pages: dict[str, BeautifulSoup] = {}
    declarations: dict[str, tuple[int, int]] = {}
    all_rows: list[dict[str, Any]] = []

    def fetch_page(url: str) -> BeautifulSoup:
        nonlocal current_session, session_requests
        for attempt in range(2):
            if (
                current_session is None
                or session_requests >= JEONJU_SESSION_REQUEST_LIMIT
            ):
                _close_quietly(current_session)
                current_session = session_factory()
                session_requests = 0
                meta["sessions_created"] += 1
            session_requests += 1
            try:
                return _fetch(fetcher, current_session, url, timeout)
            except requests.RequestException:
                if attempt:
                    raise
                meta["request_retry_count"] += 1
                _close_quietly(current_session)
                current_session = None
                session_requests = 0
        raise AssertionError("unreachable request retry boundary")

    try:
        for catalogue in JEONJU_CATALOGUES:
            soup = fetch_page(jeonju_list_url(catalogue, 1))
            first_pages[catalogue.gc] = soup
            contract = _page_contract(soup)
            if contract is None:
                errors.append(f"{catalogue.gc}: first-page total/pagination contract mismatch")
                continue
            total, declared_last, active = contract
            if active != 1:
                errors.append(f"{catalogue.gc}: first-page active marker is {active}")
                continue
            declarations[catalogue.gc] = (total, declared_last)
            meta["declared_totals"][catalogue.gc] = total
            meta["declared_pages"][catalogue.gc] = declared_last
            meta["sentinel_pages"][catalogue.gc] = declared_last + 1
            meta["pagination_detected"] = bool(
                meta["pagination_detected"] or declared_last > 1
            )

        if len(declarations) != len(JEONJU_CATALOGUES):
            errors.append("fixed Program21-Program27 fan-out discovery is incomplete")
        required = sum(last + 1 for _total, last in declarations.values())
        meta["required_list_requests"] = required
        if required > max_pages:
            meta["source_cap_reached"] = True
            errors.append(
                f"max_pages cap allows {max_pages} of {required} required list requests"
            )

        if not errors:
            for catalogue in JEONJU_CATALOGUES:
                declared_total, declared_last = declarations[catalogue.gc]
                source_rows: list[dict[str, Any]] = []
                for page in range(1, declared_last + 2):
                    source_url = jeonju_list_url(catalogue, page)
                    soup = (
                        first_pages[catalogue.gc]
                        if page == 1
                        else fetch_page(source_url)
                    )
                    meta["pages"] += 1
                    if page == declared_last + 1:
                        meta["page_counts"][f"{catalogue.gc}:{page}"] = 0
                        if not _empty_sentinel(soup):
                            errors.append(f"{catalogue.gc}: sentinel page is not empty")
                        continue
                    contract = _page_contract(soup)
                    if contract is None:
                        errors.append(
                            f"{catalogue.gc} page {page}: total/pagination contract mismatch"
                        )
                        continue
                    observed_total, observed_last, active = contract
                    if (observed_total, observed_last, active) != (
                        declared_total,
                        declared_last,
                        page,
                    ):
                        errors.append(
                            f"{catalogue.gc} page {page}: declared total/page marker changed"
                        )
                    rows, malformed = _parse_list_page(
                        target,
                        catalogue,
                        soup,
                        page=page,
                        source_url=source_url,
                    )
                    meta["page_counts"][f"{catalogue.gc}:{page}"] = len(rows)
                    if malformed:
                        errors.append(
                            f"{catalogue.gc} page {page}: {malformed} malformed catalogue rows"
                        )
                    expected = (
                        JEONJU_PAGE_SIZE
                        if page < declared_last
                        else declared_total - JEONJU_PAGE_SIZE * (declared_last - 1)
                    )
                    if len(rows) != expected:
                        errors.append(
                            f"{catalogue.gc} page {page}: expected {expected} rows, got {len(rows)}"
                        )
                    source_rows.extend(rows)
                if len(source_rows) != declared_total:
                    errors.append(
                        f"{catalogue.gc}: declared {declared_total}, parsed {len(source_rows)}"
                    )
                meta["source_counts"][catalogue.gc] = len(source_rows)
                all_rows.extend(source_rows)

        meta["source_total"] = meta["source_rows"] = len(all_rows)
        identities = [
            _clean(row.get("raw_fields", {}).get("program_id")) for row in all_rows
        ]
        course_ids = [_clean(row.get("provider_course_id")) for row in all_rows]
        raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
        meta["duplicate_identity_count"] = len(identities) - len(set(identities))
        meta["duplicate_count"] = len(course_ids) - len(set(course_ids))
        meta["duplicate_url_count"] = len(raw_urls) - len(set(raw_urls))
        if meta["duplicate_identity_count"]:
            errors.append(
                f"{meta['duplicate_identity_count']} duplicate program identities"
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
            for row in all_rows:
                identity = _clean(row.get("raw_fields", {}).get("program_id"))
                catalogue = JEONJU_CATALOGUE_BY_GC[
                    _clean(row.get("raw_fields", {}).get("source_gc"))
                ]
                meta["detail_attempts"] += 1
                try:
                    soup = fetch_page(_clean(row.get("raw_url")))
                    detail_errors = _enrich_detail(
                        row, catalogue, soup, reference_day
                    )
                    if detail_errors:
                        meta["detail_errors"] += 1
                        errors.extend(detail_errors)
                    else:
                        meta["detail_pages"] += 1
                except Exception as exc:
                    meta["detail_errors"] += 1
                    errors.append(
                        f"detail {identity}: fetch/parse failed ({type(exc).__name__})"
                    )

        current_rows = [
            row
            for row in all_rows
            if row.get("end_date")
            and date.fromisoformat(_clean(row.get("end_date"))) >= reference_day
        ]
        meta["current_count"] = len(current_rows)
        meta["expired_count"] = len(all_rows) - len(current_rows)
        meta["source_status_counts"] = dict(
            sorted(Counter(_clean(row.get("status")) for row in all_rows).items())
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
            row.get("status") in {"OPEN", "WAITING"}
            and bool(row.get("application_url"))
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
            and len(declarations) == len(JEONJU_CATALOGUES)
            and not meta["source_cap_reached"]
            and not any("page" in error or "fan-out" in error for error in errors)
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
                "the complete official Jeonju catalogues have no current/future courses"
                if all_rows
                else "the complete official Jeonju catalogues are empty"
            )
        meta["configured_collection_error"] = "; ".join(errors)
        return (
            current_rows if meta["snapshot_complete"] else [],
            JEONJU_PARSER,
            meta,
        )
    except Exception as exc:
        errors.append(f"fixed fan-out fetch/parse failed ({type(exc).__name__})")
        meta["configured_collection_error"] = "; ".join(errors)
        return [], JEONJU_PARSER, meta
    finally:
        _close_quietly(current_session)


collect = collect_jeonju_education_courses


__all__ = [
    "JEONJU_ALIAS_PROVIDERS",
    "JEONJU_BRANCH",
    "JEONJU_CANONICAL_CANDIDATE_ID",
    "JEONJU_CANONICAL_URL",
    "JEONJU_CATALOGUES",
    "JEONJU_DEOKJIN_CODE",
    "JEONJU_EXCLUDED_NON_COURSE_URLS",
    "JEONJU_HOST",
    "JEONJU_MUNICIPALITY_CODE",
    "JEONJU_MUNICIPALITY_NAMES",
    "JEONJU_OWNERSHIP_ALIAS_URLS",
    "JEONJU_PAGE_SIZE",
    "JEONJU_PARSER",
    "JEONJU_PROVIDER",
    "JEONJU_WANSAN_CODE",
    "JeonjuCatalogue",
    "collect",
    "collect_jeonju_education_courses",
    "is_jeonju_education_target",
    "is_jeonju_excluded_non_course_target",
    "is_jeonju_ownership_alias_target",
    "is_target",
    "jeonju_detail_url",
    "jeonju_list_url",
]
