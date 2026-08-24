"""Complete, branch-aware collector for Goyang public-library lectures.

The official catalogue is one shared site.  It exposes a common partition and
18 library partitions through repeated ``manageCd`` parameters.  This collector
queries every partition in one bounded snapshot, proves every status partition
with an empty sentinel and stable edge pages, and then validates every current
or future public detail page.

Only structured public fields are retained.  Application forms, account pages,
attachments, instructor names, and free-form detail content are never fetched
or persisted.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GOYANG_LIBRARY_PROVIDER = "MUNI_WWW_GOYANGLIB_OR_KR_FFF7B1F8"
GOYANG_LIBRARY_URL = "https://www.goyanglib.or.kr/MH/program/lectureList.do"
GOYANG_LIBRARY_HOST = "www.goyanglib.or.kr"
GOYANG_LIBRARY_LIST_PATH = "/MH/program/lectureList.do"
GOYANG_LIBRARY_DETAIL_PATH = (
    "/MH/menu/11275/program/30013/lectureDetail.do"
)
GOYANG_LIBRARY_PARSER = (
    "goyang_library_all_branches_status_partitions"
    "+empty_sentinel+stable_edges+all_current_details"
)
GOYANG_LIBRARY_MUNICIPALITY_CODE = "4128100000"
GOYANG_LIBRARY_MUNICIPALITY_NAME = "경기도 고양시"

GOYANG_LIBRARY_MANAGE_CODES: Mapping[str, str] = {
    "AL": "고양시도서관 공통",
    "MT": "가좌도서관",
    "MV": "높빛도서관",
    "MJ": "대화도서관",
    "MN": "덕이도서관",
    "MA": "마두도서관",
    "MQ": "별꿈도서관",
    "MP": "삼송도서관",
    "MM": "식사도서관",
    "MO": "신원도서관",
    "MF": "아람누리도서관",
    "MU": "일산도서관",
    "MG": "주엽어린이도서관",
    "ML": "풍동도서관",
    "MK": "한뫼도서관",
    "MB": "행신도서관",
    "MH": "행신어린이도서관",
    "ME": "화정도서관",
    "MI": "화정어린이도서관",
}
GOYANG_LIBRARY_LABEL_CODES: Mapping[str, str] = {
    "공통": "AL",
    "가좌": "MT",
    "높빛": "MV",
    "대화": "MJ",
    "덕이": "MN",
    "마두": "MA",
    "별꿈": "MQ",
    "삼송": "MP",
    "식사": "MM",
    "신원": "MO",
    "아람누리": "MF",
    "일산": "MU",
    "주엽어린이": "MG",
    "풍동": "ML",
    "한뫼": "MK",
    "행신": "MB",
    "행신어린이": "MH",
    "화정": "ME",
    "화정어린이": "MI",
}
GOYANG_LIBRARY_STATUS_PARTITIONS = (
    "apply",
    "ready",
    "wait",
    "finish",
    "offline",
)
GOYANG_LIBRARY_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "대기접수": "WAITLIST",
    "대기자접수": "WAITLIST",
    "접수마감": "CLOSED",
    "마감": "CLOSED",
    "현장참여": "OPEN",
    "종료": "CLOSED",
}

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"fnDetail\(\s*['\"](\d+)['\"]\s*\)")
_PAGE_RE = re.compile(r"fnList\(\s*(\d+)\s*\)")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(?!\d)"
)
_DATETIME_RE = re.compile(
    r"(?<!\d)(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})"
    r"\s+([01]?\d|2[0-3]):([0-5]\d)(?!\d)"
)
_TIME_RE = re.compile(
    r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)\s*~\s*"
    r"([01]?\d|2[0-3]):([0-5]\d)(?!\d)"
)
_CAPACITY_RE = re.compile(
    r"신청(?:자수)?\s*:\s*([\d,]+)\s*/\s*([\d,]+)"
)
_WAITLIST_RE = re.compile(
    r"대기(?:자수)?\s*:\s*([\d,]+)\s*/\s*([\d,]+)"
)


class GoyangLibraryContractError(ValueError):
    """Raised when the official public catalogue no longer matches its contract."""


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


def is_goyang_library_target(target: Any) -> bool:
    """Match only the exact provider-owned official list route."""

    return (
        _provider(target) == GOYANG_LIBRARY_PROVIDER
        and _target_url(target) == GOYANG_LIBRARY_URL
    )


is_target = is_goyang_library_target


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


def _default_fetcher(current_session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = current_session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        raise GoyangLibraryContractError("empty HTTP response")
    return BeautifulSoup(response.content, "lxml")


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher did not return HTML or BeautifulSoup")
    return BeautifulSoup(content, "lxml")


def _fetch(
    fetcher: Fetcher,
    current_session: Any,
    url: str,
    timeout: int,
) -> BeautifulSoup:
    return _coerce_soup(fetcher(current_session, url, timeout))


def _fetch_with_retry(
    fetcher: Fetcher,
    current_session: Any,
    url: str,
    timeout: int,
    *,
    request_delay: float,
) -> BeautifulSoup:
    for attempt in range(3):
        try:
            soup = _fetch(fetcher, current_session, url, timeout)
            if request_delay:
                time.sleep(request_delay)
            return soup
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(0.5 * (2**attempt))
    raise AssertionError("unreachable retry state")


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


def goyang_library_list_url(status: str, page_no: int) -> str:
    if status not in GOYANG_LIBRARY_STATUS_PARTITIONS:
        raise ValueError(f"unsupported Goyang library status partition: {status}")
    if not isinstance(page_no, int) or isinstance(page_no, bool) or page_no < 1:
        raise ValueError("page_no must be a positive integer")
    query: list[tuple[str, str]] = [
        ("currentPageNo", str(page_no)),
        ("lectureIdx", "0"),
        ("targetAll", "Y"),
        ("lectureStatusCd", status),
    ]
    query.extend(("manageCd", code) for code in GOYANG_LIBRARY_MANAGE_CODES)
    return f"https://{GOYANG_LIBRARY_HOST}{GOYANG_LIBRARY_LIST_PATH}?{urlencode(query)}"


def goyang_library_detail_url(program_id: str) -> str:
    identity = _clean(program_id)
    if not identity.isdigit():
        return ""
    query = urlencode((("lectureIdx", identity), ("currentPageNo", "1")))
    return (
        f"https://{GOYANG_LIBRARY_HOST}{GOYANG_LIBRARY_DETAIL_PATH}?{query}"
    )


def _page_state_errors(
    soup: BeautifulSoup,
    *,
    status: str,
    page_no: int,
) -> list[str]:
    errors: list[str] = []
    current = soup.select_one("input[name=currentPageNo]")
    if _clean(current.get("value") if current else "") != str(page_no):
        errors.append(f"{status} page {page_no}: current-page marker mismatch")

    selected_statuses = {
        _clean(option.get("value"))
        for option in soup.select("select[name=lectureStatusCd] option[selected]")
    }
    if selected_statuses != {status}:
        errors.append(f"{status} page {page_no}: status selection mismatch")

    checked_codes = {
        _clean(node.get("value"))
        for node in soup.select("input[name=manageCd][checked]")
    }
    if checked_codes != set(GOYANG_LIBRARY_MANAGE_CODES):
        errors.append(f"{status} page {page_no}: library selection mismatch")

    checked_targets = {
        _clean(node.get("value"))
        for node in soup.select("input[name=targetAll][checked]")
    }
    if checked_targets != {"Y"}:
        errors.append(f"{status} page {page_no}: target-all selection mismatch")
    return errors


def _declared_pages(soup: BeautifulSoup) -> int:
    pages = {1}
    for anchor in soup.select(".pagingWrap a[href]"):
        match = _PAGE_RE.search(_clean(anchor.get("href")))
        if match:
            pages.add(int(match.group(1)))
    return max(pages)


def _without_nodes(node: Any, selectors: str) -> str:
    clone = BeautifulSoup(str(node), "lxml")
    for child in clone.select(selectors):
        child.decompose()
    return _clean(clone.get_text(" ", strip=True))


def _labelled_info(item: Any) -> tuple[dict[str, str], str]:
    pairs: dict[str, str] = {}
    schedule = ""
    for box in item.select(".infoBox .info"):
        for span in box.find_all("span", recursive=False):
            label = span.select_one(".tit")
            if label is None:
                candidate = _clean(span.get_text(" ", strip=True))
                if candidate and _TIME_RE.search(candidate):
                    schedule = candidate
                continue
            key = _clean(label.get_text(" ", strip=True))
            value = _without_nodes(span, ".tit").lstrip(": ").strip()
            if key and value:
                if key in pairs and pairs[key] != value:
                    raise GoyangLibraryContractError(
                        f"duplicate list field {key!r} has conflicting values"
                    )
                pairs[key] = value
    return pairs, schedule


def _date_range(value: Any) -> tuple[str, str]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) not in {1, 2}:
        return "", ""
    try:
        values = [
            date(int(year), int(month), int(day)).isoformat()
            for year, month, day in matches
        ]
    except ValueError:
        return "", ""
    return values[0], values[-1]


def _datetime_range(value: Any) -> tuple[str, str]:
    matches = _DATETIME_RE.findall(_clean(value))
    if len(matches) != 2:
        return "", ""
    try:
        values = [
            datetime(
                int(year),
                int(month),
                int(day),
                int(hour),
                int(minute),
            ).strftime("%Y-%m-%d %H:%M")
            for year, month, day, hour, minute in matches
        ]
    except ValueError:
        return "", ""
    return values[0], values[1]


def _time_range(value: Any) -> tuple[str, str]:
    match = _TIME_RE.search(_clean(value))
    if not match:
        return "", ""
    return (
        f"{int(match.group(1)):02d}:{match.group(2)}",
        f"{int(match.group(3)):02d}:{match.group(4)}",
    )


def _capacity(value: Any) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    text = _clean(value)
    regular = _CAPACITY_RE.search(text)
    waiting = _WAITLIST_RE.search(text)
    if regular is None:
        return None, None, None, None
    current = int(regular.group(1).replace(",", ""))
    total = int(regular.group(2).replace(",", ""))
    wait_current = int(waiting.group(1).replace(",", "")) if waiting else 0
    wait_total = int(waiting.group(2).replace(",", "")) if waiting else 0
    return current, total, wait_current, wait_total


def _program_id(item: Any) -> str:
    anchor = item.select_one(".title a[onclick]")
    match = _IDENTITY_RE.search(_clean(anchor.get("onclick") if anchor else ""))
    return match.group(1) if match else ""


def _list_record(item: Any) -> dict[str, Any]:
    identity = _program_id(item)
    title_node = item.select_one(".title a")
    library_node = item.select_one(".location .lib")
    fee_node = item.select_one(".location span[class*='payment']")
    status_node = item.select_one(".statusBox > span.status")
    count_node = item.select_one(".statusBox > span.count")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    library_label = _clean(
        library_node.get_text(" ", strip=True) if library_node else ""
    )
    fee_badge = _clean(fee_node.get_text(" ", strip=True) if fee_node else "")
    source_status = _clean(
        status_node.get_text(" ", strip=True) if status_node else ""
    )
    count_text = _clean(
        count_node.get_text(" ", strip=True) if count_node else ""
    )
    fields, schedule = _labelled_info(item)
    target_badges = [
        _clean(node.get_text(" ", strip=True))
        for node in item.select(".location .target")
        if _clean(node.get_text(" ", strip=True))
    ]
    start_date, end_date = _date_range(fields.get("강의기간"))
    apply_start, apply_end = _datetime_range(fields.get("접수기간"))
    time_start, time_end = _time_range(schedule)
    capacity_current, capacity_total, wait_current, wait_total = _capacity(
        count_text
    )

    code = GOYANG_LIBRARY_LABEL_CODES.get(library_label, "")
    required = {
        "program_id": identity,
        "title": title,
        "library_code": code,
        "fee_badge": fee_badge,
        "source_status": (
            source_status if source_status in GOYANG_LIBRARY_STATUS_MAP else ""
        ),
        "target": (
            fields.get("참여대상", "")
            or fields.get("대상연령", "")
            or ", ".join(target_badges)
        ),
        "venue": fields.get("장소", ""),
        "course_start": start_date,
        "course_end": end_date,
        "schedule": schedule,
        "time_start": time_start,
        "time_end": time_end,
        "apply_start": apply_start,
        "apply_end": apply_end,
    }
    missing = [key for key, value in required.items() if not value]
    if (
        source_status != "현장참여"
        and (capacity_current is None or capacity_total is None)
    ):
        missing.append("capacity")
    if missing:
        raise GoyangLibraryContractError(
            f"malformed list row {identity or '<unknown>'}: {','.join(missing)}"
        )

    return {
        **required,
        "library_label": library_label,
        "course_period": _clean(fields.get("강의기간")),
        "apply_period": _clean(fields.get("접수기간")),
        "capacity_text": count_text,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_current": wait_current,
        "waitlist_total": wait_total,
    }


def _record_fingerprint(record: Mapping[str, Any]) -> tuple[str, ...]:
    keys = (
        "title",
        "library_code",
        "library_label",
        "fee_badge",
        "source_status",
        "target",
        "venue",
        "course_period",
        "schedule",
        "apply_period",
        "capacity_text",
    )
    return tuple(_clean(record.get(key)) for key in keys)


def _item_ids(soup: BeautifulSoup) -> tuple[str, ...]:
    identities: list[str] = []
    for item in soup.select(".programList .program-item"):
        identity = _program_id(item)
        if not identity:
            raise GoyangLibraryContractError(
                "stable-edge page contains an item without an official identity"
            )
        identities.append(identity)
    return tuple(identities)


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in soup.select(".article-view > ul > li"):
        label = item.select_one("strong")
        if label is None:
            continue
        key = _clean(label.get_text(" ", strip=True))
        value = _without_nodes(item, "strong, .tblBtn")
        if key in pairs and pairs[key] != value:
            raise GoyangLibraryContractError(
                f"duplicate detail field {key!r} has conflicting values"
            )
        pairs[key] = value
    return pairs


def _detail_title_and_library(soup: BeautifulSoup) -> tuple[str, str]:
    node = soup.select_one(".article-viewTit")
    if node is None:
        return "", ""
    library = node.select_one(".lib")
    return (
        _without_nodes(node, ".lib, .rt"),
        _clean(library.get_text(" ", strip=True) if library else ""),
    )


def _detail_status(soup: BeautifulSoup) -> str:
    node = soup.select_one(".article-view > ul > li .tblBtn")
    return _clean(node.get_text(" ", strip=True) if node else "")


def _branch(record: Mapping[str, Any], venue: str) -> tuple[str, str]:
    code = _clean(record.get("library_code"))
    if code and code != "AL":
        return GOYANG_LIBRARY_MANAGE_CODES[code], code

    matches = [
        (branch_name, branch_code)
        for branch_code, branch_name in GOYANG_LIBRARY_MANAGE_CODES.items()
        if branch_code != "AL" and branch_name in venue
    ]
    if len(matches) == 1:
        return matches[0]
    return GOYANG_LIBRARY_MANAGE_CODES["AL"], "AL"


def _branch_code(code: str) -> str:
    return f"GOYANG_LIBRARY_{code}"


def _base_row(
    target: Any,
    record: Mapping[str, Any],
    *,
    branch: str,
    branch_source_code: str,
    detail_url: str,
) -> dict[str, Any]:
    provider = _provider(target)
    identity = _clean(record.get("program_id"))
    return {
        "provider": provider,
        "provider_course_id": f"{provider}:lecture:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(record.get("title")),
        "branch": branch,
        "branch_code": _branch_code(branch_source_code),
        "preserve_branch": True,
        "branch_url": GOYANG_LIBRARY_URL,
        "program_type": "강좌",
        "category": "교육·강좌",
        "category_raw": "도서관 교육·강좌",
        "collection_category": "도서관",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공도서관",
        "source_group": "library",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "municipality_code": GOYANG_LIBRARY_MUNICIPALITY_CODE,
        "municipality_full_name": GOYANG_LIBRARY_MUNICIPALITY_NAME,
        "collection_type": GOYANG_LIBRARY_PARSER,
        "raw_url": detail_url,
        "status": GOYANG_LIBRARY_STATUS_MAP[_clean(record.get("source_status"))],
        "target": _clean(record.get("target")),
        "start_date": _clean(record.get("course_start")),
        "end_date": _clean(record.get("course_end")),
        "period": _clean(record.get("course_period")),
        "schedule_raw": _clean(record.get("schedule")),
        "schedule_time_start": _clean(record.get("time_start")),
        "schedule_time_end": _clean(record.get("time_end")),
        "apply_start": _clean(record.get("apply_start")),
        "apply_end": _clean(record.get("apply_end")),
        "capacity": _clean(record.get("capacity_text")),
        "capacity_current": record.get("capacity_current"),
        "capacity_total": record.get("capacity_total"),
        "capacity_remaining": (
            max(
                0,
                int(record["capacity_total"])
                - int(record["capacity_current"]),
            )
            if record.get("capacity_total") is not None
            and record.get("capacity_current") is not None
            else None
        ),
        "waitlist_current": record.get("waitlist_current"),
        "waitlist_total": record.get("waitlist_total"),
        "reservation_available": (
            _clean(record.get("source_status"))
            in {"접수중", "대기접수", "대기자접수", "현장참여"}
        ),
        "application_url": detail_url,
        "application_type": "OFFICIAL_DETAIL",
        "raw_fields": {
            "parser": GOYANG_LIBRARY_PARSER,
            "program_id": identity,
            "manage_code": _clean(record.get("library_code")),
            "branch_source_code": branch_source_code,
            "source_library_label": _clean(record.get("library_label")),
            "source_status": _clean(record.get("source_status")),
            "fee_badge": _clean(record.get("fee_badge")),
            "detail_required": True,
            "detail_valid": False,
        },
    }


def _enrich_detail(
    target: Any,
    record: Mapping[str, Any],
    soup: BeautifulSoup,
) -> dict[str, Any]:
    identity = _clean(record.get("program_id"))
    title, library_label = _detail_title_and_library(soup)
    pairs = _detail_pairs(soup)
    source_status = _detail_status(soup)
    required_keys = [
        "장소",
        "수강료",
        "재료비",
        "강의기간",
        "요일/시간",
        "접수기간",
    ]
    if source_status != "현장참여":
        required_keys.append("접수현황")
    missing = [key for key in required_keys if not _clean(pairs.get(key))]
    detail_target = _clean(
        pairs.get("참여대상")
        or pairs.get("대상연령")
        or record.get("target")
    )
    if not detail_target:
        missing.append("참여대상/대상연령")
    if missing:
        raise GoyangLibraryContractError(
            f"detail {identity}: missing structured fields {','.join(missing)}"
        )
    if title != _clean(record.get("title")):
        raise GoyangLibraryContractError(f"detail {identity}: title mismatch")
    if library_label != _clean(record.get("library_label")):
        raise GoyangLibraryContractError(
            f"detail {identity}: library label mismatch"
        )
    if source_status != _clean(record.get("source_status")):
        raise GoyangLibraryContractError(f"detail {identity}: status mismatch")
    if source_status not in GOYANG_LIBRARY_STATUS_MAP:
        raise GoyangLibraryContractError(
            f"detail {identity}: unknown status {source_status!r}"
        )

    course_start, course_end = _date_range(pairs["강의기간"])
    apply_start, apply_end = _datetime_range(pairs["접수기간"])
    time_start, time_end = _time_range(pairs["요일/시간"])
    comparisons = (
        ("course period", (course_start, course_end), (
            _clean(record.get("course_start")),
            _clean(record.get("course_end")),
        )),
        ("application period", (apply_start, apply_end), (
            _clean(record.get("apply_start")),
            _clean(record.get("apply_end")),
        )),
        ("time", (time_start, time_end), (
            _clean(record.get("time_start")),
            _clean(record.get("time_end")),
        )),
        ("schedule", _clean(pairs["요일/시간"]), _clean(record.get("schedule"))),
        ("venue", _clean(pairs["장소"]), _clean(record.get("venue"))),
    )
    for label, detail_value, list_value in comparisons:
        if detail_value != list_value:
            raise GoyangLibraryContractError(
                f"detail {identity}: {label} mismatch"
            )

    if source_status != "현장참여":
        detail_capacity = _capacity(pairs["접수현황"])
        list_capacity = (
            record.get("capacity_current"),
            record.get("capacity_total"),
            record.get("waitlist_current"),
            record.get("waitlist_total"),
        )
        if detail_capacity != list_capacity:
            raise GoyangLibraryContractError(
                f"detail {identity}: capacity mismatch"
            )

    fee = _clean(pairs["수강료"])
    fee_badge = _clean(record.get("fee_badge"))
    if fee_badge == "무료" and "무료" not in fee:
        raise GoyangLibraryContractError(
            f"detail {identity}: free/paid classification mismatch"
        )
    if fee_badge == "유료" and ("무료" in fee or not fee):
        raise GoyangLibraryContractError(
            f"detail {identity}: free/paid classification mismatch"
        )
    if fee_badge not in {"무료", "유료"}:
        raise GoyangLibraryContractError(
            f"detail {identity}: unknown fee badge {fee_badge!r}"
        )

    venue = _clean(pairs["장소"])
    branch, branch_source_code = _branch(record, venue)
    detail_url = goyang_library_detail_url(identity)
    if not detail_url:
        raise GoyangLibraryContractError(
            f"detail {identity}: unsafe official detail URL"
        )
    parsed = urlparse(detail_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != GOYANG_LIBRARY_HOST
        or parsed.path != GOYANG_LIBRARY_DETAIL_PATH
    ):
        raise GoyangLibraryContractError(
            f"detail {identity}: unsafe official detail route"
        )

    row = _base_row(
        target,
        record,
        branch=branch,
        branch_source_code=branch_source_code,
        detail_url=detail_url,
    )
    row.update(
        {
            "fee": fee,
            "material_fee": _clean(pairs["재료비"]),
            "target": detail_target,
            "venue_name": venue,
            "room": venue,
        }
    )
    row["raw_fields"].update(
        {
            "detail_valid": True,
            "detail_status": source_status,
            "detail_fee": fee,
            "detail_material_fee": _clean(pairs["재료비"]),
        }
    )
    return row


def _meta(
    *,
    rows: list[dict[str, Any]],
    errors: list[str],
    source_pages: int,
    source_exposed: int,
    unique_count: int,
    duplicate_count: int,
    current_count: int,
    expired_count: int,
    detail_attempts: int,
    sentinel_pages: int,
    stable_rechecks: int,
    partition_counts: Mapping[str, int],
    status_counts: Mapping[str, int],
    source_cap_reached: bool,
) -> dict[str, Any]:
    unique_errors = list(dict.fromkeys(_clean(error) for error in errors if error))
    snapshot_complete = not unique_errors and len(rows) == current_count
    result: dict[str, Any] = {
        "source_pages": source_pages,
        "source_total": unique_count,
        "source_exposed": source_exposed,
        "duplicate_partition_rows": duplicate_count,
        "current_count": current_count,
        "expired_count": expired_count,
        "returned_count": len(rows),
        "branch_count": len(
            {_clean(row.get("branch_code")) for row in rows if row.get("branch_code")}
        ),
        "detail_required_count": current_count,
        "detail_attempts": detail_attempts,
        "detail_pages": detail_attempts,
        "sentinel_pages": sentinel_pages,
        "stable_rechecks": stable_rechecks,
        "partition_counts": dict(partition_counts),
        "status_counts": dict(status_counts),
        "pagination_complete": snapshot_complete,
        "details_complete": snapshot_complete,
        "branches_separated": snapshot_complete,
        "pii_payload_persisted": False,
        "source_cap_reached": source_cap_reached,
        "snapshot_complete": snapshot_complete,
        "full_snapshot_validated": snapshot_complete,
        "no_current_data": snapshot_complete and not rows,
        "no_current_reason": (
            "official current/future Goyang library lecture catalogue is empty"
            if snapshot_complete and not rows
            else ""
        ),
        "error_kind": "" if snapshot_complete else "incomplete_snapshot",
    }
    if unique_errors:
        shown = unique_errors[:50]
        message = "; ".join(shown)
        if len(unique_errors) > len(shown):
            message += f"; ... {len(unique_errors) - len(shown)} more errors"
        result["configured_collection_error"] = message
    return result


def collect_goyang_library_courses(
    target: Any,
    timeout: int = 25,
    max_pages: int = 100,
    detail_limit: int = 500,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    request_delay: float = 0.1,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, branch-separated Goyang library snapshot."""

    errors: list[str] = []
    if not is_goyang_library_target(target):
        errors.append(
            "target does not match the provider-owned canonical Goyang library route"
        )
    if (
        not isinstance(max_pages, int)
        or isinstance(max_pages, bool)
        or max_pages < 1
    ):
        errors.append("max_pages must be a positive integer")
    if (
        not isinstance(detail_limit, int)
        or isinstance(detail_limit, bool)
        or detail_limit < 0
    ):
        errors.append("detail_limit must be a non-negative integer")
    if (
        not isinstance(request_delay, (int, float))
        or isinstance(request_delay, bool)
        or not 0 <= float(request_delay) <= 2
    ):
        errors.append("request_delay must be between 0 and 2 seconds")

    fetch = fetcher or _default_fetcher
    make_session = session_factory or _default_session_factory
    cutoff = _today(today)
    records: dict[str, dict[str, Any]] = {}
    record_partitions: dict[str, set[str]] = {}
    partition_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    source_pages = 0
    source_exposed = 0
    duplicate_count = 0
    sentinel_pages = 0
    stable_rechecks = 0
    source_cap_reached = False
    session: Any = None

    try:
        if not errors:
            session = make_session()
            edges: dict[tuple[str, int], tuple[str, ...]] = {}
            for partition in GOYANG_LIBRARY_STATUS_PARTITIONS:
                try:
                    first = _fetch_with_retry(
                        fetch,
                        session,
                        goyang_library_list_url(partition, 1),
                        timeout,
                        request_delay=float(request_delay),
                    )
                except Exception as exc:
                    errors.append(
                        f"{partition} page 1 fetch {type(exc).__name__}"
                    )
                    break
                errors.extend(
                    _page_state_errors(first, status=partition, page_no=1)
                )
                declared_pages = _declared_pages(first)
                if source_pages + declared_pages > max_pages:
                    source_cap_reached = True
                    errors.append(
                        f"max_pages cap permits {max_pages} of at least "
                        f"{source_pages + declared_pages} partition pages"
                    )
                    break

                for page_no in range(1, declared_pages + 1):
                    if page_no == 1:
                        soup = first
                    else:
                        try:
                            soup = _fetch_with_retry(
                                fetch,
                                session,
                                goyang_library_list_url(partition, page_no),
                                timeout,
                                request_delay=float(request_delay),
                            )
                        except Exception as exc:
                            errors.append(
                                f"{partition} page {page_no} fetch "
                                f"{type(exc).__name__}"
                            )
                            break
                        errors.extend(
                            _page_state_errors(
                                soup,
                                status=partition,
                                page_no=page_no,
                            )
                        )
                    source_pages += 1
                    items = soup.select(".programList .program-item")
                    if not items and declared_pages > 1:
                        errors.append(
                            f"{partition} page {page_no}: unexpected empty page"
                        )
                    try:
                        page_ids = _item_ids(soup)
                    except Exception as exc:
                        errors.append(str(exc))
                        page_ids = ()
                    if len(set(page_ids)) != len(page_ids):
                        errors.append(
                            f"{partition} page {page_no}: duplicate IDs within page"
                        )
                    if page_no in {1, declared_pages}:
                        edges[(partition, page_no)] = page_ids

                    for item in items:
                        source_exposed += 1
                        partition_counts[partition] += 1
                        try:
                            record = _list_record(item)
                        except Exception as exc:
                            errors.append(
                                f"{partition} page {page_no}: {exc}"
                            )
                            continue
                        identity = record["program_id"]
                        existing = records.get(identity)
                        if existing is not None:
                            duplicate_count += 1
                            if _record_fingerprint(existing) != _record_fingerprint(
                                record
                            ):
                                errors.append(
                                    f"program {identity}: conflicting partition rows"
                                )
                            record_partitions[identity].add(partition)
                            continue
                        records[identity] = record
                        record_partitions[identity] = {partition}
                        status_counts[record["source_status"]] += 1
                    if errors:
                        break
                if errors:
                    break

                try:
                    sentinel = _fetch_with_retry(
                        fetch,
                        session,
                        goyang_library_list_url(
                            partition,
                            declared_pages + 1,
                        ),
                        timeout,
                        request_delay=float(request_delay),
                    )
                    sentinel_pages += 1
                    errors.extend(
                        _page_state_errors(
                            sentinel,
                            status=partition,
                            page_no=declared_pages + 1,
                        )
                    )
                    if sentinel.select(".programList .program-item"):
                        errors.append(
                            f"{partition}: page {declared_pages + 1} "
                            "is not an empty sentinel"
                        )
                except Exception as exc:
                    errors.append(
                        f"{partition} sentinel fetch {type(exc).__name__}"
                    )
                if errors:
                    break

                for edge_page in sorted({1, declared_pages}):
                    try:
                        stable = _fetch_with_retry(
                            fetch,
                            session,
                            goyang_library_list_url(partition, edge_page),
                            timeout,
                            request_delay=float(request_delay),
                        )
                        stable_rechecks += 1
                        errors.extend(
                            _page_state_errors(
                                stable,
                                status=partition,
                                page_no=edge_page,
                            )
                        )
                        if _item_ids(stable) != edges[(partition, edge_page)]:
                            errors.append(
                                f"{partition} page {edge_page}: unstable edge"
                            )
                    except Exception as exc:
                        errors.append(
                            f"{partition} stable page {edge_page} fetch "
                            f"{type(exc).__name__}"
                        )
                if errors:
                    break
    finally:
        _close_quietly(session)

    current_records = [
        record
        for record in records.values()
        if date.fromisoformat(record["course_end"]) >= cutoff
    ]
    expired_count = len(records) - len(current_records)
    if len(current_records) > detail_limit:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap permits {detail_limit} of "
            f"{len(current_records)} required detail pages"
        )

    rows: list[dict[str, Any]] = []
    detail_attempts = 0
    if not errors:
        detail_session: Any = None
        try:
            detail_session = make_session()
            for record in current_records:
                identity = record["program_id"]
                try:
                    detail_attempts += 1
                    detail = _fetch_with_retry(
                        fetch,
                        detail_session,
                        goyang_library_detail_url(identity),
                        timeout,
                        request_delay=float(request_delay),
                    )
                    rows.append(_enrich_detail(target, record, detail))
                except Exception as exc:
                    errors.append(f"detail {identity}: {exc}")
                    break
        finally:
            _close_quietly(detail_session)

    if not errors and dedupe_rows is not None:
        try:
            deduped = list(dedupe_rows(rows))
            if len(deduped) != len(rows):
                errors.append(
                    "downstream dedupe removed distinct official lecture IDs"
                )
            rows = deduped
        except Exception as exc:
            errors.append(f"dedupe_rows {type(exc).__name__}")

    if errors:
        rows = []
    meta = _meta(
        rows=rows,
        errors=errors,
        source_pages=source_pages,
        source_exposed=source_exposed,
        unique_count=len(records),
        duplicate_count=duplicate_count,
        current_count=len(current_records),
        expired_count=expired_count,
        detail_attempts=detail_attempts,
        sentinel_pages=sentinel_pages,
        stable_rechecks=stable_rechecks,
        partition_counts=partition_counts,
        status_counts=status_counts,
        source_cap_reached=source_cap_reached,
    )
    return rows, GOYANG_LIBRARY_PARSER, meta


collect_goyang_library_target = collect_goyang_library_courses
collect = collect_goyang_library_courses


__all__ = [
    "GOYANG_LIBRARY_DETAIL_PATH",
    "GOYANG_LIBRARY_HOST",
    "GOYANG_LIBRARY_LABEL_CODES",
    "GOYANG_LIBRARY_LIST_PATH",
    "GOYANG_LIBRARY_MANAGE_CODES",
    "GOYANG_LIBRARY_PARSER",
    "GOYANG_LIBRARY_PROVIDER",
    "GOYANG_LIBRARY_STATUS_MAP",
    "GOYANG_LIBRARY_STATUS_PARTITIONS",
    "GOYANG_LIBRARY_URL",
    "GoyangLibraryContractError",
    "collect",
    "collect_goyang_library_courses",
    "collect_goyang_library_target",
    "goyang_library_detail_url",
    "goyang_library_list_url",
    "is_goyang_library_target",
    "is_target",
]
