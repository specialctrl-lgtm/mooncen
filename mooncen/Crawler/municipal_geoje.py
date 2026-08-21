"""Fail-closed collector for Geoje City's lifelong-learning catalogue.

``lifelonggeoje.kr`` is the course ledger operated by Geoje City's Lifelong
Education Division.  The public list is a server-rendered POST catalogue.  It
publishes an exact total and last page, ten records per page, and a stable
course identity used by its public detail page.

The collector reads the complete unfiltered ledger, verifies the immediate
empty page after the advertised end and stable first/last boundaries, and
then opens details only for courses whose education period has not ended.
Applicant lists and the POST registration endpoint are never requested.
Instructor, contact, attachment, free-text and logged-in member fields are
outside the returned allowlist.
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
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GEOJE_LIFELONG_HOST = "www.lifelonggeoje.kr"
GEOJE_LIFELONG_PATH = "/com/requestPage.do"
GEOJE_LIFELONG_PROVIDER = "MUNI_WWW_LIFELONGGEOJE_KR_D866D2AF"
GEOJE_LIFELONG_CANDIDATE_ID = "MUNI_IR_0B3EE68CBAFB"
GEOJE_LIFELONG_GROUP_ID = "GROUP_00000000000000"
GEOJE_LIFELONG_BRANCH = "거제시평생학습관"
GEOJE_SOURCE_ORGANIZER = "평생학습센터"
GEOJE_MUNICIPALITY_CODE = "4831000000"
GEOJE_MUNICIPALITY_NAME = "경상남도 거제시"
GEOJE_PAGE_SIZE = 10
GEOJE_MAX_WORKERS = 4
GEOJE_FETCH_ATTEMPTS = 2
GEOJE_LIFELONG_URL = (
    f"https://{GEOJE_LIFELONG_HOST}{GEOJE_LIFELONG_PATH}"
    "?selMenuNo=1030600&returnUrl=/educenter/b1020201.do"
    f"?groupId={GEOJE_LIFELONG_GROUP_ID}"
)
GEOJE_OFFICIAL_EVIDENCE_URL = (
    "https://www.geoje.go.kr/board/view.geoje?boardId=BBS_0000008"
    "&contentsSid=13478&dataSid=306391609"
    "&menuCd=DOM_000008902001001000&paging=ok&startPage=1"
)
GEOJE_LIBRARY_DISCOVERY_URL = (
    "https://lib.geoje.go.kr/com/requestPage.do?selMenuNo=104030100"
    "&returnUrl=/culture/d030100.do"
)
GEOJE_LIBRARY_OWNER_PROVIDER = "MUNI_LIB_GEOJE_GO_KR_401A2022"
GEOJE_LIBRARY_REVIEW_CANDIDATE_ID = "MUNI_IR_88A4E9D40A8C"
GEOJE_BOTANIC_GARDEN_PROVIDER = "MUNI_WWW_GEOJE_GO_KR_GBG_EDU"
GEOJE_BOTANIC_GARDEN_URL = (
    "https://www.geoje.go.kr/gbg/index.geoje?"
    "menuCd=DOM_000008804001000000"
)
GEOJE_LIFELONG_PARSER = (
    "geoje_lifelong_complete_post_ledger+declared_total+empty_sentinel+"
    "stable_boundaries+current_detail_summary_only+"
    "identity_bound_application_control_variants_no_registration_fetch+"
    "pii_allowlist"
)
GEOJE_OWNERSHIP_SCOPE = "official_geoje_lifelong_learning_course_ledger"

GEOJE_CANDIDATE_DECISIONS: Mapping[str, str] = {
    GEOJE_LIFELONG_CANDIDATE_ID: (
        "schedule_complete_official_lifelong_learning_ledger"
    ),
    GEOJE_LIBRARY_REVIEW_CANDIDATE_ID: (
        "exclude_registered_geoje_municipal_library_owner"
    ),
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Fetcher = Callable[..., Any]

_SPACE_RE = re.compile(r"\s+")
_COURSE_ID_RE = re.compile(r"COURSE_\d+")
_FILE_ID_RE = re.compile(r"(?:FILE_\d+)?")
_DETAIL_CALL_RE = re.compile(
    r"^javascript:couDetail\('([^']+)','([^']+)','([^']*)','([^']*)','([^']*)'\);?$"
)
_STUDENT_CALL_RE = re.compile(
    r"^javascript:couStudentView\('([^']+)','(\d+)'\);?$"
)
_TOTAL_RE = re.compile(
    r"페이지\s*:\s*(\d+)\s*/\s*(\d+)\s*전체\s*게시물\s*:\s*([\d,]+)"
)
_DATE_TIME_RE = re.compile(
    r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})\s+(\d{1,2}:\d{2})(?!\d)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,3}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_CANCELLED_RE = re.compile(r"(?:^|[<\[(])\s*(?:취소|폐강)\s*(?:$|[>\])])")

_LIST_HEADERS = (
    "기관",
    "강좌명",
    "모집/대기인원",
    "접수기간",
    "강좌기간",
    "교육장소",
    "수강료",
    "상태",
)
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수가능": "OPEN",
    "대기접수": "WAITING",
    "대기접수중": "WAITING",
    "접수예정": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수전": "SCHEDULED",
    "접수마감": "CLOSED",
    "신청마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "ARCHIVED",
}
_ACTIVE_STATUSES = frozenset({"접수중", "접수가능", "대기접수", "대기접수중"})
_SOURCE_CODES = frozenset({"SC001", "SC002", "SC003"})
_DETAIL_REQUIRED = frozenset(
    {
        "학습기관",
        "학습기간",
        "접수기간",
        "수강료",
        "교육대상",
        "교육주기",
        "모집인원/대기인원",
        "신청대상",
        "교육장소",
    }
)
_DETAIL_LABELS = _DETAIL_REQUIRED | {
    "강사명",
    "재료비",
    "동반접수인원",
    "상세내용",
    "사진",
    "강의계획서",
    "URL",
}


class GeojeContractError(ValueError):
    """Raised when the official source no longer satisfies its contract."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ").replace("\u2003", " ")
    ).strip()


