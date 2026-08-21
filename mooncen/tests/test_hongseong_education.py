from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_hongseong as hongseong


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    page: int
    business: str = "홍성군평생학습관"
    event_period: str = "2026-08-01 ~ 2026-08-31"
    apply_period: str = "2026-07-01 ~ 2026-07-31"
    schedule: str = "매주 수요일 10:00~12:00"
    capacity_current: int = 3
    capacity_total: int = 12
    waitlist: int | None = 0
    method: str = "선착순"
    source_status: str = "접수마감"
    resv_chk: str = ""
    detail_state: str = "교육중"
    category: str = "인문교양"
    target: str = "성인"
    venue: str = "홍성군평생학습관 3강의실(홍성읍 온천1길 11)"
    fee: str = "0 원"


class Response:
    def __init__(self, url: str, html: str, status_code: int = 200) -> None:
        self.url = url
        self.status_code = status_code
        self.history: list[object] = []
        self.headers = {"Content-Type": "text/html; charset=UTF-8"}
        self.content = html.encode("utf-8")


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _form(total: int) -> str:
    options = (
        ("", "사업구분"),
        ("OR1", "홍성군평생학습관"),
        ("OR2", "신도시평생학습관"),
        ("OR3", "읍면평생학습센터"),
        ("OR4", "평생학습카페"),
        ("OR5", "50플러스 스쿨"),
        ("OR6", "홍성군농업기술센터"),
        ("OR07", "홍성군홍주천년문화체험관"),
    )
    rendered = "".join(
        f'<option value="{escape(value)}">{escape(label)}</option>' for value, label in options
    )
    return f"""
      <form name="listForm" method="POST" action="{hongseong.HONGSEONG_LIST_PATH}">
        <input type="hidden" name="siteCode" value="lll">
        <input type="hidden" name="mno" value="sub06_01">
        <input type="hidden" name="pageIndex" value="1">
        <div class="program--count"><strong>{total:,}</strong></div>
        <select name="organ">{rendered}</select>
        <select name="searchCondition"><option value="subject">교육과목</option></select>
        <input name="searchKeyword" value="">
      </form>
    """


def _list_row(course: Course, *, control_identity: str | None = None) -> str:
    waitlist = f" ({course.waitlist})" if course.waitlist is not None else ""
    if course.resv_chk:
        identity = control_identity or course.identity
        control = (
            f'<a href="{hongseong.HONGSEONG_APPLICATION_PATH}?pageIndex={course.page}'
            f'&amp;eduNo={identity}&amp;resvChk={course.resv_chk}">{escape(course.source_status)}</a>'
        )
    else:
        control = f"<span>{escape(course.source_status)}</span>"
    return f"""
      <tr>
        <td>{escape(course.business)}</td>
        <td><a href="{hongseong.HONGSEONG_DETAIL_PATH}?eduNo={course.identity}">{escape(course.title)}</a></td>
        <td><strong>{escape(course.event_period)}</strong><p>({escape(course.apply_period)})</p></td>
        <td>{escape(course.schedule)}</td>
        <td><strong>{course.capacity_current} / {course.capacity_total}{waitlist}</strong><p>{escape(course.method)}</p></td>
        <td><span class="button"><span class="typeC">{control}</span></span></td>
      </tr>
    """


def _list_html(courses: list[Course], total: int, *, control_identity: str | None = None) -> str:
    headers = "".join(
        f'<th scope="col">{header}</th>'
        for header in (
            "사업명",
            "교육과목",
            "교육기간(접수기간)",
            "교육시간",
            "접수자/정원 (대기자)",
            "상태",
        )
    )
    if courses:
        rows = "".join(
            _list_row(course, control_identity=control_identity if index == 0 else None)
            for index, course in enumerate(courses)
        )
    else:
        rows = '<tr><td colspan="5">접수 예정 또는 접수중인 과목이 없습니다.</td></tr>'
    return f"""
      <html><body><div id="txt">
        {_form(total)}
        <table class="table table-bordered text-center">
          <caption><strong>교육신청 및 확인 목록</strong></caption>
          <thead><tr>{headers}</tr></thead><tbody>{rows}</tbody>
        </table>
      </div></body></html>
    """


