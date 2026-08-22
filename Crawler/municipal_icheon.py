"""Fail-closed collectors for Icheon City's public education catalogues.

Icheon currently has four independent, structured course owners:

* the city education portal for departmental courses;
* the Icheon-branded GSEEK tenant for resident-centre/lifelong courses;
* the integrated municipal library catalogue; and
* the Icheon Cultural Foundation academy.

Each collector proves its own whole-list boundary, reads an exact empty
sentinel, rechecks the first/last list edge, and validates every current or
future public detail before publishing an atomic snapshot.  The youth-life
centre view is an exact filtered alias of the city education portal and is
not executed separately.  The former ``/reserve`` service is an archive,
while the application-history, login, file-download, and application-write
routes are explicitly forbidden.

The branded GSEEK rows are also present in the provincial ``GYEONGGI_GSEEK``
aggregate under co-sponsor ``G000009``.  Production integration must exclude
that partition from the provincial owner before enabling the dedicated
Icheon owner.  The already configured worker-welfare catalogue remains a
separate owner and is never merged here.

Free-form detail text is not retained.  API descriptions are retained only
when they contain no phone number, email address, or resident-registration
number.  Applicant data, staff contacts, attachments, and application
endpoints are never requested.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import html
import json
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


ICHEON_CITY_CODE = "4150000000"
ICHEON_CITY_NAME = "경기도 이천시"

ICHEON_CITY_PROVIDER = "MUNI_WWW_ICHEON_GO_KR_1B4316ED"
ICHEON_CITY_URL = "https://www.icheon.go.kr/edu/eduLctr/lecture/list.do?mid=0101000000"
ICHEON_CITY_MAIN_URL = "https://www.icheon.go.kr/edu/main.do"
ICHEON_CITY_VIEW_BASE = "https://www.icheon.go.kr/edu/eduLctr/lecture/view.do"
ICHEON_CITY_HOST = "www.icheon.go.kr"
ICHEON_CITY_PAGE_SIZE = 12

ICHEON_GSEEK_PROVIDER = "MUNI_ICHEON_GSEEK_KR_18B68AC1"
ICHEON_GSEEK_CANDIDATE_ID = "MUNI_IR_FB96DA9F85D7"
ICHEON_GSEEK_URL = "https://icheon.gseek.kr/user/course/offline/list"
ICHEON_GSEEK_API_URL = "https://icheon.gseek.kr/user/course/offline/list/search"
ICHEON_GSEEK_HOST = "icheon.gseek.kr"
ICHEON_GSEEK_PAGE_SIZE = 9
ICHEON_GSEEK_REGION_CODE = ICHEON_CITY_CODE
ICHEON_GSEEK_CO_SPONSOR_ID = "G000009"
ICHEON_GSEEK_PARENT_PROVIDER = "GYEONGGI_GSEEK"
ICHEON_GSEEK_PARENT_URL = "https://www.gseek.kr/user/course/offline/list"

ICHEON_LIBRARY_PROVIDER = "MUNI_WWW_ICHEONLIB_GO_KR_76E3CE6D"
ICHEON_LIBRARY_CANDIDATE_ID = "MUNI_IR_1227B4EA45D5"
ICHEON_LIBRARY_URL = "https://www.icheonlib.go.kr/education/list"
ICHEON_LIBRARY_DETAIL_BASE = "https://www.icheonlib.go.kr/education/detail"
ICHEON_LIBRARY_HOST = "www.icheonlib.go.kr"
ICHEON_LIBRARY_PAGE_SIZE = 10

ICHEON_ARTIC_PROVIDER = "MUNI_WWW_ARTIC_OR_KR_9B6E3C8E"
ICHEON_ARTIC_CANDIDATE_ID = "MUNI_IR_F711ABF92A5A"
ICHEON_ARTIC_URL = (
    "https://www.artic.or.kr/base/nrr/academy/artic/list?menuLevel=2&menuNo=13"
)
ICHEON_ARTIC_DETAIL_BASE = "https://www.artic.or.kr/base/nrr/academy/artic/read"
ICHEON_ARTIC_HOST = "www.artic.or.kr"
ICHEON_ARTIC_PAGE_SIZE = 10

ICHEON_WORKER_WELFARE_PROVIDER = "ICHEON_WORKER_WELFARE"
ICHEON_WORKER_WELFARE_URL = "https://www.icheon-hrd.or.kr/program/programInfoList.do"

ICHEON_CITY_PARSER = (
    "icheon_city_education_declared_pages+empty_sentinel+stable_edges+all_current_details"
)
ICHEON_GSEEK_PARSER = (
    "icheon_gseek_region_census+exact_sentinel+stable_edges+all_current_details"
)
ICHEON_LIBRARY_PARSER = (
    "icheon_library_declared_total+empty_sentinel+stable_edges+all_current_details"
)
ICHEON_ARTIC_PARSER = (
    "icheon_artic_declared_pages+empty_sentinel+stable_edges+all_current_details"
)

ICHEON_DEFAULT_MAX_PAGES = 450
ICHEON_DEFAULT_DETAIL_LIMIT = 500
ICHEON_DEFAULT_MAX_REQUESTS = 1_200

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Sleeper = Callable[[float], None]


class IcheonContractError(ValueError):
    """Raised when an official Icheon source no longer matches its contract."""


_SPACE_RE = re.compile(r"\s+")
_INTEGER_RE = re.compile(r"[1-9]\d*")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]?\s*(\d{1,2})\s*[.\-/]?\s*(\d{1,2})(?!\d)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_TEST_RE = re.compile(r"(?:^|[\s\[(])(?:test|sample|테스트|샘플)(?:$|[\s\])])", re.I)

_FORBIDDEN_PATH_PARTS = (
    "/apply/",
    "/app/",
    "/academyreservation/",
    "/file/",
    "/download",
    "/mylist/",
    "/login",
    "/auth",
    "/pwd.do",
)
_ALLOWED_HOSTS = frozenset(
    {
        ICHEON_CITY_HOST,
        ICHEON_GSEEK_HOST,
        ICHEON_LIBRARY_HOST,
        ICHEON_ARTIC_HOST,
    }
)

ICHEON_CITY_AUDITED_BRANCHES = frozenset(
    {
        "관고동 주민자치학습센터",
        "농업기술센터",
        "대월면 주민자치학습센터",
        "마장면 주민자치학습센터",
        "모가면 주민자치학습센터",
        "백사면 주민자치학습센터",
        "부발읍 주민자치학습센터",
        "설성면 주민자치학습센터",
        "시민교육지원과",
        "시민정보화 교육(시청 6층)",
        "신둔면 주민자치학습센터",
        "여성보육과(이천행복학교)",
        "여성회관",
        "율면 주민자치 학습센터",
        "이천시 농업정책과",
        "이천시 청소년생활문화센터",
        "이천시보건소",
        "이천시보건소 보건위생과",
        "장호원읍 주민자치학습센터",
        "중리동 주민자치학습센터",
        "증포동 주민자치학습센터",
        "창전동 주민자치학습센터",
        "첨단전략산업과",
        "테스트",
        "호법면 주민자치학습센터",
    }
)

ICHEON_GSEEK_AUDITED_BRANCHES = frozenset(
    {
        "관고동행정복지센터",
        "대월면행정복지센터",
        "마장면행정복지센터",
        "모가면행정복지센터",
        "백사면행정복지센터",
        "부발읍행정복지센터",
        "설성면행정복지센터",
        "신둔면행정복지센터",
        "율면행정복지센터",
        "이천시 시민교육지원과",
        "장호원행정복지센터",
        "중리동행정복지센터",
        "증포동행정복지센터",
        "창전동행정복지센터",
        "한국관광대",
        "호법면행정복지센터",
    }
)
ICHEON_GSEEK_AUDITED_REGIONS = frozenset(
    {
        "관고동",
        "대월면",
        "마장면",
        "모가면",
        "백사면",
        "부발읍",
        "설성면",
        "신둔면",
        "율면",
        "장호원읍",
        "중리동",
        "증포동",
        "창전동",
        "호법면",
    }
)

ICHEON_LIBRARY_BRANCHES: Mapping[str, str] = {
    "MA000000": "이천시립도서관",
    "BR000000": "이천시립청미도서관",
    "MC000000": "이천시립효양도서관",
    "MB000000": "이천시립어린이도서관",
    "NA000000": "이천시립마장도서관",
    "NB000000": "이천시립서희도서관",
}
ICHEON_LIBRARY_LIST_LABEL_TO_BRANCH: Mapping[str, str] = {
    "시립도서관": "이천시립도서관",
    "청미도서관": "이천시립청미도서관",
    "효양도서관": "이천시립효양도서관",
    "어린이도서관": "이천시립어린이도서관",
    "마장도서관": "이천시립마장도서관",
    "서희도서관": "이천시립서희도서관",
}

ICHEON_NON_EXECUTING_ALIASES: tuple[dict[str, str], ...] = (
    {
        "url": "https://www.icheon.go.kr/icylcc/prgrm/list.do?mid=0302000000",
        "reason": "exact_agency_51_subset_of_city_education_owner",
        "owner": ICHEON_CITY_PROVIDER,
    },
    {
        "url": "https://www.icheon.go.kr/edu/eduLctr/apply/pwd.do?mid=0101000000",
        "reason": "applicant_history_identity_lookup_not_catalogue",
        "owner": ICHEON_CITY_PROVIDER,
    },
    {
        "url": "https://archives.icheon.go.kr/data01/static/271/23/www.icheon.go.kr/reserve/",
        "reason": "historic_2021_integrated_reservation_archive",
        "owner": "",
    },
    {
        "url": "https://www.icheon.go.kr/youth/program/list.do?mid=0201000000",
        "reason": "mixed_youth_event_application_board_without_structured_course_period",
        "owner": "",
    },
    {
        "url": "https://www.icheon.go.kr/portal/anm/master/list.do?mid=0204080100",
        "reason": "mixed_general_recruitment_board_not_course_catalogue",
        "owner": "",
    },
    {
        "url": "https://www.icheon.go.kr/market/cpblImpv/index.do?mid=0301000000",
        "reason": "structured_market_training_catalogue_has_no_current_or_future_rows",
        "owner": "",
    },
)

ICHEON_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    "MUNI_IR_091C3D3C5E71": {
        "decision": "unsafe_application_history_alias_retarget",
        "provider": ICHEON_CITY_PROVIDER,
        "url": "https://www.icheon.go.kr/edu/eduLctr/apply/pwd.do?mid=0101000000",
        "owner": ICHEON_CITY_PROVIDER,
    },
    "MUNI_IR_DA9006E505B5": {
        "decision": "navigation_shell",
        "provider": "MUNI_WWW_ICHEON_GO_KR_D177E264",
        "url": "https://www.icheon.go.kr/edu/main.do",
        "owner": ICHEON_CITY_PROVIDER,
    },
    ICHEON_ARTIC_CANDIDATE_ID: {
        "decision": "independent_public_academy_owner",
        "provider": ICHEON_ARTIC_PROVIDER,
        "url": ICHEON_ARTIC_URL,
        "owner": ICHEON_ARTIC_PROVIDER,
    },
    ICHEON_GSEEK_CANDIDATE_ID: {
        "decision": "dedicated_partition_of_provincial_gseek_owner",
        "provider": ICHEON_GSEEK_PROVIDER,
        "url": ICHEON_GSEEK_URL,
        "owner": ICHEON_GSEEK_PROVIDER,
    },
    ICHEON_LIBRARY_CANDIDATE_ID: {
        "decision": "independent_public_library_owner",
        "provider": ICHEON_LIBRARY_PROVIDER,
        "url": ICHEON_LIBRARY_URL,
        "owner": ICHEON_LIBRARY_PROVIDER,
    },
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", html.unescape(str(value or "")).replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return "".join(char.lower() for char in _clean(value) if char.isalnum())


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _target_provider(target: Any) -> str:
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


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise IcheonContractError(f"{label} must be a positive integer") from exc
    if parsed < 1:
        raise IcheonContractError(f"{label} must be a positive integer")
    return parsed


def _date_tokens(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            result.append(date(*(int(part) for part in match.groups())))
        except ValueError:
            continue
    return result


def _date_range(value: Any) -> tuple[str, str, str]:
    values = _date_tokens(value)
    if not values:
        return "", "", ""
    start = values[0]
    end = values[1] if len(values) > 1 else values[0]
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _integer(value: Any) -> Optional[int]:
    raw = _clean(value).replace(",", "")
    match = re.search(r"\d+", raw)
    return int(match.group()) if match else None


def _money(value: Any) -> str:
    text = _clean(value)
    amount = _integer(text)
    if text in {"무료", "없음"} or amount == 0:
        return "무료"
    return f"{amount:,}원" if amount is not None else text


def _contains_pii(value: Any) -> bool:
    text = _clean(value)
    return bool(_PHONE_RE.search(text) or _EMAIL_RE.search(text) or _RESIDENT_ID_RE.search(text))


def _public_description(value: Any, fallback: Any) -> tuple[str, bool]:
    text = _clean(value)
    if text and not _contains_pii(text):
        return text, False
    return _clean(fallback), bool(text)


def _branch_code(prefix: str, value: Any) -> str:
    digest = hashlib.sha1(_clean(value).encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}_{digest}"


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _signature(rows: Iterable[Any]) -> str:
    payload = json.dumps(list(rows), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _exact_target(target: Any, provider: str, canonical_url: str) -> bool:
    if _target_provider(target) != provider or _target_url(target) != canonical_url:
        return False
    parsed = urlparse(_target_url(target))
    return bool(
        parsed.scheme == "https"
        and parsed.hostname in _ALLOWED_HOSTS
        and parsed.port is None
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def is_icheon_education_target(target: Any) -> bool:
    return any(
        _exact_target(target, provider, url)
        for provider, url in (
            (ICHEON_CITY_PROVIDER, ICHEON_CITY_URL),
            (ICHEON_GSEEK_PROVIDER, ICHEON_GSEEK_URL),
            (ICHEON_LIBRARY_PROVIDER, ICHEON_LIBRARY_URL),
            (ICHEON_ARTIC_PROVIDER, ICHEON_ARTIC_URL),
        )
    )


is_target = is_icheon_education_target


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return session


def _guard_url(url: str) -> None:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise IcheonContractError("request escaped the audited HTTPS hosts")
    if any(part in path for part in _FORBIDDEN_PATH_PARTS):
        raise IcheonContractError("application, identity, login, or attachment route is forbidden")


def _response_status(response: Any) -> int:
    try:
        return int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        return 0


def _validate_response(response: Any, expected_url: str) -> None:
    if _response_status(response) != 200:
        raise IcheonContractError(f"unexpected HTTP status {_response_status(response)}")
    if getattr(response, "history", None):
        raise IcheonContractError("redirected responses are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and final_url != expected_url:
        raise IcheonContractError("response URL escaped the exact requested route")


def _response_soup(response: Any, expected_url: str) -> BeautifulSoup:
    _validate_response(response, expected_url)
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise IcheonContractError("empty HTML response")
    return BeautifulSoup(content, "lxml")


class _Fetcher:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        timeout: int,
        max_requests: int,
        bootstrap_url: str = "",
        sleeper: Sleeper = time.sleep,
    ) -> None:
        self.session_factory = session_factory
        self.timeout = timeout
        self.max_requests = max_requests
        self.bootstrap_url = bootstrap_url
        self.sleeper = sleeper
        self.session: Any = None
        self.physical_requests = 0
        self.retry_count = 0
        self.sessions_created = 0
        self.request_log: list[tuple[str, str]] = []

    def close(self) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        self.session = None

    def _new_session(self) -> None:
        self.close()
        self.session = self.session_factory()
        self.sessions_created += 1
        headers = getattr(self.session, "headers", None)
        if headers is not None and hasattr(headers, "update"):
            headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                }
            )
        if self.bootstrap_url:
            self._raw_get(self.bootstrap_url)

    def _budget(self) -> None:
        if self.physical_requests >= self.max_requests:
            raise IcheonContractError(f"max_requests cap {self.max_requests} exhausted")

    def _raw_get(self, url: str) -> Any:
        _guard_url(url)
        self._budget()
        self.physical_requests += 1
        self.request_log.append(("GET", url))
        response = self.session.get(url, timeout=self.timeout, allow_redirects=False)
        _validate_response(response, url)
        return response

    def _attempt(self, operation: Callable[[], Any]) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                if self.session is None:
                    self._new_session()
                return operation()
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    self.retry_count += 1
                    self._new_session()
                    self.sleeper(0.05)
        assert last_error is not None
        raise last_error

    def get_soup(self, url: str) -> BeautifulSoup:
        _guard_url(url)
        return self._attempt(lambda: _response_soup(self._raw_get(url), url))

    def post_json(self, url: str, data: Mapping[str, str], referer: str) -> list[Any]:
        if url != ICHEON_GSEEK_API_URL or referer != ICHEON_GSEEK_URL:
            raise IcheonContractError("POST is restricted to the audited GSEEK list-search endpoint")

        def operation() -> list[Any]:
            _guard_url(url)
            self._budget()
            self.physical_requests += 1
            self.request_log.append(("POST", url))
            response = self.session.post(
                url,
                data=dict(data),
                timeout=self.timeout,
                allow_redirects=False,
                headers={"Referer": referer, "X-Requested-With": "XMLHttpRequest"},
            )
            _validate_response(response, url)
            payload = response.json()
            if not isinstance(payload, list):
                raise IcheonContractError("GSEEK list-search response is not a JSON list")
            return payload

        return self._attempt(operation)


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "physical_requests": 0,
        "retry_count": 0,
        "sessions_created": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "discovered_links": 0,
        "current_count": 0,
        "returned_count": 0,
        "sentinel_count": -1,
        "stability_rechecks": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "application_endpoints_called": 0,
        "configured_collection_error": error,
    }


def _prepare(
    target: Any,
    provider: str,
    url: str,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    max_requests: int,
    session_factory: Optional[SessionFactory],
    allow_raw_requests_for_tests: bool,
) -> tuple[Optional[SessionFactory], str, tuple[int, int, int, int]]:
    if not _exact_target(target, provider, url):
        return None, "target does not match the exact canonical owner route", (0, 0, 0, 0)
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return None, "managed session_factory injection is required", (0, 0, 0, 0)
        session_factory = _default_session_factory
    try:
        values = (
            _positive_int(timeout, "timeout"),
            _positive_int(max_pages, "max_pages"),
            _positive_int(detail_limit, "detail_limit"),
            _positive_int(max_requests, "max_requests"),
        )
    except Exception as exc:
        return None, _clean(exc), (0, 0, 0, 0)
    return session_factory, "", values


_CITY_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수중(예비인원)": "OPEN",
    "추가접수중": "OPEN",
    "방문접수": "OPEN",
    "접수마감": "CLOSED",
    "교육중": "IN_PROGRESS",
    "교육완료": "CLOSED",
}
_CITY_OPEN_STATUSES = frozenset(
    {"접수중", "접수중(예비인원)", "추가접수중", "방문접수"}
)


def _city_page_url(page: int) -> str:
    return f"{ICHEON_CITY_URL}&mode=list&page={page}"


def _city_detail_url(identity: Any) -> str:
    raw = _clean(identity)
    if not _INTEGER_RE.fullmatch(raw):
        return ""
    return ICHEON_CITY_VIEW_BASE + "?" + urlencode(
        {"mid": "0101000000", "idx": raw}
    )


def _city_declared_pages(soup: BeautifulSoup) -> int:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "프로그램현황 목록" not in title:
        return 0
    values: set[int] = set()
    for node in soup.select(".bod_page .btn_end[onclick]"):
        match = re.search(r"goPage\((\d+)\)", _clean(node.get("onclick")))
        if match:
            values.add(int(match.group(1)))
    if len(values) == 1:
        return values.pop()
    current = soup.select_one(".bod_page span")
    return 1 if current and _clean(current.get_text(" ", strip=True)) == "1" else 0


def _city_declared_branch_contract(soup: BeautifulSoup) -> tuple[set[str], set[str]]:
    form = soup.select_one("form#listForm")
    if form is None:
        return set(), set()
    agencies = {
        _clean(node.get_text(" ", strip=True))
        for node in form.select("a[data-agency-idx]")
        if _clean(node.get("data-agency-idx")) and _clean(node.get_text(" ", strip=True))
    }
    districts: set[str] = set()
    for node in form.select("input[name='searchAgency'][data-agency-idx]"):
        if not _clean(node.get("data-agency-idx")):
            continue
        label = form.select_one(f"label[for='{_clean(node.get('id'))}']")
        if label:
            districts.add(_clean(label.get_text(" ", strip=True)))
    return agencies, districts


def _city_list_rows(soup: BeautifulSoup) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for tr in soup.select("table tbody tr"):
        cells = tr.select("td")
        link = tr.select_one("a[data-req-get-p-idx]")
        if link is None:
            continue
        if len(cells) != 9:
            errors.append("city list row does not have exactly nine cells")
            continue
        identity = _clean(link.get("data-req-get-p-idx"))
        title = _clean(link.get_text(" ", strip=True))
        title_cell_tokens = [_clean(token) for token in cells[1].stripped_strings]
        branch_tokens = [token for token in title_cell_tokens if token != title]
        branch = branch_tokens[-1] if branch_tokens else ""
        apply_start, apply_end, apply_period = _date_range(cells[2].get_text(" ", strip=True))
        start, end, period = _date_range(cells[3].get_text(" ", strip=True))
        source_status = _clean(cells[7].get_text(" ", strip=True))
        capacity_text = _clean(cells[8].get_text(" ", strip=True))
        capacity_values = [int(value) for value in re.findall(r"\d+", capacity_text)]
        test_row = bool(_TEST_RE.search(title)) or branch == "테스트"

        if not _INTEGER_RE.fullmatch(identity):
            errors.append("city list row has a non-numeric identity")
        if not title:
            errors.append(f"city course {identity}: empty title")
        if branch not in ICHEON_CITY_AUDITED_BRANCHES:
            errors.append(f"city course {identity}: unaudited branch {branch!r}")
        if source_status not in _CITY_STATUS_MAP:
            errors.append(f"city course {identity}: unknown status {source_status!r}")
        if not period:
            errors.append(f"city course {identity}: invalid education period")
        raw_url = _city_detail_url(identity)
        reservation_available = source_status in _CITY_OPEN_STATUSES
        row = {
            "provider": ICHEON_CITY_PROVIDER,
            "provider_course_id": f"{ICHEON_CITY_PROVIDER}:lecture:{identity}",
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "branch": branch,
            "branch_code": _branch_code("ICHEON_CITY_BRANCH", branch),
            "provider_organizer": branch,
            "category": "교육/강좌",
            "raw_url": raw_url,
            "application_url": raw_url if reservation_available else "",
            "reservation_available": reservation_available,
            "status": _CITY_STATUS_MAP.get(source_status, ""),
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "period": period,
            "start_date": start,
            "end_date": end,
            "schedule_raw": _clean(cells[4].get_text(" ", strip=True)),
            "venue_name": _clean(cells[5].get_text(" ", strip=True)),
            "room": _clean(cells[5].get_text(" ", strip=True)),
            "fee": _money(cells[6].get_text(" ", strip=True)),
            "capacity": capacity_text,
            "capacity_current": capacity_values[0] if capacity_values else None,
            "capacity_total": capacity_values[1] if len(capacity_values) > 1 else None,
            "description": title,
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": "static_html+detail_html",
            "program_type": "강좌",
            "region": ICHEON_CITY_NAME,
            "municipality_code": ICHEON_CITY_CODE,
            "municipality_full_name": ICHEON_CITY_NAME,
            "raw_fields": {
                "parser": ICHEON_CITY_PARSER,
                "lecture_id": identity,
                "source_status": source_status,
                "source_branch": branch,
                "source_test_row": test_row,
            },
        }
        rows.append(_clean_row(row))
    return rows, errors


def _definition_pairs(container: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if container is None:
        return pairs
    for dl in container.select("dl"):
        pending = ""
        for node in dl.find_all(["dt", "dd"], recursive=False):
            if node.name == "dt":
                pending = _clean(node.get_text(" ", strip=True)).rstrip(":")
            elif pending:
                value = _clean(node.get_text(" ", strip=True))
                if pending in pairs and pairs[pending] != value:
                    raise IcheonContractError(f"conflicting duplicate detail label {pending}")
                pairs[pending] = value
                pending = ""
    return pairs


def _city_detail_contract(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    raw = row.get("raw_fields") or {}
    identity = _clean(raw.get("lecture_id"))
    container = soup.select_one("div.bod_write")
    if container is None:
        return [f"city course {identity}: missing detail container"]
    hidden = soup.select_one("input[name='idx']")
    if hidden is None or _clean(hidden.get("value")) != identity:
        errors.append(f"city course {identity}: detail identity mismatch")
    try:
        pairs = _definition_pairs(container)
    except IcheonContractError as exc:
        return [f"city course {identity}: {_clean(exc)}"]

    if _normalized(pairs.get("강좌명")) != _normalized(row.get("title")):
        errors.append(f"city course {identity}: detail/list title mismatch")
    if _clean(pairs.get("교육기관")) != _clean(row.get("branch")):
        errors.append(f"city course {identity}: detail/list branch mismatch")
    _, _, detail_period = _date_range(pairs.get("교육기간"))
    if detail_period != _clean(row.get("period")):
        errors.append(f"city course {identity}: detail/list education period mismatch")
    _, _, detail_apply = _date_range(pairs.get("접수기간"))
    if detail_apply != _clean(row.get("apply_period")):
        errors.append(f"city course {identity}: detail/list application period mismatch")

    source_status = _clean(raw.get("source_status"))
    apply_controls = [
        node
        for node in soup.select("a.btn.point, button.btn.point")
        if "신청" in _clean(node.get_text(" ", strip=True))
    ]
    if source_status in _CITY_OPEN_STATUSES and not apply_controls:
        errors.append(f"city course {identity}: open row lacks public application control")
    if source_status not in _CITY_OPEN_STATUSES and apply_controls:
        errors.append(f"city course {identity}: non-open row exposes application control")
    for control in apply_controls:
        form_id = _clean(control.get("data-req-form-id"))
        form = soup.select_one(f"form#{form_id}") if form_id else None
        action = _clean(form.get("action")) if form is not None else ""
        if "/eduLctr/apply/write.do" not in action:
            errors.append(f"city course {identity}: unexpected application-control target")

    if pairs.get("교육시간"):
        row["schedule_raw"] = _clean(pairs.get("교육시간"))
    if pairs.get("교육대상"):
        row["target"] = _clean(pairs.get("교육대상"))
    if pairs.get("교육장소"):
        row["venue_name"] = _clean(pairs.get("교육장소"))
        row["room"] = _clean(pairs.get("교육장소"))
    if pairs.get("수강료"):
        row["fee"] = _money(pairs.get("수강료"))
    if pairs.get("재료비"):
        row["material_fee"] = _money(pairs.get("재료비"))
    description, redacted = _public_description(pairs.get("교육내용"), row.get("title"))
    row["description"] = description
    row.setdefault("raw_fields", {})["source_description_redacted"] = redacted
    row["raw_fields"]["detail_validated"] = not errors
    return errors


def collect_icheon_city_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = ICHEON_DEFAULT_MAX_PAGES,
    detail_limit: int = ICHEON_DEFAULT_DETAIL_LIMIT,
    *,
    max_requests: int = ICHEON_DEFAULT_MAX_REQUESTS,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect an atomic current/future city-education snapshot."""

    factory, error, values = _prepare(
        target,
        ICHEON_CITY_PROVIDER,
        ICHEON_CITY_URL,
        timeout,
        max_pages,
        detail_limit,
        max_requests,
        session_factory,
        allow_raw_requests_for_tests,
    )
    meta = _base_meta(error)
    if factory is None:
        return [], ICHEON_CITY_PARSER, meta
    timeout, max_pages, detail_limit, max_requests = values
    try:
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], ICHEON_CITY_PARSER, meta

    fetcher = _Fetcher(
        session_factory=factory,
        timeout=timeout,
        max_requests=max_requests,
        bootstrap_url=ICHEON_CITY_MAIN_URL,
        sleeper=sleeper,
    )
    errors: list[str] = []
    pages: dict[int, list[dict[str, Any]]] = {}
    declared_pages = 0
    sentinel_count = -1
    stability_rechecks = 0
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    rows: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []
    source_cap_reached = False
    declared_agencies: set[str] = set()
    declared_districts: set[str] = set()

    try:
        first_soup = fetcher.get_soup(_city_page_url(1))
        declared_pages = _city_declared_pages(first_soup)
        declared_agencies, declared_districts = _city_declared_branch_contract(first_soup)
        if declared_pages < 1:
            errors.append("city list lacks an unambiguous declared last page")
        if declared_pages + 1 > max_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {max_pages} of {declared_pages + 1} required pages"
            )
        expected_agencies = {
            "농업기술센터",
            "여성회관",
            "이천시보건소",
            "이천시 농업정책과",
            "시민정보화 교육(시청 6층)",
            "이천시보건소 보건위생과",
            "여성보육과(이천행복학교)",
            "이천시 청소년생활문화센터",
            "첨단전략산업과",
            "테스트",
        }
        expected_districts = {
            "장호원읍", "부발읍", "신둔면", "백사면", "마장면", "대월면", "모가면",
            "설성면", "호법면", "율면", "창전동", "증포동", "중리동", "관고동",
        }
        if declared_agencies != expected_agencies:
            errors.append("city official agency filter set changed")
        if declared_districts != expected_districts:
            errors.append("city official district filter set changed")

        if not errors:
            first_rows, page_errors = _city_list_rows(first_soup)
            pages[1] = first_rows
            errors.extend(page_errors)
            for page in range(2, declared_pages + 1):
                soup = fetcher.get_soup(_city_page_url(page))
                parsed, page_errors = _city_list_rows(soup)
                pages[page] = parsed
                errors.extend(page_errors)
            sentinel_soup = fetcher.get_soup(_city_page_url(declared_pages + 1))
            sentinel_rows, sentinel_errors = _city_list_rows(sentinel_soup)
            sentinel_count = len(sentinel_rows)
            errors.extend(sentinel_errors)
            if sentinel_rows:
                errors.append("city exact post-boundary sentinel is not empty")

        if not errors:
            for page, parsed in pages.items():
                expected = ICHEON_CITY_PAGE_SIZE if page < declared_pages else len(parsed)
                if page < declared_pages and len(parsed) != expected:
                    errors.append(f"city page {page}: expected {expected} rows, got {len(parsed)}")
                if page == declared_pages and not (1 <= len(parsed) <= ICHEON_CITY_PAGE_SIZE):
                    errors.append("city final page row count is outside the declared boundary")
                rows.extend(parsed)

        if not errors:
            for page in ([1] if declared_pages == 1 else [1, declared_pages]):
                repeated_soup = fetcher.get_soup(_city_page_url(page))
                repeated, repeated_errors = _city_list_rows(repeated_soup)
                errors.extend(repeated_errors)
                stability_rechecks += 1
                original_sig = _signature(
                    (r.get("provider_course_id"), r.get("title"), r.get("end_date"))
                    for r in pages[page]
                )
                repeated_sig = _signature(
                    (r.get("provider_course_id"), r.get("title"), r.get("end_date"))
                    for r in repeated
                )
                if original_sig != repeated_sig:
                    errors.append(f"city page {page}: stability signature changed")

        identities = [_clean(row.get("provider_course_id")) for row in rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"city source has {duplicate_count} duplicate identities")
        current_rows = [
            row
            for row in rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
            and not row.get("raw_fields", {}).get("source_test_row")
        ]
        if len(current_rows) > detail_limit:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {detail_limit} of {len(current_rows)} current details"
            )
        required_requests = 1 + declared_pages + 1 + (1 if declared_pages == 1 else 2) + len(current_rows)
        if required_requests > max_requests:
            source_cap_reached = True
            errors.append(
                f"max_requests cap allows {max_requests} of at least {required_requests} logical requests"
            )

        if not errors:
            for row in current_rows:
                detail_attempts += 1
                try:
                    detail_soup = fetcher.get_soup(_clean(row.get("raw_url")))
                    row_errors = _city_detail_contract(row, detail_soup)
                    if row_errors:
                        detail_errors += len(row_errors)
                        errors.extend(row_errors)
                    else:
                        detail_pages += 1
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: detail fetch {type(exc).__name__}"
                    )

        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in current_rows]))
            if len(result) != len(current_rows):
                errors.append("city dedupe changed the complete current snapshot")
                result = []

        snapshot_complete = not errors
        meta.update(
            {
                "pages": len(pages) + (1 if sentinel_count >= 0 else 0),
                "list_requests": len(pages) + (1 if sentinel_count >= 0 else 0) + stability_rechecks,
                "physical_requests": fetcher.physical_requests,
                "retry_count": fetcher.retry_count,
                "sessions_created": fetcher.sessions_created,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "detail_errors": detail_errors,
                "source_total": len(rows),
                "source_rows": len(rows),
                "declared_pages": declared_pages,
                "page_counts": {page: len(value) for page, value in pages.items()},
                "sentinel_page": declared_pages + 1 if declared_pages else 0,
                "sentinel_count": sentinel_count,
                "sentinel_kind": "exact_post_declared_page_empty",
                "stability_rechecks": stability_rechecks,
                "declared_agencies": sorted(declared_agencies),
                "declared_districts": sorted(declared_districts),
                "source_branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
                "test_row_count": sum(
                    bool(row.get("raw_fields", {}).get("source_test_row")) for row in rows
                ),
                "expired_count": sum(
                    date.fromisoformat(_clean(row.get("end_date"))) < cutoff for row in rows
                ),
                "current_count": len(current_rows),
                "returned_count": len(result),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
                "pagination_complete": bool(
                    snapshot_complete and len(pages) == declared_pages and sentinel_count == 0
                ),
                "details_complete": bool(
                    snapshot_complete
                    and detail_attempts == len(current_rows)
                    and detail_pages == len(current_rows)
                    and detail_errors == 0
                ),
                "snapshot_complete": snapshot_complete,
                "source_cap_reached": source_cap_reached,
                "no_current_data": snapshot_complete and not current_rows,
                "configured_collection_error": " | ".join(errors),
            }
        )
        if errors:
            result = []
    except Exception as exc:
        meta.update(
            {
                "physical_requests": fetcher.physical_requests,
                "retry_count": fetcher.retry_count,
                "sessions_created": fetcher.sessions_created,
                "source_cap_reached": source_cap_reached,
                "configured_collection_error": f"collector failure: {type(exc).__name__}: {_clean(exc)}",
            }
        )
        result = []
    finally:
        fetcher.close()
    return result, ICHEON_CITY_PARSER, meta


