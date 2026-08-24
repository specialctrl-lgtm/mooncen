from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
import requests
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


OLD_PAGE_ONE = """
<html><body>
  <ul class="eduList">
    <li class="close">
      <a href="/program/programInfoDetail.do?prgm_seq=547&amp;prgmdiv=dong">
        <div class="cate">
          <span class="tag_state c_rd">문화예술</span>
          <span class="tag_state bg_bk">오후</span>
          <p class="tag_state c_gy">접수마감</p>
        </div>
        <div class="edutit">
          <p class="spot">[운서1동]</p>
          <p class="tit">줌바댄스</p>
        </div>
        <ul class="lec_info">
          <li><span class="q">대상 :</span> 성인</li>
          <li><span class="q">수 강 료 :</span><span>60,000원</span></li>
          <li><span class="q">교육기간 :</span>2026-07-01 ~ 2026-09-22</li>
          <li><span class="q">교육시간 :</span>19:30~20:30 (화,목)</li>
          <li><span class="q">신청기간 :</span><span>2026-06-15 09:00 ~ 2026-06-15 23:00</span></li>
          <li><span class="q">정원 :</span>13명</li>
        </ul>
      </a>
    </li>
  </ul>
  <p class="paging"><a href="/program/programInfoList.do?prgmdiv=dong&amp;pgno=2">2</a></p>
</body></html>
"""


OLD_PAGE_TWO = """
<html><body>
  <ul class="eduList">
    <li class="close">
      <a href="/program/programInfoDetail.do?prgm_seq=546&amp;prgmdiv=dong&amp;pgno=2">
        <div class="cate">
          <span class="tag_state c_rd">문화예술</span>
          <span class="tag_state bg_bk">오후</span>
          <p class="tag_state c_gy">종료</p>
        </div>
        <div class="edutit">
          <p class="spot">[운서1동]</p>
          <p class="tit">파워댄스</p>
        </div>
        <ul class="lec_info">
          <li><span class="q">대상 :</span> 성인</li>
          <li><span class="q">수 강 료 :</span><span>50,000원</span></li>
          <li><span class="q">교육기간 :</span>2026-07-01 ~ 2026-09-22</li>
          <li><span class="q">신청기간 :</span>2026-06-15 09:00 ~ 2026-06-15 23:00</li>
          <li><span class="q">정원 :</span>18명</li>
        </ul>
      </a>
    </li>
  </ul>
  <p class="paging"><a href="/program/programInfoList.do?prgmdiv=dong&amp;pgno=2">2</a></p>
</body></html>
"""


OLD_DETAIL = """
<html><body>
  <div class="board_view">
    <div class="title">
      <span class="tag_state">오후</span>
      <span class="tag_state">접수마감</span>
      <p class="margin_t10">줌바댄스</p>
    </div>
    <ul class="data_list list_col2">
      <li><dl><dt>교육기관</dt><dd>운서1동</dd></dl></li>
      <li><dl><dt>분야</dt><dd>운서1동 &gt; 문화예술</dd></dl></li>
      <li><dl><dt>정원</dt><dd>6 / 13 명</dd></dl><dl><dt>대기</dt><dd>1 / 5 명</dd></dl></li>
      <li><dl><dt>정시 접수</dt><dd>2026.06.15 09시 00분 ~ 2026.06.15 23시 00분</dd></dl></li>
      <li><dl><dt>추가 접수</dt><dd>2026.06.16 09시 00분 ~ 2026.06.17 23시 00분</dd></dl></li>
      <li><dl><dt>교육 대상</dt><dd>성인</dd></dl></li>
      <li><dl><dt>교육기간</dt><dd>2026-07-01 ~ 2026-09-22</dd></dl><dl><dt>교육 요일</dt><dd>화,목</dd></dl></li>
      <li><dl><dt>교육 시간</dt><dd>19:30~20:30</dd></dl><dl><dt>강사명</dt><dd>최말엽</dd></dl></li>
      <li><dl><dt>수강료</dt><dd>60,000원</dd></dl><dl><dt>재료비</dt><dd>교재비 별도</dd></dl></li>
      <li><dl><dt>강의실</dt><dd>3층 다목적실</dd></dl><dl><dt>문의처</dt><dd>032-760-6396</dd></dl></li>
    </ul>
    <div class="con"><div class="detail">선착순으로 운영되는 주민자치 강좌입니다.</div></div>
  </div>
</body></html>
"""


