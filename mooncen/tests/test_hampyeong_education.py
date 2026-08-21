from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import inspect
import json
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_hampyeong as hampyeong


@dataclass(frozen=True)
class Target:
    provider: str = hampyeong.HAMPYEONG_PROVIDER
    url: str = hampyeong.HAMPYEONG_CANONICAL_URL
    candidate_id: str = hampyeong.HAMPYEONG_CANDIDATE_ID


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    category: str = "평생학습관"
    venue: str = "함평군 평생학습관"
    current: int = 2
    total: int = 12
    start: str = "2025-01-02"
    end: str = "2025-02-14"
    schedule: str = "화요일 10:00 ~ 12:00"
    step: str = "4"
    application_start: str = "2019-01-01 09:00:00"
    application_stop: str = "2026-06-29 18:00:00"
    application_period: str = "2025. 1. 1. ~ 2025. 1. 31."


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _courses(total: int = 23, *, current: bool = True) -> list[Course]:
    result: list[Course] = []
    for offset in range(total):
        identity = str(130 - offset)
        row = Course(identity=identity, title=f"강좌 {identity}")
        if current and offset == 0:
            row = replace(
                row,
                start="2099-07-28",
                end="2099-08-14",
                step="3",
                application_start="2026-07-01 09:00:00",
                application_stop="2099-07-31 18:00:00",
                application_period="2026. 7. 1. ~ 2099. 7. 31.",
            )
        elif current and offset == 1:
            row = replace(
                row,
                start="2099-08-15",
                end="2099-08-20",
                step="1",
                application_start="2099-08-01 09:00:00",
                application_stop="2099-08-10 18:00:00",
                application_period="2099. 8. 1. ~ 2099. 8. 10.",
            )
        elif current and offset == 2:
            row = replace(
                row,
                start="2099-09-01",
                end="2099-09-01",
                step="4",
                application_period="연중",
            )
        result.append(row)
    return result


def _search_form() -> str:
    options = "".join(
        f'<option value="{value}"{(" selected" if value == "0" else "")}>{escape(label)}</option>'
        for value, label in hampyeong._CATEGORY_OPTIONS
    )
    return f"""
      <form id="search_form" name="search_form" method="post" action="/pj/pjEducate.php">
        <input type="hidden" name="pageID" value="{hampyeong.HAMPYEONG_PAGE_ID}">
        <input type="hidden" name="action" value="list">
        <select name="category">{options}</select>
        <input type="text" name="searchQuery" value="">
      </form>
    """


def _check_script() -> str:
    return """
      <script>
        function checkClassDate(el) {
          var targetRow = el.closest("tr");
          var step = targetRow.dataset.step;
          var startStr = targetRow.dataset.start;
          var stopStr = targetRow.dataset.stop;
          if (step === "1") { return false; }
          if (step === "4") { return false; }
          if (step === "2" && startStr && stopStr) {
            var now = new Date();
            var startDate = new Date(startStr);
            var endDate = new Date(stopStr);
            if (now < startDate) { return false; }
            if (now > endDate) { return false; }
          }
          return true;
        }
      </script>
    """


def _inert_onclick(course: Course) -> str:
    if course.step == "1":
        return "alert('수강신청이 시작되지 않았습니다. (진행상태: 신청전)'); return false;"
    if course.step in {"2", "4"}:
        return "alert('수강신청 기간이 종료되었습니다.'); return false;"
    raise AssertionError("open fixture course must have a course-bound control")


