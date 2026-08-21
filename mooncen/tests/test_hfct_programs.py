from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import Crawler_MunicipalYaml as municipal


class _FakeSession:
    def __init__(self) -> None:
        self.headers = {}

    def close(self) -> None:
        pass


def _target() -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        "MUNI_WWW_HFCT_OR_KR_05C08858",
        "화순군문화관광재단",
        "전라남도 화순군",
        "https://www.hfct.or.kr/edu.do?S=S01&M=0502010000",
        "test",
    )


def _list_item(
    *,
    list_no: int,
    title: str,
    period: str,
    apply_period: str = "2026-07-21 ~ 2026-08-02",
) -> str:
    return f"""
    <li class="item">
      <div class="img_box"><img src="/image/{list_no}.jpg"></div>
      <div class="cont">
        <div class="attach">
          <span class="pro_tp">교육프로그램</span>
          <span class="state st1">진행중</span>
        </div>
        <div class="pg_tit">
          <a href="/edu.do?S=S01&amp;M=0502010000&amp;mod=detail&amp;list_no={list_no}">{title}</a>
        </div>
        <ul class="info_list">
          <li><span class="tit">신청기간</span><span class="txt">{apply_period}</span></li>
          <li><span class="tit">일시</span><span class="txt">{period}</span></li>
          <li><span class="tit">장소</span><span class="txt">화순군민회관</span></li>
          <li><span class="tit">참여대상</span><span class="txt">2인 이상 가족</span></li>
        </ul>
      </div>
    </li>
    """


def test_hfct_collects_only_current_programs_with_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_page = (
        "<ul>"
        + _list_item(
            list_no=52,
            title="가족 문화 체험",
            period="2099-08-04 ~ 2099-08-13",
        )
        + _list_item(
            list_no=1,
            title="종료된 체험",
            period="2020-01-01 ~ 2020-01-02",
        )
        + "</ul>"
    )
    detail_page = """
    <div class="pro_view_wp">
      <div class="view_hd">
        <ul class="info">
          <li><div class="tit">일시</div><div class="txt">2099-08-04 ~ 2099-08-13</div></li>
          <li><div class="tit">시간</div><div class="txt">14:00~16:00</div></li>
          <li><div class="tit">장소</div><div class="txt">화순군민회관 남산홀</div></li>
          <li><div class="tit">참여대상</div><div class="txt">초등학생 이상 가족</div></li>
          <li><div class="tit">문의</div><div class="txt">070-0000-0000</div></li>
        </ul>
      </div>
      <div class="btn_box">
        <a class="app_btn" href="https://apply.example/program">신청하기</a>
        <a class="app_btn" href="/edu.do">목록</a>
      </div>
      <div class="view_bd">가족이 함께 만드는 문화예술 체험입니다.</div>
    </div>
    """
    requests: list[str] = []

    def fetch(_session, url: str, timeout: int):
        assert timeout == 10
        requests.append(url)
        query = parse_qs(urlparse(url).query)
        if query.get("mod") == ["detail"]:
            return BeautifulSoup(detail_page, "lxml")
        return BeautifulSoup(current_page, "lxml")

    monkeypatch.setattr(municipal, "session", _FakeSession)
    monkeypatch.setattr(municipal, "fetch_soup", fetch)

    rows, parser, meta = municipal.collect_hfct_programs(
        _target(),
        timeout=10,
        max_pages=10,
        detail_limit=10,
    )

    assert parser == "hfct_program_list_detail"
    assert len(rows) == 1
    assert rows[0]["title"] == "가족 문화 체험"
    assert rows[0]["target"] == "초등학생 이상 가족"
    assert rows[0]["fee"] == "별도 안내"
    assert rows[0]["period"] == "2099-08-04 ~ 2099-08-13"
    assert rows[0]["venue_name"] == "화순군민회관 남산홀"
    assert rows[0]["category"] == "교육프로그램"
    assert rows[0]["schedule_raw"] == "14:00~16:00"
    assert rows[0]["application_url"] == "https://apply.example/program"
    assert meta["expired_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert len(requests) == 2
