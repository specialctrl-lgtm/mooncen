from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


def _row(
    lecture_key: int,
    title: str,
    venue: str,
    status: str,
    apply_period: str,
    period: str,
    *,
    capacity: str = "7 / 8",
    waitlist: str = "0 / 4",
    selection: str = "선착순",
    fee: str = "무료",
    application_method: str = "온 온라인",
) -> str:
    return f"""
    <tr>
      <td>{status}</td>
      <td class="p-subject">
        <a href="/reserve/edcLctreView.do?key=4708&amp;searchLctreKey={lecture_key}&amp;pageIndex=99">{title}</a>
        <p>{venue}</p>
      </td>
      <td>
        <p>신청 : {apply_period}</p>
        <p>교육 : {period}</p>
        <p>요일 : 화, 목 10:00~11:00</p>
      </td>
      <td>{capacity}</td>
      <td>{waitlist}</td>
      <td><p>{selection}</p><p>{fee}</p></td>
      <td>{application_method}</td>
    </tr>
    """


def _list_page(rows: str, total: int = 4, last_page: int = 2) -> str:
    pages = "".join(
        f'<a href="/reserve/webEdcLctreList.do?key=4708&amp;rep=1&amp;pageIndex={page}">{page}</a>'
        for page in range(1, last_page + 1)
    )
    return f"""
    <html><body>
      <p class="total">총 {total}건</p>
      <table><tbody>{rows}</tbody></table>
      <div class="pagination">
        {pages}
        <a class="p-page__control" href="/reserve/webEdcLctreList.do?key=4708&amp;rep=1&amp;pageIndex=21">다음 페이지</a>
      </div>
    </body></html>
    """


LIST_PAGE_1 = _list_page(
    _row(
        152460,
        "치매예방 기억튼튼교실",
        "보건의료원 치매센터 쉼터",
        "접수마감 교육중",
        "2099-06-23(화) 13:00 ~ 2099-07-01(수) 13:00",
        "2099-07-02(목) ~ 2099-07-30(목)",
    )
    + _row(
        152400,
        "지난 강좌",
        "통일평생교육원",
        "접수마감 교육종료",
        "2020-01-01 ~ 2020-01-02",
        "2020-01-03 ~ 2020-01-31",
    )
)

LIST_PAGE_2 = _list_page(
    _row(
        152404,
        "폐강 강좌",
        "통일평생교육원",
        "폐강",
        "2099-07-01 ~ 2099-07-02",
        "2099-07-03 ~ 2099-07-31",
    )
    + _row(
        152500,
        "초보 목공교실",
        "연천군 목공체험장",
        "접수중 교육중",
        "2099-07-01 ~ 2099-07-31",
        "2099-08-01 ~ 2099-08-31",
        capacity="2 / 10",
        waitlist="1 / 3",
        application_method="방 방문",
    )
)


DETAIL_152460 = """
<html><body>
  <table><tbody>
    <tr><th>신청인원</th><td>7명 접수 / 8명 모집</td></tr>
    <tr><th>강좌구분</th><td>기타</td></tr>
    <tr><th>강좌상태</th><td>모집마감 교육중</td></tr>
    <tr><th>접수기간</th><td>2099-06-23(화) 13:00 ~ 2099-07-01(수) 13:00</td></tr>
    <tr><th>교육기간</th><td>2099-07-02(목) ~ 2099-07-30(목)</td></tr>
    <tr><th>강의시간</th><td>(화) 10:00~11:00 (목) 10:00~11:00</td></tr>
    <tr><th>수강신청방법</th><td>온 온라인 ※ 선별방법 : 선착순</td></tr>
    <tr><th>수강료</th><td>무료</td></tr>
    <tr><th>강의장소</th><td>보건의료원 치매센터 쉼터(4층) 11027 경기도 연천군 전곡읍 은대성로 95 연천군보건의료원</td></tr>
    <tr><th>수강대상</th><td>여성</td></tr>
    <tr><th>모집정원</th><td>모집정원 8명 (대기인원 4명)</td></tr>
    <tr><th>문의</th><td>031-839-4072</td></tr>
  </tbody></table>
  <h3>상세정보</h3><p>기억력 향상을 위한 주 2회 교육입니다.</p><a>목록</a>
</body></html>
"""

