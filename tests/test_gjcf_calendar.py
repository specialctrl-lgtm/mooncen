from __future__ import annotations

from datetime import date, timedelta

from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as crawler


TARGET = crawler.CrawlTarget(
    provider="MUNI_WWW_GJCF_OR_KR_F9585EF3",
    name="광주문화재단",
    branch="광주문화재단",
    url="https://www.gjcf.or.kr/cf/cultureart/list/calendar.do",
    source="test",
)


LIST_HTML = """
<html><body>
  <a onclick="pf_LinkPage(2)">2</a>
  <ul class="calendarContentsUl">
    <li>
      <div class="imgBox" style="background-image:url('/poster.jpg')"></div>
      <div class="letterBox">
        <p class="ph1"><span class="cate">체험</span>
          <a href="/cf/cultureart/list/calendar/21584/view.do">청년 체험전시</a>
        </p>
        <div class="boTitle">
          <p class="place">아르스디엠</p>
          <p class="date">2026-07-25 ~ 2026-07-31</p>
        </div>
      </div>
    </li>
  </ul>
</body></html>
"""


DETAIL_HTML = """
<html><body>
  <table>
    <tr><th>행사명</th><td>청년 체험전시</td></tr>
    <tr><th>행사장소</th><td>광주 동구 아르스디엠</td><th>장르</th><td>체험</td></tr>
    <tr><th>관람료</th><td>무료</td></tr>
    <tr><th>행사기간</th><td>2026-07-25 ~ 2026-07-31</td><th>관람시간</th><td>16:00 ~ 20:00</td></tr>
  </table>
  <div class="detailViewContentsBox">
    대상 : 청년 누구나 일시 : 7월 25일 장소 : 아르스디엠
  </div>
</body></html>
"""


def test_gjcf_calendar_collects_required_experience_fields(monkeypatch) -> None:
    calls: list[str] = []

    def fake_fetch(_session, url: str, timeout: int = 20):
        calls.append(url)
        if "/21584/view.do" in url:
            return BeautifulSoup(DETAIL_HTML, "lxml")
        if "PageIndex=1" in url:
            return BeautifulSoup(LIST_HTML, "lxml")
        return BeautifulSoup("<html><body></body></html>", "lxml")

    monkeypatch.setattr(crawler, "fetch_soup", fake_fetch)

    rows, parser, meta = crawler.collect_gjcf_calendar(
        TARGET,
        timeout=10,
        max_pages=3,
        detail_limit=1,
    )

    assert parser == "gjcf_calendar"
    assert len(rows) == 1
    assert rows[0]["title"] == "청년 체험전시"
    assert rows[0]["target"] == "청년 누구나"
    assert rows[0]["fee"] == "무료"
    assert rows[0]["period"] == "2026-07-25 ~ 2026-07-31"
    assert rows[0]["venue_name"] == "광주 동구 아르스디엠"
    assert "address" not in rows[0]
    assert rows[0]["category"] == "체험"
    assert rows[0]["schedule_raw"] == "16:00 ~ 20:00"
    assert rows[0]["image_url"] == "https://www.gjcf.or.kr/poster.jpg"
    assert meta["detail_pages"] == 1
    assert meta["pagination_detected"] is True
    assert len(calls) == 3


