from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "national_institution_course_search_targets.yaml"

QUERY_INTENTS = [
    {
        "id": "education",
        "label": "교육",
        "keywords": ["교육 프로그램", "교육 신청", "교육 예약"],
    },
    {
        "id": "guided_tour",
        "label": "해설",
        "keywords": ["해설 프로그램", "전시 해설", "생태 해설", "숲 해설"],
    },
    {
        "id": "experience",
        "label": "체험",
        "keywords": ["체험 프로그램", "체험 신청", "체험 예약"],
    },
    {
        "id": "school_group",
        "label": "단체/학교",
        "keywords": ["단체 교육", "학교 교육", "청소년 체험"],
    },
    {
        "id": "family",
        "label": "가족",
        "keywords": ["가족 체험", "어린이 교육", "주말 체험"],
    },
]

SITE_SCOPED_KEYWORDS = [
    "교육",
    "해설",
    "체험",
    "예약",
    "신청",
    "프로그램",
]

EXCLUDED_BASE_URLS = {
    "https://www.bdna.or.kr",
    "https://www.nnibr.re.kr",
    "https://kna.forest.go.kr",
    "https://www.nibr.go.kr",
}

INSTITUTIONS = [
    {
        "provider": "NATIONAL_SCIENCE_MUSEUM",
        "name": "국립중앙과학관",
        "category": "national_science_museum",
        "ministry": "과학기술정보통신부",
        "region": "대전광역시",
        "base_url": "https://www.science.go.kr",
        "priority": 1,
    },
    {
        "provider": "GWACHEON_NATIONAL_SCIENCE_MUSEUM",
        "name": "국립과천과학관",
        "category": "national_science_museum",
        "ministry": "과학기술정보통신부",
        "region": "경기도 과천시",
        "base_url": "https://www.sciencecenter.go.kr",
        "priority": 1,
    },
    {
        "provider": "GWANGJU_NATIONAL_SCIENCE_MUSEUM",
        "name": "국립광주과학관",
        "category": "national_science_museum",
        "ministry": "과학기술정보통신부",
        "region": "광주광역시",
        "base_url": "https://www.sciencecenter.or.kr",
        "priority": 1,
    },
    {
        "provider": "DAEGU_NATIONAL_SCIENCE_MUSEUM",
        "name": "국립대구과학관",
        "category": "national_science_museum",
        "ministry": "과학기술정보통신부",
        "region": "대구광역시",
        "base_url": "https://www.dnsm.or.kr",
        "priority": 1,
    },
    {
        "provider": "BUSAN_NATIONAL_SCIENCE_MUSEUM",
        "name": "국립부산과학관",
        "category": "national_science_museum",
        "ministry": "과학기술정보통신부",
        "region": "부산광역시",
        "base_url": "https://www.sciport.or.kr",
        "priority": 1,
    },
    {
        "provider": "NATIONAL_ECOLOGY_CENTER",
        "name": "국립생태원",
        "category": "national_ecology",
        "ministry": "환경부",
        "region": "충청남도 서천군",
        "base_url": "https://www.nie.re.kr",
        "priority": 1,
    },
    {
        "provider": "NAKDONGGANG_BIOLOGICAL_RESOURCES",
        "name": "국립낙동강생물자원관",
        "category": "national_biodiversity",
        "ministry": "환경부",
        "region": "경상북도 상주시",
        "base_url": "https://www.nnibr.re.kr",
        "priority": 1,
    },
    {
        "provider": "HONAM_BIOLOGICAL_RESOURCES",
        "name": "국립호남권생물자원관",
        "category": "national_biodiversity",
        "ministry": "환경부",
        "region": "전라남도 목포시",
        "base_url": "https://www.hnibr.re.kr",
        "priority": 1,
    },
    {
        "provider": "MARINE_BIODIVERSITY_INSTITUTE",
        "name": "국립해양생물자원관",
        "category": "national_biodiversity",
        "ministry": "해양수산부",
        "region": "충청남도 서천군",
        "base_url": "https://www.mabik.re.kr",
        "priority": 1,
    },
    {
        "provider": "NATIONAL_INSTITUTE_OF_BIOLOGICAL_RESOURCES",
        "name": "국립생물자원관",
        "category": "national_biodiversity",
        "ministry": "환경부",
        "region": "인천광역시",
        "base_url": "https://www.nibr.go.kr",
        "priority": 1,
    },
    {
        "provider": "NATIONAL_ARBORETUM",
        "name": "국립수목원",
        "category": "national_arboretum_garden",
        "ministry": "산림청",
        "region": "경기도 포천시",
        "base_url": "https://kna.forest.go.kr",
        "priority": 1,
    },
    {
        "provider": "BAEKDUDAEGAN_NATIONAL_ARBORETUM",
        "name": "국립백두대간수목원",
        "category": "national_arboretum_garden",
        "ministry": "산림청",
        "region": "경상북도 봉화군",
        "base_url": "https://www.bdna.or.kr",
        "priority": 1,
    },
    {
        "provider": "SEJONG_NATIONAL_ARBORETUM",
        "name": "국립세종수목원",
        "category": "national_arboretum_garden",
        "ministry": "산림청",
        "region": "세종특별자치시",
        "base_url": "https://www.sjna.or.kr",
        "priority": 1,
    },
    {
        "provider": "NATIONAL_GARDEN_INSTITUTE",
        "name": "한국수목원정원관리원",
        "category": "national_arboretum_garden",
        "ministry": "산림청",
        "region": "전국",
        "base_url": "https://www.koagi.or.kr",
        "priority": 2,
    },
    {
        "provider": "NATIONAL_PARK_RESERVATION",
        "name": "국립공원공단 예약시스템",
        "category": "national_park_ecology",
        "ministry": "환경부",
        "region": "전국",
        "base_url": "https://res.knps.or.kr",
        "priority": 1,
    },
    {
        "provider": "NATIONAL_PARK_SERVICE",
        "name": "국립공원공단",
        "category": "national_park_ecology",
        "ministry": "환경부",
        "region": "전국",
        "base_url": "https://www.knps.or.kr",
        "priority": 1,
        "extra_keywords": ["생태탐방원", "자연해설", "탐방 프로그램"],
    },
    {
        "provider": "NATIONAL_FOREST_RECREATION",
        "name": "숲나들e",
        "category": "national_forest_experience",
        "ministry": "산림청",
        "region": "전국",
        "base_url": "https://www.foresttrip.go.kr",
        "priority": 2,
        "extra_keywords": ["숲체험", "산림교육", "자연휴양림 체험"],
    },
    {
        "provider": "NATIONAL_FOREST_EDUCATION_CENTER",
        "name": "국립산림치유원",
        "category": "national_forest_experience",
        "ministry": "산림청",
        "region": "경상북도 영주시",
        "base_url": "https://daslim.fowi.or.kr",
        "priority": 2,
        "extra_keywords": ["산림치유", "숲치유", "치유 프로그램"],
    },
    {
        "provider": "NATIONAL_OCEAN_SCIENCE_MUSEUM",
        "name": "국립해양과학관",
        "category": "national_marine_science",
        "ministry": "해양수산부",
        "region": "경상북도 울진군",
        "base_url": "https://www.kosm.or.kr",
        "priority": 1,
    },
    {
        "provider": "KOREA_NATIONAL_MARITIME_MUSEUM",
        "name": "국립해양박물관",
        "category": "national_marine_science",
        "ministry": "해양수산부",
        "region": "부산광역시",
        "base_url": "https://www.mmk.or.kr",
        "priority": 2,
    },
    {
        "provider": "NATIONAL_LIGHTHOUSE_MUSEUM",
        "name": "국립등대박물관",
        "category": "national_marine_science",
        "ministry": "해양수산부",
        "region": "경상북도 포항시",
        "base_url": "https://www.lighthouse-museum.or.kr",
        "priority": 2,
    },
    {
        "provider": "NATIONAL_AGRICULTURAL_MUSEUM",
        "name": "국립농업박물관",
        "category": "national_agriculture_science",
        "ministry": "농림축산식품부",
        "region": "경기도 수원시",
        "base_url": "https://www.namuk.or.kr",
        "priority": 2,
    },
    {
        "provider": "RURAL_DEVELOPMENT_ADMINISTRATION",
        "name": "농촌진흥청 농업과학관",
        "category": "national_agriculture_science",
        "ministry": "농촌진흥청",
        "region": "전라북도 전주시",
        "base_url": "https://www.rda.go.kr",
        "priority": 2,
        "extra_keywords": ["농업과학관", "농업 체험", "어린이 농업교육"],
    },
    {
        "provider": "NATIONAL_AVIATION_MUSEUM",
        "name": "국립항공박물관",
        "category": "national_transport_science",
        "ministry": "국토교통부",
        "region": "서울특별시",
        "base_url": "https://www.aviation.or.kr",
        "priority": 2,
    },
    {
        "provider": "NATIONAL_RAILROAD_MUSEUM",
        "name": "철도박물관",
        "category": "national_transport_science",
        "ministry": "국토교통부",
        "region": "경기도 의왕시",
        "base_url": "https://www.railroadmuseum.co.kr",
        "priority": 3,
    },
    {
        "provider": "NATIONAL_METEOROLOGICAL_MUSEUM",
        "name": "국립기상박물관",
        "category": "national_science_museum",
        "ministry": "기상청",
        "region": "서울특별시",
        "base_url": "https://science.kma.go.kr",
        "priority": 2,
        "extra_keywords": ["기상 교육", "기상 체험", "날씨 체험"],
    },
    {
        "provider": "NATIONAL_FOLK_MUSEUM",
        "name": "국립민속박물관",
        "category": "national_museum_history",
        "ministry": "문화체육관광부",
        "region": "서울특별시",
        "base_url": "https://www.nfm.go.kr",
        "priority": 2,
    },
    {
        "provider": "NATIONAL_MUSEUM_OF_KOREA",
        "name": "국립중앙박물관",
        "category": "national_museum_history",
        "ministry": "문화체육관광부",
        "region": "서울특별시",
        "base_url": "https://www.museum.go.kr",
        "priority": 2,
    },
    {
        "provider": "NATIONAL_HANGEUL_MUSEUM",
        "name": "국립한글박물관",
        "category": "national_museum_history",
        "ministry": "문화체육관광부",
        "region": "서울특별시",
        "base_url": "https://www.hangeul.go.kr",
        "priority": 2,
    },
    {
        "provider": "NATIONAL_PALACE_MUSEUM",
        "name": "국립고궁박물관",
        "category": "national_museum_history",
        "ministry": "국가유산청",
        "region": "서울특별시",
        "base_url": "https://www.gogung.go.kr",
        "priority": 2,
    },
    {
        "provider": "NATIONAL_INTANGIBLE_HERITAGE_CENTER",
        "name": "국립무형유산원",
        "category": "national_cultural_heritage",
        "ministry": "국가유산청",
        "region": "전라북도 전주시",
        "base_url": "https://www.nihc.go.kr",
        "priority": 2,
    },
    {
        "provider": "NATIONAL_MUSEUM_OF_MODERN_ART",
        "name": "국립현대미술관",
        "category": "national_art_museum",
        "ministry": "문화체육관광부",
        "region": "전국",
        "base_url": "https://www.mmca.go.kr",
        "priority": 2,
    },
    {
        "provider": "ASIA_CULTURE_CENTER",
        "name": "국립아시아문화전당",
        "category": "national_art_culture",
        "ministry": "문화체육관광부",
        "region": "광주광역시",
        "base_url": "https://www.acc.go.kr",
        "priority": 2,
    },
    {
        "provider": "NATIONAL_GUGAK_CENTER",
        "name": "국립국악원",
        "category": "national_art_culture",
        "ministry": "문화체육관광부",
        "region": "서울특별시",
        "base_url": "https://www.gugak.go.kr",
        "priority": 3,
        "extra_keywords": ["국악 교육", "국악 체험", "어린이 국악"],
    },
]


