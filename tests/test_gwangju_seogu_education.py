from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import os
from threading import Lock
import time
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_gwangju_seogu as seogu


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    source_kind: str = "native"
    source_status: str = "접수중"
    institution: str = "정보화교육"
    institution_code: str = "EACD001"
    subbranch: str = ""
    eap_code: str = ""
    start: str = "2025-01-01"
    end: str = "2025-02-01"
    venue: str = "서구 교육장"
    schedule: str = "매주 화요일 10:00 ~ 12:00"
    apply_period: str = "2024-12-01 (09:00) ~ 2024-12-31 (18:00)"
    capacity: int = 30
    detail_category: str = "취업역량"
    oes_id: str = ""
    oec_id: str = ""


class DummySession:
    def close(self) -> None:
        return None


def _courses() -> list[Course]:
    current = [
        Course(
            "101",
            "서구 디지털 기초교육",
            start="2026-07-21",
            end="2026-08-20",
            venue="서구청 정보화교육장",
            apply_period="2026-07-01 (09:00) ~ 2026-08-19 (18:00)",
            capacity=24,
        ),
        Course(
            "102",
            "화정4동 주민 건강교실",
            institution="통합행정복지센터",
            institution_code="EACD004",
            subbranch="화정4동",
            eap_code="C04",
            start="2026-08-01",
            end="2026-09-30",
            venue="화정4동 행정복지센터",
            schedule="매주 목요일 14:00 ~ 16:00",
            apply_period="2026-07-10 (09:00) ~ 2026-07-31 (18:00)",
            capacity=18,
        ),
        Course(
            "365-open",
            "세큰대 시민 인문학",
            source_kind="365edu",
            institution="평생학습관",
            institution_code="EACD025",
            start="2026-08-05",
            end="2026-10-07",
            venue="서구 평생학습관",
            schedule="매주 수요일 10:00 ~ 12:00",
            apply_period="2026-07-15 (10:00) ~ 2026-08-04 (17:00)",
            oes_id="OES_2026072100000001",
            oec_id="OEC_2026072100000001",
        ),
        Course(
            "365-wait",
            "세큰대 미래과학 교실",
            source_kind="365edu",
            source_status="접수대기",
            institution="평생학습관",
            institution_code="EACD025",
            start="2026-09-01",
            end="2026-11-30",
            venue="서구 평생학습관",
            schedule="매주 월요일 15:00 ~ 17:00",
            apply_period="2026-08-01 (10:00) ~ 2026-08-31 (17:00)",
            oes_id="OES_2026072100000002",
            oec_id="OEC_2026072100000002",
        ),
        Course(
            "123",
            "일자리센터 취업 면접교육",
            source_kind="job",
            institution="일자리센터",
            institution_code="EACD011",
            start="2026-07-25",
            end="2026-07-26",
            venue="서구 일자리센터",
            schedule="10:00 ~ 16:00",
            apply_period="2026-07-01 (09:00) ~ 2026-07-24 (18:00)",
            capacity=20,
        ),
    ]
    historical = [
        Course(
            str(identity),
            f"서구 과거 강좌 {identity}",
            venue=f"과거 교육장 {identity}",
        )
        for identity in range(201, 209)
    ]
    return [*current, *historical]


def _options(values: tuple[tuple[str, str], ...]) -> str:
    return "".join(
        f'<option value="{escape(code)}">{escape(label)}</option>'
        for code, label in values
    )


def _search_form(page: int) -> str:
    return f"""
      <form id="srhForm" method="post"
            action="/applySearchList.es?mid={seogu.GWANGJU_SEOGU_MID}">
        <input type="hidden" name="mid" value="{seogu.GWANGJU_SEOGU_MID}">
        <input type="hidden" name="nPage" value="{page}">
        <input type="hidden" name="eap_seq" value="">
        <input type="hidden" name="srh_div" value="{seogu.GWANGJU_SEOGU_EDUCATION_DIVISION}">
        <input type="hidden" name="srh_inte_code" value="">
        <input type="hidden" name="_csrf" value="fixture-csrf-token">
        <input type="text" name="srh_sdate" value="">
        <input type="text" name="srh_edate" value="">
        <input type="text" name="keyWord" value="">
        <select name="srh_eas_code">{_options(seogu.GWANGJU_SEOGU_INSTITUTIONS)}</select>
        <select name="srh_target">{_options(seogu.GWANGJU_SEOGU_TARGETS)}</select>
        <select name="srh_tuition">{_options(seogu.GWANGJU_SEOGU_TUITIONS)}</select>
        <select name="srh_field">{_options(seogu.GWANGJU_SEOGU_FIELDS)}</select>
        <select name="srh_state">{_options(seogu.GWANGJU_SEOGU_SEARCH_STATUSES)}</select>
      </form>
    """


