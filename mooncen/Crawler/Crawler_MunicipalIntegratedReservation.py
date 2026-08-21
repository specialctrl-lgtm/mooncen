from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler import Crawler_GeneratedYamlTargets as generated_targets
from service_group import (
    SERVICE_GROUP_EXPERIENCE,
    SERVICE_GROUP_PUBLIC_COURSE,
    infer_experience_institution_source_group,
)
from utils import clean_text


PROVIDER = "MUNICIPAL_RESERVATION_TARGETS"
OPERATIONAL_FILE = ROOT / "config" / "municipal_integrated_reservation_operational.yaml"
MAX_OPERATIONAL_ENTRIES = 2_000
MAX_OPERATIONAL_PARSER_LENGTH = 1_024
OPERATIONAL_ACTIONS = {"schedule_existing", "live_validate_new"}
VALIDATION_OUTCOMES = {"collected", "no_current_data"}
ROW_LEVEL_SERVICE_GROUP_PROVIDERS = {
    # This district catalogue contains ordinary municipal courses and rows
    # explicitly owned by 영도도서관. The collector supplies locked row-level
    # institution metadata for those rows.
    "MUNI_WWW_YEONGDO_GO_KR_33400564",
}
LOCKED_METADATA = {
    "source_group": "municipal_reservation",
    "collection_category": "공공예약",
    "domain_category": "교육·강좌",
    "service_group": "공공강좌",
    "service_group_policy": "locked",
}


def operational_target_metadata(target: dict[str, Any]) -> dict[str, str]:
    institution_source_group = infer_experience_institution_source_group(
        source_group=target.get("source_group"),
        name=target.get("name"),
        branch_name=target.get("branch"),
        collection_category=target.get("collection_category"),
        domain_category=target.get("domain_category"),
    )
    if not institution_source_group:
        # The bounded municipal aggregate also owns explicitly validated
        # reservation catalogues such as Changwon's 체험·견학 sibling target.
        # Preserve that locked target-level boundary instead of silently
        # relabelling every non-institution municipal target as 교육·강좌.
        if (
            clean_text(target.get("service_group_policy")).lower() == "locked"
            and clean_text(target.get("service_group")) == SERVICE_GROUP_EXPERIENCE
        ):
            return {
                "source_group": clean_text(target.get("source_group"))
                or "municipal_reservation",
                "collection_category": clean_text(target.get("collection_category"))
                or "공공예약",
                "domain_category": clean_text(target.get("domain_category"))
                or "체험·견학",
                "service_group": SERVICE_GROUP_EXPERIENCE,
                "service_group_policy": "locked",
            }
        metadata = dict(LOCKED_METADATA)
        if clean_text(target.get("provider")).upper() in ROW_LEVEL_SERVICE_GROUP_PROVIDERS:
            # Empty removes the target-level lock in apply_target_metadata.
            # Collector rows with their own lock remain authoritative; rows
            # without institution evidence still receive the public default.
            metadata["service_group_policy"] = ""
        return metadata

    if institution_source_group == "library":
        return {
            "source_group": "library",
            "collection_category": "도서관",
            "domain_category": "도서관",
            "service_group": SERVICE_GROUP_PUBLIC_COURSE,
            "service_group_policy": "inferred",
        }
    return {
        "source_group": institution_source_group,
        "collection_category": "박물관/과학관",
        "domain_category": "박물관/과학관",
        "service_group": SERVICE_GROUP_EXPERIENCE,
        "service_group_policy": "locked",
    }


def _bounded_text(value: Any, field: str, maximum: int = 500) -> str:
    text = clean_text(value)
    if not text or len(text) > maximum or any(ord(character) < 32 for character in text):
        raise ValueError(f"operational {field} must be a non-empty bounded string")
    return text