def _control(course: Course, *, detail: bool = False, control_drift: bool = False) -> str:
    classes = "btn btn_apply" if detail else "table_view"
    if course.step not in {"1", "2", "3", "4"}:
        return (
            f'<a class="{classes}" href="#none" '
            'onclick="alert(&#x27;수강신청 기간이 종료되었습니다.&#x27;); return false;">'
            "신청하기</a>"
        )
    if course.step == "3" or (
        course.step == "2"
        and course.application_start <= "2026-07-21 23:59:59"
        and course.application_stop >= "2026-07-21 00:00:00"
    ):
        onclick = "" if detail else ' onclick="return checkClassDate(this);"'
        if control_drift:
            onclick = ' onclick="return changedGuard(this);"'
        return (
            f'<a class="{classes}" href="/pj/pjEducateApply.php?'
            f'pageID={hampyeong.HAMPYEONG_PAGE_ID}&amp;action=insert&amp;eseq={course.identity}"'
            f'{onclick}>신청하기</a>'
        )
    onclick = _inert_onclick(course)
    if control_drift:
        onclick = "alert('변경'); return false;"
    return (
        f'<a class="{classes}" href="#none" onclick="{escape(onclick, quote=True)}">'
        "신청하기</a>"
    )


def _list_row(course: Course, *, control_drift: bool = False) -> str:
    return f"""
      <tr data-step="{course.step}" data-start="{course.application_start}"
          data-stop="{course.application_stop}">
        <td>{escape(course.category)}</td>
        <td><a href="/pj/pjEducate.php?pageID={hampyeong.HAMPYEONG_PAGE_ID}&amp;action=view&amp;seq={course.identity}">{escape(course.title)}</a></td>
        <td>{escape(course.venue)}</td>
        <td>{course.current}</td>
        <td>{course.total}</td>
        <td>강사 홍길동</td>
        <td>{course.start} ~ {course.end}</td>
        <td>{escape(course.schedule)}</td>
        <td>{_control(course, control_drift=control_drift)}</td>
      </tr>
    """


def _list_html(
    courses: list[Course],
    page: int,
    *,
    recheck_drift: bool = False,
    bad_sentinel: bool = False,
    pager_drift: bool = False,
    control_drift: bool = False,
) -> str:
    start = (page - 1) * hampyeong.HAMPYEONG_PAGE_SIZE
    page_rows = courses[start : start + hampyeong.HAMPYEONG_PAGE_SIZE]
    if recheck_drift and page == 1 and page_rows:
        page_rows = [replace(page_rows[0], title=page_rows[0].title + " 변경"), *page_rows[1:]]
    if page_rows:
        rows = "".join(
            _list_row(row, control_drift=control_drift and page == 1)
            for row in page_rows
        )
        active_page = page + 1 if pager_drift else page
        pager = (
            '<div class="pagination">'
            f'<a class="active" href="/pj/pjEducate.php?movePage={active_page}&amp;action=list&amp;pageID={hampyeong.HAMPYEONG_PAGE_ID}">{page}</a>'
            "</div>"
        )
    else:
        marker = "등록된 교육 내용이 없습니다." if not bad_sentinel else "자료 없음"
        rows = f'<tr><td colspan="9">{marker}</td></tr>'
        pager = (
            '<div class="pagination">'
            f'<a class="prev" href="/pj/pjEducate.php?movePage={page - 1}&amp;action=list&amp;pageID={hampyeong.HAMPYEONG_PAGE_ID}"></a>'
            "</div>"
            if page > 1
            else ""
        )
    headers1 = "".join(
        (
            f'<th colspan="2">{escape(label)}</th>'
            if index == 6
            else f'<th rowspan="2">{escape(label)}</th>'
        )
        for index, label in enumerate(hampyeong._LIST_HEADER_ROWS[0])
    )
    headers2 = "".join(f"<th>{escape(label)}</th>" for label in hampyeong._LIST_HEADER_ROWS[1])
    return f"""
      <html><head><title>강좌정보 | 함평군 평생학습</title></head><body>
        <header class="sitemap-title"><h2>함평군 평생학습 사이트맵</h2></header>
        {_search_form()}
        <table class="basic_table">
          <caption>{escape(hampyeong._LIST_CAPTION)}</caption>
          <thead><tr>{headers1}</tr><tr>{headers2}</tr></thead>
          <tbody>{rows}</tbody>
        </table>
        {pager}
        {_check_script()}
      </body></html>
    """


