from pathlib import Path

import yaml

from Crawler.Crawler_MunicipalYaml import load_candidate_file
from tools.run_municipal_course_search import classify_result, merge_search_results


ROOT = Path(__file__).resolve().parents[1]


def test_current_official_municipality_queue_contains_2026_reorganizations() -> None:
    data = yaml.safe_load(
        (ROOT / "config" / "municipal_course_search_targets.yaml").read_text(encoding="utf-8")
    )
    rows = data["municipalities"]
    names = {row["full_name"] for row in rows}

    assert len(rows) == data["totals"]["municipalities"] == 269
    assert {
        "인천광역시 제물포구",
        "인천광역시 영종구",
        "인천광역시 서해구",
        "인천광역시 검단구",
        "경기도 부천시 원미구",
        "경기도 화성시 동탄구",
        "전남광주통합특별시 광산구",
    }.issubset(names)
    assert all(row["primary_category"] == "integrated_reservation" for row in rows)
    assert all("통합예약" in row["primary_query"] for row in rows)


def test_every_current_municipality_has_an_executed_integrated_reservation_query() -> None:
    data = yaml.safe_load(
        (ROOT / "config" / "municipal_course_search_results.yaml").read_text(encoding="utf-8")
    )
    assert len(data["results"]) == 269
    for row in data["results"]:
        queries = [
            value.get("query", "") if isinstance(value, dict) else str(value)
            for value in row.get("queries_used") or []
        ]
        assert any("통합예약" in query for query in queries), row["full_name"]


def test_candidate_batch_carries_locked_education_and_municipality_identity(tmp_path: Path) -> None:
    source = tmp_path / "candidates.yaml"
    source.write_text(
        yaml.safe_dump(
            {
                "results": [
                    {
                        "code": "1111000000",
                        "sido": "서울특별시",
                        "sigungu": "종로구",
                        "full_name": "서울특별시 종로구",
                        "candidates": [
                            {
                                "status": "candidate",
                                "score": 12,
                                "title": "가족 체험",
                                "url": "https://example.go.kr/reserve/education",
                                "query": "종로구 통합예약",
                            }
                        ],
                    }
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    targets = []

    load_candidate_file(source, "municipal_course_candidate_results", targets, set(), 8, False)

    assert len(targets) == 1
    assert targets[0].extra["municipality_code"] == "1111000000"
    assert targets[0].extra["municipality_full_name"] == "서울특별시 종로구"
    assert targets[0].extra["service_group"] == "공공강좌"
    assert targets[0].extra["service_group_policy"] == "locked"


def test_result_merge_migrates_renamed_province_without_losing_results() -> None:
    queue = [{
        "code": "5219000000",
        "sido": "전북특별자치도",
        "sigungu": "남원시",
        "full_name": "전북특별자치도 남원시",
        "primary_query": "남원시 통합예약",
        "primary_category": "integrated_reservation",
        "google_search_url": "https://www.google.com/search?q=test",
    }]
    previous = [{
        "code": "4519000000",
        "full_name": "전라북도 남원시",
        "queries_used": ["전라북도 남원시 강좌신청"],
        "results": [{
            "title": "전라북도 남원시 통합예약 교육 강좌",
            "snippet": "남원시 평생학습 수강신청",
            "url": "https://example.go.kr/edu",
            "status": "candidate",
            "score": 10,
        }],
    }]

    merged = merge_search_results(queue, previous, [])

    assert merged[0]["code"] == "5219000000"
    assert merged[0]["full_name"] == "전북특별자치도 남원시"
    assert merged[0]["candidate_count"] == 1


def test_ambiguous_district_result_from_another_province_is_not_a_candidate() -> None:
    municipality = {
        "code": "1150000000",
        "sido": "서울특별시",
        "sigungu": "강서구",
        "full_name": "서울특별시 강서구",
    }

    status, score, reasons = classify_result(
        "부산광역시 통합예약 강서구 교육",
        "부산광역시 강서구 시설관리공단 강좌 신청",
        "https://reserve.busan.go.kr/lctre/list?srchGugun=11",
        municipality,
    )

    assert status != "candidate"
    assert score < 8
    assert "reject:region_mismatch:부산광역시" in reasons


def test_merged_province_accepts_the_correct_legacy_region_only() -> None:
    gwangju_donggu = {
        "code": "1221000000",
        "sido": "전남광주통합특별시",
        "sigungu": "동구",
        "full_name": "전남광주통합특별시 동구",
    }

    correct_status, _, correct_reasons = classify_result(
        "광주광역시 동구 통합예약",
        "교육 강좌 신청",
        "https://example.go.kr/reserve/lecture",
        gwangju_donggu,
    )
    wrong_status, wrong_score, wrong_reasons = classify_result(
        "대전광역시 동구 통합예약",
        "교육 강좌 신청",
        "https://example.go.kr/reserve/lecture",
        gwangju_donggu,
    )

    assert correct_status == "candidate"
    assert "match:region" in correct_reasons
    assert wrong_status != "candidate"
    assert wrong_score < 8
    assert "reject:region_mismatch:대전광역시" in wrong_reasons


def test_keyword_dense_wrong_metropolitan_result_is_still_review_only() -> None:
    municipality = {
        "code": "1230000000",
        "sido": "전남광주통합특별시",
        "sigungu": "북구",
        "full_name": "전남광주통합특별시 북구",
    }

    status, score, reasons = classify_result(
        "부산북구 통합예약 평생학습 교육 강좌 수강신청",
        "부산 시설관리공단 문화재단 도서관 프로그램 통합예약",
        "https://www.bsbukgu.go.kr/reservation/index.bsbukgu",
        municipality,
    )

    assert status != "candidate"
    assert score == 7
    assert any(reason.startswith("reject:region_mismatch:부산") for reason in reasons)
