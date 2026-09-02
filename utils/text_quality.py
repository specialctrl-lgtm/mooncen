from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_C1_CONTROL_RE = re.compile(r"[\u0080-\u009f]")
_HASH_TOKEN_RE = re.compile(r"^[A-F0-9]{7,}$", re.IGNORECASE)

_PROVIDER_TOKEN_LABELS = {
    "ACADEMY": "아카데미",
    "ART": "예술",
    "ARTS": "예술",
    "BOOKING": "예약",
    "CENTER": "센터",
    "COURSE": "강좌",
    "CULTURE": "문화",
    "EDU": "교육",
    "EDUCATION": "교육",
    "FACILITY": "시설",
    "LEARNING": "학습",
    "LIB": "도서관",
    "LIBRARY": "도서관",
    "LIFELONG": "평생학습",
    "MUSEUM": "박물관",
    "NATIONAL": "국립",
    "PROGRAM": "프로그램",
    "RESERVATION": "예약",
    "RESERVE": "예약",
    "SCIENCE": "과학관",
    "SENIOR": "노인복지",
    "SPORT": "체육",
    "SPORTS": "체육",
    "WELFARE": "복지",
    "YOUTH": "청소년",
}

_PROVIDER_NOISE_TOKENS = {
    "CO",
    "COM",
    "GO",
    "HOME",
    "HTML",
    "HTTP",
    "HTTPS",
    "KR",
    "NET",
    "OR",
    "ORG",
    "MUNI",
    "WWW",
}


def clean_display_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def looks_mojibake(value: Any) -> bool:
    """Return True for display text damaged by a Korean encoding round-trip.

    The check is intentionally aimed at human-facing metadata, not source-code
    regular expressions. Korean crawl target labels should not contain C1
    controls, replacement characters, Japanese kana mixed with Hangul/Hanja,
    or the characteristic question-mark/Hanja combinations produced by a
    UTF-8/CP949 decoding mismatch.
    """

    text = clean_display_text(value)
    if not text:
        return False
    if text.startswith(("http://", "https://")):
        return False
    if "\ufffd" in text or _C1_CONTROL_RE.search(text):
        return True

    cjk_count = len(_CJK_RE.findall(text))
    hangul_count = len(_HANGUL_RE.findall(text))
    kana_count = len(_KANA_RE.findall(text))
    question_count = text.count("?")

    if kana_count and (cjk_count or hangul_count or question_count):
        return True
    if question_count >= 2:
        return True
    if cjk_count and hangul_count:
        return True
    if cjk_count >= 2 and question_count:
        return True
    if cjk_count >= 4 and not hangul_count:
        return True
    return False


def readable_text(value: Any, *fallbacks: Any) -> str:
    for candidate in (value, *fallbacks):
        text = clean_display_text(candidate)
        if text and not looks_mojibake(text):
            return text
    return ""


def provider_code_label(provider: Any, url: Any = "") -> str:
    """Build a readable last-resort label without preserving broken text."""

    raw_provider = clean_display_text(provider).upper()
    tokens = [token for token in re.split(r"[^A-Z0-9]+", raw_provider) if token]
    useful_tokens: list[str] = []
    for token in tokens:
        if token in _PROVIDER_NOISE_TOKENS or _HASH_TOKEN_RE.fullmatch(token):
            continue
        label = _PROVIDER_TOKEN_LABELS.get(token)
        if label:
            if label not in useful_tokens:
                useful_tokens.append(label)
            continue
        useful_tokens.append(token.title())

    hostname = urlparse(clean_display_text(url)).hostname or ""
    if useful_tokens and hostname:
        for host_token in hostname.replace("-", ".").split("."):
            label = _PROVIDER_TOKEN_LABELS.get(host_token.upper())
            if label and label not in useful_tokens:
                useful_tokens.append(label)
    if useful_tokens:
        return " ".join(useful_tokens[:5])

    host_tokens = [
        token
        for token in hostname.split(".")
        if token and token not in {"www", "go", "or", "co", "kr", "com", "net", "org"}
    ]
    if host_tokens:
        return host_tokens[-1].replace("-", " ").title()
    return raw_provider.replace("_", " ").title() or "기관"
