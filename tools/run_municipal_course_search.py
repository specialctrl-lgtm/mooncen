from __future__ import annotations

import argparse
import base64
import html
import json
import re
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
import yaml
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "config" / "municipal_course_search_targets.yaml"
OUT = ROOT / "config" / "municipal_course_search_results.yaml"

INCLUDE_KEYWORDS = (
    "강좌",
    "강좌신청",
    "수강",
    "수강신청",
    "통합예약",
    "평생학습",
    "문화강좌",
    "사회복지관",
    "도서관",
    "주민자치",
    "학습관",
    "평생학습관",
    "교육관",
    "교육센터",
    "미술관",
    "수목원",
    "박물관",
    "스포츠센터",
    "체육센터",
    "국민체육센터",
    "생활체육센터",
    "공공체육시설",
    "체육관",
    "교육",
    "도서관",
    "시설관리공단",
    "시설공단",
    "도시관리공단",
    "도시공사",
    "도시개발공사",
    "공단",
    "공사",
    "재단",
    "문화재단",
    "체육회",
    "체육",
    "생활체육",
)
REJECT_KEYWORDS = (
    "사회복지사",
    "자격증",
    "학점은행",
    "대학교",
    "대학",
    "보수교육",
    "나무위키",
    "위키백과",
    "블로그",
    "카페",
    "뉴스",
    "초등학교",
    "중학교",
    "고등학교",
    "대학교",
    "방과후학교",
    "늘봄학교",
    "입학",
    "학사",
    "학부모",
)
SCHOOL_HOST_TOKENS = (
    ".es.kr",
    ".ms.kr",
    ".hs.kr",
    ".ac.kr",
    "school",
)
ALLOWED_PUBLIC_ORG_KEYWORDS = (
    "도서관",
    "시설관리공단",
    "시설공단",
    "도시관리공단",
    "도시공사",
    "도시개발공사",
    "공단",
    "공사",
    "재단",
    "문화재단",
    "청소년재단",
    "체육회",
    "체육",
    "생활체육",
    "평생학습",
    "통합예약",
)

REGION_LABEL_GROUPS = {
    "seoul": ("서울특별시", "서울시", "서울"),
    "busan": ("부산광역시", "부산시", "부산"),
    "daegu": ("대구광역시", "대구시", "대구"),
    "incheon": ("인천광역시", "인천시", "인천"),
    "gwangju": ("광주광역시",),
    "daejeon": ("대전광역시", "대전시", "대전"),
    "ulsan": ("울산광역시", "울산시", "울산"),
    "sejong": ("세종특별자치시", "세종시", "세종"),
    "gyeonggi": ("경기도",),
    "gangwon": ("강원특별자치도", "강원도"),
    "chungbuk": ("충청북도",),
    "chungnam": ("충청남도",),
    "jeonbuk": ("전북특별자치도", "전라북도"),
    "jeonnam": ("전라남도",),
    "gyeongbuk": ("경상북도",),
    "gyeongnam": ("경상남도",),
    "jeju": ("제주특별자치도", "제주도"),
    "jeonnam_gwangju": ("전남광주통합특별시",),
}

SIDO_REGION_GROUP = {
    "서울특별시": "seoul",
    "부산광역시": "busan",
    "대구광역시": "daegu",
    "인천광역시": "incheon",
    "광주광역시": "gwangju",
    "대전광역시": "daejeon",
    "울산광역시": "ulsan",
    "세종특별자치시": "sejong",
    "경기도": "gyeonggi",
    "강원특별자치도": "gangwon",
    "강원도": "gangwon",
    "충청북도": "chungbuk",
    "충청남도": "chungnam",
    "전북특별자치도": "jeonbuk",
    "전라북도": "jeonbuk",
    "전라남도": "jeonnam",
    "경상북도": "gyeongbuk",
    "경상남도": "gyeongnam",
    "제주특별자치도": "jeju",
}


