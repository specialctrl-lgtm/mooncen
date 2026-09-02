from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import math
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_jindo as jindo


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    venue: str
    source_status: str = "신청마감"
    display_title: str = ""
    start: str = "2026-08-01"
    end: str = "2026-10-31"
    apply_start: str = "2026-05-01"
    apply_end: str = "2026-05-31"
    capacity: int = 20
    institution: str = "진도군청"
    schedule: str = "매주 화요일 10:00 ~ 12:00 / 2시간"


class DummySession:
    def close(self) -> None:
        return None


def _historical(identity: int) -> Course:
    return Course(
        identity=str(identity),
        title=f"과거 강좌 {identity}",
        venue=f"과거 교육장 {identity}",
        start="2026-05-01",
        end="2026-06-30",
        apply_start="2026-04-01",
        apply_end="2026-04-30",
    )


def _courses() -> list[Course]:
    return [
        Course(
            "112",
            "현재 온라인 강좌",
            "진도군 여성플라자 2층 어울마당",
            source_status="신청중",
            start="2026-07-01",
            end="2026-10-31",
            apply_start="2026-07-01",
            apply_end="2026-07-31",
        ),
        Course(
            "111",
            "현재 마감 강좌",
            "진도군 청년센터",
            start="2026-07-10",
            end="2026-08-31",
        ),
        Course(
            "110",
            "접수 예정 강좌",
            "진도군 유림회관",
            source_status="신청예정",
            start="2026-08-20",
            end="2026-11-30",
            apply_start="2026-08-01",
            apply_end="2026-08-15",
        ),
        Course(
            "109",
            "표시명과 과정명이 다른 강좌",
            "진도군 옥주골 문화 복지센터",
            display_title="카드 홍보용 표시명",
            start="2026-09-01",
            end="2026-12-31",
        ),
        *[_historical(identity) for identity in range(108, 100, -1)],
    ]


