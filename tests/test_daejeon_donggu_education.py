from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_daejeon_donggu as donggu


@dataclass
class DongguTarget:
    provider: str = donggu.DAEJEON_DONGGU_PROVIDER
    url: str = donggu.DAEJEON_DONGGU_CANONICAL_URL
    branch: str = donggu.DAEJEON_DONGGU_NAME


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _list_row(record: dict[str, Any]) -> str:
    return f"""
      <tr>
        <td class="text_center"><span class="edu_state">{record['list_status']}</span></td>
        <td><a href="#n" onclick="fn_goView('{record['id']}')">
          <strong class="edu_tit clearfix">{record['title']}</strong>
          <span class="clearfix">{record['venue']}</span></a></td>
        <td class="text_center">{record['branch']}</td>
        <td class="text_center"><span>모집대상 :</span> {record['target']}</td>
        <td>
          <span class="clearfix edu_time_sp"><em class="edu_time_tit">신청기간</em>
            <span>{record['apply_start_short']} ~ {record['apply_end_short']}</span></span>
          <span class="clearfix edu_time_sp"><em class="edu_time_tit">교육기간</em>
            <span>{record['start_short']} ~ {record['end_short']}</span></span>
        </td>
        <td class="text_center">모집 {record['capacity']}명 /<br> 대기 {record['wait']}명</td>
        <td class="text_center"><span>수강료</span> 무료</td>
        <td><a class="btn type2" href="#n" onclick="fn_goView('{record['id']}')">수강신청</a></td>
      </tr>
    """