def municipality_region_groups(municipality: dict[str, Any]) -> set[str]:
    """Return current and legacy province groups accepted for a result row."""

    sido = clean(str(municipality.get("sido") or ""))
    if sido != "전남광주통합특별시":
        group = SIDO_REGION_GROUP.get(sido)
        return {group} if group else set()

    # The 2026 merged province keeps former Gwangju and Jeonnam public sites.
    # Codes below 1250000000 are the former Gwangju city/district range; the
    # remaining cities/counties retain former Jeonnam URLs and labels.
    code = clean(str(municipality.get("code") or ""))
    legacy_group = "gwangju" if code.isdigit() and int(code) < 1_250_000_000 else "jeonnam"
    return {"jeonnam_gwangju", legacy_group}


def municipality_region_mismatches(text: str, municipality: dict[str, Any]) -> tuple[list[str], bool]:
    allowed_groups = municipality_region_groups(municipality)
    allowed_labels = {
        label
        for group in allowed_groups
        for label in REGION_LABEL_GROUPS.get(group, ())
    }
    has_allowed_label = any(label in text for label in allowed_labels)
    mismatches = sorted(
        {
            label
            for group, labels in REGION_LABEL_GROUPS.items()
            if group not in allowed_groups
            for label in labels
            if label in text
        }
    )
    return mismatches, has_allowed_label


def query_entries_for_municipality(municipality: dict[str, Any]) -> list[dict[str, str]]:
    categorized = municipality.get("categorized_queries") or []
    if categorized:
        return [
            {
                "query": str(entry.get("query") or ""),
                "category_id": str(entry.get("category_id") or "unknown"),
                "category_name": str(entry.get("category_name") or "미분류"),
                "keyword": str(entry.get("keyword") or ""),
            }
            for entry in categorized
            if entry.get("query")
        ]

    queries = [municipality["primary_query"]] + list(municipality.get("alternate_queries", []))
    return [
        {
            "query": str(query),
            "category_id": "municipality" if index == 0 else "unknown",
            "category_name": "지자체" if index == 0 else "미분류",
            "keyword": "",
        }
        for index, query in enumerate(queries)
    ]


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def yaml_dump(data: Any, path: Path) -> None:
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
    path.write_text(text, encoding="utf-8")


def decode_bing_url(url: str) -> str:
    parsed = urlparse(url)
    if "bing.com" not in parsed.netloc:
        return url

    query = parse_qs(parsed.query)
    encoded = query.get("u", [""])[0]
    if not encoded:
        return url

    if encoded.startswith("a1"):
        encoded = encoded[2:]
    try:
        padding = "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8", "ignore")
    except Exception:
        return unquote(encoded)


def classify_result(title: str, snippet: str, url: str, municipality: dict[str, Any]) -> tuple[str, int, list[str]]:
    text = f"{title} {snippet} {url}"
    reasons: list[str] = []
    score = 0

    for keyword in INCLUDE_KEYWORDS:
        if keyword in text:
            score += 2
            reasons.append(f"include:{keyword}")

    short_name = municipality.get("sigungu", "")
    full_name = municipality.get("full_name", "")
    if short_name and short_name in text:
        score += 3
        reasons.append("match:sigungu")
    if full_name and full_name in text:
        score += 3
        reasons.append("match:full_name")

    region_mismatches, has_target_region = municipality_region_mismatches(text, municipality)
    region_conflict = bool(region_mismatches and not has_target_region)
    if has_target_region:
        score += 2
        reasons.append("match:region")
    elif region_mismatches:
        # Names such as 중구/서구/남구/북구/강서구 repeat across provinces.
        # An explicit different province is stronger evidence than a short-name
        # match, so keep the URL out of automatic promotion.
        score -= 20
        reasons.extend(f"reject:region_mismatch:{label}" for label in region_mismatches)

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.endswith(".go.kr") or ".go.kr" in host:
        score += 3
        reasons.append("domain:go.kr")
    if any(token in host for token in ("edu", "lll", "reserve", "yeyak", "total")):
        score += 2
        reasons.append("domain:reservation_like")

    allowed_public_org = any(keyword in text for keyword in ALLOWED_PUBLIC_ORG_KEYWORDS)
    if allowed_public_org:
        score += 3
        reasons.append("allow:public_org")

    if any(token in host for token in SCHOOL_HOST_TOKENS) and not allowed_public_org:
        score -= 12
        reasons.append("reject:school_host")

    rejected = [keyword for keyword in REJECT_KEYWORDS if keyword in text]
    if rejected:
        school_rejected = [
            keyword for keyword in rejected
            if keyword in {"초등학교", "중학교", "고등학교", "대학교", "방과후학교", "늘봄학교", "입학", "학사", "학부모"}
        ]
        other_rejected = [keyword for keyword in rejected if keyword not in school_rejected]
        score -= 10 * len(school_rejected)
        score -= 5 * len(other_rejected)
        reasons.extend(f"reject:{keyword}" for keyword in rejected)

    if region_conflict:
        # Even a keyword-dense result from another explicit province must never
        # become an automatic crawler target. Keep it visible for review only.
        score = min(score, 7)
    if score >= 8:
        return "candidate", score, reasons
    if score >= 3:
        return "review", score, reasons
    return "rejected", score, reasons


