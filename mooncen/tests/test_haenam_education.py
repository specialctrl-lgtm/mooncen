from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from html import escape
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_haenam as haenam


FOUNDATION_TARGET = {
    "provider": haenam.HAENAM_FOUNDATION_PROVIDER,
    "url": haenam.HAENAM_FOUNDATION_URL,
}
LIBRARY_TARGET = {
    "provider": haenam.HAENAM_LIBRARY_PROVIDER,
    "url": haenam.HAENAM_LIBRARY_URL,
}


class Response:
    def __init__(
        self,
        body: str | bytes,
        url: str,
        *,
        encoding: str = "utf-8",
        status_code: int = 200,
        content_type: str = "text/html",
        final_url: str | None = None,
        history: tuple[Any, ...] = (),
    ) -> None:
        self.status_code = status_code
        self.content = body.encode(encoding) if isinstance(body, str) else body
        self.url = final_url or url
        self.headers = {"Content-Type": content_type}
        self.history = history


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True)
class FoundationCourse:
    identity: str
    cohort: str
    title: str
    partition: str
    page: int
    event_start: str
    event_end: str
    apply_start: str
    apply_end: str
    source_status: str
    venue: str
    method: str
    capacity: int

    @property
    def accept(self) -> str:
        return "P" if self.source_status == "접수중" else "C"

    @property
    def detail_url(self) -> str:
        return haenam.haenam_foundation_detail_url(
            self.identity,
            self.cohort,
            "OEP_0000000000000003",
            self.accept,
            "N",
        )


def _foundation_courses() -> list[FoundationCourse]:
    regular = [
        FoundationCourse(
            identity=f"OES_{identity:016d}",
            cohort="OEC_0000000000000090",
            title=f"정규 평생학습 강좌 {index}",
            partition="regular",
            page=1,
            event_start="2026-08-18",
            event_end="2026-12-04",
            apply_start="2026-07-13 09:00",
            apply_end="2026-07-24 18:00",
            source_status="접수중",
            venue="해남군평생학습관",
            method="온/오프라인 접수",
            capacity=12,
        )
        for index, identity in enumerate(range(369, 405), start=1)
    ]
    current_venues = [
        "-",
        "해남군관내",
        "해남군교육재단",
        "해남군교육재단",
        "해남군 관내",
        "미래행복평생교육원",
    ]
    nonregular: list[FoundationCourse] = []
    for index in range(47):
        is_current = index < 6
        is_open = index < 2
        nonregular.append(
            FoundationCourse(
                identity=f"OES_{1000 + index:016d}",
                cohort=f"OEC_{100 + index:016d}",
                title=f"비정규 교육 {index + 1}",
                partition="nonregular",
                page=index // 12 + 1,
                event_start="2026-06-01" if is_current else "2026-01-02",
                event_end="2026-12-31" if is_current else "2026-06-30",
                apply_start="2026-07-01 09:00",
                apply_end="2026-07-31 18:00",
                source_status="접수중" if is_open else "접수마감",
                venue=(
                    current_venues[index]
                    if is_current
                    else "해남군교육재단"
                ),
                method=(
                    "방문 또는 이메일 접수"
                    if index == 0
                    else "방문 접수"
                ),
                capacity=0,
            )
        )
    return regular + nonregular


