from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import os
from threading import Lock
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from Crawler import municipal_jangheung as jangheung


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    row_number: int
    title: str
    category: str = "문화예술"
    source_status: str = "접수중"
    start: str = "2026-07-21"
    end: str = "2026-08-20"
    apply_start: str = "2026-07-01 09:00"
    apply_end: str = "2026-08-19 18:00"
    institution: str = "장흥군 평생학습관"
    target: str = "장흥군민"
    venue: str = "장흥군 평생학습관 교육실"
    fee: int = 0
    capacity_current: int = 3
    wait_current: int = 0
    capacity_total: int = 20
    capacity_wait_total: int = 5


class DummySession:
    def close(self) -> None:
        return None


def _courses() -> list[Course]:
    current = [
        Course("113", 13, "장흥 생활도예"),
        Course(
            "112",
            12,
            "장흥 기초문해",
            category="기초문해",
            source_status="접수대기",
            start="2026-08-01",
            end="2026-10-31",
            institution="장흥읍 주민자치센터",
            venue="장흥읍 주민자치센터",
        ),
        Course(
            "111",
            11,
            "장흥 시민 인문학",
            category="인문교양",
            source_status="수강중",
            start="2026-06-01",
            end="2026-09-30",
            institution="장흥군 평생학습관",
        ),
        Course(
            "110",
            10,
            "장흥 직업 역량",
            category="직업능력",
            source_status="수강확정",
            start="2026-08-05",
            end="2026-09-05",
            institution="대덕읍 주민자치센터",
            venue="대덕읍 주민자치센터",
            fee=10000,
        ),
        Course(
            "109",
            9,
            "장흥 시민참여 교실",
            category="시민참여",
            source_status="폐강",
            start="2026-08-10",
            end="2026-09-10",
            institution="관산읍 주민자치센터",
            venue="관산읍 주민자치센터",
        ),
    ]
    historical = [
        Course(
            str(identity),
            row_number,
            f"장흥 과거 강좌 {identity}",
            category="학력보완" if row_number % 2 else "문화예술",
            source_status="수강종료",
            start="2025-01-01",
            end="2025-02-01",
            apply_start="2024-12-01 09:00",
            apply_end="2024-12-31 18:00",
        )
        for identity, row_number in zip(range(108, 100, -1), range(8, 0, -1))
    ]
    return [*current, *historical]


def _options(values: tuple[tuple[str, str], ...]) -> str:
    return "".join(
        f'<option value="{escape(code, quote=True)}">{escape(label)}</option>'
        for code, label in values
    )


def _list_form() -> str:
    return f"""
      <form id="list_search" class="list_sch2" action="{jangheung.JANGHEUNG_PATH}">
        <input type="hidden" name="csrf_token" value="{'a' * 64}">
        <fieldset class="srch">
          <select name="search_status" id="search_status">{_options(jangheung.JANGHEUNG_SEARCH_STATUSES)}</select>
          <select name="search_type" id="search_type">{_options(jangheung.JANGHEUNG_SEARCH_TYPES)}</select>
          <input type="text" id="search_word" name="search_word" value="">
          <input type="text" id="search_startdate" name="search_startdate" class="onlydate" value="">
          <input type="text" id="search_enddate" name="search_enddate" class="onlydate" value="">
          <input type="submit" value="검색">
        </fieldset>
      </form>
    """


def _category_list(courses: list[Course]) -> str:
    counts = Counter(course.category for course in courses)
    values = [f'<li class="first on">전체<span>({len(courses)})</span></li>']
    for category in jangheung.JANGHEUNG_CATEGORIES:
        href = jangheung.JANGHEUNG_PATH + "?" + urlencode(
            (("category_1", category), ("search_status", "all"))
        )
        values.append(
            f'<li><a href="{escape(href, quote=True)}">{category}<span>({counts[category]})</span></a></li>'
        )
    return '<ul class="cate_list">' + "".join(values) + "</ul>"


