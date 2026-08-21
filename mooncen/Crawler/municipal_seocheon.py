"""Fail-closed collector for Seocheon-gun's complete lifelong-course ledger.

The historical ``cn.seocheon.go.kr/prog/educate`` candidate no longer resolves.
Its provider identity is nevertheless retained because the generated registry
already migrated that provider to the live ``www.seocheon.go.kr/prog/lctr``
catalogue.  Creating another provider would duplicate the same course owner.

The live owner is a 5-row paginated ledger.  This collector reads every
declared page, proves the immediate exact empty sentinel, validates every
current/future public detail, and rechecks first/final/sentinel boundaries.
Application and login handlers are inspected only as inert HTML/JavaScript;
they are never requested or submitted.  Contact, instructor, free-form body,
image, attachment, and applicant data are never persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, TypeVar
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


SEOCHEON_PROVIDER = "MUNI_CN_SEOCHEON_GO_KR_096AAB21"
SEOCHEON_MUNICIPALITY_CODE = "4477000000"
SEOCHEON_MUNICIPALITY_NAME = "충청남도 서천군"
SEOCHEON_HOST = "www.seocheon.go.kr"
SEOCHEON_LIST_PATH = "/prog/lctr/life/sub03_01/list.do"
SEOCHEON_DETAIL_PATH = "/prog/lctr/life/sub03_01/view.do"
SEOCHEON_APPLICATION_PATH = "/prog/lctrAplcnt/life/sub03_01/write.do"
SEOCHEON_LOGIN_PATH = "/life/login.do"
SEOCHEON_CANONICAL_URL = f"https://{SEOCHEON_HOST}{SEOCHEON_LIST_PATH}"
SEOCHEON_CANONICAL_URL_SHA256 = (
    "3e6144da2613d793493025a7d037c5744ffafe497914c1872e71caa9d36edea2"
)
SEOCHEON_LEGACY_CANDIDATE_ID = "MUNI_IR_E1565CC62D6C"
SEOCHEON_LEGACY_URL = (
    "https://cn.seocheon.go.kr/prog/educate/life/sub02_01_01/list.do"
)

SEOCHEON_PAGE_SIZE = 5
SEOCHEON_RECOMMENDED_MAX_PAGES = 400
SEOCHEON_RECOMMENDED_DETAIL_LIMIT = 100
SEOCHEON_RUNNER_MAX_PAGES = 2_000
SEOCHEON_RUNNER_MAX_DETAILS = 3_000
SEOCHEON_MAX_WORKERS = 4
SEOCHEON_FETCH_ATTEMPTS = 2
SEOCHEON_MAX_HTML_BYTES = 2_000_000
SEOCHEON_PARSER = (
    "seocheon_complete_lifelong_ledger+declared_all_pages+exact_empty_sentinel+"
    "current_public_details+stable_first_final_sentinel+application_login_"
    "attachment_applicant_no_fetch+pii_allowlist"
)
SEOCHEON_OWNERSHIP_SCOPE = "seocheon_gun_complete_lifelong_course_ledger"

SEOCHEON_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    SEOCHEON_LEGACY_CANDIDATE_ID: {
        "provider": SEOCHEON_PROVIDER,
        "url": SEOCHEON_LEGACY_URL,
        "decision": "retain_provider_identity_on_migrated_canonical_owner",
        "state": "legacy_host_dns_unavailable",
        "canonical_url": SEOCHEON_CANONICAL_URL,
        "reason": (
            "provider SHA-1 prefix belongs to the retired candidate; the existing "
            "registry already rewrites it to the complete live lctr owner"
        ),
    }
}
SEOCHEON_EXISTING_PROVIDER_AUDIT: Mapping[str, Any] = {
    "provider": SEOCHEON_PROVIDER,
    "decision": "retain_existing_provider_and_replace_generic_dispatch",
    "new_provider_required": False,
    "generic_collector_issue": (
        "default page/detail caps and per-target limit truncate the 264-page ledger; "
        "generic detail enrichment also persists instructor/contact/free text"
    ),
}

SEOCHEON_CURRENT_INSTITUTION_CODES: Mapping[str, str] = {
    "서천군": "SEOCHEON_COUNTY",
    "군산대학교 평생교육원": "GUNSAN_UNIVERSITY_LIFELONG_INSTITUTE",
    "군산대학교": "GUNSAN_UNIVERSITY",
    "종합교육센터": "SEOCHEON_GENERAL_EDUCATION_CENTER",
    "서천군,충남도립대": "SEOCHEON_CHUNGNAM_STATE_UNIVERSITY",
    "서천군, 서천군수어통역센터": "SEOCHEON_SIGN_LANGUAGE_CENTER",
}

SEOCHEON_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "cutoff": "2026-07-23",
    "source_total": 1316,
    "source_pages": 264,
    "final_page_size": 1,
    "sentinel_page": 265,
    "current_total": 34,
    "expired_dated_source": 1281,
    "historical_unknown_periods": 1,
    "source_raw_status_counts": {"모집완료": 1313, "모집중": 2, "모집예정": 1},
    "current_raw_status_counts": {"모집완료": 31, "모집중": 2, "모집예정": 1},
    "status_counts": {"CLOSED": 31, "OPEN": 2, "SCHEDULED": 1},
    "current_branch_counts": {
        "서천군": 26,
        "군산대학교 평생교육원": 3,
        "군산대학교": 2,
        "종합교육센터": 1,
        "서천군,충남도립대": 1,
        "서천군, 서천군수어통역센터": 1,
    },
    "current_page_counts": {1: 5, 2: 4, 3: 5, 4: 5, 5: 5, 6: 5, 7: 3, 14: 2},
    "current_ids": [
        "1410", "1361", "1413", "1412", "1411", "1409", "1407", "1406",
        "1405", "1404", "1403", "1402", "1401", "1400", "1399", "1398",
        "1397", "1396", "1395", "1394", "1393", "1392", "1391", "1390",
        "1388", "1387", "1382", "1381", "1380", "1378", "1377", "1376",
        "1340", "1339",
    ],
    "application_control_count": 2,
    "contacts_discarded": 33,
    "instructors_discarded": 21,
    "free_text_cells_discarded": 27,
    "attachment_links_discarded": 0,
    "images_discarded": 0,
    "list_requests": 268,
    "detail_requests": 34,
    "source_requests": 302,
    "two_snapshot_requests": 604,
}

SEOCHEON_HISTORICAL_PERIOD_EXCEPTIONS: Mapping[str, str] = {
    "407": "2015-01-07 ~ 2015-01-05",
}
SEOCHEON_FIELDS_NEVER_PERSISTED = (
    "문의처·전화번호·이메일",
    "강사명·강사 개인정보",
    "강좌내용 자유서술 HTML",
    "이미지·첨부파일·다운로드 URL",
    "로그인·신청·신청자 form payload",
)

_TARGET_OPTIONS = (
    ("", "::전체::"), ("01", "전체"), ("02", "유아"), ("03", "어린이"),
    ("04", "초등"), ("05", "중등"), ("06", "고등"), ("07", "초중"),
    ("08", "중고"), ("09", "성인"), ("10", "어르신"), ("11", "장애인"),
    ("12", "유아부모동반"), ("13", "초/중/고"), ("14", "청소년/성인"),
    ("15", "일반"), ("16", "기타"),
)
_STATUS_OPTIONS = (
    ("", "::전체::"), ("01", "모집예정"), ("02", "모집중"),
    ("03", "대기자모집중"), ("04", "모집완료"), ("05", "폐강"),
)
_LIST_LABELS = ("강좌구분", "접수기간", "교육기간", "강사명", "신청/모집인원(명)")
_DETAIL_LABELS = (
    "강좌명", "강좌구분", "강좌분야", "교육대상", "접수기간", "교육기간",
    "교육요일", "교육일정", "최소모집인원(명)", "최대모집인원(명)",
    "강사명", "교육장소명", "교육기관명", "수업료(원)", "문의처", "강좌내용",
)
_STATUS_MAP: Mapping[str, str] = {
    "모집예정": "SCHEDULED",
    "모집중": "OPEN",
    "대기자모집중": "OPEN",
    "모집완료": "CLOSED",
    "폐강": "CLOSED",
}

_SAFE_RAW_FIELDS = frozenset(
    {
        "identity", "source_page", "source_position", "source_status",
        "source_category", "source_target", "source_target_badge", "source_kind", "source_period",
        "source_apply_period", "source_capacity_current", "source_capacity_total",
        "source_waitlist_current", "source_waitlist_total", "source_room",
        "source_weekdays", "source_schedule", "source_fee", "education_institution",
        "minimum_capacity", "list_identity_verified", "detail_identity_verified",
        "detail_structured_fields_verified", "application_control_present",
        "application_owner_verified", "application_endpoint_fetched",
        "login_endpoint_fetched", "applicant_endpoint_fetched",
        "attachment_endpoint_fetched", "download_endpoint_fetched",
        "application_form_submitted", "free_text_persisted", "discarded_fields",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone", "email", "contact", "instructor", "teacher", "manager",
        "attachments", "attachment_url", "download_url", "image_url", "body",
        "content_html", "guide", "notice", "applicant_name", "resident_number",
    }
)


class SeocheonContractError(ValueError):
    """Raised when the official Seocheon source violates its audited contract."""


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
T = TypeVar("T")

_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_VIEW_HANDLER = re.compile(r"^javascript:fn_search_view\('([1-9]\d*)'\)$")
_DATE_RANGE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})\s*~\s*(\d{4})-(\d{2})-(\d{2})$"
)
_DATETIME_RANGE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})\s*~\s*"
    r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})$"
)
_CAPACITY = re.compile(
    r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)"
    r"(?:\s*\(대기\s*(\d[\d,]*)\s*/\s*(\d[\d,]*)\))?$"
)
_SESSION_ID = re.compile(r"^jsessionid=[A-F0-9]{32}$")
_APPLICATION_HANDLER = re.compile(
    r"document\.actionForm\.action\s*=\s*['\"]"
    + re.escape(SEOCHEON_APPLICATION_PATH)
    + r"(?:;jsessionid=([A-F0-9]{32}))?['\"]\s*;\s*"
    r"document\.actionForm\.submit\(\)\s*;",
    flags=re.DOTALL,
)
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}")
_RESIDENT_ID = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _strict_target_url(value: str) -> bool:
    parsed = urlparse(_clean(value))
    return (
        parsed.scheme == "https"
        and parsed.netloc == SEOCHEON_HOST
        and parsed.path == SEOCHEON_LIST_PATH
        and parsed.params == parsed.query == parsed.fragment == ""
    )


def is_seocheon_education_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == SEOCHEON_PROVIDER
        and _strict_target_url(_clean(_target_value(target, "url")))
    )


is_target = is_seocheon_education_target


def seocheon_list_url(page: int) -> str:
    return f"{SEOCHEON_CANONICAL_URL}?{urlencode({'pageIndex': page})}"


def seocheon_detail_url(identity: str, page: int) -> str:
    return (
        f"https://{SEOCHEON_HOST}{SEOCHEON_DETAIL_PATH}?"
        f"{urlencode({'pageIndex': page, 'lctrSn': identity})}"
    )


def _raw_session() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return current


def _request(current: Any, url: str, timeout: int) -> Any:
    return current.get(url, timeout=timeout, allow_redirects=False)


def _unique_query(url: str) -> tuple[Any, list[tuple[str, str]], dict[str, str]]:
    parsed = urlparse(url)
    try:
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=4,
        )
    except ValueError as exc:
        raise SeocheonContractError("malformed query string") from exc
    if len(pairs) != len({key for key, _ in pairs}):
        raise SeocheonContractError("duplicate query key")
    return parsed, pairs, dict(pairs)


def _validate_fetch_url(url: str) -> str:
    parsed, _, values = _unique_query(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != SEOCHEON_HOST
        or parsed.params
        or parsed.fragment
    ):
        raise SeocheonContractError("request escaped exact Seocheon HTTPS host")
    if parsed.path == SEOCHEON_LIST_PATH:
        if set(values) != {"pageIndex"} or not _IDENTITY.fullmatch(
            values.get("pageIndex", "")
        ):
            raise SeocheonContractError("list query binding drift")
        return "list"
    if parsed.path == SEOCHEON_DETAIL_PATH:
        if (
            set(values) != {"pageIndex", "lctrSn"}
            or not _IDENTITY.fullmatch(values.get("pageIndex", ""))
            or not _IDENTITY.fullmatch(values.get("lctrSn", ""))
        ):
            raise SeocheonContractError("detail query binding drift")
        return "detail"
    raise SeocheonContractError("request escaped audited list/detail paths")


def _same_response_url(actual: str, expected: str) -> bool:
    left, right = urlparse(actual), urlparse(expected)
    return (
        left.scheme == right.scheme
        and left.netloc == right.netloc
        and left.path == right.path
        and left.params == right.params == ""
        and left.fragment == right.fragment == ""
        and parse_qsl(left.query, keep_blank_values=True)
        == parse_qsl(right.query, keep_blank_values=True)
    )


def _validate_owner_shell(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "강좌목록 < 수강신청 < 평생학습포털":
        raise SeocheonContractError("official lifelong title shell drift")
    footer = soup.select_one("footer#foot_layout address")
    if footer is None or _clean(footer.get_text(" ", strip=True)) != (
        "(33637) 충청남도 서천군 서천읍 서림로 19"
    ):
        raise SeocheonContractError("official Seocheon footer drift")


def _fetch_soup(
    current: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
) -> tuple[BeautifulSoup, int, str]:
    kind = _validate_fetch_url(url)
    last_error: Optional[BaseException] = None
    for attempt in range(1, SEOCHEON_FETCH_ATTEMPTS + 1):
        try:
            response = fetcher(current, url, timeout)
            status = int(getattr(response, "status_code", 0))
            if status != 200:
                raise requests.RequestException(f"HTTP {status}")
            if getattr(response, "history", []):
                raise SeocheonContractError("redirect history is not allowed")
            if not _same_response_url(_clean(getattr(response, "url", "")), url):
                raise SeocheonContractError("response URL drift")
            content = getattr(response, "content", b"")
            if not isinstance(content, (bytes, bytearray)):
                raise SeocheonContractError("response body is not bytes")
            if not content or len(content) > SEOCHEON_MAX_HTML_BYTES:
                raise SeocheonContractError("response body size outside audited bounds")
            try:
                source = bytes(content).decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise SeocheonContractError("response is not strict UTF-8") from exc
            soup = BeautifulSoup(source, "html.parser")
            _validate_owner_shell(soup)
            return soup, attempt, kind
        except SeocheonContractError:
            raise
        except requests.RequestException as exc:
            last_error = exc
    raise SeocheonContractError(f"request failed after retries: {_clean(last_error)}")


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Asia/Seoul")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("today must be date, datetime, ISO date string, or None")


def _option_registry(node: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(option.get("value", "")), _clean(option.get_text(" ", strip=True)))
        for option in node.find_all("option", recursive=False)
    )


def _validate_list_form(soup: BeautifulSoup, requested_page: int) -> None:
    forms = soup.select("form#searchForm")
    if len(forms) != 1:
        raise SeocheonContractError("list search form missing/duplicated")
    form = forms[0]
    action = urlparse(_clean(form.get("action")))
    if (
        _clean(form.get("method")).lower() != "post"
        or action.path != SEOCHEON_LIST_PATH
        or action.params
        or action.query
        or action.fragment
    ):
        raise SeocheonContractError("list search form method/action drift")
    controls = [
        (node.name, _clean(node.get("type")), _clean(node.get("name")))
        for node in form.select("[name]")
    ]
    if controls != [
        ("input", "hidden", "pageIndex"),
        ("input", "hidden", "lctrSn"),
        ("select", "", "searchEduTrgtSe"),
        ("select", "", "searchRcrtStts"),
        ("input", "text", "searchBgngYmd"),
        ("input", "text", "searchEndYmd"),
        ("input", "text", "searchKeyword"),
    ]:
        raise SeocheonContractError("list search control registry drift")
    values = {
        _clean(node.get("name")): str(node.get("value", ""))
        for node in form.select("input[name]")
    }
    if values != {
        "pageIndex": str(requested_page),
        "lctrSn": "",
        "searchBgngYmd": "",
        "searchEndYmd": "",
        "searchKeyword": "",
    }:
        raise SeocheonContractError("list unfiltered input binding drift")
    for name, expected in (
        ("searchEduTrgtSe", _TARGET_OPTIONS),
        ("searchRcrtStts", _STATUS_OPTIONS),
    ):
        nodes = form.select(f'select[name="{name}"]')
        if len(nodes) != 1 or _option_registry(nodes[0]) != expected:
            raise SeocheonContractError(f"list {name} option registry drift")
        if nodes[0].select("option[selected]"):
            raise SeocheonContractError(f"list {name} unexpectedly filtered")


def _parse_navigation_url(base_url: str, href: str, expected_page: int) -> None:
    absolute = urljoin(base_url, href)
    if _validate_fetch_url(absolute) != "list":
        raise SeocheonContractError("pagination escaped list owner")
    _, _, values = _unique_query(absolute)
    if values != {"pageIndex": str(expected_page)}:
        raise SeocheonContractError("pagination href/page disagreement")


def _parse_pager(
    soup: BeautifulSoup,
    requested_url: str,
    requested_page: int,
) -> tuple[Optional[int], int]:
    pagers = soup.select(".pe-pagination")
    if len(pagers) != 1:
        raise SeocheonContractError("pagination missing/duplicated")
    pager = pagers[0]
    numbers: list[int] = []
    active: list[int] = []
    for anchor in pager.select("a.page-link"):
        text = _clean(anchor.get_text(" ", strip=True))
        match = re.fullmatch(r"(?:현재페이지\s*)?([1-9]\d*)", text)
        if match is None:
            raise SeocheonContractError("numeric pagination text drift")
        number = int(match.group(1))
        numbers.append(number)
        _parse_navigation_url(requested_url, _clean(anchor.get("href")), number)
        onclick = _clean(anchor.get("onclick"))
        if "active" in (anchor.get("class") or []):
            active.append(number)
            if text != f"현재페이지 {number}" or onclick != "return false;":
                raise SeocheonContractError("active pagination binding drift")
        elif onclick != f"fn_egov_select_linkPage({number});return false;":
            raise SeocheonContractError("numeric pagination handler drift")
    if not numbers or len(numbers) != len(set(numbers)) or 1 not in numbers:
        raise SeocheonContractError("numeric pagination registry drift")
    declared_last = max(numbers)
    if declared_last not in numbers:
        raise SeocheonContractError("declared last pagination missing")
    current = active[0] if len(active) == 1 else None
    if requested_page <= declared_last:
        if current != requested_page:
            raise SeocheonContractError("requested/active page disagreement")
    elif active:
        raise SeocheonContractError("post-last page unexpectedly active")
    nav = pager.select("a.page-navi")
    if len(nav) != 2:
        raise SeocheonContractError("previous/next pagination controls drift")
    bounded = min(requested_page, declared_last)
    if requested_page > declared_last:
        expected_nav = (("이전", declared_last), ("다음", declared_last))
    else:
        expected_nav = (
            ("이전", max(1, bounded - 1)),
            ("다음", min(declared_last, bounded + 1)),
        )
    for anchor, (label, page) in zip(nav, expected_nav):
        if _clean(anchor.get_text(" ", strip=True)) != label:
            raise SeocheonContractError("previous/next label drift")
        _parse_navigation_url(requested_url, _clean(anchor.get("href")), page)
        if _clean(anchor.get("onclick")) != (
            f"fn_egov_select_linkPage({page});return false;"
        ):
            raise SeocheonContractError("previous/next handler drift")
    return current, declared_last


def _date_from_parts(parts: Sequence[str], identity: str, label: str) -> date:
    try:
        return date(*(int(value) for value in parts))
    except ValueError as exc:
        raise SeocheonContractError(f"course {identity}: invalid {label} date") from exc


def _event_period(
    value: str,
    identity: str,
) -> tuple[Optional[date], Optional[date], str]:
    cleaned = _clean(value)
    exception = SEOCHEON_HISTORICAL_PERIOD_EXCEPTIONS.get(identity)
    if exception is not None:
        if cleaned != exception:
            raise SeocheonContractError(
                f"course {identity}: historical period exception changed"
            )
        return None, None, "audited_unknown_historical_period"
    match = _DATE_RANGE.fullmatch(cleaned)
    if match is None:
        raise SeocheonContractError(f"course {identity}: education period shape drift")
    values = match.groups()
    start = _date_from_parts(values[:3], identity, "education")
    end = _date_from_parts(values[3:], identity, "education")
    if start > end:
        raise SeocheonContractError(f"course {identity}: reversed education period")
    return start, end, "exact"


def _apply_period(
    value: str,
    identity: str,
) -> tuple[datetime, datetime]:
    match = _DATETIME_RANGE.fullmatch(_clean(value))
    if match is None:
        raise SeocheonContractError(f"course {identity}: reception period shape drift")
    values = tuple(int(item) for item in match.groups())
    try:
        start = datetime(*values[:5])
        end = datetime(*values[5:])
    except ValueError as exc:
        raise SeocheonContractError(f"course {identity}: invalid reception datetime") from exc
    if start > end:
        raise SeocheonContractError(f"course {identity}: reversed reception period")
    return start, end


def _list_pairs(card: Any, identity: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    order: list[str] = []
    for item in card.select(".c-info-list > li"):
        labels = item.select(":scope > .subjact")
        values = item.select(":scope > .con")
        if len(labels) != 1 or len(values) != 1:
            raise SeocheonContractError(f"course {identity}: list pair shape drift")
        label = _clean(labels[0].get_text(" ", strip=True))
        if not label or label in pairs:
            raise SeocheonContractError(f"course {identity}: duplicate/empty list label")
        pairs[label] = _clean(values[0].get_text(" ", strip=True))
        order.append(label)
    if tuple(order) != _LIST_LABELS:
        raise SeocheonContractError(f"course {identity}: list field order/set drift")
    return pairs


def _capacity(value: str, identity: str) -> tuple[int, int, Optional[int], Optional[int]]:
    match = _CAPACITY.fullmatch(_clean(value))
    if match is None:
        raise SeocheonContractError(f"course {identity}: capacity shape drift")
    values = [int(item.replace(",", "")) if item is not None else None for item in match.groups()]
    return values[0], values[1], values[2], values[3]  # type: ignore[return-value]


def _parse_card(card: Any, page: int, position: int) -> dict[str, Any]:
    anchors = card.select(":scope > a.structured-item-link[href]")
    if len(anchors) != 1:
        raise SeocheonContractError(f"page {page} card {position}: identity anchor drift")
    match = _VIEW_HANDLER.fullmatch(_clean(anchors[0].get("href")))
    if match is None:
        raise SeocheonContractError(f"page {page} card {position}: identity handler drift")
    identity = match.group(1)
    titles = card.select(":scope > a .c-tit > .span")
    badges = card.select(":scope > a .card-top > .pe-badge")
    if len(titles) != 1 or len(badges) != 3:
        raise SeocheonContractError(f"course {identity}: title/badge structure drift")
    title = _clean(titles[0].get_text(" ", strip=True))
    badge_values = [_clean(node.get_text(" ", strip=True)) for node in badges]
    if not title or badge_values[2] not in _STATUS_MAP:
        raise SeocheonContractError(f"course {identity}: empty title or source status drift")
    pairs = _list_pairs(card, identity)
    event_start, event_end, period_quality = _event_period(pairs["교육기간"], identity)
    apply_start, apply_end = _apply_period(pairs["접수기간"], identity)
    current, total, wait_current, wait_total = _capacity(
        pairs["신청/모집인원(명)"], identity
    )
    return {
        "identity": identity,
        "page": page,
        "position": position,
        "title": title,
        "target": badge_values[0],
        "category": badge_values[1],
        "raw_status": badge_values[2],
        "kind": pairs["강좌구분"],
        "instructor_compare": pairs["강사명"],
        "event_period": pairs["교육기간"],
        "event_start": event_start,
        "event_end": event_end,
        "period_quality": period_quality,
        "apply_period": pairs["접수기간"],
        "apply_start": apply_start,
        "apply_end": apply_end,
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_current": wait_current,
        "waitlist_total": wait_total,
        "detail_url": seocheon_detail_url(identity, page),
    }


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    requested_url: str,
    requested_page: int,
) -> dict[str, Any]:
    _validate_list_form(soup, requested_page)
    active, declared_last = _parse_pager(soup, requested_url, requested_page)
    roots = soup.select(".pe-structured-list")
    if len(roots) != 1:
        raise SeocheonContractError("course ledger missing/duplicated")
    items = roots[0].select(":scope > .structured-item")
    cards = [item for item in items if item.select_one(":scope > a.structured-item-link[href]")]
    if cards:
        if len(cards) != len(items) or active != requested_page:
            raise SeocheonContractError("mixed rows/sentinel or active page drift")
        rows = [
            _parse_card(card, requested_page, position)
            for position, card in enumerate(cards, 1)
        ]
        empty = False
    else:
        if (
            len(items) != 1
            or _clean(items[0].get_text(" ", strip=True)) != "등록된 강좌가 없습니다."
            or active is not None
        ):
            raise SeocheonContractError("exact empty-page sentinel drift")
        rows = []
        empty = True
    return {
        "requested_page": requested_page,
        "active_page": active,
        "declared_last": declared_last,
        "empty": empty,
        "rows": rows,
    }


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row["identity"], row["title"], row["target"], row["category"],
        row["raw_status"], row["kind"], row["instructor_compare"],
        row["event_period"], row["event_start"], row["event_end"],
        row["period_quality"], row["apply_period"], row["apply_start"],
        row["apply_end"], row["capacity_current"], row["capacity_total"],
        row["waitlist_current"], row["waitlist_total"], row["detail_url"],
    )


def _page_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        page["requested_page"], page["active_page"], page["declared_last"],
        page["empty"], tuple(_row_signature(row) for row in page["rows"]),
    )


def _validate_pages(
    pages: Sequence[Mapping[str, Any]],
    declared_last: int,
) -> list[dict[str, Any]]:
    if len(pages) != declared_last:
        raise SeocheonContractError("incomplete declared pages")
    rows: list[dict[str, Any]] = []
    for page_number, parsed in enumerate(pages, 1):
        if (
            parsed["requested_page"] != page_number
            or parsed["active_page"] != page_number
            or parsed["declared_last"] != declared_last
            or parsed["empty"]
        ):
            raise SeocheonContractError(f"page {page_number}: pagination contract drift")
        size = len(parsed["rows"])
        if page_number < declared_last and size != SEOCHEON_PAGE_SIZE:
            raise SeocheonContractError(f"page {page_number}: pre-final page size drift")
        if page_number == declared_last and not 1 <= size <= SEOCHEON_PAGE_SIZE:
            raise SeocheonContractError("final page size drift")
        rows.extend(dict(row) for row in parsed["rows"])
    identities = [str(row["identity"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise SeocheonContractError("duplicate lctrSn identity in complete ledger")
    return rows


def _detail_pairs(table: Any, identity: str) -> tuple[dict[str, str], dict[str, Any]]:
    pairs: dict[str, str] = {}
    cells: dict[str, Any] = {}
    order: list[str] = []
    for row in table.select(":scope > tbody > tr"):
        labels = row.select(":scope > th")
        values = row.select(":scope > td")
        if len(labels) != 1 or len(values) != 1:
            raise SeocheonContractError(f"course {identity}: detail row shape drift")
        label = _clean(labels[0].get_text(" ", strip=True))
        if not label or label in pairs:
            raise SeocheonContractError(f"course {identity}: duplicate/empty detail label")
        pairs[label] = _clean(values[0].get_text(" ", strip=True))
        cells[label] = values[0]
        order.append(label)
    if tuple(order) != _DETAIL_LABELS:
        raise SeocheonContractError(f"course {identity}: detail field order/set drift")
    return pairs, cells


def _strict_integer(value: str, identity: str, label: str) -> int:
    cleaned = _clean(value)
    if not re.fullmatch(r"\d[\d,]*", cleaned):
        raise SeocheonContractError(f"course {identity}: detail {label} integer drift")
    return int(cleaned.replace(",", ""))


def _validate_detail_search_form(
    soup: BeautifulSoup,
    identity: str,
    page: int,
) -> None:
    forms = soup.select("form#searchForm")
    if len(forms) != 1:
        raise SeocheonContractError(f"course {identity}: detail search form drift")
    form = forms[0]
    parsed = urlparse(_clean(form.get("action")))
    if (
        _clean(form.get("method")).lower() != "post"
        or parsed.path != SEOCHEON_DETAIL_PATH
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise SeocheonContractError(f"course {identity}: detail form action drift")
    nodes = form.select('input[type="hidden"][name]')
    expected_names = (
        "pageIndex", "lctrSn", "searchLctrSeSn", "searchLctrFld",
        "searchEduTrgtSe", "searchRcrtStts", "searchBgngYmd",
        "searchEndYmd", "searchKeyword",
    )
    names = tuple(_clean(node.get("name")) for node in nodes)
    if names != expected_names:
        raise SeocheonContractError(f"course {identity}: detail hidden registry drift")
    values = {name: str(node.get("value", "")) for name, node in zip(names, nodes)}
    expected_values = {name: "" for name in expected_names}
    expected_values.update({"pageIndex": str(page), "lctrSn": identity})
    if values != expected_values:
        raise SeocheonContractError(f"course {identity}: detail identity/filter binding drift")


def _validate_application_owner(
    soup: BeautifulSoup,
    identity: str,
    page: int,
) -> None:
    forms = soup.select("form#actionForm")
    if len(forms) != 1:
        raise SeocheonContractError(f"course {identity}: application owner form drift")
    form = forms[0]
    parsed = urlparse(_clean(form.get("action")))
    try:
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
        )
    except ValueError as exc:
        raise SeocheonContractError(
            f"course {identity}: application owner query drift"
        ) from exc
    if len(query_pairs) != len({key for key, _ in query_pairs}):
        raise SeocheonContractError(
            f"course {identity}: duplicate application owner query"
        )
    query = dict(query_pairs)
    if (
        _clean(form.get("method")).lower() != "post"
        or parsed.scheme
        or parsed.netloc
        or parsed.path != SEOCHEON_DETAIL_PATH
        or (parsed.params and not _SESSION_ID.fullmatch(parsed.params))
        or query
        not in (
            {"lctrSn": identity},
            {"pageIndex": str(page), "lctrSn": identity},
        )
        or parsed.fragment
    ):
        raise SeocheonContractError(f"course {identity}: application owner action drift")
    hidden = form.select('input[type="hidden"][name]')
    if (
        len(hidden) != 1
        or _clean(hidden[0].get("name")) != "lctrSn"
        or str(hidden[0].get("value", "")) != identity
    ):
        raise SeocheonContractError(f"course {identity}: application identity form drift")
    scripts = "\n".join(node.get_text("\n") for node in soup.select("script:not([src])"))
    handlers = _APPLICATION_HANDLER.findall(scripts)
    if len(handlers) != 1:
        raise SeocheonContractError(f"course {identity}: application handler drift")


def _parse_detail(soup: BeautifulSoup, expected: Mapping[str, Any]) -> dict[str, Any]:
    identity = str(expected["identity"])
    page = int(expected["page"])
    tables = soup.select('table[caption], table')
    # The page contains exactly one data table; avoid accepting another table
    # merely because a layout wrapper changed.
    unique_tables: list[Any] = []
    for table in tables:
        if table not in unique_tables:
            unique_tables.append(table)
    if len(unique_tables) != 1:
        raise SeocheonContractError(f"course {identity}: detail table cardinality drift")
    table = unique_tables[0]
    captions = table.select(":scope > caption")
    if len(captions) != 1 or _clean(captions[0].get_text(" ", strip=True)) != (
        "강좌 상세에 대한 정보 제공"
    ):
        raise SeocheonContractError(f"course {identity}: detail caption drift")
    pairs, cells = _detail_pairs(table, identity)
    detail_start, detail_end, quality = _event_period(pairs["교육기간"], identity)
    apply_start, apply_end = _apply_period(pairs["접수기간"], identity)
    minimum = _strict_integer(pairs["최소모집인원(명)"], identity, "minimum capacity")
    maximum = _strict_integer(pairs["최대모집인원(명)"], identity, "maximum capacity")
    comparisons = (
        pairs["강좌명"] == expected["title"],
        pairs["강좌구분"] == expected["kind"],
        pairs["강좌분야"] == expected["category"],
        (
            pairs["교육대상"] == expected["target"]
            or (
                expected["target"] == "기타"
                and bool(pairs["교육대상"])
            )
        ),
        pairs["접수기간"] == expected["apply_period"],
        apply_start == expected["apply_start"],
        apply_end == expected["apply_end"],
        pairs["교육기간"] == expected["event_period"],
        detail_start == expected["event_start"],
        detail_end == expected["event_end"],
        quality == expected["period_quality"],
        pairs["강사명"] == expected["instructor_compare"],
        maximum == expected["capacity_total"],
    )
    if not all(comparisons):
        raise SeocheonContractError(f"course {identity}: list/detail structured data drift")
    _validate_detail_search_form(soup, identity, page)
    _validate_application_owner(soup, identity, page)

    controls = soup.select(".btn-wrap > button, .btn-wrap > a")
    normalized_status = _STATUS_MAP[str(expected["raw_status"])]
    if normalized_status == "OPEN":
        if (
            len(controls) != 1
            or controls[0].name != "button"
            or _clean(controls[0].get("type")) != "button"
            or _clean(controls[0].get("onclick")) != "fn_search_write()"
            or _clean(controls[0].get_text(" ", strip=True)) != "신청하기"
        ):
            raise SeocheonContractError(
                f"course {identity}: open application control drift"
            )
    elif controls:
        raise SeocheonContractError(
            f"course {identity}: inactive course exposes application control"
        )

    institution = pairs["교육기관명"]
    if institution not in SEOCHEON_CURRENT_INSTITUTION_CODES:
        raise SeocheonContractError(f"course {identity}: official institution drift")
    content_cell = cells["강좌내용"]
    attachment_links = content_cell.select("a[href]")
    image_nodes = content_cell.select("img")
    return {
        "identity": identity,
        "institution": institution,
        "room": pairs["교육장소명"],
        "target": pairs["교육대상"],
        "weekdays": pairs["교육요일"],
        "schedule": pairs["교육일정"],
        "minimum_capacity": minimum,
        "fee": pairs["수업료(원)"],
        "application_control_count": len(controls),
        "discarded_contact_count": int(bool(pairs["문의처"])),
        "discarded_instructor_count": int(pairs["강사명"] not in {"", "-"}),
        "discarded_free_text_count": int(bool(pairs["강좌내용"])),
        "discarded_attachment_count": len(attachment_links),
        "discarded_image_count": len(image_nodes),
    }


def _fee_amount(value: str) -> Optional[int]:
    cleaned = _clean(value)
    if cleaned in {"", "무료", "0", "0원", "없음"}:
        return 0
    match = re.fullmatch(r"(\d[\d,]*)\s*(?:원)?", cleaned)
    return int(match.group(1).replace(",", "")) if match else None


def _output_row(row: Mapping[str, Any]) -> dict[str, Any]:
    detail = row["detail"]
    identity = str(row["identity"])
    event_start: date = row["event_start"]
    event_end: date = row["event_end"]
    apply_start: datetime = row["apply_start"]
    apply_end: datetime = row["apply_end"]
    source_status = str(row["raw_status"])
    status = _STATUS_MAP[source_status]
    open_now = status == "OPEN" and int(detail["application_control_count"]) == 1
    institution = str(detail["institution"])
    capacity_current = int(row["capacity_current"])
    capacity_total = int(row["capacity_total"])
    return {
        "provider": SEOCHEON_PROVIDER,
        "provider_course_id": f"{SEOCHEON_PROVIDER}:lctrSn:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": str(row["title"]),
        "description": str(row["title"]),
        "branch": institution,
        "branch_code": SEOCHEON_CURRENT_INSTITUTION_CODES[institution],
        "branch_url": SEOCHEON_CANONICAL_URL,
        "preserve_branch": True,
        "provider_organizer": institution,
        "category": str(row["category"]),
        "program_type": "교육",
        "raw_url": str(row["detail_url"]),
        "application_url": str(row["detail_url"]) if open_now else "",
        "application_type": "ONLINE_RESERVATION" if open_now else "INFO_ONLY",
        "application_method": "온라인" if open_now else "",
        "application_methods": ["온라인"] if open_now else [],
        "reservation_available": open_now,
        "status": status,
        "raw_status": source_status,
        "fee": str(detail["fee"]),
        "fee_amount": _fee_amount(str(detail["fee"])),
        "period": f"{event_start.isoformat()} ~ {event_end.isoformat()}",
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": str(row["apply_period"]),
        "apply_start_date": apply_start.date().isoformat(),
        "apply_end_date": apply_end.date().isoformat(),
        "apply_start_at": apply_start.isoformat(timespec="minutes"),
        "apply_end_at": apply_end.isoformat(timespec="minutes"),
        "schedule_raw": _clean(f"{detail['weekdays']} {detail['schedule']}"),
        "capacity": f"{capacity_current}/{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_remaining": max(capacity_total - capacity_current, 0),
        "waitlist_current": row["waitlist_current"],
        "waitlist_total": row["waitlist_total"],
        "target": str(detail["target"]),
        "venue": institution,
        "venue_name": institution,
        "room": str(detail["room"]),
        "facility_name": institution,
        "address": "",
        "venue_address": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": SEOCHEON_PARSER,
        "municipality_code": SEOCHEON_MUNICIPALITY_CODE,
        "municipality_full_name": SEOCHEON_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": int(row["page"]),
            "source_position": int(row["position"]),
            "source_status": source_status,
            "source_category": str(row["category"]),
            "source_target": str(detail["target"]),
            "source_target_badge": str(row["target"]),
            "source_kind": str(row["kind"]),
            "source_period": str(row["event_period"]),
            "source_apply_period": str(row["apply_period"]),
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "source_waitlist_current": row["waitlist_current"],
            "source_waitlist_total": row["waitlist_total"],
            "source_room": str(detail["room"]),
            "source_weekdays": str(detail["weekdays"]),
            "source_schedule": str(detail["schedule"]),
            "source_fee": str(detail["fee"]),
            "education_institution": institution,
            "minimum_capacity": int(detail["minimum_capacity"]),
            "list_identity_verified": True,
            "detail_identity_verified": True,
            "detail_structured_fields_verified": True,
            "application_control_present": open_now,
            "application_owner_verified": True,
            "application_endpoint_fetched": False,
            "login_endpoint_fetched": False,
            "applicant_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "download_endpoint_fetched": False,
            "application_form_submitted": False,
            "free_text_persisted": False,
            "discarded_fields": list(SEOCHEON_FIELDS_NEVER_PERSISTED),
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden PII/free-text key")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields allowlist exceeded")
    payload = repr(
        {key: value for key, value in row.items() if key not in {"raw_url", "branch_url", "application_url"}}
    )
    if _PHONE.search(payload) or _EMAIL.search(payload) or _RESIDENT_ID.search(payload):
        errors.append("PII-like value escaped structured allowlist")
    return errors


def _initial_meta() -> dict[str, Any]:
    return {
        "provider": SEOCHEON_PROVIDER,
        "provider_decision": "retain existing migrated provider; do not create duplicate provider",
        "canonical_url": SEOCHEON_CANONICAL_URL,
        "canonical_url_sha256": SEOCHEON_CANONICAL_URL_SHA256,
        "legacy_candidate_url": SEOCHEON_LEGACY_URL,
        "legacy_candidate_state": "dns_unavailable_and_superseded",
        "parser": SEOCHEON_PARSER,
        "ownership_scope": SEOCHEON_OWNERSHIP_SCOPE,
        "municipality_code": SEOCHEON_MUNICIPALITY_CODE,
        "municipality_full_name": SEOCHEON_MUNICIPALITY_NAME,
        "page_size": SEOCHEON_PAGE_SIZE,
        "recommended_max_pages": SEOCHEON_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": SEOCHEON_RECOMMENDED_DETAIL_LIMIT,
        "pagination_boundary_mode": "declared_last_plus_exact_immediate_empty_sentinel",
        "pages": 0,
        "discovered_links": 0,
        "pagination_detected": False,
        "source_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "request_attempts": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "application_form_submissions": 0,
        "source_total_count": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "historical_unknown_period_count": 0,
        "row_count": 0,
        "detail_pages": 0,
        "identity_duplicate_count": 0,
        "application_control_count": 0,
        "contacts_discarded": 0,
        "instructors_discarded": 0,
        "free_text_cells_discarded": 0,
        "attachment_links_discarded": 0,
        "images_discarded": 0,
        "pagination_complete": False,
        "empty_sentinel_verified": False,
        "identity_unique": False,
        "details_complete": False,
        "stable_boundary_recheck": False,
        "privacy_boundary_complete": False,
        "semantic_quality_passed": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
    }


def collect_seocheon_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = SEOCHEON_RECOMMENDED_MAX_PAGES,
    detail_limit: int = SEOCHEON_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, stable, privacy-safe Seocheon education snapshot."""

    meta = _initial_meta()
    if not is_seocheon_education_target(target):
        meta["configured_collection_error"] = "target does not match exact retained Seocheon owner"
        return [], SEOCHEON_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], SEOCHEON_PARSER, meta
        session_factory = _raw_session
    try:
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ValueError("timeout must be a positive integer")
        if (
            isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or not 1 <= max_pages <= SEOCHEON_RUNNER_MAX_PAGES
        ):
            raise ValueError(
                f"max_pages must be between 1 and {SEOCHEON_RUNNER_MAX_PAGES}"
            )
        if (
            isinstance(detail_limit, bool)
            or not isinstance(detail_limit, int)
            or not 0 <= detail_limit <= SEOCHEON_RUNNER_MAX_DETAILS
        ):
            raise ValueError(
                "detail_limit must be between 0 and "
                f"{SEOCHEON_RUNNER_MAX_DETAILS}"
            )
        effective_max_pages = min(max_pages, SEOCHEON_RECOMMENDED_MAX_PAGES)
        effective_detail_limit = min(
            detail_limit, SEOCHEON_RECOMMENDED_DETAIL_LIMIT
        )
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], SEOCHEON_PARSER, meta

    current_fetcher = fetcher or _request
    try:
        main_session = session_factory()
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"{type(exc).__name__}: session_factory failed: {_clean(exc)}"
        )
        return [], SEOCHEON_PARSER, meta

    def fetch_list(current: Any, page: int) -> tuple[dict[str, Any], int]:
        url = seocheon_list_url(page)
        soup, attempts, kind = _fetch_soup(current, url, timeout, current_fetcher)
        if kind != "list":
            raise SeocheonContractError("list request classification drift")
        return _parse_list_page(
            soup, requested_url=url, requested_page=page
        ), attempts

    def fetch_detail(current: Any, row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        soup, attempts, kind = _fetch_soup(
            current, str(row["detail_url"]), timeout, current_fetcher
        )
        if kind != "detail":
            raise SeocheonContractError("detail request classification drift")
        return _parse_detail(soup, row), attempts

    def parallel_batches(
        items: Sequence[T],
        worker_item: Callable[[Any, T], tuple[Any, int]],
    ) -> list[tuple[Any, int]]:
        if not items:
            return []
        if fetcher is not None or len(items) == 1:
            return [worker_item(main_session, item) for item in items]
        chunks = [list(items[index::SEOCHEON_MAX_WORKERS]) for index in range(SEOCHEON_MAX_WORKERS)]

        def worker(chunk: list[T]) -> list[tuple[Any, int]]:
            worker_session = session_factory()
            try:
                return [worker_item(worker_session, item) for item in chunk]
            finally:
                close = getattr(worker_session, "close", None)
                if callable(close):
                    close()

        indexed: dict[int, tuple[Any, int]] = {}
        with ThreadPoolExecutor(max_workers=SEOCHEON_MAX_WORKERS) as executor:
            futures = {
                executor.submit(worker, chunk): index
                for index, chunk in enumerate(chunks)
                if chunk
            }
            chunk_results: dict[int, list[tuple[Any, int]]] = {}
            for future in as_completed(futures):
                chunk_results[futures[future]] = future.result()
        for chunk_index, chunk in enumerate(chunks):
            for offset, result in enumerate(chunk_results.get(chunk_index, [])):
                indexed[chunk_index + offset * SEOCHEON_MAX_WORKERS] = result
        return [indexed[index] for index in range(len(items))]

    def account_list(results: Sequence[tuple[Any, int]]) -> None:
        meta["source_requests"] += len(results)
        meta["list_requests"] += len(results)
        meta["request_attempts"] += sum(attempts for _, attempts in results)

    def account_detail(results: Sequence[tuple[Any, int]]) -> None:
        meta["source_requests"] += len(results)
        meta["detail_requests"] += len(results)
        meta["request_attempts"] += sum(attempts for _, attempts in results)

    try:
        first_result = fetch_list(main_session, 1)
        account_list([first_result])
        first_page = first_result[0]
        declared_last = int(first_page["declared_last"])
        if declared_last < 1 or declared_last > effective_max_pages:
            raise SeocheonContractError(
                "source cap: declared last page "
                f"{declared_last} exceeds effective max_pages {effective_max_pages}"
            )
        remaining_pages = list(range(2, declared_last + 1))
        remaining_results = parallel_batches(remaining_pages, fetch_list)
        account_list(remaining_results)
        parsed_pages = [first_page, *(result for result, _ in remaining_results)]
        listed = _validate_pages(parsed_pages, declared_last)

        sentinel_result = fetch_list(main_session, declared_last + 1)
        account_list([sentinel_result])
        sentinel = sentinel_result[0]
        if (
            sentinel["requested_page"] != declared_last + 1
            or sentinel["active_page"] is not None
            or sentinel["declared_last"] != declared_last
            or not sentinel["empty"]
            or sentinel["rows"]
        ):
            raise SeocheonContractError("immediate post-last exact empty sentinel drift")

        current_rows = [
            row
            for row in listed
            if row["event_end"] is not None and row["event_end"] >= cutoff
        ]
        for row in current_rows:
            normalized = _STATUS_MAP[str(row["raw_status"])]
            apply_start: datetime = row["apply_start"]
            apply_end: datetime = row["apply_end"]
            if normalized == "OPEN" and not (
                apply_start.date() <= cutoff <= apply_end.date()
            ):
                raise SeocheonContractError(
                    f"course {row['identity']}: open status/reception date drift"
                )
            if normalized == "SCHEDULED" and cutoff > apply_start.date():
                raise SeocheonContractError(
                    f"course {row['identity']}: scheduled status/reception date drift"
                )
        if len(current_rows) > effective_detail_limit:
            raise SeocheonContractError(
                "source cap: "
                f"{len(current_rows)} current details exceed effective detail_limit "
                f"{effective_detail_limit}"
            )
        detail_results = parallel_batches(current_rows, fetch_detail)
        account_detail(detail_results)
        for row, (detail, _) in zip(current_rows, detail_results):
            row["detail"] = detail

        recheck_pages = [1, declared_last, declared_last + 1]
        recheck_results = parallel_batches(recheck_pages, fetch_list)
        account_list(recheck_results)
        expected_boundaries = [first_page, parsed_pages[-1], sentinel]
        for (actual, _), expected in zip(recheck_results, expected_boundaries):
            if _page_signature(actual) != _page_signature(expected):
                raise SeocheonContractError("source boundaries changed during detail collection")

        rows = [_output_row(row) for row in current_rows]
        failures = [error for row in rows for error in _privacy_errors(row)]
        if failures:
            raise SeocheonContractError("; ".join(sorted(set(failures))))
        before_ids = {str(row["provider_course_id"]) for row in rows}
        if dedupe_rows is not None:
            rows = [dict(row) for row in dedupe_rows(rows)]
        after_ids = [str(row.get("provider_course_id", "")) for row in rows]
        if len(after_ids) != len(set(after_ids)) or set(after_ids) != before_ids:
            raise SeocheonContractError("dedupe_rows changed complete identity cardinality")
        failures = [error for row in rows for error in _privacy_errors(row)]
        if failures:
            raise SeocheonContractError("; ".join(sorted(set(failures))))

        unknown_periods = sum(row["event_end"] is None for row in listed)
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "source_total_count": len(listed),
                "current_source_count": len(current_rows),
                "expired_source_count": len(listed) - len(current_rows) - unknown_periods,
                "historical_unknown_period_count": unknown_periods,
                "row_count": len(rows),
                "detail_pages": len(detail_results),
                "source_pages": declared_last,
                "pages": declared_last,
                "discovered_links": len(listed),
                "pagination_detected": declared_last > 1,
                "final_page_size": len(parsed_pages[-1]["rows"]),
                "sentinel_page": declared_last + 1,
                "source_raw_status_counts": dict(
                    Counter(str(row["raw_status"]) for row in listed)
                ),
                "current_raw_status_counts": dict(
                    Counter(str(row["raw_status"]) for row in current_rows)
                ),
                "status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "current_branch_counts": dict(
                    Counter(str(row["branch"]) for row in rows)
                ),
                "current_page_counts": dict(
                    Counter(int(row["page"]) for row in current_rows)
                ),
                "current_ids": [str(row["identity"]) for row in current_rows],
                "identity_union_count": len(listed),
                "identity_duplicate_count": 0,
                "application_control_count": sum(
                    int(detail["application_control_count"])
                    for detail, _ in detail_results
                ),
                "contacts_discarded": sum(
                    int(detail["discarded_contact_count"])
                    for detail, _ in detail_results
                ),
                "instructors_discarded": sum(
                    int(detail["discarded_instructor_count"])
                    for detail, _ in detail_results
                ),
                "free_text_cells_discarded": sum(
                    int(detail["discarded_free_text_count"])
                    for detail, _ in detail_results
                ),
                "attachment_links_discarded": sum(
                    int(detail["discarded_attachment_count"])
                    for detail, _ in detail_results
                ),
                "images_discarded": sum(
                    int(detail["discarded_image_count"])
                    for detail, _ in detail_results
                ),
                "full_page_requests": declared_last,
                "sentinel_requests": 1,
                "boundary_recheck_requests": len(recheck_pages),
                "pagination_complete": True,
                "empty_sentinel_verified": True,
                "identity_unique": True,
                "details_complete": True,
                "stable_boundary_recheck": True,
                "privacy_boundary_complete": True,
                "semantic_quality_passed": True,
                "snapshot_complete": True,
                "no_current_data": not rows,
                "configured_collection_error": "",
            }
        )
        return rows, SEOCHEON_PARSER, meta
    except Exception as exc:
        if "source cap:" in _clean(exc):
            meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["snapshot_complete"] = False
        meta["semantic_quality_passed"] = False
        return [], SEOCHEON_PARSER, meta
    finally:
        close = getattr(main_session, "close", None)
        if callable(close):
            close()


