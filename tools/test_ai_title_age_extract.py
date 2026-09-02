from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from backend.database import SessionLocal
from data_parser import TargetParser
from target_cleaner import extract_target_text
from title_cleaner import clean_course_title


load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / "frontend2" / ".env")

OLLAMA_URL = (os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_HOST") or "http://wtr-linux:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")

TARGET_PARSER = TargetParser()


def extract_json(value: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", value, flags=re.S | re.I).strip()
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        raise ValueError(f"No JSON object found: {cleaned[:300]}")
    return json.loads(match.group(0))


def normalize_bool(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "1", "yes", "y"}


def normalize_ai_result(result: dict[str, Any], fallback_title: str, fallback_target: str | None) -> dict[str, Any]:
    clean_title = str(result.get("clean_title") or "").strip() or fallback_title
    target_text = str(result.get("target_text") or "").strip() or fallback_target
    target_note = str(result.get("target_note") or "").strip() or None
    confidence_raw = result.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    parsed = TARGET_PARSER.parse(" ".join(part for part in [target_text, target_note] if part))
    return {
        "clean_title": clean_title,
        "target_text": target_text,
        "age_group": parsed["age_group"] or result.get("age_group"),
        "min_age": parsed["min_age"],
        "max_age": parsed["max_age"],
        "target_with_parent": normalize_bool(result.get("target_with_parent")) or parsed["with_parent"],
        "target_note": target_note,
        "confidence": confidence,
    }


def call_ollama(row: dict[str, Any], rule_title: str, rule_target: str | None) -> dict[str, Any]:
    prompt = f"""
너는 한국 문화센터 강좌 제목에서 '순수 강좌명'과 '연령/대상'만 분리하는 데이터 정리기다.
JSON 하나만 출력한다. 설명, 마크다운, 코드블록은 금지한다.

목표:
- clean_title: 순수 강좌명. 연령, 년생, 개월, 요일, 날짜, 시간, 회차, 접수방식은 제거한다.
- target_text: 연령/대상 표현만. 예: "24~48개월", "2020~22년생", "24개월 이상", "5세 이상".
- target_note: 연령은 아니지만 대상/접수 조건인 문구. 예: "보호자 1인", "아이만접수", "관람 가족 인당접수".
- 일정/요일/시간/기간/수강료/재료비는 추출하지 말고 버린다.
- min_age/max_age는 확신할 때만 넣고, 개월/년생 계산이 애매하면 null로 둔다.
- 애매하면 기존 규칙 결과를 존중한다.

주의:
- "K-POP Star G.Den [2020~22년생/일/10:00]"에서 강좌명은 "K-POP Star G.Den", 대상은 "2020~22년생"이다.
- "빛과 마술의 콜라보! 라이트 드로잉 매직쇼(24개월 이상,관람 가족 인당접수)"에서 대상은 "24개월 이상", target_note는 "관람 가족 인당접수"이다.
- "벚꽃 팝콘(24~48개월)*아이만접수"에서 강좌명은 "벚꽃 팝콘", 대상은 "24~48개월", target_note는 "아이만접수"이다.
- "아이돌 댄스 따라잡기(포인트 안무)(2017~21년생)"에서 "(포인트 안무)"는 강좌명 일부이고, "2017~21년생"만 대상이다.
- "오늘은 내가 요리사(36개월-초등)"에서 강좌명은 "오늘은 내가 요리사", 대상은 "36개월-초등"이다.

입력:
provider: {row.get("provider")}
category_raw: {row.get("category_raw")}
title: {row.get("title")}
title_raw: {row.get("title_raw")}
target: {row.get("target")}
schedule_raw: {row.get("schedule_raw")}
rule_clean_title: {rule_title}
rule_target_text: {rule_target}

출력 JSON 형식:
{{
  "clean_title": "string",
  "target_text": "string|null",
  "age_group": "INFANT|TODDLER|CHILD|TEEN|ADULT|SENIOR|ALL|null",
  "min_age": 0,
  "max_age": 7,
  "target_with_parent": false,
  "target_note": "string|null",
  "confidence": 0.0
}}
""".strip()
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": 260},
    }
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        data = json.loads(response.read().decode("utf-8"))
    return extract_json(data.get("response", ""))


def fetch_candidates(limit: int, provider: str | None) -> list[dict[str, Any]]:
    provider_filter = "AND provider = :provider" if provider else ""
    sql = text(
        f"""
        SELECT id, provider, title, title_raw, target, target_age_group,
               target_min_age, target_max_age, schedule_raw, category_raw
        FROM courses
        WHERE COALESCE(is_active, TRUE) IS TRUE
          {provider_filter}
          AND (
            COALESCE(title_raw, title) ~ '(년생|개월|[0-9]{{1,2}}세|아이만|자녀만|보호자|가족|인당접수|\\[[^\\]]*/[월화수목금토일]/|[0-9]{{1,2}}:[0-9]{{2}})'
            OR target IS NULL
            OR target_age_group IS NULL
          )
        ORDER BY updated_at DESC NULLS LAST, provider, title
        LIMIT :limit
        """
    )
    params = {"limit": limit}
    if provider:
        params["provider"] = provider
    with SessionLocal() as session:
        return [dict(row) for row in session.execute(sql, params).mappings().all()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Test AI title/age extraction on course titles")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--no-ai", action="store_true", help="Print rule-only extraction without calling Ollama")
    args = parser.parse_args()

    rows = fetch_candidates(args.limit, args.provider)
    print(f"ollama={OLLAMA_URL} model={OLLAMA_MODEL} rows={len(rows)} no_ai={args.no_ai}")

    for index, row in enumerate(rows, 1):
        source_title = row.get("title_raw") or row.get("title") or ""
        rule_title, removed = clean_course_title(source_title)
        rule_target = extract_target_text(source_title) or row.get("target")
        rule_parsed = TARGET_PARSER.parse(" ".join(part for part in [rule_target or "", row.get("category_raw") or ""] if part))

        ai_result = None
        error = None
        if not args.no_ai:
            try:
                ai_result = normalize_ai_result(call_ollama(row, rule_title, rule_target), rule_title, rule_target)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

        print("\n" + "=" * 100)
        print(f"#{index} [{row.get('provider')}] id={row.get('id')}")
        print(f"RAW       : {source_title}")
        print(f"DB TITLE  : {row.get('title')}")
        print(f"DB TARGET : {row.get('target')} / {row.get('target_age_group')} {row.get('target_min_age')}~{row.get('target_max_age')}")
        print(
            "RULE      : "
            + json.dumps(
                {
                    "clean_title": rule_title,
                    "target_text": rule_target,
                    "age_group": rule_parsed["age_group"],
                    "min_age": rule_parsed["min_age"],
                    "max_age": rule_parsed["max_age"],
                    "target_with_parent": rule_parsed["with_parent"],
                    "removed": removed,
                },
                ensure_ascii=False,
            )
        )
        if error:
            print(f"AI ERROR  : {error}")
        elif ai_result:
            print("AI        : " + json.dumps(ai_result, ensure_ascii=False))


if __name__ == "__main__":
    main()
