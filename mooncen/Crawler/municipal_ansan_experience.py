"""Exact, fail-closed collector for Ansan's official experience ledger.

The Ansan integrated-reservation site owns three public catalogues: indoor
experience, outdoor experience, and visits.  This collector proves the full
historical identity set from the advertised totals and an exact empty
post-last sentinel, but returns only programmes whose official activity period
has not ended.  Every returned row is checked against its public detail page.

Only canonical list and identity-bound public detail GET requests are allowed.
The detail page contains inert calendar/application/attachment controls; none
of those endpoints is called.  District ownership is accepted only when the
public detail address explicitly says ``상록구`` or ``단원구``.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


ANSAN_EXPERIENCE_PROVIDER = "MUNI_RESERVE_ANSAN_GO_KR_02253999"
ANSAN_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_452897AE0425"
ANSAN_EXPERIENCE_URL = (
    "https://reserve.ansan.go.kr/exp/X01/expList.do?currentMenuNo=667"
)
ANSAN_EXPERIENCE_HOST = "reserve.ansan.go.kr"
ANSAN_EXPERIENCE_PAGE_SIZE = 10
ANSAN_EXPERIENCE_MAX_HTML_BYTES = 1_000_000
ANSAN_EXPERIENCE_RECOMMENDED_MAX_PAGES = 30
ANSAN_EXPERIENCE_RECOMMENDED_DETAIL_LIMIT = 100
ANSAN_EXPERIENCE_MAX_WORKERS = 12
ANSAN_EXPERIENCE_PARSER = (
    "ansan_three_official_experience_catalogues+advertised_totals+"
    "complete_ten_item_pages+exact_empty_post_last_sentinels+"
    "stable_first_last_sentinels+all_current_public_details+"
    "list_detail_identity_and_field_binding+explicit_detail_address_districts+"
    "identity_bound_inert_application_controls_no_execute+locked_experience+"
    "no_calendar_application_login_member_applicant_pii_attachment_or_download_calls"
)
ANSAN_EXPERIENCE_OWNERSHIP_SCOPE = (
    "ansan_integrated_reservation_indoor_outdoor_visit_catalogues"
)

ANSAN_CITY_CODE = "4127000000"
ANSAN_SANGNOK_CODE = "4127100000"
ANSAN_DANWON_CODE = "4127300000"
ANSAN_MUNICIPALITY_NAMES = {
    ANSAN_CITY_CODE: "경기도 안산시",
    ANSAN_SANGNOK_CODE: "경기도 안산시 상록구",
    ANSAN_DANWON_CODE: "경기도 안산시 단원구",
}
ANSAN_EXPERIENCE_COVERED_MUNICIPALITIES: tuple[dict[str, str], ...] = (
    {
        "code": ANSAN_CITY_CODE,
        "sido": "경기도",
        "sigungu": "안산시",
        "full_name": ANSAN_MUNICIPALITY_NAMES[ANSAN_CITY_CODE],
    },
    {
        "code": ANSAN_SANGNOK_CODE,
        "sido": "경기도",
        "sigungu": "안산시 상록구",
        "full_name": ANSAN_MUNICIPALITY_NAMES[ANSAN_SANGNOK_CODE],
    },
    {
        "code": ANSAN_DANWON_CODE,
        "sido": "경기도",
        "sigungu": "안산시 단원구",
        "full_name": ANSAN_MUNICIPALITY_NAMES[ANSAN_DANWON_CODE],
    },
)


@dataclass(frozen=True)
class AnsanExperienceCategory:
    code: str
    name: str
    menu_no: str
    program_type: str

    @property
    def list_path(self) -> str:
        return f"/exp/{self.code}/expList.do"

    @property
    def detail_path(self) -> str:
        return f"/exp/{self.code}/expView.do"


ANSAN_EXPERIENCE_CATEGORIES: tuple[AnsanExperienceCategory, ...] = (
    AnsanExperienceCategory("X01", "실내체험", "667", "체험"),
    AnsanExperienceCategory("X02", "실외체험", "668", "체험"),
    AnsanExperienceCategory("X03", "견학", "669", "견학"),
)
_CATEGORY_BY_CODE = {item.code: item for item in ANSAN_EXPERIENCE_CATEGORIES}

ANSAN_EXPERIENCE_LIVE_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_total": 334,
    "category_totals": {"X01": 208, "X02": 43, "X03": 83},
    "data_pages": 35,
    "current_count": 41,
    "expired_count": 293,
    "source_status_counts": {"접수중": 28, "접수대기": 4, "접수마감": 302},
    "current_status_counts": {"OPEN": 27, "SCHEDULED": 4, "CLOSED": 10},
    "current_municipality_counts": {
        ANSAN_SANGNOK_CODE: 12,
        ANSAN_DANWON_CODE: 29,
    },
}


class AnsanExperienceContractError(RuntimeError):
    """Raised when the audited public Ansan experience contract changes."""


SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"RESR_[0-9]{10,20}\Z")
_VIEW_CONTROL_RE = re.compile(
    r"fnView\(\s*['\"](?P<identity>RESR_[0-9]{10,20})['\"]\s*\)\s*;?\Z"
)
_FAVORITE_CONTROL_RE = re.compile(
    r"fnFavorite\(\s*['\"](?P<identity>RESR_[0-9]{10,20})['\"]\s*\)\s*;?\Z"
)
_TOTAL_RE = re.compile(r"전체\s*:?\s*([\d,]+)\s*건")
_PAGE_RE = re.compile(r"fnSearch\(\s*(\d+)\s*\)")
_EVENT_PERIOD_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})\Z"
)
_APPLY_PERIOD_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})\Z"
)
_ADDRESS_RE = re.compile(r"^(?:경기|경기도)\s+안산시\s+(상록구|단원구)(?:\s+|\Z)")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")

_STATUS_MAP = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
}
_LIST_LABELS = (
    "기관/부서",
    "접수기간",
    "체험/견학기간",
    "요일",
    "대상",
    "사용료",
    "위치",
)
_DETAIL_LABELS = (
    "기관/부서",
    "예약방식",
    "접수기간",
    "선정방식",
    "대상",
    "사용료",
    "체험/견학기간",
    "요일",
)
_DETAIL_OPTIONAL_LABELS = frozenset(
    {"나이제한", "지역제한", "성별제한", "체험시간", "모집정원"}
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "parser",
        "identity",
        "category_code",
        "category_name",
        "source_page",
        "source_position",
        "source_status",
        "source_department",
        "source_location",
        "municipality_evidence",
        "detail_verified",
        "application_control_present",
        "application_control_verified",
        "application_control_executed",
        "calendar_endpoint_fetched",
        "application_endpoint_fetched",
        "login_auth_member_applicant_pii_endpoint_fetched",
        "attachment_download_endpoint_fetched",
        "discarded_detail_fields",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "attachments",
        "attachment_urls",
        "raw_html",
        "applicant",
        "member",
        "password",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _canonical_target_url(value: Any) -> bool:
    try:
        parsed = urlparse(_clean(value))
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    category = ANSAN_EXPERIENCE_CATEGORIES[0]
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == ANSAN_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == category.list_path
        and query == [("currentMenuNo", category.menu_no)]
        and not parsed.params
        and not parsed.fragment
    )


def is_ansan_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")).upper()
        == ANSAN_EXPERIENCE_PROVIDER
        and _canonical_target_url(_target_value(target, "url"))
    )


is_target = is_ansan_experience_target


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise AnsanExperienceContractError(f"{label} must be positive")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise AnsanExperienceContractError(f"{label} must be positive") from exc
    if result < 1:
        raise AnsanExperienceContractError(f"{label} must be positive")
    return result


def ansan_experience_list_url(
    category: AnsanExperienceCategory | str, page: Any = 1
) -> str:
    current = _CATEGORY_BY_CODE.get(category) if isinstance(category, str) else category
    if current not in ANSAN_EXPERIENCE_CATEGORIES:
        raise AnsanExperienceContractError("unknown Ansan experience category")
    page_number = _positive_int(page, "page")
    query = [("currentMenuNo", current.menu_no)]
    if page_number > 1:
        query.append(("pageIndex", str(page_number)))
    return f"https://{ANSAN_EXPERIENCE_HOST}{current.list_path}?{urlencode(query)}"


def ansan_experience_detail_url(
    category: AnsanExperienceCategory | str, identity: Any
) -> str:
    current = _CATEGORY_BY_CODE.get(category) if isinstance(category, str) else category
    clean_identity = _clean(identity)
    if current not in ANSAN_EXPERIENCE_CATEGORIES:
        raise AnsanExperienceContractError("unknown Ansan experience category")
    if not _IDENTITY_RE.fullmatch(clean_identity):
        raise AnsanExperienceContractError("invalid Ansan reservation identity")
    return (
        f"https://{ANSAN_EXPERIENCE_HOST}{current.detail_path}?"
        + urlencode(
            (("currentMenuNo", current.menu_no), ("resrId", clean_identity))
        )
    )


def _request_kind(method: Any, url: Any) -> str:
    if _clean(method).upper() != "GET":
        raise AnsanExperienceContractError(
            "calendar/application/login/member/applicant/PII/attachment/download/POST refused"
        )
    try:
        parsed = urlparse(_clean(url))
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise AnsanExperienceContractError("unsafe request URL") from exc
    common = bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == ANSAN_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.fragment
    )
    if not common:
        raise AnsanExperienceContractError("unsafe request URL")
    for category in ANSAN_EXPERIENCE_CATEGORIES:
        if parsed.path == category.list_path:
            allowed = [("currentMenuNo", category.menu_no)]
            if query == allowed:
                return "list"
            if (
                len(query) == 2
                and query[0] == allowed[0]
                and query[1][0] == "pageIndex"
                and query[1][1].isdigit()
                and int(query[1][1]) > 1
            ):
                return "list"
        if (
            parsed.path == category.detail_path
            and len(query) == 2
            and query[0] == ("currentMenuNo", category.menu_no)
            and query[1][0] == "resrId"
            and _IDENTITY_RE.fullmatch(query[1][1])
        ):
            return "detail"
    raise AnsanExperienceContractError(
        "calendar/application/login/member/applicant/PII/attachment/download endpoint refused"
    )


def _coerce_soup(response: Any, expected_url: str) -> BeautifulSoup:
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise AnsanExperienceContractError(f"unexpected HTTP status {status}")
    if tuple(getattr(response, "history", ()) or ()):
        raise AnsanExperienceContractError("redirect history is forbidden")
    headers = getattr(response, "headers", {}) or {}
    if any(
        str(key).lower() == "location" and _clean(value)
        for key, value in headers.items()
    ):
        raise AnsanExperienceContractError("redirect location is forbidden")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and final_url != expected_url:
        raise AnsanExperienceContractError("response URL drift")
    content_type = next(
        (
            _clean(value).lower()
            for key, value in headers.items()
            if str(key).lower() == "content-type"
        ),
        "",
    )
    if content_type and not content_type.startswith("text/html"):
        raise AnsanExperienceContractError("non-HTML response refused")
    content = getattr(response, "content", None)
    if content is None:
        content = str(getattr(response, "text", "")).encode("utf-8")
    if not content or len(content) > ANSAN_EXPERIENCE_MAX_HTML_BYTES:
        raise AnsanExperienceContractError("invalid HTML response size")
    return BeautifulSoup(content, "lxml")


class _Runner:
    """One verified session per request, permitting bounded parallel reads."""

    def __init__(self, session_factory: SessionFactory, timeout: int) -> None:
        self.session_factory = session_factory
        self.timeout = timeout
        self.lock = Lock()
        self.list_requests = 0
        self.detail_requests = 0
        self.detail_warmup_list_requests = 0
        self.post_requests = 0
        self.unsafe_endpoint_calls = 0

    def _get(self, session: Any, url: str, *, referer: str) -> BeautifulSoup:
        kind = _request_kind("GET", url)
        response = session.get(
            url,
            timeout=self.timeout,
            allow_redirects=False,
            headers={"Referer": referer},
        )
        with self.lock:
            if kind == "list":
                self.list_requests += 1
            else:
                self.detail_requests += 1
        return _coerce_soup(response, url)

    def soup(self, url: str, *, warmup_url: str = "") -> BeautifulSoup:
        # The public detail route requires the category-list cookie/tracer state.
        # The warmup remains an allowlisted list GET; no script is executed.
        session = self.session_factory()
        try:
            if warmup_url:
                if _request_kind("GET", warmup_url) != "list":
                    raise AnsanExperienceContractError("detail warmup must be a list GET")
                self._get(session, warmup_url, referer=ANSAN_EXPERIENCE_URL)
                with self.lock:
                    self.detail_warmup_list_requests += 1
            return self._get(
                session,
                url,
                referer=warmup_url or ANSAN_EXPERIENCE_URL,
            )
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()


def _parallel_map(function: Callable[[Any], Any], values: list[Any]) -> list[Any]:
    if not values:
        return []
    results: list[Any] = []
    errors: list[str] = []
    with ThreadPoolExecutor(
        max_workers=min(ANSAN_EXPERIENCE_MAX_WORKERS, len(values))
    ) as executor:
        futures = [executor.submit(function, value) for value in values]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {_clean(exc)}")
    if errors:
        raise AnsanExperienceContractError(
            "parallel contract failures: " + "; ".join(errors)
        )
    return results


def _label_pairs(node: Any, label_selector: str, value_selector: str = "") -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for item in node.select("li") if node is not None else []:
        label_node = item.select_one(label_selector)
        if label_node is None:
            raise AnsanExperienceContractError("structured field lacks label")
        label = _clean(label_node.get_text(" ", strip=True))
        if value_selector:
            value_node = item.select_one(value_selector)
            if value_node is None:
                raise AnsanExperienceContractError(f"{label}: structured value missing")
            value = _clean(value_node.get_text(" ", strip=True))
        else:
            whole = _clean(item.get_text(" ", strip=True))
            if not whole.startswith(label):
                raise AnsanExperienceContractError(f"{label}: field prefix drift")
            value = _clean(whole[len(label) :])
        if not label or not value or any(existing == label for existing, _ in pairs):
            raise AnsanExperienceContractError("empty or duplicate structured field")
        pairs.append((label, value))
    return tuple(pairs)


def _parse_event_period(value: str, identity: str) -> tuple[date, date]:
    match = _EVENT_PERIOD_RE.fullmatch(_clean(value))
    if not match:
        raise AnsanExperienceContractError(f"{identity}: event period drift")
    start, end = (date.fromisoformat(token) for token in match.groups())
    if start > end:
        raise AnsanExperienceContractError(f"{identity}: reversed event period")
    return start, end


def _parse_apply_period(value: str, identity: str) -> tuple[datetime, datetime]:
    match = _APPLY_PERIOD_RE.fullmatch(_clean(value))
    if not match:
        raise AnsanExperienceContractError(f"{identity}: application period drift")
    start = datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}")
    end = datetime.fromisoformat(f"{match.group(3)}T{match.group(4)}")
    if start > end:
        raise AnsanExperienceContractError(f"{identity}: reversed application period")
    return start, end


def _parse_list_row(
    item: Any,
    category: AnsanExperienceCategory,
    page: int,
    position: int,
) -> dict[str, Any]:
    links = item.select("a[onclick*='fnView']")
    if len(links) != 1:
        raise AnsanExperienceContractError("experience card detail control drift")
    link = links[0]
    control = _VIEW_CONTROL_RE.fullmatch(_clean(link.get("onclick")))
    if control is None or _clean(link.get("href")) != "#none":
        raise AnsanExperienceContractError("experience card detail identity drift")
    identity = control.group("identity")
    title_node = item.select_one(".txtW .tit")
    status_node = item.select_one(".label")
    title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
    source_status = _clean(
        status_node.get_text(" ", strip=True) if status_node else ""
    )
    if not title or source_status not in _STATUS_MAP:
        raise AnsanExperienceContractError(f"{identity}: title/status drift")
    pairs = _label_pairs(item.select_one(".txtW .etc"), ".em")
    if tuple(label for label, _ in pairs) != _LIST_LABELS:
        raise AnsanExperienceContractError(f"{identity}: list field vocabulary drift")
    fields = dict(pairs)
    event_start, event_end = _parse_event_period(fields["체험/견학기간"], identity)
    apply_start, apply_end = _parse_apply_period(fields["접수기간"], identity)
    detail_url = ansan_experience_detail_url(category, identity)
    return {
        "identity": identity,
        "category": category,
        "page": page,
        "position": position,
        "title": title,
        "source_status": source_status,
        "status": _STATUS_MAP[source_status],
        "department": fields["기관/부서"],
        "apply_start": apply_start,
        "apply_end": apply_end,
        "event_start": event_start,
        "event_end": event_end,
        "weekdays": fields["요일"],
        "target": fields["대상"],
        "fee": fields["사용료"],
        "location": fields["위치"],
        "detail_url": detail_url,
    }


def _parse_list_page(
    soup: BeautifulSoup,
    category: AnsanExperienceCategory,
    requested_page: int,
) -> dict[str, Any]:
    if _clean(soup.title.get_text(" ", strip=True) if soup.title else "") != (
        "안산시 통합예약시스템"
    ):
        raise AnsanExperienceContractError("Ansan page title drift")
    form = soup.select_one("form[name='searchVO']")
    menu = form.select_one("input[name='currentMenuNo']") if form else None
    if menu is None or _clean(menu.get("value")) != category.menu_no:
        raise AnsanExperienceContractError("category list form drift")
    text = _clean(soup.get_text(" ", strip=True))
    totals = {int(value.replace(",", "")) for value in _TOTAL_RE.findall(text)}
    if len(totals) != 1:
        raise AnsanExperienceContractError("missing or conflicting advertised total")
    advertised_total = totals.pop()
    if advertised_total < 1:
        raise AnsanExperienceContractError("historical experience ledger unexpectedly empty")
    calculated_last = math.ceil(advertised_total / ANSAN_EXPERIENCE_PAGE_SIZE)
    advertised_pages = [int(value) for value in _PAGE_RE.findall(str(soup))]
    if (
        any(page < 1 or page > calculated_last for page in advertised_pages)
        or (requested_page <= calculated_last and not advertised_pages)
        or (requested_page == 1 and max(advertised_pages) != calculated_last)
    ):
        raise AnsanExperienceContractError("advertised total/page disagreement")
    items = soup.select("ul.blog.reserv > li")
    if len(items) > ANSAN_EXPERIENCE_PAGE_SIZE:
        raise AnsanExperienceContractError("experience page row boundary drift")
    rows = [
        _parse_list_row(item, category, requested_page, position)
        for position, item in enumerate(items, 1)
    ]
    return {
        "category": category,
        "requested_page": requested_page,
        "advertised_total": advertised_total,
        "advertised_last": calculated_last,
        "rows": rows,
    }


def _page_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        page["category"].code,
        int(page["advertised_total"]),
        int(page["advertised_last"]),
        tuple(
            (
                row["identity"],
                row["title"],
                row["source_status"],
                row["department"],
                row["apply_start"].isoformat(),
                row["apply_end"].isoformat(),
                row["event_start"].isoformat(),
                row["event_end"].isoformat(),
                row["weekdays"],
                row["target"],
                row["fee"],
                row["location"],
            )
            for row in page["rows"]
        ),
    )


def _detail_fields(info: Any, identity: str) -> dict[str, str]:
    pairs = _label_pairs(info.select_one("ul.itemList"), ".em", ".txt")
    labels = tuple(label for label, _ in pairs)
    core_labels = tuple(
        label for label in labels if label not in _DETAIL_OPTIONAL_LABELS
    )
    if (
        core_labels != _DETAIL_LABELS
        or not set(labels) <= set(_DETAIL_LABELS) | _DETAIL_OPTIONAL_LABELS
    ):
        raise AnsanExperienceContractError(f"{identity}: detail field vocabulary drift")
    return dict(pairs)


def _detail_address(soup: BeautifulSoup, identity: str) -> tuple[str, str, str]:
    values: list[str] = []
    for item in soup.select(".rsvPlace ul.loca > li"):
        label_node = item.select_one(".em")
        label = _clean(label_node.get_text(" ", strip=True) if label_node else "")
        if "위치" not in label:
            continue
        clone = BeautifulSoup(str(item), "lxml")
        cloned_label = clone.select_one(".em")
        if cloned_label is not None:
            cloned_label.decompose()
        values.append(_clean(clone.get_text(" ", strip=True)))
    if len(values) != 1:
        raise AnsanExperienceContractError(f"{identity}: detail location drift")
    address = values[0]
    match = _ADDRESS_RE.match(address)
    if match is None:
        raise AnsanExperienceContractError(
            f"{identity}: explicit Sangnok/Danwon detail address required"
        )
    district = match.group(1)
    code = ANSAN_SANGNOK_CODE if district == "상록구" else ANSAN_DANWON_CODE
    return address, code, ANSAN_MUNICIPALITY_NAMES[code]


def _application_control(
    soup: BeautifulSoup, listed: Mapping[str, Any]
) -> bool:
    identity = str(listed["identity"])
    controls = soup.select("#resvRqstBtn")
    if listed["status"] == "OPEN":
        if len(controls) != 1:
            raise AnsanExperienceContractError(
                f"{identity}: open application control missing"
            )
        control = controls[0]
        if (
            control.name != "a"
            or _clean(control.get_text(" ", strip=True)) != "예약신청"
            or _clean(control.get("href")) != "#none"
            or _clean(control.get("onclick")) != "checkInTracer();"
            or _clean(control.get("formaction"))
        ):
            raise AnsanExperienceContractError(
                f"{identity}: open application control drift"
            )
        return True
    if controls:
        raise AnsanExperienceContractError(
            f"{identity}: inactive programme exposes application control"
        )
    return False


def _branch_code(value: str) -> str:
    return "ANSAN_EXP_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()


def _fee_amount(value: str) -> Optional[int]:
    text = _clean(value)
    if text in {"무료", "없음", "0", "0원"}:
        return 0
    match = re.fullmatch(r"(\d[\d,]*)\s*원", text)
    return int(match.group(1).replace(",", "")) if match else None


def _parse_detail(soup: BeautifulSoup, listed: Mapping[str, Any]) -> dict[str, Any]:
    identity = str(listed["identity"])
    info = soup.select_one("div.listInfo div.infoArea")
    title_node = info.select_one("p.tit") if info else None
    status_node = info.select_one("p.label") if info else None
    if (
        info is None
        or title_node is None
        or status_node is None
        or _clean(title_node.get_text(" ", strip=True)) != listed["title"]
        or _clean(status_node.get_text(" ", strip=True)) != listed["source_status"]
    ):
        raise AnsanExperienceContractError(f"{identity}: list/detail identity drift")
    favorite_controls = [
        control
        for control in soup.select("[onclick*='fnFavorite']")
        if _FAVORITE_CONTROL_RE.fullmatch(_clean(control.get("onclick")))
    ]
    if len(favorite_controls) != 1 or _FAVORITE_CONTROL_RE.fullmatch(
        _clean(favorite_controls[0].get("onclick"))
    ).group("identity") != identity:
        raise AnsanExperienceContractError(f"{identity}: detail identity control drift")
    fields = _detail_fields(info, identity)
    expected = {
        "기관/부서": listed["department"],
        "접수기간": (
            f"{listed['apply_start'].strftime('%Y-%m-%d %H:%M')} ~ "
            f"{listed['apply_end'].strftime('%Y-%m-%d %H:%M')}"
        ),
        "대상": listed["target"],
        "사용료": listed["fee"],
        "체험/견학기간": (
            f"{listed['event_start'].isoformat()} ~ {listed['event_end'].isoformat()}"
        ),
        "요일": listed["weekdays"],
    }
    if any(fields[key] != value for key, value in expected.items()):
        raise AnsanExperienceContractError(f"{identity}: list/detail field drift")
    method_tokens = tuple(
        token.strip() for token in fields["예약방식"].split(",") if token.strip()
    )
    if (
        not method_tokens
        or not set(method_tokens) <= {"인터넷", "전화", "방문"}
        or (listed["status"] == "OPEN" and "인터넷" not in method_tokens)
        or not fields["선정방식"]
    ):
        raise AnsanExperienceContractError(f"{identity}: reservation method drift")
    address, municipality_code, municipality_name = _detail_address(soup, identity)
    application_control = _application_control(soup, listed)
    detail_url = str(listed["detail_url"])
    category: AnsanExperienceCategory = listed["category"]
    event_period = (
        f"{listed['event_start'].isoformat()} ~ {listed['event_end'].isoformat()}"
    )
    apply_period = (
        f"{listed['apply_start'].strftime('%Y-%m-%d %H:%M')} ~ "
        f"{listed['apply_end'].strftime('%Y-%m-%d %H:%M')}"
    )
    branch = str(listed["department"])
    return {
        "provider": ANSAN_EXPERIENCE_PROVIDER,
        "provider_course_id": f"{ANSAN_EXPERIENCE_PROVIDER}:experience:{identity}",
        "prefer_incoming_provider_course_id": True,
        "source_course_id": f"experience:{identity}",
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": branch,
        "branch_code": _branch_code(branch),
        "branch_url": ANSAN_EXPERIENCE_URL,
        "preserve_branch": True,
        "category": category.name,
        "program_type": category.program_type,
        "program_type_source": "official_experience_category",
        "raw_url": detail_url,
        "source_url": detail_url,
        "application_url": detail_url if application_control else "",
        "application_type": (
            "ONLINE_RESERVATION" if application_control else "INFO_ONLY"
        ),
        "application_method": {"인터넷": "온라인", "전화": "전화", "방문": "방문"}[
            method_tokens[0]
        ],
        "application_methods": [
            {"인터넷": "온라인", "전화": "전화", "방문": "방문"}[token]
            for token in method_tokens
        ],
        "application_method_raw": fields["예약방식"],
        "reservation_available": application_control,
        "status": str(listed["status"]),
        "course_status": str(listed["status"]),
        "raw_status": str(listed["source_status"]),
        "source_status": str(listed["source_status"]),
        "fee": str(listed["fee"]),
        "fee_amount": _fee_amount(str(listed["fee"])),
        "period": event_period,
        "start_date": listed["event_start"].isoformat(),
        "end_date": listed["event_end"].isoformat(),
        "apply_period": apply_period,
        "apply_start_date": listed["apply_start"].date().isoformat(),
        "apply_end_date": listed["apply_end"].date().isoformat(),
        "schedule_raw": f"요일 {listed['weekdays']}",
        "target": str(listed["target"]),
        "target_audience": str(listed["target"]),
        "venue": str(listed["location"]),
        "venue_name": str(listed["location"]),
        "room": str(listed["location"]),
        "address": address,
        "venue_address": address,
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "operator_type": "지자체/공공기관",
        "source_group": "public_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "classification_locked": True,
        "collection_type": ANSAN_EXPERIENCE_PARSER,
        "municipality_code": municipality_code,
        "municipality_name": municipality_name,
        "municipality_full_name": municipality_name,
        "region": municipality_name,
        "sido": "경기도",
        "sigungu": municipality_name.removeprefix("경기도 "),
        "raw_fields": {
            "parser": ANSAN_EXPERIENCE_PARSER,
            "identity": identity,
            "category_code": category.code,
            "category_name": category.name,
            "source_page": int(listed["page"]),
            "source_position": int(listed["position"]),
            "source_status": str(listed["source_status"]),
            "source_department": branch,
            "source_location": str(listed["location"]),
            "municipality_evidence": "public_detail_address_explicit_district",
            "detail_verified": True,
            "application_control_present": application_control,
            "application_control_verified": True,
            "application_control_executed": False,
            "calendar_endpoint_fetched": False,
            "application_endpoint_fetched": False,
            "login_auth_member_applicant_pii_endpoint_fetched": False,
            "attachment_download_endpoint_fetched": False,
            "discarded_detail_fields": [
                "free-form 이용안내",
                "문의처",
                "전화번호",
                "첨부파일 이름과 다운로드 제어",
                "달력/회차 Ajax 제어",
            ],
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
            if key in _FORBIDDEN_ROW_KEYS or key in {"description", "raw_fields"}
        }
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload) or _RESIDENT_ID_RE.search(payload):
        errors.append("PII persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail persisted")
    return errors


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = str(row["provider_course_id"])
        if identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _cutoff(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(ZoneInfo("Asia/Seoul")).date()
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _initial_meta() -> dict[str, Any]:
    return {
        "municipality_code": ANSAN_CITY_CODE,
        "municipality_full_name": ANSAN_MUNICIPALITY_NAMES[ANSAN_CITY_CODE],
        "covered_municipalities": [
            dict(item) for item in ANSAN_EXPERIENCE_COVERED_MUNICIPALITIES
        ],
        "owner_provider": ANSAN_EXPERIENCE_PROVIDER,
        "canonical_candidate_id": ANSAN_EXPERIENCE_CANDIDATE_ID,
        "canonical_url": ANSAN_EXPERIENCE_URL,
        "ownership_scope": ANSAN_EXPERIENCE_OWNERSHIP_SCOPE,
        "parser": ANSAN_EXPERIENCE_PARSER,
        "live_audit_baseline": dict(ANSAN_EXPERIENCE_LIVE_BASELINE),
        "source_total": 0,
        "source_rows": 0,
        "data_pages": 0,
        "current_count": 0,
        "expired_count": 0,
        "expired_active_status_count": 0,
        "returned_count": 0,
        "list_requests": 0,
        "detail_warmup_list_requests": 0,
        "detail_pages": 0,
        "physical_requests": 0,
        "post_last_sentinels": 0,
        "boundary_rechecks": 0,
        "pagination_complete": False,
        "details_complete": False,
        "districts_complete": False,
        "application_controls_current": 0,
        "application_controls_executed": 0,
        "calendar_endpoint_requests": 0,
        "application_endpoint_requests": 0,
        "login_auth_member_applicant_pii_endpoint_requests": 0,
        "attachment_download_endpoint_requests": 0,
        "post_requests": 0,
        "unsafe_endpoint_calls": 0,
        "privacy_violations": 0,
        "semantic_duplicate_count": 0,
        "source_cap_reached": False,
        "no_current_data": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "configured_collection_error": "",
    }


def collect_ansan_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = ANSAN_EXPERIENCE_RECOMMENDED_MAX_PAGES,
    detail_limit: int = ANSAN_EXPERIENCE_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect an atomic current/future Ansan experience snapshot."""

    meta = _initial_meta()
    if not is_ansan_experience_target(target):
        meta["configured_collection_error"] = (
            "target does not match exact Ansan experience owner"
        )
        return [], ANSAN_EXPERIENCE_PARSER, meta
    if session_factory is None:
        meta["configured_collection_error"] = "managed session_factory injection is required"
        return [], ANSAN_EXPERIENCE_PARSER, meta
    try:
        timeout_value = _positive_int(timeout, "timeout")
        max_page_value = _positive_int(max_pages, "max_pages")
        if isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise AnsanExperienceContractError("detail_limit must be non-negative")
        detail_limit_value = int(detail_limit)
        cutoff = _cutoff(today)
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], ANSAN_EXPERIENCE_PARSER, meta

    runner = _Runner(session_factory, timeout_value)
    try:
        first_pages = {
            page["category"].code: page
            for page in _parallel_map(
                lambda category: _parse_list_page(
                    runner.soup(ansan_experience_list_url(category, 1)), category, 1
                ),
                list(ANSAN_EXPERIENCE_CATEGORIES),
            )
        }
        for category in ANSAN_EXPERIENCE_CATEGORIES:
            last = int(first_pages[category.code]["advertised_last"])
            if last > max_page_value:
                meta["source_cap_reached"] = True
                raise AnsanExperienceContractError(
                    f"{category.code}: advertised pages {last} exceed max_pages {max_page_value}"
                )

        page_jobs = [
            (category, page)
            for category in ANSAN_EXPERIENCE_CATEGORIES
            for page in range(2, int(first_pages[category.code]["advertised_last"]) + 1)
        ]
        parsed_pages = _parallel_map(
            lambda job: _parse_list_page(
                runner.soup(ansan_experience_list_url(job[0], job[1])),
                job[0],
                job[1],
            ),
            page_jobs,
        )
        pages_by_category: dict[str, list[dict[str, Any]]] = {
            category.code: [first_pages[category.code]]
            for category in ANSAN_EXPERIENCE_CATEGORIES
        }
        for page in parsed_pages:
            pages_by_category[page["category"].code].append(page)
        for pages in pages_by_category.values():
            pages.sort(key=lambda page: int(page["requested_page"]))

        sentinel_jobs = [
            (category, int(first_pages[category.code]["advertised_last"]) + 1)
            for category in ANSAN_EXPERIENCE_CATEGORIES
        ]
        sentinels = {
            page["category"].code: page
            for page in _parallel_map(
                lambda job: _parse_list_page(
                    runner.soup(ansan_experience_list_url(job[0], job[1])),
                    job[0],
                    job[1],
                ),
                sentinel_jobs,
            )
        }

        listed: list[dict[str, Any]] = []
        for category in ANSAN_EXPERIENCE_CATEGORIES:
            pages = pages_by_category[category.code]
            first = pages[0]
            total = int(first["advertised_total"])
            last = int(first["advertised_last"])
            if len(pages) != last:
                raise AnsanExperienceContractError(
                    f"{category.code}: incomplete page set"
                )
            if any(
                int(page["advertised_total"]) != total
                or int(page["advertised_last"]) != last
                for page in pages
            ):
                raise AnsanExperienceContractError(
                    f"{category.code}: advertised pagination contract drift"
                )
            if any(
                len(page["rows"]) != ANSAN_EXPERIENCE_PAGE_SIZE
                for page in pages[:-1]
            ):
                raise AnsanExperienceContractError(
                    f"{category.code}: non-final page is not full"
                )
            if not 1 <= len(pages[-1]["rows"]) <= ANSAN_EXPERIENCE_PAGE_SIZE:
                raise AnsanExperienceContractError(
                    f"{category.code}: final page boundary drift"
                )
            category_rows = [row for page in pages for row in page["rows"]]
            if len(category_rows) != total:
                raise AnsanExperienceContractError(
                    f"{category.code}: total/page arithmetic drift"
                )
            sentinel = sentinels[category.code]
            if (
                sentinel["rows"]
                or int(sentinel["advertised_total"]) != total
                or int(sentinel["advertised_last"]) != last
            ):
                raise AnsanExperienceContractError(
                    f"{category.code}: post-last sentinel is not exactly empty"
                )
            listed.extend(category_rows)

        identities = [str(row["identity"]) for row in listed]
        if len(identities) != len(set(identities)):
            raise AnsanExperienceContractError("cross-category identity duplicated")
        # The owner retains one historical row with a stale ``접수중`` badge.
        # Event end date is the authoritative current-snapshot boundary, so the
        # anomaly is censused but can never leak into returned current rows.
        expired_active_status_count = sum(
            item["status"] in {"OPEN", "SCHEDULED"}
            and item["event_end"] < cutoff
            for item in listed
        )
        current = [item for item in listed if item["event_end"] >= cutoff]
        if len(current) > detail_limit_value:
            meta["source_cap_reached"] = True
            raise AnsanExperienceContractError(
                f"detail_limit {detail_limit_value} below required {len(current)}"
            )

        rows = _parallel_map(
            lambda item: _parse_detail(
                runner.soup(
                    str(item["detail_url"]),
                    warmup_url=ansan_experience_list_url(
                        item["category"], int(item["page"])
                    ),
                ),
                item,
            ),
            current,
        )

        boundary_jobs = [
            (category, page)
            for category in ANSAN_EXPERIENCE_CATEGORIES
            for page in (
                1,
                int(first_pages[category.code]["advertised_last"]),
                int(first_pages[category.code]["advertised_last"]) + 1,
            )
        ]
        rechecks = _parallel_map(
            lambda job: _parse_list_page(
                runner.soup(ansan_experience_list_url(job[0], job[1])),
                job[0],
                job[1],
            ),
            boundary_jobs,
        )
        recheck_map = {
            (page["category"].code, int(page["requested_page"])): page
            for page in rechecks
        }
        for category in ANSAN_EXPERIENCE_CATEGORIES:
            pages = pages_by_category[category.code]
            expected = {
                1: pages[0],
                int(pages[-1]["requested_page"]): pages[-1],
                int(pages[-1]["requested_page"]) + 1: sentinels[category.code],
            }
            for page_number, original in expected.items():
                if _page_signature(recheck_map[(category.code, page_number)]) != _page_signature(original):
                    raise AnsanExperienceContractError(
                        f"{category.code}: first/last/sentinel changed during collection"
                    )

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _default_dedupe)(rows))
        expected_ids = {
            f"{ANSAN_EXPERIENCE_PROVIDER}:experience:{item['identity']}"
            for item in current
        }
        if len(rows) != len(current) or {
            str(row.get("provider_course_id")) for row in rows
        } != expected_ids:
            raise AnsanExperienceContractError(
                "dedupe changed complete current experience identity set"
            )
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        if privacy_errors:
            meta["privacy_violations"] = len(privacy_errors)
            raise AnsanExperienceContractError("; ".join(privacy_errors[:5]))
        if any(
            bool(row["application_url"]) != bool(row["reservation_available"])
            for row in rows
        ):
            raise AnsanExperienceContractError("application URL/availability contract drift")
        semantic_counts = Counter(
            (
                _clean(row["title"]).casefold(),
                _clean(row["start_date"]),
                _clean(row["schedule_raw"]),
                _clean(row["venue_address"]),
            )
            for row in rows
        )
        semantic_duplicates = sum(
            count - 1 for count in semantic_counts.values() if count > 1
        )
        if semantic_duplicates:
            meta["semantic_duplicate_count"] = semantic_duplicates
            raise AnsanExperienceContractError("semantic duplicate current programmes")

        category_totals = {
            category.code: int(first_pages[category.code]["advertised_total"])
            for category in ANSAN_EXPERIENCE_CATEGORIES
        }
        page_counts = {
            category.code: [len(page["rows"]) for page in pages_by_category[category.code]]
            for category in ANSAN_EXPERIENCE_CATEGORIES
        }
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "source_total": len(listed),
                "source_rows": len(listed),
                "category_totals": category_totals,
                "category_page_counts": page_counts,
                "source_status_counts": dict(
                    Counter(str(item["source_status"]) for item in listed)
                ),
                "data_pages": sum(len(pages) for pages in pages_by_category.values()),
                "current_count": len(current),
                "expired_count": len(listed) - len(current),
                "expired_active_status_count": expired_active_status_count,
                "current_category_counts": dict(
                    Counter(item["category"].code for item in current)
                ),
                "current_source_status_counts": dict(
                    Counter(str(item["source_status"]) for item in current)
                ),
                "status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "municipality_counts": dict(
                    Counter(str(row["municipality_code"]) for row in rows)
                ),
                "returned_count": len(rows),
                "list_requests": runner.list_requests,
                "detail_warmup_list_requests": runner.detail_warmup_list_requests,
                "detail_pages": runner.detail_requests,
                "physical_requests": runner.list_requests + runner.detail_requests,
                "post_last_sentinels": len(ANSAN_EXPERIENCE_CATEGORIES),
                "boundary_rechecks": len(boundary_jobs),
                "pagination_complete": True,
                "details_complete": runner.detail_requests == len(current),
                "districts_complete": all(
                    row["municipality_code"] in {ANSAN_SANGNOK_CODE, ANSAN_DANWON_CODE}
                    for row in rows
                ),
                "application_controls_current": sum(
                    bool(row["raw_fields"]["application_control_present"])
                    for row in rows
                ),
                "post_requests": runner.post_requests,
                "unsafe_endpoint_calls": runner.unsafe_endpoint_calls,
                "no_current_data": not rows,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return rows, ANSAN_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta.update(
            {
                "list_requests": runner.list_requests,
                "detail_warmup_list_requests": runner.detail_warmup_list_requests,
                "detail_pages": runner.detail_requests,
                "physical_requests": runner.list_requests + runner.detail_requests,
                "post_requests": runner.post_requests,
                "unsafe_endpoint_calls": runner.unsafe_endpoint_calls,
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
            }
        )
        return [], ANSAN_EXPERIENCE_PARSER, meta


collect = collect_ansan_experience


__all__ = [
    "ANSAN_CITY_CODE",
    "ANSAN_DANWON_CODE",
    "ANSAN_EXPERIENCE_CANDIDATE_ID",
    "ANSAN_EXPERIENCE_CATEGORIES",
    "ANSAN_EXPERIENCE_COVERED_MUNICIPALITIES",
    "ANSAN_EXPERIENCE_LIVE_BASELINE",
    "ANSAN_EXPERIENCE_OWNERSHIP_SCOPE",
    "ANSAN_EXPERIENCE_PARSER",
    "ANSAN_EXPERIENCE_PROVIDER",
    "ANSAN_EXPERIENCE_URL",
    "ANSAN_SANGNOK_CODE",
    "AnsanExperienceCategory",
    "AnsanExperienceContractError",
    "ansan_experience_detail_url",
    "ansan_experience_list_url",
    "collect",
    "collect_ansan_experience",
    "is_ansan_experience_target",
    "is_target",
]
