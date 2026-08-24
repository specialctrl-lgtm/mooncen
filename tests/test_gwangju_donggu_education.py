from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import os
from threading import Lock
import time
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_gwangju_donggu as donggu


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    category: str = "기타"
    target: str = "공통"
    start: str = "2025-01-01"
    end: str = "2025-02-01"
    venue: str = "동구 평생학습관"
    method: str = ""
    source_status: str = "접수마감"
    institution: str = "평생학습강좌"
    capacity: int = 30
    fee: str = "(무료) 원"
    schedule: str = "매주 화요일 10시 00분 ~ 12시 00분"


class DummySession:
    def close(self) -> None:
        return None


def _courses() -> list[Course]:
    historical = [
        Course(str(identity), f"동구 과거 강좌 {identity}")
        for identity in range(181, 163, -1)
    ]
    historical.append(
        Course(
            "162",
            "동구 과거 역전기간 강좌",
            start="2023-10-10",
            end="2022-11-22",
        )
    )
    return [
        Course(
            "193",
            "2026 동구 화요 인문대학",
            start="2026-03-24",
            end="2026-07-28",
            venue="동구청 6층 대회의실",
            method="전화접수 방문",
            source_status="수시접수",
            capacity=100,
        ),
        Course(
            "192",
            "2026년 동구 아카데미 연간 일정",
            start="2026-03-13",
            end="2026-12-11",
            venue="동구청 6층 대회의실",
            method="인터넷 전화접수",
            capacity=300,
        ),
        *historical,
    ]


def _options(values: tuple[tuple[str, str], ...]) -> str:
    return "".join(
        f'<option value="{escape(code)}">{escape(label)}</option>'
        for code, label in values
    )


def _search_form(page: int) -> str:
    return f"""
      <form id="srhForm" method="post"
            action="/lecture.es?mid={donggu.GWANGJU_DONGGU_MID}&amp;act=search_list">
        <input type="hidden" name="mid" value="{donggu.GWANGJU_DONGGU_MID}">
        <input type="hidden" name="act" value="search_list">
        <input type="hidden" name="nPage" value="{page}">
        <input type="hidden" name="_csrf" value="fixture-token">
        <select name="organ_cd">{_options(donggu.GWANGJU_DONGGU_INSTITUTIONS)}</select>
        <select name="target_cd">{_options(donggu.GWANGJU_DONGGU_TARGETS)}</select>
        <select name="type_cd">{_options(donggu.GWANGJU_DONGGU_CATEGORIES)}</select>
        <select name="status_cd">{_options(donggu.GWANGJU_DONGGU_STATUSES)}</select>
      </form>
    """


def _list_row(course: Course, page: int) -> str:
    href = donggu.gwangju_donggu_detail_url(course.identity, page)
    return f"""
      <tr>
        <td><a href="{escape(href)}"><span class="lecture-cate">{escape(course.category)}</span>{escape(course.title)}</a></td>
        <td>{escape(course.target)}</td>
        <td>{escape(course.start)} ~ {escape(course.end)}</td>
        <td>{escape(course.venue)}</td>
        <td>{escape(course.method)}</td>
        <td>{escape(course.source_status)}</td>
      </tr>
    """


def _list_html(page: int, rows: list[Course], total: int) -> str:
    broken_last = max(
        1,
        (total + donggu.GWANGJU_DONGGU_BROKEN_COUNTER_PAGE_SIZE - 1)
        // donggu.GWANGJU_DONGGU_BROKEN_COUNTER_PAGE_SIZE,
    )
    body = (
        "".join(_list_row(course, page) for course in rows)
        if rows
        else '<tr><td colspan="6">등록된 자료가 없습니다.</td></tr>'
    )
    headers = "".join(f"<th>{label}</th>" for label in donggu._LIST_HEADERS)
    return f"""
      <html><head><title>프로그램 안내 | 프로그램 : 광주광역시 동구 평생학습도시</title></head>
      <body>
        {_search_form(page)}
        <p class="page_info">전체 {total} 건, 현재 페이지 {page} / {broken_last}</p>
        <table class="tstyle_list">
          <caption>게시판 &gt; 프로그램 목록</caption>
          <thead><tr>{headers}</tr></thead>
          <tbody>{body}</tbody>
        </table>
      </body></html>
    """


