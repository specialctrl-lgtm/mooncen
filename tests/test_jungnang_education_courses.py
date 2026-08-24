from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalYaml as municipal


ROOT = Path(__file__).resolve().parents[1]


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _study_target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=municipal.JUNGNANG_STUDY_PROVIDER,
        name="중랑구 평생학습관 전체강좌",
        branch="중랑구 평생학습관",
        url=municipal.JUNGNANG_STUDY_LIST_URL,
        source="test",
        priority=1,
        region="서울특별시 중랑구",
    )


def _bang_target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=municipal.JUNGNANG_BANG_PROVIDER,
        name="중랑구 방정환교육지원센터 전체프로그램",
        branch=municipal.JUNGNANG_BANG_BRANCH,
        url=municipal.JUNGNANG_BANG_LIST_URL,
        source="test",
        priority=1,
        region="서울특별시 중랑구",
    )


def _imc_target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=municipal.JUNGNANG_IMC_PROVIDER,
        name="중랑구시설관리공단 4개 센터 수강신청",
        branch="중랑구시설관리공단",
        url=municipal.JUNGNANG_IMC_LIST_URL,
        source="test",
        priority=1,
        region="서울특별시 중랑구",
    )


def _study_row(
    ordinal: int,
    lecture_id: int,
    status: str,
    *,
    end_date: str,
    source_group: str,
) -> str:
    return f"""
    <tr>
      <td>{ordinal}</td><td>{source_group}</td>
      <td><span>{status}</span><a href="/study/app/studyLecture/select.do?id={lecture_id}&amp;menuNo=1200040">중랑 강좌 {lecture_id}</a></td>
      <td>2099-07-01 ~ 2099-07-31 2099-08-01 ~ {end_date}</td>
      <td>10,000원</td><td>성인 20/3</td>
    </tr>
    """


def _study_detail(lecture_id: str, *, source_group: str, location: str, application: bool) -> str:
    application_link = '<a href="javascript:lfn_verification();">신청하기</a>' if application else ""
    login_script = (
        '<script>function lfn_verification(){location.href="/portal/member/vnameLoginTo.do?'
        f'programId=userMember&amp;menuNo=200482&amp;_targetUrl=%2Fstudy%2Fapp%2FstudyLecture%2Fselect.do%3Fid%3D{lecture_id}";}}</script>'
        if application
        else ""
    )
    return f"""
    <html><body><h1>중랑 강좌 {lecture_id}</h1><table>
      <tr><th>강사명</th><td>중랑 강사</td><th>선정방식</th><td>선착순</td></tr>
      <tr><th>교육대상</th><td>성인</td><th>교육대상 상세</th><td>중랑구민</td></tr>
      <tr><th>교육장소</th><td>{location}</td><th>교육장소 상세</th><td>{location} 공식 강의실</td></tr>
      <tr><th>교육시간</th><td>매주 화요일 10:00~12:00</td><th>구분</th><td>{source_group}</td></tr>
      <tr><th>수강료</th><td>10,000원</td><th>재료비</th><td>2,000원</td></tr>
      <tr><th>모집/대기/신청</th><td>20 / 5 / 3</td><th>신청방법</th><td>인터넷</td></tr>
      <tr><th>기간</th><td colspan="3">신청 2099-07-01 09:00 ~ 2099-07-31 18:00 교육 2099-08-01 ~ 2099-08-31</td></tr>
    </table>{application_link}{login_script}</body></html>
    """


