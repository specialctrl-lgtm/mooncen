from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from datetime import date, datetime
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup, Tag


POHANG_INTEGRATED_PROVIDER = "MUNI_MBIS_POHANG_GO_KR_0407D99A"
POHANG_INTEGRATED_CANDIDATE_ID = "MUNI_IR_POHANG_MBIS_EDUCATION"
POHANG_INTEGRATED_BASE_URL = "https://mbis.pohang.go.kr"
POHANG_INTEGRATED_LIST_URL = (
    "https://mbis.pohang.go.kr/apply/lecture/lectureInfoList.do?mid=0101000000"
)
POHANG_INTEGRATED_DETAIL_URL = (
    "https://mbis.pohang.go.kr/apply/lecture/lectureInfoView.do?mid=0101000000"
)
POHANG_INTEGRATED_APPLICATION_PATH = "/apply/lecture/lectureRequestWrite.do"
POHANG_INTEGRATED_PARSER = (
    "pohang_mbis_citizen_it_declared_total_all_pages+empty_post_last+"
    "stable_first_last+all_current_public_post_details+locked_education+"
    "no_application_calls"
)
POHANG_INTEGRATED_OWNERSHIP_SCOPE = (
    "pohang_official_integrated_reservation_citizen_information_education"
)
POHANG_INTEGRATED_PAGE_SIZE = 10
POHANG_INTEGRATED_DEFAULT_MAX_PAGES = 100
POHANG_INTEGRATED_DEFAULT_DETAIL_LIMIT = 300
POHANG_INTEGRATED_REQUEST_LIMIT = 500

POHANG_CITY_CODE = "4711000000"
POHANG_NAMGU_CODE = "4711100000"
POHANG_BUKGU_CODE = "4711300000"
POHANG_MUNICIPALITIES: Mapping[str, str] = {
    POHANG_CITY_CODE: "경상북도 포항시",
    POHANG_NAMGU_CODE: "경상북도 포항시 남구",
    POHANG_BUKGU_CODE: "경상북도 포항시 북구",
}
POHANG_COVERED_MUNICIPALITIES = tuple(
    {
        "code": code,
        "sido": "경상북도",
        "sigungu": full_name.removeprefix("경상북도 "),
        "full_name": full_name,
    }
    for code, full_name in POHANG_MUNICIPALITIES.items()
)

