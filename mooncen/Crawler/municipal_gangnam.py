"""Complete dated-course collector for Gangnam-gu's integrated reservation site.

The public FMCS catalogue contains both recurring class templates and dated
special lectures.  Recurring templates do not expose a course period, so they
are audited as part of the complete source snapshot but are not published as
current courses.  Every dated row is checked against its official detail page
before it can be returned.

This module deliberately has no dependency on the shared municipal router.
Callers may inject their managed HTTP session and fetch helper.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GANGNAM_EDUCATION_PROVIDER = "MUNI_LIFE_GANGNAM_GO_KR_9C474A31"
GANGNAM_EDUCATION_URL = "https://life.gangnam.go.kr/fmcs/52"
GANGNAM_HOST = "life.gangnam.go.kr"
GANGNAM_LIST_PATH = "/fmcs/52"
GANGNAM_COMPANY_API = "https://life.gangnam.go.kr/rest/common/company"
GANGNAM_LECTURE_API = "https://life.gangnam.go.kr/rest/lecture/list"
GANGNAM_PAGE_SIZE = 100
GANGNAM_MAX_COMPANIES = 100
GANGNAM_PARSER = "gangnam_fmcs_company_complete_dated_current_future+detail"
GANGNAM_MUNICIPALITY_CODE = "1168000000"
GANGNAM_MUNICIPALITY_NAME = "서울특별시 강남구"

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_COMPANY_RE = re.compile(r"[A-Z0-9]{4,12}")
_CLASS_RE = re.compile(r"\d{5}")
_TEST_TITLE_RE = re.compile(r"(?:^|\W)test(?:\W|$)|테스트|신청금지", re.IGNORECASE)
_DETAIL_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:년|[.\-/])\s*(\d{1,2})\s*"
    r"(?:월|[.\-/])\s*(\d{1,2})\s*(?:일)?(?!\d)"
)
_DETAIL_PERIOD_RE = re.compile(
    r"(?:수업기간|일시)\s*:?\s*"
    r"(?:(20\d{2})\s*년\s*)?(\d{1,2})\s*(?:월|/)\s*(\d{1,2})\s*(?:일)?\s*"
    r"[~～-]\s*(?:(20\d{2})\s*년\s*)?(\d{1,2})\s*(?:월|/)\s*(\d{1,2})\s*(?:일)?"
)
_STATUS_MAP: Mapping[str, str] = {
    "R": "접수중",
    "W": "접수예정",
    "F": "접수마감",
    "CE": "접수마감",
    "E": "접수마감",
}


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


def is_gangnam_education_target(target: Any) -> bool:
    return (
        _provider(target) == GANGNAM_EDUCATION_PROVIDER
        and _target_url(target) == GANGNAM_EDUCATION_URL
    )


is_target = is_gangnam_education_target


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": GANGNAM_EDUCATION_URL,
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not getattr(response, "content", b""):
        raise ValueError("empty HTTP response")
    return response


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _coerce_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    method = getattr(value, "json", None)
    if callable(method):
        return method()
    if isinstance(value, bytes):
        import json

        return json.loads(value.decode("utf-8"))
    if isinstance(value, str):
        import json

        return json.loads(value)
    raise TypeError("fetcher did not return JSON or an HTTP response")


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher did not return HTML or an HTTP response")
    return BeautifulSoup(content, "lxml")


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _iso_date(value: Any) -> Optional[date]:
    raw = _clean(value)
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _detail_dates(value: Any) -> set[date]:
    result: set[date] = set()
    for match in _DETAIL_DATE_RE.finditer(_clean(value)):
        try:
            result.add(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    return result


def _detail_course_period(value: Any, fallback_start: Any) -> tuple[str, str, str]:
    fallback = _iso_date(fallback_start)
    if fallback is None:
        return "", "", ""
    match = _DETAIL_PERIOD_RE.search(_clean(value))
    if match is None:
        return "", "", ""
    start_year = int(match.group(1) or fallback.year)
    start_month = int(match.group(2))
    start_day = int(match.group(3))
    end_year = int(match.group(4) or start_year)
    end_month = int(match.group(5))
    end_day = int(match.group(6))
    if not match.group(4) and end_month < start_month:
        end_year += 1
    try:
        start = date(start_year, start_month, start_day)
        end = date(end_year, end_month, end_day)
    except ValueError:
        return "", "", ""
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _as_int(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    return int(raw) if re.fullmatch(r"\d+", raw) else None


def _stable_branch_code(provider: str, branch: str) -> str:
    digest = hashlib.sha1(
        f"{_clean(provider)}|{_normalized(branch)}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"GANGNAM_BRANCH_{digest}"


def gangnam_list_url(company_code: str, page: int) -> str:
    company = _clean(company_code)
    if not _COMPANY_RE.fullmatch(company):
        return ""
    query = urlencode(
        (
            ("company_code", company),
            ("mem_no", ""),
            ("search_type", ""),
            ("category_cd", ""),
            ("category_level", "9"),
            ("class_nm", ""),
            ("train_day", ""),
            ("adult_gubn", ""),
            ("lecturer_nm", ""),
            ("page", str(max(1, int(page)))),
            ("page_size", str(GANGNAM_PAGE_SIZE)),
        )
    )
    return f"{GANGNAM_LECTURE_API}?{query}"


def gangnam_detail_url(company_code: Any, class_code: Any) -> str:
    company = _clean(company_code)
    identity = _clean(class_code)
    if not _COMPANY_RE.fullmatch(company) or not _CLASS_RE.fullmatch(identity):
        return ""
    query = urlencode(
        (("action", "read"), ("comcd", company), ("classcd", identity), ("type", "R"))
    )
    return f"{GANGNAM_EDUCATION_URL}?{query}"


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row in soup.select(".proc_read table tr, table tr"):
        heading = row.find("th")
        value = row.find("td")
        if heading is None or value is None:
            continue
        key = _clean(heading.get_text(" ", strip=True))
        if key and key not in pairs:
            pairs[key] = _clean(value.get_text(" ", strip=True))
    return pairs


def _validate_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    raw = row.get("raw_fields", {})
    company = _clean(raw.get("company_code"))
    identity = _clean(raw.get("class_code"))
    errors: list[str] = []
    hidden_company = soup.select_one('input[name="comcd"]')
    hidden_class = soup.select_one('input[name="classcd"]')
    if hidden_company is None or _clean(hidden_company.get("value")) != company:
        errors.append(f"{company}/{identity}: detail company identity mismatch")
    if hidden_class is None or _clean(hidden_class.get("value")) != identity:
        errors.append(f"{company}/{identity}: detail class identity mismatch")
    pairs = _detail_pairs(soup)
    if _normalized(pairs.get("강좌명")) != _normalized(row.get("title")):
        errors.append(f"{company}/{identity}: detail title mismatch")
    center = _clean(pairs.get("운영센터"))
    if _normalized(row.get("branch")) not in _normalized(center):
        errors.append(f"{company}/{identity}: detail center mismatch")
    detail_text = soup.get_text(" ", strip=True)
    start, end, period = _detail_course_period(detail_text, row.get("start_date"))
    if errors:
        return errors
    if not start or not end:
        row["raw_fields"]["detail_pairs"] = pairs
        row["raw_fields"]["detail_valid"] = False
        row["raw_fields"]["excluded_reason"] = "detail_course_period_missing"
        return []

    row["start_date"] = start
    row["end_date"] = end
    row["period"] = period

    schedule = _clean(pairs.get("시간/요일"))
    api_schedule = _clean(row.get("schedule"))
    schedule_raw = schedule or api_schedule or "\uc2dc\uac04 \ubcc4\ub3c4 \uc548\ub0b4"
    venue_name = _clean(center.split("/", 1)[0]) or _clean(row.get("branch"))
    target = _clean(pairs.get("교육대상"))
    instructor = _clean(pairs.get("강사명"))
    capacity_text = _clean(pairs.get("신청인원/정원"))
    capacity_values = [_as_int(token) for token in re.findall(r"[\d,]+", capacity_text)]
    capacity_values = [value for value in capacity_values if value is not None]
    row["schedule"] = schedule
    row["schedule_raw"] = schedule_raw
    row["venue_name"] = venue_name
    row["target"] = target
    row["instructor"] = instructor
    row["application_method"] = _clean(pairs.get("접수방식"))
    if len(capacity_values) >= 2:
        row["capacity_current"] = capacity_values[0]
        row["capacity_total"] = capacity_values[1]
        row["capacity"] = capacity_values[1]
    description = _clean(
        " ".join(node.get_text(" ", strip=True) for node in soup.select(".pattern_box"))
    )
    if description:
        row["description"] = description
    status_node = soup.select_one("span.status")
    if status_node is not None:
        row["raw_fields"]["detail_status"] = _clean(
            status_node.get_text(" ", strip=True)
        )
    row["raw_fields"]["detail_pairs"] = pairs
    row["raw_fields"]["detail_valid"] = True
    row["raw_fields"]["required_field_provenance"] = {
        "schedule_raw": (
            "detail"
            if schedule
            else "list_api"
            if api_schedule
            else "official_source_omission"
        ),
        "venue_name": "detail_center" if center else "official_branch",
    }
    return []


def _build_row(target: Any, item: Mapping[str, Any], branch: str) -> dict[str, Any]:
    provider = _provider(target)
    company = _clean(item.get("comcd"))
    identity = _clean(item.get("class_cd"))
    title = _clean(item.get("class_nm"))
    start = _clean(item.get("train_sdate"))
    end = _clean(item.get("train_edate"))
    status_code = _clean(item.get("status"))
    fee_amount = _as_int(item.get("course_fee"))
    raw_url = gangnam_detail_url(company, identity)
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:{company}:{identity}"[:100],
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": branch,
        "branch_code": _stable_branch_code(provider, branch),
        "preserve_branch": True,
        "branch_url": GANGNAM_EDUCATION_URL,
        "raw_url": raw_url,
        "application_url": raw_url,
        "status": _STATUS_MAP.get(status_code, status_code),
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "schedule": _clean(
            f"{item.get('train_day_nm', '')} {item.get('train_stime', '')} ~ {item.get('train_etime', '')}"
        ),
        "target": _clean(item.get("target_age_name")),
        "instructor": _clean(item.get("teacher_name")),
        "fee": "무료" if fee_amount == 0 else (f"{fee_amount:,}원" if fee_amount is not None else ""),
        "fee_amount": fee_amount,
        "capacity": _as_int(item.get("capa")),
        "capacity_total": _as_int(item.get("capa")),
        "capacity_current": _as_int(item.get("reg_person")),
        "reservation_available": status_code == "R",
        "program_type": "강좌",
        "category": "교육·강좌",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "region": GANGNAM_MUNICIPALITY_NAME,
        "municipality_code": GANGNAM_MUNICIPALITY_CODE,
        "municipality_full_name": GANGNAM_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": GANGNAM_PARSER,
            "company_code": company,
            "class_code": identity,
            "source_status": status_code,
            "sports_code": _clean(item.get("sports_cd")),
            "receive_kind": _clean(item.get("receive_kind")),
            "list_start_date": start,
            "list_end_date": end,
        },
    }


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def collect_gangnam_education_courses(
    target: Any,
    timeout: int = 25,
    max_pages: int = 20,
    detail_limit: int = 200,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect every company catalogue page and publish only dated courses."""

    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    errors: list[str] = []
    companies: dict[str, str] = {}
    all_items: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    status_counts: Counter[str] = Counter()
    source_pages = 0
    declared_pages = 0
    declared_total = 0
    invalid_count = 0
    duplicate_count = 0
    source_cap_reached = False
    current_session: Any = None

    if not is_gangnam_education_target(target):
        errors.append("target does not match the provider-owned canonical Gangnam route")

    try:
        if not errors:
            current_session = make_session()
            try:
                company_payload = _coerce_json(
                    fetch(current_session, GANGNAM_COMPANY_API, timeout)
                )
            except Exception as exc:
                errors.append(f"company fetch {type(exc).__name__}")
                company_payload = []
            if not isinstance(company_payload, list) or not company_payload:
                errors.append("company API did not expose a non-empty list")
            elif len(company_payload) > GANGNAM_MAX_COMPANIES:
                errors.append("company API exceeded the reviewed safety cap")
            else:
                for value in company_payload:
                    if not isinstance(value, Mapping):
                        invalid_count += 1
                        continue
                    code = _clean(value.get("comcd"))
                    name = _clean(value.get("comnm"))
                    if not _COMPANY_RE.fullmatch(code) or not name or code in companies:
                        invalid_count += 1
                        continue
                    companies[code] = name

            allowed_pages = max(1, int(max_pages))
            for company, branch in companies.items():
                first_payload: Any = None
                try:
                    first_payload = _coerce_json(
                        fetch(current_session, gangnam_list_url(company, 1), timeout)
                    )
                except Exception as exc:
                    errors.append(f"{company}: page 1 fetch {type(exc).__name__}")
                    continue
                if isinstance(first_payload, Mapping) and first_payload.get("error"):
                    errors.append(f"{company}: lecture API returned an error")
                    continue
                if not isinstance(first_payload, list):
                    errors.append(f"{company}: lecture API response is not a list")
                    continue
                if not first_payload:
                    source_pages += 1
                    declared_pages += 1
                    continue
                totals = {_as_int(item.get("total_count")) for item in first_payload if isinstance(item, Mapping)}
                if len(totals) != 1 or None in totals:
                    errors.append(f"{company}: declared total is missing or inconsistent")
                    continue
                company_total = next(iter(totals)) or 0
                company_pages = max(1, math.ceil(company_total / GANGNAM_PAGE_SIZE))
                declared_total += company_total
                declared_pages += company_pages
                if company_pages > allowed_pages:
                    source_cap_reached = True
                    errors.append(
                        f"{company}: max_pages cap allows {allowed_pages} of {company_pages} pages"
                    )
                company_exposed = 0
                for page_no in range(1, min(company_pages, allowed_pages) + 1):
                    payload = first_payload
                    if page_no > 1:
                        try:
                            payload = _coerce_json(
                                fetch(
                                    current_session,
                                    gangnam_list_url(company, page_no),
                                    timeout,
                                )
                            )
                        except Exception as exc:
                            errors.append(
                                f"{company}: page {page_no} fetch {type(exc).__name__}"
                            )
                            break
                    source_pages += 1
                    if not isinstance(payload, list):
                        errors.append(f"{company}: page {page_no} is not a list")
                        break
                    expected_rows = min(
                        GANGNAM_PAGE_SIZE,
                        max(0, company_total - ((page_no - 1) * GANGNAM_PAGE_SIZE)),
                    )
                    if len(payload) != expected_rows:
                        errors.append(
                            f"{company}: page {page_no} exposed {len(payload)} rows; expected {expected_rows}"
                        )
                    company_exposed += len(payload)
                    for item in payload:
                        if not isinstance(item, Mapping):
                            invalid_count += 1
                            continue
                        item_company = _clean(item.get("comcd"))
                        identity = _clean(item.get("class_cd"))
                        title = _clean(item.get("class_nm"))
                        if (
                            item_company != company
                            or not _CLASS_RE.fullmatch(identity)
                            or not title
                            or _clean(item.get("comnm")) != branch
                            or _as_int(item.get("total_count")) != company_total
                        ):
                            invalid_count += 1
                            continue
                        key = (company, identity)
                        if key in seen:
                            duplicate_count += 1
                            continue
                        seen.add(key)
                        all_items.append(item)
                        status_counts[_clean(item.get("status"))] += 1
                if company_exposed != company_total and company_pages <= allowed_pages:
                    errors.append(
                        f"{company}: declared {company_total} rows but exposed {company_exposed}"
                    )
    finally:
        _close_quietly(current_session)

    if invalid_count:
        errors.append(f"{invalid_count} lecture/company rows were malformed")
    if duplicate_count:
        errors.append(f"{duplicate_count} duplicate company/class identities were exposed")
    if len(seen) != declared_total and not source_cap_reached:
        errors.append(
            f"declared total {declared_total} does not match {len(seen)} unique identities"
        )

    candidates: list[dict[str, Any]] = []
    undated_count = 0
    expired_count = 0
    partial_date_count = 0
    excluded_test_count = 0
    for item in all_items:
        raw_start = _clean(item.get("train_sdate"))
        raw_end = _clean(item.get("train_edate"))
        if not raw_start and not raw_end:
            undated_count += 1
            continue
        start = _iso_date(raw_start)
        end = _iso_date(raw_end)
        if start is None or end is None or end < start:
            partial_date_count += 1
            continue
        if end < cutoff:
            expired_count += 1
            continue
        if _TEST_TITLE_RE.search(_clean(item.get("class_nm"))):
            excluded_test_count += 1
            continue
        candidates.append(_build_row(target, item, companies[_clean(item.get("comcd"))]))
    if partial_date_count:
        errors.append(f"{partial_date_count} dated rows had malformed course periods")

    detail_required = len(candidates)
    allowed_details = max(0, int(detail_limit))
    selected = candidates[:allowed_details]
    if len(selected) < detail_required:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {len(selected)} of {detail_required} required details"
        )
    detail_pages = 0
    detail_errors: list[str] = []
    detail_session: Any = None
    try:
        if selected:
            detail_session = make_session()
        for row in selected:
            try:
                soup = _coerce_soup(
                    fetch(detail_session, _clean(row.get("raw_url")), timeout)
                )
                detail_pages += 1
                detail_errors.extend(_validate_detail(row, soup))
            except Exception as exc:
                identity = _clean(row.get("provider_course_id"))
                detail_errors.append(f"{identity}: detail fetch {type(exc).__name__}")
    finally:
        _close_quietly(detail_session)

    valid_rows = [
        _clean_row(row)
        for row in selected
        if row.get("raw_fields", {}).get("detail_valid") is True
    ]
    detail_undated_count = sum(
        row.get("raw_fields", {}).get("excluded_reason")
        == "detail_course_period_missing"
        for row in selected
    )
    if dedupe_rows is not None:
        try:
            valid_rows = list(dedupe_rows(valid_rows))
        except Exception as exc:
            errors.append(f"dedupe_rows {type(exc).__name__}")

    unique_errors = list(dict.fromkeys([*errors, *detail_errors]))
    list_complete = (
        not errors
        and source_pages == declared_pages
        and len(seen) == declared_total
        and invalid_count == 0
        and duplicate_count == 0
    )
    details_complete = (
        len(selected) == detail_required
        and detail_pages == detail_required
        and not detail_errors
    )
    snapshot_complete = list_complete and details_complete and not unique_errors
    no_current_data = snapshot_complete and not valid_rows
    meta: dict[str, Any] = {
        "pages": source_pages,
        "declared_pages": declared_pages,
        "detail_pages": detail_pages,
        "detail_attempts": len(selected),
        "detail_required_count": detail_required,
        "required_detail_count": detail_required,
        "detail_errors": len(detail_errors),
        "pagination_detected": declared_pages > len(companies),
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "full_snapshot_required": True,
        "source_cap_reached": source_cap_reached,
        "recursion_depth": 0,
        "company_count": len(companies),
        "total_count": declared_total,
        "discovered_links": len(seen),
        "candidate_count": detail_required,
        "dated_count": detail_required + expired_count + excluded_test_count,
        "undated_count": undated_count,
        "detail_undated_count": detail_undated_count,
        "expired_count": expired_count,
        "excluded_test_count": excluded_test_count,
        "invalid_count": invalid_count + partial_date_count,
        "duplicate_count": duplicate_count,
        "current_count": len(valid_rows),
        "source_status_counts": dict(status_counts),
        "branch_counts": dict(Counter(_clean(row.get("branch")) for row in valid_rows)),
        "no_current_data": no_current_data,
        "no_current_reason": (
            "official dated current/future Gangnam course list is empty"
            if no_current_data
            else ""
        ),
    }
    if unique_errors:
        shown = unique_errors[:50]
        message = "; ".join(shown)
        if len(unique_errors) > len(shown):
            message += f"; ... {len(unique_errors) - len(shown)} more errors"
        meta["configured_collection_error"] = message
    return valid_rows, GANGNAM_PARSER, meta


collect_gangnam_target = collect_gangnam_education_courses


__all__ = [
    "GANGNAM_COMPANY_API",
    "GANGNAM_EDUCATION_PROVIDER",
    "GANGNAM_EDUCATION_URL",
    "GANGNAM_LECTURE_API",
    "GANGNAM_PAGE_SIZE",
    "GANGNAM_PARSER",
    "collect_gangnam_education_courses",
    "collect_gangnam_target",
    "gangnam_detail_url",
    "gangnam_list_url",
    "is_gangnam_education_target",
    "is_target",
]
