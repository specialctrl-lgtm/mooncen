from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import inspect
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_gwangju_bukgu_library as bukgu


@dataclass
class Target:
    provider: str
    url: str


@dataclass(frozen=True)
class Course:
    identity: str
    number: int
    title: str
    branch: str
    source_state: str = "접수마감"
    target: str = "성인"
    start: str = "2025-01-01"
    end: str = "2025-02-01"
    capacity: int = 20
    waitlist: int = 5
    current: int = 0
    wait_current: int = 0
    apply_start: str = "2024-12-01"
    apply_start_hour: str = "09"
    apply_end: str = "2025-01-31"
    apply_end_hour: str = "18"
    venue: str = "도서관 문화강좌실"
    fee: str = "무료"
    instructor: bool = False


class DummySession:
    def close(self) -> None:
        return None


def _courses() -> list[Course]:
    branches = list(bukgu.GWANGJU_BUKGU_LIBRARY_BRANCHES)
    current = [
        Course(
            "900023",
            23,
            "북구 문해력 교실",
            "중흥도서관",
            "접수대기중",
            "초등 3~5학년",
            "2026-08-12",
            "2026-08-14",
            apply_start="2026-07-28",
            apply_end="2026-08-10",
            instructor=True,
        ),
        Course(
            "900022",
            22,
            "일곡 청소년 토론",
            "일곡도서관",
            "접수중",
            "청소년",
            "2026-08-01",
            "2026-09-01",
            current=4,
            apply_start="2026-07-01",
            apply_end="2026-08-01",
        ),
        Course(
            "900021",
            21,
            "운암 가족 책놀이",
            "운암도서관",
            "대기자접수중",
            "가족",
            "2026-07-25",
            "2026-07-25",
            capacity=8,
            waitlist=4,
            current=8,
            wait_current=1,
            apply_start="2026-07-01",
            apply_end="2026-07-24",
            instructor=True,
        ),
        Course(
            "900020",
            20,
            "양산 예술 특강",
            "양산도서관",
            "접수마감",
            "초등학생 동반 가족",
            "2026-08-23",
            "2026-08-23",
            capacity=5,
            waitlist=7,
            current=5,
            wait_current=7,
            apply_start="2026-07-01",
            apply_end="2026-07-20",
        ),
        Course(
            "900019",
            19,
            "신용 미디어 제작소",
            "신용도서관",
            "접수중",
            "성인",
            "2026-08-25",
            "2026-09-10",
            capacity=15,
            waitlist=3,
            current=2,
            apply_start="2026-07-20",
            apply_end="2026-07-27",
            instructor=True,
        ),
    ]
    historical = [
        Course(
            str(900000 + number),
            number,
            f"북구 과거 강좌 {number}",
            branches[(23 - number) % len(branches)],
            waitlist=(0 if number == 18 else 1 if number == 17 else 5),
            wait_current=(0 if number == 18 else 2 if number == 17 else 0),
        )
        for number in range(18, 0, -1)
    ]
    return [*current, *historical]


def _tabs() -> str:
    result = []
    for code, label in bukgu.GWANGJU_BUKGU_LIBRARY_BRANCH_FILTERS:
        href = bukgu.GWANGJU_BUKGU_LIBRARY_CANONICAL_URL
        if code:
            href += f"&iType={code}"
        result.append(
            f'<a class="linkColor" href="{escape(href, quote=True)}">'
            f"{escape(label)}</a>"
        )
    return "".join(result)


def _search_form() -> str:
    return f"""
      <form id="searchForm" name="searchForm" method="post"
            action="/main/cultureReq.do?PID={bukgu.GWANGJU_BUKGU_LIBRARY_PID}">
        <input type="hidden" name="CSRFToken"
               value="eMCt99UmcEFLFa35YQu5axVB0liFmzYc5CzJUaB7IVA">
        <input type="hidden" name="searchType" value="">
        <input type="text" name="searchText" value="">
      </form>
    """


