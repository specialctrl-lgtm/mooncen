from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_jeungpyeong as jp


@dataclass(frozen=True)
class Target:
    provider: str
    url: str


@dataclass(frozen=True)
class LifelongCourse:
    identity: str
    sequence: int
    title: str
    status: str
    period: str
    schedule: str
    current: int
    online: int
    waiting: int
    maximum: int
    methods: str
    venue: str
    target: str = "증평군민"
    category: str = "인문/사회"
    apply_period: str = "2099-07-01 09:00 ~ 2099-07-31 18:00"


@dataclass(frozen=True)
class LibraryCourse:
    sequence: int
    lg_code: str
    le_code: str
    title: str
    target: str
    maximum: int
    online: int
    waitlist: int
    apply_period: str
    period: str
    venue: str
    is_open: bool
    schedule: str = "수요일 10:00~12:00"


class Response:
    def __init__(self, url: str, body: str, status_code: int = 200) -> None:
        self.url = url
        self.content = body.encode("utf-8")
        self.status_code = status_code
        self.headers: dict[str, str] = {"Content-Type": "text/html;charset=UTF-8"}


class DummySession:
    def close(self) -> None:
        return None


def _lifelong_courses() -> dict[str, list[LifelongCourse]]:
    return {
        "regular": [
            LifelongCourse(
                "102",
                2,
                "현재 온라인 정규 강좌",
                "모집중",
                "2099-08-01 ~ 2099-08-31",
                "수요일 10:00 ~ 12:00",
                3,
                10,
                0,
                10,
                "온라인(10)",
                "증평군립도서관 1층",
            ),
            LifelongCourse(
                "101",
                1,
                "지난 정규 강좌",
                "교육종료",
                "2000-01-01 ~ 2000-01-31",
                "월요일 10:00 ~ 12:00",
                10,
                10,
                0,
                10,
                "온라인(10)",
                "창의파크",
            ),
        ],
        "special": [
            LifelongCourse(
                "202",
                2,
                "현재 혼합 특성화 강좌",
                "교육중",
                "2099-07-01 ~ 2099-08-15",
                "목요일 19:00 ~ 21:00",
                9,
                8,
                0,
                10,
                "온라인(8), 전화(2)",
                "가족센터",
            ),
            LifelongCourse(
                "201",
                1,
                "지난 특성화 강좌",
                "교육종료",
                "2000-02-01 ~ 2000-02-28",
                "금요일 10:00 ~ 12:00",
                8,
                8,
                0,
                8,
                "온라인(8)",
                "가족센터",
            ),
        ],
        "outreach": [
            LifelongCourse(
                "301",
                1,
                "현재 찾아가는 교육",
                "모집중",
                "2099-09-01 ~ 2099-09-30",
                "화요일 14:00 ~ 16:00",
                1,
                5,
                1,
                5,
                "온라인(5)",
                "도안문화센터",
            )
        ],
    }


def _lifelong_table(courses: list[LifelongCourse], *, page: int) -> str:
    headings = "".join(f"<th>{escape(value)}</th>" for value in jp._LIFELONG_HEADINGS)
    if page == 1:
        rows = "".join(
            f"""
              <tr onclick="fn_search_detail({course.identity}); return false;">
                <td>{course.sequence}</td><td>{escape(course.title)}</td>
                <td>{course.period}</td><td><li>{escape(course.schedule)}</li></td>
                <td>{course.current} / {course.online} / {course.waiting}</td>
                <td><button>{course.status}</button></td>
              </tr>
            """
            for course in courses
        )
    else:
        rows = "<tr><td class='center t_end' colspan='6'>내용이 존재 하지 않습니다.</td></tr>"
    return f"""
      <html><body><div class="pageInfo">총 게시물 {len(courses)} 개, 페이지 {page} /1</div>
      <table class="tbl_basic coursetbl"><thead><tr>{headings}</tr></thead>
      <tbody>{rows}</tbody></table></body></html>
    """


