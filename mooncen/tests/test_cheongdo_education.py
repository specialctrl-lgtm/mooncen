from __future__ import annotations

from collections import Counter
import hashlib
import html
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_cheongdo as cheongdo


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FixtureGetter:
    def __init__(
        self,
        ledger: cheongdo.CheongdoLedger,
        pages: Mapping[int, str | list[str]],
        details: Mapping[str, str | list[str]],
    ) -> None:
        self.ledger = ledger
        self.pages = dict(pages)
        self.details = dict(details)
        self.offsets: Counter[tuple[str, int | str]] = Counter()
        self.calls: list[str] = []

    @staticmethod
    def _value(value: str | list[str], offset: int) -> str:
        if isinstance(value, list):
            return value[min(offset, len(value) - 1)]
        return value

    def __call__(self, _session: Any, url: str, _timeout: int) -> str:
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        assert parsed.scheme == "https"
        assert parsed.hostname == cheongdo.CHEONGDO_HOST
        assert "/apply/" not in parsed.path
        assert "pwd.do" not in parsed.path
        assert "/file/" not in parsed.path
        if parsed.path == self.ledger.path:
            assert query["mid"] == [self.ledger.mid]
            page = int(query["page"][0])
            if page not in self.pages:
                raise AssertionError(f"unexpected page {page}")
            key = ("list", page)
            offset = self.offsets[key]
            self.offsets[key] += 1
            return self._value(self.pages[page], offset)
        if parsed.path == self.ledger.detail_path:
            assert query["mid"] == [self.ledger.mid]
            identity = query["idx"][0]
            if identity not in self.details:
                raise AssertionError(f"unexpected detail {identity}")
            key = ("detail", identity)
            offset = self.offsets[key]
            self.offsets[key] += 1
            return self._value(self.details[identity], offset)
        raise AssertionError(f"unsafe/unexpected endpoint {url}")


class FakeResponse:
    def __init__(self, text: str, url: str, status_code: int = 200) -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.status_code = status_code


def _target(
    ledger: cheongdo.CheongdoLedger | None = None,
    **changes: str,
) -> dict[str, str]:
    owner = ledger or cheongdo.CHEONGDO_LEDGER_BY_KEY["lifelong"]
    target = {"provider": owner.provider, "url": owner.url}
    target.update(changes)
    return target


def _course(
    identity: str,
    *,
    title: str | None = None,
    status: str = "교육중",
    method: str = "선착순",
    apply_period: str = "2026-07-01 09:00 ~ 2026-07-20 17:00",
    period: str = "2026-07-21 ~ 2026-09-30",
    schedule_time: str = "10:00~12:00",
    weekday: str = "화",
    online_current: int = 2,
    online_total: int = 5,
    onsite_current: int = 1,
    onsite_total: int = 3,
    wait_current: int = 0,
    wait_total: int = 2,
    control: bool = False,
) -> dict[str, Any]:
    return {
        "identity": identity,
        "title": title or f"강좌 {identity}",
        "status": status,
        "method": method,
        "apply_period": apply_period,
        "period": period,
        "schedule_time": schedule_time,
        "weekday": weekday,
        "online_current": online_current,
        "online_total": online_total,
        "onsite_current": onsite_current,
        "onsite_total": onsite_total,
        "wait_current": wait_current,
        "wait_total": wait_total,
        "control": control,
    }


def _list_row(course: Mapping[str, Any], sequence: int) -> str:
    return f"""
      <tr>
        <td class="list-num">{sequence}</td>
        <td class="list_tit taL">
          <em class="cate arr">{course['method']}</em>
          <a href="#" data-view-btn data-bc-idx="{course['identity']}">
            {html.escape(str(course['title']))}
          </a>
        </td>
        <td class="list-date01"><span>{str(course['apply_period']).split(' ~ ')[0]} ~</span>
          <span>{str(course['apply_period']).split(' ~ ')[1]}</span></td>
        <td class="list-date02"><span>{course['schedule_time']}</span><span>{course['weekday']}</span></td>
        <td class="list-people01">{course['online_current']} / {course['online_total']}</td>
        <td class="list-people02">{course['wait_current']} / {course['wait_total']}</td>
        <td class="list-people03">-</td>
        <td class="list-state">{course['status']}</td>
      </tr>
    """


