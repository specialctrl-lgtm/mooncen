from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalYaml as municipal


PROVIDER = municipal.HSCITY_EDUCATION_PROVIDER
TARGET_URL = municipal.HSCITY_EDUCATION_LIST_URL


def _target(url: str = TARGET_URL) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=PROVIDER,
        name="화성특례시 통합예약시스템",
        branch="경기도 화성시",
        url=url,
        source="test",
        priority=1,
        region="경기도",
        extra={
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "public_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
        },
    )


def _card(
    service_type: str,
    service_id: str,
    *,
    branch: str,
    institution_idx: str,
    title: str,
    apply: bool,
    sports_shape: bool = False,
) -> str:
    path, id_name = municipal.HSCITY_EDUCATION_SERVICE_ROUTES[service_type]
    flags = "" if sports_shape else '<span class="flag purple">성인</span><span class="flag blue">무료</span>'
    button = (
        f'<button type="button" onclick="javascript:fnApply(\'{service_type}\', \'{service_id}\'); return false;">신청</button>'
        if apply
        else ""
    )
    return f"""
    <li class="table-list-item">
      <input type="hidden" name="interestInfoList[0].linkUrl" value="{path}?{id_name}={service_id}" />
      <input type="hidden" name="interestInfoList[0].institutionIdx" value="{institution_idx}" />
      <input type="hidden" name="interestInfoList[0].serviceTypeCd" value="{service_type}" />
      <input type="hidden" name="interestInfoList[0].serviceIdx" value="{service_id}" />
      <p class="table-list-num">[강좌/교육]</p>
      <div class="table-list-main" onclick="javascript:fnDetail('{service_type}', '{service_id}'); return false;">
        <div class="table-list-title"><p class="main-title"><a>{title}</a></p><div class="flag-list">{flags}</div></div>
        <ul class="table-list-info ver2">
          <li class="info-item inst-info-item"><p class="info-title"><span>{branch}</span><span class="tel">031-5189-0000</span></p></li>
          <li><dl class="info-desc"><dt class="desc-title">강좌기간</dt><dd class="desc-txt">08.01 ~ 08.31</dd></dl></li>
          <li><dl class="info-desc"><dt class="desc-title">접수기간</dt><dd class="desc-txt">07.01 ~ 07.31</dd></dl></li>
          <li><dl class="info-desc"><dt class="desc-title">신청자수</dt><dd class="desc-txt">1 / 10</dd></dl></li>
          <li><dl class="info-desc"><dt class="desc-title">대기자수</dt><dd class="desc-txt">0 / 2</dd></dl></li>
          <li><dl class="info-desc"><dt class="desc-title">수강료</dt><dd class="desc-txt">무료</dd></dl></li>
          <li><dl class="info-desc"><dt class="desc-title">접수방법</dt><dd class="desc-txt">인터넷</dd></dl></li>
        </ul>
      </div>
      <div class="table-list-btn">{button}</div>
    </li>
    """


def _list_page(cards: list[str]) -> str:
    return f"""
    <html><body>
      <form><input name="recordCountPerPage" value="1000" /></form>
      <p class="table-total">총 항목 수 : <span class="num">{len(cards)}</span>건</p>
      <ul class="table-list">{''.join(cards)}</ul>
    </body></html>
    """


