from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import math
import os
from threading import Lock
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from Crawler import municipal_gangjin as gangjin


@dataclass
class Target:
    provider: str
    url: str
    candidate_id: str = ""


@dataclass(frozen=True)
class Course:
    scope: str
    identity: str
    title: str
    source_status: str = "접수하기"
    opening: str = "개강"
    apply_start: str = "2026-07-01 09:00"
    apply_end: str = "2026-07-31 18:00"
    start: str = "2026-08-01 09:00"
    end: str = "2026-10-31 18:00"
    venue: str = "강진군 평생학습센터"
    target: str = "강진군민"
    fee: str = "무료"
    current: int = 3
    wait: int = 0
    capacity: int = 20
    wait_capacity: int = 5
    expose_control: bool | None = None


class DummySession:
    def close(self) -> None:
        return None


def _historical(scope: str, identity: int, index: int) -> Course:
    date_only = scope == "digital"
    return Course(
        scope,
        str(identity),
        f"{scope} 과거 강좌 {index}",
        source_status="수강종료",
        opening="개강",
        apply_start="2025-01-01" if date_only else "2025-01-01 09:00",
        apply_end="2025-01-10" if date_only else "2025-01-10 18:00",
        start="2025-02-01" if date_only else "2025-02-01 09:00",
        end="2025-02-28" if date_only else "2025-02-28 18:00",
        venue="강진군도서관 시청각실" if date_only else "강진군 평생학습센터",
    )


def _courses() -> dict[str, list[Course]]:
    lifelong = [
        Course("lifelong", "400", "강진 생활예술", venue="강진군 평생학습센터"),
        Course(
            "lifelong",
            "399",
            "강진 목공예",
            source_status="접수마감",
            venue="성진목공예(다산로 426-4",
            fee="50,000 원",
        ),
        Course(
            "lifelong",
            "398",
            "강진 발레",
            source_status="접수대기",
            venue="청소년수련관",
            target="청소년(초등)",
        ),
        Course(
            "lifelong",
            "397",
            "강진 취소 강좌",
            source_status="폐강",
            opening="폐강",
            venue="강진군복지타운 3층",
        ),
        *[_historical("lifelong", 396 - index, index) for index in range(13)],
    ]
    digital = [
        Course(
            "digital",
            "300",
            "스마트폰 활용",
            apply_start="2026-07-01",
            apply_end="2026-07-31",
            start="2026-08-01",
            end="2026-08-02",
            venue="강진군도서관 시청각실",
        ),
        Course(
            "digital",
            "299",
            "이미지 만들기",
            source_status="접수마감",
            apply_start="2026-07-01",
            apply_end="2026-07-20",
            start="2026-08-03",
            end="2026-08-04",
            venue="정보화교육장(복지타운 3층)",
        ),
        *[_historical("digital", 298 - index, index) for index in range(14)],
    ]
    return {"lifelong": lifelong, "digital": digital}


def _options() -> str:
    return "".join(
        f'<option value="{escape(code)}"{" selected=\"selected\"" if code == "all" else ""}>{label}</option>'
        for code, label in gangjin.GANGJIN_SEARCH_STATUSES
    )


def _form(scope: str) -> str:
    path = gangjin.GANGJIN_SCOPE_PATHS[scope]
    return f"""
      <form id="list_search" class="list_sch2" action="{path}">
        <input type="hidden" name="csrf_token" value="{'a' * 64}">
        <fieldset class="srch">
          <select name="search_status" id="search_status">{_options()}</select>
          <input type="text" id="search_word" name="search_word" value="">
          <input type="text" id="search_startdate" name="search_startdate" value="">
          <input type="text" id="search_enddate" name="search_enddate" value="">
          <input type="submit" value="검색">
        </fieldset>
      </form>
    """


def _status(course: Course) -> str:
    classes = {
        "접수하기": "state state_receipt",
        "접수대기": "state state_waiting",
        "접수마감": "state state_finish",
        "수강중": "state state_finish",
        "수강종료": "state state_finish",
        "폐강": "state state_close",
    }
    span = f'<span class="{classes[course.source_status]}">{course.source_status}</span>'
    if course.source_status == "접수하기":
        path = gangjin.GANGJIN_SCOPE_PATHS[course.scope]
        return f'<a href="/www/operation_guide/member_login?{urlencode((("return_url", path),))}">{span}</a>'
    return span


