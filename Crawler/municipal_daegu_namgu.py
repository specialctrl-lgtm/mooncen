"""Atomic education collector for Daegu Nam-gu's official ledgers.

Daegu Nam-gu does not expose one combined reservation endpoint.  The district
owns three independently paged course ledgers: the lifelong-learning centre,
the Onmaeul I-Mom Centre's personal education programmes, and the Daedeok
Culture Centre academy.  This collector exhausts all three, verifies each
source's immediate empty or clamped sentinel and stable first/final boundary,
and then reads every current/future public detail page.

Applicant lists, registration forms, instructor/contact fields, attachments,
images, and free-form detail bodies are deliberately neither fetched nor
stored.  Experience/facility reservations and information-only schedules are
outside this provider's ownership boundary.
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
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


DAEGU_NAMGU_PROVIDER = "MUNI_NAM_DAEGU_KR_1E00F39A"
DAEGU_NAMGU_CANDIDATE_ID = "MUNI_IR_BF207B5BA4BA"
DAEGU_NAMGU_MUNICIPALITY_CODE = "2720000000"
DAEGU_NAMGU_MUNICIPALITY_NAME = "대구광역시 남구"

DAEGU_NAMGU_HOST = "nam.daegu.kr"
DAEGU_NAMGU_URL = "https://nam.daegu.kr/lll/edusat/list.do"
DAEGU_NAMGU_IMOM_URL = (
    "https://nam.daegu.kr/imom/main/site/edusat/edusat.do"
)
DAEGU_NAMGU_CULTURE_URL = (
    "https://nam.daegu.kr/culturalcenter/main/site/edusatRequest/edusat.do"
)

DAEGU_NAMGU_PARSER = (
    "daegu_namgu_three_official_education_ledgers+complete_pages+"
    "empty_or_exact_clamp_sentinels+stable_first_final_boundaries+"
    "current_future_details+identity_bound_application_controls+"
    "audited_test_record_suppression+pii_allowlist+atomic_snapshot"
)
DAEGU_NAMGU_OWNERSHIP_SCOPE = (
    "namgu_lifelong_imom_personal_education_and_daedeok_culture_academy"
)

DAEGU_NAMGU_PAGE_SIZE = 20
DAEGU_NAMGU_IMOM_PAGE_SIZE = 12
DAEGU_NAMGU_CULTURE_PAGE_SIZE = 20
DAEGU_NAMGU_FETCH_ATTEMPTS = 5
DAEGU_NAMGU_MAX_WORKERS = 6
DAEGU_NAMGU_MAX_HTML_BYTES = 8_000_000


@dataclass(frozen=True)
class EducationLedger:
    key: str
    url: str
    page_size: int
    branch: str
    branch_code: str
    shape: str


DAEGU_NAMGU_LEDGERS = (
    EducationLedger(
        "lifelong",
        DAEGU_NAMGU_URL,
        DAEGU_NAMGU_PAGE_SIZE,
        "대구 남구 평생학습관",
        "DAEGU_NAMGU_LIFELONG",
        "lifelong_table",
    ),
    EducationLedger(
        "imom",
        DAEGU_NAMGU_IMOM_URL,
        DAEGU_NAMGU_IMOM_PAGE_SIZE,
        "온마을 아이맘센터",
        "DAEGU_NAMGU_IMOM",
        "imom_cards",
    ),
    EducationLedger(
        "culture",
        DAEGU_NAMGU_CULTURE_URL,
        DAEGU_NAMGU_CULTURE_PAGE_SIZE,
        "대덕문화전당",
        "DAEGU_NAMGU_DAEDEOK",
        "culture_cards",
    ),
)
_LEDGER_BY_KEY = {ledger.key: ledger for ledger in DAEGU_NAMGU_LEDGERS}

DAEGU_NAMGU_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    DAEGU_NAMGU_CANDIDATE_ID: {
        "decision": "canonical_complete_owner_with_fixed_official_fanout",
        "url": DAEGU_NAMGU_URL,
        "fanout_urls": (DAEGU_NAMGU_IMOM_URL, DAEGU_NAMGU_CULTURE_URL),
    },
    "MUNI_IR_8AA90E3D7981": {
        "decision": "retarget_district_home_to_canonical_course_ledger",
        "url": "https://nam.daegu.kr/",
    },
    "MUNI_IR_3E765A40A9CE": {
        "decision": "exclude_separate_cultural_association_shell",
        "url": "https://namgucc.or.kr/~work2",
    },
    "MUNI_IR_F7C80F75A8A5": {
        "decision": "exclude_citywide_experience_owner_and_wrong_attribution",
        "url": "https://yeyak.daegu.go.kr/camp/detail/DSS_INST_00000103",
        "existing_owner": "MUNI_YEYAK_DAEGU_GO_KR_2685A3EB",
    },
    "MUNI_IR_EA1C908D18D2": {
        "decision": "exclude_unscoped_daegu_discovery_shell",
        "url": "https://yeyak.daegu.go.kr/",
    },
}

DAEGU_NAMGU_EXCLUDED_SCOPE: Mapping[str, Mapping[str, str]] = {
    "imom_play_experience": {
        "url": "https://nam.daegu.kr/imom/main/site/facility_reserve/experience/page.do",
        "reason": "experience_and_facility_reservation",
    },
    "imom_book_cafe": {
        "url": "https://nam.daegu.kr/imom/main/site/facility_reserve/library/page.do",
        "reason": "facility_reservation",
    },
    "resident_council_programs": {
        "url": "https://nam.daegu.kr/index.do?menu_id=00000873",
        "reason": "information_only_without_application_period_or_control",
    },
    "information_education": {
        "url": "https://nam.daegu.kr/index.do?menu_id=00000870",
        "reason": "information_only_phone_schedule",
    },
    "youth_programs": {
        "url": "https://nam.daegu.kr/index.do?menu_id=00110367",
        "reason": "information_only_program_summary",
    },
    "cpr_external_owner": {
        "url": "https://www.dandicpr.co.kr/content/edusat/list.php",
        "reason": "external_citywide_owner",
    },
}

DAEGU_NAMGU_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-28",
    "source_rows": 543,
    "source_data_pages": 30,
    "current_future_details": 79,
    "suppressed_nonproduction_rows": 1,
    "returned_rows": 78,
    "status_counts": {"OPEN": 6, "CLOSED": 72},
    "reservation_available_count": 6,
    "network_requests_without_retries": 118,
    "sentinel_kinds": {
        "lifelong": "empty",
        "imom": "exact_final_page_clamp",
        "culture": "exact_final_page_clamp",
    },
    "historical_source_date_anomalies": {
        "invalid_application": 1,
        "reversed_education_year": 1,
    },
    "duplicate_rows": 0,
}


class DaeguNamguContractError(ValueError):
    """Raised when an audited Nam-gu source contract changes."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_SHORT_RANGE_RE = re.compile(
    r"^\s*(\d{2})\.(\d{2})\.(\d{2})\s*~\s*(\d{2})\.(\d{2})\.(\d{2})\s*$"
)
_FULL_DASH_RANGE_RE = re.compile(
    r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?:\s+\d{1,2}:\d{2})?\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})(?:\s+\d{1,2}:\d{2})?(?!\d)"
)
_FULL_KOREAN_RANGE_RE = re.compile(
    r"(?<!\d)(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일(?:\([^)]*\))?\s*~\s*"
    r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일(?:\([^)]*\))?(?!\d)"
)
_CAPACITY_RE = re.compile(r"(?<!\d)(\d[\d,]*)\s*/\s*(\d[\d,]*)\s*명?")
_AMOUNT_RE = re.compile(r"(?<!\d)(\d[\d,]*)\s*원")
_PHONE_RE = re.compile(r"(?<!\d)(?:0\d{1,2}[- ]?)?\d{3,4}[- ]\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_STATUS_MAP: Mapping[str, str] = {
    "신청": "OPEN",
    "신청중": "OPEN",
    "접수중": "OPEN",
    "수강신청": "OPEN",
    "신청준비": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "신청마감": "CLOSED",
    "접수마감": "CLOSED",
    "기간종료": "CLOSED",
}