def _detail_page(
    branch: str,
    *,
    application: bool,
    venue: str = "2층 배움터",
    map_address: str = "",
    map_lat: float | None = None,
    map_lon: float | None = None,
) -> str:
    button = '<button onclick="javascript:fnApply();">신청</button>' if application else ""
    map_tab = ""
    if map_address and map_lat is not None and map_lon is not None:
        map_tab = f"""
        <div class="detail-tab map-tab">
          <script>
            var mapOption = {{
              center: new kakao.maps.LatLng('{map_lat}','{map_lon}')
            }};
            var iwContent = '<div class="infoTitle"><a href="https://map.kakao.com/link/map/{map_address},{map_lat},{map_lon}">{map_address}</a></div>';
          </script>
        </div>
        """
    return f"""
    <html><body>
      <div class="detail-info-list">
        <dl class="item-desc"><dt class="desc-title">운영기관</dt><dd class="desc-txt">{branch} 바로가기</dd></dl>
        <dl class="item-desc"><dt class="desc-title">접수방법</dt><dd class="desc-txt">인터넷</dd></dl>
        <dl class="item-desc"><dt class="desc-title">강좌분류</dt><dd class="desc-txt">인문교양교육</dd></dl>
        <dl class="item-desc"><dt class="desc-title">교육대상</dt><dd class="desc-txt">화성시민</dd></dl>
        <dl class="item-desc"><dt class="desc-title">접수일시</dt><dd class="desc-txt">2099.07.01 09:00 ~ 2099.07.31 18:00</dd></dl>
        <dl class="item-desc"><dt class="desc-title">수강기간</dt><dd class="desc-txt">2099.08.01 ~ 2099.08.31</dd></dl>
        <dl class="item-desc"><dt class="desc-title">요일/시간</dt><dd class="desc-txt">월 / 10:00~12:00</dd></dl>
        <dl class="item-desc"><dt class="desc-title">장소</dt><dd class="desc-txt">{venue}</dd></dl>
        <dl class="item-desc"><dt class="desc-title">신청/대기</dt><dd class="desc-txt">신청자수 : 2 / 10 대기자수 : 1 / 2</dd></dl>
        <dl class="item-desc"><dt class="desc-title">수강료</dt><dd class="desc-txt">무료</dd></dl>
        <dl class="item-desc"><dt class="desc-title">재료비</dt><dd class="desc-txt">5,000원</dd></dl>
        <dl class="item-desc"><dt class="desc-title">강사명</dt><dd class="desc-txt">홍길동 강사이력보기</dd></dl>
        <dl class="item-desc"><dt class="desc-title">문의처</dt><dd class="desc-txt">031-5189-1111</dd></dl>
      </div>
      <div class="detail-tab info-tab"><p>공식 상세 교육 내용</p><img src="/attach/editor/program.jpg" /></div>
      {map_tab}
      {button}
    </body></html>
    """