_GSEEK_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*개의\s*강좌")
_GSEEK_STATUS_MAP: Mapping[str, str] = {
    "모집중": "OPEN",
    "마감임박": "OPEN",
    "대기접수": "OPEN",
    "추가접수": "OPEN",
    "모집예정": "SCHEDULED",
    "마감": "CLOSED",
}
_GSEEK_OPEN_STATUSES = frozenset({"모집중", "마감임박", "대기접수", "추가접수"})


def _gseek_detail_url(subject: Any, cycle: Any) -> str:
    subject_text = _clean(subject)
    cycle_text = _clean(cycle)
    if not _INTEGER_RE.fullmatch(subject_text) or not _INTEGER_RE.fullmatch(cycle_text):
        return ""
    return "https://icheon.gseek.kr/user/course/offline/view?" + urlencode(
        {"s_sbjct_sn": subject_text, "s_sbjct_cycl_sn": cycle_text}
    )


def _gseek_range(page: int) -> tuple[int, int]:
    start = (page - 1) * ICHEON_GSEEK_PAGE_SIZE + 1
    return start, start + ICHEON_GSEEK_PAGE_SIZE


def _gseek_landing_total(soup: BeautifulSoup) -> int:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    values = {
        int(value.replace(",", ""))
        for value in _GSEEK_TOTAL_RE.findall(_clean(soup.get_text(" ", strip=True)))
    }
    region = soup.select_one("input#s_resion_cd1[name='s_resion_cd1']")
    sponsor = soup.select_one("input[name='ARK_CO_SPRVSN_ID']")
    if (
        title != "이천시 평생학습포털"
        or len(values) != 1
        or region is None
        or _clean(region.get("value")) != ICHEON_GSEEK_REGION_CODE
        or sponsor is None
        or _clean(sponsor.get("value")) != ICHEON_GSEEK_CO_SPONSOR_ID
        or "/user/course/offline/list/search" not in str(soup)
    ):
        return 0
    return values.pop()


