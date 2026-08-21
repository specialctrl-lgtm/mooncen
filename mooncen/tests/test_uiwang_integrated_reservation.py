from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalYaml as municipal


PROVIDER = municipal.UIWANG_RESERVE_PROVIDER


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=PROVIDER,
        name="의왕시 통합예약",
        branch="경기도 의왕시",
        url=municipal.UIWANG_RESERVE_ROOT_URL,
        source="test",
        priority=1,
        region="경기도",
        extra={"per_target_limit": 0},
    )


def _main_page() -> str:
    return """
    <html><body>
      <a href="/reserve/EDU/E01/eduList.do?currentMenuNo=411">주민자치</a>
      <a href="/reserve/EDU/E11/eduList.do?currentMenuNo=624">고천동</a>
      <a href="/reserve/EDU/E11/eduList.do?currentMenuNo=624">고천동 중복 메뉴</a>
      <a href="/reserve/LIB/I04/eduList.do?currentMenuNo=635">글로벌도서관</a>
    </body></html>
    """


def _card(
    reservation_id: str,
    title: str,
    status: str,
    *,
    venue: str,
    period: str = "2099-08-01 ~ 2099-08-31",
) -> str:
    return f"""
    <li>
      <div class="addLabel"><span>{status}</span></div>
      <div class="txtW">
        <p class="tit">{title}</p>
        <ul class="etc">
          <li><span class="em">교육기간</span>{period}</li>
          <li><span class="em">접수기간</span>2099-07-01 ~ 2099-07-31</li>
          <li><span class="em">교육장소</span>{venue}</li>
          <li><span class="em">대상</span>의왕시민</li>
          <li><span class="em">사용료</span>무료</li>
        </ul>
      </div>
      <a href="#none" onclick="fnView('{reservation_id}')">상세</a>
    </li>
    """


def _list_page(cards: str, *, total: int, current: int, pages: int) -> str:
    return f"""
    <html><body>
      <ul><li>총게시물 <span class="em">{total}</span>건</li>
          <li>현재페이지 <span class="em">{current}</span>/{pages}</li></ul>
      <ul class="album reserv">{cards}</ul>
      <a href="#none" onclick="fnSearch({pages}); return false;">마지막</a>
    </body></html>
    """


def _detail_page(
    reservation_id: str,
    status: str,
    *,
    agency: str,
    venue: str,
    application: str = "button",
    location: str = "",
) -> str:
    if application == "button":
        action = '<a href="#none" onclick="fnResvRqst(\'N\');">예약신청</a>'
    elif application == "library":
        action = '<form action="https://www.uwlib.or.kr/sso/loginReserve.do" method="get"></form>'
    else:
        action = ""
    location_html = (
        f'<ul class="loca"><li><span class="em on">위치</span>{location}</li></ul>'
        if location
        else ""
    )
    return f"""
    <html><body>
      <div class="listInfoTop"><span class="label">{status}</span></div>
      <div class="listInfoBtm">
        <div class="imgSlide"><img src="/reserve/getResrImg.do;jsessionid=SECRET?atchFileId=FILE_{reservation_id}&amp;fileSn=1" /></div>
        <div class="infoArea"><ul class="itemList">
          <li><span class="em">유형</span><span class="txt">교육프로그램</span></li>
          <li><span class="em">교육기간</span><span class="txt">2099-08-01 ~ 2099-08-31</span></li>
          <li><span class="em">교육시간</span><span class="txt">10:00 - 12:00</span></li>
          <li><span class="em">교육요일</span><span class="txt">토</span></li>
          <li><span class="em">교육장소</span><span class="txt">{venue}</span></li>
          <li><span class="em">대상</span><span class="txt">의왕시민</span></li>
          <li><span class="em">사용료</span><span class="txt">무료</span></li>
          <li><span class="em">예약방식</span><span class="txt">인터넷</span></li>
          <li><span class="em">기관/부서</span><span class="txt">{agency}</span></li>
          <li><span class="em">모집정원</span><span class="txt">10</span></li>
          <li><span class="em">문의처</span><span class="txt">031-345-0000</span></li>
        </ul></div>
      </div>
      <div class="contDiv"><table><tbody>
        <tr><th>접수기간</th><th>선정방식</th><th>신청조건</th></tr>
        <tr><td>2099-07-01 09:00 ~ 2099-07-31 18:00</td><td>선착순</td><td>의왕시민</td></tr>
        <tr><th>예약현황</th><th>신청</th></tr>
        <tr><td>3 / 10</td><td>{action}</td></tr>
      </tbody></table></div>
      <div class="contWrap">상세 교육 안내 {reservation_id}</div>
      {location_html}
      {action}
    </body></html>
    """


