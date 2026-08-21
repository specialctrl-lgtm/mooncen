from __future__ import annotations

import html
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.exceptions import RequestException
from urllib3.exceptions import InsecureRequestWarning


urllib3.disable_warnings(InsecureRequestWarning)


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36"

USAGE_LINK_KEYWORDS = (
    "\uc774\uc6a9\uc548\ub0b4",
    "\ub3c4\uc11c\uad00\uc774\uc6a9",
    "\uc774\uc6a9\uc2dc\uac04",
    "\uc6b4\uc601\uc2dc\uac04",
    "\uac1c\uad00\uc2dc\uac04",
    "\uc5f4\ub78c\uc2dc\uac04",
    "\ud734\uad00",
    "\ud734\uad00\uc77c",
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

STOP_LABEL_TOKENS = (
    "\uc8fc\uc18c",
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
)


@dataclass
class LibraryUsageInfo:
    operating_hours: str = ""
    regular_holiday: str = ""
    source_url: str = ""
    visited_urls: list[str] = field(default_factory=list)
    candidate_urls: list[str] = field(default_factory=list)
    snippets: dict[str, list[str]] = field(default_factory=lambda: {"hours": [], "holiday": []})
    context_score: int = 0

    def has_data(self) -> bool:
        return bool(self.operating_hours or self.regular_holiday)

    def score(self) -> int:
        score = 0
        if self.operating_hours:
            score += 4
        if self.regular_holiday:
            score += 4
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


def score_usage_link(text: str, url: str) -> int:
    haystack = compact_text(f"{text} {url}")
    if not haystack:
        return 0
    if any(compact_text(token) in haystack for token in USAGE_LINK_REJECT_KEYWORDS):
        return 0
    score = 0
    for token in USAGE_LINK_KEYWORDS:
        if compact_text(token) in haystack:
            score += 3 if token in {"\uc774\uc6a9\uc2dc\uac04", "\uc6b4\uc601\uc2dc\uac04", "\ud734\uad00\uc77c", "\ud734\uad00"} else 2
    if "guide" in haystack or "use" in haystack or "intro" in haystack:
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
        if "\ub3c4\uc11c\uad00" in part and len(part) >= 4:
            variants.add(part)
    return {compact_text(value) for value in variants if len(compact_text(value)) >= 4}


def text_has_branch_alias(text: str, branch_name: str) -> bool:
    compact = compact_text(text)
    aliases = branch_name_aliases(branch_name)
    return bool(compact and aliases and any(alias in compact for alias in aliases))


def branch_context_soup(soup: BeautifulSoup, branch_name: str) -> BeautifulSoup | None:
    aliases = branch_name_aliases(branch_name)
    if not aliases:
        return None
    lines = lines_from_soup(soup)
    branch_index = next((index for index, line in enumerate(lines) if any(alias in compact_text(line) for alias in aliases)), -1)
    if branch_index < 0:
        return None

    selected: list[str] = []
    start = branch_index
    for index in range(start, min(len(lines), branch_index + 80)):
        line = lines[index]
        compact = compact_text(line)
        other_library_heading = (
            index > branch_index + 3
            and not any(alias in compact for alias in aliases)
            and len(line) <= 80
            and re.search(r"[0-9A-Za-z\uac00-\ud7a3\u00b7_-]{2,30}\ub3c4\uc11c\uad00", line)
        )
        if other_library_heading:
            break
        selected.append(line)

    if len(selected) < 3:
        return None
    body = "\n".join(f"<p>{html.escape(line)}</p>" for line in selected)
    return BeautifulSoup(f"<main>{body}</main>", "lxml")


def bounded(value: str, limit: int = 220) -> str:
    text = clean_text(value)
    return text[:limit].rstrip() if len(text) > limit else text


def add_unique(values: list[str], value: str, limit: int = 8) -> None:
    text = bounded(value)
    if not text:
        return
    compact = compact_text(text)
    if len(compact) < 3:
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
            "\uc815\ubd80\uc9c0\uc815 \uacf5\ud734\uc77c",
            "\uad6d\uacbd\uc77c",
            "\uc2e0\uc815",
            "\uc124\ub0a0",
            "\uc124 \uc5f0\ud734",
            "\ucd94\uc11d",
            "\uc784\uc2dc\ud734\uad00",
            "\uad00\uc7a5\uc774 \uc9c0\uc815",
            "\ub3c4\uc11c\uad00 \uc0ac\uc815",
        ),
    )
    if has_day_or_rule:
        return True
    return False


