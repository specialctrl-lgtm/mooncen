from __future__ import annotations

import html
import json
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from requests.exceptions import RequestException

from utils.outbound_http import SafeSession, harden_session


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

USAGE_LINK_KEYWORDS = (
    "\uc774\uc6a9\uc548\ub0b4",
    "\ub3c4\uc11c\uad00\uc774\uc6a9",
    "\uad00\ub78c\uc548\ub0b4",
    "\uc774\uc6a9\uc2dc\uac04",
    "\uc6b4\uc601\uc2dc\uac04",
    "\uac1c\uad00\uc2dc\uac04",
    "\uc5f4\ub78c\uc2dc\uac04",
    "\uad00\ub78c\uc2dc\uac04",
    "\ud734\uad00",
    "\ud734\uad00\uc77c",
    "\uc774\uc6a9\uc694\uae08",
    "\uc694\uae08\uc548\ub0b4",
    "\uad00\ub78c\ub8cc",
    "\uc785\uc7a5\ub8cc",
    "\uc785\uc7a5\uc694\uae08",
    "\uc2dc\uc124\uc548\ub0b4",
    "\uc790\ub8cc\uc2e4\uc548\ub0b4",
)

USAGE_LINK_REJECT_KEYWORDS = (
    "\ub85c\uadf8\uc778",
    "\ud68c\uc6d0\uac00\uc785",
    "\uac1c\uc778\uc815\ubcf4",
    "\uc218\uac15",
    "\uac15\uc88c",
    "\ud589\uc0ac",
    "\ud504\ub85c\uadf8\ub7a8",
    "\uc2e0\uccad",
    "\uc608\uc57d",
    "\uacf5\uc9c0",
    "\uac8c\uc2dc\ud310",
    "\ud76c\ub9dd\ub3c4\uc11c",
    "\ud1b5\ud569\uac80\uc0c9",
    "\uc790\uc8fc\ud558\ub294\uc9c8\ubb38",
    "\ucea0\ud551",
    "\ucc45\ub728",
    "\uc88c\uc11d",
    "reserve",
    "reservation",
    "lecture",
    "program",
    "apply",
    "camping",
    "news",
    "notice",
    "bbs",
    "board",
    "post",
    "ebook",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".pdf",
    ".hwp",
    ".hwpx",
)

HOURS_LABEL_TOKENS = (
    "\uc774\uc6a9\uc2dc\uac04",
    "\uc6b4\uc601\uc2dc\uac04",
    "\uac1c\uad00\uc2dc\uac04",
    "\uc5f4\ub78c\uc2dc\uac04",
    "\uad00\ub78c\uc2dc\uac04",
    "\uc790\ub8cc\uc2e4",
    "\uc5f4\ub78c\uc2e4",
)

HOLIDAY_LABEL_TOKENS = (
    "\ud734\uad00\uc77c",
    "\uc815\uae30\ud734\uad00",
    "\ud734\uad00",
    "\ub2eb\ub294 \ub0a0",
)

FEE_LABEL_TOKENS = (
    "\uc774\uc6a9\uc694\uae08",
    "\uc774\uc6a9\ub8cc",
    "\uc694\uae08\uc548\ub0b4",
    "\uc694\uae08",
    "\uad00\ub78c\ub8cc",
    "\uad00\ub78c\uc694\uae08",
    "\uc785\uc7a5\ub8cc",
    "\uc785\uc7a5\uc694\uae08",
    "\uc785\uc7a5\uad8c",
    "\uad00\ub78c\uad8c",
    "\uc218\uc218\ub8cc",
)

STOP_LABEL_TOKENS = (
    "\uc8fc\uc18c",
    "\ub300\ud45c\uc804\ud654",
    "\uc804\ud654",
    "\ubb38\uc758",
    "\ud329\uc2a4",
    "\uad50\ud1b5",
    "\uc704\uce58",
    "\uc2dc\uc124",
    "\ud68c\uc6d0",
    "\uc774\uc6a9\uc548\ub0b4",
    "\uc774\uc6a9\uc790\uc900\uc218\uc0ac\ud56d",
)

HOURS_REJECT_TOKENS = (
    "\uc8fc\ucc28\uc7a5",
    "\uc8fc\ucc28",
    "\uc8fc\ucc28\ub8cc",
    "\uc8fc\ucc28\uc694\uae08",
    "\ud734 \uad00",
    "\ub85c\uadf8\uc778",
    "\ud68c\uc6d0\uac00\uc785",
    "\uba54\ub274",
    "\uac80\uc0c9",
)

HOLIDAY_REJECT_TOKENS = (
    "\uc774\uc6a9\uac00\ub2a5",
    "\uac1c\uad00\ud569\ub2c8\ub2e4",
    "\ub2ec\ub825",
    "\uc9c0\ubc29\uc120\uac70",
    "\ud604\ucda9\uc77c",
    "\uc678\ucd9c",
    "] :",
    "]:",
    "\ud734\uad00\uc77c \uc548\ub0b4\ud45c",
    "\uc548\ub0b4\ud45c",
    "\uc815\uc0c1 \uc6b4\uc601",
    "\uc790\uc720\ub86d\uac8c \uc774\uc6a9",
    "\uc5b8\uc81c\ub098 \uc790\uc720\ub86d\uac8c",
    "\ubb38\ud654\uac00 \uc788\ub294 \ub0a0",
    "\uacf5\uc9c0",
    "\uc54c\ub9bc",
    "\ubcf4\ub3c4\uc790\ub8cc",
    "\ucc44\uc6a9",
    "\ub354\ubcf4\uae30",
    "\ud734\uc2e4",
    "\uc784\uc2dc\ud734\uad00 \uc548\ub0b4",
)

FEE_REJECT_TOKENS = (
    "\uc218\uac15\ub8cc",
    "\uac15\uc88c",
    "\uac15\uc758",
    "\ud504\ub85c\uadf8\ub7a8",
    "\uad50\uc721\ube44",
    "\uc2e0\uccad",
    "\uc811\uc218",
    "\uc608\uc57d",
    "\uc804\ud654",
    "\ud329\uc2a4",
    "\uc8fc\ucc28",
    "\uc8fc\ucc28\ub8cc",
    "\uc8fc\ucc28\uc694\uae08",
    "\uc8fc\ucc28\uc7a5",
    "\ud61c\ud0dd",
    "\ud655\uc778\uc11c",
    "\uc911\ubcf5 \uba74\uc81c",
    "\ud560\uc778\uc740 \ubd88\uac00\ub2a5",
)

