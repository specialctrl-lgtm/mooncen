"""Fail-closed education collectors for Ulsan Buk-gu (3120000000).

Two Buk-gu-owned catalogues are canonical:

* the Buk-gu Facilities Management Corporation reservation site, whose ten
  facility tabs each return one complete, unpaginated lecture table and whose
  current/future rows expose the target on their public detail pages; and
* the Buk-gu municipal-library site, whose ``edusat`` (courses) and
  ``edusat2`` (events) boards form one education catalogue.

The facilities site accepts a ``pg`` query parameter but ignores it.  A
complete snapshot therefore verifies every fixed facility tab and then proves
that an out-of-range ``pg`` request returns the identical row fingerprint.
The library boards declare their totals, use twenty rows per page, and clamp an
overrun request to the declared last page.  Both declared page ranges and both
clamp sentinels must match before anything is returned.

The Buk-gu lifelong-learning page is not a third source: its JSON metadata
links to facilities-site member ``B0001007`` and uses the same ``lecId``
identities.  ``yes.ulsan.go.kr`` is a city-wide discovery shell, and the
candidate ``/main/edusat2/user.do`` is an unbound applicant lookup/form rather
than a catalogue.  They are intentionally exposed as rejected aliases so the
router/config layer can supersede them without double collection.  The Ulju
County ``crs.uljusiseol.or.kr`` site is explicitly outside this municipality.

Instructor, telephone, email, attachments, arbitrary detail body text, and
applicant-form fields are never copied into rows or ``raw_fields``.  An
application URL is published only when a current source row has a same-host,
course-bound application control on its verified detail page.

This module does not import ``Crawler_MunicipalYaml``.  Production must inject
the managed fetcher, session factory, and optional deduper.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import math
from pathlib import Path
import re
import ssl
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


ULSAN_BUKGU_MUNICIPALITY_CODE = "3120000000"
ULSAN_BUKGU_MUNICIPALITY_NAME = "울산광역시 북구"

ULSAN_BUKGU_PUBLIC_PROVIDER = "ULSAN_BUKGU_PUBLIC_RESERVATION"
ULSAN_BUKGU_PUBLIC_URL = "https://crs.ubimc.or.kr/yeyak/new_lecture/lecture"
ULSAN_BUKGU_PUBLIC_HOST = "crs.ubimc.or.kr"
ULSAN_BUKGU_PUBLIC_PATH = "/yeyak/new_lecture/lecture"
ULSAN_BUKGU_INTERMEDIATE_CERT = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "certificates"
    / "sectigo_public_server_authentication_ca_dv_r36.crt"
)
ULSAN_BUKGU_PUBLIC_PARSER = (
    "ulsan_bukgu_fixed_10_facilities+all_row_tables+ignored_pg_clamp+"
    "all_current_details+active_application_controls"
)

ULSAN_BUKGU_LIBRARY_PROVIDER = "MUNI_USBL_BUKGU_ULSAN_KR_A68023CB"
ULSAN_BUKGU_LIBRARY_BAD_CANDIDATE_ID = "MUNI_IR_D0992A7C0A58"
ULSAN_BUKGU_LIBRARY_URL = "https://usbl.bukgu.ulsan.kr/main/edusat/list.do"
ULSAN_BUKGU_LIBRARY_EVENT_URL = (
    "https://usbl.bukgu.ulsan.kr/main/edusat2/list.do"
)
ULSAN_BUKGU_LIBRARY_HOST = "usbl.bukgu.ulsan.kr"
ULSAN_BUKGU_LIBRARY_PARSER = (
    "ulsan_bukgu_library_edusat+edusat2_declared_pages+last_page_clamps+"
    "current_details+semantic_offering_collapse"
)

# Search-result aliases/rejections.  These constants are deliberately not
# accepted by ``is_ulsan_bukgu_target``.
ULSAN_BUKGU_LIBRARY_BAD_CANDIDATE_URL = (
    "https://usbl.bukgu.ulsan.kr/main/edusat2/user.do"
)
ULSAN_BUKGU_LIFELONG_ALIAS_PROVIDER = "MUNI_WWW_BUKGU_ULSAN_KR_EAC75056"
ULSAN_BUKGU_LIFELONG_ALIAS_URL = (
    "https://www.bukgu.ulsan.kr/edu/pageCont.do?menuNo=2010000"
)
ULSAN_BUKGU_LIFELONG_ALIAS_API = (
    "https://www.bukgu.ulsan.kr/edu/getAjaxEdu.jsp"
)
ULSAN_BUKGU_YES_DISCOVERY_PROVIDER = "MUNI_YES_ULSAN_GO_KR_2706643A"
ULSAN_BUKGU_YES_DISCOVERY_URL = "https://yes.ulsan.go.kr/lecture"
ULSAN_BUKGU_SINGLE_DETAIL_ALIAS_URL = (
    "https://crs.ubimc.or.kr/lecture/step1?lecId=L0095088"
)
ULJU_FOREIGN_LECTURE_URL = "https://crs.uljusiseol.or.kr/new_lecture/lecture"

PUBLIC_FACILITIES: tuple[tuple[str, str], ...] = (
    ("B0001026", "무룡테니스장"),
    ("B0001002", "북구국민체육센터"),
    ("B0001006", "북구문화예술회관"),
    ("B0001008", "상안테니스장"),
    ("B0001009", "송정복합문화센터"),
    ("B0001003", "쇠부리체육센터"),
    ("B0001010", "연암배드민턴장"),
    ("B0001001", "오토밸리복지센터"),
    ("B0001007", "평생학습관"),
    ("B0001004", "호계문화체육센터"),
)
PUBLIC_FACILITY_BY_CODE = dict(PUBLIC_FACILITIES)
PUBLIC_TABLE_HEADERS = ("강좌명", "구분", "잔여인원/정원", "금액", "상태")
PUBLIC_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "대기자접수": "OPEN",
    "준비중": "SCHEDULED",
    "접수마감": "CLOSED",
    "강습전": "CLOSED",
    "강습중": "CLOSED",
    "강습종료": "CLOSED",
}

LIBRARY_BRANCHES: tuple[str, ...] = (
    "중앙도서관",
    "매곡도서관",
    "농소1동도서관",
    "명촌어린이도서관",
    "농소3동도서관",
    "기적의도서관",
    "강동바다도서관",
    "염포양정도서관",
    "송정나래도서관",
)
# The two programme boards currently expose eight filters.  송정나래도서관
# is present in the official footer registry but does not yet have a board
# filter.  Cards may use any name from the full registry.
LIBRARY_FILTER_BRANCHES: tuple[str, ...] = LIBRARY_BRANCHES[:-1]
LIBRARY_EVENT_FILTER_BRANCHES: tuple[str, ...] = (
    "중앙도서관",
    "송정나래도서관",
    "매곡도서관",
    "농소1동도서관",
    "명촌어린이도서관",
    "농소3동도서관",
    "기적의도서관",
    "강동바다도서관",
    "염포양정도서관",
)
LIBRARY_UMBRELLA_BRANCH = "울산 북구 구립도서관 공동행사"
LIBRARY_PAGE_SIZE = 20


@dataclass(frozen=True)
class LibraryCatalogue:
    key: str
    label: str
    list_url: str
    list_path: str
    detail_path: str
    application_path: str


LIBRARY_COURSES = LibraryCatalogue(
    key="edusat",
    label="수강신청",
    list_url=ULSAN_BUKGU_LIBRARY_URL,
    list_path="/main/edusat/list.do",
    detail_path="/main/edusat/view.do",
    application_path="/main/edusat/regist.do",
)
LIBRARY_EVENTS = LibraryCatalogue(
    key="edusat2",
    label="행사신청",
    list_url=ULSAN_BUKGU_LIBRARY_EVENT_URL,
    list_path="/main/edusat2/list.do",
    detail_path="/main/edusat2/view.do",
    application_path="/main/edusat2/regist.do",
)
LIBRARY_CATALOGUES = (LIBRARY_COURSES, LIBRARY_EVENTS)


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


_SPACE_RE = re.compile(r"\s+")
_DATE_RANGE_RE = re.compile(
    r"^(20\d{2})[./-](\d{1,2})[./-](\d{1,2})\s*~\s*"
    r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})$"
)
_DATETIME_RANGE_RE = re.compile(
    r"^(20\d{2})[./-](\d{1,2})[./-](\d{1,2})\s+"
    r"(\d{1,2}):(\d{2})\s*~\s*"
    r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})\s+"
    r"(\d{1,2}):(\d{2})$"
)
_LIBRARY_PERIOD_RE = re.compile(
    r"^(20\d{2})-(\d{1,2})-(\d{1,2})\s*~\s*"
    r"(20\d{2})-(\d{1,2})-(\d{1,2})(?:\s+(.+))?$"
)
_OPERATIONAL_TITLE_RE = re.compile(
    r"(?:정원|대기|추가|신청|접수|마감|시간변경|대상변경|기간변경|일정변경)"
)


class UlsanBukguContractError(ValueError):
    """Raised when a live response no longer matches an audited contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).lower())


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


