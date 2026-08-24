from __future__ import annotations

from datetime import date
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_boeun as boeun


class _Response:
    def __init__(self, url: str, html: str):
        self.url = url
        self.content = html.encode("utf-8")

    def raise_for_status(self) -> None:
        return None


class _Session:
    def close(self) -> None:
        return None


def _table(
    caption: str,
    headers: tuple[str, ...],
    rows: str,
    empty_text: str,
) -> str:
    body = rows or (
        f'<tr><td colspan="{len(headers)}">{empty_text}</td></tr>'
    )
    headings = "".join(f'<th scope="col">{value}</th>' for value in headers)
    return f"""
    <table class="table responsive">
      <caption>{caption}</caption>
      <thead><tr>{headings}</tr></thead>
      <tbody>{body}</tbody>
    </table>
    """


def _open_row() -> str:
    return """
    <tr>
      <td><a href="./selectLftmLrnView.do?key=1349&amp;lftmLrnNo=7001">
        보은 미래교육
      </a></td>
      <td>2026-08-01 ~ 2026-08-31</td>
      <td>화 10:00~12:00</td>
      <td>2026-07-01 ~ 2026-07-31</td>
      <td>3 / 20</td>
      <td><span>접수중</span>
        <a href="./insertLftmLrnReqstView.do?key=1349&amp;lftmLrnNo=7001">
          신청하기
        </a>
      </td>
    </tr>
    """


def _progress_rows() -> str:
    return """
    <tr>
      <td><a href="./selectLftmLrnView.do?key=1349&amp;lftmLrnNo=7001">
        보은 미래교육
      </a></td>
      <td>2026-08-01 ~ 2026-08-31</td>
      <td>화 10:00~12:00</td>
    </tr>
    <tr>
      <td><a href="./selectLftmLrnView.do?key=1349&amp;lftmLrnNo=7002">
        보은 생활교육
      </a></td>
      <td>2026-07-20 ~ 2026-08-30</td>
      <td>수 14:00~16:00</td>
    </tr>
    """


def _catalogue_html(*, populated: bool = True) -> str:
    open_rows = _open_row() if populated else ""
    progress_rows = _progress_rows() if populated else ""
    return "<html><body>" + _table(
        "접수중인 교육과정 - 교육과정 정보 제공",
        boeun._OPEN_HEADERS,
        open_rows,
        boeun._EMPTY_TEXT["open"],
    ) + _table(
        "진행중인 교육과정 - 교육과정 정보 제공",
        boeun._PROGRESS_HEADERS,
        progress_rows,
        boeun._EMPTY_TEXT["progress"],
    ) + "</body></html>"


def _highlight(
    identity: str,
    title: str,
    period: str,
    *,
    flag: str,
) -> str:
    return f"""
    <div class="program_slide_item">
      <span class="flag">{flag}</span>
      <a class="program_anchor"
         href="./selectLftmLrnView.do?key=1349&amp;lftmLrnNo={identity}">
        <div class="town_title">{title}</div>
        <ul class="bu dl">
          <li><span class="title">교육기간</span><span class="text">{period}</span></li>
          <li><span class="title">교육비</span><span class="text">무료</span></li>
          <li><span class="title">장소</span><span class="text">생활문화센터</span></li>
        </ul>
      </a>
    </div>
    """


def _homepage_html(*, populated: bool = True) -> str:
    expired = _highlight(
        "6000",
        "지난 보은교육",
        "2025-01-01 ~ 2025-02-01",
        flag="마감",
    )
    current = ""
    if populated:
        current = _highlight(
            "7001",
            "보은 미래교육",
            "2026-08-01 ~ 2026-08-31",
            flag="접수중",
        ) + _highlight(
            "7002",
            "보은 생활교육",
            "2026-07-20 ~ 2026-08-30",
            flag="진행중",
        )
    return f"""
    <html><body><div class="program_wrap"><div class="program_list">
      {expired}{current}
    </div></div></body></html>
    """


