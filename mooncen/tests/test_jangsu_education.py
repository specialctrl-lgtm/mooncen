from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import os
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlparse

import pytest

from Crawler import municipal_jangsu as jangsu


TARGET = {
    "provider": jangsu.JANGSU_PROVIDER,
    "url": jangsu.JANGSU_CANONICAL_URL,
}


@dataclass(frozen=True)
class SyntheticCourse:
    identity: str
    source_sequence: int
    title: str
    branch: str
    category: str
    raw_status: str
    apply_start: str
    apply_end: str
    event_start: str
    event_end: str
    waitlist_total: int
    capacity_current: int
    capacity_total: int
    target: str
    venue: str


def _courses() -> list[SyntheticCourse]:
    branch_names = [item.name for item in jangsu.JANGSU_BRANCHES]
    rows: list[SyntheticCourse] = []
    for index in range(23):
        if index == 0:
            raw_status = "접수예정"
            apply_start, apply_end = "2026-07-24", "2026-08-01"
            event_start = event_end = "2026-08-05"
        elif index in {1, 2}:
            raw_status = "접수중"
            apply_start, apply_end = "2026-07-20", "2026-07-31"
            event_start = event_end = f"2026-08-{5 + index:02d}"
        elif index == 3:
            raw_status = "강좌중"
            apply_start, apply_end = "2026-06-01", "2026-06-15"
            event_start, event_end = "2026-07-20", "2026-08-20"
        elif index == 4:
            raw_status = "접수마감"
            apply_start, apply_end = "2026-07-01", "2026-07-31"
            event_start = event_end = "2026-08-10"
        else:
            raw_status = "강좌마감"
            apply_start, apply_end = "2025-01-01", "2025-01-15"
            event_start = event_end = "2025-02-01"
        branch = branch_names[index % len(branch_names)]
        category = jangsu.JANGSU_CATEGORIES[index % len(jangsu.JANGSU_CATEGORIES)]
        rows.append(
            SyntheticCourse(
                identity=f"GJRE{9_000_023 - index:07d}",
                source_sequence=23 - index,
                title=f"장수 합성 강좌 {index + 1:02d}",
                branch=branch,
                category=category,
                raw_status=raw_status,
                apply_start=apply_start,
                apply_end=apply_end,
                event_start=event_start,
                event_end=event_end,
                waitlist_total=index % 4,
                capacity_current=index % 13,
                capacity_total=12,
                target="장수군민" if index % 2 else "관내 청소년",
                venue=f"{branch} 강의실",
            )
        )
    return rows


def _options(values: tuple[tuple[str, str], ...]) -> str:
    return "".join(f'<option value="{value}">{name}</option>' for value, name in values)


def _list_form(total: int) -> str:
    return f"""
    <form name="listForm" action="{jangsu.JANGSU_PATH}?menuCd={jangsu.JANGSU_LIST_MENU}">
      <input type="hidden" name="menuCd" value="{jangsu.JANGSU_LIST_MENU}">
      <input type="hidden" name="pageIndex" value="1">
      <div class="boardList-total"><p class="totalTxt">전체 <strong>{total}</strong> 건</p></div>
      <select id="searchType" name="searchType">
        {_options((("", "전체"), ("1", "접수중"), ("2", "강좌중")))}
      </select>
      <select name="bunya">
        {_options((("", "분야선택"),) + jangsu.JANGSU_CATEGORY_FILTERS)}
      </select>
      <select id="searchType" name="searchType">
        {_options((("RE_NAME", "강좌명"), ("GANGSA_NM", "강사명"), ("SANGSE_INFO", "상세내용")))}
      </select>
    """


