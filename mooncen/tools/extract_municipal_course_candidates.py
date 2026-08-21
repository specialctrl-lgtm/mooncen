from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config" / "municipal_course_search_results.yaml"
OUT = ROOT / "config" / "municipal_course_candidate_results.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract candidate URLs from municipal course search results.")
    parser.add_argument("--source", default=str(SOURCE))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_absolute():
        source = ROOT / source
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out

    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    rows = []
    domain_counter: Counter[str] = Counter()

    for row in data["results"]:
        candidates = [
            {
                "status": result["status"],
                "score": result["score"],
                "title": result["title"],
                "url": result["url"],
                "snippet": result.get("snippet", ""),
                "query": result.get("query", ""),
                "query_category_id": result.get("query_category_id", row.get("primary_category", "municipality")),
                "query_category_name": result.get("query_category_name", ""),
                "query_keyword": result.get("query_keyword", ""),
                "reasons": result.get("reasons", []),
            }
            for result in row.get("results", [])
            if result.get("status") in {"candidate", "review"}
        ]
        for result in candidates:
            domain_counter[urlparse(result["url"]).netloc.lower()] += 1
        rows.append(
            {
                "code": row["code"],
                "sido": row["sido"],
                "sigungu": row["sigungu"],
                "full_name": row["full_name"],
                "primary_query": row["primary_query"],
                "primary_category": row.get("primary_category", "municipality"),
                "google_search_url": row["google_search_url"],
                "candidate_count": sum(1 for result in candidates if result["status"] == "candidate"),
                "review_count": sum(1 for result in candidates if result["status"] == "review"),
                "candidates": candidates,
            }
        )

    output = {
        "version": 1,
        "source": str(source.relative_to(ROOT)).replace("\\", "/") if source.is_relative_to(ROOT) else str(source),
        "summary": {
            "municipalities": len(rows),
            "municipalities_with_candidate": sum(1 for row in rows if row["candidate_count"] > 0),
            "municipalities_no_candidate": sum(1 for row in rows if row["candidate_count"] == 0),
            "candidate_results": sum(row["candidate_count"] for row in rows),
            "review_results": sum(row["review_count"] for row in rows),
            "top_domains": [
                {"domain": domain, "count": count}
                for domain, count in domain_counter.most_common(50)
            ],
        },
        "results": rows,
    }
    out.write_text(yaml.safe_dump(output, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
