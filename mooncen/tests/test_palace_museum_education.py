from __future__ import annotations

from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler.Crawler_MunicipalYaml import CrawlTarget


def test_palace_museum_sessions_receive_unique_raw_urls(monkeypatch) -> None:
    list_html = """
    <ul class="board-gallery type5">
      <li>
        <a href="javascript:modify('SEQ-1')"><p class="subject">왕실 교육</p></a>
        <div class="category"><span class="sort">접수중</span><span class="sort">대면</span></div>
      </li>
    </ul>
    <div class="pagination"><a href="?pageIndex=2">2</a></div>
    """
    detail_html = """
    <table>
      <tr>
        <td>1회</td><td>교육명 왕실 교육</td><td>교육일자 2099-08-01</td>
        <td>교육시간 10:00~11:00</td>
        <td>교육신청기간 2099-07-01 ~ 2099-07-31</td>
        <td>1 / 10</td><td>0 / 2</td><td>접수중</td>
      </tr>
      <tr>
        <td>2회</td><td>교육명 왕실 교육</td><td>교육일자 2099-08-02</td>
        <td>교육시간 14:00~15:00</td>
        <td>교육신청기간 2099-07-01 ~ 2099-07-31</td>
        <td>2 / 10</td><td>0 / 2</td><td>접수중</td>
      </tr>
    </table>
    """
    empty_html = "<html><body></body></html>"

    def fake_fetch(_session, url, **_kwargs):
        if "cultureSeq=SEQ-1" in url:
            return BeautifulSoup(detail_html, "html.parser")
        if "pageIndex=1" in url:
            return BeautifulSoup(list_html, "html.parser")
        return BeautifulSoup(empty_html, "html.parser")

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch)
    target = CrawlTarget(
        provider="NATIONAL_PALACE_MUSEUM",
        name="국립고궁박물관",
        branch="국립고궁박물관",
        url="https://www.gogung.go.kr/gogung/pgm/cultureEventReg/edu/list.do?menuNo=800212",
        source="test",
    )

    rows, parser, meta = municipal.collect_palace_museum_education(
        target,
        timeout=1,
        max_pages=2,
        detail_limit=10,
    )

    assert parser == "palace_museum_edu_cards+detail"
    assert meta["detail_pages"] == 1
    assert len(rows) == 2
    assert len({row["raw_url"] for row in rows}) == 2
    assert all("#mooncen-item-" in row["raw_url"] for row in rows)
    assert len({row["application_url"] for row in rows}) == 1
