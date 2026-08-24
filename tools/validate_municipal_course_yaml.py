from __future__ import annotations

from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / "config" / "municipal_course_search_targets.yaml"
RESULTS = ROOT / "config" / "municipal_course_search_results.yaml"
CANDIDATES = ROOT / "config" / "municipal_course_candidate_results.yaml"


LOW_VALUE_DOMAINS = {
    "blog.naver.com",
    "m.blog.naver.com",
    "namu.wiki",
    "ko.wikipedia.org",
    "www.newsro.kr",
    "www.asiatoday.co.kr",
    "www.boeuni.com",
    "www.brcity.kr",
    "www.cctimes.kr",
    "www.cfnews.kr",
    "www.domin.co.kr",
    "www.ggilbo.com",
    "www.gndomin.com",
    "www.gukjenews.com",
    "www.hyundaiilbo.com",
    "www.idaegu.co.kr",
    "www.igangbuk.com",
    "www.igimpo.com",
    "www.imedialife.co.kr",
    "www.jeollailbo.com",
    "www.jjn.co.kr",
    "www.jnilbo.com",
    "www.jntoday.co.kr",
    "www.joongdo.co.kr",
    "www.kbsm.net",
    "www.kjilbo.co.kr",
    "www.kmaeil.com",
    "www.kwtotalnews.kr",
    "www.kyongbuk.co.kr",
    "www.mygoyang.com",
    "www.newsfire.co.kr",
    "www.pointe.co.kr",
    "www.seoulilbo.com",
    "www.todayan.com",
    "www.yangsanilbo.com",
    "www.yg21.co.kr",
    "www.yongin21.co.kr",
}


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def assert_required(row: dict, required: list[str], label: str, errors: list[str]) -> None:
    for key in required:
        if row.get(key) in (None, "", []):
            errors.append(f"{label}: missing {key}")


