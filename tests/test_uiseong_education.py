from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import os
from threading import Lock
from urllib.parse import parse_qsl, urlparse

import pytest

from Crawler import municipal_uiseong as uiseong


TARGET = {
    "provider": uiseong.UISEONG_PROVIDER,
    "url": uiseong.UISEONG_CANONICAL_URL,
}


@dataclass(frozen=True)
class SyntheticCourse:
    identity: str
    title: str
    branch_code: str
    branch: str
    raw_status: str
    category: str
    target: str
    apply_start: str
    apply_end: str
    event_start: str
    event_end: str
    capacity_current: int
    capacity_total: int
    waitlist_current: int
    waitlist_total: int


def _courses() -> list[SyntheticCourse]:
    branch_codes = (
        ["150"] * 25
        + ["57"] * 5
        + ["131"] * 4
        + ["65"] * 3
        + ["63"] * 2
        + ["61"] * 2
    )
    rows: list[SyntheticCourse] = []
    for index, branch_code in enumerate(branch_codes):
        if index == 0:
            raw_status = "접수대기"
            apply_start, apply_end = "2026-07-24", "2026-08-01"
            event_start = event_end = "2026-08-05"
        elif index in {1, 2}:
            raw_status = "접수중"
            apply_start, apply_end = "2026-07-01", "2026-07-30"
            event_start = event_end = f"2026-08-{5 + index:02d}"
        elif index < 6:
            raw_status = "접수마감"
            apply_start, apply_end = "2026-06-01", "2026-06-30"
            event_start = event_end = f"2026-07-{23 + index:02d}"
        else:
            raw_status = "접수마감"
            apply_start, apply_end = "2025-01-01", "2025-01-15"
            event_start = event_end = "2025-02-01"
        branch = uiseong.UISEONG_BRANCH_BY_CODE[branch_code].name
        rows.append(
            SyntheticCourse(
                identity=str(2041 - index),
                title=f"의성 합성 강좌 {index + 1:02d}",
                branch_code=branch_code,
                branch=branch,
                raw_status=raw_status,
                category=("독서" if branch_code == "61" else "기타"),
                target=("초등학생" if index % 2 else "성인"),
                apply_start=apply_start,
                apply_end=apply_end,
                event_start=event_start,
                event_end=event_end,
                capacity_current=index % 13,
                capacity_total=12,
                waitlist_current=index % 3,
                waitlist_total=4,
            )
        )
    return rows


def _option_html(
    options: tuple[tuple[str, str], ...],
    selected: str = "",
) -> str:
    return "".join(
        (
            f'<option value="{value}"'
            + (" selected" if value == selected and selected else "")
            + f">{name}</option>"
        )
        for value, name in options
    )


def _form(branch_code: str = "", status_code: str = "") -> str:
    branch_options = (("", "기관전체"),) + tuple(
        (branch.code, branch.name) for branch in uiseong.UISEONG_BRANCHES
    )
    return f"""
    <form id="frm" name="frm" method="post" action="">
      <input name="mnu_uid" value="670">
      <input name="returnUrl" value="/reserve/page.do">
      <input name="queryString" value="mnu_uid=670&amp;">
      <input name="cmd" value="">
      <select id="srchSite" name="srchSite">
        {_option_html(branch_options, branch_code)}
      </select>
      <select id="srchFld_parents" name="srchFld_parents">
        {_option_html((("", "분야전체"),) + uiseong.UISEONG_FIELD_FILTERS)}
      </select>
      <select id="srchFld" name="srchFld"></select>
      <select id="srchTrgt" name="srchTrgt">
        {_option_html((("", "교육대상전체"),) + uiseong.UISEONG_TARGET_FILTERS)}
      </select>
      <select id="srchStts" name="srchStts">
        {_option_html((("", "전체"),) + uiseong.UISEONG_STATUS_FILTERS, status_code)}
      </select>
    """