def _source_date(value: Any) -> Optional[date]:
    values = _date_tokens(value)
    return values[0] if len(values) == 1 else None


def _gseek_api_row(item: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    subject = _clean(item.get("d_sbjct_sn"))
    cycle = _clean(item.get("d_sbjct_cycl_sn"))
    identity = f"{subject}:{cycle}"
    title = _clean(item.get("d_sbjct_nm"))
    branch = _clean(item.get("d_edu_gvmnfc"))
    region = _clean(item.get("d_rgn"))
    source_status = _clean(item.get("d_recrut_stts_nm"))
    start = _source_date(item.get("d_edu_bgng_dt"))
    end = _source_date(item.get("d_edu_end_dt"))
    test_row = bool(_TEST_RE.search(title))

    if not _INTEGER_RE.fullmatch(subject) or not _INTEGER_RE.fullmatch(cycle):
        errors.append("GSEEK row has a non-numeric subject/cycle identity")
    if not title:
        errors.append(f"GSEEK course {identity}: empty title")
    if branch not in ICHEON_GSEEK_AUDITED_BRANCHES:
        errors.append(f"GSEEK course {identity}: unaudited branch {branch!r}")
    if region not in ICHEON_GSEEK_AUDITED_REGIONS:
        errors.append(f"GSEEK course {identity}: non-Icheon region {region!r}")
    if _clean(item.get("d_co_sprvsn_id")) != ICHEON_GSEEK_CO_SPONSOR_ID:
        errors.append(f"GSEEK course {identity}: wrong co-sponsor")
    if _clean(item.get("d_sbjct_type_cd_id")) != "OF":
        errors.append(f"GSEEK course {identity}: not an offline course")
    if source_status not in _GSEEK_STATUS_MAP:
        errors.append(f"GSEEK course {identity}: unknown status {source_status!r}")
    if start is None or end is None or end < start:
        errors.append(f"GSEEK course {identity}: invalid education dates")
    single_day = _clean(item.get("d_is_single_day_course"))
    if single_day not in {"Y", "N"}:
        errors.append(f"GSEEK course {identity}: invalid single-day flag")
    elif start and end and ((start == end) != (single_day == "Y")):
        errors.append(f"GSEEK course {identity}: single-day flag/date mismatch")

    start_time = _clean(item.get("d_edu_start_time"))
    end_time = _clean(item.get("d_edu_end_time"))
    weekday = _clean(item.get("d_edu_wday_cd_nm"))
    schedule = " ".join(
        value
        for value in (
            f"매주 {weekday}" if weekday and single_day != "Y" else "",
            f"{start_time} ~ {end_time}" if start_time and end_time else start_time or end_time,
        )
        if value
    )
    category = " > ".join(
        value
        for value in (
            _clean(item.get("d_clsf_depth1_nm")),
            _clean(item.get("d_clsf_depth2_nm")),
            _clean(item.get("d_clsf_depth3_nm")),
        )
        if value
    )
    capacity_total = _integer(item.get("d_edu_nope"))
    capacity_current = _integer(item.get("d_aply_cnt"))
    description, redacted = _public_description(item.get("d_sbjct_intrd_cn"), title)
    raw_url = _gseek_detail_url(subject, cycle)
    period = f"{start.isoformat()} ~ {end.isoformat()}" if start and end else ""
    row = {
        "provider": ICHEON_GSEEK_PROVIDER,
        "provider_course_id": f"{ICHEON_GSEEK_PROVIDER}:course:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": branch,
        "branch_code": _branch_code("ICHEON_GSEEK_BRANCH", branch),
        "provider_organizer": branch,
        "category": category or "평생학습",
        "raw_url": raw_url,
        "status": _GSEEK_STATUS_MAP.get(source_status, ""),
        "period": period,
        "start_date": start.isoformat() if start else "",
        "end_date": end.isoformat() if end else "",
        "schedule_raw": schedule,
        "target": _clean(item.get("d_sbjct_trgt_nm_1")),
        "fee": _money(item.get("d_sbjct_amt")),
        "material_fee": _money(item.get("d_prepar_cmdty_amt")),
        "capacity": capacity_total,
        "capacity_total": capacity_total,
        "capacity_current": capacity_current,
        "description": description,
        "application_method_raw": _clean(item.get("d_stdnt_chice_mthd_cd_nm")),
        "reservation_available": False,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "json_api+detail_html",
        "program_type": "강좌",
        "region": ICHEON_CITY_NAME,
        "municipality_code": ICHEON_CITY_CODE,
        "municipality_full_name": ICHEON_CITY_NAME,
        "raw_fields": {
            "parser": ICHEON_GSEEK_PARSER,
            "subject_id": subject,
            "cycle_id": cycle,
            "source_status": source_status,
            "source_branch": branch,
            "source_region": region,
            "co_sponsor_id": _clean(item.get("d_co_sprvsn_id")),
            "single_day": single_day,
            "source_start_time": start_time,
            "source_end_time": end_time,
            "source_description_redacted": redacted,
            "source_test_row": test_row,
        },
    }
    return _clean_row(row), errors


def _gseek_public_pairs(container: Any) -> tuple[dict[str, str], list[str]]:
    allowed = {
        "신청기간", "일반신청기간", "우선신청기간", "추가신청기간", "학습기간",
        "학습일자", "교육시간", "교육대상", "모집인원", "수강료", "재료비", "교육장소",
    }
    pairs: dict[str, str] = {}
    conflicts: list[str] = []
    if container is None:
        return pairs, conflicts
    for dl in container.select("dl"):
        pending = ""
        for node in dl.find_all(["dt", "dd"], recursive=False):
            if node.name == "dt":
                pending = _clean(node.get_text(" ", strip=True)).replace(" ", "").rstrip(":")
            elif pending:
                value = _clean(node.get_text(" ", strip=True))
                if pending in allowed:
                    if pending in pairs and pairs[pending] != value:
                        conflicts.append(pending)
                    pairs[pending] = value
                pending = ""
    return pairs, conflicts


def _gseek_detail_contract(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    raw = row.get("raw_fields") or {}
    subject = _clean(raw.get("subject_id"))
    cycle = _clean(raw.get("cycle_id"))
    identity = f"{subject}:{cycle}"
    container = soup.select_one("div.course-detail-container")
    if container is None:
        return [f"GSEEK course {identity}: missing detail container"]

    subject_node = soup.select_one("input[name='s_sbjct_sn']")
    cycle_node = soup.select_one("input[name='s_sbjct_cycl_sn']")
    if (
        subject_node is None
        or cycle_node is None
        or _clean(subject_node.get("value")) != subject
        or _clean(cycle_node.get("value")) != cycle
    ):
        errors.append(f"GSEEK course {identity}: detail identity mismatch")
    title_node = container.select_one("h2.course-title")
    if title_node is None or _normalized(title_node.get_text(" ", strip=True)) != _normalized(row.get("title")):
        errors.append(f"GSEEK course {identity}: detail/list title mismatch")
    detail_branches = [
        _clean(node.get_text(" ", strip=True))
        for node in container.select("section.key-course-info span.tag-field")
    ]
    if detail_branches != [_clean(raw.get("source_branch"))]:
        errors.append(f"GSEEK course {identity}: detail/list branch mismatch")
    region_node = container.select_one("section.key-course-info span.tag-type.offline-type")
    if region_node is None or _clean(region_node.get_text(" ", strip=True)) != _clean(raw.get("source_region")):
        errors.append(f"GSEEK course {identity}: detail/list region mismatch")
    status_node = container.select_one("section.key-course-info .tag-item-xs")
    if status_node is None or _clean(status_node.get_text(" ", strip=True)) != _clean(raw.get("source_status")):
        errors.append(f"GSEEK course {identity}: detail/list status mismatch")

    pairs, conflicts = _gseek_public_pairs(container)
    if conflicts:
        errors.append(f"GSEEK course {identity}: conflicting public detail labels")
    period_label = "학습일자" if _clean(raw.get("single_day")) == "Y" else "학습기간"
    _, _, detail_period = _date_range(pairs.get(period_label))
    if detail_period != _clean(row.get("period")):
        errors.append(f"GSEEK course {identity}: detail/list education period mismatch")

    application_ranges: dict[str, tuple[str, str, str]] = {}
    for label in ("신청기간", "일반신청기간", "우선신청기간", "추가신청기간"):
        if label in pairs:
            parsed = _date_range(pairs[label])
            if not parsed[2]:
                errors.append(f"GSEEK course {identity}: malformed {label}")
            application_ranges[label] = parsed
    if not application_ranges:
        errors.append(f"GSEEK course {identity}: missing application period")
    canonical_label = next(
        (label for label in ("추가신청기간", "일반신청기간", "신청기간", "우선신청기간") if label in application_ranges),
        "",
    )
    apply_start, apply_end, apply_period = application_ranges.get(canonical_label, ("", "", ""))
    if apply_period:
        row["apply_start_date"] = apply_start
        row["apply_end_date"] = apply_end
        row["apply_period"] = apply_period
    if pairs.get("교육시간"):
        row["schedule_raw"] = _clean(pairs.get("교육시간"))
    if pairs.get("교육대상"):
        row["target"] = _clean(pairs.get("교육대상"))
    if pairs.get("수강료"):
        row["fee"] = _money(pairs.get("수강료"))
    if pairs.get("재료비"):
        row["material_fee"] = _money(pairs.get("재료비"))
    if pairs.get("교육장소"):
        row["venue_name"] = _clean(pairs.get("교육장소"))
        row["venue_address"] = _clean(pairs.get("교육장소"))

    source_status = _clean(raw.get("source_status"))
    callable_controls = [
        node
        for node in container.select("a.btn-course-apply, button.btn-course-apply")
        if "fnAply" in _clean(node.get("onclick"))
    ]
    if source_status in _GSEEK_OPEN_STATUSES:
        if len(callable_controls) != 1:
            errors.append(f"GSEEK course {identity}: open detail lacks one application control")
        else:
            row["reservation_available"] = True
            row["application_url"] = _clean(row.get("raw_url"))
    elif callable_controls:
        errors.append(f"GSEEK course {identity}: non-open detail exposes callable application control")
    row.setdefault("raw_fields", {})["detail_validated"] = not errors
    return errors


def collect_icheon_gseek_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = ICHEON_DEFAULT_MAX_PAGES,
    detail_limit: int = ICHEON_DEFAULT_DETAIL_LIMIT,
    *,
    max_requests: int = ICHEON_DEFAULT_MAX_REQUESTS,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete Icheon-branded GSEEK partition atomically."""

    factory, error, values = _prepare(
        target,
        ICHEON_GSEEK_PROVIDER,
        ICHEON_GSEEK_URL,
        timeout,
        max_pages,
        detail_limit,
        max_requests,
        session_factory,
        allow_raw_requests_for_tests,
    )
    meta = _base_meta(error)
    if factory is None:
        return [], ICHEON_GSEEK_PARSER, meta
    timeout, max_pages, detail_limit, max_requests = values
    try:
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], ICHEON_GSEEK_PARSER, meta

    fetcher = _Fetcher(
        session_factory=factory,
        timeout=timeout,
        max_requests=max_requests,
        sleeper=sleeper,
    )
    errors: list[str] = []
    payloads: dict[int, list[Any]] = {}
    rows: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []
    source_total = 0
    data_pages = 0
    sentinel_count = -1
    stability_rechecks = 0
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    source_cap_reached = False

    try:
        landing = fetcher.get_soup(ICHEON_GSEEK_URL)
        source_total = _gseek_landing_total(landing)
        if source_total < 1:
            errors.append("GSEEK landing lacks the exact Icheon catalogue contract")
        data_pages = math.ceil(source_total / ICHEON_GSEEK_PAGE_SIZE) if source_total else 0
        required_ranges = data_pages + 1
        if required_ranges > max_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {max_pages} of {required_ranges} required GSEEK ranges"
            )

        if not errors:
            for page in range(1, data_pages + 1):
                start, end = _gseek_range(page)
                payloads[page] = fetcher.post_json(
                    ICHEON_GSEEK_API_URL,
                    {
                        "s_sort_by": "1",
                        "s_row_start": str(start),
                        "s_row_end": str(end),
                        "resion": ICHEON_GSEEK_REGION_CODE,
                    },
                    ICHEON_GSEEK_URL,
                )
            sentinel_start = source_total + 1
            payloads[data_pages + 1] = fetcher.post_json(
                ICHEON_GSEEK_API_URL,
                {
                    "s_sort_by": "1",
                    "s_row_start": str(sentinel_start),
                    "s_row_end": str(sentinel_start + ICHEON_GSEEK_PAGE_SIZE),
                    "resion": ICHEON_GSEEK_REGION_CODE,
                },
                ICHEON_GSEEK_URL,
            )

        if not errors:
            for page in range(1, data_pages + 1):
                payload = payloads[page]
                expected = min(
                    ICHEON_GSEEK_PAGE_SIZE,
                    source_total - (page - 1) * ICHEON_GSEEK_PAGE_SIZE,
                )
                if len(payload) != expected:
                    errors.append(f"GSEEK range {page}: expected {expected}, got {len(payload)}")
                for item in payload:
                    if not isinstance(item, Mapping):
                        errors.append(f"GSEEK range {page}: non-object row")
                        continue
                    if _integer(item.get("d_total_cnt")) != source_total:
                        errors.append(f"GSEEK range {page}: declared total changed")
                    row, row_errors = _gseek_api_row(item)
                    rows.append(row)
                    errors.extend(row_errors)
            sentinel_count = len(payloads.get(data_pages + 1, []))
            if sentinel_count:
                errors.append("GSEEK exact post-total sentinel is not empty")
            if len(rows) != source_total:
                errors.append(f"GSEEK declared total {source_total} != parsed {len(rows)}")

        if not errors:
            edge_pages = [1] if data_pages == 1 else [1, data_pages]
            for page in edge_pages:
                start, end = _gseek_range(page)
                repeated = fetcher.post_json(
                    ICHEON_GSEEK_API_URL,
                    {
                        "s_sort_by": "1",
                        "s_row_start": str(start),
                        "s_row_end": str(end),
                        "resion": ICHEON_GSEEK_REGION_CODE,
                    },
                    ICHEON_GSEEK_URL,
                )
                stability_rechecks += 1
                if _signature(payloads[page]) != _signature(repeated):
                    errors.append(f"GSEEK range {page}: stability signature changed")

        identities = [_clean(row.get("provider_course_id")) for row in rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"GSEEK source has {duplicate_count} duplicate identities")
        current_rows = [
            row
            for row in rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
            and not row.get("raw_fields", {}).get("source_test_row")
        ]
        if len(current_rows) > detail_limit:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {detail_limit} of {len(current_rows)} current GSEEK details"
            )
        edge_count = 1 if data_pages == 1 else (2 if data_pages > 1 else 0)
        required_requests = 1 + data_pages + 1 + edge_count + len(current_rows)
        if required_requests > max_requests:
            source_cap_reached = True
            errors.append(
                f"max_requests cap allows {max_requests} of at least {required_requests} logical requests"
            )

        if not errors:
            for row in current_rows:
                detail_attempts += 1
                try:
                    detail_soup = fetcher.get_soup(_clean(row.get("raw_url")))
                    row_errors = _gseek_detail_contract(row, detail_soup)
                    if row_errors:
                        detail_errors += len(row_errors)
                        errors.extend(row_errors)
                    else:
                        detail_pages += 1
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: detail fetch {type(exc).__name__}"
                    )

        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in current_rows]))
            if len(result) != len(current_rows):
                errors.append("GSEEK dedupe changed the complete current snapshot")
                result = []

        snapshot_complete = not errors
        meta.update(
            {
                "pages": len(payloads),
                "list_requests": len(payloads) + stability_rechecks,
                "physical_requests": fetcher.physical_requests,
                "retry_count": fetcher.retry_count,
                "sessions_created": fetcher.sessions_created,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "detail_errors": detail_errors,
                "source_total": source_total,
                "source_rows": len(rows),
                "data_pages": data_pages,
                "page_counts": {page: len(payload) for page, payload in payloads.items()},
                "sentinel_page": data_pages + 1 if data_pages else 0,
                "sentinel_start": source_total + 1 if source_total else 0,
                "sentinel_count": sentinel_count,
                "sentinel_kind": "exact_post_total_empty_range",
                "stability_rechecks": stability_rechecks,
                "parent_aggregate_provider": ICHEON_GSEEK_PARENT_PROVIDER,
                "parent_exclusion_required": ICHEON_GSEEK_CO_SPONSOR_ID,
                "source_branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
                "source_region_counts": dict(
                    Counter(_clean(row.get("raw_fields", {}).get("source_region")) for row in rows)
                ),
                "test_row_count": sum(
                    bool(row.get("raw_fields", {}).get("source_test_row")) for row in rows
                ),
                "expired_count": sum(
                    date.fromisoformat(_clean(row.get("end_date"))) < cutoff for row in rows
                ),
                "current_count": len(current_rows),
                "returned_count": len(result),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
                "pagination_complete": bool(
                    snapshot_complete
                    and len(payloads) == data_pages + 1
                    and sentinel_count == 0
                    and len(rows) == source_total
                ),
                "details_complete": bool(
                    snapshot_complete
                    and detail_attempts == len(current_rows)
                    and detail_pages == len(current_rows)
                    and detail_errors == 0
                ),
                "snapshot_complete": snapshot_complete,
                "source_cap_reached": source_cap_reached,
                "no_current_data": snapshot_complete and not current_rows,
                "configured_collection_error": " | ".join(errors),
            }
        )
        if errors:
            result = []
    except Exception as exc:
        meta.update(
            {
                "physical_requests": fetcher.physical_requests,
                "retry_count": fetcher.retry_count,
                "sessions_created": fetcher.sessions_created,
                "source_cap_reached": source_cap_reached,
                "configured_collection_error": f"collector failure: {type(exc).__name__}: {_clean(exc)}",
            }
        )
        result = []
    finally:
        fetcher.close()
    return result, ICHEON_GSEEK_PARSER, meta


def _library_page_url(page: int) -> str:
    if page == 1:
        return ICHEON_LIBRARY_URL
    return ICHEON_LIBRARY_URL + "?" + urlencode({"pn": str(page)})


def _library_detail_url(identity: Any) -> str:
    raw = _clean(identity)
    if not _INTEGER_RE.fullmatch(raw):
        return ""
    return ICHEON_LIBRARY_DETAIL_BASE + "?" + urlencode({"lecture_id": raw})


def _library_status(value: Any) -> tuple[str, bool]:
    text = _clean(value)
    compact = text.replace(" ", "")
    if "접수중" in compact or "신청가능" in compact:
        return "OPEN", True
    if "접수전" in compact or "접수예정" in compact:
        return "SCHEDULED", False
    if any(token in compact for token in ("마감", "종료", "완료")):
        return "CLOSED", False
    return "", False


def _library_boundary(soup: BeautifulSoup) -> tuple[int, int, dict[str, str]]:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    total_node = soup.select_one(".totalcount")
    page_node = soup.select_one(".pageNum")
    branches = {
        _clean(option.get("value")): _clean(option.get_text(" ", strip=True))
        for option in soup.select("form#search select[name='loca'] option[value]")
        if _clean(option.get("value"))
    }
    total = _integer(total_node.get_text(" ", strip=True)) if total_node else None
    page_match = re.search(r"/\s*(\d+)\s*페이지", _clean(page_node.get_text(" ", strip=True)) if page_node else "")
    pages = int(page_match.group(1)) if page_match else 0
    if title != "이용자교육 목록 | 이천시 통합 도서관":
        return 0, 0, branches
    return total or 0, pages, branches


def _library_list_pairs(container: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if container is None:
        return pairs
    for dl in container.select("dl"):
        key_node = dl.select_one("dt")
        value_node = dl.select_one("dd")
        if key_node is None or value_node is None:
            continue
        key = _clean(key_node.get_text(" ", strip=True)).replace(" ", "").rstrip(":")
        value = _clean(value_node.get_text(" ", strip=True))
        if key in pairs and pairs[key] != value:
            raise IcheonContractError(f"conflicting library list label {key}")
        pairs[key] = value
    return pairs


def _library_list_rows(soup: BeautifulSoup) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for li in soup.select(".eventList > ul > li"):
        title_node = li.select_one("h3.eventTitle")
        title_link = title_node.find_parent("a", href=True) if title_node else None
        if title_node is None or title_link is None:
            continue
        parsed = urlparse(urljoin(ICHEON_LIBRARY_URL, _clean(title_link.get("href"))))
        query = parse_qs(parsed.query)
        identity = _clean((query.get("lecture_id") or [""])[0])
        branch_label_node = title_node.select_one("span")
        branch_label = _clean(branch_label_node.get_text(" ", strip=True)) if branch_label_node else ""
        branch = ICHEON_LIBRARY_LIST_LABEL_TO_BRANCH.get(branch_label, "")
        title = _clean(title_node.get_text(" ", strip=True))
        if branch_label and title.startswith(branch_label):
            title = title[len(branch_label):].strip()
        try:
            pairs = _library_list_pairs(li.select_one(".eventList2"))
        except IcheonContractError as exc:
            errors.append(_clean(exc))
            continue
        start, end, period = _date_range(pairs.get("강좌기간"))
        apply_start, apply_end, apply_period = _date_range(pairs.get("접수기간"))
        status_node = li.select_one("[class^='eventBtnStyle']")
        source_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
        status, reservation_available = _library_status(source_status)
        capacity_values = [
            _integer(node.get_text(" ", strip=True))
            for node in li.select(".eventBtn .numList li")
        ]
        capacity_total = capacity_values[0] if capacity_values else None
        capacity_current = capacity_values[1] if len(capacity_values) > 1 else None

        if not _INTEGER_RE.fullmatch(identity):
            errors.append("library row has a non-numeric lecture identity")
        if not title:
            errors.append(f"library course {identity}: empty title")
        if not branch:
            errors.append(f"library course {identity}: unaudited branch label {branch_label!r}")
        if not period:
            errors.append(f"library course {identity}: invalid course period")
        if not apply_period:
            errors.append(f"library course {identity}: invalid application period")
        if not status:
            errors.append(f"library course {identity}: unknown status {source_status!r}")
        raw_url = _library_detail_url(identity)
        row = {
            "provider": ICHEON_LIBRARY_PROVIDER,
            "provider_course_id": f"{ICHEON_LIBRARY_PROVIDER}:lecture:{identity}",
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "branch": branch,
            "branch_code": _branch_code("ICHEON_LIBRARY_BRANCH", branch),
            "provider_organizer": branch,
            "category": "도서관 문화행사",
            "raw_url": raw_url,
            "application_url": raw_url if reservation_available else "",
            "reservation_available": reservation_available,
            "status": status,
            "period": period,
            "start_date": start,
            "end_date": end,
            "apply_period": apply_period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "schedule_raw": _clean(pairs.get("강좌기간")),
            "target": _clean(pairs.get("강좌대상")),
            "venue_name": _clean(pairs.get("강좌장소")),
            "room": _clean(pairs.get("강좌장소")),
            "capacity": capacity_total,
            "capacity_total": capacity_total,
            "capacity_current": capacity_current,
            "description": title,
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": "static_html+detail_html",
            "program_type": "강좌",
            "region": ICHEON_CITY_NAME,
            "municipality_code": ICHEON_CITY_CODE,
            "municipality_full_name": ICHEON_CITY_NAME,
            "raw_fields": {
                "parser": ICHEON_LIBRARY_PARSER,
                "lecture_id": identity,
                "source_status": source_status,
                "source_branch_label": branch_label,
                "source_test_row": bool(_TEST_RE.search(title)),
            },
        }
        rows.append(_clean_row(row))
    return rows, errors


def _library_detail_pairs(dl: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if dl is None:
        return pairs
    for dd in dl.find_all("dd", recursive=False):
        label_node = dd.select_one("span")
        if label_node is None:
            continue
        label = _clean(label_node.get_text(" ", strip=True)).replace(" ", "").rstrip(":")
        value = _clean(dd.get_text(" ", strip=True))
        label_text = _clean(label_node.get_text(" ", strip=True))
        if value.startswith(label_text):
            value = value[len(label_text):].lstrip(" :")
        if label in pairs and pairs[label] != value:
            raise IcheonContractError(f"conflicting library detail label {label}")
        pairs[label] = value
    return pairs


def _library_detail_contract(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    raw = row.get("raw_fields") or {}
    identity = _clean(raw.get("lecture_id"))
    candidate = None
    for dl in soup.select("dl"):
        href = dl.select_one("dt a[href^='javascript:']")
        if href is not None and dl.select_one("dd span") is not None:
            candidate = dl
            break
    if candidate is None:
        return [f"library course {identity}: missing detail container"]
    heading = _clean(candidate.select_one("dt a").get_text(" ", strip=True))
    match = re.match(r"^\[([^\]]+)\]\s*(.+)$", heading)
    if not match:
        errors.append(f"library course {identity}: malformed detail heading")
    else:
        detail_branch, detail_title = (_clean(value) for value in match.groups())
        if detail_branch != _clean(row.get("branch")):
            errors.append(f"library course {identity}: detail/list branch mismatch")
        if _normalized(detail_title) != _normalized(row.get("title")):
            errors.append(f"library course {identity}: detail/list title mismatch")
    try:
        pairs = _library_detail_pairs(candidate)
    except IcheonContractError as exc:
        return [f"library course {identity}: {_clean(exc)}"]
    _, _, detail_period = _date_range(pairs.get("강좌기간"))
    _, _, detail_apply = _date_range(pairs.get("접수기간"))
    if detail_period != _clean(row.get("period")):
        errors.append(f"library course {identity}: detail/list course period mismatch")
    if detail_apply != _clean(row.get("apply_period")):
        errors.append(f"library course {identity}: detail/list application period mismatch")
    detail_status, detail_available = _library_status(pairs.get("상태"))
    if detail_status != _clean(row.get("status")):
        errors.append(f"library course {identity}: detail/list status mismatch")
    if detail_available != bool(row.get("reservation_available")):
        errors.append(f"library course {identity}: detail/list availability mismatch")
    if pairs.get("시간"):
        row["schedule_raw"] = _clean(pairs.get("시간"))
    if pairs.get("대상"):
        row["target"] = _clean(pairs.get("대상"))
    if pairs.get("강좌장소"):
        row["venue_name"] = _clean(pairs.get("강좌장소"))
        row["room"] = _clean(pairs.get("강좌장소"))
    capacity_values = [int(value) for value in re.findall(r"\d+", _clean(pairs.get("신청인원")))]
    if len(capacity_values) >= 2:
        detail_current, detail_total = capacity_values[0], capacity_values[1]
        if detail_current != row.get("capacity_current") or detail_total != row.get("capacity_total"):
            errors.append(f"library course {identity}: detail/list capacity mismatch")
    row.setdefault("raw_fields", {})["detail_validated"] = not errors
    return errors


def collect_icheon_library_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = ICHEON_DEFAULT_MAX_PAGES,
    detail_limit: int = ICHEON_DEFAULT_DETAIL_LIMIT,
    *,
    max_requests: int = ICHEON_DEFAULT_MAX_REQUESTS,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect all library history to prove the boundary, returning current rows."""

    factory, error, values = _prepare(
        target,
        ICHEON_LIBRARY_PROVIDER,
        ICHEON_LIBRARY_URL,
        timeout,
        max_pages,
        detail_limit,
        max_requests,
        session_factory,
        allow_raw_requests_for_tests,
    )
    meta = _base_meta(error)
    if factory is None:
        return [], ICHEON_LIBRARY_PARSER, meta
    timeout, max_pages, detail_limit, max_requests = values
    try:
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], ICHEON_LIBRARY_PARSER, meta

    fetcher = _Fetcher(
        session_factory=factory,
        timeout=timeout,
        max_requests=max_requests,
        sleeper=sleeper,
    )
    errors: list[str] = []
    pages: dict[int, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []
    source_total = 0
    declared_pages = 0
    sentinel_count = -1
    stability_rechecks = 0
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    source_cap_reached = False
    declared_branches: dict[str, str] = {}

    try:
        first_soup = fetcher.get_soup(ICHEON_LIBRARY_URL)
        source_total, declared_pages, declared_branches = _library_boundary(first_soup)
        if source_total < 1 or declared_pages != math.ceil(source_total / ICHEON_LIBRARY_PAGE_SIZE):
            errors.append("library declared total/page boundary is inconsistent")
        if declared_branches != {
            code: branch.replace("이천시립", "") if branch != "이천시립도서관" else "시립도서관"
            for code, branch in ICHEON_LIBRARY_BRANCHES.items()
        }:
            errors.append("library official branch option set changed")
        if declared_pages + 1 > max_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {max_pages} of {declared_pages + 1} required library pages"
            )

        if not errors:
            first_rows, page_errors = _library_list_rows(first_soup)
            pages[1] = first_rows
            errors.extend(page_errors)
            for page in range(2, declared_pages + 1):
                soup = fetcher.get_soup(_library_page_url(page))
                parsed, page_errors = _library_list_rows(soup)
                pages[page] = parsed
                errors.extend(page_errors)
            sentinel_soup = fetcher.get_soup(_library_page_url(declared_pages + 1))
            sentinel_rows, sentinel_errors = _library_list_rows(sentinel_soup)
            sentinel_count = len(sentinel_rows)
            errors.extend(sentinel_errors)
            if sentinel_count:
                errors.append("library exact post-boundary sentinel is not empty")

        if not errors:
            for page, parsed in pages.items():
                expected = min(
                    ICHEON_LIBRARY_PAGE_SIZE,
                    source_total - (page - 1) * ICHEON_LIBRARY_PAGE_SIZE,
                )
                if len(parsed) != expected:
                    errors.append(f"library page {page}: expected {expected}, got {len(parsed)}")
                rows.extend(parsed)
            if len(rows) != source_total:
                errors.append(f"library declared total {source_total} != parsed {len(rows)}")

        if not errors:
            for page in ([1] if declared_pages == 1 else [1, declared_pages]):
                repeated_soup = fetcher.get_soup(_library_page_url(page))
                repeated, repeated_errors = _library_list_rows(repeated_soup)
                errors.extend(repeated_errors)
                stability_rechecks += 1
                original_sig = _signature(
                    (r.get("provider_course_id"), r.get("title"), r.get("end_date"))
                    for r in pages[page]
                )
                repeated_sig = _signature(
                    (r.get("provider_course_id"), r.get("title"), r.get("end_date"))
                    for r in repeated
                )
                if original_sig != repeated_sig:
                    errors.append(f"library page {page}: stability signature changed")

        identities = [_clean(row.get("provider_course_id")) for row in rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"library source has {duplicate_count} duplicate identities")
        current_rows = [
            row
            for row in rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
            and not row.get("raw_fields", {}).get("source_test_row")
        ]
        if len(current_rows) > detail_limit:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {detail_limit} of {len(current_rows)} current library details"
            )
        edge_count = 1 if declared_pages == 1 else (2 if declared_pages > 1 else 0)
        required_requests = declared_pages + 1 + edge_count + len(current_rows)
        if required_requests > max_requests:
            source_cap_reached = True
            errors.append(
                f"max_requests cap allows {max_requests} of at least {required_requests} logical requests"
            )

        if not errors:
            for row in current_rows:
                detail_attempts += 1
                try:
                    detail_soup = fetcher.get_soup(_clean(row.get("raw_url")))
                    row_errors = _library_detail_contract(row, detail_soup)
                    if row_errors:
                        detail_errors += len(row_errors)
                        errors.extend(row_errors)
                    else:
                        detail_pages += 1
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: detail fetch {type(exc).__name__}"
                    )

        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in current_rows]))
            if len(result) != len(current_rows):
                errors.append("library dedupe changed the complete current snapshot")
                result = []

        snapshot_complete = not errors
        meta.update(
            {
                "pages": len(pages) + (1 if sentinel_count >= 0 else 0),
                "list_requests": len(pages) + (1 if sentinel_count >= 0 else 0) + stability_rechecks,
                "physical_requests": fetcher.physical_requests,
                "retry_count": fetcher.retry_count,
                "sessions_created": fetcher.sessions_created,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "detail_errors": detail_errors,
                "source_total": source_total,
                "source_rows": len(rows),
                "declared_pages": declared_pages,
                "page_counts": {page: len(value) for page, value in pages.items()},
                "sentinel_page": declared_pages + 1 if declared_pages else 0,
                "sentinel_count": sentinel_count,
                "sentinel_kind": "exact_post_declared_page_empty",
                "stability_rechecks": stability_rechecks,
                "declared_branches": declared_branches,
                "source_branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
                "test_row_count": sum(
                    bool(row.get("raw_fields", {}).get("source_test_row")) for row in rows
                ),
                "expired_count": sum(
                    date.fromisoformat(_clean(row.get("end_date"))) < cutoff for row in rows
                ),
                "current_count": len(current_rows),
                "returned_count": len(result),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
                "pagination_complete": bool(
                    snapshot_complete
                    and len(pages) == declared_pages
                    and sentinel_count == 0
                    and len(rows) == source_total
                ),
                "details_complete": bool(
                    snapshot_complete
                    and detail_attempts == len(current_rows)
                    and detail_pages == len(current_rows)
                    and detail_errors == 0
                ),
                "snapshot_complete": snapshot_complete,
                "source_cap_reached": source_cap_reached,
                "no_current_data": snapshot_complete and not current_rows,
                "configured_collection_error": " | ".join(errors),
            }
        )
        if errors:
            result = []
    except Exception as exc:
        meta.update(
            {
                "physical_requests": fetcher.physical_requests,
                "retry_count": fetcher.retry_count,
                "sessions_created": fetcher.sessions_created,
                "source_cap_reached": source_cap_reached,
                "configured_collection_error": f"collector failure: {type(exc).__name__}: {_clean(exc)}",
            }
        )
        result = []
    finally:
        fetcher.close()
    return result, ICHEON_LIBRARY_PARSER, meta