def _status_markup(course: Course) -> str:
    class_map = {
        "접수대기": "bt1",
        "접수중": "bt2",
        "수강대기": "bt3",
        "수강중": "bt3",
        "수강종료": "bt4",
        "강의종료": "bt4",
        "폐강": "bt6",
        "수강확정": "bt5",
    }
    css = class_map[course.source_status]
    if course.source_status == "접수중":
        return (
            f'<a class="s_bt {css}" href="{escape(jangheung.jangheung_application_url(course.identity), quote=True)}">'
            f"{course.source_status} 접수하기</a>"
        )
    return f'<span class="s_bt {css}">{course.source_status}</span>'


def _list_row(course: Course) -> str:
    fee = f"{course.fee:,} 원"
    return f"""
      <tr>
        <td>{course.row_number}</td>
        <td>{course.category}</td>
        <td class="lecture_title"><a href="{escape(jangheung.jangheung_detail_url(course.identity), quote=True)}">
          <span class="fc_blue3">{escape(course.title)}</span>
          <span>강 사 명 : 저장금지 강사 010-7777-8888</span>
          <span>신청기간 : {course.apply_start} ~ {course.apply_end}</span>
          <span>교육기간 : {course.start} ~ {course.end}</span>
        </a></td>
        <td><div>선착순</div><span class="apply">{course.capacity_current}</span>
          <span class="wait">({course.wait_current})</span> /
          <span class="fix_poeple">{course.capacity_total}명</span></td>
        <td>{fee}</td>
        <td class="btn_style">{_status_markup(course)}</td>
      </tr>
    """


def _paginator(page: int, source_pages: int, *, sentinel: bool) -> str:
    if sentinel:
        boundary = jangheung.JANGHEUNG_PATH + "?" + urlencode(
            (("page", source_pages), ("search_status", "all"))
        )
        body = (
            f'<a href="{escape(boundary, quote=True)}" class="first">&lt;&lt;</a>'
            f'<a href="{escape(boundary, quote=True)}" title="{source_pages} 페이지">{source_pages}</a>'
        )
    else:
        body = f'<a class="on">{page}</a>'
        if page < source_pages:
            last = jangheung.JANGHEUNG_PATH + "?" + urlencode(
                (("page", source_pages), ("search_status", "all"))
            )
            body += f'<a href="{escape(last, quote=True)}" class="last">&gt;&gt;</a>'
    return f'<div class="list_paging"><div class="num">{body}</div></div>'


def _list_html(
    page: int,
    page_courses: list[Course],
    all_courses: list[Course],
    source_pages: int,
    *,
    sentinel: bool = False,
) -> str:
    title = (
        f"{page} 페이지 목록보기 &lt; 강좌신청 &lt; 강좌정보 - 장흥군 평생교육"
        if sentinel
        else f"{page} 페이지 목록보기 &lt; (전체) &lt; 강좌신청 &lt; 강좌정보 - 장흥군 평생교육"
    )
    body = (
        "".join(_list_row(course) for course in page_courses)
        if page_courses
        else '<tr><td colspan="6">개설된 강좌가 없습니다.</td></tr>'
    )
    return f"""
      <html><head><title>{title}</title></head><body>
        {_list_form()}
        {_category_list(all_courses)}
        <table class="list_table">
          <caption>이표는 강좌관리목록로 6컬럼, {len(page_courses)}로우로 구성되어 있습니다. 각 로우는 번호, 분류, 강좌명, 신청기간으로 이루어져 있습니다."</caption>
          <thead><tr>{''.join(f'<th>{value}</th>' for value in jangheung._LIST_HEADERS)}</tr></thead>
          <tbody>{body}</tbody>
        </table>
        {_paginator(page, source_pages, sentinel=sentinel)}
      </body></html>
    """