def _list_row(course: Course) -> str:
    href = urlparse(gangjin.gangjin_detail_url(course.scope, course.identity))
    relative = href.path + "?" + href.query
    close_class = ' class="edu_close"' if course.opening == "폐강" else ""
    return f"""
      <tr>
        <td class="align_left{' edu_close' if course.opening == '폐강' else ''}">
          <a href="{escape(relative, quote=True)}"><span class="title">{escape(course.title)}</span>
            <p class="date"><span><span class="icon"></span>신청기간 - {course.apply_start} ~ {course.apply_end}</span>
            <span><span class="icon"></span>교육기간 - {course.start} ~ {course.end}</span></p>
          </a>
        </td>
        <td{close_class}><span class="blue_font">{course.current}</span>({course.wait})/{course.capacity} 명</td>
        <td{close_class}>{course.opening}</td>
        <td{close_class}>{_status(course)}</td>
      </tr>
    """


def _pagination(scope: str, page: int, pages: int, *, sentinel: bool) -> str:
    path = gangjin.GANGJIN_SCOPE_PATHS[scope]
    anchors = []
    for value in range(1, pages + 1):
        if not sentinel and value == page:
            anchors.append(f'<a class="on">{value}</a>')
        else:
            anchors.append(
                f'<a href="{path}?page={value}" title="{value} 페이지">{value}</a>'
            )
    return '<div class="paging"><div class="num">' + "".join(anchors) + "</div></div>"


def _list_html(
    scope: str,
    page: int,
    courses: list[Course],
    *,
    sentinel: bool = False,
) -> str:
    total = len(courses)
    pages = max(1, math.ceil(total / gangjin.GANGJIN_PAGE_SIZE))
    page_courses = [] if sentinel else courses[(page - 1) * 15 : page * 15]
    headers = gangjin._LIST_HEADERS[scope]
    body = (
        "".join(_list_row(course) for course in page_courses)
        if page_courses
        else '<tr><td colspan="4">개설된 강좌가 없습니다.</td></tr>'
    )
    title = f"{page} 페이지 목록보기 &lt; {escape(gangjin.GANGJIN_SCOPE_TITLES[scope])}"
    caption = (
        f"{pages}페이지 중 {page}페이지, 전체 {total}건 입니다. 본 데이터표는 6컬럼, "
        f"{len(page_courses)}로우로 구성되어 있습니다. 각 로우는 번호, 강좌명, 정원, "
        "신청기간, 상태, 접수로 이루어져 있습니다."
    )
    return f"""
      <html><head><title>{title}</title></head><body>
        {_form(scope)}
        <table id="lecture_new_table"><caption>{caption}</caption>
          <thead><tr>{''.join(f'<th>{header}</th>' for header in headers)}</tr></thead>
          <tbody>{body}</tbody>
        </table>
        {_pagination(scope, page, pages, sentinel=sentinel)}
      </body></html>
    """


def _detail_html(course: Course) -> str:
    expose = course.source_status == "접수하기" if course.expose_control is None else course.expose_control
    control = ""
    if expose:
        parsed = urlparse(gangjin.gangjin_application_url(course.scope, course.identity))
        control = (
            f'<div class="btn_center"><a href="{escape("?" + parsed.query, quote=True)}">'
            '<img src="/apply.png" alt="접수하기"></a></div>'
        )
    rows = (
        ("강좌명", escape(course.title)),
        ("강사명", "민감 강사 010-7777-8888"),
        ("재료비", course.fee),
        ("교육대상", escape(course.target)),
        ("신청기간", f"{course.apply_start} ~ {course.apply_end}"),
        ("교육기간", f"{course.start} ~ {course.end}"),
        ("교육장소", escape(course.venue)),
        ("교육내용", "민감 자유서술 contact@example.test 첨부파일"),
        ("모집정원", f"{course.capacity} 명"),
        ("대기정원", f"{course.wait_capacity} 명"),
    )
    return f"""
      <html><head><title>{escape(course.title)} &lt; {escape(gangjin.GANGJIN_SCOPE_TITLES[course.scope])}</title></head>
      <body><div id="content">{control}<div id="board_basic_view">
        <table id="lecture_view_table">{''.join(f'<tr><th>{label}</th><td>{value}</td></tr>' for label, value in rows)}</table>
        <div class="lecture_btn_box"><a href="?page=">목록</a></div>
      </div></div></body></html>
    """


