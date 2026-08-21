from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_cheongyang as cheongyang


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    page: int
    target: str = "성인"
    apply_period: str = "2026-07-01 ~ 2026-08-01 23:59"
    period: str = "2026-08-02 ~ 2026-12-01"
    schedule: str = "월/수 13:30~15:30"
    capacity_current: int = 2
    capacity_total: int = 14
    status: str = "모집중"
    venue: str = "청양군평생학습관"
    institution: str = "청양군"
    description: str = "공개 교육 내용"


class Response:
    def __init__(self, url: str, html: str, status_code: int = 200) -> None:
        self.url = url
        self.status_code = status_code
        self.history: list[object] = []
        self.headers = {"Content-Type": "text/html;charset=UTF-8"}
        self.content = html.encode("utf-8")


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _search_form(*, selected: str = "Y") -> str:
    options = []
    for value, label in (("", "전체"), ("Y", "모집중"), ("N", "모집마감")):
        marker = ' selected="selected"' if value == selected else ""
        options.append(f'<option value="{value}"{marker}>{label}</option>')
    return f"""
      <h2 class="page__title">평생학습강좌신청</h2>
      <form name="searchForm" method="post"
            action="/prog/educate/lll/sub02_01/list.do">
        <input type="hidden" name="siteCode" value="lll">
        <input type="hidden" name="mno" value="sub02_01">
        <select name="reservYn">{"".join(options)}</select>
      </form>
    """


def _list_row(course: Course, *, identity: str | None = None) -> str:
    actual_identity = identity or course.identity
    if course.status == "모집중":
        status_control = (
            '<a href="/prog/educate/reserve/lll/sub02_01/write.do?'
            f'pageIndex={course.page}&amp;eduNo={actual_identity}'
            f'&amp;oneInwon=999">{course.status}</a>'
        )
    else:
        status_control = (
            '<a href="/prog/educate/lll/sub02_01/view.do?'
            f'pageIndex={course.page}&amp;eduNo={actual_identity}">'
            f"{course.status}</a>"
        )
    return f"""
      <tr>
        <td><a href="/prog/educate/lll/sub02_01/view.do?pageIndex={course.page}&amp;eduNo={actual_identity}">{escape(course.title)}</a></td>
        <td>{escape(course.target)}</td>
        <td>{escape(course.apply_period)}</td>
        <td>{escape(course.period)}</td>
        <td>{course.capacity_current}/{course.capacity_total}</td>
        <td>{escape(course.schedule)}</td>
        <td>
          <span>{status_control}</span>
          <span><a href="/prog/educate_reserve/lll/sub02_01/list.do?eduNo={actual_identity}&amp;oneInwon=999">신청자 리스트</a></span>
        </td>
      </tr>
    """


def _list_html(courses: list[Course], *, selected: str = "Y") -> str:
    headers = "".join(
        f'<th scope="col">{header}</th>'
        for header in (
            "강좌명",
            "대상",
            "접수기간",
            "교육기간",
            "신청인원 / 모집인원",
            "시간",
            "상태",
        )
    )
    return f"""
      <html><body>
        {_search_form(selected=selected)}
        <table class="basic_table center">
          <thead><tr>{headers}</tr></thead>
          <tbody>{"".join(_list_row(course) for course in courses)}</tbody>
        </table>
      </body></html>
    """


def _detail_html(
    course: Course,
    *,
    title: str | None = None,
    application_identity: str | None = None,
    include_application: bool | None = None,
) -> str:
    pairs = [
        ("강좌명", title or course.title),
        ("교육기간", course.period),
        ("교육시간", course.schedule),
        ("접수기간", course.apply_period),
        ("교육장소", course.venue),
        ("교육대상", course.target),
        ("문의전화", "041-940-2860"),
        ("정원", str(course.capacity_total)),
        ("담당자", "공개 담당자"),
        ("교육기관", course.institution),
        ("교육내용", course.description),
        ("기타사항", "."),
    ]
    rendered = "".join(
        f"<tr><th scope='row'>{escape(label)}</th><td>{escape(value)}</td></tr>" for label, value in pairs
    )
    application = ""
    if include_application is None:
        include_application = course.status == "모집중"
    if include_application:
        identity = application_identity or course.identity
        application = (
            '<a href="/prog/educate/reserve/lll/sub02_01/write.do?'
            f'pageIndex={course.page}&amp;eduNo={identity}&amp;oneInwon=">모집중</a>'
        )
    return f"<html><body><table><tbody>{rendered}</tbody></table>{application}</body></html>"


