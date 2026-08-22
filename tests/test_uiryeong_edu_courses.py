from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


def _card(
    edu_idx: int,
    title: str,
    branch: str,
    status: str,
    period: str,
    capacity: str = "24명 / 0명 / 0명",
) -> str:
    return f"""
    <li class="active"><a href="/user/edu/view.uiryeong?eduIdx={edu_idx}&amp;menuCd=OLD&amp;pageNum=9">
      <div class="tit-icons-infos">
        <p class="tit">{title}</p>
        <p class="icons"><span class="icon">{status}</span><span class="icon">무료</span><span class="icon">선착순</span></p>
        <ul class="infos">
          <li class="info"><span class="btxt">장소</span><span class="stxt">{branch}</span></li>
          <li class="info"><span class="btxt">정원/신청/대기</span><span class="stxt">{capacity}</span></li>
          <li class="info"><span class="btxt">접수기간</span><span class="stxt">2099-07-01 ~ 2099-07-31</span></li>
          <li class="info"><span class="btxt">교육기간</span><span class="stxt">{period}</span></li>
        </ul>
      </div>
    </a></li>
    """


def _list_page(cards: str, total: int, last_page: int = 2) -> str:
    pages = "".join(
        f'<a href="#" onclick="pageChange(\'{page}\'); return false;">{page}</a>'
        for page in range(1, last_page + 1)
    )
    return f"""
    <html><body>
      <p class="total">총 {total}건</p>
      <div id="print-contents">
        <div class="board-list info-edu"><ul class="list">{cards}</ul></div>
      </div>
      <div class="pagination">{pages}</div>
    </body></html>
    """


LIST_PAGE_1 = _list_page(
    _card(
        192,
        "스마트폰 사진 편집",
        "종합사회복지관 3층 정보화교육장",
        "접수중",
        "2099-09-01 ~ 2099-09-05",
    )
    + _card(
        182,
        "Google Gemini AI",
        "동부사회복지관 2층 정보화교육장",
        "접수대기",
        "2099-08-01 ~ 2099-08-05",
        "20명 / 3명 / 1명",
    ),
    total=3,
)

LIST_PAGE_2 = _list_page(
    _card(
        180,
        "종료된 한글2020",
        "종합사회복지관 3층 정보화교육장",
        "접수마감",
        "2020-07-13 ~ 2020-07-17",
    ),
    total=3,
)

DETAIL_192 = """
<html><body><div class="board-view info-edu">
  <div class="point-info">
    <p class="tit">스마트폰 사진 편집</p>
    <p class="icons"><span class="icon">접수중</span><span class="icon">무료</span><span class="icon">선착순</span></p>
    <ul class="infos">
      <li><span class="btxt">장소</span><span class="stxt">종합사회복지관 3층 정보화교육장</span></li>
      <li><span class="btxt">접수기간</span><span class="stxt">2099-07-01 ~ 2099-07-31</span></li>
      <li><span class="btxt">교육기간</span><span class="stxt">2099-09-01 ~ 2099-09-05</span></li>
      <li><span class="btxt">교육시간</span><span class="stxt">월~금 10:00~12:00</span></li>
      <li><span class="btxt">신청인원 / 모집인원 (대기인원)</span><span class="stxt">4/24명 (대기인원 : 2명)</span></li>
      <li><span class="btxt">담당부서 / 문의전화</span><span class="stxt">행정과 전산정보팀 / 055-570-2154</span></li>
    </ul>
    <div class="btns"><a class="reser" href="/index.uiryeong?menuCd=DOM_000000701001001000&amp;reserType=EDU&amp;reserTypeIdx=192">신청</a></div>
  </div>
  <div class="detail-info"><ul class="map-infos"><li class="loca"><span class="stxt">경남 의령군 의령읍 의병로8길 44</span></li></ul></div>
</div></body></html>
"""

DETAIL_182 = """
<html><body><div class="board-view info-edu">
  <div class="point-info">
    <p class="tit">Google Gemini AI</p>
    <p class="icons"><span class="icon">접수대기</span><span class="icon">무료</span><span class="icon">선착순</span></p>
    <ul class="infos">
      <li><span class="btxt">장소</span><span class="stxt">동부사회복지관 2층 정보화교육장</span></li>
      <li><span class="btxt">접수기간</span><span class="stxt">2099-07-01 ~ 2099-07-31</span></li>
      <li><span class="btxt">교육기간</span><span class="stxt">2099-08-01 ~ 2099-08-05</span></li>
      <li><span class="btxt">신청인원 / 모집인원 (대기인원)</span><span class="stxt">3/20명 (대기인원 : 1명)</span></li>
    </ul>
  </div>
  <div class="detail-info"><ul class="map-infos"><li class="loca"><span class="stxt">경남 의령군 부림면 신번로 181</span></li></ul></div>
</div></body></html>
"""


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="MUNI_UIRYEONG_INFO_TEST",
        name="의령군 정보화교육 예약신청",
        branch="경상남도 의령군",
        url=(
            "https://www.uiryeong.go.kr/user/edu/list.uiryeong?"
            "menuCd=DOM_000000701001000000&eduCategory=1&contentsSid=463"
        ),
        source="test",
        priority=1,
        region="경상남도 의령군",
        extra={},
    )