def _list_row(course: Course, page: int) -> str:
    detail = bukgu.gwangju_bukgu_library_detail_url(course.identity, page)
    wait_current = (
        f"<br>({course.wait_current}명)" if course.waitlist else ""
    )
    return f"""
      <tr>
        <td class="tc">{course.number}</td>
        <td class="program">
          <span class="label label-lecture2">{escape(course.branch)}</span>
          <a class="title" href="{escape(detail, quote=True)}">{escape(course.title)}</a>
          <p class="desc">강좌기간 : {course.start} ~ {course.end}</p>
        </td>
        <td class="tc">{escape(course.target)}<br>
          {course.capacity}명 (대기 : {course.waitlist}명)</td>
        <td class="tc">{course.current}명{wait_current}</td>
        <td class="tc"><a class="title" href="{escape(detail, quote=True)}">
          {escape(course.source_state)}</a></td>
      </tr>
    """


def _list_html(page: int, rows: list[Course], total: int) -> str:
    headers = "".join(f"<th>{escape(label)}</th>" for label in bukgu._LIST_HEADERS)
    body = "".join(_list_row(course, page) for course in rows)
    pager = (
        f'<a class="btn btn-white active" '
        f'href="{escape(bukgu.gwangju_bukgu_library_list_url(page), quote=True)}">'
        f"{page}</a>"
        if rows
        else ""
    )
    return f"""
      <html>
        <head><title>{escape(bukgu._PAGE_TITLE)}</title></head>
        <body>
          <article>
            {_search_form()}
            <div class="library-tabs">{_tabs()}</div>
            <div><div class="row"><div class="col col-3">전체 : {total}건</div></div></div>
            <table>
              <caption>강좌 프로그램 리스트</caption>
              <thead><tr>{headers}</tr></thead>
              <tbody>{body}</tbody>
            </table>
            <div class="pagination">{pager}</div>
          </article>
        </body>
      </html>
    """


def _safe_detail_fields(course: Course) -> str:
    instructor = (
        "<li><strong>강사명</strong>: NEVER-READ-INSTRUCTOR "
        "010-9999-8888 instructor-secret@example.com</li>"
        if course.instructor
        else ""
    )
    return f"""
      <section class="styleguide">
        <div><div class="row">
          <div class="col col-10"><ul>
            <li><strong>강좌대상</strong>: {escape(course.target)}</li>
            <li><strong>수강기간</strong>: {course.start} ~ {course.end}
              10:00 ~ 12:00 (화)</li>
            <li><strong>접수시간</strong>: {course.apply_start}
              {course.apply_start_hour}시 ~ {course.apply_end}
              {course.apply_end_hour}시</li>
            <li><strong>수강인원(인터넷/대기)</strong>:
              {course.capacity}명 ({course.waitlist})명</li>
            {instructor}
          </ul></div>
          <div class="col col-10"><ul>
            <li><strong>장소</strong>: {escape(course.venue)}</li>
            <li><strong>비용</strong>: {escape(course.fee)}</li>
          </ul></div>
        </div></div>
      </section>
    """