def _courses(count: int = 11) -> list[Course]:
    return [
        Course(
            identity=str(2000 + offset),
            title=f"청양 공개 교육 {offset}",
            page=(offset // cheongyang.CHEONGYANG_PAGE_SIZE) + 1,
        )
        for offset in range(count)
    ]


class Fixture:
    def __init__(self, count: int = 11) -> None:
        self.courses = _courses(count)
        self.calls: list[str] = []
        self.list_calls: Counter[int] = Counter()
        self.mode = ""

    def __call__(self, _session, url: str, _timeout: int) -> Response:
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path in {
            cheongyang.CHEONGYANG_APPLICATION_PATH,
            cheongyang.CHEONGYANG_APPLICANT_LIST_PATH,
        }:
            raise AssertionError("private application/applicant endpoints must never be requested")
        if parsed.path == cheongyang.CHEONGYANG_DETAIL_PATH:
            identity = query["eduNo"][0]
            course = next(item for item in self.courses if item.identity == identity)
            if self.mode == "detail_http_failure" and identity == self.courses[0].identity:
                return Response(url, "temporary failure", 503)
            if self.mode == "detail_title_drift" and identity == self.courses[0].identity:
                return Response(url, _detail_html(course, title="다른 강좌"))
            if self.mode == "application_identity_drift" and identity == self.courses[0].identity:
                return Response(url, _detail_html(course, application_identity="9999"))
            if self.mode == "missing_application" and identity == self.courses[0].identity:
                return Response(url, _detail_html(course, include_application=False))
            if self.mode == "attachment":
                detail = _detail_html(course).replace(
                    "</tbody>",
                    (
                        "<tr><th>첨부파일</th>"
                        "<td><a href='/download/private-plan.pdf'>"
                        "private-plan.pdf</a></td></tr></tbody>"
                    ),
                    1,
                )
                return Response(url, detail)
            return Response(url, _detail_html(course))

        assert parsed.path == cheongyang.CHEONGYANG_LIST_PATH
        page = int(query.get("pageIndex", ["1"])[0])
        self.list_calls[page] += 1
        start = (page - 1) * cheongyang.CHEONGYANG_PAGE_SIZE
        courses = self.courses[start : start + cheongyang.CHEONGYANG_PAGE_SIZE]
        if self.mode == "first_boundary_drift" and page == 1 and self.list_calls[page] > 1:
            courses = [replace(courses[0], title="경계에서 바뀐 제목"), *courses[1:]]
        if self.mode == "sentinel_resumed" and page == 3 and self.list_calls[page] > 1:
            courses = [replace(self.courses[0], page=3)]
        if self.mode == "duplicate_identity" and page == 2 and courses:
            courses = [replace(courses[0], identity=self.courses[0].identity), *courses[1:]]
        if self.mode == "bad_filter":
            return Response(url, _list_html(courses, selected="N"))
        return Response(url, _list_html(courses))


def _target(**changes: str) -> dict[str, str]:
    target = {
        "provider": cheongyang.CHEONGYANG_PROVIDER,
        "url": cheongyang.CHEONGYANG_CANONICAL_URL,
    }
    target.update(changes)
    return target


def _collect(fixture: Fixture, **kwargs):
    options = {
        "today": "2026-07-22",
        "timeout": 5,
        "max_pages": 5,
        "detail_limit": 20,
        "session_factory": DummySession,
        "fetcher": fixture,
    }
    options.update(kwargs)
    return cheongyang.collect(_target(), **options)


@pytest.mark.parametrize(
    "url",
    [
        cheongyang.CHEONGYANG_CANONICAL_URL + "?pageIndex=1",
        cheongyang.CHEONGYANG_CANONICAL_URL + "#courses",
        cheongyang.CHEONGYANG_NOTICE_BOARD_URL,
        cheongyang.CHEONGYANG_CHILD_EXPERIENCE_URL,
        cheongyang.CHEONGYANG_MUSEUM_GROUP_URL,
        "http://www.cheongyang.go.kr/prog/educate/lll/sub02_01/list.do",
        "https://cheongyang.go.kr/prog/educate/lll/sub02_01/list.do",
        "https://www.cheongyang.go.kr.evil.example/prog/educate/lll/sub02_01/list.do",
        "https://evil@www.cheongyang.go.kr/prog/educate/lll/sub02_01/list.do",
        "https://www.cheongyang.go.kr:443/prog/educate/lll/sub02_01/list.do",
    ],
)
def test_exact_target_matcher_rejects_aliases_and_other_owners(url: str) -> None:
    assert not cheongyang.is_target(_target(url=url))


def test_stable_ids_and_owner_boundaries_are_explicit() -> None:
    assert cheongyang.is_target(_target())
    assert not cheongyang.is_target(_target(provider="MUNI_WRONG"))
    assert cheongyang.CHEONGYANG_CANONICAL_CANDIDATE_ID == "MUNI_IR_58B97DB9DDF4"
    assert cheongyang.CHEONGYANG_REVIEW_BOARD_CANDIDATE_ID == "MUNI_IR_28C0549FFD3C"
    assert cheongyang.CHEONGYANG_CANONICAL_DERIVED_PROVIDER != cheongyang.CHEONGYANG_PROVIDER
    assert "information_only" in cheongyang.CHEONGYANG_OWNER_BOUNDARY_AUDIT["lifelong_notice_board"]["decision"]
    assert "separate" in cheongyang.CHEONGYANG_OWNER_BOUNDARY_AUDIT["child_experience"]["decision"]
    with pytest.raises(ValueError):
        cheongyang.cheongyang_list_url(0)
    with pytest.raises(ValueError):
        cheongyang.cheongyang_detail_url("../2091")


def test_complete_snapshot_pages_boundaries_details_controls_and_branch() -> None:
    fixture = Fixture(11)
    rows, parser, meta = _collect(fixture)

    assert parser == cheongyang.CHEONGYANG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == meta["source_total"] == 11
    assert meta["data_pages"] == 2
    assert meta["page_counts"] == {1: 10, 2: 1}
    assert meta["empty_boundary_page"] == 3
    assert meta["list_requests"] == 6
    assert meta["stability_rechecks"] == 3
    assert meta["detail_pages"] == meta["detail_verified"] == 11
    assert meta["application_control_count"] == 11
    assert meta["application_endpoint_requests"] == 0
    assert meta["applicant_list_requests"] == 0
    assert meta["logical_requests"] == meta["physical_requests"] == 17
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert len(rows) == 11
    assert fixture.list_calls == Counter({1: 2, 2: 2, 3: 2})
    assert all(
        urlparse(url).path
        not in {
            cheongyang.CHEONGYANG_APPLICATION_PATH,
            cheongyang.CHEONGYANG_APPLICANT_LIST_PATH,
        }
        for url in fixture.calls
    )

    second_page = rows[-1]
    assert second_page["provider_course_id"].endswith(":edu:2010")
    assert second_page["branch"] == second_page["venue_name"] == "청양군평생학습관"
    assert second_page["address"] == cheongyang.CHEONGYANG_OFFICIAL_ADDRESS
    assert second_page["status"] == "OPEN"
    assert second_page["reservation_available"] is True
    assert second_page["application_type"] == "ONLINE_RESERVATION_LOGIN_REQUIRED"
    assert parse_qs(urlparse(second_page["application_url"]).query) == {
        "pageIndex": ["2"],
        "eduNo": ["2010"],
        "oneInwon": ["999"],
    }
    assert second_page["raw_url"] == cheongyang.cheongyang_detail_url("2010")
    assert second_page["municipality_code"] == "4479000000"
    assert second_page["municipality_full_name"] == "충청남도 청양군"
    assert second_page["category"] == "교육"
    assert second_page["fee"] == "요금 별도 안내"
    assert second_page["raw_fields"]["source_institution"] == "청양군"
    assert second_page["raw_fields"]["filter_scope"] == "reservYn=Y (모집중)"


def test_scheduled_and_capacity_closed_rows_have_no_application_url() -> None:
    fixture = Fixture(0)
    fixture.courses = [
        Course(
            identity="2092",
            title="접수 예정 강좌",
            page=1,
            status="모집예정",
            apply_period="2026-07-22 09:00 ~ 2026-08-01 18:00",
            capacity_current=0,
        ),
        Course(
            identity="2094",
            title="정원 마감 강좌",
            page=1,
            status="모집마감",
            capacity_current=14,
        ),
    ]

    rows, _parser, meta = _collect(fixture)

    by_id = {
        row["raw_fields"]["identity"]: row
        for row in rows
    }
    assert set(by_id) == {"2092", "2094"}
    assert by_id["2092"]["status"] == "SCHEDULED"
    assert by_id["2094"]["status"] == "CLOSED"
    assert all(not row["reservation_available"] for row in rows)
    assert all(row["application_url"] == "" for row in rows)
    assert all(
        row["raw_fields"]["inactive_application_absence_verified"]
        for row in rows
    )
    assert meta["application_control_count"] == 0
    assert meta["source_status_counts"] == {
        "모집예정": 1,
        "모집마감": 1,
    }


def test_optional_attachment_is_validated_but_not_persisted() -> None:
    fixture = Fixture(1)
    fixture.mode = "attachment"

    rows, _parser, meta = _collect(fixture)

    assert meta["snapshot_complete"] is True
    assert len(rows) == 1
    persisted = str(rows)
    assert "첨부파일" not in persisted
    assert "private-plan.pdf" not in persisted
    assert "/download/" not in persisted


def test_structural_empty_page_one_is_a_complete_empty_snapshot() -> None:
    fixture = Fixture(0)
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert meta["source_rows"] == 0
    assert meta["data_pages"] == 0
    assert meta["empty_boundary_page"] == 1
    assert meta["list_requests"] == 2
    assert meta["detail_pages"] == 0
    assert meta["no_current_data"] is True
    assert meta["full_snapshot_validated"] is True


def test_max_pages_and_detail_limits_never_return_partial_rows() -> None:
    fixture = Fixture(11)
    rows, _, meta = _collect(fixture, max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["list_requests"] == 2
    assert "max_pages" in meta["configured_collection_error"]

    fixture = Fixture(11)
    rows, _, meta = _collect(fixture, detail_limit=10)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_pages"] == 0
    assert "detail_limit" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "mode,error_fragment",
    [
        ("first_boundary_drift", "boundary stability recheck changed"),
        ("sentinel_resumed", "structural empty boundary changed"),
        ("duplicate_identity", "duplicate identities"),
        ("bad_filter", "current recruiting filter contract changed"),
        ("detail_title_drift", "detail title identity drift"),
        ("application_identity_drift", "application control identity changed"),
        ("missing_application", "detail application control count changed"),
        ("detail_http_failure", "unexpected HTTP status 503"),
    ],
)
def test_contract_drift_is_fail_closed(mode: str, error_fragment: str) -> None:
    fixture = Fixture(11)
    fixture.mode = mode
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert error_fragment in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False
    assert meta["full_snapshot_validated"] is False


def test_recruiting_date_contradictions_fail_closed() -> None:
    fixture = Fixture(1)
    fixture.courses = [
        replace(
            fixture.courses[0],
            apply_period="2026-01-01 ~ 2026-01-31",
            period="2026-01-01 ~ 2026-06-30",
        )
    ]
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "expired course" in meta["configured_collection_error"]


def test_public_description_contacts_are_redacted_and_private_fields_discarded() -> None:
    fixture = Fixture(1)
    fixture.courses = [
        replace(
            fixture.courses[0],
            description="문의 041-940-2860 또는 person@example.kr",
        )
    ]
    rows, _, meta = _collect(fixture)
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 1
    serialized = repr(rows[0])
    assert "041-940-2860" not in serialized
    assert "person@example.kr" not in serialized
    assert "공개 담당자" not in serialized
    assert "[연락처 비공개]" in rows[0]["description"]
    assert "[이메일 비공개]" in rows[0]["description"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_TESTS") != "1",
    reason="set RUN_LIVE_MUNICIPAL_TESTS=1 for the official Cheongyang audit",
)
def test_live_official_complete_current_ledger() -> None:
    rows, parser, meta = cheongyang.collect(
        _target(),
        today="2026-07-22",
        timeout=30,
        max_pages=10,
        detail_limit=30,
    )
    assert parser == cheongyang.CHEONGYANG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["pagination_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["source_rows"] == len(rows)
    assert meta["empty_boundary_page"] == meta["data_pages"] + 1
    assert meta["application_endpoint_requests"] == 0
    assert meta["applicant_list_requests"] == 0
    for row in rows:
        assert row["branch"]
        assert row["raw_fields"]["detail_verified"] is True
        assert row["raw_fields"]["application_control_verified"] is True
