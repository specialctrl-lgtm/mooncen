from __future__ import annotations

import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_goesan as goesan


class _Response:
    def __init__(self, url: str, html: str):
        self.url = url
        self.content = html.encode("utf-8")

    def raise_for_status(self) -> None:
        return None


class _Session:
    def close(self) -> None:
        return None


def _course_item(
    identity: str,
    title: str,
    *,
    education_period: str,
    status: str,
) -> str:
    return f"""
    <li class="item">
      <a class="tit"
         href="GslllEduView.do?key=1894&amp;cnteduNo={identity}">{title}</a>
      <span class="btn_edu">{status}</span>
      <ul class="list">
        <li><strong>운영기관</strong>: 평생학습관 (043-830-0000)</li>
        <li><strong>접수기간</strong>: 2026-07-01 09:00 ~ 2026-08-01 18:00</li>
        <li><strong>신청/정원</strong>: 3명 / 10명</li>
        <li><strong>교육기간</strong>: {education_period}</li>
        <li><strong>접수방법</strong>: 온라인</li>
        <li><strong>교육대상</strong>: 성인</li>
        <li><strong>교육시간</strong>: 토 10:00~12:00</li>
      </ul>
    </li>
    """


def _list_html(*, page: int, rows: str, total: int = 2, last: int = 1) -> str:
    return f"""
    <html><body>
      <div class="board_info">전체게시물 {total}, 현재페이지 {page}/{last}</div>
      <ul class="tb_edu">{rows}</ul>
    </body></html>
    """


def _detail_html() -> str:
    return """
    <html><body>
      <div class="bbs_edu_view">
        <h3>괴산 미래교육</h3>
        <div class="edu_btn"><span>신청중</span></div>
        <ul class="edu_con">
          <li><strong>운영기관</strong>: 평생학습관 (043-830-0000)</li>
          <li><strong>접수기간</strong>: 2026-07-01 09:00 ~ 2026-08-01 18:00</li>
          <li><strong>신청/정원</strong>: 3명 / 10명</li>
          <li><strong>교육기간</strong>: 2026-08-02 ~ 2026-08-30</li>
          <li><strong>접수방법</strong>: 온라인</li>
          <li><strong>교육대상</strong>: 성인</li>
          <li><strong>교육시간</strong>: 토 10:00~12:00</li>
        </ul>
      </div>
      <a href="/gslll/GslllEduApplyForm.do?key=1894&amp;cnteduNo=7001">
        신청하기
      </a>
    </body></html>
    """


def _official_empty_detail_shell() -> str:
    return """
    <!DOCTYPE html>
    <html lang="ko"><head><title>괴산군청</title>
      <link rel="stylesheet" href="/site/common/css/style.css">
      <link rel="stylesheet" href="/site/cyber/css/style.css">
      <link rel="stylesheet" href="/site/sport/css/style.css">
      <link rel="stylesheet" href="/site/rfarm/css/style.css">
      <script src="/site/common/js/jquery-1.11.3.min.js"></script>
    </head><body></body></html>
    """


_CURRENT = _course_item(
    "7001",
    "괴산 미래교육",
    education_period="2026-08-02 ~ 2026-08-30",
    status="신청중",
)
_EXPIRED = _course_item(
    "7000",
    "종료된 괴산교육",
    education_period="2026-06-01 ~ 2026-06-30",
    status="신청마감",
)


def _fetcher(_session, url: str, _timeout: int):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if parsed.path == goesan.GOESAN_DETAIL_PATH:
        assert query.get("cnteduNo") == ["7001"]
        return _Response(url, _detail_html())
    page = int(query.get("pageIndex", ["1"])[0])
    rows = _CURRENT + _EXPIRED if page == 1 else ""
    return _Response(url, _list_html(page=page, rows=rows))


def _target() -> dict[str, str]:
    return {
        "provider": goesan.GOESAN_PROVIDER,
        "url": goesan.GOESAN_CANONICAL_URL,
    }


def test_exact_canonical_target_only() -> None:
    assert goesan.is_goesan_education_target(_target())
    assert not goesan.is_goesan_education_target(
        {**_target(), "provider": "MUNI_WRONG"}
    )
    assert not goesan.is_goesan_education_target(
        {**_target(), "url": goesan.GOESAN_CANONICAL_URL + "&pageIndex=1"}
    )
    assert not goesan.is_goesan_education_target(
        {**_target(), "url": goesan.GOESAN_CANONICAL_URL + "&ignored="}
    )
    assert not goesan.is_goesan_education_target(
        {**_target(), "url": goesan.GOESAN_CANONICAL_URL + "#courses"}
    )


