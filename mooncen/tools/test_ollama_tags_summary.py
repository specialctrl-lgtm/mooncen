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


OLLAMA_URL = os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_HOST") or "http://wtr-linux:11434"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")


def extract_json(value: str) -> dict:
    match = re.search(r"\{.*\}", value.strip(), re.S)
    if not match:
        raise ValueError(f"No JSON object found: {value[:200]}")
    return json.loads(match.group(0))


def call_ollama(row: dict) -> dict:
    prompt = f"""
너는 문화센터 강좌 추천용 콘텐츠 큐레이터다. JSON 하나만 출력해라.

입력:
문화센터: {row.get('provider')}
지점: {row.get('branch_name')}
카테고리: {row.get('category_raw')}
제목: {row.get('title')}
대상: {row.get('target') or row.get('target_age_group')}
일정: {row.get('schedule_raw')}
강사: {row.get('instructor')}
기존 설명: {(row.get('description') or '')[:1200]}

요구사항:
- tags: 사용자가 검색/필터에 쓸 만한 한국어 키워드 4~6개. 너무 일반적인 "문화센터", "강좌" 제외.
- summary: 카드에 표시할 45자 이내 한국어 한 문장.
- description: 상세 페이지용 120자 이내 한국어 설명. 원문에 없는 과장/효과/보장 표현 금지.
- audience_note: 대상/보호자/접수 조건을 40자 이내로 요약. 없으면 null.
- instructor, 준비물, 신분증, 접수 조건, 혜택은 입력에 명시된 경우에만 써라.
- 원문에 없는 조건을 추측해서 쓰지 마라.
- confidence: 0.0~1.0. 원문 정보가 빈약하면 낮게.
- JSON 외 설명 금지.

출력 형식:
{{
  "tags": ["키워드1", "키워드2", "키워드3", "키워드4"],
  "summary": "45자 이내 요약",
  "description": "120자 이내 설명",
  "audience_note": "대상 조건",
  "confidence": 0.8
}}
""".strip()

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 320},
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        data = json.loads(response.read().decode("utf-8"))
    return extract_json(data.get("response", ""))


def fetch_candidates(limit: int) -> list[dict]:
    sql = text(
        """
        SELECT DISTINCT ON (c.provider, c.category_raw, c.title)
               c.id, c.provider, c.title, c.target, c.target_age_group, c.category_raw,
               c.schedule_raw, c.instructor, c.description, b.name AS branch_name
        FROM courses c
        LEFT JOIN branches b ON b.id = c.branch_id
        WHERE c.is_active IS TRUE
        ORDER BY c.provider, c.category_raw, c.title, c.updated_at DESC NULLS LAST
        LIMIT :limit
        """
    )
    with SessionLocal() as session:
        return [dict(row) for row in session.execute(sql, {"limit": limit}).mappings().all()]


def main() -> None:
    limit = int(os.getenv("LIMIT", "10"))
    rows = fetch_candidates(limit)
    print(f"ollama={OLLAMA_URL} model={OLLAMA_MODEL} candidates={len(rows)}")
    for idx, row in enumerate(rows, 1):
        try:
            result = call_ollama(row)
            error = None
        except Exception as exc:
            result = None
            error = str(exc)

        print("\n" + "=" * 80)
        print(f"#{idx} [{row.get('provider')}] {row.get('category_raw')} / {row.get('branch_name')}")
        print(f"TITLE : {row.get('title')}")
        print(f"TARGET: {row.get('target') or row.get('target_age_group')}")
        print(f"DESC  : {(row.get('description') or '')[:180]}")
        if error:
            print(f"ERROR : {error}")
        else:
            print("LLM   :", json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
