from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import inspect
import os

from bs4.element import Tag
import pytest
import requests

from Crawler import municipal_hwasun as hwasun


@dataclass
class Target:
    provider: str
    url: str


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    start: str
    end: str
    apply_start: str = "2026-06-01"
    apply_end: str = "2026-06-30"
    category: str = "취미/교양"
    branch: str = "화순군청"
    target: str = "19세 이상 화순군민"
    fee: str = "수강료 및 재료비 무료"
    current: int | None = 5
    capacity: int = 10
    schedule: str = "매주 화요일 / 10:00 ~ 12:00"
    method: str = "온라인접수"
    source_status: str = "모집마감"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class DummyResponse:
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.content = body.encode("utf-8")


def _courses() -> list[Course]:
    values: list[Course] = []
    for identity in range(30, 7, -1):
        if identity == 30:
            end = "2026-09-01"
        elif identity == 29:
            end = "2026-07-21"
        elif identity == 20:
            end = "2026-08-06"
        elif identity == 19:
            end = "2026-07-30"
        else:
            end = "2026-07-20"
        start = "2026-07-01" if end >= "2026-07-21" else "2026-06-01"
        values.append(
            Course(
                str(identity),
                f"화순 평생학습 강좌 {identity}",
                start,
                end,
                branch="" if identity == 10 else "화순군청",
                target="" if identity == 9 else "19세 이상 화순군민",
                current=(None if identity <= 15 else identity % 8),
                capacity=(0 if identity == 10 else 10 + identity % 3),
            )
        )
    return values


def _form() -> str:
    return """
      <form name="insForm" method="post"
            action="/lll/edu.do?S=lll&amp;M=010101000000">
        <input type="hidden" name="pageCnt" value="10">
        <input type="hidden" name="S" value="lll">
        <input type="hidden" name="M" value="010101000000">
        <select name="keyField2"><option value="">전체</option></select>
        <select name="keyField"><option value="">전체</option></select>
        <input name="search" value="">
      </form>
    """


def _list_row(course: Course) -> str:
    href = (
        "/lll/edu.do?S=lll&amp;M=010101000000"
        f"&amp;act=detail&amp;list_no={course.identity}"
    )
    if course.current is None:
        count = (
            '<span class="mo_vis">정원</span>'
            f'<span class="total_c">{course.capacity}명</span>'
        )
    else:
        count = (
            '<span class="mo_vis">신청/정원</span>'
            f'<span class="fb2">{course.current}명</span> / '
            f'<span class="total_c">{course.capacity}명</span>'
        )
    status_class = {"모집중": "tag_01", "모집마감": "tag_02"}.get(
        course.source_status, "tag_unknown"
    )
    return f"""
      <tr>
        <td><span class="{status_class}">{escape(course.source_status)}</span></td>
        <td><a href="{href}">
          <span>{escape(course.category)}</span>
          <strong>{escape(course.title)}</strong>
          <span>신청기간 : {course.apply_start} ~ {course.apply_end}</span>
          <span>교육기간 : {course.start} ~ {course.end}</span>
        </a></td>
        <td><span class="mo_vis">교육기관</span>{escape(course.branch)}</td>
        <td><span class="mo_vis">교육대상</span>{escape(course.target)}</td>
        <td>{count}</td>
        <td><span class="mo_vis">수강료/재료비</span>{escape(course.fee)}</td>
      </tr>
    """


def _list_html(
    courses: list[Course],
    page: int,
    *,
    empty: bool = False,
    total: int | None = None,
) -> str:
    declared = len(courses) if total is None else total
    if empty:
        body = '<tr><td colspan="6">강좌 정보가 없습니다.</td></tr>'
        paging = ""
    else:
        start = (page - 1) * hwasun.HWASUN_PAGE_SIZE
        rows = courses[start : start + hwasun.HWASUN_PAGE_SIZE]
        body = "".join(_list_row(course) for course in rows)
        paging = f'<div class="pagination"><a class="active">{page}</a></div>'
    return f"""<!doctype html>
      <html><head><title>화순군 평생학습관</title></head><body>
      {_form()}
      <span class="fb3">{declared}건</span>
      <table class="le_list_table">
        <caption>평생학습 강좌 정보를 번호, 강좌명/신청기간/교육기간, 교육기관,
          교육대상, 신청/정원, 수강료/재료비, 모집현황 순으로 안내하는 표입니다.</caption>
        <thead><tr>
          <th>모집현황</th><th>강좌명/신청기간/교육기간</th><th>교육기관</th>
          <th>교육대상</th><th>신청/정원</th><th>수강료/재료비</th>
        </tr></thead>
        <tbody>{body}</tbody>
      </table>{paging}</body></html>"""


