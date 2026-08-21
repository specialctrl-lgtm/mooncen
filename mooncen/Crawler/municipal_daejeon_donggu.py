"""Fail-closed collector for Daejeon Dong-gu's official course catalogue.

The lifelong-learning site exposes one municipal ``schType=lctr`` catalogue.
This collector owns that complete catalogue, not search-engine page fragments,
single details, or the navigation landing page.  A snapshot is returned only
after the advertised total reconciles with every data page, the immediate
post-last page is empty, page one is stable on re-read, and every current or
future row has a matching detail and application-control contract.

Contacts, instructors, arbitrary descriptions, attachments, and source HTML
are deliberately excluded from persisted rows.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


DAEJEON_DONGGU_PROVIDER = "MUNI_WWW_DONGGU_GO_KR_9A7A5E6F"
DAEJEON_DONGGU_CANONICAL_CANDIDATE_ID = "MUNI_IR_E2C39AFF585D"
DAEJEON_DONGGU_HOST = "www.donggu.go.kr"
DAEJEON_DONGGU_CODE = "3011000000"
DAEJEON_DONGGU_NAME = "대전광역시 동구"
DAEJEON_DONGGU_LIST_PATH = "/lll/www/selectUserEduList.do"
DAEJEON_DONGGU_DETAIL_PATH = "/lll/www/selectUserEduView.do"
DAEJEON_DONGGU_CANONICAL_URL = (
    f"https://{DAEJEON_DONGGU_HOST}{DAEJEON_DONGGU_LIST_PATH}?key=733"
)
DAEJEON_DONGGU_PAGE_SIZE = 10
DAEJEON_DONGGU_MAX_WORKERS = 8
DAEJEON_DONGGU_RETRY_WORKERS = 3
DAEJEON_DONGGU_FETCH_ATTEMPTS = 2
DAEJEON_DONGGU_PARSER = (
    "daejeon_donggu_single_official_lecture_catalogue+declared_total+"
    "empty_sentinel+stable_recheck+all_current_details+pii_allowlist"
)

DAEJEON_DONGGU_NON_EXECUTING_ALIASES: tuple[Mapping[str, str], ...] = (
    {
        "provider": "MUNI_WWW_DONGGU_GO_KR_5ED157FD",
        "url": "https://www.donggu.go.kr/lll/www/index.do",
        "ownership": "navigation_shell",
    },
    {
        "provider": "MUNI_WWW_DONGGU_GO_KR_50B1CF0F",
        "url": (
            "https://www.donggu.go.kr/lll/www/selectUserEduList.do?"
            "schId=&key=733&page=177&schDeptUserId=&schDeptType=&"
            "schType=lctr&schStr=&searchCondition1=&pageUnit=20"
        ),
        "ownership": "pagination_fragment",
    },
    {
        "provider": "MUNI_WWW_DONGGU_GO_KR_D309C9D9",
        "url": (
            "https://www.donggu.go.kr/lll/www/selectUserEduView.do?"
            "key=733&schStr=&searchCondition1=&pageUnit=20&schId=1822"
        ),
        "ownership": "single_detail",
    },
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_SHORT_DATE_RE = re.compile(
    r"(?<!\d)(\d{2})\s*[.]\s*(\d{1,2})\s*[.]\s*(\d{1,2})(?!\d)"
)
_FULL_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_IDENTITY_RE = re.compile(r"fn_goView\(\s*['\"]?(\d{1,20})['\"]?\s*\)")
_CAPACITY_RE = re.compile(r"\[?\s*([\d,]+)\s*/\s*([\d,]+)\s*명?\s*\]?")
_LIST_CAPACITY_RE = re.compile(
    r"모집\s*([\d,]+)\s*명.*?대기\s*([\d,]+)\s*명",
    flags=re.DOTALL,
)
_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*개\s*강좌")
_PAGE_RE = re.compile(r"\(\s*([\d,]+)\s*/\s*([\d,]+)\s*페이지\s*\)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIST_STATUS_MAP: Mapping[str, str] = {
    "모집중": "OPEN",
    "모집전": "SCHEDULED",
    "신청마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
}
_DETAIL_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
}
DAEJEON_DONGGU_RAW_FIELD_ALLOWLIST = frozenset(
    {
        "identity",
        "source_page",
        "source_status",
        "detail_source_status",
        "source_application_period",
        "source_education_period",
        "application_control_present",
        "application_control_contract",
        "detail_verified",
        "ownership_source_path",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "teacher",
        "phone",
        "contact",
        "contact_phone",
        "email",
        "description",
        "description_html",
        "source_html",
        "attachments",
    }
)


class DaejeonDongguContractError(RuntimeError):
    """Raised when the official catalogue no longer matches its contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_daejeon_donggu_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != DAEJEON_DONGGU_PROVIDER:
        return False
    raw_url = _clean(_target_value(target, "url"))
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return False
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != DAEJEON_DONGGU_HOST
        or parsed.path != DAEJEON_DONGGU_LIST_PATH
        or parsed.fragment
    ):
        return False
    # The canonical owner is deliberately one exact, unfiltered list URL.
    return parsed.query == "key=733"