class Fixture:
    def __init__(self, courses: dict[str, list[Course]] | None = None) -> None:
        self.courses = courses or _courses()
        self.requested: list[str] = []
        self.overrides: dict[str, str] = {}
        self.sequences: dict[str, list[str]] = {}
        self.lock = Lock()

    def factory(self) -> DummySession:
        return DummySession()

    def fetch(self, _session: DummySession, url: str, _timeout: int) -> str:
        with self.lock:
            self.requested.append(url)
            if url in self.sequences and self.sequences[url]:
                return self.sequences[url].pop(0)
            if url in self.overrides:
                return self.overrides[url]
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        scope = next(
            key for key, path in gangjin.GANGJIN_SCOPE_PATHS.items() if parsed.path == path
        )
        if query.get("mode") == ["view"]:
            identity = query["idx"][0]
            course = next(item for item in self.courses[scope] if item.identity == identity)
            return _detail_html(course)
        if query.get("mode") == ["write"]:
            raise AssertionError("application form must never be fetched")
        page = int(query.get("page", ["1"])[0])
        pages = max(1, math.ceil(len(self.courses[scope]) / gangjin.GANGJIN_PAGE_SIZE))
        return _list_html(scope, page, self.courses[scope], sentinel=page == pages + 1)


def _target() -> Target:
    return Target(gangjin.GANGJIN_PROVIDER, gangjin.GANGJIN_CANONICAL_URL)


def _collect(fixture: Fixture, **kwargs):
    return gangjin.collect_gangjin_education(
        _target(),
        today="2026-07-21",
        max_pages=30,
        detail_limit=30,
        max_workers=4,
        session_factory=fixture.factory,
        fetcher=fixture.fetch,
        **kwargs,
    )


def test_audit_constants_define_current_owner_and_boundaries() -> None:
    assert gangjin.GANGJIN_MUNICIPALITY_CODE == "1278000000"
    assert gangjin.GANGJIN_MUNICIPALITY_NAME == "전남광주통합특별시 강진군"
    assert gangjin.GANGJIN_DISCOVERY_AUDIT["source_rows"] == 215
    assert gangjin.GANGJIN_DISCOVERY_AUDIT["current_or_future_rows"] == 41
    audit = gangjin.GANGJIN_CANDIDATE_AUDIT
    assert audit[gangjin.GANGJIN_CANDIDATE_ID]["owner"] == gangjin.GANGJIN_PROVIDER
    assert "exclude_editorial" in audit[gangjin.GANGJIN_EDITORIAL_AUDIT_ID]["decision"]


def test_target_is_strict_and_aliases_do_not_dispatch() -> None:
    assert gangjin.is_gangjin_education_target(_target())
    for url in (
        gangjin.GANGJIN_LANDING_URL,
        gangjin.GANGJIN_DIGITAL_URL,
        gangjin.GANGJIN_EDITORIAL_URL,
    ):
        alias = Target(gangjin.GANGJIN_PROVIDER, url, gangjin.GANGJIN_CANDIDATE_ID)
        assert not gangjin.is_gangjin_education_target(alias)
        assert gangjin.is_gangjin_candidate_alias(alias)
    for url in (
        gangjin.GANGJIN_CANONICAL_URL + "/",
        gangjin.GANGJIN_CANONICAL_URL + "?page=1",
        gangjin.GANGJIN_CANONICAL_URL.replace("https://", "http://"),
        gangjin.GANGJIN_CANONICAL_URL.replace("www.", "evil."),
    ):
        assert not gangjin.is_gangjin_education_target(
            Target(gangjin.GANGJIN_PROVIDER, url)
        )


def test_url_helpers_are_canonical_and_validate_inputs() -> None:
    assert gangjin.gangjin_list_url("digital", 2).endswith("info_app?page=2")
    assert gangjin.gangjin_detail_url("lifelong", "7").endswith("idx=7&mode=view")
    assert gangjin.gangjin_application_url("lifelong", "7").endswith("idx=7&mode=write")
    for value in (0, True, "1"):
        with pytest.raises(ValueError):
            gangjin.gangjin_list_url("lifelong", value)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        gangjin.gangjin_detail_url("other", "1")