def _normalize_municipalities(value: Any, *, index: int) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value or len(value) > 300:
        raise ValueError(f"operational entry {index}: municipalities must be a non-empty bounded list")
    municipalities: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    for municipality_index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise ValueError(
                f"operational entry {index} municipality {municipality_index}: must be a mapping"
            )
        code = _bounded_text(raw.get("code"), "municipality code", 32)
        full_name = _bounded_text(raw.get("full_name"), "municipality full_name", 200)
        if code in seen_codes:
            raise ValueError(f"operational entry {index}: duplicate municipality code {code}")
        seen_codes.add(code)
        municipality = {"code": code, "full_name": full_name}
        for field in ("sido", "sigungu"):
            municipality[field] = _bounded_text(
                raw.get(field), f"municipality {field}", 100
            )
        municipalities.append(municipality)
    return municipalities


def _validated_at(value: Any, *, index: int) -> str:
    text = _bounded_text(value, "validated_at", 100)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"operational entry {index}: validated_at must be an ISO-8601 datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            f"operational entry {index}: validated_at must include a timezone"
        )
    return text


def load_operational_entries(path: Path = OPERATIONAL_FILE) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"municipal operational manifest is missing: {path}")
    data = generated_targets.load_unique_yaml(path) or {}
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("municipal operational manifest must have version: 1")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) > MAX_OPERATIONAL_ENTRIES:
        raise ValueError("municipal operational manifest entries must be a bounded list")

    entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    seen_urls: dict[str, str] = {}
    for index, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"operational entry {index}: must be a mapping")
        provider = generated_targets.validate_provider(raw.get("provider"))
        target_url = generated_targets.normalize_http_url(raw.get("target_url"), required=True)
        normalized_url = _bounded_text(raw.get("normalized_url"), "normalized_url", 8192)
        canonical_url = generated_targets.normalized_duplicate_url(target_url)
        if normalized_url != canonical_url:
            raise ValueError(
                f"operational entry {index}: normalized_url does not match target_url"
            )
        action = _bounded_text(raw.get("action"), "action", 100).lower()
        if action not in OPERATIONAL_ACTIONS:
            raise ValueError(f"operational entry {index}: action is not executable")
        validation_outcome = _bounded_text(
            raw.get("validation_outcome"), "validation_outcome", 100
        ).lower()
        if validation_outcome not in VALIDATION_OUTCOMES:
            raise ValueError(
                f"operational entry {index}: validation_outcome is not executable"
            )
        validated_at = _validated_at(raw.get("validated_at"), index=index)
        parser = _bounded_text(
            raw.get("parser"), "parser", MAX_OPERATIONAL_PARSER_LENGTH
        )
        row_count = raw.get("row_count")
        no_current_data = raw.get("no_current_data")
        if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
            raise ValueError(
                f"operational entry {index}: row_count must be a non-negative integer"
            )
        if not isinstance(no_current_data, bool):
            raise ValueError(f"operational entry {index}: no_current_data must be a boolean")
        if validation_outcome == "collected" and not (
            row_count > 0 and no_current_data is False
        ):
            raise ValueError(
                f"operational entry {index}: collected requires row_count>0 and no_current_data=false"
            )
        if validation_outcome == "no_current_data" and not (
            row_count == 0 and no_current_data is True
        ):
            raise ValueError(
                f"operational entry {index}: no_current_data requires row_count=0 and no_current_data=true"
            )
        municipalities = _normalize_municipalities(raw.get("municipalities"), index=index)
        key = (provider, normalized_url)
        if key in seen_keys:
            raise ValueError(f"operational entry {index}: duplicate provider/url allowlist key")
        previous_provider = seen_urls.get(normalized_url)
        if previous_provider and previous_provider != provider:
            raise ValueError(
                f"operational entry {index}: normalized_url has conflicting providers"
            )
        seen_keys.add(key)
        seen_urls[normalized_url] = provider
        entries.append(
            {
                "provider": provider,
                "normalized_url": normalized_url,
                "target_url": target_url,
                "action": action,
                "validation_outcome": validation_outcome,
                "validated_at": validated_at,
                "parser": parser,
                "row_count": row_count,
                "no_current_data": no_current_data,
                "municipalities": municipalities,
            }
        )
    return entries


