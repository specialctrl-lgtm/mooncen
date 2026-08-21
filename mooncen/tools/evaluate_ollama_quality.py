from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from DB.db_utils import get_db_cursor
from ai_processor import (
    AI_CATEGORIES,
    AIProcessor,
    _explicit_age_month_range,
    _has_operational_summary_noise,
    _looks_like_age_target,
)


REPORT_DIR = ROOT / "logs" / "ai_quality_reports"
AGE_KEYWORD_RE = re.compile(
    r"(\uac1c\uc6d4|\ub144\uc0dd|\ub9cc\s*\d|\d+\s*\uc138|"
    r"\ucd08\ub4f1|\uc911\ub4f1|\uc911\ud559|\uace0\ub4f1|"
    r"\uc601\uc544|\uc720\uc544|\uc544\ub3d9|\uc5b4\ub9b0\uc774|\uc131\uc778|\uc2dc\ub2c8\uc5b4)"
)
GENERIC_TAGS = {
    "\ubb38\ud654\uc13c\ud130",
    "\uac15\uc88c",
    "\uc218\uc5c5",
    "\ud504\ub85c\uadf8\ub7a8",
    "\uc774\ubca4\ud2b8",
}


def hangul_ratio(value: object) -> float:
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return 0.0
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    hangul = [char for char in letters if "\uac00" <= char <= "\ud7a3"]
    return len(hangul) / len(letters)