def _detail_html(
    course: Course,
    *,
    title_drift: bool = False,
    capacity_drift: bool = False,
    control_drift: bool = False,
    detail_field_drift: bool = False,
) -> str:
    values = {
        "강좌명": course.title + (" 변경" if title_drift else ""),
        "구분": course.category,
        "교육장소": course.venue,
        "신청인원": f"{course.current}명",
        "모집인원": f"{course.total + (1 if capacity_drift else 0)}명",
        "강사명": "홍길동",
        "신청기간": course.application_period,
        "교육기간": f"{course.start} ~ {course.end}",
        "운영시간": course.schedule,
        "문의처": "담당자 061-320-1234 contact@example.org",
        "관련 홈페이지": "https://private.example.org/profile",
        "첨부파일": "private-course-plan.pdf",
    }
    fields = list(hampyeong._DETAIL_FIELDS)
    if detail_field_drift:
        fields[-1] = "파일"
        values["파일"] = values["첨부파일"]
    pairs = "".join(
        f"<tr><th>{escape(field)}</th><td>{escape(values[field])}</td></tr>"
        for field in fields
    )
    return f"""
      <html><head><title>강좌정보 | 함평군 평생학습</title></head><body>
        <table class="basic_table">
          <caption>{escape(hampyeong._DETAIL_CAPTION)}</caption>
          <tbody>{pairs}</tbody>
        </table>
        {_control(course, detail=True, control_drift=control_drift)}
      </body></html>
    """


def _gate_html(*, drift: bool = False, with_form: bool = False) -> str:
    destination = (
        "/pj/pjLogin.php?action=login&pageID=wrong"
        if drift
        else f"/pj/pjLogin.php?action=login&pageID={hampyeong.HAMPYEONG_PAGE_ID}"
    )
    form = '<form action="/login"><input name="id"></form>' if with_form else ""
    return f"""
      <html><head><title>Move</title></head><body>{form}
        <script>alert('로그인이 필요합니다.');window.document.location.href = '{destination}';</script>
      </body></html>
    """


class FixtureSite:
    def __init__(
        self,
        courses: list[Course] | None = None,
        *,
        recheck_drift: bool = False,
        bad_sentinel: bool = False,
        pager_drift: bool = False,
        control_drift: bool = False,
        detail_title_drift: bool = False,
        capacity_drift: bool = False,
        detail_control_drift: bool = False,
        detail_field_drift: bool = False,
        gate_drift: bool = False,
        gate_form: bool = False,
    ) -> None:
        self.courses = list(_courses() if courses is None else courses)
        self.recheck_drift = recheck_drift
        self.bad_sentinel = bad_sentinel
        self.pager_drift = pager_drift
        self.control_drift = control_drift
        self.detail_title_drift = detail_title_drift
        self.capacity_drift = capacity_drift
        self.detail_control_drift = detail_control_drift
        self.detail_field_drift = detail_field_drift
        self.gate_drift = gate_drift
        self.gate_form = gate_form
        self.calls: list[str] = []
        self.page_calls: dict[int, int] = {}

    def __call__(self, _session: object, url: str, _timeout: int) -> str:
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == hampyeong.HAMPYEONG_HOST
        query = parse_qs(parsed.query, keep_blank_values=True)
        self.calls.append(url)
        if parsed.path == hampyeong.HAMPYEONG_APPLY_PATH:
            assert query == {
                "pageID": [hampyeong.HAMPYEONG_PAGE_ID],
                "action": ["insert"],
                "eseq": [query["eseq"][0]],
            }
            return _gate_html(drift=self.gate_drift, with_form=self.gate_form)
        assert parsed.path == hampyeong.HAMPYEONG_LIST_PATH
        if query.get("action") == ["view"]:
            identity = query["seq"][0]
            course = next(row for row in self.courses if row.identity == identity)
            return _detail_html(
                course,
                title_drift=self.detail_title_drift,
                capacity_drift=self.capacity_drift,
                control_drift=self.detail_control_drift,
                detail_field_drift=self.detail_field_drift,
            )
        assert query.get("action") == ["list"]
        assert query.get("pageID") == [hampyeong.HAMPYEONG_PAGE_ID]
        page = int(query.get("movePage", ["1"])[0])
        self.page_calls[page] = self.page_calls.get(page, 0) + 1
        return _list_html(
            self.courses,
            page,
            recheck_drift=(
                self.recheck_drift and page == 1 and self.page_calls[page] > 1
            ),
            bad_sentinel=self.bad_sentinel,
            pager_drift=self.pager_drift,
            control_drift=self.control_drift,
        )


