from __future__ import annotations

from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler.Crawler_MunicipalYaml import CrawlTarget


def test_empty_verified_uljin_branch_calendar_is_no_current_data(monkeypatch) -> None:
    html = """
    <html>
      <head><title>이달의 행사 | 도서관행사 | 울진군통합도서관</title></head>
      <body>
        <table>
          <thead>
            <tr>
              <th>일요일</th><th>월요일</th><th>화요일</th><th>수요일</th>
              <th>목요일</th><th>금요일</th><th>토요일</th>
            </tr>
          </thead>
          <tbody><tr><td>1</td></tr></tbody>
        </table>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    target = CrawlTarget(
        provider="MUNI_LIB_ULJIN_GO_KR_84BA0199",
        name="울진작은도서관",
        branch="울진작은도서관",
        url="https://lib.uljin.go.kr/content/02schedule/01_01.php?p_cate=48",
        source="test",
    )

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", lambda *_args, **_kwargs: soup)

    rows, parser, meta = municipal.collect_uljin_library_programs(target, timeout=1, max_pages=1)

    assert rows == []
    assert parser == "uljin_library_calendar"
    assert meta["verified_calendar_pages"] == meta["expected_calendar_pages"] == 1
    assert meta["collection_complete"] is True
    assert meta["no_current_data"] is True
