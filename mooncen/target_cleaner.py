import re
from typing import Optional


MONTH_TEXT = "\uac1c\uc6d4"
AGE_TEXT = "\uc138"
BIRTH_YEAR_TEXT = "\ub144\uc0dd"
DAY_CHARS = "\uc6d4\ud654\uc218\ubaa9\uae08\ud1a0\uc77c"
SCHOOL_RANGE_TEXT = r"(?:\uc720\uce58(?:\uc6d0|\ubd80)?|\ubbf8\ucde8\ud559|\ucd08\ub4f1(?:\uc0dd|\ud559\uc0dd)?|\uc544\ub3d9|\uc5b4\ub9b0\uc774)"
SCHOOL_LEVEL_TEXT = r"(?:\uc720\uce58(?:\uc6d0|\ubd80)?|\ubbf8\ucde8\ud559|\ucd08\ub4f1|\uc911\ub4f1|\uc911\ud559|\uace0\ub4f1)"
ENGLISH_AGE_LABEL_TARGETS = {
    "baby": "\uc601\uc544",
    "toddler": "\uc720\uc544",
    "kid": "\uc5b4\ub9b0\uc774",
    "kids": "\uc5b4\ub9b0\uc774",
    "child": "\uc5b4\ub9b0\uc774",
    "teen": "\uccad\uc18c\ub144",
    "adult": "\uc131\uc778",
    "senior": "\uc2dc\ub2c8\uc5b4",
    "all": "\uc804\uccb4",
}
ENGLISH_AGE_LABEL_RE = re.compile(
    r"[\[\(]\s*(baby|toddler|kids?|child|teen|adult|senior|all)\s*[\]\)]",
    re.IGNORECASE,
)


def english_age_label_to_target(value: str | None) -> Optional[str]:
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    match = ENGLISH_AGE_LABEL_RE.search(text)
    if match:
        return ENGLISH_AGE_LABEL_TARGETS.get(match.group(1).lower())
    exact = re.fullmatch(r"(baby|toddler|kids?|child|teen|adult|senior|all)", text, re.IGNORECASE)
    if exact:
        return ENGLISH_AGE_LABEL_TARGETS.get(exact.group(1).lower())
    return None

