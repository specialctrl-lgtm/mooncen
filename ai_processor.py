from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

from data_parser import (
    TargetParser,
    explicit_age_month_range as parse_explicit_age_month_range,
    strip_non_target_age_phrases,
)
from target_cleaner import extract_target_text, normalize_target_text
from title_cleaner import clean_course_title

load_dotenv()

logger = logging.getLogger("AIProcessor")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)


AI_CATEGORIES = {
    "Cooking",
    "Art",
    "Fitness",
    "Language",
    "Kids",
    "Music",
    "Technology",
    "Lifestyle",
    "Beauty",
    "Other",
}

TARGET_PARSER = TargetParser()
AGE_GROUPS = {"INFANT", "TODDLER", "CHILD", "TEEN", "ADULT", "SENIOR", "ALL"}
ZERO_START_AGE_ONLY_RE = re.compile(
    r"^\s*0\s*(?:\uC138|\uC0B4)(?:\s*\uBD80\uD130)?\s*$"
)


def _has_non_target_age_phrase(value: object) -> bool:
    return strip_non_target_age_phrases(str(value or "")) != str(value or "")


def _is_zero_start_age_only(value: object) -> bool:
    return bool(ZERO_START_AGE_ONLY_RE.search(str(value or "")))


def _age_fragment_variants(fragment: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(fragment or "")).strip()
    variants = [text] if text else []

    birth_range = re.search(r"(\d{2,4})\s*[~-]\s*(\d{2,4})\s*\ub144\uc0dd", text)
    if birth_range:
        start, end = birth_range.group(1), birth_range.group(2)
        if len(start) == 4 and len(end) == 4:
            variants.extend(
                [
                    f"{start}~{end[-2:]}\ub144\uc0dd",
                    f"{start}-{end[-2:]}\ub144\uc0dd",
                    f"{start[-2:]}~{end[-2:]}\ub144\uc0dd",
                    f"{start[-2:]}-{end[-2:]}\ub144\uc0dd",
                ]
            )
        elif len(start) == 4 and len(end) == 2:
            variants.extend([f"{start}~20{end}\ub144\uc0dd", f"{start}-20{end}\ub144\uc0dd"])
        elif len(start) == 2 and len(end) == 2:
            variants.extend([f"20{start}~20{end}\ub144\uc0dd", f"20{start}-20{end}\ub144\uc0dd"])

    return list(dict.fromkeys(value for value in variants if value))


def _age_fragment_regex(fragment: str) -> str:
    pattern = re.escape(fragment)
    pattern = pattern.replace(r"\~", r"[~-]")
    pattern = pattern.replace(r"\-", r"[~-]")
    pattern = pattern.replace(r"\ ", r"\s*")
    return pattern


def _remove_age_fragment(title: str, fragment: str) -> str:
    value = str(title or "")
    fragments = _age_fragment_variants(fragment)
    source_fragment = _extract_age_fragment(value)
    if source_fragment:
        fragments.extend(_age_fragment_variants(source_fragment))
    if not fragments:
        return value

    for item in dict.fromkeys(fragments):
        pattern = _age_fragment_regex(item)
        bracketed = rf"([\(\[\{{（［｛])([^\)\]\}}）］｝]{{0,80}}{pattern}[^\)\]\}}）］｝]{{0,80}})([\)\]\}}）］｝])"

        def clean_bracket(match: re.Match) -> str:
            content = re.sub(pattern, " ", match.group(2))
            content = re.sub(
                r"\s*/?\s*(?:\uc6d4|\ud654|\uc218|\ubaa9|\uae08|\ud1a0|\uc77c)\s*/?\s*"
                r"(?:(?:[01]?\d|2[0-3]):[0-5]\d)?",
                " ",
                content,
            )
            content = re.sub(r"\s*[,/|ㅣ│｜-]+\s*", " ", content)
            content = re.sub(r"\s+", " ", content).strip()
            return f" {match.group(1)}{content}{match.group(3)} " if content else " "

        value = re.sub(bracketed, clean_bracket, value)
        value = re.sub(rf"\s*{pattern}\s*,?\s*", " ", value)

    value = re.sub(r"\s*[\(\[\{（［｛]\s*[\)\]\}）］｝]\s*", " ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([)\]\}）］｝])", r"\1", value)
    value = re.sub(r"([(\[\{（［｛])\s+", r"\1", value)
    value = re.sub(r"\s*([\u3163\u2502\uFF5C|])\s*", r"\1", value)
    return value.strip(" -*|,")


def _normalize_age_group(value: object) -> Optional[str]:
    text = str(value or "").strip().upper()
    return text if text in AGE_GROUPS else None


def _optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "null", "none"}:
        return None
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 120 else None


def _valid_age_range(min_age: Optional[int], max_age: Optional[int]) -> bool:
    return min_age is None or max_age is None or min_age <= max_age


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _age_group_from_range(min_age: Optional[int], max_age: Optional[int]) -> Optional[str]:
    age = max_age if max_age is not None else min_age
    if age is None:
        return None
    if age <= 2:
        return "INFANT"
    if age <= 7:
        return "TODDLER"
    if age <= 13:
        return "CHILD"
    if age <= 19:
        return "TEEN"
    if age <= 59:
        return "ADULT"
    return "SENIOR"


def _birth_year_to_age(value: str, current_year: Optional[int] = None) -> Optional[int]:
    current_year = current_year or datetime.now().year
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    if year < 100:
        year += 2000 if year <= 30 else 1900
    if 1900 <= year <= current_year:
        return current_year - year
    return None


def _explicit_age_range(value: object) -> tuple[Optional[int], Optional[int]]:
    text = str(value or "")
    current_year = datetime.now().year

    match = re.search(r"(\d+)\s*개월\s*[~-]\s*(\d{2,4})\s*년생", text)
    if match:
        month_age = int(match.group(1)) // 12
        birth_age = _birth_year_to_age(match.group(2), current_year)
        if birth_age is not None:
            return min(month_age, birth_age), max(month_age, birth_age)

    match = re.search(r"(\d{2,4})\s*년생\s*[~-]\s*초등\s*(\d+)\s*학년", text)
    if match:
        birth_age = _birth_year_to_age(match.group(1), current_year)
        grade_age = 6 + int(match.group(2))
        if birth_age is not None:
            return min(birth_age, grade_age), max(birth_age, grade_age)

    match = re.search(r"만?\s*(\d+)\s*[~-]\s*(\d+)\s*세", text)
    if match:
        return int(match.group(1)), int(match.group(2))

    return None, None


# AI title extraction stores age bounds in months. These helpers intentionally
# use Unicode escapes so they stay stable on Windows terminals with mixed codepages.
def _has_age_keyword_month(text: str) -> bool:
    return bool(
        re.search(
            r"(\uac1c\uc6d4|\ub144\uc0dd|\uc138|\ub9cc\s*\d|"
            r"\ucd08\ub4f1|\uc911\ub4f1|\uc911\ud559|\uace0\ub4f1|\uccad\uc18c\ub144|"
            r"\uc601\uc544|\uc720\uc544|\uc544\ub3d9|\uc5b4\ub9b0\uc774|\uc131\uc778|\uc2dc\ub2c8\uc5b4)",
            text,
        )
    )


def _looks_like_date_or_time_only(text: str) -> bool:
    value = str(text or "").strip()
    if not value or _has_age_keyword_month(value):
        return False
    compact = re.sub(r"\s+", "", value)
    return any(
        re.search(pattern, compact)
        for pattern in (
            r"^\d{1,2}[./]\d{1,2}(?:\([^)]+\))?$",
            r"^\d{3,4}(?:\([^)]+\))?$",
            r"^\d{1,2}:\d{2}$",
            r"^\d{1,2}[./]\d{1,2}(?:\([^)]+\))?\d{1,2}:\d{2}$",
            r"^\d{1,2}[./]\d{1,2}[~-]\d{1,2}[./]\d{1,2}$",
        )
    )


