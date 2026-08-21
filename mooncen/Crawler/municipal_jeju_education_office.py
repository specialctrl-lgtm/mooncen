"""Fail-closed collector for the Jeju education-office reservation ledgers.

The official reservation service owns two independent public catalogues:
education/lectures and experiences/visits.  This collector reads only those
two category list pages and their public detail pages.  Login, application,
applicant, attachment, facility-rental, performance, event, notice, and admin
routes are deliberately outside the request allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
from bs4 import BeautifulSoup


JJE_RESERVATION_PROVIDER = "MUNI_ORG_JJE_GO_KR_3205C1E8"
JJE_RESERVATION_HOST = "org.jje.go.kr"
JJE_JEJU_CODE = "5011000000"
JJE_JEJU_NAME = "제주특별자치도 제주시"
JJE_SEOGWIPO_CODE = "5013000000"
JJE_SEOGWIPO_NAME = "제주특별자치도 서귀포시"
JJE_PAGE_SIZE = 10
JJE_MAX_HTML_BYTES = 4_000_000
JJE_RESERVATION_PARSER = (
    "jje_education_office_two_locked_ledgers+status_declared_totals+all_pages+"
    "empty_sentinels+stable_first_last+all_current_safe_details+exact_row_municipality+"
    "test_quarantine+category_boundary+get_only_no_application_login_attachment_pii_routes"
)


@dataclass(frozen=True)
class LedgerSpec:
    kind: str
    list_path: str
    list_menu: str
    detail_path: str
    detail_menu: str
    sid_key: str
    sid_pattern: re.Pattern[str]
    caption_token: str
    title_header: str
    target_header: str
    period_label: str
    place_label: str
    domain_category: str
    service_group: str
    ops_scope: str


LEDGERS: Mapping[str, LedgerSpec] = {
    "education": LedgerSpec(
        kind="education",
        list_path="/reserve/jjeEducation/list.jje",
        list_menu="DOM_000000502001000000",
        detail_path="/jjeEducation/view.jje",
        detail_menu="DOM_000000502002000000",
        sid_key="educationSid",
        sid_pattern=re.compile(r"ED_\d{13}"),
        caption_token="교육/강좌 테이블",
        title_header="교육/강좌명",
        target_header="교육대상",
        period_label="교육기간",
        place_label="교육장소",
        domain_category="교육·강좌",
        service_group="공공강좌",
        ops_scope="education",
    ),
    "experience": LedgerSpec(
        kind="experience",
        list_path="/reserve/jjeExperience/list.jje",
        list_menu="DOM_000000501001000000",
        detail_path="/jjeExperience/view.jje",
        detail_menu="DOM_000000501002000000",
        sid_key="experienceSid",
        sid_pattern=re.compile(r"EX_\d{13}"),
        caption_token="견학/체험 일정 테이블",
        title_header="체험명",
        target_header="체험대상",
        period_label="운영기간",
        place_label="견학/체험장소",
        domain_category="체험·견학",
        service_group="체험",
        ops_scope="experience",
    ),
}

JJE_EDUCATION_URL = "https://org.jje.go.kr/reserve/jjeEducation/list.jje?menuCd=DOM_000000502001000000"
JJE_EXPERIENCE_URL = "https://org.jje.go.kr/reserve/jjeExperience/list.jje?menuCd=DOM_000000501001000000"

STATUS_FILTERS: tuple[tuple[str, str], ...] = (("0", "예정"), ("1", "접수중"))
EMPTY_SENTINELS = (
    "게시물이 없습니다",
    "검색 결과가 없습니다",
    "조회된 결과가 없습니다",
    "등록된 게시물이 없습니다",
)
TEST_TOKENS = ("테스트", "샘플", "test", "sample")

# Exact official institution names are used only when a row detail does not
# provide a city-bearing place.  In particular, "동부" belongs to Seogwipo;
# no provider-wide Jeju-city fallback is permitted.
BRANCH_MUNICIPALITIES: Mapping[str, tuple[str, str, str]] = {
    "제주국제교육원(제주외국어학습센터)": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        "https://org.jje.go.kr/jiei/index.jje",
    ),
    "제주외국어학습센터": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        "https://org.jje.go.kr/jiei/index.jje",
    ),
    "동부외국문화학습관": (
        JJE_SEOGWIPO_CODE,
        JJE_SEOGWIPO_NAME,
        "https://org.jje.go.kr/jiei/index.jje",
    ),
    "서부외국문화학습관": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        "https://org.jje.go.kr/jiei/index.jje",
    ),
    "서귀포외국문화학습관": (
        JJE_SEOGWIPO_CODE,
        JJE_SEOGWIPO_NAME,
        "https://org.jje.go.kr/jiei/index.jje",
    ),
    "신제주외국문화학습관": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        "https://org.jje.go.kr/shjefl/index.jje",
    ),
    "제주다문화교육센터": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        "https://org.jje.go.kr/jjeis/index.jje",
    ),
    "제주융합과학연구원": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        "https://org.jje.go.kr/cisec/index.jje",
    ),
    "제주학생문화원": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        "https://org.jje.go.kr/lifelo/index.jje",
    ),
    "서귀포학생문화원야영수련장": (
        JJE_SEOGWIPO_CODE,
        JJE_SEOGWIPO_NAME,
        "https://org.jje.go.kr/sscamp/index.jje",
    ),
    "서귀포학생문화원": (
        JJE_SEOGWIPO_CODE,
        JJE_SEOGWIPO_NAME,
        "https://org.jje.go.kr/sgp/index.jje",
    ),
    "제주유아교육진흥원 본원(서귀포시)": (
        JJE_SEOGWIPO_CODE,
        JJE_SEOGWIPO_NAME,
        "https://org.jje.go.kr/jjkids/index.jje",
    ),
    "제주유아교육진흥원 회천분원(제주시)": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        "https://org.jje.go.kr/jjkids/index.jje",
    ),
    "학생안전지원과(제주시)": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        JJE_EDUCATION_URL,
    ),
    "학생안전지원(제주시)": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        JJE_EXPERIENCE_URL,
    ),
    "학생안전지원과(서귀포)": (
        JJE_SEOGWIPO_CODE,
        JJE_SEOGWIPO_NAME,
        JJE_EDUCATION_URL,
    ),
    "학생안전지원(서귀포)": (
        JJE_SEOGWIPO_CODE,
        JJE_SEOGWIPO_NAME,
        JJE_EXPERIENCE_URL,
    ),
    "공공도서관(제주도서관)": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        JJE_EDUCATION_URL,
    ),
    "공공도서관(서귀포도서관)": (
        JJE_SEOGWIPO_CODE,
        JJE_SEOGWIPO_NAME,
        JJE_EDUCATION_URL,
    ),
    "공공도서관(송악도서관)": (
        JJE_SEOGWIPO_CODE,
        JJE_SEOGWIPO_NAME,
        JJE_EDUCATION_URL,
    ),
    "공공도서관(제남도서관)": (
        JJE_SEOGWIPO_CODE,
        JJE_SEOGWIPO_NAME,
        JJE_EDUCATION_URL,
    ),
    "공공도서관(동녘도서관)": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        JJE_EDUCATION_URL,
    ),
    "공공도서관(한수풀도서관)": (
        JJE_JEJU_CODE,
        JJE_JEJU_NAME,
        JJE_EDUCATION_URL,
    ),
}


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class JejuEducationOfficeContractError(ValueError):
    pass


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[\s:：]+", "", _clean(value))


def _branch(value: Any) -> str:
    return re.sub(r"\s*([()])\s*", r"\1", _clean(value))


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _safe_origin(parsed: Any) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == JJE_RESERVATION_HOST
        and not parsed.username
        and not parsed.password
        and port is None
        and not parsed.fragment
    )


def target_kind(target: Any) -> Optional[str]:
    if _clean(_target_value(target, "provider")) != JJE_RESERVATION_PROVIDER:
        return None
    parsed = urlparse(_clean(_target_value(target, "url")))
    if not _safe_origin(parsed):
        return None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for kind, spec in LEDGERS.items():
        if parsed.path == spec.list_path and query == {"menuCd": spec.list_menu}:
            return kind
    return None


def is_jeju_education_office_target(target: Any) -> bool:
    return target_kind(target) is not None


def list_url(kind: str, status_filter: str, page: int) -> str:
    spec = LEDGERS.get(kind)
    if spec is None or status_filter not in {value for value, _ in STATUS_FILTERS}:
        raise JejuEducationOfficeContractError("invalid ledger/status partition")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise JejuEducationOfficeContractError("invalid one-based page")
    query = urlencode([("menuCd", spec.list_menu), ("reserveStatus", status_filter), ("startPage", page)])
    return f"https://{JJE_RESERVATION_HOST}{spec.list_path}?{query}"


def detail_url(kind: str, identity: Any) -> str:
    spec = LEDGERS.get(kind)
    value = _clean(identity)
    if spec is None or not spec.sid_pattern.fullmatch(value):
        raise JejuEducationOfficeContractError("invalid reservation identity")
    query = urlencode([("menuCd", spec.detail_menu), (spec.sid_key, value)])
    return f"https://{JJE_RESERVATION_HOST}{spec.detail_path}?{query}"


def _request_kind(url: Any) -> str:
    parsed = urlparse(_clean(url))
    if not _safe_origin(parsed):
        raise JejuEducationOfficeContractError("request left the education-office host")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if len(pairs) != len(dict(pairs)):
        raise JejuEducationOfficeContractError("duplicate request query key")
    query = dict(pairs)
    for kind, spec in LEDGERS.items():
        if parsed.path == spec.list_path:
            if (
                set(query) == {"menuCd", "reserveStatus", "startPage"}
                and query["menuCd"] == spec.list_menu
                and query["reserveStatus"] in {value for value, _ in STATUS_FILTERS}
                and re.fullmatch(r"[1-9]\d*", query["startPage"])
            ):
                return f"{kind}_list"
        if parsed.path == spec.detail_path:
            if (
                set(query) == {"menuCd", spec.sid_key}
                and query["menuCd"] == spec.detail_menu
                and spec.sid_pattern.fullmatch(query[spec.sid_key])
            ):
                return f"{kind}_detail"
    raise JejuEducationOfficeContractError(
        "only official education/experience list and public detail GET routes are allowlisted"
    )


def _session() -> requests.Session:
    current = requests.Session()
    current.trust_env = False
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenJejuEducationOfficeAudit/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return current


def _fetch(session: Any, url: str, timeout: int) -> Any:
    _request_kind(url)
    return session.get(url, timeout=timeout, allow_redirects=False)


def _html(value: Any, requested_url: str) -> str:
    if isinstance(value, BeautifulSoup):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        status = int(getattr(value, "status_code", 200) or 0)
        if status != 200:
            raise JejuEducationOfficeContractError(f"official request returned HTTP {status}")
        headers = getattr(value, "headers", {}) or {}
        content_type = _clean(headers.get("Content-Type"))
        if content_type and "html" not in content_type.lower():
            raise JejuEducationOfficeContractError("official response stopped being HTML")
        text = str(getattr(value, "text", "") or "")
        final_url = _clean(getattr(value, "url", requested_url))
        if final_url and final_url != requested_url:
            raise JejuEducationOfficeContractError("official response redirected outside exact route")
    if not text or len(text.encode("utf-8")) > JJE_MAX_HTML_BYTES:
        raise JejuEducationOfficeContractError("official HTML is empty or exceeds bounded size")
    return text


def _fetch_html(fetcher: Fetcher, session: Any, url: str, timeout: int) -> str:
    _request_kind(url)
    return _html(fetcher(session, url, timeout), url)


def _header_key(cell: Any) -> str:
    return _compact(cell.get("data-cell-header", ""))


def _cell_text(cell: Any) -> str:
    return _clean(cell.get_text(" ", strip=True) if cell is not None else "")


def _list_rows(html: str, spec: LedgerSpec, expected_status: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    table = next(
        (
            current
            for current in soup.find_all("table")
            if spec.caption_token
            in _clean(current.find("caption").get_text(" ", strip=True) if current.find("caption") else "")
        ),
        None,
    )
    if table is None:
        if any(token in _clean(soup.get_text(" ", strip=True)) for token in EMPTY_SENTINELS):
            return []
        raise JejuEducationOfficeContractError("official category table disappeared")
    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = {_header_key(cell): cell for cell in tr.find_all("td", recursive=False) if _header_key(cell)}
        if not cells:
            continue
        required = {
            "순번",
            "기관명",
            _compact(spec.title_header),
            "운영기간",
            "접수기간",
            _compact(spec.target_header),
            "예약상태",
        }
        if not required <= set(cells):
            raise JejuEducationOfficeContractError("official list columns changed")
        sequence_text = _cell_text(cells["순번"])
        if not re.fullmatch(r"[1-9]\d*", sequence_text):
            raise JejuEducationOfficeContractError("official declared sequence changed")
        title_cell = cells[_compact(spec.title_header)]
        link = title_cell.find("a", href=True)
        if link is None:
            raise JejuEducationOfficeContractError("official detail identity link disappeared")
        parsed_link = urlparse(_clean(link.get("href")))
        link_query = dict(parse_qsl(parsed_link.query, keep_blank_values=True))
        identity = _clean(link_query.get(spec.sid_key))
        if parsed_link.path != spec.detail_path or not spec.sid_pattern.fullmatch(identity):
            raise JejuEducationOfficeContractError("official detail link left category boundary")
        title = _cell_text(title_cell)
        branch = _branch(_cell_text(cells["기관명"]))
        status = _cell_text(cells["예약상태"])
        allowed_statuses = {"예정"} if expected_status == "예정" else {"접수중", "대기접수"}
        if not title or not branch or status not in allowed_statuses:
            raise JejuEducationOfficeContractError("official required fields/status changed")
        rows.append(
            {
                "sequence": int(sequence_text),
                "identity": identity,
                "title": title,
                "branch": branch,
                "period": _cell_text(cells["운영기간"]),
                "apply_period": _cell_text(cells["접수기간"]),
                "target": _cell_text(cells[_compact(spec.target_header)]),
                "source_status": status,
            }
        )
    if not rows and not any(token in _clean(soup.get_text(" ", strip=True)) for token in EMPTY_SENTINELS):
        raise JejuEducationOfficeContractError("empty category page lost its explicit sentinel")
    return rows


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[int, str, str], ...]:
    return tuple((int(row["sequence"]), _clean(row["identity"]), _clean(row["title"])) for row in rows)


def _crawl_status_partition(
    spec: LedgerSpec,
    status_filter: str,
    expected_status: str,
    *,
    session: Any,
    fetcher: Fetcher,
    timeout: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first_url = list_url(spec.kind, status_filter, 1)
    first = _list_rows(_fetch_html(fetcher, session, first_url, timeout), spec, expected_status)
    declared_total = first[0]["sequence"] if first else 0
    last_page = max(1, math.ceil(declared_total / JJE_PAGE_SIZE))
    if last_page > max_pages:
        raise JejuEducationOfficeContractError("max_pages cap prevents complete official ledger")
    pages: dict[int, list[dict[str, Any]]] = {1: first}
    for page in range(2, last_page + 1):
        page_url = list_url(spec.kind, status_filter, page)
        pages[page] = _list_rows(_fetch_html(fetcher, session, page_url, timeout), spec, expected_status)
    combined = [row for page in range(1, last_page + 1) for row in pages[page]]
    if len(combined) != declared_total:
        raise JejuEducationOfficeContractError("declared category total did not reconcile")
    expected_sequences = list(range(declared_total, 0, -1))
    if [row["sequence"] for row in combined] != expected_sequences:
        raise JejuEducationOfficeContractError("official category sequence boundary drifted")
    identities = [_clean(row["identity"]) for row in combined]
    if len(identities) != len(set(identities)):
        raise JejuEducationOfficeContractError("duplicate identity in official category partition")

    sentinel_page = last_page + 1
    sentinel = _list_rows(
        _fetch_html(fetcher, session, list_url(spec.kind, status_filter, sentinel_page), timeout),
        spec,
        expected_status,
    )
    if sentinel:
        raise JejuEducationOfficeContractError("post-last category sentinel is not empty")

    stable_first = _list_rows(_fetch_html(fetcher, session, first_url, timeout), spec, expected_status)
    if _page_signature(stable_first) != _page_signature(first):
        raise JejuEducationOfficeContractError("first category boundary changed during crawl")
    stable_pages = [1]
    if last_page > 1:
        stable_last = _list_rows(
            _fetch_html(
                fetcher,
                session,
                list_url(spec.kind, status_filter, last_page),
                timeout,
            ),
            spec,
            expected_status,
        )
        if _page_signature(stable_last) != _page_signature(pages[last_page]):
            raise JejuEducationOfficeContractError("last category boundary changed during crawl")
        stable_pages.append(last_page)
    return combined, {
        "declared_total": declared_total,
        "data_pages": 0 if declared_total == 0 else last_page,
        "last_page": last_page,
        "empty_sentinel_page": sentinel_page,
        "stable_pages": stable_pages,
        "list_requests": last_page + 2 + (1 if last_page > 1 else 0),
    }


def _detail_pairs(html: str, spec: LedgerSpec, source: Mapping[str, Any]) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = _clean(source.get("title"))
    if not any(_clean(node.get_text(" ", strip=True)) == title for node in soup.find_all("h3")):
        raise JejuEducationOfficeContractError("list/detail title identity mismatch")
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    expected_page = "교육/강좌" if spec.kind == "education" else "견학/체험"
    if expected_page not in page_title or "상세보기" not in page_title:
        raise JejuEducationOfficeContractError("detail page left official category")
    pairs: dict[str, str] = {}
    for item in soup.find_all("li"):
        label = item.find("em", recursive=False)
        value = item.find("span", recursive=False)
        if label is None or value is None:
            continue
        key = _clean(label.get_text(" ", strip=True)).rstrip(":：")
        if key:
            pairs[key] = _clean(value.get_text(" ", strip=True))
    required = {"운영기관", spec.period_label, "신청기간", spec.place_label}
    if not required <= set(pairs):
        raise JejuEducationOfficeContractError("public detail allowlist fields changed")
    pairs["운영기관"] = _branch(pairs["운영기관"])
    source_branch = _branch(source.get("branch"))
    detail_branch = pairs["운영기관"]
    detail_classification = _branch(pairs.get("분류"))
    if detail_branch == "공공도서관" and source_branch.startswith("공공도서관(") and detail_classification:
        detail_branch = f"{detail_branch}({detail_classification})"
    if detail_branch != source_branch:
        raise JejuEducationOfficeContractError("list/detail institution mismatch")
    source_period = _clean(source.get("period"))
    source_dates = re.findall(r"\d{4}-\d{2}-\d{2}", source_period)
    detail_dates = re.findall(r"\d{4}-\d{2}-\d{2}", pairs[spec.period_label])
    if len(source_dates) != 2 or detail_dates[:2] != source_dates:
        raise JejuEducationOfficeContractError("list/detail operation period mismatch")
    if pairs["신청기간"] != _clean(source.get("apply_period")):
        raise JejuEducationOfficeContractError("list/detail application period mismatch")
    return pairs


def _municipality(*, branch: str, title: str, place: str) -> tuple[str, str, str, str]:
    evidence: list[tuple[str, str, str, str]] = []
    place_text = _clean(place)
    if "서귀포시" in place_text:
        evidence.append((JJE_SEOGWIPO_CODE, JJE_SEOGWIPO_NAME, "detail_place", place_text))
    elif "제주시" in place_text:
        evidence.append((JJE_JEJU_CODE, JJE_JEJU_NAME, "detail_place", place_text))
    branch_text = _branch(branch)
    branch_match = BRANCH_MUNICIPALITIES.get(branch_text)
    if branch_match:
        evidence.append((branch_match[0], branch_match[1], "official_branch", branch_match[2]))
    title_text = _clean(title)
    if "[서귀포" in title_text or title_text.startswith("서귀포시"):
        evidence.append((JJE_SEOGWIPO_CODE, JJE_SEOGWIPO_NAME, "official_title", title_text))
    elif "[제주]" in title_text or "신제주" in title_text:
        evidence.append((JJE_JEJU_CODE, JJE_JEJU_NAME, "official_title", title_text))
    codes = {item[0] for item in evidence}
    if not evidence:
        raise JejuEducationOfficeContractError(f"municipality evidence missing for official branch: {branch_text}")
    if len(codes) != 1:
        raise JejuEducationOfficeContractError(f"conflicting municipality evidence for official branch: {branch_text}")
    # Detail place is the strongest row-local evidence, followed by the exact
    # official branch registry and finally an explicit title prefix.
    priority = {"detail_place": 0, "official_branch": 1, "official_title": 2}
    selected = min(evidence, key=lambda item: priority[item[2]])
    return selected


def _is_test_title(value: Any) -> bool:
    text = _clean(value).lower()
    return any(token in text for token in TEST_TOKENS)


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _sha256(values: Iterable[Any]) -> str:
    joined = "\n".join(sorted(_clean(value) for value in values))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def collect_jeju_education_office_reservations(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    session_factory: SessionFactory = _session,
    fetcher: Fetcher = _fetch,
    dedupe_fn: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    kind = target_kind(target)
    meta: dict[str, Any] = {
        "configured_collection_error": "",
        "source_total": 0,
        "returned_count": 0,
        "excluded_test_count": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "application_endpoint_requests": 0,
        "pii_values_persisted": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
    }
    if kind is None:
        meta["configured_collection_error"] = "target is not a registered Jeju education-office ledger"
        return [], JJE_RESERVATION_PARSER, meta
    spec = LEDGERS[kind]
    current_session: Any = None
    try:
        if isinstance(max_pages, bool) or max_pages < 1:
            raise JejuEducationOfficeContractError("invalid max_pages")
        if isinstance(detail_limit, bool) or detail_limit < 0:
            raise JejuEducationOfficeContractError("invalid detail_limit")
        current_session = session_factory()
        source_rows: list[dict[str, Any]] = []
        status_meta: dict[str, dict[str, Any]] = {}
        for status_filter, expected_status in STATUS_FILTERS:
            rows, partition_meta = _crawl_status_partition(
                spec,
                status_filter,
                expected_status,
                session=current_session,
                fetcher=fetcher,
                timeout=timeout,
                max_pages=max_pages,
            )
            for row in rows:
                row["status_filter"] = status_filter
            source_rows.extend(rows)
            status_meta[expected_status] = partition_meta
        source_ids = [_clean(row["identity"]) for row in source_rows]
        if len(source_ids) != len(set(source_ids)):
            raise JejuEducationOfficeContractError("identity appeared in multiple status partitions")
        eligible = [row for row in source_rows if not _is_test_title(row["title"])]
        excluded_test_count = len(source_rows) - len(eligible)
        if len(eligible) > detail_limit:
            raise JejuEducationOfficeContractError("detail_limit cap prevents all current details")

        output: list[dict[str, Any]] = []
        municipality_counts: dict[str, int] = {}
        for source in eligible:
            public_url = detail_url(kind, source["identity"])
            detail = _detail_pairs(_fetch_html(fetcher, current_session, public_url, timeout), spec, source)
            code, full_name, evidence_kind, evidence_value = _municipality(
                branch=source["branch"],
                title=source["title"],
                place=detail[spec.place_label],
            )
            municipality_counts[full_name] = municipality_counts.get(full_name, 0) + 1
            open_now = source["source_status"] == "접수중"
            branch_hash = hashlib.sha256(_clean(source["branch"]).encode("utf-8")).hexdigest()[:16]
            row = {
                "provider": JJE_RESERVATION_PROVIDER,
                "provider_course_id": f"jje-{kind}:{source['identity']}",
                "title": source["title"],
                "branch": source["branch"],
                "branch_code": f"JJE_{branch_hash.upper()}",
                "preserve_branch": True,
                "category": "교육/강좌" if kind == "education" else "견학/체험",
                "raw_url": public_url,
                "application_url": public_url,
                "application_type": "ONLINE_RESERVATION" if open_now else "INFO_ONLY",
                "reservation_available": open_now,
                "status": source["source_status"],
                "source_status": source["source_status"],
                "period": source["period"],
                "apply_period": source["apply_period"],
                "target": source["target"],
                "venue_name": detail[spec.place_label] or source["branch"],
                "program_type": "강좌" if kind == "education" else "체험",
                "collection_category": "공공예약",
                "domain_category": spec.domain_category,
                "source_group": "municipal_reservation",
                "operator_type": "교육청/공공기관",
                "service_group": spec.service_group,
                "service_group_policy": "locked",
                "collection_type": JJE_RESERVATION_PARSER,
                "municipality_code": code,
                "municipality_full_name": full_name,
                "municipality_region_verified": True,
                "municipality_evidence_kind": evidence_kind,
                "municipality_evidence": evidence_value,
                "description": source["title"],
                "application_method_raw": detail.get("신청방법", ""),
                "raw_fields": {
                    "parser": JJE_RESERVATION_PARSER,
                    "ledger": kind,
                    "identity": source["identity"],
                    "sequence": source["sequence"],
                    "status_filter": source["status_filter"],
                },
            }
            place = detail[spec.place_label]
            if "제주시" in place or "서귀포시" in place:
                row["venue_address"] = place
                row["address"] = place
            output.append({key: value for key, value in row.items() if value not in (None, "", [], {})})

        deduper = dedupe_fn or _default_dedupe
        deduped = list(deduper(output))
        if len(deduped) != len(output) or {_clean(row.get("provider_course_id")) for row in deduped} != {
            _clean(row.get("provider_course_id")) for row in output
        }:
            raise JejuEducationOfficeContractError("dedupe changed official current identity cardinality")
        meta.update(
            {
                "ledger": kind,
                "ops_scope": spec.ops_scope,
                "source_total": len(source_rows),
                "returned_count": len(deduped),
                "excluded_test_count": excluded_test_count,
                "status_declared_totals": {status: value["declared_total"] for status, value in status_meta.items()},
                "status_data_pages": {status: value["data_pages"] for status, value in status_meta.items()},
                "empty_sentinel_pages": {status: value["empty_sentinel_page"] for status, value in status_meta.items()},
                "stable_boundary_pages": {status: value["stable_pages"] for status, value in status_meta.items()},
                "data_pages": sum(value["data_pages"] for value in status_meta.values()),
                "list_requests": sum(value["list_requests"] for value in status_meta.values()),
                "detail_requests": len(eligible),
                "municipality_counts": dict(sorted(municipality_counts.items())),
                "source_identity_sha256": _sha256(source_ids),
                "output_identity_sha256": _sha256(row["provider_course_id"] for row in deduped),
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not deduped,
                "no_current_reason": ("official current/future category partitions are empty" if not deduped else ""),
                "excluded_category_routes": [
                    "facility_rental",
                    "performance",
                    "event",
                    "notice",
                ],
            }
        )
        return deduped, JJE_RESERVATION_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc) or exc.__class__.__name__
        return [], JJE_RESERVATION_PARSER, meta
    finally:
        close = getattr(current_session, "close", None)
        if callable(close):
            close()


collect = collect_jeju_education_office_reservations