def _install_fixture(monkeypatch, fetched: list[str]) -> None:
    e11_page_1 = _list_page(
        _card(
            "RESR_001",
            "고천 열린 강좌",
            "결원보충 교육중",
            venue="고천동주민센터 3층",
        ),
        total=2,
        current=1,
        pages=2,
    )
    e11_page_2 = _list_page(
        _card(
            "RESR_002",
            "고천 종료 강좌",
            "접수마감 교육종료",
            venue="고천동주민센터 4층",
        ),
        total=2,
        current=2,
        pages=2,
    )
    library_page = _list_page(
        _card(
            "RESR_001",
            "고천 열린 강좌",
            "결원보충 교육중",
            venue="고천동주민센터 3층",
        )
        + _card(
            "RESR_003",
            "도서관 예정 강좌",
            "접수예정 교육예정",
            venue="글로벌도서관 강의실",
        ),
        total=2,
        current=1,
        pages=1,
    )

    def fake_fetch(_session, url: str, timeout: int):
        assert timeout == 7
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if url == municipal.UIWANG_RESERVE_ROOT_URL:
            return _soup(_main_page())
        if parsed.path == municipal.UIWANG_RESERVE_AGGREGATE_PATH:
            raise AssertionError("aggregate category must never be fetched")
        if parsed.path.endswith("/eduList.do"):
            page = int((query.get("pageIndex") or ["1"])[0])
            if parsed.path == "/reserve/EDU/E11/eduList.do":
                return _soup(e11_page_1 if page == 1 else e11_page_2)
            if parsed.path == "/reserve/LIB/I04/eduList.do":
                assert page == 1
                return _soup(library_page)
        reservation_id = (query.get("resrId") or [""])[0]
        if reservation_id == "RESR_001":
            return _soup(
                _detail_page(
                    reservation_id,
                    "결원보충 교육중",
                    agency="고천동 주민자치센터",
                    venue="고천동주민센터 3층",
                )
            )
        if reservation_id == "RESR_002":
            return _soup(
                _detail_page(
                    reservation_id,
                    "접수마감 교육종료",
                    agency="고천동 주민자치센터",
                    venue="고천동주민센터 4층",
                    application="none",
                )
            )
        if reservation_id == "RESR_003":
            return _soup(
                _detail_page(
                    reservation_id,
                    "접수예정 교육예정",
                    agency="글로벌도서관",
                    venue="글로벌도서관 강의실",
                    application="library",
                    location="경기 의왕시 보식골로 30-10 글로벌도서관",
                )
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)


