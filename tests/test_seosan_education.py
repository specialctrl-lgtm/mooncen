from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import os
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from Crawler import municipal_seosan as seosan


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    page: int
    event_period: str
    apply_period: str
    source_status: str = "신청마감"
    institution: str = "평생학습관"
    term: str = ""
    target: str = "서산시민"
    fee: str = "무료"
    detail_fee: str = ""
    day: str = "목/공통"
    time: str = "10시 00분 ~ 12시 00분"
    venue: str = "서산시평생학습관"
    method: str = "온라인,전화"
    capacity: int = 20
    online_capacity: int = 16
    applicants: int = 3
    waitlist: int = 0
    stale_list_application: bool = False
    stale_detail_application: bool = False

    @property
    def list_title(self) -> str:
        return self.title + (f"({self.term})" if self.term else "")


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


def _application_url(course: Course, identity: str | None = None) -> str:
    query = [
        ("key", "2"),
        ("edcCourseNo", identity or course.identity),
        ("searchInsttCode", ""),
        ("cl1No", "25"),
        ("cl2No", ""),
        ("pageUnit", str(seosan.SEOSAN_PAGE_SIZE)),
        ("searchCnd", "all"),
        ("searchKrwd", ""),
        ("pageIndex", str(course.page)),
    ]
    return f"https://{seosan.SEOSAN_HOST}{seosan.SEOSAN_APPLICATION_PATH}?{urlencode(query)}"


def _list_row(course: Course, *, application_identity: str | None = None) -> str:
    if (
        course.source_status in {"접수중", "대기접수", "대기신청"}
        or course.stale_list_application
    ):
        control = (
            f'<a href="{escape(_application_url(course, application_identity))}">'
            f"{escape(course.source_status)}</a>"
        )
    else:
        control = f"<span>{escape(course.source_status)}</span>"
    return f"""
      <tr>
        <td class="text_left">
          <div class="p-subject">
            <a href="{escape(seosan.seosan_detail_url(course.identity, course.page))}">
              {escape(course.list_title)}
            </a>
            <span class="p-badge type5">{escape(course.target)}</span>
            <span class="p-badge type4">{escape(course.fee)}</span>
          </div>
          <div class="detail_info">
            <span>교육기간 : {escape(course.event_period)}</span>
            <span class="info_seperate">|</span>
            <span>교육요일 : {escape(course.day)}</span>
            <span class="info_seperate">|</span>
            <span>교육시간 : {escape(course.time)}</span>
            <span class="info_seperate">|</span>
            <span>접수기간 : {escape(course.apply_period)}</span>
            <span class="info_seperate">|</span>
            <span>접수방식 : {escape(course.method)}</span>
            <span class="info_seperate">|</span>
            <span>모집인원 : {course.capacity}명</span>
            <span>온라인 모집현황</span>
            <span class="info_seperate">|</span>
            <span>정원 : {course.online_capacity}명</span>
            <span class="info_seperate">|</span>
            <span>접수완료 : {course.applicants}명</span>
            <span class="info_seperate">|</span>
            <span>대기 : {course.waitlist}명</span>
          </div>
        </td>
        <td class="reserve_status"><span class="btn">{control}</span></td>
      </tr>
    """


def _list_html(
    courses: list[Course], total: int, page: int, *, application_identity: str | None = None
) -> str:
    last = (total + seosan.SEOSAN_PAGE_SIZE - 1) // seosan.SEOSAN_PAGE_SIZE
    rows = "".join(
        _list_row(course, application_identity=application_identity if index == 0 else None)
        for index, course in enumerate(courses)
    )
    return f"""
      <html><body><div id="template4">
        <form id="searchForm" name="searchForm" method="post"
              action="./selectEdcAtrCourseListU.do">
          <input type="hidden" name="key" value="2">
          <input type="hidden" name="cl1No" value="">
          <input type="hidden" name="cl2No" value="">
          <input type="hidden" name="searchInsttCode" value="">
          <select name="pageUnit">
            <option value="10">10 페이지</option><option value="20">20 페이지</option>
            <option value="30">30 페이지</option><option value="40">40 페이지</option>
            <option value="50" selected="selected">50 페이지</option>
          </select>
          <input name="searchKrwd" value="">
        </form>
        <div class="bbs_count">
          <span>총 게시물 <strong>{total}</strong> 개</span>,
          <span>페이지 <strong>{page}</strong> / {last}</span>
        </div>
        <table class="bbs_default list reserve_education">
          <caption>강좌 목록 - 강좌명, 교육기간, 접수기간, 신청/모집, 상태 정보 제공</caption>
          <thead><tr><th>강좌 정보</th><th>상태</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div></body></html>
    """