# The official archive contains this single impossible historical application
# date.  Its education period is valid and expired; only this exact immutable
# anomaly is tolerated so a newly malformed current row still fails closed.
_AUDITED_HISTORICAL_INVALID_APPLICATION = {
    "337": "24.02.01 ~ 24.02.31",
}
_AUDITED_HISTORICAL_INVALID_EDUCATION = {
    "322": ("24.01.11 ~ 23.03.28", "24.01.11 ~ 24.03.28"),
}
_AUDITED_NONPRODUCTION_ROWS = {
    ("imom", "1622"): "테스트 게시판",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True) if node is not None else "")


def _text_without(node: Tag, selectors: Sequence[str]) -> str:
    clone = BeautifulSoup(str(node), "lxml")
    for selector in selectors:
        for child in clone.select(selector):
            child.extract()
    return _text(clone)


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _normalized_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
    ):
        return ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return (
        f"https://{parsed.hostname.rstrip('.').lower()}{path}"
        + (f"?{query}" if query else "")
    )


def is_daegu_namgu_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != DAEGU_NAMGU_PROVIDER:
        return False
    if _normalized_url(_target_value(target, "url")) != _normalized_url(
        DAEGU_NAMGU_URL
    ):
        return False
    supplied_candidate = _clean(_target_value(target, "candidate_id"))
    return not supplied_candidate or supplied_candidate == DAEGU_NAMGU_CANDIDATE_ID


is_target = is_daegu_namgu_education_target


def _positive_page(value: Any) -> int:
    if isinstance(value, bool):
        raise DaeguNamguContractError("invalid page")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise DaeguNamguContractError("invalid page") from exc
    if result < 1:
        raise DaeguNamguContractError("invalid page")
    return result


def daegu_namgu_list_url(ledger: EducationLedger | str, page: int = 1) -> str:
    item = _LEDGER_BY_KEY[ledger] if isinstance(ledger, str) else ledger
    current = _positive_page(page)
    if item.key == "lifelong":
        return item.url + "?" + urlencode(
            (
                ("v_page", current),
                ("v_search", ""),
                ("v_keyword", ""),
                ("sh_edu_gubun", ""),
                ("sh_edu_money_type", ""),
                ("sh_edu_target_type", ""),
                ("sh_sta_date", ""),
                ("sh_end_date", ""),
                ("sh_edu_area", ""),
            )
        )
    return item.url + "?" + urlencode((("v_page", current),))


def daegu_namgu_detail_url(
    ledger: EducationLedger | str, identity: Any
) -> str:
    item = _LEDGER_BY_KEY[ledger] if isinstance(ledger, str) else ledger
    value = _clean(identity)
    if not value.isdigit() or int(value) < 1:
        raise DaeguNamguContractError("invalid course identity")
    if item.key == "lifelong":
        return "https://nam.daegu.kr/lll/edusat/view.do?" + urlencode(
            (("edu_idx", value),)
        )
    return item.url + "?" + urlencode((("proc_type", "view"), ("edu_idx", value)))