def looks_like_holiday_rule(value: str) -> bool:
    compact = compact_text(value)
    if len(compact) < 16 and compact.endswith(("\uc740", "\ub294", "\uc744", "\ub97c", ",")):
        return False
    return contains_any(
        value,
        (
            "\ub9e4\uc8fc",
            "\ubc95\uc815\uacf5\ud734\uc77c",
            "\uad00\uacf5\uc11c \uacf5\ud734\uc77c",
            "\uc815\ubd80\uc9c0\uc815 \uacf5\ud734\uc77c",
            "\uad6d\uacbd\uc77c",
            "\uad6d\uac00\uc9c0\uc815 \uacf5\ud734\uc77c",
            "\uc815\uae30\ud734\uad00",
            "\uc784\uc2dc\ud734\uad00",
            "\uad00\uc7a5 \uacf5\uace0\uc77c",
            "\uc2e0\uc815",
            "\uc124\ub0a0",
            "\uc124 \uc5f0\ud734",
            "\ucd94\uc11d",
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


def collect_from_pairs(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    hours: list[str] = []
    holidays: list[str] = []
    for label, value in list(iter_table_pairs(soup)) + list(iter_definition_pairs(soup)):
        hours_value = remove_non_hours_fragments(remove_holiday_rule_fragments(value))
        combined = clean_text(f"{label}: {hours_value or value}")
        if contains_any(label, HOURS_LABEL_TOKENS) and (looks_like_hours(value) or looks_like_hours(combined)):
            add_unique(hours, combined)
            for fragment in holiday_rule_fragments(value):
                add_unique(holidays, f"\ud734\uad00\uc77c: {fragment}")
        if contains_any(label, HOLIDAY_LABEL_TOKENS) and (looks_like_holiday(value) or looks_like_holiday(combined)):
            add_unique(holidays, combined)
    return hours, holidays


def collect_from_lines(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    hours: list[str] = []
    holidays: list[str] = []
    lines = lines_from_soup(soup)
    for index, line in enumerate(lines):
        context = clean_text(" / ".join(lines[index : index + 4]))
        if contains_any(line, HOURS_LABEL_TOKENS):
            label, value = split_label_value(line, HOURS_LABEL_TOKENS)
            candidate = clean_text(f"{label}: {value}") if value else context
            if looks_like_hours(candidate):
                add_unique(hours, candidate)
        if contains_any(line, HOLIDAY_LABEL_TOKENS):
            label, value = split_label_value(line, HOLIDAY_LABEL_TOKENS)
            candidate = clean_text(f"{label}: {value}") if value else line
            if looks_like_holiday(candidate):
                add_unique(holidays, candidate)
        elif looks_like_holiday_rule(line) and not looks_like_hours(line):
            add_unique(holidays, f"\ud734\uad00\uc77c: {line}")
    return hours, holidays


def summarize(values: list[str]) -> str:
    deduped: list[str] = []
    for value in values:
        add_unique(deduped, value, limit=5)
    return " / ".join(deduped)


def extract_library_usage_info(soup: BeautifulSoup, source_url: str, branch_name: str = "") -> LibraryUsageInfo:
    scoped_soup = branch_context_soup(soup, branch_name) or soup
    pair_hours, pair_holidays = collect_from_pairs(scoped_soup)
    line_hours, line_holidays = collect_from_lines(scoped_soup)
    hours = pair_hours + [value for value in line_hours if value not in pair_hours]
    holidays = pair_holidays + [value for value in line_holidays if value not in pair_holidays]
    page_text = compact_text(visible_text(soup))
    compact_branch = compact_text(branch_name)
    context_score = 0
    if compact_branch and compact_branch in page_text:
        context_score += 4
    elif compact_branch:
        visible = visible_text(soup)
        library_names = {compact_text(match) for match in re.findall(r"[0-9A-Za-z\uac00-\ud7a3\u00b7_-]{2,30}\ub3c4\uc11c\uad00", visible)}
        if library_names:
            context_score -= 2
    if any(token in compact_text(source_url) for token in ("camping", "reserve", "lecture")):
        context_score -= 4
    return LibraryUsageInfo(
        operating_hours=summarize(hours),
        regular_holiday=summarize(holidays),
        source_url=source_url,
        snippets={"hours": hours[:8], "holiday": holidays[:8]},
        context_score=context_score,
    )


def fetch_soup(s: requests.Session, url: str, timeout: int) -> BeautifulSoup | None:
    try:
        response = s.get(url, timeout=timeout)
    except RequestException:
        try:
            response = s.get(url, timeout=max(timeout, 15), verify=False)
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


def start_url_candidates(urls: Iterable[str]) -> list[str]:
    candidates: list[str] = []
    for url in urls:
        normalized = normalize_url("", url)
        if not normalized:
            continue
        add_unique(candidates, normalized, limit=20)
        root = root_url(normalized)
        if root:
            add_unique(candidates, root, limit=20)
    return candidates


def make_session() -> requests.Session:
    s = requests.Session()
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
    s = session or make_session()
    seeds = start_url_candidates(urls)
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
        if is_landing_page_url(url) and score_usage_link("", url) <= 0:
            info = LibraryUsageInfo(source_url=url)
        info.visited_urls = visited[:]
        info.candidate_urls = list(queued)[:20]
        if info.score() > best.score():
            best = info
            if best.operating_hours and best.regular_holiday and score_usage_link("", best.source_url) > 0:
                break
        for link in discover_usage_links(soup, url):
            if link not in queued and len(queued) < 40:
                queued.add(link)
                queue.append(link)

    best.visited_urls = visited
    best.candidate_urls = list(queued)[:20]
    return best
