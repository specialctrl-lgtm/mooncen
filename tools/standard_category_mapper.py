from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "standard_categories.yaml"

MOJIBAKE_HARD_MARKERS = ("�", "占", "횄", "횂")
MOJIBAKE_SOFT_MARKERS = ("챘", "챙", "챠", "챈", "챌", "챕", "챦", "옙")


@dataclass(frozen=True)
class StandardCategory:
    key: str
    label: str
    priority: int
    keywords: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class CategoryResult:
    key: str
    label: str
    confidence: float
    reason: str
    matched_terms: tuple[str, ...]


def looks_corrupted_category(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.count("?") >= 2:
        return True
    if any(marker in text for marker in MOJIBAKE_HARD_MARKERS):
        return True
    if sum(text.count(marker) for marker in MOJIBAKE_SOFT_MARKERS) >= 2:
        return True
    return False


def normalize_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_for_match(value: Any) -> str:
    text = normalize_text(value).lower()
    text = re.sub(r"[\[\](){}/|:,_·ㆍ.,!?~+=<>\"'-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _keyword_in_text(needle: str, haystack: str) -> bool:
    if not needle or not haystack:
        return False
    if needle.isascii() and needle.isalnum() and len(needle) <= 3:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack))
    return needle in haystack


@lru_cache(maxsize=4)
def load_standard_category_config(path: str | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_standard_categories(path: str | None = None) -> tuple[list[StandardCategory], dict[str, str], set[str]]:
    config = load_standard_category_config(path)
    unknown = config.get("unknown_category") or {"key": "uncategorized", "label": "미분류"}
    source_only_terms = {normalize_for_match(term) for term in config.get("source_only_terms") or []}
    categories = [
        StandardCategory(
            key=str(row["key"]),
            label=str(row["label"]),
            priority=int(row.get("priority") or 0),
            keywords=tuple(str(term) for term in (row.get("keywords") or [])),
            description=str(row.get("description") or ""),
        )
        for row in (config.get("categories") or [])
    ]
    categories.sort(key=lambda row: row.priority, reverse=True)
    return categories, {"key": str(unknown["key"]), "label": str(unknown["label"])}, source_only_terms


def _candidate_text(row: dict[str, Any]) -> tuple[str, str]:
    title = normalize_text(row.get("title") or row.get("title_raw"))
    category_raw = normalize_text(row.get("category_raw"))
    collection = normalize_text(row.get("collection_category"))
    domain = normalize_text(row.get("domain_category"))
    program_type = normalize_text(row.get("program_type"))
    source_group = normalize_text(row.get("source_group"))
    description = normalize_text(row.get("description"))

    strong = " ".join(
        part
        for part in (title, category_raw, program_type)
        if part and not looks_corrupted_category(part)
    )
    weak = " ".join(
        part
        for part in (collection, domain, source_group, description[:500])
        if part and not looks_corrupted_category(part)
    )
    return normalize_for_match(strong), normalize_for_match(weak)


def _is_source_only(raw_value: Any, source_only_terms: set[str]) -> bool:
    raw = normalize_for_match(raw_value)
    return bool(raw and raw in source_only_terms)


def classify_standard_category(row: dict[str, Any], config_path: str | None = None) -> CategoryResult:
    categories, unknown, source_only_terms = load_standard_categories(config_path)
    strong_text, weak_text = _candidate_text(row)
    raw_is_source_only = _is_source_only(row.get("category_raw"), source_only_terms)

    best: tuple[int, float, int, StandardCategory, list[str], str] | None = None
    for category in categories:
        matched: list[str] = []
        strong_hits = 0
        weak_hits = 0
        for keyword in category.keywords:
            needle = normalize_for_match(keyword)
            if not needle:
                continue
            if _keyword_in_text(needle, strong_text):
                matched.append(keyword)
                strong_hits += 1
            elif _keyword_in_text(needle, weak_text):
                matched.append(keyword)
                weak_hits += 1
        if not matched:
            continue

        score = category.priority + strong_hits * 20 + weak_hits * 6
        if raw_is_source_only and weak_hits and not strong_hits:
            score -= 10
        reason = f"strong={strong_hits}, weak={weak_hits}, priority={category.priority}"
        candidate = (1 if strong_hits else 0, score, category.priority, category, matched, reason)
        if best is None or candidate[:3] > best[:3]:
            best = candidate

    if not best:
        return CategoryResult(
            key=unknown["key"],
            label=unknown["label"],
            confidence=0.0,
            reason="no_rule_match",
            matched_terms=(),
        )

    _has_strong, score, _priority, category, matched, reason = best
    confidence = min(0.99, max(0.55, score / 160))
    return CategoryResult(
        key=category.key,
        label=category.label,
        confidence=round(confidence, 3),
        reason=reason,
        matched_terms=tuple(dict.fromkeys(matched)),
    )
