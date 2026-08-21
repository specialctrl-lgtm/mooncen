from __future__ import annotations

import json
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


def _nx_target(provider: str) -> municipal.CrawlTarget:
    if provider == municipal.EUNPYEONG_EDU_PROVIDER:
        return municipal.CrawlTarget(
            provider=provider,
            name="은평구 평생교육 전체 강좌",
            branch="은평구평생학습관",
            url=municipal.EUNPYEONG_EDU_LIST_URL,
            source="test",
            priority=1,
            region="서울특별시 은평구",
        )
    return municipal.CrawlTarget(
        provider=provider,
        name="은평배움모아 고유 강좌",
        branch="은평배움모아",
        url=municipal.EUNPYEONG_EPLEARNING_LIST_URL,
        source="test",
        priority=1,
        region="서울특별시 은평구",
    )


def _efmc_target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=municipal.EUNPYEONG_EFMC_PROVIDER,
        name="은평구시설관리공단 접수 중 강좌",
        branch="은평구시설관리공단",
        url=municipal.EUNPYEONG_EFMC_LIST_URL,
        source="test",
        priority=1,
        region="서울특별시 은평구",
    )


def _nx_card(
    menu_id: str,
    status: str,
    ep_idx: int,
    em_idx: int,
    title: str,
    end_date: str,
    venue: str,
    button: str,
) -> str:
    return f"""
    <li><a href="read2.nx?M2_IDX={menu_id}&amp;SC_STATUS={status}&amp;page=1&amp;EP_IDX={ep_idx}&amp;EM_IDX={em_idx}">
      <div class="img-wrap1"><img src="/thumb/{ep_idx}.jpg" alt="{title}" /></div>
      <div class="txt-wrap">
        <p class="title">{title}</p>
        <p class="ct">공식 프로그램 설명 {ep_idx}</p>
        <p class="schedule">학습기간 : 2099-01-01(목) 10:00 ~ {end_date}(금) 12:00</p>
        <p class="place">장소 : {venue}</p>
        <div class="btn-wrap"><span class="btn">{button}</span></div>
      </div>
    </a></li>
    """


def _nx_page(menu_id: str, status: str, cards: str) -> str:
    return f"""
    <html><body><form>
      <input name="M2_IDX" value="{menu_id}" />
      <input name="SC_STATUS" value="{status}" />
    </form><ul id="edu_lst" class="nx-edu-lst-type3">{cards}</ul></body></html>
    """


def _nx_no_data(menu_id: str, status: str) -> str:
    return _nx_page(menu_id, status, '<li class="nodata">등록된 정보가 없습니다.</li>')


def _nx_detail(
    title: str,
    em_idx: int,
    end_date: str,
    venue: str,
    *,
    apply: bool,
) -> str:
    control = f'<a href="javascript:onclLogin(1, {em_idx});">신청하기</a>' if apply else ""
    return f"""
    <html><body>
      <h4 class="nx-edu-tit1">{title}</h4>
      <ul class="nx-detail">
        <li><h6 class="nx-detail-tit">학습기간</h6>2099-01-01(목) 10:00 ~ {end_date}(금) 12:00</li>
        <li><h6 class="nx-detail-tit">모집기간</h6>2098-12-01(화) 09:00 ~ 2098-12-31(목) 18:00</li>
        <li><h6 class="nx-detail-tit">학습장소</h6>{venue}</li>
        <li><h6 class="nx-detail-tit">지역</h6>은평구</li>
        <li><h6 class="nx-detail-tit">대상</h6>은평구민</li>
        <li><h6 class="nx-detail-tit">분야별</h6>생활교육</li>
      </ul>
      {control}
      <input name="SecurityToken" value="must-not-be-persisted" />
    </body></html>
    """


