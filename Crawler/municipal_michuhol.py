"""Fail-closed collector for Michuhol-gu's integrated education catalogue.

The unfiltered official list is the canonical superset.  The ``organ_cd``
routes are only partitions of that list and one historical row whose former
institution is now blank is not present in any current partition.  Splitting
the source by institution would therefore lose data as well as create a second
provider for the lifelong-learning subset.

The server declares a total, returns thirty rows per page, and keeps accepting
page numbers after the declared end.  A snapshot is published only after every
declared page and an empty post-boundary sentinel have been read.  Historical
rows prove list completeness; only rows whose education end date is current or
future are detailed and returned.  Every returned row must match its detail
page, including its online, external-official, offline, or unavailable
application control.

Requests are deliberately sequential.  The origin was stable on a reused TLS
connection but intermittently timed out under parallel connections during the
live audit.  Detail requests rotate managed sessions below the shared HTTP
request-budget ceiling without introducing concurrency.
"""

from __future__ import annotations

import base64
import binascii
from collections import Counter
from datetime import date, datetime
import hashlib
import json
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


MICHUHOL_PROVIDER = "MUNI_WWW_MICHUHOL_GO_KR_06925037"
MICHUHOL_URL = "https://www.michuhol.go.kr/reserve/education_apply/list.do"
MICHUHOL_HOST = "www.michuhol.go.kr"
MICHUHOL_LIST_PATH = "/reserve/education_apply/list.do"
MICHUHOL_DETAIL_PATH = "/reserve/education_apply/step1.do"
MICHUHOL_APPLICATION_PATH = "/reserve/education_apply/step2.do"
MICHUHOL_EXTERNAL_APPLICATION_URL = (
    "https://michu1388.michuhol.go.kr/consult/personal.php"
)
MICHUHOL_PAGE_SIZE = 30
MICHUHOL_DETAIL_SESSION_LIMIT = 150
MICHUHOL_MUNICIPALITY_CODE = "2817700000"
MICHUHOL_MUNICIPALITY_NAME = "인천광역시 미추홀구"
MICHUHOL_PARSER = "michuhol_complete_pages+sentinel+current_detail"

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"\d+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_SOURCE_TOTAL_RE = re.compile(r"([\d,]+)\s*개")
_PAGE_CALL_RE = re.compile(
    r"\bfnList\(\s*\{\s*['\"]page['\"]\s*:\s*['\"](\d+)['\"]\s*\}\s*\)\s*;?"
)
_CAPACITY_RE = re.compile(
    r"^\s*([\d,]+)\s*/\s*([\d,]+)\s*/\s*([\d,]+)\s*$"
)
_TOTAL_CAPACITY_RE = re.compile(r"^\s*([\d,]+)\s*명\s*$")
_LIST_HEADERS = (
    "분류",
    "강좌명",
    "기관",
    "접수기간",
    "교육기간",
    "대상",
    "정원/예약/대기",
    "상태",
)
_DETAIL_REQUIRED = (
    "분류",
    "기관",
    "대상",
    "정원",
    "접수기간",
    "수강기간",
    "교육시간",
    "강의실",
    "문의처",
    "모집방법",
    "수강료",
)
_STATUS_MAP: Mapping[str, str] = {
    "신청하기": "OPEN",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "교육진행중": "CLOSED",
    "교육완료": "CLOSED",
}
_OFFLINE_MESSAGE = "오프라인 신청만 가능한 강좌입니다."


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).casefold()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_michuhol_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == MICHUHOL_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == MICHUHOL_HOST
        and parsed.port is None
        and parsed.path == MICHUHOL_LIST_PATH
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_michuhol_target


def michuhol_list_url(page: Any) -> str:
    raw_page = _clean(page)
    if not _IDENTITY_RE.fullmatch(raw_page) or int(raw_page) < 1:
        return ""
    if int(raw_page) == 1:
        return MICHUHOL_URL
    return f"https://{MICHUHOL_HOST}{MICHUHOL_LIST_PATH}?" + urlencode(
        {"page": int(raw_page)}
    )


def michuhol_detail_url(identity: Any) -> str:
    raw_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{MICHUHOL_HOST}{MICHUHOL_DETAIL_PATH}?" + urlencode(
        {"sq": raw_identity}
    )