def _foundation_card(course: FoundationCourse, *, identity: str | None = None) -> str:
    source_identity = identity or course.identity
    detail_url = haenam.haenam_foundation_detail_url(
        source_identity,
        course.cohort,
        "OEP_0000000000000003",
        course.accept,
        "N",
    )
    source_class = "btn_acc" if course.source_status == "접수중" else "btn_end"
    card_class = ' class="accepting"' if course.source_status == "접수중" else ""
    start_date, start_time = course.apply_start.split()
    end_date, end_time = course.apply_end.split()
    start_hour, start_minute = start_time.split(":")
    end_hour, end_minute = end_time.split(":")
    return f"""
      <li{card_class}>
        <div class="select-box">
          <input class="course-checkbox" type="checkbox" name="selectedCourse"
            value="{source_identity}" data-oec-id="{course.cohort}"
            data-accept-status="{course.accept}" data-add-accept-status="N"
            data-start-date="{start_date}" data-start-time="{start_hour}"
            data-start-minute="{start_minute}" data-end-date="{end_date}"
            data-end-time="{end_hour}" data-end-minute="{end_minute}"
            data-confirm-count="0" data-user-no="{course.capacity}">
        </div>
        <div class="txt_day"><span class="year">2026</span>
          <p class="txt"><span class="day">07.01 - 07.31</span>
            <span class="{source_class}">{course.source_status}</span></p>
        </div>
        <a href="{escape(detail_url, quote=True)}">
          <dl class="txtBox"><dt>{escape(course.title)}</dt>
            <dd>교육기간 {course.event_start} ~ {course.event_end}</dd>
            <dd></dd><dd>교육기관 해남군교육재단</dd>
            <dd>접수방법 {course.method}</dd>
          </dl>
        </a>
      </li>
    """


def _foundation_list_html(
    courses: list[FoundationCourse],
    partition: str,
    page: int,
    *,
    mode: str = "normal",
    recheck: bool = False,
) -> str:
    if partition == "1":
        visible = [item for item in courses if item.partition == "regular"]
    else:
        visible = [
            item
            for item in courses
            if item.partition == "nonregular" and item.page == page
        ]
    if mode == "sentinel_unstable" and partition == "2" and page == 5 and recheck:
        visible = [next(item for item in courses if item.partition == "nonregular")]
    cards: list[str] = []
    for index, course in enumerate(visible):
        duplicate = None
        if mode == "duplicate" and partition == "2" and page == 1 and index == 0:
            duplicate = courses[0].identity
        cards.append(_foundation_card(course, identity=duplicate))
    return f"""
      <html><head><title>해남군교육재단 &gt; 교육재단 교육정보</title></head>
      <body><form id="searchMyAccept" action="./index.9is" method="post">
        <input name="contentUid" value="{haenam.HAENAM_FOUNDATION_LIST_UID}">
        <input name="oecRegularYn" value="{partition}">
      </form>
      <div class="guideList" data-regular-yn="{partition}">
        <ul class="listBox">{''.join(cards)}</ul>
      </div></body></html>
    """


def _foundation_detail_html(
    course: FoundationCourse,
    *,
    mode: str = "normal",
) -> str:
    title = course.title + " 변경" if mode == "detail_mismatch" else course.title
    venue = "담당자 061-123-4567" if mode == "pii_venue" else course.venue
    capacity = (
        f"<li class='txt_line'><span>모집인원</span>{course.capacity}명</li>"
        if course.capacity
        else ""
    )
    control = ""
    if course.partition == "regular" and course.source_status == "접수중":
        control_identity = (
            "OES_9999999999999999"
            if mode == "application_mismatch"
            else course.identity
        )
        query = (
            f"contentUid={haenam.HAENAM_FOUNDATION_APPLY_UID}&"
            f"oesSubjectId={control_identity}&oecId={course.cohort}&"
            f"isAccept={course.accept}&isAddAccept=N"
        )
        control = (
            '<a class="btns theme" href="javascript:void(0)" '
            "onclick=\"alert('로그인 후 이용이 가능합니다.');"
            f"loginReturnUrlMain('{query}')\">신청</a>"
        )
    return f"""
      <html><head><title>해남군교육재단 &gt; 상세정보</title></head><body>
        <div class="shopViewList"><ul class="b_dot1">
          <li class="txt_line"><span>교육명</span>{escape(title)}</li>
          <li class="txt_line"><span>년도</span>2026</li>
          <li class="txt_line"><span>교육기간</span>{course.event_start} ~ {course.event_end}</li>
          <li class="txt_line"><span>접수기간</span>{course.apply_start} ~ {course.apply_end}</li>
          <li class="txt_line"><span>교육장소</span>{escape(venue)}</li>
          <li class="txt_line"><span>교육기관</span>해남군교육재단</li>
          {capacity}
          <li class="txt_line"><span>수강료</span>무료</li>
          <li class="txt_line"><span>재료비</span>무료</li>
          <li class="txt_line"><span>접수방법</span>{course.method}</li>
          <li class="txt_line"><span>문의</span>061-537-7809</li>
        </ul></div>{control}
        <div id="detail-info">강사 홍길동, discarded free body</div>
      </body></html>
    """


