from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_jincheon as jincheon


class _Response:
    def __init__(self, url: str, html: str):
        self.url = url
        self.content = html.encode("utf-8")

    def raise_for_status(self) -> None:
        return None


class _Session:
    def close(self) -> None:
        return None


def _list_html(
    *,
    page: int,
    last: int,
    with_row: bool = True,
    status: str = "신청중",
    apply_period: str = "2026.07.01 09:00 ~ 2026.08.01 18:00",
    identity: str = "4001",
    title: str = "미래 디지털 강좌",
    total: int = 1,
    courses: list[tuple[str, str]] | None = None,
) -> str:
    row = ""
    if with_row:
        rows = []
        for row_identity, row_title in courses or [(identity, title)]:
            rows.append(f"""
        <li class="item">
          <a class="tit" href="sub.do?menukey=3236&amp;mode=view&amp;cnteduNo={row_identity}">{row_title}</a>
          <span class="btn_edu"><a>온라인신청</a><a>{status}</a><a>교육준비</a></span>
          <ul class="list">
            <li><strong>운영기관</strong>: ☏진천군평생학습관 (043-000-0000)</li>
            <li><strong>접수기간</strong>: {apply_period}</li>
            <li><strong>교육기간</strong>: 2026.08.02 ~ 2026.08.30.</li>
            <li><strong>교육요일/교육시간</strong>: 토요일 / 10:00~12:00</li>
            <li><strong>신청/정원</strong>: 3명 / 10명</li>
            <li><strong>교육장소</strong>: 디지털교육장</li>
          </ul>
        </li>
        """)
        row = "".join(rows)
    return f"""
    <html><body>
      <div class="bbs_count"><span>총 게시물 <strong>{total}</strong> 개</span>,
        <span>페이지 <strong>{page}</strong> / {last}</span></div>
      <ul class="tb_edu">{row}</ul>
    </body></html>
    """


def _detail_html(
    *,
    status: str = "신청중",
    apply_period: str = "2026.07.01 09:00 ~ 2026.08.01 18:00",
    include_control: bool = True,
    title: str = "미래 디지털 강좌",
) -> str:
    control = """
      <a class="btnM_red" href="#none"
         onclick="alert('로그인이 필요한 서비스입니다.'); location.href='/member/index.do?mode=login&amp;rtnChk=Y';">신청하기</a>
    """ if include_control else ""
    return f"""
    <html><body>
      <div class="bbs_edu_view">
        <h3 class="conH1">{title}</h3>
        <div class="edu_btn"><span>{status}</span><span>교육준비</span></div>
        <ul class="edu_con">
          <li><strong>운영기관</strong>: ☏진천군평생학습관 (043-000-0000)</li>
          <li><strong>접수기간</strong>: {apply_period}</li>
          <li><strong>교육기간</strong>: 2026.08.02 ~ 2026.08.30.</li>
          <li><strong>신청/정원</strong>: 3명 / 10명</li>
          <li><strong>교육요일/교육시간</strong>: 토요일 / 10:00~12:00</li>
          <li><strong>교육비</strong>: 무료</li>
          <li><strong>교육장소</strong>: 디지털교육장</li>
          <li><strong>재료비</strong>: 무료</li>
          <li><strong>교육대상</strong>: 성인</li>
          <li><strong>신청방법</strong>: 온라인 접수</li>
        </ul>
      </div>
      {control}
    </body></html>
    """


def _fetcher(_session, url: str, _timeout: int):
    query = parse_qs(urlparse(url).query)
    if query.get("mode") == ["view"]:
        assert query.get("cnteduNo") == ["4001"]
        return _Response(url, _detail_html())
    page = int(query.get("pageIndex", ["1"])[0])
    return _Response(url, _list_html(page=page, last=1, with_row=page == 1))


def test_exact_canonical_target_only():
    target = {"provider": jincheon.JINCHEON_PROVIDER, "url": jincheon.JINCHEON_CANONICAL_URL}
    assert jincheon.is_jincheon_education_target(target)
    assert not jincheon.is_jincheon_education_target({**target, "url": target["url"] + "&mode=list"})
    assert not jincheon.is_jincheon_education_target(
        {"provider": jincheon.JINCHEON_OLD_FILTERED_PROVIDER, "url": target["url"]}
    )


def test_complete_two_scope_snapshot_and_privacy_allowlist():
    rows, parser, meta = jincheon.collect_jincheon_education(
        {"provider": jincheon.JINCHEON_PROVIDER, "url": jincheon.JINCHEON_CANONICAL_URL},
        today="2026-07-22",
        max_pages=8,
        detail_limit=2,
        session_factory=_Session,
        fetcher=_fetcher,
    )
    assert parser == jincheon.JINCHEON_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["source_rows"] == 1
    assert meta["scope_duplicate_count"] == 1
    assert meta["detail_pages"] == 1
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "OPEN"
    assert row["apply_start"] == "2026-07-01"
    assert row["apply_end"] == "2026-08-01"
    assert row["reservation_available"] is True
    assert row["branch"] == "진천군평생학습관"
    assert "043-" not in repr(row)
    assert row["raw_fields"]["detail_verified"] is True


