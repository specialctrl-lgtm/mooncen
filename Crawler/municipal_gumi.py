"""Fail-closed collector for Gumi City's integrated program ledger.

The ``search.do?key=208`` page is the canonical, all-institution owner.  The
public institution menus (including the ``go.do?key=74`` redirect) are only
filters of this ledger.  This collector walks the complete descending ledger,
proves an exact empty post-last boundary, rechecks both edges, and verifies
every current/future education or experience row against its public detail
page.

The official catalogue mixes education and experience categories.  Rows are
routed course by course using that official classification.  Private
application endpoints are never requested; their URLs are exposed only when
the official list says ``접수중`` and the detail page has a matching visible,
identity-bound application control.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


GUMI_PROVIDER = "MUNI_WWW_GUMI_GO_KR_51F967B3"
GUMI_CANONICAL_CANDIDATE_ID = "MUNI_IR_8B0F767E88A9"
GUMI_GO74_ALIAS_PROVIDER = "MUNI_WWW_GUMI_GO_KR_E8B61671"
GUMI_GO74_ALIAS_CANDIDATE_ID = "MUNI_IR_0406AA593A15"
GUMI_MUNICIPALITY_CODE = "4719000000"
GUMI_MUNICIPALITY_NAME = "경상북도 구미시"
GUMI_HOST = "www.gumi.go.kr"
GUMI_LIST_PATH = "/reservation/www/edu/program/search.do"
GUMI_DETAIL_PATH = "/reservation/www/edu/program/detail.do"
GUMI_APPLICATION_PATH = "/reservation/www/edu/app/write.do"
GUMI_CANONICAL_URL = f"https://{GUMI_HOST}{GUMI_LIST_PATH}?key=208"
GUMI_PAGE_SIZE = 10
GUMI_MAX_HTML_BYTES = 3_000_000
GUMI_MAX_WORKERS = 10
GUMI_PARSER = (
    "gumi_official_integrated_all_institution_education_ledger+"
    "declared_pages+exact_empty_post_last+stable_first_last+"
    "site_and_category_registry+date_current+experience_exclusion+"
    "current_details+identity_bound_application_controls+pii_allowlist"
)

GUMI_SITE_REGISTRY: tuple[tuple[str, str], ...] = (
    ("LS", "축산과"),
    ("CA", "구미시문화예술회관"),
    ("CB", "탄소제로교육관"),
    ("TT", "테스트"),
    ("GH", "구미보건소"),
    ("FA", "농업기술센터"),
    ("HE", "선산보건소"),
    ("PA", "박정희대통령역사자료관"),
    ("MU", "구미성리학역사관"),
    ("KW", "강동문화복지회관"),
    ("LL", "평생학습원"),
)

_ALIAS_PATH = "/reservation/www/edu/program/list.do"
GUMI_EXCLUDED_FILTER_ALIASES: tuple[dict[str, str], ...] = (
    {
        "url": "https://www.gumi.go.kr/reservation/go.do?key=74",
        "decision": "exclude_redirect_to_LL_subset",
    },
    {
        "url": "https://www.gumi.go.kr/reservation/www/edu/program/list.do?key=260&siteCode=LL",
        "decision": "exclude_institution_subset",
    },
    {
        "url": "https://www.gumi.go.kr/reservation/www/edu/program/list.do?key=261&siteCode=LL&cateIdx=3040",
        "decision": "exclude_category_subset",
    },
    {
        "url": "https://www.gumi.go.kr/reservation/www/edu/program/list.do?key=262&siteCode=LL&cateIdx=383",
        "decision": "exclude_category_subset",
    },
    {
        "url": "https://www.gumi.go.kr/reservation/www/edu/program/list.do?key=263&siteCode=LL&cateIdx=423",
        "decision": "exclude_category_subset",
    },
    *(
        {
            "url": (f"https://www.gumi.go.kr/reservation/www/edu/program/list.do?key={key}&siteCode={site}"),
            "decision": decision,
        }
        for key, site, decision in (
            ("75", "KW", "exclude_institution_subset"),
            ("76", "MU", "exclude_institution_subset"),
            ("210", "GH", "exclude_institution_subset"),
            ("78", "HE", "exclude_institution_subset"),
            ("79", "FA", "exclude_institution_subset"),
            ("77", "PA", "exclude_institution_subset"),
            ("284", "CA", "exclude_institution_subset"),
            ("82", "CL", "exclude_empty_library_filter"),
            ("282", "CB", "exclude_institution_subset"),
        )
    ),
)

GUMI_OWNER_BOUNDARY_AUDIT: dict[str, dict[str, str]] = {
    "integrated_education_search": {
        "decision": "include_canonical_owner",
        "reason": "all official education institutions and classifications share one identity ledger",
    },
    "institution_and_category_menus": {
        "decision": "exclude_duplicate_filters",
        "reason": "the menu pages filter identities already present in the integrated ledger",
    },
    "gumigx_worker_culture_center": {
        "decision": "exclude_separate_owner",
        "reason": "sports/culture-centre service has its own provider and is not this ledger",
    },
    "integrated_experience_programs": {
        "decision": "include_from_canonical_owner",
        "reason": "official experience-classified programs share the canonical identity ledger",
    },
    "facility_reservations": {
        "decision": "exclude_wrong_service_family",
        "reason": "facility inventory is not part of the education and experience program ledger",
    },
}

GUMI_PII_FIELDS_NEVER_PERSISTED = (
    "문의처",
    "강의 계획서",
    "강의 소개",
    "유의 사항",
    "신청자명",
    "생년월일",
    "주소",
    "전화번호",
    "이메일",
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GumiContractError(ValueError):
    """Raised when the audited official Gumi source contract changes."""


@dataclass(frozen=True)
class _Page:
    requested: int
    observed: int
    last: int
    total: int
    rows: tuple[dict[str, Any], ...]
    site_registry: tuple[tuple[str, str], ...]
    category_registry: tuple[tuple[str, str, str], ...]
    structural_empty: bool


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_PAGE_MARKER = re.compile(r"^총\s*([\d,]+)건\s*\[\s*(\d+)\s*/\s*(\d+)페이지\s*\]$")
_VIEW = re.compile(r"^goView\(([1-9]\d*)\);\s*return\s+false;$")
_CAPACITY_LIST = re.compile(r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)$")
_CAPACITY_DETAIL = re.compile(r"^(\d[\d,]*)\s*명\s*/\s*(\d[\d,]*)\s*명(?:\s*/\s*(\d[\d,]*)\s*명)?(?:\s+.*)?$")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ACTION = re.compile(r"frm\.action\s*=\s*['\"]([^'\"]+)['\"]\s*;")
_SITE_VALUE = re.compile(r"\$\('#siteCode'\)\.val\('([^']+)'\)")
_CATEGORY_VALUE = re.compile(r"\$\('#cateIdx'\)\.val\('([^']+)'\)")

_HEADERS = (
    "번호",
    "기관",
    "강좌분류",
    "기수/과정",
    "강좌명/시간대",
    "수강료",
    "신청인원/ 정원",
    "교육기간",
    "접수기간",
    "상태",
)
_SOURCE_STATUSES: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수 대기": "SCHEDULED",
    "마감": "CLOSED",
    "접수 마감": "CLOSED",
    "추첨 완료 당첨확인": "CLOSED",
    "추첨 대기": "CLOSED",
}
_SELECTIONS = {"선착순", "추첨"}
_DETAIL_SAFE_FIELDS = {
    "강좌명/시간대",
    "교육 기간",
    "결제 기간",
    "접수 기간",
    "강의 시간",
    "신청/접수",
    "신청/접수/대기",
    "소속",
    "난이도",
    "수강료",
    "교육장소",
    "교육 대상",
}
_DETAIL_DISCARDED_FIELDS = {"문의처", "강의 계획서", "강의 소개", "유의 사항"}
_DETAIL_REQUIRED_FIELDS = {
    "강좌명/시간대",
    "교육 기간",
    "접수 기간",
    "강의 시간",
    "소속",
    "난이도",
    "수강료",
    "교육장소",
    "교육 대상",
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "display_number",
        "source_institution",
        "source_category",
        "source_cohort",
        "source_status",
        "source_selection",
        "source_fee",
        "source_education_period",
        "source_apply_period",
        "source_site_code",
        "source_category_id",
        "source_venue",
        "branch_basis",
        "detail_verified",
        "application_control_present",
        "inactive_visible_application_control",
        "lottery_control_present",
        "education_scope_verified",
        "experience_scope_verified",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "instructor",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
        "image_url",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).casefold()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlparse(url).query, keep_blank_values=True, strict_parsing=True)


def is_gumi_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != GUMI_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
        query = _query(parsed.geturl())
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GUMI_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GUMI_LIST_PATH
        and query == [("key", "208")]
        and not parsed.fragment
    )


is_target = is_gumi_education_target


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


def gumi_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    params: list[tuple[str, Any]] = [("key", "208")]
    if page != 1:
        params.append(("page", page))
    return f"https://{GUMI_HOST}{GUMI_LIST_PATH}?{urlencode(params)}"


def gumi_detail_url(identity: str) -> str:
    if _IDENTITY.fullmatch(str(identity)) is None:
        raise ValueError("invalid Gumi course identity")
    return f"https://{GUMI_HOST}{GUMI_DETAIL_PATH}?" + urlencode((("idx", str(identity)), ("key", "208")))


def gumi_application_url(identity: str) -> str:
    if _IDENTITY.fullmatch(str(identity)) is None:
        raise ValueError("invalid Gumi course identity")
    return f"https://{GUMI_HOST}{GUMI_APPLICATION_PATH}?" + urlencode((("prmIdx", str(identity)), ("key", "208")))


def _allowed_response_url(url: str, requested_url: str) -> bool:
    parsed = urlparse(url)
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GUMI_HOST
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and not parsed.fragment
    ):
        return False
    try:
        pairs = _query(url)
    except ValueError:
        return False
    if parsed.path == GUMI_LIST_PATH:
        return pairs == _query(requested_url) and (
            pairs == [("key", "208")]
            or (
                len(pairs) == 2
                and pairs[0] == ("key", "208")
                and pairs[1][0] == "page"
                and _IDENTITY.fullmatch(pairs[1][1]) is not None
            )
        )
    if parsed.path == GUMI_DETAIL_PATH:
        return pairs == _query(requested_url) and (
            len(pairs) == 2
            and pairs[0][0] == "idx"
            and _IDENTITY.fullmatch(pairs[0][1]) is not None
            and pairs[1] == ("key", "208")
        )
    return False


def _request_soup(
    url: str,
    timeout: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    if not _allowed_response_url(url, url):
        raise GumiContractError("request left the audited Gumi public list/detail contract")
    last_error: Optional[Exception] = None
    for _attempt in range(2):
        session = session_factory()
        try:
            response = fetcher(session, url, timeout)
            status = int(getattr(response, "status_code", 200))
            if status != 200:
                raise GumiContractError(f"unexpected HTTP status {status}")
            headers = getattr(response, "headers", {}) or {}
            if headers.get("Location"):
                raise GumiContractError("redirect response is not accepted")
            final_url = _clean(getattr(response, "url", url)) or url
            if not _allowed_response_url(final_url, url):
                raise GumiContractError("response URL changed")
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", response)).encode("utf-8")
            if not content or len(content) > GUMI_MAX_HTML_BYTES:
                raise GumiContractError("empty or oversized official HTML response")
            return BeautifulSoup(content, "lxml", from_encoding="utf-8")
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()
    assert last_error is not None
    raise last_error


def _table_headers(table: Any) -> tuple[str, ...]:
    return tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))


def _registries(soup: BeautifulSoup) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str, str], ...]]:
    site_selects = soup.select("select[name='siteCode']")
    category_selects = soup.select("select[name='cateIdx']")
    if len(site_selects) != 1 or len(category_selects) != 1:
        raise GumiContractError("institution/category registry controls changed")
    site_options = tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in site_selects[0].select("option")
    )
    if not site_options or site_options[0] != ("", "전체") or site_options[1:] != GUMI_SITE_REGISTRY:
        raise GumiContractError("official institution registry changed")
    institutions = {name for _code, name in GUMI_SITE_REGISTRY}
    category_options = category_selects[0].select("option")
    if not category_options:
        raise GumiContractError("official category registry is empty")
    first = (_clean(category_options[0].get("value")), _clean(category_options[0].get_text(" ", strip=True)))
    if first != ("0", "강좌분류"):
        raise GumiContractError("category registry sentinel changed")
    result: list[tuple[str, str, str]] = []
    for option in category_options[1:]:
        identity = _clean(option.get("value"))
        text = _clean(option.get_text(" ", strip=True))
        if _IDENTITY.fullmatch(identity) is None or " - " not in text:
            raise GumiContractError("category registry entry changed")
        institution, category = text.split(" - ", 1)
        if institution not in institutions or not category:
            raise GumiContractError("category registry institution changed")
        result.append((identity, institution, category))
    if not result or len(result) != len(set(result)):
        raise GumiContractError("category registry duplicate/empty entry")
    if len({identity for identity, _institution, _category in result}) != len(result):
        raise GumiContractError("category registry identity collision")
    # The official registry currently retains two historical numeric IDs for
    # 구미성리학역사관/강좌형프로그램.  The list exposes only the label; the
    # detail action supplies the unambiguous ID, which is checked against this
    # complete candidate set below.
    return GUMI_SITE_REGISTRY, tuple(result)


def _date_values(value: Any) -> tuple[date, ...]:
    return tuple(date(int(year), int(month), int(day)) for year, month, day in _DATE.findall(_clean(value)))


def _time_values(value: Any) -> tuple[str, ...]:
    return tuple(f"{int(hour):02d}:{minute}" for hour, minute in _TIME.findall(_clean(value)))


def _date_range(value: Any, context: str, *, allow_reversed: bool = False) -> tuple[date, date]:
    values = _date_values(value)
    if len(values) != 2 or (values[1] < values[0] and not allow_reversed):
        raise GumiContractError(f"{context}: invalid date range")
    return values[0], values[1]


def _parse_page(soup: BeautifulSoup, requested: int) -> _Page:
    markers = soup.select(".bbs_page")
    if len(markers) != 1:
        raise GumiContractError(f"page {requested}: count/page marker changed")
    marker = _PAGE_MARKER.fullmatch(_clean(markers[0].get_text(" ", strip=True)))
    if marker is None:
        raise GumiContractError(f"page {requested}: count/page marker text changed")
    total, observed, last = int(marker.group(1).replace(",", "")), int(marker.group(2)), int(marker.group(3))
    if observed != requested or last < 1 or total < 1:
        raise GumiContractError(f"page {requested}: pagination identity changed")
    matching = [table for table in soup.select("table") if _table_headers(table) == _HEADERS]
    if len(matching) != 1:
        raise GumiContractError(f"page {requested}: education table/header changed")
    site_registry, category_registry = _registries(soup)
    category_map: dict[tuple[str, str], tuple[str, ...]] = {}
    for category_identity, category_institution, category_name in category_registry:
        key = (category_institution, category_name)
        category_map[key] = (*category_map.get(key, ()), category_identity)
    site_map = {institution: code for code, institution in site_registry}
    rows: list[dict[str, Any]] = []
    empty_texts: list[str] = []
    for sequence, tr in enumerate(matching[0].select("tbody tr"), start=1):
        cells = tr.find_all("td", recursive=False)
        if len(cells) == 1:
            text = _clean(cells[0].get_text(" ", strip=True))
            if text:
                empty_texts.append(text)
            continue
        if len(cells) != 10:
            raise GumiContractError(f"page {requested}: row width changed")
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        if not values[0].isdigit() or int(values[0]) < 1:
            raise GumiContractError(f"page {requested}: display number changed")
        institution, category = values[1], values[2]
        if institution not in site_map:
            raise GumiContractError(f"page {requested}: row escaped institution registry")
        anchors = cells[4].select("a[onclick]")
        if len(anchors) != 1:
            raise GumiContractError(f"page {requested}: detail control changed")
        view = _VIEW.fullmatch(_clean(anchors[0].get("onclick")))
        if view is None or _clean(anchors[0].get("href")) != "javascript:void(0)":
            raise GumiContractError(f"page {requested}: detail identity control changed")
        identity = view.group(1)
        selection_nodes = anchors[0].select(".lecture_recruit")
        if len(selection_nodes) != 1:
            raise GumiContractError(f"course {identity}: selection badge changed")
        selection = _clean(selection_nodes[0].get_text(" ", strip=True))
        if selection not in _SELECTIONS:
            raise GumiContractError(f"course {identity}: unknown selection method")
        anchor_text = _clean(anchors[0].get_text(" ", strip=True))
        if not anchor_text.startswith(selection):
            raise GumiContractError(f"course {identity}: selection/title shape changed")
        title = anchor_text[len(selection) :].strip()
        status = values[9]
        if not title or status not in _SOURCE_STATUSES:
            raise GumiContractError(f"course {identity}: title/status changed")
        capacity = _CAPACITY_LIST.fullmatch(values[6])
        if capacity is None:
            raise GumiContractError(f"course {identity}: list capacity changed")
        start, end = _date_range(values[7], f"course {identity} education period", allow_reversed=True)
        apply_start, apply_end = _date_range(values[8], f"course {identity} application period")
        rows.append(
            {
                "identity": identity,
                "list_page": requested,
                "list_sequence": sequence,
                "display_number": int(values[0]),
                "institution": institution,
                "category": category,
                # Retired historical classifications can remain in the full
                # ledger after disappearing from the current search dropdown.
                # Current/future rows are required to resolve below.
                "category_ids": category_map.get((institution, category), ()),
                "site_code": site_map[institution],
                "cohort": values[3],
                "title": title,
                "selection": selection,
                "fee": values[5],
                "capacity_current": int(capacity.group(1).replace(",", "")),
                "capacity_total": int(capacity.group(2).replace(",", "")),
                "education_period": values[7],
                "start": start,
                "end": end,
                "apply_period": values[8],
                "apply_start": apply_start,
                "apply_end": apply_end,
                "source_status": status,
                "status": _SOURCE_STATUSES[status],
            }
        )
    structural_empty = bool(empty_texts)
    if structural_empty and (rows or empty_texts != ["신청가능한 강좌가 없습니다."]):
        raise GumiContractError(f"page {requested}: structural empty row changed")
    if requested <= last:
        if structural_empty:
            raise GumiContractError(f"page {requested}: declared data page is empty")
        expected = GUMI_PAGE_SIZE if requested < last else ((total - 1) % GUMI_PAGE_SIZE + 1)
        if len(rows) != expected:
            raise GumiContractError(f"page {requested}: row count/cardinality changed")
    elif not structural_empty or rows:
        raise GumiContractError(f"page {requested}: post-last page is not structurally empty")
    identities = [row["identity"] for row in rows]
    if len(identities) != len(set(identities)):
        raise GumiContractError(f"page {requested}: duplicate identities")
    return _Page(
        requested,
        observed,
        last,
        total,
        tuple(rows),
        site_registry,
        category_registry,
        structural_empty,
    )


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        page.site_registry,
        page.category_registry,
        tuple(
            (
                row["identity"],
                row["display_number"],
                row["institution"],
                row["category"],
                row["category_ids"],
                row["cohort"],
                row["title"],
                row["selection"],
                row["fee"],
                row["capacity_current"],
                row["capacity_total"],
                row["education_period"],
                row["apply_period"],
                row["source_status"],
            )
            for row in page.rows
        ),
    )


def _experience_reason(row: Mapping[str, Any]) -> str:
    if "체험" in _clean(row.get("category")) or "체험" in _clean(row.get("title")):
        return "experience_category_or_title"
    return ""


def _detail_fields(soup: BeautifulSoup, identity: str) -> dict[str, str]:
    matching = [table for table in soup.select("table") if {"table", "type2"} <= set(table.get("class", []))]
    if len(matching) != 1:
        raise GumiContractError(f"course {identity}: detail table changed")
    fields: dict[str, str] = {}
    labels: set[str] = set()
    for tr in matching[0].select("tr"):
        children = tr.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(children):
            if children[index].name != "th" or index + 1 >= len(children) or children[index + 1].name != "td":
                raise GumiContractError(f"course {identity}: detail field pairing changed")
            label = _clean(children[index].get_text(" ", strip=True))
            if not label or label in labels or label not in (_DETAIL_SAFE_FIELDS | _DETAIL_DISCARDED_FIELDS):
                raise GumiContractError(f"course {identity}: detail field set changed: {label}")
            labels.add(label)
            if label not in _DETAIL_DISCARDED_FIELDS:
                value = _clean(children[index + 1].get_text(" ", strip=True))
                if label != "교육장소" and (_PHONE.search(value) or _EMAIL.search(value)):
                    raise GumiContractError(f"course {identity}: contact-like data entered safe field {label}")
                fields[label] = value
            index += 2
    if not _DETAIL_REQUIRED_FIELDS <= labels:
        raise GumiContractError(f"course {identity}: required detail fields missing")
    capacity_labels = labels & {"신청/접수", "신청/접수/대기"}
    if len(capacity_labels) != 1:
        raise GumiContractError(f"course {identity}: detail capacity field changed")
    return fields


def _action_identity(url: str, path: str, key_name: str, identity: str) -> bool:
    parsed = urlparse(urljoin(f"https://{GUMI_HOST}/", url))
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GUMI_HOST
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and parsed.path == path
        and pairs == [(key_name, identity), ("key", "208")]
        and not parsed.fragment
    )


def _controls(
    soup: BeautifulSoup,
    identity: str,
    expected_site: str,
    expected_categories: Iterable[str],
) -> dict[str, Any]:
    script = "\n".join(node.get_text() for node in soup.select("script"))
    sites, categories = set(_SITE_VALUE.findall(script)), set(_CATEGORY_VALUE.findall(script))
    if sites != {expected_site} or len(categories) != 1 or not categories <= set(expected_categories):
        raise GumiContractError(f"course {identity}: branch/category action binding changed")
    actions = _ACTION.findall(script)
    relevant: dict[str, list[str]] = {"write": [], "modify": [], "lottery": []}
    for action in actions:
        parsed = urlparse(urljoin(f"https://{GUMI_HOST}/", action))
        if parsed.path == GUMI_APPLICATION_PATH:
            relevant["write"].append(action)
            if not _action_identity(action, GUMI_APPLICATION_PATH, "prmIdx", identity):
                raise GumiContractError(f"course {identity}: application action identity changed")
        elif parsed.path == "/reservation/www/edu/app/modify.do":
            relevant["modify"].append(action)
            if not _action_identity(action, parsed.path, "prmIdx", identity):
                raise GumiContractError(f"course {identity}: modify action identity changed")
        elif parsed.path == "/reservation/www/edu/program/lottery/result.do":
            relevant["lottery"].append(action)
            if not _action_identity(action, parsed.path, "idx", identity):
                raise GumiContractError(f"course {identity}: lottery action identity changed")
    if any(len(set(values)) != len(values) or len(values) > 1 for values in relevant.values()):
        raise GumiContractError(f"course {identity}: duplicate identity action")
    if len(relevant["lottery"]) != 1:
        raise GumiContractError(f"course {identity}: lottery identity action missing")
    buttons = soup.select(".btn_wrap a")
    apply_buttons = [node for node in buttons if _clean(node.get_text(" ", strip=True)) == "신청하기"]
    lottery_buttons = [node for node in buttons if "당첨" in _clean(node.get_text(" ", strip=True))]
    list_buttons = [node for node in buttons if _clean(node.get_text(" ", strip=True)) == "목록"]
    if len(apply_buttons) > 1 or len(lottery_buttons) > 1 or len(list_buttons) != 1:
        raise GumiContractError(f"course {identity}: visible detail controls changed")
    visible_apply = bool(apply_buttons)
    if visible_apply:
        onclick = re.sub(r"\s+", "", _clean(apply_buttons[0].get("onclick")))
        if onclick != "go_apply();returnfalse;" or len(relevant["write"]) != 1:
            raise GumiContractError(f"course {identity}: visible application control identity changed")
    if lottery_buttons:
        onclick = re.sub(r"\s+", "", _clean(lottery_buttons[0].get("onclick")))
        if onclick != "go_lot_result();returnfalse;":
            raise GumiContractError(f"course {identity}: visible lottery control changed")
    list_onclick = re.sub(r"\s+", "", _clean(list_buttons[0].get("onclick")))
    if list_onclick != "go_list();returnfalse;":
        raise GumiContractError(f"course {identity}: list control changed")
    return {
        "visible_apply": visible_apply,
        "write_action": bool(relevant["write"]),
        "lottery_button": bool(lottery_buttons),
        "category_id": next(iter(categories)),
    }


def _sanitize_venue(value: str) -> str:
    result = _EMAIL.sub("", _PHONE.sub("", _clean(value)))
    result = result.replace("☎", " ")
    result = re.sub(r"\(\s*\)", " ", result)
    result = re.sub(r"\s+([,/])", r"\1", result)
    return _clean(result).strip(" ,/")


def _same_title(listed: Mapping[str, Any], detail_value: str, fields: Mapping[str, str]) -> bool:
    selection = _clean(listed.get("selection"))
    if not detail_value.startswith(selection):
        return False
    detail_title = detail_value[len(selection) :].strip()
    difficulty = _clean(fields.get("난이도"))
    if difficulty and difficulty != "없음":
        detail_title = f"{detail_title} ({difficulty})"
    return _normalized(detail_title) == _normalized(listed.get("title"))


def _parse_detail(listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    fields = _detail_fields(soup, identity)
    if not _same_title(listed, fields["강좌명/시간대"], fields):
        raise GumiContractError(f"course {identity}: list/detail title identity drift")
    if _date_values(fields["교육 기간"]) != (listed["start"], listed["end"]):
        raise GumiContractError(f"course {identity}: list/detail education period drift")
    if _date_values(fields["접수 기간"]) != (listed["apply_start"], listed["apply_end"]):
        raise GumiContractError(f"course {identity}: list/detail application dates drift")
    if _time_values(fields["접수 기간"]) != _time_values(listed["apply_period"]):
        raise GumiContractError(f"course {identity}: list/detail application times drift")
    if _normalized(fields["수강료"]) != _normalized(listed["fee"]):
        raise GumiContractError(f"course {identity}: list/detail fee drift")
    capacity_label = next(label for label in ("신청/접수", "신청/접수/대기") if label in fields)
    capacity = _CAPACITY_DETAIL.fullmatch(fields[capacity_label])
    if capacity is None:
        raise GumiContractError(f"course {identity}: detail capacity changed")
    first = int(capacity.group(1).replace(",", ""))
    second = int(capacity.group(2).replace(",", ""))
    third = int((capacity.group(3) or "0").replace(",", ""))
    if capacity_label == "신청/접수":
        current, total, wait, wait_total = first, second, 0, 0
        if (current, total) != (listed["capacity_current"], listed["capacity_total"]):
            raise GumiContractError(f"course {identity}: list/detail capacity drift")
    else:
        # 신청/접수/대기 means cumulative applicants / course quota / wait
        # quota.  Cumulative applicants can exceed both quotas after
        # cancellation, rejection, or review, so their excess is not a safe
        # current-waiting count.  Preserve the two official quotas, retain the
        # stable list count, and leave the unobserved current wait count unset.
        current = int(listed["capacity_current"])
        total = int(listed["capacity_total"])
        wait = None
        wait_total = third
        if total != second or current > first:
            raise GumiContractError(f"course {identity}: list/detail wait-capacity drift")
    controls = _controls(soup, identity, str(listed["site_code"]), listed["category_ids"])
    status = _clean(listed.get("status"))
    if status == "OPEN" and not controls["visible_apply"]:
        raise GumiContractError(f"course {identity}: open course lacks visible application control")
    actionable = status == "OPEN" and controls["visible_apply"]
    inactive_visible = status != "OPEN" and controls["visible_apply"]
    venue = _sanitize_venue(fields["교육장소"])
    target = _clean(fields["교육 대상"])
    title = _clean(listed.get("title"))
    if any(_PHONE.search(value) or _EMAIL.search(value) for value in (title, target, venue)):
        raise GumiContractError(f"course {identity}: contact-like data survived safe-field processing")
    branch = _clean(listed.get("institution"))
    site_code = _clean(listed.get("site_code"))
    application_url = gumi_application_url(identity) if actionable else ""
    is_experience = bool(_experience_reason(listed))
    row: dict[str, Any] = {
        "provider": GUMI_PROVIDER,
        "provider_course_id": f"{GUMI_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": f"GUMI_{site_code}",
        "preserve_branch": True,
        "category": _clean(listed.get("category")),
        "program_type": "체험" if is_experience else "교육",
        "raw_url": gumi_detail_url(identity),
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION_LOGIN_REQUIRED" if actionable else "INFO_ONLY",
        "application_method": _clean(listed.get("selection")),
        "application_methods": [_clean(listed.get("selection"))],
        "reservation_available": actionable,
        "status": status,
        "fee": _clean(listed.get("fee")),
        "period": _clean(fields["교육 기간"]),
        "start_date": listed["start"].isoformat(),
        "end_date": listed["end"].isoformat(),
        "apply_period": _clean(fields["접수 기간"]),
        "apply_start": listed["apply_start"].isoformat(),
        "apply_end": listed["apply_end"].isoformat(),
        "schedule_raw": _clean(fields["강의 시간"]),
        "capacity": f"{total}명",
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_current": wait,
        "waitlist_total": wait_total,
        "target": target,
        "venue": venue,
        "venue_name": branch,
        "collection_category": "공공예약",
        "domain_category": "체험·견학" if is_experience else "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험" if is_experience else "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GUMI_PARSER,
        "municipality_code": GUMI_MUNICIPALITY_CODE,
        "municipality_full_name": GUMI_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": int(listed["list_page"]),
            "display_number": int(listed["display_number"]),
            "source_institution": branch,
            "source_category": _clean(listed.get("category")),
            "source_cohort": _clean(listed.get("cohort")),
            "source_status": _clean(listed.get("source_status")),
            "source_selection": _clean(listed.get("selection")),
            "source_fee": _clean(listed.get("fee")),
            "source_education_period": _clean(listed.get("education_period")),
            "source_apply_period": _clean(listed.get("apply_period")),
            "source_site_code": site_code,
            "source_category_id": _clean(controls["category_id"]),
            "source_venue": venue,
            "branch_basis": "official_integrated_ledger_institution",
            "detail_verified": True,
            "application_control_present": actionable,
            "inactive_visible_application_control": inactive_visible,
            "lottery_control_present": controls["lottery_button"],
            "education_scope_verified": not is_experience,
            "experience_scope_verified": is_experience,
            "service_family": "experience" if is_experience else "education",
        },
    }
    return row


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden detail/PII key persisted")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr({key: value for key, value in row.items() if key not in {"raw_url", "application_url"}})
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail content persisted")
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


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now().astimezone().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("today must be an ISO date")


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "declared_last_page": 0,
        "post_last_empty_page": 0,
        "boundary_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "date_current_count": 0,
        "current_candidate_count": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "experience_excluded_count": 0,
        "current_experience_excluded_count": 0,
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
        "municipality_code": GUMI_MUNICIPALITY_CODE,
        "municipality_name": GUMI_MUNICIPALITY_NAME,
        "owner_provider": GUMI_PROVIDER,
        "canonical_candidate_id": GUMI_CANONICAL_CANDIDATE_ID,
        "canonical_url": GUMI_CANONICAL_URL,
        "go74_alias_provider": GUMI_GO74_ALIAS_PROVIDER,
        "go74_alias_candidate_id": GUMI_GO74_ALIAS_CANDIDATE_ID,
        "excluded_alias_count": len(GUMI_EXCLUDED_FILTER_ALIASES),
        "boundary_mode": "declared pages plus exact structural empty sentinel and stable first/last rechecks",
    }


def collect_gumi_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    max_workers: int = GUMI_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future, education-only Gumi snapshot."""

    meta = _base_meta()
    if not is_gumi_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Gumi integrated education owner"
        return [], GUMI_PARSER, meta
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < minimum
            for value, minimum in ((timeout, 1), (max_pages, 1), (detail_limit, 0), (max_workers, 1))
        )
        or max_workers > 32
    ):
        meta.update({"source_cap_reached": True, "configured_collection_error": "invalid collection limits"})
        return [], GUMI_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], GUMI_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    workers = min(max_workers, GUMI_MAX_WORKERS)
    errors: list[str] = []
    result: list[dict[str, Any]] = []

    def fetch_page(number: int) -> _Page:
        return _parse_page(_request_soup(gumi_list_url(number), timeout, factory, current_fetcher), number)

    try:
        first = fetch_page(1)
        meta["list_requests"] = meta["pages"] = 1
        last, total = first.last, first.total
        meta["declared_last_page"] = last
        if last > max_pages:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": f"max_pages cap allows {max_pages} of {last} declared pages",
                }
            )
            return [], GUMI_PARSER, meta
        pages: dict[int, _Page] = {1: first}
        if last > 1:
            with ThreadPoolExecutor(max_workers=min(workers, last - 1)) as pool:
                fetched = list(pool.map(fetch_page, range(2, last + 1)))
            pages.update({page.requested: page for page in fetched})
            meta["list_requests"] += len(fetched)
            meta["pages"] += len(fetched)
        for page in pages.values():
            if page.last != last or page.total != total:
                raise GumiContractError("declared total/page boundary changed during traversal")
            if page.site_registry != first.site_registry or page.category_registry != first.category_registry:
                raise GumiContractError("institution/category registry changed during traversal")
        sentinel = fetch_page(last + 1)
        meta["list_requests"] += 1
        meta["pages"] += 1
        meta["post_last_empty_page"] = last + 1
        if not sentinel.structural_empty or sentinel.last != last or sentinel.total != total:
            raise GumiContractError("post-last exact empty boundary changed")
        boundaries = [1] if last == 1 else [1, last]
        with ThreadPoolExecutor(max_workers=len(boundaries)) as pool:
            rechecks = list(pool.map(fetch_page, boundaries))
        meta["list_requests"] += len(rechecks)
        meta["pages"] += len(rechecks)
        meta["boundary_rechecks"] = len(rechecks)
        for recheck in rechecks:
            if _page_signature(recheck) != _page_signature(pages[recheck.requested]):
                raise GumiContractError(f"page {recheck.requested}: boundary stability recheck changed")
        required = last + 1 + len(boundaries)
        meta["required_list_requests"] = required
        listed = [row for page in range(1, last + 1) for row in pages[page].rows]
        identities = [row["identity"] for row in listed]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            raise GumiContractError(f"{duplicate_count} duplicate identities across catalogue pages")
        numbers = [row["display_number"] for row in listed]
        if len(listed) != total or numbers != list(range(total, 0, -1)):
            raise GumiContractError("complete descending ledger cardinality/number boundary changed")
        list_complete = meta["list_requests"] == required
        date_current = [row for row in listed if row["end"] >= cutoff]
        experience = [row for row in listed if _experience_reason(row)]
        current_experience = [row for row in date_current if _experience_reason(row)]
        current = list(date_current)
        if any(row["end"] < row["start"] for row in current):
            raise GumiContractError("current/future education row has a reversed education period")
        unresolved_current_categories = [row for row in date_current if not row["category_ids"]]
        if unresolved_current_categories:
            raise GumiContractError("current/future row escaped the official category registry")
        meta.update(
            {
                "data_pages": last,
                "source_total": total,
                "source_rows": len(listed),
                "identity_duplicate_count": duplicate_count,
                "source_institution_counts": dict(sorted(Counter(row["institution"] for row in listed).items())),
                "source_status_counts": dict(Counter(row["source_status"] for row in listed)),
                "source_selection_counts": dict(Counter(row["selection"] for row in listed)),
                "date_current_count": len(date_current),
                "current_candidate_count": len(current),
                "expired_count": len(listed) - len(date_current),
                "experience_source_count": len(experience),
                "current_experience_count": len(current_experience),
                "experience_classification_counts": dict(Counter(_experience_reason(row) for row in experience)),
                "experience_excluded_count": 0,
                "current_experience_excluded_count": 0,
                "pagination_complete": list_complete,
                "site_registry": dict(first.site_registry),
                "category_registry_count": len(first.category_registry),
                "retired_category_source_rows": sum(not row["category_ids"] for row in listed),
                "source_reversed_education_period_count": sum(row["end"] < row["start"] for row in listed),
            }
        )
        if not list_complete:
            raise GumiContractError("list request boundary incomplete")
        if len(current) > detail_limit:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": f"detail_limit cap allows {detail_limit} of {len(current)} current/future program details",
                }
            )
            return [], GUMI_PARSER, meta
        meta["detail_attempts"] = len(current)

        def fetch_detail(listed_row: Mapping[str, Any]) -> tuple[Optional[dict[str, Any]], str]:
            identity = _clean(listed_row.get("identity"))
            try:
                soup = _request_soup(gumi_detail_url(identity), timeout, factory, current_fetcher)
                return _parse_detail(listed_row, soup, cutoff), ""
            except Exception as exc:
                return None, f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"

        detailed: list[dict[str, Any]] = []
        if current:
            with ThreadPoolExecutor(max_workers=min(workers, len(current))) as pool:
                detail_results = list(pool.map(fetch_detail, current))
            for row, error in detail_results:
                if error:
                    errors.append(error)
                    meta["detail_errors"] += 1
                elif row is not None:
                    detailed.append(row)
                    meta["detail_pages"] += 1
                    meta["pages"] += 1
        details_complete = not errors and len(detailed) == len(current) == meta["detail_pages"]
        controls_complete = details_complete and all(
            bool(row.get("raw_fields", {}).get("detail_verified")) for row in detailed
        )
        if details_complete and controls_complete:
            for row in detailed:
                errors.extend(_privacy_errors(row))
            if not errors:
                try:
                    result = list((dedupe_rows or _dedupe_default)(detailed))
                except Exception as exc:
                    errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                if len(result) != len(detailed):
                    errors.append(f"dedupe changed official identity cardinality {len(detailed)} to {len(result)}")
                    result = []
        snapshot_complete = list_complete and details_complete and controls_complete and not errors
        if not snapshot_complete:
            result = []
        meta.update(
            {
                "current_source_count": len(detailed),
                "branch_counts": dict(sorted(Counter(row["branch"] for row in result).items())),
                "status_counts": dict(Counter(row["status"] for row in result)),
                "domain_category_counts": dict(Counter(row["domain_category"] for row in result)),
                "service_group_counts": dict(Counter(row["service_group"] for row in result)),
                "application_control_count": sum(
                    bool(row.get("raw_fields", {}).get("application_control_present")) for row in detailed
                ),
                "visible_application_control_count": sum(
                    bool(row.get("raw_fields", {}).get("application_control_present"))
                    or bool(row.get("raw_fields", {}).get("inactive_visible_application_control"))
                    for row in detailed
                ),
                "inactive_visible_application_control_count": sum(
                    bool(row.get("raw_fields", {}).get("inactive_visible_application_control")) for row in detailed
                ),
                "details_complete": details_complete,
                "application_controls_complete": controls_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": snapshot_complete,
                "returned_count": len(result),
                "no_current_data": bool(snapshot_complete and not current),
                "no_current_reason": "no current/future education or experience programs"
                if snapshot_complete and not current
                else "",
                "municipality_coverage": [GUMI_MUNICIPALITY_CODE],
                "excluded_filter_aliases": [dict(item) for item in GUMI_EXCLUDED_FILTER_ALIASES],
                "owner_boundary_audit": {key: dict(value) for key, value in GUMI_OWNER_BOUNDARY_AUDIT.items()},
                "pii_fields_never_persisted": list(GUMI_PII_FIELDS_NEVER_PERSISTED),
                "pii_payload_persisted": False,
                "forbidden_applicant_endpoint_requests": 0,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return result, GUMI_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["pagination_complete"] = False
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], GUMI_PARSER, meta


collect = collect_gumi_education


__all__ = [
    "GUMI_CANONICAL_CANDIDATE_ID",
    "GUMI_CANONICAL_URL",
    "GUMI_EXCLUDED_FILTER_ALIASES",
    "GUMI_GO74_ALIAS_CANDIDATE_ID",
    "GUMI_GO74_ALIAS_PROVIDER",
    "GUMI_HOST",
    "GUMI_LIST_PATH",
    "GUMI_MUNICIPALITY_CODE",
    "GUMI_MUNICIPALITY_NAME",
    "GUMI_OWNER_BOUNDARY_AUDIT",
    "GUMI_PARSER",
    "GUMI_PII_FIELDS_NEVER_PERSISTED",
    "GUMI_PROVIDER",
    "GUMI_SITE_REGISTRY",
    "GumiContractError",
    "collect",
    "collect_gumi_education",
    "gumi_application_url",
    "gumi_detail_url",
    "gumi_list_url",
    "is_gumi_education_target",
    "is_target",
]