def _list_page(
    records: list[dict[str, Any]],
    *,
    total: int,
    current_page: int,
    sentinel: bool = False,
) -> str:
    last = max(1, (total + donggu.DAEJEON_DONGGU_PAGE_SIZE - 1) // donggu.DAEJEON_DONGGU_PAGE_SIZE)
    body = (
        '<tr><td colspan="7">검색 결과가 존재하지 않습니니다.</td></tr>'
        if sentinel
        else "".join(_list_row(record) for record in records)
    )
    return f"""
      <html><head><title>수강신청 - 대전동구평생학습</title></head><body>
        <form id="searchForm" name="searchForm">
          <input name="key" value="733"><input name="page" value="{current_page}">
          <input name="schType" value="lctr"><input name="schType" value="dept">
        </form>
        <p class="count">총 <em>{total}</em> 개 강좌 ({current_page} / {last} 페이지)</p>
        <table class="edu_list_table"><thead><tr>
          <th>상태</th><th>교육명/장소</th><th>교육장소</th><th>대상</th>
          <th>신청/교육기간</th><th>정원</th><th>수강료</th>
        </tr></thead><tbody>{body}</tbody></table>
      </body></html>
    """


def _detail(
    record: dict[str, Any],
    *,
    control: bool | None = None,
    title: str | None = None,
    phone_and_instructor: bool = True,
) -> str:
    if control is None:
        control = record["detail_status"] == "접수중"
    controls = (
        '<a href="#n" onclick="fn_login_move()"><button class="btn type1">강의신청</button></a>'
        if control
        else ""
    )
    pii = (
        "<tr><th>문의전화</th><td>042-259-7016</td></tr>"
        "<tr><th>강사</th><td>홍길동</td></tr>"
        "<tr><th>강의내용</th><td>문의 test@example.com</td></tr>"
        if phone_and_instructor
        else ""
    )
    return f"""
      <html><head><title>수강신청 - 대전동구평생학습</title></head><body>
        <div class="sub_offline_view">
          <p class="edu_wh">{record['branch']}</p>
          <div class="offline_edu_title"><h2>{title or record['title']}</h2></div>
          <table><tbody>
            <tr><th>강의분류</th><td>문화/예술</td></tr>
            <tr><th>교육대상</th><td>{record['target']}</td></tr>
            <tr><th>신청기간</th><td><span class="edu_state">{record['detail_status']}</span>
              {record['apply_start']} ~ {record['apply_end']}</td></tr>
            <tr><th>교육기간</th><td>{record['start']} ~ {record['end']}</td></tr>
            <tr><th>교육시간</th><td>매주 목요일 16:00~18:00</td></tr>
            <tr><th>접수인원</th><td><span>선착순</span> [ {record['current']} / {record['capacity']}명 ]</td></tr>
            <tr><th>교육장소</th><td>{record['venue']}</td></tr>
            <tr><th>교육장소주소</th><td>[34674]대전 동구 중앙로 1</td></tr>
            <tr><th>재료비</th><td>10,000</td></tr>
            <tr><th>재료비설명</th><td>현장 납부</td></tr>
            {pii}
          </tbody></table>{controls}<button onclick="fn_list()">강의목록</button>
        </div>
        <form id="returnForm"><input name="key" value="733">
          <input name="page" value="1"><input name="schId" value="{record['id']}"></form>
      </body></html>
    """


def _records() -> list[dict[str, Any]]:
    return [
        {
            "id": "9001",
            "title": "공개 환경교육",
            "branch": "동구평생학습관",
            "venue": "동구청 제1강의실",
            "target": "성인",
            "list_status": "모집중",
            "detail_status": "접수중",
            "apply_start_short": "99.07.01",
            "apply_end_short": "99.07.31",
            "start_short": "99.08.01",
            "end_short": "99.08.31",
            "apply_start": "2099-07-01",
            "apply_end": "2099-07-31",
            "start": "2099-08-01",
            "end": "2099-08-31",
            "current": 3,
            "capacity": 20,
            "wait": 5,
        },
        {
            "id": "9000",
            "title": "예정 영어교실",
            "branch": "무지개도서관",
            "venue": "무지개도서관 강좌실",
            "target": "초등학생",
            "list_status": "모집전",
            "detail_status": "접수예정",
            "apply_start_short": "99.08.01",
            "apply_end_short": "99.08.20",
            "start_short": "99.09.01",
            "end_short": "99.12.20",
            "apply_start": "2099-08-01",
            "apply_end": "2099-08-20",
            "start": "2099-09-01",
            "end": "2099-12-20",
            "current": 0,
            "capacity": 15,
            "wait": 10,
        },
    ]


def _fixture(
    records: list[dict[str, Any]],
    *,
    detail_overrides: dict[str, str] | None = None,
    bad_sentinel: bool = False,
) -> tuple[Any, Counter[str], list[FakeSession]]:
    total = len(records)
    pages = {
        donggu.daejeon_donggu_list_url(1): _list_page(
            records, total=total, current_page=1
        ),
        donggu.daejeon_donggu_list_url(2): _list_page(
            records[:1] if bad_sentinel else [],
            total=total,
            current_page=2,
            sentinel=not bad_sentinel,
        ),
    }
    for record in records:
        pages[donggu.daejeon_donggu_detail_url(record["id"], 1)] = _detail(record)
    pages.update(detail_overrides or {})
    counts: Counter[str] = Counter()
    lock = Lock()

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        with lock:
            counts[url] += 1
        if url not in pages:
            raise AssertionError(f"unexpected URL {url}")
        return pages[url]

    sessions: list[FakeSession] = []

    def factory() -> FakeSession:
        current = FakeSession()
        sessions.append(current)
        return current

    return (fetch, factory), counts, sessions


def test_exact_owner_urls_aliases_and_identity_helpers() -> None:
    target = DongguTarget()
    assert donggu.is_daejeon_donggu_education_target(target)
    assert not donggu.is_daejeon_donggu_education_target(
        {"provider": target.provider, "url": target.url + "&page=1"}
    )
    assert not donggu.is_daejeon_donggu_education_target(
        {"provider": target.provider, "url": "https://www.donggu.go.kr.evil.test/lll/www/selectUserEduList.do?key=733"}
    )
    assert not donggu.is_daejeon_donggu_education_target(
        {"provider": "OTHER", "url": target.url}
    )
    assert donggu.DAEJEON_DONGGU_CODE == "3011000000"
    assert {item["ownership"] for item in donggu.DAEJEON_DONGGU_NON_EXECUTING_ALIASES} == {
        "navigation_shell",
        "pagination_fragment",
        "single_detail",
    }
    assert parse_qs(urlparse(donggu.daejeon_donggu_list_url(14)).query)["page"] == ["14"]
    detail_query = parse_qs(urlparse(donggu.daejeon_donggu_detail_url("1924", 3)).query)
    assert detail_query == {"key": ["733"], "page": ["3"], "schId": ["1924"]}
    with pytest.raises(ValueError):
        donggu.daejeon_donggu_detail_url("1924x")


def test_complete_snapshot_reconciles_sentinel_details_controls_and_pii() -> None:
    records = _records()
    (fetch, factory), counts, sessions = _fixture(records)
    rows, parser, meta = donggu.collect_daejeon_donggu_education(
        DongguTarget(),
        fetcher=fetch,
        session_factory=factory,
        dedupe_rows=lambda values: values,
        today="2099-07-21",
        max_pages=10,
        detail_limit=10,
        max_workers=2,
    )

    assert parser == donggu.DAEJEON_DONGGU_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_total"] == meta["source_rows"] == 2
    assert meta["list_requests"] == 3
    assert meta["sentinel_pages"] == meta["stable_rechecks"] == 1
    assert meta["current_count"] == meta["detail_pages"] == len(rows) == 2
    assert meta["application_control_count"] == 1
    assert meta["detail_errors"] == 0
    assert counts[donggu.daejeon_donggu_list_url(1)] == 2
    assert all(session.closed for session in sessions)

    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    assert by_id["9001"]["status"] == "OPEN"
    assert by_id["9001"]["application_url"] == by_id["9001"]["raw_url"]
    assert by_id["9001"]["reservation_available"] is True
    assert by_id["9000"]["status"] == "SCHEDULED"
    assert by_id["9000"]["application_url"] == ""
    assert by_id["9000"]["reservation_available"] is False
    assert all(
        set(row["raw_fields"]) <= donggu.DAEJEON_DONGGU_RAW_FIELD_ALLOWLIST
        for row in rows
    )
    assert "042-" not in repr(rows) and "test@example.com" not in repr(rows)
    assert all("instructor" not in row and "description" not in row for row in rows)


def test_invalid_first_detail_is_retried_then_committed_atomically() -> None:
    record = _records()[0]
    good = _detail(record)
    detail_url = donggu.daejeon_donggu_detail_url(record["id"], 1)
    (base_fetch, factory), counts, sessions = _fixture([record])
    lock = Lock()

    def fetch(current_session: Any, url: str, timeout: int) -> str:
        with lock:
            count = counts[url]
        if url == detail_url and count == 0:
            with lock:
                counts[url] += 1
            return "<html><body>temporary incomplete detail</body></html>"
        return base_fetch(current_session, url, timeout)

    rows, _parser, meta = donggu.collect_daejeon_donggu_education(
        DongguTarget(),
        fetcher=fetch,
        session_factory=factory,
        today="2099-07-21",
        max_pages=10,
        detail_limit=10,
    )
    assert good  # documents the expected second fixture response
    assert meta["snapshot_complete"] is True
    assert meta["detail_attempts"] == 1
    assert meta["detail_retry_pages"] == 1
    assert meta["detail_errors"] == 0
    assert len(rows) == 1 and rows[0]["raw_fields"]["detail_verified"] is True
    assert all(session.closed for session in sessions)


@pytest.mark.parametrize(
    "mutation",
    ["wrong_title", "missing_open_control", "control_on_scheduled"],
)
def test_detail_identity_status_and_application_contracts_fail_closed(
    mutation: str,
) -> None:
    records = _records()
    record = records[0] if mutation != "control_on_scheduled" else records[1]
    if mutation == "wrong_title":
        bad = _detail(record, title="다른 강좌")
    elif mutation == "missing_open_control":
        bad = _detail(record, control=False)
    else:
        bad = _detail(record, control=True)
    overrides = {donggu.daejeon_donggu_detail_url(record["id"], 1): bad}
    (fetch, factory), _counts, _sessions = _fixture(
        records, detail_overrides=overrides
    )
    rows, _parser, meta = donggu.collect_daejeon_donggu_education(
        DongguTarget(),
        fetcher=fetch,
        session_factory=factory,
        today="2099-07-21",
        max_pages=10,
        detail_limit=10,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_errors"] == 1
    assert meta["configured_collection_error"]


def test_nonempty_sentinel_and_resource_caps_fail_before_save() -> None:
    records = _records()
    (fetch, factory), _counts, _sessions = _fixture(records, bad_sentinel=True)
    rows, _parser, meta = donggu.collect_daejeon_donggu_education(
        DongguTarget(), fetcher=fetch, session_factory=factory, today="2099-07-21"
    )
    assert rows == [] and meta["snapshot_complete"] is False
    assert "sentinel" in meta["configured_collection_error"]

    (fetch, factory), counts, _sessions = _fixture(records)
    rows, _parser, meta = donggu.collect_daejeon_donggu_education(
        DongguTarget(),
        fetcher=fetch,
        session_factory=factory,
        today="2099-07-21",
        detail_limit=1,
    )
    assert rows == [] and meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert all("selectUserEduView" not in url for url in counts)


def test_missing_managed_session_and_wrong_target_do_not_fetch() -> None:
    rows, _parser, meta = donggu.collect_daejeon_donggu_education(DongguTarget())
    assert rows == [] and "session_factory injection" in meta["configured_collection_error"]

    called = False

    def fetch(_session: Any, _url: str, _timeout: int) -> str:
        nonlocal called
        called = True
        raise AssertionError

    rows, _parser, meta = donggu.collect_daejeon_donggu_education(
        {"provider": "WRONG", "url": donggu.DAEJEON_DONGGU_CANONICAL_URL},
        fetcher=fetch,
    )
    assert rows == [] and called is False
    assert "canonical Daejeon Dong-gu" in meta["configured_collection_error"]
