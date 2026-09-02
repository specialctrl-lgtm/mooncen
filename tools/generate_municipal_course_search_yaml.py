from __future__ import annotations

import csv
import sys
import zipfile
from datetime import date
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import quote_plus

import requests


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "municipal_course_search_targets.yaml"
ADMIN_CODE_INDEX_URL = "https://www.code.go.kr/stdcode/regCodeL.do"
ADMIN_CODE_DOWNLOAD_URL = "https://www.code.go.kr/etc/codeFullDown.do"

SEARCH_CATEGORIES = [
    {
        "id": "integrated_reservation",
        "name": "통합예약",
        "keywords": ["통합예약", "통합예약 교육", "통합예약 강좌신청"],
    },
    {
        "id": "municipality",
        "name": "지자체",
        "keywords": ["강좌신청", "통합예약 강좌신청", "평생학습 수강신청", "문화센터 수강신청", "주민자치센터 수강신청"],
    },
    {
        "id": "learning_center",
        "name": "학습관",
        "keywords": ["학습관 강좌신청", "평생학습관 수강신청"],
    },
    {
        "id": "education_center",
        "name": "교육관",
        "keywords": ["교육관 강좌신청", "교육센터 수강신청"],
    },
    {
        "id": "welfare_center",
        "name": "사회복지관",
        "keywords": ["사회복지관 강좌신청", "복지관 프로그램 신청"],
    },
    {
        "id": "library",
        "name": "도서관",
        "keywords": ["도서관 문화강좌 신청", "도서관 프로그램 신청"],
    },
    {
        "id": "art_museum",
        "name": "미술관",
        "keywords": ["미술관 교육 신청", "미술관 프로그램 신청"],
    },
    {
        "id": "arboretum",
        "name": "수목원",
        "keywords": ["수목원 교육 신청", "수목원 체험 신청"],
    },
    {
        "id": "museum",
        "name": "박물관",
        "keywords": ["박물관 교육 신청", "박물관 체험 신청"],
    },
    {
        "id": "sports_center",
        "name": "스포츠센터",
        "keywords": [
            "스포츠센터 수강신청",
            "체육센터 수강신청",
            "국민체육센터 수강신청",
            "생활체육센터 수강신청",
            "공공체육시설 수강신청",
            "체육관 수강신청",
        ],
    },
    {
        "id": "facility_corporation",
        "name": "시설관리공단",
        "keywords": [
            "시설관리공단 수강신청",
            "시설공단 수강신청",
            "도시관리공단 수강신청",
            "도시공사 수강신청",
            "도시개발공사 수강신청",
            "공단 수강신청",
        ],
    },
    {
        "id": "course_application",
        "name": "수강신청",
        "keywords": ["수강신청", "강좌 수강신청", "교육 수강신청"],
    },
    {
        "id": "sports_dong",
        "name": "동 체육 수강신청",
        "keywords": ["체육 수강신청 동", "생활체육 수강신청 동", "체육시설 수강신청 동"],
    },
]


def yaml_scalar(value: object) -> str:
    text = "" if value is None else str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def municipality_type(name: str) -> str:
    if name.endswith("시"):
        return "city"
    if name.endswith("구"):
        return "district"
    if name.endswith("군"):
        return "county"
    return "municipality"


def priority_for(name: str) -> int:
    if name.endswith("구"):
        return 1
    if name.endswith("시"):
        return 2
    if name.endswith("군"):
        return 3
    return 4


