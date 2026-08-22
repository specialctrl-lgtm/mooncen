from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
import hashlib
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_goheung as goheung


@dataclass(frozen=True)
class Target:
    provider: str
    url: str


class DummySession:
    def close(self) -> None:
        pass


def _county_target(**updates: str) -> Target:
    values = {
        "provider": goheung.GOHEUNG_COUNTY_PROVIDER,
        "url": goheung.GOHEUNG_COUNTY_URL,
    }
    values.update(updates)
    return Target(**values)


def _lifelong_target(**updates: str) -> Target:
    values = {
        "provider": goheung.GOHEUNG_LIFELONG_PROVIDER,
        "url": goheung.GOHEUNG_LIFELONG_URL,
    }
    values.update(updates)
    return Target(**values)


def _library_target(**updates: str) -> Target:
    values = {
        "provider": goheung.GOHEUNG_LIBRARY_PROVIDER,
        "url": goheung.GOHEUNG_LIBRARY_URL,
    }
    values.update(updates)
    return Target(**values)


def _strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [text for nested in value.values() for text in _strings(nested)]
    if isinstance(value, (list, tuple, set)):
        return [text for nested in value for text in _strings(nested)]
    return [value] if isinstance(value, str) else []


@dataclass(frozen=True)
class CountyCourse:
    identity: str
    title: str
    start: str
    end: str
    apply_start: str
    apply_end: str
    status: str
    venue: str
    capacity: int = 20


def _county_courses(source: goheung.GoheungCountySource) -> list[CountyCourse]:
    if source.code == "lifelong":
        return [
            CountyCourse(
                "901",
                "현재 문화 강좌",
                "2099-08-01",
                "2099-09-01",
                "2099-07-20",
                "2099-07-23",
                "신청중",
                "고흥문화회관 교육실",
            ),
            CountyCourse(
                "900",
                "지난 문화 강좌",
                "2020-01-01",
                "2020-02-01",
                "2019-12-01",
                "2019-12-10",
                "교육종료",
                "고흥문화회관 교육실",
            ),
        ]
    return [
        CountyCourse(
            "800",
            "지난 첨단 강좌",
            "2020-03-01",
            "2020-04-01",
            "2020-02-01",
            "2020-02-10",
            "교육종료",
            "첨단교육센터",
        )
    ]


def _county_list_html(
    source: goheung.GoheungCountySource,
    page: int,
    *,
    drift: bool = False,
    nonempty_sentinel: bool = False,
) -> str:
    courses = _county_courses(source) if page == 1 or nonempty_sentinel else []
    items = []
    for course in courses:
        title = course.title + (" 변경" if drift and course.identity == "901" else "")
        control = (
            f'<a id="appln_act_{course.identity}" href="javascript:void(0)" '
            f'onclick="fn_appln({course.identity})" data-sn="{course.identity}" '
            f'data-action="I" data-ctgry="{source.category}">신청하기</a>'
            if course.status == "신청중"
            else ""
        )
        items.append(
            f"""
            <li><div class="list_label">{course.status}</div>
              <h5><a href="/education/pg/hmCourseMasterView.do?sn={course.identity}&amp;pageId={source.page_id}&amp;ctgry={source.category}">{escape(title)}</a></h5>
              <dl><dt>교육기간</dt><dd>{course.start} ~ {course.end}</dd></dl>
              <dl><dt>교육시간</dt><dd>10:00~12:00</dd></dl>
              <dl><dt>접수기간</dt><dd>{course.apply_start.replace('-', '.')} ~ {course.apply_end.replace('-', '.')}</dd></dl>
              <dl><dt>모집인원</dt><dd>{course.capacity}명</dd></dl>
              <dl><dt>교육장소</dt><dd>{escape(course.venue)}</dd></dl>
              <dl><dt>수강료</dt><dd>무료</dd></dl>{control}
            </li>
            """
        )
    return f"""
      <html><head><title> 수강신청 | 교육/일자리/청년</title></head><body>
        <form id="searchForm" method="post" action="/education/pg/hmCourseMasterList.do">
          <input name="pageId" value="{source.page_id}"><input name="boardId" value="BD_00018">
          <input name="movePage" value="{page}"><input name="ctgry" value="{source.category}">
        </form>
        <ul class="board_list board_type_d">{''.join(items)}</ul>
      </body></html>
    """