def test_complete_two_scope_snapshot_and_exact_branches() -> None:
    fixture = Fixture()
    rows, parser, meta = _collect(fixture)
    assert parser == gangjin.GANGJIN_PARSER
    assert len(rows) == 6
    assert meta["source_rows"] == 33
    assert meta["source_rows_by_scope"] == {"lifelong": 17, "digital": 16}
    assert meta["data_pages_by_scope"] == {"lifelong": 2, "digital": 2}
    assert meta["required_list_requests"] == meta["list_requests"] == 10
    assert meta["sentinel_requests"] == 2
    assert meta["stability_rechecks"] == 4
    assert meta["current_source_count"] == 6
    assert meta["detail_pages"] == 6
    assert meta["snapshot_complete"] is True
    assert meta["status_counts"] == {
        "OPEN": 2,
        "CLOSED": 2,
        "SCHEDULED": 1,
        "CANCELLED": 1,
    }
    assert {row["branch"] for row in rows} == {
        f"{gangjin.GANGJIN_MUNICIPALITY_NAME} / 강진군 평생학습센터",
        f"{gangjin.GANGJIN_MUNICIPALITY_NAME} / 성진목공예(다산로 426-4",
        f"{gangjin.GANGJIN_MUNICIPALITY_NAME} / 청소년수련관",
        f"{gangjin.GANGJIN_MUNICIPALITY_NAME} / 강진군복지타운 3층",
        f"{gangjin.GANGJIN_MUNICIPALITY_NAME} / 강진군도서관 시청각실",
        f"{gangjin.GANGJIN_MUNICIPALITY_NAME} / 정보화교육장(복지타운 3층)",
    }
    assert all(row["preserve_branch"] is True for row in rows)
    assert all(row["municipality_code"] == "1278000000" for row in rows)


def test_only_current_details_and_identity_bound_controls_are_used() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture)
    detail_urls = [url for url in fixture.requested if parse_qs(urlparse(url).query).get("mode") == ["view"]]
    assert len(detail_urls) == 6
    assert not any("mode=write" in url for url in fixture.requested)
    opened = [row for row in rows if row["status"] == "OPEN"]
    assert len(opened) == meta["visible_public_application_control_count"] == 2
    assert all(row["application_url"].endswith("mode=write") for row in opened)
    assert all(row["reservation_available"] for row in opened)
    assert all(not row["application_url"] for row in rows if row["status"] != "OPEN")


def test_pii_and_freeform_cells_are_not_persisted() -> None:
    rows, _, meta = _collect(Fixture())
    payload = repr(rows)
    assert "010-7777-8888" not in payload
    assert "contact@example.test" not in payload
    assert "민감 자유서술" not in payload
    assert meta["pii_payload_persisted"] is False
    assert all(row["description"] == row["title"] for row in rows)


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"max_pages": 9}, "max_pages cap"),
        ({"detail_limit": 5}, "detail_limit cap"),
        ({"max_workers": 0}, "invalid collection limits"),
    ],
)
def test_caps_and_invalid_limits_fail_closed(kwargs: dict, fragment: str) -> None:
    fixture = Fixture()
    rows, _, meta = gangjin.collect_gangjin_education(
        _target(),
        today="2026-07-21",
        session_factory=fixture.factory,
        fetcher=fixture.fetch,
        **kwargs,
    )
    assert rows == []
    assert fragment in meta["configured_collection_error"]
    if "cap" in fragment:
        assert meta["source_cap_reached"] is True


def test_invalid_target_fails_before_fetch() -> None:
    fixture = Fixture()
    rows, _, meta = gangjin.collect_gangjin_education(
        Target(gangjin.GANGJIN_PROVIDER, gangjin.GANGJIN_LANDING_URL),
        session_factory=fixture.factory,
        fetcher=fixture.fetch,
    )
    assert rows == []
    assert fixture.requested == []
    assert "does not match" in meta["configured_collection_error"]


def test_nonempty_sentinel_fails_closed() -> None:
    fixture = Fixture()
    url = gangjin.gangjin_list_url("lifelong", 3)
    fixture.overrides[url] = _list_html("lifelong", 2, fixture.courses["lifelong"])
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "sentinel" in meta["configured_collection_error"]


def test_boundary_identity_change_fails_closed() -> None:
    fixture = Fixture()
    url = gangjin.gangjin_list_url("lifelong", 1)
    changed = list(fixture.courses["lifelong"])
    changed[0] = replace(changed[0], title="경계 변경")
    fixture.sequences[url] = [
        _list_html("lifelong", 1, fixture.courses["lifelong"]),
        _list_html("lifelong", 1, changed),
    ]
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "boundary changed" in meta["configured_collection_error"]


