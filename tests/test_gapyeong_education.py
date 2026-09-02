from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from html import escape
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_gapyeong as gapyeong


@dataclass(frozen=True)
class Course:
    identity: str
    scope: str
    title: str
    source_status: str
    start: str
    end: str
    venue: str
    target: str = "가평군민"
    current: int = 1
    capacity: int = 10
    waiting: int = 0
    waiting_capacity: int = 5
    apply_start: str = "2026-07-01"
    apply_end: str = "2026-07-31"


class Response:
    def __init__(self, url: str, html: str, *, status_code: int = 200) -> None:
        self.url = url
        self.status_code = status_code
        self.headers: dict[str, str] = {"Content-Type": "text/html;charset=UTF-8"}
        self.content = html.encode("utf-8")


class DummySession:
    def close(self) -> None:
        return None


def _courses() -> list[Course]:
    return [
        Course(
            "5020",
            "MC",
            "설악 미래 독서교실",
            "접수중",
            "2026-08-01",
            "2026-09-30",
            "설악도서관 3층 다목적실",
            target="초등학생",
            current=4,
            capacity=15,
            waiting_capacity=10,
        ),
        Course(
            "5019",
            "MA",
            "한석봉 창작공예",
            "대기중",
            "2026-08-05",
            "2026-08-26",
            "한석봉도서관 3층 세미나실",
            target="성인",
            current=10,
            capacity=10,
            waiting=2,
        ),
        Course(
            "5018",
            "CO",
            "가평 공동 인문학",
            "접수마감",
            "2026-07-22",
            "2026-08-10",
            "청평도서관 3층 문화교실",
            current=10,
            capacity=10,
            waiting=5,
            waiting_capacity=5,
        ),
        Course("5017", "MD", "지난 청평교육 1", "행사종료", "2026-01-01", "2026-06-01", "청평도서관 문화교실"),
        Course("5016", "MB", "지난 조종교육 1", "행사종료", "2026-01-01", "2026-06-01", "조종도서관 문화교실"),
        Course("5015", "MC", "지난 설악교육 1", "행사종료", "2026-01-01", "2026-06-01", "설악도서관 다목적실"),
        Course("5014", "MA", "지난 한석봉교육", "행사종료", "2026-01-01", "2026-06-01", "한석봉도서관 세미나실"),
        Course("5013", "CO", "지난 공동교육", "행사종료", "2026-01-01", "2026-06-01", "설악도서관 다목적실"),
        Course("5012", "MD", "지난 청평교육 2", "행사종료", "2026-01-01", "2026-06-01", "청평도서관 문화교실"),
        Course("5011", "MB", "지난 조종교육 2", "행사종료", "2026-01-01", "2026-06-01", "조종도서관 문화교실"),
        Course(
            "5010",
            "MC",
            "과거 원천 접수기간 오기",
            "행사종료",
            "2026-01-01",
            "2026-06-01",
            "설악도서관 다목적실",
            apply_start="2026-02-01",
            apply_end="2026-01-01",
        ),
    ]


def _source_library(scope: str) -> str:
    return gapyeong.GAPYEONG_MANAGE_CODES[scope]


def _detail_institution(course: Course) -> str:
    if course.scope == "CO":
        return "공통"
    return gapyeong.GAPYEONG_BRANCHES[course.scope]


def _date(value: str) -> str:
    return value.replace("-", ".") + "(수)"


def _list_row(course: Course, sequence: int) -> str:
    return f"""
      <tr>
        <td>{sequence}</td>
        <td><strong class="lib">{_source_library(course.scope)}</strong></td>
        <td class="title">
          <p class="lecture_tit"><a href="#javascript"
            onclick="javascript:fnDetail('{course.identity}'); return false;">{escape(course.title)}</a></p>
          <ul>
            <li><span>접수기간</span>{_date(course.apply_start)}~{_date(course.apply_end)}</li>
            <li><span>수강기간</span>{_date(course.start)}~{_date(course.end)}</li>
            <li><span>교육장소</span>{escape(course.venue)}</li>
          </ul>
        </td>
        <td class="mobileHide2">{escape(course.target)}</td>
        <td class="mobileHide2">{course.current} / {course.capacity}<br>
          ({course.waiting} / {course.waiting_capacity})</td>
        <td><span class="lecture_case">{course.source_status}</span></td>
      </tr>
    """