class FoundationRequester:
    def __init__(self, *, mode: str = "normal") -> None:
        self.courses = _foundation_courses()
        self.mode = mode
        self.calls: list[tuple[str, str, Mapping[str, str] | None]] = []
        self.counts: dict[tuple[str, str], int] = {}

    def __call__(
        self,
        _session: Any,
        method: str,
        url: str,
        _timeout: int,
        data: Mapping[str, str] | None,
    ) -> Response:
        copied = dict(data) if data is not None else None
        self.calls.append((method, url, copied))
        key = (method, f"{url}|{copied.get('nowPage') if copied else ''}")
        self.counts[key] = self.counts.get(key, 0) + 1
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if method == "GET" and query.get("contentUid") == [
            haenam.HAENAM_FOUNDATION_LIST_UID
        ]:
            return Response(
                _foundation_list_html(self.courses, "1", 1, mode=self.mode),
                url,
                content_type="text/html;charset=utf-8",
            )
        if method == "POST" and url == haenam.HAENAM_FOUNDATION_POST_URL:
            assert copied is not None
            page = int(copied["nowPage"])
            return Response(
                _foundation_list_html(
                    self.courses,
                    "2",
                    page,
                    mode=self.mode,
                    recheck=self.counts[key] > 1,
                ),
                url,
                content_type="text/html;charset=utf-8",
            )
        if method == "GET" and query.get("contentUid") == [
            haenam.HAENAM_FOUNDATION_DETAIL_UID
        ]:
            identity = query["oesSubjectId"][0]
            course = next(item for item in self.courses if item.identity == identity)
            return Response(
                _foundation_detail_html(course, mode=self.mode),
                url,
                content_type="text/html;charset=utf-8",
            )
        raise AssertionError(f"unexpected application/external request: {method} {url}")


def _collect_foundation(
    *,
    mode: str = "normal",
    detail_limit: int = 100,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], FoundationRequester]:
    requester = FoundationRequester(mode=mode)
    rows, parser, meta = haenam.collect_haenam_foundation_education(
        FOUNDATION_TARGET,
        cutoff=date(2026, 7, 21),
        detail_limit=detail_limit,
        session_factory=DummySession,
        requester=requester,
    )
    return rows, parser, meta, requester


def test_foundation_full_walk_details_branches_and_controls() -> None:
    rows, parser, meta, requester = _collect_foundation()

    assert parser == haenam.HAENAM_FOUNDATION_PARSER
    assert len(rows) == 42
    assert meta["pagination_complete"] is True
    assert meta["source_rows"] == 83
    assert meta["regular_rows"] == 36
    assert meta["nonregular_page_counts"] == {1: 12, 2: 12, 3: 12, 4: 11}
    assert meta["empty_sentinel_page"] == 5
    assert meta["source_status_counts"] == {"접수중": 38, "접수마감": 45}
    assert meta["detail_verified"] == 42
    assert meta["identity_bound_application_controls"] == 36
    assert meta["branch_counts"] == {
        "해남군평생학습관": 36,
        "해남군교육재단": 3,
        "해남군 관내": 2,
        "미래행복평생교육원": 1,
    }
    assert all(row["category"] == "교육" for row in rows)
    assert all(row["municipality_code"] == "1279000000" for row in rows)
    assert all(row["target"] == "대상 별도 안내" for row in rows)
    assert all(row["schedule_raw"] == "시간 별도 안내" for row in rows)
    assert all(
        row["raw_fields"]["target_evidence"]
        == "official_structured_detail_field_absent"
        for row in rows
    )
    assert all(
        row["raw_fields"]["schedule_evidence"]
        == "official_structured_detail_field_absent"
        for row in rows
    )
    assert sum(row["venue_name"] == "장소 별도 안내" for row in rows) == 1
    assert not any(row["venue_name"] == "-" for row in rows)
    assert not any(row["venue_name"] == "해남군관내" for row in rows)
    dash_venue = next(
        row for row in rows if row["venue_name"] == "장소 별도 안내"
    )
    assert (
        dash_venue["raw_fields"]["venue_evidence"]
        == "official_structured_detail_dash"
    )
    assert sum(bool(row["application_url"]) for row in rows) == 36
    assert not any(
        haenam.HAENAM_FOUNDATION_APPLY_UID in url
        for _method, url, _data in requester.calls
    )
    assert not any("061-537-7809" in repr(row) for row in rows)


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("sentinel_unstable", "stability"),
        ("duplicate", "duplicate"),
        ("detail_mismatch", "title mismatch"),
        ("pii_venue", "unsafe venue"),
        ("application_mismatch", "application identity"),
    ],
)
def test_foundation_contract_changes_fail_closed(mode: str, expected: str) -> None:
    rows, _parser, meta, _requester = _collect_foundation(mode=mode)

    assert rows == []
    assert meta["pagination_complete"] is False
    assert expected in meta["configured_collection_error"]