def _link_identity(ledger: EducationLedger, value: Any, *, action: str) -> str:
    parsed = urlparse(urljoin(ledger.url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != DAEGU_NAMGU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
    ):
        return ""
    identities = query.get("edu_idx", [])
    if len(identities) != 1 or not identities[0].isdigit():
        return ""
    if ledger.key == "lifelong":
        expected_path = (
            "/lll/edusat/view.do" if action == "detail" else "/lll/edusat/regist.do"
        )
        allowed = {"edu_idx", "prepage"}
        if parsed.path != expected_path or not set(query).issubset(allowed):
            return ""
    else:
        if parsed.path != urlparse(ledger.url).path:
            return ""
        proc = query.get("proc_type", [])
        if action == "detail":
            if proc != ["view"]:
                return ""
            allowed = {"proc_type", "edu_idx", "prepage", "v_ct_idx2"}
        else:
            if len(proc) != 1 or proc[0] not in {
                "regist",
                "request",
                "write",
                "apply",
            }:
                return ""
            allowed = {"proc_type", "edu_idx", "prepage"}
        if not set(query).issubset(allowed):
            return ""
    return identities[0]


def _canonical_application_url(ledger: EducationLedger, value: Any) -> str:
    identity = _link_identity(ledger, value, action="application")
    if not identity:
        return ""
    parsed = urlparse(urljoin(ledger.url, _clean(value)))
    if ledger.key == "lifelong":
        return "https://nam.daegu.kr/lll/edusat/regist.do?" + urlencode(
            (("edu_idx", identity),)
        )
    action = parse_qs(parsed.query, keep_blank_values=True)["proc_type"][0]
    return ledger.url + "?" + urlencode(
        (("proc_type", action), ("edu_idx", identity))
    )


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _RequestBudget:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.count = 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.count >= self.maximum:
                raise DaeguNamguContractError(
                    f"max_requests cap {self.maximum} exhausted"
                )
            self.count += 1


def _response_soup(response: Any, requested_url: str) -> BeautifulSoup:
    if isinstance(response, BeautifulSoup):
        return response
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise DaeguNamguContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise DaeguNamguContractError("redirected source response")
    final_url = _clean(getattr(response, "url", "")) or requested_url
    if _normalized_url(final_url) != _normalized_url(requested_url):
        raise DaeguNamguContractError("source response URL changed scope")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise DaeguNamguContractError("empty HTML response")
    byte_count = (
        len(content)
        if isinstance(content, bytes)
        else len(str(content).encode("utf-8"))
    )
    if byte_count > DAEGU_NAMGU_MAX_HTML_BYTES:
        raise DaeguNamguContractError("source HTML exceeds safety limit")
    return BeautifulSoup(content, "lxml")


def _fetch_soup(
    session: Any,
    url: str,
    *,
    fetcher: Fetcher,
    timeout: int,
    attempts: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
) -> tuple[BeautifulSoup, int]:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        try:
            budget.take()
            return _response_soup(fetcher(session, url, timeout), url), attempt - 1
        except Exception as exc:
            errors.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
            if attempt < attempts:
                sleeper(min(0.75 * (2 ** (attempt - 1)), 6.0))
    raise DaeguNamguContractError("; ".join(errors))


@dataclass
class _ManyResult:
    values: dict[tuple[str, str], BeautifulSoup]
    errors: list[str]
    retries: int
    sessions: int


def _fetch_many(
    items: Sequence[tuple[tuple[str, str], str]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    attempts: int,
    max_workers: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
) -> _ManyResult:
    values: dict[tuple[str, str], BeautifulSoup] = {}
    errors: list[str] = []
    retries = 0
    sessions: list[Any] = []
    local = threading.local()
    lock = threading.Lock()

    def worker_session() -> Any:
        current = getattr(local, "session", None)
        if current is None:
            current = session_factory()
            local.session = current
            with lock:
                sessions.append(current)
        return current

    def one(item: tuple[tuple[str, str], str]) -> tuple[tuple[str, str], BeautifulSoup, int]:
        key, url = item
        soup, retry_count = _fetch_soup(
            worker_session(),
            url,
            fetcher=fetcher,
            timeout=timeout,
            attempts=attempts,
            sleeper=sleeper,
            budget=budget,
        )
        return key, soup, retry_count

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(one, item): item[0] for item in items}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    result_key, soup, retry_count = future.result()
                    values[result_key] = soup
                    retries += retry_count
                except Exception as exc:
                    errors.append(f"{key}: {type(exc).__name__}: {_clean(exc)}")
    finally:
        for session in sessions:
            _close_quietly(session)
    return _ManyResult(values, errors, retries, len(sessions))


def _date_from_parts(parts: Sequence[Any], label: str) -> date:
    try:
        return date(*(int(value) for value in parts))
    except (TypeError, ValueError) as exc:
        raise DaeguNamguContractError(f"invalid {label}") from exc


def _full_dash_range(value: Any, label: str) -> tuple[date, date]:
    match = _FULL_DASH_RANGE_RE.search(_clean(value))
    if not match:
        raise DaeguNamguContractError(f"invalid {label}")
    parts = match.groups()
    start = _date_from_parts(parts[:3], f"{label} start")
    end = _date_from_parts(parts[3:], f"{label} end")
    if end < start:
        raise DaeguNamguContractError(f"reversed {label}")
    return start, end


def _full_korean_range(value: Any, label: str) -> tuple[date, date]:
    match = _FULL_KOREAN_RANGE_RE.search(_clean(value))
    if not match:
        raise DaeguNamguContractError(f"invalid {label}")
    parts = match.groups()
    start = _date_from_parts(parts[:3], f"{label} start")
    end = _date_from_parts(parts[3:], f"{label} end")
    if end < start:
        raise DaeguNamguContractError(f"reversed {label}")
    return start, end


def _short_range(
    value: Any, *, identity: str, application: bool
) -> tuple[Optional[date], Optional[date], bool]:
    raw = _clean(value)
    match = _SHORT_RANGE_RE.fullmatch(raw)
    if not match:
        raise DaeguNamguContractError("invalid lifelong date range")
    parts = match.groups()
    try:
        start = date(2000 + int(parts[0]), int(parts[1]), int(parts[2]))
        end = date(2000 + int(parts[3]), int(parts[4]), int(parts[5]))
    except ValueError as exc:
        if application and _AUDITED_HISTORICAL_INVALID_APPLICATION.get(identity) == raw:
            return None, None, True
        raise DaeguNamguContractError("invalid lifelong calendar date") from exc
    if end < start:
        audited = _AUDITED_HISTORICAL_INVALID_EDUCATION.get(identity)
        if not application and audited and audited[0] == raw:
            corrected = _SHORT_RANGE_RE.fullmatch(audited[1])
            if corrected is None:  # pragma: no cover - immutable module constant
                raise DaeguNamguContractError("invalid audited education correction")
            fixed = corrected.groups()
            return (
                date(2000 + int(fixed[0]), int(fixed[1]), int(fixed[2])),
                date(2000 + int(fixed[3]), int(fixed[4]), int(fixed[5])),
                True,
            )
        raise DaeguNamguContractError("reversed lifelong date range")
    return start, end, False


def _capacity(value: Any, label: str) -> tuple[int, int]:
    match = _CAPACITY_RE.search(_clean(value))
    if not match:
        raise DaeguNamguContractError(f"invalid {label}")
    current, total = (int(part.replace(",", "")) for part in match.groups())
    if current < 0 or total < 1:
        raise DaeguNamguContractError(f"invalid {label}")
    return current, total


def _fee(value: Any) -> tuple[str, int]:
    raw = _clean(value)
    if not raw or "무료" in raw:
        return "무료", 0
    amounts = [int(value.replace(",", "")) for value in _AMOUNT_RE.findall(raw)]
    if not amounts:
        return raw, 0
    amount = amounts[-1]
    return ("무료" if amount == 0 else f"{amount:,}원"), amount


def _pairs(root: Tag, selector: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in root.select(selector):
        headings = node.find_all("dt", recursive=False)
        values = node.find_all("dd", recursive=False)
        if len(headings) != 1 or len(values) != 1:
            raise DaeguNamguContractError("definition-list shape changed")
        key = _text(headings[0])
        if not key or key in result:
            raise DaeguNamguContractError("duplicate or empty detail field")
        result[key] = _text(values[0])
    return result


def _pager_current(soup: BeautifulSoup) -> Optional[int]:
    pager = soup.select_one(".board_paginate")
    if pager is None:
        raise DaeguNamguContractError("missing pagination control")
    values = pager.select("strong")
    if not values:
        return None
    if len(values) != 1 or not _text(values[0]).isdigit():
        raise DaeguNamguContractError("ambiguous pagination current page")
    return int(_text(values[0]))


def _pager_numbers(soup: BeautifulSoup) -> set[int]:
    pager = soup.select_one(".board_paginate")
    if pager is None:
        raise DaeguNamguContractError("missing pagination control")
    values: set[int] = set()
    for node in pager.select("a[href], strong"):
        text = _text(node)
        if text.isdigit():
            values.add(int(text))
    return values


def _source_signature(rows: Sequence[Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            row["_identity"],
            row["title"],
            row["apply_start"],
            row["apply_end"],
            row["start_date"],
            row["end_date"],
            row["_source_status"],
            row["capacity_current"],
            row["capacity_total"],
        )
        for row in rows
    )


def _lifelong_form(soup: BeautifulSoup) -> None:
    forms = soup.select('form[name="frm_edu"]')
    if len(forms) != 1 or _clean(forms[0].get("method")).lower() != "get":
        raise DaeguNamguContractError("lifelong search form changed")
    if _clean(forms[0].get("action")) != "list.do":
        raise DaeguNamguContractError("lifelong form action changed")


def _lifelong_row(source_row: Tag, *, page: int) -> dict[str, Any]:
    cells = source_row.find_all(["th", "td"], recursive=False)
    if len(cells) != 7:
        raise DaeguNamguContractError("lifelong row column count changed")
    title_links = cells[0].select('a[href*="edu_idx="]')
    if len(title_links) != 1:
        raise DaeguNamguContractError("lifelong title link changed")
    identity = _link_identity(
        _LEDGER_BY_KEY["lifelong"], title_links[0].get("href"), action="detail"
    )
    title = _text(title_links[0])
    if not identity or not title:
        raise DaeguNamguContractError("lifelong identity or title changed")

    periods = cells[1].select("ul.tlist > li")
    if len(periods) != 2:
        raise DaeguNamguContractError("lifelong period cells changed")
    apply_text = _text(periods[0])
    education_text = _text(periods[1])
    if not apply_text.startswith("신청:") or not education_text.startswith("교육:"):
        raise DaeguNamguContractError("lifelong period labels changed")
    apply_raw = _clean(apply_text.partition(":")[2])
    education_raw = _clean(education_text.partition(":")[2])
    apply_start, apply_end, anomaly = _short_range(
        apply_raw, identity=identity, application=True
    )
    start, end, education_anomaly = _short_range(
        education_raw, identity=identity, application=False
    )
    if start is None or end is None:
        raise DaeguNamguContractError("invalid lifelong education period")

    source_status = _text(cells[6].select_one(".state"))
    if source_status not in _STATUS_MAP:
        raise DaeguNamguContractError(f"unknown lifelong status {source_status!r}")
    application_links = cells[6].select('a[href*="regist.do"]')
    application_url = ""
    if source_status == "신청":
        if len(application_links) != 1:
            raise DaeguNamguContractError("open lifelong application control changed")
        application_url = _canonical_application_url(
            _LEDGER_BY_KEY["lifelong"], application_links[0].get("href")
        )
        if _link_identity(
            _LEDGER_BY_KEY["lifelong"],
            application_links[0].get("href"),
            action="application",
        ) != identity:
            raise DaeguNamguContractError("lifelong application identity mismatch")
    elif application_links:
        raise DaeguNamguContractError("closed lifelong row has application control")

    methods = [_text(node) for node in cells[3].select("span.acc")]
    if not methods or any(not value for value in methods):
        raise DaeguNamguContractError("lifelong application methods changed")
    current, total = _capacity(_text(cells[4]), "lifelong capacity")
    return {
        "_ledger": "lifelong",
        "_identity": identity,
        "_list_page": page,
        "_source_status": source_status,
        "_apply_raw": apply_raw,
        "_historical_apply_anomaly": anomaly,
        "_historical_education_anomaly": education_anomaly,
        "_list_application_url": application_url,
        "_list_methods": tuple(methods),
        "title": title,
        "raw_url": daegu_namgu_detail_url("lifelong", identity),
        "apply_start": apply_start.isoformat() if apply_start else "",
        "apply_end": apply_end.isoformat() if apply_end else "",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "fee_raw": _text(cells[2]),
        "capacity_current": current,
        "capacity_total": total,
    }


def _parse_lifelong_page(
    soup: BeautifulSoup, *, page: int
) -> tuple[list[dict[str, Any]], bool]:
    title = _text(soup.title)
    if "대구남구 평생학습관" not in title or "강좌신청" not in title:
        raise DaeguNamguContractError("lifelong page identity changed")
    _lifelong_form(soup)
    tables = soup.select("table.edu_list_table")
    if len(tables) != 1:
        raise DaeguNamguContractError("lifelong list table changed")
    headers = [_text(node) for node in tables[0].select("thead th")]
    if headers != [
        "강좌명",
        "기간",
        "수강료",
        "접수방법",
        "신청/모집",
        "신청현황",
        "상태",
    ]:
        raise DaeguNamguContractError("lifelong list headers changed")
    body_rows = tables[0].select("tbody > tr")
    empty = [row for row in body_rows if "등록된 강좌가 없습니다." in _text(row)]
    course_rows = [row for row in body_rows if row.select_one('a[href*="edu_idx="]')]
    if empty:
        if len(empty) != 1 or course_rows or _pager_current(soup) is not None:
            raise DaeguNamguContractError("lifelong empty sentinel changed")
        return [], True
    if len(course_rows) != len(body_rows) or _pager_current(soup) != page:
        raise DaeguNamguContractError("lifelong data page boundary changed")
    if not course_rows or len(course_rows) > DAEGU_NAMGU_PAGE_SIZE:
        raise DaeguNamguContractError("lifelong page size changed")
    return [_lifelong_row(row, page=page) for row in course_rows], False


def _card_total(soup: BeautifulSoup, ledger: EducationLedger) -> int:
    selector = (
        ".list_filter .total strong.eng"
        if ledger.key == "imom"
        else ".count strong.eng"
    )
    values = soup.select(selector)
    if len(values) != 1 or not _text(values[0]).replace(",", "").isdigit():
        raise DaeguNamguContractError(f"{ledger.key} declared total changed")
    return int(_text(values[0]).replace(",", ""))


def _card_info(card: Tag, ledger: EducationLedger) -> dict[str, str]:
    selector = ".info_group > dl" if ledger.key == "imom" else ".info > dl"
    return _pairs(card, selector)


def _imom_row(card: Tag, *, page: int) -> dict[str, Any]:
    ledger = _LEDGER_BY_KEY["imom"]
    links = card.select(":scope > a[href]")
    if len(links) != 1:
        raise DaeguNamguContractError("I-Mom card link changed")
    identity = _link_identity(ledger, links[0].get("href"), action="detail")
    title = _text(card.select_one(".subject"))
    source_status = _text(card.select_one(".label_area .state"))
    if not identity or not title or source_status not in _STATUS_MAP:
        raise DaeguNamguContractError("I-Mom card identity/status changed")
    info = _card_info(card, ledger)
    if set(info) != {"신청기간", "운영기간", "수강대상", "모집인원", "신청유형"}:
        raise DaeguNamguContractError("I-Mom card fields changed")
    if info["신청유형"] != "홈페이지접수":
        raise DaeguNamguContractError("I-Mom application type changed")
    apply_start, apply_end = _full_dash_range(info["신청기간"], "I-Mom application period")
    start, end = _full_dash_range(info["운영기간"], "I-Mom operation period")
    current, total = _capacity(info["모집인원"], "I-Mom capacity")
    return {
        "_ledger": "imom",
        "_identity": identity,
        "_list_page": page,
        "_source_status": source_status,
        "title": title,
        "raw_url": daegu_namgu_detail_url("imom", identity),
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "schedule_raw": _clean(info["운영기간"].replace(
            f"{start.isoformat()} ~ {end.isoformat()}", ""
        )),
        "target": info["수강대상"],
        "capacity_current": current,
        "capacity_total": total,
    }


def _culture_row(card: Tag, *, page: int) -> dict[str, Any]:
    ledger = _LEDGER_BY_KEY["culture"]
    title_links = card.select(".cont > .tit > a[href]")
    detail_links = card.select(".btn_box > a.check[href]")
    if len(title_links) != 1 or len(detail_links) != 1:
        raise DaeguNamguContractError("culture card detail controls changed")
    identities = {
        _link_identity(ledger, node.get("href"), action="detail")
        for node in (title_links[0], detail_links[0])
    }
    if len(identities) != 1 or "" in identities:
        raise DaeguNamguContractError("culture card identity mismatch")
    identity = next(iter(identities))
    title = _text(title_links[0])
    status_nodes = card.select(".btn_box > a.btn:not(.check)")
    if len(status_nodes) != 1:
        raise DaeguNamguContractError("culture status control changed")
    source_status = _text(status_nodes[0])
    if source_status not in _STATUS_MAP:
        raise DaeguNamguContractError(f"unknown culture status {source_status!r}")
    status_node = status_nodes[0]
    status_classes = set(status_node.get("class") or ())
    status_href = _clean(status_node.get("href"))
    application_url = ""
    if _STATUS_MAP[source_status] == "OPEN":
        application_identity = _link_identity(
            ledger, status_href, action="application"
        )
        application_url = _canonical_application_url(ledger, status_href)
        if (
            "ing" not in status_classes
            or application_identity != identity
            or not application_url
        ):
            raise DaeguNamguContractError(
                "culture list application identity mismatch"
            )
    elif "ing" in status_classes or status_href != "#javascript:;":
        raise DaeguNamguContractError(
            "unavailable culture row has application control"
        )
    info = _card_info(card, ledger)
    allowed = {"신청기간", "강의기간", "강사명", "수강료", "모집인원"}
    required = {"신청기간", "강의기간", "수강료", "모집인원"}
    if not required.issubset(info) or not set(info).issubset(allowed):
        raise DaeguNamguContractError("culture card fields changed")
    apply_start, apply_end = _full_dash_range(info["신청기간"], "culture application period")
    start, end = _full_dash_range(info["강의기간"], "culture lecture period")
    current, total = _capacity(info["모집인원"], "culture capacity")
    return {
        "_ledger": "culture",
        "_identity": identity,
        "_list_page": page,
        "_source_status": source_status,
        "_list_application_url": application_url,
        "title": title,
        "raw_url": daegu_namgu_detail_url("culture", identity),
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "schedule_raw": _clean(info["강의기간"].replace(
            f"{start.isoformat()} ~ {end.isoformat()}", ""
        )),
        "fee_raw": info["수강료"],
        "capacity_current": current,
        "capacity_total": total,
    }


def _parse_card_page(
    soup: BeautifulSoup,
    ledger: EducationLedger,
    *,
    page: int,
    expected_total: Optional[int] = None,
    sentinel_signature: Optional[tuple[Any, ...]] = None,
) -> tuple[list[dict[str, Any]], int, str]:
    title = _text(soup.title)
    expected_title = "온마을아이맘센터" if ledger.key == "imom" else "대덕문화전당"
    if expected_title not in title:
        raise DaeguNamguContractError(f"{ledger.key} page identity changed")
    total = _card_total(soup, ledger)
    if expected_total is not None and total != expected_total:
        raise DaeguNamguContractError(f"{ledger.key} declared total drifted")
    last = max(1, math.ceil(total / ledger.page_size))
    selector = "ul.list_card > li" if ledger.key == "imom" else ".edu_list > ul > li"
    cards = soup.select(selector)
    parser = _imom_row if ledger.key == "imom" else _culture_row
    rows = [parser(card, page=min(page, last)) for card in cards]

    if sentinel_signature is not None:
        current = _pager_current(soup)
        if not rows:
            text = _text(soup)
            if current is not None or not any(
                marker in text
                for marker in ("등록된 프로그램이 없습니다", "등록된 강좌가 없습니다")
            ):
                raise DaeguNamguContractError(f"{ledger.key} empty sentinel changed")
            return [], total, "empty"
        if current is not None or _source_signature(rows) != sentinel_signature:
            raise DaeguNamguContractError(f"{ledger.key} clamp sentinel changed")
        if last not in _pager_numbers(soup):
            raise DaeguNamguContractError(f"{ledger.key} clamp pager changed")
        return rows, total, "exact_final_page_clamp"

    if _pager_current(soup) != page:
        raise DaeguNamguContractError(f"{ledger.key} current-page control changed")
    expected_count = min(ledger.page_size, max(0, total - ((page - 1) * ledger.page_size)))
    if len(rows) != expected_count:
        raise DaeguNamguContractError(
            f"{ledger.key} page {page} returned {len(rows)} of {expected_count}"
        )
    return rows, total, ""


def _lifelong_title_key(value: Any) -> str:
    result = _clean(value)
    for prefix in ("[일반교육과정]", "[자격증과정]"):
        if result.startswith(prefix):
            result = _clean(result[len(prefix) :])
    return result


def _table_fields(table: Tag) -> tuple[str, dict[str, str], set[str]]:
    title_cells = table.select("thead tr")
    if len(title_cells) != 1:
        raise DaeguNamguContractError("lifelong detail title row changed")
    headings = title_cells[0].find_all("th", recursive=False)
    values = title_cells[0].find_all("td", recursive=False)
    if len(headings) != 1 or _text(headings[0]) != "강좌명" or len(values) != 1:
        raise DaeguNamguContractError("lifelong detail title changed")
    title = _text(values[0])
    fields: dict[str, str] = {}
    seen: set[str] = set()
    for row in table.select("tbody > tr"):
        children = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(children):
            if children[index].name != "th" or index + 1 >= len(children) or children[index + 1].name != "td":
                raise DaeguNamguContractError("lifelong detail field layout changed")
            label = _text(children[index])
            if not label or label in seen:
                raise DaeguNamguContractError("duplicate lifelong detail field")
            seen.add(label)
            fields[label] = _text(children[index + 1])
            index += 2
    return title, fields, seen


def _base_row(
    listed: Mapping[str, Any],
    ledger: EducationLedger,
    *,
    status: str,
    application_url: str,
    application_method: str,
    fee: str,
    fee_amount: int,
    schedule: str,
    target: str,
    venue: str,
    category: str,
) -> dict[str, Any]:
    identity = listed["_identity"]
    available = bool(application_url)
    return {
        "provider": DAEGU_NAMGU_PROVIDER,
        "provider_course_id": f"{DAEGU_NAMGU_PROVIDER}:{ledger.key}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": listed["title"],
        "branch": ledger.branch,
        "branch_code": ledger.branch_code,
        "preserve_branch": True,
        "provider_organizer": ledger.branch,
        "category": category,
        "program_type": "강좌",
        "raw_url": listed["raw_url"],
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if available else "INFO_ONLY",
        "reservation_available": available,
        "application_method_raw": application_method,
        "status": status,
        "fee": fee,
        "fee_amount": fee_amount,
        "period": f"{listed['start_date']} ~ {listed['end_date']}",
        "start_date": listed["start_date"],
        "end_date": listed["end_date"],
        "apply_period": f"{listed['apply_start']} ~ {listed['apply_end']}",
        "apply_start": listed["apply_start"],
        "apply_end": listed["apply_end"],
        "schedule_raw": schedule,
        "target": target,
        "capacity": f"{listed['capacity_current']}/{listed['capacity_total']}",
        "capacity_current": listed["capacity_current"],
        "capacity_total": listed["capacity_total"],
        "venue_name": venue or ledger.branch,
        "description": listed["title"],
        "collection_category": "평생학습",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": DAEGU_NAMGU_PARSER,
        "municipality_code": DAEGU_NAMGU_MUNICIPALITY_CODE,
        "municipality_full_name": DAEGU_NAMGU_MUNICIPALITY_NAME,
        "raw_fields": {
            "source_ledger": ledger.key,
            "source_identity": identity,
            "list_page": listed["_list_page"],
            "source_status": listed["_source_status"],
            "application_control_present": available,
            "pii_fields_read": [],
        },
    }


def _parse_lifelong_detail(
    soup: BeautifulSoup, listed: Mapping[str, Any]
) -> dict[str, Any]:
    ledger = _LEDGER_BY_KEY["lifelong"]
    title = _text(soup.title)
    if "대구남구 평생학습관" not in title or "강좌신청" not in title:
        raise DaeguNamguContractError("lifelong detail identity changed")
    tables = soup.select("table.edu_view_table")
    if len(tables) != 1:
        raise DaeguNamguContractError("lifelong detail table changed")
    detail_title, fields, seen = _table_fields(tables[0])
    required = {
        "교육기간",
        "신청기간",
        "강 사 명",
        "수 강 료",
        "교육방법",
        "교육대상",
        "교육주기",
        "교육정원",
        "문의전화",
        "접수방법",
        "지 역",
        "교육장소",
        "URL",
        "상세내용",
    }
    if not required.issubset(seen) or not seen.issubset(required | {"강의계획서"}):
        raise DaeguNamguContractError("lifelong detail field boundary changed")
    if _lifelong_title_key(detail_title) != _lifelong_title_key(listed["title"]):
        raise DaeguNamguContractError("lifelong detail title mismatch")
    start, end = _full_korean_range(fields["교육기간"], "lifelong detail education period")
    apply_start, apply_end = _full_korean_range(fields["신청기간"], "lifelong detail application period")
    if (
        start.isoformat() != listed["start_date"]
        or end.isoformat() != listed["end_date"]
        or apply_start.isoformat() != listed["apply_start"]
        or apply_end.isoformat() != listed["apply_end"]
    ):
        raise DaeguNamguContractError("lifelong list/detail dates mismatch")
    current, total = _capacity(fields["교육정원"], "lifelong detail capacity")
    if (current, total) != (listed["capacity_current"], listed["capacity_total"]):
        raise DaeguNamguContractError("lifelong list/detail capacity mismatch")
    status = _STATUS_MAP[listed["_source_status"]]
    controls = [
        node
        for node in soup.select('a[href*="regist.do"]')
        if _text(node) in {"신청", "신청접수하기", "수강신청"}
    ]
    application_url = ""
    if status == "OPEN":
        if len(controls) != 1 or "인터넷" not in fields["접수방법"]:
            raise DaeguNamguContractError("lifelong detail application control changed")
        application_url = _canonical_application_url(ledger, controls[0].get("href"))
        if (
            _link_identity(ledger, controls[0].get("href"), action="application")
            != listed["_identity"]
            or application_url != listed["_list_application_url"]
        ):
            raise DaeguNamguContractError("lifelong detail application identity mismatch")
    elif controls:
        raise DaeguNamguContractError("closed lifelong detail has application control")
    fee, fee_amount = _fee(listed["fee_raw"])
    category = "자격증과정" if detail_title.startswith("[자격증과정]") else "일반교육과정"
    return _base_row(
        listed,
        ledger,
        status=status,
        application_url=application_url,
        application_method=fields["접수방법"],
        fee=fee,
        fee_amount=fee_amount,
        schedule=fields["교육주기"],
        target=fields["교육대상"],
        venue=fields["교육장소"],
        category=category,
    )


def _parse_imom_detail(
    soup: BeautifulSoup, listed: Mapping[str, Any]
) -> dict[str, Any]:
    ledger = _LEDGER_BY_KEY["imom"]
    if "온마을아이맘센터" not in _text(soup.title):
        raise DaeguNamguContractError("I-Mom detail identity changed")
    roots = soup.select(".edu_view")
    if len(roots) != 1:
        raise DaeguNamguContractError("I-Mom detail root changed")
    root = roots[0]
    title_nodes = root.select(":scope > .tit")
    if len(title_nodes) != 1:
        raise DaeguNamguContractError("I-Mom detail title changed")
    detail_title = _text_without(title_nodes[0], (".label_area",))
    source_status = _text(title_nodes[0].select_one(".label_area .state"))
    if detail_title != listed["title"] or source_status not in _STATUS_MAP:
        raise DaeguNamguContractError("I-Mom detail title/status mismatch")
    if _STATUS_MAP[source_status] != _STATUS_MAP[listed["_source_status"]]:
        raise DaeguNamguContractError("I-Mom list/detail status mismatch")
    fields = _pairs(root, ".info > .txtw > dl")
    allowed = {
        "운영기간",
        "운영시간",
        "신청기간",
        "신청방법",
        "수강대상",
        "모집인원",
        "장소",
        "참가비",
        "강사",
    }
    required = {
        "운영기간",
        "운영시간",
        "신청기간",
        "신청방법",
        "수강대상",
        "모집인원",
        "참가비",
    }
    if not required.issubset(fields) or not set(fields).issubset(allowed):
        raise DaeguNamguContractError("I-Mom detail field boundary changed")
    start, end = _full_dash_range(fields["운영기간"], "I-Mom detail operation period")
    apply_start, apply_end = _full_dash_range(fields["신청기간"], "I-Mom detail application period")
    if (
        start.isoformat() != listed["start_date"]
        or end.isoformat() != listed["end_date"]
        or apply_start.isoformat() != listed["apply_start"]
        or apply_end.isoformat() != listed["apply_end"]
    ):
        raise DaeguNamguContractError("I-Mom list/detail dates mismatch")
    current, total = _capacity(fields["모집인원"], "I-Mom detail capacity")
    if (current, total) != (listed["capacity_current"], listed["capacity_total"]):
        raise DaeguNamguContractError("I-Mom list/detail capacity mismatch")
    status = _STATUS_MAP[source_status]
    controls = [
        node
        for node in root.select(".btn_w a[href]")
        if _text(node) in {"신청", "신청하기", "수강신청"}
    ]
    application_url = ""
    if status == "OPEN":
        if len(controls) != 1 or "인터넷" not in fields["신청방법"]:
            raise DaeguNamguContractError("I-Mom application control changed")
        application_url = _canonical_application_url(ledger, controls[0].get("href"))
        if (
            _link_identity(ledger, controls[0].get("href"), action="application")
            != listed["_identity"]
        ):
            raise DaeguNamguContractError("I-Mom application identity mismatch")
    elif controls:
        raise DaeguNamguContractError("closed I-Mom detail has application control")
    fee, fee_amount = _fee(fields["참가비"])
    return _base_row(
        listed,
        ledger,
        status=status,
        application_url=application_url,
        application_method=fields["신청방법"],
        fee=fee,
        fee_amount=fee_amount,
        schedule=fields["운영시간"],
        target=fields["수강대상"],
        venue=(
            f"{ledger.branch} {fields['장소']}" if fields.get("장소") else ledger.branch
        ),
        category="아동·가족교육",
    )


def _parse_culture_detail(
    soup: BeautifulSoup, listed: Mapping[str, Any]
) -> dict[str, Any]:
    ledger = _LEDGER_BY_KEY["culture"]
    if "대덕문화전당" not in _text(soup.title):
        raise DaeguNamguContractError("culture detail identity changed")
    roots = soup.select(".edu_view_board")
    if len(roots) != 1:
        raise DaeguNamguContractError("culture detail root changed")
    root = roots[0]
    title_nodes = root.select(":scope > div > h4.tit")
    if len(title_nodes) != 1:
        raise DaeguNamguContractError("culture detail title changed")
    detail_title = _text_without(title_nodes[0], ("a.btn2",))
    status_nodes = title_nodes[0].select("a.btn2")
    if len(status_nodes) != 1 or detail_title != listed["title"]:
        raise DaeguNamguContractError("culture detail title/status changed")
    detail_status = _text(status_nodes[0])
    if detail_status not in _STATUS_MAP:
        raise DaeguNamguContractError("unknown culture detail status")
    if _STATUS_MAP[detail_status] != _STATUS_MAP[listed["_source_status"]]:
        raise DaeguNamguContractError("culture list/detail status mismatch")
    period_nodes = root.select(".instructor_time > .time")
    if len(period_nodes) != 1:
        raise DaeguNamguContractError("culture lecture-period field changed")
    start, end = _full_dash_range(_text(period_nodes[0]), "culture detail lecture period")
    fields = _pairs(root, ".title_view_box > dl")
    allowed = {"신청기간", "신청방법", "모집인원", "강의장소", "수강료", "문의전화"}
    required = {"신청기간", "신청방법", "모집인원", "강의장소", "수강료", "문의전화"}
    if set(fields) != required or not set(fields).issubset(allowed):
        raise DaeguNamguContractError("culture detail field boundary changed")
    apply_start, apply_end = _full_dash_range(fields["신청기간"], "culture detail application period")
    if (
        start.isoformat() != listed["start_date"]
        or end.isoformat() != listed["end_date"]
        or apply_start.isoformat() != listed["apply_start"]
        or apply_end.isoformat() != listed["apply_end"]
    ):
        raise DaeguNamguContractError("culture list/detail dates mismatch")
    current, total = _capacity(fields["모집인원"], "culture detail capacity")
    if (current, total) != (listed["capacity_current"], listed["capacity_total"]):
        raise DaeguNamguContractError("culture list/detail capacity mismatch")
    status = _STATUS_MAP[detail_status]
    controls = soup.select(".board_button > .btn_right > a.btn.point[href]")
    if any(_text(node) not in {"신청", "신청하기", "수강신청"} for node in controls):
        raise DaeguNamguContractError("culture application control changed")
    application_url = ""
    online = "인터넷" in fields["신청방법"]
    if status == "OPEN" and online:
        if len(controls) != 1:
            raise DaeguNamguContractError("culture application control changed")
        application_url = _canonical_application_url(ledger, controls[0].get("href"))
        if (
            _link_identity(ledger, controls[0].get("href"), action="application")
            != listed["_identity"]
            or not application_url
            or application_url != listed["_list_application_url"]
        ):
            raise DaeguNamguContractError("culture application identity mismatch")
    elif controls:
        raise DaeguNamguContractError("unavailable culture detail has application control")
    fee, fee_amount = _fee(fields["수강료"])
    period_text = _text(period_nodes[0])
    schedule = _clean(_FULL_DASH_RANGE_RE.sub("", period_text, count=1)).strip("() ")
    return _base_row(
        listed,
        ledger,
        status=status,
        application_url=application_url,
        application_method=fields["신청방법"],
        fee=fee,
        fee_amount=fee_amount,
        schedule=schedule or listed.get("schedule_raw", ""),
        target="대상 별도 안내",
        venue=fields["강의장소"],
        category="문화예술교육",
    )


_DETAIL_PARSERS = {
    "lifelong": _parse_lifelong_detail,
    "imom": _parse_imom_detail,
    "culture": _parse_culture_detail,
}


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value)[:10])