def _list_html(
    courses: list[Course],
    *,
    scope: str,
    page: int,
    force_nonempty_sentinel: bool = False,
) -> str:
    total = len(courses)
    last = max(1, (total + gapyeong.GAPYEONG_PAGE_SIZE - 1) // gapyeong.GAPYEONG_PAGE_SIZE)
    start = (page - 1) * gapyeong.GAPYEONG_PAGE_SIZE
    selected = courses[start : start + gapyeong.GAPYEONG_PAGE_SIZE] if page <= last else []
    if force_nonempty_sentinel and page == last + 1:
        selected = courses[-1:]
    rows = "".join(
        _list_row(course, total - start - offset)
        for offset, course in enumerate(selected)
    )
    options = ['<option value="">도서관 선택</option>']
    options.extend(
        f'<option value="{code}"{" selected" if code == scope else ""}>{name}도서관</option>'
        for code, name in gapyeong.GAPYEONG_MANAGE_CODES.items()
    )
    paging = (
        f'<div class="paging"><a href="#" onclick="fnList({last}); return false;">'
        "맨 마지막 페이지로 가기</a></div>"
        if last > 1
        else '<div class="paging"></div>'
    )
    return f"""
      <html><head><title>가평군도서관</title></head><body>
        <form id="paramForm"><input name="currentPageNo" value="{page}">
          <input name="lectureIdx" value="0"></form>
        <form id="searchForm"><select name="manageCd">{''.join(options)}</select></form>
        <table class="board-list"><tbody>{rows}</tbody></table>
        {paging}
        <footer>대표전화 031-580-4041</footer>
      </body></html>
    """


def _detail_html(
    course: Course,
    *,
    title: str | None = None,
    identity: str | None = None,
    institution: str | None = None,
    venue: str | None = None,
    target: str | None = None,
    show_control: bool | None = None,
) -> str:
    active = course.source_status in {"접수중", "대기중"}
    if show_control is not None:
        active = show_control
    control = '<a href="#none" id="applyBtn">수강신청</a>' if active else ""
    values = [
        (
            "프로그램명",
            f'<span class="tblBtnSmall">{course.source_status}</span>'
            f'<span>{escape(title or course.title)}</span>',
        ),
        ("기관", escape(institution or _detail_institution(course))),
        (
            "접수기간",
            f"{_date(course.apply_start)} ~ {_date(course.apply_end)} 09:00~18:00",
        ),
        (
            "신청현황",
            f"신청자수: {course.current}/{course.capacity}명 | "
            f"대기자수: {course.waiting}/{course.waiting_capacity}명",
        ),
        (
            "수강기간 / 시간",
            f"{_date(course.start)} ~ {_date(course.end)} (수요일) / 10:00~12:00",
        ),
        ("대상 / 제한사항", escape(target if target is not None else course.target) + " / -"),
        ("교육장소", escape(venue or course.venue)),
        ("기수구분", ""),
        ("강의계획서", "개인정보 가능 첨부.pdf"),
    ]
    body = "".join(f"<tr><th>{label}</th><td>{value}</td></tr>" for label, value in values)
    return f"""
      <html><head><title>가평군도서관</title></head><body>
        <form id="paramForm"><input name="currentPageNo" value="1">
          <input name="lectureIdx" value="{identity or course.identity}"></form>
        <script>function fnApply() {{ var form = document.paramForm;
          form.action = "{gapyeong.GAPYEONG_APPLY_PATH}"; form.submit(); }}</script>
        <table class="board-view"><tbody>{body}
          <tr><td colspan="2" class="content">문의 031-580-4041 / 저장 금지 자유본문</td></tr>
        </tbody></table>
        {control}
        <footer>대표전화 031-580-4041</footer>
      </body></html>
    """


class Fixture:
    def __init__(self) -> None:
        self.courses = _courses()
        self.calls: list[str] = []
        self.counts: Counter[tuple[str, int]] = Counter()
        self.lock = Lock()
        self.first_page_drift = False
        self.partition_drift = ""
        self.nonempty_sentinel = False
        self.detail_mode = ""

    def __call__(self, _session, url: str, _timeout: int) -> Response:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.calls.append(url)
        if parsed.path == gapyeong.GAPYEONG_DETAIL_PATH:
            identity = query["lectureIdx"][0]
            course = next(item for item in self.courses if item.identity == identity)
            if self.detail_mode == "http_failure" and identity == "5020":
                return Response(url, "upstream failure", status_code=503)
            kwargs: dict[str, object] = {}
            if self.detail_mode == "title_drift" and identity == "5020":
                kwargs["title"] = "다른 강좌"
            elif self.detail_mode == "identity_drift" and identity == "5020":
                kwargs["identity"] = "9999"
            elif self.detail_mode == "missing_control" and identity == "5020":
                kwargs["show_control"] = False
            elif self.detail_mode == "inactive_control" and identity == "5018":
                kwargs["show_control"] = True
            elif self.detail_mode == "venue_drift" and identity == "5020":
                kwargs["venue"] = "다른 시설"
            elif self.detail_mode == "branch_drift" and identity == "5020":
                kwargs["institution"] = "청평도서관"
            if self.detail_mode == "pii_target" and identity == "5020":
                kwargs["target"] = "담당자 privacy.person@gaplib.go.kr"
            return Response(url, _detail_html(course, **kwargs))

        assert parsed.path == gapyeong.GAPYEONG_LIST_PATH
        scope = query.get("manageCd", [""])[0]
        page = int(query.get("currentPageNo", ["1"])[0])
        with self.lock:
            self.counts[(scope, page)] += 1
            call_number = self.counts[(scope, page)]
        courses = self.courses if not scope else [item for item in self.courses if item.scope == scope]
        if self.partition_drift and self.partition_drift == scope and courses:
            courses = [replace(courses[0], title=courses[0].title + " 분할변경"), *courses[1:]]
        if self.first_page_drift and not scope and page == 1 and call_number > 1:
            courses = [replace(courses[0], title=courses[0].title + " 경계변경"), *courses[1:]]
        if self.detail_mode == "pii_target":
            courses = [
                replace(item, target="담당자 privacy.person@gaplib.go.kr")
                if item.identity == "5020"
                else item
                for item in courses
            ]
        return Response(
            url,
            _list_html(
                courses,
                scope=scope,
                page=page,
                force_nonempty_sentinel=self.nonempty_sentinel and not scope,
            ),
        )


def _target(**changes: str) -> dict[str, str]:
    target = {
        "provider": gapyeong.GAPYEONG_PROVIDER,
        "url": gapyeong.GAPYEONG_CANONICAL_URL,
        "candidate_id": gapyeong.GAPYEONG_CANONICAL_CANDIDATE_ID,
    }
    target.update(changes)
    return target


def _collect(fixture: Fixture, **kwargs):
    return gapyeong.collect(
        _target(),
        today="2026-07-22",
        timeout=5,
        max_pages=25,
        detail_limit=10,
        max_workers=2,
        session_factory=DummySession,
        fetcher=fixture,
        **kwargs,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.gaplib.go.kr/intro/menu/10058/program/30014/lectureList.do",
        "https://www.gaplib.go.kr.evil.example/intro/menu/10058/program/30014/lectureList.do",
        "https://www.gaplib.go.kr@evil.example/intro/menu/10058/program/30014/lectureList.do",
        "https://evil@www.gaplib.go.kr/intro/menu/10058/program/30014/lectureList.do",
        "https://www.gaplib.go.kr:443/intro/menu/10058/program/30014/lectureList.do",
        gapyeong.GAPYEONG_CANONICAL_URL + "?currentPageNo=1",
        gapyeong.GAPYEONG_CANONICAL_URL + "?ignored=",
        gapyeong.GAPYEONG_CANONICAL_URL + "#lecture",
    ],
)
def test_exact_target_rejects_noncanonical_and_malicious_urls(url: str) -> None:
    assert not gapyeong.is_target(_target(url=url))