def _relative(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path + ("?" + parsed.query if parsed.query else "")


def _search_form() -> str:
    return """
      <div class="proSearch">
        <form action="?act=list&amp;m=9" method="post"><fieldset>
          <legend>교육 검색</legend><div class="searchInput"><ul>
            <li><select name="searchYear" id="searchYear">
              <option value="0">전체</option><option value="2026">2026</option>
              <option value="2025">2025</option>
            </select></li>
            <li><select name="searchCondition" id="searchType">
              <option value="">전체보기</option><option value="titleSub">제목</option>
              <option value="title">과정명</option>
            </select></li>
            <li><input id="keyword" name="searchKeyword" value=""></li>
          </ul><button type="submit">검색</button>
          <a href="/edu/edu/E0004.cs?m=10&amp;act=confirmList">나의 신청목록</a>
          </div>
        </fieldset></form>
      </div>
    """


def _card(course: Course) -> str:
    detail = escape(_relative(jindo.jindo_detail_url(course.identity)), quote=True)
    display = course.display_title or course.title
    return f"""
      <li><a href="{detail}"><div class="thumb"><img src="/safe.jpg" alt=""></div>
        <div class="data"><span class="tit">{escape(display)}</span><ul class="info">
          <li><em>과정명</em>{escape(course.title)}</li>
          <li><em>교육기간</em>{course.start} ~ {course.end}</li>
          <li><em>교육장소</em>{escape(course.venue)}</li>
          <li><em>모집인원</em>{course.capacity}</li>
          <li class="progress st02"><span class="status">{course.source_status}</span></li>
        </ul></div>
      </a></li>
    """


def _pagination(page: int, pages: int, *, sentinel: bool) -> str:
    numbers: list[str] = []
    for number in range(1, pages + 1):
        if number == page and not sentinel:
            numbers.append(f"<li><strong>{number}</strong></li>")
        else:
            numbers.append(f'<li><a href="?m=9&amp;pageIndex={number}">{number}</a></li>')
    previous = min(max(page - 1, 1), pages)
    following = min(max(page + 1, 1), pages)
    return f"""
      <div class="paginate">
        <div class="page_prev page_ctrl"><a href="?m=9&amp;pageIndex=1">first</a>
          <a href="?m=9&amp;pageIndex={previous}">prev</a></div>
        <div class="pages"><ul>{''.join(numbers)}</ul></div>
        <div class="current_pages"><em>{page}</em> / <span>{pages}</span></div>
        <div class="page_next page_ctrl"><a href="?m=9&amp;pageIndex={following}">next</a>
          <a href="?m=9&amp;pageIndex={pages}">last</a></div>
      </div>
    """


def _list_html(
    page: int,
    courses: list[Course],
    *,
    sentinel: bool = False,
) -> str:
    pages = math.ceil(len(courses) / jindo.JINDO_PAGE_SIZE)
    selected = (
        []
        if sentinel
        else courses[(page - 1) * jindo.JINDO_PAGE_SIZE : page * jindo.JINDO_PAGE_SIZE]
    )
    cards = "".join(_card(course) for course in selected)
    empty = '<div class="no_data">해당 데이터가 없습니다.</div>' if not selected else ""
    return f"""
      <html><head><title>교육일정 안내 : 진도 평생학습관</title></head><body>
        {_search_form()}
        <div class="proWrap"><ul class="proList">{cards}</ul></div>{empty}
        {_pagination(page, pages, sentinel=sentinel)}
      </body></html>
    """


def _detail_html(course: Course, *, script_identity: str | None = None) -> str:
    if course.source_status == "신청중":
        button_text, button_href = "신청하기", "javascript:fn_eduReser();"
    elif course.source_status == "신청예정":
        button_text, button_href = "신청예정", "javascript:;"
    else:
        button_text, button_href = "신청마감", "javascript:;"
    bound = script_identity or course.identity
    return f"""
      <html><head><title>교육일정 안내 : 진도 평생학습관</title></head><body>
        <div class="proInfo"><h4>{escape(course.title)}</h4><ul>
          <li><em>과정명</em><span>{escape(course.title)}</span></li>
          <li><em>교육기관</em><span>{escape(course.institution)}</span></li>
          <li><em>강사명</em><span>저장 금지 강사</span></li>
          <li><em>교육장소</em><span>{escape(course.venue)}</span></li>
          <li><em>모집인원</em><span>{course.capacity}</span></li>
          <li><em>신청기간</em><span>{course.apply_start} ~ {course.apply_end}</span></li>
          <li><em>교육기간</em><span>{course.start} ~ {course.end}</span></li>
          <li><em>운영시간</em><span>{escape(course.schedule)}</span></li>
          <li><em>문의처</em><span>061-540-9999</span></li>
          <li><em>관련홈페이지</em><span>private@example.test</span></li>
          <li><em>첨부파일</em><span>개인정보.hwp</span></li>
        </ul><p class="lkBtn"><a href="{button_href}">{button_text}</a></p></div>
        <div class="proDetail"><div class="proContent">수집하면 안 되는 자유 본문</div></div>
        <p class="btnCenter"><a href="?searchKeyword=&amp;searchCondition=&amp;m=9" class="button_list">목록보기</a></p>
        <script>
          function fn_eduReser() {{
            var thisForm = $('#eduReserForm');
            thisForm.find('[name=infoId]').val('{bound}');
            thisForm.find('[name=returnQueryString]').val('act=view&infoId={bound}&m=9');
          }}
        </script>
        <form id="eduReserForm"><input name="memName" value="민감한 신청자"></form>
      </body></html>
    """


class Site:
    def __init__(
        self,
        courses: list[Course] | None = None,
        *,
        mutate_first_recheck: bool = False,
        sentinel_has_rows: bool = False,
        mismatched_script_identity: str = "",
    ) -> None:
        self.courses = list(courses or _courses())
        self.mutate_first_recheck = mutate_first_recheck
        self.sentinel_has_rows = sentinel_has_rows
        self.mismatched_script_identity = mismatched_script_identity
        self.calls: list[str] = []
        self.list_page_calls: dict[int, int] = {}
        self.detail_ids: list[str] = []
        self.lock = Lock()

    def fetch(self, _session: object, url: str, _timeout: int) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self.lock:
            self.calls.append(url)
        if query.get("act") == ["view"]:
            identity = query["infoId"][0]
            with self.lock:
                self.detail_ids.append(identity)
            course = next(course for course in self.courses if course.identity == identity)
            mismatch = self.mismatched_script_identity if identity == "112" else ""
            return _detail_html(course, script_identity=mismatch or None)
        page = int(query.get("pageIndex", ["1"])[0])
        with self.lock:
            self.list_page_calls[page] = self.list_page_calls.get(page, 0) + 1
            occurrence = self.list_page_calls[page]
        pages = math.ceil(len(self.courses) / jindo.JINDO_PAGE_SIZE)
        if page == pages + 1:
            if self.sentinel_has_rows:
                return _list_html(page, self.courses[:1], sentinel=False)
            return _list_html(page, self.courses, sentinel=True)
        selected = self.courses
        if self.mutate_first_recheck and page == 1 and occurrence >= 2:
            selected = [replace(self.courses[0], title="재조회 중 변경된 제목"), *self.courses[1:]]
        return _list_html(page, selected)


def _collect(site: Site, **kwargs: object):
    return jindo.collect_jindo_education(
        Target(jindo.JINDO_PROVIDER, jindo.JINDO_URL),
        today="2026-07-21",
        session_factory=DummySession,
        fetcher=site.fetch,
        **kwargs,
    )


def test_exact_owner_and_candidate_boundaries() -> None:
    assert jindo.is_jindo_education_target(Target(jindo.JINDO_PROVIDER, jindo.JINDO_URL))
    assert not jindo.is_jindo_education_target(Target(jindo.JINDO_DUPLICATE_PROVIDER, jindo.JINDO_URL))
    assert not jindo.is_jindo_education_target(Target(jindo.JINDO_PROVIDER, "https://www.jindo.go.kr/"))
    assert jindo.is_jindo_candidate_alias(
        Target(jindo.JINDO_PROVIDER, jindo.JINDO_ROOT_URL, jindo.JINDO_ROOT_CANDIDATE_ID)
    )
    assert jindo.is_jindo_candidate_alias(
        Target(jindo.JINDO_DUPLICATE_PROVIDER, jindo.JINDO_INTRO_URL, jindo.JINDO_INTRO_CANDIDATE_ID)
    )
    assert jindo.jindo_list_url(2).endswith("?m=9&pageIndex=2")
    assert "infoId=112" in jindo.jindo_detail_url("112")
    assert jindo.jindo_detail_url("../private") == ""


def test_complete_snapshot_returns_only_current_details_and_exact_branches() -> None:
    site = Site()
    rows, parser, meta = _collect(site)

    assert parser == jindo.JINDO_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == ["112", "111", "110", "109"]
    assert sorted(site.detail_ids) == ["109", "110", "111", "112"]
    assert not set(site.detail_ids) & {str(value) for value in range(101, 109)}
    assert rows[0]["application_url"] == rows[0]["raw_url"]
    assert rows[0]["reservation_available"] is True
    assert all(not row.get("application_url") for row in rows[1:])
    assert rows[3]["title"] == "표시명과 과정명이 다른 강좌"
    assert rows[3]["raw_fields"]["source_display_title"] == "카드 홍보용 표시명"
    assert [row["branch"] for row in rows] == [
        "진도군 여성플라자 2층 어울마당",
        "진도군 청년센터",
        "진도군 유림회관",
        "진도군 옥주골 문화 복지센터",
    ]
    assert meta["list_requests"] == 5
    assert meta["required_list_requests"] == 5
    assert meta["page_counts"] == {1: 10, 2: 2}
    assert meta["source_rows"] == 12
    assert meta["expired_count"] == 8
    assert meta["current_source_count"] == 4
    assert meta["detail_pages"] == 4
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is False
    assert not meta["configured_collection_error"]
    payload = repr(rows)
    for forbidden in (
        "저장 금지 강사",
        "061-540-9999",
        "private@example.test",
        "개인정보.hwp",
        "수집하면 안 되는 자유 본문",
        "민감한 신청자",
    ):
        assert forbidden not in payload


def test_complete_historical_snapshot_is_valid_no_current_data_without_details() -> None:
    courses = [_historical(identity) for identity in range(212, 200, -1)]
    site = Site(courses)
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert site.detail_ids == []
    assert meta["source_rows"] == 12
    assert meta["list_requests"] == 5
    assert meta["detail_attempts"] == 0
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["current_branch_names"] == []


@pytest.mark.parametrize(
    ("site", "error_fragment"),
    [
        (Site(mutate_first_recheck=True), "first-page stability recheck changed"),
        (Site(sentinel_has_rows=True), "sentinel"),
        (
            Site([*_courses()[:1], replace(_courses()[1], identity="112"), *_courses()[2:]]),
            "duplicate official identities",
        ),
    ],
)
def test_list_contract_changes_fail_closed(site: Site, error_fragment: str) -> None:
    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


def test_expired_active_status_fails_closed() -> None:
    courses = _courses()
    courses[-1] = replace(courses[-1], source_status="신청중", apply_start="2026-07-01", apply_end="2026-07-31")
    rows, _parser, meta = _collect(Site(courses))
    assert rows == []
    assert "expired rows expose active application status" in meta["configured_collection_error"]


def test_identity_mismatched_application_script_fails_closed() -> None:
    rows, _parser, meta = _collect(Site(mismatched_script_identity="999"))
    assert rows == []
    assert meta["detail_errors"] == 1
    assert "JavaScript application identity binding changed" in meta["configured_collection_error"]


def test_limits_and_dedupe_cardinality_fail_closed() -> None:
    rows, _parser, capped = _collect(Site(), max_pages=4)
    assert rows == []
    assert capped["source_cap_reached"] is True
    assert "4 of 5" in capped["configured_collection_error"]

    rows, _parser, limited = _collect(Site(), detail_limit=3)
    assert rows == []
    assert limited["source_cap_reached"] is True
    assert "3 of 4" in limited["configured_collection_error"]

    rows, _parser, deduped = _collect(Site(), dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert deduped["snapshot_complete"] is False
    assert "dedupe changed official identity cardinality" in deduped["configured_collection_error"]


def test_invalid_target_never_fetches() -> None:
    site = Site()
    rows, parser, meta = jindo.collect_jindo_education(
        Target(jindo.JINDO_DUPLICATE_PROVIDER, jindo.JINDO_URL),
        session_factory=DummySession,
        fetcher=site.fetch,
    )
    assert rows == []
    assert parser == jindo.JINDO_PARSER
    assert site.calls == []
    assert "canonical Jindo" in meta["configured_collection_error"]


@pytest.mark.skipif(os.getenv("JINDO_LIVE_TEST") != "1", reason="set JINDO_LIVE_TEST=1")
def test_live_official_catalogue_complete_no_current_snapshot() -> None:
    rows, parser, meta = jindo.collect_jindo_education(
        Target(jindo.JINDO_PROVIDER, jindo.JINDO_URL),
        today="2026-07-21",
    )
    assert parser == jindo.JINDO_PARSER
    assert rows == []
    assert meta["source_rows"] == 11
    assert meta["declared_data_pages"] == 2
    assert meta["data_pages"] == 2
    assert meta["page_counts"] == {1: 10, 2: 1}
    assert meta["list_requests"] == 5
    assert meta["required_list_requests"] == 5
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 2
    assert meta["current_source_count"] == 0
    assert meta["expired_count"] == 11
    assert meta["detail_attempts"] == 0
    assert meta["detail_pages"] == 0
    assert meta["source_status_counts"] == {"신청마감": 11}
    assert meta["current_branch_names"] == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert not meta["configured_collection_error"]
