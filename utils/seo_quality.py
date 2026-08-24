from __future__ import annotations

import re
import unicodedata
from typing import Any


MIN_CATEGORY_COURSES = 3
NON_INDEXABLE_CATEGORY_TOKENS = (
    "검토필요",
    "미분류",
    "알수없음",
    "unknown",
)


def is_indexable_category_value(category: Any, course_count: Any, *, minimum_courses: int = MIN_CATEGORY_COURSES) -> bool:
    value = unicodedata.normalize("NFKC", str(category or "")).strip()
    try:
        count = int(course_count or 0)
    except (TypeError, ValueError):
        return False
    compact = re.sub(r"\s+", "", value).lower()
    if count < minimum_courses or not 2 <= len(value) <= 40:
        return False
    if any(token in compact for token in NON_INDEXABLE_CATEGORY_TOKENS):
        return False
    if compact in {"기타", "other", "none", "null"}:
        return False
    if re.search(r"(?:https?://|www\.|\d{4}\s*[-./~]\s*\d{1,2})", compact):
        return False
    return True
