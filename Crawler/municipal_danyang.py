"""Fail-closed collector for Danyang-gun Lifelong Learning courses.

The public menu is only a shell.  The actual current catalogue is rendered in
an iframe at ``/lms/sub3/course_lst.jsp``.  The generic municipal crawler used
to mistake explanatory pages such as "평생학습프로그램이란" for courses.
This collector owns the reviewed iframe, validates every current/future detail
identity, and publishes nothing when the shell, list, or any detail drifts.

Professor names, operator phone numbers, plan attachments, and free-form
detail text are deliberately not persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


DANYANG_PROVIDER = "MUNI_OK_DANYANG_GO_KR_34A40811"
DANYANG_ALIAS_PROVIDERS = frozenset(
    {
        "MUNI_OK_DANYANG_GO_KR_6BF58004",
        "MUNI_OK_DANYANG_GO_KR_E2244BA0",
    }
)
DANYANG_CANONICAL_CANDIDATE_ID = "MUNI_IR_446384798FE7"
DANYANG_CANONICAL_URL = (
    "https://ok.danyang.go.kr/lms/menu.jsp?menu=menu_03_01"
)
DANYANG_ALIAS_URLS = (
    "https://ok.danyang.go.kr/",
    "https://ok.danyang.go.kr/lms/cms.jsp?menu=menu_03_13",
    "https://ok.danyang.go.kr/lms/menu.jsp?menu=menu_04_06",
)
DANYANG_LIST_URL = (
    "https://ok.danyang.go.kr/lms/sub3/course_lst.jsp?"
    "edu_insti_seq=1&org_seq=1"
)
DANYANG_HOST = "ok.danyang.go.kr"
DANYANG_MUNICIPALITY_CODE = "4380000000"
DANYANG_MUNICIPALITY_NAME = "충청북도 단양군"
DANYANG_BRANCH = "단양군평생학습관"
DANYANG_MAX_WORKERS = 6
DANYANG_PARSER = (
    "danyang_lifelong_iframe_complete_list+stable_recheck+"
    "all_current_details+identity_crosscheck+pii_allowlist"
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class DanyangContractError(ValueError):
    """The official source no longer matches the reviewed Danyang contract."""


_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_CAPACITY_RE = re.compile(r"(?P<total>\d{1,5})\s*/\s*(?P<current>\d{1,5})")
_LIST_PERIOD_RE = re.compile(
    r"^(?P<apply_start>20\d{2}-\d{2}-\d{2})\s+"
    r"(?P<apply_start_time>[0-2]\d:[0-5]\d)\s*~\s*"
    r"(?P<apply_end>20\d{2}-\d{2}-\d{2})\s+"
    r"(?P<apply_end_time>[0-2]\d:[0-5]\d)\s+"
    r"(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_DETAIL_PERIOD_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})(?:\s*/.*)?$"
)
_DETAIL_ID_QUERY = ("term_seq", "edu_insti_seq", "open_course_seq", "org_seq")
_LIST_HEADERS = (
    "강좌명",
    "정원/ 수강신청자",
    "등록 기간/ 교육 기간",
    "강의료",
    "현황",
)
_DETAIL_KEYS = (
    "운영기관",
    "운영기관 연락처",
    "교육기간/시수",
    "정원",
    "교육시간/장소",
    "수강료",
    "대상",
    "교육목적",
    "강의안내",
    "교재",
    "강의 계획서 파일",
)
_STATUS_MAP = {
    "신청중": "OPEN",
    "접수중": "OPEN",
    "모집중": "OPEN",
    "신청대기": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "신청완료": "CLOSED",
    "접수완료": "CLOSED",
    "모집완료": "CLOSED",
    "접수마감": "CLOSED",
    "학습중": "CLOSED",
    "교육중": "CLOSED",
    "학습완료": "CLOSED",
    "교육완료": "CLOSED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_danyang_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != DANYANG_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == DANYANG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == "/lms/menu.jsp"
        and parse_qsl(parsed.query, keep_blank_values=True)
        == [("menu", "menu_03_01")]
        and not parsed.params
        and not parsed.fragment
    )


is_target = is_danyang_target


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": DANYANG_CANONICAL_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise DanyangContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise DanyangContractError("redirect response is not accepted")
    if not getattr(response, "content", b""):
        raise DanyangContractError("empty HTTP response")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("HTML fetcher returned neither HTML nor a response")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _validate_site_title(soup: BeautifulSoup, context: str) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "단양군 평생학습관 | 단양군 평생학습관":
        raise DanyangContractError(f"{context} ownership title changed")


def _validate_landing(soup: BeautifulSoup) -> None:
    _validate_site_title(soup, "landing")
    headings = [_clean(node.get_text(" ", strip=True)) for node in soup.select("h2")]
    if "수강 안내 및 신청" not in headings:
        raise DanyangContractError("landing course heading changed")
    frames = soup.select("iframe[src]")
    owned = []
    for frame in frames:
        parsed = urlparse(urljoin(DANYANG_CANONICAL_URL, _clean(frame.get("src"))))
        if (
            parsed.scheme == "https"
            and parsed.hostname == DANYANG_HOST
            and parsed.path == "/lms/sub3/course_lst.jsp"
            and parse_qsl(parsed.query, keep_blank_values=True)
            == [("edu_insti_seq", "1"), ("org_seq", "1")]
            and not parsed.fragment
        ):
            owned.append(frame)
    if len(owned) != 1:
        raise DanyangContractError("landing canonical course iframe changed")


def _direct_cells(row: Any) -> list[Any]:
    return row.find_all(["th", "td"], recursive=False)


def _detail_identity_from_anchor(anchor: Any) -> tuple[str, str, str, str]:
    href = urlparse(urljoin(DANYANG_LIST_URL, _clean(anchor.get("href"))))
    query = parse_qsl(href.query, keep_blank_values=True)
    if (
        href.scheme != "https"
        or href.hostname != DANYANG_HOST
        or href.path != "/lms/menu.jsp"
        or not query
        or query[0] != ("menu", "menu_13_01_v")
        or tuple(key for key, _ in query[1:])
        != (
            "term_seq",
            "edu_insti_seq",
            "open_course_seq",
            "returnurl",
            "menu_code",
            "org_seq",
        )
        or href.fragment
    ):
        raise DanyangContractError("course detail URL contract changed")
    values = dict(query)
    identity = tuple(values[key] for key in _DETAIL_ID_QUERY)
    if any(not _IDENTITY_RE.fullmatch(value) for value in identity):
        raise DanyangContractError("course detail identity is invalid")
    onclick = _clean(anchor.get("onclick"))
    expected_call = (
        f"openCourseView('{identity[0]}','{identity[1]}','{identity[2]}',"
        f"'{identity[3]}','{values['returnurl']}&menu_code={values['menu_code']}'); "
        "return false;"
    )
    if onclick != expected_call:
        raise DanyangContractError("course onclick identity differs from href")
    return identity


def _detail_url(identity: tuple[str, str, str, str]) -> str:
    return "https://ok.danyang.go.kr/lms/menu.jsp?" + urlencode(
        (
            ("menu", "menu_03_01_v"),
            ("term_seq", identity[0]),
            ("edu_insti_seq", identity[1]),
            ("open_course_seq", identity[2]),
            ("org_seq", identity[3]),
        )
    )


def _parse_list(soup: BeautifulSoup) -> tuple[list[dict[str, Any]], tuple[Any, ...]]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "프로그램 리스트":
        raise DanyangContractError("iframe ownership title changed")
    tables = soup.select("table.tb_list")
    if len(tables) != 1:
        raise DanyangContractError("expected exactly one course table")
    table = tables[0]
    header_rows = table.select("thead > tr")
    if len(header_rows) != 1:
        raise DanyangContractError("course table header structure changed")
    headers = tuple(_clean(cell.get_text(" ", strip=True)) for cell in _direct_cells(header_rows[0]))
    if headers != _LIST_HEADERS:
        raise DanyangContractError(f"course headers changed: {headers!r}")

    rows: list[dict[str, Any]] = []
    no_data = False
    for tr in table.select("tbody > tr"):
        cells = _direct_cells(tr)
        if len(cells) == 1 and "등록된" in _clean(cells[0].get_text(" ", strip=True)):
            no_data = True
            continue
        if len(cells) != 5:
            raise DanyangContractError("course row column count changed")
        anchors = cells[0].select("a[href]")
        if len(anchors) != 1:
            raise DanyangContractError("course row detail link changed")
        identity = _detail_identity_from_anchor(anchors[0])
        title_text = _clean(anchors[0].get_text(" ", strip=True)).rstrip("/").strip()
        if len(title_text) < 2:
            raise DanyangContractError("course title is empty")
        capacity_text = _clean(cells[1].get_text(" ", strip=True))
        capacity_match = _CAPACITY_RE.fullmatch(capacity_text)
        if not capacity_match:
            raise DanyangContractError(f"course {identity[2]} capacity changed")
        period_text = _clean(cells[2].get_text(" ", strip=True))
        period_match = _LIST_PERIOD_RE.fullmatch(period_text)
        if not period_match:
            raise DanyangContractError(f"course {identity[2]} periods changed")
        status_text = _clean(cells[4].get_text(" ", strip=True))
        status = _STATUS_MAP.get(status_text)
        if not status:
            raise DanyangContractError(
                f"course {identity[2]} unknown status {status_text!r}"
            )
        rows.append(
            {
                "identity": identity,
                "title_list": title_text,
                "capacity_total": int(capacity_match.group("total")),
                "capacity_current": int(capacity_match.group("current")),
                "apply_start": period_match.group("apply_start"),
                "apply_start_time": period_match.group("apply_start_time"),
                "apply_end": period_match.group("apply_end"),
                "apply_end_time": period_match.group("apply_end_time"),
                "start": period_match.group("start"),
                "end": period_match.group("end"),
                "fee": _clean(cells[3].get_text(" ", strip=True)),
                "source_status": status_text,
                "status": status,
                "detail_url": _detail_url(identity),
            }
        )
    if no_data and rows:
        raise DanyangContractError("no-data marker appears with course rows")
    identities = [row["identity"] for row in rows]
    if len(identities) != len(set(identities)):
        raise DanyangContractError("course list contains duplicate identities")
    paging = [
        anchor
        for anchor in soup.select("a[href], a[onclick]")
        if re.search(r"(?:page|paging|goPage)\s*[=(]", _clean(anchor), re.I)
        or re.search(r"(?:page|paging|goPage)\s*[=(]", _clean(anchor.get("onclick")), re.I)
    ]
    if paging:
        raise DanyangContractError("unaudited course pagination appeared")
    fingerprint = tuple(
        (
            row["identity"],
            row["title_list"],
            row["capacity_total"],
            row["capacity_current"],
            row["apply_start"],
            row["apply_start_time"],
            row["apply_end"],
            row["apply_end_time"],
            row["start"],
            row["end"],
            row["fee"],
            row["source_status"],
        )
        for row in rows
    )
    return rows, fingerprint


def _detail_values(table: Any) -> tuple[str, dict[str, str]]:
    title_nodes = table.select("th.bbs_tit")
    if len(title_nodes) != 1:
        raise DanyangContractError("detail course title changed")
    title = _clean(title_nodes[0].get_text(" ", strip=True))
    values: dict[str, str] = {}
    for tr in table.select("tr"):
        cells = _direct_cells(tr)
        for index, cell in enumerate(cells):
            if (
                cell.name == "th"
                and "bbs_tit" not in (cell.get("class") or [])
                and index + 1 < len(cells)
                and cells[index + 1].name == "td"
            ):
                key = _clean(cell.get_text(" ", strip=True))
                if key in values:
                    raise DanyangContractError(f"duplicate detail field {key!r}")
                values[key] = _clean(cells[index + 1].get_text(" ", strip=True))
    if tuple(values) != _DETAIL_KEYS:
        raise DanyangContractError(f"detail fields changed: {tuple(values)!r}")
    return title, values


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    return f"{DANYANG_PROVIDER}:CENTER:{digest}"[:100]


def _price(value: str) -> Optional[int]:
    if value == "무료":
        return 0
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else None


def _venues(schedule: str) -> str:
    values = []
    for value in re.findall(r",\s*([^\[]+?)(?=\s*\[[월화수목금토일]\]|$)", schedule):
        cleaned = _clean(value).strip(" ,")
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return " / ".join(values)


def _parse_detail(
    soup: BeautifulSoup,
    source: Mapping[str, Any],
    target: Any,
) -> dict[str, Any]:
    _validate_site_title(soup, f"course {source['identity'][2]}")
    tables = soup.select("table.tb_view")
    if not tables:
        raise DanyangContractError(f"course {source['identity'][2]} detail table missing")
    title, values = _detail_values(tables[-1])
    detail_period = _DETAIL_PERIOD_RE.fullmatch(values["교육기간/시수"])
    if (
        not detail_period
        or detail_period.group("start") != source["start"]
        or detail_period.group("end") != source["end"]
    ):
        raise DanyangContractError(
            f"course {source['identity'][2]} detail period differs from list"
        )
    capacity_match = re.fullmatch(r"(\d{1,5})명", values["정원"])
    if not capacity_match or int(capacity_match.group(1)) != source["capacity_total"]:
        raise DanyangContractError(
            f"course {source['identity'][2]} detail capacity differs from list"
        )
    if values["수강료"] != source["fee"]:
        raise DanyangContractError(
            f"course {source['identity'][2]} detail fee differs from list"
        )
    if not title or source["title_list"].replace(" | ", "_").replace("/", "").replace(" ", "") != title.replace(" ", ""):
        raise DanyangContractError(
            f"course {source['identity'][2]} detail title differs from list"
        )
    institution = values["운영기관"] or DANYANG_BRANCH
    branch = f"{DANYANG_MUNICIPALITY_NAME} · {institution}"
    schedule = values["교육시간/장소"]
    venue = _venues(schedule)
    identity = source["identity"]
    course_id = ":".join((DANYANG_PROVIDER, *identity))
    open_now = source["status"] == "OPEN"
    return {
        "provider": DANYANG_PROVIDER,
        "provider_course_id": course_id,
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": branch,
        "branch_code": _branch_code(branch),
        "period": f"{source['start']} ~ {source['end']}",
        "start_date": source["start"],
        "end_date": source["end"],
        "apply_period": (
            f"{source['apply_start']} {source['apply_start_time']} ~ "
            f"{source['apply_end']} {source['apply_end_time']}"
        ),
        "apply_start_date": source["apply_start"],
        "apply_end_date": source["apply_end"],
        "status": source["status"],
        "category": "교육",
        "program_type": "평생학습 강좌",
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "iframe_list+detail_html",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "schedule_raw": schedule,
        "target": values["대상"],
        "room": venue,
        "venue": venue,
        "description": "",
        "price": _price(source["fee"]),
        "price_text": source["fee"],
        "capacity_total": source["capacity_total"],
        "capacity_current": source["capacity_current"],
        "capacity_remaining": max(
            0, source["capacity_total"] - source["capacity_current"]
        ),
        "application_method": "온라인 수강신청",
        "application_methods": ["온라인"],
        "reservation_available": open_now,
        "application_url": source["detail_url"] if open_now else "",
        "application_type": "ONLINE_RESERVATION" if open_now else "",
        "raw_url": source["detail_url"],
        "source_url": _clean(_target_value(target, "url")),
        "raw_fields": {
            "term_seq": identity[0],
            "edu_insti_seq": identity[1],
            "open_course_seq": identity[2],
            "org_seq": identity[3],
            "source_status": source["source_status"],
            "source_application_period": (
                f"{source['apply_start']} {source['apply_start_time']}~"
                f"{source['apply_end']} {source['apply_end_time']}"
            ),
            "source_education_period": f"{source['start']}~{source['end']}",
            "detail_verified": True,
            "data_plane": "official_iframe_and_detail_html",
        },
    }


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "landing_requests": 0,
        "list_requests": 0,
        "list_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "unique_id_count": 0,
        "duplicate_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": DANYANG_MUNICIPALITY_CODE,
        "municipality_name": DANYANG_MUNICIPALITY_NAME,
        "canonical_candidate_id": DANYANG_CANONICAL_CANDIDATE_ID,
        "canonical_url": DANYANG_CANONICAL_URL,
        "ownership_alias_urls": list(DANYANG_ALIAS_URLS),
        "superseded_providers": sorted(DANYANG_ALIAS_PROVIDERS),
    }


def collect_danyang_lifelong_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 200,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = DANYANG_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future Danyang lifelong-course snapshot."""

    meta = _base_meta()
    if not is_danyang_target(target):
        meta["configured_collection_error"] = (
            "target is not the exact Danyang canonical provider/URL"
        )
        return [], DANYANG_PARSER, meta
    try:
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        workers = max(1, min(int(max_workers or 1), DANYANG_MAX_WORKERS))
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "collection limits are invalid"
        return [], DANYANG_PARSER, meta
    if page_cap < 1:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "max_pages cap excludes the source list"
        return [], DANYANG_PARSER, meta

    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    cutoff = _today(today)
    sessions: list[Any] = []
    errors: list[str] = []

    def new_session() -> Any:
        value = current_factory()
        sessions.append(value)
        return value

    base_session = new_session()
    listed: list[dict[str, Any]] = []
    fingerprint: tuple[Any, ...] = ()
    current_rows: list[dict[str, Any]] = []
    collected: list[dict[str, Any]] = []
    try:
        try:
            landing = _coerce_soup(
                current_fetcher(base_session, DANYANG_CANONICAL_URL, timeout)
            )
            meta["landing_requests"] = 1
            _validate_landing(landing)
            first = _coerce_soup(
                current_fetcher(base_session, DANYANG_LIST_URL, timeout)
            )
            meta["list_requests"] = 1
            listed, fingerprint = _parse_list(first)
            meta["pages"] = 1
        except Exception as exc:
            errors.append(f"list: {type(exc).__name__}: {_clean(exc)}")
        if errors:
            return [], DANYANG_PARSER, {
                **meta,
                "request_count": meta["landing_requests"] + meta["list_requests"],
                "configured_collection_error": "; ".join(errors),
            }

        meta["source_rows"] = len(listed)
        meta["unique_id_count"] = len({row["identity"] for row in listed})
        current_rows = [
            row for row in listed if date.fromisoformat(row["end"]) >= cutoff
        ]
        meta["current_count"] = len(current_rows)
        if detail_cap < len(current_rows):
            meta["source_cap_reached"] = True
            errors.append(
                f"detail_limit cap {detail_cap} is below required {len(current_rows)}"
            )

        if not errors and current_rows:
            meta["detail_attempts"] = len(current_rows)

            def fetch_detail(row: Mapping[str, Any]) -> dict[str, Any]:
                session = new_session()
                try:
                    soup = _coerce_soup(
                        current_fetcher(session, row["detail_url"], timeout)
                    )
                    return _parse_detail(soup, row, target)
                finally:
                    _close_quietly(session)

            future_to_row = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for row in current_rows:
                    future_to_row[pool.submit(fetch_detail, row)] = row
                by_identity: dict[tuple[str, str, str, str], dict[str, Any]] = {}
                for future in as_completed(future_to_row):
                    row = future_to_row[future]
                    try:
                        by_identity[row["identity"]] = future.result()
                        meta["detail_pages"] += 1
                    except Exception as exc:
                        errors.append(
                            f"detail {row['identity'][2]}: "
                            f"{type(exc).__name__}: {_clean(exc)}"
                        )
                collected = [
                    by_identity[row["identity"]]
                    for row in current_rows
                    if row["identity"] in by_identity
                ]

        try:
            recheck = _coerce_soup(
                current_fetcher(base_session, DANYANG_LIST_URL, timeout)
            )
            meta["list_requests"] += 1
            meta["list_rechecks"] = 1
            _, rechecked_fingerprint = _parse_list(recheck)
            if rechecked_fingerprint != fingerprint:
                raise DanyangContractError("course list changed during traversal")
        except Exception as exc:
            errors.append(f"recheck: {type(exc).__name__}: {_clean(exc)}")

        if not errors:
            try:
                deduped = list(current_dedupe(collected))
                if len(deduped) != len(collected):
                    raise DanyangContractError(
                        f"dedupe changed complete count {len(collected)} to {len(deduped)}"
                    )
                collected = deduped
            except Exception as exc:
                errors.append(f"dedupe: {type(exc).__name__}: {_clean(exc)}")

        details_complete = (
            not meta["source_cap_reached"]
            and meta["detail_attempts"] == len(current_rows)
            and meta["detail_pages"] == len(current_rows)
        )
        snapshot_complete = (
            not errors
            and meta["list_rechecks"] == 1
            and details_complete
            and len(collected) == len(current_rows)
        )
        if not snapshot_complete:
            collected = []
        meta.update(
            {
                "request_count": (
                    meta["landing_requests"]
                    + meta["list_requests"]
                    + meta["detail_attempts"]
                ),
                "returned_count": len(collected),
                "status_counts": dict(Counter(row["status"] for row in collected)),
                "pagination_complete": snapshot_complete,
                "details_complete": details_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": snapshot_complete,
                "network_concurrency": workers,
                "no_current_data": snapshot_complete and not current_rows,
                "no_current_reason": (
                    "the complete official iframe has no current/future courses"
                    if snapshot_complete and not current_rows
                    else ""
                ),
            }
        )
        if errors:
            meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return collected, DANYANG_PARSER, meta
    finally:
        for session in sessions:
            _close_quietly(session)


collect_danyang_target = collect_danyang_lifelong_courses
collect = collect_danyang_lifelong_courses


__all__ = [
    "DANYANG_ALIAS_PROVIDERS",
    "DANYANG_ALIAS_URLS",
    "DANYANG_BRANCH",
    "DANYANG_CANONICAL_CANDIDATE_ID",
    "DANYANG_CANONICAL_URL",
    "DANYANG_LIST_URL",
    "DANYANG_MUNICIPALITY_CODE",
    "DANYANG_MUNICIPALITY_NAME",
    "DANYANG_PARSER",
    "DANYANG_PROVIDER",
    "DanyangContractError",
    "collect",
    "collect_danyang_lifelong_courses",
    "collect_danyang_target",
    "is_danyang_target",
    "is_target",
]
