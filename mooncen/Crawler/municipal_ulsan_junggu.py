"""Complete official lifelong-learning collector for Ulsan Jung-gu.

The canonical list is a 15-row historical ledger. Every declared page, the
immediate empty sentinel, stable first/final boundaries, and every
current/future public detail are required before any row is returned.
Application, login, attachment, and applicant endpoints are never fetched.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


ULSAN_JUNGGU_PROVIDER = "MUNI_WWW_JUNGGU_ULSAN_KR_9703AC0F"
ULSAN_JUNGGU_HOST = "www.junggu.ulsan.kr"
ULSAN_JUNGGU_LIST_PATH = "/edu/onRequest/selectProgram.do"
ULSAN_JUNGGU_AGREE_PATH = "/edu/onRequest/agree.do"
ULSAN_JUNGGU_URL = f"https://{ULSAN_JUNGGU_HOST}{ULSAN_JUNGGU_LIST_PATH}"
ULSAN_JUNGGU_PAGE_SIZE = 15
ULSAN_JUNGGU_BRANCH = "울산 중구 평생학습관"
ULSAN_JUNGGU_BRANCH_CODE = "ULSAN_JUNGGU_LIFELONG"
ULSAN_JUNGGU_MUNICIPALITY_CODE = "3111000000"
ULSAN_JUNGGU_MUNICIPALITY_NAME = "울산광역시 중구"
ULSAN_JUNGGU_MAX_WORKERS = 6
ULSAN_JUNGGU_FETCH_ATTEMPTS = 3
ULSAN_JUNGGU_PARSER = (
    "ulsan_junggu_lifelong_complete_ordinal_pages+empty_sentinel+"
    "stable_boundaries+all_current_details+identity_bound_application_control+"
    "official_place_omission+pii_allowlist"
)

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"PRG_\d{16}")
_VIEW_RE = re.compile(r"fn_view\('(PRG_\d{16})'\);return false;")
_APPLY_RE = re.compile(r"fn_apply\('(PRG_\d{16})'\);")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_LIST_LABELS = (
    "번호",
    "분류",
    "강좌명",
    "접수기간",
    "교육기간",
    "수강료",
    "재료비",
    "현재 신청/ 대기인원",
    "현장신청",
    "신청",
)
_DETAIL_REQUIRED_LABELS = {
    "일반/특강",
    "강좌기관",
    "강좌명",
    "접수기간",
    "교육기간",
    "교육시간",
    "모집정원",
    "현재 신청/대기인원",
    "현장신청",
    "수강료",
}
_DETAIL_OPTIONAL_LABELS = {
    "교육대상",
    "교육장소",
    "강사명",
    "재료(교재)비",
    "강좌소개",
    "교육내용",
    "첨부파일",
}
_STATUS_MAP = {
    "신청중": "OPEN",
    "교육중": "CLOSED",
    "신청마감": "CLOSED",
    "교육종료": "CLOSED",
}
_STATUS_CLASSES = {
    "신청중": ("label", "label-danger"),
    "교육중": ("label", "label-warning"),
    "신청마감": ("label", "label-success"),
    "교육종료": ("label", "label-default"),
}

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[Iterable[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]


class UlsanJungguContractError(ValueError):
    """The official source no longer matches the audited public contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_ulsan_junggu_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != ULSAN_JUNGGU_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == ULSAN_JUNGGU_HOST
        and parsed.port is None
        and parsed.path == ULSAN_JUNGGU_LIST_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def ulsan_junggu_list_url(page: Any) -> str:
    if isinstance(page, bool):
        raise UlsanJungguContractError("page must be an integer")
    value = int(page)
    if value < 1:
        raise UlsanJungguContractError("page must be positive")
    query = urlencode(
        (
            ("exec", "list"),
            ("currentPage", str(value)),
            ("pagePerCount", str(ULSAN_JUNGGU_PAGE_SIZE)),
            ("eduCategory", ""),
            ("prgId", ""),
            ("eduState", ""),
            ("searchKey", ""),
        )
    )
    return f"{ULSAN_JUNGGU_URL}?{query}"