def _list_page(
    ledger: cheongdo.CheongdoLedger,
    page: int,
    rows: list[Mapping[str, Any]],
    last: int,
    *,
    title: str | None = None,
    application_path: str | None = None,
) -> str:
    body = "".join(_list_row(item, offset) for offset, item in enumerate(rows, 1))
    if not rows:
        body = '<tr><td colspan="8">등록된 강좌가 없습니다.</td></tr>'
    pager = "".join(
        f"<span>{number}</span>" if number == page else f"<a>{number}</a>"
        for number in range(1, last + 1)
    ) if rows else ""
    status_options = "".join(
        f'<option value="{offset}">{label}</option>'
        for offset, label in enumerate(cheongdo._EXPECTED_STATUS_FILTERS)
    )
    application = application_path or ledger.application_path
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
      <title>{title or f'수강신청 | {ledger.branch} | 교육/강좌 | 홈페이지'}</title></head><body>
        <form id="listForm" name="listForm" method="post"
          action="{ledger.path}?mid={ledger.mid}">
          <input name="page" value="{page}"><input name="aIdx" value="{ledger.aidx}">
          <input name="cIdx" value="{ledger.cidx}"><input name="idx" value="">
          <select name="state">{status_options}</select><input name="searchTxt" value="">
          <table class="woman-edu-list"><thead><tr>
            <th>과목번호</th><th>강좌명</th><th>접수기간</th><th>교육시간</th>
            <th>모집인원 (신청/정원)</th><th>후보인원 (신청/정원)</th>
            <th>추첨인원 (추첨자/후보)</th><th>상태</th>
          </tr></thead><tbody>{body}</tbody></table>
          <div class="bod_page">{pager}</div>
        </form>
        <form id="postListForm" method="post" action="{application}?mid={ledger.mid}"></form>
        <form id="applyListForm" method="post" action="{ledger.applicant_list_path}?mid={ledger.mid}"></form>
      </body></html>
    """


def _pages(
    ledger: cheongdo.CheongdoLedger,
    courses: list[Mapping[str, Any]],
) -> dict[int, str]:
    chunks = [
        courses[offset : offset + cheongdo.CHEONGDO_PAGE_SIZE]
        for offset in range(0, len(courses), cheongdo.CHEONGDO_PAGE_SIZE)
    ]
    assert chunks
    result = {
        number: _list_page(ledger, number, chunk, len(chunks))
        for number, chunk in enumerate(chunks, 1)
    }
    result[len(chunks) + 1] = _list_page(ledger, len(chunks) + 1, [], len(chunks))
    return result


def _pair(label: str, value: str) -> str:
    return f"<dl><dt>{label}</dt><dd>{value}</dd></dl>"


def _detail(
    ledger: cheongdo.CheongdoLedger,
    course: Mapping[str, Any],
    *,
    identity: str | None = None,
    title: str | None = None,
    capacity: str | None = None,
    control_identity: str | None = None,
) -> str:
    actual_identity = identity or str(course["identity"])
    display_title = title or str(course["title"])
    total = int(course["online_total"]) + int(course["onsite_total"])
    capacity_text = capacity or (
        f"{total}명 (온라인:{course['online_total']}명/현장 {course['onsite_total']}명) "
        f"/ 후보 {course['wait_total']}명"
    )
    fields = (
        _pair("접수기간", str(course["apply_period"]))
        + _pair("추가접수기간", "2026-07-21 09:00 ~ 2026-07-28 17:00")
        + _pair("교육일시", str(course["period"]))
        + _pair("교육시간", str(course["schedule_time"]))
        + _pair("교육장소", "3층 다목적강당")
        + _pair("교육대상", "청도군민")
        + _pair("수강료", "무료")
        + _pair("재료비", "재료비 별도")
        + _pair("준비물", "개인 준비물")
        + _pair("강사명", "홍길동")
        + _pair("문의처", "010-1234-5678")
        + _pair("첨부파일", "강의계획서.hwp")
        + _pair("정원", capacity_text)
        + _pair(
            "신청현황",
            f"온라인 : {course['online_current']}명 / 현장 : {course['onsite_current']}명 "
            f"/ 후보 : {course['wait_current']}명",
        )
        + _pair("결제방식", "무료")
        + _pair("추첨", "-")
    )
    control = ""
    if course["control"]:
        bound = control_identity or str(course["identity"])
        control = (
            '<a onclick="yhLib.inline.post(this)" data-req-form-id="postListForm" '
            f'data-req-get-p-l-idx="{bound}" data-req-merge-prefix="list" '
            'data-req-merge-form-id="listForm">신청하기</a>'
        )
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
      <title>| 청도군청 홈페이지</title></head><body><div id="ajaxContent">
        <form id="viewForm" method="post"
          action="/reservation/edu/{ledger.aidx}/lecutre/view.do?mid={ledger.mid}">
          <input name="idx" value="{actual_identity}"></form>
        <div class="title"><em class="cate arr">{course['method']} 강좌</em>
          {html.escape(display_title)}</div>
        <div class="cont"><div class="info"><div class="detail-top">{fields}</div>
          <div class="detail-view">전화 054-370-0000 및 자유 본문은 버린다.</div>
        </div></div><div class="taC">{control}</div>
      </div></body></html>
    """


