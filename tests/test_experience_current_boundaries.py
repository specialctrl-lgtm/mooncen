from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


class _FakeSession:
    def __init__(self) -> None:
        self.headers = {}

    def close(self) -> None:
        pass


def _target(provider: str, url: str, branch: str = "테스트 기관") -> municipal.CrawlTarget:
    return municipal.CrawlTarget(provider, provider, branch, url, "test")


def test_gbe_uses_curr_page_and_page_index_as_page_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = """
    <table><tbody><tr>
      <td>
        <p>[특별교육과정] 미래 체험</p>
        접수기간 : 2099/07/24 09:00 ~ 2099/07/26 17:59<br>
        강좌기간 : 2099/09/05 ~ 2099/09/12 (09:00 ~ 15:20)
      </td>
      <td>신청 : 1 명 / 20 명</td><td>인터넷</td><td></td>
      <td><button data-id="1457">접수중</button></td>
    </tr></tbody></table>
    """
    requested: list[str] = []
    monkeypatch.setattr(municipal, "session", _FakeSession)
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, url, timeout: (
            requested.append(url) or BeautifulSoup(html, "lxml")
        ),
    )

    rows, parser, meta = municipal.collect_gbe_eq_list(
        _target(
            "MUNI_WWW_GBE_KR_98673AC8",
            "https://www.gbe.kr/uj/eq/view/selectEqList.do?mi=22841",
        ),
        timeout=10,
        max_pages=100,
    )

    query = parse_qs(urlparse(requested[0]).query)
    assert query["currPage"] == ["1"]
    assert query["pageIndex"] == ["20"]
    assert parser == "gbe_eq_table"
    assert len(rows) == 1
    assert rows[0]["provider_course_id"] == "1457"
    assert rows[0]["fee"] == "별도 안내"
    assert rows[0]["target"] == "전체"
    assert rows[0]["period"] == "2099-09-05 ~ 2099-09-12"
    assert rows[0]["schedule_raw"] == "09:00 ~ 15:20"
    assert meta["pagination_complete"] is True


def test_honam_stops_before_a_fully_archived_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_page = (
        '<ul class="lst type_card">'
        + "".join(
            f"""
            <li>
              <span class="type1">접수중</span><span class="type2">무료</span>
              <span class="tit">갯벌 생태 체험 {index}</span>
              <span class="n1">이용대상 : 가족</span>
              <span class="n2">접수기간 : 2099.07.22 09:00~2099.08.28 12:00</span>
              <span class="n3">교육시간 : 90분</span>
              <span class="n4">접수방법 : 온라인접수</span>
              <a onclick="fn_detail('future-{index}')">예약하기</a>
            </li>
            """
            for index in range(1, 10)
        )
        + "</ul>"
    )
    archived_page = """
    <ul class="lst type_card"><li>
      <span class="type1">마감</span><span class="type2">무료</span>
      <span class="tit">종료된 체험</span>
      <span class="n2">접수기간 : 2020.01.01~2020.01.02</span>
      <a onclick="fn_detail('old-1')">상세</a>
    </li></ul>
    """
    detail_page = "<html><body><dl><dt>교육장소</dt><dd>체험관</dd></dl></body></html>"
    requested: list[str] = []

    def fetch(_session, url: str, timeout: int):
        requested.append(url)
        if "edu_id=future-" in url:
            return BeautifulSoup(detail_page, "lxml")
        page = parse_qs(urlparse(url).query).get("pageIndex", ["1"])[0]
        return BeautifulSoup(
            current_page if page == "1" else archived_page,
            "lxml",
        )

    monkeypatch.setattr(municipal, "session", _FakeSession)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)

    rows, parser, meta = municipal.collect_honam_bio_resources(
        _target(
            "HONAM_BIOLOGICAL_RESOURCES",
            "https://resve.hnibr.re.kr/index.do?menu_id=00000440",
        ),
        timeout=10,
        max_pages=100,
        detail_limit=1000,
    )

    assert parser == "honam_bio_cards"
    assert len(rows) == 9
    assert all(row["category"] == "생물·생태 체험" for row in rows)
    assert all(row["fee"] == "무료" for row in rows)
    assert all(row["venue_name"] for row in rows)
    assert meta["pages"] == 2
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert not any("edu_id=old-1" in url for url in requested)


