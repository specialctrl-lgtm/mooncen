"""Fail-closed collector for Gyeryong's official lifelong-learning ledger.

The configured ``www`` provider owns the course ``mng_no`` namespace.  The
``farm`` host serves the same 207 identities and is excluded as a mirror.  The
collector requests only the public list and current-course detail routes; it
never requests application, login, download, image, or contact endpoints.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

GYERYONG_PROVIDER = "MUNI_WWW_GYERYONG_GO_KR_42F86CD2"
GYERYONG_CANDIDATE_ID = "MUNI_IR_8194B259C1E5"
GYERYONG_EXCLUDED_FARM_PROVIDER = "MUNI_FARM_GYERYONG_GO_KR_42EC6CF6"
GYERYONG_EXCLUDED_FARM_DETAIL_PROVIDER = "MUNI_FARM_GYERYONG_GO_KR_12AC4C02"
GYERYONG_EXCLUDED_FARM_CANDIDATE_ID = "MUNI_IR_E50BA9F5358E"
GYERYONG_MUNICIPALITY_CODE = "4425000000"
GYERYONG_MUNICIPALITY_NAME = "충청남도 계룡시"
GYERYONG_HOST = "www.gyeryong.go.kr"
GYERYONG_PATH = "/lll/html/sub03/030102.html"
GYERYONG_URL = f"https://{GYERYONG_HOST}{GYERYONG_PATH}"
GYERYONG_FARM_URL = f"https://farm.gyeryong.go.kr{GYERYONG_PATH}"
GYERYONG_PAGE_SIZE = 10
GYERYONG_MAX_PAGES = 100
GYERYONG_MAX_DETAILS = 300
GYERYONG_MAX_HTML_BYTES = 4_000_000
GYERYONG_TEST_IDENTITY = "60d494ccc32a16ca3009f88732e18c41"
GYERYONG_TEST_TITLE = "테스트 평생교육(유료_신청금지)"
GYERYONG_PARSER = (
    "gyeryong_www_owner+mng_no_identity+all_post_pages+exact_final_clamp+"
    "stable_boundaries+all_current_details+test_exclusion+no_private_endpoints"
)

GYERYONG_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "owner": {
        "provider": GYERYONG_PROVIDER,
        "candidate_id": GYERYONG_CANDIDATE_ID,
        "url": GYERYONG_URL,
        "decision": "retain_configured_www_identity_owner",
    },
    "farm_mirror": {
        "providers": (GYERYONG_EXCLUDED_FARM_PROVIDER, GYERYONG_EXCLUDED_FARM_DETAIL_PROVIDER),
        "candidate_id": GYERYONG_EXCLUDED_FARM_CANDIDATE_ID,
        "url": GYERYONG_FARM_URL,
        "decision": "exclude_exact_mng_no_ledger_mirror",
    },
    "province_intro": {"decision": "discovery_only; links to separate provincial learning site"},
    "unofficial_articles": {"decision": "information_only; no official application identity"},
}

GYERYONG_EDUCATION_REGISTRY = (
    ("__intro__", "교육구분전체"),
    ("0201", "평생교육"),
    ("0202", "정보화교육"),
    ("0206", "면·동 평생학습센터"),
    ("0204", "면·동 문화강좌교육"),
    ("0205", "소소마루 프로그램"),
    ("0203", "농업인교육"),
    ("0207", "건강교육"),
    ("0208", "한훈기념관"),
)
GYERYONG_PLACE_REGISTRY = (
    ("__intro__", "교육장소전체"),
    ("0500", "온라인"),
    ("0502", "농업기술센터"),
    ("0503", "보건소"),
    ("0504", "계룡청년공간 소소마루"),
    ("0501", "주민자치센터"),
    ("0505", "계룡시평생학습관 1교육실"),
    ("0506", "계룡시청 3층 대회의실"),
    ("0507", "계룡시평생학습관"),
    ("0508", "평생학습관 3층 코딩센터1"),
    ("0509", "키즈코딩센터(엄사도서관3층)"),
    ("0510", "엄사도서관"),
    ("0511", "계룡도서관"),
    ("0512", "금암동사무소"),
    ("0513", "엄사면주민자치센터"),
    ("0514", "두마면사무소"),
    ("0515", "신도안면사무소"),
    ("0518", "한훈기념관 다목적실"),
    ("0516", "보훈회관"),
    ("0517", "기타"),
)
GYERYONG_OPERATOR_REGISTRY = (
    ("__intro__", "운영주체전체"),
    ("0606", "계룡시 평생학습관"),
    ("0601", "계룡시청"),
    ("0602", "보건소"),
    ("0605", "한훈기념관"),
    ("0603", "농업기술센터"),
    ("0604", "교육"),
)
GYERYONG_METHOD_REGISTRY = (
    ("__intro__", "모집방법별검색"),
    ("0801", "방문"),
    ("0802", "전화"),
    ("0803", "인터넷"),
    ("0804", "혼합"),
    ("0805", "기타"),
    ("0806", "교육"),
)
GYERYONG_SEARCH_REGISTRY = (("edu_nm", "교육명"), ("dtl_cn", "상세내용"), ("edu_trgt", "교육대상"))
GYERYONG_BRANCH_REGISTRY = frozenset(label for value, label in GYERYONG_PLACE_REGISTRY if value != "__intro__")

_STATUS = {
    "접수중": "OPEN",
    "대기접수": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
}
_ID_RE = re.compile(r"[0-9a-f]{32}")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_INT_RE = re.compile(r"\d[\d,]*")
_SPACE_RE = re.compile(r"\s+")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_DETAIL_QUERY_KEYS = (
    "mode",
    "mng_no",
    "sch_edu_ty",
    "sch_edu_se",
    "sch_area",
    "sch_edu_place",
    "sch_oper_mby",
    "sch_edu_bgng_ymd",
    "sch_edu_end_ymd",
    "sch_rcrit_mth",
    "skey",
    "sval",
)
_DETAIL_REQUIRED = frozenset(
    {
        "학기명",
        "운영주체",
        "교육기간",
        "접수기간",
        "취소기간",
        "교육장소",
        "교육주기",
        "교육대상",
        "교육시간",
        "신청/정원",
        "문의",
        "신청방법",
    }
)


class GyeryongContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Page:
    requested: int
    reported: int
    last: int
    rows: tuple[dict[str, Any], ...]


def _clean(v: Any) -> str:
    return _SPACE_RE.sub(" ", str(v or "").replace("\xa0", " ")).strip()


def _target_value(t: Any, k: str) -> Any:
    return t.get(k) if isinstance(t, Mapping) else getattr(t, k, None)


def _strict_base(url: Any) -> bool:
    p = urlparse(_clean(url))
    try:
        port = p.port
    except ValueError:
        return False
    return (
        p.scheme == "https"
        and (p.hostname or "").lower() == GYERYONG_HOST
        and port is None
        and not p.username
        and not p.password
        and p.path == GYERYONG_PATH
        and not p.query
        and not p.fragment
    )


def is_gyeryong_target(target: Any) -> bool:
    return _clean(_target_value(target, "provider")) == GYERYONG_PROVIDER and _strict_base(_target_value(target, "url"))


is_target = is_gyeryong_target


def gyeryong_source_identity(identity: Any) -> str:
    value = _clean(identity)
    if not _ID_RE.fullmatch(value):
        raise ValueError("invalid Gyeryong identity")
    return f"{GYERYONG_PROVIDER}:course:{value}"


def gyeryong_list_data(page: int) -> dict[str, str]:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be positive")
    return {"sch_edu_ty": "0102", "skey": "", "sval": "", "GotoPage": str(page)}


def gyeryong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _ID_RE.fullmatch(value):
        raise ValueError("invalid Gyeryong identity")
    values = (
        ("mode", "V"),
        ("mng_no", value),
        ("sch_edu_ty", "0102"),
        ("sch_edu_se", ""),
        ("sch_area", ""),
        ("sch_edu_place", ""),
        ("sch_oper_mby", ""),
        ("sch_edu_bgng_ymd", ""),
        ("sch_edu_end_ymd", ""),
        ("sch_rcrit_mth", ""),
        ("skey", ""),
        ("sval", ""),
    )
    return f"{GYERYONG_URL}?{urlencode(values)}"


def gyeryong_session_factory() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {"User-Agent": "Mozilla/5.0 Chrome/138.0", "Accept-Language": "ko-KR,ko;q=0.9", "Referer": GYERYONG_URL}
    )
    return s


def _default_fetcher(
    session: Any, method: str, url: str, *, timeout: int, data: Optional[Mapping[str, str]] = None
) -> Any:
    if method == "POST":
        return session.post(url, data=data, timeout=timeout, allow_redirects=False)
    if method == "GET":
        return session.get(url, timeout=timeout, allow_redirects=False)
    raise GyeryongContractError("unaudited method")


def _allowed(method: str, url: str, data: Optional[Mapping[str, str]]) -> bool:
    p = urlparse(url)
    try:
        query = parse_qsl(p.query, keep_blank_values=True, strict_parsing=True)
        port = p.port
    except (ValueError, TypeError):
        return False
    if (
        p.scheme != "https"
        or (p.hostname or "").lower() != GYERYONG_HOST
        or port is not None
        or p.username
        or p.password
        or p.path != GYERYONG_PATH
        or p.fragment
    ):
        return False
    if method == "POST":
        return (
            not query
            and isinstance(data, Mapping)
            and dict(data) == gyeryong_list_data(int(_clean(data.get("GotoPage"))))
        )
    if method != "GET" or data is not None or tuple(k for k, _ in query) != _DETAIL_QUERY_KEYS:
        return False
    q = dict(query)
    return (
        q["mode"] == "V"
        and bool(_ID_RE.fullmatch(q["mng_no"]))
        and q["sch_edu_ty"] == "0102"
        and all(q[k] == "" for k in _DETAIL_QUERY_KEYS[3:])
    )


def _soup(response: Any, requested: str) -> BeautifulSoup:
    if int(getattr(response, "status_code", 200)) != 200 or getattr(response, "history", None):
        raise GyeryongContractError("HTTP/redirect changed")
    headers = getattr(response, "headers", {}) or {}
    if headers.get("Location") or headers.get("location"):
        raise GyeryongContractError("redirect changed")
    if "html" not in _clean(headers.get("Content-Type", "text/html")).lower():
        raise GyeryongContractError("non-HTML response")
    if _clean(getattr(response, "url", requested) or requested) != requested:
        raise GyeryongContractError("response URL changed")
    content = getattr(response, "content", None) or str(getattr(response, "text", response)).encode()
    if not content or len(content) > GYERYONG_MAX_HTML_BYTES:
        raise GyeryongContractError("empty/oversize HTML")
    return BeautifulSoup(content, "html.parser")


def _options(node: Any) -> tuple[tuple[str, str], ...]:
    return (
        tuple((_clean(o.get("value")), _clean(o.get_text(" ", strip=True))) for o in node.select("option"))
        if node
        else ()
    )


def _list_contract(soup: BeautifulSoup, page: int) -> tuple[Any, int, int]:
    forms = [f for f in soup.select("form[name='searchFrm']") if _clean(f.get("method")).upper() == "POST"]
    if len(forms) != 1 or urlparse(urljoin(GYERYONG_URL, _clean(forms[0].get("action")))).path != GYERYONG_PATH:
        raise GyeryongContractError(f"page {page}: search form changed")
    form = forms[0]
    registries = (
        ("sch_edu_se", GYERYONG_EDUCATION_REGISTRY),
        ("sch_edu_place", GYERYONG_PLACE_REGISTRY),
        ("sch_oper_mby", GYERYONG_OPERATOR_REGISTRY),
        ("sch_rcrit_mth", GYERYONG_METHOD_REGISTRY),
        ("skey", GYERYONG_SEARCH_REGISTRY),
    )
    for name, expected in registries:
        if _options(form.select_one(f"select[name='{name}']")) != expected:
            raise GyeryongContractError(f"page {page}: {name} registry changed")
    for name in ("sch_edu_bgng_ymd", "sch_edu_end_ymd", "sval"):
        n = form.select_one(f"input[name='{name}']")
        if n is None or _clean(n.get("value")):
            raise GyeryongContractError(f"page {page}: unfiltered scope changed")
    last_node = soup.select_one(".pagination a[aria-label='last'][onclick]")
    active = soup.select_one(".pagination .page-item.active a[onclick]")
    lm = re.fullmatch(r"postPrintPage\((\d+)\);", _clean(last_node.get("onclick")) if last_node else "")
    am = re.fullmatch(r"postPrintPage\((\d+)\);", _clean(active.get("onclick")) if active else "")
    if not lm or not am:
        raise GyeryongContractError(f"page {page}: pagination changed")
    last, reported = int(lm.group(1)), int(am.group(1))
    if last < 1 or reported != min(page, last):
        raise GyeryongContractError(f"page {page}: clamp/report changed")
    return form, reported, last


def _two_dates(v: Any, identity: str, field: str) -> tuple[date, date]:
    found = _DATE_RE.findall(_clean(v))
    if len(found) != 2:
        raise GyeryongContractError(f"course {identity}: {field} dates changed")
    a, b = (date(*map(int, x)) for x in found)
    if b < a:
        raise GyeryongContractError(f"course {identity}: reversed {field}")
    return a, b


def _pairs(root: Any) -> dict[str, str]:
    result = {}
    for li in root.select("ul.list_con:not(.btn-lst) > li"):
        key = _clean(li.select_one(":scope > span").get_text(" ", strip=True)) if li.select_one(":scope > span") else ""
        vals = [_clean(e.get_text(" ", strip=True)) for e in li.select(":scope > em")]
        if not key or not vals or key in result:
            raise GyeryongContractError("course fields changed")
        result[key] = " ".join(x for x in vals if x)
    return result


def _parse_card(card: Any, page: int) -> dict[str, Any]:
    links = card.select(":scope > .inner > a[href]")
    if len(links) != 1:
        raise GyeryongContractError(f"page {page}: card link changed")
    link = links[0]
    parsed = urlparse(urljoin(GYERYONG_URL, _clean(link.get("href"))))
    query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if tuple(k for k, _ in query) != _DETAIL_QUERY_KEYS:
        raise GyeryongContractError(f"page {page}: detail query changed")
    q = dict(query)
    identity = q["mng_no"]
    if (
        parsed.path != GYERYONG_PATH
        or q["mode"] != "V"
        or not _ID_RE.fullmatch(identity)
        or q["sch_edu_ty"] != "0102"
        or any(q[k] for k in _DETAIL_QUERY_KEYS[3:])
    ):
        raise GyeryongContractError(f"page {page}: unsafe detail")
    accept = link.select_one(".accept")
    status_text = (
        _clean(accept.select_one("span").get_text(" ", strip=True)) if accept and accept.select_one("span") else ""
    )
    method = _clean(accept.select_one("em").get_text(" ", strip=True)) if accept and accept.select_one("em") else ""
    title = (
        _clean(link.select_one(".list__divps .tit").get_text(" ", strip=True))
        if link.select_one(".list__divps .tit")
        else ""
    )
    pairs = _pairs(link)
    required = {"학 기 명", "교육기간", "교육시간", "접수기간", "신청/정원"}
    if (
        not title
        or status_text not in _STATUS
        or method not in {x[1] for x in GYERYONG_METHOD_REGISTRY[1:]}
        or not required.issubset(pairs)
    ):
        raise GyeryongContractError(f"course {identity}: card contract changed")
    event_start, event_end = _two_dates(pairs["교육기간"], identity, "education")
    if _clean(pairs["접수기간"]) == "상시접수":
        apply_start = apply_end = None
    else:
        apply_start, apply_end = _two_dates(pairs["접수기간"], identity, "application")
    nums = [int(x.replace(",", "")) for x in _INT_RE.findall(pairs["신청/정원"])]
    if len(nums) != 3:
        raise GyeryongContractError(f"course {identity}: list capacity changed")
    return {
        "identity": identity,
        "source_identity": gyeryong_source_identity(identity),
        "detail_url": gyeryong_detail_url(identity),
        "page": page,
        "title": title,
        "source_status": status_text,
        "status": _STATUS[status_text],
        "method": method,
        "semester": pairs["학 기 명"],
        "event_start": event_start,
        "event_end": event_end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule": pairs["교육시간"],
        "applicants": nums[0],
        "waiting": nums[1],
        "capacity": nums[2],
    }


def _parse_page(soup: BeautifulSoup, page: int, expected_last: Optional[int] = None) -> _Page:
    _, reported, last = _list_contract(soup, page)
    if expected_last is not None and last != expected_last:
        raise GyeryongContractError(f"page {page}: last page drift")
    cards = soup.select(".program_con.edu_list > .col")
    rows = tuple(_parse_card(x, page) for x in cards)
    if not rows or len(rows) > GYERYONG_PAGE_SIZE or (reported < last and len(rows) != GYERYONG_PAGE_SIZE):
        raise GyeryongContractError(f"page {page}: row count changed")
    ids = [r["identity"] for r in rows]
    if len(ids) != len(set(ids)):
        raise GyeryongContractError(f"page {page}: duplicate identities")
    return _Page(page, reported, last, rows)


def _signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.reported,
        page.last,
        tuple((r["source_identity"], r["title"], r["source_status"], r["event_end"], r["capacity"]) for r in page.rows),
    )


def _parse_detail(row: Mapping[str, Any], soup: BeautifulSoup) -> tuple[Optional[dict[str, Any]], Counter[str]]:
    identity = str(row["identity"])
    views = soup.select(".program_con.program_view")
    if len(views) != 1:
        raise GyeryongContractError(f"course {identity}: detail root changed")
    view = views[0]
    title_node = view.select_one(".in_top .tit")
    if title_node is None:
        raise GyeryongContractError(f"course {identity}: detail title missing")
    for badge in title_node.select(".cond"):
        badge.extract()
    title = _clean(title_node.get_text(" ", strip=True))
    list_title = str(row["title"])
    if title != list_title and not (list_title.endswith("...") and title.startswith(list_title[:-3])):
        raise GyeryongContractError(f"course {identity}: title/status drift")
    accept = view.select_one(".accept")
    source_status = (
        _clean(accept.select_one("span").get_text(" ", strip=True)) if accept and accept.select_one("span") else ""
    )
    method = _clean(accept.select_one("em").get_text(" ", strip=True)) if accept and accept.select_one("em") else ""
    if source_status != row["source_status"] or method != row["method"]:
        raise GyeryongContractError(f"course {identity}: title/status drift")
    pairs = _pairs(view)
    unknown = set(pairs) - (_DETAIL_REQUIRED | {"대기자/정원"})
    if not _DETAIL_REQUIRED.issubset(pairs) or unknown:
        raise GyeryongContractError(f"course {identity}: detail fields changed")
    official_operators = {label for value, label in GYERYONG_OPERATOR_REGISTRY if value != "__intro__"}
    if pairs["운영주체"] not in official_operators:
        raise GyeryongContractError(f"course {identity}: operator changed")
    branch = pairs["교육장소"]
    if branch not in GYERYONG_BRANCH_REGISTRY:
        raise GyeryongContractError(f"course {identity}: unaudited official branch")
    event = _two_dates(pairs["교육기간"], identity, "detail education")
    apply = _two_dates(pairs["접수기간"], identity, "detail application")
    if event != (row["event_start"], row["event_end"]) or apply != (row["apply_start"], row["apply_end"]):
        raise GyeryongContractError(f"course {identity}: detail dates drift")
    nums = [int(x.replace(",", "")) for x in _INT_RE.findall(pairs["신청/정원"])]
    if len(nums) != 2 or nums != [row["applicants"], row["capacity"]]:
        raise GyeryongContractError(f"course {identity}: detail capacity drift")
    if "대기자/정원" in pairs:
        waits = [int(x.replace(",", "")) for x in _INT_RE.findall(pairs["대기자/정원"])]
        if len(waits) != 2 or waits[0] != row["waiting"]:
            raise GyeryongContractError(f"course {identity}: waitlist drift")
    controls = soup.select("a[href*='edu_no=']")
    expected_control = source_status in {"접수중", "대기접수"}
    if len(controls) != int(expected_control):
        raise GyeryongContractError(f"course {identity}: reservation control changed")
    for a in controls:
        q = parse_qsl(
            urlparse(urljoin(GYERYONG_URL, _clean(a.get("href")))).query, keep_blank_values=True, strict_parsing=True
        )
        if q != [("edu_no", identity), ("mode", "W")]:
            raise GyeryongContractError(f"course {identity}: reservation identity drift")
    attachments = view.select("ul.list_con.btn-lst a[href]")
    for a in attachments:
        p = urlparse(urljoin(GYERYONG_URL, _clean(a.get("href"))))
        q = parse_qsl(p.query, keep_blank_values=True, strict_parsing=True)
        if p.path != "/_prog/dn00/" or len(q) != 1 or q[0][0] != "file_id" or not _ID_RE.fullmatch(q[0][1]):
            raise GyeryongContractError(f"course {identity}: attachment control changed")
    labels = {_clean(x.get_text(" ", strip=True)) for x in soup.select("table th")}
    if not {"담당강사", "수강료", "강좌 상세설명"}.issubset(labels):
        raise GyeryongContractError(f"course {identity}: descriptive table changed")
    fee = ""
    for th in soup.select("table th"):
        if _clean(th.get_text(" ", strip=True)) == "수강료" and th.find_next_sibling("td"):
            fee = _clean(th.find_next_sibling("td").get_text(" ", strip=True))
    counters = Counter(reservation_controls=len(controls), attachments=len(attachments), sensitive=3)
    if identity == GYERYONG_TEST_IDENTITY:
        if title != GYERYONG_TEST_TITLE:
            raise GyeryongContractError("audited test identity title changed")
        return None, counters
    if title == GYERYONG_TEST_TITLE or title.startswith("테스트 평생교육"):
        raise GyeryongContractError("unknown operational-test row")
    event_period = f"{row['event_start'].isoformat()} ~ {row['event_end'].isoformat()}"
    apply_period = (
        f"{row['apply_start'].isoformat()} ~ {row['apply_end'].isoformat()}"
        if row["apply_start"] is not None and row["apply_end"] is not None
        else "상시접수"
    )
    output = {
        "provider": GYERYONG_PROVIDER,
        "municipality_code": GYERYONG_MUNICIPALITY_CODE,
        "municipality_name": GYERYONG_MUNICIPALITY_NAME,
        "municipality_full_name": GYERYONG_MUNICIPALITY_NAME,
        "provider_course_id": row["source_identity"],
        "prefer_incoming_provider_course_id": True,
        "source_course_id": identity,
        "title": title,
        "description": title,
        "status": row["status"],
        "source_status": source_status,
        "start_date": row["event_start"].isoformat(),
        "end_date": row["event_end"].isoformat(),
        "period": event_period,
        "apply_start_date": row["apply_start"].isoformat() if row["apply_start"] else "",
        "apply_end_date": row["apply_end"].isoformat() if row["apply_end"] else "",
        "apply_period": apply_period,
        "schedule": pairs["교육시간"],
        "schedule_raw": pairs["교육시간"],
        "target": pairs["교육대상"],
        "branch": branch,
        "branch_code": f"{GYERYONG_PROVIDER}:place:{branch}",
        "preserve_branch": True,
        "venue": branch,
        "venue_name": branch,
        "category": "평생교육",
        "program_type": "교육",
        "fee": fee,
        "capacity": row["capacity"],
        "capacity_total": row["capacity"],
        "capacity_current": row["applicants"],
        "applicants": row["applicants"],
        "application_method": method,
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "source_url": row["detail_url"],
        "raw_url": row["detail_url"],
        "application_url": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GYERYONG_PARSER,
        "raw_fields": {"semester": pairs["학기명"], "operator": pairs["운영주체"]},
    }
    return output, counters


def _base_meta(cutoff: date) -> dict[str, Any]:
    return {
        "provider": GYERYONG_PROVIDER,
        "municipality_code": GYERYONG_MUNICIPALITY_CODE,
        "audit_date": cutoff.isoformat(),
        "logical_requests": 0,
        "physical_requests": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "request_retry_count": 0,
        "reservation_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_values_persisted": 0,
        "source_cap_reached": False,
        "snapshot_complete": False,
    }


def _privacy(rows: Iterable[Mapping[str, Any]]) -> int:
    forbidden = {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "teacher",
        "attachments",
        "detail_description",
        "source_html",
    }
    return sum(
        len(set(r) & forbidden) + len(_PHONE_RE.findall(repr(r))) + len(_EMAIL_RE.findall(repr(r))) for r in rows
    )


def collect_gyeryong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = GYERYONG_MAX_PAGES,
    detail_limit: int = GYERYONG_MAX_DETAILS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[Callable[[], Any]] = None,
    fetcher: Optional[Callable[..., Any]] = None,
    dedupe_rows: Optional[Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    try:
        cutoff = (
            today.date()
            if isinstance(today, datetime)
            else today
            if isinstance(today, date)
            else date.fromisoformat(_clean(today))
            if today
            else datetime.now(ZoneInfo("Asia/Seoul")).date()
        )
    except Exception:
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
        meta = _base_meta(cutoff)
        meta["configured_collection_error"] = "today is invalid"
        return [], GYERYONG_PARSER, meta
    meta = _base_meta(cutoff)
    session = None
    listed: list[dict[str, Any]] = []
    if not is_gyeryong_target(target):
        meta["configured_collection_error"] = "target does not match Gyeryong www owner"
        return [], GYERYONG_PARSER, meta
    try:
        timeout, max_pages, detail_limit = int(timeout), int(max_pages), int(detail_limit)
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise ValueError
    except Exception:
        meta["configured_collection_error"] = "invalid limits"
        return [], GYERYONG_PARSER, meta
    try:
        session = (session_factory or gyeryong_session_factory)()
        fetch = fetcher or _default_fetcher
        lock = threading.Lock()

        def load(method: str, url: str, kind: str, data: Optional[Mapping[str, str]] = None) -> BeautifulSoup:
            if not _allowed(method, url, data):
                raise GyeryongContractError("refusing unaudited route")
            with lock:
                meta["logical_requests"] += 1
                meta["list_requests" if kind == "list" else "detail_pages"] += 1
            last_error = None
            for attempt in range(2):
                with lock:
                    meta["physical_requests"] += 1
                try:
                    response = fetch(session, method, url, timeout=timeout, data=data)
                    if int(getattr(response, "status_code", 200)) in {429, 500, 502, 503, 504} and attempt == 0:
                        with lock:
                            meta["request_retry_count"] += 1
                        continue
                    return _soup(response, url)
                except requests.RequestException as exc:
                    last_error = exc
                    if attempt == 0:
                        with lock:
                            meta["request_retry_count"] += 1
                        continue
                    raise
            raise GyeryongContractError(f"request failed: {last_error}")

        first = _parse_page(load("POST", GYERYONG_URL, "list", gyeryong_list_data(1)), 1)
        sentinel_number = first.last + 1
        required = first.last + 1 + len({1, first.last, sentinel_number})
        meta["required_list_requests"] = required
        if required > max_pages:
            meta["source_cap_reached"] = True
            raise GyeryongContractError(f"max_pages cap allows {max_pages} of {required}")
        pages = {1: first}
        for n in range(2, first.last + 1):
            pages[n] = _parse_page(load("POST", GYERYONG_URL, "list", gyeryong_list_data(n)), n, first.last)
        sentinel = _parse_page(
            load("POST", GYERYONG_URL, "list", gyeryong_list_data(sentinel_number)), sentinel_number, first.last
        )
        if _signature(sentinel) != _signature(pages[first.last]):
            raise GyeryongContractError("immediate clamp sentinel changed")
        for n in range(1, first.last + 1):
            listed.extend(dict(r) for r in pages[n].rows)
        ids = [r["source_identity"] for r in listed]
        if len(ids) != len(set(ids)):
            raise GyeryongContractError("duplicate identities across pages")
        expected_total = (first.last - 1) * GYERYONG_PAGE_SIZE + len(pages[first.last].rows)
        if len(listed) != expected_total:
            raise GyeryongContractError("full page union changed")
        current = [r for r in listed if r["event_end"] >= cutoff]
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise GyeryongContractError(f"detail cap allows {detail_limit} of {len(current)}")
        parsed = [_parse_detail(r, load("GET", r["detail_url"], "detail")) for r in current]
        originals = {1: first, first.last: pages[first.last], sentinel_number: sentinel}
        rechecks = {}
        for n, original in originals.items():
            observed = _parse_page(load("POST", GYERYONG_URL, "list", gyeryong_list_data(n)), n, first.last)
            rechecks[str(n)] = _signature(observed) == _signature(original)
            if not rechecks[str(n)]:
                raise GyeryongContractError(f"page {n}: boundary stability changed")
        output = [r for r, _ in parsed if r is not None]
        discarded = Counter()
        for _, c in parsed:
            discarded.update(c)
        if dedupe_rows:
            deduped = list(dedupe_rows(output))
        else:
            seen = set()
            deduped = []
            for r in output:
                if r["provider_course_id"] not in seen:
                    seen.add(r["provider_course_id"])
                    deduped.append(r)
        if len(deduped) != len(output):
            raise GyeryongContractError("dedupe changed complete output")
        privacy = _privacy(deduped)
        meta["pii_values_persisted"] = privacy
        if privacy:
            raise GyeryongContractError("PII allowlist violation")
        deduped.sort(key=lambda r: (r["start_date"], r["title"], r["provider_course_id"]))
        meta.update(
            {
                "pages": first.last,
                "page_counts": {n: len(p.rows) for n, p in pages.items()},
                "source_total": len(listed),
                "source_rows": len(listed),
                "clamp_sentinel_page": sentinel_number,
                "clamp_sentinel_rows": len(sentinel.rows),
                "boundary_rechecks": rechecks,
                "current_source_count": len(current),
                "expired_count": len(listed) - len(current),
                "detail_verified": len(current),
                "excluded_test_count": len(current) - len(output),
                "returned_count": len(deduped),
                "source_status_counts": dict(Counter(r["source_status"] for r in current)),
                "status_counts": dict(Counter(r["status"] for r in deduped)),
                "branch_counts": dict(Counter(r["branch"] for r in deduped)),
                "source_identity_count": len(ids),
                "source_identity_sha256": hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest(),
                "reservation_control_count": discarded["reservation_controls"],
                "attachment_fields_discarded": discarded["attachments"],
                "sensitive_detail_fields_discarded": discarded["sensitive"],
                "excluded_farm_provider": GYERYONG_EXCLUDED_FARM_PROVIDER,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, GYERYONG_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "source_rows": len(listed),
                "returned_count": 0,
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], GYERYONG_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


collect = collect_gyeryong_education

__all__ = [name for name in globals() if name.startswith("GYERYONG_")] + [
    "GyeryongContractError",
    "collect",
    "collect_gyeryong_education",
    "gyeryong_detail_url",
    "gyeryong_list_data",
    "gyeryong_source_identity",
    "is_gyeryong_target",
    "is_target",
]
