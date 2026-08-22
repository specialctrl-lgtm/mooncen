from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_yeongwol as yeongwol


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    start: str = "2026-01-01"
    end: str = "2026-01-31"
    apply_start: str = "2025-12-01"
    apply_end: str = "2025-12-15"
    status: str = "접수마감"
    category: str = "문화예술교육"
    schedule: str = "월 10:00~12:00"
    detail_schedule: str = "월요일 10시 00분 ~ 12시 00분"
    capacity_current: int = 3
    capacity_total: int = 12
    institution: str = "영월군 평생학습관"
    target: str = "성인"
    venue: str = "영월군 평생학습관 강의실"
    region: str = "영월권"
    fee: str = "무료"


class DummySession:
    def close(self) -> None:
        return None


def _short(value: str) -> str:
    return value[2:]


def _long(value: str) -> str:
    year, month, day = value.split("-")
    return f"{year}년 {month}월 {day}일"


def _form(*, filtered: bool = False) -> str:
    checked = " checked" if filtered else ""
    checkboxes = "".join(
        f'<input type="checkbox" name="{name}" value="x"{checked}>'
        for name in (
            "eduTagetYn",
            "weekDayYn",
            "ptimeYn",
            "appStatusYn",
            "priceYn",
            "recruitmentYn",
            "localYn",
            "edcCategoryYn",
        )
    )
    return f"""
      <form name="bbsNttSearchForm" class="boardSearchForm"
            method="get" action="./courseList.do">
        <input type="hidden" name="key" value="241">
        <input type="hidden" name="srcTitle" value="">
        <input type="hidden" name="srcEduName" value="">
        <select name="searchCnd"><option value="srcTitle" selected>강좌명</option></select>
        <input type="text" name="searchKrwd" value="">
        <select name="srcCategory"><option value="" selected>전체</option></select>
        {checkboxes}
      </form>
    """


def _card(course: Course, *, omit_schedule: bool = False) -> str:
    schedule = "" if omit_schedule else course.schedule
    if course.status == "접수마감":
        button_text, button_class = "강좌 신청 마감", "edu_end"
    elif course.status == "접수예정":
        button_text, button_class = "강좌 신청 예정", "edu_wait"
    else:
        button_text, button_class = "강좌 신청", "edu_ing"
    return f"""
      <li class="edu_item">
        <div class="edu_wrap">
          <span class="edu_state {button_class}">{escape(course.status)}</span>
          <span class="edu_type">{escape(course.category)}</span>
          <a class="edu_title"
             href="./courseView.do?key=241&amp;course={course.identity}">{escape(course.title)}</a>
          <ul class="edu_info">
            <li><span class="info_title">교육시간</span>
              <span class="info_text">{_short(course.start)} ~ {_short(course.end)}<br>{escape(schedule)}</span>
            </li>
            <li class="info_count"><span class="info_title">접수인원</span>
              <span class="info_text"><em class="count">{course.capacity_current}</em> / {course.capacity_total}</span>
            </li>
            <li class="info_place"><span class="info_title">교육기관</span>
              <span class="info_text">{escape(course.institution)}</span>
            </li>
            <li><span class="info_title">대상</span>
              <span class="info_text">{escape(course.target)}</span>
            </li>
          </ul>
        </div>
        <a class="edu_btn" href="#">{button_text}</a>
      </li>
    """