def _county_detail_html(
    source: goheung.GoheungCountySource,
    course: CountyCourse,
    *,
    status_drift: bool = False,
    detail_status: str | None = None,
) -> str:
    status = detail_status or ("신청마감" if status_drift else course.status)
    control = (
        f'<button id="appln_act_{course.identity}" onclick="fn_appln({course.identity})" '
        f'data-sn="{course.identity}" data-action="I" data-ctgry="{source.category}">신청하기</button>'
        if status == "신청중"
        else ""
    )
    return f"""
      <html><head><title>수강신청 | 교육/일자리/청년</title></head><body>
        <form id="syForm" method="post"><input type="hidden" name="progId" value="eduProgram">
          <input type="hidden" name="pageAction" value="I"><input type="hidden" name="sn" value="{course.identity}">
          <div class="bd_view_top"><h4>{escape(course.title)}</h4></div><div class="bd_view_cont">
            <dl><dt>기관</dt><dd>{goheung.GOHEUNG_COUNTY_BRANCH}</dd></dl>
            <dl><dt>강좌분류</dt><dd>문화예술교육</dd></dl>
            <dl><dt>교육기간</dt><dd>{course.start} ~ {course.end}</dd></dl>
            <dl><dt>접수기간</dt><dd>{course.apply_start} 09:00 ~ {course.apply_end} 18:00</dd></dl>
            <dl><dt>교육주기</dt><dd>총 8회</dd></dl><dl><dt>교육정원</dt><dd>{course.capacity}명</dd></dl>
            <dl><dt>대기자정원</dt><dd>5명</dd></dl><dl><dt>교육시간</dt><dd>10:00~12:00</dd></dl>
            <dl><dt>강사명</dt><dd>민감강사</dd></dl><dl><dt>수강료</dt><dd>무료</dd></dl>
            <dl><dt>교육장소</dt><dd>{escape(course.venue)}</dd></dl><dl><dt>접수방법</dt><dd>온라인접수</dd></dl>
            <dl><dt>교육문의전화</dt><dd>061-123-4567</dd></dl><dl><dt>교육대상</dt><dd>고흥군민</dd></dl>
            <dl><dt>상세소개</dt><dd>child@example.org 주민번호와 신청서를 제출하세요</dd></dl>
            <dl><dt>접수상태</dt><dd>{status}</dd></dl><dl><dt>첨부파일</dt><dd>private.hwp</dd></dl>
          </div>
        </form>{control}
        <script>function fn_appln(n){{ form.action='/education/pg/HmCourseAppln.do?pageId={source.page_id}&ctgry={source.category}'; }}</script>
      </body></html>
    """


class CountySite:
    def __init__(self, *, recheck_drift: bool = False, status_drift: bool = False, bad_sentinel: bool = False) -> None:
        self.recheck_drift = recheck_drift
        self.status_drift = status_drift
        self.bad_sentinel = bad_sentinel
        self.page_one_calls: dict[str, int] = {}

    def __call__(self, _session: object, url: str, _timeout: int) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        source = next(row for row in goheung.GOHEUNG_COUNTY_SOURCES if row.category == query["ctgry"][0])
        if parsed.path.endswith("hmCourseMasterView.do"):
            course = next(row for row in _county_courses(source) if row.identity == query["sn"][0])
            return _county_detail_html(source, course, status_drift=self.status_drift)
        page = int(query["movePage"][0])
        if page == 1:
            self.page_one_calls[source.code] = self.page_one_calls.get(source.code, 0) + 1
        return _county_list_html(
            source,
            page,
            drift=self.recheck_drift and page == 1 and self.page_one_calls[source.code] > 1,
            nonempty_sentinel=self.bad_sentinel and source.code == "advanced" and page == 2,
        )


