from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

import Crawler.Crawler_GeneratedYamlTargets as generated
import Crawler.Crawler_MunicipalYaml as municipal


ROOT = Path(__file__).resolve().parents[1]
PROVIDER = "MUNI_WWW_DDM_GO_KR_315F4471"
LEGACY_PROVIDER = "MUNI_WWW_DDM_GO_KR_0521A5B9"
LIST_URL = "https://www.ddm.go.kr/reserve/selectDongdaemunUserCourseList.do?key=1529"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _target(url: str = LIST_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=PROVIDER,
        name="동대문구 예약포털 전체 교육강좌",
        branch="동대문구 예약포털",
        url=url,
        source="test",
        priority=2,
        region="서울특별시",
        extra={},
    )


def _course(
    lecture_id: int,
    source_status: str,
    *,
    institution: str = "교육정책과",
    capacity_total: int = 20,
    waitlist_total: int = 5,
) -> dict[str, Any]:
    return {
        "id": str(lecture_id),
        "title": f"동대문 공개강좌 {lecture_id}",
        "source_status": source_status,
        "institution": institution,
        "venue": f"{institution} 강의실",
        "capacity_current": lecture_id % 4,
        "capacity_total": capacity_total,
        "waitlist_current": lecture_id % 2,
        "waitlist_total": waitlist_total,
    }


def _list_page(courses: list[dict[str, Any]], page_index: int) -> BeautifulSoup:
    total = len(courses)
    page_count = max(1, math.ceil(total / municipal.DONGDAEMUN_EDUCATION_PAGE_SIZE))
    start = (page_index - 1) * municipal.DONGDAEMUN_EDUCATION_PAGE_SIZE
    page_courses = courses[start : start + municipal.DONGDAEMUN_EDUCATION_PAGE_SIZE]
    rows: list[str] = []
    for offset, course in enumerate(page_courses):
        ordinal = total - start - offset
        rows.append(
            "<tr>"
            f"<td>{ordinal}</td>"
            f"<td><a href='./selectDongdaemunUserCourseView.do?key=1529&lctreRcritKey={course['id']}'>{course['title']}</a></td>"
            f"<td>{course['institution']}</td>"
            f"<td>{course['venue']}</td>"
            "<td>2099-01-01 ~ 2099-01-31 2099-02-01 ~ 2099-03-31</td>"
            "<td>월 수 10:00 ~ 12:00</td>"
            "<td>선착순</td>"
            f"<td>{course['capacity_current']} / {course['capacity_total']} "
            f"( {course['waitlist_current']} / {course['waitlist_total']} )</td>"
            f"<td>{course['source_status']}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='9'>등록된 교육강좌가 없습니다.</td></tr>")
    return _soup(
        "<html><body>"
        f"<div class='board_info'>총 게시물 : {total} 건 페이지 : {page_index}/{page_count}</div>"
        f"<table><tbody>{''.join(rows)}</tbody></table>"
        "</body></html>"
    )


def _detail(
    course: dict[str, Any],
    *,
    include_application: bool = True,
    capacity_total_delta: int = 0,
) -> BeautifulSoup:
    pairs = [
        ("접수기간", "2099-01-01 09:00~2099-01-31 18:00"),
        (
            "접수현황",
            f"신청 {course['capacity_current']} 명 / 모집정원 "
            f"{course['capacity_total'] + capacity_total_delta} 명 "
            f"(대기신청 {course['waitlist_current']} 명/ 대기정원 {course['waitlist_total']} 명)",
        ),
        ("선발방법", "선착순"),
        ("신청방법", "인터넷"),
        ("교육대상", "동대문구민"),
        ("교육기간", "2099-02-01 ~ 2099-03-31"),
        ("교육시간", "월 수 / 10:00 ~ 12:00"),
        ("교육장", course["venue"]),
        (
            "교육장 주소",
            f"(02565) 서울특별시 동대문구 테스트로 1 {course['venue']}",
        ),
        ("강사명", "동대문 강사"),
        ("수강료", "무료"),
        ("문의전화", "02-2127-0000"),
        ("강좌소개", f"{course['title']} 상세 소개"),
    ]
    rows = "".join(f"<tr><th>{label}</th><td>{value}</td></tr>" for label, value in pairs)
    application = (
        "<a class='p-button default' "
        f"href='./addDongdaemunUserCourseRegistView.do?key=1529&lctreRcritKey={course['id']}'>확인</a>"
        if include_application
        else ""
    )
    return _soup(
        "<html><body>"
        "<div class='bbs_view_title'>"
        f"<span class='view_tit'>{course['title']}</span>"
        f"<span class='state'>{course['source_status']}</span>"
        f"<span class='apply'>{application}</span></div>"
        f"<table>{rows}</table>"
        "</body></html>"
    )


