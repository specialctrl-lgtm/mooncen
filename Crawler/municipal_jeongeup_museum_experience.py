"""Fail-closed collector for Jeongeup Museum's culture-experience ledger.

Jeongeup City's integrated-reservation service exposes the municipal museum's
dated programmes as a complete public ``RE`` list.  The list and each public
detail are readable with GET.  The inline ``writeFunc()`` control may reveal an
application form only after identity verification, so this collector validates
that control as inert evidence and never executes it.

Only the exact museum list and identity-bound public detail GET routes are in
the network allowlist.  The art museum, infant-forest calendar, toy rental,
tour-guide facility directory, application/receipt/login/member/applicant/PII,
attachment and download routes are deliberately outside this collector.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER = "MUNI_WWW_JEONGEUP_GO_KR_B4C520A0"
JEONGEUP_MUSEUM_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_7F1A4A90EBE7"
JEONGEUP_MUSEUM_EXPERIENCE_URL = (
    "https://www.jeongeup.go.kr/reserve/index.jeongeup?"
    "menuCd=DOM_000001202001000000"
)
JEONGEUP_MUSEUM_EXPERIENCE_URL_SHA256 = (
    "7f1a4a90ebe7cbb50041e58b2cc03fdb59caa669117b34b4aa1ed7517090fea3"
)
JEONGEUP_MUSEUM_EXPERIENCE_HOST = "www.jeongeup.go.kr"
JEONGEUP_MUSEUM_EXPERIENCE_PATH = "/reserve/index.jeongeup"
JEONGEUP_MUSEUM_EXPERIENCE_LINK_PATH = "/index.jeongeup"
JEONGEUP_MUSEUM_EXPERIENCE_LIST_MENU = "DOM_000001202001000000"
JEONGEUP_MUSEUM_EXPERIENCE_DETAIL_MENU = "DOM_000001202001001000"
JEONGEUP_MUSEUM_EXPERIENCE_BRANCH = "정읍시립박물관"
JEONGEUP_MUSEUM_EXPERIENCE_MUNICIPALITY_CODE = "5218000000"
JEONGEUP_MUSEUM_EXPERIENCE_MUNICIPALITY_NAME = "전북특별자치도 정읍시"
JEONGEUP_MUSEUM_EXPERIENCE_PAGE_SIZE = 10
JEONGEUP_MUSEUM_EXPERIENCE_MAX_HTML_BYTES = 2_000_000
JEONGEUP_MUSEUM_EXPERIENCE_RECOMMENDED_MAX_PAGES = 20
JEONGEUP_MUSEUM_EXPERIENCE_RECOMMENDED_DETAIL_LIMIT = 100
JEONGEUP_MUSEUM_EXPERIENCE_PARSER = (
    "jeongeup_museum_complete_culture_experience_re_ledger+"
    "advertised_total_and_six_pages+exact_clamped_post_last+"
    "stable_first_last_overflow+all_current_public_details+"
    "identity_bound_inline_application_control_no_execute+"
    "locked_experience+canonical_list_detail_get_only+"
    "no_application_receipt_login_member_pii_attachment_download_or_post"
)
JEONGEUP_MUSEUM_EXPERIENCE_OWNERSHIP_SCOPE = (
    "jeongeup_integrated_reservation_municipal_museum_culture_experience_re_ledger"
)

JEONGEUP_MUSEUM_EXPERIENCE_LIVE_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_total": 58,
    "data_pages": 6,
    "current_count": 7,
    "expired_count": 51,
    "source_status_counts": {
        "계획중": 1,
        "온라인 접수중": 6,
        "행사종료": 51,
    },
    "current_status_counts": {"SCHEDULED": 1, "OPEN": 6},
    "application_controls_current": 6,
}

# These are owner-boundary evidence, not extra routes for this collector.  No
# URL below is fetched while collecting the museum provider.
JEONGEUP_MUSEUM_EXPERIENCE_SIBLING_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "art_museum_children": {
        "url": "https://www.jeongeup.go.kr/reserve/index.jeongeup?menuCd=DOM_000001202002001000",
        "decision": "separate_valid_art_museum_re_owner_historical_only",
        "observed_total": 57,
    },
    "art_museum_family": {
        "url": "https://www.jeongeup.go.kr/reserve/index.jeongeup?menuCd=DOM_000001202002002000",
        "decision": "separate_valid_art_museum_re_owner_with_current_programmes",
        "observed_total": 111,
        "observed_current": 4,
    },
    "art_museum_youth": {
        "url": "https://www.jeongeup.go.kr/reserve/index.jeongeup?menuCd=DOM_000001202002003000",
        "decision": "separate_valid_art_museum_re_owner_historical_only",
        "observed_total": 15,
    },
    "art_museum_adult": {
        "url": "https://www.jeongeup.go.kr/reserve/index.jeongeup?menuCd=DOM_000001202002005000",
        "decision": "separate_valid_art_museum_re_owner_empty",
        "observed_total": 0,
    },
    "art_museum_exhibition_stale": {
        "url": "https://www.jeongeup.go.kr/reserve/index.jeongeup?menuCd=DOM_000001202002004000",
        "decision": "exclude_stale_menu_route_without_public_ledger",
    },
    "forest_products_complex": {
        "url": "https://www.jeongeup.go.kr/reserve/index.jeongeup?menuCd=DOM_000001202007002000",
        "decision": "exclude_information_and_rental_shell_without_programme_identity",
    },
    "toy_rental": {
        "url": "https://www.jeongeup.go.kr/reserve/schedule/list.jeongeup?boardId=BBS_0000265&menuCd=DOM_000001202008000000&contentsSid=3843&cpath=%2Freserve",
        "decision": "exclude_equipment_rental_calendar",
    },
    "infant_forest": {
        "url": "https://www.jeongeup.go.kr/reserve/schedule/list.jeongeup?boardId=BBS_0000203&menuCd=DOM_000001202004006000&contentsSid=3538&cpath=%2Freserve",
        "decision": "separate_schedule_owner_not_part_of_museum_re_ledger",
    },
    "tour_guide": {
        "url": "https://www.jeongeup.go.kr/reserve/index.jeongeup?menuCd=DOM_000001202005001000",
        "decision": "exclude_facility_directory_without_dated_programme_identity",
    },
}


class JeongeupMuseumExperienceContractError(RuntimeError):
    """Raised when the audited public museum contract changes."""


SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"RE\d{7}\Z")
_DATE_RANGE_RE = re.compile(
    r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})\Z"
)
_CAPACITY_RE = re.compile(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)\Z")
_RESULT_COUNT_RE = re.compile(r"(\d[\d,]*)\s*건")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_WRITE_FUNC_RE = re.compile(r"writeFunc\s*\(\s*\)\s*;?\Z")

_STATUS_MAP = {
    "계획중": "SCHEDULED",
    "온라인 접수중": "OPEN",
    "행사종료": "CLOSED",
}
_STATUS_CLASSES = {
    "계획중": frozenset({"rec", "rec01"}),
    "온라인 접수중": frozenset({"rec", "rec02"}),
    "행사종료": frozenset({"rec", "rec04"}),
}
_LIST_LABELS = ("교육기간", "접수기간", "교육장")
_DETAIL_LABELS = (
    "접수기간",
    "행사기간",
    "행사시간",
    "행사장",
    "강사명",
    "수강료/재료비",
    "행사대상",
    "신청/정원",
    "문의담당자",
    "문의전화",
    "행사내용",
    "강의자료",
    "접수상태",
)
_DISCARDED_DETAIL_FIELDS = (
    "강사명",
    "문의담당자",
    "문의전화",
    "행사내용",
    "강의자료",
    "attachments and images",
    "inline identity/application form fields",
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "parser",
        "identity",
        "source_page",
        "source_position",
        "source_status",
        "source_apply_period",
        "source_event_period",
        "source_schedule",
        "source_target",
        "source_venue",
        "source_capacity_current",
        "source_capacity_total",
        "detail_verified",
        "application_control_present",
        "application_control_verified",
        "application_control_executed",
        "application_endpoint_fetched",
        "reservation_lookup_endpoint_fetched",
        "attachment_download_endpoint_fetched",
        "discarded_detail_fields",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "instructor",
        "manager",
        "contact",
        "phone",
        "email",
        "attachments",
        "attachment_urls",
        "event_content",
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
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == JEONGEUP_MUSEUM_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == JEONGEUP_MUSEUM_EXPERIENCE_PATH
        and query == [("menuCd", JEONGEUP_MUSEUM_EXPERIENCE_LIST_MENU)]
        and not parsed.params
        and not parsed.fragment
    )


def is_jeongeup_museum_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")).upper()
        == JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER
        and _canonical_target_url(_target_value(target, "url"))
    )


is_target = is_jeongeup_museum_experience_target


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise JeongeupMuseumExperienceContractError(f"{label} must be positive")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise JeongeupMuseumExperienceContractError(
            f"{label} must be positive"
        ) from exc
    if result < 1:
        raise JeongeupMuseumExperienceContractError(f"{label} must be positive")
    return result


def jeongeup_museum_experience_list_url(page: Any = 1) -> str:
    page_number = _positive_int(page, "page")
    query: list[tuple[str, str]] = [
        ("menuCd", JEONGEUP_MUSEUM_EXPERIENCE_LIST_MENU)
    ]
    if page_number > 1:
        query.append(("startPage", str(page_number)))
    return (
        f"https://{JEONGEUP_MUSEUM_EXPERIENCE_HOST}"
        f"{JEONGEUP_MUSEUM_EXPERIENCE_PATH}?{urlencode(query)}"
    )


def jeongeup_museum_experience_detail_url(identity: Any) -> str:
    clean_identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(clean_identity):
        raise JeongeupMuseumExperienceContractError("invalid RE identity")
    return (
        f"https://{JEONGEUP_MUSEUM_EXPERIENCE_HOST}"
        f"{JEONGEUP_MUSEUM_EXPERIENCE_PATH}?"
        + urlencode(
            (
                ("menuCd", JEONGEUP_MUSEUM_EXPERIENCE_DETAIL_MENU),
                ("reUniqId", clean_identity),
            )
        )
    )


def _request_kind(method: Any, url: Any) -> str:
    if _clean(method).upper() != "GET":
        raise JeongeupMuseumExperienceContractError(
            "application/login/member/PII/attachment/download/POST endpoint refused"
        )
    try:
        parsed = urlparse(_clean(url))
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise JeongeupMuseumExperienceContractError("unsafe request URL") from exc
    common = bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == JEONGEUP_MUSEUM_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == JEONGEUP_MUSEUM_EXPERIENCE_PATH
        and not parsed.params
        and not parsed.fragment
    )
    if not common:
        raise JeongeupMuseumExperienceContractError("unsafe request URL")
    if pairs == [("menuCd", JEONGEUP_MUSEUM_EXPERIENCE_LIST_MENU)]:
        return "list"
    if (
        len(pairs) == 2
        and pairs[0] == ("menuCd", JEONGEUP_MUSEUM_EXPERIENCE_LIST_MENU)
        and pairs[1][0] == "startPage"
        and pairs[1][1].isdigit()
        and int(pairs[1][1]) > 1
    ):
        return "list"
    if (
        len(pairs) == 2
        and pairs[0] == ("menuCd", JEONGEUP_MUSEUM_EXPERIENCE_DETAIL_MENU)
        and pairs[1][0] == "reUniqId"
        and _IDENTITY_RE.fullmatch(pairs[1][1])
    ):
        return "detail"
    raise JeongeupMuseumExperienceContractError(
        "application/login/member/PII/attachment/download endpoint refused"
    )


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 municipal-course-crawler/1.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


class _Runner:
    def __init__(self, session_factory: SessionFactory, timeout: int) -> None:
        self.session_factory = session_factory
        self.timeout = timeout
        self.session: Any = None
        self.list_requests = 0
        self.detail_requests = 0
        self.post_requests = 0
        self.unsafe_endpoint_calls = 0

    def __enter__(self) -> "_Runner":
        self.session = self.session_factory()
        return self

    def __exit__(self, *_: Any) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()

    def soup(self, url: str) -> BeautifulSoup:
        kind = _request_kind("GET", url)
        response = self.session.get(
            url,
            timeout=self.timeout,
            allow_redirects=False,
            headers={"Referer": JEONGEUP_MUSEUM_EXPERIENCE_URL},
        )
        if kind == "list":
            self.list_requests += 1
        else:
            self.detail_requests += 1
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise JeongeupMuseumExperienceContractError(
                f"unexpected HTTP status {status}"
            )
        if tuple(getattr(response, "history", ()) or ()):
            raise JeongeupMuseumExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(
            str(key).lower() == "location" and _clean(value)
            for key, value in headers.items()
        ):
            raise JeongeupMuseumExperienceContractError(
                "redirect location is forbidden"
            )
        final_url = _clean(getattr(response, "url", ""))
        if final_url and final_url != url:
            raise JeongeupMuseumExperienceContractError("response URL drift")
        content_type = next(
            (
                _clean(value).lower()
                for key, value in headers.items()
                if str(key).lower() == "content-type"
            ),
            "",
        )
        if content_type and not content_type.startswith("text/html"):
            raise JeongeupMuseumExperienceContractError("non-HTML response refused")
        content = getattr(response, "content", None)
        if content is None:
            content = str(getattr(response, "text", "")).encode("utf-8")
        if not content or len(content) > JEONGEUP_MUSEUM_EXPERIENCE_MAX_HTML_BYTES:
            raise JeongeupMuseumExperienceContractError("invalid HTML response size")
        return BeautifulSoup(content, "html.parser")


def _unique_query(url: str) -> tuple[Any, dict[str, str]]:
    try:
        parsed = urlparse(url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise JeongeupMuseumExperienceContractError("malformed public link") from exc
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise JeongeupMuseumExperienceContractError("duplicate public-link key")
        values[key] = value
    return parsed, values


def _parse_period(value: Any, identity: str, label: str) -> tuple[date, date]:
    match = _DATE_RANGE_RE.fullmatch(_clean(value))
    if not match:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: invalid {label} period"
        )
    start, end = (date.fromisoformat(token) for token in match.groups())
    if end < start:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: reversed {label} period"
        )
    return start, end


def _parse_count(node: Any, label: str) -> int:
    text = _clean(node.get_text(" ", strip=True))
    if label not in text:
        raise JeongeupMuseumExperienceContractError(f"missing summary label {label}")
    match = _RESULT_COUNT_RE.search(text)
    if not match:
        raise JeongeupMuseumExperienceContractError("invalid result summary")
    return int(match.group(1).replace(",", ""))


def _parse_navigation_href(
    href: Any,
    *,
    expected_menu: str,
    expected_page: Optional[int] = None,
    allowed_order_sorts: frozenset[str] = frozenset({"asc"}),
) -> dict[str, str]:
    absolute = urljoin(JEONGEUP_MUSEUM_EXPERIENCE_URL, _clean(href))
    parsed, values = _unique_query(absolute)
    expected_keys = {
        "menuCd",
        "searchCondition",
        "searchKeyword",
        "orderField",
        "orderSort",
        "searchDateGubun",
        "startPage",
    }
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != JEONGEUP_MUSEUM_EXPERIENCE_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {
            JEONGEUP_MUSEUM_EXPERIENCE_PATH,
            JEONGEUP_MUSEUM_EXPERIENCE_LINK_PATH,
        }
        or set(values) != expected_keys
        or values.get("menuCd") != expected_menu
        or values.get("searchCondition") != "RE_NAME"
        or values.get("searchKeyword") != ""
        or values.get("orderField") != ""
        or values.get("orderSort") not in allowed_order_sorts
        or values.get("searchDateGubun") != "3"
        or not values.get("startPage", "").isdigit()
        or int(values["startPage"]) < 1
        or (
            expected_page is not None
            and int(values["startPage"]) != expected_page
        )
        or parsed.fragment
    ):
        raise JeongeupMuseumExperienceContractError("public navigation link drift")
    return values


def _parse_detail_href(href: Any, page: int) -> tuple[str, str]:
    absolute = urljoin(JEONGEUP_MUSEUM_EXPERIENCE_URL, _clean(href))
    parsed, values = _unique_query(absolute)
    expected_keys = {
        "menuCd",
        "reUniqId",
        "searchCondition",
        "searchKeyword",
        "orderField",
        "orderSort",
        "searchDateGubun",
        "startPage",
    }
    identity = values.get("reUniqId", "")
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != JEONGEUP_MUSEUM_EXPERIENCE_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {
            JEONGEUP_MUSEUM_EXPERIENCE_PATH,
            JEONGEUP_MUSEUM_EXPERIENCE_LINK_PATH,
        }
        or set(values) != expected_keys
        or values.get("menuCd") != JEONGEUP_MUSEUM_EXPERIENCE_DETAIL_MENU
        or not _IDENTITY_RE.fullmatch(identity)
        or values.get("searchCondition") != "RE_NAME"
        or values.get("searchKeyword") != ""
        or values.get("orderField") != ""
        or values.get("orderSort") != "asc"
        or values.get("searchDateGubun") != "3"
        or values.get("startPage") != str(page)
        or parsed.fragment
    ):
        raise JeongeupMuseumExperienceContractError("public detail link drift")
    return identity, jeongeup_museum_experience_detail_url(identity)


def _direct_text(node: Any) -> str:
    return _clean(
        " ".join(str(value) for value in node.find_all(string=True, recursive=False))
    )


def _parse_list_row(node: Any, page: int, position: int) -> dict[str, Any]:
    link = node.select_one('dl > dt > a[href*="reUniqId"]')
    if link is None:
        raise JeongeupMuseumExperienceContractError("list row lacks public detail")
    identity, detail_url = _parse_detail_href(link.get("href"), page)
    title = _clean(link.get_text(" ", strip=True))
    if not title:
        raise JeongeupMuseumExperienceContractError(f"{identity}: empty title")

    fields: dict[str, str] = {}
    labels: list[str] = []
    for item in node.select("dl > dd"):
        strong = item.find("strong", recursive=False)
        if strong is None:
            raise JeongeupMuseumExperienceContractError(
                f"{identity}: malformed list field"
            )
        label = _clean(strong.get_text(" ", strip=True))
        labels.append(label)
        if label in fields:
            raise JeongeupMuseumExperienceContractError(
                f"{identity}: duplicate list field"
            )
        fields[label] = _clean(
            " ".join(
                str(value)
                for value in item.find_all(string=True)
                if value.parent is not strong
            )
        )
    if tuple(labels) != _LIST_LABELS:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: list field vocabulary drift"
        )
    event_start, event_end = _parse_period(fields["교육기간"], identity, "event")
    apply_start, apply_end = _parse_period(
        fields["접수기간"], identity, "application"
    )
    venue = fields["교육장"]
    if not venue:
        raise JeongeupMuseumExperienceContractError(f"{identity}: empty venue")

    status_node = node.select_one("p.rec")
    if status_node is None:
        raise JeongeupMuseumExperienceContractError(f"{identity}: missing status")
    source_status = _direct_text(status_node)
    if source_status not in _STATUS_MAP:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: unknown source status {source_status!r}"
        )
    if frozenset(status_node.get("class") or ()) != _STATUS_CLASSES[source_status]:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: status class drift"
        )
    capacity_node = status_node.find("span")
    capacity_match = _CAPACITY_RE.fullmatch(
        _clean(capacity_node.get_text(" ", strip=True) if capacity_node else "")
    )
    if not capacity_match:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: invalid list capacity"
        )
    capacity_current, capacity_total = (
        int(token.replace(",", "")) for token in capacity_match.groups()
    )

    controls = node.select("a.possible, button.possible")
    if len(controls) != 1:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: list control count drift"
        )
    control = controls[0]
    control_classes = frozenset(control.get("class") or ())
    control_text = _clean(control.get_text(" ", strip=True))
    open_control = source_status == "온라인 접수중"
    if open_control:
        if (
            control.name != "a"
            or control_classes
            != frozenset({"possible", "possible01", "blink"})
            or control_text != "행사신청"
            or _clean(control.get("onclick"))
        ):
            raise JeongeupMuseumExperienceContractError(
                f"{identity}: open list control drift"
            )
        control_identity, _ = _parse_detail_href(control.get("href"), page)
        if control_identity != identity:
            raise JeongeupMuseumExperienceContractError(
                f"{identity}: list control identity drift"
            )
    else:
        expected_text = "접수대기" if source_status == "계획중" else "접수마감"
        if (
            control_classes != frozenset({"possible", "possible02"})
            or control_text != expected_text
            or _clean(control.get("href"))
            or _clean(control.get("onclick"))
        ):
            raise JeongeupMuseumExperienceContractError(
                f"{identity}: inactive list control drift"
            )

    return {
        "identity": identity,
        "title": title,
        "page": page,
        "position": position,
        "detail_url": detail_url,
        "event_start": event_start,
        "event_end": event_end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "venue": venue,
        "source_status": source_status,
        "status": _STATUS_MAP[source_status],
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "list_application_control": open_control,
    }


def _parse_list_page(soup: BeautifulSoup, requested_page: int) -> dict[str, Any]:
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if page_title != "문화체험 > 정읍시립박물관":
        raise JeongeupMuseumExperienceContractError("museum list title drift")
    heading = soup.select_one("#content h3")
    if heading is None or _clean(heading.get_text(" ", strip=True)) != (
        JEONGEUP_MUSEUM_EXPERIENCE_BRANCH
    ):
        raise JeongeupMuseumExperienceContractError("museum list heading drift")
    form = soup.select_one('form[name="listForm"]')
    if form is None:
        raise JeongeupMuseumExperienceContractError("museum list form missing")
    if (
        _clean(form.get("method")).lower() != "get"
        or _clean(form.get("action")) != JEONGEUP_MUSEUM_EXPERIENCE_LINK_PATH
    ):
        raise JeongeupMuseumExperienceContractError("museum list form drift")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select('input[type="hidden"][name]')
    }
    if hidden != {
        "menuCd": JEONGEUP_MUSEUM_EXPERIENCE_LIST_MENU,
        "startPage": str(requested_page),
        "searchCondition": "RE_NAME",
        "orderField": "",
        "searchDateGubun": "3",
    }:
        raise JeongeupMuseumExperienceContractError("museum list hidden filter drift")
    keyword = form.select_one('input[name="searchKeyword"]')
    category = form.select_one('select[name="lectureType"]')
    options = tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in category.select("option")
    ) if category else ()
    if keyword is None or _clean(keyword.get("value")) or options != (("", "선택"),):
        raise JeongeupMuseumExperienceContractError("museum list filters drift")
    buttons = tuple(
        (_clean(button.get_text(" ", strip=True)), _clean(button.get("onclick")))
        for button in form.select("ul.btn_condition button")
    )
    if buttons != (("전체", "searchDatefunc('3')"), ("접수중", "searchDatefunc('1')")):
        raise JeongeupMuseumExperienceContractError("museum status filters drift")

    summary = soup.select("ul.search_result > li")
    if len(summary) != 3:
        raise JeongeupMuseumExperienceContractError("museum result summary drift")
    open_count = _parse_count(summary[0], "모집중")
    closed_count = _parse_count(summary[1], "마감")
    advertised_total = _parse_count(summary[2], "검색된 결과")
    if advertised_total < 1:
        raise JeongeupMuseumExperienceContractError("museum ledger unexpectedly empty")

    pager = soup.select_one("div.bbs_page")
    if pager is None:
        raise JeongeupMuseumExperienceContractError("museum pager missing")
    advertised_pages = [
        int(
            _parse_navigation_href(
                anchor.get("href"),
                expected_menu=JEONGEUP_MUSEUM_EXPERIENCE_LIST_MENU,
            )["startPage"]
        )
        for anchor in pager.select("a[href]")
    ]
    if not advertised_pages:
        raise JeongeupMuseumExperienceContractError("museum pager has no links")
    advertised_last = max(advertised_pages)
    calculated_last = max(
        1,
        (
            advertised_total + JEONGEUP_MUSEUM_EXPERIENCE_PAGE_SIZE - 1
        )
        // JEONGEUP_MUSEUM_EXPERIENCE_PAGE_SIZE,
    )
    if advertised_last != calculated_last:
        raise JeongeupMuseumExperienceContractError("museum total/page disagreement")

    current_node = pager.select_one("span.on")
    current_page: Optional[int] = None
    if current_node is not None:
        current_text = _clean(current_node.get_text(" ", strip=True))
        if not current_text.isdigit():
            raise JeongeupMuseumExperienceContractError("current-page marker drift")
        current_page = int(current_text)
    items = soup.select("div.bbs_list01 > ul > li")
    if not items or len(items) > JEONGEUP_MUSEUM_EXPERIENCE_PAGE_SIZE:
        raise JeongeupMuseumExperienceContractError("museum page row boundary drift")
    rows = [
        _parse_list_row(item, requested_page, position)
        for position, item in enumerate(items, 1)
    ]
    return {
        "requested_page": requested_page,
        "current_page": current_page,
        "open_count": open_count,
        "closed_count": closed_count,
        "advertised_total": advertised_total,
        "advertised_last": advertised_last,
        "rows": rows,
    }


def _page_signature(page: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        int(page["advertised_total"]),
        int(page["advertised_last"]),
        int(page["open_count"]),
        int(page["closed_count"]),
        tuple(
            (
                row["identity"],
                row["title"],
                row["event_start"].isoformat(),
                row["event_end"].isoformat(),
                row["apply_start"].isoformat(),
                row["apply_end"].isoformat(),
                row["venue"],
                row["source_status"],
                row["capacity_current"],
                row["capacity_total"],
                row["list_application_control"],
            )
            for row in page["rows"]
        ),
    )


def _detail_fields(soup: BeautifulSoup, identity: str) -> dict[str, str]:
    table = soup.select_one("div.edu_view01 table.view_table")
    if table is None:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: public detail table missing"
        )
    fields: dict[str, str] = {}
    labels: list[str] = []
    for row in table.select("tbody > tr"):
        label_node = row.find("th", recursive=False)
        value_node = row.find("td", recursive=False)
        if label_node is None or value_node is None:
            raise JeongeupMuseumExperienceContractError(
                f"{identity}: malformed public detail field"
            )
        label = _clean(label_node.get_text(" ", strip=True))
        labels.append(label)
        if label in fields:
            raise JeongeupMuseumExperienceContractError(
                f"{identity}: duplicate public detail field"
            )
        fields[label] = _clean(value_node.get_text(" ", strip=True))
    if tuple(labels) != _DETAIL_LABELS:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: public detail vocabulary drift"
        )
    return fields


def _detail_control(soup: BeautifulSoup, listed: Mapping[str, Any]) -> bool:
    identity = str(listed["identity"])
    apply_container = soup.select_one("div.edu_view01 div.btn > p.btn_apply")
    back = soup.select_one("div.edu_view01 div.btn > p.btn_back > a[href]")
    if apply_container is None or back is None:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: public detail controls missing"
        )
    back_values = _parse_navigation_href(
        back.get("href"),
        expected_menu=JEONGEUP_MUSEUM_EXPERIENCE_LIST_MENU,
        allowed_order_sorts=frozenset({"asc", "desc"}),
    )
    if int(back_values["startPage"]) not in {1, int(listed["page"])}:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: public back control drift"
        )
    controls = apply_container.find_all(["button", "a"], recursive=False)
    if len(controls) != 1:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: apply control count drift"
        )
    control = controls[0]
    status = str(listed["status"])
    if status == "OPEN":
        if (
            control.name != "button"
            or _clean(control.get_text(" ", strip=True)) != "신청하기"
            or not _WRITE_FUNC_RE.fullmatch(_clean(control.get("onclick")))
            or _clean(control.get("href"))
            or _clean(control.get("formaction"))
        ):
            raise JeongeupMuseumExperienceContractError(
                f"{identity}: open inline control drift"
            )
        return True
    expected_text = "접수대기" if status == "SCHEDULED" else "접수마감"
    if (
        _clean(control.get_text(" ", strip=True)) != expected_text
        or _clean(control.get("href"))
        or _clean(control.get("onclick"))
        or _clean(control.get("formaction"))
    ):
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: inactive inline control drift"
        )
    return False


def _fee_amount(value: str) -> Optional[int]:
    text = _clean(value)
    if text in {"무료", "없음", "0", "0원"}:
        return 0
    match = re.fullmatch(r"(\d[\d,]*)\s*원", text)
    return int(match.group(1).replace(",", "")) if match else None


def _parse_detail(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
    cutoff: date,
) -> dict[str, Any]:
    identity = str(listed["identity"])
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if page_title != "문화체험 > 정읍시립박물관 > 신청하기":
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: public detail title drift"
        )
    title_node = soup.select_one("div.edu_view01 h4")
    if title_node is None or _clean(title_node.get_text(" ", strip=True)) != (
        listed["title"]
    ):
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: list/detail identity drift"
        )
    fields = _detail_fields(soup, identity)
    apply_start, apply_end = _parse_period(
        fields["접수기간"], identity, "application"
    )
    event_start, event_end = _parse_period(fields["행사기간"], identity, "event")
    if (
        apply_start != listed["apply_start"]
        or apply_end != listed["apply_end"]
        or event_start != listed["event_start"]
        or event_end != listed["event_end"]
        or fields["행사장"] != listed["venue"]
        or fields["접수상태"] != listed["source_status"]
    ):
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: list/detail field drift"
        )
    capacity_match = _CAPACITY_RE.fullmatch(fields["신청/정원"])
    if not capacity_match:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: invalid detail capacity"
        )
    capacity_current, capacity_total = (
        int(token.replace(",", "")) for token in capacity_match.groups()
    )
    if (
        capacity_current != listed["capacity_current"]
        or capacity_total != listed["capacity_total"]
    ):
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: list/detail capacity drift"
        )
    status = str(listed["status"])
    if status == "SCHEDULED" and not cutoff < apply_start:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: scheduled date/status drift"
        )
    if status == "OPEN" and not apply_start <= cutoff <= apply_end:
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: open date/status drift"
        )
    control_present = _detail_control(soup, listed)
    if control_present != bool(listed["list_application_control"]):
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: list/detail application control drift"
        )
    schedule = fields["행사시간"]
    venue = fields["행사장"]
    target = fields["행사대상"]
    fee = fields["수강료/재료비"]
    safe_values = (str(listed["title"]), schedule, venue, target, fee)
    if not all(safe_values) or any(
        _PHONE_RE.search(value)
        or _EMAIL_RE.search(value)
        or _RESIDENT_ID_RE.search(value)
        for value in safe_values
    ):
        raise JeongeupMuseumExperienceContractError(
            f"{identity}: unsafe or empty allowlisted detail field"
        )

    event_period = f"{event_start.isoformat()} ~ {event_end.isoformat()}"
    apply_period = f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
    detail_url = str(listed["detail_url"])
    return {
        "provider": JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER,
        "provider_course_id": (
            f"{JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER}:experience:{identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "source_course_id": f"experience:{identity}",
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": JEONGEUP_MUSEUM_EXPERIENCE_BRANCH,
        "branch_code": "JEONGEUP_MUNICIPAL_MUSEUM",
        "branch_url": JEONGEUP_MUSEUM_EXPERIENCE_URL,
        "preserve_branch": True,
        "category": "체험·견학",
        "program_type": "체험",
        "program_type_source": "official_culture_experience_ledger",
        "raw_url": detail_url,
        "source_url": detail_url,
        "application_url": detail_url if control_present else "",
        "application_type": "ONLINE_RESERVATION" if control_present else "INFO_ONLY",
        "application_method": "온라인",
        "application_methods": ["온라인"],
        "reservation_available": control_present,
        "status": status,
        "course_status": status,
        "raw_status": str(listed["source_status"]),
        "source_status": str(listed["source_status"]),
        "fee": fee,
        "fee_amount": _fee_amount(fee),
        "period": event_period,
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": apply_period,
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "schedule_raw": schedule,
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_remaining": max(capacity_total - capacity_current, 0),
        "target": target,
        "target_audience": target,
        "venue": venue,
        "venue_name": venue,
        "room": venue,
        "facility_name": JEONGEUP_MUSEUM_EXPERIENCE_BRANCH,
        "address": "",
        "venue_address": "",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "classification_locked": True,
        "collection_type": JEONGEUP_MUSEUM_EXPERIENCE_PARSER,
        "municipality_code": JEONGEUP_MUSEUM_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": JEONGEUP_MUSEUM_EXPERIENCE_MUNICIPALITY_NAME,
        "municipality_full_name": JEONGEUP_MUSEUM_EXPERIENCE_MUNICIPALITY_NAME,
        "region": JEONGEUP_MUSEUM_EXPERIENCE_MUNICIPALITY_NAME,
        "sido": "전북특별자치도",
        "sigungu": "정읍시",
        "raw_fields": {
            "parser": JEONGEUP_MUSEUM_EXPERIENCE_PARSER,
            "identity": identity,
            "source_page": int(listed["page"]),
            "source_position": int(listed["position"]),
            "source_status": str(listed["source_status"]),
            "source_apply_period": apply_period,
            "source_event_period": event_period,
            "source_schedule": schedule,
            "source_target": target,
            "source_venue": venue,
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "detail_verified": True,
            "application_control_present": control_present,
            "application_control_verified": True,
            "application_control_executed": False,
            "application_endpoint_fetched": False,
            "reservation_lookup_endpoint_fetched": False,
            "attachment_download_endpoint_fetched": False,
            "discarded_detail_fields": list(_DISCARDED_DETAIL_FIELDS),
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
            if key not in {"raw_url", "source_url", "application_url", "branch_url"}
        }
    )
    if (
        _PHONE_RE.search(payload)
        or _EMAIL_RE.search(payload)
        or _RESIDENT_ID_RE.search(payload)
    ):
        errors.append("PII persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail persisted")
    if row.get("address") or row.get("venue_address"):
        errors.append("unverified address persisted")
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
        "municipality_code": JEONGEUP_MUSEUM_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_full_name": JEONGEUP_MUSEUM_EXPERIENCE_MUNICIPALITY_NAME,
        "owner_provider": JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER,
        "canonical_candidate_id": JEONGEUP_MUSEUM_EXPERIENCE_CANDIDATE_ID,
        "canonical_url": JEONGEUP_MUSEUM_EXPERIENCE_URL,
        "canonical_url_sha256": JEONGEUP_MUSEUM_EXPERIENCE_URL_SHA256,
        "ownership_scope": JEONGEUP_MUSEUM_EXPERIENCE_OWNERSHIP_SCOPE,
        "parser": JEONGEUP_MUSEUM_EXPERIENCE_PARSER,
        "live_audit_baseline": dict(JEONGEUP_MUSEUM_EXPERIENCE_LIVE_BASELINE),
        "sibling_audit": {
            key: dict(value)
            for key, value in JEONGEUP_MUSEUM_EXPERIENCE_SIBLING_AUDIT.items()
        },
        "page_size": JEONGEUP_MUSEUM_EXPERIENCE_PAGE_SIZE,
        "source_total": 0,
        "source_rows": 0,
        "data_pages": 0,
        "post_last_page": 0,
        "current_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "physical_requests": 0,
        "boundary_rechecks": 0,
        "post_last_clamp_verified": False,
        "stable_first_last_overflow": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_current": 0,
        "application_controls_executed": 0,
        "application_endpoint_requests": 0,
        "reservation_lookup_endpoint_requests": 0,
        "login_auth_member_applicant_pii_endpoint_requests": 0,
        "attachment_download_endpoint_requests": 0,
        "post_requests": 0,
        "unsafe_endpoint_calls": 0,
        "sibling_pages_requested": 0,
        "privacy_violations": 0,
        "semantic_duplicate_count": 0,
        "source_cap_reached": False,
        "no_current_data": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "configured_collection_error": "",
    }


def collect_jeongeup_museum_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = JEONGEUP_MUSEUM_EXPERIENCE_RECOMMENDED_MAX_PAGES,
    detail_limit: int = JEONGEUP_MUSEUM_EXPERIENCE_RECOMMENDED_DETAIL_LIMIT,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic, current Jeongeup museum experience snapshot."""

    meta = _initial_meta()
    if not is_jeongeup_museum_experience_target(target):
        meta["configured_collection_error"] = (
            "target does not match exact Jeongeup museum experience owner"
        )
        return [], JEONGEUP_MUSEUM_EXPERIENCE_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = (
                "managed session_factory injection is required"
            )
            return [], JEONGEUP_MUSEUM_EXPERIENCE_PARSER, meta
        session_factory = _default_session_factory
    try:
        timeout_value = _positive_int(timeout, "timeout")
        max_page_value = _positive_int(max_pages, "max_pages")
        if isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise JeongeupMuseumExperienceContractError(
                "detail_limit must be non-negative"
            )
        detail_limit_value = int(detail_limit)
        cutoff = _cutoff(today)
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], JEONGEUP_MUSEUM_EXPERIENCE_PARSER, meta

    try:
        with _Runner(session_factory, timeout_value) as runner:
            first = _parse_list_page(
                runner.soup(jeongeup_museum_experience_list_url(1)), 1
            )
            last = int(first["advertised_last"])
            total = int(first["advertised_total"])
            if first["current_page"] != 1:
                raise JeongeupMuseumExperienceContractError(
                    "canonical list did not render page one"
                )
            if last > max_page_value:
                meta["source_cap_reached"] = True
                raise JeongeupMuseumExperienceContractError(
                    f"advertised pages {last} exceed max_pages {max_page_value}"
                )
            pages = [first]
            for page_number in range(2, last + 1):
                pages.append(
                    _parse_list_page(
                        runner.soup(
                            jeongeup_museum_experience_list_url(page_number)
                        ),
                        page_number,
                    )
                )
            for page_number, page in enumerate(pages, 1):
                if (
                    page["current_page"] != page_number
                    or int(page["advertised_total"]) != total
                    or int(page["advertised_last"]) != last
                    or int(page["open_count"]) != int(first["open_count"])
                    or int(page["closed_count"]) != int(first["closed_count"])
                ):
                    raise JeongeupMuseumExperienceContractError(
                        "advertised pagination contract drift"
                    )
            if any(
                len(page["rows"]) != JEONGEUP_MUSEUM_EXPERIENCE_PAGE_SIZE
                for page in pages[:-1]
            ):
                raise JeongeupMuseumExperienceContractError(
                    "non-final museum page is not full"
                )
            if not 1 <= len(pages[-1]["rows"]) <= (
                JEONGEUP_MUSEUM_EXPERIENCE_PAGE_SIZE
            ):
                raise JeongeupMuseumExperienceContractError(
                    "final museum page boundary drift"
                )
            if (
                (last - 1) * JEONGEUP_MUSEUM_EXPERIENCE_PAGE_SIZE
                + len(pages[-1]["rows"])
                != total
            ):
                raise JeongeupMuseumExperienceContractError(
                    "museum total/page arithmetic drift"
                )

            overflow_page = last + 1
            overflow = _parse_list_page(
                runner.soup(jeongeup_museum_experience_list_url(overflow_page)),
                overflow_page,
            )
            if (
                overflow["current_page"] is not None
                or _page_signature(overflow) != _page_signature(pages[-1])
            ):
                raise JeongeupMuseumExperienceContractError(
                    "post-last museum page did not clamp exactly"
                )
            listed = [row for page in pages for row in page["rows"]]
            identities = [str(row["identity"]) for row in listed]
            if len(listed) != total or len(identities) != len(set(identities)):
                raise JeongeupMuseumExperienceContractError(
                    "museum RE identity set incomplete or duplicated"
                )
            if Counter(str(row["source_status"]) for row in listed)[
                "온라인 접수중"
            ] != int(first["open_count"]):
                raise JeongeupMuseumExperienceContractError(
                    "museum open summary/status census drift"
                )
            for item in listed:
                if item["source_status"] == "행사종료" and not (
                    item["event_end"] < cutoff
                ):
                    raise JeongeupMuseumExperienceContractError(
                        f"{item['identity']}: ended status/date drift"
                    )
            current = [item for item in listed if item["event_end"] >= cutoff]
            if len(current) > detail_limit_value:
                meta["source_cap_reached"] = True
                raise JeongeupMuseumExperienceContractError(
                    f"detail_limit {detail_limit_value} below required {len(current)}"
                )

            rows = [
                _parse_detail(runner.soup(str(item["detail_url"])), item, cutoff)
                for item in current
            ]
            first_recheck = _parse_list_page(
                runner.soup(jeongeup_museum_experience_list_url(1)), 1
            )
            last_recheck = _parse_list_page(
                runner.soup(jeongeup_museum_experience_list_url(last)), last
            )
            overflow_recheck = _parse_list_page(
                runner.soup(jeongeup_museum_experience_list_url(overflow_page)),
                overflow_page,
            )
            if (
                _page_signature(first_recheck) != _page_signature(pages[0])
                or _page_signature(last_recheck) != _page_signature(pages[-1])
                or _page_signature(overflow_recheck) != _page_signature(overflow)
            ):
                raise JeongeupMuseumExperienceContractError(
                    "museum first/last/overflow changed during collection"
                )

            rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
            rows = list((dedupe_rows or _default_dedupe)(rows))
            expected_ids = {
                f"{JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER}:experience:{item['identity']}"
                for item in current
            }
            if len(rows) != len(current) or {
                str(row.get("provider_course_id")) for row in rows
            } != expected_ids:
                raise JeongeupMuseumExperienceContractError(
                    "dedupe changed complete current RE identity set"
                )
            privacy_errors = [
                error for row in rows for error in _privacy_errors(row)
            ]
            if privacy_errors:
                meta["privacy_violations"] = len(privacy_errors)
                raise JeongeupMuseumExperienceContractError(
                    "; ".join(privacy_errors[:5])
                )
            semantic_counts = Counter(
                (
                    _clean(row["title"]).casefold(),
                    _clean(row["start_date"]),
                    _clean(row["schedule_raw"]),
                    _clean(row["venue_name"]),
                )
                for row in rows
            )
            semantic_duplicates = sum(
                count - 1 for count in semantic_counts.values() if count > 1
            )
            if semantic_duplicates:
                meta["semantic_duplicate_count"] = semantic_duplicates
                raise JeongeupMuseumExperienceContractError(
                    "semantic duplicate current museum programmes"
                )
            meta.update(
                {
                    "cutoff": cutoff.isoformat(),
                    "source_total": total,
                    "source_rows": len(listed),
                    "source_identity_numeric_min": min(
                        int(identity.removeprefix("RE")) for identity in identities
                    ),
                    "source_identity_numeric_max": max(
                        int(identity.removeprefix("RE")) for identity in identities
                    ),
                    "source_status_counts": dict(
                        Counter(str(row["source_status"]) for row in listed)
                    ),
                    "data_pages": last,
                    "page_counts": [len(page["rows"]) for page in pages],
                    "post_last_page": overflow_page,
                    "current_count": len(current),
                    "expired_count": len(listed) - len(current),
                    "current_source_status_counts": dict(
                        Counter(str(row["source_status"]) for row in current)
                    ),
                    "status_counts": dict(
                        Counter(str(row["status"]) for row in rows)
                    ),
                    "returned_count": len(rows),
                    "list_requests": runner.list_requests,
                    "detail_pages": runner.detail_requests,
                    "physical_requests": runner.list_requests
                    + runner.detail_requests,
                    "boundary_rechecks": 3,
                    "post_last_clamp_verified": True,
                    "stable_first_last_overflow": True,
                    "pagination_complete": True,
                    "details_complete": runner.detail_requests == len(current),
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
            return rows, JEONGEUP_MUSEUM_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], JEONGEUP_MUSEUM_EXPERIENCE_PARSER, meta


collect = collect_jeongeup_museum_experience


__all__ = [
    "JEONGEUP_MUSEUM_EXPERIENCE_BRANCH",
    "JEONGEUP_MUSEUM_EXPERIENCE_CANDIDATE_ID",
    "JEONGEUP_MUSEUM_EXPERIENCE_LIVE_BASELINE",
    "JEONGEUP_MUSEUM_EXPERIENCE_MUNICIPALITY_CODE",
    "JEONGEUP_MUSEUM_EXPERIENCE_MUNICIPALITY_NAME",
    "JEONGEUP_MUSEUM_EXPERIENCE_OWNERSHIP_SCOPE",
    "JEONGEUP_MUSEUM_EXPERIENCE_PARSER",
    "JEONGEUP_MUSEUM_EXPERIENCE_PROVIDER",
    "JEONGEUP_MUSEUM_EXPERIENCE_SIBLING_AUDIT",
    "JEONGEUP_MUSEUM_EXPERIENCE_URL",
    "JeongeupMuseumExperienceContractError",
    "collect",
    "collect_jeongeup_museum_experience",
    "is_jeongeup_museum_experience_target",
    "is_target",
    "jeongeup_museum_experience_detail_url",
    "jeongeup_museum_experience_list_url",
]