def load_municipalities() -> list[dict[str, str]]:
    session = requests.Session()
    index_response = session.get(ADMIN_CODE_INDEX_URL, timeout=30)
    index_response.raise_for_status()
    response = session.post(
        ADMIN_CODE_DOWNLOAD_URL,
        data={"codeseId": "법정동코드"},
        timeout=90,
    )
    response.raise_for_status()
    try:
        archive = zipfile.ZipFile(BytesIO(response.content))
        member = next(name for name in archive.namelist() if name.lower().endswith(".txt"))
        source_text = archive.read(member).decode("cp949")
    except (StopIteration, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise RuntimeError("행정표준코드 법정동 전체자료를 읽을 수 없습니다.") from exc

    rows = csv.DictReader(StringIO(source_text), delimiter="\t")
    municipalities: list[dict[str, str]] = []
    seen: set[str] = set()

    for row in rows:
        if (row.get("폐지여부") or "").strip() != "존재":
            continue
        code = (row.get("법정동코드") or "").strip()
        full_name = (row.get("법정동명") or "").strip()
        parts = full_name.split()
        if len(code) != 10 or not code.endswith("00000") or not parts:
            continue
        if not parts[-1].endswith(("시", "구", "군")):
            continue
        # 시도 자체는 제외하되 기초자치단체가 없는 세종특별자치시는 포함한다.
        if len(parts) == 1 and full_name != "세종특별자치시":
            continue
        if code in seen:
            continue
        seen.add(code)

        sido = parts[0]
        sigungu = " ".join(parts[1:]) if len(parts) > 1 else full_name

        municipalities.append(
            {
                "code": code,
                "sido": sido,
                "sigungu": sigungu,
                "full_name": full_name,
                "type": municipality_type(parts[-1]),
                "priority": str(priority_for(parts[-1])),
            }
        )
    return sorted(municipalities, key=lambda item: item["code"])


def categorized_query_set(item: dict[str, str]) -> list[dict[str, str]]:
    full_name = item["full_name"]
    short_name = item["sigungu"]
    queries: list[dict[str, str]] = []
    seen: set[str] = set()

    for category in SEARCH_CATEGORIES:
        for index, keyword in enumerate(category["keywords"]):
            region = full_name if category["id"] == "municipality" and index == 0 else short_name
            query = f"{region} {keyword}"
            if query in seen:
                continue
            seen.add(query)
            queries.append(
                {
                    "category_id": category["id"],
                    "category_name": category["name"],
                    "keyword": keyword,
                    "query": query,
                    "google_search_url": "https://www.google.com/search?q=" + quote_plus(query),
                }
            )

    return queries


def query_set(item: dict[str, str]) -> list[str]:
    return [entry["query"] for entry in categorized_query_set(item)]


def write_yaml(items: list[dict[str, str]]) -> None:
    type_counts: dict[str, int] = {}
    sido_counts: dict[str, int] = {}
    for item in items:
        type_counts[item["type"]] = type_counts.get(item["type"], 0) + 1
        sido_counts[item["sido"]] = sido_counts.get(item["sido"], 0) + 1

    lines: list[str] = [
        "# MoonCen municipal course search target registry.",
        "# Generated from the Ministry of the Interior and Safety legal-dong code archive.",
        "# Scope: every active 시/구/군 + 강좌신청 search queue.",
        "",
        "version: 1",
        f"updated_at: {yaml_scalar(date.today().isoformat())}",
        "source:",
        "  name: 행정표준코드관리시스템 법정동 코드 전체자료",
        f"  url: {yaml_scalar(ADMIN_CODE_INDEX_URL)}",
        "  note: \"행정안전부 행정표준코드관리시스템의 현존 법정동 코드를 실행 시점에 직접 내려받아 사용.\"",
        "",
        "discovery_policy:",
        "  search_engine: google",
        "  status_flow: \"search_queue -> searched -> candidate -> discovery -> ready -> active -> rejected\"",
        "  default_query_suffix: \"통합예약\"",
        "  include_keywords:",
        "    - 강좌신청",
        "    - 수강신청",
        "    - 통합예약",
        "    - 평생학습",
        "    - 문화강좌",
        "    - 사회복지관",
        "    - 도서관",
        "    - 주민자치센터",
        "    - 학습관",
        "    - 교육관",
        "    - 미술관",
        "    - 수목원",
        "    - 박물관",
        "    - 스포츠센터",
        "    - 체육센터",
        "    - 국민체육센터",
        "    - 생활체육센터",
        "    - 공공체육시설",
        "    - 체육관",
        "    - 수강신청",
        "    - 생활체육",
        "    - 시설관리공단",
        "    - 시설공단",
        "    - 도시관리공단",
        "    - 도시공사",
        "    - 도시개발공사",
        "    - 재단",
        "  reject_keywords:",
        "    - 사회복지사 자격증",
        "    - 대학 수강신청",
        "    - 보수교육",
        "    - 학점은행",
        "    - 민간 자격증",
        "    - 초등학교",
        "    - 중학교",
        "    - 고등학교",
        "    - 대학교",
        "    - 방과후학교",
        "    - 늘봄학교",
        "  result_handling:",
        "    - \"구글 결과 URL을 직접 운영 크롤러로 쓰지 않고, 결과의 실제 기관/통합예약 URL을 candidate target으로 승격한다.\"",
        "    - \"같은 지자체에서 통합예약과 평생학습 포털이 동시에 발견되면 통합예약을 우선 소스로 둔다.\"",
        "    - \"사회복지관/도서관/주민자치센터/학습관/교육관/미술관/수목원/박물관/스포츠센터 개별 페이지는 상세 보강 소스로 둔다.\"",
        "",
        "search_categories:",
    ]

    for category in SEARCH_CATEGORIES:
        lines.extend(
            [
                f"  - id: {yaml_scalar(category['id'])}",
                f"    name: {yaml_scalar(category['name'])}",
                "    keywords:",
            ]
        )
        for keyword in category["keywords"]:
            lines.append(f"      - {yaml_scalar(keyword)}")

    lines.extend(["", "totals:", f"  municipalities: {len(items)}", "  by_type:"])

    for key in sorted(type_counts):
        lines.append(f"    {key}: {type_counts[key]}")
    lines.append("  by_sido:")
    for sido in sorted(sido_counts):
        lines.append(f"    {yaml_scalar(sido)}: {sido_counts[sido]}")

    lines.extend(["", "municipalities:"])
    for item in items:
        categorized_queries = categorized_query_set(item)
        queries = [entry["query"] for entry in categorized_queries]
        lines.extend(
            [
                f"  - code: {yaml_scalar(item['code'])}",
                f"    sido: {yaml_scalar(item['sido'])}",
                f"    sigungu: {yaml_scalar(item['sigungu'])}",
                f"    full_name: {yaml_scalar(item['full_name'])}",
                f"    municipality_type: {yaml_scalar(item['type'])}",
                f"    priority: {item['priority']}",
                "    status: search_queue",
                f"    primary_category: {yaml_scalar(categorized_queries[0]['category_id'])}",
                f"    primary_query: {yaml_scalar(queries[0])}",
                f"    google_search_url: {yaml_scalar(categorized_queries[0]['google_search_url'])}",
                "    alternate_queries:",
            ]
        )
        for query in queries[1:]:
            lines.append(f"      - {yaml_scalar(query)}")
        lines.append("    categorized_queries:")
        for entry in categorized_queries:
            lines.extend(
                [
                    f"      - category_id: {yaml_scalar(entry['category_id'])}",
                    f"        category_name: {yaml_scalar(entry['category_name'])}",
                    f"        keyword: {yaml_scalar(entry['keyword'])}",
                    f"        query: {yaml_scalar(entry['query'])}",
                    f"        google_search_url: {yaml_scalar(entry['google_search_url'])}",
                ]
            )
        lines.append("    candidate_target_file: \"config/public_course_targets.yaml\"")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    municipalities = load_municipalities()
    if not municipalities:
        print("No municipalities loaded", file=sys.stderr)
        return 1
    write_yaml(municipalities)
    print(f"wrote {OUT} municipalities={len(municipalities)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
