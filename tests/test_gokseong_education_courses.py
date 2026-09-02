from __future__ import annotations

from dataclasses import dataclass
import inspect
import math
from typing import Any

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_gokseong as gokseong


@dataclass(frozen=True)
class GokmgTarget:
    provider: str = gokseong.GOKSEONG_GOKMG_PROVIDER
    name: str = "곡성교육포털 통합예약 교육"
    branch: str = gokseong.GOKSEONG_MUNICIPALITY_NAME
    url: str = gokseong.GOKSEONG_GOKMG_URL


@dataclass(frozen=True)
class JneLectureTarget:
    provider: str = gokseong.GOKSEONG_JNE_LECTURE_PROVIDER
    name: str = "곡성교육문화회관 수강 신청"
    branch: str = gokseong.GOKSEONG_JNE_BRANCH
    url: str = gokseong.GOKSEONG_JNE_LECTURE_URL


@dataclass(frozen=True)
class JneEducationTarget:
    provider: str = gokseong.GOKSEONG_JNE_EDUCATION_PROVIDER
    name: str = "곡성교육문화회관 독서문화행사"
    branch: str = gokseong.GOKSEONG_JNE_BRANCH
    url: str = gokseong.GOKSEONG_JNE_EDUCATION_URL


class DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FixtureFetcher:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, _session: DummySession, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        self.calls.append(url)
        return BeautifulSoup(self.pages[url], "lxml")


def _sessions() -> tuple[list[DummySession], Any]:
    created: list[DummySession] = []

    def factory() -> DummySession:
        current = DummySession()
        created.append(current)
        return current

    return created, factory


def test_gokmg_accepts_only_the_two_identity_bound_official_application_routes() -> None:
    source = gokseong._GOKMG_SOURCE_BY_CODE["foundation"]
    legacy = (
        "https://www.gokmg.or.kr/edu/educationMemberForm.es?"
        "mid=a10301000000&category=foundation&edu_seq=677&seq=5001"
    )
    current = (
        "https://www.gokmg.or.kr/edu/educationMemberForm.es?"
        "mid=a10301000000&target=&educ_cg=&edu_seq=677&seq=0"
    )

    assert gokseong._gokmg_application_url(source, "677", legacy) == legacy
    assert gokseong._gokmg_application_url(source, "677", current) == current
    assert not gokseong._gokmg_application_url(
        source,
        "677",
        current + "&admin=true",
    )
    assert not gokseong._gokmg_application_url(
        source,
        "678",
        current,
    )


def _gokmg_program(
    source_code: str,
    number: int,
    identity: str,
    title: str,
    *,
    current: bool,
    status: str,
) -> dict[str, Any]:
    source = next(
        source
        for source in gokseong.GOKSEONG_GOKMG_SOURCES
        if source.code == source_code
    )
    period = (
        "2099.07.01 ~ 2099.08.31 10:00~12:00"
        if current
        else "2020.01.01 ~ 2020.02.01 10:00~12:00"
    )
    apply_period = (
        "2099.06.01 09:00 ~ 2099.06.30 18:00"
        if current
        else "2019.12.01 09:00 ~ 2019.12.31 18:00"
    )
    category = {
        "foundation": "미래교육재단",
        "agency": "청소년기관",
        "school": "미래교육재단",
    }[source_code]
    return {
        "source": source,
        "number": number,
        "identity": identity,
        "title": title,
        "category": category,
        "target": "곡성군민",
        "period": period,
        "apply_period": apply_period,
        "status": status,
        "capacity": "3/20",
        "waitlist": "1/5",
    }


def _gokmg_row(program: dict[str, Any]) -> str:
    source = program["source"]
    detail = gokseong.gokmg_detail_url(source.code, program["identity"])
    return f"""
    <tr>
      <td>{program['number']}</td><td class="thum"><img alt="{program['title']}"></td>
      <td class="program"><a href="{detail}">
        <span class="title"><span class="cate">{program['category']}</span>
          <strong>{program['title']}</strong></span>
        <span class="item object"><strong>대상</strong><span>{program['target']}</span></span>
        <span class="item edu-period"><strong>교육기간</strong><span>{program['period']}</span></span>
        <span class="item appl-period"><strong>접수기간</strong><span>{program['apply_period']}</span></span>
      </a></td>
      <td>선착순</td><td>{program['capacity']}</td><td>{program['waitlist']}</td>
      <td><span class="type">{program['status']}</span></td>
    </tr>
    """


