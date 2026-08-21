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
PROVIDER = "MUNI_WWW_MAPO_GO_KR_7852A077"
LIST_URL = "https://www.mapo.go.kr/site/mll/edu/lecture_list"
TODAY = "2099-01-01"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _target(url: str = LIST_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=PROVIDER,
        name="서울특별시 마포구 현재·미래 교육",
        branch="서울특별시 마포구 교육",
        url=url,
        source="test",
        priority=2,
        region="서울특별시 마포구",
        extra={},
    )


def _course(
    lecture_id: int,
    source_status: str,
    *,
    scope: str,
    branch: str = "평생학습센터",
    method: str = "인터넷, 방문",
    reversed_period: bool = False,
) -> dict[str, Any]:
    if scope == "ongoing":
        education_start, education_end = "2098-12-01", "2099-03-31"
    elif reversed_period:
        education_start, education_end = "2099-12-01", "2099-03-31"
    else:
        education_start, education_end = "2099-02-01", "2099-02-28"
    return {
        "id": str(lecture_id),
        "title": f"마포 공개강좌 {lecture_id}",
        "source_status": source_status,
        "scope": scope,
        "branch": branch,
        "venue": f"{branch} 테스트 강의실",
        "method": method,
        "apply_start": "2098-12-01",
        "apply_end": "2098-12-31",
        "education_start": education_start,
        "education_end": education_end,
        "reversed_period": reversed_period,
    }


def _short(value: str) -> str:
    return value[2:].replace("-", ".")


def _list_page(
    courses: list[dict[str, Any]],
    page_index: int,
    scope: str,
) -> BeautifulSoup:
    page_count = max(1, math.ceil(len(courses) / municipal.MAPO_EDUCATION_PAGE_SIZE))
    start = (page_index - 1) * municipal.MAPO_EDUCATION_PAGE_SIZE
    current = courses[start : start + municipal.MAPO_EDUCATION_PAGE_SIZE]
    rows: list[str] = []
    for offset, course in enumerate(current):
        ordinal = len(courses) - start - offset
        rows.append(
            "<tr>"
            f"<td>{ordinal}</td>"
            "<td class='tal_l_i'>"
            f"<a href='./lecture_view?ltSeq={course['id']}&cp={page_index}&pageSize=9&listType=list'>"
            f"{course['title']}</a></td>"
            f"<td>{_short(course['apply_start'])} ~ {_short(course['apply_end'])}<br>"
            f"{_short(course['education_start'])} ~ {_short(course['education_end'])}</td>"
            f"<td>{course['branch']}</td>"
            f"<td>{course['venue']}</td>"
            "<td>무료</td><td>선착순</td><td>20 / 5 / 3</td>"
            f"<td>{course['source_status']}</td><td></td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='10'>등록된 강좌가 없습니다.</td></tr>")
    headers = "".join(f"<th>{value}</th>" for value in municipal.MAPO_EDUCATION_HEADERS)
    paging = (
        "<div class='bbs_paging'>"
        f"<a class='now' title='현재 페이지' href='./lecture_list?cp={page_index}'>{page_index}</a>"
        f"<a title='마지막 페이지' href='./lecture_list?cp={page_count}'></a>"
        "</div>"
    )
    if scope == "future":
        filters = (
            "<select name='lecstate'><option value=''></option></select>"
            f"<input name='ltEduSday' value='{TODAY}'>"
            f"<input name='ltEduEday' value='{municipal.MAPO_EDUCATION_FUTURE_END}'>"
        )
    else:
        filters = (
            "<select name='lecstate'><option value=''></option>"
            "<option value='L0705' selected>교육중</option></select>"
            "<input name='ltEduSday' value=''><input name='ltEduEday' value=''>"
        )
    return _soup(
        "<html><body>"
        f"<form id='frm'>{filters}</form><p class='total-num'>총 <span>{len(courses)}</span>건</p>"
        "<div class='page-style page-style02 on'><div class='bbs_list'><table><tbody>"
        f"<tr>{headers}</tr>{''.join(rows)}"
        f"</tbody></table></div></div>{paging}</body></html>"
    )