@pytest.fixture
def study_site(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    page = (
        "<html><body><table><tbody>"
        + _study_row(3, 9001, "접수중", end_date="2099-08-31", source_group="평생학습관")
        + _study_row(2, 9002, "접수예정", end_date="2099-08-31", source_group="동 평생학습센터")
        + _study_row(1, 8001, "마감", end_date="2020-08-31", source_group="평생학습관")
        + "</tbody></table></body></html>"
    )
    fetched: list[str] = []

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.JUNGNANG_STUDY_LIST_PATH:
            assert query["pageUnit"] == ["100"]
            return _soup(page)
        lecture_id = query["id"][0]
        return _soup(
            _study_detail(
                lecture_id,
                source_group="평생학습관" if lecture_id == "9001" else "동 평생학습센터",
                location="망우본동" if lecture_id == "9001" else "면목2동",
                application=lecture_id == "9001",
            )
        )

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    return fetched


def test_jungnang_study_full_snapshot_branch_venue_and_application(study_site: list[str]) -> None:
    rows, parser, meta = municipal.collect_from_url(
        _study_target(), timeout=5, max_depth=0, max_pages=5, detail_limit=10
    )

    assert parser == municipal.JUNGNANG_STUDY_PARSER
    assert len(rows) == 2
    assert meta["total_count"] == meta["discovered_links"] == 3
    assert meta["current_count"] == 2
    assert meta["expired_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta
    assert {row["provider_course_id"] for row in rows} == {
        f"{municipal.JUNGNANG_STUDY_PROVIDER}:lecture:9001",
        f"{municipal.JUNGNANG_STUDY_PROVIDER}:lecture:9002",
    }
    assert all(row["prefer_incoming_provider_course_id"] is True for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["municipality_code"] == "1126000000" for row in rows)
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["branch"] == "중랑구 평생학습관"
    assert open_row["venue_name"] == "망우본동 공식 강의실"
    assert open_row["reservation_available"] is True
    assert urlparse(open_row["application_url"]).path == "/portal/member/vnameLoginTo.do"
    scheduled = next(row for row in rows if row["status"] == "SCHEDULED")
    assert scheduled["branch"] == "면목2동 평생학습센터"
    assert scheduled["venue_name"] == "면목2동 공식 강의실"
    assert scheduled["reservation_available"] is False
    assert "application_url" not in scheduled
    assert len(study_site) == 3


def test_jungnang_study_waitlist_status_and_application_control() -> None:
    list_soup = _soup(
        "<table><tbody>"
        + _study_row(
            1,
            9010,
            "대기자접수",
            end_date="2099-08-31",
            source_group="평생학습관",
        )
        + "</tbody></table>"
    )
    rows, invalid = municipal.jungnang_study_list_rows(
        _study_target(),
        list_soup,
        1,
    )

    assert invalid == 0
    assert len(rows) == 1
    assert rows[0]["status"] == "WAITING"

    error = municipal.jungnang_enrich_study_detail(
        rows[0],
        _soup(
            _study_detail(
                "9010",
                source_group="평생학습관",
                location="망우본동",
                application=True,
            )
        ),
    )

    assert error == ""
    assert rows[0]["application_type"] == "WAITLIST_APPLY"
    assert rows[0]["reservation_available"] is True
    assert urlparse(rows[0]["application_url"]).path == (
        "/portal/member/vnameLoginTo.do"
    )


def _bang_block(
    ordinal: int,
    program_id: int,
    status: str,
    *,
    end_date: str,
    category_code: str,
    one_date_range: bool = False,
) -> str:
    dates = (
        f"2020-07-01 ~ {end_date}"
        if one_date_range
        else f"2099-07-01 ~ 2099-07-31 2099-08-01 ~ {end_date}"
    )
    return f"""
    <ul class="bLine">
      <li>{ordinal}</li>
      <li>{status}<a href="/pf/content.php?sgm={category_code}&amp;no={program_id}&amp;sugang=0&amp;intPage=1">방정환 강좌 {program_id}</a></li>
      <li>{dates}</li>
      <li>초등 모집 : 20 / 신청 : 3</li><li>진로프로그램</li>
    </ul>
    """


def _bang_detail(program_id: str, *, application: bool) -> str:
    application_link = '<a href="javascript: popupFormFunc()">신청하기</a>' if application else ""
    login_script = (
        '<script>function popupFormFunc(){location.href="/member/login.php?redirectUrl=L3BmL2NvbnRlbnQucGhw";}</script>'
        if application
        else ""
    )
    return f"""
    <html><body><h1>방정환 강좌 {program_id}</h1>
      <div class="li"><p>접수기간</p><font>2099-07-01(수) ~ 2099-07-31(금)</font></div>
      <div class="li"><p>문의</p><font>02-2094-0107</font></div>
      <div class="li"><p>강사</p><font>방정환 강사</font></div>
      <div class="li"><p>강의대상</p><font>초등학생</font></div>
      <div class="li"><p>교육장소</p><font>방정환교육지원센터 강의실 {program_id}</font></div>
      <div class="li"><p>교육시간</p><font>08/01 10:00~12:00</font></div>
      <div class="li"><p>재료비</p><font>2,000원</font></div>
      <div class="li"><p>수강료</p><font>무료</font></div>
      <div class="li"><p>인원</p><font>모집 : 20명 / 접수 : 3명</font></div>
      <table><tr><th>차시</th><th>교육 내용</th></tr><tr><td>1</td><td>공식 상세 내용</td></tr></table>
      {application_link}{login_script}
    </body></html>
    """


@pytest.fixture
def bang_site(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    page = (
        "<html><body><div>총 4 개</div>"
        + _bang_block(4, 9101, "접수중", end_date="2099-08-31", category_code="experience")
        + _bang_block(3, 9102, "접수예정", end_date="2099-08-31", category_code="research")
        + _bang_block(2, 8101, "접수마감", end_date="2020-08-31", category_code="family")
        + _bang_block(
            1,
            8102,
            "접수마감",
            end_date="2020-07-31",
            category_code="experience",
            one_date_range=True,
        )
        + "</body></html>"
    )
    fetched: list[str] = []

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.JUNGNANG_BANG_LIST_PATH:
            assert query["intPage"] == ["1"]
            return _soup(page)
        program_id = query["no"][0]
        return _soup(_bang_detail(program_id, application=program_id == "9101"))

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    return fetched


def test_jungnang_bang_full_snapshot_and_actual_login_application(bang_site: list[str]) -> None:
    rows, parser, meta = municipal.collect_from_url(
        _bang_target(), timeout=5, max_depth=0, max_pages=5, detail_limit=10
    )

    assert parser == municipal.JUNGNANG_BANG_PARSER
    assert len(rows) == 2
    assert meta["total_count"] == meta["discovered_links"] == 4
    assert meta["current_count"] == 2
    assert meta["detail_pages"] == 2
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta
    assert {row["provider_course_id"] for row in rows} == {
        f"{municipal.JUNGNANG_BANG_PROVIDER}:program:9101",
        f"{municipal.JUNGNANG_BANG_PROVIDER}:program:9102",
    }
    assert all(row["branch"] == municipal.JUNGNANG_BANG_BRANCH for row in rows)
    assert all(row["venue_name"].startswith("방정환교육지원센터 강의실") for row in rows)
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["reservation_available"] is True
    assert urlparse(open_row["application_url"]).path == "/member/login.php"
    scheduled = next(row for row in rows if row["status"] == "SCHEDULED")
    assert scheduled["reservation_available"] is False
    assert "application_url" not in scheduled
    assert len(bang_site) == 3


def _imc_program(item_code: str, adult_group: str, cost: int) -> dict[str, Any]:
    return {
        "itemCd": item_code,
        "itemNm": f"공식 요금 {adult_group}",
        "adultGubn": adult_group,
        "costAmt": cost,
        "monthCnt": 1,
    }


def _imc_class(
    center: str,
    api_name: str,
    class_code: str,
    programs: list[dict[str, Any]],
    *,
    always: str = "N",
    use_yn: str = "Y",
    web_finished: str = "N",
    rec_start: str = "20990701",
    rec_end: str = "20990731",
) -> dict[str, Any]:
    return {
        "comcd": center,
        "comnm": api_name,
        "classCd": class_code,
        "classNm": f"공식 강습 {class_code}",
        "sportsCdNm": "수영",
        "placeCdNm": f"강의실 {class_code}",
        "trainDayNm": "월,수",
        "trainTimeNm": "10:00~12:00",
        "classObj": "중랑구민",
        "webCapa": 20,
        "webUser": 3,
        "useYn": use_yn,
        "alwaysAcceptYn": always,
        "webAcceptFinishYn": web_finished,
        "freeClassYn": "N",
        "tdateUseYn": "N",
        "programItem": programs,
        "grpcd": {
            "cdNm": "수영",
            "recSdate": rec_start,
            "recEdate": rec_end,
            "repSdate": "20990620",
            "repEdate": "20990630",
            "startdate": "01",
        },
    }


@pytest.fixture
def imc_site(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any] | None]]:
    facilities = [
        {"comcd": code, "comnm": names[0], "addr": f"서울 중랑구 {code}", "tel1": "02-0000-0000"}
        for code, names in municipal.JUNGNANG_IMC_CENTERS.items()
    ]
    by_center = {
        "JUNGNANG01": [
            _imc_class(
                "JUNGNANG01",
                "중랑구민체육센터",
                "C001",
                [_imc_program("I001", "성인", 10000), _imc_program("I002", "청소년", 8000)],
                rec_start="20990801",
                rec_end="20990831",
            )
        ],
        "JUNGNANG02": [
            _imc_class(
                "JUNGNANG02", "중랑문화체육관", "C002", [_imc_program("I003", "성인", 12000)], always="Y"
            )
        ],
        "JUNGNANG03": [
            _imc_class(
                "JUNGNANG03",
                "면목2동체육관",
                "C003",
                [_imc_program("I004", "성인", 14000)],
                web_finished="Y",
            )
        ],
        "JUNGNANG19": [
            _imc_class(
                "JUNGNANG19",
                "묵2동문화체육복합센터",
                "C004",
                [_imc_program("I005", "성인", 16000)],
                use_yn="R",
            )
        ],
    }
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def request_json(
        _session: object,
        endpoint: str,
        params: dict[str, Any] | None,
        _timeout: int,
    ) -> Any:
        calls.append((endpoint, params))
        if endpoint == "/data/lecture/sb1":
            return facilities
        if endpoint == "/data/lecture/today":
            return {"today": "20990715"}
        assert endpoint == "/data/lecture/lectureList/"
        assert params is not None
        items = by_center[params["comcd"]]
        return {
            "pageIndex": int(params["pageIndex"]),
            "pageSize": int(params["pageSize"]),
            "startRow": int(params["startRow"]),
            "totalCount": len(items),
            "resultList": items,
        }

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "jungnang_imc_request_json", request_json)
    return calls


