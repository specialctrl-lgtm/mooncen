from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from DB.db_utils import get_db_cursor


AGE_TOKEN_RE = re.compile(r"(?:\d{1,3}\s*개월|\d{4}\s*~\s*\d{2,4}\s*년생|\d{2}\s*~\s*\d{2}\s*년생|\d{1,2}\s*세)")
SCHEDULE_TOKEN_RE = re.compile(r"(?:[월화수목금토일]\s*[/:|]\s*|\d{1,2}:\d{2}|\d{1,2}\s*/\s*\d{1,2})")
BAD_TITLE_RE = re.compile(r"^\s*(?:상단 메뉴|검색|부산광역시 통합예약 시스템|수강일시)\s*$")
EXPLICIT_AGE_DETAIL_RE = re.compile(
    r"(\d{1,3}\s*\uac1c\uc6d4|\d{1,2}\s*\uc138|\ub9cc\s*\d{1,2}|"
    r"\d{2,4}\s*(?:[~-]\s*\d{2,4})?\s*\ub144\uc0dd|\ucd08\ub4f1\s*\d+\s*\ud559\ub144)"
)


def pct(numerator: int, denominator: int) -> float:
    return round((numerator / denominator * 100), 1) if denominator else 0.0


def parse_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            return [item.strip() for item in value.split(",") if item.strip()]
    return []


def title_result_source(row: dict[str, Any]) -> str:
    value = row.get("ai_title_result")
    if isinstance(value, dict):
        return str(value.get("source") or "")
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return ""
        if isinstance(parsed, dict):
            return str(parsed.get("source") or "")
    return ""


def has_explicit_age_detail(row: dict[str, Any]) -> bool:
    source = " ".join(str(row.get(key) or "") for key in ("title_raw", "title", "target"))
    return bool(EXPLICIT_AGE_DETAIL_RE.search(source))


