"""Fail-closed collector for Yeongju Lifelong Learning Center courses.

The search candidate ``/lecture/`` is an information page, not a course
ledger.  The executable owner is the centre's real citizen-course list,
``lecture_list.php``.  Five sibling lists (one-day classes, middle-aged
school, special lectures, happy-learning centres, and activity-leader
training) are disjoint views of the same owner and are collected together.

The legacy PHP pages intentionally publish only courses that are currently
advertised for application.  Each catalogue is therefore treated as a
complete current ledger: all declared pages are fetched, an impossible class
filter must return the official empty-row sentinel, and first/last/sentinel
boundaries are fetched again before the snapshot is accepted.  Identities
repeated by sibling views are merged only when their public facts agree.

Only current/future courses are opened.  Detail extraction is an allowlist.
Instructor names, education prose, remarks, attachments, privacy overlays,
member forms, applicant data and contact fields are never read or persisted.
The site's application button is an identityless login gate, so it is audited
but never emitted as an application URL and is never requested.
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
from bs4 import BeautifulSoup, Tag


YEONGJU_MUNICIPALITY_CODE = "4721000000"
YEONGJU_MUNICIPALITY_NAME = "경상북도 영주시"
YEONGJU_HOST = "www.yeongjulll.go.kr"
YEONGJU_BRANCH = "영주시 평생학습센터"

# Canonical executable owner.  The suffix is SHA1(canonical URL)[:8].
YEONGJU_CANONICAL_URL = (
    "https://www.yeongjulll.go.kr/lecture/lecture_list.php"
)
YEONGJU_PROVIDER = "MUNI_WWW_YEONGJULLL_GO_KR_68BB3D3C"

# Search/deprecated candidates which are aliases or included scopes.
YEONGJU_GUIDE_URL = "https://www.yeongjulll.go.kr/lecture/"
YEONGJU_GUIDE_PROVIDER = "MUNI_WWW_YEONGJULLL_GO_KR_780627CE"
YEONGJU_GUIDE_DEPRECATED_PROVIDER = "MUNI_WWW_YEONGJULLL_GO_KR_4A3830E4"
YEONGJU_GUIDE_CANDIDATE_ID = "MUNI_IR_951AE9950D5F"
YEONGJU_ONEDAY_URL = (
    "https://www.yeongjulll.go.kr/lecture/lecture_list_oneday.php"
)
YEONGJU_ONEDAY_PROVIDER = "MUNI_WWW_YEONGJULLL_GO_KR_5EF37EB8"
YEONGJU_ONEDAY_CANDIDATE_ID = "MUNI_IR_2F9BC904000D"
YEONGJU_ROOT_URL = "https://www.yeongjulll.go.kr/"
YEONGJU_ROOT_PROVIDER = "MUNI_WWW_YEONGJULLL_GO_KR_00A58ED8"
YEONGJU_ROOT_CANDIDATE_ID = "MUNI_IR_764110A02AD3"

YEONGJU_DETAIL_PATH = "/lecture/lecture_detail.php"
YEONGJU_LOGIN_PATH = "/main.jsp"
YEONGJU_EMPTY_SENTINEL_CLASS = "999999"
YEONGJU_MAX_HTML_BYTES = 4_000_000
YEONGJU_FETCH_ATTEMPTS = 3

YEONGJU_PARSER = (
    "yeongju_lifelong_six_complete_catalogues+declared_all_pages+"
    "structural_empty_sentinels+stable_first_last_sentinel+"
    "cross_catalogue_identity_dedupe+current_safe_details+"
    "source_application_status+identityless_login_gate_excluded+"
    "fixed_facility_branch+pii_allowlist"
)


@dataclass(frozen=True)
class YeongjuCatalogue:
    code: str
    label: str
    url: str

    @property
    def path(self) -> str:
        return urlparse(self.url).path


YEONGJU_CATALOGUES: tuple[YeongjuCatalogue, ...] = (
    YeongjuCatalogue("regular", "시민교육", YEONGJU_CANONICAL_URL),
    YeongjuCatalogue("oneday", "원데이클래스", YEONGJU_ONEDAY_URL),
    YeongjuCatalogue(
        "chungchun",
        "신중년청춘학교",
        "https://www.yeongjulll.go.kr/lecture/lecture_list_chungchun.php",
    ),
    YeongjuCatalogue(
        "special",
        "평생학습특강",
        "https://www.yeongjulll.go.kr/lecture/lecture_list_specialprogram.php",
    ),
    YeongjuCatalogue(
        "happy",
        "행복학습센터",
        "https://www.yeongjulll.go.kr/lecture/lecture_list_happy.php",
    ),
    YeongjuCatalogue(
        "activist",
        "평생학습활동가 양성과정",
        "https://www.yeongjulll.go.kr/lecture/lecture_list_activist.php",
    ),
)

YEONGJU_INCLUDED_SCOPE_URLS = tuple(source.url for source in YEONGJU_CATALOGUES[1:])

YEONGJU_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    YEONGJU_PROVIDER: {
        "decision": "canonical_six_catalogue_education_owner",
        "url": YEONGJU_CANONICAL_URL,
        "branch": YEONGJU_BRANCH,
        "included_scopes": YEONGJU_INCLUDED_SCOPE_URLS,
    },
    YEONGJU_GUIDE_PROVIDER: {
        "decision": "exclude_information_page_alias_not_a_course_ledger",
        "candidate_id": YEONGJU_GUIDE_CANDIDATE_ID,
        "url": YEONGJU_GUIDE_URL,
        "canonical_owner": YEONGJU_PROVIDER,
    },
    YEONGJU_GUIDE_DEPRECATED_PROVIDER: {
        "decision": "disable_deprecated_raw_trailing_slash_alias",
        "url": YEONGJU_GUIDE_URL,
        "canonical_owner": YEONGJU_PROVIDER,
    },
    YEONGJU_ONEDAY_PROVIDER: {
        "decision": "disable_separate_schedule_included_under_canonical_owner",
        "candidate_id": YEONGJU_ONEDAY_CANDIDATE_ID,
        "url": YEONGJU_ONEDAY_URL,
        "canonical_owner": YEONGJU_PROVIDER,
    },
    YEONGJU_ROOT_PROVIDER: {
        "decision": "exclude_navigation_and_five-item_highlight_alias",
        "candidate_id": YEONGJU_ROOT_CANDIDATE_ID,
        "url": YEONGJU_ROOT_URL,
        "canonical_owner": YEONGJU_PROVIDER,
    },
}

# Reproducible live observations; the opt-in live test refreshes the contract.
YEONGJU_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "canonical_url": YEONGJU_CANONICAL_URL,
    "catalogue_source_rows": {
        "regular": 0,
        "oneday": 0,
        "chungchun": 0,
        "special": 1,
        "happy": 0,
        "activist": 0,
    },
    "catalogue_current_rows": {
        "regular": 0,
        "oneday": 0,
        "chungchun": 0,
        "special": 1,
        "happy": 0,
        "activist": 0,
    },
    "source_rows": 1,
    "unique_identities": 1,
    "current_or_future_rows": 1,
    "current_identities": ("1290",),
    "source_status_counts": {"접수중": 1},
    "current_status_counts": {"OPEN": 1},
    "current_branch_counts": {YEONGJU_BRANCH: 1},
    "current_detail_pages": 1,
    "identity_bound_application_controls": 0,
    "identityless_login_gates_excluded": 1,
    "guide_page_is_catalogue": False,
}

YEONGJU_PII_FIELDS_NEVER_READ = (
    "강사명 value",
    "교육내용 value",
    "기타사항 value",
    "문의/연락처 value",
    "첨부파일 names and bodies",
    "privacy overlay text",
    "member/login form payload",
    "applicant or payment payload",
)


class YeongjuContractError(ValueError):
    """Raised when the audited Yeongju source contract changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport or block response."""


HtmlFetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


@dataclass(frozen=True)
class _ListPage:
    source: YeongjuCatalogue
    page: int
    rows: tuple[dict[str, Any], ...]
    empty_marker: bool
    declared_pages: int


_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_DETAIL_HREF_RE = re.compile(r"(?:^|/)lecture_detail\.php\?(?:[^#]*&)?seq=(\d+)(?:&|$)")
_DATE_TOKEN_RE = re.compile(
    r"(?<!\d)(?:(?P<year>20\d{2})\s*[.\-/]\s*)?"
    r"(?P<month>\d{1,2})\s*[.\-/]\s*(?P<day>\d{1,2})(?!\d)"
)
_APPLY_RANGE_RE = re.compile(
    r"(?:(?P<start_year>20\d{2})\s*[.\-/]\s*)?"
    r"(?P<start_month>\d{1,2})\s*[.\-/]\s*(?P<start_day>\d{1,2})\s*"
    r"(?P<start_hour>\d{1,2})(?:"
    r"\s*:\s*(?P<start_minute>\d{1,2})|"
    r"\s*시(?:\s*(?P<start_minute_ko>\d{1,2})\s*분)?"
    r")?\s*"
    r"[~∼-]\s*"
    r"(?:(?P<end_year>20\d{2})\s*[.\-/]\s*)?"
    r"(?P<end_month>\d{1,2})\s*[.\-/]\s*(?P<end_day>\d{1,2})\s*"
    r"(?P<end_hour>\d{1,2})(?:"
    r"\s*:\s*(?P<end_minute>\d{1,2})|"
    r"\s*시(?:\s*(?P<end_minute_ko>\d{1,2})\s*분)?"
    r")?"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[)\-\s]\s*\d{3,4}[\-\s]\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)

_EMPTY_TEXT = "접수가능한 강좌 또는검색결과가 없습니다."
_TITLE = "영주시 평생학습센터"
_STATUS_MAP = {
    "접수중": "OPEN",
    "접수가능": "OPEN",
    "신청중": "OPEN",
    "접수예정": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "신청예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "접수종료": "CLOSED",
    "신청마감": "CLOSED",
    "마감": "CLOSED",
}

_DETAIL_REQUIRED_FIELDS = frozenset(
    ("모집년도", "모집인원", "신청인원", "접수기간", "교육기간", "교육장소")
)
_DETAIL_SAFE_OPTIONAL_FIELDS = frozenset(("결제금액", "재료비"))
_DETAIL_IGNORED_FIELDS = frozenset(
    (
        "강사명",
        "교육내용",
        "기타사항",
        "문의",
        "문의전화",
        "연락처",
        "첨부파일",
    )
)

_ALLOWED_ROW_KEYS = frozenset(
    (
        "provider",
        "provider_course_id",
        "prefer_incoming_provider_course_id",
        "title",
        "description",
        "branch",
        "branch_code",
        "preserve_branch",
        "category",
        "program_type",
        "raw_url",
        "reservation_available",
        "status",
        "fee",
        "materials_fee",
        "period",
        "start_date",
        "end_date",
        "apply_period",
        "apply_start_date",
        "apply_end_date",
        "schedule_raw",
        "capacity_current",
        "capacity_total",
        "venue_name",
        "collection_category",
        "domain_category",
        "operator_type",
        "source_group",
        "service_group",
        "collection_type",
        "municipality_code",
        "municipality_name",
        "raw_fields",
    )
)
_ALLOWED_RAW_KEYS = frozenset(
    (
        "parser",
        "source_identity",
        "source_catalogues",
        "source_pages",
        "source_status",
        "detail_verified",
        "application_control_contract",
        "application_control_verified",
    )
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _new_session() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "html.parser")
    if isinstance(value, str):
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if content is None:
        raise YeongjuContractError("fetcher did not return HTML")
    if len(content) > YEONGJU_MAX_HTML_BYTES:
        raise YeongjuContractError("HTML response exceeds size limit")
    headers = getattr(value, "headers", {}) or {}
    content_type = _clean(headers.get("Content-Type"))
    if content_type and not any(token in content_type.lower() for token in ("text/html", "application/xhtml")):
        raise YeongjuContractError("response is not HTML")
    return BeautifulSoup(content, "html.parser")


def _validate_fetched_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != YEONGJU_HOST:
        raise YeongjuContractError("request redirected outside the audited HTTPS host")
    allowed = {source.path for source in YEONGJU_CATALOGUES} | {YEONGJU_DETAIL_PATH}
    if parsed.path not in allowed:
        raise YeongjuContractError("request redirected outside the audited course paths")


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    response = current.get(url, timeout=timeout, allow_redirects=True)
    if response.status_code in {403, 408, 425, 429, 500, 502, 503, 504}:
        raise _TransientFetchError(f"HTTP {response.status_code}")
    response.raise_for_status()
    _validate_fetched_url(_clean(getattr(response, "url", url)) or url)
    if not getattr(response, "content", b""):
        raise YeongjuContractError("empty HTTP response")
    return response


def _fetch_soup(
    current: Any,
    url: str,
    timeout: int,
    fetcher: HtmlFetcher,
    sleeper: Sleeper,
) -> BeautifulSoup:
    last: Optional[BaseException] = None
    for attempt in range(YEONGJU_FETCH_ATTEMPTS):
        try:
            soup = _coerce_soup(fetcher(current, url, timeout))
            title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
            if title != _TITLE:
                raise YeongjuContractError(f"unexpected document owner/title: {title!r}")
            return soup
        except (requests.RequestException, _TransientFetchError) as exc:
            last = exc
            if attempt + 1 < YEONGJU_FETCH_ATTEMPTS:
                sleeper(0.15 * (2**attempt))
    if last is not None:
        raise last
    raise YeongjuContractError("unreachable fetch state")


def yeongju_catalogue_url(source: YeongjuCatalogue | str, page: int = 1) -> str:
    if isinstance(source, str):
        matched = next((item for item in YEONGJU_CATALOGUES if item.code == source), None)
        if matched is None:
            raise YeongjuContractError(f"unknown catalogue {source!r}")
        source = matched
    page_number = int(page)
    if page_number < 1:
        raise YeongjuContractError("page must be positive")
    if page_number == 1:
        return source.url
    return f"{source.url}?{urlencode({'page': page_number})}"


def yeongju_sentinel_url(source: YeongjuCatalogue | str) -> str:
    if isinstance(source, str):
        source = next((item for item in YEONGJU_CATALOGUES if item.code == source), None)
        if source is None:
            raise YeongjuContractError("unknown catalogue")
    return f"{source.url}?{urlencode({'sclass': YEONGJU_EMPTY_SENTINEL_CLASS})}"


def yeongju_detail_url(identity: str) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise YeongjuContractError("invalid course identity")
    return f"https://{YEONGJU_HOST}{YEONGJU_DETAIL_PATH}?{urlencode({'seq': value})}"


def _detail_identity(href: str, base_url: str) -> str:
    absolute = urljoin(base_url, _clean(href))
    parsed = urlparse(absolute)
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != YEONGJU_HOST:
        return ""
    if parsed.path != YEONGJU_DETAIL_PATH or parsed.fragment:
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    identities = query.get("seq", [])
    if len(identities) != 1 or not _IDENTITY_RE.fullmatch(identities[0]):
        return ""
    return identities[0]


def _date_tokens(value: str, fallback_year: int) -> list[date]:
    result: list[date] = []
    for match in _DATE_TOKEN_RE.finditer(_clean(value)):
        year = int(match.group("year") or fallback_year)
        try:
            parsed = date(year, int(match.group("month")), int(match.group("day")))
        except ValueError as exc:
            raise YeongjuContractError(f"invalid date in {value!r}") from exc
        if not result or parsed != result[-1]:
            result.append(parsed)
    return result


def _event_range(value: str, fallback_year: int) -> tuple[date, date]:
    dates = _date_tokens(value, fallback_year)
    if not dates:
        raise YeongjuContractError(f"education period has no date: {value!r}")
    start, end = dates[0], dates[1] if len(dates) > 1 else dates[0]
    if end < start:
        # A year-spanning legacy range may omit the end year.
        try:
            end = end.replace(year=end.year + 1)
        except ValueError as exc:
            raise YeongjuContractError("invalid year-spanning education period") from exc
    return start, end


def _apply_range(value: str, fallback_year: int) -> tuple[datetime, datetime]:
    match = _APPLY_RANGE_RE.search(_clean(value))
    if match is None:
        raise YeongjuContractError(f"application period has no timed range: {value!r}")
    start_year = int(match.group("start_year") or fallback_year)
    end_year = int(match.group("end_year") or start_year)
    try:
        start = datetime(
            start_year,
            int(match.group("start_month")),
            int(match.group("start_day")),
            int(match.group("start_hour")),
            int(match.group("start_minute") or match.group("start_minute_ko") or 0),
        )
        end = datetime(
            end_year,
            int(match.group("end_month")),
            int(match.group("end_day")),
            int(match.group("end_hour")),
            int(match.group("end_minute") or match.group("end_minute_ko") or 0),
        )
    except ValueError as exc:
        raise YeongjuContractError("invalid application period") from exc
    if end < start and not match.group("end_year"):
        end = end.replace(year=end.year + 1)
    if end < start:
        raise YeongjuContractError("application period ends before it starts")
    return start, end


def _first_int(value: str) -> Optional[int]:
    match = re.search(r"\d[\d,]*", _clean(value))
    return int(match.group(0).replace(",", "")) if match else None


def _declared_pages(soup: BeautifulSoup, source: YeongjuCatalogue) -> int:
    pages = {1}
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(source.url, _clean(anchor.get("href")))
        parsed = urlparse(absolute)
        if (parsed.hostname or "").lower() != YEONGJU_HOST or parsed.path != source.path:
            continue
        query = parse_qs(parsed.query, keep_blank_values=True)
        values = query.get("page", [])
        if len(values) == 1 and values[0].isdigit() and int(values[0]) > 0:
            pages.add(int(values[0]))
    return max(pages)


def _parse_list_page(
    soup: BeautifulSoup,
    source: YeongjuCatalogue,
    page: int,
) -> _ListPage:
    table = soup.find("table", class_="table_black")
    if table is None:
        raise YeongjuContractError(f"{source.code} page {page}: course table missing")
    caption = table.find("caption")
    if _clean(caption.get_text(" ", strip=True) if caption else "") != "수강과목 목록":
        raise YeongjuContractError(f"{source.code} page {page}: table caption changed")
    headers = [_clean(node.get_text(" ", strip=True)) for node in table.find_all("th")]
    if (
        len(headers) != 8
        or headers[:3] != ["번호", "모집년도", "과목명/접수기간"]
        or headers[-1] != "접수"
        or "신청인원" not in headers
        or not any("교육기간" in value for value in headers)
    ):
        raise YeongjuContractError(f"{source.code} page {page}: table headers changed")

    body = table.find("tbody")
    if body is None:
        raise YeongjuContractError(f"{source.code} page {page}: table body missing")
    parsed_rows: list[dict[str, Any]] = []
    empty_marker = False
    exposed = 0
    for tr in body.find_all("tr", recursive=False):
        cells = tr.find_all("td")
        if not cells:
            continue
        exposed += 1
        if len(cells) == 1 and _clean(cells[0].get_text(" ", strip=True)) == _EMPTY_TEXT:
            if parsed_rows or empty_marker:
                raise YeongjuContractError(f"{source.code} page {page}: mixed/duplicate empty row")
            empty_marker = True
            continue
        if empty_marker or len(cells) != 8:
            raise YeongjuContractError(f"{source.code} page {page}: malformed course row")
        number = _clean(cells[0].get_text(" ", strip=True))
        year_text = _clean(cells[1].get_text(" ", strip=True))
        title_anchor = next(
            (
                anchor
                for anchor in cells[2].find_all("a", href=True)
                if _detail_identity(_clean(anchor.get("href")), source.url)
            ),
            None,
        )
        identity = (
            _detail_identity(_clean(title_anchor.get("href")), source.url)
            if title_anchor is not None
            else ""
        )
        title = _clean(title_anchor.get_text(" ", strip=True) if title_anchor else "")
        if not number.isdigit() or not re.fullmatch(r"20\d{2}", year_text) or not identity or not title:
            raise YeongjuContractError(f"{source.code} page {page}: row identity changed")
        source_status = _clean(cells[7].get_text(" ", strip=True))
        status = _STATUS_MAP.get(source_status, "")
        if not status:
            raise YeongjuContractError(
                f"{source.code} course {identity}: unknown status {source_status!r}"
            )
        year = int(year_text)
        apply_start, apply_end = _apply_range(
            _clean(cells[2].get_text(" ", strip=True)), year
        )
        event_start, event_end = _event_range(
            _clean(cells[5].get_text(" ", strip=True)), year
        )
        capacity_total = _first_int(_clean(cells[3].get_text(" ", strip=True)))
        capacity_current = _first_int(_clean(cells[4].get_text(" ", strip=True)))
        if capacity_total is None or capacity_current is None:
            raise YeongjuContractError(f"{source.code} course {identity}: capacity missing")
        parsed_rows.append(
            {
                "identity": identity,
                "number": int(number),
                "year": year,
                "title": title,
                "source_status": source_status,
                "status": status,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "event_start": event_start,
                "event_end": event_end,
                "schedule": _clean(cells[5].get_text(" ", strip=True)),
                "venue_fee": _clean(cells[6].get_text(" ", strip=True)),
                "capacity_total": capacity_total,
                "capacity_current": capacity_current,
                "source_catalogues": (source.code,),
                "source_pages": (page,),
                "detail_url": yeongju_detail_url(identity),
            }
        )
    if exposed != len(parsed_rows) + int(empty_marker):
        raise YeongjuContractError(f"{source.code} page {page}: unparsed source rows")
    return _ListPage(
        source=source,
        page=page,
        rows=tuple(parsed_rows),
        empty_marker=empty_marker,
        declared_pages=_declared_pages(soup, source),
    )


def _listed_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("identity"),
        row.get("year"),
        row.get("title"),
        row.get("source_status"),
        row.get("apply_start"),
        row.get("apply_end"),
        row.get("event_start"),
        row.get("event_end"),
        row.get("schedule"),
        row.get("venue_fee"),
        row.get("capacity_total"),
        row.get("capacity_current"),
    )


def _page_signature(page: _ListPage) -> tuple[Any, ...]:
    return (
        page.page,
        page.empty_marker,
        page.declared_pages,
        tuple(_listed_signature(row) for row in page.rows),
    )


def _merge_listed(existing: dict[str, Any], incoming: Mapping[str, Any]) -> None:
    if _listed_signature(existing) != _listed_signature(incoming):
        raise YeongjuContractError(
            f"course {existing.get('identity')}: sibling catalogue facts conflict"
        )
    existing["source_catalogues"] = tuple(
        dict.fromkeys((*existing["source_catalogues"], *incoming["source_catalogues"]))
    )
    existing["source_pages"] = tuple(
        dict.fromkeys((*existing["source_pages"], *incoming["source_pages"]))
    )


def _detail_fields(soup: BeautifulSoup, identity: str) -> dict[str, str]:
    table = soup.find("table", class_="table_basic")
    if table is None:
        raise YeongjuContractError(f"detail {identity}: information table missing")
    caption = table.find("caption")
    if _clean(caption.get_text(" ", strip=True) if caption else "") != "과목정보 목록":
        raise YeongjuContractError(f"detail {identity}: table caption changed")
    allowed = _DETAIL_REQUIRED_FIELDS | _DETAIL_SAFE_OPTIONAL_FIELDS | _DETAIL_IGNORED_FIELDS
    fields: dict[str, str] = {}
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        position = 0
        while position < len(cells):
            label_node = cells[position]
            if label_node.name != "th" or position + 1 >= len(cells):
                raise YeongjuContractError(f"detail {identity}: malformed label/value cells")
            value_node = cells[position + 1]
            if value_node.name != "td":
                raise YeongjuContractError(f"detail {identity}: malformed field value")
            label = _clean(label_node.get_text(" ", strip=True))
            if label not in allowed:
                raise YeongjuContractError(f"detail {identity}: unexpected field {label!r}")
            if label in fields:
                raise YeongjuContractError(f"detail {identity}: duplicate field {label!r}")
            # Never access text from a private/free-form value cell.
            fields[label] = (
                "present"
                if label in _DETAIL_IGNORED_FIELDS
                else _clean(value_node.get_text(" ", strip=True))
            )
            position += 2
    if not _DETAIL_REQUIRED_FIELDS.issubset(fields):
        missing = sorted(_DETAIL_REQUIRED_FIELDS - fields.keys())
        raise YeongjuContractError(f"detail {identity}: missing fields {missing}")
    return fields


def _application_contract(soup: BeautifulSoup, identity: str, status: str) -> str:
    control: Optional[Tag] = None
    for button in soup.select("#lecture_cnt .textr button"):
        if _clean(button.get("target")) == "접수하기" or _clean(
            button.get_text(" ", strip=True)
        ) == "접수하기":
            parent = button.find_parent("a", href=True)
            if parent is not None:
                control = parent
                break
    if control is None:
        if status == "OPEN":
            raise YeongjuContractError(f"detail {identity}: open application control missing")
        return "not_advertised"
    absolute = urljoin(YEONGJU_CANONICAL_URL, _clean(control.get("href")))
    parsed = urlparse(absolute)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != YEONGJU_HOST
        or parsed.path != YEONGJU_LOGIN_PATH
        or query != {"home_url": ["yeongjulll"], "code": ["MEMBER_LOGIN"]}
        or "seq" in query
    ):
        raise YeongjuContractError(f"detail {identity}: application gate changed")
    return "identityless_login_gate_excluded"


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"YEONGJU_BRANCH_{digest}"


def _parse_detail(soup: BeautifulSoup, listed: Mapping[str, Any]) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    title_node = soup.select_one("#lecture_cnt .detail_name")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if title != listed.get("title"):
        raise YeongjuContractError(f"detail {identity}: list/detail title mismatch")
    fields = _detail_fields(soup, identity)
    if fields["모집년도"] != str(listed.get("year")):
        raise YeongjuContractError(f"detail {identity}: recruitment year mismatch")
    detail_total = _first_int(fields["모집인원"])
    detail_current = _first_int(fields["신청인원"])
    if (
        detail_total != listed.get("capacity_total")
        or detail_current != listed.get("capacity_current")
    ):
        raise YeongjuContractError(f"detail {identity}: capacity mismatch")
    year = int(listed["year"])
    detail_start, detail_end = _event_range(fields["교육기간"], year)
    apply_dates = _date_tokens(fields["접수기간"], year)
    if len(apply_dates) < 2:
        raise YeongjuContractError(f"detail {identity}: application dates missing")
    if (
        detail_start != listed.get("event_start")
        or detail_end != listed.get("event_end")
        or apply_dates[0] != listed["apply_start"].date()
        or apply_dates[1] != listed["apply_end"].date()
    ):
        raise YeongjuContractError(f"detail {identity}: list/detail date mismatch")
    venue = fields["교육장소"]
    if not venue or len(venue) > 300 or _PHONE_RE.search(venue) or _EMAIL_RE.search(venue):
        raise YeongjuContractError(f"detail {identity}: unsafe education venue")
    status = _clean(listed.get("status"))
    contract = _application_contract(soup, identity, status)
    row: dict[str, Any] = {
        "provider": YEONGJU_PROVIDER,
        "provider_course_id": f"yeongju_lifelong:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": YEONGJU_BRANCH,
        "branch_code": _branch_code(YEONGJU_BRANCH),
        "preserve_branch": True,
        "category": "교육",
        "program_type": "영주시 평생학습 프로그램",
        "raw_url": yeongju_detail_url(identity),
        "reservation_available": False,
        "status": status,
        "fee": _clean(fields.get("결제금액")),
        "materials_fee": _clean(fields.get("재료비")),
        "period": f"{detail_start.isoformat()} ~ {detail_end.isoformat()}",
        "start_date": detail_start.isoformat(),
        "end_date": detail_end.isoformat(),
        "apply_period": (
            f"{listed['apply_start'].strftime('%Y-%m-%d %H:%M')} ~ "
            f"{listed['apply_end'].strftime('%Y-%m-%d %H:%M')}"
        ),
        "apply_start_date": listed["apply_start"].strftime("%Y-%m-%d %H:%M"),
        "apply_end_date": listed["apply_end"].strftime("%Y-%m-%d %H:%M"),
        "schedule_raw": fields["교육기간"],
        "capacity_current": int(listed["capacity_current"]),
        "capacity_total": int(listed["capacity_total"]),
        "venue_name": venue,
        "collection_category": "education",
        "domain_category": "교육",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "education",
        "collection_type": "course",
        "municipality_code": YEONGJU_MUNICIPALITY_CODE,
        "municipality_name": YEONGJU_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": YEONGJU_PARSER,
            "source_identity": identity,
            "source_catalogues": tuple(listed["source_catalogues"]),
            "source_pages": tuple(listed["source_pages"]),
            "source_status": _clean(listed.get("source_status")),
            "detail_verified": True,
            "application_control_contract": contract,
            "application_control_verified": True,
        },
    }
    _validate_output(row)
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _validate_output(row: Mapping[str, Any]) -> None:
    unknown = frozenset(row) - _ALLOWED_ROW_KEYS
    raw = row.get("raw_fields")
    if unknown or not isinstance(raw, Mapping) or frozenset(raw) - _ALLOWED_RAW_KEYS:
        raise YeongjuContractError("output contains a non-allowlisted field")
    safe_values = [
        row.get("title"),
        row.get("description"),
        row.get("branch"),
        row.get("fee"),
        row.get("materials_fee"),
        row.get("schedule_raw"),
        row.get("venue_name"),
    ]
    combined = " ".join(_clean(value) for value in safe_values)
    if _PHONE_RE.search(combined) or _EMAIL_RE.search(combined):
        raise YeongjuContractError("output leaked contact data")


def is_yeongju_education_target(target: Any) -> bool:
    """Return true only for the executable six-catalogue owner."""

    return (
        _clean(_target_value(target, "provider")) == YEONGJU_PROVIDER
        and _clean(_target_value(target, "url")) == YEONGJU_CANONICAL_URL
    )


is_target = is_yeongju_education_target


def collect_yeongju_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 200,
    cutoff: Optional[date] = None,
    session_factory: Optional[SessionFactory] = None,
    html_fetcher: Optional[HtmlFetcher] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Yeongju education snapshot."""

    audit_date = cutoff or datetime.now(ZoneInfo("Asia/Seoul")).date()
    factory = session_factory or _new_session
    fetcher = html_fetcher or _default_fetcher
    meta: dict[str, Any] = {
        "municipality_code": YEONGJU_MUNICIPALITY_CODE,
        "owner_provider": YEONGJU_PROVIDER,
        "canonical_url": YEONGJU_CANONICAL_URL,
        "parser": YEONGJU_PARSER,
        "cutoff": audit_date.isoformat(),
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "pagination_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "application_form_requests": 0,
        "login_page_requests": 0,
    }
    try:
        if not is_yeongju_education_target(target):
            raise YeongjuContractError("target is not the canonical Yeongju owner")
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise YeongjuContractError("invalid collector limits")

        current = factory()
        all_rows: list[dict[str, Any]] = []
        catalogue_counts: dict[str, int] = {}
        catalogue_pages: dict[str, int] = {}
        empty_sentinel_pages: dict[str, str] = {}
        try:
            for source in YEONGJU_CATALOGUES:
                first = _parse_list_page(
                    _fetch_soup(current, yeongju_catalogue_url(source, 1), timeout, fetcher, sleeper),
                    source,
                    1,
                )
                meta["list_requests"] += 1
                declared = first.declared_pages
                if declared > max_pages:
                    meta["source_cap_reached"] = True
                    raise YeongjuContractError(
                        f"{source.code}: {declared} pages exceed max_pages"
                    )
                pages = {1: first}
                for page_number in range(2, declared + 1):
                    parsed = _parse_list_page(
                        _fetch_soup(
                            current,
                            yeongju_catalogue_url(source, page_number),
                            timeout,
                            fetcher,
                            sleeper,
                        ),
                        source,
                        page_number,
                    )
                    meta["list_requests"] += 1
                    if parsed.empty_marker or parsed.declared_pages != declared:
                        raise YeongjuContractError(
                            f"{source.code} page {page_number}: pagination boundary changed"
                        )
                    pages[page_number] = parsed

                sentinel = _parse_list_page(
                    _fetch_soup(current, yeongju_sentinel_url(source), timeout, fetcher, sleeper),
                    source,
                    0,
                )
                first_check = _parse_list_page(
                    _fetch_soup(current, yeongju_catalogue_url(source, 1), timeout, fetcher, sleeper),
                    source,
                    1,
                )
                last_check = _parse_list_page(
                    _fetch_soup(current, yeongju_catalogue_url(source, declared), timeout, fetcher, sleeper),
                    source,
                    declared,
                )
                sentinel_check = _parse_list_page(
                    _fetch_soup(current, yeongju_sentinel_url(source), timeout, fetcher, sleeper),
                    source,
                    0,
                )
                meta["list_requests"] += 4
                if (
                    sentinel.rows
                    or not sentinel.empty_marker
                    or sentinel_check.rows
                    or not sentinel_check.empty_marker
                    or _page_signature(first_check) != _page_signature(first)
                    or _page_signature(last_check) != _page_signature(pages[declared])
                    or _page_signature(sentinel_check) != _page_signature(sentinel)
                ):
                    raise YeongjuContractError(
                        f"{source.code}: first/last/empty sentinel stability failed"
                    )
                source_rows = [row for number in sorted(pages) for row in pages[number].rows]
                if any(page.empty_marker and page.rows for page in pages.values()):
                    raise YeongjuContractError(f"{source.code}: mixed empty/data page")
                if first.empty_marker and source_rows:
                    raise YeongjuContractError(f"{source.code}: data after empty first page")
                identities = [row["identity"] for row in source_rows]
                if len(identities) != len(set(identities)):
                    raise YeongjuContractError(f"{source.code}: duplicate identity within catalogue")
                catalogue_counts[source.code] = len(source_rows)
                catalogue_pages[source.code] = declared
                empty_sentinel_pages[source.code] = yeongju_sentinel_url(source)
                all_rows.extend(source_rows)
        finally:
            close = getattr(current, "close", None)
            if callable(close):
                close()

        unique: dict[str, dict[str, Any]] = {}
        duplicate_count = 0
        for row in all_rows:
            identity = row["identity"]
            if identity in unique:
                _merge_listed(unique[identity], row)
                duplicate_count += 1
            else:
                unique[identity] = dict(row)
        listed = list(unique.values())
        current_rows = [row for row in listed if row["event_end"] >= audit_date]
        expired_rows = [row for row in listed if row["event_end"] < audit_date]
        for row in current_rows:
            if row["status"] == "OPEN" and not (
                row["apply_start"].date() <= audit_date <= row["apply_end"].date()
            ):
                raise YeongjuContractError(
                    f"course {row['identity']}: OPEN status contradicts reception dates"
                )
            if row["status"] == "SCHEDULED" and audit_date >= row["apply_start"].date():
                raise YeongjuContractError(
                    f"course {row['identity']}: SCHEDULED status contradicts reception dates"
                )
        if len(current_rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise YeongjuContractError("detail_limit would create a partial snapshot")

        detailed: list[dict[str, Any]] = []
        detail_session = factory()
        try:
            for listed_row in current_rows:
                soup = _fetch_soup(
                    detail_session,
                    listed_row["detail_url"],
                    timeout,
                    fetcher,
                    sleeper,
                )
                detailed.append(_parse_detail(soup, listed_row))
                meta["detail_pages"] += 1
        finally:
            close = getattr(detail_session, "close", None)
            if callable(close):
                close()

        output = detailed
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        if len(output) != len(detailed):
            raise YeongjuContractError("external dedupe removed an owned unique identity")
        snapshot_complete = True
        current_catalogue_counts = dict(
            Counter(
                code
                for row in current_rows
                for code in row["source_catalogues"]
            )
        )
        for source in YEONGJU_CATALOGUES:
            current_catalogue_counts.setdefault(source.code, 0)
        meta.update(
            {
                "pages": int(meta["list_requests"]) + int(meta["detail_pages"]),
                "data_pages": sum(catalogue_pages.values()),
                "catalogue_pages": catalogue_pages,
                "catalogue_source_counts": catalogue_counts,
                "catalogue_current_counts": current_catalogue_counts,
                "empty_sentinel_urls": empty_sentinel_pages,
                "empty_sentinel_requests": len(YEONGJU_CATALOGUES) * 2,
                "stability_rechecks": len(YEONGJU_CATALOGUES) * 3,
                "source_rows": len(all_rows),
                "source_total": len(all_rows),
                "unique_source_rows": len(listed),
                "cross_catalogue_duplicates": duplicate_count,
                "source_status_counts": dict(Counter(row["source_status"] for row in all_rows)),
                "current_source_count": len(current_rows),
                "expired_source_count": len(expired_rows),
                "current_status_counts": dict(Counter(row["status"] for row in current_rows)),
                "detail_verified": len(detailed),
                "application_controls_verified": len(detailed),
                "identityless_login_gates_excluded": sum(
                    row.get("raw_fields", {}).get("application_control_contract")
                    == "identityless_login_gate_excluded"
                    for row in output
                ),
                "active_application_controls": 0,
                "branch_counts": dict(Counter(row["branch"] for row in output)),
                "output_rows": len(output),
                "pagination_complete": True,
                "pagination_exhausted": True,
                "details_complete": len(detailed) == len(current_rows),
                "snapshot_complete": snapshot_complete,
                "no_current_data": bool(snapshot_complete and not current_rows),
                "no_current_reason": (
                    "all six official current education catalogues are empty"
                    if not current_rows
                    else ""
                ),
                "configured_collection_error": "",
            }
        )
        return output, YEONGJU_PARSER, meta
    except (YeongjuContractError, requests.RequestException, ValueError, TypeError) as exc:
        meta.update(
            {
                "configured_collection_error": _clean(exc),
                "pagination_complete": False,
                "pagination_exhausted": False,
                "snapshot_complete": False,
                "no_current_data": False,
                "output_rows": 0,
            }
        )
        return [], YEONGJU_PARSER, meta


collect = collect_yeongju_education


__all__ = [
    "YEONGJU_BRANCH",
    "YEONGJU_CANONICAL_URL",
    "YEONGJU_CATALOGUES",
    "YEONGJU_DISCOVERY_AUDIT",
    "YEONGJU_EMPTY_SENTINEL_CLASS",
    "YEONGJU_GUIDE_CANDIDATE_ID",
    "YEONGJU_GUIDE_DEPRECATED_PROVIDER",
    "YEONGJU_GUIDE_PROVIDER",
    "YEONGJU_GUIDE_URL",
    "YEONGJU_INCLUDED_SCOPE_URLS",
    "YEONGJU_MUNICIPALITY_CODE",
    "YEONGJU_MUNICIPALITY_NAME",
    "YEONGJU_ONEDAY_CANDIDATE_ID",
    "YEONGJU_ONEDAY_PROVIDER",
    "YEONGJU_ONEDAY_URL",
    "YEONGJU_OWNER_BOUNDARY_AUDIT",
    "YEONGJU_PARSER",
    "YEONGJU_PII_FIELDS_NEVER_READ",
    "YEONGJU_PROVIDER",
    "YEONGJU_ROOT_CANDIDATE_ID",
    "YEONGJU_ROOT_PROVIDER",
    "YEONGJU_ROOT_URL",
    "YeongjuCatalogue",
    "YeongjuContractError",
    "collect_yeongju_education",
    "is_yeongju_education_target",
    "yeongju_catalogue_url",
    "yeongju_detail_url",
    "yeongju_sentinel_url",
]
