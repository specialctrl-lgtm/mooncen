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
import Crawler.Crawler_MunicipalIntegratedReservation as aggregate
import Crawler.Crawler_MunicipalYaml as municipal


ROOT = Path(__file__).resolve().parents[1]
PROVIDER = "MUNI_YEDU_YONGSAN_GO_KR_4E97CC33"
LEGACY_PROVIDER = "MUNI_YEDU_YONGSAN_GO_KR_36A48D5E"
LIST_URL = (
    "https://yedu.yongsan.go.kr/site/edtotal/lesson/userlist.do?"
    "sitecdv=S0000500&decorator=user27EdTotal&menucdv=02020000"
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _target(url: str = LIST_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=PROVIDER,
        name="용산구교육종합포털 전체 수강신청",
        branch="용산구교육종합포털",
        url=url,
        source="test",
    )


def _course(seq: int, status: str, *, education_type: str = "F0910105") -> dict[str, Any]:
    return {
        "seq": str(seq),
        "title": f"용산 공개강좌 {seq}",
        "education_type": education_type,
        "list_status": status,
        "branch": f"용산 교육기관 {seq % 3}",
        "capacity_current": seq % 7,
        "capacity_total": 20,
        "waitlist_total": 5,
    }


def _list_page(all_courses: list[dict[str, Any]], page_index: int) -> BeautifulSoup:
    total = len(all_courses)
    start = page_index * municipal.YONGSAN_EDUCATION_PAGE_SIZE
    page_courses = all_courses[start : start + municipal.YONGSAN_EDUCATION_PAGE_SIZE]
    rows: list[str] = []
    for offset, course in enumerate(page_courses):
        ordinal = total - start - offset
        rows.append(
            "<tr>"
            f"<td>{ordinal}</td>"
            f"<td><a href=\"javascript:goWrite({course['seq']},'{course['education_type']}')\">"
            f"{course['title']}</a></td>"
            "<td>2099.01.01 ~ 2099.01.31</td>"
            f"<td>{course['branch']}</td>"
            f"<td>{course['capacity_current']} / {course['capacity_total']} / "
            f"{course['waitlist_total']} 명</td>"
            "<td>무료</td>"
            f"<td>{course['list_status']}</td>"
            "</tr>"
        )
    page_count = max(1, math.ceil(total / municipal.YONGSAN_EDUCATION_PAGE_SIZE))
    pagination = "".join(
        f"<a href='javascript:void(0)' onclick='return doSearch({index})'>{index + 1}</a>"
        for index in range(page_count)
    )
    return _soup(
        f"<html><body><table><tbody>{''.join(rows)}</tbody></table>"
        f"<div class='pagination'>{pagination}</div></body></html>"
    )


def _detail(course: dict[str, Any], *, include_application: bool = True) -> BeautifulSoup:
    detail_status = "접수대기" if course["list_status"] == "모집예정" else "접수중"
    button = "<a id='btnSubmit' href='javascript:void(0)'>수강신청</a>" if include_application else ""
    pairs = [
        ("강좌명", course["title"]),
        ("교육장", course["branch"]),
        ("장소", f"{course['branch']} 강의실 (용산구 테스트로 1)"),
        ("접수기간", "2099.01.01~2099.01.31"),
        ("교육시간", "10:00~12:00"),
        ("교육기간", "2099.02.01~2099.03.31"),
        ("수업요일", "월수"),
        ("접수나이", "성인"),
        ("접수방법", "인터넷"),
        ("수강료", "무료"),
        ("정원", f"{course['capacity_total']}명"),
        ("접수상태", detail_status),
        ("강좌소개", f"{course['title']} 상세 소개"),
        ("강좌계획서", "주차별 교육 계획"),
        ("담당부서", "교육지원과(02-2199-6490)"),
        ("강사명", "용산 강사"),
    ]
    rows = "".join(f"<tr><th>{label}</th><td>{value}</td></tr>" for label, value in pairs)
    return _soup(f"<html><body><table>{rows}</table>{button}</body></html>")


def _fixture_pages() -> tuple[dict[tuple[str, int], BeautifulSoup], dict[str, dict[str, Any]]]:
    scheduled = [_course(1100 + index, "모집예정") for index in range(17)]
    opened = [_course(2100 + index, "모집중", education_type="F0910101") for index in range(2)]
    state_courses = {
        "E0820100": scheduled,
        "E0820110": opened,
    }
    pages: dict[tuple[str, int], BeautifulSoup] = {}
    by_seq: dict[str, dict[str, Any]] = {}
    for state_code, courses in state_courses.items():
        by_seq.update({course["seq"]: course for course in courses})
        for page_index in range(max(1, math.ceil(len(courses) / municipal.YONGSAN_EDUCATION_PAGE_SIZE))):
            pages[(state_code, page_index)] = _list_page(courses, page_index)
    return pages, by_seq


def _install_fetchers(
    monkeypatch: pytest.MonkeyPatch,
    pages: dict[tuple[str, int], BeautifulSoup],
    by_seq: dict[str, dict[str, Any]],
    *,
    detail_factory: Callable[[dict[str, Any]], BeautifulSoup] | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    posts: list[dict[str, str]] = []
    details: list[str] = []
    monkeypatch.setattr(municipal, "session", lambda: object())

    def post_soup(_session: object, url: str, data: dict[str, Any], timeout: int) -> BeautifulSoup:
        assert url == LIST_URL
        assert timeout > 0
        assert data["searchEdutypecdv"] == ""
        assert data["edutypecdv"] == ""
        payload = {str(key): str(value) for key, value in data.items()}
        posts.append(payload)
        return pages[(payload["searchState"], int(payload["currentPage"]))]

    def fetch_soup(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        parsed = urlparse(url)
        assert parsed.netloc == municipal.YONGSAN_EDUCATION_HOST
        assert parsed.path == municipal.YONGSAN_EDUCATION_DETAIL_PATH
        query = parse_qs(parsed.query)
        seq = query["lesseqn"][0]
        assert query["edutypecdv"] == [by_seq[seq]["education_type"]]
        details.append(seq)
        return (detail_factory or _detail)(by_seq[seq])

    monkeypatch.setattr(municipal, "post_soup", post_soup)
    monkeypatch.setattr(municipal, "fetch_soup", fetch_soup)
    return posts, details


def test_yongsan_state_filters_full_snapshot_and_details(monkeypatch: pytest.MonkeyPatch) -> None:
    pages, by_seq = _fixture_pages()
    posts, details = _install_fetchers(monkeypatch, pages, by_seq)

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=50, detail_limit=500
    )

    assert parser == "yongsan_education_state_filters+detail"
    assert len(rows) == 19
    assert Counter(row["status"] for row in rows) == {"SCHEDULED": 17, "OPEN": 2}
    assert meta["pages"] == 3
    assert meta["status_pages"] == {"E0820100": 2, "E0820110": 1}
    assert meta["declared_totals_by_status"] == {"E0820100": 17, "E0820110": 2}
    assert meta["detail_attempts"] == meta["detail_pages"] == 19
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert "configured_collection_error" not in meta
    assert len(posts) == 3
    assert len(details) == 19
    assert len({row["provider_course_id"] for row in rows}) == 19
    assert len({row["raw_url"] for row in rows}) == 19

    for row in rows:
        lesson_seq = row["raw_fields"]["lesson_seq"]
        assert row["provider_course_id"] == f"{PROVIDER}:lesson:{lesson_seq}"
        assert row["prefer_incoming_provider_course_id"] is True
        assert row["branch"].startswith("용산 교육기관")
        assert row["venue_name"].endswith("강의실 (용산구 테스트로 1)")
        assert row["venue_address"] == "용산구 테스트로 1"
        assert row["period"] == "2099-02-01 ~ 2099-03-31"
        assert row["apply_period"] == "2099-01-01 ~ 2099-01-31"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["application_url"] == row["raw_url"]
        assert row["reservation_available"] is (row["status"] == "OPEN")


def test_yongsan_duplicate_official_id_across_states_blocks_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = _course(3101, "모집예정")
    opened = {**shared, "list_status": "모집중"}
    pages = {
        ("E0820100", 0): _list_page([shared], 0),
        ("E0820110", 0): _list_page([opened], 0),
    }
    _install_fetchers(monkeypatch, pages, {shared["seq"]: shared})

    rows, _parser, meta = municipal.collect_yongsan_education(
        _target(), timeout=5, max_pages=50, detail_limit=10
    )

    assert len(rows) == 1
    assert meta["pagination_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "duplicate lesson_seq" in meta["configured_collection_error"]
    assert "declared total 1 does not match 0 unique lessons" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "error_token"),
    [
        (1, 500, "max_pages cap"),
        (50, 1, "detail_limit cap"),
    ],
)
def test_yongsan_full_snapshot_caps_block_persistence_contract(
    monkeypatch: pytest.MonkeyPatch,
    max_pages: int,
    detail_limit: int,
    error_token: str,
) -> None:
    pages, by_seq = _fixture_pages()
    _install_fetchers(monkeypatch, pages, by_seq)

    rows, _parser, meta = municipal.collect_yongsan_education(
        _target(), timeout=5, max_pages=max_pages, detail_limit=detail_limit
    )

    assert rows
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert error_token in meta["configured_collection_error"]


def test_yongsan_missing_application_control_blocks_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    pages, by_seq = _fixture_pages()

    def detail(course: dict[str, Any]) -> BeautifulSoup:
        return _detail(course, include_application=course["seq"] != "1100")

    _install_fetchers(monkeypatch, pages, by_seq, detail_factory=detail)
    rows, _parser, meta = municipal.collect_yongsan_education(
        _target(), timeout=5, max_pages=50, detail_limit=500
    )

    assert len(rows) == 19
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "application control missing" in meta["configured_collection_error"]


def test_yongsan_configured_error_blocks_database_and_stale_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {"provider": PROVIDER, "title": "불완전 스냅샷", "branch": "용산구"}
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
        lambda *_args, **_kwargs: ([row], "yongsan-test", meta),
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


def test_yongsan_config_uses_one_canonical_owner_through_the_aggregate() -> None:
    lifelong = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(encoding="utf-8")
    )
    targets = lifelong["targets"]
    canonical_rows = [row for row in targets if row.get("provider") == PROVIDER]
    assert len(canonical_rows) == 1
    canonical = canonical_rows[0]
    assert canonical["url"] == LIST_URL
    assert canonical["crawler_status"] == "ready"
    assert canonical["collection_type"] == "state_filters+detail_html"
    assert canonical["collection_category"] == "공공예약"
    assert canonical["domain_category"] == "교육·강좌"
    assert canonical["source_group"] == "municipal_reservation"
    assert canonical["service_group"] == "공공강좌"
    assert canonical["service_group_policy"] == "locked"
    assert canonical["full_snapshot_required"] is True
    assert canonical["municipality_code"] == "1117000000"
    assert canonical["origin"] == "manual"

    legacy = next(row for row in targets if row.get("provider") == LEGACY_PROVIDER)
    assert legacy["collection_type"] == "duplicate"
    assert legacy["duplicate_of"] == PROVIDER
    assert legacy["superseded_by"] == PROVIDER
    assert all(
        token not in row.get("url", "")
        for row in canonical_rows
        for token in ("searchEdutypecdv=", "/lifeStudy/", "/eachOther/", "/happyStudy/")
    )

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
    by_provider = {row["provider"]: row for row in registry["targets"]}
    assert PROVIDER not in by_provider
    assert PROVIDER in aggregate.municipal_provider_names()
    assert not (ROOT / "Crawler" / "generated_yaml" / f"{PROVIDER}.py").exists()
    assert LEGACY_PROVIDER not in by_provider

    production = yaml.safe_load(
        (ROOT / "config" / "production_crawler_providers.yaml").read_text(encoding="utf-8")
    )
    assert PROVIDER not in production["providers"]
    assert "MUNICIPAL_RESERVATION_TARGETS" in production["providers"]

    coverage = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_coverage.yaml").read_text(encoding="utf-8")
    )
    municipality = next(row for row in coverage["municipalities"] if row["code"] == "1117000000")
    assert municipality["status"] == "promoted"
    assert municipality["owner_providers"] == [PROVIDER]
    assert municipality["promoted_providers"] == [PROVIDER]
    assert "MUNI_IR_651E73F405C9" in municipality["review_candidate_ids"]

    aggregate_targets = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "municipal_integrated_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert PROVIDER not in {row["provider"] for row in aggregate_targets["targets"]}

    operational = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    operational_rows = [row for row in operational["entries"] if row["provider"] == PROVIDER]
    assert len(operational_rows) == 1
    assert operational_rows[0]["action"] == "schedule_existing"
    assert operational_rows[0]["validation_outcome"] == "collected"
    assert operational_rows[0]["row_count"] == 33
    assert operational_rows[0]["municipalities"] == [
        {
            "code": "1117000000",
            "sido": "서울특별시",
            "sigungu": "용산구",
            "full_name": "서울특별시 용산구",
        }
    ]

    overrides = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_overrides.yaml").read_text(
            encoding="utf-8"
        )
    )
    override = next(row for row in overrides["municipalities"] if row["code"] == "1117000000")
    assert override["candidates"] == [
        {
            "status": "candidate",
            "score": 100,
            "title": "용산구교육종합포털 전체 수강신청",
            "url": LIST_URL,
            "evidence_urls": [
                LIST_URL,
                (
                    "https://yedu.yongsan.go.kr/site/edtotal/lesson/form.do?"
                    "sitecdv=S0000500&decorator=user27EdTotal&menucdv=02020000&"
                    "edutypecdv=F0910105&lesseqn=6244"
                ),
            ],
            "evidence_note": (
                "2026-07-18 공식 전체 목록의 모집예정 28건과 모집중 5건을 상태 필터 "
                "전 페이지 및 상세 33건으로 검증했으며, 안정적인 lesseqn과 실제 "
                "수강신청 제어를 확인했다."
            ),
        }
    ]