def _korean_date(value: str) -> str:
    year, month, day = value.split("-")
    return f"{year}년 {int(month)}월 {int(day)}일"


def _detail_html(
    course: Course,
    page: int,
    *,
    title: str | None = None,
    institution: str | None = None,
    hidden: bool = True,
) -> str:
    style = ' style="display:none;"' if hidden else ""
    return f"""
      <html><head><title>프로그램 안내 | 프로그램 : 광주광역시 동구 평생학습도시</title></head>
      <body>
        <table class="tstyle_view">
          <thead><tr><th colspan="4"><p class="title">[<span class="state02">{escape(course.source_status)}</span>] {escape(title if title is not None else course.title)}</p></th></tr></thead>
          <tbody>
            <tr><th>대상구분</th><td>{escape(course.target)}</td><th>교육구분</th><td>{escape(course.category)}</td></tr>
            <tr><th>접수일자</th><td></td><th>강사</th><td>저장금지 강사 홍길동</td></tr>
            <tr><th>교육기간</th><td>{_korean_date(course.start)} ~ {_korean_date(course.end)}</td><th>교육시간</th><td>{escape(course.schedule)}</td></tr>
            <tr><th>정원</th><td>{course.capacity}명</td><th>수강료</th><td>{escape(course.fee)}</td></tr>
            <tr><th>기관구분</th><td>{escape(institution if institution is not None else course.institution)}</td><th>교육장소</th><td>{escape(course.venue)}</td></tr>
            <tr><th>접수방법</th><td>{escape(course.method)}</td><th>강좌번호</th><td>{escape(course.identity)}</td></tr>
            <tr><th>담당자</th><td>저장금지 담당자</td><th>문의전화</th><td>062-123-4567</td></tr>
            <tr><th colspan="4">강좌소개</th></tr>
            <tr><td colspan="4">저장하면 안 되는 자유 설명 applicant-secret@example.com</td></tr>
            <tr><th colspan="4">주의사항 및 취소 환불 규정</th></tr>
            <tr><td colspan="4">저장하면 안 되는 환불 규정과 개인정보</td></tr>
          </tbody>
        </table>
        <div class="application"{style}>
          <form id="insForm" method="post" action="/lecture.es">
            <input type="hidden" name="mid" value="{donggu.GWANGJU_DONGGU_MID}">
            <input type="hidden" name="act" value="mem_ins">
            <input type="hidden" name="actionUrl" value="/lecture.es">
            <input type="hidden" name="lec_no" value="{escape(course.identity)}">
            <input type="hidden" name="nPage" value="{page}">
            <input type="text" name="mem_nm" value="신청자 실명">
            <input type="email" name="email" value="applicant-secret@example.com">
            <input type="tel" name="phone" value="010-9999-8888">
            <button type="button" onclick="ins_mem_check(); return false;">신청하기</button>
          </form>
        </div>
      </body></html>
    """


class HtmlFixture:
    def __init__(self, courses: list[Course] | None = None) -> None:
        self.courses = list(courses or _courses())
        self.pages: dict[str, str] = {}
        last = max(
            1,
            (len(self.courses) + donggu.GWANGJU_DONGGU_PAGE_SIZE - 1)
            // donggu.GWANGJU_DONGGU_PAGE_SIZE,
        )
        for page in range(1, last + 1):
            start = (page - 1) * donggu.GWANGJU_DONGGU_PAGE_SIZE
            rows = self.courses[start : start + donggu.GWANGJU_DONGGU_PAGE_SIZE]
            self.pages[donggu.gwangju_donggu_list_url(page)] = _list_html(
                page, rows, len(self.courses)
            )
        self.pages[donggu.gwangju_donggu_list_url(last + 1)] = _list_html(
            last + 1, [], len(self.courses)
        )
        for page in range(1, last + 1):
            start = (page - 1) * donggu.GWANGJU_DONGGU_PAGE_SIZE
            for course in self.courses[
                start : start + donggu.GWANGJU_DONGGU_PAGE_SIZE
            ]:
                self.pages[
                    donggu.gwangju_donggu_detail_url(course.identity, page)
                ] = _detail_html(course, page)
        self.overrides: dict[tuple[str, int], str] = {}
        self.failures: Counter[str] = Counter()
        self.calls: Counter[str] = Counter()
        self.active = 0
        self.max_active = 0
        self.lock = Lock()

    def fetch(self, _session: DummySession, url: str, _timeout: int) -> str:
        with self.lock:
            self.calls[url] += 1
            call = self.calls[url]
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            should_fail = self.failures[url] > 0
            if should_fail:
                self.failures[url] -= 1
        try:
            time.sleep(0.002)
            if should_fail:
                raise RuntimeError("fixture transient failure")
            override = self.overrides.get((url, call))
            if override is not None:
                return override
            if url not in self.pages:
                raise RuntimeError(f"unexpected URL: {url}")
            return self.pages[url]
        finally:
            with self.lock:
                self.active -= 1


