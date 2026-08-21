from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from data_parser import explicit_age_month_range
from target_category_fallback import infer_age_group_from_category


CULTURE_CENTER_PROVIDERS = {
    "HOMEPLUS",
    "EMART",
    "LOTTE",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
}
CULTURE_CENTER_CATEGORY = "\ubb38\ud654\uc13c\ud130"

BROAD_GROUP_DEFAULTS = {
    "INFANT": (0, 2),
    "TODDLER": (3, 7),
    "CHILD": (8, 13),
    "TEEN": (14, 19),
    "ADULT": (20, 59),
    "SENIOR": (60, 120),
}

SCHOOL_LEVEL_MAX_MONTHS = {
    "\uc720\uce58": 83,
    "\ubbf8\ucde8\ud559": 83,
    "\ucd08\ub4f1": 156,
    "\uc911\ub4f1": 180,
    "\uc911\ud559": 180,
    "\uace0\ub4f1": 216,
    "\uace0\ud559": 216,
}

AGE_GROUP_KEYWORDS = (
    ("SENIOR", ("\uc2dc\ub2c8\uc5b4", "\uc5b4\ub974\uc2e0", "\uc911\uc7a5\ub144", "senior")),
    ("ADULT", ("\uc131\uc778", "\uc77c\ubc18", "\uc8fc\ubd80", "\uc9c1\uc7a5\uc778", "adult")),
    ("INFANT", ("\uc601\uc544", "\ubca0\uc774\ube44", "infant", "baby")),
    ("TODDLER", ("\uc720\uc544", "\ubbf8\ucde8\ud559", "\uc720\uce58", "toddler", "preschool", "with mom")),
    ("CHILD", ("\uc5b4\ub9b0\uc774", "\uc544\ub3d9", "\ucd08\ub4f1", "kids", "children", "child")),
    ("TEEN", ("\uccad\uc18c\ub144", "\uc911\ub4f1", "\uc911\ud559", "\uace0\ub4f1", "teen")),
    ("ALL", ("\uc804\uccb4", "\uc804\uc5f0\ub839", "all")),
)


@dataclass
class PatternDecision:
    updates: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def add(self, column: str, value: Any, reason: str) -> None:
        self.updates[column] = value
        self.reasons.append(reason)


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _text_from_row(row: dict[str, Any]) -> str:
    fields = (
        row.get("target"),
        row.get("title"),
        row.get("eligibility_raw"),
        row.get("category_raw"),
    )
    return " ".join(str(value) for value in fields if value)


def _explicit_age_candidates(row: dict[str, Any]) -> list[str]:
    target_text = " ".join(
        str(value)
        for value in (row.get("target"), row.get("eligibility_raw"))
        if value
    )
    candidates = []
    if target_text.strip():
        candidates.append(target_text)
    if row.get("title"):
        candidates.append(str(row.get("title")))
    return candidates


def _full_birth_year(value: str, current_year: int) -> int:
    year = int(value)
    if year >= 1000:
        return year
    century = current_year // 100 * 100
    candidate = century + year
    if candidate > current_year + 1:
        candidate -= 100
    return candidate


def _birth_year_to_month_age(value: str, current_year: int) -> int | None:
    year = _full_birth_year(value, current_year)
    age = current_year - year
    if 0 <= age <= 120:
        return age * 12
    return None