def google_url(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query)


def site_query(base_url: str, keyword: str) -> str:
    host = base_url.replace("https://", "").replace("http://", "").strip("/")
    return f"site:{host} {keyword}"


def build_queries(item: dict) -> list[dict]:
    queries: list[dict] = []
    seen: set[str] = set()

    for intent in QUERY_INTENTS:
        for keyword in intent["keywords"]:
            query = f"{item['name']} {keyword}"
            if query not in seen:
                seen.add(query)
                queries.append(
                    {
                        "intent": intent["id"],
                        "intent_label": intent["label"],
                        "keyword": keyword,
                        "query": query,
                        "google_search_url": google_url(query),
                    }
                )

    for keyword in item.get("extra_keywords", []):
        query = f"{item['name']} {keyword}"
        if query not in seen:
            seen.add(query)
            queries.append(
                {
                    "intent": "extra",
                    "intent_label": "기관특화",
                    "keyword": keyword,
                    "query": query,
                    "google_search_url": google_url(query),
                }
            )

    for keyword in SITE_SCOPED_KEYWORDS:
        query = site_query(item["base_url"], keyword)
        if query not in seen:
            seen.add(query)
            queries.append(
                {
                    "intent": "site_scoped",
                    "intent_label": "사이트검색",
                    "keyword": keyword,
                    "query": query,
                    "google_search_url": google_url(query),
                }
            )

    return queries


