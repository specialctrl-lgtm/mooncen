from __future__ import annotations

from datetime import date
import html
import math
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import municipal_national_sejong_library as national


TOTAL = 188
CURRENT_ID = "106243"
ROOT = Path(__file__).resolve().parents[1]


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target(**updates: str) -> dict[str, str]:
    value = {
        "provider": national.NATIONAL_SEJONG_LIBRARY_PROVIDER,
        "url": national.NATIONAL_SEJONG_LIBRARY_CANONICAL_URL,
    }
    value.update(updates)
    return value


def _specs() -> list[dict[str, str]]:
    current = {
        "identity": CURRENT_ID,
        "title": "[도서관 잇다 4차]공간은 어떻게 삶이 되는가?",
        "application_status": "대기자 접수중",
        "education_status": "교육전",
        "period": "2026-08-12 ~ 2026-08-12",
        "apply_period": "2026-07-22 10:00 ~ 2026-08-10 00:00",
        "capacity": "100/100 (6/20)",
    }
    expired = [
        {
            "identity": str(105_000 - index),
            "title": f"지난 특별프로그램 {index + 1}",
            "application_status": "신청 마감",
            "education_status": "교육종료",
            "period": "2025-01-01 ~ 2025-01-01",
            "apply_period": "2024-12-01 10:00 ~ 2024-12-31 18:00",
            "capacity": "10/20 (0/5)",
        }
        for index in range(TOTAL - 1)
    ]
    return [current, *expired]


def _list_query(page: int) -> tuple[tuple[str, str], ...]:
    return (
        ("codeId", "PRO043"),
        ("menuId", "O365"),
        ("upperMenuId", "O300"),
        ("sel", "O360"),
        ("pageIndex", str(page)),
        ("searchCondition", "2"),
        ("searchKeyword", ""),
        ("etc1", ""),
        ("progrmId", ""),
    )


def _programme_row(spec: dict[str, str], ordinal: int) -> str:
    return f"""
    <tr>
      <td>{ordinal}</td>
      <td><span>{spec['application_status']}</span></td>
      <td>{spec['education_status']}</td>
      <td class="taL"><a href="javascript:fn_egov_view('{spec['identity']}')">{html.escape(spec['title'])}</a></td>
      <td>출력하면 안 되는 강사명</td>
      <td>{spec['period'].replace(' ~ ', '<br>~')}</td>
      <td>{spec['apply_period'].replace(' ~ ', '<br>~')}</td>
      <td>{spec['capacity'].replace(' (', '<br>(')}</td>
    </tr>
    """


def _paginator(page: int, pages: int, *, sentinel: bool) -> str:
    linked_page = pages if sentinel else 2 if page == 1 else max(1, page - 1)
    current = "" if sentinel else f'<a class="on" href="#">{page}</a>'
    return f"""
    <div class="paginate" id="paging">
      <a href="#" onclick="fn_egov_link_page({linked_page}); return false;">{linked_page}</a>
      {current}
      <input id="pageIndex" name="pageIndex" type="hidden" value="{page}">
    </div>
    """