def test_cnc_collects_current_list_item_and_detail_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_page = """
    <ul><li><a href="?mode=V&amp;mng_no=9536">
      <div class="listcont"><div class="thumb"><img alt="가을꽃박람회" src="/flower.jpg"></div>
      <div class="listbox"><div class="cate">축제/문화행사</div><ul>
        <li><span>태안군</span>가을꽃박람회</li>
        <li>2099-09-17 ~ 2099-11-01 09:00</li>
        <li>네이처월드</li>
      </ul></div></div>
    </a></li></ul>
    """
    detail_page = """
    <div id="contents"><ul class="listbox">
      <li><b>행사명</b>가을꽃박람회</li>
      <li><b>분야</b>축제/문화행사</li>
      <li><b>기간</b>2099-09-17 ~ 2099-11-01</li>
      <li><b>장소</b>네이처월드</li>
      <li><b>시작 시간</b>09:00</li>
      <li><b>유/무료 여부</b>유료</li>
      <li><b>비용</b>10,000원</li>
      <li><b>대상</b>전 연령</li>
    </ul></div>
    """
    calls: list[str] = []

    def fetch(_session, url: str, timeout: int):
        calls.append(url)
        query = parse_qs(urlparse(url).query)
        if query.get("mode") == ["V"]:
            return BeautifulSoup(detail_page, "lxml")
        if query.get("GotoPage") == ["1"]:
            return BeautifulSoup(list_page, "lxml")
        return BeautifulSoup("<html></html>", "lxml")

    monkeypatch.setattr(municipal, "session", _FakeSession)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)

    rows, parser, meta = municipal.collect_cnc_culture_events(
        _target(
            "MUNI_CNC_CACF_OR_KR_7A12B48E",
            "https://cnc.cacf.or.kr/main/html/sub02/0201.html?mode=V&mng_no=5595",
        ),
        timeout=10,
        max_pages=100,
        detail_limit=1000,
    )

    assert parser == "cnc_culture_list_detail"
    assert len(rows) == 1
    assert rows[0]["target"] == "전 연령"
    assert rows[0]["fee"] == "10,000원"
    assert rows[0]["period"] == "2099-09-17 ~ 2099-11-01"
    assert rows[0]["venue_name"] == "네이처월드"
    assert rows[0]["category"] == "축제/문화행사"
    assert rows[0]["schedule_raw"] == "09:00"
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True


def test_nihc_empty_sections_are_a_complete_no_current_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(municipal, "session", _FakeSession)
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, url, timeout: BeautifulSoup("<html></html>", "lxml"),
    )

    rows, _parser, meta = municipal.collect_national_intangible_heritage_center(
        _target(
            "NATIONAL_INTANGIBLE_HERITAGE_CENTER",
            "https://www.nihc.go.kr/planweb/board/list.9is",
        ),
        timeout=10,
        max_pages=2,
        detail_limit=100,
    )

    assert rows == []
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True


def test_home_pen_experience_route_uses_the_official_branch_table_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = (
        [{"title": "부산 체험"}],
        "jne_experience_branch_table",
        {"pagination_complete": True},
    )
    monkeypatch.setattr(
        municipal,
        "collect_jne_experiences_by_branch",
        lambda target, timeout, max_pages: expected,
    )

    result = municipal.collect_from_url(
        _target(
            "MUNI_HOME_PEN_GO_KR_92635850",
            "https://home.pen.go.kr/yeyak/exprn/selectExprnList.do?mi=14438",
        ),
        timeout=10,
        max_pages=100,
        detail_limit=1000,
    )

    assert result == expected