def _row_html(course: SyntheticCourse, requested_page: int) -> str:
    detail_query = [("menuCd", jangsu.JANGSU_DETAIL_MENU)]
    application_query = [("menuCd", jangsu.JANGSU_APPLICATION_MENU)]
    if requested_page > 1:
        detail_query.append(("pageIndex", str(requested_page)))
        application_query.append(("pageIndex", str(requested_page)))
    detail_query.append(("reUniqId", course.identity))
    application_query.append(("reUniqId", course.identity))
    if course.raw_status == "접수중":
        status = (
            f'<a href="{jangsu.JANGSU_PATH}?{urlencode(application_query)}">'
            f'<span class="btn_st btn_stbg01">{course.raw_status}</span></a>'
        )
    else:
        classes = " ".join(jangsu.JANGSU_STATUS_CLASSES[course.raw_status])
        status = f'<span class="{classes}">{course.raw_status}</span>'
    return f"""
    <tr>
      <td>{course.source_sequence}</td>
      <td>{status}</td>
      <td>{course.category}</td>
      <td class="title">
        <a href="{jangsu.JANGSU_PATH}?{urlencode(detail_query)}">
          [{course.branch}] {course.title}
        </a>
      </td>
      <td>
        신청 : {course.apply_start} 10:00~{course.apply_end}<br>
        교육 : {course.event_start}~{course.event_end}
      </td>
      <td>{course.waitlist_total}</td>
    </tr>
    """