def _gokmg_list_page(
    source_code: str,
    rows: list[dict[str, Any]],
    *,
    total: int,
    page: int,
    last: int,
) -> str:
    source = next(
        source
        for source in gokseong.GOKSEONG_GOKMG_SOURCES
        if source.code == source_code
    )
    body = "".join(_gokmg_row(row) for row in rows)
    if not body:
        body = '<tr><td colspan="7"><p class="no_result nodata">해당되는 교육이 없습니다.</p></td></tr>'
    return f"""
    <html><head><title>{source.name} | 통합예약 : 곡성교육포털</title></head><body>
      <div class="board_info">전체 {total}건 페이지 {page} / {last}</div>
      <table class="tstyle_list"><thead><tr>
        <th>번호</th><th>프로그램명</th><th>선정방법</th><th>신청/정원</th>
        <th>대기/대기정원</th><th>진행상태</th>
      </tr></thead><tbody>{body}</tbody></table>
    </body></html>
    """


def _basic_detail(
    program: dict[str, Any],
    *,
    venue: str,
    include_apply_period: bool = True,
) -> str:
    apply = (
        f"<li><strong>신청기간</strong><span>{program['apply_period']}</span></li>"
        if include_apply_period
        else ""
    )
    return f"""
    <div class="basic"><ul class="item">
      <li class="title"><div class="state">
        <span class="type">{program['status']}</span>
        <span class="type category">{program['category']}</span>
      </div><strong>{program['title']}</strong></li>
      <li><strong>교육기간</strong><span>{program['period']}</span></li>
      <li><strong>교육장소</strong><span>{venue}</span></li>
      <li><strong>교육대상</strong><span>{program['target']}</span></li>
      {apply}
      <li><strong>예약인원</strong><span>신청 3명 / 정원 20명</span></li>
      <li><strong>대기인원</strong><span>대기 1명 / 대기정원 5명</span></li>
      <li><strong>접수담당</strong><span>곡성 담당자</span></li>
    </ul></div>
    """


def _gokmg_schedule_table(
    program: dict[str, Any],
    schedules: list[dict[str, str]],
    *,
    with_round_title: bool,
) -> str:
    first_header = "<th>회차명</th>" if with_round_title else ""
    wait_header = "<th>대기 / 대기정원</th>"
    rows: list[str] = []
    source = program["source"]
    for index, schedule in enumerate(schedules, start=1):
        first_cell = f"<td>{schedule['title']}</td>" if with_round_title else ""
        if schedule["status"] == "접수중":
            app = gokseong.gokmg_detail_url(source.code, program["identity"]).replace(
                "/educationView.es", "/educationMemberForm.es"
            ) + f"&seq={5000 + index}"
            state = f'<a data-label="접수중" href="{app}">신청하기</a>'
        else:
            state = f'<span data-label="{schedule["status"]}">{schedule["status"]}</span>'
        rows.append(
            f"<tr>{first_cell}<td>{schedule['period']}</td>"
            f"<td>{schedule['apply_period']}</td><td>3명 / 20명</td>"
            f"<td>1명 / 5명</td><td>{state}</td></tr>"
        )
    return f"""
      <table class="tstyle_list"><thead><tr>{first_header}<th>교육기간</th>
        <th>접수기간</th><th>신청 / 정원</th>{wait_header}<th>진행상태</th>
      </tr></thead><tbody>{''.join(rows)}</tbody></table>
    """


def _gokmg_detail(
    program: dict[str, Any],
    *,
    venue: str,
    schedules: list[dict[str, str]] | None,
    with_round_title: bool = True,
    include_apply_period: bool = True,
) -> str:
    table = (
        _gokmg_schedule_table(
            program, schedules, with_round_title=with_round_title
        )
        if schedules is not None
        else ""
    )
    return f"""
    <html><body><div class="board_view type2">
      {_basic_detail(program, venue=venue, include_apply_period=include_apply_period)}
      <div class="detail">{table}<div class="edu_conts">{program['title']} 상세 교육내용</div>
        <span class="place-info">전남광주통합특별시 곡성군 테스트로 1</span>
      </div>
    </div></body></html>
    """