@pytest.fixture
def hscity_site(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    definitions = {
        ("401", "ready"): ("lecture", "101", "만세학습관", "11", "만세 준비 강좌", False, False),
        ("402", "apply"): ("citizenuniv", "202", "효행평생학습관", "22", "효행 접수 강좌", True, False),
        ("403", "wait"): ("citizeninfor", "303", "병점정보화교육장", "33", "병점 대기 강좌", True, False),
        ("404", "finish"): ("lecture", "404", "동탄체육센터", "44", "월자유수영", False, True),
    }
    fetched: list[str] = []
    state: dict[str, Any] = {"global_extra": False, "detail_failure_id": ""}

    def card_for(definition: tuple[str, str, str, str, str, bool, bool]) -> str:
        service_type, service_id, branch, institution_idx, title, apply, sports = definition
        return _card(
            service_type,
            service_id,
            branch=branch,
            institution_idx=institution_idx,
            title=title,
            apply=apply,
            sports_shape=sports,
        )

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == municipal.HSCITY_EDUCATION_LIST_PATH:
            district = query["searchAreaEmd"][0]
            status = query["statusCd"][0]
            assert query["recordCountPerPage"] == ["1000"]
            cards: list[str] = []
            if district:
                definition = definitions.get((district, status))
                if definition:
                    cards.append(card_for(definition))
            else:
                cards.extend(card_for(value) for (area, row_status), value in definitions.items() if row_status == status)
                if state["global_extra"] and status == "ready":
                    cards.append(_card("lecture", "999", branch="전역누락기관", institution_idx="99", title="전역 누락 강좌", apply=False))
            return BeautifulSoup(_list_page(cards), "lxml")

        service_id = next(iter(parse_qs(parsed.query).values()))[0]
        if state["detail_failure_id"] == service_id:
            raise RuntimeError("fixture detail outage")
        definition = next(value for value in definitions.values() if value[1] == service_id)
        return BeautifulSoup(_detail_page(definition[2], application=definition[5]), "lxml")

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    return {"definitions": definitions, "fetched": fetched, "state": state}


def test_hscity_all_education_fanout_parses_three_services_and_four_districts(hscity_site: dict[str, Any]) -> None:
    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=50, detail_limit=10
    )

    assert parser == municipal.HSCITY_EDUCATION_PARSER
    assert len(rows) == 4
    assert meta["pages"] == 20
    assert meta["detail_pages"] == 4
    assert meta["detail_errors"] == 0
    assert meta["pagination_complete"] is True
    assert meta["global_union_matches"] is True
    assert meta["duplicate_count"] == 0
    assert meta["district_counts"] == {"401": 1, "402": 1, "403": 1, "404": 1}
    assert meta["status_counts"] == {"ready": 1, "apply": 1, "wait": 1, "finish": 1}
    assert meta["service_type_counts"] == {"lecture": 2, "citizenuniv": 1, "citizeninfor": 1}
    assert "configured_collection_error" not in meta

    by_id = {row["provider_course_id"]: row for row in rows}
    assert set(by_id) == {"101", "citizenuniv:202", "citizeninfor:303", "404"}
    expected_codes = {
        "101": ("4159100000", "HSCITY_401_11"),
        "citizenuniv:202": ("4159300000", "HSCITY_402_22"),
        "citizeninfor:303": ("4159500000", "HSCITY_403_33"),
        "404": ("4159700000", "HSCITY_404_44"),
    }
    for course_id, row in by_id.items():
        municipality_code, branch_code = expected_codes[course_id]
        assert row["municipality_code"] == municipality_code
        assert row["branch_code"] == branch_code
        assert row["venue_name"] == "2층 배움터"
        assert row["period"] == "2099-08-01 ~ 2099-08-31"
        assert row["apply_period"] == "2099-07-01 ~ 2099-07-31"
        assert row["capacity_current"] == 2 and row["capacity_total"] == 10
        assert row["waitlist_current"] == 1 and row["waitlist_total"] == 2
        assert row["raw_fields"]["service_type"] in municipal.HSCITY_EDUCATION_SERVICE_ROUTES

    assert by_id["101"]["raw_url"].endswith("/lectureDetail.do?lectureIdx=101")
    assert by_id["citizenuniv:202"]["raw_url"].endswith("/citizenUnivDetail.do?citizenUnivIdx=202")
    assert by_id["citizeninfor:303"]["raw_url"].endswith("/citizenInforDetail.do?citizenInforIdx=303")
    assert by_id["citizenuniv:202"]["application_url"] == by_id["citizenuniv:202"]["raw_url"]
    assert by_id["citizeninfor:303"]["application_url"] == by_id["citizeninfor:303"]["raw_url"]
    assert "application_url" not in by_id["101"]
    assert "application_url" not in by_id["404"]
    assert by_id["404"]["branch"] == "동탄체육센터"
    assert by_id["404"]["phone"] == "031-5189-1111"


def test_hscity_promotes_verified_offsite_course_to_physical_branch() -> None:
    row = {
        "provider": PROVIDER,
        "branch": "화성시청 도서관정책과",
        "branch_code": "HSCITY_401_141",
        "raw_url": (
            "https://yeyak.hscity.go.kr/1002/3001/"
            "lectureDetail.do?lectureIdx=31703"
        ),
        "raw_fields": {},
    }
    detail = _detail_page(
        "화성시청 도서관정책과",
        application=False,
    ).replace(
        "2층 배움터",
        "화성시민대학(413호)",
    )

    municipal.hscity_enrich_detail(row, BeautifulSoup(detail, "lxml"))

    assert row["branch"] == "화성시민대학"
    assert row["branch_code"] == municipal.hscity_venue_branch_code(
        PROVIDER,
        "화성시민대학",
    )
    assert row["address"] == (
        "경기도 화성시 효행구 봉담읍 효행로 212 4층"
    )
    assert row["venue_address"] == row["address"]
    assert row["branch_lat"] == 37.2285182
    assert row["branch_lon"] == 126.9686585
    assert row["raw_fields"]["source_institution"] == (
        "화성시청 도서관정책과"
    )