TARGET_PATTERNS = [
    r"\(([^)]*\d{2,4}\s*(?:\ub144(?:\uc0dd)?)?\s*[~-]\s*\uc131\uc778[^)]*)\)",
    r"\[([^\]]*\d{2,4}\s*(?:\ub144(?:\uc0dd)?)?\s*[~-]\s*\uc131\uc778[^\]]*)\]",
    r"(\d{2,4}\s*(?:\ub144(?:\uc0dd)?)?\s*[~-]\s*\uc131\uc778)",
    r"\(([^)]*\uc131\uc778\s*[~-]\s*\d{2,4}\s*(?:\ub144(?:\uc0dd)?)?[^)]*)\)",
    r"\[([^\]]*\uc131\uc778\s*[~-]\s*\d{2,4}\s*(?:\ub144(?:\uc0dd)?)?[^\]]*)\]",
    r"(\uc131\uc778\s*[~-]\s*\d{2,4}\s*(?:\ub144(?:\uc0dd)?)?)",
    rf"\(([^)]*\d+\s*{AGE_TEXT}\s*[~-]\s*\uc131\uc778[^)]*)\)",
    rf"\[([^\]]*\d+\s*{AGE_TEXT}\s*[~-]\s*\uc131\uc778[^\]]*)\]",
    rf"(\d+\s*{AGE_TEXT}\s*[~-]\s*\uc131\uc778)",
    rf"\(([^)]*\d+\s*{AGE_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0)[^)]*)\)",
    rf"\[([^\]]*\d+\s*{AGE_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0)[^\]]*)\]",
    rf"(\d+\s*{AGE_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0))",
    rf"\(([^)]*\d+\s*{AGE_TEXT}\s*[~-]\s*{SCHOOL_RANGE_TEXT}[^)]*)\)",
    rf"\[([^\]]*\d+\s*{AGE_TEXT}\s*[~-]\s*{SCHOOL_RANGE_TEXT}[^\]]*)\]",
    rf"(\d+\s*{AGE_TEXT}\s*[~-]\s*{SCHOOL_RANGE_TEXT})",
    rf"\(([^)]*\d+\s*{MONTH_TEXT}\s*[~-]\s*\ub9cc?\s*\d+\s*{AGE_TEXT}[^)]*)\)",
    rf"\[([^\]]*\d+\s*{MONTH_TEXT}\s*[~-]\s*\ub9cc?\s*\d+\s*{AGE_TEXT}[^\]]*)\]",
    rf"(\d+\s*{MONTH_TEXT}\s*[~-]\s*\ub9cc?\s*\d+\s*{AGE_TEXT})",
    rf"\(([^)]*\ub9cc?\s*\d+\s*{AGE_TEXT}\s*[~-]\s*\d+\s*{MONTH_TEXT}[^)]*)\)",
    rf"\[([^\]]*\ub9cc?\s*\d+\s*{AGE_TEXT}\s*[~-]\s*\d+\s*{MONTH_TEXT}[^\]]*)\]",
    rf"(\ub9cc?\s*\d+\s*{AGE_TEXT}\s*[~-]\s*\d+\s*{MONTH_TEXT})",
    rf"\(([^)]*\d+\s*{MONTH_TEXT}\s*[~-]\s*{SCHOOL_RANGE_TEXT}[^)]*)\)",
    rf"\[([^\]]*\d+\s*{MONTH_TEXT}\s*[~-]\s*{SCHOOL_RANGE_TEXT}[^\]]*)\]",
    rf"(\d+\s*{MONTH_TEXT}\s*[~-]\s*{SCHOOL_RANGE_TEXT})",
    rf"\(([^)]*\d+\s*[~-]\s*\d+\s*{MONTH_TEXT}[^)]*)\)",
    rf"\[([^\]]*\d+\s*[~-]\s*\d+\s*{MONTH_TEXT}[^\]]*)\]",
    rf"(\d+\s*[~-]\s*\d+\s*{MONTH_TEXT})",
    rf"\(([^)]*\d+\s*{MONTH_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130)?[^)]*)\)",
    rf"\[([^\]]*\d+\s*{MONTH_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130)?[^\]]*)\]",
    rf"(\d+\s*{MONTH_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130)?)",
    rf"\(([^)]*\d{{2,4}}\s*[~-]\s*\d{{2}}\s*{BIRTH_YEAR_TEXT}[^)]*)\)",
    rf"\[([^\]]*\d{{2,4}}\s*[~-]\s*\d{{2}}\s*{BIRTH_YEAR_TEXT}[^\]]*)\]",
    rf"(\d{{2,4}}\s*[~-]\s*\d{{2}}\s*{BIRTH_YEAR_TEXT})",
    rf"\(([^)]*\d{{2,4}}\s*{BIRTH_YEAR_TEXT}\s*[~-]\s*{SCHOOL_LEVEL_TEXT}(?:\s*\d+\s*\ud559\ub144|\s*[A-Z])?[^)]*)\)",
    rf"\[([^\]]*\d{{2,4}}\s*{BIRTH_YEAR_TEXT}\s*[~-]\s*{SCHOOL_LEVEL_TEXT}(?:\s*\d+\s*\ud559\ub144|\s*[A-Z])?[^\]]*)\]",
    rf"(\d{{2,4}}\s*{BIRTH_YEAR_TEXT}\s*[~-]\s*{SCHOOL_LEVEL_TEXT}(?:\s*\d+\s*\ud559\ub144|\s*[A-Z])?)",
    rf"\(([^)]*\d{{2,4}}\s*{BIRTH_YEAR_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0)[^)]*)\)",
    rf"\[([^\]]*\d{{2,4}}\s*{BIRTH_YEAR_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0)[^\]]*)\]",
    rf"(\d{{2,4}}\s*{BIRTH_YEAR_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0))",
]