def _list_html(
    rows: list[SyntheticCourse],
    requested_page: int,
    *,
    advertised_total: int | None = None,
    bad_overflow: bool = False,
) -> str:
    total = len(rows) if advertised_total is None else advertised_total
    last = max(1, (len(rows) + jangsu.JANGSU_PAGE_SIZE - 1) // jangsu.JANGSU_PAGE_SIZE)
    actual = requested_page if requested_page <= last else last
    if bad_overflow and requested_page > last:
        actual = requested_page
    offset = (min(actual, last) - 1) * jangsu.JANGSU_PAGE_SIZE
    page_rows = rows[offset : offset + jangsu.JANGSU_PAGE_SIZE]
    body = "".join(_row_html(course, requested_page) for course in page_rows)
    headers = "".join(f"<th>{header}</th>" for header in jangsu.JANGSU_LIST_HEADERS)
    return f"""
    <html><body>
      {_list_form(total)}
      <div class="board-list">
        <table class="list01">
          <thead><tr>{headers}</tr></thead>
          <tbody>{body}</tbody>
        </table>
      </div>
      <div class="bbs_page">
        <span class="on"><a title="현재 페이지">{actual}</a></span>
        <span><a href="#" onclick="linkPage({last}); return false;">마지막</a></span>
      </div>
      </form>
    </body></html>
    """


def _detail_html(
    course: SyntheticCourse,
    *,
    title: str | None = None,
    omit_application: bool = False,
) -> str:
    fields = (
        ("분야", course.category),
        ("상태", course.raw_status),
        ("수강료", "없음"),
        ("교재비용", "무료"),
        ("수강인원", f"{course.capacity_current}명/{course.capacity_total}명"),
        ("교육시설", f"{course.branch} 시설"),
        ("교육기간", f"{course.event_start}~{course.event_end}"),
        ("강의시간", "매주 화요일 10:00~12:00(2시간)"),
        ("접수기간", f"{course.apply_start}~{course.apply_end}"),
        ("대상", course.target),
        ("접수방법", "온라인"),
        ("교육장소", course.venue),
        ("담당자", "개인담당자"),
        ("문의처", "063-350-1234"),
        ("강사명", "개인강사"),
        ("강사소개", "저장하면 안 되는 강사소개"),
    )
    dl_html = "".join(f"<dl><dt>{label}</dt><dd>{value}</dd></dl>" for label, value in fields)
    apply = ""
    if course.raw_status == "접수중" and not omit_application:
        apply = (
            f'<a class="write" href="{jangsu.JANGSU_PATH}?'
            f'menuCd={jangsu.JANGSU_APPLICATION_MENU}&amp;reUniqId={course.identity}">'
            "<span>신청</span></a>"
        )
    return f"""
    <html><body>
      <div class="boardViewWrap">
        <div class="bdvTitWrap"><p class="bdvTit">
          [{course.branch}] {title or course.title}
        </p></div>
        <div class="bdvInfo">{dl_html[:len(dl_html)]}</div>
        <div class="bdvCntWrap">
          저장하면 안 되는 상세 본문
          <a href="/private/fileDown">개인정보 신청서.hwp</a>
          <img src="/private/image.jpg">
        </div>
      </div>
      <div class="btn-wrap type01 tr">
        {apply}
        <a class="list" href="{jangsu.JANGSU_PATH}?menuCd={jangsu.JANGSU_LIST_MENU}">
          <span>목록</span>
        </a>
      </div>
    </body></html>
    """


class FakeResponse:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = status_code
        self.history: list[object] = []


class SyntheticBackend:
    def __init__(
        self,
        *,
        advertised_total_delta: int = 0,
        bad_overflow: bool = False,
        duplicate_identity: bool = False,
        drift_page_one: bool = False,
        detail_title_mismatch: str = "",
        omit_application: str = "",
        detail_http_failure: str = "",
    ):
        self.courses = _courses()
        if duplicate_identity:
            self.courses[-1] = replace(
                self.courses[-1],
                identity=self.courses[-2].identity,
            )
        self.advertised_total_delta = advertised_total_delta
        self.bad_overflow = bad_overflow
        self.drift_page_one = drift_page_one
        self.detail_title_mismatch = detail_title_mismatch
        self.omit_application = omit_application
        self.detail_http_failure = detail_http_failure
        self.urls: list[str] = []
        self._page_one_calls = 0
        self._lock = Lock()

    def response(self, url: str) -> FakeResponse:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        with self._lock:
            self.urls.append(url)
        menu = query.get("menuCd")
        if menu == jangsu.JANGSU_APPLICATION_MENU:
            raise AssertionError("application endpoint must never be fetched")
        if menu == jangsu.JANGSU_DETAIL_MENU:
            identity = query["reUniqId"]
            course = next(row for row in self.courses if row.identity == identity)
            if identity == self.detail_http_failure:
                return FakeResponse(url, "temporary failure", status_code=500)
            title = "변조된 상세 제목" if identity == self.detail_title_mismatch else None
            return FakeResponse(
                url,
                _detail_html(
                    course,
                    title=title,
                    omit_application=identity == self.omit_application,
                ),
            )
        assert menu == jangsu.JANGSU_LIST_MENU
        page = int(query.get("pageIndex", "1"))
        html = _list_html(
            self.courses,
            page,
            advertised_total=len(self.courses) + self.advertised_total_delta,
            bad_overflow=self.bad_overflow,
        )
        if page == 1:
            with self._lock:
                self._page_one_calls += 1
                call_number = self._page_one_calls
            if self.drift_page_one and call_number > 1:
                html = html.replace(self.courses[0].title, "변조된 첫 페이지 제목", 1)
        return FakeResponse(url, html)


class FakeSession:
    def __init__(self, backend: SyntheticBackend):
        self.backend = backend
        self.closed = False

    def get(self, url: str, timeout: int, allow_redirects: bool = False) -> FakeResponse:
        assert timeout > 0
        assert allow_redirects is False
        return self.backend.response(url)

    def close(self) -> None:
        self.closed = True


def _factory(backend: SyntheticBackend):
    return lambda: FakeSession(backend)


def _collect(backend: SyntheticBackend, **kwargs):
    options = {
        "max_pages": 10,
        "detail_limit": 20,
        "max_workers": 3,
    }
    options.update(kwargs)
    return jangsu.collect_jangsu_education(
        TARGET,
        today="2026-07-23",
        session_factory=_factory(backend),
        **options,
    )


def test_incumbent_provider_candidate_retarget_and_owner_boundaries() -> None:
    assert jangsu.is_target(TARGET)
    assert not jangsu.is_target(
        {
            "provider": jangsu.JANGSU_PROVIDER,
            "url": jangsu.JANGSU_REVIEW_DETAIL_URL,
        }
    )
    assert not jangsu.is_target(
        {
            "provider": "MUNI_WWW_JANGSU_GO_KR_66C83E96",
            "url": "https://www.jangsu.go.kr/index.jangsu?contentsSid=454",
        }
    )
    audit = jangsu.JANGSU_CANDIDATE_AUDIT
    assert audit[jangsu.JANGSU_CANONICAL_CANDIDATE_ID]["provider"] == jangsu.JANGSU_PROVIDER
    assert "retarget_expired_single" in audit[jangsu.JANGSU_REVIEW_DETAIL_CANDIDATE_ID]["decision"]
    assert "duplicate_rendering" in audit[jangsu.JANGSU_PARENT_ALIAS_CANDIDATE_ID]["decision"]
    decisions = {item["decision"] for item in jangsu.JANGSU_OWNER_BOUNDARIES}
    assert "exclude_separate_active_municipal_library_IDX_program_owner" in decisions
    assert "exclude_separate_experience_calendar_owner" in decisions
    assert "exclude_pii_bearing_reservation_lookup" in decisions


def test_complete_clamped_snapshot_details_controls_and_privacy() -> None:
    backend = SyntheticBackend()
    rows, parser, meta = _collect(backend)

    assert parser == jangsu.JANGSU_PARSER
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 5
    assert meta["source_requests"] == 12
    assert meta["request_attempts"] == 12
    assert meta["list_requests"] == 7
    assert meta["detail_pages"] == 5
    assert meta["advertised_total"] == 23
    assert meta["advertised_last_page"] == 3
    assert meta["data_pages"] == 3
    assert meta["page_counts"] == [10, 10, 3]
    assert meta["overflow_page"] == 4
    assert meta["overflow_actual_page"] == 3
    assert meta["overflow_clamp_verified"]
    assert meta["source_rows"] == 23
    assert meta["source_sequence_duplicate_count"] == 0
    assert meta["current_source_count"] == 5
    assert meta["expired_source_count"] == 18
    assert meta["source_status_counts"] == {
        "접수예정": 1,
        "접수중": 2,
        "강좌중": 1,
        "접수마감": 1,
        "강좌마감": 18,
    }
    assert meta["status_counts"] == {"SCHEDULED": 1, "OPEN": 2, "CLOSED": 2}
    assert meta["application_control_count"] == 2
    assert meta["actionable_application_count"] == 2
    assert meta["application_endpoints_called"] == 0
    assert meta["reservation_lookup_endpoints_called"] == 0
    assert meta["attachment_endpoints_called"] == 0
    assert meta["privacy_violations"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["page1_rechecked"]
    assert meta["last_page_rechecked"]
    assert meta["overflow_rechecked"]
    assert meta["details_complete"]
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]

    expected = {
        f"{jangsu.JANGSU_PROVIDER}:gjre:GJRE{9_000_023 - index:07d}"
        for index in range(5)
    }
    assert {row["provider_course_id"] for row in rows} == expected
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["address"] == row["venue_address"] == "" for row in rows)
    assert all(row["application_method"] == "온라인" for row in rows)
    assert all(
        bool(row["application_url"]) == (row["status"] == "OPEN") for row in rows
    )
    payload = repr(rows)
    for forbidden in (
        "개인담당자",
        "063-350-1234",
        "개인강사",
        "저장하면 안 되는 강사소개",
        "저장하면 안 되는 상세 본문",
        "개인정보 신청서.hwp",
    ):
        assert forbidden not in payload
    assert not any(
        f"menuCd={jangsu.JANGSU_APPLICATION_MENU}" in url for url in backend.urls
    )