def test_exact_targets_provider_hash_and_owner_boundaries() -> None:
    assert goheung.GOHEUNG_LIBRARY_PROVIDER == "MUNI_WWW_GHLIB_GO_KR_" + hashlib.sha1(
        goheung.GOHEUNG_LIBRARY_URL.encode()
    ).hexdigest()[:8].upper()
    assert goheung.is_goheung_target(_county_target())
    assert goheung.is_goheung_target(_lifelong_target())
    assert goheung.is_goheung_target(_library_target())
    assert not goheung.is_goheung_target(_county_target(provider=goheung.GOHEUNG_COUNTY_ALIAS_PROVIDER))
    assert not goheung.is_goheung_target(_county_target(url=goheung.GOHEUNG_COUNTY_HTTP_ALIAS_URL))
    assert goheung.GOHEUNG_CANDIDATE_AUDIT["MUNI_IR_B8037A4195A6"]["owner"] == goheung.GOHEUNG_COUNTY_PROVIDER
    assert goheung.GOHEUNG_CANDIDATE_AUDIT["MUNI_IR_E0D61DBD8A04"]["decision"] == "exclude_accommodation_owner"
    assert set(goheung.GOHEUNG_OWNER_BOUNDARY_AUDIT) == {
        goheung.GOHEUNG_COUNTY_PROVIDER,
        goheung.GOHEUNG_LIFELONG_PROVIDER,
        goheung.GOHEUNG_LIBRARY_PROVIDER,
    }


def test_county_two_categories_complete_detail_and_privacy_allowlist() -> None:
    rows, parser, meta = goheung.collect_goheung_county_courses(
        _county_target(),
        fetcher=CountySite(),
        session_factory=DummySession,
        today="2099-07-21",
        max_pages=6,
        detail_limit=5,
    )
    assert parser == goheung.GOHEUNG_COUNTY_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"].endswith(":lifelong:901")
    assert row["branch"] == goheung.GOHEUNG_COUNTY_BRANCH
    assert row["status"] == "OPEN" and row["reservation_available"]
    assert row["application_url"] == goheung.goheung_county_detail_url("lifelong", "901")
    persisted = " ".join(_strings(row))
    for forbidden in ("민감강사", "061-123-4567", "child@example.org", "주민번호", "private.hwp"):
        assert forbidden not in persisted
    assert meta["source_totals"] == {"lifelong": 2, "advanced": 1}
    assert meta["required_list_requests"] == 6
    assert meta["list_requests"] == 6
    assert meta["sentinel_pages"] == 2 and meta["list_rechecks"] == 2
    assert meta["detail_pages"] == 1
    assert meta["full_snapshot_validated"]


def test_county_caps_sentinel_and_detail_drift_fail_closed() -> None:
    for site, pages in (
        (CountySite(recheck_drift=True), 6),
        (CountySite(status_drift=True), 6),
        (CountySite(bad_sentinel=True), 6),
        (CountySite(), 5),
    ):
        rows, _, meta = goheung.collect_goheung_county_courses(
            _county_target(), fetcher=site, session_factory=DummySession,
            today="2099-07-21", max_pages=pages, detail_limit=5,
        )
        assert rows == []
        assert meta["configured_collection_error"]
        assert not meta["full_snapshot_validated"]


def test_county_closed_list_may_transition_to_ended_detail_on_end_date() -> None:
    source = goheung.GOHEUNG_COUNTY_SOURCES[0]
    course = CountyCourse(
        "297",
        "라탄화분만들기",
        "2026-07-28",
        "2026-07-28",
        "2026-07-17",
        "2026-07-22",
        "접수마감",
        "고흥군청소년문화의집 4층 다목적실",
        10,
    )
    parents, empty = goheung._county_page(
        source,
        goheung._coerce_soup(_county_list_html(source, 1).replace(
            _county_courses(source)[0].title,
            course.title,
        )),
        1,
    )
    parent = {
        **parents[0],
        "identity": course.identity,
        "title": course.title,
        "start_date": course.start,
        "end_date": course.end,
        "apply_start": course.apply_start,
        "apply_end": course.apply_end,
        "source_status": course.status,
        "venue": course.venue,
        "capacity_total": course.capacity,
        "raw_url": goheung.goheung_county_detail_url(source.code, course.identity),
    }
    assert empty is False

    row = goheung._county_detail(
        source,
        parent,
        goheung._coerce_soup(
            _county_detail_html(source, course, detail_status="교육종료")
        ),
        _county_target(),
        date(2026, 7, 28),
    )

    assert row["status"] == "CLOSED"
    assert row["reservation_available"] is False
    assert row["raw_fields"]["list_status"] == "접수마감"
    assert row["raw_fields"]["detail_status"] == "교육종료"
    assert row["raw_fields"]["list_detail_status_transition"] is True

    future_parent = {**parent, "end_date": "2026-07-29"}
    with pytest.raises(goheung.GoheungContractError, match="status mismatch"):
        goheung._county_detail(
            source,
            future_parent,
            goheung._coerce_soup(
                _county_detail_html(
                    source,
                    CountyCourse(
                        **{
                            **course.__dict__,
                            "end": "2026-07-29",
                        }
                    ),
                    detail_status="교육종료",
                )
            ),
            _county_target(),
            date(2026, 7, 28),
        )


