from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_jecheon as jecheon


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    category: str = "2025년 하반기 프로그램"
    source_status: str = "접수마감"
    status_class: str = "close"
    start: str = "2025-01-01"
    end: str = "2025-02-01"
    apply_start: str = "2024-12-01"
    apply_end: str = "2024-12-10"
    schedule: str = "매주 화요일 10:00~12:00"
    venue: str = "평생학습관 강의실3(2층)"
    fee: str = "0원"
    remaining: int = 4
    total: int = 16
    wait_remaining: int = 8
    wait_total: int = 10


class DummySession:
    def close(self) -> None:
        return None


def _courses() -> list[Course]:
    rows = [Course(str(identity), f"제천 과거 강좌 {identity}") for identity in range(17, 0, -1)]
    rows[0] = Course(
        "17",
        "제천 예정 강좌",
        category="2026년 하반기 프로그램",
        source_status="접수대기",
        status_class="stay",
        start="2026-08-18",
        end="2026-11-03",
        apply_start="2026-07-27",
        apply_end="2026-08-07",
        remaining=16,
    )
    rows[1] = Course(
        "16",
        "제천 접수중 강좌",
        category="2026년 하반기 프로그램",
        source_status="접수중",
        status_class="ing",
        start="2026-07-01",
        end="2026-08-31",
        apply_start="2026-07-01",
        apply_end="2026-08-01",
    )
    rows[2] = Course(
        "15",
        "제천 교육중 마감 강좌",
        category="2026년 하반기 프로그램",
        start="2026-05-23",
        end="2026-08-08",
        apply_start="2026-05-05",
        apply_end="2026-05-18",
        remaining=0,
    )
    return rows


def _redundant_detail_href(identity: str) -> str:
    return (
        "https://www.jecheon.go.kr/okjcedu/bbs/board.php?"
        f"bo_table=class_list&mode=view&rm_ix={identity}&&bo_table=class_list"
    )


def _card(course: Course) -> str:
    href = _redundant_detail_href(course.identity)
    return f"""
      <li class="{course.status_class}">
        <div class="img_wrap">
          <span class="status">{escape(course.source_status)}</span>
          <a class="img" href="{href}"><img alt="thumbnail"></a>
          <a class="btn-like" href="#none">[찜하기]</a>
        </div>
        <div class="con_wrap">
          <div><div class="cate">{escape(course.category)}</div></div>
          <div class="date">접수 정보</div>
          <div class="title">{escape(course.title)}</div>
          <p class="txt_content">저장하면 안 되는 자유 설명</p>
          <div class="btn_area"><a class="more" href="{href}">자세히보기</a></div>
        </div>
      </li>
    """


def _list_html(page: int, rows: list[Course], all_courses: list[Course]) -> str:
    counts = Counter(course.category for course in all_courses)
    last = (len(all_courses) + jecheon.JECHEON_PAGE_SIZE - 1) // jecheon.JECHEON_PAGE_SIZE
    tabs = ['<li><a href="https://www.jecheon.go.kr/okjcedu/class_list">전체</a></li>']
    for index, (name, count) in enumerate(counts.items(), start=1):
        tabs.append(
            '<li><a href="https://www.jecheon.go.kr/okjcedu/class_list?'
            f'sca={index}">{escape(name)} ({count})</a></li>'
        )
    current = f'<strong class="pg_current">{page}</strong>' if rows else ""
    boundary = (
        '<a class="pg_page pg_end" href="/okjcedu/bbs/board.php?'
        f'bo_table=class_list&page={last}">맨끝</a>'
    )
    return f"""
      <html><head><title>평생학습관 강좌 {page} 페이지 | 제천시평생학습관</title></head>
      <body><div id="class_wrap" class="list">
        <div class="tabType_1"><ul>{''.join(tabs)}</ul></div>
        <ul id="class_list">{''.join(_card(course) for course in rows)}</ul>
        <nav class="pg_wrap"><span class="pg">{current}{boundary}</span></nav>
      </div></body></html>
    """


def _display_date(value: str, *, time: str = "") -> str:
    year, month, day = value.split("-")
    return f"{year}년{int(month):02d}월{int(day):02d}일(월){(' ' + time) if time else ''}"