def test_duplicate_identity_across_scopes_fails_closed() -> None:
    courses = _courses()
    courses["digital"][0] = replace(courses["digital"][0], identity="400")
    rows, _, meta = _collect(Fixture(courses))
    assert rows == []
    assert "duplicate official identities" in meta["configured_collection_error"]


def test_non_descending_identity_order_fails_closed() -> None:
    courses = _courses()
    courses["digital"][1] = replace(courses["digital"][1], identity="301")
    rows, _, meta = _collect(Fixture(courses))
    assert rows == []
    assert "not strictly descending" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        (lambda c: replace(c, title="상세 제목 불일치"), "detail"),
        (lambda c: replace(c, venue=""), "safe venue changed"),
        (lambda c: replace(c, capacity=c.capacity + 1), "list/detail safe fields mismatch"),
    ],
)
def test_detail_mismatches_fail_closed(mutation, fragment: str) -> None:
    fixture = Fixture()
    source = fixture.courses["lifelong"][0]
    fixture.overrides[gangjin.gangjin_detail_url("lifelong", source.identity)] = _detail_html(
        mutation(source)
    )
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert fragment in meta["configured_collection_error"]


def test_missing_open_application_control_fails_closed() -> None:
    fixture = Fixture()
    source = fixture.courses["lifelong"][0]
    fixture.overrides[gangjin.gangjin_detail_url("lifelong", source.identity)] = _detail_html(
        replace(source, expose_control=False)
    )
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "application control" in meta["configured_collection_error"]


def test_inactive_application_control_fails_closed() -> None:
    fixture = Fixture()
    source = fixture.courses["lifelong"][1]
    fixture.overrides[gangjin.gangjin_detail_url("lifelong", source.identity)] = _detail_html(
        replace(source, expose_control=True)
    )
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "inactive detail exposes" in meta["configured_collection_error"]


def test_current_reversed_period_fails_but_historical_is_audited() -> None:
    courses = _courses()
    courses["lifelong"][0] = replace(
        courses["lifelong"][0], apply_start="2026-08-01 09:00", apply_end="2026-07-01 18:00"
    )
    rows, _, meta = _collect(Fixture(courses))
    assert rows == []
    assert meta["current_reversed_application_period_count"] == 1

    courses = _courses()
    courses["digital"][-1] = replace(
        courses["digital"][-1], apply_start="2025-02-01", apply_end="2025-01-01"
    )
    rows, _, meta = _collect(Fixture(courses))
    assert len(rows) == 6
    assert meta["historical_reversed_application_period_count"] == 1


def test_current_semantic_duplicate_fails_closed() -> None:
    courses = _courses()
    first = courses["lifelong"][0]
    courses["lifelong"][1] = replace(
        courses["lifelong"][1],
        title=first.title,
        start=first.start,
        end=first.end,
        venue=first.venue,
    )
    rows, _, meta = _collect(Fixture(courses))
    assert rows == []
    assert meta["semantic_duplicate_group_count"] == 1


def test_no_current_data_only_after_complete_snapshot() -> None:
    courses = _courses()
    courses = {
        scope: [_historical(scope, int(course.identity), index) for index, course in enumerate(values)]
        for scope, values in courses.items()
    }
    rows, _, meta = _collect(Fixture(courses))
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["detail_attempts"] == 0


def test_dedupe_must_preserve_official_cardinality() -> None:
    fixture = Fixture()
    rows, _, meta = gangjin.collect_gangjin_education(
        _target(),
        today="2026-07-21",
        max_pages=30,
        detail_limit=30,
        session_factory=fixture.factory,
        fetcher=fixture.fetch,
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert "dedupe changed official identity cardinality" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.environ.get("GANGJIN_LIVE_TEST") != "1",
    reason="set GANGJIN_LIVE_TEST=1 for official-site integration",
)
def test_live_official_snapshot() -> None:
    rows, parser, meta = gangjin.collect_gangjin_education(
        _target(),
        today="2026-07-21",
        max_pages=30,
        detail_limit=60,
    )
    assert parser == gangjin.GANGJIN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["list_requests"] == meta["required_list_requests"]
    assert meta["source_rows"] == 215
    assert meta["source_rows_by_scope"] == {"lifelong": 127, "digital": 88}
    assert meta["current_source_count"] == 41
    assert len(rows) == meta["returned_count"] == 41
    assert meta["visible_public_application_control_count"] == 33
    assert meta["full_snapshot_validated"] is True
    assert meta["pii_payload_persisted"] is False