def _lifelong_detail(
    catalogue: str,
    course: LifelongCourse,
    *,
    wrong_identity: bool = False,
    missing_application: bool = False,
    pii_target: bool = False,
) -> str:
    path = jp.JEUNGPYEONG_LIFELONG_CATALOGUES[catalogue][1]
    application_identity = "9999" if wrong_identity else course.identity
    application = ""
    if course.status == "모집중" and not missing_application:
        application = (
            "<button onclick='fn_search_regist(); return false;'>신청하기</button>"
        )
    target = "person@example.kr" if pii_target else course.target
    return f"""
      <html><body><div class="eduView"><div class="topBox">
        <div class="thumb"><div class="cate">{course.status}<span>선착순</span></div></div>
        <div class="info"><strong><span>{course.category}</span>{escape(course.title)}</strong>
          <ul>
            <li><span>교육기간</span>{course.period}</li>
            <li><span>교육시간</span><ul><li>{escape(course.schedule)}</li></ul></li>
            <li><span>접수기간</span>{course.apply_period}</li>
            <li><span>모집방법</span>{course.methods}</li>
            <li><span>모집인원</span>{course.maximum}</li>
            <li><span>강사명</span>홍길동</li><li><span>강의계획서</span></li>
          </ul></div></div>
        <table class="tbl_basic"><tbody>
          <tr><th>학기구분</th><td colspan="3">2099 프로그램</td></tr>
          <tr><th>교육대상</th><td>{target}</td><th>교육장소</th><td>{course.venue}</td></tr>
          <tr><th>수강료</th><td>무료</td><th>재료비 또는 기타비용</th><td>없음</td></tr>
          <tr><th>주관부서 담당자</th><td>담당자</td><th>문의전화</th><td>043-000-0000</td></tr>
        </tbody></table></div>
        <div class="btnbox"><button onclick="fn_search_list(); return false;">목록</button>{application}</div>
        <script>function fn_search_regist() {{ form.action =
          "/prog/aplcnt/lll/{path}/write.do?courseNo={application_identity}"; }}</script>
      </body></html>
    """


def _library_courses() -> list[LibraryCourse]:
    return [
        LibraryCourse(
            3,
            "1",
            "1003",
            "[독서문화행사] 미래 코딩",
            "초등학생",
            10,
            10,
            4,
            "2099.07.01 09:00 ~ 2099.07.15 18:00",
            "2099.08.01 ~ 2099.08.05",
            "증평군립도서관 3층 프로그램2실",
            False,
        ),
        LibraryCourse(
            2,
            "4",
            "1002",
            "[방학프로그램] 미래 독서교실",
            "초등 1~3학년",
            12,
            12,
            5,
            "2099.07.01 09:00 ~ 2099.07.31 18:00",
            "2099.08.10 ~ 2099.08.20",
            "증평군립도서관 평생학습3실",
            True,
        ),
        LibraryCourse(
            1,
            "12",
            "1001",
            "[기타 프로그램] 지난 강좌",
            "성인",
            15,
            15,
            0,
            "2000.01.01 09:00 ~ 2000.01.10 18:00",
            "2000.02.01 ~ 2000.02.28",
            "창의파크",
            False,
        ),
    ]


def _library_link(course: LibraryCourse, act: str, *, wrong: bool = False) -> str:
    le_code = "9999" if wrong else course.le_code
    return (
        "./index.php?g_page=culture&amp;m_page=culture01&amp;"
        f"act={act}&amp;lgCode={course.lg_code}&amp;leCode={le_code}&amp;cate="
    )


def _library_row(course: LibraryCourse) -> str:
    receive = (
        f"<a href='{_library_link(course, 'lecture_receive_form')}'>신청하기</a>"
        if course.is_open
        else "접수마감"
    )
    result = f"<a href='{_library_link(course, 'lecture_result_view')}'>접수확인</a>"
    category, title = course.title.split("]", 1)
    category += "]"
    apply_start, apply_end = course.apply_period.split(" ~ ")
    start_date, start_time = apply_start.rsplit(" ", 1)
    end_date, end_time = apply_end.rsplit(" ", 1)
    return f"""
      <tr><td class="td_num">{course.sequence}</td><td>전체</td><td class="tal">
        <a href="{_library_link(course, 'lecture_view')}">{category}<br><strong>{escape(title.strip())}</strong></a>
      </td><td>{escape(course.target)}<br>{course.maximum} / {course.online} / {course.waitlist}</td>
      <td>{start_date} / {start_time}<br>~ {end_date} / {end_time}</td>
      <td>{receive}{result}</td></tr>
    """


