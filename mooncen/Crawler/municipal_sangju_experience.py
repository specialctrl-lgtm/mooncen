"""Fail-closed collector for Sangju City's official experience ledger.

``MUNI_WWW_SANGJU_GO_KR_D7A38366`` exclusively owns the first-party
``체험/견학`` catalogue selected by menu ``15383`` and class
``RMS004004``.  It is a separate service ledger from the incumbent Sangju
education provider; neither provider may match or dispatch the other's URL.

Every snapshot walks the complete unfiltered ledger, proves the exact twelve
facility partitions, reads every current/future public detail, and then
rechecks the first, last, and post-last boundaries.  Application controls are
identity-validated but ``apply.tc`` is never requested.  Standing facility
directory rows and expired rows are audited but never returned.  Instructor
names, contacts, free-text panels, images, and attachments are deliberately
outside the returned allowlist.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


SANGJU_EXPERIENCE_PROVIDER = "MUNI_WWW_SANGJU_GO_KR_D7A38366"
SANGJU_EXPERIENCE_MUNICIPALITY_CODE = "4725000000"
SANGJU_EXPERIENCE_MUNICIPALITY_NAME = "경상북도 상주시"
SANGJU_EXPERIENCE_HOST = "www.sangju.go.kr"
SANGJU_EXPERIENCE_CANONICAL_URL = f"https://{SANGJU_EXPERIENCE_HOST}/page/15383/11881.tc?pageIndex=1"
SANGJU_EXPERIENCE_CANONICAL_URL_SHA1 = "d7a3836637a8feeefdf66f73da41c168c4941de5"
SANGJU_EXPERIENCE_CANONICAL_URL_SHA256 = "06252147160ca5c7357d66edd5ccc1a702e3774f49aa26f621adc00823810326"
SANGJU_EXPERIENCE_LIST_PATH = "/reserve/reservation/list.tc"
SANGJU_EXPERIENCE_DETAIL_PATH = "/reserve/reservation/detail.tc"
SANGJU_EXPERIENCE_APPLICATION_PATH = "/reserve/reservation/apply.tc"
SANGJU_EXPERIENCE_PAGE_NO = "11881"
SANGJU_EXPERIENCE_MENU_NO = "15383"
SANGJU_EXPERIENCE_CLASS_CODE = "RMS004004"
SANGJU_EXPERIENCE_PAGE_SIZE = 8
SANGJU_EXPERIENCE_RECOMMENDED_MAX_PAGES = 100
SANGJU_EXPERIENCE_RECOMMENDED_DETAIL_LIMIT = 100
SANGJU_EXPERIENCE_MAX_WORKERS = 4
SANGJU_EXPERIENCE_FETCH_ATTEMPTS = 2
SANGJU_EXPERIENCE_MAX_HTML_BYTES = 3_000_000
SANGJU_EXPERIENCE_PARSER = (
    "sangju_integrated_complete_current_experience+exact_post_last_empty+"
    "twelve_facility_partition+all_current_public_details+stable_first_last_"
    "sentinel_recheck+cycl_rcpt_identity_controls_no_apply_fetch+standing_"
    "directory_exclusion+pii_allowlist"
)
SANGJU_EXPERIENCE_OWNERSHIP_SCOPE = "sangju_city_integrated_reservation_complete_experience_ledger"


class SangjuExperienceContractError(ValueError):
    """Raised when the official source violates its audited contract."""


@dataclass(frozen=True)
class SangjuExperienceFacility:
    code: str
    name: str

    @property
    def url(self) -> str:
        return _list_url(1, self.code)


SANGJU_EXPERIENCE_FACILITIES: tuple[SangjuExperienceFacility, ...] = (
    SangjuExperienceFacility("129", "상주시립도서관(생활문화센터)"),
    SangjuExperienceFacility("135", "상주시 청소년 해양교육원"),
    SangjuExperienceFacility("92", "상주시육아종합지원센터"),
    SangjuExperienceFacility("89", "상주목재문화체험장"),
    SangjuExperienceFacility("90", "상주시힐링센터"),
    SangjuExperienceFacility("102", "밀리터리 테마파크"),
    SangjuExperienceFacility("119", "상주시농업기술센터"),
    SangjuExperienceFacility("105", "상주보 물놀이장"),
    SangjuExperienceFacility("111", "거꾸로 옛이야기나라숲 이야기공작소"),
    SangjuExperienceFacility("113", "상주박물관"),
    SangjuExperienceFacility("120", "국제승마장관리사업소"),
    SangjuExperienceFacility("100004", "낙동강 어린이 수상안전교육장"),
)
SANGJU_EXPERIENCE_FACILITY_BY_CODE = {item.code: item for item in SANGJU_EXPERIENCE_FACILITIES}
SANGJU_EXPERIENCE_FACILITY_BY_NAME = {item.name: item for item in SANGJU_EXPERIENCE_FACILITIES}

SANGJU_EXPERIENCE_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "url": SANGJU_EXPERIENCE_CANONICAL_URL,
        "decision": "one_complete_official_experience_ledger",
    },
)

SANGJU_EXPERIENCE_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "cutoff": "2026-08-05",
    "source_total": 17,
    "current_total": 1,
    "standing_source_total": 8,
    "expired_source_total": 8,
    "data_pages": 3,
    "page_sizes": [8, 8, 1],
    "post_last_page": 4,
    "facility_counts": {
        "129": 1,
        "135": 1,
        "92": 1,
        "89": 1,
        "90": 1,
        "102": 1,
        "119": 1,
        "105": 1,
        "111": 1,
        "113": 6,
        "120": 1,
        "100004": 1,
    },
    "facility_pages": {
        "129": 1,
        "135": 1,
        "92": 1,
        "89": 1,
        "90": 1,
        "102": 1,
        "119": 1,
        "105": 1,
        "111": 1,
        "113": 1,
        "120": 1,
        "100004": 1,
    },
    "current_facility_counts": {"129": 1},
    "status_counts": {"OPEN": 1},
    "current_ids": ["100609"],
    "identity_first": "100609",
    "identity_last": "165",
    "list_requests": 19,
    "detail_requests": 1,
    "source_requests": 20,
    "two_snapshot_requests": 40,
}

SANGJU_EXPERIENCE_FIELDS_NEVER_PERSISTED = (
    "강사",
    "담당자·연락처·전화번호·이메일",
    "상세안내·유의사항·문의 등 자유서술 본문",
    "첨부파일·이미지·파일 다운로드 URL",
    "신청자 입력값·로그인·본인인증·신청 form payload",
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_ORDINAL = re.compile(r"^\d+$")
_LIST_DETAIL_ACTION = re.compile(r"^reserveList[.]detail\('([1-9]\d*)'\);?$")
_DETAIL_APPLY_ACTION = re.compile(r"^reserveDetail[.]apply\('([1-9]\d*)'\);?$")
_LIST_APPLY_ACTION = re.compile(r"^reserveList[.]apply\('([1-9]\d*)'\);?$")
_PAGE_ACTION = re.compile(r"^reserveList[.]pageMove\(([1-9]\d*)\);\s*return false;$")
_FACILITY_ACTION = re.compile(r"^reserveList[.]searchFacility\('([0-9]*)'\);$")
_ISO_DATE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_KOREAN_DATETIME = re.compile(
    r"(?<!\d)(20\d{2})년\s*(\d{2})월\s*(\d{2})일\s*"
    r"([01]?\d|2[0-3])시\s*([0-5]\d)분(?!\d)"
)
_COUNT = re.compile(r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)명$")
_PHONE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}|"
    r"01[016789][\s.-]*\d{3,4}[\s.-]*\d{4})(?!\d)"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}")
_RESIDENT_ID = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_LIST_FIELD_LABELS = ("분류", "시설명", "운영기간", "주소")
_DETAIL_REQUIRED_LABELS = frozenset({"분류", "시설명", "주소", "운영기간"})
_DETAIL_OPTIONAL_DISCARDED_LABELS = frozenset({"강사"})
_RECEPTION_FIELD_SHAPES = frozenset(
    {
        ("접수기간", "정원", "후보"),
        ("접수기간", "정원"),
        ("접수기간",),
    }
)
_STATUS_MAP = {
    "예약": "OPEN",
    "대기": "SCHEDULED",
    "종료": "CLOSED",
    "정보제공": "CLOSED",
    "예약불가": "CLOSED",
    "정보없음": "CLOSED",
}
_DETAIL_STATE_BY_SOURCE = {
    "예약": "온라인예약 접수중",
    "대기": "온라인예약 준비중",
    "종료": "온라인예약 준비중",
    "정보제공": "정보제공",
}
_AUDITED_REVERSED_HISTORICAL_PERIOD_IDS = frozenset({"263"})
_AUDITED_UNAVAILABLE_BADGE_ROWS: Mapping[str, tuple[str, str, str, str]] = {
    "170": (
        "상주보 물놀이장",
        "상주보 물놀이장",
        "2024-07-01 ~ 2024-09-30",
        "경북 상주시 도남동 146",
    ),
    "165": (
        "밀리터리 테마파크 일반 예약",
        "밀리터리 테마파크",
        "2024-03-04 ~ 상시운영",
        "경북 상주시 사벌국면 경천로 654 (삼덕리)",
    ),
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_page",
        "source_position",
        "source_ordinal",
        "source_category",
        "source_facility_code",
        "source_facility_name",
        "source_address",
        "source_status",
        "source_experience_period",
        "source_operating_schedule",
        "source_reception_heading",
        "source_apply_period",
        "source_capacity_current",
        "source_capacity_total",
        "source_waitlist_current",
        "source_waitlist_total",
        "source_rcpt_no",
        "list_identity_verified",
        "facility_partition_verified",
        "detail_identity_verified",
        "detail_structured_fields_verified",
        "detail_reception_verified",
        "application_control_present",
        "application_control_verified",
        "application_endpoint_fetched",
        "application_form_submitted",
        "attachment_endpoint_fetched",
        "free_text_persisted",
        "discarded_fields",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "instructor",
        "teacher",
        "manager",
        "contact",
        "phone",
        "email",
        "attachments",
        "attachment_url",
        "image_url",
        "body",
        "content_html",
        "guide",
        "notice",
        "applicant_name",
        "resident_number",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _strict_target_url(url: str) -> bool:
    parsed = urlparse(_clean(url))
    return (
        parsed.scheme == "https"
        and parsed.netloc == SANGJU_EXPERIENCE_HOST
        and parsed.path == "/page/15383/11881.tc"
        and parsed.params == ""
        and parsed.fragment == ""
        and parse_qsl(parsed.query, keep_blank_values=True) == [("pageIndex", "1")]
    )


def is_sangju_experience_target(target: Any) -> bool:
    return _clean(_target_value(target, "provider")) == SANGJU_EXPERIENCE_PROVIDER and _strict_target_url(
        _clean(_target_value(target, "url"))
    )


is_target = is_sangju_experience_target


def _raw_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MoonCen-Sangju-Experience-Audit/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _list_url(page: int, facility_code: str = "") -> str:
    query = urlencode(
        (
            ("pageNo", SANGJU_EXPERIENCE_PAGE_NO),
            ("mn", SANGJU_EXPERIENCE_MENU_NO),
            ("searchTrgtClsfCd", SANGJU_EXPERIENCE_CLASS_CODE),
            ("searchFcltNo", facility_code),
            ("pageIndex", str(page)),
        )
    )
    return f"https://{SANGJU_EXPERIENCE_HOST}{SANGJU_EXPERIENCE_LIST_PATH}?{query}"


def _detail_url(identity: str) -> str:
    query = urlencode(
        (
            ("mn", SANGJU_EXPERIENCE_MENU_NO),
            ("pageNo", SANGJU_EXPERIENCE_PAGE_NO),
            ("searchTrgtClsfCd", SANGJU_EXPERIENCE_CLASS_CODE),
            ("searchFcltNo", ""),
            ("cyclNo", identity),
        )
    )
    return f"https://{SANGJU_EXPERIENCE_HOST}{SANGJU_EXPERIENCE_DETAIL_PATH}?{query}"


def _application_url(identity: str, rcpt_no: str) -> str:
    query = urlencode(
        (
            ("mn", SANGJU_EXPERIENCE_MENU_NO),
            ("pageNo", SANGJU_EXPERIENCE_PAGE_NO),
            ("searchTrgtClsfCd", SANGJU_EXPERIENCE_CLASS_CODE),
            ("searchFcltNo", ""),
            ("cyclNo", identity),
            ("rcptNo", rcpt_no),
        )
    )
    return f"https://{SANGJU_EXPERIENCE_HOST}{SANGJU_EXPERIENCE_APPLICATION_PATH}?{query}"


def _validate_fetch_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != SANGJU_EXPERIENCE_HOST or parsed.fragment:
        raise SangjuExperienceContractError("request escaped exact Sangju HTTPS host")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.path == SANGJU_EXPERIENCE_LIST_PATH:
        expected_keys = ["pageNo", "mn", "searchTrgtClsfCd", "searchFcltNo", "pageIndex"]
        if [key for key, _ in pairs] != expected_keys:
            raise SangjuExperienceContractError("list query shape/order drift")
        values = dict(pairs)
        if (
            values["pageNo"] != SANGJU_EXPERIENCE_PAGE_NO
            or values["mn"] != SANGJU_EXPERIENCE_MENU_NO
            or values["searchTrgtClsfCd"] != SANGJU_EXPERIENCE_CLASS_CODE
            or values["searchFcltNo"] not in {"", *SANGJU_EXPERIENCE_FACILITY_BY_CODE}
            or not _IDENTITY.fullmatch(values["pageIndex"])
        ):
            raise SangjuExperienceContractError("list query value drift")
        return "list"
    if parsed.path == SANGJU_EXPERIENCE_DETAIL_PATH:
        expected_keys = ["mn", "pageNo", "searchTrgtClsfCd", "searchFcltNo", "cyclNo"]
        if [key for key, _ in pairs] != expected_keys:
            raise SangjuExperienceContractError("detail query shape/order drift")
        values = dict(pairs)
        if (
            values["mn"] != SANGJU_EXPERIENCE_MENU_NO
            or values["pageNo"] != SANGJU_EXPERIENCE_PAGE_NO
            or values["searchTrgtClsfCd"] != SANGJU_EXPERIENCE_CLASS_CODE
            or values["searchFcltNo"] != ""
            or not _IDENTITY.fullmatch(values["cyclNo"])
        ):
            raise SangjuExperienceContractError("detail query value drift")
        return "detail"
    raise SangjuExperienceContractError("request escaped audited list/detail endpoints")


def _same_response_url(actual: str, expected: str) -> bool:
    left, right = urlparse(actual), urlparse(expected)
    return (
        left.scheme == right.scheme
        and left.netloc == right.netloc
        and left.path == right.path
        and left.params == right.params == ""
        and left.fragment == right.fragment == ""
        and parse_qsl(left.query, keep_blank_values=True) == parse_qsl(right.query, keep_blank_values=True)
    )


def _validate_owner_shell(soup: BeautifulSoup, kind: str) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "상주시" not in title or "통합예약 홈페이지" not in title:
        raise SangjuExperienceContractError(f"{kind} owner/title shell drift")
    page_text = _clean(soup.get_text(" ", strip=True))
    if "(37211) 경상북도 상주시 상산로 223(남성동 140-3)" not in page_text:
        raise SangjuExperienceContractError(f"{kind} official owner footer drift")


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
) -> tuple[BeautifulSoup, int, str]:
    kind = _validate_fetch_url(url)
    last_error: Optional[BaseException] = None
    for attempt in range(1, SANGJU_EXPERIENCE_FETCH_ATTEMPTS + 1):
        try:
            response = fetcher(session, url, timeout)
            status = int(getattr(response, "status_code", 0))
            if status != 200:
                raise requests.RequestException(f"HTTP {status}")
            if getattr(response, "history", []):
                raise SangjuExperienceContractError("redirect history is not allowed")
            actual_url = _clean(getattr(response, "url", ""))
            if not _same_response_url(actual_url, url):
                raise SangjuExperienceContractError("response URL drift")
            content = getattr(response, "content", b"")
            if not isinstance(content, (bytes, bytearray)):
                raise SangjuExperienceContractError("response body is not bytes")
            if not content or len(content) > SANGJU_EXPERIENCE_MAX_HTML_BYTES:
                raise SangjuExperienceContractError("response body size outside audited bounds")
            try:
                html = bytes(content).decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise SangjuExperienceContractError("response is not strict UTF-8") from exc
            soup = BeautifulSoup(html, "html.parser")
            _validate_owner_shell(soup, kind)
            return soup, attempt, kind
        except SangjuExperienceContractError:
            raise
        except requests.RequestException as exc:
            last_error = exc
    raise SangjuExperienceContractError(f"request failed after retries: {_clean(last_error)}")


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


def _direct_text(node: Any, *, skip: Any = None) -> str:
    values = []
    for child in getattr(node, "children", []):
        if child is skip:
            continue
        if isinstance(child, str):
            values.append(child)
    return _clean(" ".join(values))


def _label_text(node: Any) -> str:
    direct = _direct_text(node)
    return direct or _clean(node.get_text(" ", strip=True))


def _field_map(container: Any, *, direct_ul: bool = True) -> tuple[dict[str, str], tuple[str, ...]]:
    ul = None
    candidates = container.find_all("ul", recursive=not direct_ul)
    for candidate in candidates:
        if "tm_cir" in candidate.get("class", []):
            ul = candidate
            break
    if ul is None:
        raise SangjuExperienceContractError("structured tm_cir field list missing")
    result: dict[str, str] = {}
    order: list[str] = []
    for item in ul.find_all("li", recursive=False):
        label_node = item.find("span", recursive=False)
        if label_node is None:
            raise SangjuExperienceContractError("structured field label missing")
        label = _label_text(label_node)
        if label in result:
            raise SangjuExperienceContractError(f"duplicate structured field: {label}")
        value = _direct_text(item, skip=label_node)
        if not value:
            clone_text = _clean(item.get_text(" ", strip=True))
            label_all = _clean(label_node.get_text(" ", strip=True))
            value = _clean(clone_text[len(label_all) :]) if clone_text.startswith(label_all) else ""
        result[label] = value
        order.append(label)
    return result, tuple(order)


def _parse_iso_period(value: str, identity: str) -> tuple[date, Optional[date]]:
    matches = _ISO_DATE.findall(value)
    if len(matches) == 1 and "상시운영" in value:
        y, m, d = matches[0]
        return date(int(y), int(m), int(d)), None
    if len(matches) != 2:
        raise SangjuExperienceContractError(f"course {identity}: operating date shape drift")
    values = [date(int(y), int(m), int(d)) for y, m, d in matches]
    if values[0] > values[1] and identity not in _AUDITED_REVERSED_HISTORICAL_PERIOD_IDS:
        raise SangjuExperienceContractError(f"course {identity}: reversed operating period")
    return values[0], values[1]


def _parse_korean_datetimes(value: str, identity: str) -> tuple[datetime, Optional[datetime]]:
    matches = _KOREAN_DATETIME.findall(value)
    if len(matches) == 1 and "상시운영" in value:
        return datetime(*(int(part) for part in matches[0])), None
    if len(matches) != 2:
        raise SangjuExperienceContractError(f"course {identity}: reception datetime shape drift")
    values = [datetime(*(int(part) for part in match)) for match in matches]
    if values[0] > values[1]:
        raise SangjuExperienceContractError(f"course {identity}: reversed reception period")
    return values[0], values[1]


def _parse_count(value: str, identity: str, label: str) -> tuple[int, int]:
    match = _COUNT.fullmatch(_clean(value))
    if not match:
        raise SangjuExperienceContractError(f"course {identity}: {label} count shape drift")
    current, total = (int(part.replace(",", "")) for part in match.groups())
    if current < 0 or total < 0:
        raise SangjuExperienceContractError(f"course {identity}: negative {label} count")
    return current, total


def _normal_action(anchor: Any, namespace: str, identity: str) -> tuple[str, str]:
    text = _clean(anchor.get_text(" ", strip=True))
    onclick = _clean(anchor.get("onclick"))
    href = _clean(anchor.get("href"))
    classes = frozenset(anchor.get("class", []))
    if href != "javascript:;":
        raise SangjuExperienceContractError(f"course {identity}: reception control href drift")
    if text == "예약":
        pattern = _LIST_APPLY_ACTION if namespace == "reserveList" else _DETAIL_APPLY_ACTION
        match = pattern.fullmatch(onclick)
        if match is None or classes:
            raise SangjuExperienceContractError(f"course {identity}: active reservation control drift")
        return text, match.group(1)
    if text == "대기":
        if onclick != "alert('접수 대기 중입니다.');" or classes != {"bg_gray"}:
            raise SangjuExperienceContractError(f"course {identity}: waiting control drift")
        return text, ""
    if text == "종료":
        if onclick not in {
            "alert('접수가 종료되었습니다.')",
            "alert('접수가 종료되었습니다.');",
        } or classes != {"bg_dark"}:
            raise SangjuExperienceContractError(f"course {identity}: ended control drift")
        return text, ""
    raise SangjuExperienceContractError(f"course {identity}: unknown reception control {text}")


def _parse_reception_block(block: Any, namespace: str, identity: str) -> dict[str, Any]:
    fields, order = _field_map(block)
    anchors = block.find_all("a", recursive=False)
    heading_node = block.find("h2", recursive=False)
    if order == ("[예약불가]",):
        if heading_node is not None or anchors or fields["[예약불가]"] != "등록된 접수 정보가 없습니다.":
            raise SangjuExperienceContractError(f"course {identity}: reservation-unavailable drift")
        return {
            "heading": "",
            "status": "예약불가",
            "rcpt_no": "",
            "apply_start": None,
            "apply_end": None,
            "capacity_current": None,
            "capacity_total": None,
            "waitlist_current": None,
            "waitlist_total": None,
        }
    if order not in _RECEPTION_FIELD_SHAPES or heading_node is None or len(anchors) != 1:
        raise SangjuExperienceContractError(f"course {identity}: reception block field/control drift")
    heading = _clean(heading_node.get_text(" ", strip=True))
    if not heading:
        raise SangjuExperienceContractError(f"course {identity}: empty reception heading")
    apply_start, apply_end = _parse_korean_datetimes(fields["접수기간"], identity)
    capacity_current = capacity_total = None
    waitlist_current = waitlist_total = None
    if "정원" in fields:
        capacity_current, capacity_total = _parse_count(fields["정원"], identity, "capacity")
    if "후보" in fields:
        waitlist_current, waitlist_total = _parse_count(fields["후보"], identity, "waitlist")
    status, rcpt_no = _normal_action(anchors[0], namespace, identity)
    return {
        "heading": heading,
        "status": status,
        "rcpt_no": rcpt_no,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_current": waitlist_current,
        "waitlist_total": waitlist_total,
    }


def _direct_list_sub(container: Any) -> Any:
    for child in container.find_all("ul", recursive=False):
        if "list_sub" in child.get("class", []):
            return child
    return None


def _parse_receptions(container: Any, namespace: str, identity: str) -> list[dict[str, Any]]:
    list_sub = _direct_list_sub(container)
    if list_sub is None:
        return []
    blocks = list_sub.find_all("li", recursive=False)
    if not blocks:
        raise SangjuExperienceContractError(f"course {identity}: empty reception container")
    return [_parse_reception_block(block, namespace, identity) for block in blocks]


def _reception_signature(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        item["heading"],
        item["status"],
        item["rcpt_no"],
        item["apply_start"],
        item["apply_end"],
        item["capacity_current"],
        item["capacity_total"],
        item["waitlist_current"],
        item["waitlist_total"],
    )


def _aggregate_status(
    badge: str,
    receptions: list[dict[str, Any]],
    identity: str,
    *,
    audited_unavailable_badge: bool = False,
) -> str:
    statuses = {str(item["status"]) for item in receptions}
    if not statuses:
        if badge == "정보제공":
            return "정보제공"
        if badge:
            raise SangjuExperienceContractError(f"course {identity}: unknown top badge {badge}")
        return "정보없음"
    if statuses == {"예약불가"}:
        expected_badge = "온라인예약 접수중" if audited_unavailable_badge else ""
        if badge != expected_badge:
            raise SangjuExperienceContractError(f"course {identity}: unavailable/badge disagreement")
        return "예약불가"
    if len(statuses) != 1 or statuses - {"예약", "대기", "종료"}:
        raise SangjuExperienceContractError(f"course {identity}: mixed reception statuses")
    status = next(iter(statuses))
    expected_badge = "온라인예약 접수중" if status == "예약" else ""
    if badge != expected_badge:
        raise SangjuExperienceContractError(f"course {identity}: list badge/control disagreement")
    return status


def _parse_card(section: Any, requested_page: int, position: int) -> dict[str, Any]:
    right = section.select_one("div.flex > div.right")
    if right is None:
        raise SangjuExperienceContractError("course card right panel missing")
    heading = right.find("h1", recursive=False)
    if heading is None:
        raise SangjuExperienceContractError("course card heading missing")
    ordinal_node = heading.find("em", recursive=False)
    title_anchor = heading.find("a", recursive=False)
    if ordinal_node is None or title_anchor is None:
        raise SangjuExperienceContractError("course card ordinal/title control missing")
    ordinal_text = _clean(ordinal_node.get_text(" ", strip=True))
    if not _ORDINAL.fullmatch(ordinal_text):
        raise SangjuExperienceContractError("course card ordinal drift")
    title = _clean(title_anchor.get_text(" ", strip=True))
    if not title:
        raise SangjuExperienceContractError("empty course title")
    actions = []
    for anchor in right.find_all("a"):
        onclick = _clean(anchor.get("onclick"))
        match = _LIST_DETAIL_ACTION.fullmatch(onclick)
        if match:
            actions.append(match.group(1))
    if len(actions) != 2 or len(set(actions)) != 1:
        raise SangjuExperienceContractError("course list/detail identity controls drift")
    identity = actions[0]
    fields, order = _field_map(right)
    if order != _LIST_FIELD_LABELS or any(not fields[label] for label in _LIST_FIELD_LABELS):
        raise SangjuExperienceContractError(f"course {identity}: list field contract drift")
    if fields["분류"] != "체험/견학":
        raise SangjuExperienceContractError(f"course {identity}: escaped experience classification")
    if fields["시설명"] not in SANGJU_EXPERIENCE_FACILITY_BY_NAME:
        raise SangjuExperienceContractError(f"course {identity}: unknown facility")
    event_start, event_end = _parse_iso_period(fields["운영기간"], identity)
    badge_node = right.select_one(":scope > div.top > span")
    badge = _clean(badge_node.get_text(" ", strip=True)) if badge_node else ""
    receptions = _parse_receptions(section, "reserveList", identity)
    audited_unavailable_badge = (
        _AUDITED_UNAVAILABLE_BADGE_ROWS.get(identity)
        == (
            title,
            fields["시설명"],
            fields["운영기간"],
            fields["주소"],
        )
        and len(receptions) == 1
        and receptions[0]
        == {
            "heading": "",
            "status": "예약불가",
            "rcpt_no": "",
            "apply_start": None,
            "apply_end": None,
            "capacity_current": None,
            "capacity_total": None,
            "waitlist_current": None,
            "waitlist_total": None,
        }
    )
    raw_status = _aggregate_status(
        badge,
        receptions,
        identity,
        audited_unavailable_badge=audited_unavailable_badge,
    )
    return {
        "identity": identity,
        "ordinal": int(ordinal_text),
        "page": requested_page,
        "position": position,
        "title": title,
        "source_category": fields["분류"],
        "facility_name": fields["시설명"],
        "address": fields["주소"],
        "event_start": event_start,
        "event_end": event_end,
        "standing_operation": event_end is None,
        "list_period": fields["운영기간"],
        "badge": badge,
        "raw_status": raw_status,
        "receptions": receptions,
        "detail_url": _detail_url(identity),
    }


def _named_hidden(form: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in form.select('input[type="hidden"][name]'):
        name = _clean(node.get("name"))
        if not name or name in result:
            raise SangjuExperienceContractError("hidden form field missing/duplicated")
        result[name] = str(node.get("value", ""))
    return result


def _validate_facility_registry(soup: BeautifulSoup, selected_code: str) -> None:
    buttons = soup.select("ul.com_tab.com_tab2 button")
    expected = [("", "전체"), *((item.code, item.name) for item in SANGJU_EXPERIENCE_FACILITIES)]
    actual: list[tuple[str, str]] = []
    active: list[str] = []
    for button in buttons:
        match = _FACILITY_ACTION.fullmatch(_clean(button.get("onclick")))
        if match is None or _clean(button.get("type")) != "button":
            raise SangjuExperienceContractError("facility navigation action drift")
        code = match.group(1)
        actual.append((code, _clean(button.get_text(" ", strip=True))))
        parent = button.parent
        if parent is not None and "active" in parent.get("class", []):
            active.append(code)
    if actual != expected or active != [selected_code]:
        raise SangjuExperienceContractError("facility registry/order/selection drift")


def _parse_pager(soup: BeautifulSoup, requested_page: int) -> tuple[int, Optional[int]]:
    pager = soup.select_one("ul.pager")
    if pager is None:
        raise SangjuExperienceContractError("pager missing")
    last_anchor = pager.select_one("a.pager_next_all")
    if last_anchor is None:
        raise SangjuExperienceContractError("pager last control missing")
    match = _PAGE_ACTION.fullmatch(_clean(last_anchor.get("onclick")))
    if match is None:
        raise SangjuExperienceContractError("pager last action drift")
    advertised_last = int(match.group(1))
    href_pairs = parse_qsl(urlparse(_clean(last_anchor.get("href"))).query, keep_blank_values=True)
    if href_pairs != [("pageIndex", str(advertised_last))]:
        raise SangjuExperienceContractError("pager last href drift")
    active_nodes = pager.select("a.active")
    if len(active_nodes) > 1:
        raise SangjuExperienceContractError("multiple active pager entries")
    current = None
    if active_nodes:
        value = _clean(active_nodes[0].get_text(" ", strip=True))
        if not _IDENTITY.fullmatch(value):
            raise SangjuExperienceContractError("active pager value drift")
        current = int(value)
    if current is not None and current != requested_page:
        raise SangjuExperienceContractError("active pager/request disagreement")
    return advertised_last, current


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    requested_page: int,
    facility_code: str,
) -> dict[str, Any]:
    form = soup.select_one("form#reserveListForm")
    if form is None or form.get("action") is not None or form.get("method") is not None:
        raise SangjuExperienceContractError("list form contract drift")
    expected_hidden = {
        "pageNo": SANGJU_EXPERIENCE_PAGE_NO,
        "mn": SANGJU_EXPERIENCE_MENU_NO,
        "pageIndex": str(requested_page),
        "searchTrgtClsfCd": SANGJU_EXPERIENCE_CLASS_CODE,
        "searchFcltNo": facility_code,
        "cyclNo": "",
        "rcptNo": "",
    }
    if _named_hidden(form) != expected_hidden:
        raise SangjuExperienceContractError("list hidden form binding drift")
    _validate_facility_registry(soup, facility_code)
    advertised_last, current_page = _parse_pager(soup, requested_page)
    ledger = soup.select_one("#reserveList.list3")
    if ledger is None:
        raise SangjuExperienceContractError("reserveList ledger missing")
    sections = ledger.find_all("section", recursive=False)
    if not sections:
        raise SangjuExperienceContractError("reserveList sections missing")
    empty = False
    if len(sections) == 1 and sections[0].select_one(":scope > p.no_data") is not None:
        empty_node = sections[0].select_one(":scope > p.no_data")
        if _clean(empty_node.get_text(" ", strip=True)) != "자료가 없습니다.":
            raise SangjuExperienceContractError("empty sentinel text drift")
        if sections[0].find("h1") is not None:
            raise SangjuExperienceContractError("empty sentinel contains course data")
        rows: list[dict[str, Any]] = []
        empty = True
    else:
        if any(section.select_one(":scope > p.no_data") for section in sections):
            raise SangjuExperienceContractError("mixed data/empty page")
        rows = [_parse_card(section, requested_page, position) for position, section in enumerate(sections, 1)]
    if empty and current_page is None and requested_page <= advertised_last:
        raise SangjuExperienceContractError("advertised data page lost active pager")
    if rows and current_page != requested_page:
        raise SangjuExperienceContractError("data page lacks matching active pager")
    return {
        "requested_page": requested_page,
        "facility_code": facility_code,
        "advertised_last": advertised_last,
        "current_page": current_page,
        "empty": empty,
        "rows": rows,
    }


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row["identity"],
        row["title"],
        row["source_category"],
        row["facility_name"],
        row["address"],
        row["event_start"],
        row["event_end"],
        row["badge"],
        row["raw_status"],
        tuple(_reception_signature(item) for item in row["receptions"]),
    )


def _page_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        page["requested_page"],
        page["facility_code"],
        page["advertised_last"],
        page["current_page"],
        page["empty"],
        tuple((row["ordinal"], _row_signature(row)) for row in page["rows"]),
    )


def _collect_advertised_pages(
    fetch_page: Callable[[int, str], dict[str, Any]],
    *,
    facility_code: str,
    max_pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    first = fetch_page(1, facility_code)
    last = int(first["advertised_last"])
    if last < 1 or last > max_pages:
        raise SangjuExperienceContractError(f"source cap: advertised last page {last} exceeds max_pages {max_pages}")
    pages = [first]
    for number in range(2, last + 1):
        pages.append(fetch_page(number, facility_code))
    total = int(first["rows"][0]["ordinal"]) if first["rows"] else 0
    expected_last = max(1, (total + SANGJU_EXPERIENCE_PAGE_SIZE - 1) // SANGJU_EXPERIENCE_PAGE_SIZE)
    if last != expected_last:
        raise SangjuExperienceContractError("ordinal total/pager last disagreement")
    for index, page in enumerate(pages, 1):
        if page["requested_page"] != index or page["advertised_last"] != last:
            raise SangjuExperienceContractError("pagination metadata drift")
        expected_size = (
            0 if total == 0 else min(SANGJU_EXPERIENCE_PAGE_SIZE, total - SANGJU_EXPERIENCE_PAGE_SIZE * (index - 1))
        )
        if len(page["rows"]) != expected_size or bool(page["empty"]) != (expected_size == 0):
            raise SangjuExperienceContractError("page-size/empty boundary drift")
        expected_ordinals = list(
            range(
                total - SANGJU_EXPERIENCE_PAGE_SIZE * (index - 1),
                total - SANGJU_EXPERIENCE_PAGE_SIZE * (index - 1) - expected_size,
                -1,
            )
        )
        if [row["ordinal"] for row in page["rows"]] != expected_ordinals:
            raise SangjuExperienceContractError("global course ordinal sequence drift")
    rows = [row for page in pages for row in page["rows"]]
    identities = [str(row["identity"]) for row in rows]
    if len(rows) != total or len(identities) != len(set(identities)):
        raise SangjuExperienceContractError("ledger total/identity cardinality drift")
    return rows, pages


def _detail_field_map(root: Any) -> tuple[dict[str, str], tuple[str, ...]]:
    return _field_map(root)


def _parse_detail(soup: BeautifulSoup, expected_row: Mapping[str, Any]) -> dict[str, Any]:
    identity = str(expected_row["identity"])
    form = soup.select_one("form#reserveDetailForm")
    if form is None:
        raise SangjuExperienceContractError(f"course {identity}: detail form missing")
    expected_hidden = {
        "pageNo": SANGJU_EXPERIENCE_PAGE_NO,
        "mn": SANGJU_EXPERIENCE_MENU_NO,
        "pageIndex": "",
        "searchTrgtClsfCd": SANGJU_EXPERIENCE_CLASS_CODE,
        "searchFcltNo": "",
        "cyclNo": identity,
        "rcptNo": "",
    }
    if _named_hidden(form) != expected_hidden:
        raise SangjuExperienceContractError(f"course {identity}: detail identity binding drift")
    root = form.select_one(".img_jb > .right")
    if root is None:
        raise SangjuExperienceContractError(f"course {identity}: detail header missing")
    title_node = root.find("h1", recursive=False)
    state_node = root.select_one(":scope > .top > span")
    if title_node is None or state_node is None:
        raise SangjuExperienceContractError(f"course {identity}: detail title/state missing")
    title = _clean(title_node.get_text(" ", strip=True))
    state = _clean(state_node.get_text(" ", strip=True))
    fields, order = _detail_field_map(root)
    labels = frozenset(order)
    if (
        not _DETAIL_REQUIRED_LABELS <= labels
        or labels - _DETAIL_REQUIRED_LABELS - _DETAIL_OPTIONAL_DISCARDED_LABELS
        or len(order) != len(labels)
    ):
        raise SangjuExperienceContractError(f"course {identity}: detail field set drift")
    event_start, event_end = _parse_korean_datetimes(fields["운영기간"], identity)
    if (
        title != expected_row["title"]
        or fields["시설명"] != expected_row["facility_name"]
        or fields["주소"] != expected_row["address"]
        or not expected_row["source_category"].startswith(fields["분류"])
        or event_start.date() != expected_row["event_start"]
        or event_end.date() != expected_row["event_end"]
    ):
        raise SangjuExperienceContractError(f"course {identity}: list/detail structured data drift")
    expected_state = _DETAIL_STATE_BY_SOURCE.get(str(expected_row["raw_status"]))
    if expected_state is None or state != expected_state:
        raise SangjuExperienceContractError(f"course {identity}: list/detail state drift")
    motion = form.select_one(".motion_wrap")
    if motion is None:
        if expected_row["receptions"]:
            raise SangjuExperienceContractError(f"course {identity}: detail reception area missing")
        receptions: list[dict[str, Any]] = []
    else:
        receptions = _parse_receptions(motion, "reserveDetail", identity)
    if tuple(map(_reception_signature, receptions)) != tuple(map(_reception_signature, expected_row["receptions"])):
        raise SangjuExperienceContractError(f"course {identity}: list/detail reception drift")
    back_controls = [
        anchor for anchor in form.select(".bot_btn a") if _clean(anchor.get("onclick")) == "reserveDetail.list();"
    ]
    if len(back_controls) != 1:
        raise SangjuExperienceContractError(f"course {identity}: detail return control drift")
    panels = form.select(".tabpanel_wrap .bd_scroll")
    if len(panels) != 3:
        raise SangjuExperienceContractError(f"course {identity}: free-text panel boundary drift")
    return {
        "identity": identity,
        "state": state,
        "event_start_at": event_start,
        "event_end_at": event_end,
        "operating_schedule": _clean(fields["운영기간"]),
        "receptions": receptions,
        "attachment_count": len(form.select('a[href*="/file/readFile.tc"]')),
        "image_count": len(form.select('img[src*="/file/readFile.tc"]')),
        "discarded_instructor": "강사" in fields,
        "discarded_panel_count": len(panels),
    }


def _current_semantics(row: Mapping[str, Any]) -> None:
    identity = str(row["identity"])
    status = str(row["raw_status"])
    receptions = list(row["receptions"])
    if status not in _DETAIL_STATE_BY_SOURCE:
        raise SangjuExperienceContractError(f"course {identity}: unsupported current status {status}")
    if status == "정보제공":
        if receptions or row["badge"] != "정보제공":
            raise SangjuExperienceContractError(f"course {identity}: information-only control drift")
        return
    if len(receptions) != 1 or receptions[0]["status"] != status:
        raise SangjuExperienceContractError(f"course {identity}: current reception cardinality drift")
    if status == "예약" and not receptions[0]["rcpt_no"]:
        raise SangjuExperienceContractError(f"course {identity}: active rcptNo missing")
    if status != "예약" and receptions[0]["rcpt_no"]:
        raise SangjuExperienceContractError(f"course {identity}: inactive rcptNo present")


def _iso_minute(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else ""


def _output_row(row: Mapping[str, Any]) -> dict[str, Any]:
    _current_semantics(row)
    detail = row["detail"]
    facility = SANGJU_EXPERIENCE_FACILITY_BY_NAME[str(row["facility_name"])]
    receptions = list(row["receptions"])
    reception = receptions[0] if receptions else None
    identity = str(row["identity"])
    active = str(row["raw_status"]) == "예약"
    rcpt_no = str(reception["rcpt_no"]) if reception else ""
    apply_start = reception["apply_start"] if reception else None
    apply_end = reception["apply_end"] if reception else None
    apply_period = f"{_iso_minute(apply_start)} ~ {_iso_minute(apply_end)}" if apply_start else ""
    event_start: date = row["event_start"]
    event_end: date = row["event_end"]
    period = f"{event_start.isoformat()} ~ {event_end.isoformat()}"
    capacity_current = reception["capacity_current"] if reception else None
    capacity_total = reception["capacity_total"] if reception else None
    wait_current = reception["waitlist_current"] if reception else None
    wait_total = reception["waitlist_total"] if reception else None
    return {
        "provider": SANGJU_EXPERIENCE_PROVIDER,
        "provider_course_id": f"{SANGJU_EXPERIENCE_PROVIDER}:cycl:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": str(row["title"]),
        "description": str(row["title"]),
        "branch": facility.name,
        "branch_code": facility.code,
        "branch_url": facility.url,
        "preserve_branch": True,
        "category": "체험/견학",
        "program_type": "체험",
        "raw_url": str(row["detail_url"]),
        "application_url": _application_url(identity, rcpt_no) if active else "",
        "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
        "application_method": "온라인" if reception else "",
        "application_methods": ["온라인"] if reception else [],
        "reservation_available": active,
        "status": _STATUS_MAP[str(row["raw_status"])],
        "raw_status": str(row["raw_status"]),
        "fee": "",
        "fee_amount": None,
        "material_fee": "",
        "material_fee_amount": None,
        "period": period,
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": apply_period,
        "apply_start_date": apply_start.date().isoformat() if apply_start else "",
        "apply_end_date": apply_end.date().isoformat() if apply_end else "",
        "apply_start_at": _iso_minute(apply_start),
        "apply_end_at": _iso_minute(apply_end),
        "schedule_raw": str(detail["operating_schedule"]),
        "capacity": f"{capacity_total}명" if capacity_total is not None else "",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_remaining": (
            max(int(capacity_total) - int(capacity_current), 0)
            if capacity_total is not None and capacity_current is not None
            else None
        ),
        "waitlist_current": wait_current,
        "waitlist_total": wait_total,
        "target": "",
        "venue": facility.name,
        "venue_name": facility.name,
        "room": "",
        "facility_name": facility.name,
        "address": str(row["address"]),
        "venue_address": str(row["address"]),
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "operator_type": "지자체/공공기관",
        "source_group": "public_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "collection_type": SANGJU_EXPERIENCE_PARSER,
        "municipality_code": SANGJU_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_full_name": SANGJU_EXPERIENCE_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": int(row["page"]),
            "source_position": int(row["position"]),
            "source_ordinal": int(row["ordinal"]),
            "source_category": str(row["source_category"]),
            "source_facility_code": facility.code,
            "source_facility_name": facility.name,
            "source_address": str(row["address"]),
            "source_status": str(row["raw_status"]),
            "source_experience_period": period,
            "source_operating_schedule": str(detail["operating_schedule"]),
            "source_reception_heading": str(reception["heading"]) if reception else "",
            "source_apply_period": apply_period,
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "source_waitlist_current": wait_current,
            "source_waitlist_total": wait_total,
            "source_rcpt_no": rcpt_no,
            "list_identity_verified": True,
            "facility_partition_verified": True,
            "detail_identity_verified": True,
            "detail_structured_fields_verified": True,
            "detail_reception_verified": True,
            "application_control_present": active,
            "application_control_verified": True,
            "application_endpoint_fetched": False,
            "application_form_submitted": False,
            "attachment_endpoint_fetched": False,
            "free_text_persisted": False,
            "discarded_fields": list(SANGJU_EXPERIENCE_FIELDS_NEVER_PERSISTED),
            "service_family": "experience",
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
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "branch_url", "raw_fields"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload) or _RESIDENT_ID.search(payload):
        errors.append("PII-like value escaped structured allowlist")
    return errors


def _initial_meta() -> dict[str, Any]:
    return {
        "provider": SANGJU_EXPERIENCE_PROVIDER,
        "provider_decision": (
            "retain one official provider for the complete integrated experience "
            "ledger and keep the education owner unchanged"
        ),
        "canonical_url": SANGJU_EXPERIENCE_CANONICAL_URL,
        "canonical_url_sha1": SANGJU_EXPERIENCE_CANONICAL_URL_SHA1,
        "canonical_url_sha256": SANGJU_EXPERIENCE_CANONICAL_URL_SHA256,
        "parser": SANGJU_EXPERIENCE_PARSER,
        "ownership_scope": SANGJU_EXPERIENCE_OWNERSHIP_SCOPE,
        "municipality_code": SANGJU_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_full_name": SANGJU_EXPERIENCE_MUNICIPALITY_NAME,
        "page_size": SANGJU_EXPERIENCE_PAGE_SIZE,
        "recommended_max_pages": SANGJU_EXPERIENCE_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": SANGJU_EXPERIENCE_RECOMMENDED_DETAIL_LIMIT,
        "source_requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "request_attempts": 0,
        "application_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "application_form_submissions": 0,
        "data_pages": 0,
        "facility_filter_requests": 0,
        "full_recheck_requests": 0,
        "source_total_count": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "standing_source_count": 0,
        "row_count": 0,
        "detail_pages": 0,
        "facility_partition_union_count": 0,
        "facility_partition_overlap_count": 0,
        "application_control_count": 0,
        "attachment_links_discarded": 0,
        "images_discarded": 0,
        "instructor_fields_discarded": 0,
        "free_text_panels_discarded": 0,
        "pagination_complete": False,
        "post_last_empty_verified": False,
        "facility_partition_complete": False,
        "details_complete": False,
        "stable_boundary_recheck": False,
        "privacy_boundary_complete": False,
        "semantic_quality_passed": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
    }


def collect_sangju_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = SANGJU_EXPERIENCE_RECOMMENDED_MAX_PAGES,
    detail_limit: int = SANGJU_EXPERIENCE_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, stable, privacy-safe Sangju experience snapshot."""

    meta = _initial_meta()
    if not is_sangju_experience_target(target):
        meta["configured_collection_error"] = "target does not match exact official Sangju experience owner"
        return [], SANGJU_EXPERIENCE_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], SANGJU_EXPERIENCE_PARSER, meta
        session_factory = _raw_session
    try:
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ValueError("timeout must be a positive integer")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")
        if isinstance(detail_limit, bool) or not isinstance(detail_limit, int) or detail_limit < 0:
            raise ValueError("detail_limit must be a non-negative integer")
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], SANGJU_EXPERIENCE_PARSER, meta

    current_fetcher = fetcher or _request
    main_session = session_factory()

    def fetch_page(page: int, facility_code: str) -> dict[str, Any]:
        soup, attempts, _ = _fetch_soup(main_session, _list_url(page, facility_code), timeout, current_fetcher)
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        meta["request_attempts"] += attempts
        return _parse_list_page(soup, requested_page=page, facility_code=facility_code)

    def fetch_one_detail(row: Mapping[str, Any], session: Any) -> tuple[dict[str, Any], int]:
        soup, attempts, _ = _fetch_soup(session, str(row["detail_url"]), timeout, current_fetcher)
        return _parse_detail(soup, row), attempts

    try:
        listed, initial_pages = _collect_advertised_pages(fetch_page, facility_code="", max_pages=max_pages)
        initial_last = len(initial_pages)
        sentinel = fetch_page(initial_last + 1, "")
        if (
            not sentinel["empty"]
            or sentinel["rows"]
            or sentinel["current_page"] is not None
            or sentinel["advertised_last"] != initial_last
        ):
            raise SangjuExperienceContractError("post-last page is not the exact empty sentinel")
        source_by_id = {str(row["identity"]): row for row in listed}
        if len(source_by_id) != len(listed):
            raise SangjuExperienceContractError("duplicate source identity")

        facility_request_start = int(meta["list_requests"])
        memberships: dict[str, str] = {}
        facility_rows: dict[str, list[dict[str, Any]]] = {}
        facility_pages: dict[str, int] = {}
        overlap_count = 0
        for facility in SANGJU_EXPERIENCE_FACILITIES:
            filtered, pages = _collect_advertised_pages(fetch_page, facility_code=facility.code, max_pages=max_pages)
            facility_rows[facility.code] = filtered
            facility_pages[facility.code] = len(pages)
            for filtered_row in filtered:
                identity = str(filtered_row["identity"])
                if identity in memberships:
                    overlap_count += 1
                    raise SangjuExperienceContractError(f"facility partitions overlap at {identity}")
                source_row = source_by_id.get(identity)
                if source_row is None:
                    raise SangjuExperienceContractError(f"facility partition escapes source at {identity}")
                if _row_signature(filtered_row) != _row_signature(source_row):
                    raise SangjuExperienceContractError(f"facility partition data drift at {identity}")
                if filtered_row["facility_name"] != facility.name:
                    raise SangjuExperienceContractError(f"facility filter/name disagreement at {identity}")
                memberships[identity] = facility.code
        if set(memberships) != set(source_by_id):
            missing = sorted(set(source_by_id) - set(memberships))
            raise SangjuExperienceContractError("facility partition union incomplete: " + ", ".join(missing[:5]))
        for row in listed:
            row["facility_code"] = memberships[str(row["identity"])]

        active_rcpt: dict[str, str] = {}
        for row in listed:
            for reception in row["receptions"]:
                rcpt_no = str(reception["rcpt_no"])
                if not rcpt_no:
                    continue
                if rcpt_no in active_rcpt:
                    raise SangjuExperienceContractError("rcptNo reused across course identities")
                active_rcpt[rcpt_no] = str(row["identity"])

        current_rows = [row for row in listed if row["event_end"] is not None and row["event_end"] >= cutoff]
        standing_rows = [row for row in listed if row["event_end"] is None]
        if len(current_rows) > detail_limit:
            raise SangjuExperienceContractError(
                f"source cap: {len(current_rows)} current details exceed detail_limit {detail_limit}"
            )
        for row in current_rows:
            _current_semantics(row)

        detail_results: dict[str, dict[str, Any]] = {}
        detail_attempts = 0
        if current_rows and fetcher is None and len(current_rows) > 1:
            chunks = [
                current_rows[index::SANGJU_EXPERIENCE_MAX_WORKERS] for index in range(SANGJU_EXPERIENCE_MAX_WORKERS)
            ]

            def worker(chunk: list[dict[str, Any]]) -> tuple[list[tuple[str, dict[str, Any]]], int]:
                worker_session = session_factory()
                result: list[tuple[str, dict[str, Any]]] = []
                attempts_total = 0
                try:
                    for item in chunk:
                        parsed, attempts = fetch_one_detail(item, worker_session)
                        result.append((str(item["identity"]), parsed))
                        attempts_total += attempts
                    return result, attempts_total
                finally:
                    close = getattr(worker_session, "close", None)
                    if callable(close):
                        close()

            with ThreadPoolExecutor(max_workers=SANGJU_EXPERIENCE_MAX_WORKERS) as executor:
                futures = [executor.submit(worker, chunk) for chunk in chunks if chunk]
                for future in as_completed(futures):
                    parsed_batch, attempts = future.result()
                    detail_attempts += attempts
                    for identity, parsed in parsed_batch:
                        if identity in detail_results:
                            raise SangjuExperienceContractError("duplicate detail result identity")
                        detail_results[identity] = parsed
        else:
            for row in current_rows:
                parsed, attempts = fetch_one_detail(row, main_session)
                detail_results[str(row["identity"])] = parsed
                detail_attempts += attempts
        if set(detail_results) != {str(row["identity"]) for row in current_rows}:
            raise SangjuExperienceContractError("current detail result union incomplete")
        meta["source_requests"] += len(current_rows)
        meta["detail_requests"] += len(current_rows)
        meta["request_attempts"] += detail_attempts
        for row in current_rows:
            row["detail"] = detail_results[str(row["identity"])]

        recheck_request_start = int(meta["list_requests"])
        first_recheck = fetch_page(1, "")
        last_recheck = fetch_page(initial_last, "")
        sentinel_recheck = fetch_page(initial_last + 1, "")
        if (
            _page_signature(first_recheck) != _page_signature(initial_pages[0])
            or _page_signature(last_recheck) != _page_signature(initial_pages[-1])
            or _page_signature(sentinel_recheck) != _page_signature(sentinel)
        ):
            raise SangjuExperienceContractError("source boundaries changed during detail collection")

        rows = [_output_row(row) for row in current_rows]
        privacy_failures = [error for row in rows for error in _privacy_errors(row)]
        if privacy_failures:
            raise SangjuExperienceContractError("; ".join(sorted(set(privacy_failures))))
        before_ids = {str(row["provider_course_id"]) for row in rows}
        if dedupe_rows is not None:
            rows = [dict(row) for row in dedupe_rows(rows)]
        after_ids = [str(row.get("provider_course_id", "")) for row in rows]
        if len(after_ids) != len(set(after_ids)) or set(after_ids) != before_ids:
            raise SangjuExperienceContractError("dedupe_rows changed complete identity cardinality")
        privacy_failures = [error for row in rows for error in _privacy_errors(row)]
        if privacy_failures:
            raise SangjuExperienceContractError("; ".join(sorted(set(privacy_failures))))

        source_actions = Counter(str(row["raw_status"]) for row in listed)
        current_actions = Counter(str(row["raw_status"]) for row in current_rows)
        output_statuses = Counter(str(row["status"]) for row in rows)
        current_facilities = Counter(str(row["facility_code"]) for row in current_rows)
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "data_pages": len(initial_pages),
                "advertised_last_page": initial_last,
                "post_last_page": initial_last + 1,
                "page_sizes": [len(page["rows"]) for page in initial_pages],
                "source_total_count": len(listed),
                "current_source_count": len(current_rows),
                "expired_source_count": len(listed) - len(current_rows) - len(standing_rows),
                "standing_source_count": len(standing_rows),
                "row_count": len(rows),
                "detail_pages": len(detail_results),
                "identity_first": str(listed[0]["identity"]) if listed else "",
                "identity_last": str(listed[-1]["identity"]) if listed else "",
                "current_ids": [str(row["identity"]) for row in current_rows],
                "facility_filter_requests": recheck_request_start - facility_request_start,
                "full_recheck_requests": int(meta["list_requests"]) - recheck_request_start,
                "facility_partition_counts": {
                    facility.code: len(facility_rows[facility.code]) for facility in SANGJU_EXPERIENCE_FACILITIES
                },
                "facility_partition_pages": facility_pages,
                "facility_partition_union_count": len(memberships),
                "facility_partition_overlap_count": overlap_count,
                "empty_facility_filter_count": sum(
                    not facility_rows[facility.code] for facility in SANGJU_EXPERIENCE_FACILITIES
                ),
                "current_facility_counts": dict(current_facilities),
                "source_raw_status_counts": dict(source_actions),
                "current_raw_status_counts": dict(current_actions),
                "status_counts": dict(output_statuses),
                "source_application_control_count": len(active_rcpt),
                "application_control_count": sum(row["reservation_available"] for row in rows),
                "attachment_links_discarded": sum(int(item["attachment_count"]) for item in detail_results.values()),
                "images_discarded": sum(int(item["image_count"]) for item in detail_results.values()),
                "instructor_fields_discarded": sum(
                    bool(item["discarded_instructor"]) for item in detail_results.values()
                ),
                "free_text_panels_discarded": sum(
                    int(item["discarded_panel_count"]) for item in detail_results.values()
                ),
                "pagination_complete": True,
                "post_last_empty_verified": True,
                "facility_partition_complete": True,
                "details_complete": True,
                "stable_boundary_recheck": True,
                "privacy_boundary_complete": True,
                "semantic_quality_passed": True,
                "snapshot_complete": True,
                "no_current_data": not rows,
                "configured_collection_error": "",
            }
        )
        return rows, SANGJU_EXPERIENCE_PARSER, meta
    except Exception as exc:
        if "source cap:" in _clean(exc):
            meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["snapshot_complete"] = False
        meta["semantic_quality_passed"] = False
        return [], SANGJU_EXPERIENCE_PARSER, meta
    finally:
        close = getattr(main_session, "close", None)
        if callable(close):
            close()