def _is_exact_url(value: Any, canonical: str) -> bool:
    parsed = urlparse(_clean(value))
    expected = urlparse(canonical)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == (expected.hostname or "").lower()
        and parsed.port is None
        and parsed.path == expected.path
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_ulsan_bukgu_public_target(target: Any) -> bool:
    return _provider(target) == ULSAN_BUKGU_PUBLIC_PROVIDER and _is_exact_url(
        _target_url(target), ULSAN_BUKGU_PUBLIC_URL
    )


def is_ulsan_bukgu_library_target(target: Any) -> bool:
    return _provider(target) == ULSAN_BUKGU_LIBRARY_PROVIDER and _is_exact_url(
        _target_url(target), ULSAN_BUKGU_LIBRARY_URL
    )


def is_ulsan_bukgu_target(target: Any) -> bool:
    return is_ulsan_bukgu_public_target(target) or is_ulsan_bukgu_library_target(
        target
    )


def is_ulsan_bukgu_rejected_alias_target(target: Any) -> bool:
    provider = _provider(target)
    url = _target_url(target)
    return bool(
        (
            provider == ULSAN_BUKGU_LIFELONG_ALIAS_PROVIDER
            and url == ULSAN_BUKGU_LIFELONG_ALIAS_URL
        )
        or (
            provider == ULSAN_BUKGU_YES_DISCOVERY_PROVIDER
            and url == ULSAN_BUKGU_YES_DISCOVERY_URL
        )
        or (
            provider == ULSAN_BUKGU_LIBRARY_PROVIDER
            and url == ULSAN_BUKGU_LIBRARY_BAD_CANDIDATE_URL
        )
        or url == ULSAN_BUKGU_SINGLE_DETAIL_ALIAS_URL
        or url.startswith("https://crs.uljusiseol.or.kr/")
    )


is_target = is_ulsan_bukgu_target


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


class _UlsanBukguVerifiedTLSAdapter(HTTPAdapter):
    """Verify UBIMC with the public intermediate omitted by its server."""

    @staticmethod
    def context() -> ssl.SSLContext:
        if not ULSAN_BUKGU_INTERMEDIATE_CERT.is_file():
            raise UlsanBukguContractError(
                "pinned Sectigo public intermediate certificate is missing"
            )
        context = ssl.create_default_context(cafile=requests.certs.where())
        context.load_verify_locations(cafile=str(ULSAN_BUKGU_INTERMEDIATE_CERT))
        return context

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self.context()
        super().init_poolmanager(*args, **kwargs)


def configure_ulsan_bukgu_verified_session(current: Any) -> Any:
    """Mount a CA-validating adapter only for the UBIMC HTTPS origin."""

    mount = getattr(current, "mount", None)
    if not callable(mount):
        raise UlsanBukguContractError("managed session does not support TLS adapters")
    mount(
        f"https://{ULSAN_BUKGU_PUBLIC_HOST}/",
        _UlsanBukguVerifiedTLSAdapter(),
    )
    return current


def _default_fetcher(session_obj: Any, url: str, timeout: int) -> Any:
    return session_obj.get(url, timeout=timeout, allow_redirects=False, verify=True)