KRDS_PAGE = """
<html><body>
  <ul class="krds-structured-list type-full gallery lecture">
    <li class="structured-item">
      <div class="card-img"><a href="/main/program/programInfoDetail.do?prgm_seq=6648&amp;prgmdiv=dong"><img alt="여성근력교실" src="/share/noimg.png"></a></div>
      <div class="in">
        <div class="card-top">
          <span class="krds-badge">생활체육</span>
          <span class="krds-badge">오전</span>
          <span class="krds-badge">접수중</span>
        </div>
        <div class="card-body">
          <a class="c-text" href="/main/program/programInfoDetail.do?prgm_seq=6648&amp;prgmdiv=dong&amp;pgno=3">
            <p class="c-tit">여성근력교실</p>
            <p class="c-txt">[송림4동]</p>
            <ul class="c-date">
              <li><strong class="key">대상</strong><span class="value">여성</span></li>
              <li><strong class="key">수강료</strong><span class="value">무료</span></li>
              <li><strong class="key">교육기간</strong><span class="value">2026-07-21 ~ 2026-09-30</span></li>
              <li><strong class="key">신청기간</strong><span class="value">2026-07-13 09:00 ~ 2026-07-17 18:00</span></li>
              <li><strong class="key">정원</strong><span class="value">10명 (대기 5명)</span></li>
            </ul>
          </a>
        </div>
      </div>
    </li>
  </ul>
</body></html>
"""


KRDS_DETAIL = """
<html><body>
  <div id="detail_con">
    <div class="board-view">
      <div class="title"><p class="tag-group"><span class="tag">오전</span><span class="tag">접수중</span><span class="tag">교육전</span></p>여성근력교실</div>
      <ul class="info-data">
        <li><dl><dt>교육기관</dt><dd>송림4동</dd></dl></li>
        <li><dl><dt>분야</dt><dd>송림4동 &gt; 생활체육</dd></dl></li>
        <li><dl><dt>정원</dt><dd>3 / 10 명</dd></dl><dl><dt>대기</dt><dd>1 / 5 명</dd></dl></li>
        <li><dl><dt>정시 접수</dt><dd>2026.07.13 09시 00분 ~ 2026.07.17 18시 00분</dd></dl></li>
        <li><dl><dt>교육 대상</dt><dd>여성</dd></dl></li>
        <li><dl><dt>교육기간</dt><dd>2026-07-21 ~ 2026-09-30</dd></dl><dl><dt>교육 요일</dt><dd>화,목</dd></dl></li>
        <li><dl><dt>교육 시간</dt><dd>10:30 ~ 11:20</dd></dl><dl><dt>강사명</dt><dd>김강사</dd></dl></li>
        <li><dl><dt>수강료</dt><dd>무료</dd></dl><dl><dt>재료비</dt><dd>편한 운동복</dd></dl></li>
        <li><dl><dt>강의실</dt><dd>3층 GX실</dd></dl><dl><dt>문의처</dt><dd>032-770-5927</dd></dl></li>
      </ul>
      <div class="con-box"><div class="detail">덤벨과 밴드를 활용한 기초근력 수업</div></div>
    </div>
  </div>
</body></html>
"""


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _target(url: str, provider: str = "MUNI_INC_TEST") -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="인천 주민자치 교육",
        branch="인천",
        url=url,
        source="test",
        priority=1,
        region="인천광역시",
        extra={},
    )


