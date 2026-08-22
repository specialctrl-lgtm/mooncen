from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

import Crawler.Crawler_MunicipalYaml as municipal
import Crawler.jne_integrated_library as jne


def _target(
    path: str = "/educationIntegration.es",
    mid: str = "d50401000000",
    *,
    excluded_branch_sids: list[str] | None = None,
) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="JNE_LIBRARY_READING_INTEGRATED",
        name="JNE 통합도서관",
        branch="JNE 통합도서관",
        url=f"https://jnelib.jne.go.kr{path}?mid={mid}",
        source="test",
        extra={
            "discover_from_main_url": False,
            "excluded_branch_sids": excluded_branch_sids or [],
        },
    )


def _page(
    rows: str,
    *,
    total: int | None = None,
    last_page: int = 1,
    registry: str | None = None,
) -> BeautifulSoup:
    registry_html = registry or """
        <option value="">도서관을 선택하세요.</option>
        <option value="ALL">도서관 전체</option>
        <option value="a2">전남광주통합특별시교육청목포도서관</option>
        <option value="c1">전남광주통합특별시교육청곡성교육문화회관</option>
    """
    total_html = (
        f'<p class="page_info">전체 <span class="txt_bold">{total}</span>건</p>'
        if total is not None
        else ""
    )
    return BeautifulSoup(
        f"""
        <html><body>
          <form name="srhForm">
            <select name="selSid">{registry_html}</select>
          </form>
          {total_html}
          <table><tbody>{rows}</tbody></table>
          <a class="last" href="?nPage={last_page}">{last_page}</a>
        </body></html>
        """,
        "html.parser",
    )


def _row(
    branch: str,
    *,
    href: str = "/educationIntegration.es?mid=d50402000000&edu_seq=10&act=view",
    title: str = "과학 체험",
    schedule: str = "2026.08.01 ~ 2026.08.02",
) -> str:
    return f"""
      <tr>
        <td class="lib">{branch}</td>
        <td class="title"><a href="{href}">{title}<span class="text-day">{schedule}</span></a></td>
        <td>초등학생</td>
        <td>2026.07.01 ~ 2026.07.10</td>
        <td>접수중</td>
      </tr>
    """


def test_integrated_reading_keeps_official_branch_and_excludes_canonical_owner(
    monkeypatch,
) -> None:
    retained = "전남광주통합특별시교육청목포도서관"
    excluded = "전남광주통합특별시교육청곡성교육문화회관"
    pages = {
        1: _page(_row(retained) + _row(excluded), total=2),
        2: _page("", total=2),
    }

    monkeypatch.setattr(jne, "session", lambda: object())
    monkeypatch.setattr(
        jne,
        "fetch_soup",
        lambda _client, url, timeout: pages[
            int((parse_qs(urlparse(url).query).get("nPage") or ["1"])[0])
        ],
    )

    rows, parser, meta = jne.collect_jne_integrated_library(
        _target(excluded_branch_sids=["c1"]),
        timeout=5,
        max_pages=3,
    )

    assert parser == "jne_integrated_library_reading"
    assert len(rows) == 1
    assert rows[0]["title"] == "과학 체험"
    assert rows[0]["branch"] == retained
    assert rows[0]["branch_code"] == "a2"
    assert rows[0]["period"] == "2026-08-01 ~ 2026-08-02"
    assert rows[0]["reservation_available"] is True
    assert meta["source_rows"] == 2
    assert meta["excluded_branch_counts"] == {excluded: 1}
    assert meta["official_branch_count"] == 2
    assert meta["pages"] == 2
    assert meta["pagination_complete"] is True
    assert meta["configured_collection_error"] == ""


def test_integrated_lecture_uses_current_catalogue_contract(monkeypatch) -> None:
    branch = "전남광주통합특별시교육청목포도서관"
    pages = {
        1: _page(
            _row(
                branch,
                href="/lectureIntegration.es?mid=d50402000000&act=view&el_seq=20",
                title="로봇 교실",
                schedule="2026-08-01~2026-10-31 / 10:00 ~ 12:00",
            )
        ),
        2: _page(""),
    }
    monkeypatch.setattr(jne, "session", lambda: object())
    monkeypatch.setattr(
        jne,
        "fetch_soup",
        lambda _client, url, timeout: pages[
            int((parse_qs(urlparse(url).query).get("nPage") or ["1"])[0])
        ],
    )

    rows, parser, meta = jne.collect_jne_integrated_library(
        _target(
            "/lectureIntegration.es",
            "d50402000000",
        ),
        timeout=5,
        max_pages=3,
    )

    assert parser == "jne_integrated_library_lecture"
    assert rows[0]["category"] == "평생학습강좌"
    assert rows[0]["period"] == "2026-08-01 ~ 2026-10-31"
    assert rows[0]["schedule_raw"].endswith("10:00 ~ 12:00")
    assert meta["pagination_complete"] is True


