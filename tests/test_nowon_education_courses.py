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


class DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def close(self) -> None:
        return None


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _lifelong_target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=municipal.NOWON_LIFELONG_PROVIDER,
        name="노원평생교육포털 전체 강좌",
        branch="노원평생교육포털",
        url=municipal.NOWON_LIFELONG_LIST_URL,
        source="test",
        priority=1,
        region="서울특별시 노원구",
    )


def _fmcs_target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=municipal.NOWON_FMCS_PROVIDER,
        name="노원구시설관리공단 전체 수강신청",
        branch="노원구시설관리공단",
        url=municipal.NOWON_FMCS_LIST_URL,
        source="test",
        priority=1,
        region="서울특별시 노원구",
    )


def _lifelong_card(program_id: int, ordinal: int, status: str, branch: str) -> str:
    return f"""
    <li class="li">
      <span class="ask color01">{status}</span>
      <div class="img_box"><img src="/images/{program_id}.png" /></div>
      <div class="txt_box"><div class="top_box">
        <ul><li>성인</li><li>상계1동</li></ul>
        <a class="top_tit"><span class="pno">{ordinal}. </span>노원 강좌 {program_id}</a>
      </div><div class="bottom_box">
        <dl><dt class="place">기관</dt><dd>{branch}</dd></dl>
        <dl><dt class="teacher">강사</dt><dd>노원 강사 {program_id}</dd></dl>
        <dl><dt class="time">시간</dt><dd>월 / 10:00 ~ 12:00</dd></dl>
        <dl><dt class="price">수강료</dt><dd>10,000원</dd></dl>
        <a href="javascript:fnGoDetail({program_id})">상세보기</a>
      </div></div>
    </li>
    """


def _lifelong_page(cards: str, total: int) -> str:
    return f"""
    <html><body>
      <script>$("#totCount").text("총 {total}개");</script>
      <ul class="search_list">{cards}</ul>
    </body></html>
    """


def _lifelong_detail(
    program_id: str,
    *,
    end_date: str,
    branch: str,
    status_application: str = "none",
) -> str:
    apply_link = ""
    receipt_type = "1001"
    external = ""
    if status_application != "none":
        apply_link = '<a class="btn_s1_c1" href="javascript:fnDetailApply();">강좌 신청</a>'
    if status_application == "external":
        receipt_type = "5001"
        external = f"https://apply.example.org/forms/{program_id}?course=1&currentPage=1"
    return f"""
    <html><body><table>
      <tr><th>주관기관</th><td>{branch} 이동</td><th>유관부서</th><td>미래교육과</td></tr>
      <tr><th>신청기간</th><td>99.07.01_09:00 ~ 99.07.31_18:00</td></tr>
      <tr><th>정원</th><td>총 20명 (3/20)</td></tr>
      <tr><th>교육기간</th><td>99.08.01 ~ {end_date}</td></tr>
      <tr><th>요일/시간</th><td>월 / 10:00 ~ 12:00</td></tr>
      <tr><th>장소</th><td>{branch} 강의실</td></tr>
      <tr><th>수강료</th><td>10,000원</td></tr>
      <tr><th>대상</th><td>성인</td></tr>
      <tr><th>접수방식</th><td>선착순마감대기 (5명 까지)</td></tr>
      <tr><th>강좌분야</th><td>생활/기타</td></tr>
      <tr><th>강사명</th><td>노원 강사 {program_id}</td></tr>
      <tr><th>문의전화</th><td>02-0000-{program_id[-4:]}</td></tr>
    </table>
    <div id="tab01" class="tab_con">공식 강좌 설명 {program_id}</div>
    {apply_link}
    <script>
      function fnDetailApply() {{
        var rsvnRectype = "{receipt_type}";
        var linkUrl = "{external}";
        data.edcPrgmid = {program_id};
        data.edcRsvnsetSeq = 209901;
      }}
    </script></body></html>
    """