def ulsan_junggu_detail_url(identity: Any, page: Any = 1) -> str:
    course_id = _clean(identity)
    if not _IDENTITY_RE.fullmatch(course_id):
        raise UlsanJungguContractError("invalid program identity")
    if isinstance(page, bool):
        raise UlsanJungguContractError("page must be an integer")
    source_page = int(page)
    if source_page < 1:
        raise UlsanJungguContractError("page must be positive")
    query = urlencode(
        (
            ("exec", "view"),
            ("currentPage", str(source_page)),
            ("pagePerCount", str(ULSAN_JUNGGU_PAGE_SIZE)),
            ("eduCategory", ""),
            ("prgId", course_id),
            ("eduState", ""),
            ("searchKey", ""),
        )
    )
    return f"{ULSAN_JUNGGU_URL}?{query}"


def ulsan_junggu_application_url(identity: Any) -> str:
    course_id = _clean(identity)
    if not _IDENTITY_RE.fullmatch(course_id):
        raise UlsanJungguContractError("invalid program identity")
    return (
        f"https://{ULSAN_JUNGGU_HOST}{ULSAN_JUNGGU_AGREE_PATH}?"
        + urlencode((("prgId", course_id),))
    )


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_soup(response: Any, requested_url: str) -> BeautifulSoup:
    if isinstance(response, BeautifulSoup):
        return response
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise UlsanJungguContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise UlsanJungguContractError("redirected source response")
    final_url = _clean(getattr(response, "url", "")) or requested_url
    if final_url != requested_url:
        raise UlsanJungguContractError("source response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", "")
    if not content:
        raise UlsanJungguContractError("empty source response")
    soup = BeautifulSoup(content, "lxml")
    if soup.select_one("title") is None:
        raise UlsanJungguContractError("status-200 response is not official HTML")
    return soup


@dataclass
class _FetchResult:
    values: dict[Any, BeautifulSoup]
    errors: list[str]
    retries: int
    sessions: int


def _fetch_many(
    jobs: Sequence[tuple[Any, str]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    sleeper: Sleeper,
) -> _FetchResult:
    values: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    retries = 0
    sessions: list[Any] = []
    local = threading.local()
    lock = threading.Lock()

    def thread_session() -> Any:
        current = getattr(local, "session", None)
        if current is None:
            current = session_factory()
            local.session = current
            with lock:
                sessions.append(current)
        return current

    def one(job: tuple[Any, str]) -> tuple[Any, BeautifulSoup, int]:
        key, url = job
        messages: list[str] = []
        for attempt in range(1, ULSAN_JUNGGU_FETCH_ATTEMPTS + 1):
            try:
                response = fetcher(thread_session(), url, timeout)
                return key, _response_soup(response, url), attempt - 1
            except Exception as exc:
                messages.append(
                    f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}"
                )
                if attempt < ULSAN_JUNGGU_FETCH_ATTEMPTS:
                    sleeper(0.25 * (2 ** (attempt - 1)))
        raise UlsanJungguContractError("; ".join(messages))

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(one, job): job[0] for job in jobs}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    found_key, soup, item_retries = future.result()
                    values[found_key] = soup
                    retries += item_retries
                except Exception as exc:
                    errors.append(f"{key}: {type(exc).__name__}: {_clean(exc)}")
    finally:
        for current in sessions:
            _close_quietly(current)
    return _FetchResult(values, errors, retries, len(sessions))


def _form_page(soup: BeautifulSoup, requested_page: int) -> None:
    forms = soup.select("form#listForm")
    if len(forms) != 1:
        raise UlsanJungguContractError("expected one official list form")
    form = forms[0]
    action = urlparse(urljoin(ULSAN_JUNGGU_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "get"
        or action.scheme.lower() != "https"
        or (action.hostname or "").rstrip(".").lower() != ULSAN_JUNGGU_HOST
        or action.path != ULSAN_JUNGGU_LIST_PATH
    ):
        raise UlsanJungguContractError("official list form route changed")
    values = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[name]")
    }
    if (
        values.get("currentPage") != str(requested_page)
        or values.get("pagePerCount") != str(ULSAN_JUNGGU_PAGE_SIZE)
    ):
        raise UlsanJungguContractError("official list form page declaration changed")


