from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime
from html import escape
import os
import ssl
from threading import Lock
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest

from Crawler import municipal_donghae as donghae


@dataclass
class Target:
    provider: str = donghae.DONGHAE_PROVIDER
    url: str = donghae.DONGHAE_CANONICAL_URL


@dataclass(frozen=True)
class Course:
    identity: str
    ordinal: int
    title: str
    category: str = "문화예술"
    shift: str = "주간"
    apply_start: str = "2026-02-01"
    apply_end: str = "2026-02-10"
    start: str = "2026-03-01"
    end: str = "2026-06-30"
    schedule: str = "월,수 10:00-12:00"
    selection_method: str = "선착순"
    capacity_current: int = 8
    capacity_total: int = 15
    wait_current: int = 1
    wait_total: int = 30
    status: str = "접수마감"
    venue: str = "3층7강의실"
    fee: str = "무료"


class DummySession:
    def close(self) -> None:
        return None


def _courses() -> list[Course]:
    rows = [
        Course(str(2000 - index), 21 - index, f"과거 강좌 {21 - index}")
        for index in range(21)
    ]
    rows[0] = replace(
        rows[0],
        title="라인댄스",
        shift="야간",
        apply_start="2026-07-13",
        apply_end="2026-07-21",
        start="2026-08-18",
        end="2026-12-07",
        schedule="화,목 19:00-21:00",
        selection_method="추첨",
        capacity_current=55,
        capacity_total=15,
        wait_current=0,
        wait_total=50,
        venue="지하헬스실",
        fee="유료 (40,000원)",
    )
    return rows


def _status_class(value: str) -> str:
    return {"접수예정": "n1", "접수중": "n2", "접수마감": "n3"}[value]


def _list_row(
    course: Course,
    source: donghae.DonghaeCatalogue,
    *,
    identity_override: str | None = None,
) -> str:
    identity = identity_override or course.identity
    return f"""
      <tr>
        <td class="td_num">{course.ordinal}</td>
        <td>{escape(course.category)}</td>
        <td class="p-subject"><a href="./selectEduLctreWebView.do?key={source.key}&amp;eduLctreNo={identity}">
          <span class="two_shift {'night' if course.shift == '야간' else 'weekly'}">{course.shift}</span>
          <span class="table_row-title">{escape(course.title)}</span>
        </a></td>
        <td><p class="register_date">{course.apply_start}~{course.apply_end}</p>
            <p class="education_date">{course.start}~{course.end}</p></td>
        <td>{escape(course.schedule)}</td>
        <td>{course.selection_method}</td>
        <td><p><span class="request_num">{course.capacity_current}</span> /
            <span class="recruit_num">{course.capacity_total}</span></p>
            <p>( <span class="pre-req_num">{course.wait_current}</span> /
            <span class="pre-rec_num">{course.wait_total}</span> )</p></td>
        <td><a class="p-btn {_status_class(course.status)}"
          href="./selectEduLctreWebView.do?key={source.key}&amp;eduLctreNo={identity}">{course.status}</a></td>
      </tr>
    """


def _form(
    source: donghae.DonghaeCatalogue,
    year: int = 2026,
    *,
    filtered: bool = False,
) -> str:
    category = "12" if filtered else ""
    return f"""
      <form name="bbsNttSearchForm" method="get" action="./selectEduLctreWebList.do">
        <input name="key" value="{source.key}">
        <input name="eduInfoNo" value="{source.info_no}">
        <select name="year"><option value="2027">2027</option>
          <option value="{year}" selected>{year}</option></select>
        <select name="eduClassNo"><option value="" selected>기수선택</option></select>
        <select name="eduCtgryNo"><option value="{category}" selected>과정선택</option></select>
        <select name="rceptSttus"><option value="" selected>접수상태선택</option></select>
        <input name="eduPoolSj" value="">
      </form>
    """