def test_foundation_detail_limit_never_returns_partial_snapshot() -> None:
    rows, _parser, meta, requester = _collect_foundation(detail_limit=41)

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "partial" in meta["configured_collection_error"]
    assert not any(
        parse_qs(urlparse(url).query).get("contentUid")
        == [haenam.HAENAM_FOUNDATION_DETAIL_UID]
        for method, url, _data in requester.calls
        if method == "GET"
    )


@dataclass(frozen=True)
class LibraryCourse:
    identity: str
    category: str
    title: str
    page: int
    venue: str
    control: str
    current: int
    capacity: int
    waiting: int
    is_education: bool = True

    @property
    def detail_url(self) -> str:
        return haenam.haenam_library_detail_url(self.identity, self.page)


def _library_courses() -> list[LibraryCourse]:
    courses = [
        LibraryCourse(
            "1041",
            "독서교실",
            "2026년 여름독서교실",
            1,
            "해남군립도서관 2층 세미나실",
            "신청하기",
            17,
            20,
            5,
        ),
        LibraryCourse(
            "1022",
            "2026 길위의 인문학",
            "서양 역사 내의 공화정",
            1,
            "",
            "신청하기",
            28,
            30,
            10,
        ),
    ]
    for index in range(18):
        control = (
            "대기자신청"
            if index < 7
            else "마감"
            if index == 17
            else "신청하기"
        )
        capacity = 12
        current = 13 + index if index < 7 else index + 1
        courses.append(
            LibraryCourse(
                str(1023 + index),
                "문화강좌",
                f"여름 문화강좌 {index + 1}",
                1,
                "해남문화예술회관 2층 문화활동실",
                control,
                current,
                capacity,
                20,
            )
        )
    courses.append(
        LibraryCourse(
            "991",
            "도서장기대여",
            "다중이용시설 도서대여서비스",
            2,
            "해남군립도서관",
            "신청하기",
            2,
            6,
            0,
            is_education=False,
        )
    )
    courses.append(
        LibraryCourse(
            "1049",
            "전집대출",
            "이야기 마법사 전집대출",
            2,
            "해남군립도서관",
            "신청전",
            0,
            1,
            0,
            is_education=False,
        )
    )
    return courses


