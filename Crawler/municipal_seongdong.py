"""Fail-closed collector for Seongdong-gu's integrated booking catalogues.

The retained provider used to own only the agency-305 lifelong-learning
subset.  The canonical owner now walks the municipality's complete
``education/course`` and ``experience/tour`` ledgers.  The historical
``eduMngNo`` provider-course identities are intentionally preserved, so the
larger snapshot updates the existing rows instead of creating a second owner.

Only numbered list pages and public detail pages are requested.  Login,
application, applicant, attachment and download endpoints are outside the URL
allowlist.  Pagination, source numbering, public details, dates and category
classification must all complete before any row is returned.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# Keep the production provider identity even though its canonical route grows
# from the old Dokseodang subset into the official integrated owner.
SEONGDONG_INTEGRATED_PROVIDER = "MUNI_DOKSEODANG_SD_GO_KR_A8C20229"
SEONGDONG_DOKSEODANG_PROVIDER = SEONGDONG_INTEGRATED_PROVIDER
SEONGDONG_EDUCATION_ALL_URL = (
    "https://www.sd.go.kr/booking/webEduList.do?key=4833"
)
SEONGDONG_EXPERIENCE_URL = (
    "https://www.sd.go.kr/booking/webExcursionsProgramList.do?key=4836"
)
SEONGDONG_INTEGRATED_URL = SEONGDONG_EDUCATION_ALL_URL
SEONGDONG_DOKSEODANG_URL = SEONGDONG_INTEGRATED_URL
SEONGDONG_DOKSEODANG_LEGACY_URL = (
    "https://www.sd.go.kr/booking/webEduList2.do?key=4916"
)

SEONGDONG_HOST = "www.sd.go.kr"
SEONGDONG_EDUCATION_LIST_PATH = "/booking/webEduList.do"
SEONGDONG_EDUCATION_DETAIL_PATH = "/booking/webEduDetail.do"
SEONGDONG_EXPERIENCE_LIST_PATH = "/booking/webExcursionsProgramList.do"
SEONGDONG_EXPERIENCE_DETAIL_PATH = "/booking/webExcursionsProgramView.do"
SEONGDONG_EDUCATION_KEY = "4833"
SEONGDONG_EXPERIENCE_KEY = "4836"

# Compatibility names used by older tests/importers.
SEONGDONG_LIST_PATH = SEONGDONG_EDUCATION_LIST_PATH
SEONGDONG_DETAIL_PATH = SEONGDONG_EDUCATION_DETAIL_PATH
SEONGDONG_CATALOGUE_KEY = SEONGDONG_EDUCATION_KEY
SEONGDONG_AGENCY_ID = "305"
SEONGDONG_DOKSEODANG_BRANCH = "평생학습관"

SEONGDONG_PAGE_SIZE = 9
SEONGDONG_MAX_DETAIL_WORKERS = 8
SEONGDONG_PARSER = (
    "seongdong_integrated_education_experience_complete_pages+"
    "current_public_details+locked_classification+no_application_endpoints"
)
SEONGDONG_MUNICIPALITY_CODE = "1120000000"
SEONGDONG_MUNICIPALITY_NAME = "서울특별시 성동구"

SPORTS_HOST = "sports.happysd.or.kr"
CCIC_HOST = "ccic.sd.go.kr"
FIFTY_PLUS_HOST = "www.50plus.or.kr"

NATIVE_EDUCATION_CATEGORIES = frozenset(
    {
        "스포츠",
        "기타",
        "음악",
        "교양",
        "미술",
        "외국어",
        "한문/서예",
        "정보화",
        "진로/진학",
        "국어/논술",
        "문화",
    }
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d+")
_CODE_RE = re.compile(r"[A-Za-z0-9_-]+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_NON_COURSE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("test_record", re.compile(r"^\W*(?:점검|테스트)\d*\W*$")),
    ("notice", re.compile(r"공지(?:사항)?|(?:^|[\[（(])안내(?:문)?(?:[\]）)]|$)")),
    ("event", re.compile(r"행사")),
    ("counselling", re.compile(r"상담")),
    ("recruitment", re.compile(r"모집|서포터즈")),
    (
        "facility",
        re.compile(r"대관|사물함|공간\s*이용|시설\s*(?:이용|예약)|장소\s*예약"),
    ),
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _title_text(value: Any) -> str:
    # A small number of FMCS titles expose a literal escaped ``<br>`` in the
    # municipal mirror while their public detail renders the same token as a
    # line break.
    return _clean(re.sub(r"<\s*br\s*/?\s*>", " ", str(value or ""), flags=re.I))


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


def _exact_https_url(value: Any, host: str, path: str, query: dict[str, list[str]]) -> bool:
    parsed = urlparse(_clean(value))
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == host
        and parsed.port is None
        and parsed.path == path
        and parse_qs(parsed.query, keep_blank_values=True) == query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_seongdong_integrated_target(target: Any) -> bool:
    return bool(_catalogue_kind(target))


def _catalogue_kind(target: Any) -> str:
    if _provider(target) != SEONGDONG_INTEGRATED_PROVIDER:
        return ""
    target_url = _target_url(target)
    if _exact_https_url(
        target_url,
        SEONGDONG_HOST,
        SEONGDONG_EDUCATION_LIST_PATH,
        {"key": [SEONGDONG_EDUCATION_KEY]},
    ):
        return "education"
    if _exact_https_url(
        target_url,
        SEONGDONG_HOST,
        SEONGDONG_EXPERIENCE_LIST_PATH,
        {"key": [SEONGDONG_EXPERIENCE_KEY]},
    ):
        return "experience"
    return ""


is_seongdong_dokseodang_target = is_seongdong_integrated_target
is_target = is_seongdong_integrated_target


def _positive_int(value: Any) -> str:
    raw = _clean(value)
    if not _IDENTITY_RE.fullmatch(raw) or int(raw) < 1:
        return ""
    return str(int(raw))


def seongdong_education_list_url(page: Any) -> str:
    current = _positive_int(page)
    if not current:
        return ""
    return (
        f"https://{SEONGDONG_HOST}{SEONGDONG_EDUCATION_LIST_PATH}?"
        + urlencode({"key": SEONGDONG_EDUCATION_KEY, "cpn": current})
    )


def seongdong_experience_list_url(page: Any) -> str:
    current = _positive_int(page)
    if not current:
        return ""
    return (
        f"https://{SEONGDONG_HOST}{SEONGDONG_EXPERIENCE_LIST_PATH}?"
        + urlencode(
            {
                "key": SEONGDONG_EXPERIENCE_KEY,
                "pageUnit": SEONGDONG_PAGE_SIZE,
                "pageIndex": current,
            }
        )
    )


def seongdong_education_detail_url(identity: Any, *, page: Any = 1) -> str:
    current_identity = _positive_int(identity)
    current_page = _positive_int(page)
    if not current_identity or not current_page:
        return ""
    return (
        f"https://{SEONGDONG_HOST}{SEONGDONG_EDUCATION_DETAIL_PATH}?"
        + urlencode(
            {
                "key": SEONGDONG_EDUCATION_KEY,
                "eduMngNo": current_identity,
                "cpn": current_page,
            }
        )
    )


def seongdong_experience_detail_url(identity: Any) -> str:
    current_identity = _positive_int(identity)
    if not current_identity:
        return ""
    return (
        f"https://{SEONGDONG_HOST}{SEONGDONG_EXPERIENCE_DETAIL_PATH}?"
        + urlencode(
            {
                "key": SEONGDONG_EXPERIENCE_KEY,
                "programNumber": current_identity,
            }
        )
    )


# Compatibility builders now point at the integrated education ledger.
seongdong_list_url = seongdong_education_list_url
seongdong_detail_url = seongdong_education_detail_url


def _single_query(parsed: Any, keys: set[str]) -> dict[str, str] | None:
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != keys or any(len(values) != 1 for values in query.values()):
        return None
    return {key: values[0] for key, values in query.items()}


def _canonical_education_detail(
    href: Any,
    current_url: str,
    page: int,
) -> tuple[str, str, str, dict[str, str]]:
    parsed = urlparse(urljoin(current_url, _clean(href)))
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.port is not None or parsed.params or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("education row contains a non-public detail URL")

    if host == SEONGDONG_HOST and parsed.scheme.lower() == "https" and parsed.path == SEONGDONG_EDUCATION_DETAIL_PATH:
        query = _single_query(parsed, {"key", "eduMngNo", "cpn"})
        if (
            query is None
            or query["key"] != SEONGDONG_EDUCATION_KEY
            or not _positive_int(query["eduMngNo"])
            or not _positive_int(query["cpn"])
        ):
            raise ValueError("native education detail identity is invalid")
        identity = _positive_int(query["eduMngNo"])
        return (
            "native_education",
            identity,
            seongdong_education_detail_url(identity, page=page),
            {"eduMngNo": identity},
        )

    if host == SPORTS_HOST and parsed.scheme.lower() in {"http", "https"} and parsed.path == "/fmcs/191":
        query = _single_query(parsed, {"action", "comcd", "classcd", "type"})
        if (
            query is None
            or query["action"] != "read"
            or query["type"] != "R"
            or not _CODE_RE.fullmatch(query["comcd"])
            or not _CODE_RE.fullmatch(query["classcd"])
        ):
            raise ValueError("sports education detail identity is invalid")
        identity = f"{query['comcd']}:{query['classcd']}:{query['type']}"
        canonical = "https://" + SPORTS_HOST + "/fmcs/191?" + urlencode(query)
        return "sports_education", identity, canonical, query

    if host == CCIC_HOST and parsed.scheme.lower() == "https" and parsed.path == "/main/main.php":
        query = _single_query(
            parsed,
            {"categoryid", "menuid", "groupid", "board", "no"},
        )
        if (
            query is None
            or query["categoryid"] != "06"
            or query["groupid"] != "02"
            or query["board"] != "view"
            or query["menuid"] not in {"03", "04", "05"}
            or not _positive_int(query["no"])
        ):
            raise ValueError("childcare education detail identity is invalid")
        identity = f"{query['menuid']}:{_positive_int(query['no'])}"
        canonical = "https://" + CCIC_HOST + "/main/main.php?" + urlencode(query)
        return "ccic_education", identity, canonical, query

    if host in {"50plus.or.kr", FIFTY_PLUS_HOST} and parsed.scheme.lower() == "https" and parsed.path == "/sdc/education-detail.do":
        query = _single_query(parsed, {"id"})
        if query is None or not _positive_int(query["id"]):
            raise ValueError("50plus education detail identity is invalid")
        identity = _positive_int(query["id"])
        canonical = (
            "https://"
            + FIFTY_PLUS_HOST
            + "/sdc/education-detail.do?"
            + urlencode({"id": identity})
        )
        return "fifty_plus_education", identity, canonical, {"id": identity}

    raise ValueError("education row points outside the audited public detail families")


def _experience_identity(
    href: Any, current_url: str
) -> tuple[str, str, str, str]:
    parsed = urlparse(urljoin(current_url, _clean(href)))
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme.lower() == "https"
        and host == CCIC_HOST
        and parsed.port is None
        and parsed.path == "/main/main.php"
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        query = _single_query(parsed, {"categoryid", "menuid", "groupid"})
        audited_facilities = {("06", "01"), ("08", "00")}
        if (
            query
            and query["categoryid"] == "06"
            and (query["menuid"], query["groupid"]) in audited_facilities
        ):
            identity = f"ccicFacility:{query['menuid']}:{query['groupid']}"
            canonical = "https://" + CCIC_HOST + "/main/main.php?" + urlencode(query)
            # These source rows are links to a facility-reservation landing
            # menu, not individual experience details.
            return identity, canonical, "external_experience_facility", "facility"
    if (
        parsed.scheme.lower() != "https"
        or host != SEONGDONG_HOST
        or parsed.port is not None
        or parsed.path != SEONGDONG_EXPERIENCE_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("experience row contains a non-public detail URL")
    query = _single_query(parsed, {"key", "programNumber"})
    if (
        query is None
        or query["key"] != SEONGDONG_EXPERIENCE_KEY
        or not _positive_int(query["programNumber"])
    ):
        raise ValueError("experience detail identity is invalid")
    identity = _positive_int(query["programNumber"])
    return identity, seongdong_experience_detail_url(identity), "experience", ""


def _allowed_public_url(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme.lower() != "https"
        or parsed.port is not None
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)

    if host == SEONGDONG_HOST and parsed.path == SEONGDONG_EDUCATION_LIST_PATH:
        return (
            set(query) == {"key", "cpn"}
            and query["key"] == [SEONGDONG_EDUCATION_KEY]
            and len(query["cpn"]) == 1
            and bool(_positive_int(query["cpn"][0]))
        )
    if host == SEONGDONG_HOST and parsed.path == SEONGDONG_EXPERIENCE_LIST_PATH:
        return (
            set(query) == {"key", "pageUnit", "pageIndex"}
            and query["key"] == [SEONGDONG_EXPERIENCE_KEY]
            and query["pageUnit"] == [str(SEONGDONG_PAGE_SIZE)]
            and len(query["pageIndex"]) == 1
            and bool(_positive_int(query["pageIndex"][0]))
        )
    if host == SEONGDONG_HOST and parsed.path == SEONGDONG_EDUCATION_DETAIL_PATH:
        return (
            set(query) == {"key", "eduMngNo", "cpn"}
            and query["key"] == [SEONGDONG_EDUCATION_KEY]
            and len(query["eduMngNo"]) == len(query["cpn"]) == 1
            and bool(_positive_int(query["eduMngNo"][0]))
            and bool(_positive_int(query["cpn"][0]))
        )
    if host == SEONGDONG_HOST and parsed.path == SEONGDONG_EXPERIENCE_DETAIL_PATH:
        return (
            set(query) == {"key", "programNumber"}
            and query["key"] == [SEONGDONG_EXPERIENCE_KEY]
            and len(query["programNumber"]) == 1
            and bool(_positive_int(query["programNumber"][0]))
        )
    if host == SPORTS_HOST and parsed.path == "/fmcs/191":
        single = _single_query(parsed, {"action", "comcd", "classcd", "type"})
        return bool(
            single
            and single["action"] == "read"
            and single["type"] == "R"
            and _CODE_RE.fullmatch(single["comcd"])
            and _CODE_RE.fullmatch(single["classcd"])
        )
    if host == CCIC_HOST and parsed.path == "/main/main.php":
        single = _single_query(parsed, {"categoryid", "menuid", "groupid", "board", "no"})
        return bool(
            single
            and single["categoryid"] == "06"
            and single["menuid"] in {"03", "04", "05"}
            and single["groupid"] == "02"
            and single["board"] == "view"
            and _positive_int(single["no"])
        )
    if host == FIFTY_PLUS_HOST and parsed.path == "/sdc/education-detail.do":
        single = _single_query(parsed, {"id"})
        return bool(single and _positive_int(single["id"]))
    return False


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


def _validate_response(response: Any) -> None:
    status = getattr(response, "status_code", 200)
    try:
        status_code = int(status)
    except (TypeError, ValueError) as exc:
        raise ValueError("HTTP response status is invalid") from exc
    if 300 <= status_code < 400:
        raise ValueError("HTTP redirects are not accepted")
    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    return current_session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    _validate_response(value)
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise ValueError("empty HTTP response")
    return BeautifulSoup(content, "lxml")


def _fetch(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    if not _allowed_public_url(url):
        raise ValueError(f"URL is outside the public list/detail allowlist: {url}")
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
        try:
            result.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    return result


def _date_pair(value: Any) -> tuple[date, date] | None:
    values = _date_tokens(value)
    if len(values) != 2 or values[1] < values[0]:
        return None
    return values[0], values[1]


def _range_pairs(value: Any) -> list[tuple[date, date]]:
    values = _date_tokens(value)
    if len(values) % 2:
        return []
    pairs = list(zip(values[::2], values[1::2]))
    return [pair for pair in pairs if pair[1] >= pair[0]]


def _capacity_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    match = re.search(r"([\d,]+)\s*/\s*([\d,]+)", _clean(value))
    if not match:
        return None, None
    return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))


def _first_int(value: Any) -> Optional[int]:
    match = re.search(r"[\d,]+", _clean(value))
    return int(match.group(0).replace(",", "")) if match else None


def _table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for tr in soup.select("table tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        for index, cell in enumerate(cells[:-1]):
            if cell.name == "th" and cells[index + 1].name == "td":
                key = _clean(cell.get_text(" ", strip=True))
                value = _clean(cells[index + 1].get_text(" ", strip=True))
                if key:
                    pairs[key] = value
    return pairs


def _visible_pages(soup: BeautifulSoup, source: str) -> set[int]:
    if source == "education":
        base = SEONGDONG_EDUCATION_ALL_URL
        path = SEONGDONG_EDUCATION_LIST_PATH
        parameter = "cpn"
        key = SEONGDONG_EDUCATION_KEY
    else:
        base = SEONGDONG_EXPERIENCE_URL
        path = SEONGDONG_EXPERIENCE_LIST_PATH
        parameter = "pageIndex"
        key = SEONGDONG_EXPERIENCE_KEY
    pages = {1}
    for anchor in soup.select(".p-pagination a[href], .pagination a[href], .paging a[href]"):
        parsed = urlparse(urljoin(base, anchor.get("href", "")))
        query = parse_qs(parsed.query)
        if (
            (parsed.hostname or "").rstrip(".").lower() != SEONGDONG_HOST
            or parsed.path != path
            or query.get("key") != [key]
        ):
            continue
        raw = query.get(parameter, [""])[0]
        if _positive_int(raw):
            pages.add(int(raw))
    return pages


def _parse_education_page(soup: BeautifulSoup, page: int) -> list[dict[str, Any]]:
    current_url = seongdong_education_list_url(page)
    rows: list[dict[str, Any]] = []
    for tr in soup.select("table tbody tr"):
        cells = tr.find_all("td", recursive=False)
        raw_number = _clean(cells[0].get_text(" ", strip=True)).replace(",", "") if cells else ""
        if not raw_number.isdigit():
            continue
        if len(cells) != 7:
            raise ValueError(f"education page {page} contains a malformed numbered row")
        link = cells[1].find("a", href=True)
        if link is None:
            raise ValueError(f"education page {page} contains a numbered row without detail")
        title = _title_text(link.get_text(" ", strip=True))
        dates = _date_tokens(cells[2].get_text(" ", strip=True))
        if not title or len(dates) != 4:
            raise ValueError(f"education page {page} contains an invalid course row")
        apply_start, apply_end, start, end = dates
        if apply_end < apply_start or end < start:
            raise ValueError(f"education page {page} contains a reversed date range")
        capacity = _clean(cells[6].get_text(" ", strip=True))
        capacity_current, capacity_total = _capacity_pair(capacity)
        if capacity_current is None or capacity_total is None:
            raise ValueError(f"education page {page} contains an invalid capacity")
        kind, identity, detail_url, identity_fields = _canonical_education_detail(
            link.get("href"), current_url, page
        )
        rows.append(
            {
                "source": "education",
                "source_number": int(raw_number),
                "detail_kind": kind,
                "identity": identity,
                "identity_fields": identity_fields,
                "detail_url": detail_url,
                "title": title,
                "page": page,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "start": start,
                "end": end,
                "venue": _clean(cells[3].get_text(" ", strip=True)),
                "target": _clean(cells[4].get_text(" ", strip=True)),
                "status": _clean(cells[5].get_text(" ", strip=True)),
                "capacity": capacity,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "list_cells": [_clean(cell.get_text(" ", strip=True)) for cell in cells],
            }
        )
    return rows


def _parse_experience_page(soup: BeautifulSoup, page: int) -> list[dict[str, Any]]:
    current_url = seongdong_experience_list_url(page)
    rows: list[dict[str, Any]] = []
    for tr in soup.select("table tbody tr"):
        cells = tr.find_all("td", recursive=False)
        raw_number = _clean(cells[0].get_text(" ", strip=True)).replace(",", "") if cells else ""
        if not raw_number.isdigit():
            continue
        if len(cells) != 10:
            raise ValueError(f"experience page {page} contains a malformed numbered row")
        link = cells[2].find("a", href=True)
        if link is None:
            raise ValueError(f"experience page {page} contains a numbered row without detail")
        title = _clean(link.get_text(" ", strip=True))
        dates = _date_tokens(cells[3].get_text(" ", strip=True))
        if not title or len(dates) != 4:
            raise ValueError(f"experience page {page} contains an invalid programme row")
        apply_start, apply_end, start, end = dates
        if apply_end < apply_start or end < start:
            raise ValueError(f"experience page {page} contains a reversed date range")
        identity, detail_url, detail_kind, preclassified_exclusion = _experience_identity(
            link.get("href"), current_url
        )
        capacity = _clean(cells[7].get_text(" ", strip=True))
        capacity_total = _first_int(capacity)
        if capacity_total is None:
            raise ValueError(f"experience page {page} contains an invalid capacity")
        rows.append(
            {
                "source": "experience",
                "source_number": int(raw_number),
                "detail_kind": detail_kind,
                "identity": identity,
                "identity_fields": {"programNumber": identity},
                "detail_url": detail_url,
                "preclassified_exclusion": preclassified_exclusion,
                "title": title,
                "page": page,
                "year": _clean(cells[1].get_text(" ", strip=True)),
                "apply_start": apply_start,
                "apply_end": apply_end,
                "start": start,
                "end": end,
                "venue": _clean(cells[4].get_text(" ", strip=True)),
                "target": _clean(cells[5].get_text(" ", strip=True)),
                "fee": _clean(cells[6].get_text(" ", strip=True)),
                "capacity": capacity,
                "capacity_current": None,
                "capacity_total": capacity_total,
                "selection": _clean(cells[8].get_text(" ", strip=True)),
                "status": _clean(cells[9].get_text(" ", strip=True)),
                "list_cells": [_clean(cell.get_text(" ", strip=True)) for cell in cells],
            }
        )
    return rows


def _status_code(value: Any) -> str:
    status = _clean(value)
    if status in {"접수중", "추가모집"}:
        return "OPEN"
    if status == "접수대기":
        return "SCHEDULED"
    if status == "대기자접수":
        return "WAITLIST"
    return "CLOSED"


def _non_course_reason(title: Any) -> str:
    current = _clean(title)
    for reason, pattern in _NON_COURSE_PATTERNS:
        if pattern.search(current):
            return reason
    return ""


def _branch_code(prefix: str, value: Any) -> str:
    digest = hashlib.sha1(_clean(value).encode("utf-8")).hexdigest()[:10].upper()
    return f"SEONGDONG_{prefix}_{digest}"


def _required_pairs(pairs: Mapping[str, str], required: Iterable[str], label: str) -> None:
    missing = [key for key in required if not _clean(pairs.get(key))]
    if missing:
        raise ValueError(f"{label} is missing {', '.join(missing)}")


def _validate_title_in_document(item: Mapping[str, Any], soup: BeautifulSoup, label: str) -> None:
    title = _clean(item["title"])
    document_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if not document_title or title not in document_title:
        raise ValueError(f"{label} title mismatch for {item['identity']}")


def _validate_standard_dates(item: Mapping[str, Any], pairs: Mapping[str, str], label: str) -> None:
    if _date_pair(pairs["접수기간"]) != (item["apply_start"], item["apply_end"]):
        raise ValueError(f"{label} application dates mismatch for {item['identity']}")
    if _date_pair(pairs["운영기간"]) != (item["start"], item["end"]):
        raise ValueError(f"{label} programme dates mismatch for {item['identity']}")


def _safe_detail_fields(pairs: Mapping[str, str]) -> dict[str, str]:
    allowed = (
        "구분",
        "운영기관",
        "대상",
        "장소",
        "주소",
        "접수기간",
        "신청기간",
        "운영기간",
        "교육기간",
        "행사일시",
        "교육일시",
        "운영시간",
        "운영요일",
        "시간/요일",
        "모집인원",
        "모집인원(회차별)",
        "이용요금",
        "재료비/교재비",
        "선별방법",
        "예약방법",
        "접수방식",
        "운영센터",
        "강습장소",
        "정원",
        "상태",
        "행사장소",
        "행사대상",
        "교육장소",
        "교육대상",
    )
    return {key: _clean(pairs[key]) for key in allowed if _clean(pairs.get(key))}


def _build_row(
    target: Any,
    item: Mapping[str, Any],
    *,
    provider_id_suffix: str,
    branch: str,
    branch_code: str,
    category: str,
    program_type: str,
    domain_category: str,
    service_group: str,
    venue: str,
    target_text: str,
    fee: str,
    schedule: str,
    application_method: str,
    selection_method: str,
    safe_pairs: Mapping[str, str],
) -> dict[str, Any]:
    status = _clean(item["status"])
    status_code = _status_code(status)
    source = _clean(item["source"])
    return {
        "provider": _provider(target),
        "provider_course_id": f"{SEONGDONG_INTEGRATED_PROVIDER}:{provider_id_suffix}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": _clean(item["title"]),
        "branch": branch,
        "branch_code": branch_code,
        "preserve_branch": True,
        "branch_url": (
            SEONGDONG_EDUCATION_ALL_URL if source == "education" else SEONGDONG_EXPERIENCE_URL
        ),
        "program_type": program_type,
        "category": category,
        "raw_url": _clean(item["detail_url"]),
        # The public detail is deliberately used instead of any form action.
        "application_url": _clean(item["detail_url"]),
        "status": status,
        "status_code": status_code,
        "reservation_available": status_code in {"OPEN", "WAITLIST"},
        "period": f"{item['start'].isoformat()} ~ {item['end'].isoformat()}",
        "start_date": item["start"].isoformat(),
        "end_date": item["end"].isoformat(),
        "apply_period": f"{item['apply_start'].isoformat()} ~ {item['apply_end'].isoformat()}",
        "apply_start_date": item["apply_start"].isoformat(),
        "apply_end_date": item["apply_end"].isoformat(),
        "schedule_raw": schedule,
        "target": target_text,
        "capacity": _clean(item["capacity"]),
        "capacity_current": item.get("capacity_current"),
        "capacity_total": item.get("capacity_total"),
        "venue_name": venue,
        "fee": fee,
        "application_method_raw": application_method,
        "selection_method_raw": selection_method,
        "description": " / ".join(f"{key}: {value}" for key, value in safe_pairs.items()),
        "collection_category": "공공예약",
        "domain_category": domain_category,
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": service_group,
        "service_group_policy": "locked",
        "municipality_code": SEONGDONG_MUNICIPALITY_CODE,
        "municipality_full_name": SEONGDONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": SEONGDONG_PARSER,
            "source_catalogue": source,
            "source_number": item["source_number"],
            "source_identity": _clean(item["identity"]),
            "detail_kind": _clean(item["detail_kind"]),
            "identity_fields": dict(item["identity_fields"]),
            "list_cells": list(item["list_cells"]),
            "detail_fields": dict(safe_pairs),
            "classification_locked": True,
        },
    }


def _native_education_detail(
    target: Any, item: Mapping[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any] | None, str]:
    label = f"native education {item['identity']}"
    _validate_title_in_document(item, soup, label)
    pairs = _table_pairs(soup)
    _required_pairs(
        pairs,
        ("구분", "운영기관", "대상", "장소", "접수기간", "운영기간", "모집인원"),
        label,
    )
    category = _clean(pairs["구분"])
    if category not in NATIVE_EDUCATION_CATEGORIES:
        raise ValueError(f"{label} has an unclassified category {category!r}")
    _validate_standard_dates(item, pairs, label)
    detail_capacity = _first_int(pairs["모집인원"])
    if detail_capacity != item["capacity_total"]:
        raise ValueError(f"{label} capacity mismatch")
    reason = _non_course_reason(item["title"])
    if reason:
        return None, reason

    branch = _clean(pairs["운영기관"])
    is_legacy_subset = branch == SEONGDONG_DOKSEODANG_BRANCH
    branch_code = (
        f"SEONGDONG_EDU_AGENCY_{SEONGDONG_AGENCY_ID}"
        if is_legacy_subset
        else _branch_code("EDU_OPERATOR", branch)
    )
    safe = _safe_detail_fields(pairs)
    row = _build_row(
        target,
        item,
        provider_id_suffix=f"eduMngNo:{item['identity']}",
        branch=branch,
        branch_code=branch_code,
        category=category,
        program_type="강좌",
        domain_category="교육·강좌",
        service_group="공공강좌",
        venue=_clean(pairs["장소"]),
        target_text=_clean(pairs["대상"]),
        fee=_clean(pairs.get("이용요금")),
        schedule=" / ".join(
            value
            for value in (_clean(pairs.get("운영요일")), _clean(pairs.get("운영시간")))
            if value
        ),
        application_method=_clean(pairs.get("예약방법")),
        selection_method=_clean(pairs.get("선별방법")),
        safe_pairs=safe,
    )
    if is_legacy_subset:
        row["raw_fields"]["agency_id"] = SEONGDONG_AGENCY_ID
        row["raw_fields"]["historical_owner_identity_preserved"] = True
    return row, ""


def _sports_education_detail(
    target: Any, item: Mapping[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any], str]:
    label = f"sports education {item['identity']}"
    document_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "수강신청" not in document_title or "교육/강좌 상세" not in document_title:
        raise ValueError(f"{label} is not a public course detail")
    pairs = _table_pairs(soup)
    _required_pairs(
        pairs,
        ("강좌명", "운영센터", "교육기간", "시간/요일", "강습장소"),
        label,
    )
    if _title_text(pairs["강좌명"]) != _title_text(item["title"]):
        raise ValueError(f"{label} title mismatch")
    detail_period = _date_pair(pairs["교육기간"])
    if detail_period != (item["start"], item["end"]):
        # FMCS reuses a class code for the next monthly product after the
        # municipal mirror has already closed the previous row.  Publishing
        # either period would bind one identity to two schedules, so the stale
        # closed mirror row is classified out after its public detail is read.
        if (
            detail_period is not None
            and detail_period[0] > item["end"]
            and _clean(item.get("status")) in {"접수마감", "운영중", "폐강", "종료"}
        ):
            return None, "stale_reused_public_detail"
        raise ValueError(f"{label} programme dates mismatch")
    reason = _non_course_reason(item["title"])
    if reason:
        return None, reason
    _required_pairs(pairs, ("교육대상",), label)
    application_ranges = _range_pairs(pairs.get("수강신청 상태"))
    application_state = _clean(pairs.get("수강신청 상태"))
    closed_without_dates = (
        not application_ranges
        and application_state in {"마감", "접수마감", "종료", "현장접수"}
        and _clean(item.get("status")) in {"접수마감", "운영중", "폐강", "종료"}
    )
    if (
        (item["apply_start"], item["apply_end"]) not in application_ranges
        and not closed_without_dates
    ):
        raise ValueError(f"{label} application dates mismatch")
    if not _clean(pairs.get("접수방식")) and not closed_without_dates:
        raise ValueError(f"{label} is missing 접수방식")
    branch = _clean(pairs["운영센터"]).split("/", 1)[0].strip()
    if not branch:
        raise ValueError(f"{label} has an empty operating centre")
    venue = " ".join(
        part for part in (branch, _clean(pairs["강습장소"])) if part
    )
    safe = _safe_detail_fields(pairs)
    query = item["identity_fields"]
    return (
        _build_row(
            target,
            item,
            provider_id_suffix=(
                f"sports:{query['comcd']}:{query['classcd']}:{query['type']}"
            ),
            branch=branch,
            branch_code=f"SEONGDONG_SPORTS_{query['comcd']}",
            category="스포츠",
            program_type="강좌",
            domain_category="교육·강좌",
            service_group="공공강좌",
            venue=venue,
            target_text=_clean(pairs["교육대상"]),
            fee="",
            schedule=_clean(pairs["시간/요일"]),
            application_method=_clean(pairs["접수방식"]),
            selection_method="",
            safe_pairs=safe,
        ),
        "",
    )


def _ccic_education_detail(
    target: Any, item: Mapping[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any] | None, str]:
    label = f"childcare education {item['identity']}"
    pairs = _table_pairs(soup)
    menu_id = _clean(item["identity_fields"].get("menuid"))
    if menu_id == "03":
        title_key, period_key = "교육명", "교육일시"
        venue_key, target_key = "교육장소", "교육대상"
    elif menu_id in {"04", "05"}:
        title_key, period_key = "행사명", "행사일시"
        venue_key, target_key = "행사장소", "행사대상"
    else:
        raise ValueError(f"{label} has an unsupported programme menu")
    _required_pairs(
        pairs,
        (title_key, "정원", period_key, "신청기간", venue_key, target_key),
        label,
    )
    if _clean(pairs[title_key]) != _clean(item["title"]):
        raise ValueError(f"{label} title mismatch")
    if _date_pair(pairs["신청기간"]) != (item["apply_start"], item["apply_end"]):
        raise ValueError(f"{label} application dates mismatch")
    if _date_pair(pairs[period_key]) != (item["start"], item["end"]):
        raise ValueError(f"{label} programme dates mismatch")
    if _first_int(pairs["정원"]) is None:
        raise ValueError(f"{label} capacity is invalid")
    reason = _non_course_reason(item["title"])
    if reason:
        return None, reason
    safe = _safe_detail_fields(pairs)
    query = item["identity_fields"]
    return (
        _build_row(
            target,
            item,
            provider_id_suffix=f"ccic:{query['menuid']}:{query['no']}",
            branch="성동구육아종합지원센터",
            branch_code="SEONGDONG_CCIC",
            category="영유아교육",
            program_type="강좌",
            domain_category="교육·강좌",
            service_group="공공강좌",
            venue=_clean(pairs[venue_key]),
            target_text=_clean(pairs[target_key]),
            fee=_clean(item.get("fee")),
            schedule=_clean(pairs[period_key]),
            application_method="온라인 접수",
            selection_method="",
            safe_pairs=safe,
        ),
        "",
    )


def _fifty_plus_education_detail(
    target: Any, item: Mapping[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any] | None, str]:
    label = f"50plus education {item['identity']}"
    text = _clean(soup.get_text(" ", strip=True))
    if _clean(item["title"]) not in text:
        raise ValueError(f"{label} title mismatch or empty public detail")
    ranges = _range_pairs(text)
    if (item["apply_start"], item["apply_end"]) not in ranges:
        raise ValueError(f"{label} application dates mismatch")
    if (item["start"], item["end"]) not in ranges:
        raise ValueError(f"{label} programme dates mismatch")
    reason = _non_course_reason(item["title"])
    if reason:
        return None, reason
    return (
        _build_row(
            target,
            item,
            provider_id_suffix=f"50plus:{item['identity']}",
            branch="성동50플러스센터",
            branch_code="SEONGDONG_50PLUS",
            category="평생교육",
            program_type="강좌",
            domain_category="교육·강좌",
            service_group="공공강좌",
            venue=_clean(item["venue"]),
            target_text=_clean(item["target"]),
            fee="",
            schedule="",
            application_method="공개 상세 참조",
            selection_method="",
            safe_pairs={},
        ),
        "",
    )


def _experience_detail(
    target: Any, item: Mapping[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any] | None, str]:
    label = f"experience {item['identity']}"
    _validate_title_in_document(item, soup, label)
    pairs = _table_pairs(soup)
    _required_pairs(
        pairs,
        (
            "구분",
            "운영기관",
            "대상",
            "장소",
            "접수기간",
            "운영기간",
            "모집인원(회차별)",
        ),
        label,
    )
    _validate_standard_dates(item, pairs, label)
    if _first_int(pairs["모집인원(회차별)"]) != item["capacity_total"]:
        raise ValueError(f"{label} capacity mismatch")
    category = _clean(pairs["구분"])
    if category != "체험":
        return None, f"experience_category:{category or 'missing'}"
    branch = _clean(pairs["운영기관"])
    safe = _safe_detail_fields(pairs)
    return (
        _build_row(
            target,
            item,
            provider_id_suffix=f"programNumber:{item['identity']}",
            branch=branch,
            branch_code=_branch_code("EXPERIENCE_OPERATOR", branch),
            category="체험",
            program_type="체험",
            domain_category="체험·견학",
            service_group="체험",
            venue=_clean(pairs["장소"]),
            target_text=_clean(pairs["대상"]),
            fee=_clean(pairs.get("이용요금")) or _clean(item.get("fee")),
            schedule=" / ".join(
                value
                for value in (_clean(pairs.get("운영요일")), _clean(pairs.get("운영시간")))
                if value
            ),
            application_method=_clean(pairs.get("예약방법")),
            selection_method=_clean(pairs.get("선별방법")) or _clean(item.get("selection")),
            safe_pairs=safe,
        ),
        "",
    )


def _detail_result(
    target: Any, item: Mapping[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any] | None, str]:
    kind = _clean(item["detail_kind"])
    if kind == "native_education":
        return _native_education_detail(target, item, soup)
    if kind == "sports_education":
        return _sports_education_detail(target, item, soup)
    if kind == "ccic_education":
        return _ccic_education_detail(target, item, soup)
    if kind == "fifty_plus_education":
        return _fifty_plus_education_detail(target, item, soup)
    if kind == "experience":
        return _experience_detail(target, item, soup)
    raise ValueError(f"unsupported Seongdong public detail kind {kind!r}")


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "declared_pages": 0,
        "source_total": 0,
        "discovered_links": 0,
        "current_candidate_count": 0,
        "current_count": 0,
        "expired_count": 0,
        "invalid_period_count": 0,
        "detail_pages": 0,
        "detail_attempts": 0,
        "detail_required_count": 0,
        "required_detail_count": 0,
        "excluded_non_course_count": 0,
        "excluded_non_course_counts": {},
        "pagination_complete": False,
        "details_complete": False,
        "classification_locked": True,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "full_snapshot_required": True,
        "declared_total_basis": "continuous descending No columns in both official ledgers",
        "branch_basis": "verified public detail operator/centre",
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "application_form_submissions": 0,
    }


def _fail(meta: dict[str, Any], reason: Any) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta["configured_collection_error"] = _clean(reason) or "unknown collection error"
    meta["snapshot_complete"] = False
    return [], SEONGDONG_PARSER, meta


def _validate_ledger(
    items: list[dict[str, Any]], source_total: int, label: str
) -> None:
    numbers = [int(item["source_number"]) for item in items]
    if numbers != list(range(source_total, 0, -1)):
        raise ValueError(f"{label} source No values are not continuous and descending")
    identities = [(item["detail_kind"], _clean(item["identity"])) for item in items]
    if len(set(identities)) != len(identities):
        raise ValueError(f"{label} source contains duplicate public detail identities")
    if len(items) != source_total:
        raise ValueError(f"{label} observed rows do not equal the declared source total")


def collect_seongdong_integrated_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 250,
    detail_limit: int = 2000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = SEONGDONG_MAX_DETAIL_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one exact current/future education or experience catalogue."""

    meta = _base_meta()
    catalogue_kind = _catalogue_kind(target)
    if not catalogue_kind:
        return _fail(meta, "target is not the canonical Seongdong integrated route")
    meta["catalogue_kind"] = catalogue_kind

    request = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    as_of = _today(today)
    list_session = make_session()
    education_items: list[dict[str, Any]] = []
    experience_items: list[dict[str, Any]] = []
    try:
        education_total = 0
        experience_total = 0
        education_pages = 0
        experience_pages = 0
        education_visible: set[int] = set()
        experience_visible: set[int] = set()

        if catalogue_kind == "education":
            education_first = _fetch(
                request,
                list_session,
                seongdong_education_list_url(1),
                max(1, int(timeout)),
            )
            first_education_items = _parse_education_page(education_first, 1)
            meta["pages"] = 1
            if not first_education_items:
                return _fail(meta, "the official education first page contained no numbered rows")
            education_total = first_education_items[0]["source_number"]
            education_pages = math.ceil(education_total / SEONGDONG_PAGE_SIZE)
            education_visible = _visible_pages(education_first, "education")
            education_items.extend(first_education_items)
        else:
            experience_first = _fetch(
                request,
                list_session,
                seongdong_experience_list_url(1),
                max(1, int(timeout)),
            )
            first_experience_items = _parse_experience_page(experience_first, 1)
            meta["pages"] = 1
            if not first_experience_items:
                return _fail(meta, "the official experience first page contained no numbered rows")
            experience_total = first_experience_items[0]["source_number"]
            experience_pages = math.ceil(experience_total / SEONGDONG_PAGE_SIZE)
            experience_visible = _visible_pages(experience_first, "experience")
            experience_items.extend(first_experience_items)

        meta.update(
            {
                "source_total": education_total + experience_total,
                "declared_pages": education_pages + experience_pages,
                "education_source_total": education_total,
                "experience_source_total": experience_total,
                "education_declared_pages": education_pages,
                "experience_declared_pages": experience_pages,
                "education_pagination_visible_pages": sorted(education_visible),
                "experience_pagination_visible_pages": sorted(experience_visible),
            }
        )
        for label, declared, visible in (
            ("education", education_pages, education_visible),
            ("experience", experience_pages, experience_visible),
        ):
            if not declared:
                continue
            if declared < 1 or max(visible) > declared or declared not in visible:
                return _fail(meta, f"{label} pagination does not expose its declared final page")
            if int(max_pages) < declared:
                meta["source_cap_reached"] = True
                return _fail(
                    meta,
                    f"max_pages={max_pages} is below {label} declared_pages={declared}",
                )

        for page in range(2, education_pages + 1):
            soup = _fetch(
                request,
                list_session,
                seongdong_education_list_url(page),
                max(1, int(timeout)),
            )
            education_items.extend(_parse_education_page(soup, page))
            meta["pages"] += 1

        for page in range(2, experience_pages + 1):
            soup = _fetch(
                request,
                list_session,
                seongdong_experience_list_url(page),
                max(1, int(timeout)),
            )
            experience_items.extend(_parse_experience_page(soup, page))
            meta["pages"] += 1
    except Exception as exc:
        return _fail(meta, exc)
    finally:
        _close_quietly(list_session)

    try:
        if education_items:
            _validate_ledger(education_items, int(meta["education_source_total"]), "education")
        if experience_items:
            _validate_ledger(experience_items, int(meta["experience_source_total"]), "experience")
    except Exception as exc:
        return _fail(meta, exc)

    all_items = education_items + experience_items
    meta["discovered_links"] = len(all_items)
    meta["pagination_complete"] = True
    source_current = [item for item in all_items if item["end"] >= as_of]
    preclassified_exclusions = Counter(
        _clean(item.get("preclassified_exclusion"))
        for item in source_current
        if _clean(item.get("preclassified_exclusion"))
    )
    current = [
        item
        for item in source_current
        if not _clean(item.get("preclassified_exclusion"))
    ]
    expired = [item for item in all_items if item["end"] < as_of]
    current_kinds = Counter(_clean(item["detail_kind"]) for item in current)
    meta.update(
        {
            "current_candidate_count": len(source_current),
            "education_current_candidate_count": sum(item["source"] == "education" for item in source_current),
            "experience_current_candidate_count": sum(item["source"] == "experience" for item in source_current),
            "expired_count": len(expired),
            "detail_required_count": len(current),
            "required_detail_count": len(current),
            "current_detail_kind_counts": dict(current_kinds),
        }
    )
    if int(detail_limit) < len(current):
        meta["source_cap_reached"] = True
        return _fail(
            meta,
            f"detail_limit={detail_limit} is below required details={len(current)}",
        )

    def load_detail(item: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
        current_session = make_session()
        try:
            detail_soup = _fetch(
                request,
                current_session,
                _clean(item["detail_url"]),
                max(1, int(timeout)),
            )
            return _detail_result(target, item, detail_soup)
        finally:
            _close_quietly(current_session)

    try:
        meta["detail_attempts"] = len(current)
        workers = max(1, min(int(max_workers), SEONGDONG_MAX_DETAIL_WORKERS))
        if not current:
            results: list[tuple[dict[str, Any] | None, str]] = []
        elif workers == 1:
            results = [load_detail(item) for item in current]
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                results = list(executor.map(load_detail, current))
        meta["detail_pages"] = len(results)
    except Exception as exc:
        return _fail(meta, exc)

    rows = [row for row, _reason in results if row is not None]
    exclusion_counts = preclassified_exclusions + Counter(
        reason for row, reason in results if row is None and reason
    )
    output = list(dedupe_rows(rows)) if dedupe_rows is not None else _dedupe(rows)
    if len(output) != len(rows):
        return _fail(meta, "row deduplication removed a distinct official source identity")
    if len({_clean(row.get("provider_course_id")) for row in output}) != len(output):
        return _fail(meta, "combined ledgers produced duplicate provider_course_id values")

    branches = Counter(_clean(row.get("branch")) for row in output)
    domains = Counter(_clean(row.get("domain_category")) for row in output)
    services = Counter(_clean(row.get("service_group")) for row in output)
    detail_hosts = Counter(
        (urlparse(_clean(item["detail_url"])).hostname or "").lower() for item in current
    )
    meta.update(
        {
            "current_count": len(output),
            "education_current_count": sum(row.get("domain_category") == "교육·강좌" for row in output),
            "experience_current_count": sum(row.get("domain_category") == "체험·견학" for row in output),
            "excluded_non_course_count": sum(exclusion_counts.values()),
            "excluded_non_course_counts": dict(exclusion_counts),
            "reservation_discovery_links": len(output),
            "branch_count": len(branches),
            "branch_counts": dict(branches),
            "domain_category_counts": dict(domains),
            "service_group_counts": dict(services),
            "detail_host_counts": dict(detail_hosts),
            "legacy_subset_identity_preserved_count": sum(
                bool(row.get("raw_fields", {}).get("historical_owner_identity_preserved"))
                for row in output
            ),
            "details_complete": meta["detail_pages"] == len(current),
            "snapshot_complete": True,
        }
    )
    return output, SEONGDONG_PARSER, meta


# Compatibility entry points for the retained production provider.
collect_seongdong_dokseodang_courses = collect_seongdong_integrated_courses
collect_courses = collect_seongdong_integrated_courses
