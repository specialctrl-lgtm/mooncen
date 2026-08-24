from __future__ import annotations

import pytest

from Crawler import Crawler_MunicipalYaml as municipal
from Crawler.Crawler_MunicipalYaml import CrawlTarget


def _target(provider: str, url: str) -> CrawlTarget:
    return CrawlTarget(
        provider=provider,
        name="Test institution",
        branch="Test branch",
        url=url,
        source="test",
    )


def _agriculture_page(page: int, total_pages: int = 2) -> municipal.BeautifulSoup:
    return municipal.BeautifulSoup(
        f"""
        <html><body>
          <form action="/artclSearch/kr/artclList.do">
            <input name="srchWrd" value="" />
          </form>
          <form name="pageForm" action="/reservAll/kr/artclList.do">
            <input name="siteId" value="namuk" />
            <input name="page" value="{page}" />
          </form>
          <div class="_paging">
            <span class="_curPage">{page}</span>
            <span class="_totPage">{total_pages}</span>
          </div>
          <div class="event-wrap">
            <ul class="list-webzine">
              <li>
                <div class="title"><strong>Program {page}</strong></div>
                <a onclick="reserv_View('namuk','program-{page}','x')">View</a>
              </li>
            </ul>
          </div>
        </body></html>
        """,
        "html.parser",
    )


def test_national_agricultural_museum_posts_every_declared_page(
    monkeypatch,
) -> None:
    posted_pages: list[str] = []
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda *_args, **_kwargs: _agriculture_page(1),
    )

    def fake_post_soup(_session, _url, data, _timeout):
        posted_pages.append(data["page"])
        return _agriculture_page(int(data["page"]))

    monkeypatch.setattr(municipal, "post_soup", fake_post_soup)
    target = _target(
        "NATIONAL_AGRICULTURAL_MUSEUM",
        "https://www.namuk.or.kr/reservAll/kr/artclList.do",
    )

    rows, parser, meta = municipal.collect_national_agricultural_museum(
        target,
        timeout=1,
        max_pages=5,
    )

    assert parser == "namuk_reservation_list"
    assert len(rows) == 2
    assert posted_pages == ["2"]
    assert meta["pages"] == 2
    assert meta["total_pages"] == 2
    assert meta["pagination_complete"] is True
    assert meta["source_cap_reached"] is False
    assert {row["raw_fields"]["source_page"] for row in rows} == {1, 2}


def test_national_agricultural_museum_reports_a_page_cap(monkeypatch) -> None:
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda *_args, **_kwargs: _agriculture_page(1),
    )
    monkeypatch.setattr(
        municipal,
        "post_soup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("capped collection must not request another page")
        ),
    )

    rows, _parser, meta = municipal.collect_national_agricultural_museum(
        _target(
            "NATIONAL_AGRICULTURAL_MUSEUM",
            "https://www.namuk.or.kr/reservAll/kr/artclList.do",
        ),
        timeout=1,
        max_pages=1,
    )

    assert len(rows) == 1
    assert meta["pagination_complete"] is False
    assert meta["source_cap_reached"] is True


def test_national_agricultural_museum_rejects_a_repeated_page(
    monkeypatch,
) -> None:
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda *_args, **_kwargs: _agriculture_page(1),
    )
    monkeypatch.setattr(
        municipal,
        "post_soup",
        lambda *_args, **_kwargs: _agriculture_page(1),
    )

    with pytest.raises(ValueError, match="repeated page 1"):
        municipal.collect_national_agricultural_museum(
            _target(
                "NATIONAL_AGRICULTURAL_MUSEUM",
                "https://www.namuk.or.kr/reservAll/kr/artclList.do",
            ),
            timeout=1,
            max_pages=5,
        )


def test_hangeul_museum_requires_all_three_scopes_to_end(monkeypatch) -> None:
    education = municipal.BeautifulSoup(
        '<div class="apply-list"><ul></ul></div>',
        "html.parser",
    )
    events = municipal.BeautifulSoup(
        '<div class="gall-list"><ul></ul></div>',
        "html.parser",
    )

    def fake_fetch_soup(_session, url: str, **_kwargs):
        return education if "education" in url else events

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fake_fetch_soup)

    rows, parser, meta = municipal.collect_national_hangeul_museum(
        _target(
            "NATIONAL_HANGEUL_MUSEUM",
            "https://www.hangeul.go.kr/education/list.do",
        ),
        timeout=1,
        max_pages=2,
        detail_limit=0,
    )

    assert rows == []
    assert parser == "national_hangeul_education_event"
    assert meta["exhausted_scopes"] == 3
    assert meta["pagination_complete"] is True
    assert meta["source_cap_reached"] is False
    assert meta["no_current_data"] is True