def _library_card(course: LibraryCourse, *, identity: str | None = None) -> str:
    source_identity = identity or course.identity
    detail_url = haenam.haenam_library_detail_url(source_identity, course.page)
    if course.control == "마감":
        application_url = "#"
        application_class = "Prostate03"
    elif course.control == "신청전":
        application_url = "#"
        application_class = "Prostate01"
    else:
        application_url = haenam.haenam_library_application_url(source_identity)
        application_class = "Prostate02"
    return f"""
      <dl><dt><span class="Boxsection01">{course.category}</span>
        <p class="online_tit"><a href="{escape(detail_url, quote=True)}">{escape(course.title)}</a></p>
        <p class="online_sub MAT10"><span>운영기간</span>2026-07-25 ~ 2026-08-21</p>
        <p class="online_sub"><span>운영시간</span>화,목 10:00 ~ 11:30</p>
        <p class="online_sub"><span>대<font>상</font></span>해남군민</p>
        <p class="online_sub"><span>접수기간</span>2026-07-16 ~ 2026-07-22</p>
      </dt><dd><p><span>신청 {course.current} /</span><span> 정원 {course.capacity}</span></p>
        <a class="{application_class}" href="{escape(application_url, quote=True)}">{course.control}</a>
        <a class="Tplanbtn" href="/private-plan-{source_identity}.hwp">강의계획서</a>
      </dd></dl>
    """


def _library_list_html(
    courses: list[LibraryCourse],
    page: int,
    *,
    mode: str = "normal",
    recheck: bool = False,
) -> str:
    visible = [item for item in courses if item.page == page]
    if mode == "sentinel_unstable" and page == 3 and recheck:
        visible = [replace(courses[-1], page=3)]
    cards: list[str] = []
    for index, course in enumerate(visible):
        duplicate = None
        if mode == "duplicate" and page == 2 and index == 0:
            duplicate = courses[0].identity
        cards.append(_library_card(course, identity=duplicate))
    pager = "".join(
        (
            '<a href="https://lib.haenam.go.kr/main/sub.php?'
            f'mno=43&amp;page={number}&amp;key=all&amp;searchword=">{number}</a>'
        )
        for number in (1, 2)
    )
    return f"""
      <html><head><meta charset="euc-kr"><title>해남군립도서관 - 프로그램신청</title></head>
      <body><div>게시물 : {len(courses)}개</div><div class="boardlist">
        <div class="ProgramList">{''.join(cards)}</div></div>
        <div class="paging">{pager}</div></body></html>
    """


def _library_detail_bytes(course: LibraryCourse, *, mode: str = "normal") -> bytes:
    title = course.title + " 변경" if mode == "detail_mismatch" else course.title
    target = "담당자 061-123-4567" if mode == "pii_target" else "해남군민"
    application_identity = "9999" if mode == "application_mismatch" else course.identity
    application_url = (
        "#"
        if course.control == "마감"
        else haenam.haenam_library_application_url(application_identity)
    )
    application_class = (
        "Prostate03" if course.control == "마감" else "Prostate02"
    )
    prefix = f"""
      <html><head><meta charset="euc-kr"><title>해남군립도서관 - 프로그램신청</title></head>
      <body><div class="boardlist_p"><div class="bviewlist">
        <div class="Progdetails_tit"><span>{course.category}</span>{escape(title)}</div>
        <ul class="Progdetails_list">
          <li><dl><dt>대상</dt><dd>{escape(target)}</dd></dl></li>
          <li><dl><dt>운영기간</dt><dd>2026-07-25 ~ 2026-08-21</dd></dl></li>
          <li><dl><dt>운영시간</dt><dd>화,목요일 10:00 ~ 11:30</dd></dl></li>
          <li><dl><dt>운영장소</dt><dd>{escape(course.venue)}</dd></dl></li>
          <li><dl><dt>접수기간</dt><dd>2026-07-16 ~ 2026-07-22</dd></dl></li>
          <li><dl><dt>강사명</dt><dd>홍길동</dd></dl></li>
          <li><dl><dt>재료비</dt><dd>없음</dd></dl></li>
          <li><dl><dt>수강인원</dt><dd>{course.current}/{course.capacity}/{course.waiting}(총{course.capacity + course.waiting}) 신청수/정원/대기자(총정원)</dd></dl></li>
          <li><dl><dt>강의계획서</dt><dd><a href="/discarded-plan.hwp">다운로드</a></dd></dl></li>
        </ul></div>
        <div class="pagelist"><a class="{application_class}" href="{escape(application_url, quote=True)}">{course.control}</a></div>
      """
    if mode == "privacy_marker_missing":
        return (prefix + "</div></body></html>").encode("cp949")
    # The invalid byte and PII after the marker prove that the collector cuts
    # the response before decoding or parsing the applicant/free-body section.
    suffix = (
        '<div class="Progdetails_report_tit MAB20">신청자 명단</div>'
        "<table><tr><td>김*민</td><td>010-****-1234</td></tr></table>"
        "</div></body></html>"
    ).encode("cp949")
    return prefix.encode("cp949") + suffix + b"\xff"