def test_constants_and_exact_canonical_owner() -> None:
    assert gapyeong.GAPYEONG_PROVIDER == "MUNI_WWW_GAPLIB_GO_KR_38AFB1BF"
    assert gapyeong.GAPYEONG_CANONICAL_CANDIDATE_ID == "MUNI_IR_9B2CE41807D7"
    assert gapyeong.GAPYEONG_MUNICIPALITY_CODE == "4182000000"
    assert set(gapyeong.GAPYEONG_MANAGE_CODES) == {"CO", "MA", "MC", "MD", "MB"}
    assert gapyeong.is_target(_target())
    assert not gapyeong.is_target(_target(provider="MUNI_WRONG"))
    with pytest.raises(ValueError):
        gapyeong.gapyeong_list_url(0)
    with pytest.raises(ValueError):
        gapyeong.gapyeong_list_url(1, "XX")
    with pytest.raises(ValueError):
        gapyeong.gapyeong_detail_url("../5020")


def test_full_ledger_five_partitions_current_details_controls_branches_and_pii() -> None:
    fixture = Fixture()
    rows, parser, meta = _collect(fixture)

    assert parser == gapyeong.GAPYEONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == meta["source_total"] == 11
    assert meta["scope_totals"] == {
        "ALL": 11,
        "CO": 2,
        "MA": 2,
        "MC": 3,
        "MD": 2,
        "MB": 2,
    }
    assert meta["scope_page_counts"] == {
        "ALL": 2,
        "CO": 1,
        "MA": 1,
        "MC": 1,
        "MD": 1,
        "MB": 1,
    }
    assert meta["required_list_requests"] == meta["list_requests"] == 25
    assert meta["data_pages"] == 7
    assert meta["post_last_sentinel_count"] == 6
    assert meta["boundary_rechecks"] == 12
    assert meta["partition_total"] == 11
    assert meta["partition_identity_difference_count"] == 0
    assert meta["current_source_count"] == meta["detail_pages"] == meta["returned_count"] == 3
    assert meta["expired_count"] == 8
    assert meta["expired_period_anomaly_count"] == 1
    assert meta["source_status_counts"] == {
        "접수중": 1,
        "대기중": 1,
        "접수마감": 1,
        "행사종료": 8,
    }
    assert meta["status_counts"] == {"CLOSED": 1, "OPEN": 2}
    assert meta["application_control_count"] == 2
    assert meta["pagination_complete"] is True
    assert meta["classification_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["pii_payload_persisted"] is False

    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    assert set(by_id) == {"5020", "5019", "5018"}
    assert by_id["5020"]["branch"] == "설악도서관"
    assert by_id["5020"]["branch_code"] == "GAPLIB_MC"
    assert by_id["5019"]["branch"] == "한석봉도서관"
    assert by_id["5018"]["branch"] == "청평도서관"
    assert by_id["5018"]["branch_code"] == "GAPLIB_MD"
    assert by_id["5018"]["venue_name"] == "청평도서관 3층 문화교실"
    assert by_id["5020"]["reservation_available"] is True
    assert by_id["5019"]["reservation_available"] is True
    assert by_id["5018"]["reservation_available"] is False
    assert parse_qs(urlparse(by_id["5020"]["application_url"]).query) == {
        "lectureIdx": ["5020"]
    }
    assert by_id["5020"]["capacity_current"] == 4
    assert by_id["5020"]["capacity_total"] == 15
    assert by_id["5020"]["waiting_capacity"] == 10
    payload = repr(rows)
    for forbidden in (
        "031-580-4041",
        "저장 금지 자유본문",
        "개인정보 가능 첨부",
        "instructor",
        "contact",
        "attachments",
        "source_html",
    ):
        assert forbidden not in payload
    assert not any(
        urlparse(url).path == gapyeong.GAPYEONG_APPLY_PATH for url in fixture.calls
    )


@pytest.mark.parametrize(
    ("max_pages", "detail_limit", "expected"),
    [
        (24, 10, "max_pages 24 below required 25"),
        (25, 2, "detail_limit 2 below required 3"),
    ],
)
def test_list_and_detail_caps_fail_closed_without_partial_rows(
    max_pages: int,
    detail_limit: int,
    expected: str,
) -> None:
    fixture = Fixture()
    rows, _parser, meta = gapyeong.collect(
        _target(),
        today="2026-07-22",
        timeout=5,
        max_pages=max_pages,
        detail_limit=detail_limit,
        max_workers=2,
        session_factory=DummySession,
        fetcher=fixture,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert expected in meta["configured_collection_error"]
    assert not any(
        urlparse(url).path == gapyeong.GAPYEONG_DETAIL_PATH for url in fixture.calls
    )


def test_first_page_boundary_drift_fails_before_details() -> None:
    fixture = Fixture()
    fixture.first_page_drift = True
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert meta["pagination_complete"] is False
    assert "scope ALL: first page recheck failed" in meta["configured_collection_error"]
    assert not any(
        urlparse(url).path == gapyeong.GAPYEONG_DETAIL_PATH for url in fixture.calls
    )


def test_post_last_page_must_be_empty() -> None:
    fixture = Fixture()
    fixture.nonempty_sentinel = True
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "post-last page is not empty" in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_unfiltered_and_facility_partition_drift_fails_closed() -> None:
    fixture = Fixture()
    fixture.partition_drift = "MA"
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "five facility partitions do not reconcile" in meta["configured_collection_error"]
    assert meta["classification_complete"] is False


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("http_failure", "unexpected HTTP status 503"),
        ("title_drift", "list/detail identity or status drift"),
        ("identity_drift", "detail identity drift"),
        ("missing_control", "active status lacks identity-bound application control"),
        ("inactive_control", "inactive status exposes an application control"),
        ("venue_drift", "list/detail venue drift"),
        ("branch_drift", "facility branch drift"),
        ("pii_target", "contact data persisted"),
    ],
)
def test_detail_identity_application_facility_and_pii_fail_closed(
    mode: str, expected: str
) -> None:
    fixture = Fixture()
    fixture.detail_mode = mode
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert expected in meta["configured_collection_error"]