@pytest.mark.parametrize(
    ("venue", "expected"),
    [
        ("나래울종합사회복지관(4층 정보화교육실)", "나래울종합사회복지관"),
        ("동탄2동 행정복지센터 정보화교육장", "동탄2동 행정복지센터"),
        ("동탄노인복지관(+더배움4)", "동탄노인복지관"),
        ("동탄다원이음터 3층 창작2", "동탄다원이음터"),
        ("동탄중앙이음터 마이랩 (507호)", "동탄중앙이음터"),
        ("2층 배움터", ""),
    ],
)
def test_hscity_physical_branch_name_uses_named_facility(
    venue: str,
    expected: str,
) -> None:
    assert municipal.hscity_physical_branch_name(venue) == expected


def test_hscity_promotes_detail_venue_and_official_map_location() -> None:
    map_address = (
        "경기도 화성시 동탄순환대로 754-14 "
        "동탄다원이음터 3층 창작2"
    )
    row = {
        "provider": PROVIDER,
        "branch": "동탄구청 자치행정과",
        "branch_code": "HSCITY_404_108",
        "raw_url": (
            "https://yeyak.hscity.go.kr/1085/3043/"
            "citizenInforDetail.do?citizenInforIdx=673"
        ),
        "raw_fields": {},
    }
    detail = _detail_page(
        "동탄구청 자치행정과",
        application=True,
        venue="동탄다원이음터 3층 창작2",
        map_address=map_address,
        map_lat=37.2105922232,
        map_lon=127.1045418058,
    )

    municipal.hscity_enrich_detail(row, BeautifulSoup(detail, "lxml"))

    assert row["branch"] == "동탄다원이음터"
    assert row["branch_code"] == municipal.hscity_venue_branch_code(
        PROVIDER,
        "동탄다원이음터",
    )
    assert row["provider_organizer"] == "동탄구청 자치행정과"
    assert row["venue_name"] == "동탄다원이음터 3층 창작2"
    assert row["address"] == map_address
    assert row["branch_address"] == map_address
    assert row["venue_address"] == map_address
    assert row["branch_lat"] == 37.2105922232
    assert row["branch_lon"] == 127.1045418058
    assert row["branch_address_source"] == "OFFICIAL_HSCITY_DETAIL_MAP"
    assert row["branch_coordinate_source"] == "OFFICIAL_HSCITY_DETAIL_MAP"
    assert row["branch_location_verified"] is True
    assert row["basic_info"] == {
        "location_role": "course_venue",
        "education_institution": "동탄구청 자치행정과",
    }
    assert row["raw_fields"]["source_institution"] == "동탄구청 자치행정과"
    assert row["raw_fields"]["official_location"] == {
        "venue_name": "동탄다원이음터 3층 창작2",
        "address": map_address,
        "lat": 37.2105922232,
        "lon": 127.1045418058,
    }


def test_hscity_named_venue_reuses_matching_institution_branch_code() -> None:
    row = {
        "provider": PROVIDER,
        "branch": "동탄다원이음터",
        "branch_code": "HSCITY_404_321",
        "raw_url": (
            "https://yeyak.hscity.go.kr/1002/3001/"
            "lectureDetail.do?lectureIdx=321"
        ),
        "raw_fields": {},
    }
    detail = _detail_page(
        "동탄다원이음터",
        application=True,
        venue="동탄다원이음터 3층 창작2",
        map_address="경기도 화성시 동탄순환대로 754-14",
        map_lat=37.2105302,
        map_lon=127.104617,
    )

    municipal.hscity_enrich_detail(row, BeautifulSoup(detail, "lxml"))

    assert row["branch"] == "동탄다원이음터"
    assert row["branch_code"] == "HSCITY_404_321"