def _detail(
    course: dict[str, Any],
    *,
    include_application: bool = True,
    branch_override: str = "",
) -> BeautifulSoup:
    branch = branch_override or course["branch"]
    pairs = [
        ("강좌분야", branch),
        ("교육대상", "마포구민"),
        ("교육장소", course["venue"]),
        ("강사명", "마포 강사"),
        (
            "접수기간",
            f"{course['apply_start']} 09:00 ~ {course['apply_end']} 18:00",
        ),
        (
            "교육기간",
            f"{course['education_start']} ~ {course['education_end']} 10 : 00 ~ 12 : 00",
        ),
        ("교육요일", "화요일, 목요일"),
        ("수강료", "무료"),
        ("신청/정원", "3 명 / 20 명"),
        ("신청/대기자", "1명/ 5 명"),
        ("접수방법", course["method"]),
        ("선정방법", "선착순"),
        ("문의처", "02-3153-0000"),
        ("첨부파일", ""),
    ]
    table_rows: list[str] = []
    for index in range(0, len(pairs), 2):
        chunk = pairs[index : index + 2]
        table_rows.append(
            "<tr>"
            + "".join(f"<th>{key}</th><td>{value}</td>" for key, value in chunk)
            + "</tr>"
        )
    button = ""
    if include_application and course["source_status"] in {"접수중", "오늘마감"} and "인터넷" in course["method"]:
        button = (
            f"<a class='btn_midium btn_emerald' href='./lecture_inscr_step01?ltSeq={course['id']}'>"
            "온라인접수</a>"
        )
    return _soup(
        "<html><body><div id='contents'>"
        "<div class='dt-page-title'>"
        f"<p class='text-btn cl-blue'>{course['source_status']}</p>"
        f"<p class='dpt-p'>{course['title']}</p></div>"
        "<div class='dpc-table'><table>"
        f"{''.join(table_rows)}</table></div><div class='tal_r'>{button}</div>"
        f"<div class='lesson-intro-cont'>{course['title']} 상세 소개</div>"
        "</div></body></html>"
    )


@pytest.fixture
def mapo_fixture() -> tuple[
    dict[tuple[str, int], BeautifulSoup],
    dict[str, dict[str, Any]],
]:
    future = [
        _course(8000, "접수중", scope="future"),
        _course(8001, "접수중", scope="future", method="전화"),
        *[
            _course(
                8000 + index,
                "접수예정",
                scope="future",
                branch=("보건교육" if index % 2 else "서강동"),
            )
            for index in range(2, 11)
        ],
        _course(8011, "접수예정", scope="future", reversed_period=True),
    ]
    ongoing = [
        _course(
            9000 + index,
            "교육중",
            scope="ongoing",
            branch=("생활체육교실" if index % 2 else "망원1동"),
        )
        for index in range(10)
    ]
    pages: dict[tuple[str, int], BeautifulSoup] = {}
    by_id = {course["id"]: course for course in [*future, *ongoing]}
    for scope, courses in (("future", future), ("ongoing", ongoing)):
        page_count = max(1, math.ceil(len(courses) / municipal.MAPO_EDUCATION_PAGE_SIZE))
        for page_index in range(1, page_count + 1):
            pages[(scope, page_index)] = _list_page(courses, page_index, scope)
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
    monkeypatch.setattr(municipal, "mapo_education_today", lambda: TODAY)
    monkeypatch.setattr(municipal, "session", lambda: object())

    def fetch_soup(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        parsed = urlparse(url)
        assert parsed.netloc == municipal.MAPO_EDUCATION_HOST
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == municipal.MAPO_EDUCATION_LIST_PATH:
            assert "listType" not in query
            assert "pageSize" not in query
            page_index = int(query["cp"][0])
            if "ltEduSday" in query:
                scope = "future"
                assert query == {
                    "ltEduSday": [TODAY],
                    "ltEduEday": [municipal.MAPO_EDUCATION_FUTURE_END],
                    "cp": [str(page_index)],
                }
            else:
                scope = "ongoing"
                assert query == {"lecstate": ["L0705"], "cp": [str(page_index)]}
            lists.append((scope, page_index))
            return pages[(scope, page_index)]
        assert parsed.path == municipal.MAPO_EDUCATION_DETAIL_PATH
        assert set(query) == {"ltSeq"}
        lecture_id = query["ltSeq"][0]
        details.append(lecture_id)
        return (detail_factory or _detail)(by_id[lecture_id])

    monkeypatch.setattr(municipal, "fetch_soup", fetch_soup)
    return lists, details


def test_mapo_current_future_full_pagination_details_and_branches(
    monkeypatch: pytest.MonkeyPatch,
    mapo_fixture: tuple[dict[tuple[str, int], BeautifulSoup], dict[str, dict[str, Any]]],
) -> None:
    pages, by_id = mapo_fixture
    lists, details = _install_fetcher(monkeypatch, pages, by_id)

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=50, detail_limit=500
    )

    assert parser == municipal.MAPO_EDUCATION_PARSER
    assert len(rows) == 21
    assert Counter(row["status"] for row in rows) == {
        "CLOSED": 10,
        "SCHEDULED": 9,
        "OPEN": 2,
    }
    assert meta["pages"] == 4
    assert meta["detail_pages"] == meta["detail_attempts"] == 21
    assert meta["declared_pages_by_scope"] == {"future": 2, "ongoing": 2}
    assert meta["declared_totals_by_scope"] == {"future": 12, "ongoing": 10}
    assert meta["invalid_period_count"] == 1
    assert meta["out_of_scope_count"] == 0
    assert meta["scope_complete"] == {"future": True, "ongoing": True}
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_cap_reached"] is False
    assert meta["reservation_discovery_links"] == 1
    assert "configured_collection_error" not in meta
    assert lists == [("future", 1), ("future", 2), ("ongoing", 1), ("ongoing", 2)]
    assert len(details) == 21
    assert {row["provider_course_id"] for row in rows} == {
        f"mapo:{row['raw_fields']['lecture_id']}" for row in rows
    }
    assert all(row["prefer_incoming_provider_course_id"] is True for row in rows)
    assert all(row["collection_category"] == "공공예약" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["municipality_code"] == "1144000000" for row in rows)
    assert {row["branch"] for row in rows} == {
        "평생학습센터",
        "보건교육",
        "서강동",
        "생활체육교실",
        "망원1동",
    }
    internet_open = next(row for row in rows if row["provider_course_id"] == "mapo:8000")
    phone_open = next(row for row in rows if row["provider_course_id"] == "mapo:8001")
    assert internet_open["application_url"].endswith("lecture_inscr_step01?ltSeq=8000")
    assert internet_open["reservation_available"] is True
    assert phone_open["application_type"] == "OFFLINE_APPLY"
    assert phone_open["reservation_available"] is False
    assert "application_url" not in phone_open
    assert all(row["raw_url"].endswith(row["raw_fields"]["lecture_id"]) for row in rows)


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "error_token"),
    [(1, 500, "max_pages cap"), (50, 1, "detail_limit cap")],
)
def test_mapo_snapshot_caps_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    mapo_fixture: tuple[dict[tuple[str, int], BeautifulSoup], dict[str, dict[str, Any]]],
    max_pages: int,
    detail_limit: int,
    error_token: str,
) -> None:
    pages, by_id = mapo_fixture
    _install_fetcher(monkeypatch, pages, by_id)

    rows, _parser, meta = municipal.collect_mapo_education(
        _target(), timeout=5, max_pages=max_pages, detail_limit=detail_limit
    )

    assert rows
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert error_token in meta["configured_collection_error"]


