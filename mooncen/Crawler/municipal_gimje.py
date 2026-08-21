"""Fail-closed collector for Gimje's complete official education catalogue.

The public target is the Gimje integrated-reservation root.  Education is
owned by one aggregate server-rendered catalogue and three disjoint child
catalogues (citizen IT, lifelong learning, and home learning).  Facility and
experience menus live under different top-level menu codes and are never
visited by this collector.

A snapshot is returned only after all aggregate and child pages are read, the
immediate post-last page for every catalogue is empty, page one is unchanged,
and the three child identity sets exactly partition the aggregate set.  Every
current/future aggregate row is then checked against its public detail page.
Application pages are deliberately never fetched: availability is true only
when the public detail page exposes a strictly validated official apply link.
No applicant, login, attachment, or free-form editor content is collected.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GIMJE_HOST = "www.gimje.go.kr"
GIMJE_PATH = "/reserve/index.gimje"
GIMJE_NAV_PATH = "/index.gimje"
GIMJE_ROOT_URL = f"https://{GIMJE_HOST}{GIMJE_PATH}"
GIMJE_URL = GIMJE_ROOT_URL
GIMJE_PROVIDER = "MUNI_WWW_GIMJE_GO_KR_834104C0"
GIMJE_MUNICIPALITY_CODE = "5221000000"
GIMJE_MUNICIPALITY_NAME = "전북특별자치도 김제시"
GIMJE_PAGE_SIZE = 10
GIMJE_MAX_WORKERS = 8
GIMJE_PARSER = (
    "gimje_education_aggregate+child_union+sentinel+page1_recheck+current_detail"
)

GIMJE_EDUCATION_MENU = "DOM_000001801000000000"
GIMJE_FACILITY_MENU = "DOM_000001802000000000"
GIMJE_EXPERIENCE_MENU = "DOM_000001803000000000"


@dataclass(frozen=True)
class GimjeCatalogue:
    key: str
    name: str
    menu: str
    venue: str


GIMJE_AGGREGATE = GimjeCatalogue(
    key="aggregate",
    name="김제시 교육강좌",
    menu=GIMJE_EDUCATION_MENU,
    venue="",
)
GIMJE_CHILD_CATALOGUES = (
    GimjeCatalogue(
        key="citizen_it",
        name="시민정보화교육장",
        menu="DOM_000001801001000000",
        venue="시민정보화교육장",
    ),
    GimjeCatalogue(
        key="lifelong",
        name="평생학습관",
        menu="DOM_000001801002000000",
        venue="평생학습관",
    ),
    GimjeCatalogue(
        key="home_learning",
        name="집콕 평생학습교실",
        menu="DOM_000001801003000000",
        venue="집콕 평생학습교실",
    ),
)
GIMJE_CATALOGUES = (GIMJE_AGGREGATE, *GIMJE_CHILD_CATALOGUES)
_CATALOGUE_BY_KEY = {item.key: item for item in GIMJE_CATALOGUES}

GIMJE_EDUCATION_URL = (
    f"{GIMJE_ROOT_URL}?" + urlencode((("menuCd", GIMJE_EDUCATION_MENU),))
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"IEDU_\d{15}")
_DATE_RANGE_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})"
)
_DATETIME_RANGE_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s+\d{2}:\d{2}\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s+\d{2}:\d{2}"
)
_CAPACITY_RE = re.compile(r"([\d,]+)\s*명")
_EXPECTED_LIST_FIELDS = {
    "접수기간",
    "교육장",
    "교육기간",
    "교육료",
    "교육시간",
    "모집인원",
    "강사명",
    "접수방법",
}
_CATEGORY_CODES: Mapping[str, str] = {
    "직업능력": "A",
    "문화예술": "B",
    "인문교양": "C",
    "취업대비": "D",
    "방학특강": "F",
}
_STATUS_MAP: Mapping[str, str] = {
    "준비중": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
    "교육완료": "CLOSED",
    "교육취소": "CLOSED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def is_gimje_education_target(target: Any) -> bool:
    """Match only the canonical root provider and exact root URL."""

    return _provider(target) == GIMJE_PROVIDER and _target_url(target) == GIMJE_URL


is_target = is_gimje_education_target


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
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
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
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def gimje_list_url(catalogue: Any = "aggregate", page: Any = 1) -> str:
    source = _CATALOGUE_BY_KEY.get(_clean(catalogue))
    raw_page = _clean(page)
    if source is None or not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    page_no = int(raw_page)
    values: list[tuple[str, Any]] = [("menuCd", source.menu)]
    if page_no > 1:
        values.append(("pageIndex", page_no))
    return f"{GIMJE_ROOT_URL}?{urlencode(values)}"


def gimje_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return f"{GIMJE_ROOT_URL}?" + urlencode(
        (("menuCd", GIMJE_EDUCATION_MENU), ("ieduSid", value))
    )


def gimje_application_url(identity: Any, category: Any) -> str:
    value = _clean(identity)
    code = _CATEGORY_CODES.get(_clean(category), "")
    if not _IDENTITY_RE.fullmatch(value) or not code:
        return ""
    return f"{GIMJE_ROOT_URL}?" + urlencode(
        (
            ("menuCd", GIMJE_EDUCATION_MENU),
            ("ieduSid", value),
            ("type", "rsv"),
            ("category", code),
        )
    )


def _query_one(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _official_menu_from_href(value: Any) -> str:
    parsed = urlparse(urljoin(f"https://{GIMJE_HOST}/", _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.hostname != GIMJE_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {GIMJE_PATH, GIMJE_NAV_PATH}
        or parsed.params
        or parsed.fragment
        or set(query) != {"menuCd"}
    ):
        return ""
    return _query_one(query, "menuCd")


def _root_owned(soup: BeautifulSoup) -> bool:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "김제시 통합 예약 시스템" not in title:
        return False
    menus = {
        menu
        for anchor in soup.select("a[href]")
        if (menu := _official_menu_from_href(anchor.get("href")))
    }
    required = {
        GIMJE_EDUCATION_MENU,
        *(item.menu for item in GIMJE_CHILD_CATALOGUES),
        GIMJE_FACILITY_MENU,
        GIMJE_EXPERIENCE_MENU,
    }
    return required <= menus


def _page_contract(
    soup: BeautifulSoup, source: GimjeCatalogue, requested_page: int
) -> Optional[int]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "김제시 통합 예약 시스템" not in title:
        return None
    containers = soup.select(".system_list")
    totals = soup.select(".system_list .total strong")
    if len(containers) != 1 or len(totals) != 1:
        return None
    raw_total = _clean(totals[0].get_text())
    if not raw_total.isdigit():
        return None
    matching_forms = []
    for form in soup.select("form"):
        menu = form.select_one("[name='menuCd']")
        page = form.select_one("[name='pageIndex']")
        if menu is None or page is None:
            continue
        if _clean(menu.get("value")) == source.menu:
            matching_forms.append((form, _clean(page.get("value"))))
    if len(matching_forms) != 1:
        return None
    form, page_value = matching_forms[0]
    if _clean(form.get("method")).lower() != "get":
        return None
    if not page_value.isdigit() or int(page_value) != requested_page:
        return None
    return int(raw_total)


def _identity_from_href(value: Any, source: GimjeCatalogue) -> str:
    parsed = urlparse(urljoin(f"https://{GIMJE_HOST}/", _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _query_one(query, "ieduSid")
    if (
        parsed.scheme != "https"
        or parsed.hostname != GIMJE_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {GIMJE_NAV_PATH, GIMJE_PATH}
        or parsed.params
        or parsed.fragment
        or set(query) != {"menuCd", "ieduSid"}
        or _query_one(query, "menuCd") != source.menu
        or not _IDENTITY_RE.fullmatch(identity)
    ):
        return ""
    return identity


def _valid_date_range(value: Any) -> tuple[str, str, str]:
    raw = _clean(value)
    match = _DATE_RANGE_RE.fullmatch(raw)
    if match is None:
        return "", "", ""
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
    except ValueError:
        return "", "", ""
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _application_dates(value: Any) -> tuple[str, str]:
    raw = _clean(value)
    if raw == "상시":
        return "", ""
    match = _DATETIME_RANGE_RE.fullmatch(raw)
    if match is None:
        return "", ""
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
    except ValueError:
        return "", ""
    if end < start:
        return "", ""
    return start.isoformat(), end.isoformat()


def _capacity(value: Any) -> int:
    match = _CAPACITY_RE.fullmatch(_clean(value))
    return int(match.group(1).replace(",", "")) if match else -1


def _card_fields(card: Any) -> tuple[dict[str, str], list[str]]:
    result: dict[str, str] = {}
    errors: list[str] = []
    for item in card.select("div.con > ul > li"):
        label = item.select_one("strong")
        value = item.select_one("span")
        if label is None or value is None:
            errors.append("malformed labelled field")
            continue
        key = _clean(label.get_text(" ", strip=True))
        if not key or key in result:
            errors.append("blank or duplicate labelled field")
            continue
        result[key] = _clean(value.get_text(" ", strip=True))
    if set(result) != _EXPECTED_LIST_FIELDS:
        errors.append(
            f"field labels {sorted(result)!r} != {sorted(_EXPECTED_LIST_FIELDS)!r}"
        )
    return result, errors


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"GIMJE_{digest}"


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    fields = row.get("raw_fields", {}).get("list_course_fields", {})
    return (
        _clean(row.get("raw_fields", {}).get("iedu_sid")),
        _clean(row.get("title")),
        _clean(row.get("category")),
        _clean(row.get("raw_fields", {}).get("source_status")),
        tuple(sorted((_clean(key), _clean(value)) for key, value in fields.items())),
    )


def _parse_list_page(
    soup: BeautifulSoup,
    source: GimjeCatalogue,
    page_no: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    cards = soup.select(".system_list ul.edu_list > li")
    for index, card in enumerate(cards, start=1):
        prefix = f"card {index}"
        anchors = card.select("a[href]")
        title_nodes = card.select("p.title > strong")
        category_nodes = card.select("p.title > span.cate")
        status_nodes = card.select("p.state > span")
        if not (
            len(anchors) == len(title_nodes) == len(category_nodes) == len(status_nodes) == 1
        ):
            errors.append(f"{prefix}: missing unique identity/title/category/status")
            continue
        identity = _identity_from_href(anchors[0].get("href"), source)
        title = _clean(title_nodes[0].get_text(" ", strip=True))
        category = _clean(category_nodes[0].get_text(" ", strip=True))
        source_status = _clean(status_nodes[0].get_text(" ", strip=True))
        normalized_status = _STATUS_MAP.get(source_status, "")
        fields, field_errors = _card_fields(card)
        errors.extend(f"{prefix}: {item}" for item in field_errors)
        if not identity or not title:
            errors.append(f"{prefix}: malformed stable identity or blank title")
        if category not in _CATEGORY_CODES:
            errors.append(f"{prefix}: unknown category {category!r}")
        if not normalized_status:
            errors.append(f"{prefix}: unknown source status {source_status!r}")
        start, end, period = _valid_date_range(fields.get("교육기간"))
        if not start or not end:
            errors.append(f"{prefix}: malformed education period")
        apply_start, apply_end = _application_dates(fields.get("접수기간"))
        if fields.get("접수기간") != "상시" and (not apply_start or not apply_end):
            errors.append(f"{prefix}: malformed application period")
        capacity_total = _capacity(fields.get("모집인원"))
        if capacity_total < 0:
            errors.append(f"{prefix}: malformed capacity")
        venue = _clean(fields.get("교육장"))
        if not venue:
            errors.append(f"{prefix}: blank education venue")
        if source.venue and venue != source.venue:
            errors.append(
                f"{prefix}: child venue {venue!r} != catalogue owner {source.venue!r}"
            )
        if not identity:
            continue
        detail_url = gimje_detail_url(identity)
        row: dict[str, Any] = {
            "provider": GIMJE_PROVIDER,
            "provider_course_id": f"{GIMJE_PROVIDER}:course:{identity}",
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "branch": venue,
            "branch_code": _branch_code(venue),
            "preserve_branch": True,
            "region": GIMJE_MUNICIPALITY_NAME,
            "category": category,
            "raw_url": detail_url,
            "source_url": detail_url,
            "status": normalized_status,
            "start_date": start,
            "end_date": end,
            "period": period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "apply_period": _clean(fields.get("접수기간")),
            "fee": _clean(fields.get("교육료")),
            "schedule_raw": _clean(fields.get("교육시간")),
            "capacity": _clean(fields.get("모집인원")),
            "capacity_total": max(0, capacity_total),
            "instructor": _clean(fields.get("강사명")),
            "application_method": _clean(fields.get("접수방법")),
            "room": venue,
            "venue_name": venue,
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "collection_type": "static_html+detail_html",
            "program_type": "교육",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": GIMJE_MUNICIPALITY_CODE,
            "municipality_full_name": GIMJE_MUNICIPALITY_NAME,
            "reservation_available": False,
            "raw_fields": {
                "municipality_code": GIMJE_MUNICIPALITY_CODE,
                "municipality_name": GIMJE_MUNICIPALITY_NAME,
                "catalogue_key": source.key,
                "catalogue_name": source.name,
                "catalogue_menu": source.menu,
                "list_page": page_no,
                "iedu_sid": identity,
                "category_code": _CATEGORY_CODES.get(category, ""),
                "source_status": source_status,
                "list_course_fields": fields,
                "parser": GIMJE_PARSER,
            },
        }
        if not apply_start:
            row.pop("apply_start_date", None)
        if not apply_end:
            row.pop("apply_end_date", None)
        rows.append(row)
    return rows, errors


def _table_pairs(table: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row in table.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index + 1 < len(cells):
            heading, value = cells[index], cells[index + 1]
            if heading.name == "th" and value.name == "td":
                label = _clean(heading.get_text(" ", strip=True))
                if label and label not in pairs:
                    pairs[label] = _clean(value.get_text(" ", strip=True))
            index += 2
    return pairs


def _strict_apply_control(
    anchor: Any, identity: str, category: str
) -> bool:
    parsed = urlparse(urljoin(f"https://{GIMJE_HOST}/", _clean(anchor.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == GIMJE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GIMJE_NAV_PATH
        and not parsed.params
        and not parsed.fragment
        and set(query) == {"menuCd", "ieduSid", "type", "category"}
        and _query_one(query, "menuCd") == GIMJE_EDUCATION_MENU
        and _query_one(query, "ieduSid") == identity
        and _query_one(query, "type") == "rsv"
        and _query_one(query, "category") == _CATEGORY_CODES.get(category, "")
    )


def _enrich_detail(
    row: dict[str, Any], soup: BeautifulSoup, cutoff: date
) -> tuple[bool, list[str]]:
    identity = _clean(row.get("raw_fields", {}).get("iedu_sid"))
    errors: list[str] = []
    sections = soup.select("section.edu_view.con_inner")
    if len(sections) != 1:
        return False, [f"course {identity}: missing unique public education detail"]
    section = sections[0]
    # An application/login form appearing inside this public detail boundary
    # would invalidate the PII-safe parser contract.
    if section.select("form"):
        errors.append(f"course {identity}: unexpected form in public detail")
    title_node = section.select_one("h4 > strong")
    venue_node = section.select_one("h4 > span.place")
    tables = section.select("table.edu_view_table")
    if title_node is None or venue_node is None or len(tables) != 1:
        return False, [f"course {identity}: malformed detail heading/table"]
    expected_title = f"[{_clean(row.get('category'))}] {_clean(row.get('title'))}"
    if _clean(title_node.get_text(" ", strip=True)) != expected_title:
        errors.append(f"course {identity}: detail title mismatch")
    if _clean(venue_node.get_text(" ", strip=True)) != _clean(row.get("venue_name")):
        errors.append(f"course {identity}: detail venue heading mismatch")

    table = tables[0]
    pairs = _table_pairs(table)
    required = {
        "수강대상",
        "모집인원",
        "교육기간",
        "교육시간",
        "교육료",
        "접수기간",
        "접수방법",
        "강사명",
        "문의처",
    }
    if not required <= set(pairs):
        errors.append(f"course {identity}: missing required detail labels")

    period_cell = next(
        (
            heading.find_next_sibling("td")
            for heading in table.select("th")
            if _clean(heading.get_text(" ", strip=True)) == "교육기간"
        ),
        None,
    )
    apply_cell = next(
        (
            heading.find_next_sibling("td")
            for heading in table.select("th")
            if _clean(heading.get_text(" ", strip=True)) == "접수기간"
        ),
        None,
    )
    detail_period = _clean(
        period_cell.select_one("strong").get_text(" ", strip=True)
        if period_cell and period_cell.select_one("strong")
        else ""
    )
    detail_apply_period = _clean(
        apply_cell.select_one("strong").get_text(" ", strip=True)
        if apply_cell and apply_cell.select_one("strong")
        else ""
    )
    detail_status = _clean(
        apply_cell.select_one("span.state").get_text(" ", strip=True)
        if apply_cell and apply_cell.select_one("span.state")
        else ""
    )
    if detail_period != _clean(row.get("period")):
        errors.append(f"course {identity}: detail education period mismatch")
    if detail_apply_period != _clean(row.get("apply_period")):
        errors.append(f"course {identity}: detail application period mismatch")
    if detail_status != _clean(row.get("raw_fields", {}).get("source_status")):
        errors.append(f"course {identity}: detail status mismatch")
    if _capacity(pairs.get("모집인원")) != int(row.get("capacity_total") or 0):
        errors.append(f"course {identity}: detail capacity mismatch")
    comparisons = (
        ("교육시간", "schedule_raw"),
        ("교육료", "fee"),
        ("접수방법", "application_method"),
        ("강사명", "instructor"),
    )
    for label, field in comparisons:
        if _compact(pairs.get(label)) != _compact(row.get(field)):
            errors.append(f"course {identity}: detail {label} mismatch")

    place_tables = section.select("table.place_table")
    if len(place_tables) != 1:
        errors.append(f"course {identity}: missing unique venue detail table")
        place_pairs: dict[str, str] = {}
    else:
        place_pairs = _table_pairs(place_tables[0])
        if _clean(place_pairs.get("교육장")) != _clean(row.get("venue_name")):
            errors.append(f"course {identity}: venue owner mismatch")

    controls = [
        anchor
        for anchor in section.select("ul.inline_btn a[href]")
        if _clean(anchor.get_text(" ", strip=True)) == "접수하기"
    ]
    if len(controls) > 1:
        errors.append(f"course {identity}: multiple application controls")
    control_present = len(controls) == 1
    if control_present and not _strict_apply_control(
        controls[0], identity, _clean(row.get("category"))
    ):
        errors.append(f"course {identity}: unsafe application control")
    if control_present and "온라인" not in _clean(row.get("application_method")):
        errors.append(f"course {identity}: online control conflicts with method")
    if control_present and _clean(row.get("raw_fields", {}).get("source_status")) != "접수중":
        errors.append(f"course {identity}: application control conflicts with status")

    row.update(
        {
            "target": _clean(pairs.get("수강대상")),
            "description": _clean(pairs.get("교육소개")),
            "phone": _clean(place_pairs.get("문의처") or pairs.get("문의처")),
            "address": _clean(place_pairs.get("주소")),
            "venue_address": _clean(place_pairs.get("주소")),
            "reservation_available": bool(control_present and not errors),
        }
    )
    raw_fields = row.get("raw_fields") or {}
    raw_fields.update(
        {
            "detail_valid": not errors,
            "detail_status": detail_status,
            "application_control_present": control_present,
            "pii_safe_public_detail_only": True,
            "clear_application_url": not control_present,
        }
    )
    row["raw_fields"] = raw_fields
    if control_present and not errors:
        row["application_url"] = gimje_application_url(
            identity, _clean(row.get("category"))
        )
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        row.pop("application_type", None)
    try:
        current = date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
    except ValueError:
        current = False
        errors.append(f"course {identity}: invalid detail end date")
    return current, errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure_meta(message: str, *, source_cap_reached: bool = False) -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "root_requests": 0,
        "warmup_requests": 0,
        "list_pages": 0,
        "list_requests": 0,
        "sentinel_requests": 0,
        "page_one_rechecks": 0,
        "detail_pages": 0,
        "detail_required_count": 0,
        "total_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "child_union_complete": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": source_cap_reached,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_gimje_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 300,
    detail_limit: int = 300,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GIMJE_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future Gimje education snapshot."""

    if not is_gimje_education_target(target):
        return [], GIMJE_PARSER, _failure_meta(
            "target is not the canonical Gimje integrated-reservation root owner"
        )
    if (fetcher is None) != (session_factory is None):
        return [], GIMJE_PARSER, _failure_meta(
            "fetcher and session_factory must be injected together"
        )

    current_fetcher = fetcher or _default_fetcher
    current_session_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    cutoff = _today(today)
    worker_count = min(max(1, int(max_workers)), GIMJE_MAX_WORKERS)
    errors: list[str] = []
    detail_errors: list[str] = []
    source_cap_reached = False
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    metrics_lock = threading.Lock()
    local = threading.local()
    warmup_requests = 0

    def register_session(value: Any) -> Any:
        with sessions_lock:
            sessions.append(value)
        return value

    def session_for_thread(*, warm: bool) -> Any:
        nonlocal warmup_requests
        value = getattr(local, "session", None)
        if value is None:
            value = register_session(current_session_factory())
            local.session = value
            local.warmed = False
        if warm and not bool(getattr(local, "warmed", False)):
            root = _fetch(current_fetcher, value, GIMJE_ROOT_URL, timeout)
            if not _root_owned(root):
                raise ValueError("detail-session root ownership/navigation failed")
            local.warmed = True
            with metrics_lock:
                warmup_requests += 1
        return value

    def fetch_url(url: str, *, warm: bool = False) -> BeautifulSoup:
        return _fetch(current_fetcher, session_for_thread(warm=warm), url, timeout)

    page_rows: dict[tuple[str, int], list[dict[str, Any]]] = {}
    totals: dict[str, int] = {}
    last_pages: dict[str, int] = {}
    list_requests = 0
    sentinel_requests = 0
    page_one_rechecks = 0
    detail_pages = 0

    try:
        primary = register_session(current_session_factory())
        local.session = primary
        local.warmed = True
        try:
            root = _fetch(current_fetcher, primary, GIMJE_ROOT_URL, timeout)
        except Exception as exc:
            return [], GIMJE_PARSER, _failure_meta(
                f"official root fetch failed: {type(exc).__name__}"
            )
        if not _root_owned(root):
            return [], GIMJE_PARSER, _failure_meta(
                "official root ownership/navigation contract failed"
            )

        for source in GIMJE_CATALOGUES:
            try:
                soup = _fetch(
                    current_fetcher,
                    primary,
                    gimje_list_url(source.key, 1),
                    timeout,
                )
                list_requests += 1
            except Exception as exc:
                errors.append(f"{source.name} page 1: fetch {type(exc).__name__}")
                continue
            total = _page_contract(soup, source, 1)
            if total is None:
                errors.append(f"{source.name} page 1: malformed ownership/page contract")
                continue
            last = max(1, math.ceil(total / GIMJE_PAGE_SIZE))
            totals[source.key] = total
            last_pages[source.key] = last
            if last > int(max_pages):
                source_cap_reached = True
                errors.append(
                    f"{source.name}: max_pages cap {int(max_pages)} is below required {last} pages"
                )
                continue
            parsed, page_errors = _parse_list_page(soup, source, 1)
            errors.extend(f"{source.name} page 1: {item}" for item in page_errors)
            page_rows[(source.key, 1)] = parsed

        if errors:
            meta = _failure_meta(
                "; ".join(dict.fromkeys(errors)),
                source_cap_reached=source_cap_reached,
            )
            meta.update(
                {
                    "pages": 1 + list_requests,
                    "request_count": 1 + list_requests,
                    "root_requests": 1,
                    "list_requests": list_requests,
                    "catalogue_totals": totals,
                }
            )
            return [], GIMJE_PARSER, meta

        tasks = [
            (source, page_no, page_no == last_pages[source.key] + 1)
            for source in GIMJE_CATALOGUES
            for page_no in range(2, last_pages[source.key] + 2)
        ]

        def fetch_page(
            task: tuple[GimjeCatalogue, int, bool]
        ) -> tuple[GimjeCatalogue, int, bool, Optional[BeautifulSoup], str]:
            source, page_no, sentinel = task
            try:
                return (
                    source,
                    page_no,
                    sentinel,
                    fetch_url(gimje_list_url(source.key, page_no)),
                    "",
                )
            except Exception as exc:
                return source, page_no, sentinel, None, f"fetch {type(exc).__name__}"

        if worker_count == 1:
            page_results = [fetch_page(task) for task in tasks]
        else:
            with ThreadPoolExecutor(
                max_workers=min(worker_count, len(tasks)),
                thread_name_prefix="gimje-list",
            ) as pool:
                page_results = list(pool.map(fetch_page, tasks))
        for source, page_no, sentinel, soup, fetch_error in page_results:
            if sentinel:
                sentinel_requests += 1
            else:
                list_requests += 1
            if fetch_error or soup is None:
                errors.append(
                    f"{source.name} page {page_no}: {fetch_error or 'empty response'}"
                )
                continue
            total = _page_contract(soup, source, page_no)
            if total != totals[source.key]:
                errors.append(
                    f"{source.name} page {page_no}: total/ownership contract changed"
                )
                continue
            parsed, page_errors = _parse_list_page(soup, source, page_no)
            errors.extend(f"{source.name} page {page_no}: {item}" for item in page_errors)
            if sentinel:
                if parsed:
                    errors.append(
                        f"{source.name} immediate post-last page {page_no} is not empty"
                    )
            else:
                page_rows[(source.key, page_no)] = parsed

        for source in GIMJE_CATALOGUES:
            total = totals[source.key]
            last = last_pages[source.key]
            exposed = 0
            for page_no in range(1, last + 1):
                count = len(page_rows.get((source.key, page_no), []))
                exposed += count
                expected = (
                    GIMJE_PAGE_SIZE
                    if page_no < last
                    else total - GIMJE_PAGE_SIZE * (last - 1)
                )
                if total == 0 and page_no == 1:
                    expected = 0
                if count != expected:
                    errors.append(
                        f"{source.name} page {page_no}: exposed {count}, expected {expected}"
                    )
            if exposed != total:
                errors.append(f"{source.name}: exposed {exposed}, declared {total}")

        def recheck(
            source: GimjeCatalogue,
        ) -> tuple[GimjeCatalogue, Optional[BeautifulSoup], str]:
            try:
                return source, fetch_url(gimje_list_url(source.key, 1)), ""
            except Exception as exc:
                return source, None, f"fetch {type(exc).__name__}"

        if worker_count == 1:
            rechecks = [recheck(source) for source in GIMJE_CATALOGUES]
        else:
            with ThreadPoolExecutor(
                max_workers=min(worker_count, len(GIMJE_CATALOGUES)),
                thread_name_prefix="gimje-recheck",
            ) as pool:
                rechecks = list(pool.map(recheck, GIMJE_CATALOGUES))
        page_one_rechecks = len(rechecks)
        for source, soup, fetch_error in rechecks:
            if fetch_error or soup is None:
                errors.append(f"{source.name} page 1 recheck: {fetch_error}")
                continue
            total = _page_contract(soup, source, 1)
            checked, checked_errors = _parse_list_page(soup, source, 1)
            errors.extend(
                f"{source.name} page 1 recheck: {item}" for item in checked_errors
            )
            original = page_rows.get((source.key, 1), [])
            if total != totals[source.key] or [
                _row_signature(row) for row in checked
            ] != [_row_signature(row) for row in original]:
                errors.append(f"{source.name}: page 1 changed during traversal")

        rows_by_source: dict[str, list[dict[str, Any]]] = {
            source.key: [
                row
                for page_no in range(1, last_pages[source.key] + 1)
                for row in page_rows.get((source.key, page_no), [])
            ]
            for source in GIMJE_CATALOGUES
        }
        identities_by_source: dict[str, list[str]] = {
            key: [
                _clean(row.get("raw_fields", {}).get("iedu_sid")) for row in rows
            ]
            for key, rows in rows_by_source.items()
        }
        for source in GIMJE_CATALOGUES:
            identities = identities_by_source[source.key]
            if len(set(identities)) != len(identities) or any(not item for item in identities):
                errors.append(f"{source.name}: duplicate or blank stable identities")

        aggregate_rows = rows_by_source[GIMJE_AGGREGATE.key]
        aggregate_by_id = {
            _clean(row.get("raw_fields", {}).get("iedu_sid")): row
            for row in aggregate_rows
        }
        child_sets = {
            source.key: set(identities_by_source[source.key])
            for source in GIMJE_CHILD_CATALOGUES
        }
        child_union = set().union(*child_sets.values())
        aggregate_set = set(identities_by_source[GIMJE_AGGREGATE.key])
        if child_union != aggregate_set:
            errors.append("three child catalogues do not exactly cover aggregate identities")
        for index, left in enumerate(GIMJE_CHILD_CATALOGUES):
            for right in GIMJE_CHILD_CATALOGUES[index + 1 :]:
                if child_sets[left.key] & child_sets[right.key]:
                    errors.append(
                        f"child catalogue ownership overlaps: {left.name}/{right.name}"
                    )
        for source in GIMJE_CHILD_CATALOGUES:
            for child in rows_by_source[source.key]:
                identity = _clean(child.get("raw_fields", {}).get("iedu_sid"))
                aggregate = aggregate_by_id.get(identity)
                if aggregate is None or _row_signature(child) != _row_signature(aggregate):
                    errors.append(
                        f"course {identity}: child/aggregate course payload mismatch"
                    )

        current_by_list: list[dict[str, Any]] = []
        expired_count = 0
        for row in aggregate_rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(
                    f"course {row.get('raw_fields', {}).get('iedu_sid')}: invalid list end date"
                )
                continue
            if end >= cutoff:
                current_by_list.append(row)
            else:
                expired_count += 1

        detail_required_count = len(current_by_list)
        if int(detail_limit) < detail_required_count:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap {int(detail_limit)} is below required {detail_required_count} details"
            )

        current_rows: list[dict[str, Any]] = []
        if not errors and current_by_list:

            def fetch_detail(
                row: dict[str, Any],
            ) -> tuple[dict[str, Any], bool, bool, list[str]]:
                identity = _clean(row.get("raw_fields", {}).get("iedu_sid"))
                try:
                    soup = fetch_url(gimje_detail_url(identity), warm=True)
                    is_current, item_errors = _enrich_detail(row, soup, cutoff)
                    return row, True, is_current, item_errors
                except Exception as exc:
                    return row, False, False, [
                        f"course {identity}: detail fetch {type(exc).__name__}"
                    ]

            if worker_count == 1:
                detail_results = [fetch_detail(row) for row in current_by_list]
            else:
                with ThreadPoolExecutor(
                    max_workers=min(worker_count, detail_required_count),
                    thread_name_prefix="gimje-detail",
                ) as pool:
                    detail_results = list(pool.map(fetch_detail, current_by_list))
            for row, fetched, is_current, item_errors in detail_results:
                detail_pages += int(fetched)
                detail_errors.extend(item_errors)
                if is_current and not item_errors:
                    current_rows.append(row)

        errors.extend(detail_errors)
        if len(current_rows) != detail_required_count:
            errors.append(
                f"validated current detail count {len(current_rows)} != required {detail_required_count}"
            )

        cleaned = current_rows
        if not errors:
            try:
                deduped = list(current_dedupe(cleaned))
            except Exception as exc:
                errors.append(f"dedupe failed {type(exc).__name__}")
                deduped = []
            if len(deduped) != len(cleaned):
                errors.append(
                    f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}"
                )
            cleaned = deduped

        child_union_complete = bool(
            not errors
            and child_union == aggregate_set
            and sum(len(values) for values in child_sets.values()) == len(aggregate_set)
        )
        list_pages = sum(last_pages.values())
        pagination_complete = bool(
            not errors
            and len(page_rows) == list_pages
            and sentinel_requests == len(GIMJE_CATALOGUES)
            and page_one_rechecks == len(GIMJE_CATALOGUES)
        )
        details_complete = bool(
            not detail_errors
            and not source_cap_reached
            and detail_pages == detail_required_count
            and len(current_rows) == detail_required_count
        )
        snapshot_complete = bool(
            pagination_complete and child_union_complete and details_complete and not errors
        )
        if not snapshot_complete:
            cleaned = []

        status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status"))
            for row in aggregate_rows
        )
        current_status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status"))
            for row in current_rows
        )
        branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        request_count = (
            1
            + warmup_requests
            + list_requests
            + sentinel_requests
            + page_one_rechecks
            + detail_pages
        )
        meta: dict[str, Any] = {
            "pages": request_count,
            "request_count": request_count,
            "root_requests": 1,
            "warmup_requests": warmup_requests,
            "list_pages": list_pages,
            "list_requests": list_requests,
            "sentinel_requests": sentinel_requests,
            "page_one_rechecks": page_one_rechecks,
            "detail_pages": detail_pages,
            "detail_required_count": detail_required_count,
            "total_count": len(aggregate_rows),
            "source_exposed_count": len(aggregate_rows),
            "unique_id_count": len(aggregate_set),
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(cleaned),
            "catalogue_totals": {
                source.name: totals.get(source.key, 0) for source in GIMJE_CATALOGUES
            },
            "catalogue_page_counts": {
                source.name: last_pages.get(source.key, 0)
                for source in GIMJE_CATALOGUES
            },
            "catalogue_row_counts": {
                source.name: len(rows_by_source.get(source.key, []))
                for source in GIMJE_CATALOGUES
            },
            "status_counts": dict(status_counts),
            "current_status_counts": dict(current_status_counts),
            "branch_counts": dict(branch_counts),
            "application_control_count": sum(
                bool(row.get("application_url")) for row in current_rows
            ),
            "pii_pages_fetched": 0,
            "education_menu": GIMJE_EDUCATION_MENU,
            "excluded_facility_menu": GIMJE_FACILITY_MENU,
            "excluded_experience_menu": GIMJE_EXPERIENCE_MENU,
            "child_union_complete": child_union_complete,
            "pagination_detected": list_pages > len(GIMJE_CATALOGUES),
            "pagination_complete": pagination_complete,
            "pagination_exhausted": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": snapshot_complete and not current_rows,
            "no_current_reason": (
                "complete Gimje education catalogue has no current/future rows"
                if snapshot_complete and not current_rows
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
        return cleaned, GIMJE_PARSER, meta
    finally:
        for value in sessions:
            _close_quietly(value)


collect_gimje_target = collect_gimje_education_courses


__all__ = [
    "GIMJE_AGGREGATE",
    "GIMJE_CATALOGUES",
    "GIMJE_CHILD_CATALOGUES",
    "GIMJE_EDUCATION_MENU",
    "GIMJE_EDUCATION_URL",
    "GIMJE_EXPERIENCE_MENU",
    "GIMJE_FACILITY_MENU",
    "GIMJE_HOST",
    "GIMJE_MAX_WORKERS",
    "GIMJE_MUNICIPALITY_CODE",
    "GIMJE_MUNICIPALITY_NAME",
    "GIMJE_PAGE_SIZE",
    "GIMJE_PARSER",
    "GIMJE_PROVIDER",
    "GIMJE_ROOT_URL",
    "GIMJE_URL",
    "GimjeCatalogue",
    "collect_gimje_education_courses",
    "collect_gimje_target",
    "gimje_application_url",
    "gimje_detail_url",
    "gimje_list_url",
    "is_gimje_education_target",
    "is_target",
]