@pytest.fixture
def lifelong_site(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    first = "".join(
        [
            _lifelong_card(9001, 5, "접수중", "상계학습관"),
            _lifelong_card(9002, 4, "온라인 마감", "월계학습관"),
            _lifelong_card(9003, 3, "접수중", "공릉학습관"),
            _lifelong_card(8001, 2, "종료", "하계학습관"),
        ]
    )
    second = _lifelong_card(9004, 1, "준비", "중계학습관")
    details = {
        "9001": _lifelong_detail("9001", end_date="99.08.31", branch="상계학습관", status_application="internal"),
        "9002": _lifelong_detail("9002", end_date="99.08.31", branch="월계학습관"),
        "9003": _lifelong_detail("9003", end_date="99.08.31", branch="공릉학습관", status_application="external"),
        "8001": _lifelong_detail("8001", end_date="20.08.31", branch="하계학습관"),
        "9004": _lifelong_detail("9004", end_date="99.08.31", branch="중계학습관"),
    }
    fetched: list[str] = []

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        fetched.append(url)
        parsed = urlparse(url)
        if parsed.path == municipal.NOWON_LIFELONG_AJAX_PATH:
            query = parse_qs(parsed.query, keep_blank_values=True)
            assert query["areaCd"] == ["0"]
            assert query["searchOrderBy"] == ["BY_RECENT_UP"]
            page = int(query["pageIndex"][0])
            return _soup(_lifelong_page(first if page == 1 else second, total=5))
        program_id = parsed.path.rstrip("/").split("/")[-1]
        return _soup(details[program_id])

    monkeypatch.setattr(municipal, "session", DummySession)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    return fetched


def test_nowon_lifelong_full_snapshot_current_rows_and_actual_applications(
    lifelong_site: list[str],
) -> None:
    rows, parser, meta = municipal.collect_from_url(
        _lifelong_target(), timeout=5, max_depth=0, max_pages=5, detail_limit=10
    )

    assert parser == municipal.NOWON_LIFELONG_PARSER
    assert len(rows) == 4
    assert meta["total_count"] == meta["discovered_links"] == 5
    assert meta["pages"] == meta["declared_pages"] == 2
    assert meta["raw_row_count"] == 5
    assert meta["current_count"] == 4
    assert meta["expired_count"] == 1
    assert meta["detail_attempts"] == meta["detail_pages"] == 5
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta
    assert {row["provider_course_id"] for row in rows} == {
        f"{municipal.NOWON_LIFELONG_PROVIDER}:program:{program_id}"
        for program_id in ("9001", "9002", "9003", "9004")
    }
    assert all(row["prefer_incoming_provider_course_id"] is True for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["municipality_code"] == "1135000000" for row in rows)
    assert {row["branch"] for row in rows} == {
        "상계학습관", "월계학습관", "공릉학습관", "중계학습관"
    }
    internal = next(row for row in rows if row["provider_course_id"].endswith(":9001"))
    assert urlparse(internal["application_url"]).path.endswith("/termsAgree/9001/209901")
    external = next(row for row in rows if row["provider_course_id"].endswith(":9003"))
    assert external["application_url"] == (
        "https://apply.example.org/forms/9003?course=1&currentPage=1"
    )
    closed = next(row for row in rows if row["provider_course_id"].endswith(":9002"))
    scheduled = next(row for row in rows if row["provider_course_id"].endswith(":9004"))
    for row in (closed, scheduled):
        assert row["reservation_available"] is False
        assert "application_url" not in row
        assert row["raw_fields"]["clear_application_url"] is True
    assert len(lifelong_site) == 7


def test_nowon_lifelong_enrichment_adds_official_branch_location() -> None:
    row = {
        "branch": "임시 기관",
        "status": "SCHEDULED",
        "raw_url": "https://www.nowon.kr/nwll/web/edc/program/4194",
        "raw_fields": {},
    }
    detail = _lifelong_detail(
        "4194",
        end_date="99.08.31",
        branch="노원어르신상담센터",
    ).replace(
        "노원어르신상담센터 강의실",
        "노원사회적경제지원센터 3관 회의실(수락산로212-12 2층)",
    )

    valid, error = municipal.nowon_enrich_lifelong_detail(row, _soup(detail))

    assert valid is True
    assert error == ""
    assert row["branch"] == "노원어르신상담센터"
    assert row["address"] == (
        "서울특별시 노원구 수락산로 214, "
        "구립수락노인종합복지관 4층"
    )
    assert row["branch_lat"] == 37.6709103
    assert row["branch_lon"] == 127.0548074
    assert row["branch_location_verified"] is True
    assert row["venue_address"] == (
        "서울특별시 노원구 수락산로 212-12, 2층"
    )


def _fmcs_item(company: str, class_code: str, status: str) -> dict[str, Any]:
    return {
        "comcd": company,
        "comnm": f"노원 센터 {company}",
        "class_cd": class_code,
        "class_nm": f"노원 수강 {class_code}",
        "train_stime": "10:00",
        "train_etime": "12:00",
        "course_fee": "10000",
        "status": status,
        "target_age_name": "성인",
        "train_day_nm": "월수",
        "capa": "20",
        "reg_person": "3",
        "teacher_name": "노원 강사",
        "category1": "문화강좌",
        "category2": "생활교육",
    }


def _fmcs_detail(company: str, class_code: str, status: str) -> str:
    return f"""
    <html><body><div class="lctre_detail">
      <form id="form_lecture_reg">
        <input name="comcd" value="{company}" />
        <input name="classcd" value="{class_code}" />
        <input name="type" value="R" />
        <input name="status" value="{status}" />
        <input name="SecurityToken" value="secret-not-persisted" />
      </form>
      <dl>
        <dt>· 강좌명</dt><dd>노원 수강 {class_code}</dd>
        <dt>· 센터명</dt><dd>노원 센터 {company} / 02-0000-0000</dd>
        <dt>· 시간/요일</dt><dd>10:00 ~ 12:00 / 월수</dd>
        <dt>· 교육대상</dt><dd>성인</dd>
        <dt>· 강사명</dt><dd>노원 강사</dd>
        <dt>· 접수방식</dt><dd>선착접수</dd>
        <dt>· 신청인원/정원</dt><dd>3 / 20</dd>
      </dl>
      <table><thead><tr><th>선택</th><th>상품명</th><th>월수강료</th><th>수강기간</th></tr></thead>
        <tbody><tr><td></td><td>공식 상품 {class_code}</td><td>10,000원</td><td>1개월</td></tr></tbody>
      </table>
      <div class="tt_txt"><strong>강좌 안내</strong></div><div>공식 상세 {class_code}</div>
      <div class="tt_txt"><strong>강사 안내</strong></div>
    </div></body></html>
    """


@pytest.fixture
def fmcs_site(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(municipal, "NOWON_FMCS_PAGE_SIZE", 2)
    companies = [
        {"comcd": "NOWON02", "comnm": "노원 센터 NOWON02"},
        {"comcd": "NOWON03", "comnm": "노원 센터 NOWON03"},
        {"comcd": "NOWON04", "comnm": "노원 센터 NOWON04"},
    ]
    by_company = {
        "NOWON02": [
            _fmcs_item("NOWON02", "001", "R"),
            _fmcs_item("NOWON02", "002", "RW"),
            _fmcs_item("NOWON02", "003", "W"),
        ],
        "NOWON03": [_fmcs_item("NOWON03", "004", "E")],
        "NOWON04": [],
    }

    def request_json(
        _session: object,
        _root: str,
        endpoint: str,
        params: dict[str, Any],
        _method: str,
        _referer: str,
        _timeout: int,
    ) -> list[dict[str, Any]]:
        if endpoint == "rest/common/lecture_company":
            assert params == {}
            return companies
        assert endpoint == "rest/lecture/list"
        assert params["search_type"] == ""
        assert params["category_level"] == "9"
        items = by_company[params["company_code"]]
        page = int(params["page"])
        page_size = int(params["page_size"])
        result = [dict(item) for item in items[(page - 1) * page_size : page * page_size]]
        for item in result:
            item["total_count"] = len(items)
        return result

    fetched: list[str] = []

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        fetched.append(url)
        query = parse_qs(urlparse(url).query)
        assert query["type"] == ["R"]
        company = query["comcd"][0]
        class_code = query["classcd"][0]
        status = next(
            item["status"] for item in by_company[company] if item["class_cd"] == class_code
        )
        return _soup(_fmcs_detail(company, class_code, status))

    monkeypatch.setattr(municipal, "session", DummySession)
    monkeypatch.setattr(municipal, "fmcs_http_method", lambda *_args: "get")
    monkeypatch.setattr(municipal, "fmcs_request_json", request_json)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    return fetched


def test_nowon_fmcs_all_companies_pages_zero_center_and_official_ids(
    fmcs_site: list[str],
) -> None:
    rows, parser, meta = municipal.collect_from_url(
        _fmcs_target(), timeout=5, max_depth=0, max_pages=10, detail_limit=10
    )

    assert parser == municipal.NOWON_FMCS_PARSER
    assert len(rows) == 4
    assert meta["pages"] == 4
    assert meta["company_totals"] == {"NOWON02": 3, "NOWON03": 1, "NOWON04": 0}
    assert meta["company_pages"] == {"NOWON02": 2, "NOWON03": 1, "NOWON04": 1}
    assert meta["total_count"] == meta["discovered_links"] == 4
    assert meta["detail_attempts"] == meta["detail_pages"] == 4
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta
    assert {row["provider_course_id"] for row in rows} == {
        f"{municipal.NOWON_FMCS_PROVIDER}:class:NOWON02:001",
        f"{municipal.NOWON_FMCS_PROVIDER}:class:NOWON02:002",
        f"{municipal.NOWON_FMCS_PROVIDER}:class:NOWON02:003",
        f"{municipal.NOWON_FMCS_PROVIDER}:class:NOWON03:004",
    }
    assert all(row["prefer_incoming_provider_course_id"] is True for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["municipality_code"] == "1135000000" for row in rows)
    assert {row["status"] for row in rows} == {"OPEN", "WAITING", "SCHEDULED", "CLOSED"}
    for row in rows:
        assert parse_qs(urlparse(row["raw_url"]).query)["type"] == ["R"]
        assert "SecurityToken" not in row["raw_fields"]["detail_form"]
        if row["status"] in {"OPEN", "WAITING"}:
            assert row["application_url"] == row["raw_url"]
            assert row["reservation_available"] is True
        else:
            assert "application_url" not in row
            assert row["reservation_available"] is False
            assert row["raw_fields"]["clear_application_url"] is True
    assert len(fmcs_site) == 4


def _yaml_target(path: Path, provider: str) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return next(row for row in document["targets"] if row.get("provider") == provider)


def test_nowon_configs_overrides_and_full_snapshot_contract() -> None:
    lifelong = _yaml_target(
        ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml",
        municipal.NOWON_LIFELONG_PROVIDER,
    )
    fmcs = _yaml_target(
        ROOT / "config" / "crawl_targets" / "public_reservation.yaml",
        municipal.NOWON_FMCS_PROVIDER,
    )
    for target in (lifelong, fmcs):
        assert target["collection_category"] == "공공예약"
        assert target["domain_category"] == "교육·강좌"
        assert target["source_group"] == "municipal_reservation"
        assert target["service_group"] == "공공강좌"
        assert target["service_group_policy"] == "locked"
        assert target["full_snapshot_required"] is True
        assert target["municipality_code"] == "1135000000"

    assert generated.MAX_PAGES >= 350
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[municipal.NOWON_LIFELONG_PROVIDER] == (
        "--save-db", "--mark-stale", "--per-target-limit", "0",
        "--max-pages", "350", "--detail-limit", "2000",
    )
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[municipal.NOWON_FMCS_PROVIDER] == (
        "--save-db", "--mark-stale", "--per-target-limit", "0",
        "--max-pages", "20", "--detail-limit", "1000",
    )

    overrides = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_overrides.yaml").read_text(encoding="utf-8")
    )
    municipality = next(row for row in overrides["municipalities"] if row["code"] == "1135000000")
    candidates = {row["url"]: row for row in municipality["candidates"]}
    assert candidates[municipal.NOWON_LIFELONG_LIST_URL]["status"] == "candidate"
    assert candidates[municipal.NOWON_FMCS_LIST_URL]["status"] == "candidate"
    assert candidates["https://nwllc.sen.go.kr/"]["exclusion_reason"] == "separate_education_office_scope"
    assert (
        candidates["https://nwllc.sen.go.kr/nwllc/html.do?menu_idx=93"]["exclusion_reason"]
        == "separate_education_office_scope"
    )