def _gokmg_fixture() -> tuple[dict[str, str], list[dict[str, Any]]]:
    foundation: list[dict[str, Any]] = [
        _gokmg_program(
            "foundation",
            13,
            "103",
            "2026년 평생학습공동체 신청 모집 공고 안내",
            current=True,
            status="접수마감",
        ),
        _gokmg_program(
            "foundation",
            12,
            "101",
            "현재 장인 교육",
            current=True,
            status="접수중",
        ),
        _gokmg_program(
            "foundation",
            11,
            "102",
            "테스트 페이지",
            current=True,
            status="접수마감",
        ),
    ]
    foundation.extend(
        _gokmg_program(
            "foundation",
            number,
            str(1000 + number),
            f"지난 교육 {number}",
            current=False,
            status="접수마감",
        )
        for number in range(10, 0, -1)
    )
    agency = [
        _gokmg_program(
            "agency",
            1,
            "201",
            "현재 기관 문의 교육",
            current=True,
            status="별도문의",
        )
    ]
    school = [
        _gokmg_program(
            "school",
            1,
            "301",
            "현재 학교 교육",
            current=True,
            status="접수예정",
        )
    ]
    by_source = {
        "foundation": foundation,
        "agency": agency,
        "school": school,
    }
    pages: dict[str, str] = {}
    for source_code, programs in by_source.items():
        total = len(programs)
        last = max(1, math.ceil(total / gokseong.GOKSEONG_GOKMG_PAGE_SIZE))
        for page in range(1, last + 1):
            start = (page - 1) * gokseong.GOKSEONG_GOKMG_PAGE_SIZE
            page_rows = programs[start : start + gokseong.GOKSEONG_GOKMG_PAGE_SIZE]
            pages[gokseong.gokmg_list_url(source_code, page)] = _gokmg_list_page(
                source_code,
                page_rows,
                total=total,
                page=page,
                last=last,
            )
        pages[gokseong.gokmg_list_url(source_code, last + 1)] = _gokmg_list_page(
            source_code,
            [],
            total=total,
            page=last + 1,
            last=last,
        )

    foundation_schedules = [
        {
            "title": "1회차 / 토탈공예",
            "period": "2099.07.01 10:00 ~ 2099.08.01 12:00",
            "apply_period": "2099.06.01 09:00 ~ 2099.06.30 18:00",
            "status": "접수중",
        },
        {
            "title": "1회차 / 목공예",
            "period": "2099.07.02 10:00 ~ 2099.08.02 12:00",
            "apply_period": "2099.06.01 09:00 ~ 2099.06.30 18:00",
            "status": "접수마감",
        },
    ]
    school_schedules = [
        {
            "title": "0회차 / 곡성초 / 1학년",
            "period": "2099.07.03 10:00 ~ 2099.08.03 12:00",
            "apply_period": "2099.06.01 09:00 ~ 2099.06.30 18:00",
            "status": "접수예정",
        },
        {
            "title": "0회차 / 옥과초 / 1학년",
            "period": "2099.07.04 10:00 ~ 2099.08.04 12:00",
            "apply_period": "2099.06.01 09:00 ~ 2099.06.30 18:00",
            "status": "접수예정",
        },
    ]
    pages[gokseong.gokmg_detail_url("foundation", "101")] = _gokmg_detail(
        foundation[1],
        venue="강좌별 상이",
        schedules=foundation_schedules,
    )
    pages[gokseong.gokmg_detail_url("agency", "201")] = _gokmg_detail(
        agency[0],
        venue="곡성청소년문화의집",
        schedules=None,
        include_apply_period=False,
    )
    pages[gokseong.gokmg_detail_url("school", "301")] = _gokmg_detail(
        school[0],
        venue="곡성초·옥과초",
        schedules=school_schedules,
    )
    return pages, foundation + agency + school


def _collect_gokmg(pages: dict[str, str], **kwargs: Any):
    created, factory = _sessions()
    fetcher = FixtureFetcher(pages)
    rows, parser, meta = gokseong.collect_gokseong_gokmg_courses(
        GokmgTarget(),
        timeout=7,
        max_pages=kwargs.pop("max_pages", 7),
        detail_limit=kwargs.pop("detail_limit", 10),
        fetcher=fetcher,
        session_factory=factory,
        today=kwargs.pop("today", "2099-06-15"),
        **kwargs,
    )
    return rows, parser, meta, fetcher, created


def test_gokmg_collects_three_complete_catalogues_and_expands_schedules() -> None:
    pages, _programs = _gokmg_fixture()

    rows, parser, meta, fetcher, sessions = _collect_gokmg(pages)

    assert parser == gokseong.GOKSEONG_GOKMG_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["source_totals"] == {
        "foundation": 13,
        "agency": 1,
        "school": 1,
    }
    assert meta["source_total"] == 15
    assert meta["source_rows"] == 15
    assert meta["canonical_source_rows"] == 15
    assert meta["exact_source_duplicate_count"] == 0
    assert meta["transient_duplicate_retry_count"] == 0
    assert meta["required_list_requests"] == 7
    assert meta["list_requests"] == 7
    assert meta["aggregate_list_requests"] == 7
    assert meta["physical_requests"] == 10
    assert meta["pages"] == 7
    assert meta["request_safety_budget"] == 27
    assert meta["request_budget_remaining"] == 17
    assert meta["request_budget_exhausted"] is False
    assert meta["expired_parent_count"] == 10
    assert meta["excluded_test_count"] == 1
    assert meta["excluded_non_course_count"] == 1
    assert meta["excluded_non_course_parent_ids"] == ["103"]
    assert meta["current_parent_count"] == 3
    assert meta["detail_pages"] == 3
    assert meta["expanded_count"] == 5
    assert meta["returned_count"] == 5
    assert meta["reservation_discovery_links"] == 1
    assert meta["status_counts"] == {"CLOSED": 2, "OPEN": 1, "SCHEDULED": 2}
    assert len(rows) == 5
    assert {row["branch"] for row in rows} == {
        "곡성군미래교육재단",
        "곡성군 기관·마을 배움터",
        "곡성군 학교대상 교육",
    }
    assert len({row["provider_course_id"] for row in rows}) == 5
    assert len({row["raw_url"] for row in rows}) == 5
    assert all(
        "#schedule-" in row["raw_url"]
        for row in rows
        if ":schedule:" in row["provider_course_id"]
    )
    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["title"] == "현재 장인 교육 - 토탈공예"
    assert open_row["reservation_available"] is True
    assert open_row["application_url"].endswith("edu_seq=101&seq=5001")
    inquiry = next(row for row in rows if row["title"] == "현재 기관 문의 교육")
    assert inquiry["venue_name"] == "곡성청소년문화의집"
    assert inquiry["reservation_available"] is False
    assert all(row["municipality_code"] == "1272000000" for row in rows)
    assert all(session.closed for session in sessions)
    assert len(fetcher.calls) == 10