def _detail_html(
    course: Course,
    *,
    title: str | None = None,
    handler_identity: str | None = None,
    control: bool | None = None,
) -> str:
    show_control = course.source_status == "접수중" if control is None else control
    control_html = (
        '<a class="btn btn160" href="#none" onclick="check_reservation();">수강신청</a>'
        if show_control
        else ""
    )
    handler_id = handler_identity if handler_identity is not None else course.identity
    return f"""
      <html><head><title>평생학습관 강좌 1 페이지 | 제천시평생학습관</title></head><body>
        <div id="class_wrap" class="view">
          <div id="class_view" class="{course.status_class}">
            <div class="intro_box">
              <div class="img_wrap"><span class="status">{escape(course.source_status)}</span></div>
              <div class="con_wrap">
                <div class="txt_subject">
                  <p>{escape(title if title is not None else course.title)}</p>
                  <p class="txt_content">임의 상세 설명과 개인정보 가능 영역</p>
                </div>
                <ul class="class_info">
                  <li><b>접수 기간</b><p>{_display_date(course.apply_start, time='09:00')} <span>~ {_display_date(course.apply_end, time='23:50')}</span></p></li>
                  <li><b>교육 기간</b><p><span>{_display_date(course.start)}</span><span>~</span><span>{_display_date(course.end)}</span></p></li>
                  <li><b>시간</b><p><span>{escape(course.schedule)}</span></p></li>
                  <li><b>교육비</b><p><span>{escape(course.fee)}</span></p></li>
                  <li><b>정원</b><p>(신청가능인원/최대인원 - {course.remaining}/{course.total}) (예비신청가능인원/예비최대인원 - {course.wait_remaining}/{course.wait_total})</p></li>
                  <li><b>장소</b><p><span>{escape(course.venue)}</span></p></li>
                  <li><b>강사</b><p><span>홍길동</span></p></li>
                  <li><b>문의</b><p><span>043-641-5491</span></p></li>
                </ul>
              </div>
            </div>
            <div class="detail_box"><div class="detail_con">저장하면 안 되는 상세 본문</div></div>
          </div>
        </div>
        <div class="btn_confirm write_div mt50 type2">
          <a class="btn btn_prev" href="{jecheon.JECHEON_CANONICAL_URL}">이전 화면</a>
          {control_html}
        </div>
        <script>
          function check_reservation() {{
            location.href = '{jecheon.jecheon_application_url(handler_id)}';
          }}
        </script>
      </body></html>
    """


class HtmlFixture:
    def __init__(self, courses: list[Course] | None = None) -> None:
        self.courses = list(courses or _courses())
        self.pages: dict[str, str] = {}
        last = (len(self.courses) + jecheon.JECHEON_PAGE_SIZE - 1) // jecheon.JECHEON_PAGE_SIZE
        for page in range(1, last + 1):
            start = (page - 1) * jecheon.JECHEON_PAGE_SIZE
            page_rows = self.courses[start : start + jecheon.JECHEON_PAGE_SIZE]
            self.pages[jecheon.jecheon_list_url(page)] = _list_html(page, page_rows, self.courses)
        self.pages[jecheon.jecheon_list_url(last + 1)] = _list_html(last + 1, [], self.courses)
        for course in self.courses:
            self.pages[jecheon.jecheon_detail_url(course.identity)] = _detail_html(course)
        self.overrides: dict[tuple[str, int], str] = {}
        self.calls: Counter[str] = Counter()
        self.lock = Lock()

    def fetch(self, _session: DummySession, url: str, _timeout: int) -> str:
        with self.lock:
            self.calls[url] += 1
            call = self.calls[url]
        override = self.overrides.get((url, call))
        if override is not None:
            return override
        if url not in self.pages:
            raise RuntimeError(f"unexpected URL: {url}")
        return self.pages[url]


def _target(**changes: str) -> Target:
    values = {
        "provider": jecheon.JECHEON_PROVIDER,
        "url": jecheon.JECHEON_CANONICAL_URL,
        "candidate_id": jecheon.JECHEON_CANONICAL_CANDIDATE_ID,
    }
    values.update(changes)
    return Target(**values)


def _collect(fixture: HtmlFixture, **kwargs):
    return jecheon.collect(
        _target(),
        today="2026-07-21",
        timeout=5,
        max_pages=20,
        detail_limit=100,
        max_workers=4,
        session_factory=DummySession,
        fetcher=fixture.fetch,
        **kwargs,
    )


