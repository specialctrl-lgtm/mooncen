from __future__ import annotations

import argparse
import os
import re
import sys
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from Crawler.Crawler_GeneratedYamlTargets import _iter_target_rows, TARGETS_FILE  # noqa: E402
from Crawler.Crawler_MunicipalYaml import (  # noqa: E402
    filter_generic_miscollected_rows,
    node_link_evidence,
    node_link_url,
    parse_all_courses,
    session,
)
from Crawler.Crawler_YamlSources import parse_date_range  # noqa: E402
from utils import clean_text  # noqa: E402
from utils.outbound_http import (  # noqa: E402
    OutboundRequestBlocked,
    OutboundResponseTooLarge,
    outbound_request_budget,
)


REPORT_DIR = ROOT / "logs" / "url_discovery_reports"
DISCOVERY_USER_AGENT = "MooncenCrawler/1.0"
MAX_PARENT_LEVELS = 8
MAX_DISCOVERY_URL_LENGTH = 8192
SENSITIVE_QUERY_MARKERS = (
    "accesstoken",
    "apikey",
    "authorization",
    "clientsecret",
    "credential",
    "csrftoken",
    "password",
    "secret",
    "session",
    "sessionid",
    "signature",
    "token",
)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}

CULTURE_PROVIDERS = {
    "HOMEPLUS",
    "EMART",
    "LOTTE",
    "HYUNDAI_DEPT",
    "GALLERIA",
    "AK_PLAZA",
    "ELAND_RETAIL",
    "SHINSEGAE_ACADEMY",
    "LOTTE_MART",
}

POSITIVE_KEYWORDS = (
    "수강신청",
    "강좌신청",
    "교육신청",
    "프로그램신청",
    "예약",
    "통합예약",
    "신청하기",
    "접수",
    "강좌",
    "강의",
    "교육",
    "프로그램",
    "평생학습",
    "문화강좌",
    "문화행사",
    "체험",
    "해설",
    "탐방",
    "전시",
    "공연",
    "관람",
    "예매",
    "티켓",
    "reserve",
    "reservation",
    "apply",
    "course",
    "lecture",
    "program",
    "edu",
    "sugang",
    "yeyak",
)
STRUCTURE_KEYWORDS = (
    "교육기간",
    "강좌기간",
    "운영기간",
    "접수기간",
    "신청기간",
    "모집기간",
    "모집인원",
    "행사기간",
    "관람기간",
    "전시기간",
    "공연기간",
    "수강료",
    "참가비",
    "대상",
    "교육대상",
    "신청",
    "예약",
    "예매",
    "관람",
)
NEGATIVE_KEYWORDS = (
    "뉴스",
    "신문",
    "기사",
    "보도자료",
    "채용",
    "공고",
    "입찰",
    "합격",
    "로그인",
    "회원가입",
    "개인정보",
    "사이트맵",
)
NEGATIVE_URL_TOKENS = (
    "news/article",
    "articleview",
    "/bbs/",
    "/board/",
    "notice",
    "press",
    "recruit",
    "applylecturer",
    "lecturer",
    "volunteer",
    "camp",
    "lottery",
)


@dataclass
class Candidate:
    url: str
    title: str = ""
    source: str = "internal"
    source_page: str = ""
    source_level: int = 0
    link_score: int = 0
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    parser: str = ""
    rows: int = 0
    field_counts: dict[str, int] = field(default_factory=dict)
    final_url: str = ""
    status_code: int | None = None
    content_type: str = ""
    response_bytes: int = 0
    same_organization: bool = False
    host_allowed: bool = False
    verdict: str = "unverified"
    error_kind: str = ""
    error: str = ""

    @property
    def parse_ready(self) -> bool:
        """Whether the candidate produces course rows with useful structure."""
        return bool(
            self.rows
            and (
                self.field_counts.get("period")
                or self.field_counts.get("schedule_raw")
                or self.field_counts.get("apply_start")
                or self.field_counts.get("apply_end")
                or self.field_counts.get("apply_period")
                or self.field_counts.get("apply_period_raw")
            )
        )

    @property
    def registration_schedule_ready(self) -> bool:
        return bool(self.rows and self.field_counts.get("persisted_registration_schedule"))

    @property
    def application_path_ready(self) -> bool:
        return bool(self.rows and self.field_counts.get("application_url"))


@dataclass(frozen=True)
class DiscoverySeed:
    url: str
    kind: str
    level: int


@dataclass
class FetchedPage:
    requested_url: str
    final_url: str = ""
    title: str = ""
    text: str = ""
    soup: BeautifulSoup | None = None
    status_code: int | None = None
    content_type: str = ""
    response_bytes: int = 0
    error_kind: str = ""
    error: str = ""


@dataclass
class RobotsPolicy:
    parser: RobotFileParser | None
    status: str
    crawl_delay: float = 0.0


