from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import inspect
import json
import os
from urllib.parse import parse_qs, quote, urlparse

import pytest

from Crawler import municipal_yeonggwang as yeonggwang


@dataclass(frozen=True)
class Target:
    provider: str = yeonggwang.YEONGGWANG_PROVIDER
    url: str = yeonggwang.YEONGGWANG_CANONICAL_URL
    candidate_id: str = yeonggwang.YEONGGWANG_CANDIDATE_ID


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    target: str = "영광군민"
    start: str = "2025-01-02"
    end: str = "2025-02-14"
    schedule: str = "화요일 10:00 ~ 12:00"
    application_start: str = "2024-12-01 09:00"
    application_stop: str = "2024-12-20 18:00"
    status: str = "CLOSED"
    overall_total: int = 12
    capacity_current: int = 10
    online_total: int = 12
    wait_current: int = 0
    wait_total: int = 0
    branch: str = "여성문화센터"
    venue: str = "여성문화센터 교육실"
    detail_state: str = "교육종료"
    source_state: str = ""
    period_kind: str = "iso"
    application_kind: str = "iso_datetime"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _courses(total: int = 23, *, current: bool = True) -> list[Course]:
    result: list[Course] = []
    for offset in range(total):
        identity = str(2000 - offset)
        row = Course(identity=identity, title=f"강좌 {identity}")
        if offset == 0:
            row = replace(
                row,
                title="연중 종료 강좌",
                start="",
                end="",
                schedule="",
                application_start="",
                application_stop="",
                period_kind="annual",
                application_kind="annual",
            )
        elif offset == total - 1:
            row = replace(
                row,
                title="과거 날짜 형식 강좌",
                start="2020-01-01",
                end="2020-12-31",
                application_start="2020-01-01",
                application_stop="2020-12-31",
                period_kind="compact",
                application_kind="compact",
            )
        if current and offset == 1:
            row = replace(
                row,
                title="샌드아트 체험",
                target="6세~10세",
                start="2099-08-01",
                end="2099-08-01",
                schedule="14:00 ~ 15:00",
                application_start="2026-07-20 10:00",
                application_stop="2026-07-22 17:00",
                status="OPEN",
                capacity_current=4,
                online_total=12,
                wait_current=1,
                wait_total=3,
                branch="인구교육정책실",
                venue="영광청년육아나눔터 2층 커뮤니티홀",
                detail_state="접수대기",
            )
        elif current and offset == 2:
            row = replace(
                row,
                title="직업키링 체험",
                target="6세 ~10세",
                start="2099-08-08",
                end="2099-08-08",
                schedule="15:00~",
                application_start="2026-07-20 10:00",
                application_stop="2026-07-22 17:00",
                status="OPEN",
                capacity_current=5,
                online_total=12,
                branch="인구교육정책실",
                venue="영광청년육아나눔터 2층 커뮤니티홀",
                detail_state="접수중",
            )
        elif current and offset == 3:
            row = replace(
                row,
                title="향후 마감 강좌",
                start="2099-09-01",
                end="2099-09-10",
                application_start="2026-06-01 09:00",
                application_stop="2026-06-30 18:00",
                status="CLOSED",
                capacity_current=12,
                online_total=12,
                branch="여성문화센터",
                venue="여성문화센터 프로그램실",
                detail_state="접수종료",
                source_state="접수종료",
            )
        result.append(row)
    return result


def _category_tabs(*, drift: bool = False) -> str:
    links = []
    for index, (label, url, _boundary) in enumerate(yeonggwang._CATEGORY_LINKS):
        if drift and index == 3:
            url = "https://example.invalid/escaped"
        active = ' class="on"' if index == 0 else ""
        links.append(
            f'<li><h4><a{active} href="{escape(url, quote=True)}">'
            f"{escape(label)}</a></h4></li>"
        )
    return '<div id="board_category2"><ul class="title_box">' + "".join(links) + "</ul></div>"


