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
PROVIDER = "GANGBUK_RESERVATION"
LEGACY_PROVIDER = "MUNI_WWW_GANGBUK_GO_KR_D185F30C"
LIST_URL = "https://office.gangbuk.go.kr/rsvt/cntedu/lctre/list.do?menuNo=800039"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _target(url: str = LIST_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=PROVIDER,
        name="강북구 통합예약 전체 강좌/교육",
        branch="강북구 통합예약",
        url=url,
        source="test",
        priority=2,
        region="서울특별시 강북구",
        extra={},
    )


def _course(
    lecture_id: int,
    source_status: str,
    *,
    branch: str = "평생학습관",
    target: str = "초등, 성인",
) -> dict[str, Any]:
    scheduled = source_status == "접수대기"
    return {
        "id": str(lecture_id),
        "title": f"강북 공개강좌 {lecture_id}",
        "source_status": source_status,
        "branch": branch,
        "target": target,
        "category": "디지털교육",
        "venue": f"{branch} 테스트 강의실",
        "capacity_current": 0 if scheduled else lecture_id % 5,
        "capacity_total": 20,
        "waitlist_current": 0 if scheduled else lecture_id % 2,
        "waitlist_total": 5,
        "scheduled": scheduled,
    }


def _list_page(courses: list[dict[str, Any]], page_index: int, state_code: str) -> BeautifulSoup:
    page_count = max(1, math.ceil(len(courses) / municipal.GANGBUK_EDUCATION_PAGE_SIZE))
    start = (page_index - 1) * municipal.GANGBUK_EDUCATION_PAGE_SIZE
    current = courses[start : start + municipal.GANGBUK_EDUCATION_PAGE_SIZE]
    rows: list[str] = []
    for course in current:
        capacity = (
            "0/0/(0)"
            if course["scheduled"]
            else (
                f"{course['capacity_total']}/{course['capacity_current']}"
                f"({course['waitlist_current']})"
            )
        )
        rows.append(
            "<tr>"
            "<td class='align-left'>"
            f"<a href='./view.do?lctreNo={course['id']}&menuNo=800039&pageUnit=100&pageIndex={page_index}'>"
            "<div class='flex-box mb5'>"
            f"<span class='badge badge--info'>{course['category']}</span>"
            "<span class='badge badge--info'>온라인 접수</span></div>"
            "<div class='flex-box align-center'>"
            "<span class='bage-circle1 large' aria-label=' OFF'><i>OFF</i></span>"
            f"<span class='sub-title black fb'>{course['title']}</span></div></a></td>"
            "<td>2099.02.01<br>~2099.02.28</td>"
            "<td>10:00~12:00<br>[월, 수]</td>"
            "<td class='align-left'><div>"
            f"<strong class='black'>대상 : {course['target']}</strong><br>"
            "수강료 : 무료<br>"
            "<ul class='badge-info-list'><li><span>2099.01.01~2099.01.31</span></li></ul>"
            "</div></td>"
            f"<td>{capacity}</td>"
            f"<td><a href='./view.do?lctreNo={course['id']}&menuNo=800039'>{course['source_status']}</a></td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='6'>등록된 강좌가 없습니다.</td></tr>")
    pagination = []
    for page in range(1, page_count + 1):
        if page == page_index:
            pagination.append(
                f"<li class='active'><span><em title='현재목록'><span>{page}</span></em></span></li>"
            )
        else:
            pagination.append(f"<li><a href='javascript:fnSearch({page})'>{page}</a></li>")
    headers = "".join(f"<th>{value}</th>" for value in municipal.GANGBUK_EDUCATION_HEADERS)
    return _soup(
        "<html><body>"
        "<form id='frm'><input name='menuNo' value='800039'>"
        f"<input name='searchStatus' value='{state_code}'></form>"
        "<div class='bd-list'><table>"
        f"<thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody>"
        "</table></div>"
        "<div class='paginationSet'><ul class='pagination'>"
        f"{''.join(pagination)}</ul></div>"
        "</body></html>"
    )


