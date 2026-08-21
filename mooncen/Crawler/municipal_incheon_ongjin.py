"""Fail-closed collector for Ongjin-gun's resident education ledger.

The registered owner page renders the official ``lectureList.do`` catalogue.
Every advertised page is collected, a request immediately beyond the final
page must clamp to the exact final page, and the first/final boundaries are
rechecked before current rows are enriched from their detail pages.

``www.ongjin.go.kr`` omits the TuringSign RSA Secure CA 2 intermediate from
its TLS handshake.  The scoped session below adds that audited intermediate
to the operating-system trust store while retaining normal CA, hostname and
validity checks.  Only the Ongjin host receives the custom adapter.

Details contain public contact and free-text fields.  Their labels and DOM
shape are validated, but their values are deliberately never read or stored.
Only the explicit structural allowlist is extracted.  Application controls
are inspected, never followed, and any newly appearing control fails closed.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import math
import re
import ssl
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter

from Crawler.municipal_namdong import NAMDONG_AIA_INTERMEDIATE_PEM


ONGJIN_PROVIDER = "MUNI_WWW_ONGJIN_GO_KR_0243B215"
ONGJIN_DUPLICATE_PROVIDER = "MUNI_WWW_ONGJIN_GO_KR_9B7F8E38"
ONGJIN_CANONICAL_CANDIDATE_ID = "MUNI_IR_66B91AC3F17B"
ONGJIN_LEDGER_CANDIDATE_ID = "MUNI_IR_8D6870866D4E"
ONGJIN_MUNICIPALITY_CODE = "2872000000"
ONGJIN_MUNICIPALITY_NAME = "인천광역시 옹진군"

ONGJIN_HOST = "www.ongjin.go.kr"
ONGJIN_CANONICAL_PATH = "/open_content/main/community/education/program.jsp"
ONGJIN_LIST_PATH = "/open_content/main/lecture/lectureList.do"
ONGJIN_DETAIL_PATH = "/open_content/main/lecture/lectureDetail.do"
ONGJIN_CANONICAL_URL = f"https://{ONGJIN_HOST}{ONGJIN_CANONICAL_PATH}"
ONGJIN_LEDGER_URL = f"https://{ONGJIN_HOST}{ONGJIN_LIST_PATH}?sitediv=main"
ONGJIN_URL = ONGJIN_CANONICAL_URL

ONGJIN_PAGE_SIZE = 10
ONGJIN_FETCH_ATTEMPTS = 3
ONGJIN_MAX_HTML_BYTES = 2_000_000
ONGJIN_AIA_INTERMEDIATE_PEM = NAMDONG_AIA_INTERMEDIATE_PEM
ONGJIN_AIA_INTERMEDIATE_SHA256 = (
    "a6f9c967eb8aa9283a1ca649b87b764720e9f5c3afa81c150676f4ca36e98cf6"
)
ONGJIN_LEAF_SHA256_AUDITED_2026_07_22 = (
    "6475b581d35c2d1b6fc90b915097451af084428331daba9dbfe0227fddac0ccc"
)
ONGJIN_PARSER = (
    "ongjin_resident_education_all_pages+exact_final_page_clamp+"
    "stable_boundaries+verified_aia_intermediate+current_safe_details+"
    "identity_bound_schedule_anomaly+zero_application_controls+pii_never_read"
)
ONGJIN_OWNERSHIP_SCOPE = "ongjin_official_resident_education_ledger_only"


class OngjinContractError(ValueError):
    """Raised when the audited Ongjin source contract changes."""


ONGJIN_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    ONGJIN_CANONICAL_CANDIDATE_ID: {
        "decision": "registered_canonical_owner_landing",
        "provider": ONGJIN_PROVIDER,
        "url": ONGJIN_CANONICAL_URL,
        "owner": ONGJIN_PROVIDER,
    },
    ONGJIN_LEDGER_CANDIDATE_ID: {
        "decision": "canonical_resident_education_ledger_rendered_by_landing",
        "provider": ONGJIN_PROVIDER,
        "url": ONGJIN_LEDGER_URL,
        "owner": ONGJIN_PROVIDER,
    },
    ONGJIN_DUPLICATE_PROVIDER: {
        "decision": "registered_duplicate_provider_same_landing_url",
        "provider": ONGJIN_DUPLICATE_PROVIDER,
        "url": ONGJIN_CANONICAL_URL,
        "owner": ONGJIN_PROVIDER,
        "duplicate_of": ONGJIN_PROVIDER,
    },
    "MUNI_IR_5E84D62B0E43": {
        "decision": "wrong_category_statistics_page",
        "provider": "MUNI_WWW_ONGJIN_GO_KR_3F88FA7F",
        "url": "https://www.ongjin.go.kr/open_content/main/administration/data/statistic.jsp",
        "owner": "MUNI_WWW_ONGJIN_GO_KR_3F88FA7F",
    },
    "MUNI_IR_61DCB5D8E152": {
        "decision": "cyber_learning_information_page_not_course_ledger",
        "provider": ONGJIN_DUPLICATE_PROVIDER,
        "url": "https://www.ongjin.go.kr/open_content/main/community/education/cyber_incheon.jsp",
        "owner": ONGJIN_DUPLICATE_PROVIDER,
    },
    "MUNI_IR_6FC6F8469CA1": {
        "decision": "separate_incheon_metropolitan_reservation_owner",
        "provider": "INCHEON_RESERVATION",
        "url": "https://www.incheon.go.kr/res/",
        "owner": "INCHEON_RESERVATION",
    },
}

ONGJIN_EXCLUDED_SOURCE_AUDIT: tuple[Mapping[str, str], ...] = (
    {
        "url": "https://www.ongjin.go.kr/open_content/main/administration/data/statistic.jsp",
        "reason": "statistics page is not an education catalogue",
    },
    {
        "url": "https://www.ongjin.go.kr/open_content/main/community/education/cyber_incheon.jsp",
        "reason": "cyber-learning information links are not resident course records",
    },
    {
        "url": "https://www.incheon.go.kr/res/",
        "reason": "metropolitan reservation service has a separate owner",
    },
)

ONGJIN_INSTITUTIONS: Mapping[str, str] = {
    "main": "옹진군청",
    "bukdo": "북도면",
    "yeonpyeong": "연평면",
    "baekryeong": "백령면",
    "daecheong": "대청면",
    "deokjeok": "덕적면",
    "jawol": "자월면",
    "yeongheung": "영흥면",
}

ONGJIN_CATEGORIES: Mapping[str, str] = {
    "health": "건강",
    "hobby": "취미",
    "lang": "외국어",
    "info": "컴퓨터",
    "edu": "자격증",
    "cult": "교양",
    "etc": "기타",
}

ONGJIN_AUDITED_SCHEDULE_ANOMALIES: Mapping[str, Mapping[str, str]] = {
    "98": {
        "title": "미싱클래스",
        "branch": "영흥면",
        "schedule": "월요일 16:00 ~ 210:00",
    }
}

ONGJIN_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "source_total": 31,
    "source_pages": 4,
    "page_sizes": (10, 10, 10, 1),
    "sentinel_page": 5,
    "sentinel_kind": "exact_final_page_clamp",
    "required_list_requests": 7,
    "current_source_count": 12,
    "current_ids": ("100", "99", "98", "96", "95", "94", "60", "48", "46", "44", "43", "42"),
    "current_branch": "영흥면",
    "current_source_status": "접수마감 교육중",
    "detail_pages": 12,
    "detail_field_count": 17,
    "complete_network_requests": 19,
    "application_controls": 0,
    "source_identity_duplicates": 0,
    "current_semantic_duplicates": 0,
    "audited_schedule_anomalies": 1,
    "tls_intermediate_sha256": ONGJIN_AIA_INTERMEDIATE_SHA256,
    "tls_leaf_sha256_audited_2026_07_22": ONGJIN_LEAF_SHA256_AUDITED_2026_07_22,
}


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Parser = Callable[[BeautifulSoup, str], Any]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_TOTAL_RE = re.compile(r"전체\s*강좌\s*수\s*:\s*([\d,]+)")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
_CLOCK_RE = re.compile(r"(?<!\d)(\d{1,3}):([0-5]\d)(?!\d)")
_PAGE_TITLE_RE = re.compile(r"^([1-9]\d*)\s+page(?:\(현재 페이지\))?$")
_GENERIC_NON_COURSE_RE = re.compile(
    r"^(?:(?:test|sample|테스트|샘플)(?:\s*[-_#]?\s*\d+)?|"
    r"(?:교육\s*)?(?:안내|공지)(?:사항)?|(?:강좌|교육)?\s*(?:등록|없음))$",
    re.IGNORECASE,
)

_LIST_TITLE = "주민교육프로그램 목록 | main>함께하는군정>평생교육/강좌정보"
_DETAIL_TITLE = "주민교육프로그램 내용 | main>함께하는군정>평생교육/강좌정보"
_LIST_HEADERS = ("강좌명", "교육기관", "접수기간/교육기간", "진행상태")
_DETAIL_LABELS = (
    "접수상태",
    "강좌상태",
    "교육기관",
    "분야",
    "대상",
    "교육기간",
    "교육요일시간",
    "수강료",
    "기타/재료비",
    "교육내용",
    "교육상세내용",
    "입금정보",
    "취소환불규정",
    "첨부파일",
    "교육장명",
    "교육장주소",
    "문의전화",
)
_DETAIL_SAFE_LABELS = frozenset(
    {
        "접수상태",
        "강좌상태",
        "교육기관",
        "분야",
        "대상",
        "교육기간",
        "교육요일시간",
        "수강료",
        "기타/재료비",
        "교육장명",
        "교육장주소",
    }
)
_DETAIL_PRIVATE_LABELS = frozenset(
    {
        "교육내용",
        "교육상세내용",
        "입금정보",
        "취소환불규정",
        "첨부파일",
        "문의전화",
    }
)
_STATUS_MAP: Mapping[tuple[str, str], str] = {
    ("접수마감", "교육중"): "CLOSED",
    ("접수마감", "교육종료"): "CLOSED",
}


class _OngjinSSLContextAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext, **kwargs: Any) -> None:
        self._context = context
        super().__init__(**kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self._context
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        proxy_kwargs["ssl_context"] = self._context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def ongjin_session_factory() -> requests.Session:
    """Return a strictly verified session with the missing issuer supplied."""

    context = ssl.create_default_context()
    context.load_verify_locations(cadata=ONGJIN_AIA_INTERMEDIATE_PEM)
    session = requests.Session()
    session.mount(
        f"https://{ONGJIN_HOST}/",
        _OngjinSSLContextAdapter(context, max_retries=0),
    )
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).casefold()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise OngjinContractError(f"{label} may not be boolean")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise OngjinContractError(f"invalid {label}") from exc
    if parsed < 1:
        raise OngjinContractError(f"{label} must be positive")
    return parsed


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _normal_path(value: Any) -> str:
    return re.sub(r"/{2,}", "/", str(value or "/"))


def _compare_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
    ):
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return (
        f"https://{parsed.hostname.rstrip('.').lower()}{_normal_path(parsed.path)}"
        + (f"?{query}" if query else "")
    )


def is_ongjin_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != ONGJIN_PROVIDER:
        return False
    candidate = _clean(_target_value(target, "candidate_id"))
    if candidate and candidate not in {
        ONGJIN_CANONICAL_CANDIDATE_ID,
        ONGJIN_LEDGER_CANDIDATE_ID,
    }:
        return False
    compared = _compare_url(_target_value(target, "url"))
    return compared in {
        _compare_url(ONGJIN_CANONICAL_URL),
        _compare_url(ONGJIN_LEDGER_URL),
    }


is_target = is_ongjin_education_target


def ongjin_list_url(page: int = 1) -> str:
    current = _positive_int(page, "list page")
    return f"https://{ONGJIN_HOST}{ONGJIN_LIST_PATH}?" + urlencode(
        (("sitediv", "main"), ("nowPage", current))
    )


def ongjin_detail_url(identity: Any) -> str:
    token = _clean(identity)
    if not _IDENTITY_RE.fullmatch(token):
        raise OngjinContractError("invalid lecture identity")
    return f"https://{ONGJIN_HOST}{ONGJIN_DETAIL_PATH}?" + urlencode(
        (("lecseq", token), ("sitediv", "main"))
    )


def canonical_ongjin_detail_identity(current_url: str, value: Any) -> str:
    parsed = urlparse(urljoin(current_url, _clean(value)))
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname != ONGJIN_HOST
        or parsed.path != ONGJIN_DETAIL_PATH
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
    ):
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"lecseq", "sitediv"} or query.get("sitediv") != ["main"]:
        return ""
    identity = _clean((query.get("lecseq") or [""])[0])
    return identity if _IDENTITY_RE.fullmatch(identity) else ""


def _full_date_range(value: Any, label: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise OngjinContractError(f"{label} must contain exactly two dates")
    try:
        start, end = (
            date(int(year), int(month), int(day))
            for year, month, day in matches
        )
    except ValueError as exc:
        raise OngjinContractError(f"{label} contains an invalid date") from exc
    if start > end:
        raise OngjinContractError(f"{label} is reversed")
    return start, end


def _query_page(value: Any, *, expected_path: str = ONGJIN_LIST_PATH) -> int:
    parsed = urlparse(urljoin(ONGJIN_LEDGER_URL, _clean(value)))
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname != ONGJIN_HOST
        or parsed.path != expected_path
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
    ):
        raise OngjinContractError("pagination link escaped official list")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"sitediv", "nowPage"} or query.get("sitediv") != ["main"]:
        raise OngjinContractError("pagination query changed")
    token = _clean((query.get("nowPage") or [""])[0])
    if not _IDENTITY_RE.fullmatch(token):
        raise OngjinContractError("pagination page is invalid")
    return int(token)


def _validate_registry(soup: BeautifulSoup) -> None:
    institution = soup.select_one("form select#instcd0[name='instcd0']")
    category = soup.select_one("form select#leccate[name='leccate']")
    if institution is None or category is None:
        raise OngjinContractError("course filter registry disappeared")
    actual_institutions = {
        _clean(option.get("value")): _text(option)
        for option in institution.select("option[value]")
        if _clean(option.get("value"))
    }
    actual_categories = {
        _clean(option.get("value")): _text(option)
        for option in category.select("option[value]")
        if _clean(option.get("value"))
    }
    if actual_institutions != dict(ONGJIN_INSTITUTIONS):
        raise OngjinContractError("official institution registry changed")
    if actual_categories != dict(ONGJIN_CATEGORIES):
        raise OngjinContractError("official course category registry changed")


def _list_contract(soup: BeautifulSoup) -> tuple[int, int, int, Tag]:
    if _text(soup.title) != _LIST_TITLE:
        raise OngjinContractError("list page title changed")
    forms = [
        form
        for form in soup.select("form")
        if urlparse(urljoin(ONGJIN_LEDGER_URL, _clean(form.get("action")))).path
        == ONGJIN_LIST_PATH
        and _clean(form.get("method")).lower() == "get"
    ]
    if len(forms) != 1:
        raise OngjinContractError("canonical list search form changed")
    hidden = forms[0].select("input[name='sitediv'][value='main']")
    if len(hidden) != 1:
        raise OngjinContractError("list form lost sitediv=main")
    _validate_registry(soup)
    totals = [
        match
        for node in soup.select("#contents p.right")
        if (match := _TOTAL_RE.fullmatch(_text(node))) is not None
    ]
    if len(totals) != 1:
        raise OngjinContractError("list total summary changed")
    total = int(totals[0].group(1).replace(",", ""))
    last = math.ceil(total / ONGJIN_PAGE_SIZE) if total else 0
    pagers = soup.select("#contents .paging")
    if len(pagers) != 1:
        raise OngjinContractError("expected one pagination block")
    pager = pagers[0]
    selected = pager.select("a.select[href][title]")
    if len(selected) != 1:
        raise OngjinContractError("pagination selected page changed")
    title_match = _PAGE_TITLE_RE.fullmatch(_clean(selected[0].get("title")))
    if title_match is None:
        raise OngjinContractError("selected page title changed")
    reported = int(title_match.group(1))
    if _query_page(selected[0].get("href")) != reported:
        raise OngjinContractError("selected page href disagrees with title")
    final_links = pager.select("a.last[href]")
    if len(final_links) != 1 or _query_page(final_links[0].get("href")) != last:
        raise OngjinContractError("pagination final-page link changed")
    tables = [
        table
        for table in soup.select("#contents table.general_board")
        if tuple(_text(node) for node in table.select("thead th")) == _LIST_HEADERS
    ]
    if len(tables) != 1:
        raise OngjinContractError("course table/header contract changed")
    return total, reported, last, tables[0]


def _status(
    reception: str,
    course: str,
    *,
    start: date,
    end: date,
    cutoff: date,
) -> str:
    key = (_clean(reception), _clean(course))
    if key not in _STATUS_MAP:
        raise OngjinContractError(f"unknown course status {key!r}")
    expected = "교육중" if start <= cutoff <= end else "교육종료" if end < cutoff else "강좌준비"
    if course != expected:
        raise OngjinContractError("course status disagrees with education dates")
    return _STATUS_MAP[key]


def _parse_list_row(
    target: Any,
    row: Tag,
    *,
    page_url: str,
    cutoff: date,
) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 4:
        raise OngjinContractError(
            f"non-course/instruction row encountered: {_clean(row.get_text(' ', strip=True))!r}"
        )
    link = cells[0].select_one("a[href]")
    if link is None:
        raise OngjinContractError("course row has no detail link")
    identity = canonical_ongjin_detail_identity(page_url, link.get("href"))
    if not identity:
        raise OngjinContractError("course detail link escaped official identity")
    title = _text(link)
    if not title:
        raise OngjinContractError("course row has blank title")
    if _GENERIC_NON_COURSE_RE.fullmatch(title):
        raise OngjinContractError(
            f"unaudited test/information row encountered: {identity}:{title}"
        )
    branch = _text(cells[1])
    if branch not in set(ONGJIN_INSTITUTIONS.values()):
        raise OngjinContractError(f"unknown official institution {branch!r}")
    date_lines = tuple(_text(node) for node in cells[2].select("p"))
    education_lines = [line for line in date_lines if line.startswith("교육기간")]
    if len(education_lines) != 1:
        raise OngjinContractError("course row education-period line changed")
    start, end = _full_date_range(education_lines[0], "list education period")
    status_nodes = cells[3].select("span.lectag")
    if len(status_nodes) != 2:
        raise OngjinContractError("list status tags changed")
    reception, course = map(_text, status_nodes)
    status = _status(
        reception,
        course,
        start=start,
        end=end,
        cutoff=cutoff,
    )
    application_lines = tuple(line for line in date_lines if line not in education_lines)
    apply_period = ""
    if application_lines:
        apply_start, apply_end = _full_date_range(
            " ".join(application_lines), "list application period"
        )
        apply_period = f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
    branch_code = next(
        code for code, name in ONGJIN_INSTITUTIONS.items() if name == branch
    )
    return {
        "provider": _clean(_target_value(target, "provider")),
        "provider_course_id": f"{ONGJIN_PROVIDER}:lecture:{identity}",
        "title": title,
        "branch": branch,
        "branch_code": f"ONGJIN_{branch_code.upper()}",
        "category": "평생교육",
        "raw_url": ongjin_detail_url(identity),
        "status": status,
        "reservation_available": False,
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "apply_period": apply_period,
        "program_type": "강좌",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html+detail_html",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "description": title,
        "raw_fields": {
            "source_identity": identity,
            "source_reception_status": reception,
            "source_course_status": course,
            "source_branch": branch,
            "list_page": int(
                (parse_qs(urlparse(page_url).query).get("nowPage") or ["0"])[0]
            ),
            "list_date_lines": date_lines,
            "parser": "ongjin_resident_education_list",
        },
        "_identity": identity,
        "_reception_status": reception,
        "_course_status": course,
    }


def _parse_list_page(
    target: Any,
    soup: BeautifulSoup,
    *,
    requested_page: int,
    cutoff: date,
    expected_total: Optional[int] = None,
    allow_final_clamp: bool = False,
) -> tuple[list[dict[str, Any]], int, int]:
    total, reported, last, table = _list_contract(soup)
    if expected_total is not None and total != expected_total:
        raise OngjinContractError("advertised source total changed during scan")
    if total < 1 or last < 1:
        raise OngjinContractError("audited resident education archive became empty")
    if allow_final_clamp:
        if requested_page != last + 1 or reported != last:
            raise OngjinContractError("beyond-final page did not clamp exactly")
    elif reported != requested_page:
        raise OngjinContractError("requested data page was silently clamped")
    page_url = ongjin_list_url(requested_page)
    rows = [
        _parse_list_row(target, row, page_url=page_url, cutoff=cutoff)
        for row in table.select("tbody > tr")
    ]
    if len(rows) > ONGJIN_PAGE_SIZE:
        raise OngjinContractError("course page exceeds audited page size")
    return rows, total, last


def _source_signature(rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("_identity")),
            _clean(row.get("title")),
            _clean(row.get("branch")),
            _clean(row.get("_reception_status")),
            _clean(row.get("_course_status")),
            _clean(row.get("period")),
        )
        for row in rows
    )


def _safe_detail_values(root: Tag) -> tuple[dict[str, str], tuple[str, ...]]:
    pairs: dict[str, str] = {}
    labels: list[str] = []
    items = root.select(":scope > ul.datalist > li > dl")
    for item in items:
        key_node = item.find("dt", recursive=False)
        value_node = item.find("dd", recursive=False)
        key = _text(key_node)
        if not key or value_node is None or key in labels:
            raise OngjinContractError("detail definition-list structure changed")
        labels.append(key)
        if key in _DETAIL_SAFE_LABELS:
            pairs[key] = _text(value_node)
        elif key not in _DETAIL_PRIVATE_LABELS:
            raise OngjinContractError(f"unaudited detail field {key!r}")
        # Values of private/free-text labels are intentionally never read.
    if tuple(labels) != _DETAIL_LABELS:
        raise OngjinContractError(f"detail field schema changed: {tuple(labels)!r}")
    if set(pairs) != _DETAIL_SAFE_LABELS:
        raise OngjinContractError("safe detail allowlist became incomplete")
    for key in (
        "접수상태",
        "강좌상태",
        "교육기관",
        "분야",
        "대상",
        "교육기간",
        "교육요일시간",
        "수강료",
        "기타/재료비",
        "교육장명",
    ):
        if not pairs[key]:
            raise OngjinContractError(f"safe detail field {key!r} is blank")
    return pairs, tuple(labels)


def _validate_schedule(
    identity: str,
    title: str,
    branch: str,
    value: str,
) -> bool:
    clocks = [(int(hour), int(minute)) for hour, minute in _CLOCK_RE.findall(value)]
    if len(clocks) < 2:
        raise OngjinContractError("detail schedule has fewer than two clock values")
    invalid = any(hour > 24 or (hour == 24 and minute != 0) for hour, minute in clocks)
    if not invalid:
        return False
    expected = ONGJIN_AUDITED_SCHEDULE_ANOMALIES.get(identity)
    actual = {"title": title, "branch": branch, "schedule": value}
    if expected != actual:
        raise OngjinContractError(
            f"unaudited invalid schedule clock for {identity}: {value!r}"
        )
    return True


def _validate_zero_application_controls(root: Tag) -> None:
    if root.select("form"):
        raise OngjinContractError("unaudited application form appeared in detail")
    controls: list[Tag] = []
    for anchor in root.select("a[href], a[onclick]"):
        text = _text(anchor)
        raw = " ".join((_clean(anchor.get("href")), _clean(anchor.get("onclick"))))
        if (
            any(token in text for token in ("신청", "접수", "예약"))
            or re.search(r"apply|request|reserve|receipt", raw, re.IGNORECASE)
        ):
            controls.append(anchor)
    if controls:
        raise OngjinContractError("unaudited application control appeared in detail")


def _parse_detail(
    soup: BeautifulSoup,
    final_url: str,
    listed: Mapping[str, Any],
    cutoff: date,
) -> dict[str, Any]:
    identity = _clean(listed.get("_identity"))
    if _compare_url(final_url) != _compare_url(ongjin_detail_url(identity)):
        raise OngjinContractError("detail response URL changed")
    if _text(soup.title) != _DETAIL_TITLE:
        raise OngjinContractError("detail page title changed")
    roots = soup.select("#detail_con > .board_view")
    if len(roots) != 1:
        raise OngjinContractError("expected one course detail root")
    root = roots[0]
    titles = root.select(":scope > p.title")
    if len(titles) != 1 or _text(titles[0]) != _clean(listed.get("title")):
        raise OngjinContractError("detail title does not match list identity")
    pairs, labels = _safe_detail_values(root)
    branch = pairs["교육기관"]
    if branch != _clean(listed.get("branch")):
        raise OngjinContractError("detail institution does not match list identity")
    start, end = _full_date_range(pairs["교육기간"], "detail education period")
    if (start.isoformat(), end.isoformat()) != (
        _clean(listed.get("start_date")),
        _clean(listed.get("end_date")),
    ):
        raise OngjinContractError("detail education period disagrees with list")
    status = _status(
        pairs["접수상태"],
        pairs["강좌상태"],
        start=start,
        end=end,
        cutoff=cutoff,
    )
    if (
        pairs["접수상태"] != _clean(listed.get("_reception_status"))
        or pairs["강좌상태"] != _clean(listed.get("_course_status"))
    ):
        raise OngjinContractError("detail status does not match list identity")
    schedule_anomaly = _validate_schedule(
        identity,
        _clean(listed.get("title")),
        branch,
        pairs["교육요일시간"],
    )
    _validate_zero_application_controls(root)
    raw_fields = dict(listed.get("raw_fields") or {})
    raw_fields.update(
        {
            "parser": "ongjin_resident_education_list+safe_detail",
            "detail_field_labels": labels,
            "safe_detail_labels": tuple(
                label for label in labels if label in _DETAIL_SAFE_LABELS
            ),
            "private_detail_labels_never_read": tuple(
                label for label in labels if label in _DETAIL_PRIVATE_LABELS
            ),
            "application_control_count": 0,
            "audited_schedule_anomaly": schedule_anomaly,
        }
    )
    enriched = {
        key: value
        for key, value in listed.items()
        if not key.startswith("_") and key != "raw_fields"
    }
    enriched.update(
        {
            "branch": branch,
            "category": pairs["분야"],
            "status": status,
            "reservation_available": False,
            "application_url": "",
            "application_type": "INFORMATION_ONLY",
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "schedule_raw": pairs["교육요일시간"],
            "target": pairs["대상"],
            "fee": pairs["수강료"],
            "material_fee": pairs["기타/재료비"],
            "room": pairs["교육장명"],
            "venue_name": pairs["교육장명"],
            "venue_address": pairs["교육장주소"],
            "description": _clean(listed.get("title")),
            "raw_fields": raw_fields,
        }
    )
    return _clean_row(enriched)


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _compact(row.get("title")),
        _compact(row.get("branch")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _compact(row.get("schedule_raw")),
    )


def _clean_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if not key.startswith("_") and value not in (None, "", [], {}, ())
    }


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    response = current_session.get(url, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    return response


def _close_quietly(value: Any) -> None:
    try:
        value.close()
    except Exception:
        pass


class _Runner:
    def __init__(
        self,
        *,
        timeout: int,
        maximum: int,
        fetcher: Fetcher,
        session_factory: SessionFactory,
        sleeper: Sleeper,
    ) -> None:
        self.timeout = timeout
        self.maximum = maximum
        self.fetcher = fetcher
        self.session_factory = session_factory
        self.sleeper = sleeper
        self.requests = 0
        self.retries = 0
        self.session = session_factory()

    def get(self, url: str, parser: Parser) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(ONGJIN_FETCH_ATTEMPTS):
            if self.requests >= self.maximum:
                raise OngjinContractError("network request cap exceeded")
            self.requests += 1
            try:
                response = self.fetcher(self.session, url, self.timeout)
                if (
                    isinstance(response, tuple)
                    and len(response) == 2
                    and isinstance(response[0], BeautifulSoup)
                ):
                    soup, final_url = response
                elif isinstance(response, BeautifulSoup):
                    soup, final_url = response, url
                else:
                    status = int(getattr(response, "status_code", 200) or 0)
                    if status != 200:
                        raise RuntimeError(f"HTTP {status}")
                    final_url = _clean(getattr(response, "url", url)) or url
                    content = bytes(getattr(response, "content", b"") or b"")
                    if not content:
                        content = str(getattr(response, "text", "") or "").encode("utf-8")
                    if not content:
                        raise RuntimeError("empty HTML response")
                    if len(content) > ONGJIN_MAX_HTML_BYTES:
                        raise OngjinContractError("HTML response is too large")
                    soup = BeautifulSoup(content, "lxml")
                if _compare_url(final_url) != _compare_url(url):
                    raise OngjinContractError("request response URL changed")
                return parser(soup, final_url)
            except OngjinContractError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < ONGJIN_FETCH_ATTEMPTS:
                    self.retries += 1
                    self.sleeper(0.35 * (attempt + 1))
        raise OngjinContractError(
            f"failed source fetch after retries: {last_error}"
        ) from last_error

    def close(self) -> None:
        _close_quietly(self.session)


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
        "candidate_id": ONGJIN_CANONICAL_CANDIDATE_ID,
        "ledger_candidate_id": ONGJIN_LEDGER_CANDIDATE_ID,
        "canonical_url": ONGJIN_CANONICAL_URL,
        "ledger_url": ONGJIN_LEDGER_URL,
        "ownership_scope": ONGJIN_OWNERSHIP_SCOPE,
        "owner_boundary_audit": dict(ONGJIN_OWNER_BOUNDARY_AUDIT),
        "excluded_source_audit": tuple(ONGJIN_EXCLUDED_SOURCE_AUDIT),
        "discovery_audit": dict(ONGJIN_DISCOVERY_AUDIT),
        "tls_verification": "system_trust_plus_audited_aia_intermediate",
        "tls_intermediate_sha256": ONGJIN_AIA_INTERMEDIATE_SHA256,
    }


def _scan_archive(
    target: Any,
    runner: _Runner,
    *,
    cutoff: date,
    max_pages: int,
) -> tuple[list[dict[str, Any]], int, int]:
    first_rows, total, last = runner.get(
        ongjin_list_url(1),
        lambda soup, _final: _parse_list_page(
            target,
            soup,
            requested_page=1,
            cutoff=cutoff,
        ),
    )
    if last + 1 > max_pages:
        raise OngjinContractError(
            f"sentinel page {last + 1} exceeds max_pages cap"
        )
    pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
    for page in range(2, last + 1):
        page_rows, _, _ = runner.get(
            ongjin_list_url(page),
            lambda soup, _final, page=page: _parse_list_page(
                target,
                soup,
                requested_page=page,
                cutoff=cutoff,
                expected_total=total,
            ),
        )
        pages[page] = page_rows
    expected_last_size = total - ONGJIN_PAGE_SIZE * (last - 1)
    for page in range(1, last + 1):
        expected_size = ONGJIN_PAGE_SIZE if page < last else expected_last_size
        if len(pages[page]) != expected_size:
            raise OngjinContractError(
                f"page {page} has {len(pages[page])} of {expected_size} expected rows"
            )
    source_rows = [
        row for page in range(1, last + 1) for row in pages[page]
    ]
    if len(source_rows) != total:
        raise OngjinContractError("complete page union disagrees with advertised total")
    identities = [_clean(row.get("_identity")) for row in source_rows]
    if len(identities) != len(set(identities)):
        raise OngjinContractError("archive has duplicate lecture identities")

    sentinel_rows, sentinel_total, sentinel_last = runner.get(
        ongjin_list_url(last + 1),
        lambda soup, _final: _parse_list_page(
            target,
            soup,
            requested_page=last + 1,
            cutoff=cutoff,
            expected_total=total,
            allow_final_clamp=True,
        ),
    )
    if sentinel_total != total or sentinel_last != last:
        raise OngjinContractError("final-page clamp summary changed")
    if _source_signature(sentinel_rows) != _source_signature(pages[last]):
        raise OngjinContractError("final-page clamp contents changed")

    rechecks = ((1, "first"), (last, "last")) if last > 1 else ((1, "first"),)
    for page, label in rechecks:
        rows, _, _ = runner.get(
            ongjin_list_url(page),
            lambda soup, _final, page=page: _parse_list_page(
                target,
                soup,
                requested_page=page,
                cutoff=cutoff,
                expected_total=total,
            ),
        )
        if _source_signature(rows) != _source_signature(pages[page]):
            raise OngjinContractError(f"{label} page changed during stable recheck")
    return source_rows, last, len(rechecks)


def collect_incheon_ongjin_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 100,
    max_requests: int = 100,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete, fail-closed current/future Ongjin snapshot."""

    meta = _base_meta()
    if not is_ongjin_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the canonical/registered Ongjin owner"
        )
        return [], ONGJIN_PARSER, meta
    try:
        timeout = _positive_int(timeout, "timeout")
        max_pages = _positive_int(max_pages, "max_pages")
        detail_limit = _positive_int(detail_limit, "detail_limit")
        max_requests = _positive_int(max_requests, "max_requests")
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], ONGJIN_PARSER, meta

    runner = _Runner(
        timeout=timeout,
        maximum=max_requests,
        fetcher=fetcher or _default_fetcher,
        session_factory=session_factory or ongjin_session_factory,
        sleeper=sleeper,
    )
    try:
        source_rows, source_pages, stability_rechecks = _scan_archive(
            target,
            runner,
            cutoff=cutoff,
            max_pages=max_pages,
        )
        current_rows = [
            row
            for row in source_rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
        ]
        if len(current_rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise OngjinContractError(
                f"detail_limit cap allows {detail_limit} of "
                f"{len(current_rows)} required details"
            )
        required_list_requests = source_pages + 1 + stability_rechecks
        required_requests = required_list_requests + len(current_rows)
        if required_requests > max_requests:
            meta["source_cap_reached"] = True
            raise OngjinContractError(
                f"max_requests cap allows {max_requests} of {required_requests} "
                "required requests"
            )
        result: list[dict[str, Any]] = []
        for row in current_rows:
            identity = _clean(row.get("_identity"))
            result.append(
                runner.get(
                    ongjin_detail_url(identity),
                    lambda soup, final, row=row: _parse_detail(
                        soup,
                        final,
                        row,
                        cutoff,
                    ),
                )
            )
        semantic = [_semantic_key(row) for row in result]
        if len(semantic) != len(set(semantic)):
            raise OngjinContractError("current snapshot has semantic duplicates")
        deduper = dedupe_rows or _default_dedupe
        deduped = list(deduper(result))
        if len(deduped) != len(result):
            raise OngjinContractError("dedupe changed the atomic row count")
        identities = [_clean(row.get("provider_course_id")) for row in deduped]
        if len(identities) != len(set(identities)):
            raise OngjinContractError("returned provider identities are not unique")
        result = deduped
        meta.update(
            {
                "pages": source_pages,
                "page_sizes": tuple(
                    Counter(
                        int((row.get("raw_fields") or {}).get("list_page", 0))
                        for row in source_rows
                    )[page]
                    for page in range(1, source_pages + 1)
                ),
                "list_requests": required_list_requests,
                "required_list_requests": required_list_requests,
                "sentinel_requests": 1,
                "sentinel_page": source_pages + 1,
                "sentinel_kind": "exact_final_page_clamp",
                "stability_rechecks": stability_rechecks,
                "detail_attempts": len(current_rows),
                "detail_pages": len(current_rows),
                "detail_errors": 0,
                "source_total": len(source_rows),
                "source_rows": len(source_rows),
                "unique_source_rows": len(source_rows),
                "current_source_count": len(current_rows),
                "current_ids": tuple(_clean(row.get("_identity")) for row in current_rows),
                "publishable_current_count": len(result),
                "returned_count": len(result),
                "source_status_counts": dict(
                    Counter(
                        f"{_clean(row.get('_reception_status'))} {_clean(row.get('_course_status'))}"
                        for row in source_rows
                    )
                ),
                "current_source_status_counts": dict(
                    Counter(
                        f"{_clean(row.get('_reception_status'))} {_clean(row.get('_course_status'))}"
                        for row in current_rows
                    )
                ),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
                "source_branch_counts": dict(Counter(_clean(row.get("branch")) for row in source_rows)),
                "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
                "branch_count": len({_clean(row.get("branch")) for row in result}),
                "institution_registry": dict(ONGJIN_INSTITUTIONS),
                "institution_registry_count": len(ONGJIN_INSTITUTIONS),
                "detail_field_count": len(_DETAIL_LABELS),
                "safe_detail_field_count": len(_DETAIL_SAFE_LABELS),
                "private_detail_field_count": len(_DETAIL_PRIVATE_LABELS),
                "private_detail_values_read": 0,
                "application_control_count": 0,
                "audited_schedule_anomaly_count": sum(
                    bool((row.get("raw_fields") or {}).get("audited_schedule_anomaly"))
                    for row in result
                ),
                "semantic_duplicate_count": 0,
                "test_or_notice_row_count": 0,
                "pagination_detected": source_pages > 1,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "atomic_snapshot_complete": True,
                "source_cap_reached": False,
                "no_current_data": not result,
                "no_current_reason": (
                    "the complete Ongjin archive has no course whose education end date is current/future"
                    if not result
                    else ""
                ),
                "configured_collection_error": "",
            }
        )
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        if "cap" in _clean(exc):
            meta["source_cap_reached"] = True
        return [], ONGJIN_PARSER, meta
    finally:
        meta["network_requests"] = runner.requests
        meta["network_retry_count"] = runner.retries
        meta["sessions_created"] = 1
        runner.close()
    return result, ONGJIN_PARSER, meta


collect_ongjin_education = collect_incheon_ongjin_education
collect_courses = collect_incheon_ongjin_education
collect = collect_incheon_ongjin_education


__all__ = [
    "ONGJIN_AIA_INTERMEDIATE_PEM",
    "ONGJIN_AIA_INTERMEDIATE_SHA256",
    "ONGJIN_AUDITED_SCHEDULE_ANOMALIES",
    "ONGJIN_CANONICAL_CANDIDATE_ID",
    "ONGJIN_CANONICAL_URL",
    "ONGJIN_CATEGORIES",
    "ONGJIN_DISCOVERY_AUDIT",
    "ONGJIN_DUPLICATE_PROVIDER",
    "ONGJIN_EXCLUDED_SOURCE_AUDIT",
    "ONGJIN_INSTITUTIONS",
    "ONGJIN_LEAF_SHA256_AUDITED_2026_07_22",
    "ONGJIN_LEDGER_CANDIDATE_ID",
    "ONGJIN_LEDGER_URL",
    "ONGJIN_MUNICIPALITY_CODE",
    "ONGJIN_MUNICIPALITY_NAME",
    "ONGJIN_OWNER_BOUNDARY_AUDIT",
    "ONGJIN_OWNERSHIP_SCOPE",
    "ONGJIN_PAGE_SIZE",
    "ONGJIN_PARSER",
    "ONGJIN_PROVIDER",
    "ONGJIN_URL",
    "OngjinContractError",
    "canonical_ongjin_detail_identity",
    "collect",
    "collect_courses",
    "collect_incheon_ongjin_education",
    "collect_ongjin_education",
    "is_ongjin_education_target",
    "is_target",
    "ongjin_detail_url",
    "ongjin_list_url",
    "ongjin_session_factory",
]