PARKING_SECTION_TOKENS = (
    "\uc8fc\ucc28",
    "\uc8fc\ucc28\ub8cc",
    "\uc8fc\ucc28\uc694\uae08",
    "\uc8fc\ucc28\uc7a5",
    "\uc8fc\ucc28\uc815\uc0b0",
)

NON_PARKING_FEE_REJECT_TOKENS = (
    "\uc218\uac15\ub8cc",
    "\uac15\uc88c",
    "\uac15\uc758",
    "\ud504\ub85c\uadf8\ub7a8",
    "\uad50\uc721\ube44",
    "\uc2e0\uccad",
    "\uc811\uc218",
    "\uc608\uc57d",
    "\uc804\ud654",
    "\ud329\uc2a4",
    "\ud61c\ud0dd",
    "\ud655\uc778\uc11c",
    "\uc911\ubcf5 \uba74\uc81c",
    "\ud560\uc778\uc740 \ubd88\uac00\ub2a5",
)

ADMISSION_FEE_LABEL_TOKENS = (
    "\uad00\ub78c\ub8cc",
    "\uad00\ub78c\uc694\uae08",
    "\uc785\uc7a5\ub8cc",
    "\uc785\uc7a5\uc694\uae08",
    "\uc785\uc7a5\uad8c",
    "\uad00\ub78c\uad8c",
)

STRONG_FEE_LABEL_TOKENS = (
    "\uc774\uc6a9\uc694\uae08",
    "\uc694\uae08\uc548\ub0b4",
    "\uad00\ub78c\ub8cc",
    "\uad00\ub78c\uc694\uae08",
    "\uc785\uc7a5\ub8cc",
    "\uc785\uc7a5\uc694\uae08",
    "\uc785\uc7a5\uad8c",
    "\uad00\ub78c\uad8c",
)

COMMON_SECTION_STOP_TOKENS = (
    "\uc624\uc2dc\ub294\uae38",
    "\uc608\uc57d\ud558\uae30",
    "\uad50\uc721",
    "\uacf5\uc9c0",
    "\uc54c\ub9bc",
    "\ubcf4\ub3c4\uc790\ub8cc",
    "\ucc44\uc6a9",
    "\ub354\ubcf4\uae30",
    "\ubaa9\ub85d",
)

HOURS_SECTION_STOP_TOKENS = HOLIDAY_LABEL_TOKENS + FEE_LABEL_TOKENS + COMMON_SECTION_STOP_TOKENS
HOLIDAY_SECTION_STOP_TOKENS = HOURS_LABEL_TOKENS + FEE_LABEL_TOKENS + STOP_LABEL_TOKENS + COMMON_SECTION_STOP_TOKENS
FEE_SECTION_STOP_TOKENS = COMMON_SECTION_STOP_TOKENS + HOURS_LABEL_TOKENS + HOLIDAY_LABEL_TOKENS

FACILITY_NAME_SUFFIXES = (
    "\ub3c4\uc11c\uad00",
    "\uacfc\ud559\uad00",
    "\ubc15\ubb3c\uad00",
    "\ubbf8\uc220\uad00",
    "\ubb38\ud559\uad00",
    "\uc804\uc2dc\uad00",
    "\ubb38\ud654\uad00",
    "\uc218\ubaa9\uc6d0",
    "\uc0dd\ud0dc\uad00",
    "\uae30\ub150\uad00",
    "\uccb4\ud5d8\uad00",
)

BRANCH_USAGE_URL_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("\ubc29\uc9dc\uc720\uae30\ubc15\ubb3c\uad00",),
        ("https://www.dmhm.or.kr/bangjja/content.html?md=0156",),
    ),
    (
        ("\uad11\uc8fc\ubb38\ud559\uad00",),
        ("https://www.gwangju.go.kr/gjlm/contentsView.do?pageId=gjlm16", "https://www.gwangju.go.kr/gjlm/"),
    ),
    (
        ("\uad11\uc8fc\uc5ed\uc0ac\ubbfc\uc18d\ubc15\ubb3c\uad00",),
        ("https://www.gwangju.go.kr/gjhfm/contentsView.do?pageId=gjhfm68", "https://www.gwangju.go.kr/gjhfm/"),
    ),
    (
        ("\uc6a9\uc6b4\ub3c4\uc11c\uad00",),
        ("https://www.donggu.go.kr/dg/lib/contents/671",),
    ),
    (
        ("\uac15\ub989\uc194\ud5a5\uc218\ubaa9\uc6d0", "\uc194\ud5a5\uc218\ubaa9\uc6d0", "\uc720\uc544\uc232\uccb4\ud5d8\uc6d0"),
        ("https://www.gn.go.kr/solhyang/contents.do?key=846", "https://www.gn.go.kr/solhyang/index.do"),
    ),
    (
        ("\uac10\uace8\ub3c4\uc11c\uad00",),
        ("https://lib.ansan.go.kr/gamgol",),
    ),
)

ANSAN_ROUTE_MANAGE_CODES = {
    "jungang": "MA",
    "gamgol": "MD",
    "gwansan": "MB",
    "seongpo": "MC",
}

ANSAN_USE_INFO_STATIC_INDEXES = {
    "MA": (0, 1),
    "MD": (18, 19),
    "MB": (33, 34),
    "MC": (48, 49),
}

FACILITY_NAME_RE = re.compile(
    r"[0-9A-Za-z\uac00-\ud7a3\u00b7_-]{2,40}(?:"
    + "|".join(re.escape(suffix) for suffix in FACILITY_NAME_SUFFIXES)
    + r")"
)


@dataclass
class LibraryUsageInfo:
    operating_hours: str = ""
    regular_holiday: str = ""
    admission_fee: str = ""
    source_url: str = ""
    visited_urls: list[str] = field(default_factory=list)
    candidate_urls: list[str] = field(default_factory=list)
    snippets: dict[str, list[str]] = field(default_factory=lambda: {"hours": [], "holiday": [], "fee": []})
    context_score: int = 0

    def has_data(self) -> bool:
        return bool(self.operating_hours or self.regular_holiday or self.admission_fee)

    def score(self) -> int:
        score = 0
        if self.operating_hours:
            score += 4
        if self.regular_holiday:
            score += 4
        if self.admission_fee:
            score += 3
        if self.source_url and any(token in compact_text(self.source_url) for token in map(compact_text, USAGE_LINK_KEYWORDS)):
            score += 1
        score += self.context_score
        return score

    def as_basic_info(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "visited_urls": self.visited_urls[:10],
            "candidate_urls": self.candidate_urls[:20],
            "snippets": self.snippets,
            "admission_fee": self.admission_fee,
        }


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", "\n").replace("\t", " ")
    text = re.sub(r"\s*\n+\s*", " / ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", clean_text(value)).lower()