def test_constants_urls_and_exact_target_contract() -> None:
    assert jecheon.JECHEON_PROVIDER == "MUNI_WWW_JECHEON_GO_KR_A4E8D5CB"
    assert jecheon.JECHEON_CANONICAL_CANDIDATE_ID == "MUNI_IR_427A861B331C"
    assert jecheon.JECHEON_REVIEW_CANDIDATE_ID == "MUNI_IR_6AC02AE4EEE2"
    assert jecheon.jecheon_list_url(1) == jecheon.JECHEON_CANONICAL_URL
    assert parse_qs(urlparse(jecheon.jecheon_list_url(9)).query) == {
        "bo_table": ["class_list"],
        "page": ["9"],
    }
    assert parse_qs(urlparse(jecheon.jecheon_detail_url("125")).query) == {
        "bo_table": ["class_list"],
        "mode": ["view"],
        "rm_ix": ["125"],
    }
    assert jecheon.is_target(_target())
    assert not jecheon.is_target(_target(provider="WRONG"))
    assert not jecheon.is_target(_target(url=jecheon.JECHEON_CANONICAL_URL + "?page=1"))
    assert not jecheon.is_target(_target(url=jecheon.JECHEON_CANONICAL_URL + "#top"))
    assert not jecheon.is_target(
        Target(jecheon.JECHEON_ANNOUNCEMENT_PROVIDER, jecheon.JECHEON_ANNOUNCEMENT_URL)
    )
    with pytest.raises(ValueError):
        jecheon.jecheon_detail_url("../1")


def test_candidate_and_superseded_announcement_audit() -> None:
    assert jecheon.is_jecheon_excluded_candidate(
        Target("ignored", "ignored", jecheon.JECHEON_REVIEW_CANDIDATE_ID)
    )
    assert jecheon.is_jecheon_superseded_announcement_target(
        Target(jecheon.JECHEON_ANNOUNCEMENT_PROVIDER, jecheon.JECHEON_ANNOUNCEMENT_URL)
    )
    assert set(jecheon.JECHEON_CANDIDATE_AUDIT) == {
        "MUNI_IR_DF2CDE484821",
        "MUNI_IR_6AC02AE4EEE2",
        "MUNI_IR_B80A9F0F48BA",
        "MUNI_IR_2C268712AE7E",
    }
    assert all(
        item["decision"].startswith("excluded_")
        for item in jecheon.JECHEON_CANDIDATE_AUDIT.values()
    )