is_target = is_daejeon_donggu_education_target


def daejeon_donggu_list_url(page: int) -> str:
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    query = (
        ("key", "733"),
        ("page", str(page)),
        ("schDeptUserId", ""),
        ("schDeptType", ""),
        ("schType", "lctr"),
        ("schStr", ""),
        ("searchCondition1", ""),
    )
    return (
        f"https://{DAEJEON_DONGGU_HOST}{DAEJEON_DONGGU_LIST_PATH}?"
        + urlencode(query)
    )


def daejeon_donggu_detail_url(identity: Any, page: int = 1) -> str:
    value = _clean(identity)
    if not re.fullmatch(r"\d{1,20}", value):
        raise ValueError("invalid Daejeon Dong-gu course identity")
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    return (
        f"https://{DAEJEON_DONGGU_HOST}{DAEJEON_DONGGU_DETAIL_PATH}?"
        + urlencode((('key', '733'), ('page', str(page)), ('schId', value)))
    )


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise DaejeonDongguContractError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = getattr(value, "status_code", None)
    if status is not None and int(status) != 200:
        raise DaejeonDongguContractError(f"unexpected HTTP status {status}")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location"):
        raise DaejeonDongguContractError("redirect response is not accepted")
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise DaejeonDongguContractError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _fetch(
    fetcher: Optional[Fetcher], current_session: Any, url: str, timeout: int
) -> BeautifulSoup:
    last_error: Optional[Exception] = None
    for _attempt in range(DAEJEON_DONGGU_FETCH_ATTEMPTS):
        try:
            if fetcher is None:
                response = current_session.get(
                    url, timeout=timeout, allow_redirects=False
                )
                return _coerce_soup(response)
            return _coerce_soup(fetcher(current_session, url, timeout))
        except Exception as exc:  # fail closed after a bounded retry
            last_error = exc
    raise DaejeonDongguContractError(f"fetch failed for {url}: {last_error}")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _short_range(value: Any) -> tuple[str, str]:
    dates = _SHORT_DATE_RE.findall(_clean(value))
    if len(dates) != 2:
        return "", ""
    parsed: list[str] = []
    for year, month, day in dates:
        try:
            parsed.append(date(2000 + int(year), int(month), int(day)).isoformat())
        except ValueError:
            return "", ""
    return parsed[0], parsed[1]


def _full_range(value: Any) -> tuple[str, str]:
    dates = _FULL_DATE_RE.findall(_clean(value))
    if len(dates) != 2:
        return "", ""
    parsed: list[str] = []
    for year, month, day in dates:
        try:
            parsed.append(date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            return "", ""
    return parsed[0], parsed[1]


def _period(start: str, end: str) -> str:
    return f"{start} ~ {end}" if start and end else ""


def _course_id(identity: str) -> str:
    token = f"{DAEJEON_DONGGU_PROVIDER}|{identity}"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]


