from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_nonsan as nonsan


class _Response:
    def __init__(self, url: str, html: str):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = 200
        self.headers: dict[str, str] = {}


class _Session:
    def close(self) -> None:
        return None


def _card(
    identity: int,
    title: str,
    period: str,
    *,
    source_status: str = "접수마감",
) -> str:
    return f"""
    <div class="list ready">
      <div class="state_btn"><p class="type close"><b>{source_status}</b><span class="typeC">자체접수</span></p></div>
      <div class="item"><div class="style"><div class="item_content">
        <div class="tit"><span class="type3 cate">문화·예술교육</span>
          <a href="./view.do?eduNo={identity}&amp;organ=41">{title}</a>
        </div>
        <ul class="info">
          <li class="addr"><b>교육기관</b>논산시평생학습관 온담</li>
          <li><b>교육장소</b>강의실 1</li>
          <li><b>접수기간</b>2026-07-01 09:00 ~ 2026-07-31 23:55</li>
          <li><b>교육기간</b>{period}</li>
          <li><b>교육시간</b>수, 10:00 ~ 11:50</li>
          <li><b>신청/정원</b>3명 / 12명 (신청완료 3명)</li>
        </ul>
      </div></div></div>
    </div>
    """


def _page(page: int) -> str:
    if page == 1:
        rows = _card(11, "논산 미래교육", "2026-08-01 ~ 2026-08-31", source_status="접수중")
        rows += _card(10, "[폐강] 논산 취소교육", "2026-08-01 ~ 2026-08-31")
        rows += _card(9, "[test] 논산 시험교육", "2026-08-01 ~ 2026-08-31")
        rows += "".join(
            _card(number, f"지난 논산교육 {number}", "2025-01-01 ~ 2025-01-31")
            for number in range(8, 1, -1)
        )
    elif page == 2:
        rows = _card(1, "지난 논산교육 1", "2025-01-01 ~ 2025-01-31")
    else:
        rows = ""
    return f"""
    <html><body><section id="contents">
      <div class="total_chk">총 <b>11</b>개의 등록된 강좌가 있습니다.</div>
      <div class="courses_wrap">{rows}</div>
    </section></body></html>
    """


def _detail(identity: str) -> str:
    assert identity == "11"
    return f"""
    <html><body><section id="contents">
      <div class="state_btn"><p><b>접수중</b><span class="typeC">자체접수</span></p></div>
      <table class="tbl_basic">
        <tr><th>강좌명</th><td>논산 미래교육</td><th>교육기간</th><td>2026-08-01 ~ 2026-08-31</td></tr>
        <tr><th>교육시간</th><td>수, 10:00 ~ 11:50</td><th>접수기간</th><td>2026-07-01 ~ 2026-07-31</td></tr>
        <tr><th>교육장소</th><td>강의실 1</td><th>정원</th><td>12명</td></tr>
        <tr><th>교육대상</th><td>논산시민</td><th>수강료</th><td>무료</td></tr>
        <tr><th>강사명</th><td>홍길동</td><th>문의전화</th><td>041-000-0000</td></tr>
        <tr><th>교육기관</th><td>논산시평생학습관 온담</td></tr>
        <tr><th>교육내용</th><td>test@example.com 010-1234-5678</td></tr>
      </table>
      <a href="/prog/educate_reserve/kor/sub01_01_01_01/allList.do?pageIndex=1&amp;eduNo={identity}">신청자확인</a>
      <a href="/prog/educate_reserve/kor/sub01_01_01_01/write.do?eduNo={identity}">수강신청</a>
    </section></body></html>
    """


def _target() -> dict[str, str]:
    return {"provider": nonsan.NONSAN_PROVIDER, "url": nonsan.NONSAN_CANONICAL_URL}


def _fetcher(_session, url: str, _timeout: int):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if parsed.path == nonsan.NONSAN_LIST_PATH:
        return _Response(url, _page(int((query.get("pageIndex") or ["1"])[0])))
    return _Response(url, _detail(query["eduNo"][0]))


