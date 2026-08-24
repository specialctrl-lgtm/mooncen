"""Fail-closed collector for Gunpo's mixed public event/reservation ledger.

The official ``행사/모집`` catalogue is not an education owner and is kept
strictly separate from :mod:`Crawler.municipal_gunpo`, which owns nine
independent education catalogues.  This provider reads the complete mixed
ledger, validates every current/future public detail, and returns only rows
that the public title/detail explicitly identifies as a real experience or
visit programme.

Known non-program families (notices, administrative benefits, employment or
participant recruitment, facility rental, committees/clubs, and service
applications) are excluded by explicit rules.  An unfamiliar current/future
row fails the whole snapshot rather than risking an incomplete experience
partition.  Only public list and detail GET routes are allowlisted.  Login,
authentication, identity, applicant, application, attachment, download and
personal-data endpoints are never requested.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


GUNPO_EXPERIENCE_PROVIDER = "MUNI_WWW_GUNPO_GO_KR_F18F83BB"
GUNPO_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_4AEEF3A1262A"
GUNPO_EXPERIENCE_URL = (
    "https://www.gunpo.go.kr/portal/webEtcResveList.do?key=1008275&rep=1"
)
GUNPO_EXPERIENCE_LIST_ENDPOINT = (
    "https://www.gunpo.go.kr/portal/webEtcResveList.do"
)
GUNPO_EXPERIENCE_DETAIL_ENDPOINT = (
    "https://www.gunpo.go.kr/portal/webEtcResveView.do"
)
GUNPO_EXPERIENCE_APPLICATION_ENDPOINT = (
    "https://www.gunpo.go.kr/portal/webEtcResveApplcntAgree.do"
)
GUNPO_EXPERIENCE_BRANCH = "군포시 통합예약 행사/모집"
GUNPO_EXPERIENCE_MUNICIPALITY_CODE = "4141000000"
GUNPO_EXPERIENCE_MUNICIPALITY_NAME = "경기도 군포시"
GUNPO_EXPERIENCE_PAGE_SIZE = 1_000
GUNPO_EXPERIENCE_MAX_PAGES = 20
GUNPO_EXPERIENCE_MAX_DETAILS = 100
GUNPO_EXPERIENCE_REQUEST_LIMIT = 150
GUNPO_EXPERIENCE_MAX_BYTES = 2_000_000
GUNPO_EXPERIENCE_PARSER = (
    "gunpo_event_experience_complete_ledger+pageunit1000+empty_post_last_sentinel+"
    "stable_first_last_sentinel+all_current_public_details+deterministic_mixed_"
    "ledger_partition+unknown_fail_closed+locked_experience+list_detail_only_tls"
)
GUNPO_EXPERIENCE_OWNERSHIP_SCOPE = (
    "gunpo_official_event_ledger_explicit_experience_current_future"
)
GUNPO_EXPERIENCE_LIVE_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_total": 89,
    "data_pages": 1,
    "sentinel_page": 2,
    "source_current_count": 1,
    "experience_current_count": 1,
    "current_identity": "1721",
}


SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*건")
_PAGE_RE = re.compile(r"\[\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\]")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_ADDRESS_RE = re.compile(
    r"(?:경기도\s+)?군포시\s+(?P<road>[가-힣0-9·]+(?:로|길)\s*\d+(?:-\d+)?)"
)

_STATUS = {
    "접수중": "OPEN",
    "모집중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "완료": "CLOSED",
    "모집마감": "CLOSED",
    "접수마감": "CLOSED",
}

_NOTICE_MARKERS = ("공지사항", "[공지]", "(공지)", "예약 금지", "신청하지 마세요")
_TEST_MARKERS = ("테스트", "test record", "시험용")
_ADMIN_BENEFIT_MARKERS = (
    "재난지원금",
    "지원금 신청",
    "지원사업",
    "보조금 신청",
    "수당 신청",
    "상자텃밭 지원",
)
_FACILITY_RENTAL_MARKERS = (
    "대관",
    "시설대여",
    "시설 대여",
    "공간대여",
    "공간 대여",
    "사용허가",
    "주차장",
)
_COMMITTEE_CLUB_MARKERS = (
    "위원회",
    "위원 모집",
    "참여단",
    "시민단",
    "동아리",
    "클럽",
    "회원 모집",
    "서포터즈",
)
_SERVICE_MARKERS = (
    "체험 서비스",
    "견학 서비스",
    "서비스 신청",
    "서비스 이용",
    "안심콜",
)
_JOB_RECRUITMENT_MARKERS = (
    "인턴",
    "아르바이트",
    "채용",
    "근로자 모집",
    "활동가 모집",
    "강사 모집",
    "지도자 모집",
    "운영자 모집",
)
_EXPERIENCE_MARKERS = ("체험", "견학")
_EDUCATION_ONLY_MARKERS = (
    "입시설명회",
    "입시 설명회",
    "수험생",
    "드론교육",
    "시민대학",
    "농부학교",
    "특강",
)
_EVENT_INFORMATION_MARKERS = (
    "설명회",
    "토론회",
    "설문조사",
    "선호도 조사",
    "명칭 공모",
    "후보자",
    "수상자",
    "시민대상",
)
_GENERAL_RECRUITMENT_MARKERS = (
    "참가 신청",
    "참가자 모집",
    "참여 신청",
    "참여자 모집",
    "참석 신청",
    "참석자 모집",
    "수강생 모집",
    "모집",
    "선발",
    "추천",
)

_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "attachments",
        "attachment_url",
        "download_url",
        "applicant",
        "identity",
        "detail_text",
        "request_form",
    }
)


class GunpoExperienceContractError(ValueError):
    """Raised when the audited Gunpo public experience contract changes."""


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
        raise GunpoExperienceContractError(
            f"{name} must be a positive integer"
        ) from exc
    if result < 1:
        raise GunpoExperienceContractError(f"{name} must be a positive integer")
    return result


def _query(parsed: Any) -> dict[str, list[str]]:
    return parse_qs(parsed.query, keep_blank_values=True)


def _exact_target_url(value: str) -> bool:
    got = urlparse(value)
    wanted = urlparse(GUNPO_EXPERIENCE_URL)
    return bool(
        got.scheme == "https"
        and got.hostname == wanted.hostname
        and got.port is None
        and got.path == wanted.path
        and _query(got) == _query(wanted)
        and not got.params
        and not got.fragment
        and not got.username
        and not got.password
    )


def is_gunpo_experience_target(target: Any) -> bool:
    return (
        _provider(target) == GUNPO_EXPERIENCE_PROVIDER
        and _exact_target_url(_target_url(target))
    )


is_target = is_gunpo_experience_target


def gunpo_experience_list_url(page: int) -> str:
    page_number = _positive(page, "page")
    return GUNPO_EXPERIENCE_LIST_ENDPOINT + "?" + urlencode(
        {
            "key": "1008275",
            "rep": "1",
            "pageUnit": str(GUNPO_EXPERIENCE_PAGE_SIZE),
            "pageIndex": str(page_number),
        }
    )


def gunpo_experience_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise GunpoExperienceContractError("invalid event reservation identity")
    return GUNPO_EXPERIENCE_DETAIL_ENDPOINT + "?" + urlencode(
        {"key": "1008275", "searchEtcResveNo": value}
    )


def _assert_safe_public_url(value: str) -> str:
    parsed = urlparse(_clean(value))
    if not (
        parsed.scheme == "https"
        and parsed.hostname == "www.gunpo.go.kr"
        and parsed.port is None
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        raise GunpoExperienceContractError("request escaped the audited Gunpo host")
    query = _query(parsed)
    if parsed.path == "/portal/webEtcResveList.do":
        if set(query) != {"key", "rep", "pageUnit", "pageIndex"}:
            raise GunpoExperienceContractError("unexpected event-list query")
        if (
            query["key"] != ["1008275"]
            or query["rep"] != ["1"]
            or query["pageUnit"] != [str(GUNPO_EXPERIENCE_PAGE_SIZE)]
        ):
            raise GunpoExperienceContractError("event-list ownership changed")
        _positive(query["pageIndex"][0], "pageIndex")
        return "list"
    if parsed.path == "/portal/webEtcResveView.do":
        if set(query) != {"key", "searchEtcResveNo"}:
            raise GunpoExperienceContractError("unexpected event-detail query")
        identity = query["searchEtcResveNo"][0]
        if query["key"] != ["1008275"] or not _IDENTITY_RE.fullmatch(identity):
            raise GunpoExperienceContractError("event-detail ownership changed")
        return "detail"
    raise GunpoExperienceContractError(
        "application/login/attachment/private endpoint refused"
    )


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
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


def _response_soup(response: Any, expected_url: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise GunpoExperienceContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise GunpoExperienceContractError("redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if final_url:
        _assert_safe_public_url(final_url)
        got = urlparse(final_url)
        wanted = urlparse(expected_url)
        if got.path != wanted.path or _query(got) != _query(wanted):
            raise GunpoExperienceContractError("response URL changed the audited query")
    content = getattr(response, "content", b"")
    if isinstance(content, str):
        content = content.encode("utf-8")
    if content and len(content) > GUNPO_EXPERIENCE_MAX_BYTES:
        raise GunpoExperienceContractError("public response exceeded the byte limit")
    text = str(getattr(response, "text", "") or "")
    if not text and content:
        text = bytes(content).decode("utf-8", errors="replace")
    if not text:
        raise GunpoExperienceContractError("empty public response")
    soup = BeautifulSoup(text, "lxml")
    page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "로그인" in page_title or "안내메시지" in page_title:
        raise GunpoExperienceContractError("public page resolved to a guard page")
    return soup


class _Runner:
    def __init__(self, factory: SessionFactory, timeout: int) -> None:
        self.factory = factory
        self.timeout = timeout
        self.session: Any = None
        self.requests = 0
        self.sessions_created = 0

    def __enter__(self) -> "_Runner":
        self.session = self.factory()
        self.sessions_created = 1
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

    def soup(self, url: str, *, referer: str = "") -> BeautifulSoup:
        _assert_safe_public_url(url)
        if self.requests >= GUNPO_EXPERIENCE_REQUEST_LIMIT:
            raise GunpoExperienceContractError("audited request budget exceeded")
        self.requests += 1
        response = self.session.get(
            url,
            timeout=self.timeout,
            allow_redirects=False,
            headers={"Referer": referer} if referer else None,
            verify=True,
        )
        return _response_soup(response, url)


def _dates(value: Any, field: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise GunpoExperienceContractError(f"{field} must contain one date range")
    parsed = tuple(date(int(y), int(m), int(d)) for y, m, d in matches)
    if parsed[1] < parsed[0]:
        raise GunpoExperienceContractError(f"{field} date range is reversed")
    return parsed[0], parsed[1]


def _four_dates(value: Any) -> tuple[date, date, date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 4:
        raise GunpoExperienceContractError("list row must expose two complete date ranges")
    parsed = tuple(date(int(y), int(m), int(d)) for y, m, d in matches)
    if parsed[1] < parsed[0] or parsed[3] < parsed[2]:
        raise GunpoExperienceContractError("list row contains a reversed date range")
    return parsed  # type: ignore[return-value]


def _page_declaration(soup: BeautifulSoup) -> tuple[int, int, int]:
    root = soup.select_one("#contents") or soup
    text = _clean(root.get_text(" ", strip=True))
    totals = {int(value.replace(",", "")) for value in _TOTAL_RE.findall(text)}
    pages = {(int(current), int(last)) for current, last in _PAGE_RE.findall(text)}
    if len(totals) != 1 or len(pages) != 1:
        raise GunpoExperienceContractError(
            "missing unambiguous total/page declaration"
        )
    total = totals.pop()
    current, last = pages.pop()
    if current < 1 or last < 1:
        raise GunpoExperienceContractError("invalid declared page boundary")
    return total, current, last


def _assert_list_shell(soup: BeautifulSoup) -> None:
    form = soup.select_one("form#frm")
    if form is None or _clean(form.get("method")).lower() != "get":
        raise GunpoExperienceContractError("missing public event-list GET form")
    if not _clean(form.get("action")).endswith("webEtcResveList.do"):
        raise GunpoExperienceContractError("event-list form action changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[type=hidden][name]")
    }
    if (
        hidden.get("key") != "1008275"
        or hidden.get("rep") != "1"
        or hidden.get("searchGubun") != "S"
    ):
        raise GunpoExperienceContractError("mixed event ledger ownership changed")


def _method(value: str) -> str:
    methods: list[str] = []
    if "온라인" in value:
        methods.append("온라인")
    if "방문" in value:
        methods.append("방문")
    return ", ".join(methods) or _clean(value)


def _list_rows(soup: BeautifulSoup) -> list[dict[str, Any]]:
    _assert_list_shell(soup)
    root = soup.select_one("#contents") or soup
    rows: list[dict[str, Any]] = []
    for table_row in root.select("table.p-table tbody tr"):
        links = table_row.select(
            "a[href*='webEtcResveView.do'][href*='searchEtcResveNo=']"
        )
        if not links:
            continue
        if len(links) != 1:
            raise GunpoExperienceContractError("ambiguous event-list detail link")
        link = links[0]
        parsed = urlparse(urljoin(GUNPO_EXPERIENCE_URL, _clean(link.get("href"))))
        query = _query(parsed)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.gunpo.go.kr"
            or parsed.path != "/portal/webEtcResveView.do"
            or set(query) != {"key", "searchEtcResveNo"}
            or query.get("key") != ["1008275"]
        ):
            raise GunpoExperienceContractError("event-list detail link escaped owner")
        identity = query["searchEtcResveNo"][0]
        if not _IDENTITY_RE.fullmatch(identity):
            raise GunpoExperienceContractError("invalid event-list identity")
        cells = [
            _clean(cell.get_text(" ", strip=True))
            for cell in table_row.find_all("td", recursive=False)
        ]
        if len(cells) != 6:
            raise GunpoExperienceContractError("event-list table schema changed")
        source_status = cells[0]
        if source_status not in _STATUS:
            raise GunpoExperienceContractError(
                f"unknown event-list status {source_status!r}"
            )
        title = _clean(link.get_text(" ", strip=True))
        if not title or title not in cells[2]:
            raise GunpoExperienceContractError("event-list title contract changed")
        apply_start, apply_end, event_start, event_end = _four_dates(cells[3])
        detail_url = gunpo_experience_detail_url(identity)
        rows.append(
            {
                "provider": GUNPO_EXPERIENCE_PROVIDER,
                "provider_course_id": (
                    f"{GUNPO_EXPERIENCE_PROVIDER}:event:{identity}"
                ),
                "prefer_incoming_provider_course_id": True,
                "source_course_id": identity,
                "title": title,
                "branch": GUNPO_EXPERIENCE_BRANCH,
                "preserve_branch": True,
                "raw_url": detail_url,
                "source_url": detail_url,
                "application_url": "",
                "status": _STATUS[source_status],
                "course_status": _STATUS[source_status],
                "source_status": source_status,
                "reservation_available": False,
                "registration_start_date": apply_start.isoformat(),
                "registration_end_date": apply_end.isoformat(),
                "apply_start_date": apply_start.isoformat(),
                "apply_end_date": apply_end.isoformat(),
                "apply_period": (
                    f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
                ),
                "start_date": event_start.isoformat(),
                "end_date": event_end.isoformat(),
                "period": f"{event_start.isoformat()} ~ {event_end.isoformat()}",
                "target": cells[4],
                "target_audience": cells[4],
                "application_method_raw": _method(cells[5]),
                "collection_category": "공공예약",
                "domain_category": "체험·견학",
                "category": "체험·견학",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "체험",
                "service_group_policy": "locked",
                "program_type": "체험",
                "program_type_source": "explicit_mixed_ledger_partition",
                "classification_locked": True,
                "collection_type": GUNPO_EXPERIENCE_PARSER,
                "municipality_code": GUNPO_EXPERIENCE_MUNICIPALITY_CODE,
                "municipality_name": GUNPO_EXPERIENCE_MUNICIPALITY_NAME,
                "municipality_full_name": GUNPO_EXPERIENCE_MUNICIPALITY_NAME,
                "region": GUNPO_EXPERIENCE_MUNICIPALITY_NAME,
                "sido": "경기도",
                "sigungu": "군포시",
                "raw_fields": {
                    "parser": GUNPO_EXPERIENCE_PARSER,
                    "reservation_id": identity,
                    "official_ledger": "행사/모집",
                    "source_owner": cells[1],
                    "source_status": source_status,
                    "classification_state": "pending_public_detail",
                    "contact_omitted": True,
                },
            }
        )
    return rows


def _fingerprint(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("status")),
            _clean(row.get("apply_period")),
            _clean(row.get("period")),
        )
        for row in rows
    )


def _table_pairs(root: Tag) -> dict[str, str]:
    result: dict[str, str] = {}
    for table_row in root.select("table.p-table tr"):
        cells = table_row.find_all(["th", "td"], recursive=False)
        for index, cell in enumerate(cells):
            if cell.name != "th":
                continue
            value = next(
                (candidate for candidate in cells[index + 1 :] if candidate.name == "td"),
                None,
            )
            if value is None:
                raise GunpoExperienceContractError("detail labelled table changed")
            key = _clean(cell.get_text(" ", strip=True))
            if key in {"문의", "첨부파일"}:
                continue
            if key in result:
                raise GunpoExperienceContractError("duplicate detail label")
            result[key] = _clean(value.get_text(" ", strip=True))
    return result


def _program_text(root: Tag) -> str:
    clone = BeautifulSoup(str(root), "lxml")
    for node in clone.select(
        "table.p-table, .p-attach, .btn_group, script, style, form"
    ):
        node.decompose()
    return _clean(clone.get_text(" ", strip=True))[:100_000]


def _has_any(value: str, markers: Iterable[str]) -> bool:
    return any(marker.casefold() in value.casefold() for marker in markers)


def _classification(title: str, detail_program_text: str) -> tuple[bool, str]:
    """Return a deterministic mixed-ledger classification.

    Unknown current/future rows are not silently treated as non-experiences;
    the caller turns ``ambiguous`` into an atomic snapshot failure.
    """

    clean_title = _clean(title)
    if _has_any(clean_title, _TEST_MARKERS):
        return False, "test"
    if _has_any(clean_title, _NOTICE_MARKERS) or clean_title.startswith("공지"):
        return False, "notice"
    if _has_any(clean_title, _ADMIN_BENEFIT_MARKERS):
        return False, "administrative_benefit"
    if _has_any(clean_title, _FACILITY_RENTAL_MARKERS):
        return False, "facility_rental"
    if _has_any(clean_title, _COMMITTEE_CLUB_MARKERS):
        return False, "committee_or_club"
    if _has_any(clean_title, _SERVICE_MARKERS):
        return False, "service_application"
    if _has_any(clean_title, _JOB_RECRUITMENT_MARKERS):
        return False, "employment_recruitment"

    if _has_any(clean_title, _EXPERIENCE_MARKERS):
        return True, "explicit_experience_title"
    explicit_detail = bool(
        re.search(
            r"(?:체험|견학)\s*(?:프로그램|활동|행사|교육|수업|강좌|신청|예약|운영)",
            detail_program_text,
        )
        or re.search(
            r"(?:시설|현장|기관)\s*견학|만들기\s*체험",
            detail_program_text,
        )
    )
    if explicit_detail:
        return True, "explicit_experience_detail"

    if _has_any(clean_title, _EDUCATION_ONLY_MARKERS):
        return False, "education_only"
    if _has_any(clean_title, _EVENT_INFORMATION_MARKERS):
        return False, "event_or_information"
    if _has_any(clean_title, _GENERAL_RECRUITMENT_MARKERS):
        return False, "other_recruitment"
    if "사업" in clean_title and ("신청" in clean_title or "참여" in clean_title):
        return False, "administrative_application"
    return False, "ambiguous"


def _application_control(root: Tag, expected_identity: str) -> bool:
    candidates = {
        urljoin(GUNPO_EXPERIENCE_URL, _clean(node.get("href")))
        for node in root.select("a[href*='webEtcResveApplcntAgree.do']")
    }
    if len(candidates) > 1:
        raise GunpoExperienceContractError("multiple application controls discovered")
    if not candidates:
        return False
    parsed = urlparse(next(iter(candidates)))
    query = _query(parsed)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.gunpo.go.kr"
        or parsed.path != "/portal/webEtcResveApplcntAgree.do"
        or set(query) != {"key", "searchEtcResveNo"}
        or query.get("key") != ["1008275"]
        or query.get("searchEtcResveNo") != [expected_identity]
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise GunpoExperienceContractError("application control identity changed")
    return True


def _venue_address(value: str) -> str:
    match = _ADDRESS_RE.search(_clean(value))
    if not match:
        return ""
    road = re.sub(r"(?<=[가-힣])(?=\d)", " ", _clean(match.group("road")))
    return f"경기도 군포시 {road}"


def _venue_name(title: str, location: str) -> str:
    title_match = re.match(
        r"^20\d{2}년\s*(.+?)\s*(?:체험|견학)\s*프로그램",
        _clean(title),
    )
    if title_match:
        return _clean(title_match.group(1))
    clean_location = _clean(location)
    if ")" in clean_location:
        suffix = _clean(clean_location.rsplit(")", 1)[-1])
        if suffix and not _PHONE_RE.search(suffix):
            return suffix
    return ""


def _capacity(value: str) -> tuple[int, int, int, int]:
    numbers = [int(item.replace(",", "")) for item in re.findall(r"\d[\d,]*", value)]
    if len(numbers) != 4:
        raise GunpoExperienceContractError("experience capacity contract changed")
    current, total, waiting, waiting_total = numbers
    if min(numbers) < 0 or total < 1 or waiting_total < 0:
        raise GunpoExperienceContractError("invalid experience capacity")
    return current, total, waiting, waiting_total


def _enrich_and_classify(
    soup: BeautifulSoup, row: dict[str, Any]
) -> tuple[bool, str]:
    root = soup.select_one("#contents .etc.view .bbs__view")
    if root is None:
        raise GunpoExperienceContractError("missing public event-detail root")
    title_nodes = root.select(".detail_info_title .p-table__subject_text")
    if len(title_nodes) != 1:
        raise GunpoExperienceContractError("missing unambiguous detail title")
    title = _clean(title_nodes[0].get_text(" ", strip=True))
    if title != row["title"]:
        raise GunpoExperienceContractError("event-detail title does not match list")
    fields = _table_pairs(root)
    if not {"신청기간", "행사기간"}.issubset(fields):
        raise GunpoExperienceContractError("event-detail date fields changed")
    apply_start, apply_end = _dates(fields["신청기간"], "detail application period")
    event_start, event_end = _dates(fields["행사기간"], "detail event period")
    if (
        apply_start.isoformat() != row["registration_start_date"]
        or apply_end.isoformat() != row["registration_end_date"]
        or event_start.isoformat() != row["start_date"]
        or event_end.isoformat() != row["end_date"]
    ):
        raise GunpoExperienceContractError("event-detail dates do not match list")

    control = _application_control(root, row["source_course_id"])
    is_experience, reason = _classification(title, _program_text(root))
    row["raw_fields"].update(
        {
            "classification_state": reason,
            "application_control_present": control,
            "application_endpoint_called": False,
            "attachment_endpoints_called": False,
            "detail_public_contract": True,
        }
    )
    if not is_experience:
        return False, reason

    required = {"장소", "신청방법", "신청대상", "주최", "신청현황"}
    if not required.issubset(fields):
        raise GunpoExperienceContractError("experience detail fields changed")
    if row["status"] == "OPEN" and not control:
        raise GunpoExperienceContractError("open experience lost application control")
    capacity_current, capacity_total, waiting, waiting_total = _capacity(
        fields["신청현황"]
    )
    location = fields["장소"]
    venue_name = _venue_name(title, location)
    venue_address = _venue_address(location)
    branch = venue_name or GUNPO_EXPERIENCE_BRANCH
    branch_digest = hashlib.sha1(
        f"{GUNPO_EXPERIENCE_PROVIDER}|{branch}".encode("utf-8")
    ).hexdigest()[:12].upper()
    row.update(
        {
            "branch": branch,
            "branch_code": f"GUNPO_EXP_{branch_digest}",
            "room": venue_name,
            "venue_name": venue_name,
            "venue_address": venue_address,
            "address": venue_address,
            "organizer": fields["주최"],
            "target": fields["신청대상"],
            "target_audience": fields["신청대상"],
            "eligibility_raw": fields["신청대상"],
            "application_method_raw": _method(fields["신청방법"]),
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "capacity_remaining": max(capacity_total - capacity_current, 0),
            "waitlist_current": waiting,
            "waitlist_total": waiting_total,
            "reservation_available": row["status"] == "OPEN" and control,
            # Keep navigation on the audited public detail.  The application
            # control is verified but its endpoint is deliberately not stored.
            "application_url": (
                row["raw_url"] if row["status"] == "OPEN" and control else ""
            ),
            "application_type": (
                "ONLINE_RESERVATION"
                if row["status"] == "OPEN" and control
                else "INFO_ONLY"
            ),
        }
    )
    row["raw_fields"].update(
        {
            "classification_state": "explicit_experience",
            "classification_basis": reason,
            "venue_source": "official_public_detail",
            "contact_omitted": True,
            "attachments_omitted": True,
        }
    )
    return True, reason


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_OUTPUT_KEYS:
        errors.append("forbidden PII/free-text key")
    safe_url_keys = {"source_url", "raw_url", "application_url"}
    payload = repr({key: value for key, value in row.items() if key not in safe_url_keys})
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload) or _RESIDENT_ID_RE.search(payload):
        errors.append("PII-like value escaped output allowlist")
    for key in safe_url_keys:
        value = _clean(row.get(key))
        if value and any(
            token in urlparse(value).path.casefold()
            for token in ("applcnt", "download", "login", "auth", "attach")
        ):
            errors.append("private/application URL escaped output allowlist")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _redact_error(value: Any) -> str:
    text = _clean(value)
    text = _PHONE_RE.sub("[redacted]", text)
    text = _EMAIL_RE.sub("[redacted]", text)
    return _RESIDENT_ID_RE.sub("[redacted]", text)


def _failure(message: str) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "physical_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "source_total": 0,
        "source_rows": 0,
        "source_current_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "sentinel_verified": False,
        "stable_first_page": False,
        "stable_last_page": False,
        "stable_sentinel": False,
        "details_complete": False,
        "classification_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "application_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "pii_endpoints_called": 0,
        "tls_verification_disabled": False,
        "configured_collection_error": message,
        "ownership_scope": GUNPO_EXPERIENCE_OWNERSHIP_SCOPE,
        "municipality_code": GUNPO_EXPERIENCE_MUNICIPALITY_CODE,
    }


def collect_gunpo_experience_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = GUNPO_EXPERIENCE_MAX_PAGES,
    detail_limit: int = GUNPO_EXPERIENCE_MAX_DETAILS,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future explicit-experience snapshot."""

    if not is_gunpo_experience_target(target):
        return [], GUNPO_EXPERIENCE_PARSER, _failure(
            "target does not match the audited Gunpo event experience owner"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], GUNPO_EXPERIENCE_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory

    source_total = 0
    data_pages = 0
    list_requests = 0
    detail_attempts = 0
    detail_pages = 0
    source_current_count = 0
    source_cap_reached = False
    stable_first = False
    stable_last = False
    stable_sentinel = False
    page_rows: dict[int, list[dict[str, Any]]] = {}
    exclusion_counts: Counter[str] = Counter()
    runner: Optional[_Runner] = None
    try:
        allowed_pages = _positive(max_pages, "max_pages")
        allowed_details = _positive(detail_limit, "detail_limit")
        request_timeout = _positive(timeout, "timeout")
        cutoff = _today(today)
        with _Runner(session_factory, request_timeout) as runner:
            first_soup = runner.soup(gunpo_experience_list_url(1))
            list_requests += 1
            source_total, current_page, declared_last = _page_declaration(first_soup)
            if current_page != 1:
                raise GunpoExperienceContractError("first page declaration changed")
            data_pages = max(1, math.ceil(source_total / GUNPO_EXPERIENCE_PAGE_SIZE))
            if declared_last != data_pages:
                raise GunpoExperienceContractError(
                    "declared last page disagrees with source total"
                )
            page_rows[1] = _list_rows(first_soup)
            required_list_requests = data_pages + 3 + (1 if data_pages > 1 else 0)
            if required_list_requests > allowed_pages:
                source_cap_reached = True
                raise GunpoExperienceContractError(
                    f"max_pages cap allows {allowed_pages} of "
                    f"{required_list_requests} required list requests"
                )

            for page in range(2, data_pages + 1):
                soup = runner.soup(
                    gunpo_experience_list_url(page),
                    referer=GUNPO_EXPERIENCE_URL,
                )
                list_requests += 1
                total, declared_page, last = _page_declaration(soup)
                if (total, declared_page, last) != (source_total, page, data_pages):
                    raise GunpoExperienceContractError(
                        "event-list page declaration changed during snapshot"
                    )
                page_rows[page] = _list_rows(soup)

            sentinel_page = data_pages + 1
            sentinel_soup = runner.soup(
                gunpo_experience_list_url(sentinel_page),
                referer=GUNPO_EXPERIENCE_URL,
            )
            list_requests += 1
            sentinel_declaration = _page_declaration(sentinel_soup)
            if sentinel_declaration != (source_total, sentinel_page, data_pages):
                raise GunpoExperienceContractError("sentinel declaration changed")
            sentinel_rows = _list_rows(sentinel_soup)
            if sentinel_rows:
                raise GunpoExperienceContractError("post-last page is not exactly empty")

            first_verify = runner.soup(
                gunpo_experience_list_url(1), referer=GUNPO_EXPERIENCE_URL
            )
            list_requests += 1
            if _page_declaration(first_verify) != (source_total, 1, data_pages):
                raise GunpoExperienceContractError(
                    "first-page declaration changed on recheck"
                )
            stable_first = _fingerprint(_list_rows(first_verify)) == _fingerprint(
                page_rows[1]
            )
            if not stable_first:
                raise GunpoExperienceContractError(
                    "first-page identities changed during snapshot"
                )

            if data_pages > 1:
                last_verify = runner.soup(
                    gunpo_experience_list_url(data_pages),
                    referer=GUNPO_EXPERIENCE_URL,
                )
                list_requests += 1
                if _page_declaration(last_verify) != (
                    source_total,
                    data_pages,
                    data_pages,
                ):
                    raise GunpoExperienceContractError(
                        "last-page declaration changed on recheck"
                    )
                stable_last = _fingerprint(_list_rows(last_verify)) == _fingerprint(
                    page_rows[data_pages]
                )
            else:
                stable_last = stable_first
            if not stable_last:
                raise GunpoExperienceContractError(
                    "last-page identities changed during snapshot"
                )

            sentinel_verify = runner.soup(
                gunpo_experience_list_url(sentinel_page),
                referer=GUNPO_EXPERIENCE_URL,
            )
            list_requests += 1
            stable_sentinel = (
                _page_declaration(sentinel_verify) == sentinel_declaration
                and not _list_rows(sentinel_verify)
            )
            if not stable_sentinel:
                raise GunpoExperienceContractError(
                    "empty post-last sentinel changed during snapshot"
                )

            all_rows = [
                row for page in range(1, data_pages + 1) for row in page_rows[page]
            ]
            expected_counts = {
                page: min(
                    GUNPO_EXPERIENCE_PAGE_SIZE,
                    max(
                        0,
                        source_total
                        - (page - 1) * GUNPO_EXPERIENCE_PAGE_SIZE,
                    ),
                )
                for page in range(1, data_pages + 1)
            }
            for page, expected in expected_counts.items():
                if len(page_rows[page]) != expected:
                    raise GunpoExperienceContractError(
                        f"page {page} expected {expected} rows, "
                        f"got {len(page_rows[page])}"
                    )
            if len(all_rows) != source_total:
                raise GunpoExperienceContractError(
                    f"declared total {source_total} != parsed rows {len(all_rows)}"
                )
            identities = [row["provider_course_id"] for row in all_rows]
            if len(identities) != len(set(identities)):
                raise GunpoExperienceContractError("duplicate source identities")

            current_source_rows = [
                row
                for row in all_rows
                if date.fromisoformat(row["end_date"]) >= cutoff
            ]
            source_current_count = len(current_source_rows)
            if source_current_count > allowed_details:
                source_cap_reached = True
                raise GunpoExperienceContractError(
                    f"detail_limit cap allows {allowed_details} of "
                    f"{source_current_count} required current/future details"
                )

            experience_rows: list[dict[str, Any]] = []
            for row in current_source_rows:
                detail_attempts += 1
                detail_soup = runner.soup(
                    row["raw_url"], referer=GUNPO_EXPERIENCE_URL
                )
                is_experience, reason = _enrich_and_classify(detail_soup, row)
                detail_pages += 1
                if reason == "ambiguous":
                    raise GunpoExperienceContractError(
                        "unclassified current/future mixed-ledger row"
                    )
                if is_experience:
                    experience_rows.append(row)
                else:
                    exclusion_counts[reason] += 1

            result = list((dedupe_rows or _dedupe_default)(experience_rows))
            if [row["provider_course_id"] for row in result] != [
                row["provider_course_id"] for row in experience_rows
            ]:
                raise GunpoExperienceContractError(
                    "dedupe changed the complete ordered experience partition"
                )
            privacy_errors = [
                error for row in result for error in _privacy_errors(row)
            ]
            if privacy_errors:
                raise GunpoExperienceContractError(
                    "; ".join(dict.fromkeys(privacy_errors))
                )

            identity_hash = hashlib.sha256(
                "|".join(identities).encode("utf-8")
            ).hexdigest()
            meta = {
                "pages": data_pages + 1,
                "list_requests": list_requests,
                "physical_requests": runner.requests,
                "sessions_created": runner.sessions_created,
                "required_list_requests": required_list_requests,
                "source_total": source_total,
                "source_rows": len(all_rows),
                "source_current_count": source_current_count,
                "data_pages": data_pages,
                "page_size": GUNPO_EXPERIENCE_PAGE_SIZE,
                "page_counts": {
                    page: len(rows) for page, rows in page_rows.items()
                },
                "source_identity_hash": identity_hash,
                "sentinel_page": sentinel_page,
                "sentinel_rows": 0,
                "stable_first_page": stable_first,
                "stable_last_page": stable_last,
                "stable_sentinel": stable_sentinel,
                "expired_count": len(all_rows) - source_current_count,
                "classification_excluded_current_count": sum(
                    exclusion_counts.values()
                ),
                "classification_exclusion_counts": dict(exclusion_counts),
                "unknown_current_count": 0,
                "current_count": len(experience_rows),
                "returned_count": len(result),
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "application_control_count": sum(
                    bool(row["raw_fields"].get("application_control_present"))
                    for row in result
                ),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "pagination_detected": data_pages > 1,
                "pagination_complete": True,
                "sentinel_verified": True,
                "details_complete": detail_pages == source_current_count,
                "classification_complete": True,
                "snapshot_complete": True,
                "source_cap_reached": False,
                "no_current_data": not result,
                "no_current_reason": (
                    "complete mixed ledger has no explicit current/future experience"
                    if not result
                    else ""
                ),
                "application_endpoints_called": 0,
                "attachment_endpoints_called": 0,
                "pii_endpoints_called": 0,
                "tls_verification_disabled": False,
                "pii_payload_persisted": False,
                "configured_collection_error": "",
                "ownership_scope": GUNPO_EXPERIENCE_OWNERSHIP_SCOPE,
                "municipality_code": GUNPO_EXPERIENCE_MUNICIPALITY_CODE,
                "covered_municipalities": [
                    {
                        "code": GUNPO_EXPERIENCE_MUNICIPALITY_CODE,
                        "sido": "경기도",
                        "sigungu": "군포시",
                        "full_name": GUNPO_EXPERIENCE_MUNICIPALITY_NAME,
                    }
                ],
            }
            return result, GUNPO_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta = _failure(f"{type(exc).__name__}: {_redact_error(exc)}")
        meta.update(
            {
                "pages": len(page_rows),
                "list_requests": list_requests,
                "physical_requests": getattr(runner, "requests", 0),
                "source_total": source_total,
                "source_rows": sum(len(rows) for rows in page_rows.values()),
                "source_current_count": source_current_count,
                "data_pages": data_pages,
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "stable_first_page": stable_first,
                "stable_last_page": stable_last,
                "stable_sentinel": stable_sentinel,
                "classification_exclusion_counts": dict(exclusion_counts),
                "source_cap_reached": source_cap_reached,
            }
        )
        return [], GUNPO_EXPERIENCE_PARSER, meta