def _safe_explicit_age_month_range(value: str) -> tuple[int | None, int | None]:
    current_year = datetime.now().year
    text = value or ""

    match = re.search(
        r"(\d{2,4})\s*\ub144\uc0dd\s*[~-]\s*(\uc720\uce58|\ubbf8\ucde8\ud559|\ucd08\ub4f1|\uc911\ub4f1|\uc911\ud559|\uace0\ub4f1|\uace0\ud559)",
        text,
    )
    if match:
        min_month = _birth_year_to_month_age(match.group(1), current_year)
        max_month = SCHOOL_LEVEL_MAX_MONTHS.get(match.group(2))
        return min_month, max_month

    match = re.search(
        r"(\uc720\uce58|\ubbf8\ucde8\ud559|\ucd08\ub4f1|\uc911\ub4f1|\uc911\ud559|\uace0\ub4f1|\uace0\ud559)\s*[~-]\s*(\d{2,4})\s*\ub144\uc0dd",
        text,
    )
    if match:
        min_month = _birth_year_to_month_age(match.group(2), current_year)
        max_month = SCHOOL_LEVEL_MAX_MONTHS.get(match.group(1))
        return min_month, max_month

    match = re.search(r"(\d{2,4})\s*\ub144\uc0dd\s*(\uc774\uc0c1|\ubd80\ud130)", text)
    if match:
        return _birth_year_to_month_age(match.group(1), current_year), None

    match = re.search(r"(\d{2,4})\s*\ub144\uc0dd\s*(\uc774\ud558|\uae4c\uc9c0)", text)
    if match:
        return None, _birth_year_to_month_age(match.group(1), current_year)

    return explicit_age_month_range(text)


def age_group_from_month_range(min_month: int | None, max_month: int | None) -> str | None:
    month = max_month if max_month is not None else min_month
    if month is None:
        return None
    if month <= 23:
        return "INFANT"
    if month <= 83:
        return "TODDLER"
    if month <= 167:
        return "CHILD"
    if month <= 239:
        return "TEEN"
    if month <= 719:
        return "ADULT"
    return "SENIOR"


def infer_age_group_from_text(row: dict[str, Any]) -> str | None:
    text = _text_from_row(row).lower()
    for group, keywords in AGE_GROUP_KEYWORDS:
        if any(keyword.lower() in text for keyword in keywords):
            return group
    return infer_age_group_from_category(row.get("category_raw"))


def has_broad_default_age_range(row: dict[str, Any]) -> bool:
    group = str(row.get("target_age_group") or "").strip().upper()
    default_range = BROAD_GROUP_DEFAULTS.get(group)
    if not default_range:
        return False
    return (row.get("target_min_age"), row.get("target_max_age")) == default_range


def build_category_age_updates(row: dict[str, Any]) -> PatternDecision:
    decision = PatternDecision()
    provider = str(row.get("provider") or "").strip().upper()

    if provider in CULTURE_CENTER_PROVIDERS:
        if _blank(row.get("collection_category")):
            decision.add("collection_category", CULTURE_CENTER_CATEGORY, "fill_culture_center_collection")
        if _blank(row.get("domain_category")):
            decision.add("domain_category", CULTURE_CENTER_CATEGORY, "fill_culture_center_domain")

    min_month = None
    max_month = None
    for candidate in _explicit_age_candidates(row):
        min_month, max_month = _safe_explicit_age_month_range(candidate)
        if min_month is not None or max_month is not None:
            break
    explicit_age = min_month is not None or max_month is not None

    if explicit_age:
        if row.get("target_min_age") != min_month:
            decision.add("target_min_age", min_month, "explicit_age_min")
        if row.get("target_max_age") != max_month:
            decision.add("target_max_age", max_month, "explicit_age_max")
        inferred_group = age_group_from_month_range(min_month, max_month)
        if inferred_group and _blank(row.get("target_age_group")):
            decision.add("target_age_group", inferred_group, "explicit_age_group")
    else:
        existing_group = str(row.get("target_age_group") or "").strip().upper()
        if existing_group in {"ADULT", "SENIOR"} and (
            row.get("target_min_age") is not None or row.get("target_max_age") is not None
        ):
            if row.get("target_min_age") is not None:
                decision.add("target_min_age", None, "remove_non_explicit_adult_senior_numeric_min")
            if row.get("target_max_age") is not None:
                decision.add("target_max_age", None, "remove_non_explicit_adult_senior_numeric_max")
        elif has_broad_default_age_range(row):
            if row.get("target_min_age") is not None:
                decision.add("target_min_age", None, "remove_non_explicit_group_default_min")
            if row.get("target_max_age") is not None:
                decision.add("target_max_age", None, "remove_non_explicit_group_default_max")
        if _blank(row.get("target_age_group")):
            inferred_group = infer_age_group_from_text(row)
            if inferred_group:
                decision.add("target_age_group", inferred_group, "infer_age_group_from_text_or_category")

    return decision