def _jne_action(status: str, *, detail_wait_class: bool = False) -> str:
    css = goheung._JNE_STATUS_CLASS[status]
    if detail_wait_class and status == "대기자신청하기":
        css = "w_app"
    span = f'<span class="{css}">{status}</span>'
    return f'<a href="#" onclick="checkLogin(); return false;">{span}</a>' if status in {"신청하기", "대기자신청하기"} else span


@dataclass(frozen=True)
class JneCourse:
    identity: str
    title: str
    start: str
    end: str
    apply_start: str
    apply_end: str
    status: str
    target: str = "고흥군민"
    current: int = 2
    total: int = 10
    wait_current: int = 1
    wait_total: int = 3


def _lecture_course(source: goheung.GoheungLectureSource) -> JneCourse:
    if source.code == "resident":
        return JneCourse("1101", "주민 미래 강좌", "2099-08-01", "2099-10-01", "2099-07-28", "2099-07-30", "접수전")
    if source.code == "vacation":
        return JneCourse("1104", "방학 현재 강좌", "2099-08-02", "2099-08-20", "2099-07-01", "2099-07-30", "신청하기")
    return JneCourse("1102" if source.code == "student" else "1103", f"{source.menu} 지난 강좌", "2020-01-01", "2020-02-01", "2019-12-01", "2019-12-10", "마감")


def _lecture_list_html(source: goheung.GoheungLectureSource, page: int, *, drift: bool = False) -> str:
    if page == 1:
        course = _lecture_course(source)
        title = course.title + (" 변경" if drift else "")
        row = f"""<tr><td>1</td><td class="t_title"><a href="/lecture.es?mid={source.mid}&amp;act=view&amp;el_seq={course.identity}&amp;nPage=">{escape(title)}</a></td>
          <td>{escape(course.target)}</td><td>{course.start} ~<br>{course.end}<br>월 10:00 ~ 12:00</td>
          <td>{course.apply_start} 10:00 ~<br>{course.apply_end} 18:00</td>
          <td><span class="edu-state01">{course.current}</span> / <span class="edu-state02">{course.total}</span><br>(<span class="edu-state01">{course.wait_current}</span> / <span class="edu-state02">{course.wait_total}</span>)</td>
          <td>{_jne_action(course.status)}</td></tr>"""
    else:
        row = '<tr><td>등록된 자료가 존재하지 않습니다.</td></tr>'
    return f"""<html><head><title>글쓰기 | {source.menu} | 수강 신청 | 평생학습 : {goheung.GOHEUNG_LIFELONG_BRANCH}</title></head><body>
      <a href="/login_search.es?sid=c4">로그인</a><form name="srhForm" method="post" action="/lecture.es?mid={source.mid}">
      <input name="actionUrl" value="/lecture.es"><input name="nPage" value="{'' if page == 1 else page}"><input name="mid" value="{source.mid}"><input name="act" value="list"><input name="b_list" value="100"></form>
      <table class="tstyle_list"><thead><tr>{''.join(f'<th>{x}</th>' for x in goheung._LECTURE_HEADERS)}</tr></thead><tbody>{row}</tbody></table></body></html>"""


