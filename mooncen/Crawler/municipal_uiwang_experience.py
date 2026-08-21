"""Fail-closed collector for Uiwang City's official experience ledger.

The ``EXP/X01`` catalogue is the first-party ``체험·캠프`` owner.  It is
separate from the education fan-out rooted at the reservation homepage and
must therefore keep its own provider identity and locked experience service
group.

Only the public list and public detail documents are requested.  Login,
application, applicant, identity-verification, cancellation and personal
reservation endpoints are never called.  A complete snapshot requires every
declared data page, an exactly empty post-last page, a stable page-one
recheck, and every current/future public detail.  Any contract drift returns
no rows.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


UIWANG_EXPERIENCE_PROVIDER = "MUNI_WWW_UIWANG_GO_KR_322A3D6C"
UIWANG_EXPERIENCE_URL = (
    "https://www.uiwang.go.kr/reserve/EXP/X01/eduList.do?currentMenuNo=473"
)
UIWANG_EXPERIENCE_LIST_ENDPOINT = (
    "https://www.uiwang.go.kr/reserve/EXP/X01/eduList.do"
)
UIWANG_EXPERIENCE_DETAIL_ENDPOINT = (
    "https://www.uiwang.go.kr/reserve/EXP/X01/eduView.do"
)
UIWANG_EXPERIENCE_MUNICIPALITY_CODE = "4143000000"
UIWANG_EXPERIENCE_MUNICIPALITY_NAME = "경기도 의왕시"
UIWANG_EXPERIENCE_PARSER = (
    "uiwang_official_experience_declared_total+all_pages+exact_empty_sentinel+"
    "stable_first_page+all_current_details+source_bound_application_control+"
    "pii_allowlist"
)
UIWANG_EXPERIENCE_OWNERSHIP_SCOPE = (
    "uiwang_integrated_reservation_exp_x01_complete_experience_ledger"
)
UIWANG_EXPERIENCE_LIVE_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_total": 43,
    "data_pages": 6,
    "sentinel_page": 7,
    "current_count": 4,
    "open_count": 3,
}

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_RESERVATION_ID_RE = re.compile(r"RESR_[0-9]{15}")
_VIEW_CALL_RE = re.compile(r"^fnView\(\s*['\"](RESR_[0-9]{15})['\"]\s*\)\s*;?$")
_ADDRESS_RE = re.compile(
    r"(?:(?:경기|경기도)\s+)?의왕시\s+"
    r"(?P<road>[가-힣0-9·]+(?:로|길)\s+\d+(?:-\d+)?)"
)
_STATUS = {
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "교육종료": "CLOSED",
}
_LIST_REQUIRED_FIELDS = frozenset(
    {"접수기간", "체험캠프기간", "요일", "대상", "사용료", "위치", "신청/정원"}
)
_DETAIL_REQUIRED_FIELDS = frozenset(
    {
        "유형",
        "체험캠프기간",
        "체험캠프시간",
        "체험캠프요일",
        "체험캠프장소",
        "읍면동",
        "대상",
        "사용료",
        "예약방식",
        "기관/부서",
        "모집정원",
    }
)
# One immutable, visibly labelled test shell remains in the public archive.
# Its dates and capacity are blank/zero, so it is accounted for in the
# declared source boundary but can never become a returned programme.
_EXPLICIT_TEST_RECORDS: Mapping[str, str] = {
    "RESR_000000000010989": "테스트",
}


class UiwangExperienceContractError(ValueError):
    """Raised when the audited first-party public contract changes."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider")).upper()


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


