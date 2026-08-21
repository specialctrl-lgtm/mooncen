from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_inje as inje


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    scope: str = "center"
    institution: str = inje.INJE_CENTER_BRANCH
    category: str = "문화/예술"
    source_status: str = "신청중"
    start: str = "2026-07-21"
    end: str = "2026-08-20"
    apply_start: str = "2026-07-01"
    apply_end: str = "2026-08-19"
    target: str = "인제군민"
    fee: str = "무료"
    material_fee: str = "0원"
    capacity: str = "20명"
    venue: str = "인제군 평생학습센터"
    schedule: str = "매주 화요일 10:00 ~ 12:00"
    online_payment: str = "아니오"


class DummySession:
    def close(self) -> None:
        return None


def _fixture_courses() -> list[Course]:
    center_current = [
        Course("101", "인제 생활도예"),
        Course("102", "인제 디지털 교실", category="IT/컴퓨터", capacity="24명"),
        Course(
            "103",
            "인제 시민 인문학",
            category="인문/시민교육",
            source_status="신청마감",
            start="2026-07-10",
            end="2026-09-10",
        ),
    ]
    historical = [
        Course(
            str(identity),
            f"인제 과거 강좌 {identity}",
            category="기타",
            source_status="교육종료",
            start="2025-01-01",
            end="2025-02-01",
            apply_start="2024-12-01",
            apply_end="2024-12-31",
        )
        for identity in range(201, 209)
    ]
    institutions = [
        Course(
            "301",
            "천리길 생태교육",
            scope="institution",
            institution="인제 천리길",
            category="체육",
            source_status="교육중",
            start="2026-06-01",
            end="2026-10-31",
            venue="인제 천리길 교육장",
            schedule="매주 토요일 09:00 ~ 12:00",
        ),
        Course(
            "302",
            "교육협력 진로교실",
            scope="institution",
            institution="인제군 문화교육과 교육협력",
            category="취업/자격증",
            source_status="신청마감",
            start="2026-08-01",
            end="2026-09-30",
            venue="인제군청 교육실",
        ),
    ]
    return [*center_current, *historical, *institutions]


def _options(values: tuple[tuple[str, str], ...]) -> str:
    return "".join(
        f'<option value="{escape(code, quote=True)}">{escape(label)}</option>'
        for code, label in values
    )


def _list_form(scope: str, displayed_page: int) -> str:
    institution_filter = ""
    if scope == "institution":
        institution_filter = """
          <select id="facilities-type" name="facilitiesType"></select>
          <input type="hidden" id="facilities-select" value="">
        """
    return f"""
      <form id="list-form" name="search" method="post"
            action="{inje.INJE_SCOPE_PATHS[scope]}">
        <input type="hidden" name="ptSignature" value="fixture-signature">
        <input type="hidden" id="paging-page" class="search-elements"
               name="page.page" value="{displayed_page}">
        <select id="teach-type" name="teachType"></select>
        <input type="hidden" id="teach-select" value="">
        <input type="checkbox" id="status-check" name="statuscheck" value="">
        <input type="hidden" id="status-type" name="statusType" value="">
        <input type="hidden" id="key-field" name="keyField" value="COURSE_NAME">
        <input type="text" id="search-word" name="searchWord" value="">
        <select id="age-type" name="ageType">{_options(inje.INJE_AGE_OPTIONS)}</select>
        <select id="pay-select" name="paySelect">{_options(inje.INJE_PAY_OPTIONS)}</select>
        {institution_filter}
      </form>
    """


def _card(course: Course) -> str:
    status_class = ' class="state1"' if course.source_status == "신청중" else ""
    return f"""
      <a href="/lct/course/view?courseSeq={course.identity}">
        <div class="eduCenter">{escape(course.institution)}</div>
        <div class="eduTitle">{escape(course.title)} <span>[{escape(course.category)}]</span></div>
        <p>교육기간: {course.start} ~ {course.end}</p>
        <p>신청기간: {course.apply_start} ~ {course.apply_end}</p>
        <p>교육대상: {escape(course.target)}</p>
        <p>접수상태: <span{status_class}>{course.source_status}</span></p>
        <p>수강료/재료비: {escape(course.fee)} / {escape(course.material_fee)}</p>
        <p>모집인원: {escape(course.capacity)}</p>
      </a>
    """