DETAIL_152500 = """
<html><body>
  <table><tbody>
    <tr><th>신청인원</th><td>2명 접수 / 10명 모집</td></tr>
    <tr><th>강좌구분</th><td>목공</td></tr>
    <tr><th>강좌상태</th><td>접수중 교육중</td></tr>
    <tr><th>접수기간</th><td>2099-07-01 ~ 2099-07-31</td></tr>
    <tr><th>교육기간</th><td>2099-08-01 ~ 2099-08-31</td></tr>
    <tr><th>강의시간</th><td>토 14:00~16:00</td></tr>
    <tr><th>수강신청방법</th><td>방 방문 ※ 선별방법 : 선착순</td></tr>
    <tr><th>수강료</th><td>10,000원</td></tr>
    <tr><th>강의장소</th><td>연천군 목공체험장 교육실 11017 경기도 연천군 연천읍 문화로 150</td></tr>
    <tr><th>수강대상</th><td>연천군민</td></tr>
    <tr><th>모집정원</th><td>모집정원 10명 (대기인원 3명)</td></tr>
    <tr><th>문의</th><td>031-839-4432</td></tr>
  </tbody></table>
  <h3>상세정보</h3><p>안전한 목공 체험 수업입니다.</p><a>목록</a>
</body></html>
"""


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="MUNI_IR_45F4833C14D2",
        name="연천군 통합예약",
        branch="경기도 연천군",
        url="https://www.yeoncheon.go.kr/reserve/webEdcLctreList.do?key=4708&rep=1",
        source="test",
        priority=1,
        region="경기도 연천군",
        extra={},
    )


def test_yeoncheon_dispatch_completes_pages_filters_before_detail_and_maps_fields(monkeypatch) -> None:
    fetched: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        fetched.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == municipal.YEONCHEON_RESERVE_DETAIL_PATH:
            lecture_key = query["searchLctreKey"][0]
            assert lecture_key in {"152460", "152500"}
            return _soup(DETAIL_152460 if lecture_key == "152460" else DETAIL_152500)
        page = query["pageIndex"][0]
        return _soup(LIST_PAGE_2 if page == "2" else LIST_PAGE_1)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=5, detail_limit=5
    )

    assert parser == "yeoncheon_reserve_lecture_list+detail"
    assert len(rows) == 2
    by_key = {row["raw_fields"]["search_lctre_key"]: row for row in rows}
    memory = by_key["152460"]
    assert memory["provider_course_id"] == "MUNI_IR_45F4833C14D2:edc:152460"
    assert memory["raw_url"] == (
        "https://www.yeoncheon.go.kr/reserve/edcLctreView.do?"
        "key=4708&searchLctreKey=152460"
    )
    assert memory["branch"] == "보건의료원 치매센터 쉼터(4층)"
    assert memory["branch_code"].startswith("YEONCHEON_")
    assert memory["preserve_branch"] is True
    assert memory["address"] == (
        "11027 경기도 연천군 전곡읍 은대성로 95 연천군보건의료원"
    )
    assert memory["category"] == "기타"
    assert memory["capacity_current"] == 7
    assert memory["capacity_total"] == 8
    assert memory["waitlist_current"] == 0
    assert memory["waitlist_total"] == 4
    assert memory["selection_method"] == "선착순"
    assert memory["application_method_raw"] == "온 온라인 ※ 선별방법 : 선착순"
    assert memory["schedule_raw"] == "(화) 10:00~11:00 (목) 10:00~11:00"
    assert memory["fee"] == "무료"
    assert memory["target"] == "여성"
    assert memory["phone"] == "031-839-4072"
    assert memory["reservation_available"] is False
    assert "application_url" not in memory

    wood = by_key["152500"]
    assert wood["branch"] == "연천군 목공체험장 교육실"
    assert wood["venue_address"] == "11017 경기도 연천군 연천읍 문화로 150"
    assert wood["fee"] == "10,000원"
    assert wood["reservation_available"] is True
    assert wood["application_url"] == wood["raw_url"]

    fetched_detail_keys = {
        parse_qs(urlparse(url).query)["searchLctreKey"][0]
        for url in fetched
        if urlparse(url).path == municipal.YEONCHEON_RESERVE_DETAIL_PATH
    }
    assert fetched_detail_keys == {"152460", "152500"}
    list_pages = [
        parse_qs(urlparse(url).query)["pageIndex"][0]
        for url in fetched
        if urlparse(url).path == municipal.YEONCHEON_RESERVE_LIST_PATH
    ]
    assert list_pages == ["1", "2"]
    assert meta["pages"] == 2
    assert meta["detail_pages"] == 2
    assert meta["discovered_links"] == 4
    assert meta["reservation_discovery_links"] == 2
    assert meta["expired_count"] == 1
    assert meta["cancelled_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["pagination_exhausted"] is True
    assert meta["source_cap_reached"] is False
    assert "configured_collection_error" not in meta


def test_yeoncheon_canonical_detail_url_uses_only_numeric_search_key() -> None:
    first, first_key = municipal.canonical_yeoncheon_reserve_detail_url(
        "https://www.yeoncheon.go.kr/reserve/webEdcLctreList.do?key=4708&rep=1&pageIndex=1",
        "/reserve/other.do?pageIndex=9&searchLctreKey=152460&key=bad#section",
    )
    second, second_key = municipal.canonical_yeoncheon_reserve_detail_url(
        "https://www.yeoncheon.go.kr/reserve/webEdcLctreList.do?key=4708&rep=1&pageIndex=2",
        "/reserve/edcLctreView.do?searchLctreKey=152460&tracking=yes",
    )

    expected = (
        "https://www.yeoncheon.go.kr/reserve/edcLctreView.do?"
        "key=4708&searchLctreKey=152460"
    )
    assert first_key == second_key == "152460"
    assert first == second == expected
    assert municipal.canonical_yeoncheon_reserve_detail_url(
        _target().url, "https://example.com/view?searchLctreKey=152460"
    ) == ("", "")
    assert municipal.canonical_yeoncheon_reserve_detail_url(
        _target().url, "/reserve/view.do?searchLctreKey=15x"
    ) == ("", "")


def test_yeoncheon_page_cap_is_incomplete_and_never_claims_no_current(monkeypatch) -> None:
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, _url, timeout: _soup(LIST_PAGE_1),
    )

    rows, _parser, meta = municipal.collect_yeoncheon_reserve_lectures(
        _target(), timeout=5, max_pages=1, detail_limit=0
    )

    assert len(rows) == 1
    assert meta["pages"] == 1
    assert meta["discovered_links"] == 2
    assert meta["pagination_complete"] is False
    assert meta["pagination_exhausted"] is False
    assert meta["source_cap_reached"] is True
    assert meta["no_current_data"] is False
    assert "max_pages cap reached" in meta["configured_collection_error"]