def _course_html(course: SyntheticCourse) -> str:
    status_class = uiseong.UISEONG_STATUS_CLASS[course.raw_status]
    return f"""
    <li>
      <a href="?cmd=2&amp;mnu_uid=670&amp;lctre_uid={course.identity}">
        <div class="div">
          <span class="type">{course.category}</span>
          <span class="org">{course.branch}</span>
        </div>
        <p class="tit">{course.title}</p>
        <ul class="dep_02">
          <li>신청 : {course.apply_start} ~ {course.apply_end}</li>
          <li>교육 : {course.event_start} ~ {course.event_end}</li>
          <li>교육대상 : {course.target}</li>
        </ul>
      </a>
      <ul class="num">
        <li><b>신청</b> {course.capacity_current}/{course.capacity_total}명</li>
        <li><b>후보</b> {course.waitlist_current}/{course.waitlist_total}명</li>
      </ul>
      <div class="status"><span class="{status_class}">{course.raw_status}</span></div>
    </li>
    """


def _pager(page: int, last: int, empty: bool, filtered: bool) -> str:
    if empty and not filtered:
        current = ""
    else:
        current = f'<strong title="현재 페이지">{page}</strong>'
    last_link = (
        ""
        if page == last
        else f'<a class="arrow last" href="?pageNo={last}&amp;mnu_uid=670&amp;">마지막</a>'
    )
    return f'<div class="paging">{current}{last_link}</div>'