_NAMGU_VENUE_MARKERS = (
    "구룡포읍",
    "연일읍",
    "오천읍",
    "대송면",
    "동해면",
    "장기면",
    "호미곶면",
    "상대동",
    "해도동",
    "송도동",
    "청림동",
    "제철동",
    "효곡동",
    "대이동",
)
_BUKGU_VENUE_MARKERS = (
    "흥해읍",
    "신광면",
    "청하면",
    "송라면",
    "기계면",
    "죽장면",
    "기북면",
    "중앙동",
    "양학동",
    "죽도동",
    "용흥동",
    "우창동",
    "두호동",
    "장량동",
    "환여동",
)
_IDENTITY_RE = re.compile(r"\d{16}")
_INTEGER_RE = re.compile(r"[\d,]+")
_DATE_RE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")
_PHONE_RE = re.compile(r"(?<!\d)(?:0\d{1,2}[-.\s]?)?\d{3,4}[-.\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NOTICE_PREFIXES = ("공지", "알림", "안내")
_TEST_MARKERS = ("테스트", "test", "TEST")

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


class PohangIntegratedContractError(ValueError):
    pass


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _redact(value: Any) -> str:
    return _clean(_EMAIL_RE.sub(" ", _PHONE_RE.sub(" ", _clean(value))))


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def is_pohang_integrated_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != POHANG_INTEGRATED_PROVIDER:
        return False
    return _canonical_url(_target_value(target, "url")) == POHANG_INTEGRATED_LIST_URL


def _canonical_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if parsed.scheme != "https" or parsed.hostname != "mbis.pohang.go.kr":
        return ""
    if parsed.path != "/apply/lecture/lectureInfoList.do":
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query != {"mid": ["0101000000"]}:
        return ""
    return POHANG_INTEGRATED_LIST_URL


def _assert_public_request(url: str, data: Mapping[str, Any]) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "mbis.pohang.go.kr":
        raise PohangIntegratedContractError("non-official request host refused")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query != {"mid": ["0101000000"]}:
        raise PohangIntegratedContractError("unexpected public request query")
    if parsed.path == "/apply/lecture/lectureInfoList.do":
        if set(data) != {"page"} or not str(data["page"]).isdigit():
            raise PohangIntegratedContractError("invalid list request body")
        return "list"
    if parsed.path == "/apply/lecture/lectureInfoView.do":
        identity = _clean(data.get("idx"))
        if (
            set(data) != {"idx", "rtnMenu"}
            or not _IDENTITY_RE.fullmatch(identity)
            or data.get("rtnMenu") != "/lecture/lectureInfoList.do"
        ):
            raise PohangIntegratedContractError("invalid public detail body")
        return "detail"
    raise PohangIntegratedContractError(
        "application/login/file/PII endpoint request refused"
    )


def _default_session_factory() -> requests.Session:
    return requests.Session()


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


class _Runner:
    def __init__(self, factory: SessionFactory, timeout: int) -> None:
        self.factory = factory
        self.timeout = timeout
        self.session: Any = None
        self.requests = 0
        self.list_requests = 0
        self.detail_requests = 0

    def __enter__(self) -> "_Runner":
        self.session = self.factory()
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
        return self

    def __exit__(self, *_: Any) -> None:
        _close(self.session)

    def post(
        self,
        url: str,
        data: Mapping[str, Any],
        *,
        referer: str = "",
    ) -> BeautifulSoup:
        kind = _assert_public_request(url, data)
        if self.requests >= POHANG_INTEGRATED_REQUEST_LIMIT:
            raise PohangIntegratedContractError("audited request budget exceeded")
        self.requests += 1
        if kind == "list":
            self.list_requests += 1
        else:
            self.detail_requests += 1
        response = self.session.post(
            url,
            data=dict(data),
            timeout=self.timeout,
            allow_redirects=False,
            headers={"Referer": referer} if referer else None,
        )
        status = int(getattr(response, "status_code", 200) or 0)
        if status != 200 or getattr(response, "history", None):
            raise PohangIntegratedContractError(
                f"unexpected public response status {status}"
            )
        response_url = _clean(getattr(response, "url", url))
        if urlparse(response_url).hostname != "mbis.pohang.go.kr":
            raise PohangIntegratedContractError("public response left official host")
        content = getattr(response, "content", b"")
        text = getattr(response, "text", "")
        soup = BeautifulSoup(content or text, "lxml")
        if soup.select_one("form[action*='login'], form[action*='Login']"):
            raise PohangIntegratedContractError("public request reached login page")
        return soup


def _date_range(value: Any) -> tuple[str, str]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) < 2:
        raise PohangIntegratedContractError("missing complete date range")
    values = [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in matches]
    date.fromisoformat(values[0])
    date.fromisoformat(values[-1])
    return values[0], values[-1]


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value)[:10])


def _label_values(item: Tag) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in item.select("ul.info > li"):
        label = node.select_one("strong")
        if label is None:
            continue
        key = _clean(label.get_text(" ", strip=True))
        clone = BeautifulSoup(str(node), "lxml").select_one("li")
        if clone is None:
            continue
        cloned_label = clone.select_one("strong")
        if cloned_label is not None:
            cloned_label.decompose()
        for interactive in clone.select("a, button, script"):
            interactive.decompose()
        values[key] = _clean(clone.get_text(" ", strip=True)).lstrip("> ")
    return values