def _list_html(
    scope: str,
    requested_page: int,
    rows: list[Course],
    total: int,
    *,
    sentinel: bool = False,
) -> str:
    displayed_page = 1 if sentinel else requested_page
    last = max(1, (total + inje.INJE_PAGE_SIZE - 1) // inje.INJE_PAGE_SIZE)
    last_control = (
        f'<li><a class="last" href="javascript:admin.pageMove({last}, \'#list-form\');">끝</a></li>'
        if displayed_page == 1 and last > 1
        else ""
    )
    body = (
        "".join(_card(course) for course in rows)
        if rows
        else '<a href="javascript:void(0);"><div class="eduTitle">등록된 강좌가 없습니다.</div></a>'
    )
    return f"""
      <html>
        <head><title>{inje.INJE_DOCUMENT_TITLE}</title></head>
        <body>
          {_list_form(scope, displayed_page)}
          <div class="tblTopArea"><h4><span>총 {total} 건의 강좌가 있습니다.</span></h4></div>
          <div class="eduList2"><ul><li>{body}</li></ul></div>
          <div class="btnArea mt40"><ul class="paging">
            <li><a class="on" href="javascript:admin.pageMove({displayed_page}, '#list-form');">{displayed_page}</a></li>
            {last_control}
          </ul></div>
        </body>
      </html>
    """


def _detail_html(
    course: Course,
    *,
    detail_title: str | None = None,
    detail_institution: str | None = None,
    detail_category: str | None = None,
    detail_period: str | None = None,
    payment_identity: str | None = None,
    expose_control: bool | None = None,
) -> str:
    control = course.source_status == "신청중" if expose_control is None else expose_control
    application = (
        '<li><a class="course" href="javascript:noLogin()">수강 신청</a></li>'
        if control
        else ""
    )
    return f"""
      <html>
        <head><title>{inje.INJE_DOCUMENT_TITLE}</title></head>
        <body>
          <div class="tblDetail-01"><table>
            <thead><tr><th>
              <p class="eduCenter">{escape(detail_institution or course.institution)}</p>
              <p class="eduTitle">{escape(detail_title or course.title)}
                <span>[{escape(detail_category or course.category)}]</span>
              </p>
            </th></tr></thead>
            <tbody>
              <tr><th>교육기간</th><td>{detail_period or f'{course.start} ~ {course.end}'}</td></tr>
              <tr><th>교육대상</th><td>{escape(course.target)}</td></tr>
              <tr><th>교육장소</th><td>{escape(course.venue)}</td></tr>
              <tr><th>교육일시</th><td>{escape(course.schedule)}</td></tr>
              <tr><th>연락처</th><td>033-460-1234 / private@example.com</td></tr>
              <tr><th>강사명</th><td>저장금지 강사</td></tr>
              <tr><th>온라인 결제여부</th><td>{course.online_payment}</td></tr>
              <tr><th>첨부파일</th><td><a href="/private.pdf">개인정보 첨부</a></td></tr>
              <tr><th>내용</th><td>저장하면 안 되는 자유 본문 010-7777-8888</td></tr>
            </tbody>
          </table></div>
          <form id="pay-form" name="payForm" method="post" action="{inje.INJE_PAYMENT_PATH}">
            <input type="hidden" name="ptSignature" value="detail-signature">
            <input type="hidden" name="courseSeq" value="{payment_identity or course.identity}">
          </form>
          <div class="btnArea mt20"><ul>{application}</ul><ul class="aRight"></ul></div>
          <script>function noLogin() {{ location.href = "/main/login"; }}</script>
        </body>
      </html>
    """


class FixtureSite:
    def __init__(self, courses: list[Course] | None = None) -> None:
        self.courses = list(courses or _fixture_courses())
        self.calls: list[str] = []
        self.failures: Counter[str] = Counter()
        self.permanent_failure: str = ""
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
        query = parse_qs(parsed.query)
        if parsed.path in inje.INJE_SCOPE_PATHS.values():
            scope = next(
                key for key, path in inje.INJE_SCOPE_PATHS.items() if path == parsed.path
            )
            page = int(query["page.page"][0])
            scoped = [course for course in self.courses if course.scope == scope]
            last = max(1, (len(scoped) + inje.INJE_PAGE_SIZE - 1) // inje.INJE_PAGE_SIZE)
            sentinel = page == last + 1
            rows = [] if sentinel else scoped[(page - 1) * 10 : page * 10]
            html = _list_html(scope, page, rows, len(scoped), sentinel=sentinel)
        elif parsed.path == inje.INJE_DETAIL_PATH:
            identity = query["courseSeq"][0]
            course = next(course for course in self.courses if course.identity == identity)
            html = _detail_html(course)
        else:
            raise AssertionError(f"unexpected URL: {url}")
        if self.mutator is not None:
            html = self.mutator(url, html, call_number)
        return html


def _target() -> Target:
    return Target(inje.INJE_PROVIDER, inje.INJE_CANONICAL_URL)


def _collect(site: FixtureSite, **kwargs):
    return inje.collect_inje_education(
        _target(),
        today="2026-07-21",
        session_factory=site.session_factory,
        fetcher=site.fetch,
        **kwargs,
    )


def test_target_urls_and_candidate_ownership_audit() -> None:
    assert inje.INJE_MUNICIPALITY_CODE == "5181000000"
    assert inje.INJE_MUNICIPALITY_NAME == "강원특별자치도 인제군"
    assert inje.is_inje_education_target(_target())
    assert not inje.is_inje_education_target(
        Target(inje.INJE_DUPLICATE_EDU_PROVIDER, inje.INJE_INSTITUTION_URL)
    )
    assert not inje.is_inje_education_target(
        Target(inje.INJE_PROVIDER, inje.INJE_CANONICAL_URL + "?page.page=1")
    )
    assert inje.inje_list_url("center", 2).endswith("/lct/course/list?page.page=2")
    assert inje.inje_list_url("institution", 3).endswith("/lct/edu/list?page.page=3")
    assert inje.inje_detail_url("42").endswith("/lct/course/view?courseSeq=42")
    decisions = {
        key: value["decision"] for key, value in inje.INJE_CANDIDATE_AUDIT.items()
    }
    assert decisions[inje.INJE_DUPLICATE_OWNER_AUDIT_ID].startswith("exclude_duplicate")
    assert decisions[inje.INJE_LANDING_CANDIDATE_ID].startswith("exclude_discovery")
    assert decisions[inje.INJE_EDUCATION_SUPPORT_CANDIDATE_ID].startswith("exclude_separate")
    assert inje.is_inje_candidate_alias(
        Target("", inje.INJE_LANDING_URL, inje.INJE_LANDING_CANDIDATE_ID)
    )


def test_complete_two_scope_snapshot_pagination_details_controls_and_privacy() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == inje.INJE_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == [
        "101",
        "102",
        "103",
        "301",
        "302",
    ]
    assert meta["source_rows"] == 13
    assert meta["source_rows_by_scope"] == {"center": 11, "institution": 2}
    assert meta["current_source_count"] == 5
    assert meta["expired_count"] == 8
    assert meta["data_pages"] == 3
    assert meta["list_requests"] == meta["required_list_requests"] == 9
    assert meta["sentinel_requests"] == 2
    assert meta["stability_rechecks"] == 4
    assert meta["detail_attempts"] == meta["detail_pages"] == 5
    assert meta["pages"] == 14
    assert meta["visible_public_application_control_count"] == 2
    assert meta["status_counts"] == {"OPEN": 2, "CLOSED": 3}
    assert meta["scope_counts"] == {"center": 3, "institution": 2}
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert {row["program_type"] for row in rows} == {"교육"}
    assert {row["municipality_code"] for row in rows} == {"5181000000"}
    assert rows[0]["branch"] == "강원특별자치도 인제군 / 인제군평생학습센터"
    assert rows[3]["branch"] == "강원특별자치도 인제군 / 인제 천리길"
    assert [row["reservation_available"] for row in rows] == [True, True, False, False, False]
    assert rows[0]["application_url"] == inje.inje_detail_url("101")
    assert rows[2]["application_url"] == ""
    payload = repr(rows)
    for forbidden in (
        "033-460-1234",
        "private@example.com",
        "010-7777-8888",
        "저장금지 강사",
        "개인정보 첨부",
        "저장하면 안 되는 자유 본문",
    ):
        assert forbidden not in payload
    assert all(row["description"] == row["title"] for row in rows)
    assert all(
        set(row["raw_fields"]) <= inje._SAFE_RAW_FIELDS  # noqa: SLF001
        for row in rows
    )


@pytest.mark.parametrize(
    ("needle", "replacement", "expected"),
    [
        ('<option value="1">어린이</option>', '<option value="1">유아</option>', "age taxonomy changed"),
        ('<option value="free">무료</option>', '<option value="free">무상</option>', "fee taxonomy changed"),
        ('name="keyField" value="COURSE_NAME"', 'name="keyField" value="TITLE"', "search key changed"),
        ('id="teach-type" name="teachType"></select>', 'id="teach-type" name="teachType"><option value="x">X</option></select>', "course-category dynamic taxonomy changed"),
    ],
)
def test_list_search_contract_changes_fail_closed(
    needle: str, replacement: str, expected: str
) -> None:
    site = FixtureSite()
    site.mutator = lambda _url, html, _call: html.replace(needle, replacement)
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert expected in meta["configured_collection_error"]


def test_scope_specific_facility_filter_change_fails_closed() -> None:
    site = FixtureSite()

    def mutate(url: str, html: str, _call: int) -> str:
        if urlparse(url).path == inje.INJE_CENTER_PATH:
            return html.replace(
                '<select id="teach-type" name="teachType"></select>',
                '<select id="teach-type" name="teachType"></select><select id="facilities-type" name="facilitiesType"></select>',
            )
        return html

    site.mutator = mutate
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "center scope gained an institution filter" in meta["configured_collection_error"]


def test_sentinel_must_be_explicit_empty_and_reset_to_page_one() -> None:
    site = FixtureSite()

    def mutate(url: str, html: str, _call: int) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == inje.INJE_CENTER_PATH and query.get("page.page") == ["3"]:
            return html.replace('name="page.page" value="1"', 'name="page.page" value="3"')
        return html

    site.mutator = mutate
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["pagination_complete"] is False
    assert "paging field changed" in meta["configured_collection_error"]


def test_first_page_stability_recheck_change_fails_closed() -> None:
    site = FixtureSite()

    def mutate(url: str, html: str, call: int) -> str:
        parsed = urlparse(url)
        if (
            parsed.path == inje.INJE_CENTER_PATH
            and parse_qs(parsed.query).get("page.page") == ["1"]
            and call == 2
        ):
            return html.replace("인제 생활도예", "변경된 생활도예")
        return html

    site.mutator = mutate
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "first-page stability recheck changed" in meta["configured_collection_error"]


def test_identity_overlap_between_scopes_fails_closed() -> None:
    courses = _fixture_courses()
    courses[-2] = replace(courses[-2], identity="101")
    rows, _parser, meta = _collect(FixtureSite(courses))
    assert rows == []
    assert meta["identity_duplicate_count"] == 1
    assert "duplicate official identities" in meta["configured_collection_error"]


def test_current_semantic_duplicate_fails_closed_but_historical_is_report_only() -> None:
    courses = _fixture_courses()
    courses[-1] = replace(
        courses[-1],
        title=courses[-2].title,
        institution=courses[-2].institution,
        category=courses[-2].category,
        start=courses[-2].start,
        end=courses[-2].end,
    )
    rows, _parser, meta = _collect(FixtureSite(courses))
    assert rows == []
    assert meta["semantic_duplicate_group_count"] == 1
    assert "current semantic duplicate groups" in meta["configured_collection_error"]

    historical = _fixture_courses()
    historical[4] = replace(
        historical[4],
        title=historical[3].title,
        institution=historical[3].institution,
        start=historical[3].start,
        end=historical[3].end,
    )
    rows, _parser, meta = _collect(FixtureSite(historical))
    assert len(rows) == 5
    assert meta["historical_semantic_duplicate_group_count"] == 1
    assert meta["semantic_duplicate_group_count"] == 0


def test_reversed_list_period_and_status_marker_changes_fail_closed() -> None:
    courses = _fixture_courses()
    courses[0] = replace(courses[0], start="2026-09-01", end="2026-08-01")
    rows, _parser, meta = _collect(FixtureSite(courses))
    assert rows == []
    assert "current/future rows have reversed source periods" in meta["configured_collection_error"]
    assert meta["current_reversed_period_count"] == 1

    historical = _fixture_courses()
    historical[3] = replace(
        historical[3], start="2025-02-01", end="2025-01-01"
    )
    rows, _parser, meta = _collect(FixtureSite(historical))
    assert len(rows) == 5
    assert meta["snapshot_complete"] is True
    assert meta["historical_reversed_education_period_count"] == 1

    site = FixtureSite()
    site.mutator = lambda _url, html, _call: html.replace(
        '<span class="state1">신청중</span>', '<span>신청중</span>'
    )
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "reception state changed" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("identity", "needle", "replacement", "expected"),
    [
        ("101", "인제 생활도예", "상세 제목 변조", "list/detail identity mismatch"),
        ("101", "[문화/예술]", "[기타]", "list/detail identity mismatch"),
        ("301", "인제 천리길", "다른 기관", "list/detail identity mismatch"),
        ("101", "2026-07-21 ~ 2026-08-20", "2026-07-22 ~ 2026-08-20", "safe fields mismatch"),
        ("101", 'name="courseSeq" value="101"', 'name="courseSeq" value="999"', "payment identity changed"),
    ],
)
def test_detail_identity_and_safe_field_changes_fail_closed(
    identity: str, needle: str, replacement: str, expected: str
) -> None:
    site = FixtureSite()

    def mutate(url: str, html: str, _call: int) -> str:
        parsed = urlparse(url)
        if parsed.path == inje.INJE_DETAIL_PATH and parse_qs(parsed.query)["courseSeq"] == [identity]:
            return html.replace(needle, replacement, 1)
        return html

    site.mutator = mutate
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["detail_errors"] >= 1
    assert expected in meta["configured_collection_error"]


def test_detail_schema_change_fails_closed_without_reading_unsafe_cells() -> None:
    site = FixtureSite()

    def mutate(url: str, html: str, _call: int) -> str:
        if urlparse(url).path == inje.INJE_DETAIL_PATH:
            return html.replace("<th>강사명</th>", "<th>강사</th>", 1)
        return html

    site.mutator = mutate
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "detail schema changed" in meta["configured_collection_error"]


def test_application_controls_are_required_only_for_open_rows() -> None:
    site = FixtureSite()

    def remove_open(url: str, html: str, _call: int) -> str:
        parsed = urlparse(url)
        if parsed.path == inje.INJE_DETAIL_PATH and parse_qs(parsed.query)["courseSeq"] == ["101"]:
            return html.replace(
                '<li><a class="course" href="javascript:noLogin()">수강 신청</a></li>',
                "",
            )
        return html

    site.mutator = remove_open
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "open course has no unique public application control" in meta["configured_collection_error"]

    site = FixtureSite()

    def expose_closed(url: str, html: str, _call: int) -> str:
        parsed = urlparse(url)
        if parsed.path == inje.INJE_DETAIL_PATH and parse_qs(parsed.query)["courseSeq"] == ["103"]:
            return html.replace(
                '<div class="btnArea mt20"><ul></ul>',
                '<div class="btnArea mt20"><ul><li><a class="course" href="javascript:noLogin()">수강 신청</a></li></ul>',
            )
        return html

    site.mutator = expose_closed
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "inactive course exposes an application control" in meta["configured_collection_error"]


def test_open_login_handler_change_fails_closed() -> None:
    site = FixtureSite()

    def mutate(url: str, html: str, _call: int) -> str:
        if urlparse(url).path == inje.INJE_DETAIL_PATH:
            return html.replace('/main/login";', '/changed/login";')
        return html

    site.mutator = mutate
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert "login application handler changed" in meta["configured_collection_error"]


def test_caps_invalid_inputs_and_dedupe_cardinality_fail_closed() -> None:
    site = FixtureSite()
    rows, _parser, meta = _collect(site, max_pages=8)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "8 of 9 required list requests" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FixtureSite(), detail_limit=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "4 of 5 required current details" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        FixtureSite(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]

    rows, _parser, meta = inje.collect_inje_education(
        Target("WRONG", inje.INJE_CANONICAL_URL)
    )
    assert rows == []
    assert "target does not match" in meta["configured_collection_error"]

    rows, _parser, meta = inje.collect_inje_education(_target(), max_workers=0)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["configured_collection_error"] == "invalid collection limits"


def test_transient_failures_retry_and_permanent_failure_is_fail_closed() -> None:
    site = FixtureSite()
    detail = inje.inje_detail_url("101")
    site.failures[detail] = 2
    rows, _parser, meta = _collect(site)
    assert len(rows) == 5
    assert site.calls.count(detail) == 3
    assert meta["snapshot_complete"] is True

    site = FixtureSite()
    site.permanent_failure = "courseSeq=101"
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["detail_errors"] == 1
    assert "fixture permanent failure" in meta["configured_collection_error"]


def test_complete_historical_only_snapshot_does_not_fetch_details() -> None:
    courses = [
        replace(
            course,
            start="2025-01-01",
            end="2025-02-01",
            apply_start="2024-12-01",
            apply_end="2024-12-31",
            source_status="교육종료",
        )
        for course in _fixture_courses()
    ]
    site = FixtureSite(courses)
    rows, _parser, meta = _collect(site, detail_limit=0)
    assert rows == []
    assert meta["source_rows"] == 13
    assert meta["current_source_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["detail_pages"] == 0
    assert meta["no_current_data"] is True
    assert meta["snapshot_complete"] is True
    assert "no current/future courses" in meta["no_current_reason"]
    assert not any("courseSeq=" in call for call in site.calls)


@pytest.mark.skipif(
    os.getenv("INJE_LIVE_TEST") != "1",
    reason="set INJE_LIVE_TEST=1 for the audited official-site snapshot",
)
def test_live_official_inje_snapshot_contract() -> None:
    rows, parser, meta = inje.collect_inje_education(
        _target(),
        today="2026-07-21",
        timeout=30,
        max_pages=120,
        detail_limit=100,
        max_workers=8,
    )
    assert parser == inje.INJE_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["source_rows_by_scope"] == {"center": 766, "institution": 135}
    assert meta["source_rows"] == 901
    assert meta["data_pages_by_scope"] == {"center": 77, "institution": 14}
    assert meta["data_pages"] == 91
    assert meta["list_requests"] == meta["required_list_requests"] == 97
    assert meta["sentinel_requests"] == 2
    assert meta["stability_rechecks"] == 4
    assert meta["current_source_count"] == 35
    assert meta["detail_pages"] == 35
    assert len(rows) == 35
    assert meta["current_scope_counts"] == {"center": 33, "institution": 2}
    assert meta["status_counts"] == {"OPEN": 5, "CLOSED": 30}
    assert meta["visible_public_application_control_count"] == 5
    assert meta["identity_duplicate_count"] == 0
    assert meta["semantic_duplicate_group_count"] == 0
    assert meta["historical_semantic_duplicate_group_count"] == 7
    assert meta["historical_reversed_education_period_count"] == 6
    assert meta["historical_reversed_application_period_count"] == 1
    assert meta["pages"] == 132
    assert meta["institution_counts"] == {
        "인제군평생학습센터": 33,
        "인제 천리길": 1,
        "인제군 문화교육과 교육협력": 1,
    }