def fetch_rows(providers: list[str] | None, active_only: bool, limit_samples: int) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    params: dict[str, Any] = {}
    if providers:
        where.append("provider = ANY(%(providers)s)")
        params["providers"] = [provider.upper() for provider in providers]
    if active_only:
        where.append("COALESCE(is_active, TRUE) IS TRUE")

    with get_db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, provider, title, title_raw, target, target_age_group,
                   target_min_age, target_max_age, description, category_raw,
                   ai_category, ai_tags, ai_summary,
                   COALESCE(is_ai_processed, FALSE) AS is_ai_processed,
                   COALESCE(ai_title_processed, FALSE) AS ai_title_processed,
                   ai_title_confidence, ai_title_result, updated_at
            FROM courses
            WHERE {" AND ".join(where)}
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            """,
            params,
        )
        rows = [dict(row) for row in cursor.fetchall()]

    return rows[:limit_samples] if limit_samples > 0 else rows


def quality_report(rows: list[dict[str, Any]], sample_limit: int) -> dict[str, Any]:
    total = len(rows)
    summary_processed = sum(1 for row in rows if row.get("is_ai_processed"))
    title_processed = sum(1 for row in rows if row.get("ai_title_processed"))
    title_unchanged = sum(1 for row in rows if title_result_source(row) == "title_unchanged")
    with_summary = sum(1 for row in rows if row.get("ai_summary") and str(row.get("ai_summary")).strip())
    with_tags = sum(1 for row in rows if parse_tags(row.get("ai_tags")))
    with_ai_category = sum(1 for row in rows if row.get("ai_category"))
    with_age_group = sum(1 for row in rows if row.get("target_age_group"))
    with_age_bounds = sum(1 for row in rows if row.get("target_min_age") is not None or row.get("target_max_age") is not None)
    with_target = sum(1 for row in rows if row.get("target") and str(row.get("target")).strip())

    title_ai_rows = [row for row in rows if row.get("ai_title_processed")]
    low_confidence = [
        row for row in title_ai_rows
        if title_result_source(row) not in {"rule_fallback", "rule_fast_path", "title_unchanged"}
        and row.get("ai_title_confidence") is not None
        and float(row.get("ai_title_confidence") or 0) < 0.65
    ]
    remaining_age_title = [row for row in title_ai_rows if AGE_TOKEN_RE.search(str(row.get("title") or ""))]
    remaining_schedule_title = [row for row in title_ai_rows if SCHEDULE_TOKEN_RE.search(str(row.get("title") or ""))]
    bad_titles = [row for row in rows if BAD_TITLE_RE.search(str(row.get("title") or ""))]
    explicit_age_missing = [
        row for row in rows
        if AGE_TOKEN_RE.search(" ".join(str(row.get(key) or "") for key in ("title_raw", "title", "target")))
        and not row.get("target_age_group")
    ]
    adult_with_months = [
        row for row in rows
        if row.get("target_age_group") == "ADULT"
        and (row.get("target_min_age") is not None or row.get("target_max_age") is not None)
        and not has_explicit_age_detail(row)
    ]
    summary_too_short = [
        row for row in rows
        if row.get("is_ai_processed")
        and len(str(row.get("ai_summary") or "").strip()) < 12
    ]
    tags_empty_processed = [
        row for row in rows
        if row.get("is_ai_processed") and not parse_tags(row.get("ai_tags"))
    ]

    def sample(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "provider": row.get("provider"),
                "title": row.get("title"),
                "title_raw": row.get("title_raw"),
                "target": row.get("target"),
                "age_group": row.get("target_age_group"),
                "min_age": row.get("target_min_age"),
                "max_age": row.get("target_max_age"),
                "confidence": row.get("ai_title_confidence"),
                "summary": row.get("ai_summary"),
            }
            for row in items[:sample_limit]
        ]

    metrics = [
        ("summary_processed", summary_processed, total, "AI 요약/태그 처리 완료"),
        ("title_processed", title_processed, total, "AI 제목/연령 처리 완료"),
        ("title_unchanged", title_unchanged, total, "제목 동일로 AI 제목 미적용"),
        ("with_summary", with_summary, summary_processed, "처리 완료 중 요약 있음"),
        ("with_tags", with_tags, summary_processed, "처리 완료 중 태그 있음"),
        ("with_ai_category", with_ai_category, summary_processed, "처리 완료 중 AI 카테고리 있음"),
        ("with_target", with_target, total, "대상 텍스트 있음"),
        ("with_age_group", with_age_group, total, "연령 그룹 있음"),
        ("with_age_bounds", with_age_bounds, total, "개월 경계값 있음"),
    ]
    issues = [
        ("low_confidence", len(low_confidence), title_processed, "AI title confidence < 0.65"),
        ("remaining_age_in_title", len(remaining_age_title), title_processed, "정리 후 제목에 개월/년생/세 잔존"),
        ("remaining_schedule_in_title", len(remaining_schedule_title), title_processed, "정리 후 제목에 요일/시간/날짜 잔존"),
        ("bad_title", len(bad_titles), total, "메뉴/검색/시스템명 같은 비정상 제목"),
        ("explicit_age_missing", len(explicit_age_missing), total, "제목/대상에 연령 표현이 있는데 age_group 없음"),
        ("adult_with_months", len(adult_with_months), total, "명시 나이 없는 성인인데 min/max 개월값 있음"),
        ("summary_too_short", len(summary_too_short), summary_processed, "요약이 너무 짧거나 비어 있음"),
        ("tags_empty_processed", len(tags_empty_processed), summary_processed, "처리 완료인데 태그 없음"),
    ]

    return {
        "total": total,
        "metrics": [
            {"key": key, "count": count, "base": base, "rate": pct(count, base), "label": label}
            for key, count, base, label in metrics
        ],
        "issues": [
            {"key": key, "count": count, "base": base, "rate": pct(count, base), "label": label}
            for key, count, base, label in issues
        ],
        "samples": {
            "low_confidence": sample(low_confidence),
            "remaining_age_in_title": sample(remaining_age_title),
            "remaining_schedule_in_title": sample(remaining_schedule_title),
            "bad_title": sample(bad_titles),
            "explicit_age_missing": sample(explicit_age_missing),
            "adult_with_months": sample(adult_with_months),
            "summary_too_short": sample(summary_too_short),
            "tags_empty_processed": sample(tags_empty_processed),
        },
    }


def print_markdown(report: dict[str, Any]) -> None:
    print("# AI Quality Report\n")
    print(f"Total rows: {report['total']}\n")
    print("## Coverage\n")
    print("| Metric | Count | Base | Rate |")
    print("|---|---:|---:|---:|")
    for row in report["metrics"]:
        print(f"| {row['label']} | {row['count']} | {row['base']} | {row['rate']}% |")
    print("\n## Quality Issues\n")
    print("| Issue | Count | Base | Rate |")
    print("|---|---:|---:|---:|")
    for row in report["issues"]:
        print(f"| {row['label']} | {row['count']} | {row['base']} | {row['rate']}% |")
    print("\n## Samples\n")
    for key, items in report["samples"].items():
        if not items:
            continue
        print(f"\n### {key}\n")
        for item in items:
            print(
                "- "
                f"[{item.get('provider')}] {item.get('title')} "
                f"| target={item.get('target')} "
                f"| age={item.get('age_group')} {item.get('min_age')}~{item.get('max_age')} "
                f"| confidence={item.get('confidence')}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Report AI processing quality for MoonCen courses.")
    parser.add_argument("--provider", action="append", help="Limit to provider. Can be repeated.")
    parser.add_argument("--active-only", action="store_true", help="Only include active courses.")
    parser.add_argument("--sample", type=int, default=10, help="Sample rows per issue.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of markdown.")
    args = parser.parse_args()

    rows = fetch_rows(args.provider, args.active_only, 0)
    report = quality_report(rows, args.sample)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_markdown(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
