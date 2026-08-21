"""Fail-closed collector for Gwanak-gu's official education lectures.

The existing provider historically pointed at the ``29000400`` information-
education subset.  Keeping that provider identifier avoids creating duplicate
database ownership, while the canonical route is widened to the official
``Lecture_List.do`` union.  The collector also follows the disjoint official
talent-sharing lecture list exposed from the same education portal.

This module intentionally has no dependency on the shared municipal router,
configuration, scheduler, or database.  A parent crawler may inject managed
HTTP sessions, a fetch helper, and its normal row deduplicator.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import date, datetime
import hashlib
import ipaddress
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


# Preserve the already-owned provider even though the canonical URL is widened
# from the old ``?scLcOrganization1=29000400`` subset to the complete list.
GWANAK_EDUCATION_PROVIDER = "MUNI_WWW_GWANAK_GO_KR_51D9DCB4"
GWANAK_EDUCATION_LEGACY_URL = "https://www.gwanak.go.kr/site/edu/lecture/Lecture_List.do?scLcOrganization1=29000400"
GWANAK_EDUCATION_URL = "https://www.gwanak.go.kr/site/edu/lecture/Lecture_List.do"
GWANAK_KNOWLEDGE_URL = "https://www.gwanak.go.kr/site/edu/lecture/Knowledge_Lecture_List.do"
GWANAK_HOST = "www.gwanak.go.kr"
GWANAK_MAIN_LIST_PATH = "/site/edu/lecture/Lecture_List.do"
GWANAK_MAIN_DETAIL_PATH = "/site/edu/lecture/Lecture_View.do"
GWANAK_KNOWLEDGE_LIST_PATH = "/site/edu/lecture/Knowledge_Lecture_List.do"
GWANAK_KNOWLEDGE_DETAIL_PATH = "/site/edu/lecture/Knowledge_Lecture_View.do"
GWANAK_MAIN_PAGE_SIZE = 10
GWANAK_KNOWLEDGE_PAGE_SIZE = 9
GWANAK_MAX_WORKERS = 8
GWANAK_EDUCATION_PARSER = "gwanak_official_lecture_union_complete_current_future+detail"
GWANAK_MUNICIPALITY_CODE = "1162000000"
GWANAK_MUNICIPALITY_NAME = "서울특별시 관악구"

GWANAK_ORGANIZATIONS: Mapping[str, str] = {
    "29000100": "평생학습관",
    "29000400": "구민정보화교육",
    "29000500": "자치회관",
    "29000800": "여성교실",
}

GWANAK_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "1차 접수중": "OPEN",
    "2차 접수중": "OPEN",
    "예비자접수": "OPEN",
    "대기자접수": "OPEN",
    "접수대기": "WAITING",
    "2차 접수 대기": "WAITING",
    "접수완료": "CLOSED",
    "접수 마감": "CLOSED",
    "접수마감": "CLOSED",
    "강좌시작": "CLOSED",
    "강좌종료": "CLOSED",
}
GWANAK_OPEN_STATUSES = frozenset(value for value, normalized in GWANAK_STATUS_MAP.items() if normalized == "OPEN")

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DECLARATION_RE = re.compile(r"총\s*([\d,]+)\s*건\s*페이지\s*:\s*([\d,]+)\s*/\s*([\d,]+)")
_MAIN_ID_RE = re.compile(
    r"doLectureView\(\s*'(?P<identity>L\d{8})'\s*,\s*'(?P<delay>[^']*)'\s*,"
    r"\s*'(?P<organization>\d{8})'\s*\)"
)
_KNOWLEDGE_ID_RE = re.compile(r"doLectureView\(\s*'(?P<identity>L\d{8})'\s*\)")
_DATE_RE = re.compile(r"(?<!\d)(?P<year>20\d{2})[.\-/](?P<month>\d{1,2})[.\-/](?P<day>\d{1,2})(?!\d)")
_DATETIME_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})[.\-/](?P<month>\d{1,2})[.\-/](?P<day>\d{1,2})"
    r"(?:[ /T]+(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)
_CAPACITY_RE = re.compile(r"([\d,]+)\s*/\s*(?:\(\s*)?([\d,]+)")
_TEST_TITLE_RE = re.compile(r"(?:^|\W)test(?:\W|$)|테스트|신청금지", re.IGNORECASE)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\s\u200b]+", "", _clean(value)).casefold()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def is_gwanak_education_target(target: Any) -> bool:
    """Match only the upgraded provider-owned canonical union route."""

    return _provider(target) == GWANAK_EDUCATION_PROVIDER and _target_url(target) == GWANAK_EDUCATION_URL


is_target = is_gwanak_education_target


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not getattr(response, "content", b""):
        raise ValueError("empty HTTP response")
    return BeautifulSoup(response.content, "lxml")


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (bytes, bytearray, str)):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher did not return HTML or BeautifulSoup")
    return BeautifulSoup(content, "lxml")


def _fetch(fetcher: Fetcher, current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            result.append(
                date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            )
        except ValueError:
            continue
    return result


def _date_range(value: Any) -> tuple[str, str, str]:
    tokens = _date_tokens(value)
    if len(tokens) < 2 or tokens[1] < tokens[0]:
        return "", "", ""
    start, end = tokens[0], tokens[1]
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _datetime_bounds(value: Any) -> tuple[str, str, str]:
    values: list[datetime] = []
    for match in _DATETIME_RE.finditer(_clean(value)):
        try:
            values.append(
                datetime(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                    int(match.group("hour") or 0),
                    int(match.group("minute") or 0),
                )
            )
        except ValueError:
            continue
    if len(values) < 2:
        return "", "", ""
    start, end = min(values), max(values)
    if end < start:
        return "", "", ""

    def fmt(item: datetime) -> str:
        return item.strftime("%Y-%m-%d %H:%M")

    return fmt(start), fmt(end), f"{fmt(start)} ~ {fmt(end)}"


def _as_int(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    return int(raw) if re.fullmatch(r"\d+", raw) else None


def _capacity_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    match = _CAPACITY_RE.search(_clean(value))
    if not match:
        return None, None
    return _as_int(match.group(1)), _as_int(match.group(2))


def _stable_branch_code(provider: str, branch: str) -> str:
    digest = hashlib.sha1(f"{_clean(provider)}|{_normalized(branch)}".encode("utf-8")).hexdigest()[:12].upper()
    return f"GWANAK_BRANCH_{digest}"


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _base_row(
    target: Any,
    *,
    source_kind: str,
    identity: str,
    title: str,
    institution: str,
    source_status: str,
    start_date: str,
    end_date: str,
    period: str,
    raw_url: str,
) -> dict[str, Any]:
    provider = _provider(target)
    namespace = "knowledge" if source_kind == "knowledge" else "lecture"
    category = "재능나눔학교" if source_kind == "knowledge" else institution
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:{namespace}:{identity}",
        "title": title,
        "branch": institution or "관악구 통합예약",
        "branch_code": _stable_branch_code(provider, institution or "관악구 통합예약"),
        "preserve_branch": True,
        "branch_url": GWANAK_EDUCATION_URL,
        "raw_url": raw_url,
        "application_url": raw_url,
        "status": GWANAK_STATUS_MAP[source_status],
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "category": category,
        "program_type": "강좌",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "region": GWANAK_MUNICIPALITY_NAME,
        "municipality_code": GWANAK_MUNICIPALITY_CODE,
        "municipality_full_name": GWANAK_MUNICIPALITY_NAME,
        "reservation_available": False,
        "raw_fields": {
            "source_kind": source_kind,
            "lecture_id": identity,
            "source_status": source_status,
            "list_institution": institution,
        },
    }


def gwanak_main_list_url(page_no: int) -> str:
    query = urlencode((("pageIndex", str(max(1, int(page_no)))), ("pageUnit", str(GWANAK_MAIN_PAGE_SIZE))))
    return f"https://{GWANAK_HOST}{GWANAK_MAIN_LIST_PATH}?{query}"


def gwanak_knowledge_list_url(page_no: int) -> str:
    query = urlencode((("pageIndex", str(max(1, int(page_no)))),))
    return f"https://{GWANAK_HOST}{GWANAK_KNOWLEDGE_LIST_PATH}?{query}"


def gwanak_detail_url(source_kind: str, identity: str, organization: str = "") -> str:
    lecture_id = _clean(identity)
    if not re.fullmatch(r"L\d{8}", lecture_id):
        return ""
    if source_kind == "knowledge":
        query = urlencode((("clIdx", lecture_id),))
        return f"https://{GWANAK_HOST}{GWANAK_KNOWLEDGE_DETAIL_PATH}?{query}"
    if source_kind != "main" or organization not in GWANAK_ORGANIZATIONS:
        return ""
    query = urlencode((("clIdx", lecture_id), ("scLcOrganization1", organization)))
    return f"https://{GWANAK_HOST}{GWANAK_MAIN_DETAIL_PATH}?{query}"


def _safe_detail_url(value: Any, source_kind: str, identity: str, organization: str = "") -> str:
    candidate = _clean(value)
    if not candidate or candidate != gwanak_detail_url(source_kind, identity, organization):
        return ""
    parsed = urlparse(candidate)
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() != GWANAK_HOST
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return ""
    expected_path = GWANAK_KNOWLEDGE_DETAIL_PATH if source_kind == "knowledge" else GWANAK_MAIN_DETAIL_PATH
    if parsed.path != expected_path:
        return ""
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    ):
        return ""
    expected_query = {"clIdx": [identity]}
    if source_kind == "main":
        expected_query["scLcOrganization1"] = [organization]
    if parse_qs(parsed.query, keep_blank_values=True) != expected_query:
        return ""
    return candidate


def _declaration(soup: BeautifulSoup) -> Optional[tuple[int, int, int]]:
    node = soup.select_one(".count")
    match = _DECLARATION_RE.search(_clean(node.get_text(" ", strip=True) if node else ""))
    if not match:
        return None
    return tuple(int(value.replace(",", "")) for value in match.groups())  # type: ignore[return-value]


def _main_rows(target: Any, soup: BeautifulSoup) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for position, tr in enumerate(soup.select("tbody tr"), start=1):
        anchor = tr.select_one("a[href*='doLectureView']")
        if anchor is None:
            if _clean(tr.get_text(" ", strip=True)):
                errors.append(f"main row {position}: missing official lecture link")
            continue
        match = _MAIN_ID_RE.search(_clean(anchor.get("href")))
        if not match:
            errors.append(f"main row {position}: malformed official lecture identity")
            continue
        identity = match.group("identity")
        organization = match.group("organization")
        institution = GWANAK_ORGANIZATIONS.get(organization, "")
        if not institution:
            errors.append(f"main {identity}: unknown organization {organization}")
            continue
        spans = anchor.select("span")
        listed_institution = _clean(spans[0].get_text(" ", strip=True)).strip("[]") if spans else ""
        title = _clean(" ".join(span.get_text(" ", strip=True) for span in spans[1:]))
        if not title:
            title = _clean(anchor.get_text(" ", strip=True)).removeprefix(f"[{listed_institution}]").strip()
        if listed_institution != institution:
            errors.append(f"main {identity}: organization label {listed_institution!r} does not match {institution!r}")
        cells = tr.select("td")
        if len(cells) < 7:
            errors.append(f"main {identity}: expected seven list columns")
            continue
        start_date, end_date, period = _date_range(cells[2].get_text(" ", strip=True))
        source_status = _clean((tr.select_one(".state") or cells[6]).get_text(" ", strip=True))
        if not title or not start_date or not end_date:
            errors.append(f"main {identity}: missing title or valid education period")
            continue
        if source_status not in GWANAK_STATUS_MAP:
            errors.append(f"main {identity}: unknown source status {source_status!r}")
            continue
        raw_url = gwanak_detail_url("main", identity, organization)
        current, capacity = _capacity_pair(cells[5].get_text(" ", strip=True))
        row = _base_row(
            target,
            source_kind="main",
            identity=identity,
            title=title,
            institution=institution,
            source_status=source_status,
            start_date=start_date,
            end_date=end_date,
            period=period,
            raw_url=raw_url,
        )
        row.update(
            {
                "venue_name": _clean(cells[3].get_text(" ", strip=True)),
                "fee": _clean(cells[4].get_text(" ", strip=True)),
                "capacity_current": current,
                "capacity_total": capacity,
                "application_method_raw": _clean((tr.select_one(".method") or cells[6]).get_text(" ", strip=True)),
            }
        )
        row["raw_fields"].update(
            {
                "organization_code": organization,
                "list_number": _clean(cells[0].get_text(" ", strip=True)),
                "list_venue": _clean(cells[3].get_text(" ", strip=True)),
                "list_fee": _clean(cells[4].get_text(" ", strip=True)),
            }
        )
        rows.append(_clean_row(row))
    return rows, errors


def _without_child_label(node: Optional[Tag]) -> str:
    if node is None:
        return ""
    clone = BeautifulSoup(str(node), "lxml").find(node.name)
    if clone is None:
        return ""
    label = clone.select_one("span")
    if label is not None:
        label.decompose()
    return _clean(clone.get_text(" ", strip=True))


def _knowledge_rows(target: Any, soup: BeautifulSoup) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    anchors = soup.select("li > a[href*='doLectureView']")
    for position, anchor in enumerate(anchors, start=1):
        match = _KNOWLEDGE_ID_RE.search(_clean(anchor.get("href")))
        if not match:
            errors.append(f"knowledge card {position}: malformed official lecture identity")
            continue
        identity = match.group("identity")
        title_node = anchor.select_one(".txt-title .title")
        status_node = anchor.select_one(".txt-title .status")
        period_node = anchor.select_one(".txt-data .period")
        institution_node = anchor.select_one(".txt-data .org")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        source_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
        institution = _without_child_label(institution_node) or "재능나눔학교"
        start_date, end_date, period = _date_range(_without_child_label(period_node))
        if not title or not start_date or not end_date:
            errors.append(f"knowledge {identity}: missing title or valid education period")
            continue
        if source_status not in GWANAK_STATUS_MAP:
            errors.append(f"knowledge {identity}: unknown source status {source_status!r}")
            continue
        row = _base_row(
            target,
            source_kind="knowledge",
            identity=identity,
            title=title,
            institution=institution,
            source_status=source_status,
            start_date=start_date,
            end_date=end_date,
            period=period,
            raw_url=gwanak_detail_url("knowledge", identity),
        )
        image = anchor.select_one("img[src]")
        image_url = _clean(image.get("src") if image else "")
        if image_url.startswith(f"https://{GWANAK_HOST}/"):
            row["image_url"] = image_url
        rows.append(_clean_row(row))
    return rows, errors


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    table = soup.select_one("table.info-table")
    pairs: dict[str, str] = {}
    if table is None:
        return pairs
    for tr in table.select("tr"):
        children = [node for node in tr.find_all(["th", "td"], recursive=False)]
        for index, node in enumerate(children):
            if node.name != "th" or index + 1 >= len(children):
                continue
            value_node = children[index + 1]
            if value_node.name != "td":
                continue
            key = _clean(node.get_text(" ", strip=True))
            value = _clean(value_node.get_text(" ", strip=True))
            if key and key not in pairs:
                pairs[key] = value
    return pairs


def _branch(institution: str, venue: str) -> str:
    normalized = _normalized(venue)
    if not venue or any(token in normalized for token in ("온라인", "zoom", "비대면")):
        return institution or "관악구 통합예약"
    return venue


def _application_control(soup: BeautifulSoup, source_kind: str, identity: str) -> Optional[Tag]:
    expected = "doLectureMemberForm" if source_kind == "knowledge" else "getToday"
    controls: list[Tag] = []
    for anchor in soup.select(".btns a.btn.blue[href]"):
        href = _clean(anchor.get("href"))
        match = re.fullmatch(rf"javascript:{expected}\(\s*'{re.escape(identity)}'\s*\);?", href)
        if match:
            controls.append(anchor)
    return controls[0] if len(controls) == 1 else None


def _enrich_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    raw_fields = row.setdefault("raw_fields", {})
    identity = _clean(raw_fields.get("lecture_id"))
    source_kind = _clean(raw_fields.get("source_kind"))
    organization = _clean(raw_fields.get("organization_code"))
    title_node = soup.select_one(".title .name")
    status_node = soup.select_one(".title .status")
    detail_title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    detail_status = _clean(status_node.get_text(" ", strip=True) if status_node else "")
    pairs = _detail_pairs(soup)
    required_keys = {
        "교육기관",
        "교육대상",
        "강좌분야",
        "강사명",
        "수강료",
        "교육장소",
        "교육기간",
        "수강요일",
        "접수기간",
        "정원(예비)",
        "접수인원(예비)",
        "접수방법",
        "전화문의",
    }
    missing = sorted(required_keys - set(pairs))
    if missing:
        errors.append(f"{source_kind} {identity}: missing detail fields {','.join(missing)}")
    if detail_title != _clean(row.get("title")):
        errors.append(f"{source_kind} {identity}: detail/list title mismatch")
    if detail_status != _clean(raw_fields.get("source_status")):
        errors.append(f"{source_kind} {identity}: detail/list status mismatch")
    if detail_status not in GWANAK_STATUS_MAP:
        errors.append(f"{source_kind} {identity}: unknown detail status")

    start_date, end_date, period = _date_range(pairs.get("교육기간"))
    if (start_date, end_date) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ):
        errors.append(f"{source_kind} {identity}: detail/list education period mismatch")
    detail_institution = _clean(pairs.get("교육기관"))
    list_institution = _clean(raw_fields.get("list_institution"))
    if source_kind == "main" and detail_institution != list_institution:
        errors.append(f"main {identity}: detail/list institution mismatch")
    detail_fee = _clean(pairs.get("수강료"))
    list_fee = _clean(raw_fields.get("list_fee"))
    if source_kind == "main" and detail_fee != list_fee:
        errors.append(f"main {identity}: detail/list fee mismatch")

    capacity_total, waitlist_total = _capacity_pair(pairs.get("정원(예비)"))
    capacity_current, waitlist_current = _capacity_pair(pairs.get("접수인원(예비)"))
    if source_kind == "main" and (
        capacity_current != row.get("capacity_current") or capacity_total != row.get("capacity_total")
    ):
        errors.append(f"main {identity}: detail/list capacity mismatch")

    raw_url = _safe_detail_url(row.get("raw_url"), source_kind, identity, organization)
    if not raw_url:
        errors.append(f"{source_kind} {identity}: unsafe official detail URL")
    control = _application_control(soup, source_kind, identity)
    is_open = detail_status in GWANAK_OPEN_STATUSES
    if is_open and control is None:
        errors.append(f"{source_kind} {identity}: open detail has no exact application control")
    # Knowledge pages keep the same exact member-form button visible after a
    # course has started or closed.  Source status is authoritative: retain
    # the control as audit evidence, but never expose it as an application URL
    # unless the status is currently open.

    venue = _clean(pairs.get("교육장소"))
    branch = _branch(detail_institution or list_institution, venue)
    apply_start, apply_end, apply_period = _datetime_bounds(pairs.get("접수기간"))
    row.update(
        {
            "status": GWANAK_STATUS_MAP.get(detail_status, row.get("status")),
            "period": period,
            "start_date": start_date,
            "end_date": end_date,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "apply_period": apply_period,
            "branch": branch,
            "branch_code": _stable_branch_code(_clean(row.get("provider")), branch),
            "venue_name": venue,
            "room": venue,
            "target": _clean(pairs.get("교육대상")),
            "category": (
                "재능나눔학교" if source_kind == "knowledge" else _clean(pairs.get("강좌분야")) or row.get("category")
            ),
            "instructor": _clean(pairs.get("강사명")),
            "fee": detail_fee,
            "schedule_raw": _clean(pairs.get("수강요일")),
            "application_method_raw": _clean(pairs.get("접수방법")),
            "phone": _clean(pairs.get("전화문의")),
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "waitlist_current": waitlist_current,
            "waitlist_total": waitlist_total,
            "reservation_available": bool(is_open and control is not None and raw_url),
        }
    )
    if row["reservation_available"]:
        row["application_url"] = raw_url
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        raw_fields["clear_application_url"] = True
    raw_fields.update(
        {
            "detail_pairs": pairs,
            "detail_application_control": control is not None,
            "detail_valid": not errors,
        }
    )
    return errors


def _parallel_fetch_pages(
    requests_to_make: list[tuple[str, int, str]],
    *,
    timeout: int,
    max_workers: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> tuple[dict[tuple[str, int], BeautifulSoup], list[str]]:
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def current_session() -> Any:
        value = getattr(local, "session", None)
        if value is None:
            value = session_factory()
            local.session = value
            with sessions_lock:
                sessions.append(value)
        return value

    def fetch_one(item: tuple[str, int, str]) -> tuple[str, int, Optional[BeautifulSoup], str]:
        source_kind, page_no, url = item
        error_type = ""
        for attempt in range(3):
            try:
                return (
                    source_kind,
                    page_no,
                    _fetch(fetcher, current_session(), url, timeout),
                    "",
                )
            except Exception as exc:
                error_type = type(exc).__name__
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
        return source_kind, page_no, None, error_type

    fetched: dict[tuple[str, int], BeautifulSoup] = {}
    errors: list[str] = []
    try:
        if requests_to_make:
            workers = min(max(1, int(max_workers)), len(requests_to_make), GWANAK_MAX_WORKERS)
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gwanak-education-pages") as pool:
                for source_kind, page_no, soup, error_type in pool.map(fetch_one, requests_to_make):
                    if soup is None:
                        errors.append(f"{source_kind} page {page_no}: fetch {error_type}")
                    else:
                        fetched[(source_kind, page_no)] = soup
    finally:
        for value in sessions:
            _close_quietly(value)
    return fetched, errors


def _parallel_details(
    rows: list[dict[str, Any]],
    *,
    timeout: int,
    detail_limit: int,
    max_workers: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> tuple[list[dict[str, Any]], int, int, int, list[str], bool]:
    required = len(rows)
    allowed = max(0, int(detail_limit))
    selected = rows[:allowed]
    capped = len(selected) < required
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def current_session() -> Any:
        value = getattr(local, "session", None)
        if value is None:
            value = session_factory()
            local.session = value
            with sessions_lock:
                sessions.append(value)
        return value

    def enrich(row: dict[str, Any]) -> tuple[dict[str, Any], bool, list[str]]:
        raw_fields = row.get("raw_fields") or {}
        identity = _clean(raw_fields.get("lecture_id"))
        source_kind = _clean(raw_fields.get("source_kind"))
        last_errors: list[str] = []
        last_row = row
        for attempt in range(3):
            try:
                soup = _fetch(
                    fetcher,
                    current_session(),
                    _clean(row.get("raw_url")),
                    timeout,
                )
            except Exception as exc:
                last_errors = [f"{source_kind} {identity}: detail fetch {type(exc).__name__}"]
                if attempt < 2:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                return row, False, last_errors
            candidate = copy.deepcopy(row)
            item_errors = _enrich_detail(candidate, soup)
            last_row = candidate
            if not item_errors:
                return candidate, True, []
            last_errors = item_errors
            if not any("missing detail fields" in error for error in item_errors):
                break
            if attempt < 2:
                time.sleep(0.1 * (attempt + 1))
        return last_row, True, last_errors

    results: list[tuple[dict[str, Any], bool, list[str]]] = []
    try:
        if selected:
            workers = min(max(1, int(max_workers)), len(selected), GWANAK_MAX_WORKERS)
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gwanak-education-details") as pool:
                results = list(pool.map(enrich, selected))
    finally:
        for value in sessions:
            _close_quietly(value)
    fetched = sum(success for _row, success, _errors in results)
    errors = [error for _row, _success, item_errors in results for error in item_errors]
    valid_rows = [_clean_row(row) for row, success, item_errors in results if success and not item_errors]
    return valid_rows, required, len(selected), fetched, errors, capped


def _error_message(errors: list[str]) -> str:
    unique = list(dict.fromkeys(errors))
    shown = unique[:50]
    message = "; ".join(shown)
    if len(unique) > len(shown):
        message += f"; ... {len(unique) - len(shown)} more errors"
    return message


def collect_gwanak_education_courses(
    target: Any,
    timeout: int = 25,
    max_pages: int = 100,
    detail_limit: int = 1000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 8,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect both complete official lecture sections and current details.

    Every declared list page is reconciled, the main/knowledge identity sets
    must remain disjoint, and every retained current/future course must have a
    schema-valid official detail before ``snapshot_complete`` becomes true.
    """

    errors: list[str] = []
    if not is_gwanak_education_target(target):
        errors.append("target does not match the provider-owned canonical Gwanak education route")
    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    source_cap_reached = False
    primary_session: Any = None
    first_pages: dict[str, BeautifulSoup] = {}
    declarations: dict[str, tuple[int, int, int]] = {}
    fetched_pages: dict[tuple[str, int], BeautifulSoup] = {}

    if not errors:
        try:
            primary_session = make_session()
            for source_kind, url in (
                ("main", gwanak_main_list_url(1)),
                ("knowledge", gwanak_knowledge_list_url(1)),
            ):
                try:
                    soup = _fetch(fetch, primary_session, url, timeout)
                except Exception as exc:
                    errors.append(f"{source_kind} page 1: fetch {type(exc).__name__}")
                    continue
                first_pages[source_kind] = soup
                declaration = _declaration(soup)
                if declaration is None:
                    errors.append(f"{source_kind} page 1: missing total/page declaration")
                    continue
                total, current_page, pages = declaration
                if current_page != 1 or total < 0 or pages < 1:
                    errors.append(f"{source_kind} page 1: malformed declaration {declaration}")
                    continue
                declarations[source_kind] = declaration
                fetched_pages[(source_kind, 1)] = soup
        finally:
            _close_quietly(primary_session)

    page_requests: list[tuple[str, int, str]] = []
    page_cap = max(1, int(max_pages))
    for source_kind, declaration in declarations.items():
        _total, _current_page, declared_pages = declaration
        if declared_pages > page_cap:
            source_cap_reached = True
            errors.append(f"{source_kind}: max_pages cap reached after {page_cap} of {declared_pages} declared pages")
        for page_no in range(2, min(declared_pages, page_cap) + 1):
            url = gwanak_main_list_url(page_no) if source_kind == "main" else gwanak_knowledge_list_url(page_no)
            page_requests.append((source_kind, page_no, url))
    additional_pages, fetch_errors = _parallel_fetch_pages(
        page_requests,
        timeout=timeout,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
    )
    fetched_pages.update(additional_pages)
    errors.extend(fetch_errors)

    section_rows: dict[str, list[dict[str, Any]]] = {"main": [], "knowledge": []}
    section_unique: dict[str, set[str]] = {"main": set(), "knowledge": set()}
    duplicate_count = 0
    pages_fetched = 0
    section_complete: dict[str, bool] = {"main": False, "knowledge": False}
    for source_kind in ("main", "knowledge"):
        declaration = declarations.get(source_kind)
        if declaration is None:
            continue
        total, _current_page, declared_pages = declaration
        allowed_pages = min(declared_pages, page_cap)
        for page_no in range(1, allowed_pages + 1):
            soup = fetched_pages.get((source_kind, page_no))
            if soup is None:
                continue
            pages_fetched += 1
            if _declaration(soup) != (total, page_no, declared_pages):
                errors.append(
                    f"{source_kind} page {page_no}: declaration changed from "
                    f"{(total, page_no, declared_pages)} to {_declaration(soup)}"
                )
            parsed, parse_errors = _main_rows(target, soup) if source_kind == "main" else _knowledge_rows(target, soup)
            errors.extend(parse_errors)
            for row in parsed:
                identity = _clean((row.get("raw_fields") or {}).get("lecture_id"))
                if identity in section_unique[source_kind]:
                    duplicate_count += 1
                    errors.append(f"{source_kind}: duplicate official lecture ID {identity}")
                    continue
                section_unique[source_kind].add(identity)
                section_rows[source_kind].append(row)
        complete = (
            allowed_pages == declared_pages
            and all((source_kind, page_no) in fetched_pages for page_no in range(1, declared_pages + 1))
            and len(section_unique[source_kind]) == total
        )
        if len(section_unique[source_kind]) != total and allowed_pages == declared_pages:
            errors.append(
                f"{source_kind}: declared total {total} does not match "
                f"{len(section_unique[source_kind])} unique lecture IDs"
            )
        section_complete[source_kind] = complete

    cross_source_ids = section_unique["main"] & section_unique["knowledge"]
    if cross_source_ids:
        duplicate_count += len(cross_source_ids)
        errors.append(f"main/knowledge overlap contains {len(cross_source_ids)} official lecture IDs")

    all_rows = [*section_rows["main"], *section_rows["knowledge"]]
    source_status_counts = Counter(_clean((row.get("raw_fields") or {}).get("source_status")) for row in all_rows)
    section_total_counts = {
        source_kind: declarations.get(source_kind, (0, 0, 0))[0] for source_kind in ("main", "knowledge")
    }
    section_page_counts = {
        source_kind: declarations.get(source_kind, (0, 0, 0))[2] for source_kind in ("main", "knowledge")
    }
    raw_current: list[dict[str, Any]] = []
    expired_count = 0
    invalid_test_count = 0
    raw_current_section_counts: Counter[str] = Counter()
    current_section_counts: Counter[str] = Counter()
    organization_counts: Counter[str] = Counter()
    for row in all_rows:
        end_date = _clean(row.get("end_date"))
        source_kind = _clean((row.get("raw_fields") or {}).get("source_kind"))
        if not end_date or date.fromisoformat(end_date) < cutoff:
            expired_count += 1
            continue
        raw_current_section_counts[source_kind] += 1
        raw_current.append(row)
        if _TEST_TITLE_RE.search(_clean(row.get("title"))):
            invalid_test_count += 1
            continue
        current_section_counts[source_kind] += 1
        if source_kind == "main":
            code = _clean((row.get("raw_fields") or {}).get("organization_code"))
            organization_counts[code] += 1

    current_candidates = [row for row in raw_current if not _TEST_TITLE_RE.search(_clean(row.get("title")))]
    (
        detail_rows,
        detail_required,
        detail_attempts,
        detail_pages,
        detail_errors,
        detail_capped,
    ) = _parallel_details(
        current_candidates,
        timeout=timeout,
        detail_limit=detail_limit,
        max_workers=max_workers,
        fetcher=fetch,
        session_factory=make_session,
    )
    if detail_capped:
        source_cap_reached = True
        errors.append(f"detail_limit cap allows {detail_attempts} of {detail_required} required detail pages")
    errors.extend(detail_errors)

    output_deduplicated = 0
    if dedupe_rows is not None:
        before = len(detail_rows)
        detail_rows = list(dedupe_rows(detail_rows))
        output_deduplicated = before - len(detail_rows)
        if output_deduplicated:
            duplicate_count += output_deduplicated

    list_complete = (
        len(declarations) == 2
        and all(section_complete.values())
        and not cross_source_ids
        and duplicate_count == output_deduplicated
    )
    details_complete = (
        detail_attempts == detail_required
        and detail_pages == detail_required
        and not detail_errors
        and len(detail_rows) == detail_required - output_deduplicated
    )
    snapshot_complete = list_complete and details_complete and not errors
    no_current_data = snapshot_complete and not detail_rows
    total_count = sum(section_total_counts.values())
    meta: dict[str, Any] = {
        "pages": pages_fetched,
        "declared_pages": sum(section_page_counts.values()),
        "detail_pages": detail_pages,
        "detail_attempts": detail_attempts,
        "detail_required_count": detail_required,
        "required_detail_count": detail_required,
        "detail_errors": len(detail_errors),
        "pagination_detected": any(value > 1 for value in section_page_counts.values()),
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "full_snapshot_required": True,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "total_count": total_count,
        "source_total_count": total_count,
        "main_total_count": section_total_counts["main"],
        "knowledge_total_count": section_total_counts["knowledge"],
        "main_declared_pages": section_page_counts["main"],
        "knowledge_declared_pages": section_page_counts["knowledge"],
        "discovered_links": len(section_unique["main"] | section_unique["knowledge"]),
        "candidate_count": len(raw_current),
        "raw_current_count": len(raw_current),
        "current_count": len(detail_rows),
        "expired_count": expired_count,
        "invalid_test_count": invalid_test_count,
        "duplicate_count": duplicate_count,
        "cross_source_duplicate_count": len(cross_source_ids),
        "output_deduplicated_count": output_deduplicated,
        "raw_current_section_counts": dict(raw_current_section_counts),
        "current_section_counts": dict(current_section_counts),
        "current_organization_counts": dict(organization_counts),
        "source_status_counts": dict(source_status_counts),
        "branch_counts": dict(Counter(_clean(row.get("branch")) for row in detail_rows)),
        "no_current_data": no_current_data,
        "no_current_reason": (
            "official Gwanak lecture and talent-sharing lists contain no current/future education"
            if no_current_data
            else ""
        ),
        "legacy_subset_url": GWANAK_EDUCATION_LEGACY_URL,
    }
    if errors:
        meta["configured_collection_error"] = _error_message(errors)
    return detail_rows, GWANAK_EDUCATION_PARSER, meta


collect_courses = collect_gwanak_education_courses


__all__ = [
    "GWANAK_EDUCATION_PROVIDER",
    "GWANAK_EDUCATION_URL",
    "GWANAK_EDUCATION_LEGACY_URL",
    "GWANAK_KNOWLEDGE_URL",
    "GWANAK_EDUCATION_PARSER",
    "GWANAK_MUNICIPALITY_CODE",
    "GWANAK_ORGANIZATIONS",
    "is_gwanak_education_target",
    "is_target",
    "gwanak_main_list_url",
    "gwanak_knowledge_list_url",
    "gwanak_detail_url",
    "collect_gwanak_education_courses",
    "collect_courses",
]