def test_mapo_open_internet_course_requires_official_application_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    mapo_fixture: tuple[dict[tuple[str, int], BeautifulSoup], dict[str, dict[str, Any]]],
) -> None:
    pages, by_id = mapo_fixture
    _install_fetcher(
        monkeypatch,
        pages,
        by_id,
        detail_factory=lambda course: _detail(course, include_application=False),
    )

    rows, _parser, meta = municipal.collect_mapo_education(
        _target(), timeout=5, max_pages=50, detail_limit=500
    )

    assert len(rows) == 21
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "online application endpoint missing" in meta["configured_collection_error"]


def test_mapo_detail_branch_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    mapo_fixture: tuple[dict[tuple[str, int], BeautifulSoup], dict[str, dict[str, Any]]],
) -> None:
    pages, by_id = mapo_fixture
    _install_fetcher(
        monkeypatch,
        pages,
        by_id,
        detail_factory=lambda course: _detail(
            course,
            branch_override=("다른 기관" if course["id"] == "8000" else ""),
        ),
    )

    _rows, _parser, meta = municipal.collect_mapo_education(
        _target(), timeout=5, max_pages=50, detail_limit=500
    )

    assert meta["snapshot_complete"] is False
    assert "branch mismatch" in meta["configured_collection_error"]


def test_mapo_target_is_canonical_locked_public_lecture() -> None:
    lifelong = yaml.safe_load(
        (ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(
            encoding="utf-8"
        )
    )
    canonical_rows = [row for row in lifelong["targets"] if row.get("provider") == PROVIDER]
    assert len(canonical_rows) == 1
    canonical = canonical_rows[0]
    assert canonical["url"] == LIST_URL
    assert canonical["crawler_status"] == "ready"
    assert canonical["collection_type"] == "current_future_date_status+detail_html"
    assert canonical["collection_category"] == "공공예약"
    assert canonical["domain_category"] == "교육·강좌"
    assert canonical["source_group"] == "municipal_reservation"
    assert canonical["service_group"] == "공공강좌"
    assert canonical["service_group_policy"] == "locked"
    assert canonical["full_snapshot_required"] is True
    assert canonical["municipality_code"] == "1144000000"
    assert canonical["origin"] == "live_validated"
    assert canonical["parser_assigned"] == municipal.MAPO_EDUCATION_PARSER

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

    historical_url = f"{LIST_URL}?cp=575&pageSize=9&listType=list"
    assert municipal.is_mapo_education_target(_target(historical_url)) is False