def test_integrated_catalogue_accepts_single_cell_empty_page_sentinel(monkeypatch) -> None:
    branch = "전남광주통합특별시교육청목포도서관"
    sentinel = '<tr><td colspan="5">등록된 자료가 없습니다.</td></tr>'
    pages = {
        1: _page(_row(branch), total=1),
        2: _page(sentinel, total=1),
    }
    monkeypatch.setattr(jne, "session", lambda: object())
    monkeypatch.setattr(
        jne,
        "fetch_soup",
        lambda _client, url, timeout: pages[
            int((parse_qs(urlparse(url).query).get("nPage") or ["1"])[0])
        ],
    )

    rows, _parser, meta = jne.collect_jne_integrated_library(
        _target(),
        timeout=5,
        max_pages=3,
    )

    assert len(rows) == 1
    assert meta["pagination_complete"] is True
    assert meta["configured_collection_error"] == ""


def test_integrated_catalogue_fails_closed_on_unregistered_branch(monkeypatch) -> None:
    pages = {
        1: _page(_row("목록에 없는 기관"), total=1),
        2: _page("", total=1),
    }
    monkeypatch.setattr(jne, "session", lambda: object())
    monkeypatch.setattr(
        jne,
        "fetch_soup",
        lambda _client, url, timeout: pages[
            int((parse_qs(urlparse(url).query).get("nPage") or ["1"])[0])
        ],
    )

    _rows, _parser, meta = jne.collect_jne_integrated_library(
        _target(),
        timeout=5,
        max_pages=3,
    )

    assert meta["pagination_complete"] is False
    assert "unregistered official branch rows" in meta["configured_collection_error"]


def test_integrated_catalogue_rejects_reversed_source_dates() -> None:
    assert jne._has_reversed_date_range("2026-05-16 ~ 2026-04-29") is True
    assert jne._has_reversed_date_range("2026-04-29 ~ 2026-05-16") is False


def test_integrated_catalogue_filters_non_program_inventory(monkeypatch) -> None:
    branch = "전남광주통합특별시교육청목포도서관"
    pages = {
        1: _page(
            _row(branch, title="[8월 전집 대출] 자연 이야기")
            + _row(branch, title="[3분기] 사물함 1")
            + _row(branch, title="가족 과학 체험"),
            total=3,
        ),
        2: _page("", total=3),
    }
    monkeypatch.setattr(jne, "session", lambda: object())
    monkeypatch.setattr(
        jne,
        "fetch_soup",
        lambda _client, url, timeout: pages[
            int((parse_qs(urlparse(url).query).get("nPage") or ["1"])[0])
        ],
    )

    rows, _parser, meta = jne.collect_jne_integrated_library(
        _target(),
        timeout=5,
        max_pages=3,
    )

    assert [row["title"] for row in rows] == ["가족 과학 체험"]
    assert meta["source_rows"] == 3
    assert meta["out_of_scope_counts"] == {"전집대출": 1, "사물함": 1}


def test_integrated_catalogue_enriches_current_detail(monkeypatch) -> None:
    branch = "전남광주통합특별시교육청목포도서관"
    pages = {
        1: _page(
            _row(
                branch,
                title="가족 과학 체험",
                schedule="2099.08.01 ~ 2099.08.01",
            ),
            total=1,
        ),
        2: _page("", total=1),
    }
    detail = BeautifulSoup(
        """
        <table>
          <tr><th>강좌명</th><td>가족 과학 체험</td></tr>
          <tr><th>대상</th><td>초등학생 가족</td></tr>
          <tr><th>수강기간</th><td>20990801 ~ 20990801</td></tr>
          <tr><th>수강시간</th><td>10:00 ~ 12:00</td></tr>
          <tr><th>교육장소</th><td>어린이자료실</td></tr>
          <tr><th>강사명</th><td>홍길동</td></tr>
          <tr><th>수강료</th><td>무료</td></tr>
          <tr><th>내용</th><td>과학 실험</td></tr>
        </table>
        """,
        "html.parser",
    )

    def fake_fetch(_client, url, timeout):
        if "act=view" in url:
            return detail
        return pages[
            int((parse_qs(urlparse(url).query).get("nPage") or ["1"])[0])
        ]

    monkeypatch.setattr(jne, "session", lambda: object())
    monkeypatch.setattr(jne, "fetch_soup", fake_fetch)

    rows, _parser, meta = jne.collect_jne_integrated_library(
        _target(),
        timeout=5,
        max_pages=3,
        detail_limit=5,
    )

    assert rows[0]["target"] == "초등학생 가족"
    assert rows[0]["venue_name"] == "어린이자료실"
    assert rows[0]["instructor"] == "홍길동"
    assert rows[0]["fee"] == "무료"
    assert (
        rows[0]["schedule_raw"]
        == "2099-08-01 ~ 2099-08-01 / 10:00 ~ 12:00"
    )
    assert meta["detail_pages"] == 1
    assert meta["details_complete"] is True


def test_municipal_dispatch_routes_integrated_jne_catalogue(monkeypatch) -> None:
    expected = ([{"title": "ok"}], "jne_integrated_library_reading", {"pages": 1})
    monkeypatch.setattr(
        jne,
        "collect_jne_integrated_library",
        lambda target, timeout, max_pages, detail_limit: expected,
    )

    result = municipal.crawl_experience_from_url(
        _target(),
        timeout=5,
        max_depth=0,
        max_pages=3,
        detail_limit=0,
    )

    assert result == expected