def _looks_like_age_target(value: object) -> bool:
    text = strip_non_target_age_phrases(str(value or "")).strip()
    if not text or _looks_like_date_or_time_only(text):
        return False
    if _has_age_keyword_month(text):
        return True
    return bool(re.search(r"\d{2,4}\s*[~-]\s*\d{2,4}\s*\ub144\uc0dd|\d{2,4}\s*\ub144\uc0dd", text))


def _extract_age_fragment(value: object) -> str:
    text = strip_non_target_age_phrases(str(value or ""))
    patterns = [
        r"\d+\s*\uac1c\uc6d4\s*[~-]\s*\d{2,4}\s*\ub144\uc0dd",
        r"\d+\s*\uac1c\uc6d4\s*[~-]\s*\ub9cc?\s*\d+\s*\uc138",
        r"\ub9cc?\s*\d+\s*\uc138\s*[~-]\s*\d+\s*\uac1c\uc6d4",
        r"\d{2,4}\s*\ub144\uc0dd\s*[~-]\s*(?:\uc720\uce58(?:\uc6d0|\ubd80)?|\ubbf8\ucde8\ud559|\ucd08\ub4f1|\uc911\ub4f1|\uc911\ud559|\uace0\ub4f1)(?:\s*\d+\s*\ud559\ub144|\s*[A-Z])?",
        r"\d{2,4}\s*\ub144\uc0dd\s*[~-]\s*\ucd08\ub4f1\s*\d+\s*\ud559\ub144",
        r"\d{2,4}\s*[~-]\s*\d{2,4}\s*\ub144\uc0dd",
        r"\ub9cc\s*\d+\s*[~-]\s*\d+\s*\uc138",
        r"\d+\s*\uc138\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0)",
        r"\d+\s*\uc138",
        r"\d+\s*[~-]\s*\d+\s*\uac1c\uc6d4",
        r"\d+\s*\uac1c\uc6d4\s*(?:\uc774\uc0c1|\uc774\ud558|\ubd80\ud130|\uae4c\uc9c0)?",
        r"\d{2,4}\s*\ub144\uc0dd",
        r"\ucd08\ub4f1\s*\d+\s*\ud559\ub144",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match and not _looks_like_date_or_time_only(match.group(0)):
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return ""


def _optional_month_int(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "null", "none"}:
        return None
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 1440 else None


def _age_group_from_month_range(min_month: Optional[int], max_month: Optional[int]) -> Optional[str]:
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


def _age_group_from_target_text(
    value: object,
    min_month: Optional[int],
    max_month: Optional[int],
) -> Optional[str]:
    text = str(value or "")
    if "\uc131\uc778" in text and (max_month is None or max_month >= 216):
        return "ADULT"
    if any(word in text for word in ("\uc2dc\ub2c8\uc5b4", "\uc5b4\ub974\uc2e0", "\uc911\uc7a5\ub144")):
        return "SENIOR"
    return _age_group_from_month_range(min_month, max_month)


def _birth_year_to_month_age(value: str, current_year: Optional[int] = None) -> Optional[int]:
    age = _birth_year_to_age(value, current_year)
    return age * 12 if age is not None else None


def _age_year_month_bounds(age: int) -> tuple[int, int]:
    start = age * 12
    return start, start + 11


def _parser_result_to_months(parsed: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    min_age = _optional_int(parsed.get("min_age"))
    max_age = _optional_int(parsed.get("max_age"))
    return (
        min_age * 12 if min_age is not None else None,
        max_age * 12 if max_age is not None else None,
    )


def _explicit_age_month_range(value: object) -> tuple[Optional[int], Optional[int]]:
    text = strip_non_target_age_phrases(str(value or ""))
    shared_min_month, shared_max_month = parse_explicit_age_month_range(text)
    if shared_min_month is not None or shared_max_month is not None:
        return shared_min_month, shared_max_month
    current_year = datetime.now().year
    school_level_max_months = {
        "\uc720\uce58": 84,
        "\uc720\uce58\uc6d0": 84,
        "\uc720\uce58\ubd80": 84,
        "\ubbf8\ucde8\ud559": 84,
        "\ucd08\ub4f1": 156,
        "\uc911\ub4f1": 180,
        "\uc911\ud559": 180,
        "\uace0\ub4f1": 216,
    }

    match = re.search(r"(\d{2,4})\s*(?:\ub144(?:\uc0dd)?)?\s*[~-]\s*\uc131\uc778", text)
    if match:
        month_age = _birth_year_to_month_age(match.group(1), current_year)
        if month_age is not None:
            return month_age, None

    match = re.search(r"\uc131\uc778\s*[~-]\s*(\d{2,4})\s*(?:\ub144(?:\uc0dd)?)?", text)
    if match:
        month_age = _birth_year_to_month_age(match.group(1), current_year)
        if month_age is not None:
            return month_age, None

    match = re.search(r"\ub9cc?\s*(\d{1,2})\s*\uc138\s*[~-]\s*\uc131\uc778", text)
    if match:
        return int(match.group(1)) * 12, None

    match = re.search(r"(\d{1,3})\s*\uac1c\uc6d4\s*[~-]\s*(\d{2,4})\s*\ub144\uc0dd", text)
    if match:
        month_age = int(match.group(1))
        birth_month = _birth_year_to_month_age(match.group(2), current_year)
        if birth_month is not None:
            return min(month_age, birth_month), max(month_age, birth_month)

    match = re.search(r"(\d{2,4})\s*\ub144\uc0dd\s*[~-]\s*\ucd08\ub4f1\s*(\d+)\s*\ud559\ub144", text)
    if match:
        birth_month = _birth_year_to_month_age(match.group(1), current_year)
        grade_month = (6 + int(match.group(2))) * 12
        if birth_month is not None:
            return min(birth_month, grade_month), max(birth_month, grade_month)

    match = re.search(
        r"(\d{2,4})\s*\ub144\uc0dd\s*[~-]\s*(\uc720\uce58(?:\uc6d0|\ubd80)?|\ubbf8\ucde8\ud559|\ucd08\ub4f1|\uc911\ub4f1|\uc911\ud559|\uace0\ub4f1)",
        text,
    )
    if match:
        birth_month = _birth_year_to_month_age(match.group(1), current_year)
        level_month = school_level_max_months.get(match.group(2))
        if birth_month is not None and level_month is not None:
            return min(birth_month, level_month), max(birth_month, level_month)

    match = re.search(r"(\d{2,4})\s*[~-]\s*(\d{2,4})\s*\ub144\uc0dd", text)
    if match:
        first = match.group(1)
        second = match.group(2)
        if len(second) == 2 and len(first) == 4:
            second = first[:2] + second
        first_month = _birth_year_to_month_age(first, current_year)
        second_month = _birth_year_to_month_age(second, current_year)
        if first_month is not None and second_month is not None:
            return min(first_month, second_month), max(first_month, second_month)

    match = re.search(r"(\d{2,4})\s*\ub144\uc0dd\s*(?:\uc774\uc0c1|\ubd80\ud130)", text)
    if match:
        month_age = _birth_year_to_month_age(match.group(1), current_year)
        if month_age is not None:
            return month_age, None

    match = re.search(r"(\d{2,4})\s*\ub144\uc0dd\s*(?:\uc774\ud558|\uae4c\uc9c0)", text)
    if match:
        month_age = _birth_year_to_month_age(match.group(1), current_year)
        if month_age is not None:
            return 0, month_age

    match = re.search(r"(\d{2,4})\s*\ub144\uc0dd", text)
    if match:
        month_age = _birth_year_to_month_age(match.group(1), current_year)
        if month_age is not None:
            return month_age, month_age

    match = re.search(r"(\d{1,3})\s*[~-]\s*(\d{1,3})\s*\uac1c\uc6d4", text)
    if match:
        return int(match.group(1)), int(match.group(2))

    match = re.search(r"(\d{1,3})\s*\uac1c\uc6d4\s*(?:\uc774\uc0c1|\ubd80\ud130)", text)
    if match:
        return int(match.group(1)), None

    match = re.search(r"(\d{1,3})\s*\uac1c\uc6d4\s*(?:\uc774\ud558|\uae4c\uc9c0)", text)
    if match:
        return 0, int(match.group(1))

    match = re.search(r"(\d{1,3})\s*\uac1c\uc6d4", text)
    if match:
        month = int(match.group(1))
        return month, month

    match = re.search(r"\ub9cc\s*(\d{1,2})\s*[~-]\s*(\d{1,2})\s*\uc138", text)
    if match:
        start_age = int(match.group(1))
        end_age = int(match.group(2))
        return start_age * 12, end_age * 12 + 11

    match = re.search(r"(\d{1,2})\s*[~-]\s*(\d{1,2})\s*\uc138", text)
    if match:
        start_age = int(match.group(1))
        end_age = int(match.group(2))
        return start_age * 12, end_age * 12 + 11

    match = re.search(r"(\d{1,2})\s*\uc138\s*(?:\uc774\uc0c1|\ubd80\ud130)", text)
    if match:
        return int(match.group(1)) * 12, None

    match = re.search(r"(\d{1,2})\s*\uc138\s*(?:\uc774\ud558|\uae4c\uc9c0)", text)
    if match:
        return 0, int(match.group(1)) * 12 + 11

    match = re.search(r"(\d{1,2})\s*\uc138", text)
    if match:
        return _age_year_month_bounds(int(match.group(1)))

    return None, None


def _has_explicit_age_month_range(value: object) -> bool:
    min_month, max_month = _explicit_age_month_range(value)
    return min_month is not None or max_month is not None


def _normalized_age_target(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    normalized = normalize_target_text(text) or text
    if not _looks_like_age_target(normalized):
        return ""
    if _has_explicit_age_month_range(normalized) or _looks_like_broad_target_label(normalized):
        return normalized
    return ""


def _specific_crawler_target(value: object) -> str:
    target = _normalized_age_target(value)
    return target if target and _has_explicit_age_month_range(target) else ""


def _looks_like_broad_target_label(value: object) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or len(text) > 90:
        return False
    if any(
        word in text
        for word in (
            "\uc18c\uac1c",
            "\uc548\ub0b4",
            "\ubb38\uc758",
            "\uc811\uc218",
            "\uc218\uac15\ub8cc",
            "\uc7ac\ub8cc\ube44",
            "\uad50\uc721\uacfc\uc815",
            "\ud3c9\uc0dd\ud559\uc2b5\uad00",
            "\ubbfc\uac04\uc774\uc804",
            "\uc9c1\uc601\uad50\uc2e4",
            "\uac80\uc0c9",
            "\ub85c\uadf8\uc778",
            ">",
        )
    ):
        return False

    allowed_words = (
        "\uc601\uc544",
        "\uc720\uc544",
        "\uc544\ub3d9",
        "\uc5b4\ub9b0\uc774",
        "\ucd08\ub4f1\ud559\uc0dd",
        "\ucd08\ub4f1\uc0dd",
        "\ucd08\ub4f1",
        "\uc911\ud559\uc0dd",
        "\uc911\ub4f1",
        "\uc911\ud559",
        "\uace0\ub4f1\ud559\uc0dd",
        "\uace0\ub4f1",
        "\uccad\uc18c\ub144",
        "\uccad\ub144",
        "\uc9c1\uc7a5\uc778",
        "\uc131\uc778",
        "\uc8fc\ubd80",
        "\ubd80\ubaa8",
        "\ubcf4\ud638\uc790",
        "\uc5c4\ub9c8",
        "\uc544\ube60",
        "\uac00\uc871",
        "\uc5b4\ub974\uc2e0",
        "\uc2dc\ub2c8\uc5b4",
        "\uc911\uc7a5\ub144",
        "\uc7a5\uc560\uc778",
        "\uc678\uad6d\uc778",
        "\ub204\uad6c\ub098",
        "\uc804\uccb4",
        "\uc804\uc5f0\ub839",
        "\uc77c\ubc18",
        "\uc784\uc0b0\ubd80",
        "\ub300\uc0c1",
        "\uac15\uc88c",
        "\ubc18",
        "\ud559\ub144",
        "\ub9cc",
        "\uc138",
        "\uac1c\uc6d4",
        "\uc774\uc0c1",
        "\uc774\ud558",
        "\ubd80\ud130",
        "\uae4c\uc9c0",
        "\ub144\uc0dd",
    )
    remainder = text
    for word in allowed_words:
        remainder = remainder.replace(word, " ")
    remainder = re.sub(r"[\d\s,./·ㆍ~\-+&():\[\]\u00b7]+", " ", remainder)
    return not remainder.strip()


def _should_clear_existing_target_text(course: dict[str, Any], target_text: str, explicit_crawler_age: bool) -> bool:
    if target_text or explicit_crawler_age:
        return False
    existing_target = str(course.get("target") or "").strip()
    if not existing_target:
        return False
    return not bool(_normalized_age_target(existing_target))


def _preferred_rule_target(course: dict[str, Any], source_title: str) -> str:
    crawler_target = _specific_crawler_target(course.get("target"))
    if crawler_target:
        return crawler_target
    title_target = extract_target_text(source_title) or ""
    if _looks_like_age_target(title_target):
        return title_target
    return _normalized_age_target(course.get("target"))

FORBIDDEN_SUMMARY_WORDS = (
    "환불",
    "접수",
    "결제",
    "전화",
    "데스크",
    "지점",
    "가격",
    "수강료",
    "재료비",
    "준비물",
    "주차",
    "폐강",
)

DESCRIPTION_NOISE_WORDS = (
    "강좌 규정안내",
    "수강/접수/환불",
    "수강 접수 환불",
    "취소 환불",
    "환불",
    "업무시간",
    "데스크",
    "법정공휴일",
    "폐강",
    "대기 신청",
    "수강신청",
    "강좌 취소",
    "접수",
    "결제",
    "전화",
    "주차안내",
    "주차 할인",
)

TAG_STOP_WORDS = {"문화센터", "강좌", "수업", "프로그램", "이벤트"}


def _has_ascii_alpha(value: str) -> bool:
    return any("a" <= char.lower() <= "z" for char in value)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _compact_prompt_text(value: object, max_length: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip()


def fallback_summary_from_description(description: str, title: str = "") -> str:
    source = clean_course_description(description or "", max_length=180) or _strip_title_operational_noise(title or "") or ""
    source = re.sub(r"\b[A-Za-z][A-Za-z0-9+._-]*\b", " ", source)
    source = re.sub(r"[\[\(（][^\]\)）]*(?:개월|년생|세|접수|보호자|아이만|인당|요일|시간|개강)[^\]\)）]*[\]\)）]", "", source)
    source = re.sub(r"\b\d{1,2}:\d{2}\b", "", source)
    for word in FORBIDDEN_SUMMARY_WORDS:
        source = source.replace(word, " ")
    source = re.sub(r"\s+", " ", source).strip(" -*|.,")
    source = source.rstrip(".。")
    if source:
        return source[:35].rstrip()
    return fallback_summary(title, description)


# Clean Korean rule set uses Unicode escapes so it survives Windows shells and
# mixed code pages.
FORBIDDEN_SUMMARY_WORDS = (
    "\ud658\ubd88",
    "\uc811\uc218",
    "\uc2e0\uccad",
    "\uacb0\uc81c",
    "\uc804\ud654",
    "\ud14c\uc2a4\ud2b8",
    "\uc9c0\uc810",
    "\uac00\uaca9",
    "\uc218\uac15\ub8cc",
    "\uc7ac\ub8cc\ube44",
    "\uc900\ube44\ubb3c",
    "\uc8fc\ucc28",
    "\ub9c8\uac10",
    "\ucde8\uc18c",
    "\ubcc0\uacbd",
    "\uac1c\uac15",
    "\ud734\uac15",
    "\uc2dc\uac04",
    "\uc694\uc77c",
    "\ubc29\ubb38",
    "\ubcf4\ud638\uc790",
    "\uc544\uc774\ub9cc\uc811\uc218",
    "\uc778\ub2f9\uc811\uc218",
)

DESCRIPTION_NOISE_WORDS = (
    "\uac15\uc88c \uaddc\uc815\uc548\ub0b4",
    "\uc218\uac15/\uc811\uc218/\ud658\ubd88",
    "\uc218\uac15 \uc811\uc218 \ud658\ubd88",
    "\ucde8\uc18c \ud658\ubd88",
    "\ud658\ubd88",
    "\uc5c5\ubb34\uc2dc\uac04",
    "\ud14c\uc2a4\ud2b8",
    "\ubc95\uc815\uacf5\ud734\uc77c",
    "\ub9c8\uac10",
    "\ub300\uae30 \uc2e0\uccad",
    "\uc218\uac15\uc2e0\uccad",
    "\uac15\uc88c \ucde8\uc18c",
    "\uc811\uc218",
    "\uacb0\uc81c",
    "\uc804\ud654",
    "\uc8fc\ucc28\uc548\ub0b4",
    "\uc8fc\ucc28 \ud560\uc778",
    "\ubb38\uc758\uc804\ud654",
    "\ub370\uc2a4\ud06c \uc6b4\uc601\uc2dc\uac04",
    "\uc6b4\uc601\uc2dc\uac04",
    "\ud3c9\uc77c/\uc8fc\ub9d0",
    "\ud734\uad00",
    "FAQ",
    "\uc54c\ub9bc\ud1a1",
    "\uc790\ub140\uc774\ub984",
    "\uc131\uc778\uc218\uc5c5\uc2dc",
    "\uc790\uaca9\uad00\ub9ac",
    "\uad50\uc721\uacfc\uc815 \uc6b4\uc601\uae30\uad00",
)


SUMMARY_NOISE_WORDS = DESCRIPTION_NOISE_WORDS + (
    "\uc5c5\ubb34",
    "\ub370\uc2a4\ud06c",
    "\ubb38\uc758",
    "\ubb38\uc758\uc804\ud654",
    "\uc790\uaca9\uba85",
    "\uc790\uaca9\ubc1c\uae09\uae30\uad00",
    "\ubbfc\uac04\uc790\uaca9",
    "\ub86f\ub370\ubb38\ud654\uc13c\ud130",
    "CULTURE CLUB",
)


def _strip_title_operational_noise(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"^\s*\[\s*\ucd94\uac00\s*\uc811\uc218\s*\uc778\uc6d0\uc6a9\s*\]\s*", " ", text)
    text = re.sub(r"^\s*\[\s*\ucd94\uac00\uc811\uc218\uc778\uc6d0\uc6a9\s*\]\s*", " ", text)
    text = re.sub(r"\s*[\(\[][^)\]]*(?:\uc811\uc218|\uc778\ub2f9\s*\uacb0\uc81c|\uc778\ub2f9\uacb0\uc81c)[^)\]]*[\)\]]\s*", " ", text)
    text = re.sub(r"\s*\(\s*\d+\s*\uc778\s*\d*\s*\ud300\s*\uae30\uc900\s*\)\s*", " ", text)
    text = re.sub(r"\s*\(\s*\d+\s*\uc778\s*\uae30\uc900\s*,?\s*\uc778\ub2f9\s*\uacb0\uc81c\s*\)\s*", " ", text)
    text = re.sub(r"\s*\*\s*\d+\s*\uac00\uc9c0\s*\ubaa8\ub450\s*\uc811\uc218\s*$", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -*|,")


def _has_operational_summary_noise(value: object) -> bool:
    text = str(value or "")
    if not text:
        return False
    if any(word and word in text for word in SUMMARY_NOISE_WORDS):
        return True
    if re.search(r"\d{2,3}-\d{3,4}-\d{4}", text):
        return True
    if re.search(r"\d{1,2}\s*:\s*\d{2}\s*~\s*\d{1,2}\s*:\s*\d{2}", text):
        return True
    if text.count("(") != text.count(")") or text.count("[") != text.count("]"):
        return True
    return False


def _category_age_hint(category_raw: object) -> str:
    value = str(category_raw or "").strip().upper()
    if value in {"ADULT", "SENIOR", "ALL"}:
        return value
    return ""

TAG_STOP_WORDS = {
    "\ubb38\ud654\uc13c\ud130",
    "\uac15\uc88c",
    "\uc218\uc5c5",
    "\ud504\ub85c\uadf8\ub7a8",
    "\uc774\ubca4\ud2b8",
    "\uccb4\ud5d8",
    "\uc0dd\ud65c",
}


def clean_course_description(description: str, max_length: int = 700) -> str:
    text = (description or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    for separator in ("\u318d", "\u00b7", "*", "\u203b", "\u25a0", "\u25b6", "\u25c6", "\u25cb", "|"):
        text = text.replace(separator, "|")

    parts = []
    for part in text.split("|"):
        part = part.strip(" -[]")
        if not part:
            continue
        if any(word in part for word in DESCRIPTION_NOISE_WORDS):
            continue
        if re.search(r"\d{2,3}-\d{3,4}-\d{4}", part):
            continue
        if re.search(r"\d{1,2}:\d{2}", part) and len(part) < 80:
            continue
        parts.append(part)

    cleaned = " ".join(parts).strip()
    if cleaned:
        return cleaned[:max_length]
    if _has_operational_summary_noise(text):
        return ""
    return text[:max_length]


def fallback_summary(title: str, description: str = "") -> str:
    title_value = _strip_title_operational_noise(title or "")
    description_value = clean_course_description(description or "", max_length=180)
    title_has_noise = any(word in title_value for word in FORBIDDEN_SUMMARY_WORDS) or any(
        phrase in title_value
        for phrase in (
            "\uc811\uc218",
            "\ubcf4\ud638\uc790",
            "\uc120\ucc29\uc21c",
            "\ubc29\ubb38",
            "\ud734\uac15",
            "\ubcf4\uac15",
            "\uac1c\uac15\ud655\uc815",
            "\uac1c\uac15 \uc608\uc815",
        )
    )
    source = description_value if description_value and (title_has_noise or len(title_value) > 35) else title_value
    source = source or description_value or title_value
    source = re.sub(r"[\[\(（][^\]\)）]*(?:개월|년생|세|접수|보호자|아이만|인당|요일|시간|개강)[^\]\)）]*[\]\)）]", "", source)
    source = re.sub(r"\b\d{1,2}:\d{2}\b", "", source)
    source = re.sub(r"\b\d{4}\s*~\s*\d{2,4}\s*년생\b", "", source)
    source = re.sub(r"\b\d{1,3}\s*~\s*\d{1,3}\s*개월\b", "", source)
    source = re.sub(r"\b\d{1,2}\s*~\s*\d{1,2}\s*세\b", "", source)
    source = re.sub(r"\d+\+\d+행사\s*\d*인?\|?", "", source)
    source = re.sub(r"\d{4}\s*~\s*\d{2,4}\s*년생", "", source)
    source = re.sub(r"\d{1,3}\s*~\s*\d{1,3}\s*개월", "", source)
    source = re.sub(r"\d{1,3}\s*개월\s*이상", "", source)
    source = re.sub(r"\d{1,2}\s*세\s*이상", "", source)
    for phrase in (
        "\ubcf4\ud638\uc790\uc811\uc218",
        "\ubcf4\ud638\uc790 1\uc778",
        "\ubcf4\ud638\uc790\uc640",
        "\ubcf4\ud638\uc790 2\uc778",
        "\uc790\ub140\ub9cc\uc811\uc218",
        "\uc544\uc774\ub9cc\uc811\uc218",
        "\uc544\uc774\ub9cc \uc811\uc218",
        "\uc778\ub2f9\uc811\uc218",
        "\ubc29\ubb38\uc811\uc218",
        "\uc120\ucc29\uc21c",
        "\ud2b9\uc77c",
        "\uac1c\uac15\ud655\uc815",
        "\uac1c\uac15 \uc608\uc815",
    ):
        source = source.replace(phrase, " ")
    for word in FORBIDDEN_SUMMARY_WORDS:
        source = source.replace(word, " ")
    source = re.sub(r"\s+", " ", source).strip(" -*|.,")
    if len(source) <= 35:
        return source
    return source[:35].rstrip()


def infer_category(title: str, category_raw: str = "", tags: Optional[list[str]] = None) -> str:
    text = f"{title} {category_raw} {' '.join(tags or [])}".lower()
    if any(word in text for word in ("\ucfe0\ud0b9", "\uc694\ub9ac", "\ubca0\uc774\ud0b9", "\ucfe0\ud0a4", "\ucf00\uc774\ud06c", "\ud478\ub4dc", "\uae40\uce58", "\ub514\uc800\ud2b8")):
        return "Cooking"
    if any(word in text for word in ("\ubbf8\uc220", "\uadf8\ub9bc", "\uc544\ud2b8", "\ub4dc\ub85c\uc789", "\uacf5\uc608", "\ud074\ub808\uc774", "\ud50c\ub77c\uc6cc", "\ucc3d\uc758\ubbf8\uc220", "\uc6d0\uc608", "\ud50c\ub85c\ub9ac\uc2a4\ud2b8", "\uaf43\uaf42\uc774")):
        return "Art"
    if any(word in text for word in ("\uc694\uac00", "\ud544\ub77c\ud14c\uc2a4", "\ub304\uc2a4", "\ubc1c\ub808", "\ub77c\uc778\ub304\uc2a4", "\uc6b4\ub3d9", "\uccb4\ud615", "\uc6cc\ud0b9", "k-pop", "kpop")):
        return "Fitness"
    if any(word in text for word in ("\ud53c\uc544\ub178", "\ubc14\uc774\uc62c\ub9b0", "\uc74c\uc545", "\ub178\ub798", "\uc131\uc545", "\uc6b0\ucfe8\ub810\ub808", "\uc545\uae30", "\uae30\ud0c0", "\ud1b5\uae30\ud0c0", "\uc7a5\uad6c", "\uace0\uace0\uc7a5\uad6c", "\ub09c\ud0c0", "\ud48d\ubb3c", "\uad6d\uc545")):
        return "Music"
    if any(word in text for word in ("\uc601\uc5b4", "\uc911\uad6d\uc5b4", "\uc77c\ubcf8\uc5b4", "\uc2a4\ud53c\uce58", "\uc5b8\uc5b4")):
        return "Language"
    if any(word in text for word in ("\ucf54\ub529", "\ub85c\ubd07", "\uacfc\ud559", "ai", "\ucef4\ud4e8\ud130", "\ub514\uc9c0\ud138")):
        return "Technology"
    if any(word in text for word in ("\uba54\uc774\ud06c\uc5c5", "\ub124\uc77c", "\ud5e4\uc5b4", "\ubdf0\ud2f0")):
        return "Beauty"
    if any(word in text for word in ("\ud0a4\uc988", "\uc5b4\ub9b0\uc774", "\uc720\uc544", "\uc601\uc544", "\uac1c\uc6d4", "\ub144\uc0dd", "\uc5c4\ub9c8\ub791", "\uc624\uac10")):
        return "Kids"
    if any(word in text for word in ("\uc0dd\ud65c", "\uc778\ubb38", "\ucde8\ubbf8", "\uc5ec\ud589", "\uc0ac\uc9c4", "\uc815\ub9ac", "\ub3c5\uc11c", "\uae00\uc4f0\uae30")):
        return "Lifestyle"
    return "Other"


class AIProcessor:
    def __init__(self, ollama_url: str | None = None):
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.gemini_model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        self.ollama_url = (ollama_url or os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_HOST") or "http://wtr-linux:11434").rstrip("/")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
        self.provider_preference = os.getenv("AI_PROVIDER", "OLLAMA").upper()
        self.model = None
        self.provider = None
        self.last_call_metrics: dict[str, Any] = {}
        self._init_client()

    def _init_client(self):
        if self.provider_preference == "OLLAMA":
            self.model = {
                "endpoint": f"{self.ollama_url}/api/generate",
                "model": self.ollama_model,
            }
            self.provider = "OLLAMA"
            logger.info("Initialized Ollama client: %s %s", self.ollama_url, self.ollama_model)
            return

        if self.gemini_key and not self.gemini_key.startswith("your_"):
            self.model = {
                "endpoint": f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model_name}:generateContent",
                "api_key": self.gemini_key,
            }
            self.provider = "GEMINI"
            logger.info("Initialized Gemini client")
            return

        if self.openai_key and not self.openai_key.startswith("your_"):
            self.provider = "OPENAI"
            logger.warning("OpenAI provider is configured but not implemented in this project.")

        if not self.provider:
            logger.warning("No valid AI provider configured.")

    def analyze_course(self, title: str, description: str = "", category_raw: str = "") -> Optional[Dict]:
        if not self.model:
            return None

        description_max = _env_int("AI_DESCRIPTION_MAX_CHARS", 520, 250, 900)
        cleaned_description = clean_course_description(description, max_length=description_max)
        prompt = self._build_prompt(title, cleaned_description, category_raw)

        try:
            if self.provider == "OLLAMA":
                payload = self._call_ollama(prompt)
            elif self.provider == "GEMINI":
                payload = self._call_gemini(prompt)
            else:
                return None

            result = self._parse_ai_json(payload)
            if not result:
                logger.warning("AI response parsing failed for %s", title)
                return self._fallback_result(title, cleaned_description, category_raw)

            normalized = self._normalize_ai_result(result, title, cleaned_description, category_raw)
            if not normalized:
                return self._fallback_result(title, cleaned_description, category_raw)
            return normalized
        except Exception as exc:
            logger.error("AI processing error for %s: %s", title, exc)
            if isinstance(exc, requests.RequestException):
                return None
            return self._fallback_result(title, cleaned_description, category_raw)

    def analyze_title(self, course: dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build display title and age target fields with deterministic rules."""
        source_title = str(course.get("title_raw") or course.get("title") or "").strip()
        if not source_title:
            return None

        rule_title, removed = clean_course_title(source_title)
        rule_target = _preferred_rule_target(course, source_title)
        has_explicit_rule_target = (
            bool(rule_title)
            and _looks_like_age_target(rule_target)
            and (
                _explicit_age_month_range(rule_target)[0] is not None
                or _explicit_age_month_range(rule_target)[1] is not None
            )
        )
        source = "rule_fast_path" if has_explicit_rule_target else "rule_regex"
        confidence = 0.82 if has_explicit_rule_target else 0.72
        return self._normalize_title_result(
            {"confidence": confidence, "source": source},
            course,
            rule_title,
            rule_target,
            removed,
        )

    def _build_title_prompt(self, course: dict[str, Any], rule_title: str, rule_target: object) -> str:
        current_year = datetime.now().year
        provider = _compact_prompt_text(course.get("provider"), 40)
        category_raw = _compact_prompt_text(course.get("category_raw"), 120)
        title = _compact_prompt_text(course.get("title"), 180)
        title_raw = _compact_prompt_text(course.get("title_raw"), 180)
        target = _compact_prompt_text(course.get("target"), 120)
        schedule_raw = _compact_prompt_text(course.get("schedule_raw"), 160)
        rule_title_text = _compact_prompt_text(rule_title, 180)
        rule_target_text = _compact_prompt_text(rule_target, 120)
        return f"""
You clean Korean culture-center course titles.
Return exactly one JSON object. No markdown. No explanation.

Goal:
- clean_title: only the course name for display.
- target_text: only age or birth-year target text.
- age_group: normalized age segment.
- min_age/max_age: age bounds in months, not years. Use Korean age-year math from birth year using current year {current_year}, then multiply by 12.
- Single year-age targets cover the whole age year: 8 years old => min_age 96 and max_age 107. 8~9 years old => min_age 96 and max_age 119.
- Do not extract schedule, date, day, time, fee, material fee, period, branch, or instructor.
- If a parenthesized phrase contains both age and a non-age note, remove only the age from target_text and keep the non-age note in clean_title.
- Keep meaningful course subtitles such as "(포인트 안무)" in clean_title.
- Do not guess missing ages.
- If target is written in months, keep the value in months.
- For "24개월 이상", use min_age 24 and max_age null.
- For "24~48개월", use min_age 24 and max_age 48.
- For "2020~22년생", use min_age 48 and max_age 72 in {current_year}.
- If only a broad group is known from category, age_group may be set and min_age/max_age may use the month-based group default.
- confidence should be 0.8 or higher only when the split is clear.
- Important: date/time numbers such as 5/26, 05.17, 0517, 10:20 are not ages. Do not put them in target_text or min_age/max_age.
- Month examples: 24 months or 24개월 => min_age 24. 24~48 months or 24~48개월 => min_age 24, max_age 48. 2020~22 birth-year target in {current_year} => min_age 48, max_age 72.

Examples:
- "K-POP Star G.Den [2020~22년생/일/10:00]" -> clean_title "K-POP Star G.Den", target_text "2020~22년생", age_group "TODDLER", min_age 48, max_age 72
- "벚꽃 팝콘(24~48개월)*아이만접수" -> clean_title "벚꽃 팝콘 아이만접수", target_text "24~48개월", age_group "TODDLER", min_age 24, max_age 48
- "아이돌 댄스 따라잡기(포인트 안무)(2017~21년생)" -> clean_title "아이돌 댄스 따라잡기(포인트 안무)", target_text "2017~21년생", age_group "CHILD", min_age 60, max_age 108
- "빛과 마술의 콜라보! 라이트 드로잉 매직쇼(24개월 이상,관람 가족 인당접수)" -> clean_title "빛과 마술의 콜라보! 라이트 드로잉 매직쇼 관람 가족 인당접수", target_text "24개월 이상", age_group "TODDLER", min_age 24, max_age null

Input:
provider: {provider}
category_raw: {category_raw}
title: {title}
title_raw: {title_raw}
target: {target}
schedule_raw: {schedule_raw}
rule_clean_title: {rule_title_text}
rule_target_text: {rule_target_text}

Schema:
{{"clean_title":"string|null","target_text":"string|null","age_group":"INFANT|TODDLER|CHILD|TEEN|ADULT|SENIOR|ALL|null","min_age":0,"max_age":1440,"target_with_parent":false,"confidence":0.0}}
""".strip()

    def _normalize_title_result(
        self,
        result: Dict[str, Any],
        course: dict[str, Any],
        rule_title: str,
        rule_target: object,
        removed: str,
    ) -> Dict[str, Any]:
        source_title = str(course.get("title_raw") or course.get("title") or "").strip()
        source_has_non_target_age = _has_non_target_age_phrase(source_title)
        crawler_target = _specific_crawler_target(course.get("target"))
        used_crawler_target = False
        clean_title = str(result.get("clean_title") or "").strip()
        target_text = str(result.get("target_text") or "").strip()
        if source_has_non_target_age and _is_zero_start_age_only(target_text):
            target_text = ""
        try:
            confidence = float(result.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        if not clean_title or len(clean_title) < 2:
            clean_title = rule_title or source_title
        if crawler_target:
            target_text = crawler_target
            used_crawler_target = True
        elif not _looks_like_age_target(target_text):
            source_fragment = _extract_age_fragment(source_title)
            if _looks_like_age_target(source_fragment):
                target_text = source_fragment
            elif _looks_like_age_target(rule_target):
                target_text = str(rule_target or "").strip()
            else:
                target_text = ""
        elif _looks_like_age_target(rule_target):
            rule_target_text = str(rule_target or "").strip()
            if len(rule_target_text) > len(target_text) and rule_target_text.startswith(target_text):
                target_text = rule_target_text

        clean_title = re.sub(r"\s*[\(\[\{（［｛]\s*[\)\]\}）］｝]\s*", " ", clean_title)
        clean_title = re.sub(r"\s+", " ", clean_title).strip(" -*|,")
        target_text = re.sub(r"\s+", " ", target_text).strip(" -*|,")
        target_text = normalize_target_text(target_text) or target_text
        if source_has_non_target_age and _is_zero_start_age_only(target_text):
            target_text = ""
        clean_title = _remove_age_fragment(clean_title, target_text)
        clean_title = re.sub(
            r"\s*[~-]\s*(?:\uc720\uce58(?:\uc6d0|\ubd80)?|\ubbf8\ucde8\ud559|\ucd08\ub4f1|\uc911\ub4f1|\uc911\ud559|\uace0\ub4f1)(?:\s*\d+\s*\ud559\ub144|\s*[A-Z])?\s*$",
            "",
            clean_title,
        )
        clean_title = _strip_title_operational_noise(clean_title)
        rule_cleaned_title, rule_removed_after_ai = clean_course_title(clean_title)
        if rule_cleaned_title and rule_cleaned_title != clean_title:
            clean_title = rule_cleaned_title
            removed = " | ".join(part for part in [removed, rule_removed_after_ai] if part)
        clean_title = _strip_title_operational_noise(clean_title)
        target_text = re.sub(
            r"(\ub144\uc0dd\s*[~-]\s*(?:\uc720\uce58(?:\uc6d0|\ubd80)?|\ubbf8\ucde8\ud559|\ucd08\ub4f1|\uc911\ub4f1|\uc911\ud559|\uace0\ub4f1))(?:\s*[A-Z])$",
            r"\1",
            target_text,
        )
        if confidence == 0.0 and not result and rule_title and rule_title != source_title:
            confidence = 0.72

        category_hint = _category_age_hint(course.get("category_raw"))
        parsed_source = target_text or category_hint
        parsed = TARGET_PARSER.parse(parsed_source)
        used_category_hint = False
        if category_hint and not target_text:
            parsed["age_group"] = category_hint
            parsed["min_age"] = None
            parsed["max_age"] = None
            used_category_hint = True
            if confidence == 0.0 and not result:
                confidence = 0.72
        parsed_min_month, parsed_max_month = _parser_result_to_months(parsed)
        explicit_min_month, explicit_max_month = _explicit_age_month_range(target_text)
        if explicit_min_month is not None or explicit_max_month is not None:
            parsed["min_age"] = explicit_min_month
            parsed["max_age"] = explicit_max_month
            parsed["age_group"] = _age_group_from_target_text(target_text, explicit_min_month, explicit_max_month)
            group_tags = {
                tag
                for defaults in TARGET_PARSER.GROUP_DEFAULTS.values()
                for tag in defaults.get("tags", [])
            }
            preserved_tags = [tag for tag in parsed.get("tags", []) if tag not in group_tags]
            final_group_tags = TARGET_PARSER.GROUP_DEFAULTS.get(parsed["age_group"], {}).get("tags", [])
            parsed["tags"] = sorted(set(preserved_tags + final_group_tags))
        else:
            parsed["min_age"] = parsed_min_month
            parsed["max_age"] = parsed_max_month
            if parsed_min_month is not None or parsed_max_month is not None:
                parsed["age_group"] = _age_group_from_month_range(parsed_min_month, parsed_max_month) or parsed.get("age_group")
        if (
            target_text
            and explicit_min_month is None
            and explicit_max_month is None
            and parsed.get("age_group") in {"ADULT", "SENIOR", "ALL"}
            and _looks_like_broad_target_label(target_text)
        ):
            parsed["min_age"] = None
            parsed["max_age"] = None
        ai_age_group = _normalize_age_group(result.get("age_group"))
        ai_min_age = _optional_month_int(result.get("min_age"))
        ai_max_age = _optional_month_int(result.get("max_age"))
        ai_with_parent = str(result.get("target_with_parent") or "").strip().lower() in {"true", "1", "yes", "y"}
        ai_age_is_usable = bool(ai_age_group or ai_min_age is not None or ai_max_age is not None)
        if not _valid_age_range(ai_min_age, ai_max_age):
            ai_age_is_usable = False
        used_ai_age_values = False

        if ai_age_is_usable and target_text:
            parsed_has_range = parsed.get("min_age") is not None or parsed.get("max_age") is not None
            if (ai_min_age is not None or ai_max_age is not None) and not parsed_has_range:
                parsed["min_age"] = ai_min_age
                parsed["max_age"] = ai_max_age
                parsed["age_group"] = _age_group_from_month_range(ai_min_age, ai_max_age) or ai_age_group
                used_ai_age_values = True
            elif ai_age_group:
                had_group = bool(parsed.get("age_group"))
                parsed["age_group"] = parsed.get("age_group") or ai_age_group
                used_ai_age_values = not had_group
            parsed["with_parent"] = bool(parsed.get("with_parent")) or ai_with_parent

        existing_target_min = _optional_month_int(course.get("target_min_age"))
        existing_target_max = _optional_month_int(course.get("target_max_age"))
        existing_target_looks_bad = source_has_non_target_age and (
            _is_zero_start_age_only(course.get("target"))
            or (existing_target_min == 0 and existing_target_max in {None, 0})
        )
        existing_explicit_age_valid = _valid_age_range(existing_target_min, existing_target_max)
        existing_target_value = str(course.get("target") or "").strip()
        existing_target_text_invalid = bool(existing_target_value and not _normalized_age_target(existing_target_value))
        explicit_crawler_age = (
            _truthy(course.get("target_age_is_explicit"))
            and not existing_target_looks_bad
            and existing_explicit_age_valid
            and (existing_target_min is not None or existing_target_max is not None)
        )

        if explicit_crawler_age:
            parsed["min_age"] = existing_target_min
            parsed["max_age"] = existing_target_max
            parsed["age_group"] = (
                _normalize_age_group(course.get("target_age_group"))
                or _age_group_from_month_range(existing_target_min, existing_target_max)
            )
            parsed["with_parent"] = bool(course.get("target_with_parent")) or bool(parsed.get("with_parent"))
            if course.get("target_tags"):
                parsed["tags"] = course.get("target_tags") or []
            crawler_target_text = _normalized_age_target(course.get("target"))
            if crawler_target_text:
                target_text = crawler_target_text
                used_crawler_target = True
            else:
                target_text = ""
            used_ai_age_values = False

        if (
            not explicit_crawler_age
            and not parsed.get("age_group")
            and course.get("target_age_group")
            and not existing_target_looks_bad
            and not existing_target_text_invalid
        ):
            parsed["age_group"] = _normalize_age_group(course.get("target_age_group"))
            existing_age_source = str(course.get("target") or category_hint or "")
            if not existing_age_source:
                parsed["age_group"] = None
            default_min_month, default_max_month = (
                (None, None) if category_hint and not course.get("target") else _parser_result_to_months(TARGET_PARSER.parse(existing_age_source))
            )
            parsed["min_age"] = default_min_month
            parsed["max_age"] = default_max_month
            parsed["with_parent"] = bool(course.get("target_with_parent"))
            parsed["tags"] = course.get("target_tags") or []

        if parsed.get("age_group") and not parsed.get("tags"):
            parsed["tags"] = TARGET_PARSER.GROUP_DEFAULTS.get(parsed["age_group"], {}).get("tags", [])

        if confidence == 0.0 and (clean_title != source_title or used_category_hint):
            confidence = 0.72

        clear_target_text = _should_clear_existing_target_text(course, target_text, explicit_crawler_age)

        return {
            "title": clean_title[:255],
            "target": target_text[:100] if target_text else None,
            "target_age_group": parsed.get("age_group"),
            "target_min_age": parsed.get("min_age"),
            "target_max_age": parsed.get("max_age"),
            "target_with_parent": parsed.get("with_parent", False),
            "target_tags": parsed.get("tags", []),
            "title_prefix_removed": removed or None,
            "ai_title_confidence": confidence,
            "clear_target_age_bounds": used_category_hint,
            "clear_target_text": clear_target_text,
            "ai_title_result": {
                "clean_title": clean_title,
                "target_text": target_text or None,
                "age_group": parsed.get("age_group"),
                "min_age": parsed.get("min_age"),
                "max_age": parsed.get("max_age"),
                "target_with_parent": parsed.get("with_parent", False),
                "confidence": confidence,
                "ai_age_group": ai_age_group,
                "ai_min_age": ai_min_age,
                "ai_max_age": ai_max_age,
                "ai_age_used": used_ai_age_values,
                "age_unit": "months",
                "age_value_source": (
                    "crawler_explicit"
                    if explicit_crawler_age
                    else (
                        "ai"
                        if used_ai_age_values
                        else (
                            "crawler_target"
                            if used_crawler_target
                            else ("target_text" if target_text else ("category_hint" if used_category_hint else "existing"))
                        )
                    )
                ),
                "source_title": course.get("title"),
                "source_title_raw": course.get("title_raw"),
                "source_category_raw": course.get("category_raw"),
                "source": result.get("source") if result.get("source") else ("ai" if result else "rule_fallback"),
                "clear_target_text": clear_target_text,
            },
        }

    def _build_prompt(self, title: str, description: str, category_raw: str = "") -> str:
        title_text = _compact_prompt_text(title, 180)
        description_text = _compact_prompt_text(description, _env_int("AI_DESCRIPTION_MAX_CHARS", 520, 250, 900))
        category_text = _compact_prompt_text(category_raw, 120)
        return f"""
You extract Korean search tags for culture-center courses.
Return exactly one JSON object. No markdown. No thinking text.
Use only facts in TITLE and DESCRIPTION.

Rules:
- tags: 3 to 5 Korean noun phrases users would search or filter by.
- Do not use English tags.
- Do not use generic tags such as 문화센터, 강좌, 수업, 프로그램, 이벤트.
- Exclude date, day, time, price, branch, phone, refund, registration, material-fee, parking.
- confidence: use 0.7 when the title/description is useful, 0.4 when it is weak.

Schema:
{{"tags":["키워드1","키워드2","키워드3"],"confidence":0.7}}

TITLE: {title_text}
DESCRIPTION: {description_text}
CATEGORY: {category_text}
""".strip()

    def _call_ollama(self, prompt: str, num_predict: int = 120) -> str:
        started = datetime.now()
        response = requests.post(
            self.model["endpoint"],
            json={
                "model": self.model["model"],
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "think": False,
                "options": {
                    "temperature": 0,
                    "top_p": 0.7,
                    "num_predict": num_predict,
                },
            },
            timeout=90,
        )
        response.raise_for_status()
        data = response.json()
        self.last_call_metrics = {
            "provider": "OLLAMA",
            "host": self.ollama_url,
            "model": self.model["model"],
            "elapsed_seconds": max(0.0, (datetime.now() - started).total_seconds()),
            "total_duration_ns": data.get("total_duration"),
            "load_duration_ns": data.get("load_duration"),
            "prompt_eval_count": data.get("prompt_eval_count"),
            "prompt_eval_duration_ns": data.get("prompt_eval_duration"),
            "eval_count": data.get("eval_count"),
            "eval_duration_ns": data.get("eval_duration"),
        }
        return data.get("response", "")

    def _call_gemini(self, prompt: str) -> str:
        response = requests.post(
            self.model["endpoint"],
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": self.model["api_key"],
            },
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]

    def _parse_ai_json(self, payload: str) -> Optional[Dict]:
        cleaned = (payload or "").replace("```json", "").replace("```", "").strip()
        candidates = [cleaned]

        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            candidates.append(match.group(0))

        for candidate in candidates:
            try:
                value = json.loads(candidate)
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                continue

        return None

    def _normalize_ai_result(
        self,
        result: Dict,
        title: str,
        description: str,
        category_raw: str = "",
    ) -> Optional[Dict]:
        tags = self._normalize_tags(result.get("tags"))
        if not tags:
            tags = self._fallback_tags(title, category_raw)
        elif len(tags) < 3:
            for tag in self._fallback_tags(title, category_raw):
                if tag not in tags:
                    tags.append(tag)
                if len(tags) >= 3:
                    break
        category = infer_category(title, category_raw, tags)
        summary = fallback_summary_from_description(description, title)

        return {
            "category": category,
            "tags": tags[:5],
            "summary": summary,
        }

    def _normalize_summary(self, summary: object, title: str, description: str) -> str:
        value = str(summary or "").strip().strip(".")
        value = re.sub(r"\s+", " ", value)
        title_norm = re.sub(r"\s+", " ", str(title or "")).strip().strip(".")

        if not value or len(value) > 35:
            return fallback_summary_from_description(description, title)
        if _has_operational_summary_noise(value):
            return fallback_summary_from_description(description, title)
        if value == title_norm or value in title_norm or title_norm in value:
            return fallback_summary_from_description(description, title)
        if any(word in value for word in FORBIDDEN_SUMMARY_WORDS):
            fallback = fallback_summary(title, description)
            if any(word in fallback for word in FORBIDDEN_SUMMARY_WORDS):
                return fallback_summary_from_description(description, title)
            return fallback
        if _has_ascii_alpha(value):
            return fallback_summary_from_description(description, title)
        return value

    def _normalize_tags(self, tags: object) -> list[str]:
        if isinstance(tags, str):
            values = [tag.strip().lstrip("#") for tag in tags.split(",")]
        elif isinstance(tags, list):
            values = [str(tag).strip().lstrip("#") for tag in tags]
        else:
            values = []

        normalized = []
        seen = set()
        for tag in values:
            tag = re.sub(r"\s+", " ", tag).strip(" #.,")
            if not tag or tag in seen:
                continue
            if len(tag) > 16 or _has_ascii_alpha(tag):
                continue
            if tag in TAG_STOP_WORDS:
                continue
            seen.add(tag)
            normalized.append(tag)
        return normalized[:5]

    def _fallback_tags(self, title: str, category_raw: str = "") -> list[str]:
        text = f"{title} {category_raw}"
        candidates = []
        keyword_groups = [
            ("요리", ("쿠킹", "요리", "베이킹", "쿠키", "케이크")),
            ("미술", ("미술", "그림", "드로잉", "클레이", "공예", "도예")),
            ("운동", ("요가", "필라테스", "댄스", "발레", "라인댄스")),
            ("음악", ("피아노", "바이올린", "음악", "노래", "합창")),
            ("놀이", ("놀이", "오감", "체험", "퍼포먼스")),
            ("어린이", ("키즈", "어린이", "유아", "영아", "개월", "년생")),
        ]
        for tag, keywords in keyword_groups:
            if any(keyword in text for keyword in keywords):
                candidates.append(tag)
        return (candidates + ["문화생활", "취미", "체험"])[:3]

    def _fallback_result(self, title: str, description: str = "", category_raw: str = "") -> Dict:
        tags = self._fallback_tags(title, category_raw)
        return {
            "category": infer_category(title, category_raw, tags),
            "tags": tags,
            "summary": fallback_summary_from_description(description, title),
        }


def _clean_fallback_tags(self: AIProcessor, title: str, category_raw: str = "") -> list[str]:
    text = f"{title} {category_raw}".lower()
    candidates: list[str] = []
    keyword_groups = [
        ("\uc694\ub9ac", ("\ucfe0\ud0b9", "\uc694\ub9ac", "\ubca0\uc774\ud0b9", "\ucfe0\ud0a4", "\ucf00\uc774\ud06c", "\ub514\uc800\ud2b8")),
        ("\ubbf8\uc220", ("\ubbf8\uc220", "\uadf8\ub9bc", "\ub4dc\ub85c\uc789", "\ud074\ub808\uc774", "\uacf5\uc608", "\ucc3d\uc758")),
        ("\uc6b4\ub3d9", ("\uc694\uac00", "\ud544\ub77c\ud14c\uc2a4", "\ub304\uc2a4", "\ubc1c\ub808", "\uc6b4\ub3d9", "\uc6cc\ud0b9")),
        ("\uc74c\uc545", ("\ud53c\uc544\ub178", "\ubc14\uc774\uc62c\ub9b0", "\uc74c\uc545", "\ub178\ub798", "\uc131\uc545", "\uc545\uae30", "\uae30\ud0c0", "\ud1b5\uae30\ud0c0", "\uc7a5\uad6c", "\ub09c\ud0c0", "\ud48d\ubb3c", "\uad6d\uc545")),
        ("\ud0a4\uc988", ("\ud0a4\uc988", "\uc5b4\ub9b0\uc774", "\uc720\uc544", "\uc601\uc544", "\uac1c\uc6d4", "\ub144\uc0dd")),
        ("\uacfc\ud559", ("\uacfc\ud559", "\ucf54\ub529", "\ub85c\ubd07", "\ucef4\ud4e8\ud130", "\ub514\uc9c0\ud138")),
    ]
    for tag, keywords in keyword_groups:
        if any(keyword in text for keyword in keywords):
            candidates.append(tag)
    for fallback in ("\ubb38\ud654\uc0dd\ud65c", "\ucde8\ubbf8", "\uccb4\ud5d8"):
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates[:3]


AIProcessor._fallback_tags = _clean_fallback_tags