def test_current_reversed_official_period_fails_before_details() -> None:
    fixture = Fixture()
    fixture.courses = [
        replace(item, apply_start="2026-08-01", apply_end="2026-07-01")
        if item.identity == "5020"
        else item
        for item in fixture.courses
    ]
    rows, _parser, meta = _collect(fixture)
    assert rows == []
    assert "unsafe current/non-terminal reversed official period" in meta["configured_collection_error"]
    assert not any(
        urlparse(url).path == gapyeong.GAPYEONG_DETAIL_PATH for url in fixture.calls
    )


def test_dedupe_may_not_reduce_complete_current_identity_set() -> None:
    fixture = Fixture()
    rows, _parser, meta = _collect(fixture, dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert "dedupe changed the complete current identity set" in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_wrong_target_and_invalid_limits_return_no_rows() -> None:
    fixture = Fixture()
    rows, _parser, meta = gapyeong.collect(
        _target(provider="WRONG"),
        session_factory=DummySession,
        fetcher=fixture,
    )
    assert rows == []
    assert "canonical Gapyeong Library owner" in meta["configured_collection_error"]

    rows, _parser, meta = gapyeong.collect(
        _target(),
        max_workers=0,
        session_factory=DummySession,
        fetcher=fixture,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "invalid collection limits" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("GAPYEONG_EDUCATION_LIVE") != "1",
    reason="set GAPYEONG_EDUCATION_LIVE=1 for official-source verification",
)
def test_live_gapyeong_complete_ledger() -> None:
    rows, parser, meta = gapyeong.collect(
        _target(),
        today=date(2026, 7, 22),
        timeout=40,
        max_pages=200,
        detail_limit=300,
        max_workers=4,
    )
    assert parser == gapyeong.GAPYEONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == meta["source_total"] == 686
    assert meta["scope_totals"] == {
        "ALL": 686,
        "CO": 33,
        "MA": 187,
        "MC": 162,
        "MD": 169,
        "MB": 135,
    }
    assert meta["required_list_requests"] == meta["list_requests"] == 158
    assert meta["partition_total"] == 686
    assert meta["partition_identity_difference_count"] == 0
    assert meta["current_source_count"] == meta["detail_pages"] == 13
    assert meta["application_control_count"] == 8
    assert meta["expired_period_anomaly_count"] == 1
    assert meta["pagination_complete"] is True
    assert meta["classification_complete"] is True
    assert meta["snapshot_complete"] is True
    assert len(rows) == meta["returned_count"] == 13
    assert all(row["municipality_code"] == "4182000000" for row in rows)
    assert all(not gapyeong._privacy(row) for row in rows)
