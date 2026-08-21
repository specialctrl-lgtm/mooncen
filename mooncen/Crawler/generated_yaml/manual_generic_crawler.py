from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.Crawler_GeneratedYamlTargets import load_unique_yaml  # noqa: E402
from DB.course_upsert_guards import coalesce_provider_course_id_by_raw_url  # noqa: E402
from utils.generic_course_eligibility import (  # noqa: E402
    generic_course_row_decision,
    generic_link_is_editorial,
)
from utils.fee_semantics import fee_status  # noqa: E402
from utils.outbound_http import OutboundRequestBlocked, SafeSession  # noqa: E402
from utils.source_endpoint import canonical_source_endpoint  # noqa: E402
from utils.url_security import safe_external_http_url  # noqa: E402


PROVIDER_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,49}\Z")
MAX_ROWS = 5_000
MAX_PAGES = 120
MAX_DETAIL_PAGES = 1_200
MAX_TIMEOUT_SECONDS = 60
MAX_MENU_FALLBACK_LINKS = 8
WORKING_STATUSES = {"ready", "partial", "candidate", "generated"}
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(\b(?:token|secret|password|api[_-]?key|client[_-]?secret|authorization)\b\s*[=:]\s*)([^\s&,;]+)"
)
DATE_TOKEN_PATTERN = r"(?:\d{4}|\d{2})[.년/\- ]+\d{1,2}[.월/\- ]+\d{1,2}"
MENU_FALLBACK_TOKENS = (
    "수강신청",
    "교육신청",
    "강좌신청",
    "프로그램신청",
    "강좌",
    "프로그램",
)
MENU_FALLBACK_REJECT_PATTERN = re.compile(
    r"(로그인|회원|개인정보|마이페이지|신청내역|신청자|첨부|다운로드|"
    r"login|member|privacy|mypage|applicant|roster|attachment|download)",
    re.IGNORECASE,
)
DOWNLOAD_PATH_PATTERN = re.compile(
    r"(?:\.(?:pdf|hwp|hwpx|xls|xlsx|doc|docx|ppt|pptx|zip)(?:$|[?#])|"
    r"/(?:download|attachment|file)(?:/|$))",
    re.IGNORECASE,
)
FORM_CONTROL_REJECT_PATTERN = re.compile(
    r"(?:pass(?:word)?|passwd|token|csrf|secret|auth|resident|jumin|ssn|"
    r"phone|mobile|email|name|birth|address|applicant)",
    re.IGNORECASE,
)


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def validate_provider(provider: object) -> str:
    value = clean_text(provider).upper()
    if not PROVIDER_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid provider identifier: {value!r}")
    return value


def bounded_int(label: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{label} must be an integer") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"{label} must be between {minimum} and {maximum}")
        return parsed

    return parse


def normalize_url(value: object, *, required: bool = False) -> str:
    raw = "" if value is None else str(value)
    if any(ord(character) < 32 for character in raw):
        raise ValueError("URL contains control characters")
    text = clean_text(raw)
    if not text:
        if required:
            raise ValueError("URL is required")
        return ""
    normalized = safe_external_http_url(text)
    if not normalized:
        raise ValueError("URL must be a safe absolute HTTP(S) URL without credentials")
    return normalized


def safe_url_for_log(value: object) -> str:
    try:
        parsed = urlparse(normalize_url(value, required=True))
    except ValueError:
        return "<invalid-url>"
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))[:500]


def safe_error_text(value: object) -> str:
    text = clean_text(value)
    text = re.sub(r"https?://[^\s<>'\"]+", lambda match: safe_url_for_log(match.group(0)), text)
    return SENSITIVE_TEXT_PATTERN.sub(r"\1<redacted>", text)[:1_000]