def _row(label: str, value: str, *, private: bool = False) -> str:
    marker = ' data-private="true"' if private else ""
    return (
        f"<tr><th>{escape(label)}</th>"
        f"<td{marker}>{escape(value)}</td></tr>"
    )


def _detail_html(course: Course, *, bad_control: bool = False) -> str:
    assert course.current is not None
    if course.source_status == "모집중":
        application_identity = "999999" if bad_control else course.identity
        application_url = hwasun.hwasun_application_url(application_identity).replace(
            "&", "&amp;"
        )
        control = (
            f'<a class="apply" href="{application_url}"><span>신청하기</span></a>'
        )
    else:
        control = (
            '<a class="wait" href="javascript:void(0);"><span>모집중</span></a>'
            if bad_control
            else '<a class="wait" href="javascript:void(0);"><span>모집마감</span></a>'
        )
    schedule = "".join(
        (
            _row("모집기간", f"{course.apply_start} ~ {course.apply_end}"),
            _row("교육기간", f"{course.start} ~ {course.end}"),
            _row("교육시간", course.schedule),
            _row("교육기관", course.branch),
            _row("접수방법", course.method),
            _row("모집인원/정원", f"{course.current}명 / {course.capacity}명"),
            _row("대기인원/정원", "4명 / 5명", private=True),
            _row("문의사항", "061-379-3351", private=True),
        )
    )
    detail = "".join(
        (
            _row("강좌분류", course.category),
            _row("교육대상", course.target),
            _row("수강료 / 재료비", course.fee),
            _row("강사명", "비공개강사", private=True),
            _row("첨부파일", "private-roster.hwp", private=True),
            _row("홈페이지", "https://private.example/", private=True),
            _row("교육내용", "자유서술 개인정보 가능 영역", private=True),
        )
    )
    return f"""<!doctype html><html><body>
      <div class="le_v_title"><p>{escape(course.title)}</p>
        <div class="state_wp"><span class="state">{escape(course.source_status)}</span></div>
      </div>
      <div class="le_info_box tbox"><div class="table_box">
        <table class="le_v_table">
          <caption>모집일정, 강의일정, 교육시간, 교육기관, 접수방법, 모집인원/정원,
            문의사항 정보를 제공하는 표입니다.</caption>
          {schedule}
        </table>
      </div><div class="btn_box">{control}
        <a href="/lll/edu.do?S=lll&amp;M=010101000000">목록</a>
      </div></div>
      <div class="le_info_box mbox"><div class="table_box">
        <table class="le_v_table">
          <caption>강좌상세정보를 강좌분류, 교육대상, 수강료, 강사명, 교육내용,
            첨부파일, 홈페이지, 기타안내 순으로 안내하는 표입니다.</caption>
          {detail}
        </table>
      </div></div>
      <div class="le_info_box bbox"><div class="table_box">
        <table class="le_v_table" data-private="true">
          <caption>수강신청현황을 번호, 접수상채, 이름, 연락처, 접수일 순으로 안내하는 표입니다.</caption>
          <tr><th>이름</th><th>연락처</th></tr>
          <tr><td>홍길동</td><td>010-1234-5678 PII-SECRET-NEVER-READ</td></tr>
        </table>
      </div></div>
    </body></html>"""