def test_jungnang_imc_four_centers_expand_official_program_items(
    imc_site: list[tuple[str, dict[str, Any] | None]],
) -> None:
    rows, parser, meta = municipal.collect_from_url(
        _imc_target(), timeout=5, max_depth=0, max_pages=10, detail_limit=0
    )

    assert parser == municipal.JUNGNANG_IMC_PARSER
    assert len(rows) == 5
    assert meta["declared_class_count"] == 4
    assert meta["selectable_item_count"] == 5
    assert meta["center_totals"] == {code: 1 for code in municipal.JUNGNANG_IMC_CENTERS}
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta
    assert {row["provider_course_id"] for row in rows} == {
        f"{municipal.JUNGNANG_IMC_PROVIDER}:class:JUNGNANG01:C001:I001",
        f"{municipal.JUNGNANG_IMC_PROVIDER}:class:JUNGNANG01:C001:I002",
        f"{municipal.JUNGNANG_IMC_PROVIDER}:class:JUNGNANG02:C002:I003",
        f"{municipal.JUNGNANG_IMC_PROVIDER}:class:JUNGNANG03:C003:I004",
        f"{municipal.JUNGNANG_IMC_PROVIDER}:class:JUNGNANG19:C004:I005",
    }
    assert all(row["prefer_incoming_provider_course_id"] is True for row in rows)
    assert all(row["venue_name"].startswith("강의실") for row in rows)
    assert {row["branch"] for row in rows} == {
        "중랑구민체육센터(묵동)",
        "중랑문화체육관(면목7동)",
        "면목2동체육관",
        "묵2동문화체육복합센터",
    }
    assert {row["status"] for row in rows} == {"OPEN", "SCHEDULED", "CLOSED"}
    assert sum(row["reservation_available"] for row in rows) == 1
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert parse_qs(urlparse(open_row["application_url"]).query)["itemCd"] == ["I003"]
    assert len([call for call in imc_site if call[0] == "/data/lecture/lectureList/"]) == 4