def _detail_html(
    course: Course,
    *,
    title: str | None = None,
    institution: str | None = None,
    application_identity: str | None = None,
    extra_field: bool = False,
) -> str:
    application = course.source_status in {"접수중", "대기접수", "대기신청"}
    if application:
        href = escape(_application_url(course, application_identity))
        controls = f"""
          <div class="clearfix">
            <div class="fleft"><a href="{href}">신청</a></div>
            <div class="fright"><span><a href="{href}">{escape(course.source_status)}</a></span></div>
          </div>
        """
    elif course.stale_detail_application:
        href = escape(_application_url(course, application_identity))
        controls = f"""
          <div class="clearfix">
            <div class="fleft"><a href="#n" onclick="return false;">신청마감</a></div>
            <div class="fright"><span><a href="{href}">접수중</a></span></div>
          </div>
        """
    else:
        controls = (
            '<div class="clearfix"><div class="fright"><span>'
            f"{escape(course.source_status)}</span></div></div>"
        )
    fields: list[tuple[str, str]] = [
        ("기관명", institution or course.institution),
        ("강좌명", title or course.title),
        ("기수", course.term),
        ("접수기간", course.apply_period),
        ("접수방식", course.method),
        ("모집인원", f"{course.capacity}명 (온라인모집:{course.online_capacity}명)"),
        ("선발방식", "선착순"),
        ("대기인원", "5명"),
        ("강사명", "비공개 테스트강사"),
        ("교육기간", course.event_period),
        ("총교육일", "2회"),
        ("교육시간", f"{course.day} {course.time}"),
        ("교육대상", course.target),
        ("수강료", course.detail_fee or course.fee),
        ("재료비", "0원"),
        ("교육장소", course.venue),
        ("강의개요", "test@example.com 010-1234-5678 비공개 자유서술"),
        ("교재 및 참고자료", "비공개 교재 설명"),
        ("강의계획서", "강의계획서.hwpx 다운로드"),
        ("수강신청 유의사항", "신청자 개인정보를 입력하세요"),
    ]
    if extra_field:
        fields.append(("개인정보", "010-9999-9999"))
    rendered = []
    for index in range(0, len(fields), 2):
        rendered.append(
            "<tr>"
            + "".join(
                f'<th scope="row">{escape(label)}</th><td>{escape(value)}</td>'
                for label, value in fields[index : index + 2]
            )
            + "</tr>"
        )
    return f"""
      <html><body><div id="template4">
        <h3>강좌상세정보</h3>{controls}
        <table class="table"><caption>수강생관리 강좌상세</caption>
          <tbody>{''.join(rendered)}</tbody>
        </table>
      </div></body></html>
    """