def _detail(
    course: dict[str, Any],
    *,
    include_application: bool = True,
    capacity_total_delta: int = 0,
) -> BeautifulSoup:
    targets = [value.strip() for value in course["target"].split(",") if value.strip()]
    target_badges = "".join(f"<span class='badge'>{value}</span>" for value in targets)
    detail_capacity = (
        "0/0/(0)"
        if course["scheduled"]
        else (
            f"{course['capacity_total']}/{course['capacity_current']}"
            f"({course['waitlist_current']})"
        )
    )
    pairs = [
        ("교육기관", course["branch"]),
        ("분야", course["category"]),
        ("접수기간", "일반 2099.01.01~2099.01.31"),
        ("교육기간", "2099.02.01~2099.02.28"),
        ("교육시간", "[월, 수] / 10:00~12:00"),
        ("교육장소", f"{course['branch']} / {course['venue']}"),
        ("주소", "서울 강북구 테스트로 1"),
        ("수강료", "무료"),
        ("재료비", "0원"),
        ("선발방법", "선착순"),
        ("접수방식", "온라인 접수"),
        (
            "모집인원",
            f"{course['capacity_total'] + capacity_total_delta} 명 / "
            f"대기 : {course['waitlist_total']} 명",
        ),
        ("신청인원(정원/신청(예비))", detail_capacity),
        ("강사명", "강북 강사"),
        ("강좌소개", f"{course['title']} 상세 소개"),
        ("문의전화", "02-901-0000"),
    ]
    groups = "".join(f"<dl><dt>{label}</dt><dd>{value}</dd></dl>" for label, value in pairs)
    button = (
        f"<a href='#' onclick=\"fnApply('{course['id']}');return false;\">신청하기</a>"
        if include_application and not course["scheduled"]
        else ""
    )
    return _soup(
        "<html><body><div id='contents'>"
        "<div class='box th-bg box-tag'>"
        f"<span class='tag'>{course['source_status']}</span>"
        "<div class='flex-box badge-title wrap'><span aria-label=' OFF'>OFF</span>"
        f"<span class='badge badge--green'>{course['branch']}</span>{target_badges}</div>"
        "<div class='sid-bar-contents'><span>"
        f"{course['category']}</span><span class='title-large'>{course['title']}</span></div>"
        "</div>"
        f"<div class='table-dl table-dl--line'>{groups}</div>"
        "<form name='item' action='./apply.do' method='get'>"
        "<input name='menuNo' value='800039'><input name='lctreNo' value=''></form>"
        f"{button}</div></body></html>"
    )


@pytest.fixture
def gangbuk_fixture() -> tuple[
    dict[tuple[str, int], BeautifulSoup],
    dict[str, dict[str, Any]],
]:
    state_courses = {
        "5": [
            _course(
                5000 + index,
                "접수중",
                branch=("평생학습관" if index % 2 else "강북구 보건소"),
            )
            for index in range(101)
        ],
        "3": [
            _course(6000 + index, "접수대기", branch="정보화 교육", target="강북구민")
            for index in range(2)
        ],
    }
    pages: dict[tuple[str, int], BeautifulSoup] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for state_code, courses in state_courses.items():
        by_id.update({course["id"]: course for course in courses})
        page_count = max(1, math.ceil(len(courses) / municipal.GANGBUK_EDUCATION_PAGE_SIZE))
        for page_index in range(1, page_count + 1):
            pages[(state_code, page_index)] = _list_page(courses, page_index, state_code)
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
        assert parsed.netloc == municipal.GANGBUK_EDUCATION_HOST
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == municipal.GANGBUK_EDUCATION_LIST_PATH:
            assert query["menuNo"] == [municipal.GANGBUK_EDUCATION_MENU_NO]
            assert query["pageUnit"] == [str(municipal.GANGBUK_EDUCATION_PAGE_SIZE)]
            state_code = query["searchStatus"][0]
            page_index = int(query["pageIndex"][0])
            lists.append((state_code, page_index))
            return pages[(state_code, page_index)]
        assert parsed.path == municipal.GANGBUK_EDUCATION_DETAIL_PATH
        lecture_id = query["lctreNo"][0]
        assert query["menuNo"] == [municipal.GANGBUK_EDUCATION_MENU_NO]
        details.append(lecture_id)
        return (detail_factory or _detail)(by_id[lecture_id])

    monkeypatch.setattr(municipal, "fetch_soup", fetch_soup)
    return lists, details