def test_old_edu_list_paginates_canonicalizes_and_respects_detail_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 9
        calls.append(url)
        if "programInfoDetail.do" in url:
            return _soup(OLD_DETAIL)
        page = int((parse_qs(urlparse(url).query).get("pgno") or ["1"])[0])
        return _soup(OLD_PAGE_ONE if page == 1 else OLD_PAGE_TWO)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    rows, parser, meta = municipal.collect_incheon_program_info(
        _target("https://edu.yeongjong.go.kr/program/programInfoList.do?prgmdiv=dong"),
        timeout=9,
        max_pages=5,
        detail_limit=1,
    )

    assert parser == "incheon_program_info"
    assert [row["title"] for row in rows] == ["줌바댄스", "파워댄스"]
    assert rows[0]["branch"] == "운서1동"
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["period"] == "2026-07-01 ~ 2026-09-22"
    assert rows[0]["apply_period"].startswith("2026.06.15")
    assert rows[0]["fee"] == "60,000원"
    assert rows[0]["capacity_current"] == 6
    assert rows[0]["capacity_total"] == 13
    assert rows[0]["waitlist_current"] == 1
    assert rows[0]["waitlist_total"] == 5
    assert rows[0]["schedule_raw"] == "화,목 19:30~20:30"
    assert rows[0]["instructor"] == "최말엽"
    assert rows[0]["room"] == "3층 다목적실"
    assert rows[0]["raw_fields"]["additional_apply_period"].startswith("2026.06.16")
    assert rows[1]["raw_url"] == "https://edu.yeongjong.go.kr/program/programInfoDetail.do?prgm_seq=546&prgmdiv=dong"
    assert rows[1]["provider_course_id"].endswith(":prgm:546")
    assert meta["pages"] == 2
    assert meta["detail_pages"] == meta["detail_attempts"] == 1
    assert meta["discovered_links"] == meta["source_rows"] == 2
    assert meta["reservation_discovery_links"] == 0
    assert meta["pagination_detected"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap 1 covers 1 of 2" in meta[
        "configured_collection_error"
    ]
    assert len([url for url in calls if "programInfoDetail.do" in url]) == 1
    assert [int(parse_qs(urlparse(url).query)["pgno"][0]) for url in calls if "programInfoList.do" in url] == [1, 2]


def test_krds_cards_and_detail_are_mapped_without_site_heading_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        return _soup(KRDS_DETAIL if "programInfoDetail.do" in url else KRDS_PAGE)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    rows, parser, meta = municipal.collect_incheon_program_info(
        _target("https://www.jemulpo.go.kr/main/program/programInfoList.do?prgmdiv=dong"),
        timeout=7,
        max_pages=3,
        detail_limit=1,
    )

    assert parser == "incheon_program_info"
    assert meta["pages"] == 1
    assert meta["detail_pages"] == 1
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "여성근력교실"
    assert row["branch"] == "송림4동"
    assert row["category"] == "생활체육"
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["application_url"] == row["raw_url"]
    assert row["raw_url"] == "https://www.jemulpo.go.kr/main/program/programInfoDetail.do?prgm_seq=6648&prgmdiv=dong"
    assert row["period"] == "2026-07-21 ~ 2026-09-30"
    assert row["apply_period"].startswith("2026.07.13")
    assert row["fee"] == "무료"
    assert row["capacity_current"] == 3
    assert row["capacity_total"] == 10
    assert row["waitlist_current"] == 1
    assert row["waitlist_total"] == 5
    assert row["target"] == "여성"
    assert row["description"] == "덤벨과 밴드를 활용한 기초근력 수업"


def test_empty_detail_target_uses_official_age_limit() -> None:
    target = _target(
        "https://www.geomdan.go.kr/pmainp/program/programInfoList.do?prgmdiv=dong"
    )
    item = _soup(KRDS_PAGE).select_one("li.structured-item")
    assert item is not None
    row = municipal.incheon_program_info_list_row(
        target,
        target.url,
        item,
        "krds",
    )
    assert row is not None
    detail = KRDS_DETAIL.replace(
        "<li><dl><dt>교육 대상</dt><dd>여성</dd></dl></li>",
        (
            "<li><dl><dt>교육 대상</dt><dd></dd></dl>"
            "<dl><dt>나이 제한</dt><dd>만 60세 이상</dd></dl></li>"
        ),
    )

    municipal.enrich_incheon_program_info_detail(row, _soup(detail))

    assert row["target"] == "만 60세 이상"
    assert row["raw_fields"]["target_source"] == "detail_age_limit"