def _source_status_contract(
    course: Course,
    *,
    detail: bool = False,
) -> tuple[str, str]:
    if course.source_status == "접수중":
        return "ing", "접수중"
    if course.source_status == "정원마감":
        return "end", "종료"
    if course.source_status == "대기신청":
        return "book", "대기" if detail else "진행중"
    return "wait", "접수예정"


def _365_url(course: Course) -> str:
    is_accept = "R" if course.source_status == "접수대기" else "P"
    return (
        f"https://{seogu.GWANGJU_SEOGU_365_HOST}/365edu/index.9is?"
        f"contentUid={seogu.GWANGJU_SEOGU_365_CONTENT_UID}"
        f"&oesSubjectId={course.oes_id}&oecId={course.oec_id}"
        f"&isAccept={is_accept}&isAddAccept=N"
    )


def _list_anchor(course: Course) -> tuple[str, str]:
    if course.source_kind == "native":
        onclick = (
            f"goView2('{course.identity}','{seogu.GWANGJU_SEOGU_MID}',"
            f"'','{course.eap_code}'); return false;"
        )
        return f"/applyList.es?mid={seogu.GWANGJU_SEOGU_MID}", onclick
    if course.source_kind == "365edu":
        return _365_url(course), ""
    return seogu.gwangju_seogu_job_detail_url(course.identity), ""


def _list_card(course: Course) -> str:
    href, onclick = _list_anchor(course)
    status_class, data_label = _source_status_contract(course)
    subbranch = (
        f'<span class="state cate">{escape(course.subbranch)}</span>'
        if course.subbranch
        else ""
    )
    onclick_attr = f' onclick="{escape(onclick, quote=True)}"' if onclick else ""
    return f"""
      <li>
        <a href="{escape(href, quote=True)}"{onclick_attr}>
          <div class="txt">
            <div class="tt">
              <div class="type">
                <span class="state {status_class}" data-label="{data_label}">{course.source_status}</span>
                <span class="state cate">{escape(course.institution)}</span>
                {subbranch}
              </div>
              <strong>{escape(course.title)}</strong>
            </div>
            <ul class="con">
              <li><strong>교육장소</strong><span>{escape(course.venue)}</span></li>
              <li><strong>교육일자</strong><span>{course.start} ~ {course.end}</span></li>
              <li><strong>교육시간</strong><span>{escape(course.schedule)}</span></li>
              <li><strong>접수기간</strong><span>{course.apply_period}</span></li>
              <li><strong>등록일자</strong><span>저장금지 010-7777-8888 hidden@example.com</span></li>
            </ul>
          </div>
        </a>
      </li>
    """


def _list_html(page: int, rows: list[Course], total: int) -> str:
    last = (total + seogu.GWANGJU_SEOGU_PAGE_SIZE - 1) // seogu.GWANGJU_SEOGU_PAGE_SIZE
    body = (
        "".join(_list_card(course) for course in rows)
        if rows
        else '<li class="nodata">해당 내용이 없습니다.</li>'
    )
    return f"""
      <html>
        <head><title>{seogu.GWANGJU_SEOGU_LIST_TITLE}</title></head>
        <body>
          {_search_form(page)}
          <p class="page">
            <span class="total"><b>{total}건</b></span>
            <span class="current"><strong>{page}</strong> / <b>{last}</b></span>
          </p>
          <div class="apply_list webzine"><ul class="gallery_list">{body}</ul></div>
        </body>
      </html>
    """


def _hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{name}" value="{escape(value, quote=True)}">'