def _failed_meta(error: str = "") -> dict[str, Any]:
    return {
        "source_total": 0,
        "source_rows": 0,
        "source_publishable_rows": 0,
        "source_rows_by_ledger": {},
        "data_pages_by_ledger": {},
        "pages": 0,
        "sentinel_requests": 0,
        "sentinel_kinds": {},
        "stability_rechecks": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "current_count": 0,
        "expired_count": 0,
        "suppressed_nonproduction_rows": 0,
        "historical_invalid_application_dates": 0,
        "duplicate_source_rows": 0,
        "semantic_duplicate_rows": 0,
        "returned_count": 0,
        "status_counts": {},
        "reservation_available_count": 0,
        "network_requests": 0,
        "retry_count": 0,
        "worker_sessions": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "stable_recheck_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": error,
        "errors": [error] if error else [],
        "no_current_data": False,
        "no_current_reason": "",
        "parser": DAEGU_NAMGU_PARSER,
        "candidate_id": DAEGU_NAMGU_CANDIDATE_ID,
        "canonical_url": DAEGU_NAMGU_URL,
        "ownership_scope": DAEGU_NAMGU_OWNERSHIP_SCOPE,
        "excluded_scope": DAEGU_NAMGU_EXCLUDED_SCOPE,
        "pii_policy": (
            "public_structured_allowlist; applicant_lists/forms/instructors/"
            "contacts/attachments/images/free_text_not_fetched_or_stored"
        ),
    }