def _detail_html(course: Course, *, title: str | None = None, extra_field: bool = False) -> str:
    fields: list[tuple[str, str]] = [
        ("사업명", course.business),
        ("분야", course.category),
        ("강좌명", title or course.title),
        ("교육대상", course.target),
        ("접수기간", course.apply_period),
        ("교육기간", course.event_period),
        ("교육시간", course.schedule),
        ("재료비", course.fee),
        ("강사명", "홍길동"),
        ("교육장소", course.venue),
        ("담당자", "공개 담당자"),
        ("문의전화", "041-630-9591"),
        ("첨부파일", "강의계획서.hwpx 다운로드"),
    ]
    if extra_field:
        fields.append(("새 개인정보", "010-1234-5678"))
    rows = []
    index = 0
    while index < len(fields):
        pair = fields[index : index + 2]
        rows.append(
            "<tr>"
            + "".join(
                f'<th scope="row">{escape(label)}</th><td>{escape(value)}</td>'
                for label, value in pair
            )
            + "</tr>"
        )
        index += 2
    return f"""
      <html><body><div id="txt">
        <div class="lecture_info">
          <em>{escape(course.detail_state)}</em><h2>{escape(course.title)}</h2>
          <table class="table"><caption><strong>강좌 정보표</strong></caption>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>
        <div class="wrap_lecture_txt">test@example.com 010-1234-5678 비공개 상세 설명</div>
        <a href="{hongseong.HONGSEONG_LIST_PATH}">목록</a>
      </div></body></html>
    """


def _courses() -> list[Course]:
    current = [
        Course(
            "3000",
            "홍성 미래교육",
            1,
            source_status="접수하기",
            resv_chk="N",
            detail_state="대기신청",
        ),
        Course(
            "2999",
            "카페 여행영어",
            1,
            business="평생학습카페",
            source_status="대기신청",
            resv_chk="Y",
            detail_state="대기중",
            venue="K카페(홍북읍 용봉산2길 41)",
        ),
        Course(
            "2998",
            "오십플러스 인문학",
            1,
            business="50플러스 스쿨",
            source_status="접수마감",
            resv_chk="",
            detail_state="교육중",
            venue="홍성군평생학습관",
            waitlist=None,
            method="추첨",
        ),
    ]
    expired = [
        Course(
            str(2997 - offset),
            f"지난 홍성교육 {offset}",
            1 if offset < 9 else 2,
            event_period="2025-01-01 ~ 2025-01-31",
            apply_period="2024-12-01 ~ 2024-12-31",
            detail_state="교육종료",
        )
        for offset in range(10)
    ]
    return current + expired


class Fixture:
    def __init__(self) -> None:
        self.courses = _courses()
        self.calls: list[str] = []
        self.list_calls: Counter[int] = Counter()
        self.mode = ""

    def __call__(self, _session, url: str, _timeout: int) -> Response:
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == hongseong.HONGSEONG_APPLICATION_PATH:
            raise AssertionError("application endpoint must never be requested")
        if parsed.path == hongseong.HONGSEONG_DETAIL_PATH:
            identity = query["eduNo"][0]
            course = next(item for item in self.courses if item.identity == identity)
            if self.mode == "detail_title_drift" and identity == "3000":
                return Response(url, _detail_html(course, title="다른 강좌"))
            if self.mode == "extra_sensitive_field" and identity == "3000":
                return Response(url, _detail_html(course, extra_field=True))
            if self.mode == "response_host_drift" and identity == "3000":
                return Response("https://evil.example/detail", _detail_html(course))
            return Response(url, _detail_html(course))

        assert parsed.path == hongseong.HONGSEONG_LIST_PATH
        page = int((query.get("pageIndex") or ["1"])[0])
        self.list_calls[page] += 1
        start = (page - 1) * hongseong.HONGSEONG_PAGE_SIZE
        courses = self.courses[start : start + hongseong.HONGSEONG_PAGE_SIZE]
        if self.mode == "boundary_drift" and page == 1 and self.list_calls[page] > 1:
            courses = [replace(courses[0], title="경계에서 바뀐 강좌"), *courses[1:]]
        if self.mode == "sentinel_resumed" and page == 3 and self.list_calls[page] > 1:
            courses = [replace(self.courses[0], page=3)]
        if self.mode == "duplicate_identity" and page == 2 and courses:
            courses = [replace(courses[0], identity=self.courses[0].identity)]
        control_identity = "9999" if self.mode == "application_identity_drift" and page == 1 else None
        return Response(url, _list_html(courses, len(self.courses), control_identity=control_identity))


