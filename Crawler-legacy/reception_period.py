from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from utils import clean_text, parse_date


RECEPTION_LABEL_RE = re.compile(
    r"(\uc811\uc218\s*(?:\uae30\uac04|\uc77c\uc2dc|\uc2dc\uc791|\uc885\ub8cc|"
    r"\uc608\uc815)?|\uc2e0\uccad\s*(?:\uae30\uac04|\uc77c\uc2dc|\uc2dc\uc791|\uc885\ub8cc)?|"
    r"\uc218\uac15\s*\uc2e0\uccad|\ubaa8\uc9d1\s*(?:\uae30\uac04|\uc77c\uc2dc)?|"
    r"\ub4f1\ub85d\s*(?:\uae30\uac04|\uc77c\uc2dc)?)"
)
EXCLUDED_LABEL_RE = re.compile(r"(\ucde8\uc18c|\ud658\ubd88|\uc218\uac15\s*\uae30\uac04|\uac15\uc758\s*\uae30\uac04|\uad50\uc721\s*\uae30\uac04)")
DATE_TOKEN_RE = re.compile(
    r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
    r"|\d{8}"
    r"|\d{1,2}[.\-/]\d{1,2}"
)


def _reference_year(reference_date: Any = None) -> int:
    if isinstance(reference_date, datetime):
        return reference_date.year
    if isinstance(reference_date, date):
        return reference_date.year
    parsed = parse_date(str(reference_date)) if reference_date else None
    return parsed.year if parsed else datetime.now().year


def _parse_date_token(value: str, reference_year: int) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        return parse_date(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
    if re.fullmatch(r"\d{1,2}[.\-/]\d{1,2}", text):
        month, day = re.split(r"[.\-/]", text)
        return parse_date(f"{reference_year}-{int(month):02d}-{int(day):02d}")
    return parse_date(text.replace("/", "-").replace(".", "-"))


def _normalize_range_years(start: date | None, end: date | None) -> tuple[date | None, date | None]:
    if start and end and end < start and end.month < start.month:
        try:
            end = date(start.year + 1, end.month, end.day)
        except ValueError:
            pass
    return start, end


def parse_reception_period_text(text: Any, reference_date: Any = None) -> dict[str, Any]:
    """Extract registration/reception dates from Korean culture-center text."""
    source = clean_text(text)
    if not source:
        return {}

    reference_year = _reference_year(reference_date)
    tokens = DATE_TOKEN_RE.findall(source)
    if not tokens:
        return {}

    start = _parse_date_token(tokens[0], reference_year)
    end = _parse_date_token(tokens[1], start.year if start else reference_year) if len(tokens) > 1 else start
    start, end = _normalize_range_years(start, end)
    if not start and not end:
        return {}
    raw = clean_text(tokens[0])
    if len(tokens) > 1:
        raw = f"{clean_text(tokens[0])}~{clean_text(tokens[1])}"
    return {
        "apply_start": start,
        "apply_end": end,
        "apply_period_raw": raw,
    }


def extract_reception_period(text: Any, reference_date: Any = None) -> dict[str, Any]:
    """Find the most likely reception period in a larger block of text."""
    source = clean_text(text)
    if not source:
        return {}

    normalized = re.sub(r"([:\uff1a])", r"\1 ", source)
    chunks = [chunk.strip() for chunk in re.split(r"[\r\n|]+", normalized) if chunk.strip()]
    if len(chunks) <= 1:
        chunks = [chunk.strip() for chunk in re.split(r"\s{2,}", normalized) if chunk.strip()] or [normalized]

    candidates: list[str] = []
    for label_match in RECEPTION_LABEL_RE.finditer(source):
        snippet = source[label_match.start():min(label_match.end() + 180, len(source))]
        excluded = EXCLUDED_LABEL_RE.search(snippet[label_match.end() - label_match.start():])
        if excluded:
            snippet = snippet[:label_match.end() - label_match.start() + excluded.start()]
        if DATE_TOKEN_RE.search(snippet):
            candidates.append(snippet)

    for chunk in chunks:
        if EXCLUDED_LABEL_RE.search(chunk):
            continue
        if RECEPTION_LABEL_RE.search(chunk) and DATE_TOKEN_RE.search(chunk):
            candidates.append(chunk)

    if not candidates and RECEPTION_LABEL_RE.search(source):
        label_match = RECEPTION_LABEL_RE.search(source)
        if label_match:
            start_index = label_match.start()
            end_index = min(label_match.end() + 160, len(source))
            snippet = source[start_index:end_index]
            if not EXCLUDED_LABEL_RE.search(snippet):
                candidates.append(snippet)

    for candidate in candidates:
        parsed = parse_reception_period_text(candidate, reference_date)
        if parsed:
            return parsed
    return {}


def format_apply_period_raw(start_value: Any, end_value: Any) -> str | None:
    start = clean_text(start_value)
    end = clean_text(end_value)
    if start and end:
        return f"{start} ~ {end}"
    return start or end or None