def test_complete_snapshot_filters_expired_and_verifies_current_detail() -> None:
    rows, parser, meta = goesan.collect_goesan_education(
        _target(),
        today="2026-07-22",
        max_pages=4,
        detail_limit=1,
        session_factory=_Session,
        fetcher=_fetcher,
    )

    assert parser == goesan.GOESAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == meta["source_rows"] == 2
    assert meta["data_pages"] == 1
    assert meta["empty_sentinel_page"] == 2
    assert meta["list_requests"] == 4
    assert meta["current_source_count"] == 1
    assert meta["expired_count"] == 1
    assert meta["detail_pages"] == 1
    assert meta["returned_count"] == 1
    assert meta["status_counts"] == {"OPEN": 1}

    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"].endswith(":7001")
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["branch"] == "괴산군평생학습관"
    assert row["address"] == (
        "충청북도 괴산군 괴산읍 읍내로 184, 괴산군립도서관 3층"
    )
    assert row["venue_address"] == row["address"]
    assert row["branch_location_verified"] is True
    assert row["capacity_current"] == 3
    assert row["capacity_total"] == 10
    assert row["raw_fields"]["detail_verified"] is True
    assert row["raw_fields"]["application_control_present"] is True
    assert "043-830-0000" not in repr(row)


def test_exact_official_empty_detail_shell_uses_stable_list_identity_only() -> None:
    def empty_detail_fetcher(_session, url: str, _timeout: int):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == goesan.GOESAN_DETAIL_PATH:
            assert query.get("cnteduNo") == ["7001"]
            return _Response(url, _official_empty_detail_shell())
        page = int(query.get("pageIndex", ["1"])[0])
        rows = _CURRENT + _EXPIRED if page == 1 else ""
        return _Response(url, _list_html(page=page, rows=rows))

    rows, _, meta = goesan.collect_goesan_education(
        _target(),
        today="2026-07-22",
        max_pages=4,
        detail_limit=1,
        session_factory=_Session,
        fetcher=empty_detail_fetcher,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"].endswith(":7001")
    assert row["raw_url"] == goesan._detail_url("7001")
    assert row["raw_fields"]["identity_binding"] == "stable_canonical_list_link"
    assert row["raw_fields"]["detail_verified"] is False
    assert row["raw_fields"]["detail_unavailable_official_shell"] is True
    assert row["reservation_available"] is False
    assert row["application_url"] == ""
    assert meta["detail_unavailable_official_shell_count"] == 1
    assert meta["identity_binding_complete"] is True
    assert meta["snapshot_complete"] is True


def test_arbitrary_empty_or_notice_detail_still_fails_closed() -> None:
    def notice_fetcher(_session, url: str, _timeout: int):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == goesan.GOESAN_DETAIL_PATH:
            return _Response(
                url,
                "<html><head><title>괴산군청</title></head>"
                "<body><div class='board_notice'>점검 공지</div></body></html>",
            )
        page = int(query.get("pageIndex", ["1"])[0])
        rows = _CURRENT + _EXPIRED if page == 1 else ""
        return _Response(url, _list_html(page=page, rows=rows))

    rows, _, meta = goesan.collect_goesan_education(
        _target(),
        today="2026-07-22",
        max_pages=4,
        detail_limit=1,
        session_factory=_Session,
        fetcher=notice_fetcher,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail identity drift" in meta["configured_collection_error"]


def test_fail_closed_when_list_request_cap_cannot_cover_proof_requests() -> None:
    rows, _, meta = goesan.collect_goesan_education(
        _target(),
        today="2026-07-22",
        max_pages=3,
        detail_limit=1,
        session_factory=_Session,
        fetcher=_fetcher,
    )

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["pagination_complete"] is False
    assert "max_pages 3 below required 4" in meta["configured_collection_error"]


def test_page_contract_rejects_nonempty_post_last_sentinel() -> None:
    html = _list_html(page=2, rows=_CURRENT, total=1, last=1)
    soup = BeautifulSoup(html, "html.parser")

    with pytest.raises(goesan.GoesanContractError, match="non-empty sentinel"):
        goesan._parse_page(soup, 2)


def test_snapshot_fails_closed_when_first_page_changes_on_recheck() -> None:
    lock = Lock()
    page_one_calls = 0

    def drift_fetcher(_session, url: str, _timeout: int):
        nonlocal page_one_calls
        parsed = urlparse(url)
        page = int(parse_qs(parsed.query).get("pageIndex", ["1"])[0])
        if page == 1:
            with lock:
                page_one_calls += 1
                initial = page_one_calls == 1
            rows = _CURRENT + _EXPIRED
            if not initial:
                rows = rows.replace("괴산 미래교육", "변경된 괴산 미래교육")
        else:
            rows = ""
        return _Response(url, _list_html(page=page, rows=rows))

    rows, _, meta = goesan.collect_goesan_education(
        _target(),
        today="2026-07-22",
        max_pages=4,
        detail_limit=1,
        session_factory=_Session,
        fetcher=drift_fetcher,
    )

    assert rows == []
    assert meta["pagination_complete"] is False
    assert "recheck failed" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("MOONCEN_LIVE_CRAWL") != "1",
    reason="opt-in live crawl",
)
def test_live_goesan_snapshot() -> None:
    rows, parser, meta = goesan.collect_goesan_education(
        _target(),
        timeout=40,
        max_pages=40,
        detail_limit=200,
        max_workers=2,
    )

    assert parser == goesan.GOESAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_rows"] == meta["source_total"]
    assert meta["list_requests"] == meta["data_pages"] + 3
    assert len(rows) == meta["returned_count"]