def _collect(site: FixtureSite, **kwargs: object):
    return hampyeong.collect_hampyeong_education_courses(
        Target(),
        fetcher=site,
        session_factory=DummySession,
        today="2026-07-21",
        **kwargs,
    )


def test_complete_unknown_total_pagination_cutoff_controls_and_pii_allowlist() -> None:
    site = FixtureSite()

    rows, parser, meta = _collect(site)

    assert parser == hampyeong.HAMPYEONG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["source_total"] == 23
    assert meta["page_counts"] == {1: 10, 2: 10, 3: 3, 4: 0}
    assert meta["data_pages"] == 3
    assert meta["sentinel_page"] == 4
    assert meta["required_list_requests"] == 5
    assert meta["list_requests"] == 5
    assert meta["list_rechecks"] == 1
    assert meta["current_count"] == 3
    assert meta["expired_count"] == 20
    assert meta["detail_pages"] == 3
    assert meta["application_gate_pages"] == 1
    assert meta["request_count"] == 9
    assert meta["source_step_counts"] == {"3": 1, "1": 1, "4": 21}
    assert meta["source_status_counts"] == {
        "OPEN": 1,
        "SCHEDULED": 1,
        "CLOSED": 21,
    }
    assert len(rows) == 3

    open_row = next(row for row in rows if row["status"] == "OPEN")
    scheduled = next(row for row in rows if row["status"] == "SCHEDULED")
    closed = next(row for row in rows if row["status"] == "CLOSED")
    assert open_row["provider"] == hampyeong.HAMPYEONG_PROVIDER
    assert open_row["branch"] == "함평군 평생학습"
    assert open_row["category"] == "교육"
    assert open_row["reservation_available"] is True
    assert open_row["application_url"] == hampyeong.hampyeong_application_url("130")
    assert open_row["raw_fields"]["login_gate_verified"] is True
    assert scheduled["reservation_available"] is False
    assert scheduled["application_url"] == ""
    assert closed["period"] == "2099-09-01 ~ 2099-09-01"
    assert closed["apply_period"] == "연중"
    assert open_row["description"] == open_row["title"]
    assert set(open_row["raw_fields"]) <= hampyeong._SAFE_RAW_FIELDS

    persisted = json.dumps(rows, ensure_ascii=False)
    assert "홍길동" not in persisted
    assert "061-320-1234" not in persisted
    assert "contact@example.org" not in persisted
    assert "private-course-plan.pdf" not in persisted
    assert "private.example.org" not in persisted
    assert "문의처" not in persisted
    assert "첨부파일" not in persisted


def test_no_current_rows_is_a_complete_empty_snapshot() -> None:
    rows, _parser, meta = _collect(FixtureSite(_courses(13, current=False)))

    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["no_current_data"] is True
    assert meta["current_count"] == 0
    assert meta["expired_count"] == 13
    assert meta["detail_attempts"] == 0
    assert meta["application_gate_attempts"] == 0
    assert "have ended" in meta["no_current_reason"]


