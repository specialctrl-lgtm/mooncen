from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_yesan as yesan


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
    apply_end: str = "2025-12-02"
    place: str = "예산군평생학습관"
    organizer: str = "예산군평생학습관"
    category: str = "인문교양"
    mode: str = "온라인모집"
    recruitment_status: str = "모집마감"
    education_status: str = "교육마감"
    selection_method: str = "선착"
    target: str = "성인"
    schedule: str = "화] 10:00~12:00"
    venue: str = "예산읍 / 예산군평생학습관"
    capacity_current: int = 3
    capacity_total: int = 20


class DummySession:
    def close(self) -> None:
        return None


def _form(page: int, *, filtered: bool = False, detail_id: str = "") -> str:
    detail_fields = ""
    if detail_id:
        detail_fields = (
            '<input name="pageUnit" value="20">'
            f'<input name="copyUrlData" value="lctrNo={detail_id}">'
        )
    return f"""
      <form id="searchForm" action="{yesan.YESAN_LIST_PATH}" method="post">
        <input name="pageIndex" value="{page}">
        <input name="lctrNo" value="{detail_id}">
        {detail_fields}
        <select name="searchEmd"><option value="">전체</option></select>
        <select name="searchInst"><option value="">전체</option></select>
        <select name="searchFld"><option value="{'06' if filtered else ''}">전체</option></select>
        <select name="searchTrgt"><option value="">전체</option></select>
        <input name="searchBgnDt" value="">
        <input name="searchEndDt" value="">
        <select name="searchSe"><option value="">전체</option></select>
        <input name="searchKeyword" value="">
      </form>
    """


def _card(course: Course, *, omit_schedule: bool = False) -> str:
    schedule = "" if omit_schedule else (
        f'<li><span class="tit">교육시간</span>'
        f'<em class="txt">{escape(course.schedule)}</em></li>'
    )
    return f"""
      <div class="item"><div class="item-inner">
        <a class="inner-box" onclick="fn_search_view('{course.identity}'); return false;">
          <div class="type-wrap">
            <span class="place">[{escape(course.place)}]</span>
            <span class="status status5">{escape(course.category)}</span>
            <span class="type type1 ico">{escape(course.mode)}</span>
            <span class="status status14">{escape(course.recruitment_status)}</span>
            <span class="status status6">{escape(course.education_status)}</span>
            <span class="status status9 outline">{escape(course.selection_method)}</span>
          </div>
          <strong class="title">{escape(course.title)}</strong>
          <ul class="list-1st">
            <li><span class="tit">모집기간</span><em class="txt">
              {course.apply_start} 10:00 ~ {course.apply_end} 17:00
            </em></li>
            <li><span class="tit">교육기간</span><em class="txt">{course.start} ~ {course.end}</em></li>
            <li><span class="tit">교육대상</span><em class="txt">{escape(course.target)}</em></li>
            {schedule}
            <li><span class="tit">강의장소</span><em class="txt">{escape(course.venue)}</em></li>
            <li><span class="tit">주최기관</span><em class="txt">{escape(course.organizer)}</em></li>
          </ul>
          <div class="apply-status">
            <span class="current">{course.capacity_current}</span>
            <span class="total">{course.capacity_total}명</span>
          </div>
        </a>
      </div></div>
    """