def _artic_page_url(page: int) -> str:
    if page == 1:
        return ICHEON_ARTIC_URL
    return f"{ICHEON_ARTIC_URL}&page={page}"


def _artic_detail_url(identity: Any) -> str:
    raw = _clean(identity)
    if not _INTEGER_RE.fullmatch(raw):
        return ""
    return ICHEON_ARTIC_DETAIL_BASE + "?" + urlencode(
        {"academyNo": raw, "menuLevel": "2", "menuNo": "13"}
    )


def _artic_status(value: Any) -> tuple[str, bool]:
    text = _clean(value).replace(" ", "")
    if any(token in text for token in ("접수중", "신청가능", "대기접수")):
        return "OPEN", True
    if any(token in text for token in ("접수예정", "접수전")):
        return "SCHEDULED", False
    if any(token in text for token in ("마감", "종료", "완료")):
        return "CLOSED", False
    return "", False


def _artic_declared_pages(soup: BeautifulSoup) -> int:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "강좌안내 및 수강신청 | 아카데미 | 이천문화재단":
        return 0
    values = {
        int(match.group())
        for node in soup.select(".pagination a")
        for match in [re.match(r"\s*(\d+)", _clean(node.get_text(" ", strip=True)))]
        if match
    }
    return max(values) if values else 0