def test_entirely_empty_source_is_stable_and_complete() -> None:
    rows, _parser, meta = _collect(FixtureSite([]))

    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["source_total"] == 0
    assert meta["page_counts"] == {1: 0}
    assert meta["data_pages"] == 0
    assert meta["sentinel_page"] == 1
    assert meta["required_list_requests"] == 2
    assert meta["list_requests"] == 2
    assert meta["list_rechecks"] == 1
    assert meta["no_current_data"] is True
    assert "catalogue is empty" in meta["no_current_reason"]


def test_deleted_identity_gaps_are_allowed_but_order_and_uniqueness_are_proved() -> None:
    courses = _courses(13, current=False)
    for index in range(6, len(courses)):
        identity = str(129 - index)
        courses[index] = replace(courses[index], identity=identity, title=f"강좌 {identity}")

    rows, _parser, meta = _collect(FixtureSite(courses))

    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 13
    assert meta["duplicate_count"] == 0


@pytest.mark.parametrize(
    ("site", "error"),
    [
        (FixtureSite(recheck_drift=True), "page-one recheck changed"),
        (FixtureSite(bad_sentinel=True), "unknown empty marker"),
        (FixtureSite(pager_drift=True), "active pager URL changed"),
        (FixtureSite(control_drift=True), "application guard changed"),
        (FixtureSite(detail_title_drift=True), "detail/list 강좌명 mismatch"),
        (FixtureSite(capacity_drift=True), "detail/list capacity mismatch"),
        (FixtureSite(detail_control_drift=True), "application"),
        (FixtureSite(detail_field_drift=True), "detail fields changed"),
        (FixtureSite(gate_drift=True), "login gate destination changed"),
        (FixtureSite(gate_form=True), "login gate exposed a form"),
    ],
)
def test_contract_drift_fails_the_whole_snapshot(site: FixtureSite, error: str) -> None:
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error in meta["configured_collection_error"]


