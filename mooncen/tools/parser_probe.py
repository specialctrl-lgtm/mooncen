"""Restricted generic HTML parser probe used by the integrated Ops worker."""

from __future__ import annotations

import html
import re
import time
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup

from Crawler.reception_period import extract_reception_period
from data_parser import parse_crawler_target
from service_group import infer_service_group
from utils import clean_text, extract_number, infer_course_status
from utils.outbound_http import OutboundRequestBlocked, SafeSession, validate_outbound_url


MAX_RESPONSE_BYTES = 2_000_000
USER_AGENT = "MoonCen-Ops-ParserProbe/2.0"
CULTURE_CENTER_PROVIDERS = {
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
FIELD_LABELS = {
    "title": ("강좌명", "교육명", "프로그램명", "강의명", "과정명", "행사명", "제목"),
    "branch": ("지점", "교육기관", "운영기관", "기관", "센터", "장소", "시설명"),
    "instructor": ("강사명", "지도강사", "강사"),
    "period": ("강좌기간", "강의기간", "교육기간", "수강기간", "운영기간", "기간"),
    "apply_period_raw": ("접수기간", "신청기간", "모집기간", "접수일시", "신청일시"),
    "schedule_raw": ("요일/시간", "강좌시간", "강의시간", "교육시간", "수강시간", "운영시간", "일시"),
    "target": ("수강대상", "교육대상", "참여대상", "신청대상", "접수나이", "대상"),
    "age_raw": ("연령", "나이", "대상연령", "수강연령"),
    "fee": ("수강료", "교육비", "참가비", "이용료", "금액", "비용"),
    "status_raw": ("접수상태", "모집상태", "상태"),
    "capacity_total": ("정원", "모집인원", "신청/정원"),
}
QUALITY_WEIGHTS = {
    "title": 20,
    "branch": 15,
    "raw_url": 15,
    "status": 10,
    "period": 10,
    "schedule_raw": 10,
    "description": 10,
    "target": 5,
    "fee": 5,
}


def _public_url(value: Any) -> str:
    try:
        return validate_outbound_url(str(value or "").strip()).url
    except OutboundRequestBlocked as exc:
        raise ValueError("URL must be a public HTTP(S) endpoint") from exc


def _decode(body: bytes, header_charset: str | None) -> tuple[str, str]:
    candidates: list[tuple[int, str, str]] = []
    for encoding in (header_charset, "utf-8", "cp949", "euc-kr"):
        if not encoding or any(encoding.lower() == row[1].lower() for row in candidates):
            continue
        try:
            decoded = body.decode(encoding)
            penalty = 0
        except UnicodeDecodeError:
            decoded = body.decode(encoding, errors="replace")
            penalty = decoded.count("\ufffd")
        candidates.append((penalty, encoding, decoded))
    if not candidates:
        return body.decode("utf-8", errors="replace"), "utf-8"
    _, encoding, decoded = min(candidates, key=lambda row: row[0])
    return decoded, encoding


def _fetch(url: str, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    with SafeSession(
        max_redirects=5,
        max_response_bytes=MAX_RESPONSE_BYTES + 1,
        total_timeout_seconds=timeout,
    ) as session:
        response = session.get(
            _public_url(url),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
            timeout=timeout,
            allow_redirects=True,
        )
        body = response.content
        final_url = _public_url(response.url)
        content_type = response.headers.get("Content-Type", "")
        status_code = int(response.status_code)
        header_charset = response.encoding
    if not 200 <= status_code < 300:
        raise ValueError(f"Upstream URL returned HTTP {status_code}")
    if "html" not in content_type.lower() and "xhtml" not in content_type.lower():
        raise ValueError(f"Upstream content type is not HTML: {content_type or 'unknown'}")
    truncated = len(body) > MAX_RESPONSE_BYTES
    text, encoding = _decode(body[:MAX_RESPONSE_BYTES], header_charset)
    return {
        "status_code": status_code,
        "final_url": final_url,
        "content_type": content_type,
        "bytes": min(len(body), MAX_RESPONSE_BYTES),
        "truncated": truncated,
        "encoding": encoding,
        "elapsed_ms": int((time.monotonic() - started) * 1_000),
        "text": text,
    }


def _provider(url: str) -> str:
    host = urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
    matches = (
        ("culture.lotteshopping.com", "LOTTE"),
        ("culture.lottemart.com", "LOTTE_MART"),
        ("mschool.homeplus.co.kr", "HOMEPLUS"),
        ("cultureclub.emart.com", "EMART"),
        ("e-hyundai.com", "HYUNDAI_DEPT"),
        ("shinsegae", "SHINSEGAE_ACADEMY"),
        ("galleria", "GALLERIA"),
        ("akplaza", "AK_PLAZA"),
    )
    for marker, provider in matches:
        if marker in host:
            return provider
    return re.sub(r"[^a-z0-9]+", "_", host).strip("_").upper()[:48] or "UNKNOWN"


def _lines(soup: BeautifulSoup, limit: int = 450) -> list[str]:
    fragment = BeautifulSoup(str(soup), "html.parser")
    for node in fragment.select("script,style,noscript,svg,iframe"):
        node.decompose()
    result: list[str] = []
    for raw in fragment.get_text("\n", strip=True).splitlines():
        value = clean_text(html.unescape(raw))
        if len(value) > 1 and (not result or result[-1] != value):
            result.append(value)
        if len(result) >= limit:
            break
    return result


def _labeled_value(lines: list[str], labels: tuple[str, ...]) -> tuple[str, str]:
    pattern = re.compile(
        rf"^(?:\[?\s*)({'|'.join(re.escape(label) for label in sorted(labels, key=len, reverse=True))})"
        r"(?:\s*\]?)(?=$|[\s:：])\s*[:：]?\s*(.*)$"
    )
    all_labels = {label for values in FIELD_LABELS.values() for label in values}
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        value = clean_text(match.group(2))
        if not value and index + 1 < len(lines) and not any(lines[index + 1].startswith(label) for label in all_labels):
            value = clean_text(lines[index + 1])
        if value:
            return value[:1_200], f"line:{index + 1}:{match.group(1)}"
    return "", ""


def _page_title(soup: BeautifulSoup) -> tuple[str, str]:
    candidates = (
        ("meta[property='og:title']", "content"),
        ("meta[name='twitter:title']", "content"),
        ("h1", None),
        ("h2", None),
        ("title", None),
    )
    for selector, attribute in candidates:
        node = soup.select_one(selector)
        if not node:
            continue
        value = clean_text(node.get(attribute) if attribute else node.get_text(" ", strip=True))
        if value:
            return value, selector
    return "", ""


def _application_links(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in soup.select("a[href]"):
        href = str(node.get("href") or "").strip()
        label = clean_text(node.get_text(" ", strip=True))
        if not any(keyword in f"{label} {href}".lower() for keyword in ("신청", "접수", "예약", "apply", "reserve")):
            continue
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urllib.parse.urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        result.append({"url": absolute, "text": label, "source": "a[href]"})
        if len(result) >= 10:
            break
    return result


def _format_age_month_range(min_month: Any, max_month: Any) -> str:
    if min_month is None and max_month is None:
        return ""
    if min_month is None:
        return f"{max_month}개월 이하"
    if max_month is None:
        return f"{min_month}개월 이상"
    if min_month == max_month:
        return f"{min_month}개월"
    return f"{min_month}~{max_month}개월"


def _add_age_fields(fields: dict[str, Any], sources: dict[str, str]) -> None:
    parts: list[str] = []
    source_parts: list[str] = []
    for field_name in ("target", "age_raw"):
        value = clean_text(fields.get(field_name) or "")
        if not value or any(value == part or value in part for part in parts):
            continue
        keep = [(part, source_part) for part, source_part in zip(parts, source_parts) if part not in value]
        parts = [part for part, _source_part in keep]
        source_parts = [source_part for _part, source_part in keep]
        parts.append(value)
        source_parts.append(field_name)
    if not parts:
        return
    source_text = " / ".join(parts)
    parsed = parse_crawler_target(source_text)
    if not (
        parsed.get("age_group")
        or parsed.get("min_age") is not None
        or parsed.get("max_age") is not None
        or parsed.get("age_is_explicit")
    ):
        return
    source = "data_parser.parse_crawler_target:" + "+".join(source_parts)
    for key, value in (
        ("target_age_source", source_text),
        ("target_age_group", parsed.get("age_group")),
        ("target_min_age", parsed.get("min_age")),
        ("target_max_age", parsed.get("max_age")),
        ("target_age_is_explicit", bool(parsed.get("age_is_explicit"))),
        ("target_with_parent", bool(parsed.get("with_parent"))),
    ):
        fields[key] = value
        sources[key] = source
    if parsed.get("tags"):
        fields["target_tags"] = ", ".join(str(item) for item in parsed.get("tags") or [])
        sources["target_tags"] = source
    display = _format_age_month_range(parsed.get("min_age"), parsed.get("max_age"))
    if display:
        fields["target_age_display"] = display
        sources["target_age_display"] = source


def parser_probe(payload: dict[str, Any]) -> dict[str, Any]:
    url = _public_url(payload.get("url"))
    timeout = max(5, min(60, int(payload.get("timeout") or 25)))
    fetched = _fetch(url, timeout)
    html_text = fetched.pop("text")
    final_url = str(fetched["final_url"])
    provider = _provider(final_url)
    soup = BeautifulSoup(html_text, "html.parser")
    lines = _lines(soup)
    full_text = clean_text(" ".join(lines))
    fields: dict[str, Any] = {"raw_url": final_url}
    sources: dict[str, str] = {}
    warnings: list[str] = []

    fields["title"], sources["title"] = _page_title(soup)
    for field_name, labels in FIELD_LABELS.items():
        value, source = _labeled_value(lines, labels)
        if value:
            fields[field_name] = value
            sources[field_name] = source
    _add_age_fields(fields, sources)

    reception = extract_reception_period(full_text, None)
    if reception:
        for name in ("apply_start", "apply_end", "apply_period_raw"):
            value = reception.get(name)
            if value:
                fields[name] = value.isoformat() if hasattr(value, "isoformat") else str(value)
                sources[name] = "reception_period.extract_reception_period"

    description_node = soup.select_one(
        ".description,.content,.course-intro,.lecture-description,article,main"
    )
    if description_node:
        description = clean_text(description_node.get_text(" ", strip=True))
        if description:
            fields["description"] = description[:2_500]
            sources["description"] = "generic description selector"

    candidates = _application_links(soup, final_url)
    if candidates:
        fields["application_url"] = candidates[0]["url"]
        fields["application_candidates"] = candidates
        sources["application_url"] = "a[href] keyword scan"
    if fields.get("fee"):
        fields["fee_amount"] = extract_number(fields["fee"])
    status = infer_course_status(str(fields.get("status_raw") or ""), full_text[:800], default="")
    if status:
        fields["status"] = status
        sources["status"] = "utils.infer_course_status"
    fields["service_group"] = infer_service_group(
        provider=provider,
        branch_name=fields.get("branch"),
        raw_url=final_url,
    )
    sources["service_group"] = "service_group.infer_service_group"

    page_title = clean_text(soup.title.get_text(" ", strip=True) if soup.title else "").lower()
    login_or_challenge = bool(soup.select_one("input[type='password']")) or any(
        marker in page_title for marker in ("로그인", "login", "access denied", "captcha", "cloudflare")
    )
    js_only = len(lines) < 12 and len(soup.select("script")) >= 8
    course_evidence = bool(
        fields.get("title")
        and (
            fields.get("period")
            or fields.get("schedule_raw")
            or fields.get("apply_start")
            or fields.get("apply_period_raw")
        )
    )
    provider_probe_required = provider in CULTURE_CENTER_PROVIDERS
    safe_to_stage = not login_or_challenge and not js_only and course_evidence and not provider_probe_required
    if not fields.get("title"):
        warnings.append("title was not detected")
    if js_only:
        warnings.append("page looks JavaScript-heavy")
    if provider_probe_required:
        warnings.append("culture-center URL requires its dedicated provider crawler dry-run")
    if fetched.get("truncated"):
        warnings.append(f"response was truncated at {MAX_RESPONSE_BYTES} bytes")

    filled = [name for name in QUALITY_WEIGHTS if fields.get(name)]
    missing = [name for name in QUALITY_WEIGHTS if not fields.get(name)]
    score = sum(QUALITY_WEIGHTS[name] for name in filled)
    return {
        "ok": safe_to_stage,
        "error": "" if safe_to_stage else "probe did not meet safe staging gates",
        "parser": "ops-generic-html-v2",
        "url": url,
        "final_url": final_url,
        "host": urllib.parse.urlsplit(final_url).netloc.lower(),
        "provider_guess": provider,
        "fetch": fetched,
        "quality": {
            "score": round(score / sum(QUALITY_WEIGHTS.values()) * 100, 1),
            "filled": filled,
            "missing": missing,
            "warnings": warnings,
        },
        "probe_gates": {
            "fetch_ok": True,
            "html_ok": True,
            "login_or_challenge": login_or_challenge,
            "js_only": js_only,
            "course_evidence": course_evidence,
            "registration_schedule_ready": bool(fields.get("apply_start") or fields.get("apply_period_raw")),
            "application_path_ready": bool(fields.get("application_url")),
            "provider_probe_required": provider_probe_required,
            "safe_to_stage": safe_to_stage,
        },
        "fields": fields,
        "sources": sources,
        "evidence": {
            "matched_selectors": [
                {"field": field, "source": source, "value": str(fields.get(field) or "")[:240]}
                for field, source in sources.items()
            ],
            "sample_lines": lines[:80],
            "raw_text_preview": full_text[:4_000],
        },
    }