def test_home_pen_experience_uses_official_branch_location_not_branch_name_as_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_page = """
    <table><tbody><tr>
      <td>1</td>
      <td>해운대도서관</td>
      <td>
        <a class="viewExprnInfo"
           data-id="1599"
           data-period-id="6030"
           data-rssysid="haeundaelib">여름 독서 체험</a>
      </td>
      <td>2099/08/01 ~ 2099/08/02</td>
      <td>2099/07/01 ~ 2099/07/20</td>
      <td>초등학생</td>
      <td>온라인</td>
      <td>접수중</td>
    </tr></tbody></table>
    """

    monkeypatch.setattr(municipal, "session", _FakeSession)
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, url, timeout: BeautifulSoup(
            list_page if parse_qs(urlparse(url).query)["currPage"] == ["1"] else "<table></table>",
            "lxml",
        ),
    )

    rows, parser, meta = municipal.collect_jne_experiences_by_branch(
        _target(
            "MUNI_HOME_PEN_GO_KR_92635850",
            "https://home.pen.go.kr/yeyak/exprn/selectExprnList.do?mi=14438",
        ),
        timeout=10,
        max_pages=2,
    )

    assert parser == "jne_experience_branch_table"
    assert meta["pagination_complete"] is True
    assert len(rows) == 1
    assert rows[0]["branch_address"] == "부산광역시 해운대구 양운로 183"
    assert rows[0]["branch_lat"] == pytest.approx(35.1778586)
    assert rows[0]["branch_lon"] == pytest.approx(129.1689029)
    assert rows[0]["branch_location_verified"] is True
    assert "address" not in rows[0]

    branch = municipal.MunicipalDbWriter(
        "MUNI_HOME_PEN_GO_KR_92635850"
    ).branch_info_from_row(rows[0])
    assert branch["address"] == "부산광역시 해운대구 양운로 183"
    assert branch["address_source"] == "OFFICIAL_INSTITUTION_LOCATION"
    assert branch["location_verified"] is True