def _native_detail_html(
    course: Course,
    *,
    detail_title: str | None = None,
    institution_code: str | None = None,
    control: bool = True,
    control_identity: str | None = None,
) -> str:
    status_class, data_label = _source_status_contract(course, detail=True)
    caption = (
        f"{course.title} 프로그램을 상태, 교육명, 장소, 교육기간, 교육시간, "
        "모집정원, 문의를 구분한 표입니다."
    )
    button = ""
    if control:
        button_identity = control_identity or course.identity
        wait = "Y" if course.source_status == "대기신청" else "N"
        label = "대기신청" if course.source_status == "대기신청" else "신청"
        onclick = (
            f"mem_goForm('{button_identity}','{course.title}','{wait}',"
            f"'{course.eap_code}','Y'); return false;"
        )
        button = (
            f'<button type="button" class="btn type2" '
            f'onclick="{escape(onclick, quote=True)}">{label}</button>'
        )
    return f"""
      <html>
        <head><title>{seogu.GWANGJU_SEOGU_LIST_TITLE}</title></head>
        <body>
          <form id="srhForm" method="post" action="/applyView.es">
            {_hidden("mid", seogu.GWANGJU_SEOGU_MID)}
            {_hidden("eap_seq", course.identity)}
            {_hidden("eas_code", institution_code or course.institution_code)}
            {_hidden("keyField", "")}
            {_hidden("keyWord", "")}
            {_hidden("nPage", "")}
            {_hidden("eap_code", course.eap_code)}
          </form>
          <div class="board_view webzine">
            <div class="head"><div class="txt"><table>
              <caption>{escape(caption)}</caption>
              <tbody>
                <tr><th>상태</th><td><span class="state {status_class}" data-label="{data_label}">{course.source_status}</span></td></tr>
                <tr><th>교육명</th><td>{escape(detail_title or course.title)}</td></tr>
                <tr><th>장소</th><td>{escape(course.venue)}</td></tr>
                <tr><th>교육기간</th><td>{course.start} ~ {course.end}</td></tr>
                <tr><th>교육시간</th><td>{escape(course.schedule)}</td></tr>
                <tr><th>모집정원</th><td>{course.capacity}명</td></tr>
                <tr><th>문의</th><td>저장금지 담당자 062-123-4567 private@example.com</td></tr>
              </tbody>
            </table></div></div>
            <div class="con">저장금지 자유 설명 010-9999-8888 applicant@example.com</div>
          </div>
          <p class="board_btns">{button}</p>
        </body>
      </html>
    """


def _365_detail_html(
    course: Course,
    *,
    detail_title: str | None = None,
    detail_status: str | None = None,
    control: bool | None = None,
) -> str:
    is_open = course.source_status == "접수중"
    is_capacity_closed = course.source_status == "정원마감"
    marker = detail_status or (
        "신청가능" if is_open or is_capacity_closed else "모집예정"
    )
    marker_class = "ms03" if marker == "신청가능" else "ms04"
    include_control = (
        is_open or is_capacity_closed if control is None else control
    )
    button = ""
    if include_control:
        query = (
            f"contentUid={seogu.GWANGJU_SEOGU_365_LOGIN_RETURN_UID}"
            f"&oesSubjectId={course.oes_id}&oecId={course.oec_id}"
            "&isAccept=P&isAddAccept=N"
        )
        onclick = f"alert('로그인이 필요합니다'); loginReturnUrl('{query}')"
        button = (
            f'<div class="tc"><a class="btns theme" href="javascript:void(0)" '
            f'onclick="{escape(onclick, quote=True)}">신청</a></div>'
        )
    return f"""
      <html>
        <head><title>{seogu.GWANGJU_SEOGU_365_TITLE}</title></head>
        <body>
          <table class="board_view2"><tbody>
            <tr><td class="view_title textind"><h4>
              <span class="mask {marker_class}">{marker}</span>
              {escape(detail_title or course.title)}
            </h4></td></tr>
            <tr><td class="data_cont"><h4>강의내용</h4>
              저장금지 강사 홍길동 / 062-555-7777 / teacher@example.com
            </td></tr>
          </tbody></table>
          <table class="instructor"><tr><td>저장금지 교육담당자 010-2222-3333</td></tr></table>
          {button}
        </body>
      </html>
    """


def _job_detail_html(course: Course, *, control_identity: str | None = None) -> str:
    identity = control_identity or course.identity
    return f"""
      <html>
        <head><title>{seogu.GWANGJU_SEOGU_JOB_TITLE}</title></head>
        <body>
          <form id="srhForm" method="post" action="/jobProgramView.es">
            {_hidden("mid", seogu.GWANGJU_SEOGU_JOB_MID)}
            {_hidden("jp_seq", course.identity)}
            {_hidden("keyField", "")}
            {_hidden("keyWord", "")}
            {_hidden("nPage", "")}
          </form>
          <div class="board_view jobProgram">
            <div class="head"><div class="txt"><table><tbody>
              <tr><th>상태</th><td><span class="state ing" data-label="접수">신청가능</span></td></tr>
              <tr><th>교육명</th><td>{escape(course.title)}</td></tr>
              <tr><th>교육장소</th><td>{escape(course.venue)}</td></tr>
              <tr><th>교육분류</th><td>{escape(course.detail_category)}</td></tr>
              <tr><th>교육기간</th><td>{course.start} ~ {course.end}</td></tr>
              <tr><th>교육시간</th><td>금요일 {escape(course.schedule)}</td></tr>
              <tr><th>모집정원</th><td>{course.capacity}명</td></tr>
              <tr><th>문의</th><td>저장금지 062-888-9999 jobs@example.com</td></tr>
            </tbody></table></div></div>
            <div class="con">저장금지 지원자 양식 010-1111-2222</div>
          </div>
          <div class="board_btns"><button type="button" class="btn type2"
            onclick="goForm('{seogu.GWANGJU_SEOGU_JOB_MID}','{identity}'); return false;">신청</button></div>
        </body>
      </html>
    """