def _lecture_detail_html(
    source: goheung.GoheungLectureSource,
    course: JneCourse,
    *,
    current_delta: int = 0,
) -> str:
    return f"""<html><head><title>글쓰기 | {source.menu} | 수강 신청 | 평생학습 : {goheung.GOHEUNG_LIFELONG_BRANCH}</title></head><body>
      <a href="/login_search.es?sid=c4">로그인</a><script>function checkLogin(){{location.href='/login_search.es?sid=c4';return false;}}</script>
      <form name="insForm" method="post" action="/lecture.es&act=ins"><input type="hidden" name="actionUrl" value="/lecture.es"><input type="hidden" name="nPage" value=""><input type="hidden" name="act" value="list">
      <table class="tstyle_write"><tbody>
      <tr><th>강좌명</th><td>{escape(course.title)}</td></tr><tr><th>대상</th><td>{escape(course.target)}</td></tr>
      <tr><th>신청기간</th><td>{course.apply_start} 10시 00분 ~ {course.apply_end} 18시 00분</td></tr>
      <tr><th>운영기간</th><td>{course.start}~{course.end}</td></tr><tr><th>강의 시간</th><td>10:00 ~ 12:00</td></tr>
      <tr><th>회차</th><td>8</td><th>강의 요일</th><td>월</td></tr><tr><th>교육장소</th><td>306호</td></tr>
      <tr><th>모집인원</th><td>{course.total}명 (대기 {course.wait_total}명)</td><th>신청자</th><td>{course.current + current_delta}명 (대기 {course.wait_current}명)</td></tr>
      <tr><th>신청방법</th><td>인터넷</td><th>접수상태</th><td>{_jne_action(course.status)}</td></tr>
      <tr><th>비고</th><td>민감강사 061-123-4567 child@example.org 학생 개인정보</td></tr>
      </tbody></table></form></body></html>"""


READING = JneCourse("1201", "현재 독서회", "2099-03-10", "2099-12-08", "2099-02-19", "2099-03-04", "마감", current=3, total=10, wait_current=1, wait_total=2)


def _reading_list_html(page: int) -> str:
    if page == 1:
        row = f"""<tr><td>1</td><td><a class="subject" href="/education.es?mid=c40208000000&amp;eid=0128&amp;edu_seq={READING.identity}&amp;educ_cg=&amp;act=view">{READING.title}</a></td>
          <td>{READING.apply_start} ~ {READING.apply_end}</td><td>{READING.start} ~ {READING.end}</td>
          <td><span class="edu-state01">{READING.current}</span><span class="edu-state02">{READING.total}</span><span class="edu-state01">{READING.wait_current}</span><span class="edu-state02">{READING.wait_total}</span></td><td>{_jne_action(READING.status)}</td></tr>"""
    else:
        row = "<tr><td>결과 없음</td></tr>"
    return f"""<html><head><title>독서프로그램 신청 | 독서문화진흥 : {goheung.GOHEUNG_LIFELONG_BRANCH}</title></head><body><a href="/login_search.es?sid=c4">로그인</a>
      <form name="srhForm" method="post" action="/education.es?mid=c40208000000&eid=0128"><input name="mid" value="c40208000000"><input name="eid" value="0128"><input name="nPage" value="{page}"><input name="act" value="list"></form>
      <table class="tstyle_list"><thead><tr>{''.join(f'<th>{x}</th>' for x in goheung._READING_HEADERS)}</tr></thead><tbody>{row}</tbody></table></body></html>"""


def _reading_detail_html() -> str:
    return f"""<html><head><title>{READING.title} | 독서프로그램 신청 | 독서문화진흥 : {goheung.GOHEUNG_LIFELONG_BRANCH}</title></head><body><a href="/login_search.es?sid=c4">로그인</a>
      <form name="vewForm" method="post" action="/education.es?mid=c40208000000"></form><table class="tstyle_view"><tbody>
      <tr><th>강좌명</th><td>{READING.title}</td></tr><tr><th>대상</th><td>{READING.target}</td></tr><tr><th>수강기간</th><td>{READING.start} ~ {READING.end}</td></tr>
      <tr><th>수강시간</th><td>19:00 ~ 21:00</td></tr><tr><th>수강요일</th><td>매월 화요일</td></tr><tr><th>인터넷 접수기간</th><td>{READING.apply_start} ~ {READING.apply_end}</td></tr>
      <tr><th>수강인원</th><td>{READING.total}명 (대기 {READING.wait_total}명)</td></tr><tr><th>신청자</th><td>{READING.current}명 (대기 {READING.wait_current}명)</td></tr>
      <tr><th>교육장소</th><td></td></tr><tr><th>강사명</th><td>민감강사</td></tr><tr><th>내용</th><td>child@example.org 신청자 개인정보</td></tr><tr><th>비고</th><td>{READING.status}</td></tr>
      </tbody></table></body></html>"""