def test_hscity_uses_verified_central_ieumteo_location_override() -> None:
    row = {
        "provider": PROVIDER,
        "branch": "동탄구청 자치행정과",
        "branch_code": "HSCITY_404_108",
        "raw_url": (
            "https://yeyak.hscity.go.kr/1085/3043/"
            "citizenInforDetail.do?citizenInforIdx=673"
        ),
        "raw_fields": {},
    }
    detail = _detail_page(
        "동탄구청 자치행정과",
        application=True,
        venue="동탄중앙이음터 마이랩 (507호)",
        map_address="경기도 화성시 동탄순환대로 754-14 3층 창작2",
        map_lat=37.2105302,
        map_lon=127.104617,
    )

    municipal.hscity_enrich_detail(row, BeautifulSoup(detail, "lxml"))

    assert row["branch"] == "동탄중앙이음터"
    assert row["branch_code"] == "HSCITY_404_100"
    assert row["address"] == "경기도 화성시 동탄구 동탄대로시범길 115"
    assert row["branch_lat"] == 37.1987111
    assert row["branch_lon"] == 127.1106577
    assert row["branch_address_source"] == "OFFICIAL_FACILITY_LOCATION"
    assert row["branch_coordinate_source"] == "GOOGLE_PLACES_TEXT_SEARCH"


def test_hscity_library_directory_and_current_membership_contract() -> None:
    assert len(municipal.HSCITY_LIBRARY_SITE_MENU_IDS) == 22
    assert municipal.HSCITY_LIBRARY_DIRECTORY_SLUGS == set(
        municipal.HSCITY_LIBRARY_SITE_MENU_IDS
    )
    directory = BeautifulSoup(
        '<html><body><a href="/intro/index.do">Directory home</a>'
        + "".join(
            f'<a href="/{slug}/index.do">{slug}</a>'
            for slug in municipal.HSCITY_LIBRARY_DIRECTORY_SLUGS
        )
        + "</body></html>",
        "lxml",
    )
    assert municipal.hscity_library_directory_slugs(directory) == (
        municipal.HSCITY_LIBRARY_DIRECTORY_SLUGS
    )

    statuses = (
        ("101", "\uC811\uC218\uC608\uC815"),
        ("102", "\uC811\uC218\uC911"),
        ("103", "\uB300\uAE30 \uC811\uC218\uC911"),
        ("104", "\uC811\uC218\uB9C8\uAC10"),
        ("105", "\uAC15\uC88C\uC885\uB8CC"),
        ("106", "\uAC15\uC88C\uCDE8\uC18C"),
    )
    page = BeautifulSoup(
        '<ul class="article-list lecture">'
        + "".join(
            f"""
            <li>
              <p class="title">
                <a href="https://yeyak.hscity.go.kr/1002/3001/lectureDetail.do?lectureIdx={course_id}">
                  {index}. Course {course_id}
                </a>
              </p>
              <span class="themeFC">Library branch</span>
              <span class="status">{status}</span>
            </li>
            """
            for index, (course_id, status) in enumerate(statuses, 1)
        )
        + "</ul>",
        "lxml",
    )
    memberships, errors = municipal.hscity_library_page_memberships(
        "nylib",
        municipal.hscity_library_list_url("nylib"),
        page,
    )

    assert errors == []
    assert set(memberships) == {
        "lecture:101",
        "lecture:102",
        "lecture:103",
        "lecture:104",
    }
    assert memberships["lecture:103"]["status_filter"] == "wait"
    assert memberships["lecture:101"]["list_url"].endswith(
        "/nylib/menu/10209/program/30021/lectureList.do"
    )

    invalid_memberships, invalid_errors = municipal.hscity_library_page_memberships(
        "nylib",
        municipal.hscity_library_list_url("nylib"),
        BeautifulSoup("<html><body>login page</body></html>", "lxml"),
    )
    assert invalid_memberships == {}
    assert invalid_errors == ["nylib: library lecture list root missing or duplicated"]


