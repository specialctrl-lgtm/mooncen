from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import yaml

from utils.text_quality import looks_mojibake as _looks_mojibake
from utils.text_quality import readable_text
from utils.url_security import safe_external_http_url


ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = ROOT / "config" / "crawl_targets"
TARGET_FILES = [
    ROOT / "config" / "collected_yaml_crawl_targets.yaml",
    ROOT / "config" / "generated_yaml_crawler_registry.yaml",
    ROOT / "config" / "public_course_targets.yaml",
    ROOT / "config" / "welfare_course_targets.yaml",
    ROOT / "config" / "national_institution_course_search_targets.yaml",
]

PROVIDER_DEFAULTS = {
    "HOMEPLUS": {"label": "홈플러스", "marker_label": "H", "marker_color": "#d71920"},
    "LOTTE": {"label": "롯데", "marker_label": "L", "marker_color": "#7a4a24"},
    "EMART": {"label": "이마트", "marker_label": "E", "marker_color": "#f5c400"},
    "HYUNDAI_DEPT": {"label": "현대백화점", "marker_label": "HD", "marker_color": "#2563eb"},
    "SHINSEGAE_ACADEMY": {"label": "신세계아카데미", "marker_label": "S", "marker_color": "#dc2626"},
    "ELAND_RETAIL": {"label": "이랜드리테일", "marker_label": "ER", "marker_color": "#111827"},
    "AK_PLAZA": {"label": "AK플라자", "marker_label": "A", "marker_color": "#7c3aed"},
    "GALLERIA": {"label": "갤러리아", "marker_label": "G", "marker_color": "#111827"},
    "LOTTE_MART": {"label": "롯데마트", "marker_label": "M", "marker_color": "#92400e"},
    "CULTURE_FACILITY": {"label": "문화기반시설", "marker_label": "체", "marker_color": "#14b8a6"},
}

PROVIDER_COLOR_PALETTE = [
    "#0f766e",
    "#2563eb",
    "#7c3aed",
    "#db2777",
    "#ea580c",
    "#16a34a",
    "#64748b",
]

COLLECTION_SCOPE_MARKERS = (
    "전체",
    "원장",
    "목록",
    "보도자료",
    "부분 목록",
    "부분집합",
    "중복 별칭",
)
COLLECTION_SCOPE_COUNT_SUFFIX = re.compile(r"\s+\d+\s*개(?:관|소|지점)\s*$")


def clean(value: Any) -> str:
    return str(value or "").strip()


def looks_mojibake(value: Any) -> bool:
    return _looks_mojibake(value)


def readable_label(value: Any, fallback: Any = "") -> str:
    return readable_text(value, fallback)


def normalize_url(value: Any) -> str:
    url = clean(value)
    if not url:
        return ""
    if url.startswith("//"):
        url = f"https:{url}"
    elif "://" in url and not url.lower().startswith(("http://", "https://")):
        return ""
    if not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"
    safe_url = safe_external_http_url(url)
    if not safe_url:
        return ""
    parsed = urlparse(safe_url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "", "", parsed.query or "", ""))


def favicon_url_for(homepage_url: str) -> str:
    if not homepage_url:
        return ""
    return f"https://www.google.com/s2/favicons?sz=64&domain_url={quote(homepage_url, safe='')}"


def marker_label_from_text(value: str, provider: str) -> str:
    text = clean(value)
    for char in text:
        if char.isalnum() or "\uac00" <= char <= "\ud7a3":
            return char.upper()
    parts = [part for part in provider.split("_") if part]
    return ("".join(part[0] for part in parts)[:2] or provider[:1] or "?").upper()


def concise_provider_label(value: Any) -> str:
    name = clean(value)
    marker_indexes = [
        name.find(marker)
        for marker in COLLECTION_SCOPE_MARKERS
        if name.find(marker) > 0
    ]
    if marker_indexes:
        name = name[: min(marker_indexes)].strip(" \t·/-")
    name = COLLECTION_SCOPE_COUNT_SUFFIX.sub("", name).strip(" \t·/-")
    return readable_label(name)


def choose_provider_label(row: dict[str, Any]) -> str:
    name = clean(row.get("name") or row.get("label"))
    branch = clean(row.get("branch"))
    if branch and name:
        if concise_provider_label(name) != readable_label(name):
            return readable_label(branch)
        notice_tokens = ("접수", "안내", "공지", "모집", "문화강좌", "이용")
        if any(token in name for token in notice_tokens) and any(char.isdigit() for char in name):
            return readable_label(branch)
    return concise_provider_label(name) or readable_label(branch)


@lru_cache(maxsize=1)
def load_config_provider_metadata() -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}

    paths = []
    if TARGET_DIR.exists():
        paths.extend(sorted(TARGET_DIR.glob("*.yaml")))
    paths.extend(path for path in TARGET_FILES if path.exists())

    for path in paths:
        if path.name == "index.yaml":
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        for row in data.get("targets") or []:
            if not isinstance(row, dict):
                continue
            provider = clean(row.get("provider")).upper()
            if not provider:
                continue
            label = choose_provider_label(row)
            homepage_url = normalize_url(
                row.get("website_url")
                or row.get("homepage")
                or row.get("url")
                or row.get("list_url")
                or row.get("base_url")
                or row.get("source_url")
            )
            existing = metadata.get(provider)
            status = clean(row.get("crawler_status")).lower()
            if not existing or status == "ready":
                metadata[provider] = {
                    "label": label or existing.get("label", "") if existing else label,
                    "website_url": homepage_url or (existing or {}).get("website_url", ""),
                    "favicon_url": favicon_url_for(homepage_url) if homepage_url else (existing or {}).get("favicon_url", ""),
                }
            elif homepage_url and not existing.get("website_url"):
                existing["website_url"] = homepage_url
                existing["favicon_url"] = favicon_url_for(homepage_url)
    return metadata


def provider_defaults(provider: str) -> dict[str, str]:
    known = PROVIDER_DEFAULTS.get(provider)
    config = load_config_provider_metadata().get(provider, {})
    label = readable_label(config.get("label")) or (known or {}).get("label")

    if known:
        return {
            **known,
            "label": label or known["label"],
            "website_url": "",
            "favicon_url": "",
        }

    display_label = label or provider.replace("_", " ").title()
    color = PROVIDER_COLOR_PALETTE[sum(ord(char) for char in provider) % len(PROVIDER_COLOR_PALETTE)]
    return {
        "label": display_label,
        "marker_label": marker_label_from_text(display_label, provider),
        "marker_color": color,
        "website_url": config.get("website_url", ""),
        "favicon_url": config.get("favicon_url", ""),
    }


def provider_label(provider: str, fallback: Any = "") -> str:
    fallback_label = readable_label(fallback)
    code_label = provider.replace("_", " ").title()
    label = readable_label(provider_defaults(provider).get("label", ""))
    if provider.startswith("MUNI_"):
        if label and label != code_label:
            return label
        return fallback_label or label or code_label
    return label or fallback_label or code_label