def normalized_netloc(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    return hostname.removeprefix("www.")


def _normalized_query_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _sensitive_query_key(value: str) -> bool:
    normalized = _normalized_query_key(value)
    return normalized in {"auth", "sig", "token"} or any(
        marker in normalized for marker in SENSITIVE_QUERY_MARKERS
    )


def normalize_discovery_url(value: Any, base_url: str = "") -> str:
    """Return a report-safe absolute URL or an empty string.

    Discovery reports are persisted and shown in the Ops Console, so links
    containing credentials or session-like query parameters must never enter
    the candidate set.
    """

    raw = clean_text(value)
    if not raw:
        return ""
    joined = urljoin(base_url, raw) if base_url else raw
    if len(joined) > MAX_DISCOVERY_URL_LENGTH or any(
        ord(character) <= 32 or ord(character) == 127 for character in joined
    ):
        return ""
    try:
        parsed = urlparse(joined)
        _ = parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    if re.search(r";j?sessionid=", parsed.path, re.IGNORECASE):
        return ""
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if any(_sensitive_query_key(key) for key, _value in query_pairs):
        return ""
    query = urlencode(
        [(key, value) for key, value in query_pairs if key.lower() not in TRACKING_QUERY_KEYS],
        doseq=True,
    )
    hostname = parsed.hostname.lower().rstrip(".")
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (parsed.scheme.lower() == "http" and parsed.port == 80) or (
        parsed.scheme.lower() == "https" and parsed.port == 443
    )
    netloc = host if parsed.port is None or default_port else f"{host}:{parsed.port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunparse((parsed.scheme.lower(), netloc, path, "", query, ""))


def discovery_seed_urls(source_url: str, max_parent_levels: int = MAX_PARENT_LEVELS) -> list[DiscoverySeed]:
    """Build current -> queryless path -> parent paths -> origin seeds."""

    current = normalize_discovery_url(source_url)
    if not current:
        return []
    parsed = urlparse(current)
    seeds: list[DiscoverySeed] = [DiscoverySeed(current, "configured_target", 0)]
    queryless = urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
    queryless = normalize_discovery_url(queryless)
    if queryless and queryless != current:
        seeds.append(DiscoverySeed(queryless, "configured_path", 0))

    path = parsed.path or "/"
    if path.endswith("/"):
        segments = [segment for segment in path.strip("/").split("/") if segment]
    else:
        segments = [segment for segment in path.rsplit("/", 1)[0].strip("/").split("/") if segment]
    for level in range(1, min(len(segments), max_parent_levels) + 1):
        remaining = segments[: len(segments) - level + 1]
        parent_path = "/" + "/".join(remaining) + "/" if remaining else "/"
        parent = normalize_discovery_url(
            urlunparse((parsed.scheme, parsed.netloc, parent_path, "", "", ""))
        )
        if parent:
            seeds.append(DiscoverySeed(parent, "parent_path", level))

    origin = normalize_discovery_url(urlunparse((parsed.scheme, parsed.netloc, "/", "", "", "")))
    if origin:
        seeds.append(DiscoverySeed(origin, "site_root", len(segments) + 1))

    unique: dict[str, DiscoverySeed] = {}
    for seed in seeds:
        unique.setdefault(seed.url.rstrip("/") or seed.url, seed)
    return list(unique.values())


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def robots_allowed(
    http_session: requests.Session,
    url: str,
    timeout: int,
    cache: dict[str, RobotsPolicy],
) -> tuple[bool, str]:
    origin = _origin(url)
    if origin not in cache:
        robots_url = f"{origin}/robots.txt"
        try:
            response = http_session.get(robots_url, timeout=min(timeout, 10))
            if response.status_code in {401, 403}:
                parser = RobotFileParser()
                parser.parse(["User-agent: *", "Disallow: /"])
                cache[origin] = RobotsPolicy(parser, "robots_access_denied")
            elif 200 <= response.status_code < 300:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                delay = parser.crawl_delay(DISCOVERY_USER_AGENT)
                if delay is None:
                    delay = parser.crawl_delay("*")
                cache[origin] = RobotsPolicy(parser, "robots_loaded", float(delay or 0.0))
            elif response.status_code in {404, 410}:
                cache[origin] = RobotsPolicy(None, "robots_not_found")
            elif response.status_code == 429 or response.status_code >= 500:
                cache[origin] = RobotsPolicy(None, "robots_unavailable_retry")
            else:
                cache[origin] = RobotsPolicy(None, f"robots_http_{response.status_code}")
        except OutboundRequestBlocked:
            cache[origin] = RobotsPolicy(None, "robots_ssrf_blocked")
        except requests.Timeout:
            cache[origin] = RobotsPolicy(None, "robots_unavailable_retry")
        except requests.RequestException:
            cache[origin] = RobotsPolicy(None, "robots_unavailable_retry")
    policy = cache[origin]
    if policy.status == "robots_not_found":
        return True, policy.status
    if policy.parser is None:
        return False, policy.status
    allowed = policy.parser.can_fetch(DISCOVERY_USER_AGENT, url)
    return allowed, "robots_allowed" if allowed else "robots_disallowed"


def fetch_page(
    http_session: requests.Session,
    url: str,
    timeout: int,
    robots_cache: dict[str, RobotsPolicy] | None = None,
    request_timestamps: dict[str, float] | None = None,
) -> FetchedPage:
    page = FetchedPage(requested_url=url)
    try:
        current_url = url
        response: requests.Response | None = None
        for _hop in range(6):
            if robots_cache is not None:
                policy = robots_cache.get(_origin(current_url))
                delay = float(policy.crawl_delay if policy else 0.0)
                if delay > 10:
                    page.error_kind = "robots_crawl_delay_too_high"
                    page.error = f"robots crawl-delay {delay:g}s exceeds the discovery safety cap"
                    return page
                if delay > 0 and request_timestamps is not None:
                    elapsed = time.monotonic() - request_timestamps.get(_origin(current_url), 0.0)
                    if elapsed < delay:
                        time.sleep(delay - elapsed)
            response = http_session.get(current_url, timeout=timeout, allow_redirects=False)
            if request_timestamps is not None:
                request_timestamps[_origin(current_url)] = time.monotonic()
            if not response.is_redirect and not response.is_permanent_redirect:
                break
            location = response.headers.get("Location")
            next_url = normalize_discovery_url(location, current_url)
            if not next_url:
                page.error_kind = "unsafe_redirect"
                page.error = "redirect target was not safe to persist"
                return page
            if urlparse(current_url).scheme.lower() == "https" and urlparse(next_url).scheme.lower() == "http":
                page.error_kind = "tls_downgrade"
                page.error = "HTTPS target redirected to plaintext HTTP"
                return page
            if robots_cache is not None:
                allowed, robots_reason = robots_allowed(http_session, next_url, timeout, robots_cache)
                if not allowed:
                    page.error_kind = robots_reason
                    page.error = f"redirect blocked by {robots_reason}"
                    return page
            current_url = next_url
        else:
            page.error_kind = "redirect_limit"
            page.error = "redirect limit exceeded"
            return page
        if response is None:
            page.error_kind = "network_error"
            page.error = "request produced no response"
            return page
        page.status_code = int(response.status_code)
        page.final_url = normalize_discovery_url(current_url)
        page.content_type = clean_text(response.headers.get("Content-Type")).split(";", 1)[0].lower()
        page.response_bytes = len(response.content)
        response.raise_for_status()
        if not page.final_url:
            page.error_kind = "unsafe_redirect"
            page.error = "redirect target was not safe to persist"
            return page
        if page.content_type and not any(
            token in page.content_type for token in ("html", "xhtml", "xml", "text/plain")
        ):
            page.error_kind = "unsupported_content_type"
            page.error = f"unsupported content type: {page.content_type}"
            return page
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding
        page.soup = BeautifulSoup(response.text, "lxml")
        page.title = clean_text(page.soup.title.get_text(" ", strip=True)) if page.soup.title else ""
        page.text = clean_text(page.soup.get_text(" ", strip=True))[:12000]
    except OutboundResponseTooLarge:
        page.error_kind = "response_too_large"
        page.error = "response exceeded the configured byte limit"
    except OutboundRequestBlocked:
        page.error_kind = "ssrf_blocked"
        page.error = "destination failed public-network safety checks"
    except requests.Timeout:
        page.error_kind = "timeout"
        page.error = "request timed out"
    except requests.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code is not None:
            page.error_kind = "http_4xx" if 400 <= int(status_code) < 500 else "http_5xx"
        else:
            page.error_kind = "network_error"
        page.error = f"HTTP request failed{f' ({status_code})' if status_code else ''}"
    except Exception as exc:
        page.error_kind = "parse_error"
        page.error = f"{type(exc).__name__}: page could not be parsed"
    return page


def is_same_organization(source_url: str, candidate_url: str) -> bool:
    source_host = normalized_netloc(source_url)
    candidate_host = normalized_netloc(candidate_url)
    if not source_host or not candidate_host:
        return False
    return source_host == candidate_host or candidate_host.endswith("." + source_host) or source_host.endswith("." + candidate_host)


def is_configured_host(source_url: str, candidate_url: str) -> bool:
    source_host = normalized_netloc(source_url)
    candidate_host = normalized_netloc(candidate_url)
    return bool(source_host and candidate_host and source_host == candidate_host)


def candidate_score(url: str, title: str, page_text: str, source_url: str) -> tuple[int, list[str]]:
    haystack = clean_text(f"{url} {title} {page_text}").lower()
    score = 0
    reasons: list[str] = []
    if is_same_organization(source_url, url):
        score += 15
        reasons.append("same_site")
    positive_hits = sum(1 for keyword in POSITIVE_KEYWORDS if keyword.lower() in haystack)
    if positive_hits:
        score += min(30, positive_hits * 4)
        reasons.append(f"application_terms={positive_hits}")
    structure_hits = sum(1 for keyword in STRUCTURE_KEYWORDS if keyword in page_text)
    if structure_hits:
        score += min(30, structure_hits * 5)
        reasons.append(f"structure_terms={structure_hits}")
    if re.search(r"\d{4}[./-]\d{1,2}(?:[./-]\d{1,2})?", page_text):
        score += 10
        reasons.append("date_evidence")
    if any(token in url.lower() for token in ("reserve", "reservation", "apply", "sugang", "yeyak", "lecture", "course", "program", "edu")):
        score += 15
        reasons.append("url_token")
    if any(token in url.lower() for token in NEGATIVE_URL_TOKENS):
        score -= 35
        reasons.append("negative_url")
    if any(keyword in clean_text(f"{title} {page_text}") for keyword in NEGATIVE_KEYWORDS):
        score -= 20
        reasons.append("negative_text")
    parsed = urlparse(url)
    if (parsed.path or "/") == "/" and structure_hits == 0:
        score -= 10
        reasons.append("generic_root")
    if re.search(r"(?:^|[?&])(ntt(?:no|id)|article|board(?:seq|no)|mode=view|idx)=", parsed.query, re.IGNORECASE):
        score -= 15
        reasons.append("detail_or_notice_query")
    return max(0, min(score, 100)), reasons


def google_cse_candidates(
    query: str,
    limit: int,
    http_session: requests.Session | None = None,
) -> list[Candidate]:
    api_key = os.getenv("GOOGLE_SEARCH_API_KEY") or os.getenv("GOOGLE_CUSTOM_SEARCH_API_KEY")
    cx = os.getenv("GOOGLE_SEARCH_CX") or os.getenv("GOOGLE_CUSTOM_SEARCH_CX")
    if not api_key or not cx:
        return []
    context = nullcontext(http_session) if http_session is not None else session()
    try:
        with context as active_session:
            response = active_session.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": api_key, "cx": cx, "q": query, "num": min(limit, 10)},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        status_text = f" status={status_code}" if status_code is not None else ""
        raise RuntimeError(f"Google Custom Search failed type={type(exc).__name__}{status_text}") from None
    candidates = []
    for item in payload.get("items") or []:
        link = normalize_discovery_url(item.get("link"))
        if link:
            candidates.append(Candidate(url=link, title=clean_text(item.get("title")), source=f"google:{query}"))
    return candidates


def extract_links(
    base_url: str,
    soup: BeautifulSoup,
    limit: int,
    *,
    source_kind: str = "internal_link",
    source_level: int = 0,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for node in soup.find_all(["a", "button", "form"]):
        text = node_link_evidence(node)
        raw_url = node.get("action") if getattr(node, "name", "") == "form" else node_link_url(base_url, node)
        if not raw_url:
            continue
        url = normalize_discovery_url(raw_url, base_url)
        if not url:
            continue
        same_organization = is_same_organization(base_url, url)
        if url in seen:
            continue
        label = clean_text(text)
        haystack = f"{label} {url}".lower()
        if not any(keyword.lower() in haystack for keyword in POSITIVE_KEYWORDS):
            continue
        if not same_organization and not any(
            keyword in haystack
            for keyword in ("수강신청", "강좌신청", "교육신청", "프로그램신청", "신청하기", "예약", "reserve", "apply")
        ):
            continue
        seen.add(url)
        link_score, _reasons = candidate_score(url, label, "", base_url)
        candidates.append(
            Candidate(
                url=url,
                title=label,
                source=f"{'internal' if same_organization else 'external'}_link:{source_kind}",
                source_page=base_url,
                source_level=source_level,
                link_score=link_score,
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def sitemap_candidates(
    base_url: str,
    limit: int,
    http_session: requests.Session | None = None,
    errors: list[dict[str, str]] | None = None,
) -> list[Candidate]:
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return []
    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    context = nullcontext(http_session) if http_session is not None else session()
    try:
        with context as active_session:
            response = active_session.get(sitemap_url, timeout=10)
            response.raise_for_status()
            urls = re.findall(r"<loc>\s*([^<]+)\s*</loc>", response.text, flags=re.IGNORECASE)
    except OutboundResponseTooLarge:
        if errors is not None:
            errors.append({"source": "sitemap", "error": "response_too_large"})
        return []
    except OutboundRequestBlocked:
        if errors is not None:
            errors.append({"source": "sitemap", "error": "ssrf_blocked"})
        return []
    except requests.Timeout:
        if errors is not None:
            errors.append({"source": "sitemap", "error": "timeout"})
        return []
    except requests.RequestException as exc:
        if errors is not None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            errors.append({"source": "sitemap", "error": f"http_{status}" if status else "network_error"})
        return []
    candidates = []
    for raw_url in urls:
        url = normalize_discovery_url(raw_url)
        if not url or not is_same_organization(base_url, url):
            continue
        haystack = url.lower()
        if any(keyword.lower() in haystack for keyword in POSITIVE_KEYWORDS):
            link_score, _reasons = candidate_score(url, "", "", base_url)
            candidates.append(Candidate(url=url, source="sitemap", source_page=sitemap_url, link_score=link_score))
        if len(candidates) >= limit:
            break
    return candidates


def target_queries(target: dict[str, Any]) -> list[str]:
    name = clean_text(target.get("name"))
    url = clean_text(target.get("url") or target.get("list_url") or target.get("base_url"))
    host = normalized_netloc(url)
    base_terms = [name, host]
    queries = []
    for base in base_terms:
        if not base:
            continue
        for suffix in ("수강신청", "강좌신청", "교육신청", "통합예약", "예약", "프로그램 신청"):
            queries.append(f"{base} {suffix}")
    if host:
        for suffix in ("수강신청", "강좌신청", "예약", "교육신청"):
            queries.append(f"site:{host} {suffix}")
    return list(dict.fromkeys(queries))[:8]


def _parse_explicit_course_date(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return value
    start, _end = parse_date_range(clean_text(value))
    return start


def persisted_registration_schedule_count(rows: list[dict[str, Any]]) -> int:
    count = 0
    today = datetime.now().date()
    for row in rows:
        raw_period = clean_text(row.get("apply_period") or row.get("apply_period_raw"))
        parsed_start, parsed_end = parse_date_range(raw_period)
        explicit_start = _parse_explicit_course_date(row.get("apply_start"))
        explicit_end = _parse_explicit_course_date(row.get("apply_end"))
        start = explicit_start or parsed_start
        end = explicit_end or parsed_end or start
        if (
            (start or end)
            and not (start and end and start > end)
            and (end or start) >= today
            and today.year - 1 <= (start or end).year <= today.year + 3
        ):
            count += 1
    return count


def fetch_candidate(
    candidate: Candidate,
    source_url: str,
    provider: str,
    timeout: int,
    http_session: requests.Session | None = None,
    prefetched: FetchedPage | None = None,
) -> Candidate:
    context = nullcontext(http_session) if http_session is not None else session()
    try:
        with context as active_session:
            page = prefetched or fetch_page(active_session, candidate.url, timeout)
        candidate.final_url = page.final_url
        candidate.status_code = page.status_code
        candidate.content_type = page.content_type
        candidate.response_bytes = page.response_bytes
        candidate.error_kind = page.error_kind
        candidate.error = page.error
        effective_url = page.final_url or candidate.url
        candidate.same_organization = is_same_organization(source_url, effective_url)
        candidate.host_allowed = is_configured_host(source_url, effective_url)
        if page.error or page.soup is None:
            candidate.score = 0
            candidate.verdict = (
                "unreachable"
                if page.error_kind in {"timeout", "network_error", "http_4xx", "http_5xx"}
                else "rejected"
            )
            return candidate

        candidate.title = page.title or candidate.title
        parsed_rows, parser = parse_all_courses(provider, candidate.title or provider, effective_url, page.soup)
        rows = filter_generic_miscollected_rows(parsed_rows)
        candidate.rows = len(rows)
        candidate.parser = parser
        fields = {}
        for field_name in (
            "title",
            "period",
            "schedule_raw",
            "apply_start",
            "apply_end",
            "apply_period",
            "apply_period_raw",
            "target",
            "fee",
            "description",
            "application_url",
        ):
            fields[field_name] = sum(1 for row in rows if row.get(field_name))
        fields["persisted_registration_schedule"] = persisted_registration_schedule_count(rows)
        candidate.field_counts = fields
        score, reasons = candidate_score(effective_url, candidate.title, page.text, source_url)
        if candidate.rows:
            score += min(20, 8 + candidate.rows * 3)
            reasons.append(f"parsed_rows={candidate.rows}")
        if any(
            fields.get(field)
            for field in ("period", "schedule_raw", "apply_start", "apply_end", "apply_period", "apply_period_raw")
        ):
            score += 15
            reasons.append("date_or_schedule_parsed")
        if fields.get("application_url"):
            score += 10
            reasons.append("application_url_parsed")
        if not candidate.same_organization:
            score -= 20
            reasons.append("external_domain")
        candidate.score = max(0, min(score, 100))
        candidate.reasons = reasons

        negative = "negative_url" in reasons or "negative_text" in reasons
        structured = any(
            fields.get(field)
            for field in (
                "period",
                "schedule_raw",
                "apply_start",
                "apply_end",
                "apply_period",
                "apply_period_raw",
                "application_url",
            )
        )
        hard_negative = "negative_url" in reasons
        soft_negative = "negative_text" in reasons
        negative_override = candidate.registration_schedule_ready and candidate.rows >= 2
        if provider in CULTURE_PROVIDERS:
            candidate.reasons.append("dedicated_crawler_probe_required")
            candidate.verdict = "provider_probe_required"
        elif hard_negative and not candidate.registration_schedule_ready:
            candidate.verdict = "false_positive"
        elif (
            candidate.parse_ready
            and candidate.host_allowed
            and candidate.score >= 60
            and not hard_negative
            and (not soft_negative or negative_override)
        ):
            candidate.verdict = "verified"
        elif candidate.parse_ready and not candidate.host_allowed and candidate.score >= 50:
            candidate.verdict = "external_booking_review"
        elif candidate.score >= 50 and (structured or candidate.rows):
            candidate.verdict = "promising"
        elif negative and not structured:
            candidate.verdict = "false_positive"
        else:
            candidate.verdict = "weak"
    except Exception as exc:
        candidate.error_kind = "parser_error"
        candidate.error = f"{type(exc).__name__}: candidate parser failed"
        candidate.score = 0
        candidate.verdict = "rejected"
    return candidate


def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    deduped: dict[str, Candidate] = {}
    for candidate in candidates:
        normalized = normalize_discovery_url(candidate.url)
        if not normalized:
            continue
        clean_url = normalized.rstrip("/") or normalized
        current = deduped.get(clean_url)
        preferred = current is None or (
            candidate.source == "configured_target"
            or (current.source != "configured_target" and candidate.link_score > current.link_score)
        )
        if preferred:
            candidate.url = normalized
            deduped[clean_url] = candidate
    return list(deduped.values())


def needs_discovery(target: dict[str, Any], db_stats: dict[str, dict[str, Any]]) -> bool:
    provider = clean_text(target.get("provider")).upper()
    status = clean_text(target.get("crawler_status")).lower()
    if status in {"needs_discovery", "needs_parser", "blocked"}:
        return True
    collection_type = clean_text(target.get("collection_type")).lower()
    if collection_type in {
        "external_article",
        "external_guide",
        "info_only",
        "notice_article",
        "notice_attachment",
        "splash_or_homepage",
        "unknown",
    }:
        return True
    target_url = clean_text(target.get("url") or target.get("list_url") or target.get("base_url"))
    if any(token in target_url.lower() for token in NEGATIVE_URL_TOKENS):
        return True
    stats = db_stats.get(provider)
    if not stats:
        return True
    active = int(stats.get("active") or 0)
    online = int(stats.get("online_reservation") or 0)
    application_url = int(stats.get("application_url") or 0)
    registration_alarm_ready = int(stats.get("registration_alarm_ready_future") or 0)
    alert_candidate_population = int(stats.get("alert_candidate_population") or 0)
    external = int(stats.get("external_notice") or 0)
    info = int(stats.get("info_only") or 0)
    if active == 0:
        return True
    if application_url < active:
        return True
    if (
        provider in CULTURE_PROVIDERS
        and alert_candidate_population > 0
        and registration_alarm_ready < alert_candidate_population
    ):
        return True
    return online == 0 and (external > 0 or info > 0)


def load_db_application_stats() -> dict[str, dict[str, Any]]:
    try:
        from DB.db_utils import get_db_cursor

        with get_db_cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '15s'")
            cur.execute(
                """
                SELECT provider,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE COALESCE(is_active, FALSE)) AS active,
                       COUNT(*) FILTER (
                           WHERE COALESCE(is_active, FALSE)
                             AND NULLIF(btrim(application_url), '') IS NOT NULL
                             AND (
                               application_type = 'ONLINE_RESERVATION'
                              OR (
                                  application_type IS NULL
                                  AND COALESCE(application_method_raw, '') ~ '(온라인|인터넷|홈페이지|웹|사이트|수강신청|예약|접수)'
                              )
                             )
                       ) AS online_reservation,
                       COUNT(*) FILTER (
                           WHERE COALESCE(is_active, FALSE)
                             AND NULLIF(btrim(application_url), '') IS NOT NULL
                       ) AS application_url,
                       COUNT(*) FILTER (
                           WHERE COALESCE(is_active, FALSE)
                             AND (
                               status = 'SCHEDULED'
                               OR start_date >= CURRENT_DATE
                               OR (start_date IS NULL AND end_date >= CURRENT_DATE)
                             )
                             AND (apply_start IS NULL OR apply_start >= CURRENT_DATE)
                       ) AS alert_candidate_population,
                       COUNT(*) FILTER (
                           WHERE COALESCE(is_active, FALSE)
                             AND (
                               status = 'SCHEDULED'
                               OR start_date >= CURRENT_DATE
                               OR (start_date IS NULL AND end_date >= CURRENT_DATE)
                             )
                             AND apply_start IS NOT NULL
                             AND apply_start >= CURRENT_DATE
                       ) AS registration_alarm_ready_future,
                       COUNT(*) FILTER (
                           WHERE COALESCE(is_active, FALSE)
                             AND apply_start IS NULL
                             AND NULLIF(btrim(apply_period_raw), '') IS NOT NULL
                       ) AS registration_raw_only,
                       COUNT(*) FILTER (WHERE COALESCE(is_active, FALSE) AND application_type = 'EXTERNAL_NOTICE') AS external_notice,
                       COUNT(*) FILTER (WHERE COALESCE(is_active, FALSE) AND application_type = 'INFO_ONLY') AS info_only
                FROM courses
                GROUP BY provider
                """
            )
            return {str(row["provider"]).upper(): dict(row) for row in cur.fetchall()}
    except Exception as exc:
        raise RuntimeError(f"database preflight failed: {type(exc).__name__}") from exc


def candidate_payload(item: Candidate) -> dict[str, Any]:
    return {
        "url": item.url,
        "final_url": item.final_url,
        "title": item.title,
        "source": item.source,
        "source_page": item.source_page,
        "source_level": item.source_level,
        "score": item.score,
        "reasons": item.reasons,
        "verdict": item.verdict,
        "parser": item.parser,
        "rows": item.rows,
        "field_counts": item.field_counts,
        "parse_ready": item.parse_ready,
        "registration_schedule_ready": item.registration_schedule_ready,
        "application_path_ready": item.application_path_ready,
        "same_organization": item.same_organization,
        "host_allowed": item.host_allowed,
        "status_code": item.status_code,
        "content_type": item.content_type,
        "response_bytes": item.response_bytes,
        "needs_parser": not item.parse_ready,
        "error_kind": item.error_kind,
        "error": item.error,
    }


def recommend_target_url(
    source_url: str,
    current: Candidate | None,
    evaluated: list[Candidate],
    min_score: int,
) -> dict[str, Any]:
    current_effective = normalize_discovery_url(
        (current.final_url or current.url) if current else source_url
    )
    alternatives = [
        item
        for item in evaluated
        if normalize_discovery_url(item.final_url or item.url).rstrip("/")
        != current_effective.rstrip("/")
    ]
    eligible = [
        item
        for item in alternatives
        if not item.error
        and item.verdict == "verified"
        and item.host_allowed
        and item.parse_ready
        and item.score >= min_score
        and urlparse(item.final_url or item.url).scheme.lower() == "https"
    ]
    eligible.sort(
        key=lambda item: (
            not item.registration_schedule_ready,
            -item.score,
            -item.rows,
            item.source_level,
            len(item.final_url or item.url),
        )
    )

    schedule_upgrade = next(
        (
            item
            for item in eligible
            if item.registration_schedule_ready
            and (current is None or not current.registration_schedule_ready)
        ),
        None,
    )
    if current and current.verdict == "verified" and schedule_upgrade is not None:
        return {
            "action": "review_candidate",
            "confidence": "medium",
            "recommended_url": schedule_upgrade.final_url or schedule_upgrade.url,
            "reason": "current target collects courses but the candidate adds future registration schedule evidence",
            "candidate_score": schedule_upgrade.score,
        }

    if current and current.verdict == "verified":
        if (
            current.final_url
            and normalize_discovery_url(source_url).rstrip("/") != current_effective.rstrip("/")
            and urlparse(current.final_url).scheme.lower() == "https"
            and current.host_allowed
        ):
            return {
                "action": "canonicalize_target",
                "confidence": "high",
                "recommended_url": current.final_url,
                "reason": "configured target redirects to a verified canonical HTTPS URL",
            }
        return {
            "action": "keep_current",
            "confidence": "high" if current.registration_schedule_ready else "medium",
            "recommended_url": source_url,
            "reason": (
                "current target yields registration schedule evidence"
                if current.registration_schedule_ready
                else "current target yields structured courses; registration schedule remains a parser/data task"
            ),
        }

    if eligible:
        best = eligible[0]
        recommended_url = best.final_url or best.url
        current_bad = current is None or bool(current.error) or current.verdict in {
            "false_positive",
            "rejected",
            "unreachable",
            "weak",
        }
        if best.registration_schedule_ready and current_bad:
            return {
                "action": "replace_target",
                "confidence": "high" if best.score >= max(75, min_score) else "medium",
                "recommended_url": recommended_url,
                "reason": "same-host HTTPS candidate yields structured courses and registration schedule evidence",
                "candidate_score": best.score,
            }
        return {
            "action": "review_candidate",
            "confidence": "medium",
            "recommended_url": recommended_url,
            "reason": "candidate improves collection evidence but lacks a safe automatic replacement gate",
            "candidate_score": best.score,
        }

    if any(item.verdict == "provider_probe_required" for item in evaluated):
        reason = "generic HTML evidence is insufficient; the dedicated Provider crawler must pass a read-only probe"
    elif any(item.verdict == "external_booking_review" for item in alternatives):
        reason = "only cross-host booking candidates were found; ownership requires manual review"
    elif evaluated and all(item.error for item in evaluated):
        reason = "every hierarchy and candidate request failed"
    elif any(item.parse_ready for item in alternatives):
        reason = "structured candidates did not meet same-host HTTPS confidence gates"
    else:
        reason = "no candidate produced reliable structured course evidence"
    return {
        "action": "unresolved",
        "confidence": "none",
        "recommended_url": "",
        "reason": reason,
    }


def discover_for_target(target: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    provider = clean_text(target.get("provider")).upper()
    source_url = normalize_discovery_url(target.get("url") or target.get("list_url") or target.get("base_url"))
    candidates: list[Candidate] = []
    if not source_url:
        return {
            "provider": provider,
            "name": target.get("name"),
            "source_url": "",
            "candidates": [],
            "recommended_action": "unresolved",
            "error_kind": "invalid_source_url",
            "error": "missing or unsafe source URL",
        }

    max_candidates = max(1, min(int(getattr(args, "max_candidates", 5)), 20))
    timeout = max(1, min(int(getattr(args, "timeout", 15)), 120))
    min_score = max(0, min(int(getattr(args, "min_score", 60)), 100))
    max_parent_levels = max(1, min(int(getattr(args, "max_parent_levels", MAX_PARENT_LEVELS)), MAX_PARENT_LEVELS))
    request_budget = max(20, min(int(getattr(args, "request_budget", 100)), 500))
    hierarchy: list[dict[str, Any]] = []
    discovery_errors: list[dict[str, str]] = []
    page_cache: dict[str, FetchedPage] = {}
    robots_cache: dict[str, RobotsPolicy] = {}
    request_timestamps: dict[str, float] = {}

    with outbound_request_budget(request_budget), session() as http_session:
        http_session.headers["User-Agent"] = DISCOVERY_USER_AGENT
        for seed in discovery_seed_urls(source_url, max_parent_levels=max_parent_levels):
            allowed, robots_reason = robots_allowed(http_session, seed.url, timeout, robots_cache)
            if allowed:
                page = fetch_page(http_session, seed.url, timeout, robots_cache, request_timestamps)
            else:
                page = FetchedPage(
                    requested_url=seed.url,
                    error_kind=robots_reason,
                    error=robots_reason.replace("_", " "),
                )
            page_cache[seed.url] = page
            seed_candidate = Candidate(
                url=seed.url,
                title=page.title,
                source=seed.kind,
                source_page=seed.url,
                source_level=seed.level,
                link_score=100 if seed.kind == "configured_target" else max(0, 50 - seed.level),
            )
            candidates.append(seed_candidate)
            links: list[Candidate] = []
            if page.soup is not None and not page.error:
                links = extract_links(
                    page.final_url or seed.url,
                    page.soup,
                    max_candidates * 4,
                    source_kind=seed.kind,
                    source_level=seed.level,
                )
                candidates.extend(links)
            hierarchy.append(
                {
                    "url": seed.url,
                    "kind": seed.kind,
                    "level": seed.level,
                    "robots": robots_reason,
                    "status_code": page.status_code,
                    "final_url": page.final_url,
                    "content_type": page.content_type,
                    "links_found": len(links),
                    "error_kind": page.error_kind,
                    "error": page.error,
                }
            )

        try:
            candidates.extend(
                sitemap_candidates(
                    source_url,
                    max_candidates * 3,
                    http_session,
                    errors=discovery_errors,
                )
            )
        except Exception as exc:
            discovery_errors.append({"source": "sitemap", "error": type(exc).__name__})

        queries = target_queries(target)
        if getattr(args, "google", False):
            for query in queries:
                try:
                    candidates.extend(google_cse_candidates(query, max_candidates, http_session))
                except Exception as exc:
                    discovery_errors.append({"source": "google_cse", "error": type(exc).__name__})

        deduped = dedupe_candidates(candidates)
        deduped.sort(
            key=lambda item: (
                item.source != "configured_target",
                -item.link_score,
                item.source_level,
                len(item.url),
            )
        )
        hierarchy_urls = {
            normalize_discovery_url(item["url"]).rstrip("/")
            for item in hierarchy
            if item.get("url")
        }
        hierarchy_candidates = [
            item
            for item in deduped
            if normalize_discovery_url(item.url).rstrip("/") in hierarchy_urls
        ]
        other_candidates = [item for item in deduped if item not in hierarchy_candidates]
        deduped = hierarchy_candidates + other_candidates
        evaluation_limit = max(max_candidates * 6, len(hierarchy))
        evaluated: list[Candidate] = []
        for candidate in deduped[:evaluation_limit]:
            prefetched = page_cache.get(candidate.url)
            if prefetched is None:
                allowed, robots_reason = robots_allowed(http_session, candidate.url, timeout, robots_cache)
                if allowed:
                    prefetched = fetch_page(
                        http_session,
                        candidate.url,
                        timeout,
                        robots_cache,
                        request_timestamps,
                    )
                else:
                    prefetched = FetchedPage(
                        requested_url=candidate.url,
                        error_kind=robots_reason,
                        error=robots_reason.replace("_", " "),
                    )
            evaluated.append(
                fetch_candidate(
                    candidate,
                    source_url,
                    provider,
                    timeout,
                    http_session=http_session,
                    prefetched=prefetched,
                )
            )

    current = next((item for item in evaluated if item.source == "configured_target"), None)
    recommendation = recommend_target_url(source_url, current, evaluated, min_score)
    evaluated.sort(
        key=lambda row: (
            row.error != "",
            row.verdict not in {"verified", "external_booking_review", "promising"},
            not row.registration_schedule_ready,
            -row.score,
            -row.rows,
            len(row.final_url or row.url),
        )
    )
    best = evaluated[:max_candidates]
    recommended_url = recommendation.get("recommended_url") or ""
    best_candidate = next(
        (
            item
            for item in evaluated
            if recommended_url
            and normalize_discovery_url(item.final_url or item.url).rstrip("/")
            == normalize_discovery_url(recommended_url).rstrip("/")
        ),
        best[0] if best else None,
    )
    return {
        "provider": provider,
        "name": clean_text(target.get("name")),
        "source_url": source_url,
        "crawler_status": clean_text(target.get("crawler_status")),
        "target_file": clean_text(target.get("_target_file")),
        "target_id": clean_text(target.get("target_id")),
        "queries": queries,
        "source_hierarchy": hierarchy,
        "discovery_errors": discovery_errors,
        "current_assessment": candidate_payload(current) if current else {},
        "recommended_action": recommendation.get("action"),
        "recommendation": recommendation,
        "best_score": best_candidate.score if best_candidate else 0,
        "best_url": (best_candidate.final_url or best_candidate.url) if best_candidate else "",
        "best_parse_ready": bool(best_candidate and best_candidate.parse_ready),
        "best_registration_schedule_ready": bool(best_candidate and best_candidate.registration_schedule_ready),
        "candidates": [candidate_payload(item) for item in best],
        "failures": [
            {
                "url": item.url,
                "source": item.source,
                "error_kind": item.error_kind,
                "error": item.error,
            }
            for item in evaluated
            if item.error
        ][:20],
    }


def select_targets(args: argparse.Namespace) -> list[dict[str, Any]]:
    rows = _iter_target_rows(TARGETS_FILE)
    providers = {provider.upper() for provider in args.provider or []}
    db_stats = {} if providers else load_db_application_stats()
    selected = []
    for row in rows:
        provider = clean_text(row.get("provider")).upper()
        status = clean_text(row.get("crawler_status")).lower()
        if status in {"deprecated", "excluded_url_shape"} or status.startswith("duplicate_url:"):
            continue
        if providers and provider not in providers:
            continue
        if not providers and not args.include_culture and provider in CULTURE_PROVIDERS:
            continue
        if not providers and not needs_discovery(row, db_stats):
            continue
        selected.append(row)
    selected.sort(key=lambda row: (int(row.get("priority") or 9), clean_text(row.get("provider"))))
    if args.offset:
        selected = selected[args.offset :]
    if args.limit:
        selected = selected[: args.limit]
    return selected


def write_report(results: list[dict[str, Any]], args: argparse.Namespace) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{os.getpid()}"
    path = REPORT_DIR / f"url_discovery_{run_id}.yaml"
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "hierarchy_internal_links+google_cse" if args.google else "hierarchy_internal_links",
        "summary": {
            "targets": len(results),
            "with_candidates": sum(1 for row in results if row.get("candidates")),
            "keep_current": sum(1 for row in results if row.get("recommended_action") == "keep_current"),
            "canonicalize_target": sum(1 for row in results if row.get("recommended_action") == "canonicalize_target"),
            "replace_target": sum(1 for row in results if row.get("recommended_action") == "replace_target"),
            "review_candidate": sum(1 for row in results if row.get("recommended_action") == "review_candidate"),
            "unresolved": sum(1 for row in results if row.get("recommended_action") == "unresolved"),
            "current_false_positive": sum(
                1
                for row in results
                if (row.get("current_assessment") or {}).get("verdict") == "false_positive"
            ),
            "promising": sum(
                1
                for row in results
                if any(
                    candidate.get("verdict") in {"verified", "external_booking_review", "promising"}
                    and int(candidate.get("score") or 0) >= args.min_score
                    for candidate in row.get("candidates") or []
                )
            ),
            "parse_ready": sum(1 for row in results if any(candidate.get("parse_ready") for candidate in row.get("candidates") or [])),
            "registration_schedule_ready": sum(
                1
                for row in results
                if any(candidate.get("registration_schedule_ready") for candidate in row.get("candidates") or [])
            ),
            "failed_targets": sum(1 for row in results if row.get("error")),
            "min_score": args.min_score,
        },
        "results": results,
    }
    content = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose and discover safer collection target URLs for weak crawler targets.")
    parser.add_argument("--provider", action="append", help="Provider to inspect. Can be repeated.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--min-score", type=int, default=60)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--max-parent-levels", type=int, default=MAX_PARENT_LEVELS)
    parser.add_argument("--request-budget", type=int, default=100, help="Maximum safe HTTP hops per target.")
    parser.add_argument("--google", action="store_true", help="Use Google Custom Search API when GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_CX are set.")
    parser.add_argument("--include-culture", action="store_true", help="Include culture-center providers in the discovery batch.")
    args = parser.parse_args()

    try:
        targets = select_targets(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    results = []
    for index, target in enumerate(targets, start=1):
        print(f"[{index}/{len(targets)}] {target.get('provider')} {target.get('url') or target.get('list_url') or target.get('base_url')}")
        results.append(discover_for_target(target, args))
    report = write_report(results, args)
    print(f"report={report}")
    for row in results:
        recommendation = row.get("recommendation") or {}
        print(
            f"{row.get('provider')} action={recommendation.get('action', 'unresolved')} "
            f"confidence={recommendation.get('confidence', 'none')} "
            f"url={recommendation.get('recommended_url', '')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
