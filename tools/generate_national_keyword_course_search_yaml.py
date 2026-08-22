from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "national_keyword_course_search_targets.yaml"

KEYWORD_GROUPS = [
    {
        "id": "national_experience_reservation",
        "label": "국립 체험 예약",
        "category": "national_experience",
        "seed_keywords": ["국립 체험 예약"],
        "expanded_keywords": [
            "국립 체험 신청",
            "국립 체험 프로그램",
            "국립 어린이 체험 예약",
            "국립 가족 체험 예약",
            "국립 주말 체험 예약",
        ],
    },
    {
        "id": "national_guided_tour_reservation",
        "label": "국립 해설 예약",
        "category": "national_guided_tour",
        "seed_keywords": ["국립 해설 예약"],
        "expanded_keywords": [
            "국립 해설 신청",
            "국립 전시 해설 예약",
            "국립 생태 해설 예약",
            "국립 숲 해설 예약",
            "국립 문화해설 예약",
        ],
    },
    {
        "id": "national_lecture_reservation",
        "label": "국립 강의 예약",
        "category": "national_lecture",
        "seed_keywords": ["국립 강의 예약"],
        "expanded_keywords": [
            "국립 강좌 예약",
            "국립 교육 예약",
            "국립 교육 신청",
            "국립 강의 신청",
            "국립 특강 예약",
        ],
    },
    {
        "id": "national_exploration_reservation",
        "label": "국립 탐방 예약",
        "category": "national_exploration",
        "seed_keywords": ["국립 탐방 예약"],
        "expanded_keywords": [
            "국립 탐방 신청",
            "국립 생태탐방 예약",
            "국립공원 탐방 예약",
            "국립공원 탐방프로그램 예약",
            "국립 자연탐방 예약",
        ],
    },
    {
        "id": "museum_reservation",
        "label": "박물관 예약",
        "category": "museum_reservation",
        "seed_keywords": ["박물관 예약"],
        "expanded_keywords": [
            "박물관 교육 예약",
            "박물관 체험 예약",
            "박물관 해설 예약",
            "국립 박물관 예약",
            "국립박물관 교육 신청",
            "국립박물관 체험 프로그램",
        ],
    },
]

DOMAIN_SCOPES = [
    {"id": "go_kr", "query_prefix": "site:.go.kr", "label": "정부 도메인"},
    {"id": "or_kr", "query_prefix": "site:.or.kr", "label": "공공기관 도메인"},
    {"id": "re_kr", "query_prefix": "site:.re.kr", "label": "연구/공공기관 도메인"},
]


def google_url(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query)


def build_queries(group: dict) -> list[dict]:
    queries: list[dict] = []
    seen: set[str] = set()
    keywords = group["seed_keywords"] + group["expanded_keywords"]

    for keyword in keywords:
        if keyword in seen:
            continue
        seen.add(keyword)
        queries.append(
            {
                "type": "keyword",
                "keyword": keyword,
                "query": keyword,
                "google_search_url": google_url(keyword),
            }
        )

    for scope in DOMAIN_SCOPES:
        for keyword in keywords:
            query = f"{scope['query_prefix']} {keyword}"
            if query in seen:
                continue
            seen.add(query)
            queries.append(
                {
                    "type": "site_scoped_keyword",
                    "domain_scope": scope["id"],
                    "domain_scope_label": scope["label"],
                    "keyword": keyword,
                    "query": query,
                    "google_search_url": google_url(query),
                }
            )

    return queries


def main() -> int:
    targets = []
    for group in KEYWORD_GROUPS:
        queries = build_queries(group)
        targets.append(
            {
                "id": group["id"],
                "label": group["label"],
                "category": group["category"],
                "status": "search_seed",
                "priority": 1,
                "seed_keywords": group["seed_keywords"],
                "expanded_keywords": group["expanded_keywords"],
                "query_count": len(queries),
                "queries": queries,
            }
        )

    data = {
        "version": 1,
        "generated_at": date.today().isoformat(),
        "scope": "국립/공공기관의 체험, 해설, 강의, 탐방, 박물관 예약 후보 URL 탐색용 키워드 검색 큐",
        "collection_policy": {
            "include": [
                "공식 예약/신청/교육/체험 목록 페이지",
                "국립 또는 공공기관이 운영하는 박물관, 과학관, 생태원, 수목원, 국립공원, 전시관 프로그램",
                "교육, 강의, 해설, 체험, 탐방, 가족/어린이 프로그램",
            ],
            "exclude": [
                "블로그 후기, 언론 기사, 여행 후기, 단순 장소 소개",
                "민간 체험 업체, 사설 학원, 학교 내부 공지",
                "예약 가능한 프로그램 목록 없이 공지만 있는 페이지",
            ],
            "preferred_domains": [".go.kr", ".or.kr", ".re.kr", "공식 기관 도메인"],
        },
        "domain_scopes": DOMAIN_SCOPES,
        "targets": targets,
        "summary": {
            "target_count": len(targets),
            "query_count": sum(target["query_count"] for target in targets),
            "seed_keywords": [keyword for group in KEYWORD_GROUPS for keyword in group["seed_keywords"]],
            "categories": [group["category"] for group in KEYWORD_GROUPS],
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")
    print(f"wrote {OUT} targets={data['summary']['target_count']} queries={data['summary']['query_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