def _list_html(
    rows: list[Course],
    source: donghae.DonghaeCatalogue,
    *,
    page: int,
    total: int,
    last: int,
    year: int = 2026,
    filtered: bool = False,
    linked_last: int | None = None,
    identity_override: str | None = None,
) -> str:
    headers = (
        "<th>번호</th><th>과정</th><th>강좌명</th>"
        "<th>접수기간/교육기간</th><th>교육요일 및 시간</th>"
        "<th>선발방법</th><th>신청/모집<br>(예비자)</th><th>접수상태</th>"
    )
    if rows:
        body = "".join(
            _list_row(
                item,
                source,
                identity_override=identity_override if index == 0 else None,
            )
            for index, item in enumerate(rows)
        )
    else:
        body = '<tr><td class="empty" colspan="8">등록된 강좌 목록이 없습니다.</td></tr>'
    advertised_last = linked_last if linked_last is not None else last
    return f"""
      <html><head><title>수강신청 - 평생학습관</title></head><body>
      <div id="contents">{_form(source, year, filtered=filtered)}
        <table class="p-table simple"><thead><tr>{headers}</tr></thead>
          <tbody class="text_center">{body}</tbody></table>
        <div class="p-pagination"><a class="p-page__link next-end"
          href="./selectEduLctreWebList.do?key={source.key}&amp;year={year}&amp;eduInfoNo={source.info_no}&amp;pageIndex={advertised_last}">끝 페이지</a></div>
      </div></body></html>
    """


def _detail_html(
    course: Course,
    source: donghae.DonghaeCatalogue,
    *,
    control: str = "auto",
    title: str | None = None,
    venue: str | None = None,
    status: str | None = None,
) -> str:
    actual_status = status or course.status
    if control == "auto":
        control = "safe" if actual_status == "접수중" else "missing"
    control_html = ""
    if control == "safe":
        href = (
            f"./addEduLctreReqstWebView.do?key={source.key}&amp;eduInfoNo={source.info_no}&amp;"
            f"eduCtgryNo=12&amp;eduClassNo=2&amp;eduLctreNo={course.identity}&amp;eduPoolNo=2833"
        )
        control_html = f'<div class="top_btn"><a class="p-button application" href="{href}">신청하기</a></div>'
    elif control == "unsafe":
        control_html = (
            '<div class="top_btn"><a class="p-button application" '
            'href="https://evil.example/apply?eduLctreNo=999">신청하기</a></div>'
        )
    return f"""
      <html><head><title>수강신청 - 평생학습관</title></head><body>
      <div id="contents"><div class="program lecture_apply view">
      <div class="p-wrap bbs bbs__view">
        <div class="top_box">
          <span class="lc_subject">[{escape(course.category)}]</span>
          <span class="title">{escape(title if title is not None else course.title)}</span>
          <span class="two_shift {'night' if course.shift == '야간' else 'weekly'} top">{course.shift}</span>
          <span class="p-btn {_status_class(actual_status)}" href="#">{actual_status}</span>
          {control_html}
        </div>
        <table class="p-table block"><tbody class="p-table--th-left">
          <tr><th>접수기간</th><td>{course.apply_start} (09시 00분) ~ {course.apply_end} (18시 00분)</td></tr>
          <tr><th>접수현황</th><td>신청 <strong>{course.capacity_current}</strong>명 /
            모집정원 <strong>{course.capacity_total}</strong>명
            (예비자신청 <strong>{course.wait_current}</strong>명 /
            예비자정원 <strong>{course.wait_total}</strong>명)</td></tr>
          <tr><th>선발방법</th><td>{course.selection_method}</td></tr>
          <tr><th>교육기간</th><td>{course.start} ~ {course.end} (16주간)</td></tr>
          <tr><th>교육시간</th><td>{escape(course.schedule)}</td></tr>
          <tr><th>교육장</th><td>{escape(venue if venue is not None else course.venue)}</td></tr>
          <tr><th>강사명</th><td>홍길동</td></tr>
          <tr><th>수강료</th><td>{escape(course.fee)}</td></tr>
          <tr><th>강좌소개</th><td>문의 033-123-4567, personal@example.com 자유 서술</td></tr>
          <tr><th>강의계획서</th><td><a href="/secret.pdf">개인자료.pdf</a></td></tr>
        </tbody></table>
      </div></div></div></body></html>
    """