def comparable_text(value: Any) -> str:
    text = clean_text(value).lower().replace("\uc774\uc804 \ub2e4\uc74c", "").replace("\uc774\uc804\ub2e4\uc74c", "")
    return re.sub(r"[\s:：/|,·*()\[\]\-~]+", "", text)


def clean_snippet_noise(value: str) -> str:
    text = clean_text(value)
    text = re.sub(r"\bkeyboard_arrow_(?:left|right|up|down)\b", " ", text)
    text = re.sub(r"(^|[:：/]\s*)\uc774\uc804\s+\ub2e4\uc74c\s+", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*/\s*/\s*", " / ", text)
    return text.rstrip(" /")


def normalize_url(base_url: str, href: Any) -> str:
    text = clean_text(href)
    if not text or text.startswith(("#", "javascript:", "mailto:", "tel:")):
        return ""
    absolute = urljoin(base_url, text)
    absolute = urldefrag(absolute)[0]
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if re.search(r"\.(?:jpg|jpeg|png|gif|webp|svg|pdf|hwp|hwpx|doc|docx|xls|xlsx|ppt|pptx|zip)(?:$|\?)", parsed.path.lower()):
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", parsed.query, ""))


def site_key(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def same_site(left: str, right: str) -> bool:
    return bool(left and right and site_key(left) == site_key(right))


def root_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def is_landing_page_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "/").rstrip("/")
    return path in {"", "/", "/index", "/index.asp", "/index.do", "/main", "/main.do", "/library"}


def is_probable_redirect_page(soup: BeautifulSoup) -> bool:
    for meta in soup.select("meta[http-equiv]"):
        if clean_text(meta.get("http-equiv")).lower() == "refresh":
            return True
    script_text = "\n".join(script.get_text("\n", strip=True) for script in soup.find_all("script"))
    if "location" not in script_text:
        return False
    return len(lines_from_soup(soup)) <= 8 and len(soup.find_all("a", href=True)) <= 3


def score_usage_link(text: str, url: str) -> int:
    haystack = compact_text(f"{text} {url}")
    compact_label = compact_text(text)
    if not haystack:
        return 0
    if any(compact_text(token) in haystack for token in USAGE_LINK_REJECT_KEYWORDS):
        return 0
    score = 0
    for token in USAGE_LINK_KEYWORDS:
        if compact_text(token) in haystack:
            score += 3 if token in {
                "\uc774\uc6a9\uc2dc\uac04",
                "\uc6b4\uc601\uc2dc\uac04",
                "\uad00\ub78c\uc2dc\uac04",
                "\ud734\uad00\uc77c",
                "\ud734\uad00",
                "\uc774\uc6a9\uc694\uae08",
                "\uad00\ub78c\ub8cc",
                "\uc785\uc7a5\ub8cc",
            } else 2
    if compact_label in {compact_text("\uc774\uc6a9\uc548\ub0b4"), compact_text("\uad00\ub78c\uc548\ub0b4")}:
        score += 3
    if any(token in haystack for token in ("tourguide", "viewguide", "guideuse", "usetim")):
        score += 2
    url_lower = url.lower()
    if "guide" in url_lower or re.search(r"use(?:time|guide|info|hour|notice)", url_lower):
        score += 1
    return score


def visible_text(soup: BeautifulSoup) -> str:
    cloned = BeautifulSoup(str(soup), "lxml")
    for node in cloned.select("script, style, noscript"):
        node.extract()
    main = (
        cloned.select_one("main")
        or cloned.select_one("#contents")
        or cloned.select_one("#content")
        or cloned.select_one(".contents")
        or cloned.select_one(".content")
        or cloned.body
        or cloned
    )
    return main.get_text("\n", strip=True)


def lines_from_soup(soup: BeautifulSoup) -> list[str]:
    lines = []
    for line in visible_text(soup).splitlines():
        text = clean_text(line)
        if text:
            lines.append(text)
    return lines


def branch_name_aliases(branch_name: str) -> set[str]:
    text = clean_text(branch_name)
    if not text:
        return set()
    variants = {text}
    no_paren = re.sub(r"\([^)]*\)", "", text).strip()
    if no_paren:
        variants.add(no_paren)
    stripped = re.sub(
        r"^(?:서울특별시교육청|경기도교육청|경상남도교육청|전라남도교육청|강원특별자치도교육청|광주광역시교육청|대구광역시립|인천광역시립|부산광역시립|수성구립|구립|시립|군립|공립)\s*",
        "",
        no_paren or text,
    ).strip()
    if stripped:
        variants.add(stripped)
    for part in re.split(r"[\s/·,]+", no_paren or text):
        part = clean_text(part)
        if any(suffix in part for suffix in FACILITY_NAME_SUFFIXES) and len(part) >= 4:
            variants.add(part)
    return {compact_text(value) for value in variants if len(compact_text(value)) >= 4}


def text_has_branch_alias(text: str, branch_name: str) -> bool:
    compact = compact_text(text)
    aliases = branch_name_aliases(branch_name)
    return bool(compact and aliases and any(alias in compact for alias in aliases))


def branch_usage_url_candidates(branch_name: str) -> list[str]:
    compact_branch = compact_text(branch_name)
    aliases = branch_name_aliases(branch_name)
    candidates: list[str] = []
    if not compact_branch and not aliases:
        return candidates
    for names, urls in BRANCH_USAGE_URL_RULES:
        rule_aliases = {compact_text(name) for name in names}
        matched = any(
            alias in compact_branch
            or compact_branch in alias
            or any(alias in branch_alias or branch_alias in alias for branch_alias in aliases)
            for alias in rule_aliases
        )
        if not matched:
            continue
        for url in urls:
            add_unique_url(candidates, url, limit=10)
    return candidates


def is_branch_usage_url(url: str, branch_name: str) -> bool:
    normalized = normalize_url("", url)
    return bool(normalized and normalized in {normalize_url("", candidate) for candidate in branch_usage_url_candidates(branch_name)})


def branch_context_soup(soup: BeautifulSoup, branch_name: str) -> BeautifulSoup | None:
    aliases = branch_name_aliases(branch_name)
    if not aliases:
        return None
    lines = lines_from_soup(soup)
    branch_index = next((index for index, line in enumerate(lines) if any(alias in compact_text(line) for alias in aliases)), -1)
    if branch_index < 0:
        return None
    if contains_any(lines[branch_index], PARKING_SECTION_TOKENS):
        return None

    selected: list[str] = []
    start = branch_index
    for index in range(start, min(len(lines), branch_index + 80)):
        line = lines[index]
        compact = compact_text(line)
        other_facility_heading = (
            index > branch_index + 3
            and not any(alias in compact for alias in aliases)
            and len(line) <= 80
            and FACILITY_NAME_RE.search(line)
        )
        if other_facility_heading:
            break
        selected.append(line)

    if len(selected) < 3:
        return None
    body = "\n".join(f"<p>{html.escape(line)}</p>" for line in selected)
    return BeautifulSoup(f"<main>{body}</main>", "lxml")


def bounded(value: str, limit: int = 220) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    separator = " / "
    cut_at = text.rfind(separator, 0, limit)
    if cut_at >= max(40, int(limit * 0.55)):
        return text[:cut_at].strip()
    return text[:limit].strip().rstrip(" ,./:")


def add_unique(values: list[str], value: str, limit: int = 8) -> None:
    text = bounded(value)
    if not re.match(r"https?://", text, flags=re.IGNORECASE):
        text = clean_snippet_noise(text)
    if not text:
        return
    compact = comparable_text(text)
    if len(compact) < 3:
        return
    for index, existing in enumerate(values):
        existing_compact = comparable_text(existing)
        if compact == existing_compact:
            return
        if len(compact) >= 12 and compact in existing_compact:
            if len(existing_compact) > len(compact) * 2:
                values[index] = text
            return
        if len(existing_compact) >= 12 and existing_compact in compact:
            return
    if len(values) < limit:
        values.append(text)


def add_unique_url(values: list[str], value: str, limit: int = 20) -> None:
    text = bounded(value, limit=2000)
    if not text:
        return
    compact = compact_text(text)
    if len(compact) < 8:
        return
    if any(compact == compact_text(existing) for existing in values):
        return
    if len(values) < limit:
        values.append(text)


def contains_any(text: str, tokens: Iterable[str]) -> bool:
    compact = compact_text(text)
    return any(compact_text(token) in compact for token in tokens)


def looks_like_hours(value: str) -> bool:
    text = clean_text(value)
    if not text:
        return False
    if contains_any(text, HOURS_REJECT_TOKENS):
        return False
    if not re.search(r"\d{1,2}\s*(?::\s*\d{2}|시)\s*\d{0,2}", text):
        return False
    if re.search(r"\d{1,2}\s*(?::|시)\s*\d{0,2}\s*(?:~|-|\u223c|\u2013|부터|까지)", text):
        return True
    return contains_any(
        text,
        (
            "\ud3c9\uc77c",
            "\uc8fc\ub9d0",
            "\ud1a0\uc694\uc77c",
            "\uc77c\uc694\uc77c",
            "\uacf5\ud734\uc77c",
            "\uc790\ub8cc\uc2e4",
            "\uc5f4\ub78c\uc2e4",
            "\uc5b4\ub9b0\uc774\uc790\ub8cc\uc2e4",
        ),
    ) and bool(re.search(r"\d{1,2}", text))


def looks_like_holiday(value: str) -> bool:
    text = clean_text(value)
    if not text:
        return False
    if contains_any(text, HOLIDAY_REJECT_TOKENS):
        return False
    compact = compact_text(text)
    if len(compact) < 16 and compact.endswith(("\uc740", "\ub294", "\uc744", "\ub97c", ",")):
        return False
    if re.search(r"\.(?:jpg|jpeg|png|gif|webp|pdf|hwp|hwpx)(?:$|\s)", text.lower()):
        return False
    if compact in {"\ud734\uad00:\uc77c", "\ud734\uad00\uc77c:\uc548\ub0b4", "\ud734\uad00\uc77c:\uc548\ub0b4\ud45c\uc785\ub2c8\ub2e4"}:
        return False
    has_day_or_rule = contains_any(
        text,
        (
            "\ub9e4\uc8fc",
            "\uccab\uc9f8",
            "\ub458\uc9f8",
            "\uc14b\uc9f8",
            "\ub137\uc9f8",
            "\ub2e4\uc12f\uc9f8",
            "\uc6d4\uc694\uc77c",
            "\ud654\uc694\uc77c",
            "\uc218\uc694\uc77c",
            "\ubaa9\uc694\uc77c",
            "\uae08\uc694\uc77c",
            "\ud1a0\uc694\uc77c",
            "\uc77c\uc694\uc77c",
            "\ubc95\uc815\uacf5\ud734\uc77c",
            "\uad00\uacf5\uc11c \uacf5\ud734\uc77c",
            "\uad00\uacf5\uc11c\uc758 \uacf5\ud734\uc77c",
            "\uc815\ubd80\uc9c0\uc815 \uacf5\ud734\uc77c",
            "\uad6d\uacbd\uc77c",
            "1\uc6d4 1\uc77c",
            "\uc2e0\uc815",
            "\uc124\ub0a0",
            "\uc124 \uc5f0\ud734",
            "\uba85\uc808\uc5f0\ud734",
            "\uba85\uc808 \uc5f0\ud734",
            "\ucd94\uc11d",
            "\uacf5\ud734\uc77c \ub2e4\uc74c\ub0a0",
            "\uad00\uc7a5\uc774 \uc9c0\uc815",
            "\ub3c4\uc11c\uad00 \uc0ac\uc815",
        ),
    )
    if has_day_or_rule:
        return True
    return False


def looks_like_fee(value: str) -> bool:
    text = clean_text(value)
    if not text:
        return False
    if contains_any(text, PARKING_SECTION_TOKENS) and not contains_any(text, ADMISSION_FEE_LABEL_TOKENS):
        return False
    if contains_any(text, NON_PARKING_FEE_REJECT_TOKENS):
        return False
    compact = compact_text(text)
    if len(compact) > 900:
        return False
    if contains_any(text, ("\ubb34\ub8cc", "\uc720\ub8cc")):
        return True
    if contains_any(text, ("\uba74\uc81c", "\uac10\uba74")) and re.search(r"\d[\d,.\s]*(?:\uc6d0|\ub9cc\uc6d0)|\ubb34\ub8cc|\uc720\ub8cc", text):
        return True
    if contains_any(text, FEE_LABEL_TOKENS) and re.search(r"\d[\d,.\s]*(?:\uc6d0|\ub9cc\uc6d0)", text):
        return True
    if re.search(r"(?:\uc5b4\ub978|\uc131\uc778|\uccad\uc18c\ub144|\uc5b4\ub9b0\uc774|\uc720\uc544|\uac1c\uc778|\ub2e8\uccb4)\s*[:：]?\s*\d[\d,.\s]*(?:\uc6d0|\ub9cc\uc6d0)", text):
        return True
    return False


def looks_like_holiday_rule(value: str) -> bool:
    if contains_any(value, HOLIDAY_REJECT_TOKENS):
        return False
    if re.search(r"(?:\uc6d4|\ud654|\uc218|\ubaa9|\uae08|\ud1a0|\uc77c)\uc694\uc77c\s*(?:~|-|\u223c|\u2013)\s*(?:\uc6d4|\ud654|\uc218|\ubaa9|\uae08|\ud1a0|\uc77c)\uc694\uc77c", clean_text(value)) and not contains_any(value, HOLIDAY_LABEL_TOKENS):
        return False
    compact = compact_text(value)
    if len(compact) < 16 and compact.endswith(("\uc740", "\ub294", "\uc744", "\ub97c", ",")):
        return False
    return contains_any(
        value,
        (
            "\ub9e4\uc8fc",
            "\ubc95\uc815\uacf5\ud734\uc77c",
            "\uad00\uacf5\uc11c \uacf5\ud734\uc77c",
            "\uad00\uacf5\uc11c\uc758 \uacf5\ud734\uc77c",
            "\uc815\ubd80\uc9c0\uc815 \uacf5\ud734\uc77c",
            "\uad6d\uacbd\uc77c",
            "\uad6d\uac00\uc9c0\uc815 \uacf5\ud734\uc77c",
            "\uc815\uae30\ud734\uad00",
            "\uad00\uc7a5 \uacf5\uace0\uc77c",
            "1\uc6d4 1\uc77c",
            "\uc2e0\uc815",
            "\uc124\ub0a0",
            "\uc124 \uc5f0\ud734",
            "\uba85\uc808\uc5f0\ud734",
            "\uba85\uc808 \uc5f0\ud734",
            "\ucd94\uc11d",
            "\uacf5\ud734\uc77c \ub2e4\uc74c\ub0a0",
        ),
    )


def holiday_rule_fragments(value: str) -> list[str]:
    fragments: list[str] = []
    for part in re.split(r"\s*/\s*|\s{2,}|(?:\s+)(?=\ub9e4\uc8fc|\ubc95\uc815\uacf5\ud734\uc77c|\uad00\uacf5\uc11c \uacf5\ud734\uc77c|\uad6d\uac00\uc9c0\uc815 \uacf5\ud734\uc77c)", clean_text(value)):
        text = clean_text(part)
        if looks_like_holiday_rule(text) and not looks_like_hours(text):
            fragments.append(text)
    return fragments


def remove_holiday_rule_fragments(value: str) -> str:
    parts = []
    for part in re.split(r"\s*/\s*", clean_text(value)):
        text = clean_text(part)
        if not text:
            continue
        if looks_like_holiday_rule(text) and not looks_like_hours(text):
            continue
        parts.append(text)
    return " / ".join(parts)


def remove_non_hours_fragments(value: str) -> str:
    parts = []
    for part in re.split(r"\s*/\s*", clean_text(value)):
        text = clean_text(part)
        if not text:
            continue
        if contains_any(text, HOURS_REJECT_TOKENS):
            continue
        parts.append(text)
    return " / ".join(parts)


def trim_at_section_stop(value: str, stop_tokens: Iterable[str], min_position: int = 12) -> str:
    text = clean_text(value)
    cut_at: int | None = None
    for token in stop_tokens:
        token_text = clean_text(token)
        if not token_text:
            continue
        position = text.find(token_text)
        if position >= min_position and (cut_at is None or position < cut_at):
            cut_at = position
    if cut_at is None:
        return text
    return clean_text(text[:cut_at])


def lines_context(lines: list[str], start: int, *, lookback: int = 0, lookahead: int = 0) -> str:
    begin = max(0, start - lookback)
    end = min(len(lines), start + lookahead + 1)
    return clean_text(" / ".join(lines[begin:end]))


def is_parking_section(lines: list[str], index: int) -> bool:
    return contains_any(lines_context(lines, index, lookback=20), PARKING_SECTION_TOKENS)


def section_from_lines(
    lines: list[str],
    index: int,
    *,
    max_lines: int = 16,
    stop_tokens: Iterable[str] = (),
) -> str:
    parts: list[str] = []
    for current in range(index, min(len(lines), index + max_lines)):
        line = lines[current]
        if current > index and contains_any(line, stop_tokens):
            break
        parts.append(line)
    return clean_text(" / ".join(parts))


def split_label_value(text: str, label_tokens: tuple[str, ...]) -> tuple[str, str]:
    compact = clean_text(text)
    for label in label_tokens:
        match = re.search(rf"({re.escape(label)})\s*[:：]?\s*(.+)$", compact)
        if match:
            return clean_text(match.group(1)), clean_text(match.group(2))
    return "", ""


def iter_table_pairs(soup: BeautifulSoup) -> Iterable[tuple[str, str]]:
    for row in soup.select("tr"):
        headers = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("th")]
        values = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
        if headers and values:
            if len(headers) == 1:
                yield headers[0], " / ".join(values)
            else:
                for index, header in enumerate(headers):
                    if index < len(values):
                        yield header, values[index]
        elif len(values) >= 2:
            yield values[0], " / ".join(values[1:])