def test_uiryeong_dispatch_paginates_and_maps_stable_course_branch_and_apply_url(monkeypatch) -> None:
    fetched: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.UIRYEONG_EDU_DETAIL_PATH:
            return _soup(DETAIL_192 if query["eduIdx"] == ["192"] else DETAIL_182)
        return _soup(LIST_PAGE_2 if query["pageNum"] == ["2"] else LIST_PAGE_1)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=5, detail_limit=5
    )

    assert parser == "uiryeong_edu_list+detail"
    assert len(rows) == 2
    by_id = {row["raw_fields"]["edu_idx"]: row for row in rows}
    main = by_id["192"]
    assert main["provider_course_id"] == "MUNI_UIRYEONG_INFO_TEST:edu:192"
    assert main["raw_url"] == (
        "https://www.uiryeong.go.kr/user/edu/view.uiryeong?"
        "eduIdx=192&menuCd=DOM_000000701001000000"
    )
    assert main["branch"] == "종합사회복지관 3층 정보화교육장"
    assert main["branch_code"] == "UIRYEONG_INFO_MAIN"
    assert main["address"] == "경남 의령군 의령읍 의병로8길 44"
    assert main["capacity_current"] == 4
    assert main["capacity_total"] == 24
    assert main["waitlist_current"] == 2
    assert main["phone"] == "055-570-2154"
    assert main["application_url"] == (
        "https://www.uiryeong.go.kr/index.uiryeong?"
        "menuCd=DOM_000000701001001000&reserType=EDU&reserTypeIdx=192"
    )
    assert main["reservation_available"] is True

    east = by_id["182"]
    assert east["branch"] == "동부사회복지관 2층 정보화교육장"
    assert east["branch_code"] == "UIRYEONG_INFO_EAST"
    assert east["address"] == "경남 의령군 부림면 신번로 181"
    assert east["status"] == "SCHEDULED"
    assert east["reservation_available"] is False
    assert "application_url" not in east

    list_pages = [
        parse_qs(urlparse(url).query)["pageNum"][0]
        for url in fetched
        if urlparse(url).path == municipal.UIRYEONG_EDU_LIST_PATH
    ]
    assert list_pages == ["1", "2"]
    assert meta["pages"] == 2
    assert meta["detail_pages"] == 2
    assert meta["discovered_links"] == 3
    assert meta["reservation_discovery_links"] == 2
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is False


def test_uiryeong_canonical_detail_url_uses_only_edu_idx_and_menu() -> None:
    first, first_idx = municipal.canonical_uiryeong_edu_detail_url(
        "https://www.uiryeong.go.kr/user/edu/list.uiryeong?pageNum=1&searchText=AI",
        "/user/edu/view.uiryeong?eduIdx=192&pageNum=1&menuCd=OLD#section",
    )
    second, second_idx = municipal.canonical_uiryeong_edu_detail_url(
        "https://www.uiryeong.go.kr/user/edu/list.uiryeong?pageNum=2",
        "/user/edu/view.uiryeong?menuCd=OTHER&eduIdx=192&searchText=photo",
    )

    assert first_idx == second_idx == "192"
    assert first == second == (
        "https://www.uiryeong.go.kr/user/edu/view.uiryeong?"
        "eduIdx=192&menuCd=DOM_000000701001000000"
    )


def test_uiryeong_no_current_is_true_only_after_all_pages_are_complete(monkeypatch) -> None:
    expired_page_1 = _list_page(
        _card(
            20,
            "종료 강좌 1",
            "종합사회복지관 3층 정보화교육장",
            "접수마감",
            "2020-01-01 ~ 2020-01-02",
        ),
        total=2,
    )
    expired_page_2 = _list_page(
        _card(
            19,
            "종료 강좌 2",
            "동부사회복지관 2층 정보화교육장",
            "접수마감",
            "2020-01-03 ~ 2020-01-04",
        ),
        total=2,
    )

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        page = parse_qs(urlparse(url).query)["pageNum"][0]
        return _soup(expired_page_2 if page == "2" else expired_page_1)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    capped_rows, _parser, capped_meta = municipal.collect_uiryeong_edu_courses(
        _target(), timeout=5, max_pages=1, detail_limit=0
    )
    assert capped_rows == []
    assert capped_meta["pagination_complete"] is False
    assert capped_meta["no_current_data"] is False

    complete_rows, _parser, complete_meta = municipal.collect_uiryeong_edu_courses(
        _target(), timeout=5, max_pages=5, detail_limit=0
    )
    assert complete_rows == []
    assert complete_meta["pages"] == 2
    assert complete_meta["discovered_links"] == 2
    assert complete_meta["pagination_complete"] is True
    assert complete_meta["no_current_data"] is True
    assert complete_meta["no_current_reason"] == "all listed courses are expired"
