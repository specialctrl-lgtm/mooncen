from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from DB.db_utils import get_db_cursor
from ai_processor import clean_course_description


FORBIDDEN_WORDS = ("환불", "접수", "결제", "전화", "데스크", "지점", "가격", "수강료", "재료비", "준비물", "주차", "폐강")
GENERIC_TAGS = {"문화센터", "강좌", "수업", "프로그램", "이벤트"}


@dataclass(frozen=True)
class PromptVariant:
    name: str
    max_chars: int
    prompt: str


VARIANTS = [
    PromptVariant(
        name="strict_35_activity",
        max_chars=35,
        prompt=(
            "You prepare Korean culture-center course search snippets. "
            "Return exactly one JSON object. No markdown. No thinking text. "
            "Use only facts in TITLE and DESCRIPTION.\n\n"
            "Rules:\n"
            "- summary: Korean, 35 characters or fewer, no period.\n"
            "- summary must describe the activity, not registration rules.\n"
            "- Exclude date, day, time, price, branch, phone, refund, registration, material-fee, parking.\n"
            "- tags: exactly 3 Korean noun phrases.\n"
            "- Do not use English tags.\n"
            "- Do not use generic tags such as 문화센터, 강좌, 수업, 프로그램, 이벤트.\n"
            "- confidence: use 0.7 when the description is useful, 0.4 when it is weak.\n\n"
            'Schema: {{"summary":"...","tags":["...","...","..."],"confidence":0.7}}\n\n'
            "TITLE: {title}\nDESCRIPTION: {description}\nCATEGORY: {category}\n"
        ),
    ),
    PromptVariant(
        name="very_short_25",
        max_chars=25,
        prompt=(
            "Return JSON only. Create Korean search text for a culture-center course.\n"
            "summary must be <=25 Korean characters and mention only the main activity. "
            "No dates, time, fees, branch, refund, registration, material, parking. "
            "tags must be exactly 3 short Korean nouns. No English.\n"
            'Schema: {{"summary":"...","tags":["...","...","..."],"confidence":0.7}}\n'
            "TITLE: {title}\nDESCRIPTION: {description}\nCATEGORY: {category}\n"
        ),
    ),
    PromptVariant(
        name="title_first_35",
        max_chars=35,
        prompt=(
            "Return one JSON object only. You write Korean course card snippets.\n"
            "First use TITLE to identify the activity, then use DESCRIPTION only to clarify it. "
            "Do not summarize policies or notices. "
            "summary <=35 Korean characters. tags exactly 3 Korean noun phrases. "
            "No English. No dates/time/fees/refund/registration/branch/materials.\n"
            'Schema: {{"summary":"...","tags":["...","...","..."],"confidence":0.7}}\n'
            "TITLE: {title}\nDESCRIPTION: {description}\nCATEGORY: {category}\n"
        ),
    ),
]


def has_ascii_alpha(value: str) -> bool:
    return any("a" <= char.lower() <= "z" for char in value)


def extract_json(value: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", value.strip(), re.S)
    if not match:
        raise ValueError("no-json")
    return json.loads(match.group(0))


def fetch_rows(limit: int) -> list[dict[str, Any]]:
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            WITH classified AS (
                SELECT
                    c.id,
                    c.provider,
                    c.title,
                    c.description,
                    c.category_raw,
                    c.target,
                    CASE
                      WHEN btrim(c.description) = btrim(c.title) THEN 'same_title'
                      WHEN length(btrim(c.description)) <= 20 THEN 'short'
                      WHEN c.description ~ '^\\[(Adult|Kids|Baby|Child|Infant)\\]' THEN 'open_info'
                      WHEN c.description ILIKE '강좌소개 %%' OR c.description ILIKE '문화행사%%' THEN 'template'
                      ELSE 'usable'
                    END AS quality
                FROM courses c
                WHERE c.is_active IS TRUE
                  AND c.description IS NOT NULL
                  AND btrim(c.description) <> ''
            ),
            sampled AS (
                (SELECT * FROM classified WHERE quality <> 'usable' ORDER BY random() LIMIT GREATEST(1, %s / 4))
                UNION ALL
                (SELECT * FROM classified WHERE quality = 'usable' ORDER BY random() LIMIT %s)
            )
            SELECT * FROM sampled LIMIT %s
            """,
            (limit, limit, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def call_ollama(url: str, model: str, prompt: str) -> tuple[dict[str, Any] | None, str, float]:
    started = time.time()
    response = requests.post(
        f"{url.rstrip('/')}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0, "top_p": 0.7, "num_predict": 120},
        },
        timeout=90,
    )
    response.raise_for_status()
    raw = response.json().get("response", "")
    elapsed = time.time() - started
    try:
        return extract_json(raw), raw, elapsed
    except Exception:
        return None, raw, elapsed


def validate(result: dict[str, Any] | None, max_chars: int) -> tuple[bool, list[str]]:
    flags = []
    if not result:
        return False, ["json_fail"]

    summary = str(result.get("summary") or "").strip()
    tags = result.get("tags")
    if not summary:
        flags.append("empty_summary")
    if len(summary) > max_chars:
        flags.append(f"summary_long:{len(summary)}")
    if has_ascii_alpha(summary):
        flags.append("summary_english")
    if any(word in summary for word in FORBIDDEN_WORDS):
        flags.append("summary_forbidden")

    if not isinstance(tags, list) or len(tags) != 3:
        flags.append("tag_count")
    else:
        for tag in tags:
            tag_value = str(tag).strip()
            if not tag_value or tag_value in GENERIC_TAGS:
                flags.append("tag_generic")
                break
            if len(tag_value) > 16:
                flags.append("tag_long")
                break
            if has_ascii_alpha(tag_value):
                flags.append("tag_english")
                break

    return not flags, flags


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Ollama prompt variants for course summaries")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--url", default=os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_HOST") or "http://wtr-linux:11434")
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "qwen3.5:9b"))
    parser.add_argument("--show-failures", action="store_true")
    args = parser.parse_args()

    rows = fetch_rows(args.limit)
    print(f"ollama={args.url} model={args.model} rows={len(rows)}")

    best_name = None
    best_score = -1
    for variant in VARIANTS:
        passed = 0
        total_elapsed = 0.0
        failures = []
        print("\n" + "=" * 80)
        print(f"variant={variant.name} max_chars={variant.max_chars}")

        for row in rows:
            description = clean_course_description(row.get("description") or "")
            prompt = variant.prompt.format(
                title=row.get("title") or "",
                description=description,
                category=row.get("category_raw") or "",
            )
            result, raw, elapsed = call_ollama(args.url, args.model, prompt)
            total_elapsed += elapsed
            ok, flags = validate(result, variant.max_chars)
            if ok:
                passed += 1
            elif args.show_failures:
                failures.append((row, result, raw, flags))

        score = passed / len(rows) if rows else 0
        print(f"pass={passed}/{len(rows)} ({score * 100:.1f}%) avg={total_elapsed / max(len(rows), 1):.1f}s")
        if failures:
            print("failures:")
            for row, result, raw, flags in failures[:5]:
                print(f"- [{row.get('provider')}] {row.get('title')} | flags={flags}")
                print(f"  result={json.dumps(result, ensure_ascii=False) if result else raw[:180]}")

        if passed > best_score:
            best_score = passed
            best_name = variant.name

    print("\nBEST:", best_name, f"{best_score}/{len(rows)}")


if __name__ == "__main__":
    main()