def _detail_html(
    course: Course,
    *,
    title: str | None = None,
    branch: str | None = None,
    button: bool = True,
) -> str:
    control = (
        '<button class="btn btn-edit" type="button" id="nloginBtn">신청하기</button>'
        if button
        else ""
    )
    return f"""
      <html>
        <head><title>{escape(bukgu._PAGE_TITLE)}</title></head>
        <body>
          <form id="writeForm" name="writeForm" method="post"
                action="/main/cultureReq.do?PID={bukgu.GWANGJU_BUKGU_LIBRARY_PID}">
            <input type="hidden" name="CSRFToken"
                   value="vXKbo6xVN9Ph3_l_B1aobZ_8gLj1QUXfJ-NH8tB5cDs">
            <input type="hidden" name="iType" value="">
            <input type="hidden" name="searchText" value="">
            <input type="hidden" name="idx" value="{course.identity}">
            <input type="hidden" name="action" value="Next">
            <div class="boardRead">
              {escape(title if title is not None else course.title)}
              <span class="label label-lecture2">{escape(branch if branch is not None else course.branch)}</span>
              {_safe_detail_fields(course)}
              <section class="articleBody">
                <p>NEVER-READ-ARTICLE applicant-article@example.com 010-8888-7777</p>
              </section>
              <section class="styleguide" data-applicant-roster="true">
                <div>
                  <h3>신청승인</h3>
                  <ul>
                    <li>NEVER-READ-APPLICANT 홍길동 applicant-secret@example.com</li>
                    <li><input name="applicant_phone" value="010-7777-6666"></li>
                  </ul>
                  <h3>대기</h3>
                  <ul><li>NEVER-READ-WAITING 김대기</li></ul>
                </div>
              </section>
              <footer>
                {control}
                <a class="btn btn-gray"
                   href="/main/cultureReq.do?PID=0401&amp;iType=&amp;searchText=">목록</a>
              </footer>
            </div>
          </form>
          <script>
            $("#nloginBtn").click(function(){{
              location.href="/main/login.do?PID=9901";
            }});
          </script>
        </body>
      </html>
    """


class HtmlFixture:
    def __init__(self, courses: list[Course] | None = None) -> None:
        self.courses = list(courses or _courses())
        self.pages: dict[str, str] = {}
        self.calls: list[str] = []
        total = len(self.courses)
        last = (total + bukgu.GWANGJU_BUKGU_LIBRARY_PAGE_SIZE - 1) // (
            bukgu.GWANGJU_BUKGU_LIBRARY_PAGE_SIZE
        )
        for page in range(1, last + 1):
            start = (page - 1) * bukgu.GWANGJU_BUKGU_LIBRARY_PAGE_SIZE
            rows = self.courses[
                start : start + bukgu.GWANGJU_BUKGU_LIBRARY_PAGE_SIZE
            ]
            self.pages[bukgu.gwangju_bukgu_library_list_url(page)] = _list_html(
                page, rows, total
            )
        self.pages[bukgu.gwangju_bukgu_library_list_url(last + 1)] = _list_html(
            last + 1, [], total
        )
        for page in range(1, last + 1):
            start = (page - 1) * bukgu.GWANGJU_BUKGU_LIBRARY_PAGE_SIZE
            for course in self.courses[
                start : start + bukgu.GWANGJU_BUKGU_LIBRARY_PAGE_SIZE
            ]:
                self.pages[
                    bukgu.gwangju_bukgu_library_detail_url(
                        course.identity, page
                    )
                ] = _detail_html(course)

    def __call__(self, _session: object, url: str, _timeout: int) -> str:
        self.calls.append(url)
        if url not in self.pages:
            raise AssertionError(f"unexpected URL {url}")
        return self.pages[url]


def _target() -> Target:
    return Target(
        bukgu.GWANGJU_BUKGU_LIBRARY_PROVIDER,
        bukgu.GWANGJU_BUKGU_LIBRARY_CANONICAL_URL,
    )


def _collect(
    fixture: HtmlFixture,
    **kwargs: object,
) -> tuple[list[dict[str, object]], str, dict[str, object]]:
    return bukgu.collect_gwangju_bukgu_library_education(
        _target(),
        max_pages=10,
        detail_limit=10,
        today="2026-07-21",
        fetcher=fixture,
        session_factory=DummySession,
        **kwargs,
    )