def _other_catalogue_rows() -> dict[str, list[Course]]:
    return {
        "digital": [
            Course("3002", 2, "컴퓨터 기초", category="직업능력"),
            Course("3001", 1, "엑셀", category="직업능력"),
        ],
        "special": [
            Course("4002", 2, "특성화 강좌", category="인문교양"),
            Course("4001", 1, "기획 강좌", category="인문교양"),
        ],
        "university": [
            Course("5001", 1, "평생교육론", category="학력보완"),
        ],
    }


class HtmlFixture:
    def __init__(
        self,
        *,
        rows: list[Course] | None = None,
        rows_by_catalogue: dict[str, list[Course]] | None = None,
        nonempty_sentinel: bool = False,
        unstable_first: bool = False,
        unstable_last: bool = False,
        filtered_form: bool = False,
        bad_last_link: bool = False,
        ordinal_gap: bool = False,
        terminal_unlisted: bool = False,
        duplicate_identity: bool = False,
        detail_control: str = "auto",
        detail_title_mismatch: bool = False,
        detail_venue_empty: bool = False,
        detail_error: Exception | None = None,
    ) -> None:
        defaults = _other_catalogue_rows()
        defaults["regular"] = list(rows if rows is not None else _courses())
        if rows_by_catalogue is not None:
            defaults.update(
                {key: list(value) for key, value in rows_by_catalogue.items()}
            )
        self.rows_by_catalogue = defaults
        self.nonempty_sentinel = nonempty_sentinel
        self.unstable_first = unstable_first
        self.unstable_last = unstable_last
        self.filtered_form = filtered_form
        self.bad_last_link = bad_last_link
        self.ordinal_gap = ordinal_gap
        self.terminal_unlisted = terminal_unlisted
        self.duplicate_identity = duplicate_identity
        self.detail_control = detail_control
        self.detail_title_mismatch = detail_title_mismatch
        self.detail_venue_empty = detail_venue_empty
        self.detail_error = detail_error
        self.calls: Counter[str] = Counter()
        self.lock = Lock()

    def __call__(self, _session: DummySession, url: str, timeout: int) -> str:
        assert timeout == 7
        with self.lock:
            self.calls[url] += 1
            call_number = self.calls[url]
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        key = query["key"][0]
        info_no = query.get("eduInfoNo", [""])[0]
        if parsed.path == donghae.DONGHAE_DETAIL_PATH:
            source = next(
                item for item in donghae.DONGHAE_CATALOGUES if item.key == key
            )
        else:
            source = next(
                item
                for item in donghae.DONGHAE_CATALOGUES
                if (item.key, item.info_no) == (key, info_no)
            )
        source_rows = self.rows_by_catalogue[source.code]
        if parsed.path == donghae.DONGHAE_DETAIL_PATH:
            if self.detail_error is not None:
                raise self.detail_error
            identity = query["eduLctreNo"][0]
            course = next(item for item in source_rows if item.identity == identity)
            return _detail_html(
                course,
                source,
                control=self.detail_control,
                title=("다른 강좌" if self.detail_title_mismatch else None),
                venue=("" if self.detail_venue_empty else None),
            )

        page = int(query["pageIndex"][0])
        year = int(query["year"][0])
        total = len(source_rows)
        last = max(1, (total + donghae.DONGHAE_PAGE_SIZE - 1) // donghae.DONGHAE_PAGE_SIZE)
        start = (page - 1) * donghae.DONGHAE_PAGE_SIZE
        page_rows = list(source_rows[start : start + donghae.DONGHAE_PAGE_SIZE])
        if (
            source.code == "regular"
            and page == last
            and last > 1
            and self.terminal_unlisted
        ):
            page_rows = []
        if (
            source.code == "regular"
            and page == last + 1
            and self.nonempty_sentinel
        ):
            page_rows = [source_rows[-1]]
        if source.code == "regular" and page == 1 and call_number > 1 and self.unstable_first:
            page_rows[0] = replace(page_rows[0], title="변경된 첫 강좌")
        if source.code == "regular" and page == last and call_number > 1 and self.unstable_last:
            page_rows[-1] = replace(page_rows[-1], title="변경된 마지막 강좌")
        if source.code == "regular" and page == 2 and self.ordinal_gap and page_rows:
            page_rows[0] = replace(page_rows[0], ordinal=999)
        identity_override = (
            source_rows[0].identity
            if source.code == "regular" and self.duplicate_identity and page == 2
            else None
        )
        return _list_html(
            page_rows,
            source,
            page=page,
            total=total,
            last=last,
            year=year,
            filtered=self.filtered_form and source.code == "regular",
            linked_last=(
                last - 1
                if self.bad_last_link and source.code == "regular"
                else last
            ),
            identity_override=identity_override,
        )


def _collect(
    fixture: HtmlFixture,
    *,
    target: Target | None = None,
    max_pages: int = 20,
    detail_limit: int = 20,
    today: str = "2026-07-22",
    dedupe_rows=None,
):
    return donghae.collect_donghae_education(
        target or Target(),
        timeout=7,
        max_pages=max_pages,
        detail_limit=detail_limit,
        today=today,
        max_workers=4,
        session_factory=DummySession,
        fetcher=fixture,
        dedupe_rows=dedupe_rows,
    )


def test_complete_snapshot_proves_boundaries_detail_branch_and_privacy() -> None:
    fixture = HtmlFixture()
    rows, parser, meta = _collect(fixture)

    assert parser == donghae.DONGHAE_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"].endswith(":2000")
    assert row["title"] == "야간 라인댄스"
    assert row["branch"] == "동해시 평생학습관"
    assert row["venue"] == "지하헬스실"
    assert row["municipality_code"] == "5117000000"
    assert row["period"] == "2026-08-18 ~ 2026-12-07"
    assert row["status"] == "CLOSED"
    assert row["reservation_available"] is False
    assert row["application_url"] == ""
    assert row["fee_amount"] == 40000
    assert row["capacity_current"] == 55
    assert row["capacity_total"] == 15
    assert row["raw_fields"]["detail_verified"] is True
    payload = repr(row)
    for forbidden in (
        "홍길동",
        "033-123-4567",
        "personal@example.com",
        "자유 서술",
        "secret.pdf",
        "개인자료.pdf",
    ):
        assert forbidden not in payload

    assert meta["source_total"] == meta["source_rows"] == 26
    assert meta["source_totals"] == {
        "regular": 21,
        "digital": 2,
        "special": 2,
        "university": 1,
    }
    assert meta["declared_pages"] == 6
    assert meta["page_counts"] == {
        "regular": {1: 10, 2: 10, 3: 1},
        "digital": {1: 2},
        "special": {1: 2},
        "university": {1: 1},
    }
    assert meta["required_list_requests"] == meta["list_requests"] == 15
    assert meta["sentinel_requests"] == 4
    assert meta["stability_rechecks"] == 5
    assert meta["current_source_count"] == 1
    assert meta["expired_count"] == 25
    assert meta["detail_attempts"] == meta["detail_pages"] == 1
    assert meta["source_status_counts"] == {"접수마감": 26}
    assert meta["current_status_counts"] == {"CLOSED": 1}
    assert meta["branch_counts"] == {"동해시 평생학습관": 1}
    assert meta["venue_counts"] == {"지하헬스실": 1}
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["pii_payload_persisted"] is False
    assert meta["configured_collection_error"] == ""
    assert fixture.calls[donghae.donghae_list_url(1, 2026)] == 2
    assert fixture.calls[donghae.donghae_list_url(3, 2026)] == 2
    assert fixture.calls[donghae.donghae_list_url(4, 2026)] == 1
    for source in donghae.DONGHAE_CATALOGUES[1:]:
        assert fixture.calls[donghae.donghae_list_url(1, 2026, source)] == 2
        assert fixture.calls[donghae.donghae_list_url(2, 2026, source)] == 1
        snapshot = meta["catalogue_snapshots"][source.code]
        assert snapshot["source_total"] == len(
            fixture.rows_by_catalogue[source.code]
        )
        assert snapshot["data_pages"] == 1
        assert snapshot["required_list_requests"] == 3


def test_open_course_requires_safe_identity_bound_application_control() -> None:
    course = replace(
        _courses()[0],
        ordinal=1,
        status="접수중",
        apply_start="2026-07-01",
        apply_end="2026-07-31",
    )
    rows, _parser, meta = _collect(HtmlFixture(rows=[course]))

    assert len(rows) == 1
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_type"] == "ONLINE_RESERVATION"
    assert rows[0]["application_url"].startswith(
        "https://www.dh.go.kr/lifelong/addEduLctreReqstWebView.do?"
    )
    assert "eduLctreNo=2000" in rows[0]["application_url"]
    assert meta["online_open_count"] == 1
    assert meta["application_control_count"] == 1
    assert meta["snapshot_complete"] is True


@pytest.mark.parametrize("control", ["missing", "unsafe"])
def test_open_course_without_safe_application_control_fails_closed(control: str) -> None:
    course = replace(
        _courses()[0],
        ordinal=1,
        status="접수중",
        apply_start="2026-07-01",
        apply_end="2026-07-31",
    )
    rows, _parser, meta = _collect(
        HtmlFixture(rows=[course], detail_control=control)
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "application control" in meta["configured_collection_error"]


def test_scheduled_course_has_no_application_url() -> None:
    course = replace(
        _courses()[0],
        ordinal=1,
        status="접수예정",
        apply_start="2026-08-01",
        apply_end="2026-08-10",
    )
    rows, _parser, meta = _collect(HtmlFixture(rows=[course]))
    assert len(rows) == 1
    assert rows[0]["status"] == "SCHEDULED"
    assert rows[0]["application_url"] == ""
    assert rows[0]["reservation_available"] is False
    assert meta["snapshot_complete"] is True


def test_scheduled_course_is_valid_on_date_only_application_boundary() -> None:
    course = replace(
        _courses()[0],
        ordinal=1,
        status="접수예정",
        apply_start="2026-07-28",
        apply_end="2026-07-29",
    )
    rows, _parser, meta = _collect(
        HtmlFixture(rows=[course]),
        today="2026-07-28",
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "SCHEDULED"
    assert rows[0]["application_url"] == ""
    assert meta["snapshot_complete"] is True


def test_scheduled_course_fails_after_application_boundary() -> None:
    course = replace(
        _courses()[0],
        ordinal=1,
        status="접수예정",
        apply_start="2026-07-28",
        apply_end="2026-07-29",
    )
    rows, _parser, meta = _collect(
        HtmlFixture(rows=[course]),
        today="2026-07-29",
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "scheduled status/application period mismatch" in (
        meta["configured_collection_error"]
    )


def test_official_empty_terminal_page_is_audited_as_unlisted() -> None:
    rows, _parser, meta = _collect(HtmlFixture(terminal_unlisted=True))

    assert len(rows) == 1
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 26
    assert meta["source_rows"] == 25
    assert meta["official_unlisted_count"] == 1
    assert meta["official_unlisted_counts"]["regular"] == 1
    regular = meta["catalogue_snapshots"]["regular"]
    assert regular["source_total"] == 20
    assert regular["advertised_total"] == 21
    assert regular["official_unlisted_count"] == 1


def test_all_four_catalogues_are_one_owner_with_source_bound_details() -> None:
    regular = replace(
        _courses()[0],
        ordinal=1,
        identity="2101",
    )
    digital = replace(
        _courses()[0],
        ordinal=1,
        identity="3101",
        title="컴퓨터 기초",
        shift="주간",
        category="직업능력",
    )
    special = replace(
        _courses()[0],
        ordinal=1,
        identity="4101",
        title="동해바캉스 굿즈",
        shift="주간",
        category="장애인",
        status="접수중",
        apply_start="2026-07-01",
        apply_end="2026-07-31",
    )
    university = replace(
        _courses()[0],
        ordinal=1,
        identity="5101",
        title="평생교육론",
        shift="야간",
        category="학력보완",
        status="접수예정",
        apply_start="2026-08-01",
        apply_end="2026-08-10",
    )
    fixture = HtmlFixture(
        rows_by_catalogue={
            "regular": [regular],
            "digital": [digital],
            "special": [special],
            "university": [university],
        }
    )
    rows, _parser, meta = _collect(fixture)

    assert len(rows) == 4
    assert meta["source_total"] == meta["current_source_count"] == 4
    assert meta["returned_counts"] == {
        "regular": 1,
        "digital": 1,
        "special": 1,
        "university": 1,
    }
    by_source = {
        row["raw_fields"]["source_catalogue"]: row for row in rows
    }
    assert set(by_source) == {"regular", "digital", "special", "university"}
    assert "key=1060" in by_source["regular"]["raw_url"]
    assert "key=1064" in by_source["digital"]["raw_url"]
    assert "key=1067" in by_source["special"]["raw_url"]
    assert "key=1926" in by_source["university"]["raw_url"]
    assert by_source["special"]["status"] == "OPEN"
    assert by_source["special"]["reservation_available"] is True
    assert "key=1067" in by_source["special"]["application_url"]
    assert "eduInfoNo=3" in by_source["special"]["application_url"]
    assert by_source["university"]["status"] == "SCHEDULED"
    assert by_source["university"]["application_url"] == ""
    assert meta["sentinel_requests"] == 4
    assert meta["stability_rechecks"] == 4
    assert meta["snapshot_complete"] is True


def test_duplicate_identity_across_catalogues_fails_closed() -> None:
    fixture = HtmlFixture(
        rows_by_catalogue={
            "special": [Course("2000", 1, "중복 카탈로그 강좌")]
        }
    )
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert meta["identity_duplicate_count"] == 1
    assert "across catalogues" in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_closed_course_with_application_control_fails_closed() -> None:
    rows, _parser, meta = _collect(HtmlFixture(detail_control="safe"))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "inactive course exposes application control" in meta[
        "configured_collection_error"
    ]


def test_complete_expired_snapshot_is_verified_no_current_data() -> None:
    expired = [
        replace(item, start="2026-07-01", end="2026-07-21")
        for item in _courses()
    ]
    fixture = HtmlFixture(rows=expired)
    rows, _parser, meta = _collect(fixture)

    assert rows == []
    assert meta["source_rows"] == 26
    assert meta["current_source_count"] == 0
    assert meta["expired_count"] == 26
    assert meta["detail_attempts"] == 0
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert not any("selectEduLctreWebView" in url for url in fixture.calls)


@pytest.mark.parametrize(
    ("fixture", "needle"),
    [
        (HtmlFixture(nonempty_sentinel=True), "sentinel"),
        (HtmlFixture(unstable_first=True), "stability recheck"),
        (HtmlFixture(unstable_last=True), "stability recheck"),
        (HtmlFixture(filtered_form=True), "unfiltered form field"),
        (HtmlFixture(bad_last_link=True), "last-page link"),
        (HtmlFixture(ordinal_gap=True), "descending ordinal"),
        (HtmlFixture(duplicate_identity=True), "duplicate official identities"),
        (HtmlFixture(detail_title_mismatch=True), "heading or status mismatch"),
        (HtmlFixture(detail_venue_empty=True), "education venue empty"),
    ],
)
def test_incomplete_or_changed_contract_fails_closed(
    fixture: HtmlFixture, needle: str
) -> None:
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert needle in meta["configured_collection_error"]


def test_detail_fetch_error_and_caps_fail_closed() -> None:
    rows, _parser, meta = _collect(
        HtmlFixture(detail_error=RuntimeError("detail unavailable"))
    )
    assert rows == []
    assert meta["detail_errors"] > 0
    assert "detail unavailable" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(HtmlFixture(), max_pages=5)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(HtmlFixture(), detail_limit=0)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]


def test_deduper_must_not_drop_or_inject_private_fields() -> None:
    rows, _parser, meta = _collect(HtmlFixture(), dedupe_rows=lambda values: [])
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "identity cardinality" in meta["configured_collection_error"]

    def inject(values):
        result = list(values)
        result[0]["phone"] = "033-123-4567"
        return result

    rows, _parser, meta = _collect(HtmlFixture(), dedupe_rows=inject)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "forbidden PII" in meta["configured_collection_error"]


def test_target_identity_urls_and_verified_tls_contract() -> None:
    assert donghae.is_donghae_education_target(Target()) is True
    assert donghae.is_donghae_education_target(
        Target(provider=donghae.DONGHAE_SUPERSEDED_PROVIDER)
    ) is False
    assert donghae.is_donghae_education_target(
        Target(url=donghae.DONGHAE_CANONICAL_URL + "&pageIndex=1")
    ) is False
    assert donghae.donghae_detail_url("2041").endswith(
        "key=1060&eduLctreNo=2041"
    )
    assert len(donghae.DONGHAE_CATALOGUES) == 4
    assert donghae.DONGHAE_OWNERSHIP_ALIAS_URLS == tuple(
        source.canonical_url for source in donghae.DONGHAE_CATALOGUES[1:]
    )
    assert donghae.donghae_detail_url("2043", "digital").endswith(
        "key=1064&eduLctreNo=2043"
    )
    with pytest.raises(ValueError):
        donghae.donghae_detail_url("../../login")

    context = donghae._donghae_tls_context()  # noqa: SLF001 - transport boundary.
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_wrong_target_does_not_create_session() -> None:
    def forbidden_session():
        pytest.fail("invalid target must not create a session")

    rows, parser, meta = donghae.collect_donghae_education(
        Target(provider="MUNI_WRONG"),
        timeout=7,
        session_factory=forbidden_session,
    )
    assert rows == []
    assert parser == donghae.DONGHAE_PARSER
    assert meta["snapshot_complete"] is False
    assert "canonical Donghae" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("DONGHAE_EDUCATION_LIVE") != "1",
    reason="set DONGHAE_EDUCATION_LIVE=1 for official-source verification",
)
def test_live_official_snapshot() -> None:
    cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()
    rows, parser, meta = donghae.collect_donghae_education(
        Target(),
        timeout=40,
        max_pages=40,
        detail_limit=300,
        today=cutoff,
        max_workers=8,
    )

    assert parser == donghae.DONGHAE_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert (
        meta["source_rows"] + meta["official_unlisted_count"]
        == meta["source_total"]
    )
    assert meta["list_requests"] == meta["required_list_requests"]
    assert meta["sentinel_requests"] == 4
    assert len(meta["catalogue_snapshots"]) == 4
    assert meta["stability_rechecks"] == sum(
        2 if snapshot["data_pages"] > 1 else 1
        for snapshot in meta["catalogue_snapshots"].values()
    )
    assert meta["detail_pages"] == meta["current_source_count"]
    assert len(rows) == meta["current_source_count"]
    assert all(date.fromisoformat(row["end_date"]) >= cutoff for row in rows)
    assert all(row["branch"] == donghae.DONGHAE_BRANCH for row in rows)
    assert all(row["raw_fields"]["detail_verified"] is True for row in rows)
    assert all(not donghae._privacy_errors(row) for row in rows)  # noqa: SLF001
