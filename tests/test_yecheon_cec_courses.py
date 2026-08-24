from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


LIST_PAGE = """
<html><body>
  <ul class="lc_lect_ul">
    <li>
      <div class="li_in">
        <div class="li_in_img" onclick="goView('13580002', '2099', '20580003', '209930018');"
             style="background-image:url(/data/course.jpg);"></div>
        <div class="li_in_text">
          <div class="stat_nm"><div class="lc_lect_stat_nm">마감</div></div>
          <div class="li_gs_kind2_nm">예술과정</div>
          <div class="li_title" onclick="goView('13580002', '2099', '20580003', '209930018');">보태니컬 아트</div>
          <div class="li_lect_hope_tfee">70,000<span class="won">원</span><div class="matc">재료비 20,000원 별도</div></div>
          <div class="li_sub_text">
            <dl><dt>교육기간</dt><dd>2099-07-07(화) ~ 2099-08-04(화)</dd></dl>
            <dl><dt>교육시간</dt><dd>18:30 ~ 20:30(화)</dd></dl>
            <dl><dt>신청기간</dt><dd>2099-06-08(월) ~ 2099-06-14(일)</dd></dl>
          </div>
          <div class="li_sub_sc_cnt_text">7/10명</div>
        </div>
      </div>
    </li>
    <li>
      <div class="li_title" onclick="goView('13580002', '2099', '20580003', '209930018');">보태니컬 아트</div>
    </li>
    <li>
      <div class="li_title" onclick="goView('13580002', '2099', '20580003', '209930099');">테스트</div>
      <div class="li_sub_text"><dl><dt>교육기간</dt><dd>2099-01-01 ~ 2099-12-31</dd></dl></div>
    </li>
    <li>
      <div class="li_title" onclick="goView('13580002', '2020', '20580003', '202030001');">종료된 강좌</div>
      <div class="li_sub_text"><dl><dt>교육기간</dt><dd>2020-01-01 ~ 2020-01-31</dd></dl></div>
    </li>
  </ul>
</body></html>
"""


DETAIL_PAGE = """
<html><body>
  <table class="lecture-detail"><tbody>
    <tr><th>강좌명</th><td>보태니컬 아트</td></tr>
    <tr><th>정원</th><td>10명</td><th>학습비</th><td>70,000원</td></tr>
    <tr><th>교육기간</th><td>2099-07-07 ~ 2099-08-04</td><th>교육시간</th><td>18:30 ~ 20:30(화)</td></tr>
    <tr><th>교육주수</th><td>5</td><th>총시수</th><td>10</td></tr>
    <tr><th>수강신청기간</th><td>2099-06-08 09:00 ~ 2099-06-14 22:00</td></tr>
    <tr><th>강사</th><td>김강사</td><th>강의실</th><td>청남교육관 청남교육관204호</td></tr>
    <tr><th>교육대상</th><td>회원 및 지역민</td></tr>
    <tr><th>강좌소개</th><td>식물을 관찰하고 세밀하게 그리는 강좌</td></tr>
    <tr><th>재료비</th><td>20,000원</td></tr>
    <tr><th>재료비상세내역</th><td>드로잉 재료 일체</td></tr>
  </tbody></table>
  <table class="weekly"><thead><tr><th>주차</th><th>강의일자</th><th>강의내용</th><th>강사명</th></tr></thead>
    <tbody>
      <tr><td>1</td><td>2099-07-07</td><td>오리엔테이션</td><td>김강사</td></tr>
      <tr><td>2</td><td>2099-07-14</td><td>기초 드로잉</td><td>김강사</td></tr>
    </tbody>
  </table>
</body></html>
"""