def _list_page(
    specs: list[dict[str, str]],
    page: int,
    *,
    sentinel_row: bool = False,
    unstable_title: bool = False,
    notice_row: bool = False,
) -> str:
    pages = math.ceil(TOTAL / national.NATIONAL_SEJONG_LIBRARY_PAGE_SIZE)
    sentinel = page == pages + 1
    if sentinel:
        page_specs = specs[-1:] if sentinel_row else []
    else:
        start = (page - 1) * national.NATIONAL_SEJONG_LIBRARY_PAGE_SIZE
        page_specs = [dict(item) for item in specs[start : start + national.NATIONAL_SEJONG_LIBRARY_PAGE_SIZE]]
    if unstable_title and page_specs:
        page_specs[0]["title"] += " 변경"
    rows = "".join(
        _programme_row(spec, TOTAL - ((page - 1) * 10 + index))
        for index, spec in enumerate(page_specs)
    )
    if notice_row and not sentinel:
        rows = '<tr><td colspan="8"><a href="/notice/view.do?id=1">공지사항</a></td></tr>' + rows[rows.find("</tr>") + 5 :]
    displayed_end = pages if sentinel else min(math.ceil(page / 10) * 10, pages)
    query = urlencode(_list_query(page))
    action = html.escape(f"{national.NATIONAL_SEJONG_LIBRARY_LIST_PATH}?{query}", quote=True)
    headers = "".join(f"<th>{value}</th>" for value in national._LIST_HEADERS)
    return f"""
    <html><head><title>모든 대상 : 국립세종도서관</title></head><body>
      <form id="searchVO" name="listForm" method="post" action="{action}">
        <input name="menuId" value="O365"><input name="upperMenuId" value="O300">
        <input name="codeId" value="PRO043"><input name="etc1" value="">
        <input name="progrmId" value=""><input name="searchKeyword" value="">
        <select name="searchCondition"><option value="2">전체</option></select>
        <div class="board_tit"><div class="curpage">
          총 게시물 수 : {TOTAL} 현재 페이지 : {page} / {displayed_end}
        </div></div>
        <table class="board_table applyT">
          <caption>교육신청목록</caption><thead><tr>{headers}</tr></thead>
          <tbody>{rows}</tbody>
        </table>
        {_paginator(page, pages, sentinel=sentinel)}
      </form>
    </body></html>
    """


def _detail_page(
    spec: dict[str, str],
    *,
    missing_time: bool = False,
    wrong_identity: bool = False,
) -> str:
    identity = "999999" if wrong_identity else spec["identity"]
    time_cells = "" if missing_time else '<th class="bL">시간</th><td>10:00 ~ 11:30</td>'
    return f"""
    <html><head><title>모든 대상 : 국립세종도서관</title></head><body>
      <table class="boardView">
        <caption>교육 내용을 상세하게 작성한 표</caption><tbody>
          <tr><th>제목</th><td colspan="3">{html.escape(spec['title'])}</td></tr>
          <tr><th>강사</th><td>출력하면 안 되는 강사명</td><th class="bL">장소</th><td>대회의실(3층)</td></tr>
          <tr><th>정원</th><td>100 / 100</td><th class="bL">대기 정원</th><td>6 / 20</td></tr>
          <tr><th>기간</th><td>2026-08-12 ~ 2026-08-12</td>{time_cells}</tr>
          <tr><td colspan="4"><div class="viewbox">민감 문의 044-000-0000 private@example.test</div></td></tr>
          <tr><th>첨부파일</th><td colspan="3"><a href="/download/private.do?id=7">민감 강의계획서.pdf</a></td></tr>
        </tbody>
      </table>
      <form name="progrm" method="post" action="">
        <input name="progrmId" value="{identity}"><input name="partcptPsncpa" value="100">
        <input name="waitPsncpa" value="20"><input name="codeId" value="PRO043">
        <input name="menuId" value="O365"><input name="upperMenuId" value="O300">
        <input name="startDt" value="2026-07-22"><input name="endDt" value="2026-08-10">
      </form>
      <script>
        function apply() {{ return '/reqst/ProgrmAppDetailView.do'; }}
        function capacity() {{ return '/progrm/progrmAppTimeCon.do'; }}
      </script>
    </body></html>
    """


class FakeSession:
    def __init__(self, backend: "FakeBackend") -> None:
        self.backend = backend
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> BeautifulSoup:
        assert kwargs == {"timeout": 7, "allow_redirects": False, "verify": True}
        return self.backend.get(url)

    def close(self) -> None:
        self.closed = True


