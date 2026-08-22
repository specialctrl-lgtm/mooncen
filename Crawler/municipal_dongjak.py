"""Fail-closed collector for Dongjak-gu integrated reservation education.

The Dongjak reservation application uses the same controller for several
templates.  ``tmplatSeCd=91`` is therefore part of the ownership boundary: if
it is omitted, the response silently mixes education with other reservation
types and expands from the education-only history to a much larger result set.

This module deliberately has no dependency on ``Crawler_MunicipalYaml``.  The
main crawler can inject its managed HTTP session, fetch helper and row
deduplicator when the provider is promoted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


DONGJAK_EDUCATION_PROVIDER = "MUNI_WWW_DONGJAK_GO_KR_25A73CFC"
DONGJAK_EDUCATION_URL = (
    "https://www.dongjak.go.kr/yeyak/progrm/master/yeyak/list.do"
    "?menuNo=1600007&tmplatSeCd=91"
)
DONGJAK_HOST = "www.dongjak.go.kr"
DONGJAK_LIST_PATH = "/yeyak/progrm/master/yeyak/list.do"
DONGJAK_DETAIL_PATH = "/yeyak/progrm/master/yeyak/view.do"
DONGJAK_APPLICATION_PATH = "/yeyak/progrm/reqst/yeyak/forInsertConfirm.do"
DONGJAK_TEMPLATE_CODE = "91"
DONGJAK_MENU_NO = "1600007"
DONGJAK_PAGE_SIZE = 8
DONGJAK_MAX_WORKERS = 8
DONGJAK_MUNICIPALITY_CODE = "1159000000"
DONGJAK_MUNICIPALITY_NAME = "서울특별시 동작구"
DONGJAK_PARSER = (
    "dongjak_integrated_education_complete_pages+bounded_snapshot_retry+"
    "detail+required_field_provenance"
)

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RANGE_RE = re.compile(
    r"(?<!\d)(\d{4})[-./](\d{1,2})[-./](\d{1,2})\s*[~∼]\s*"
    r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?!\d)"
)
_FULL_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*"
    r"(\d{1,2})\s*[.일]?"
)
_PROGRAM_ID_RE = re.compile(r"\d+")

_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "대기": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "인원마감": "CLOSED",
    "마감": "CLOSED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def is_dongjak_education_target(target: Any) -> bool:
    """Accept only the provider-owned education-template canonical URL."""

    return (
        _provider(target) == DONGJAK_EDUCATION_PROVIDER
        and _target_url(target) == DONGJAK_EDUCATION_URL
    )


is_target = is_dongjak_education_target


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": DONGJAK_EDUCATION_URL,
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not getattr(response, "content", b""):
        raise ValueError("empty HTTP response")
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
        raise TypeError("fetcher did not return HTML or an HTTP response")
    return BeautifulSoup(content, "lxml")


def _fetch(
    fetcher: Fetcher, current_session: Any, url: str, timeout: int
) -> BeautifulSoup:
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


def dongjak_list_url(page_no: int = 1) -> str:
    page = max(1, int(page_no))
    query = urlencode(
        (
            ("tmplatSeCd", DONGJAK_TEMPLATE_CODE),
            ("menuNo", DONGJAK_MENU_NO),
            ("useAt", "Y"),
            ("pageIndex", str(page)),
        )
    )
    return f"https://{DONGJAK_HOST}{DONGJAK_LIST_PATH}?{query}"


def dongjak_detail_url(program_id: str) -> str:
    identity = _clean(program_id)
    if not _PROGRAM_ID_RE.fullmatch(identity):
        return ""
    query = urlencode(
        (
            ("prgSn", identity),
            ("tmplatSeCd", DONGJAK_TEMPLATE_CODE),
            ("menuNo", DONGJAK_MENU_NO),
            ("useAt", "Y"),
        )
    )
    return f"https://{DONGJAK_HOST}{DONGJAK_DETAIL_PATH}?{query}"


def _query_value(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _program_identity_from_link(value: Any) -> str:
    parsed = urlparse(urljoin(DONGJAK_EDUCATION_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _query_value(query, "prgSn")
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() != DONGJAK_HOST
        or parsed.path != DONGJAK_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or set(query) - {"prgSn", "tmplatSeCd", "menuNo", "useAt", "pageIndex"}
        or not _PROGRAM_ID_RE.fullmatch(identity)
        or _query_value(query, "tmplatSeCd") != DONGJAK_TEMPLATE_CODE
        or _query_value(query, "menuNo") != DONGJAK_MENU_NO
        or _query_value(query, "useAt") != "Y"
    ):
        return ""
    page_index = _query_value(query, "pageIndex")
    if page_index and not page_index.isdigit():
        return ""
    return identity


def _page_contract(soup: BeautifulSoup) -> Optional[tuple[int, int]]:
    active = soup.select_one(".paginationSet em[title='현재목록'] span")
    end = soup.select_one(".paginationSet a[title='마지막 목록'][href]")
    if active is None or end is None:
        return None
    active_text = _clean(active.get_text(" ", strip=True))
    parsed = urlparse(urljoin(DONGJAK_EDUCATION_URL, _clean(end.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    last_text = _query_value(query, "pageIndex")
    if (
        not active_text.isdigit()
        or not last_text.isdigit()
        or parsed.netloc.lower() != DONGJAK_HOST
        or parsed.path != DONGJAK_LIST_PATH
        or _query_value(query, "tmplatSeCd") != DONGJAK_TEMPLATE_CODE
        or _query_value(query, "menuNo") != DONGJAK_MENU_NO
        or _query_value(query, "useAt") != "Y"
    ):
        return None
    current_page = int(active_text)
    last_page = int(last_text)
    if current_page < 1 or last_page < current_page:
        return None
    return current_page, last_page


def _range_from_text(value: Any) -> tuple[str, str, str]:
    match = _DATE_RANGE_RE.search(_clean(value))
    if match is None:
        return "", "", ""
    try:
        start = date(*(int(part) for part in match.groups()[:3]))
        end = date(*(int(part) for part in match.groups()[3:]))
    except ValueError:
        return "", "", ""
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _list_periods(card: Any) -> tuple[tuple[str, str, str], tuple[str, str, str]]:
    apply_range = ("", "", "")
    operation_range = ("", "", "")
    for node in card.select(".details li"):
        text = _clean(node.get_text(" ", strip=True))
        parsed = _range_from_text(text)
        if not parsed[0]:
            continue
        if text.startswith("신청"):
            if apply_range[0]:
                return ("", "", ""), ("", "", "")
            apply_range = parsed
        elif not operation_range[0]:
            operation_range = parsed
        else:
            return ("", "", ""), ("", "", "")
    return apply_range, operation_range


def _label_value(card: Any, label: str) -> str:
    for node in card.select(".details li"):
        marker = node.find("b")
        if marker is None or _clean(marker.get_text(" ", strip=True)) != label:
            continue
        clone = BeautifulSoup(str(node), "lxml")
        copied_marker = clone.find("b")
        if copied_marker is not None:
            copied_marker.decompose()
        return _clean(clone.get_text(" ", strip=True))
    return ""


def _stable_branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"DONGJAK_BRANCH_{digest}"


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _base_row(target: Any, identity: str, title: str) -> dict[str, Any]:
    provider = _provider(target)
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:prg:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "program_type": "강좌",
        "category": "교육·강좌",
        "raw_url": dongjak_detail_url(identity),
        "reservation_available": False,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "municipality_code": DONGJAK_MUNICIPALITY_CODE,
        "municipality_full_name": DONGJAK_MUNICIPALITY_NAME,
        "collection_type": "complete_paginated_cards+detail_html",
        "raw_fields": {"parser": DONGJAK_PARSER, "program_id": identity},
    }


def _parse_list_page(
    target: Any, soup: BeautifulSoup
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    cards = soup.select(".card-list > li")
    for card_index, card in enumerate(cards, start=1):
        link = card.select_one(
            f"a[href*='{DONGJAK_DETAIL_PATH}'][href*='prgSn=']"
        )
        title_node = card.select_one(".info-desc .title")
        identity = _program_identity_from_link(link.get("href") if link else "")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        status_nodes = [
            _clean(node.get_text(" ", strip=True))
            for node in card.select(".info-desc .status span")
        ]
        source_status = next(
            (value for value in status_nodes if value in _STATUS_MAP), ""
        )
        apply_range, operation_range = _list_periods(card)
        if not identity or not title or not source_status or not apply_range[0]:
            errors.append(f"card {card_index}: malformed identity/title/status/application period")
            continue
        if not dongjak_detail_url(identity):
            errors.append(f"card {card_index}: unsafe detail identity")
            continue

        fee_node = card.select_one(".info .unchrgd, .info .chrgd")
        info_nodes = card.select(".info > span")
        theme = ""
        for node in info_nodes:
            value = _clean(node.get_text(" ", strip=True))
            if node is not fee_node and value and value not in {"무료", "유료"}:
                theme = value
                break
        description_node = card.select_one(".info-desc .desc_4")
        row = _base_row(target, identity, title)
        row.update(
            {
                "status": _STATUS_MAP[source_status],
                "apply_start": apply_range[0],
                "apply_end": apply_range[1],
                "apply_period": apply_range[2],
                "target": _label_value(card, "대상"),
                "fee": _clean(fee_node.get_text(" ", strip=True) if fee_node else ""),
                "source_theme": theme,
                "description": _clean(
                    description_node.get_text(" ", strip=True)
                    if description_node
                    else ""
                ),
            }
        )
        if operation_range[0]:
            row.update(
                {
                    "start_date": operation_range[0],
                    "end_date": operation_range[1],
                    "period": operation_range[2],
                }
            )
        row["raw_fields"].update(
            {
                "source_status": source_status,
                "selection_method_list": next(
                    (value for value in status_nodes if value != source_status), ""
                ),
                "list_operation_period_missing": not bool(operation_range[0]),
            }
        )
        rows.append(row)
    if len(cards) != len(rows):
        errors.append(f"parsed {len(rows)} of {len(cards)} cards")
    return rows, errors


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for block in soup.select(".program-info .desc-box dl"):
        key_node = block.find("dt")
        value_node = block.find("dd")
        if key_node is None or value_node is None:
            continue
        key = _clean(key_node.get_text(" ", strip=True)).replace(" ", "")
        value = _clean(value_node.get_text(" ", strip=True))
        if key and key not in pairs:
            pairs[key] = value
    return pairs


def _pair(pairs: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = _clean(pairs.get(name.replace(" ", "")))
        if value:
            return value
    return ""


def _safe_application_link(value: Any, identity: str) -> str:
    candidate = urljoin(DONGJAK_EDUCATION_URL, _clean(value))
    parsed = urlparse(candidate)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() != DONGJAK_HOST
        or parsed.path != DONGJAK_APPLICATION_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or set(query) - {"prgSn", "tmplatSeCd", "menuNo", "useAt", "pageIndex"}
        or _query_value(query, "prgSn") != identity
        or _query_value(query, "tmplatSeCd") != DONGJAK_TEMPLATE_CODE
        or _query_value(query, "menuNo") != DONGJAK_MENU_NO
        or _query_value(query, "useAt") != "Y"
    ):
        return ""
    page_index = _query_value(query, "pageIndex")
    if page_index and not page_index.isdigit():
        return ""
    return candidate


def _external_application_link(soup: BeautifulSoup) -> str:
    for link in soup.select(".txt-per-box a[href]"):
        candidate = _clean(link.get("href"))
        parsed = urlparse(candidate)
        if (
            parsed.scheme.lower() == "https"
            and parsed.hostname
            and parsed.hostname.rstrip(".").lower() != DONGJAK_HOST
            and not parsed.username
            and not parsed.password
        ):
            return candidate
    return ""


def _latest_explicit_date(soup: BeautifulSoup) -> Optional[date]:
    root = soup.select_one(".program-resve-wrap")
    text = _clean(root.get_text(" ", strip=True) if root else "")
    values: list[date] = []
    for year, month, day in _FULL_DATE_RE.findall(text):
        try:
            values.append(date(int(year), int(month), int(day)))
        except ValueError:
            continue
    return max(values) if values else None


def _capacity(value: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    text = _clean(value)
    total_match = re.search(r"정원\s*(\d[\d,]*)", text)
    current_match = re.search(r"신청\s*(\d[\d,]*)", text)
    wait_match = re.search(r"대기자?\s*(\d[\d,]*)", text)

    def number(match: Optional[re.Match[str]]) -> Optional[int]:
        return int(match.group(1).replace(",", "")) if match else None

    return number(current_match), number(total_match), number(wait_match)


def _enrich_detail(
    row: dict[str, Any], soup: BeautifulSoup, cutoff: date
) -> tuple[bool, list[str]]:
    identity = _clean(row.get("raw_fields", {}).get("program_id"))
    errors: list[str] = []
    title_node = soup.select_one(".program-resve-wrap .subject h4")
    detail_title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    if detail_title != _clean(row.get("title")):
        errors.append(f"program {identity}: detail title mismatch")

    pairs = _detail_pairs(soup)
    detail_apply = _range_from_text(_pair(pairs, "신청기간", "접수기간"))
    detail_operation = _range_from_text(
        _pair(pairs, "운영기간", "교육기간", "강의기간")
    )
    if not detail_apply[0]:
        errors.append(f"program {identity}: detail application period missing")
    elif detail_apply[2] != _clean(row.get("apply_period")):
        errors.append(f"program {identity}: detail/list application period mismatch")

    list_operation = _clean(row.get("period"))
    if list_operation:
        if not detail_operation[0]:
            errors.append(f"program {identity}: detail operating period missing")
        elif detail_operation[2] != list_operation:
            errors.append(f"program {identity}: detail/list operating period mismatch")

    effective_operation = detail_operation if detail_operation[0] else (
        (_clean(row.get("start_date")), _clean(row.get("end_date")), list_operation)
        if list_operation
        else ("", "", "")
    )
    current = False
    if effective_operation[1]:
        current = date.fromisoformat(effective_operation[1]) >= cutoff
    else:
        latest = _latest_explicit_date(soup)
        source_status = _clean(row.get("raw_fields", {}).get("source_status"))
        application_end = date.fromisoformat(_clean(row.get("apply_end")))
        if latest is not None and latest >= cutoff:
            errors.append(
                f"program {identity}: current/future date exists but structured operating period is missing"
            )
        elif source_status not in {"마감", "인원마감"} or application_end >= cutoff:
            errors.append(
                f"program {identity}: cannot prove current/expired without an operating period"
            )

    venue = _pair(pairs, "운영장소", "교육장소", "장소")
    organization = _pair(pairs, "운영기관")
    department = _pair(pairs, "담당부서")
    # ``담당부서`` is an organizational sub-unit, not a user-facing branch.
    # When the source omits 운영기관 but identifies a Dongjak-gu department,
    # keep the institution stable as 동작구청 and retain the department in its
    # own field.  This avoids presenting e.g. 아동여성과 as a facility branch.
    branch = organization or ("동작구청" if department else venue)
    if current and (not venue or not branch):
        errors.append(f"program {identity}: current detail has no venue/branch")

    capacity_raw = _pair(pairs, "모집방법", "모집인원", "정원")
    capacity_current, capacity_total, waitlist_total = _capacity(capacity_raw)
    source_schedule = _pair(
        pairs,
        "운영시간",
        "교육시간",
        "강의시간",
        "수강시간",
        "시간",
    )
    if not source_schedule:
        title_time = re.search(
            r"(?<!\d)(?:[01]?\d|2[0-3])\s*(?::\s*[0-5]\d|시)"
            r"(?:\s*~\s*(?:[01]?\d|2[0-3])\s*(?::\s*[0-5]\d|시)?)?",
            _clean(row.get("title")),
        )
        source_schedule = _clean(title_time.group(0)) if title_time else ""
    target = _pair(pairs, "대상") or _clean(row.get("target"))
    source_fee = (
        _pair(pairs, "이용요금", "수강료", "교육비")
        or _clean(row.get("fee"))
    )
    category = _clean(row.get("source_theme")) or "교육·강좌"
    internal_application = ""
    for link in soup.select(
        f".program-resve-wrap .btn-box a[href*='{DONGJAK_APPLICATION_PATH}']"
    ):
        internal_application = _safe_application_link(link.get("href"), identity)
        if internal_application:
            break
    external_application = _external_application_link(soup)
    source_status = _clean(row.get("raw_fields", {}).get("source_status"))
    application = internal_application or external_application

    if effective_operation[0]:
        row.update(
            {
                "start_date": effective_operation[0],
                "end_date": effective_operation[1],
                "period": effective_operation[2],
            }
        )
    row.update(
        {
            "branch": branch,
            "branch_code": _stable_branch_code(branch) if branch else "",
            "preserve_branch": bool(branch),
            "provider_organizer": organization,
            "provider_department": department,
            "room": venue,
            "venue_name": venue,
            "target": target or "대상 별도 안내",
            "fee": source_fee or "요금 별도 안내",
            "schedule_raw": source_schedule or "시간 별도 안내",
            "category_raw": category,
            "phone": _pair(pairs, "문의전화", "문의"),
            "selection_method": _pair(pairs, "선정방식")
            or row.get("raw_fields", {}).get("selection_method_list"),
            "capacity": capacity_raw,
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "waitlist_total": waitlist_total,
            "reservation_available": bool(source_status == "접수중" and application),
        }
    )
    if source_status == "접수중" and application:
        row["application_url"] = application
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        row["raw_fields"]["clear_application_url"] = True
    row["raw_fields"].update(
        {
            "detail_identity_verified": detail_title == _clean(row.get("title")),
            "detail_pairs": pairs,
            "internal_application_link": bool(internal_application),
            "external_application_link": bool(external_application),
            "source_schedule": source_schedule,
            "source_time_omitted": not bool(source_schedule),
            "source_fee": source_fee,
            "source_fee_omitted": not bool(source_fee),
            "source_target": target,
            "source_target_omitted": not bool(target),
            "source_venue": venue,
        }
    )
    return current, errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure_meta(message: str, *, source_cap_reached: bool = False) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_pages": 0,
        "list_requests": 0,
        "total_pages": 0,
        "total_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "detail_required_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": source_cap_reached,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def collect_dongjak_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 200,
    detail_limit: int = 1000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = DONGJAK_MAX_WORKERS,
    snapshot_attempts: int = 3,
    _snapshot_attempt: int = 1,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete current/future education-template snapshot.

    Every official list page is consumed and page one is fetched again after
    pagination to detect insertion drift.  Every row that is current by its
    list period, or ambiguous because the list omitted that period, is then
    detail-verified.  Any page, identity, date or detail failure discards the
    entire snapshot.
    """

    if not is_dongjak_education_target(target):
        return [], DONGJAK_PARSER, _failure_meta(
            "target does not match the exact Dongjak education provider route"
        )

    current_fetcher = fetcher or _default_fetcher
    current_session_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    cutoff = _today(today)
    errors: list[str] = []
    detail_errors: list[str] = []
    source_cap_reached = False
    list_requests = 0
    total_pages = 0
    page_rows: dict[int, list[dict[str, Any]]] = {}
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def thread_session() -> Any:
        value = getattr(local, "session", None)
        if value is None:
            value = current_session_factory()
            local.session = value
            with sessions_lock:
                sessions.append(value)
        return value

    def fetch_page(page_no: int) -> tuple[int, Optional[BeautifulSoup], str]:
        try:
            return (
                page_no,
                _fetch(
                    current_fetcher,
                    thread_session(),
                    dongjak_list_url(page_no),
                    timeout,
                ),
                "",
            )
        except Exception as exc:
            return page_no, None, f"page {page_no}: fetch {type(exc).__name__}"

    try:
        first_no, first_soup, first_error = fetch_page(1)
        list_requests += 1
        if first_error or first_soup is None:
            return [], DONGJAK_PARSER, _failure_meta(first_error or "page 1 fetch failed")
        first_contract = _page_contract(first_soup)
        if first_contract is None or first_contract[0] != first_no:
            return [], DONGJAK_PARSER, _failure_meta(
                "page 1 pagination contract is missing or malformed"
            )
        total_pages = first_contract[1]
        if int(max_pages) < total_pages:
            return [], DONGJAK_PARSER, _failure_meta(
                f"max_pages cap {int(max_pages)} is below declared {total_pages} education pages",
                source_cap_reached=True,
            )
        first_rows, first_errors = _parse_list_page(target, first_soup)
        errors.extend(f"page 1: {message}" for message in first_errors)
        page_rows[1] = first_rows

        remaining = list(range(2, total_pages + 1))
        if remaining:
            workers = min(
                DONGJAK_MAX_WORKERS,
                max(1, int(max_workers)),
                len(remaining),
            )
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="dongjak-list"
            ) as pool:
                page_results = list(pool.map(fetch_page, remaining))
            list_requests += len(page_results)
            for page_no, soup, fetch_error in page_results:
                if fetch_error or soup is None:
                    errors.append(fetch_error or f"page {page_no}: empty response")
                    continue
                contract = _page_contract(soup)
                if contract != (page_no, total_pages):
                    errors.append(
                        f"page {page_no}: pagination contract {contract!r} != {(page_no, total_pages)!r}"
                    )
                    continue
                rows, page_errors = _parse_list_page(target, soup)
                errors.extend(f"page {page_no}: {message}" for message in page_errors)
                page_rows[page_no] = rows

        for page_no in range(1, total_pages + 1):
            rows = page_rows.get(page_no, [])
            if page_no < total_pages and len(rows) != DONGJAK_PAGE_SIZE:
                errors.append(
                    f"page {page_no}: exposed {len(rows)} rows, expected {DONGJAK_PAGE_SIZE}"
                )
            elif page_no == total_pages and not (1 <= len(rows) <= DONGJAK_PAGE_SIZE):
                errors.append(f"last page {page_no}: invalid row count {len(rows)}")

        _check_no, check_soup, check_error = fetch_page(1)
        list_requests += 1
        if check_error or check_soup is None:
            errors.append(check_error or "page 1 recheck failed")
        else:
            check_contract = _page_contract(check_soup)
            check_rows, check_errors = _parse_list_page(target, check_soup)
            errors.extend(f"page 1 recheck: {message}" for message in check_errors)
            first_ids = [
                _clean(row.get("raw_fields", {}).get("program_id"))
                for row in page_rows.get(1, [])
            ]
            check_ids = [
                _clean(row.get("raw_fields", {}).get("program_id"))
                for row in check_rows
            ]
            if check_contract != (1, total_pages) or check_ids != first_ids:
                errors.append("page 1 changed while the complete snapshot was collected")

        all_rows = [
            row
            for page_no in range(1, total_pages + 1)
            for row in page_rows.get(page_no, [])
        ]
        all_ids = [
            _clean(row.get("raw_fields", {}).get("program_id")) for row in all_rows
        ]
        rows_by_id: dict[str, list[dict[str, Any]]] = {}
        for row, identity in zip(all_rows, all_ids):
            rows_by_id.setdefault(identity, []).append(row)
        duplicate_count = sum(len(values) - 1 for values in rows_by_id.values())
        benign_expired_duplicate_count = 0
        for identity, values in rows_by_id.items():
            if len(values) < 2:
                continue
            signatures = {
                (
                    _clean(row.get("title")),
                    _clean(row.get("raw_url")),
                    _clean(row.get("start_date")),
                    _clean(row.get("end_date")),
                    _clean(row.get("raw_fields", {}).get("source_status")),
                )
                for row in values
            }
            ends: list[date] = []
            for row in values:
                try:
                    ends.append(date.fromisoformat(_clean(row.get("end_date"))))
                except ValueError:
                    ends = []
                    break
            if len(signatures) == 1 and ends and all(value < cutoff for value in ends):
                benign_expired_duplicate_count += len(values) - 1
                continue
            errors.append(
                f"duplicate program identities: program {identity} is not an identical expired history row"
            )

        # The official backend can repeat byte-identical expired history rows
        # on late pages.  They do not affect the owned current/future snapshot;
        # retain one identity for all lifecycle and detail decisions while
        # recording the raw anomaly in metadata.
        unique_rows = [values[0] for values in rows_by_id.values()]

        expected_total = (
            (total_pages - 1) * DONGJAK_PAGE_SIZE
            + len(page_rows.get(total_pages, []))
            if total_pages
            else 0
        )
        if len(all_rows) != expected_total:
            errors.append(
                f"complete-page row count {len(all_rows)} != expected {expected_total}"
            )

        current_by_list: list[dict[str, Any]] = []
        ambiguous: list[dict[str, Any]] = []
        expired_by_list = 0
        for row in unique_rows:
            end_text = _clean(row.get("end_date"))
            if not end_text:
                ambiguous.append(row)
                continue
            if date.fromisoformat(end_text) >= cutoff:
                current_by_list.append(row)
            else:
                expired_by_list += 1

        detail_rows = [*current_by_list, *ambiguous]
        detail_required_count = len(detail_rows)
        if int(detail_limit) < detail_required_count:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap {int(detail_limit)} is below required {detail_required_count} details"
            )

        detail_attempts = 0
        detail_pages = 0
        current_rows: list[dict[str, Any]] = []
        ambiguous_expired_count = 0
        if not errors and detail_rows:
            def fetch_detail(row: dict[str, Any]) -> tuple[dict[str, Any], bool, bool, list[str]]:
                identity = _clean(row.get("raw_fields", {}).get("program_id"))
                try:
                    soup = _fetch(
                        current_fetcher,
                        thread_session(),
                        _clean(row.get("raw_url")),
                        timeout,
                    )
                    is_current, item_errors = _enrich_detail(row, soup, cutoff)
                    return row, True, is_current, item_errors
                except Exception as exc:
                    return row, False, False, [
                        f"program {identity}: detail fetch {type(exc).__name__}"
                    ]

            detail_attempts = detail_required_count
            workers = min(
                DONGJAK_MAX_WORKERS,
                max(1, int(max_workers)),
                detail_required_count,
            )
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="dongjak-detail"
            ) as pool:
                detail_results = list(pool.map(fetch_detail, detail_rows))
            for row, fetched, is_current, item_errors in detail_results:
                detail_pages += int(fetched)
                detail_errors.extend(item_errors)
                if is_current:
                    current_rows.append(row)
                elif row in ambiguous:
                    ambiguous_expired_count += 1
        elif not detail_rows:
            detail_attempts = 0
            detail_pages = 0
            current_rows = []

        errors.extend(detail_errors)
        details_complete = (
            detail_attempts == detail_required_count
            and detail_pages == detail_required_count
            and not detail_errors
            and not source_cap_reached
        )
        cleaned = [_clean_row(row) for row in current_rows]
        if not errors and details_complete:
            try:
                deduped = list(current_dedupe(cleaned))
            except Exception as exc:
                errors.append(f"dedupe failed {type(exc).__name__}")
                deduped = []
            if len(deduped) != len(cleaned):
                errors.append(
                    f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}"
                )
            cleaned = deduped

        pagination_complete = not errors and len(page_rows) == total_pages
        snapshot_complete = pagination_complete and details_complete and not errors
        if not snapshot_complete:
            cleaned = []

        current_status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status"))
            for row in current_rows
        )
        all_status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status")) for row in all_rows
        )
        branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        expired_count = expired_by_list + ambiguous_expired_count
        meta: dict[str, Any] = {
            "pages": list_requests,
            "list_pages": total_pages,
            "list_requests": list_requests,
            "page_one_rechecks": 1,
            "request_count": list_requests + detail_attempts,
            "total_pages": total_pages,
            "page_size": DONGJAK_PAGE_SIZE,
            "total_count": len(all_rows),
            "source_exposed_count": len(all_rows),
            "unique_total_count": len(unique_rows),
            "raw_row_count": len(all_rows),
            "unique_id_count": len(set(all_ids)),
            "duplicate_count": duplicate_count,
            "benign_expired_duplicate_count": benign_expired_duplicate_count,
            "ambiguous_list_period_count": len(ambiguous),
            "expired_by_list_count": expired_by_list,
            "ambiguous_expired_count": ambiguous_expired_count,
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(cleaned),
            "detail_required_count": detail_required_count,
            "required_detail_count": detail_required_count,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_errors": len(detail_errors),
            "detail_error_messages": detail_errors,
            "pagination_detected": total_pages > 1,
            "pagination_complete": pagination_complete,
            "pagination_exhausted": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "recursion_depth": 0,
            "status_counts": dict(all_status_counts),
            "current_status_counts": dict(current_status_counts),
            "branch_counts": dict(branch_counts),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in current_rows
            ),
            "no_current_data": snapshot_complete and not current_rows,
            "no_current_reason": (
                "the complete Dongjak education-template snapshot has no current/future rows"
                if snapshot_complete and not current_rows
                else ""
            ),
        }
        meta["snapshot_attempts"] = _snapshot_attempt
        if errors:
            unique_errors = list(dict.fromkeys(errors))
            retryable_snapshot_drift = all(
                message == "page 1 changed while the complete snapshot was collected"
                or message.startswith("duplicate program identities:")
                for message in unique_errors
            )
            if (
                retryable_snapshot_drift
                and _snapshot_attempt < max(1, int(snapshot_attempts))
            ):
                return collect_dongjak_education_courses(
                    target,
                    timeout=timeout,
                    max_pages=max_pages,
                    detail_limit=detail_limit,
                    fetcher=fetcher,
                    session_factory=session_factory,
                    dedupe_rows=dedupe_rows,
                    today=today,
                    max_workers=max_workers,
                    snapshot_attempts=snapshot_attempts,
                    _snapshot_attempt=_snapshot_attempt + 1,
                )
            meta["configured_collection_error"] = "; ".join(unique_errors)
        return cleaned, DONGJAK_PARSER, meta
    finally:
        for value in sessions:
            _close_quietly(value)


collect_dongjak_target = collect_dongjak_education_courses


__all__ = [
    "DONGJAK_APPLICATION_PATH",
    "DONGJAK_DETAIL_PATH",
    "DONGJAK_EDUCATION_PROVIDER",
    "DONGJAK_EDUCATION_URL",
    "DONGJAK_HOST",
    "DONGJAK_LIST_PATH",
    "DONGJAK_MAX_WORKERS",
    "DONGJAK_MENU_NO",
    "DONGJAK_MUNICIPALITY_CODE",
    "DONGJAK_MUNICIPALITY_NAME",
    "DONGJAK_PAGE_SIZE",
    "DONGJAK_PARSER",
    "DONGJAK_TEMPLATE_CODE",
    "collect_dongjak_education_courses",
    "collect_dongjak_target",
    "dongjak_detail_url",
    "dongjak_list_url",
    "is_dongjak_education_target",
    "is_target",
]