def _target(**changes: str) -> Target:
    values = {
        "provider": donggu.GWANGJU_DONGGU_PROVIDER,
        "url": donggu.GWANGJU_DONGGU_CANONICAL_URL,
        "candidate_id": donggu.GWANGJU_DONGGU_CANONICAL_CANDIDATE_ID,
    }
    values.update(changes)
    return Target(**values)


def _collect(fixture: HtmlFixture, **kwargs):
    return donggu.collect(
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


def test_constants_urls_target_and_candidate_audit() -> None:
    assert donggu.GWANGJU_DONGGU_PROVIDER == "MUNI_WWW_DONGGU_KR_4B011833"
    assert donggu.GWANGJU_DONGGU_MUNICIPALITY_CODE == "1221000000"
    assert donggu.GWANGJU_DONGGU_MUNICIPALITY_NAME == "전남광주통합특별시 동구"
    assert donggu.gwangju_donggu_list_url(1) == donggu.GWANGJU_DONGGU_CANONICAL_URL
    assert parse_qs(urlparse(donggu.gwangju_donggu_list_url(7)).query) == {
        "mid": [donggu.GWANGJU_DONGGU_MID],
        "act": ["search_list"],
        "nPage": ["7"],
    }
    assert parse_qs(urlparse(donggu.gwangju_donggu_detail_url("193", 2)).query) == {
        "mid": [donggu.GWANGJU_DONGGU_MID],
        "act": ["view"],
        "return_act": ["search_list"],
        "lec_no": ["193"],
        "nPage": ["2"],
    }
    assert donggu.is_target(_target())
    assert donggu.is_target(_target(url=donggu.GWANGJU_DONGGU_EXISTING_DETAIL_URL))
    assert not donggu.is_target(_target(provider="WRONG"))
    assert not donggu.is_target(_target(url=donggu.GWANGJU_DONGGU_CANONICAL_URL + "#top"))
    assert donggu.is_gwangju_donggu_candidate_alias(
        Target("ignored", "ignored", donggu.GWANGJU_DONGGU_LANDING_CANDIDATE_ID)
    )
    assert donggu.is_gwangju_donggu_candidate_alias(
        Target("ignored", donggu.GWANGJU_DONGGU_LIBRARY_DATASET_URL)
    )
    decisions = {
        key: value["decision"]
        for key, value in donggu.GWANGJU_DONGGU_CANDIDATE_AUDIT.items()
    }
    assert decisions[donggu.GWANGJU_DONGGU_CANONICAL_CANDIDATE_ID].startswith(
        "include_existing_owner"
    )
    assert decisions[donggu.GWANGJU_DONGGU_LANDING_CANDIDATE_ID].startswith(
        "exclude_generic_city_landing"
    )
    assert "exclude_external_library_dataset" in decisions[
        "SEARCH_RESULT_DATA_GO_KR_15120373"
    ]
    with pytest.raises(ValueError):
        donggu.gwangju_donggu_detail_url("../193", 1)


def test_complete_collection_uses_broken_counter_sentinel_and_discards_pii() -> None:
    fixture = HtmlFixture()
    rows, parser, meta = _collect(fixture)

    assert parser == donggu.GWANGJU_DONGGU_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == ["193", "192"]
    assert meta["declared_source_rows"] == meta["source_rows"] == 21
    assert meta["source_counter_last_page"] == 2
    assert meta["source_counter_defect_verified"] is True
    assert meta["derived_data_pages"] == meta["data_pages"] == 3
    assert meta["required_list_requests"] == meta["list_requests"] == 6
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 2
    assert meta["current_source_count"] == meta["returned_count"] == 2
    assert meta["expired_count"] == 19
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["pages"] == 8
    assert meta["historical_reversed_period_count"] == 1
    assert meta["status_counts"] == {"OPEN": 1, "CLOSED": 1}
    assert meta["institution_counts"] == {"평생학습강좌": 2}
    assert meta["branch_counts"] == {"전남광주통합특별시 동구 / 평생학습강좌": 2}
    assert meta["visible_online_application_control_count"] == 0
    assert meta["offline_open_count"] == 1
    assert meta["semantic_duplicate_group_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""
    assert fixture.max_active <= donggu.GWANGJU_DONGGU_MAX_WORKERS

    opened, closed = rows
    assert (
        opened["branch"]
        == closed["branch"]
        == "전남광주통합특별시 동구 / 평생학습강좌"
    )
    assert opened["venue"] == "동구청 6층 대회의실"
    assert opened["status"] == "OPEN"
    assert opened["application_type"] == "OFFLINE_APPLY"
    assert opened["reservation_available"] is False
    assert opened["application_url"] == ""
    assert opened["application_method"] == "전화 / 방문"
    assert closed["status"] == "CLOSED"
    assert closed["application_type"] == "INFO_ONLY"
    assert closed["reservation_available"] is False
    assert closed["fee_amount"] == 0
    assert all(row["program_type"] == "교육" for row in rows)
    assert all(row["municipality_code"] == "1221000000" for row in rows)

    payload = repr(rows)
    for forbidden in (
        "홍길동",
        "저장금지",
        "062-123-4567",
        "010-9999-8888",
        "applicant-secret@example.com",
        "instructor",
        "manager",
        "contact",
        "source_html",
    ):
        assert forbidden not in payload
    assert meta["pii_payload_persisted"] is False


@pytest.mark.parametrize(
    ("taxonomy", "old", "new", "message"),
    [
        ("organ", "기관분류", "시설분류", "institution taxonomy changed"),
        ("target", "대상분류", "이용대상", "target taxonomy changed"),
        ("category", "강좌분야", "분야", "course-category taxonomy changed"),
        ("status", "접수상태", "신청상태", "reception-status taxonomy changed"),
    ],
)
def test_search_taxonomies_are_exact(
    taxonomy: str, old: str, new: str, message: str
) -> None:
    del taxonomy
    fixture = HtmlFixture()
    first = donggu.gwangju_donggu_list_url(1)
    fixture.pages[first] = fixture.pages[first].replace(old, new, 1)
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_immediate_post_last_page_must_be_empty() -> None:
    fixture = HtmlFixture()
    sentinel = donggu.gwangju_donggu_list_url(4)
    fixture.pages[sentinel] = _list_html(4, [fixture.courses[0]], 21)
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "immediate post-last sentinel is not stable empty" in meta[
        "configured_collection_error"
    ]
    assert meta["detail_attempts"] == 0


@pytest.mark.parametrize(("page", "message"), [(1, "first-page"), (3, "last-page")])
def test_first_and_last_boundaries_must_be_stable(page: int, message: str) -> None:
    fixture = HtmlFixture()
    url = donggu.gwangju_donggu_list_url(page)
    course = fixture.courses[(page - 1) * donggu.GWANGJU_DONGGU_PAGE_SIZE]
    fixture.overrides[(url, 2)] = fixture.pages[url].replace(
        course.title, course.title + " 변경", 1
    )
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_duplicate_identity_and_unexpected_reversed_period_fail_closed() -> None:
    fixture = HtmlFixture()
    page2 = donggu.gwangju_donggu_list_url(2)
    fixture.pages[page2] = fixture.pages[page2].replace("lec_no=173", "lec_no=193")
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "duplicate official identities" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0

    fixture = HtmlFixture()
    page1 = donggu.gwangju_donggu_list_url(1)
    fixture.pages[page1] = fixture.pages[page1].replace(
        "2025-01-01 ~ 2025-02-01", "2025-12-01 ~ 2025-01-01", 1
    )
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "unexpected reversed education period" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("title", "list/detail title mismatch"),
        ("institution", "unknown institution branch"),
        ("visible_offline", "inactive/offline course exposes the applicant form"),
        ("hidden_online", "internet-open course has only a hidden application template"),
    ],
)
def test_detail_and_public_application_contracts_fail_closed(
    mutation: str, message: str
) -> None:
    fixture = HtmlFixture()
    course = fixture.courses[0]
    detail_url = donggu.gwangju_donggu_detail_url(course.identity, 1)
    if mutation == "title":
        fixture.pages[detail_url] = _detail_html(course, 1, title="다른 강좌")
    elif mutation == "institution":
        fixture.pages[detail_url] = _detail_html(course, 1, institution="임의기관")
    elif mutation == "visible_offline":
        fixture.pages[detail_url] = _detail_html(course, 1, hidden=False)
    else:
        online = replace(course, method="인터넷 전화접수 방문")
        first = donggu.gwangju_donggu_list_url(1)
        fixture.pages[first] = fixture.pages[first].replace(
            "전화접수 방문", "인터넷 전화접수 방문", 1
        )
        fixture.pages[detail_url] = _detail_html(online, 1, hidden=True)
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["detail_errors"] == 1


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "message"),
    [(5, 100, "max_pages cap"), (20, 1, "detail_limit cap")],
)
def test_caps_fail_before_partial_snapshot(
    max_pages: int, detail_limit: int, message: str
) -> None:
    fixture = HtmlFixture()
    rows, _parser, meta = donggu.collect(
        _target(),
        today="2026-07-21",
        max_pages=max_pages,
        detail_limit=detail_limit,
        session_factory=DummySession,
        fetcher=fixture.fetch,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert message in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_bounded_retries_and_dedupe_cardinality() -> None:
    fixture = HtmlFixture()
    page2 = donggu.gwangju_donggu_list_url(2)
    fixture.failures[page2] = 2
    rows, _parser, meta = _collect(fixture)
    assert len(rows) == 2
    assert fixture.calls[page2] >= 3
    assert meta["snapshot_complete"] is True

    fixture = HtmlFixture()
    detail = donggu.gwangju_donggu_detail_url("193", 1)
    fixture.failures[detail] = 3
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert fixture.calls[detail] == donggu.GWANGJU_DONGGU_FETCH_ATTEMPTS
    assert "fixture transient failure" in meta["configured_collection_error"]

    fixture = HtmlFixture()
    rows, _parser, meta = _collect(
        fixture, dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta[
        "configured_collection_error"
    ]
    assert meta["full_snapshot_validated"] is False


def test_wrong_target_and_invalid_limits_return_no_rows() -> None:
    fixture = HtmlFixture()
    rows, _parser, meta = donggu.collect(
        _target(provider="WRONG"),
        session_factory=DummySession,
        fetcher=fixture.fetch,
    )
    assert rows == []
    assert "canonical Gwangju Dong-gu course owner" in meta[
        "configured_collection_error"
    ]

    rows, _parser, meta = donggu.collect(
        _target(),
        max_workers=0,
        session_factory=DummySession,
        fetcher=fixture.fetch,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "invalid collection limits" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("GWANGJU_DONGGU_LIVE_TEST") != "1",
    reason="set GWANGJU_DONGGU_LIVE_TEST=1 for official live audit",
)
def test_live_official_catalogue_audit_2026_07_21() -> None:
    rows, _parser, meta = donggu.collect(
        _target(),
        today="2026-07-21",
        timeout=30,
        max_pages=30,
        detail_limit=100,
    )
    assert meta["configured_collection_error"] == ""
    assert meta["declared_source_rows"] == meta["source_rows"] == 38
    assert meta["source_counter_last_page"] == 2
    assert meta["source_counter_defect_verified"] is True
    assert meta["derived_data_pages"] == meta["data_pages"] == 4
    assert meta["required_list_requests"] == meta["list_requests"] == 7
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 2
    assert meta["historical_reversed_period_count"] == 1
    assert meta["current_source_count"] == meta["detail_pages"] == len(rows) == 2
    assert [row["raw_fields"]["identity"] for row in rows] == ["193", "192"]
    assert meta["status_counts"] == {"OPEN": 1, "CLOSED": 1}
    assert meta["institution_counts"] == {"평생학습강좌": 2}
    assert meta["visible_online_application_control_count"] == 0
    assert meta["offline_open_count"] == 1
    assert meta["semantic_duplicate_group_count"] == 0
    assert meta["pages"] == 9
    assert meta["full_snapshot_validated"] is True