def _target(**changes: str) -> dict[str, str]:
    target = {
        "provider": hongseong.HONGSEONG_PROVIDER,
        "url": hongseong.HONGSEONG_CANONICAL_URL,
    }
    target.update(changes)
    return target


def _collect(fixture: Fixture, **kwargs):
    options = {
        "today": "2026-07-23",
        "timeout": 5,
        "max_pages": 5,
        "detail_limit": 3,
        "session_factory": DummySession,
        "fetcher": fixture,
    }
    options.update(kwargs)
    return hongseong.collect(_target(), **options)


@pytest.mark.parametrize(
    "url",
    [
        hongseong.HONGSEONG_HOME_URL,
        hongseong.HONGSEONG_INTEGRATED_ALIAS_URL,
        hongseong.HONGSEONG_GENERAL_RESERVATION_URL,
        hongseong.HONGSEONG_SPACE_RESERVATION_URL,
        hongseong.HONGSEONG_CANONICAL_URL + "?pageIndex=1",
        hongseong.HONGSEONG_CANONICAL_URL + "#courses",
        hongseong.HONGSEONG_CANONICAL_URL.replace("https://", "http://"),
        hongseong.HONGSEONG_CANONICAL_URL.replace("www.hongseong.go.kr", "hongseong.go.kr"),
        hongseong.HONGSEONG_CANONICAL_URL.replace(
            "www.hongseong.go.kr", "www.hongseong.go.kr.evil.example"
        ),
        hongseong.HONGSEONG_CANONICAL_URL.replace("https://", "https://evil@"),
        hongseong.HONGSEONG_CANONICAL_URL.replace(".go.kr/", ".go.kr:443/"),
    ],
)
def test_exact_canonical_target_rejects_home_alias_and_other_owners(url: str) -> None:
    assert not hongseong.is_target(_target(url=url))


def test_stable_ids_owner_boundaries_and_official_centres_are_explicit() -> None:
    assert hongseong.is_target(_target())
    assert not hongseong.is_target(_target(provider="MUNI_WRONG"))
    assert hongseong.HONGSEONG_HOME_CANDIDATE_ID == "MUNI_IR_A11602D77C70"
    assert hongseong.HONGSEONG_CANONICAL_CANDIDATE_ID == "MUNI_IR_817903D7299F"
    assert hongseong.HONGSEONG_CANONICAL_DERIVED_PROVIDER != hongseong.HONGSEONG_PROVIDER
    audit = hongseong.HONGSEONG_OWNER_BOUNDARY_AUDIT
    assert "exact_replica" in audit["integrated_reservation_lifelong_alias"]["decision"]
    assert "separate" in audit["agriculture_education"]["decision"]
    assert "separate" in audit["culture_experience"]["decision"]
    assert hongseong.HONGSEONG_OFFICIAL_CENTRE_ADDRESSES == {
        "홍성군평생학습관": "충청남도 홍성군 홍성읍 온천1길 11",
        "신도시평생학습관": "충청남도 홍성군 홍북읍 홍학로 50",
    }
    with pytest.raises(ValueError):
        hongseong.hongseong_list_url(0)
    with pytest.raises(ValueError):
        hongseong.hongseong_detail_url("../3000")