def _fixture_pages() -> tuple[
    dict[tuple[str, int], BeautifulSoup],
    dict[str, dict[str, Any]],
]:
    state_courses = {
        "TBCCPT": [
            _course(1100 + index, "접수예정", institution=f"교육기관 {index % 3}")
            for index in range(17)
        ],
        "ACCPT": [
            _course(2100 + index, "접수중", institution="스마트도시과")
            for index in range(2)
        ],
        "WRCPT": [_course(3100, "대기자접수", institution="이문2동")],
    }
    pages: dict[tuple[str, int], BeautifulSoup] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for state_code, courses in state_courses.items():
        by_id.update({course["id"]: course for course in courses})
        page_count = max(1, math.ceil(len(courses) / municipal.DONGDAEMUN_EDUCATION_PAGE_SIZE))
        for page_index in range(1, page_count + 1):
            pages[(state_code, page_index)] = _list_page(courses, page_index)
    return pages, by_id


def _install_fetcher(
    monkeypatch: pytest.MonkeyPatch,
    pages: dict[tuple[str, int], BeautifulSoup],
    by_id: dict[str, dict[str, Any]],
    *,
    detail_factory: Callable[[dict[str, Any]], BeautifulSoup] | None = None,
) -> tuple[list[tuple[str, int]], list[str]]:
    lists: list[tuple[str, int]] = []
    details: list[str] = []
    monkeypatch.setattr(municipal, "session", lambda: object())

    def fetch_soup(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        parsed = urlparse(url)
        assert parsed.netloc == municipal.DONGDAEMUN_EDUCATION_HOST
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == municipal.DONGDAEMUN_EDUCATION_LIST_PATH:
            assert query["key"] == [municipal.DONGDAEMUN_EDUCATION_MENU_KEY]
            assert query["pageUnit"] == [str(municipal.DONGDAEMUN_EDUCATION_PAGE_SIZE)]
            assert query["searchCnd"] == ["all"]
            assert query["searchEduInstSe"] == [""]
            assert query["searchEdcKey"] == [""]
            state_code = query["receptionStts"][0]
            page_index = int(query["pageIndex"][0])
            lists.append((state_code, page_index))
            return pages[(state_code, page_index)]
        assert parsed.path == municipal.DONGDAEMUN_EDUCATION_DETAIL_PATH
        lecture_id = query["lctreRcritKey"][0]
        assert query["key"] == [municipal.DONGDAEMUN_EDUCATION_MENU_KEY]
        details.append(lecture_id)
        return (detail_factory or _detail)(by_id[lecture_id])

    monkeypatch.setattr(municipal, "fetch_soup", fetch_soup)
    return lists, details


def test_dongdaemun_active_states_full_snapshot_and_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages, by_id = _fixture_pages()
    lists, details = _install_fetcher(monkeypatch, pages, by_id)

    rows, parser, meta = municipal.collect_from_url(
        _target(),
        timeout=7,
        max_depth=0,
        max_pages=50,
        detail_limit=500,
    )

    assert parser == "dongdaemun_active_course_states+detail"
    assert len(rows) == 20
    assert Counter(row["status"] for row in rows) == {"SCHEDULED": 17, "OPEN": 3}
    assert meta["pages"] == 4
    assert meta["detail_pages"] == meta["detail_attempts"] == 20
    assert meta["declared_totals_by_status"] == {
        "TBCCPT": 17,
        "ACCPT": 2,
        "WRCPT": 1,
    }
    assert meta["status_pages"] == {"TBCCPT": 2, "ACCPT": 1, "WRCPT": 1}
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert "configured_collection_error" not in meta
    assert len(lists) == 4
    assert len(details) == 20
    assert len({row["provider_course_id"] for row in rows}) == 20
    assert len({row["raw_url"] for row in rows}) == 20
    assert len({row["application_url"] for row in rows}) == 20

    for row in rows:
        lecture_id = row["raw_fields"]["lecture_id"]
        assert row["provider_course_id"] == f"{PROVIDER}:lecture:{lecture_id}"
        assert row["prefer_incoming_provider_course_id"] is True
        assert row["branch"].startswith(("교육기관", "스마트도시과", "이문2동"))
        assert row["venue_name"].endswith("강의실")
        assert row["venue_address"] == "서울특별시 동대문구 테스트로 1"
        assert row["period"] == "2099-02-01 ~ 2099-03-31"
        assert row["apply_period"] == "2099-01-01 09:00 ~ 2099-01-31 18:00"
        assert row["application_url"].endswith(
            f"addDongdaemunUserCourseRegistView.do?key=1529&lctreRcritKey={lecture_id}"
        )
        assert row["reservation_available"] is (row["status"] == "OPEN")
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"


def test_dongdaemun_accpt_reconciles_closed_rows_exposed_by_official_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = [_course(5000 + index, "접수중") for index in range(4)]
    closed = [_course(6000 + index, "접수마감") for index in range(6)]
    accpt_rows = opened + closed
    pages = {
        ("TBCCPT", 1): _list_page([], 1),
        ("ACCPT", 1): _list_page(accpt_rows, 1),
        ("WRCPT", 1): _list_page([], 1),
    }
    by_id = {course["id"]: course for course in accpt_rows}
    _lists, details = _install_fetcher(monkeypatch, pages, by_id)

    rows, _parser, meta = municipal.collect_dongdaemun_education(
        _target(), timeout=5, max_pages=50, detail_limit=500
    )

    assert {row["raw_fields"]["lecture_id"] for row in rows} == {
        course["id"] for course in opened
    }
    assert details == [course["id"] for course in opened]
    assert meta["declared_totals_by_status"]["ACCPT"] == 10
    assert meta["source_rows_by_status"]["ACCPT"] == 10
    assert meta["selected_rows_by_status"]["ACCPT"] == 4
    assert meta["excluded_closed_rows_by_status"]["ACCPT"] == 6
    assert meta["source_row_count"] == 10
    assert meta["current_count"] == 4
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta


def test_dongdaemun_duplicate_official_id_across_states_blocks_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled = _course(4100, "접수예정")
    opened = {**scheduled, "source_status": "접수중"}
    pages = {
        ("TBCCPT", 1): _list_page([scheduled], 1),
        ("ACCPT", 1): _list_page([opened], 1),
        ("WRCPT", 1): _list_page([], 1),
    }
    _install_fetcher(monkeypatch, pages, {scheduled["id"]: scheduled})

    rows, _parser, meta = municipal.collect_dongdaemun_education(
        _target(), timeout=5, max_pages=50, detail_limit=20
    )

    assert len(rows) == 1
    assert meta["snapshot_complete"] is False
    assert meta["pagination_complete"] is False
    assert "duplicate lecture IDs" in meta["configured_collection_error"]
    assert "declared total 1 does not match 0 unique lectures" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "error_token"),
    [
        (1, 500, "max_pages cap"),
        (50, 1, "detail_limit cap"),
    ],
)
def test_dongdaemun_full_snapshot_caps_block_persistence_contract(
    monkeypatch: pytest.MonkeyPatch,
    max_pages: int,
    detail_limit: int,
    error_token: str,
) -> None:
    pages, by_id = _fixture_pages()
    _install_fetcher(monkeypatch, pages, by_id)

    rows, _parser, meta = municipal.collect_dongdaemun_education(
        _target(), timeout=5, max_pages=max_pages, detail_limit=detail_limit
    )

    assert rows
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert error_token in meta["configured_collection_error"]