@pytest.mark.parametrize(
    ("provider", "url", "html", "expected_title", "expected_path"),
    [
        (
            "MUNI_YEONGJONG",
            "https://edu.yeongjong.go.kr/program/programInfoList.do?prgmdiv=dong",
            OLD_PAGE_TWO,
            "파워댄스",
            "/program/programInfoList.do",
        ),
        (
            "MUNI_JEMULPO",
            "https://www.jemulpo.go.kr/main/program/programInfoList.do?prgmdiv=dong",
            KRDS_PAGE,
            "여성근력교실",
            "/main/program/programInfoList.do",
        ),
        (
            "MUNI_SEOHAE",
            "https://www.seohae.go.kr/pmainp/program/programInfoList.do?prgmdiv=dong",
            OLD_PAGE_TWO.replace("/program/", "/pmainp/program/"),
            "파워댄스",
            "/pmainp/program/programInfoList.do",
        ),
        (
            "MUNI_GEOMDAN",
            "https://www.geomdan.go.kr/main/part/education/class_resident.jsp",
            KRDS_PAGE.replace("/main/program/", "/pmainp/program/"),
            "여성근력교실",
            "/pmainp/program/programInfoList.do",
        ),
    ],
)
def test_all_four_official_hosts_dispatch_before_generic(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    url: str,
    html: str,
    expected_title: str,
    expected_path: str,
) -> None:
    fetched: list[str] = []

    def fake_fetch(_session: object, requested_url: str, timeout: int) -> BeautifulSoup:
        fetched.append(requested_url)
        return _soup(html)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    rows, parser, meta = municipal.collect_from_url(
        _target(url, provider=provider),
        timeout=6,
        max_depth=0,
        max_pages=1,
        detail_limit=0,
    )

    assert parser == "incheon_program_info"
    assert meta["pages"] == 1
    assert [row["title"] for row in rows] == [expected_title]
    assert urlparse(fetched[0]).path == expected_path
    assert parse_qs(urlparse(fetched[0]).query)["pgno"] == ["1"]


def test_unknown_program_info_host_is_not_claimed() -> None:
    assert not municipal.is_incheon_program_info_target(
        "https://example.org/program/programInfoList.do?prgmdiv=dong"
    )


def test_expired_list_row_is_skipped_before_fetching_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    expired_page = OLD_PAGE_TWO.replace(
        "2026-07-01 ~ 2026-09-22",
        "2020-01-01 ~ 2020-03-31",
    )
    detail_calls: list[str] = []

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        if "programInfoDetail.do" in url:
            detail_calls.append(url)
            return _soup(OLD_DETAIL)
        return _soup(expired_page)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)

    rows, parser, meta = municipal.collect_incheon_program_info(
        _target("https://edu.yeongjong.go.kr/program/programInfoList.do?prgmdiv=dong"),
        timeout=7,
        max_pages=1,
        detail_limit=5,
    )

    assert parser == "incheon_program_info"
    assert rows == []
    assert meta["discovered_links"] == 1
    assert meta["detail_pages"] == 0
    assert detail_calls == []


def test_throttled_later_page_keeps_verified_partial_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    response = requests.Response()
    response.status_code = 403
    response.url = "https://edu.yeongjong.go.kr/program/programInfoList.do?pgno=2"

    def fake_fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        page = int((parse_qs(urlparse(url).query).get("pgno") or ["1"])[0])
        if page == 2:
            raise requests.HTTPError("throttled", response=response)
        return _soup(OLD_PAGE_ONE)

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_incheon_program_soup", fake_fetch)

    rows, _parser, meta = municipal.collect_incheon_program_info(
        _target("https://edu.yeongjong.go.kr/program/programInfoList.do?prgmdiv=dong"),
        timeout=7,
        max_pages=5,
        detail_limit=0,
    )

    assert [row["title"] for row in rows] == ["줌바댄스"]
    assert meta["pages"] == 1
    assert meta["pagination_detected"] is True
    assert meta["pagination_complete"] is False
    assert meta["snapshot_complete"] is False
    assert "HTTP 403 while fetching page 2" in meta[
        "configured_collection_error"
    ]
    assert "detail_limit cap 0 covers 0 of 1" in meta[
        "configured_collection_error"
    ]
