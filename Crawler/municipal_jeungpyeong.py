"""Fail-closed collectors for Jeungpyeong County education catalogues.

Jeungpyeong has two independent HTTPS course owners that can be promoted:

* the county lifelong-learning portal, whose complete ledger is split across
  regular, specialised and outreach catalogues; and
* Jeungpyeong County Library's culture-programme catalogue.

The youth-centre ledger is deliberately not collected here.  Its public
course list is HTTP-only and the HTTPS endpoint presents a certificate for a
different hostname.  Resident-centre records are also kept outside this
collector: the structured county ledger is stale, while current programmes
are published as application-ended notice-board prose.

Every data page and an immediate empty sentinel are checked, boundary pages
are re-read for stability, and every returned record is bound to its public
detail and application identity.  Application/result forms, applicant data,
contacts, instructors, attachments, free-form descriptions and source HTML
are never requested or persisted.  Any incomplete contract returns an empty
snapshot.
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


JEUNGPYEONG_MUNICIPALITY_CODE = "4374500000"
JEUNGPYEONG_MUNICIPALITY_NAME = "충청북도 증평군"

JEUNGPYEONG_LIFELONG_PROVIDER = "MUNI_WWW_JP_GO_KR_44B42971"
JEUNGPYEONG_LIFELONG_CANDIDATE_ID = "MUNI_IR_2028A1584014"
JEUNGPYEONG_LIFELONG_HOST = "www.jp.go.kr"
JEUNGPYEONG_LIFELONG_LANDING_URL = "https://www.jp.go.kr/lll.do"
JEUNGPYEONG_LIFELONG_PAGE_SIZE = 200
JEUNGPYEONG_LIFELONG_CATALOGUES: dict[str, tuple[str, str]] = {
    "regular": ("정규 프로그램", "sub02_01_02"),
    "special": ("특성화 프로그램", "sub02_02_01"),
    "outreach": ("찾아가는 교육문화", "sub02_03_01"),
}
JEUNGPYEONG_LIFELONG_CANONICAL_URL = (
    "https://www.jp.go.kr/prog/course/lll/sub02_01_02/list.do"
)
JEUNGPYEONG_LIFELONG_PARSER = (
    "jeungpyeong_lifelong_three_catalogues+declared_totals+all_pages+"
    "empty_sentinels+stable_first_last+recruitment_closed_status+current_details+"
    "identity_bound_application_controls+exact_venues+pii_allowlist"
)

JEUNGPYEONG_LIBRARY_PROVIDER = "MUNI_LIB_JP_GO_KR_57C5EEED"
JEUNGPYEONG_LIBRARY_CANDIDATE_ID = "MUNI_IR_00E5B1C95302"
JEUNGPYEONG_LIBRARY_HOST = "lib.jp.go.kr"
JEUNGPYEONG_LIBRARY_PATH = "/front/index.php"
JEUNGPYEONG_LIBRARY_CANONICAL_URL = (
    "https://lib.jp.go.kr/front/index.php?g_page=culture&m_page=culture01"
)
JEUNGPYEONG_LIBRARY_PAGE_SIZE = 10
JEUNGPYEONG_LIBRARY_PARSER = (
    "jeungpyeong_library_complete_catalogue+sequence_total+all_pages+"
    "empty_sentinel+stable_first_last+all_public_details+"
    "identity_bound_application_controls+exact_venues+pii_allowlist"
)

JEUNGPYEONG_YOUTH_PROVIDER = "MUNI_WWW_JPYOUTH_CO_KR_5E838FBF"
JEUNGPYEONG_YOUTH_CANDIDATE_ID = "MUNI_IR_A8AD3A380C1A"
JEUNGPYEONG_YOUTH_URL = "http:" "//www.jpyouth.co.kr/sub.php?menukey=54"
JEUNGPYEONG_RESIDENT_LEDGER_URL = (
    "https://www.jp.go.kr/jp/prog/juminCenter/sub04_03/list.do"
)
JEUNGPYEONG_EUP_NOTICE_URL = (
    "https://www.jp.go.kr/jp/cop/bbs/BBSMSTR_000000000181/"
    "selectBoardArticle.do?nttId=B00000055903dy4nF1sp"
)
JEUNGPYEONG_DOAN_NOTICE_URL = (
    "https://www.jp.go.kr/da/cop/bbs/BBSMSTR_000000000183/"
    "selectBoardArticle.do?nttId=B00000056518vc6oC4ly"
)

JEUNGPYEONG_DISCOVERY_AUDIT: dict[str, Any] = {
    "lifelong": {
        "decision": "include_separate_owner",
        "canonical_url": JEUNGPYEONG_LIFELONG_CANONICAL_URL,
        "catalogues": tuple(
            f"https://{JEUNGPYEONG_LIFELONG_HOST}/prog/course/lll/{path}/list.do"
            for _label, path in JEUNGPYEONG_LIFELONG_CATALOGUES.values()
        ),
    },
    "library": {
        "decision": "include_separate_owner",
        "canonical_url": JEUNGPYEONG_LIBRARY_CANONICAL_URL,
    },
    "youth": {
        "decision": "blocked_http_only_tls_hostname_mismatch",
        "url": JEUNGPYEONG_YOUTH_URL,
        "candidate_id": JEUNGPYEONG_YOUTH_CANDIDATE_ID,
    },
    "resident_centres": {
        "decision": "exclude_stale_ledger_and_notice_only_current_programmes",
        "ledger_url": JEUNGPYEONG_RESIDENT_LEDGER_URL,
        "current_notice_urls": (
            JEUNGPYEONG_EUP_NOTICE_URL,
            JEUNGPYEONG_DOAN_NOTICE_URL,
        ),
    },
}

JEUNGPYEONG_PII_FIELDS_NEVER_PERSISTED = (
    "신청자명",
    "생년월일",
    "주소",
    "전화번호",
    "이메일",
    "강사명",
    "담당자",
    "문의전화",
    "첨부파일",
    "신청서 본문",
)

JEUNGPYEONG_MAX_HTML_BYTES = 3_000_000
JEUNGPYEONG_MAX_WORKERS = 8
JEUNGPYEONG_PARSER = "jeungpyeong_https_education_owner_dispatch"

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class JeungpyeongContractError(ValueError):
    """Raised when an audited Jeungpyeong public-source contract changes."""


@dataclass(frozen=True)
class _LifelongPage:
    catalogue: str
    requested: int
    observed: int
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]
    empty_marker: bool


@dataclass(frozen=True)
class _LibraryPage:
    requested: int
    observed: Optional[int]
    last: int
    rows: tuple[dict[str, Any], ...]


_SPACE = re.compile(r"\s+")
_LIFELONG_PAGE_INFO = re.compile(
    r"총\s*게시물\s*([\d,]+)\s*개\s*,\s*페이지\s*(\d+)\s*/\s*(\d+)"
)
_LIFELONG_ONCLICK = re.compile(
    r"\s*fn_search_detail\(([1-9]\d*)\);\s*return\s+false;\s*"
)
_DASH_PERIOD = re.compile(
    r"(20\d{2})-(\d{2})-(\d{2})\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})"
)
_DASH_DATE_TOKEN = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_DOT_DATE = re.compile(r"(?<!\d)(20\d{2})\.(\d{1,2})\.(\d{1,2})(?!\d)")
_CAPACITY_TRIPLE = re.compile(
    r"([\d,]+)\s*/\s*([\d,]+)\s*/\s*([\d,]+)"
)
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIFELONG_HEADINGS = (
    "번호",
    "강좌명",
    "교육 기간",
    "교육 시간",
    "신청 / 모집 / 대기인원",
    "상태",
)
_LIFELONG_STATUS_MAP: Mapping[str, str] = {
    "모집중": "OPEN",
    "모집마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
}
_LIFELONG_STATUSES = set(_LIFELONG_STATUS_MAP)
_LIBRARY_HEADINGS = (
    "번호",
    "분류",
    "교육명",
    "대상 정원 / 온라인 / 대기",
    "접수기간",
    "상태",
)

_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "catalogue",
        "catalogue_name",
        "list_page",
        "source_sequence",
        "source_status",
        "source_category",
        "source_scope",
        "source_venue",
        "source_target",
        "source_period",
        "source_apply_period",
        "source_schedule",
        "source_capacity_current",
        "source_capacity_total",
        "source_waitlist_current",
        "source_online_capacity",
        "source_waitlist_capacity",
        "detail_verified",
        "application_control_present",
        "result_control_present_not_requested",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "instructor",
        "staff",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).casefold()


def _temporal_key(value: Any) -> str:
    """Normalise display-only zero padding while retaining date/time identity."""

    zero_normalized = re.sub(r"(?<!\d)0(?=\d:)", "", _clean(value))
    return _compact(zero_normalized)


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _target_kind(target: Any) -> str:
    provider = _clean(_value(target, "provider"))
    url = _clean(_value(target, "url"))
    if (
        provider == JEUNGPYEONG_LIFELONG_PROVIDER
        and url == JEUNGPYEONG_LIFELONG_CANONICAL_URL
    ):
        return "lifelong"
    if (
        provider == JEUNGPYEONG_LIBRARY_PROVIDER
        and url == JEUNGPYEONG_LIBRARY_CANONICAL_URL
    ):
        return "library"
    return ""


def is_jeungpyeong_lifelong_target(target: Any) -> bool:
    return _target_kind(target) == "lifelong"


def is_jeungpyeong_library_target(target: Any) -> bool:
    return _target_kind(target) == "library"


def is_jeungpyeong_education_target(target: Any) -> bool:
    return bool(_target_kind(target))


is_target = is_jeungpyeong_education_target


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive integer")
    raw = _clean(value)
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"{label} must be a positive integer")
    return int(raw)


def jeungpyeong_lifelong_list_url(catalogue: str, page: Any = 1) -> str:
    if catalogue not in JEUNGPYEONG_LIFELONG_CATALOGUES:
        raise ValueError("unknown lifelong catalogue")
    page_number = _positive_int(page, "page")
    path = JEUNGPYEONG_LIFELONG_CATALOGUES[catalogue][1]
    base = (
        f"https://{JEUNGPYEONG_LIFELONG_HOST}/prog/course/lll/"
        f"{path}/list.do"
    )
    return base + "?" + urlencode(
        {"pageIndex": page_number, "pageUnit": JEUNGPYEONG_LIFELONG_PAGE_SIZE}
    )


def jeungpyeong_lifelong_detail_url(catalogue: str, identity: Any) -> str:
    course_no = _positive_int(identity, "courseNo")
    if catalogue not in JEUNGPYEONG_LIFELONG_CATALOGUES:
        raise ValueError("unknown lifelong catalogue")
    path = JEUNGPYEONG_LIFELONG_CATALOGUES[catalogue][1]
    return (
        f"https://{JEUNGPYEONG_LIFELONG_HOST}/prog/course/lll/{path}/view.do?"
        + urlencode({"courseNo": course_no})
    )


def jeungpyeong_lifelong_application_url(catalogue: str, identity: Any) -> str:
    course_no = _positive_int(identity, "courseNo")
    if catalogue not in JEUNGPYEONG_LIFELONG_CATALOGUES:
        raise ValueError("unknown lifelong catalogue")
    path = JEUNGPYEONG_LIFELONG_CATALOGUES[catalogue][1]
    return (
        f"https://{JEUNGPYEONG_LIFELONG_HOST}/prog/aplcnt/lll/{path}/write.do?"
        + urlencode({"courseNo": course_no})
    )


def jeungpyeong_library_list_url(page: Any = 1) -> str:
    page_number = _positive_int(page, "page")
    return JEUNGPYEONG_LIBRARY_CANONICAL_URL + "&" + urlencode({"page": page_number})


def _library_identity(value: Any, label: str) -> int:
    return _positive_int(value, label)


def jeungpyeong_library_detail_url(lg_code: Any, le_code: Any) -> str:
    group = _library_identity(lg_code, "lgCode")
    lecture = _library_identity(le_code, "leCode")
    return (
        JEUNGPYEONG_LIBRARY_CANONICAL_URL
        + "&"
        + urlencode(
            {
                "act": "lecture_view",
                "lgCode": group,
                "leCode": lecture,
                "cate": "",
            }
        )
    )


def jeungpyeong_library_application_url(lg_code: Any, le_code: Any) -> str:
    group = _library_identity(lg_code, "lgCode")
    lecture = _library_identity(le_code, "leCode")
    return (
        JEUNGPYEONG_LIBRARY_CANONICAL_URL
        + "&"
        + urlencode(
            {
                "act": "lecture_receive_form",
                "lgCode": group,
                "leCode": lecture,
                "cate": "",
            }
        )
    )


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _coerce_soup(value: Any, requested_url: str, expected_host: str) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise JeungpyeongContractError(f"unexpected HTTP status {status}")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location"):
        raise JeungpyeongContractError("redirect response is not accepted")
    final_url = _clean(getattr(value, "url", requested_url)) or requested_url
    parsed = urlparse(final_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise JeungpyeongContractError("invalid final response URL") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise JeungpyeongContractError("response left the audited HTTPS owner")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if not content:
        raise JeungpyeongContractError("empty HTML response")
    if len(content) > JEUNGPYEONG_MAX_HTML_BYTES:
        raise JeungpyeongContractError("HTML response exceeds safety limit")
    return BeautifulSoup(content, "lxml")


def _fetch_many(
    items: list[tuple[Any, str]],
    *,
    timeout: int,
    max_workers: int,
    expected_host: str,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> dict[Any, BeautifulSoup]:
    if not items:
        return {}
    workers = min(max_workers, len(items))
    chunks: list[list[tuple[Any, str]]] = [[] for _ in range(workers)]
    for index, item in enumerate(items):
        chunks[index % workers].append(item)

    def run(chunk: list[tuple[Any, str]]) -> tuple[dict[Any, BeautifulSoup], list[str]]:
        values: dict[Any, BeautifulSoup] = {}
        errors: list[str] = []
        current = session_factory()
        try:
            for key, url in chunk:
                try:
                    response = fetcher(current, url, timeout)
                    values[key] = _coerce_soup(response, url, expected_host)
                except Exception as exc:
                    errors.append(f"{key}: {type(exc).__name__}: {_clean(exc)}")
        finally:
            _close_quietly(current)
        return values, errors

    values: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run, chunk) for chunk in chunks if chunk]
        for future in as_completed(futures):
            current_values, current_errors = future.result()
            values.update(current_values)
            errors.extend(current_errors)
    if errors:
        raise JeungpyeongContractError("; ".join(sorted(errors)))
    if len(values) != len(items):
        raise JeungpyeongContractError("parallel fetch cardinality changed")
    return values


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    raw = _clean(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise ValueError("today must be an ISO date")
    return date.fromisoformat(raw)


def _dash_period(value: Any) -> tuple[date, date]:
    match = _DASH_PERIOD.fullmatch(_clean(value))
    if not match:
        raise JeungpyeongContractError("invalid education period")
    numbers = [int(part) for part in match.groups()]
    start = date(*numbers[:3])
    end = date(*numbers[3:])
    if end < start:
        raise JeungpyeongContractError("reversed education period")
    return start, end


def _dash_date_pair(value: Any, context: str) -> tuple[date, date]:
    matches = _DASH_DATE_TOKEN.findall(_clean(value))
    if len(matches) != 2:
        raise JeungpyeongContractError(f"{context} requires exactly two dates")
    start, end = (date(*(int(part) for part in match)) for match in matches)
    if end < start:
        raise JeungpyeongContractError(f"{context} is reversed")
    return start, end


def _dot_period(value: Any) -> tuple[date, date]:
    dates: list[date] = []
    for year, month, day in _DOT_DATE.findall(_clean(value)):
        dates.append(date(int(year), int(month), int(day)))
    if len(dates) not in {1, 2}:
        raise JeungpyeongContractError("invalid library course period")
    start = dates[0]
    end = dates[-1]
    if end < start:
        raise JeungpyeongContractError("reversed library course period")
    return start, end


def _branch_code(provider: str, branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"{provider}:{digest}"


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    payload = "\n".join(
        "|".join(
            _clean(row.get(key))
            for key in (
                "identity",
                "sequence",
                "title",
                "period",
                "schedule",
                "source_status",
                "apply_period",
                "target",
                "venue",
                "application_url",
            )
        )
        for row in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_lifelong_page(
    soup: BeautifulSoup, catalogue: str, requested: int
) -> _LifelongPage:
    table = soup.select_one("table.tbl_basic.coursetbl")
    if table is None:
        raise JeungpyeongContractError(f"{catalogue} page {requested}: course table missing")
    headings = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headings != _LIFELONG_HEADINGS:
        raise JeungpyeongContractError(f"{catalogue} page {requested}: headings changed")
    page_info = soup.select_one(".pageInfo")
    match = _LIFELONG_PAGE_INFO.search(
        _clean(page_info.get_text(" ", strip=True) if page_info else "")
    )
    if not match:
        raise JeungpyeongContractError(f"{catalogue} page {requested}: page info missing")
    total, observed, last = (int(part.replace(",", "")) for part in match.groups())
    if observed != requested or last < 1:
        raise JeungpyeongContractError(f"{catalogue} page {requested}: page identity changed")

    source_rows = table.select("tbody > tr")
    empty_marker = bool(
        len(source_rows) == 1
        and not source_rows[0].get("onclick")
        and len(source_rows[0].select(":scope > td")) == 1
        and "내용이 존재 하지 않습니다." in _clean(source_rows[0].get_text(" ", strip=True))
    )
    rows: list[dict[str, Any]] = []
    if not empty_marker:
        for row in source_rows:
            identity_match = _LIFELONG_ONCLICK.fullmatch(row.get("onclick", ""))
            cells = row.select(":scope > td")
            if not identity_match or len(cells) != 6:
                raise JeungpyeongContractError(
                    f"{catalogue} page {requested}: malformed course row"
                )
            sequence_text = _clean(cells[0].get_text(" ", strip=True))
            if not sequence_text.isdigit() or int(sequence_text) < 1:
                raise JeungpyeongContractError(
                    f"{catalogue} page {requested}: invalid sequence"
                )
            title = _clean(cells[1].get_text(" ", strip=True))
            period = _clean(cells[2].get_text(" ", strip=True))
            schedule = _clean(cells[3].get_text(" ", strip=True))
            capacity_text = _clean(cells[4].get_text(" ", strip=True))
            capacity = _CAPACITY_TRIPLE.fullmatch(capacity_text)
            source_status = _clean(cells[5].get_text(" ", strip=True))
            if not title or not capacity or source_status not in _LIFELONG_STATUSES:
                raise JeungpyeongContractError(
                    f"{catalogue} page {requested}: invalid public course fields"
                )
            start, end = _dash_period(period)
            current, online_capacity, waiting = (
                int(part.replace(",", "")) for part in capacity.groups()
            )
            rows.append(
                {
                    "catalogue": catalogue,
                    "catalogue_name": JEUNGPYEONG_LIFELONG_CATALOGUES[catalogue][0],
                    "list_page": requested,
                    "identity": identity_match.group(1),
                    "sequence": int(sequence_text),
                    "title": title,
                    "period": period,
                    "start": start,
                    "end": end,
                    "schedule": schedule,
                    "capacity_current": current,
                    "online_capacity": online_capacity,
                    "waitlist_current": waiting,
                    "source_status": source_status,
                }
            )
    return _LifelongPage(
        catalogue=catalogue,
        requested=requested,
        observed=observed,
        total=total,
        last=last,
        rows=tuple(rows),
        empty_marker=empty_marker,
    )


def _lifelong_page_signature(page: _LifelongPage) -> str:
    return hashlib.sha256(
        (
            f"{page.catalogue}|{page.total}|{page.last}|{page.empty_marker}|"
            f"{_page_signature(page.rows)}"
        ).encode("utf-8")
    ).hexdigest()


def _direct_label_fields(nodes: Iterable[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in nodes:
        strings = [_clean(value) for value in node.stripped_strings]
        if not strings:
            continue
        label = strings[0]
        value = _clean(" ".join(strings[1:]))
        if label in result:
            raise JeungpyeongContractError(f"duplicate detail label {label}")
        result[label] = value
    return result


def _table_fields(table: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in table.select("tr"):
        cells = row.select(":scope > th, :scope > td")
        index = 0
        while index < len(cells):
            if cells[index].name != "th" or index + 1 >= len(cells) or cells[index + 1].name != "td":
                raise JeungpyeongContractError("detail table structure changed")
            label = _clean(cells[index].get_text(" ", strip=True))
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if label in result:
                raise JeungpyeongContractError(f"duplicate table label {label}")
            result[label] = value
            index += 2
    return result


def _lifelong_methods(value: Any) -> list[str]:
    text = _clean(value)
    methods = [method for method in ("온라인", "전화", "방문") if method in text]
    return methods or (["기타"] if text else [])


def _parse_lifelong_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> dict[str, Any]:
    catalogue = _clean(listed.get("catalogue"))
    identity = _clean(listed.get("identity"))
    root = soup.select_one(".eduView")
    top = root.select_one(".topBox") if root else None
    strong = top.select_one(".info > strong") if top else None
    status_node = top.select_one(".thumb .cate") if top else None
    table = root.select_one("table.tbl_basic") if root else None
    if root is None or top is None or strong is None or status_node is None or table is None:
        raise JeungpyeongContractError(f"course {identity}: detail shell missing")

    title_strings = [_clean(value) for value in strong.stripped_strings]
    if len(title_strings) < 2:
        raise JeungpyeongContractError(f"course {identity}: detail title missing")
    source_category = title_strings[0]
    title = _clean(" ".join(title_strings[1:]))
    status_strings = [_clean(value) for value in status_node.stripped_strings]
    if not status_strings:
        raise JeungpyeongContractError(f"course {identity}: detail status missing")
    source_status = status_strings[0]
    if title != _clean(listed.get("title")) or source_status != _clean(
        listed.get("source_status")
    ):
        raise JeungpyeongContractError(f"course {identity}: list/detail identity drift")

    info_fields = _direct_label_fields(top.select(".info > ul > li"))
    required_info = {"교육기간", "교육시간", "접수기간", "모집방법", "모집인원"}
    if not required_info <= set(info_fields):
        raise JeungpyeongContractError(f"course {identity}: public detail fields missing")
    if (
        _compact(info_fields["교육기간"]) != _compact(listed.get("period"))
        or _compact(info_fields["교육시간"]) != _compact(listed.get("schedule"))
    ):
        raise JeungpyeongContractError(f"course {identity}: list/detail schedule drift")
    maximum = _clean(info_fields["모집인원"]).replace(",", "")
    method_capacities = {
        method: int(value.replace(",", ""))
        for method, value in re.findall(
            r"(온라인|전화|방문)\s*\(([\d,]+)\)", info_fields["모집방법"]
        )
    }
    if (
        not maximum.isdigit()
        or method_capacities.get("온라인") != int(listed["online_capacity"])
        or sum(method_capacities.values()) != int(maximum)
    ):
        raise JeungpyeongContractError(f"course {identity}: capacity drift")
    capacity_total = int(maximum)

    basic = _table_fields(table)
    if not {"교육대상", "교육장소", "수강료"} <= set(basic):
        raise JeungpyeongContractError(f"course {identity}: basic fields missing")
    venue = _clean(basic["교육장소"])
    if not venue:
        raise JeungpyeongContractError(f"course {identity}: education venue missing")

    path = JEUNGPYEONG_LIFELONG_CATALOGUES[catalogue][1]
    scripts = "\n".join(node.get_text(" ", strip=False) for node in soup.select("script"))
    script_pattern = re.compile(
        rf"/prog/aplcnt/lll/{re.escape(path)}/write\.do\?courseNo=([1-9]\d*)"
    )
    script_identities = script_pattern.findall(scripts)
    if script_identities != [identity]:
        raise JeungpyeongContractError(
            f"course {identity}: application script identity changed"
        )
    controls = [
        node
        for node in soup.select(".btnbox button[onclick]")
        if _compact(node.get("onclick")) == "fn_search_regist();returnfalse;"
        and _clean(node.get_text(" ", strip=True)) == "신청하기"
    ]
    is_open = source_status == "모집중"
    if len(controls) != (1 if is_open else 0):
        raise JeungpyeongContractError(
            f"course {identity}: application control/status drift"
        )
    methods = _lifelong_methods(info_fields["모집방법"])
    if is_open and "온라인" not in methods:
        raise JeungpyeongContractError(
            f"course {identity}: open course is not bound to online application"
        )

    start = listed["start"]
    end = listed["end"]
    apply_start, apply_end = _dash_date_pair(
        info_fields["접수기간"], f"course {identity} application period"
    )
    if is_open and not apply_start <= cutoff <= apply_end:
        raise JeungpyeongContractError(
            f"course {identity}: open status/application dates disagree"
        )
    target = _clean(basic["교육대상"])
    fee = _clean(basic["수강료"])
    schedule = _clean(listed["schedule"])
    application_url = (
        jeungpyeong_lifelong_application_url(catalogue, identity) if is_open else ""
    )
    provider_course_id = f"{JEUNGPYEONG_LIFELONG_PROVIDER}:course:{identity}"
    return {
        "provider": JEUNGPYEONG_LIFELONG_PROVIDER,
        "provider_course_id": provider_course_id,
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": venue,
        "branch_code": _branch_code(JEUNGPYEONG_LIFELONG_PROVIDER, venue),
        "preserve_branch": True,
        "category": "평생학습",
        "program_type": "교육",
        "raw_url": jeungpyeong_lifelong_detail_url(catalogue, identity),
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if is_open else "INFO_ONLY",
        "application_method": ", ".join(methods),
        "application_methods": methods,
        "reservation_available": is_open,
        "status": _LIFELONG_STATUS_MAP[source_status],
        "fee": fee or "요금 별도 안내",
        "period": _clean(listed["period"]),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": _clean(info_fields["접수기간"]),
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": schedule or "시간 별도 안내",
        "capacity": f"{capacity_total}명",
        "capacity_current": int(listed["capacity_current"]),
        "capacity_total": capacity_total,
        "waitlist_current": int(listed["waitlist_current"]),
        "target": target or "대상 별도 안내",
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": JEUNGPYEONG_LIFELONG_PARSER,
        "municipality_code": JEUNGPYEONG_MUNICIPALITY_CODE,
        "municipality_full_name": JEUNGPYEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "catalogue": catalogue,
            "catalogue_name": _clean(listed["catalogue_name"]),
            "list_page": int(listed["list_page"]),
            "source_sequence": int(listed["sequence"]),
            "source_status": source_status,
            "source_category": source_category,
            "source_venue": venue,
            "source_target": target,
            "source_period": _clean(listed["period"]),
            "source_apply_period": _clean(info_fields["접수기간"]),
            "source_schedule": schedule,
            "source_capacity_current": int(listed["capacity_current"]),
            "source_capacity_total": capacity_total,
            "source_online_capacity": int(listed["online_capacity"]),
            "source_waitlist_current": int(listed["waitlist_current"]),
            "detail_verified": True,
            "application_control_present": is_open,
            "service_family": "education",
        },
    }


def _query_identity(
    href: str,
    *,
    expected_act: str,
    lg_code: str,
    le_code: str,
) -> bool:
    parsed = urlparse(urljoin(JEUNGPYEONG_LIBRARY_CANONICAL_URL, href))
    try:
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != JEUNGPYEONG_LIBRARY_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != JEUNGPYEONG_LIBRARY_PATH
        or parsed.fragment
    ):
        return False
    if len(pairs) != len({key for key, _value in pairs}):
        return False
    values = dict(pairs)
    required = {
        "g_page": "culture",
        "m_page": "culture01",
        "act": expected_act,
        "lgCode": lg_code,
        "leCode": le_code,
    }
    if any(values.get(key) != value for key, value in required.items()):
        return False
    extras = set(values) - set(required)
    return extras <= {"cate"} and ("cate" not in values or values["cate"] == "")


def _library_pagination(soup: BeautifulSoup, requested: int) -> tuple[Optional[int], int]:
    paging = soup.select_one(".paging")
    if paging is None:
        raise JeungpyeongContractError(f"library page {requested}: pagination missing")
    numbers: set[int] = set()
    for node in paging.select("strong, a.num"):
        text = _clean(node.get_text(" ", strip=True))
        if not text.isdigit() or int(text) < 1:
            raise JeungpyeongContractError(
                f"library page {requested}: pagination identity changed"
            )
        numbers.add(int(text))
    if not numbers or numbers != set(range(1, max(numbers) + 1)):
        raise JeungpyeongContractError(
            f"library page {requested}: pagination boundary changed"
        )
    current_nodes = paging.select("strong")
    if len(current_nodes) > 1:
        raise JeungpyeongContractError(
            f"library page {requested}: multiple active pagination markers"
        )
    observed: Optional[int] = None
    if current_nodes:
        observed = int(_clean(current_nodes[0].get_text(" ", strip=True)))
    return observed, max(numbers)


def _parse_library_page(soup: BeautifulSoup, requested: int) -> _LibraryPage:
    table = soup.select_one("table.tstyle")
    if table is None:
        raise JeungpyeongContractError(f"library page {requested}: course table missing")
    headings = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headings != _LIBRARY_HEADINGS:
        raise JeungpyeongContractError(f"library page {requested}: headings changed")
    observed, last = _library_pagination(soup, requested)
    rows: list[dict[str, Any]] = []
    for row in table.select("tbody > tr"):
        cells = row.select(":scope > td")
        if len(cells) != 6:
            raise JeungpyeongContractError(f"library page {requested}: malformed row")
        sequence = _clean(cells[0].get_text(" ", strip=True))
        title_link = cells[2].select_one('a[href*="act=lecture_view"]')
        if not sequence.isdigit() or int(sequence) < 1 or title_link is None:
            raise JeungpyeongContractError(
                f"library page {requested}: row identity missing"
            )
        parsed_link = urlparse(urljoin(JEUNGPYEONG_LIBRARY_CANONICAL_URL, title_link.get("href", "")))
        try:
            query = dict(
                parse_qsl(parsed_link.query, keep_blank_values=True, strict_parsing=True)
            )
        except ValueError as exc:
            raise JeungpyeongContractError(
                f"library page {requested}: invalid detail identity"
            ) from exc
        lg_code = _clean(query.get("lgCode"))
        le_code = _clean(query.get("leCode"))
        if (
            not lg_code.isdigit()
            or int(lg_code) < 1
            or not le_code.isdigit()
            or int(le_code) < 1
            or not _query_identity(
                title_link.get("href", ""),
                expected_act="lecture_view",
                lg_code=lg_code,
                le_code=le_code,
            )
        ):
            raise JeungpyeongContractError(
                f"library page {requested}: unsafe detail identity"
            )
        title = _clean(title_link.get_text(" ", strip=True))
        target_parts = [_clean(value) for value in cells[3].stripped_strings]
        if len(target_parts) < 2:
            raise JeungpyeongContractError(
                f"library page {requested}: target/capacity missing"
            )
        capacity = _CAPACITY_TRIPLE.fullmatch(target_parts[-1])
        target = _clean(" ".join(target_parts[:-1]))
        apply_period = _clean(cells[4].get_text(" ", strip=True)).replace(" / ", " ")
        apply_dates = _DOT_DATE.findall(apply_period)
        if not title or not target or capacity is None or len(apply_dates) != 2:
            raise JeungpyeongContractError(
                f"library page {requested}: invalid public fields"
            )
        maximum, online, waitlist = (
            int(part.replace(",", "")) for part in capacity.groups()
        )

        receive_links = cells[5].select('a[href*="act=lecture_receive_form"]')
        result_links = cells[5].select('a[href*="act=lecture_result_view"]')
        if len(receive_links) > 1 or len(result_links) != 1:
            raise JeungpyeongContractError(
                f"library page {requested}: application/result controls changed"
            )
        if result_links and not _query_identity(
            result_links[0].get("href", ""),
            expected_act="lecture_result_view",
            lg_code=lg_code,
            le_code=le_code,
        ):
            raise JeungpyeongContractError(
                f"library page {requested}: result identity changed"
            )
        is_open = bool(receive_links)
        if is_open and not _query_identity(
            receive_links[0].get("href", ""),
            expected_act="lecture_receive_form",
            lg_code=lg_code,
            le_code=le_code,
        ):
            raise JeungpyeongContractError(
                f"library page {requested}: application identity changed"
            )
        if not is_open and "접수마감" not in _clean(cells[5].get_text(" ", strip=True)):
            raise JeungpyeongContractError(
                f"library page {requested}: closed status marker missing"
            )
        rows.append(
            {
                "identity": f"{lg_code}:{le_code}",
                "lg_code": lg_code,
                "le_code": le_code,
                "sequence": int(sequence),
                "list_page": requested,
                "source_scope": _clean(cells[1].get_text(" ", strip=True)),
                "title": title,
                "target": target,
                "capacity_total": maximum,
                "online_capacity": online,
                "waitlist_capacity": waitlist,
                "apply_period": apply_period,
                "is_open": is_open,
                "application_url": (
                    jeungpyeong_library_application_url(lg_code, le_code)
                    if is_open
                    else ""
                ),
            }
        )
    return _LibraryPage(
        requested=requested,
        observed=observed,
        last=last,
        rows=tuple(rows),
    )


def _library_page_signature(page: _LibraryPage) -> str:
    return hashlib.sha256(
        f"{page.last}|{_page_signature(page.rows)}".encode("utf-8")
    ).hexdigest()


def _library_detail_fields(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in soup.select("ul.con03 > li"):
        text = _clean(node.get_text(" ", strip=True))
        if ":" not in text:
            raise JeungpyeongContractError("library detail label changed")
        label, value = text.split(":", 1)
        label = _clean(label)
        value = _clean(value)
        if label in result:
            raise JeungpyeongContractError(f"duplicate library detail label {label}")
        result[label] = value
    return result


def _parse_library_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup
) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    lg_code = _clean(listed.get("lg_code"))
    le_code = _clean(listed.get("le_code"))
    title_node = soup.select_one(".tit > h2")
    table = soup.select_one("table.tstyle")
    if title_node is None or table is None:
        raise JeungpyeongContractError(f"library {identity}: detail shell missing")
    title = _clean(title_node.get_text(" ", strip=True))
    if title != _clean(listed.get("title")):
        raise JeungpyeongContractError(f"library {identity}: title drift")

    public = _library_detail_fields(soup)
    if not {"접수 기간", "강좌 기간", "강좌 장소"} <= set(public):
        raise JeungpyeongContractError(f"library {identity}: public fields missing")
    if _temporal_key(public["접수 기간"]) != _temporal_key(
        listed.get("apply_period")
    ):
        raise JeungpyeongContractError(f"library {identity}: reception period drift")
    start, end = _dot_period(public["강좌 기간"])
    venue = _clean(public["강좌 장소"])
    if not venue:
        raise JeungpyeongContractError(f"library {identity}: venue missing")

    detail_fields = _table_fields(table)
    if not {"대상", "정원", "대상인원", "대기인원", "수강료"} <= set(detail_fields):
        raise JeungpyeongContractError(f"library {identity}: detail table fields missing")
    if _compact(detail_fields["대상"]) != _compact(listed.get("target")):
        raise JeungpyeongContractError(f"library {identity}: target drift")

    def people(field: str) -> int:
        match = re.fullmatch(r"([\d,]+)\s*명", _clean(detail_fields[field]))
        if not match:
            raise JeungpyeongContractError(
                f"library {identity}: invalid {field} value"
            )
        return int(match.group(1).replace(",", ""))

    maximum = people("정원")
    target_capacity = people("대상인원")
    waitlist_capacity = people("대기인원")
    if (
        maximum != int(listed["capacity_total"])
        or target_capacity != int(listed["online_capacity"])
        or waitlist_capacity != int(listed["waitlist_capacity"])
    ):
        raise JeungpyeongContractError(f"library {identity}: capacity drift")
    current = people("현재 접수인원") if "현재 접수인원" in detail_fields else 0

    controls = soup.select('a[href*="act=lecture_receive_form"]')
    if len(controls) != (1 if listed["is_open"] else 0):
        raise JeungpyeongContractError(
            f"library {identity}: detail application/status drift"
        )
    if controls and not _query_identity(
        controls[0].get("href", ""),
        expected_act="lecture_receive_form",
        lg_code=lg_code,
        le_code=le_code,
    ):
        raise JeungpyeongContractError(
            f"library {identity}: detail application identity changed"
        )

    source_category_match = re.match(r"\[([^\]]+)\]", title)
    source_category = _clean(source_category_match.group(1)) if source_category_match else ""
    is_open = bool(listed["is_open"])
    schedule = _clean(public.get("강좌 일시"))
    provider_course_id = f"{JEUNGPYEONG_LIBRARY_PROVIDER}:lecture:{identity}"
    return {
        "provider": JEUNGPYEONG_LIBRARY_PROVIDER,
        "provider_course_id": provider_course_id,
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": venue,
        "branch_code": _branch_code(JEUNGPYEONG_LIBRARY_PROVIDER, venue),
        "preserve_branch": True,
        "category": "도서관 문화프로그램",
        "program_type": "교육",
        "raw_url": jeungpyeong_library_detail_url(lg_code, le_code),
        "application_url": _clean(listed["application_url"]),
        "application_type": "ONLINE_RESERVATION" if is_open else "INFO_ONLY",
        "application_method": "온라인" if is_open else "",
        "application_methods": ["온라인"] if is_open else [],
        "reservation_available": is_open,
        "status": "OPEN" if is_open else "CLOSED",
        "fee": _clean(detail_fields["수강료"]),
        "period": _clean(public["강좌 기간"]),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": _clean(public["접수 기간"]),
        "schedule_raw": schedule,
        "capacity": f"{maximum}명",
        "capacity_current": current,
        "capacity_total": maximum,
        "waitlist_current": 0,
        "target": _clean(detail_fields["대상"]),
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": JEUNGPYEONG_LIBRARY_PARSER,
        "municipality_code": JEUNGPYEONG_MUNICIPALITY_CODE,
        "municipality_full_name": JEUNGPYEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": int(listed["list_page"]),
            "source_sequence": int(listed["sequence"]),
            "source_status": "신청하기" if is_open else "접수마감",
            "source_category": source_category,
            "source_scope": _clean(listed["source_scope"]),
            "source_venue": venue,
            "source_target": _clean(detail_fields["대상"]),
            "source_period": _clean(public["강좌 기간"]),
            "source_apply_period": _clean(public["접수 기간"]),
            "source_schedule": schedule,
            "source_capacity_current": current,
            "source_capacity_total": maximum,
            "source_online_capacity": int(listed["online_capacity"]),
            "source_waitlist_capacity": int(listed["waitlist_capacity"]),
            "detail_verified": True,
            "application_control_present": is_open,
            "result_control_present_not_requested": True,
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden detail/PII key persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail description persisted")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta(kind: str) -> dict[str, Any]:
    if kind == "lifelong":
        provider = JEUNGPYEONG_LIFELONG_PROVIDER
        candidate = JEUNGPYEONG_LIFELONG_CANDIDATE_ID
        canonical = JEUNGPYEONG_LIFELONG_CANONICAL_URL
    elif kind == "library":
        provider = JEUNGPYEONG_LIBRARY_PROVIDER
        candidate = JEUNGPYEONG_LIBRARY_CANDIDATE_ID
        canonical = JEUNGPYEONG_LIBRARY_CANONICAL_URL
    else:
        provider = ""
        candidate = ""
        canonical = ""
    return {
        "provider": provider,
        "canonical_candidate_id": candidate,
        "canonical_url": canonical,
        "municipality_code": JEUNGPYEONG_MUNICIPALITY_CODE,
        "municipality_name": JEUNGPYEONG_MUNICIPALITY_NAME,
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "sentinel_requests": 0,
        "boundary_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_candidate_count": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "archived_rows_skipped_before_detail": 0,
        "identity_duplicate_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "forbidden_applicant_endpoint_requests": 0,
        "pii_payload_persisted": False,
        "pii_fields_never_persisted": list(JEUNGPYEONG_PII_FIELDS_NEVER_PERSISTED),
        "discovery_audit": JEUNGPYEONG_DISCOVERY_AUDIT,
    }


def _finalize(
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
    *,
    dedupe_rows: Optional[DedupeRows],
) -> list[dict[str, Any]]:
    errors: list[str] = []
    for row in rows:
        errors.extend(_privacy_errors(row))
    if errors:
        raise JeungpyeongContractError("; ".join(dict.fromkeys(errors)))
    deduper = dedupe_rows or _dedupe_default
    try:
        result = list(deduper(rows))
    except Exception as exc:
        raise JeungpyeongContractError(
            f"dedupe failed: {type(exc).__name__}: {_clean(exc)}"
        ) from exc
    if len(result) != len(rows):
        raise JeungpyeongContractError(
            f"dedupe changed official identity cardinality {len(rows)} to {len(result)}"
        )
    meta.update(
        {
            "branch_counts": dict(
                sorted(Counter(_clean(row.get("branch")) for row in result).items())
            ),
            "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
            "application_control_count": sum(
                bool(row.get("raw_fields", {}).get("application_control_present"))
                for row in result
            ),
            "returned_count": len(result),
            "pagination_complete": True,
            "details_complete": True,
            "application_controls_complete": True,
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not result,
            "no_current_reason": (
                "all official Jeungpyeong education records have ended" if not result else ""
            ),
        }
    )
    return result


def _collect_lifelong(
    *,
    cutoff: date,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    max_workers: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
    dedupe_rows: Optional[DedupeRows],
    meta: dict[str, Any],
) -> list[dict[str, Any]]:
    catalogue_pages: dict[str, dict[int, _LifelongPage]] = {}
    all_listed: list[dict[str, Any]] = []
    total_data_pages = 0
    catalogue_counts: dict[str, int] = {}
    catalogue_page_counts: dict[str, int] = {}

    for catalogue in JEUNGPYEONG_LIFELONG_CATALOGUES:
        first_url = jeungpyeong_lifelong_list_url(catalogue, 1)
        first_soup = _fetch_many(
            [((catalogue, "first", 1), first_url)],
            timeout=timeout,
            max_workers=max_workers,
            expected_host=JEUNGPYEONG_LIFELONG_HOST,
            session_factory=session_factory,
            fetcher=fetcher,
        )[(catalogue, "first", 1)]
        first = _parse_lifelong_page(first_soup, catalogue, 1)
        meta["list_requests"] += 1
        meta["pages"] += 1
        total_data_pages += first.last
        if total_data_pages > max_pages:
            meta["source_cap_reached"] = True
            raise JeungpyeongContractError(
                f"max_pages cap allows {max_pages} of {total_data_pages} catalogue pages"
            )
        remaining = [
            ((catalogue, "data", page), jeungpyeong_lifelong_list_url(catalogue, page))
            for page in range(2, first.last + 1)
        ]
        remaining.append(
            (
                (catalogue, "sentinel", first.last + 1),
                jeungpyeong_lifelong_list_url(catalogue, first.last + 1),
            )
        )
        boundaries = sorted({1, first.last})
        remaining.extend(
            (
                (catalogue, "recheck", page),
                jeungpyeong_lifelong_list_url(catalogue, page),
            )
            for page in boundaries
        )
        fetched = _fetch_many(
            remaining,
            timeout=timeout,
            max_workers=max_workers,
            expected_host=JEUNGPYEONG_LIFELONG_HOST,
            session_factory=session_factory,
            fetcher=fetcher,
        )
        meta["list_requests"] += len(remaining)
        meta["pages"] += len(remaining)
        meta["sentinel_requests"] += 1
        meta["boundary_rechecks"] += len(boundaries)
        pages: dict[int, _LifelongPage] = {1: first}
        for page in range(2, first.last + 1):
            pages[page] = _parse_lifelong_page(
                fetched[(catalogue, "data", page)], catalogue, page
            )
        sentinel = _parse_lifelong_page(
            fetched[(catalogue, "sentinel", first.last + 1)],
            catalogue,
            first.last + 1,
        )
        if (
            sentinel.total != first.total
            or sentinel.last != first.last
            or sentinel.rows
            or not sentinel.empty_marker
        ):
            raise JeungpyeongContractError(
                f"{catalogue}: immediate post-last sentinel changed"
            )
        for page in pages.values():
            if page.total != first.total or page.last != first.last or page.empty_marker:
                if first.total == 0 and page.requested == 1 and page.empty_marker:
                    continue
                raise JeungpyeongContractError(
                    f"{catalogue}: declared catalogue boundary changed"
                )
        for page in boundaries:
            rechecked = _parse_lifelong_page(
                fetched[(catalogue, "recheck", page)], catalogue, page
            )
            if _lifelong_page_signature(rechecked) != _lifelong_page_signature(pages[page]):
                raise JeungpyeongContractError(
                    f"{catalogue} page {page}: boundary stability recheck changed"
                )
        listed = [row for page in range(1, first.last + 1) for row in pages[page].rows]
        if len(listed) != first.total:
            raise JeungpyeongContractError(f"{catalogue}: declared total changed")
        if [row["sequence"] for row in listed] != list(range(first.total, 0, -1)):
            raise JeungpyeongContractError(f"{catalogue}: source sequence coverage changed")
        catalogue_pages[catalogue] = pages
        catalogue_counts[catalogue] = first.total
        catalogue_page_counts[catalogue] = first.last
        all_listed.extend(listed)

    identities = [_clean(row["identity"]) for row in all_listed]
    duplicate_count = len(identities) - len(set(identities))
    if duplicate_count:
        raise JeungpyeongContractError(
            f"{duplicate_count} duplicate courseNo identities across catalogues"
        )
    current = [row for row in all_listed if row["end"] >= cutoff]
    if len(current) > detail_limit:
        meta["source_cap_reached"] = True
        raise JeungpyeongContractError(
            f"detail_limit cap allows {detail_limit} of {len(current)} current details"
        )
    meta.update(
        {
            "required_list_requests": meta["list_requests"],
            "data_pages": total_data_pages,
            "source_total": len(all_listed),
            "source_rows": len(all_listed),
            "catalogue_counts": catalogue_counts,
            "catalogue_page_counts": catalogue_page_counts,
            "identity_duplicate_count": duplicate_count,
            "current_candidate_count": len(current),
            "expired_count": len(all_listed) - len(current),
            "archived_rows_skipped_before_detail": len(all_listed) - len(current),
            "source_status_counts": dict(
                Counter(_clean(row["source_status"]) for row in all_listed)
            ),
            "pagination_complete": True,
            "detail_attempts": len(current),
        }
    )

    detail_items = [
        (
            _clean(row["identity"]),
            jeungpyeong_lifelong_detail_url(_clean(row["catalogue"]), row["identity"]),
        )
        for row in current
    ]
    detail_soups = _fetch_many(
        detail_items,
        timeout=timeout,
        max_workers=max_workers,
        expected_host=JEUNGPYEONG_LIFELONG_HOST,
        session_factory=session_factory,
        fetcher=fetcher,
    )
    meta["pages"] += len(detail_items)
    detailed = [
        _parse_lifelong_detail(row, detail_soups[_clean(row["identity"])], cutoff)
        for row in current
    ]
    meta.update(
        {
            "detail_pages": len(detailed),
            "current_source_count": len(detailed),
        }
    )
    return _finalize(detailed, meta, dedupe_rows=dedupe_rows)


def _collect_library(
    *,
    cutoff: date,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    max_workers: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
    dedupe_rows: Optional[DedupeRows],
    meta: dict[str, Any],
) -> list[dict[str, Any]]:
    first_soup = _fetch_many(
        [(1, jeungpyeong_library_list_url(1))],
        timeout=timeout,
        max_workers=max_workers,
        expected_host=JEUNGPYEONG_LIBRARY_HOST,
        session_factory=session_factory,
        fetcher=fetcher,
    )[1]
    first = _parse_library_page(first_soup, 1)
    meta["list_requests"] = 1
    meta["pages"] = 1
    if first.observed != 1:
        raise JeungpyeongContractError("library first-page identity changed")
    if first.last > max_pages:
        meta["source_cap_reached"] = True
        raise JeungpyeongContractError(
            f"max_pages cap allows {max_pages} of {first.last} library pages"
        )
    remaining = [
        (("data", page), jeungpyeong_library_list_url(page))
        for page in range(2, first.last + 1)
    ]
    remaining.append(
        (("sentinel", first.last + 1), jeungpyeong_library_list_url(first.last + 1))
    )
    boundaries = sorted({1, first.last})
    remaining.extend(
        (("recheck", page), jeungpyeong_library_list_url(page)) for page in boundaries
    )
    fetched = _fetch_many(
        remaining,
        timeout=timeout,
        max_workers=max_workers,
        expected_host=JEUNGPYEONG_LIBRARY_HOST,
        session_factory=session_factory,
        fetcher=fetcher,
    )
    meta["list_requests"] += len(remaining)
    meta["pages"] += len(remaining)
    meta["sentinel_requests"] = 1
    meta["boundary_rechecks"] = len(boundaries)
    pages: dict[int, _LibraryPage] = {1: first}
    for page in range(2, first.last + 1):
        pages[page] = _parse_library_page(fetched[("data", page)], page)
        if pages[page].observed != page or pages[page].last != first.last:
            raise JeungpyeongContractError(
                f"library page {page}: declared boundary changed"
            )
    sentinel = _parse_library_page(
        fetched[("sentinel", first.last + 1)], first.last + 1
    )
    if sentinel.rows or sentinel.observed is not None or sentinel.last != first.last:
        raise JeungpyeongContractError("library immediate post-last sentinel changed")
    for page in boundaries:
        rechecked = _parse_library_page(fetched[("recheck", page)], page)
        if (
            rechecked.observed != page
            or _library_page_signature(rechecked) != _library_page_signature(pages[page])
        ):
            raise JeungpyeongContractError(
                f"library page {page}: boundary stability recheck changed"
            )

    listed = [row for page in range(1, first.last + 1) for row in pages[page].rows]
    if not listed:
        raise JeungpyeongContractError("library catalogue has no declared-total marker")
    total = int(listed[0]["sequence"])
    expected_last = max(1, math.ceil(total / JEUNGPYEONG_LIBRARY_PAGE_SIZE))
    if first.last != expected_last or len(listed) != total:
        raise JeungpyeongContractError("library sequence total/pagination changed")
    if [row["sequence"] for row in listed] != list(range(total, 0, -1)):
        raise JeungpyeongContractError("library source sequence coverage changed")
    identities = [_clean(row["identity"]) for row in listed]
    duplicate_count = len(identities) - len(set(identities))
    if duplicate_count:
        raise JeungpyeongContractError(
            f"{duplicate_count} duplicate library lecture identities"
        )
    if len(listed) > detail_limit:
        meta["source_cap_reached"] = True
        raise JeungpyeongContractError(
            f"detail_limit cap allows {detail_limit} of {len(listed)} library details"
        )
    meta.update(
        {
            "required_list_requests": meta["list_requests"],
            "data_pages": first.last,
            "source_total": total,
            "source_rows": len(listed),
            "identity_duplicate_count": duplicate_count,
            "pagination_complete": True,
            "detail_attempts": len(listed),
        }
    )

    detail_items = [
        (
            _clean(row["identity"]),
            jeungpyeong_library_detail_url(row["lg_code"], row["le_code"]),
        )
        for row in listed
    ]
    detail_soups = _fetch_many(
        detail_items,
        timeout=timeout,
        max_workers=max_workers,
        expected_host=JEUNGPYEONG_LIBRARY_HOST,
        session_factory=session_factory,
        fetcher=fetcher,
    )
    meta["pages"] += len(detail_items)
    all_detailed = [
        _parse_library_detail(row, detail_soups[_clean(row["identity"])])
        for row in listed
    ]
    current = [
        row
        for row in all_detailed
        if date.fromisoformat(_clean(row["end_date"])) >= cutoff
    ]
    meta.update(
        {
            "detail_pages": len(all_detailed),
            "current_candidate_count": len(current),
            "current_source_count": len(current),
            "expired_count": len(all_detailed) - len(current),
            "archived_rows_skipped_before_detail": 0,
            "source_status_counts": dict(
                Counter(
                    "신청하기" if row["is_open"] else "접수마감" for row in listed
                )
            ),
        }
    )
    return _finalize(current, meta, dedupe_rows=dedupe_rows)


def collect_jeungpyeong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    max_workers: int = JEUNGPYEONG_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one exact Jeungpyeong HTTPS education owner atomically."""

    kind = _target_kind(target)
    parser = (
        JEUNGPYEONG_LIFELONG_PARSER
        if kind == "lifelong"
        else JEUNGPYEONG_LIBRARY_PARSER
        if kind == "library"
        else JEUNGPYEONG_PARSER
    )
    meta = _base_meta(kind)
    if not kind:
        meta["configured_collection_error"] = (
            "target does not match an audited Jeungpyeong HTTPS education owner"
        )
        return [], parser, meta
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages < 1
        or isinstance(detail_limit, bool)
        or not isinstance(detail_limit, int)
        or detail_limit < 0
        or isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
        or max_workers > 32
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], parser, meta
    try:
        cutoff = _today(today)
        factory = session_factory or _default_session_factory
        current_fetcher = fetcher or _default_fetcher
        if kind == "lifelong":
            rows = _collect_lifelong(
                cutoff=cutoff,
                timeout=timeout,
                max_pages=max_pages,
                detail_limit=detail_limit,
                max_workers=max_workers,
                session_factory=factory,
                fetcher=current_fetcher,
                dedupe_rows=dedupe_rows,
                meta=meta,
            )
        else:
            rows = _collect_library(
                cutoff=cutoff,
                timeout=timeout,
                max_pages=max_pages,
                detail_limit=detail_limit,
                max_workers=max_workers,
                session_factory=factory,
                fetcher=current_fetcher,
                dedupe_rows=dedupe_rows,
                meta=meta,
            )
        return rows, parser, meta
    except Exception as exc:
        meta.update(
            {
                "detail_errors": max(
                    int(meta.get("detail_errors") or 0),
                    1 if int(meta.get("detail_attempts") or 0) else 0,
                ),
                "pagination_complete": False,
                "details_complete": False,
                "application_controls_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "returned_count": 0,
                "configured_collection_error": (
                    f"{type(exc).__name__}: {_clean(exc)}"
                ),
            }
        )
        return [], parser, meta


