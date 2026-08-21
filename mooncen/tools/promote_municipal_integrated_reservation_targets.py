from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.course_title_quality import semantic_course_title_rejection_reason as shared_title_rejection_reason
from utils.generic_course_eligibility import editorial_surface_reason, generic_course_row_decision

QUEUE = ROOT / "config" / "municipal_course_search_targets.yaml"
CANDIDATES = ROOT / "config" / "municipal_course_candidate_results.yaml"
TARGET_DIR = ROOT / "config" / "crawl_targets"
PRODUCTION_PROVIDERS = ROOT / "config" / "production_crawler_providers.yaml"
PRODUCTION_EVIDENCE = ROOT / "config" / "production_municipal_provider_evidence.yaml"
MANUAL_OVERRIDES = ROOT / "config" / "municipal_integrated_reservation_overrides.yaml"
COVERAGE_OUT = ROOT / "config" / "municipal_integrated_reservation_coverage.yaml"
REVIEW_OUT = ROOT / "config" / "municipal_integrated_reservation_promotion_review.yaml"
OPERATIONAL_OUT = ROOT / "config" / "municipal_integrated_reservation_operational.yaml"
TARGET_OUT = TARGET_DIR / "municipal_integrated_reservation.yaml"

WORKING_STATUSES = {"ready", "partial", "candidate", "generated"}
OPS_SCOPES = {"education", "experience"}
STATUS_RANK = {"ready": 0, "partial": 1, "generated": 2, "candidate": 3}
MANUAL_OVERRIDE_STATUSES = {"candidate", "excluded"}
MANUAL_EXCLUSION_REASON_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,63}\Z")
DUPLICATE_QUERY_DROP_PARAMS = {
    "_",
    "callback",
    "currentpage",
    "currentpageno",
    "currpage",
    "nowpage",
    "page",
    "pageindex",
    "pageno",
    "pageunit",
    "pagesize",
    "recordcountperpage",
    "rows",
    "timestamp",
    "token",
    "ts",
    "viewtype",
}
LOW_VALUE_DOMAINS = {
    "blog.naver.com",
    "m.blog.naver.com",
    "nid.naver.com",
    "www.data.go.kr",
    "data.go.kr",
    "data.busan.go.kr",
    "data.daegu.go.kr",
    "www.newsro.kr",
    "mediahub.seoul.go.kr",
}
LOW_VALUE_DOMAIN_TOKENS = ("news", "daily", "press", "ilbo", "times", "today", "domin", "blog", "data.")
EXCLUDED_DOMAIN_TOKENS = ("e-ncom.co.kr",)
EXCLUDED_MEDIA_DOMAINS = (
    "asiatoday.co.kr",
    "boeuni.com",
    "brcity.kr",
    "cctimes.kr",
    "cfnews.kr",
    "domin.co.kr",
    "gukjenews.com",
    "hyundaiilbo.com",
    "idaegu.co.kr",
    "igangbuk.com",
    "igimpo.com",
    "imedialife.co.kr",
    "jeollailbo.com",
    "jjn.co.kr",
    "jnilbo.com",
    "jntoday.co.kr",
    "joongdo.co.kr",
    "kbsm.net",
    "khan.co.kr",
    "kjilbo.co.kr",
    "kmaeil.com",
    "kwtotalnews.kr",
    "kyongbuk.com",
    "mygoyang.com",
    "newsfire.co.kr",
    "pointe.co.kr",
    "seoulilbo.com",
    "todayan.com",
    "yangsanilbo.com",
    "yg21.co.kr",
    "yongin21.co.kr",
)
EXCLUDED_PATH_TOKENS = (
    "/news/",
    "/m_news/",
    "/attaches/",
    "articleview",
    "/notice/detail/",
    "bbsmsgdetail",
    "selectbbsdetail",
    "selectbbsnttview",
    "selectnttlist",
    "selectboardview",
    "/board/view.",
    "/board/view/",
    "board/download",
    "/download.do",
    "doviewboarditem",
    "/media/board/",
    "cmmboardview.do",
    "mode=view",
    "opendata/view",
    ".pdf",
    ".hwp",
    ".hwpx",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".zip",
)
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "password",
    "secret",
    "session",
    "sessionid",
    "token",
}

_NON_COURSE_TITLE_KEYS = frozenset(
    {
        "조종면",
        "학습장소",
        "접수중강좌",
        "접수예정강좌",
        "인문교양",
        "공지사항",
        "교육강좌",
        "게시물검색",
        "접수모집",
        "민원안내",
        "구술전화신청민원",
        "성인신청",
        "제물포구청",
        "인천광역시서해구",
        "교육명장소",
        "선사체험마을",
        "선사체험마을신청",
        "디지털저장매체파기신청",
        "영천시평생학습관메인",
    }
)
_SITE_SLOGAN_FRAGMENTS = (
    "오신것을환영",
    "방문을환영",
    "거침없는도약",
    "당찬당진",
    "시민이행복한",
    "군민이행복한",
    "구민이행복한",
    "살기좋은",
    "미래를여는",
    "더큰당진",
)
_COURSE_INTENT_FRAGMENTS = (
    "강좌",
    "강의",
    "교실",
    "교육",
    "과정",
    "특강",
    "아카데미",
    "만들기",
    "배우기",
    "체험",
    "수업",
    "캠프",
    "워크숍",
    "세미나",
    "탐방",
    "놀이",
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalized_course_title(value: Any) -> tuple[str, str]:
    display = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    display = re.sub(r"<[^>]*>", " ", display)
    display = " ".join(display.split()).strip(" \t\r\n|/\\>·ㆍ-_:;,.")
    key = re.sub(r"[^0-9a-z가-힣]", "", display.casefold())
    return display, key


def semantic_course_title_rejection_reason(value: Any) -> str:
    """Return why a collected title is not evidence of a real course."""
    return shared_title_rejection_reason(value)


def semantic_live_validation_quality(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    raw_rows = list(rows)
    reasons: Counter[str] = Counter()
    rejected_titles: list[str] = []
    accepted_row_count = 0
    for row in raw_rows:
        title = row.get("title") if isinstance(row, dict) else ""
        reason = semantic_course_title_rejection_reason(title)
        if not reason and isinstance(row, dict):
            raw_fields = row.get("raw_fields") if isinstance(row.get("raw_fields"), dict) else {}
            parser = str(raw_fields.get("parser") or "").lower()
            reason = editorial_surface_reason(
                row.get("raw_url"),
                source_url=raw_fields.get("source_url"),
                context=raw_fields.get("surface_context"),
            )
            if not reason and parser.startswith("generic_"):
                eligible, eligibility_reason = generic_course_row_decision(row)
                if not eligible:
                    reason = eligibility_reason
        if not reason:
            accepted_row_count += 1
            continue
        reasons[reason] += 1
        display, _ = _normalized_course_title(title)
        if display and display not in rejected_titles and len(rejected_titles) < 10:
            rejected_titles.append(display)
    return {
        "raw_row_count": len(raw_rows),
        "accepted_row_count": accepted_row_count,
        "rejected_row_count": len(raw_rows) - accepted_row_count,
        "rejection_reasons": dict(sorted(reasons.items())),
        "rejected_title_samples": rejected_titles,
    }


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML document must be a mapping: {path}")
    return data


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=160),
        encoding="utf-8",
    )