class FakeBackend:
    def __init__(
        self,
        *,
        sentinel_row: bool = False,
        unstable_first: bool = False,
        duplicate_identity: bool = False,
        missing_detail_time: bool = False,
        wrong_detail_identity: bool = False,
        notice_row: bool = False,
    ) -> None:
        self.specs = _specs()
        if duplicate_identity:
            self.specs[-1]["identity"] = self.specs[0]["identity"]
        self.sentinel_row = sentinel_row
        self.unstable_first = unstable_first
        self.missing_detail_time = missing_detail_time
        self.wrong_detail_identity = wrong_detail_identity
        self.notice_row = notice_row
        self.urls: list[str] = []
        self.page_calls: dict[int, int] = {}
        self.session = FakeSession(self)

    def get(self, url: str) -> BeautifulSoup:
        self.urls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == national.NATIONAL_SEJONG_LIBRARY_DETAIL_PATH:
            assert query == {
                "progrmId": [CURRENT_ID],
                "menuId": ["O365"],
                "upperMenuId": ["O300"],
                "codeId": ["PRO043"],
                "etc1": [""],
                "sel": ["O360"],
            }
            return _soup(
                _detail_page(
                    self.specs[0],
                    missing_time=self.missing_detail_time,
                    wrong_identity=self.wrong_detail_identity,
                )
            )
        assert parsed.path == national.NATIONAL_SEJONG_LIBRARY_LIST_PATH
        page = int(query["pageIndex"][0])
        assert query == {
            "codeId": ["PRO043"],
            "menuId": ["O365"],
            "upperMenuId": ["O300"],
            "sel": ["O360"],
            "pageIndex": [str(page)],
            "searchCondition": ["2"],
            "searchKeyword": [""],
            "etc1": [""],
            "progrmId": [""],
        }
        self.page_calls[page] = self.page_calls.get(page, 0) + 1
        return _soup(
            _list_page(
                self.specs,
                page,
                sentinel_row=self.sentinel_row,
                unstable_title=(
                    self.unstable_first and page == 1 and self.page_calls[page] > 1
                ),
                notice_row=self.notice_row and page == 1,
            )
        )