def test_hscity_library_memberships_enrich_canonical_rows_and_detect_missing() -> None:
    row = {
        "provider_course_id": "101",
        "raw_fields": {"service_type": "lecture", "service_id": "101"},
    }
    scan = {
        "errors": [],
        "memberships": {
            "lecture:101": {
                "slug": "nylib",
                "branch": "Library branch",
                "status_filter": "apply",
                "home_url": "https://www.hscitylib.or.kr/nylib/index.do",
                "list_url": municipal.hscity_library_list_url("nylib"),
            }
        },
    }

    enriched, errors = municipal.apply_hscity_library_memberships(
        [row],
        {"lecture:101"},
        scan,
    )

    assert enriched == 1
    assert errors == []
    assert row["branch_url"] == municipal.hscity_library_list_url("nylib")
    assert row["raw_fields"]["library_directory_url"] == municipal.HSCITY_LIBRARY_DIRECTORY_URL
    assert row["raw_fields"]["library_status_filter"] == "apply"

    _enriched, missing_errors = municipal.apply_hscity_library_memberships(
        [],
        set(),
        scan,
    )
    assert "missed 1 current library lectures" in missing_errors[0]


def test_hscity_normalization_keeps_required_target_when_source_omits_it() -> None:
    writer = municipal.MunicipalDbWriter(PROVIDER)
    base = {
        "provider_course_id": "lecture:1",
        "category": "기타",
        "fee": "20,000원",
        "period": "2026.08.01 ~ 2026.08.31",
        "schedule_raw": "화,목 / 11:00 ~ 11:50",
        "raw_url": "https://yeyak.hscity.go.kr/1002/3001/lectureDetail.do?lectureIdx=1",
    }

    adult = writer.normalize_course({**base, "title": "줌바 10시 월수금(성인)"}, "branch-id")
    unknown = writer.normalize_course({**base, "title": "PT댄스 화목 11시"}, "branch-id")

    assert adult["target"] == "성인"
    assert unknown["target"] == "연령 미정"


def test_hscity_collection_rows_record_truthful_target_fallback() -> None:
    parser = municipal.TargetParser()
    adult = {"title": "줌바 10시 월수금(성인)", "raw_fields": {}}
    unknown = {"title": "PT댄스 화목 11시", "raw_fields": {}}

    municipal.hscity_fill_missing_target(adult, parser)
    municipal.hscity_fill_missing_target(unknown, parser)

    assert adult["target"] == "성인"
    assert adult["raw_fields"]["target_fallback"] == "title_age_group:ADULT"
    assert unknown["target"] == "연령 미정"
    assert unknown["raw_fields"]["target_fallback"] == "official_source_unspecified"