def test_jungnang_incomplete_snapshot_blocks_save_and_stale(
    study_site: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        generated,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("incomplete Jungnang crawl must not open DB")),
    )
    monkeypatch.setattr(generated, "mark_stale_courses", lambda *args: stale_calls.append(args) or 0)

    result = generated._collect_single_target(
        _study_target(),
        per_target_limit=0,
        max_depth=0,
        max_pages=5,
        detail_limit=1,
        timeout=5,
    )

    assert result.collection_complete is False
    assert "detail_limit cap allows 1 of 2 current details" in result.report.configured_collection_error
    generated._persist_collection_results(
        [result],
        mark_stale=True,
        max_pages=5,
        per_target_limit=0,
        complete_providers={municipal.JUNGNANG_STUDY_PROVIDER},
    )
    assert result.report.saved == 0
    assert result.report.success is False
    assert stale_calls == []


def test_jungnang_imc_page_cap_marks_snapshot_incomplete(
    imc_site: list[tuple[str, dict[str, Any] | None]],
) -> None:
    _rows, _parser, meta = municipal.collect_from_url(
        _imc_target(), timeout=5, max_depth=0, max_pages=3, detail_limit=0
    )
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "max_pages cap reached before completing JUNGNANG19" in meta["configured_collection_error"]