def validate() -> tuple[list[str], list[str], dict]:
    errors: list[str] = []
    warnings: list[str] = []

    targets = load_yaml(TARGETS)
    results = load_yaml(RESULTS)
    candidates = load_yaml(CANDIDATES)

    municipalities = targets.get("municipalities", [])
    result_rows = results.get("results", [])
    candidate_rows = candidates.get("results", [])

    summary = {
        "target_municipalities": len(municipalities),
        "result_rows": len(result_rows),
        "candidate_rows": len(candidate_rows),
        "candidate_results": candidates.get("summary", {}).get("candidate_results"),
        "review_results": candidates.get("summary", {}).get("review_results"),
    }

    declared_total = int(targets.get("totals", {}).get("municipalities") or 0)
    if declared_total != len(municipalities):
        errors.append(f"targets: declared total={declared_total} actual={len(municipalities)}")
    if len(result_rows) != len(municipalities):
        errors.append(f"results: row count mismatch targets={len(municipalities)} results={len(result_rows)}")
    if len(candidate_rows) != len(municipalities):
        errors.append(f"candidates: row count mismatch targets={len(municipalities)} candidates={len(candidate_rows)}")

    target_codes = [row.get("code") for row in municipalities]
    duplicate_codes = [code for code, count in Counter(target_codes).items() if count > 1]
    if duplicate_codes:
        errors.append(f"targets: duplicate codes {duplicate_codes[:10]}")

    target_names = [row.get("full_name") for row in municipalities]
    duplicate_names = [name for name, count in Counter(target_names).items() if count > 1]
    if duplicate_names:
        errors.append(f"targets: duplicate full_name {duplicate_names[:10]}")

    for row in municipalities:
        label = f"targets:{row.get('full_name')}"
        assert_required(
            row,
            [
                "code",
                "sido",
                "sigungu",
                "full_name",
                "municipality_type",
                "primary_category",
                "primary_query",
                "google_search_url",
                "alternate_queries",
                "categorized_queries",
            ],
            label,
            errors,
        )
        if row.get("municipality_type") not in {"city", "district", "county"}:
            errors.append(f"{label}: invalid municipality_type={row.get('municipality_type')}")
        if row.get("sigungu") != "세종특별자치시" and not str(row.get("sigungu", "")).endswith(("시", "구", "군")):
            errors.append(f"{label}: sigungu is not 시/구/군")
        if "통합예약" not in str(row.get("primary_query", "")):
            errors.append(f"{label}: primary_query does not contain 통합예약")
        if len(row.get("alternate_queries", [])) < 7:
            warnings.append(f"{label}: alternate_queries shorter than expected")
        categorized = row.get("categorized_queries", [])
        if len(categorized) < 20:
            warnings.append(f"{label}: categorized_queries shorter than expected")
        for query in categorized:
            if query.get("category_id") not in {
                "integrated_reservation",
                "municipality",
                "learning_center",
                "education_center",
                "welfare_center",
                "library",
                "art_museum",
                "arboretum",
                "museum",
                "sports_center",
                "facility_corporation",
                "course_application",
                "sports_dong",
            }:
                errors.append(f"{label}: invalid query category={query.get('category_id')}")

    result_by_name = {row.get("full_name"): row for row in result_rows}
    candidate_by_name = {row.get("full_name"): row for row in candidate_rows}
    for name in target_names:
        if name not in result_by_name:
            errors.append(f"results: missing municipality {name}")
        if name not in candidate_by_name:
            errors.append(f"candidates: missing municipality {name}")

    result_errors = [row["full_name"] for row in result_rows if row.get("errors")]
    if result_errors:
        errors.append(f"results: {len(result_errors)} rows still have errors; first={result_errors[:5]}")

    no_candidate = [row["full_name"] for row in candidate_rows if row.get("candidate_count", 0) == 0]
    summary["municipalities_no_candidate"] = len(no_candidate)
    if no_candidate:
        warnings.append(f"candidates: {len(no_candidate)} municipalities have no candidate results")

    url_counter: Counter[str] = Counter()
    domain_counter: Counter[str] = Counter()
    low_value_counter: Counter[str] = Counter()
    invalid_urls: list[str] = []
    rows_with_count_mismatch: list[str] = []

    for row in candidate_rows:
        items = row.get("candidates", [])
        candidate_count = sum(1 for item in items if item.get("status") == "candidate")
        review_count = sum(1 for item in items if item.get("status") == "review")
        if candidate_count != row.get("candidate_count") or review_count != row.get("review_count"):
            rows_with_count_mismatch.append(row.get("full_name"))

        for item in items:
            url = item.get("url", "")
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                invalid_urls.append(f"{row.get('full_name')}: {url}")
                continue
            url_counter[url.rstrip("/")] += 1
            domain = parsed.netloc.lower()
            domain_counter[domain] += 1
            if domain in LOW_VALUE_DOMAINS or domain.endswith(".naver.com"):
                low_value_counter[domain] += 1

    if rows_with_count_mismatch:
        errors.append(f"candidates: count mismatch rows {rows_with_count_mismatch[:10]}")
    if invalid_urls:
        errors.append(f"candidates: invalid urls {invalid_urls[:10]}")

    duplicate_urls = [(url, count) for url, count in url_counter.items() if count > 1]
    summary["duplicate_candidate_urls"] = len(duplicate_urls)
    summary["low_value_candidate_urls"] = sum(low_value_counter.values())
    summary["top_domains"] = domain_counter.most_common(20)
    summary["low_value_domains"] = low_value_counter.most_common(20)
    summary["no_candidate_examples"] = no_candidate[:20]

    if duplicate_urls:
        warnings.append(f"candidates: {len(duplicate_urls)} URLs appear in multiple municipalities")
    if low_value_counter:
        warnings.append(f"candidates: {sum(low_value_counter.values())} low-value/blog/naver URLs need filtering")

    return errors, warnings, summary


def main() -> int:
    errors, warnings, summary = validate()
    print("== Summary ==")
    for key, value in summary.items():
        print(f"{key}: {value}")
    print("\n== Errors ==")
    if errors:
        for error in errors:
            print(f"- {error}")
    else:
        print("none")
    print("\n== Warnings ==")
    if warnings:
        for warning in warnings:
            print(f"- {warning}")
    else:
        print("none")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
