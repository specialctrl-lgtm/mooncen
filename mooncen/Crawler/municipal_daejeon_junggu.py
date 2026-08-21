"""Fail-closed collector for Daejeon Jung-gu's official education catalogues.

The district publishes two independent public course stores on the same
official host: the lifelong-learning catalogue under ``lecCourse`` and the
district information-literacy catalogue under ``infoCourse``.  The
``lecReserve`` menu is not a third catalogue; anonymous access is immediately
sent to identity verification and it only exposes a user's own applications.

One snapshot is emitted only after the four official site maps still expose
exactly that ownership fan-out, both catalogues reconcile their advertised
totals with every page, the immediate post-last pages are empty, page one is
stable on re-read, and every current/future course passes its detail and
course-bound application-control contract.  Instructor names, contacts,
emails, arbitrary descriptions, attachments, and source HTML are deliberately
excluded from persisted rows.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


DAEJEON_JUNGGU_PROVIDER = "MUNI_WWW_DJJUNGGU_GO_KR_6A89B08A"
DAEJEON_JUNGGU_CANONICAL_CANDIDATE_ID = "MUNI_IR_A636404A8358"
DAEJEON_JUNGGU_HOST = "www.djjunggu.go.kr"
DAEJEON_JUNGGU_MUNICIPALITY_CODE = "3014000000"
DAEJEON_JUNGGU_MUNICIPALITY_NAME = "대전광역시 중구"
DAEJEON_JUNGGU_CANONICAL_PATH = (
    "/prog/lecCourse/lec/lll/sub02_01_02/list.do"
)
DAEJEON_JUNGGU_CANONICAL_URL = (
    f"https://{DAEJEON_JUNGGU_HOST}{DAEJEON_JUNGGU_CANONICAL_PATH}"
)
DAEJEON_JUNGGU_CONFIRMATION_PATH = (
    "/prog/lecReserve/lec/lll/sub02_01_03/list.do"
)
DAEJEON_JUNGGU_CONFIRMATION_URL = (
    f"https://{DAEJEON_JUNGGU_HOST}{DAEJEON_JUNGGU_CONFIRMATION_PATH}"
)
DAEJEON_JUNGGU_PAGE_SIZE = 10
DAEJEON_JUNGGU_MAX_WORKERS = 8
DAEJEON_JUNGGU_FETCH_ATTEMPTS = 2
DAEJEON_JUNGGU_PARSER = (
    "daejeon_junggu_official_sitemaps+lifelong_and_information_fanout+"
    "all_pages+empty_sentinels+stable_rechecks+current_details+pii_allowlist"
)
DAEJEON_JUNGGU_OWNERSHIP_SCOPE = (
    "djjunggu_official_lifelong_and_information_education_catalogues"
)


@dataclass(frozen=True)
class DaejeonJungguCatalogue:
    key: str
    label: str
    branch: str
    list_path: str
    detail_path: str
    site_key: str
    expected_page_title: str
    expected_heading: str
    expected_headers: tuple[str, ...]
    status_options: tuple[tuple[str, str], ...]

    @property
    def list_url(self) -> str:
        return f"https://{DAEJEON_JUNGGU_HOST}{self.list_path}"


DAEJEON_JUNGGU_CATALOGUES: tuple[DaejeonJungguCatalogue, ...] = (
    DaejeonJungguCatalogue(
        key="lifelong",
        label="평생학습",
        branch="대전 중구 평생학습관",
        list_path=DAEJEON_JUNGGU_CANONICAL_PATH,
        detail_path="/prog/lecCourse/lec/lll/sub02_01_02/view.do",
        site_key="lll",
        expected_page_title="대전 중구 평생학습관",
        expected_heading="온라인 수강 신청",
        expected_headers=(
            "학기",
            "강좌명/강사명",
            "접수기간",
            "교육기간",
            "신청인원/모집인원",
            "시간",
            "상태",
        ),
        status_options=(
            ("", "-강좌상태-"),
            ("1", "모집중"),
            ("2", "모집예정"),
            ("3", "교육중"),
            ("4", "교육종료"),
        ),
    ),
    DaejeonJungguCatalogue(
        key="information",
        label="정보화교육",
        branch="대전광역시 중구 정보화교육장",
        list_path="/prog/infoCourse/infoedu/kr/sub04_01_02_02/list.do",
        detail_path="/prog/infoCourse/infoedu/kr/sub04_01_02_02/view.do",
        site_key="kr",
        expected_page_title="대전광역시 중구청",
        expected_heading="교육신청",
        expected_headers=(
            "강좌명/강사명",
            "접수기간",
            "교육기간",
            "신청인원/모집인원",
            "시간",
            "상태",
        ),
        status_options=(
            ("", "-강좌상태-"),
            ("1", "모집중"),
            ("2", "대기중"),
            ("3", "교육중"),
            ("4", "교육종료"),
        ),
    ),
)
DAEJEON_JUNGGU_CATALOGUE_BY_KEY = {
    item.key: item for item in DAEJEON_JUNGGU_CATALOGUES
}
DAEJEON_JUNGGU_INFORMATION_URL = DAEJEON_JUNGGU_CATALOGUE_BY_KEY[
    "information"
].list_url

DAEJEON_JUNGGU_SITEMAPS: Mapping[str, str] = {
    "kr": f"https://{DAEJEON_JUNGGU_HOST}/kr/sitemap.do",
    "lll": f"https://{DAEJEON_JUNGGU_HOST}/lll/sitemap.do",
    "health": f"https://{DAEJEON_JUNGGU_HOST}/health/sitemap.do",
    "hyo": f"https://{DAEJEON_JUNGGU_HOST}/hyo/sitemap.do",
}
DAEJEON_JUNGGU_EXPECTED_SITEMAP_ROUTES: Mapping[str, frozenset[str]] = {
    "kr": frozenset({DAEJEON_JUNGGU_INFORMATION_URL}),
    "lll": frozenset(
        {DAEJEON_JUNGGU_CANONICAL_URL, DAEJEON_JUNGGU_CONFIRMATION_URL}
    ),
    "health": frozenset(),
    "hyo": frozenset(),
}

# Detail-only candidates are aliases of the canonical archive.  The
# information list is a disjoint fan-out leaf owned by the same collector.
DAEJEON_JUNGGU_DETAIL_ALIAS_URLS: tuple[str, ...] = (
    (
        "https://www.djjunggu.go.kr/prog/lecCourse/lec/lll/"
        "sub02_01_02/view.do?eduNo=263"
    ),
    (
        "https://www.djjunggu.go.kr/prog/lecCourse/lec/lll/"
        "sub02_01_02/view.do?eduNo=322"
    ),
)
DAEJEON_JUNGGU_FANOUT_URLS: tuple[str, ...] = (
    DAEJEON_JUNGGU_INFORMATION_URL,
)

# Live exhaustive audit on 2026-07-21.  OK's two education leaves were read
# with its official Jung-gu selector (313 + 68 rows) and compared with both
# independent district archives (217 + 30 rows).  IDs live in different
# namespaces; neither normalized title nor normalized title+dates overlapped.
DAEJEON_JUNGGU_OK_OVERLAP_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "independent_source_rows": 247,
    "independent_catalogue_totals": {"lifelong": 217, "information": 30},
    "ok_junggu_source_rows": 381,
    "ok_junggu_category_totals": {"8101": 313, "8102": 68},
    "normalized_title_overlap_count": 0,
    "normalized_title_period_overlap_count": 0,
    "identity_namespace_overlap_applicable": False,
    "conclusion": "independent_non_alias_catalogues",
}


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_DATE_TIME_RE = re.compile(
    r"(?<!\d)(20\d{2}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})"
    r"(?:\s+(\d{1,2}:\d{2}))?(?!\d)"
)
_CAPACITY_RE = re.compile(r"^\s*([\d,]+)\s*/\s*([\d,]+)")
_WAIT_RE = re.compile(r"대기\s*\(\s*([\d,]+)\s*/\s*([\d,]+)\s*\)")
_DIGITS_RE = re.compile(r"\d+")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_COURSE_MODULE_RE = re.compile(
    r"^/prog/(?:lec|info)(?:Course|Reserve)/[^?#]+/list\.do$",
    flags=re.IGNORECASE,
)

_STATUS_MAP: Mapping[str, str] = {
    "모집중": "OPEN",
    "대기 신청중": "WAITING",
    "모집예정": "SCHEDULED",
    "대기중": "SCHEDULED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
    "모집마감": "CLOSED",
    "접수마감": "CLOSED",
}
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "teacher",
        "phone",
        "contact",
        "contact_phone",
        "email",
        "manager",
        "manager_name",
        "source_html",
        "description_html",
        "attachments",
    }
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "catalogue",
        "source_page",
        "source_semester",
        "source_status",
        "source_application_period",
        "source_education_period",
        "application_control_present",
        "application_control_contract",
        "detail_verified",
        "detail_unpublished",
        "source_identity_kind",
        "target_source_omission",
        "fee_source_omission",
        "venue_source_omission",
        "ownership_source_path",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _clean(value).casefold(), flags=re.UNICODE)


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _exact_https_url(value: Any, path: str, *, allow_query: bool = False) -> bool:
    parsed = urlparse(_clean(value))
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == DAEJEON_JUNGGU_HOST
        and parsed.port is None
        and parsed.path == path
        and (allow_query or not parsed.query)
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_daejeon_junggu_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == DAEJEON_JUNGGU_PROVIDER
        and _exact_https_url(
            _target_value(target, "url"), DAEJEON_JUNGGU_CANONICAL_PATH
        )
    )


def is_daejeon_junggu_owned_alias_target(target: Any) -> bool:
    value = _clean(_target_value(target, "url"))
    if value in {
        DAEJEON_JUNGGU_CONFIRMATION_URL,
        *DAEJEON_JUNGGU_FANOUT_URLS,
        *DAEJEON_JUNGGU_DETAIL_ALIAS_URLS,
    }:
        return True
    parsed = urlparse(value)
    if not _exact_https_url(value, parsed.path, allow_query=True):
        return False
    if parsed.path != DAEJEON_JUNGGU_CATALOGUE_BY_KEY["lifelong"].detail_path:
        return False
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    return bool(
        len(pairs) == 1
        and pairs[0][0] == "eduNo"
        and _DIGITS_RE.fullmatch(pairs[0][1])
    )


is_target = is_daejeon_junggu_education_target


def daejeon_junggu_list_url(catalogue_key: Any, page: Any = 1) -> str:
    catalogue = DAEJEON_JUNGGU_CATALOGUE_BY_KEY.get(_clean(catalogue_key))
    raw_page = _clean(page)
    if catalogue is None or not raw_page.isdigit() or int(raw_page) < 1:
        return ""
    return catalogue.list_url + "?" + urlencode({"pageIndex": int(raw_page)})


def daejeon_junggu_detail_url(catalogue_key: Any, identity: Any) -> str:
    catalogue = DAEJEON_JUNGGU_CATALOGUE_BY_KEY.get(_clean(catalogue_key))
    value = _clean(identity)
    if catalogue is None or not _DIGITS_RE.fullmatch(value):
        return ""
    return (
        f"https://{DAEJEON_JUNGGU_HOST}{catalogue.detail_path}?"
        + urlencode({"eduNo": value})
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
    for _attempt in range(DAEJEON_JUNGGU_FETCH_ATTEMPTS):
        try:
            response = current.get(url, timeout=timeout)
            response.raise_for_status()
            final = urlparse(_clean(getattr(response, "url", url)))
            if (final.hostname or "").rstrip(".").lower() != DAEJEON_JUNGGU_HOST:
                raise ValueError("official request redirected off host")
            return response
        except (requests.RequestException, ValueError) as exc:
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


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    status = getattr(value, "status_code", None)
    if status is not None and not (200 <= int(status) < 300):
        raise ValueError(f"unexpected HTTP status {status}")
    if isinstance(value, (str, bytes, bytearray)):
        payload = value
    else:
        payload = getattr(value, "content", None)
        if payload is None:
            payload = getattr(value, "text", None)
    if not payload:
        raise ValueError("empty HTML response")
    return BeautifulSoup(payload, "html.parser")


def _fetch_many(
    items: Iterable[tuple[Any, str]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, BeautifulSoup], list[str]]:
    materialized = list(items)
    results: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []

    def work(key: Any, url: str) -> tuple[Any, BeautifulSoup]:
        current = session_factory()
        try:
            return key, _coerce_soup(fetcher(current, url, timeout))
        finally:
            _close_quietly(current)

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(materialized) or 1))) as pool:
        futures = {
            pool.submit(work, key, url): (key, url) for key, url in materialized
        }
        for future in as_completed(futures):
            key, _url = futures[future]
            try:
                result_key, soup = future.result()
                results[result_key] = soup
            except Exception as exc:
                errors.append(f"{key}: {_clean(exc)}")
    return results, errors


def _options(node: Any) -> tuple[tuple[str, str], ...]:
    if node is None:
        return ()
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in node.select(":scope > option")
    )


def _schema_and_total(
    soup: BeautifulSoup,
    catalogue: DaejeonJungguCatalogue,
    page: int,
) -> tuple[int, int, list[str]]:
    label = f"{catalogue.key} page {page}"
    errors: list[str] = []
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if catalogue.expected_page_title not in page_title:
        errors.append(f"{label}: official page title changed")
    headings = [
        _clean(node.get_text(" ", strip=True))
        for node in soup.select("#contents h2, #contents h1, .content_wrap h2, .content_wrap h1")
    ]
    if headings and not any(catalogue.expected_heading == item for item in headings):
        errors.append(f"{label}: official content heading changed")

    forms = soup.select('form[name="eduSearchForm"]')
    if len(forms) != 1:
        return 0, 1, [*errors, f"{label}: list search form missing or duplicated"]
    form = forms[0]
    if _clean(form.get("method")).lower() != "post":
        errors.append(f"{label}: list method changed")
    if _clean(form.get("action")) != catalogue.list_path:
        errors.append(f"{label}: list action changed")
    expected_hidden = {
        "pageUnit": str(DAEJEON_JUNGGU_PAGE_SIZE),
        # The official search form deliberately resets every new search to
        # page one; pagination itself is carried by the request URL.
        "pageIndex": "1",
        "pageSize": str(DAEJEON_JUNGGU_PAGE_SIZE),
        "suborgCode": "",
        "searchCondition": "subject",
    }
    for name, expected in expected_hidden.items():
        nodes = form.select(f'input[name="{name}"]')
        if len(nodes) != 1 or _clean(nodes[0].get("value")) != expected:
            errors.append(f"{label}: hidden field {name} changed")
    if _options(form.select_one('select[name="state"]')) != catalogue.status_options:
        errors.append(f"{label}: status options changed")
    expected_date_options = (
        ("", "-기준-"),
        ("date", "예약일"),
        ("reqdate", "교육일"),
    )
    if _options(form.select_one('select[name="dateType"]')) != expected_date_options:
        errors.append(f"{label}: date filter options changed")
    if catalogue.key == "lifelong":
        groups = _options(form.select_one('select[name="searchGroupNo"]'))
        if not groups or groups[0] != ("", ":: 학기 전체 ::"):
            errors.append(f"{label}: semester selector changed")
    else:
        years = _options(form.select_one('select[name="year"]'))
        if not years or years[0] != ("", "-년도 전체-") or any(
            value and not re.fullmatch(r"20\d{2}", value) for value, _text in years
        ):
            errors.append(f"{label}: year selector changed")

    counters = soup.select(".program--count strong")
    counter_value = _clean(counters[0].get_text()) if len(counters) == 1 else ""
    if not counter_value.replace(",", "").isdigit():
        errors.append(f"{label}: advertised total changed")
        total = 0
    else:
        total = int(counter_value.replace(",", ""))
    last = max(1, math.ceil(total / DAEJEON_JUNGGU_PAGE_SIZE))

    tables = soup.select("div.no-more-tables > table.table-default")
    if len(tables) != 1:
        errors.append(f"{label}: official course table missing or duplicated")
    else:
        headers = tuple(
            _normalized(node.get_text(" ", strip=True))
            for node in tables[0].select("thead th")
        )
        if headers != tuple(_normalized(item) for item in catalogue.expected_headers):
            errors.append(f"{label}: course table headers changed")
    if total > DAEJEON_JUNGGU_PAGE_SIZE:
        if last > 10:
            last_links = soup.select('ul.pagination a[aria-label="last"]')
        else:
            last_links = [
                node
                for node in soup.select("ul.pagination a[href]")
                if parse_qsl(urlparse(_clean(node.get("href"))).query)
                == [("pageIndex", str(last))]
            ]
            if page == last:
                active_last = [
                    node
                    for node in soup.select("ul.pagination li.active a")
                    if _clean(node.get_text(" ", strip=True)) == str(last)
                ]
                if active_last and not last_links:
                    last_links = active_last
        if len(last_links) != 1:
            errors.append(f"{label}: advertised last-page control missing")
        elif last > 10 or page != last:
            pairs = parse_qsl(urlparse(_clean(last_links[0].get("href"))).query)
            if pairs != [("pageIndex", str(last))]:
                errors.append(f"{label}: advertised last-page control changed")
    return total, last, errors


def _date_pair(value: Any, field: str) -> tuple[date, date, str]:
    raw = _clean(value)
    matches = _DATE_TIME_RE.findall(raw)
    if len(matches) != 2:
        raise ValueError(f"{field}: expected exactly two dates")
    parsed_dates: list[date] = []
    rendered: list[str] = []
    for raw_date, raw_time in matches:
        date_match = _DATE_RE.search(raw_date)
        if date_match is None:
            raise ValueError(f"{field}: invalid date")
        try:
            item = date(*(int(part) for part in date_match.groups()))
        except ValueError as exc:
            raise ValueError(f"{field}: invalid calendar date") from exc
        if raw_time:
            hour, minute = (int(part) for part in raw_time.split(":"))
            if hour > 23 or minute > 59:
                raise ValueError(f"{field}: invalid time")
        parsed_dates.append(item)
        rendered.append(item.isoformat() + (f" {raw_time}" if raw_time else ""))
    if bool(matches[0][1]) != bool(matches[1][1]):
        raise ValueError(f"{field}: incomplete time range")
    return parsed_dates[0], parsed_dates[1], " ~ ".join(rendered)


def _detail_identity(
    href: Any,
    catalogue: DaejeonJungguCatalogue,
    page: int,
) -> tuple[str, str]:
    absolute = urljoin(catalogue.list_url, _clean(href))
    parsed = urlparse(absolute)
    if not _exact_https_url(absolute, catalogue.detail_path, allow_query=True):
        raise ValueError("detail link left the official catalogue")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if len(pairs) not in {2, 3} or len({name for name, _value in pairs}) != len(pairs):
        raise ValueError("detail link query changed")
    values = dict(pairs)
    if set(values) not in ({"pageIndex", "eduNo"}, {"pageIndex", "eduNo", "oneInwon"}):
        raise ValueError("detail link query fields changed")
    identity = _clean(values.get("eduNo"))
    if (
        _clean(values.get("pageIndex")) != str(page)
        or not _DIGITS_RE.fullmatch(identity)
        or ("oneInwon" in values and _clean(values["oneInwon"]))
    ):
        raise ValueError("detail link identity/page changed")
    canonical = daejeon_junggu_detail_url(catalogue.key, identity)
    if not canonical:
        raise ValueError("detail identity is invalid")
    return identity, canonical


def _title_from_cell(cell: Any) -> str:
    for part in cell.get_text("\n", strip=True).splitlines():
        value = _clean(part)
        if value and not value.startswith("(강사"):
            return value
    return ""


def _unpublished_identity(
    catalogue: DaejeonJungguCatalogue,
    *,
    semester: str,
    title: str,
    application_period: str,
    education_period: str,
    schedule: str,
) -> str:
    evidence = "\x1f".join(
        (
            catalogue.key,
            _normalized(semester),
            _normalized(title),
            _clean(application_period),
            _clean(education_period),
            _normalized(schedule),
        )
    )
    return "unpublished-" + hashlib.sha256(evidence.encode("utf-8")).hexdigest()[:20]


def _parse_list_page(
    soup: BeautifulSoup,
    catalogue: DaejeonJungguCatalogue,
    page: int,
    cutoff: date,
) -> tuple[list[dict[str, Any]], int, int, list[str]]:
    total, last, errors = _schema_and_total(soup, catalogue, page)
    label = f"{catalogue.key} page {page}"
    tables = soup.select("div.no-more-tables > table.table-default")
    body_rows = tables[0].select("tbody > tr") if len(tables) == 1 else []
    expected_cells = len(catalogue.expected_headers)
    parsed_rows: list[dict[str, Any]] = []
    for tr in body_rows:
        cells = tr.select(":scope > td")
        links = tr.select('td:last-child a[href*="view.do"][href*="eduNo="]')
        row_text = _clean(tr.get_text(" ", strip=True))
        if not row_text or (
            len(cells) == 1
            and any(token in row_text for token in ("없습니다", "조회된"))
        ):
            continue
        if len(cells) != expected_cells or len(links) > 1:
            errors.append(f"{label}: course row shape changed")
            continue
        offset = 1 if catalogue.key == "lifelong" else 0
        title = _title_from_cell(cells[offset])
        status = _clean(cells[-1].get_text(" ", strip=True))
        if not title:
            errors.append(f"{label}: empty course title")
            continue
        if status not in _STATUS_MAP:
            errors.append(f"{label}: unknown course status")
            continue
        detail_unpublished = not links
        if detail_unpublished and (
            _STATUS_MAP[status] != "SCHEDULED" or tr.select("a[href]")
        ):
            errors.append(f"{label}: unexpected non-course row")
            continue
        source_semester = _clean(cells[0].get_text(" ", strip=True)) if offset else ""
        application_period_text = _clean(
            cells[offset + 1].get_text(" ", strip=True)
        )
        education_period_text = _clean(
            cells[offset + 2].get_text(" ", strip=True)
        )
        schedule = _clean(cells[offset + 4].get_text(" ", strip=True))
        try:
            if detail_unpublished:
                identity = _unpublished_identity(
                    catalogue,
                    semester=source_semester,
                    title=title,
                    application_period=application_period_text,
                    education_period=education_period_text,
                    schedule=schedule,
                )
                raw_url = (
                    f"{daejeon_junggu_list_url(catalogue.key, page)}"
                    f"#mooncen-item-{identity.removeprefix('unpublished-')}"
                )
            else:
                identity, raw_url = _detail_identity(
                    links[0].get("href"), catalogue, page
                )
            apply_start_raw, apply_end_raw, apply_period = _date_pair(
                application_period_text,
                f"{catalogue.key}/{identity}.application period",
            )
            start_raw, end_raw, education_period = _date_pair(
                education_period_text,
                f"{catalogue.key}/{identity}.education period",
            )
            current_or_future = max(start_raw, end_raw) >= cutoff
            if current_or_future and end_raw < start_raw:
                raise ValueError(
                    f"{catalogue.key}/{identity}.education period: current range reversed"
                )
            if current_or_future and apply_end_raw < apply_start_raw:
                raise ValueError(
                    f"{catalogue.key}/{identity}.application period: current range reversed"
                )
            start, end = sorted((start_raw, end_raw))
            apply_start, apply_end = sorted((apply_start_raw, apply_end_raw))
            capacity_text = _clean(cells[offset + 3].get_text(" ", strip=True))
            capacity_match = _CAPACITY_RE.search(capacity_text)
            if capacity_match is None:
                raise ValueError(f"{catalogue.key}/{identity}.capacity: invalid fraction")
            capacity_current = int(capacity_match.group(1).replace(",", ""))
            capacity_total = int(capacity_match.group(2).replace(",", ""))
            wait_match = _WAIT_RE.search(capacity_text)
            wait_current = int(wait_match.group(1).replace(",", "")) if wait_match else 0
            wait_total = int(wait_match.group(2).replace(",", "")) if wait_match else 0
        except ValueError as exc:
            errors.append(_clean(exc))
            continue
        source_omission = "공식 페이지 미기재" if detail_unpublished else ""
        row = {
            "provider": DAEJEON_JUNGGU_PROVIDER,
            "provider_course_id": (
                f"{DAEJEON_JUNGGU_PROVIDER}:{catalogue.key}:{identity}"
            ),
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "description": title,
            "branch": catalogue.branch,
            "preserve_branch": True,
            "provider_organizer": catalogue.branch,
            "category": catalogue.label,
            "program_type": "교육",
            "raw_url": raw_url,
            "application_url": "",
            "application_type": "INFO_ONLY",
            "reservation_available": False,
            "status": _STATUS_MAP[status],
            "fee": source_omission,
            "fee_amount": 0,
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_period": apply_period,
            "apply_start": apply_start.isoformat(),
            "apply_end": apply_end.isoformat(),
            "schedule_raw": schedule,
            "capacity": f"{capacity_current}/{capacity_total}",
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "wait_capacity": f"{wait_current}/{wait_total}",
            "wait_capacity_current": wait_current,
            "wait_capacity_total": wait_total,
            "target": source_omission,
            "venue": source_omission,
            "venue_name": source_omission,
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "municipality_code": DAEJEON_JUNGGU_MUNICIPALITY_CODE,
            "municipality_name": DAEJEON_JUNGGU_MUNICIPALITY_NAME,
            "region": "대전광역시",
            "district": "중구",
            "raw_fields": {
                "identity": identity,
                "catalogue": catalogue.key,
                "source_page": page,
                "source_semester": source_semester,
                "source_status": status,
                "source_application_period": apply_period,
                "source_education_period": education_period,
                "application_control_present": False,
                "application_control_contract": (
                    "official_scheduled_detail_not_published"
                    if detail_unpublished
                    else ""
                ),
                "detail_verified": False,
                "detail_unpublished": detail_unpublished,
                "source_identity_kind": (
                    "stable_list_evidence_hash"
                    if detail_unpublished
                    else "official_eduNo"
                ),
                "target_source_omission": detail_unpublished,
                "fee_source_omission": detail_unpublished,
                "venue_source_omission": detail_unpublished,
                "ownership_source_path": catalogue.list_path,
            },
        }
        parsed_rows.append(row)
    return parsed_rows, total, last, errors


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("title")),
        )
        for row in rows
    )


def _official_route(value: Any) -> str:
    absolute = urljoin(f"https://{DAEJEON_JUNGGU_HOST}/", _clean(value))
    parsed = urlparse(absolute)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or (parsed.hostname or "").rstrip(".").lower()
        not in {DAEJEON_JUNGGU_HOST, "djjunggu.go.kr"}
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 80, 443}
    ):
        return ""
    if not _COURSE_MODULE_RE.fullmatch(parsed.path):
        return ""
    return f"https://{DAEJEON_JUNGGU_HOST}{parsed.path}"


def _validate_sitemap(site_key: str, soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    expected_site = "대전 중구 평생학습관" if site_key == "lll" else "대전광역시 중구청"
    if site_key in {"kr", "lll"} and expected_site not in title:
        errors.append(f"{site_key} sitemap official title changed")
    discovered = {
        route
        for route in (_official_route(node.get("href")) for node in soup.select("a[href]"))
        if route
    }
    expected = DAEJEON_JUNGGU_EXPECTED_SITEMAP_ROUTES[site_key]
    if discovered != expected:
        errors.append(f"{site_key} sitemap course/reservation fanout changed")
    return errors


def _validate_confirmation_alias(soup: BeautifulSoup) -> list[str]:
    script = _clean(" ".join(node.get_text(" ", strip=False) for node in soup.select("script")))
    errors: list[str] = []
    if "본인 확인후에 이용이 가능합니다." not in script:
        errors.append("application-confirmation identity-verification alert changed")
    if not re.search(r"location\.href\s*=\s*[\"']/lll/login\.do[\"']", script):
        errors.append("application-confirmation login redirect changed")
    if soup.select("table.table-default tbody tr, form[name='eduSearchForm']"):
        errors.append("application-confirmation unexpectedly exposed a public catalogue")
    return errors


def _detail_fields(soup: BeautifulSoup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for li in soup.select(".progphoto_wrap .info_box ul.progicon-list > li"):
        label_node = li.select_one(":scope > em")
        if label_node is None:
            continue
        label = _clean(label_node.get_text(" ", strip=True))
        entire = _clean(li.get_text(" ", strip=True))
        value = _clean(entire[len(label) :]) if entire.startswith(label) else ""
        if label in fields:
            raise ValueError(f"detail field {label} duplicated")
        fields[label] = value
    return fields


def _detail_cards(soup: BeautifulSoup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in soup.select(".apply-article > .forward-article .self-accrdt .item"):
        label_node = item.select_one(":scope > strong")
        value_node = item.select_one(":scope > em")
        if label_node is None or value_node is None:
            continue
        label = _clean(label_node.get_text(" ", strip=True))
        value = _clean(value_node.get_text(" ", strip=True))
        if label in fields:
            raise ValueError(f"detail card {label} duplicated")
        fields[label] = value
    return fields


def _fee(value: Any) -> tuple[str, int]:
    raw = _clean(value)
    if not raw:
        return "공식 페이지 미기재", 0
    if raw in {"0", "무료"}:
        return "무료", 0
    if not re.fullmatch(r"[\d,]+\s*원?", raw):
        raise ValueError("detail tuition format changed")
    amount = int("".join(_DIGITS_RE.findall(raw)))
    return f"{amount:,}원", amount


def _application_url(
    href: Any, catalogue: DaejeonJungguCatalogue, identity: str
) -> str:
    absolute = urljoin(catalogue.list_url, _clean(href))
    parsed = urlparse(absolute)
    if not _exact_https_url(absolute, parsed.path, allow_query=True):
        return ""
    if catalogue.key == "lifelong":
        valid_path = (
            parsed.path == "/prog/lecReserve/lec/lll/sub02_01_02/write.do"
        )
    else:
        valid_path = bool(
            re.fullmatch(
                r"/prog/infoReserve/infoedu(?:/kr)?/sub04_01_02_02/write\.do",
                parsed.path,
            )
        )
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if not valid_path or len({name for name, _value in pairs}) != len(pairs):
        return ""
    values = dict(pairs)
    if not {"eduNo", "resvChk"}.issubset(values) or not set(values) <= {
        "pageIndex",
        "eduNo",
        "oneInwon",
        "resvChk",
    }:
        return ""
    if (
        _clean(values.get("eduNo")) != identity
        or _clean(values.get("resvChk")) != "N"
        or ("pageIndex" in values and not _DIGITS_RE.fullmatch(_clean(values["pageIndex"])))
        or ("oneInwon" in values and _clean(values["oneInwon"]))
    ):
        return ""
    return absolute


def _validate_detail(
    row: dict[str, Any],
    soup: BeautifulSoup,
    catalogue: DaejeonJungguCatalogue,
) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    label = f"{catalogue.key}/{identity}"
    errors: list[str] = []
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if catalogue.expected_page_title not in page_title:
        errors.append(f"{label}: detail official title changed")
    headings = soup.select(".progphoto_wrap .info_box > strong")
    detail_title = _clean(headings[0].get_text(" ", strip=True)) if len(headings) == 1 else ""
    if _normalized(detail_title) != _normalized(row.get("title")):
        errors.append(f"{label}: detail/list title mismatch")
    try:
        fields = _detail_fields(soup)
        cards = _detail_cards(soup)
    except ValueError as exc:
        return [*errors, f"{label}: {_clean(exc)}"]
    for required in ("교육기간", "접수기간", "수업료"):
        if required not in fields:
            errors.append(f"{label}: detail field {required} missing")
    for required in ("교육정원", "교육대상", "교육장소"):
        if required not in cards:
            errors.append(f"{label}: detail card {required} missing")
    if errors:
        return errors
    try:
        start, end, _period = _date_pair(fields["교육기간"], f"{label}.detail education")
        apply_start, apply_end, _apply_period = _date_pair(
            fields["접수기간"], f"{label}.detail application"
        )
        capacity_match = _DIGITS_RE.search(cards["교육정원"].replace(",", ""))
        if capacity_match is None:
            raise ValueError("detail capacity format changed")
        detail_capacity = int(capacity_match.group())
        fee, fee_amount = _fee(fields["수업료"])
    except ValueError as exc:
        return [*errors, f"{label}: {_clean(exc)}"]
    if (start.isoformat(), end.isoformat()) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ):
        errors.append(f"{label}: detail/list education period mismatch")
    if (apply_start.isoformat(), apply_end.isoformat()) != (
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
    ):
        errors.append(f"{label}: detail/list application period mismatch")
    if detail_capacity != int(row.get("capacity_total", -1)):
        errors.append(f"{label}: detail/list capacity mismatch")

    controls = [
        node
        for node in soup.select(".progphoto_wrap .btn_wrap a[href]")
        if _clean(node.get_text(" ", strip=True)) in {"신청하기", "대기신청"}
    ]
    normalized_status = _clean(row.get("status"))
    expected_control_text = {
        "OPEN": "신청하기",
        "WAITING": "대기신청",
    }.get(normalized_status, "")
    if expected_control_text:
        if (
            len(controls) != 1
            or _clean(controls[0].get_text(" ", strip=True))
            != expected_control_text
        ):
            errors.append(
                f"{label}: {normalized_status.lower()} course application control changed"
            )
        else:
            application_url = _application_url(
                controls[0].get("href"), catalogue, identity
            )
            if not application_url:
                errors.append(f"{label}: application control is not course-bound")
            else:
                row["application_url"] = application_url
                row["application_type"] = (
                    "ONLINE_WAITLIST_LOGIN_REQUIRED"
                    if normalized_status == "WAITING"
                    else "ONLINE_RESERVATION_LOGIN_REQUIRED"
                )
                row["reservation_available"] = True
                row["raw_fields"]["application_control_present"] = True
                row["raw_fields"]["application_control_contract"] = (
                    "official_course_bound_waitlist_control"
                    if normalized_status == "WAITING"
                    else "official_course_bound_write_control"
                )
    elif controls:
        errors.append(f"{label}: non-open course unexpectedly exposes application control")
    else:
        row["raw_fields"]["application_control_contract"] = (
            "no_public_control_for_non_open_status"
        )
    target_value = _clean(cards["교육대상"])
    venue_value = _clean(cards["교육장소"])
    row["fee"] = fee
    row["fee_amount"] = fee_amount
    row["target"] = target_value or "공식 페이지 미기재"
    row["venue"] = venue_value or catalogue.branch
    row["venue_name"] = venue_value or catalogue.branch
    row["raw_fields"]["target_source_omission"] = not target_value
    row["raw_fields"]["fee_source_omission"] = fee == "공식 페이지 미기재"
    row["raw_fields"]["venue_source_omission"] = not venue_value
    if not errors:
        row["raw_fields"]["detail_verified"] = True
    return errors


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    forbidden = set(row) & _FORBIDDEN_PERSISTED_KEYS
    if forbidden:
        errors.append("forbidden PII/detail keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    serializable = {
        key: value
        for key, value in row.items()
        if key not in {"raw_url", "application_url"}
    }
    payload = repr(serializable)
    if _EMAIL_RE.search(payload) or _PHONE_RE.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("arbitrary detail description persisted")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def collect_daejeon_junggu_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 200,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = DAEJEON_JUNGGU_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future Jung-gu education snapshot."""

    cutoff = _today(today)
    current_factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    errors: list[str] = []
    meta: dict[str, Any] = {
        "pages": 0,
        "list_requests": 0,
        "sitemap_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_cap_reached": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "configured_collection_error": "",
    }
    if not is_daejeon_junggu_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Jung-gu owner"
        return [], DAEJEON_JUNGGU_PARSER, meta
    if (
        isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages < 7
        or isinstance(detail_limit, bool)
        or not isinstance(detail_limit, int)
        or detail_limit < 0
        or isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "invalid max_pages/detail_limit/max_workers cap"
        return [], DAEJEON_JUNGGU_PARSER, meta

    bootstrap_items: list[tuple[Any, str]] = [
        (("sitemap", site_key), url)
        for site_key, url in DAEJEON_JUNGGU_SITEMAPS.items()
    ]
    bootstrap_items.extend(
        (("list", catalogue.key, 1, "data"), catalogue.list_url)
        for catalogue in DAEJEON_JUNGGU_CATALOGUES
    )
    bootstrap_items.append(
        (("confirmation", "login_alias"), DAEJEON_JUNGGU_CONFIRMATION_URL)
    )
    fetched, fetch_errors = _fetch_many(
        bootstrap_items,
        fetcher=current_fetcher,
        session_factory=current_factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(fetched)
    meta["list_requests"] += len(fetched)
    meta["sitemap_requests"] = sum(
        ("sitemap", key) in fetched for key in DAEJEON_JUNGGU_SITEMAPS
    )

    for site_key in DAEJEON_JUNGGU_SITEMAPS:
        soup = fetched.get(("sitemap", site_key))
        if soup is None:
            errors.append(f"{site_key} sitemap missing")
        else:
            errors.extend(_validate_sitemap(site_key, soup))
    confirmation = fetched.get(("confirmation", "login_alias"))
    if confirmation is None:
        errors.append("application-confirmation alias response missing")
    else:
        errors.extend(_validate_confirmation_alias(confirmation))

    first_rows: dict[str, list[dict[str, Any]]] = {}
    totals: dict[str, int] = {}
    lasts: dict[str, int] = {}
    for catalogue in DAEJEON_JUNGGU_CATALOGUES:
        soup = fetched.get(("list", catalogue.key, 1, "data"))
        if soup is None:
            errors.append(f"{catalogue.key} first page missing")
            continue
        rows, total, last, page_errors = _parse_list_page(
            soup, catalogue, 1, cutoff
        )
        errors.extend(page_errors)
        first_rows[catalogue.key] = rows
        totals[catalogue.key] = total
        lasts[catalogue.key] = last

    if len(totals) != len(DAEJEON_JUNGGU_CATALOGUES):
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], DAEJEON_JUNGGU_PARSER, meta
    required_list_requests = len(bootstrap_items) + sum(
        lasts[item.key] + 1 for item in DAEJEON_JUNGGU_CATALOGUES
    )
    meta["required_list_requests"] = required_list_requests
    if required_list_requests > max_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {max_pages} of {required_list_requests} required source requests"
        )
    if errors:
        meta.update(
            {
                "source_totals": totals,
                "declared_pages": lasts,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return [], DAEJEON_JUNGGU_PARSER, meta

    remaining_items: list[tuple[Any, str]] = []
    for catalogue in DAEJEON_JUNGGU_CATALOGUES:
        remaining_items.extend(
            (
                ("list", catalogue.key, page, "data"),
                daejeon_junggu_list_url(catalogue.key, page),
            )
            for page in range(2, lasts[catalogue.key] + 1)
        )
        remaining_items.extend(
            [
                (
                    ("list", catalogue.key, lasts[catalogue.key] + 1, "sentinel"),
                    daejeon_junggu_list_url(
                        catalogue.key, lasts[catalogue.key] + 1
                    ),
                ),
                (
                    ("list", catalogue.key, 1, "recheck"),
                    daejeon_junggu_list_url(catalogue.key, 1),
                ),
            ]
        )
    remaining, remaining_errors = _fetch_many(
        remaining_items,
        fetcher=current_fetcher,
        session_factory=current_factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    fetched.update(remaining)
    errors.extend(remaining_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)
    meta["sentinel_requests"] = sum(
        ("list", item.key, lasts[item.key] + 1, "sentinel") in fetched
        for item in DAEJEON_JUNGGU_CATALOGUES
    )
    meta["stability_rechecks"] = sum(
        ("list", item.key, 1, "recheck") in fetched
        for item in DAEJEON_JUNGGU_CATALOGUES
    )

    all_rows: list[dict[str, Any]] = []
    page_counts: dict[str, dict[int, int]] = {}
    signatures: dict[str, dict[int, tuple[tuple[str, str], ...]]] = {}
    for catalogue in DAEJEON_JUNGGU_CATALOGUES:
        total = totals[catalogue.key]
        last = lasts[catalogue.key]
        catalogue_rows: list[dict[str, Any]] = []
        page_counts[catalogue.key] = {}
        signatures[catalogue.key] = {}
        for page in range(1, last + 1):
            if page == 1:
                rows = first_rows[catalogue.key]
                page_errors: list[str] = []
                declared_total, declared_last = total, last
            else:
                soup = fetched.get(("list", catalogue.key, page, "data"))
                if soup is None:
                    errors.append(f"{catalogue.key} page {page}: missing response")
                    continue
                rows, declared_total, declared_last, page_errors = _parse_list_page(
                    soup, catalogue, page, cutoff
                )
            errors.extend(page_errors)
            if (declared_total, declared_last) != (total, last):
                errors.append(f"{catalogue.key} page {page}: total/last changed")
            expected_count = (
                0
                if total == 0
                else DAEJEON_JUNGGU_PAGE_SIZE
                if page < last
                else total - DAEJEON_JUNGGU_PAGE_SIZE * (last - 1)
            )
            if len(rows) != expected_count:
                errors.append(f"{catalogue.key} page {page}: row count mismatch")
            page_counts[catalogue.key][page] = len(rows)
            signatures[catalogue.key][page] = _page_signature(rows)
            catalogue_rows.extend(rows)
        if len(catalogue_rows) != total:
            errors.append(f"{catalogue.key}: advertised total does not match parsed rows")
        nonempty = [signature for signature in signatures[catalogue.key].values() if signature]
        if len(nonempty) != len(set(nonempty)):
            errors.append(f"{catalogue.key}: duplicate non-empty page signature")

        sentinel_soup = fetched.get(
            ("list", catalogue.key, last + 1, "sentinel")
        )
        recheck_soup = fetched.get(("list", catalogue.key, 1, "recheck"))
        if sentinel_soup is None or recheck_soup is None:
            errors.append(f"{catalogue.key}: sentinel or page-one recheck missing")
        else:
            sentinel_rows, sentinel_total, sentinel_last, sentinel_errors = (
                _parse_list_page(sentinel_soup, catalogue, last + 1, cutoff)
            )
            recheck_rows, recheck_total, recheck_last, recheck_errors = (
                _parse_list_page(recheck_soup, catalogue, 1, cutoff)
            )
            errors.extend(sentinel_errors)
            errors.extend(recheck_errors)
            if (
                (sentinel_total, sentinel_last) != (total, last)
                or sentinel_rows
            ):
                errors.append(f"{catalogue.key}: immediate post-last page is not empty")
            if (
                (recheck_total, recheck_last) != (total, last)
                or _page_signature(recheck_rows)
                != signatures[catalogue.key].get(1, ())
            ):
                errors.append(f"{catalogue.key}: page-one recheck changed")
        all_rows.extend(catalogue_rows)

    identities = [
        (
            _clean(row.get("raw_fields", {}).get("catalogue")),
            _clean(row.get("raw_fields", {}).get("identity")),
        )
        for row in all_rows
    ]
    identity_duplicate_count = len(identities) - len(set(identities))
    if identity_duplicate_count:
        errors.append(f"{identity_duplicate_count} duplicate catalogue identities")
    semantic = Counter(
        (
            _normalized(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
        )
        for row in all_rows
    )
    semantic_duplicate_groups = sum(count > 1 for count in semantic.values())
    semantic_duplicate_excess = sum(max(0, count - 1) for count in semantic.values())
    current_rows = [
        row
        for row in all_rows
        if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
    ]
    expired_count = len(all_rows) - len(current_rows)

    list_complete = bool(not errors and len(all_rows) == sum(totals.values()))
    detail_rows = [
        row
        for row in current_rows
        if not row.get("raw_fields", {}).get("detail_unpublished")
    ]
    required_details = len(detail_rows)
    if required_details > detail_limit:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {detail_limit} of {required_details} required details"
        )
    detail_errors: list[str] = []
    if list_complete and not errors:
        detail_items = [
            (
                (
                    "detail",
                    _clean(row["raw_fields"]["catalogue"]),
                    _clean(row["raw_fields"]["identity"]),
                ),
                _clean(row["raw_url"]),
            )
            for row in detail_rows
        ]
        meta["detail_attempts"] = len(detail_items)
        details, detail_fetch_errors = _fetch_many(
            detail_items,
            fetcher=current_fetcher,
            session_factory=current_factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        meta["pages"] += len(details)
        rows_by_key = {
            (
                "detail",
                _clean(row["raw_fields"]["catalogue"]),
                _clean(row["raw_fields"]["identity"]),
            ): row
            for row in current_rows
        }
        for key, soup in details.items():
            row = rows_by_key[key]
            catalogue = DAEJEON_JUNGGU_CATALOGUE_BY_KEY[key[1]]
            item_errors = _validate_detail(row, soup, catalogue)
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                meta["detail_pages"] += 1
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        list_complete
        and meta["detail_attempts"] == required_details
        and meta["detail_pages"] == required_details
        and not detail_errors
        and all(
            row.get("raw_fields", {}).get("detail_verified")
            or row.get("raw_fields", {}).get("detail_unpublished")
            for row in current_rows
        )
    )

    result: list[dict[str, Any]] = []
    if list_complete and details_complete and not errors:
        for row in current_rows:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper(current_rows))
            if len(result) != len(current_rows):
                errors.append(
                    f"dedupe changed official identity cardinality {len(current_rows)} to {len(result)}"
                )
                result = []
    snapshot_complete = bool(list_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    source_counts = Counter(
        _clean(row.get("raw_fields", {}).get("catalogue")) for row in all_rows
    )
    current_counts = Counter(
        _clean(row.get("raw_fields", {}).get("catalogue")) for row in current_rows
    )
    status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in all_rows
    )
    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    meta.update(
        {
            "ownership_scope": DAEJEON_JUNGGU_OWNERSHIP_SCOPE,
            "ownership_fanout_urls": [
                item.list_url for item in DAEJEON_JUNGGU_CATALOGUES
            ],
            "confirmation_alias_url": DAEJEON_JUNGGU_CONFIRMATION_URL,
            "confirmation_alias_verified": confirmation is not None
            and not _validate_confirmation_alias(confirmation),
            "official_sitemaps": dict(DAEJEON_JUNGGU_SITEMAPS),
            "sitemaps_complete": meta["sitemap_requests"]
            == len(DAEJEON_JUNGGU_SITEMAPS)
            and not any("sitemap" in item for item in errors),
            "source_totals": totals,
            "source_rows": len(all_rows),
            "source_counts": dict(source_counts),
            "declared_pages": lasts,
            "page_counts": page_counts,
            "current_counts": dict(current_counts),
            "current_count": len(current_rows),
            "scheduled_detail_unpublished_count": sum(
                bool(row.get("raw_fields", {}).get("detail_unpublished"))
                for row in current_rows
            ),
            "expired_count": expired_count,
            "returned_count": len(result),
            "source_status_counts": dict(status_counts),
            "status_counts": dict(
                Counter(_clean(row.get("status")) for row in result)
            ),
            "application_control_count": sum(
                bool(row.get("reservation_available")) for row in result
            ),
            "identity_duplicate_count": identity_duplicate_count,
            "semantic_duplicate_group_count": semantic_duplicate_groups,
            "semantic_duplicate_excess_rows": semantic_duplicate_excess,
            "semantic_duplicate_policy": (
                "preserve_distinct_official_catalogue_identities"
            ),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "details_complete": details_complete,
            "pagination_complete": list_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "both complete Jung-gu education catalogues have ended"
                if snapshot_complete and not current_rows
                else ""
            ),
            "municipality_coverage": [DAEJEON_JUNGGU_MUNICIPALITY_CODE],
            "ok_overlap_audit": dict(DAEJEON_JUNGGU_OK_OVERLAP_AUDIT),
            "ok_catalogue_is_alias": False,
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, DAEJEON_JUNGGU_PARSER, meta


collect = collect_daejeon_junggu_education


__all__ = [
    "DAEJEON_JUNGGU_CANONICAL_CANDIDATE_ID",
    "DAEJEON_JUNGGU_CANONICAL_URL",
    "DAEJEON_JUNGGU_CATALOGUES",
    "DAEJEON_JUNGGU_CONFIRMATION_URL",
    "DAEJEON_JUNGGU_DETAIL_ALIAS_URLS",
    "DAEJEON_JUNGGU_FANOUT_URLS",
    "DAEJEON_JUNGGU_INFORMATION_URL",
    "DAEJEON_JUNGGU_MUNICIPALITY_CODE",
    "DAEJEON_JUNGGU_MUNICIPALITY_NAME",
    "DAEJEON_JUNGGU_OK_OVERLAP_AUDIT",
    "DAEJEON_JUNGGU_PAGE_SIZE",
    "DAEJEON_JUNGGU_PARSER",
    "DAEJEON_JUNGGU_PROVIDER",
    "DAEJEON_JUNGGU_SITEMAPS",
    "DaejeonJungguCatalogue",
    "collect",
    "collect_daejeon_junggu_education",
    "daejeon_junggu_detail_url",
    "daejeon_junggu_list_url",
    "is_daejeon_junggu_education_target",
    "is_daejeon_junggu_owned_alias_target",
    "is_target",
]