def _list_template_errors(soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "수강신청 - 대전동구평생학습":
        errors.append("unexpected official page title")
    form = soup.select_one("form#searchForm")
    if form is None:
        errors.append("missing canonical search form")
    else:
        key = form.select_one("input[name='key']")
        if _clean(key.get("value") if key else "") != "733":
            errors.append("canonical key contract changed")
        if form.select_one("input[name='schType'][value='lctr']") is None:
            errors.append("lecture catalogue filter contract changed")
    headers = [
        _clean(node.get_text(" ", strip=True))
        for node in soup.select("table.edu_list_table thead th")
    ]
    if headers != [
        "상태",
        "교육명/장소",
        "교육장소",
        "대상",
        "신청/교육기간",
        "정원",
        "수강료",
    ]:
        errors.append("official list headers changed")
    return errors


def _declared_total_and_last_page(soup: BeautifulSoup) -> tuple[int, int]:
    count = soup.select_one("p.count")
    text = _clean(count.get_text(" ", strip=True) if count else "")
    total_match = _TOTAL_RE.search(text)
    page_match = _PAGE_RE.search(text)
    if total_match is None or page_match is None:
        raise DaejeonDongguContractError("missing declared total/page count")
    total = int(total_match.group(1).replace(",", ""))
    current = int(page_match.group(1).replace(",", ""))
    declared_last = int(page_match.group(2).replace(",", ""))
    expected_last = max(1, math.ceil(total / DAEJEON_DONGGU_PAGE_SIZE))
    if declared_last != expected_last:
        raise DaejeonDongguContractError("declared last page disagrees with total")
    if current < 1:
        raise DaejeonDongguContractError("invalid current page marker")
    return total, declared_last


def _list_rows(
    soup: BeautifulSoup, source_page: int
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for row_number, tr in enumerate(
        soup.select("table.edu_list_table tbody tr"), start=1
    ):
        cells = tr.find_all("td", recursive=False)
        if (
            len(cells) == 1
            and _clean(cells[0].get("colspan")) == "7"
            and _clean(cells[0].get_text(" ", strip=True))
            == "검색 결과가 존재하지 않습니니다."
        ):
            # The official template contains this long-standing typo on an
            # empty result page.  It is a sentinel, not a malformed course.
            continue
        if len(cells) != 8:
            errors.append(f"page {source_page} row {row_number}: unexpected cell count")
            continue
        links = tr.find_all("a", onclick=True)
        identities = {
            match.group(1)
            for link in links
            if (match := _IDENTITY_RE.fullmatch(_clean(link.get("onclick"))))
        }
        if len(identities) != 1:
            errors.append(f"page {source_page} row {row_number}: invalid identity links")
            continue
        identity = next(iter(identities))
        title_node = cells[1].select_one("strong.edu_tit")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        venue_node = cells[1].select_one("span.clearfix")
        venue = _clean(venue_node.get_text(" ", strip=True) if venue_node else "")
        branch = _clean(cells[2].get_text(" ", strip=True))
        target = re.sub(
            r"^모집대상\s*:\s*", "", _clean(cells[3].get_text(" ", strip=True))
        )
        source_status = _clean(cells[0].get_text(" ", strip=True))
        status = _LIST_STATUS_MAP.get(source_status, "")
        if not title or not branch or not status:
            errors.append(
                f"page {source_page} identity {identity}: missing title/branch/status"
            )
            continue
        date_values: dict[str, str] = {}
        for item in cells[4].select("span.edu_time_sp"):
            label_node = item.select_one("em.edu_time_tit")
            label = _clean(
                label_node.get_text(" ", strip=True) if label_node else ""
            )
            value_node = item.find("span")
            date_values[label] = _clean(
                value_node.get_text(" ", strip=True) if value_node else ""
            )
        apply_start, apply_end = _short_range(date_values.get("신청기간"))
        start_date, end_date = _short_range(date_values.get("교육기간"))
        if not all((apply_start, apply_end, start_date, end_date)):
            errors.append(f"page {source_page} identity {identity}: invalid periods")
            continue
        if apply_start > apply_end or start_date > end_date:
            errors.append(f"page {source_page} identity {identity}: reversed periods")
            continue
        capacity_match = _LIST_CAPACITY_RE.search(
            _clean(cells[5].get_text(" ", strip=True))
        )
        if capacity_match is None:
            errors.append(f"page {source_page} identity {identity}: invalid capacity")
            continue
        capacity_total = int(capacity_match.group(1).replace(",", ""))
        waitlist_total = int(capacity_match.group(2).replace(",", ""))
        fee = re.sub(
            r"^수강료\s*", "", _clean(cells[6].get_text(" ", strip=True))
        )
        raw_url = daejeon_donggu_detail_url(identity, source_page)
        rows.append(
            {
                "provider": DAEJEON_DONGGU_PROVIDER,
                "provider_course_id": _course_id(identity),
                "title": title,
                "branch": branch,
                "municipality_code": DAEJEON_DONGGU_CODE,
                "municipality_full_name": DAEJEON_DONGGU_NAME,
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFORMATION_ONLY",
                "reservation_available": False,
                "status": status,
                "start_date": start_date,
                "end_date": end_date,
                "period": _period(start_date, end_date),
                "apply_start_date": apply_start,
                "apply_end_date": apply_end,
                "apply_period": _period(apply_start, apply_end),
                "schedule_raw": "",
                "target": target,
                "room": venue,
                "venue_name": venue,
                "fee": fee,
                "capacity": capacity_total,
                "capacity_total": capacity_total,
                "waitlist_total": waitlist_total,
                "raw_fields": {
                    "identity": identity,
                    "source_page": source_page,
                    "source_status": source_status,
                    "source_application_period": date_values.get("신청기간", ""),
                    "source_education_period": date_values.get("교육기간", ""),
                    "application_control_present": False,
                    "application_control_contract": "list_control_is_navigation_only",
                    "detail_verified": False,
                    "ownership_source_path": DAEJEON_DONGGU_LIST_PATH,
                },
            }
        )
    return rows, errors


def _main_detail_pairs(container: Any) -> dict[str, Any]:
    pairs: dict[str, Any] = {}
    for th in container.find_all("th"):
        label = _clean(th.get_text(" ", strip=True))
        if label in pairs:
            continue
        td = th.find_next_sibling("td")
        if td is not None:
            pairs[label] = td
    return pairs


def _application_controls(container: Any) -> list[Any]:
    controls: list[Any] = []
    for node in container.select("a, button"):
        if _clean(node.get_text(" ", strip=True)) != "강의신청":
            continue
        onclick = _clean(node.get("onclick"))
        if node.name == "a" and node.get("href") == "#n" and onclick == "fn_login_move()":
            controls.append(node)
        elif node.name == "button" and onclick == "fn_lecture_sp_req()":
            controls.append(node)
    return controls


def _enrich_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    container = soup.select_one(".sub_offline_view")
    if container is None:
        return ["missing official detail container"]
    form = soup.select_one("form#returnForm")
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    form_identity = form.select_one("input[name='schId']") if form else None
    form_key = form.select_one("input[name='key']") if form else None
    if (
        form is None
        or _clean(form_identity.get("value") if form_identity else "") != identity
        or _clean(form_key.get("value") if form_key else "") != "733"
    ):
        return ["detail identity/form contract mismatch"]
    title_node = container.select_one(".offline_edu_title h2") or container.find("h2")
    detail_title = _clean(
        title_node.get_text(" ", strip=True) if title_node else ""
    )
    if detail_title != _clean(row.get("title")):
        errors.append("detail title mismatch")
    branch_node = container.select_one("p.edu_wh")
    if _clean(branch_node.get_text(" ", strip=True) if branch_node else "") != _clean(
        row.get("branch")
    ):
        errors.append("detail branch mismatch")

    pairs = _main_detail_pairs(container)
    required = {
        "강의분류",
        "교육대상",
        "신청기간",
        "교육기간",
        "접수인원",
        "교육장소",
    }
    if not required.issubset(pairs):
        errors.append("missing required detail fields")
        return errors
    apply_node = pairs["신청기간"]
    detail_status_node = apply_node.select_one("span.edu_state")
    detail_source_status = _clean(
        detail_status_node.get_text(" ", strip=True) if detail_status_node else ""
    )
    detail_status = _DETAIL_STATUS_MAP.get(detail_source_status, "")
    if not detail_status or detail_status != row.get("status"):
        errors.append("detail status mismatch")
    detail_apply_start, detail_apply_end = _full_range(
        apply_node.get_text(" ", strip=True)
    )
    detail_start, detail_end = _full_range(
        pairs["교육기간"].get_text(" ", strip=True)
    )
    if (detail_apply_start, detail_apply_end) != (
        row.get("apply_start_date"),
        row.get("apply_end_date"),
    ):
        errors.append("detail application period mismatch")
    if (detail_start, detail_end) != (
        row.get("start_date"),
        row.get("end_date"),
    ):
        errors.append("detail education period mismatch")
    capacity_match = _CAPACITY_RE.search(
        _clean(pairs["접수인원"].get_text(" ", strip=True))
    )
    if capacity_match is None:
        errors.append("invalid detail capacity")
    else:
        capacity_current = int(capacity_match.group(1).replace(",", ""))
        capacity_total = int(capacity_match.group(2).replace(",", ""))
        if capacity_total != row.get("capacity_total"):
            errors.append("detail capacity total mismatch")
        row["capacity_current"] = capacity_current

    controls = _application_controls(container)
    should_have_control = row.get("status") == "OPEN"
    if should_have_control != bool(controls):
        errors.append("detail status/application control mismatch")
    if len(controls) > 1:
        errors.append("ambiguous detail application controls")
    if errors:
        return errors

    def text_for(label: str) -> str:
        node = pairs.get(label)
        return _clean(node.get_text(" ", strip=True) if node else "")

    row["category"] = text_for("강의분류")
    row["target"] = text_for("교육대상") or row.get("target", "")
    row["schedule_raw"] = text_for("교육시간")
    row["room"] = text_for("교육장소") or row.get("room", "")
    row["venue_name"] = row["room"]
    address = re.sub(r"^\[[^\]]+\]\s*", "", text_for("교육장소주소"))
    if address:
        row["venue_address"] = address
    material_fee = text_for("재료비")
    if material_fee:
        row["material_fee"] = material_fee
    material_note = text_for("재료비설명")
    if material_note:
        row["material_note"] = material_note
    if controls:
        row["application_url"] = row["raw_url"]
        row["application_type"] = "ONLINE_RESERVATION"
        row["reservation_available"] = True
    else:
        row["application_url"] = ""
        row["application_type"] = "INFORMATION_ONLY"
        row["reservation_available"] = False
    row["raw_fields"].update(
        {
            "detail_source_status": detail_source_status,
            "application_control_present": bool(controls),
            "application_control_contract": (
                "course_bound_public_control"
                if controls
                else "status_disallows_public_control"
            ),
            "detail_verified": True,
        }
    )
    return []


def _detail_bucket(
    bucket: list[tuple[int, dict[str, Any]]],
    *,
    fetcher: Optional[Fetcher],
    session_factory: SessionFactory,
    timeout: int,
) -> tuple[dict[int, dict[str, Any]], dict[int, str], int]:
    valid: dict[int, dict[str, Any]] = {}
    failures: dict[int, str] = {}
    attempts = 0
    current_session = session_factory()
    try:
        for index, source in bucket:
            candidate = dict(source)
            candidate["raw_fields"] = dict(source.get("raw_fields") or {})
            attempts += 1
            try:
                soup = _fetch(fetcher, current_session, source["raw_url"], timeout)
                errors = _enrich_detail(candidate, soup)
                soup.decompose()
            except Exception as exc:
                errors = [str(exc)]
            if errors:
                failures[index] = "; ".join(dict.fromkeys(errors))
            else:
                valid[index] = candidate
    finally:
        _close_quietly(current_session)
    return valid, failures, attempts


def _validate_details(
    rows: list[dict[str, Any]],
    *,
    fetcher: Optional[Fetcher],
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[list[dict[str, Any]], dict[int, str], int, int]:
    if not rows:
        return [], {}, 0, 0
    worker_count = max(1, min(int(max_workers), DAEJEON_DONGGU_MAX_WORKERS, len(rows)))
    buckets: list[list[tuple[int, dict[str, Any]]]] = [
        [] for _ in range(worker_count)
    ]
    for index, row in enumerate(rows):
        buckets[index % worker_count].append((index, row))
    valid: dict[int, dict[str, Any]] = {}
    failures: dict[int, str] = {}
    attempts = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                _detail_bucket,
                bucket,
                fetcher=fetcher,
                session_factory=session_factory,
                timeout=timeout,
            )
            for bucket in buckets
            if bucket
        ]
        for future in as_completed(futures):
            bucket_valid, bucket_failures, bucket_attempts = future.result()
            valid.update(bucket_valid)
            failures.update(bucket_failures)
            attempts += bucket_attempts
    retry_attempts = 0
    if failures:
        retry_indexes = sorted(failures)
        retry_rows = [rows[index] for index in retry_indexes]
        retried, retry_failures, retry_attempts = _validate_details_once(
            retry_rows,
            original_indexes=retry_indexes,
            fetcher=fetcher,
            session_factory=session_factory,
            timeout=timeout,
            max_workers=min(DAEJEON_DONGGU_RETRY_WORKERS, max_workers),
        )
        valid.update(retried)
        failures = retry_failures
    return [valid[index] for index in sorted(valid)], failures, attempts, retry_attempts


def _validate_details_once(
    rows: list[dict[str, Any]],
    *,
    original_indexes: list[int],
    fetcher: Optional[Fetcher],
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[int, dict[str, Any]], dict[int, str], int]:
    worker_count = max(1, min(int(max_workers), len(rows)))
    buckets: list[list[tuple[int, dict[str, Any]]]] = [
        [] for _ in range(worker_count)
    ]
    for offset, row in enumerate(rows):
        buckets[offset % worker_count].append((original_indexes[offset], row))
    valid: dict[int, dict[str, Any]] = {}
    failures: dict[int, str] = {}
    attempts = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                _detail_bucket,
                bucket,
                fetcher=fetcher,
                session_factory=session_factory,
                timeout=timeout,
            )
            for bucket in buckets
            if bucket
        ]
        for future in as_completed(futures):
            bucket_valid, bucket_failures, bucket_attempts = future.result()
            valid.update(bucket_valid)
            failures.update(bucket_failures)
            attempts += bucket_attempts
    return valid, failures, attempts