def _search_form(*, drift: bool = False) -> str:
    board = "changed" if drift else yeonggwang.YEONGGWANG_BOARD
    options = "".join(
        f'<option value="{value}">{escape(label)}</option>'
        for value, label in yeonggwang._SEARCH_OPTIONS
    )
    return f"""
      <form id="frm" method="get" action="./">
        <input type="hidden" name="csrf_token_name" value="{'a' * 64}">
        <input type="hidden" name="b_id" value="{board}">
        <input type="hidden" name="site" value="{yeonggwang.YEONGGWANG_SITE}">
        <input type="hidden" name="mn" value="{yeonggwang.YEONGGWANG_MENU}">
        <input type="hidden" name="type" value="lists">
        <input type="hidden" name="sc_cate" value="">
        <input type="hidden" name="per_page" value="{yeonggwang.YEONGGWANG_PAGE_SIZE}">
        <select name="sc_key">{options}</select>
        <input type="text" name="sc_word" value="">
      </form>
    """


def _period(course: Course) -> str:
    if course.period_kind == "annual":
        return "연중 <br>~<br> 연중"
    if course.period_kind == "compact":
        start = course.start.replace("-", "")
        end = course.end.replace("-", "")
    else:
        start, end = course.start, course.end
    schedule = f"<br><br>{escape(course.schedule)}" if course.schedule else ""
    return f"{start} <br>~<br> {end}{schedule}"


def _apply_period(course: Course) -> str:
    if course.application_kind == "annual":
        return "연중 <br>~<br> 연중"
    if course.application_kind == "empty":
        return ""
    if course.application_kind == "compact":
        return (
            f"{course.application_start.replace('-', '')} <br>~<br>"
            f"{course.application_stop.replace('-', '')}"
        )
    return f"{course.application_start}<br>~<br>{course.application_stop}"


def _capacity(course: Course) -> str:
    if course.period_kind == "annual":
        return '<span class="applicant_number">수강인원 : 20명</span><br>'
    wait = (
        f'( <span class="applicant_number">{course.wait_current}</span> / {course.wait_total})'
        if course.wait_total
        else ""
    )
    return (
        f'<span class="applicant_number">수강인원 : {course.overall_total}명</span><br>'
        f'<span class="applicant_number">{course.capacity_current}</span> / '
        f"{course.online_total} {wait}"
    )


def _list_state(
    course: Course,
    page: int,
    *,
    state_drift: bool = False,
    gate_identity_drift: bool = False,
) -> str:
    if state_drift:
        return '<span class="state_unknown">접수중</span>'
    if course.source_state == "접수종료":
        return '<span class="state_end">접수종료</span>'
    if course.status == "CLOSED":
        return '<span class="state_finish">교육종료</span>'
    identity = "999999" if gate_identity_drift else course.identity
    offset = (
        f"&amp;offset={(page - 1) * yeonggwang.YEONGGWANG_PAGE_SIZE}"
        if page > 1
        else ""
    )
    return (
        '<span class="state_G"><a '
        f'href="?b_id={yeonggwang.YEONGGWANG_BOARD}&amp;site={yeonggwang.YEONGGWANG_SITE}'
        f'&amp;mn={yeonggwang.YEONGGWANG_MENU}{offset}&amp;type=application&amp;bs_idx={identity}" '
        'title="교육신청하기">신청하기</a></span>'
    )


def _list_row(
    course: Course,
    page: int,
    *,
    state_drift: bool = False,
    gate_identity_drift: bool = False,
) -> str:
    offset = (
        f"&amp;offset={(page - 1) * yeonggwang.YEONGGWANG_PAGE_SIZE}"
        if page > 1
        else ""
    )
    detail = (
        f"?b_id={yeonggwang.YEONGGWANG_BOARD}&amp;site={yeonggwang.YEONGGWANG_SITE}"
        f"&amp;mn={yeonggwang.YEONGGWANG_MENU}{offset}&amp;type=view&amp;bs_idx={course.identity}"
    )
    return f"""
      <tr>
        <td class="name">{escape(course.target)}</td>
        <td class="subject"><div class="title"><a href="{detail}">{escape(course.title)}</a></div></td>
        <td class="number">{_capacity(course)}</td>
        <td class="date date_time date_start">{_period(course)}</td>
        <td class="date date_register">{_apply_period(course)}</td>
        <td class="state">{_list_state(course, page, state_drift=state_drift, gate_identity_drift=gate_identity_drift)}</td>
      </tr>
    """