def test_dongdaemun_missing_application_endpoint_blocks_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages, by_id = _fixture_pages()

    def detail(course: dict[str, Any]) -> BeautifulSoup:
        return _detail(course, include_application=course["id"] != "1100")

    _install_fetcher(monkeypatch, pages, by_id, detail_factory=detail)
    rows, _parser, meta = municipal.collect_dongdaemun_education(
        _target(), timeout=5, max_pages=50, detail_limit=500
    )

    assert len(rows) == 20
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "application endpoint missing" in meta["configured_collection_error"]


def test_dongdaemun_detail_capacity_mismatch_blocks_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages, by_id = _fixture_pages()

    def detail(course: dict[str, Any]) -> BeautifulSoup:
        return _detail(course, capacity_total_delta=1 if course["id"] == "1100" else 0)

    _install_fetcher(monkeypatch, pages, by_id, detail_factory=detail)
    rows, _parser, meta = municipal.collect_dongdaemun_education(
        _target(), timeout=5, max_pages=50, detail_limit=500
    )

    assert len(rows) == 20
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "detail/list capacity mismatch" in meta["configured_collection_error"]


def test_dongdaemun_configured_error_blocks_database_and_stale_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {"provider": PROVIDER, "title": "불완전 스냅샷", "branch": "동대문구"}
    meta = {
        "pages": 1,
        "detail_pages": 0,
        "pagination_detected": True,
        "pagination_complete": False,
        "configured_collection_error": "declared total mismatch",
    }
    monkeypatch.setattr(municipal, "load_targets", lambda *_args, **_kwargs: [_target()])
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_args, **_kwargs: ([row], "dongdaemun-test", meta),
    )
    monkeypatch.setattr(
        municipal,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be opened")),
    )
    monkeypatch.setattr(
        municipal,
        "mark_stale_courses",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale must not run")),
    )

    reports = municipal.run(
        source="registry",
        target_limit=None,
        per_target_limit=0,
        min_score=0,
        include_review=False,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=50,
        detail_limit=500,
        timeout=5,
    )

    assert reports[0].success is True
    assert reports[0].saved == 0
    assert reports[0].configured_collection_error == "declared total mismatch"