def collect_daegu_namgu_education(
    target: Any,
    timeout: int = 25,
    max_pages: int = 50,
    detail_limit: int = 150,
    *,
    today: Optional[date | datetime | str] = None,
    max_requests: int = 240,
    max_workers: int = DAEGU_NAMGU_MAX_WORKERS,
    fetch_attempts: int = DAEGU_NAMGU_FETCH_ATTEMPTS,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete Nam-gu education snapshot or fail atomically."""

    if not is_daegu_namgu_education_target(target):
        return [], DAEGU_NAMGU_PARSER, _failed_meta(
            "target does not match the canonical Daegu Nam-gu education owner"
        )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (timeout, max_pages, max_requests, max_workers, fetch_attempts)
    ) or isinstance(detail_limit, bool) or not isinstance(detail_limit, int) or detail_limit < 0:
        return [], DAEGU_NAMGU_PARSER, _failed_meta("invalid collection limits")

    factory = session_factory or _default_session_factory
    request = fetcher or _default_fetcher
    budget = _RequestBudget(max_requests)
    list_session: Any = None
    listed: list[dict[str, Any]] = []
    pages_by_ledger: dict[str, int] = {}
    rows_by_ledger: dict[str, int] = {}
    sentinel_kinds: dict[str, str] = {}
    boundary_signatures: dict[str, tuple[tuple[Any, ...], tuple[Any, ...]]] = {}
    list_requests = 0
    retries = 0
    stability_rechecks = 0

    try:
        reference_day = _today(today)
        list_session = factory()
        if list_session is None:
            raise DaeguNamguContractError("session factory returned no session")
        # The lifelong ledger has no declared total.  Exhaust it until the
        # immediate, explicitly marked empty page.
        lifelong = _LEDGER_BY_KEY["lifelong"]
        lifelong_pages: list[list[dict[str, Any]]] = []
        sentinel_seen = False
        for page in range(1, max_pages + 1):
            soup, retry_count = _fetch_soup(
                list_session,
                daegu_namgu_list_url(lifelong, page),
                fetcher=request,
                timeout=timeout,
                attempts=fetch_attempts,
                sleeper=sleeper,
                budget=budget,
            )
            retries += retry_count
            list_requests += 1
            rows, empty = _parse_lifelong_page(soup, page=page)
            if empty:
                if page != len(lifelong_pages) + 1 or page == 1:
                    raise DaeguNamguContractError("lifelong sentinel boundary changed")
                numbers = _pager_numbers(soup)
                if max(numbers or {0}) != page - 1:
                    raise DaeguNamguContractError("lifelong sentinel pager changed")
                sentinel_seen = True
                sentinel_kinds[lifelong.key] = "empty"
                break
            if lifelong_pages and len(lifelong_pages[-1]) < lifelong.page_size:
                raise DaeguNamguContractError("lifelong data followed a short final page")
            if len(rows) < lifelong.page_size and page == max_pages:
                raise DaeguNamguContractError("lifelong sentinel exceeds max_pages")
            lifelong_pages.append(rows)
        if not sentinel_seen:
            raise DaeguNamguContractError("lifelong pagination exceeded max_pages")
        pages_by_ledger[lifelong.key] = len(lifelong_pages)
        rows_by_ledger[lifelong.key] = sum(len(page) for page in lifelong_pages)
        listed.extend(row for page in lifelong_pages for row in page)
        boundary_signatures[lifelong.key] = (
            _source_signature(lifelong_pages[0]),
            _source_signature(lifelong_pages[-1]),
        )

        # Both card ledgers declare exact totals and clamp any page beyond the
        # final page back to that final page.  The repeated semantic signature
        # is therefore their sentinel contract.
        for ledger in DAEGU_NAMGU_LEDGERS[1:]:
            first_soup, retry_count = _fetch_soup(
                list_session,
                daegu_namgu_list_url(ledger, 1),
                fetcher=request,
                timeout=timeout,
                attempts=fetch_attempts,
                sleeper=sleeper,
                budget=budget,
            )
            retries += retry_count
            list_requests += 1
            first_rows, total, _ = _parse_card_page(first_soup, ledger, page=1)
            last_page = max(1, math.ceil(total / ledger.page_size))
            if last_page + 1 > max_pages:
                raise DaeguNamguContractError(
                    f"{ledger.key} sentinel page {last_page + 1} exceeds max_pages"
                )
            page_rows = [first_rows]
            for page in range(2, last_page + 1):
                soup, retry_count = _fetch_soup(
                    list_session,
                    daegu_namgu_list_url(ledger, page),
                    fetcher=request,
                    timeout=timeout,
                    attempts=fetch_attempts,
                    sleeper=sleeper,
                    budget=budget,
                )
                retries += retry_count
                list_requests += 1
                rows, _, _ = _parse_card_page(
                    soup, ledger, page=page, expected_total=total
                )
                page_rows.append(rows)
            if sum(len(rows) for rows in page_rows) != total:
                raise DaeguNamguContractError(f"{ledger.key} rows do not match total")
            final_signature = _source_signature(page_rows[-1])
            sentinel_soup, retry_count = _fetch_soup(
                list_session,
                daegu_namgu_list_url(ledger, last_page + 1),
                fetcher=request,
                timeout=timeout,
                attempts=fetch_attempts,
                sleeper=sleeper,
                budget=budget,
            )
            retries += retry_count
            list_requests += 1
            _, _, sentinel_kind = _parse_card_page(
                sentinel_soup,
                ledger,
                page=last_page + 1,
                expected_total=total,
                sentinel_signature=final_signature,
            )
            sentinel_kinds[ledger.key] = sentinel_kind
            pages_by_ledger[ledger.key] = last_page
            rows_by_ledger[ledger.key] = total
            listed.extend(row for page in page_rows for row in page)
            boundary_signatures[ledger.key] = (
                _source_signature(page_rows[0]),
                final_signature,
            )

        identities = [(row["_ledger"], row["_identity"]) for row in listed]
        duplicate_source_rows = len(identities) - len(set(identities))
        if duplicate_source_rows:
            raise DaeguNamguContractError("duplicate source identity")

        # Re-read both edges of every ledger after all data/sentinel pages.
        for ledger in DAEGU_NAMGU_LEDGERS:
            last_page = pages_by_ledger[ledger.key]
            expected_first, expected_final = boundary_signatures[ledger.key]
            for page, expected in ((1, expected_first), (last_page, expected_final)):
                soup, retry_count = _fetch_soup(
                    list_session,
                    daegu_namgu_list_url(ledger, page),
                    fetcher=request,
                    timeout=timeout,
                    attempts=fetch_attempts,
                    sleeper=sleeper,
                    budget=budget,
                )
                retries += retry_count
                list_requests += 1
                stability_rechecks += 1
                if ledger.key == "lifelong":
                    rows, empty = _parse_lifelong_page(soup, page=page)
                    if empty:
                        raise DaeguNamguContractError("lifelong boundary became empty")
                else:
                    rows, _, _ = _parse_card_page(
                        soup,
                        ledger,
                        page=page,
                        expected_total=rows_by_ledger[ledger.key],
                    )
                if _source_signature(rows) != expected:
                    raise DaeguNamguContractError(
                        f"{ledger.key} page {page} changed during stable recheck"
                    )

        historical_apply_anomalies = sum(
            bool(row.get("_historical_apply_anomaly")) for row in listed
        )
        historical_education_anomalies = sum(
            bool(row.get("_historical_education_anomaly")) for row in listed
        )
        for row in listed:
            if (
                row.get("_historical_apply_anomaly")
                or row.get("_historical_education_anomaly")
            ) and date.fromisoformat(row["end_date"]) >= reference_day:
                raise DaeguNamguContractError(
                    "audited historical date anomaly entered current scope"
                )
        current = [
            row
            for row in listed
            if date.fromisoformat(row["end_date"]) >= reference_day
        ]
        if len(current) > detail_limit:
            raise DaeguNamguContractError(
                f"current/future detail count {len(current)} exceeds detail_limit {detail_limit}"
            )

        details = _fetch_many(
            [
                ((row["_ledger"], row["_identity"]), row["raw_url"])
                for row in current
            ],
            fetcher=request,
            session_factory=factory,
            timeout=timeout,
            attempts=fetch_attempts,
            max_workers=max_workers,
            sleeper=sleeper,
            budget=budget,
        )
        retries += details.retries
        if details.errors or len(details.values) != len(current):
            raise DaeguNamguContractError(
                "detail snapshot incomplete: " + "; ".join(details.errors)
            )

        parsed_rows: list[dict[str, Any]] = []
        suppressed = 0
        for listed_row in current:
            key = (listed_row["_ledger"], listed_row["_identity"])
            parsed = _DETAIL_PARSERS[listed_row["_ledger"]](
                details.values[key], listed_row
            )
            audited_title = _AUDITED_NONPRODUCTION_ROWS.get(key)
            if audited_title and parsed["title"] == audited_title:
                suppressed += 1
                continue
            parsed_rows.append(parsed)

        semantic_keys = [
            (
                row["branch_code"],
                re.sub(r"[^0-9A-Za-z가-힣]+", "", row["title"]).lower(),
                row["start_date"],
                row["end_date"],
                _clean(row["venue_name"]),
            )
            for row in parsed_rows
        ]
        semantic_duplicates = len(semantic_keys) - len(set(semantic_keys))
        if semantic_duplicates:
            raise DaeguNamguContractError("semantic duplicate courses across ledgers")
        if dedupe_rows is not None:
            deduped = list(dedupe_rows(parsed_rows))
            if len(deduped) != len(parsed_rows):
                raise DaeguNamguContractError("downstream dedupe changed owned snapshot")
            parsed_rows = deduped
        serialized = repr(parsed_rows)
        if _EMAIL_RE.search(serialized) or _PHONE_RE.search(serialized):
            raise DaeguNamguContractError("PII-like contact escaped public allowlist")

        status_counts = dict(Counter(row["status"] for row in parsed_rows))
        meta = {
            **_failed_meta(),
            "source_total": len(listed),
            "source_rows": len(listed),
            "source_publishable_rows": len(listed) - suppressed,
            "source_rows_by_ledger": rows_by_ledger,
            "data_pages_by_ledger": pages_by_ledger,
            "pages": sum(pages_by_ledger.values()),
            "sentinel_requests": len(DAEGU_NAMGU_LEDGERS),
            "sentinel_kinds": sentinel_kinds,
            "stability_rechecks": stability_rechecks,
            "list_requests": list_requests,
            "detail_attempts": len(current),
            "detail_pages": len(details.values),
            "current_count": len(current),
            "expired_count": len(listed) - len(current),
            "suppressed_nonproduction_rows": suppressed,
            "historical_invalid_application_dates": historical_apply_anomalies,
            "historical_invalid_education_dates": historical_education_anomalies,
            "duplicate_source_rows": duplicate_source_rows,
            "semantic_duplicate_rows": semantic_duplicates,
            "returned_count": len(parsed_rows),
            "status_counts": status_counts,
            "reservation_available_count": sum(
                bool(row["reservation_available"]) for row in parsed_rows
            ),
            "network_requests": budget.count,
            "retry_count": retries,
            "worker_sessions": details.sessions,
            "pagination_detected": any(value > 1 for value in pages_by_ledger.values()),
            "pagination_complete": True,
            "details_complete": True,
            "stable_recheck_complete": True,
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "configured_collection_error": "",
            "errors": [],
            "no_current_data": not parsed_rows,
            "no_current_reason": (
                "all complete official ledgers have no current/future production courses"
                if not parsed_rows
                else ""
            ),
        }
        return parsed_rows, DAEGU_NAMGU_PARSER, meta
    except Exception as exc:
        error = f"{type(exc).__name__}: {_clean(exc)}"
        meta = {
            **_failed_meta(error),
            "source_rows": len(listed),
            "source_total": len(listed),
            "source_rows_by_ledger": rows_by_ledger,
            "data_pages_by_ledger": pages_by_ledger,
            "pages": sum(pages_by_ledger.values()),
            "sentinel_requests": len(sentinel_kinds),
            "sentinel_kinds": sentinel_kinds,
            "stability_rechecks": stability_rechecks,
            "list_requests": list_requests,
            "network_requests": budget.count,
            "retry_count": retries,
            "source_cap_reached": any(
                marker in error
                for marker in ("max_pages", "max_requests", "detail_limit", "cap", "exceeded")
            ),
        }
        return [], DAEGU_NAMGU_PARSER, meta
    finally:
        _close_quietly(list_session)


collect = collect_daegu_namgu_education


__all__ = [
    "DAEGU_NAMGU_PROVIDER",
    "DAEGU_NAMGU_CANDIDATE_ID",
    "DAEGU_NAMGU_MUNICIPALITY_CODE",
    "DAEGU_NAMGU_MUNICIPALITY_NAME",
    "DAEGU_NAMGU_URL",
    "DAEGU_NAMGU_IMOM_URL",
    "DAEGU_NAMGU_CULTURE_URL",
    "DAEGU_NAMGU_PARSER",
    "DAEGU_NAMGU_OWNERSHIP_SCOPE",
    "DAEGU_NAMGU_CANDIDATE_AUDIT",
    "DAEGU_NAMGU_EXCLUDED_SCOPE",
    "DAEGU_NAMGU_DISCOVERY_AUDIT",
    "DAEGU_NAMGU_LEDGERS",
    "DaeguNamguContractError",
    "is_daegu_namgu_education_target",
    "is_target",
    "daegu_namgu_list_url",
    "daegu_namgu_detail_url",
    "collect_daegu_namgu_education",
    "collect",
]