collect = collect_jeungpyeong_education


__all__ = [
    "JEUNGPYEONG_DISCOVERY_AUDIT",
    "JEUNGPYEONG_DOAN_NOTICE_URL",
    "JEUNGPYEONG_EUP_NOTICE_URL",
    "JEUNGPYEONG_LIBRARY_CANDIDATE_ID",
    "JEUNGPYEONG_LIBRARY_CANONICAL_URL",
    "JEUNGPYEONG_LIBRARY_HOST",
    "JEUNGPYEONG_LIBRARY_PARSER",
    "JEUNGPYEONG_LIBRARY_PROVIDER",
    "JEUNGPYEONG_LIFELONG_CANDIDATE_ID",
    "JEUNGPYEONG_LIFELONG_CANONICAL_URL",
    "JEUNGPYEONG_LIFELONG_CATALOGUES",
    "JEUNGPYEONG_LIFELONG_HOST",
    "JEUNGPYEONG_LIFELONG_LANDING_URL",
    "JEUNGPYEONG_LIFELONG_PARSER",
    "JEUNGPYEONG_LIFELONG_PROVIDER",
    "JEUNGPYEONG_MUNICIPALITY_CODE",
    "JEUNGPYEONG_MUNICIPALITY_NAME",
    "JEUNGPYEONG_PARSER",
    "JEUNGPYEONG_PII_FIELDS_NEVER_PERSISTED",
    "JEUNGPYEONG_RESIDENT_LEDGER_URL",
    "JEUNGPYEONG_YOUTH_CANDIDATE_ID",
    "JEUNGPYEONG_YOUTH_PROVIDER",
    "JEUNGPYEONG_YOUTH_URL",
    "JeungpyeongContractError",
    "collect",
    "collect_jeungpyeong_education",
    "is_jeungpyeong_education_target",
    "is_jeungpyeong_library_target",
    "is_jeungpyeong_lifelong_target",
    "is_target",
    "jeungpyeong_library_application_url",
    "jeungpyeong_library_detail_url",
    "jeungpyeong_library_list_url",
    "jeungpyeong_lifelong_application_url",
    "jeungpyeong_lifelong_detail_url",
    "jeungpyeong_lifelong_list_url",
]