collect = collect_gunpo_experience_courses


__all__ = [
    "GUNPO_EXPERIENCE_APPLICATION_ENDPOINT",
    "GUNPO_EXPERIENCE_BRANCH",
    "GUNPO_EXPERIENCE_CANDIDATE_ID",
    "GUNPO_EXPERIENCE_DETAIL_ENDPOINT",
    "GUNPO_EXPERIENCE_LIST_ENDPOINT",
    "GUNPO_EXPERIENCE_LIVE_BASELINE",
    "GUNPO_EXPERIENCE_MAX_DETAILS",
    "GUNPO_EXPERIENCE_MAX_PAGES",
    "GUNPO_EXPERIENCE_MUNICIPALITY_CODE",
    "GUNPO_EXPERIENCE_MUNICIPALITY_NAME",
    "GUNPO_EXPERIENCE_OWNERSHIP_SCOPE",
    "GUNPO_EXPERIENCE_PARSER",
    "GUNPO_EXPERIENCE_PROVIDER",
    "GUNPO_EXPERIENCE_URL",
    "GunpoExperienceContractError",
    "collect",
    "collect_gunpo_experience_courses",
    "gunpo_experience_detail_url",
    "gunpo_experience_list_url",
    "is_gunpo_experience_target",
    "is_target",
]