INLINE_TARGET_PATTERNS = [
    r"(\d{2,4}\s*(?:\ub144(?:\uc0dd)?)?\s*[~-]\s*\uc131\uc778)",
    r"(\uc131\uc778\s*[~-]\s*\d{2,4}\s*(?:\ub144(?:\uc0dd)?)?)",
    rf"(\d+\s*{AGE_TEXT}\s*[~-]\s*\uc131\uc778)",
    rf"(\d+\s*{AGE_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0))",
    rf"(\d+\s*{AGE_TEXT}\s*[~-]\s*{SCHOOL_RANGE_TEXT})",
    rf"(\d+\s*{MONTH_TEXT}\s*[~-]\s*\ub9cc?\s*\d+\s*{AGE_TEXT})",
    rf"(\ub9cc?\s*\d+\s*{AGE_TEXT}\s*[~-]\s*\d+\s*{MONTH_TEXT})",
    rf"(\d+\s*{MONTH_TEXT}\s*[~-]\s*{SCHOOL_RANGE_TEXT})",
    rf"(\d+\s*[~-]\s*\d+\s*{MONTH_TEXT})",
    rf"(\d+\s*{MONTH_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130)?)",
    rf"(\d{{2,4}}\s*[~-]\s*\d{{2}}\s*{BIRTH_YEAR_TEXT})",
    rf"(\d{{2,4}}\s*{BIRTH_YEAR_TEXT}\s*[~-]\s*{SCHOOL_LEVEL_TEXT})",
    rf"(\d{{2,4}}\s*{BIRTH_YEAR_TEXT}\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0))",
]


def _full_birth_year(value: str, reference_year: int | None = None) -> str:
    year = int(value)
    if year >= 1000:
        return str(year)
    if reference_year and reference_year >= 1000:
        century = reference_year // 100 * 100
        return str(century + year)
    return str(2000 + year)


def normalize_target_text(value: str | None) -> Optional[str]:
    if not value:
        return None

    target = re.sub(r"\s+", " ", str(value)).strip()
    english_label = english_age_label_to_target(target)
    if english_label:
        return english_label
    target = re.sub(r"^\s*\uc0ac?\ub300\uc0c1\s*[:：]?\s*", "", target)
    target = re.sub(r"\s*([~-])\s*", r"\1", target)
    target = re.sub(r"\s*,\s*", ", ", target)

    def normalize_birth_range(match: re.Match) -> str:
        start_raw, end_raw = match.group(1), match.group(2)
        start_year = _full_birth_year(start_raw)
        end_year = _full_birth_year(end_raw, int(start_year))
        if int(end_year) < int(start_year):
            start_year, end_year = end_year, start_year
        return f"{start_year}~{end_year}{BIRTH_YEAR_TEXT}"

    target = re.sub(
        rf"\b(\d{{2,4}})\s*[~-]\s*(\d{{2,4}})\s*{BIRTH_YEAR_TEXT}",
        normalize_birth_range,
        target,
    )
    target = re.sub(rf"({BIRTH_YEAR_TEXT})(?=\d)", r"\1 ", target)

    def normalize_single_birth(match: re.Match) -> str:
        year = _full_birth_year(match.group(1))
        suffix = match.group(2) or ""
        return f"{year}{BIRTH_YEAR_TEXT}{(' ' + suffix) if suffix else ''}"

    target = re.sub(
        rf"\b(\d{{2,4}})\s*{BIRTH_YEAR_TEXT}(?:\s*(\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0))?",
        normalize_single_birth,
        target,
    )
    target = re.sub(r"\s+", " ", target).strip()
    return target or None


def extract_target_text(value: str | None) -> Optional[str]:
    if not value:
        return None

    english_label = english_age_label_to_target(value)
    if english_label:
        return english_label

    for pattern in TARGET_PATTERNS:
        match = re.search(pattern, value)
        if not match:
            continue
        target = match.group(1).strip()
        original_target = target
        target = re.sub(rf"^\s*[{DAY_CHARS}]\s*/\s*\d{{1,2}}:\d{{2}}\s*/\s*", "", target)
        target = re.split(rf"\s*/\s*[{DAY_CHARS}]\s*/\s*\d{{1,2}}:\d{{2}}", target, maxsplit=1)[0]
        target = re.split(r"\s*/\s*\d{1,2}:\d{2}", target, maxsplit=1)[0]
        for inline_pattern in INLINE_TARGET_PATTERNS:
            inline_match = re.search(inline_pattern, target) or re.search(inline_pattern, original_target)
            if inline_match:
                target = inline_match.group(1)
                break
        return normalize_target_text(target)

    return None