def _list_html(
    courses: list[Course],
    page: int,
    *,
    total_drift: bool = False,
    page_count_drift: bool = False,
    category_drift: bool = False,
    search_drift: bool = False,
    pager_drift: bool = False,
    state_drift: bool = False,
    gate_identity_drift: bool = False,
    header_drift: bool = False,
) -> str:
    total = len(courses) + (1 if total_drift and page == 2 else 0)
    total_pages = max(1, (total + yeonggwang.YEONGGWANG_PAGE_SIZE - 1) // yeonggwang.YEONGGWANG_PAGE_SIZE)
    start = (page - 1) * yeonggwang.YEONGGWANG_PAGE_SIZE
    page_rows = courses[start : start + yeonggwang.YEONGGWANG_PAGE_SIZE]
    if page_count_drift and page == total_pages and page_rows:
        page_rows = page_rows[:-1]
    rows = "".join(
        _list_row(
            row,
            page,
            state_drift=state_drift and page == 1 and index == 0,
            gate_identity_drift=(gate_identity_drift and row.status == "OPEN"),
        )
        for index, row in enumerate(page_rows)
    )
    pager_links = []
    if total_pages > 1:
        if page == 1:
            offset = 999 if pager_drift else yeonggwang.YEONGGWANG_PAGE_SIZE
            pager_links.append(
                f'<a href="?b_id=lecture&amp;site=headquarter_new&amp;mn=9247&amp;offset={offset}">2</a>'
            )
        else:
            pager_links.append(
                '<a href="?b_id=lecture&amp;site=headquarter_new&amp;mn=9247&amp;offset=">1</a>'
            )
    headers = list(yeonggwang._LIST_HEADERS)
    if header_drift:
        headers[1] = "프로그램"
    return f"""
      <html><head><title>{yeonggwang._DOCUMENT_TITLE}</title></head><body>
        {_category_tabs(drift=category_drift)}
        {_search_form(drift=search_drift)}
        <div id="list_total_count">전체 : <strong>{total}</strong>, 페이지 : <strong>{page}</strong>/{total_pages}</div>
        <div id="board_list"><table>
          <caption>{escape(yeonggwang._LIST_CAPTION)}</caption>
          <thead><tr>{''.join(f'<th>{escape(value)}</th>' for value in headers)}</tr></thead>
          <tbody>{rows}</tbody>
        </table></div>
        <div id="paginate_complex"><p class="btn_page"><span class="on">{page}</span>{''.join(pager_links)}</p></div>
      </body></html>
    """


def _detail_html(
    course: Course,
    *,
    title_drift: bool = False,
    target_drift: bool = False,
    schedule_drift: bool = False,
    capacity_drift: bool = False,
    state_drift: bool = False,
    branch_empty: bool = False,
    field_drift: bool = False,
) -> str:
    values = {
        "교육기관": "" if branch_empty else course.branch,
        "교육장소": course.venue,
        "문의전화": "061-350-1234",
        "문의이메일": "private@example.org",
        "상태": "변경상태" if state_drift else course.detail_state,
        "교육대상": course.target + (" 변경" if target_drift else ""),
        "교육 기간": f"{course.start} ~ {course.end}",
        "교육 시간": course.schedule + (" 변경" if schedule_drift else ""),
        "신청 기간": f"{course.application_start} ~ {course.application_stop}",
        "수강인원": str(course.overall_total),
        "온라인 신청인원": str(course.online_total + (1 if capacity_drift else 0)),
        "대기신청인원": str(course.wait_total) if course.wait_total else "",
        "수강료": "무료",
        "비고": "선착순",
        "등록일": "2026-06-29",
        "강좌명": course.title + (" 변경" if title_drift else ""),
    }
    ordered = list(yeonggwang._DETAIL_REQUIRED_FIELDS[:-1])
    if field_drift:
        ordered[-1] = "과정명"
        values["과정명"] = values["강좌명"]
    pairs = []
    for index in range(0, len(ordered), 2):
        left = ordered[index]
        right = ordered[index + 1] if index + 1 < len(ordered) else ""
        right_html = (
            f'<th scope="row">{escape(right)}</th><td>{escape(values[right])}</td>'
            if right
            else ""
        )
        pairs.append(
            f'<tr><th scope="row">{escape(left)}</th><td>{escape(values[left])}'
            f'{"<span class=f_alert>설명</span>" if left in {"수강인원", "온라인 신청인원", "대기신청인원"} else ""}'
            f"</td>{right_html}</tr>"
        )
    attachment = (
        '<tr><th scope="row">첨부</th><td colspan="3">private-plan.pdf</td></tr>'
    )
    app_info = """
      <tr><th scope="row">강좌신청정보</th><td colspan="3">
        ※ 로그인(본인인증) 후 신청정보를 확인할 수 있습니다.
        <a href="/bbs/?b_id=lecture&amp;site=headquarter_new&amp;mn=9247&amp;type=my_application_list">나의 신청현황</a>
      </td></tr>
    """
    return f"""
      <html><head><title>{yeonggwang._DOCUMENT_TITLE}</title></head><body>
        <div id="board_view"><table>
          <caption>{escape(yeonggwang._DETAIL_CAPTION)}</caption>
          <tbody>{''.join(pairs)}{attachment}{app_info}
            <tr><td class="leftcell rightcell" colspan="4"><div class="board_view_contents">담당 061-350-1234 private@example.org 비공개</div></td></tr>
          </tbody>
        </table></div>
      </body></html>
    """


def _gate_html(identity: str, *, drift: bool = False, with_form: bool = False) -> str:
    returned_identity = "999999" if drift else identity
    ret = (
        f"/bbs/?b_id=lecture&site=headquarter_new&mn=9247&type=application&bs_idx={returned_identity}"
    )
    destination = (
        "/subpage/?site=headquarter_new&mn=9641&ret_url=" + quote(ret, safe="")
    )
    form = '<form action="/login"><input name="id"></form>' if with_form else ""
    return f"""
      <html><head><title>Message</title></head><body>{form}
        <script>alert('로그인 후 이용가능합니다.');location.href='{destination}';</script>
      </body></html>
    """


class FixtureSite:
    def __init__(
        self,
        courses: list[Course] | None = None,
        *,
        recheck_drift: bool = False,
        total_drift: bool = False,
        page_count_drift: bool = False,
        category_drift: bool = False,
        search_drift: bool = False,
        pager_drift: bool = False,
        state_drift: bool = False,
        action_identity_drift: bool = False,
        detail_title_drift: bool = False,
        detail_target_drift: bool = False,
        detail_schedule_drift: bool = False,
        detail_capacity_drift: bool = False,
        detail_state_drift: bool = False,
        detail_branch_empty: bool = False,
        detail_field_drift: bool = False,
        gate_drift: bool = False,
        gate_form: bool = False,
        header_drift: bool = False,
    ) -> None:
        self.courses = list(_courses() if courses is None else courses)
        self.recheck_drift = recheck_drift
        self.total_drift = total_drift
        self.page_count_drift = page_count_drift
        self.category_drift = category_drift
        self.search_drift = search_drift
        self.pager_drift = pager_drift
        self.state_drift = state_drift
        self.action_identity_drift = action_identity_drift
        self.detail_title_drift = detail_title_drift
        self.detail_target_drift = detail_target_drift
        self.detail_schedule_drift = detail_schedule_drift
        self.detail_capacity_drift = detail_capacity_drift
        self.detail_state_drift = detail_state_drift
        self.detail_branch_empty = detail_branch_empty
        self.detail_field_drift = detail_field_drift
        self.gate_drift = gate_drift
        self.gate_form = gate_form
        self.header_drift = header_drift
        self.calls: list[str] = []
        self.page_calls: dict[int, int] = {}

    def __call__(self, _session: object, url: str, _timeout: int) -> str:
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == yeonggwang.YEONGGWANG_HOST
        assert parsed.path == yeonggwang.YEONGGWANG_PATH
        query = parse_qs(parsed.query, keep_blank_values=True)
        self.calls.append(url)
        kind = query.get("type", [""])[0]
        if kind == "view":
            identity = query["bs_idx"][0]
            course = next(row for row in self.courses if row.identity == identity)
            return _detail_html(
                course,
                title_drift=self.detail_title_drift,
                target_drift=self.detail_target_drift,
                schedule_drift=self.detail_schedule_drift,
                capacity_drift=self.detail_capacity_drift,
                state_drift=self.detail_state_drift,
                branch_empty=self.detail_branch_empty,
                field_drift=self.detail_field_drift,
            )
        if kind == "application":
            return _gate_html(
                query["bs_idx"][0], drift=self.gate_drift, with_form=self.gate_form
            )
        assert kind == ""
        offset = int(query.get("offset", ["0"])[0] or "0")
        page = offset // yeonggwang.YEONGGWANG_PAGE_SIZE + 1
        self.page_calls[page] = self.page_calls.get(page, 0) + 1
        courses = self.courses
        if self.recheck_drift and page == 1 and self.page_calls[page] > 1:
            courses = [replace(courses[0], title=courses[0].title + " 변경"), *courses[1:]]
        return _list_html(
            courses,
            page,
            total_drift=self.total_drift,
            page_count_drift=self.page_count_drift,
            category_drift=self.category_drift,
            search_drift=self.search_drift,
            pager_drift=self.pager_drift,
            state_drift=self.state_drift,
            gate_identity_drift=self.action_identity_drift,
            header_drift=self.header_drift,
        )


def _collect(site: FixtureSite, **kwargs: object):
    return yeonggwang.collect_yeonggwang_education_courses(
        Target(),
        fetcher=site,
        session_factory=DummySession,
        today="2026-07-21",
        **kwargs,
    )


def test_complete_declared_total_snapshot_cutoff_details_controls_and_pii() -> None:
    rows, parser, meta = _collect(FixtureSite())

    assert parser == yeonggwang.YEONGGWANG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["source_total"] == 23
    assert meta["page_counts"] == {1: 15, 2: 8}
    assert meta["declared_total_pages"] == 2
    assert meta["required_list_requests"] == 3
    assert meta["list_requests"] == 3
    assert meta["list_rechecks"] == 1
    assert meta["current_count"] == 3
    assert meta["expired_count"] == 20
    assert meta["detail_pages"] == 3
    assert meta["application_gate_pages"] == 2
    assert meta["request_count"] == 8
    assert meta["source_status_counts"] == {
        "교육종료": 20,
        "신청하기": 2,
        "접수종료": 1,
    }
    assert meta["branch_counts"] == {"인구교육정책실": 2, "여성문화센터": 1}
    assert len(rows) == 3

    open_row = next(row for row in rows if row["title"] == "샌드아트 체험")
    closed = next(row for row in rows if row["status"] == "CLOSED")
    assert open_row["branch"] == "인구교육정책실"
    assert open_row["venue"] == "영광청년육아나눔터 2층 커뮤니티홀"
    assert open_row["reservation_available"] is True
    assert open_row["application_url"] == yeonggwang.yeonggwang_application_url(
        "1999"
    )
    assert open_row["capacity_current"] == 4
    assert open_row["capacity_total"] == 12
    assert open_row["waitlist_total"] == 3
    assert open_row["raw_fields"]["detail_state"] == "접수대기"
    assert open_row["raw_fields"]["login_gate_verified"] is True
    assert closed["reservation_available"] is False
    assert closed["application_url"] == ""
    assert closed["branch"] == "여성문화센터"
    assert set(open_row["raw_fields"]) <= yeonggwang._SAFE_RAW_FIELDS

    persisted = json.dumps(rows, ensure_ascii=False)
    assert "061-350-1234" not in persisted
    assert "private@example.org" not in persisted
    assert "private-plan.pdf" not in persisted
    assert "강좌신청정보" not in persisted
    assert "나의 신청현황" not in persisted
    assert "csrf_token_name" not in persisted


def test_historical_annual_compact_and_empty_application_formats_are_quarantined() -> None:
    courses = _courses(13, current=False)
    courses[5] = replace(
        courses[5], application_start="", application_stop="", application_kind="empty"
    )

    rows, _parser, meta = _collect(FixtureSite(courses))

    assert rows == []
    assert meta["full_snapshot_validated"] is True
    assert meta["no_current_data"] is True
    assert meta["historical_period_kind_counts"] == {
        "annual": 1,
        "iso": 11,
        "compact": 1,
    }
    assert meta["historical_application_kind_counts"]["empty"] == 1
    assert meta["detail_attempts"] == 0


def test_reordered_non_monotonic_ids_are_valid_but_duplicates_fail() -> None:
    courses = _courses(13, current=False)
    courses[2], courses[3] = courses[3], courses[2]
    rows, _parser, meta = _collect(FixtureSite(courses))
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["duplicate_count"] == 0

    duplicate = _courses()
    duplicate[10] = replace(duplicate[10], identity=duplicate[9].identity)
    rows, _parser, meta = _collect(FixtureSite(duplicate))
    assert rows == []
    assert meta["duplicate_count"] == 1
    assert "duplicate source identities" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


@pytest.mark.parametrize(
    ("site", "error"),
    [
        (FixtureSite(recheck_drift=True), "page-one recheck changed"),
        (FixtureSite(total_drift=True), "total changed during pagination"),
        (FixtureSite(page_count_drift=True), "page row counts"),
        (FixtureSite(category_drift=True), "institution-owner tabs changed"),
        (FixtureSite(search_drift=True), "search scope changed"),
        (FixtureSite(pager_drift=True), "pager offset changed"),
        (FixtureSite(header_drift=True), "table headers changed"),
        (FixtureSite(state_drift=True), "unknown state/control contract"),
        (FixtureSite(action_identity_drift=True), "course action identity changed"),
        (FixtureSite(detail_title_drift=True), "detail/list title mismatch"),
        (FixtureSite(detail_target_drift=True), "detail/list target mismatch"),
        (FixtureSite(detail_schedule_drift=True), "detail/list schedule mismatch"),
        (FixtureSite(detail_capacity_drift=True), "detail/list capacity mismatch"),
        (FixtureSite(detail_state_drift=True), "detail/list state mismatch"),
        (FixtureSite(detail_branch_empty=True), "institution/location is empty"),
        (FixtureSite(detail_field_drift=True), "detail fields changed"),
        (FixtureSite(gate_drift=True), "login return identity changed"),
        (FixtureSite(gate_form=True), "login gate exposed a form"),
    ],
)
def test_contract_drift_fails_the_whole_snapshot(site: FixtureSite, error: str) -> None:
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error in meta["configured_collection_error"]


def test_expired_active_action_and_out_of_window_action_fail_before_details() -> None:
    expired = _courses()
    expired[5] = replace(
        expired[5],
        status="OPEN",
        application_start="2026-07-20 10:00",
        application_stop="2026-07-22 17:00",
        detail_state="접수중",
    )
    rows, _parser, meta = _collect(FixtureSite(expired))
    assert rows == []
    assert "expired row has an application action" in meta[
        "configured_collection_error"
    ]
    assert meta["detail_attempts"] == 0

    outside = _courses()
    outside[1] = replace(
        outside[1],
        application_start="2099-07-20 10:00",
        application_stop="2099-07-22 17:00",
    )
    rows, _parser, meta = _collect(FixtureSite(outside))
    assert rows == []
    assert "active application window mismatch" in meta[
        "configured_collection_error"
    ]
    assert meta["detail_attempts"] == 0


def test_page_and_detail_caps_fail_before_partial_publication() -> None:
    rows, _parser, meta = _collect(FixtureSite(), max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "3 declared list requests" in meta["configured_collection_error"]
    assert meta["list_requests"] == 1
    assert meta["detail_attempts"] == 0

    rows, _parser, meta = _collect(FixtureSite(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


@pytest.mark.parametrize("field", ["title", "venue", "branch"])
def test_phone_or_email_in_a_persisted_field_fails_closed(field: str) -> None:
    courses = _courses()
    courses[1] = replace(courses[1], **{field: "문의 061-350-1234"})
    rows, _parser, meta = _collect(FixtureSite(courses))
    assert rows == []
    assert "phone" in meta["configured_collection_error"]


def test_dedupe_mutation_invalidates_complete_snapshot() -> None:
    rows, _parser, meta = _collect(
        FixtureSite(), dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed complete row count" in meta[
        "configured_collection_error"
    ]


def test_target_builders_candidate_partition_and_owner_boundaries_are_exact() -> None:
    assert yeonggwang.is_yeonggwang_target(Target()) is True
    assert yeonggwang.is_yeonggwang_target(
        Target(url=yeonggwang.YEONGGWANG_CANDIDATE_URL)
    ) is False
    assert yeonggwang.is_yeonggwang_target(Target(provider="WRONG")) is False
    assert yeonggwang.is_yeonggwang_target(
        Target(url=yeonggwang.YEONGGWANG_CANONICAL_URL + "&offset=15")
    ) is False
    assert yeonggwang.yeonggwang_list_url(1) == yeonggwang.YEONGGWANG_CANONICAL_URL
    assert yeonggwang.yeonggwang_list_url(18).endswith("offset=255")
    assert yeonggwang.yeonggwang_detail_url("1168068").endswith(
        "type=view&bs_idx=1168068"
    )
    assert yeonggwang.yeonggwang_application_url("1168068").endswith(
        "type=application&bs_idx=1168068"
    )
    with pytest.raises(ValueError):
        yeonggwang.yeonggwang_list_url(0)
    with pytest.raises(ValueError):
        yeonggwang.yeonggwang_detail_url("1&evil=1")

    audit = yeonggwang.YEONGGWANG_CANDIDATE_AUDIT[
        yeonggwang.YEONGGWANG_CANDIDATE_ID
    ]
    assert audit["canonical_url"] == yeonggwang.YEONGGWANG_CANONICAL_URL
    tour = yeonggwang.YEONGGWANG_CANDIDATE_AUDIT[
        yeonggwang.YEONGGWANG_TOUR_CANDIDATE_ID
    ]
    assert tour["decision"] == "exclude_tourist_attraction_page"
    partition = yeonggwang.YEONGGWANG_CATEGORY_PARTITION_AUDIT
    assert sum(partition["filters"].values()) == 261
    assert partition["filtered_union_count"] == 261
    assert partition["pairwise_filter_overlap_count"] == 0
    assert set(partition["unclassified"]) == {"1078062", "1007328"}
    library = yeonggwang.YEONGGWANG_OWNER_BOUNDARY_AUDIT[
        yeonggwang.YEONGGWANG_COUNTY_LIBRARY_PROVIDER
    ]
    assert library["audited_rows"] == 71
    assert library["audited_current_rows"] == 4
    assert library["current_title_overlap_with_municipal"] == 0
    education_library = yeonggwang.YEONGGWANG_OWNER_BOUNDARY_AUDIT[
        yeonggwang.YEONGGWANG_EDUCATION_LIBRARY_PROVIDER
    ]
    assert education_library["exact_branch"] == (
        "전남광주통합특별시교육청영광도서관"
    )
    assert education_library["audited_current_rows"] == 6


def test_default_transport_is_verified_get_only_and_never_submits_forms() -> None:
    source = inspect.getsource(yeonggwang)
    assert "verify=False" not in source
    assert "verify = False" not in source
    assert ".post(" not in source
    assert "allow_redirects=False" in source


def test_invalid_target_returns_fail_closed_without_creating_session() -> None:
    rows, parser, meta = yeonggwang.collect_yeonggwang_education_courses(
        Target(url=yeonggwang.YEONGGWANG_CANDIDATE_URL),
        fetcher=lambda *_args: pytest.fail("fetcher must not run"),
        session_factory=lambda: pytest.fail("session must not be created"),
        today="2026-07-21",
    )
    assert rows == []
    assert parser == yeonggwang.YEONGGWANG_PARSER
    assert meta["snapshot_complete"] is False
    assert "canonical Yeonggwang" in meta["configured_collection_error"]


def test_session_setup_failure_is_returned_as_fail_closed_metadata() -> None:
    rows, parser, meta = yeonggwang.collect_yeonggwang_education_courses(
        Target(),
        fetcher=lambda *_args: pytest.fail("fetcher must not run"),
        session_factory=lambda: (_ for _ in ()).throw(RuntimeError("session failed")),
        today="2026-07-21",
    )
    assert rows == []
    assert parser == yeonggwang.YEONGGWANG_PARSER
    assert meta["snapshot_complete"] is False
    assert "client setup: RuntimeError: session failed" in meta[
        "configured_collection_error"
    ]


@pytest.mark.skipif(
    os.getenv("MOONCEN_RUN_YEONGGWANG_LIVE") != "1",
    reason="set MOONCEN_RUN_YEONGGWANG_LIVE=1 for the official-site audit",
)
def test_live_official_snapshot_opt_in() -> None:
    rows, _parser, meta = yeonggwang.collect_yeonggwang_education_courses(
        Target(), today="2026-07-21", max_pages=30, detail_limit=20
    )
    assert meta["configured_collection_error"] == ""
    assert meta["full_snapshot_validated"] is True
    assert meta["source_total"] == 263
    assert meta["page_counts"] == {**{page: 15 for page in range(1, 18)}, 18: 8}
    assert meta["required_list_requests"] == 19
    assert meta["current_count"] == 3
    assert len(rows) == 3
    assert {row["branch"] for row in rows} == {"인구교육정책실"}
