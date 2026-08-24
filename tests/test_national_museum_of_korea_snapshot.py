from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="NATIONAL_MUSEUM_OF_KOREA",
        name="국립중앙박물관",
        branch="국립중앙박물관",
        url="https://modu.museum.go.kr/learn?museum=1",
        source="test",
    )


def _card(detail_id: int) -> str:
    return f"""
    <li>
      <div class="card type02">
        <button onclick="goDetail({detail_id})">상세</button>
        <strong class="title">박물관 교육 {detail_id}</strong>
        <span class="writer">국립중앙박물관</span>
        <div class="badge"><span>접수중</span><span>어린이</span></div>
        <dl class="info_text"><dt>교육기간</dt><dd>2026-08-01 ~ 2026-08-31</dd></dl>
        <dl class="info_text"><dt>신청대상자</dt><dd>초등학생</dd></dl>
      </div>
    </li>
    """


def _first_page(detail_ids: list[int], *, total_pages: int = 3) -> str:
    return (
        f"<html><script>var totalPages = {total_pages};</script>"
        f"<ul id='listUl'>{''.join(_card(value) for value in detail_ids)}</ul></html>"
    )


def _detail(detail_id: int) -> BeautifulSoup:
    return BeautifulSoup(
        f"""
        <html><body>
          <h2 class="detail_title">박물관 교육 {detail_id}</h2>
          <div class="detail_cont">박물관 교육 {detail_id}의 상세 교육 내용입니다.</div>
          <table>
            <tr><th>박물관</th><td>국립중앙박물관</td></tr>
            <tr><th>교육기간</th><td>2026-08-01 ~ 2026-08-31</td></tr>
            <tr><th>접수기간</th><td>2026-07-01 ~ 2026-07-31</td></tr>
            <tr><th>교육대상</th><td>초등학생</td></tr>
            <tr><th>참가비</th><td>무료</td></tr>
            <tr><th>교육장소</th><td>교육관</td></tr>
            <tr><th>교육그룹</th><td>어린이</td></tr>
          </table>
        </body></html>
        """,
        "lxml",
    )


class Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class ModuFixture:
    def __init__(
        self,
        *,
        empty_page: int = 0,
        failed_detail: int = 0,
    ) -> None:
        self.empty_page = empty_page
        self.failed_detail = failed_detail
        self.list_calls: list[int] = []
        self.detail_calls: list[int] = []

    def session_get(
        self,
        url: str,
        *,
        params: dict[str, Any],
        timeout: int,
    ) -> Response:
        del timeout
        if url == municipal.MODU_LEARN_URL:
            self.list_calls.append(1)
            return Response(_first_page(list(range(1001, 1009))))
        assert url == municipal.MODU_LEARN_APPEND_URL
        page = int(params["page"])
        self.list_calls.append(page)
        if page == self.empty_page:
            return Response("")
        detail_ids = list(range(1009, 1017)) if page == 2 else list(range(1017, 1021))
        return Response("".join(_card(value) for value in detail_ids))

    def fetch_detail(
        self,
        _session: Any,
        url: str,
        timeout: int = 20,
    ) -> BeautifulSoup:
        del timeout
        detail_id = int(urlparse(url).path.rsplit("/", 1)[-1])
        self.detail_calls.append(detail_id)
        if detail_id == self.failed_detail:
            raise RuntimeError("detail unavailable")
        return _detail(detail_id)


def _collect(
    monkeypatch: pytest.MonkeyPatch,
    fixture: ModuFixture,
    *,
    max_pages: int = 3,
    detail_limit: int = 20,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    session = type("Session", (), {"get": fixture.session_get})()
    monkeypatch.setattr(municipal, "session", lambda: session)
    monkeypatch.setattr(municipal, "fetch_soup", fixture.fetch_detail)
    return municipal.collect_national_museum_of_korea(
        _target(),
        timeout=5,
        max_pages=max_pages,
        detail_limit=detail_limit,
    )


def test_modu_three_page_snapshot_is_complete_and_persistable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = ModuFixture()
    rows, parser, meta = _collect(monkeypatch, fixture)

    assert parser == "modu_learn_list+detail"
    assert len(rows) == 20
    assert fixture.list_calls == [1, 2, 3]
    assert len(fixture.detail_calls) == 20
    assert meta["pages"] == 3
    assert meta["expected_pages"] == 3
    assert meta["detail_pages"] == 20
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert "configured_collection_error" not in meta


def test_modu_page_cap_blocks_partial_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = ModuFixture()
    rows, _parser, meta = _collect(monkeypatch, fixture, max_pages=2)

    assert rows == []
    assert fixture.list_calls == [1]
    assert fixture.detail_calls == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]


def test_modu_missing_intermediate_page_blocks_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = ModuFixture(empty_page=2)
    rows, _parser, meta = _collect(monkeypatch, fixture)

    assert rows == []
    assert fixture.list_calls == [1, 2]
    assert fixture.detail_calls == []
    assert meta["pagination_complete"] is False
    assert "page 2 exposes 0 of 8 expected rows" in meta["configured_collection_error"]


def test_modu_detail_cap_blocks_partial_enrichment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = ModuFixture()
    rows, _parser, meta = _collect(monkeypatch, fixture, detail_limit=19)

    assert rows == []
    assert fixture.detail_calls == []
    assert meta["detail_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_modu_detail_failure_blocks_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = ModuFixture(failed_detail=1010)
    rows, _parser, meta = _collect(monkeypatch, fixture)

    assert rows == []
    assert len(fixture.detail_calls) == 20
    assert meta["details_complete"] is False
    assert "detail collection failed" in meta["configured_collection_error"]
