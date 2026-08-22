"""Fail-closed collector for Gapyeong County Library education programmes."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
from bs4 import BeautifulSoup


GAPYEONG_PROVIDER = "MUNI_WWW_GAPLIB_GO_KR_38AFB1BF"
GAPYEONG_CANONICAL_CANDIDATE_ID = "MUNI_IR_9B2CE41807D7"
GAPYEONG_MUNICIPALITY_CODE = "4182000000"
GAPYEONG_MUNICIPALITY_NAME = "경기도 가평군"
GAPYEONG_HOST = "www.gaplib.go.kr"
GAPYEONG_LIST_PATH = "/intro/menu/10058/program/30014/lectureList.do"
GAPYEONG_DETAIL_PATH = "/intro/menu/10058/program/30014/lectureDetail.do"
GAPYEONG_APPLY_PATH = "/intro/menu/10058/program/30014/lectureUserApply.do"
GAPYEONG_CANONICAL_URL = f"https://{GAPYEONG_HOST}{GAPYEONG_LIST_PATH}"
GAPYEONG_PAGE_SIZE = 10
GAPYEONG_MAX_WORKERS = 6
GAPYEONG_MAX_HTML_BYTES = 3_000_000
GAPYEONG_PARSER = (
    "gapyeong_library_complete_ledger+five_facility_partitions+declared_sequence_totals+"
    "empty_post_last+stable_first_last+partition_identity_reconciliation+current_details+"
    "identity_bound_application_controls+exact_facility_branches+pii_allowlist"
)

GAPYEONG_MANAGE_CODES: Mapping[str, str] = {
    "CO": "공통",
    "MA": "한석봉",
    "MC": "설악",
    "MD": "청평",
    "MB": "조종",
}
GAPYEONG_BRANCHES: Mapping[str, str] = {
    "MA": "한석봉도서관",
    "MC": "설악도서관",
    "MD": "청평도서관",
    "MB": "조종도서관",
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GapyeongContractError(ValueError):
    """Raised when the audited official-source contract changes."""


@dataclass(frozen=True)
class _Page:
    requested: int
    observed: int
    scope: str
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DETAIL_ID = re.compile(r"fnDetail\('([1-9]\d*)'\)")
_PAGE_ID = re.compile(r"fnList\((\d+)\)")
_DATE = re.compile(r"20\d{2}[.-]\d{1,2}[.-]\d{1,2}")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS = {
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "대기중": "OPEN",
    "접수마감": "CLOSED",
    "행사종료": "CLOSED",
}
_OPEN_SOURCE_STATUSES = frozenset({"접수중", "대기중"})
_SAFE_RAW = frozenset(
    {
        "identity",
        "source_sequence",
        "source_scope",
        "source_list_page",
        "source_status",
        "source_library",
        "source_apply_period",
        "source_education_period",
        "source_schedule",
        "detail_verified",
        "application_control_present",
        "service_family",
    }
)
_FORBIDDEN = frozenset(
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
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_gapyeong_education_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != GAPYEONG_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == GAPYEONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GAPYEONG_LIST_PATH
        and not query
        and not parsed.fragment
    )


is_target = is_gapyeong_education_target


def _session() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return current


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any, requested_url: str) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise GapyeongContractError(f"unexpected HTTP status {status}")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location"):
        raise GapyeongContractError("redirect response is not accepted")
    final_url = str(getattr(value, "url", requested_url) or requested_url)
    final = urlparse(final_url)
    if (
        final.scheme != "https"
        or (final.hostname or "").lower() != GAPYEONG_HOST
        or final.username is not None
        or final.password is not None
        or final.port is not None
    ):
        raise GapyeongContractError("response left the official host")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if not content:
        raise GapyeongContractError("empty official response")
    if len(content) > GAPYEONG_MAX_HTML_BYTES:
        raise GapyeongContractError("HTML size cap exceeded")
    return BeautifulSoup(content, "lxml")


def _soup(
    url: str,
    timeout: int,
    factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != GAPYEONG_HOST
        or parsed.path not in {GAPYEONG_LIST_PATH, GAPYEONG_DETAIL_PATH}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
    ):
        raise GapyeongContractError("non-canonical request refused")
    last_error: Optional[Exception] = None
    for _attempt in range(2):
        current = factory()
        try:
            return _coerce_soup(fetcher(current, url, timeout), url)
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
        finally:
            close = getattr(current, "close", None)
            if callable(close):
                close()
    assert last_error is not None
    raise last_error


def gapyeong_list_url(page: int = 1, manage_code: str = "") -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    if manage_code and manage_code not in GAPYEONG_MANAGE_CODES:
        raise ValueError("unknown Gapyeong library scope")
    params: list[tuple[str, Any]] = [("currentPageNo", page)]
    if manage_code:
        params.append(("manageCd", manage_code))
    return f"{GAPYEONG_CANONICAL_URL}?{urlencode(params)}"


def gapyeong_detail_url(identity: str) -> str:
    if not _IDENTITY.fullmatch(str(identity)):
        raise ValueError("invalid lecture identity")
    return (
        f"https://{GAPYEONG_HOST}{GAPYEONG_DETAIL_PATH}?"
        + urlencode({"lectureIdx": identity})
    )


def gapyeong_application_url(identity: str) -> str:
    if not _IDENTITY.fullmatch(str(identity)):
        raise ValueError("invalid lecture identity")
    return (
        f"https://{GAPYEONG_HOST}{GAPYEONG_APPLY_PATH}?"
        + urlencode({"lectureIdx": identity})
    )


def _date_range(value: str, identity: str, field: str) -> tuple[date, date]:
    values = _DATE.findall(value)
    if len(values) < 2:
        raise GapyeongContractError(f"lecture {identity}: {field} missing")
    parsed = [date.fromisoformat(item.replace(".", "-")) for item in values]
    start, end = parsed[0], parsed[1]
    if end < start:
        raise GapyeongContractError(f"lecture {identity}: reversed {field}")
    return start, end


def _source_date_range(
    value: str, identity: str, field: str
) -> tuple[date, date, bool]:
    values = _DATE.findall(value)
    if len(values) < 2:
        raise GapyeongContractError(f"lecture {identity}: {field} missing")
    first, second = (
        date.fromisoformat(item.replace(".", "-")) for item in values[:2]
    )
    return min(first, second), max(first, second), second < first


def _status(value: str) -> str:
    source = _clean(value)
    if source not in _STATUS:
        raise GapyeongContractError(f"unknown lecture status: {source}")
    return _STATUS[source]


def _list_fields(cell: Any, identity: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in cell.select("li"):
        label = item.find("span")
        key = _clean(label.get_text(" ", strip=True) if label else "")
        if not key:
            raise GapyeongContractError(f"lecture {identity}: list field label missing")
        if key in result:
            raise GapyeongContractError(f"lecture {identity}: duplicate list field {key}")
        if label:
            label.extract()
        result[key] = _clean(item.get_text(" ", strip=True))
    required = {"접수기간", "수강기간", "교육장소"}
    if set(result) != required:
        raise GapyeongContractError(f"lecture {identity}: list field contract changed")
    return result


def _selected_scope(soup: BeautifulSoup) -> str:
    selected = [
        _clean(option.get("value"))
        for option in soup.select("select[name='manageCd'] option[selected]")
        if _clean(option.get("value"))
    ]
    if len(selected) > 1:
        raise GapyeongContractError("multiple library scopes selected")
    return selected[0] if selected else ""


def _derive_last(soup: BeautifulSoup, total: int) -> int:
    expected = max(1, (total + GAPYEONG_PAGE_SIZE - 1) // GAPYEONG_PAGE_SIZE)
    advertised = [
        int(match.group(1))
        for anchor in soup.select(".paging a[onclick]")
        if (match := _PAGE_ID.search(_clean(anchor.get("onclick"))))
    ]
    if expected > 1 and (not advertised or max(advertised) != expected):
        raise GapyeongContractError("advertised last page drift")
    return expected


def _parse_page(
    soup: BeautifulSoup,
    requested: int,
    scope: str,
    *,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> _Page:
    table = soup.select_one("table.board-list")
    page_node = soup.select_one("#paramForm input[name='currentPageNo']")
    if table is None or page_node is None:
        raise GapyeongContractError(f"scope {scope or 'ALL'} page {requested}: ledger missing")
    try:
        observed = int(_clean(page_node.get("value")))
    except ValueError as exc:
        raise GapyeongContractError("observed page is invalid") from exc
    if observed != requested:
        raise GapyeongContractError(
            f"scope {scope or 'ALL'} page {requested}: observed page {observed}"
        )
    selected = _selected_scope(soup)
    if selected != scope:
        raise GapyeongContractError(
            f"scope {scope or 'ALL'} page {requested}: selected scope drift"
        )

    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 6:
            raise GapyeongContractError(
                f"scope {scope or 'ALL'} page {requested}: lecture row shape changed"
            )
        anchor = cells[2].select_one("p.lecture_tit a[onclick]")
        detail_matches = _DETAIL_ID.findall(
            _clean(anchor.get("onclick")) if anchor else ""
        )
        if len(detail_matches) != 1:
            raise GapyeongContractError(f"scope {scope or 'ALL'} page {requested}: identity missing")
        identity = detail_matches[0]
        title = _clean(anchor.get_text(" ", strip=True))
        try:
            sequence = int(_clean(cells[0].get_text(" ", strip=True)))
        except ValueError as exc:
            raise GapyeongContractError(f"lecture {identity}: sequence invalid") from exc
        library = _clean(cells[1].get_text(" ", strip=True))
        matching_codes = [code for code, name in GAPYEONG_MANAGE_CODES.items() if name == library]
        if len(matching_codes) != 1 or (scope and matching_codes[0] != scope):
            raise GapyeongContractError(f"lecture {identity}: library scope changed")
        source_scope = matching_codes[0]
        fields = _list_fields(cells[2], identity)
        start, end, education_period_anomaly = _source_date_range(
            fields["수강기간"], identity, "education period"
        )
        apply_start, apply_end, application_period_anomaly = _source_date_range(
            fields["접수기간"], identity, "application period"
        )
        source_status = _clean(cells[5].get_text(" ", strip=True))
        status = _status(source_status)
        target = _clean(cells[3].get_text(" ", strip=True))
        capacity = _clean(cells[4].get_text(" ", strip=True))
        # The official ledger contains a small number of archived events whose
        # audience cell was intentionally left blank.  Identity, dates,
        # capacity and venue remain mandatory; a current blank audience is
        # accepted only when its detail page independently confirms it.
        if not title or not capacity or not fields["교육장소"]:
            raise GapyeongContractError(f"lecture {identity}: required list value missing")
        rows.append(
            {
                "identity": identity,
                "sequence": sequence,
                "scope": source_scope,
                "library": library,
                "title": title,
                "apply_text": fields["접수기간"],
                "apply_start": apply_start,
                "apply_end": apply_end,
                "application_period_anomaly": application_period_anomaly,
                "period_text": fields["수강기간"],
                "start": start,
                "end": end,
                "education_period_anomaly": education_period_anomaly,
                "venue": fields["교육장소"],
                "target": target,
                "capacity_text": capacity,
                "source_status": source_status,
                "status": status,
                "list_page": requested,
            }
        )

    if expected_total is None:
        if not rows:
            raise GapyeongContractError(f"scope {scope or 'ALL'}: first page is unexpectedly empty")
        total = rows[0]["sequence"]
        last = _derive_last(soup, total)
    else:
        total = int(expected_total)
        last = int(expected_last or 0)
        if last != max(1, (total + GAPYEONG_PAGE_SIZE - 1) // GAPYEONG_PAGE_SIZE):
            raise GapyeongContractError("provided catalogue boundary is inconsistent")
    if requested <= last:
        high = total - ((requested - 1) * GAPYEONG_PAGE_SIZE)
        expected_sequences = list(
            range(high, max(0, high - GAPYEONG_PAGE_SIZE), -1)
        )
        if [row["sequence"] for row in rows] != expected_sequences:
            raise GapyeongContractError(
                f"scope {scope or 'ALL'} page {requested}: declared sequence drift"
            )
    elif requested == last + 1:
        if rows:
            raise GapyeongContractError(
                f"scope {scope or 'ALL'} page {requested}: post-last page is not empty"
            )
    else:
        raise GapyeongContractError("only the exact post-last sentinel may be requested")
    identities = [row["identity"] for row in rows]
    if len(identities) != len(set(identities)):
        raise GapyeongContractError(
            f"scope {scope or 'ALL'} page {requested}: duplicate identity"
        )
    return _Page(requested, observed, scope, total, last, tuple(rows))


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple(
            (
                row["sequence"],
                row["identity"],
                row["scope"],
                row["title"],
                row["source_status"],
                row["end"],
            )
            for row in page.rows
        ),
    )


def _course_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        row[key]
        for key in (
            "identity",
            "scope",
            "library",
            "title",
            "apply_text",
            "period_text",
            "venue",
            "target",
            "capacity_text",
            "source_status",
        )
    )


def _detail_rows(table: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for tr in table.select("tr"):
        th = tr.find("th")
        td = tr.find("td")
        if th is None or td is None:
            continue
        key = _clean(th.get_text(" ", strip=True))
        value = _clean(td.get_text(" ", strip=True))
        if key in result and result[key] != value:
            raise GapyeongContractError(f"conflicting detail field {key}")
        result[key] = value
    return result


def _capacity(value: str, identity: str) -> tuple[int, int, int, int]:
    pairs = re.findall(r"(\d+)\s*/\s*(\d+)", value)
    if len(pairs) != 2:
        raise GapyeongContractError(f"lecture {identity}: capacity contract changed")
    current, total = (int(value) for value in pairs[0])
    waiting, waiting_total = (int(value) for value in pairs[1])
    if current > total or waiting > waiting_total:
        raise GapyeongContractError(f"lecture {identity}: capacity exceeds limit")
    return current, total, waiting, waiting_total


def _resolve_branch(scope: str, institution: str, venue: str) -> tuple[str, str]:
    institution = _clean(institution)
    venue = _clean(venue)
    if scope in GAPYEONG_BRANCHES:
        expected = GAPYEONG_BRANCHES[scope]
        if institution != expected:
            raise GapyeongContractError(f"facility branch drift for scope {scope}")
        return expected, f"GAPLIB_{scope}"
    if scope != "CO":
        raise GapyeongContractError("unknown facility scope")
    for code, branch in GAPYEONG_BRANCHES.items():
        if institution == branch or branch in venue:
            return branch, f"GAPLIB_{code}"
    if institution not in {"공통", "가평군도서관", "가평군립도서관"}:
        raise GapyeongContractError("common programme facility branch drift")
    return "가평군도서관 공통", "GAPLIB_CO"


def _application_control(soup: BeautifulSoup, identity: str, source_status: str) -> bool:
    forms = soup.select("#paramForm")
    identities = [
        _clean(node.get("value"))
        for node in soup.select("#paramForm input[name='lectureIdx']")
    ]
    if len(forms) != 1 or identities != [identity]:
        raise GapyeongContractError(f"lecture {identity}: detail identity drift")
    buttons = soup.select("#applyBtn")
    scripts = " ".join(script.get_text(" ", strip=True) for script in soup.find_all("script"))
    bound_script = GAPYEONG_APPLY_PATH in scripts
    expected = source_status in _OPEN_SOURCE_STATUSES
    if expected and (len(buttons) != 1 or not bound_script):
        raise GapyeongContractError(
            f"lecture {identity}: active status lacks identity-bound application control"
        )
    if not expected and buttons:
        raise GapyeongContractError(
            f"lecture {identity}: inactive status exposes an application control"
        )
    return expected


def _detail(listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date) -> dict[str, Any]:
    identity = str(listed["identity"])
    table = soup.select_one("table.board-view")
    if table is None:
        raise GapyeongContractError(f"lecture {identity}: detail table missing")
    fields = _detail_rows(table)
    required = {
        "프로그램명",
        "기관",
        "접수기간",
        "신청현황",
        "수강기간 / 시간",
        "대상 / 제한사항",
        "교육장소",
    }
    if not required <= set(fields):
        raise GapyeongContractError(f"lecture {identity}: structured detail fields missing")
    title_cell = next(
        (
            tr.find("td")
            for tr in table.select("tr")
            if tr.find("th") is not None
            and _clean(tr.find("th").get_text(" ", strip=True)) == "프로그램명"
        ),
        None,
    )
    title_spans = title_cell.find_all("span", recursive=False) if title_cell else []
    if len(title_spans) != 2:
        raise GapyeongContractError(f"lecture {identity}: programme title/status shape changed")
    detail_status = _clean(title_spans[0].get_text(" ", strip=True))
    detail_title = _clean(title_spans[1].get_text(" ", strip=True))
    if detail_title != listed["title"] or detail_status != listed["source_status"]:
        raise GapyeongContractError(f"lecture {identity}: list/detail identity or status drift")
    start, end = _date_range(fields["수강기간 / 시간"], identity, "detail education period")
    apply_start, apply_end = _date_range(fields["접수기간"], identity, "detail application period")
    if (start, end) != (listed["start"], listed["end"]) or end < cutoff:
        raise GapyeongContractError(f"lecture {identity}: detail education period drift")
    if (apply_start, apply_end) != (listed["apply_start"], listed["apply_end"]):
        raise GapyeongContractError(f"lecture {identity}: detail application period drift")
    venue = _clean(fields["교육장소"])
    if venue != listed["venue"]:
        raise GapyeongContractError(f"lecture {identity}: list/detail venue drift")
    detail_target = _clean(fields["대상 / 제한사항"])
    if not detail_target.startswith(str(listed["target"])):
        raise GapyeongContractError(f"lecture {identity}: list/detail target drift")
    list_capacity = _capacity(str(listed["capacity_text"]), identity)
    detail_capacity = _capacity(fields["신청현황"], identity)
    if list_capacity != detail_capacity:
        raise GapyeongContractError(f"lecture {identity}: list/detail capacity drift")
    branch, branch_code = _resolve_branch(
        str(listed["scope"]), fields["기관"], venue
    )
    has_control = _application_control(soup, identity, str(listed["source_status"]))
    application_url = gapyeong_application_url(identity) if has_control else ""
    current, capacity, waiting, waiting_capacity = list_capacity
    row = {
        "provider": GAPYEONG_PROVIDER,
        "provider_course_id": f"{GAPYEONG_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": listed["title"],
        "description": listed["title"],
        "branch": branch,
        "branch_code": branch_code,
        "branch_url": GAPYEONG_CANONICAL_URL,
        "preserve_branch": True,
        "category": "도서관 문화행사",
        "program_type": "교육",
        "raw_url": gapyeong_detail_url(identity),
        "application_url": application_url,
        "application_type": (
            "ONLINE_RESERVATION_LOGIN_REQUIRED" if has_control else "INFO_ONLY"
        ),
        "application_method": "온라인" if has_control else "안내",
        "application_methods": ["온라인"] if has_control else [],
        "reservation_available": has_control,
        "status": listed["status"],
        "fee": "",
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": fields["접수기간"],
        "schedule_raw": fields["수강기간 / 시간"],
        "capacity": f"{capacity}명",
        "capacity_current": current,
        "capacity_total": capacity,
        "waiting_current": waiting,
        "waiting_capacity": waiting_capacity,
        "target": listed["target"],
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GAPYEONG_PARSER,
        "municipality_code": GAPYEONG_MUNICIPALITY_CODE,
        "municipality_full_name": GAPYEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_sequence": listed["sequence"],
            "source_scope": listed["scope"],
            "source_list_page": listed["list_page"],
            "source_status": listed["source_status"],
            "source_library": listed["library"],
            "source_apply_period": fields["접수기간"],
            "source_education_period": listed["period_text"],
            "source_schedule": fields["수강기간 / 시간"],
            "detail_verified": True,
            "application_control_present": has_control,
            "service_family": "education",
        },
    }
    return row


def _privacy(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN:
        errors.append("forbidden detail/PII key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW:
        errors.append("raw field allowlist exceeded")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "branch_url"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("contact data persisted")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = str(row["provider_course_id"])
        if identity not in seen:
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
    return date.fromisoformat(_clean(value))


def collect_gapyeong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 200,
    detail_limit: int = 300,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GAPYEONG_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    scopes = [""] + list(GAPYEONG_MANAGE_CODES)
    meta: dict[str, Any] = {
        "municipality_code": GAPYEONG_MUNICIPALITY_CODE,
        "owner_provider": GAPYEONG_PROVIDER,
        "canonical_url": GAPYEONG_CANONICAL_URL,
        "parser": GAPYEONG_PARSER,
        "classification_scopes": ["ALL", *GAPYEONG_MANAGE_CODES],
        "list_requests": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "classification_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }
    if not is_gapyeong_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Gapyeong Library owner"
        )
        return [], GAPYEONG_PARSER, meta
    try:
        cutoff = _today(today)
        if any(
            isinstance(value, bool) or int(value) < 1
            for value in (timeout, max_pages, max_workers)
        ) or isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("invalid collection limits")
    except Exception as exc:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": _clean(exc),
            }
        )
        return [], GAPYEONG_PARSER, meta
    factory, current_fetcher = session_factory or _session, fetcher or _request
    workers = min(int(max_workers), GAPYEONG_MAX_WORKERS)
    first_pages: dict[str, _Page] = {}
    scope_pages: dict[str, dict[int, _Page]] = {}
    scope_checks: dict[str, dict[str, _Page]] = {}
    try:
        for scope in scopes:
            first = _parse_page(
                _soup(
                    gapyeong_list_url(1, scope),
                    int(timeout),
                    factory,
                    current_fetcher,
                ),
                1,
                scope,
            )
            meta["list_requests"] += 1
            first_pages[scope] = first
            scope_pages[scope] = {1: first}
        required_requests = sum(first.last + 3 for first in first_pages.values())
        meta["required_list_requests"] = required_requests
        if required_requests > int(max_pages):
            raise GapyeongContractError(
                f"max_pages {max_pages} below required {required_requests}"
            )

        for scope in scopes:
            first = first_pages[scope]
            jobs = [("data", page) for page in range(2, first.last + 1)] + [
                ("sentinel", first.last + 1),
                ("first", 1),
                ("last", first.last),
            ]
            checks: dict[str, _Page] = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        lambda page=page, scope=scope, first=first: _parse_page(
                            _soup(
                                gapyeong_list_url(page, scope),
                                int(timeout),
                                factory,
                                current_fetcher,
                            ),
                            page,
                            scope,
                            expected_total=first.total,
                            expected_last=first.last,
                        )
                    ): (kind, page)
                    for kind, page in jobs
                }
                for future in as_completed(futures):
                    kind, page = futures[future]
                    parsed = future.result()
                    meta["list_requests"] += 1
                    if kind == "data":
                        scope_pages[scope][page] = parsed
                    else:
                        checks[kind] = parsed
            scope_checks[scope] = checks
            if set(scope_pages[scope]) != set(range(1, first.last + 1)):
                raise GapyeongContractError(f"scope {scope or 'ALL'}: data page missing")
            if checks.get("sentinel") is None or checks["sentinel"].rows:
                raise GapyeongContractError(
                    f"scope {scope or 'ALL'}: post-last empty sentinel failed"
                )
            if (
                checks.get("first") is None
                or _page_signature(checks["first"]) != _page_signature(first)
            ):
                raise GapyeongContractError(
                    f"scope {scope or 'ALL'}: first page recheck failed"
                )
            if (
                checks.get("last") is None
                or _page_signature(checks["last"])
                != _page_signature(scope_pages[scope][first.last])
            ):
                raise GapyeongContractError(
                    f"scope {scope or 'ALL'}: last page recheck failed"
                )

        rows_by_scope: dict[str, list[dict[str, Any]]] = {}
        for scope in scopes:
            first = first_pages[scope]
            rows = [
                row
                for page in range(1, first.last + 1)
                for row in scope_pages[scope][page].rows
            ]
            if len(rows) != first.total or len({row["identity"] for row in rows}) != first.total:
                raise GapyeongContractError(
                    f"scope {scope or 'ALL'}: declared total/identity mismatch"
                )
            rows_by_scope[scope] = rows
        all_rows = rows_by_scope[""]
        all_by_id = {row["identity"]: row for row in all_rows}
        partition_rows = [
            row for scope in GAPYEONG_MANAGE_CODES for row in rows_by_scope[scope]
        ]
        partition_by_id = {row["identity"]: row for row in partition_rows}
        if (
            len(partition_rows) != len(partition_by_id)
            or set(partition_by_id) != set(all_by_id)
            or any(
                _course_signature(all_by_id[identity])
                != _course_signature(partition_by_id[identity])
                for identity in all_by_id
            )
        ):
            raise GapyeongContractError(
                "unfiltered and five facility partitions do not reconcile"
            )
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["source_cap_reached"] = "max_pages" in meta["configured_collection_error"]
        return [], GAPYEONG_PARSER, meta

    period_anomalies = [
        row
        for row in all_rows
        if row["education_period_anomaly"] or row["application_period_anomaly"]
    ]
    unsafe_period_anomalies = [
        row
        for row in period_anomalies
        if row["status"] != "CLOSED" or row["end"] >= cutoff
    ]
    if unsafe_period_anomalies:
        meta["configured_collection_error"] = (
            "unsafe current/non-terminal reversed official period: "
            + ",".join(row["identity"] for row in unsafe_period_anomalies[:5])
        )
        return [], GAPYEONG_PARSER, meta
    current = [row for row in all_rows if row["end"] >= cutoff]
    scope_totals = {
        (scope or "ALL"): first_pages[scope].total for scope in scopes
    }
    scope_page_counts = {
        (scope or "ALL"): first_pages[scope].last for scope in scopes
    }
    meta.update(
        {
            "cutoff": cutoff.isoformat(),
            "source_rows": len(all_rows),
            "source_total": len(all_rows),
            "scope_totals": scope_totals,
            "scope_page_counts": scope_page_counts,
            "data_pages": sum(scope_page_counts.values()),
            "post_last_sentinel_count": len(scopes),
            "boundary_rechecks": len(scopes) * 2,
            "partition_total": len(partition_rows),
            "partition_identity_difference_count": 0,
            "current_source_count": len(current),
            "expired_count": len(all_rows) - len(current),
            "expired_period_anomaly_count": len(period_anomalies),
            "source_status_counts": dict(
                Counter(row["source_status"] for row in all_rows)
            ),
            "source_scope_counts": dict(Counter(row["scope"] for row in all_rows)),
            "pagination_complete": True,
            "classification_complete": True,
        }
    )
    if len(current) > int(detail_limit):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit {detail_limit} below required {len(current)}"
                ),
            }
        )
        return [], GAPYEONG_PARSER, meta

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                lambda item=item: _detail(
                    item,
                    _soup(
                        gapyeong_detail_url(str(item["identity"])),
                        int(timeout),
                        factory,
                        current_fetcher,
                    ),
                    cutoff,
                )
            ): item["identity"]
            for item in current
        }
        for future in as_completed(futures):
            try:
                rows.append(future.result())
                meta["detail_pages"] += 1
            except Exception as exc:
                errors.append(
                    f"{futures[future]}: {type(exc).__name__}: {_clean(exc)}"
                )
    if errors:
        meta["configured_collection_error"] = "; ".join(errors[:5])
        return [], GAPYEONG_PARSER, meta
    rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
    rows = list((dedupe_rows or _dedupe)(rows))
    privacy_errors = [error for row in rows for error in _privacy(row)]
    if privacy_errors or len(rows) != len(current):
        meta["configured_collection_error"] = (
            "; ".join(privacy_errors[:5])
            or "dedupe changed the complete current identity set"
        )
        return [], GAPYEONG_PARSER, meta
    meta.update(
        {
            "returned_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "branch_counts": dict(Counter(row["branch"] for row in rows)),
            "application_control_count": sum(
                1 for row in rows if row["application_url"]
            ),
            "identity_duplicate_count": 0,
            "pii_payload_persisted": False,
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "no_current_data": not rows,
        }
    )
    return rows, GAPYEONG_PARSER, meta


collect = collect_gapyeong_education
