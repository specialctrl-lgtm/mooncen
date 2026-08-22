"""Fail-closed experience collector for Saenggeo Jincheon Forest Lodge.

The official Foresttrip programme ledger publishes the programme identity,
operating period, audience, times, capacity, fee and fixed meeting place in
one public list document.  This collector requests that one document only.
It never follows the JavaScript detail control and never requests reservation,
application, login, auth, member, applicant, PII, attachment, image or download
routes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag
import requests


JINCHEON_FOREST_EXPERIENCE_PROVIDER = "MUNI_WWW_FORESTTRIP_GO_KR_D4C0A15C"
JINCHEON_FOREST_EXPERIENCE_CANDIDATE_ID = "MUNI_IR_710999E57224"
JINCHEON_FOREST_EXPERIENCE_HOST = "www.foresttrip.go.kr"
JINCHEON_FOREST_EXPERIENCE_PATH = "/pot/rm/fa/selectPrgrmListView.do"
JINCHEON_FOREST_EXPERIENCE_INSTITUTION_ID = "ID02030033"
JINCHEON_FOREST_EXPERIENCE_MENU_ID = "002003"
JINCHEON_FOREST_EXPERIENCE_URL = (
    "https://www.foresttrip.go.kr/pot/rm/fa/selectPrgrmListView.do?"
    "hmpgId=ID02030033&menuId=002003"
)
JINCHEON_FOREST_EXPERIENCE_DETAIL_PATH = "/pot/rm/fa/selectPrgrmDtlView.do"
JINCHEON_FOREST_EXPERIENCE_MUNICIPALITY_CODE = "4375000000"
JINCHEON_FOREST_EXPERIENCE_MUNICIPALITY_NAME = "충청북도 진천군"
JINCHEON_FOREST_EXPERIENCE_BRANCH = "생거진천자연휴양림"
JINCHEON_FOREST_EXPERIENCE_ADDRESS = "충청북도 진천군 백곡면 명암길 435-135"
JINCHEON_FOREST_EXPERIENCE_VENUE = "산림문화휴양관 주차장"
JINCHEON_FOREST_EXPERIENCE_MAX_BYTES = 2_000_000
JINCHEON_FOREST_EXPERIENCE_PARSER = (
    "jincheon_foresttrip_exact_public_programme_ledger+single_page_contract+"
    "stable_goods_identity+current_period+fixed_meeting_venue+experience_evidence+"
    "retail_addon_exclusion+locked_experience+provider_prefixed_id+"
    "application_url_suppressed+one_public_list_get_only+no_detail_reservation_"
    "apply_login_auth_member_applicant_pii_attachment_image_or_download_calls"
)

SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class JincheonForestExperienceContractError(RuntimeError):
    """Raised when the audited public programme contract changes."""


@dataclass(frozen=True)
class _Programme:
    identity: str
    title: str
    description: str
    start_date: date
    end_date: date


_SPACE_RE = re.compile(r"\s+")
_GOODS_ID_RE = re.compile(r"GID020300334000100\d{4}")
_PERIOD_RE = re.compile(
    r"이용일\s*:\s*(20\d{2}\.\d{2}\.\d{2})\s*~\s*"
    r"(20\d{2}\.\d{2}\.\d{2})"
)
_PAGE_RE = re.compile(r"1\s*\(1/1\)")
_DETAIL_CONTROL_RE = re.compile(
    r"return\s+runParse\(\s*'(?P<url>[^']+)'\s*,\s*'\.layer_wrap'\s*,\s*"
    r"\[\s*openLayer\s*,\s*activePgSlideShow\s*,\s*photoWrapSlide\s*\]\s*,\s*"
    r"this\s*\)\s*;?"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_KNOWN_RETAIL_ADDON = {
    "GID0203003340001000077": "숯 2kg+철망",
}
_EXPERIENCE_TITLE_MARKERS = ("체험", "숲해설")
_EXPERIENCE_EVIDENCE = (
    "교육 프로그램",
    "10시",
    "14시",
    "20명 정원",
    "무료입니다",
    "매주 화요일 휴무",
    "모이는 장소",
    JINCHEON_FOREST_EXPERIENCE_VENUE,
)
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "applicant",
        "applicant_name",
        "member",
        "member_id",
        "user",
        "user_id",
        "phone",
        "email",
        "contact",
        "manager",
        "attachment",
        "attachments",
        "download",
        "raw_html",
        "detail_description",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _canonical_list_url(value: Any) -> bool:
    try:
        parsed = urlparse(_clean(value))
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == JINCHEON_FOREST_EXPERIENCE_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == JINCHEON_FOREST_EXPERIENCE_PATH
        and query
        == [
            ("hmpgId", JINCHEON_FOREST_EXPERIENCE_INSTITUTION_ID),
            ("menuId", JINCHEON_FOREST_EXPERIENCE_MENU_ID),
        ]
        and not parsed.params
        and not parsed.fragment
    )


def is_jincheon_forest_experience_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")).upper()
        == JINCHEON_FOREST_EXPERIENCE_PROVIDER
        and _canonical_list_url(_target_value(target, "url"))
    )


is_target = is_jincheon_forest_experience_target


def _request_contract(method: Any, url: Any) -> None:
    if _clean(method).upper() != "GET" or not _canonical_list_url(url):
        raise JincheonForestExperienceContractError(
            "only the audited public programme-list GET is allowed; detail/reservation/"
            "apply/login/auth/member/applicant/PII/attachment/image/download routes are blocked"
        )


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _response_soup(response: Any) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise JincheonForestExperienceContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise JincheonForestExperienceContractError("redirected response refused")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and not _canonical_list_url(final_url):
        raise JincheonForestExperienceContractError("response escaped canonical list identity")
    content = getattr(response, "content", None)
    if content is None:
        content = str(getattr(response, "text", "")).encode("utf-8")
    if not isinstance(content, (bytes, bytearray)):
        raise JincheonForestExperienceContractError("response body is not bytes")
    payload = bytes(content)
    if not payload or len(payload) > JINCHEON_FOREST_EXPERIENCE_MAX_BYTES:
        raise JincheonForestExperienceContractError("response body size outside contract")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JincheonForestExperienceContractError("response is not strict UTF-8") from exc
    if "\x00" in text:
        raise JincheonForestExperienceContractError("response contains NUL bytes")
    soup = BeautifulSoup(text, "html.parser")
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != f"{JINCHEON_FOREST_EXPERIENCE_BRANCH} - 프로그램 |":
        raise JincheonForestExperienceContractError("official document title changed")
    return soup


def _single(root: Tag | BeautifulSoup, selector: str, field: str) -> Tag:
    nodes = root.select(selector)
    if len(nodes) != 1:
        raise JincheonForestExperienceContractError(f"{field} selector cardinality changed")
    return nodes[0]


def _detail_identity(onclick: Any) -> str:
    match = _DETAIL_CONTROL_RE.fullmatch(_clean(onclick))
    if not match:
        raise JincheonForestExperienceContractError("public detail identity control changed")
    try:
        parsed = urlparse(match.group("url"))
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise JincheonForestExperienceContractError("invalid public detail identity") from exc
    if not (
        not parsed.scheme
        and not parsed.netloc
        and parsed.path == JINCHEON_FOREST_EXPERIENCE_DETAIL_PATH
        and len(query) == 2
        and query[0]
        == ("insttId", JINCHEON_FOREST_EXPERIENCE_INSTITUTION_ID)
        and query[1][0] == "goodsId"
        and _GOODS_ID_RE.fullmatch(query[1][1])
        and not parsed.params
        and not parsed.fragment
    ):
        raise JincheonForestExperienceContractError("public detail identity escaped contract")
    return query[1][1]


def _parse_programme(item: Tag) -> _Programme:
    anchor = _single(item, ":scope > a[onclick]", "programme anchor")
    if _clean(anchor.get("href")) != "#runParse":
        raise JincheonForestExperienceContractError("programme anchor changed")
    identity = _detail_identity(anchor.get("onclick"))
    title_node = _single(item, ".pi_pt > .pp_ti", "programme title")
    description_node = _single(item, ".pi_pt > .pp_txt", "programme description")
    period_node = _single(item, ".pi_pt > .pp_list > li", "programme period")
    title = _clean(title_node.get_text(" ", strip=True))
    description = _clean(description_node.get_text(" ", strip=True))
    if not title:
        raise JincheonForestExperienceContractError("programme title became empty")
    period_match = _PERIOD_RE.fullmatch(_clean(period_node.get_text(" ", strip=True)))
    if not period_match:
        raise JincheonForestExperienceContractError("programme period changed")
    start = date.fromisoformat(period_match.group(1).replace(".", "-"))
    end = date.fromisoformat(period_match.group(2).replace(".", "-"))
    if end < start:
        raise JincheonForestExperienceContractError("programme period is reversed")
    return _Programme(identity, title, description, start, end)


def _parse_ledger(soup: BeautifulSoup) -> tuple[_Programme, ...]:
    heading = _single(soup, "h1", "institution heading")
    if _clean(heading.get_text(" ", strip=True)) != JINCHEON_FOREST_EXPERIENCE_BRANCH:
        raise JincheonForestExperienceContractError("institution heading changed")
    footer = _single(soup, ".fa_addr", "official institution address")
    footer_text = _clean(footer.get_text(" ", strip=True))
    if not all(
        marker in footer_text
        for marker in (
            "충북 진천군 백곡면 명암길 435-135",
            JINCHEON_FOREST_EXPERIENCE_BRANCH,
        )
    ):
        raise JincheonForestExperienceContractError("official municipality address changed")
    form = _single(soup, "form#fripPotForm", "programme list form")
    if _clean(form.get("method")).lower() != "post":
        raise JincheonForestExperienceContractError("programme list form changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select(":scope > input[type=hidden]")
    }
    if hidden != {
        "hmpgId": JINCHEON_FOREST_EXPERIENCE_INSTITUTION_ID,
        "nowPage": "1",
    }:
        raise JincheonForestExperienceContractError("programme list identity fields changed")
    page = _single(soup, ".page_list", "programme pager")
    if not _PAGE_RE.fullmatch(_clean(page.get_text(" ", strip=True))):
        raise JincheonForestExperienceContractError("programme ledger is no longer one complete page")
    container = _single(soup, ".prog_webzinlist", "programme ledger")
    items = container.select(":scope > .pw_item")
    if not items:
        raise JincheonForestExperienceContractError("programme ledger became empty")
    programmes = tuple(_parse_programme(item) for item in items)
    identities = [row.identity for row in programmes]
    if len(identities) != len(set(identities)):
        raise JincheonForestExperienceContractError("duplicate programme identity")
    return programmes


def _classification(programme: _Programme, cutoff: date) -> str:
    if programme.end_date < cutoff:
        return "expired"
    if _KNOWN_RETAIL_ADDON.get(programme.identity) == programme.title:
        return "retail_addon_not_programme"
    if not any(marker in programme.title for marker in _EXPERIENCE_TITLE_MARKERS):
        return "not_experience_title"
    if not all(marker in programme.description for marker in _EXPERIENCE_EVIDENCE):
        return "missing_fixed_programme_evidence"
    return "experience"


def _audience(programme: _Programme) -> str:
    if "5세" in programme.description and "10세" in programme.description:
        return "어린이(5~10세)"
    if all(value in programme.description for value in ("어린이", "청소년", "성인")):
        return "어린이·청소년·성인"
    return ""


def _row(programme: _Programme, cutoff: date) -> dict[str, Any]:
    source_status = "운영예정" if cutoff < programme.start_date else "운영기간 중"
    status = "SCHEDULED" if cutoff < programme.start_date else "OPEN"
    return {
        "provider": JINCHEON_FOREST_EXPERIENCE_PROVIDER,
        "provider_course_id": (
            f"{JINCHEON_FOREST_EXPERIENCE_PROVIDER}:experience:{programme.identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "source_course_id": programme.identity,
        "title": programme.title,
        "branch": JINCHEON_FOREST_EXPERIENCE_BRANCH,
        "branch_code": JINCHEON_FOREST_EXPERIENCE_INSTITUTION_ID,
        "preserve_branch": True,
        "provider_organizer": JINCHEON_FOREST_EXPERIENCE_BRANCH,
        "venue": JINCHEON_FOREST_EXPERIENCE_VENUE,
        "venue_name": JINCHEON_FOREST_EXPERIENCE_VENUE,
        "address": (
            f"{JINCHEON_FOREST_EXPERIENCE_ADDRESS} "
            f"{JINCHEON_FOREST_EXPERIENCE_VENUE}"
        ),
        "region_sido": "충청북도",
        "region_sigungu": "진천군",
        "region_full_name": JINCHEON_FOREST_EXPERIENCE_MUNICIPALITY_NAME,
        "municipality_code": JINCHEON_FOREST_EXPERIENCE_MUNICIPALITY_CODE,
        "municipality_full_name": JINCHEON_FOREST_EXPERIENCE_MUNICIPALITY_NAME,
        "category": "생거진천자연휴양림 숲 체험",
        "category_raw": "프로그램",
        "program_type": "체험",
        "raw_url": JINCHEON_FOREST_EXPERIENCE_URL,
        "source_url": JINCHEON_FOREST_EXPERIENCE_URL,
        "application_url": "",
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "source_status": source_status,
        "status": status,
        "fee": "무료",
        "period": f"{programme.start_date.isoformat()} ~ {programme.end_date.isoformat()}",
        "start_date": programme.start_date.isoformat(),
        "end_date": programme.end_date.isoformat(),
        "schedule": "10:00, 14:00 / 매주 화요일 휴무",
        "capacity": "회차별 20명",
        "target": _audience(programme),
        "description": (
            f"{programme.title} · 10:00/14:00 · 회차별 20명 · "
            f"{JINCHEON_FOREST_EXPERIENCE_VENUE} 집결"
        ),
        "source_group": "municipal_reservation",
        "collection_category": "공공예약",
        "domain_category": "체험·견학",
        "service_group": "체험",
        "service_group_policy": "locked",
        "service_family": "experience",
        "operator_type": "지자체/공공기관",
        "collection_type": JINCHEON_FOREST_EXPERIENCE_PARSER,
        "raw_fields": {
            "identity": programme.identity,
            "source_period": (
                f"{programme.start_date.isoformat()} ~ {programme.end_date.isoformat()}"
            ),
            "source_status": source_status,
            "fixed_meeting_venue_verified": True,
            "programme_evidence_verified": True,
            "classification_locked": True,
            "service_family": "experience",
            "public_detail_requested": False,
        },
    }


def _contains_forbidden_output(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in _FORBIDDEN_OUTPUT_KEYS or _contains_forbidden_output(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_output(child) for child in value)
    if isinstance(value, str):
        return bool(
            _PHONE_RE.search(value)
            or _EMAIL_RE.search(value)
            or _RESIDENT_ID_RE.search(value)
        )
    return False


def collect_jincheon_forest_experience(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 10,
    detail_limit: int = 30,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta: dict[str, Any] = {
        "municipality_code": JINCHEON_FOREST_EXPERIENCE_MUNICIPALITY_CODE,
        "owner_provider": JINCHEON_FOREST_EXPERIENCE_PROVIDER,
        "canonical_url": JINCHEON_FOREST_EXPERIENCE_URL,
        "parser": JINCHEON_FOREST_EXPERIENCE_PARSER,
        "source_total": 0,
        "source_current_count": 0,
        "source_expired_count": 0,
        "returned_count": 0,
        "excluded_count": 0,
        "excluded_reason_counts": {},
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "physical_requests": 0,
        "application_endpoint_requests": 0,
        "reservation_endpoint_requests": 0,
        "login_auth_member_applicant_pii_endpoint_requests": 0,
        "attachment_image_download_endpoint_requests": 0,
        "unsafe_endpoint_calls": 0,
        "pii_payload_persisted": False,
        "pagination_complete": False,
        "classification_complete": False,
        "fixed_venue_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_identity_sha256": "",
        "output_identity_sha256": "",
        "configured_collection_error": "",
        "errors": [],
    }
    if not is_jincheon_forest_experience_target(target):
        meta["configured_collection_error"] = "target/provider failed exact contract"
        return [], JINCHEON_FOREST_EXPERIENCE_PARSER, meta
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in (timeout, max_pages, detail_limit)
    ):
        meta["configured_collection_error"] = "invalid collection limits"
        return [], JINCHEON_FOREST_EXPERIENCE_PARSER, meta
    if max_pages < 1:
        meta["configured_collection_error"] = "max_pages truncates the one-page ledger"
        return [], JINCHEON_FOREST_EXPERIENCE_PARSER, meta

    factory = session_factory or _default_session_factory
    current = factory()
    try:
        _request_contract("GET", JINCHEON_FOREST_EXPERIENCE_URL)
        meta["physical_requests"] += 1
        meta["list_requests"] += 1
        response = current.get(
            JINCHEON_FOREST_EXPERIENCE_URL,
            timeout=timeout,
            allow_redirects=False,
        )
        programmes = _parse_ledger(_response_soup(response))
        meta["pages"] = meta["data_pages"] = 1
        meta["pagination_complete"] = True
        if len(programmes) > detail_limit:
            raise JincheonForestExperienceContractError(
                "detail_limit truncates complete programme classification"
            )
        cutoff = _today(today)
        reasons: Counter[str] = Counter()
        rows: list[dict[str, Any]] = []
        current_identities: list[str] = []
        for programme in programmes:
            reason = _classification(programme, cutoff)
            if reason == "expired":
                meta["source_expired_count"] += 1
                reasons[reason] += 1
                continue
            meta["source_current_count"] += 1
            current_identities.append(programme.identity)
            if reason != "experience":
                reasons[reason] += 1
                continue
            rows.append(_row(programme, cutoff))
        meta["source_total"] = len(programmes)
        meta["excluded_reason_counts"] = dict(sorted(reasons.items()))
        meta["excluded_count"] = sum(reasons.values())
        meta["classification_complete"] = True
        meta["fixed_venue_complete"] = all(
            row.get("venue") == JINCHEON_FOREST_EXPERIENCE_VENUE for row in rows
        )
        meta["source_identity_sha256"] = hashlib.sha256(
            "\n".join(sorted(programme.identity for programme in programmes)).encode("utf-8")
        ).hexdigest()
        if dedupe_rows is not None:
            rows = list(dedupe_rows(rows))
        identities = [_clean(row.get("provider_course_id")) for row in rows]
        if (
            not rows
            or len(identities) != len(set(identities))
            or any(
                not identity.startswith(
                    f"{JINCHEON_FOREST_EXPERIENCE_PROVIDER}:experience:"
                )
                for identity in identities
            )
        ):
            raise JincheonForestExperienceContractError(
                "empty, duplicate or non-provider-prefixed experience identity"
            )
        if any(_contains_forbidden_output(row) for row in rows):
            meta["pii_payload_persisted"] = True
            raise JincheonForestExperienceContractError(
                "PII or forbidden application payload reached output"
            )
        meta["returned_count"] = len(rows)
        meta["output_identity_sha256"] = hashlib.sha256(
            "\n".join(sorted(identities)).encode("utf-8")
        ).hexdigest()
        meta["snapshot_complete"] = bool(
            meta["pagination_complete"]
            and meta["classification_complete"]
            and meta["fixed_venue_complete"]
            and meta["source_total"]
            == meta["source_current_count"] + meta["source_expired_count"]
            and meta["source_current_count"]
            == meta["returned_count"]
            + sum(
                count
                for reason, count in reasons.items()
                if reason != "expired"
            )
            and meta["unsafe_endpoint_calls"] == 0
        )
        meta["full_snapshot_validated"] = meta["snapshot_complete"]
        if not meta["snapshot_complete"]:
            raise JincheonForestExperienceContractError("snapshot completeness invariant failed")
        return rows, JINCHEON_FOREST_EXPERIENCE_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        meta["errors"] = [meta["configured_collection_error"]]
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], JINCHEON_FOREST_EXPERIENCE_PARSER, meta
    finally:
        _close(current)