def _fixture(
    ledger: cheongdo.CheongdoLedger,
    courses: list[Mapping[str, Any]],
) -> tuple[dict[int, str], dict[str, str]]:
    pages = _pages(ledger, courses)
    details = {
        str(item["identity"]): _detail(ledger, item)
        for item in courses
        if item["status"] != "교육완료"
    }
    return pages, details


def _collect(
    ledger: cheongdo.CheongdoLedger,
    courses: list[Mapping[str, Any]],
    **changes: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], FixtureGetter, FakeSession]:
    pages, details = _fixture(ledger, courses)
    pages.update(changes.pop("pages", {}))
    details.update(changes.pop("details", {}))
    getter = FixtureGetter(ledger, pages, details)
    session = FakeSession()
    rows, parser, meta = cheongdo.collect_cheongdo_education(
        _target(ledger),
        today=changes.pop("today", "2026-07-23"),
        session_factory=lambda: session,
        fetcher=getter,
        **changes,
    )
    assert parser == cheongdo.CHEONGDO_PARSER
    return rows, meta, getter, session


def test_audit_constants_hashes_owner_names_and_boundaries() -> None:
    assert hashlib.sha256(
        "https://www.cheongdo.go.kr/open.content/edu/program/course.info".encode()
    ).hexdigest().startswith("94536b8913fa")
    assert hashlib.sha256(cheongdo.CHEONGDO_PASSWORD_CANDIDATE_URL.encode()).hexdigest().startswith(
        "c20c987ce8cc"
    )
    assert set(cheongdo.CHEONGDO_LEDGER_BY_KEY) == {"lifelong", "youth", "women"}
    assert [item.branch for item in cheongdo.CHEONGDO_LEDGERS] == [
        "평생학습교육",
        "청소년교육강좌",
        "여성교육강좌",
    ]
    assert cheongdo.CHEONGDO_LEDGER_BY_KEY["lifelong"].existing_owner
    assert cheongdo.CHEONGDO_LEDGER_BY_KEY["women"].existing_owner
    assert cheongdo.CHEONGDO_LEDGER_BY_KEY["women"].cidx == "58"
    assert not cheongdo.CHEONGDO_LEDGER_BY_KEY["youth"].existing_owner
    assert cheongdo.CHEONGDO_CANDIDATE_AUDIT[
        cheongdo.CHEONGDO_COURSE_INFO_CANDIDATE_ID
    ]["checked_status"] == 404
    assert "pii_form" in cheongdo.CHEONGDO_CANDIDATE_AUDIT[
        cheongdo.CHEONGDO_PASSWORD_CANDIDATE_ID
    ]["decision"]
    providers = {item["provider"] for item in cheongdo.CHEONGDO_SEPARATE_OWNER_BOUNDARIES}
    assert "CULTURE_MUSEUM_618CA2DF06" in providers
    assert "CULTURE_PUBLIC_LIBRARY_A9B3CA5F75" in providers
    assert "CULTURE_PUBLIC_LIBRARY_25F264CCDD" in providers
    assert "CULTURE_CULTURE_FOUNDATION_ED0C7C38D4" in providers


@pytest.mark.parametrize("ledger", cheongdo.CHEONGDO_LEDGERS)
def test_target_matcher_accepts_only_exact_provider_bound_canonical_url(
    ledger: cheongdo.CheongdoLedger,
) -> None:
    assert cheongdo.is_cheongdo_education_target(_target(ledger))
    assert not cheongdo.is_cheongdo_education_target(
        _target(ledger, provider="MUNI_WRONG")
    )
    assert not cheongdo.is_cheongdo_education_target(
        _target(ledger, url=ledger.url + "&page=1")
    )
    assert not cheongdo.is_cheongdo_education_target(
        _target(ledger, url=ledger.url.replace(ledger.mid, "0200000000"))
    )
    assert not cheongdo.is_cheongdo_education_target(
        _target(ledger, url=ledger.url.replace("https://", "http://"))
    )


