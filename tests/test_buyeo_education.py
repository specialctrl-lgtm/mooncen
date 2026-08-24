from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import os
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_buyeo as buyeo


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    period: str
    status: str = "접수마감"
    session: str = "주간"
    schedule: str = "매주 월 10:00~12:00"
    target: str = "성인"
    instructor: str = "공개강사"
    class_size: int = 12
    applicants: int = 3
    capacity: int = 12


@dataclass(frozen=True)
class Programme:
    identity: str
    title: str
    courses: tuple[Course, ...]
    apply_period: str = "2026-07-01 ~ 2026-07-31"
    apply_time: str = "09:00 ~ 23:59"
    method: str = "추첨"
    status: str = "접수중"


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


def _site_shell(body: str) -> str:
    return f"""
      <html lang="ko"><head>
        <meta charset="UTF-8">
        <title>온라인 수강신청 &gt; 정규강좌 &gt; 부여평생학습관</title>
      </head><body>
        <h2>부여군 평생학습관</h2>
        {body}
        <span>[33159] 충남 부여군 부여읍 성왕로 360 부여군 평생학습관</span>
      </body></html>
    """


def _pager(current: int, last: int, *, group_id: str | None = None) -> str:
    nodes = []
    for page in range(1, last + 1):
        if page == current:
            nodes.append(f'<span class="on">{page}</span>')
        elif group_id:
            nodes.append(
                '<a class="pg_num" href="?mode=SL&amp;'
                f'p_mng_no={group_id}&amp;site_dvs_cd=lll&amp;menu_dvs_cd=0201&amp;GotoPage={page}">{page}</a>'
            )
        else:
            nodes.append(
                '<a class="pg_num" href="?site_dvs_cd=lll&amp;menu_dvs_cd=0201&amp;'
                f'GotoPage={page}">{page}</a>'
            )
    return f'<div class="page_navi">{"".join(nodes)}</div>'


def _outer_form() -> str:
    return """
      <form method="get" action="/_prog/lll_edu/index.php">
        <input type="hidden" name="site_dvs_cd" value="lll">
        <input type="hidden" name="menu_dvs_cd" value="0201">
        <select name="sch_status">
          <option value="" selected="selected">전체</option>
          <option value="1">접수대기</option>
          <option value="2">접수중</option>
          <option value="3">접수마감</option>
        </select>
        <select name="skey"><option value="title">교육과정명</option></select>
        <input type="text" name="sval" value="">
      </form>
    """


def _outer_html(programmes: list[Programme], current: int = 1, last: int = 1) -> str:
    headers = "".join(f"<th>{value}</th>" for value in buyeo._OUTER_HEADERS)
    rows = []
    for programme in programmes:
        href = (
            f"?mode=SL&amp;p_mng_no={programme.identity}&amp;site_dvs_cd=lll&amp;menu_dvs_cd=0201"
        )
        rows.append(
            "<tr>"
            f"<td>{escape(programme.title)}</td>"
            f"<td>{escape(programme.apply_period)}</td>"
            f"<td>{escape(programme.apply_time)}</td>"
            f"<td>{escape(programme.method)}</td>"
            f"<td>{escape(programme.status)}</td>"
            f'<td><a class="btn_more" href="{href}">강좌상세보기</a></td>'
            "</tr>"
        )
    return _site_shell(
        _outer_form()
        + f'<table class="tb_base mt10"><thead><tr>{headers}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        + _pager(current, last)
    )


def _group_form(programme: Programme) -> str:
    return f"""
      <form method="post" action="/_prog/lll_edu/index.php">
        <input type="hidden" name="mode" value="SL">
        <input type="hidden" name="site_dvs_cd" value="lll">
        <input type="hidden" name="menu_dvs_cd" value="0201">
        <input type="hidden" name="p_mng_no" value="{programme.identity}">
        <input type="text" name="skey" value="">
        <input type="text" name="sval" value="">
      </form>
    """


def _summary(programme: Programme, *, title: str | None = None) -> str:
    pairs = (
        ("교육과정명", title or programme.title),
        ("접수기간", programme.apply_period),
        ("접수시간", programme.apply_time),
        ("유형", programme.method),
        ("접수상태", programme.status),
    )
    rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>" for label, value in pairs
    )
    return f'<table class="tb_base mt10"><tbody>{rows}</tbody></table>'