def _artic_pairs(container: Any, selector: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    if container is None:
        return pairs
    for li in container.select("li"):
        label_node = li.select_one("strong")
        value_node = li.select_one(selector)
        if label_node is None or value_node is None:
            continue
        label = _clean(label_node.get_text(" ", strip=True))
        value = _clean(value_node.get_text(" ", strip=True))
        if label in pairs and pairs[label] != value:
            raise IcheonContractError(f"conflicting cultural-foundation label {label}")
        pairs[label] = value
    return pairs


def _artic_list_rows(soup: BeautifulSoup) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for card in soup.select(".academy_list_wrap"):
        title_node = card.select_one(".academy_list_left_title h2")
        detail_link = card.select_one("a.academy_list_right_more[href]")
        status_node = card.select_one(".academy_list_left_tag_state")
        if title_node is None or detail_link is None:
            errors.append("cultural-foundation card lacks title or detail link")
            continue
        title = _clean(title_node.get_text(" ", strip=True))
        parsed = urlparse(_clean(detail_link.get("href")))
        query = parse_qs(parsed.query)
        identity = _clean((query.get("academyNo") or [""])[0])
        source_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
        status, reservation_available = _artic_status(source_status)
        try:
            pairs = _artic_pairs(
                card.select_one(".academy_list_left_info_box"),
                ".academy_list_left_info_box_item_result",
            )
        except IcheonContractError as exc:
            errors.append(_clean(exc))
            continue
        start, end, period = _date_range(pairs.get("교육기간"))
        capacity_values = [int(value) for value in re.findall(r"\d+", _clean(pairs.get("정원")))]
        capacity_current = capacity_values[0] if capacity_values else None
        capacity_total = capacity_values[1] if len(capacity_values) > 1 else capacity_current
        expected_detail = _artic_detail_url(identity)
        normalized_actual = parsed._replace(netloc=ICHEON_ARTIC_HOST).geturl()

        if not _INTEGER_RE.fullmatch(identity):
            errors.append("cultural-foundation row has a non-numeric academy identity")
        if normalized_actual != expected_detail:
            errors.append(f"cultural-foundation course {identity}: non-canonical detail link")
        if not title:
            errors.append(f"cultural-foundation course {identity}: empty title")
        if not period:
            errors.append(f"cultural-foundation course {identity}: invalid education period")
        if not status:
            errors.append(f"cultural-foundation course {identity}: unknown status {source_status!r}")
        explicit_apply = card.select_one("a.academy_list_right_ok, button.academy_list_right_ok")
        if reservation_available and explicit_apply is None:
            errors.append(f"cultural-foundation course {identity}: open row lacks application control")
        if not reservation_available and explicit_apply is not None:
            errors.append(f"cultural-foundation course {identity}: non-open row exposes application control")
        row = {
            "provider": ICHEON_ARTIC_PROVIDER,
            "provider_course_id": f"{ICHEON_ARTIC_PROVIDER}:academy:{identity}",
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "branch": "이천문화재단",
            "branch_code": "ICHEON_CULTURAL_FOUNDATION",
            "provider_organizer": "이천문화재단",
            "category": "문화예술 아카데미",
            "raw_url": expected_detail,
            "application_url": expected_detail if reservation_available else "",
            "reservation_available": reservation_available,
            "status": status,
            "period": period,
            "start_date": start,
            "end_date": end,
            "schedule_raw": _clean(pairs.get("시간")),
            "fee": _money(pairs.get("수강료")),
            "capacity": capacity_total,
            "capacity_total": capacity_total,
            "capacity_current": capacity_current,
            "description": title,
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": "static_html+detail_html",
            "program_type": "강좌",
            "region": ICHEON_CITY_NAME,
            "municipality_code": ICHEON_CITY_CODE,
            "municipality_full_name": ICHEON_CITY_NAME,
            "raw_fields": {
                "parser": ICHEON_ARTIC_PARSER,
                "academy_no": identity,
                "source_status": source_status,
                "source_test_row": bool(_TEST_RE.search(title)),
            },
        }
        rows.append(_clean_row(row))
    return rows, errors


def _artic_detail_contract(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    errors: list[str] = []
    raw = row.get("raw_fields") or {}
    identity = _clean(raw.get("academy_no"))
    container = soup.select_one(".academy_view")
    if container is None:
        return [f"cultural-foundation course {identity}: missing detail container"]
    title_node = container.select_one(".academy_view_title h2")
    if title_node is None or _normalized(title_node.get_text(" ", strip=True)) != _normalized(row.get("title")):
        errors.append(f"cultural-foundation course {identity}: detail/list title mismatch")
    status_node = container.select_one(".academy_view_tag_state, [class*='academy_view'][class*='state']")
    detail_source_status = _clean(status_node.get_text(" ", strip=True)) if status_node else ""
    if detail_source_status and _artic_status(detail_source_status)[0] != _clean(row.get("status")):
        errors.append(f"cultural-foundation course {identity}: detail/list status mismatch")
    try:
        pairs = _artic_pairs(
            container.select_one(".academy_view_info_box"),
            ".academy_view_info_box_item_result",
        )
    except IcheonContractError as exc:
        return [f"cultural-foundation course {identity}: {_clean(exc)}"]
    _, _, detail_period = _date_range(pairs.get("교육기간"))
    if detail_period != _clean(row.get("period")):
        errors.append(f"cultural-foundation course {identity}: detail/list period mismatch")
    if pairs.get("교육시간"):
        row["schedule_raw"] = _clean(pairs.get("교육시간"))
    if pairs.get("참가대상"):
        row["target"] = _clean(pairs.get("참가대상"))
    if pairs.get("장소"):
        row["venue_name"] = _clean(pairs.get("장소"))
        row["room"] = _clean(pairs.get("장소"))
    if pairs.get("수강료"):
        row["fee"] = _money(pairs.get("수강료"))
    detail_capacity = _integer(pairs.get("정원"))
    if detail_capacity is not None and detail_capacity != row.get("capacity_total"):
        errors.append(f"cultural-foundation course {identity}: detail/list capacity mismatch")
    callable_controls = [
        node
        for node in container.select("a,button")
        if "신청" in _clean(node.get_text(" ", strip=True))
        and ("Reservation" in _clean(node.get("href")) or _clean(node.get("onclick")))
    ]
    if row.get("reservation_available") and not callable_controls:
        errors.append(f"cultural-foundation course {identity}: open detail lacks application control")
    if not row.get("reservation_available") and callable_controls:
        errors.append(f"cultural-foundation course {identity}: non-open detail exposes application control")
    row.setdefault("raw_fields", {})["detail_validated"] = not errors
    return errors


def collect_icheon_artic_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = ICHEON_DEFAULT_MAX_PAGES,
    detail_limit: int = ICHEON_DEFAULT_DETAIL_LIMIT,
    *,
    max_requests: int = ICHEON_DEFAULT_MAX_REQUESTS,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current Icheon Cultural Foundation academy."""

    factory, error, values = _prepare(
        target,
        ICHEON_ARTIC_PROVIDER,
        ICHEON_ARTIC_URL,
        timeout,
        max_pages,
        detail_limit,
        max_requests,
        session_factory,
        allow_raw_requests_for_tests,
    )
    meta = _base_meta(error)
    if factory is None:
        return [], ICHEON_ARTIC_PARSER, meta
    timeout, max_pages, detail_limit, max_requests = values
    try:
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], ICHEON_ARTIC_PARSER, meta

    fetcher = _Fetcher(
        session_factory=factory,
        timeout=timeout,
        max_requests=max_requests,
        sleeper=sleeper,
    )
    errors: list[str] = []
    pages: dict[int, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []
    declared_pages = 0
    sentinel_count = -1
    stability_rechecks = 0
    detail_attempts = 0
    detail_pages = 0
    detail_errors = 0
    source_cap_reached = False

    try:
        first_soup = fetcher.get_soup(ICHEON_ARTIC_URL)
        declared_pages = _artic_declared_pages(first_soup)
        if declared_pages < 1:
            errors.append("cultural-foundation list lacks a declared page boundary")
        if declared_pages + 1 > max_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {max_pages} of {declared_pages + 1} required academy pages"
            )
        if not errors:
            first_rows, page_errors = _artic_list_rows(first_soup)
            pages[1] = first_rows
            errors.extend(page_errors)
            for page in range(2, declared_pages + 1):
                soup = fetcher.get_soup(_artic_page_url(page))
                parsed, page_errors = _artic_list_rows(soup)
                pages[page] = parsed
                errors.extend(page_errors)
            sentinel_soup = fetcher.get_soup(_artic_page_url(declared_pages + 1))
            sentinel_rows, sentinel_errors = _artic_list_rows(sentinel_soup)
            sentinel_count = len(sentinel_rows)
            errors.extend(sentinel_errors)
            if sentinel_count:
                errors.append("cultural-foundation exact post-boundary sentinel is not empty")

        if not errors:
            for page, parsed in pages.items():
                if page < declared_pages and len(parsed) != ICHEON_ARTIC_PAGE_SIZE:
                    errors.append(f"cultural-foundation page {page}: non-full intermediate page")
                if page == declared_pages and not (1 <= len(parsed) <= ICHEON_ARTIC_PAGE_SIZE):
                    errors.append("cultural-foundation final page count is invalid")
                rows.extend(parsed)

        if not errors:
            for page in ([1] if declared_pages == 1 else [1, declared_pages]):
                repeated_soup = fetcher.get_soup(_artic_page_url(page))
                repeated, repeated_errors = _artic_list_rows(repeated_soup)
                errors.extend(repeated_errors)
                stability_rechecks += 1
                if _signature(
                    (r.get("provider_course_id"), r.get("title"), r.get("end_date"))
                    for r in pages[page]
                ) != _signature(
                    (r.get("provider_course_id"), r.get("title"), r.get("end_date"))
                    for r in repeated
                ):
                    errors.append(f"cultural-foundation page {page}: stability signature changed")

        identities = [_clean(row.get("provider_course_id")) for row in rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"cultural-foundation source has {duplicate_count} duplicate identities")
        current_rows = [
            row
            for row in rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
            and not row.get("raw_fields", {}).get("source_test_row")
        ]
        if len(current_rows) > detail_limit:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap allows {detail_limit} of {len(current_rows)} academy details"
            )
        edge_count = 1 if declared_pages == 1 else (2 if declared_pages > 1 else 0)
        required_requests = declared_pages + 1 + edge_count + len(current_rows)
        if required_requests > max_requests:
            source_cap_reached = True
            errors.append(
                f"max_requests cap allows {max_requests} of at least {required_requests} logical requests"
            )

        if not errors:
            for row in current_rows:
                detail_attempts += 1
                try:
                    detail_soup = fetcher.get_soup(_clean(row.get("raw_url")))
                    row_errors = _artic_detail_contract(row, detail_soup)
                    if row_errors:
                        detail_errors += len(row_errors)
                        errors.extend(row_errors)
                    else:
                        detail_pages += 1
                except Exception as exc:
                    detail_errors += 1
                    errors.append(
                        f"{_clean(row.get('provider_course_id'))}: detail fetch {type(exc).__name__}"
                    )

        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper([_clean_row(row) for row in current_rows]))
            if len(result) != len(current_rows):
                errors.append("cultural-foundation dedupe changed the complete current snapshot")
                result = []

        snapshot_complete = not errors
        meta.update(
            {
                "pages": len(pages) + (1 if sentinel_count >= 0 else 0),
                "list_requests": len(pages) + (1 if sentinel_count >= 0 else 0) + stability_rechecks,
                "physical_requests": fetcher.physical_requests,
                "retry_count": fetcher.retry_count,
                "sessions_created": fetcher.sessions_created,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "detail_errors": detail_errors,
                "source_total": len(rows),
                "source_rows": len(rows),
                "declared_pages": declared_pages,
                "page_counts": {page: len(value) for page, value in pages.items()},
                "sentinel_page": declared_pages + 1 if declared_pages else 0,
                "sentinel_count": sentinel_count,
                "sentinel_kind": "exact_post_declared_page_empty",
                "stability_rechecks": stability_rechecks,
                "source_branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
                "expired_count": sum(
                    date.fromisoformat(_clean(row.get("end_date"))) < cutoff for row in rows
                ),
                "current_count": len(current_rows),
                "returned_count": len(result),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
                "pagination_complete": bool(
                    snapshot_complete and len(pages) == declared_pages and sentinel_count == 0
                ),
                "details_complete": bool(
                    snapshot_complete
                    and detail_attempts == len(current_rows)
                    and detail_pages == len(current_rows)
                    and detail_errors == 0
                ),
                "snapshot_complete": snapshot_complete,
                "source_cap_reached": source_cap_reached,
                "no_current_data": snapshot_complete and not current_rows,
                "configured_collection_error": " | ".join(errors),
            }
        )
        if errors:
            result = []
    except Exception as exc:
        meta.update(
            {
                "physical_requests": fetcher.physical_requests,
                "retry_count": fetcher.retry_count,
                "sessions_created": fetcher.sessions_created,
                "source_cap_reached": source_cap_reached,
                "configured_collection_error": f"collector failure: {type(exc).__name__}: {_clean(exc)}",
            }
        )
        result = []
    finally:
        fetcher.close()
    return result, ICHEON_ARTIC_PARSER, meta


def collect_icheon_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = ICHEON_DEFAULT_MAX_PAGES,
    detail_limit: int = ICHEON_DEFAULT_DETAIL_LIMIT,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Dispatch only exact Icheon owner targets; unknown aliases fail closed."""

    provider = _target_provider(target)
    collectors: Mapping[str, Callable[..., tuple[list[dict[str, Any]], str, dict[str, Any]]]] = {
        ICHEON_CITY_PROVIDER: collect_icheon_city_education,
        ICHEON_GSEEK_PROVIDER: collect_icheon_gseek_education,
        ICHEON_LIBRARY_PROVIDER: collect_icheon_library_education,
        ICHEON_ARTIC_PROVIDER: collect_icheon_artic_education,
    }
    collector = collectors.get(provider)
    if collector is None:
        meta = _base_meta("unknown Icheon provider or non-owner alias")
        return [], "icheon_owner_dispatch", meta
    rows, parser, meta = collector(
        target,
        timeout=timeout,
        max_pages=max_pages,
        detail_limit=detail_limit,
        **kwargs,
    )
    meta.update(
        {
            "discovered_links": int(meta.get("source_rows") or meta.get("source_total") or 0),
            "pagination_detected": int(meta.get("pages") or 0) > 1,
            "full_snapshot_validated": bool(
                meta.get("snapshot_complete")
                and meta.get("pagination_complete")
                and meta.get("details_complete")
            ),
            "no_current_reason": (
                "complete owner ledger has no current/future courses"
                if meta.get("no_current_data")
                else ""
            ),
            "application_endpoints_called": 0,
        }
    )
    return rows, parser, meta


def icheon_cross_owner_overlap(
    rows_by_provider: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Return strong cross-owner overlaps without mutating or merging rows."""

    seen: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for provider, rows in rows_by_provider.items():
        for row in rows:
            key = (
                _normalized(row.get("title")),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
            )
            if not key[0] or not key[1] or not key[2]:
                continue
            seen.setdefault(key, []).append((provider, _clean(row.get("provider_course_id"))))
    overlaps = {
        "|".join(key): values
        for key, values in seen.items()
        if len({provider for provider, _identity in values}) > 1
    }
    return {
        "overlap_count": len(overlaps),
        "overlaps": overlaps,
        "worker_welfare_owner": ICHEON_WORKER_WELFARE_PROVIDER,
        "worker_welfare_merged": False,
        "gseek_parent_exclusion_required": ICHEON_GSEEK_CO_SPONSOR_ID,
    }