def test_configured_partial_collection_default_blocks_save_and_stale_but_opt_in_can_save(
    monkeypatch,
) -> None:
    stale_calls: list[tuple[object, ...]] = []
    save_calls: list[list[dict[str, object]]] = []

    class FakeConnection:
        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeWriter:
        def __init__(self, provider: str) -> None:
            assert provider == _target().provider

        def save_rows(self, rows: list[dict[str, object]]) -> int:
            save_calls.append(rows)
            return len(rows)

    partial_row = {
        "provider": _target().provider,
        "provider_course_id": f"{_target().provider}:edc:152460",
        "title": "부분 수집 강좌",
        "branch": "연천군 통합예약",
        "raw_url": "https://www.yeoncheon.go.kr/reserve/edcLctreView.do?key=4708&searchLctreKey=152460",
    }
    monkeypatch.setattr(municipal, "load_targets", lambda *_args, **_kwargs: [_target()])
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_args, **_kwargs: (
            [partial_row],
            "yeoncheon_reserve_lecture_list+detail",
            {
                "pages": 1,
                "pagination_complete": False,
                "configured_collection_error": "max_pages cap reached after 1 of 13 declared pages",
            },
        ),
    )
    monkeypatch.setattr(municipal, "get_db_connection", FakeConnection)
    monkeypatch.setattr(municipal, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(
        municipal,
        "mark_stale_courses",
        lambda *args: stale_calls.append(args) or 0,
    )

    reports = municipal.run(
        source="municipal",
        target_limit=None,
        per_target_limit=0,
        min_score=0,
        include_review=True,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=1,
        detail_limit=0,
        timeout=5,
    )

    assert reports[0].success is True
    assert reports[0].saved == 0
    assert reports[0].configured_collection_error.startswith("max_pages cap reached")
    assert save_calls == []
    assert stale_calls == []

    opt_in_reports = municipal.run(
        source="municipal",
        target_limit=None,
        per_target_limit=1,
        min_score=0,
        include_review=True,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=1,
        detail_limit=0,
        timeout=5,
        allow_partial_save=True,
    )

    assert opt_in_reports[0].success is True
    assert opt_in_reports[0].saved == 1
    assert len(save_calls) == 1
    assert stale_calls == []


def test_geumcheon_shared_detail_parser_preserves_legacy_label_priority(monkeypatch) -> None:
    fixture = """
    <html><body>
      <div>신청자 : 3 / 20명</div>
      <table><tbody>
        <tr><th>강좌영역</th><td>문화예술</td></tr>
        <tr><th>강좌구분</th><td>기타</td></tr>
        <tr><th>신청기간</th><td>2099.07.01 ~ 2099.07.15</td></tr>
        <tr><th>접수기간</th><td>2099.01.01 ~ 2099.01.15</td></tr>
        <tr><th>교육기간</th><td>2099.08.01 ~ 2099.08.31</td></tr>
        <tr><th>정원</th><td>20명</td></tr>
        <tr><th>모집정원</th><td>99명</td></tr>
        <tr><th>대기접수</th><td>가능 2 / 5</td></tr>
        <tr><th>강의장소</th><td>금천문화회관 08584 가산디지털로 1</td></tr>
        <tr><th>수강신청방법</th><td>온라인</td></tr>
      </tbody></table>
    </body></html>
    """
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, _url, timeout: _soup(fixture),
    )

    detail = municipal.geumcheon_detail_fields(object(), "https://example.test/detail", 5)

    assert detail["category"] == "문화예술"
    assert detail["apply_period"] == "2099-07-01 ~ 2099-07-15"
    assert detail["capacity"] == "20명"
    assert detail["capacity_current"] == 3
    assert detail["capacity_total"] == 20
    assert detail["waitlist_current"] == 2
    assert detail["waitlist_total"] == 5
    assert detail["room"] == "금천문화회관"
    assert detail["venue_address"] == "08584 가산디지털로 1"