def test_owner_selection_aliases_and_separate_operator_boundaries() -> None:
    aliases = bukgu.GWANGJU_BUKGU_LIBRARY_FACILITY_ALIASES
    assert len(aliases) == 5
    selected = [item for item in aliases if item.execution_enabled]
    assert [(item.provider, item.exact_branch) for item in selected] == [
        (bukgu.GWANGJU_BUKGU_LIBRARY_PROVIDER, "중흥도서관")
    ]
    assert len(bukgu.GWANGJU_BUKGU_LIBRARY_NON_EXECUTING_ALIASES) == 4
    assert {item.exact_branch for item in aliases} == set(
        bukgu.GWANGJU_BUKGU_LIBRARY_BRANCHES
    )
    assert {
        item.registry_name: item.exact_branch for item in aliases
    }["광주북구운암도서관"] == "운암도서관"
    assert {
        item.registry_name: item.exact_branch for item in aliases
    }["광주북구일곡도서관"] == "일곡도서관"

    boundaries = bukgu.GWANGJU_BUKGU_LIBRARY_OWNER_BOUNDARY_AUDIT
    assert (
        boundaries[bukgu.GWANGJU_METROPOLITAN_BOOKING_PROVIDER]["decision"]
        == "keep_separate_metropolitan_reservation_owner"
    )
    assert (
        boundaries[bukgu.GWANGJU_CULTURAL_FOUNDATION_PROVIDER]["decision"]
        == "keep_separate_cultural_foundation_owner"
    )
    assert (
        boundaries[bukgu.GWANGJU_METROPOLITAN_BOOKING_PROVIDER][
            "candidate_id"
        ]
        == "MUNI_IR_9A23E8B5B35F"
    )
    assert (
        boundaries[bukgu.GWANGJU_CULTURAL_FOUNDATION_PROVIDER]["candidate_id"]
        == "MUNI_IR_61D91EBA841D"
    )


def test_strict_canonical_target_and_nonexecuting_alias_metadata() -> None:
    assert bukgu.is_target(_target())
    assert not bukgu.is_target(
        Target(
            bukgu.GWANGJU_BUKGU_LIBRARY_PROVIDER,
            bukgu.GWANGJU_BUKGU_LIBRARY_CANONICAL_URL + "&page=1",
        )
    )
    assert not bukgu.is_target(
        Target(
            "CULTURE_PUBLIC_LIBRARY_C78844E97B",
            bukgu.GWANGJU_BUKGU_LIBRARY_CANONICAL_URL,
        )
    )
    alias_target = Target(
        "CULTURE_PUBLIC_LIBRARY_C78844E97B",
        "https://lib.bukgu.gwangju.kr/main.do",
    )
    assert bukgu.is_gwangju_bukgu_library_alias_target(alias_target)
    metadata = bukgu.gwangju_bukgu_library_alias_metadata(alias_target)
    assert metadata["execution_enabled"] is False
    assert metadata["duplicate_of"] == bukgu.GWANGJU_BUKGU_LIBRARY_PROVIDER
    assert metadata["exact_branch"] == "운암도서관"


def test_url_builders_keep_identity_page_and_origin() -> None:
    assert (
        bukgu.gwangju_bukgu_library_list_url()
        == bukgu.GWANGJU_BUKGU_LIBRARY_CANONICAL_URL
    )
    page = bukgu.gwangju_bukgu_library_list_url(153)
    assert parse_qs(urlparse(page).query, keep_blank_values=True) == {
        "PID": ["0401"],
        "searchText": [""],
        "iType": [""],
        "page": ["153"],
    }
    detail = bukgu.gwangju_bukgu_library_detail_url("1005443", 1)
    assert parse_qs(urlparse(detail).query, keep_blank_values=True) == {
        "PID": ["0401"],
        "action": ["View"],
        "idx": ["1005443"],
        "page": ["1"],
        "searchType": [""],
        "searchText": [""],
    }
    with pytest.raises(ValueError):
        bukgu.gwangju_bukgu_library_list_url(0)
    with pytest.raises(ValueError):
        bukgu.gwangju_bukgu_library_detail_url("../1")


