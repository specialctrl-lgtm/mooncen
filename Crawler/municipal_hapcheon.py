"""Complete, fail-closed collector for Hapcheon-gun education courses.

The review candidate is the Gyeongsangnam-do Office of Education Hapcheon
support-office home page.  It is official, but it is neither owned by
Hapcheon-gun nor a public course identity ledger.  The county's canonical
ledger is ``통합예약 > 교육강좌 > 프로그램 안내``.  The lifelong-learning
portal exposes the same main identities, plus one separate literacy-course
ledger under the same county owner.

Both ledgers are walked through their declared final page.  The server clamps
an exact post-last request to the final page, so completeness requires that
clamp and stable first/final boundary rechecks.  Only current/future identities
receive detail requests.  Public application controls are inspected, while
application/authentication pages are never fetched.  Instructor/contact
fields, attachments, preparation notes, free-text introductions and any
applicant information are deliberately excluded from returned rows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


HAPCHEON_HOST = "www.hc.go.kr"
HAPCHEON_PROVIDER = "MUNI_WWW_HC_GO_KR_3C13AEC0"
HAPCHEON_MUNICIPALITY_CODE = "4889000000"
HAPCHEON_MUNICIPALITY_NAME = "경상남도 합천군"
HAPCHEON_LIST_PATH = "/09373/09374.web"
HAPCHEON_PORTAL_MIRROR_PATH = "/09117/09127.web"
HAPCHEON_LITERACY_PATH = "/09117/09207.web"
HAPCHEON_URL = f"https://{HAPCHEON_HOST}{HAPCHEON_LIST_PATH}"
HAPCHEON_PORTAL_MIRROR_URL = (
    f"https://{HAPCHEON_HOST}{HAPCHEON_PORTAL_MIRROR_PATH}"
)
HAPCHEON_LITERACY_URL = f"https://{HAPCHEON_HOST}{HAPCHEON_LITERACY_PATH}"
HAPCHEON_PAGE_SIZE = 9
HAPCHEON_FETCH_ATTEMPTS = 2
HAPCHEON_MAX_WORKERS = 4
HAPCHEON_PARSER = (
    "hapcheon_two_complete_county_education_ledgers+declared_totals+"
    "exact_final_page_clamps+stable_first_final_boundaries+"
    "current_future_detail_identity_binding+official_branch_vocabulary+"
    "source_status_and_application_control_validation+"
    "no_application_endpoint_fetch+pii_allowlist"
)
HAPCHEON_OWNERSHIP_SCOPE = (
    "official_hapcheon_integrated_education_and_literacy_ledgers"
)

HAPCHEON_CANONICAL_CANDIDATE_ID = "MUNI_IR_E06BB8D5CD0D"
HAPCHEON_REVIEW_CANDIDATE_ID = "MUNI_IR_9823192E9747"
HAPCHEON_REVIEW_PROVIDER = "MUNI_HCEDU_GNE_GO_KR_013D30EB"
HAPCHEON_CANDIDATE_DECISIONS: Mapping[str, str] = {
    HAPCHEON_CANONICAL_CANDIDATE_ID: (
        "manual_override_promote_new_complete_county_education_owner"
    ),
    HAPCHEON_REVIEW_CANDIDATE_ID: (
        "exclude_education_support_office_home_not_course_identity_ledger"
    ),
}

HAPCHEON_MAIN_BRANCHES = frozenset(
    {"평생교육포털", "주민복지과", "체육시설과", "노인아동여성과"}
)
HAPCHEON_LITERACY_BRANCH = "학력인정반"
HAPCHEON_OFFICIAL_BRANCHES = frozenset(
    {*HAPCHEON_MAIN_BRANCHES, HAPCHEON_LITERACY_BRANCH}
)

HAPCHEON_OWNER_BOUNDARIES: Mapping[str, str] = {
    "https://hcedu.gne.go.kr/": (
        "separate_provincial_education_support_office_home_not_course_ledger"
    ),
    (
        "https://hcedu.gne.go.kr/hcedu/na/ntt/selectNttList.do?"
        "mi=2714&bbsId=501"
    ): "separate_education_support_office_notice_board_not_course_ledger",
    "https://hclib.gne.go.kr/": (
        "separate_provincial_library_owner_not_county_integrated_ledger"
    ),
    "https://www.hc.go.kr/yeyak.web": (
        "county_reservation_discovery_home_not_course_identity_ledger"
    ),
    "https://www.hc.go.kr/09363/09364/09364.web": (
        "sports_facility_booking_owner_not_education_courses"
    ),
    "https://www.hc.go.kr/09368/09370.web": (
        "facility_rental_owner_not_education_courses"
    ),
    "https://www.hc.go.kr/04923/04924/04945.web?idx=28088297&amode=view": (
        "social_welfare_recruitment_article_not_course_identity_ledger"
    ),
    "https://www.hc.go.kr/04960/05111/05247.web?amode=view&idx=27825959": (
        "single_information_article_not_course_identity_ledger"
    ),
}


@dataclass(frozen=True)
class HapcheonLedger:
    key: str
    label: str
    path: str
    facility_key: str
    facility_value: str
    branches: frozenset[str]

    @property
    def url(self) -> str:
        return f"https://{HAPCHEON_HOST}{self.path}"


HAPCHEON_LEDGERS = (
    HapcheonLedger(
        "main",
        "통합예약 교육강좌",
        HAPCHEON_LIST_PATH,
        "facCodes",
        "001",
        HAPCHEON_MAIN_BRANCHES,
    ),
    HapcheonLedger(
        "literacy",
        "학력인정반",
        HAPCHEON_LITERACY_PATH,
        "facCode",
        "004",
        frozenset({HAPCHEON_LITERACY_BRANCH}),
    ),
)
HAPCHEON_LEDGER_BY_KEY = {ledger.key: ledger for ledger in HAPCHEON_LEDGERS}


class HapcheonContractError(RuntimeError):
    """Raised when the audited Hapcheon public-source contract changes."""


Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_TOTAL_RE = re.compile(
    r"총\s*([\d,]+)\s*건의\s*자료가\s*있습니다\.\s*"
    r"\(\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\)"
)
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\.)?(?!\d)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[-\s*]+[\d*]{3,4}[-\s*]+[\d*]{4}|"
    r"0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4}|0\d{8,11})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CANCELLED_RE = re.compile(r"(?:^|[<\[(])\s*(?:폐강|취소)\s*(?:$|[>\])])")

_LIST_LABELS = ("교육기간", "신청기간", "수강료")
_DETAIL_REQUIRED = {
    "교육기간",
    "접수기간",
    "수강료",
    "모집대상",
    "접수방법",
    "인원",
    "강사명",
    "교육소개",
}
_DETAIL_OPTIONAL = {
    "교육시간",
    "교육장소",
    "준비물",
    "모집지역",
    "이용문의",
    "첨부파일",
}
_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "대기접수": "WAITING",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "접수완료": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "ARCHIVED",
}
_ACTIVE_SOURCE_STATUSES = frozenset({"접수중", "대기접수"})
_CONTROL_LABELS: Mapping[str, frozenset[str]] = {
    "접수중": frozenset({"신청하기", "교육신청", "접수중"}),
    "대기접수": frozenset({"신청하기", "대기접수", "대기자신청"}),
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _safe_hapcheon_url(url: str, *, path: str) -> bool:
    parsed = urlparse(url)
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == HAPCHEON_HOST
        and parsed.port is None
        and parsed.path == path
        and not parsed.params
        and not parsed.username
        and not parsed.password
    )


def is_hapcheon_education_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return bool(
        _provider(target) == HAPCHEON_PROVIDER
        and _safe_hapcheon_url(_target_url(target), path=HAPCHEON_LIST_PATH)
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_hapcheon_education_target


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


def hapcheon_list_url(ledger: HapcheonLedger, page: int = 1) -> str:
    if page < 1:
        return ""
    if page == 1:
        return ledger.url
    query: tuple[tuple[str, str], ...]
    if ledger.key == "main":
        query = ((ledger.facility_key, ledger.facility_value), ("cpage", str(page)))
    else:
        query = (("cpage", str(page)),)
    return f"{ledger.url}?{urlencode(query)}"


def hapcheon_detail_url(ledger: HapcheonLedger, identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return f"{ledger.url}?{urlencode((('amode', 'view'), ('idx', value), (ledger.facility_key, ledger.facility_value)))}"


def _response_soup(response: Any, *, ledger: HapcheonLedger) -> tuple[BeautifulSoup, str]:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise HapcheonContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise HapcheonContractError("HTTP redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if not _safe_hapcheon_url(final_url, path=ledger.path):
        raise HapcheonContractError("source response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise HapcheonContractError("empty HTML response")
    return BeautifulSoup(content, "lxml"), final_url


def _request_soup(
    current: Any,
    url: str,
    *,
    ledger: HapcheonLedger,
    timeout: int,
    fetcher: Optional[Fetcher],
    meta: dict[str, Any],
) -> tuple[BeautifulSoup, str]:
    meta["logical_requests"] += 1
    messages: list[str] = []
    for attempt in range(1, HAPCHEON_FETCH_ATTEMPTS + 1):
        meta["physical_requests"] += 1
        try:
            if fetcher is not None:
                result = fetcher(current, "GET", url, timeout=timeout, data={})
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and isinstance(result[0], BeautifulSoup)
                ):
                    soup, final_url = result
                    final_url = _clean(final_url or url)
                    if not _safe_hapcheon_url(final_url, path=ledger.path):
                        raise HapcheonContractError("source response URL changed")
                    return soup, final_url
                if isinstance(result, BeautifulSoup):
                    return result, url
                if isinstance(result, (str, bytes, bytearray)):
                    if not result:
                        raise HapcheonContractError("empty HTML response")
                    return BeautifulSoup(result, "lxml"), url
                return _response_soup(result, ledger=ledger)
            return _response_soup(current.get(url, timeout=timeout), ledger=ledger)
        except Exception as exc:
            messages.append(
                f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}"
            )
    meta["request_retry_count"] = meta["physical_requests"] - meta["logical_requests"]
    raise HapcheonContractError("; ".join(messages))


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError:
            return []
    return result


def _range_dates(value: Any) -> tuple[date, date]:
    values = _dates(value)
    if len(values) == 1:
        return values[0], values[0]
    if len(values) == 2 and values[0] <= values[1]:
        return values[0], values[1]
    raise HapcheonContractError(f"malformed date range: {_clean(value)}")


def _form_and_total(
    soup: BeautifulSoup,
    *,
    ledger: HapcheonLedger,
    requested_page: int,
    displayed_page: int,
) -> tuple[int, int, list[str]]:
    errors: list[str] = []
    forms = soup.select("#body_content form#frmLecture[name='frmLecture']")
    if len(forms) != 1:
        return 0, 0, ["expected one unfiltered frmLecture"]
    form = forms[0]
    action = urlparse(urljoin(ledger.url, _clean(form.get("action"))))
    expected_query = parse_qs(
        urlparse(hapcheon_list_url(ledger, requested_page)).query,
        keep_blank_values=True,
    )
    if (
        _clean(form.get("method")).lower() != "get"
        or action.path != ledger.path
        or parse_qs(action.query, keep_blank_values=True) != expected_query
    ):
        errors.append("unexpected frmLecture method/action")
    stype = form.select("select[name='stype']")
    if (
        len(stype) != 1
        or len(stype[0].select("option")) != 1
        or _clean(stype[0].select_one("option").get("value")) != "title"
    ):
        errors.append("frmLecture stype contract changed")
    sstring = form.select("input[name='sstring']")
    if len(sstring) != 1 or _clean(sstring[0].get("value")):
        errors.append("frmLecture search string is not empty")

    blocks = form.select("div.infomenu1 div.left")
    matches = [
        _TOTAL_RE.fullmatch(_clean(block.get_text(" ", strip=True)))
        for block in blocks
    ]
    matches = [match for match in matches if match is not None]
    if len(matches) != 1:
        return 0, 0, [*errors, "expected one declared course total"]
    total, current, last = (
        int(value.replace(",", "")) for value in matches[0].groups()
    )
    if current != displayed_page:
        errors.append("declared current page mismatch")
    expected_last = max(1, math.ceil(total / HAPCHEON_PAGE_SIZE))
    if last != expected_last:
        errors.append("declared last page does not match total/page size")

    pagers = soup.select("#body_content div.pagination")
    if len(pagers) != 1:
        errors.append("expected one pagination block")
    else:
        current_nodes = pagers[0].select("span.pages span.m.on a")
        if (
            len(current_nodes) != 1
            or _clean(current_nodes[0].get_text(" ", strip=True))
            != str(displayed_page)
        ):
            errors.append("pagination current page mismatch")
        for anchor in pagers[0].select("a[href]"):
            parsed = urlparse(urljoin(ledger.url, _clean(anchor.get("href"))))
            query = parse_qs(parsed.query, keep_blank_values=True)
            page_value = _clean((query.get("cpage") or [""])[0])
            if parsed.path != ledger.path or not page_value.isdigit():
                errors.append("malformed pagination link")
                continue
            if int(page_value) > last:
                errors.append("pagination link exceeds declared final page")
    return total, last, errors


def _parse_fields(card: Any) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    errors: list[str] = []
    for node in card.select("div.tg2 span.li1"):
        labels = node.select(":scope > span.t1")
        values = node.select(":scope > span.t2")
        if len(labels) != 1 or len(values) != 1:
            errors.append("malformed list field")
            continue
        key = _clean(labels[0].get_text(" ", strip=True))
        if not key or key in fields:
            errors.append("duplicate or empty list field")
            continue
        fields[key] = _clean(values[0].get_text(" ", strip=True))
    if tuple(fields) != _LIST_LABELS:
        errors.append("list field vocabulary changed")
    return fields, errors


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    ledger: HapcheonLedger,
    requested_page: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    cards = soup.select("#body_content div.cp8card1 > ul > li.column")
    scoped = soup.select("#body_content div.cp8card1 a.a1[href*='amode=view']")
    if len(cards) != len(scoped):
        errors.append("course detail anchors are outside canonical cards")
    for index, card in enumerate(cards, 1):
        item_errors: list[str] = []
        anchors = card.select("a.a1[href]")
        anchor = anchors[0] if len(anchors) == 1 else None
        query: dict[str, list[str]] = {}
        if anchor is None:
            item_errors.append("expected one course detail anchor")
        else:
            parsed = urlparse(urljoin(ledger.url, _clean(anchor.get("href"))))
            query = parse_qs(parsed.query, keep_blank_values=True)
            expected_keys = {"amode", "idx", ledger.facility_key}
            if requested_page > 1:
                expected_keys.add("cpage")
            if (
                not _safe_hapcheon_url(parsed.geturl(), path=ledger.path)
                or set(query) != expected_keys
                or query.get("amode") != ["view"]
                or query.get(ledger.facility_key) != [ledger.facility_value]
                or (
                    requested_page > 1
                    and query.get("cpage") != [str(requested_page)]
                )
                or any(len(values) != 1 for values in query.values())
            ):
                item_errors.append("malformed course detail route")
        identity = _clean((query.get("idx") or [""])[0])
        if not _IDENTITY_RE.fullmatch(identity):
            item_errors.append("missing source identity")

        title_nodes = anchor.select("div.tg1 strong.t1") if anchor else []
        category_nodes = anchor.select("div.tg1 span.t2") if anchor else []
        status_nodes = anchor.select("div.tg1 i.c[data-progress]") if anchor else []
        place_nodes = anchor.select("div.tg2 span.place1") if anchor else []
        title = _clean(title_nodes[0].get_text(" ", strip=True)) if len(title_nodes) == 1 else ""
        category = _clean(category_nodes[0].get_text(" ", strip=True)) if len(category_nodes) == 1 else ""
        branch = (
            _clean(place_nodes[0].get_text(" ", strip=True)).strip("[] ")
            if len(place_nodes) == 1
            else ""
        )
        if not title or len(title_nodes) != 1:
            item_errors.append("expected one nonempty course title")
        if not category or len(category_nodes) != 1:
            item_errors.append("expected one nonempty course category")
        if branch not in ledger.branches:
            item_errors.append(f"course branch is outside official vocabulary: {branch!r}")

        source_status = badge_label = ""
        if len(status_nodes) != 1:
            item_errors.append("expected one source status badge")
        else:
            source_status = _clean(status_nodes[0].get("data-progress"))
            badge_label = _clean(status_nodes[0].get_text(" ", strip=True))
            if source_status not in _STATUS_MAP:
                item_errors.append(f"unknown source status {source_status!r}")
            if badge_label != source_status:
                item_errors.append("source status badge label mismatch")

        fields, field_errors = _parse_fields(anchor) if anchor else ({}, [])
        item_errors.extend(field_errors)
        try:
            event_start, event_end = _range_dates(fields.get("교육기간"))
            apply_start, apply_end = _range_dates(fields.get("신청기간"))
        except HapcheonContractError as exc:
            item_errors.append(str(exc))
            event_start = event_end = apply_start = apply_end = date.min
        if _CANCELLED_RE.search(title):
            item_errors.append("cancelled course remains in active identity ledger")

        if item_errors:
            errors.extend(
                f"{ledger.key} page {requested_page} row {index}: {message}"
                for message in item_errors
            )
            continue
        rows.append(
            {
                "provider": HAPCHEON_PROVIDER,
                "provider_course_id": (
                    f"{HAPCHEON_PROVIDER}:education:{ledger.key}:{identity}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": branch,
                "branch_code": (
                    "gyeongnam-hapcheon-"
                    + hashlib.sha256(branch.encode("utf-8")).hexdigest()[:12]
                ),
                "municipality_code": HAPCHEON_MUNICIPALITY_CODE,
                "municipality_name": HAPCHEON_MUNICIPALITY_NAME,
                "sido": "경상남도",
                "sigungu": "합천군",
                "provider_organizer": branch,
                "venue_name": branch,
                "category": category,
                "program_type": "강좌",
                "raw_url": hapcheon_detail_url(ledger, identity),
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": _STATUS_MAP[source_status],
                "period": fields["교육기간"],
                "start_date": event_start.isoformat(),
                "end_date": event_end.isoformat(),
                "apply_period": fields["신청기간"],
                "apply_start": apply_start.isoformat(),
                "apply_end": apply_end.isoformat(),
                "schedule_raw": "",
                "fee": fields["수강료"],
                "target": "",
                "capacity": None,
                "capacity_total": None,
                "description": title,
                "source_group": "municipal_reservation",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "complete_html_pages+current_detail_summary",
                "raw_fields": {
                    "parser": HAPCHEON_PARSER,
                    "source_catalog": f"hapcheon_{ledger.key}_education",
                    "source_ledger": ledger.key,
                    "source_education_id": identity,
                    "source_page": requested_page,
                    "source_branch": branch,
                    "source_status": source_status,
                    "source_badge_label": badge_label,
                },
            }
        )
    return rows, errors


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("branch")),
            _clean(row.get("category")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _detail_pairs(soup: BeautifulSoup) -> tuple[dict[str, str], list[str]]:
    tables = soup.select("#body_content table.w100")
    if len(tables) != 1:
        return {}, ["expected one public course detail table"]
    pairs: dict[str, str] = {}
    errors: list[str] = []
    for item in tables[0].select("tbody > tr"):
        labels = item.select(":scope > th[scope='row']")
        values = item.select(":scope > td")
        if len(labels) != 1 or len(values) != 1:
            errors.append("malformed public detail row")
            continue
        key = _clean(labels[0].get_text(" ", strip=True))
        if key == "첨부파일" and key in pairs:
            # Some records render one row per attachment.  Attachments are
            # outside the returned allowlist, so their multiplicity is safe.
            continue
        if not key or key in pairs:
            errors.append("duplicate or empty public detail label")
            continue
        pairs[key] = _clean(values[0].get_text(" ", strip=True))
    vocabulary = set(pairs)
    if not _DETAIL_REQUIRED.issubset(vocabulary):
        errors.append("required course detail fields changed")
    if vocabulary - _DETAIL_REQUIRED - _DETAIL_OPTIONAL:
        errors.append("course detail field vocabulary changed")
    return pairs, errors


def _capacity(value: Any) -> Optional[int]:
    match = re.search(r"정원\s*:\s*(\d[\d,]*)", _clean(value))
    return int(match.group(1).replace(",", "")) if match else None


def _application_control(
    soup: BeautifulSoup,
    *,
    ledger: HapcheonLedger,
    identity: str,
    source_status: str,
    application_method: str,
) -> tuple[str, str, list[str]]:
    errors: list[str] = []
    controls = []
    for anchor in soup.select("#body_content div.infomenu1 a[href]"):
        label = _clean(anchor.get_text(" ", strip=True))
        if label in {"신청하기", "교육신청", "접수중", "대기접수", "대기자신청"}:
            controls.append(anchor)
    if len(controls) > 1:
        return "", "", ["multiple public application controls"]
    active_online = (
        source_status in _ACTIVE_SOURCE_STATUSES and "온라인" in application_method
    )
    if not controls:
        if active_online:
            errors.append("active online course lacks public application control")
        return "", "", errors

    control = controls[0]
    label = _clean(control.get_text(" ", strip=True))
    parsed = urlparse(urljoin(ledger.url, _clean(control.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    allowed_keys = {"amode", "lecIdx", ledger.facility_key}
    if (
        not _safe_hapcheon_url(parsed.geturl(), path=ledger.path)
        or not {"amode", "lecIdx"}.issubset(query)
        or set(query) - allowed_keys
        or query.get("amode") not in (["ins"], ["ins_realname"])
        or query.get("lecIdx") != [identity]
        or (
            ledger.facility_key in query
            and query.get(ledger.facility_key) != [ledger.facility_value]
        )
        or any(len(values) != 1 for values in query.values())
    ):
        errors.append("malformed public application control")
    if source_status not in _ACTIVE_SOURCE_STATUSES:
        errors.append("inactive source status exposes application control")
    elif label not in _CONTROL_LABELS[source_status]:
        errors.append("source status/application control label mismatch")
    if "온라인" not in application_method:
        errors.append("application control lacks online application method")
    if errors:
        return label, "", errors
    return label, parsed.geturl(), []


def _detail_row(
    parent: dict[str, Any],
    soup: BeautifulSoup,
    *,
    ledger: HapcheonLedger,
) -> tuple[dict[str, Any], list[str]]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_education_id"))
    source_status = _clean(raw.get("source_status"))
    errors: list[str] = []
    titles = soup.select("#body_content .h1.cv0")
    if len(titles) != 1:
        errors.append("expected one public detail title")
    elif _clean(titles[0].get_text(" ", strip=True)) != _clean(parent.get("title")):
        errors.append("list/detail title mismatch")

    pairs, pair_errors = _detail_pairs(soup)
    errors.extend(pair_errors)
    try:
        detail_start, detail_end = _range_dates(pairs.get("교육기간"))
        apply_start, apply_end = _range_dates(pairs.get("접수기간"))
    except HapcheonContractError as exc:
        errors.append(str(exc))
        detail_start = detail_end = apply_start = apply_end = date.min
    if [detail_start.isoformat(), detail_end.isoformat()] != [
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ]:
        errors.append("list/detail education period mismatch")
    if [apply_start.isoformat(), apply_end.isoformat()] != [
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ]:
        errors.append("list/detail application period mismatch")
    if _clean(pairs.get("수강료")) != _clean(parent.get("fee")):
        errors.append("list/detail fee mismatch")

    method = _clean(pairs.get("접수방법"))
    if not method:
        errors.append("detail application method is empty")
    control_label, control_url, control_errors = _application_control(
        soup,
        ledger=ledger,
        identity=identity,
        source_status=source_status,
        application_method=method,
    )
    errors.extend(control_errors)

    capacity = _capacity(pairs.get("인원"))
    if capacity is None:
        errors.append("detail capacity is malformed")
    application_type = "INFO_ONLY"
    if control_url:
        application_type = (
            "WAITLIST_APPLY" if source_status == "대기접수" else "ONLINE_RESERVATION"
        )
    row = dict(parent)
    row.update(
        {
            "application_url": control_url,
            "application_type": application_type,
            "reservation_available": bool(control_url),
            "status": _STATUS_MAP.get(source_status, ""),
            "schedule_raw": _clean(pairs.get("교육시간")),
            "fee": _clean(pairs.get("수강료")),
            "target": _clean(pairs.get("모집대상")),
            "venue_name": _clean(pairs.get("교육장소")) or _clean(parent.get("branch")),
            "address": _clean(pairs.get("교육장소")),
            "capacity": capacity,
            "capacity_total": capacity,
        }
    )
    row["raw_fields"] = {
        **raw,
        "source_application_method": method,
        "source_application_control": control_label,
        "detail_validated": not errors,
        "application_form_fetched": False,
        "instructor_excluded": True,
        "contact_excluded": True,
        "attachments_excluded": True,
        "preparation_excluded": True,
        "free_text_excluded": True,
        "applicant_data_excluded": True,
    }
    return row, errors


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _empty_meta() -> dict[str, Any]:
    return {
        "ownership_scope": HAPCHEON_OWNERSHIP_SCOPE,
        "municipality_code": HAPCHEON_MUNICIPALITY_CODE,
        "canonical_url": HAPCHEON_URL,
        "portal_mirror_url": HAPCHEON_PORTAL_MIRROR_URL,
        "ledger_urls": {ledger.key: ledger.url for ledger in HAPCHEON_LEDGERS},
        "official_branches": sorted(HAPCHEON_OFFICIAL_BRANCHES),
        "source_total": 0,
        "ledger_totals": {},
        "ledger_pages": {},
        "ledger_current_counts": {},
        "source_status_counts": {},
        "current_status_counts": {},
        "branch_counts": {},
        "current_count": 0,
        "expired_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "application_control_count": 0,
        "semantic_duplicate_count": 0,
        "identity_duplicate_count": 0,
        "list_requests": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "sentinel_mode": "exact_clamped_final_page",
        "sentinel_pages": {},
        "sentinel_counts": {},
        "stable_rechecks": {},
        "pii_payload_persisted": False,
        "privacy_violations": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }


def collect_hapcheon_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 30,
    detail_limit: int = 50,
    max_workers: int = HAPCHEON_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session_factory,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Hapcheon county snapshot."""

    meta = _empty_meta()
    errors: list[str] = []
    try:
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        timeout_value = int(timeout)
        workers = int(max_workers)
        as_of = _today(today)
        if min(allowed_pages, timeout_value, workers) < 1 or allowed_details < 0:
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], HAPCHEON_PARSER, meta
    if not is_hapcheon_education_target(target):
        meta["configured_collection_error"] = "target is outside canonical Hapcheon scope"
        return [], HAPCHEON_PARSER, meta

    current = session_factory()
    all_rows: list[dict[str, Any]] = []
    ledger_signatures: dict[str, dict[int, str]] = {}
    try:
        for ledger in HAPCHEON_LEDGERS:
            soup, _ = _request_soup(
                current,
                hapcheon_list_url(ledger, 1),
                ledger=ledger,
                timeout=timeout_value,
                fetcher=fetcher,
                meta=meta,
            )
            meta["list_requests"] += 1
            total, last_page, form_errors = _form_and_total(
                soup,
                ledger=ledger,
                requested_page=1,
                displayed_page=1,
            )
            errors.extend(f"{ledger.key}: {message}" for message in form_errors)
            if last_page > allowed_pages:
                meta["source_cap_reached"] = True
                errors.append(
                    f"{ledger.key}: max_pages cap allows {allowed_pages} of "
                    f"{last_page} required pages"
                )
            pages: dict[int, list[dict[str, Any]]] = {}
            signatures: dict[int, str] = {}
            first_rows, first_errors = _parse_list_page(
                soup, ledger=ledger, requested_page=1
            )
            errors.extend(first_errors)
            pages[1] = first_rows
            signatures[1] = _page_signature(first_rows)

            if not errors or not meta["source_cap_reached"]:
                for page in range(2, min(last_page, allowed_pages) + 1):
                    page_soup, _ = _request_soup(
                        current,
                        hapcheon_list_url(ledger, page),
                        ledger=ledger,
                        timeout=timeout_value,
                        fetcher=fetcher,
                        meta=meta,
                    )
                    meta["list_requests"] += 1
                    declared_total, declared_last, page_form_errors = _form_and_total(
                        page_soup,
                        ledger=ledger,
                        requested_page=page,
                        displayed_page=page,
                    )
                    errors.extend(
                        f"{ledger.key}: {message}" for message in page_form_errors
                    )
                    if declared_total != total or declared_last != last_page:
                        errors.append(f"{ledger.key}: declared total/page boundary changed")
                    parsed_rows, page_errors = _parse_list_page(
                        page_soup, ledger=ledger, requested_page=page
                    )
                    errors.extend(page_errors)
                    pages[page] = parsed_rows
                    signatures[page] = _page_signature(parsed_rows)

            if last_page <= allowed_pages:
                sentinel_page = last_page + 1
                sentinel_soup, _ = _request_soup(
                    current,
                    hapcheon_list_url(ledger, sentinel_page),
                    ledger=ledger,
                    timeout=timeout_value,
                    fetcher=fetcher,
                    meta=meta,
                )
                meta["list_requests"] += 1
                sentinel_total, sentinel_last, sentinel_form_errors = _form_and_total(
                    sentinel_soup,
                    ledger=ledger,
                    requested_page=sentinel_page,
                    displayed_page=last_page,
                )
                errors.extend(
                    f"{ledger.key}: {message}" for message in sentinel_form_errors
                )
                sentinel_rows, sentinel_errors = _parse_list_page(
                    sentinel_soup, ledger=ledger, requested_page=sentinel_page
                )
                errors.extend(sentinel_errors)
                if sentinel_total != total or sentinel_last != last_page:
                    errors.append(f"{ledger.key}: exact post-last boundary changed")
                if _page_signature(sentinel_rows) != signatures.get(last_page):
                    errors.append(f"{ledger.key}: immediate post-last clamp is not final page")
                meta["sentinel_pages"][ledger.key] = sentinel_page
                meta["sentinel_counts"][ledger.key] = len(sentinel_rows)

                recheck_pages = (1,) if last_page == 1 else (1, last_page)
                stable: dict[str, bool] = {}
                for page in recheck_pages:
                    recheck_soup, _ = _request_soup(
                        current,
                        hapcheon_list_url(ledger, page),
                        ledger=ledger,
                        timeout=timeout_value,
                        fetcher=fetcher,
                        meta=meta,
                    )
                    meta["list_requests"] += 1
                    _, _, recheck_form_errors = _form_and_total(
                        recheck_soup,
                        ledger=ledger,
                        requested_page=page,
                        displayed_page=page,
                    )
                    errors.extend(
                        f"{ledger.key}: {message}" for message in recheck_form_errors
                    )
                    recheck_rows, recheck_errors = _parse_list_page(
                        recheck_soup, ledger=ledger, requested_page=page
                    )
                    errors.extend(recheck_errors)
                    stable[str(page)] = (
                        _page_signature(recheck_rows) == signatures.get(page)
                    )
                    if not stable[str(page)]:
                        errors.append(f"{ledger.key}: page {page} changed on recheck")
                meta["stable_rechecks"][ledger.key] = stable

            flattened = [
                row
                for page in range(1, last_page + 1)
                for row in pages.get(page, [])
            ]
            expected_last_count = total - HAPCHEON_PAGE_SIZE * (last_page - 1)
            for page in range(1, last_page):
                if len(pages.get(page, [])) != HAPCHEON_PAGE_SIZE:
                    errors.append(
                        f"{ledger.key}: page {page} row count changed "
                        f"({len(pages.get(page, []))})"
                    )
            if len(pages.get(last_page, [])) != expected_last_count:
                errors.append(
                    f"{ledger.key}: final page expected {expected_last_count} rows, "
                    f"got {len(pages.get(last_page, []))}"
                )
            if len(flattened) != total:
                errors.append(
                    f"{ledger.key}: parsed {len(flattened)} of declared {total} rows"
                )
            identities = [_clean(row.get("provider_course_id")) for row in flattened]
            duplicate_count = len(identities) - len(set(identities))
            meta["identity_duplicate_count"] += duplicate_count
            if duplicate_count:
                errors.append(f"{ledger.key}: duplicate source identities")

            meta["ledger_totals"][ledger.key] = total
            meta["ledger_pages"][ledger.key] = last_page
            ledger_signatures[ledger.key] = signatures
            all_rows.extend(flattened)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {_clean(exc)}")
    finally:
        _close_quietly(current)

    meta["source_total"] = sum(meta["ledger_totals"].values())
    meta["source_status_counts"] = dict(
        Counter(
            _clean(row.get("raw_fields", {}).get("source_status"))
            for row in all_rows
        )
    )
    meta["branch_counts"] = dict(Counter(_clean(row.get("branch")) for row in all_rows))
    current_rows = [
        row
        for row in all_rows
        if date.fromisoformat(_clean(row.get("end_date"))) >= as_of
    ]
    meta["current_count"] = len(current_rows)
    meta["expired_count"] = len(all_rows) - len(current_rows)
    meta["ledger_current_counts"] = dict(
        Counter(_clean(row.get("raw_fields", {}).get("source_ledger")) for row in current_rows)
    )
    if len(current_rows) > allowed_details:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {len(current_rows)} "
            "required current/future details"
        )

    detailed: list[dict[str, Any]] = []
    if not errors:
        detail_session = session_factory()
        try:
            for row in current_rows:
                ledger_key = _clean(row.get("raw_fields", {}).get("source_ledger"))
                ledger = HAPCHEON_LEDGER_BY_KEY.get(ledger_key)
                identity = _clean(row.get("raw_fields", {}).get("source_education_id"))
                if ledger is None:
                    errors.append(f"detail {identity}: unknown source ledger")
                    continue
                meta["detail_attempts"] += 1
                try:
                    soup, _ = _request_soup(
                        detail_session,
                        hapcheon_detail_url(ledger, identity),
                        ledger=ledger,
                        timeout=timeout_value,
                        fetcher=fetcher,
                        meta=meta,
                    )
                    detail_row, detail_errors = _detail_row(
                        row, soup, ledger=ledger
                    )
                    errors.extend(
                        f"detail {identity}: {message}" for message in detail_errors
                    )
                    if not detail_errors:
                        detailed.append(detail_row)
                except Exception as exc:
                    errors.append(
                        f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                    )
        finally:
            _close_quietly(detail_session)

    details_complete = bool(
        not errors
        and meta["detail_attempts"] == len(current_rows)
        and len(detailed) == len(current_rows)
    )
    result: list[dict[str, Any]] = []
    if details_complete:
        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(detailed))
        if len(result) != len(detailed):
            meta["semantic_duplicate_count"] = len(detailed) - len(result)
            errors.append("dedupe changed complete current/future identity count")

    serialized = repr(result)
    privacy_violations = int(bool(_PHONE_RE.search(serialized))) + int(
        bool(_EMAIL_RE.search(serialized))
    )
    if any(
        token in serialized
        for token in ("강사명", "교육소개", "첨부파일", "준비물", "현재접수인원")
    ):
        privacy_violations += 1
    if privacy_violations:
        errors.append("returned payload violates the public-summary PII allowlist")
        result = []

    meta["detail_pages"] = len(detailed)
    meta["application_control_count"] = sum(
        bool(row.get("application_url")) for row in result
    )
    meta["current_status_counts"] = dict(
        Counter(_clean(row.get("status")) for row in result)
    )
    meta["privacy_violations"] = privacy_violations
    meta["pii_payload_persisted"] = False
    meta["details_complete"] = bool(details_complete and not errors)
    meta["pagination_complete"] = bool(
        not meta["source_cap_reached"]
        and meta["source_total"] == len(all_rows)
        and len(meta["ledger_totals"]) == len(HAPCHEON_LEDGERS)
        and not any("page" in error or "total" in error or "boundary" in error for error in errors)
    )
    meta["snapshot_complete"] = bool(
        meta["pagination_complete"] and meta["details_complete"] and not errors
    )
    meta["full_snapshot_validated"] = meta["snapshot_complete"]
    meta["request_retry_count"] = meta["physical_requests"] - meta["logical_requests"]
    meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
    if errors:
        return [], HAPCHEON_PARSER, meta
    return result, HAPCHEON_PARSER, meta


collect = collect_hapcheon_education


__all__ = [
    "HAPCHEON_CANONICAL_CANDIDATE_ID",
    "HAPCHEON_CANDIDATE_DECISIONS",
    "HAPCHEON_HOST",
    "HAPCHEON_LEDGERS",
    "HAPCHEON_LITERACY_URL",
    "HAPCHEON_MUNICIPALITY_CODE",
    "HAPCHEON_MUNICIPALITY_NAME",
    "HAPCHEON_OFFICIAL_BRANCHES",
    "HAPCHEON_OWNER_BOUNDARIES",
    "HAPCHEON_PARSER",
    "HAPCHEON_PORTAL_MIRROR_URL",
    "HAPCHEON_PROVIDER",
    "HAPCHEON_REVIEW_CANDIDATE_ID",
    "HAPCHEON_REVIEW_PROVIDER",
    "HAPCHEON_URL",
    "HapcheonContractError",
    "HapcheonLedger",
    "collect",
    "collect_hapcheon_education",
    "hapcheon_detail_url",
    "hapcheon_list_url",
    "is_hapcheon_education_target",
    "is_target",
]
