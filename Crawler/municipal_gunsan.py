"""Fail-closed collector for Gunsan City's official education catalogues.

The official lifelong-learning site does not expose one complete catalogue.
Its configured ``/pro/course.php`` route owns only the neighbourhood culture
cafe and can legitimately be empty while the municipal learning centres still
publish courses.  A complete snapshot therefore enumerates the eight audited
official catalogues below.  Every catalogue is read through its declared final
page and an immediately following empty sentinel.  All list identities are
then verified against detail pages before education-end filtering is applied.

``www.gunsan.go.kr/main/m140`` is deliberately not an alias that may execute:
that menu id now serves the general municipal notice board and the generic
parser has historically mistaken housing and scholarship notices for courses.

This module intentionally does not import ``Crawler_MunicipalYaml``.  The
shared router must inject its managed fetcher and session factory, preventing
an import cycle and preserving the global request/TLS safety controls.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GUNSAN_PROVIDER = "MUNI_LLL_GUNSAN_GO_KR_0202DBAC"
GUNSAN_CANONICAL_URL = (
    "https://lll.gunsan.go.kr/program/lecture2018.php?m=160100"
)
GUNSAN_LEGACY_ENTRY_URL = "https://lll.gunsan.go.kr/pro/course.php?pm=list"
GUNSAN_HOST = "lll.gunsan.go.kr"
GUNSAN_MUNICIPALITY_CODE = "5213000000"
GUNSAN_MUNICIPALITY_NAME = "전북특별자치도 군산시"
GUNSAN_PARSER = (
    "gunsan_fixed_official_fanout+continuous_pages+empty_sentinels+"
    "all_details+education_end_filter"
)
GUNSAN_PAGE_SIZE = 10

GUNSAN_OWNERSHIP_ALIAS_URLS = (GUNSAN_LEGACY_ENTRY_URL,)
GUNSAN_EXCLUDED_STALE_NOTICE_URLS = (
    "https://www.gunsan.go.kr/main/m140",
)


@dataclass(frozen=True)
class GunsanSource:
    code: str
    name: str
    path: str
    query: tuple[tuple[str, str], ...]
    headers: tuple[str, ...]
    kind: str = "standard"
    detail_m: str = ""
    sequence_column: bool = False


_STANDARD_HEADERS_WITH_CODE = (
    "코드",
    "상태",
    "강좌명",
    "대상",
    "요일(시간)",
    "기간",
    "접수/정원",
)
_STANDARD_HEADERS = (
    "상태",
    "강좌명",
    "대상",
    "요일(시간)",
    "기간",
    "접수/정원",
)
_CULTURE_HEADERS = (
    "상태",
    "구분",
    "강좌명",
    "요일",
    "시간",
    "읍면동",
    "장소",
    "접수 및 인원",
    "남/여",
    "공개 여부",
    "신청",
)
_GUNSANHAK_HEADERS_TAIL = ("일자", "강사명", "직위 및 강의주제")

GUNSAN_SOURCES: tuple[GunsanSource, ...] = (
    GunsanSource(
        "central",
        "군산시 평생학습관",
        "/program/lecture2018.php",
        (("m", "160100"),),
        _STANDARD_HEADERS_WITH_CODE,
        detail_m="160100",
        sequence_column=True,
    ),
    GunsanSource(
        "wolmyeong",
        "월명 평생학습센터",
        "/program/mlecture2018.php",
        (("m", "170100"),),
        _STANDARD_HEADERS_WITH_CODE,
        detail_m="170100",
        sequence_column=True,
    ),
    GunsanSource(
        "osicdo",
        "오식도 평생학습센터",
        "/program/osicdo.php",
        (),
        _STANDARD_HEADERS_WITH_CODE,
        detail_m="00000000",
        sequence_column=True,
    ),
    GunsanSource(
        "semangm",
        "군산새만금아카데미",
        "/program/semangm2018.php",
        (("m", "130201"),),
        _STANDARD_HEADERS,
        detail_m="130201",
    ),
    GunsanSource(
        "gunsanhak",
        "군산학",
        "/program/gunsanhak2018_1.php",
        (("m", "130505"),),
        (),
        kind="gunsanhak",
    ),
    GunsanSource(
        "future",
        "미래설계 교육과정",
        "/program/future_list.php",
        (("m", "131500"),),
        _STANDARD_HEADERS,
        detail_m="131500",
    ),
    GunsanSource(
        "minju",
        "민주시민 교육과정",
        "/program/minju_list.php",
        (),
        _STANDARD_HEADERS,
    ),
    GunsanSource(
        "culture_cafe",
        "동네문화카페",
        "/pro/course.php",
        (("pm", "list"),),
        _CULTURE_HEADERS,
        kind="culture",
        detail_m="00000000",
    ),
)


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d{6,18}")
_FULL_RANGE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})"
    r"\s*~\s*"
    r"(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_LIST_PERIOD_RE = re.compile(
    r"교육\s*:\s*(?:20\d{2}-)?(\d{1,2})-(\d{1,2})\s*~\s*"
    r"(?:20\d{2}-)?(\d{1,2})-(\d{1,2})\s*"
    r"신청\s*:\s*(?:20\d{2}-)?(\d{1,2})-(\d{1,2})\s*~\s*"
    r"(?:20\d{2}-)?(\d{1,2})-(\d{1,2})"
)
_NO_DATA_TEXTS = frozenset(
    {
        "검색 결과가 없습니다.",
        "검색결과가없습니다.",
        "등록된 강좌가 없습니다.",
        "등록된강좌가없습니다.",
        "자료가 없습니다.",
        "자료가없습니다.",
    }
)
_DETAIL_REQUIRED_KEYS = frozenset(
    {
        "강좌명",
        "수강료",
        "모집정원",
        "강의기간",
        "접수기간",
        "교육장소",
        "강좌요일",
    }
)
_CANCELLED_TOKENS = ("폐강", "취소")
_SOURCE_CLOSED_TOKENS = ("마감", "종료", "완료")
_APPLICATION_LABELS = ("신청하기", "수강신청", "접수하기", "대기신청")


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


def _exact_https_url(value: Any, path: str, query: Mapping[str, list[str]]) -> bool:
    parsed = urlparse(_clean(value))
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GUNSAN_HOST
        and parsed.port is None
        and parsed.path == path
        and parse_qs(parsed.query, keep_blank_values=True) == query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_gunsan_education_target(target: Any) -> bool:
    if _provider(target) != GUNSAN_PROVIDER:
        return False
    value = _target_url(target)
    return _exact_https_url(
        value, "/program/lecture2018.php", {"m": ["160100"]}
    ) or _exact_https_url(value, "/pro/course.php", {"pm": ["list"]})


def is_gunsan_excluded_notice_target(target: Any) -> bool:
    return _target_url(target) in GUNSAN_EXCLUDED_STALE_NOTICE_URLS


is_target = is_gunsan_education_target


def _source_variants() -> tuple[tuple[GunsanSource, str], ...]:
    result: list[tuple[GunsanSource, str]] = []
    for source in GUNSAN_SOURCES:
        if source.kind == "culture":
            result.extend(((source, "1"), (source, "2")))
        else:
            result.append((source, ""))
    return tuple(result)


def gunsan_list_url(
    source: GunsanSource,
    page: Any = 1,
    *,
    variant: str = "",
    reference_year: Optional[int] = None,
) -> str:
    raw_page = _clean(page)
    if source not in GUNSAN_SOURCES or not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    if source.kind == "culture" and variant not in {"1", "2"}:
        return ""
    if source.kind != "culture" and variant:
        return ""
    pairs = list(source.query)
    if source.kind == "gunsanhak":
        year = int(reference_year or datetime.now(ZoneInfo("Asia/Seoul")).year)
        if year < 2000 or year > 2200:
            return ""
        pairs.append(("cate_year", str(year)))
    if source.kind == "culture":
        pairs.append(("status", variant))
    if int(raw_page) > 1:
        pairs.append(("page", str(int(raw_page))))
    query = urlencode(pairs)
    return f"https://{GUNSAN_HOST}{source.path}" + (f"?{query}" if query else "")


def gunsan_detail_url(source: GunsanSource, identity: Any) -> str:
    raw_identity = _clean(identity)
    if source not in GUNSAN_SOURCES or source.kind == "gunsanhak":
        return ""
    if not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    pairs: list[tuple[str, str]] = [("pm", "view"), ("idx", raw_identity)]
    if source.detail_m:
        pairs.append(("m", source.detail_m))
    return f"https://{GUNSAN_HOST}{source.path}?{urlencode(pairs)}"


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


def _table_headers(table: Any) -> tuple[str, ...]:
    first = table.find("tr") if table is not None else None
    return tuple(
        _clean(cell.get_text(" ", strip=True))
        for cell in first.find_all("th", recursive=False)
    ) if first is not None else ()


def _catalogue_table(
    soup: BeautifulSoup,
    source: GunsanSource,
    reference_year: int,
) -> Optional[Any]:
    matches = []
    for table in soup.select("table.borad_skin"):
        headers = _table_headers(table)
        if source.kind == "gunsanhak":
            if (
                len(headers) == 4
                and headers[0] == f"{reference_year} 강좌"
                and headers[1:] == _GUNSANHAK_HEADERS_TAIL
            ):
                matches.append(table)
        elif headers == source.headers:
            matches.append(table)
    return matches[0] if len(matches) == 1 else None


def _page_contract(soup: BeautifulSoup, source: GunsanSource) -> tuple[int, int]:
    pages = {1}
    for anchor in soup.select(".bbspage a[href], .listbox-page a[href]"):
        parsed = urlparse(urljoin(f"https://{GUNSAN_HOST}{source.path}", _clean(anchor.get("href"))))
        raw = (parse_qs(parsed.query, keep_blank_values=True).get("page") or [""])[0]
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").rstrip(".").lower() != GUNSAN_HOST
            or parsed.path != source.path
            or not raw.isdigit()
            or int(raw) < 1
        ):
            return 0, -1
        pages.add(int(raw))
    active_nodes = soup.select(".bbspage li.on, .listbox-page li.on")
    active = 0
    if active_nodes:
        if len(active_nodes) != 1:
            return 0, -1
        raw_active = _clean(active_nodes[0].get_text(" ", strip=True))
        if not raw_active.isdigit() or int(raw_active) < 1:
            return 0, -1
        active = int(raw_active)
        pages.add(active)
    return max(pages), active


def _is_no_data_row(cells: list[Any]) -> bool:
    if len(cells) != 1:
        return False
    text = _clean(cells[0].get_text(" ", strip=True))
    return text in _NO_DATA_TEXTS or _normalized(text) in {
        _normalized(value) for value in _NO_DATA_TEXTS
    }


def _single_query_value(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _detail_identity(
    source: GunsanSource,
    value: Any,
    base_url: str,
) -> tuple[str, str]:
    parsed = urlparse(urljoin(base_url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query_value(query, "idx")
    allowed = {"pm", "idx"}
    if "m" in query:
        allowed.add("m")
    expected_m = source.detail_m
    actual_m = _single_query_value(query, "m")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != GUNSAN_HOST
        or parsed.port is not None
        or parsed.path != source.path
        or set(query) != allowed
        or _single_query_value(query, "pm") != "view"
        or not _IDENTITY_RE.fullmatch(identity)
        or (expected_m and actual_m != expected_m)
        or (not expected_m and actual_m)
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return "", ""
    return identity, gunsan_detail_url(source, identity)


def _row_detail_identity(
    source: GunsanSource,
    row: Any,
    base_url: str,
) -> tuple[str, str]:
    values = {
        _detail_identity(source, anchor.get("href"), base_url)
        for anchor in row.select("a[href]")
        if "pm=view" in _clean(anchor.get("href"))
    }
    values.discard(("", ""))
    return next(iter(values)) if len(values) == 1 else ("", "")


def _list_period_signature(value: Any) -> tuple[tuple[int, int], ...]:
    match = _LIST_PERIOD_RE.fullmatch(_clean(value))
    if match is None:
        return ()
    raw = [int(part) for part in match.groups()]
    result = tuple((raw[index], raw[index + 1]) for index in range(0, 8, 2))
    try:
        for month, day_value in result:
            date(2000, month, day_value)
    except ValueError:
        return ()
    return result


def _branch_code() -> str:
    digest = hashlib.sha1(
        f"{GUNSAN_PROVIDER}|{GUNSAN_MUNICIPALITY_NAME}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"GUNSAN_BRANCH_{digest}"


def _base_row(
    target: Any,
    source: GunsanSource,
    identity: str,
    raw_url: str,
    title: str,
    source_status: str,
    page: int,
) -> dict[str, Any]:
    return {
        "provider": _provider(target),
        "provider_course_id": f"{_provider(target)}:gunsan:{source.code}:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "program_type": source.name,
        "category": "교육·강좌",
        "branch": GUNSAN_MUNICIPALITY_NAME,
        "branch_code": _branch_code(),
        "branch_url": GUNSAN_CANONICAL_URL,
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
        "municipality_code": GUNSAN_MUNICIPALITY_CODE,
        "municipality_full_name": GUNSAN_MUNICIPALITY_NAME,
        "collection_type": "complete_fixed_fanout+sentinels+detail_html",
        "raw_fields": {
            "parser": GUNSAN_PARSER,
            "source_code": source.code,
            "source_name": source.name,
            "source_page": page,
            "source_status": source_status,
            "detail_id": identity,
        },
    }


def _parse_standard_page(
    target: Any,
    source: GunsanSource,
    table: Any,
    *,
    page: int,
    source_url: str,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    expected = len(source.headers)
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td", recursive=False)
        if _is_no_data_row(cells):
            continue
        if len(cells) != expected:
            malformed += 1
            continue
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        identity, raw_url = _row_detail_identity(source, tr, source_url)
        offset = 1 if source.sequence_column else 0
        sequence = values[0] if source.sequence_column else ""
        source_status = values[offset]
        title = values[offset + 1]
        target_value = values[offset + 2]
        schedule = values[offset + 3]
        list_period = values[offset + 4]
        capacity = values[offset + 5]
        signature = _list_period_signature(list_period)
        if (
            not identity
            or not title
            or not source_status
            or not schedule
            or not signature
            or (source.sequence_column and (not sequence.isdigit() or int(sequence) < 1))
        ):
            malformed += 1
            continue
        row = _base_row(
            target, source, identity, raw_url, title, source_status, page
        )
        row.update(
            {
                "target": target_value,
                "schedule_raw": schedule,
                "capacity": capacity,
                "description": _clean(tr.get_text(" ", strip=True)),
            }
        )
        row["raw_fields"] = {
            **row["raw_fields"],
            "source_sequence": int(sequence) if sequence else None,
            "list_period": list_period,
            "list_period_signature": signature,
            "list_cells": values,
        }
        rows.append(row)
    return rows, malformed


def _parse_culture_page(
    target: Any,
    source: GunsanSource,
    table: Any,
    *,
    page: int,
    source_url: str,
    variant: str,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td", recursive=False)
        if _is_no_data_row(cells):
            continue
        if len(cells) != len(_CULTURE_HEADERS):
            malformed += 1
            continue
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        identity, raw_url = _row_detail_identity(source, tr, source_url)
        schedule = _clean(f"{values[3]} {values[4]}")
        if not identity or not values[0] or not values[2] or not schedule:
            malformed += 1
            continue
        row = _base_row(
            target, source, identity, raw_url, values[2], values[0], page
        )
        row.update(
            {
                "program_type": values[1] or source.name,
                "schedule_raw": schedule,
                "venue_name": values[6],
                "capacity": values[7],
                "description": _clean(tr.get_text(" ", strip=True)),
            }
        )
        row["raw_fields"] = {
            **row["raw_fields"],
            "culture_status_filter": variant,
            "town": values[5],
            "gender": values[8],
            "visibility": values[9],
            "list_cells": values,
        }
        rows.append(row)
    return rows, malformed


def _parse_list_page(
    target: Any,
    source: GunsanSource,
    table: Any,
    *,
    page: int,
    source_url: str,
    variant: str,
) -> tuple[list[dict[str, Any]], int]:
    if source.kind == "gunsanhak":
        data_rows = [tr for tr in table.find_all("tr")[1:] if tr.find_all("td", recursive=False)]
        # The current official page exposes no stable detail identity or
        # application/education-period contract for these tabular rows.  Empty
        # pages are authoritative; an activated unsupported shape fails closed
        # instead of inventing unstable content-hash courses.
        return [], len(data_rows)
    if source.kind == "culture":
        return _parse_culture_page(
            target,
            source,
            table,
            page=page,
            source_url=source_url,
            variant=variant,
        )
    return _parse_standard_page(
        target, source, table, page=page, source_url=source_url
    )


def _full_range(value: Any) -> tuple[Optional[date], Optional[date], str]:
    match = _FULL_RANGE_RE.fullmatch(_clean(value))
    if match is None:
        return None, None, ""
    try:
        start = date(*(int(part) for part in match.groups()[:3]))
        end = date(*(int(part) for part in match.groups()[3:]))
    except ValueError:
        return None, None, ""
    if end < start:
        return None, None, ""
    return start, end, f"{start.isoformat()} ~ {end.isoformat()}"


def _detail_pairs(soup: BeautifulSoup) -> Optional[dict[str, str]]:
    tables = soup.select("table.borad_list")
    if len(tables) != 1:
        return None
    result: dict[str, str] = {}
    # The live pages wrap rows in ``tbody`` while fixtures may omit it.  The
    # borad_list table itself has no nested tables, so a descendant row walk is
    # both shape-stable and compatible with either representation.
    for tr in tables[0].find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        for index, cell in enumerate(cells[:-1]):
            if cell.name != "th" or cells[index + 1].name != "td":
                continue
            key = _clean(cell.get_text(" ", strip=True))
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if not key or key in result:
                return None
            result[key] = value
    return result


def _description(soup: BeautifulSoup) -> str:
    parts: list[str] = []
    for label in ("강사소개", "강좌 상세내용"):
        for cell in soup.find_all("th"):
            if _clean(cell.get_text(" ", strip=True)) != label:
                continue
            value = cell.find_next_sibling("td")
            if value is not None:
                text = _clean(value.get_text(" ", strip=True))
                if text and text != "-":
                    parts.append(text)
            break
    return _clean(" ".join(parts))


def _application_evidence(node: Any) -> str:
    return _clean(
        " ".join(
            _clean(value)
            for value in (
                node.get_text(" ", strip=True) if hasattr(node, "get_text") else "",
                node.get("value") if hasattr(node, "get") else "",
                node.get("title") if hasattr(node, "get") else "",
                node.get("alt") if hasattr(node, "get") else "",
            )
        )
    )


def _candidate_urls(node: Any, base_url: str) -> list[str]:
    values: list[str] = []
    for attribute in ("href", "formaction", "onclick"):
        raw = _clean(node.get(attribute)) if hasattr(node, "get") else ""
        if not raw:
            continue
        if attribute == "onclick":
            values.extend(
                urljoin(base_url, match)
                for match in re.findall(r"['\"]([^'\"]+)['\"]", raw)
                if match.startswith(("/", "https://"))
            )
        elif not raw.lower().startswith("javascript:"):
            values.append(urljoin(base_url, raw))
    return values


def _safe_application_url(
    candidate: Any,
    detail_url: str,
    identity: str,
) -> str:
    parsed = urlparse(_clean(candidate))
    detail_parsed = urlparse(detail_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity_values = {
        value
        for key, values in query.items()
        if key.lower() in {"idx", "lectureid", "courseid", "id"}
        for value in values
    }
    action = _single_query_value(query, "pm").lower()
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != GUNSAN_HOST
        or parsed.port is not None
        or identity not in identity_values
        or action in {"", "view", "list"}
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or _clean(candidate) == detail_url
        or (parsed.path == detail_parsed.path and query == parse_qs(detail_parsed.query))
    ):
        return ""
    return parsed.geturl()


def _detail_application_contract(
    soup: BeautifulSoup,
    detail_url: str,
    identity: str,
    *,
    application_active: bool,
) -> tuple[str, int]:
    safe: set[str] = set()
    unresolved_identity_controls = 0
    for node in soup.find_all(["a", "button", "input"]):
        evidence = _application_evidence(node)
        if not any(label in evidence for label in _APPLICATION_LABELS):
            continue
        candidates = _candidate_urls(node, detail_url)
        matched_identity = identity in _clean(node)
        for candidate in candidates:
            resolved = _safe_application_url(candidate, detail_url, identity)
            if resolved:
                safe.add(resolved)
                matched_identity = True
            elif identity in candidate:
                matched_identity = True
                unresolved_identity_controls += 1
        if matched_identity and not candidates:
            unresolved_identity_controls += 1
    if not application_active:
        return "", 0
    if len(safe) == 1 and not unresolved_identity_controls:
        return next(iter(safe)), 0
    return "", unresolved_identity_controls + (len(safe) if len(safe) > 1 else 0)


def _capacity_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    numbers = [int(item.replace(",", "")) for item in re.findall(r"[\d,]+", _clean(value))]
    if len(numbers) < 2:
        return None, None
    current, total = numbers[0], numbers[1]
    if current < 0 or total < 0 or current > total:
        return None, None
    return current, total


def _combined_list_capacity(value: Any) -> tuple[Optional[int], Optional[int]]:
    pairs = [
        (int(left.replace(",", "")), int(right.replace(",", "")))
        for left, right in re.findall(r"([\d,]+)\s*/\s*([\d,]+)", _clean(value))
    ]
    if not pairs or any(current < 0 or total < 0 or current > total for current, total in pairs):
        return None, None
    return sum(current for current, _ in pairs), sum(total for _, total in pairs)


def _month_day_signature(
    education_start: date,
    education_end: date,
    apply_start: date,
    apply_end: date,
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (value.month, value.day)
        for value in (education_start, education_end, apply_start, apply_end)
    )


def _enrich_detail(
    row: dict[str, Any],
    soup: BeautifulSoup,
    reference_day: date,
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("detail_id"))
    pairs = _detail_pairs(soup)
    if pairs is None:
        return [f"detail {identity}: detail table contract mismatch"]
    missing = sorted(_DETAIL_REQUIRED_KEYS - set(pairs))
    if missing:
        return [f"detail {identity}: missing detail keys {','.join(missing)}"]
    if _normalized(pairs.get("강좌명")) != _normalized(row.get("title")):
        return [f"detail {identity}: detail/list title mismatch"]

    education_start, education_end, period = _full_range(pairs.get("강의기간"))
    apply_start, apply_end, apply_period = _full_range(pairs.get("접수기간"))
    if None in {education_start, education_end, apply_start, apply_end}:
        return [f"detail {identity}: invalid full-year education/application period"]
    assert education_start is not None and education_end is not None
    assert apply_start is not None and apply_end is not None
    list_signature = tuple(row.get("raw_fields", {}).get("list_period_signature") or ())
    if list_signature and list_signature != _month_day_signature(
        education_start, education_end, apply_start, apply_end
    ):
        return [f"detail {identity}: detail/list period mismatch"]

    source_status = _clean(row.get("raw_fields", {}).get("source_status"))
    cancelled = any(token in source_status for token in _CANCELLED_TOKENS)
    source_closed = any(token in source_status for token in _SOURCE_CLOSED_TOKENS)
    application_active = apply_start <= reference_day <= apply_end and not cancelled and not source_closed
    application_url, unsafe_controls = _detail_application_contract(
        soup,
        _clean(row.get("raw_url")),
        identity,
        application_active=application_active,
    )
    if unsafe_controls:
        return [f"detail {identity}: ambiguous or unsafe application control"]
    if cancelled:
        status = "CANCELLED"
    elif reference_day < apply_start:
        status = "SCHEDULED"
    elif application_active and application_url:
        status = "OPEN"
    else:
        status = "CLOSED"

    capacity_current, capacity_total = _capacity_pair(pairs.get("모집정원"))
    if capacity_current is None or capacity_total is None:
        return [f"detail {identity}: invalid capacity"]
    # Some centre programmes publish an online capacity of ``0 / 0`` in the
    # detail while the list separately carries a non-zero walk-in channel.
    # Use that explicit channel sum only for the zero-detail case.
    if capacity_total == 0:
        list_current, list_total = _combined_list_capacity(row.get("capacity"))
        if list_current is not None and list_total is not None and list_total > 0:
            capacity_current, capacity_total = list_current, list_total
    venue = _clean(pairs.get("교육장소"))
    fee = _clean(pairs.get("수강료"))
    weekday = _clean(pairs.get("강좌요일"))
    if not venue or not fee or not weekday:
        return [f"detail {identity}: empty core detail field"]

    row.update(
        {
            "period": period,
            "start_date": education_start.isoformat(),
            "end_date": education_end.isoformat(),
            "apply_period": apply_period,
            "apply_start": apply_start.isoformat(),
            "apply_end": apply_end.isoformat(),
            "venue_name": venue,
            "fee": fee,
            "capacity": pairs.get("모집정원"),
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "status": status,
            "application_url": application_url,
            "reservation_available": bool(application_url and status == "OPEN"),
            "application_type": (
                "ONLINE_RESERVATION" if application_url else "INFORMATION_ONLY"
            ),
            "phone": _clean(pairs.get("문의전화")),
        }
    )
    if not row.get("target") and pairs.get("대상"):
        row["target"] = _clean(pairs.get("대상"))
    if not row.get("schedule_raw"):
        row["schedule_raw"] = _clean(
            " ".join(value for value in (weekday, pairs.get("강의시간")) if value)
        )
    description = _description(soup)
    if description:
        row["description"] = description
    row["raw_fields"] = {
        **row["raw_fields"],
        "detail_pairs": pairs,
        "education_lifecycle_status": source_status,
        "application_control_present": bool(application_url),
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
        "source_count": len(GUNSAN_SOURCES),
        "source_variant_count": len(_source_variants()),
        "required_list_requests": 0,
        "declared_pages_by_source": {},
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
        "duplicate_count": 0,
        "duplicate_identity_count": 0,
        "duplicate_url_count": 0,
        "semantic_duplicate_count": 0,
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


def collect_gunsan_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 200,
    detail_limit: int = 2000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Gunsan education snapshot."""

    meta = _base_meta()
    if not is_gunsan_education_target(target):
        meta["configured_collection_error"] = (
            "target is not the canonical or audited legacy Gunsan education route"
        )
        return [], GUNSAN_PARSER, meta
    if fetcher is None or session_factory is None:
        meta["configured_collection_error"] = (
            "managed fetcher and session_factory injection are required"
        )
        return [], GUNSAN_PARSER, meta
    if max_pages < len(_source_variants()) or detail_limit < 0:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "max_pages/detail_limit are invalid for fixed fan-out"
        return [], GUNSAN_PARSER, meta

    reference_day = _today(today)
    errors: list[str] = []
    current_session: Any = None
    first_pages: dict[str, BeautifulSoup] = {}
    declarations: dict[str, tuple[GunsanSource, str, int]] = {}
    all_rows: list[dict[str, Any]] = []
    try:
        current_session = session_factory()
        for source, variant in _source_variants():
            key = source.code if not variant else f"{source.code}:{variant}"
            url = gunsan_list_url(
                source, 1, variant=variant, reference_year=reference_day.year
            )
            soup = _fetch(fetcher, current_session, url, timeout)
            first_pages[key] = soup
            table = _catalogue_table(soup, source, reference_day.year)
            if table is None:
                errors.append(f"{key}: first-page catalogue table contract mismatch")
                continue
            declared_last, active = _page_contract(soup, source)
            if declared_last < 1 or active not in {0, 1}:
                errors.append(f"{key}: first-page pagination contract mismatch")
                continue
            declarations[key] = (source, variant, declared_last)
            meta["declared_pages_by_source"][key] = declared_last
            meta["sentinel_pages"][key] = declared_last + 1
            meta["pagination_detected"] = bool(
                meta["pagination_detected"] or declared_last > 1
            )

        if len(declarations) != len(_source_variants()):
            errors.append("fixed fan-out discovery is incomplete")
        required_list_requests = sum(last + 1 for _, _, last in declarations.values())
        meta["required_list_requests"] = required_list_requests
        if required_list_requests > max_pages:
            meta["source_cap_reached"] = True
            errors.append(
                f"max_pages cap allows {max_pages} of {required_list_requests} required list requests"
            )

        if not errors:
            for key, (source, variant, declared_last) in declarations.items():
                source_rows: list[dict[str, Any]] = []
                for page in range(1, declared_last + 2):
                    url = gunsan_list_url(
                        source,
                        page,
                        variant=variant,
                        reference_year=reference_day.year,
                    )
                    soup = first_pages[key] if page == 1 else _fetch(
                        fetcher, current_session, url, timeout
                    )
                    meta["pages"] += 1
                    table = _catalogue_table(soup, source, reference_day.year)
                    if table is None:
                        errors.append(f"{key} page {page}: catalogue table contract mismatch")
                        continue
                    observed_last, active = _page_contract(soup, source)
                    if page <= declared_last:
                        if observed_last != declared_last:
                            errors.append(
                                f"{key} page {page}: declared final page changed to {observed_last}"
                            )
                        if declared_last > 1 and active != page:
                            errors.append(
                                f"{key} page {page}: active pagination marker is {active}"
                            )
                    elif active:
                        errors.append(f"{key}: sentinel page has active pagination marker")
                    rows, malformed = _parse_list_page(
                        target,
                        source,
                        table,
                        page=page,
                        source_url=url,
                        variant=variant,
                    )
                    meta["page_counts"][f"{key}:{page}"] = len(rows)
                    if malformed:
                        errors.append(
                            f"{key} page {page}: {malformed} malformed or unsupported catalogue rows"
                        )
                    if page < declared_last and len(rows) != GUNSAN_PAGE_SIZE:
                        errors.append(
                            f"{key} page {page}: expected {GUNSAN_PAGE_SIZE} rows before final page"
                        )
                    if page == declared_last and declared_last > 1 and not rows:
                        errors.append(f"{key}: declared final page is empty")
                    if page == declared_last + 1 and rows:
                        errors.append(f"{key}: sentinel page is not empty")
                    if page <= declared_last:
                        source_rows.extend(rows)

                if source.sequence_column and source_rows:
                    sequence = [
                        int(row["raw_fields"]["source_sequence"])
                        for row in source_rows
                    ]
                    if sequence != list(range(1, len(source_rows) + 1)):
                        errors.append(f"{key}: source sequence is not continuous from one")
                meta["source_counts"][key] = len(source_rows)
                all_rows.extend(source_rows)

        meta["source_total"] = meta["source_rows"] = len(all_rows)
        identities = [_clean(row["raw_fields"].get("detail_id")) for row in all_rows]
        course_ids = [_clean(row.get("provider_course_id")) for row in all_rows]
        raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
        meta["duplicate_identity_count"] = len(identities) - len(set(identities))
        meta["duplicate_count"] = len(course_ids) - len(set(course_ids))
        meta["duplicate_url_count"] = len(raw_urls) - len(set(raw_urls))
        if meta["duplicate_identity_count"]:
            errors.append(f"{meta['duplicate_identity_count']} duplicate detail identities")
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
                identity = row["raw_fields"]["detail_id"]
                source = next(
                    item for item in GUNSAN_SOURCES
                    if item.code == row["raw_fields"]["source_code"]
                )
                meta["detail_attempts"] += 1
                try:
                    soup = _fetch(
                        fetcher,
                        current_session,
                        gunsan_detail_url(source, identity),
                        timeout,
                    )
                    detail_errors = _enrich_detail(row, soup, reference_day)
                    if detail_errors:
                        meta["detail_errors"] += 1
                        errors.extend(detail_errors)
                        continue
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
            and len(declarations) == len(_source_variants())
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
                "the complete official Gunsan fan-out has no current/future courses"
                if all_rows
                else "the complete official Gunsan fan-out is empty"
            )
        meta["configured_collection_error"] = "; ".join(errors)
        return (
            current_rows if meta["snapshot_complete"] else [],
            GUNSAN_PARSER,
            meta,
        )
    except Exception as exc:
        errors.append(f"fixed fan-out fetch/parse failed ({type(exc).__name__})")
        meta["configured_collection_error"] = "; ".join(errors)
        return [], GUNSAN_PARSER, meta
    finally:
        _close_quietly(current_session)


collect = collect_gunsan_education_courses


__all__ = [
    "GUNSAN_CANONICAL_URL",
    "GUNSAN_EXCLUDED_STALE_NOTICE_URLS",
    "GUNSAN_HOST",
    "GUNSAN_LEGACY_ENTRY_URL",
    "GUNSAN_MUNICIPALITY_CODE",
    "GUNSAN_MUNICIPALITY_NAME",
    "GUNSAN_OWNERSHIP_ALIAS_URLS",
    "GUNSAN_PARSER",
    "GUNSAN_PROVIDER",
    "GUNSAN_SOURCES",
    "GunsanSource",
    "collect",
    "collect_gunsan_education_courses",
    "gunsan_detail_url",
    "gunsan_list_url",
    "is_gunsan_education_target",
    "is_gunsan_excluded_notice_target",
    "is_target",
]