def stable_id(*parts: object) -> str:
    raw = "|".join(clean_text(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def session() -> SafeSession:
    hardened = SafeSession()
    hardened.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return hardened


def fetch_soup(s: requests.Session, url: str, timeout: int) -> BeautifulSoup:
    safe_url = normalize_url(url, required=True)
    response = s.get(safe_url, timeout=timeout, verify=True)
    if urlparse(safe_url).scheme == "https" and (
        urlparse(response.url).scheme == "http"
        or any(urlparse(history.url).scheme == "http" for history in response.history)
    ):
        response.close()
        raise OutboundRequestBlocked("HTTPS request attempted a plaintext redirect")
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


def is_pagination_terminal_error(error: requests.exceptions.HTTPError) -> bool:
    response = getattr(error, "response", None)
    return bool(response is not None and response.status_code in {400, 404})


def parse_money(value: object) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    if "무료" in text:
        return 0
    match = re.search(r"\d[\d,]*", text)
    return int(match.group(0).replace(",", "")) if match else None


def parse_date(value: str) -> str | None:
    match = re.search(
        r"(\d{4}|\d{2})[.년/\- ]+(\d{1,2})[.월/\- ]+(\d{1,2})",
        clean_text(value),
    )
    if not match:
        return None
    year = int(match.group(1))
    if year < 100:
        year += 2000
    try:
        parsed = date(year, int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return parsed.isoformat()


def parse_date_range(value: str) -> tuple[str | None, str | None]:
    matches = re.findall(DATE_TOKEN_PATTERN, clean_text(value))
    parsed = [item for item in (parse_date(match) for match in matches) if item]
    if not parsed:
        return None, None
    if parsed[0] > parsed[-1]:
        return None, None
    return parsed[0], parsed[-1]


def normalize_schedule(value: object) -> str:
    text = clean_text(value)
    if not text:
        return ""
    match = re.search(
        r"(?<!\d)(?P<start_hour>[01]?\d|2[0-3])"
        r"(?::(?P<start_minute>[0-5]\d))?\s*"
        r"[~～-]\s*"
        r"(?P<end_hour>[01]?\d|2[0-3])"
        r"(?::(?P<end_minute>[0-5]\d))?\s*시?(?!\d)",
        text,
    )
    if not match:
        return ""
    matched_text = text[match.start() : match.end()]
    if match.group("start_minute") is None and match.group("end_minute") is None and "시" not in matched_text:
        return ""
    start = f"{int(match.group('start_hour')):02d}:{int(match.group('start_minute') or 0):02d}"
    end = f"{int(match.group('end_hour')):02d}:{int(match.group('end_minute') or 0):02d}"
    day_matches = re.findall(
        r"(월요일|화요일|수요일|목요일|금요일|토요일|일요일|"
        r"[월화수목금토일](?:\s*,\s*[월화수목금토일])*)",
        text[max(0, match.start() - 40) : match.start()],
    )
    day = clean_text(day_matches[-1]) if day_matches else ""
    return clean_text(f"{day} / {start}~{end}" if day else f"{start}~{end}")


def normalize_status(value: str) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if any(token in text for token in ("접수중", "예약가능", "신청가능", "모집중")) and "예정" not in text:
        return "OPEN"
    if any(token in text for token in ("예정", "준비")):
        return "SCHEDULED"
    if "대기" in text:
        return "WAITING"
    if any(token in text for token in ("마감", "종료", "완료", "폐강")):
        return "CLOSED"
    return "SCHEDULED"


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    data = load_unique_yaml(path) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML document must be a mapping: {path}")
    return data


def provider_meta(provider: str) -> dict[str, Any]:
    provider = validate_provider(provider)
    registry = ROOT / "config" / "generated_yaml_crawler_registry.yaml"
    if registry.exists():
        for row in _load_yaml_mapping(registry).get("targets") or []:
            if isinstance(row, dict) and clean_text(row.get("provider")).upper() == provider:
                return row
    target_dir = ROOT / "config" / "crawl_targets"
    for path in sorted(target_dir.glob("*.yaml")):
        if path.name == "index.yaml":
            continue
        for row in _load_yaml_mapping(path).get("targets") or []:
            if isinstance(row, dict) and clean_text(row.get("provider")).upper() == provider:
                return row
    return {"provider": provider, "name": provider, "url": "", "crawler_status": "missing"}


def make_page_url(url: str, page: int) -> str:
    parsed = urlparse(normalize_url(url, required=True))
    query = parse_qs(parsed.query)
    for key in ("pageIndex", "currentPageNo", "page", "pageNo", "pageNum", "cPage"):
        if key in query:
            query[key] = [str(page)]
            return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    if page <= 1:
        return url
    query["pageIndex"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def score_anchor(text: str, href: str) -> int:
    score = 0
    if re.search(r"(교육|강좌|체험|프로그램|예약|신청|해설|탐방)", text):
        score += 4
    if re.search(r"(detail|view|read|select|edu|expr|lect|program|course|reservation|bbs)", href, re.I):
        score += 3
    if re.search(r"(로그인|회원|개인정보|사이트맵|목록|검색|첨부|다운로드)", text):
        score -= 5
    if len(text) < 4 or len(text) > 160:
        score -= 2
    return score


def href_url(base_url: str, href: object) -> str:
    value = clean_text(href)
    if not value or value.startswith(("#", "mailto:", "tel:")):
        return ""
    if value.lower().startswith("javascript:"):
        candidates = re.findall(r"['\"]([^'\"]+)['\"]", value)
        value = next(
            (
                candidate
                for candidate in candidates
                if re.search(r"(?:/|\.do|\.jsp|\.php|\.asp|\.aspx|\.html?)", candidate, re.I)
            ),
            "",
        )
    if not value:
        return ""
    try:
        return normalize_url(urljoin(base_url, value), required=True)
    except ValueError:
        return ""


def page_surface_context(soup: BeautifulSoup) -> str:
    values: list[str] = []
    if soup.title:
        values.append(clean_text(soup.title.get_text(" ", strip=True)))
    for node in soup.select("h1, h2, .page-title, .sub-title, .breadcrumb, .breadcrumbs, .location, .page-location")[
        :12
    ]:
        text = clean_text(node.get_text(" ", strip=True))
        if text and text not in values:
            values.append(text)
    return clean_text(" ".join(values))[:1000]


def _same_site(left: str, right: str) -> bool:
    try:
        return bool(
            urlparse(left).hostname and urlparse(left).hostname.lower() == (urlparse(right).hostname or "").lower()
        )
    except ValueError:
        return False


def _menu_link_text(anchor: Tag) -> str:
    return clean_text(
        " ".join(
            clean_text(value)
            for value in (
                anchor.get_text(" ", strip=True),
                anchor.get("title"),
                anchor.get("aria-label"),
            )
            if clean_text(value)
        )
    )


def _safe_get_form_url(form: Tag, base_url: str) -> str:
    """Build a bounded, non-personal GET catalogue request from a form."""

    if clean_text(form.get("method") or "get").lower() != "get":
        return ""
    evidence = clean_text(
        " ".join(
            clean_text(value)
            for value in (
                form.get_text(" ", strip=True),
                form.get("title"),
                form.get("aria-label"),
                form.get("id"),
                " ".join(form.get("class") or []),
                form.get("action"),
            )
            if clean_text(value)
        )
    )
    if MENU_FALLBACK_REJECT_PATTERN.search(evidence):
        return ""
    action_url = href_url(base_url, form.get("action") or base_url)
    if not action_url or not _same_site(base_url, action_url):
        return ""
    if not (
        any(token in evidence for token in MENU_FALLBACK_TOKENS)
        or re.search(r"(?:lecture|course|program|education|edu|sugang|reserve)", action_url, re.IGNORECASE)
    ):
        return ""
    controls: list[tuple[str, str]] = []
    for control in form.select("input[name], select[name]")[:24]:
        name = clean_text(control.get("name"))
        control_type = clean_text(control.get("type") or "text").lower()
        if (
            not name
            or FORM_CONTROL_REJECT_PATTERN.search(name)
            or control_type in {"password", "file", "email", "tel"}
        ):
            if control_type in {"password", "file", "email", "tel"}:
                return ""
            continue
        if control.name == "select":
            selected = control.select_one("option[selected]") or control.select_one("option")
            value = clean_text(selected.get("value")) if selected else ""
        elif control_type in {"checkbox", "radio"} and not control.has_attr("checked"):
            continue
        elif control_type in {"submit", "button", "reset", "image"}:
            continue
        else:
            value = clean_text(control.get("value"))
        if len(name) <= 100 and len(value) <= 500:
            controls.append((name, value))
    parsed = urlparse(action_url)
    existing = parse_qs(parsed.query, keep_blank_values=True)
    for name, value in controls:
        existing[name] = [value]
    return normalize_url(
        urlunparse(parsed._replace(query=urlencode(existing, doseq=True))),
        required=True,
    )


def ranked_menu_fallback_links(
    soup: BeautifulSoup,
    base_url: str,
    *,
    limit: int = MAX_MENU_FALLBACK_LINKS,
) -> list[str]:
    """Return a bounded set of safe, same-site course catalogue menus."""

    if limit <= 0:
        return []
    context = page_surface_context(soup)
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        if not isinstance(anchor, Tag):
            continue
        text = _menu_link_text(anchor)
        href = clean_text(anchor.get("href"))
        url = href_url(base_url, href)
        combined = clean_text(f"{text} {href}")
        if (
            not text
            or not url
            or url in seen
            or url == base_url
            or not _same_site(base_url, url)
            or MENU_FALLBACK_REJECT_PATTERN.search(combined)
            or DOWNLOAD_PATH_PATTERN.search(urlparse(url).path)
            or generic_link_is_editorial(text, url, page_context=context)
        ):
            continue
        token_hits = [token for token in MENU_FALLBACK_TOKENS if token in text]
        if not token_hits:
            continue
        score = sum(8 if token in {"수강신청", "교육신청", "강좌신청", "프로그램신청"} else 4 for token in token_hits)
        if re.search(r"(lecture|course|program|education|edu|sugang)", url, re.IGNORECASE):
            score += 2
        seen.add(url)
        ranked.append((score, len(url), url))
    for iframe in soup.select("iframe[src]"):
        if not isinstance(iframe, Tag):
            continue
        url = href_url(base_url, iframe.get("src"))
        evidence = _menu_link_text(iframe)
        combined = clean_text(f"{evidence} {iframe.get('src')}")
        if (
            not url
            or url in seen
            or not _same_site(base_url, url)
            or MENU_FALLBACK_REJECT_PATTERN.search(combined)
            or DOWNLOAD_PATH_PATTERN.search(urlparse(url).path)
            or generic_link_is_editorial(evidence, url, page_context=context)
            or not (
                any(token in combined for token in MENU_FALLBACK_TOKENS)
                or re.search(r"(?:lecture|course|program|education|edu|sugang|reserve)", url, re.IGNORECASE)
            )
        ):
            continue
        seen.add(url)
        ranked.append((7, len(url), url))
    for form in soup.select("form"):
        if not isinstance(form, Tag):
            continue
        url = _safe_get_form_url(form, base_url)
        if not url or url in seen or generic_link_is_editorial(
            clean_text(form.get_text(" ", strip=True)),
            url,
            page_context=context,
        ):
            continue
        seen.add(url)
        ranked.append((6, len(url), url))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [url for _score, _length, url in ranked[: min(limit, MAX_MENU_FALLBACK_LINKS)]]


def candidate_items(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    surface_context = page_surface_context(soup)
    containers = soup.select(
        ".lecture-list > li, .program-list > li, .edu-list > li, .board-list > li, "
        ".reserve-list > li, .list-body > li, tbody tr, .card, .program, .item"
    )
    for container in containers:
        if not isinstance(container, Tag):
            continue
        if container.find_parent(["header", "nav", "footer"]):
            continue
        if container.find_parent(id=re.compile(r"(header|gnb|lnb|footer|menu|nav)", re.I)):
            continue
        if container.find_parent(class_=re.compile(r"(header|gnb|lnb|footer|menu|nav|sitemap)", re.I)):
            continue
        best: Optional[Tag] = None
        best_url = ""
        best_score = -99
        for anchor in container.select("a[href]"):
            text = clean_text(anchor.get_text(" ", strip=True))
            href = clean_text(anchor.get("href"))
            url = href_url(base_url, href)
            if not url:
                continue
            if generic_link_is_editorial(text, url, page_context=surface_context):
                continue
            score = score_anchor(text, href)
            if score > best_score:
                best = anchor
                best_url = url
                best_score = score
        if best is None or best_score < 0:
            continue
        title = clean_text(best.get_text(" ", strip=True)) or clean_text(container.get_text(" ", strip=True))[:120]
        if not title:
            continue
        key = f"{title.casefold()}|{best_url}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "title": title,
                "raw_url": best_url,
                "container_text": clean_text(container.get_text(" ", strip=True)),
                "surface_context": surface_context,
                "explicit_application_action": bool(
                    re.search(
                        r"(수강신청|교육신청|강좌신청|프로그램신청|신청하기|예약하기)",
                        clean_text(container.get_text(" ", strip=True)),
                    )
                ),
            }
        )
    return rows


def pairs_from_detail(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row in soup.select("tr"):
        headers = [clean_text(node.get_text(" ", strip=True)) for node in row.find_all("th")]
        cells = [clean_text(node.get_text(" ", strip=True)) for node in row.find_all("td")]
        for index, key in enumerate(headers):
            if key and index < len(cells):
                pairs[key] = cells[index]
    for definition_list in soup.select("dl"):
        term = definition_list.find("dt")
        description = definition_list.find("dd")
        if term and description:
            pairs[clean_text(term.get_text(" ", strip=True))] = clean_text(description.get_text(" ", strip=True))
    return pairs


def value_by_keywords(pairs: dict[str, str], *keywords: str) -> str:
    for key, value in pairs.items():
        if any(keyword in key for keyword in keywords):
            return clean_text(value)
    return ""


def infer_from_text(text: str) -> dict[str, str]:
    period = ""
    range_match = re.search(
        rf"{DATE_TOKEN_PATTERN}.*?(?:~|-|부터|까지).*?{DATE_TOKEN_PATTERN}",
        text,
    )
    single_match = re.search(DATE_TOKEN_PATTERN, text)
    if range_match:
        period = clean_text(range_match.group(0))
    elif single_match:
        period = clean_text(single_match.group(0))
    fee_match = re.search(r"(무료|수강료\s*[:：]?\s*[\d,]+원|참가비\s*[:：]?\s*[\d,]+원|[\d,]+원)", text)
    target_match = re.search(
        r"(?:만\s*)?\d{1,3}\s*세\s*(?:이상|이하|부터|까지)?"
        r"|(?:유아|초등|중등|고등|청소년|성인|가족|누구나|어린이|학생|"
        r"장[·ㆍ]?노년층|노년층|지역주민)[^,./|]{0,30}",
        text,
    )
    return {
        "period": period,
        "schedule_raw": normalize_schedule(text),
        "fee_raw": clean_text(fee_match.group(0)) if fee_match else "",
        "target": clean_text(target_match.group(0)) if target_match else "",
    }


def enrich_detail(s: requests.Session, row: dict[str, Any], timeout: int) -> None:
    soup = fetch_soup(s, normalize_url(row["raw_url"], required=True), timeout)
    pairs = pairs_from_detail(soup)
    body = clean_text(soup.get_text(" ", strip=True))
    inferred = infer_from_text(body)
    period = value_by_keywords(pairs, "기간", "일시", "교육일", "운영일") or inferred["period"]
    start_date, end_date = parse_date_range(period)
    schedule_raw = (
        normalize_schedule(value_by_keywords(pairs, "시간", "요일"))
        or normalize_schedule(period)
        or inferred["schedule_raw"]
    )
    venue_name = value_by_keywords(
        pairs,
        "장소",
        "강의실",
        "교육장",
    )
    row.update(
        {
            "period": period,
            "start_date": start_date,
            "end_date": end_date,
            "schedule_raw": schedule_raw,
            "target": (value_by_keywords(pairs, "대상") or inferred["target"] or "연령 미정"),
            "fee_raw": (
                value_by_keywords(pairs, "수강료", "참가비", "이용료", "금액")
                or inferred["fee_raw"]
                or "요금 별도 안내"
            ),
            "status_raw": value_by_keywords(pairs, "상태", "접수", "모집"),
            "description": body[:3000],
        }
    )
    if venue_name:
        row["venue_name"] = venue_name
    row["fee"] = parse_money(row.get("fee_raw"))
    row["fee_status"] = fee_status(row["fee"])
    row["status"] = normalize_status(row.get("status_raw") or body)
    image = soup.select_one("meta[property='og:image']")
    image_url = href_url(row["raw_url"], image.get("content")) if image and image.get("content") else ""
    if not image_url:
        for image_node in soup.select("img[src]"):
            source = clean_text(image_node.get("src"))
            if re.search(r"(logo|icon|btn|mark|banner)", source, re.I):
                continue
            image_url = href_url(row["raw_url"], source)
            if image_url:
                break
    row["image_url"] = image_url


def _collection_meta(
    *,
    pages: int,
    details: int,
    errors: list[str],
    pagination_complete: bool,
    page_cap_reached: bool,
    detail_cap_reached: bool,
    row_cap_reached: bool,
    sample_cap_reached: bool,
    menu_fallback_links: int = 0,
    menu_fallback_pages: int = 0,
    eligibility_rejected_rows: int = 0,
    eligibility_rejection_reasons: dict[str, int] | None = None,
    eligibility_complete: bool = True,
) -> dict[str, Any]:
    complete = (
        pagination_complete
        and not page_cap_reached
        and not detail_cap_reached
        and not row_cap_reached
        and not sample_cap_reached
        and not errors
        and eligibility_complete
    )
    return {
        "pages": pages,
        "detail_pages": details,
        "parser": "manual_generic",
        "errors": errors,
        "pagination_complete": pagination_complete,
        "page_cap_reached": page_cap_reached,
        "detail_cap_reached": detail_cap_reached,
        "row_cap_reached": row_cap_reached,
        "sample_cap_reached": sample_cap_reached,
        "menu_fallback_links": menu_fallback_links,
        "menu_fallback_pages": menu_fallback_pages,
        "eligibility_rejected_rows": eligibility_rejected_rows,
        "eligibility_rejection_reasons": dict(
            sorted((eligibility_rejection_reasons or {}).items())
        ),
        "eligibility_rejection_scope": "row",
        "eligibility_rejections_are_provider_failure": False,
        "eligibility_complete": eligibility_complete,
        "complete": complete,
    }


def collect(
    provider: str,
    limit: int,
    max_pages: int,
    detail_limit: int,
    timeout: int,
    max_depth: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider = validate_provider(provider)
    meta = provider_meta(provider)
    status = clean_text(meta.get("crawler_status") or meta.get("status")).lower()
    if status not in WORKING_STATUSES:
        return [], {
            "error": f"disabled_status:{status or 'missing'}",
            "errors": [f"disabled_status:{status or 'missing'}"],
            "pages": 0,
            "detail_pages": 0,
            "parser": "blocked",
            "complete": False,
        }
    url = normalize_url(meta.get("url"), required=True)
    source_endpoint = canonical_source_endpoint(url)
    if not source_endpoint:
        raise ValueError("configured source endpoint is not a safe HTTP(S) URL")
    effective_detail_limit = detail_limit if max_depth > 0 else 0
    rows: list[dict[str, Any]] = []
    seen_rows: set[str] = set()
    errors: list[str] = []
    pages = 0
    details = 0
    menu_fallback_links = 0
    menu_fallback_pages = 0
    eligibility_rejected_rows = 0
    eligibility_rejection_reasons: Counter[str] = Counter()
    pagination_complete = False
    page_cap_reached = False
    detail_cap_reached = False
    row_cap_reached = False
    sample_cap_reached = False

    def process_page(
        hardened_session: requests.Session,
        soup: BeautifulSoup,
        page_url: str,
    ) -> tuple[int, int]:
        nonlocal details
        nonlocal detail_cap_reached
        nonlocal eligibility_rejected_rows
        nonlocal row_cap_reached
        nonlocal sample_cap_reached

        items = candidate_items(soup, page_url)
        page_added = 0
        surface_context = page_surface_context(soup)
        for item in items:
            title = clean_text(item.get("title"))
            raw_url = normalize_url(item.get("raw_url"), required=True)
            identity = f"{title.casefold()}|{raw_url}"
            if identity in seen_rows:
                continue
            seen_rows.add(identity)

            item_context = clean_text(item.get("surface_context") or surface_context)
            text = clean_text(item.get("container_text"))
            inferred = infer_from_text(text)
            start_date, end_date = parse_date_range(inferred["period"])
            raw_fields = {
                "parser": "manual_generic",
                "source_url": page_url,
                "source_endpoint": source_endpoint,
                "surface_context": item_context,
                "explicit_application_action": bool(item.get("explicit_application_action")),
            }
            row = {
                "provider": provider,
                "provider_course_id": stable_id(provider, raw_url, title),
                "title": title,
                "branch": clean_text(meta.get("name") or provider)[:100],
                "branch_code": f"manual_{stable_id(provider, meta.get('url'))[:16]}",
                "address": "",
                "raw_url": raw_url,
                "source_endpoint": source_endpoint,
                "application_url": raw_url,
                "application_type": "ONLINE_RESERVATION",
                "reservation_available": True,
                "period": inferred["period"],
                "start_date": start_date,
                "end_date": end_date,
                "schedule_raw": inferred["schedule_raw"],
                "target": inferred["target"],
                "fee_raw": inferred["fee_raw"],
                "fee": parse_money(inferred["fee_raw"]),
                "fee_status": fee_status(parse_money(inferred["fee_raw"])),
                "status": None,
                "status_raw": "",
                "description": "",
                "image_url": "",
                # A provider label is filled after eligibility. It must not count
                # as independent source evidence for an otherwise empty row.
                "venue_name": "",
                "venue_address": "",
                "collection_category": clean_text(meta.get("collection_category") or "OTHER"),
                "domain_category": clean_text(meta.get("category") or meta.get("domain_category") or "기타"),
                "source_group": "manual_target",
                "operator_type": "public",
                "collection_type": "manual_generic",
                "program_type": "강좌",
                "raw_fields": raw_fields,
            }
            if effective_detail_limit > 0 and details < effective_detail_limit:
                details += 1
                try:
                    enrich_detail(hardened_session, row, timeout)
                except Exception as exc:
                    errors.append(f"detail:{safe_error_text(f'{type(exc).__name__}: {exc}')}")
            else:
                detail_cap_reached = True

            eligible, eligibility_reason = generic_course_row_decision(row)
            raw_fields["eligibility_reason"] = eligibility_reason
            if not eligible:
                eligibility_rejected_rows += 1
                eligibility_rejection_reasons[eligibility_reason] += 1
                continue

            row["venue_name"] = clean_text(row.get("venue_name")) or clean_text(meta.get("name") or provider)[:150]
            rows.append(row)
            page_added += 1
            if len(rows) >= MAX_ROWS:
                row_cap_reached = True
                break
            if limit > 0 and len(rows) >= limit:
                sample_cap_reached = True
                break
        return page_added, len(items)

    with session() as hardened_session:
        fallback_links: list[str] = []
        for page in range(1, max_pages + 1):
            page_url = make_page_url(url, page)
            try:
                soup = fetch_soup(hardened_session, page_url, timeout)
            except requests.exceptions.HTTPError as error:
                if page > 1 and is_pagination_terminal_error(error):
                    pagination_complete = True
                    break
                raise
            pages += 1
            page_added, item_count = process_page(hardened_session, soup, page_url)
            if row_cap_reached or sample_cap_reached:
                break
            if page == 1 and not rows:
                fallback_links = ranked_menu_fallback_links(
                    soup,
                    page_url,
                    limit=MAX_MENU_FALLBACK_LINKS,
                )
                menu_fallback_links = len(fallback_links)
                if fallback_links:
                    break
                pagination_complete = True
                break
            if item_count == 0:
                pagination_complete = True
                break
            if page_added == 0:
                pagination_complete = True
                break
        else:
            page_cap_reached = True

        if fallback_links and not row_cap_reached and not sample_cap_reached:
            exhausted_fallback = True
            for fallback_url in fallback_links:
                if pages >= max_pages:
                    page_cap_reached = True
                    exhausted_fallback = False
                    break
                pages += 1
                try:
                    fallback_soup = fetch_soup(hardened_session, fallback_url, timeout)
                except Exception as exc:
                    errors.append(f"menu_fallback:{safe_error_text(f'{type(exc).__name__}: {exc}')}")
                    continue
                menu_fallback_pages += 1
                process_page(hardened_session, fallback_soup, fallback_url)
                if row_cap_reached or sample_cap_reached:
                    exhausted_fallback = False
                    break
            if exhausted_fallback:
                pagination_complete = True

    collection_meta = _collection_meta(
        pages=pages,
        details=details,
        errors=errors,
        pagination_complete=pagination_complete,
        page_cap_reached=page_cap_reached,
        detail_cap_reached=detail_cap_reached,
        row_cap_reached=row_cap_reached,
        sample_cap_reached=sample_cap_reached,
        menu_fallback_links=menu_fallback_links,
        menu_fallback_pages=menu_fallback_pages,
        eligibility_rejected_rows=eligibility_rejected_rows,
        eligibility_rejection_reasons=dict(eligibility_rejection_reasons),
        # A generic empty shell is not proof that every prior course ended, and
        # a one-hop menu fallback does not exhaust the discovered catalogue's
        # own pagination. Neither path may authorize stale-row cleanup.
        eligibility_complete=bool(rows and not fallback_links),
    )
    collection_meta["source_endpoint"] = source_endpoint
    return rows, collection_meta


def save_db(
    rows: list[dict[str, Any]],
    *,
    skip_expired: bool = True,
    stale_provider: str = "",
    stale_cutoff: Optional[datetime] = None,
    stale_source_endpoint: str = "",
) -> int:
    if not rows and not stale_provider:
        return 0
    for row in rows:
        eligible, reason = generic_course_row_decision(row)
        if not eligible:
            raise ValueError(f"Refusing to publish ineligible manual generic row: {reason}")
    from DB.db_utils import get_db_cursor

    if stale_provider:
        stale_provider = validate_provider(stale_provider)
        if stale_cutoff is None:
            raise ValueError("stale_cutoff is required when stale_provider is set")
        stale_source_endpoint = canonical_source_endpoint(stale_source_endpoint)
        if not stale_source_endpoint:
            raise ValueError("stale_source_endpoint is required when stale_provider is set")
    today = date.today().isoformat()
    saved = 0
    branch_ids: dict[tuple[str, str], str] = {}
    with get_db_cursor() as cursor:
        for raw_row in rows:
            row = dict(raw_row)
            row["provider"] = validate_provider(row.get("provider"))
            row["title"] = clean_text(row.get("title"))
            if not row["title"]:
                raise ValueError("Course title is required")
            row["raw_url"] = normalize_url(row.get("raw_url"), required=True)
            row["application_url"] = normalize_url(row.get("application_url") or row["raw_url"], required=True)
            row["source_endpoint"] = canonical_source_endpoint(
                row.get("source_endpoint")
                or (row.get("raw_fields") or {}).get("source_endpoint")
            ) or None
            row["image_url"] = normalize_url(row.get("image_url")) if row.get("image_url") else ""
            if skip_expired and row.get("end_date") and row["end_date"] < today:
                continue
            branch_key = (row["provider"], clean_text(row.get("branch_code")))
            if not branch_key[1]:
                raise ValueError("branch_code is required")
            if branch_key not in branch_ids:
                cursor.execute(
                    """
                    INSERT INTO branches(provider, branch_code, name, address, website_url, address_source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (provider, branch_code)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        address = COALESCE(NULLIF(EXCLUDED.address, ''), branches.address),
                        website_url = EXCLUDED.website_url,
                        updated_at = now()
                    RETURNING id
                    """,
                    (
                        row["provider"],
                        branch_key[1],
                        clean_text(row.get("branch"))[:100] or row["provider"],
                        clean_text(row.get("address")),
                        row["raw_url"],
                        "crawler" if clean_text(row.get("address")) else None,
                    ),
                )
                branch_ids[branch_key] = str(cursor.fetchone()["id"])
            crawler_raw_fields = dict(row.get("raw_fields")) if isinstance(row.get("raw_fields"), dict) else {}
            course_payload = {
                **row,
                "branch_id": branch_ids[branch_key],
                "category_raw": row.get("domain_category"),
                "discovery_status": "manual_generic",
                "raw_fields": json.dumps(
                    {
                        **crawler_raw_fields,
                        "fee_raw": row.get("fee_raw"),
                        "fee_status": fee_status(row.get("fee")),
                        "status_raw": row.get("status_raw"),
                        "period_raw": row.get("period"),
                    },
                    ensure_ascii=False,
                ),
            }
            coalesce_provider_course_id_by_raw_url(
                cursor,
                course_payload,
            )
            cursor.execute(
                """
                INSERT INTO courses(
                    provider, provider_course_id, branch_id, title, target, category_raw,
                    collection_category, domain_category, source_group, operator_type, collection_type,
                    fee, schedule_raw, start_date, end_date, venue_name, venue_address,
                    application_url, application_type, reservation_available, discovery_status,
                    program_type, raw_fields, status, raw_url, description, image_url,
                    source_endpoint,
                    is_active, last_seen_at
                )
                VALUES (
                    %(provider)s, %(provider_course_id)s, %(branch_id)s, %(title)s, %(target)s, %(category_raw)s,
                    %(collection_category)s, %(domain_category)s, %(source_group)s, %(operator_type)s, %(collection_type)s,
                    %(fee)s, %(schedule_raw)s, %(start_date)s, %(end_date)s, %(venue_name)s, %(venue_address)s,
                    %(application_url)s, %(application_type)s, %(reservation_available)s, %(discovery_status)s,
                    %(program_type)s, %(raw_fields)s::jsonb, %(status)s, %(raw_url)s, %(description)s, %(image_url)s,
                    %(source_endpoint)s,
                    TRUE, now()
                )
                ON CONFLICT (provider, provider_course_id)
                DO UPDATE SET
                    branch_id = EXCLUDED.branch_id,
                    title = EXCLUDED.title,
                    target = EXCLUDED.target,
                    category_raw = EXCLUDED.category_raw,
                    fee = EXCLUDED.fee,
                    schedule_raw = EXCLUDED.schedule_raw,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    venue_name = EXCLUDED.venue_name,
                    venue_address = EXCLUDED.venue_address,
                    application_url = EXCLUDED.application_url,
                    application_type = EXCLUDED.application_type,
                    reservation_available = EXCLUDED.reservation_available,
                    raw_fields = EXCLUDED.raw_fields,
                    status = EXCLUDED.status,
                    raw_url = EXCLUDED.raw_url,
                    description = EXCLUDED.description,
                    image_url = EXCLUDED.image_url,
                    source_endpoint = COALESCE(EXCLUDED.source_endpoint, courses.source_endpoint),
                    is_active = TRUE,
                    last_seen_at = now(),
                    removed_at = NULL,
                    updated_at = now()
                """,
                course_payload,
            )
            saved += 1
        if stale_provider:
            cursor.execute(
                """
                UPDATE courses
                SET is_active = FALSE,
                    removed_at = COALESCE(removed_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE provider = %s
                  AND source_endpoint = %s
                  AND is_active = TRUE
                  AND last_seen_at < %s
                """,
                (stale_provider, stale_source_endpoint, stale_cutoff),
            )
    return saved


def field_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    keys = [
        "title",
        "branch",
        "period",
        "schedule_raw",
        "target",
        "fee_raw",
        "status",
        "description",
        "image_url",
        "application_url",
    ]
    return {key: sum(1 for row in rows if clean_text(row.get(key))) for key in keys}


def parse_args(provider: str, argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Manual generic crawler for {provider}")
    parser.add_argument(
        "--limit", "--per-target-limit", dest="limit", type=bounded_int("limit", 0, MAX_ROWS), default=10
    )
    parser.add_argument("--max-pages", type=bounded_int("max-pages", 1, MAX_PAGES), default=2)
    parser.add_argument(
        "--max-depth",
        type=bounded_int("max-depth", 0, 1),
        default=1,
        help="0 disables detail requests; 1 enables the single supported list-to-detail level",
    )
    parser.add_argument("--detail-limit", type=bounded_int("detail-limit", 0, MAX_DETAIL_PAGES), default=10)
    parser.add_argument("--timeout", type=bounded_int("timeout", 1, MAX_TIMEOUT_SECONDS), default=25)
    persistence = parser.add_mutually_exclusive_group()
    persistence.add_argument("--save-db", action="store_true")
    persistence.add_argument(
        "--dry-run", action="store_true", help="Explicitly perform no database writes (the default)"
    )
    parser.add_argument(
        "--allow-partial-save",
        action="store_true",
        help="Allow an explicitly bounded sample to be upserted without stale cleanup",
    )
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--mark-stale", action="store_true")
    args = parser.parse_args(argv)
    if args.mark_stale and not args.save_db:
        parser.error("--mark-stale requires --save-db")
    if args.mark_stale and args.limit != 0:
        parser.error("--mark-stale requires --limit 0; sampled crawls cannot deactivate unseen rows")
    if args.allow_partial_save and args.limit == 0:
        parser.error("--allow-partial-save requires a positive --limit")
    if args.save_db and args.limit > 0 and not args.allow_partial_save:
        parser.error("bounded database writes require explicit --allow-partial-save")
    return args


def run_cli(provider: str, argv: Optional[Sequence[str]] = None) -> int:
    try:
        provider = validate_provider(provider)
    except ValueError as exc:
        print(safe_error_text(exc), file=sys.stderr)
        return 2
    args = parse_args(provider, argv)
    crawl_started_at = datetime.now().astimezone()
    try:
        rows, meta = collect(provider, args.limit, args.max_pages, args.detail_limit, args.timeout, args.max_depth)
    except Exception as exc:
        print(f"provider={provider} error={safe_error_text(f'{type(exc).__name__}: {exc}')}", file=sys.stderr)
        return 1
    errors = [safe_error_text(error) for error in meta.get("errors") or []]
    if errors:
        print(f"provider={provider} collection_errors={len(errors)} first={errors[0]}", file=sys.stderr)
        return 1
    if args.mark_stale and not meta.get("complete"):
        print("--mark-stale refused: crawl did not prove full uncapped pagination/detail/row coverage", file=sys.stderr)
        return 1
    if args.save_db and not meta.get("complete") and not (args.allow_partial_save and args.limit > 0):
        print("database write refused: incomplete crawl lacks explicit bounded partial-save opt-in", file=sys.stderr)
        return 1
    saved = 0
    if args.save_db:
        try:
            saved = save_db(
                rows,
                skip_expired=not args.include_expired,
                stale_provider=provider if args.mark_stale else "",
                stale_cutoff=crawl_started_at if args.mark_stale else None,
                stale_source_endpoint=meta.get("source_endpoint", "") if args.mark_stale else "",
            )
        except Exception as exc:
            print(f"provider={provider} db_error={safe_error_text(f'{type(exc).__name__}: {exc}')}", file=sys.stderr)
            return 1
    fields = field_counts(rows)
    print(
        f"provider={provider} rows={len(rows)} saved={saved} parser={meta.get('parser')} "
        f"pages={meta.get('pages')} detail={meta.get('detail_pages')} complete={bool(meta.get('complete'))}"
    )
    print("field_counts " + " ".join(f"{key}={value}" for key, value in fields.items()))
    for row in rows[:5]:
        print(
            f"- {clean_text(row.get('title'))} / {clean_text(row.get('period')) or '-'} / "
            f"{clean_text(row.get('schedule_raw')) or '-'} / {safe_url_for_log(row.get('raw_url'))}"
        )
    if meta.get("parser") == "blocked":
        return 2
    return 0 if rows else 2
