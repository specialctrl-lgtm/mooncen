from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_QUEUE = ROOT / "config" / "municipal_course_search_targets.yaml"
DEFAULT_COVERAGE = ROOT / "config" / "municipal_integrated_reservation_coverage.yaml"
DEFAULT_TARGET_DIR = ROOT / "config" / "crawl_targets"
DEFAULT_PRODUCTION_PROVIDERS = ROOT / "config" / "production_crawler_providers.yaml"
DEFAULT_PRODUCTION_EVIDENCE = ROOT / "config" / "production_municipal_provider_evidence.yaml"
DEFAULT_OPERATIONAL = ROOT / "config" / "municipal_integrated_reservation_operational.yaml"
EXPECTED_MUNICIPALITIES = 269

ALLOWED_COVERAGE_STATUSES = {
    "covered_by_existing",
    "covered_by_parent",
    "promoted",
    "review",
    "no_candidate",
}
COMPLETE_COVERAGE_STATUSES = {
    "covered_by_existing",
    "covered_by_parent",
    "promoted",
}
WORKING_TARGET_STATUSES = {
    "ready",
    "partial",
    "candidate",
    "generated",
    "no_current_data",
}
ALLOWED_OPERATIONAL_ACTIONS = {"schedule_existing", "live_validate_new"}
ALLOWED_OPERATIONAL_OUTCOMES = {"collected", "no_current_data"}
AUTOMATIC_CANDIDATE_EXCLUSION_REASONS = {
    "credential_bearing_url",
    "excluded_url_shape",
    "invalid_http_url",
    "low_value_domain",
    "media_domain",
    "normalization_failed",
    "score_below_threshold",
    "sensitive_query_url",
    "session_bearing_url",
    "url_too_long",
    "wrong_query_category",
}
MANUAL_EXCLUSION_REASON_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,63}\Z")
PROVIDER_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9_]{1,49}")
DEDICATED_PROVIDER_NAMES = {
    "BABSANG_WELFARE_PROGRAM",
    "HOMEPLUS",
    "EMART",
    "LOTTE",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
    "BUSAN_RESERVATION",
    "SAHASILVER_COURSE",
    "SEOSAN_WELFARE_TOTAL_RESERVATION",
    "SEONGNAM_BAEUMSOOP",
    "ANYANG_LIFELONG_LEARNING",
    "YONGIN_LIFELONG_LEARNING",
    "ESONGPA_SPORTS_CULTURE",
    "SEOUL_PUBLIC_SERVICE",
}

