from __future__ import annotations

from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as crawler


def test_bukgu_daegu_uses_the_explicit_title_node_for_recruitment_titles() -> None:
    target = crawler.CrawlTarget(
        provider=crawler.BUKGU_DAEGU_PROVIDER,
        name="대구 북구 통합예약",
        branch="대구 북구 통합예약",
        url="https://www.buk.daegu.kr/reserve/index.do?menu_id=00002617",
        source="test",
    )
    card = BeautifulSoup(
        """
        <div class="lec_list">
          <span class="ing_box">접수중</span>
          <p class="le_name"><span>★ 나도야, 강사 ★ 강사 모집</span></p>
          <ul>
            <li><span>접수기간</span>2026-04-06 ~ 2026-12-31</li>
            <li><span>교육기간</span>강사모집 후 결정</li>
          </ul>
          <a href="?program_id=1">접수신청</a>
        </div>
        """,
        "lxml",
    ).select_one(".lec_list")

    row = crawler.bukgu_daegu_row_from_card(
        target,
        target.url,
        "평생학습프로그램",
        card,
    )

    assert row is not None
    assert row["title"] == "★ 나도야, 강사 ★ 강사 모집"


def test_bukgu_daegu_fanout_shares_the_global_page_budget(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_fetch_soup(_session, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 5
        requested_urls.append(url)
        return BeautifulSoup(
            "<div class='pagination'></div><div class='lec_list'></div>",
            "lxml",
        )

    monkeypatch.setattr(crawler, "session", object)
    monkeypatch.setattr(crawler, "fetch_soup", fake_fetch_soup)
    monkeypatch.setattr(
        crawler,
        "bukgu_daegu_row_from_card",
        lambda target, current_url, category, card: {
            "provider": target.provider,
            "provider_course_id": current_url,
            "title": current_url,
            "branch": target.branch,
            "category": category,
            "raw_url": current_url,
        },
    )
    target = crawler.CrawlTarget(
        provider=crawler.BUKGU_DAEGU_PROVIDER,
        name="대구 북구 통합예약",
        branch="대구 북구 통합예약",
        url="https://www.buk.daegu.kr/reserve/index.do?menu_id=00002617",
        source="test",
        extra={
            "list_urls": [
                "https://www.buk.daegu.kr/reserve/index.do?menu_id=00002617",
                "https://www.buk.daegu.kr/reserve/index.do?menu_id=00002777",
                "https://www.buk.daegu.kr/reserve/index.do?menu_id=00002965",
                "https://www.buk.daegu.kr/reserve/index.do?menu_id=00002619",
            ]
        },
    )

    rows, parser, meta = crawler.collect_bukgu_daegu_reservation(
        target,
        timeout=5,
        max_pages=2,
        detail_limit=0,
    )

    assert len(requested_urls) == 2
    assert len(rows) == 2
    assert parser == "bukgu_daegu_lec_list+detail"
    assert meta["pages"] == 2
    assert meta["page_cap_reached"] is True
    assert meta["pagination_complete"] is False