def _yaml_target(path: Path, provider: str) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return next(row for row in document["targets"] if row.get("provider") == provider)


def test_jungnang_configs_and_full_snapshot_contract() -> None:
    study = _yaml_target(
        ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml",
        municipal.JUNGNANG_STUDY_PROVIDER,
    )
    bang = _yaml_target(
        ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml",
        municipal.JUNGNANG_BANG_PROVIDER,
    )
    imc = _yaml_target(
        ROOT / "config" / "crawl_targets" / "sports_facility.yaml",
        municipal.JUNGNANG_IMC_PROVIDER,
    )
    for target in (study, bang, imc):
        assert target["collection_category"] == "공공예약"
        assert target["domain_category"] == "교육·강좌"
        assert target["source_group"] == "municipal_reservation"
        assert target["service_group"] == "공공강좌"
        assert target["service_group_policy"] == "locked"
        assert target["full_snapshot_required"] is True
        assert target["municipality_code"] == "1126000000"

    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[municipal.JUNGNANG_STUDY_PROVIDER] == (
        "--save-db", "--mark-stale", "--per-target-limit", "0",
        "--max-pages", "10", "--detail-limit", "100",
    )
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[municipal.JUNGNANG_BANG_PROVIDER] == (
        "--save-db", "--mark-stale", "--per-target-limit", "0",
        "--max-pages", "250", "--detail-limit", "100",
    )
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[municipal.JUNGNANG_IMC_PROVIDER] == (
        "--save-db", "--mark-stale", "--per-target-limit", "0",
        "--max-pages", "20", "--detail-limit", "0",
    )

    overrides = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_overrides.yaml").read_text(encoding="utf-8")
    )
    municipality = next(row for row in overrides["municipalities"] if row["code"] == "1126000000")
    assert {candidate["url"] for candidate in municipality["candidates"]} == {
        municipal.JUNGNANG_STUDY_LIST_URL,
        municipal.JUNGNANG_BANG_LIST_URL,
        municipal.JUNGNANG_IMC_LIST_URL,
    }