def test_candidates_and_separate_owner_ledgers_are_not_targets() -> None:
    assert not cheongdo.is_cheongdo_education_target(
        {
            "provider": cheongdo.CHEONGDO_COURSE_INFO_CANDIDATE_PROVIDER,
            "url": cheongdo.CHEONGDO_COURSE_INFO_CANDIDATE_URL,
        }
    )
    assert not cheongdo.is_cheongdo_education_target(
        {
            "provider": cheongdo.CHEONGDO_WOMEN_PROVIDER,
            "url": cheongdo.CHEONGDO_PASSWORD_CANDIDATE_URL,
        }
    )
    for path, mid in (("4", "0205090000"), ("17", "0207010000"), ("5", "0208120000")):
        assert not cheongdo.is_cheongdo_education_target(
            {
                "provider": cheongdo.CHEONGDO_WOMEN_PROVIDER,
                "url": f"https://www.cheongdo.go.kr/reservation/edu/{path}/lecture/list.do?mid={mid}",
            }
        )


def test_managed_session_is_required_and_invalid_target_fails_closed() -> None:
    rows, _, meta = cheongdo.collect_cheongdo_education(_target())
    assert rows == []
    assert meta["configured_collection_error"] == "managed session_factory injection is required"
    rows, _, meta = cheongdo.collect_cheongdo_education(
        {"provider": "MUNI_WRONG", "url": cheongdo.CHEONGDO_PASSWORD_CANDIDATE_URL}
    )
    assert rows == []
    assert "exact Cheongdo education owner" in meta["configured_collection_error"]


def test_complete_current_snapshot_is_safe_and_never_fetches_pii_or_write_endpoints() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["lifelong"]
    current = _course("102", status="추가접수중", control=True)
    expired = _course("101", status="교육완료", period="2026-01-01 ~ 2026-02-01")
    rows, meta, getter, session = _collect(ledger, [current, expired])
    assert session.closed
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == ledger.provider
    assert row["provider_course_id"] == f"{ledger.provider}:lecture:16:102"
    assert row["branch"] == "평생학습교육"
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["application_url"] == ledger.url
    assert row["capacity_current"] == 3
    assert row["capacity_total"] == 8
    assert row["waitlist_total"] == 2
    assert row["address"] == row["venue_address"] == ""
    assert row["description"] == row["title"]
    payload = repr(row)
    assert "010-1234-5678" not in payload
    assert "홍길동" not in payload
    assert "강의계획서.hwp" not in payload
    assert meta["source_rows"] == 2
    assert meta["current_source_count"] == 1
    assert meta["expired_source_count"] == 1
    assert meta["detail_pages"] == 1
    assert meta["list_requests"] == 4
    assert meta["logical_requests"] == 5
    assert meta["application_control_count"] == 1
    assert meta["snapshot_complete"] and meta["full_snapshot_validated"]
    assert getter.offsets[("list", 1)] == 2
    assert getter.offsets[("list", 2)] == 2
    assert all("/apply/" not in url and "pwd.do" not in url for url in getter.calls)


def test_completed_only_institution_publishes_valid_empty_current_snapshot_without_details() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["youth"]
    old = _course("433", status="교육완료", period="2025-11-01 ~ 2025-11-02")
    rows, meta, getter, _ = _collect(ledger, [old])
    assert rows == []
    assert meta["source_rows"] == 1
    assert meta["current_source_count"] == 0
    assert meta["detail_pages"] == 0
    assert meta["no_current_data"] is True
    assert meta["snapshot_complete"] is True
    assert all(urlparse(url).path == ledger.path for url in getter.calls)


