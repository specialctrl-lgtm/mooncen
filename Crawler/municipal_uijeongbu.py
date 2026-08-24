"""Fail-closed collector for Uijeongbu City's official education fan-out.

The former city reservation owner (``ui4u.go.kr/reservation/youthProgram``)
is a stale copy whose newest public rows stop in June 2025.  The current city
education menu is a routing hub, not a catalogue owner.  Its four real owners
are the Neuron lifelong-learning service, the Urban Education Foundation's
youth programme service, the Uijeongbu Cultural Foundation, and the city
youth centre.

This collector assigns each record to exactly one originating owner.  It does
not execute the Neuron mirror offices for youth programmes, the cultural
foundation, or the youth centre.  Cultural events, facility rental, tours,
clubs, and room reservations are excluded.  Every declared page, the first
empty post-boundary page (allowing the youth centre's documented pinned
programmes), a stable page-one recheck, and every current/future detail and
public application control are required.  Any incomplete contract returns no
rows.

Instructor names, staff contacts, free-form descriptions, attachments, and
applicant/member data are deliberately not persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import re
from threading import Lock, local
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


UIJEONGBU_PROVIDER = "MUNI_SUGANG_ULL_OR_KR_8EC76DA5"
UIJEONGBU_CANONICAL_CANDIDATE_ID = "MUNI_IR_76A34D1F10F3"
UIJEONGBU_CANONICAL_URL = (
    "https://sugang.ull.or.kr/ilms/learning/officeMainList.do"
)
UIJEONGBU_LEGACY_CANDIDATE_ID = "MUNI_IR_36EE4B255E26"
UIJEONGBU_LEGACY_PROVIDER = "MUNI_WWW_UI4U_GO_KR_F1E0CC26"
UIJEONGBU_LEGACY_URL = (
    "https://www.ui4u.go.kr/reservation/youthProgram/list.do"
    "?mId=0104000000"
)

UIJEONGBU_MUNICIPALITY_CODE = "4115000000"
UIJEONGBU_MUNICIPALITY_NAME = "경기도 의정부시"
UIJEONGBU_SIDO = "경기도"
UIJEONGBU_SIGUNGU = "의정부시"

UIJEONGBU_SUGANG_HOST = "sugang.ull.or.kr"
UIJEONGBU_SUGANG_LIST_PATH = "/ilms/learning/learningList.do"
UIJEONGBU_SUGANG_DETAIL_PATH = "/ilms/learning/learningDetail.do"
UIJEONGBU_SUGANG_OFFICE_ID = "OFFICE_00002140"
UIJEONGBU_SUGANG_LIST_URL = (
    f"https://{UIJEONGBU_SUGANG_HOST}{UIJEONGBU_SUGANG_LIST_PATH}"
    f"?office_id={UIJEONGBU_SUGANG_OFFICE_ID}"
)

UIJEONGBU_YOUTH_HOST = "www.uiyouth.or.kr"
UIJEONGBU_YOUTH_DETAIL_PATH = (
    "/center/search/regularProgramDataAjax.ui"
)
UIJEONGBU_UAC_HOST = "www.uac.or.kr"
UIJEONGBU_UAC_LIST_PATH = "/newuac/ams/ams_03.php"
UIJEONGBU_UAC_LIST_URL = (
    f"https://{UIJEONGBU_UAC_HOST}{UIJEONGBU_UAC_LIST_PATH}"
)
UIJEONGBU_UAC_BRANCH = "의정부문화관광재단"
UIJEONGBU_UAC_TITLE_MARKERS = frozenset(
    {"의정부문화관광재단", "의정부문화재단"}
)
UIJEONGBU_YOUNG_CENTER_HOST = "211.188.65.173"
UIJEONGBU_YOUNG_CENTER_LIST_PATH = "/pages/program/list"
UIJEONGBU_YOUNG_CENTER_LIST_URL = (
    f"https://{UIJEONGBU_YOUNG_CENTER_HOST}"
    f"{UIJEONGBU_YOUNG_CENTER_LIST_PATH}"
)

UIJEONGBU_PAGE_SIZE = 10
UIJEONGBU_YOUNG_CENTER_NORMAL_PAGE_SIZE = 8
UIJEONGBU_YOUNG_CENTER_PINNED_IDS = frozenset(
    {"613", "697", "885", "886"}
)
# Three archived records are published with reversed start/end fields (487:
# 2024-02-23~01-23, 491: 2024-02-01~01-22, 742: 2025-08-02~07-23).  They are
# all expired; only these audited identities may be normalized for complete
# archive-boundary accounting.  Any newly reversed record still fails closed.
UIJEONGBU_YOUNG_CENTER_REVERSED_ARCHIVE_IDS = frozenset(
    {"487", "491", "742"}
)
# The centre blanked the structured period/capacity fields on 235 closed
# archive cards.  The audited affected range ends at immutable numeric id
# 448; a blank period on any newer/open record remains a contract failure.
UIJEONGBU_YOUNG_CENTER_BLANK_ARCHIVE_MAX_ID = 448
UIJEONGBU_MAX_WORKERS = 8
UIJEONGBU_FETCH_ATTEMPTS = 3
UIJEONGBU_PARSER = (
    "uijeongbu_origin_owned_education_fanout+all_pages+sentinels+"
    "stable_rechecks+current_details+source_bound_application_contracts+"
    "facility_and_non_education_exclusion+pii_allowlist"
)


@dataclass(frozen=True)
class UijeongbuYouthCatalogue:
    key: str
    label: str
    path: str
    code: str
    native: bool

    @property
    def url(self) -> str:
        return f"https://{UIJEONGBU_YOUTH_HOST}{self.path}"


UIJEONGBU_YOUTH_CATALOGUES: tuple[UijeongbuYouthCatalogue, ...] = (
    UijeongbuYouthCatalogue(
        "general",
        "일반프로그램",
        "/center/search/regularProgramList.ui",
        "PGM_YNG",
        True,
    ),
    UijeongbuYouthCatalogue(
        "swimming",
        "수영",
        "/center/search/regularProgramRegList.ui",
        "PGM_CATE009",
        True,
    ),
    UijeongbuYouthCatalogue(
        "sports",
        "체육",
        "/center/search/regularProgramRegList.ui",
        "PGM_CATE011",
        False,
    ),
    UijeongbuYouthCatalogue(
        "always",
        "상시프로그램",
        "/center/search/alwaysProgramList.ui",
        "PGM_ALW",
        True,
    ),
)
_YOUTH_BY_KEY = {item.key: item for item in UIJEONGBU_YOUTH_CATALOGUES}


# The three Neuron offices below are verified mirrors and deliberately never
# execute.  Their external originals are the owners returned by this module.
UIJEONGBU_NON_EXECUTING_MIRRORS: Mapping[str, Mapping[str, str]] = {
    "API_OFFICE_00000000": {
        "owner": "uiyouth",
        "reason": "Neuron mirror of the Urban Education Foundation youth catalogues",
    },
    "API_OFFICE_00000010": {
        "owner": "uac",
        "reason": "Neuron mirror retaining the Cultural Foundation lecture URLs",
    },
    "OFFICE_00002310": {
        "owner": "young_center",
        "reason": "Neuron mirror of city youth-centre programmes",
    },
}

UIJEONGBU_OWNERSHIP_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-29",
    "legacy_latest_date": "2025-06-14",
    "legacy_conclusion": "stale_non_owner_mirror",
    "integrated_search": {
        "lifelong": "external_link_only",
        "cultural_foundation": "server_error_on_selected_search",
        "urban_education_foundation": "three_stale_2025_rows",
        "young_center": "external_link_only",
    },
    "selected_owners": (
        "sugang_lifelong",
        "uiyouth_programmes",
        "uac_education",
        "ui4u_young_center",
    ),
    "excluded_service_families": (
        "culturalEvent",
        "facility",
        "tour",
        "reservePlace",
        "club",
    ),
}


Requester = Callable[[Any, str, str, Optional[Mapping[str, str]], int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)"
)
_SHORT_DATE_RE = re.compile(
    r"(?<!\d)(\d{2})\s*[.]\s*(\d{1,2})\s*[.]\s*(\d{1,2})(?!\d)"
)
_MONTH_DAY_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[.]\s*(\d{1,2})(?!\d)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LEARNING_ID_RE = re.compile(
    r"fn_learning_detail\(\s*['\"](LEARNING_[A-Za-z0-9_]+)['\"]\s*\)"
)
_YOUTH_ID_RE = re.compile(r"programDetail[.]view\(\s*['\"]?(\d+)['\"]?\s*\)")
_YOUTH_APPLY_ID_RE = re.compile(
    r"programRegForm\([^)]*['\"](\d+)['\"]\s*\)"
)
_UAC_ID_RE = re.compile(r"^[0-9]{15}$")
_YOUNG_ID_RE = re.compile(r"/pages/program/detail/(\d+)(?:[/?#]|$)")

_SUGANG_STATUS_SEQUENCES = {
    ("접수중",): "OPEN",
    ("대기접수",): "OPEN",
    ("대기",): "SCHEDULED",
    ("접수예정",): "SCHEDULED",
    ("마감",): "CLOSED",
    ("접수마감",): "CLOSED",
    ("교육중",): "CLOSED",
    ("교육중", "마감"): "CLOSED",
    ("교육완료",): "CLOSED",
    ("교육종료",): "CLOSED",
}
_UAC_STATUS = {
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
}
_APPLICATION_LABELS = {
    "신청",
    "신청하기",
    "수강신청",
    # The Neuron detail page currently uses this exact label for ordinary
    # public enrolment while retaining the same fn_learning_apply control.
    "일반모집신청",
    "대기자신청",
}
_INACTIVE_LABELS = {
    "마감",
    "준비중",
    "현장신청",
    "신청종료",
    "접수마감",
    "신청 기간이 아닙니다.",
}

# The youth list's single "접수기간" cell is not the online-new-member
# interval alone.  It is the envelope of every published enrolment phase:
# existing-member re-registration, class changes, and new registration,
# across online and on-site channels.  This was exhaustively reconciled for
# all 91 live rows on 2026-07-21 (91/91 exact list/detail envelopes).  Some
# group programmes publish both newCls* date pairs even when regYnOfline is
# N; those dates still form the official list interval and must not be
# discarded based on the channel flag.
_YOUTH_APPLICATION_PHASE_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("new_online", "newClsOnDtmSt", "newClsOnDtmEd"),
    ("new_offline", "newClsOffDtmSt", "newClsOffDtmEd"),
    (
        "reregistration_online",
        "reReceptionPeriodOnStart",
        "reReceptionPeriodOnEnd",
    ),
    (
        "reregistration_offline",
        "reReceptionPeriodOffStart",
        "reReceptionPeriodOffEnd",
    ),
    (
        "class_change_online",
        "classChangePeriodOnStart",
        "classChangePeriodOnEnd",
    ),
    (
        "class_change_offline",
        "classChangePeriodOffStart",
        "classChangePeriodOffEnd",
    ),
    (
        "new_registration_online",
        "receptionPeriodOnStart",
        "receptionPeriodOnEnd",
    ),
    (
        "new_registration_offline",
        "receptionPeriodOffStart",
        "receptionPeriodOffEnd",
    ),
)


class UijeongbuContractError(ValueError):
    """Raised when an official source no longer matches the audited contract."""


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return _SPACE_RE.sub(" ", str(value)).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _dates(value: Any) -> list[date]:
    output: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        output.append(date(int(year), int(month), int(day)))
    return output


def _short_dates(value: Any) -> list[date]:
    output: list[date] = []
    for year, month, day in _SHORT_DATE_RE.findall(_clean(value)):
        output.append(date(2000 + int(year), int(month), int(day)))
    return output


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _provider_course_id(source: str, identity: str) -> str:
    digest = hashlib.sha256(
        f"{source}\x1f{identity}".encode("utf-8")
    ).hexdigest()[:32]
    return f"UIJEONGBU_{digest}"


def _session_factory() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/136.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
        }
    )
    return value


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_text(value: Any) -> str:
    if isinstance(value, BeautifulSoup):
        return str(value)
    if isinstance(value, str):
        return value
    raise_for_status = getattr(value, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    content = getattr(value, "content", None)
    if content is not None and hasattr(value, "apparent_encoding"):
        try:
            value.encoding = value.apparent_encoding or value.encoding
        except Exception:
            pass
    text = getattr(value, "text", None)
    if text is None:
        raise UijeongbuContractError("request returned no text")
    return str(text)


def _request_value(
    session: Any,
    method: str,
    url: str,
    payload: Optional[Mapping[str, str]],
    timeout: int,
    requester: Optional[Requester],
) -> Any:
    if requester is not None:
        return requester(session, method, url, payload, timeout)
    last_error: Optional[Exception] = None
    for attempt in range(UIJEONGBU_FETCH_ATTEMPTS):
        try:
            if method == "POST":
                response = session.post(url, data=payload, timeout=timeout)
            else:
                response = session.get(url, params=payload, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < UIJEONGBU_FETCH_ATTEMPTS:
                time.sleep(0.2 * (attempt + 1))
    raise UijeongbuContractError(
        f"request failed for {url} ({type(last_error).__name__})"
    )


def _request_soup(
    session: Any,
    method: str,
    url: str,
    payload: Optional[Mapping[str, str]],
    timeout: int,
    requester: Optional[Requester],
) -> BeautifulSoup:
    attempts = (
        6
        if requester is None
        and (urlparse(url).hostname or "").lower() == UIJEONGBU_YOUTH_HOST
        else 1
    )
    document: Optional[BeautifulSoup] = None
    for attempt in range(attempts):
        value = _request_value(session, method, url, payload, timeout, requester)
        document = (
            value
            if isinstance(value, BeautifulSoup)
            else BeautifulSoup(_response_text(value), "html.parser")
        )
        if attempts == 1:
            return document
        title = _clean(
            document.title.get_text(" ", strip=True)
            if document.title
            else ""
        )
        if (
            "의정부시청소년재단" in title
            and document.select_one("form#programSeachForm") is not None
        ):
            return document
        if attempt + 1 < attempts:
            time.sleep(0.4 * (attempt + 1))
    if document is None:
        raise UijeongbuContractError("request returned no document")
    return document


def _request_json(
    session: Any,
    url: str,
    payload: Optional[Mapping[str, str]],
    timeout: int,
    requester: Optional[Requester],
) -> Mapping[str, Any]:
    value = _request_value(session, "GET", url, payload, timeout, requester)
    if isinstance(value, Mapping):
        return value
    json_method = getattr(value, "json", None)
    if callable(json_method):
        data = json_method()
    else:
        data = json.loads(_response_text(value))
    if not isinstance(data, Mapping):
        raise UijeongbuContractError("youth detail is not an object")
    return data


def _safe_https(value: Any, hosts: Optional[set[str]] = None) -> str:
    url = _clean(value)
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    if hosts is not None and parsed.hostname.lower() not in hosts:
        return ""
    return url


def _base_row(
    source: str,
    identity: str,
    title: str,
    branch: str,
    raw_url: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    branch_code = (
        "UIJEONGBU_"
        + hashlib.sha1(_normalized(branch).encode("utf-8")).hexdigest()[:16]
    )
    return {
        "provider": UIJEONGBU_PROVIDER,
        "provider_course_id": _provider_course_id(source, identity),
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": branch,
        "branch_code": branch_code,
        "preserve_branch": True,
        "municipality_code": UIJEONGBU_MUNICIPALITY_CODE,
        "municipality_name": UIJEONGBU_MUNICIPALITY_NAME,
        "sido": UIJEONGBU_SIDO,
        "sigungu": UIJEONGBU_SIGUNGU,
        "provider_organizer": branch,
        "venue_name": branch,
        "category": "교육",
        "program_type": "강좌",
        "raw_url": raw_url,
        "application_url": "",
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "status": "CLOSED",
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": "",
        "apply_start": "",
        "apply_end": "",
        "schedule_raw": "",
        "fee": "",
        "capacity": "",
        "target": "",
        "description": title,
        "source_group": "municipal_reservation",
        "collection_category": "교육",
        "domain_category": "교육·강좌",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "operator_type": "지자체/공공기관",
        "collection_type": "static_html+detail_validation",
        "raw_fields": {
            "source_kind": source,
            "source_identity": identity,
            "ownership": "origin_owner",
            "municipality_code": UIJEONGBU_MUNICIPALITY_CODE,
        },
    }


def _parse_sugang_page(
    soup: BeautifulSoup,
    page: int,
) -> tuple[list[dict[str, Any]], int, int]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "평생학습강좌 수강신청" not in title:
        raise UijeongbuContractError(f"sugang page {page}: wrong page title")
    table = soup.select_one("table#bbsList")
    if table is None:
        raise UijeongbuContractError(f"sugang page {page}: course table missing")
    headings = [_clean(node.get_text(" ", strip=True)) for node in table.select("thead th")]
    required = ("번호", "강좌명", "교육기간", "신청기간", "상태", "보기")
    if len(headings) != 6 or any(
        token not in headings[index] for index, token in enumerate(required)
    ):
        raise UijeongbuContractError(f"sugang page {page}: headers changed")
    text = _clean(soup.get_text(" ", strip=True))
    total_match = re.search(
        r"총\s*([\d,]+)건\s*\(\s*\d+/(\d+)페이지\s*\)", text
    )
    if not total_match:
        raise UijeongbuContractError(f"sugang page {page}: total missing")
    total = int(total_match.group(1).replace(",", ""))
    last = int(total_match.group(2))
    rows: list[dict[str, Any]] = []
    for source_row in table.select("tbody tr"):
        cells = source_row.select("td")
        control = source_row.select_one("td.subject a[onclick]")
        if control is None:
            empty_text = _clean(source_row.get_text(" ", strip=True))
            if not empty_text or (
                empty_text == "등록된 강좌가 없습니다."
                and len(cells) == 1
                and _clean(cells[0].get("colspan")) == "6"
            ):
                continue
            raise UijeongbuContractError(
                f"sugang page {page}: non-course row"
            )
        if len(cells) != 6:
            raise UijeongbuContractError(
                f"sugang page {page}: course row has {len(cells)} cells"
            )
        identity_match = _LEARNING_ID_RE.search(
            _clean(control.get("onclick"))
        )
        if not identity_match:
            raise UijeongbuContractError(
                f"sugang page {page}: identity control changed"
            )
        identity = identity_match.group(1)
        title_node = control.select_one(".tit")
        branch_node = control.select_one(".org")
        title_value = _clean(
            title_node.get_text(" ", strip=True) if title_node else ""
        )
        branch = _clean(
            branch_node.get_text(" ", strip=True) if branch_node else ""
        )
        if not title_value or branch != "도시교육사업본부":
            raise UijeongbuContractError(
                f"sugang page {page}: title/owner changed"
            )
        periods = _short_dates(cells[2].get_text(" ", strip=True))
        if len(periods) != 2 or periods[1] < periods[0]:
            raise UijeongbuContractError(
                f"sugang page {page} {identity}: invalid education period"
            )
        row = _base_row(
            "sugang",
            identity,
            title_value,
            "의정부도시교육재단 도시교육사업본부",
            (
                f"https://{UIJEONGBU_SUGANG_HOST}"
                f"{UIJEONGBU_SUGANG_DETAIL_PATH}?"
                + urlencode({"learning_id": identity})
            ),
            periods[0],
            periods[1],
        )
        general_apply = next(
            (
                node
                for node in cells[3].select("span")
                if "일반" in _clean(node.get_text(" ", strip=True))
                and "접수" in _clean(node.get_text(" ", strip=True))
            ),
            None,
        )
        apply_text = _clean(
            (general_apply or cells[3]).get_text(" ", strip=True)
        )
        apply_dates = _short_dates(apply_text)
        if apply_dates and len(apply_dates) != 2:
            raise UijeongbuContractError(
                f"sugang page {page} {identity}: invalid application period"
            )
        if len(apply_dates) == 2:
            row["apply_start"] = apply_dates[0].isoformat()
            row["apply_end"] = apply_dates[1].isoformat()
            row["apply_period"] = (
                f"{row['apply_start']} ~ {row['apply_end']}"
            )
        capacity_node = cells[3].select_one(".s_type.indigo1")
        row["capacity"] = _clean(
            capacity_node.get_text(" ", strip=True) if capacity_node else ""
        )
        statuses = [
            _clean(node.get_text(" ", strip=True))
            for node in cells[4].select(".s_btn")
            if _clean(node.get_text(" ", strip=True))
        ]
        status_sequence = tuple(statuses)
        if status_sequence not in _SUGANG_STATUS_SEQUENCES:
            raise UijeongbuContractError(
                f"sugang page {page} {identity}: unknown status"
            )
        source_status = " / ".join(statuses)
        row["status"] = _SUGANG_STATUS_SEQUENCES[status_sequence]
        action = cells[5].select_one("a,button")
        action_label = _clean(
            action.get_text(" ", strip=True) if action else ""
        )
        row["raw_fields"].update(
            {
                "list_page": page,
                "source_status": source_status,
                "list_action": action_label,
            }
        )
        rows.append(row)
    return rows, total, last


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    output: dict[str, str] = {}
    for node in soup.select("dl"):
        heading = node.find("dt")
        value = node.find("dd")
        if heading is None or value is None:
            continue
        key = _clean(heading.get_text(" ", strip=True))
        if key and key not in output:
            output[key] = _clean(value.get_text(" ", strip=True))
    return output


def _validate_sugang_detail(
    row: dict[str, Any], soup: BeautifulSoup
) -> tuple[dict[str, Any], bool]:
    identity = _clean(row["raw_fields"]["source_identity"])
    identity_values = {
        _clean(node.get("value"))
        for node in soup.select("input[name='learning_id']")
        if _clean(node.get("value"))
    }
    if identity_values != {identity}:
        raise UijeongbuContractError(
            f"sugang {identity}: detail identity mismatch"
        )
    heading = soup.select_one("h2.enrolTit") or soup.select_one("h2")
    heading_text = _clean(heading.get_text(" ", strip=True) if heading else "")
    if _normalized(row["title"]) not in _normalized(heading_text):
        raise UijeongbuContractError(
            f"sugang {identity}: detail title mismatch"
        )
    pairs = _detail_pairs(soup)
    periods = _dates(pairs.get("교육기간"))
    expected = [
        date.fromisoformat(row["start_date"]),
        date.fromisoformat(row["end_date"]),
    ]
    if periods != expected:
        raise UijeongbuContractError(
            f"sugang {identity}: detail period mismatch"
        )
    detail_apply = _dates(pairs.get("일반신청기간"))
    if row.get("apply_start") and row.get("apply_end"):
        expected_apply = [
            date.fromisoformat(row["apply_start"]),
            date.fromisoformat(row["apply_end"]),
        ]
        if detail_apply != expected_apply:
            raise UijeongbuContractError(
                f"sugang {identity}: detail application period mismatch"
            )
    source_status = _clean(row["raw_fields"].get("source_status"))
    control = soup.select_one("#learning_aply_btn")
    control_label = _clean(
        control.get_text(" ", strip=True) if control else ""
    )
    active = bool(
        control is not None
        and control_label in _APPLICATION_LABELS
        and "fn_learning_apply" in _clean(control.get("onclick"))
    )
    if source_status in {"접수중", "대기접수"} and not active:
        raise UijeongbuContractError(
            f"sugang {identity}: public application control missing"
        )
    if active:
        row["application_url"] = row["raw_url"]
        row["application_type"] = (
            "WAITLIST_APPLY"
            if control_label == "대기자신청"
            else "ONLINE_RESERVATION"
        )
        row["reservation_available"] = True
        row["status"] = "OPEN"
    if pairs.get("교육장소"):
        row["venue_name"] = pairs["교육장소"]
    if pairs.get("교육시간"):
        row["schedule_raw"] = pairs["교육시간"]
    if pairs.get("수강료"):
        row["fee"] = pairs["수강료"]
    if pairs.get("교육대상"):
        row["target"] = pairs["교육대상"]
    facility = bool(
        "대관" in row["title"]
        and "대관" in pairs.get("강좌소개", "")
        and (
            "시설 사용" in pairs.get("강좌소개", "")
            or "대관장소" in pairs.get("강좌소개", "")
        )
    )
    row["raw_fields"].update(
        {
            "detail_verified": True,
            "application_control_verified": active,
            "application_control_label": control_label,
            "source_detail_status": pairs.get("신청상태", ""),
            "education_category": pairs.get("강좌분류", ""),
            "excluded_as_facility": facility,
        }
    )
    return row, facility


def _youth_form_data(
    soup: BeautifulSoup,
    catalogue: UijeongbuYouthCatalogue,
    page: int,
    *,
    force_category: bool,
) -> dict[str, str]:
    form = soup.select_one("form#programSeachForm")
    if form is None:
        raise UijeongbuContractError(
            f"youth {catalogue.key}: programme form missing"
        )
    output: dict[str, str] = {}
    for node in form.select("input[name],select[name]"):
        name = _clean(node.get("name"))
        if not name:
            continue
        if node.name == "select":
            selected = node.select_one("option[selected]") or node.select_one(
                "option"
            )
            output[name] = _clean(selected.get("value") if selected else "")
            continue
        kind = _clean(node.get("type")).lower()
        if kind in {"radio", "checkbox"}:
            if node.has_attr("checked"):
                output[name] = _clean(node.get("value"))
            continue
        output[name] = _clean(node.get("value"))
    output["pageNumber"] = str(page)
    if force_category:
        output.update(
            {
                "topMenu": "program",
                "subMenu": catalogue.code,
                "pgmCate1": catalogue.code,
                "pgmCate2": "",
                "pgmTitle": "",
                "oiSeq": "",
                "piSeq": "",
                "mobileCheck": "P",
            }
        )
    return output


def _youth_contract(
    soup: BeautifulSoup,
    catalogue: UijeongbuYouthCatalogue,
    page: int,
) -> tuple[int, int]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    form = soup.select_one("form#programSeachForm")
    if "의정부시청소년재단" not in title or form is None:
        raise UijeongbuContractError(
            f"youth {catalogue.key} page {page}: waiting/error response"
        )
    sub_menu = form.select_one("#subMenu")
    if _clean(sub_menu.get("value") if sub_menu else "") != catalogue.code:
        raise UijeongbuContractError(
            f"youth {catalogue.key} page {page}: category changed"
        )
    page_node = form.select_one("#pageNumber")
    if _clean(page_node.get("value") if page_node else "") != str(page):
        raise UijeongbuContractError(
            f"youth {catalogue.key} page {page}: page echo changed"
        )
    match = re.search(
        r"fn_page_display\(\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]",
        str(soup),
    )
    if match:
        page_size, total = int(match.group(1)), int(match.group(2))
    else:
        page_size, total = UIJEONGBU_PAGE_SIZE, 0
    if page_size != UIJEONGBU_PAGE_SIZE:
        raise UijeongbuContractError(
            f"youth {catalogue.key}: page size changed"
        )
    return total, max(1, math.ceil(total / page_size))


def _parse_youth_page(
    soup: BeautifulSoup,
    catalogue: UijeongbuYouthCatalogue,
    page: int,
) -> list[dict[str, Any]]:
    tables = [
        table
        for table in soup.select("table")
        if table.select_one("[onclick*='programDetail.view']") is not None
    ]
    if len(tables) > 1:
        raise UijeongbuContractError(
            f"youth {catalogue.key} page {page}: ambiguous programme tables"
        )
    if not tables:
        return []
    rows: list[dict[str, Any]] = []
    for source_row in tables[0].select("tbody tr"):
        detail = source_row.select_one("[onclick*='programDetail.view']")
        if detail is None:
            if not _clean(source_row.get_text(" ", strip=True)):
                continue
            raise UijeongbuContractError(
                f"youth {catalogue.key} page {page}: non-programme row"
            )
        cells = source_row.select("td")
        if len(cells) != 9:
            raise UijeongbuContractError(
                f"youth {catalogue.key} page {page}: expected nine cells"
            )
        identity_match = _YOUTH_ID_RE.search(_clean(detail.get("onclick")))
        if not identity_match:
            raise UijeongbuContractError(
                f"youth {catalogue.key} page {page}: detail identity missing"
            )
        identity = identity_match.group(1)
        title_value = _clean(cells[1].get_text(" ", strip=True))
        if not title_value:
            raise UijeongbuContractError(
                f"youth {catalogue.key} page {page}: empty title"
            )
        apply_dates = _dates(cells[5].get_text(" ", strip=True))
        if len(apply_dates) != 2:
            raise UijeongbuContractError(
                f"youth {catalogue.key} page {page} {identity}: application period changed"
            )
        actions = cells[8].select("a,button")
        detail_controls = [
            item
            for item in actions
            if _YOUTH_ID_RE.search(_clean(item.get("onclick")))
        ]
        if len(detail_controls) != 1:
            raise UijeongbuContractError(
                f"youth {catalogue.key} page {page} {identity}: detail control changed"
            )
        application = next(
            (item for item in actions if item is not detail_controls[0]), None
        )
        application_label = _clean(
            application.get_text(" ", strip=True) if application else ""
        )
        application_script = _clean(
            (application.get("onclick") or application.get("href"))
            if application
            else ""
        )
        apply_match = _YOUTH_APPLY_ID_RE.search(application_script)
        if application_label == "신청":
            if not apply_match or apply_match.group(1) != identity:
                raise UijeongbuContractError(
                    f"youth {catalogue.key} {identity}: application is not course-bound"
                )
        elif application_label not in _INACTIVE_LABELS:
            raise UijeongbuContractError(
                f"youth {catalogue.key} {identity}: unknown application control"
            )
        row = {
            "source_kind": f"youth_{catalogue.key}",
            "identity": identity,
            "title": title_value,
            "catalogue": catalogue.key,
            "catalogue_label": catalogue.label,
            "list_page": page,
            "list_apply_start": apply_dates[0],
            "list_apply_end": apply_dates[1],
            "list_target": _clean(cells[3].get_text(" ", strip=True)),
            "list_schedule": _clean(cells[4].get_text(" ", strip=True)),
            "list_capacity": _clean(cells[6].get_text(" ", strip=True)),
            "list_fee": _clean(cells[7].get_text(" ", strip=True)),
            "application_label": application_label,
            "course_bound_application": bool(apply_match),
        }
        rows.append(row)
    return rows


def _youth_detail_date(value: Any, field: str) -> Optional[date]:
    raw = _clean(value)
    if not raw:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        rendered = raw
    elif re.fullmatch(r"\d{8}(?:\d{2}){0,2}", raw):
        rendered = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    else:
        raise UijeongbuContractError(
            f"youth application phase {field}: invalid date"
        )
    try:
        return date.fromisoformat(rendered)
    except ValueError as exc:
        raise UijeongbuContractError(
            f"youth application phase {field}: invalid calendar date"
        ) from exc


def _youth_application_envelope(
    identity: str,
    data: Mapping[str, Any],
) -> tuple[date, date, tuple[str, ...]]:
    windows: list[tuple[str, date, date]] = []
    for label, start_field, end_field in _YOUTH_APPLICATION_PHASE_PAIRS:
        start = _youth_detail_date(data.get(start_field), start_field)
        end = _youth_detail_date(data.get(end_field), end_field)
        if (start is None) != (end is None):
            raise UijeongbuContractError(
                f"youth {identity}: incomplete {label} application phase"
            )
        if start is None or end is None:
            continue
        if end < start:
            raise UijeongbuContractError(
                f"youth {identity}: reversed {label} application phase"
            )
        windows.append((label, start, end))
    if not windows:
        raise UijeongbuContractError(
            f"youth {identity}: detail application phases missing"
        )
    return (
        min(item[1] for item in windows),
        max(item[2] for item in windows),
        tuple(item[0] for item in windows),
    )


def _validate_youth_detail(
    source: Mapping[str, Any], data: Mapping[str, Any]
) -> dict[str, Any]:
    identity = _clean(source["identity"])
    if _clean(data.get("piSeq")) != identity:
        raise UijeongbuContractError(
            f"youth {identity}: detail identity mismatch"
        )
    if _clean(data.get("pgmTitle")) != _clean(source["title"]):
        raise UijeongbuContractError(
            f"youth {identity}: detail title mismatch"
        )
    start_value = _clean(data.get("operateDtSt"))
    end_value = _clean(data.get("operateDtEd"))
    try:
        start, end = date.fromisoformat(start_value), date.fromisoformat(
            end_value
        )
    except ValueError as exc:
        raise UijeongbuContractError(
            f"youth {identity}: invalid detail period"
        ) from exc
    if end < start:
        raise UijeongbuContractError(
            f"youth {identity}: reversed detail period"
        )
    apply_start_date, apply_end_date, application_phases = (
        _youth_application_envelope(identity, data)
    )
    if (
        apply_start_date != source["list_apply_start"]
        or apply_end_date != source["list_apply_end"]
    ):
        raise UijeongbuContractError(
            f"youth {identity}: list/detail application envelope mismatch"
        )
    apply_start = apply_start_date.isoformat()
    apply_end = apply_end_date.isoformat()
    branch = _clean(data.get("officeName"))
    if not branch:
        raise UijeongbuContractError(f"youth {identity}: office missing")
    raw_url = (
        f"https://{UIJEONGBU_YOUTH_HOST}{UIJEONGBU_YOUTH_DETAIL_PATH}?"
        + urlencode({"piSeq": identity})
    )
    row = _base_row(
        _clean(source["source_kind"]),
        identity,
        _clean(source["title"]),
        branch,
        raw_url,
        start,
        end,
    )
    row["apply_start"] = apply_start
    row["apply_end"] = apply_end
    row["apply_period"] = f"{apply_start} ~ {apply_end}"
    row["schedule_raw"] = _clean(
        data.get("pgmOperate") or source.get("list_schedule")
    )
    fees = [
        _clean(data.get("tuitionYng")),
        _clean(data.get("tuitionAdt")),
    ]
    fees = [item for item in fees if item]
    row["fee"] = "/".join(fees) or _clean(source.get("list_fee"))
    row["capacity"] = _clean(data.get("regularOn")) or _clean(
        source.get("list_capacity")
    )
    age_from, age_to = _clean(data.get("ageFrom")), _clean(
        data.get("ageTo")
    )
    row["target"] = (
        f"{age_from}세 ~ {age_to}세"
        if age_from and age_to
        else _clean(source.get("list_target"))
    )
    control_label = _clean(source.get("application_label"))
    course_bound = bool(source.get("course_bound_application"))
    if control_label == "신청":
        if not course_bound or _clean(data.get("regYnOnline")) != "Y":
            raise UijeongbuContractError(
                f"youth {identity}: public online application contract changed"
            )
        catalogue = _YOUTH_BY_KEY[_clean(source["catalogue"])]
        row["application_url"] = catalogue.url + "?" + urlencode(
            {"pageNumber": int(source["list_page"])}
        )
        row["application_type"] = "COURSE_BOUND_POST_CONTROL"
        row["reservation_available"] = True
        row["status"] = "OPEN"
    elif control_label == "현장신청":
        # The exact public list label is the authoritative on-site contract.
        # Group programmes such as 10419 legitimately render 현장신청 while
        # regYnOfline remains N; requiring that internal flag dropped a live
        # public course after its online phase ended.
        if course_bound:
            raise UijeongbuContractError(
                f"youth {identity}: on-site control unexpectedly course-bound"
            )
        row["application_type"] = "OFFLINE_APPLICATION"
        row["status"] = "OPEN"
    else:
        row["status"] = "CLOSED"
    row["raw_fields"].update(
        {
            "catalogue": source["catalogue"],
            "catalogue_label": source["catalogue_label"],
            "list_page": source["list_page"],
            "detail_verified": True,
            "application_control_verified": (
                course_bound or control_label == "현장신청"
            ),
            "application_control_label": control_label,
            "online_application": _clean(data.get("regYnOnline")) == "Y",
            "offline_application": _clean(data.get("regYnOfline")) == "Y",
            "application_period_contract": (
                "list_envelope_of_all_detail_reception_phases"
            ),
            "application_phase_count": len(application_phases),
        }
    )
    return row


def _parse_uac_page(
    soup: BeautifulSoup,
    page: int,
) -> tuple[list[dict[str, Any]], int]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if not any(marker in title for marker in UIJEONGBU_UAC_TITLE_MARKERS):
        raise UijeongbuContractError(f"uac page {page}: wrong title")
    rows: list[dict[str, Any]] = []
    course_links = soup.select("a[href*='ams_03D.php'][href*='lecture=']")
    for link in course_links:
        source_row = link.find_parent("tr")
        if source_row is None:
            raise UijeongbuContractError(
                f"uac page {page}: course row missing"
            )
        cells = source_row.select("td")
        if len(cells) != 7:
            raise UijeongbuContractError(
                f"uac page {page}: expected seven cells"
            )
        href = urljoin(UIJEONGBU_UAC_LIST_URL, _clean(link.get("href")))
        identity_values = parse_qs(urlparse(href).query).get("lecture", [])
        if len(identity_values) != 1 or not _UAC_ID_RE.fullmatch(
            identity_values[0]
        ):
            raise UijeongbuContractError(
                f"uac page {page}: invalid lecture identity"
            )
        identity = identity_values[0]
        title_value = _clean(link.get_text(" ", strip=True))
        month_days = _MONTH_DAY_RE.findall(
            _clean(cells[1].get_text(" ", strip=True))
        )
        if len(month_days) != 2:
            raise UijeongbuContractError(
                f"uac {identity}: invalid list period"
            )
        year = int(identity[:4])
        start = date(year, int(month_days[0][0]), int(month_days[0][1]))
        end_year = year + (int(month_days[1][0]) < int(month_days[0][0]))
        end = date(end_year, int(month_days[1][0]), int(month_days[1][1]))
        status_node = cells[6].select_one("img[alt]")
        source_status = _clean(status_node.get("alt") if status_node else "")
        if source_status not in _UAC_STATUS:
            raise UijeongbuContractError(
                f"uac {identity}: unknown status {source_status!r}"
            )
        row = _base_row(
            "uac",
            identity,
            title_value,
            UIJEONGBU_UAC_BRANCH,
            href,
            start,
            end,
        )
        row["schedule_raw"] = " ".join(
            value
            for value in (
                _clean(cells[2].get_text(" ", strip=True)),
                _clean(cells[3].get_text(" ", strip=True)),
            )
            if value
        )
        row["venue_name"] = _clean(cells[4].get_text(" ", strip=True))
        row["fee"] = _clean(cells[5].get_text(" ", strip=True))
        row["status"] = _UAC_STATUS[source_status]
        row["raw_fields"].update(
            {"list_page": page, "source_status": source_status}
        )
        rows.append(row)
    # ``page`` in this PHP catalogue is a row-offset token (10, 20, ...),
    # not the displayed page number.  Only the numbered anchor text denotes
    # the actual page boundary; using the query value makes an empty sentinel
    # look like a ten-page catalogue.
    pages = [
        int(label)
        for node in soup.select(".pagging a")
        if (label := _clean(node.get_text(" ", strip=True))).isdigit()
    ]
    return rows, max(pages, default=1)


def _validate_uac_detail(
    row: dict[str, Any], soup: BeautifulSoup
) -> dict[str, Any]:
    identity = _clean(row["raw_fields"]["source_identity"])
    text = _clean(soup.get_text(" ", strip=True))
    if _normalized(row["title"]) not in _normalized(text):
        raise UijeongbuContractError(f"uac {identity}: title mismatch")
    found_dates = set(_dates(text))
    expected = {
        date.fromisoformat(row["start_date"]),
        date.fromisoformat(row["end_date"]),
    }
    if not expected.issubset(found_dates):
        raise UijeongbuContractError(f"uac {identity}: period mismatch")
    source_status = _clean(row["raw_fields"].get("source_status"))
    detail_statuses = {
        _clean(node.get("alt"))
        for node in soup.select("table.academy_D img[src*='state_'][alt]")
        if _clean(node.get("alt"))
    }
    if detail_statuses != {source_status}:
        raise UijeongbuContractError(
            f"uac {identity}: list/detail status mismatch"
        )
    active_urls: list[str] = []
    for node in soup.select("a,button,input[type='submit'],input[type='button']"):
        label = _clean(
            node.get_text(" ", strip=True)
            or node.get("value")
            or node.get("title")
        )
        if label not in _APPLICATION_LABELS:
            continue
        raw = _clean(node.get("href") or node.get("formaction") or node.get("onclick"))
        if identity not in raw:
            continue
        if raw.lower().startswith("http"):
            url = _safe_https(raw, {UIJEONGBU_UAC_HOST})
        else:
            url = row["raw_url"]
        if url:
            active_urls.append(url)
    active_urls = sorted(set(active_urls))
    if len(active_urls) > 1:
        raise UijeongbuContractError(
            f"uac {identity}: application control is ambiguous"
        )
    if row["status"] != "OPEN" and active_urls:
        raise UijeongbuContractError(
            f"uac {identity}: inactive detail exposes active control"
        )
    if active_urls:
        row["application_url"] = active_urls[0]
        row["application_type"] = "ONLINE_RESERVATION"
        row["reservation_available"] = True
        row["status"] = "OPEN"
    row["raw_fields"].update(
        {
            "detail_verified": True,
            "application_control_verified": bool(active_urls),
            "source_detail_status": source_status,
            "application_surface": (
                "course_bound_control"
                if active_urls
                else "status_only_no_public_control"
            ),
        }
    )
    return row


def _young_page_rows(
    soup: BeautifulSoup,
    page: int,
) -> list[dict[str, Any]]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "프로그램 신청" not in title:
        raise UijeongbuContractError(
            f"young-center page {page}: wrong page title"
        )
    output: dict[str, dict[str, Any]] = {}
    for link in soup.select("a[href*='/pages/program/detail/']"):
        href = urljoin(UIJEONGBU_YOUNG_CENTER_LIST_URL, _clean(link.get("href")))
        identity_match = _YOUNG_ID_RE.search(urlparse(href).path + "/")
        if not identity_match:
            continue
        identity = identity_match.group(1)
        card = link.find_parent("li") or link.find_parent("div")
        if card is None:
            raise UijeongbuContractError(
                f"young-center {identity}: card missing"
            )
        text = _clean(card.get_text(" ", strip=True))
        title_match = re.match(r"(.+?)\s*교육기간\s*:\s*", text)
        periods = _dates(text)
        actions = [
            node
            for node in card.select("a,button")
            if _clean(node.get_text(" ", strip=True)) in (
                _APPLICATION_LABELS | _INACTIVE_LABELS
            )
        ]
        if len(actions) != 1:
            raise UijeongbuContractError(
                f"young-center {identity}: application state ambiguous"
            )
        action_label = _clean(actions[0].get_text(" ", strip=True))
        missing_archive_period = not periods
        if not title_match or len(periods) == 1:
            raise UijeongbuContractError(
                f"young-center {identity}: card fields changed"
            )
        if missing_archive_period:
            if not (
                action_label == "마감"
                and int(identity)
                <= UIJEONGBU_YOUNG_CENTER_BLANK_ARCHIVE_MAX_ID
            ):
                raise UijeongbuContractError(
                    f"young-center {identity}: current/new period missing"
                )
            start = end = date(1970, 1, 1)
        else:
            start, end = periods[0], periods[1]
        reversed_archive_period = end < start
        if (
            reversed_archive_period
            and identity not in UIJEONGBU_YOUNG_CENTER_REVERSED_ARCHIVE_IDS
        ):
            raise UijeongbuContractError(
                f"young-center {identity}: reversed period"
            )
        if reversed_archive_period:
            start, end = min(start, end), max(start, end)
        title_value = _clean(title_match.group(1))
        row = _base_row(
            "young_center",
            identity,
            title_value,
            "의정부시 청년센터 청년공감터",
            href,
            start,
            end,
        )
        schedule_match = re.search(
            r"수업일시\s*:\s*(.+?)\s*수업료\s*:", text
        )
        fee_match = re.search(r"수업료\s*:\s*(.+?)\s*정원\s*:", text)
        capacity_match = re.search(r"정원\s*:\s*([\d,]+\s*명)", text)
        row["schedule_raw"] = _clean(
            schedule_match.group(1) if schedule_match else ""
        )
        row["fee"] = _clean(fee_match.group(1) if fee_match else "")
        row["capacity"] = _clean(
            capacity_match.group(1) if capacity_match else ""
        )
        row["status"] = "OPEN" if action_label == "신청하기" else "CLOSED"
        row["raw_fields"].update(
            {
                "list_page": page,
                "pinned": identity in UIJEONGBU_YOUNG_CENTER_PINNED_IDS,
                "source_period_missing": missing_archive_period,
                "source_period_reversed": reversed_archive_period,
                "list_application_label": action_label,
            }
        )
        output[identity] = row
    return list(output.values())


def _young_last_page(soup: BeautifulSoup) -> int:
    node = soup.select_one(".pagination .last[onclick]")
    match = re.search(
        r"fn_goPage\(\s*(\d+)\s*\)", _clean(node.get("onclick") if node else "")
    )
    if not match:
        raise UijeongbuContractError("young-center last page missing")
    return int(match.group(1))


def _validate_young_detail(
    row: dict[str, Any], soup: BeautifulSoup
) -> dict[str, Any]:
    identity = _clean(row["raw_fields"]["source_identity"])
    identity_values = {
        _clean(node.get("value"))
        for node in soup.select("input[name='pgm_pid']")
        if _clean(node.get("value"))
    }
    if identity_values and identity_values != {identity}:
        raise UijeongbuContractError(
            f"young-center {identity}: detail identity mismatch"
        )
    text = _clean(soup.get_text(" ", strip=True))
    if _normalized(row["title"]) not in _normalized(text):
        raise UijeongbuContractError(
            f"young-center {identity}: detail title mismatch"
        )
    for expected in (row["start_date"], row["end_date"]):
        if expected not in text:
            raise UijeongbuContractError(
                f"young-center {identity}: detail period mismatch"
            )
    list_label = _clean(row["raw_fields"].get("list_application_label"))
    controls: list[str] = []
    for node in soup.select("a[href]"):
        if _clean(node.get_text(" ", strip=True)) != "신청하기":
            continue
        resolved = _safe_https(urljoin(row["raw_url"], _clean(node.get("href"))))
        if resolved:
            controls.append(resolved)
    controls = sorted(set(controls))
    if list_label == "신청하기" and len(controls) != 1:
        raise UijeongbuContractError(
            f"young-center {identity}: public application control missing"
        )
    if list_label != "신청하기" and controls:
        raise UijeongbuContractError(
            f"young-center {identity}: closed row exposes active control"
        )
    if controls:
        row["application_url"] = controls[0]
        row["application_type"] = "EXTERNAL_APPLICATION"
        row["reservation_available"] = True
        row["status"] = "OPEN"
    pairs = _detail_pairs(soup)
    if pairs.get("강의실"):
        row["venue_name"] = pairs["강의실"]
    if pairs.get("강의일시"):
        row["schedule_raw"] = pairs["강의일시"]
    if pairs.get("수강료"):
        row["fee"] = pairs["수강료"]
    if pairs.get("수강자격"):
        row["target"] = pairs["수강자격"]
    row["raw_fields"].update(
        {
            "detail_verified": True,
            "application_control_verified": bool(controls),
            "application_control_label": list_label,
        }
    )
    return row


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            _clean(row.get("raw_fields", {}).get("source_kind") or row.get("source_kind")),
            _clean(row.get("raw_fields", {}).get("source_identity") or row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
        )
        for row in rows
    )


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "required_list_requests": 0,
        "source_total": 0,
        "source_rows": 0,
        "source_totals": {},
        "source_page_counts": {},
        "current_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "excluded_facility_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_retries": 0,
        "detail_errors": 0,
        "application_control_count": 0,
        "duplicate_identity_count": 0,
        "duplicate_url_count": 0,
        "semantic_overlap_count": 0,
        "status_counts": {},
        "source_kind_counts": {},
        "branch_counts": {},
        "municipality_counts": {},
        "pagination_detected": False,
        "pagination_complete": False,
        "partitions_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "canonical_provider": UIJEONGBU_PROVIDER,
        "canonical_candidate_id": UIJEONGBU_CANONICAL_CANDIDATE_ID,
        "canonical_url": UIJEONGBU_CANONICAL_URL,
        "legacy_candidate_id": UIJEONGBU_LEGACY_CANDIDATE_ID,
        "legacy_provider": UIJEONGBU_LEGACY_PROVIDER,
        "legacy_url": UIJEONGBU_LEGACY_URL,
        "ownership_audit": dict(UIJEONGBU_OWNERSHIP_AUDIT),
        "non_executing_mirror_offices": dict(
            UIJEONGBU_NON_EXECUTING_MIRRORS
        ),
        "covered_municipalities": [
            {
                "code": UIJEONGBU_MUNICIPALITY_CODE,
                "sido": UIJEONGBU_SIDO,
                "sigungu": UIJEONGBU_SIGUNGU,
                "full_name": UIJEONGBU_MUNICIPALITY_NAME,
            }
        ],
    }


def is_uijeongbu_target(target: Any) -> bool:
    provider = _clean(
        target.get("provider") if isinstance(target, Mapping) else getattr(target, "provider", "")
    ).upper()
    raw_url = _clean(
        target.get("url") if isinstance(target, Mapping) else getattr(target, "url", "")
    )
    if provider not in {UIJEONGBU_PROVIDER, UIJEONGBU_LEGACY_PROVIDER}:
        return False
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if provider == UIJEONGBU_PROVIDER:
        return host == UIJEONGBU_SUGANG_HOST and path in {
            "/ilms/learning/officeMainList.do",
            "/ilms/learning/learningList.do",
        }
    return (
        host in {"www.ui4u.go.kr", "ui4u.go.kr"}
        and path == "/reservation/youthProgram/list.do"
        and parse_qs(parsed.query).get("mId") == ["0104000000"]
    )


def collect_uijeongbu_education_courses(
    target: Any,
    timeout: int = 45,
    max_pages: int = 400,
    detail_limit: int = 1000,
    *,
    requester: Optional[Requester] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = UIJEONGBU_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Uijeongbu education snapshot."""

    meta = _base_meta()
    if not is_uijeongbu_target(target):
        meta["configured_collection_error"] = (
            "target is not the canonical or audited legacy Uijeongbu owner"
        )
        return [], UIJEONGBU_PARSER, meta
    try:
        allowed_pages = max(0, int(max_pages))
        allowed_details = max(0, int(detail_limit))
        worker_count = max(1, min(int(max_workers), UIJEONGBU_MAX_WORKERS))
        reference_day = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = f"invalid collection limits/date: {exc}"
        meta["source_cap_reached"] = True
        return [], UIJEONGBU_PARSER, meta
    if session_factory is None:
        session_factory = _session_factory

    errors: list[str] = []
    list_requests = 0
    main_session: Any = None
    all_source_rows: list[dict[str, Any]] = []
    youth_sources: list[dict[str, Any]] = []
    current_detail_tasks: dict[tuple[str, str], tuple[str, Any]] = {}
    facility_count = 0

    def budget_request() -> bool:
        nonlocal list_requests
        list_requests += 1
        if list_requests > allowed_pages:
            meta["source_cap_reached"] = True
            errors.append("max_pages is below the complete fan-out boundary")
            return False
        return True

    try:
        main_session = session_factory()

        # Neuron / city education archive.
        if not budget_request():
            raise UijeongbuContractError(errors[-1])
        first_sugang = _request_soup(
            main_session,
            "GET",
            f"https://{UIJEONGBU_SUGANG_HOST}{UIJEONGBU_SUGANG_LIST_PATH}",
            {"office_id": UIJEONGBU_SUGANG_OFFICE_ID, "pageIndex": "1"},
            timeout,
            requester,
        )
        first_rows, sugang_total, sugang_last = _parse_sugang_page(
            first_sugang, 1
        )
        sugang_pages = [first_rows]
        for page in range(2, sugang_last + 1):
            if not budget_request():
                raise UijeongbuContractError(errors[-1])
            document = _request_soup(
                main_session,
                "GET",
                f"https://{UIJEONGBU_SUGANG_HOST}{UIJEONGBU_SUGANG_LIST_PATH}",
                {
                    "office_id": UIJEONGBU_SUGANG_OFFICE_ID,
                    "pageIndex": str(page),
                },
                timeout,
                requester,
            )
            rows, total, last = _parse_sugang_page(document, page)
            if total != sugang_total or last != sugang_last:
                raise UijeongbuContractError(
                    f"sugang page {page}: pagination total changed"
                )
            sugang_pages.append(rows)
        if not budget_request():
            raise UijeongbuContractError(errors[-1])
        sentinel = _request_soup(
            main_session,
            "GET",
            f"https://{UIJEONGBU_SUGANG_HOST}{UIJEONGBU_SUGANG_LIST_PATH}",
            {
                "office_id": UIJEONGBU_SUGANG_OFFICE_ID,
                "pageIndex": str(sugang_last + 1),
            },
            timeout,
            requester,
        )
        sentinel_rows, sentinel_total, sentinel_last = _parse_sugang_page(
            sentinel, sugang_last + 1
        )
        if sentinel_rows or sentinel_total != sugang_total or sentinel_last != sugang_last:
            raise UijeongbuContractError("sugang sentinel is not empty/stable")
        if not budget_request():
            raise UijeongbuContractError(errors[-1])
        recheck = _request_soup(
            main_session,
            "GET",
            f"https://{UIJEONGBU_SUGANG_HOST}{UIJEONGBU_SUGANG_LIST_PATH}",
            {"office_id": UIJEONGBU_SUGANG_OFFICE_ID, "pageIndex": "1"},
            timeout,
            requester,
        )
        recheck_rows, recheck_total, recheck_last = _parse_sugang_page(
            recheck, 1
        )
        if (
            _page_signature(first_rows) != _page_signature(recheck_rows)
            or recheck_total != sugang_total
            or recheck_last != sugang_last
        ):
            raise UijeongbuContractError("sugang page one changed on recheck")
        sugang_rows = [row for page in sugang_pages for row in page]
        if len(sugang_rows) != sugang_total:
            raise UijeongbuContractError(
                "sugang advertised total does not reconcile with all pages"
            )
        sugang_ids = [row["raw_fields"]["source_identity"] for row in sugang_rows]
        if len(sugang_ids) != len(set(sugang_ids)):
            raise UijeongbuContractError("sugang duplicate course identity")
        all_source_rows.extend(sugang_rows)
        meta["source_totals"]["sugang"] = sugang_total
        meta["source_page_counts"]["sugang"] = sugang_last

        # Youth Foundation catalogues.  Sports is selected by POST and must
        # retain its selected form values on every subsequent page.
        for catalogue in UIJEONGBU_YOUTH_CATALOGUES:
            youth_session = session_factory()
            try:
                if not budget_request():
                    raise UijeongbuContractError(errors[-1])
                initial = _request_soup(
                    youth_session,
                    "GET",
                    catalogue.url,
                    {"pageNumber": "1"},
                    timeout,
                    requester,
                )
                first = initial
                if not catalogue.native:
                    if not budget_request():
                        raise UijeongbuContractError(errors[-1])
                    first = _request_soup(
                        youth_session,
                        "POST",
                        catalogue.url,
                        _youth_form_data(
                            initial,
                            catalogue,
                            1,
                            force_category=True,
                        ),
                        timeout,
                        requester,
                    )
                total, last = _youth_contract(first, catalogue, 1)
                first_parsed = _parse_youth_page(first, catalogue, 1)
                pages = [first_parsed]
                for page in range(2, last + 1):
                    if not budget_request():
                        raise UijeongbuContractError(errors[-1])
                    method = "GET" if catalogue.native else "POST"
                    payload = (
                        {"pageNumber": str(page)}
                        if catalogue.native
                        else _youth_form_data(
                            first,
                            catalogue,
                            page,
                            force_category=False,
                        )
                    )
                    document = _request_soup(
                        youth_session,
                        method,
                        catalogue.url,
                        payload,
                        timeout,
                        requester,
                    )
                    page_total, page_last = _youth_contract(
                        document, catalogue, page
                    )
                    if page_total != total or page_last != last:
                        raise UijeongbuContractError(
                            f"youth {catalogue.key} page {page}: total changed"
                        )
                    pages.append(
                        _parse_youth_page(document, catalogue, page)
                    )
                if not budget_request():
                    raise UijeongbuContractError(errors[-1])
                sentinel_page = last + 1
                sentinel_method = "GET" if catalogue.native else "POST"
                sentinel_payload = (
                    {"pageNumber": str(sentinel_page)}
                    if catalogue.native
                    else _youth_form_data(
                        first,
                        catalogue,
                        sentinel_page,
                        force_category=False,
                    )
                )
                youth_sentinel = _request_soup(
                    youth_session,
                    sentinel_method,
                    catalogue.url,
                    sentinel_payload,
                    timeout,
                    requester,
                )
                sentinel_total, sentinel_last = _youth_contract(
                    youth_sentinel, catalogue, sentinel_page
                )
                if (
                    _parse_youth_page(
                        youth_sentinel, catalogue, sentinel_page
                    )
                    or sentinel_total != total
                    or sentinel_last != last
                ):
                    raise UijeongbuContractError(
                        f"youth {catalogue.key}: sentinel is not empty/stable"
                    )
                if not budget_request():
                    raise UijeongbuContractError(errors[-1])
                recheck_method = "GET" if catalogue.native else "POST"
                recheck_payload = (
                    {"pageNumber": "1"}
                    if catalogue.native
                    else _youth_form_data(
                        first,
                        catalogue,
                        1,
                        force_category=False,
                    )
                )
                youth_recheck = _request_soup(
                    youth_session,
                    recheck_method,
                    catalogue.url,
                    recheck_payload,
                    timeout,
                    requester,
                )
                re_total, re_last = _youth_contract(
                    youth_recheck, catalogue, 1
                )
                re_rows = _parse_youth_page(
                    youth_recheck, catalogue, 1
                )
                if (
                    _page_signature(first_parsed)
                    != _page_signature(re_rows)
                    or re_total != total
                    or re_last != last
                ):
                    raise UijeongbuContractError(
                        f"youth {catalogue.key}: page one changed on recheck"
                    )
                rows = [row for page_rows in pages for row in page_rows]
                identities = [row["identity"] for row in rows]
                if len(rows) != total:
                    raise UijeongbuContractError(
                        f"youth {catalogue.key}: advertised total mismatch"
                    )
                if len(identities) != len(set(identities)):
                    raise UijeongbuContractError(
                        f"youth {catalogue.key}: duplicate identity"
                    )
                youth_sources.extend(rows)
                meta["source_totals"][f"youth_{catalogue.key}"] = total
                meta["source_page_counts"][f"youth_{catalogue.key}"] = last
            finally:
                _close_quietly(youth_session)

        youth_identity_pairs = [
            (row["catalogue"], row["identity"]) for row in youth_sources
        ]
        if len(youth_identity_pairs) != len(set(youth_identity_pairs)):
            raise UijeongbuContractError(
                "youth catalogues overlap by source identity"
            )

        # Cultural Foundation education catalogue.
        if not budget_request():
            raise UijeongbuContractError(errors[-1])
        uac_first = _request_soup(
            main_session,
            "GET",
            UIJEONGBU_UAC_LIST_URL,
            {"page": "1", "page_start": "1"},
            timeout,
            requester,
        )
        uac_first_rows, uac_last = _parse_uac_page(uac_first, 1)
        uac_pages = [uac_first_rows]
        for page in range(2, uac_last + 1):
            if not budget_request():
                raise UijeongbuContractError(errors[-1])
            document = _request_soup(
                main_session,
                "GET",
                UIJEONGBU_UAC_LIST_URL,
                {"page": str(page), "page_start": str((page - 1) * 10 + 1)},
                timeout,
                requester,
            )
            rows, page_last = _parse_uac_page(document, page)
            if page_last != uac_last:
                raise UijeongbuContractError(
                    f"uac page {page}: pagination changed"
                )
            uac_pages.append(rows)
        if not budget_request():
            raise UijeongbuContractError(errors[-1])
        uac_sentinel = _request_soup(
            main_session,
            "GET",
            UIJEONGBU_UAC_LIST_URL,
            {
                "page": str(uac_last + 1),
                "page_start": str(uac_last * 10 + 1),
            },
            timeout,
            requester,
        )
        sentinel_rows, sentinel_last = _parse_uac_page(
            uac_sentinel, uac_last + 1
        )
        if sentinel_rows or sentinel_last != uac_last:
            raise UijeongbuContractError("uac sentinel is not empty/stable")
        if not budget_request():
            raise UijeongbuContractError(errors[-1])
        uac_recheck = _request_soup(
            main_session,
            "GET",
            UIJEONGBU_UAC_LIST_URL,
            {"page": "1", "page_start": "1"},
            timeout,
            requester,
        )
        uac_re_rows, uac_re_last = _parse_uac_page(uac_recheck, 1)
        if (
            _page_signature(uac_first_rows) != _page_signature(uac_re_rows)
            or uac_re_last != uac_last
        ):
            raise UijeongbuContractError("uac page one changed on recheck")
        uac_rows = [row for page in uac_pages for row in page]
        uac_ids = [row["raw_fields"]["source_identity"] for row in uac_rows]
        if len(uac_ids) != len(set(uac_ids)):
            raise UijeongbuContractError("uac duplicate lecture identity")
        all_source_rows.extend(uac_rows)
        meta["source_totals"]["uac"] = len(uac_rows)
        meta["source_page_counts"]["uac"] = uac_last

        # City youth centre: audited evergreen rows are pinned on every page.
        if not budget_request():
            raise UijeongbuContractError(errors[-1])
        young_first = _request_soup(
            main_session,
            "GET",
            UIJEONGBU_YOUNG_CENTER_LIST_URL,
            {"page": "1"},
            timeout,
            requester,
        )
        young_last = _young_last_page(young_first)
        young_pages = [_young_page_rows(young_first, 1)]
        for page in range(2, young_last + 1):
            if not budget_request():
                raise UijeongbuContractError(errors[-1])
            document = _request_soup(
                main_session,
                "GET",
                UIJEONGBU_YOUNG_CENTER_LIST_URL,
                {"page": str(page)},
                timeout,
                requester,
            )
            if _young_last_page(document) != young_last:
                raise UijeongbuContractError(
                    f"young-center page {page}: last page changed"
                )
            young_pages.append(_young_page_rows(document, page))
        if not budget_request():
            raise UijeongbuContractError(errors[-1])
        young_sentinel_soup = _request_soup(
            main_session,
            "GET",
            UIJEONGBU_YOUNG_CENTER_LIST_URL,
            {"page": str(young_last + 1)},
            timeout,
            requester,
        )
        young_sentinel = _young_page_rows(
            young_sentinel_soup, young_last + 1
        )
        sentinel_ids = {
            row["raw_fields"]["source_identity"] for row in young_sentinel
        }
        if sentinel_ids != UIJEONGBU_YOUNG_CENTER_PINNED_IDS:
            raise UijeongbuContractError(
                "young-center sentinel contains non-pinned programmes"
            )
        if not budget_request():
            raise UijeongbuContractError(errors[-1])
        young_recheck_soup = _request_soup(
            main_session,
            "GET",
            UIJEONGBU_YOUNG_CENTER_LIST_URL,
            {"page": "1"},
            timeout,
            requester,
        )
        young_recheck = _young_page_rows(young_recheck_soup, 1)
        if (
            _young_last_page(young_recheck_soup) != young_last
            or _page_signature(young_pages[0])
            != _page_signature(young_recheck)
        ):
            raise UijeongbuContractError(
                "young-center page one changed on recheck"
            )
        pinned_by_page: list[set[str]] = []
        normal_rows: list[dict[str, Any]] = []
        pinned_rows: dict[str, dict[str, Any]] = {}
        for page_number, page_rows in enumerate(young_pages, 1):
            pinned_ids: set[str] = set()
            normal_page: list[dict[str, Any]] = []
            for row in page_rows:
                identity = row["raw_fields"]["source_identity"]
                if identity in UIJEONGBU_YOUNG_CENTER_PINNED_IDS:
                    pinned_ids.add(identity)
                    pinned_rows[identity] = row
                else:
                    normal_page.append(row)
            pinned_by_page.append(pinned_ids)
            expected_size = (
                UIJEONGBU_YOUNG_CENTER_NORMAL_PAGE_SIZE
                if page_number < young_last
                else len(normal_page)
            )
            if page_number < young_last and len(normal_page) != expected_size:
                raise UijeongbuContractError(
                    f"young-center page {page_number}: normal page size changed"
                )
            if page_number == young_last and not (
                1 <= len(normal_page) <= UIJEONGBU_YOUNG_CENTER_NORMAL_PAGE_SIZE
            ):
                raise UijeongbuContractError(
                    "young-center last page boundary changed"
                )
            normal_rows.extend(normal_page)
        if any(
            values != UIJEONGBU_YOUNG_CENTER_PINNED_IDS
            for values in pinned_by_page
        ):
            raise UijeongbuContractError(
                "young-center pinned programme contract changed"
            )
        normal_ids = [
            row["raw_fields"]["source_identity"] for row in normal_rows
        ]
        if len(normal_ids) != len(set(normal_ids)):
            raise UijeongbuContractError(
                "young-center normal programme duplicated across pages"
            )
        young_rows = normal_rows + [
            pinned_rows[value]
            for value in sorted(UIJEONGBU_YOUNG_CENTER_PINNED_IDS)
        ]
        all_source_rows.extend(young_rows)
        meta["source_totals"]["young_center"] = len(young_rows)
        meta["source_page_counts"]["young_center"] = young_last

        # Current/future details.  Youth list rows require their JSON detail
        # before their education end date is known, so all current search rows
        # are validated.
        for row in all_source_rows:
            if date.fromisoformat(row["end_date"]) < reference_day:
                continue
            source = row["raw_fields"]["source_kind"]
            identity = row["raw_fields"]["source_identity"]
            current_detail_tasks[(source, identity)] = (
                "html",
                row,
            )
        for source in youth_sources:
            current_detail_tasks[(source["source_kind"], source["identity"])] = (
                "json",
                source,
            )
        if len(current_detail_tasks) > allowed_details:
            meta["source_cap_reached"] = True
            raise UijeongbuContractError(
                "detail_limit is below the complete current-detail boundary"
            )

        thread_state = local()
        thread_sessions: list[Any] = []
        session_lock = Lock()

        def detail_session() -> Any:
            value = getattr(thread_state, "session", None)
            if value is None:
                value = session_factory()
                thread_state.session = value
                with session_lock:
                    thread_sessions.append(value)
            return value

        def fetch_detail(
            item: tuple[tuple[str, str], tuple[str, Any]]
        ) -> tuple[tuple[str, str], Any, int]:
            key, (kind, payload) = item
            source, identity = key
            if kind == "json":
                value = detail_session()
                data = _request_json(
                    value,
                    f"https://{UIJEONGBU_YOUTH_HOST}{UIJEONGBU_YOUTH_DETAIL_PATH}",
                    {"piSeq": identity},
                    timeout,
                    requester,
                )
                return key, data, 0
            row = payload
            if source == "sugang":
                contract_retries = 0
                document: BeautifulSoup | None = None
                while contract_retries < UIJEONGBU_FETCH_ATTEMPTS:
                    # Neuron stores the selected course in server-side
                    # session state.  Reusing a session for another detail
                    # can therefore return that prior course even though the
                    # query string is correct.  A fresh anonymous session per
                    # attempt prevents cross-course state leakage.
                    isolated_session = session_factory()
                    try:
                        document = _request_soup(
                            isolated_session,
                            "GET",
                            row["raw_url"],
                            None,
                            timeout,
                            requester,
                        )
                    finally:
                        _close_quietly(isolated_session)
                    identity_values = {
                        _clean(node.get("value"))
                        for node in document.select("input[name='learning_id']")
                        if _clean(node.get("value"))
                    }
                    if identity_values == {identity}:
                        break
                    contract_retries += 1
                assert document is not None
                return key, document, min(
                    contract_retries, UIJEONGBU_FETCH_ATTEMPTS - 1
                )

            value = detail_session()
            document = _request_soup(
                value,
                "GET",
                row["raw_url"],
                None,
                timeout,
                requester,
            )
            return key, document, 0

        detail_results: dict[tuple[str, str], Any] = {}
        meta["detail_attempts"] = len(current_detail_tasks)
        try:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(fetch_detail, item): item[0]
                    for item in current_detail_tasks.items()
                }
                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        result_key, result, contract_retries = future.result()
                        detail_results[result_key] = result
                        meta["detail_retries"] += contract_retries
                    except Exception as exc:
                        errors.append(
                            f"detail {key[0]}:{key[1]} failed ({type(exc).__name__}: {exc})"
                        )
        finally:
            for value in thread_sessions:
                _close_quietly(value)
        meta["detail_pages"] = len(detail_results)
        meta["detail_errors"] = len(errors)
        if errors or len(detail_results) != len(current_detail_tasks):
            raise UijeongbuContractError(
                "one or more current details could not be fetched"
            )

        returned: list[dict[str, Any]] = []
        for row in all_source_rows:
            if date.fromisoformat(row["end_date"]) < reference_day:
                continue
            source = row["raw_fields"]["source_kind"]
            identity = row["raw_fields"]["source_identity"]
            document = detail_results[(source, identity)]
            if source == "sugang":
                validated, facility = _validate_sugang_detail(row, document)
                if facility:
                    facility_count += 1
                    continue
                returned.append(validated)
            elif source == "uac":
                returned.append(_validate_uac_detail(row, document))
            elif source == "young_center":
                returned.append(_validate_young_detail(row, document))
            else:
                raise UijeongbuContractError(
                    f"unknown HTML detail source {source}"
                )
        for source in youth_sources:
            key = (source["source_kind"], source["identity"])
            validated = _validate_youth_detail(source, detail_results[key])
            if date.fromisoformat(validated["end_date"]) >= reference_day:
                returned.append(validated)

        all_identities = [
            (
                row["raw_fields"]["source_kind"],
                row["raw_fields"]["source_identity"],
            )
            for row in returned
        ]
        raw_urls = [_clean(row.get("raw_url")) for row in returned]
        semantic_keys = [
            (
                _normalized(row.get("title")),
                row.get("start_date"),
                row.get("end_date"),
                _normalized(row.get("schedule_raw")),
                _normalized(row.get("branch")),
            )
            for row in returned
        ]
        meta["duplicate_identity_count"] = len(all_identities) - len(
            set(all_identities)
        )
        meta["duplicate_url_count"] = len(raw_urls) - len(set(raw_urls))
        meta["semantic_overlap_count"] = len(semantic_keys) - len(
            set(semantic_keys)
        )
        if (
            meta["duplicate_identity_count"]
            or meta["duplicate_url_count"]
            or meta["semantic_overlap_count"]
        ):
            raise UijeongbuContractError(
                "selected origin owners still overlap"
            )
        if dedupe_rows is not None:
            deduped = list(dedupe_rows(returned))
            if len(deduped) != len(returned):
                raise UijeongbuContractError(
                    "repository dedupe detected an unexpected owner overlap"
                )
            returned = deduped

        # No PII-bearing source field is copied; additionally reject any
        # accidental phone/email in raw_fields or descriptions.
        for row in returned:
            if not _clean(row.get("target")):
                row["target"] = "공식 페이지 미기재"
                row.setdefault("raw_fields", {})[
                    "target_source_omission"
                ] = True
            safe_text = json.dumps(
                {
                    "description": row.get("description", ""),
                    "raw_fields": row.get("raw_fields", {}),
                },
                ensure_ascii=False,
            )
            if _PHONE_RE.search(safe_text) or _EMAIL_RE.search(safe_text):
                raise UijeongbuContractError(
                    "PII allowlist violation in persisted row"
                )

        source_rows = len(all_source_rows) + len(youth_sources)
        expired = (
            sum(
                date.fromisoformat(row["end_date"]) < reference_day
                for row in all_source_rows
            )
            + sum(
                date.fromisoformat(
                    _validate_youth_detail(
                        source,
                        detail_results[(source["source_kind"], source["identity"])],
                    )["end_date"]
                )
                < reference_day
                for source in youth_sources
            )
        )
        meta.update(
            {
                "pages": list_requests + len(detail_results),
                "required_list_requests": list_requests,
                "source_total": source_rows,
                "source_rows": source_rows,
                "current_count": len(returned) + facility_count,
                "expired_count": expired,
                "returned_count": len(returned),
                "excluded_facility_count": facility_count,
                "application_control_count": sum(
                    bool(
                        row.get("raw_fields", {}).get(
                            "application_control_verified"
                        )
                    )
                    for row in returned
                ),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in returned)
                ),
                "source_kind_counts": dict(
                    Counter(
                        _clean(row.get("raw_fields", {}).get("source_kind"))
                        for row in returned
                    )
                ),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in returned)
                ),
                "municipality_counts": {
                    UIJEONGBU_MUNICIPALITY_CODE: len(returned)
                },
                "pagination_detected": True,
                "pagination_complete": True,
                "partitions_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "no_current_data": not returned,
                "no_current_reason": (
                    "all complete source records are expired or excluded non-education"
                    if not returned
                    else ""
                ),
            }
        )
        return returned, UIJEONGBU_PARSER, meta
    except Exception as exc:
        if not errors or _clean(exc) not in errors:
            errors.append(_clean(exc) or type(exc).__name__)
        meta.update(
            {
                "pages": list_requests + int(meta.get("detail_pages") or 0),
                "required_list_requests": list_requests,
                "source_rows": 0,
                "returned_count": 0,
                "detail_errors": max(
                    int(meta.get("detail_errors") or 0), len(errors)
                ),
                "pagination_complete": False,
                "partitions_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "configured_collection_error": "; ".join(errors[:12]),
            }
        )
        return [], UIJEONGBU_PARSER, meta
    finally:
        _close_quietly(main_session)


__all__ = [
    "UIJEONGBU_PROVIDER",
    "UIJEONGBU_CANONICAL_CANDIDATE_ID",
    "UIJEONGBU_CANONICAL_URL",
    "UIJEONGBU_LEGACY_CANDIDATE_ID",
    "UIJEONGBU_LEGACY_PROVIDER",
    "UIJEONGBU_LEGACY_URL",
    "UIJEONGBU_MUNICIPALITY_CODE",
    "UIJEONGBU_MUNICIPALITY_NAME",
    "UIJEONGBU_YOUTH_CATALOGUES",
    "UIJEONGBU_NON_EXECUTING_MIRRORS",
    "UIJEONGBU_OWNERSHIP_AUDIT",
    "UIJEONGBU_PARSER",
    "UijeongbuContractError",
    "collect_uijeongbu_education_courses",
    "is_uijeongbu_target",
]