def test_uiwang_collects_all_detail_pages_with_stable_reservation_ids(monkeypatch) -> None:
    fetched: list[str] = []
    _install_fixture(monkeypatch, fetched)

    rows, parser, meta = municipal.collect_uiwang_reserve_categories(
        _target(), timeout=7, max_pages=10, detail_limit=10
    )

    assert parser == "uiwang_reserve_category_detail"
    assert [row["provider_course_id"] for row in rows] == [
        "uiwang:RESR_001",
        "uiwang:RESR_002",
        "uiwang:RESR_003",
    ]
    assert all(row["prefer_incoming_provider_course_id"] is True for row in rows)
    assert all("pageIndex" not in row["raw_url"] for row in rows)
    assert len({row["raw_url"] for row in rows}) == 3
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)
    assert all(row["source_group"] == "municipal_reservation" for row in rows)

    opened = rows[0]
    assert opened["status"] == "OPEN"
    assert opened["branch"] == "고천동 주민자치센터"
    assert opened["venue_name"] == "고천동주민센터 3층"
    assert opened["capacity_current"] == 3
    assert opened["capacity_total"] == 10
    assert opened["capacity_remaining"] == 7
    assert opened["reservation_available"] is True
    assert opened["application_url"] == opened["raw_url"]
    assert "fnResvRqst" in opened["raw_fields"]["application_action"]
    assert ";jsessionid" not in opened["image_url"]
    assert "상세 교육 안내" in opened["description"]

    assert rows[1]["status"] == "CLOSED"
    assert rows[1]["application_url"] == ""
    assert rows[1]["application_type"] == "INFO_ONLY"
    assert rows[2]["status"] == "SCHEDULED"
    assert rows[2]["branch"] == "글로벌도서관"
    assert rows[2]["address"] == "경기도 의왕시 보식골로 30-10"
    assert rows[2]["venue_address"] == rows[2]["address"]
    assert rows[2]["branch_location_verified"] is True
    assert rows[2]["raw_fields"]["location_text"].endswith("글로벌도서관")
    assert rows[2]["reservation_available"] is False
    assert "loginReserve.do" in rows[2]["raw_fields"]["application_action"]

    assert meta["pages"] == 3
    assert meta["detail_pages"] == 3
    assert meta["categories"] == 2
    assert meta["declared_pages"] == 3
    assert meta["declared_rows"] == 4
    assert meta["accounted_declared_rows"] == 4
    assert meta["duplicate_reservation_ids"] == 1
    assert meta["configured_collection_error"] == ""
    assert meta["list_pagination_complete"] is True
    assert meta["detail_collection_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["skipped_aggregate_path"] == municipal.UIWANG_RESERVE_AGGREGATE_PATH
    assert not any(municipal.UIWANG_RESERVE_AGGREGATE_PATH in url for url in fetched)


def test_uiwang_discovery_excludes_culture_and_experience_routes(monkeypatch) -> None:
    soup = _soup(
        """
        <a href="/reserve/EDU/E11/eduList.do?currentMenuNo=624">education</a>
        <a href="/reserve/FEV/V01/eduList.do?currentMenuNo=471">festival</a>
        <a href="/reserve/EHN/N01/eduList.do?currentMenuNo=472">performance</a>
        <a href="/reserve/EXP/X01/eduList.do?currentMenuNo=473">experience</a>
        """
    )
    monkeypatch.setattr(municipal, "fetch_soup", lambda *_args, **_kwargs: soup)

    categories = municipal.uiwang_discover_edu_categories(object(), timeout=7)

    assert [row["path"] for row in categories] == ["/reserve/EDU/E11/eduList.do"]


def test_uiwang_current_album_markup_and_total_declaration_are_supported() -> None:
    soup = _soup(
        """
        <div class="listTop"><p class="total">description 전체
          <span class="em">43</span> 건</p></div>
        <ul class="album reserv"><li>current card</li></ul>
        <a onclick="fnSearch(6); return false;">last</a>
        """
    )

    assert municipal.uiwang_page_declaration(soup) == (43, 6)


def test_uiwang_physical_location_normalizes_official_lot_address() -> None:
    branch, address = municipal.uiwang_physical_location(
        "경기 의왕시 오전동 236-14 글로벌도서관 1층 강당",
        "1층 강당",
        "도서관정책과",
    )

    assert branch == "글로벌도서관"
    assert address == "경기도 의왕시 보식골로 30-10"


def test_uiwang_course_location_keeps_department_for_flexible_venue() -> None:
    branch, branch_address, venue_address, physical = (
        municipal.uiwang_course_location(
            "평생교육과",
            "학습자가 선정",
            "의왕시평생학습관",
            "경기도 의왕시 오전로 122",
        )
    )

    assert branch == "평생교육과"
    assert branch_address == "경기도 의왕시 오전로 122"
    assert venue_address == ""
    assert physical is False


def test_uiwang_course_location_uses_explicit_lifelong_venue() -> None:
    branch, branch_address, venue_address, physical = (
        municipal.uiwang_course_location(
            "평생교육과",
            "청계동 주민센터 강의실",
            "평생교육과",
            "경기도 의왕시 오전로 122",
        )
    )

    assert branch == "청계동 주민센터"
    assert branch_address == "경기도 의왕시 안양판교로 232"
    assert venue_address == branch_address
    assert physical is True


def test_uiwang_course_location_does_not_assign_online_venue_address() -> None:
    branch, branch_address, venue_address, physical = (
        municipal.uiwang_course_location(
            "도서관정책과",
            "글로벌도서관(온라인)",
            "글로벌도서관",
            "경기도 의왕시 보식골로 30-10",
        )
    )

    assert branch == "글로벌도서관"
    assert branch_address == "경기도 의왕시 보식골로 30-10"
    assert venue_address == ""
    assert physical is True


def test_uiwang_page_cap_marks_collection_incomplete(monkeypatch) -> None:
    fetched: list[str] = []
    _install_fixture(monkeypatch, fetched)

    rows, _parser, meta = municipal.collect_uiwang_reserve_categories(
        _target(), timeout=7, max_pages=1, detail_limit=10
    )

    assert [row["provider_course_id"] for row in rows] == ["uiwang:RESR_001"]
    assert meta["pages"] == 1
    assert meta["pagination_complete"] is False
    assert meta["configured_collection_error"].startswith("max_pages cap reached")


def test_uiwang_detail_cap_marks_collection_incomplete(monkeypatch) -> None:
    fetched: list[str] = []
    _install_fixture(monkeypatch, fetched)

    rows, _parser, meta = municipal.collect_uiwang_reserve_categories(
        _target(), timeout=7, max_pages=10, detail_limit=2
    )

    assert len(rows) == 3
    assert meta["detail_pages"] == 2
    assert meta["detail_collection_complete"] is False
    assert meta["pagination_complete"] is False
    assert "detail_limit cap reached" in meta["configured_collection_error"]


def test_uiwang_dispatch_passes_detail_limit(monkeypatch) -> None:
    captured: dict[str, int] = {}

    def fake_collect(target, timeout: int, max_pages: int, detail_limit: int):
        assert target.provider == PROVIDER
        captured.update(timeout=timeout, max_pages=max_pages, detail_limit=detail_limit)
        return [], "uiwang_reserve_category_detail", {"no_current_data": True}

    monkeypatch.setattr(municipal, "collect_uiwang_reserve_categories", fake_collect)
    _rows, parser, _meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=250, detail_limit=2000
    )

    assert parser == "uiwang_reserve_category_detail"
    assert captured == {"timeout": 7, "max_pages": 250, "detail_limit": 2000}