def _library_page(courses: list[LibraryCourse], *, page: int) -> str:
    headings = "".join(f"<th>{escape(value)}</th>" for value in jp._LIBRARY_HEADINGS)
    if page == 1:
        rows = "".join(_library_row(course) for course in courses[:2])
        paging = "<strong>1</strong><a class='num' href='?page=2'>2</a>"
    elif page == 2:
        rows = _library_row(courses[2])
        paging = "<a class='num' href='?page=1'>1</a><strong>2</strong>"
    else:
        rows = ""
        paging = "<a class='num' href='?page=1'>1</a><a class='num' href='?page=2'>2</a>"
    return f"""
      <html><body><table class="tstyle"><thead><tr>{headings}</tr></thead>
      <tbody>{rows}</tbody></table><div class="paging">{paging}</div></body></html>
    """


def _library_detail(
    course: LibraryCourse,
    *,
    wrong_title: bool = False,
    wrong_application: bool = False,
    missing_application: bool = False,
    pii_target: bool = False,
) -> str:
    title = "[기타 프로그램] 다른 강좌" if wrong_title else course.title
    application = ""
    if course.is_open and not missing_application:
        application = (
            f"<a href='{_library_link(course, 'lecture_receive_form', wrong=wrong_application)}'>"
            "신청하기</a>"
        )
    target = "person@example.kr" if pii_target else course.target
    # The live detail omits the leading zero from single-digit hours.
    detail_apply_period = course.apply_period.replace(" 09:00", " 9:00")
    return f"""
      <html><body><div class="tit"><h2>{escape(title)}</h2></div>
        <table class="tstyle"><tbody>
          <tr><th>대상</th><td>{escape(target)}</td><th>강사명</th><td>홍길동</td></tr>
          <tr><th>정원</th><td>{course.maximum} 명</td><th>현재 접수인원</th><td>2 명</td></tr>
          <tr><th>대상인원</th><td>{course.online} 명</td><th>대기인원</th><td>{course.waitlist} 명</td></tr>
          <tr><th>수강료</th><td>0 원</td></tr>
        </tbody></table>
        <ul class="con03"><li>접수 기간 : <strong>{detail_apply_period}</strong></li>
          <li>강좌 기간 : <span>{course.period}</span></li>
          <li>강좌 일시 : {escape(course.schedule)}</li>
          <li>강좌 장소 : {escape(course.venue)}</li><li>수업계획안 : 다운로드</li></ul>
        <div class="chart">담당자 043-000-0000</div>{application}
      </body></html>
    """