def test_exact_canonical_target_only() -> None:
    assert nonsan.is_nonsan_education_target(_target())
    assert not nonsan.is_nonsan_education_target({**_target(), "provider": "OTHER"})
    assert not nonsan.is_nonsan_education_target(
        {**_target(), "url": nonsan.NONSAN_CANONICAL_URL + "&pageIndex=1"}
    )
    assert not nonsan.is_nonsan_education_target(
        {**_target(), "url": nonsan.NONSAN_CANONICAL_URL + "#courses"}
    )


def test_complete_snapshot_excludes_cancelled_test_and_applicant_list() -> None:
    rows, parser, meta = nonsan.collect_nonsan_education(
        _target(),
        today="2026-07-22",
        max_pages=5,
        detail_limit=1,
        session_factory=_Session,
        fetcher=_fetcher,
    )

    assert parser == nonsan.NONSAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["list_requests"] == 5
    assert meta["declared_total"] == meta["source_rows"] == 11
    assert meta["data_pages"] == 2
    assert meta["empty_sentinel_page"] == 3
    assert meta["boundary_rechecks"] == 2
    assert meta["current_source_count"] == 3
    assert meta["current_education_count"] == 1
    assert meta["excluded_cancelled_count"] == 1
    assert meta["excluded_test_record_count"] == 1
    assert meta["expired_count"] == 8
    assert meta["period_anomaly_count"] == 0
    assert meta["detail_pages"] == meta["returned_count"] == 1
    assert meta["status_counts"] == {"OPEN": 1}
    assert meta["branch_counts"] == {"논산시평생학습관 온담": 1}
    assert meta["application_control_count"] == 1
    assert meta["applicant_check_controls_excluded"] == 1

    row = rows[0]
    assert row["title"] == "논산 미래교육"
    assert row["branch"] == "논산시평생학습관 온담"
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["application_type"] == "ONLINE_RESERVATION"
    assert row["application_url"].endswith("write.do?eduNo=11")
    assert row["raw_fields"]["applicant_check_control_excluded"] is True
    assert "allList.do" not in repr(row)
    assert "홍길동" not in repr(row)
    assert "010-" not in repr(row)
    assert "test@example.com" not in repr(row)


def test_page_cap_is_fail_closed() -> None:
    rows, _, meta = nonsan.collect_nonsan_education(
        _target(),
        today="2026-07-22",
        max_pages=4,
        session_factory=_Session,
        fetcher=_fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "max_pages 4 below required 5" in meta["configured_collection_error"]


def test_only_exact_audited_reversed_period_is_quarantinable() -> None:
    start, end = nonsan._dates("2026-05-07 ~ 2025-07-23", "1730", "education period")
    assert end < start
    with pytest.raises(nonsan.NonsanContractError, match="reversed"):
        nonsan._dates("2026-05-07 ~ 2025-07-23", "9999", "education period")


def test_application_identity_drift_is_fail_closed() -> None:
    broken = _detail("11").replace("write.do?eduNo=11", "write.do?eduNo=12")

    def fetcher(_session, url: str, _timeout: int):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == nonsan.NONSAN_LIST_PATH:
            return _Response(url, _page(int((query.get("pageIndex") or ["1"])[0])))
        return _Response(url, broken)

    rows, _, meta = nonsan.collect_nonsan_education(
        _target(),
        today="2026-07-22",
        max_pages=5,
        detail_limit=1,
        session_factory=_Session,
        fetcher=fetcher,
    )
    assert rows == []
    assert "application-control identity drift" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_CRAWLER_TESTS") != "1",
    reason="set RUN_LIVE_CRAWLER_TESTS=1 for official live validation",
)
def test_live_nonsan_ondam_snapshot() -> None:
    rows, _, meta = nonsan.collect_nonsan_education(
        _target(), today="2026-07-22", timeout=40, max_pages=100, detail_limit=100
    )
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] >= 66
    assert meta["snapshot_complete"] is True
    assert meta["returned_count"] == len(rows)