def _positive(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise UiwangExperienceContractError(
            f"{name} must be a positive integer"
        ) from exc
    if result < 1:
        raise UiwangExperienceContractError(f"{name} must be a positive integer")
    return result


def _exact_target_url(value: str) -> bool:
    got = urlparse(value)
    wanted = urlparse(UIWANG_EXPERIENCE_URL)
    return bool(
        got.scheme == "https"
        and got.hostname == wanted.hostname
        and got.port is None
        and got.path == wanted.path
        and parse_qs(got.query, keep_blank_values=True)
        == parse_qs(wanted.query, keep_blank_values=True)
        and not got.params
        and not got.fragment
        and not got.username
        and not got.password
    )


def is_uiwang_experience_target(target: Any) -> bool:
    return _provider(target) == UIWANG_EXPERIENCE_PROVIDER and _exact_target_url(
        _target_url(target)
    )


is_target = is_uiwang_experience_target


def uiwang_experience_list_url(page: int) -> str:
    page_number = _positive(page, "page")
    return f"{UIWANG_EXPERIENCE_LIST_ENDPOINT}?{urlencode({'currentMenuNo': '473', 'pageIndex': page_number})}"


def uiwang_experience_detail_url(reservation_id: str) -> str:
    identity = _clean(reservation_id)
    if not _RESERVATION_ID_RE.fullmatch(identity):
        raise UiwangExperienceContractError("invalid reservation identity")
    return f"{UIWANG_EXPERIENCE_DETAIL_ENDPOINT}?{urlencode({'currentMenuNo': '473', 'resrId': identity})}"


def _validate_public_url(value: str) -> tuple[str, str]:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.uiwang.go.kr"
        or parsed.port is not None
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise UiwangExperienceContractError("request escaped the audited public host")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == urlparse(UIWANG_EXPERIENCE_LIST_ENDPOINT).path:
        if set(query) != {"currentMenuNo", "pageIndex"}:
            raise UiwangExperienceContractError("unexpected list query")
        if query["currentMenuNo"] != ["473"]:
            raise UiwangExperienceContractError("wrong experience menu identity")
        _positive(query["pageIndex"][0], "pageIndex")
        return "list", parsed.path
    if parsed.path == urlparse(UIWANG_EXPERIENCE_DETAIL_ENDPOINT).path:
        if set(query) != {"currentMenuNo", "resrId"}:
            raise UiwangExperienceContractError("unexpected detail query")
        if query["currentMenuNo"] != ["473"]:
            raise UiwangExperienceContractError("wrong experience menu identity")
        if not _RESERVATION_ID_RE.fullmatch(query["resrId"][0]):
            raise UiwangExperienceContractError("invalid detail identity")
        return "detail", parsed.path
    raise UiwangExperienceContractError("private or unrelated endpoint blocked")


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


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_soup(response: Any, expected_url: str, kind: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise UiwangExperienceContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise UiwangExperienceContractError("redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url:
        final = urlparse(final_url)
        expected = urlparse(expected_url)
        final_path = final.path.split(";jsessionid", 1)[0]
        if (
            final.scheme != "https"
            or final.hostname != expected.hostname
            or final_path != expected.path
        ):
            raise UiwangExperienceContractError(
                "response escaped the audited public endpoint"
            )
    content = getattr(response, "content", b"")
    if content:
        text = bytes(content).decode("utf-8", errors="replace")
    else:
        text = str(getattr(response, "text", "") or "")
    if not text:
        raise UiwangExperienceContractError("empty public response")
    soup = BeautifulSoup(text, "lxml")
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "체험·캠프" not in title or "의왕시 통합예약시스템" not in title:
        raise UiwangExperienceContractError(f"{kind} page title changed")
    subtitle = soup.select_one("#conArea .subTit")
    if _clean(subtitle.get_text(" ", strip=True) if subtitle else "") != "체험·캠프":
        raise UiwangExperienceContractError(f"{kind} menu identity changed")
    return soup


class _Runner:
    def __init__(self, session_factory: SessionFactory, timeout: int):
        self._session_factory = session_factory
        self._timeout = _positive(timeout, "timeout")
        self._session: Any = None
        self.requests = 0

    def __enter__(self) -> "_Runner":
        self._session = self._session_factory()
        if self._session is None:
            raise UiwangExperienceContractError("session factory returned no session")
        return self

    def __exit__(self, *_args: Any) -> None:
        _close(self._session)

    def soup(self, url: str, *, referer: str = "") -> BeautifulSoup:
        kind, _path = _validate_public_url(url)
        headers = {"Referer": referer} if referer else None
        response = self._session.get(
            url,
            timeout=self._timeout,
            allow_redirects=False,
            headers=headers,
        )
        self.requests += 1
        return _response_soup(response, url, kind)


def _pairs(parent: Tag, selector: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in parent.select(selector):
        label_node = node.select_one(".em")
        label = _clean(label_node.get_text(" ", strip=True) if label_node else "")
        if not label or label in result:
            raise UiwangExperienceContractError("ambiguous labelled field")
        clone = BeautifulSoup(str(node), "lxml")
        clone_label = clone.select_one(".em")
        if clone_label:
            clone_label.decompose()
        result[label] = _clean(clone.get_text(" ", strip=True))
    return result


def _dates(value: str, field: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise UiwangExperienceContractError(f"{field} must contain one date range")
    values = tuple(date(int(year), int(month), int(day)) for year, month, day in matches)
    if values[1] < values[0]:
        raise UiwangExperienceContractError(f"{field} is reversed")
    return values[0], values[1]


def _capacity_pair(value: str) -> tuple[int, int]:
    numbers = [int(item.replace(",", "")) for item in re.findall(r"\d[\d,]*", _clean(value))]
    if len(numbers) != 2 or numbers[0] < 0 or numbers[1] < 1:
        raise UiwangExperienceContractError("invalid 신청/정원 contract")
    return numbers[0], numbers[1]


def _declared_total(soup: BeautifulSoup) -> int:
    totals: set[int] = set()
    for node in soup.select(".listTop .total, #conArea .total"):
        text = _clean(node.get_text(" ", strip=True))
        match = re.search(r"([\d,]+)\s*건", text)
        if match:
            totals.add(int(match.group(1).replace(",", "")))
    if len(totals) != 1:
        raise UiwangExperienceContractError("missing unambiguous declared total")
    return totals.pop()


def _list_rows(soup: BeautifulSoup) -> list[dict[str, Any]]:
    cards = soup.select("ul.album.reserv > li, ul.blog.reserv > li")
    rows: list[dict[str, Any]] = []
    for card in cards:
        view_links = card.select("a[onclick*='fnView']")
        if len(view_links) != 1:
            raise UiwangExperienceContractError("card detail identity changed")
        call = _clean(view_links[0].get("onclick"))
        identity_match = _VIEW_CALL_RE.fullmatch(call)
        title_nodes = card.select(".txtW .tit")
        status_nodes = card.select(".label")
        if not identity_match or len(title_nodes) != 1 or len(status_nodes) != 1:
            raise UiwangExperienceContractError("card structure changed")
        identity = identity_match.group(1)
        title = _clean(title_nodes[0].get_text(" ", strip=True))
        status_raw = _clean(status_nodes[0].get_text(" ", strip=True))
        if not title or status_raw not in _STATUS:
            raise UiwangExperienceContractError("card title/status contract changed")
        pairs = _pairs(card, ".txtW .etc > li")
        if not _LIST_REQUIRED_FIELDS.issubset(pairs):
            raise UiwangExperienceContractError("card labelled fields changed")
        if _EXPLICIT_TEST_RECORDS.get(identity) == title:
            rows.append(
                {
                    "provider": UIWANG_EXPERIENCE_PROVIDER,
                    "provider_course_id": f"uiwang-experience:{identity}",
                    "title": title,
                    "end_date": "",
                    "raw_fields": {
                        "parser": UIWANG_EXPERIENCE_PARSER,
                        "reservation_id": identity,
                        "status_raw": status_raw,
                        "list_contract": dict(pairs),
                        "explicit_non_program": True,
                        "non_program_reason": "test",
                    },
                }
            )
            continue
        apply_start, apply_end = _dates(pairs["접수기간"], "접수기간")
        start, end = _dates(pairs["체험캠프기간"], "체험캠프기간")
        capacity_current, capacity_total = _capacity_pair(pairs["신청/정원"])
        if capacity_current > capacity_total and status_raw == "접수중":
            raise UiwangExperienceContractError("open card exceeds declared capacity")
        detail_url = uiwang_experience_detail_url(identity)
        rows.append(
            {
                "provider": UIWANG_EXPERIENCE_PROVIDER,
                "provider_course_id": f"uiwang-experience:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": "의왕시 통합예약 체험·캠프",
                "branch_code": "UIWANG_EXPERIENCE",
                "preserve_branch": True,
                "category": "체험·캠프",
                "category_raw": "/reserve/EXP/X01",
                "raw_url": detail_url,
                "application_url": "",
                "status": _STATUS[status_raw],
                "fee": pairs["사용료"],
                "period": f"{start.isoformat()} ~ {end.isoformat()}",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
                "schedule_raw": pairs["요일"],
                "target": pairs["대상"],
                "eligibility_raw": pairs["대상"],
                "capacity": pairs["신청/정원"],
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "capacity_remaining": max(capacity_total - capacity_current, 0),
                "room": pairs["위치"],
                "venue_name": pairs["위치"],
                "collection_category": "공공예약",
                "domain_category": "체험·견학",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "체험",
                "service_group_policy": "locked",
                "collection_type": UIWANG_EXPERIENCE_PARSER,
                "program_type": "체험",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "municipality_code": UIWANG_EXPERIENCE_MUNICIPALITY_CODE,
                "municipality_name": UIWANG_EXPERIENCE_MUNICIPALITY_NAME,
                "sido": "경기도",
                "sigungu": "의왕시",
                "raw_fields": {
                    "parser": UIWANG_EXPERIENCE_PARSER,
                    "reservation_id": identity,
                    "status_raw": status_raw,
                    "list_contract": dict(pairs),
                    "explicit_non_program": False,
                },
            }
        )
    return rows


def _list_fingerprint(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("status")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
            _clean(row.get("capacity")),
        )
        for row in rows
    )


def _public_image(soup: BeautifulSoup, detail_url: str) -> str:
    image = soup.select_one(".listInfoTop img[src*='getResrImg.do'], .imgSlide img[src]")
    source = _clean(image.get("src") if image else "")
    if not source:
        return ""
    if source.startswith("/"):
        source = f"https://www.uiwang.go.kr{source}"
    parsed = urlparse(source)
    clean_path = parsed.path.split(";jsessionid", 1)[0]
    if parsed.scheme not in {"", "https"} or parsed.hostname not in {
        None,
        "www.uiwang.go.kr",
    }:
        raise UiwangExperienceContractError("detail image escaped official host")
    return urlunparse(("https", "www.uiwang.go.kr", clean_path, "", parsed.query, ""))


def _detail_location(soup: BeautifulSoup) -> str:
    values: list[str] = []
    for item in soup.select(".loca li"):
        text = _clean(item.get_text(" ", strip=True))
        if text.startswith("위치"):
            values.append(_clean(re.sub(r"^위치\s*", "", text)))
    if len(values) != 1:
        raise UiwangExperienceContractError("missing unambiguous public location")
    return values[0]


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(
        f"{UIWANG_EXPERIENCE_PROVIDER}|{branch}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"UIWANG_EXP_{digest}"


def _enrich_detail(soup: BeautifulSoup, row: dict[str, Any]) -> None:
    title_nodes = soup.select(".listInfoTop p.tit")
    if len(title_nodes) != 1 or _clean(title_nodes[0].get_text(" ", strip=True)) != row["title"]:
        raise UiwangExperienceContractError("detail title does not match list identity")
    hidden_ids = {
        _clean(node.get("value")) for node in soup.select("input#resrId[name='resrId']")
    }
    expected_identity = row["raw_fields"]["reservation_id"]
    if hidden_ids != {expected_identity}:
        raise UiwangExperienceContractError("detail reservation identity mismatch")
    container = soup.select_one(".listInfoBtm .infoArea")
    if container is None:
        raise UiwangExperienceContractError("detail information block missing")
    pairs = _pairs(container, ".itemList > li")
    if not _DETAIL_REQUIRED_FIELDS.issubset(pairs) or pairs["유형"] != "체험":
        raise UiwangExperienceContractError("detail is no longer an experience record")
    detail_start, detail_end = _dates(pairs["체험캠프기간"], "detail period")
    if (
        detail_start.isoformat() != row["start_date"]
        or detail_end.isoformat() != row["end_date"]
    ):
        raise UiwangExperienceContractError("detail period does not match list")
    detail_capacity_values = re.findall(r"\d[\d,]*", pairs["모집정원"])
    if len(detail_capacity_values) != 1:
        raise UiwangExperienceContractError("detail capacity changed")
    detail_capacity = int(detail_capacity_values[0].replace(",", ""))
    if detail_capacity != row["capacity_total"]:
        raise UiwangExperienceContractError("detail capacity does not match list")

    location = _detail_location(soup)
    address_match = _ADDRESS_RE.search(location)
    if not address_match:
        raise UiwangExperienceContractError("official Uiwang road address missing")
    address = f"경기도 의왕시 {_clean(address_match.group('road'))}"
    branch = pairs["체험캠프장소"]
    if not branch:
        raise UiwangExperienceContractError("experience venue missing")

    script_text = "\n".join(script.get_text(" ", strip=False) for script in soup.select("script"))
    has_application_control = bool(
        re.search(r"function\s+fnResvRqst\s*\(", script_text)
        and '$("#resrId").val()' in script_text
        and soup.select_one("#resvRqstBtnArea") is not None
    )
    if row["status"] == "OPEN" and not has_application_control:
        raise UiwangExperienceContractError("open detail application control changed")

    row.update(
        {
            "branch": branch,
            "branch_code": _branch_code(branch),
            "fee": pairs["사용료"],
            "schedule_raw": _clean(
                f"{pairs['체험캠프요일']} {pairs['체험캠프시간']}"
            ),
            "target": pairs["대상"],
            "eligibility_raw": pairs["대상"],
            "room": branch,
            "venue_name": branch,
            "address": address,
            "venue_address": address,
            "branch_address_source": "OFFICIAL_UIWANG_RESERVATION_DETAIL",
            "branch_location_verified": True,
            "branch_location_confidence": 100,
            "branch_location_query": row["raw_url"],
            "image_url": _public_image(soup, row["raw_url"]),
            "application_url": row["raw_url"] if row["status"] == "OPEN" else "",
            "application_type": (
                "ONLINE_RESERVATION" if row["status"] == "OPEN" else "INFO_ONLY"
            ),
            "application_method_raw": pairs["예약방식"],
            "reservation_available": row["status"] == "OPEN",
        }
    )
    row["raw_fields"].update(
        {
            "detail_contract": {
                key: pairs[key]
                for key in (
                    "유형",
                    "체험캠프기간",
                    "체험캠프시간",
                    "체험캠프요일",
                    "체험캠프장소",
                    "읍면동",
                    "대상",
                    "사용료",
                    "예약방식",
                    "기관/부서",
                    "모집정원",
                )
            },
            "application_control_verified": has_application_control,
            "application_endpoint_called": False,
            "public_location": location,
        }
    )


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "application_endpoints_called": 0,
        "configured_collection_error": message,
        "ownership_scope": UIWANG_EXPERIENCE_OWNERSHIP_SCOPE,
        "municipality_code": UIWANG_EXPERIENCE_MUNICIPALITY_CODE,
    }


def collect_uiwang_experience_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 30,
    detail_limit: int = 100,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future Uiwang experience snapshot."""

    if not is_uiwang_experience_target(target):
        return [], UIWANG_EXPERIENCE_PARSER, _failure(
            "target does not match the audited Uiwang experience owner"
        )

    source_total = 0
    data_pages = 0
    list_requests = 0
    detail_attempts = 0
    detail_pages = 0
    source_cap_reached = False
    stable_first = False
    page_rows: dict[int, list[dict[str, Any]]] = {}
    try:
        allowed_pages = _positive(max_pages, "max_pages")
        allowed_details = _positive(detail_limit, "detail_limit")
        cutoff = _today(today)
        with _Runner(session_factory or _default_session_factory, timeout) as runner:
            first_soup = runner.soup(uiwang_experience_list_url(1))
            list_requests += 1
            source_total = _declared_total(first_soup)
            first_rows = _list_rows(first_soup)
            page_rows[1] = first_rows
            if source_total and not first_rows:
                raise UiwangExperienceContractError(
                    "nonzero declaration has no first-page records"
                )
            page_size = len(first_rows) if first_rows else 1
            data_pages = max(1, math.ceil(source_total / page_size))
            required_list_requests = data_pages + 2
            if required_list_requests > allowed_pages:
                source_cap_reached = True
                raise UiwangExperienceContractError(
                    f"max_pages cap allows {allowed_pages} of "
                    f"{required_list_requests} required list requests"
                )

            for page in range(2, data_pages + 1):
                soup = runner.soup(
                    uiwang_experience_list_url(page),
                    referer=UIWANG_EXPERIENCE_URL,
                )
                list_requests += 1
                if _declared_total(soup) != source_total:
                    raise UiwangExperienceContractError(
                        "declared total changed across pages"
                    )
                page_rows[page] = _list_rows(soup)

            sentinel_soup = runner.soup(
                uiwang_experience_list_url(data_pages + 1),
                referer=UIWANG_EXPERIENCE_URL,
            )
            list_requests += 1
            if _declared_total(sentinel_soup) != source_total:
                raise UiwangExperienceContractError(
                    "sentinel declared total changed"
                )
            sentinel_rows = _list_rows(sentinel_soup)
            if sentinel_rows:
                raise UiwangExperienceContractError("post-last page is not exactly empty")

            verify_soup = runner.soup(
                uiwang_experience_list_url(1),
                referer=UIWANG_EXPERIENCE_URL,
            )
            list_requests += 1
            if _declared_total(verify_soup) != source_total:
                raise UiwangExperienceContractError(
                    "declared total changed on page-one recheck"
                )
            verify_rows = _list_rows(verify_soup)
            stable_first = _list_fingerprint(verify_rows) == _list_fingerprint(
                first_rows
            )
            if not stable_first:
                raise UiwangExperienceContractError(
                    "first list page changed during snapshot"
                )

            all_rows = [
                row for page in range(1, data_pages + 1) for row in page_rows[page]
            ]
            expected_counts = {
                page: min(page_size, max(0, source_total - (page - 1) * page_size))
                for page in range(1, data_pages + 1)
            }
            for page, expected in expected_counts.items():
                if len(page_rows[page]) != expected:
                    raise UiwangExperienceContractError(
                        f"page {page} expected {expected} rows, got {len(page_rows[page])}"
                    )
            identities = [row["provider_course_id"] for row in all_rows]
            if len(all_rows) != source_total:
                raise UiwangExperienceContractError(
                    f"declared total {source_total} != parsed rows {len(all_rows)}"
                )
            if len(identities) != len(set(identities)):
                raise UiwangExperienceContractError("duplicate source identities")

            explicit_non_program = [
                row
                for row in all_rows
                if row["raw_fields"].get("explicit_non_program")
            ]
            current_rows = [
                row
                for row in all_rows
                if not row["raw_fields"].get("explicit_non_program")
                and date.fromisoformat(row["end_date"]) >= cutoff
            ]
            if len(current_rows) > allowed_details:
                source_cap_reached = True
                raise UiwangExperienceContractError(
                    f"detail_limit cap allows {allowed_details} of "
                    f"{len(current_rows)} required current/future details"
                )
            for row in current_rows:
                detail_attempts += 1
                detail_soup = runner.soup(
                    row["raw_url"], referer=UIWANG_EXPERIENCE_URL
                )
                _enrich_detail(detail_soup, row)
                detail_pages += 1

            result = list((dedupe_rows or _dedupe_default)(current_rows))
            if [row["provider_course_id"] for row in result] != [
                row["provider_course_id"] for row in current_rows
            ]:
                raise UiwangExperienceContractError(
                    "dedupe changed a complete ordered snapshot"
                )

            meta = {
                "pages": data_pages + 1,
                "list_requests": list_requests,
                "physical_requests": runner.requests,
                "required_list_requests": required_list_requests,
                "source_total": source_total,
                "source_rows": len(all_rows),
                "data_pages": data_pages,
                "page_size": page_size,
                "page_counts": {
                    page: len(rows) for page, rows in page_rows.items()
                },
                "sentinel_page": data_pages + 1,
                "sentinel_rows": 0,
                "stable_first_page": stable_first,
                "current_count": len(current_rows),
                "expired_count": (
                    len(all_rows) - len(current_rows) - len(explicit_non_program)
                ),
                "explicit_non_program_count": len(explicit_non_program),
                "test_count": len(explicit_non_program),
                "returned_count": len(result),
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "application_control_count": sum(
                    bool(row.get("reservation_available")) for row in result
                ),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "pagination_detected": data_pages > 1,
                "pagination_complete": True,
                "details_complete": detail_pages == len(current_rows),
                "snapshot_complete": True,
                "source_cap_reached": False,
                "no_current_data": not current_rows,
                "no_current_reason": (
                    "complete official experience ledger has no current/future rows"
                    if not current_rows
                    else ""
                ),
                "application_endpoints_called": 0,
                "pii_payload_persisted": False,
                "configured_collection_error": "",
                "ownership_scope": UIWANG_EXPERIENCE_OWNERSHIP_SCOPE,
                "municipality_code": UIWANG_EXPERIENCE_MUNICIPALITY_CODE,
                "covered_municipalities": [
                    {
                        "code": UIWANG_EXPERIENCE_MUNICIPALITY_CODE,
                        "sido": "경기도",
                        "sigungu": "의왕시",
                        "full_name": UIWANG_EXPERIENCE_MUNICIPALITY_NAME,
                    }
                ],
            }
            return result, UIWANG_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta = _failure(f"{type(exc).__name__}: {_clean(exc)}")
        meta.update(
            {
                "pages": len(page_rows),
                "list_requests": list_requests,
                "source_total": source_total,
                "source_rows": sum(len(rows) for rows in page_rows.values()),
                "data_pages": data_pages,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "stable_first_page": stable_first,
                "source_cap_reached": source_cap_reached,
            }
        )
        return [], UIWANG_EXPERIENCE_PARSER, meta


collect = collect_uiwang_experience_courses


__all__ = [
    "UIWANG_EXPERIENCE_PROVIDER",
    "UIWANG_EXPERIENCE_URL",
    "UIWANG_EXPERIENCE_LIST_ENDPOINT",
    "UIWANG_EXPERIENCE_DETAIL_ENDPOINT",
    "UIWANG_EXPERIENCE_MUNICIPALITY_CODE",
    "UIWANG_EXPERIENCE_MUNICIPALITY_NAME",
    "UIWANG_EXPERIENCE_PARSER",
    "UIWANG_EXPERIENCE_LIVE_BASELINE",
    "UiwangExperienceContractError",
    "collect_uiwang_experience_courses",
    "is_uiwang_experience_target",
    "uiwang_experience_list_url",
    "uiwang_experience_detail_url",
]