def test_complete_snapshot_pages_details_controls_branches_addresses_and_pii() -> None:
    fixture = Fixture()
    rows, parser, meta = _collect(fixture)

    assert parser == hongseong.HONGSEONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["declared_total"] == meta["source_rows"] == meta["source_total"] == 13
    assert meta["pages"] == meta["data_pages"] == 2
    assert meta["page_counts"] == {1: 12, 2: 1}
    assert meta["empty_sentinel_page"] == 3
    assert meta["list_requests"] == 6
    assert meta["boundary_rechecks"] == 3
    assert fixture.list_calls == Counter({1: 2, 2: 2, 3: 2})
    assert meta["current_source_count"] == meta["current_education_count"] == 3
    assert meta["expired_count"] == 10
    assert meta["detail_pages"] == meta["detail_verified"] == 3
    assert meta["logical_requests"] == meta["physical_requests"] == 9
    assert meta["request_retry_count"] == 0
    assert meta["source_status_counts"] == {
        "접수하기": 1,
        "대기신청": 1,
        "접수마감": 11,
    }
    assert meta["status_counts"] == {"OPEN": 1, "WAITING": 1, "CLOSED": 1}
    assert meta["detail_state_counts"] == {"대기신청": 1, "대기중": 1, "교육중": 1}
    assert meta["branch_counts"] == {
        "홍성군평생학습관": 1,
        "평생학습카페": 1,
        "50플러스 스쿨": 1,
    }
    assert meta["application_control_count"] == 2
    assert meta["online_application_count"] == 1
    assert meta["waitlist_application_count"] == 1
    assert meta["info_only_count"] == 1
    assert meta["application_endpoint_requests"] == 0
    assert meta["applicant_list_requests"] == 0
    assert meta["sensitive_detail_fields_discarded"] == 9
    assert meta["attachment_fields_discarded"] == 3
    assert meta["freeform_detail_blocks_persisted"] == 0
    assert meta["pii_values_persisted"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert len(rows) == 3

    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    open_row = by_id["3000"]
    assert open_row["branch"] == "홍성군평생학습관"
    assert open_row["venue_name"] == "홍성군평생학습관 3강의실"
    assert open_row["address"] == "충청남도 홍성군 홍성읍 온천1길 11"
    assert open_row["status"] == "OPEN"
    assert open_row["application_type"] == "ONLINE_RESERVATION_LOGIN_REQUIRED"
    assert open_row["reservation_available"] is True
    assert parse_qs(urlparse(open_row["application_url"]).query) == {
        "pageIndex": ["1"],
        "eduNo": ["3000"],
        "resvChk": ["N"],
    }

    wait_row = by_id["2999"]
    assert wait_row["branch"] == "평생학습카페"
    assert wait_row["venue_name"] == "K카페"
    assert wait_row["address"] == "충청남도 홍성군 홍북읍 용봉산2길 41"
    assert wait_row["status"] == "WAITING"
    assert wait_row["application_type"] == "ONLINE_WAITLIST_LOGIN_REQUIRED"

    closed_row = by_id["2998"]
    assert closed_row["branch"] == "50플러스 스쿨"
    assert closed_row["venue_name"] == "홍성군평생학습관"
    assert closed_row["address"] == "충청남도 홍성군 홍성읍 온천1길 11"
    assert closed_row["status"] == "CLOSED"
    assert closed_row["application_url"] == ""
    assert closed_row["application_type"] == "INFO_ONLY"
    assert closed_row["reservation_available"] is False

    payload = repr(rows)
    assert "홍길동" not in payload
    assert "공개 담당자" not in payload
    assert "041-630-9591" not in payload
    assert "010-1234-5678" not in payload
    assert "test@example.com" not in payload
    assert "강의계획서.hwpx" not in payload
    assert all(row["municipality_code"] == "4480000000" for row in rows)
    assert all(row["program_type"] == "교육" for row in rows)


def test_page_cap_is_fail_closed_before_partial_walk() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture, max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "below required 3" in meta["configured_collection_error"]
    assert meta["full_snapshot_validated"] is False
    assert fixture.list_calls == Counter({1: 1})


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("boundary_drift", "stability recheck changed"),
        ("sentinel_resumed", "expected 0 source rows"),
        ("duplicate_identity", "duplicate identities across declared pages"),
        ("application_identity_drift", "application identity/path drift"),
        ("detail_title_drift", "list/detail identity drift"),
        ("extra_sensitive_field", "unaudited detail fields"),
        ("response_host_drift", "official response URL changed"),
    ],
)
def test_contract_and_privacy_drifts_fail_closed(mode: str, message: str) -> None:
    fixture = Fixture()
    fixture.mode = mode
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False
    assert meta["full_snapshot_validated"] is False


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_CRAWLER_TESTS") != "1",
    reason="set RUN_LIVE_CRAWLER_TESTS=1 for official live validation",
)
def test_live_hongseong_complete_snapshot() -> None:
    rows, parser, meta = hongseong.collect(
        _target(),
        today="2026-07-23",
        timeout=40,
        max_pages=200,
        detail_limit=100,
    )
    assert parser == hongseong.HONGSEONG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] >= 1701
    assert meta["current_source_count"] >= 25
    assert meta["returned_count"] == len(rows)
    assert meta["application_endpoint_requests"] == 0
    assert meta["pii_values_persisted"] == 0
    assert meta["full_snapshot_validated"] is True
