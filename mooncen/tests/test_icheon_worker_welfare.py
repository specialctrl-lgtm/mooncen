from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider="ICHEON_WORKER_WELFARE",
        name="이천시 노동자복지관 평생학습 프로그램",
        branch="이천시노동자복지관",
        url="https://www.icheon-hrd.or.kr/main/program/rule.jsp",
        source="test",
        priority=1,
        region="경기도 이천시",
        extra={},
    )


def _list_page(page: int, last: int, identity: str, status: str) -> BeautifulSoup:
    return BeautifulSoup(
        f"""
        <html><body>
          <p>현재페이지 : {page} / {last}</p>
          <table>
            <tr><th>강좌</th><th>분류</th><th>접수</th><th>정원</th>
                <th>신청</th><th>대기</th><th>상태</th></tr>
            <tr>
              <td><span class="tag">야간</span>
                <a href="/program/programInfoDetail.do?idx={identity}">과정 {identity}</a>
              </td>
              <td>교육</td><td>26.07.01 ~ 26.07.10</td>
              <td>20</td><td>5</td><td>2</td>
              <td><span class="edutag">접수마감</span>
                  <span class="edutag">{status}</span></td>
            </tr>
          </table>
        </body></html>
        """,
        "lxml",
    )


def _detail_page(identity: str) -> BeautifulSoup:
    return BeautifulSoup(
        f"""
        <html><body><div class="board_view">
          <p class="title">과정 {identity}</p>
          <dl>
            <dt>교육과정</dt><dd>정보화교육</dd>
            <dt>정시 모집 기간</dt><dd>26.07.01 10:00 ~ 26.07.10 17:00</dd>
            <dt>교육기간</dt><dd>2026-08-01 ~ 2026-08-31</dd>
            <dt>교육일시</dt><dd>화,목 19:00 ~ 21:00</dd>
            <dt>수강료</dt><dd>무료</dd>
            <dt>강의실</dt><dd>4층 강의실</dd>
          </dl>
          <div class="con">
            ◈ 일반모집대상: 지역주민 및 일반인
            ◈ 최소연령제한: 만14세 이상
            ◈ 인터넷접수 후 참여
          </div>
        </div></body></html>
        """,
        "lxml",
    )


def test_icheon_worker_scans_declared_pages_and_skips_ended_courses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    def fetch(_session, url: str, **_kwargs):
        requested.append(url)
        if "programInfoDetail.do" in url:
            return _detail_page("CURRENT")
        page = int((parse_qs(urlparse(url).query).get("pgno") or ["1"])[0])
        return (
            _list_page(1, 2, "CURRENT", "교육중")
            if page == 1
            else _list_page(2, 2, "ENDED", "교육종료")
        )

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fetch)

    rows, parser, meta = municipal.collect_icheon_worker_welfare(
        _target(),
        timeout=10,
        max_pages=10,
        detail_limit=10,
    )

    assert parser == "icheon_worker_welfare_table+detail"
    assert len(rows) == 1
    assert rows[0]["title"] == "과정 CURRENT"
    assert rows[0]["schedule_raw"] == "화,목 19:00 ~ 21:00"
    assert rows[0]["period"] == "2026-08-01 ~ 2026-08-31"
    assert rows[0]["apply_period"] == "2026-07-01 10:00 ~ 2026-07-10 17:00"
    assert rows[0]["target"] == "지역주민 및 일반인 / 만14세 이상"
    assert rows[0]["category"] == "야간 / 정보화교육"
    assert meta["pages"] == 2
    assert meta["declared_last_page"] == 2
    assert meta["terminal_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["detail_enrichment_complete"] is True
    assert meta["source_cap_reached"] is False
    assert len(requested) == 3


def test_icheon_worker_page_cap_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, url, **_kwargs: (
            _detail_page("CURRENT")
            if "programInfoDetail.do" in url
            else _list_page(1, 2, "CURRENT", "교육중")
        ),
    )

    rows, _parser, meta = municipal.collect_icheon_worker_welfare(
        _target(),
        timeout=10,
        max_pages=1,
        detail_limit=10,
    )

    assert len(rows) == 1
    assert meta["pagination_complete"] is False
    assert meta["page_cap_reached"] is True
    assert meta["source_cap_reached"] is True