def michuhol_application_url(identity: Any) -> str:
    raw_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(raw_identity):
        return ""
    return f"https://{MICHUHOL_HOST}{MICHUHOL_APPLICATION_PATH}?" + urlencode(
        {"edu_sq": raw_identity}
    )


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    return current_session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 200))
    if 300 <= status < 400:
        raise ValueError("HTTP redirects are not accepted")
    raise_for_status = getattr(value, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise ValueError("empty HTTP response")
    return BeautifulSoup(content, "lxml")


def _fetch(
    fetcher: Fetcher, current_session: Any, url: str, timeout: int
) -> BeautifulSoup:
    if not url:
        raise ValueError("empty fetch URL")
    return _coerce_soup(fetcher(current_session, url, timeout))


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            result.append(
                date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            )
        except ValueError:
            continue
    return result


def _date_range(value: Any) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if len(values) != 2 or values[1] < values[0]:
        return "", "", ""
    start, end = values
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _capacity(value: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    match = _CAPACITY_RE.fullmatch(_clean(value))
    if match is None:
        return None, None, None
    total, current, waitlist = (
        int(part.replace(",", "")) for part in match.groups()
    )
    if min(total, current, waitlist) < 0:
        return None, None, None
    return total, current, waitlist


def _single_query_value(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _safe_course_query(
    query: Mapping[str, list[str]],
    *,
    identity_name: str,
    identity: str,
    expected_page: Optional[int] = None,
) -> bool:
    allowed_names = {identity_name, "search", "backUrl"}
    if (
        identity_name not in query
        or not set(query).issubset(allowed_names)
        or any(len(values) != 1 for values in query.values())
        or _single_query_value(query, identity_name) != identity
    ):
        return False
    if "search" in query:
        search = _single_query_value(query, "search")
        if search not in {"", "state"}:
            try:
                payload = json.loads(
                    base64.b64decode(search, validate=True).decode("utf-8")
                )
                search_page = int(payload["page"])
            except (
                binascii.Error,
                KeyError,
                TypeError,
                ValueError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ):
                return False
            if (
                payload != {"page": str(search_page)}
                or search_page < 1
                or (expected_page is not None and search_page != expected_page)
            ):
                return False
    if "backUrl" in query and _single_query_value(query, "backUrl") != "list":
        return False
    return True


def _detail_identity(current_url: str, value: Any) -> tuple[str, str]:
    parsed = urlparse(urljoin(current_url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query_value(query, "sq")
    current_query = parse_qs(urlparse(current_url).query, keep_blank_values=True)
    current_page_text = _single_query_value(current_query, "page") or "1"
    expected_page = int(current_page_text) if _IDENTITY_RE.fullmatch(current_page_text) else 0
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != MICHUHOL_HOST
        or parsed.port is not None
        or parsed.path != MICHUHOL_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or not _IDENTITY_RE.fullmatch(identity)
        or not _safe_course_query(
            query,
            identity_name="sq",
            identity=identity,
            expected_page=expected_page,
        )
    ):
        return "", ""
    return identity, michuhol_detail_url(identity)


def _application_control(
    value: Any,
    identity: str,
    expected_page: Optional[int] = None,
) -> tuple[str, str]:
    if value is None:
        return "NONE", ""
    href = _clean(value.get("href"))
    onclick = _clean(value.get("onclick"))
    if href == "javascript:;" and _OFFLINE_MESSAGE in onclick:
        return "OFFLINE", ""
    parsed = urlparse(urljoin(MICHUHOL_URL, href))
    if (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == MICHUHOL_HOST
        and parsed.port is None
        and parsed.path == MICHUHOL_APPLICATION_PATH
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        query = parse_qs(parsed.query, keep_blank_values=True)
        application_identity = _single_query_value(query, "edu_sq")
        if (
            application_identity == identity
            and _safe_course_query(
                query,
                identity_name="edu_sq",
                identity=identity,
                expected_page=expected_page,
            )
        ):
            return "INTERNAL_ONLINE", michuhol_application_url(identity)
        return "INVALID", ""
    if parsed.geturl() == MICHUHOL_EXTERNAL_APPLICATION_URL:
        return "EXTERNAL_ONLINE", MICHUHOL_EXTERNAL_APPLICATION_URL
    return "INVALID", ""


def _source_total(soup: BeautifulSoup) -> int:
    values: list[int] = []
    for node in soup.select("span.b"):
        match = _SOURCE_TOTAL_RE.fullmatch(_clean(node.get_text(" ", strip=True)))
        if match:
            values.append(int(match.group(1).replace(",", "")))
    return values[0] if len(values) == 1 else 0


def _page_from_onclick(value: Any) -> int:
    match = _PAGE_CALL_RE.fullmatch(_clean(value))
    return int(match.group(1)) if match else 0


def _pagination_contract(
    soup: BeautifulSoup, *, requested_page: int, source_pages: int, sentinel: bool
) -> bool:
    pagers = soup.select("div.paging")
    if len(pagers) != 1:
        return False
    controls = pagers[0].select("[onclick]")
    pages = [_page_from_onclick(node.get("onclick")) for node in controls]
    if not pages or any(page < 1 or page > source_pages for page in pages):
        return False
    if max(pages) != source_pages:
        return False
    active = pagers[0].select("strong.paging-link.active[onclick]")
    if sentinel:
        return not active
    return len(active) == 1 and _page_from_onclick(active[0].get("onclick")) == requested_page


def _stable_branch_code(branch: str) -> str:
    digest = hashlib.sha1(
        f"{MICHUHOL_PROVIDER}|{_normalized(branch)}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"MICHUHOL_BRANCH_{digest}"


def _parse_list_page(
    target: Any, soup: BeautifulSoup, *, page: int
) -> tuple[list[dict[str, Any]], int]:
    tables = soup.select("table.c-table-s1")
    matching = []
    for table in tables:
        headers = tuple(
            _clean(cell.get_text(" ", strip=True))
            for cell in table.select("thead th")
        )
        if headers == _LIST_HEADERS:
            matching.append(table)
    if len(matching) != 1:
        return [], 1

    parsed_rows: list[dict[str, Any]] = []
    malformed = 0
    current_url = michuhol_list_url(page)
    for tr in matching[0].select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        detail_link = tr.select_one("a[href*='step1.do']")
        if detail_link is None:
            malformed += 1
            continue
        identity, raw_url = _detail_identity(
            current_url, detail_link.get("href")
        )
        title = _clean(detail_link.get_text(" ", strip=True))
        application_dates = _date_tokens(values[3] if len(values) > 3 else "")
        application_period_valid = (
            len(application_dates) == 2
            and application_dates[1] >= application_dates[0]
        )
        apply_start = (
            application_dates[0].isoformat() if len(application_dates) == 2 else ""
        )
        apply_end = (
            application_dates[1].isoformat() if len(application_dates) == 2 else ""
        )
        apply_period = (
            f"{apply_start} ~ {apply_end}" if application_period_valid else ""
        )
        start, end, period = _date_range(values[4] if len(values) > 4 else "")
        capacity_total, capacity_current, waitlist_current = _capacity(
            values[6] if len(values) > 6 else ""
        )
        raw_status = values[7] if len(values) > 7 else ""
        application_anchor = cells[7].find("a") if len(cells) > 7 else None
        application_mode, application_url = _application_control(
            application_anchor,
            identity,
            expected_page=page,
        )
        status_has_application = raw_status == "신청하기"
        if (
            len(values) != len(_LIST_HEADERS)
            or not identity
            or not raw_url
            or not title
            or len(application_dates) != 2
            or not start
            or not end
            or capacity_total is None
            or capacity_current is None
            or waitlist_current is None
            or raw_status not in _STATUS_MAP
            or application_mode == "INVALID"
            or (status_has_application and application_mode == "NONE")
            or (not status_has_application and application_mode != "NONE")
        ):
            malformed += 1
            continue

        institution = values[2]
        branch = (
            institution
            or _clean(_target_value(target, "branch"))
            or MICHUHOL_MUNICIPALITY_NAME
        )
        row: dict[str, Any] = {
            "provider": MICHUHOL_PROVIDER,
            "provider_course_id": f"{MICHUHOL_PROVIDER}:education:{identity}"[:100],
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "program_type": "교육·강좌",
            "category": values[0],
            "branch": branch,
            "branch_code": _stable_branch_code(branch),
            "branch_url": MICHUHOL_URL,
            "preserve_branch": True,
            "raw_url": raw_url,
            "status": _STATUS_MAP[raw_status],
            "period": period,
            "start_date": start,
            "end_date": end,
            "target": values[5],
            "capacity": values[6],
            "capacity_total": capacity_total,
            "capacity_current": capacity_current,
            "waitlist_current": waitlist_current,
            "reservation_available": application_mode in {
                "INTERNAL_ONLINE",
                "EXTERNAL_ONLINE",
            },
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": MICHUHOL_MUNICIPALITY_CODE,
            "municipality_full_name": MICHUHOL_MUNICIPALITY_NAME,
            "collection_type": "complete_ssr_pages+sentinel+current_detail",
            "description": _clean(" ".join(values)),
            "raw_fields": {
                "parser": MICHUHOL_PARSER,
                "identity": identity,
                "source_page": page,
                "source_status": raw_status,
                "list_category": values[0],
                "list_institution": institution,
                "list_target": values[5],
                "list_capacity": values[6],
                "application_mode": application_mode,
                "application_period_valid": application_period_valid,
                "list_cells": values,
            },
        }
        if application_period_valid:
            row.update(
                {
                    "apply_period": apply_period,
                    "apply_start": apply_start,
                    "apply_end": apply_end,
                }
            )
        else:
            row["raw_fields"].update(
                {
                    "invalid_apply_start": apply_start,
                    "invalid_apply_end": apply_end,
                }
            )
        if application_url:
            row["application_url"] = application_url
            row["application_type"] = "ONLINE_RESERVATION"
        elif application_mode == "OFFLINE":
            row["application_type"] = "OFFLINE_RESERVATION"
        parsed_rows.append(row)
    return parsed_rows, malformed


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    containers = soup.select("div.detailinfo")
    if len(containers) != 1:
        return {}
    result: dict[str, str] = {}
    for dl in containers[0].select("dl"):
        heading = dl.find("dt")
        value = dl.find("dd")
        if heading is None or value is None:
            continue
        key = _clean(heading.get_text(" ", strip=True)).rstrip(":")
        if key and key not in result:
            result[key] = _clean(value.get_text(" ", strip=True))
    return result


def _detail_description(soup: BeautifulSoup) -> str:
    containers = soup.select("div.classcon")
    if len(containers) != 1:
        return ""
    first = containers[0].find("div", recursive=False)
    return _clean(first.get_text(" ", strip=True)) if first else ""


def _validate_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    raw = row.get("raw_fields", {})
    identity = _clean(raw.get("identity"))
    errors: list[str] = []
    headings = soup.select("div.detailinfo div.infohead")
    detail_title = (
        _normalized(headings[0].get_text(" ", strip=True))
        if len(headings) == 1
        else ""
    )
    if len(headings) != 1 or detail_title != _normalized(row.get("title")):
        errors.append(f"{identity}: detail title mismatch")
    pairs = _detail_pairs(soup)
    if any(key not in pairs or not _clean(pairs[key]) for key in _DETAIL_REQUIRED):
        errors.append(f"{identity}: detail fields are incomplete")
        return errors

    detail_category = _clean(pairs.get("분류"))
    detail_dong = _clean(pairs.get("동 이름"))
    list_category = _clean(raw.get("list_category"))
    resident_category_layout = bool(
        _normalized(detail_category) == _normalized("주민자치교육")
        and _normalized(detail_dong) == _normalized(list_category)
    )
    if (
        _normalized(detail_category) != _normalized(list_category)
        and not resident_category_layout
    ):
        errors.append(f"{identity}: detail/list category mismatch")
    if _normalized(pairs.get("기관")) != _normalized(raw.get("list_institution")):
        errors.append(f"{identity}: detail/list institution mismatch")

    detail_course_target = _clean(pairs.get("교육대상"))
    if (
        detail_course_target
        and _normalized(detail_course_target)
        != _normalized(raw.get("list_target"))
    ):
        errors.append(f"{identity}: detail/list target mismatch")

    detail_apply_start, detail_apply_end, detail_apply_period = _date_range(
        pairs.get("접수기간")
    )
    detail_start, detail_end, detail_period = _date_range(pairs.get("수강기간"))
    if (
        detail_apply_start != _clean(row.get("apply_start"))
        or detail_apply_end != _clean(row.get("apply_end"))
    ):
        errors.append(f"{identity}: detail/list application period mismatch")
    if (
        detail_start != _clean(row.get("start_date"))
        or detail_end != _clean(row.get("end_date"))
    ):
        errors.append(f"{identity}: detail/list education period mismatch")

    capacity_match = _TOTAL_CAPACITY_RE.fullmatch(_clean(pairs.get("정원")))
    detail_capacity = (
        int(capacity_match.group(1).replace(",", "")) if capacity_match else None
    )
    if detail_capacity != row.get("capacity_total"):
        errors.append(f"{identity}: detail/list capacity mismatch")

    controls = soup.select("div.btn-wrap a[title='신청하기']")
    if len(controls) > 1:
        errors.append(f"{identity}: multiple detail application controls")
        detail_mode, detail_application_url = "INVALID", ""
    else:
        detail_mode, detail_application_url = _application_control(
            controls[0] if controls else None, identity
        )
    list_mode = _clean(raw.get("application_mode"))
    if detail_mode != list_mode:
        errors.append(f"{identity}: detail/list application mode mismatch")
    if detail_application_url != _clean(row.get("application_url")):
        errors.append(f"{identity}: detail/list application URL mismatch")
    if errors:
        return errors

    institution = _clean(pairs.get("기관"))
    dong = _clean(pairs.get("동 이름"))
    branch = dong or institution
    if not branch:
        return [f"{identity}: detail branch is missing"]
    room = _clean(pairs.get("강의실"))
    description = _detail_description(soup)
    row.update(
        {
            "category": _clean(pairs.get("분류")),
            "branch": branch,
            "branch_code": _stable_branch_code(branch),
            "period": detail_period,
            "apply_period": detail_apply_period,
            "schedule_raw": _clean(
                " ".join(
                    part
                    for part in (pairs.get("수강기간"), pairs.get("교육시간"))
                    if _clean(part)
                )
            ),
            "target": detail_course_target or _clean(row.get("target")),
            "eligibility_raw": _clean(pairs.get("대상")),
            "room": room,
            "venue_name": room,
            "phone": _clean(pairs.get("문의처")),
            "contact": _clean(pairs.get("문의처")),
            "fee": _clean(pairs.get("수강료")),
            "application_method_raw": _clean(pairs.get("모집방법")),
        }
    )
    material_fee = _clean(pairs.get("재료비"))
    if material_fee:
        row["material_fee"] = material_fee
    if description:
        row["description"] = description
    raw["detail_pairs"] = pairs
    raw["resident_category_layout"] = resident_category_layout
    raw["detail_application_mode"] = detail_mode
    raw["detail_valid"] = True
    return []


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("branch")),
        _normalized(row.get("period")),
        _normalized(row.get("schedule_raw")),
    )


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "detail_pages": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "current_count": 0,
        "returned_count": 0,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_michuhol_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 60,
    detail_limit: int = 300,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Michuhol education snapshot."""

    if not is_michuhol_target(target):
        return [], MICHUHOL_PARSER, _failure(
            "target does not match the canonical unfiltered Michuhol route"
        )
    if fetcher is None or session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], MICHUHOL_PARSER, _failure(
                "managed fetcher and session_factory injection are required"
            )
        fetcher = fetcher or _default_fetcher
        session_factory = session_factory or _default_session_factory
    assert fetcher is not None
    assert session_factory is not None

    cutoff = _today(today)
    allowed_pages = max(0, int(max_pages))
    allowed_details = max(0, int(detail_limit))
    errors: list[str] = []
    all_rows: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    source_total = 0
    source_pages = 0
    required_list_requests = 0
    list_requests = 0
    malformed_count = 0
    duplicate_count = 0
    duplicate_url_count = 0
    semantic_duplicate_count = 0
    expired_count = 0
    historical_application_period_defect_count = 0
    source_cap_reached = False
    list_complete = False
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    details_complete = False
    sessions_created = 0
    current_session: Any = None

    try:
        if allowed_pages < 1:
            source_cap_reached = True
            errors.append("max_pages cap cannot inspect the first official page")
        else:
            current_session = session_factory()
            sessions_created += 1
            first = _fetch(fetcher, current_session, MICHUHOL_URL, timeout)
            list_requests = 1
            source_total = _source_total(first)
            source_pages = math.ceil(source_total / MICHUHOL_PAGE_SIZE) if source_total else 0
            required_list_requests = source_pages + 1 if source_pages else 0
            first_rows, malformed = _parse_list_page(target, first, page=1)
            malformed_count += malformed
            page_counts[1] = len(first_rows)
            if not source_total or not source_pages:
                errors.append("first page does not expose one positive official source total")
            elif not _pagination_contract(
                first, requested_page=1, source_pages=source_pages, sentinel=False
            ):
                errors.append("first-page pagination contract is malformed")
            expected_first_count = min(MICHUHOL_PAGE_SIZE, source_total)
            if malformed or len(first_rows) != expected_first_count:
                errors.append("first page row count or row contract mismatch")
            all_rows.extend(first_rows)

            if required_list_requests > allowed_pages:
                source_cap_reached = True
                errors.append(
                    f"max_pages cap allows {allowed_pages} of "
                    f"{required_list_requests} required list requests"
                )
            elif source_pages:
                for page in range(2, source_pages + 1):
                    soup = _fetch(
                        fetcher,
                        current_session,
                        michuhol_list_url(page),
                        timeout,
                    )
                    list_requests += 1
                    page_rows, malformed = _parse_list_page(target, soup, page=page)
                    malformed_count += malformed
                    page_counts[page] = len(page_rows)
                    expected_count = min(
                        MICHUHOL_PAGE_SIZE,
                        source_total - (page - 1) * MICHUHOL_PAGE_SIZE,
                    )
                    if malformed or len(page_rows) != expected_count:
                        errors.append(f"page {page}: row count or row contract mismatch")
                    if not _pagination_contract(
                        soup,
                        requested_page=page,
                        source_pages=source_pages,
                        sentinel=False,
                    ):
                        errors.append(f"page {page}: pagination contract mismatch")
                    all_rows.extend(page_rows)

                sentinel_page = source_pages + 1
                sentinel = _fetch(
                    fetcher,
                    current_session,
                    michuhol_list_url(sentinel_page),
                    timeout,
                )
                list_requests += 1
                sentinel_rows, sentinel_malformed = _parse_list_page(
                    target, sentinel, page=sentinel_page
                )
                malformed_count += sentinel_malformed
                page_counts[sentinel_page] = len(sentinel_rows)
                if sentinel_rows or sentinel_malformed:
                    errors.append("post-boundary sentinel page is not empty")
                if not _pagination_contract(
                    sentinel,
                    requested_page=sentinel_page,
                    source_pages=source_pages,
                    sentinel=True,
                ):
                    errors.append("post-boundary sentinel pagination contract mismatch")
    except Exception as exc:
        errors.append(f"list fetch {type(exc).__name__}")
    finally:
        _close_quietly(current_session)
        current_session = None

    identities = [_clean(row.get("raw_fields", {}).get("identity")) for row in all_rows]
    raw_urls = [_clean(row.get("raw_url")) for row in all_rows]
    duplicate_count = len(identities) - len(set(identities))
    duplicate_url_count = len(raw_urls) - len(set(raw_urls))
    if duplicate_count:
        errors.append(f"{duplicate_count} duplicate course identities")
    if duplicate_url_count:
        errors.append(f"{duplicate_url_count} duplicate canonical detail URLs")
    if source_total and len(all_rows) != source_total:
        errors.append(f"source declared {source_total}, parsed {len(all_rows)}")

    for row in all_rows:
        try:
            end = date.fromisoformat(_clean(row.get("end_date")))
        except ValueError:
            errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
            continue
        if end < cutoff:
            expired_count += 1
            if not bool(
                row.get("raw_fields", {}).get("application_period_valid")
            ):
                historical_application_period_defect_count += 1
        else:
            current_rows.append(row)
            if not bool(
                row.get("raw_fields", {}).get("application_period_valid")
            ):
                errors.append(
                    f"{_clean(row.get('provider_course_id'))}: "
                    "current/future application period is reversed"
                )

    list_complete = (
        not errors
        and source_total > 0
        and list_requests == required_list_requests
        and len(all_rows) == source_total
        and not duplicate_count
        and not duplicate_url_count
    )
    required_details = len(current_rows)
    if allowed_details < required_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} required details"
        )
    elif list_complete:
        detail_session_requests = MICHUHOL_DETAIL_SESSION_LIMIT
        try:
            for row in current_rows:
                if detail_session_requests >= MICHUHOL_DETAIL_SESSION_LIMIT:
                    _close_quietly(current_session)
                    current_session = session_factory()
                    sessions_created += 1
                    detail_session_requests = 0
                detail_attempts += 1
                detail_session_requests += 1
                identity = _clean(row.get("raw_fields", {}).get("identity"))
                try:
                    detail = _fetch(
                        fetcher,
                        current_session,
                        _clean(row.get("raw_url")),
                        timeout,
                    )
                    item_errors = _validate_detail(row, detail)
                    if item_errors:
                        detail_errors.extend(item_errors)
                    else:
                        detail_pages += 1
                except Exception as exc:
                    detail_errors.append(
                        f"{identity}: detail fetch {type(exc).__name__}"
                    )
        except Exception as exc:
            detail_errors.append(f"detail session {type(exc).__name__}")
        finally:
            _close_quietly(current_session)
            current_session = None

    errors.extend(detail_errors)
    if not detail_errors and detail_pages == required_details:
        semantic_keys = [_semantic_key(row) for row in current_rows]
        semantic_duplicate_count = len(semantic_keys) - len(set(semantic_keys))
        if semantic_duplicate_count:
            errors.append(
                f"{semantic_duplicate_count} semantic duplicate current courses"
            )

    details_complete = (
        list_complete
        and detail_attempts == required_details
        and detail_pages == required_details
        and not detail_errors
        and not semantic_duplicate_count
    )
    cleaned = [_clean_row(row) for row in current_rows]
    dedupe = dedupe_rows or _dedupe_default
    if list_complete and details_complete:
        try:
            deduped = list(dedupe(cleaned))
        except Exception as exc:
            errors.append(f"dedupe failed {type(exc).__name__}")
            deduped = []
        if len(deduped) != len(cleaned):
            errors.append(
                f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}"
            )
        cleaned = deduped

    snapshot_complete = list_complete and details_complete and not errors
    if not snapshot_complete:
        cleaned = []

    status_counts = Counter(_clean(row.get("status")) for row in current_rows)
    branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
    source_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in all_rows
    )
    application_mode_counts = Counter(
        _clean(row.get("raw_fields", {}).get("application_mode"))
        for row in current_rows
    )
    application_urls = [
        _clean(row.get("application_url"))
        for row in current_rows
        if _clean(row.get("application_url"))
    ]
    meta: dict[str, Any] = {
        "pages": list_requests,
        "list_requests": list_requests,
        "required_list_requests": required_list_requests,
        "max_pages": allowed_pages,
        "page_unit": MICHUHOL_PAGE_SIZE,
        "source_total": source_total,
        "source_pages": source_pages,
        "sentinel_page": source_pages + 1 if source_pages else 0,
        "page_counts": page_counts,
        "sessions_created": sessions_created,
        "discovered_links": len(set(identities)),
        "malformed_count": malformed_count,
        "duplicate_count": duplicate_count,
        "duplicate_url_count": duplicate_url_count,
        "semantic_duplicate_count": semantic_duplicate_count,
        "expired_count": expired_count,
        "historical_application_period_defect_count": historical_application_period_defect_count,
        "current_count": len(current_rows),
        "returned_count": len(cleaned),
        "required_detail_count": required_details,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_pages,
        "detail_errors": len(detail_errors),
        "pagination_detected": source_pages > 1,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "status_counts": dict(status_counts),
        "source_status_counts": dict(source_status_counts),
        "branch_count": len(branch_counts),
        "branch_counts": dict(branch_counts),
        "application_mode_counts": dict(application_mode_counts),
        "reservation_discovery_links": len(application_urls),
        "application_url_duplicate_count": len(application_urls)
        - len(set(application_urls)),
        "no_current_data": snapshot_complete and not current_rows,
        "no_current_reason": (
            "all official Michuhol education rows have ended"
            if snapshot_complete and not current_rows
            else ""
        ),
    }
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
    return cleaned, MICHUHOL_PARSER, meta


collect_michuhol_target = collect_michuhol_education_courses


__all__ = [
    "MICHUHOL_APPLICATION_PATH",
    "MICHUHOL_DETAIL_PATH",
    "MICHUHOL_EXTERNAL_APPLICATION_URL",
    "MICHUHOL_HOST",
    "MICHUHOL_LIST_PATH",
    "MICHUHOL_MUNICIPALITY_CODE",
    "MICHUHOL_MUNICIPALITY_NAME",
    "MICHUHOL_PAGE_SIZE",
    "MICHUHOL_PARSER",
    "MICHUHOL_PROVIDER",
    "MICHUHOL_URL",
    "collect_michuhol_education_courses",
    "collect_michuhol_target",
    "is_michuhol_target",
    "is_target",
    "michuhol_application_url",
    "michuhol_detail_url",
    "michuhol_list_url",
]