def test_application_preparation_is_a_future_scheduled_course_without_control():
    apply_period = "2026.08.03 10:00 ~ 2026.08.14 18:00"

    def fetcher(_session, url: str, _timeout: int):
        query = parse_qs(urlparse(url).query)
        if query.get("mode") == ["view"]:
            return _Response(
                url,
                _detail_html(
                    status="신청준비",
                    apply_period=apply_period,
                    include_control=False,
                ),
            )
        page = int(query.get("pageIndex", ["1"])[0])
        return _Response(
            url,
            _list_html(
                page=page,
                last=1,
                with_row=page == 1,
                status="신청준비",
                apply_period=apply_period,
            ),
        )

    rows, _, meta = jincheon.collect_jincheon_education(
        {"provider": jincheon.JINCHEON_PROVIDER, "url": jincheon.JINCHEON_CANONICAL_URL},
        today="2026-07-22",
        max_pages=8,
        detail_limit=2,
        session_factory=_Session,
        fetcher=fetcher,
    )

    assert meta["configured_collection_error"] == ""
    assert rows[0]["status"] == "SCHEDULED"
    assert rows[0]["reservation_available"] is False
    assert rows[0]["apply_start"] == "2026-08-03"
    assert rows[0]["application_type"] == "INFO_ONLY"


def test_collection_discovers_rows_beyond_stale_declared_last_page():
    page_one = [(str(4100 + offset), f"미래 디지털 강좌 {offset}") for offset in range(10)]
    overflow = [("4110", "미래 디지털 강좌 10")]
    titles = dict(page_one + overflow)

    def fetcher(_session, url: str, _timeout: int):
        query = parse_qs(urlparse(url).query)
        if query.get("mode") == ["view"]:
            identity = query["cnteduNo"][0]
            return _Response(url, _detail_html(title=titles[identity]))
        page = int(query.get("pageIndex", ["1"])[0])
        if query.get("searchCnd") == ["CND04"]:
            return _Response(
                url,
                _list_html(page=page, last=1, with_row=False, total=0),
            )
        courses = page_one if page == 1 else overflow if page == 2 else []
        return _Response(
            url,
            _list_html(
                page=page,
                last=1,
                with_row=bool(courses),
                courses=courses,
                total=10,
            ),
        )

    rows, _, meta = jincheon.collect_jincheon_education(
        {"provider": jincheon.JINCHEON_PROVIDER, "url": jincheon.JINCHEON_CANONICAL_URL},
        today="2026-07-22",
        max_pages=9,
        detail_limit=20,
        session_factory=_Session,
        fetcher=fetcher,
    )

    assert meta["configured_collection_error"] == ""
    assert meta["catalogues"]["window"]["declared_data_pages"] == 1
    assert meta["catalogues"]["window"]["data_pages"] == 2
    assert meta["catalogues"]["window"]["overflow_data_pages"] == 1
    assert meta["catalogues"]["window"]["empty_sentinel_page"] == 3
    assert meta["catalogues"]["window"]["advertised_total_delta"] == 1
    assert len(rows) == 11


def test_fail_closed_when_list_request_cap_is_too_small():
    rows, _, meta = jincheon.collect_jincheon_education(
        {"provider": jincheon.JINCHEON_PROVIDER, "url": jincheon.JINCHEON_CANONICAL_URL},
        today="2026-07-22",
        max_pages=7,
        detail_limit=2,
        session_factory=_Session,
        fetcher=_fetcher,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages" in meta["configured_collection_error"]


def test_page_parser_accepts_data_beyond_stale_declared_last_page():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_list_html(page=2, last=1, with_row=True), "html.parser")
    page = jincheon._parse_page(soup, "window", 2)
    assert page.displayed_page == 2
    assert page.last_page == 1
    assert len(page.rows) == 1


@pytest.mark.skipif(os.getenv("MOONCEN_LIVE_CRAWL") != "1", reason="opt-in live crawl")
def test_live_jincheon_snapshot():
    rows, parser, meta = jincheon.collect_jincheon_education(
        {"provider": jincheon.JINCHEON_PROVIDER, "url": jincheon.JINCHEON_CANONICAL_URL},
        timeout=40,
        max_pages=50,
        detail_limit=200,
        max_workers=2,
    )
    assert parser == jincheon.JINCHEON_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert len(rows) == meta["returned_count"]