def _list_html(
    rows: list[SyntheticCourse],
    page: int,
    *,
    branch_code: str = "",
    status_code: str = "",
) -> str:
    last = max(1, (len(rows) + uiseong.UISEONG_PAGE_SIZE - 1) // uiseong.UISEONG_PAGE_SIZE)
    offset = (page - 1) * uiseong.UISEONG_PAGE_SIZE
    page_rows = rows[offset : offset + uiseong.UISEONG_PAGE_SIZE]
    empty = not page_rows
    content = (
        f"<li>{uiseong.UISEONG_EMPTY_SENTINEL}</li>"
        if empty
        else "".join(_course_html(row) for row in page_rows)
    )
    filtered = bool(branch_code or status_code)
    return (
        "<html><body>"
        + _form(branch_code, status_code)
        + f'<div class="applyList"><ul>{content}</ul></div>'
        + _pager(page, last, empty, filtered)
        + "</form></body></html>"
    )


def _detail_html(
    course: SyntheticCourse,
    *,
    title: str | None = None,
) -> str:
    fields = (
        ("교육명", title or course.title),
        (
            "접수 일시",
            f"{course.apply_start} 09시 ~ {course.apply_end} 18시",
        ),
        (
            "교육 일시",
            f"{course.event_start}~{course.event_end} (10:00~12:00)",
        ),
        ("교육 요일", "토"),
        ("장소", f"{course.branch} 강의실"),
        ("교육대상", course.target),
        ("1회 교육시간", "2"),
        ("교육횟수", "1"),
        (
            "모집인원",
            f"신청정원 : {course.capacity_total}/후보정원:{course.waitlist_total}",
        ),
        ("수강료", "0"),
        ("재료", "개인 준비물"),
        ("재료비", "0"),
        ("강사명", "개인강사"),
        ("지역", "의성읍"),
        ("담당자", "담당자"),
        ("문의전화", "054-830-1234"),
        ("교육내용", "저장하면 안 되는 상세 본문"),
        ("주의사항", "저장하면 안 되는 주의사항"),
        ("첨부파일", "개인정보양식.hwp"),
    )
    details = "".join(f"<dl><dt>{label}</dt><dd>{value}</dd></dl>" for label, value in fields)
    if course.raw_status == "접수중":
        action = (
            '<a class="btn_write deadline big" '
            f'href="?cmd=4&amp;pageNo=&amp;mnu_uid=670&amp;lctre_uid={course.identity}">'
            "신청하기</a>"
        )
    else:
        action = f'<a class="btn deadline big" disabled>{course.raw_status}</a>'
    return f"""
    <html><body>
      <div class="class-lst">{details}</div>
      <div class="lectureBtn">
        {action}
        <a class="btn list big" href="?pageNo=&amp;mnu_uid=670&amp;">목록</a>
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
        drop_branch_row: bool = False,
        drift_page_one: bool = False,
        detail_title_mismatch: str = "",
        detail_http_failure: str = "",
    ):
        self.courses = _courses()
        self.drop_branch_row = drop_branch_row
        self.drift_page_one = drift_page_one
        self.detail_title_mismatch = detail_title_mismatch
        self.detail_http_failure = detail_http_failure
        self.urls: list[str] = []
        self._unfiltered_page_one_calls = 0
        self._lock = Lock()

    def response(self, url: str) -> FakeResponse:
        parsed = urlparse(url)
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        query = dict(query_pairs)
        with self._lock:
            self.urls.append(url)
        if query.get("cmd") == "4":
            raise AssertionError("application endpoint must never be fetched")
        if query.get("cmd") == "2":
            identity = query["lctre_uid"]
            course = next(row for row in self.courses if row.identity == identity)
            if identity == self.detail_http_failure:
                return FakeResponse(url, "temporary failure", status_code=500)
            title = "변조된 상세 제목" if identity == self.detail_title_mismatch else None
            return FakeResponse(url, _detail_html(course, title=title))

        page = int(query.get("pageNo", "1"))
        branch_code = query.get("srchSite", "")
        status_code = query.get("srchStts", "")
        rows = list(self.courses)
        if branch_code:
            rows = [row for row in rows if row.branch_code == branch_code]
            if self.drop_branch_row and branch_code == "150":
                rows = rows[:-1]
        if status_code:
            status_name = dict(uiseong.UISEONG_STATUS_FILTERS)[status_code]
            rows = [row for row in rows if row.raw_status == status_name]
        html = _list_html(
            rows,
            page,
            branch_code=branch_code,
            status_code=status_code,
        )
        if not branch_code and not status_code and page == 1:
            with self._lock:
                self._unfiltered_page_one_calls += 1
                call_number = self._unfiltered_page_one_calls
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
    return uiseong.collect_uiseong_education(
        TARGET,
        today="2026-07-23",
        session_factory=_factory(backend),
        **options,
    )


def test_exact_target_candidate_provider_and_owner_boundaries() -> None:
    assert uiseong.is_target(TARGET)
    assert not uiseong.is_target(
        {
            "provider": uiseong.UISEONG_REVIEW_MAIN_PROVIDER,
            "url": "https://usc.go.kr/ko/main.do",
        }
    )
    assert not uiseong.is_target(
        {
            "provider": uiseong.UISEONG_NO_WWW_ALIAS_PROVIDER,
            "url": "https://usc.go.kr/reserve/page.do?mnu_uid=670",
        }
    )
    assert not uiseong.is_target(
        {
            "provider": uiseong.UISEONG_PROVIDER,
            "url": uiseong.UISEONG_CANONICAL_URL + "&pageNo=1",
        }
    )
    audit = uiseong.UISEONG_CANDIDATE_AUDIT
    assert audit[uiseong.UISEONG_CANONICAL_CANDIDATE_ID]["provider"] == uiseong.UISEONG_PROVIDER
    assert "general_homepage" in audit[uiseong.UISEONG_REVIEW_MAIN_CANDIDATE_ID]["decision"]
    assert "organization_chart" in audit[uiseong.UISEONG_REVIEW_ORG_CANDIDATE_ID]["decision"]
    decisions = {item["decision"] for item in uiseong.UISEONG_OWNER_BOUNDARIES}
    assert any("duplicate_filter_alias" in value for value in decisions)
    assert any("separate_gyeongbuk_education_office_library_owner" in value for value in decisions)


def test_complete_snapshot_partitions_details_controls_and_privacy() -> None:
    backend = SyntheticBackend()
    rows, parser, meta = _collect(backend)

    assert parser == uiseong.UISEONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 6
    assert meta["source_rows"] == 41
    assert meta["source_capacity_shape_counts"] == {
        "current_total_and_waitlist": 41,
    }
    assert meta["data_pages"] == 3
    assert meta["page_counts"] == [20, 20, 1]
    assert meta["advertised_last_page"] == 3
    assert meta["sentinel_page"] == 4
    assert meta["source_requests"] == 31
    assert meta["request_attempts"] == 31
    assert meta["list_requests"] == 25
    assert meta["detail_pages"] == 6
    assert meta["current_source_count"] == 6
    assert meta["expired_source_count"] == 35
    assert meta["source_status_counts"] == {
        "접수대기": 1,
        "접수중": 2,
        "접수마감": 38,
    }
    assert meta["status_counts"] == {"SCHEDULED": 1, "OPEN": 2, "CLOSED": 3}
    assert meta["branch_filter_counts"]["청소년문화의집"] == 25
    assert meta["branch_filter_pages"]["청소년문화의집"] == 2
    assert meta["branch_filter_counts"]["의성가족센터"] == 0
    assert meta["status_filter_pages"]["접수마감"] == 2
    assert meta["application_control_count"] == 2
    assert meta["actionable_application_count"] == 2
    assert meta["application_endpoints_called"] == 0
    assert meta["applicant_endpoints_called"] == 0
    assert meta["attachment_endpoints_called"] == 0
    assert meta["privacy_violations"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["page1_rechecked"]
    assert meta["last_page_rechecked"]
    assert meta["sentinel_rechecked"]
    assert meta["filter_census_complete"]
    assert meta["details_complete"]
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]

    assert {row["provider_course_id"] for row in rows} == {
        f"{uiseong.UISEONG_PROVIDER}:lctre:{identity}"
        for identity in ("2041", "2040", "2039", "2038", "2037", "2036")
    }
    open_rows = [row for row in rows if row["status"] == "OPEN"]
    assert len(open_rows) == 2
    assert all("cmd=4" in row["application_url"] for row in open_rows)
    assert all(
        row["application_url"] == ""
        for row in rows
        if row["status"] != "OPEN"
    )
    assert all(row["address"] == row["venue_address"] == "" for row in rows)
    assert all(row["description"] == row["title"] for row in rows)
    payload = repr(rows)
    for forbidden in (
        "개인강사",
        "054-830-1234",
        "저장하면 안 되는 상세 본문",
        "저장하면 안 되는 주의사항",
        "개인정보양식.hwp",
    ):
        assert forbidden not in payload
    assert not any("cmd=4" in url for url in backend.urls)


def test_legacy_list_count_uses_detail_capacity_without_inventing_waitlist_count() -> None:
    course = _courses()[0]
    list_html = _course_html(course).replace(
        (
            '<ul class="num">\n'
            f'        <li><b>신청</b> {course.capacity_current}/{course.capacity_total}명</li>\n'
            f'        <li><b>후보</b> {course.waitlist_current}/{course.waitlist_total}명</li>\n'
            "      </ul>"
        ),
        (
            '<ul class="num">\n'
            "        <li></li>\n"
            f"        <li><b>신청</b> {course.capacity_current}</li>\n"
            "      </ul>"
        ),
    )
    listed = uiseong._parse_row(
        uiseong.BeautifulSoup(list_html, "lxml").select_one("li"),
        1,
        1,
    )

    row = uiseong._parse_detail(
        listed,
        uiseong.BeautifulSoup(_detail_html(course), "lxml"),
        uiseong.date.fromisoformat("2026-07-23"),
    )

    assert listed["capacity_shape"] == "legacy_applied_without_capacity"
    assert row["capacity_current"] == course.capacity_current
    assert row["capacity_total"] == course.capacity_total
    assert row["waitlist_current"] is None
    assert row["waitlist_total"] == course.waitlist_total


@pytest.mark.parametrize(
    ("backend", "error_fragment"),
    (
        (SyntheticBackend(drop_branch_row=True), "branch partition"),
        (SyntheticBackend(drift_page_one=True), "stability recheck"),
        (SyntheticBackend(detail_title_mismatch="2041"), "title drift"),
        (SyntheticBackend(detail_http_failure="2041"), "HTTP 500"),
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
    rows, _, meta = uiseong.collect_uiseong_education(
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
    rows, _, meta = _collect(backend, detail_limit=5)
    assert rows == []
    assert meta["source_cap_reached"]
    assert "detail_limit" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0


def test_response_url_redirect_and_option_drift_fail_closed() -> None:
    class DriftBackend(SyntheticBackend):
        def response(self, url: str) -> FakeResponse:
            response = super().response(url)
            response.url = "https://usc.go.kr/reserve/page.do?mnu_uid=670"
            return response

    rows, _, meta = _collect(DriftBackend())
    assert rows == []
    assert "response URL drift" in meta["configured_collection_error"]

    class OptionBackend(SyntheticBackend):
        def response(self, url: str) -> FakeResponse:
            response = super().response(url)
            response.content = response.content.replace(
                "의성군 평생학습관".encode(),
                "변조된 기관".encode(),
                1,
            )
            return response

    rows, _, meta = _collect(OptionBackend())
    assert rows == []
    assert "option vocabulary drift" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_UISEONG_LIVE") != "1",
    reason="set RUN_UISEONG_LIVE=1 for the bounded official-source audit",
)
def test_live_exact_audit_baseline() -> None:
    rows, parser, meta = uiseong.collect_uiseong_education(
        TARGET,
        today="2026-07-23",
        allow_raw_requests_for_tests=True,
        timeout=30,
        max_pages=60,
        detail_limit=100,
        max_workers=3,
    )
    assert parser == uiseong.UISEONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 38
    assert meta["source_requests"] == 105
    assert meta["request_attempts"] == 105
    assert meta["list_requests"] == 67
    assert meta["detail_pages"] == 38
    assert meta["data_pages"] == 39
    assert meta["page_counts"] == [20] * 38 + [18]
    assert meta["sentinel_page"] == 40
    assert meta["source_rows"] == 778
    assert meta["source_capacity_shape_counts"] == {
        "current_total_and_waitlist": 560,
        "legacy_applied_without_capacity": 35,
        "legacy_confirmed_without_capacity": 183,
    }
    assert meta["source_reversed_date_anomaly_count"] == 1
    assert meta["current_source_count"] == 38
    assert meta["source_status_counts"] == {
        "접수대기": 1,
        "접수중": 13,
        "접수마감": 764,
    }
    assert meta["status_counts"] == {"CLOSED": 24, "OPEN": 13, "SCHEDULED": 1}
    assert meta["branch_filter_counts"] == {
        "청소년문화의집": 82,
        "의성군 평생학습관": 317,
        "의성가족센터": 0,
        "읍면사무소": 0,
        "지질공원": 0,
        "펫월드": 28,
        "의성군청소년상담복지센터": 0,
        "청년정책과": 0,
        "관광문화과": 0,
        "보건소": 131,
        "농업기술센터": 44,
        "의성조문국박물관": 66,
        "군립도서관": 110,
    }
    assert meta["branch_counts"] == {
        "청소년문화의집": 19,
        "의성군 평생학습관": 8,
        "군립도서관": 7,
        "의성조문국박물관": 2,
        "보건소": 2,
    }
    assert meta["application_control_count"] == 13
    assert meta["privacy_violations"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["snapshot_complete"]
    assert Counter(row["status"] for row in rows) == {
        "CLOSED": 24,
        "OPEN": 13,
        "SCHEDULED": 1,
    }