class Source:
    def __init__(self) -> None:
        self.lifelong = _lifelong_courses()
        self.library = _library_courses()
        self.calls: list[str] = []
        self.lock = Lock()
        self.mode = ""

    def __call__(self, _session: Any, url: str, _timeout: int) -> Response:
        with self.lock:
            self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if "/prog/aplcnt/" in parsed.path:
            raise AssertionError("applicant form must never be requested")
        if parsed.hostname == jp.JEUNGPYEONG_LIFELONG_HOST:
            catalogue = next(
                key
                for key, (_label, path) in jp.JEUNGPYEONG_LIFELONG_CATALOGUES.items()
                if f"/{path}/" in parsed.path
            )
            if parsed.path.endswith("/list.do"):
                page = int(query.get("pageIndex", ["1"])[0])
                if self.mode == "lifelong_nonempty_sentinel" and catalogue == "regular" and page == 2:
                    body = _lifelong_table(self.lifelong[catalogue], page=1).replace(
                        "페이지 1 /1", "페이지 2 /1"
                    )
                    return Response(url, body)
                return Response(url, _lifelong_table(self.lifelong[catalogue], page=page))
            identity = query["courseNo"][0]
            course = next(item for item in self.lifelong[catalogue] if item.identity == identity)
            return Response(
                url,
                _lifelong_detail(
                    catalogue,
                    course,
                    wrong_identity=(self.mode == "lifelong_wrong_identity" and identity == "102"),
                    missing_application=(self.mode == "lifelong_missing_application" and identity == "102"),
                    pii_target=(self.mode == "lifelong_pii" and identity == "102"),
                ),
            )

        if parsed.hostname != jp.JEUNGPYEONG_LIBRARY_HOST:
            raise AssertionError(f"unexpected host: {url}")
        act = query.get("act", [""])[0]
        if act in {"lecture_receive_form", "lecture_result_view", "lecture_down"}:
            raise AssertionError("private/application endpoint must never be requested")
        if act == "lecture_view":
            identity = query["leCode"][0]
            course = next(item for item in self.library if item.le_code == identity)
            return Response(
                url,
                _library_detail(
                    course,
                    wrong_title=(self.mode == "library_title_drift" and identity == "1002"),
                    wrong_application=(self.mode == "library_wrong_application" and identity == "1002"),
                    missing_application=(self.mode == "library_missing_application" and identity == "1002"),
                    pii_target=(self.mode == "library_pii" and identity == "1002"),
                ),
            )
        page = int(query.get("page", ["1"])[0])
        body = _library_page(self.library, page=page)
        if self.mode == "library_nonempty_sentinel" and page == 3:
            body = body.replace("<tbody></tbody>", f"<tbody>{_library_row(self.library[2])}</tbody>")
        return Response(url, body)


def _lifelong_target(**changes: str) -> Target:
    values = {
        "provider": jp.JEUNGPYEONG_LIFELONG_PROVIDER,
        "url": jp.JEUNGPYEONG_LIFELONG_CANONICAL_URL,
    }
    values.update(changes)
    return Target(**values)


def _library_target(**changes: str) -> Target:
    values = {
        "provider": jp.JEUNGPYEONG_LIBRARY_PROVIDER,
        "url": jp.JEUNGPYEONG_LIBRARY_CANONICAL_URL,
    }
    values.update(changes)
    return Target(**values)


def _collect_lifelong(source: Source, **changes: Any):
    options = {
        "today": "2099-07-22",
        "timeout": 5,
        "max_pages": 10,
        "detail_limit": 10,
        "max_workers": 3,
        "session_factory": DummySession,
        "fetcher": source,
    }
    options.update(changes)
    return jp.collect(_lifelong_target(), **options)


def _collect_library(
    source: Source, monkeypatch: pytest.MonkeyPatch, **changes: Any
):
    monkeypatch.setattr(jp, "JEUNGPYEONG_LIBRARY_PAGE_SIZE", 2)
    options = {
        "today": "2099-07-22",
        "timeout": 5,
        "max_pages": 10,
        "detail_limit": 10,
        "max_workers": 3,
        "session_factory": DummySession,
        "fetcher": source,
    }
    options.update(changes)
    return jp.collect(_library_target(), **options)


@pytest.mark.parametrize(
    "target",
    [
        _lifelong_target(url=jp.JEUNGPYEONG_LIFELONG_LANDING_URL),
        _lifelong_target(url=jp.JEUNGPYEONG_LIFELONG_CANONICAL_URL + "?pageIndex=1"),
        _lifelong_target(url=jp.JEUNGPYEONG_LIFELONG_CANONICAL_URL + "#courses"),
        _lifelong_target(url=jp.JEUNGPYEONG_LIFELONG_CANONICAL_URL.replace("https:", "http:")),
        _lifelong_target(provider="MUNI_WRONG"),
        _library_target(url=jp.JEUNGPYEONG_LIBRARY_CANONICAL_URL + "&page=1"),
        _library_target(url=jp.JEUNGPYEONG_LIBRARY_CANONICAL_URL + "#programmes"),
        _library_target(url=jp.JEUNGPYEONG_LIBRARY_CANONICAL_URL.replace("https:", "http:")),
        _library_target(provider="MUNI_WRONG"),
        Target(jp.JEUNGPYEONG_YOUTH_PROVIDER, jp.JEUNGPYEONG_YOUTH_URL),
    ],
)
def test_exact_owner_matcher_rejects_aliases_queries_and_http(target: Target) -> None:
    assert not jp.is_target(target)