class LibraryRequester:
    def __init__(self, *, mode: str = "normal") -> None:
        self.courses = _library_courses()
        self.mode = mode
        self.calls: list[tuple[str, str]] = []
        self.counts: dict[str, int] = {}

    def __call__(
        self,
        _session: Any,
        method: str,
        url: str,
        _timeout: int,
        data: Mapping[str, str] | None,
    ) -> Response:
        assert method == "GET"
        assert data is None
        self.calls.append((method, url))
        self.counts[url] = self.counts.get(url, 0) + 1
        query = parse_qs(urlparse(url).query, keep_blank_values=True)
        if query.get("mode") == ["read"]:
            identity = query["no"][0]
            assert identity not in {
                "991",
                "1049",
            }, "non-education service detail must not be fetched"
            course = next(item for item in self.courses if item.identity == identity)
            return Response(
                _library_detail_bytes(course, mode=self.mode),
                url,
                content_type="text/html",
            )
        page = int((query.get("page") or ["1"])[0])
        return Response(
            _library_list_html(
                self.courses,
                page,
                mode=self.mode,
                recheck=self.counts[url] > 1,
            ),
            url,
            encoding="cp949",
            content_type="text/html",
        )


def _collect_library(
    *,
    mode: str = "normal",
    detail_limit: int = 100,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], LibraryRequester]:
    requester = LibraryRequester(mode=mode)
    rows, parser, meta = haenam.collect_haenam_county_library_education(
        LIBRARY_TARGET,
        cutoff=date(2026, 7, 21),
        detail_limit=detail_limit,
        session_factory=DummySession,
        requester=requester,
    )
    return rows, parser, meta, requester


def test_county_library_full_walk_privacy_boundary_and_owner_split() -> None:
    rows, parser, meta, requester = _collect_library()

    assert parser == haenam.HAENAM_LIBRARY_PARSER
    assert len(rows) == 20
    assert meta["pagination_complete"] is True
    assert meta["source_total"] == 22
    assert meta["page_counts"] == {1: 20, 2: 2}
    assert meta["empty_sentinel_page"] == 3
    assert meta["education_source_count"] == 20
    assert meta["excluded_non_education_identities"] == ["991", "1049"]
    assert meta["privacy_cuts_verified"] == 20
    assert meta["identity_bound_application_controls"] == 19
    assert meta["branch_counts"] == {
        "해남문화예술회관": 18,
        "해남군립도서관": 2,
    }
    assert {row["program_type"] for row in rows} == {
        "문화강좌",
        "독서교실",
        "2026 길위의 인문학",
    }
    assert all(row["raw_fields"]["privacy_cut_applied"] for row in rows)
    closed = next(row for row in rows if row["raw_fields"]["source_application_control"] == "마감")
    assert closed["status"] == "CLOSED"
    assert closed["application_url"] == ""
    assert all(row["fee"] == "요금 별도 안내" for row in rows)
    assert all(row["venue_name"] for row in rows)
    assert not any("010-****-1234" in repr(row) for row in rows)
    assert not any(
        parse_qs(urlparse(url).query, keep_blank_values=True).get("mode")
        == ["write"]
        for _method, url in requester.calls
    )