def _list_html(
    page: int,
    total: int,
    rows: list[Course],
    *,
    filtered: bool = False,
    bad_last_link: bool = False,
    omit_schedule_ids: frozenset[str] = frozenset(),
) -> str:
    last = max(1, (total + yeongwol.YEONGWOL_PAGE_SIZE - 1) // yeongwol.YEONGWOL_PAGE_SIZE)
    linked_last = max(1, last - 1) if bad_last_link else last
    body = "".join(
        _card(item, omit_schedule=item.identity in omit_schedule_ids)
        for item in rows
    )
    if not rows:
        body = '<p class="p-empty">등록된 게시물이 없습니다.</p>'
    return f"""
      <html><head><title>평생학습강좌(★수강신청★) - 영월군 평생학습 평생교육</title></head>
      <body><div class="p-wrap bbs bbs__list">
        {_form(filtered=filtered)}
        <p class="small">총 <em data-mask="number">{total}</em>건</p>
        <ul class="edu_list">{body}</ul>
        <div class="p-pagination">
          <a class="nextEnd" href="./courseList.do?key=241&amp;pageIndex={linked_last}">마지막</a>
        </div>
      </div></body></html>
    """


def _detail_html(
    course: Course,
    *,
    title: str | None = None,
    institution: str | None = None,
    period: str | None = None,
    extra_control: str = "auto",
) -> str:
    detail_period = period or f"{_long(course.start)} ~ {_long(course.end)}"
    if extra_control == "auto":
        show_control = course.status == "접수중"
        safe_control = True
    else:
        show_control = extra_control != "missing"
        safe_control = extra_control == "safe"
    control = ""
    if show_control:
        href = (
            f"./courseApply.do?key=241&amp;course={course.identity}"
            if safe_control
            else "https://evil.example/apply?course=999"
        )
        control = f'<a class="bbs_btn apply" href="{href}">수강신청</a>'
    return f"""
      <html><head><title>평생학습강좌(★수강신청★) - 영월군 평생학습 평생교육</title></head>
      <body><div id="contents">
        <div class="board clearfix"><table class="bbs_default view">
          <caption>게시판 보기</caption>
          <tr><th colspan="4">강좌정보</th></tr>
          <tr><th>강좌명</th><td colspan="3">{escape(title if title is not None else course.title)}</td></tr>
          <tr><th>분야</th><td>{escape(course.category)}</td><th>교육대상</th><td>{escape(course.target)}</td></tr>
          <tr><th>교육장소</th><td>{escape(course.venue)}</td><th>지역</th><td>{escape(course.region)}</td></tr>
          <tr><th>모집인원</th><td colspan="3">{course.capacity_current}명 접수 / 총 {course.capacity_total}명 모집 (방문접수 2명)</td></tr>
          <tr><th>접수기간</th><td colspan="3">{_long(course.apply_start)} 09시 ~ {_long(course.apply_end)} 18시</td></tr>
          <tr><th>교육기간</th><td colspan="3">{detail_period}</td></tr>
          <tr><th>교육시간</th><td>{escape(course.detail_schedule)}</td><th>강사명</th><td>개인강사</td></tr>
          <tr><th>수강료</th><td>{escape(course.fee)}</td><th>재료비</th><td>60,000원</td></tr>
          <tr><th>자격증발급비</th><td colspan="3"></td></tr>
          <tr><th>교육내용</th><td colspan="3">전화 033-123-4567 및 자유 서술</td></tr>
          <tr><th>비고</th><td colspan="3">개인 연락처가 포함될 수 있는 자유 서술</td></tr>
          <tr><th>첨부파일</th><td colspan="3"><a href="/secret.pdf">계획서</a></td></tr>
        </table></div>
        <div class="board clearfix"><table class="bbs_default view">
          <caption>게시판 보기</caption>
          <tr><th colspan="4">교육기관 정보</th></tr>
          <tr><th>교육기관</th><td colspan="3">{escape(institution if institution is not None else course.institution)}</td></tr>
          <tr><th>주소</th><td colspan="3">강원특별자치도 영월군 예시로 1</td></tr>
          <tr><th>담당자</th><td>홍길동</td><th>연락처</th><td>033-123-4567</td></tr>
        </table></div>
        <div class="bbs_left"><a class="bbs_btn list"
          href="./courseList.do?key=241&amp;srcEdu=288&amp;pageIndex=1">목록</a></div>
        {control}
      </div></body></html>
    """


def _courses() -> list[Course]:
    rows = [
        Course(str(identity), f"과거 강좌 {identity}")
        for identity in range(21, 0, -1)
    ]
    rows[0] = Course(
        "21",
        "현재 패브릭 캘리그라피",
        start="2026-04-06",
        end="2026-07-27",
        apply_start="2026-03-02",
        apply_end="2026-03-15",
        capacity_current=10,
        capacity_total=12,
        institution="김삿갓면주민자치센터",
        venue="김삿갓면 문화복지센터",
        region="김삿갓권",
    )
    return rows


class HtmlFixture:
    def __init__(
        self,
        *,
        rows: list[Course] | None = None,
        nonempty_sentinel: bool = False,
        unstable_page_one: bool = False,
        filtered_form: bool = False,
        bad_last_link: bool = False,
        page_two_total: int | None = None,
        detail_title_mismatch: bool = False,
        detail_institution_mismatch: bool = False,
        detail_period_mismatch: bool = False,
        detail_control: str = "auto",
        current_missing_schedule: bool = False,
    ) -> None:
        self.rows = list(rows if rows is not None else _courses())
        self.total = len(self.rows)
        self.nonempty_sentinel = nonempty_sentinel
        self.unstable_page_one = unstable_page_one
        self.filtered_form = filtered_form
        self.bad_last_link = bad_last_link
        self.page_two_total = page_two_total
        self.detail_title_mismatch = detail_title_mismatch
        self.detail_institution_mismatch = detail_institution_mismatch
        self.detail_period_mismatch = detail_period_mismatch
        self.detail_control = detail_control
        self.current_missing_schedule = current_missing_schedule
        self.calls: Counter[str] = Counter()
        self.lock = Lock()

    def __call__(self, _session: DummySession, url: str, _timeout: int) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        with self.lock:
            self.calls[url] += 1
            call_number = self.calls[url]
        if parsed.path == yeongwol.YEONGWOL_DETAIL_PATH:
            identity = query["course"][0]
            course = next(item for item in self.rows if item.identity == identity)
            return _detail_html(
                course,
                title="다른 강좌" if self.detail_title_mismatch else None,
                institution=(
                    "다른 교육기관" if self.detail_institution_mismatch else None
                ),
                period=(
                    "2026년 08월 01일 ~ 2026년 08월 31일"
                    if self.detail_period_mismatch
                    else None
                ),
                extra_control=self.detail_control,
            )
        if parsed.path != yeongwol.YEONGWOL_LIST_PATH:
            raise AssertionError(f"unexpected path: {parsed.path}")
        page = int(query.get("pageIndex", ["1"])[0])
        last = max(
            1,
            (self.total + yeongwol.YEONGWOL_PAGE_SIZE - 1)
            // yeongwol.YEONGWOL_PAGE_SIZE,
        )
        if page <= last:
            begin = (page - 1) * yeongwol.YEONGWOL_PAGE_SIZE
            page_rows = self.rows[begin : begin + yeongwol.YEONGWOL_PAGE_SIZE]
        else:
            page_rows = []
        if page == last + 1 and self.nonempty_sentinel:
            page_rows = [self.rows[-1]]
        if page == 1 and call_number > 1 and self.unstable_page_one and page_rows:
            page_rows = [replace(page_rows[0], title="재검증 중 변경"), *page_rows[1:]]
        total = (
            self.page_two_total
            if page == 2 and self.page_two_total is not None
            else self.total
        )
        omit = (
            frozenset({self.rows[0].identity})
            if self.current_missing_schedule
            else frozenset()
        )
        return _list_html(
            page,
            total,
            page_rows,
            filtered=self.filtered_form,
            bad_last_link=self.bad_last_link,
            omit_schedule_ids=omit,
        )


def _collect(fixture: HtmlFixture, **kwargs):
    options = {
        "today": "2026-07-21",
        "timeout": 5,
        "max_pages": 10,
        "detail_limit": 10,
        "max_workers": 4,
        "session_factory": DummySession,
        "fetcher": fixture,
    }
    options.update(kwargs)
    return yeongwol.collect_yeongwol_education(
        Target(yeongwol.YEONGWOL_PROVIDER, yeongwol.YEONGWOL_CANONICAL_URL),
        **options,
    )


def test_candidate_audit_and_canonical_identity_are_exhaustive() -> None:
    assert yeongwol.YEONGWOL_PROVIDER == "MUNI_LLL_YW_GO_KR_EF1034A0"
    assert yeongwol.YEONGWOL_CANONICAL_CANDIDATE_ID == "MUNI_IR_C8CD97987323"
    assert yeongwol.YEONGWOL_MUNICIPALITY_CODE == "5175000000"
    assert set(yeongwol.YEONGWOL_CANDIDATE_AUDIT) == {
        "MUNI_IR_0CA08FDF06B7",
        "MUNI_IR_3ED99D175BC0",
        "MUNI_IR_5CDB62B96391",
        "MUNI_IR_CB6DEBAAEBD3",
        "MUNI_IR_F2564D73C044",
    }
    decisions = Counter(
        item["decision"] for item in yeongwol.YEONGWOL_CANDIDATE_AUDIT.values()
    )
    assert decisions == {
        "separate_library_owner_homepage_alias": 1,
        "subset_category_alias": 1,
        "subset_time_alias": 1,
        "excluded_expired_donation_detail": 1,
        "excluded_education_institution_directory": 1,
    }
    audit = yeongwol.YEONGWOL_DISCOVERY_AUDIT
    assert audit["unfiltered_historical_rows"] == 889
    assert audit["night_subset_rows"] == 324
    assert audit["career_subset_rows"] == 100
    assert audit["night_career_intersection_rows"] == 39
    assert audit["source_status_counts"] == {
        "접수예정": 0,
        "접수중": 0,
        "접수마감": 889,
    }


def test_target_alias_exclusion_and_separate_library_boundaries() -> None:
    canonical = Target(
        yeongwol.YEONGWOL_PROVIDER, yeongwol.YEONGWOL_CANONICAL_URL
    )
    assert yeongwol.is_yeongwol_education_target(canonical)
    assert not yeongwol.is_yeongwol_education_target(
        Target(yeongwol.YEONGWOL_PROVIDER, yeongwol.YEONGWOL_EQUIVALENT_UNFILTERED_URL)
    )
    assert not yeongwol.is_yeongwol_education_target(
        Target("MUNI_LLL_YW_GO_KR_DCB266C5", yeongwol.YEONGWOL_CANONICAL_URL)
    )
    assert not yeongwol.is_yeongwol_education_target(
        Target(
            yeongwol.YEONGWOL_PROVIDER,
            yeongwol.YEONGWOL_CANONICAL_URL + "&pageIndex=1",
        )
    )
    assert yeongwol.is_yeongwol_owned_alias_target(
        Target("", yeongwol.YEONGWOL_NIGHT_SUBSET_URL, "MUNI_IR_5CDB62B96391")
    )
    assert yeongwol.is_yeongwol_owned_alias_target(
        Target("MUNI_LLL_YW_GO_KR_022DBD52", yeongwol.YEONGWOL_PORTAL_URL)
    )
    assert yeongwol.is_yeongwol_owned_alias_target(
        Target("", yeongwol.YEONGWOL_EQUIVALENT_UNFILTERED_URL)
    )
    directory = Target(
        "MUNI_LLL_YW_GO_KR_022DBD52",
        yeongwol.YEONGWOL_INSTITUTION_DIRECTORY_URL,
        "MUNI_IR_F2564D73C044",
    )
    assert yeongwol.is_yeongwol_excluded_candidate(directory)
    assert not yeongwol.is_yeongwol_owned_alias_target(directory)
    assert yeongwol.is_yeongwol_excluded_candidate(
        Target("", yeongwol.YEONGWOL_DONATION_DETAIL_URL)
    )
    library = Target(
        "MUNI_LIB_GWE_GO_KR_90FD6E6A",
        yeongwol.YEONGWOL_LIBRARY_HOMEPAGE_URL,
        "MUNI_IR_0CA08FDF06B7",
    )
    assert yeongwol.is_yeongwol_separate_library_target(library)
    assert not yeongwol.is_yeongwol_owned_alias_target(library)


def test_url_builders_are_bounded() -> None:
    assert yeongwol.yeongwol_list_url(1) == yeongwol.YEONGWOL_CANONICAL_URL
    assert parse_qs(urlparse(yeongwol.yeongwol_list_url(89)).query) == {
        "key": ["241"],
        "searchCnd": ["srcTitle"],
        "pageIndex": ["89"],
    }
    assert yeongwol.yeongwol_list_url(0) == ""
    assert yeongwol.yeongwol_list_url(True) == ""
    assert yeongwol.yeongwol_detail_url("3907").endswith("key=241&course=3907")
    assert yeongwol.yeongwol_detail_url("../3907") == ""


def test_complete_snapshot_traverses_boundaries_and_discards_pii() -> None:
    fixture = HtmlFixture()
    rows, parser, meta = _collect(fixture)
    assert parser == yeongwol.YEONGWOL_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"].endswith(":21")
    assert row["title"] == "현재 패브릭 캘리그라피"
    assert row["branch"] == "김삿갓면주민자치센터"
    assert row["venue"] == "김삿갓면 문화복지센터"
    assert row["period"] == "2026-04-06 ~ 2026-07-27"
    assert row["apply_start"] == "2026-03-02"
    assert row["apply_end"] == "2026-03-15"
    assert row["status"] == "CLOSED"
    assert row["reservation_available"] is False
    assert row["application_url"] == ""
    assert row["fee"] == "무료"
    assert row["raw_fields"]["application_control_verified"] is True
    payload = repr(row)
    assert "개인강사" not in payload
    assert "홍길동" not in payload
    assert "033-123-4567" not in payload
    assert "secret.pdf" not in payload
    assert "자유 서술" not in payload
    assert meta["source_total"] == 21
    assert meta["declared_pages"] == 3
    assert meta["required_list_requests"] == 5
    assert meta["list_requests"] == 5
    assert meta["page_counts"] == {1: 10, 2: 10, 3: 1}
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 1
    assert meta["current_source_count"] == 1
    assert meta["expired_count"] == 20
    assert meta["detail_attempts"] == meta["detail_pages"] == 1
    assert meta["application_control_count"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["returned_count"] == 1
    assert meta["pii_payload_persisted"] is False
    assert meta["configured_collection_error"] == ""
    assert fixture.calls[yeongwol.yeongwol_list_url(1)] == 2
    assert fixture.calls[yeongwol.yeongwol_list_url(4)] == 1


def test_complete_historical_catalogue_can_return_verified_no_current_data() -> None:
    rows = [replace(item, end="2026-07-20") for item in _courses()]
    fixture = HtmlFixture(rows=rows)
    result, _parser, meta = _collect(fixture)
    assert result == []
    assert meta["source_rows"] == 21
    assert meta["current_source_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True


def test_live_open_control_must_be_official_and_course_bound() -> None:
    current = replace(
        _courses()[0],
        status="접수중",
        apply_start="2026-07-01",
        apply_end="2026-07-31",
    )
    fixture = HtmlFixture(rows=[current], detail_control="safe")
    rows, _parser, meta = _collect(fixture)
    assert len(rows) == 1
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_type"] == "ONLINE_RESERVATION"
    assert rows[0]["application_url"].endswith("key=241&course=21")
    assert meta["online_open_count"] == 1
    assert meta["application_control_count"] == 1
    assert meta["snapshot_complete"] is True


@pytest.mark.parametrize("control", ["missing", "unsafe"])
def test_open_course_without_safe_course_bound_control_fails_closed(control: str) -> None:
    current = replace(
        _courses()[0],
        status="접수중",
        apply_start="2026-07-01",
        apply_end="2026-07-31",
    )
    result, _parser, meta = _collect(
        HtmlFixture(rows=[current], detail_control=control)
    )
    assert result == []
    assert meta["snapshot_complete"] is False
    assert "application control" in meta["configured_collection_error"]


def test_closed_course_with_extra_application_control_fails_closed() -> None:
    result, _parser, meta = _collect(HtmlFixture(detail_control="safe"))
    assert result == []
    assert meta["snapshot_complete"] is False
    assert "inactive course exposes application control" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("fixture", "needle"),
    [
        (HtmlFixture(nonempty_sentinel=True), "sentinel"),
        (HtmlFixture(unstable_page_one=True), "stability"),
        (HtmlFixture(filtered_form=True), "unfiltered checkbox"),
        (HtmlFixture(bad_last_link=True), "last-page navigation"),
        (HtmlFixture(page_two_total=22), "total/last changed"),
        (HtmlFixture(detail_title_mismatch=True), "강좌명 list/detail mismatch"),
        (
            HtmlFixture(detail_institution_mismatch=True),
            "교육기관 list/detail mismatch",
        ),
        (HtmlFixture(detail_period_mismatch=True), "education period list/detail mismatch"),
        (HtmlFixture(current_missing_schedule=True), "current course field invalid"),
    ],
)
def test_contract_drift_never_returns_a_partial_snapshot(
    fixture: HtmlFixture, needle: str
) -> None:
    result, _parser, meta = _collect(fixture)
    assert result == []
    assert meta["snapshot_complete"] is False
    assert needle in meta["configured_collection_error"]


def test_duplicate_official_identity_and_unknown_status_fail_closed() -> None:
    duplicate_rows = _courses()
    duplicate_rows[-1] = replace(duplicate_rows[-1], identity="2")
    result, _parser, meta = _collect(HtmlFixture(rows=duplicate_rows))
    assert result == []
    assert "duplicate official identities" in meta["configured_collection_error"]

    unknown_rows = [replace(_courses()[0], status="임의상태")]
    result, _parser, meta = _collect(HtmlFixture(rows=unknown_rows))
    assert result == []
    assert "unknown application status" in meta["configured_collection_error"]


def test_caps_invalid_target_and_privacy_mutation_fail_closed() -> None:
    fixture = HtmlFixture()
    result, _parser, meta = _collect(fixture, max_pages=4)
    assert result == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    result, _parser, meta = _collect(HtmlFixture(), detail_limit=0)
    assert result == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    result, _parser, meta = yeongwol.collect_yeongwol_education(
        Target("WRONG", yeongwol.YEONGWOL_CANONICAL_URL),
        session_factory=DummySession,
        fetcher=fixture,
    )
    assert result == []
    assert "does not match canonical" in meta["configured_collection_error"]

    def leak(rows: list[dict]) -> list[dict]:
        rows[0]["contact"] = "033-123-4567"
        return rows

    result, _parser, meta = _collect(HtmlFixture(), dedupe_rows=leak)
    assert result == []
    assert "forbidden PII" in meta["configured_collection_error"]


def test_detail_limit_is_applied_to_every_current_or_future_row() -> None:
    current_rows = [
        replace(
            item,
            start="2026-07-01",
            end="2026-08-01",
            apply_start="2026-06-01",
            apply_end="2026-06-15",
        )
        for item in _courses()[:2]
    ]
    result, _parser, meta = _collect(
        HtmlFixture(rows=current_rows), detail_limit=1
    )
    assert result == []
    assert meta["current_source_count"] == 2
    assert meta["detail_attempts"] == 0
    assert "detail_limit cap allows 1 of 2" in meta["configured_collection_error"]
