"""Atomic collector for Daejeon Daedeok-gu's official education catalogues.

Daedeok-gu publishes one resident-autonomy catalogue and four disjoint
lifelong-learning catalogues.  All five are owned by the district and are
therefore collected as one provider snapshot.  The separate ``delivery``
``listInfo`` screen is exhaustively audited, but it is a roster of reusable
course templates rather than dated offerings and is never emitted here.

The collector fails closed unless every advertised list page, the immediate
post-last sentinel, a stable page-one recheck, and every current/future POST
``detail1`` response pass their contracts.  Instructor names, contacts,
applicant counts, arbitrary descriptions, attachments, and source HTML are
not persisted.
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
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


DAEJEON_DAEDEOK_PROVIDER = "MUNI_WWW_DAEDEOK_GO_KR_360B9B7C"
DAEJEON_DAEDEOK_ALIAS_PROVIDERS = frozenset(
    {
        "MUNI_WWW_DAEDEOK_GO_KR_F1987640",
        "MUNI_LLL_DAEDEOK_GO_KR_3C0F5E13",
    }
)
DAEJEON_DAEDEOK_MUNICIPALITY_CODE = "3023000000"
DAEJEON_DAEDEOK_MUNICIPALITY_NAME = "대전광역시 대덕구"
DAEJEON_DAEDEOK_PAGE_SIZE = 10
DAEJEON_DAEDEOK_FETCH_ATTEMPTS = 2
DAEJEON_DAEDEOK_MAX_WORKERS = 10
DAEJEON_DAEDEOK_MAX_HTML_BYTES = 4_000_000
DAEJEON_DAEDEOK_DELIVERY_TEMPLATE_STATUSES = frozenset(
    {"신청 마감", "신청 대기중"}
)
DAEJEON_DAEDEOK_PARSER = (
    "daejeon_daedeok_resident_plus_four_lifelong_leaves+post_detail1+"
    "delivery_listinfo_audit+all_pages+empty_sentinels+stable_rechecks+"
    "current_details+pii_allowlist"
)
DAEJEON_DAEDEOK_OWNERSHIP_SCOPE = (
    "daedeok_official_resident_autonomy_and_four_lifelong_education_catalogues"
)


@dataclass(frozen=True)
class DaedeokSource:
    key: str
    kind: str
    label: str
    host: str
    path: str
    menu_code: str
    list_title: str
    detail_title: str
    headers: tuple[str, ...]

    @property
    def list_url(self) -> str:
        pairs = [("mnucd", self.menu_code)]
        if self.kind == "delivery":
            pairs.append(("bmode", "listInfo"))
        return f"https://{self.host}{self.path}?{urlencode(pairs)}"


_RESIDENT_HEADERS = (
    "번호",
    "기관명",
    "프로그램명",
    "모집기간",
    "운영기간",
    "요일/시간",
    "인원",
    "대상",
    "수강료",
    "진행",
)
_LIFELONG_HEADERS = (
    "번호",
    "프로그램명",
    "모집기간",
    "운영기간",
    "요일/시간",
    "인원",
    "대상",
    "수강료",
    "진행",
)
_LIFELONG_VENUE_HEADERS = (
    "번호",
    "프로그램명",
    "모집기간",
    "운영기간",
    "요일/시간",
    "장소",
    "모집현황/정원",
    "대상",
    "수강료",
    "진행",
)
_DELIVERY_HEADERS = ("번호", "분야", "강좌명", "수강신청")

DAEJEON_DAEDEOK_EDUCATION_SOURCES: tuple[DaedeokSource, ...] = (
    DaedeokSource(
        "resident",
        "resident",
        "주민자치프로그램",
        "edu.daedeok.go.kr",
        "/damoa/contents/dms/edu/02/edu.02.001.motion",
        "MENU0100010",
        "프로그램 수강신청- 대덕구주민자치프로그램",
        "프로그램 수강신청 : 상세 화면- 대덕구주민자치프로그램",
        _RESIDENT_HEADERS,
    ),
    DaedeokSource(
        "lifelong_01",
        "lifelong",
        "평생학습 프로그램",
        "lll.daedeok.go.kr",
        "/lms/damoa/contents/dms/edu/01/edu.01.001.motion",
        "MENU0100023",
        "평생학습 프로그램 : 목록 화면 - 대덕구평생학습",
        "평생학습 프로그램 : 상세 화면 - 대덕구평생학습",
        _LIFELONG_HEADERS,
    ),
    DaedeokSource(
        "lifelong_05",
        "lifelong",
        "대덕미래아카데미",
        "lll.daedeok.go.kr",
        "/lms/damoa/contents/dms/edu/05/edu.05.001.motion",
        "MENU0100021",
        "대덕미래아카데미 - 대덕구평생학습",
        "대덕미래아카데미 : 상세 화면 - 대덕구평생학습",
        _LIFELONG_VENUE_HEADERS,
    ),
    DaedeokSource(
        "lifelong_07",
        "lifelong",
        "프로그램신청",
        "lll.daedeok.go.kr",
        "/lms/damoa/contents/dms/edu/07/edu.07.001.motion",
        "MENU0100097",
        "프로그램신청 - 대덕구평생학습",
        "프로그램신청 : 상세 화면 - 대덕구평생학습",
        _LIFELONG_HEADERS,
    ),
    DaedeokSource(
        "lifelong_08",
        "lifelong",
        "장애인평생학습도시",
        "lll.daedeok.go.kr",
        "/lms/damoa/contents/dms/edu/08/edu.08.001.motion",
        "MENU0100085",
        "장애인평생학습도시 - 대덕구평생학습",
        "장애인평생학습도시 : 상세 화면 - 대덕구평생학습",
        _LIFELONG_HEADERS,
    ),
)
DAEJEON_DAEDEOK_DELIVERY_SOURCE = DaedeokSource(
    "delivery_info",
    "delivery",
    "배달강좌 정보",
    "lll.daedeok.go.kr",
    "/lms/damoa/contents/dms/delivery/01/delivery.01.001.motion",
    "MENU0100057",
    "배달강좌 - 대덕구평생학습",
    "",
    _DELIVERY_HEADERS,
)
DAEJEON_DAEDEOK_SOURCES = (
    *DAEJEON_DAEDEOK_EDUCATION_SOURCES,
    DAEJEON_DAEDEOK_DELIVERY_SOURCE,
)
DAEJEON_DAEDEOK_SOURCE_BY_KEY = {
    source.key: source for source in DAEJEON_DAEDEOK_SOURCES
}
DAEJEON_DAEDEOK_CANONICAL_URL = DAEJEON_DAEDEOK_EDUCATION_SOURCES[0].list_url
DAEJEON_DAEDEOK_ALIAS_URLS = tuple(
    source.list_url for source in DAEJEON_DAEDEOK_SOURCES[1:]
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
Poster = Callable[[Any, str, Mapping[str, str], int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE4_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_DATE2_RE = re.compile(
    r"(?<!\d)(\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_RESIDENT_ID_RE = re.compile(
    r"^fn_egov_select1\('([0-9]+)','(ORD_[0-9]{12})','(35)','([0-9]{7})'\);"
    r"\s*return false;$"
)
_LIFELONG_ID_RE = re.compile(
    r"^fn_egov_select1\(document\.getElementById\(['\"]listForm['\"]\),"
    r"\s*['\"]((?:LEC_[0-9]{12})|(?:[0-9]+))['\"],"
    r"\s*['\"](ORD_[0-9]{12})['\"],\s*['\"](35)['\"],"
    r"\s*['\"]([0-9]{7})['\"]\);\s*return false;$"
)
_DELIVERY_ID_RE = re.compile(
    r"^return fn_egov_selectInfo\('([0-9]+)','list'\)$"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]+\d{3,4}[\s.-]+\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_SOURCE_STATUS = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "폐강": "CANCELLED",
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "source_kind",
        "source_key",
        "identity",
        "menu_code",
        "list_page",
        "source_status",
        "source_period",
        "source_application_period",
        "source_schedule",
        "source_target",
        "source_capacity",
        "source_fee",
        "list_institution",
        "education_institution",
        "education_location",
        "education_location_source",
        "application_control_present",
        "application_control_contract",
        "detail_verified",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "contact",
        "contact_name",
        "phone",
        "email",
        "applicant_count",
        "application_count",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)


@dataclass
class _ListPage:
    rows: list[dict[str, Any]]
    total: int
    last: int
    errors: list[str]
    archived_missing_apply_start: int = 0
    application_period_anomalies: int = 0
    current_application_period_anomalies: int = 0
    archived_education_period_anomalies: int = 0


@dataclass
class _DeliveryPage:
    rows: list[dict[str, str]]
    total: int
    last: int
    errors: list[str]
    real_offering_count: int = 0


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
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise ValueError("today must be an ISO date") from exc


def _canonical_compare_url(value: Any) -> str:
    raw = _clean(value)
    parsed = urlparse(raw)
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname not in {"edu.daedeok.go.kr", "lll.daedeok.go.kr"}
        or parsed.fragment
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        return ""
    pairs = sorted(parse_qsl(parsed.query, keep_blank_values=True))
    return f"https://{parsed.hostname}{parsed.path}" + (
        f"?{urlencode(pairs)}" if pairs else ""
    )


def is_daejeon_daedeok_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == DAEJEON_DAEDEOK_PROVIDER
        and _canonical_compare_url(_target_value(target, "url"))
        == _canonical_compare_url(DAEJEON_DAEDEOK_CANONICAL_URL)
    )


def is_daejeon_daedeok_owned_alias_target(target: Any) -> bool:
    provider = _clean(_target_value(target, "provider"))
    compared = _canonical_compare_url(_target_value(target, "url"))
    return bool(
        provider in DAEJEON_DAEDEOK_ALIAS_PROVIDERS
        or compared
        in {_canonical_compare_url(url) for url in DAEJEON_DAEDEOK_ALIAS_URLS}
    )


def daejeon_daedeok_list_url(source_key: Any, page: Any = 1) -> str:
    source = DAEJEON_DAEDEOK_SOURCE_BY_KEY.get(_clean(source_key))
    if source is None or isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return ""
    pairs: list[tuple[str, str]] = [("mnucd", source.menu_code)]
    if source.kind == "delivery":
        pairs.append(("bmode", "listInfo"))
    if page != 1:
        pairs.append(("pageIndex", str(page)))
    return f"https://{source.host}{source.path}?{urlencode(pairs)}"


def daejeon_daedeok_detail_url(
    source_key: Any,
    identity: Any,
    order_code: Any,
    sido_code: Any,
    local_code: Any,
) -> str:
    source = DAEJEON_DAEDEOK_SOURCE_BY_KEY.get(_clean(source_key))
    values = tuple(
        _clean(value) for value in (identity, order_code, sido_code, local_code)
    )
    if source is None or source.kind == "delivery" or not all(values):
        return ""
    identity_value, order_value, sido_value, local_value = values
    pattern = r"[0-9]+" if source.kind == "resident" else r"(?:LEC_[0-9]{12}|[0-9]+)"
    if (
        re.fullmatch(pattern, identity_value) is None
        or re.fullmatch(r"ORD_[0-9]{12}", order_value) is None
        or sido_value != "35"
        or re.fullmatch(r"[0-9]{7}", local_value) is None
    ):
        return ""
    pairs = [
        ("mnucd", source.menu_code),
        ("bmode", "detail1"),
        ("lecId", identity_value),
        ("ordCd", order_value),
        ("ordSidoCd", sido_value),
        ("ordLocalCd", local_value),
    ]
    return f"https://{source.host}{source.path}?{urlencode(pairs)}"


def _detail_payload(row: Mapping[str, Any]) -> dict[str, str]:
    raw = row["raw_fields"]
    identity = raw["identity_parts"]
    return {
        "mnucd": _clean(raw["menu_code"]),
        "searchLecDivArray": "",
        "bmode": "detail1",
        "pageIndex": str(raw["list_page"]),
        "lecId": identity[0],
        "ordCd": identity[1],
        "ordSidoCd": identity[2],
        "ordLocalCd": identity[3],
        "searchCondition": "1",
        "searchKeyword": "",
    }


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://www.daedeok.go.kr/)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _validated_response(response: Any, requested_url: str) -> BeautifulSoup:
    response.raise_for_status()
    requested = urlparse(requested_url)
    final = urlparse(_clean(getattr(response, "url", requested_url)))
    if final.scheme.lower() != "https" or final.hostname != requested.hostname:
        raise ValueError("response left the requested official HTTPS host")
    content_type = _clean(response.headers.get("Content-Type")).lower()
    if "html" not in content_type:
        raise ValueError("response is not HTML")
    content = response.content
    if len(content) > DAEJEON_DAEDEOK_MAX_HTML_BYTES:
        raise ValueError("HTML response exceeded the bounded size limit")
    return BeautifulSoup(content, "html.parser")


def _default_fetcher(session: Any, url: str, timeout: int) -> BeautifulSoup:
    return _validated_response(
        session.get(url, timeout=timeout, allow_redirects=True), url
    )


def _default_poster(
    session: Any, url: str, data: Mapping[str, str], timeout: int
) -> BeautifulSoup:
    return _validated_response(
        session.post(url, data=dict(data), timeout=timeout, allow_redirects=True), url
    )


def _close_quietly(value: Any) -> None:
    try:
        close = getattr(value, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > DAEJEON_DAEDEOK_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > DAEJEON_DAEDEOK_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return _coerce_soup(bytes(content))
    raise TypeError("request hook must return HTML, bytes, a response, or BeautifulSoup")


def _fetch_many(
    items: Iterable[tuple[Any, str, Optional[Mapping[str, str]], Callable[[BeautifulSoup], Any]]],
    *,
    fetcher: Fetcher,
    poster: Poster,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, Any], list[str]]:
    tasks = list(items)
    if not tasks:
        return {}, []

    def worker(
        key: Any,
        url: str,
        data: Optional[Mapping[str, str]],
        parser: Callable[[BeautifulSoup], Any],
    ) -> tuple[Any, Any]:
        last_error: Optional[Exception] = None
        for _attempt in range(DAEJEON_DAEDEOK_FETCH_ATTEMPTS):
            session = session_factory()
            try:
                value = (
                    fetcher(session, url, timeout)
                    if data is None
                    else poster(session, url, data, timeout)
                )
                return key, parser(_coerce_soup(value))
            except Exception as exc:
                last_error = exc
            finally:
                _close_quietly(session)
        raise RuntimeError(_clean(last_error))

    results: dict[Any, Any] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        futures = {
            executor.submit(worker, key, url, data, parser): key
            for key, url, data, parser in tasks
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                result_key, value = future.result()
                results[result_key] = value
            except Exception as exc:
                errors.append(f"{key}: {_clean(exc)}")
    return results, errors


def _dates(value: Any, *, short_year: bool = False) -> list[date]:
    matches = (_DATE2_RE if short_year else _DATE4_RE).findall(_clean(value))
    parsed: list[date] = []
    for year, month, day_value in matches:
        full_year = 2000 + int(year) if short_year else int(year)
        try:
            parsed.append(date(full_year, int(month), int(day_value)))
        except ValueError as exc:
            raise ValueError("invalid calendar date") from exc
    return parsed


def _date_pair(
    value: Any,
    field: str,
    *,
    short_year: bool = False,
    allow_reversed: bool = False,
) -> tuple[date, date]:
    parsed = _dates(value, short_year=short_year)
    if len(parsed) != 2:
        raise ValueError(f"{field}: expected exactly two dates")
    if parsed[1] < parsed[0] and not allow_reversed:
        raise ValueError(f"{field}: reversed date range")
    return parsed[0], parsed[1]


def _integer(value: Any, field: str, *, last: bool = False) -> int:
    matches = re.findall(r"[0-9][0-9,]*", _clean(value))
    if not matches:
        raise ValueError(f"{field}: integer missing")
    return int((matches[-1] if last else matches[0]).replace(",", ""))


def _fee(value: Any) -> tuple[str, int]:
    raw = _clean(value)
    if not raw:
        raise ValueError("fee missing")
    if "무료" in raw:
        return raw, 0
    match = re.search(r"([0-9][0-9,]*)\s*원", raw)
    if match is None:
        raise ValueError("fee format changed")
    return raw, int(match.group(1).replace(",", ""))


def _one_table(soup: BeautifulSoup, source: DaedeokSource) -> Any:
    selector = "table.table" if source.kind == "resident" else "table.simple"
    tables = [table for table in soup.select(selector) if table.select("thead th")]
    return tables[0] if len(tables) == 1 else None


def _hidden(form: Any, name: str) -> tuple[int, str]:
    nodes = form.select(f'input[name="{name}"]') if form is not None else []
    return len(nodes), _clean(nodes[0].get("value")) if len(nodes) == 1 else ""


def _counter(soup: BeautifulSoup, page: int, page_size: int) -> tuple[int, int, list[str]]:
    nodes = soup.select("span.counter")
    errors: list[str] = []
    text = _clean(nodes[0].get_text(" ", strip=True)) if len(nodes) == 1 else ""
    match = re.match(r"^Total\s+([0-9,]+)\s*[｜|]\s*([0-9]+)\s*/\s*([0-9]+)(?:\s+.*)?$", text)
    if match is None:
        return 0, 1, ["advertised total/page counter changed"]
    total = int(match.group(1).replace(",", ""))
    response_page = int(match.group(2))
    last = int(match.group(3))
    expected_last = max(1, math.ceil(total / page_size))
    if response_page != page or last != expected_last:
        errors.append("response page/last declaration changed")
    return total, last, errors


def _parse_status(value: Any, field: str) -> tuple[str, str]:
    raw = _clean(value)
    found = [name for name in _SOURCE_STATUS if name in raw]
    if len(found) != 1:
        raise ValueError(f"{field}: unknown or ambiguous source status")
    return found[0], _SOURCE_STATUS[found[0]]


def _base_row(
    source: DaedeokSource,
    identity: tuple[str, str, str, str],
    title: str,
    start: date,
    end: date,
    page: int,
) -> dict[str, Any]:
    identity_text = ":".join(identity)
    return {
        "provider": DAEJEON_DAEDEOK_PROVIDER,
        "provider_course_id": (
            f"{DAEJEON_DAEDEOK_PROVIDER}:{source.key}:{identity_text}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": "",
        "branch_code": "",
        "preserve_branch": True,
        "provider_organizer": "",
        "category": source.label,
        "program_type": "교육",
        "raw_url": daejeon_daedeok_detail_url(source.key, *identity),
        "application_url": "",
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "status": "CLOSED",
        "fee": "",
        "fee_amount": 0,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": "",
        "apply_start": "",
        "apply_end": "",
        "schedule_raw": "",
        "capacity": "",
        "capacity_total": 0,
        "target": "",
        "venue": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": DAEJEON_DAEDEOK_PARSER,
        "municipality_code": DAEJEON_DAEDEOK_MUNICIPALITY_CODE,
        "municipality_full_name": DAEJEON_DAEDEOK_MUNICIPALITY_NAME,
        "raw_fields": {
            "source_kind": source.kind,
            "source_key": source.key,
            "identity": identity_text,
            "identity_parts": identity,
            "menu_code": source.menu_code,
            "list_page": page,
            "source_status": "",
            "source_period": "",
            "source_application_period": "",
            "source_schedule": "",
            "source_target": "",
            "source_capacity": "",
            "source_fee": "",
            "list_institution": "",
            "education_institution": "",
            "education_location": "",
            "education_location_source": "",
            "application_control_present": False,
            "application_control_contract": "",
            "detail_verified": False,
        },
    }


def _parse_education_list(
    soup: BeautifulSoup, source: DaedeokSource, page: int, cutoff: date
) -> _ListPage:
    label = f"{source.key} page {page}"
    errors: list[str] = []
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if page_title != source.list_title:
        errors.append(f"{label}: official page title changed")
    forms = soup.select("form#listForm")
    if len(forms) != 1:
        return _ListPage([], 0, 1, [*errors, f"{label}: list form missing or duplicated"])
    form = forms[0]
    if _clean(form.get("method")).lower() != "post" or _clean(form.get("action")) != source.path:
        errors.append(f"{label}: list form method/action changed")
    expected_hidden = {
        "mnucd": source.menu_code,
        "searchLecDivArray": "",
        "bmode": "",
        "pageIndex": str(page),
        "lecId": "",
        "ordCd": "",
        "ordSidoCd": "",
        "ordLocalCd": "",
    }
    for name, expected in expected_hidden.items():
        count, actual = _hidden(form, name)
        if count != 1 or actual != expected:
            errors.append(f"{label}: hidden field {name} changed")
    total, last, counter_errors = _counter(soup, page, DAEJEON_DAEDEOK_PAGE_SIZE)
    errors.extend(f"{label}: {item}" for item in counter_errors)
    table = _one_table(soup, source)
    if table is None:
        return _ListPage([], total, last, [*errors, f"{label}: course table missing or duplicated"])
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != source.headers:
        errors.append(f"{label}: course table headers changed")

    rows: list[dict[str, Any]] = []
    empty_markers = 0
    missing_apply_start = 0
    application_period_anomalies = 0
    current_application_period_anomalies = 0
    archived_education_period_anomalies = 0
    for tr in table.select("tbody > tr"):
        cells = tr.select(":scope > td")
        if len(cells) == 1 and "등록된 자료가 없습니다" in _clean(tr.get_text(" ", strip=True)):
            empty_markers += 1
            continue
        try:
            if source.kind == "resident":
                if len(cells) != len(source.headers):
                    raise ValueError("course row shape changed")
                match = _RESIDENT_ID_RE.fullmatch(_clean(tr.get("onclick")))
                if match is None:
                    raise ValueError("course identity control changed")
                identity = tuple(match.groups())
                title = _clean(cells[2].get_text(" ", strip=True))
                list_institution = _clean(cells[1].get_text(" ", strip=True))
                apply_raw = _clean(cells[3].get_text(" ", strip=True))
                period_raw = _clean(cells[4].get_text(" ", strip=True))
                schedule = _clean(cells[5].get_text(" ", strip=True))
                capacity_raw = _clean(cells[6].get_text(" ", strip=True))
                target = _clean(cells[7].get_text(" ", strip=True))
                fee_raw = _clean(cells[8].get_text(" ", strip=True))
                status_raw = _clean(cells[9].get_text(" ", strip=True))
            else:
                if len(cells) != len(source.headers):
                    raise ValueError("course row shape changed")
                anchors = cells[1].select("a[onclick]")
                if len(anchors) != 2 or _clean(anchors[1].get_text(" ", strip=True)) != "[상세보기]":
                    raise ValueError("course detail controls changed")
                onclicks = {_clean(anchor.get("onclick")) for anchor in anchors}
                if len(onclicks) != 1:
                    raise ValueError("course detail identities disagree")
                match = _LIFELONG_ID_RE.fullmatch(next(iter(onclicks)))
                if match is None:
                    raise ValueError("course identity control changed")
                identity = tuple(match.groups())
                title = _clean(anchors[0].get_text(" ", strip=True))
                list_institution = ""
                values = []
                for cell in cells:
                    nodes = cell.select(":scope > .tds")
                    if len(nodes) != 1:
                        raise ValueError("responsive course value wrapper changed")
                    values.append(_clean(nodes[0].get_text(" ", strip=True)))
                apply_raw, period_raw, schedule = values[2], values[3], values[4]
                capacity_index = 6 if source.key == "lifelong_05" else 5
                target_index = 7 if source.key == "lifelong_05" else 6
                fee_index = 8 if source.key == "lifelong_05" else 7
                status_index = 9 if source.key == "lifelong_05" else 8
                capacity_raw = values[capacity_index]
                target = values[target_index]
                fee_raw = values[fee_index]
                status_raw = values[status_index]
            if not title:
                raise ValueError("course title missing")
            if source.kind == "resident" and not list_institution:
                raise ValueError("official list institution missing")
            period_dates = _dates(period_raw)
            if len(period_dates) != 2:
                raise ValueError("education period: expected exactly two dates")
            start, end = period_dates
            if end < start:
                if end >= cutoff:
                    raise ValueError("education period: current range reversed")
                archived_education_period_anomalies += 1
            if not schedule and end >= cutoff:
                raise ValueError("current course schedule missing")
            apply_dates = _dates(apply_raw)
            if len(apply_dates) == 2:
                apply_start, apply_end = apply_dates
                if apply_end < apply_start:
                    application_period_anomalies += 1
                    if end >= cutoff:
                        current_application_period_anomalies += 1
            elif len(apply_dates) == 1 and end < cutoff:
                apply_start, apply_end = None, apply_dates[0]
                missing_apply_start += 1
            else:
                raise ValueError("application period must contain two dates for current rows")
            source_status, status = _parse_status(status_raw, "status")
            application_period_reversed = (
                apply_start is not None and apply_end < apply_start
            )
            if application_period_reversed and status == "OPEN":
                raise ValueError("open course application period is reversed")
            capacity_total = _integer(
                capacity_raw, "capacity", last=source.key == "lifelong_05"
            )
            fee, fee_amount = _fee(fee_raw)
        except ValueError as exc:
            errors.append(f"{label}: {_clean(exc)}")
            continue
        row = _base_row(source, identity, title, start, end, page)
        row.update(
            {
                "status": status,
                "fee": fee,
                "fee_amount": fee_amount,
                "apply_period": (
                    ""
                    if application_period_reversed
                    else
                    f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
                    if apply_start is not None
                    else f"~ {apply_end.isoformat()}"
                ),
                "apply_start": (
                    apply_start.isoformat()
                    if apply_start is not None and not application_period_reversed
                    else ""
                ),
                "apply_end": "" if application_period_reversed else apply_end.isoformat(),
                "schedule_raw": schedule,
                "capacity": str(capacity_total),
                "capacity_total": capacity_total,
                "target": target,
            }
        )
        row["raw_fields"].update(
            {
                "source_status": source_status,
                "source_period": period_raw,
                "source_application_period": apply_raw,
                "source_schedule": schedule,
                "source_target": target,
                "source_capacity": capacity_raw,
                "source_fee": fee_raw,
                "list_institution": list_institution,
            }
        )
        rows.append(row)
    if rows and empty_markers:
        errors.append(f"{label}: course rows and empty marker coexist")
    if not rows and empty_markers != 1:
        errors.append(f"{label}: empty page lacks one official marker")
    return _ListPage(
        rows,
        total,
        last,
        errors,
        missing_apply_start,
        application_period_anomalies,
        current_application_period_anomalies,
        archived_education_period_anomalies,
    )


def _delivery_counter(soup: BeautifulSoup, page: int) -> tuple[int, int, list[str]]:
    nodes = soup.select("div.count")
    text = _clean(nodes[0].get_text(" ", strip=True)) if len(nodes) == 1 else ""
    match = re.fullmatch(
        r"총\s*강좌\s*:\s*([0-9,]+)\s+([0-9]+)\s*/\s*([0-9]+)", text
    )
    if match is None:
        return 0, 1, ["advertised delivery total/page counter changed"]
    total = int(match.group(1).replace(",", ""))
    response_page = int(match.group(2))
    last = int(match.group(3))
    errors: list[str] = []
    if response_page != page or last != max(1, math.ceil(total / DAEJEON_DAEDEOK_PAGE_SIZE)):
        errors.append("delivery response page/last declaration changed")
    return total, last, errors


def _parse_delivery_list(
    soup: BeautifulSoup, source: DaedeokSource, page: int
) -> _DeliveryPage:
    label = f"{source.key} page {page}"
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != source.list_title:
        errors.append(f"{label}: official page title changed")
    forms = soup.select("form#listForm")
    if len(forms) != 1:
        return _DeliveryPage([], 0, 1, [*errors, f"{label}: list form missing or duplicated"])
    form = forms[0]
    if _clean(form.get("method")).lower() != "post" or _clean(form.get("action")) != source.path:
        errors.append(f"{label}: list form method/action changed")
    # The live JSP intentionally resets this hidden value to 1 on every page;
    # the visible counter is the authoritative response-page declaration.
    for name, expected in {
        "mnucd": source.menu_code,
        "bmode": "listInfo",
        "seq": "",
        "pageIndex": "1",
    }.items():
        count, actual = _hidden(form, name)
        if count != 1 or actual != expected:
            errors.append(f"{label}: hidden field {name} changed")
    total, last, counter_errors = _delivery_counter(soup, page)
    errors.extend(f"{label}: {item}" for item in counter_errors)
    tables = [table for table in soup.select("table.table") if table.select("thead th")]
    if len(tables) != 1:
        return _DeliveryPage([], total, last, [*errors, f"{label}: roster table missing or duplicated"])
    table = tables[0]
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != source.headers:
        errors.append(f"{label}: roster table headers changed")
    rows: list[dict[str, str]] = []
    empty_markers = 0
    real_offerings = 0
    for tr in table.select("tbody > tr"):
        cells = tr.select(":scope > td")
        if len(cells) == 1 and "등록된 자료가 없습니다" in _clean(tr.get_text(" ", strip=True)):
            empty_markers += 1
            continue
        if len(cells) != 4:
            errors.append(f"{label}: roster row shape changed")
            continue
        anchors = tr.select("a[onclick]")
        if len(anchors) != 2:
            errors.append(f"{label}: roster identity controls changed")
            continue
        onclicks = {_clean(anchor.get("onclick")) for anchor in anchors}
        match = _DELIVERY_ID_RE.fullmatch(next(iter(onclicks))) if len(onclicks) == 1 else None
        if match is None or any(_clean(anchor.get("href")) != "#view" for anchor in anchors):
            errors.append(f"{label}: roster identity contract changed")
            continue
        serial, category, course_title, status = (
            _clean(cell.get_text(" ", strip=True)) for cell in cells
        )
        if not serial.isdigit() or not category or not course_title:
            errors.append(f"{label}: roster value missing")
            continue
        if (
            status not in DAEJEON_DAEDEOK_DELIVERY_TEMPLATE_STATUSES
            or _DATE4_RE.search(_clean(tr.get_text(" ", strip=True))) is not None
        ):
            real_offerings += 1
        rows.append(
            {
                "serial": serial,
                "identity": match.group(1),
                "category": category,
                "title": course_title,
                "status": status,
            }
        )
    if rows and empty_markers:
        errors.append(f"{label}: roster rows and empty marker coexist")
    if not rows and empty_markers != 1:
        errors.append(f"{label}: empty roster page lacks one official marker")
    return _DeliveryPage(rows, total, last, errors, real_offerings)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for row in rows:
        raw = row.get("raw_fields") if isinstance(row.get("raw_fields"), Mapping) else row
        result.append((_clean(raw.get("identity")), _clean(row.get("title"))))
    return tuple(result)


def _detail_fields(soup: BeautifulSoup) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    errors: list[str] = []
    for node in soup.select(".board_view ul.detail > li"):
        names = node.select(":scope > .titles strong")
        values = node.select(":scope > .txts")
        if len(names) != 1 or len(values) != 1:
            continue
        name = _clean(names[0].get_text(" ", strip=True))
        if name in fields:
            errors.append(f"duplicate detail field {name}")
        fields[name] = _clean(values[0].get_text(" ", strip=True))
    return fields, errors


def _validate_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, source: DaedeokSource
) -> tuple[dict[str, Any], list[str]]:
    row = dict(listed)
    row["raw_fields"] = dict(listed["raw_fields"])
    identity = tuple(row["raw_fields"].get("identity_parts", ()))
    label = f"{source.key}/{identity[0] if identity else ''}"
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != source.detail_title:
        errors.append(f"{label}: official detail title changed")
    forms = soup.select("form#detailForm")
    if len(forms) != 1:
        return row, [*errors, f"{label}: detail form missing or duplicated"]
    form = forms[0]
    if _clean(form.get("method")).lower() != "post" or _clean(form.get("action")) != source.path:
        errors.append(f"{label}: detail form method/action changed")
    expected = {
        "mnucd": source.menu_code,
        "bmode": "" if source.kind == "resident" else "detail1",
        "pageIndex": str(row["raw_fields"]["list_page"]),
        "lecId": identity[0],
        "ordCd": identity[1],
        "ordSidoCd": identity[2],
        "ordLocalCd": identity[3],
    }
    for name, value in expected.items():
        count, actual = _hidden(form, name)
        if count != 1 or actual != value:
            errors.append(f"{label}: detail identity field {name} changed")
    fields, field_errors = _detail_fields(soup)
    errors.extend(f"{label}: {item}" for item in field_errors)
    required = {
        "프로그램명",
        "교육일정",
        "교육대상",
        "수강료",
        "모집인원",
        "교육장소",
        "교육기관",
        "교육기간",
        "수강신청기간",
        "모집방법",
    }
    if not required <= set(fields):
        errors.append(f"{label}: required detail fields changed")
        return row, errors
    try:
        start, end = _date_pair(fields["교육기간"], "detail period", short_year=True)
        apply_start, apply_end = _date_pair(
            fields["수강신청기간"],
            "detail application period",
            short_year=True,
            allow_reversed=True,
        )
        capacity_total = _integer(fields["모집인원"], "detail capacity")
        fee, fee_amount = _fee(fields["수강료"])
    except ValueError as exc:
        return row, [*errors, f"{label}: {_clean(exc)}"]
    if _normalized(fields["프로그램명"]) != _normalized(row["title"]):
        errors.append(f"{label}: detail/list title mismatch")
    if (start.isoformat(), end.isoformat()) != (row["start_date"], row["end_date"]):
        errors.append(f"{label}: detail/list education period mismatch")
    listed_apply_dates = _dates(row["raw_fields"]["source_application_period"])
    listed_apply_period = tuple(value.isoformat() for value in listed_apply_dates)
    if (apply_start.isoformat(), apply_end.isoformat()) != listed_apply_period:
        errors.append(f"{label}: detail/list application period mismatch")
    if capacity_total != int(row["capacity_total"]):
        errors.append(f"{label}: detail/list capacity mismatch")
    if fee_amount != int(row["fee_amount"]):
        errors.append(f"{label}: detail/list fee mismatch")
    if _normalized(fields["교육일정"]) != _normalized(row["schedule_raw"]):
        errors.append(f"{label}: detail/list schedule mismatch")
    if _normalized(fields["교육대상"]) != _normalized(row["target"]):
        errors.append(f"{label}: detail/list target mismatch")
    institution = fields["교육기관"]
    detail_venue = fields["교육장소"]
    list_institution = _clean(row["raw_fields"].get("list_institution"))
    if not institution:
        errors.append(f"{label}: official institution missing")
    if source.kind == "resident" and _normalized(institution) != _normalized(list_institution):
        errors.append(f"{label}: detail/list institution mismatch")
    if detail_venue:
        venue = detail_venue
        venue_source = "detail_education_location"
    elif source.kind == "resident" and list_institution:
        venue = list_institution
        venue_source = "official_list_institution_fallback"
    else:
        venue = ""
        venue_source = ""
        errors.append(f"{label}: official location missing")

    apply_controls = [
        node
        for node in soup.select(".al_right .btn_type_green > a")
        if _clean(node.get_text(" ", strip=True)) == "수강신청하기"
    ]
    back_controls = [
        node
        for node in soup.select(".al_right .btn_type_gray > a")
        if _clean(node.get_text(" ", strip=True)) == "프로그램 목록보기"
    ]
    controls_valid = not (
        len(apply_controls) != 1
        or _clean(apply_controls[0].get("href")) != "#"
        or len(back_controls) != 1
        or _clean(back_controls[0].get("href")) != "#"
        or _clean(back_controls[0].get("onclick"))
        != "fn_egov_selectList(document.getElementById('detailForm')); return false;"
    )
    expected_apply_control = (
        "alert('폐강된 강좌입니다.'); return false;"
        if row["status"] == "CANCELLED"
        else "fn_NonCheck(); return false;"
    )
    if (
        not controls_valid
        or _clean(apply_controls[0].get("onclick")) != expected_apply_control
    ):
        errors.append(f"{label}: course-bound application controls changed")
    else:
        row["raw_fields"]["application_control_present"] = True
        row["raw_fields"]["application_control_contract"] = (
            "detail_form_identity_plus_official_cancelled_gate"
            if row["status"] == "CANCELLED"
            else "detail_form_identity_plus_official_login_gate"
        )
        if row["status"] == "OPEN":
            row["application_url"] = row["raw_url"]
            row["application_type"] = "ONLINE_RESERVATION_LOGIN_REQUIRED"
            row["reservation_available"] = True

    row.update(
        {
            "branch": venue,
            "branch_code": (
                f"DAEDEOK:{source.key}:"
                f"{hashlib.sha1(_normalized(venue).encode('utf-8')).hexdigest()[:12]}"
            ),
            "provider_organizer": institution,
            "venue": venue,
            "fee": fee,
            "fee_amount": fee_amount,
            "schedule_raw": fields["교육일정"],
            "target": fields["교육대상"],
        }
    )
    row["raw_fields"].update(
        {
            "education_institution": institution,
            "education_location": venue,
            "education_location_source": venue_source,
        }
    )
    row["raw_fields"].pop("identity_parts", None)
    if not errors:
        row["raw_fields"]["detail_verified"] = True
    return row, errors


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if _FORBIDDEN_PERSISTED_KEYS & set(row):
        errors.append("forbidden top-level PII/free-form key persisted")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the safe allowlist")
    payload = repr(
        {key: value for key, value in row.items() if key not in {"raw_url", "application_url"}}
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
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


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "delivery_information_count": 0,
        "delivery_real_offering_count": 0,
        "delivery_emitted_count": 0,
        "archived_missing_apply_start_count": 0,
        "application_period_anomaly_count": 0,
        "current_application_period_anomaly_count": 0,
        "archived_education_period_anomaly_count": 0,
        "configured_collection_error": error,
    }


def collect_daejeon_daedeok_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 500,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = DAEJEON_DAEDEOK_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    poster: Optional[Poster] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Daedeok-gu education snapshot."""

    meta = _base_meta()
    if not is_daejeon_daedeok_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Daedeok-gu owner"
        return [], DAEJEON_DAEDEOK_PARSER, meta
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages < len(DAEJEON_DAEDEOK_SOURCES)
        or isinstance(detail_limit, bool)
        or not isinstance(detail_limit, int)
        or detail_limit < 0
        or isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid timeout/max_pages/detail_limit/max_workers cap",
            }
        )
        return [], DAEJEON_DAEDEOK_PARSER, meta
    try:
        cutoff = _today(today)
    except ValueError as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], DAEJEON_DAEDEOK_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    current_poster = poster or _default_poster
    errors: list[str] = []

    first_items = []
    for source in DAEJEON_DAEDEOK_SOURCES:
        parser = (
            (lambda soup, current=source: _parse_delivery_list(soup, current, 1))
            if source.kind == "delivery"
            else (
                lambda soup, current=source: _parse_education_list(
                    soup, current, 1, cutoff
                )
            )
        )
        first_items.append(((source.key, 1, "data"), source.list_url, None, parser))
    initial, initial_fetch_errors = _fetch_many(
        first_items,
        fetcher=current_fetcher,
        poster=current_poster,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(initial_fetch_errors)
    meta["pages"] = len(initial)
    meta["list_requests"] = len(initial)
    first_pages: dict[str, _ListPage | _DeliveryPage] = {}
    totals: dict[str, int] = {}
    lasts: dict[str, int] = {}
    for source in DAEJEON_DAEDEOK_SOURCES:
        result = initial.get((source.key, 1, "data"))
        if not isinstance(result, (_ListPage, _DeliveryPage)):
            errors.append(f"{source.key}: first page missing")
            continue
        first_pages[source.key] = result
        totals[source.key] = result.total
        lasts[source.key] = result.last
        errors.extend(result.errors)
    if len(totals) != len(DAEJEON_DAEDEOK_SOURCES):
        meta.update(
            {
                "source_totals": totals,
                "declared_pages": lasts,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return [], DAEJEON_DAEDEOK_PARSER, meta

    required_requests = sum(last + 2 for last in lasts.values())
    meta["required_list_requests"] = required_requests
    if required_requests > max_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {max_pages} of {required_requests} required list requests"
        )
    if errors:
        meta.update(
            {
                "source_totals": totals,
                "declared_pages": lasts,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return [], DAEJEON_DAEDEOK_PARSER, meta

    remaining_items = []
    for source in DAEJEON_DAEDEOK_SOURCES:
        def parser_for(current: DaedeokSource, current_page: int) -> Callable[[BeautifulSoup], Any]:
            if current.kind == "delivery":
                return lambda soup: _parse_delivery_list(soup, current, current_page)
            return lambda soup: _parse_education_list(soup, current, current_page, cutoff)

        for page in range(2, lasts[source.key] + 1):
            remaining_items.append(
                (
                    (source.key, page, "data"),
                    daejeon_daedeok_list_url(source.key, page),
                    None,
                    parser_for(source, page),
                )
            )
        sentinel_page = lasts[source.key] + 1
        remaining_items.extend(
            [
                (
                    (source.key, sentinel_page, "sentinel"),
                    daejeon_daedeok_list_url(source.key, sentinel_page),
                    None,
                    parser_for(source, sentinel_page),
                ),
                (
                    (source.key, 1, "recheck"),
                    source.list_url,
                    None,
                    parser_for(source, 1),
                ),
            ]
        )
    remaining, remaining_fetch_errors = _fetch_many(
        remaining_items,
        fetcher=current_fetcher,
        poster=current_poster,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(remaining_fetch_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)
    meta["sentinel_requests"] = sum(
        (source.key, lasts[source.key] + 1, "sentinel") in remaining
        for source in DAEJEON_DAEDEOK_SOURCES
    )
    meta["stability_rechecks"] = sum(
        (source.key, 1, "recheck") in remaining
        for source in DAEJEON_DAEDEOK_SOURCES
    )

    education_rows: list[dict[str, Any]] = []
    delivery_rows: list[dict[str, str]] = []
    page_counts: dict[str, dict[int, int]] = {}
    archived_missing = 0
    application_period_anomalies = 0
    current_application_period_anomalies = 0
    archived_education_period_anomalies = 0
    delivery_real_offerings = 0
    for source in DAEJEON_DAEDEOK_SOURCES:
        source_rows: list[dict[str, Any]] = []
        page_counts[source.key] = {}
        signatures: dict[int, tuple[tuple[str, str], ...]] = {}
        total, last = totals[source.key], lasts[source.key]
        for page in range(1, last + 1):
            result = (
                first_pages[source.key]
                if page == 1
                else remaining.get((source.key, page, "data"))
            )
            expected_type = _DeliveryPage if source.kind == "delivery" else _ListPage
            if not isinstance(result, expected_type):
                errors.append(f"{source.key} page {page}: missing response")
                continue
            errors.extend(result.errors)
            if (result.total, result.last) != (total, last):
                errors.append(f"{source.key} page {page}: total/last changed")
            expected_count = (
                0
                if total == 0
                else DAEJEON_DAEDEOK_PAGE_SIZE
                if page < last
                else total - DAEJEON_DAEDEOK_PAGE_SIZE * (last - 1)
            )
            if len(result.rows) != expected_count:
                errors.append(
                    f"{source.key} page {page}: row count {len(result.rows)} != {expected_count}"
                )
            page_counts[source.key][page] = len(result.rows)
            signatures[page] = _page_signature(result.rows)
            source_rows.extend(result.rows)
            if isinstance(result, _ListPage):
                archived_missing += result.archived_missing_apply_start
                application_period_anomalies += result.application_period_anomalies
                current_application_period_anomalies += (
                    result.current_application_period_anomalies
                )
                archived_education_period_anomalies += (
                    result.archived_education_period_anomalies
                )
            else:
                delivery_real_offerings += result.real_offering_count
        if len(source_rows) != total:
            errors.append(f"{source.key}: advertised total does not match parsed rows")
        nonempty_signatures = [signature for signature in signatures.values() if signature]
        if len(nonempty_signatures) != len(set(nonempty_signatures)):
            errors.append(f"{source.key}: duplicate non-empty page signature")
        sentinel = remaining.get((source.key, last + 1, "sentinel"))
        recheck = remaining.get((source.key, 1, "recheck"))
        expected_type = _DeliveryPage if source.kind == "delivery" else _ListPage
        if not isinstance(sentinel, expected_type) or not isinstance(recheck, expected_type):
            errors.append(f"{source.key}: sentinel or page-one recheck missing")
        else:
            errors.extend(sentinel.errors)
            errors.extend(recheck.errors)
            if (sentinel.total, sentinel.last) != (total, last) or sentinel.rows:
                errors.append(f"{source.key}: immediate post-last page is not empty")
            if (
                (recheck.total, recheck.last) != (total, last)
                or _page_signature(recheck.rows) != signatures.get(1, ())
            ):
                errors.append(f"{source.key}: page-one recheck changed")
        if source.kind == "delivery":
            delivery_rows.extend(source_rows)
        else:
            education_rows.extend(source_rows)

    official_identities = [
        tuple(row["raw_fields"].get("identity_parts", ())) for row in education_rows
    ]
    identity_duplicate_count = len(official_identities) - len(set(official_identities))
    if identity_duplicate_count:
        errors.append(
            f"{identity_duplicate_count} duplicate official identities across education sources"
        )
    delivery_identities = [_clean(row.get("identity")) for row in delivery_rows]
    delivery_duplicate_count = len(delivery_identities) - len(set(delivery_identities))
    if delivery_duplicate_count:
        errors.append(f"{delivery_duplicate_count} duplicate delivery template identities")
    if delivery_real_offerings:
        errors.append(
            "delivery listInfo exposed a non-closed or dated row without a proven dated-offering contract"
        )
    current_rows = [
        row
        for row in education_rows
        if date.fromisoformat(_clean(row["end_date"])) >= cutoff
    ]
    list_complete = bool(
        not errors
        and len(education_rows) + len(delivery_rows) == sum(totals.values())
        and meta["list_requests"] == required_requests
    )
    if len(current_rows) > detail_limit:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {detail_limit} of {len(current_rows)} required details"
        )

    detailed_rows: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    if list_complete and not errors:
        detail_items = []
        for listed in current_rows:
            source = DAEJEON_DAEDEOK_SOURCE_BY_KEY[listed["raw_fields"]["source_key"]]
            identity = tuple(listed["raw_fields"]["identity_parts"])
            detail_items.append(
                (
                    (source.key, identity),
                    f"https://{source.host}{source.path}",
                    _detail_payload(listed),
                    lambda soup, current=dict(listed), current_source=source: _validate_detail(
                        current, soup, current_source
                    ),
                )
            )
        meta["detail_attempts"] = len(detail_items)
        details, detail_fetch_errors = _fetch_many(
            detail_items,
            fetcher=current_fetcher,
            poster=current_poster,
            session_factory=factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        meta["pages"] += len(details)
        for listed in current_rows:
            source_key = listed["raw_fields"]["source_key"]
            identity = tuple(listed["raw_fields"]["identity_parts"])
            result = details.get((source_key, identity))
            if not isinstance(result, tuple) or len(result) != 2:
                detail_errors.append(f"{source_key}/{identity[0]}: detail response missing")
                continue
            detailed, item_errors = result
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                detailed_rows.append(detailed)
                meta["detail_pages"] += 1
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        list_complete
        and meta["detail_attempts"] == len(current_rows)
        and meta["detail_pages"] == len(current_rows)
        and not detail_errors
    )

    result: list[dict[str, Any]] = []
    if details_complete and not errors:
        for row in detailed_rows:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(detailed_rows))
            except Exception as exc:
                errors.append(f"dedupe failed: {_clean(exc)}")
            if len(result) != len(detailed_rows):
                errors.append(
                    f"dedupe changed official identity cardinality {len(detailed_rows)} to {len(result)}"
                )
                result = []
    snapshot_complete = bool(list_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    source_counts = Counter(row["raw_fields"]["source_key"] for row in education_rows)
    current_counts = Counter(row["raw_fields"]["source_key"] for row in current_rows)
    branch_counts = Counter(_clean(row["branch"]) for row in result)
    status_counts = Counter(_clean(row["status"]) for row in result)
    meta.update(
        {
            "ownership_scope": DAEJEON_DAEDEOK_OWNERSHIP_SCOPE,
            "ownership_fanout_urls": [source.list_url for source in DAEJEON_DAEDEOK_EDUCATION_SOURCES],
            "delivery_audit_url": DAEJEON_DAEDEOK_DELIVERY_SOURCE.list_url,
            "source_totals": totals,
            "declared_pages": lasts,
            "page_counts": page_counts,
            "education_source_rows": len(education_rows),
            "education_source_counts": dict(source_counts),
            "current_source_count": len(current_rows),
            "current_counts": dict(current_counts),
            "expired_count": len(education_rows) - len(current_rows),
            "archived_missing_apply_start_count": archived_missing,
            "application_period_anomaly_count": application_period_anomalies,
            "current_application_period_anomaly_count": current_application_period_anomalies,
            "archived_education_period_anomaly_count": archived_education_period_anomalies,
            "identity_duplicate_count": identity_duplicate_count,
            "delivery_information_count": len(delivery_rows),
            "delivery_closed_count": sum(
                row["status"] == "신청 마감" for row in delivery_rows
            ),
            "delivery_waiting_count": sum(
                row["status"] == "신청 대기중" for row in delivery_rows
            ),
            "delivery_real_offering_count": delivery_real_offerings,
            "delivery_identity_duplicate_count": delivery_duplicate_count,
            "delivery_emitted_count": 0,
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "branch_fallback_count": sum(
                row["raw_fields"].get("education_location_source")
                == "official_list_institution_fallback"
                for row in result
            ),
            "status_counts": dict(status_counts),
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "all complete Daedeok-gu education catalogues have ended"
                if snapshot_complete and not current_rows
                else ""
            ),
            "municipality_coverage": [DAEJEON_DAEDEOK_MUNICIPALITY_CODE],
            "alias_providers": sorted(DAEJEON_DAEDEOK_ALIAS_PROVIDERS),
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, DAEJEON_DAEDEOK_PARSER, meta


collect = collect_daejeon_daedeok_education


__all__ = [
    "DAEJEON_DAEDEOK_ALIAS_PROVIDERS",
    "DAEJEON_DAEDEOK_ALIAS_URLS",
    "DAEJEON_DAEDEOK_CANONICAL_URL",
    "DAEJEON_DAEDEOK_DELIVERY_SOURCE",
    "DAEJEON_DAEDEOK_EDUCATION_SOURCES",
    "DAEJEON_DAEDEOK_MUNICIPALITY_CODE",
    "DAEJEON_DAEDEOK_MUNICIPALITY_NAME",
    "DAEJEON_DAEDEOK_PARSER",
    "DAEJEON_DAEDEOK_PROVIDER",
    "DAEJEON_DAEDEOK_SOURCES",
    "collect",
    "collect_daejeon_daedeok_education",
    "daejeon_daedeok_detail_url",
    "daejeon_daedeok_list_url",
    "is_daejeon_daedeok_education_target",
    "is_daejeon_daedeok_owned_alias_target",
]
