"""Fail-closed collector for Boeun Youth Center's official Play.Pass ledger.

The Boeun-gun Youth Center homepage links directly to the Play.Pass programme
catalogue.  The catalogue exposes public programme, detail, and round JSON
routes.  This collector reads only those routes.  It never calls the apply,
application, member, login/auth, question/answer, file, attachment, or download
routes that are present in the client application.

Every listed identity must have a matching public detail and round response.
The complete paginated ledger, an exact empty post-last sentinel, and stable
first/last/sentinel rechecks are required before publication.  The audited
course families are locked: career/practical pop-up programmes are experience,
regular semester courses are education, and the festival plus an off-site Jeju
tour are explicitly excluded.  Unknown future classifications fail closed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests


BOEUN_YOUTH_PROVIDER = "MUNI_BOEUN_PLAYPASS_CO_KR_69657ED8"
BOEUN_YOUTH_CANDIDATE_ID = "MUNI_IR_5843C7492BB3"
BOEUN_YOUTH_HOST = "boeun.playpass.co.kr"
BOEUN_YOUTH_URL = "https://boeun.playpass.co.kr/program/list"
BOEUN_YOUTH_LIST_PATH = "/nodeapi/web/program"
BOEUN_YOUTH_DETAIL_PREFIX = "/nodeapi/web/program/"
BOEUN_YOUTH_ROUND_PATH = "/nodeapi/web/program/round"
BOEUN_YOUTH_PAGE_SIZE = 12
BOEUN_YOUTH_MUNICIPALITY_CODE = "4372000000"
BOEUN_YOUTH_MUNICIPALITY_NAME = "충청북도 보은군"
BOEUN_YOUTH_BRANCH = "보은군청소년센터"
BOEUN_YOUTH_ADDRESS = "충청북도 보은군 보은읍 군청길 114"
BOEUN_YOUTH_BRANCH_CODE = "BOEUN_YOUTH_CENTER"
BOEUN_YOUTH_OWNERSHIP_EVIDENCE_URL = "https://www.boeun.go.kr/youth/index.do"
BOEUN_YOUTH_OWNERSHIP_SCOPE = "boeun_youth_center_playpass_complete_programme_ledger"
BOEUN_YOUTH_MAX_JSON_BYTES = 3_000_000
BOEUN_YOUTH_PARSER = (
    "boeun_youth_center_playpass_complete_mixed_ledger+declared_pagination+"
    "exact_empty_post_last_sentinel+stable_first_last_sentinel+all_public_details+"
    "all_public_rounds+exact_course_family_lock+venue_municipality_guard+"
    "locked_education_experience+safe_json_allowlist+"
    "no_application_login_auth_member_applicant_question_file_attachment_download_or_pii_calls"
)

_EXPERIENCE_CAREER_PREFIX = "[꿈.찾.주] "
_EXPERIENCE_POPUP_PREFIX = "팝업부스 "
_EDUCATION_PREFIX = "[하반기] "
_STATUS_MAP = {
    "applyStatusAccepting": "OPEN",
    "applyStatusWait": "SCHEDULED",
    "applyStatusClosed": "CLOSED",
}
_FORBIDDEN_ROUTE_MARKERS = (
    "/apply",
    "/application",
    "/auth",
    "/login",
    "/member",
    "/applicant",
    "/question",
    "/answer",
    "/file",
    "/attachment",
    "/download",
)
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "adminId",
        "adminName",
        "adminPhone",
        "createdBy",
        "createdByName",
        "updatedBy",
        "updatedByName",
        "teacherList",
        "memberList",
        "applicantList",
        "pageContent",
        "coverFullPath",
        "memo",
    }
)
_PHONE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}|"
    r"01[016789][- .]?\d{3,4}[- .]?\d{4})(?!\d)"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IDENTITY = re.compile(r"[1-9]\d{0,11}")

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, str, Optional[Mapping[str, Any]], int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class BoeunYouthContractError(ValueError):
    """Raised when the audited public-source contract changes."""


@dataclass(frozen=True)
class _ListPage:
    page: int
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


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


def is_boeun_youth_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == BOEUN_YOUTH_PROVIDER
        and _clean(_target_value(target, "url")) == BOEUN_YOUTH_URL
    )


is_target = is_boeun_youth_experience_target


def _session() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MoonCenBot/1.0; +https://mooncen.kr)",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    return value


def _list_payload(page: int) -> dict[str, Any]:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    return {
        "buildingNo": None,
        "categoryNo": None,
        "searchText": None,
        "pagination": {"perPage": BOEUN_YOUTH_PAGE_SIZE, "currentPage": page},
    }


def _round_payload(identity: int) -> dict[str, int]:
    if not isinstance(identity, int) or isinstance(identity, bool) or identity < 1:
        raise ValueError("identity must be a positive integer")
    return {"programNo": identity}


def boeun_youth_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY.fullmatch(value):
        raise ValueError("invalid programme identity")
    return f"https://{BOEUN_YOUTH_HOST}{BOEUN_YOUTH_DETAIL_PREFIX}{value}"


def _api_url(path: str) -> str:
    return f"https://{BOEUN_YOUTH_HOST}{path}"


def _request_kind(method: str, url: str, payload: Optional[Mapping[str, Any]]) -> str:
    parsed = urlparse(_clean(url))
    path = parsed.path
    lowered = path.lower()
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == BOEUN_YOUTH_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    ):
        raise BoeunYouthContractError("non-canonical API URL refused")
    if any(marker in lowered for marker in _FORBIDDEN_ROUTE_MARKERS):
        raise BoeunYouthContractError("unsafe API route refused")
    upper = _clean(method).upper()
    if upper == "POST" and path == BOEUN_YOUTH_LIST_PATH:
        if payload != _list_payload(int((payload or {}).get("pagination", {}).get("currentPage", 0))):
            raise BoeunYouthContractError("non-canonical list payload refused")
        return "list"
    if upper == "POST" and path == BOEUN_YOUTH_ROUND_PATH:
        identity = (payload or {}).get("programNo")
        if payload != _round_payload(identity):
            raise BoeunYouthContractError("non-canonical round payload refused")
        return "round"
    if upper == "GET" and path.startswith(BOEUN_YOUTH_DETAIL_PREFIX):
        identity = path.removeprefix(BOEUN_YOUTH_DETAIL_PREFIX)
        if _IDENTITY.fullmatch(identity):
            return "detail"
    raise BoeunYouthContractError("unapproved API route refused")


def _request(
    session: Any,
    method: str,
    url: str,
    payload: Optional[Mapping[str, Any]],
    timeout: int,
) -> Any:
    _request_kind(method, url, payload)
    if method.upper() == "GET":
        return session.get(url, timeout=timeout, allow_redirects=False)
    return session.post(url, json=payload, timeout=timeout, allow_redirects=False)


def _json_response(response: Any, expected_url: str) -> dict[str, Any]:
    if int(getattr(response, "status_code", 0)) != 200:
        raise BoeunYouthContractError("unexpected HTTP status")
    if getattr(response, "history", ()):
        raise BoeunYouthContractError("redirected API response refused")
    final = urlparse(_clean(getattr(response, "url", expected_url)))
    expected = urlparse(expected_url)
    if (final.scheme, final.hostname, final.path, final.query) != (
        expected.scheme,
        expected.hostname,
        expected.path,
        expected.query,
    ):
        raise BoeunYouthContractError("API response URL changed")
    content = bytes(getattr(response, "content", b""))
    if not content or len(content) > BOEUN_YOUTH_MAX_JSON_BYTES:
        raise BoeunYouthContractError("invalid API response size")
    content_type = _clean(getattr(response, "headers", {}).get("content-type")).lower()
    if "json" not in content_type:
        raise BoeunYouthContractError("non-JSON API response refused")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoeunYouthContractError("invalid JSON API response") from exc
    if not isinstance(value, dict) or value.get("resultCd") != "S":
        raise BoeunYouthContractError("API result contract changed")
    return value


def _safe_call(
    session: Any,
    method: str,
    url: str,
    payload: Optional[Mapping[str, Any]],
    timeout: int,
    fetcher: Fetcher,
) -> dict[str, Any]:
    response = fetcher(session, method, url, payload, timeout)
    return _json_response(response, url)


def _parse_list_page(value: Mapping[str, Any], page: int) -> _ListPage:
    data = value.get("data")
    pagination = value.get("pagination")
    if not isinstance(data, list) or not isinstance(pagination, Mapping):
        raise BoeunYouthContractError("list response shape changed")
    try:
        current = int(pagination["currentPage"])
        per_page = int(pagination["perPage"])
        total = int(pagination["total"])
        last = int(pagination["lastPage"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BoeunYouthContractError("invalid pagination metadata") from exc
    if current != page or per_page != BOEUN_YOUTH_PAGE_SIZE or total < 1 or last < 1:
        raise BoeunYouthContractError("pagination metadata changed")
    expected_last = (total + BOEUN_YOUTH_PAGE_SIZE - 1) // BOEUN_YOUTH_PAGE_SIZE
    if last != expected_last:
        raise BoeunYouthContractError("declared last page changed")
    expected_count = 0 if page > last else min(
        BOEUN_YOUTH_PAGE_SIZE,
        total - ((page - 1) * BOEUN_YOUTH_PAGE_SIZE),
    )
    if len(data) != expected_count:
        raise BoeunYouthContractError("page row count differs from pagination")
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise BoeunYouthContractError("non-object programme row")
        row = dict(item)
        identity = row.get("programNo")
        if not isinstance(identity, int) or isinstance(identity, bool) or identity < 1:
            raise BoeunYouthContractError("invalid programme identity")
        for key in (
            "programTitle",
            "categoryName",
            "applyStatus",
            "openYn",
            "location",
            "applyPeriod",
            "programPeriod",
            "applyWay",
        ):
            if not isinstance(row.get(key), str):
                raise BoeunYouthContractError(f"programme field changed: {key}")
        if not _clean(row["programTitle"]) or row["applyStatus"] not in _STATUS_MAP:
            raise BoeunYouthContractError("programme title/status contract changed")
        rows.append(row)
    return _ListPage(page=page, total=total, last=last, rows=tuple(rows))


def _page_fingerprint(page: _ListPage) -> str:
    values = [
        {
            "programNo": row["programNo"],
            "programTitle": _clean(row["programTitle"]),
            "categoryNo": row.get("categoryNo"),
            "typeNo": row.get("typeNo"),
            "applyStatus": row["applyStatus"],
            "openYn": row["openYn"],
            "location": _clean(row["location"]),
            "applyPeriod": _clean(row["applyPeriod"]),
            "programPeriod": _clean(row["programPeriod"]),
            "applyWay": _clean(row["applyWay"]),
        }
        for row in page.rows
    ]
    raw = json.dumps(
        {"page": page.page, "total": page.total, "last": page.last, "rows": values},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_iso_date(value: Any) -> Optional[date]:
    text = _clean(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise BoeunYouthContractError("invalid round date") from exc


def _flatten_rounds(value: Any, identity: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise BoeunYouthContractError("programme has no public rounds")
    result: list[dict[str, Any]] = []

    def add(item: Any) -> None:
        if not isinstance(item, Mapping) or item.get("programNo") != identity:
            raise BoeunYouthContractError("round identity mismatch")
        row = dict(item)
        if not isinstance(row.get("roundNo"), int) or row.get("roundNo", 0) < 1:
            raise BoeunYouthContractError("invalid round identity")
        if row.get("applyStatus") not in _STATUS_MAP or row.get("openYn") != "Y":
            raise BoeunYouthContractError("round status contract changed")
        if _parse_iso_date(row.get("useDate")) is None:
            raise BoeunYouthContractError("round has no use date")
        result.append(row)
        nested = row.get("nextRoundList", [])
        if nested is None:
            nested = []
        if not isinstance(nested, list):
            raise BoeunYouthContractError("nested round shape changed")
        for child in nested:
            add(child)

    for source in value:
        add(source)
    identities = [row["roundNo"] for row in result]
    if len(identities) != len(set(identities)):
        raise BoeunYouthContractError("duplicate round identity")
    return result


def _classification(row: Mapping[str, Any]) -> tuple[str, str]:
    title = _clean(row.get("programTitle"))
    category_no = row.get("categoryNo")
    type_no = row.get("typeNo")
    location = _clean(row.get("location"))
    if category_no == 54 and type_no == 24 and title.startswith(_EXPERIENCE_CAREER_PREFIX):
        return "experience", ""
    if category_no == 9 and type_no == 26 and title.startswith(_EXPERIENCE_POPUP_PREFIX):
        return "experience", ""
    if category_no in {14, 17} and type_no == 24 and title.startswith(_EDUCATION_PREFIX):
        return "education", ""
    if category_no == 5 and type_no == 25:
        return "excluded", "youth_festival_without_programme_application"
    if category_no == 39 and type_no == 26 and "제주" in location:
        return "excluded", "offsite_jeju_venue_not_boeun_municipality"
    raise BoeunYouthContractError(f"unknown programme classification: {row.get('programNo')}")


def _validate_detail(list_row: Mapping[str, Any], detail: Any) -> dict[str, Any]:
    if not isinstance(detail, Mapping):
        raise BoeunYouthContractError("detail response shape changed")
    value = dict(detail)
    for key in (
        "programNo",
        "programTitle",
        "categoryNo",
        "typeNo",
        "applyStatus",
        "openYn",
        "location",
        "applyPeriod",
        "programPeriod",
        "applyWay",
    ):
        left = _clean(list_row.get(key)) if isinstance(list_row.get(key), str) else list_row.get(key)
        right = _clean(value.get(key)) if isinstance(value.get(key), str) else value.get(key)
        if left != right:
            raise BoeunYouthContractError(f"detail/list mismatch: {key}")
    return value


def _row(
    item: Mapping[str, Any],
    rounds: list[dict[str, Any]],
    family: str,
) -> dict[str, Any]:
    identity = int(item["programNo"])
    dates = [_parse_iso_date(value["useDate"]) for value in rounds]
    if any(value is None for value in dates):
        raise BoeunYouthContractError("round date is missing")
    use_dates = [value for value in dates if value is not None]
    start_date = min(use_dates)
    end_date = max(use_dates)
    apply_starts = [
        value for value in (_parse_iso_date(row.get("applyStartDate")) for row in rounds) if value
    ]
    apply_ends = [
        value for value in (_parse_iso_date(row.get("applyEndDate")) for row in rounds) if value
    ]
    source_status = _clean(item["applyStatus"])
    status = _STATUS_MAP[source_status]
    label = "체험" if family == "experience" else "교육"
    return {
        "provider": BOEUN_YOUTH_PROVIDER,
        "provider_course_id": f"{BOEUN_YOUTH_PROVIDER}:program:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(item["programTitle"]),
        "branch": BOEUN_YOUTH_BRANCH,
        "branch_code": BOEUN_YOUTH_BRANCH_CODE,
        "preserve_branch": True,
        "provider_organizer": BOEUN_YOUTH_BRANCH,
        "venue": BOEUN_YOUTH_BRANCH,
        "venue_name": BOEUN_YOUTH_BRANCH,
        "address": BOEUN_YOUTH_ADDRESS,
        "region_sido": "충청북도",
        "region_sigungu": "보은군",
        "region_full_name": BOEUN_YOUTH_MUNICIPALITY_NAME,
        "municipality_code": BOEUN_YOUTH_MUNICIPALITY_CODE,
        "municipality_full_name": BOEUN_YOUTH_MUNICIPALITY_NAME,
        "category": f"보은군청소년센터 {label}",
        "category_raw": _clean(item["categoryName"]),
        "program_type": label,
        "raw_url": BOEUN_YOUTH_URL,
        "source_url": BOEUN_YOUTH_URL,
        "application_url": "",
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "status": status,
        "fee": _clean(item.get("programFee")),
        "period": f"{start_date.isoformat()} ~ {end_date.isoformat()}",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "apply_period": (
            f"{min(apply_starts).isoformat()} ~ {max(apply_ends).isoformat()}"
            if apply_starts and apply_ends
            else _clean(item.get("applyPeriod"))
        ),
        "apply_start": min(apply_starts).isoformat() if apply_starts else "",
        "apply_end": max(apply_ends).isoformat() if apply_ends else "",
        "capacity": _clean(item.get("quota")),
        "description": _clean(item["programTitle"]),
        "source_group": "municipal_reservation",
        "collection_category": "공공예약",
        "domain_category": "체험·견학" if family == "experience" else "교육·강좌",
        "service_group": "체험" if family == "experience" else "공공강좌",
        "service_group_policy": "locked",
        "service_family": family,
        "operator_type": "지자체/공공기관",
        "collection_type": BOEUN_YOUTH_PARSER,
        "raw_fields": {
            "identity": identity,
            "category_no": item.get("categoryNo"),
            "type_no": item.get("typeNo"),
            "source_status": source_status,
            "source_apply_period": _clean(item.get("applyPeriod")),
            "source_program_period": _clean(item.get("programPeriod")),
            "source_apply_way": _clean(item.get("applyWay")),
            "round_ids": [int(value["roundNo"]) for value in rounds],
            "round_count": len(rounds),
            "classification_locked": True,
            "service_family": family,
            "detail_verified": True,
            "rounds_verified": True,
        },
    }


def _contains_forbidden_output(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in _FORBIDDEN_OUTPUT_KEYS or _contains_forbidden_output(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_output(child) for child in value)
    if isinstance(value, str):
        return bool(_PHONE.search(value) or _EMAIL.search(value))
    return False


def collect_boeun_youth_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 10,
    detail_limit: int = 60,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "municipality_code": BOEUN_YOUTH_MUNICIPALITY_CODE,
        "owner_provider": BOEUN_YOUTH_PROVIDER,
        "canonical_url": BOEUN_YOUTH_URL,
        "ownership_evidence_url": BOEUN_YOUTH_OWNERSHIP_EVIDENCE_URL,
        "parser": BOEUN_YOUTH_PARSER,
        "source_total": 0,
        "source_rows": 0,
        "pages": 0,
        "data_pages": 0,
        "sentinel_pages": 0,
        "stable_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "round_attempts": 0,
        "round_pages": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "education_rows": 0,
        "experience_rows": 0,
        "excluded_count": 0,
        "excluded_reason_counts": {},
        "returned_count": 0,
        "application_urls": 0,
        "application_endpoint_requests": 0,
        "login_endpoint_requests": 0,
        "auth_endpoint_requests": 0,
        "member_endpoint_requests": 0,
        "applicant_endpoint_requests": 0,
        "question_endpoint_requests": 0,
        "file_endpoint_requests": 0,
        "attachment_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "pii_endpoint_requests": 0,
        "duplicate_count": 0,
        "classification_complete": False,
        "pagination_complete": False,
        "details_complete": False,
        "rounds_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "configured_collection_error": "",
        "errors": [],
    }
    if not is_boeun_youth_experience_target(target):
        meta["configured_collection_error"] = "target/provider failed exact contract"
        return [], BOEUN_YOUTH_PARSER, meta
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in (timeout, max_pages, detail_limit)
    ):
        meta["configured_collection_error"] = "invalid collection limits"
        return [], BOEUN_YOUTH_PARSER, meta

    factory = session_factory or _session
    current_fetcher = fetcher or _request
    session = factory()
    cutoff = _today(today)
    try:
        list_url = _api_url(BOEUN_YOUTH_LIST_PATH)

        def get_page(page: int) -> _ListPage:
            meta["pages"] += 1
            value = _safe_call(
                session,
                "POST",
                list_url,
                _list_payload(page),
                timeout,
                current_fetcher,
            )
            return _parse_list_page(value, page)

        first = get_page(1)
        if first.last + 1 > max_pages:
            raise BoeunYouthContractError("max_pages truncates declared ledger and sentinel")
        pages = [first]
        for page_no in range(2, first.last + 1):
            current = get_page(page_no)
            if current.total != first.total or current.last != first.last:
                raise BoeunYouthContractError("pagination declaration changed between pages")
            pages.append(current)
        sentinel = get_page(first.last + 1)
        if sentinel.rows or sentinel.total != first.total or sentinel.last != first.last:
            raise BoeunYouthContractError("post-last sentinel is not exact empty page")
        first_check = get_page(1)
        last_check = get_page(first.last)
        sentinel_check = get_page(first.last + 1)
        if (
            _page_fingerprint(first_check) != _page_fingerprint(first)
            or _page_fingerprint(last_check) != _page_fingerprint(pages[-1])
            or _page_fingerprint(sentinel_check) != _page_fingerprint(sentinel)
        ):
            raise BoeunYouthContractError("first/last/sentinel boundary changed during crawl")
        meta["data_pages"] = first.last
        meta["sentinel_pages"] = 1
        meta["stable_rechecks"] = 3
        meta["pagination_complete"] = True
        source = [row for page in pages for row in page.rows]
        identities = [int(row["programNo"]) for row in source]
        if len(source) != first.total or len(identities) != len(set(identities)):
            raise BoeunYouthContractError("declared total or programme identity set changed")
        if detail_limit < len(source):
            raise BoeunYouthContractError("detail_limit truncates complete programme validation")
        meta["source_total"] = first.total
        meta["source_rows"] = len(source)

        candidates: list[dict[str, Any]] = []
        excluded_reasons: Counter[str] = Counter()
        expired_count = 0
        for item in source:
            identity = int(item["programNo"])
            detail_url = boeun_youth_detail_url(identity)
            meta["detail_attempts"] += 1
            detail_response = _safe_call(
                session,
                "GET",
                detail_url,
                None,
                timeout,
                current_fetcher,
            )
            detail = _validate_detail(item, detail_response.get("data"))
            meta["detail_pages"] += 1
            meta["round_attempts"] += 1
            round_response = _safe_call(
                session,
                "POST",
                _api_url(BOEUN_YOUTH_ROUND_PATH),
                _round_payload(identity),
                timeout,
                current_fetcher,
            )
            rounds = _flatten_rounds(round_response.get("data"), identity)
            meta["round_pages"] += 1
            family, reason = _classification(detail)
            dates = [_parse_iso_date(value["useDate"]) for value in rounds]
            end_date = max(value for value in dates if value is not None)
            if end_date < cutoff:
                expired_count += 1
                continue
            if family == "excluded":
                excluded_reasons[reason] += 1
                continue
            candidates.append(_row(detail, rounds, family))

        meta["current_source_count"] = len(source) - expired_count
        meta["expired_count"] = expired_count
        meta["excluded_count"] = sum(excluded_reasons.values())
        meta["excluded_reason_counts"] = dict(excluded_reasons)
        meta["details_complete"] = meta["detail_pages"] == len(source)
        meta["rounds_complete"] = meta["round_pages"] == len(source)
        meta["classification_complete"] = (
            len(candidates) + meta["excluded_count"] + expired_count == len(source)
        )

        result = dedupe_rows([], candidates) if dedupe_rows else candidates
        result = list(result)
        unique_ids = {_clean(row.get("provider_course_id")) for row in result}
        duplicate_count = len(result) - len(unique_ids)
        if duplicate_count or "" in unique_ids:
            raise BoeunYouthContractError("duplicate or empty provider_course_id")
        if any(not _clean(row.get("provider_course_id")).startswith(f"{BOEUN_YOUTH_PROVIDER}:") for row in result):
            raise BoeunYouthContractError("provider_course_id prefix contract changed")
        if any(bool(row.get("application_url")) != bool(row.get("reservation_available")) for row in result):
            raise BoeunYouthContractError("application URL/availability contract changed")
        if any(_contains_forbidden_output(row) for row in result):
            raise BoeunYouthContractError("forbidden contact or identity data reached output")

        family_counts = Counter(_clean(row.get("service_family")) for row in result)
        meta["education_rows"] = family_counts.get("education", 0)
        meta["experience_rows"] = family_counts.get("experience", 0)
        meta["status_counts"] = dict(Counter(_clean(row.get("status")) for row in result))
        meta["returned_count"] = len(result)
        meta["application_urls"] = sum(bool(row.get("application_url")) for row in result)
        meta["duplicate_count"] = duplicate_count
        meta["snapshot_complete"] = bool(
            meta["pagination_complete"]
            and meta["details_complete"]
            and meta["rounds_complete"]
            and meta["classification_complete"]
            and meta["experience_rows"] > 0
            and len(result) + meta["excluded_count"] + expired_count == len(source)
        )
        meta["full_snapshot_validated"] = meta["snapshot_complete"]
        if not meta["snapshot_complete"]:
            raise BoeunYouthContractError("complete snapshot proof failed")
        return result, BOEUN_YOUTH_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["errors"] = [meta["configured_collection_error"]]
        meta["returned_count"] = 0
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], BOEUN_YOUTH_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_boeun_youth_experience


__all__ = [
    "BOEUN_YOUTH_ADDRESS",
    "BOEUN_YOUTH_CANDIDATE_ID",
    "BOEUN_YOUTH_MUNICIPALITY_CODE",
    "BOEUN_YOUTH_PARSER",
    "BOEUN_YOUTH_PROVIDER",
    "BOEUN_YOUTH_URL",
    "BoeunYouthContractError",
    "boeun_youth_detail_url",
    "collect",
    "collect_boeun_youth_experience",
    "is_boeun_youth_experience_target",
]