def test_gokmg_page_detail_application_and_dedupe_contracts_fail_closed() -> None:
    pages, _programs = _gokmg_fixture()
    sentinel = gokseong.gokmg_list_url("foundation", 3)
    pages[sentinel] = pages[gokseong.gokmg_list_url("foundation", 2)]
    rows, _parser, meta, _fetcher, _sessions_created = _collect_gokmg(pages)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel" in meta["configured_collection_error"]

    pages2, _programs2 = _gokmg_fixture()
    detail_url = gokseong.gokmg_detail_url("foundation", "101")
    pages2[detail_url] = pages2[detail_url].replace(
        '<a data-label="접수중" href="', '<span data-label="접수중" data-href="'
    ).replace("</a>", "</span>", 1)
    rows, _parser, meta, _fetcher, _sessions_created = _collect_gokmg(pages2)
    assert rows == []
    assert "status/application mismatch" in meta["configured_collection_error"]

    pages3, _programs3 = _gokmg_fixture()
    rows, _parser, meta, _fetcher, _sessions_created = _collect_gokmg(
        pages3, dedupe_rows=lambda _rows: []
    )
    assert rows == []
    assert "dedupe changed complete row count" in meta["configured_collection_error"]


def test_gokmg_page_and_detail_caps_are_fail_closed() -> None:
    pages, _programs = _gokmg_fixture()
    rows, _parser, meta, fetcher, _sessions_created = _collect_gokmg(
        pages, max_pages=6
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]
    assert not any("educationView.es" in url for url in fetcher.calls)

    pages2, _programs2 = _gokmg_fixture()
    rows, _parser, meta, fetcher, _sessions_created = _collect_gokmg(
        pages2, detail_limit=2
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "2 of 3 required" in meta["configured_collection_error"]
    assert not any("educationView.es" in url for url in fetcher.calls)


def test_gokmg_schedule_ids_ignore_mutable_application_window() -> None:
    source = gokseong.GOKSEONG_GOKMG_SOURCES[0]
    first = gokseong._schedule_identity(
        source,
        "101",
        "1회차 / 토탈공예",
        "2099-07-01 ~ 2099-08-01",
    )
    second = gokseong._schedule_identity(
        source,
        "101",
        "1회차 / 토탈공예",
        "2099-07-01 ~ 2099-08-01",
    )
    assert first == second
    assert gokseong._is_gokmg_non_course_title(
        "2026년 평생학습공동체 신청 모집 공고 안내"
    )
    assert not gokseong._is_gokmg_non_course_title(
        "2026년 장인아카데미 학습자 모집"
    )


def test_gokmg_exact_source_duplicates_collapse_but_conflicts_fail() -> None:
    first = {
        "source_sequence": 2,
        "identity": "388",
        "title": "창의융합 체험부스",
        "period": "2099-07-01 ~ 2099-07-01",
        "raw_url": gokseong.gokmg_detail_url("foundation", "388"),
    }
    exact = {**first, "source_sequence": 1}
    canonical, duplicate_ids, errors = gokseong._canonical_gokmg_source_rows(
        [first, exact]
    )
    assert canonical == [first]
    assert duplicate_ids == ["388"]
    assert errors == []

    conflicting = {**exact, "title": "서로 다른 교육"}
    canonical, duplicate_ids, errors = gokseong._canonical_gokmg_source_rows(
        [first, conflicting]
    )
    assert canonical == [first]
    assert duplicate_ids == []
    assert errors == ["388: conflicting duplicate programme identity"]


def test_gokmg_persistent_page_drift_retries_bounded_attempts_then_fails_closed() -> None:
    pages, programs = _gokmg_fixture()
    foundation = [
        program
        for program in programs
        if program["source"].code == "foundation"
    ]
    duplicate = dict(next(program for program in foundation if program["identity"] == "1004"))
    duplicate["number"] = 3
    page_two = [
        duplicate,
        next(program for program in foundation if program["identity"] == "1002"),
        next(program for program in foundation if program["identity"] == "1001"),
    ]
    pages[gokseong.gokmg_list_url("foundation", 2)] = _gokmg_list_page(
        "foundation",
        page_two,
        total=13,
        page=2,
        last=2,
    )

    rows, _parser, meta, fetcher, _sessions_created = _collect_gokmg(pages)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["transient_duplicate_retry_count"] == 3
    assert meta["source_scan_attempts"]["foundation"] == 4
    assert meta["list_requests"] == 3
    assert meta["recovery_list_requests"] == 9
    assert meta["aggregate_list_requests"] == 12
    assert meta["physical_requests"] == 12
    assert meta["request_safety_budget"] == 27
    assert meta["request_budget_exhausted"] is False
    assert "duplicate programme identities" in meta["configured_collection_error"]
    assert meta["detail_attempts"] == 0
    assert not any("educationView.es" in url for url in fetcher.calls)


def test_gokmg_recovery_requests_stop_at_explicit_physical_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages, programs = _gokmg_fixture()
    foundation = [
        program
        for program in programs
        if program["source"].code == "foundation"
    ]
    duplicate = dict(
        next(program for program in foundation if program["identity"] == "1004")
    )
    duplicate["number"] = 3
    pages[gokseong.gokmg_list_url("foundation", 2)] = _gokmg_list_page(
        "foundation",
        [
            duplicate,
            next(program for program in foundation if program["identity"] == "1002"),
            next(program for program in foundation if program["identity"] == "1001"),
        ],
        total=13,
        page=2,
        last=2,
    )
    monkeypatch.setattr(gokseong, "GOKSEONG_SOURCE_SCAN_ATTEMPTS", 10)

    rows, _parser, meta, _fetcher, _sessions_created = _collect_gokmg(
        pages,
        max_pages=7,
        detail_limit=0,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert meta["aggregate_list_requests"] <= meta["request_safety_budget"]
    assert meta["physical_requests"] <= meta["request_safety_budget"]
    assert meta["aggregate_list_requests"] == meta["physical_requests"]
    assert meta["request_safety_budget"] == 17
    assert meta["request_budget_exhausted"] is True
    assert "max_pages cap" in meta["configured_collection_error"]
    assert "physical request safety budget cannot fit" in meta[
        "configured_collection_error"
    ]


def _jne_list_page(
    source: gokseong.JneSource,
    rows: list[dict[str, str]],
    *,
    no_data: bool = False,
    branch: str = gokseong.GOKSEONG_JNE_BRANCH,
) -> str:
    if source.kind == "lecture":
        headers = gokseong._JNE_LECTURE_HEADERS
        body = "".join(
            f"""
            <tr><td>{row['number']}</td>
              <td><a href="{_jne_detail_href(source, row['identity'])}">{row['title']}</a></td>
              <td>{row['target']}</td><td>{row['period']}</td><td>{row['apply']}</td>
              <td>{row['capacity']}</td><td>{row['status']}</td></tr>
            """
            for row in rows
        )
    else:
        headers = gokseong._JNE_EDUCATION_HEADERS
        body = "".join(
            f"""
            <tr><td>{row['number']}</td>
              <td><a href="{_jne_detail_href(source, row['identity'])}">{row['title']}</a></td>
              <td>{row['apply']}</td><td>{row['period']}</td>
              <td>{row['capacity']}</td><td>{row['status']}</td></tr>
            """
            for row in rows
        )
    if no_data:
        body = '<tr><td class="no-data" colspan="7">결과 없음</td></tr>'
    return f"""
    <html><head><title>수강 신청 : {branch}</title></head><body>
      <table class="tstyle_list"><thead><tr>
        {''.join(f'<th>{header}</th>' for header in headers)}
      </tr></thead><tbody>{body}</tbody></table>
    </body></html>
    """


def _jne_detail_href(source: gokseong.JneSource, identity: str) -> str:
    if source.kind == "lecture":
        return f"{source.path}?mid={source.mid}&act=view&el_seq={identity}&nPage="
    return (
        f"{source.path}?mid={source.mid}&eid={source.eid}&edu_seq={identity}"
        "&educ_cg=&act=view"
    )


def _lecture_detail(
    source: gokseong.JneSource,
    row: dict[str, str],
    *,
    include_application: bool = True,
) -> str:
    application = ""
    if include_application and row["status"] == "신청":
        application = (
            f'<a href="{source.path}?mid={source.mid}&el_seq={row["identity"]}'
            '&act=agree">신청하기</a>'
        )
    pairs = (
        ("강좌명", row["title"]),
        ("분기", "여름"),
        ("대상", row["target"]),
        ("신청기간", row["apply"]),
        ("운영기간", "2099-07-01~2099-08-01"),
        ("강의 시간", "10:00 ~ 12:00"),
        ("회차", "4"),
        ("강의 요일", "월"),
        ("교육장소", "다목적실"),
        ("계좌제 여부", ""),
        ("모집인원", "10명 (대기 2명)"),
        ("신청자", "3명 (대기 0명)"),
        ("신청방법", "인터넷"),
        ("접수상태", row["status"]),
        ("비고", "상세 강의 안내"),
    )
    return f"""
    <html><body><table class="tstyle_write"><tbody>
      {''.join(f'<tr><th>{key}</th><td>{value}</td></tr>' for key, value in pairs)}
    </tbody></table>{application}</body></html>
    """


def _education_detail(
    source: gokseong.JneSource,
    row: dict[str, str],
    *,
    include_application: bool = True,
    period_field: str = "수강일",
    detail_status: str = "",
    detail_capacity: str = "20명 (대기 5명) / 방문 0명",
) -> str:
    application = ""
    if include_application and row["status"] == "신청":
        application = (
            f'<a href="{source.path}?mid={source.mid}&eid={source.eid}'
            f'&edu_seq={row["identity"]}&act=write">신청하기</a>'
        )
    pairs = (
        ("강좌명", row["title"]),
        ("대상", "학생 및 성인"),
        (period_field, row["period"]),
        ("수강시간", "13:30 ~ 17:00"),
        ("수강요일", "월요일"),
        ("인터넷 접수기간", row["apply"]),
        ("신청시간", "10:00 ~ 24:00"),
        ("수강인원", detail_capacity),
        ("신청자", "3명 (대기 1명)"),
        ("교육장소", "곡성교육문화회관"),
        ("강사명", "곡성 강사"),
        ("내용", "독서문화행사 상세"),
        ("비고", detail_status or row["status"]),
    )
    return f"""
    <html><body><table class="tstyle_view"><tbody>
      {''.join(f'<tr><th>{key}</th><td>{value}</td></tr>' for key, value in pairs)}
    </tbody></table>{application}</body></html>
    """


def _jne_lecture_fixture() -> dict[str, str]:
    source = gokseong._JNE_SOURCE_BY_PROVIDER[
        gokseong.GOKSEONG_JNE_LECTURE_PROVIDER
    ]
    current = {
        "number": "2",
        "identity": "101",
        "title": "현재 AI 강좌",
        "target": "성인",
        "period": "2099-07-01 ~ 2099-08-01 월 10:00 ~ 12:00",
        "apply": "2099-06-01 ~ 2099-06-30",
        "capacity": "3 / 10 ( 0 / 2 )",
        "status": "신청",
    }
    expired = {
        "number": "1",
        "identity": "99",
        "title": "지난 강좌",
        "target": "성인",
        "period": "2020-01-01 ~ 2020-02-01 월 10:00 ~ 12:00",
        "apply": "2019-12-01 ~ 2019-12-31",
        "capacity": "10 / 10 ( 0 / 2 )",
        "status": "마감",
    }
    return {
        gokseong.jne_list_url(source.provider): _jne_list_page(
            source, [current, expired]
        ),
        gokseong.jne_list_url(source.provider, 2): _jne_list_page(
            source, [], no_data=True
        ),
        gokseong._jne_detail_url(source, "101"): _lecture_detail(source, current),
    }


def _jne_education_fixture(*, current: bool) -> dict[str, str]:
    source = gokseong._JNE_SOURCE_BY_PROVIDER[
        gokseong.GOKSEONG_JNE_EDUCATION_PROVIDER
    ]
    row = {
        "number": "1",
        "identity": "301",
        "title": "현재 독서문화행사" if current else "지난 독서문화행사",
        "period": "2099.07.05" if current else "2020.07.05",
        "apply": "2099.06.01 ~ 2099.06.30" if current else "2020.06.01 ~ 2020.06.30",
        "capacity": "3 / 20 ( 1 / 5 )",
        "status": "신청" if current else "마감",
    }
    pages = {
        gokseong.jne_list_url(source.provider): _jne_list_page(source, [row]),
        gokseong.jne_list_url(source.provider, 2): _jne_list_page(
            source, [], no_data=True
        ),
    }
    if current:
        pages[gokseong._jne_detail_url(source, "301")] = _education_detail(
            source, row
        )
    return pages


def _collect_jne(target: Any, pages: dict[str, str], **kwargs: Any):
    created, factory = _sessions()
    fetcher = FixtureFetcher(pages)
    rows, parser, meta = gokseong.collect_gokseong_jne_courses(
        target,
        timeout=7,
        max_pages=kwargs.pop("max_pages", 2),
        detail_limit=kwargs.pop("detail_limit", 10),
        fetcher=fetcher,
        session_factory=factory,
        today=kwargs.pop("today", "2099-06-15"),
        **kwargs,
    )
    return rows, parser, meta, fetcher, created


def test_jne_lecture_collects_current_detail_and_application() -> None:
    pages = _jne_lecture_fixture()

    rows, parser, meta, fetcher, sessions = _collect_jne(
        JneLectureTarget(), pages
    )

    assert parser == gokseong.GOKSEONG_JNE_LECTURE_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["source_total"] == 2
    assert meta["expired_count"] == 1
    assert meta["current_count"] == 1
    assert meta["detail_pages"] == 1
    assert meta["reservation_discovery_links"] == 1
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_course_id"].endswith(":course:101")
    assert row["branch"] == gokseong.GOKSEONG_JNE_BRANCH
    assert row["status"] == "OPEN"
    assert row["application_url"].endswith("el_seq=101&act=agree")
    assert row["venue_name"] == "다목적실"
    assert row["capacity_total"] == 10
    assert len(fetcher.calls) == 3
    assert all(session.closed for session in sessions)


def test_jne_lecture_caps_overbooked_current_capacity_and_preserves_source_value() -> None:
    source = gokseong._JNE_SOURCE_BY_PROVIDER[
        gokseong.GOKSEONG_JNE_LECTURE_PROVIDER
    ]
    source_row = {
        "number": "1",
        "identity": "901",
        "title": "초과 접수 강좌",
        "target": "성인",
        "period": "2099-07-01 ~ 2099-08-01 월 10:00 ~ 12:00",
        "apply": "2099-06-01 ~ 2099-06-30",
        "capacity": "15 / 12 ( 2 / 5 )",
        "status": "마감",
    }
    soup = BeautifulSoup(_jne_list_page(source, [source_row]), "html.parser")

    rows, errors = gokseong._jne_list_rows(source, soup)

    assert errors == []
    assert rows[0]["capacity_current"] == 12
    assert rows[0]["capacity_current_reported"] == 15
    assert rows[0]["capacity_total"] == 12
    assert rows[0]["waitlist_current"] == 2


def test_jne_reading_event_handles_complete_no_current_and_current_detail() -> None:
    ended_pages = _jne_education_fixture(current=False)
    rows, parser, meta, _fetcher, _sessions_created = _collect_jne(
        JneEducationTarget(), ended_pages
    )
    assert parser == gokseong.GOKSEONG_JNE_EDUCATION_PARSER
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["source_total"] == 1
    assert meta["expired_count"] == 1

    current_pages = _jne_education_fixture(current=True)
    rows, _parser, meta, _fetcher, _sessions_created = _collect_jne(
        JneEducationTarget(), current_pages
    )
    assert meta["snapshot_complete"] is True
    assert len(rows) == 1
    assert rows[0]["category"] == "독서문화행사"
    assert rows[0]["period"] == "2099-07-05 ~ 2099-07-05"
    assert rows[0]["application_url"].endswith("edu_seq=301&act=write")


def test_jne_reading_event_accepts_current_scheduled_site_contract() -> None:
    source = gokseong._JNE_SOURCE_BY_PROVIDER[
        gokseong.GOKSEONG_JNE_EDUCATION_PROVIDER
    ]
    row = {
        "number": "1",
        "identity": "13076",
        "title": "그림책감정코칭지도사 2급 자격증 과정",
        "period": "2099.09.01 ~ 2099.12.01",
        "apply": "2099.07.28 ~ 2099.08.14",
        "capacity": "0 / 15 ( 0 / 5 )",
        "status": "접수전",
    }
    pages = {
        gokseong.jne_list_url(source.provider): _jne_list_page(source, [row]),
        gokseong.jne_list_url(source.provider, 2): _jne_list_page(
            source, [], no_data=True
        ),
        gokseong._jne_detail_url(source, row["identity"]): _education_detail(
            source,
            row,
            include_application=False,
            period_field="수강기간",
            detail_status="접수예정 접수시작 : 2099.07.28 00:00",
            detail_capacity="15명 (대기 5명) / 방문 0명",
        ),
    }

    rows, _parser, meta, _fetcher, _sessions_created = _collect_jne(
        JneEducationTarget(), pages, today="2099-07-23"
    )

    assert meta["snapshot_complete"] is True, meta
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 1
    assert rows[0]["status"] == "SCHEDULED"
    assert rows[0]["period"] == "2099-09-01 ~ 2099-12-01"
    assert rows[0].get("application_url", "") == ""


def test_jne_branch_sentinel_application_and_caps_fail_closed() -> None:
    pages = _jne_lecture_fixture()
    first = gokseong.GOKSEONG_JNE_LECTURE_URL
    pages[first] = pages[first].replace(
        gokseong.GOKSEONG_JNE_BRANCH, "옛 기관명"
    )
    rows, _parser, meta, _fetcher, _sessions_created = _collect_jne(
        JneLectureTarget(), pages
    )
    assert rows == []
    assert "exact institution title changed" in meta["configured_collection_error"]

    pages2 = _jne_lecture_fixture()
    sentinel = gokseong.jne_list_url(
        gokseong.GOKSEONG_JNE_LECTURE_PROVIDER, 2
    )
    pages2[sentinel] = pages2[gokseong.GOKSEONG_JNE_LECTURE_URL]
    rows, _parser, meta, _fetcher, _sessions_created = _collect_jne(
        JneLectureTarget(), pages2
    )
    assert rows == []
    assert "sentinel" in meta["configured_collection_error"]

    pages3 = _jne_lecture_fixture()
    detail = gokseong._jne_detail_url(
        gokseong._JNE_SOURCE_BY_PROVIDER[
            gokseong.GOKSEONG_JNE_LECTURE_PROVIDER
        ],
        "101",
    )
    pages3[detail] = pages3[detail].replace("act=agree", "act=view")
    rows, _parser, meta, _fetcher, _sessions_created = _collect_jne(
        JneLectureTarget(), pages3
    )
    assert rows == []
    assert "status/application mismatch" in meta["configured_collection_error"]

    pages4 = _jne_lecture_fixture()
    rows, _parser, meta, fetcher, _sessions_created = _collect_jne(
        JneLectureTarget(), pages4, max_pages=1
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert len(fetcher.calls) == 1


def test_sessions_rotate_below_managed_request_budget(monkeypatch: Any) -> None:
    pages, _programs = _gokmg_fixture()
    monkeypatch.setattr(gokseong, "GOKSEONG_SESSION_REQUEST_LIMIT", 2)

    rows, _parser, meta, _fetcher, sessions = _collect_gokmg(pages)

    assert len(rows) == 5
    assert meta["snapshot_complete"] is True
    assert meta["sessions_created"] == 5
    assert len(sessions) == 5
    assert all(session.closed for session in sessions)


def test_target_helpers_alias_audit_and_managed_session_requirement_are_strict() -> None:
    assert gokseong.is_gokseong_education_target(GokmgTarget()) is True
    assert gokseong.is_gokseong_education_target(JneLectureTarget()) is True
    assert gokseong.is_gokseong_education_target(JneEducationTarget()) is True
    assert (
        gokseong.is_gokseong_education_target(
            GokmgTarget(url=gokseong.GOKSEONG_GOKMG_LEGACY_DETAIL_URL)
        )
        is False
    )
    assert gokseong.is_gokseong_education_target(GokmgTarget(provider="WRONG")) is False
    assert (
        gokseong.is_gokseong_education_target(
            JneLectureTarget(url=gokseong.GOKSEONG_JNE_LECTURE_URL + "&extra=1")
        )
        is False
    )
    assert gokseong.gokmg_list_url("foundation", 2).endswith("nPage=2")
    assert gokseong.gokmg_page_form("school", 2) == {
        "mid": "a10305000000",
        "category": "school",
        "edu_seq": "",
        "seq": "",
        "nPage": "2",
        "offset": "Y",
        "chk_lepr_arr": "",
        "chk_belo_arr": "",
        "chk_state_arr": "",
        "srh_edu_sdate": "",
        "srh_edu_edate": "",
        "keyField": "",
        "keyWord": "",
    }
    assert gokseong.gokmg_page_form("school", "2&evil=1") == {}
    assert gokseong.gokmg_list_url("../foundation", 1) == ""
    assert gokseong.gokmg_detail_url("school", "301").endswith("edu_seq=301")
    assert gokseong.gokmg_detail_url("school", "301&evil=1") == ""
    assert gokseong.jne_list_url(gokseong.GOKSEONG_JNE_EDUCATION_PROVIDER, 2).endswith(
        "eid=0130&nPage=2"
    )
    assert gokseong.GOKSEONG_GOKMG_LEGACY_AGGREGATE_URL.endswith(
        "educationList.es?mid=a10301000000"
    )
    assert gokseong.GOKSEONG_GOKMG_SPACE_RESERVATION_URL.endswith(
        "rentList.es?mid=a10302010000"
    )
    assert gokseong.GOKSEONG_CANDIDATE_IDS["jne_lecture"] == "MUNI_IR_C925653A81D5"

    rows, parser, meta = gokseong.collect_gokseong_gokmg_courses(GokmgTarget())
    assert rows == []
    assert parser == gokseong.GOKSEONG_GOKMG_PARSER
    assert "managed session_factory" in meta["configured_collection_error"]

    source = inspect.getsource(gokseong)
    assert "verify=False" not in source
    assert "verify = False" not in source