class FixtureSite:
    def __init__(
        self,
        courses: list[Course] | None = None,
        *,
        bad_sentinel: bool = False,
        drift_first_recheck: bool = False,
        bad_detail_control: str = "",
    ) -> None:
        self.courses = list(courses or _courses())
        self.bad_sentinel = bad_sentinel
        self.drift_first_recheck = drift_first_recheck
        self.bad_detail_control = bad_detail_control
        self.calls: list[str] = []
        self.list_call_counts: Counter[int] = Counter()
        self.by_id = {course.identity: course for course in self.courses}

    def fetch(self, session: DummySession, url: str, timeout: int) -> str:
        del session, timeout
        self.calls.append(url)
        if url == hwasun.HWASUN_CANONICAL_URL:
            page = 1
        else:
            page = next(
                (
                    value
                    for value in range(2, 20)
                    if url == hwasun.hwasun_list_url(value)
                ),
                0,
            )
        last = (len(self.courses) + hwasun.HWASUN_PAGE_SIZE - 1) // hwasun.HWASUN_PAGE_SIZE
        if page:
            self.list_call_counts[page] += 1
            if page == last + 1:
                if self.bad_sentinel:
                    return _list_html(self.courses, last, total=len(self.courses))
                return _list_html(
                    self.courses, page, empty=True, total=len(self.courses)
                )
            data = self.courses
            if (
                self.drift_first_recheck
                and page == 1
                and self.list_call_counts[page] > 1
            ):
                data = list(data)
                data[0] = replace(data[0], title="재검사에서 바뀐 제목")
            return _list_html(data, page, total=len(self.courses))
        for identity, course in self.by_id.items():
            if url == hwasun.hwasun_detail_url(identity):
                return _detail_html(
                    course, bad_control=identity == self.bad_detail_control
                )
        raise AssertionError(f"unexpected URL: {url}")


def _target() -> Target:
    return Target(hwasun.HWASUN_PROVIDER, hwasun.HWASUN_CANONICAL_URL)


def _collect(
    site: FixtureSite,
    **kwargs: object,
) -> tuple[list[dict[str, object]], str, dict[str, object]]:
    return hwasun.collect_hwasun_education(
        _target(),
        today="2026-07-21",
        session_factory=DummySession,
        fetcher=site.fetch,
        sleeper=lambda _: None,
        **kwargs,
    )


def test_exact_canonical_target_and_owner_boundaries() -> None:
    assert hwasun.is_hwasun_education_target(_target())
    assert not hwasun.is_hwasun_education_target(
        Target(
            hwasun.HWASUN_PROVIDER,
            "https://www.hwasun.go.kr/lll/edu.do?M=010101000000&S=lll",
        )
    )
    assert not hwasun.is_hwasun_education_target(
        Target("WRONG_PROVIDER", hwasun.HWASUN_CANONICAL_URL)
    )
    assert hwasun.is_hwasun_home_page_alias_target(
        Target(hwasun.HWASUN_PROVIDER, hwasun.HWASUN_HOME_PAGE_ALIAS_URL)
    )
    assert hwasun.is_hwasun_memcheck_pii_alias_target(
        Target(hwasun.HWASUN_PROVIDER, hwasun.HWASUN_MEMCHECK_PII_URL)
    )
    assert hwasun.is_hwasun_separate_or_excluded_owner_target(
        Target(hwasun.HWASUN_HFCT_PROVIDER, hwasun.HWASUN_HFCT_URL)
    )
    assert hwasun.is_hwasun_separate_or_excluded_owner_target(
        Target(hwasun.HWASUN_FORESTTRIP_PROVIDER, hwasun.HWASUN_FORESTTRIP_URL)
    )
    assert hwasun.HWASUN_OWNER_BOUNDARY_AUDIT[hwasun.HWASUN_HFCT_PROVIDER][
        "decision"
    ] == "keep_separate_arts_foundation_owner"