def configured_provider_names(value: Optional[str] = None) -> set[str]:
    raw = os.getenv("CRAWLER_PROVIDERS", "") if value is None else value
    providers = {
        token.strip().upper()
        for token in re.split(r"[\s,]+", str(raw or ""))
        if token.strip()
    }
    providers.discard(PROVIDER)
    return providers


def select_operational_targets(
    targets: Iterable[dict[str, Any]],
    entries: Iterable[dict[str, Any]],
    *,
    scheduled_providers: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    scheduled = configured_provider_names() if scheduled_providers is None else {
        str(provider).strip().upper() for provider in scheduled_providers
    }
    scheduled.discard(PROVIDER)
    entry_by_key = {
        (str(entry["provider"]).strip().upper(), str(entry["normalized_url"])): entry
        for entry in entries
        if str(entry.get("provider") or "").strip().upper() not in scheduled
    }
    selected: list[dict[str, Any]] = []
    matched: set[tuple[str, str]] = set()
    for target in targets:
        provider = clean_text(target.get("provider")).upper()
        if provider in scheduled:
            continue
        normalized_url = generated_targets.normalized_duplicate_url(
            generated_targets.target_url(target)
        )
        key = (provider, normalized_url)
        entry = entry_by_key.get(key)
        if not entry:
            continue
        municipalities = [dict(row) for row in entry["municipalities"]]
        primary = municipalities[0]
        prepared = {
            **target,
            **operational_target_metadata(target),
            "municipality_code": primary["code"],
            "municipality_full_name": primary["full_name"],
            "covered_municipalities": municipalities,
            "municipal_operational_action": entry["action"],
            "municipal_validation_outcome": entry["validation_outcome"],
            "municipal_validated_at": entry["validated_at"],
            "municipal_validation_parser": entry["parser"],
            "municipal_validation_row_count": entry["row_count"],
            "municipal_validation_no_current_data": entry["no_current_data"],
        }
        selected.append(prepared)
        matched.add(key)

    missing = sorted(set(entry_by_key) - matched)
    if missing:
        providers = ", ".join(provider for provider, _url in missing[:10])
        raise ValueError(
            "municipal operational allowlist references missing or disabled working targets: "
            f"{providers}"
        )
    return selected


def load_municipal_targets(
    *,
    path: Path = OPERATIONAL_FILE,
    scheduled_providers: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    return select_operational_targets(
        generated_targets.load_yaml_targets(extra_statuses={"no_current_data"}),
        load_operational_entries(path),
        scheduled_providers=scheduled_providers,
    )


def municipal_provider_names(targets: Optional[Iterable[dict[str, Any]]] = None) -> list[str]:
    rows = list(targets) if targets is not None else load_municipal_targets()
    return sorted(
        {
            clean_text(target.get("provider")).upper()
            for target in rows
            if clean_text(target.get("provider"))
        }
    )


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    targets = load_municipal_targets()
    has_provider = any(argument == "--provider" or argument.startswith("--provider=") for argument in args)
    if not has_provider and "--all" not in args:
        providers = municipal_provider_names(targets)
        if not providers:
            print("No operational municipal integrated-reservation targets were found.", file=sys.stderr)
            # The aggregate is intentionally safe to leave in the production
            # schedule before the first live-validated target is promoted.
            return 0
        provider_args: list[str] = []
        for provider in providers:
            provider_args.extend(("--provider", provider))
        args = [*provider_args, *args]

    original_loader = generated_targets.load_yaml_targets
    original_argv = sys.argv[:]

    def load_exact_operational_targets(*_loader_args, **_loader_kwargs) -> list[dict[str, Any]]:
        return [dict(target) for target in targets]

    try:
        generated_targets.load_yaml_targets = load_exact_operational_targets
        sys.argv = [sys.argv[0], *args]
        return generated_targets.main()
    finally:
        generated_targets.load_yaml_targets = original_loader
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(run())