def _courses() -> list[Course]:
    current = [
        Course(
            "1000",
            "AI 시민강좌",
            1,
            "2026-08-01 ~ 2026-08-31",
            "2026-07-01 ~ 2026-07-31",
            source_status="접수중",
            institution="평생학습관",
            venue="서산시평생학습관",
        ),
        Course(
            "999",
            "동화교실",
            1,
            "2026-08-03 ~ 2026-08-03",
            "2026-07-10 ~ 2026-08-02",
            source_status="접수예정",
            institution="어린이도서관",
            term="8월",
            target="2017~2018년",
            venue="어린이도서관 체험동화방",
            method="온라인",
        ),
        Course(
            "998",
            "도서관 인문학",
            1,
            "2026-07-23 ~ 2026-07-30",
            "2026-06-01 ~ 2026-06-30",
            source_status="신청마감",
            institution="시립도서관",
            fee="유료",
            detail_fee="유료 32,000원",
            venue="",
            stale_list_application=True,
            stale_detail_application=True,
        ),
    ]
    expired = [
        Course(
            str(997 - offset),
            f"지난 서산교육 {offset}",
            1 if offset < 47 else 2,
            "2025-01-01 ~ 2025-01-31",
            "2024-12-01 ~ 2024-12-31",
        )
        for offset in range(50)
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
        forbidden = {
            seosan.SEOSAN_APPLICATION_PATH,
            *seosan.SEOSAN_LOGIN_PATHS,
            "/total/fileDown.do",
            "/total/selectEdcAtrListM.do",
        }
        if parsed.path in forbidden:
            raise AssertionError("private/application/download endpoint must never be requested")
        if parsed.path == seosan.SEOSAN_DETAIL_PATH:
            identity = query["edcCourseNo"][0]
            course = next(item for item in self.courses if item.identity == identity)
            if course.event_period.startswith("2025"):
                raise AssertionError("expired detail must never be requested")
            kwargs = {}
            if self.mode == "detail_title_drift" and identity == "1000":
                kwargs["title"] = "다른 강좌"
            if self.mode == "unknown_institution" and identity == "1000":
                kwargs["institution"] = "미감사 기관"
            if self.mode == "extra_sensitive_field" and identity == "1000":
                kwargs["extra_field"] = True
            if self.mode == "detail_application_identity_drift" and identity == "1000":
                kwargs["application_identity"] = "9999"
            response_url = "https://evil.example/detail" if self.mode == "response_host_drift" else url
            return Response(response_url, _detail_html(course, **kwargs))

        assert parsed.path == seosan.SEOSAN_LIST_PATH
        page = int(query["pageIndex"][0])
        self.list_calls[page] += 1
        start = (page - 1) * seosan.SEOSAN_PAGE_SIZE
        courses = self.courses[start : start + seosan.SEOSAN_PAGE_SIZE]
        if self.mode == "boundary_drift" and page == 1 and self.list_calls[page] > 1:
            courses = [replace(courses[0], title="경계에서 바뀐 강좌"), *courses[1:]]
        if self.mode == "sentinel_resumed" and page == 3 and self.list_calls[page] > 1:
            courses = [replace(self.courses[0], page=3)]
        if self.mode == "duplicate_identity" and page == 2 and courses:
            courses = [replace(courses[0], identity=self.courses[0].identity), *courses[1:]]
        application_identity = (
            "9999" if self.mode == "list_application_identity_drift" and page == 1 else None
        )
        return Response(
            url,
            _list_html(
                courses,
                len(self.courses),
                page,
                application_identity=application_identity,
            ),
        )


def _target(**changes: str) -> dict[str, str]:
    target = {"provider": seosan.SEOSAN_PROVIDER, "url": seosan.SEOSAN_CANONICAL_URL}
    target.update(changes)
    return target


def _collect(fixture: Fixture, **kwargs):
    options = {
        "today": "2026-07-23",
        "timeout": 5,
        "max_pages": 6,
        "detail_limit": 3,
        "session_factory": DummySession,
        "fetcher": fixture,
    }
    options.update(kwargs)
    return seosan.collect(_target(), **options)


@pytest.mark.parametrize(
    "provider,url",
    [
        (seosan.SEOSAN_REVIEW_PROVIDER, seosan.SEOSAN_CANONICAL_URL),
        (seosan.SEOSAN_PROVIDER, seosan.SEOSAN_LEARNING_HOME_URL),
        (seosan.SEOSAN_PROVIDER, seosan.SEOSAN_CHUNGNAM_DIRECTORY_URL),
        (seosan.SEOSAN_PROVIDER, seosan.SEOSAN_LEGACY_PARTITION_URLS[0]),
        (seosan.SEOSAN_PROVIDER, seosan.SEOSAN_CANONICAL_URL + "&pageIndex=1"),
        (seosan.SEOSAN_PROVIDER, seosan.SEOSAN_CANONICAL_URL + "#courses"),
        (seosan.SEOSAN_PROVIDER, seosan.SEOSAN_CANONICAL_URL.replace("https://", "http://")),
        (
            seosan.SEOSAN_PROVIDER,
            seosan.SEOSAN_CANONICAL_URL.replace("total.seosan.go.kr", "evil.example"),
        ),
        (
            seosan.SEOSAN_PROVIDER,
            seosan.SEOSAN_CANONICAL_URL.replace("https://", "https://evil@"),
        ),
    ],
)
def test_exact_canonical_target_rejects_candidates_aliases_and_unsafe_urls(
    provider: str, url: str
) -> None:
    assert not seosan.is_target({"provider": provider, "url": url})


def test_incumbent_owner_candidate_and_canonical_audit_are_explicit() -> None:
    assert seosan.is_target(_target())
    assert seosan.SEOSAN_PROVIDER == "SEOSAN_WELFARE_TOTAL_RESERVATION"
    assert seosan.SEOSAN_HOME_CANDIDATE_ID == "MUNI_IR_B588388CAD68"
    assert seosan.SEOSAN_CHUNGNAM_CANDIDATE_ID == "MUNI_IR_55277C6BA9C1"
    assert seosan.SEOSAN_REVIEW_PROVIDER == "MUNI_WWW_SEOSAN_GO_KR_D18127D4"
    assert seosan.SEOSAN_CANONICAL_DERIVED_PROVIDER_NOT_TO_CREATE != seosan.SEOSAN_PROVIDER
    audit = seosan.SEOSAN_OWNER_BOUNDARY_AUDIT
    assert audit["reviewed_lifelong_home"]["owner"] == seosan.SEOSAN_PROVIDER
    assert "reuse_incumbent" in audit["canonical_complete_education_ledger"]["decision"]
    assert audit["canonical_complete_education_ledger"]["new_provider_created"] is False
    assert "separate_provincial" in audit["chungnam_municipality_profile"]["decision"]
    assert len(seosan.SEOSAN_LEGACY_PARTITION_URLS) == 9
    with pytest.raises(ValueError):
        seosan.seosan_list_url(0)
    with pytest.raises(ValueError):
        seosan.seosan_detail_url("../1000")


def test_complete_snapshot_pagination_details_branches_and_zero_private_endpoints() -> None:
    fixture = Fixture()
    rows, parser, meta = _collect(fixture)

    assert parser == seosan.SEOSAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == meta["source_rows"] == 53
    assert meta["pages"] == meta["data_pages"] == 2
    assert meta["page_counts"] == {1: 50, 2: 3}
    assert meta["empty_sentinel_page"] == 3
    assert meta["list_requests"] == 6
    assert meta["boundary_recheck_count"] == 3
    assert meta["boundary_rechecks"] == {"1": True, "2": True, "3": True}
    assert fixture.list_calls == Counter({1: 2, 2: 2, 3: 2})
    assert meta["current_source_count"] == meta["current_education_count"] == 3
    assert meta["expired_count"] == 50
    assert meta["detail_pages"] == meta["detail_verified"] == 3
    assert meta["logical_requests"] == meta["physical_requests"] == 9
    assert meta["request_retry_count"] == 0
    assert meta["source_status_counts"] == {"접수중": 1, "접수예정": 1, "신청마감": 1}
    assert meta["status_counts"] == {"OPEN": 1, "SCHEDULED": 1, "CLOSED": 1}
    assert meta["branch_counts"] == {
        "평생학습관": 1,
        "어린이도서관": 1,
        "시립도서관": 1,
    }
    assert meta["application_control_count"] == 1
    assert meta["detail_application_control_count"] == 3
    assert meta["application_endpoint_requests"] == 0
    assert meta["login_endpoint_requests"] == 0
    assert meta["applicant_endpoint_requests"] == 0
    assert meta["attachment_endpoint_requests"] == 0
    assert meta["download_endpoint_requests"] == 0
    assert meta["pii_endpoint_requests"] == 0
    assert meta["pii_values_persisted"] == 0
    assert meta["sensitive_detail_fields_discarded"] == 3
    assert meta["free_text_fields_discarded"] == 9
    assert meta["attachment_fields_discarded"] == 3
    assert meta["semantic_duplicate_count"] == 0
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert len(rows) == 3

    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    open_row = by_id["1000"]
    assert open_row["branch"] == "평생학습관"
    assert open_row["branch_code"] == "SEOSAN_LIFELONG_LEARNING"
    assert open_row["status"] == "OPEN"
    assert open_row["application_type"] == "ONLINE_RESERVATION_LOGIN_REQUIRED"
    assert open_row["reservation_available"] is True
    assert parse_qs(urlparse(open_row["application_url"]).query)["edcCourseNo"] == ["1000"]

    scheduled = by_id["999"]
    assert scheduled["title"] == "동화교실(8월)"
    assert scheduled["branch"] == "어린이도서관"
    assert scheduled["status"] == "SCHEDULED"
    assert scheduled["application_url"] == ""

    closed = by_id["998"]
    assert closed["branch"] == "시립도서관"
    assert closed["status"] == "CLOSED"
    assert closed["application_type"] == "INFO_ONLY"
    assert closed["fee"] == "유료 32,000원"
    assert closed["venue"] == ""
    assert closed["raw_fields"]["venue_basis"] == "official detail 교육장소 blank; no venue inferred"

    payload = repr(rows)
    assert "비공개 테스트강사" not in payload
    assert "010-1234-5678" not in payload
    assert "test@example.com" not in payload
    assert "강의계획서.hwpx" not in payload
    assert "신청자 개인정보" not in payload
    assert all(row["municipality_code"] == "4421000000" for row in rows)
    assert all(row["provider"] == seosan.SEOSAN_PROVIDER for row in rows)
    requested_paths = Counter(urlparse(url).path for url in fixture.calls)
    assert requested_paths == {
        seosan.SEOSAN_LIST_PATH: 6,
        seosan.SEOSAN_DETAIL_PATH: 3,
    }


def test_waiting_list_status_accepts_generic_open_detail_control() -> None:
    course = replace(
        _courses()[0],
        identity="1001",
        source_status="대기접수",
    )
    detail_html = _detail_html(course).replace(
        ">대기접수</a>",
        ">접수중</a>",
        1,
    )
    root = seosan.BeautifulSoup(detail_html, "html.parser").select_one(
        "#template4"
    )
    assert root is not None
    listed = {
        "identity": course.identity,
        "page": course.page,
        "status": "WAITING",
        "source_status": "대기접수",
        "application_url": _application_url(course),
    }
    assert seosan._detail_application_contract(root, listed) == 2


def test_request_caps_fail_closed_before_partial_or_detail_walk() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture, max_pages=5)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "allows 5 of 6" in meta["configured_collection_error"]
    assert fixture.list_calls == Counter({1: 1})

    fixture = Fixture()
    rows, _, meta = _collect(fixture, detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "allows 2 of 3" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0
    assert fixture.list_calls == Counter({1: 1, 2: 1, 3: 1})


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("boundary_drift", "boundary stability recheck changed"),
        ("sentinel_resumed", "expected 0 rows"),
        ("duplicate_identity", "duplicate identities across declared pages"),
        ("list_application_identity_drift", "course identity/path drift"),
        ("detail_title_drift", "list/detail identity drift"),
        ("unknown_institution", "unaudited official institution"),
        ("extra_sensitive_field", "unaudited detail fields"),
        ("detail_application_identity_drift", "course identity/path drift"),
        ("response_host_drift", "official response URL changed"),
    ],
)
def test_contract_privacy_and_stability_drifts_fail_closed(mode: str, message: str) -> None:
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
def test_live_seosan_complete_snapshot() -> None:
    rows, parser, meta = seosan.collect(
        _target(),
        today="2026-07-23",
        timeout=40,
        max_pages=200,
        detail_limit=200,
    )
    assert parser == seosan.SEOSAN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] >= 79
    assert meta["current_source_count"] >= 79
    assert meta["returned_count"] == len(rows)
    assert meta["application_endpoint_requests"] == 0
    assert meta["login_endpoint_requests"] == 0
    assert meta["attachment_endpoint_requests"] == 0
    assert meta["pii_values_persisted"] == 0
    assert meta["full_snapshot_validated"] is True