def _detail_html(
    course: Course,
    *,
    detail_title: str | None = None,
    detail_category: str | None = None,
    detail_institution: str | None = None,
    detail_period: str | None = None,
    detail_fee: int | None = None,
    detail_capacity: int | None = None,
    expose_control: bool | None = None,
) -> str:
    control = course.source_status == "접수중" if expose_control is None else expose_control
    application = (
        f'<div class="mat30"><a href="{escape(jangheung.jangheung_application_url(course.identity), quote=True)}" '
        'class="s_bt bt1">강사신청</a></div>'
        if control
        else ""
    )
    institution = course.institution if detail_institution is None else detail_institution
    fee = course.fee if detail_fee is None else detail_fee
    capacity = course.capacity_total if detail_capacity is None else detail_capacity
    return f"""
      <html><head><title>{escape(detail_title or course.title)} &lt; 강좌신청 &lt; 강좌정보 - 장흥군 평생교육</title></head>
      <body><div id="content">
        {application}
        <h3>강좌정보</h3>
        <table class="view_table">
          <caption>강좌접수 상세내용으로 강좌명, 기수, 교육기관, 문의전화, 강좌소개로 구성</caption>
          <tbody>
            <tr><th>분류</th><td>{escape(detail_category or course.category)}</td></tr>
            <tr><th>강좌명(기수)</th><td>{escape(detail_title or course.title)}</td></tr>
            <tr><th>교육정보</th><td>{escape(institution)}</td><th>교육대상</th><td>{escape(course.target)}</td></tr>
            <tr><th>수강료</th><td>{fee:,} 원</td><th>문의전화</th><td>061-860-1234 private@example.com</td></tr>
            <tr><th>신청기간</th><td>{course.apply_start} ~ {course.apply_end}</td>
                <th>교육기간</th><td>{detail_period or f'{course.start} ~ {course.end}'}</td></tr>
            <tr><th>강사명</th><td>저장금지 강사</td><th>교육장소</th><td>{escape(course.venue)}</td></tr>
            <tr><th>모집정원</th><td>{capacity} 명</td><th>모집대기인원</th><td>{course.capacity_wait_total} 명</td></tr>
            <tr><th>강좌소개</th><td>저장하면 안 되는 자유 본문 010-9999-8888</td></tr>
          </tbody>
        </table>
        <div class="list_btn"><ul class="clear"><li>
          <a class="m_bt bt6" href="{jangheung.JANGHEUNG_PATH}?page=" id="btn_list" title="목록">목록</a>
        </li></ul></div>
      </div></body></html>
    """