def main() -> int:
    targets = []
    for item in INSTITUTIONS:
        if item.get("base_url") in EXCLUDED_BASE_URLS:
            continue
        queries = build_queries(item)
        target = {
            "provider": item["provider"],
            "name": item["name"],
            "category": item["category"],
            "owner_type": "national_government_or_public_institution",
            "ministry": item["ministry"],
            "region": item["region"],
            "priority": item["priority"],
            "status": "search_seed",
            "base_url": item["base_url"],
            "search_query_count": len(queries),
            "queries": queries,
            "crawler_notes": [
                "검색 결과에서 실제 교육/해설/체험 목록 URL을 후보로 승격한다.",
                "통합예약/별도 교육예약 도메인이 발견되면 list_url 후보로 분리한다.",
                "상세 페이지에서 title, period, schedule_raw, target, fee, status, description 필드를 우선 확인한다.",
            ],
        }
        targets.append(target)

    data = {
        "version": 1,
        "generated_at": date.today().isoformat(),
        "scope": "국가 또는 중앙 공공기관이 운영하는 과학관, 생태원, 생물자원관, 수목원, 국립공원, 해양/농업/항공/박물관의 교육·해설·체험 검색 큐",
        "collection_policy": {
            "include": [
                "공식 홈페이지 또는 공식 예약/교육 플랫폼의 교육, 해설, 체험, 단체, 가족 프로그램",
                "국립/중앙정부/공공기관이 운영하거나 위탁 운영하는 전시·체험 기관",
                "무료/유료 교육 프로그램, 전시해설, 생태해설, 숲해설, 청소년·가족 체험",
            ],
            "exclude": [
                "언론 기사, 블로그 후기, 여행 후기, 단순 시설 소개",
                "채용, 입찰, 공지사항만 있고 실제 신청 가능한 프로그램 목록이 없는 페이지",
                "민간 학원, 사설 체험 업체, 학교 내부 공지",
            ],
            "preferred_domains": [".go.kr", ".or.kr", ".re.kr", "official institution domains"],
        },
        "query_intents": QUERY_INTENTS,
        "targets": targets,
        "summary": {
            "target_count": len(targets),
            "query_count": sum(len(target["queries"]) for target in targets),
            "categories": sorted({target["category"] for target in targets}),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")
    print(f"wrote {OUT} targets={data['summary']['target_count']} queries={data['summary']['query_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