def bing_search(session: requests.Session, query: str, top: int, timeout: int) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?q=" + quote_plus(query)
    # Bing serves a JavaScript-only result shell to some full Chrome UA
    # strings. The stable basic HTML response is required by this parser.
    response = session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")

    results: list[dict[str, str]] = []
    for node in soup.select("li.b_algo"):
        link = node.select_one("h2 a") or node.select_one("a")
        if not link:
            continue
        title = clean(link.get_text(" ", strip=True))
        raw_url = clean(link.get("href"))
        if not title or not raw_url:
            continue
        snippet_node = node.select_one(".b_caption p") or node.select_one("p")
        snippet = clean(snippet_node.get_text(" ", strip=True) if snippet_node else node.get_text(" ", strip=True))
        results.append(
            {
                "title": title,
                "url": decode_bing_url(raw_url),
                "snippet": snippet,
            }
        )
        if len(results) >= top:
            break
    if not results:
        raise RuntimeError("Bing returned no parseable search results")
    return results


def decode_js_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return value.replace("\\/", "/").replace('\\"', '"')


def strip_markup(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    return clean(html.unescape(text))


def naver_search(session: requests.Session, query: str, top: int, timeout: int) -> list[dict[str, str]]:
    url = "https://search.naver.com/search.naver?where=web&query=" + quote_plus(query)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    text = response.text

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'"href":"(https?://[^"]+)".{0,2200}?"title":"(.*?)"', text):
        raw_url, raw_title = match.groups()
        result_url = decode_js_string(raw_url)
        if result_url in seen:
            continue
        seen.add(result_url)

        title = strip_markup(decode_js_string(raw_title))
        if not title or title in {"NAVER", "네이버"}:
            continue

        chunk = text[match.start() : match.end() + 1200]
        body_match = re.search(r'"bodyText":"(.*?)"', chunk)
        snippet = strip_markup(decode_js_string(body_match.group(1))) if body_match else ""
        results.append({"title": title, "url": result_url, "snippet": snippet})
        if len(results) >= top:
            break
    if not results:
        raise RuntimeError("Naver returned no parseable search results")
    return results


def unique_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for result in results:
        key = re.sub(r"#.*$", "", result["url"]).rstrip("/")
        if key not in merged or result.get("score", 0) > merged[key].get("score", 0):
            merged[key] = result
    return list(merged.values())