class HtmlFixture:
    def __init__(self, courses: list[Course] | None = None) -> None:
        self.courses = list(courses or _courses())
        self.pages: dict[str, str] = {}
        last = max(
            1,
            (len(self.courses) + seogu.GWANGJU_SEOGU_PAGE_SIZE - 1)
            // seogu.GWANGJU_SEOGU_PAGE_SIZE,
        )
        for page in range(1, last + 1):
            start = (page - 1) * seogu.GWANGJU_SEOGU_PAGE_SIZE
            rows = self.courses[start : start + seogu.GWANGJU_SEOGU_PAGE_SIZE]
            self.pages[seogu.gwangju_seogu_list_url(page)] = _list_html(
                page, rows, len(self.courses)
            )
        self.pages[seogu.gwangju_seogu_list_url(last + 1)] = _list_html(
            last + 1, [], len(self.courses)
        )
        for course in self.courses:
            if course.end < "2026-07-21":
                continue
            if course.source_kind == "native":
                url = seogu.gwangju_seogu_native_detail_url(
                    course.identity, course.eap_code
                )
                self.pages[url] = _native_detail_html(course)
            elif course.source_kind == "365edu":
                self.pages[_365_url(course)] = _365_detail_html(course)
            else:
                self.pages[
                    seogu.gwangju_seogu_job_detail_url(course.identity)
                ] = _job_detail_html(course)
        self.overrides: dict[tuple[str, int], str] = {}
        self.failures: Counter[str] = Counter()
        self.calls: Counter[str] = Counter()
        self.active = 0
        self.max_active = 0
        self.lock = Lock()

    def fetch(self, _session: DummySession, url: str, _timeout: int) -> str:
        with self.lock:
            self.calls[url] += 1
            call = self.calls[url]
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            should_fail = self.failures[url] > 0
            if should_fail:
                self.failures[url] -= 1
        try:
            time.sleep(0.002)
            if should_fail:
                raise RuntimeError("fixture transient failure")
            override = self.overrides.get((url, call))
            if override is not None:
                return override
            if url not in self.pages:
                raise RuntimeError(f"unexpected URL: {url}")
            return self.pages[url]
        finally:
            with self.lock:
                self.active -= 1


def _target(**changes: str) -> Target:
    values = {
        "provider": seogu.GWANGJU_SEOGU_PROVIDER,
        "url": seogu.GWANGJU_SEOGU_CANONICAL_URL,
        "candidate_id": seogu.GWANGJU_SEOGU_CANONICAL_CANDIDATE_ID,
    }
    values.update(changes)
    return Target(**values)


def _collect(fixture: HtmlFixture, **kwargs):
    return seogu.collect(
        _target(),
        today="2026-07-21",
        timeout=5,
        max_pages=20,
        detail_limit=100,
        max_workers=4,
        session_factory=DummySession,
        fetcher=fixture.fetch,
        **kwargs,
    )


