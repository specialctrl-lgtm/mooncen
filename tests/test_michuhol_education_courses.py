from __future__ import annotations

from dataclasses import dataclass
import math
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_michuhol as michuhol


@dataclass
class Target:
    provider: str = michuhol.MICHUHOL_PROVIDER
    name: str = "미추홀구 통합예약 교육강좌"
    branch: str = "인천광역시 미추홀구"
    url: str = michuhol.MICHUHOL_URL


@dataclass
class DummySession:
    number: int
    calls: int = 0
    closed: bool = False

    def close(self) -> None:
        self.closed = True


def _course(
    identity: str,
    *,
    title: str | None = None,
    category: str = "주민자치교육",
    institution: str = "주민자치센터",
    dong: str = "도화2.3동",
    status: str = "신청하기",
    mode: str = "INTERNAL_ONLINE",
    apply_period: str = "2099-06-01~2099-06-30",
    education_period: str = "2099-07-01~2099-08-01",
    target: str = "회원, 비회원",
    capacity: str = "20 / 3 / 1",
    schedule: str = "10:00 - 12:00",
    room: str = "행정복지센터 3층",
) -> dict[str, str]:
    return {
        "identity": identity,
        "title": title or f"교육 강좌 {identity}",
        "category": category,
        "institution": institution,
        "dong": dong,
        "status": status,
        "mode": mode,
        "apply_period": apply_period,
        "education_period": education_period,
        "target": target,
        "capacity": capacity,
        "schedule": schedule,
        "room": room,
    }


def _application_anchor(course: dict[str, str], *, detail: bool = False) -> str:
    identity = course["identity"]
    mode = course["mode"]
    classes = ' class="btn btn-s1" title="신청하기"' if detail else ""
    if mode == "INTERNAL_ONLINE":
        return (
            f'<a{classes} href="step2.do?edu_sq={identity}'
            '&amp;backUrl=list&amp;search=">'
            "신청하기</a>"
        )
    if mode == "EXTERNAL_ONLINE":
        return (
            f'<a{classes} href="{michuhol.MICHUHOL_EXTERNAL_APPLICATION_URL}">'
            "신청하기</a>"
        )
    if mode == "OFFLINE":
        return (
            f'<a{classes} href="javascript:;" '
            "onclick=\"alert('오프라인 신청만 가능한 강좌입니다.');\">"
            "신청하기</a>"
        )
    return ""


def _list_row(course: dict[str, str]) -> str:
    application = _application_anchor(course)
    status = application or f"<span>{course['status']}</span>"
    return f"""
    <tr>
      <td>{course['category']}</td>
      <td><a href="step1.do?sq={course['identity']}&amp;backUrl=list&amp;search=">
        {course['title']}
      </a></td>
      <td>{course['institution']}</td>
      <td>{course['apply_period']}</td>
      <td>{course['education_period']}</td>
      <td>{course['target']}</td>
      <td>{course['capacity']}</td>
      <td>{status}</td>
    </tr>
    """


def _list_page(
    page: int,
    total: int,
    courses: list[dict[str, str]],
    *,
    sentinel: bool = False,
) -> str:
    last = max(1, math.ceil(total / michuhol.MICHUHOL_PAGE_SIZE))
    active = "" if sentinel else (
        f'<strong class="paging-link active" '
        f'onclick="fnList({{\'page\' : \'{page}\'}});">{page}</strong>'
    )
    return f"""
    <html><body>
      <span class="b">{total:,}개</span>
      <table class="c-table-s1">
        <thead><tr>
          <th>분류</th><th>강좌명</th><th>기관</th><th>접수기간</th>
          <th>교육기간</th><th>대상</th><th>정원/예약/대기</th><th>상태</th>
        </tr></thead>
        <tbody>{''.join(_list_row(course) for course in courses)}</tbody>
      </table>
      <div class="paging">
        <a onclick="fnList({{'page' : '1'}});">처음</a>
        {active}
        <a onclick="fnList({{'page' : '{last}'}});">마지막</a>
      </div>
    </body></html>
    """