def _response_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise UlsanBukguContractError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise UlsanBukguContractError(f"unexpected HTTP status {status}")
    if getattr(value, "history", ()):
        raise UlsanBukguContractError("redirected response is not canonical")
    raise_for_status = getattr(value, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    if not getattr(value, "encoding", None) or str(value.encoding).lower() == "iso-8859-1":
        apparent = getattr(value, "apparent_encoding", None)
        if apparent:
            value.encoding = apparent
    body = getattr(value, "text", "")
    if not _clean(body) and getattr(value, "content", b""):
        body = value.content.decode("utf-8")
    if not _clean(body):
        raise UlsanBukguContractError("empty HTML response")
    return BeautifulSoup(body, "lxml")


def _date_range(value: Any) -> tuple[str, date, date]:
    text = _clean(value)
    match = _DATE_RANGE_RE.fullmatch(text)
    if not match:
        raise UlsanBukguContractError(f"invalid date range: {text or '<empty>'}")
    values = [int(item) for item in match.groups()]
    start = date(values[0], values[1], values[2])
    end = date(values[3], values[4], values[5])
    if end < start:
        raise UlsanBukguContractError("date range ends before it starts")
    return f"{start.isoformat()} ~ {end.isoformat()}", start, end


def _datetime_range(
    value: Any,
) -> tuple[str, datetime, datetime, date, date]:
    text = _clean(value)
    match = _DATETIME_RANGE_RE.fullmatch(text)
    if not match:
        raise UlsanBukguContractError(
            f"invalid datetime range: {text or '<empty>'}"
        )
    values = [int(item) for item in match.groups()]
    tz = ZoneInfo("Asia/Seoul")
    start = datetime(values[0], values[1], values[2], values[3], values[4], tzinfo=tz)
    end = datetime(values[5], values[6], values[7], values[8], values[9], tzinfo=tz)
    if end < start:
        raise UlsanBukguContractError("datetime range ends before it starts")
    return (
        f"{start.strftime('%Y-%m-%d %H:%M')} ~ {end.strftime('%Y-%m-%d %H:%M')}",
        start,
        end,
        start.date(),
        end.date(),
    )


def _single_query(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _base_row(
    target: Any,
    *,
    provider: str,
    identity: str,
    title: str,
    branch: str,
    branch_code: str,
    raw_url: str,
    status: str,
    source_status: str,
    period: str,
    start: date,
    end: date,
    category: str,
    source_kind: str,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:{identity}",
        "title": title,
        "branch": branch,
        "branch_code": branch_code,
        "preserve_branch": True,
        "raw_url": raw_url,
        "application_url": "",
        "application_type": "",
        "reservation_available": False,
        "status": status,
        "period": period,
        "start_date": start,
        "end_date": end,
        "category": category,
        "program_type": "강좌",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "municipality_code": ULSAN_BUKGU_MUNICIPALITY_CODE,
        "municipality_name": ULSAN_BUKGU_MUNICIPALITY_NAME,
        "raw_fields": {
            "source_kind": source_kind,
            "source_status": source_status,
        },
    }


def _public_url(code: str, *, clamp: bool = False) -> str:
    query: list[tuple[str, str]] = [("mem_id", code)]
    if clamp:
        query.append(("pg", "999999"))
    return f"{ULSAN_BUKGU_PUBLIC_URL}?{urlencode(query)}"


def _public_detail_url(code: str, lecture_id: str) -> str:
    return (
        f"{ULSAN_BUKGU_PUBLIC_URL}?"
        f"{urlencode((('prc', 'detail'), ('lec_id', lecture_id), ('mem_id', code)))}"
    )


def _public_application_url(code: str, lecture_id: str) -> str:
    return (
        f"{ULSAN_BUKGU_PUBLIC_URL}?"
        f"{urlencode((('mem_id', code), ('prc', 'rsvinfo'), ('lec_id', lecture_id)))}"
    )


def _public_facilities(soup: BeautifulSoup) -> tuple[tuple[str, str], ...]:
    seen: dict[str, str] = {}
    for link in soup.select("a[href*='new_lecture/lecture'][href*='mem_id=']"):
        parsed = urlparse(urljoin(ULSAN_BUKGU_PUBLIC_URL, _clean(link.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        code = _single_query(query, "mem_id")
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != ULSAN_BUKGU_PUBLIC_HOST
            or parsed.port is not None
            or parsed.path != ULSAN_BUKGU_PUBLIC_PATH
            or set(query) != {"mem_id"}
            or not re.fullmatch(r"B\d{7}", code)
        ):
            continue
        name = _clean(link.get_text(" ", strip=True))
        if not name:
            raise UlsanBukguContractError("facility link has no label")
        if code in seen and seen[code] != name:
            raise UlsanBukguContractError("facility code has conflicting labels")
        seen[code] = name
    found = tuple((code, seen[code]) for code, _name in PUBLIC_FACILITIES if code in seen)
    if found != PUBLIC_FACILITIES or set(seen) != set(PUBLIC_FACILITY_BY_CODE):
        raise UlsanBukguContractError(
            f"official facility fanout changed: {sorted(seen.items())}"
        )
    return found


def _public_filter_contract(soup: BeautifulSoup, code: str) -> None:
    matches = []
    for form in soup.select("form"):
        mem = form.select_one('input[name="mem_id"]')
        if mem and _clean(mem.get("value")) == code:
            matches.append(form)
    if len(matches) != 1:
        raise UlsanBukguContractError("expected one facility lecture filter form")
    form = matches[0]
    if _clean(form.get("method")).lower() != "get":
        raise UlsanBukguContractError("facility lecture filter is no longer GET")
    names = [
        _clean(node.get("name"))
        for node in form.select("[name]")
        if _clean(node.get("name"))
    ]
    if names != ["selItemKind", "mem_id", "selkind", "selcheck", "seek"]:
        raise UlsanBukguContractError("facility lecture filter fields changed")


def _public_table_contract(soup: BeautifulSoup, code: str) -> list[Any]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "울산북구공공시설예약서비스":
        raise UlsanBukguContractError("unexpected facilities-site title")
    _public_facilities(soup)
    _public_filter_contract(soup, code)
    if soup.select(
        ".pagination, .paging, .paginate, .board_paginate, "
        '[name="pg"], [name="page"], [name="pageIndex"]'
    ):
        raise UlsanBukguContractError("unexpected pagination control on all-row table")
    tables = soup.select("table.table_list")
    if len(tables) != 1:
        raise UlsanBukguContractError("expected one facilities lecture table")
    table = tables[0]
    headers = tuple(
        _clean(node.get_text(" ", strip=True)) for node in table.select("thead th")
    )
    if headers != PUBLIC_TABLE_HEADERS:
        raise UlsanBukguContractError("facilities lecture table headers changed")
    rows = [row for row in table.select("tbody > tr") if row.find_all("td", recursive=False)]
    if rows and not any(row.select_one("a[href*='lec_id=']") for row in rows):
        if len(rows) != 1:
            raise UlsanBukguContractError("ambiguous facilities no-data table")
        cells = rows[0].find_all("td", recursive=False)
        text = _clean(rows[0].get_text(" ", strip=True))
        if (
            len(cells) != 1
            or _clean(cells[0].get("colspan")) not in {"5", ""}
            or not re.search(r"(?:등록|검색|조회|강좌|프로그램).*(?:없습니다|없음)", text)
        ):
            raise UlsanBukguContractError("unrecognized facilities no-data row")
        return []
    return rows


def _public_subject_value(subject: Any, label: str) -> str:
    matches = []
    for node in subject.select("p.edu_date"):
        marker = node.select_one("span")
        key = _clean(marker.get_text(" ", strip=True) if marker else "").rstrip(":： ")
        if key == label:
            text = _clean(node.get_text(" ", strip=True))
            marker_text = _clean(marker.get_text(" ", strip=True))
            matches.append(_clean(text.replace(marker_text, "", 1)).lstrip(":： "))
    if len(matches) != 1 or not matches[0]:
        raise UlsanBukguContractError(f"missing facilities row field: {label}")
    return matches[0]


def _public_detail_identity(href: Any, expected_code: str) -> tuple[str, str]:
    parsed = urlparse(urljoin(ULSAN_BUKGU_PUBLIC_URL, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    lecture_id = _single_query(query, "lec_id")
    code = _single_query(query, "mem_id")
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != ULSAN_BUKGU_PUBLIC_HOST
        or parsed.port is not None
        or parsed.path != ULSAN_BUKGU_PUBLIC_PATH
        or _single_query(query, "prc") != "detail"
        or code != expected_code
        or not re.fullmatch(r"L\d{7}", lecture_id)
        or not set(query).issubset({"prc", "lec_id", "mem_id", "selcheck", "pg"})
        or parsed.params
        or parsed.fragment
    ):
        raise UlsanBukguContractError("facilities detail identity changed")
    return lecture_id, _public_detail_url(code, lecture_id)


def _fee(value: Any) -> str:
    text = _clean(value).replace("원", "").replace(",", "")
    if text in {"무료", "면제"}:
        return text
    if not text.isdigit():
        raise UlsanBukguContractError("invalid fee value")
    return f"{int(text):,}원"


def _public_fee(cell: Any) -> str:
    # Discounted youth/child/senior prices are nested in separate ``p``
    # elements.  The catalogue's primary fee is the sole direct text node.
    values = [
        _clean(node)
        for node in cell.find_all(string=True, recursive=False)
        if _clean(node)
    ]
    if len(values) != 1:
        raise UlsanBukguContractError("facilities primary fee marker changed")
    return _fee(values[0])


def _public_row(
    target: Any,
    row: Any,
    code: str,
    facility: str,
) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    classes = tuple(_clean(" ".join(cell.get("class") or [])) for cell in cells)
    if len(cells) != 5 or classes != ("subject", "devide", "person", "pay", "state"):
        raise UlsanBukguContractError("facilities lecture row columns changed")
    subject, category_cell, person_cell, pay_cell, state_cell = cells
    links = subject.select("a[href]")
    if len(links) != 1:
        raise UlsanBukguContractError("facilities lecture row has ambiguous detail link")
    lecture_id, raw_url = _public_detail_identity(links[0].get("href"), code)
    title_nodes = subject.select("p.tit")
    title = _clean(title_nodes[0].get_text(" ", strip=True)) if len(title_nodes) == 1 else ""
    category = _clean(category_cell.get_text(" ", strip=True))
    source_status = _clean(state_cell.get_text(" ", strip=True))
    status = PUBLIC_STATUS_MAP.get(source_status, "")
    if not title or not category or not status:
        raise UlsanBukguContractError("facilities lecture row lacks title/category/status")
    period_raw = _public_subject_value(subject, "강습기간")
    schedule = _public_subject_value(subject, "강습시간")
    period, start, end = _date_range(period_raw)
    person = _clean(person_cell.get_text(" ", strip=True))
    remaining: Optional[int] = None
    capacity: Optional[int] = None
    if source_status == "접수중":
        match = re.fullmatch(r"(\d{1,6})\s*/\s*(\d{1,6})", person.replace(",", ""))
        if not match:
            raise UlsanBukguContractError("open facilities row lacks remaining/capacity")
        remaining, capacity = int(match.group(1)), int(match.group(2))
        if capacity <= 0 or remaining > capacity:
            raise UlsanBukguContractError("invalid facilities remaining/capacity")
    elif person != source_status:
        raise UlsanBukguContractError("facilities row person/status marker mismatch")
    branch = f"{ULSAN_BUKGU_MUNICIPALITY_NAME} · {facility}"
    result = _base_row(
        target,
        provider=ULSAN_BUKGU_PUBLIC_PROVIDER,
        identity=f"lecture:{code}:{lecture_id}",
        title=title,
        branch=branch,
        branch_code=f"{ULSAN_BUKGU_PUBLIC_PROVIDER}:{code}",
        raw_url=raw_url,
        status=status,
        source_status=source_status,
        period=period,
        start=start,
        end=end,
        category=category,
        source_kind="ubimc_facility_lecture",
    )
    result.update(
        {
            "schedule_raw": schedule,
            "fee": _public_fee(pay_cell),
            "venue_name": facility,
        }
    )
    if capacity is not None and remaining is not None:
        result.update(
            {
                "capacity": capacity,
                "capacity_total": capacity,
                "capacity_remaining": remaining,
                "capacity_current": capacity - remaining,
            }
        )
    result["raw_fields"].update(
        {"mem_id": code, "lec_id": lecture_id, "all_row_table": True}
    )
    return result


def _public_fingerprint(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("period")),
            _clean(row.get("schedule_raw")),
            _clean(row.get("status")),
            _clean(row.get("category")),
            _clean(row.get("fee")),
        )
        for row in rows
    )


def _definition_pairs(table: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row in table.select("tr"):
        header = row.select_one("th")
        value = row.select_one("td")
        if not header or not value:
            continue
        key = _clean(header.get_text(" ", strip=True)).rstrip(":：")
        if not key or key in pairs:
            raise UlsanBukguContractError("duplicate/empty detail field")
        pairs[key] = _clean(value.get_text(" ", strip=True))
    return pairs


def _public_capacity(value: Any) -> tuple[int, Optional[int]]:
    text = _clean(value).replace(",", "")
    match = re.fullmatch(
        r"(\d{1,6})명(?:\(접수가능인원\s*:\s*(\d{1,6})명\))?", text
    )
    if not match:
        raise UlsanBukguContractError("invalid facilities detail capacity")
    total = int(match.group(1))
    remaining = int(match.group(2)) if match.group(2) is not None else None
    if total <= 0 or (remaining is not None and remaining > total):
        raise UlsanBukguContractError("invalid facilities detail capacity")
    return total, remaining


def _public_schedule_supplement(list_value: Any, detail_value: Any) -> str:
    list_schedule = _clean(list_value)
    detail_schedule = _clean(detail_value)
    if _normalized(detail_schedule) == _normalized(list_schedule):
        return ""
    match = re.fullmatch(
        rf"{re.escape(list_schedule)}\s+"
        r"(주말/공휴일\s+\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2})",
        detail_schedule,
    )
    if not match:
        raise UlsanBukguContractError("facilities list/detail schedule mismatch")
    return _clean(match.group(1))


def _public_visible_application_controls(soup: BeautifulSoup) -> list[Any]:
    visible = []
    for anchor in soup.select(".select_area a[onclick]"):
        button = anchor.select_one("button.bt_visible")
        if (
            button
            and _clean(button.get_text(" ", strip=True)) == "강좌신청"
            and re.fullmatch(r"\s*goto_lecture\(\s*\)\s*;?\s*", _clean(anchor.get("onclick")))
        ):
            visible.append(anchor)
    return visible


def _public_application_control(
    soup: BeautifulSoup,
    code: str,
    lecture_id: str,
) -> str:
    visible = _public_visible_application_controls(soup)
    if len(visible) != 1:
        raise UlsanBukguContractError("open lecture lacks one visible application control")
    found: set[str] = set()
    pattern = re.compile(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]")
    for script in soup.select("script"):
        for match in pattern.finditer(script.get_text()):
            parsed = urlparse(urljoin(ULSAN_BUKGU_PUBLIC_URL, match.group(1)))
            query = parse_qs(parsed.query, keep_blank_values=True)
            if (
                parsed.scheme == "https"
                and (parsed.hostname or "").lower() == ULSAN_BUKGU_PUBLIC_HOST
                and parsed.port is None
                and parsed.path == ULSAN_BUKGU_PUBLIC_PATH
                and _single_query(query, "prc") == "rsvinfo"
                and _single_query(query, "mem_id") == code
                and _single_query(query, "lec_id") == lecture_id
                and set(query).issubset(
                    {"mem_id", "seek", "selcheck", "pg", "prc", "lec_id"}
                )
            ):
                found.add(_public_application_url(code, lecture_id))
    if found != {_public_application_url(code, lecture_id)}:
        raise UlsanBukguContractError("application JavaScript is not course-bound")
    return next(iter(found))


def _enrich_public_detail(row: dict[str, Any], soup: BeautifulSoup) -> None:
    code = _clean(row["raw_fields"].get("mem_id"))
    lecture_id = _clean(row["raw_fields"].get("lec_id"))
    titles = soup.select(".select_area .s_tit")
    if len(titles) != 1 or _normalized(titles[0].get_text(" ", strip=True)) != _normalized(
        row["title"]
    ):
        raise UlsanBukguContractError("facilities list/detail title mismatch")
    tables = soup.select(".select_area table.table_st2")
    if len(tables) != 1:
        raise UlsanBukguContractError("expected one facilities detail table")
    pairs = _definition_pairs(tables[0])
    required = {
        "강습대상",
        "정원",
        "신규회원 모집기간",
        "강습기간",
        "강습시간",
        "수강료",
    }
    if not required.issubset(pairs):
        raise UlsanBukguContractError("facilities detail fields changed")
    period, start, end = _date_range(pairs["강습기간"])
    if period != row["period"] or start != row["start_date"] or end != row["end_date"]:
        raise UlsanBukguContractError("facilities list/detail period mismatch")
    schedule = _clean(pairs["강습시간"])
    schedule_supplement = _public_schedule_supplement(
        row["schedule_raw"], schedule
    )
    total, remaining = _public_capacity(pairs["정원"])
    if row.get("capacity_total") is not None and total != row["capacity_total"]:
        raise UlsanBukguContractError("facilities list/detail capacity mismatch")
    if row.get("capacity_remaining") is not None and remaining != row["capacity_remaining"]:
        raise UlsanBukguContractError("facilities list/detail remaining mismatch")
    apply_period, apply_start, apply_end, apply_start_date, apply_end_date = _datetime_range(
        pairs["신규회원 모집기간"]
    )
    row.update(
        {
            "target": _clean(pairs["강습대상"]),
            "capacity": total,
            "capacity_total": total,
            "capacity_remaining": remaining,
            "capacity_current": total - remaining if remaining is not None else None,
            "apply_period": apply_period,
            "apply_start_at": apply_start,
            "apply_end_at": apply_end,
            "apply_start_date": apply_start_date,
            "apply_end_date": apply_end_date,
        }
    )
    application_present = False
    if row["status"] == "OPEN":
        row.update(
            {
                "application_url": _public_application_control(
                    soup, code, lecture_id
                ),
                "application_type": "ONLINE_RESERVATION",
                "reservation_available": True,
            }
        )
        application_present = True
    else:
        if _public_visible_application_controls(soup):
            raise UlsanBukguContractError(
                "non-open lecture exposes a visible application control"
            )
        row["application_type"] = "INFO_ONLY"
    row["raw_fields"].update(
        {
            "detail_verified": True,
            "detail_schedule_raw": schedule,
            "detail_schedule_supplement": schedule_supplement,
            "application_control_verified": True,
            "application_control_present": application_present,
        }
    )


def _public_semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _normalized(row.get("schedule_raw")),
        _clean(row.get("branch_code")),
    )


def _common_failure(parser: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "parser": parser,
        "pages": 0,
        "data_pages": 0,
        "clamp_pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "request_count": 0,
        "source_rows": 0,
        "declared_source_rows": 0,
        "valid_count": 0,
        "invalid_count": 0,
        "duplicate_count": 0,
        "duplicate_url_count": 0,
        "semantic_candidate_duplicate_count": 0,
        "semantic_collapsed_count": 0,
        "expired_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "detail_candidates": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "clamp_verified": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": reason,
        **extra,
    }


def collect_ulsan_bukgu_public_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 30,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete ten-facility UBIMC education snapshot."""

    parser = ULSAN_BUKGU_PUBLIC_PARSER
    if not is_ulsan_bukgu_public_target(target):
        meta = _common_failure(parser, "target provider/url is not canonical UBIMC")
        return [], parser, meta
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            meta = _common_failure(parser, "managed fetcher and session_factory injection are required")
            return [], parser, meta
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory

    required_list_requests = len(PUBLIC_FACILITIES) * 2
    allowed_pages = max(0, int(max_pages))
    if allowed_pages < required_list_requests:
        meta = _common_failure(
            parser,
            f"max_pages cap allows {allowed_pages} of {required_list_requests} required facility/clamp requests",
            required_list_requests=required_list_requests,
            source_cap_reached=True,
        )
        return [], parser, meta

    cutoff = _today(today)
    errors: list[str] = []
    candidates: list[dict[str, Any]] = []
    list_requests = 0
    data_pages = 0
    clamp_pages = 0
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    invalid_count = 0
    clamp_verified_count = 0
    session_obj = session_factory()
    try:
        root_soup: Optional[BeautifulSoup] = None
        try:
            root_soup = _response_soup(fetcher(session_obj, ULSAN_BUKGU_PUBLIC_URL, timeout))
            list_requests += 1
            data_pages += 1
            _public_facilities(root_soup)
        except Exception as exc:
            errors.append(f"root: {type(exc).__name__}: {_clean(exc)}")

        for index, (code, facility) in enumerate(PUBLIC_FACILITIES):
            if errors:
                break
            try:
                if index == 0:
                    assert root_soup is not None
                    data_soup = root_soup
                else:
                    data_soup = _response_soup(
                        fetcher(session_obj, _public_url(code), timeout)
                    )
                    list_requests += 1
                    data_pages += 1
                source_nodes = _public_table_contract(data_soup, code)
                source_rows = [
                    _public_row(target, node, code, facility) for node in source_nodes
                ]
                clamp_soup = _response_soup(
                    fetcher(session_obj, _public_url(code, clamp=True), timeout)
                )
                list_requests += 1
                clamp_pages += 1
                clamp_nodes = _public_table_contract(clamp_soup, code)
                clamp_rows = [
                    _public_row(target, node, code, facility) for node in clamp_nodes
                ]
                if _public_fingerprint(source_rows) != _public_fingerprint(clamp_rows):
                    raise UlsanBukguContractError(
                        f"facility {code} ignored-pg clamp fingerprint changed"
                    )
                clamp_verified_count += 1
                candidates.extend(source_rows)
            except Exception as exc:
                invalid_count += 1
                errors.append(
                    f"facility {code}: {type(exc).__name__}: {_clean(exc)}"
                )

        identities = [_clean(row.get("provider_course_id")) for row in candidates]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate UBIMC lecture identities")
        urls = [_clean(row.get("raw_url")) for row in candidates]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate UBIMC detail URLs")
        semantic_counts = Counter(_public_semantic_signature(row) for row in candidates)
        semantic_duplicate_count = sum(
            count - 1 for count in semantic_counts.values() if count > 1
        )
        if semantic_duplicate_count:
            errors.append(
                f"{semantic_duplicate_count} duplicate UBIMC semantic offerings"
            )

        current_rows = [row for row in candidates if row["end_date"] >= cutoff]
        expired_count = len(candidates) - len(current_rows)
        active_rows = [row for row in current_rows if row["status"] == "OPEN"]
        if any(row["end_date"] < cutoff for row in candidates if row["status"] == "OPEN"):
            errors.append("expired UBIMC row still advertises an open source status")
        allowed_details = max(0, int(detail_limit))
        source_cap_reached = len(current_rows) > allowed_details
        if source_cap_reached:
            errors.append(
                f"detail_limit cap allows {allowed_details} of {len(current_rows)} "
                "required current/future details"
            )

        if not errors:
            for row in current_rows:
                detail_attempts += 1
                try:
                    soup = _response_soup(
                        fetcher(session_obj, _clean(row["raw_url"]), timeout)
                    )
                    _enrich_public_detail(row, soup)
                    detail_pages += 1
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: detail {type(exc).__name__}: {_clean(exc)}"
                    )

        result: list[dict[str, Any]] = []
        if not errors:
            result = current_rows
            if dedupe_rows is not None:
                deduped = list(dedupe_rows(result))
                if len(deduped) != len(result):
                    errors.append("downstream dedupe changed complete UBIMC snapshot count")
                else:
                    result = deduped
        snapshot_complete = not errors
        if not snapshot_complete:
            result = []
        no_current_data = snapshot_complete and not result
        branch_counts = Counter(_clean(row.get("branch")) for row in result)
        status_counts = Counter(_clean(row.get("status")) for row in result)
        meta = {
            "parser": parser,
            "pages": list_requests,
            "data_pages": data_pages,
            "clamp_pages": clamp_pages,
            "list_requests": list_requests,
            "required_list_requests": required_list_requests,
            "request_count": list_requests + detail_attempts,
            "source_rows": len(candidates),
            "declared_source_rows": len(candidates),
            "valid_count": len(candidates),
            "invalid_count": invalid_count,
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_candidate_duplicate_count": semantic_duplicate_count,
            "semantic_collapsed_count": 0,
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "detail_candidates": len(current_rows),
            "application_detail_candidates": len(active_rows),
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_errors": detail_errors,
            "facility_count": len(PUBLIC_FACILITIES),
            "facility_codes": [code for code, _name in PUBLIC_FACILITIES],
            "all_row_tables_verified": snapshot_complete and data_pages == len(PUBLIC_FACILITIES),
            "clamp_verified_count": clamp_verified_count,
            "clamp_verified": snapshot_complete and clamp_verified_count == len(PUBLIC_FACILITIES),
            "pagination_detected": False,
            "pagination_complete": bool(
                snapshot_complete
                and data_pages == len(PUBLIC_FACILITIES)
                and clamp_verified_count == len(PUBLIC_FACILITIES)
            ),
            "details_complete": bool(
                snapshot_complete and detail_pages == len(current_rows)
            ),
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "no_current_data": no_current_data,
            "no_current_reason": (
                "all complete facility lecture rows are expired"
                if no_current_data and candidates
                else "all ten official facility tables and clamps are empty"
                if no_current_data
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
        }
        return result, parser, meta
    finally:
        close = getattr(session_obj, "close", None)
        if callable(close):
            close()


def _library_page_url(source: LibraryCatalogue, page: int) -> str:
    if page < 1:
        raise UlsanBukguContractError("library page must be positive")
    if page == 1:
        return source.list_url
    return f"{source.list_url}?{urlencode({'v_page': page})}"


def _library_detail_url(source: LibraryCatalogue, identity: str) -> str:
    if not identity.isdigit() or len(identity) > 10:
        raise UlsanBukguContractError("invalid library edu_idx")
    return (
        f"https://{ULSAN_BUKGU_LIBRARY_HOST}{source.detail_path}?"
        f"{urlencode({'edu_idx': identity})}"
    )


def _library_application_url(source: LibraryCatalogue, identity: str) -> str:
    if not identity.isdigit() or len(identity) > 10:
        raise UlsanBukguContractError("invalid library edu_idx")
    return (
        f"https://{ULSAN_BUKGU_LIBRARY_HOST}{source.application_path}?"
        f"{urlencode({'edu_idx': identity})}"
    )


def _library_total(soup: BeautifulSoup) -> tuple[int, int]:
    nodes = soup.select(".board_total .board_total_left strong.eng")
    if len(nodes) != 1:
        raise UlsanBukguContractError("missing library declared total")
    raw = _clean(nodes[0].get_text(" ", strip=True)).replace(",", "")
    if not raw.isdigit():
        raise UlsanBukguContractError("invalid library declared total")
    total = int(raw)
    marker = _clean(nodes[0].parent.get_text(" ", strip=True)).replace(",", "")
    if not re.fullmatch(
        rf"총\s*{total}\s*개의\s*프로그램이\s*등록되어\s*있습니다\.?",
        marker,
    ):
        raise UlsanBukguContractError("library total marker changed")
    pages = max(1, math.ceil(total / LIBRARY_PAGE_SIZE))
    return total, pages


def _library_branch_filter_contract(
    source: LibraryCatalogue, soup: BeautifulSoup
) -> None:
    labels: dict[str, str] = {}
    for link in soup.select("a[href*='sh_ct_idx=']"):
        parsed = urlparse(urljoin(source.list_url, _clean(link.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        code = _single_query(query, "sh_ct_idx")
        label = _clean(link.get_text(" ", strip=True))
        if (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == ULSAN_BUKGU_LIBRARY_HOST
            and parsed.path == source.list_path
            and code.isdigit()
            and label
        ):
            labels[label] = code
    expected = (
        LIBRARY_FILTER_BRANCHES
        if source == LIBRARY_COURSES
        else LIBRARY_EVENT_FILTER_BRANCHES
    )
    if tuple(labels) != expected:
        raise UlsanBukguContractError(
            f"library branch filters changed: {tuple(labels)}"
        )


def _library_form_contract(source: LibraryCatalogue, soup: BeautifulSoup) -> None:
    matches = []
    for form in soup.select("form"):
        action = urlparse(urljoin(source.list_url, _clean(form.get("action"))))
        if action.path == source.list_path and form.select_one('[name="sh_ct_idx"]'):
            matches.append(form)
    if len(matches) != 1 or _clean(matches[0].get("method")).lower() != "get":
        raise UlsanBukguContractError("library programme search form changed")
    names = [
        _clean(node.get("name"))
        for node in matches[0].select("[name]")
        if _clean(node.get("name"))
    ]
    if names != ["sh_ct_idx", "v_search", "v_keyword"]:
        raise UlsanBukguContractError("library programme search fields changed")


def _library_pagination_contract(
    soup: BeautifulSoup,
    total: int,
    pages: int,
    requested_page: int,
    *,
    clamp: bool = False,
) -> None:
    containers = soup.select(".board_paginate")
    if len(containers) != 1:
        raise UlsanBukguContractError("expected one library paginator")
    paginator = containers[0]
    strong = paginator.select("strong")
    if clamp:
        # The controller returns the last-page rows for an overrun, while the
        # paginator deliberately leaves every page as a link (no current
        # ``strong`` marker).  The last-row fingerprint check below completes
        # the clamp proof.
        if strong:
            raise UlsanBukguContractError("library overrun unexpectedly marks a current page")
        displayed: set[int] = set()
    else:
        if len(strong) != 1 or not _clean(strong[0].get_text(" ", strip=True)).isdigit():
            raise UlsanBukguContractError("library current-page marker changed")
        current = int(_clean(strong[0].get_text(" ", strip=True)))
        if current != requested_page:
            raise UlsanBukguContractError(
                f"library page {requested_page} returned current page {current}"
            )
        displayed = {current}
    for link in paginator.select("a[data-page]"):
        page_text = _clean(link.get("data-page"))
        parsed = urlparse(urljoin("https://placeholder.invalid/list.do", _clean(link.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if not page_text.isdigit() or _single_query(query, "v_page") != page_text:
            raise UlsanBukguContractError("library paginator link changed")
        displayed.add(int(page_text))
    if displayed != set(range(1, pages + 1)):
        raise UlsanBukguContractError("library paginator range differs from declared total")
    if total == 0 and displayed != {1}:
        raise UlsanBukguContractError("empty library paginator is inconsistent")


def _library_page_contract(
    source: LibraryCatalogue,
    soup: BeautifulSoup,
    requested_page: int,
    *,
    clamp: bool = False,
) -> tuple[list[Any], int, int]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if source.label not in title or "울산 북구 구립도서관" not in title:
        raise UlsanBukguContractError("unexpected library board title")
    _library_branch_filter_contract(source, soup)
    _library_form_contract(source, soup)
    total, pages = _library_total(soup)
    _library_pagination_contract(
        soup, total, pages, requested_page, clamp=clamp
    )
    cards = soup.select("#board .lesson > ul > li")
    actual_page = pages if clamp else requested_page
    expected_rows = min(
        LIBRARY_PAGE_SIZE,
        max(0, total - (actual_page - 1) * LIBRARY_PAGE_SIZE),
    )
    if len(cards) != expected_rows:
        raise UlsanBukguContractError(
            f"library {source.key} page {actual_page} has {len(cards)} rows, expected {expected_rows}"
        )
    return cards, total, pages


def _library_identity(
    source: LibraryCatalogue,
    base_url: str,
    href: Any,
) -> tuple[str, str]:
    parsed = urlparse(urljoin(base_url, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query(query, "edu_idx")
    prepage = _single_query(query, "prepage")
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != ULSAN_BUKGU_LIBRARY_HOST
        or parsed.port is not None
        or parsed.path != source.detail_path
        or set(query) != {"edu_idx", "prepage"}
        or not identity.isdigit()
        or len(identity) > 10
        or not prepage.startswith(source.list_path)
        or parsed.params
        or parsed.fragment
    ):
        raise UlsanBukguContractError("library detail identity changed")
    return identity, _library_detail_url(source, identity)


def _library_display_title(value: Any) -> tuple[str, str]:
    text = _clean(value)
    labels: list[str] = []
    while text.startswith("["):
        match = re.match(r"^\[([^\]]+)\]\s*(.*)$", text)
        if not match:
            break
        labels.append(_clean(match.group(1)))
        text = _clean(match.group(2))
    category = next((label for label in labels if not _OPERATIONAL_TITLE_RE.search(label)), "")
    text = re.sub(
        r"\([^)]*(?:정원|대기|추가|신청|접수|마감)[^)]*\)",
        " ",
        text,
    )
    text = _clean(text)
    if not text:
        raise UlsanBukguContractError("library title is empty after label normalization")
    return text, category or "도서관 프로그램"


def _library_period(value: Any) -> tuple[str, date, date, str]:
    text = _clean(value).replace(" 요일", "요일")
    match = _LIBRARY_PERIOD_RE.fullmatch(text)
    if not match:
        raise UlsanBukguContractError(f"invalid library operation period: {text}")
    values = match.groups()
    start = date(int(values[0]), int(values[1]), int(values[2]))
    end = date(int(values[3]), int(values[4]), int(values[5]))
    if end < start:
        raise UlsanBukguContractError("library operation period ends before it starts")
    schedule = _clean(values[6]).replace(" 요일", "요일")
    if not schedule:
        raise UlsanBukguContractError("library operation schedule is missing")
    return f"{start.isoformat()} ~ {end.isoformat()}", start, end, schedule


def _library_capacity(value: Any) -> tuple[int, int, Optional[int]]:
    text = _clean(value).replace(",", "")
    match = re.search(r"(\d{1,6})\s*/\s*(\d{1,6})\s*명", text)
    if not match:
        raise UlsanBukguContractError("invalid library capacity")
    current, total = int(match.group(1)), int(match.group(2))
    if total <= 0:
        raise UlsanBukguContractError("library capacity must be positive")
    wait_match = re.search(r"대기\s*[:：]?\s*(\d{1,6})명?", text)
    wait = int(wait_match.group(1)) if wait_match else None
    return current, total, wait


def _library_state(control: Any) -> tuple[str, str]:
    classes = set(control.get("class") or [])
    text = _clean(control.get_text(" ", strip=True))
    known = [
        ("btn_ing", "수강신청", "OPEN"),
        ("btn_prepare", "신청준비", "SCHEDULED"),
        ("btn_close", "기간종료", "CLOSED"),
        ("btn_end", "신청마감", "CLOSED"),
    ]
    matches = [(expected_text, status) for cls, expected_text, status in known if cls in classes]
    if len(matches) != 1 or text != matches[0][0]:
        raise UlsanBukguContractError("unknown library source status control")
    return text, matches[0][1]


def _library_bound_url(
    source: LibraryCatalogue,
    base_url: str,
    href: Any,
    expected_path: str,
    identity: str,
) -> str:
    parsed = urlparse(urljoin(base_url, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != ULSAN_BUKGU_LIBRARY_HOST
        or parsed.port is not None
        or parsed.path != expected_path
        or _single_query(query, "edu_idx") != identity
        or not set(query).issubset({"edu_idx", "prepage"})
        or parsed.params
        or parsed.fragment
    ):
        raise UlsanBukguContractError("library course-bound control changed")
    return _library_application_url(source, identity)


def _library_row(
    target: Any,
    source: LibraryCatalogue,
    card: Any,
    page_url: str,
    page: int,
) -> dict[str, Any]:
    links = card.select("p.tit a[href]")
    if len(links) != 1:
        raise UlsanBukguContractError("library card has ambiguous detail link")
    identity, raw_url = _library_identity(source, page_url, links[0].get("href"))
    title, category = _library_display_title(links[0].get_text(" ", strip=True))
    category_nodes = card.select("p.cate")
    if len(category_nodes) != 1:
        raise UlsanBukguContractError("library card branch marker changed")
    source_branch = _clean(category_nodes[0].get_text(" ", strip=True))
    if not source_branch:
        if source != LIBRARY_EVENTS:
            raise UlsanBukguContractError("course card has no library branch")
        source_branch = LIBRARY_UMBRELLA_BRANCH
    elif source_branch not in LIBRARY_BRANCHES:
        raise UlsanBukguContractError(f"unknown Buk-gu library branch: {source_branch}")
    dls = card.select(".sm_box dl")
    labels = tuple(
        _clean(dl.select_one("dt").get_text(" ", strip=True))
        if dl.select_one("dt")
        else ""
        for dl in dls
    )
    if labels != ("신청기간", "운영기간", "참가대상", "모집인원"):
        raise UlsanBukguContractError("library card fields changed")
    values = [
        _clean(dl.select_one("dd").get_text(" ", strip=True))
        if dl.select_one("dd")
        else ""
        for dl in dls
    ]
    apply_period, apply_start, apply_end, apply_start_date, apply_end_date = _datetime_range(
        values[0]
    )
    period, start, end, schedule = _library_period(values[1])
    target_text = values[2]
    if not target_text:
        raise UlsanBukguContractError("library card target is missing")
    current, total, wait = _library_capacity(values[3])
    state_controls = card.select(".btn_box a.btn_sm:not(.btn_check)")
    check_controls = card.select(".btn_box a.btn_check[href]")
    if len(state_controls) != 1 or len(check_controls) != 1:
        raise UlsanBukguContractError("library card controls changed")
    source_status, status = _library_state(state_controls[0])
    _library_bound_url(
        source,
        page_url,
        check_controls[0].get("href"),
        source.list_path.replace("list.do", "user.do"),
        identity,
    )
    application_url = ""
    if status == "OPEN":
        application_url = _library_bound_url(
            source,
            page_url,
            state_controls[0].get("href"),
            source.application_path,
            identity,
        )
    elif _clean(state_controls[0].get("href")) != "#javascript:;":
        raise UlsanBukguContractError("non-open library status exposes an application link")
    branch = (
        source_branch
        if source_branch == LIBRARY_UMBRELLA_BRANCH
        else f"{ULSAN_BUKGU_MUNICIPALITY_NAME} · {source_branch}"
    )
    result = _base_row(
        target,
        provider=ULSAN_BUKGU_LIBRARY_PROVIDER,
        identity=f"{source.key}:{identity}",
        title=title,
        branch=branch,
        branch_code=f"{ULSAN_BUKGU_LIBRARY_PROVIDER}:{_normalized(source_branch)}",
        raw_url=raw_url,
        status=status,
        source_status=source_status,
        period=period,
        start=start,
        end=end,
        category=category,
        source_kind=f"library_{source.key}",
    )
    result.update(
        {
            "apply_period": apply_period,
            "apply_start_at": apply_start,
            "apply_end_at": apply_end,
            "apply_start_date": apply_start_date,
            "apply_end_date": apply_end_date,
            "schedule_raw": schedule,
            "target": target_text,
            "capacity": total,
            "capacity_current": current,
            "capacity_total": total,
            "capacity_remaining": max(0, total - current),
            "waitlist_total": wait,
            "venue_name": source_branch,
            "application_url": application_url,
            "application_type": "ONLINE_RESERVATION" if application_url else "",
            "reservation_available": bool(application_url),
        }
    )
    result["raw_fields"].update(
        {
            "catalogue": source.key,
            "edu_idx": identity,
            "source_page": page,
            "source_branch": source_branch,
        }
    )
    return result


def _library_fingerprint(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("period")),
            _clean(row.get("schedule_raw")),
            _clean(row.get("branch")),
            _clean(row.get("status")),
        )
        for row in rows
    )


def _library_detail_pairs(table: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for dl in table.select("tbody dl.info"):
        key_node = dl.select_one("dt")
        value_node = dl.select_one("dd")
        key = _clean(key_node.get_text(" ", strip=True) if key_node else "")
        value = _clean(value_node.get_text(" ", strip=True) if value_node else "")
        if not key or key in pairs:
            raise UlsanBukguContractError("duplicate/empty library detail field")
        pairs[key] = value
    return pairs


def _detail_date_only(value: Any) -> tuple[str, date, date]:
    text = _clean(value)
    return _date_range(text)


def _library_detail_state(control: Any) -> tuple[str, str]:
    classes = set(control.get("class") or [])
    text = _clean(control.get_text(" ", strip=True))
    known = [
        ("btn_receipt", "신청중", "OPEN"),
        ("btn_prepare", "신청준비", "SCHEDULED"),
        ("btn_close", "기간종료", "CLOSED"),
        ("btn_end", "신청마감", "CLOSED"),
    ]
    matches = [(label, status) for cls, label, status in known if cls in classes]
    if len(matches) != 1 or text != matches[0][0]:
        raise UlsanBukguContractError("unknown library detail status")
    return text, matches[0][1]


def _enrich_library_current(
    source: LibraryCatalogue,
    row: dict[str, Any],
    soup: BeautifulSoup,
) -> None:
    # Free-form descriptions may legitimately embed their own presentation tables.
    # The structured contract is the one direct child of ``.table_bview``.
    tables = soup.select("#contents .table_bview > table")
    if len(tables) != 1:
        raise UlsanBukguContractError("expected one library detail table")
    table = tables[0]
    headers = table.select("thead th")
    if len(headers) != 1:
        raise UlsanBukguContractError("library detail title header changed")
    state_controls = headers[0].select("a.btn_sm")
    if len(state_controls) != 1:
        raise UlsanBukguContractError("library detail status control changed")
    source_detail_status, detail_status = _library_detail_state(state_controls[0])
    title_text = _clean(headers[0].get_text(" ", strip=True))
    title_text = _clean(title_text.replace(source_detail_status, "", 1))
    detail_title, _category = _library_display_title(title_text)
    if _normalized(detail_title) != _normalized(row["title"]):
        raise UlsanBukguContractError("library list/detail title mismatch")
    if detail_status != row["status"]:
        raise UlsanBukguContractError("library list/detail status mismatch")
    pairs = _library_detail_pairs(table)
    venue_key = "교육장소" if source == LIBRARY_COURSES else "행사장소"
    required = {
        "운영기간",
        "운영시간",
        "신청기간",
        "신청방법",
        "참가대상",
        "모집인원",
        venue_key,
        "참가비",
    }
    if not required.issubset(pairs):
        raise UlsanBukguContractError("library detail fields changed")
    period, start, end = _detail_date_only(pairs["운영기간"])
    if period != row["period"] or start != row["start_date"] or end != row["end_date"]:
        raise UlsanBukguContractError("library list/detail period mismatch")
    schedule = _clean(pairs["운영시간"]).replace(" 요일", "요일")
    if _normalized(schedule) != _normalized(row["schedule_raw"]):
        raise UlsanBukguContractError("library list/detail schedule mismatch")
    _apply_period, apply_start, apply_end = _detail_date_only(pairs["신청기간"])
    if (
        apply_start != row["apply_start_date"]
        or apply_end != row["apply_end_date"]
    ):
        raise UlsanBukguContractError("library list/detail application period mismatch")
    if _normalized(pairs["참가대상"]) != _normalized(row["target"]):
        raise UlsanBukguContractError("library list/detail target mismatch")
    current, total, _wait = _library_capacity(pairs["모집인원"])
    if current != row["capacity_current"] or total != row["capacity_total"]:
        raise UlsanBukguContractError("library list/detail capacity mismatch")
    identity = _clean(row["raw_fields"].get("edu_idx"))
    application_controls = table.find_next_siblings()
    del application_controls  # controls live outside the table, queried below
    application_links = []
    for link in soup.select("a.con_btn.btn_receipt[href]"):
        try:
            application_links.append(
                _library_bound_url(
                    source,
                    row["raw_url"],
                    link.get("href"),
                    source.application_path,
                    identity,
                )
            )
        except UlsanBukguContractError:
            raise
    if detail_status == "OPEN":
        expected = _library_application_url(source, identity)
        if application_links != [expected] or row.get("application_url") != expected:
            raise UlsanBukguContractError("open library detail lacks one course-bound application control")
    elif application_links or row.get("application_url"):
        raise UlsanBukguContractError("non-open library detail exposes an application control")
    venue = _clean(pairs[venue_key])
    source_branch = _clean(row["raw_fields"].get("source_branch"))
    room = venue
    if source_branch in LIBRARY_BRANCHES and venue.startswith(source_branch):
        room = _clean(venue[len(source_branch) :])
    row.update(
        {
            "room": room,
            "venue_name": venue or source_branch,
            "fee": _clean(pairs["참가비"]),
            "application_method_raw": _clean(pairs["신청방법"]),
        }
    )
    row["raw_fields"].update(
        {"detail_verified": True, "detail_status": source_detail_status}
    )


def _library_semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("branch")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _normalized(row.get("schedule_raw")),
    )


def _collapse_library_semantic_duplicates(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_library_semantic_signature(row)].append(row)
    collapsed: list[dict[str, Any]] = []
    duplicate_groups = 0
    collapsed_count = 0
    status_rank = {"OPEN": 3, "SCHEDULED": 2, "CLOSED": 1}
    for signature, group in groups.items():
        del signature
        if len(group) == 1:
            collapsed.append(group[0])
            continue
        duplicate_groups += 1
        open_rows = [row for row in group if row.get("status") == "OPEN"]
        if len(open_rows) > 1:
            raise UlsanBukguContractError(
                "semantic duplicate offering has multiple active application controls"
            )

        def rank(row: Mapping[str, Any]) -> tuple[int, int, int, str]:
            identity = _clean(row.get("raw_fields", {}).get("edu_idx"))
            numeric = int(identity) if identity.isdigit() else 10**12
            return (
                status_rank.get(_clean(row.get("status")), 0),
                int(row.get("capacity_total") or 0),
                -numeric,
                _clean(row.get("provider_course_id")),
            )

        chosen = max(group, key=rank)
        aliases = sorted(
            _clean(row.get("provider_course_id"))
            for row in group
            if row is not chosen
        )
        chosen["raw_fields"]["semantic_alias_course_ids"] = aliases
        collapsed.append(chosen)
        collapsed_count += len(group) - 1
    return collapsed, duplicate_groups, collapsed_count


def collect_ulsan_bukgu_library_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect both complete Buk-gu library education catalogues."""

    parser = ULSAN_BUKGU_LIBRARY_PARSER
    if not is_ulsan_bukgu_library_target(target):
        meta = _common_failure(parser, "target provider/url is not canonical Buk-gu library")
        return [], parser, meta
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            meta = _common_failure(parser, "managed fetcher and session_factory injection are required")
            return [], parser, meta
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory
    allowed_pages = max(0, int(max_pages))
    if allowed_pages < len(LIBRARY_CATALOGUES) * 2:
        meta = _common_failure(
            parser,
            "max_pages cap cannot cover both catalogue roots and clamps",
            required_list_requests=len(LIBRARY_CATALOGUES) * 2,
            source_cap_reached=True,
        )
        return [], parser, meta

    cutoff = _today(today)
    errors: list[str] = []
    candidates: list[dict[str, Any]] = []
    first_payloads: dict[str, tuple[BeautifulSoup, list[Any], int, int]] = {}
    list_requests = 0
    data_pages = 0
    clamp_pages = 0
    clamp_verified_count = 0
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    invalid_count = 0
    source_totals: dict[str, int] = {}
    source_pages: dict[str, int] = {}
    session_obj = session_factory()
    try:
        for source in LIBRARY_CATALOGUES:
            try:
                soup = _response_soup(fetcher(session_obj, source.list_url, timeout))
                list_requests += 1
                data_pages += 1
                cards, total, pages = _library_page_contract(source, soup, 1)
                first_payloads[source.key] = (soup, cards, total, pages)
                source_totals[source.key] = total
                source_pages[source.key] = pages
            except Exception as exc:
                errors.append(
                    f"{source.key} page 1: {type(exc).__name__}: {_clean(exc)}"
                )
                break

        required_list_requests = sum(pages + 1 for pages in source_pages.values())
        source_cap_reached = bool(required_list_requests and required_list_requests > allowed_pages)
        if not errors and source_cap_reached:
            errors.append(
                f"max_pages cap allows {allowed_pages} of {required_list_requests} required library page/clamp requests"
            )

        if not errors:
            for source in LIBRARY_CATALOGUES:
                _soup, first_cards, total, pages = first_payloads[source.key]
                page_rows: list[dict[str, Any]] = []
                try:
                    page_rows.extend(
                        _library_row(target, source, card, source.list_url, 1)
                        for card in first_cards
                    )
                    last_rows = page_rows if pages == 1 else []
                    for page in range(2, pages + 1):
                        page_url = _library_page_url(source, page)
                        soup = _response_soup(fetcher(session_obj, page_url, timeout))
                        list_requests += 1
                        data_pages += 1
                        cards, declared_total, declared_pages = _library_page_contract(
                            source, soup, page
                        )
                        if declared_total != total or declared_pages != pages:
                            raise UlsanBukguContractError(
                                "library declared total/page count changed during crawl"
                            )
                        parsed_rows = [
                            _library_row(target, source, card, page_url, page)
                            for card in cards
                        ]
                        page_rows.extend(parsed_rows)
                        if page == pages:
                            last_rows = parsed_rows
                    clamp_page = pages + 1
                    clamp_url = _library_page_url(source, clamp_page)
                    clamp_soup = _response_soup(
                        fetcher(session_obj, clamp_url, timeout)
                    )
                    list_requests += 1
                    clamp_pages += 1
                    clamp_cards, declared_total, declared_pages = _library_page_contract(
                        source, clamp_soup, clamp_page, clamp=True
                    )
                    if declared_total != total or declared_pages != pages:
                        raise UlsanBukguContractError("library clamp total/page count changed")
                    clamp_rows = [
                        _library_row(target, source, card, clamp_url, pages)
                        for card in clamp_cards
                    ]
                    if _library_fingerprint(last_rows) != _library_fingerprint(clamp_rows):
                        raise UlsanBukguContractError(
                            "library overrun did not clamp to identical last page"
                        )
                    if len(page_rows) != total:
                        raise UlsanBukguContractError(
                            f"library complete count mismatch: parsed={len(page_rows)} declared={total}"
                        )
                    clamp_verified_count += 1
                    candidates.extend(page_rows)
                except Exception as exc:
                    invalid_count += 1
                    errors.append(
                        f"{source.key}: {type(exc).__name__}: {_clean(exc)}"
                    )
                    break

        identities = [_clean(row.get("provider_course_id")) for row in candidates]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate library course identities")
        urls = [_clean(row.get("raw_url")) for row in candidates]
        duplicate_url_count = len(urls) - len(set(urls))
        if duplicate_url_count:
            errors.append(f"{duplicate_url_count} duplicate library detail URLs")

        current_rows = [row for row in candidates if row["end_date"] >= cutoff]
        expired_count = len(candidates) - len(current_rows)
        allowed_details = max(0, int(detail_limit))
        if len(current_rows) > allowed_details:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {allowed_details} of {len(current_rows)} required current library details"
            )

        if not errors:
            source_by_key = {source.key: source for source in LIBRARY_CATALOGUES}
            for row in current_rows:
                detail_attempts += 1
                source = source_by_key[_clean(row["raw_fields"].get("catalogue"))]
                try:
                    soup = _response_soup(
                        fetcher(session_obj, _clean(row["raw_url"]), timeout)
                    )
                    _enrich_library_current(source, row, soup)
                    detail_pages += 1
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: detail {type(exc).__name__}: {_clean(exc)}"
                    )

        semantic_groups = 0
        semantic_collapsed_count = 0
        collapsed_rows: list[dict[str, Any]] = []
        if not errors:
            try:
                collapsed_rows, semantic_groups, semantic_collapsed_count = (
                    _collapse_library_semantic_duplicates(current_rows)
                )
            except Exception as exc:
                errors.append(f"semantic collapse: {type(exc).__name__}: {_clean(exc)}")
        semantic_counts = Counter(
            _library_semantic_signature(row) for row in collapsed_rows
        )
        remaining_semantic_duplicates = sum(
            count - 1 for count in semantic_counts.values() if count > 1
        )
        if remaining_semantic_duplicates:
            errors.append(
                f"{remaining_semantic_duplicates} semantic duplicates remain after collapse"
            )

        result: list[dict[str, Any]] = []
        if not errors:
            result = collapsed_rows
            if dedupe_rows is not None:
                deduped = list(dedupe_rows(result))
                if len(deduped) != len(result):
                    errors.append("downstream dedupe changed complete library snapshot count")
                else:
                    result = deduped
        snapshot_complete = not errors
        if not snapshot_complete:
            result = []
        no_current_data = snapshot_complete and not result
        branch_counts = Counter(_clean(row.get("branch")) for row in result)
        status_counts = Counter(_clean(row.get("status")) for row in result)
        declared_total = sum(source_totals.values())
        meta = {
            "parser": parser,
            "pages": list_requests,
            "data_pages": data_pages,
            "clamp_pages": clamp_pages,
            "list_requests": list_requests,
            "required_list_requests": required_list_requests,
            "request_count": list_requests + detail_attempts,
            "source_rows": len(candidates),
            "declared_source_rows": declared_total,
            "source_totals": source_totals,
            "source_pages": source_pages,
            "valid_count": len(candidates),
            "invalid_count": invalid_count,
            "duplicate_count": duplicate_count,
            "duplicate_url_count": duplicate_url_count,
            "semantic_candidate_duplicate_count": semantic_collapsed_count,
            "semantic_duplicate_groups": semantic_groups,
            "semantic_collapsed_count": semantic_collapsed_count,
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "detail_candidates": len(current_rows),
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_errors": detail_errors,
            "catalogue_count": len(LIBRARY_CATALOGUES),
            "clamp_verified_count": clamp_verified_count,
            "clamp_verified": snapshot_complete and clamp_verified_count == len(LIBRARY_CATALOGUES),
            "pagination_detected": any(page > 1 for page in source_pages.values()),
            "pagination_complete": bool(
                snapshot_complete
                and data_pages == sum(source_pages.values())
                and clamp_verified_count == len(LIBRARY_CATALOGUES)
            ),
            "details_complete": bool(
                snapshot_complete and detail_pages == len(current_rows)
            ),
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "status_counts": dict(status_counts),
            "no_current_data": no_current_data,
            "no_current_reason": (
                "all complete library programme rows are expired"
                if no_current_data and candidates
                else "both complete library catalogues contain zero rows"
                if no_current_data
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
        }
        return result, parser, meta
    finally:
        close = getattr(session_obj, "close", None)
        if callable(close):
            close()


def collect_ulsan_bukgu_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 100,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if is_ulsan_bukgu_public_target(target):
        return collect_ulsan_bukgu_public_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    if is_ulsan_bukgu_library_target(target):
        return collect_ulsan_bukgu_library_courses(
            target,
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            **kwargs,
        )
    parser = "ulsan_bukgu_exact_source_router"
    reason = (
        "target is an explicitly rejected alias/non-owner"
        if is_ulsan_bukgu_rejected_alias_target(target)
        else "target provider/url does not match an exact Ulsan Buk-gu source"
    )
    meta = _common_failure(parser, reason)
    return [], parser, meta


def collect_from_url(
    target: Any,
    timeout: int = 20,
    max_depth: int = 0,
    max_pages: int = 20,
    detail_limit: int = 100,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    del max_depth
    return collect_ulsan_bukgu_courses(
        target,
        timeout=timeout,
        max_pages=max_pages,
        detail_limit=detail_limit,
        **kwargs,
    )


collect = collect_ulsan_bukgu_courses


__all__ = [
    "LIBRARY_BRANCHES",
    "LIBRARY_CATALOGUES",
    "LIBRARY_COURSES",
    "LIBRARY_EVENTS",
    "PUBLIC_FACILITIES",
    "ULJU_FOREIGN_LECTURE_URL",
    "ULSAN_BUKGU_LIBRARY_BAD_CANDIDATE_URL",
    "ULSAN_BUKGU_LIBRARY_BAD_CANDIDATE_ID",
    "ULSAN_BUKGU_LIBRARY_EVENT_URL",
    "ULSAN_BUKGU_LIBRARY_PARSER",
    "ULSAN_BUKGU_LIBRARY_PROVIDER",
    "ULSAN_BUKGU_LIBRARY_URL",
    "ULSAN_BUKGU_LIFELONG_ALIAS_API",
    "ULSAN_BUKGU_LIFELONG_ALIAS_PROVIDER",
    "ULSAN_BUKGU_LIFELONG_ALIAS_URL",
    "ULSAN_BUKGU_MUNICIPALITY_CODE",
    "ULSAN_BUKGU_MUNICIPALITY_NAME",
    "ULSAN_BUKGU_PUBLIC_PARSER",
    "ULSAN_BUKGU_PUBLIC_PROVIDER",
    "ULSAN_BUKGU_PUBLIC_URL",
    "ULSAN_BUKGU_SINGLE_DETAIL_ALIAS_URL",
    "ULSAN_BUKGU_YES_DISCOVERY_PROVIDER",
    "ULSAN_BUKGU_YES_DISCOVERY_URL",
    "UlsanBukguContractError",
    "collect",
    "collect_from_url",
    "collect_ulsan_bukgu_courses",
    "collect_ulsan_bukgu_library_courses",
    "collect_ulsan_bukgu_public_courses",
    "is_target",
    "is_ulsan_bukgu_library_target",
    "is_ulsan_bukgu_public_target",
    "is_ulsan_bukgu_rejected_alias_target",
    "is_ulsan_bukgu_target",
]