def _declared_page_contract(soup: BeautifulSoup, requested_page: int) -> tuple[int, int]:
    total_node = soup.select_one(".page_total em")
    page_node = soup.select_one(".page_num")
    if total_node is None or page_node is None:
        raise PohangIntegratedContractError("missing declared total/page contract")
    total_match = _INTEGER_RE.search(_clean(total_node.get_text(" ", strip=True)))
    if total_match is None:
        raise PohangIntegratedContractError("invalid declared source total")
    source_total = int(total_match.group().replace(",", ""))
    page_match = re.fullmatch(
        r"현재 페이지\s*(\d+)\s*/\s*전체 페이지\s*(\d+)",
        _clean(page_node.get_text(" ", strip=True)),
    )
    if page_match is None:
        raise PohangIntegratedContractError("invalid declared page text")
    current_page = int(page_match.group(1))
    declared_pages = int(page_match.group(2))
    expected_pages = max(1, math.ceil(source_total / POHANG_INTEGRATED_PAGE_SIZE))
    if current_page != requested_page or declared_pages != expected_pages:
        raise PohangIntegratedContractError("declared pagination contract drift")
    return source_total, declared_pages


def _municipality(venue: str) -> tuple[str, str, bool]:
    if any(marker in venue for marker in _NAMGU_VENUE_MARKERS):
        code = POHANG_NAMGU_CODE
        return code, POHANG_MUNICIPALITIES[code], True
    if any(marker in venue for marker in _BUKGU_VENUE_MARKERS):
        code = POHANG_BUKGU_CODE
        return code, POHANG_MUNICIPALITIES[code], True
    return POHANG_CITY_CODE, POHANG_MUNICIPALITIES[POHANG_CITY_CODE], False


def _capacity(value: str) -> Optional[int]:
    match = _INTEGER_RE.search(value)
    return int(match.group().replace(",", "")) if match else None