def test_constants_urls_target_and_candidate_audit() -> None:
    assert seogu.GWANGJU_SEOGU_PROVIDER == "MUNI_WWW_SEOGU_GWANGJU_KR_10B34AC9"
    assert seogu.GWANGJU_SEOGU_MUNICIPALITY_CODE == "1224000000"
    assert seogu.GWANGJU_SEOGU_MUNICIPALITY_NAME == "전남광주통합특별시 서구"
    assert seogu.gwangju_seogu_list_url(1) == seogu.GWANGJU_SEOGU_CANONICAL_URL
    assert parse_qs(urlparse(seogu.gwangju_seogu_list_url(7)).query) == {
        "mid": [seogu.GWANGJU_SEOGU_MID],
        "srh_div": [seogu.GWANGJU_SEOGU_EDUCATION_DIVISION],
        "nPage": ["7"],
    }
    assert parse_qs(
        urlparse(seogu.gwangju_seogu_native_detail_url("101", "C04")).query,
        keep_blank_values=True,
    ) == {
        "mid": [seogu.GWANGJU_SEOGU_MID],
        "eap_seq": ["101"],
        "eas_code": [""],
        "search_yn": ["Y"],
        "eap_code": ["C04"],
    }
    assert parse_qs(
        urlparse(
            seogu.gwangju_seogu_native_application_url(
                "101", "EACD004", "C04"
            )
        ).query,
        keep_blank_values=True,
    )["eas_code"] == ["EACD004"]
    assert seogu.is_target(_target())
    assert seogu.is_target(_target(url=seogu.GWANGJU_SEOGU_CANDIDATE_URL))
    assert not seogu.is_target(_target(provider="WRONG"))
    assert not seogu.is_target(
        _target(url=seogu.GWANGJU_SEOGU_CANONICAL_URL + "#fragment")
    )
    assert seogu.is_gwangju_seogu_candidate_alias(
        Target("ignored", "ignored", seogu.GWANGJU_SEOGU_LANDING_CANDIDATE_ID)
    )
    assert seogu.is_gwangju_seogu_candidate_alias(
        Target("ignored", seogu.GWANGJU_SEOGU_CULTURE_URL)
    )
    decisions = {
        key: value["decision"]
        for key, value in seogu.GWANGJU_SEOGU_CANDIDATE_AUDIT.items()
    }
    assert decisions[seogu.GWANGJU_SEOGU_CANONICAL_CANDIDATE_ID].startswith(
        "include_as_complete"
    )
    assert "superseded_by_complete" in decisions[
        seogu.GWANGJU_SEOGU_LANDING_CANDIDATE_ID
    ]
    assert decisions[seogu.GWANGJU_SEOGU_HEALTH_CANDIDATE_ID].startswith(
        "exclude_health"
    )
    assert decisions[seogu.GWANGJU_SEOGU_CULTURE_CANDIDATE_ID].startswith(
        "exclude_separate_cultural"
    )
    with pytest.raises(ValueError):
        seogu.gwangju_seogu_native_detail_url("../101")


def test_capacity_closed_marker_is_a_valid_current_course_state() -> None:
    course = replace(_courses()[0], source_status="정원마감")
    soup = BeautifulSoup(_list_card(course), "lxml")
    row = seogu._parse_list_card(soup.li, 1)

    assert row["source_status"] == "정원마감"
    assert row["status"] == "CLOSED"


def test_native_waitlist_marker_and_identity_bound_control_are_actionable() -> None:
    course = replace(_courses()[0], source_status="대기신청", venue="")
    listed_soup = BeautifulSoup(_list_card(course), "lxml")
    listed = seogu._parse_list_card(listed_soup.li, 1)
    detail_soup = BeautifulSoup(_native_detail_html(course), "lxml")
    detail = seogu._parse_native_detail(detail_soup, listed)
    row = seogu._build_row(listed, detail)

    assert row["status"] == "WAITLIST"
    assert row["reservation_available"] is True
    assert row["application_type"] == "WAITLIST_APPLY"
    assert row["venue_name"] == "전남광주통합특별시 서구 / 정보화교육"
    assert row["raw_fields"]["venue_fallback_used"] is True
    assert parse_qs(
        urlparse(row["application_url"]).query,
        keep_blank_values=True,
    ) == {
        "mid": [seogu.GWANGJU_SEOGU_MID],
        "eap_seq": ["101"],
        "eas_code": ["EACD001"],
        "wait_yn": ["Y"],
        "eap_code": [""],
        "search_yn": ["Y"],
    }


def test_365_capacity_closed_list_overrides_stale_open_detail_control() -> None:
    closed = replace(_courses()[2], source_status="정원마감")
    fixture = HtmlFixture([closed])

    rows, _parser, meta = _collect(fixture)

    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "CLOSED"
    assert row["reservation_available"] is False
    assert row["application_url"] == ""
    assert row["application_type"] == "INFO_ONLY"
    assert row["raw_fields"]["visible_application_control_present"] is True
    assert row["raw_fields"]["actionable_application_control_present"] is False
    assert row["raw_fields"]["application_control_contract"] == (
        "closed_list_overrides_identity_bound_stale_365edu_control"
    )
    assert meta["visible_public_application_control_count"] == 1
    assert meta["actionable_application_control_count"] == 0
    assert meta["application_controls_complete"] is True
    assert meta["snapshot_complete"] is True