def test_complete_collection_filters_current_and_discards_pii() -> None:
    fixture = HtmlFixture()
    rows, parser, meta = _collect(fixture)

    assert parser == jecheon.JECHEON_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == ["17", "16", "15"]
    assert meta["source_total"] == meta["source_rows"] == 17
    assert meta["declared_pages"] == meta["data_pages"] == 3
    assert meta["required_list_requests"] == meta["list_requests"] == 6
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 2
    assert meta["detail_attempts"] == meta["detail_pages"] == 17
    assert meta["current_source_count"] == meta["returned_count"] == 3
    assert meta["expired_count"] == 14
    assert meta["status_counts"] == {"SCHEDULED": 1, "OPEN": 1, "CLOSED": 1}
    assert meta["application_control_count"] == 1
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""

    scheduled, opened, closed = rows
    assert scheduled["branch"] == "제천시평생학습관"
    assert scheduled["venue_name"] == "평생학습관 강의실3(2층)"
    assert scheduled["status"] == "SCHEDULED"
    assert scheduled["application_url"] == ""
    assert scheduled["reservation_available"] is False
    assert opened["status"] == "OPEN"
    assert opened["application_url"] == jecheon.jecheon_application_url("16")
    assert opened["reservation_available"] is True
    assert opened["capacity_current"] == 12
    assert all(row["target"] == "대상 별도 안내" for row in rows)
    assert all(
        row["raw_fields"]["target_evidence"]
        == "official_detail_omits_target_field"
        for row in rows
    )
    assert closed["status"] == "CLOSED"
    assert closed["capacity_current"] == 16
    assert all(row["program_type"] == "교육" for row in rows)
    assert all(row["municipality_code"] == "4315000000" for row in rows)

    payload = repr(rows)
    for forbidden in (
        "홍길동",
        "043-641-5491",
        "저장하면 안 되는",
        "instructor",
        "contact",
        "attachments",
        "source_html",
    ):
        assert forbidden not in payload
    assert meta["pii_payload_persisted"] is False
    assert "강사" in meta["pii_fields_discarded"]
    assert "문의" in meta["pii_fields_discarded"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_pages": 5}, "max_pages cap"),
        ({"detail_limit": 16}, "detail_limit cap"),
    ],
)
def test_caps_fail_closed_before_partial_collection(kwargs: dict, message: str) -> None:
    fixture = HtmlFixture()
    rows, _parser, meta = jecheon.collect(
        _target(),
        today="2026-07-21",
        timeout=5,
        max_pages=kwargs.get("max_pages", 20),
        detail_limit=kwargs.get("detail_limit", 100),
        session_factory=DummySession,
        fetcher=fixture.fetch,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert message in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_immediate_post_last_page_must_be_empty() -> None:
    fixture = HtmlFixture()
    sentinel_url = jecheon.jecheon_list_url(4)
    fixture.pages[sentinel_url] = _list_html(4, [fixture.courses[0]], fixture.courses)
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "sentinel page is not a stable empty page" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


@pytest.mark.parametrize(("page", "error"), [(1, "first-page"), (3, "last-page")])
def test_first_and_last_boundary_rechecks_must_be_stable(page: int, error: str) -> None:
    fixture = HtmlFixture()
    url = jecheon.jecheon_list_url(page)
    original = fixture.pages[url]
    course = fixture.courses[0] if page == 1 else fixture.courses[-1]
    fixture.overrides[(url, 2)] = original.replace(course.title, course.title + " 변경", 1)
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert error in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_advertised_category_counts_must_match_all_rows() -> None:
    fixture = HtmlFixture()
    page2 = jecheon.jecheon_list_url(2)
    fixture.pages[page2] = fixture.pages[page2].replace(
        "2025년 하반기 프로그램 (14)", "2025년 하반기 프로그램 (13)"
    )
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "advertised catalogue boundary changed" in meta["configured_collection_error"]


def test_duplicate_official_identity_fails_closed() -> None:
    fixture = HtmlFixture()
    page2 = jecheon.jecheon_list_url(2)
    fixture.pages[page2] = fixture.pages[page2].replace("rm_ix=9", "rm_ix=17")
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "duplicate official identities" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_detail_title_mismatch_discards_the_snapshot() -> None:
    fixture = HtmlFixture()
    course = fixture.courses[0]
    fixture.pages[jecheon.jecheon_detail_url(course.identity)] = _detail_html(
        course, title="다른 강좌"
    )
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "list/detail title mismatch" in meta["configured_collection_error"]
    assert meta["detail_errors"] == 1


def test_open_course_bound_handler_without_visible_control_is_actionable() -> None:
    fixture = HtmlFixture()
    course = fixture.courses[1]
    fixture.pages[jecheon.jecheon_detail_url(course.identity)] = _detail_html(
        course,
        control=False,
    )
    rows, _parser, meta = _collect(fixture)

    assert meta["snapshot_complete"] is True
    opened = next(row for row in rows if row["status"] == "OPEN")
    assert opened["application_url"] == jecheon.jecheon_application_url(
        course.identity
    )
    assert opened["reservation_available"] is True
    assert opened["raw_fields"]["application_control_present"] is False
    assert opened["raw_fields"]["application_actionable"] is True
    assert opened["raw_fields"]["application_control_contract"] == (
        "course_bound_handler_without_visible_control"
    )


@pytest.mark.parametrize("failure", ["inactive_with_control", "wrong_handler"])
def test_public_application_state_is_course_bound_and_status_consistent(failure: str) -> None:
    fixture = HtmlFixture()
    if failure == "inactive_with_control":
        course = fixture.courses[0]
        html = _detail_html(course, control=True)
        expected = "inactive course exposes an application control"
    else:
        course = fixture.courses[0]
        html = _detail_html(course, handler_identity="16")
        expected = "application handler identity mismatch"
    fixture.pages[jecheon.jecheon_detail_url(course.identity)] = html
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert expected in meta["configured_collection_error"]


def test_scheduled_status_must_not_lag_behind_application_start() -> None:
    fixture = HtmlFixture()
    course = replace(
        fixture.courses[0], apply_start="2026-07-01", apply_end="2026-08-07"
    )
    fixture.pages[jecheon.jecheon_detail_url(course.identity)] = _detail_html(course)
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "scheduled status/date mismatch" in meta["configured_collection_error"]


def test_dedupe_is_not_allowed_to_reduce_official_identity_cardinality() -> None:
    fixture = HtmlFixture()
    rows, _parser, meta = _collect(fixture, dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]
    assert meta["full_snapshot_validated"] is False


def test_wrong_target_and_invalid_limits_return_no_rows() -> None:
    fixture = HtmlFixture()
    rows, _parser, meta = jecheon.collect(
        _target(provider="WRONG"),
        session_factory=DummySession,
        fetcher=fixture.fetch,
    )
    assert rows == []
    assert "canonical Jecheon owner" in meta["configured_collection_error"]

    rows, _parser, meta = jecheon.collect(
        _target(),
        max_workers=0,
        session_factory=DummySession,
        fetcher=fixture.fetch,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "invalid collection limits" in meta["configured_collection_error"]
