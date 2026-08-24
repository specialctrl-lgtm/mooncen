from __future__ import annotations


def infer_age_group_from_category(category_raw: str | None) -> str | None:
    if not category_raw:
        return None

    category = category_raw.strip().lower()
    if not category:
        return None

    if any(token in category for token in ("senior", "\uc2dc\ub2c8\uc5b4", "\uc5b4\ub974\uc2e0", "\uc911\uc7a5\ub144")):
        return "SENIOR"
    if any(token in category for token in ("adult", "dance & exercise", "\uc131\uc778", "\uc77c\ubc18", "\uc8fc\ubd80", "\ud544\ub77c\ud14c\uc2a4", "\uc694\uac00")):
        return "ADULT"
    if any(token in category for token in ("baby", "infant", "\uc601\uc544", "\ubca0\uc774\ube44")):
        return "INFANT"
    if any(token in category for token in ("toddler", "\uc720\uc544", "\ubbf8\ucde8\ud559", "with mom")):
        return "TODDLER"
    if any(token in category for token in ("kids", "children", "child", "\uc5b4\ub9b0\uc774", "\uc544\ub3d9", "\ucd08\ub4f1")):
        return "CHILD"
    if any(token in category for token in ("teen", "\uccad\uc18c\ub144", "\uc911\ub4f1", "\uace0\ub4f1")):
        return "TEEN"
    if category in {"all", "\uc804\uccb4"}:
        return "ALL"
    return None