def _detail_page(
    course: dict[str, str],
    *,
    title: str | None = None,
    mode: str | None = None,
) -> str:
    detail_course = dict(course)
    if mode is not None:
        detail_course["mode"] = mode
    total = course["capacity"].split("/", 1)[0].strip()
    dong = (
        f"<dl><dt>동 이름</dt><dd>{course['dong']}</dd></dl>"
        if course["dong"]
        else ""
    )
    application = _application_anchor(detail_course, detail=True)
    return f"""
    <html><body>
      <div class="detailinfo">
        <div class="infohead">{title or course['title']}</div>
        <dl><dt>분류</dt><dd>{course['category']}</dd></dl>
        <dl><dt>기관</dt><dd>{course['institution']}</dd></dl>
        {dong}
        <dl><dt>대상</dt><dd>{course['target']}</dd></dl>
        <dl><dt>정원</dt><dd>{total}명</dd></dl>
        <dl><dt>접수기간</dt><dd>{course['apply_period']} 18:00</dd></dl>
        <dl><dt>수강기간</dt><dd>{course['education_period']} (매주 화)</dd></dl>
        <dl><dt>교육시간</dt><dd>{course['schedule']}</dd></dl>
        <dl><dt>강의실</dt><dd>{course['room']}</dd></dl>
        <dl><dt>문의처</dt><dd>032-000-0000</dd></dl>
        <dl><dt>모집방법</dt><dd>선착순</dd></dl>
        <dl><dt>수강료</dt><dd>무료</dd></dl>
        <dl><dt>재료비</dt><dd>5,000원</dd></dl>
      </div>
      <div class="classcon"><div>상세한 강좌 소개</div><div>강의계획</div></div>
      <div class="btn-wrap">{application}<a href="list.do">목록</a></div>
    </body></html>
    """


def _pages(courses: list[dict[str, str]]) -> dict[int, str]:
    total = len(courses)
    last = max(1, math.ceil(total / michuhol.MICHUHOL_PAGE_SIZE))
    result: dict[int, str] = {}
    for page in range(1, last + 1):
        start = (page - 1) * michuhol.MICHUHOL_PAGE_SIZE
        result[page] = _list_page(
            page,
            total,
            courses[start : start + michuhol.MICHUHOL_PAGE_SIZE],
        )
    result[last + 1] = _list_page(last + 1, total, [], sentinel=True)
    return result