def test_uiwang_target_ownership_and_generated_full_run_contract() -> None:
    public_document = yaml.safe_load(
        (municipal.ROOT / "config" / "crawl_targets" / "public_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    public_targets = {row["provider"]: row for row in public_document["targets"]}
    canonical = public_targets[PROVIDER]
    duplicate = public_targets["MUNI_WWW_UIWANG_GO_KR_F89FBD11"]
    assert canonical["service_group"] == "공공강좌"
    assert canonical["service_group_policy"] == "locked"
    assert canonical["domain_category"] == "교육·강좌"
    assert duplicate["collection_type"] == "duplicate"
    assert duplicate["crawler_status"] == f"duplicate_url:{PROVIDER}"
    assert duplicate["duplicate_of"] == PROVIDER
    assert duplicate["superseded_by"] == PROVIDER

    lifelong_document = yaml.safe_load(
        (municipal.ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(
            encoding="utf-8"
        )
    )
    lifelong_targets = {row["provider"]: row for row in lifelong_document["targets"]}
    for provider in (
        "MUNI_EDU_UIWANG_GO_KR_C58B7D3A",
        "MUNI_EDU_UIWANG_GO_KR_D8B94720",
    ):
        assert lifelong_targets[provider]["crawler_status"] == "no_current_data"
        assert lifelong_targets[provider]["blocked_reason"] == "retired_archive"
        assert lifelong_targets[provider]["superseded_by"] == PROVIDER

    arguments = list(generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[PROVIDER])
    assert arguments == [
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "250",
        "--detail-limit",
        "2000",
    ]
    parsed = generated.parse_args(["--provider", PROVIDER, *arguments])
    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.per_target_limit == 0
    assert parsed.max_pages == 250
    assert parsed.detail_limit == 2000
    assert parsed.allow_partial_save is False