def test_gangbuk_current_states_full_pagination_details_and_branches(
    monkeypatch: pytest.MonkeyPatch,
    gangbuk_fixture: tuple[dict[tuple[str, int], BeautifulSoup], dict[str, dict[str, Any]]],
) -> None:
    pages, by_id = gangbuk_fixture
    lists, details = _install_fetcher(monkeypatch, pages, by_id)

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=50, detail_limit=500
    )

    assert parser == municipal.GANGBUK_EDUCATION_PARSER
    assert len(rows) == 103
    assert Counter(row["status"] for row in rows) == {"OPEN": 101, "SCHEDULED": 2}
    assert meta["pages"] == 3
    assert meta["detail_pages"] == meta["detail_attempts"] == 103
    assert meta["declared_pages_by_status"] == {"5": 2, "3": 1}
    assert meta["declared_totals_by_status"] == {"5": 101, "3": 2}
    assert meta["status_complete"] == {"5": True, "3": True}
    assert meta["reservation_discovery_links"] == 101
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert "configured_collection_error" not in meta
    assert lists == [("5", 1), ("5", 2), ("3", 1)]
    assert len(details) == 103
    assert len({row["provider_course_id"] for row in rows}) == 103
    assert len({row["raw_url"] for row in rows}) == 103
    assert len({row["application_url"] for row in rows if row.get("application_url")}) == 101

    scheduled = [row for row in rows if row["status"] == "SCHEDULED"]
    assert all(row["capacity_total"] == 20 for row in scheduled)
    assert all(row["waitlist_total"] == 5 for row in scheduled)
    assert all("application_url" not in row for row in scheduled)
    for row in rows:
        lecture_id = row["raw_fields"]["lecture_id"]
        assert row["provider_course_id"] == f"{PROVIDER}:lecture:{lecture_id}"
        assert row["prefer_incoming_provider_course_id"] is True
        assert row["branch"] in {"평생학습관", "강북구 보건소", "정보화 교육"}
        assert row["branch_url"].startswith(LIST_URL)
        assert row["venue_name"].endswith("테스트 강의실")
        assert row["period"] == "2099-02-01 ~ 2099-02-28"
        assert row["apply_period"] == "2099-01-01 ~ 2099-01-31"
        assert row["collection_category"] == "공공예약"
        assert row["domain_category"] == "교육·강좌"
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["municipality_code"] == "1130500000"
        assert row["reservation_available"] is (row["status"] == "OPEN")
        if row["status"] == "OPEN":
            assert row["application_url"].endswith(
                f"apply.do?menuNo=800039&lctreNo={lecture_id}"
            )


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "error_token"),
    [(1, 500, "max_pages cap"), (50, 1, "detail_limit cap")],
)
def test_gangbuk_snapshot_caps_block_persistence_contract(
    monkeypatch: pytest.MonkeyPatch,
    gangbuk_fixture: tuple[dict[tuple[str, int], BeautifulSoup], dict[str, dict[str, Any]]],
    max_pages: int,
    detail_limit: int,
    error_token: str,
) -> None:
    pages, by_id = gangbuk_fixture
    _install_fetcher(monkeypatch, pages, by_id)

    rows, _parser, meta = municipal.collect_gangbuk_education(
        _target(), timeout=5, max_pages=max_pages, detail_limit=detail_limit
    )

    assert rows
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert error_token in meta["configured_collection_error"]


def test_gangbuk_duplicate_official_id_across_states_blocks_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = _course(7000, "접수중")
    scheduled = {**opened, "source_status": "접수대기", "scheduled": True}
    pages = {
        ("5", 1): _list_page([opened], 1, "5"),
        ("3", 1): _list_page([scheduled], 1, "3"),
    }
    _install_fetcher(monkeypatch, pages, {opened["id"]: opened})

    rows, _parser, meta = municipal.collect_gangbuk_education(
        _target(), timeout=5, max_pages=50, detail_limit=20
    )

    assert len(rows) == 1
    assert meta["snapshot_complete"] is False
    assert "duplicate lecture IDs" in meta["configured_collection_error"]


def test_gangbuk_open_course_requires_official_application_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = _course(7100, "접수중")
    pages = {
        ("5", 1): _list_page([opened], 1, "5"),
        ("3", 1): _list_page([], 1, "3"),
    }
    _install_fetcher(
        monkeypatch,
        pages,
        {opened["id"]: opened},
        detail_factory=lambda course: _detail(course, include_application=False),
    )

    rows, _parser, meta = municipal.collect_gangbuk_education(
        _target(), timeout=5, max_pages=50, detail_limit=20
    )

    assert len(rows) == 1
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "application endpoint missing" in meta["configured_collection_error"]


def test_gangbuk_detail_capacity_mismatch_blocks_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = _course(7200, "접수중")
    pages = {
        ("5", 1): _list_page([opened], 1, "5"),
        ("3", 1): _list_page([], 1, "3"),
    }
    _install_fetcher(
        monkeypatch,
        pages,
        {opened["id"]: opened},
        detail_factory=lambda course: _detail(course, capacity_total_delta=1),
    )

    rows, _parser, meta = municipal.collect_gangbuk_education(
        _target(), timeout=5, max_pages=50, detail_limit=20
    )

    assert len(rows) == 1
    assert meta["details_complete"] is False
    assert "detail/list capacity mismatch" in meta["configured_collection_error"]


def test_gangbuk_configured_error_blocks_database_and_stale_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {"provider": PROVIDER, "title": "불완전 스냅샷", "branch": "강북구"}
    meta = {
        "pages": 1,
        "detail_pages": 0,
        "pagination_detected": True,
        "pagination_complete": False,
        "configured_collection_error": "terminal page mismatch",
    }
    monkeypatch.setattr(municipal, "load_targets", lambda *_args, **_kwargs: [_target()])
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_args, **_kwargs: ([row], "gangbuk-test", meta),
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
    assert reports[0].configured_collection_error == "terminal page mismatch"


def test_gangbuk_target_ownership_is_canonical_and_experience_is_separate() -> None:
    public = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    canonical_rows = [row for row in public["targets"] if row.get("provider") == PROVIDER]
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
    assert canonical["municipality_code"] == "1130500000"
    assert canonical["origin"] == "live_validated"

    lifelong = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(
            encoding="utf-8"
        )
    )
    legacy = next(row for row in lifelong["targets"] if row.get("provider") == LEGACY_PROVIDER)
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

    experience_url = (
        "https://office.gangbuk.go.kr/rsvt/exprn/exprn/"
        "selectDate.do?exprnSe=CDPMRSCT&menuNo=800043"
    )
    assert municipal.is_gangbuk_education_target(_target(experience_url)) is False