def test_complete_snapshot_safe_details_statuses_and_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = HtmlFixture()
    original = bukgu._allowed_detail_value
    read_labels: list[str] = []

    def guarded_allowed_value(item: object, label: str) -> str:
        assert label != "강사명"
        read_labels.append(label)
        return original(item, label)

    monkeypatch.setattr(bukgu, "_allowed_detail_value", guarded_allowed_value)
    rows, parser, meta = _collect(fixture)

    assert parser == bukgu.GWANGJU_BUKGU_LIBRARY_PARSER
    assert len(rows) == 5
    assert meta["source_total"] == 23
    assert meta["source_rows"] == 23
    assert meta["page_counts"] == {1: 10, 2: 10, 3: 3}
    assert meta["data_pages"] == 3
    assert meta["sentinel_page"] == 4
    assert meta["sentinel_pages"] == 1
    assert meta["required_list_requests"] == 6
    assert meta["list_requests"] == 6
    assert meta["list_rechecks"] == 2
    assert meta["current_count"] == 5
    assert meta["expired_count"] == 18
    assert meta["detail_pages"] == 5
    assert meta["source_branch_counts"] == {
        "중흥도서관": 5,
        "일곡도서관": 5,
        "운암도서관": 5,
        "양산도서관": 4,
        "신용도서관": 4,
    }
    assert meta["current_branch_counts"] == {
        "중흥도서관": 1,
        "일곡도서관": 1,
        "운암도서관": 1,
        "양산도서관": 1,
        "신용도서관": 1,
    }
    assert meta["identity_duplicate_count"] == 0
    assert meta["capacity_overflow_count"] == 0
    assert meta["waitlist_overflow_count"] == 1
    assert meta["applicant_roster_sections_skipped"] == 5
    assert meta["article_body_sections_skipped"] == 5
    assert meta["instructor_values_skipped"] == 3
    assert meta["application_control_count"] == 5
    assert meta["actionable_application_count"] == 3
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["pii_boundaries_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""
    assert set(read_labels) == bukgu._DETAIL_REQUIRED_FIELDS

    by_branch = {row["branch"]: row for row in rows}
    assert by_branch["중흥도서관"]["status"] == "SCHEDULED"
    assert by_branch["일곡도서관"]["status"] == "OPEN"
    assert by_branch["운암도서관"]["status"] == "OPEN"
    assert by_branch["양산도서관"]["status"] == "CLOSED"
    assert by_branch["신용도서관"]["status"] == "OPEN"
    assert by_branch["운암도서관"]["raw_fields"]["source_waitlist_mode"] is True
    assert by_branch["운암도서관"]["waitlist_current"] == 1
    assert by_branch["중흥도서관"]["reservation_available"] is False
    assert by_branch["중흥도서관"]["application_url"] == ""
    assert by_branch["일곡도서관"]["reservation_available"] is True
    assert by_branch["일곡도서관"]["application_url"].endswith(
        "idx=900022&page=1&searchType=&searchText="
    )
    assert all(row["provider"] == bukgu.GWANGJU_BUKGU_LIBRARY_PROVIDER for row in rows)
    assert all(row["preserve_branch"] is True for row in rows)

    persisted = repr((rows, meta))
    for secret in (
        "NEVER-READ-INSTRUCTOR",
        "NEVER-READ-ARTICLE",
        "NEVER-READ-APPLICANT",
        "NEVER-READ-WAITING",
        "applicant-secret@example.com",
        "010-7777-6666",
    ):
        assert secret not in persisted


def test_cap_missing_sentinel_duplicate_and_detail_drift_fail_closed() -> None:
    fixture = HtmlFixture()
    rows, _, meta = bukgu.collect_gwangju_bukgu_library_education(
        _target(),
        max_pages=3,
        detail_limit=10,
        today="2026-07-21",
        fetcher=fixture,
        session_factory=DummySession,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "sentinel page 4 is required" in meta["configured_collection_error"]

    fixture = HtmlFixture()
    fixture.pages[bukgu.gwangju_bukgu_library_list_url(4)] = _list_html(
        4, [fixture.courses[-1]], len(fixture.courses)
    )
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "empty sentinel" in meta["configured_collection_error"]

    courses = _courses()
    courses[1] = replace(courses[1], identity=courses[0].identity)
    fixture = HtmlFixture(courses)
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "duplicate source identities" in meta["configured_collection_error"]

    fixture = HtmlFixture()
    current = fixture.courses[0]
    fixture.pages[
        bukgu.gwangju_bukgu_library_detail_url(current.identity, 1)
    ] = _detail_html(current, branch="일곡도서관")
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "detail branch changed" in meta["configured_collection_error"]

    fixture = HtmlFixture()
    current = fixture.courses[0]
    fixture.pages[
        bukgu.gwangju_bukgu_library_detail_url(current.identity, 1)
    ] = _detail_html(current, button=False)
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "controls changed" in meta["configured_collection_error"]


def test_page_recheck_and_detail_limit_fail_closed() -> None:
    fixture = HtmlFixture()
    original = fixture.pages[bukgu.GWANGJU_BUKGU_LIBRARY_CANONICAL_URL]
    first_calls = 0

    def drifting_fetcher(
        session: object, url: str, timeout: int
    ) -> str:
        nonlocal first_calls
        html = fixture(session, url, timeout)
        if url == bukgu.GWANGJU_BUKGU_LIBRARY_CANONICAL_URL:
            first_calls += 1
            if first_calls >= 2:
                return original.replace("북구 문해력 교실", "변경된 강좌", 1)
        return html

    rows, _, meta = bukgu.collect_gwangju_bukgu_library_education(
        _target(),
        max_pages=10,
        detail_limit=10,
        today="2026-07-21",
        fetcher=drifting_fetcher,
        session_factory=DummySession,
    )
    assert rows == []
    assert "page-one recheck changed" in meta["configured_collection_error"]

    fixture = HtmlFixture()
    rows, _, meta = bukgu.collect_gwangju_bukgu_library_education(
        _target(),
        max_pages=10,
        detail_limit=4,
        today="2026-07-21",
        fetcher=fixture,
        session_factory=DummySession,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "5 required current/future details" in meta[
        "configured_collection_error"
    ]


def test_get_only_and_source_never_reads_forbidden_sections() -> None:
    source = inspect.getsource(bukgu)
    assert ".post(" not in source
    assert "verify=False" not in source
    assert "verify = False" not in source
    assert "allow_redirects=False" in source

    detail_source = inspect.getsource(bukgu._parse_detail)
    boundary_source = inspect.getsource(
        bukgu._discard_forbidden_detail_sections
    )
    assert ".get_text(" not in detail_source
    assert ".stripped_strings" not in detail_source
    assert ".get_text(" not in boundary_source
    assert ".stripped_strings" not in boundary_source
    assert "article.extract()" in boundary_source
    assert "applicant_roster.extract()" in boundary_source
    assert inspect.getsource(bukgu._validate_detail_form).count(
        "recursive=False"
    ) == 1


@pytest.mark.skipif(
    os.getenv("MOONCEN_RUN_GWANGJU_BUKGU_LIBRARY_LIVE") != "1",
    reason="set MOONCEN_RUN_GWANGJU_BUKGU_LIBRARY_LIVE=1 for the live audit",
)
def test_live_complete_bukgu_library_snapshot() -> None:
    rows, parser, meta = bukgu.collect_gwangju_bukgu_library_education(
        _target(),
        timeout=30,
        max_pages=180,
        detail_limit=200,
        today="2026-07-21",
    )
    assert parser == bukgu.GWANGJU_BUKGU_LIBRARY_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] >= 1530
    assert meta["source_rows"] == meta["source_total"]
    assert meta["data_pages"] >= 153
    assert meta["sentinel_page"] == meta["declared_total_pages"] + 1
    assert meta["sentinel_pages"] == 1
    assert meta["identity_duplicate_count"] == 0
    assert set(meta["source_branch_counts"]) == set(
        bukgu.GWANGJU_BUKGU_LIBRARY_BRANCHES
    )
    assert meta["current_count"] == len(rows)
    assert meta["detail_pages"] == len(rows)
    assert meta["applicant_roster_sections_skipped"] == len(rows)
    assert meta["full_snapshot_validated"] is True
    assert all(
        set(row["raw_fields"]) == bukgu._SAFE_RAW_FIELDS for row in rows
    )
    persisted = repr((rows, meta))
    assert "source_html" not in persisted
    assert "approval_roster" not in persisted
    assert "waiting_roster" not in persisted