def normalized_duplicate_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query_pairs = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in DUPLICATE_QUERY_DROP_PARAMS
    ]
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def target_urls(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("url", "list_url", "base_url"):
        if str(row.get(key) or "").strip():
            values.append(str(row[key]).strip())
    for value in row.get("list_urls") or []:
        if str(value or "").strip():
            values.append(str(value).strip())
    return list(dict.fromkeys(values))


def target_ownership_urls(row: dict[str, Any]) -> list[tuple[str, str]]:
    """Return crawl URLs and explicit non-crawl aliases used for ownership only."""

    entries = [(value, "exact_active_url") for value in target_urls(row)]
    raw_aliases = row.get("ownership_aliases") or []
    if not isinstance(raw_aliases, list):
        raise ValueError(
            f"target {str(row.get('provider') or '').strip().upper() or '<missing>'}: "
            "ownership_aliases must be a list"
        )
    for raw_value in raw_aliases:
        value = str(raw_value or "").strip()
        if not value:
            raise ValueError(
                f"target {str(row.get('provider') or '').strip().upper() or '<missing>'}: "
                "ownership_aliases cannot contain empty URLs"
            )
        if candidate_exclusion_reason(value):
            raise ValueError(
                f"target {str(row.get('provider') or '').strip().upper() or '<missing>'}: "
                f"unsafe ownership alias {value!r}"
            )
        entries.append((value, "ownership_alias"))
    deduped: dict[str, tuple[str, str]] = {}
    for value, kind in entries:
        normalized = normalized_duplicate_url(value)
        if normalized:
            deduped.setdefault(normalized, (value, kind))
    return list(deduped.values())


def explicit_duplicate_reason(row: dict[str, Any]) -> str:
    duplicate_of = str(row.get("duplicate_of") or "").strip()
    if duplicate_of:
        return f"duplicate_of:{duplicate_of}"
    blocked_reason = str(row.get("blocked_reason") or "").strip()
    if blocked_reason.lower().startswith("duplicate_of:"):
        return blocked_reason
    last_quality = row.get("last_quality") if isinstance(row.get("last_quality"), dict) else {}
    error_kind = str(last_quality.get("error_kind") or "").strip()
    if error_kind.lower().startswith("duplicate_of:"):
        return error_kind
    if str(row.get("collection_type") or "").strip().lower() == "duplicate":
        return "duplicate_collection_type"
    status = str(row.get("crawler_status") or row.get("status") or "").strip()
    return status if status.lower().startswith("duplicate_url:") else ""


def candidate_exclusion_reason(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return "invalid_http_url"
    if len(text) > 8192:
        return "url_too_long"
    if parsed.username or parsed.password:
        return "credential_bearing_url"
    if re.search(r";j?sessionid=", parsed.path, re.IGNORECASE):
        return "session_bearing_url"
    if any(key.lower() in SENSITIVE_QUERY_KEYS for key, _item in parse_qsl(parsed.query, keep_blank_values=True)):
        return "sensitive_query_url"
    host = parsed.netloc.lower()
    lowered = text.lower()
    if host in LOW_VALUE_DOMAINS or host.endswith(".naver.com"):
        return "low_value_domain"
    if any(token in host for token in LOW_VALUE_DOMAIN_TOKENS + EXCLUDED_DOMAIN_TOKENS):
        return "low_value_domain"
    if any(token in host for token in EXCLUDED_MEDIA_DOMAINS):
        return "media_domain"
    if any(token in lowered for token in EXCLUDED_PATH_TOKENS):
        return "excluded_url_shape"
    return ""


def target_preference_key(row: dict[str, Any], production_providers: set[str]) -> tuple[int, int, int, str, str]:
    provider = str(row.get("provider") or "").strip().upper()
    status = str(row.get("crawler_status") or row.get("status") or "").strip().lower()
    return (
        0 if provider in production_providers else 1,
        int(row.get("priority") or 9),
        STATUS_RANK.get(status, 9),
        str(row.get("source") or ""),
        provider,
    )


def stable_provider(value: str) -> str:
    parsed = urlparse(value)
    host = re.sub(r"[^A-Za-z0-9]+", "_", parsed.netloc.upper()).strip("_")
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8].upper()
    return (f"MUNI_{host}_{digest}" if host else f"MUNI_{digest}")[:50]


def candidate_id(value: str) -> str:
    return "MUNI_IR_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()


def load_target_rows(target_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(target_dir.glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        document = load_yaml(path)
        defaults = {
            key: document.get(key)
            for key in ("collection_category", "domain_category", "source_group", "operator_type", "service_group")
            if document.get(key)
        }
        for index, raw_row in enumerate(document.get("targets") or [], start=1):
            if not isinstance(raw_row, dict):
                continue
            rows.append({**defaults, **raw_row, "_target_file": path.name, "_target_index": index})
    return rows


def owner_indexes(
    rows: Iterable[dict[str, Any]],
    production_providers: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    working: dict[str, list[dict[str, Any]]] = defaultdict(list)
    disabled: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        status = str(row.get("crawler_status") or row.get("status") or "").strip().lower()
        duplicate_reason = explicit_duplicate_reason(row)
        canonical_target_url = next(iter(target_urls(row)), "")
        for raw_url, ownership_kind in target_ownership_urls(row):
            key = normalized_duplicate_url(raw_url)
            if not key:
                continue
            item = {
                **row,
                "_matched_target_url": raw_url,
                "_canonical_target_url": canonical_target_url,
                "_ownership_match_kind": ownership_kind,
            }
            if status in WORKING_STATUSES and not duplicate_reason and not candidate_exclusion_reason(raw_url):
                working[key].append(item)
            else:
                disabled[key].append(item)
    for values in working.values():
        values.sort(key=lambda row: target_preference_key(row, production_providers))
    return dict(working), dict(disabled)


def explicitly_mapped_ops_target_codes(
    row: dict[str, Any],
    queue_by_code: dict[str, dict[str, Any]],
) -> list[str]:
    """Return canonical municipalities deliberately owned by one Ops target.

    Search candidates are only discovery inputs.  A live-validated target can
    own a municipality even when Google did not return its exact crawl URL, so
    explicit education/experience mappings must survive manifest regeneration
    independently of the candidate queue.
    """

    declared_scopes = row.get("ops_scopes")
    if declared_scopes is None:
        return []
    if (
        not isinstance(declared_scopes, list)
        or not declared_scopes
        or len(declared_scopes) > len(OPS_SCOPES)
        or any(
            not isinstance(scope, str) or scope not in OPS_SCOPES
            for scope in declared_scopes
        )
        or len(set(declared_scopes)) != len(declared_scopes)
    ):
        provider = str(row.get("provider") or "").strip().upper() or "<missing>"
        raise ValueError(f"target {provider}: invalid ops_scopes")

    raw_codes: list[Any] = []
    covered = row.get("covered_municipalities")
    if covered is not None:
        if not isinstance(covered, list):
            raise ValueError("covered_municipalities must be a list")
        for municipality in covered:
            raw_codes.append(
                municipality.get("code")
                if isinstance(municipality, dict)
                else municipality
            )

    row_codes = row.get("row_municipality_codes")
    if row_codes is not None:
        if not isinstance(row_codes, list):
            raise ValueError("row_municipality_codes must be a list")
        raw_codes.extend(row_codes)
    raw_codes.append(row.get("municipality_code"))

    by_full_name = {
        str(municipality.get("full_name") or "").strip(): code
        for code, municipality in queue_by_code.items()
        if str(municipality.get("full_name") or "").strip()
    }
    full_name = str(row.get("municipality_full_name") or "").strip()
    if full_name and full_name in by_full_name:
        raw_codes.append(by_full_name[full_name])

    return list(
        dict.fromkeys(
            code
            for value in raw_codes
            if (code := str(value or "").strip()) in queue_by_code
        )
    )


def load_production_providers(path: Path) -> set[str]:
    providers = load_yaml(path).get("providers") or []
    return {str(provider).strip().upper() for provider in providers if str(provider).strip()}


def load_production_evidence(path: Path) -> dict[str, dict[str, Any]]:
    document = load_yaml(path)
    rows = document.get("providers") or []
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "").strip().upper()
        if provider:
            result[provider] = {
                "checked_at": document.get("checked_at"),
                "environment": document.get("environment"),
                "scope": document.get("scope"),
                **row,
            }
    return result


def owner_has_production_evidence(
    provider: str,
    production_providers: set[str],
    production_evidence: dict[str, dict[str, Any]],
    normalized_url: str = "",
) -> bool:
    if provider not in production_providers:
        return False
    evidence = production_evidence.get(provider) or {}
    if int(evidence.get("active_course_count") or 0) <= 0:
        return False
    active_urls = evidence.get("active_urls")
    if active_urls is None:
        return True
    if not isinstance(active_urls, list):
        raise ValueError(f"production evidence {provider} active_urls must be a list")
    normalized_active_urls = {
        normalized_duplicate_url(value)
        for value in active_urls
        if str(value or "").strip()
    }
    return bool(normalized_url and normalized_url in normalized_active_urls)


def ownership_evidence(
    candidate_url: str,
    normalized_url: str,
    owner: dict[str, Any],
    production_providers: set[str],
    production_evidence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    provider = str(owner.get("provider") or "").strip().upper()
    if owner_has_production_evidence(
        provider,
        production_providers,
        production_evidence,
        normalized_url,
    ):
        basis = "production_scheduled_active_courses"
    elif provider in production_providers:
        basis = "production_scheduled_without_active_course_evidence"
    else:
        basis = "yaml_working_not_scheduled"
    ownership_kind = str(owner.get("_ownership_match_kind") or "exact_active_url")
    evidence = {
        "kind": ownership_kind,
        "ownership_basis": basis,
        "candidate_id": candidate_id(normalized_url),
        "provider": provider,
        "candidate_url": candidate_url,
        "target_url": owner.get("_canonical_target_url") or owner.get("_matched_target_url"),
        "normalized_url": normalized_url,
        "target_file": owner.get("_target_file"),
        "target_index": owner.get("_target_index"),
    }
    if ownership_kind == "ownership_alias":
        evidence["ownership_alias"] = owner.get("_matched_target_url")
    production_row = production_evidence.get(provider) or {}
    if production_row:
        evidence["production_active_course_count"] = int(production_row.get("active_course_count") or 0)
        if production_row.get("active_branch_count") is not None:
            evidence["production_active_branch_count"] = int(production_row.get("active_branch_count") or 0)
        if production_row.get("checked_at"):
            evidence["production_checked_at"] = production_row["checked_at"]
    return evidence


def trust_tier(value: str) -> str:
    host = (urlparse(value).hostname or "").lower()
    if host.endswith(".go.kr") or host.endswith(".or.kr"):
        return "official_public_domain"
    if host.endswith((".seoul.kr", ".busan.kr", ".daegu.kr", ".gwangju.kr", ".incheon.kr")):
        return "official_public_domain"
    return "unverified_domain"


def unique_municipalities(occurrences: Iterable[tuple[dict[str, Any], dict[str, Any]]]) -> list[dict[str, str]]:
    values: dict[str, dict[str, str]] = {}
    for municipality, _candidate in occurrences:
        code = str(municipality.get("code") or "").strip()
        values.setdefault(
            code,
            {
                "code": code,
                "sido": str(municipality.get("sido") or ""),
                "sigungu": str(municipality.get("sigungu") or ""),
                "full_name": str(municipality.get("full_name") or ""),
            },
        )
    return list(values.values())


OPERATIONAL_REQUIRED_FIELDS = {
    "provider",
    "normalized_url",
    "target_url",
    "action",
    "validation_outcome",
    "validated_at",
    "parser",
    "row_count",
    "no_current_data",
    "municipalities",
}
OPERATIONAL_ACTIONS = {"live_validate_new", "schedule_existing"}
OPERATIONAL_OUTCOMES = {"collected", "no_current_data"}


def operational_entries(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    data = document or {"version": 1, "entries": []}
    if data.get("version") != 1:
        raise ValueError("operational allowlist version must be 1")
    rows = data.get("entries") or []
    if not isinstance(rows, list):
        raise ValueError("operational allowlist entries must be a list")
    result: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    seen_urls: dict[str, str] = {}
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"operational entry {index} must be a mapping")
        missing = sorted(key for key in OPERATIONAL_REQUIRED_FIELDS if key not in raw)
        if missing:
            raise ValueError(f"operational entry {index} missing fields: {', '.join(missing)}")
        action = str(raw.get("action") or "")
        outcome = str(raw.get("validation_outcome") or "")
        if action not in OPERATIONAL_ACTIONS:
            raise ValueError(f"operational entry {index} has invalid action: {action}")
        if outcome not in OPERATIONAL_OUTCOMES:
            raise ValueError(f"operational entry {index} has invalid validation_outcome: {outcome}")
        row_count = int(raw.get("row_count") or 0)
        no_current_data = bool(raw.get("no_current_data"))
        if outcome == "collected" and (row_count <= 0 or no_current_data):
            raise ValueError(f"operational entry {index} collected outcome requires positive row_count")
        if outcome == "no_current_data" and (row_count != 0 or not no_current_data):
            raise ValueError(f"operational entry {index} no_current_data outcome is inconsistent")
        municipalities = raw.get("municipalities")
        if not isinstance(municipalities, list) or not municipalities:
            raise ValueError(f"operational entry {index} requires municipalities")
        for municipality in municipalities:
            if not isinstance(municipality, dict) or any(
                not str(municipality.get(key) or "").strip()
                for key in ("code", "sido", "sigungu", "full_name")
            ):
                raise ValueError(f"operational entry {index} has invalid municipality")
        normalized_url = normalized_duplicate_url(raw.get("normalized_url"))
        if not normalized_url or normalized_url != str(raw.get("normalized_url") or ""):
            raise ValueError(f"operational entry {index} normalized_url is not canonical")
        normalized_row = dict(raw)
        key = operational_entry_key(normalized_row)
        provider = key[0]
        if key in seen_keys:
            raise ValueError(f"operational entry {index} duplicates provider URL ownership")
        prior_provider = seen_urls.get(normalized_url)
        if prior_provider and prior_provider != provider:
            raise ValueError(f"operational entry {index} URL is already owned by {prior_provider}")
        seen_keys.add(key)
        seen_urls[normalized_url] = provider
        result.append(normalized_row)
    return result


def operational_entry_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("provider") or "").strip().upper(),
        str(row.get("normalized_url") or ""),
    )


def _review_candidate_identity(row: Any) -> tuple[str, str, str] | None:
    if not isinstance(row, dict):
        return None
    values = tuple(row.get(key) for key in ("candidate_id", "provider", "normalized_url"))
    if not all(isinstance(value, str) and value for value in values):
        return None
    candidate_value, provider, normalized_url = values
    return candidate_value, provider, normalized_url


def preserve_live_validations(
    review_document: dict[str, Any],
    existing_review_document: dict[str, Any] | None,
) -> int:
    """Copy prior validation evidence only across an exact candidate identity match."""

    if not isinstance(review_document, dict) or not isinstance(existing_review_document, dict):
        return 0
    existing_rows = (existing_review_document or {}).get("candidates")
    new_rows = review_document.get("candidates")
    if not isinstance(existing_rows, list) or not isinstance(new_rows, list):
        return 0

    identity_counts: Counter[tuple[str, str, str]] = Counter()
    validations: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in existing_rows:
        identity = _review_candidate_identity(row)
        if identity is None:
            continue
        identity_counts[identity] += 1
        validation = row.get("live_validation")
        if isinstance(validation, dict):
            validations[identity] = validation

    preserved = 0
    for row in new_rows:
        identity = _review_candidate_identity(row)
        if identity is None or identity_counts[identity] != 1:
            continue
        validation = validations.get(identity)
        if validation is None:
            continue
        row["live_validation"] = deepcopy(validation)
        preserved += 1
    return preserved


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def hydrate_live_validations_from_operational_entries(
    review_document: dict[str, Any],
    entries: Iterable[dict[str, Any]],
    target_rows: Iterable[dict[str, Any]],
) -> int:
    """Reconstruct missing validation evidence from mutually consistent snapshots."""

    if not isinstance(review_document, dict):
        return 0
    review_rows = review_document.get("candidates")
    if not isinstance(review_rows, list):
        return 0

    entry_rows = list(entries)
    entry_counts: Counter[tuple[str, str]] = Counter()
    entries_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entry_rows:
        if not isinstance(entry, dict):
            continue
        provider = entry.get("provider")
        normalized_url = entry.get("normalized_url")
        target_url = entry.get("target_url")
        if not all(isinstance(value, str) and value for value in (provider, normalized_url, target_url)):
            continue
        if normalized_duplicate_url(normalized_url) != normalized_url:
            continue
        if normalized_duplicate_url(target_url) != normalized_url:
            continue
        key = (provider, normalized_url)
        entry_counts[key] += 1
        entries_by_key[key] = entry

    targets_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for target_row in target_rows:
        if not isinstance(target_row, dict):
            continue
        provider = target_row.get("provider")
        if not isinstance(provider, str) or not provider:
            continue
        normalized_urls = {
            normalized_duplicate_url(raw_url)
            for raw_url in target_urls(target_row)
            if normalized_duplicate_url(raw_url)
        }
        for normalized_url in normalized_urls:
            targets_by_key[(provider, normalized_url)].append(target_row)

    hydrated = 0
    for review_row in review_rows:
        if not isinstance(review_row, dict) or "live_validation" in review_row:
            continue
        provider = review_row.get("provider")
        normalized_url = review_row.get("normalized_url")
        if not isinstance(provider, str) or not provider:
            continue
        if not isinstance(normalized_url, str) or normalized_duplicate_url(normalized_url) != normalized_url:
            continue
        key = (provider, normalized_url)
        if entry_counts[key] != 1:
            continue
        matching_targets = targets_by_key.get(key) or []
        if len(matching_targets) != 1:
            continue

        entry = entries_by_key[key]
        quality = matching_targets[0].get("last_quality")
        if not isinstance(quality, dict) or quality.get("snapshot_complete") is False:
            continue
        entry_parser = entry.get("parser")
        quality_parser = quality.get("parser")
        entry_row_count = _nonnegative_int(entry.get("row_count"))
        quality_row_count = _nonnegative_int(quality.get("collected"))
        entry_no_current_data = entry.get("no_current_data")
        if "no_current_data" in quality and not isinstance(quality.get("no_current_data"), bool):
            continue
        quality_no_current_data = quality.get("no_current_data", False)
        validated_at = entry.get("validated_at")
        if not isinstance(entry_parser, str) or entry_parser != quality_parser:
            continue
        if entry_row_count is None or entry_row_count != quality_row_count:
            continue
        if (
            not isinstance(entry_no_current_data, bool)
            or entry_no_current_data != quality_no_current_data
        ):
            continue
        if not isinstance(validated_at, str) or not validated_at:
            continue
        pages = _nonnegative_int(quality.get("pages", 0))
        detail_pages = _nonnegative_int(quality.get("detail_pages", 0))
        if pages is None or detail_pages is None:
            continue

        review_row["live_validation"] = {
            "checked_at": validated_at,
            "parser": entry_parser,
            "row_count": entry_row_count,
            "no_current_data": entry_no_current_data,
            "error": "",
            "raw_row_count": entry_row_count,
            "semantic_rejected_row_count": 0,
            "semantic_rejection_reasons": {},
            "semantic_rejected_title_samples": [],
            "semantic_quality_passed": True,
            "pages": pages,
            "detail_pages": detail_pages,
        }
        hydrated += 1
    return hydrated


def merge_operational_entries(path: Path, additions: list[dict[str, Any]]) -> bool:
    if not additions:
        return False
    existing_document = load_yaml(path) if path.exists() else {"version": 1, "entries": []}
    merged = {operational_entry_key(row): row for row in operational_entries(existing_document)}
    url_owners = {str(row["normalized_url"]): str(row["provider"]).strip().upper() for row in merged.values()}
    for row in operational_entries({"version": 1, "entries": additions}):
        key = operational_entry_key(row)
        prior_provider = url_owners.get(key[1])
        if prior_provider and prior_provider != key[0]:
            raise ValueError(f"operational URL {key[1]} is already owned by {prior_provider}")
        previous = merged.get(key)
        if previous:
            previous_municipalities = list(previous.get("municipalities") or [])
            incoming_municipalities = list(row.get("municipalities") or [])
            previous_codes = {
                str(municipality.get("code") or "")
                for municipality in previous_municipalities
                if isinstance(municipality, dict) and str(municipality.get("code") or "")
            }
            incoming_codes = {
                str(municipality.get("code") or "")
                for municipality in incoming_municipalities
                if isinstance(municipality, dict) and str(municipality.get("code") or "")
            }
            if previous_codes.issubset(incoming_codes):
                municipalities_by_code = {
                    str(municipality.get("code") or ""): dict(municipality)
                    for municipality in previous_municipalities + incoming_municipalities
                    if isinstance(municipality, dict) and str(municipality.get("code") or "")
                }
                municipalities = list(municipalities_by_code.values())
            else:
                municipalities = previous_municipalities
            row = {**row, "municipalities": municipalities}
        merged[key] = row
        url_owners[key[1]] = key[0]
    entries = sorted(
        merged.values(),
        key=lambda row: (
            str(row.get("normalized_url") or ""),
            str(row.get("provider") or ""),
            str(row.get("action") or ""),
        ),
    )
    write_yaml(path, {"version": 1, "updated_at": now_iso(), "entries": entries})
    return True


def _refresh_manifest_summaries(
    coverage_document: dict[str, Any],
    review_document: dict[str, Any],
) -> None:
    coverage_document["summary"]["by_status"] = dict(
        Counter(str(row.get("status") or "") for row in coverage_document.get("municipalities") or [])
    )
    review_document["summary"] = {
        "candidates": len(review_document.get("candidates") or []),
        "by_status": dict(Counter(str(row.get("status") or "") for row in review_document.get("candidates") or [])),
        "by_action": dict(
            Counter(str(row.get("recommended_action") or "") for row in review_document.get("candidates") or [])
        ),
        "by_trust_tier": dict(
            Counter(str(row.get("trust_tier") or "") for row in review_document.get("candidates") or [])
        ),
        "municipalities": len(
            {
                municipality["code"]
                for row in review_document.get("candidates") or []
                for municipality in row.get("municipalities") or []
            }
        ),
    }


def sync_live_validation_evidence_to_coverage(
    coverage_document: dict[str, Any],
    review_document: dict[str, Any],
) -> int:
    """Idempotently project review live-validation evidence into coverage rows."""

    if not isinstance(coverage_document, dict) or not isinstance(review_document, dict):
        return 0
    coverage_rows = coverage_document.get("municipalities")
    review_rows = review_document.get("candidates")
    if not isinstance(coverage_rows, list) or not isinstance(review_rows, list):
        return 0
    coverage_by_code = {
        row.get("code"): row
        for row in coverage_rows
        if isinstance(row, dict) and isinstance(row.get("code"), str) and row.get("code")
    }
    upserted = 0
    for review_row in review_rows:
        identity = _review_candidate_identity(review_row)
        if identity is None:
            continue
        candidate_value, provider, normalized_url = identity
        candidate_url = review_row.get("url")
        validation = review_row.get("live_validation")
        municipalities = review_row.get("municipalities")
        if not isinstance(candidate_url, str) or not candidate_url:
            continue
        if not isinstance(validation, dict) or not isinstance(municipalities, list):
            continue
        evidence = {
            "kind": "live_validation",
            "ownership_basis": "live_crawl_probe",
            "candidate_id": candidate_value,
            "provider": provider,
            "candidate_url": candidate_url,
            "normalized_url": normalized_url,
            **deepcopy(validation),
        }
        seen_codes: set[str] = set()
        for municipality in municipalities:
            if not isinstance(municipality, dict):
                continue
            code = municipality.get("code")
            if not isinstance(code, str) or not code or code in seen_codes:
                continue
            seen_codes.add(code)
            coverage_row = coverage_by_code.get(code)
            if coverage_row is None:
                continue
            prior_evidence = coverage_row.get("evidence")
            if not isinstance(prior_evidence, list):
                continue
            retained = [
                item
                for item in prior_evidence
                if not (
                    isinstance(item, dict)
                    and item.get("kind") == "live_validation"
                    and item.get("candidate_id") == candidate_value
                )
            ]
            coverage_row["evidence"] = [*retained, deepcopy(evidence)]
            upserted += 1
    return upserted


def apply_operational_entries_to_manifests(
    coverage_document: dict[str, Any],
    review_document: dict[str, Any],
    entries: Iterable[dict[str, Any]],
    *,
    target_rows: Iterable[dict[str, Any]] | None = None,
) -> None:
    entry_rows = list(entries)
    if target_rows is not None:
        hydrate_live_validations_from_operational_entries(
            review_document,
            entry_rows,
            target_rows,
        )
    coverage_by_code = {
        str(row.get("code") or ""): row
        for row in coverage_document.get("municipalities") or []
        if isinstance(row, dict)
    }
    review_by_url = {
        str(row.get("normalized_url") or ""): row
        for row in review_document.get("candidates") or []
        if isinstance(row, dict)
    }
    for entry in entry_rows:
        provider = str(entry.get("provider") or "").strip().upper()
        normalized_url = str(entry.get("normalized_url") or "")
        evidence = {
            "kind": "operational_allowlist",
            "ownership_basis": "live_validation_success",
            "provider": provider,
            "normalized_url": normalized_url,
            "target_url": entry.get("target_url"),
            "action": entry.get("action"),
            "validation_outcome": entry.get("validation_outcome"),
            "validated_at": entry.get("validated_at"),
            "parser": entry.get("parser"),
            "row_count": int(entry.get("row_count") or 0),
            "no_current_data": bool(entry.get("no_current_data")),
        }
        for municipality in entry.get("municipalities") or []:
            code = str(municipality.get("code") or "")
            row = coverage_by_code.get(code)
            if row is None:
                raise ValueError(f"operational entry references unknown municipality code: {code}")
            if row.get("status") not in {"covered_by_existing", "covered_by_parent"}:
                row["status"] = "promoted"
            owners = set(row.get("owner_providers") or [])
            owners.add(provider)
            row["owner_providers"] = sorted(owners)
            promoted = set(row.get("promoted_providers") or [])
            promoted.add(provider)
            row["promoted_providers"] = sorted(promoted)
            if evidence not in row.setdefault("evidence", []):
                row["evidence"].append(evidence)
        review_row = review_by_url.get(normalized_url)
        if review_row is not None:
            review_row["status"] = "promoted"
            summaries = review_row.setdefault("operational_entries", [])
            compact = {
                "provider": provider,
                "action": entry.get("action"),
                "validation_outcome": entry.get("validation_outcome"),
                "validated_at": entry.get("validated_at"),
                "municipality_codes": [str(row.get("code") or "") for row in entry.get("municipalities") or []],
            }
            if compact not in summaries:
                summaries.append(compact)
    operational_keys = {
        (
            str(evidence.get("provider") or "").strip().upper(),
            str(evidence.get("normalized_url") or ""),
        )
        for row in coverage_document.get("municipalities") or []
        if isinstance(row, dict)
        for evidence in row.get("evidence") or []
        if isinstance(evidence, dict) and evidence.get("kind") == "operational_allowlist"
    }
    coverage_document["summary"]["operational_entries"] = len(operational_keys)
    _refresh_manifest_summaries(coverage_document, review_document)


def build_manifests(
    queue_document: dict[str, Any],
    candidate_document: dict[str, Any],
    target_rows: list[dict[str, Any]],
    production_providers: set[str],
    production_evidence: dict[str, dict[str, Any]],
    overrides_document: dict[str, Any] | None = None,
    operational_document: dict[str, Any] | None = None,
    *,
    min_score: int = 8,
) -> tuple[dict[str, Any], dict[str, Any]]:
    municipalities = queue_document.get("municipalities") or []
    if not isinstance(municipalities, list) or not municipalities:
        raise ValueError("municipal search queue has no municipalities")
    queue_by_code: dict[str, dict[str, Any]] = {}
    for row in municipalities:
        if not isinstance(row, dict):
            raise ValueError("municipal search queue contains a non-mapping row")
        code = str(row.get("code") or "").strip()
        if not code or code in queue_by_code:
            raise ValueError(f"municipality code must be present and unique: {code!r}")
        queue_by_code[code] = row

    candidate_by_code = {
        str(row.get("code") or "").strip(): row
        for row in candidate_document.get("results") or []
        if isinstance(row, dict)
    }
    override_document = overrides_document or {}
    override_by_code = {
        str(row.get("code") or "").strip(): row
        for row in override_document.get("municipalities") or []
        if isinstance(row, dict)
    }
    working_owners, disabled_owners = owner_indexes(target_rows, production_providers)

    occurrences_by_url: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    municipality_stats: dict[str, dict[str, Any]] = {}
    for code, municipality in queue_by_code.items():
        result_row = candidate_by_code.get(code) or {}
        raw_candidates = []
        for row in result_row.get("candidates") or []:
            if isinstance(row, dict) and str(row.get("status") or "") == "candidate":
                raw_candidates.append(dict(row))
        manual_exclusions: dict[str, dict[str, Any]] = {}
        for row in (override_by_code.get(code) or {}).get("candidates") or []:
            if not isinstance(row, dict):
                raise ValueError(f"manual override for municipality {code} must be a mapping")
            override_status = str(row.get("status") or "candidate").strip().lower()
            if override_status not in MANUAL_OVERRIDE_STATUSES:
                raise ValueError(
                    f"manual override for municipality {code} has unsupported status={override_status!r}"
                )
            enriched = {
                **row,
                "status": override_status,
                "query": "official manual verification",
                "query_category_id": "integrated_reservation",
                "_manual_override": True,
                "_override_checked_at": override_document.get("checked_at"),
                "_override_source": override_document.get("source"),
            }
            if override_status == "excluded":
                raw_url = str(row.get("url") or "").strip()
                normalized_url = normalized_duplicate_url(raw_url)
                exclusion_reason = str(row.get("exclusion_reason") or "").strip().lower()
                if not normalized_url or candidate_exclusion_reason(raw_url):
                    raise ValueError(
                        f"manual exclusion for municipality {code} requires a safe canonical HTTP URL"
                    )
                if not MANUAL_EXCLUSION_REASON_PATTERN.fullmatch(exclusion_reason):
                    raise ValueError(
                        f"manual exclusion for municipality {code} requires a stable snake_case exclusion_reason"
                    )
                previous = manual_exclusions.get(normalized_url)
                if previous and previous.get("exclusion_reason") != exclusion_reason:
                    raise ValueError(
                        f"manual exclusions for municipality {code} disagree for {normalized_url}"
                    )
                enriched["exclusion_reason"] = exclusion_reason
                manual_exclusions[normalized_url] = enriched
            raw_candidates.append(enriched)
        eligible_keys: set[str] = set()
        exclusion_counts: Counter[str] = Counter()
        manual_exclusion_evidence: dict[str, dict[str, Any]] = {}
        for candidate in raw_candidates:
            raw_url = str(candidate.get("url") or "").strip()
            normalized_url = normalized_duplicate_url(raw_url)
            manual_exclusion = manual_exclusions.get(normalized_url)
            if manual_exclusion:
                exclusion_reason = str(manual_exclusion["exclusion_reason"])
                exclusion_counts[exclusion_reason] += 1
                related_disabled = disabled_owners.get(normalized_url) or []
                provider = str(manual_exclusion.get("provider") or "").strip().upper()
                evidence = {
                    "kind": "official_manual_exclusion",
                    "ownership_basis": "official_page_manual_verification",
                    "candidate_id": candidate_id(normalized_url),
                    "candidate_url": str(manual_exclusion.get("url") or ""),
                    "normalized_url": normalized_url,
                    "exclusion_reason": exclusion_reason,
                    "evidence_urls": list(manual_exclusion.get("evidence_urls") or []),
                    "evidence_note": str(manual_exclusion.get("evidence_note") or ""),
                    "checked_at": manual_exclusion.get("_override_checked_at"),
                    "source": manual_exclusion.get("_override_source"),
                    "disabled_owner_providers": sorted(
                        {
                            str(owner.get("provider") or "").strip().upper()
                            for owner in related_disabled
                            if owner.get("provider")
                        }
                    ),
                    "target_statuses": sorted(
                        {
                            str(owner.get("crawler_status") or owner.get("status") or "").strip().lower()
                            for owner in related_disabled
                            if owner.get("crawler_status") or owner.get("status")
                        }
                    ),
                }
                if provider:
                    evidence["provider"] = provider
                manual_exclusion_evidence[normalized_url] = evidence
                continue
            if int(candidate.get("score") or 0) < min_score:
                exclusion_counts["score_below_threshold"] += 1
                continue
            category_id = str(candidate.get("query_category_id") or "integrated_reservation")
            if category_id != "integrated_reservation":
                exclusion_counts["wrong_query_category"] += 1
                continue
            reason = candidate_exclusion_reason(raw_url)
            if reason:
                exclusion_counts[reason] += 1
                continue
            if not normalized_url:
                exclusion_counts["normalization_failed"] += 1
                continue
            eligible_keys.add(normalized_url)
            occurrences_by_url[normalized_url].append((municipality, candidate))
        municipality_stats[code] = {
            "candidate_count": len(raw_candidates),
            "eligible_candidate_count": len(eligible_keys),
            "excluded_candidate_count": sum(exclusion_counts.values()),
            "exclusion_reasons": dict(sorted(exclusion_counts.items())),
            "eligible_urls": eligible_keys,
            "owner_providers": set(),
            "yaml_owner_providers": set(),
            "review_candidate_ids": set(),
            "evidence": list(manual_exclusion_evidence.values()),
        }

    review_rows: list[dict[str, Any]] = []
    for normalized_url, occurrences in sorted(occurrences_by_url.items()):
        representative_municipality, representative = min(
            occurrences,
            key=lambda item: (
                -int(item[1].get("score") or 0),
                len(str(item[1].get("url") or "")),
                str(item[1].get("url") or ""),
            ),
        )
        raw_url = str(representative.get("url") or "").strip()
        active_owners = working_owners.get(normalized_url) or []
        proven_owners = [
            owner
            for owner in active_owners
            if owner_has_production_evidence(
                str(owner.get("provider") or "").strip().upper(),
                production_providers,
                production_evidence,
                normalized_url,
            )
        ]
        related_disabled = disabled_owners.get(normalized_url) or []
        municipalities_for_url = unique_municipalities(occurrences)
        explicit_providers = {
            str(candidate.get("provider") or "").strip().upper()
            for _municipality, candidate in occurrences
            if candidate.get("_manual_override")
            and str(candidate.get("provider") or "").strip()
        }
        if len(explicit_providers) > 1:
            raise ValueError(
                f"manual candidate providers disagree for {normalized_url}: "
                f"{sorted(explicit_providers)}"
            )
        explicit_provider = next(iter(explicit_providers), "")
        active_owner_providers = {
            str(owner.get("provider") or "").strip().upper()
            for owner in active_owners
            if str(owner.get("provider") or "").strip()
        }
        if explicit_provider and active_owners and active_owner_providers != {explicit_provider}:
            raise ValueError(
                f"manual candidate provider {explicit_provider} disagrees with active owner providers "
                f"{sorted(active_owner_providers)} for {normalized_url}"
            )

        for municipality, candidate in occurrences:
            stats = municipality_stats[str(municipality["code"])]
            if candidate.get("_manual_override"):
                evidence = {
                    "kind": "official_manual_candidate",
                    "ownership_basis": "official_page_manual_verification",
                    "candidate_url": str(candidate.get("url") or ""),
                    "normalized_url": normalized_url,
                    "evidence_urls": list(candidate.get("evidence_urls") or []),
                    "evidence_note": str(candidate.get("evidence_note") or ""),
                    "checked_at": candidate.get("_override_checked_at"),
                    "source": candidate.get("_override_source"),
                }
                if evidence not in stats["evidence"]:
                    stats["evidence"].append(evidence)
            for owner in active_owners:
                provider = str(owner.get("provider") or "").strip().upper()
                stats["yaml_owner_providers"].add(provider)
                evidence = ownership_evidence(
                    str(candidate.get("url") or ""),
                    normalized_url,
                    owner,
                    production_providers,
                    production_evidence,
                )
                if evidence not in stats["evidence"]:
                    stats["evidence"].append(evidence)
                if owner in proven_owners:
                    stats["owner_providers"].add(provider)
            for owner in related_disabled:
                evidence = {
                    "kind": "ignored_disabled_owner",
                    "ownership_basis": "disabled_target",
                    "provider": str(owner.get("provider") or "").strip().upper(),
                    "candidate_url": str(candidate.get("url") or ""),
                    "target_url": owner.get("_matched_target_url"),
                    "normalized_url": normalized_url,
                    "target_file": owner.get("_target_file"),
                    "target_index": owner.get("_target_index"),
                    "target_status": str(owner.get("crawler_status") or owner.get("status") or ""),
                }
                if evidence not in stats["evidence"]:
                    stats["evidence"].append(evidence)

        if proven_owners:
            continue

        review_id = candidate_id(normalized_url)
        existing_owner = active_owners[0] if active_owners else None
        provider = (
            str(existing_owner.get("provider") or "").strip().upper()
            if existing_owner
            else explicit_provider or stable_provider(normalized_url)
        )
        action = "schedule_existing" if existing_owner else "live_validate_new"
        for municipality in municipalities_for_url:
            municipality_stats[municipality["code"]]["review_candidate_ids"].add(review_id)
        review_rows.append(
            {
                "candidate_id": review_id,
                "provider": provider,
                "status": "review",
                "recommended_action": action,
                "url": raw_url,
                "normalized_url": normalized_url,
                "score": int(representative.get("score") or 0),
                "title": str(representative.get("title") or representative_municipality.get("full_name") or ""),
                "snippet": str(representative.get("snippet") or ""),
                "query": str(representative.get("query") or ""),
                "query_category_id": "integrated_reservation",
                "manual_override": bool(representative.get("_manual_override")),
                "official_evidence_urls": list(representative.get("evidence_urls") or []),
                "official_evidence_note": str(representative.get("evidence_note") or ""),
                "trust_tier": trust_tier(raw_url),
                "existing_owner_providers": [
                    str(owner.get("provider") or "").strip().upper() for owner in active_owners
                ],
                "disabled_owner_providers": sorted(
                    {
                        str(owner.get("provider") or "").strip().upper()
                        for owner in related_disabled
                        if owner.get("provider")
                    }
                ),
                "municipalities": municipalities_for_url,
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "source_group": "municipal_reservation",
                "operator_type": "지자체/공공기관",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
            }
        )

    # Exact target-to-municipality declarations are stronger than discovery
    # search results.  Project them as YAML ownership evidence without claiming
    # that data is active; production/operational evidence below remains the
    # authority for collected coverage status.
    for target_row in target_rows:
        provider = str(target_row.get("provider") or "").strip().upper()
        status = str(
            target_row.get("crawler_status") or target_row.get("status") or ""
        ).strip().lower()
        canonical_url = next(iter(target_urls(target_row)), "")
        normalized_url = normalized_duplicate_url(canonical_url)
        if (
            not provider
            or status not in WORKING_STATUSES
            or explicit_duplicate_reason(target_row)
            or not normalized_url
            or candidate_exclusion_reason(canonical_url)
        ):
            continue
        municipality_codes = explicitly_mapped_ops_target_codes(
            target_row,
            queue_by_code,
        )
        if not municipality_codes:
            continue
        owner = {
            **target_row,
            "_matched_target_url": canonical_url,
            "_canonical_target_url": canonical_url,
            "_ownership_match_kind": "exact_active_url",
        }
        evidence = ownership_evidence(
            canonical_url,
            normalized_url,
            owner,
            production_providers,
            production_evidence,
        )
        for code in municipality_codes:
            stats = municipality_stats[code]
            stats["yaml_owner_providers"].add(provider)
            if evidence not in stats["evidence"]:
                stats["evidence"].append(deepcopy(evidence))

    coverage_rows: list[dict[str, Any]] = []
    for municipality in municipalities:
        code = str(municipality["code"])
        stats = municipality_stats[code]
        if stats["owner_providers"]:
            status = "covered_by_existing"
        elif stats["review_candidate_ids"]:
            status = "review"
        else:
            status = "no_candidate"
        evidence = sorted(
            stats["evidence"],
            key=lambda row: (
                str(row.get("normalized_url") or ""),
                str(row.get("ownership_basis") or ""),
                str(row.get("provider") or ""),
            ),
        )
        coverage_rows.append(
            {
                "code": code,
                "sido": str(municipality.get("sido") or ""),
                "sigungu": str(municipality.get("sigungu") or ""),
                "full_name": str(municipality.get("full_name") or ""),
                "status": status,
                "candidate_count": stats["candidate_count"],
                "eligible_candidate_count": stats["eligible_candidate_count"],
                "excluded_candidate_count": stats["excluded_candidate_count"],
                "exclusion_reasons": stats["exclusion_reasons"],
                "owner_providers": sorted(stats["owner_providers"]),
                "promoted_providers": [],
                "yaml_owner_providers": sorted(stats["yaml_owner_providers"]),
                "review_candidate_ids": sorted(stats["review_candidate_ids"]),
                "evidence": evidence,
            }
        )

    generated_at = now_iso()
    coverage_summary = {
        "municipalities": len(coverage_rows),
        "by_status": dict(Counter(row["status"] for row in coverage_rows)),
        "candidate_results": sum(row["candidate_count"] for row in coverage_rows),
        "eligible_candidate_urls_by_municipality": sum(row["eligible_candidate_count"] for row in coverage_rows),
        "excluded_candidate_results": sum(row["excluded_candidate_count"] for row in coverage_rows),
        "unique_review_candidates": len(review_rows),
        "production_scheduled_providers": len(production_providers),
        "production_providers_with_active_course_evidence": sum(
            1 for row in production_evidence.values() if int(row.get("active_course_count") or 0) > 0
        ),
    }
    coverage_document = {
        "version": 1,
        "generated_at": generated_at,
        "source_queue": "config/municipal_course_search_targets.yaml",
        "source_candidates": "config/municipal_course_candidate_results.yaml",
        "source_target_dir": "config/crawl_targets",
        "production_provider_source": "config/production_crawler_providers.yaml",
        "production_evidence_source": "config/production_municipal_provider_evidence.yaml",
        "manual_override_source": "config/municipal_integrated_reservation_overrides.yaml",
        "operational_source": "config/municipal_integrated_reservation_operational.yaml",
        "ownership_rule": "exact normalized candidate or explicit Ops target URL + working target; production or operational evidence required for collected coverage",
        "summary": coverage_summary,
        "municipalities": coverage_rows,
    }
    review_document = {
        "version": 1,
        "generated_at": generated_at,
        "source_coverage": "config/municipal_integrated_reservation_coverage.yaml",
        "execution_policy": "review rows are not operational until live validation succeeds",
        "summary": {
            "candidates": len(review_rows),
            "by_action": dict(Counter(row["recommended_action"] for row in review_rows)),
            "by_trust_tier": dict(Counter(row["trust_tier"] for row in review_rows)),
            "municipalities": len(
                {municipality["code"] for row in review_rows for municipality in row["municipalities"]}
            ),
        },
        "candidates": review_rows,
    }
    apply_operational_entries_to_manifests(
        coverage_document,
        review_document,
        operational_entries(operational_document),
        target_rows=target_rows,
    )
    sync_live_validation_evidence_to_coverage(coverage_document, review_document)
    return coverage_document, review_document


def _selected_for_live_validation(
    row: dict[str, Any],
    municipality_filters: set[str] | None,
    candidate_filters: set[str] | None = None,
) -> bool:
    if candidate_filters and str(row.get("candidate_id") or "") not in candidate_filters:
        return False
    if not municipality_filters:
        return True
    for municipality in row.get("municipalities") or []:
        if str(municipality.get("code") or "") in municipality_filters:
            return True
        if str(municipality.get("full_name") or "") in municipality_filters:
            return True
    return False


def _live_validation_preference_key(row: dict[str, Any]) -> tuple[int, int, int, int, str, str]:
    url = str(row.get("url") or "")
    return (
        0 if row.get("manual_override") else 1,
        0 if str(row.get("trust_tier") or "") == "official_public_domain" else 1,
        -int(row.get("score") or 0),
        len(url),
        url,
        str(row.get("candidate_id") or ""),
    )


def _primary_municipality(
    row: dict[str, Any],
    municipality_filters: set[str] | None,
) -> dict[str, Any]:
    municipalities = [item for item in row.get("municipalities") or [] if isinstance(item, dict)]
    if municipality_filters:
        for municipality in municipalities:
            if str(municipality.get("code") or "") in municipality_filters:
                return municipality
            if str(municipality.get("full_name") or "") in municipality_filters:
                return municipality
    return municipalities[0] if municipalities else {}


def _existing_target_for_review(
    review_row: dict[str, Any],
    target_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    provider = str(review_row.get("provider") or "").strip().upper()
    normalized_url = str(review_row.get("normalized_url") or "")
    matches = [
        row
        for row in target_rows
        if str(row.get("provider") or "").strip().upper() == provider
        and normalized_url in {normalized_duplicate_url(url) for url in target_urls(row)}
    ]
    return matches[0] if matches else None


def live_validate_candidates(
    review_document: dict[str, Any],
    coverage_document: dict[str, Any],
    target_rows: list[dict[str, Any]],
    *,
    municipality_filters: set[str] | None = None,
    candidate_filters: set[str] | None = None,
    limit: int | None = None,
    timeout: int = 20,
    max_pages: int = 3,
    detail_limit: int = 20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from Crawler.Crawler_MunicipalYaml import CrawlTarget, collect_from_url

    selected = [
        row
        for row in review_document.get("candidates") or []
        if isinstance(row, dict) and _selected_for_live_validation(row, municipality_filters, candidate_filters)
    ]
    selected.sort(key=_live_validation_preference_key)
    if limit is not None:
        selected = selected[:limit]
    coverage_by_code = {
        str(row.get("code") or ""): row
        for row in coverage_document.get("municipalities") or []
        if isinstance(row, dict)
    }
    promoted_targets: list[dict[str, Any]] = []
    operational_additions: list[dict[str, Any]] = []

    for review_row in selected:
        primary = _primary_municipality(review_row, municipality_filters)
        primary_municipalities = [dict(primary)] if primary else []
        existing = _existing_target_for_review(review_row, target_rows)
        validated_municipalities = primary_municipalities
        review_municipalities = [
            dict(item)
            for item in review_row.get("municipalities") or []
            if isinstance(item, dict) and str(item.get("code") or "")
        ]
        if not municipality_filters and review_row.get("manual_override") and existing:
            configured_codes = {
                str(item.get("code") or "")
                for item in existing.get("covered_municipalities") or []
                if isinstance(item, dict) and str(item.get("code") or "")
            }
            review_codes = {str(item.get("code") or "") for item in review_municipalities}
            if review_codes and review_codes.issubset(configured_codes):
                validated_municipalities = review_municipalities
        target_extra = (
            dict(existing)
            if existing
            else {
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "source_group": "municipal_reservation",
                "operator_type": "지자체/공공기관",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "municipality_code": primary.get("code"),
                "municipality_full_name": primary.get("full_name"),
            }
        )
        target = CrawlTarget(
            provider=str(review_row["provider"]),
            name=str(review_row.get("title") or primary.get("full_name") or review_row["provider"]),
            branch=str(primary.get("full_name") or review_row.get("title") or review_row["provider"]),
            url=str(review_row["url"]),
            source="municipal_integrated_reservation_promotion",
            priority=2,
            region=str(primary.get("sido") or ""),
            extra=target_extra,
        )
        checked_at = now_iso()
        validation: dict[str, Any] = {
            "checked_at": checked_at,
            "parser": "",
            "row_count": 0,
            "no_current_data": False,
            "error": "",
        }
        try:
            rows, parser, meta = collect_from_url(
                target,
                timeout=timeout,
                max_depth=1,
                max_pages=max_pages,
                detail_limit=detail_limit,
            )
            semantic_quality = semantic_live_validation_quality(rows)
            row_count = int(semantic_quality["accepted_row_count"])
            no_current_data = bool(meta.get("no_current_data")) and not semantic_quality["raw_row_count"]
            collection_error = " ".join(
                str(meta.get("configured_collection_error") or "").split()
            )[:1000]
            validation.update(
                {
                    "parser": str(parser or ""),
                    "row_count": row_count,
                    "no_current_data": no_current_data,
                    "raw_row_count": int(semantic_quality["raw_row_count"]),
                    "semantic_rejected_row_count": int(semantic_quality["rejected_row_count"]),
                    "semantic_rejection_reasons": semantic_quality["rejection_reasons"],
                    "semantic_rejected_title_samples": semantic_quality["rejected_title_samples"],
                    "semantic_quality_passed": bool(row_count > 0 or no_current_data),
                    "pages": int(meta.get("pages") or 0),
                    "detail_pages": int(meta.get("detail_pages") or 0),
                    "error": collection_error,
                }
            )
            if row_count > 0:
                review_row["status"] = "validated"
            elif no_current_data:
                review_row["status"] = "no_current_data"
            else:
                review_row["status"] = "needs_parser"
        except Exception as exc:
            validation["error"] = f"{type(exc).__name__}: {' '.join(str(exc).split())}"[:1000]
            review_row["status"] = "review"
        review_row["live_validation"] = validation

        evidence = {
            "kind": "live_validation",
            "ownership_basis": "live_crawl_probe",
            "candidate_id": review_row["candidate_id"],
            "provider": review_row["provider"],
            "candidate_url": review_row["url"],
            "normalized_url": review_row["normalized_url"],
            **validation,
        }
        for municipality in validated_municipalities:
            coverage_row = coverage_by_code.get(str(municipality.get("code") or ""))
            if coverage_row:
                coverage_row.setdefault("evidence", []).append(dict(evidence))

        if urlparse(str(review_row["url"])).scheme.lower() != "https":
            if validation["row_count"] > 0 or validation["no_current_data"]:
                review_row["status"] = "needs_https_canonical"
            continue
        if not validation["row_count"] and not validation["no_current_data"]:
            continue

        action = str(review_row.get("recommended_action") or "")
        target_url_value = str(review_row["url"])
        if existing:
            target_url_value = next(
                (
                    raw_url
                    for raw_url in target_urls(existing)
                    if normalized_duplicate_url(raw_url) == str(review_row["normalized_url"])
                ),
                target_url_value,
            )
        outcome = "collected" if validation["row_count"] > 0 else "no_current_data"
        operational_additions.append(
            {
                "provider": str(review_row["provider"]),
                "normalized_url": str(review_row["normalized_url"]),
                "target_url": target_url_value,
                "action": action,
                "validation_outcome": outcome,
                "validated_at": checked_at,
                "parser": str(validation["parser"]),
                "row_count": int(validation["row_count"]),
                "no_current_data": bool(validation["no_current_data"]),
                "municipalities": validated_municipalities,
            }
        )
        if action != "live_validate_new":
            review_row["status"] = "validated"
            continue

        validation_parser = str(validation.get("parser") or "").strip()
        parser_parts = {part.strip() for part in validation_parser.split("+") if part.strip()}
        specialized_ready = (
            bool(parser_parts)
            and parser_parts != {"none"}
            and not bool(validation.get("no_current_data"))
            and not any(part.startswith("generic_") for part in parser_parts)
        )
        promoted = {
            "provider": review_row["provider"],
            "name": str(review_row.get("title") or primary.get("full_name") or review_row["provider"]),
            "branch": str(primary.get("full_name") or review_row.get("title") or review_row["provider"]),
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": validation_parser if specialized_ready else "generic_auto_discovery",
            "crawler_status": "ready" if specialized_ready else "partial",
            "priority": 2,
            "url": review_row["url"],
            "source": "municipal_integrated_reservation_promotion",
            "origin": "live_validated",
            "municipality_code": str(primary.get("code") or ""),
            "municipality_full_name": str(primary.get("full_name") or ""),
            "covered_municipalities": validated_municipalities,
            "last_quality": {
                "collected": validation["row_count"],
                "parser": validation["parser"],
                "error_kind": "",
                "checked_at": checked_at,
                "no_current_data": bool(validation["no_current_data"]),
            },
        }
        promoted_targets.append(promoted)
        review_row["status"] = "validated"

    _refresh_manifest_summaries(coverage_document, review_document)
    coverage_document["summary"]["live_validated_candidates"] = len(selected)
    coverage_document["summary"]["promoted_targets"] = len(promoted_targets)
    coverage_document["summary"]["operational_additions"] = len(operational_additions)
    sync_live_validation_evidence_to_coverage(coverage_document, review_document)
    return promoted_targets, operational_additions


def _target_document(targets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 1,
        "generated_at": now_iso(),
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "summary": {
            "targets": len(targets),
            "by_status": dict(Counter(str(row.get("crawler_status") or row.get("status") or "") for row in targets)),
            "by_service_group": dict(Counter(str(row.get("service_group") or "") for row in targets if row.get("service_group"))),
            "by_collection_type": dict(Counter(str(row.get("collection_type") or "") for row in targets)),
        },
        "targets": targets,
    }


def merge_promoted_targets(path: Path, promoted_targets: list[dict[str, Any]], all_target_rows: list[dict[str, Any]]) -> bool:
    if not promoted_targets:
        return False
    existing_document = load_yaml(path)
    existing_rows = [row for row in existing_document.get("targets") or [] if isinstance(row, dict)]
    by_url = {normalized_duplicate_url(row.get("url")): dict(row) for row in existing_rows if row.get("url")}
    # Disabled rows are historical evidence, not live ownership.  A provider can
    # therefore be retargeted from a deprecated/blocked URL to a newly verified
    # canonical ledger without deleting the old audit row.  Keep the collision
    # guard strict for every active, non-duplicate owner.
    provider_scopes: dict[str, set[str]] = defaultdict(set)
    for row in all_target_rows:
        status = str(row.get("crawler_status") or row.get("status") or "").strip().lower()
        if status not in WORKING_STATUSES or explicit_duplicate_reason(row):
            continue
        provider = str(row.get("provider") or "").strip().upper()
        provider_scopes[provider].update(
            normalized_duplicate_url(url)
            for url in target_urls(row)
            if not candidate_exclusion_reason(url)
        )
    for row in promoted_targets:
        key = normalized_duplicate_url(row["url"])
        provider = str(row["provider"]).upper()
        foreign_scopes = {scope for scope in provider_scopes.get(provider, set()) if scope and scope != key}
        if foreign_scopes and key not in provider_scopes.get(provider, set()):
            raise ValueError(f"provider collision for {provider}: {key}")
        previous = by_url.get(key) or {}
        covered: dict[str, dict[str, Any]] = {}
        for municipality in list(previous.get("covered_municipalities") or []) + list(row.get("covered_municipalities") or []):
            if isinstance(municipality, dict) and str(municipality.get("code") or ""):
                covered[str(municipality["code"])] = municipality
        if previous:
            row = {
                **row,
                "municipality_code": previous.get("municipality_code") or row.get("municipality_code"),
                "municipality_full_name": previous.get("municipality_full_name") or row.get("municipality_full_name"),
                "covered_municipalities": list(covered.values()),
            }
        by_url[key] = row
    merged = sorted(by_url.values(), key=lambda row: (int(row.get("priority") or 9), str(row.get("provider") or "")))
    write_yaml(path, _target_document(merged))
    return True


def rebuild_target_index(target_dir: Path) -> None:
    file_entries: list[dict[str, Any]] = []
    raw_rows: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(target_dir.glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        document = load_yaml(path)
        rows = [row for row in document.get("targets") or [] if isinstance(row, dict)]
        expected_summary = {
            "targets": len(rows),
            "by_status": dict(
                Counter(str(row.get("crawler_status") or row.get("status") or "") for row in rows)
            ),
            "by_service_group": dict(
                Counter(str(row.get("service_group") or "") for row in rows if row.get("service_group"))
            ),
            "by_collection_type": dict(
                Counter(str(row.get("collection_type") or "") for row in rows)
            ),
        }
        if document.get("summary") != expected_summary:
            document["generated_at"] = now_iso()
            document["summary"] = expected_summary
            write_yaml(path, document)
        file_entries.append(
            {
                "domain_category": document.get("domain_category"),
                "source_group": document.get("source_group"),
                "file": path.name,
                "targets": len(rows),
            }
        )
        raw_rows.extend((path.name, row) for row in rows)
    category_by_file = {row["file"]: row["domain_category"] for row in file_entries}
    index = {
        "version": 1,
        "generated_at": now_iso(),
        "summary": {
            "targets": len(raw_rows),
            "by_category": dict(Counter(category_by_file[file_name] for file_name, _row in raw_rows)),
            "by_status": dict(
                Counter(str(row.get("crawler_status") or row.get("status") or "") for _file, row in raw_rows)
            ),
            "by_collection_type": dict(
                Counter(str(row.get("collection_type") or "") for _file, row in raw_rows)
            ),
            "by_service_group": dict(
                Counter(str(row.get("service_group") or "") for _file, row in raw_rows if row.get("service_group"))
            ),
            "by_origin": dict(Counter(str(row.get("origin") or "") for _file, row in raw_rows)),
        },
        "files": file_entries,
    }
    write_yaml(target_dir / "index.yaml", index)


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build canonical ownership and review manifests for municipal integrated-reservation candidates."
    )
    parser.add_argument("--queue", default=str(QUEUE))
    parser.add_argument("--candidates", default=str(CANDIDATES))
    parser.add_argument("--target-dir", default=str(TARGET_DIR))
    parser.add_argument("--production-providers", default=str(PRODUCTION_PROVIDERS))
    parser.add_argument("--production-evidence", default=str(PRODUCTION_EVIDENCE))
    parser.add_argument("--overrides", default=str(MANUAL_OVERRIDES))
    parser.add_argument("--coverage-out", default=str(COVERAGE_OUT))
    parser.add_argument("--review-out", default=str(REVIEW_OUT))
    parser.add_argument("--operational-out", default=str(OPERATIONAL_OUT))
    parser.add_argument("--target-out", default=str(TARGET_OUT))
    parser.add_argument("--min-score", type=int, default=8)
    parser.add_argument("--live-validate", action="store_true")
    parser.add_argument("--municipality", action="append", help="Live-validate only this code or exact full name.")
    parser.add_argument("--candidate-id", action="append", help="Live-validate only this exact candidate id. Repeatable.")
    parser.add_argument("--limit", type=int, help="Maximum review URLs to live-validate.")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--detail-limit", type=int, default=20)
    parser.add_argument(
        "--no-write-targets",
        action="store_true",
        help="Dry probe: write coverage/review reports, but do not change operational allowlist or crawl targets.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queue_path = resolve_path(args.queue)
    candidate_path = resolve_path(args.candidates)
    target_dir = resolve_path(args.target_dir)
    production_provider_path = resolve_path(args.production_providers)
    production_evidence_path = resolve_path(args.production_evidence)
    override_path = resolve_path(args.overrides)
    coverage_path = resolve_path(args.coverage_out)
    review_path = resolve_path(args.review_out)
    operational_path = resolve_path(args.operational_out)
    target_path = resolve_path(args.target_out)

    existing_review_document = load_yaml(review_path) if review_path.exists() else None
    target_rows = load_target_rows(target_dir)
    production_providers = load_production_providers(production_provider_path)
    production_evidence = load_production_evidence(production_evidence_path)
    coverage_document, review_document = build_manifests(
        load_yaml(queue_path),
        load_yaml(candidate_path),
        target_rows,
        production_providers,
        production_evidence,
        load_yaml(override_path),
        load_yaml(operational_path),
        min_score=args.min_score,
    )
    preserve_live_validations(review_document, existing_review_document)

    promoted_targets: list[dict[str, Any]] = []
    operational_additions: list[dict[str, Any]] = []
    if args.live_validate:
        promoted_targets, operational_additions = live_validate_candidates(
            review_document,
            coverage_document,
            target_rows,
            municipality_filters=set(args.municipality or []) or None,
            candidate_filters=set(args.candidate_id or []) or None,
            limit=args.limit,
            timeout=args.timeout,
            max_pages=args.max_pages,
            detail_limit=args.detail_limit,
        )
        if not args.no_write_targets:
            target_changed = merge_promoted_targets(target_path, promoted_targets, target_rows)
            merge_operational_entries(operational_path, operational_additions)
            apply_operational_entries_to_manifests(coverage_document, review_document, operational_additions)
            if target_changed:
                rebuild_target_index(target_dir)

    sync_live_validation_evidence_to_coverage(coverage_document, review_document)
    write_yaml(coverage_path, coverage_document)
    write_yaml(review_path, review_document)
    print(f"coverage={coverage_path}")
    print(f"review={review_path}")
    print(f"municipalities={coverage_document['summary']['municipalities']}")
    print(f"by_status={coverage_document['summary']['by_status']}")
    print(f"review_candidates={review_document['summary']['candidates']}")
    if args.live_validate:
        print(f"promoted_targets={len(promoted_targets)}")
        print(f"operational_additions={len(operational_additions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