NO_CURRENT_PAGE = """
<html><body><ul class="lc_lect_ul">
  <li><div class="li_title" onclick="goView('13580002', '2099', '20580003', '209930099');">테스트</div>
      <div class="li_sub_text"><dl><dt>교육기간</dt><dd>2099-01-01 ~ 2099-12-31</dd></dl></div></li>
  <li><div class="li_title" onclick="goView('13580002', '2020', '20580003', '202030001');">종료된 강좌</div>
      <div class="li_sub_text"><dl><dt>교육기간</dt><dd>2020-01-01 ~ 2020-01-31</dd></dl></div></li>
</ul></body></html>
"""


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="MUNI_CEC_YECHEON_TEST",
        name="예천군 평생학습관 교육과정",
        branch="경상북도 예천군",
        url="https://cec.gknu.ac.kr/yecheon?mid=%2Fyecheon%2Fcomm%2Fcomm2",
        source="test",
        priority=1,
        region="경상북도 예천군",
        extra={},
    )


def test_yecheon_dispatches_to_current_term_and_maps_unique_detail(monkeypatch) -> None:
    fetched: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 8
        fetched.append(url)
        return _soup(DETAIL_PAGE if urlparse(url).path.endswith("/view.php") else LIST_PAGE)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, parser, meta = municipal.collect_from_url(
        _target(),
        timeout=8,
        max_depth=0,
        max_pages=5,
        detail_limit=5,
    )

    assert parser == "yecheon_cec_current_term+detail"
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "보태니컬 아트"
    assert row["provider_course_id"] == (
        "MUNI_CEC_YECHEON_TEST:lect:13580002:2099:20580003:209930018"
    )
    assert row["branch"] == municipal.YECHEON_CEC_BRANCH
    assert row["branch_code"] == municipal.YECHEON_CEC_BRANCH_CODE
    assert row["venue_name"] == "청남교육관 204호"
    assert row["room"] == "청남교육관 204호"
    assert row["target"] == "회원 및 지역민"
    assert row["instructor"] == "김강사"
    assert row["fee"] == "70,000원"
    assert row["material_fee"] == "20,000원"
    assert row["capacity_current"] == 7
    assert row["capacity_total"] == 10
    assert row["status"] == "CLOSED"
    assert row["period"] == "2099-07-07 ~ 2099-08-04"
    assert row["schedule_dates"] == ["2099-07-07", "2099-07-14"]
    assert row["image_url"] == "https://cec.gknu.ac.kr/data/course.jpg"
    assert "식물을 관찰하고" in row["description"]
    assert row["raw_fields"]["lecture_no"] == "209930018"
    assert row["raw_fields"]["education_weeks"] == "5"

    detail_query = parse_qs(urlparse(row["raw_url"]).query)
    assert detail_query == {
        "mid": [municipal.YECHEON_CEC_MID],
        "search_camp_dvcd": ["13580002"],
        "search_year": ["2099"],
        "search_hakgi": ["20580003"],
        "search_lect_no": ["209930018"],
    }
    assert urlparse(fetched[0]).path == municipal.YECHEON_CEC_LIST_PATH
    assert parse_qs(urlparse(fetched[0]).query)["mid"] == [municipal.YECHEON_CEC_MID]
    assert len(fetched) == 2
    assert meta["pages"] == 1
    assert meta["detail_pages"] == 1
    assert meta["discovered_links"] == 3
    assert meta["reservation_discovery_links"] == 1
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is False


def test_yecheon_test_and_expired_cards_produce_complete_no_current_data(monkeypatch) -> None:
    fetched: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        fetched.append(url)
        return _soup(NO_CURRENT_PAGE)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, parser, meta = municipal.collect_yecheon_cec_courses(
        _target(), timeout=6, max_pages=3, detail_limit=3
    )

    assert parser == "yecheon_cec_current_term+detail"
    assert rows == []
    assert len(fetched) == 1
    assert meta["pages"] == 1
    assert meta["detail_pages"] == 0
    assert meta["discovered_links"] == 2
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == "all current-term courses are expired or test rows"


def test_yecheon_go_view_key_rejects_non_numeric_or_incomplete_values() -> None:
    assert municipal.yecheon_cec_go_view_key(
        "goView('13580002', '2026', '20580003', '202630018')"
    ) == ("13580002", "2026", "20580003", "202630018")
    assert municipal.yecheon_cec_go_view_key("goView('13580002', '2026')") is None
    assert municipal.yecheon_cec_go_view_key(
        "goView('13580002', '2026', '20580003', '../bad')"
    ) is None