collect = collect_seocheon_education


__all__ = [
    "SEOCHEON_APPLICATION_PATH", "SEOCHEON_CANONICAL_URL",
    "SEOCHEON_CANONICAL_URL_SHA256", "SEOCHEON_CANDIDATE_AUDIT",
    "SEOCHEON_CURRENT_INSTITUTION_CODES", "SEOCHEON_DETAIL_PATH",
    "SEOCHEON_EXISTING_PROVIDER_AUDIT", "SEOCHEON_FIELDS_NEVER_PERSISTED",
    "SEOCHEON_HISTORICAL_PERIOD_EXCEPTIONS", "SEOCHEON_HOST",
    "SEOCHEON_LEGACY_CANDIDATE_ID", "SEOCHEON_LEGACY_URL",
    "SEOCHEON_LIST_PATH", "SEOCHEON_LIVE_AUDIT_BASELINE",
    "SEOCHEON_LOGIN_PATH", "SEOCHEON_MUNICIPALITY_CODE",
    "SEOCHEON_MUNICIPALITY_NAME", "SEOCHEON_OWNERSHIP_SCOPE",
    "SEOCHEON_PAGE_SIZE", "SEOCHEON_PARSER", "SEOCHEON_PROVIDER",
    "SEOCHEON_RECOMMENDED_DETAIL_LIMIT", "SEOCHEON_RECOMMENDED_MAX_PAGES",
    "SEOCHEON_RUNNER_MAX_DETAILS", "SEOCHEON_RUNNER_MAX_PAGES",
    "SeocheonContractError", "collect", "collect_seocheon_education",
    "is_seocheon_education_target", "is_target", "seocheon_detail_url",
    "seocheon_list_url",
]