def _detail_html(identity: str) -> str:
    if identity == "7001":
        title = "보은 미래교육"
        method = "온라인"
        capacity = "20명"
        venue = "생활문화센터 (043-540-0000)"
        apply_period = "2026-07-01 ~ 2026-07-31"
        education_period = "2026-08-01 ~ 2026-08-31"
        time = "10:00~12:00"
        weekday = "화"
        control = """
        <a href="./insertLftmLrnReqstView.do?key=1349&amp;lftmLrnNo=7001">
          신청하기
        </a>
        """
    else:
        title = "보은 생활교육"
        method = "오프라인"
        capacity = "15"
        venue = "여성회관"
        apply_period = "2026-07-01 ~ 2026-07-10"
        education_period = "2026-07-20 ~ 2026-08-30"
        time = "14:00~16:00"
        weekday = "수"
        control = ""
    return f"""
    <html><body><div class="cts99_wrap">
      <table class="table type2"><tbody>
        <tr><th>이미지</th><td><img src="/private-image.png"></td></tr>
        <tr><th>교육명</th><td>{title}</td></tr>
        <tr><th>강사명</th><td>홍길동 010-1234-5678</td></tr>
        <tr><th>교육과정</th><td>상세 과정 test@example.com</td></tr>
        <tr><th>접수방법</th><td>{method}</td></tr>
        <tr><th>정원</th><td>{capacity}</td></tr>
        <tr><th>교육비</th><td>무료</td></tr>
        <tr><th>장소</th><td>{venue}</td></tr>
        <tr><th>접수 시작/종료 날짜</th><td>{apply_period}</td></tr>
        <tr><th>교육 시작/종료 날짜</th><td>{education_period}</td></tr>
        <tr><th>교육 시간</th><td>{time}</td></tr>
        <tr><th>요일</th><td>{weekday}</td></tr>
        <tr><th>상세내용</th><td>연락처 010-9876-5432</td></tr>
      </tbody></table>
      {control}
    </div></body></html>
    """


def _target() -> dict[str, str]:
    return {
        "provider": boeun.BOEUN_PROVIDER,
        "url": boeun.BOEUN_CANONICAL_URL,
    }


def _fetcher(_session, url: str, _timeout: int):
    parsed = urlparse(url)
    if parsed.path == boeun.BOEUN_LIST_PATH:
        html = _catalogue_html()
    elif parsed.path == boeun.BOEUN_HOME_PATH:
        html = _homepage_html()
    else:
        identity = parse_qs(parsed.query)["lftmLrnNo"][0]
        html = _detail_html(identity)
    return _Response(url, html)


def test_exact_no_www_canonical_target_only() -> None:
    assert boeun.is_boeun_education_target(_target())
    assert not boeun.is_boeun_education_target(
        {**_target(), "url": boeun.BOEUN_CANONICAL_URL.replace("boeun.go.kr", "www.boeun.go.kr")}
    )
    assert not boeun.is_boeun_education_target(
        {**_target(), "url": boeun.BOEUN_CANONICAL_URL + "&ignored="}
    )
    assert not boeun.is_boeun_education_target(
        {**_target(), "url": boeun.BOEUN_IEUM_NOTICE_URL}
    )


def test_complete_two_table_snapshot_details_branches_and_pii() -> None:
    rows, parser, meta = boeun.collect_boeun_education(
        _target(),
        today="2026-07-22",
        max_pages=3,
        detail_limit=2,
        session_factory=_Session,
        fetcher=_fetcher,
    )

    assert parser == boeun.BOEUN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["source_requests"] == 3
    assert meta["authoritative_requests"] == 2
    assert meta["boundary_rechecks"] == 1
    assert meta["authoritative_table_count"] == 2
    assert meta["stable_snapshot_recheck"] is True
    assert meta["scope_counts"] == {"open": 1, "progress": 2}
    assert meta["raw_scope_rows"] == 3
    assert meta["scope_duplicate_count"] == 1
    assert meta["source_rows"] == meta["current_source_count"] == 2
    assert meta["homepage_highlight_count"] == 3
    assert meta["homepage_current_count"] == 2
    assert meta["homepage_expired_count"] == 1
    assert meta["detail_pages"] == meta["returned_count"] == 2
    assert meta["status_counts"] == {"OPEN": 1, "CLOSED": 1}
    assert "image_attachments" in meta["excluded_notice_reason"]

    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    opened = by_id["7001"]
    assert opened["status"] == "OPEN"
    assert opened["reservation_available"] is True
    assert opened["application_type"] == "ONLINE_RESERVATION"
    assert "lftmLrnNo=7001" in opened["application_url"]
    assert opened["branch"] == "보은군 생활문화센터"
    assert opened["capacity_current"] == 3
    assert opened["capacity_total"] == 20
    assert opened["raw_fields"]["source_scopes"] == ["open", "progress"]
    assert opened["raw_fields"]["application_control_verified"] is True
    assert "010-" not in repr(rows)
    assert "test@example.com" not in repr(rows)
    assert "홍길동" not in repr(rows)

    closed = by_id["7002"]
    assert closed["status"] == "CLOSED"
    assert closed["reservation_available"] is False
    assert closed["application_type"] == "INFO_ONLY"
    assert closed["branch"] == "보은군 여성회관"