def _list_html(
    page: int,
    total: int,
    rows: list[Course],
    *,
    filtered: bool = False,
    omit_schedule_ids: frozenset[str] = frozenset(),
) -> str:
    last = max(1, (total + yesan.YESAN_PAGE_SIZE - 1) // yesan.YESAN_PAGE_SIZE)
    body = "".join(
        _card(item, omit_schedule=item.identity in omit_schedule_ids) for item in rows
    )
    if not rows:
        body = (
            '<div class="PRGRM_nodata PRGRM_list-nodata">'
            "현재 데이터 준비중 입니다</div>"
        )
    return f"""
      <html><head><title>온라인강좌 신청 &lt; 평생학습 프로그램 &lt; 예산군 평생학습</title></head>
      <body><div class="edu-search-list mt_50 type2">
        {_form(page, filtered=filtered)}
        <p>전체 게시물 검색 총 {total} 건의 강좌가 검색되었습니다</p>
        <div class="list-wrap">{body}</div>
        <div class="pagination"><a href="?pageIndex={last}">마지막</a></div>
      </div></body></html>
    """


def _detail_html(
    course: Course,
    *,
    button: bool | None = None,
    handler: bool = True,
    identity: str | None = None,
    title: str | None = None,
    period: str | None = None,
) -> str:
    detail_id = identity if identity is not None else course.identity
    show_button = (
        course.mode == "온라인모집" and course.recruitment_status == "모집중"
        if button is None
        else button
    )
    control = (
        '<button type="button" class="button_write">수강신청</button>'
        if show_button
        else ""
    )
    script = (
        f'<script>$(".button_write").click(function () {{ '
        f'fn_submit("{yesan.YESAN_APPLICATION_PATH}"); }});</script>'
        if handler
        else "<script>function unrelated() { return true; }</script>"
    )
    education_period = period or f"{course.start} ~ {course.end}"
    return f"""
      <html><head><title>{escape(course.title)} - 예산군 평생학습</title></head><body>
      {_form(1, detail_id=detail_id)}
      <div class="view-wrap">
        <div class="type-wrap">
          <span class="status status5">{escape(course.category)}</span>
          <span class="type type1 ico">{escape(course.mode)}</span>
          <span class="status status14">{escape(course.recruitment_status)}</span>
          <span class="status status6">{escape(course.education_status)}</span>
        </div>
        <h3 class="title">{escape(title if title is not None else course.title)}</h3>
        <ul class="list-1st">
          <li class="info"><span class="tit">모집기간</span>{course.apply_start} 10:00 ~ {course.apply_end} 17:00</li>
          <li class="info"><span class="tit">교육기간</span>{education_period}</li>
          <li class="info"><span class="tit">교육대상</span>{escape(course.target)}</li>
          <li class="info"><span class="tit">교육시간</span>{escape(course.schedule)}</li>
          <li class="info"><span class="tit">강의장소</span>{escape(course.venue)}</li>
          <li class="info"><span class="tit">교재</span>재료비 30,000원</li>
          <li class="info"><span class="tit">문의전화</span>041-339-8965</li>
          <li class="info"><span class="tit">강사명</span>홍길동</li>
        </ul>
        <div class="free-description">개인 연락처가 포함될 수 있는 자유 서술</div>
        {control}
      </div>{script}</body></html>
    """


def _courses() -> list[Course]:
    rows = [Course(str(identity), f"과거 강좌 {identity}") for identity in range(21, 0, -1)]
    rows[0] = Course(
        "21",
        "현재 마감 강좌",
        start="2026-07-01",
        end="2026-08-01",
        apply_start="2026-06-01",
        apply_end="2026-06-10",
        education_status="교육중",
        place="예산군평생학습관",
    )
    rows[-1] = Course(
        "1",
        "현재 모집 강좌",
        start="2026-08-01",
        end="2026-08-31",
        apply_start="2026-07-01",
        apply_end="2026-08-01",
        recruitment_status="모집중",
        education_status="교육예정",
        place="내포신도시 평생학습센터",
        organizer="내포신도시 평생학습센터",
        venue="삽교읍 / 내포신도시 평생학습센터",
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
        page_two_total: int | None = None,
        detail_title_mismatch: bool = False,
        detail_identity_mismatch: bool = False,
        detail_period_mismatch: bool = False,
        open_missing_control: bool = False,
        closed_extra_control: bool = False,
        missing_handler: bool = False,
        current_missing_schedule: bool = False,
        omit_schedule_ids: frozenset[str] = frozenset(),
    ) -> None:
        self.rows = list(rows if rows is not None else _courses())
        self.total = len(self.rows)
        self.nonempty_sentinel = nonempty_sentinel
        self.unstable_page_one = unstable_page_one
        self.filtered_form = filtered_form
        self.page_two_total = page_two_total
        self.detail_title_mismatch = detail_title_mismatch
        self.detail_identity_mismatch = detail_identity_mismatch
        self.detail_period_mismatch = detail_period_mismatch
        self.open_missing_control = open_missing_control
        self.closed_extra_control = closed_extra_control
        self.missing_handler = missing_handler
        self.current_missing_schedule = current_missing_schedule
        self.omit_schedule_ids = omit_schedule_ids
        self.calls: Counter[str] = Counter()
        self.lock = Lock()

    def __call__(self, _session: DummySession, url: str, _timeout: int) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        with self.lock:
            self.calls[url] += 1
            call_number = self.calls[url]
        if parsed.path == yesan.YESAN_DETAIL_PATH:
            identity = query["lctrNo"][0]
            course = next(item for item in self.rows if item.identity == identity)
            button: bool | None = None
            if course.recruitment_status == "모집중" and self.open_missing_control:
                button = False
            if course.recruitment_status != "모집중" and self.closed_extra_control:
                button = True
            return _detail_html(
                course,
                button=button,
                handler=not self.missing_handler,
                identity=("999" if self.detail_identity_mismatch else None),
                title=("변경된 제목" if self.detail_title_mismatch else None),
                period=("2026-08-02 ~ 2026-09-01" if self.detail_period_mismatch else None),
            )
        if parsed.path != yesan.YESAN_LIST_PATH:
            raise AssertionError(f"unexpected path: {parsed.path}")
        page = int(query.get("pageIndex", ["1"])[0])
        last = max(1, (self.total + yesan.YESAN_PAGE_SIZE - 1) // yesan.YESAN_PAGE_SIZE)
        if page <= last:
            begin = (page - 1) * yesan.YESAN_PAGE_SIZE
            page_rows = self.rows[begin : begin + yesan.YESAN_PAGE_SIZE]
        else:
            page_rows = []
        if page == last + 1 and self.nonempty_sentinel:
            page_rows = [self.rows[-1]]
        if page == 1 and call_number > 1 and self.unstable_page_one and page_rows:
            page_rows = [replace(page_rows[0], title="재조회 중 변경"), *page_rows[1:]]
        total = self.page_two_total if page == 2 and self.page_two_total is not None else self.total
        omit_ids = self.omit_schedule_ids | (
            frozenset({"21"}) if self.current_missing_schedule else frozenset({"2"})
        )
        return _list_html(
            page,
            total,
            page_rows,
            filtered=self.filtered_form,
            omit_schedule_ids=omit_ids,
        )


def _collect(fixture: HtmlFixture, **kwargs):
    return yesan.collect_yesan_education(
        Target(yesan.YESAN_PROVIDER, yesan.YESAN_CANONICAL_URL),
        today="2026-07-21",
        timeout=5,
        max_pages=20,
        detail_limit=20,
        max_workers=4,
        session_factory=DummySession,
        fetcher=fixture,
        **kwargs,
    )


def test_candidate_audit_selects_unfiltered_canonical_owner() -> None:
    assert yesan.YESAN_PROVIDER == "MUNI_WWW_YESAN_GO_KR_AC1B96E1"
    assert yesan.YESAN_CANONICAL_CANDIDATE_ID == "MUNI_IR_DA028F76EEF2"
    assert yesan.YESAN_MUNICIPALITY_CODE == "4481000000"
    assert set(yesan.YESAN_CANDIDATE_AUDIT) == {
        "MUNI_IR_115D0BDDBCD1",
        "MUNI_IR_4656E90DB7E2",
        "MUNI_IR_65D9986F213A",
        "MUNI_IR_87C88406967B",
        "MUNI_IR_DB56FB51A33C",
    }
    decisions = Counter(
        value["decision"] for value in yesan.YESAN_CANDIDATE_AUDIT.values()
    )
    assert decisions == {
        "subset_category_alias": 1,
        "excluded_single_announcement_evidence_only": 1,
        "excluded_general_county_homepage": 1,
        "excluded_provincial_static_overview": 1,
        "subset_homepage_alias": 1,
    }
    assert yesan.YESAN_DISCOVERY_AUDIT["unfiltered_historical_rows"] == 856
    assert yesan.YESAN_DISCOVERY_AUDIT["computer_subset_historical_rows"] == 38


def test_target_and_alias_boundaries_are_exact() -> None:
    canonical = Target(yesan.YESAN_PROVIDER, yesan.YESAN_CANONICAL_URL)
    assert yesan.is_yesan_education_target(canonical)
    assert not yesan.is_yesan_education_target(
        Target(yesan.YESAN_PROVIDER, yesan.YESAN_COMPUTER_SUBSET_URL)
    )
    assert not yesan.is_yesan_education_target(
        Target("MUNI_WWW_YESAN_GO_KR_DB7F84C1", yesan.YESAN_CANONICAL_URL)
    )
    assert not yesan.is_yesan_education_target(
        Target(yesan.YESAN_PROVIDER, "http://www.yesan.go.kr" + yesan.YESAN_LIST_PATH)
    )
    assert yesan.is_yesan_owned_alias_target(
        Target("", yesan.YESAN_COMPUTER_SUBSET_URL, "MUNI_IR_115D0BDDBCD1")
    )
    assert yesan.is_yesan_owned_alias_target(
        Target("", "https://www.yesan.go.kr/edu/")
    )
    assert yesan.is_yesan_excluded_candidate(
        Target("", "https://www.yesan.go.kr/index.jsp", "MUNI_IR_65D9986F213A")
    )
    assert not yesan.is_yesan_owned_alias_target(canonical)


def test_url_builders_are_bounded() -> None:
    assert yesan.yesan_list_url(1) == yesan.YESAN_CANONICAL_URL
    assert yesan.yesan_list_url(3) == yesan.YESAN_CANONICAL_URL + "?pageIndex=3"
    assert yesan.yesan_list_url(True) == ""
    assert yesan.yesan_list_url(0) == ""
    assert yesan.yesan_detail_url("894") == (
        "https://www.yesan.go.kr/prog/lctr/edu/sub01_01/view.do?lctrNo=894"
    )
    assert yesan.yesan_detail_url("894&admin=true") == ""


def test_complete_snapshot_traverses_pages_sentinel_recheck_and_details() -> None:
    fixture = HtmlFixture()
    rows, parser, meta = _collect(fixture)
    assert parser == yesan.YESAN_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == ["21", "1"]
    assert meta["source_total"] == 21
    assert meta["declared_pages"] == 2
    assert meta["page_counts"] == {1: 20, 2: 1}
    assert meta["required_list_requests"] == 4
    assert meta["list_requests"] == 4
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 1
    assert meta["current_source_count"] == 2
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["historical_missing_schedule_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""
    assert fixture.calls[yesan.YESAN_CANONICAL_URL] == 2
    assert fixture.calls[yesan.yesan_list_url(3)] == 1

    closed, opened = rows
    assert closed["status"] == "CLOSED"
    assert closed["reservation_available"] is False
    assert closed["application_url"] == ""
    assert opened["status"] == "OPEN"
    assert opened["reservation_available"] is True
    assert opened["application_type"] == "ONLINE_RESERVATION"
    assert opened["application_url"] == opened["raw_url"]
    assert opened["raw_fields"]["application_control_present"] is True
    assert all(row["fee"] == "요금 별도 안내" for row in rows)
    assert all(row["venue_name"] == row["venue"] for row in rows)


def test_detail_payload_is_reduced_to_pii_safe_allowlist() -> None:
    rows, _parser, meta = _collect(HtmlFixture())
    assert meta["snapshot_complete"] is True
    assert meta["pii_payload_persisted"] is False
    payload = repr(rows)
    assert "041-339-8965" not in payload
    assert "홍길동" not in payload
    assert "재료비 30,000원" not in payload
    assert "개인 연락처가 포함될 수 있는 자유 서술" not in payload
    for row in rows:
        assert row["description"] == row["title"]
        for field in yesan.YESAN_PII_FIELDS_DISCARDED:
            assert field not in row
            assert field not in row["raw_fields"]


@pytest.mark.parametrize(
    ("fixture_kwargs", "error_text"),
    [
        ({"nonempty_sentinel": True}, "sentinel page is not empty"),
        ({"unstable_page_one": True}, "stability recheck changed"),
        ({"filtered_form": True}, "unfiltered form field searchFld changed"),
        ({"page_two_total": 22}, "total/last changed"),
        ({"current_missing_schedule": True}, "current course field is empty"),
    ],
)
def test_list_contract_failures_return_no_partial_snapshot(
    fixture_kwargs: dict[str, object], error_text: str
) -> None:
    rows, _parser, meta = _collect(HtmlFixture(**fixture_kwargs))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_text in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("fixture_kwargs", "error_text"),
    [
        ({"detail_title_mismatch": True}, "title/list mismatch"),
        ({"detail_identity_mismatch": True}, "form identity field lctrNo changed"),
        ({"detail_period_mismatch": True}, "교육기간 list/detail mismatch"),
        ({"open_missing_control": True}, "open course has no unique application control"),
        ({"closed_extra_control": True}, "inactive/offline course exposes application control"),
        ({"missing_handler": True}, "official application handler changed"),
    ],
)
def test_detail_and_application_contract_failures_are_fail_closed(
    fixture_kwargs: dict[str, object], error_text: str
) -> None:
    rows, _parser, meta = _collect(HtmlFixture(**fixture_kwargs))
    assert rows == []
    assert meta["details_complete"] is False
    assert meta["snapshot_complete"] is False
    assert error_text in meta["configured_collection_error"]


def test_offline_open_course_is_verified_without_online_url() -> None:
    rows = _courses()
    rows[-1] = replace(rows[-1], mode="오프라인모집")
    result, _parser, meta = _collect(HtmlFixture(rows=rows))
    assert meta["snapshot_complete"] is True
    offline = next(row for row in result if row["raw_fields"]["identity"] == "1")
    assert offline["status"] == "OPEN"
    assert offline["application_type"] == "OFFLINE_APPLICATION"
    assert offline["reservation_available"] is False
    assert offline["application_url"] == ""


def test_cancelled_current_row_is_detail_checked_but_not_emitted() -> None:
    rows = _courses()
    rows[0] = replace(rows[0], education_status="교육취소")
    result, _parser, meta = _collect(HtmlFixture(rows=rows))
    assert meta["snapshot_complete"] is True
    assert meta["current_source_count"] == 2
    assert meta["detail_pages"] == 2
    assert meta["excluded_cancelled_current_count"] == 1
    assert [row["raw_fields"]["identity"] for row in result] == ["1"]


def test_exact_official_test_course_is_excluded_without_weakening_missing_fields() -> None:
    pseudo = Course(
        "895",
        "★☆★☆★☆TEST★☆★☆★☆",
        start="2027-01-01",
        end="2027-12-31",
        apply_start="2026-07-22",
        apply_end="2026-08-31",
        place="내포신도시 평생학습센터",
        organizer="내포신도시 평생학습센터",
        recruitment_status="모집중",
        education_status="교육예정",
        schedule="",
        venue="삽교읍",
        capacity_current=2,
        capacity_total=100,
    )
    result, _parser, meta = _collect(
        HtmlFixture(rows=[pseudo], omit_schedule_ids=frozenset({"895"}))
    )

    assert result == []
    assert meta["snapshot_complete"] is True
    assert meta["current_source_count"] == 1
    assert meta["audited_current_count"] == 0
    assert meta["excluded_pseudo_current_count"] == 1
    assert meta["excluded_pseudo_current_ids"] == ["895"]
    assert meta["detail_attempts"] == meta["detail_pages"] == 0
    assert meta["no_current_data"] is True

    near_match = replace(pseudo, identity="896")
    rows, _parser, near_meta = _collect(
        HtmlFixture(rows=[near_match], omit_schedule_ids=frozenset({"896"}))
    )
    assert rows == []
    assert near_meta["snapshot_complete"] is False
    assert "current course field is empty" in near_meta["configured_collection_error"]


def test_no_current_data_is_valid_only_after_complete_empty_detail_set() -> None:
    expired = [Course(str(identity), f"종료 강좌 {identity}") for identity in range(20, 0, -1)]
    rows, _parser, meta = _collect(HtmlFixture(rows=expired))
    assert rows == []
    assert meta["source_total"] == 20
    assert meta["current_source_count"] == 0
    assert meta["detail_attempts"] == meta["detail_pages"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"]


def test_caps_and_dedupe_cardinality_fail_closed() -> None:
    fixture = HtmlFixture()
    rows, _parser, meta = yesan.collect_yesan_education(
        Target(yesan.YESAN_PROVIDER, yesan.YESAN_CANONICAL_URL),
        today="2026-07-21",
        max_pages=3,
        session_factory=DummySession,
        fetcher=fixture,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]

    rows, _parser, meta = yesan.collect_yesan_education(
        Target(yesan.YESAN_PROVIDER, yesan.YESAN_CANONICAL_URL),
        today="2026-07-21",
        max_pages=20,
        detail_limit=1,
        session_factory=DummySession,
        fetcher=HtmlFixture(),
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        HtmlFixture(), dedupe_rows=lambda values: values[:1]
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]


def test_wrong_or_alias_target_never_fetches() -> None:
    fixture = HtmlFixture()
    rows, parser, meta = yesan.collect_yesan_education(
        Target("MUNI_WWW_YESAN_GO_KR_DB7F84C1", yesan.YESAN_COMPUTER_SUBSET_URL),
        session_factory=DummySession,
        fetcher=fixture,
    )
    assert rows == []
    assert parser == yesan.YESAN_PARSER
    assert fixture.calls == Counter()
    assert "does not match canonical Yesan owner" in meta["configured_collection_error"]