def _collect(
    backend: FakeBackend,
    *,
    max_pages: int = 30,
    detail_limit: int = 10,
    dedupe_rows: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, parser, meta = national.collect_national_sejong_library_courses(
        _target(),
        timeout=7,
        max_pages=max_pages,
        detail_limit=detail_limit,
        session_factory=lambda: backend.session,
        today=date(2026, 8, 6),
        dedupe_rows=dedupe_rows,
    )
    assert parser == national.NATIONAL_SEJONG_LIBRARY_PARSER
    return rows, meta


def test_exact_target_provider_and_reviewed_url_boundary() -> None:
    assert national.NATIONAL_SEJONG_LIBRARY_PROVIDER == "MUNI_SEJONG_NL_GO_KR_7F55E25D"
    assert national.NATIONAL_SEJONG_LIBRARY_CANDIDATE_ID == "MUNI_IR_97ABDAB9D017"
    assert national.is_national_sejong_library_target(_target())
    assert national.is_target(_target())
    assert not national.is_target(_target(provider="WRONG"))
    assert not national.is_target(
        _target(url=national.NATIONAL_SEJONG_LIBRARY_CANONICAL_URL + "&pageIndex=1")
    )
    assert not national.is_target(
        _target(url="https://sejong.nl.go.kr.evil.test/html/c3/c320.jsp?codeId=PRO043&menuId=O365&upperMenuId=O300&sel=O360")
    )


def test_complete_pages_empty_sentinel_stability_and_current_public_detail() -> None:
    from Crawler.Crawler_MunicipalYaml import MunicipalDbWriter

    backend = FakeBackend()
    rows, meta = _collect(backend)

    assert meta["snapshot_complete"] is True
    assert meta["source_rows"] == meta["declared_source_rows"] == TOTAL
    assert meta["source_pages"] == 19
    assert meta["required_list_requests"] == meta["list_requests"] == 22
    assert meta["empty_sentinel_verified"] is True
    assert meta["stable_boundaries"] is True
    assert meta["pagination_complete"] is True
    assert meta["current_count"] == meta["detail_pages"] == len(rows) == 1
    assert meta["expired_count"] == 187
    assert backend.page_calls == {**{page: 1 for page in range(2, 19)}, 1: 2, 19: 2, 20: 1}

    row = rows[0]
    assert row["provider_course_id"].endswith(f":program:{CURRENT_ID}")
    assert row["status"] == "OPEN"
    assert row["is_active"] is True
    assert row["period"] == "2026-08-12 ~ 2026-08-12"
    assert row["apply_period"] == "2026-07-22 10:00 ~ 2026-08-10 00:00"
    assert row["branch"] == row["venue_name"] == "국립세종도서관"
    assert row["address"] == "세종특별자치시 다솜3로 48"
    assert row["branch_lat"] == 36.4988247
    assert row["branch_lon"] == 127.2683884
    assert row["branch_coordinate_source"] == "KAKAO_LOCAL_ADDRESS"
    assert row["branch_location_confidence"] == 100
    assert row["branch_location_verified"] is True
    assert row["branch_location_query"] == row["address"]
    assert row["municipality_full_name"] == "세종특별자치시"
    assert row["municipality_region_verified"] is True
    assert row["region_sido"] == "세종특별자치시"
    assert row["region_sigungu"] == "세종특별자치시"
    saved_branch = MunicipalDbWriter(
        national.NATIONAL_SEJONG_LIBRARY_PROVIDER
    ).branch_info_from_row(row)
    assert saved_branch["address"] == row["address"]
    assert saved_branch["lat"] == 36.4988247
    assert saved_branch["lon"] == 127.2683884
    assert saved_branch["coordinate_source"] == "KAKAO_LOCAL_ADDRESS"
    assert saved_branch["location_verified"] is True
    assert saved_branch["region_sido"] == "세종특별자치시"
    assert saved_branch["region_sigungu"] == "세종특별자치시"
    assert row["room"] == "대회의실(3층)"
    assert row["program_type"] == "교육"
    assert row["service_group"] == "공공강좌"
    assert row["application_url"] == row["raw_url"]
    assert "/html/c3/c320_1.jsp?" in row["application_url"]
    assert row["raw_fields"]["application_endpoint_fetched"] is False

    assert all(urlparse(url).path in {national.NATIONAL_SEJONG_LIBRARY_LIST_PATH, national.NATIONAL_SEJONG_LIBRARY_DETAIL_PATH} for url in backend.urls)
    assert not any(
        marker in url.lower()
        for url in backend.urls
        for marker in ("/reqst/", "progrmapptimecon", "login.do", "download", "down.do")
    )
    payload = repr((rows, meta))
    for forbidden in (
        "출력하면 안 되는 강사명",
        "044-000-0000",
        "private@example.test",
        "민감 강의계획서.pdf",
        "/reqst/ProgrmAppDetailView.do",
        "/progrm/progrmAppTimeCon.do",
    ):
        assert forbidden not in payload
    assert backend.session.closed is True


@pytest.mark.parametrize(
    ("backend", "message"),
    [
        (FakeBackend(sentinel_row=True), "empty sentinel"),
        (FakeBackend(unstable_first=True), "first boundary changed"),
        (FakeBackend(duplicate_identity=True), "duplicate programme identities"),
        (FakeBackend(notice_row=True), "row shape changed"),
    ],
)
def test_catalogue_completeness_or_notice_drift_fails_closed(
    backend: FakeBackend,
    message: str,
) -> None:
    rows, meta = _collect(backend)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert message in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "backend",
    [
        FakeBackend(missing_detail_time=True),
        FakeBackend(wrong_detail_identity=True),
    ],
)
def test_every_current_detail_and_static_identity_are_atomic(backend: FakeBackend) -> None:
    rows, meta = _collect(backend)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_errors"] == 1
    assert f"detail {CURRENT_ID}" in meta["configured_collection_error"]