def _fetcher(
    pages: dict[int, str],
    details: dict[str, str],
    calls: list[str] | None = None,
):
    def fetch(session: DummySession, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        session.calls += 1
        if calls is not None:
            calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if parsed.path == michuhol.MICHUHOL_LIST_PATH:
            page = int((query.get("page") or ["1"])[0])
            return BeautifulSoup(pages[page], "lxml")
        if parsed.path == michuhol.MICHUHOL_DETAIL_PATH:
            return BeautifulSoup(details[query["sq"][0]], "lxml")
        raise AssertionError(url)

    return fetch


def _collect(
    courses: list[dict[str, str]],
    *,
    details: dict[str, str] | None = None,
    pages: dict[int, str] | None = None,
    calls: list[str] | None = None,
    sessions: list[DummySession] | None = None,
    **kwargs,
):
    actual_pages = pages or _pages(courses)
    actual_details = details or {
        course["identity"]: _detail_page(course)
        for course in courses
        if not course["education_period"].startswith("2020-")
    }
    session_list = sessions if sessions is not None else []

    def session_factory() -> DummySession:
        current = DummySession(len(session_list) + 1)
        session_list.append(current)
        return current

    return michuhol.collect_michuhol_education_courses(
        Target(),
        timeout=7,
        max_pages=kwargs.pop("max_pages", len(actual_pages)),
        detail_limit=kwargs.pop("detail_limit", len(actual_details)),
        fetcher=_fetcher(actual_pages, actual_details, calls),
        session_factory=session_factory,
        today=kwargs.pop("today", "2099-06-15"),
        **kwargs,
    )


def _mixed_courses() -> list[dict[str, str]]:
    return [
        _course("1001", title="온라인 주민 강좌"),
        _course(
            "1002",
            title="청소년 심리상담",
            category="청소년 상담",
            institution="청소년상담복지센터",
            dong="",
            mode="EXTERNAL_ONLINE",
        ),
        _course(
            "1003",
            title="오프라인 주민 강좌",
            dong="주안1동",
            mode="OFFLINE",
        ),
        _course(
            "1004",
            title="교육 진행 강좌",
            category="인문교양교육",
            institution="평생학습관",
            dong="",
            status="교육진행중",
            mode="NONE",
        ),
        _course(
            "42",
            title="종료된 강좌",
            institution="",
            dong="",
            status="교육완료",
            mode="NONE",
            apply_period="2020-01-10~2020-01-01",
            education_period="2020-02-01~2020-02-10",
        ),
    ]


def test_collects_complete_current_snapshot_and_all_application_modes() -> None:
    courses = _mixed_courses()
    calls: list[str] = []
    sessions: list[DummySession] = []

    rows, parser, meta = _collect(courses, calls=calls, sessions=sessions)

    assert parser == michuhol.MICHUHOL_PARSER
    assert len(rows) == 4
    by_id = {row["raw_fields"]["identity"]: row for row in rows}
    assert by_id["1001"]["provider_course_id"].endswith(":education:1001")
    assert by_id["1001"]["branch"] == "도화2.3동"
    assert by_id["1001"]["application_url"] == michuhol.michuhol_application_url("1001")
    assert by_id["1001"]["reservation_available"] is True
    assert by_id["1002"]["branch"] == "청소년상담복지센터"
    assert by_id["1002"]["application_url"] == michuhol.MICHUHOL_EXTERNAL_APPLICATION_URL
    assert by_id["1003"]["branch"] == "주안1동"
    assert by_id["1003"]["application_type"] == "OFFLINE_RESERVATION"
    assert by_id["1003"]["reservation_available"] is False
    assert "application_url" not in by_id["1003"]
    assert by_id["1004"]["status"] == "CLOSED"
    assert by_id["1004"]["venue_name"] == "행정복지센터 3층"
    assert by_id["1004"]["description"] == "상세한 강좌 소개"
    assert meta["source_total"] == 5
    assert meta["source_pages"] == 1
    assert meta["page_counts"] == {1: 5, 2: 0}
    assert meta["expired_count"] == 1
    assert meta["historical_application_period_defect_count"] == 1
    assert meta["current_count"] == 4
    assert meta["detail_pages"] == 4
    assert meta["branch_count"] == 4
    assert meta["application_mode_counts"] == {
        "INTERNAL_ONLINE": 1,
        "EXTERNAL_ONLINE": 1,
        "OFFLINE": 1,
        "NONE": 1,
    }
    assert sum(meta["application_mode_counts"].values()) == meta["current_count"]
    assert meta["reservation_discovery_links"] == 2
    assert meta["duplicate_count"] == 0
    assert meta["duplicate_url_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["snapshot_complete"] is True
    assert len(sessions) == 2
    assert all(session.closed for session in sessions)
    assert not any("sq=42" in url for url in calls)


def test_resident_list_category_is_dong_and_membership_target_is_not_course_target() -> None:
    course = _course(
        "2001",
        category="도화1동",
        dong="도화1동",
        target="전체",
        mode="OFFLINE",
    )
    list_soup = BeautifulSoup(_list_page(1, 1, [course]), "lxml")
    rows, malformed = michuhol._parse_list_page(Target(), list_soup, page=1)
    assert malformed == 0
    detail_soup = BeautifulSoup(_detail_page(course), "lxml")
    for dl in detail_soup.select("div.detailinfo dl"):
        heading = dl.find("dt")
        value = dl.find("dd")
        if heading and value and heading.get_text(strip=True) == "분류":
            value.string = "주민자치교육"
        if heading and value and heading.get_text(strip=True) == "대상":
            value.string = "비회원"

    errors = michuhol._validate_detail(rows[0], detail_soup)

    assert errors == []
    assert rows[0]["category"] == "주민자치교육"
    assert rows[0]["branch"] == "도화1동"
    assert rows[0]["target"] == "전체"
    assert rows[0]["eligibility_raw"] == "비회원"
    assert rows[0]["raw_fields"]["resident_category_layout"] is True


def test_max_pages_must_include_the_post_boundary_sentinel() -> None:
    rows, _parser, meta = _collect(_mixed_courses(), max_pages=1)

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "1 of 2 required list requests" in meta["configured_collection_error"]


def test_detail_limit_is_fail_closed_before_partial_detail_fetches() -> None:
    calls: list[str] = []

    rows, _parser, meta = _collect(
        _mixed_courses(), detail_limit=3, calls=calls
    )

    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert "3 of 4 required details" in meta["configured_collection_error"]
    assert not any(urlparse(url).path == michuhol.MICHUHOL_DETAIL_PATH for url in calls)


def test_source_total_page_count_and_sentinel_are_fail_closed() -> None:
    courses = _mixed_courses()
    too_short = _pages(courses)
    too_short[1] = _list_page(1, 5, courses[:-1])
    nonempty_sentinel = _pages(courses)
    nonempty_sentinel[2] = _list_page(2, 5, [courses[0]], sentinel=True)

    short_rows, _parser, short_meta = _collect(courses, pages=too_short)
    sentinel_rows, _parser, sentinel_meta = _collect(
        courses, pages=nonempty_sentinel
    )

    assert short_rows == []
    assert "first page row count" in short_meta["configured_collection_error"]
    assert sentinel_rows == []
    assert "sentinel page is not empty" in sentinel_meta["configured_collection_error"]


def test_duplicate_identity_and_canonical_url_fail_the_snapshot() -> None:
    first = _course("1001", title="첫 강좌")
    duplicate = _course("1001", title="다른 제목")

    rows, _parser, meta = _collect([first, duplicate])

    assert rows == []
    assert meta["duplicate_count"] == 1
    assert meta["duplicate_url_count"] == 1
    assert "duplicate course identities" in meta["configured_collection_error"]


def test_detail_title_and_application_mode_mismatches_fail_closed() -> None:
    course = _course("1001")
    bad_title = {"1001": _detail_page(course, title="다른 강좌")}
    bad_mode = {"1001": _detail_page(course, mode="OFFLINE")}

    title_rows, _parser, title_meta = _collect([course], details=bad_title)
    mode_rows, _parser, mode_meta = _collect([course], details=bad_mode)

    assert title_rows == []
    assert "detail title mismatch" in title_meta["configured_collection_error"]
    assert mode_rows == []
    assert "application mode mismatch" in mode_meta["configured_collection_error"]


def test_reversed_application_period_is_tolerated_only_after_course_end() -> None:
    current = _course(
        "1001",
        status="교육진행중",
        mode="NONE",
        apply_period="2099-06-30~2099-06-01",
    )

    rows, _parser, meta = _collect([current])

    assert rows == []
    assert meta["historical_application_period_defect_count"] == 0
    assert "current/future application period is reversed" in meta[
        "configured_collection_error"
    ]


def test_semantic_duplicates_and_post_validation_dedupe_are_fail_closed() -> None:
    first = _course("1001", title="같은 강좌")
    second = _course("1002", title="같은 강좌")

    semantic_rows, _parser, semantic_meta = _collect([first, second])
    deduped_rows, _parser, dedupe_meta = _collect(
        [first], dedupe_rows=lambda _rows: []
    )

    assert semantic_rows == []
    assert semantic_meta["semantic_duplicate_count"] == 1
    assert "semantic duplicate" in semantic_meta["configured_collection_error"]
    assert deduped_rows == []
    assert "dedupe changed complete row count" in dedupe_meta["configured_collection_error"]


def test_detail_sessions_rotate_below_the_managed_request_budget() -> None:
    courses = [
        _course(
            str(2000 + index),
            title=f"서로 다른 강좌 {index}",
            institution="평생학습관",
            dong="",
            status="교육진행중",
            mode="NONE",
        )
        for index in range(michuhol.MICHUHOL_DETAIL_SESSION_LIMIT + 1)
    ]
    sessions: list[DummySession] = []

    rows, _parser, meta = _collect(courses, sessions=sessions)

    assert len(rows) == 151
    assert meta["required_list_requests"] == 7
    assert meta["detail_pages"] == 151
    assert meta["sessions_created"] == 3
    assert [session.calls for session in sessions] == [7, 150, 1]
    assert all(session.closed for session in sessions)
    assert meta["snapshot_complete"] is True


def test_target_url_helpers_and_managed_injection_contract_are_strict() -> None:
    assert michuhol.is_michuhol_target(Target()) is True
    assert michuhol.is_michuhol_target(Target(provider="WRONG")) is False
    assert michuhol.is_michuhol_target(
        Target(url=michuhol.MICHUHOL_URL + "?organ_cd=001001")
    ) is False
    assert michuhol.michuhol_list_url("1") == michuhol.MICHUHOL_URL
    assert michuhol.michuhol_list_url("55").endswith("?page=55")
    assert michuhol.michuhol_list_url("../2") == ""
    assert michuhol.michuhol_detail_url("3527").endswith("?sq=3527")
    assert michuhol.michuhol_application_url("3527").endswith("?edu_sq=3527")
    assert michuhol.michuhol_detail_url("3527&evil=1") == ""
    assert michuhol._detail_identity(
        michuhol.MICHUHOL_URL,
        "step1.do?sq=3527&backUrl=list&search=",
    ) == ("3527", michuhol.michuhol_detail_url("3527"))
    assert michuhol._detail_identity(
        michuhol.MICHUHOL_URL,
        "step1.do?sq=3527&backUrl=https://evil.example&search=",
    ) == ("", "")
    assert michuhol._detail_identity(
        michuhol.michuhol_list_url(2),
        "step1.do?sq=3527&backUrl=list&search=eyJwYWdlIjoiMiJ9",
    ) == ("3527", michuhol.michuhol_detail_url("3527"))
    assert michuhol._detail_identity(
        michuhol.michuhol_list_url(3),
        "step1.do?sq=3527&backUrl=list&search=eyJwYWdlIjoiMiJ9",
    ) == ("", "")
    safe_application = BeautifulSoup(
        '<a href="step2.do?edu_sq=3527&amp;backUrl=list&amp;search=">신청</a>',
        "lxml",
    ).a
    unsafe_application = BeautifulSoup(
        '<a href="step2.do?edu_sq=3527&amp;backUrl=detail&amp;search=">신청</a>',
        "lxml",
    ).a
    assert michuhol._application_control(safe_application, "3527") == (
        "INTERNAL_ONLINE",
        michuhol.michuhol_application_url("3527"),
    )
    assert michuhol._application_control(unsafe_application, "3527") == (
        "INVALID",
        "",
    )

    rows, parser, meta = michuhol.collect_michuhol_education_courses(Target())

    assert rows == []
    assert parser == michuhol.MICHUHOL_PARSER
    assert "managed fetcher and session_factory" in meta["configured_collection_error"]
