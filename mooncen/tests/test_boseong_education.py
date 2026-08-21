from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import inspect
import json
import ssl
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_boseong as boseong


@dataclass(frozen=True)
class Target:
    provider: str = boseong.BOSEONG_PROVIDER
    url: str = boseong.BOSEONG_CANONICAL_URL
    candidate_id: str = boseong.BOSEONG_CANDIDATE_ID


@dataclass(frozen=True)
class Course:
    sequence: int
    identity: str
    title: str
    target: str
    start: str
    end: str
    apply_start: str
    apply_end: str
    status: str
    current: int = 2
    total: int = 12
    wait_current: int = 0
    wait_total: int = 5
    days: str = "화 수"
    time: str = "10:00 ~ 12:00"
    venue: str = "문화행사실1"


class DummySession:
    def close(self) -> None:
        pass


def _courses(total: int, prefix: int, *, current: bool) -> list[Course]:
    result: list[Course] = []
    for sequence in range(total, 0, -1):
        is_open = current and sequence == total
        is_current_closed = current and sequence == total - 1
        if is_open or is_current_closed:
            start, end = "2099-07-28", "2099-08-14"
            apply_start, apply_end = "2099-07-08", "2099-07-23"
        else:
            start, end = "2025-01-02", "2025-02-14"
            apply_start, apply_end = "2024-12-01", "2024-12-20"
        result.append(
            Course(
                sequence=sequence,
                identity=str(prefix + sequence),
                title=f"강좌 {prefix + sequence}",
                target="학생(어린이포함) (초4-6)",
                start=start,
                end=end,
                apply_start=apply_start,
                apply_end=apply_end,
                status="신청하기" if is_open else "마감",
                current=4 if is_open else 10,
                total=12 if is_open else 10,
                wait_current=0,
                wait_total=5,
                venue="문화행사실1" if sequence % 2 else "문화행사실2",
            )
        )
    return result


def _search_form(source: boseong.BoseongSource, page: int) -> str:
    n_page = "" if page == 1 else str(page)
    return f"""
      <form name="srhForm" method="post" action="/lecture.es?mid={source.mid}">
        <input type="hidden" name="actionUrl" value="/lecture.es">
        <input type="hidden" name="nPage" value="{n_page}">
        <input type="hidden" name="mid" value="{source.mid}">
        <input type="hidden" name="act" value="list">
        <input type="hidden" name="b_list" value="100">
        <input type="text" name="keyWord" value="">
      </form>
    """


def _status_control(course: Course, *, missing_open_control: bool = False) -> str:
    if course.status == "신청하기":
        if missing_open_control:
            return '<span class="w_app">신청하기</span>'
        return (
            '<a href="#" onclick="checkLogin(); return false;">'
            '<span class="w_app">신청하기</span></a>'
        )
    if course.status == "마감":
        return '<span class="w_close">마감</span>'
    return f'<span class="w_unknown">{escape(course.status)}</span>'


def _list_row(
    source: boseong.BoseongSource,
    course: Course,
    page: int,
    *,
    missing_open_control: bool = False,
    malformed_apply: bool = False,
) -> str:
    n_page = "" if page == 1 else str(page)
    detail = (
        f"/lecture.es?mid={source.mid}&act=view&el_seq={course.identity}"
        f"&nPage={n_page}"
    )
    control = _status_control(
        course, missing_open_control=missing_open_control
    )
    apply_end = course.apply_end.replace("-", "") if malformed_apply else course.apply_end
    return f"""
      <tr class="pd0">
        <td>{course.sequence}</td>
        <td aria-label="강좌명" class="txt_left t_title" scope="row">
          <a href="{escape(detail)}">{escape(course.title)}</a>
        </td>
        <td aria-label="대상">{escape(course.target)}</td>
        <td aria-label="운영기간">{course.start} ~<br>{course.end}<br>
          {escape(course.days)} {escape(course.time)}
        </td>
        <td aria-label="인터넷접수">{course.apply_start} 10:00 ~<br>
          {apply_end} 17:00
        </td>
        <td aria-label="신청현황">
          <span class="edu-state01">{course.current}</span> /
          <span class="edu-state02">{course.total}</span><br>
          ( <span class="edu-state01">{course.wait_current}</span> /
          <span class="edu-state02">{course.wait_total}</span> )
        </td>
        <td aria-label="상태">{control}</td>
      </tr>
    """