def test_transport_caps_and_downstream_dedupe_are_fail_closed() -> None:
    rows, _parser, meta = national.collect_national_sejong_library_courses(_target())
    assert rows == []
    assert "managed session_factory" in meta["configured_collection_error"]

    rows, meta = _collect(FakeBackend(), max_pages=20)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "required list requests=22" in meta["configured_collection_error"]

    rows, meta = _collect(FakeBackend(), detail_limit=0)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "below current rows=1" in meta["configured_collection_error"]

    rows, meta = _collect(FakeBackend(), dedupe_rows=lambda _rows: [])
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "downstream dedupe changed" in meta["configured_collection_error"]


def test_router_registry_schedule_and_national_owner_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as router
    from backend.ops.region_collection import _target_scopes

    library = yaml.safe_load(
        (ROOT / "config/crawl_targets/library.yaml").read_text(encoding="utf-8")
    )
    target = next(
        row
        for row in library["targets"]
        if row.get("provider") == national.NATIONAL_SEJONG_LIBRARY_PROVIDER
    )
    assert target["url"] == national.NATIONAL_SEJONG_LIBRARY_CANONICAL_URL
    assert target["crawler_module"] == "Crawler.municipal_national_sejong_library"
    assert target["full_snapshot_required"] is True
    assert target["max_pages"] >= 22
    assert target["service_group_policy"] == "locked"
    assert _target_scopes(target) == frozenset({"education"})

    production = yaml.safe_load(
        (ROOT / "config/production_crawler_providers.yaml").read_text(
            encoding="utf-8"
        )
    )["providers"]
    assert national.NATIONAL_SEJONG_LIBRARY_PROVIDER in production

    operational = yaml.safe_load(
        (
            ROOT
            / "config/municipal_integrated_reservation_operational.yaml"
        ).read_text(encoding="utf-8")
    )["entries"]
    assert all(
        row.get("provider") != national.NATIONAL_SEJONG_LIBRARY_PROVIDER
        for row in operational
    )

    coverage = yaml.safe_load(
        (
            ROOT
            / "config/municipal_integrated_reservation_coverage.yaml"
        ).read_text(encoding="utf-8")
    )["municipalities"]
    sejong = next(row for row in coverage if row["code"] == "3611000000")
    for field in ("owner_providers", "promoted_providers", "yaml_owner_providers"):
        assert national.NATIONAL_SEJONG_LIBRARY_PROVIDER not in sejong[field]
    exclusion = next(
        row
        for row in sejong["evidence"]
        if row.get("candidate_id") == national.NATIONAL_SEJONG_LIBRARY_CANDIDATE_ID
    )
    assert exclusion["exclusion_reason"] == "separate_national_institution_owner"

    registry = yaml.safe_load(
        (ROOT / "config/generated_yaml_crawler_registry.yaml").read_text(
            encoding="utf-8"
        )
    )["targets"]
    registered = next(
        row
        for row in registry
        if row.get("provider") == national.NATIONAL_SEJONG_LIBRARY_PROVIDER
    )
    assert registered["enabled"] is True
    assert registered["crawler"].endswith(
        f"/{national.NATIONAL_SEJONG_LIBRARY_PROVIDER}.py"
    )

    sentinel = (
        [{"provider_course_id": "sentinel"}],
        national.NATIONAL_SEJONG_LIBRARY_PARSER,
        {"snapshot_complete": True},
    )
    monkeypatch.setattr(
        national,
        "collect_national_sejong_library_courses",
        lambda *_args, **_kwargs: sentinel,
    )
    crawl_target = router.CrawlTarget(
        provider=national.NATIONAL_SEJONG_LIBRARY_PROVIDER,
        name="국립세종도서관",
        branch="국립세종도서관",
        url=national.NATIONAL_SEJONG_LIBRARY_CANONICAL_URL,
        source="test",
    )
    assert router.collect_from_url(
        crawl_target,
        timeout=3,
        max_depth=0,
        max_pages=30,
        detail_limit=2,
    ) == sentinel