def test_duplicate_or_reordered_source_identity_fails_before_details() -> None:
    duplicate = _courses()
    duplicate[10] = replace(duplicate[10], identity=duplicate[9].identity)
    rows, _parser, meta = _collect(FixtureSite(duplicate))
    assert rows == []
    assert meta["duplicate_count"] == 1
    assert "duplicate source identities" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0

    reordered = _courses()
    reordered[10], reordered[11] = reordered[11], reordered[10]
    rows, _parser, meta = _collect(FixtureSite(reordered))
    assert rows == []
    assert "not strictly descending" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_unknown_step_and_step_two_window_mismatch_fail_closed() -> None:
    unknown = _courses()
    unknown[0] = replace(unknown[0], step="5")
    rows, _parser, meta = _collect(FixtureSite(unknown))
    assert rows == []
    assert "unknown source step" in meta["configured_collection_error"]

    mismatched = _courses()
    mismatched[0] = replace(
        mismatched[0],
        step="2",
        application_start="2099-01-01 09:00:00",
        application_stop="2099-01-31 18:00:00",
    )
    rows, _parser, meta = _collect(FixtureSite(mismatched))
    assert rows == []
    assert "step-2 closed window mismatch" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_page_and_detail_caps_fail_before_partial_publication() -> None:
    rows, _parser, meta = _collect(FixtureSite(), max_pages=4)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "before explicit sentinel" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0

    rows, _parser, meta = _collect(FixtureSite(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


@pytest.mark.parametrize("field", ["title", "venue"])
def test_phone_or_email_in_a_persisted_field_fails_closed(field: str) -> None:
    courses = _courses()
    courses[0] = replace(courses[0], **{field: "문의 061-320-1234"})

    rows, _parser, meta = _collect(FixtureSite(courses))

    assert rows == []
    assert "phone/email reached persisted allowlist" in meta[
        "configured_collection_error"
    ]


def test_dedupe_mutation_invalidates_the_complete_snapshot() -> None:
    rows, _parser, meta = _collect(
        FixtureSite(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed complete row count" in meta[
        "configured_collection_error"
    ]


def test_target_builders_candidate_and_owner_boundaries_are_exact() -> None:
    assert hampyeong.is_hampyeong_target(Target()) is True
    assert hampyeong.is_hampyeong_target(
        Target(url=hampyeong.HAMPYEONG_CANONICAL_URL + "&movePage=1")
    ) is False
    assert hampyeong.is_hampyeong_target(
        Target(url=hampyeong.HAMPYEONG_REGISTERED_CANDIDATE_URL)
    ) is False
    assert hampyeong.is_hampyeong_target(Target(provider="WRONG")) is False
    assert hampyeong.hampyeong_list_url(1) == hampyeong.HAMPYEONG_CANONICAL_URL
    assert hampyeong.hampyeong_list_url(29).endswith(
        f"movePage=29&action=list&pageID={hampyeong.HAMPYEONG_PAGE_ID}"
    )
    assert hampyeong.hampyeong_detail_url("293").endswith(
        f"pageID={hampyeong.HAMPYEONG_PAGE_ID}&action=view&seq=293"
    )
    assert hampyeong.hampyeong_application_url("293").endswith(
        f"pageID={hampyeong.HAMPYEONG_PAGE_ID}&action=insert&eseq=293"
    )
    with pytest.raises(ValueError):
        hampyeong.hampyeong_list_url(0)
    with pytest.raises(ValueError):
        hampyeong.hampyeong_detail_url("293&evil=1")

    audit = hampyeong.HAMPYEONG_CANDIDATE_AUDIT[
        hampyeong.HAMPYEONG_CANDIDATE_ID
    ]
    assert audit["canonical_url"] == hampyeong.HAMPYEONG_CANONICAL_URL
    assert "job board" in audit["reason"]
    library = hampyeong.HAMPYEONG_OWNER_BOUNDARY_AUDIT[
        hampyeong.HAMPYEONG_EDUCATION_LIBRARY_PROVIDER
    ]
    assert library["decision"] == "keep_separate_office_of_education_library_owner"
    assert library["audited_rows"] == 8
    assert library["exact_title_overlap_with_municipal_catalogue"] == 0
    assert hampyeong.HAMPYEONG_EDUCATION_LIBRARY_BRANCH == (
        "전남광주통합특별시교육청함평도서관"
    )


def test_default_transport_is_verified_get_only_and_never_submits_forms() -> None:
    source = inspect.getsource(hampyeong)
    assert "verify=False" not in source
    assert "verify = False" not in source
    assert ".post(" not in source
    assert "allow_redirects=False" in source


def test_invalid_target_returns_fail_closed_without_creating_a_session() -> None:
    rows, parser, meta = hampyeong.collect_hampyeong_education_courses(
        Target(url=hampyeong.HAMPYEONG_REGISTERED_CANDIDATE_URL),
        fetcher=lambda *_args: pytest.fail("fetcher must not run"),
        session_factory=lambda: pytest.fail("session must not be created"),
        today="2026-07-21",
    )
    assert rows == []
    assert parser == hampyeong.HAMPYEONG_PARSER
    assert meta["snapshot_complete"] is False
    assert "canonical Hampyeong" in meta["configured_collection_error"]


def test_session_setup_failure_is_returned_as_fail_closed_metadata() -> None:
    rows, parser, meta = hampyeong.collect_hampyeong_education_courses(
        Target(),
        fetcher=lambda *_args: pytest.fail("fetcher must not run"),
        session_factory=lambda: (_ for _ in ()).throw(RuntimeError("session failed")),
        today="2026-07-21",
    )
    assert rows == []
    assert parser == hampyeong.HAMPYEONG_PARSER
    assert meta["snapshot_complete"] is False
    assert "client setup: RuntimeError: session failed" in meta[
        "configured_collection_error"
    ]