def test_hscity_library_audit_is_applied_to_the_canonical_scan(
    hscity_site: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target()
    target.extra["hscity_library_directory_audit"] = True
    monkeypatch.setattr(
        municipal,
        "hscity_library_membership_scan",
        lambda *_args, **_kwargs: {
            "pages": 23,
            "memberships": {
                "lecture:101": {
                    "slug": "nylib",
                    "branch": "Library branch",
                    "status_filter": "ready",
                    "home_url": "https://www.hscitylib.or.kr/nylib/index.do",
                    "list_url": municipal.hscity_library_list_url("nylib"),
                }
            },
            "source_counts": {"nylib": 1},
            "directory_slugs": sorted(municipal.HSCITY_LIBRARY_DIRECTORY_SLUGS),
            "errors": [],
        },
    )

    rows, _parser, meta = municipal.collect_hscity_lecture_list(
        target,
        timeout=5,
        max_pages=80,
        detail_limit=4,
    )

    assert meta["pages"] == 43
    assert meta["library_directory_audit"] is True
    assert meta["library_directory_count"] == 22
    assert meta["library_current_count"] == 1
    assert meta["library_enriched_count"] == 1
    row = next(item for item in rows if item["provider_course_id"] == "101")
    assert row["branch_url"] == municipal.hscity_library_list_url("nylib")


def test_hscity_global_partition_mismatch_is_incomplete_after_one_retry(hscity_site: dict[str, Any]) -> None:
    hscity_site["state"]["global_extra"] = True
    rows, _parser, meta = municipal.collect_hscity_lecture_list(
        _target(), timeout=5, max_pages=50, detail_limit=10
    )

    assert len(rows) == 4
    assert meta["scan_attempts"] == 2
    assert meta["pages"] == 40
    assert meta["detail_pages"] == 0
    assert meta["pagination_complete"] is False
    assert meta["global_union_matches"] is False
    assert "district union did not match global current set" in meta["configured_collection_error"]


@pytest.mark.parametrize("failure", ["cap", "detail"])
def test_hscity_detail_incompleteness_blocks_persistence_and_stale(
    hscity_site: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    if failure == "detail":
        hscity_site["state"]["detail_failure_id"] = "303"
    stale_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        generated,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("incomplete HSCITY crawl must not open a DB transaction")),
    )
    monkeypatch.setattr(generated, "mark_stale_courses", lambda *args: stale_calls.append(args) or 0)

    result = generated._collect_single_target(
        _target(),
        per_target_limit=0,
        max_depth=0,
        max_pages=50,
        detail_limit=1 if failure == "cap" else 10,
        timeout=5,
    )

    assert result.collection_complete is False
    if failure == "cap":
        assert result.report.detail_pages == 1
        assert "detail enrichment capped at 1 of 4" in result.report.configured_collection_error
    else:
        assert result.report.detail_pages == 4
        assert "detail fetch failed for 1" in result.report.configured_collection_error

    generated._persist_collection_results(
        [result],
        mark_stale=True,
        max_pages=50,
        per_target_limit=0,
        complete_providers={PROVIDER},
    )
    assert result.report.saved == 0
    assert result.report.success is False
    assert stale_calls == []


def test_hscity_target_lock_duplicate_exclusions_and_generated_full_run_contract() -> None:
    public = yaml.safe_load(
        (municipal.ROOT / "config" / "crawl_targets" / "public_reservation.yaml").read_text(encoding="utf-8")
    )
    canonical = next(row for row in public["targets"] if row["provider"] == PROVIDER)
    assert canonical["url"] == TARGET_URL
    assert canonical["service_group"] == "공공강좌"
    assert canonical["service_group_policy"] == "locked"
    assert canonical["hscity_library_directory_audit"] is True

    duplicate_specs = (
        ("lifelong_learning.yaml", "MUNI_WWW_HSTREE_ORG_A9F492C9"),
        ("library.yaml", "MUNI_YEYAK_HSCITY_GO_KR_E7FCC3C0"),
    )
    for filename, provider in duplicate_specs:
        document = yaml.safe_load((municipal.ROOT / "config" / "crawl_targets" / filename).read_text(encoding="utf-8"))
        duplicate = next(row for row in document["targets"] if row["provider"] == provider)
        assert duplicate["collection_type"] == "duplicate"
        assert duplicate["crawler_status"] == f"duplicate_url:{PROVIDER}"
        assert duplicate["duplicate_of"] == PROVIDER

    arguments = list(generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[PROVIDER])
    assert arguments == [
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "80",
        "--detail-limit",
        "1200",
    ]
    parsed = generated.parse_args(["--provider", PROVIDER, *arguments])
    assert parsed.mark_stale is True
    assert parsed.per_target_limit == 0
    assert parsed.allow_partial_save is False


def test_hscity_dispatch_is_owned_and_accepts_legacy_list_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = _target("https://yeyak.hscity.go.kr/1002/3001/lectureList.do")
    assert municipal.is_hscity_education_target(_target()) is True
    assert municipal.is_hscity_education_target(legacy) is True
    sentinel = ([{"title": "sentinel"}], municipal.HSCITY_EDUCATION_PARSER, {"pages": 1})
    monkeypatch.setattr(municipal, "collect_hscity_lecture_list", lambda *_args, **_kwargs: sentinel)
    assert municipal.collect_from_url(_target(), timeout=5, max_depth=0, max_pages=30, detail_limit=10) == sentinel