def test_complete_collection_all_sources_branches_controls_and_pii() -> None:
    fixture = HtmlFixture()
    rows, parser, meta = _collect(fixture)

    assert parser == seogu.GWANGJU_SEOGU_PARSER
    assert [row["raw_fields"]["identity"] for row in rows] == [
        "native:101",
        "native:102",
        "365edu:OES_2026072100000001:OEC_2026072100000001",
        "365edu:OES_2026072100000002:OEC_2026072100000002",
        "job:123",
    ]
    assert meta["declared_source_rows"] == meta["source_rows"] == 13
    assert meta["declared_data_pages"] == meta["derived_data_pages"] == 2
    assert meta["data_pages"] == 2
    assert meta["required_list_requests"] == meta["list_requests"] == 5
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 2
    assert meta["current_source_count"] == meta["returned_count"] == 5
    assert meta["expired_count"] == 8
    assert meta["detail_attempts"] == meta["detail_pages"] == 5
    assert meta["detail_errors"] == 0
    assert meta["pages"] == 10
    assert meta["source_kind_counts"] == {"native": 2, "365edu": 2, "job": 1}
    assert meta["status_counts"] == {"OPEN": 4, "SCHEDULED": 1}
    assert meta["institution_counts"] == {
        "정보화교육": 1,
        "통합행정복지센터": 1,
        "평생학습관": 2,
        "일자리센터": 1,
    }
    assert meta["branch_counts"] == {
        "전남광주통합특별시 서구 / 정보화교육": 1,
        "전남광주통합특별시 서구 / 통합행정복지센터 / 화정4동": 1,
        "전남광주통합특별시 서구 / 평생학습관": 2,
        "전남광주통합특별시 서구 / 일자리센터": 1,
    }
    assert meta["visible_public_application_control_count"] == 4
    assert meta["actionable_application_control_count"] == 4
    assert meta["identity_duplicate_count"] == 0
    assert meta["raw_url_duplicate_count"] == 0
    assert meta["semantic_duplicate_group_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["municipality_coverage"] == ["1224000000"]
    assert fixture.max_active <= seogu.GWANGJU_SEOGU_MAX_WORKERS

    native, subbranch, learning_open, learning_wait, job = rows
    assert native["branch"] == "전남광주통합특별시 서구 / 정보화교육"
    assert subbranch["branch"] == "전남광주통합특별시 서구 / 통합행정복지센터 / 화정4동"
    assert learning_open["branch"] == learning_wait["branch"] == (
        "전남광주통합특별시 서구 / 평생학습관"
    )
    assert job["branch"] == "전남광주통합특별시 서구 / 일자리센터"
    assert native["capacity_total"] == 24
    assert subbranch["capacity_total"] == 18
    assert job["capacity_total"] == 20
    assert all(row["program_type"] == "교육" for row in rows)
    assert all(row["municipality_code"] == "1224000000" for row in rows)
    assert all(row["raw_fields"]["detail_verified"] is True for row in rows)
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["target"] == "대상 별도 안내" for row in rows)
    assert all(row["fee"] == "요금 별도 안내" for row in rows)
    assert all(row["reservation_available"] for row in rows if row["status"] == "OPEN")
    assert learning_wait["reservation_available"] is False
    assert learning_wait["application_url"] == ""
    assert learning_wait["application_type"] == "INFO_ONLY"
    assert learning_open["application_url"] == learning_open["raw_url"]
    assert job["application_url"] == seogu.gwangju_seogu_job_application_url("123")

    payload = repr(rows)
    for forbidden in (
        "저장금지",
        "홍길동",
        "062-123-4567",
        "010-9999-8888",
        "hidden@example.com",
        "private@example.com",
        "teacher@example.com",
        "applicant@example.com",
        "instructor",
        "manager",
        "contact",
        "source_html",
    ):
        assert forbidden not in payload
    assert meta["pii_payload_persisted"] is False


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("교육기관 선택", "기관 선택", "institution taxonomy changed"),
        ("교육대상 선택", "대상 선택", "target taxonomy changed"),
        ("수강료 선택", "비용 선택", "tuition taxonomy changed"),
        ("교육분야 선택", "분야 선택", "education-field taxonomy changed"),
        ("접수상태 선택", "상태 선택", "reception-status taxonomy changed"),
    ],
)
def test_search_taxonomies_are_exact(old: str, new: str, message: str) -> None:
    fixture = HtmlFixture()
    first = seogu.gwangju_seogu_list_url(1)
    fixture.pages[first] = fixture.pages[first].replace(old, new, 1)
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_immediate_post_last_page_must_be_explicit_empty() -> None:
    fixture = HtmlFixture()
    sentinel = seogu.gwangju_seogu_list_url(3)
    fixture.pages[sentinel] = _list_html(3, [fixture.courses[-1]], 13)
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "immediate post-last sentinel is not stable empty" in meta[
        "configured_collection_error"
    ]
    assert meta["detail_attempts"] == 0