collect = collect_sangju_experience


__all__ = [
    "SANGJU_EXPERIENCE_APPLICATION_PATH",
    "SANGJU_EXPERIENCE_CANONICAL_URL",
    "SANGJU_EXPERIENCE_CANONICAL_URL_SHA1",
    "SANGJU_EXPERIENCE_CANONICAL_URL_SHA256",
    "SANGJU_EXPERIENCE_CLASS_CODE",
    "SANGJU_EXPERIENCE_DETAIL_PATH",
    "SANGJU_EXPERIENCE_FACILITIES",
    "SANGJU_EXPERIENCE_FIELDS_NEVER_PERSISTED",
    "SANGJU_EXPERIENCE_LIST_PATH",
    "SANGJU_EXPERIENCE_LIVE_AUDIT_BASELINE",
    "SANGJU_EXPERIENCE_MENU_NO",
    "SANGJU_EXPERIENCE_MUNICIPALITY_CODE",
    "SANGJU_EXPERIENCE_MUNICIPALITY_NAME",
    "SANGJU_EXPERIENCE_OWNER_BOUNDARIES",
    "SANGJU_EXPERIENCE_OWNERSHIP_SCOPE",
    "SANGJU_EXPERIENCE_PAGE_NO",
    "SANGJU_EXPERIENCE_PARSER",
    "SANGJU_EXPERIENCE_PROVIDER",
    "SANGJU_EXPERIENCE_RECOMMENDED_DETAIL_LIMIT",
    "SANGJU_EXPERIENCE_RECOMMENDED_MAX_PAGES",
    "SangjuExperienceContractError",
    "collect",
    "collect_sangju_experience",
    "is_sangju_experience_target",
    "is_target",
]
