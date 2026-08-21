import json
import os
import re
import sys
import urllib.request

from sqlalchemy import text

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from backend.database import SessionLocal
from target_cleaner import extract_target_text
from title_cleaner import clean_course_title


OLLAMA_URL = os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_HOST") or "http://wtr-linux:11434"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")


def extract_json(value: str) -> dict:
    value = value.strip()
    match = re.search(r"\{.*\}", value, re.S)
    if not match:
        raise ValueError(f"No JSON object found: {value[:200]}")
    return json.loads(match.group(0))


def call_ollama(row: dict) -> dict:
    prompt = f"""
너는 한국 문화센터 강좌 제목 정리기다. 입력을 분석해서 JSON 하나만 출력해라.

규칙:
- clean_title: 순수 강좌명만. 날짜, 요일, 시간, 연령, 년생, 개월, 접수방식은 제거한다.
- target_text: 대상 연령/조건만. 예: "24~48개월", "2020~22년생", "24개월 이상"
- schedule_text: 날짜/요일/시간 정보만.
- extra_note: 연령/일정이 아닌 접수 조건이나 관람 조건. 예: "아이만접수", "관람 가족 인당접수"
- 0516, 0520 같은 4자리 숫자는 시간이 아니라 MMDD 날짜다. 예: 0516(토)11:00~ = 5월16일 토요일 11:00 시작.
- 끝 시간이 명시되지 않은 "11:00~"를 "11:00-16:00"처럼 추정하지 마라.
- 확실하지 않으면 null.
- JSON 외 다른 설명 금지.

입력:
provider: {row.get('provider')}
category_raw: {row.get('category_raw')}
title: {row.get('title')}
title_raw: {row.get('title_raw')}
target: {row.get('target')}
schedule_raw: {row.get('schedule_raw')}

출력 형식:
{{
  "clean_title": string|null,
  "target_text": string|null,
  "schedule_text": string|null,
  "extra_note": string|null
}}
""".strip()
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": 220,
        },
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    return extract_json(data.get("response", ""))


def fetch_candidates(limit: int) -> list[dict]:
    sql = text(
        """
        SELECT provider, title, title_raw, target, schedule_raw, category_raw
        FROM courses
        WHERE is_active IS TRUE
          AND (
            COALESCE(title_raw, title) ~ '(년생|개월|\\[[^\\]]*/[월화수목금토일]/|[0-9]{1,2}:[0-9]{2}|아이만|인당접수|보호자|관람)'
            OR title <> COALESCE(title_raw, title)
          )
        GROUP BY provider, title, title_raw, target, schedule_raw, category_raw
        ORDER BY provider, title_raw
        LIMIT :limit
        """
    )
    with SessionLocal() as session:
        return [dict(row) for row in session.execute(sql, {"limit": limit}).mappings().all()]


def main() -> None:
    limit = int(os.getenv("LIMIT", "30"))
    rows = fetch_candidates(limit)
    print(f"ollama={OLLAMA_URL} model={OLLAMA_MODEL} candidates={len(rows)}")
    for idx, row in enumerate(rows, 1):
        source_title = row.get("title_raw") or row.get("title") or ""
        rule_title, removed = clean_course_title(source_title)
        rule_target = extract_target_text(source_title)
        try:
            llm = call_ollama(row)
            error = None
        except Exception as exc:
            llm = None
            error = str(exc)

        print("\n" + "=" * 80)
        print(f"#{idx} [{row.get('provider')}] {row.get('category_raw')}")
        print(f"RAW      : {source_title}")
        print(f"CURRENT  : {row.get('title')}")
        print(f"RULE     : title={rule_title!r} target={rule_target!r} removed={removed!r}")
        if error:
            print(f"LLM ERROR: {error}")
        else:
            print("LLM      :", json.dumps(llm, ensure_ascii=False))


if __name__ == "__main__":
    main()
