"""Fail-closed collector for Yesan-gun's official lifelong-course catalogue.

The reviewed search results point at a category-filtered list, the lifelong
learning homepage, a one-off announcement, and two general information pages.
Only the same official list *without* ``searchFld=06`` is a complete catalogue.

The collector reads every advertised list page, the immediate empty page after
the last page, and page one a second time.  It then validates every current or
future row against its course-bound detail page.  A partial snapshot is never
returned.  Contact details, instructor data, attachments, free-form detail
copy, and source HTML are deliberately not persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YESAN_PROVIDER = "MUNI_WWW_YESAN_GO_KR_AC1B96E1"
YESAN_CANONICAL_CANDIDATE_ID = "MUNI_IR_DA028F76EEF2"
YESAN_HOST = "www.yesan.go.kr"
YESAN_MUNICIPALITY_CODE = "4481000000"
YESAN_MUNICIPALITY_NAME = "충청남도 예산군"
YESAN_LIST_PATH = "/prog/lctr/edu/sub01_01/list.do"
YESAN_DETAIL_PATH = "/prog/lctr/edu/sub01_01/view.do"
YESAN_APPLICATION_PATH = "/prog/lctrAplcnt/edu/sub01_01/write.do"
YESAN_CANONICAL_URL = f"https://{YESAN_HOST}{YESAN_LIST_PATH}"
YESAN_PAGE_SIZE = 20
YESAN_FETCH_ATTEMPTS = 2
YESAN_MAX_WORKERS = 12
YESAN_MAX_HTML_BYTES = 4_000_000
YESAN_PARSER = (
    "yesan_official_unfiltered_lifelong_courses+all_pages+empty_sentinel+"
    "stable_page1+exact_pseudo_course_exclusion+current_details+"
    "course_bound_application_control+pii_allowlist"
)
YESAN_OWNERSHIP_SCOPE = "yesan_official_unfiltered_lifelong_course_catalogue"

YESAN_COMPUTER_SUBSET_URL = (
    f"{YESAN_CANONICAL_URL}?searchFld=06"
)
YESAN_LIFELONG_HOMEPAGE_URLS = (
    f"https://{YESAN_HOST}/edu",
    f"https://{YESAN_HOST}/edu/",
)
YESAN_ANNOUNCEMENT_URL = (
    "https://www.yesan.go.kr/bbs/BBSMSTR_000000000046/view.do?"
    "nttId=B000000170637Zn0pI6"
)
YESAN_GENERAL_HOMEPAGE_URLS = (
    f"https://{YESAN_HOST}/",
    f"https://{YESAN_HOST}/index.jsp",
)
YESAN_PROVINCIAL_OVERVIEW_URL = (
    "https://www.chungnam.go.kr/cnportal/main/contents.do?menuNo=500970"
)

YESAN_ALIAS_PROVIDERS = frozenset(
    {
        "MUNI_WWW_YESAN_GO_KR_08022095",
        "MUNI_WWW_YESAN_GO_KR_DB7F84C1",
        "MUNI_WWW_YESAN_GO_KR_EBB76471",
    }
)
YESAN_ALIAS_CANDIDATE_IDS = frozenset(
    {
        "MUNI_IR_115D0BDDBCD1",
        "MUNI_IR_4656E90DB7E2",
        "MUNI_IR_DB56FB51A33C",
    }
)
YESAN_EXCLUDED_CANDIDATE_IDS = frozenset(
    {"MUNI_IR_65D9986F213A", "MUNI_IR_87C88406967B"}
)

# Exhaustive decision table for all five promotion-review candidates.  The
# canonical unfiltered URL was discovered by following the official homepage
# and is intentionally represented separately above.
YESAN_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_IR_115D0BDDBCD1": {
        "decision": "subset_category_alias",
        "provider": "MUNI_WWW_YESAN_GO_KR_DB7F84C1",
        "url": YESAN_COMPUTER_SUBSET_URL,
        "owner": YESAN_PROVIDER,
        "reason": "searchFld=06 contains only the computer category",
    },
    "MUNI_IR_4656E90DB7E2": {
        "decision": "excluded_single_announcement_evidence_only",
        "provider": "MUNI_WWW_YESAN_GO_KR_EBB76471",
        "url": YESAN_ANNOUNCEMENT_URL,
        "owner": YESAN_PROVIDER,
        "reason": "one recruitment notice links to the canonical catalogue",
    },
    "MUNI_IR_65D9986F213A": {
        "decision": "excluded_general_county_homepage",
        "provider": "MUNI_WWW_YESAN_GO_KR_86A2A4FF",
        "url": f"https://{YESAN_HOST}/index.jsp",
        "owner": "",
        "reason": "general county homepage has no course catalogue",
    },
    "MUNI_IR_87C88406967B": {
        "decision": "excluded_provincial_static_overview",
        "provider": "MUNI_WWW_CHUNGNAM_GO_KR_AB6BAFD7",
        "url": YESAN_PROVINCIAL_OVERVIEW_URL,
        "owner": "",
        "reason": "static provincial municipality profile has no courses",
    },
    "MUNI_IR_DB56FB51A33C": {
        "decision": "subset_homepage_alias",
        "provider": "MUNI_WWW_YESAN_GO_KR_08022095",
        "url": f"https://{YESAN_HOST}/edu/",
        "owner": YESAN_PROVIDER,
        "reason": "portal homepage exposes only featured/latest course cards",
    },
}

YESAN_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": YESAN_CANONICAL_URL,
    "canonical_candidate_id": YESAN_CANONICAL_CANDIDATE_ID,
    "computer_subset_historical_rows": 38,
    "computer_subset_pages": 2,
    "unfiltered_historical_rows": 856,
    "unfiltered_pages": 43,
    "immediate_empty_page": 44,
    "current_or_future_rows": 22,
    "current_details_verified": 22,
    "conclusion": "canonical_complete_catalogue_supersedes_three_partial_yaml_owners",
}

YESAN_PII_FIELDS_DISCARDED = (
    "문의전화",
    "담당자",
    "담당자명",
    "강사",
    "강사명",
    "전화번호",
    "휴대전화",
    "이메일",
    "첨부파일",
    "교재",
    "상세내용",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_TOTAL_RE = re.compile(
    r"전체\s*게시물\s*검색\s*총\s*([\d,]+)\s*건의\s*강좌가\s*검색되었습니다"
)
_VIEW_RE = re.compile(
    r"^\s*fn_search_view\(['\"](\d+)['\"]\);\s*return\s+false;?\s*$"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_WRITE_HANDLER_RE = re.compile(
    r"\.button_write[^\n]*?\.click\s*\(\s*function\s*\([^)]*\)\s*\{.*?"
    r"fn_submit\s*\(\s*['\"]"
    + re.escape(YESAN_APPLICATION_PATH)
    + r"['\"]\s*\)",
    flags=re.DOTALL,
)

_LIST_FIELDS = frozenset(
    {"모집기간", "교육기간", "교육대상", "교육시간", "강의장소", "주최기관"}
)
_LIST_CORE_FIELDS = frozenset(
    {"모집기간", "교육기간", "교육대상", "강의장소", "주최기관"}
)
_DETAIL_REQUIRED_FIELDS = (
    "모집기간",
    "교육기간",
    "교육대상",
    "교육시간",
    "강의장소",
)
_RECRUITMENT_STATUS_MAP: Mapping[str, str] = {
    "모집중": "OPEN",
    "접수중": "OPEN",
    "신청중": "OPEN",
    "모집예정": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "신청예정": "SCHEDULED",
    "모집마감": "CLOSED",
    "접수마감": "CLOSED",
    "신청마감": "CLOSED",
    "모집취소": "CLOSED",
    "접수취소": "CLOSED",
}
_EDUCATION_STATUSES = frozenset(
    {"교육예정", "교육중", "교육마감", "교육취소", "폐강"}
)
_CANCELLED_EDUCATION_STATUSES = frozenset({"교육취소", "폐강"})
_RECRUITMENT_MODES = frozenset({"온라인모집", "오프라인모집"})
_SELECTION_METHODS = frozenset({"선착", "추첨"})
_EXCLUDED_PSEUDO_COURSE_IDENTITIES = frozenset({"895"})

_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "source_place",
        "source_category",
        "source_recruitment_mode",
        "source_recruitment_status",
        "source_education_status",
        "source_selection_method",
        "source_period",
        "source_application_period",
        "source_schedule",
        "source_target",
        "source_venue",
        "source_fee_omitted",
        "source_capacity_current",
        "source_capacity_total",
        "education_institution",
        "service_family",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
        "detail_verified",
        "excluded_pseudo_course",
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
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)


class YesanContractError(ValueError):
    """Raised when the official source no longer satisfies its contract."""


@dataclass
class _ListPage:
    rows: list[dict[str, Any]]
    total: int
    last: int
    errors: list[str]


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
        or not parsed.hostname
        or parsed.fragment
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        return ""
    hostname = parsed.hostname.lower()
    pairs = sorted(parse_qsl(parsed.query, keep_blank_values=True))
    return f"https://{hostname}{parsed.path}" + (
        f"?{urlencode(pairs)}" if pairs else ""
    )


def is_yesan_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == YESAN_PROVIDER
        and _canonical_compare_url(_target_value(target, "url"))
        == YESAN_CANONICAL_URL
    )


def is_yesan_owned_alias_target(target: Any) -> bool:
    provider = _clean(_target_value(target, "provider"))
    candidate_id = _clean(_target_value(target, "candidate_id"))
    compared = _canonical_compare_url(_target_value(target, "url"))
    alias_urls = {
        _canonical_compare_url(YESAN_COMPUTER_SUBSET_URL),
        *(_canonical_compare_url(item) for item in YESAN_LIFELONG_HOMEPAGE_URLS),
        _canonical_compare_url(YESAN_ANNOUNCEMENT_URL),
    }
    return bool(
        provider in YESAN_ALIAS_PROVIDERS
        or candidate_id in YESAN_ALIAS_CANDIDATE_IDS
        or compared in alias_urls
    )


def is_yesan_excluded_candidate(target: Any) -> bool:
    candidate_id = _clean(_target_value(target, "candidate_id"))
    compared = _canonical_compare_url(_target_value(target, "url"))
    excluded_urls = {
        *(_canonical_compare_url(item) for item in YESAN_GENERAL_HOMEPAGE_URLS),
        _canonical_compare_url(YESAN_PROVINCIAL_OVERVIEW_URL),
    }
    return candidate_id in YESAN_EXCLUDED_CANDIDATE_IDS or compared in excluded_urls


def yesan_list_url(page: Any = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return ""
    if page == 1:
        return YESAN_CANONICAL_URL
    return f"{YESAN_CANONICAL_URL}?{urlencode({'pageIndex': page})}"


def yesan_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not re.fullmatch(r"\d+", value):
        return ""
    return f"https://{YESAN_HOST}{YESAN_DETAIL_PATH}?{urlencode({'lctrNo': value})}"


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MooncenMunicipalAudit/1.0; "
                "+https://www.yesan.go.kr/)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    final = urlparse(_clean(getattr(response, "url", url)))
    if final.scheme.lower() != "https" or final.hostname != YESAN_HOST:
        raise ValueError("response left the official HTTPS host")
    content_type = _clean(response.headers.get("Content-Type")).lower()
    if "html" not in content_type:
        raise ValueError("response is not HTML")
    content = response.content
    if len(content) > YESAN_MAX_HTML_BYTES:
        raise ValueError("HTML response exceeded the bounded size limit")
    return BeautifulSoup(content, "html.parser")


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
        if len(value) > YESAN_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > YESAN_MAX_HTML_BYTES:
            raise ValueError("fixture HTML exceeded the bounded size limit")
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return _coerce_soup(bytes(content))
    raise TypeError("fetcher must return HTML, bytes, a response, or BeautifulSoup")


def _fetch_parse_many(
    items: Iterable[tuple[Any, str, Callable[[BeautifulSoup], Any]]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, Any], list[str]]:
    tasks = list(items)
    if not tasks:
        return {}, []

    def worker(
        key: Any, url: str, parser: Callable[[BeautifulSoup], Any]
    ) -> tuple[Any, Any]:
        last_error: Optional[Exception] = None
        for _attempt in range(YESAN_FETCH_ATTEMPTS):
            session = session_factory()
            try:
                return key, parser(_coerce_soup(fetcher(session, url, timeout)))
            except Exception as exc:
                last_error = exc
            finally:
                _close_quietly(session)
        raise RuntimeError(_clean(last_error))

    results: dict[Any, Any] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        futures = {
            executor.submit(worker, key, url, parser): key
            for key, url, parser in tasks
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                result_key, value = future.result()
                results[result_key] = value
            except Exception as exc:
                errors.append(f"{key}: {_clean(exc)}")
    return results, errors


def _date_pair(value: Any, field: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise YesanContractError(f"{field}: expected exactly two dates")
    parsed: list[date] = []
    for year, month, day_value in matches:
        try:
            parsed.append(date(int(year), int(month), int(day_value)))
        except ValueError as exc:
            raise YesanContractError(f"{field}: invalid calendar date") from exc
    if parsed[0] > parsed[1]:
        raise YesanContractError(f"{field}: reversed dates")
    return parsed[0], parsed[1]


def _integer(value: Any, field: str) -> int:
    match = re.search(r"[\d,]+", _clean(value))
    if match is None:
        raise YesanContractError(f"{field}: integer missing")
    return int(match.group().replace(",", ""))


def _classes(node: Any) -> set[str]:
    return {_clean(value) for value in (node.get("class") or []) if _clean(value)}


def _single_text(root: Any, selector: str, field: str) -> str:
    nodes = root.select(selector) if root is not None else []
    if len(nodes) != 1:
        raise YesanContractError(f"{field}: expected one node")
    value = _clean(nodes[0].get_text(" ", strip=True))
    if not value:
        raise YesanContractError(f"{field}: empty")
    return value


def _form_value(form: Any, name: str) -> tuple[int, str]:
    nodes = form.select(f'[name="{name}"]') if form is not None else []
    if len(nodes) != 1:
        return len(nodes), ""
    node = nodes[0]
    if node.name == "select":
        selected = node.select("option[selected]")
        option = selected[0] if len(selected) == 1 else node.select_one("option")
        return 1, _clean(option.get("value") if option is not None else "")
    return 1, _clean(node.get("value"))


def _list_form_errors(soup: BeautifulSoup, page: int) -> list[str]:
    errors: list[str] = []
    forms = soup.select("form#searchForm")
    if len(forms) != 1:
        return [f"page {page}: search form missing or duplicated"]
    form = forms[0]
    action = urlparse(_clean(form.get("action")))
    if _clean(form.get("method")).lower() != "post" or (
        action.path,
        action.query,
        action.fragment,
    ) != (YESAN_LIST_PATH, "", ""):
        errors.append(f"page {page}: search form method/action changed")
    count, value = _form_value(form, "pageIndex")
    if count != 1 or value != str(page):
        errors.append(f"page {page}: pageIndex echo changed")
    count, value = _form_value(form, "lctrNo")
    if count != 1 or value:
        errors.append(f"page {page}: list identity field is not empty")
    for name in (
        "searchEmd",
        "searchInst",
        "searchFld",
        "searchTrgt",
        "searchBgnDt",
        "searchEndDt",
        "searchSe",
        "searchKeyword",
    ):
        count, value = _form_value(form, name)
        if count != 1 or value:
            errors.append(f"page {page}: unfiltered form field {name} changed")
    return errors


def _field_pairs(card: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in card.select("ul.list-1st > li"):
        labels = item.select(":scope > .tit")
        values = item.select(":scope > .txt")
        if len(labels) != 1 or len(values) != 1:
            raise YesanContractError("list field label/value structure changed")
        label = _clean(labels[0].get_text(" ", strip=True))
        value = _clean(values[0].get_text(" ", strip=True))
        if not label or label in pairs:
            raise YesanContractError("list field is empty or duplicated")
        pairs[label] = value
    if not _LIST_CORE_FIELDS <= set(pairs) or not set(pairs) <= _LIST_FIELDS:
        raise YesanContractError("list field set changed")
    return pairs


def _base_row(
    *,
    identity: str,
    title: str,
    page: int,
    place: str,
    category: str,
    mode: str,
    recruitment_status: str,
    education_status: str,
    selection_method: str,
    pairs: Mapping[str, str],
    start: date,
    end: date,
    apply_start: date,
    apply_end: date,
    capacity_current: int,
    capacity_total: int,
) -> dict[str, Any]:
    normalized_status = _RECRUITMENT_STATUS_MAP[recruitment_status]
    organizer = _clean(pairs["주최기관"])
    branch = place or organizer or "예산군 평생학습"
    venue = _clean(pairs["강의장소"])
    target = _clean(pairs["교육대상"])
    schedule = _clean(pairs.get("교육시간"))
    return {
        "provider": YESAN_PROVIDER,
        "provider_course_id": f"{YESAN_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": f"yesan:{_normalized(branch)}",
        "preserve_branch": True,
        "provider_organizer": organizer,
        "category": category,
        "program_type": "교육",
        "raw_url": yesan_detail_url(identity),
        "application_url": "",
        "application_type": "INFO_ONLY",
        "application_method": selection_method,
        "application_methods": ["온라인" if mode == "온라인모집" else "방문"],
        "reservation_available": False,
        "status": normalized_status,
        "fee": "요금 별도 안내",
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": _clean(pairs["모집기간"]),
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": schedule,
        "capacity": f"{capacity_current}/{capacity_total}",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "target": target,
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": YESAN_PARSER,
        "municipality_code": YESAN_MUNICIPALITY_CODE,
        "municipality_full_name": YESAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": page,
            "source_place": place,
            "source_category": category,
            "source_recruitment_mode": mode,
            "source_recruitment_status": recruitment_status,
            "source_education_status": education_status,
            "source_selection_method": selection_method,
            "source_period": _clean(pairs["교육기간"]),
            "source_application_period": _clean(pairs["모집기간"]),
            "source_schedule": schedule,
            "source_target": target,
            "source_venue": venue,
            "source_fee_omitted": True,
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "education_institution": organizer,
            "service_family": "education",
            "application_control_present": False,
            "application_control_contract": "",
            "application_control_verified": False,
            "detail_verified": False,
            "excluded_pseudo_course": False,
        },
    }


def _is_excluded_pseudo_course(
    *,
    identity: str,
    title: str,
    place: str,
    category: str,
    mode: str,
    recruitment_status: str,
    education_status: str,
    selection_method: str,
    pairs: Mapping[str, str],
    start: date,
    end: date,
    apply_start: date,
    apply_end: date,
    capacity_current: int,
    capacity_total: int,
) -> bool:
    return bool(
        identity in _EXCLUDED_PSEUDO_COURSE_IDENTITIES
        and title == "★☆★☆★☆TEST★☆★☆★☆"
        and place == "내포신도시 평생학습센터"
        and category == "인문교양"
        and mode == "온라인모집"
        and recruitment_status == "모집중"
        and education_status == "교육예정"
        and selection_method == "선착"
        and (start.isoformat(), end.isoformat()) == ("2027-01-01", "2027-12-31")
        and (apply_start.isoformat(), apply_end.isoformat()) == ("2026-07-22", "2026-08-31")
        and _clean(pairs.get("교육대상")) == "성인"
        and not _clean(pairs.get("교육시간"))
        and _clean(pairs.get("강의장소")) == "삽교읍"
        and _clean(pairs.get("주최기관")) == "내포신도시 평생학습센터"
        and (capacity_current, capacity_total) == (2, 100)
    )


def _parse_card(card: Any, page: int, cutoff: date) -> dict[str, Any]:
    match = _VIEW_RE.fullmatch(_clean(card.get("onclick")))
    if match is None:
        raise YesanContractError("course identity onclick changed")
    identity = match.group(1)
    title = _single_text(card, ".title", f"course {identity} title")
    spans = card.select(".type-wrap > span")
    if len(spans) != 6:
        raise YesanContractError(f"course {identity}: type/status field count changed")
    if "place" not in _classes(spans[0]) or "type" not in _classes(spans[2]):
        raise YesanContractError(f"course {identity}: type/status order changed")
    if any("status" not in _classes(spans[index]) for index in (1, 3, 4, 5)):
        raise YesanContractError(f"course {identity}: status classes changed")
    place = _clean(spans[0].get_text(" ", strip=True)).strip("[] ")
    category = _clean(spans[1].get_text(" ", strip=True))
    mode = _clean(spans[2].get_text(" ", strip=True))
    recruitment_status = _clean(spans[3].get_text(" ", strip=True))
    education_status = _clean(spans[4].get_text(" ", strip=True))
    selection_method = _clean(spans[5].get_text(" ", strip=True))
    if not category or mode not in _RECRUITMENT_MODES:
        raise YesanContractError(f"course {identity}: category/mode changed")
    if recruitment_status not in _RECRUITMENT_STATUS_MAP:
        raise YesanContractError(f"course {identity}: recruitment status changed")
    if education_status not in _EDUCATION_STATUSES:
        raise YesanContractError(f"course {identity}: education status changed")
    if selection_method and selection_method not in _SELECTION_METHODS:
        raise YesanContractError(f"course {identity}: selection method changed")
    pairs = _field_pairs(card)
    start, end = _date_pair(pairs["교육기간"], f"course {identity} education period")
    apply_start, apply_end = _date_pair(
        pairs["모집기간"], f"course {identity} application period"
    )
    current = end >= cutoff
    current_nodes = card.select(".apply-status .current")
    total_nodes = card.select(".apply-status .total")
    if len(current_nodes) != 1 or len(total_nodes) != 1:
        raise YesanContractError(f"course {identity}: capacity structure changed")
    capacity_current = _integer(
        current_nodes[0].get_text(" ", strip=True), f"course {identity} current capacity"
    )
    capacity_total = _integer(
        total_nodes[0].get_text(" ", strip=True), f"course {identity} total capacity"
    )
    excluded_pseudo = _is_excluded_pseudo_course(
        identity=identity,
        title=title,
        place=place,
        category=category,
        mode=mode,
        recruitment_status=recruitment_status,
        education_status=education_status,
        selection_method=selection_method,
        pairs=pairs,
        start=start,
        end=end,
        apply_start=apply_start,
        apply_end=apply_end,
        capacity_current=capacity_current,
        capacity_total=capacity_total,
    )
    if current and not excluded_pseudo and (
        not selection_method
        or not _clean(pairs.get("교육시간"))
        or not _clean(pairs["교육대상"])
        or not _clean(pairs["강의장소"])
        or not _clean(pairs["주최기관"])
    ):
        raise YesanContractError(f"course {identity}: current course field is empty")
    normalized_status = _RECRUITMENT_STATUS_MAP[recruitment_status]
    if (
        not excluded_pseudo
        and normalized_status == "OPEN"
        and not (apply_start <= cutoff <= apply_end)
    ):
        raise YesanContractError(f"course {identity}: open status/date mismatch")
    if (
        not excluded_pseudo
        and normalized_status == "SCHEDULED"
        and cutoff > apply_start
    ):
        raise YesanContractError(f"course {identity}: scheduled status/date mismatch")
    # Two audited historical records use the official ``0명`` sentinel.  It is
    # harmless for expired audit rows, but a current/future course must expose
    # a meaningful capacity before it can be persisted.
    if current and not excluded_pseudo and capacity_total < 1:
        raise YesanContractError(f"course {identity}: current course has no capacity")
    row = _base_row(
        identity=identity,
        title=title,
        page=page,
        place=place,
        category=category,
        mode=mode,
        recruitment_status=recruitment_status,
        education_status=education_status,
        selection_method=selection_method,
        pairs=pairs,
        start=start,
        end=end,
        apply_start=apply_start,
        apply_end=apply_end,
        capacity_current=capacity_current,
        capacity_total=capacity_total,
    )
    row["raw_fields"]["excluded_pseudo_course"] = excluded_pseudo
    return row


def _parse_list(soup: BeautifulSoup, page: int, cutoff: date) -> _ListPage:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "온라인강좌 신청" not in title:
        errors.append(f"page {page}: official catalogue title changed")
    roots = soup.select(".edu-search-list")
    if len(roots) != 1:
        errors.append(f"page {page}: catalogue root missing or duplicated")
    text = _clean(soup.get_text(" ", strip=True))
    totals = _TOTAL_RE.findall(text)
    if len(totals) != 1:
        total = 0
        errors.append(f"page {page}: advertised total missing or duplicated")
    else:
        total = int(totals[0].replace(",", ""))
    last = max(1, math.ceil(total / YESAN_PAGE_SIZE))
    errors.extend(_list_form_errors(soup, page))

    linked_pages: set[int] = set()
    for anchor in soup.select(".pagination a, .paging a"):
        source = f"{_clean(anchor.get('href'))} {_clean(anchor.get('onclick'))}"
        for value in re.findall(r"(?:pageIndex=|linkPage\s*\()\s*(\d+)", source):
            linked_pages.add(int(value))
    if total > YESAN_PAGE_SIZE and last not in linked_pages:
        errors.append(f"page {page}: advertised last-page navigation changed")

    cards = soup.select(".edu-search-list a.inner-box[onclick]")
    nodata = soup.select(".edu-search-list .PRGRM_nodata.PRGRM_list-nodata")
    if cards and nodata:
        errors.append(f"page {page}: rows and no-data sentinel coexist")
    if not cards and len(nodata) != 1:
        errors.append(f"page {page}: empty-page sentinel changed")
    rows: list[dict[str, Any]] = []
    for index, card in enumerate(cards, start=1):
        try:
            rows.append(_parse_card(card, page, cutoff))
        except Exception as exc:
            errors.append(f"page {page} card {index}: {_clean(exc)}")
    return _ListPage(rows=rows, total=total, last=last, errors=errors)


def _detail_pairs(root: Any) -> tuple[dict[str, str], list[str]]:
    pairs: dict[str, str] = {}
    errors: list[str] = []
    for item in root.select("ul.list-1st li.info"):
        labels = item.select(":scope > .tit")
        if len(labels) != 1:
            errors.append("detail field label structure changed")
            continue
        label = _clean(labels[0].get_text(" ", strip=True))
        clone = BeautifulSoup(str(item), "html.parser").select_one("li")
        clone_label = clone.select_one(":scope > .tit") if clone else None
        if clone_label is not None:
            clone_label.extract()
        value = _clean(clone.get_text(" ", strip=True) if clone else "")
        if not label or label in pairs:
            errors.append("detail field is empty or duplicated")
        else:
            pairs[label] = value
    return pairs, errors


def _validate_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> tuple[dict[str, Any], list[str]]:
    row = dict(listed)
    row["raw_fields"] = dict(listed["raw_fields"])
    identity = _clean(row["raw_fields"]["identity"])
    label = f"course {identity} detail"
    errors: list[str] = []
    roots = soup.select(".view-wrap")
    if len(roots) != 1:
        return row, [f"{label}: root missing or duplicated"]
    root = roots[0]
    try:
        title = _single_text(root, ".title", f"{label} title")
    except Exception as exc:
        title = ""
        errors.append(_clean(exc))
    if title != _clean(row["title"]):
        errors.append(f"{label}: title/list mismatch")

    forms = soup.select("form#searchForm")
    if len(forms) != 1:
        errors.append(f"{label}: search form missing or duplicated")
        form = None
    else:
        form = forms[0]
        action = urlparse(_clean(form.get("action")))
        if _clean(form.get("method")).lower() != "post" or (
            action.path,
            action.query,
            action.fragment,
        ) != (YESAN_LIST_PATH, "", ""):
            errors.append(f"{label}: form method/action changed")
        for name, expected in (
            ("lctrNo", identity),
            ("copyUrlData", f"lctrNo={identity}"),
            ("pageUnit", str(YESAN_PAGE_SIZE)),
        ):
            count, value = _form_value(form, name)
            if count != 1 or value != expected:
                errors.append(f"{label}: form identity field {name} changed")

    pairs, pair_errors = _detail_pairs(root)
    errors.extend(f"{label}: {item}" for item in pair_errors)
    if not set(_DETAIL_REQUIRED_FIELDS) <= set(pairs):
        errors.append(f"{label}: required detail fields changed")
    else:
        expected = row["raw_fields"]
        comparisons = {
            "모집기간": expected["source_application_period"],
            "교육기간": expected["source_period"],
            "교육대상": expected["source_target"],
            "교육시간": expected["source_schedule"],
            "강의장소": expected["source_venue"],
        }
        for field, value in comparisons.items():
            if _clean(pairs[field]) != _clean(value):
                errors.append(f"{label}: {field} list/detail mismatch")
        try:
            detail_start, detail_end = _date_pair(pairs["교육기간"], f"{label} period")
            if (detail_start.isoformat(), detail_end.isoformat()) != (
                row["start_date"],
                row["end_date"],
            ):
                errors.append(f"{label}: normalized education dates changed")
        except Exception as exc:
            errors.append(_clean(exc))

    type_nodes = root.select(".type-wrap > span")
    detail_types = [_clean(node.get_text(" ", strip=True)) for node in type_nodes]
    expected_types = [
        row["raw_fields"]["source_category"],
        row["raw_fields"]["source_recruitment_mode"],
        row["raw_fields"]["source_recruitment_status"],
        row["raw_fields"]["source_education_status"],
    ]
    if detail_types != expected_types:
        errors.append(f"{label}: type/status list/detail mismatch")

    scripts = "\n".join(
        node.get_text("\n") for node in soup.select("script:not([src])")
    )
    if _WRITE_HANDLER_RE.search(scripts) is None:
        errors.append(f"{label}: official application handler changed")
    controls = root.select(
        "button.button_write, a.button_write, input.button_write, "
        "button[class~='button_write'], a[class~='button_write']"
    )
    mode = _clean(row["raw_fields"]["source_recruitment_mode"])
    status = _clean(row["status"])
    online_open = mode == "온라인모집" and status == "OPEN"
    if online_open:
        if len(controls) != 1:
            errors.append(f"{label}: open course has no unique application control")
        else:
            control_text = _clean(
                controls[0].get("value")
                or controls[0].get_text(" ", strip=True)
            )
            if "신청" not in control_text and "접수" not in control_text:
                errors.append(f"{label}: application control label changed")
            row["reservation_available"] = True
            row["application_url"] = row["raw_url"]
            row["application_type"] = "ONLINE_RESERVATION"
            row["raw_fields"]["application_control_present"] = True
            row["raw_fields"]["application_control_contract"] = (
                "detail_searchForm.lctrNo+button_write->lctrAplcnt.write"
            )
    elif controls:
        errors.append(f"{label}: inactive/offline course exposes application control")
    elif mode == "오프라인모집" and status == "OPEN":
        row["application_type"] = "OFFLINE_APPLICATION"

    # Re-check source dates for an actionable row at detail time.  Date-level
    # comparison intentionally permits a scheduled label on its opening day.
    if status == "OPEN":
        start = date.fromisoformat(_clean(row["apply_start"]))
        end = date.fromisoformat(_clean(row["apply_end"]))
        if not (start <= cutoff <= end):
            errors.append(f"{label}: application period no longer matches open status")
    row["raw_fields"]["application_control_verified"] = not errors
    row["raw_fields"]["detail_verified"] = not errors
    return row, errors


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("title")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
            _clean(row.get("raw_fields", {}).get("source_recruitment_status")),
            int(row.get("capacity_current") or 0),
            int(row.get("capacity_total") or 0),
        )
        for row in rows
    )


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr(
        {key: value for key, value in row.items() if key not in {"raw_url", "application_url"}}
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("arbitrary detail description persisted")
    if _clean(row.get("raw_fields", {}).get("service_family")) != "education":
        errors.append("non-education row reached education persistence")
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
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "configured_collection_error": error,
    }


def collect_yesan_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    max_workers: int = YESAN_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Yesan education snapshot."""

    meta = _base_meta()
    if not is_yesan_education_target(target):
        meta["configured_collection_error"] = "target does not match canonical Yesan owner"
        return [], YESAN_PARSER, meta
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
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    "invalid timeout/max_pages/detail_limit/max_workers cap"
                ),
            }
        )
        return [], YESAN_PARSER, meta
    try:
        cutoff = _today(today)
    except ValueError as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], YESAN_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    errors: list[str] = []

    initial, initial_fetch_errors = _fetch_parse_many(
        [
            (
                ("list", 1, "data"),
                yesan_list_url(1),
                lambda soup: _parse_list(soup, 1, cutoff),
            )
        ],
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(initial_fetch_errors)
    meta["pages"] += len(initial)
    meta["list_requests"] += len(initial)
    first = initial.get(("list", 1, "data"))
    if not isinstance(first, _ListPage):
        errors.append("page 1: response missing")
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], YESAN_PARSER, meta
    errors.extend(first.errors)
    total, last = first.total, first.last
    required_list_requests = last + 2
    meta.update(
        {
            "source_total": total,
            "declared_pages": last,
            "required_list_requests": required_list_requests,
        }
    )
    if required_list_requests > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of "
                    f"{required_list_requests} required list requests"
                ),
            }
        )
        return [], YESAN_PARSER, meta
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], YESAN_PARSER, meta

    remaining_items: list[tuple[Any, str, Callable[[BeautifulSoup], Any]]] = []
    for page in range(2, last + 1):
        remaining_items.append(
            (
                ("list", page, "data"),
                yesan_list_url(page),
                lambda soup, current_page=page: _parse_list(soup, current_page, cutoff),
            )
        )
    remaining_items.extend(
        [
            (
                ("list", last + 1, "sentinel"),
                yesan_list_url(last + 1),
                lambda soup, current_page=last + 1: _parse_list(
                    soup, current_page, cutoff
                ),
            ),
            (
                ("list", 1, "recheck"),
                yesan_list_url(1),
                lambda soup: _parse_list(soup, 1, cutoff),
            ),
        ]
    )
    remaining, remaining_fetch_errors = _fetch_parse_many(
        remaining_items,
        fetcher=current_fetcher,
        session_factory=factory,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(remaining_fetch_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)
    meta["sentinel_requests"] = int(
        ("list", last + 1, "sentinel") in remaining
    )
    meta["stability_rechecks"] = int(("list", 1, "recheck") in remaining)

    all_rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    signatures: dict[int, tuple[tuple[Any, ...], ...]] = {}
    for page in range(1, last + 1):
        parsed = first if page == 1 else remaining.get(("list", page, "data"))
        if not isinstance(parsed, _ListPage):
            errors.append(f"page {page}: response missing")
            continue
        errors.extend(parsed.errors)
        if (parsed.total, parsed.last) != (total, last):
            errors.append(f"page {page}: total/last changed")
        expected = (
            0
            if total == 0
            else YESAN_PAGE_SIZE
            if page < last
            else total - YESAN_PAGE_SIZE * (last - 1)
        )
        if len(parsed.rows) != expected:
            errors.append(f"page {page}: row count {len(parsed.rows)} != {expected}")
        page_counts[page] = len(parsed.rows)
        signatures[page] = _page_signature(parsed.rows)
        all_rows.extend(parsed.rows)
    if len(all_rows) != total:
        errors.append("advertised total does not match parsed row count")
    nonempty_signatures = [value for value in signatures.values() if value]
    if len(nonempty_signatures) != len(set(nonempty_signatures)):
        errors.append("duplicate non-empty page signature")

    sentinel = remaining.get(("list", last + 1, "sentinel"))
    recheck = remaining.get(("list", 1, "recheck"))
    if not isinstance(sentinel, _ListPage) or not isinstance(recheck, _ListPage):
        errors.append("sentinel or page-one recheck missing")
    else:
        errors.extend(sentinel.errors)
        errors.extend(recheck.errors)
        if (sentinel.total, sentinel.last) != (total, last) or sentinel.rows:
            errors.append("immediate post-last sentinel page is not empty")
        if (
            (recheck.total, recheck.last) != (total, last)
            or _page_signature(recheck.rows) != signatures.get(1, ())
        ):
            errors.append("page-one stability recheck changed")

    identities = [_clean(row["raw_fields"]["identity"]) for row in all_rows]
    identity_duplicate_count = len(identities) - len(set(identities))
    if identity_duplicate_count:
        errors.append(f"{identity_duplicate_count} duplicate official identities")
    semantic_counter = Counter(
        (
            _normalized(row["title"]),
            _clean(row["start_date"]),
            _clean(row["end_date"]),
        )
        for row in all_rows
    )
    historical_missing_schedule_count = sum(
        not _clean(row["schedule_raw"])
        and date.fromisoformat(_clean(row["end_date"])) < cutoff
        for row in all_rows
    )
    current_source_rows = [
        row
        for row in all_rows
        if date.fromisoformat(_clean(row["end_date"])) >= cutoff
    ]
    excluded_pseudo_current_rows = [
        row
        for row in current_source_rows
        if bool(row["raw_fields"].get("excluded_pseudo_course"))
    ]
    audited_current_rows = [
        row
        for row in current_source_rows
        if not bool(row["raw_fields"].get("excluded_pseudo_course"))
    ]
    active_current_rows = [
        row
        for row in audited_current_rows
        if _clean(row["raw_fields"]["source_education_status"])
        not in _CANCELLED_EDUCATION_STATUSES
    ]
    excluded_cancelled_current_rows = [
        row
        for row in audited_current_rows
        if _clean(row["raw_fields"]["source_education_status"])
        in _CANCELLED_EDUCATION_STATUSES
    ]
    list_complete = bool(
        not errors
        and len(all_rows) == total
        and meta["list_requests"] == required_list_requests
        and meta["sentinel_requests"] == 1
        and meta["stability_rechecks"] == 1
    )
    if len(audited_current_rows) > detail_limit:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {detail_limit} of "
            f"{len(audited_current_rows)} required current details"
        )

    detailed_rows: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    if list_complete and not errors:
        detail_items = [
            (
                ("detail", _clean(row["raw_fields"]["identity"])),
                _clean(row["raw_url"]),
                lambda soup, current=dict(row): _validate_detail(current, soup, cutoff),
            )
            for row in audited_current_rows
        ]
        meta["detail_attempts"] = len(detail_items)
        details, detail_fetch_errors = _fetch_parse_many(
            detail_items,
            fetcher=current_fetcher,
            session_factory=factory,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        meta["pages"] += len(details)
        for listed in audited_current_rows:
            identity = _clean(listed["raw_fields"]["identity"])
            value = details.get(("detail", identity))
            if not isinstance(value, tuple) or len(value) != 2:
                detail_errors.append(f"course {identity}: detail response missing")
                continue
            detailed, item_errors = value
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                detailed_rows.append(detailed)
                meta["detail_pages"] += 1
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        list_complete
        and meta["detail_attempts"] == len(audited_current_rows)
        and meta["detail_pages"] == len(audited_current_rows)
        and not detail_errors
    )

    active_identities = {
        _clean(row["raw_fields"]["identity"]) for row in active_current_rows
    }
    persistable = [
        row
        for row in detailed_rows
        if _clean(row["raw_fields"]["identity"]) in active_identities
    ]
    application_controls_complete = bool(
        details_complete
        and all(
            bool(row["raw_fields"].get("application_control_verified"))
            for row in detailed_rows
        )
    )
    result: list[dict[str, Any]] = []
    if details_complete and application_controls_complete and not errors:
        for row in persistable:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(persistable))
            except Exception as exc:
                errors.append(f"dedupe failed: {_clean(exc)}")
                result = []
            if len(result) != len(persistable):
                errors.append(
                    "dedupe changed official identity cardinality "
                    f"{len(persistable)} to {len(result)}"
                )
                result = []
    snapshot_complete = bool(
        list_complete
        and details_complete
        and application_controls_complete
        and not errors
    )
    if not snapshot_complete:
        result = []

    meta.update(
        {
            "ownership_scope": YESAN_OWNERSHIP_SCOPE,
            "canonical_url": YESAN_CANONICAL_URL,
            "page_counts": page_counts,
            "source_rows": len(all_rows),
            "current_source_count": len(current_source_rows),
            "audited_current_count": len(audited_current_rows),
            "active_current_count": len(active_current_rows),
            "expired_count": len(all_rows) - len(current_source_rows),
            "excluded_pseudo_current_count": len(excluded_pseudo_current_rows),
            "excluded_pseudo_current_ids": [
                _clean(row["raw_fields"]["identity"])
                for row in excluded_pseudo_current_rows
            ],
            "excluded_cancelled_current_count": len(excluded_cancelled_current_rows),
            "excluded_cancelled_current_ids": [
                _clean(row["raw_fields"]["identity"])
                for row in excluded_cancelled_current_rows
            ],
            "historical_missing_schedule_count": historical_missing_schedule_count,
            "identity_duplicate_count": identity_duplicate_count,
            "semantic_duplicate_group_count": sum(
                count > 1 for count in semantic_counter.values()
            ),
            "semantic_duplicate_excess_rows": sum(
                max(0, count - 1) for count in semantic_counter.values()
            ),
            "semantic_duplicate_policy": "preserve_distinct_official_lctrNo",
            "branch_counts": dict(Counter(_clean(row["branch"]) for row in result)),
            "status_counts": dict(Counter(_clean(row["status"]) for row in result)),
            "online_open_count": sum(
                row.get("reservation_available") is True for row in result
            ),
            "application_control_count": sum(
                bool(row["raw_fields"].get("application_control_present"))
                for row in detailed_rows
            ),
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "application_controls_complete": application_controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not active_current_rows),
            "no_current_reason": (
                "the complete official catalogue has no active current/future courses"
                if snapshot_complete and not active_current_rows
                else ""
            ),
            "municipality_coverage": [YESAN_MUNICIPALITY_CODE],
            "candidate_audit": {
                key: dict(value) for key, value in YESAN_CANDIDATE_AUDIT.items()
            },
            "discovery_audit": dict(YESAN_DISCOVERY_AUDIT),
            "alias_providers": sorted(YESAN_ALIAS_PROVIDERS),
            "pii_fields_discarded": list(YESAN_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, YESAN_PARSER, meta


collect = collect_yesan_education


__all__ = [
    "YESAN_ALIAS_CANDIDATE_IDS",
    "YESAN_ALIAS_PROVIDERS",
    "YESAN_ANNOUNCEMENT_URL",
    "YESAN_CANONICAL_CANDIDATE_ID",
    "YESAN_CANONICAL_URL",
    "YESAN_CANDIDATE_AUDIT",
    "YESAN_COMPUTER_SUBSET_URL",
    "YESAN_DISCOVERY_AUDIT",
    "YESAN_EXCLUDED_CANDIDATE_IDS",
    "YESAN_GENERAL_HOMEPAGE_URLS",
    "YESAN_LIFELONG_HOMEPAGE_URLS",
    "YESAN_MUNICIPALITY_CODE",
    "YESAN_MUNICIPALITY_NAME",
    "YESAN_PAGE_SIZE",
    "YESAN_PARSER",
    "YESAN_PII_FIELDS_DISCARDED",
    "YESAN_PROVIDER",
    "YESAN_PROVINCIAL_OVERVIEW_URL",
    "YesanContractError",
    "collect",
    "collect_yesan_education",
    "is_yesan_education_target",
    "is_yesan_excluded_candidate",
    "is_yesan_owned_alias_target",
    "yesan_detail_url",
    "yesan_list_url",
]