def test_audited_owner_identities_and_url_builders_are_stable() -> None:
    assert jp.is_jeungpyeong_lifelong_target(_lifelong_target())
    assert jp.is_jeungpyeong_library_target(_library_target())
    assert jp.JEUNGPYEONG_LIFELONG_PROVIDER == "MUNI_WWW_JP_GO_KR_44B42971"
    assert jp.JEUNGPYEONG_LIFELONG_CANDIDATE_ID == "MUNI_IR_2028A1584014"
    assert jp.JEUNGPYEONG_LIBRARY_PROVIDER == "MUNI_LIB_JP_GO_KR_57C5EEED"
    assert jp.JEUNGPYEONG_LIBRARY_CANDIDATE_ID == "MUNI_IR_00E5B1C95302"
    assert jp.JEUNGPYEONG_YOUTH_CANDIDATE_ID == "MUNI_IR_A8AD3A380C1A"
    assert "pageIndex=2&pageUnit=200" in jp.jeungpyeong_lifelong_list_url(
        "special", 2
    )
    assert "courseNo=358" in jp.jeungpyeong_lifelong_detail_url("special", 358)
    assert "act=lecture_view" in jp.jeungpyeong_library_detail_url(12, 1399)
    with pytest.raises(ValueError):
        jp.jeungpyeong_lifelong_list_url("unknown", 1)
    with pytest.raises(ValueError):
        jp.jeungpyeong_library_detail_url("../12", 1399)


def test_lifelong_complete_three_catalogue_snapshot_and_exact_branches() -> None:
    source = Source()
    rows, parser, meta = _collect_lifelong(source)

    assert parser == jp.JEUNGPYEONG_LIFELONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["catalogue_counts"] == {"regular": 2, "special": 2, "outreach": 1}
    assert meta["source_total"] == meta["source_rows"] == 5
    assert meta["data_pages"] == 3
    assert meta["list_requests"] == meta["required_list_requests"] == 9
    assert meta["sentinel_requests"] == meta["boundary_rechecks"] == 3
    assert meta["current_candidate_count"] == meta["current_source_count"] == 3
    assert meta["expired_count"] == 2
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["status_counts"] == {"OPEN": 2, "CLOSED": 1}
    assert meta["application_control_count"] == 2
    assert meta["snapshot_complete"] is True
    assert {row["branch"] for row in rows} == {
        "증평군립도서관 1층",
        "가족센터",
        "도안문화센터",
    }
    assert next(row for row in rows if row["provider_course_id"].endswith(":202"))[
        "capacity_total"
    ] == 10
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["apply_start"] for row in rows)
    assert "043-000-0000" not in repr(rows)
    assert not any("/prog/aplcnt/" in url for url in source.calls)


def test_lifelong_recruitment_closed_status_and_empty_target_are_supported() -> None:
    source = Source()
    source.lifelong["special"][0] = replace(
        source.lifelong["special"][0],
        status="모집마감",
        target="",
    )

    rows, _parser, meta = _collect_lifelong(source)

    assert meta["configured_collection_error"] == ""
    closed = next(row for row in rows if row["provider_course_id"].endswith(":202"))
    assert closed["status"] == "CLOSED"
    assert closed["reservation_available"] is False
    assert closed["target"] == "대상 별도 안내"
    assert closed["raw_fields"]["source_target"] == ""