class FixtureSite:
    def __init__(self, courses: list[Course] | None = None, *, page_unit: int = 5) -> None:
        self.courses = sorted(
            list(_courses() if courses is None else courses),
            key=lambda course: course.row_number,
            reverse=True,
        )
        self.page_unit = page_unit
        self.calls: list[str] = []
        self.failures: Counter[str] = Counter()
        self.permanent_failure = ""
        self.mutator = None
        self._lock = Lock()

    def session_factory(self) -> DummySession:
        return DummySession()

    def fetch(self, _session: DummySession, url: str, _timeout: int) -> str:
        with self._lock:
            self.calls.append(url)
            call_number = self.calls.count(url)
            if self.permanent_failure and self.permanent_failure in url:
                raise OSError("fixture permanent failure")
            if self.failures[url] > 0:
                self.failures[url] -= 1
                raise OSError("fixture transient failure")
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path != jangheung.JANGHEUNG_PATH:
            raise AssertionError(f"unexpected path: {url}")
        if query.get("mode") == ["view"]:
            identity = query["idx"][0]
            course = next(course for course in self.courses if course.identity == identity)
            html = _detail_html(course)
        else:
            page = int(query["page"][0])
            source_pages = max(1, (len(self.courses) + self.page_unit - 1) // self.page_unit)
            sentinel = page == source_pages + 1
            page_courses = (
                []
                if sentinel
                else self.courses[(page - 1) * self.page_unit : page * self.page_unit]
            )
            html = _list_html(
                page,
                page_courses,
                self.courses,
                source_pages,
                sentinel=sentinel,
            )
        if self.mutator is not None:
            html = self.mutator(url, html, call_number)
        return html


def _target() -> Target:
    return Target(jangheung.JANGHEUNG_PROVIDER, jangheung.JANGHEUNG_URL)


def _collect(site: FixtureSite, **kwargs):
    return jangheung.collect_jangheung_education(
        _target(),
        today="2026-07-21",
        session_factory=site.session_factory,
        fetcher=site.fetch,
        **kwargs,
    )


def test_target_helpers_candidate_rollup_and_source_boundaries() -> None:
    assert jangheung.JANGHEUNG_MUNICIPALITY_CODE == "1277000000"
    assert jangheung.JANGHEUNG_MUNICIPALITY_NAME == "전남광주통합특별시 장흥군"
    assert jangheung.is_jangheung_education_target(_target())
    assert not jangheung.is_jangheung_education_target(
        Target(jangheung.JANGHEUNG_DUPLICATE_LANDING_PROVIDER, jangheung.JANGHEUNG_ROOT_URL)
    )
    assert not jangheung.is_jangheung_education_target(
        Target(jangheung.JANGHEUNG_PROVIDER, jangheung.JANGHEUNG_URL + "?page=1")
    )
    assert jangheung.jangheung_list_url(2).endswith("page=2&search_status=all")
    assert jangheung.jangheung_detail_url("12").endswith("idx=12&mode=view")
    assert jangheung.jangheung_application_url("12").endswith(
        "lecture_idx=12&mode=reserve_form2"
    )
    assert jangheung.is_jangheung_candidate_alias(
        Target("", "http://www.jangheung.go.kr/lifelong", jangheung.JANGHEUNG_CANDIDATE_ID)
    )
    audit = jangheung.JANGHEUNG_CANDIDATE_AUDIT
    assert audit[jangheung.JANGHEUNG_CANDIDATE_ID]["owner"] == jangheung.JANGHEUNG_PROVIDER
    assert audit[jangheung.JANGHEUNG_DUPLICATE_OWNER_AUDIT_ID]["decision"].startswith(
        "exclude_duplicate"
    )
    assert audit[jangheung.JANGHEUNG_SCHEDULE_AUDIT_ID]["decision"].startswith(
        "exclude_independent"
    )
    assert audit[jangheung.JANGHEUNG_EDUCATION_SUPPORT_AUDIT_ID]["owner"] == (
        "jangheung_education_support_office"
    )


def test_complete_snapshot_all_pages_exact_branches_controls_and_pii_no_store() -> None:
    rows, parser, meta = _collect(FixtureSite())
    assert parser == jangheung.JANGHEUNG_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == [
        "113",
        "112",
        "111",
        "110",
        "109",
    ]
    assert meta["source_total"] == meta["source_rows"] == 13
    assert meta["data_pages"] == 3
    assert meta["page_counts"] == {1: 5, 2: 5, 3: 3}
    assert meta["list_requests"] == meta["required_list_requests"] == 6
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 2
    assert meta["current_source_count"] == 5
    assert meta["expired_count"] == 8
    assert meta["detail_attempts"] == meta["detail_pages"] == 5
    assert meta["pages"] == 11
    assert meta["source_application_control_count"] == 1
    assert meta["visible_public_application_control_count"] == 1
    assert meta["status_counts"] == {
        "OPEN": 1,
        "SCHEDULED": 1,
        "CLOSED": 2,
        "CANCELLED": 1,
    }
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert rows[0]["branch"] == (
        "전남광주통합특별시 장흥군 / 장흥군 평생학습관"
    )
    assert rows[1]["branch"] == (
        "전남광주통합특별시 장흥군 / 장흥읍 주민자치센터"
    )
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_url"] == jangheung.jangheung_application_url("113")
    assert all(not row["reservation_available"] for row in rows[1:])
    assert {row["municipality_code"] for row in rows} == {"1277000000"}
    assert {row["program_type"] for row in rows} == {"교육"}
    payload = repr(rows)
    for forbidden in (
        "010-7777-8888",
        "061-860-1234",
        "private@example.com",
        "저장금지 강사",
        "저장하면 안 되는 자유 본문",
    ):
        assert forbidden not in payload
    assert all(row["description"] == row["title"] for row in rows)
    assert all(
        set(row["raw_fields"]) <= jangheung._SAFE_RAW_FIELDS  # noqa: SLF001
        for row in rows
    )


@pytest.mark.parametrize(
    ("needle", "replacement", "expected"),
    [
        ('<option value="16">수강종료</option>', '<option value="16">종료</option>', "status taxonomy changed"),
        ('<option value="institute">교육기관</option>', '<option value="agency">교육기관</option>', "search taxonomy changed"),
        ('name="csrf_token" value="' + 'a' * 64 + '"', 'name="csrf_token" value="bad"', "CSRF field changed"),
        ('id="search_startdate" name="search_startdate" class="onlydate" value=""', 'id="search_startdate" name="search_startdate" class="onlydate" value="2026-01-01"', "empty all-history search changed"),
    ],
)
def test_search_form_contract_changes_fail_closed(
    needle: str, replacement: str, expected: str
) -> None:
    site = FixtureSite()
    site.mutator = lambda _url, html, _call: html.replace(needle, replacement)
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert expected in meta["configured_collection_error"]


def test_both_official_list_title_variants_keep_the_same_course_contract() -> None:
    site = FixtureSite()
    site.mutator = lambda _url, html, _call: html.replace(
        "&lt; (전체) &lt; 강좌신청", "&lt; 강좌신청"
    )

    rows, _parser, meta = _collect(site)

    assert len(rows) == 5
    assert meta["source_rows"] == meta["source_total"] == 13
    assert meta["pagination_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""


def test_unrecognised_list_title_still_fails_closed() -> None:
    site = FixtureSite()
    site.mutator = lambda _url, html, _call: html.replace(
        "장흥군 평생교육</title>", "장흥군 공지사항</title>"
    )

    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "official list title changed" in meta["configured_collection_error"]


def test_category_totals_and_links_must_reconcile() -> None:
    site = FixtureSite()
    site.mutator = lambda _url, html, _call: html.replace(
        "문화예술<span>(5)</span>", "문화예술<span>(6)</span>"
    )
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "category totals do not reconcile" in meta["configured_collection_error"]

    site = FixtureSite()
    site.mutator = lambda _url, html, _call: html.replace(
        "category_1=%EB%AC%B8%ED%99%94%EC%98%88%EC%88%A0",
        "category_1=%EA%B8%B0%ED%83%80",
    )
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "category taxonomy changed" in meta["configured_collection_error"]


def test_sentinel_must_be_explicit_empty_with_last_boundary_link() -> None:
    site = FixtureSite()

    def mutate(url: str, html: str, _call: int) -> str:
        query = parse_qs(urlparse(url).query)
        if query.get("page") == ["4"]:
            return html.replace('title="3 페이지"', 'title="2 페이지"')
        return html

    site.mutator = mutate
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["pagination_complete"] is False
    assert "sentinel boundary link changed" in meta["configured_collection_error"]


def test_first_and_last_page_stability_rechecks_fail_closed() -> None:
    site = FixtureSite()

    def mutate(url: str, html: str, call: int) -> str:
        if parse_qs(urlparse(url).query).get("page") == ["1"] and call == 2:
            return html.replace("장흥 생활도예", "변경된 생활도예")
        return html

    site.mutator = mutate
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "first-page stability recheck changed" in meta["configured_collection_error"]


def test_identity_and_row_number_duplicates_fail_closed() -> None:
    courses = _courses()
    courses[1] = replace(courses[1], identity=courses[0].identity)
    rows, _parser, meta = _collect(FixtureSite(courses))
    assert rows == []
    assert meta["identity_duplicate_count"] == 1
    assert "duplicate official identities" in meta["configured_collection_error"]

    courses = _courses()
    courses[1] = replace(courses[1], row_number=courses[0].row_number)
    rows, _parser, meta = _collect(FixtureSite(courses))
    assert rows == []
    assert meta["row_number_duplicate_count"] == 1
    assert "row numbers do not reconcile" in meta["configured_collection_error"]


def test_current_semantic_duplicate_fails_after_exact_detail_branches() -> None:
    courses = _courses()
    courses[1] = replace(
        courses[1],
        title=courses[0].title,
        category=courses[0].category,
        start=courses[0].start,
        end=courses[0].end,
        institution=courses[0].institution,
        venue=courses[0].venue,
    )
    rows, _parser, meta = _collect(FixtureSite(courses))
    assert rows == []
    assert meta["semantic_duplicate_group_count"] == 1
    assert "current semantic duplicate groups" in meta["configured_collection_error"]


def test_historical_semantic_duplicates_are_reported_not_published() -> None:
    courses = _courses()
    courses[-1] = replace(
        courses[-1],
        title=courses[-2].title,
        category=courses[-2].category,
        start=courses[-2].start,
        end=courses[-2].end,
    )
    rows, _parser, meta = _collect(FixtureSite(courses))
    assert len(rows) == 5
    assert meta["historical_semantic_duplicate_group_count"] == 1
    assert meta["snapshot_complete"] is True


def test_reversed_period_status_class_and_application_binding_fail_closed() -> None:
    courses = _courses()
    courses[0] = replace(courses[0], start="2026-09-01", end="2026-08-01")
    rows, _parser, meta = _collect(FixtureSite(courses))
    assert rows == []
    assert "reversed education period" in meta["configured_collection_error"]

    site = FixtureSite()
    site.mutator = lambda _url, html, _call: html.replace(
        'class="s_bt bt2"', 'class="s_bt unknown"'
    )
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "status class changed" in meta["configured_collection_error"]

    site = FixtureSite()
    site.mutator = lambda _url, html, _call: html.replace(
        "lecture_idx=113&amp;mode=reserve_form2",
        "lecture_idx=999&amp;mode=reserve_form2",
    )
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "open row lacks one identity-bound" in meta["configured_collection_error"]


def test_inactive_list_row_cannot_expose_application_control() -> None:
    site = FixtureSite()

    def mutate(_url: str, html: str, _call: int) -> str:
        return html.replace(
            '<span class="s_bt bt3">수강중</span>',
            '<a class="s_bt bt3" href="/lifelong/course/apply?lecture_idx=111&amp;mode=reserve_form2">수강중</a>',
        )

    site.mutator = mutate
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "inactive row exposes an application control" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("identity", "needle", "replacement", "expected"),
    [
        ("113", "장흥 생활도예", "상세 제목 변조", "official detail title changed"),
        ("113", "<td>문화예술</td>", "<td>기초문해</td>", "safe fields mismatch"),
        ("113", "<td>장흥군 평생학습관</td>", "<td></td>", "safe institution changed"),
        ("113", "2026-07-21 ~ 2026-08-20", "2026-07-22 ~ 2026-08-20", "safe fields mismatch"),
        ("110", "10,000 원", "20,000 원", "safe fields mismatch"),
        ("113", "<td>20 명</td>", "<td>21 명</td>", "safe fields mismatch"),
    ],
)
def test_detail_identity_branch_and_safe_values_fail_closed(
    identity: str, needle: str, replacement: str, expected: str
) -> None:
    site = FixtureSite()

    def mutate(url: str, html: str, _call: int) -> str:
        query = parse_qs(urlparse(url).query)
        if query.get("idx") == [identity]:
            return html.replace(needle, replacement, 1)
        return html

    site.mutator = mutate
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["detail_errors"] >= 1
    assert expected in meta["configured_collection_error"]


def test_detail_schema_and_controls_fail_closed_without_persisting_unsafe_cells() -> None:
    site = FixtureSite()

    def schema_change(url: str, html: str, _call: int) -> str:
        if parse_qs(urlparse(url).query).get("idx") == ["113"]:
            return html.replace("<th>강사명</th>", "<th>강사</th>", 1)
        return html

    site.mutator = schema_change
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "detail schema changed" in meta["configured_collection_error"]

    site = FixtureSite()

    def remove_open(url: str, html: str, _call: int) -> str:
        if parse_qs(urlparse(url).query).get("idx") == ["113"]:
            start = html.index('<div class="mat30">')
            finish = html.index("</div>", start) + len("</div>")
            return html[:start] + html[finish:]
        return html

    site.mutator = remove_open
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "detail/list application control mismatch" in meta["configured_collection_error"]

    site = FixtureSite()

    def expose_closed(url: str, html: str, _call: int) -> str:
        if parse_qs(urlparse(url).query).get("idx") == ["111"]:
            return html.replace(
                '<h3>강좌정보</h3>',
                '<a class="s_bt bt1" href="/lifelong/course/apply?lecture_idx=111&amp;mode=reserve_form2">신청</a><h3>강좌정보</h3>',
            )
        return html

    site.mutator = expose_closed
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "inactive detail exposes an application control" in meta["configured_collection_error"]


def test_caps_invalid_target_limits_and_dedupe_cardinality_fail_closed() -> None:
    rows, _parser, meta = _collect(FixtureSite(), max_pages=5)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "5 of 6 required list requests" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FixtureSite(), detail_limit=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "4 of 5 required current details" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        FixtureSite(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]

    rows, _parser, meta = jangheung.collect_jangheung_education(
        Target("WRONG", jangheung.JANGHEUNG_URL)
    )
    assert rows == []
    assert "target does not match" in meta["configured_collection_error"]

    rows, _parser, meta = jangheung.collect_jangheung_education(
        _target(), max_workers=0
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["configured_collection_error"] == "invalid collection limits"


def test_transient_failure_retries_and_permanent_detail_failure_is_fail_closed() -> None:
    site = FixtureSite()
    detail = jangheung.jangheung_detail_url("113")
    site.failures[detail] = 2
    rows, _parser, meta = _collect(site)
    assert len(rows) == 5
    assert site.calls.count(detail) == 3
    assert meta["snapshot_complete"] is True

    site = FixtureSite()
    site.permanent_failure = "idx=113"
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["detail_errors"] == 1
    assert "fixture permanent failure" in meta["configured_collection_error"]


def test_complete_historical_only_snapshot_is_valid_no_current_data() -> None:
    courses = [
        replace(
            course,
            source_status="수강종료",
            start="2025-01-01",
            end="2025-02-01",
            apply_start="2024-12-01 09:00",
            apply_end="2024-12-31 18:00",
        )
        for course in _courses()
    ]
    site = FixtureSite(courses)
    rows, _parser, meta = _collect(site, detail_limit=0)
    assert rows == []
    assert meta["source_rows"] == 13
    assert meta["current_source_count"] == 0
    assert meta["detail_attempts"] == meta["detail_pages"] == 0
    assert meta["no_current_data"] is True
    assert meta["snapshot_complete"] is True
    assert "no current/future courses" in meta["no_current_reason"]
    assert not any("idx=" in call for call in site.calls)


def test_complete_zero_course_catalogue_is_not_an_error() -> None:
    site = FixtureSite([])
    rows, _parser, meta = _collect(site, detail_limit=0)
    assert rows == []
    assert meta["source_total"] == meta["source_rows"] == 0
    assert meta["list_requests"] == meta["required_list_requests"] == 4
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 2
    assert meta["no_current_data"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""


@pytest.mark.skipif(
    os.getenv("JANGHEUNG_LIVE_TEST") != "1",
    reason="set JANGHEUNG_LIVE_TEST=1 for the audited official-site snapshot",
)
def test_live_official_jangheung_snapshot_contract() -> None:
    rows, parser, meta = jangheung.collect_jangheung_education(
        _target(),
        today="2026-07-21",
        timeout=30,
        max_pages=120,
        detail_limit=100,
        max_workers=6,
    )
    assert parser == jangheung.JANGHEUNG_PARSER
    assert rows == []
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == meta["source_rows"] == 2
    assert meta["data_pages"] == 1
    assert meta["page_counts"] == {1: 2}
    assert meta["list_requests"] == meta["required_list_requests"] == 4
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 2
    assert meta["current_source_count"] == 0
    assert meta["expired_count"] == 2
    assert meta["detail_attempts"] == meta["detail_pages"] == 0
    assert meta["pages"] == 4
    assert meta["source_status_counts"] == {"수강종료": 2}
    assert meta["source_category_counts"] == {"문화예술": 2}
    assert meta["source_application_control_count"] == 0
    assert meta["identity_duplicate_count"] == 0
    assert meta["row_number_duplicate_count"] == 0
    assert meta["current_branch_names"] == []
    assert meta["no_current_data"] is True