def test_county_library_excludes_book_set_loans_as_non_education() -> None:
    assert haenam._library_category("전집대출", "1049") == ("전집대출", False)


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("sentinel_unstable", "stability"),
        ("duplicate", "duplicate"),
        ("detail_mismatch", "category/title mismatch"),
        ("pii_target", "unsafe target"),
        ("application_mismatch", "application"),
        ("privacy_marker_missing", "privacy boundary"),
    ],
)
def test_county_library_contract_changes_fail_closed(
    mode: str,
    expected: str,
) -> None:
    rows, _parser, meta, _requester = _collect_library(mode=mode)

    assert rows == []
    assert meta["pagination_complete"] is False
    assert expected in meta["configured_collection_error"]


def test_county_library_detail_limit_excludes_service_without_partial_output() -> None:
    rows, _parser, meta, requester = _collect_library(detail_limit=19)

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "partial" in meta["configured_collection_error"]
    assert not any(
        parse_qs(urlparse(url).query, keep_blank_values=True).get("mode")
        == ["read"]
        for _method, url in requester.calls
    )


def test_owner_boundaries_audit_and_exact_target_matching() -> None:
    assert haenam.is_haenam_education_target(FOUNDATION_TARGET)
    assert haenam.is_haenam_education_target(LIBRARY_TARGET)
    assert not haenam.is_haenam_education_target(
        {
            "provider": haenam.HAENAM_JNE_PROVIDER,
            "url": haenam.HAENAM_JNE_URL,
        }
    )
    assert haenam.HAENAM_MUNICIPALITY_CODE == "1279000000"
    assert (
        haenam.HAENAM_OWNER_BOUNDARY_AUDIT[haenam.HAENAM_JNE_PROVIDER][
            "decision"
        ]
        == "keep_separate_education_office_library_owner"
    )
    assert (
        haenam.HAENAM_OWNER_BOUNDARY_AUDIT[
            haenam.HAENAM_FOUNDATION_PROVIDER
        ]["replaces"]
        == haenam.HAENAM_FOUNDATION_OLD_PROVIDER
    )
    assert (
        haenam.HAENAM_NO_LEDGER_AUDIT["resident_community_notice"][
            "decision"
        ]
        == "exclude_notice_and_hwpx_attachment_only"
    )
    assert haenam.HAENAM_JNE_DISCOVERY_AUDIT["source_total"] == 136
    assert haenam.HAENAM_LIBRARY_DISCOVERY_AUDIT[
        "excluded_non_education_service"
    ]["identity"] == "991"
    assert haenam.haenam_foundation_detail_url(
        "OES_0000000000000266",
        "OEC_0000000000000050",
        "ALL",
        "C",
        "N",
    )
    assert haenam.haenam_foundation_detail_url(
        "OES_0000000000000037",
        "OEC_0000000000000003",
        "DMCCTZUNV",
        "C",
        "C",
    )


def test_foundation_tls_intermediate_is_fingerprint_checked() -> None:
    context = haenam._tls_context()  # noqa: SLF001 - transport boundary itself.

    assert context.verify_mode.name == "CERT_REQUIRED"
    assert context.check_hostname is True


@pytest.mark.skipif(
    os.getenv("HAENAM_EDUCATION_LIVE") != "1",
    reason="set HAENAM_EDUCATION_LIVE=1 for official-source verification",
)
def test_live_foundation_snapshot() -> None:
    rows, _parser, meta = haenam.collect_haenam_foundation_education(
        FOUNDATION_TARGET,
        cutoff=date(2026, 7, 21),
        timeout=40,
    )

    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == 83
    assert meta["current_source_count"] == 42
    assert meta["detail_verified"] == 42
    assert len(rows) == 42


@pytest.mark.skipif(
    os.getenv("HAENAM_EDUCATION_LIVE") != "1",
    reason="set HAENAM_EDUCATION_LIVE=1 for official-source verification",
)
def test_live_county_library_snapshot() -> None:
    rows, _parser, meta = haenam.collect_haenam_county_library_education(
        LIBRARY_TARGET,
        cutoff=date(2026, 7, 21),
        timeout=40,
    )

    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == 21
    assert meta["education_source_count"] == 20
    assert meta["privacy_cuts_verified"] == 20
    assert len(rows) == 20
