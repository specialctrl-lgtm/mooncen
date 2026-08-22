"""Fail-closed collector for Gunsan's Geumgang Future Experience Center.

The official ``자연친구, 건강학교`` page is both the public programme detail
and the complete date/class ledger.  All 2026 slots are embedded in one GET
response; there is no pagination or separate public detail route.  A second
GET of that exact page must have the same complete row fingerprint before an
atomic snapshot is returned.

Application and receipt-check controls are inspected only as inert links.
Their routes, every sibling page, login/member/applicant/PII routes, POST,
attachments and downloads are outside the network allowlist.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import html
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import requests


GUNSAN_FUTURE_EXPERIENCE_PROVIDER = "MUNI_GREEN_GUNSAN_GO_KR_3031CB82"
GUNSAN_FUTURE_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_BCD651618686"
GUNSAN_FUTURE_EXPERIENCE_URL = (
    "https://green.gunsan.go.kr/contents.htm?code=3_1"
)
GUNSAN_FUTURE_EXPERIENCE_HOST = "green.gunsan.go.kr"
GUNSAN_FUTURE_EXPERIENCE_PATH = "/contents.htm"
GUNSAN_FUTURE_EXPERIENCE_CODE = "3_1"
GUNSAN_FUTURE_EXPERIENCE_APPLICATION_CODE = "3_1_1"
GUNSAN_FUTURE_EXPERIENCE_RECEIPT_CODE = "3_1_3"
GUNSAN_FUTURE_EXPERIENCE_BRANCH = "금강미래체험관"
GUNSAN_FUTURE_EXPERIENCE_ADDRESS = (
    "전북특별자치도 군산시 성산면 철새로 120"
)
GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_CODE = "5213000000"
GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_NAME = "전북특별자치도 군산시"
GUNSAN_FUTURE_EXPERIENCE_MAX_BYTES = 500_000
GUNSAN_FUTURE_EXPERIENCE_PARSER = (
    "gunsan_geumgang_future_nature_health_single_page_ledger+"
    "continuous_232_identity+stable_double_fetch+current_date_filter+"
    "identity_bound_application_controls_observed_not_called+"
    "audited_367_sibling_exclusions+locked_experience+canonical_get_only+"
    "no_application_receipt_login_member_pii_attachment_download_or_post"
)
GUNSAN_FUTURE_EXPERIENCE_OWNERSHIP_SCOPE = (
    "gunsan_geumgang_future_experience_center_nature_health_2026_ledger"
)

GUNSAN_FUTURE_EXPERIENCE_LIVE_BASELINE: Mapping[str, Any] = {
    "checked_at": "2026-08-05",
    "source_total": 232,
    "source_first_date": "2026-04-01",
    "source_last_date": "2026-12-11",
    "current_count": 116,
    "expired_count": 116,
    "open_current_count": 13,
    "closed_current_count": 103,
    "application_controls_total": 23,
    "sibling_excluded_count": 367,
}

# Audited sibling ledgers are deliberately not fetched by this provider.  The
# first five are undated capacity placeholders and the final one is formal
# teacher training rather than the locked experience category.
GUNSAN_FUTURE_EXPERIENCE_SIBLING_EXCLUSIONS: Mapping[str, Mapping[str, Any]] = {
    "3_2": {"count": 90, "reason": "undated_class_placeholders"},
    "3_4": {"count": 170, "reason": "undated_session_placeholders"},
    "3_5": {"count": 5, "reason": "undated_team_placeholders"},
    "3_6": {"count": 50, "reason": "undated_class_placeholders"},
    "3_7": {"count": 50, "reason": "undated_class_placeholders"},
    "3_8": {"count": 2, "reason": "formal_teacher_training"},
}
GUNSAN_FUTURE_EXPERIENCE_STATIC_SIBLING_CODE = "3_3"

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_SLOT_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})\s*\[(\d+)반\]")
_CAPACITY_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_STATUS_MAP = {"접수중": "OPEN", "접수마감": "CLOSED"}
_SIBLING_LABELS = {
    "3_2": "초·중등 미래교실",
    "3_3": "상시프로그램",
    "3_4": "지역사회연계",
    "3_5": "환경동아리",
    "3_6": "기후탐험대",
    "3_7": "생태배움터",
    "3_8": "2026 교원연수",
}
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "applicant",
        "member",
        "attachment",
        "attachments",
        "download",
        "raw_html",
        "description",
        "content",
    }
)


class GunsanFutureExperienceContractError(ValueError):
    """Raised when the audited public ledger contract changes."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


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
        raise GunsanFutureExperienceContractError(
            f"{name} must be a positive integer"
        ) from exc
    if result < 1:
        raise GunsanFutureExperienceContractError(
            f"{name} must be a positive integer"
        )
    return result