def iter_definition_pairs(soup: BeautifulSoup) -> Iterable[tuple[str, str]]:
    for dt in soup.select("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            yield clean_text(dt.get_text(" ", strip=True)), clean_text(dd.get_text(" ", strip=True))
    for item in soup.select("li, p, div"):
        strong = item.find(["strong", "b", "em"])
        if not strong:
            continue
        label = clean_text(strong.get_text(" ", strip=True)).rstrip(":：")
        if not label:
            continue
        value = clean_text(item.get_text(" ", strip=True).replace(strong.get_text(" ", strip=True), "", 1))
        if value:
            yield label, value


def collect_from_pairs(soup: BeautifulSoup) -> tuple[list[str], list[str], list[str]]:
    hours: list[str] = []
    holidays: list[str] = []
    fees: list[str] = []
    for label, value in list(iter_table_pairs(soup)) + list(iter_definition_pairs(soup)):
        hours_raw_value = trim_at_section_stop(value, HOURS_SECTION_STOP_TOKENS)
        fee_raw_value = trim_at_section_stop(value, FEE_SECTION_STOP_TOKENS)
        hours_value = remove_non_hours_fragments(remove_holiday_rule_fragments(hours_raw_value))
        combined = clean_text(f"{label}: {hours_value or hours_raw_value}")
        if contains_any(label, HOURS_LABEL_TOKENS) and (looks_like_hours(hours_raw_value) or looks_like_hours(combined)):
            add_unique(hours, combined)
            for fragment in holiday_rule_fragments(value):
                add_unique(holidays, f"\ud734\uad00\uc77c: {fragment}")
        if contains_any(label, HOLIDAY_LABEL_TOKENS):
            _, embedded_holiday_value = split_label_value(value, HOLIDAY_LABEL_TOKENS)
            holiday_raw_value = trim_at_section_stop(embedded_holiday_value or value, HOLIDAY_SECTION_STOP_TOKENS)
            holiday_combined = clean_text(f"{label}: {holiday_raw_value}")
            if looks_like_holiday(holiday_raw_value) or looks_like_holiday(holiday_combined):
                add_unique(holidays, holiday_combined)
        if contains_any(label, FEE_LABEL_TOKENS) and (looks_like_fee(fee_raw_value) or looks_like_fee(clean_text(f"{label}: {fee_raw_value}"))):
            add_unique(fees, clean_text(f"{label}: {fee_raw_value}"))
    return hours, holidays, fees


def collect_from_lines(soup: BeautifulSoup) -> tuple[list[str], list[str], list[str]]:
    hours: list[str] = []
    holidays: list[str] = []
    fees: list[str] = []
    lines = lines_from_soup(soup)
    skip_fee_until = -1
    for index, line in enumerate(lines):
        parking_section = is_parking_section(lines, index) and not contains_any(line, ADMISSION_FEE_LABEL_TOKENS)
        context = clean_text(" / ".join(lines[index : index + 6]))
        fee_context = section_from_lines(
            lines,
            index,
            max_lines=80 if contains_any(line, STRONG_FEE_LABEL_TOKENS) else 16,
            stop_tokens=tuple(FEE_SECTION_STOP_TOKENS) + PARKING_SECTION_TOKENS,
        )
        if contains_any(line, HOURS_LABEL_TOKENS) and not parking_section:
            label, value = split_label_value(line, HOURS_LABEL_TOKENS)
            candidate = clean_text(f"{label}: {value}") if value else trim_at_section_stop(context, HOURS_SECTION_STOP_TOKENS)
            if looks_like_hours(candidate):
                add_unique(hours, candidate)
        if contains_any(line, HOLIDAY_LABEL_TOKENS) and not parking_section:
            label, value = split_label_value(line, HOLIDAY_LABEL_TOKENS)
            candidate = clean_text(f"{label}: {value}") if value else line
            if not looks_like_holiday(candidate):
                candidate = line
            if looks_like_holiday(candidate):
                add_unique(holidays, candidate)
        elif not parking_section and looks_like_holiday_rule(line) and not looks_like_hours(line):
            add_unique(holidays, f"\ud734\uad00\uc77c: {line}")
        if index < skip_fee_until and contains_any(line, FEE_LABEL_TOKENS):
            continue
        if contains_any(line, FEE_LABEL_TOKENS) and not parking_section:
            label, value = split_label_value(line, FEE_LABEL_TOKENS)
            candidate = clean_text(f"{label}: {value}") if value else ""
            if not looks_like_fee(candidate):
                candidate = trim_at_section_stop(fee_context, FEE_SECTION_STOP_TOKENS)
            if not looks_like_fee(candidate):
                candidate = clean_text(" / ".join(lines[index : index + 2]))
            if looks_like_fee(candidate):
                add_unique(fees, candidate)
                if contains_any(line, STRONG_FEE_LABEL_TOKENS):
                    skip_fee_until = index + 160
    return hours, holidays, fees


def summarize(values: list[str]) -> str:
    deduped: list[str] = []
    for value in values:
        add_unique(deduped, value, limit=5)
    return " / ".join(deduped)


def extract_library_usage_info(soup: BeautifulSoup, source_url: str, branch_name: str = "") -> LibraryUsageInfo:
    scoped_soup = soup if score_usage_link("", source_url) > 0 else (branch_context_soup(soup, branch_name) or soup)
    pair_hours, pair_holidays, pair_fees = collect_from_pairs(scoped_soup)
    line_hours, line_holidays, line_fees = collect_from_lines(scoped_soup)
    if scoped_soup is not soup and not any((pair_hours, pair_holidays, pair_fees, line_hours, line_holidays, line_fees)):
        pair_hours, pair_holidays, pair_fees = collect_from_pairs(soup)
        line_hours, line_holidays, line_fees = collect_from_lines(soup)
    hours = pair_hours + [value for value in line_hours if value not in pair_hours]
    holidays = pair_holidays + [value for value in line_holidays if value not in pair_holidays]
    fees = pair_fees + [value for value in line_fees if value not in pair_fees]
    page_text = compact_text(visible_text(soup))
    compact_branch = compact_text(branch_name)
    context_score = 0
    if compact_branch and compact_branch in page_text:
        context_score += 4
    elif compact_branch:
        visible = visible_text(soup)
        facility_names = {compact_text(match.group(0)) for match in FACILITY_NAME_RE.finditer(visible)}
        if facility_names:
            context_score -= 2
    if is_branch_usage_url(source_url, branch_name):
        context_score += 4
    if any(token in compact_text(source_url) for token in ("camping", "reserve", "lecture")):
        context_score -= 4
    return LibraryUsageInfo(
        operating_hours=summarize(hours),
        regular_holiday=summarize(holidays),
        admission_fee=summarize(fees),
        source_url=source_url,
        snippets={"hours": hours[:8], "holiday": holidays[:8], "fee": fees[:8]},
        context_score=context_score,
    )


def fetch_daegu_reservation_detail_soup(s: requests.Session, url: str, timeout: int) -> BeautifulSoup | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "yeyak.daegu.go.kr":
        return None
    match = re.search(r"/expr/detail/([^/]+)/([^/?#]+)", parsed.path)
    if not match:
        return None

    api_url = f"{parsed.scheme or 'https'}://{parsed.netloc}/api/v1/res/expr/user/expr-prod-detail"
    try:
        response = s.post(
            api_url,
            json={"instId": match.group(1), "ftrPrgrmId": match.group(2)},
            timeout=timeout,
        )
    except RequestException:
        return None
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None

    html_parts = [
        f"<h1>{html.escape(clean_text(data.get('instNm')))}</h1>",
        f"<p>{html.escape(clean_text(data.get('ftrPrgrmNm')))}</p>",
    ]
    phone = clean_text(data.get("inqryTelNo"))
    if phone:
        html_parts.append(f"<p>\ubb38\uc758: {html.escape(phone)}</p>")
    address = clean_text(" ".join(part for part in [data.get("addr"), data.get("daddr")] if clean_text(part)))
    if address:
        html_parts.append(f"<p>\uc8fc\uc18c: {html.escape(address)}</p>")
    for key in ("dtlCn", "fcltItr", "rcptText"):
        value = data.get(key)
        if value:
            html_parts.append(str(value))
    return BeautifulSoup(f"<main>{''.join(html_parts)}</main>", "lxml")


def fetch_gwacheon_science_guide_soup(s: requests.Session, url: str, timeout: int) -> BeautifulSoup | None:
    parsed = urlparse(url)
    if site_key(url) != "sciencecenter.go.kr":
        return None
    guide_content_ids = {
        "/gnsm/guide/private": "55",
        "/gnsm/guide/group": "56",
    }
    content_id = guide_content_ids.get((parsed.path or "").rstrip("/"))
    if not content_id:
        return None
    api_url = f"https://www.sciencecenter.go.kr/gnsm-api/api/v1/contents/{content_id}"
    try:
        response = request_page(s, api_url, timeout=timeout)
    except RequestException:
        return None
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    content = data.get("content") if isinstance(data, dict) else None
    if not content:
        return None
    return BeautifulSoup(str(content), "lxml")


def ansan_manage_code_from_url(url: str) -> str:
    parsed = urlparse(url)
    if site_key(url) != "lib.ansan.go.kr":
        return ""
    route = (parsed.path or "/").strip("/").split("/", 1)[0].lower()
    return ANSAN_ROUTE_MANAGE_CODES.get(route, "")


def extract_vue_static_functions(script_text: str, module_marker: str) -> list[str]:
    start = script_text.find(module_marker)
    if start < 0:
        return []
    array_start = script_text.find("r=[", start)
    if array_start < 0:
        return []
    functions: list[str] = []
    index = array_start + 3
    while index < len(script_text):
        if script_text.startswith("function()", index):
            brace = script_text.find("{", index)
            if brace < 0:
                break
            depth = 0
            quote = ""
            escaped = False
            cursor = brace
            while cursor < len(script_text):
                char = script_text[cursor]
                if quote:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        quote = ""
                else:
                    if char in {"'", '"'}:
                        quote = char
                    elif char == "{":
                        depth += 1
                    elif char == "}":
                        depth -= 1
                        if depth == 0:
                            functions.append(script_text[index : cursor + 1])
                            index = cursor + 1
                            break
                cursor += 1
            else:
                break
        elif script_text[index] == "]":
            break
        else:
            index += 1
    return functions


def vue_text_values(function_text: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r't\._v\("((?:[^"\\]|\\.)*)"\)', function_text):
        raw_value = match.group(1)
        try:
            value = json.loads(f'"{raw_value}"')
        except ValueError:
            value = raw_value.replace('\\"', '"').replace("\\\\", "\\")
        value = clean_text(value)
        if value:
            values.append(value)
    return values


def fetch_ansan_library_usage_soup(s: requests.Session, url: str, timeout: int) -> BeautifulSoup | None:
    manage_code = ansan_manage_code_from_url(url)
    static_indexes = ANSAN_USE_INFO_STATIC_INDEXES.get(manage_code)
    if not static_indexes:
        return None
    try:
        response = request_page(s, root_url(url) or url, timeout=timeout)
    except RequestException:
        return None
    if response.status_code >= 400:
        return None
    soup = BeautifulSoup(response.text, "lxml")
    app_url = ""
    for script in soup.find_all("script", src=True):
        candidate = normalize_url(url, script.get("src"))
        if "/app." in candidate and candidate.endswith(".js"):
            app_url = candidate
            break
    if not app_url:
        return None
    try:
        app_response = request_page(s, app_url, timeout=timeout)
    except RequestException:
        return None
    if app_response.status_code >= 400:
        return None
    content = getattr(app_response, "content", b"")
    if content:
        script_text = content.decode("utf-8", errors="replace")
    else:
        script_text = app_response.text
    functions = extract_vue_static_functions(script_text, "1340:function")
    holiday_index, hours_index = static_indexes
    if max(static_indexes) >= len(functions):
        return None
    holidays = [
        value
        for value in vue_text_values(functions[holiday_index])
        if compact_text(value) not in {compact_text("\ud734\uad00\uc77c")}
    ]
    hour_skip_values = {
        compact_text("\uc6b4\uc601\uc2dc\uac04"),
        compact_text("\uc2e4\ubcc4"),
        compact_text("\uc774\uc6a9\uc2dc\uac04"),
        compact_text("\ud558\uc808\uae30(3\uc6d4~10\uc6d4)"),
        compact_text("\ub3d9\uc808\uae30(11\uc6d4~2\uc6d4)"),
        compact_text("\uc815\uae30\ud734\uad00\uc77c"),
        compact_text("\uc6b4\uc601\uc548\ud568"),
    }
    hours = [value for value in vue_text_values(functions[hours_index]) if compact_text(value) not in hour_skip_values]
    if not holidays and not hours:
        return None
    html_parts = []
    if holidays:
        html_parts.append(f"<p>\ud734\uad00\uc77c: {html.escape(' / '.join(holidays))}</p>")
    if hours:
        html_parts.append(f"<p>\uc6b4\uc601\uc2dc\uac04: {html.escape(' / '.join(hours))}</p>")
    return BeautifulSoup(f"<main>{''.join(html_parts)}</main>", "lxml")


def request_page(s: requests.Session, url: str, timeout: int) -> requests.Response:
    headers = {"Referer": root_url(url)} if root_url(url) else None
    kwargs: dict[str, Any] = {"timeout": timeout}
    if headers:
        kwargs["headers"] = headers
    try:
        return s.get(url, **kwargs)
    except TypeError:
        kwargs.pop("headers", None)
        return s.get(url, **kwargs)


def fetch_soup(s: requests.Session, url: str, timeout: int) -> BeautifulSoup | None:
    special_soup = fetch_daegu_reservation_detail_soup(s, url, timeout)
    if special_soup is not None:
        return special_soup
    special_soup = fetch_gwacheon_science_guide_soup(s, url, timeout)
    if special_soup is not None:
        return special_soup
    special_soup = fetch_ansan_library_usage_soup(s, url, timeout)
    if special_soup is not None:
        return special_soup
    try:
        response = request_page(s, url, timeout=timeout)
    except RequestException:
        try:
            response = request_page(s, url, timeout=max(timeout, 15))
        except RequestException:
            return None
    if response.status_code >= 400:
        return None
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding
    return BeautifulSoup(response.text, "lxml")


def discover_usage_links(soup: BeautifulSoup, base_url: str, limit: int = 12) -> list[str]:
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = normalize_url(base_url, link.get("href"))
        if not href or href in seen or not same_site(base_url, href):
            continue
        text = clean_text(link.get_text(" ", strip=True))
        score = score_usage_link(text, href)
        if score <= 0:
            continue
        seen.add(href)
        scored.append((score, href))
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [url for _, url in scored[:limit]]


def discover_redirect_links(soup: BeautifulSoup, base_url: str, limit: int = 3) -> list[str]:
    candidates: list[str] = []

    for meta in soup.select("meta[http-equiv]"):
        if clean_text(meta.get("http-equiv")).lower() != "refresh":
            continue
        content = clean_text(meta.get("content"))
        match = re.search(r"url\s*=\s*([^;]+)", content, flags=re.IGNORECASE)
        if match:
            add_unique_url(candidates, normalize_url(base_url, match.group(1).strip("'\"")), limit=limit)

    script_text = "\n".join(script.get_text("\n", strip=True) for script in soup.find_all("script"))
    string_vars = {
        match.group(1): match.group(2)
        for match in re.finditer(r"(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*['\"]([^'\"]*)['\"]", script_text)
    }
    for match in re.finditer(r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", script_text):
        target = normalize_url(base_url, match.group(1))
        if target and same_site(base_url, target):
            add_unique_url(candidates, target, limit=limit)
    for match in re.finditer(r"(?:window\.)?location(?:\.href)?\s*=\s*([A-Za-z_$][\w$]*)\s*\+\s*['\"]([^'\"]+)['\"]", script_text):
        prefix = string_vars.get(match.group(1))
        if prefix is None:
            continue
        target = normalize_url(base_url, prefix + match.group(2))
        if target and same_site(base_url, target):
            add_unique_url(candidates, target, limit=limit)
    for match in re.finditer(r"(?:window\.)?location\.(?:replace|assign)\(\s*['\"]([^'\"]+)['\"]", script_text):
        target = normalize_url(base_url, match.group(1))
        if target and same_site(base_url, target):
            add_unique_url(candidates, target, limit=limit)

    links = [url for url in candidates if url and same_site(base_url, url)]
    host_label = (urlparse(base_url).hostname or "").split(".")[0].lower()

    def redirect_priority(url: str) -> tuple[int, int, str]:
        path = urlparse(url).path.lower()
        if host_label == "mc" and "/mc/" in path:
            return (0, len(url), url)
        if host_label == "portal" and "/portal/" in path:
            return (0, len(url), url)
        if host_label not in {"mc", "portal"} and "/mps/" in path:
            return (0, len(url), url)
        return (1, len(url), url)

    return sorted(links, key=redirect_priority)


def start_url_candidates(urls: Iterable[str], branch_name: str = "") -> list[str]:
    candidates: list[str] = []
    for url in branch_usage_url_candidates(branch_name):
        add_unique_url(candidates, url, limit=20)
    for url in urls:
        normalized = normalize_url("", url)
        if not normalized:
            continue
        add_unique_url(candidates, normalized, limit=20)
        if site_key(normalized) == "sciencecenter.go.kr":
            add_unique_url(candidates, "https://www.sciencecenter.go.kr/gnsm/guide/private", limit=20)
            add_unique_url(candidates, "https://www.sciencecenter.go.kr/gnsm/guide/group", limit=20)
        root = root_url(normalized)
        if root:
            add_unique_url(candidates, root, limit=20)
    return candidates


def make_session() -> requests.Session:
    s = SafeSession()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
    return s


def fetch_library_usage_info(
    urls: Iterable[str],
    *,
    session: requests.Session | None = None,
    timeout: int = 10,
    max_pages: int = 8,
    branch_name: str = "",
) -> LibraryUsageInfo:
    if session is None:
        s: requests.Session = make_session()
    elif isinstance(session, requests.Session):
        s = harden_session(session)
    else:
        # Parser tests use lightweight fake sessions. Production callers use
        # requests.Session/SafeSession instances and are always hardened.
        s = session
    seeds = start_url_candidates(urls, branch_name=branch_name)
    queue: deque[str] = deque(seeds)
    queued = set(seeds)
    visited: list[str] = []
    best = LibraryUsageInfo(candidate_urls=seeds[:])

    while queue and len(visited) < max_pages:
        url = queue.popleft()
        if url in visited:
            continue
        soup = fetch_soup(s, url, timeout=timeout)
        visited.append(url)
        if not soup:
            continue
        info = extract_library_usage_info(soup, url, branch_name=branch_name)
        if is_landing_page_url(url) and score_usage_link("", url) <= 0 and not info.has_data():
            info = LibraryUsageInfo(source_url=url)
        info.visited_urls = visited[:]
        info.candidate_urls = list(queued)[:20]
        if info.score() > best.score():
            best = info
            if (
                best.operating_hours
                and best.regular_holiday
                and best.admission_fee
                and (score_usage_link("", best.source_url) > 0 or is_branch_usage_url(best.source_url, branch_name))
            ):
                break
        if is_probable_redirect_page(soup):
            for link in reversed(discover_redirect_links(soup, url)):
                if link not in queued and len(queued) < 40:
                    queued.add(link)
                    queue.appendleft(link)
        for link in reversed(discover_usage_links(soup, url)):
            if link not in queued and len(queued) < 40:
                queued.add(link)
                queue.appendleft(link)

    best.visited_urls = visited
    best.candidate_urls = list(queued)[:20]
    return best