def _dedupe(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    duplicates = 0
    for row in rows:
        identity = _clean(row.get("raw_fields", {}).get("identity"))
        if not identity or identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        result.append(row)
    return result, duplicates


def _row_safety_errors(rows: Iterable[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for row in rows:
        identity = _clean(row.get("raw_fields", {}).get("identity")) or "unknown"
        forbidden = _FORBIDDEN_PERSISTED_KEYS.intersection(row)
        if forbidden:
            errors.append(f"{identity}: forbidden persisted fields {sorted(forbidden)}")
        raw_fields = row.get("raw_fields")
        if not isinstance(raw_fields, dict) or not set(raw_fields).issubset(
            DAEJEON_DONGGU_RAW_FIELD_ALLOWLIST
        ):
            errors.append(f"{identity}: raw_fields allowlist violation")
        safe_repr = repr(
            {
                key: value
                for key, value in row.items()
                if key not in {"raw_url", "application_url"}
            }
        )
        if _PHONE_RE.search(safe_repr) or _EMAIL_RE.search(safe_repr):
            errors.append(f"{identity}: contact data leaked into persisted row")
    return errors


def _today(value: Optional[Any]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(_clean(value))


def _empty_meta(error: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "physical_requests": 0,
        "detail_pages": 0,
        "detail_attempts": 0,
        "detail_retry_pages": 0,
        "detail_errors": 0,
        "discovered_links": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "duplicate_count": 0,
        "application_control_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "pagination_exhausted": False,
        "partitions_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "recursion_depth": 0,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": error,
    }


def collect_daejeon_donggu_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[Any] = None,
    max_workers: int = DAEJEON_DONGGU_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic snapshot of the official Dong-gu lecture store."""

    if not is_daejeon_donggu_education_target(target):
        return [], DAEJEON_DONGGU_PARSER, _empty_meta(
            "target is not the canonical Daejeon Dong-gu education owner"
        )
    if session_factory is None:
        return [], DAEJEON_DONGGU_PARSER, _empty_meta(
            "managed session_factory injection is required"
        )
    try:
        bounded_max_pages = int(max_pages)
        bounded_detail_limit = int(detail_limit)
        if bounded_max_pages < 2 or bounded_detail_limit < 0:
            raise ValueError
        reference_date = _today(today)
    except Exception:
        return [], DAEJEON_DONGGU_PARSER, _empty_meta(
            "invalid max-pages/detail-limit/today contract"
        )

    errors: list[str] = []
    all_rows: list[dict[str, Any]] = []
    list_requests = 0
    detail_attempts = 0
    detail_retry_pages = 0
    detail_errors = 0
    source_total = 0
    data_pages = 0
    sentinel_pages = 0
    stable_rechecks = 0
    source_cap_reached = False
    page_counts: dict[int, int] = {}
    first_page_signature: tuple[str, ...] = ()
    main_session = session_factory()
    try:
        first_soup = _fetch(
            fetcher, main_session, daejeon_donggu_list_url(1), timeout
        )
        list_requests += 1
        errors.extend(_list_template_errors(first_soup))
        try:
            source_total, data_pages = _declared_total_and_last_page(first_soup)
        except Exception as exc:
            errors.append(str(exc))
        first_rows, first_errors = _list_rows(first_soup, 1)
        errors.extend(first_errors)
        page_counts[1] = len(first_rows)
        all_rows.extend(first_rows)
        first_page_signature = tuple(
            _clean(row.get("raw_fields", {}).get("identity"))
            for row in first_rows
        )
        first_soup.decompose()

        required_last = data_pages + 1
        if required_last > bounded_max_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages {bounded_max_pages} cannot reach sentinel page {required_last}"
            )
        if not errors:
            for page in range(2, required_last + 1):
                soup = _fetch(
                    fetcher,
                    main_session,
                    daejeon_donggu_list_url(page),
                    timeout,
                )
                list_requests += 1
                errors.extend(_list_template_errors(soup))
                try:
                    page_total, page_last = _declared_total_and_last_page(soup)
                    if (page_total, page_last) != (source_total, data_pages):
                        errors.append(f"page {page}: declared total changed")
                except Exception as exc:
                    errors.append(f"page {page}: {exc}")
                page_rows, page_errors = _list_rows(soup, page)
                errors.extend(page_errors)
                page_counts[page] = len(page_rows)
                if page <= data_pages:
                    all_rows.extend(page_rows)
                elif page_rows:
                    errors.append("post-last sentinel page is not empty")
                else:
                    sentinel_pages += 1
                soup.decompose()

        if not errors:
            recheck = _fetch(
                fetcher, main_session, daejeon_donggu_list_url(1), timeout
            )
            list_requests += 1
            errors.extend(_list_template_errors(recheck))
            try:
                recheck_total, recheck_last = _declared_total_and_last_page(recheck)
            except Exception as exc:
                recheck_total, recheck_last = -1, -1
                errors.append(f"page-one recheck: {exc}")
            recheck_rows, recheck_errors = _list_rows(recheck, 1)
            errors.extend(recheck_errors)
            recheck_signature = tuple(
                _clean(row.get("raw_fields", {}).get("identity"))
                for row in recheck_rows
            )
            if (
                (recheck_total, recheck_last) != (source_total, data_pages)
                or recheck_signature != first_page_signature
            ):
                errors.append("page-one stable recheck failed")
            else:
                stable_rechecks = 1
            recheck.decompose()
    except Exception as exc:
        errors.append(str(exc))
    finally:
        _close_quietly(main_session)

    source_rows = len(all_rows)
    identity_counts = Counter(
        _clean(row.get("raw_fields", {}).get("identity")) for row in all_rows
    )
    duplicate_count = sum(count - 1 for count in identity_counts.values() if count > 1)
    if source_total != source_rows:
        errors.append(
            f"declared total {source_total} does not reconcile with {source_rows} rows"
        )
    if duplicate_count:
        errors.append(f"duplicate source identities: {duplicate_count}")
    expected_page_counts = {
        page: (
            DAEJEON_DONGGU_PAGE_SIZE
            if page < data_pages
            else source_total - DAEJEON_DONGGU_PAGE_SIZE * (data_pages - 1)
        )
        for page in range(1, data_pages + 1)
    }
    if data_pages and any(
        page_counts.get(page) != expected
        for page, expected in expected_page_counts.items()
    ):
        errors.append("data-page row counts do not reconcile with declared total")

    current_rows = [
        row
        for row in all_rows
        if row.get("end_date")
        and date.fromisoformat(str(row["end_date"])) >= reference_date
    ]
    expired_count = source_rows - len(current_rows)
    detailed_rows: list[dict[str, Any]] = []
    if len(current_rows) > bounded_detail_limit:
        source_cap_reached = True
        errors.append(
            f"detail_limit {bounded_detail_limit} cannot validate {len(current_rows)} current rows"
        )
    if not errors and current_rows:
        try:
            detailed_rows, failures, detail_attempts, detail_retry_pages = (
                _validate_details(
                    current_rows,
                    fetcher=fetcher,
                    session_factory=session_factory,
                    timeout=timeout,
                    max_workers=max_workers,
                )
            )
            detail_errors = len(failures)
            if failures:
                for index, message in sorted(failures.items()):
                    identity = _clean(
                        current_rows[index].get("raw_fields", {}).get("identity")
                    )
                    errors.append(f"detail {identity}: {message}")
        except Exception as exc:
            errors.append(f"detail validation failed: {exc}")
    elif not current_rows:
        detailed_rows = []

    safety_errors = _row_safety_errors(detailed_rows)
    errors.extend(safety_errors)
    internally_deduped, result_duplicate_count = _dedupe(detailed_rows)
    if result_duplicate_count:
        errors.append(f"detail result dedupe changed row count: {result_duplicate_count}")
    result = internally_deduped
    if dedupe_rows is not None and not errors:
        try:
            external = list(dedupe_rows(result))
            if len(external) != len(result):
                errors.append("external dedupe changed complete snapshot row count")
            else:
                result = external
        except Exception as exc:
            errors.append(f"external dedupe failed: {exc}")

    pagination_complete = (
        not source_cap_reached
        and data_pages >= 1
        and sentinel_pages == 1
        and stable_rechecks == 1
        and source_rows == source_total
        and duplicate_count == 0
        and all(
            page_counts.get(page) == expected
            for page, expected in expected_page_counts.items()
        )
    )
    details_complete = (
        detail_errors == 0
        and len(detailed_rows) == len(current_rows)
        and all(
            row.get("raw_fields", {}).get("detail_verified") for row in detailed_rows
        )
    )
    snapshot_complete = pagination_complete and details_complete and not errors
    if not snapshot_complete:
        result = []
    no_current_data = snapshot_complete and not current_rows
    meta = {
        "pages": list_requests,
        "list_requests": list_requests,
        "physical_requests": list_requests + detail_attempts + detail_retry_pages,
        "detail_pages": len(detailed_rows),
        "detail_attempts": detail_attempts,
        "detail_retry_pages": detail_retry_pages,
        "detail_errors": detail_errors,
        "discovered_links": source_rows,
        "source_total": source_total,
        "source_rows": source_rows,
        "source_page_counts": dict(sorted(page_counts.items())),
        "data_pages": data_pages,
        "sentinel_pages": sentinel_pages,
        "stable_rechecks": stable_rechecks,
        "current_count": len(current_rows),
        "returned_count": len(result),
        "expired_count": expired_count,
        "duplicate_count": duplicate_count,
        "application_control_count": sum(
            bool(row.get("application_url")) for row in result
        ),
        "status_counts": dict(sorted(Counter(row.get("status") for row in result).items())),
        "branch_counts": dict(sorted(Counter(row.get("branch") for row in result).items())),
        "pagination_detected": data_pages > 1,
        "pagination_complete": pagination_complete,
        "pagination_exhausted": pagination_complete,
        "partitions_complete": pagination_complete,
        "details_complete": details_complete,
        "snapshot_complete": snapshot_complete,
        "source_cap_reached": source_cap_reached,
        "worker_limit": DAEJEON_DONGGU_MAX_WORKERS,
        "detail_retry_worker_limit": DAEJEON_DONGGU_RETRY_WORKERS,
        "recursion_depth": 0,
        "no_current_data": no_current_data,
        "no_current_reason": (
            "the complete official Dong-gu catalogue contains only ended courses"
            if no_current_data
            else ""
        ),
        "configured_collection_error": "; ".join(dict.fromkeys(errors)),
    }
    return result, DAEJEON_DONGGU_PARSER, meta


collect_daejeon_donggu_target = collect_daejeon_donggu_education


__all__ = [
    "DAEJEON_DONGGU_CANONICAL_CANDIDATE_ID",
    "DAEJEON_DONGGU_CANONICAL_URL",
    "DAEJEON_DONGGU_CODE",
    "DAEJEON_DONGGU_HOST",
    "DAEJEON_DONGGU_MAX_WORKERS",
    "DAEJEON_DONGGU_NAME",
    "DAEJEON_DONGGU_NON_EXECUTING_ALIASES",
    "DAEJEON_DONGGU_PAGE_SIZE",
    "DAEJEON_DONGGU_PARSER",
    "DAEJEON_DONGGU_PROVIDER",
    "DAEJEON_DONGGU_RAW_FIELD_ALLOWLIST",
    "DaejeonDongguContractError",
    "collect_daejeon_donggu_education",
    "collect_daejeon_donggu_target",
    "daejeon_donggu_detail_url",
    "daejeon_donggu_list_url",
    "is_daejeon_donggu_education_target",
    "is_target",
]