@pytest.mark.parametrize(
    ("backend", "error_fragment"),
    (
        (SyntheticBackend(advertised_total_delta=1), "does not reconcile"),
        (SyntheticBackend(bad_overflow=True), "did not clamp"),
        (SyntheticBackend(duplicate_identity=True), "identity set"),
        (SyntheticBackend(drift_page_one=True), "stability recheck"),
        (
            SyntheticBackend(detail_title_mismatch="GJRE9000023"),
            "title drift",
        ),
        (
            SyntheticBackend(omit_application="GJRE9000022"),
            "control count drift",
        ),
        (
            SyntheticBackend(detail_http_failure="GJRE9000023"),
            "HTTP 500",
        ),
    ),
)
def test_contract_drift_fails_closed(
    backend: SyntheticBackend,
    error_fragment: str,
) -> None:
    rows, _, meta = _collect(backend)
    assert rows == []
    assert error_fragment in meta["configured_collection_error"]
    assert not meta["snapshot_complete"]


def test_caps_and_managed_session_requirement_fail_closed() -> None:
    rows, _, meta = jangsu.collect_jangsu_education(
        TARGET,
        today="2026-07-23",
    )
    assert rows == []
    assert meta["configured_collection_error"] == "managed session_factory injection is required"

    backend = SyntheticBackend()
    rows, _, meta = _collect(backend, max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"]
    assert "exceeds max_pages" in meta["configured_collection_error"]

    backend = SyntheticBackend()
    rows, _, meta = _collect(backend, detail_limit=4)
    assert rows == []
    assert meta["source_cap_reached"]
    assert "detail_limit" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0


def test_response_url_and_form_vocabulary_drift_fail_closed() -> None:
    class UrlDriftBackend(SyntheticBackend):
        def response(self, url: str) -> FakeResponse:
            response = super().response(url)
            response.url = url.replace("www.jangsu.go.kr", "jangsu.go.kr")
            return response

    rows, _, meta = _collect(UrlDriftBackend())
    assert rows == []
    assert "response URL drift" in meta["configured_collection_error"]

    class FormDriftBackend(SyntheticBackend):
        def response(self, url: str) -> FakeResponse:
            response = super().response(url)
            response.content = response.content.replace(
                "전문화교육".encode(),
                "변조된 분야".encode(),
                1,
            )
            return response

    rows, _, meta = _collect(FormDriftBackend())
    assert rows == []
    assert "category selector vocabulary drift" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_JANGSU_LIVE") != "1",
    reason="set RUN_JANGSU_LIVE=1 for the bounded official-source audit",
)
def test_live_exact_audit_baseline() -> None:
    rows, parser, meta = jangsu.collect_jangsu_education(
        TARGET,
        today="2026-07-23",
        allow_raw_requests_for_tests=True,
        timeout=30,
        max_pages=70,
        detail_limit=50,
        max_workers=3,
    )
    assert parser == jangsu.JANGSU_PARSER
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 15
    assert meta["source_requests"] == 81
    assert meta["request_attempts"] == 81
    assert meta["list_requests"] == 66
    assert meta["detail_pages"] == 15
    assert meta["advertised_total"] == 613
    assert meta["advertised_last_page"] == 62
    assert meta["page_counts"] == [10] * 61 + [3]
    assert meta["overflow_page"] == 63
    assert meta["overflow_actual_page"] == 62
    assert meta["overflow_clamp_verified"]
    assert meta["source_rows"] == 613
    assert meta["source_sequence_duplicate_count"] == 303
    assert meta["source_identity_numeric_min"] == 43
    assert meta["source_identity_numeric_max"] == 757
    assert meta["source_status_counts"] == {
        "접수마감": 2,
        "접수중": 4,
        "강좌중": 9,
        "강좌마감": 598,
    }
    assert meta["source_category_counts"] == {
        "청소년문화": 111,
        "전문화교육": 3,
        "문화예술": 311,
        "평생교육": 134,
        "평생학습": 24,
        "독서문화": 23,
        "정보화교육": 7,
    }
    assert meta["source_branch_counts"] == {
        "청소년문화의집": 127,
        "농업기술센터": 2,
        "여성문화센터": 284,
        "장수군": 160,
        "도서관": 39,
        "농촌지원": 1,
    }
    assert meta["current_source_count"] == 15
    assert meta["expired_source_count"] == 598
    assert meta["status_counts"] == {"CLOSED": 11, "OPEN": 4}
    assert meta["branch_counts"] == {
        "여성문화센터": 9,
        "농업기술센터": 1,
        "청소년문화의집": 5,
    }
    assert meta["category_counts"] == {
        "문화예술": 9,
        "전문화교육": 1,
        "청소년문화": 5,
    }
    assert meta["application_control_count"] == 4
    assert meta["privacy_violations"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["snapshot_complete"]
    assert Counter(row["status"] for row in rows) == {"CLOSED": 11, "OPEN": 4}