class LifelongSite:
    def __init__(self, *, drift: bool = False, capacity_delta: int = 0) -> None:
        self.drift = drift
        self.capacity_delta = capacity_delta
        self.first_calls: dict[str, int] = {}

    def __call__(self, _session: object, url: str, _timeout: int) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == "/lecture.es":
            source = next(row for row in goheung.GOHEUNG_LECTURE_SOURCES if row.mid == query["mid"][0])
            if query.get("act") == ["view"]:
                return _lecture_detail_html(
                    source,
                    _lecture_course(source),
                    current_delta=(
                        self.capacity_delta if source.code == "resident" else 0
                    ),
                )
            page = int(query.get("nPage", ["1"])[0])
            if page == 1:
                self.first_calls[source.code] = self.first_calls.get(source.code, 0) + 1
            return _lecture_list_html(source, page, drift=self.drift and source.code == "resident" and page == 1 and self.first_calls[source.code] > 1)
        if query.get("act") == ["view"]:
            return _reading_detail_html()
        return _reading_list_html(int(query["nPage"][0]))


def test_lifelong_five_catalogues_complete_details_and_privacy_allowlist() -> None:
    rows, parser, meta = goheung.collect_goheung_lifelong_courses(
        _lifelong_target(), fetcher=LifelongSite(), session_factory=DummySession,
        today="2099-07-21", max_pages=15, detail_limit=10,
    )
    assert parser == goheung.GOHEUNG_LIFELONG_PARSER
    assert len(rows) == 3
    assert {row["status"] for row in rows} == {"SCHEDULED", "OPEN", "CLOSED"}
    assert sum(row["reservation_available"] for row in rows) == 1
    assert {row["raw_fields"]["source_catalogue"] for row in rows} == {"resident", "vacation", "reading"}
    for row in rows:
        assert all(
            row.get(key)
            for key in (
                "target",
                "fee",
                "start_date",
                "end_date",
                "venue_name",
                "category",
                "schedule_raw",
            )
        )
    persisted = " ".join(text for row in rows for text in _strings(row))
    for forbidden in ("민감강사", "061-123-4567", "child@example.org", "학생 개인정보", "신청자 개인정보"):
        assert forbidden not in persisted
    assert meta["source_totals"] == {"resident": 1, "student": 1, "experience": 1, "vacation": 1, "reading": 1}
    assert meta["required_list_requests"] == 15
    assert meta["sentinel_pages"] == 5 and meta["list_rechecks"] == 5
    assert meta["current_count"] == 3 and meta["detail_pages"] == 3
    assert meta["partition_union_complete"] and meta["full_snapshot_validated"]


def test_lifelong_scheduled_status_uses_official_start_minute() -> None:
    rows, _, meta = goheung.collect_goheung_lifelong_courses(
        _lifelong_target(),
        fetcher=LifelongSite(),
        session_factory=DummySession,
        today="2099-07-28T09:59:00+09:00",
        max_pages=15,
        detail_limit=10,
    )
    assert meta["configured_collection_error"] == ""
    resident = next(
        row for row in rows if row["raw_fields"]["source_catalogue"] == "resident"
    )
    assert resident["status"] == "SCHEDULED"

    failed, _, bad = goheung.collect_goheung_lifelong_courses(
        _lifelong_target(),
        fetcher=LifelongSite(),
        session_factory=DummySession,
        today="2099-07-28T10:00:00+09:00",
        max_pages=15,
        detail_limit=10,
    )
    assert failed == []
    assert "scheduled JNE course reached application datetime" in bad[
        "configured_collection_error"
    ]


def test_lifelong_accepts_live_current_capacity_change() -> None:
    rows, _, meta = goheung.collect_goheung_lifelong_courses(
        _lifelong_target(),
        fetcher=LifelongSite(capacity_delta=9),
        session_factory=DummySession,
        today="2099-07-21",
        max_pages=15,
        detail_limit=10,
    )
    assert meta["configured_collection_error"] == ""
    resident = next(
        row for row in rows if row["raw_fields"]["source_catalogue"] == "resident"
    )
    assert resident["capacity_current"] == 11
    assert resident["raw_fields"]["capacity_snapshot_changed"] is True