DUPLICATE_QUERY_DROP_PARAMS = {
    "_",
    "callback",
    "currentpage",
    "currentpageno",
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

EXCLUDED_URL_DOMAIN_TOKENS = ("e-ncom.co.kr",)
EXCLUDED_URL_MEDIA_DOMAINS = (
    "asiatoday.co.kr",
    "boeuni.com",
    "brcity.kr",
    "cctimes.kr",
    "cfnews.kr",
    "domin.co.kr",
    "elovejc.kr",
    "ggilbo.com",
    "gndomin.com",
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
    "kyongbuk.co.kr",
    "mygoyang.com",
    "newsfire.co.kr",
    "pointe.co.kr",
    "seoulilbo.com",
    "todayan.com",
    "welfarehello.com",
    "yangsanilbo.com",
    "yg21.co.kr",
    "yongin21.co.kr",
    "zsick.com",
)
EXCLUDED_URL_PATH_TOKENS = (
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
    "common/bbs/selectbbsdetail",
    "/board/view.",
    "/board/view/",
    "board/download",
    "/download.do",
    "doviewboarditem",
    "bbs/board.php?bo_table=notice",
    "/media/board/",
    "boardlist.do?boardid=",
    "cmmboardview.do",
    "selecteminwonnewsview.do",
    "notice?idx=",
    "mode=view",
    "articleseq=",
    "nttid=",
    "ntatcseq=",
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


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return data


def normalized_scope_url(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.netloc:
        return ""
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in DUPLICATE_QUERY_DROP_PARAMS
        ),
        doseq=True,
    )
    return urlunparse((scheme, netloc, path, "", query, ""))


def target_urls(target: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("url", "list_url", "base_url"):
        value = clean_text(target.get(key))
        if value:
            values.append(value)
    for value in target.get("list_urls") or []:
        if clean_text(value):
            values.append(clean_text(value))
    return list(dict.fromkeys(values))


def ownership_aliases(target: dict[str, Any]) -> list[str]:
    raw = target.get("ownership_aliases") or []
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(clean_text(value) for value in raw if clean_text(value)))


def target_ownership_urls(target: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys([*target_urls(target), *ownership_aliases(target)]))


def target_scope_keys(target: dict[str, Any]) -> list[str]:
    explicit = clean_text(target.get("crawl_scope") or target.get("collection_scope"))
    if explicit:
        return [f"scope:{explicit.lower()}"]
    return list(
        dict.fromkeys(
            normalized
            for normalized in (normalized_scope_url(value) for value in target_urls(target))
            if normalized
        )
    )


def explicit_duplicate_reason(target: dict[str, Any]) -> str:
    duplicate_of = clean_text(target.get("duplicate_of"))
    if duplicate_of:
        return f"duplicate_of:{duplicate_of}"
    blocked_reason = clean_text(target.get("blocked_reason"))
    if blocked_reason.lower().startswith("duplicate_of:"):
        return blocked_reason
    quality = target.get("last_quality") if isinstance(target.get("last_quality"), dict) else {}
    error_kind = clean_text(quality.get("error_kind"))
    if error_kind.lower().startswith("duplicate_of:"):
        return error_kind
    if clean_text(target.get("collection_type")).lower() == "duplicate":
        return "duplicate_collection_type"
    status = clean_text(target.get("crawler_status") or target.get("status"))
    if status.lower().startswith("duplicate_url:"):
        return status
    return ""


def target_url_is_excluded(value: str) -> bool:
    lowered = value.lower()
    return (
        any(token in lowered for token in EXCLUDED_URL_DOMAIN_TOKENS)
        or any(token in lowered for token in EXCLUDED_URL_MEDIA_DOMAINS)
        or any(token in lowered for token in EXCLUDED_URL_PATH_TOKENS)
    )


def is_working_target(target: dict[str, Any]) -> bool:
    status = clean_text(target.get("crawler_status") or target.get("status")).lower()
    main_url = clean_text(target.get("url") or target.get("list_url") or target.get("base_url"))
    return (
        target.get("enabled") is not False
        and clean_text(target.get("manual_action")).lower() != "delete"
        and status in WORKING_TARGET_STATUSES
        and bool(main_url)
        and not explicit_duplicate_reason(target)
        and not target_url_is_excluded(main_url)
    )


def load_target_rows(target_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not target_dir.exists():
        return rows
    for path in sorted(target_dir.glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        data = load_yaml(path)
        defaults = {
            key: data.get(key)
            for key in ("collection_category", "domain_category", "source_group", "operator_type", "service_group")
            if data.get(key) not in (None, "")
        }
        targets = data.get("targets") or []
        if not isinstance(targets, list):
            raise ValueError(f"{path}: targets must be a list")
        for index, target in enumerate(targets, start=1):
            if not isinstance(target, dict):
                raise ValueError(f"{path}:{index}: target must be a mapping")
            rows.append({**defaults, **target, "_target_file": path.name, "_target_index": index})
    return rows


def working_target_indexes(
    rows: Iterable[dict[str, Any]],
    production_providers: set[str] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], list[tuple[str, list[dict[str, Any]]]]]:
    by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_url: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for target in rows:
        if not is_working_target(target):
            continue
        provider = clean_text(target.get("provider")).upper()
        if not provider:
            continue
        # Dedicated providers are intentionally excluded from the generated
        # YAML registry.  They are executable here only when the production
        # scheduler snapshot proves that their standalone command is enabled.
        if provider in DEDICATED_PROVIDER_NAMES and provider not in (production_providers or set()):
            continue
        by_provider[provider].append(target)
        for value in target_ownership_urls(target):
            normalized = normalized_scope_url(value)
            if normalized:
                by_url[normalized].append(target)
        for scope in target_scope_keys(target):
            by_scope[scope].append(target)
    duplicates = [(scope, owners) for scope, owners in by_scope.items() if len(owners) > 1]
    return dict(by_provider), dict(by_url), duplicates


def validate_ownership_alias_contract(
    rows: Iterable[dict[str, Any]],
    production_providers: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    claims: dict[str, list[dict[str, Any]]] = defaultdict(list)
    target_rows = list(rows)
    scheduled = production_providers or set()

    def eligible(target: dict[str, Any]) -> bool:
        provider = clean_text(target.get("provider")).upper()
        return is_working_target(target) and (
            provider not in DEDICATED_PROVIDER_NAMES or provider in scheduled
        )

    for target in target_rows:
        if not eligible(target):
            continue
        for value in target_urls(target):
            normalized = normalized_scope_url(value)
            if normalized:
                claims[normalized].append(target)
    for target in target_rows:
        if "ownership_aliases" not in target:
            continue
        provider = clean_text(target.get("provider")).upper() or "<missing>"
        label = f"target ownership aliases:{provider}"
        raw_aliases = target.get("ownership_aliases")
        if not isinstance(raw_aliases, list):
            errors.append(f"{label}: ownership_aliases must be a list")
            continue
        normalized_values: list[str] = []
        for index, raw_value in enumerate(raw_aliases, start=1):
            value = clean_text(raw_value)
            normalized = normalized_scope_url(value)
            if not value or not normalized:
                errors.append(f"{label}:{index}: alias must be an absolute HTTP(S) URL")
                continue
            if target_url_is_excluded(value):
                errors.append(f"{label}:{index}: alias uses an excluded URL shape")
                continue
            normalized_values.append(normalized)
            if eligible(target):
                claims[normalized].append(target)
        duplicates = sorted(value for value, count in Counter(normalized_values).items() if count > 1)
        if duplicates:
            errors.append(f"{label}: duplicate normalized aliases {duplicates}")
    for normalized, owners in sorted(claims.items()):
        providers = sorted({clean_text(owner.get("provider")).upper() for owner in owners})
        if len(providers) > 1:
            errors.append(f"target ownership aliases: {normalized} is claimed by multiple providers {providers}")
    return errors


def load_production_providers(path: Path) -> tuple[set[str], list[str]]:
    errors: list[str] = []
    if not path.exists():
        return set(), [f"production providers: missing file {path}"]
    data = load_yaml(path)
    if data.get("version") != 1:
        errors.append("production providers: version must be 1")
    if not clean_text(data.get("captured_at")):
        errors.append("production providers: missing captured_at")
    if not clean_text(data.get("source")):
        errors.append("production providers: missing source")
    raw = data.get("providers") or []
    if not isinstance(raw, list):
        return set(), errors + ["production providers: providers must be a list"]
    providers = [clean_text(value).upper() for value in raw if clean_text(value)]
    duplicates = sorted(provider for provider, count in Counter(providers).items() if count > 1)
    if duplicates:
        errors.append(f"production providers: duplicate providers {duplicates[:10]}")
    return set(providers), errors


def expand_aggregate_production_providers(
    scheduled_providers: set[str],
    *,
    target_rows: Iterable[dict[str, Any]],
    operational_path: Path,
) -> tuple[set[str], list[str]]:
    """Resolve scheduler macros to the concrete providers they own.

    ``production_crawler_providers.yaml`` mirrors the literal worker schedule.
    Aggregate entries persist rows for many concrete providers, so coverage and
    production-evidence checks must compare against the same ownership view as
    ``run_crawlers.build_course_provider_owners`` rather than treating a macro
    token as if it were a course provider.
    """
    effective = set(scheduled_providers)
    errors: list[str] = []
    municipal_owners: set[str] = set()

    if "MUNICIPAL_RESERVATION_TARGETS" in scheduled_providers:
        try:
            operational = load_yaml(operational_path)
            entries = operational.get("entries") or []
            if not isinstance(entries, list):
                errors.append("production providers: municipal aggregate entries must be a list")
                entries = []
            for index, entry in enumerate(entries, start=1):
                if not isinstance(entry, dict):
                    continue
                provider = clean_text(entry.get("provider")).upper()
                if not provider or not PROVIDER_PATTERN.fullmatch(provider):
                    errors.append(
                        "production providers: municipal aggregate has invalid provider "
                        f"at entry {index}"
                    )
                    continue
                municipal_owners.add(provider)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            errors.append(f"production providers: municipal aggregate expansion failed: {exc}")
        effective.update(municipal_owners)

    if "EXPERIENCE_TARGETS" in scheduled_providers:
        try:
            from Crawler.Crawler_EducationExperience import is_experience_target

            for target in target_rows:
                provider = clean_text(target.get("provider")).upper()
                if (
                    provider
                    and provider not in scheduled_providers
                    and provider not in municipal_owners
                    and is_working_target(target)
                    and is_experience_target(target)
                ):
                    effective.add(provider)
        except (ImportError, OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
            errors.append(f"production providers: experience aggregate expansion failed: {exc}")

    return effective, errors


def load_production_evidence(path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    if not path.exists():
        return {}, [f"production evidence: missing file {path}"]
    data = load_yaml(path)
    if data.get("version") != 1:
        errors.append("production evidence: version must be 1")
    if clean_text(data.get("scope")).lower() != "education":
        errors.append("production evidence: scope must be education")
    if data.get("include_inactive") is not False:
        errors.append("production evidence: include_inactive must be false")
    if not clean_text(data.get("checked_at")):
        errors.append("production evidence: missing checked_at")
    raw = data.get("providers") or []
    if not isinstance(raw, list):
        return {}, errors + ["production evidence: providers must be a list"]
    rows: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(raw, start=1):
        if not isinstance(row, dict):
            errors.append(f"production evidence:{index}: provider row must be a mapping")
            continue
        provider = clean_text(row.get("provider")).upper()
        count = row.get("active_course_count")
        if not provider:
            errors.append(f"production evidence:{index}: missing provider")
            continue
        if provider in rows:
            errors.append(f"production evidence: duplicate provider {provider}")
            continue
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            errors.append(f"production evidence:{provider}: active_course_count must be a non-negative integer")
            continue
        branch_count = row.get("active_branch_count")
        if branch_count is not None and (
            isinstance(branch_count, bool) or not isinstance(branch_count, int) or branch_count < 0
        ):
            errors.append(f"production evidence:{provider}: active_branch_count must be a non-negative integer")
        rows[provider] = row
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    expected_courses = sum(int(row.get("active_course_count") or 0) for row in rows.values())
    expected_active_providers = sum(1 for row in rows.values() if int(row.get("active_course_count") or 0) > 0)
    if summary.get("queried_providers") is not None and summary.get("queried_providers") != len(rows):
        errors.append(
            f"production evidence summary: queried_providers={summary.get('queried_providers')} actual={len(rows)}"
        )
    if summary.get("active_courses") is not None and summary.get("active_courses") != expected_courses:
        errors.append(
            f"production evidence summary: active_courses={summary.get('active_courses')} actual={expected_courses}"
        )
    if (
        summary.get("providers_with_active_courses") is not None
        and summary.get("providers_with_active_courses") != expected_active_providers
    ):
        errors.append(
            "production evidence summary: providers_with_active_courses="
            f"{summary.get('providers_with_active_courses')} actual={expected_active_providers}"
        )
    return rows, errors


def promoted_providers(row: dict[str, Any]) -> list[str]:
    raw = row.get("promoted_providers")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raw = []
    return list(dict.fromkeys(clean_text(value).upper() for value in raw if clean_text(value)))


def is_locked_education_target(target: dict[str, Any]) -> bool:
    return (
        clean_text(target.get("service_group")) == "공공강좌"
        and clean_text(target.get("service_group_policy")).lower() == "locked"
        and clean_text(target.get("collection_category")) == "공공예약"
        and clean_text(target.get("domain_category")) == "교육·강좌"
    )


def is_locked_live_validate_target(target: dict[str, Any]) -> bool:
    if clean_text(target.get("service_group_policy")).lower() != "locked":
        return False
    category = (
        clean_text(target.get("collection_category")),
        clean_text(target.get("domain_category")),
        clean_text(target.get("service_group")),
    )
    return category in {
        ("공공예약", "교육·강좌", "공공강좌"),
        ("공공예약", "체험·견학", "체험"),
    }


def valid_iso_datetime(value: Any) -> bool:
    text = clean_text(value)
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_operational_manifest(
    path: Path,
    *,
    official_by_code: dict[str, dict[str, Any]],
    working_by_url: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    errors: list[str] = []
    summary: dict[str, Any] = {
        "operational_entries": 0,
        "operational_bound_entries": 0,
        "operational_municipalities": 0,
        "operational_scope_duplicates": 0,
        "operational_by_action": {},
        "operational_by_outcome": {},
    }
    if not path.exists():
        return [], [f"operational: missing file {path}"], summary
    data = load_yaml(path)
    if data.get("version") != 1:
        errors.append("operational: version must be 1")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        return [], errors + ["operational: entries must be a list"], summary

    required_fields = {
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
    entries: list[dict[str, Any]] = []
    pair_owners: dict[tuple[str, str], list[int]] = defaultdict(list)
    scope_owners: dict[str, list[int]] = defaultdict(list)
    action_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    municipality_codes: set[str] = set()

    for index, raw_entry in enumerate(raw_entries, start=1):
        label = f"operational:{index}"
        if not isinstance(raw_entry, dict):
            errors.append(f"{label}: entry must be a mapping")
            continue
        entry_error_count = len(errors)
        missing = sorted(field for field in required_fields if field not in raw_entry)
        if missing:
            errors.append(f"{label}: missing required fields {missing}")
        entry = dict(raw_entry)
        provider = clean_text(entry.get("provider")).upper()
        normalized = clean_text(entry.get("normalized_url"))
        target_url = clean_text(entry.get("target_url"))
        action = clean_text(entry.get("action")).lower()
        outcome = clean_text(entry.get("validation_outcome")).lower()
        parser = clean_text(entry.get("parser"))
        row_count = entry.get("row_count")
        no_current_data = entry.get("no_current_data")

        if not PROVIDER_PATTERN.fullmatch(provider):
            errors.append(f"{label}: invalid provider={provider!r}")
        expected_normalized = normalized_scope_url(target_url)
        parsed_target_url = urlparse(target_url)
        if (
            parsed_target_url.scheme.lower() not in {"http", "https"}
            or not parsed_target_url.netloc
            or not expected_normalized
        ):
            errors.append(f"{label}: target_url must be an absolute HTTP(S) URL")
        elif normalized != expected_normalized:
            errors.append(
                f"{label}: normalized_url mismatch expected={expected_normalized!r} actual={normalized!r}"
            )
        if action not in ALLOWED_OPERATIONAL_ACTIONS:
            errors.append(f"{label}: unsupported action={action!r}")
        if outcome not in ALLOWED_OPERATIONAL_OUTCOMES:
            errors.append(f"{label}: unsupported validation_outcome={outcome!r}")
        if not valid_iso_datetime(entry.get("validated_at")):
            errors.append(f"{label}: validated_at must be an ISO-8601 datetime with timezone")
        if not parser:
            errors.append(f"{label}: parser must be a non-empty string")
        if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
            errors.append(f"{label}: row_count must be a non-negative integer")
        if not isinstance(no_current_data, bool):
            errors.append(f"{label}: no_current_data must be a boolean")
        if outcome == "collected" and not (
            isinstance(row_count, int)
            and not isinstance(row_count, bool)
            and row_count > 0
            and no_current_data is False
        ):
            errors.append(f"{label}: collected requires row_count>0 and no_current_data=false")
        if outcome == "no_current_data" and not (
            row_count == 0 and not isinstance(row_count, bool) and no_current_data is True
        ):
            errors.append(f"{label}: no_current_data outcome requires row_count=0 and no_current_data=true")

        municipalities = entry.get("municipalities")
        valid_municipalities: list[dict[str, Any]] = []
        if not isinstance(municipalities, list) or not municipalities:
            errors.append(f"{label}: municipalities must be a non-empty list")
            municipalities = []
        seen_entry_codes: set[str] = set()
        for municipality_index, municipality in enumerate(municipalities, start=1):
            municipality_label = f"{label}:municipality:{municipality_index}"
            if not isinstance(municipality, dict):
                errors.append(f"{municipality_label}: municipality must be a mapping")
                continue
            code = clean_text(municipality.get("code"))
            if code in seen_entry_codes:
                errors.append(f"{label}: duplicate municipality code {code!r}")
                continue
            seen_entry_codes.add(code)
            official = official_by_code.get(code)
            if official is None:
                errors.append(f"{municipality_label}: unknown official municipality code={code!r}")
                continue
            for field in ("sido", "sigungu", "full_name"):
                actual = clean_text(municipality.get(field))
                expected = clean_text(official.get(field))
                if actual != expected:
                    errors.append(
                        f"{municipality_label}: {field} mismatch expected={expected!r} actual={actual!r}"
                    )
            valid_municipalities.append(municipality)
            municipality_codes.add(code)

        matching_targets = [
            target
            for target in working_by_url.get(expected_normalized, [])
            if clean_text(target.get("provider")).upper() == provider
        ]
        if not matching_targets:
            errors.append(f"{label}: no exact enabled/working YAML target for provider+URL")
        elif action == "live_validate_new" and not any(
            is_locked_live_validate_target(target) for target in matching_targets
        ):
            errors.append(
                f"{label}: live_validate_new target must lock "
                "공공예약/교육·강좌/공공강좌 or 공공예약/체험·견학/체험"
            )

        pair_owners[(provider, normalized)].append(index)
        if normalized:
            scope_owners[normalized].append(index)
        action_counts[action] += 1
        outcome_counts[outcome] += 1
        entry["_provider"] = provider
        entry["_normalized_url"] = normalized
        entry["_municipality_codes"] = tuple(
            clean_text(municipality.get("code")) for municipality in valid_municipalities
        )
        entry["_bound_target_count"] = len(matching_targets)
        entry["_entry_index"] = index
        entry["_valid"] = len(errors) == entry_error_count
        entries.append(entry)

    duplicate_pairs = [(key, indexes) for key, indexes in pair_owners.items() if len(indexes) > 1]
    for (provider, normalized), indexes in duplicate_pairs:
        errors.append(
            f"operational: duplicate provider+normalized_url provider={provider} url={normalized} entries={indexes}"
        )
    duplicate_scopes = [(scope, indexes) for scope, indexes in scope_owners.items() if len(indexes) > 1]
    for scope, indexes in duplicate_scopes:
        errors.append(f"operational: duplicate URL scope {scope} entries={indexes}")
    duplicate_entry_indexes = {
        index
        for _key, indexes in duplicate_pairs
        for index in indexes
    } | {
        index
        for _scope, indexes in duplicate_scopes
        for index in indexes
    }
    for entry in entries:
        if entry["_entry_index"] in duplicate_entry_indexes:
            entry["_valid"] = False

    declared_summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    if declared_summary.get("entries") is not None and declared_summary.get("entries") != len(raw_entries):
        errors.append(
            f"operational summary: entries={declared_summary.get('entries')} actual={len(raw_entries)}"
        )
    for key, actual in (("by_action", dict(action_counts)), ("by_outcome", dict(outcome_counts))):
        declared = declared_summary.get(key)
        if isinstance(declared, dict) and declared != actual:
            errors.append(f"operational summary: {key} mismatch declared={declared} actual={actual}")

    summary.update(
        {
            "operational_entries": len(raw_entries),
            "operational_bound_entries": sum(1 for entry in entries if entry["_bound_target_count"] > 0),
            "operational_municipalities": len(municipality_codes),
            "operational_scope_duplicates": len(duplicate_scopes),
            "operational_by_action": dict(sorted(action_counts.items())),
            "operational_by_outcome": dict(sorted(outcome_counts.items())),
        }
    )
    return entries, errors, summary


def coverage_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = data.get("municipalities")
    if not isinstance(rows, list):
        raise ValueError("coverage: municipalities must be a list")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("coverage: every municipality row must be a mapping")
    return rows


def owner_providers(row: dict[str, Any]) -> list[str]:
    raw = row.get("owner_providers")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raw = []
    return list(dict.fromkeys(clean_text(value).upper() for value in raw if clean_text(value)))


def evidence_urls(row: dict[str, Any]) -> set[str]:
    evidence = row.get("evidence") or []
    if isinstance(evidence, dict):
        evidence = [evidence]
    values: set[str] = set()
    if not isinstance(evidence, list):
        return values
    for item in evidence:
        if isinstance(item, str):
            normalized = normalized_scope_url(item)
            if normalized:
                values.add(normalized)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("url", "target_url", "candidate_url", "normalized_url", "ownership_alias"):
            normalized = normalized_scope_url(item.get(key))
            if normalized:
                values.add(normalized)
    return values


def numeric_production_rows(row: dict[str, Any]) -> int:
    direct = row.get("production_active_rows")
    if isinstance(direct, int) and not isinstance(direct, bool):
        return max(0, direct)
    evidence = row.get("evidence") or []
    if isinstance(evidence, dict):
        evidence = [evidence]
    total = 0
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict):
            continue
        value = item.get("production_active_rows")
        if value is None:
            value = item.get("production_active_course_count")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return total


def validate(
    *,
    queue_path: Path = DEFAULT_QUEUE,
    coverage_path: Path = DEFAULT_COVERAGE,
    target_dir: Path = DEFAULT_TARGET_DIR,
    production_providers_path: Path = DEFAULT_PRODUCTION_PROVIDERS,
    production_evidence_path: Path = DEFAULT_PRODUCTION_EVIDENCE,
    operational_path: Path = DEFAULT_OPERATIONAL,
    expected_municipalities: int | None = EXPECTED_MUNICIPALITIES,
    require_complete: bool = True,
) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    summary: dict[str, Any] = {}

    if not coverage_path.exists():
        return [f"coverage: missing file {coverage_path}"], warnings, summary
    queue = load_yaml(queue_path)
    coverage = load_yaml(coverage_path)
    if queue.get("version") != 1:
        errors.append("queue: version must be 1")
    if coverage.get("version") != 1:
        errors.append("coverage: version must be 1")

    official = queue.get("municipalities") or []
    if not isinstance(official, list) or not all(isinstance(row, dict) for row in official):
        return errors + ["queue: municipalities must be a list of mappings"], warnings, summary
    covered = coverage_rows(coverage)
    summary["official_municipalities"] = len(official)
    summary["coverage_rows"] = len(covered)
    if expected_municipalities is not None and len(official) != expected_municipalities:
        errors.append(f"queue: expected {expected_municipalities} municipalities, found {len(official)}")

    official_codes = [clean_text(row.get("code")) for row in official]
    official_names = [clean_text(row.get("full_name")) for row in official]
    duplicate_official_codes = sorted(code for code, count in Counter(official_codes).items() if count > 1)
    duplicate_official_names = sorted(name for name, count in Counter(official_names).items() if count > 1)
    if duplicate_official_codes:
        errors.append(f"queue: duplicate municipality codes {duplicate_official_codes[:10]}")
    if duplicate_official_names:
        errors.append(f"queue: duplicate municipality names {duplicate_official_names[:10]}")
    official_by_code = {clean_text(row.get("code")): row for row in official}

    coverage_codes = [clean_text(row.get("code")) for row in covered]
    duplicate_coverage_codes = sorted(code for code, count in Counter(coverage_codes).items() if count > 1)
    if duplicate_coverage_codes:
        errors.append(f"coverage: duplicate municipality codes {duplicate_coverage_codes[:10]}")
    missing_codes = sorted(set(official_codes) - set(coverage_codes))
    extra_codes = sorted(set(coverage_codes) - set(official_codes))
    if missing_codes:
        errors.append(f"coverage: missing official municipality codes count={len(missing_codes)} first={missing_codes[:10]}")
    if extra_codes:
        errors.append(f"coverage: unknown municipality codes count={len(extra_codes)} first={extra_codes[:10]}")

    target_rows = load_target_rows(target_dir)
    production_schedule, production_errors = load_production_providers(production_providers_path)
    errors.extend(production_errors)
    production_providers, expansion_errors = expand_aggregate_production_providers(
        production_schedule,
        target_rows=target_rows,
        operational_path=operational_path,
    )
    errors.extend(expansion_errors)
    summary["production_schedule_owners"] = len(production_schedule)
    summary["production_scheduled_providers"] = len(production_providers)

    errors.extend(validate_ownership_alias_contract(target_rows, production_providers))
    working_by_provider, working_by_url, duplicate_scopes = working_target_indexes(
        target_rows,
        production_providers=production_providers,
    )
    summary["configured_target_rows"] = len(target_rows)
    summary["working_target_rows"] = sum(len(rows) for rows in working_by_provider.values())
    summary["working_target_providers"] = len(working_by_provider)
    summary["active_scope_duplicates"] = len(duplicate_scopes)
    for scope, owners in duplicate_scopes[:20]:
        labels = [
            f"{clean_text(owner.get('provider'))}@{owner.get('_target_file')}:{owner.get('_target_index')}"
            for owner in owners
        ]
        errors.append(f"targets: active scope duplicate {scope} owners={labels}")

    production_evidence, production_evidence_errors = load_production_evidence(production_evidence_path)
    errors.extend(production_evidence_errors)
    unscheduled_evidence = sorted(set(production_evidence) - production_providers)
    if unscheduled_evidence:
        errors.append(f"production evidence: providers are not production-scheduled {unscheduled_evidence[:20]}")
    summary["production_evidence_providers"] = len(production_evidence)
    summary["production_evidence_active_providers"] = sum(
        1 for row in production_evidence.values() if int(row.get("active_course_count") or 0) > 0
    )

    operational_entries, operational_errors, operational_summary = validate_operational_manifest(
        operational_path,
        official_by_code=official_by_code,
        working_by_url=working_by_url,
    )
    errors.extend(operational_errors)
    summary.update(operational_summary)
    coverage_by_code = {
        clean_text(row.get("code")): row
        for row in covered
        if clean_text(row.get("code"))
    }
    operational_by_municipality_provider: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in operational_entries:
        if not entry.get("_valid"):
            continue
        provider = clean_text(entry.get("_provider")).upper()
        for code in entry.get("_municipality_codes") or ():
            coverage_row = coverage_by_code.get(code)
            if coverage_row is None:
                errors.append(
                    f"operational:{entry.get('_entry_index')}: municipality code={code} has no coverage row"
                )
                continue
            status = clean_text(coverage_row.get("status")).lower()
            coverage_provider_list = promoted_providers(coverage_row) or owner_providers(coverage_row)
            if status not in {"promoted", "covered_by_existing", "covered_by_parent"}:
                errors.append(
                    f"operational:{entry.get('_entry_index')}: municipality code={code} "
                    f"coverage status must be promoted or already covered, found={status!r}"
                )
                continue
            if provider not in coverage_provider_list:
                errors.append(
                    f"operational:{entry.get('_entry_index')}: provider={provider} is not linked by "
                    f"coverage promoted_providers for municipality code={code}"
                )
                continue
            operational_by_municipality_provider[(code, provider)].append(entry)

    status_counts: Counter[str] = Counter()
    configured_covered = 0
    production_scheduled_covered = 0
    production_active_covered = 0
    locked_promoted = 0
    operational_promoted = 0
    validated_promoted = 0
    unresolved: list[str] = []
    unscheduled_covered: list[str] = []

    for row in covered:
        code = clean_text(row.get("code"))
        name = clean_text(row.get("full_name"))
        label = f"coverage:{code or '<missing>'}:{name or '<missing>'}"
        official_row = official_by_code.get(code)
        if official_row is not None and name != clean_text(official_row.get("full_name")):
            errors.append(
                f"{label}: full_name mismatch expected={clean_text(official_row.get('full_name'))!r} actual={name!r}"
            )
        status = clean_text(row.get("status")).lower()
        status_counts[status] += 1
        if status not in ALLOWED_COVERAGE_STATUSES:
            errors.append(f"{label}: unsupported status={status!r}")
            continue
        if status not in COMPLETE_COVERAGE_STATUSES:
            unresolved.append(name or code)

        candidate_count = row.get("candidate_count")
        eligible_count = row.get("eligible_candidate_count")
        excluded_count = row.get("excluded_candidate_count")
        for key, value in (
            ("candidate_count", candidate_count),
            ("eligible_candidate_count", eligible_count),
            ("excluded_candidate_count", excluded_count),
        ):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                errors.append(f"{label}: {key} must be a non-negative integer")
        raw_exclusion_reasons = row.get("exclusion_reasons") or {}
        if not isinstance(raw_exclusion_reasons, dict):
            errors.append(f"{label}: exclusion_reasons must be a mapping")
            exclusion_reasons: dict[str, int] = {}
        else:
            exclusion_reasons = {}
            for raw_reason, raw_count in raw_exclusion_reasons.items():
                reason = clean_text(raw_reason).lower()
                if not reason or isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
                    errors.append(f"{label}: exclusion_reasons must use non-empty keys and non-negative integer counts")
                    continue
                exclusion_reasons[reason] = raw_count
            if isinstance(excluded_count, int) and not isinstance(excluded_count, bool):
                if sum(exclusion_reasons.values()) != excluded_count:
                    errors.append(
                        f"{label}: excluded_candidate_count={excluded_count} does not match "
                        f"exclusion_reasons total={sum(exclusion_reasons.values())}"
                    )

        raw_evidence = row.get("evidence") or []
        if isinstance(raw_evidence, dict):
            raw_evidence = [raw_evidence]
        manual_exclusion_reasons: set[str] = set()
        for item in raw_evidence if isinstance(raw_evidence, list) else []:
            if not isinstance(item, dict) or clean_text(item.get("kind")).lower() != "official_manual_exclusion":
                continue
            reason = clean_text(item.get("exclusion_reason")).lower()
            candidate_url = normalized_scope_url(item.get("candidate_url"))
            normalized_url = normalized_scope_url(item.get("normalized_url"))
            if not MANUAL_EXCLUSION_REASON_PATTERN.fullmatch(reason):
                errors.append(f"{label}: official_manual_exclusion requires a stable snake_case exclusion_reason")
                continue
            manual_exclusion_reasons.add(reason)
            if reason not in exclusion_reasons:
                errors.append(
                    f"{label}: official_manual_exclusion reason={reason!r} is absent from exclusion_reasons"
                )
            if not candidate_url or candidate_url != normalized_url:
                errors.append(
                    f"{label}: official_manual_exclusion candidate_url and normalized_url must identify the same URL"
                )
            if not clean_text(item.get("evidence_note")):
                errors.append(f"{label}: official_manual_exclusion requires evidence_note")
            evidence_links = item.get("evidence_urls")
            if not isinstance(evidence_links, list) or not evidence_links or not all(
                normalized_scope_url(value) for value in evidence_links
            ):
                errors.append(f"{label}: official_manual_exclusion requires valid evidence_urls")
        missing_manual_evidence = sorted(
            reason
            for reason in exclusion_reasons
            if reason not in AUTOMATIC_CANDIDATE_EXCLUSION_REASONS and reason not in manual_exclusion_reasons
        )
        if missing_manual_evidence:
            errors.append(
                f"{label}: manual exclusion reasons require official_manual_exclusion evidence "
                f"{missing_manual_evidence}"
            )
        if status == "no_candidate" and eligible_count not in (None, 0):
            errors.append(f"{label}: no_candidate requires eligible_candidate_count=0")
        if status == "review" and not (row.get("review_candidate_ids") or []):
            errors.append(f"{label}: review requires review_candidate_ids")

        owners = owner_providers(row)
        effective_owners = (promoted_providers(row) or owners) if status == "promoted" else owners
        owner_status = status in COMPLETE_COVERAGE_STATUSES
        if owner_status and not effective_owners:
            required_field = "promoted_providers or owner_providers" if status == "promoted" else "owner_providers"
            errors.append(f"{label}: {status} requires {required_field}")
            continue
        missing_owners = [provider for provider in effective_owners if provider not in working_by_provider]
        if owner_status and missing_owners:
            errors.append(f"{label}: owner providers are not enabled/working {missing_owners}")
            continue
        if owner_status:
            configured_covered += 1
            if any(provider in production_providers for provider in effective_owners):
                production_scheduled_covered += 1
            else:
                unscheduled_covered.append(name or code)
            if any(
                int(production_evidence.get(provider, {}).get("active_course_count") or 0) > 0
                for provider in effective_owners
            ):
                production_active_covered += 1

        if status in {"covered_by_existing", "covered_by_parent"}:
            urls = evidence_urls(row)
            if not urls:
                errors.append(f"{label}: {status} requires exact URL evidence")
            else:
                matching = {
                    clean_text(target.get("provider")).upper()
                    for url in urls
                    for target in working_by_url.get(url, [])
                }
                if not matching.intersection(owners):
                    errors.append(f"{label}: evidence URL does not match an enabled owner target")
            evidence = row.get("evidence") or []
            if isinstance(evidence, dict):
                evidence = [evidence]
            strong_evidence: list[dict[str, Any]] = []
            for item in evidence if isinstance(evidence, list) else []:
                if not isinstance(item, dict):
                    continue
                provider = clean_text(item.get("provider")).upper()
                normalized = normalized_scope_url(
                    item.get("normalized_url")
                    or item.get("target_url")
                    or item.get("candidate_url")
                    or item.get("url")
                )
                target_matches = {
                    clean_text(target.get("provider")).upper()
                    for target in working_by_url.get(normalized, [])
                }
                source_count = int(production_evidence.get(provider, {}).get("active_course_count") or 0)
                claimed_count = item.get("production_active_course_count")
                if claimed_count is None:
                    claimed_count = item.get("production_active_rows")
                if (
                    clean_text(item.get("kind")).lower() in {"exact_active_url", "ownership_alias"}
                    and clean_text(item.get("ownership_basis")).lower()
                    == "production_scheduled_active_courses"
                    and provider in owners
                    and provider in production_providers
                    and provider in target_matches
                    and source_count > 0
                    and claimed_count == source_count
                ):
                    strong_evidence.append(item)
            if not strong_evidence:
                errors.append(
                    f"{label}: {status} requires active target or ownership-alias evidence "
                    "cross-checked with production education rows"
                )
            if status == "covered_by_parent" and not clean_text(
                row.get("parent_code") or row.get("parent_full_name") or row.get("coverage_reason")
            ):
                errors.append(f"{label}: covered_by_parent requires parent_code, parent_full_name, or coverage_reason")

        if status == "promoted":
            direct_providers = {
                provider
                for provider in effective_owners
                if any(
                    clean_text(target.get("municipality_code")) == code
                    and clean_text(target.get("municipality_full_name")) == name
                    and is_locked_education_target(target)
                    for target in working_by_provider.get(provider, [])
                )
            }
            operational_providers = {
                provider
                for provider in effective_owners
                if operational_by_municipality_provider.get((code, provider))
            }
            unmatched_providers = sorted(set(effective_owners) - direct_providers - operational_providers)
            if unmatched_providers:
                errors.append(
                    f"{label}: promoted providers lack a locked target or exact operational binding "
                    f"{unmatched_providers}"
                )
            if direct_providers:
                locked_promoted += 1
            if operational_providers:
                operational_promoted += 1
            if direct_providers or operational_providers:
                validated_promoted += 1

    summary["by_status"] = dict(sorted(status_counts.items()))
    summary["configured_covered_municipalities"] = configured_covered
    summary["production_scheduled_covered_municipalities"] = production_scheduled_covered
    summary["production_active_covered_municipalities"] = production_active_covered
    summary["locked_promoted_municipalities"] = locked_promoted
    summary["operational_promoted_municipalities"] = operational_promoted
    summary["validated_promoted_municipalities"] = validated_promoted
    summary["unresolved_municipalities"] = len(unresolved)
    summary["unscheduled_covered_municipalities"] = len(unscheduled_covered)

    declared_summary = coverage.get("summary") if isinstance(coverage.get("summary"), dict) else {}
    declared_municipalities = declared_summary.get("municipalities")
    if declared_municipalities is not None and declared_municipalities != len(covered):
        errors.append(
            f"coverage summary: municipalities={declared_municipalities} actual={len(covered)}"
        )
    declared_by_status = declared_summary.get("by_status")
    if isinstance(declared_by_status, dict):
        normalized_declared = {clean_text(key).lower(): value for key, value in declared_by_status.items()}
        if normalized_declared != dict(status_counts):
            errors.append(
                f"coverage summary: by_status mismatch declared={normalized_declared} actual={dict(status_counts)}"
            )

    if require_complete and unresolved:
        errors.append(
            f"coverage: unresolved municipalities count={len(unresolved)} first={unresolved[:20]}"
        )
    elif unresolved:
        warnings.append(f"coverage: unresolved municipalities count={len(unresolved)}")
    if unscheduled_covered:
        warnings.append(
            "coverage: configured owners are not production-scheduled for "
            f"{len(unscheduled_covered)} municipalities; first={unscheduled_covered[:20]}"
        )
    if production_scheduled_covered and not production_active_covered:
        warnings.append(
            "coverage: production scheduling alone is not active-course evidence; no municipality has production_active_rows"
        )
    return errors, warnings, summary


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate nationwide municipal integrated-reservation coverage and target ownership."
    )
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--coverage", default=str(DEFAULT_COVERAGE))
    parser.add_argument("--target-dir", default=str(DEFAULT_TARGET_DIR))
    parser.add_argument("--production-providers", default=str(DEFAULT_PRODUCTION_PROVIDERS))
    parser.add_argument("--production-evidence", default=str(DEFAULT_PRODUCTION_EVIDENCE))
    parser.add_argument("--operational", default=str(DEFAULT_OPERATIONAL))
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Validate structure while reporting review/no_candidate rows as warnings instead of errors.",
    )
    args = parser.parse_args()
    errors, warnings, summary = validate(
        queue_path=resolve_path(args.queue),
        coverage_path=resolve_path(args.coverage),
        target_dir=resolve_path(args.target_dir),
        production_providers_path=resolve_path(args.production_providers),
        production_evidence_path=resolve_path(args.production_evidence),
        operational_path=resolve_path(args.operational),
        require_complete=not args.allow_incomplete,
    )
    print("== Summary ==")
    for key, value in summary.items():
        print(f"{key}: {value}")
    print("\n== Errors ==")
    print("none" if not errors else "\n".join(f"- {error}" for error in errors))
    print("\n== Warnings ==")
    print("none" if not warnings else "\n".join(f"- {warning}" for warning in warnings))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