def _list_rows(soup: BeautifulSoup, page: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for position, item in enumerate(
        soup.select(".multiPurpose-list.edu > ul > li"), start=1
    ):
        link = item.select_one(
            "a[data-req-form-id='viewForm'][data-req-p-idx]"
        )
        title_node = item.select_one("p.subject")
        if link is None or title_node is None:
            raise PohangIntegratedContractError("malformed official programme card")
        identity = _clean(link.get("data-req-p-idx"))
        title = _clean(title_node.get_text(" ", strip=True))
        if not _IDENTITY_RE.fullmatch(identity) or not title:
            raise PohangIntegratedContractError("invalid programme identity/title")
        values = _label_values(item)
        required = {"교육기간", "접수기간", "교육장소", "모집인원"}
        if not required.issubset(values):
            raise PohangIntegratedContractError("incomplete official programme card")
        start_date, end_date = _date_range(values["교육기간"])
        apply_start, apply_end = _date_range(values["접수기간"])
        venue = values["교육장소"]
        municipality_code, municipality_full_name, region_verified = _municipality(
            venue
        )
        statuses = [
            _clean(node.get_text(" ", strip=True))
            for node in link.select("span.tag")
            if _clean(node.get_text(" ", strip=True))
        ]
        if not statuses:
            raise PohangIntegratedContractError("missing official programme status")
        explicit_non_program = title.startswith(_NOTICE_PREFIXES) or any(
            marker in title for marker in _TEST_MARKERS
        )
        result.append(
            {
                "identity": identity,
                "title": title,
                "branch": venue,
                "venue_name": venue,
                "start_date": start_date,
                "end_date": end_date,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "status": " / ".join(statuses),
                "capacity_total": _capacity(values["모집인원"]),
                "target": values.get("교육대상", ""),
                "schedule_raw": values.get("교육시간", ""),
                "municipality_code": municipality_code,
                "municipality_full_name": municipality_full_name,
                "municipality_region_verified": region_verified,
                "source_page": page,
                "source_position": position,
                "explicit_non_program": explicit_non_program,
            }
        )
    return result


def _detail_row(
    soup: BeautifulSoup,
    listed: dict[str, Any],
) -> dict[str, Any]:
    root = soup.select_one(".multiPurpose-view")
    title_node = root.select_one(".subject") if root else None
    if root is None or title_node is None:
        raise PohangIntegratedContractError("missing public detail contract")
    title = _clean(title_node.get_text(" ", strip=True))
    if title != listed["title"]:
        raise PohangIntegratedContractError("detail/list title mismatch")
    values = _label_values(root)
    if not {"교육기간", "접수기간", "교육장소"}.issubset(values):
        raise PohangIntegratedContractError("incomplete public detail contract")
    if _date_range(values["교육기간"]) != (
        listed["start_date"],
        listed["end_date"],
    ):
        raise PohangIntegratedContractError("detail/list education dates mismatch")
    if _date_range(values["접수기간"]) != (
        listed["apply_start"],
        listed["apply_end"],
    ):
        raise PohangIntegratedContractError("detail/list application dates mismatch")
    detail_venue = values["교육장소"]
    if _clean(detail_venue) != _clean(listed["venue_name"]):
        raise PohangIntegratedContractError("detail/list venue mismatch")
    application_form = soup.select_one("form#writeForm")
    application_control = soup.select_one(
        "[data-req-form-id='writeForm'], [data-req-form-name='writeForm']"
    )
    if application_form is not None:
        action = urlparse(_clean(application_form.get("action"))).path
        if action != POHANG_INTEGRATED_APPLICATION_PATH:
            raise PohangIntegratedContractError("unexpected application form action")
    has_application = application_control is not None
    raw_url = f"{POHANG_INTEGRATED_LIST_URL}#course-{listed['identity']}"
    branch_code = "POHANG_MBIS_" + hashlib.sha1(
        f"{listed['municipality_code']}|{listed['branch']}".encode("utf-8")
    ).hexdigest()[:16].upper()
    capacity_total = listed["capacity_total"]
    description = _redact(
        f"{title} | {detail_venue} | "
        f"{listed['start_date']} ~ {listed['end_date']}"
    )
    return {
        "provider": POHANG_INTEGRATED_PROVIDER,
        "provider_course_id": (
            f"{POHANG_INTEGRATED_PROVIDER}:education:{listed['identity']}"
        )[:100],
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "branch": listed["branch"],
        "branch_code": branch_code[:50],
        "preserve_branch": True,
        "branch_url": POHANG_INTEGRATED_LIST_URL,
        "category": "교육·강좌",
        "program_type": "교육",
        "raw_url": raw_url,
        "application_url": POHANG_INTEGRATED_LIST_URL if has_application else "",
        "application_type": "ONLINE_RESERVATION" if has_application else "INFO_ONLY",
        "reservation_available": has_application,
        "status": listed["status"],
        "period": f"{listed['start_date']} ~ {listed['end_date']}",
        "start_date": listed["start_date"],
        "end_date": listed["end_date"],
        "apply_period": f"{listed['apply_start']} ~ {listed['apply_end']}",
        "apply_start": listed["apply_start"],
        "apply_end": listed["apply_end"],
        "schedule_raw": listed["schedule_raw"],
        "target": listed["target"],
        "capacity": f"정원 {capacity_total}명" if capacity_total is not None else "",
        "capacity_total": capacity_total,
        "venue_name": listed["venue_name"],
        "venue_address": "",
        "address": "",
        "description": description,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": POHANG_INTEGRATED_PARSER,
        "municipality_code": listed["municipality_code"],
        "municipality_full_name": listed["municipality_full_name"],
        "municipality_region_verified": listed["municipality_region_verified"],
        "raw_fields": {
            "parser": POHANG_INTEGRATED_PARSER,
            "ownership_scope": POHANG_INTEGRATED_OWNERSHIP_SCOPE,
            "source_identity": listed["identity"],
            "source_page": listed["source_page"],
            "source_position": listed["source_position"],
            "source_status": listed["status"],
            "application_control_present": has_application,
            "application_endpoint_fetched": False,
            "login_endpoint_fetched": False,
            "pii_endpoint_fetched": False,
            "full_detail_contract": True,
        },
    }


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _failure(message: str, **extra: Any) -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "stable_first_page": False,
        "stable_last_page": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "canonical_provider": POHANG_INTEGRATED_PROVIDER,
        "canonical_url": POHANG_INTEGRATED_LIST_URL,
        "ownership_scope": POHANG_INTEGRATED_OWNERSHIP_SCOPE,
        "covered_municipalities": [dict(row) for row in POHANG_COVERED_MUNICIPALITIES],
        "application_endpoint_requests": 0,
        "application_endpoints_called": 0,
        "authentication_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "http_methods": ["POST"],
        "configured_collection_error": message,
        **extra,
    }