def test_structural_empty_sentinels_are_a_complete_zero_snapshot() -> None:
    def empty_fetcher(_session, url: str, _timeout: int):
        path = urlparse(url).path
        html = _homepage_html(populated=False) if path == boeun.BOEUN_HOME_PATH else _catalogue_html(populated=False)
        return _Response(url, html)

    rows, _, meta = boeun.collect_boeun_education(
        _target(),
        today="2026-07-22",
        max_pages=3,
        detail_limit=0,
        session_factory=_Session,
        fetcher=empty_fetcher,
    )

    assert rows == []
    assert meta["configured_collection_error"] == ""
    assert meta["scope_counts"] == {"open": 0, "progress": 0}
    assert meta["structural_empty_scopes"] == ["open", "progress"]
    assert meta["homepage_highlight_count"] == 1
    assert meta["homepage_current_count"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True


def test_source_request_cap_fails_before_partial_collection() -> None:
    rows, _, meta = boeun.collect_boeun_education(
        _target(),
        today="2026-07-22",
        max_pages=2,
        detail_limit=2,
        session_factory=_Session,
        fetcher=_fetcher,
    )

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["source_requests"] == 0
    assert "max_pages 2 below required 3" in meta["configured_collection_error"]


def test_malformed_empty_sentinel_fails_closed() -> None:
    def malformed_fetcher(_session, url: str, _timeout: int):
        path = urlparse(url).path
        if path == boeun.BOEUN_HOME_PATH:
            html = _homepage_html(populated=False)
        else:
            html = _catalogue_html(populated=False).replace(
                'colspan="6"',
                'colspan="5"',
                1,
            )
        return _Response(url, html)

    rows, _, meta = boeun.collect_boeun_education(
        _target(),
        today="2026-07-22",
        max_pages=3,
        detail_limit=0,
        session_factory=_Session,
        fetcher=malformed_fetcher,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "row shape changed" in meta["configured_collection_error"]


def test_authoritative_recheck_drift_fails_closed() -> None:
    list_requests = 0

    def drift_fetcher(_session, url: str, _timeout: int):
        nonlocal list_requests
        path = urlparse(url).path
        if path == boeun.BOEUN_LIST_PATH:
            list_requests += 1
            html = _catalogue_html()
            if list_requests == 2:
                html = html.replace("보은 미래교육", "변경된 보은 미래교육")
        elif path == boeun.BOEUN_HOME_PATH:
            html = _homepage_html()
        else:
            raise AssertionError("detail must not be fetched after source drift")
        return _Response(url, html)

    rows, _, meta = boeun.collect_boeun_education(
        _target(),
        today="2026-07-22",
        max_pages=3,
        detail_limit=2,
        session_factory=_Session,
        fetcher=drift_fetcher,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "stability recheck failed" in meta["configured_collection_error"]


def test_open_online_detail_rejects_wrong_application_identity() -> None:
    def wrong_control_fetcher(_session, url: str, _timeout: int):
        parsed = urlparse(url)
        if parsed.path == boeun.BOEUN_LIST_PATH:
            html = _catalogue_html()
        elif parsed.path == boeun.BOEUN_HOME_PATH:
            html = _homepage_html()
        else:
            identity = parse_qs(parsed.query)["lftmLrnNo"][0]
            html = _detail_html(identity)
            if identity == "7001":
                html = html.replace("lftmLrnNo=7001", "lftmLrnNo=9999")
        return _Response(url, html)

    rows, _, meta = boeun.collect_boeun_education(
        _target(),
        today="2026-07-22",
        max_pages=3,
        detail_limit=2,
        session_factory=_Session,
        fetcher=wrong_control_fetcher,
    )

    assert rows == []
    assert "application control is not identity-bound" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("MOONCEN_LIVE_CRAWL") != "1",
    reason="opt-in live crawl",
)
def test_live_boeun_snapshot_and_historical_detail_contract() -> None:
    rows, parser, meta = boeun.collect_boeun_education(
        _target(),
        timeout=40,
        max_pages=20,
        detail_limit=200,
        max_workers=2,
    )

    assert parser == boeun.BOEUN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_requests"] == 3
    assert meta["scope_counts"].keys() == {"open", "progress"}
    assert meta["homepage_reconciliation_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["source_rows"] == meta["source_total"]
    assert len(rows) == meta["returned_count"]

    soup = boeun._soup(
        boeun._detail_url("41"),
        40,
        boeun._session,
        boeun._request,
    )
    _root, fields = boeun._detail_fields(soup, "41")
    listed = {
        "identity": "41",
        "title": fields["교육명"],
        "start": date(2025, 11, 10),
        "end": date(2025, 11, 24),
        "schedule": "09:00~09:00",
        "source_scopes": ["progress"],
        "source_status": "",
        "source_apply_period": "",
        "apply_start": None,
        "apply_end": None,
        "capacity_current": None,
        "capacity_total": None,
        "list_control": "",
    }
    historical = boeun._detail(listed, soup, date(2025, 11, 24))
    assert historical["provider_course_id"].endswith(":41")
    assert historical["status"] == "CLOSED"
    assert historical["branch"] == "보은군 생활문화센터"
    assert historical["capacity_total"] == 15
    assert boeun._privacy(historical) == []