def test_lifelong_recheck_and_caps_fail_closed() -> None:
    for site, pages in ((LifelongSite(drift=True), 15), (LifelongSite(), 14)):
        rows, _, meta = goheung.collect_goheung_lifelong_courses(
            _lifelong_target(), fetcher=site, session_factory=DummySession,
            today="2099-07-21", max_pages=pages, detail_limit=10,
        )
        assert rows == [] and meta["configured_collection_error"]
        assert not meta["full_snapshot_validated"]


LIB_CURRENT = JneCourse("501", "[북부] 미래 독서문화", "2099-08-01", "2099-08-01", "2099-07-01", "2099-07-30", "모집중", target="초등학생", current=2, total=15, wait_current=1, wait_total=3)
LIB_OLD = JneCourse("500", "[남부] 지난 독서문화", "2020-01-01", "2020-01-01", "2019-12-01", "2019-12-10", "모집마감", target="성인", current=5, total=10, wait_current=0, wait_total=2)
LIB_CLOSED_CURRENT = JneCourse("502", "[북부] 마감 독서문화", "2099-08-02", "2099-08-02", "2099-07-01", "2099-07-30", "모집마감", target="초등학생", current=15, total=15, wait_current=3, wait_total=3)


def _library_list_html(
    page: int,
    *,
    drift: bool = False,
    current: JneCourse = LIB_CURRENT,
) -> str:
    rows = []
    if page == 1:
        for sequence, course in ((2, current), (1, LIB_OLD)):
            title = course.title + (" 변경" if drift and sequence == 2 else "")
            rows.append(f"""<tr><td>{sequence}</td><td class="program"><span class="label">독서문화</span><a class="title" href="/ProgramJoin/All/All/1/read/{course.identity}">{escape(title)}</a><p class="desc">강좌기간 : {course.start} ~ {course.end}</p></td>
              <td>{course.target}<br>{course.total}명 (대기 : {course.wait_total}명)</td><td>{course.current}명 신청 (대기 : {course.wait_current}명)</td><td><span class="label">{course.status}</span></td></tr>""")
    else:
        rows.append('<tr><td>등록된 프로그램이 없습니다.</td></tr>')
    return f"""<html><head><title>프로그램 신청 &lt; 문화마당 -  고흥군립도서관</title></head><body>
      <form method="post" action="/ProgramJoin/All/All"><input name="csrf_token" value="private"><input name="query"></form>
      <table><thead><tr>{''.join(f'<th>{x}</th>' for x in goheung._LIBRARY_HEADERS)}</tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""


def _library_detail_html(current: JneCourse = LIB_CURRENT) -> str:
    c = current
    return f"""<html><head><title>프로그램 신청 &lt; 문화마당 - 고흥군립도서관</title></head><body><article><div class="boardRead"><h1><span class="label">독서문화</span>{c.title}</h1>
      <section class="styleguide"><div><ul><li><strong>강좌대상</strong>: {c.target}</li><li><strong>강좌기간</strong>: {c.start} ~ {c.end}</li><li><strong>강좌시간</strong>: 2시간</li><li><strong>신청기간</strong>: {c.apply_start} 10:00 ~ {c.apply_end} 17:00</li><li><strong>모집인원</strong>: {c.total}명 (대기 : {c.wait_total}명)</li><li><strong>신청대상</strong>: 아동</li><li><strong>장소</strong>: 북부도서관 문화교실</li><li><strong>비용</strong>: 없음</li></ul></div></section>
      <section class="styleguide"><p>child@example.org 상세소개</p></section><section class="styleguide"><h1>강사 소개</h1><p>민감강사 061-123-4567</p></section><section class="styleguide"><h3>신청승인</h3><li>김***** (private_id)</li></section>
      <footer><a href="/ProgramJoin/All/All/1/apply/{c.identity}">신청하기</a></footer></div></article></body></html>"""


class LibrarySite:
    def __init__(
        self,
        *,
        drift: bool = False,
        current: JneCourse = LIB_CURRENT,
    ) -> None:
        self.drift = drift
        self.current = current
        self.first_calls = 0

    def __call__(self, _session: object, url: str, _timeout: int) -> str:
        path = urlparse(url).path
        if "/read/" in path:
            return _library_detail_html(self.current)
        page = int(path.rstrip("/").split("/")[-1])
        if page == 1:
            self.first_calls += 1
        return _library_list_html(
            page,
            drift=self.drift and page == 1 and self.first_calls > 1,
            current=self.current,
        )


def test_library_programjoin_complete_exact_branch_and_privacy_allowlist() -> None:
    rows, parser, meta = goheung.collect_goheung_library_courses(
        _library_target(), fetcher=LibrarySite(), session_factory=DummySession,
        today="2099-07-21", max_pages=3, detail_limit=5,
    )
    assert parser == goheung.GOHEUNG_LIBRARY_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["branch"] == "고흥군립북부도서관"
    assert row["status"] == "OPEN" and row["reservation_available"]
    persisted = " ".join(_strings(row))
    for forbidden in ("민감강사", "061-123-4567", "child@example.org", "private_id", "신청승인"):
        assert forbidden not in persisted
    assert meta["source_total"] == 2 and meta["source_page_counts"] == [2]
    assert meta["required_list_requests"] == 3 and meta["detail_pages"] == 1
    assert meta["full_snapshot_validated"]
    failed, _, bad = goheung.collect_goheung_library_courses(
        _library_target(), fetcher=LibrarySite(drift=True), session_factory=DummySession,
        today="2099-07-21", max_pages=3, detail_limit=5,
    )
    assert failed == [] and bad["configured_collection_error"]


def test_library_closed_identity_bound_apply_link_stays_unavailable() -> None:
    rows, parser, meta = goheung.collect_goheung_library_courses(
        _library_target(),
        fetcher=LibrarySite(current=LIB_CLOSED_CURRENT),
        session_factory=DummySession,
        today="2099-07-31",
        max_pages=3,
        detail_limit=5,
    )

    assert parser == goheung.GOHEUNG_LIBRARY_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["full_snapshot_validated"]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "CLOSED"
    assert row["reservation_available"] is False
    assert row["application_url"] == ""
    assert row["application_type"] == ""
    assert row["raw_fields"]["application_control_verified"] is True


def test_library_closed_apply_link_must_remain_identity_bound() -> None:
    class MismatchedApplySite(LibrarySite):
        def __call__(self, session: object, url: str, timeout: int) -> str:
            html = super().__call__(session, url, timeout)
            if "/read/" in url:
                return html.replace("/apply/502", "/apply/999")
            return html

    rows, _, meta = goheung.collect_goheung_library_courses(
        _library_target(),
        fetcher=MismatchedApplySite(current=LIB_CLOSED_CURRENT),
        session_factory=DummySession,
        today="2099-07-31",
        max_pages=3,
        detail_limit=5,
    )

    assert rows == []
    assert "apply identity changed" in meta["configured_collection_error"]


def test_library_scheduled_status_is_valid_on_its_opening_date() -> None:
    assert goheung._library_status(
        "모집전",
        "2099-07-21",
        "2099-07-30",
        date(2099, 7, 21),
    ) == "SCHEDULED"
    with pytest.raises(goheung.GoheungContractError, match="scheduled status/date"):
        goheung._library_status(
            "모집전",
            "2099-07-21",
            "2099-07-30",
            date(2099, 7, 22),
        )


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_GOHEUNG_EDUCATION") != "1",
    reason="set RUN_LIVE_GOHEUNG_EDUCATION=1 for official live audit",
)
@pytest.mark.parametrize(
    "target",
    (
        _county_target(),
        _lifelong_target(),
        _library_target(),
    ),
)
def test_live_goheung_official_owners(target: Target) -> None:
    rows, _parser, meta = goheung.collect_goheung_education(
        target, today="2026-07-21", max_pages=200, detail_limit=500
    )
    assert meta["configured_collection_error"] == ""
    assert meta["pagination_complete"]
    assert meta["details_complete"]
    assert meta["application_controls_complete"]
    assert meta["full_snapshot_validated"]
    assert len(rows) == meta["returned_count"] == meta["current_count"]