def _list_html(
    source: boseong.BoseongSource,
    courses: list[Course],
    page: int,
    *,
    title_drift: bool = False,
    bad_sentinel: bool = False,
    sequence_drift: bool = False,
    missing_open_control: bool = False,
    malformed_apply_identity: str = "",
) -> str:
    start = (page - 1) * boseong.BOSEONG_PAGE_SIZE
    page_courses = courses[start : start + boseong.BOSEONG_PAGE_SIZE]
    last = max(1, (len(courses) + boseong.BOSEONG_PAGE_SIZE - 1) // boseong.BOSEONG_PAGE_SIZE)
    if bad_sentinel and page == last + 1 and courses:
        page_courses = [courses[-1]]
    if sequence_drift and page == 2 and page_courses:
        page_courses = [
            replace(page_courses[0], sequence=page_courses[0].sequence + 1),
            *page_courses[1:],
        ]
    if title_drift and page_courses:
        page_courses = [
            replace(page_courses[0], title=page_courses[0].title + " 변경"),
            *page_courses[1:],
        ]
    rows = "".join(
        _list_row(
            source,
            course,
            page,
            missing_open_control=missing_open_control,
            malformed_apply=course.identity == malformed_apply_identity,
        )
        for course in page_courses
    )
    if not page_courses:
        rows = (
            '<tr><td class="nodata" colspan="6">'
            "등록된 자료가 존재하지 않습니다.</td></tr>"
        )
    branch = boseong.BOSEONG_BRANCH
    return f"""
      <html><head><title>글쓰기 | 수강 신청 | {source.menu} : {branch}</title></head>
      <body>{_search_form(source, page)}
        <table class="tstyle_list">
          <thead><tr>
            <th>연번</th><th>강좌명</th><th>대상</th><th>운영기간</th>
            <th>인터넷접수</th><th>신청 / 정원 (대기인원)</th><th>상태</th>
          </tr></thead><tbody>{rows}</tbody>
        </table>
      </body></html>
    """


def _detail_html(
    source: boseong.BoseongSource,
    course: Course,
    *,
    gate_drift: bool = False,
    detail_status_drift: bool = False,
    capacity_drift: bool = False,
    title_drift: bool = False,
    force_open_control: bool | None = None,
) -> str:
    status = "마감" if detail_status_drift else course.status
    total = course.total + 1 if capacity_drift else course.total
    if force_open_control is None:
        include_open = status == "신청하기"
    else:
        include_open = force_open_control
    open_control = (
        '<a href="#" onclick="checkLogin(); return false;">'
        '<span class="w_app">신청하기</span></a>'
        if include_open
        else ""
    )
    status_value = open_control if status == "신청하기" else status
    extra_forced_control = open_control if include_open and status != "신청하기" else ""
    login_path = "/login_search.es?sid=wrong" if gate_drift else "/login_search.es?sid=a8"
    document_title = "변경 기관" if title_drift else boseong.BOSEONG_BRANCH
    return f"""
      <html><head><title>글쓰기 | 수강 신청 | {source.menu} : {document_title}</title></head>
      <body>
        <script>
          function checkLogin() {{
            alert('로그인 후 이용할 수 있습니다.');
            location.href='{login_path}';
            return false;
          }}
        </script>
        <form name="insForm" method="post" action="/lecture.es&act=ins">
          <input type="hidden" name="actionUrl" value="/lecture.es">
          <input type="hidden" name="nPage" value="">
          <input type="hidden" name="act" value="list">
          <table class="tstyle_write"><tbody>
            <tr><th>강좌명</th><td colspan="3">{escape(course.title)}</td></tr>
            <tr><th>분기</th><td>여름</td><th>대상</th><td>{escape(course.target)}</td></tr>
            <tr><th>신청기간</th><td colspan="3">{course.apply_start} 10시 00분 ~ {course.apply_end} 17시 00분</td></tr>
            <tr><th>운영기간</th><td colspan="3">{course.start}~{course.end}</td></tr>
            <tr><th>강의 시간</th><td colspan="3">{escape(course.time)}</td></tr>
            <tr><th>회차</th><td>12</td><th>강의 요일</th><td>{escape(course.days)}</td></tr>
            <tr><th>교육장소</th><td>{escape(course.venue)}</td><th>계좌제 여부</th><td></td></tr>
            <tr><th>모집인원</th><td>{total}명 (대기 {course.wait_total}명)</td>
                <th>신청자</th><td>{course.current}명 (대기 {course.wait_current}명)</td></tr>
            <tr><th>신청방법</th><td>인터넷</td><th>접수상태</th><td>{status_value}</td></tr>
            <tr><th>강의 계획서</th><td colspan="3"><a href="/download.es?filename=private.pdf">private.pdf</a></td></tr>
            <tr><th>교육 일정표</th><td colspan="3"><a href="/lecture.es?act=timetable">교육일정표 보기</a>{extra_forced_control}</td></tr>
            <tr><th>비고</th><td colspan="3">담당자 061-852-3893 teacher@example.org 비공개</td></tr>
          </tbody></table>
        </form>
      </body></html>
    """


class FixtureSite:
    def __init__(
        self,
        *,
        lifelong: list[Course] | None = None,
        reading: list[Course] | None = None,
        recheck_drift: bool = False,
        bad_sentinel: bool = False,
        sequence_drift: bool = False,
        missing_open_control: bool = False,
        gate_drift: bool = False,
        detail_status_drift: bool = False,
        capacity_drift: bool = False,
        detail_title_drift: bool = False,
        force_open_control: bool | None = None,
        malformed_apply_identity: str = "",
    ) -> None:
        self.courses = {
            "lifelong": list(
                _courses(101, 10000, current=True)
                if lifelong is None
                else lifelong
            ),
            "reading_culture": list(
                _courses(3, 20000, current=False)
                if reading is None
                else reading
            ),
        }
        self.recheck_drift = recheck_drift
        self.bad_sentinel = bad_sentinel
        self.sequence_drift = sequence_drift
        self.missing_open_control = missing_open_control
        self.gate_drift = gate_drift
        self.detail_status_drift = detail_status_drift
        self.capacity_drift = capacity_drift
        self.detail_title_drift = detail_title_drift
        self.force_open_control = force_open_control
        self.malformed_apply_identity = malformed_apply_identity
        self.calls: list[str] = []
        self.page_calls: dict[tuple[str, int], int] = {}
        self.lock = Lock()

    def _source(self, mid: str) -> boseong.BoseongSource:
        return next(source for source in boseong.BOSEONG_SOURCES if source.mid == mid)

    def __call__(self, _session: object, url: str, _timeout: int) -> str:
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == boseong.BOSEONG_HOST
        assert parsed.path == boseong.BOSEONG_PATH
        query = parse_qs(parsed.query, keep_blank_values=True)
        source = self._source(query["mid"][0])
        with self.lock:
            self.calls.append(url)
        if query.get("act") == ["view"]:
            identity = query["el_seq"][0]
            course = next(
                row for row in self.courses[source.code] if row.identity == identity
            )
            return _detail_html(
                source,
                course,
                gate_drift=self.gate_drift,
                detail_status_drift=self.detail_status_drift,
                capacity_drift=self.capacity_drift,
                title_drift=self.detail_title_drift,
                force_open_control=self.force_open_control,
            )
        page = int(query.get("nPage", ["1"])[0] or "1")
        with self.lock:
            key = (source.code, page)
            self.page_calls[key] = self.page_calls.get(key, 0) + 1
            call = self.page_calls[key]
        last = max(
            1,
            (len(self.courses[source.code]) + boseong.BOSEONG_PAGE_SIZE - 1)
            // boseong.BOSEONG_PAGE_SIZE,
        )
        return _list_html(
            source,
            self.courses[source.code],
            page,
            title_drift=(
                self.recheck_drift and source.code == "lifelong" and page == 1 and call > 1
            ),
            bad_sentinel=(
                self.bad_sentinel and source.code == "lifelong" and page == last + 1
            ),
            sequence_drift=(self.sequence_drift and source.code == "lifelong"),
            missing_open_control=(
                self.missing_open_control and source.code == "lifelong" and page == 1
            ),
            malformed_apply_identity=self.malformed_apply_identity,
        )


def _collect(site: FixtureSite, **kwargs: object):
    return boseong.collect_boseong_education_courses(
        Target(),
        fetcher=site,
        session_factory=DummySession,
        today="2026-07-21",
        **kwargs,
    )


def test_complete_two_catalogue_snapshot_pagination_cutoff_and_pii_allowlist() -> None:
    site = FixtureSite()

    rows, parser, meta = _collect(site)

    assert parser == boseong.BOSEONG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["pagination_complete"] is True
    assert meta["partition_union_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["source_totals"] == {"lifelong": 101, "reading_culture": 3}
    assert meta["source_page_counts"] == {
        "lifelong": [100, 1],
        "reading_culture": [3],
    }
    assert meta["source_current_counts"] == {
        "lifelong": 2,
        "reading_culture": 0,
    }
    assert meta["source_rows"] == 104
    assert meta["current_count"] == 2
    assert meta["expired_count"] == 102
    assert meta["required_list_requests"] == 7
    assert meta["list_requests"] == 7
    assert meta["list_rechecks"] == 2
    assert meta["sentinel_pages"] == 2
    assert meta["detail_pages"] == 2
    assert meta["request_count"] == 9
    assert len(rows) == 2

    open_row = next(row for row in rows if row["status"] == "OPEN")
    closed_row = next(row for row in rows if row["status"] == "CLOSED")
    assert open_row["branch"] == boseong.BOSEONG_BRANCH
    assert open_row["provider"] == boseong.BOSEONG_PROVIDER
    assert open_row["program_type"] == "평생학습 강좌"
    assert open_row["reservation_available"] is True
    assert open_row["application_url"].endswith(
        f"el_seq={open_row['raw_fields']['source_identity']}"
    )
    assert open_row["application_type"] == "ONLINE_RESERVATION"
    assert closed_row["reservation_available"] is False
    assert closed_row["application_url"] == ""
    assert closed_row["application_type"] == ""
    assert open_row["capacity_current"] == 4
    assert open_row["capacity_total"] == 12
    assert open_row["waitlist_total"] == 5
    assert open_row["description"] == open_row["title"]
    assert set(open_row["raw_fields"]) <= boseong._SAFE_RAW_FIELDS

    persisted = json.dumps(rows, ensure_ascii=False)
    assert "061-852-3893" not in persisted
    assert "teacher@example.org" not in persisted
    assert "private.pdf" not in persisted
    assert "비고" not in persisted
    assert "강의 계획서" not in persisted
    assert "detail_pairs" not in persisted


def test_no_current_rows_is_a_complete_empty_snapshot() -> None:
    site = FixtureSite(
        lifelong=_courses(2, 10000, current=False),
        reading=_courses(1, 20000, current=False),
    )

    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["no_current_data"] is True
    assert meta["current_count"] == 0
    assert meta["expired_count"] == 3
    assert meta["detail_attempts"] == 0
    assert "have ended" in meta["no_current_reason"]


def test_historical_overbooking_is_preserved_as_valid_source_data() -> None:
    lifelong = _courses(2, 10000, current=False)
    lifelong[0] = replace(
        lifelong[0], current=14, total=10, wait_current=7, wait_total=5
    )
    rows, _parser, meta = _collect(
        FixtureSite(lifelong=lifelong, reading=[])
    )
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["source_totals"] == {"lifelong": 2, "reading_culture": 0}


def test_historical_application_typo_is_quarantined_but_current_typo_fails() -> None:
    historical = _courses(2, 10000, current=False)
    rows, _parser, meta = _collect(
        FixtureSite(
            lifelong=historical,
            reading=[],
            malformed_apply_identity=historical[0].identity,
        )
    )
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["historical_application_date_anomaly_count"] == 1

    current = _courses(2, 10000, current=True)
    rows, _parser, meta = _collect(
        FixtureSite(
            lifelong=current,
            reading=[],
            malformed_apply_identity=current[0].identity,
        )
    )
    assert rows == []
    assert "current/future application date range changed" in meta[
        "configured_collection_error"
    ]
    assert meta["detail_attempts"] == 0


@pytest.mark.parametrize(
    ("site", "error"),
    [
        (FixtureSite(recheck_drift=True), "page-one recheck changed"),
        (FixtureSite(bad_sentinel=True), "immediate sentinel"),
        (FixtureSite(sequence_drift=True), "source numbering is not continuous"),
        (FixtureSite(missing_open_control=True), "list application control changed"),
        (FixtureSite(gate_drift=True), "login gate destination changed"),
        (FixtureSite(capacity_drift=True), "detail/list capacity mismatch"),
        (FixtureSite(detail_title_drift=True), "detail owner changed"),
    ],
)
def test_contract_drift_fails_the_whole_snapshot(
    site: FixtureSite, error: str
) -> None:
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error in meta["configured_collection_error"]


def test_unknown_status_and_status_control_mismatch_fail_closed() -> None:
    unknown = _courses(2, 10000, current=True)
    unknown[0] = replace(unknown[0], status="접수중")
    rows, _parser, meta = _collect(
        FixtureSite(lifelong=unknown, reading=[])
    )
    assert rows == []
    assert "unknown source status" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FixtureSite(detail_status_drift=True))
    assert rows == []
    assert "detail/list status mismatch" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(FixtureSite(force_open_control=True))
    assert rows == []
    assert "closed detail unexpectedly has an action" in meta[
        "configured_collection_error"
    ]


def test_cross_catalogue_identity_overlap_fails_before_details() -> None:
    lifelong = _courses(2, 10000, current=True)
    reading = _courses(1, 20000, current=False)
    reading[0] = replace(reading[0], identity=lifelong[0].identity)
    site = FixtureSite(lifelong=lifelong, reading=reading)

    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["cross_source_duplicate_count"] == 1
    assert "overlap across catalogues" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_page_and_detail_caps_fail_before_partial_publication() -> None:
    site = FixtureSite()
    rows, _parser, meta = _collect(site, max_pages=6)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "7 required" in meta["configured_collection_error"]
    assert meta["list_requests"] == 2
    assert meta["detail_attempts"] == 0

    site2 = FixtureSite()
    rows, _parser, meta = _collect(site2, detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_phone_or_email_in_an_allowed_course_field_still_fails_closed() -> None:
    lifelong = _courses(2, 10000, current=True)
    lifelong[0] = replace(lifelong[0], target="문의 061-852-3893")
    site = FixtureSite(lifelong=lifelong, reading=[])

    rows, _parser, meta = _collect(site)

    assert rows == []
    assert "phone/email reached persisted allowlist" in meta[
        "configured_collection_error"
    ]


def test_dedupe_mutation_invalidates_the_complete_snapshot() -> None:
    site = FixtureSite()
    rows, _parser, meta = _collect(
        site, dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed complete row count" in meta[
        "configured_collection_error"
    ]


def test_target_url_builders_and_owner_audit_are_exact() -> None:
    assert boseong.is_boseong_target(Target()) is True
    assert (
        boseong.is_boseong_target(
            Target(url=boseong.BOSEONG_REGISTERED_NOTICE_URL)
        )
        is False
    )
    assert boseong.is_boseong_target(Target(provider="WRONG")) is False
    assert (
        boseong.is_boseong_target(
            Target(url=boseong.BOSEONG_CANONICAL_URL + "&act=view")
        )
        is False
    )
    assert boseong.boseong_list_url("lifelong", 2).endswith(
        "mid=a80402000000&nPage=2"
    )
    assert boseong.boseong_list_url("reading_culture", 1) == (
        boseong.BOSEONG_READING_URL
    )
    assert boseong.boseong_detail_url("lifelong", "25990").endswith(
        "mid=a80402000000&act=view&el_seq=25990"
    )
    with pytest.raises(ValueError):
        boseong.boseong_list_url("lifelong", 0)
    with pytest.raises(ValueError):
        boseong.boseong_detail_url("lifelong", "25990&evil=1")

    audit = boseong.BOSEONG_CANDIDATE_AUDIT[boseong.BOSEONG_CANDIDATE_ID]
    assert audit["provider"] == boseong.BOSEONG_PROVIDER
    assert audit["canonical_url"] == boseong.BOSEONG_CANONICAL_URL
    county = boseong.BOSEONG_OWNER_BOUNDARY_AUDIT[
        boseong.BOSEONG_COUNTY_PROVIDER
    ]
    assert county["decision"] == "retain_existing_separate_county_owner"
    assert county["audited_source_rows"] == 13
    assert boseong.BOSEONG_BRANCH == "전남광주통합특별시교육청보성도서관"
    assert boseong.BOSEONG_BEOLGYO_BRANCH != boseong.BOSEONG_BRANCH


def test_default_tls_context_is_narrow_and_verified_and_no_post_is_possible() -> None:
    context = boseong.build_boseong_tls_context()
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.maximum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert boseong.BOSEONG_TLS_CIPHER in {
        cipher["name"] for cipher in context.get_ciphers()
    }
    session = boseong._default_session_factory()
    try:
        adapter = session.get_adapter("https://bslib.jne.go.kr/")
        assert isinstance(adapter, boseong.BoseongTlsAdapter)
        assert adapter.ssl_context.verify_mode == ssl.CERT_REQUIRED
    finally:
        session.close()

    source = inspect.getsource(boseong)
    assert "verify=False" not in source
    assert "verify = False" not in source
    assert ".post(" not in source


def test_invalid_target_returns_a_fail_closed_meta_without_fetching() -> None:
    rows, parser, meta = boseong.collect_boseong_education_courses(
        Target(url=boseong.BOSEONG_REGISTERED_NOTICE_URL),
        fetcher=lambda *_args: pytest.fail("fetcher must not run"),
        session_factory=DummySession,
        today="2026-07-21",
    )
    assert rows == []
    assert parser == boseong.BOSEONG_PARSER
    assert meta["snapshot_complete"] is False
    assert "canonical Boseong Library" in meta["configured_collection_error"]