def test_stably_empty_canonical_ledger_is_valid_no_current_snapshot() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["women"]
    pages = {
        1: _list_page(ledger, 1, [], 0),
        2: _list_page(ledger, 2, [], 0),
    }
    getter = FixtureGetter(ledger, pages, {})

    rows, parser, meta = cheongdo.collect_cheongdo_education(
        _target(ledger),
        today="2026-07-28",
        session_factory=FakeSession,
        fetcher=getter,
    )

    assert parser == cheongdo.CHEONGDO_PARSER
    assert rows == []
    assert meta["source_rows"] == 0
    assert meta["data_pages"] == 0
    assert meta["sentinel_page"] == 2
    assert meta["list_requests"] == 4
    assert meta["page1_rechecked"] is True
    assert meta["last_page_rechecked"] is True
    assert meta["sentinel_rechecked"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == "canonical institution ledger is stably empty"
    assert meta["snapshot_complete"] is True


def test_three_page_ledger_reads_every_page_exact_sentinel_and_stable_boundaries() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["women"]
    courses = [
        _course(
            str(451 + offset),
            status="교육완료" if offset < 40 else "교육중",
            period="2026-03-01 ~ 2026-06-30" if offset < 40 else "2026-07-01 ~ 2026-08-31",
        )
        for offset in range(42)
    ]
    rows, meta, getter, _ = _collect(ledger, courses)
    assert len(rows) == 2
    assert meta["data_pages"] == 3
    assert meta["page_counts"] == [20, 20, 2]
    assert meta["sentinel_page"] == 4
    assert meta["sentinel_verified"]
    assert meta["page1_rechecked"] and meta["last_page_rechecked"] and meta["sentinel_rechecked"]
    assert meta["list_requests"] == 7
    assert getter.offsets[("list", 1)] == 2
    assert getter.offsets[("list", 3)] == 2
    assert getter.offsets[("list", 4)] == 2


def test_detail_limit_is_completeness_gate_before_any_detail_request() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["lifelong"]
    courses = [_course("101"), _course("102")]
    rows, meta, getter, session = _collect(ledger, courses, detail_limit=1)
    assert rows == []
    assert session.closed
    assert meta["source_cap_reached"] is True
    assert "detail_limit 1 below required 2" in meta["configured_collection_error"]
    assert all(urlparse(url).path == ledger.path for url in getter.calls)


def test_max_pages_is_completeness_gate() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["women"]
    courses = [_course(str(100 + offset), status="교육완료") for offset in range(21)]
    pages, details = _fixture(ledger, courses)
    getter = FixtureGetter(ledger, pages, details)
    rows, _, meta = cheongdo.collect_cheongdo_education(
        _target(ledger),
        max_pages=1,
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=getter,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "exceeds max_pages" in meta["configured_collection_error"]


def test_duplicate_identity_across_pages_fails_closed() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["women"]
    courses = [_course(str(100 + offset), status="교육완료") for offset in range(21)]
    pages = _pages(ledger, courses)
    pages[2] = _list_page(ledger, 2, [courses[0]], 2)
    getter = FixtureGetter(ledger, pages, {})
    rows, _, meta = cheongdo.collect_cheongdo_education(
        _target(ledger),
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=getter,
    )
    assert rows == []
    assert "repeated across pages" in meta["configured_collection_error"]


def test_boundary_drift_fails_closed_atomically() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["lifelong"]
    course = _course("101")
    pages, details = _fixture(ledger, [course])
    changed = dict(course)
    changed["title"] = "변경된 강좌"
    pages[1] = [pages[1], _list_page(ledger, 1, [changed], 1)]
    getter = FixtureGetter(ledger, pages, details)
    rows, _, meta = cheongdo.collect_cheongdo_education(
        _target(ledger),
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=getter,
    )
    assert rows == []
    assert "page-one stability recheck failed" in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


@pytest.mark.parametrize(
    ("detail_changes", "message"),
    [
        ({"identity": "999"}, "detail identity mismatch"),
        ({"title": "다른 강좌"}, "list/detail title mismatch"),
        ({"capacity": "99명 (온라인:5명/현장 3명) / 후보 2명"}, "capacity components"),
        ({"control_identity": "999"}, "unsafe application control"),
    ],
)
def test_detail_binding_and_application_identity_drift_fail_closed(
    detail_changes: Mapping[str, str],
    message: str,
) -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["lifelong"]
    course = _course("101", status="추가접수중", control=True)
    pages = _pages(ledger, [course])
    details = {"101": _detail(ledger, course, **detail_changes)}
    getter = FixtureGetter(ledger, pages, details)
    rows, _, meta = cheongdo.collect_cheongdo_education(
        _target(ledger),
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=getter,
    )
    assert rows == []
    assert message in meta["configured_collection_error"]


def test_pii_or_write_redirect_is_blocked_before_publication() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["lifelong"]
    course = _course("101")
    pages, details = _fixture(ledger, [course])
    normal = FixtureGetter(ledger, pages, details)

    def redirecting(session: Any, url: str, timeout: int) -> Any:
        source = normal(session, url, timeout)
        if urlparse(url).path == ledger.detail_path:
            unsafe = (
                f"https://{cheongdo.CHEONGDO_HOST}/reservation/edu/{ledger.aidx}/"
                f"apply/pwd.do?mid={ledger.mid}"
            )
            return FakeResponse(source, unsafe)
        return source

    rows, _, meta = cheongdo.collect_cheongdo_education(
        _target(ledger),
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=redirecting,
    )
    assert rows == []
    assert "unsafe read URL" in meta["configured_collection_error"]


def test_read_allowlist_rejects_all_sensitive_endpoint_families() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["lifelong"]
    sensitive = [
        f"https://{cheongdo.CHEONGDO_HOST}{ledger.application_path}?mid={ledger.mid}",
        f"https://{cheongdo.CHEONGDO_HOST}{ledger.applicant_list_path}?mid={ledger.mid}",
        cheongdo.CHEONGDO_PASSWORD_CANDIDATE_URL,
        f"https://{cheongdo.CHEONGDO_HOST}/file/direct/download.do",
        f"https://{cheongdo.CHEONGDO_HOST}/docviewer/checkConvert.do",
    ]
    for url in sensitive:
        with pytest.raises(cheongdo.CheongdoContractError):
            cheongdo._guard_read_url(url, ledger)


def test_application_form_action_drift_is_rejected_without_fetching_it() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["lifelong"]
    course = _course("101", status="교육완료")
    pages = _pages(ledger, [course])
    pages[1] = _list_page(
        ledger,
        1,
        [course],
        1,
        application_path=f"/reservation/edu/{ledger.aidx}/apply/changed.do",
    )
    getter = FixtureGetter(ledger, pages, {})
    rows, _, meta = cheongdo.collect_cheongdo_education(
        _target(ledger),
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=getter,
    )
    assert rows == []
    assert "unsafe form action" in meta["configured_collection_error"]
    assert len(getter.calls) == 1


def test_dedupe_cannot_silently_drop_a_current_identity() -> None:
    ledger = cheongdo.CHEONGDO_LEDGER_BY_KEY["lifelong"]
    courses = [_course("101"), _course("102")]
    pages, details = _fixture(ledger, courses)
    getter = FixtureGetter(ledger, pages, details)
    rows, _, meta = cheongdo.collect_cheongdo_education(
        _target(ledger),
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=getter,
        dedupe_rows=lambda values: values[:1],
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_CHEONGDO_LIVE") != "1",
    reason="set RUN_CHEONGDO_LIVE=1 for two-run official-source validation",
)
@pytest.mark.parametrize("ledger", cheongdo.CHEONGDO_LEDGERS)
def test_live_each_institution_twice_is_complete_stable_and_privacy_safe(
    ledger: cheongdo.CheongdoLedger,
) -> None:
    snapshots: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for _ in range(2):
        rows, parser, meta = cheongdo.collect_cheongdo_education(
            _target(ledger),
            timeout=30,
            max_pages=cheongdo.CHEONGDO_RECOMMENDED_MAX_PAGES,
            detail_limit=cheongdo.CHEONGDO_RECOMMENDED_DETAIL_LIMIT,
            today="2026-07-23",
            allow_raw_requests_for_tests=True,
        )
        assert parser == cheongdo.CHEONGDO_PARSER
        assert meta["configured_collection_error"] == ""
        assert meta["snapshot_complete"] and meta["full_snapshot_validated"]
        assert meta["pagination_complete"] and meta["details_complete"]
        assert meta["sentinel_verified"]
        assert meta["page1_rechecked"] and meta["last_page_rechecked"]
        assert meta["sentinel_rechecked"]
        assert meta["privacy_violations"] == 0
        assert meta["application_endpoints_called"] == 0
        assert meta["applicant_endpoints_called"] == 0
        assert meta["attachment_endpoints_called"] == 0
        snapshots.append((rows, meta))
    first_rows, first_meta = snapshots[0]
    second_rows, second_meta = snapshots[1]
    assert first_rows == second_rows
    stable_meta_keys = (
        "source_rows",
        "source_status_counts",
        "data_pages",
        "page_counts",
        "sentinel_page",
        "current_source_count",
        "expired_source_count",
        "detail_pages",
        "returned_count",
        "application_control_count",
    )
    assert {key: first_meta[key] for key in stable_meta_keys} == {
        key: second_meta[key] for key in stable_meta_keys
    }
    baseline = cheongdo.CHEONGDO_LIVE_AUDIT_BASELINE["included"][ledger.key]
    for key in ("source_rows", "data_pages", "sentinel_page", "current_rows", "detail_pages"):
        meta_key = "current_source_count" if key == "current_rows" else key
        assert first_meta[meta_key] == baseline[key]