def reclassify_results(
    results: list[dict[str, Any]],
    municipality: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply the current scoring policy when old search files are merged."""

    reclassified: list[dict[str, Any]] = []
    for source in results:
        result = dict(source)
        status, score, reasons = classify_result(
            clean(str(result.get("title") or "")),
            clean(str(result.get("snippet") or "")),
            clean(str(result.get("url") or "")),
            municipality,
        )
        result.update({"status": status, "score": score, "reasons": reasons})
        reclassified.append(result)
    return reclassified


MUNICIPALITY_PREFIX_ALIASES = {
    "전라북도 ": "전북특별자치도 ",
    "전라남도 ": "전남광주통합특별시 ",
    "광주광역시 ": "전남광주통합특별시 ",
}


def municipality_identity(row: dict[str, Any]) -> str:
    full_name = clean(str(row.get("full_name") or ""))
    for old_prefix, current_prefix in MUNICIPALITY_PREFIX_ALIASES.items():
        if full_name.startswith(old_prefix):
            return current_prefix + full_name[len(old_prefix):]
    return full_name


def _query_key(value: Any) -> str:
    if isinstance(value, dict):
        return clean(str(value.get("query") or ""))
    return clean(str(value or ""))


def merge_search_results(
    queue_rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
    searched_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous_by_name = {municipality_identity(row): row for row in previous_rows}
    searched_by_name = {municipality_identity(row): row for row in searched_rows}
    merged: list[dict[str, Any]] = []

    for municipality in queue_rows:
        identity = municipality_identity(municipality)
        previous = previous_by_name.get(identity) or {}
        searched = searched_by_name.get(identity) or {}
        combined_results = reclassify_results(
            unique_results(
                [dict(row) for row in previous.get("results") or []]
                + [dict(row) for row in searched.get("results") or []]
            ),
            municipality,
        )
        queries_used: list[Any] = []
        seen_queries: set[str] = set()
        for value in list(previous.get("queries_used") or []) + list(searched.get("queries_used") or []):
            key = _query_key(value)
            if key and key not in seen_queries:
                seen_queries.add(key)
                queries_used.append(value)

        row = {
            **previous,
            **searched,
            "code": municipality["code"],
            "sido": municipality["sido"],
            "sigungu": municipality["sigungu"],
            "full_name": municipality["full_name"],
            "primary_query": municipality["primary_query"],
            "primary_category": municipality.get("primary_category", "integrated_reservation"),
            "google_search_url": municipality["google_search_url"],
            "queries_used": queries_used,
            "candidate_count": sum(1 for result in combined_results if result.get("status") == "candidate"),
            "review_count": sum(1 for result in combined_results if result.get("status") == "review"),
            "rejected_count": sum(1 for result in combined_results if result.get("status") == "rejected"),
            "errors": list(searched.get("errors") if searched else previous.get("errors") or []),
            "results": combined_results,
        }
        row["search_status"] = "searched" if queries_used else "pending"
        merged.append(row)
    return merged


def result_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "municipalities": len(rows),
        "searched": sum(1 for row in rows if row.get("queries_used")),
        "queries": sum(len(row.get("queries_used") or []) for row in rows),
        "candidate_results": sum(int(row.get("candidate_count") or 0) for row in rows),
        "review_results": sum(int(row.get("review_count") or 0) for row in rows),
        "rejected_results": sum(int(row.get("rejected_count") or 0) for row in rows),
        "errors": sum(1 for row in rows if row.get("errors")),
    }


def run(
    limit: int | None,
    queries_per_municipality: int,
    top: int,
    delay: float,
    timeout: int,
    backend: str,
    category_ids: set[str] | None = None,
    only_error_names: set[str] | None = None,
    municipality_names: set[str] | None = None,
) -> dict[str, Any]:
    data = yaml.safe_load(QUEUE.read_text(encoding="utf-8"))
    municipalities = data["municipalities"]
    if only_error_names is not None:
        current_error_names = {
            municipality_identity({"full_name": name}) for name in only_error_names
        }
        municipalities = [item for item in municipalities if municipality_identity(item) in current_error_names]
    if municipality_names:
        municipalities = [item for item in municipalities if item["full_name"] in municipality_names]
    municipalities = municipalities[: limit or None]
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "Chrome/125.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )

    summary = {
        "municipalities": len(municipalities),
        "searched": 0,
        "queries": 0,
        "candidate_results": 0,
        "review_results": 0,
        "rejected_results": 0,
        "errors": 0,
    }
    rows: list[dict[str, Any]] = []

    for index, municipality in enumerate(municipalities, start=1):
        query_entries = query_entries_for_municipality(municipality)
        if category_ids:
            query_entries = [entry for entry in query_entries if entry["category_id"] in category_ids]
        query_entries = query_entries[:queries_per_municipality]
        raw_results: list[dict[str, Any]] = []
        errors: list[str] = []

        for query_entry in query_entries:
            query = query_entry["query"]
            summary["queries"] += 1
            try:
                search_fn = naver_search if backend == "naver" else bing_search
                for result in search_fn(session, query, top=top, timeout=timeout):
                    status, score, reasons = classify_result(
                        result["title"],
                        result["snippet"],
                        result["url"],
                        municipality,
                    )
                    result.update(
                        {
                            "query": query,
                            "query_category_id": query_entry["category_id"],
                            "query_category_name": query_entry["category_name"],
                            "query_keyword": query_entry["keyword"],
                            "status": status,
                            "score": score,
                            "reasons": reasons,
                        }
                    )
                    raw_results.append(result)
            except Exception as exc:
                errors.append(f"{query}: {type(exc).__name__}: {exc}")
                summary["errors"] += 1
            if delay:
                time.sleep(delay)

        results = unique_results(raw_results)
        candidate_count = sum(1 for result in results if result["status"] == "candidate")
        review_count = sum(1 for result in results if result["status"] == "review")
        rejected_count = sum(1 for result in results if result["status"] == "rejected")
        summary["candidate_results"] += candidate_count
        summary["review_results"] += review_count
        summary["rejected_results"] += rejected_count
        summary["searched"] += 1

        rows.append(
            {
                "code": municipality["code"],
                "sido": municipality["sido"],
                "sigungu": municipality["sigungu"],
                "full_name": municipality["full_name"],
                "primary_query": municipality["primary_query"],
                "primary_category": municipality.get("primary_category", "municipality"),
                "google_search_url": municipality["google_search_url"],
                "search_backend": backend,
                "queries_used": query_entries,
                "candidate_count": candidate_count,
                "review_count": review_count,
                "rejected_count": rejected_count,
                "errors": errors,
                "results": results,
            }
        )
        print(
            f"[{index}/{len(municipalities)}] {municipality['full_name']} "
            f"candidate={candidate_count} review={review_count} rejected={rejected_count} errors={len(errors)}"
        )

    return {
        "version": 1,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source_queue": str(QUEUE.relative_to(ROOT)).replace("\\", "/"),
        "summary": summary,
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run municipal course search queue and save YAML results")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--queries-per-municipality", type=int, default=1)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--backend", choices=["naver", "bing"], default="naver")
    parser.add_argument("--category-id", action="append", help="Only run queries in this category id. Repeatable.")
    parser.add_argument("--municipality", action="append", help="Only search this exact full_name. Repeatable.")
    parser.add_argument("--merge-from", help="Merge searched rows into this prior result file using the current queue order.")
    parser.add_argument("--retry-errors-from")
    parser.add_argument("--retry-empty-from", help="Retry municipalities whose prior result has no parsed URLs.")
    parser.add_argument("--import-results", help="Use an existing search result as the new rows when merging; do not search.")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out

    previous = None
    only_error_names = None
    previous_path_value = args.retry_errors_from or args.retry_empty_from or args.merge_from
    if previous_path_value:
        previous_path = Path(previous_path_value)
        if not previous_path.is_absolute():
            previous_path = ROOT / previous_path
        previous = yaml.safe_load(previous_path.read_text(encoding="utf-8"))
    if args.retry_errors_from and previous is not None:
        only_error_names = {row["full_name"] for row in previous.get("results", []) if row.get("errors")}
    if args.retry_empty_from and previous is not None:
        only_error_names = {row["full_name"] for row in previous.get("results", []) if not row.get("results")}

    if args.import_results:
        import_path = Path(args.import_results)
        if not import_path.is_absolute():
            import_path = ROOT / import_path
        data = yaml.safe_load(import_path.read_text(encoding="utf-8"))
    else:
        data = run(
            args.limit,
            args.queries_per_municipality,
            args.top,
            args.delay,
            args.timeout,
            args.backend,
            set(args.category_id or []) or None,
            only_error_names,
            set(args.municipality or []) or None,
        )

    if previous is not None:
        queue_data = yaml.safe_load(QUEUE.read_text(encoding="utf-8"))
        merged = merge_search_results(
            queue_data.get("municipalities") or [],
            previous.get("results") or [],
            data.get("results") or [],
        )
        previous["updated_at"] = data["updated_at"]
        previous["summary"] = result_summary(merged)
        previous["results"] = merged
        data = previous

    yaml_dump(data, out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