@pytest.mark.parametrize(
    "mode",
    [
        "lifelong_nonempty_sentinel",
        "lifelong_wrong_identity",
        "lifelong_missing_application",
        "lifelong_pii",
    ],
)
def test_lifelong_contract_drift_and_pii_fail_closed(mode: str) -> None:
    source = Source()
    source.mode = mode
    rows, _parser, meta = _collect_lifelong(source)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_lifelong_caps_and_dedupe_cardinality_fail_closed() -> None:
    rows, _parser, meta = _collect_lifelong(Source(), max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    rows, _parser, meta = _collect_lifelong(Source(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    rows, _parser, meta = _collect_lifelong(
        Source(), dedupe_rows=lambda values: values[:1]
    )
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta[
        "configured_collection_error"
    ]


def test_library_complete_pages_all_details_controls_and_exact_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source()
    rows, parser, meta = _collect_library(source, monkeypatch)

    assert parser == jp.JEUNGPYEONG_LIBRARY_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == meta["source_rows"] == 3
    assert meta["data_pages"] == 2
    assert meta["list_requests"] == meta["required_list_requests"] == 5
    assert meta["sentinel_requests"] == 1
    assert meta["boundary_rechecks"] == 2
    assert meta["detail_attempts"] == meta["detail_pages"] == 3
    assert meta["current_candidate_count"] == meta["current_source_count"] == 2
    assert meta["expired_count"] == 1
    assert meta["status_counts"] == {"CLOSED": 1, "OPEN": 1}
    assert meta["application_control_count"] == 1
    assert meta["snapshot_complete"] is True
    assert {row["branch"] for row in rows} == {
        "증평군립도서관 3층 프로그램2실",
        "증평군립도서관 평생학습3실",
    }
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert "act=lecture_receive_form" in open_row["application_url"]
    assert "lecture_result_view" not in repr(rows)
    assert "043-000-0000" not in repr(rows)
    assert not any(
        action in url
        for url in source.calls
        for action in ("lecture_receive_form", "lecture_result_view", "lecture_down")
    )


@pytest.mark.parametrize(
    "mode",
    [
        "library_nonempty_sentinel",
        "library_title_drift",
        "library_wrong_application",
        "library_missing_application",
        "library_pii",
    ],
)
def test_library_contract_drift_and_pii_fail_closed(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Source()
    source.mode = mode
    rows, _parser, meta = _collect_library(source, monkeypatch)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_library_caps_and_dedupe_cardinality_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, _parser, meta = _collect_library(Source(), monkeypatch, max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    rows, _parser, meta = _collect_library(Source(), monkeypatch, detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    rows, _parser, meta = _collect_library(
        Source(), monkeypatch, dedupe_rows=lambda values: values[:1]
    )
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta[
        "configured_collection_error"
    ]


def test_noncanonical_target_never_fetches() -> None:
    source = Source()
    rows, parser, meta = jp.collect(
        Target(jp.JEUNGPYEONG_YOUTH_PROVIDER, jp.JEUNGPYEONG_YOUTH_URL),
        fetcher=source,
        session_factory=DummySession,
    )
    assert rows == []
    assert parser == jp.JEUNGPYEONG_PARSER
    assert meta["configured_collection_error"]
    assert source.calls == []


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_TESTS") != "1",
    reason="set RUN_LIVE_MUNICIPAL_TESTS=1 for official live audit",
)
@pytest.mark.parametrize(
    "target",
    [
        Target(
            jp.JEUNGPYEONG_LIFELONG_PROVIDER,
            jp.JEUNGPYEONG_LIFELONG_CANONICAL_URL,
        ),
        Target(
            jp.JEUNGPYEONG_LIBRARY_PROVIDER,
            jp.JEUNGPYEONG_LIBRARY_CANONICAL_URL,
        ),
    ],
)
def test_live_jeungpyeong_snapshot_is_complete_or_atomically_empty(
    target: Target,
) -> None:
    rows, _parser, meta = jp.collect(
        target,
        today="2026-07-22",
        timeout=30,
        max_pages=100,
        detail_limit=500,
        max_workers=8,
    )
    assert meta["snapshot_complete"] is True, meta["configured_collection_error"]
    assert meta["returned_count"] == len(rows)
    assert meta["source_total"] >= len(rows)
    assert meta["forbidden_applicant_endpoint_requests"] == 0