def test_dongdaemun_config_is_canonical_and_operationally_promoted() -> None:
    lifelong = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(encoding="utf-8")
    )
    targets = lifelong["targets"]
    canonical_rows = [row for row in targets if row.get("provider") == PROVIDER]
    assert len(canonical_rows) == 1
    canonical = canonical_rows[0]
    assert canonical["url"] == LIST_URL
    assert canonical["crawler_status"] == "ready"
    assert canonical["collection_type"] == "active_status_filters+detail_html"
    assert canonical["collection_category"] == "공공예약"
    assert canonical["domain_category"] == "교육·강좌"
    assert canonical["source_group"] == "municipal_reservation"
    assert canonical["service_group"] == "공공강좌"
    assert canonical["service_group_policy"] == "locked"
    assert canonical["full_snapshot_required"] is True
    assert canonical["municipality_code"] == "1123000000"
    assert canonical["origin"] == "live_validated"

    legacy = next(row for row in targets if row.get("provider") == LEGACY_PROVIDER)
    assert legacy["collection_type"] == "duplicate"
    assert legacy["duplicate_of"] == PROVIDER
    assert legacy["superseded_by"] == PROVIDER

    arguments = list(generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[PROVIDER])
    assert arguments == [
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "50",
        "--detail-limit",
        "500",
    ]
    parsed = generated.parse_args(["--provider", PROVIDER, *arguments])
    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.per_target_limit == 0
    assert parsed.allow_partial_save is False

    registry = yaml.safe_load(
        (ROOT / "config" / "generated_yaml_crawler_registry.yaml").read_text(encoding="utf-8")
    )
    registry_providers = {row["provider"] for row in registry["targets"]}
    assert PROVIDER not in registry_providers
    assert LEGACY_PROVIDER not in registry_providers

    production = yaml.safe_load(
        (ROOT / "config" / "production_crawler_providers.yaml").read_text(encoding="utf-8")
    )
    assert PROVIDER not in production["providers"]

    coverage = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )
    municipality = next(row for row in coverage["municipalities"] if row["code"] == "1123000000")
    assert municipality["status"] == "promoted"
    assert municipality["candidate_count"] == 12
    assert municipality["eligible_candidate_count"] == 6
    assert municipality["owner_providers"] == [PROVIDER]
    assert municipality["promoted_providers"] == [PROVIDER]
    assert municipality["yaml_owner_providers"] == [PROVIDER]
    assert "MUNI_IR_AC5AD74FE1DF" in municipality["review_candidate_ids"]

    aggregate = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "municipal_integrated_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert PROVIDER not in {row["provider"] for row in aggregate["targets"]}

    operational = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    operational_rows = [row for row in operational["entries"] if row["provider"] == PROVIDER]
    assert len(operational_rows) == 1
    assert operational_rows[0]["action"] == "schedule_existing"
    assert operational_rows[0]["validation_outcome"] == "collected"
    assert operational_rows[0]["parser"] == "dongdaemun_active_course_states+detail"
    assert operational_rows[0]["row_count"] == 34
    assert operational_rows[0]["no_current_data"] is False
    assert operational_rows[0]["municipalities"] == [
        {
            "code": "1123000000",
            "sido": "서울특별시",
            "sigungu": "동대문구",
            "full_name": "서울특별시 동대문구",
        }
    ]

    overrides = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_overrides.yaml").read_text(
            encoding="utf-8"
        )
    )
    override = next(row for row in overrides["municipalities"] if row["code"] == "1123000000")
    assert override["candidates"] == [
        {
            "status": "candidate",
            "score": 100,
            "title": "동대문구 예약포털 전체 교육강좌",
            "url": LIST_URL,
            "evidence_urls": [
                LIST_URL,
                (
                    "https://www.ddm.go.kr/reserve/selectDongdaemunUserCourseView.do?"
                    "key=1529&lctreRcritKey=7200"
                ),
            ],
            "evidence_note": (
                "2026-07-18 공식 전체프로그램의 접수예정 34건을 상태별 전 페이지와 "
                "상세 34건으로 검증했으며, 안정적인 lctreRcritKey와 강좌별 공식 "
                "신청 endpoint를 확인했다."
            ),
        }
    ]

    review = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_promotion_review.yaml").read_text(
            encoding="utf-8"
        )
    )
    candidate = next(
        row for row in review["candidates"] if row["candidate_id"] == "MUNI_IR_AC5AD74FE1DF"
    )
    assert candidate["provider"] == PROVIDER
    assert candidate["status"] == "promoted"
    assert candidate["manual_override"] is True
    assert candidate["live_validation"]["row_count"] == 34
    assert candidate["live_validation"]["semantic_quality_passed"] is True