@pytest.mark.parametrize(("page", "message"), [(1, "first-page"), (2, "last-page")])
def test_first_and_last_page_rechecks_must_be_stable(page: int, message: str) -> None:
    fixture = HtmlFixture()
    url = seogu.gwangju_seogu_list_url(page)
    course = fixture.courses[(page - 1) * seogu.GWANGJU_SEOGU_PAGE_SIZE]
    fixture.overrides[(url, 2)] = fixture.pages[url].replace(
        course.title, course.title + " 변경", 1
    )
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_duplicate_identity_semantic_duplicate_and_reversed_period_fail_closed() -> None:
    courses = _courses()
    courses[10] = replace(courses[10], identity=courses[0].identity)
    rows, _parser, meta = _collect(HtmlFixture(courses))
    assert rows == []
    assert "duplicate official identities" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0

    courses = _courses()
    courses[10] = replace(
        courses[10],
        identity="999",
        title=courses[0].title,
        start=courses[0].start,
        end=courses[0].end,
        venue=courses[0].venue,
    )
    rows, _parser, meta = _collect(HtmlFixture(courses))
    assert rows == []
    assert "semantic duplicate groups" in meta["configured_collection_error"]
    assert meta["semantic_duplicate_group_count"] == 1

    fixture = HtmlFixture()
    first = seogu.gwangju_seogu_list_url(1)
    fixture.pages[first] = fixture.pages[first].replace(
        "2026-07-21 ~ 2026-08-20", "2026-08-20 ~ 2026-07-21", 1
    )
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "reversed education period" in meta["configured_collection_error"]