def _dates(value: Any, label: str) -> tuple[str, str]:
    found = _DATE_RE.findall(_clean(value))
    if len(found) != 2:
        raise UlsanJungguContractError(f"{label} must expose two ISO dates")
    try:
        start, end = (date.fromisoformat(item) for item in found)
    except ValueError as exc:
        raise UlsanJungguContractError(f"{label} contains an invalid date") from exc
    if end < start:
        raise UlsanJungguContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _list_pairs(root: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    labels: list[str] = []
    for item in root.find_all("li", recursive=False):
        definition = item.find("dl", recursive=False)
        if definition is None:
            continue
        headings = definition.find_all("dt", recursive=False)
        values = definition.find_all("dd", recursive=False)
        if len(headings) != 1 or len(values) != 1:
            raise UlsanJungguContractError("malformed official list definition")
        label = _clean(headings[0].get_text(" ", strip=True))
        if not label or label in pairs:
            raise UlsanJungguContractError("duplicate or empty official list label")
        labels.append(label)
        pairs[label] = _clean(values[0].get_text(" ", strip=True))
    if tuple(labels) != _LIST_LABELS:
        raise UlsanJungguContractError("official list field vocabulary changed")
    return pairs


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    requested_page: int,
) -> list[dict[str, Any]]:
    _form_page(soup, requested_page)
    rows: list[dict[str, Any]] = []
    for root in soup.select("ul.inner_list"):
        link = root.select_one("a[onclick*='fn_view']")
        if link is None:
            continue
        pairs = _list_pairs(root)
        title = _clean(link.get_text(" ", strip=True))
        match = _VIEW_RE.fullmatch(_clean(link.get("onclick")))
        linked = urlparse(urljoin(ULSAN_JUNGGU_URL, _clean(link.get("href"))))
        status_node = link.find_previous_sibling("span")
        source_status = _clean(
            status_node.get_text(" ", strip=True) if status_node else ""
        )
        classes = tuple(status_node.get("class", ())) if status_node else ()
        if (
            match is None
            or not title
            or linked.scheme.lower() != "https"
            or (linked.hostname or "").rstrip(".").lower() != ULSAN_JUNGGU_HOST
            or linked.path != ULSAN_JUNGGU_LIST_PATH
            or linked.query
            or source_status not in _STATUS_MAP
            or classes != _STATUS_CLASSES[source_status]
        ):
            raise UlsanJungguContractError("official list identity/status route changed")
        ordinal_text = _clean(pairs["번호"])
        if not ordinal_text.isdigit():
            raise UlsanJungguContractError("official list ordinal is malformed")
        apply_start, apply_end = _dates(pairs["접수기간"], "application period")
        start_date, end_date = _dates(pairs["교육기간"], "education period")
        identity = match.group(1)
        fee = _clean(pairs["수강료"]) or "공식 페이지 요금 미기재"
        category = _clean(pairs["분류"]) or "공식 페이지 분야 미기재"
        row = {
            "provider": ULSAN_JUNGGU_PROVIDER,
            "provider_course_id": f"{ULSAN_JUNGGU_PROVIDER}:program:{identity}",
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "branch": ULSAN_JUNGGU_BRANCH,
            "branch_code": ULSAN_JUNGGU_BRANCH_CODE,
            "branch_url": ULSAN_JUNGGU_URL,
            "preserve_branch": True,
            "category": category,
            "raw_url": ulsan_junggu_detail_url(identity, requested_page),
            "status": _STATUS_MAP[source_status],
            "reservation_available": False,
            "fee": fee,
            "period": f"{start_date} ~ {end_date}",
            "apply_period": f"{apply_start} ~ {apply_end}",
            "schedule_raw": "공식 페이지 시간 미기재",
            "target": "공식 페이지 대상 미기재",
            "venue_name": "공식 페이지 장소 미기재",
            "material_fee": _clean(pairs["재료비"]),
            "capacity": _clean(pairs["현재 신청/ 대기인원"]),
            "description": title,
            "start_date": start_date,
            "end_date": end_date,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "program_type": "강좌",
            "collection_category": "평생학습",
            "domain_category": "교육·강좌",
            "source_group": "lifelong_learning",
            "operator_type": "지자체/공공기관",
            "collection_type": ULSAN_JUNGGU_PARSER,
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": ULSAN_JUNGGU_MUNICIPALITY_CODE,
            "municipality_full_name": ULSAN_JUNGGU_MUNICIPALITY_NAME,
            "raw_fields": {
                "parser": ULSAN_JUNGGU_PARSER,
                "source_program_id": identity,
                "source_ordinal": int(ordinal_text),
                "source_page": requested_page,
                "source_status": source_status,
                "source_category": category,
                "source_material_fee": _clean(pairs["재료비"]),
                "source_current_waiting": _clean(
                    pairs["현재 신청/ 대기인원"]
                ),
                "source_onsite_count": _clean(pairs["현장신청"]),
                "target_source_omission": True,
                "venue_source_omission": True,
                "schedule_source_omission": True,
                "application_form_fetched": False,
            },
        }
        rows.append(row)
    return rows