def _card(
    programme: Programme,
    course: Course,
    *,
    visible_identity: str | None = None,
    force_visible: bool = False,
) -> str:
    visible = ""
    if course.status == "접수중" or force_visible:
        identity = visible_identity or course.identity
        visible = (
            f'<a href="/_prog/lll_edu_app/?mng_no={identity}" '
            'class="btn_base btn_point2 btn_more"><span>신청하기</span></a>'
        )
    comment = f"""
      <!--
        <a href="#">강의 계획서 다운로드</a>
        <a href="/_prog/lll_edu_app/?mng_no={course.identity}">신청하기</a>
      -->
    """
    fields = (
        ("교육기간  :", course.period),
        ("교육시간  :", course.schedule),
        ("대상  :", course.target),
        ("강사  :", course.instructor),
        ("수강인원  :", str(course.class_size)),
        ("접수인원/최대모집인원 :", f"{course.applicants} / {course.capacity}"),
    )
    items = "".join(
        f"<li><b>{escape(label)}</b>{escape(value)}</li>" for label, value in fields
    )
    return f"""
      <div class="ui ui-topbox type2">
        <span class="tag02">{escape(course.status)}</span>
        <span class="gu02">{escape(course.session)}</span>
        <div class="inner"><div class="txtwrap">
          <strong class="h-box ctgr_title">[{escape(programme.title)}]</strong>
          <strong class="h-box">{escape(course.title)}</strong>
          <div class="item"><ul class="list_st1">{items}</ul></div>
          <div class="btn_wrap">{comment}{visible}</div>
        </div></div>
      </div>
    """


def _group_html(
    programme: Programme,
    courses: list[Course],
    current: int,
    last: int,
    *,
    summary_title: str | None = None,
    application_identity: str | None = None,
    force_closed_control: bool = False,
) -> str:
    cards = []
    for index, course in enumerate(courses):
        cards.append(
            _card(
                programme,
                course,
                visible_identity=(application_identity if index == 0 else None),
                force_visible=(force_closed_control and index == 0),
            )
        )
    return _site_shell(
        _group_form(programme)
        + _summary(programme, title=summary_title)
        + "".join(cards)
        + _pager(current, last, group_id=programme.identity)
    )


def _current_courses(count: int = 12) -> tuple[Course, ...]:
    result = []
    for index in range(count):
        result.append(
            Course(
                identity=str(800 + index),
                title=f"부여 공개 강좌 {index}",
                period="2026년 7월 1일 ~ 8월 31일",
                status="접수중" if index == 0 else "접수마감",
                session="야간" if index == 0 else "주간",
                instructor=f"공개강사{index}",
            )
        )
    return tuple(result)