def test_busan_experience_collects_full_list_and_detail_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title = "\ud574\uc6b4\ub300 \uc218\ubaa9\uc6d0 \uc232\uccb4\ud5d8"
    branch = "\ud478\ub978\ub3c4\uc2dc\uac00\uafb8\uae30\uc0ac\uc5c5\uc18c"
    target_text = "\ucd08\ub4f1\uc0dd \ub3d9\ubc18 \uac00\uc871"
    list_page = f"""
    <table class="boardList"><tbody><tr>
      <td>1</td>
      <td><a class="reserveItem" href="javascript:void(0);"
        onclick="fn_viewProgrm('471', '1366');return false;">{title}</a></td>
      <td>{branch}</td>
      <td>{target_text}</td>
      <td>2099-08-10 ~ 2099-08-22</td>
      <td>2099-08-19 ~ 2099-08-26</td>
      <td>\uc628\ub77c\uc778</td>
      <td><span>\uc811\uc218\uc911</span></td>
    </tr></tbody></table>
    """
    detail_page = f"""
    <dl><dt>\uc6b4\uc601\uae30\uac04</dt><dd>2099-08-19(\uc218) ~ 2099-08-26(\uc218)</dd></dl>
    <dl><dt>\uc2e0\uccad\uae30\uac04</dt><dd>2099-08-10 09:00 ~ 2099-08-22 16:00</dd></dl>
    <dl><dt>\uc218\uac15\ub8cc</dt><dd>\uc608\uc57d \uc811\uc218\uc2dc \ud68c\ucc28 \uc815\ubcf4\uc5d0\uc11c \ud655\uc778 \uac00\ub2a5</dd></dl>
    <dl><dt>\ud504\ub85c\uadf8\ub7a8 \uc6b4\uc601\uc694\uc77c</dt><dd>\uc218</dd></dl>
    <dl><dt>\uc6b4\uc601\uae30\uad00</dt><dd>{branch}</dd></dl>
    <dl><dt>\ub300\uc0c1</dt><dd>{target_text}</dd></dl>
    <div class="tabCont">
      <h4>\uc18c\uac1c</h4>
      <p>\uc7a5 \uc18c : \ud574\uc6b4\ub300\uc218\ubaa9\uc6d0</p>
      <p>\uc774 \uc6a9 \ub8cc : \ubb34\ub8cc</p>
      <p>\uc624\uc804 10\uc2dc ~ 11\uc2dc 30\ubd84</p>
    </div>
    <div class="tabCont">
      <h4>\uc704\uce58\uc815\ubcf4</h4>
      <h5>\uc8fc\uc18c</h5><div>\ubd80\uc0b0 \ud574\uc6b4\ub300\uad6c \uc11d\ub300\ub3d9 77</div>
    </div>
    """
    requested: list[str] = []

    def fetch(_session, url: str, timeout: int):
        requested.append(url)
        return BeautifulSoup(
            detail_page if "/exprn/view?" in url else list_page,
            "lxml",
        )

    monkeypatch.setattr(municipal, "session", _FakeSession)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)

    rows, parser, meta = municipal.collect_from_url(
        _target(
            "BUSAN_RESERVATION",
            "https://reserve.busan.go.kr/exprn",
            "\ubd80\uc0b0\uad11\uc5ed\uc2dc \ud1b5\ud569\uc608\uc57d",
        ),
        timeout=10,
        max_pages=100,
        detail_limit=1000,
    )

    assert parser == "busan_experience_table+detail"
    assert len(rows) == 1
    assert rows[0]["provider_course_id"] == "BUSAN_RESERVATION:exprn:471:1366"
    assert rows[0]["target"] == target_text
    assert rows[0]["fee"] == "\ubb34\ub8cc"
    assert rows[0]["period"] == "2099-08-19 ~ 2099-08-26"
    assert rows[0]["venue_name"] == "\ud574\uc6b4\ub300\uc218\ubaa9\uc6d0"
    assert rows[0]["category"] == "\uacac\ud559/\uccb4\ud5d8"
    assert rows[0]["schedule_raw"] == "\uc218 10:00~11:30"
    assert rows[0]["venue_address"] == "\ubd80\uc0b0 \ud574\uc6b4\ub300\uad6c \uc11d\ub300\ub3d9 77"
    assert rows[0]["service_group"] == "\uccb4\ud5d8"
    assert rows[0]["service_group_policy"] == "locked"
    assert meta["total_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert len(requested) == 2


def test_busan_experience_extracts_inline_fee_and_place() -> None:
    text = (
        "\uc0c1\uc138\uc815\ubcf4 "
        "\u3147 \uc7a5 \uc18c : \ud574\uc6b4\ub300\uc218\ubaa9\uc6d0 "
        "\u3147 \ucc38 \uac00 \ube44 : \ubb34\ub8cc "
        "\u203b \uae30\uc0c1 \uc0c1\ud669\uc5d0 \ub530\ub77c \ucde8\uc18c\ub420 \uc218 \uc788\uc74c"
    )

    assert municipal.busan_exprn_rich_fee(text) == "\ubb34\ub8cc"
    assert municipal.busan_exprn_rich_place(text) == "\ud574\uc6b4\ub300\uc218\ubaa9\uc6d0"


def test_busan_experience_normalizes_tiered_korean_fees() -> None:
    text = (
        "\ucc38 \uac00 \ube44 : 20\uba85 \ubbf8\ub9cc (8\ub9cc\uc6d0), "
        "20~25\uba85 (10\ub9cc\uc6d0), 26~30\uba85 (12\ub9cc\uc6d0)/1\uac1c\ubc18 "
        "\u25b7 \uacb0\uc81c\ubc29\ubc95 : \ud604\uc7a5 \uacb0\uc81c, \uc2dc\uc2a4\ud15c\uc0c1 0\uc6d0"
    )

    assert municipal.busan_exprn_rich_fee(text) == (
        "80,000\uc6d0 ~ 100,000\uc6d0 ~ 120,000\uc6d0 (\uc870\uac74\ubcc4 \uc0c1\uc774)"
    )


def test_gwangju_api_total_count_marks_a_complete_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {
                "error": "N",
                "dataMap": {
                    "totalCnt": 1,
                    "pageCnt": 1,
                    "list": [
                        {
                            "bookingCode": "A-1",
                            "eduNm": "광주 문화 체험",
                            "startEduDate": "2099-08-01",
                            "endEduDate": "2099-08-01",
                            "startEduTime": "10:00",
                            "endEduTime": "12:00",
                            "eduTarget": "가족",
                            "eduPriceType": "F",
                            "cateNm": "체험",
                        }
                    ],
                },
            }

    class _PostSession(_FakeSession):
        def post(self, url, data, timeout):
            return _Response()

    monkeypatch.setattr(municipal, "session", _PostSession)

    rows, parser, meta = municipal.collect_gwangju_booking(
        _target(
            "MUNI_WWW_GWANGJU_GO_KR_82EF77CD",
            "https://www.gwangju.go.kr/reserve/bookingList.do?pageId=reserve1&searchCate1=A",
        ),
        timeout=10,
        max_pages=100,
    )

    assert parser == "gwangju_booking_api"
    assert len(rows) == 1
    assert meta["source_count"] == meta["total_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["source_cap_reached"] is False


def test_namwon_experience_api_publishes_complete_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {
                "result": {
                    "totalPages": 1,
                    "content": [
                        {
                            "itemUid": "experience-1",
                            "itemTitle": "남원 생태 체험",
                            "applyBeginDate": "2099-07-01",
                            "applyEndDate": "2099-08-01",
                            "itemInfo4": "",
                            "itemProgress": "접수중",
                            "facilityInfo": {
                                "fcltName": "백두대간생태교육장",
                                "fcltCode": "ECOCUBE",
                                "rsvtMthd": "온라인",
                            },
                            "tags": [],
                        }
                    ],
                }
            }

    class _NamwonSession(_FakeSession):
        def get(self, url, params, timeout):
            return _Response()

    monkeypatch.setattr(municipal, "namwon_api_session", _NamwonSession)
    monkeypatch.setattr(
        municipal,
        "namwon_extract_detail_info",
        lambda session, url, timeout: {},
    )

    rows, parser, meta = municipal.collect_namwon_reserve_api(
        _target(
            "MUNI_WWW_NAMWON_GO_KR_37D4EA88",
            "https://www.namwon.go.kr/reserve/index.do?menuUid=ff80808190963f64019096945f6000b9",
        ),
        timeout=10,
        max_pages=100,
        detail_limit=1000,
    )

    assert parser == "namwon_reserve_api"
    assert len(rows) == 1
    assert rows[0]["target"] == "전체"
    assert rows[0]["fee"]
    assert rows[0]["period"] == "2099-07-01 ~ 2099-08-01"
    assert rows[0]["venue_name"]
    assert rows[0]["category"]
    assert rows[0]["schedule_raw"] == "시간 별도 안내"


def test_namwon_root_scope_never_collects_lodging_rental_or_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_types: list[str] = []

    class _Response:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return {"result": {"totalPages": 0, "content": []}}

    class _NamwonSession(_FakeSession):
        def get(self, url, params, timeout):
            requested_types.append(params["rsvtType"])
            return _Response()

    monkeypatch.setattr(municipal, "namwon_api_session", _NamwonSession)

    rows, parser, meta = municipal.collect_namwon_reserve_api(
        _target(
            "MUNI_WWW_NAMWON_GO_KR_37D4EA88",
            "https://www.namwon.go.kr/reserve/index.do",
        ),
        timeout=10,
        max_pages=100,
        detail_limit=1000,
    )

    assert parser == "namwon_reserve_api"
    assert rows == []
    assert set(requested_types) == {"EDUCATION", "EXPERIENCE"}
    assert len(requested_types) == 5
    assert meta["program_reservation_types"] == ["EDUCATION", "EXPERIENCE"]
    assert meta["excluded_non_program_config_count"] == 4
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
