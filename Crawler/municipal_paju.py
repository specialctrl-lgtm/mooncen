"""Fail-closed audit collector for Paju's official education aggregator.

The incumbent list provider owns the native ``edcMnnstCode/edcSn`` records.
The same list deliberately embeds Paju Library records whose application
identity is ``lectureIdx``; those identities remain in a separate namespace.
The root landing-page provider is a duplicate discovery surface, while the
Youth Foundation FMCS provider is an already-operational, disjoint owner.
Only public list and detail pages are requested.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

PAJU_PROVIDER = "MUNI_LLL_PAJU_GO_KR_F639C571"
PAJU_EXCLUDED_ROOT_PROVIDER = "MUNI_LLL_PAJU_GO_KR_7EF7AB87"
PAJU_YOUTH_PROVIDER = "MUNI_PAJU_PCY_OR_KR_412053A6"
PAJU_MUNICIPALITY_CODE = "4148000000"
PAJU_MUNICIPALITY_NAME = "경기도 파주시"
PAJU_HOST = "lll.paju.go.kr"
PAJU_LIBRARY_HOST = "lib.paju.go.kr"
PAJU_LIST_PATH = "/user/lll/lecture/BD_lectureList.do"
PAJU_DETAIL_PATH = "/user/lll/lecture/BD_lectureView.do"
PAJU_URL = f"https://{PAJU_HOST}{PAJU_LIST_PATH}?eventClCode=1001"
PAJU_ROOT_URL = f"https://{PAJU_HOST}/"
PAJU_PAGE_SIZE = 10
PAJU_MAX_PAGES = 150
PAJU_MAX_DETAILS = 500
PAJU_DETAIL_WORKERS = 20
PAJU_MAX_HTML_BYTES = 4_000_000
PAJU_PARSER = (
    "paju_official_education_aggregator+native_and_library_identity_owners+"
    "all_declared_pages+empty_post_last_sentinel+stable_boundaries+"
    "all_nonterminal_details+current_future_filter+no_private_endpoints"
)

PAJU_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "native_owner": {
        "provider": PAJU_PROVIDER,
        "identity": "edcMnnstCode+edcSn",
        "decision": "retain_incumbent_list_provider",
    },
    "library_owner": {
        "surface": PAJU_LIBRARY_HOST,
        "identity": "lectureIdx",
        "decision": "separate_subowner_namespace_from_embedded_official_links",
    },
    "root_duplicate": {
        "provider": PAJU_EXCLUDED_ROOT_PROVIDER,
        "url": PAJU_ROOT_URL,
        "decision": "exclude_navigation_only_duplicate",
    },
    "youth_fmcs": {
        "provider": PAJU_YOUTH_PROVIDER,
        "identity": "FMCS course identity",
        "decision": "retain_existing_operational_disjoint_owner",
    },
}

PAJU_INSTITUTION_REGISTRY = (
    ("", "전체"),
    ("EDC_0001", "평생학습관"),
    ("EDC_0002", "정보화교육"),
    ("EDC_0003", "기타교육기관"),
    ("EDC_0004", "농업기술센터"),
    ("EDC_0005", "보건소"),
    ("EDC_0006", "도서관"),
    ("EDC_0007", "광탄면"),
    ("EDC_0008", "교하읍"),
    ("EDC_0009", "금촌1동"),
    ("EDC_0010", "금촌2동"),
    ("EDC_0011", "금촌3동"),
    ("EDC_0012", "문산읍"),
    ("EDC_0013", "법원읍"),
    ("EDC_0014", "운정1동"),
    ("EDC_0015", "운정2동"),
    ("EDC_0016", "운정3동"),
    ("EDC_0017", "월롱면"),
    ("EDC_0018", "적성면"),
    ("EDC_0019", "조리읍"),
    ("EDC_0020", "탄현면"),
    ("EDC_0021", "파주읍"),
    ("EDC_0022", "파평면"),
    ("EDT_0001", "자치"),
    ("ECT_0001", "민간교육기관"),
)
PAJU_STATUS_REGISTRY = (
    ("", "분류 선택"),
    ("1001", "모집예정"),
    ("1002", "모집중"),
    ("1003", "모집마감"),
    ("1004", "대기모집"),
    ("1005", "교육중"),
    ("1006", "교육종료"),
    ("1007", "교육폐강"),
)
PAJU_METHOD_REGISTRY = (("", "전체"), ("1", "온라인접수"), ("2", "방문접수"))
PAJU_OFFICIAL_INSTITUTIONS = frozenset({"도서관", "평생학습관", "보건소", "농업기술센터", "정보화교육"})

_STATUS = {
    "모집예정": "SCHEDULED",
    "모집중": "OPEN",
    "모집마감": "CLOSED",
    "대기모집": "OPEN",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
    "교육폐강": "CLOSED",
}
_NONTERMINAL = frozenset({"모집예정", "모집중", "모집마감", "대기모집", "교육중"})
_LIBRARY_STATUS_PREFIXES = {
    "접수예정": "SCHEDULED",
    "신청예정": "SCHEDULED",
    "대기접수": "OPEN",
    "접수중": "OPEN",
    "신청중": "OPEN",
    "접수마감": "CLOSED",
    "신청마감": "CLOSED",
}
_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[.-](\d{2})[.-](\d{2})(?!\d)")
_NATIVE_ID_RE = re.compile(r"^[A-Z]{3}_\d{4}$")
_SERIAL_RE = re.compile(r"^\d{8,16}$")
_LIB_SITE_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$")
_LIB_ID_RE = re.compile(r"^[1-9]\d{0,11}$")
_NATIVE_JS_RE = re.compile(r"jsView\('([^']+)','([^']+)'\);")
_LIB_JS_RE = re.compile(r"jsLibraryRequestForm\('([^']+)'\);")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class PajuContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Page:
    requested: int
    reported: int
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]


def _clean(v: Any) -> str:
    return _SPACE_RE.sub(" ", str(v or "").replace("\xa0", " ")).strip()


def _target_value(t: Any, k: str) -> Any:
    return t.get(k) if isinstance(t, Mapping) else getattr(t, k, None)


def _strict_target(url: Any) -> bool:
    p = urlparse(_clean(url))
    try:
        q = parse_qsl(p.query, keep_blank_values=True, strict_parsing=True)
        port = p.port
    except (ValueError, TypeError):
        return False
    return (
        p.scheme == "https"
        and (p.hostname or "").lower() == PAJU_HOST
        and port is None
        and not p.username
        and not p.password
        and p.path == PAJU_LIST_PATH
        and q == [("eventClCode", "1001")]
        and not p.fragment
    )


def is_paju_target(target: Any) -> bool:
    return _clean(_target_value(target, "provider")) == PAJU_PROVIDER and _strict_target(_target_value(target, "url"))


is_target = is_paju_target


def paju_list_url(page: int) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be positive")
    return f"https://{PAJU_HOST}{PAJU_LIST_PATH}?{urlencode((('eventClCode', '1001'), ('q_currPage', str(page)), ('q_rowPerPage', str(PAJU_PAGE_SIZE))))}"


def paju_native_detail_url(code: Any, serial: Any) -> str:
    code, serial = _clean(code), _clean(serial)
    if not _NATIVE_ID_RE.fullmatch(code) or not _SERIAL_RE.fullmatch(serial):
        raise ValueError("invalid native identity")
    return f"https://{PAJU_HOST}{PAJU_DETAIL_PATH}?{urlencode((('eventClCode', '1001'), ('edcMnnstCode', code), ('edcSn', serial)))}"


def paju_source_identity(owner: str, *parts: Any) -> str:
    if owner == "native" and len(parts) == 2:
        code, serial = map(_clean, parts)
        if _NATIVE_ID_RE.fullmatch(code) and _SERIAL_RE.fullmatch(serial):
            return f"{PAJU_PROVIDER}:native:{code}:{serial}"
    if owner == "library" and len(parts) == 1 and _LIB_ID_RE.fullmatch(_clean(parts[0])):
        return f"{PAJU_PROVIDER}:library:{_clean(parts[0])}"
    raise ValueError("invalid Paju source identity")


def paju_session_factory() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {"User-Agent": "Mozilla/5.0 Chrome/138.0", "Accept-Language": "ko-KR,ko;q=0.9", "Referer": PAJU_URL}
    )
    return s


def _default_fetcher(session: Any, method: str, url: str, *, timeout: int) -> Any:
    if method != "GET":
        raise PajuContractError("only audited GET routes are allowed")
    return session.get(url, timeout=timeout, allow_redirects=False)


def _allowed(url: str) -> bool:
    p = urlparse(url)
    try:
        q = parse_qsl(p.query, keep_blank_values=True, strict_parsing=True)
        port = p.port
    except (ValueError, TypeError):
        return False
    if p.scheme != "https" or port is not None or p.username or p.password or p.fragment:
        return False
    host = (p.hostname or "").lower()
    if host == PAJU_HOST and p.path == PAJU_LIST_PATH:
        return (
            len(q) == 3
            and q[0] == ("eventClCode", "1001")
            and q[1][0] == "q_currPage"
            and q[1][1].isdigit()
            and int(q[1][1]) >= 1
            and q[2] == ("q_rowPerPage", str(PAJU_PAGE_SIZE))
        )
    if host == PAJU_HOST and p.path == PAJU_DETAIL_PATH:
        return (
            len(q) == 3
            and q[0] == ("eventClCode", "1001")
            and q[1][0] == "edcMnnstCode"
            and bool(_NATIVE_ID_RE.fullmatch(q[1][1]))
            and q[2][0] == "edcSn"
            and bool(_SERIAL_RE.fullmatch(q[2][1]))
        )
    return (
        host == PAJU_LIBRARY_HOST
        and bool(re.fullmatch(r"/[a-z][a-z0-9]{1,15}/lectureDetail\.do", p.path))
        and len(q) == 1
        and q[0][0] == "lectureIdx"
        and bool(_LIB_ID_RE.fullmatch(q[0][1]))
    )


def _soup(response: Any, requested: str) -> BeautifulSoup:
    if int(getattr(response, "status_code", 200)) != 200 or getattr(response, "history", None):
        raise PajuContractError("HTTP/redirect changed")
    h = getattr(response, "headers", {}) or {}
    if h.get("Location") or h.get("location") or "html" not in _clean(h.get("Content-Type", "text/html")).lower():
        raise PajuContractError("redirect/non-HTML response")
    if _clean(getattr(response, "url", requested) or requested) != requested:
        raise PajuContractError("response URL changed")
    content = getattr(response, "content", None) or str(getattr(response, "text", response)).encode()
    if not content or len(content) > PAJU_MAX_HTML_BYTES:
        raise PajuContractError("empty/oversize HTML")
    return BeautifulSoup(content, "html.parser")


def _options(node: Any) -> tuple[tuple[str, str], ...]:
    return (
        tuple((_clean(o.get("value")), _clean(o.get_text(" ", strip=True))) for o in node.select("option"))
        if node
        else ()
    )


def _dates(value: Any, identity: str, field: str) -> tuple[date, date]:
    found = _DATE_RE.findall(_clean(value))
    if len(found) not in {1, 2}:
        raise PajuContractError(f"course {identity}: {field} dates changed")
    if len(found) == 1:
        found = found * 2
    a, b = (date(*map(int, x)) for x in found)
    if b < a:
        raise PajuContractError(f"course {identity}: reversed {field}")
    return a, b


def _parse_list(soup: BeautifulSoup, page: int, known_total: Optional[int] = None) -> _Page:
    forms = soup.select("form#dataForm[name='dataForm']")
    if len(forms) != 1 or _clean(forms[0].get("method")).upper() != "POST":
        raise PajuContractError(f"page {page}: list form changed")
    form = forms[0]
    for name, expected in (
        ("q_edcMnnstCode", PAJU_INSTITUTION_REGISTRY),
        ("q_rcritSttusCode", PAJU_STATUS_REGISTRY),
        ("q_edcReqstMth", PAJU_METHOD_REGISTRY),
    ):
        if _options(form.select_one(f"select[name='{name}']")) != expected:
            raise PajuContractError(f"page {page}: {name} registry changed")
    hidden = {_clean(x.get("name")): _clean(x.get("value")) for x in form.select("input[name]")}
    if (
        hidden.get("eventClCode") != "1001"
        or hidden.get("q_currPage") != str(page)
        or hidden.get("q_rowPerPage") != str(PAJU_PAGE_SIZE)
    ):
        raise PajuContractError(f"page {page}: scope/page changed")
    total_node = soup.select_one(".list-info .total")
    m = re.fullmatch(
        r"총\s*([\d,]+)\s*건,\s*페이지\s*(\d+)\s*/\s*(\d+)",
        _clean(total_node.get_text(" ", strip=True)) if total_node else "",
    )
    if not m:
        raise PajuContractError(f"page {page}: total contract changed")
    total, reported, last = (int(x.replace(",", "")) for x in m.groups())
    if known_total is not None and total != known_total:
        raise PajuContractError(f"page {page}: total drift")
    if last != math.ceil(total / PAJU_PAGE_SIZE) or reported != page:
        raise PajuContractError(f"page {page}: pagination changed")
    trs = soup.select(".table-list-edu tbody > tr")
    rows = []
    if page > last:
        if len(trs) != 1 or _clean(trs[0].get_text(" ", strip=True)) != "게시물이 없습니다.":
            raise PajuContractError("post-last empty sentinel changed")
        return _Page(page, reported, total, last, ())
    for tr in trs:
        cells = tr.select(":scope > td")
        if len(cells) != 6:
            raise PajuContractError(f"page {page}: row shape changed")
        institution = _clean(cells[0].get_text(" ", strip=True))
        link = cells[1].select_one("a[onclick]")
        if institution not in PAJU_OFFICIAL_INSTITUTIONS or link is None:
            raise PajuContractError(f"page {page}: institution/control changed")
        title = _clean(link.get_text(" ", strip=True))
        onclick = _clean(link.get("onclick"))
        owner = ""
        code = ""
        serial = ""
        site = ""
        library_id = ""
        native = _NATIVE_JS_RE.fullmatch(onclick)
        library = _LIB_JS_RE.fullmatch(onclick)
        if native:
            owner = "native"
            code, serial = native.groups()
            detail = paju_native_detail_url(code, serial)
            source_id = paju_source_identity(owner, code, serial)
        elif library:
            p = urlparse(library.group(1))
            q = parse_qsl(p.query, keep_blank_values=True, strict_parsing=True)
            parts = p.path.strip("/").split("/")
            if (
                p.scheme != "https"
                or (p.hostname or "").lower() != PAJU_LIBRARY_HOST
                or len(parts) != 2
                or parts[1] != "lectureDetail.do"
                or not _LIB_SITE_RE.fullmatch(parts[0])
                or len(q) != 1
                or q[0][0] != "lectureIdx"
                or not _LIB_ID_RE.fullmatch(q[0][1])
            ):
                raise PajuContractError(f"page {page}: unsafe library identity")
            owner = "library"
            site = parts[0]
            library_id = q[0][1]
            detail = f"https://{PAJU_LIBRARY_HOST}/{site}/lectureDetail.do?lectureIdx={library_id}"
            source_id = paju_source_identity(owner, library_id)
        else:
            raise PajuContractError(f"page {page}: unknown identity owner")
        status_text = _clean(cells[5].get_text(" ", strip=True))
        status = next((x for x in _STATUS if status_text.startswith(x)), "")
        if not title or not status:
            raise PajuContractError(f"course {source_id}: title/status changed")
        apply_start, apply_end = _dates(cells[4].get_text(" ", strip=True), source_id, "list application")
        rows.append(
            {
                "owner": owner,
                "code": code,
                "serial": serial,
                "site": site,
                "library_id": library_id,
                "source_identity": source_id,
                "detail_url": detail,
                "title": title,
                "institution": institution,
                "source_status": status,
                "status": _STATUS[status],
                "apply_start": apply_start,
                "apply_end": apply_end,
                "fee": _clean(cells[2].select_one(".text-red").get_text(" ", strip=True))
                if cells[2].select_one(".text-red")
                else "",
                "method": _clean(cells[2].select_one(".text-label").get_text(" ", strip=True))
                if cells[2].select_one(".text-label")
                else "",
            }
        )
    expected = min(PAJU_PAGE_SIZE, total - (page - 1) * PAJU_PAGE_SIZE)
    if len(rows) != expected:
        raise PajuContractError(f"page {page}: expected {expected} rows, got {len(rows)}")
    return _Page(page, reported, total, last, tuple(rows))


def _page_sig(p: _Page) -> tuple[Any, ...]:
    return (
        p.reported,
        p.total,
        p.last,
        tuple((r["source_identity"], r["title"], r["source_status"], r["apply_end"]) for r in p.rows),
    )


def _table_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs = {}
    for tr in soup.select("table tr"):
        cells = tr.select(":scope > th,:scope > td")
        for i, node in enumerate(cells[:-1]):
            if node.name == "th" and cells[i + 1].name == "td":
                key = _clean(node.get_text(" ", strip=True))
                if key and key not in pairs:
                    pairs[key] = _clean(cells[i + 1].get_text(" ", strip=True))
    return pairs


def _library_detail_state(detail_title: Any) -> tuple[str, str]:
    detail = _clean(detail_title)
    prefix = next((value for value in _LIBRARY_STATUS_PREFIXES if detail.startswith(value)), "")
    return prefix, _LIBRARY_STATUS_PREFIXES.get(prefix, "")


def _library_application_extension_allowed(
    list_start: date,
    list_end: date,
    detail_start: date,
    detail_end: date,
    list_source_status: Any,
    detail_status: Any,
) -> bool:
    return (
        detail_status == "OPEN"
        and _STATUS.get(_clean(list_source_status)) in {"SCHEDULED", "CLOSED"}
        and detail_start == list_start
        and detail_end > list_end
    )


def _library_title_matches(list_title: Any, detail_title: Any, source_status: Any) -> bool:
    listed = _clean(list_title)
    detail = _clean(detail_title)
    status = _clean(source_status)
    prefix, _normalized_status = _library_detail_state(detail)
    if prefix:
        detail = _clean(detail[len(prefix) :])
    elif status and detail.startswith(status):
        detail = _clean(detail[len(status) :])
    if listed == detail or listed in detail:
        return True

    def without_scope(value: str) -> str:
        value = re.sub(r"^\[[^\]\r\n]{1,80}\]\s*", "", value, count=1)
        value = re.sub(
            r"^(?:[★☆※!]+\s*)?(?:\[\s*)?추가\s*(?:모집|접수)"
            r"(?:\s*\])?(?:\s*[★☆※!]+)?\s*",
            "",
            value,
            count=1,
        )
        return _clean(value)

    listed_core = without_scope(listed)
    detail_core = without_scope(detail)
    return len(listed_core) >= 4 and (detail_core == listed_core or detail_core.startswith(listed_core + " "))


def _parse_detail(row: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], Counter[str]]:
    identity = str(row["source_identity"])
    pairs = _table_pairs(soup)
    resolved_status = str(row["status"])
    detail_source_status = ""
    if row["owner"] == "native":
        required = {"교육분류", "모집상태", "접수방법", "모집기간", "교육기간", "교육시간", "교육장소", "모집인원"}
        title_node = soup.select_one("h1.article-subject")
        if not required.issubset(pairs) or title_node is None:
            raise PajuContractError(f"course {identity}: native detail changed")
        detail_title = _clean(title_node.get_text(" ", strip=True))
        title = str(row["title"])
        if detail_title != title and not detail_title.startswith(title + " - "):
            raise PajuContractError(f"course {identity}: detail title drift")
        period = pairs["교육기간"]
        apply_period = pairs["모집기간"]
        branch = pairs["교육장소"]
        schedule = pairs["교육시간"]
        if not pairs["모집상태"].startswith(str(row["source_status"])):
            raise PajuContractError(f"course {identity}: detail status drift")
        controls = soup.select("button[onclick='jsRequestForm();']")
        hidden = {_clean(x.get("name")): _clean(x.get("value")) for x in soup.select("form#dataForm input[name]")}
        if hidden.get("edcMnnstCode") != row["code"] or hidden.get("edcSn") != row["serial"]:
            raise PajuContractError(f"course {identity}: application identity drift")
        fee = _clean(pairs.get("교육비 금액") or pairs.get("교육비 여부"))
        capacity = _clean(pairs["모집인원"])
        method = _clean(pairs["접수방법"])
    else:
        required = {"프로그램명", "접수기간", "수강기간", "시간", "장소", "접수방법"}
        if not required.issubset(pairs):
            raise PajuContractError(f"course {identity}: library detail changed")
        detail_title = pairs["프로그램명"]
        title = str(row["title"])
        if not _library_title_matches(title, detail_title, row["source_status"]):
            raise PajuContractError(f"course {identity}: detail title drift")
        detail_source_status, resolved_status = _library_detail_state(detail_title)
        if not detail_source_status:
            resolved_status = str(row["status"])
        period = pairs["수강기간"]
        apply_period = pairs["접수기간"]
        branch = pairs["장소"]
        schedule = pairs["시간"]
        fee = _clean(pairs.get("재료비"))
        capacity = _clean(pairs.get("신청/정원") or pairs.get("정원"))
        method = _clean(pairs["접수방법"])
        controls = []
    if title != row["title"]:
        raise PajuContractError(f"course {identity}: detail title drift")
    event_start, event_end = _dates(period, identity, "education")
    apply_start, apply_end = _dates(apply_period, identity, "application")
    application_period_extended = False
    if (apply_start, apply_end) != (row["apply_start"], row["apply_end"]):
        application_period_extended = row["owner"] == "library" and _library_application_extension_allowed(
            row["apply_start"],
            row["apply_end"],
            apply_start,
            apply_end,
            row["source_status"],
            resolved_status,
        )
        if not application_period_extended:
            raise PajuContractError(f"course {identity}: application period drift")
    if not branch or len(branch) > 160 or _PHONE_RE.search(branch) or _EMAIL_RE.search(branch):
        raise PajuContractError(f"course {identity}: unaudited official branch")
    attachments = []
    for a in soup.select("a[href]"):
        href = _clean(a.get("href"))
        low = href.lower()
        if "download" in low or "filedown" in low:
            attachments.append(href)
    for href in attachments:
        p = urlparse(urljoin(str(row["detail_url"]), href))
        if p.scheme != "https" or (p.hostname or "").lower() not in {PAJU_HOST, PAJU_LIBRARY_HOST}:
            raise PajuContractError(f"course {identity}: unsafe attachment control")
    out = {
        "provider": PAJU_PROVIDER,
        "municipality_code": PAJU_MUNICIPALITY_CODE,
        "municipality_name": PAJU_MUNICIPALITY_NAME,
        "provider_course_id": identity,
        "source_course_id": f"{row['code']}:{row['serial']}" if row["owner"] == "native" else row["library_id"],
        "identity_owner": row["owner"],
        "title": title,
        "status": resolved_status,
        "source_status": row["source_status"],
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "branch": branch,
        "venue": branch,
        "institution": row["institution"],
        "schedule": schedule,
        "fee": fee,
        "capacity_text": capacity,
        "application_method": method,
        "source_url": row["detail_url"],
        "application_url": "",
        "raw_fields": {
            "identity_owner": row["owner"],
            "list_source_status": row["source_status"],
            "detail_source_status": detail_source_status,
            "application_period_extended_from_list": application_period_extended,
        },
    }
    return out, Counter(application_controls=len(controls), attachments=len(attachments), sensitive=3)


def _base_meta(cutoff: date) -> dict[str, Any]:
    return {
        "provider": PAJU_PROVIDER,
        "municipality_code": PAJU_MUNICIPALITY_CODE,
        "audit_date": cutoff.isoformat(),
        "logical_requests": 0,
        "physical_requests": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "request_retry_count": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "pii_values_persisted": 0,
        "source_cap_reached": False,
        "snapshot_complete": False,
    }


def _privacy(rows: Iterable[Mapping[str, Any]]) -> int:
    forbidden = {"phone", "email", "contact", "manager", "instructor", "attachments", "description", "source_html"}
    return sum(
        len(set(r) & forbidden) + len(_PHONE_RE.findall(repr(r))) + len(_EMAIL_RE.findall(repr(r))) for r in rows
    )


def collect_paju_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = PAJU_MAX_PAGES,
    detail_limit: int = PAJU_MAX_DETAILS,
    detail_workers: int = PAJU_DETAIL_WORKERS,
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
        return [], PAJU_PARSER, meta
    meta = _base_meta(cutoff)
    listed = []
    session = None
    if not is_paju_target(target):
        meta["configured_collection_error"] = "target does not match Paju incumbent owner"
        return [], PAJU_PARSER, meta
    try:
        timeout, max_pages, detail_limit, detail_workers = map(int, (timeout, max_pages, detail_limit, detail_workers))
        if timeout < 1 or max_pages < 1 or detail_limit < 0 or not 1 <= detail_workers <= 24:
            raise ValueError
    except Exception:
        meta["configured_collection_error"] = "invalid limits"
        return [], PAJU_PARSER, meta
    try:
        session = (session_factory or paju_session_factory)()
        fetch = fetcher or _default_fetcher
        lock = threading.Lock()

        def load(url: str, kind: str) -> BeautifulSoup:
            if not _allowed(url):
                raise PajuContractError("refusing unaudited route")
            with lock:
                meta["logical_requests"] += 1
                meta["list_requests" if kind == "list" else "detail_pages"] += 1
            for attempt in range(2):
                with lock:
                    meta["physical_requests"] += 1
                try:
                    response = fetch(session, "GET", url, timeout=timeout)
                    if int(getattr(response, "status_code", 200)) in {429, 500, 502, 503, 504} and attempt == 0:
                        with lock:
                            meta["request_retry_count"] += 1
                        continue
                    return _soup(response, url)
                except requests.RequestException:
                    if attempt == 0:
                        with lock:
                            meta["request_retry_count"] += 1
                        continue
                    raise
            raise PajuContractError("request retries exhausted")

        first = _parse_list(load(paju_list_url(1), "list"), 1)
        sentinel_number = first.last + 1
        required = first.last + 1 + len({1, first.last, sentinel_number})
        meta["required_list_requests"] = required
        if required > max_pages:
            meta["source_cap_reached"] = True
            raise PajuContractError(f"max_pages cap allows {max_pages} of {required}")
        pages = {1: first}

        def page_one(n: int) -> tuple[int, _Page]:
            return n, _parse_list(load(paju_list_url(n), "list"), n, first.total)

        if first.last > 1:
            with ThreadPoolExecutor(max_workers=min(detail_workers, first.last - 1)) as pool:
                for n, p in pool.map(page_one, range(2, first.last + 1)):
                    pages[n] = p
        sentinel = _parse_list(load(paju_list_url(sentinel_number), "list"), sentinel_number, first.total)
        for n in range(1, first.last + 1):
            listed.extend(dict(r) for r in pages[n].rows)
        ids = [r["source_identity"] for r in listed]
        if len(listed) != first.total or len(ids) != len(set(ids)):
            raise PajuContractError("full identity union changed")
        candidates = [r for r in listed if r["source_status"] in _NONTERMINAL]
        if len(candidates) > detail_limit:
            meta["source_cap_reached"] = True
            raise PajuContractError(f"detail cap allows {detail_limit} of {len(candidates)}")

        def detail_one(r: Mapping[str, Any]):
            return _parse_detail(r, load(str(r["detail_url"]), "detail"))

        with ThreadPoolExecutor(max_workers=min(detail_workers, max(1, len(candidates)))) as pool:
            parsed = list(pool.map(detail_one, candidates))
        current = [(r, c) for r, c in parsed if date.fromisoformat(r["end_date"]) >= cutoff]
        originals = {1: first, first.last: pages[first.last], sentinel_number: sentinel}
        rechecks = {}
        for n, original in originals.items():
            observed = _parse_list(load(paju_list_url(n), "list"), n, first.total)
            rechecks[str(n)] = _page_sig(observed) == _page_sig(original)
            if not rechecks[str(n)]:
                raise PajuContractError(f"page {n}: boundary stability changed")
        output = [r for r, _ in current]
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
            raise PajuContractError("dedupe changed complete output")
        native_ids = {r["provider_course_id"] for r in deduped if r["identity_owner"] == "native"}
        library_ids = {r["provider_course_id"] for r in deduped if r["identity_owner"] == "library"}
        if native_ids & library_ids or any(PAJU_YOUTH_PROVIDER in x for x in ids):
            raise PajuContractError("owner identity overlap")
        privacy = _privacy(deduped)
        meta["pii_values_persisted"] = privacy
        if privacy:
            raise PajuContractError("PII allowlist violation")
        deduped.sort(key=lambda r: (r["start_date"], r["title"], r["provider_course_id"]))
        meta.update(
            {
                "pages": first.last,
                "source_total": first.total,
                "source_rows": len(listed),
                "page_counts": {n: len(p.rows) for n, p in pages.items()},
                "empty_sentinel_page": sentinel_number,
                "empty_sentinel_rows": 0,
                "boundary_rechecks": rechecks,
                "nonterminal_detail_candidates": len(candidates),
                "detail_verified": len(parsed),
                "current_source_count": len(current),
                "returned_count": len(deduped),
                "source_status_counts": dict(Counter(r["source_status"] for r in listed)),
                "current_status_counts": dict(Counter(r["source_status"] for r in deduped)),
                "status_counts": dict(Counter(r["status"] for r in deduped)),
                "owner_counts": dict(Counter(r["identity_owner"] for r in deduped)),
                "institution_counts": dict(Counter(r["institution"] for r in deduped)),
                "branch_counts": dict(Counter(r["branch"] for r in deduped)),
                "source_identity_count": len(ids),
                "source_identity_sha256": hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest(),
                "application_control_count": discarded["application_controls"],
                "attachment_fields_discarded": discarded["attachments"],
                "sensitive_detail_fields_discarded": discarded["sensitive"],
                "excluded_root_provider": PAJU_EXCLUDED_ROOT_PROVIDER,
                "disjoint_youth_provider": PAJU_YOUTH_PROVIDER,
                "owner_identity_disjoint": True,
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return deduped, PAJU_PARSER, meta
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
        return [], PAJU_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


collect = collect_paju_education
__all__ = [name for name in globals() if name.startswith("PAJU_")] + [
    "PajuContractError",
    "collect",
    "collect_paju_education",
    "is_paju_target",
    "is_target",
    "paju_list_url",
    "paju_native_detail_url",
    "paju_source_identity",
]