def collect_pohang_integrated_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = POHANG_INTEGRATED_DEFAULT_MAX_PAGES,
    detail_limit: int = POHANG_INTEGRATED_DEFAULT_DETAIL_LIMIT,
    *,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if not is_pohang_integrated_education_target(target):
        return [], POHANG_INTEGRATED_PARSER, _failure(
            "target does not match the audited Pohang integrated education owner"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], POHANG_INTEGRATED_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory
    try:
        max_pages = int(max_pages)
        detail_limit = int(detail_limit)
        if max_pages <= 0 or detail_limit < 0:
            raise PohangIntegratedContractError("invalid collection caps")
        cutoff = _today(today)
        with _Runner(session_factory, int(timeout)) as runner:
            first_soup = runner.post(
                POHANG_INTEGRATED_LIST_URL,
                {"page": "1"},
                referer=POHANG_INTEGRATED_LIST_URL,
            )
            source_total, data_pages = _declared_page_contract(first_soup, 1)
            required_pages = data_pages + 1
            if required_pages > max_pages:
                raise PohangIntegratedContractError(
                    f"max_pages cap allows {max_pages} of {required_pages} required pages"
                )
            page_rows: dict[int, list[dict[str, Any]]] = {
                1: _list_rows(first_soup, 1)
            }
            for page in range(2, data_pages + 1):
                soup = runner.post(
                    POHANG_INTEGRATED_LIST_URL,
                    {"page": str(page)},
                    referer=POHANG_INTEGRATED_LIST_URL,
                )
                total, declared_pages = _declared_page_contract(soup, page)
                if total != source_total or declared_pages != data_pages:
                    raise PohangIntegratedContractError("source totals changed during pagination")
                page_rows[page] = _list_rows(soup, page)
            sentinel_page = data_pages + 1
            sentinel_soup = runner.post(
                POHANG_INTEGRATED_LIST_URL,
                {"page": str(sentinel_page)},
                referer=POHANG_INTEGRATED_LIST_URL,
            )
            sentinel_total, sentinel_declared = _declared_page_contract(
                sentinel_soup, sentinel_page
            )
            sentinel_rows = _list_rows(sentinel_soup, sentinel_page)
            if (
                sentinel_total != source_total
                or sentinel_declared != data_pages
                or sentinel_rows
            ):
                raise PohangIntegratedContractError("post-last sentinel is not exactly empty")
            first_recheck = runner.post(
                POHANG_INTEGRATED_LIST_URL,
                {"page": "1"},
                referer=POHANG_INTEGRATED_LIST_URL,
            )
            _declared_page_contract(first_recheck, 1)
            stable_first = [row["identity"] for row in _list_rows(first_recheck, 1)] == [
                row["identity"] for row in page_rows[1]
            ]
            last_recheck = runner.post(
                POHANG_INTEGRATED_LIST_URL,
                {"page": str(data_pages)},
                referer=POHANG_INTEGRATED_LIST_URL,
            )
            _declared_page_contract(last_recheck, data_pages)
            stable_last = [
                row["identity"] for row in _list_rows(last_recheck, data_pages)
            ] == [row["identity"] for row in page_rows[data_pages]]
            if not stable_first or not stable_last:
                raise PohangIntegratedContractError("list boundary changed during snapshot")
            listed_rows = [row for page in page_rows.values() for row in page]
            identities = [row["identity"] for row in listed_rows]
            if len(listed_rows) != source_total or len(set(identities)) != source_total:
                raise PohangIntegratedContractError("declared total/unique row reconciliation failed")
            explicit_non_program = [row for row in listed_rows if row["explicit_non_program"]]
            current_rows = [
                row
                for row in listed_rows
                if not row["explicit_non_program"]
                and date.fromisoformat(row["end_date"]) >= cutoff
            ]
            if len(current_rows) > detail_limit:
                raise PohangIntegratedContractError(
                    f"detail_limit cap allows {detail_limit} of {len(current_rows)} required details"
                )
            result: list[dict[str, Any]] = []
            for listed in current_rows:
                detail_soup = runner.post(
                    POHANG_INTEGRATED_DETAIL_URL,
                    {
                        "idx": listed["identity"],
                        "rtnMenu": "/lecture/lectureInfoList.do",
                    },
                    referer=POHANG_INTEGRATED_LIST_URL,
                )
                result.append(_detail_row(detail_soup, listed))
            result = list((dedupe_rows or _dedupe_default)(result))
            if len(result) != len(current_rows):
                raise PohangIntegratedContractError(
                    "dedupe changed a complete current/future snapshot"
                )
            meta = {
                "pages": required_pages,
                "request_count": runner.requests,
                "list_requests": runner.list_requests,
                "detail_attempts": len(current_rows),
                "detail_pages": runner.detail_requests,
                "source_total": source_total,
                "source_rows": len(listed_rows),
                "data_pages": data_pages,
                "sentinel_page": sentinel_page,
                "sentinel_raw_rows": 0,
                "stable_first_page": stable_first,
                "stable_last_page": stable_last,
                "unique_id_count": len(set(identities)),
                "expired_count": len(listed_rows) - len(current_rows) - len(explicit_non_program),
                "explicit_non_program_count": len(explicit_non_program),
                "notice_count": sum(
                    row["title"].startswith(_NOTICE_PREFIXES)
                    for row in explicit_non_program
                ),
                "test_count": sum(
                    any(marker in row["title"] for marker in _TEST_MARKERS)
                    for row in explicit_non_program
                ),
                "current_count": len(current_rows),
                "returned_count": len(result),
                "branch_count": len({_clean(row["branch"]) for row in result}),
                "branch_counts": dict(Counter(_clean(row["branch"]) for row in result)),
                "municipality_counts": dict(
                    Counter(_clean(row["municipality_code"]) for row in result)
                ),
                "pagination_complete": True,
                "details_complete": runner.detail_requests == len(current_rows),
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "source_cap_reached": False,
                "no_current_data": not current_rows,
                "canonical_provider": POHANG_INTEGRATED_PROVIDER,
                "canonical_url": POHANG_INTEGRATED_LIST_URL,
                "ownership_scope": POHANG_INTEGRATED_OWNERSHIP_SCOPE,
                "covered_municipalities": [
                    dict(row) for row in POHANG_COVERED_MUNICIPALITIES
                ],
                "application_endpoint_requests": 0,
                "application_endpoints_called": 0,
                "authentication_endpoint_requests": 0,
                "pii_endpoint_requests": 0,
                "http_methods": ["POST"],
                "configured_collection_error": "",
            }
            return result, POHANG_INTEGRATED_PARSER, meta
    except Exception as exc:
        return [], POHANG_INTEGRATED_PARSER, _failure(
            f"{type(exc).__name__}: {_redact(exc)}"
        )


collect = collect_pohang_integrated_education


__all__ = [
    "POHANG_BUKGU_CODE",
    "POHANG_CITY_CODE",
    "POHANG_COVERED_MUNICIPALITIES",
    "POHANG_INTEGRATED_APPLICATION_PATH",
    "POHANG_INTEGRATED_CANDIDATE_ID",
    "POHANG_INTEGRATED_DETAIL_URL",
    "POHANG_INTEGRATED_LIST_URL",
    "POHANG_INTEGRATED_OWNERSHIP_SCOPE",
    "POHANG_INTEGRATED_PARSER",
    "POHANG_INTEGRATED_PROVIDER",
    "POHANG_NAMGU_CODE",
    "PohangIntegratedContractError",
    "collect",
    "collect_pohang_integrated_education",
    "is_pohang_integrated_education_target",
]