def test_dmgj_event_parser_does_not_use_the_status_as_the_title(monkeypatch) -> None:
    period_start = date.today() + timedelta(days=365)
    period_end = period_start + timedelta(days=6)
    compact_period = f"{period_start:%Y.%m.%d}~{period_end:%Y.%m.%d}"
    normalized_period = f"{period_start:%Y-%m-%d} ~ {period_end:%Y-%m-%d}"
    list_html = f"""
    <ul class="grid-view"><li>
      <a href="/event.es?mid=a10301000000&amp;seq=9530&amp;act=view&amp;p_cate=0301">
        <span class="list_cate">체험</span>
        <span class="thumb"><img src="/poster.png"></span>
        <span class="info">
          <span class="list_label">진행중</span>
          <span class="info_tit">청년 문화 체험</span>
          <span class="place">광주문화공간</span>
          <span class="period">{compact_period}</span>
        </span>
      </a>
    </li></ul>
    """
    detail_html = f"""
    <div class="list_detail">
      <h3 class="detail_tit">청년 문화 체험</h3>
      <dl>
        <dt>기간</dt><dd>{compact_period}</dd>
        <dt>시간</dt><dd>18:00 ~ 19:00</dd>
        <dt>장소</dt><dd>광주문화공간</dd>
        <dt>요금정보</dt><dd>무료</dd>
      </dl>
      <div class="detail_con">관람: 청년 누구나 운영: 광주문화재단</div>
    </div>
    """
    calls: list[str] = []

    def fake_fetch(_session, url: str, timeout: int = 20):
        calls.append(url)
        if "act=view" in url:
            return BeautifulSoup(detail_html, "lxml")
        if "nPage=1" in url:
            return BeautifulSoup(list_html, "lxml")
        return BeautifulSoup("<html></html>", "lxml")

    monkeypatch.setattr(crawler, "fetch_soup", fake_fetch)
    target = crawler.CrawlTarget(
        provider="MUNI_WWW_GJCF_OR_KR_F9585EF3",
        name="디어마이광주 문화행사",
        branch="광주광역시",
        url="https://dmgj.kr/event.es?mid=a10301000000&p_cate=0301",
        source="test",
    )

    rows, parser, meta = crawler.collect_dmgj_events(
        target,
        timeout=10,
        max_pages=3,
        detail_limit=1,
    )

    assert parser == "dmgj_event_list_detail"
    assert len(rows) == 1
    assert rows[0]["title"] == "청년 문화 체험"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["target"] == "청년 누구나"
    assert rows[0]["fee"] == "무료"
    assert rows[0]["period"] == normalized_period
    assert rows[0]["venue_name"] == "광주문화공간"
    assert "address" not in rows[0]
    assert rows[0]["category"] == "체험"
    assert rows[0]["schedule_raw"] == "18:00 ~ 19:00"
    assert meta["pagination_complete"] is True
    assert len(calls) == 2


def test_dmgj_locality_branch_is_promoted_to_the_physical_venue() -> None:
    row = {
        "branch": "광주광역시 동구 동명동",
        "venue_name": "국립아시아문화전당 극장2",
        "title": "공연",
        "raw_url": "https://dmgj.kr/event.es?seq=1",
    }
    writer = crawler.MunicipalDbWriter(
        "MUNI_WWW_GJCF_OR_KR_F9585EF3"
    )

    assert writer.is_broad_branch_name(row["branch"]) is True
    writer.normalize_branch_split_row(row)

    assert row["branch"] == "국립아시아문화전당 극장2"
    assert row["branch_code"].startswith("국립아시아문화전당_극장2_")


def test_dmgj_event_parser_stops_at_the_first_fully_expired_page(
    monkeypatch,
) -> None:
    expired_page = """
    <ul class="grid-view"><li>
      <a href="/event.es?mid=a10301000000&amp;seq=1&amp;act=view&amp;p_cate=0301">
        <span class="info_tit">종료된 공연</span>
        <span class="period">2025.01.01~2025.01.02</span>
      </a>
    </li></ul>
    """
    requests: list[str] = []

    def fake_fetch(_session, url: str, timeout: int = 20):
        requests.append(url)
        return BeautifulSoup(expired_page, "lxml")

    monkeypatch.setattr(crawler, "fetch_soup", fake_fetch)
    target = crawler.CrawlTarget(
        provider="MUNI_WWW_GJCF_OR_KR_F9585EF3",
        name="디어마이광주 문화행사",
        branch="광주광역시",
        url="https://dmgj.kr/event.es?mid=a10301000000&p_cate=0301",
        source="test",
    )

    rows, _parser, meta = crawler.collect_dmgj_events(
        target,
        timeout=10,
        max_pages=100,
        detail_limit=1000,
    )

    assert rows == []
    assert len(requests) == 1
    assert meta["expired_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["no_current_data"] is True