def test_complete_snapshot_scans_past_expired_rows_and_verifies_current_details() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == hwasun.HWASUN_PARSER
    assert [row["provider_course_id"] for row in rows] == ["30", "29", "20", "19"]
    assert meta["declared_total"] == 23
    assert meta["data_pages"] == 3
    assert meta["page_counts"] == {1: 10, 2: 10, 3: 3}
    assert meta["required_list_requests"] == 6
    assert meta["list_requests"] == 6
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 2
    assert meta["source_rows"] == 23
    assert meta["current_source_count"] == 4
    assert meta["expired_count"] == 19
    assert meta["non_monotonic_end_date_detected"] is True
    assert meta["current_after_first_expired_ids"] == ["20", "19"]
    assert meta["page_2_or_later_current_ids"] == ["20", "19"]
    assert meta["detail_attempts"] == meta["detail_pages"] == 4
    assert meta["roster_sections_discarded"] == 4
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["sessions_created"] == 1
    assert site.list_call_counts == Counter({1: 2, 3: 2, 2: 1, 4: 1})
    assert hwasun.HWASUN_MEMCHECK_PII_URL not in site.calls
    assert hwasun.HWASUN_HOME_PAGE_ALIAS_URL not in site.calls
    assert hwasun.HWASUN_HFCT_URL not in site.calls
    assert hwasun.HWASUN_FORESTTRIP_URL not in site.calls
    assert all(row["branch"] == "화순군청" for row in rows)
    assert all(row["status"] == "CLOSED" for row in rows)
    assert all(row["reservation_available"] is False for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(
        row["raw_fields"]["applicant_roster_structurally_discarded"] is True
        for row in rows
    )
    assert "PII-SECRET-NEVER-READ" not in repr(rows)
    assert "061-379-3351" not in repr(rows)
    assert "비공개강사" not in repr(rows)


def test_private_cells_are_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    original = Tag.get_text

    def guarded_get_text(self: Tag, *args: object, **kwargs: object) -> str:
        if self.has_attr("data-private") or self.find(attrs={"data-private": "true"}):
            raise AssertionError("private/roster text was accessed")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Tag, "get_text", guarded_get_text)
    rows, _, meta = _collect(FixtureSite())
    assert len(rows) == 4
    assert meta["snapshot_complete"] is True


def test_open_status_requires_identity_bound_application_control() -> None:
    courses = _courses()
    courses[0] = replace(courses[0], source_status="모집중")
    rows, _, meta = _collect(FixtureSite(courses))

    opened = next(row for row in rows if row["provider_course_id"] == "30")
    assert opened["status"] == "OPEN"
    assert opened["reservation_available"] is True
    assert opened["application_url"] == hwasun.hwasun_application_url("30")
    assert (
        opened["raw_fields"]["application_control_contract"]
        == "open_mem_form_control_bound_to_official_list_no"
    )
    assert meta["current_status_counts"] == {"OPEN": 1, "CLOSED": 3}


def test_open_application_control_with_different_identity_fails_closed() -> None:
    courses = _courses()
    courses[0] = replace(courses[0], source_status="모집중")
    rows, _, meta = _collect(FixtureSite(courses, bad_detail_control="30"))

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "identity-bound application control" in str(
        meta["configured_collection_error"]
    )


def test_unknown_source_status_fails_closed() -> None:
    courses = _courses()
    courses[0] = replace(courses[0], source_status="접수가능")
    rows, _, meta = _collect(FixtureSite(courses))

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "unaudited status 접수가능" in str(meta["configured_collection_error"])


def test_ssl_and_block_response_rebuild_session_then_complete() -> None:
    site = FixtureSite()
    sessions: list[DummySession] = []
    first_page_attempts = 0
    sleeps: list[float] = []

    def factory() -> DummySession:
        session = DummySession()
        sessions.append(session)
        return session

    def flaky(session: DummySession, url: str, timeout: int) -> object:
        nonlocal first_page_attempts
        if url == hwasun.HWASUN_CANONICAL_URL and first_page_attempts < 2:
            first_page_attempts += 1
            if first_page_attempts == 1:
                raise requests.exceptions.SSLError("WRONG_VERSION_NUMBER")
            return DummyResponse(400, "Request Blocked")
        return site.fetch(session, url, timeout)

    rows, _, meta = hwasun.collect_hwasun_education(
        _target(),
        today="2026-07-21",
        session_factory=factory,
        fetcher=flaky,
        sleeper=sleeps.append,
    )
    assert len(rows) == 4
    assert meta["snapshot_complete"] is True
    assert meta["network_retry_count"] == 2
    assert meta["sessions_created"] == 3
    assert meta["http_attempts"] == 12
    assert len(sleeps) == 2
    assert sessions[0].closed is True
    assert sessions[1].closed is True
    assert sessions[2].closed is True