class Fixture:
    def __init__(self) -> None:
        self.programmes = [
            Programme("101", "2026년 부여군 평생학습관 공개강좌", _current_courses()),
            Programme(
                "100",
                "2025년 종료 강좌",
                (Course("700", "종료된 강좌", "2025-01-01 ~ 2025-02-01"),),
                apply_period="2025-01-01 ~ 2025-01-10",
                status="접수마감",
            ),
        ]
        self.calls: list[str] = []
        self.outer_calls: Counter[int] = Counter()
        self.group_calls: Counter[tuple[str, int]] = Counter()
        self.mode = ""

    def __call__(self, _session, url: str, _timeout: int) -> Response:
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path in {buyeo.BUYEO_APPLICATION_PATH, buyeo.BUYEO_DOWNLOAD_PATH}:
            raise AssertionError("application/download endpoints must never be requested")
        assert parsed.path == buyeo.BUYEO_LIST_PATH
        requested = int(query.get("GotoPage", ["1"])[0])
        if query.get("mode") != ["SL"]:
            self.outer_calls[requested] += 1
            programmes = list(self.programmes)
            if self.mode == "outer_sentinel_drift" and requested == 2:
                programmes[0] = replace(programmes[0], title="경계에서 바뀐 교육과정")
            if self.mode == "outer_boundary_drift" and requested == 1 and self.outer_calls[1] > 1:
                programmes[0] = replace(programmes[0], title="재검증에서 바뀐 교육과정")
            if self.mode == "duplicate_group_identity":
                programmes[1] = replace(programmes[1], identity=programmes[0].identity)
            return Response(url, _outer_html(programmes))

        group_id = query["p_mng_no"][0]
        self.group_calls[(group_id, requested)] += 1
        if self.mode == "detail_http_failure" and group_id == "101":
            return Response(url, "temporary failure", 503)
        programme = next(item for item in self.programmes if item.identity == group_id)
        last = max(1, (len(programme.courses) + buyeo.BUYEO_COURSE_PAGE_SIZE - 1) // buyeo.BUYEO_COURSE_PAGE_SIZE)
        displayed = min(requested, last)
        start = (displayed - 1) * buyeo.BUYEO_COURSE_PAGE_SIZE
        courses = list(programme.courses[start : start + buyeo.BUYEO_COURSE_PAGE_SIZE])
        if self.mode == "group_sentinel_drift" and group_id == "101" and requested == 3:
            courses[0] = replace(courses[0], title="sentinel에서 바뀐 강좌")
        if (
            self.mode == "group_boundary_drift"
            and group_id == "101"
            and requested == 1
            and self.group_calls[(group_id, requested)] > 1
        ):
            courses[0] = replace(courses[0], title="재검증에서 바뀐 강좌")
        if self.mode == "duplicate_course_identity" and group_id == "101" and displayed == 2:
            courses[0] = replace(courses[0], identity=programme.courses[0].identity)
        if self.mode == "unknown_undated" and group_id == "101" and displayed == 1:
            courses[0] = replace(courses[0], period="강의계획서 참고")
        summary_title = "잘못된 교육과정" if self.mode == "summary_drift" and group_id == "101" else None
        app_identity = "9999" if self.mode == "application_identity_drift" and group_id == "101" else None
        force_control = self.mode == "closed_control" and group_id == "100"
        return Response(
            url,
            _group_html(
                programme,
                courses,
                displayed,
                last,
                summary_title=summary_title,
                application_identity=app_identity,
                force_closed_control=force_control,
            ),
        )


def _target(**changes: str) -> dict[str, str]:
    target = {"provider": buyeo.BUYEO_PROVIDER, "url": buyeo.BUYEO_CANONICAL_URL}
    target.update(changes)
    return target


def _collect(fixture: Fixture, **kwargs):
    options = {
        "today": "2026-07-23",
        "timeout": 5,
        "max_pages": 10,
        "detail_limit": 20,
        "session_factory": DummySession,
        "fetcher": fixture,
    }
    options.update(kwargs)
    return buyeo.collect(_target(), **options)


@pytest.mark.parametrize(
    "url",
    [
        buyeo.BUYEO_HOMEPAGE_URL,
        buyeo.BUYEO_DIRECTORY_URL,
        buyeo.BUYEO_CANONICAL_URL + "?GotoPage=1",
        buyeo.BUYEO_CANONICAL_URL + "#courses",
        "http://edu.buyeo.go.kr/_prog/lll_edu/",
        "https://buyeo.go.kr/_prog/lll_edu/",
        "https://edu.buyeo.go.kr.evil.example/_prog/lll_edu/",
        "https://evil@edu.buyeo.go.kr/_prog/lll_edu/",
        "https://edu.buyeo.go.kr:443/_prog/lll_edu/",
    ],
)
def test_exact_target_matcher_rejects_aliases_and_spoofs(url: str) -> None:
    assert not buyeo.is_target(_target(url=url))


def test_stable_ids_owner_boundaries_and_url_builders() -> None:
    assert buyeo.is_target(_target())
    assert not buyeo.is_target(_target(provider="MUNI_WRONG"))
    assert buyeo.BUYEO_HOMEPAGE_CANDIDATE_ID == "MUNI_IR_E34BB4693437"
    assert buyeo.BUYEO_CANONICAL_CANDIDATE_ID == "MUNI_IR_4951139821FD"
    assert buyeo.BUYEO_CANONICAL_DERIVED_PROVIDER == "MUNI_EDU_BUYEO_GO_KR_24450708"
    assert "retarget" in buyeo.BUYEO_OWNER_BOUNDARY_AUDIT["reviewed_homepage"]["decision"]
    assert "alias" in buyeo.BUYEO_OWNER_BOUNDARY_AUDIT["course_search_directory"]["decision"]
    assert "separate" in buyeo.BUYEO_OWNER_BOUNDARY_AUDIT["national_buyeo_museum"]["decision"]
    assert parse_qs(urlparse(buyeo.buyeo_outer_url(2)).query) == {
        "site_dvs_cd": ["lll"],
        "menu_dvs_cd": ["0201"],
        "GotoPage": ["2"],
    }
    assert parse_qs(urlparse(buyeo.buyeo_group_url("101", 2)).query, keep_blank_values=True) == {
        "mode": ["SL"],
        "p_mng_no": ["101"],
        "site_dvs_cd": ["lll"],
        "menu_dvs_cd": ["0201"],
        "skey": [""],
        "sval": [""],
        "GotoPage": ["2"],
    }
    with pytest.raises(ValueError):
        buyeo.buyeo_outer_url(0)
    with pytest.raises(ValueError):
        buyeo.buyeo_group_url("../101")


def test_complete_nested_snapshot_clamps_rechecks_controls_and_branch() -> None:
    fixture = Fixture()
    rows, parser, meta = _collect(fixture)

    assert parser == buyeo.BUYEO_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["pages"] == meta["data_pages"] == 1
    assert meta["list_requests"] == 3
    assert meta["group_count"] == 2
    assert meta["detail_pages"] == 3
    assert meta["detail_requests"] == 8
    assert meta["logical_requests"] == meta["physical_requests"] == 11
    assert meta["group_sentinel_count"] == 2
    assert meta["group_boundary_recheck_count"] == 3
    assert meta["group_page_counts"] == {"101": [10, 2], "100": [1]}
    assert meta["source_total"] == meta["detail_verified"] == 13
    assert meta["current_count"] == meta["returned_count"] == 12
    assert meta["expired_count"] == 1
    assert meta["application_control_count"] == 1
    assert meta["application_endpoint_requests"] == 0
    assert meta["download_endpoint_requests"] == 0
    assert meta["duplicate_source_id_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["privacy_violations"] == 0
    assert meta["status_counts"] == {"OPEN": 1, "CLOSED": 11}
    assert meta["branch_counts"] == {buyeo.BUYEO_OFFICIAL_BRANCH: 12}
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert fixture.outer_calls == Counter({1: 2, 2: 1})
    assert fixture.group_calls == Counter(
        {("101", 1): 2, ("101", 2): 2, ("101", 3): 1, ("100", 1): 2, ("100", 2): 1}
    )
    assert all(urlparse(url).path == buyeo.BUYEO_LIST_PATH for url in fixture.calls)

    open_row = next(row for row in rows if row["status"] == "OPEN")
    assert open_row["provider_course_id"].endswith(":lll-edu:800")
    assert open_row["branch"] == open_row["venue_name"] == "부여군 평생학습관"
    assert open_row["address"] == buyeo.BUYEO_OFFICIAL_ADDRESS
    assert open_row["reservation_available"] is True
    assert open_row["application_type"] == "ONLINE_RESERVATION_LOGIN_REQUIRED"
    assert parse_qs(urlparse(open_row["application_url"]).query) == {"mng_no": ["800"]}
    assert open_row["municipality_code"] == "4476000000"
    assert open_row["municipality_full_name"] == "충청남도 부여군"
    assert open_row["raw_fields"]["detail_verified"] is True
    assert open_row["raw_fields"]["application_control_verified"] is True


def test_limits_never_publish_a_partial_nested_snapshot() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture, max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["list_requests"] == 1
    assert "max_pages" in meta["configured_collection_error"]

    fixture = Fixture()
    rows, _, meta = _collect(fixture, detail_limit=12)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["source_total"] == 13
    assert "detail_limit" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "mode,error_fragment",
    [
        ("outer_sentinel_drift", "outer immediate post-last clamp differs"),
        ("outer_boundary_drift", "outer page 1 boundary stability recheck changed"),
        ("duplicate_group_identity", "duplicate programme identities"),
        ("group_sentinel_drift", "immediate post-last clamp differs"),
        ("group_boundary_drift", "boundary stability recheck changed"),
        ("duplicate_course_identity", "duplicate individual-course identities"),
        ("summary_drift", "programme summary differs"),
        ("application_identity_drift", "application identity hint changed"),
        ("unknown_undated", "unknown course has no auditable education period"),
        ("closed_control", "inactive course exposes an application control"),
        ("detail_http_failure", "unexpected HTTP status 503"),
    ],
)
def test_contract_drift_is_fail_closed(mode: str, error_fragment: str) -> None:
    fixture = Fixture()
    fixture.mode = mode
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert error_fragment in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False
    assert meta["full_snapshot_validated"] is False


def test_period_variants_and_instructor_pii_are_handled_without_persistence() -> None:
    assert buyeo._parse_period("2026년 6월 15일 ~ 8월 24일") == (
        buyeo.date(2026, 6, 15),
        buyeo.date(2026, 8, 24),
    )
    assert buyeo._parse_period("2023. 4. 3.(월) ~ 6. 19.(월)") == (
        buyeo.date(2023, 4, 3),
        buyeo.date(2023, 6, 19),
    )
    assert buyeo._parse_period("2021-02-01 ~ 2021-02-04 (월~목)") == (
        buyeo.date(2021, 2, 1),
        buyeo.date(2021, 2, 4),
    )

    fixture = Fixture()
    fixture.programmes[0] = replace(
        fixture.programmes[0],
        courses=tuple(
            replace(course, instructor=f"개인강사이름{index}")
            for index, course in enumerate(fixture.programmes[0].courses)
        ),
    )
    rows, _, meta = _collect(fixture)
    assert meta["configured_collection_error"] == ""
    serialized = repr(rows)
    assert "개인강사이름" not in serialized
    assert "'instructor':" not in serialized
    assert all(row["raw_fields"]["instructor_discarded"] is True for row in rows)


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_TESTS") != "1",
    reason="set RUN_LIVE_MUNICIPAL_TESTS=1 for the official Buyeo audit",
)
def test_live_official_complete_nested_ledger() -> None:
    rows, parser, meta = buyeo.collect(
        _target(),
        today="2026-07-23",
        timeout=30,
        max_pages=10,
        detail_limit=700,
        session_factory=buyeo.buyeo_session_factory,
    )
    assert parser == buyeo.BUYEO_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["pages"] == 5
    assert meta["list_requests"] == 8
    assert meta["group_count"] == 46
    assert meta["detail_pages"] == 89
    assert meta["detail_requests"] == 196
    assert meta["logical_requests"] == meta["physical_requests"] == 204
    assert meta["source_total"] == 668
    assert meta["current_count"] == len(rows) == 37
    assert meta["expired_count"] == 630
    assert meta["undated_historical_count"] == 1
    assert meta["group_status_counts"] == {"접수마감": 46}
    assert meta["group_selection_method_counts"] == {"추첨": 24, "선착순": 18, "방문접수": 4}
    assert meta["all_source_status_counts"] == {"접수마감": 667, "폐쇄": 1}
    assert meta["source_status_counts"] == {"접수마감": 37}
    assert meta["status_counts"] == {"CLOSED": 37}
    assert meta["branch_counts"] == {"부여군 평생학습관": 37}
    assert meta["application_type_counts"] == {"INFO_ONLY": 37}
    assert meta["application_control_count"] == 0
    assert meta["group_sentinel_count"] == 46
    assert meta["group_boundary_recheck_count"] == 61
    assert meta["archived_identity_hint_count"] == 668
    assert meta["discarded_instructor_count"] == 666
    assert meta["missing_instructor_historical_count"] == 2
    assert meta["duplicate_source_id_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["privacy_violations"] == 0
    assert meta["application_endpoint_requests"] == 0
    assert meta["download_endpoint_requests"] == 0
    assert meta["full_snapshot_validated"] is True
    assert all(row["branch"] == "부여군 평생학습관" for row in rows)