def test_365_list_link_status_identity_contract_is_exact() -> None:
    fixture = HtmlFixture()
    first = seogu.gwangju_seogu_list_url(1)
    fixture.pages[first] = fixture.pages[first].replace("isAccept=P", "isAccept=R", 1)
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "365edu link/status mismatch" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("native_title", "list/detail fields mismatch"),
        ("native_schema", "detail schema changed"),
        ("native_institution", "navigation field eas_code changed"),
        ("native_no_control", "active course has no unique application control"),
        ("native_wrong_control", "application control identity mismatch"),
        ("365_title", "365edu title mismatch"),
        ("365_status", "365edu list/detail status mismatch"),
        ("365_no_control", "365edu open control changed"),
        ("365_scheduled_control", "scheduled 365edu course exposes"),
        ("job_wrong_control", "job application identity mismatch"),
    ],
)
def test_detail_and_public_application_contracts_fail_closed(
    mutation: str, message: str
) -> None:
    fixture = HtmlFixture()
    native = fixture.courses[0]
    learning_open = fixture.courses[2]
    learning_wait = fixture.courses[3]
    job = fixture.courses[4]
    if mutation.startswith("native"):
        url = seogu.gwangju_seogu_native_detail_url(native.identity, native.eap_code)
        if mutation == "native_title":
            fixture.pages[url] = _native_detail_html(native, detail_title="다른 강좌")
        elif mutation == "native_schema":
            fixture.pages[url] = fixture.pages[url].replace(
                "<th>모집정원</th>", "<th>정원</th>", 1
            )
        elif mutation == "native_institution":
            fixture.pages[url] = _native_detail_html(
                native, institution_code="EACD004"
            )
        elif mutation == "native_no_control":
            fixture.pages[url] = _native_detail_html(native, control=False)
        else:
            fixture.pages[url] = _native_detail_html(native, control_identity="999")
    elif mutation.startswith("365"):
        if mutation == "365_scheduled_control":
            fixture.pages[_365_url(learning_wait)] = _365_detail_html(
                learning_wait, control=True
            )
        elif mutation == "365_title":
            fixture.pages[_365_url(learning_open)] = _365_detail_html(
                learning_open, detail_title="다른 평생학습 강좌"
            )
        elif mutation == "365_status":
            fixture.pages[_365_url(learning_open)] = _365_detail_html(
                learning_open, detail_status="모집예정"
            )
        else:
            fixture.pages[_365_url(learning_open)] = _365_detail_html(
                learning_open, control=False
            )
    else:
        fixture.pages[
            seogu.gwangju_seogu_job_detail_url(job.identity)
        ] = _job_detail_html(job, control_identity="999")
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["detail_errors"] == 1
    assert meta["snapshot_complete"] is False


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "message"),
    [(4, 100, "max_pages cap"), (20, 4, "detail_limit cap")],
)
def test_caps_fail_before_partial_snapshot(
    max_pages: int, detail_limit: int, message: str
) -> None:
    fixture = HtmlFixture()
    rows, _parser, meta = seogu.collect(
        _target(),
        today="2026-07-21",
        max_pages=max_pages,
        detail_limit=detail_limit,
        session_factory=DummySession,
        fetcher=fixture.fetch,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert message in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0


def test_bounded_retries_and_dedupe_cardinality() -> None:
    fixture = HtmlFixture()
    page2 = seogu.gwangju_seogu_list_url(2)
    fixture.failures[page2] = 2
    rows, _parser, meta = _collect(fixture)
    assert len(rows) == 5
    assert fixture.calls[page2] >= 4
    assert meta["snapshot_complete"] is True

    fixture = HtmlFixture()
    detail = seogu.gwangju_seogu_native_detail_url("101")
    fixture.failures[detail] = seogu.GWANGJU_SEOGU_FETCH_ATTEMPTS
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert fixture.calls[detail] == seogu.GWANGJU_SEOGU_FETCH_ATTEMPTS
    assert "fixture transient failure" in meta["configured_collection_error"]

    fixture = HtmlFixture()
    rows, _parser, meta = _collect(
        fixture, dedupe_rows=lambda values: values[:-1]
    )
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta[
        "configured_collection_error"
    ]
    assert meta["full_snapshot_validated"] is False


def test_wrong_target_invalid_limits_and_all_expired_snapshot() -> None:
    fixture = HtmlFixture()
    rows, _parser, meta = seogu.collect(
        _target(provider="WRONG"),
        session_factory=DummySession,
        fetcher=fixture.fetch,
    )
    assert rows == []
    assert "canonical Gwangju Seo-gu education owner" in meta[
        "configured_collection_error"
    ]

    rows, _parser, meta = seogu.collect(
        _target(),
        max_workers=0,
        session_factory=DummySession,
        fetcher=fixture.fetch,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "invalid collection limits" in meta["configured_collection_error"]

    expired = [
        replace(
            course,
            start="2025-01-01",
            end="2025-02-01",
            apply_period="2024-12-01 (09:00) ~ 2024-12-31 (18:00)",
        )
        for course in _courses()
    ]
    expired_fixture = HtmlFixture(expired)
    rows, _parser, meta = seogu.collect(
        _target(),
        today="2026-07-21",
        max_pages=20,
        detail_limit=0,
        session_factory=DummySession,
        fetcher=expired_fixture.fetch,
    )
    assert rows == []
    assert meta["current_source_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["no_current_data"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""


@pytest.mark.skipif(
    os.getenv("GWANGJU_SEOGU_LIVE_TEST") != "1",
    reason="set GWANGJU_SEOGU_LIVE_TEST=1 for official live audit",
)
def test_live_official_catalogue_audit_2026_07_21() -> None:
    rows, _parser, meta = seogu.collect(
        _target(),
        today="2026-07-21",
        timeout=30,
        max_pages=30,
        detail_limit=100,
    )
    assert meta["configured_collection_error"] == ""
    assert meta["declared_source_rows"] == meta["source_rows"] == 38
    assert meta["declared_data_pages"] == meta["derived_data_pages"] == 4
    assert meta["data_pages"] == 4
    assert meta["required_list_requests"] == meta["list_requests"] == 7
    assert meta["sentinel_requests"] == 1
    assert meta["stability_rechecks"] == 2
    assert meta["current_source_count"] == meta["detail_pages"] == len(rows) == 38
    assert meta["source_kind_counts"] == {"native": 20, "365edu": 17, "job": 1}
    assert meta["status_counts"] == {"OPEN": 27, "SCHEDULED": 10, "CLOSED": 1}
    assert meta["institution_counts"] == {
        "빛고을국악전수관": 6,
        "정보화교육": 2,
        "평생학습관": 17,
        "통합행정복지센터": 10,
        "서구청": 2,
        "일자리센터": 1,
    }
    assert meta["visible_public_application_control_count"] == 28
    assert meta["actionable_application_control_count"] == 27
    assert meta["semantic_duplicate_group_count"] == 0
    assert meta["pages"] == 45
    assert meta["full_snapshot_validated"] is True