def _exact_canonical_url(value: str) -> bool:
    parsed = urlparse(_clean(value))
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == GUNSAN_FUTURE_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.path == GUNSAN_FUTURE_EXPERIENCE_PATH
        and parse_qs(parsed.query, keep_blank_values=True)
        == {"code": [GUNSAN_FUTURE_EXPERIENCE_CODE]}
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_gunsan_future_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")).upper()
        == GUNSAN_FUTURE_EXPERIENCE_PROVIDER
        and _exact_canonical_url(_clean(_target_value(target, "url")))
    )


is_target = is_gunsan_future_experience_target


def _assert_safe_public_get(method: str, url: str) -> None:
    if method.upper() != "GET" or not _exact_canonical_url(url):
        raise GunsanFutureExperienceContractError(
            "application/receipt/login/member/applicant/PII/attachment/"
            "download/POST endpoint refused"
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
        self.requests = 0
        self.sessions_created = 0

    def __enter__(self) -> "_Runner":
        self.session = self.session_factory()
        self.sessions_created = 1
        return self

    def __exit__(self, *_: Any) -> None:
        close = getattr(self.session, "close", None)
        if callable(close):
            close()

    def soup(self, url: str) -> BeautifulSoup:
        _assert_safe_public_get("GET", url)
        self.requests += 1
        response = self.session.get(
            url,
            timeout=self.timeout,
            allow_redirects=False,
            headers={"Referer": GUNSAN_FUTURE_EXPERIENCE_URL},
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise GunsanFutureExperienceContractError(
                f"unexpected HTTP status {status}"
            )
        if tuple(getattr(response, "history", ()) or ()):
            raise GunsanFutureExperienceContractError("redirect history is forbidden")
        headers = getattr(response, "headers", {}) or {}
        if any(
            str(key).lower() == "location" and value
            for key, value in headers.items()
        ):
            raise GunsanFutureExperienceContractError(
                "redirect location is forbidden"
            )
        final_url = _clean(getattr(response, "url", ""))
        if final_url and not _exact_canonical_url(final_url):
            raise GunsanFutureExperienceContractError(
                "official response escaped canonical URL"
            )
        content_type = _clean(
            next(
                (
                    value
                    for key, value in headers.items()
                    if str(key).lower() == "content-type"
                ),
                "text/html",
            )
        ).lower()
        if "html" not in content_type:
            raise GunsanFutureExperienceContractError(
                "official response is not HTML"
            )
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", response)).encode("utf-8")
        body = bytes(body)
        if not body or len(body) > GUNSAN_FUTURE_EXPERIENCE_MAX_BYTES:
            raise GunsanFutureExperienceContractError(
                "empty or oversized official response"
            )
        soup = BeautifulSoup(body, "html.parser")
        title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title != "금강미래체험관 > 체험프로그램 > 유·초등 프로그램":
            raise GunsanFutureExperienceContractError(
                "official page title changed"
            )
        return soup


def _parse_exact_code_link(href: str) -> str:
    parsed = urlparse(urljoin(GUNSAN_FUTURE_EXPERIENCE_URL, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not (
        parsed.scheme == "https"
        and parsed.hostname == GUNSAN_FUTURE_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.path == GUNSAN_FUTURE_EXPERIENCE_PATH
        and set(query) == {"code"}
        and len(query["code"]) == 1
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        raise GunsanFutureExperienceContractError("unsafe sibling navigation link")
    return query["code"][0]


def _assert_public_program_contract(soup: BeautifulSoup) -> None:
    text = _clean(soup.get_text(" ", strip=True))
    required = (
        "유·초등 프로그램",
        "자연친구, 건강학교",
        "유치(만 3세이상), 초등",
        "금강미래체험관",
        "홈페이지 접수",
        "기후·건강 연계 체험 중심 환경교육 프로그램",
    )
    if any(marker not in text for marker in required):
        raise GunsanFutureExperienceContractError(
            "public programme detail contract changed"
        )

    found: dict[str, str] = {}
    for anchor in soup.select("a[href]"):
        label = _clean(anchor.get_text(" ", strip=True))
        if label not in _SIBLING_LABELS.values():
            continue
        code = _parse_exact_code_link(_clean(anchor.get("href")))
        if code in found and found[code] != label:
            raise GunsanFutureExperienceContractError(
                "ambiguous sibling programme navigation"
            )
        found[code] = label
    if found != {code: label for code, label in _SIBLING_LABELS.items()}:
        raise GunsanFutureExperienceContractError(
            "audited sibling programme registry changed"
        )

    receipt_links = []
    for anchor in soup.select("a[href]"):
        href = _clean(anchor.get("href"))
        if "code=3_1_3" in href:
            receipt_links.append(_parse_exact_code_link(href))
    if receipt_links != [GUNSAN_FUTURE_EXPERIENCE_RECEIPT_CODE]:
        raise GunsanFutureExperienceContractError(
            "receipt-check control contract changed"
        )


def _parse_application_control(
    href: str,
    *,
    sequence: int,
    service_date: date,
    class_number: int,
) -> None:
    parsed = urlparse(urljoin(GUNSAN_FUTURE_EXPERIENCE_URL, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected = {
        "code": [GUNSAN_FUTURE_EXPERIENCE_APPLICATION_CODE],
        "oidx": ["1"],
        "pidx": [str(sequence)],
        "sdate": [service_date.isoformat()],
        "stime": [f"{class_number}반"],
    }
    if not (
        parsed.scheme == "https"
        and parsed.hostname == GUNSAN_FUTURE_EXPERIENCE_HOST
        and parsed.port is None
        and parsed.path == GUNSAN_FUTURE_EXPERIENCE_PATH
        and query == expected
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        raise GunsanFutureExperienceContractError(
            "application control is not bound to its public slot identity"
        )


def _slot_row(
    *,
    sequence: int,
    service_date: date,
    class_number: int,
    source_status: str,
    capacity_current: int,
    capacity_total: int,
    application_control_present: bool,
) -> dict[str, Any]:
    status = _STATUS_MAP[source_status]
    identity = f"nature-health:{service_date.isoformat()}:{class_number}"
    title = (
        "자연친구, 건강학교 "
        f"{service_date.isoformat()} {class_number}반"
    )
    return {
        "provider": GUNSAN_FUTURE_EXPERIENCE_PROVIDER,
        "provider_course_id": (
            f"{GUNSAN_FUTURE_EXPERIENCE_PROVIDER}:{identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "source_course_id": identity,
        "title": title,
        "branch": GUNSAN_FUTURE_EXPERIENCE_BRANCH,
        "preserve_branch": True,
        "raw_url": GUNSAN_FUTURE_EXPERIENCE_URL,
        "source_url": GUNSAN_FUTURE_EXPERIENCE_URL,
        # Application controls are verified but deliberately never persisted.
        "application_url": (
            GUNSAN_FUTURE_EXPERIENCE_URL if status == "OPEN" else ""
        ),
        "application_type": (
            "ONLINE_RESERVATION" if status == "OPEN" else "INFO_ONLY"
        ),
        "status": status,
        "course_status": status,
        "source_status": source_status,
        "reservation_available": status == "OPEN",
        "start_date": service_date.isoformat(),
        "end_date": service_date.isoformat(),
        "period": service_date.isoformat(),
        "schedule": "10:30 ~ 12:00",
        "target": "유치(만 3세 이상), 초등 단체",
        "target_audience": "유치(만 3세 이상), 초등 단체",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_remaining": max(capacity_total - capacity_current, 0),
        "location": GUNSAN_FUTURE_EXPERIENCE_BRANCH,
        "address": GUNSAN_FUTURE_EXPERIENCE_ADDRESS,
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "category": "체험·견학",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "체험",
        "service_group_policy": "locked",
        "program_type": "체험",
        "program_type_source": "official_experience_program_ledger",
        "classification_locked": True,
        "collection_type": GUNSAN_FUTURE_EXPERIENCE_PARSER,
        "municipality_code": GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_name": GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_NAME,
        "municipality_full_name": GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_NAME,
        "region": GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_NAME,
        "sido": "전북특별자치도",
        "sigungu": "군산시",
        "raw_fields": {
            "parser": GUNSAN_FUTURE_EXPERIENCE_PARSER,
            "official_ledger": "자연친구, 건강학교",
            "sequence": sequence,
            "service_date": service_date.isoformat(),
            "class_number": class_number,
            "source_status": source_status,
            "application_control_present": application_control_present,
            "application_control_omitted": True,
            "receipt_control_not_called": True,
            "contact_omitted": True,
        },
    }


def _parse_slots(soup: BeautifulSoup) -> list[dict[str, Any]]:
    _assert_public_program_contract(soup)
    tables = soup.select("table")
    if len(tables) != 1:
        raise GunsanFutureExperienceContractError(
            "programme ledger table count changed"
        )
    table = tables[0]
    header = [_clean(cell.get_text(" ", strip=True)) for cell in table.select("th")]
    if header != ["회차", "날짜", "교육신청", ""]:
        raise GunsanFutureExperienceContractError(
            "programme ledger header changed"
        )

    rows: list[dict[str, Any]] = []
    for table_row in table.select("tr"):
        cells = table_row.find_all("td", recursive=False)
        if not cells:
            continue
        if len(cells) != 4:
            raise GunsanFutureExperienceContractError(
                "programme ledger row schema changed"
            )
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        if not re.fullmatch(r"[1-9]\d*", values[0]):
            raise GunsanFutureExperienceContractError("invalid slot sequence")
        match = _SLOT_RE.fullmatch(values[1])
        capacity = _CAPACITY_RE.fullmatch(values[2])
        if match is None or capacity is None:
            raise GunsanFutureExperienceContractError(
                "slot date/class/capacity contract changed"
            )
        sequence = int(values[0])
        service_date = date.fromisoformat(match.group(1))
        class_number = int(match.group(2))
        capacity_current = int(capacity.group(1))
        capacity_total = int(capacity.group(2))
        if class_number not in {1, 2, 3, 4}:
            raise GunsanFutureExperienceContractError("unexpected class number")
        if capacity_total != 1 or capacity_current not in {0, 1}:
            raise GunsanFutureExperienceContractError(
                "slot capacity contract changed"
            )
        source_status = values[3]
        if source_status not in _STATUS_MAP:
            raise GunsanFutureExperienceContractError(
                f"unknown slot status {source_status!r}"
            )
        controls = cells[3].select("a[href]")
        if source_status == "접수중":
            if len(controls) != 1 or capacity_current != 0:
                raise GunsanFutureExperienceContractError(
                    "open slot control/capacity contract changed"
                )
            _parse_application_control(
                _clean(controls[0].get("href")),
                sequence=sequence,
                service_date=service_date,
                class_number=class_number,
            )
        elif controls or capacity_current != 1:
            raise GunsanFutureExperienceContractError(
                "closed slot unexpectedly exposes an application control"
            )
        rows.append(
            _slot_row(
                sequence=sequence,
                service_date=service_date,
                class_number=class_number,
                source_status=source_status,
                capacity_current=capacity_current,
                capacity_total=capacity_total,
                application_control_present=bool(controls),
            )
        )

    if not rows:
        raise GunsanFutureExperienceContractError("programme ledger is empty")
    sequences = [int(row["raw_fields"]["sequence"]) for row in rows]
    if sequences != list(range(1, len(rows) + 1)):
        raise GunsanFutureExperienceContractError(
            "slot identities are not exactly continuous"
        )
    identities = [row["provider_course_id"] for row in rows]
    if len(identities) != len(set(identities)):
        raise GunsanFutureExperienceContractError("duplicate slot identity")
    date_classes = [
        (
            row["raw_fields"]["service_date"],
            int(row["raw_fields"]["class_number"]),
        )
        for row in rows
    ]
    if len(date_classes) != len(set(date_classes)):
        raise GunsanFutureExperienceContractError("duplicate date/class identity")
    return rows


def _fingerprint(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        "|".join(
            (
                _clean(row.get("provider_course_id")),
                _clean(row.get("status")),
                _clean(row.get("source_status")),
                str(row.get("capacity_current", "")),
                str(row.get("capacity_total", "")),
            )
        )
        for row in rows
    ]
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_OUTPUT_KEYS:
        errors.append("forbidden PII/free-text key")
    safe_url_keys = {"source_url", "raw_url", "application_url"}
    payload = repr(
        {key: value for key, value in row.items() if key not in safe_url_keys}
    )
    if (
        _PHONE_RE.search(payload)
        or _EMAIL_RE.search(payload)
        or _RESIDENT_ID_RE.search(payload)
    ):
        errors.append("PII-like value escaped output allowlist")
    for key in safe_url_keys:
        value = _clean(row.get(key))
        if value and not _exact_canonical_url(value):
            errors.append("non-canonical URL escaped output allowlist")
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
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "data_pages": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "sentinel_required": False,
        "sentinel_verified": False,
        "stable_double_fetch": False,
        "source_identity_continuous": False,
        "classification_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "application_endpoints_called": 0,
        "receipt_endpoints_called": 0,
        "login_member_pii_endpoints_called": 0,
        "attachment_download_endpoints_called": 0,
        "post_requests": 0,
        "sibling_pages_requested": 0,
        "sibling_excluded_count": sum(
            int(value["count"])
            for value in GUNSAN_FUTURE_EXPERIENCE_SIBLING_EXCLUSIONS.values()
        ),
        "tls_verification_disabled": False,
        "pii_payload_persisted": False,
        "configured_collection_error": message,
        "ownership_scope": GUNSAN_FUTURE_EXPERIENCE_OWNERSHIP_SCOPE,
        "municipality_code": GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_CODE,
    }


def _assert_audited_baseline(
    rows: list[dict[str, Any]], current_rows: list[dict[str, Any]], cutoff: date
) -> None:
    baseline = GUNSAN_FUTURE_EXPERIENCE_LIVE_BASELINE
    if len(rows) != int(baseline["source_total"]):
        raise GunsanFutureExperienceContractError(
            "audited 2026 source row count changed"
        )
    if rows[0]["start_date"] != baseline["source_first_date"]:
        raise GunsanFutureExperienceContractError(
            "audited source first date changed"
        )
    if rows[-1]["start_date"] != baseline["source_last_date"]:
        raise GunsanFutureExperienceContractError(
            "audited source last date changed"
        )
    if cutoff.isoformat() != baseline["checked_at"]:
        return
    status_counts = Counter(row["status"] for row in current_rows)
    expected = (
        int(baseline["current_count"]),
        int(baseline["expired_count"]),
        int(baseline["open_current_count"]),
        int(baseline["closed_current_count"]),
    )
    actual = (
        len(current_rows),
        len(rows) - len(current_rows),
        status_counts["OPEN"],
        status_counts["CLOSED"],
    )
    if actual != expected:
        raise GunsanFutureExperienceContractError(
            "2026-08-05 audited current/status baseline changed"
        )


def collect_gunsan_future_experience(
    target: Any,
    timeout: int = 20,
    max_pages: int = 4,
    detail_limit: int = 300,
    *,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return the complete current/future public experience-slot snapshot."""

    if not is_gunsan_future_experience_target(target):
        return [], GUNSAN_FUTURE_EXPERIENCE_PARSER, _failure(
            "target does not match the audited Gunsan experience owner"
        )
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            return [], GUNSAN_FUTURE_EXPERIENCE_PARSER, _failure(
                "managed session_factory injection is required"
            )
        session_factory = _default_session_factory

    first_rows: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    runner: Optional[_Runner] = None
    source_cap_reached = False
    stable_double_fetch = False
    try:
        allowed_pages = _positive(max_pages, "max_pages")
        allowed_details = _positive(detail_limit, "detail_limit")
        request_timeout = _positive(timeout, "timeout")
        if allowed_pages < 2:
            source_cap_reached = True
            raise GunsanFutureExperienceContractError(
                "max_pages must allow two stable canonical GETs"
            )
        cutoff = _today(today)
        with _Runner(session_factory, request_timeout) as runner:
            first_rows = _parse_slots(runner.soup(GUNSAN_FUTURE_EXPERIENCE_URL))
            verify_rows = _parse_slots(runner.soup(GUNSAN_FUTURE_EXPERIENCE_URL))
            first_fingerprint = _fingerprint(first_rows)
            verify_fingerprint = _fingerprint(verify_rows)
            stable_double_fetch = first_fingerprint == verify_fingerprint
            if not stable_double_fetch:
                raise GunsanFutureExperienceContractError(
                    "complete single-page ledger changed on recheck"
                )

            current_rows = [
                row
                for row in first_rows
                if date.fromisoformat(row["end_date"]) >= cutoff
            ]
            if len(current_rows) > allowed_details:
                source_cap_reached = True
                raise GunsanFutureExperienceContractError(
                    f"detail_limit cap allows {allowed_details} of "
                    f"{len(current_rows)} current/future rows"
                )
            _assert_audited_baseline(first_rows, current_rows, cutoff)

            result = list((dedupe_rows or _dedupe_default)(current_rows))
            if [row["provider_course_id"] for row in result] != [
                row["provider_course_id"] for row in current_rows
            ]:
                raise GunsanFutureExperienceContractError(
                    "dedupe changed the complete ordered experience ledger"
                )
            privacy_errors = [
                error for row in result for error in _privacy_errors(row)
            ]
            if privacy_errors:
                raise GunsanFutureExperienceContractError(
                    "; ".join(dict.fromkeys(privacy_errors))
                )

            current_status_counts = Counter(row["status"] for row in result)
            source_status_counts = Counter(row["status"] for row in first_rows)
            application_controls_total = sum(
                bool(row["raw_fields"]["application_control_present"])
                for row in first_rows
            )
            sibling_breakdown = {
                code: {
                    "count": int(value["count"]),
                    "reason": _clean(value["reason"]),
                }
                for code, value in GUNSAN_FUTURE_EXPERIENCE_SIBLING_EXCLUSIONS.items()
            }
            sibling_excluded_count = sum(
                value["count"] for value in sibling_breakdown.values()
            )
            if sibling_excluded_count != int(
                GUNSAN_FUTURE_EXPERIENCE_LIVE_BASELINE[
                    "sibling_excluded_count"
                ]
            ):
                raise GunsanFutureExperienceContractError(
                    "audited sibling exclusion registry changed"
                )
            meta = {
                "pages": 1,
                "list_requests": 2,
                "physical_requests": runner.requests,
                "sessions_created": runner.sessions_created,
                "source_total": len(first_rows),
                "source_rows": len(first_rows),
                "current_count": len(current_rows),
                "returned_count": len(result),
                "expired_count": len(first_rows) - len(current_rows),
                "data_pages": 1,
                "page_counts": {1: len(first_rows)},
                "source_identity_hash": first_fingerprint,
                "unique_identity_count": len(
                    {row["provider_course_id"] for row in first_rows}
                ),
                "source_identity_continuous": True,
                "source_first_date": first_rows[0]["start_date"],
                "source_last_date": first_rows[-1]["start_date"],
                "status_counts": dict(current_status_counts),
                "source_status_counts": dict(source_status_counts),
                "application_controls_total": application_controls_total,
                "application_controls_current": sum(
                    bool(row["raw_fields"]["application_control_present"])
                    for row in result
                ),
                "pagination_detected": False,
                "pagination_complete": True,
                "sentinel_required": False,
                "sentinel_not_applicable": True,
                "sentinel_strategy": (
                    "single_inline_annual_ledger_continuous_sequence_and_"
                    "stable_double_fetch"
                ),
                "sentinel_verified": True,
                "stable_double_fetch": stable_double_fetch,
                "details_complete": True,
                "classification_complete": True,
                "snapshot_complete": True,
                "source_cap_reached": False,
                "no_current_data": not result,
                "no_current_reason": (
                    "complete annual ledger has no current/future slots"
                    if not result
                    else ""
                ),
                "sibling_excluded_count": sibling_excluded_count,
                "sibling_exclusion_counts": sibling_breakdown,
                "static_sibling_excluded_count": 1,
                "static_sibling_code": (
                    GUNSAN_FUTURE_EXPERIENCE_STATIC_SIBLING_CODE
                ),
                "sibling_exclusion_contract_locked": True,
                "sibling_pages_requested": 0,
                "application_endpoints_called": 0,
                "receipt_endpoints_called": 0,
                "login_member_pii_endpoints_called": 0,
                "attachment_download_endpoints_called": 0,
                "post_requests": 0,
                "tls_verification_disabled": False,
                "pii_payload_persisted": False,
                "configured_collection_error": "",
                "ownership_scope": (
                    GUNSAN_FUTURE_EXPERIENCE_OWNERSHIP_SCOPE
                ),
                "municipality_code": (
                    GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_CODE
                ),
                "covered_municipalities": [
                    {
                        "code": GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_CODE,
                        "sido": "전북특별자치도",
                        "sigungu": "군산시",
                        "full_name": (
                            GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_NAME
                        ),
                    }
                ],
            }
            return result, GUNSAN_FUTURE_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta = _failure(
            f"{type(exc).__name__}: {_redact_error(exc)}"
        )
        meta.update(
            {
                "pages": 1 if first_rows else 0,
                "list_requests": getattr(runner, "requests", 0),
                "physical_requests": getattr(runner, "requests", 0),
                "source_total": len(first_rows),
                "source_rows": len(first_rows),
                "current_count": len(current_rows),
                "expired_count": len(first_rows) - len(current_rows),
                "data_pages": 1 if first_rows else 0,
                "stable_double_fetch": stable_double_fetch,
                "source_identity_continuous": bool(first_rows),
                "source_cap_reached": source_cap_reached,
            }
        )
        return [], GUNSAN_FUTURE_EXPERIENCE_PARSER, meta


collect = collect_gunsan_future_experience


__all__ = [
    "GUNSAN_FUTURE_EXPERIENCE_ADDRESS",
    "GUNSAN_FUTURE_EXPERIENCE_APPLICATION_CODE",
    "GUNSAN_FUTURE_EXPERIENCE_BRANCH",
    "GUNSAN_FUTURE_EXPERIENCE_CANDIDATE_ID",
    "GUNSAN_FUTURE_EXPERIENCE_LIVE_BASELINE",
    "GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_CODE",
    "GUNSAN_FUTURE_EXPERIENCE_MUNICIPALITY_NAME",
    "GUNSAN_FUTURE_EXPERIENCE_OWNERSHIP_SCOPE",
    "GUNSAN_FUTURE_EXPERIENCE_PARSER",
    "GUNSAN_FUTURE_EXPERIENCE_PROVIDER",
    "GUNSAN_FUTURE_EXPERIENCE_RECEIPT_CODE",
    "GUNSAN_FUTURE_EXPERIENCE_SIBLING_EXCLUSIONS",
    "GUNSAN_FUTURE_EXPERIENCE_URL",
    "GunsanFutureExperienceContractError",
    "collect",
    "collect_gunsan_future_experience",
    "is_gunsan_future_experience_target",
    "is_target",
]