def _page_signature(rows: Sequence[dict[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            row["provider_course_id"],
            row["title"],
            row["period"],
            row["apply_period"],
            row["fee"],
            row["raw_fields"]["source_status"],
            row["raw_fields"]["source_ordinal"],
        )
        for row in rows
    )


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    tables = soup.select("table.table_view")
    if len(tables) != 1:
        raise UlsanJungguContractError("expected one official detail table")
    pairs: dict[str, str] = {}
    for tr in tables[0].select("tbody > tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) != 2:
            raise UlsanJungguContractError("official detail row schema changed")
        key = _clean(cells[0].get_text(" ", strip=True))
        value = _clean(cells[1].get_text(" ", strip=True))
        if not key or key in pairs:
            raise UlsanJungguContractError(
                "official detail labels are empty or duplicated"
            )
        pairs[key] = value
    labels = set(pairs)
    if (
        not _DETAIL_REQUIRED_LABELS.issubset(labels)
        or not labels.issubset(_DETAIL_REQUIRED_LABELS | _DETAIL_OPTIONAL_LABELS)
    ):
        raise UlsanJungguContractError("official detail field vocabulary changed")
    return pairs


def _fee_key(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _detail_row(
    parent: dict[str, Any],
    soup: BeautifulSoup,
    *,
    today: date,
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_program_id"))
    try:
        pairs = _detail_pairs(soup)
    except UlsanJungguContractError as exc:
        raise UlsanJungguContractError(f"detail {identity}: {exc}") from exc
    if _clean(pairs.get("강좌명")) != _clean(parent.get("title")):
        raise UlsanJungguContractError(f"detail {identity}: title mismatch")
    if _clean(pairs.get("일반/특강")) != _clean(parent.get("category")):
        raise UlsanJungguContractError(f"detail {identity}: category mismatch")
    detail_apply = _dates(pairs.get("접수기간"), "detail application period")
    detail_education = _dates(pairs.get("교육기간"), "detail education period")
    if detail_apply != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise UlsanJungguContractError(
            f"detail {identity}: application period mismatch"
        )
    if detail_education != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ):
        raise UlsanJungguContractError(
            f"detail {identity}: education period mismatch"
        )
    if _fee_key(pairs.get("수강료")) != _fee_key(parent.get("fee")):
        raise UlsanJungguContractError(f"detail {identity}: fee mismatch")

    controls = []
    for node in soup.select("button[onclick], a[onclick]"):
        onclick = _clean(node.get("onclick"))
        if "fn_apply" not in onclick:
            continue
        match = _APPLY_RE.fullmatch(onclick)
        if match is None or match.group(1) != identity:
            raise UlsanJungguContractError(
                f"detail {identity}: malformed application control"
            )
        controls.append(node)
    if len(controls) > 1:
        raise UlsanJungguContractError(
            f"detail {identity}: multiple application controls"
        )

    source_status = _clean(raw.get("source_status"))
    apply_start = date.fromisoformat(_clean(parent.get("apply_start")))
    apply_end = date.fromisoformat(_clean(parent.get("apply_end")))
    active = False
    if source_status == "신청중":
        if not controls:
            raise UlsanJungguContractError(
                f"detail {identity}: active course omitted application control"
            )
        active = True
    elif (
        source_status == "교육중"
        and controls
        and apply_start <= today <= apply_end
    ):
        active = True
    status = "OPEN" if active else "CLOSED"
    target = _clean(pairs.get("교육대상"))
    venue = _clean(pairs.get("교육장소"))
    schedule = _clean(pairs.get("교육시간"))
    fee = _clean(pairs.get("수강료")) or "공식 페이지 요금 미기재"
    result = dict(parent)
    result.update(
        {
            "status": status,
            "reservation_available": active,
            "application_type": (
                "ONLINE_RESERVATION" if active else "INFO_ONLY"
            ),
            "application_url": (
                ulsan_junggu_application_url(identity) if active else ""
            ),
            "fee": fee,
            "period": _clean(pairs.get("교육기간")),
            "apply_period": _clean(pairs.get("접수기간")),
            "schedule_raw": schedule or "공식 페이지 시간 미기재",
            "target": target or "공식 페이지 대상 미기재",
            "venue_name": venue or "공식 페이지 장소 미기재",
            "material_fee": _clean(pairs.get("재료(교재)비")),
            "capacity": _clean(pairs.get("현재 신청/대기인원")),
            "description": _clean(
                " / ".join(
                    (
                        _clean(parent.get("title")),
                        _clean(parent.get("category")),
                        _clean(pairs.get("강좌기관")),
                        _clean(pairs.get("교육기간")),
                        schedule,
                        target,
                        fee,
                    )
                )
            ),
        }
    )
    result["raw_fields"] = {
        **raw,
        "source_institution": _clean(pairs.get("강좌기관")),
        "source_capacity": _clean(pairs.get("모집정원")),
        "source_current_waiting": _clean(
            pairs.get("현재 신청/대기인원")
        ),
        "source_onsite_count": _clean(pairs.get("현장신청")),
        "source_detail_labels": list(pairs),
        "source_application_control": (
            _clean(controls[0].get_text(" ", strip=True)) if controls else ""
        ),
        "inactive_control_ignored": bool(controls and not active),
        "target_source_omission": not target,
        "venue_source_omission": not venue,
        "schedule_source_omission": not schedule,
        "detail_validated": True,
        "application_form_fetched": False,
        "login_endpoint_fetched": False,
        "attachment_fetched": False,
        "applicant_endpoint_fetched": False,
        "instructor_excluded": True,
        "free_text_excluded": True,
    }
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now().astimezone().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _default_dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "detail_pages": 0,
        "detail_attempts": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "source_total": 0,
        "data_pages": 0,
        "page_counts": {},
        "sentinel_page": 0,
        "sentinel_count": 0,
        "stable_rechecks": {},
        "current_count": 0,
        "expired_count": 0,
        "status_counts": {},
        "application_control_count": 0,
        "source_omission_counts": {},
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
    }


def collect_ulsan_junggu_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 140,
    detail_limit: int = 300,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = ULSAN_JUNGGU_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a fail-closed complete snapshot from the canonical owner."""

    meta = _base_meta()
    if not is_ulsan_junggu_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact canonical Ulsan Jung-gu owner"
        )
        return [], ULSAN_JUNGGU_PARSER, meta
    try:
        if any(
            isinstance(value, bool)
            for value in (timeout, max_pages, detail_limit, max_workers)
        ):
            raise ValueError
        request_timeout = max(1, int(timeout))
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        workers = min(
            max(1, int(max_workers)),
            ULSAN_JUNGGU_MAX_WORKERS,
        )
        cutoff = _today(today)
    except (TypeError, ValueError):
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], ULSAN_JUNGGU_PARSER, meta

    fetch = fetcher or _default_fetcher
    factory = session_factory or _default_session_factory
    first = _fetch_many(
        ((1, ulsan_junggu_list_url(1)),),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=1,
        sleeper=sleeper,
    )
    meta["network_requests"] += 1 + first.retries
    meta["network_retry_count"] += first.retries
    meta["sessions_created"] += first.sessions
    if first.errors or 1 not in first.values:
        meta["configured_collection_error"] = "; ".join(first.errors) or (
            "missing first list page"
        )
        return [], ULSAN_JUNGGU_PARSER, meta
    try:
        first_rows = _parse_list_page(first.values[1], requested_page=1)
        if not first_rows:
            raise UlsanJungguContractError(
                "official history unexpectedly has no rows"
            )
        total = int(first_rows[0]["raw_fields"]["source_ordinal"])
        last_page = math.ceil(total / ULSAN_JUNGGU_PAGE_SIZE)
        if total < 1 or last_page < 1:
            raise UlsanJungguContractError("invalid source total")
        if page_cap < last_page:
            meta["source_cap_reached"] = True
            raise UlsanJungguContractError(
                f"max_pages cap allows {page_cap} of {last_page} declared pages"
            )
    except Exception as exc:
        meta["configured_collection_error"] = f"first-page contract: {_clean(exc)}"
        return [], ULSAN_JUNGGU_PARSER, meta

    jobs = [
        (page, ulsan_junggu_list_url(page))
        for page in range(2, last_page + 2)
    ]
    remaining = _fetch_many(
        jobs,
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
    )
    meta["network_requests"] += len(jobs) + remaining.retries
    meta["network_retry_count"] += remaining.retries
    meta["sessions_created"] += remaining.sessions
    if remaining.errors or len(remaining.values) != len(jobs):
        meta["configured_collection_error"] = "; ".join(remaining.errors) or (
            "missing complete history/sentinel response"
        )
        return [], ULSAN_JUNGGU_PARSER, meta

    pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
    try:
        for page in range(2, last_page + 1):
            pages[page] = _parse_list_page(
                remaining.values[page],
                requested_page=page,
            )
        sentinel = _parse_list_page(
            remaining.values[last_page + 1],
            requested_page=last_page + 1,
        )
        if sentinel:
            raise UlsanJungguContractError(
                "immediate post-final sentinel is not empty"
            )
        rows = [
            row
            for page in range(1, last_page + 1)
            for row in pages[page]
        ]
        if len(rows) != total:
            raise UlsanJungguContractError(
                "parsed row count differs from declared ordinal total"
            )
        ordinals = [
            int(row["raw_fields"]["source_ordinal"])
            for row in rows
        ]
        if ordinals != list(range(total, 0, -1)):
            raise UlsanJungguContractError(
                "source ordinals contain a gap or reorder"
            )
        identities = [
            _clean(row["raw_fields"]["source_program_id"])
            for row in rows
        ]
        if len(identities) != len(set(identities)):
            raise UlsanJungguContractError(
                "source program identities are duplicated"
            )
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"complete history contract: {_clean(exc)}"
        )
        return [], ULSAN_JUNGGU_PARSER, meta

    recheck = _fetch_many(
        (
            ("first", ulsan_junggu_list_url(1)),
            ("last", ulsan_junggu_list_url(last_page)),
        ),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=2,
        sleeper=sleeper,
    )
    meta["network_requests"] += 2 + recheck.retries
    meta["network_retry_count"] += recheck.retries
    meta["sessions_created"] += recheck.sessions
    if recheck.errors or set(recheck.values) != {"first", "last"}:
        meta["configured_collection_error"] = "; ".join(recheck.errors) or (
            "missing stable boundary response"
        )
        return [], ULSAN_JUNGGU_PARSER, meta
    try:
        stable_first = _parse_list_page(
            recheck.values["first"],
            requested_page=1,
        )
        stable_last = _parse_list_page(
            recheck.values["last"],
            requested_page=last_page,
        )
        if (
            _page_signature(stable_first) != _page_signature(pages[1])
            or _page_signature(stable_last)
            != _page_signature(pages[last_page])
        ):
            raise UlsanJungguContractError(
                "first/final page changed on stability recheck"
            )
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"stability contract: {_clean(exc)}"
        )
        return [], ULSAN_JUNGGU_PARSER, meta

    current_rows = [
        row
        for row in rows
        if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
    ]
    if len(current_rows) > detail_cap:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            f"detail_limit cap allows {detail_cap} of "
            f"{len(current_rows)} required current/future details"
        )
        return [], ULSAN_JUNGGU_PARSER, meta

    detail_jobs = [
        (
            _clean(row["raw_fields"]["source_program_id"]),
            _clean(row["raw_url"]),
        )
        for row in current_rows
    ]
    details = _fetch_many(
        detail_jobs,
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
    )
    meta["network_requests"] += len(detail_jobs) + details.retries
    meta["network_retry_count"] += details.retries
    meta["sessions_created"] += details.sessions
    if details.errors or len(details.values) != len(detail_jobs):
        meta["configured_collection_error"] = "; ".join(details.errors) or (
            "missing one or more required current/future details"
        )
        return [], ULSAN_JUNGGU_PARSER, meta
    try:
        enriched = [
            _detail_row(
                row,
                details.values[
                    _clean(row["raw_fields"]["source_program_id"])
                ],
                today=cutoff,
            )
            for row in current_rows
        ]
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"detail contract: {_clean(exc)}"
        )
        return [], ULSAN_JUNGGU_PARSER, meta

    result = list((dedupe_rows or _default_dedupe)(enriched))
    if len(result) != len(enriched):
        meta["configured_collection_error"] = (
            f"dedupe changed complete row count {len(enriched)} "
            f"to {len(result)}"
        )
        return [], ULSAN_JUNGGU_PARSER, meta
    page_counts = {
        str(page): len(pages[page])
        for page in range(1, last_page + 1)
    }
    source_status_counts = Counter(
        _clean(row["raw_fields"]["source_status"])
        for row in rows
    )
    omission_counts = {
        "target": sum(
            bool(row["raw_fields"].get("target_source_omission"))
            for row in result
        ),
        "venue": sum(
            bool(row["raw_fields"].get("venue_source_omission"))
            for row in result
        ),
        "schedule": sum(
            bool(row["raw_fields"].get("schedule_source_omission"))
            for row in result
        ),
    }
    meta.update(
        {
            "pages": last_page + 3,
            "list_requests": last_page + 3,
            "required_list_requests": last_page + 3,
            "detail_pages": len(result),
            "detail_attempts": len(detail_jobs),
            "source_total": total,
            "data_pages": last_page,
            "page_counts": page_counts,
            "sentinel_page": last_page + 1,
            "sentinel_count": 1,
            "stable_rechecks": {"first": True, "last": True},
            "current_count": len(result),
            "expired_count": total - len(current_rows),
            "status_counts": dict(source_status_counts),
            "application_control_count": sum(
                bool(row.get("application_url"))
                for row in result
            ),
            "source_omission_counts": omission_counts,
            "pagination_detected": last_page > 1,
            "pagination_complete": True,
            "details_complete": True,
            "snapshot_complete": True,
            "no_current_data": not result,
            "no_current_reason": (
                "all published programs have ended"
                if not result
                else ""
            ),
        }
    )
    return result, ULSAN_JUNGGU_PARSER, meta


collect = collect_ulsan_junggu_courses


__all__ = [
    "ULSAN_JUNGGU_BRANCH",
    "ULSAN_JUNGGU_LIST_PATH",
    "ULSAN_JUNGGU_MUNICIPALITY_CODE",
    "ULSAN_JUNGGU_PARSER",
    "ULSAN_JUNGGU_PROVIDER",
    "ULSAN_JUNGGU_URL",
    "UlsanJungguContractError",
    "collect",
    "collect_ulsan_junggu_courses",
    "is_ulsan_junggu_target",
    "ulsan_junggu_application_url",
    "ulsan_junggu_detail_url",
    "ulsan_junggu_list_url",
]