def _label(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _normalized(value: Any) -> str:
    return "".join(char.lower() for char in _clean(value) if char.isalnum())


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_geoje_lifelong_target(target: Any) -> bool:
    """Accept only the reviewed provider at its exact complete ledger URL."""

    return bool(
        _clean(_target_value(target, "provider")) == GEOJE_LIFELONG_PROVIDER
        and _clean(_target_value(target, "url")) == GEOJE_LIFELONG_URL
    )


is_target = is_geoje_lifelong_target


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
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


def geoje_lifelong_page_payload(page: int) -> dict[str, str]:
    if page < 1:
        return {}
    return {
        "currentPageNo": str(page),
        "groupId": GEOJE_LIFELONG_GROUP_ID,
        "COURSE_ID": "",
        "imgFile": "",
        "imgFile2": "",
        "imgFile3": "",
        "searchStartDate": "",
        "searchEndDate": "",
        "search_txt": "",
    }


def geoje_lifelong_detail_url(
    course_id: Any,
    image_id: Any = "",
    image_id2: Any = "",
    image_id3: Any = "",
) -> str:
    course = _clean(course_id)
    images = tuple(_clean(value) for value in (image_id, image_id2, image_id3))
    if not _COURSE_ID_RE.fullmatch(course) or any(
        not _FILE_ID_RE.fullmatch(value) for value in images
    ):
        return ""
    return f"https://{GEOJE_LIFELONG_HOST}{GEOJE_LIFELONG_PATH}?" + urlencode(
        (
            ("selMenuNo", "1030600"),
            ("returnUrl", "/educenter/b1020201_detail.do"),
            ("imgFile", images[0]),
            ("imgFile2", images[1]),
            ("imgFile3", images[2]),
            ("COURSE_ID", course),
        )
    )


def _response_soup(response: Any, expected_url: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise GeojeContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise GeojeContractError("HTTP redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and final_url != expected_url:
        raise GeojeContractError("response escaped the expected official route")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise GeojeContractError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _request_soup(
    current: Any,
    method: str,
    url: str,
    *,
    timeout: int,
    data: Optional[Mapping[str, str]] = None,
    fetcher: Optional[Fetcher] = None,
) -> tuple[BeautifulSoup, int]:
    last_error: Optional[Exception] = None
    for attempt in range(GEOJE_FETCH_ATTEMPTS):
        try:
            kwargs: dict[str, Any] = {"timeout": timeout}
            if data is not None:
                kwargs["data"] = dict(data)
            if fetcher is not None:
                response = fetcher(current, method, url, **kwargs)
            else:
                response = getattr(current, method.lower())(url, **kwargs)
            return _response_soup(response, url), attempt
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _page_contract(
    soup: BeautifulSoup, *, requested_page: int
) -> tuple[int, int, list[str]]:
    errors: list[str] = []
    page_nodes = soup.select("p.t_page")
    if len(page_nodes) != 1:
        return 0, 0, ["expected one pagination summary"]
    match = _TOTAL_RE.search(_clean(page_nodes[0].get_text(" ", strip=True)))
    if not match:
        return 0, 0, ["pagination summary is malformed"]
    displayed, last_page, declared_total = (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3).replace(",", "")),
    )
    if displayed != requested_page:
        errors.append(
            f"page {requested_page}: displayed page changed to {displayed}"
        )
    expected_last = max(1, math.ceil(declared_total / GEOJE_PAGE_SIZE))
    if last_page != expected_last:
        errors.append(
            f"page {requested_page}: advertised last page {last_page} "
            f"does not match total {declared_total}"
        )

    forms = soup.select("form#frm_searchs")
    if len(forms) != 1:
        errors.append(f"page {requested_page}: search form changed")
    else:
        form = forms[0]
        page_input = form.select_one("input[name='currentPageNo']")
        group_input = form.select_one("input[name='groupId']")
        if _clean(page_input.get("value") if page_input else "") != str(
            requested_page
        ):
            errors.append(f"page {requested_page}: current page form binding changed")
        if _clean(group_input.get("value") if group_input else "") != GEOJE_LIFELONG_GROUP_ID:
            errors.append(f"page {requested_page}: complete group binding changed")

    tables = soup.select("table.responTable")
    if len(tables) != 1:
        errors.append(f"page {requested_page}: course table changed")
    else:
        headers = tuple(
            _clean(node.get_text(" ", strip=True))
            for node in tables[0].select("thead th")
        )
        if headers != _LIST_HEADERS:
            errors.append(f"page {requested_page}: course headers changed")
    return declared_total, last_page, errors


def _date_times(value: Any) -> list[datetime]:
    found: list[datetime] = []
    for year, month, day, clock in _DATE_TIME_RE.findall(_clean(value)):
        found.append(
            datetime.strptime(
                f"{year}-{month}-{day} {clock}", "%Y-%m-%d %H:%M"
            )
        )
    return found


def _period(value: Any) -> tuple[str, str, str, str]:
    text = _clean(value)
    values = _date_times(text)
    if len(values) != 2:
        return "", "", "", ""
    start, end = values
    rendered = f"{start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M}"
    last_match = list(_DATE_TIME_RE.finditer(text))[-1]
    schedule = _clean(text[last_match.end() :])
    return rendered, start.date().isoformat(), end.date().isoformat(), schedule


def _capacity(value: Any) -> tuple[str, str, str, str]:
    text = _clean(value)
    regular = re.search(r"모집인원\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)", text)
    waiting = re.search(r"대기인원\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)", text)
    if not regular or not waiting:
        return "", "", "", ""
    return regular.group(2), regular.group(1), waiting.group(2), waiting.group(1)


def _parse_list_page(
    soup: BeautifulSoup, *, source_page: int
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    table = soup.select_one("table.responTable")
    if table is None:
        return [], [f"page {source_page}: missing course table"]
    for index, tr in enumerate(table.select("tbody > tr"), 1):
        cells = tr.find_all("td", recursive=False)
        if len(cells) == 1:
            if tr.select("a.detailItem") or "없" not in _clean(cells[0].get_text(" ", strip=True)):
                errors.append(f"page {source_page} row {index}: malformed empty row")
            continue
        if len(cells) != len(_LIST_HEADERS):
            errors.append(f"page {source_page} row {index}: expected eight cells")
            continue
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        organizer, title, capacity_text, apply_text, course_text, venue, fee, source_status = values
        item_errors: list[str] = []
        if organizer != GEOJE_SOURCE_ORGANIZER:
            item_errors.append("official source organization changed")
        if not title or not venue:
            item_errors.append("title or education venue is empty")
        if source_status not in _STATUS_MAP:
            item_errors.append("unknown source status")

        detail_links = tr.select("a.detailItem[href]")
        calls = {_clean(link.get("href")) for link in detail_links}
        if not detail_links or len(calls) != 1:
            item_errors.append("detail identity controls changed")
            course_id = source_code = ""
            image_ids = ("", "", "")
        else:
            call = _DETAIL_CALL_RE.fullmatch(next(iter(calls)))
            if not call:
                item_errors.append("detail call is malformed")
                course_id = source_code = ""
                image_ids = ("", "", "")
            else:
                course_id, source_code = call.group(1), call.group(2)
                image_ids = (call.group(3), call.group(4), call.group(5))
                if not _COURSE_ID_RE.fullmatch(course_id):
                    item_errors.append("course identity is malformed")
                if source_code not in _SOURCE_CODES:
                    item_errors.append("unknown source course code")
                if any(not _FILE_ID_RE.fullmatch(value) for value in image_ids):
                    item_errors.append("image identity is malformed")
                expected_anchor_id = f"{course_id}||{source_code}"
                if any(_clean(link.get("id")) != expected_anchor_id for link in detail_links):
                    item_errors.append("detail identity binding changed")

        students = tr.select("a.detailStudent[href]")
        if len(students) != 1:
            item_errors.append("public applicant-count control changed")
        else:
            student_call = _STUDENT_CALL_RE.fullmatch(_clean(students[0].get("href")))
            if (
                not student_call
                or student_call.group(1) != course_id
                # The second argument is the applicant-list page, not the
                # catalogue page.  The official ledger always starts that
                # separate surface at page one; it is validated but never
                # requested by this collector.
                or int(student_call.group(2)) != 1
            ):
                item_errors.append("applicant-count identity binding changed")

        apply_period, apply_start, apply_end, apply_extra = _period(apply_text)
        period, start_date, end_date, schedule = _period(course_text)
        capacity, capacity_current, wait_capacity, wait_current = _capacity(capacity_text)
        if not apply_period or apply_extra:
            item_errors.append("application period is malformed")
        if not period:
            item_errors.append("education period is malformed")
        if not capacity:
            item_errors.append("capacity is malformed")

        detail_url = geoje_lifelong_detail_url(course_id, *image_ids)
        if not detail_url:
            item_errors.append("detail URL could not be constructed")
        if item_errors:
            errors.extend(
                f"page {source_page} row {index}: {message}"
                for message in item_errors
            )
            continue

        active = source_status in _ACTIVE_STATUSES
        rows.append(
            {
                "provider": GEOJE_LIFELONG_PROVIDER,
                "provider_course_id": f"{GEOJE_LIFELONG_PROVIDER}:course:{course_id}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": GEOJE_LIFELONG_BRANCH,
                "branch_code": "geoje-lifelong-learning-center",
                "preserve_branch": True,
                "municipality_code": GEOJE_MUNICIPALITY_CODE,
                "municipality_name": GEOJE_MUNICIPALITY_NAME,
                "sido": "경상남도",
                "sigungu": "거제시",
                "provider_organizer": organizer,
                "venue_name": venue,
                "category": "평생학습",
                "program_type": "강좌",
                "raw_url": detail_url,
                "application_url": detail_url if active else "",
                "application_type": (
                    "WAITLIST_APPLY"
                    if source_status in {"대기접수", "대기접수중"}
                    else "ONLINE_RESERVATION"
                    if active
                    else "INFO_ONLY"
                ),
                "reservation_available": active,
                "status": _STATUS_MAP[source_status],
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
                "apply_period": apply_period,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": schedule,
                "fee": fee,
                "target": "",
                "capacity": capacity,
                "capacity_current": capacity_current,
                "waitlist_capacity": wait_capacity,
                "waitlist_current": wait_current,
                "description": title,
                # The nationwide municipality workflow owns this official
                # reservation ledger.  Its user-facing programme category is
                # still lifelong learning, while operational routing remains
                # aligned with the production public-reservation taxonomy.
                "source_group": "municipal_reservation",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "complete_post_pages+current_detail_summary",
                "raw_fields": {
                    "parser": GEOJE_LIFELONG_PARSER,
                    "source_catalog": "geoje_lifelong_complete",
                    "source_course_id": course_id,
                    "source_course_code": source_code,
                    "source_page": source_page,
                    "source_organizer": organizer,
                    "source_status": source_status,
                    "source_image_ids": list(image_ids),
                    "detail_validated": False,
                    "applicant_list_fetched": False,
                    "registration_endpoint_fetched": False,
                    "contact_excluded": True,
                    "instructor_excluded": True,
                    "attachments_excluded": True,
                    "free_text_excluded": True,
                    "member_fields_excluded": True,
                },
            }
        )
    return rows, errors


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _detail_pairs(table: Any) -> tuple[dict[str, str], list[str]]:
    pairs: dict[str, str] = {}
    labels: set[str] = set()
    errors: list[str] = []
    for tr in table.select("tbody > tr"):
        nodes = tr.find_all(["th", "td"], recursive=False)
        for index, node in enumerate(nodes):
            if node.name != "th":
                continue
            key = _label(node.get_text(" ", strip=True))
            if not key:
                continue
            labels.add(key)
            if key not in _DETAIL_LABELS:
                errors.append(f"unknown detail label {key}")
                continue
            if key not in _DETAIL_REQUIRED:
                continue
            if index + 1 >= len(nodes) or nodes[index + 1].name != "td":
                errors.append(f"detail label {key} has no value cell")
                continue
            value = _clean(nodes[index + 1].get_text(" ", strip=True))
            if key in pairs:
                errors.append(f"duplicate detail label {key}")
            pairs[key] = value
    missing = _DETAIL_REQUIRED - labels
    if missing:
        errors.append("missing detail labels " + ",".join(sorted(missing)))
    return pairs, errors


def _detail_row(
    parent: Mapping[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    raw = parent.get("raw_fields", {})
    identity = _clean(raw.get("source_course_id"))
    forms = soup.select("form#couForm")
    if len(forms) != 1:
        return dict(parent), [f"detail {identity}: course form changed"]
    form = forms[0]

    def form_value(name: str) -> str:
        node = form.select_one(f"input[name='{name}']")
        return _clean(node.get("value") if node else "")

    if form_value("COURSE_ID") != identity:
        errors.append(f"detail {identity}: course identity mismatch")
    if form_value("ORGNZT_ID") != GEOJE_LIFELONG_GROUP_ID:
        errors.append(f"detail {identity}: organization identity mismatch")
    if form_value("ORGNZT_NM") != GEOJE_SOURCE_ORGANIZER:
        errors.append(f"detail {identity}: source organization mismatch")
    if form_value("TITLE") != _clean(parent.get("title")):
        errors.append(f"detail {identity}: hidden title mismatch")
    private_values = {
        key: form_value(key) for key in ("loginId", "loginNm", "loginBirthDay")
    }
    if any(private_values.values()):
        errors.append(f"detail {identity}: authenticated member data exposed")

    headings = soup.select("div.life_tit h3.dan_h3")
    if len(headings) != 1 or _clean(headings[0].get_text(" ", strip=True)) != _clean(
        parent.get("title")
    ):
        errors.append(f"detail {identity}: visible title mismatch")
    tables = soup.select("div.life_program table.responTable")
    if len(tables) != 1:
        return dict(parent), [*errors, f"detail {identity}: detail table changed"]
    pairs, pair_errors = _detail_pairs(tables[0])
    errors.extend(f"detail {identity}: {message}" for message in pair_errors)

    education_period, start_date, end_date, extra = _period(pairs.get("학습기간"))
    application_period, apply_start, apply_end, apply_extra = _period(
        pairs.get("접수기간")
    )
    if extra or education_period != _clean(parent.get("period")):
        errors.append(f"detail {identity}: education period mismatch")
    if apply_extra or application_period != _clean(parent.get("apply_period")):
        errors.append(f"detail {identity}: application period mismatch")
    if [start_date, end_date] != [
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ]:
        errors.append(f"detail {identity}: education date mismatch")
    if [apply_start, apply_end] != [
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ]:
        errors.append(f"detail {identity}: application date mismatch")
    comparisons = {
        "학습기관": "provider_organizer",
        "수강료": "fee",
        "교육주기": "schedule_raw",
        "교육장소": "venue_name",
    }
    for label, key in comparisons.items():
        if _clean(pairs.get(label)) != _clean(parent.get(key)):
            errors.append(f"detail {identity}: {label} mismatch")

    capacity_values = re.findall(r"(\d+)\s*명", _clean(pairs.get("모집인원/대기인원")))
    if capacity_values != [
        _clean(parent.get("capacity")),
        _clean(parent.get("waitlist_capacity")),
    ]:
        errors.append(f"detail {identity}: capacity mismatch")

    source_status = _clean(raw.get("source_status"))
    controls = soup.select("#btn_cart, a.bg_btn[href], button.bg_btn, input.bg_btn")
    application_contract = "inactive_control_absent"
    if source_status in _ACTIVE_STATUSES:
        if len(controls) != 1:
            errors.append(f"detail {identity}: active application control missing")
        else:
            control = controls[0]
            control_text = _clean(
                control.get_text(" ", strip=True) or control.get("value")
            )
            binding = _clean(control.get("onclick") or control.get("href"))
            classes = {_clean(value) for value in control.get("class", [])}
            legacy_control = (
                _clean(control.get("id")) == "btn_cart"
                and "신청" in control_text
                and binding == "javascript:couRegist();"
            )
            current_control = (
                control.name == "a"
                and "bg_btn" in classes
                and control_text == "접수하기"
                and binding == "javascript:couRegist();"
            )
            if not (legacy_control or current_control):
                errors.append(f"detail {identity}: active application control changed")
            else:
                application_contract = (
                    "current_bg_btn_couRegist"
                    if current_control
                    else "legacy_btn_cart_couRegist"
                )
        scripts = "\n".join(
            node.get_text("\n", strip=False)
            for node in soup.select("script:not([src])")
        )
        expected_action = (
            "/com/requestPage.do?selMenuNo=1030600&"
            "returnUrl=/educenter/b1020201_couRegist.do"
        )
        if (
            re.search(r"function\s+couRegist\s*\(", scripts) is None
            or expected_action not in scripts
        ):
            errors.append(f"detail {identity}: application handler changed")
    elif controls:
        errors.append(f"detail {identity}: inactive course exposes application control")

    row = dict(parent)
    row["target"] = _clean(pairs.get("교육대상"))
    row["eligibility_raw"] = _clean(pairs.get("신청대상"))
    row["raw_fields"] = {
        **raw,
        "detail_validated": not errors,
        "detail_summary_only": True,
        "application_control_present": bool(controls),
        "application_control_contract": application_contract,
        "applicant_list_fetched": False,
        "registration_endpoint_fetched": False,
        "contact_excluded": True,
        "instructor_excluded": True,
        "attachments_excluded": True,
        "free_text_excluded": True,
        "member_fields_excluded": True,
    }
    return row, errors


def _fetch_details(
    rows: list[dict[str, Any]],
    *,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    fetcher: Optional[Fetcher],
) -> tuple[list[dict[str, Any]], list[str], int, int]:
    if not rows:
        return [], [], 0, 0

    def one(parent: dict[str, Any]) -> tuple[dict[str, Any], list[str], int]:
        current = session_factory()
        try:
            url = _clean(parent.get("raw_url"))
            soup, retries = _request_soup(
                current, "GET", url, timeout=timeout, fetcher=fetcher
            )
            row, errors = _detail_row(parent, soup)
            return row, errors, retries
        finally:
            _close_quietly(current)

    found: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    attempts = retries = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(one, row): row for row in rows}
        for future in as_completed(futures):
            parent = futures[future]
            identity = _clean(parent.get("raw_fields", {}).get("source_course_id"))
            attempts += 1
            try:
                row, item_errors, item_retries = future.result()
                retries += item_retries
                if item_errors:
                    errors.extend(item_errors)
                else:
                    found[identity] = row
            except Exception as exc:
                errors.append(
                    f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                )
    ordered = [
        found[_clean(row.get("raw_fields", {}).get("source_course_id"))]
        for row in rows
        if _clean(row.get("raw_fields", {}).get("source_course_id")) in found
    ]
    return ordered, errors, attempts, retries


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _privacy_violations(rows: Iterable[Mapping[str, Any]]) -> int:
    violations = 0
    forbidden = {
        "phone",
        "email",
        "contact",
        "instructor",
        "teacher",
        "applicant",
        "loginId",
        "loginNm",
        "loginBirthDay",
    }
    for row in rows:
        serialized = repr(row)
        violations += len(_PHONE_RE.findall(serialized))
        violations += len(_EMAIL_RE.findall(serialized))
        violations += len(_RESIDENT_ID_RE.findall(serialized))
        violations += sum(key in row for key in forbidden)
        raw = row.get("raw_fields", {})
        if isinstance(raw, Mapping):
            violations += sum(key in raw for key in forbidden)
    return violations


def _semantic_duplicate_count(rows: Iterable[Mapping[str, Any]]) -> int:
    signatures = [
        (
            _normalized(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _normalized(row.get("branch")),
        )
        for row in rows
    ]
    return len(signatures) - len(set(signatures))


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "source_total": 0,
        "source_rows": 0,
        "declared_total": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "cancelled_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "sentinel_count": None,
        "sentinel_mode": "immediate_empty_page",
        "stable_rechecks": {},
        "duplicate_source_id_count": 0,
        "semantic_duplicate_count": 0,
        "privacy_violations": 0,
        "network_retry_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": GEOJE_MUNICIPALITY_CODE,
        "municipality_name": GEOJE_MUNICIPALITY_NAME,
        "ownership_scope": GEOJE_OWNERSHIP_SCOPE,
        "candidate_id": GEOJE_LIFELONG_CANDIDATE_ID,
        "candidate_decisions": dict(GEOJE_CANDIDATE_DECISIONS),
        "official_evidence_urls": [GEOJE_OFFICIAL_EVIDENCE_URL],
        "separate_owners": [
            {
                "provider": GEOJE_LIBRARY_OWNER_PROVIDER,
                "url": GEOJE_LIBRARY_DISCOVERY_URL,
                "relationship": "registered municipal library course owner",
            },
            {
                "provider": GEOJE_BOTANIC_GARDEN_PROVIDER,
                "url": GEOJE_BOTANIC_GARDEN_URL,
                "relationship": "separate botanic-garden education/experience owner",
            },
        ],
    }


def collect_geoje_lifelong_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 80,
    detail_limit: int = 250,
    *,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GEOJE_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return a complete current/future Geoje lifelong-learning snapshot."""

    meta = _base_meta()
    if not is_geoje_lifelong_target(target):
        meta["configured_collection_error"] = (
            "target does not match the canonical Geoje lifelong-learning owner"
        )
        return [], GEOJE_LIFELONG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = (
                "managed session_factory injection is required"
            )
            return [], GEOJE_LIFELONG_PARSER, meta
        session_factory = _default_session_factory
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        request_timeout = max(1, int(timeout))
        workers = min(max(1, int(max_workers)), GEOJE_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], GEOJE_LIFELONG_PARSER, meta

    errors: list[str] = []
    source_cap_reached = False
    declared_total = last_page = 0
    pages: dict[int, list[dict[str, Any]]] = {}
    stable_rechecks: dict[str, bool] = {}
    sentinel_count: Optional[int] = None
    retries = 0
    current = session_factory()
    try:
        try:
            first_soup, item_retries = _request_soup(
                current, "GET", GEOJE_LIFELONG_URL, timeout=request_timeout, fetcher=fetcher
            )
            retries += item_retries
            meta["pages"] += 1
            meta["list_requests"] += 1
            declared_total, last_page, item_errors = _page_contract(
                first_soup, requested_page=1
            )
            errors.extend(item_errors)
            first_rows, item_errors = _parse_list_page(first_soup, source_page=1)
            errors.extend(item_errors)
            pages[1] = first_rows
            if declared_total and not first_rows:
                errors.append("first page contains no course rows")
        except Exception as exc:
            errors.append(f"first page: {type(exc).__name__}: {_clean(exc)}")

        boundary_count = 1 if last_page == 1 else 2
        required_requests = last_page + 1 + boundary_count if last_page else 0
        meta["required_list_requests"] = required_requests
        if required_requests > allowed_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of {required_requests} "
                "required list/sentinel/recheck requests"
            )

        if not errors:
            for page in range(2, last_page + 1):
                soup, item_retries = _request_soup(
                    current,
                    "POST",
                    GEOJE_LIFELONG_URL,
                    timeout=request_timeout,
                    data=geoje_lifelong_page_payload(page),
                    fetcher=fetcher,
                )
                retries += item_retries
                meta["pages"] += 1
                meta["list_requests"] += 1
                total, found_last, item_errors = _page_contract(
                    soup, requested_page=page
                )
                errors.extend(item_errors)
                parsed, item_errors = _parse_list_page(soup, source_page=page)
                errors.extend(item_errors)
                if total != declared_total or found_last != last_page:
                    errors.append(f"page {page}: declared pagination changed")
                pages[page] = parsed

            sentinel_page = last_page + 1
            soup, item_retries = _request_soup(
                current,
                "POST",
                GEOJE_LIFELONG_URL,
                timeout=request_timeout,
                data=geoje_lifelong_page_payload(sentinel_page),
                fetcher=fetcher,
            )
            retries += item_retries
            meta["pages"] += 1
            meta["list_requests"] += 1
            total, found_last, item_errors = _page_contract(
                soup, requested_page=sentinel_page
            )
            errors.extend(item_errors)
            sentinel_rows, item_errors = _parse_list_page(
                soup, source_page=sentinel_page
            )
            errors.extend(item_errors)
            sentinel_count = len(sentinel_rows)
            if total != declared_total or found_last != last_page or sentinel_rows:
                errors.append("immediate post-last page is not empty and stable")

            for page in dict.fromkeys((1, last_page)):
                soup, item_retries = _request_soup(
                    current,
                    "POST",
                    GEOJE_LIFELONG_URL,
                    timeout=request_timeout,
                    data=geoje_lifelong_page_payload(page),
                    fetcher=fetcher,
                )
                retries += item_retries
                meta["pages"] += 1
                meta["list_requests"] += 1
                total, found_last, item_errors = _page_contract(
                    soup, requested_page=page
                )
                errors.extend(item_errors)
                parsed, item_errors = _parse_list_page(soup, source_page=page)
                errors.extend(item_errors)
                stable = bool(
                    total == declared_total
                    and found_last == last_page
                    and _page_signature(parsed) == _page_signature(pages.get(page, []))
                )
                stable_rechecks[str(page)] = stable
                if not stable:
                    errors.append(f"page {page}: stable boundary recheck changed")
    except Exception as exc:
        errors.append(f"catalogue traversal: {type(exc).__name__}: {_clean(exc)}")
    finally:
        _close_quietly(current)

    source_rows = [
        row for page in range(1, last_page + 1) for row in pages.get(page, [])
    ]
    for page in range(1, last_page):
        if len(pages.get(page, [])) != GEOJE_PAGE_SIZE:
            errors.append(f"page {page}: expected a full page")
    if declared_total:
        expected_last_count = declared_total % GEOJE_PAGE_SIZE or GEOJE_PAGE_SIZE
        if len(pages.get(last_page, [])) != expected_last_count:
            errors.append("last page cardinality is invalid")
    if declared_total != len(source_rows):
        errors.append(
            f"declared total {declared_total} != parsed total {len(source_rows)}"
        )
    identities = [_clean(row.get("provider_course_id")) for row in source_rows]
    duplicate_ids = len(identities) - len(set(identities))
    if duplicate_ids:
        errors.append(f"{duplicate_ids} duplicate source identities")

    current_rows: list[dict[str, Any]] = []
    expired_count = cancelled_count = 0
    for row in source_rows:
        try:
            ended = date.fromisoformat(_clean(row.get("end_date")))
        except ValueError:
            errors.append(f"{_clean(row.get('provider_course_id'))}: invalid end date")
            continue
        if ended < cutoff:
            expired_count += 1
        elif _CANCELLED_RE.search(_clean(row.get("title"))):
            cancelled_count += 1
        else:
            current_rows.append(row)

    semantic_duplicates = _semantic_duplicate_count(current_rows)
    if semantic_duplicates:
        errors.append(f"{semantic_duplicates} duplicate current semantic signatures")
    if len(current_rows) > allowed_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {len(current_rows)} "
            "required current/future details"
        )

    detailed: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    detail_attempts = detail_retries = 0
    if not errors:
        detailed, detail_errors, detail_attempts, detail_retries = _fetch_details(
            current_rows,
            session_factory=session_factory,
            timeout=request_timeout,
            max_workers=workers,
            fetcher=fetcher,
        )
    retries += detail_retries
    errors.extend(detail_errors)
    details_complete = bool(
        not detail_errors
        and detail_attempts == len(current_rows)
        and len(detailed) == len(current_rows)
    )

    result: list[dict[str, Any]] = []
    if not errors and details_complete:
        result = list((dedupe_rows or _default_dedupe)(detailed))
        if len(result) != len(detailed):
            errors.append(
                f"dedupe changed complete row count {len(detailed)} to {len(result)}"
            )
            result = []
    result.sort(
        key=lambda row: (
            _clean(row.get("start_date")),
            _clean(row.get("title")),
            _clean(row.get("provider_course_id")),
        )
    )

    privacy_violations = _privacy_violations(result)
    if privacy_violations:
        errors.append(f"{privacy_violations} PII allowlist violations")
        result = []
    expected_rechecks = 1 if last_page == 1 else 2
    pagination_complete = bool(
        not errors
        and sentinel_count == 0
        and len(stable_rechecks) == expected_rechecks
        and all(stable_rechecks.values())
        and meta["list_requests"] == meta["required_list_requests"]
    )
    snapshot_complete = bool(pagination_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    meta.update(
        {
            "source_total": len(source_rows),
            "source_rows": len(source_rows),
            "declared_total": declared_total,
            "data_pages": last_page,
            "current_count": len(current_rows),
            "returned_count": len(result),
            "expired_count": expired_count,
            "cancelled_count": cancelled_count,
            "detail_attempts": detail_attempts,
            "detail_pages": len(detailed),
            "detail_errors": len(detail_errors),
            "sentinel_count": sentinel_count,
            "stable_rechecks": stable_rechecks,
            "duplicate_source_id_count": duplicate_ids,
            "semantic_duplicate_count": semantic_duplicates,
            "privacy_violations": privacy_violations,
            "network_retry_count": retries,
            "first_source_course_id": (
                _clean(source_rows[0].get("raw_fields", {}).get("source_course_id"))
                if source_rows
                else ""
            ),
            "last_source_course_id": (
                _clean(source_rows[-1].get("raw_fields", {}).get("source_course_id"))
                if source_rows
                else ""
            ),
            "source_organizer_counts": dict(
                Counter(
                    _clean(row.get("raw_fields", {}).get("source_organizer"))
                    for row in source_rows
                )
            ),
            "source_status_counts": dict(
                Counter(
                    _clean(row.get("raw_fields", {}).get("source_status"))
                    for row in source_rows
                )
            ),
            "source_course_code_counts": dict(
                Counter(
                    _clean(row.get("raw_fields", {}).get("source_course_code"))
                    for row in source_rows
                )
            ),
            "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
            "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
            "application_type_counts": dict(
                Counter(_clean(row.get("application_type")) for row in result)
            ),
            "application_control_count": sum(
                bool(
                    row.get("raw_fields", {}).get(
                        "application_control_present"
                    )
                )
                for row in result
            ),
            "pagination_detected": last_page > 1,
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not result),
            "no_current_reason": (
                "complete Geoje lifelong-learning ledger contains only ended/cancelled courses"
                if snapshot_complete and not result
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, GEOJE_LIFELONG_PARSER, meta


collect = collect_geoje_lifelong_education_courses


__all__ = [
    "GEOJE_BOTANIC_GARDEN_PROVIDER",
    "GEOJE_BOTANIC_GARDEN_URL",
    "GEOJE_CANDIDATE_DECISIONS",
    "GEOJE_LIBRARY_DISCOVERY_URL",
    "GEOJE_LIBRARY_OWNER_PROVIDER",
    "GEOJE_LIBRARY_REVIEW_CANDIDATE_ID",
    "GEOJE_LIFELONG_BRANCH",
    "GEOJE_LIFELONG_CANDIDATE_ID",
    "GEOJE_LIFELONG_GROUP_ID",
    "GEOJE_LIFELONG_PARSER",
    "GEOJE_LIFELONG_PROVIDER",
    "GEOJE_LIFELONG_URL",
    "GEOJE_MUNICIPALITY_CODE",
    "GEOJE_MUNICIPALITY_NAME",
    "GEOJE_OFFICIAL_EVIDENCE_URL",
    "GEOJE_OWNERSHIP_SCOPE",
    "GEOJE_SOURCE_ORGANIZER",
    "GeojeContractError",
    "collect",
    "collect_geoje_lifelong_education_courses",
    "geoje_lifelong_detail_url",
    "geoje_lifelong_page_payload",
    "is_geoje_lifelong_target",
    "is_target",
]