def normalize_hosts(raw_hosts: str) -> list[str]:
    hosts = [item.strip() for item in raw_hosts.replace(";", ",").split(",") if item.strip()]
    if not hosts:
        hosts = [os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_HOST") or "http://wtr-linux:11434"]
    result: list[str] = []
    seen: set[str] = set()
    for host in hosts:
        value = host.rstrip("/")
        if not value.startswith(("http://", "https://")):
            value = f"http://{value}"
        if value.lower() not in seen:
            seen.add(value.lower())
            result.append(value)
    return result


def fetch_samples(limit: int, pool_size: int) -> list[dict[str, Any]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id::text, provider, title, title_raw, target, target_age_group,
                   target_min_age, target_max_age, target_with_parent, target_tags,
                   description, category_raw, schedule_raw
            FROM courses
            WHERE COALESCE(is_active, TRUE) = TRUE
              AND NULLIF(btrim(title), '') IS NOT NULL
            ORDER BY random()
            LIMIT %s
            """,
            (pool_size,),
        )
        rows = list(cursor.fetchall())

    def priority(row: dict[str, Any]) -> tuple[int, int]:
        text = " ".join(str(row.get(key) or "") for key in ("title_raw", "title", "target", "description"))
        has_age = bool(AGE_KEYWORD_RE.search(text))
        has_description = len(str(row.get("description") or "").strip()) >= 40
        return (1 if has_age else 0, 1 if has_description else 0)

    rows.sort(key=priority, reverse=True)
    return rows[:limit]


def expected_age(row: dict[str, Any]) -> tuple[int | None, int | None, str]:
    for key in ("target", "title_raw", "title"):
        value = row.get(key)
        min_age, max_age = _explicit_age_month_range(value)
        if min_age is not None or max_age is not None:
            return min_age, max_age, key
    return None, None, ""


def score_title(row: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {"ok": False, "score": 0.0, "reason": "no_result"}

    title = str(result.get("title") or "").strip()
    target = result.get("target")
    min_age = result.get("target_min_age")
    max_age = result.get("target_max_age")
    expected_min, expected_max, expected_source = expected_age(row)
    expected_has_age = expected_min is not None or expected_max is not None

    checks: dict[str, bool | None] = {
        "title_present": len(title) >= 2,
        "title_age_removed": not bool(AGE_KEYWORD_RE.search(title)),
        "range_valid": min_age is None or max_age is None or int(min_age) <= int(max_age),
        "age_exact": None,
    }
    if target:
        checks["target_looks_age"] = _looks_like_age_target(target)
    elif expected_has_age:
        checks["target_looks_age"] = False
    else:
        checks["target_looks_age"] = None
    if expected_has_age:
        checks["age_exact"] = min_age == expected_min and max_age == expected_max

    applicable = [value for value in checks.values() if value is not None]
    score = sum(1 for value in applicable if value) / max(1, len(applicable))
    return {
        "ok": score >= 0.8,
        "score": round(score, 3),
        "checks": checks,
        "expected_age": {"min": expected_min, "max": expected_max, "source": expected_source},
        "result": {
            "title": title,
            "target": target,
            "min": min_age,
            "max": max_age,
            "group": result.get("target_age_group"),
            "confidence": result.get("ai_title_confidence"),
            "source": (result.get("ai_title_result") or {}).get("source"),
        },
    }


def has_ascii(value: str) -> bool:
    return any("a" <= char.lower() <= "z" for char in value)


def tag_is_displayable_korean(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and not has_ascii(text) and hangul_ratio(text) >= 0.35 and text not in GENERIC_TAGS


def score_summary(row: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {"ok": False, "score": 0.0, "reason": "no_result"}

    summary = str(result.get("summary") or "").strip()
    category = result.get("category")
    tags = result.get("tags") or []
    if isinstance(tags, str):
        tags = [item.strip() for item in tags.split(",") if item.strip()]
    source = f"{row.get('title') or ''} {row.get('description') or ''}"

    checks = {
        "summary_present": 2 <= len(summary) <= 35,
        "summary_displayable_korean": hangul_ratio(summary) >= 0.35,
        "summary_no_noise": not _has_operational_summary_noise(summary),
        "summary_not_schedule": not bool(re.search(r"\d{1,2}:\d{2}|\d{1,2}[./]\d{1,2}", summary)),
        "category_valid": category in AI_CATEGORIES,
        "tags_count": isinstance(tags, list) and len([tag for tag in tags if str(tag).strip()]) == 3,
        "tags_korean": isinstance(tags, list) and all(tag_is_displayable_korean(tag) for tag in tags[:3]),
        "not_hallucinated_empty_source": bool(source.strip()) or bool(summary),
    }
    score = sum(1 for value in checks.values() if value) / len(checks)
    return {
        "ok": score >= 0.85,
        "score": round(score, 3),
        "checks": checks,
        "result": {
            "summary": summary,
            "category": category,
            "tags": tags[:3] if isinstance(tags, list) else tags,
            "confidence": result.get("confidence"),
        },
    }


def metric_row(processor: AIProcessor, task: str) -> dict[str, Any]:
    metrics = dict(processor.last_call_metrics or {})
    metrics["task"] = task
    eval_count = int(metrics.get("eval_count") or 0)
    eval_duration = int(metrics.get("eval_duration_ns") or 0)
    if eval_count and eval_duration:
        metrics["output_tokens_per_second"] = round(eval_count / (eval_duration / 1_000_000_000), 2)
    return metrics


def evaluate_host(host: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    processor = AIProcessor(ollama_url=host)
    title_scores = []
    summary_scores = []
    metrics = []
    examples = []

    for row in samples:
        started = time.time()
        processor.last_call_metrics = {}
        title_result = processor.analyze_title(row)
        metrics.append(metric_row(processor, "title"))
        title_score = score_title(row, title_result)
        title_scores.append(title_score)

        processor.last_call_metrics = {}
        summary_result = processor.analyze_course(
            str((title_result or {}).get("title") or row.get("title") or ""),
            str(row.get("description") or ""),
            str(row.get("category_raw") or ""),
        )
        metrics.append(metric_row(processor, "summary"))
        summary_score = score_summary(row, summary_result)
        summary_scores.append(summary_score)

        examples.append(
            {
                "id": row["id"],
                "provider": row.get("provider"),
                "source_title": row.get("title_raw") or row.get("title"),
                "source_target": row.get("target"),
                "title_eval": title_score,
                "summary_eval": summary_score,
                "elapsed_seconds": round(time.time() - started, 3),
            }
        )

    throughput_values = [
        float(metric.get("output_tokens_per_second"))
        for metric in metrics
        if metric.get("output_tokens_per_second") is not None
    ]
    title_values = [item["score"] for item in title_scores]
    summary_values = [item["score"] for item in summary_scores]
    total_values = [(title_values[i] + summary_values[i]) / 2 for i in range(len(samples))]
    return {
        "host": host,
        "model": processor.ollama_model,
        "sample_count": len(samples),
        "title_avg": round(statistics.mean(title_values), 3) if title_values else 0.0,
        "summary_avg": round(statistics.mean(summary_values), 3) if summary_values else 0.0,
        "overall_avg": round(statistics.mean(total_values), 3) if total_values else 0.0,
        "title_pass": sum(1 for item in title_scores if item["ok"]),
        "summary_pass": sum(1 for item in summary_scores if item["ok"]),
        "throughput_tps_avg": round(statistics.mean(throughput_values), 2) if throughput_values else None,
        "throughput_tps_p50": round(statistics.median(throughput_values), 2) if throughput_values else None,
        "examples": examples,
        "metrics": metrics,
    }


def print_summary(report: dict[str, Any]) -> None:
    print(f"report={report['report_path']}")
    print(f"samples={report['sample_count']} generated_at={report['generated_at']}")
    print()
    print("| host | model | overall | title | summary | title pass | summary pass | avg tok/s | p50 tok/s |")
    print("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for host in report["hosts"]:
        print(
            f"| {host['host']} | {host['model']} | {host['overall_avg']} | {host['title_avg']} | "
            f"{host['summary_avg']} | {host['title_pass']}/{host['sample_count']} | "
            f"{host['summary_pass']}/{host['sample_count']} | {host['throughput_tps_avg']} | "
            f"{host['throughput_tps_p50']} |"
        )
    print()
    for host in report["hosts"]:
        weak = [
            item
            for item in host["examples"]
            if item["title_eval"]["score"] < 0.8 or item["summary_eval"]["score"] < 0.85
        ][:5]
        if not weak:
            continue
        print(f"weak_examples host={host['host']}")
        for item in weak:
            print(
                f"- {item['source_title']} | title={item['title_eval']['score']} "
                f"summary={item['summary_eval']['score']} title_result={item['title_eval'].get('result')} "
                f"summary_result={item['summary_eval'].get('result')}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Ollama AI processing quality without updating DB.")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--pool-size", type=int, default=240)
    parser.add_argument("--hosts", default=os.getenv("OLLAMA_HOSTS", ""))
    args = parser.parse_args()

    samples = fetch_samples(max(1, args.limit), max(args.pool_size, args.limit))
    hosts = normalize_hosts(args.hosts)
    host_reports: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(len(hosts), 4))) as executor:
        future_map = {executor.submit(evaluate_host, host, samples): host for host in hosts}
        for future in as_completed(future_map):
            host_reports.append(future.result())
    host_reports.sort(key=lambda item: hosts.index(item["host"]) if item["host"] in hosts else 999)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sample_count": len(samples),
        "sample_ids": [row["id"] for row in samples],
        "hosts": host_reports,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"ollama_quality_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