@pytest.fixture
def eunpyeong_primary_site(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(municipal, "EUNPYEONG_NX_PAGE_SIZE", 2)
    courses = {
        "E": [
            (101, 1001, "[은평구미래교육센터] 온라인 학습 컨설팅", "2099-03-01", "온라인으로 진행", "신청하기", True),
            (102, 1002, "[영어도서관] 영어 그림책", "2099-03-02", "은평어린이영어도서관", "신청하기", True),
            (103, 1003, "은평 평생학습 특강", "2099-03-03", "은평구평생학습관 3층 채움실2", "신청하기", True),
        ],
        "B": [
            (201, 2001, "갈현동 주민 강좌", "2099-04-01", "갈현2동 주민센터 소공자", "신청하기", True),
            (202, 2002, "문화예술 공방", "2099-04-02", "꿈마루 문화센터", "신청하기", True),
        ],
        "C": [
            (301, 3001, "현재 학습 중 강좌", "2099-05-01", "은평구평생학습관", "학습중", False),
            (302, 3002, "지난 강좌 하나", "2020-01-01", "은평구평생학습관", "학습종료", False),
            (303, 3003, "지난 강좌 둘", "2020-02-01", "은평구평생학습관", "학습종료", False),
        ],
    }
    details = {
        (str(ep), str(em)): _nx_detail(title, em, end, venue, apply=apply)
        for values in courses.values()
        for ep, em, title, end, venue, _button, apply in values
    }
    fetched: list[str] = []

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/list.nx"):
            status = query["SC_STATUS"][0]
            page = int(query["page"][0])
            values = courses[status]
            page_count = (len(values) + 1) // 2
            selected = values[(min(page, page_count) - 1) * 2 : min(page, page_count) * 2]
            cards = "".join(
                _nx_card(
                    municipal.EUNPYEONG_EDU_MENU_ID,
                    status,
                    ep,
                    em,
                    title,
                    end,
                    venue,
                    button,
                )
                for ep, em, title, end, venue, button, _apply in selected
            )
            return _soup(_nx_page(municipal.EUNPYEONG_EDU_MENU_ID, status, cards))
        return _soup(details[(query["EP_IDX"][0], query["EM_IDX"][0])])

    monkeypatch.setattr(municipal, "session", DummySession)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    return fetched


def test_eunpyeong_nx_full_status_snapshot_filters_expired_and_keeps_only_real_applications(
    eunpyeong_primary_site: list[str],
) -> None:
    rows, parser, meta = municipal.collect_from_url(
        _nx_target(municipal.EUNPYEONG_EDU_PROVIDER),
        timeout=5,
        max_depth=0,
        max_pages=20,
        detail_limit=20,
    )

    assert parser == municipal.EUNPYEONG_EDU_PARSER
    assert meta["status_totals"] == {"E": 3, "B": 2, "C": 3}
    assert meta["status_pages"] == {"E": 2, "B": 1, "C": 2}
    assert meta["terminal_repeat_pages"] == {"E": 3, "B": 2, "C": 3}
    assert meta["total_count"] == meta["discovered_links"] == 8
    assert meta["current_count"] == len(rows) == 6
    assert meta["expired_count"] == 2
    assert meta["detail_attempts"] == meta["detail_pages"] == 6
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta
    assert len(eunpyeong_primary_site) == 14

    assert {row["provider_course_id"] for row in rows} == {
        f"{municipal.EUNPYEONG_EDU_PROVIDER}:program:{ep}:{em}"
        for ep, em in ((101, 1001), (102, 1002), (103, 1003), (201, 2001), (202, 2002), (301, 3001))
    }
    assert all(row["prefer_incoming_provider_course_id"] is True for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    assert all(row["municipality_code"] == "1138000000" for row in rows)
    assert {row["branch"] for row in rows} == {
        "은평구미래교육센터",
        "은평어린이영어도서관",
        "은평구평생학습관",
        "갈현2동 주민센터",
        "꿈마루 문화센터",
    }

    open_rows = [row for row in rows if row["raw_fields"]["status_code"] == "B"]
    assert len(open_rows) == 2
    for row in open_rows:
        assert row["application_url"] == row["raw_url"]
        assert row["reservation_available"] is True
        assert urlparse(row["application_url"]).path.endswith("/edu/read2.nx")
    for row in rows:
        if row not in open_rows:
            assert "application_url" not in row
            assert row["reservation_available"] is False
            assert row["raw_fields"]["clear_application_url"] is True
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "javascript:" not in serialized
    assert "SecurityToken" not in serialized
    assert "must-not-be-persisted" not in serialized


def test_eunpyeong_nx_fails_closed_if_a_short_page_is_followed_by_another_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(municipal, "EUNPYEONG_NX_PAGE_SIZE", 2)

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/list.nx"):
            status = query["SC_STATUS"][0]
            page = int(query["page"][0])
            ep = 900 + page
            card = _nx_card(
                municipal.EUNPYEONG_EDU_MENU_ID,
                status,
                ep,
                ep + 1000,
                f"비정상 페이지 {page}",
                "2099-12-31",
                "은평구평생학습관",
                "신청하기",
            )
            return _soup(_nx_page(municipal.EUNPYEONG_EDU_MENU_ID, status, card))
        raise AssertionError("incomplete pagination must fail before detail fetch")

    monkeypatch.setattr(municipal, "session", DummySession)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    rows, _parser, meta = municipal.collect_from_url(
        _nx_target(municipal.EUNPYEONG_EDU_PROVIDER),
        timeout=5,
        max_depth=0,
        max_pages=5,
        detail_limit=0,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["pagination_complete"] is False
    assert "page 1 exposed 1 rows before a later page" in meta["configured_collection_error"]


def test_eplearning_owned_source_collects_only_its_current_unique_course(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(municipal, "EUNPYEONG_EPLEARNING_PAGE_SIZE", 2)
    current = (501, 5001, "2026 은평구 지역특화 주민자치사업 - 몸펴기 생활운동", "2099-11-19", "고리마루 문화센터 (갈현1동 주민센터 맞은편)")
    expired = (502, 5002, "지난 은평배움모아 강좌", "2020-01-01", "고리마루 문화센터")

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/list.nx"):
            status = query["SC_STATUS"][0]
            if status in {"E", "B"}:
                return _soup(_nx_no_data(municipal.EUNPYEONG_EPLEARNING_MENU_ID, status))
            cards = "".join(
                _nx_card(
                    municipal.EUNPYEONG_EPLEARNING_MENU_ID,
                    "C",
                    ep,
                    em,
                    title,
                    end,
                    venue,
                    "학습중" if end.startswith("2099") else "학습종료",
                )
                for ep, em, title, end, venue in (current, expired)
            )
            return _soup(_nx_page(municipal.EUNPYEONG_EPLEARNING_MENU_ID, "C", cards))
        return _soup(_nx_detail(current[2], current[1], current[3], current[4], apply=False))

    monkeypatch.setattr(municipal, "session", DummySession)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    rows, parser, meta = municipal.collect_from_url(
        _nx_target(municipal.EUNPYEONG_EPLEARNING_PROVIDER),
        timeout=5,
        max_depth=0,
        max_pages=10,
        detail_limit=10,
    )

    assert parser == municipal.EUNPYEONG_EPLEARNING_PARSER
    assert meta["status_totals"] == {"E": 0, "B": 0, "C": 2}
    assert meta["current_count"] == 1
    assert meta["expired_count"] == 1
    assert meta["snapshot_complete"] is True
    assert len(rows) == 1
    assert rows[0]["provider_course_id"] == (
        f"{municipal.EUNPYEONG_EPLEARNING_PROVIDER}:program:501:5001"
    )
    assert rows[0]["branch"] == "고리마루 문화센터"
    assert rows[0]["status"] == "CLOSED"
    assert "application_url" not in rows[0]
    primary_fixture_titles = {
        "[은평구미래교육센터] 온라인 학습 컨설팅",
        "[영어도서관] 영어 그림책",
        "은평 평생학습 특강",
        "갈현동 주민 강좌",
        "문화예술 공방",
        "현재 학습 중 강좌",
    }
    assert rows[0]["title"] not in primary_fixture_titles


def test_eunpyeong_nx_classifies_official_undated_continuous_rows_without_saving_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(municipal, "EUNPYEONG_NX_PAGE_SIZE", 2)
    card = _nx_card(
        municipal.EUNPYEONG_EDU_MENU_ID,
        "C",
        700,
        7000,
        "기능테스트 상시 강좌",
        "2099-12-31",
        "은평구평생학습관",
        "학습중",
    )
    detail = """
    <html><body><h4 class="nx-edu-tit1">기능테스트 상시 강좌</h4>
      <ul class="nx-detail">
        <li><h6 class="nx-detail-tit">학습기간</h6>상시</li>
        <li><h6 class="nx-detail-tit">모집기간</h6>상시</li>
        <li><h6 class="nx-detail-tit">학습장소</h6>은평구평생학습관</li>
      </ul>
    </body></html>
    """

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/list.nx"):
            status = query["SC_STATUS"][0]
            if status in {"E", "B"}:
                return _soup(_nx_no_data(municipal.EUNPYEONG_EDU_MENU_ID, status))
            return _soup(_nx_page(municipal.EUNPYEONG_EDU_MENU_ID, status, card))
        return _soup(detail)

    monkeypatch.setattr(municipal, "session", DummySession)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    rows, _parser, meta = municipal.collect_from_url(
        _nx_target(municipal.EUNPYEONG_EDU_PROVIDER),
        timeout=5,
        max_depth=0,
        max_pages=10,
        detail_limit=10,
    )

    assert rows == []
    assert meta["total_count"] == 1
    assert meta["detail_attempts"] == meta["detail_pages"] == 1
    assert meta["undated_continuous_count"] == 1
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert "configured_collection_error" not in meta


def _fmcs_item(company: str, class_code: str, *, status: str = "R") -> dict[str, Any]:
    return {
        "comcd": company,
        "comnm": municipal.EUNPYEONG_EFMC_EXPECTED_COMPANIES[company],
        "class_cd": class_code,
        "class_nm": f"은평 접수 강좌 {class_code}",
        "train_stime": "10:00",
        "train_etime": "12:00",
        "course_fee": "10000",
        "status": status,
        "target_age_name": "성인",
        "train_day_nm": "월수",
        "capa": "20",
        "reg_person": "3",
        "teacher_name": "은평 강사",
        "category1": "생활체육",
        "category2": "교육",
    }


def _fmcs_detail(company: str, class_code: str) -> str:
    branch = municipal.EUNPYEONG_EFMC_EXPECTED_COMPANIES[company]
    return f"""
    <html><body><div class="proc_read">
      <form id="form_lecture_reg">
        <input name="comcd" value="{company}" />
        <input name="classcd" value="{class_code}" />
        <input name="type" value="R" />
        <input name="SecurityToken" value="secret-not-persisted" />
      </form>
      <table class="fit"><tbody>
        <tr><th>강좌명</th><td>은평 접수 강좌 {class_code}</td></tr>
        <tr><th>운영센터</th><td>{branch} / 02-0000-0000</td></tr>
        <tr><th>시간/요일</th><td>10:00 ~ 12:00 / 월수</td></tr>
        <tr><th>교육대상</th><td>성인</td></tr>
        <tr><th>강사명</th><td>은평 강사</td></tr>
        <tr><th>접수방식</th><td>온라인접수</td></tr>
        <tr><th>신청인원/정원</th><td>3 / 20</td></tr>
      </tbody></table>
      <table class="fee_list"><thead><tr>
        <th>선택</th><th>상품명</th><th>월 수강료</th><th>수강기간</th>
      </tr></thead>
        <tbody><tr>
          <td><input name="itemcd" value="ITEM-1" /></td>
          <td>공식 상품</td><td>10,000원</td><td>1개월</td>
        </tr></tbody>
      </table>
    </div></body></html>
    """


def _efmc_companies() -> list[dict[str, str]]:
    return [
        {"comcd": code, "comnm": name}
        for code, name in municipal.EUNPYEONG_EFMC_EXPECTED_COMPANIES.items()
    ]


def _efmc_category(company: str) -> dict[str, Any]:
    return {
        "company_code": company,
        "category_code": f"CAT-{company}",
        "category_name": f"{company} 교육 프로그램",
        "category_level": 1,
        "low_category_count": 1,
        "parent_category_name": f"{company} 교육 프로그램",
        "top_category_name": f"{company} 교육 프로그램",
    }


def test_eunpyeong_efmc_complete_open_fanout_accepts_zero_as_no_current_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests_seen: list[tuple[str, dict[str, Any]]] = []

    def request_json(
        _session: object,
        _root: str,
        endpoint: str,
        params: dict[str, Any],
        _method: str,
        _referer: str,
        _timeout: int,
    ) -> list[dict[str, Any]]:
        requests_seen.append((endpoint, dict(params)))
        if endpoint == "rest/common/company":
            return _efmc_companies()
        if endpoint == "rest/common/category":
            return [_efmc_category(params["company_code"])]
        assert endpoint == "rest/lecture/list"
        assert params["search_type"] == "R"
        assert params["category_level"] == "1"
        return []

    monkeypatch.setattr(municipal, "session", DummySession)
    monkeypatch.setattr(municipal, "fmcs_http_method", lambda *_args: "get")
    monkeypatch.setattr(municipal, "fmcs_request_json", request_json)
    rows, parser, meta = municipal.collect_from_url(
        _efmc_target(), timeout=5, max_depth=0, max_pages=10, detail_limit=10
    )

    assert parser == municipal.EUNPYEONG_EFMC_PARSER
    assert rows == []
    assert meta["company_names"] == municipal.EUNPYEONG_EFMC_EXPECTED_COMPANIES
    assert meta["category_count"] == 2
    assert meta["category_totals"] == {"EFMC01:CAT-EFMC01": 0, "EFMC02:CAT-EFMC02": 0}
    assert meta["pages"] == 2
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert "search_type=R" in meta["no_current_reason"]
    assert "configured_collection_error" not in meta
    assert all(
        params.get("search_type") != "E"
        for endpoint, params in requests_seen
        if endpoint == "rest/lecture/list"
    )


def test_eunpyeong_efmc_future_open_rows_use_stable_ids_branches_and_safe_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(municipal, "EUNPYEONG_EFMC_PAGE_SIZE", 2)
    items = [_fmcs_item("EFMC01", code) for code in ("001", "002", "003")]

    def request_json(
        _session: object,
        _root: str,
        endpoint: str,
        params: dict[str, Any],
        _method: str,
        _referer: str,
        _timeout: int,
    ) -> list[dict[str, Any]]:
        if endpoint == "rest/common/company":
            return _efmc_companies()
        if endpoint == "rest/common/category":
            return [_efmc_category(params["company_code"])]
        if params["company_code"] == "EFMC02":
            return []
        page = int(params["page"])
        result = [dict(item) for item in items[(page - 1) * 2 : page * 2]]
        for item in result:
            item["total_count"] = 3
        return result

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        query = parse_qs(urlparse(url).query)
        return _soup(_fmcs_detail(query["comcd"][0], query["classcd"][0]))

    monkeypatch.setattr(municipal, "session", DummySession)
    monkeypatch.setattr(municipal, "fmcs_http_method", lambda *_args: "get")
    monkeypatch.setattr(municipal, "fmcs_request_json", request_json)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    rows, parser, meta = municipal.collect_from_url(
        _efmc_target(), timeout=5, max_depth=0, max_pages=10, detail_limit=10
    )

    assert parser == municipal.EUNPYEONG_EFMC_PARSER
    assert len(rows) == 3
    assert meta["total_count"] == meta["discovered_links"] == 3
    assert meta["category_totals"] == {"EFMC01:CAT-EFMC01": 3, "EFMC02:CAT-EFMC02": 0}
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is False
    assert {row["provider_course_id"] for row in rows} == {
        f"{municipal.EUNPYEONG_EFMC_PROVIDER}:class:EFMC01:{code}"
        for code in ("001", "002", "003")
    }
    assert all(row["branch"] == "은평종합스포츠타운" for row in rows)
    assert all(row["branch_code"] == "EFMC01" for row in rows)
    assert all(
        row["address"] == "서울특별시 은평구 진관1로 40" for row in rows
    )
    assert all(row["venue_address"] == row["address"] for row in rows)
    assert all(row["branch_location_verified"] is True for row in rows)
    assert all(row["branch_location_confidence"] == 100 for row in rows)
    assert all(row["application_url"] == row["raw_url"] for row in rows)
    assert all(row["reservation_available"] is True for row in rows)
    assert all(row["period"] == "월 단위 상시 강좌" for row in rows)
    assert all(row["schedule_raw"] == "10:00 ~ 12:00 / 월수" for row in rows)
    assert all(row["target"] == "성인" for row in rows)
    assert all(row["fee"] == "10,000원" for row in rows)
    assert all(row["service_group_policy"] == "locked" for row in rows)
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "SecurityToken" not in serialized
    assert "secret-not-persisted" not in serialized


def test_eunpyeong_efmc_counts_and_excludes_ended_rows_from_mixed_open_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def request_json(
        _session: object,
        _root: str,
        endpoint: str,
        params: dict[str, Any],
        _method: str,
        _referer: str,
        _timeout: int,
    ) -> list[dict[str, Any]]:
        if endpoint == "rest/common/company":
            return _efmc_companies()
        if endpoint == "rest/common/category":
            return [_efmc_category(params["company_code"])]
        if params["company_code"] == "EFMC01":
            row = _fmcs_item("EFMC01", "ENDED", status="E")
            row["total_count"] = 1
            return [row]
        return []

    monkeypatch.setattr(municipal, "session", DummySession)
    monkeypatch.setattr(municipal, "fmcs_http_method", lambda *_args: "get")
    monkeypatch.setattr(municipal, "fmcs_request_json", request_json)
    rows, _parser, meta = municipal.collect_from_url(
        _efmc_target(), timeout=5, max_depth=0, max_pages=10, detail_limit=10
    )

    assert rows == []
    assert meta["total_count"] == meta["discovered_links"] == 1
    assert meta["open_count"] == 0
    assert meta["ended_count"] == 1
    assert meta["source_status_counts"] == {"E": 1}
    assert meta["no_current_data"] is True
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta


def test_eunpyeong_targets_are_strictly_owned_and_scheduled_as_full_snapshots() -> None:
    assert municipal.eunpyeong_nx_page_size(
        municipal.EUNPYEONG_NX_SOURCES[municipal.EUNPYEONG_EDU_PROVIDER]
    ) == 12
    assert municipal.eunpyeong_nx_page_size(
        municipal.EUNPYEONG_NX_SOURCES[municipal.EUNPYEONG_EPLEARNING_PROVIDER]
    ) == 10
    assert municipal.is_eunpyeong_nx_target(_nx_target(municipal.EUNPYEONG_EDU_PROVIDER))
    assert municipal.is_eunpyeong_nx_target(_nx_target(municipal.EUNPYEONG_EPLEARNING_PROVIDER))
    wrapper = _nx_target(municipal.EUNPYEONG_EPLEARNING_PROVIDER)
    wrapper.url = "https://www.eplearning.or.kr/edu/list.nx?M2_IDX=27474"
    assert not municipal.is_eunpyeong_nx_target(wrapper)
    detail = _nx_target(municipal.EUNPYEONG_EDU_PROVIDER)
    detail.url = "https://edu.eunpyeong.go.kr/edu/read2.nx?M2_IDX=15028&EP_IDX=1&EM_IDX=2"
    assert not municipal.is_eunpyeong_nx_target(detail)
    assert municipal.is_eunpyeong_efmc_target(_efmc_target())

    targets: dict[str, dict[str, Any]] = {}
    for filename in ("lifelong_learning.yaml", "public_reservation.yaml"):
        document = yaml.safe_load((ROOT / "config" / "crawl_targets" / filename).read_text(encoding="utf-8"))
        targets.update({item["provider"]: item for item in document["targets"]})
    for provider, url, parser in (
        (municipal.EUNPYEONG_EDU_PROVIDER, municipal.EUNPYEONG_EDU_LIST_URL, municipal.EUNPYEONG_EDU_PARSER),
        (
            municipal.EUNPYEONG_EPLEARNING_PROVIDER,
            municipal.EUNPYEONG_EPLEARNING_LIST_URL,
            municipal.EUNPYEONG_EPLEARNING_PARSER,
        ),
        (municipal.EUNPYEONG_EFMC_PROVIDER, municipal.EUNPYEONG_EFMC_LIST_URL, municipal.EUNPYEONG_EFMC_PARSER),
    ):
        target = targets[provider]
        assert target["url"] == url
        assert target["crawler_status"] == "ready"
        assert target["service_group"] == "공공강좌"
        assert target["service_group_policy"] == "locked"
        assert target["municipality_code"] == "1138000000"
        assert target["full_snapshot_required"] is True
        assert target["parser_assigned"] == parser

    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[municipal.EUNPYEONG_EDU_PROVIDER] == (
        "--save-db", "--mark-stale", "--per-target-limit", "0", "--max-pages", "80", "--detail-limit", "1000"
    )
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[municipal.EUNPYEONG_EPLEARNING_PROVIDER] == (
        "--save-db", "--mark-stale", "--per-target-limit", "0", "--max-pages", "20", "--detail-limit", "100"
    )
    assert generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[municipal.EUNPYEONG_EFMC_PROVIDER] == (
        "--save-db", "--mark-stale", "--per-target-limit", "0", "--max-pages", "100", "--detail-limit", "1000"
    )

    operational = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    eunpyeong_operational = {
        row["provider"]: row
        for row in operational["entries"]
        if any(municipality["code"] == "1138000000" for municipality in row["municipalities"])
    }
    assert set(eunpyeong_operational) == {
        municipal.EUNPYEONG_EDU_PROVIDER,
        municipal.EUNPYEONG_EPLEARNING_PROVIDER,
        municipal.EUNPYEONG_EFMC_PROVIDER,
    }
    assert eunpyeong_operational[municipal.EUNPYEONG_EDU_PROVIDER]["row_count"] == 61
    assert eunpyeong_operational[municipal.EUNPYEONG_EPLEARNING_PROVIDER]["row_count"] == 1
    assert eunpyeong_operational[municipal.EUNPYEONG_EFMC_PROVIDER]["validation_outcome"] == "no_current_data"
    assert eunpyeong_operational[municipal.EUNPYEONG_EFMC_PROVIDER]["no_current_data"] is True

    coverage = yaml.safe_load(
        (ROOT / "config" / "municipal_integrated_reservation_coverage.yaml").read_text(
            encoding="utf-8"
        )
    )
    municipality = next(row for row in coverage["municipalities"] if row["code"] == "1138000000")
    assert municipality["status"] == "promoted"
    assert set(municipality["owner_providers"]) == set(eunpyeong_operational)
    assert set(municipality["promoted_providers"]) == set(eunpyeong_operational)
    assert {
        evidence["provider"]
        for evidence in municipality["evidence"]
        if evidence["kind"] == "operational_allowlist"
    } == set(eunpyeong_operational)
