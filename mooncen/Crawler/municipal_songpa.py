"""Fail-closed collector for Songpa-gu's integrated education catalogue.

The Songpa Learn page is the municipality's declared integrated programme
application source.  Its HTML list exposes stable lecture identifiers, branch
names, course periods and application states.  The site's own AJAX detail
endpoint exposes the canonical record behind each lecture.  Every current or
future list row is cross-checked against that endpoint before any row is
returned.

This module intentionally does not import ``Crawler_MunicipalYaml`` so the
shared router can integrate it without creating a circular import.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import hashlib
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


SONGPA_EDUCATION_PROVIDER = "MUNI_WWW_SONGPA_GO_KR_982793EC"
SONGPA_EDUCATION_URL = (
    "https://www.songpa.go.kr/learn/youth/program/lecture_list.do"
)
SONGPA_HOST = "www.songpa.go.kr"
SONGPA_LIST_PATH = "/learn/youth/program/lecture_list.do"
SONGPA_DETAIL_PATH = "/learn/youth/program/lecture_view.do"
SONGPA_DETAIL_API_PATH = "/learn/ajax/lecture_insert_reload.do"
SONGPA_DETAIL_API_URL = f"https://{SONGPA_HOST}{SONGPA_DETAIL_API_PATH}"
SONGPA_PAGE_SIZE = 12
SONGPA_FUTURE_HORIZON = "2099-12-31"
SONGPA_MAX_DETAIL_WORKERS = 8
SONGPA_DETAIL_FETCH_ATTEMPTS = 3
SONGPA_DETAIL_RETRY_BACKOFF_SECONDS = 0.5
SONGPA_PARSER = (
    "songpa_learn_integrated_current_future+dual_view_registration_datetime+"
    "exact_known_reversed_registration_evidence+ajax_detail"
)
SONGPA_MUNICIPALITY_CODE = "1171000000"
SONGPA_MUNICIPALITY_NAME = "서울특별시 송파구"
SONGPA_KNOWN_REVERSED_REGISTRATION: Mapping[
    str, tuple[str, str, str, str]
] = {
    "16644": ("2026-08-06", "10:00", "2026-07-08", "00:00"),
}
SONGPA_GROUP_LOCATIONS: Mapping[int, Mapping[str, Any]] = {
    39: {
        "address": "서울특별시 송파구 올림픽로 326",
        "lat": 37.5144533,
        "lon": 127.1059047,
        "source_url": (
            "https://www.songpa.go.kr/learn/youth/campus/"
            "instrum_lib_intro.do"
        ),
    },
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_LECTURE_ID_RE = re.compile(r"\d{1,10}")
_DATE_RANGE_RE = re.compile(
    r"(?<!\d)(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})(?!\d)"
)
_DATETIME_RANGE_RE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})$"
)
_DETAIL_DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "신청가능": "OPEN",
    "대기신청": "WAITLIST",
    "신청마감": "CLOSED",
    "교육전": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
    "외부홈페이지": "OPEN",
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


def is_songpa_education_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return (
        _provider(target) == SONGPA_EDUCATION_PROVIDER
        and parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == SONGPA_HOST
        and parsed.port is None
        and parsed.path == SONGPA_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_songpa_education_target


def songpa_detail_url(lecture_id: Any) -> str:
    identity = _clean(lecture_id)
    if not _LECTURE_ID_RE.fullmatch(identity):
        return ""
    return f"https://{SONGPA_HOST}{SONGPA_DETAIL_PATH}?{urlencode({'lecture_idx': identity})}"


def songpa_detail_api_url(lecture_id: Any) -> str:
    identity = _clean(lecture_id)
    if not _LECTURE_ID_RE.fullmatch(identity):
        return ""
    return f"{SONGPA_DETAIL_API_URL}?{urlencode({'lecture_idx': identity})}"


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": SONGPA_EDUCATION_URL,
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return current


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _validate_response(response: Any) -> None:
    status = getattr(response, "status_code", 200)
    try:
        status_code = int(status)
    except (TypeError, ValueError) as exc:
        raise ValueError("HTTP response status is invalid") from exc
    if 300 <= status_code < 400:
        raise ValueError("HTTP redirects are not accepted")
    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        return BeautifulSoup(value, "lxml")
    _validate_response(value)
    content = getattr(value, "content", None)
    if content is None:
        text = getattr(value, "text", None)
        if text is None:
            raise TypeError("list response did not contain HTML")
        content = text
    if not content:
        raise ValueError("list response was empty")
    return BeautifulSoup(content, "lxml")


def _coerce_json(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        payload: Any = value
    else:
        _validate_response(value)
        method = getattr(value, "json", None)
        if not callable(method):
            raise TypeError("detail response did not expose JSON")
        payload = method()
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("detail response must be a non-empty object")
    return payload


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
    if not _DETAIL_DATE_RE.fullmatch(raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    raw = _clean(value).replace(",", "")
    if not re.fullmatch(r"-?\d+", raw):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _money_from_text(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    match = re.fullmatch(r"(\d+)\s*원", raw)
    return int(match.group(1)) if match else None


def _format_fee(value: Optional[int], fallback: str) -> str:
    if value is None or value < 0:
        return _clean(fallback)
    return "무료" if value == 0 else f"{value:,}원"


def _stable_branch_code(group_idx: Any, branch: Any) -> str:
    group = _as_int(group_idx)
    if group is not None and group > 0:
        return f"SONGPA_LEARN_GROUP_{group}"
    digest = hashlib.sha1(_normalized(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"SONGPA_LEARN_BRANCH_{digest}"


def _set_group_location(row: dict[str, Any], group_idx: int) -> None:
    location = SONGPA_GROUP_LOCATIONS.get(group_idx)
    if not location:
        return
    address = _clean(location.get("address"))
    row.update(
        {
            "address": address,
            "venue_address": address,
            "branch_address_source": "OFFICIAL_SONGPA_FACILITY_PAGE",
            "branch_lat": location.get("lat"),
            "branch_lon": location.get("lon"),
            "branch_coordinate_source": "GOOGLE_PLACES_TEXT_SEARCH",
            "branch_location_confidence": 100,
            "branch_location_verified": True,
            "branch_location_query": _clean(location.get("source_url")),
        }
    )


def _declared_total(soup: BeautifulSoup) -> int:
    summary = soup.select_one(".prog_list_top p")
    if summary is None:
        return -1
    spans = summary.find_all("span", recursive=False)
    if len(spans) < 2:
        return -1
    raw = _clean(spans[1].get_text(" ", strip=True)).replace(",", "")
    return int(raw) if re.fullmatch(r"\d+", raw) else -1


def _declared_pages(soup: BeautifulSoup) -> int:
    node = soup.select_one(".current_m .total")
    raw = _clean(node.get_text(" ", strip=True)) if node is not None else ""
    return int(raw) if re.fullmatch(r"\d+", raw) else -1


def _lecture_identity(href: Any) -> str:
    parsed = urlparse(urljoin(SONGPA_EDUCATION_URL, _clean(href)))
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != SONGPA_HOST
        or parsed.port is not None
        or parsed.path != SONGPA_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"lecture_idx"} or len(query["lecture_idx"]) != 1:
        return ""
    identity = _clean(query["lecture_idx"][0])
    return identity if _LECTURE_ID_RE.fullmatch(identity) else ""


def _date_ranges(value: Any) -> list[tuple[str, str]]:
    return [(match.group(1), match.group(2)) for match in _DATE_RANGE_RE.finditer(_clean(value))]


def _datetime_range(
    value: Any,
) -> Optional[tuple[str, str, str, str, bool]]:
    match = _DATETIME_RANGE_RE.fullmatch(_clean(value))
    if not match:
        return None
    start_date, start_time, end_date, end_time = match.groups()
    try:
        start = datetime.fromisoformat(f"{start_date}T{start_time}")
        end = datetime.fromisoformat(f"{end_date}T{end_time}")
    except ValueError:
        return None
    return start_date, start_time, end_date, end_time, end < start


def _datetime_minute(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(_clean(value))
    except ValueError:
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M")


def _list_status_rows(soup: BeautifulSoup) -> tuple[dict[str, dict[str, str]], list[str]]:
    rows: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for anchor in soup.select(".list_type a[href*='lecture_view.do']"):
        identity = _lecture_identity(anchor.get("href"))
        cells = anchor.select("section.table > .col")
        if not identity or len(cells) < 7:
            errors.append("list-view row has an invalid identity or column shape")
            continue
        cell_id = _clean(cells[0].get_text(" ", strip=True))
        title = _clean(cells[1].get_text(" ", strip=True))
        branch = _clean(cells[2].get_text(" ", strip=True))
        registration = _datetime_range(cells[3].get_text(" ", strip=True))
        schedule = _clean(cells[4].get_text(" ", strip=True))
        fee = _clean(cells[5].get_text(" ", strip=True))
        status_node = cells[6].select_one(".status")
        status_text = _clean(
            status_node.get_text(" ", strip=True)
            if status_node is not None
            else cells[6].get_text(" ", strip=True)
        )
        if (
            cell_id != identity
            or not title
            or not branch
            or registration is None
            or _money_from_text(fee) is None
            or status_text not in _STATUS_MAP
            or identity in rows
        ):
            errors.append(f"{identity or 'unknown'}: malformed list-view fields")
            continue
        assert registration is not None
        (
            registration_start,
            registration_start_time,
            registration_end,
            registration_end_time,
            registration_reversed,
        ) = registration
        registration_contract = (
            registration_start,
            registration_start_time,
            registration_end,
            registration_end_time,
        )
        if registration_reversed and (
            SONGPA_KNOWN_REVERSED_REGISTRATION.get(identity)
            != registration_contract
        ):
            errors.append(f"{identity}: unsupported reversed registration period")
            continue
        rows[identity] = {
            "title": title,
            "branch": branch,
            "registration_start": registration_start,
            "registration_end": registration_end,
            "registration_start_at": f"{registration_start} {registration_start_time}",
            "registration_end_at": f"{registration_end} {registration_end_time}",
            "registration_reversed": registration_reversed,
            "schedule": schedule,
            "fee": fee,
            "status_text": status_text,
        }
    return rows, errors


def _parse_list_page(
    target: Any,
    soup: BeautifulSoup,
    *,
    page_index: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    status_rows, status_errors = _list_status_rows(soup)
    errors.extend(status_errors)
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    anchors = soup.select(".grid_list a[href*='lecture_view.do']")
    for anchor in anchors:
        identity = _lecture_identity(anchor.get("href"))
        title_node = anchor.select_one(".lec_tit")
        branch_node = anchor.select_one(".loca")
        period_node = anchor.select_one(".desc_box i")
        desc = anchor.select_one(".desc_box")
        direct_spans = desc.find_all("span", recursive=False) if desc is not None else []
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
        branch = _clean(branch_node.get_text(" ", strip=True)) if branch_node else ""
        branch = re.sub(r"\s*/\s*$", "", branch).strip()
        application_method = (
            _clean(direct_spans[0].get_text(" ", strip=True)) if direct_spans else ""
        )
        periods = _date_ranges(
            period_node.get_text(" ", strip=True) if period_node is not None else ""
        )
        status_row = status_rows.get(identity)
        if (
            not identity
            or identity in seen
            or not title
            or not branch
            or not application_method
            or len(periods) != 2
            or status_row is None
        ):
            errors.append(f"page {page_index} {identity or 'unknown'}: malformed grid row")
            continue
        education_start, education_end = periods[1]
        if (
            _normalized(status_row["title"]) != _normalized(title)
            or _normalized(status_row["branch"]) != _normalized(branch)
            or status_row["registration_start"] != periods[0][0]
            or status_row["registration_end"] != periods[0][1]
        ):
            errors.append(f"page {page_index} {identity}: grid/list row mismatch")
            continue
        seen.add(identity)
        provider = _provider(target)
        status = _STATUS_MAP[status_row["status_text"]]
        raw_url = songpa_detail_url(identity)
        parsed.append(
            {
                "provider": provider,
                "provider_course_id": f"{provider}:lecture:{identity}"[:100],
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": branch,
                "venue_name": branch,
                "preserve_branch": True,
                "branch_url": SONGPA_EDUCATION_URL,
                "program_type": "강좌",
                "category": "교육·강좌",
                "raw_url": raw_url,
                "application_url": raw_url if status in {"OPEN", "WAITLIST"} else "",
                "registration_start": (
                    "" if status_row["registration_reversed"] else status_row["registration_start"]
                ),
                "registration_end": (
                    "" if status_row["registration_reversed"] else status_row["registration_end"]
                ),
                "apply_start": (
                    "" if status_row["registration_reversed"] else status_row["registration_start"]
                ),
                "apply_end": (
                    "" if status_row["registration_reversed"] else status_row["registration_end"]
                ),
                "apply_period": (
                    ""
                    if status_row["registration_reversed"]
                    else (
                        f"{status_row['registration_start']} ~ "
                        f"{status_row['registration_end']}"
                    )
                ),
                "start_date": education_start,
                "end_date": education_end,
                "period": f"{education_start} ~ {education_end}",
                "schedule": status_row["schedule"],
                "schedule_raw": status_row["schedule"],
                "fee": status_row["fee"],
                "status": status,
                "reservation_available": status in {"OPEN", "WAITLIST"},
                "application_method": application_method,
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "region": SONGPA_MUNICIPALITY_NAME,
                "municipality_code": SONGPA_MUNICIPALITY_CODE,
                "municipality_full_name": SONGPA_MUNICIPALITY_NAME,
                "raw_fields": {
                    "parser": SONGPA_PARSER,
                    "lecture_idx": identity,
                    "source_status": status_row["status_text"],
                    "list_application_method": application_method,
                    "list_page": page_index,
                    "list_fee": status_row["fee"],
                    "list_registration_start_at": status_row[
                        "registration_start_at"
                    ],
                    "list_registration_end_at": status_row[
                        "registration_end_at"
                    ],
                    "official_reversed_registration_period": status_row[
                        "registration_reversed"
                    ],
                    "venue_source": "list_loca_and_detail_grp_name",
                    "detail_api_url": songpa_detail_api_url(identity),
                },
            }
        )
    if set(status_rows) != seen:
        errors.append(
            f"page {page_index}: grid/list identity sets differ "
            f"({len(seen)}!={len(status_rows)})"
        )
    return parsed, errors


def _description_from_html(value: Any) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    return _clean(BeautifulSoup(raw, "lxml").get_text(" ", strip=True))


def _validate_detail(row: dict[str, Any], payload: Mapping[str, Any]) -> list[str]:
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, dict):
        return ["row raw_fields are missing"]
    identity = _clean(raw_fields.get("lecture_idx"))
    errors: list[str] = []
    detail_identity = _as_int(payload.get("lecture_idx"))
    if detail_identity is None or str(detail_identity) != identity:
        errors.append(f"{identity}: detail identity mismatch")
    if _normalized(payload.get("name")) != _normalized(row.get("title")):
        errors.append(f"{identity}: detail title mismatch")
    if _normalized(payload.get("grp_name")) != _normalized(row.get("branch")):
        errors.append(f"{identity}: detail branch mismatch")
    start = _clean(payload.get("start_dt"))
    end = _clean(payload.get("end_dt"))
    if start != _clean(row.get("start_date")) or end != _clean(row.get("end_date")):
        errors.append(f"{identity}: detail course period mismatch")
    if _clean(payload.get("use_yn")) != "Y":
        errors.append(f"{identity}: detail is not an active official record")
    group_idx = _as_int(payload.get("group_idx"))
    if group_idx is None or group_idx <= 0:
        errors.append(f"{identity}: detail branch group is invalid")
    fee_amount = _as_int(payload.get("fee"))
    list_fee_amount = _money_from_text(raw_fields.get("list_fee"))
    if fee_amount is not None and fee_amount >= 0 and list_fee_amount != fee_amount:
        errors.append(f"{identity}: detail fee mismatch")
    detail_registration_start = _datetime_minute(payload.get("reg_start_st"))
    detail_registration_end = _datetime_minute(payload.get("reg_end_dt"))
    if (
        detail_registration_start
        != _clean(raw_fields.get("list_registration_start_at"))
        or detail_registration_end
        != _clean(raw_fields.get("list_registration_end_at"))
    ):
        errors.append(f"{identity}: detail registration period mismatch")
    if errors:
        return errors

    row["branch_code"] = _stable_branch_code(group_idx, row.get("branch"))
    _set_group_location(row, group_idx)
    row["fee"] = _format_fee(fee_amount, _clean(row.get("fee")))
    if not raw_fields.get("official_reversed_registration_period"):
        row["registration_start"] = _clean(payload.get("reg_start_st")) or _clean(
            row.get("registration_start")
        )
        row["registration_end"] = _clean(payload.get("reg_end_dt")) or _clean(
            row.get("registration_end")
        )
    start_time = _clean(payload.get("start_time"))
    end_time = _clean(payload.get("end_time"))
    if start_time and end_time:
        row["schedule"] = _clean(f"{row.get('schedule')} {start_time}~{end_time}")
        row["schedule_raw"] = row["schedule"]
    target = _clean(payload.get("tgt_detail"))
    instructor = _clean(payload.get("teacher_nm"))
    description = _description_from_html(payload.get("cont"))
    row["target"] = target or "대상 별도 안내"
    if instructor:
        row["instructor"] = instructor
    if description:
        row["description"] = description
    capacity = _as_int(payload.get("student_qty"))
    if capacity is not None and capacity >= 0:
        row["capacity_total"] = capacity
        row["capacity"] = capacity
    raw_fields.update(
        {
            "detail_valid": True,
            "group_idx": group_idx,
            "parent_category_idx": _clean(payload.get("p_idx")),
            "parent_category_name": _clean(payload.get("p_name")),
            "field_code": _clean(payload.get("part_code_idx")),
            "target_codes": _clean(payload.get("tgt_code")),
            "target_source": (
                "detail_tgt_detail"
                if target
                else "official_detail_tgt_detail_blank"
            ),
            "official_detail_target_blank": not target,
            "registration_method_code": _clean(payload.get("reg_method")),
            "detail_status_code": _clean(payload.get("status_code")),
            "study_place_idx": _clean(payload.get("study_place_idx")),
            "source_use_yn": _clean(payload.get("use_yn")),
        }
    )
    return []


def _detail_payload(current_session: Any, row: Mapping[str, Any], timeout: int) -> Mapping[str, Any]:
    raw_fields = row.get("raw_fields") if isinstance(row, Mapping) else None
    api_url = _clean(raw_fields.get("detail_api_url")) if isinstance(raw_fields, Mapping) else ""
    if not api_url:
        raise ValueError("detail API URL is missing")
    for attempt in range(SONGPA_DETAIL_FETCH_ATTEMPTS):
        try:
            response = current_session.get(
                api_url,
                timeout=timeout,
                allow_redirects=False,
                headers={
                    "Referer": SONGPA_EDUCATION_URL,
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            return _coerce_json(response)
        except (requests.RequestException, TimeoutError):
            if attempt + 1 >= SONGPA_DETAIL_FETCH_ATTEMPTS:
                raise
            time.sleep(SONGPA_DETAIL_RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise AssertionError("unreachable detail retry state")


def _parallel_details(
    rows: list[dict[str, Any]],
    *,
    timeout: int,
    detail_limit: int,
    max_workers: int,
    session_factory: SessionFactory,
) -> tuple[int, int, int, list[str], bool]:
    required_count = len(rows)
    allowed = max(0, int(detail_limit))
    selected_count = min(required_count, allowed)
    selected = rows[:selected_count]
    capped = selected_count < required_count
    errors: list[str] = []
    if capped:
        errors.append(
            f"detail_limit cap allows {selected_count} of {required_count} required details"
        )
    if not selected:
        return required_count, selected_count, 0, errors, capped

    worker_count = max(1, min(int(max_workers), SONGPA_MAX_DETAIL_WORKERS, selected_count))
    results: list[tuple[bool, list[str]]] = [(False, []) for _ in selected]
    assignments = [list(range(offset, selected_count, worker_count)) for offset in range(worker_count)]

    def run(indices: list[int]) -> None:
        current_session: Any = None
        try:
            current_session = session_factory()
            for index in indices:
                row = selected[index]
                identity = _clean(row.get("raw_fields", {}).get("lecture_idx"))
                try:
                    payload = _detail_payload(current_session, row, timeout)
                    results[index] = (True, _validate_detail(row, payload))
                except Exception as exc:
                    results[index] = (
                        False,
                        [f"{identity}: detail fetch {type(exc).__name__}"],
                    )
        except Exception as exc:
            for index in indices:
                if results[index] != (False, []):
                    continue
                identity = _clean(
                    selected[index].get("raw_fields", {}).get("lecture_idx")
                )
                results[index] = (
                    False,
                    [f"{identity}: detail session {type(exc).__name__}"],
                )
        finally:
            _close_quietly(current_session)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(run, indices) for indices in assignments if indices]
        for future in futures:
            try:
                future.result()
            except Exception as exc:
                errors.append(f"detail worker {type(exc).__name__}")

    detail_pages = sum(success for success, _item_errors in results)
    errors.extend(error for _success, item_errors in results for error in item_errors)
    return required_count, selected_count, detail_pages, errors, capped


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _empty_meta(errors: list[str]) -> dict[str, Any]:
    return {
        "pages": 0,
        "declared_pages": 0,
        "detail_pages": 0,
        "detail_attempts": 0,
        "detail_required_count": 0,
        "required_detail_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "pagination_exhausted": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "current_count": 0,
        "branch_counts": {},
        "configured_collection_error": "; ".join(errors),
        "full_snapshot_required": True,
    }


def collect_songpa_education_courses(
    target: Any,
    timeout: int = 25,
    max_pages: int = 100,
    detail_limit: int = 1000,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = 6,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete current/future Songpa education snapshot or no rows."""

    errors: list[str] = []
    if not is_songpa_education_target(target):
        errors.append("target does not match the canonical Songpa education source")
    if int(max_pages) < 1:
        errors.append("max_pages must be positive")
    if int(detail_limit) < 0:
        errors.append("detail_limit cannot be negative")
    if int(timeout) < 1:
        errors.append("timeout must be positive")
    if int(max_workers) < 1:
        errors.append("max_workers must be positive")
    if errors:
        return [], SONGPA_PARSER, _empty_meta(errors)

    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    current_session: Any = None
    listed_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    pages_fetched = 0
    declared_total = -1
    declared_pages = -1
    source_cap_reached = False
    duplicate_count = 0

    try:
        current_session = make_session()
        for page_index in range(1, max(1, int(max_pages)) + 1):
            response = current_session.post(
                SONGPA_EDUCATION_URL,
                data={
                    "page": str(page_index),
                    "searchKind2": "1",
                    "searchSDate": cutoff.isoformat(),
                    "searchEDate": SONGPA_FUTURE_HORIZON,
                },
                timeout=timeout,
                allow_redirects=False,
            )
            soup = _coerce_soup(response)
            pages_fetched += 1
            page_total = _declared_total(soup)
            page_count = _declared_pages(soup)
            if page_total < 0 or page_count < 1:
                errors.append(f"page {page_index}: declared totals are missing")
                break
            calculated_pages = max(1, math.ceil(page_total / SONGPA_PAGE_SIZE))
            if page_count != calculated_pages:
                errors.append(
                    f"page {page_index}: declared page count {page_count} "
                    f"does not match total {page_total}"
                )
                break
            if page_index == 1:
                declared_total = page_total
                declared_pages = page_count
                if declared_pages > int(max_pages):
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap allows {int(max_pages)} of {declared_pages} list pages"
                    )
                    break
            elif page_total != declared_total or page_count != declared_pages:
                errors.append(f"page {page_index}: declared totals changed during pagination")
                break

            page_rows, page_errors = _parse_list_page(
                target,
                soup,
                page_index=page_index,
            )
            errors.extend(page_errors)
            expected_rows = min(
                SONGPA_PAGE_SIZE,
                max(0, declared_total - ((page_index - 1) * SONGPA_PAGE_SIZE)),
            )
            if len(page_rows) != expected_rows:
                errors.append(
                    f"page {page_index}: parsed {len(page_rows)} rows; expected {expected_rows}"
                )
            for row in page_rows:
                identity = _clean(row.get("raw_fields", {}).get("lecture_idx"))
                if identity in seen_ids:
                    duplicate_count += 1
                    continue
                seen_ids.add(identity)
                listed_rows.append(row)
            if errors or page_index >= declared_pages:
                break
    except Exception as exc:
        errors.append(f"list fetch {type(exc).__name__}")
    finally:
        _close_quietly(current_session)

    if duplicate_count:
        errors.append(f"{duplicate_count} duplicate lecture identities were exposed")
    if declared_total >= 0 and len(seen_ids) != declared_total and not source_cap_reached:
        errors.append(
            f"declared total {declared_total} does not match {len(seen_ids)} unique rows"
        )
    list_complete = bool(
        not errors
        and declared_pages >= 1
        and pages_fetched == declared_pages
        and len(seen_ids) == declared_total
        and duplicate_count == 0
    )

    current_rows: list[dict[str, Any]] = []
    expired_count = 0
    invalid_period_count = 0
    if list_complete:
        for row in listed_rows:
            start = _iso_date(row.get("start_date"))
            end = _iso_date(row.get("end_date"))
            if start is None or end is None or end < start:
                invalid_period_count += 1
                continue
            if end < cutoff:
                expired_count += 1
                continue
            current_rows.append(row)

    detail_required = len(current_rows)
    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    detail_cap_reached = False
    if list_complete:
        (
            detail_required,
            detail_attempts,
            detail_pages,
            detail_errors,
            detail_cap_reached,
        ) = _parallel_details(
            current_rows,
            timeout=timeout,
            detail_limit=detail_limit,
            max_workers=max_workers,
            session_factory=make_session,
        )

    branch_groups: dict[str, set[str]] = defaultdict(set)
    group_branches: dict[str, set[str]] = defaultdict(set)
    if not detail_errors and detail_attempts == detail_required:
        for row in current_rows:
            branch = _clean(row.get("branch"))
            group = _clean(row.get("raw_fields", {}).get("group_idx"))
            if branch and group:
                branch_groups[branch].add(group)
                group_branches[group].add(branch)
    branch_errors = [
        f"branch {branch} maps to {len(groups)} official group identifiers"
        for branch, groups in branch_groups.items()
        if len(groups) != 1
    ]
    branch_errors.extend(
        f"group {group} maps to {len(branches)} branch names"
        for group, branches in group_branches.items()
        if len(branches) != 1
    )
    detail_errors.extend(branch_errors)

    details_complete = bool(
        list_complete
        and detail_attempts == detail_required
        and detail_pages == detail_required
        and not detail_errors
        and not detail_cap_reached
        and all(row.get("raw_fields", {}).get("detail_valid") is True for row in current_rows)
    )
    all_errors = list(dict.fromkeys([*errors, *detail_errors]))
    snapshot_complete = bool(list_complete and details_complete and not all_errors)

    if dedupe_rows is not None and snapshot_complete:
        try:
            deduped = list(dedupe_rows(current_rows))
            duplicate_count += max(0, len(current_rows) - len(deduped))
            current_rows = deduped
        except Exception as exc:
            all_errors.append(f"dedupe_rows {type(exc).__name__}")
            snapshot_complete = False
    if not snapshot_complete:
        current_rows = []

    clean_rows = [_clean_row(row) for row in current_rows]
    status_counts = Counter(_clean(row.get("status")) for row in clean_rows)
    source_status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status")) for row in clean_rows
    )
    branch_counts = Counter(_clean(row.get("branch")) for row in clean_rows)
    no_current_data = snapshot_complete and not clean_rows
    meta: dict[str, Any] = {
        "pages": pages_fetched,
        "declared_pages": max(0, declared_pages),
        "detail_pages": detail_pages,
        "detail_attempts": detail_attempts,
        "detail_required_count": detail_required,
        "required_detail_count": detail_required,
        "detail_errors": len(detail_errors),
        "pagination_detected": declared_pages > 1,
        "pagination_complete": list_complete,
        "pagination_exhausted": list_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached or detail_cap_reached,
        "recursion_depth": 0,
        "source_total": max(0, declared_total),
        "total_count": max(0, declared_total),
        "discovered_links": len(seen_ids),
        "listed_unique_count": len(seen_ids),
        "expired_count": expired_count,
        "invalid_period_count": invalid_period_count,
        "current_candidate_count": detail_required,
        "current_count": len(clean_rows),
        "duplicate_count": duplicate_count,
        "branch_count": len(branch_counts),
        "branch_counts": dict(branch_counts),
        "status_counts": dict(status_counts),
        "source_status_counts": dict(source_status_counts),
        "no_current_data": no_current_data,
        "no_current_reason": (
            "official Songpa integrated education source has no current/future courses"
            if no_current_data
            else ""
        ),
        "full_snapshot_required": True,
    }
    if all_errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(all_errors))
    return clean_rows, SONGPA_PARSER, meta


collect_songpa_integrated = collect_songpa_education_courses


__all__ = [
    "SONGPA_DETAIL_API_URL",
    "SONGPA_EDUCATION_PROVIDER",
    "SONGPA_EDUCATION_URL",
    "SONGPA_FUTURE_HORIZON",
    "SONGPA_MUNICIPALITY_CODE",
    "SONGPA_MUNICIPALITY_NAME",
    "SONGPA_PAGE_SIZE",
    "SONGPA_PARSER",
    "collect_songpa_education_courses",
    "collect_songpa_integrated",
    "is_songpa_education_target",
    "is_target",
    "songpa_detail_api_url",
    "songpa_detail_url",
]