def test_retry_exhaustion_discards_snapshot() -> None:
    sessions: list[DummySession] = []

    def factory() -> DummySession:
        session = DummySession()
        sessions.append(session)
        return session

    def blocked(session: DummySession, url: str, timeout: int) -> DummyResponse:
        del session, url, timeout
        return DummyResponse(400, "Request Blocked")

    rows, _, meta = hwasun.collect_hwasun_education(
        _target(),
        today="2026-07-21",
        fetch_attempts=3,
        session_factory=factory,
        fetcher=blocked,
        sleeper=lambda _: None,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["http_attempts"] == 3
    assert meta["network_retry_count"] == 2
    assert meta["sessions_created"] == 3
    assert "fetch failed after 3 attempts" in str(meta["configured_collection_error"])
    assert all(session.closed for session in sessions)


@pytest.mark.parametrize(
    ("site", "error"),
    [
        (FixtureSite(bad_sentinel=True), "sentinel"),
        (FixtureSite(drift_first_recheck=True), "first-page stability"),
        (FixtureSite(bad_detail_control="30"), "closed status control"),
    ],
)
def test_contract_drift_fails_closed(site: FixtureSite, error: str) -> None:
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error in str(meta["configured_collection_error"])


def test_duplicate_identity_and_caps_fail_closed() -> None:
    courses = _courses()
    courses[1] = replace(courses[1], identity=courses[0].identity)
    rows, _, meta = _collect(FixtureSite(courses))
    assert rows == []
    assert meta["identity_duplicate_count"] == 1
    assert "duplicate official identities" in str(meta["configured_collection_error"])

    rows, _, meta = _collect(FixtureSite(), max_pages=5)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in str(meta["configured_collection_error"])

    rows, _, meta = _collect(FixtureSite(), detail_limit=3)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in str(meta["configured_collection_error"])


def test_dedupe_may_not_change_official_identity_cardinality() -> None:
    rows, _, meta = _collect(
        FixtureSite(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed official identity" in str(meta["configured_collection_error"])


def test_static_security_and_sequential_request_contract() -> None:
    source = inspect.getsource(hwasun)
    detail_source = inspect.getsource(hwasun._parse_detail)
    assert "verify=False" not in source
    assert "ThreadPoolExecutor" not in source
    assert '"Referer": "https://www.hwasun.go.kr/lll/"' in source
    assert "allow_redirects=False" in source
    assert "roster.extract()" in detail_source
    assert detail_source.index("roster.extract()") < detail_source.index("_text(title_node)")
    assert "memCheck" not in inspect.getsource(hwasun.collect_hwasun_education)
    audit = hwasun.HWASUN_DISCOVERY_AUDIT
    assert audit["initial_current_count_assumption"] == 9
    assert audit["corrected_current_count"] == 14
    assert audit["page_2_current_ids_found_by_full_scan"] == (
        "263", "261", "260", "259",
    )
    assert "non-monotonic" in audit["correction_reason"]


@pytest.mark.skipif(
    os.getenv("MOONCEN_RUN_HWASUN_LIVE") != "1",
    reason="set MOONCEN_RUN_HWASUN_LIVE=1 for the audited live contract test",
)
def test_hwasun_live_2026_07_28_contract() -> None:
    rows, parser, meta = hwasun.collect_hwasun_education(
        _target(),
        today="2026-07-28",
        timeout=45,
        max_pages=100,
        detail_limit=100,
    )
    assert parser == hwasun.HWASUN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["declared_total"] == 260
    assert meta["data_pages"] == 26
    assert meta["page_counts"] == {page: 10 for page in range(1, 27)}
    assert meta["required_list_requests"] == meta["list_requests"] == 29
    assert meta["current_source_count"] == 14
    assert meta["detail_attempts"] == meta["detail_pages"] == 14
    assert meta["roster_sections_discarded"] == 14
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["non_monotonic_end_date_detected"] is True
    assert meta["page_2_or_later_current_ids"] == ["263", "261", "260", "259"]
    assert [row["provider_course_id"] for row in rows] == [
        "274", "273", "272", "271", "269", "270", "268",
        "267", "266", "265", "263", "261", "260", "259",
    ]
    assert all(row["branch"] == "화순군청" for row in rows)
    assert [row["status"] for row in rows].count("OPEN") == 5
    assert [row["status"] for row in rows].count("CLOSED") == 9
    assert all(row["raw_fields"]["application_control_verified"] for row in rows)
    assert "010-" not in repr(rows)
    assert "061-379-" not in repr(rows)
