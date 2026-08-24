from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import math
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from Crawler import municipal_jangseong as jangseong


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
    venue: str
    source_status: str = "신청마감"
    education_status: str = "교육중"
    apply_start: str = "2026-07-01"
    apply_end: str = "2026-07-31"
    start: str | None = "2026-08-01"
    end: str | None = "2026-10-31"
    capacity_current: int = 0
    capacity_total: int = 20
    target: str = "장성군민"


class DummySession:
    def close(self) -> None:
        return None


def _historical(scope: str, identity: int, index: int) -> Course:
    return Course(
        scope=scope,
        identity=str(identity),
        title=f"{scope} 과거 강좌 {index}",
        venue="군청 4층 전산교육장" if scope == "digital" else "장성 평생학습실",
        source_status="신청마감",
        education_status="교육종료",
        apply_start="2025-01-01",
        apply_end="2025-01-10",
        start="2025-02-01",
        end="2025-02-28",
    )


def _courses() -> dict[str, list[Course]]:
    rows = {scope: [] for scope in jangseong.JANGSEONG_CATALOGUES}
    rows["digital"] = [
        Course(
            "digital",
            "500",
            "주민 정보화교육",
            "군청 4층 전산교육장",
            source_status="신청마감",
            capacity_total=24,
        ),
        *[_historical("digital", 499 - index, index) for index in range(16)],
    ]
    rows["lifelong_children"] = [
        Course(
            "lifelong_children",
            "800",
            "AI 로봇교육",
            "장성군청소년수련관 2층",
            source_status="신청마감",
            capacity_current=2,
        )
    ]
    rows["lifelong_health"] = [
        Course(
            "lifelong_health",
            "702",
            "테니스",
            "삼계테니스장",
            source_status="신청하기",
            apply_start="2026-01-05",
            apply_end="2026-11-30",
            start="2026-01-05",
            end="2026-12-30",
            capacity_current=2,
        ),
        Course(
            "lifelong_health",
            "701",
            "생활체육 골프",
            "홍길동체육관 지하1층",
            source_status="신청마감",
            apply_start="2026-03-02",
            apply_end="2026-03-31",
            start="2026-04-01",
            end="2026-11-30",
        ),
    ]
    rows["lifelong_job"] = [_historical("lifelong_job", 699, 1)]
    rows["lifelong_senior"] = [
        replace(
            _historical("lifelong_senior", 698, 1),
            title="test",
            venue="",
            start=None,
            end=None,
        )
    ]
    return rows


def _relative(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path + ("?" + parsed.query if parsed.query else "")


def _status_cell(course: Course) -> str:
    apply_image = (
        jangseong._STATUS_IMAGE_SOURCES[course.source_status]
    )
    if course.source_status == "신청하기":
        application = jangseong.jangseong_application_url(
            course.scope, course.identity
        )
        apply_state = (
            f'<a href="{escape(_relative(application), quote=True)}">'
            f'<img alt="신청하기" src="{apply_image}"></a>'
        )
    else:
        apply_state = f'<img alt="신청마감" src="{apply_image}">'
    education_range = (
        f"{course.start} ~ {course.end}"
        if course.start is not None and course.end is not None
        else "~"
    )
    return f"""
      <img alt="신청" src="{jangseong._STATUS_IMAGE_SOURCES['신청']}">
      {course.apply_start} ~ {course.apply_end} {apply_state}<br>
      <img alt="교육" src="{jangseong._STATUS_IMAGE_SOURCES['교육']}">
      {education_range}
      <img alt="{course.education_status}"
           src="{jangseong._STATUS_IMAGE_SOURCES[course.education_status]}">
    """


def _list_row(course: Course, row_number: int | None, page: int) -> str:
    catalogue = jangseong.JANGSEONG_CATALOGUES[course.scope]
    detail = jangseong.jangseong_detail_url(course.scope, course.identity, page)
    capacity = (
        f"{course.capacity_current} / {course.capacity_total}"
        if catalogue.numbered
        else f"{course.capacity_total}명"
    )
    number = f'<td class="list_idx">{row_number}</td>' if catalogue.numbered else ""
    return f"""
      <tr>{number}
        <td class="list_title"><a href="{escape(_relative(detail), quote=True)}">{escape(course.title)}</a></td>
        <td class="list_class_subject">{escape(course.venue)}</td>
        <td class="list_class_object">{capacity}</td>
        <td class="list_class_place">{_status_cell(course)}</td>
      </tr>
    """


def _pagination(scope: str, page: int, pages: int, *, sentinel: bool) -> str:
    children: list[str] = []
    for value in range(1, pages + 1):
        if not sentinel and value == page:
            children.append(f"<strong>{value}</strong>")
        else:
            children.append(
                f'<a href="?page={value}&amp;search=&amp;keyword=&amp;cate_sel=" '
                f'title="{value} 페이지">{value}</a>'
            )
    return '<div class="pagenum">' + "".join(children) + "</div>"


def _list_html(
    scope: str,
    page: int,
    courses: list[Course],
    *,
    sentinel: bool = False,
) -> str:
    catalogue = jangseong.JANGSEONG_CATALOGUES[scope]
    total = len(courses)
    pages = math.ceil(total / jangseong.JANGSEONG_PAGE_SIZE) if total else 0
    selected = (
        []
        if sentinel
        else courses[
            (page - 1) * jangseong.JANGSEONG_PAGE_SIZE : page
            * jangseong.JANGSEONG_PAGE_SIZE
        ]
    )
    if selected:
        body = "".join(
            _list_row(
                course,
                total - (page - 1) * jangseong.JANGSEONG_PAGE_SIZE - index,
                page,
            )
            for index, course in enumerate(selected)
        )
    else:
        body = '<tr><td class="list_empty" colspan="31">검색내역이 없습니다.</td></tr>'
    headers = (
        jangseong._LIST_HEADERS_NUMBERED
        if catalogue.numbered
        else jangseong._LIST_HEADERS_DIGITAL
    )
    return f"""
      <html><head><title>{escape(catalogue.title)}</title></head><body>
        <div id="content">
          <table class="list_table" id="board_list_table">
            <caption>{escape(catalogue.caption)}</caption>
            <thead><tr>{''.join(f'<th>{escape(header)}</th>' for header in headers)}</tr></thead>
            <tbody>{body}</tbody>
          </table>
          {_pagination(scope, page, pages, sentinel=sentinel or not selected)}
        </div>
      </body></html>
    """


def _detail_html(course: Course) -> str:
    catalogue = jangseong.JANGSEONG_CATALOGUES[course.scope]
    assert course.start is not None and course.end is not None
    if catalogue.numbered:
        labels = jangseong._LIFELONG_DETAIL_LABELS
        values = {
            "강좌명": course.title,
            "교육대상": course.target,
            "모집인원": str(course.capacity_total),
            "교육장소": course.venue,
            "상세주소": "민감 상세주소 101동 202호",
            "접수기간": f"{course.apply_start} (09시) ~ {course.apply_end} (18시)",
            "교육기간": f"{course.start}~{course.end}",
            "강사명": "민감강사",
            "문의전화": "010-7777-8888",
            "내용": "민감 자유서술 contact@example.test",
            "신청url": "",
            "첨부파일": "민감 신청서.pdf",
        }
        caption = f"{catalogue.label} {jangseong._DETAIL_CAPTION_SUFFIX}"
    else:
        labels = jangseong._DIGITAL_DETAIL_LABELS
        values = {
            "교육구분": course.title,
            "교육정원": f"{course.capacity_total}명",
            "접수기간": f"{course.apply_start} (09시) ~ {course.apply_end} (18시)",
            "교육기간": f"{course.start}~{course.end}",
            "총시간": "2시간 총16시간",
            "교육장소": course.venue,
            "내용": "민감 자유서술 contact@example.test 010-7777-8888",
            "첨부파일": "민감 교육안내.hwpx",
        }
        caption = "강좌예약 " + jangseong._DETAIL_CAPTION_SUFFIX
    rows = "".join(
        f"<tr><th><label>{escape(label)}</label></th><td>{escape(values[label])}</td></tr>"
        for label in labels
    )
    return f"""
      <html><head><title>{escape(catalogue.title)}</title></head><body>
        <div id="content"><table class="show_form"><caption>{escape(caption)}</caption>
          <tbody>{rows}</tbody>
        </table></div>
      </body></html>
    """


class Fixture:
    def __init__(
        self,
        courses: dict[str, list[Course]] | None = None,
        *,
        all_historical: bool = False,
    ) -> None:
        self.courses = courses or _courses()
        if all_historical:
            converted: dict[str, list[Course]] = {}
            for scope, rows in self.courses.items():
                converted[scope] = [
                    replace(
                        row,
                        source_status="신청마감",
                        education_status="교육종료",
                        apply_start="2025-01-01",
                        apply_end="2025-01-10",
                        start="2025-02-01",
                        end="2025-02-28",
                    )
                    for row in rows
                ]
            self.courses = converted
        self.requested: list[str] = []
        self.overrides: dict[str, str] = {}
        self.sequences: dict[str, list[str]] = {}
        self.lock = Lock()

    def fetch(self, _session: object, url: str, _timeout: int) -> str:
        with self.lock:
            self.requested.append(url)
            if url in self.sequences:
                values = self.sequences[url]
                if len(values) > 1:
                    return values.pop(0)
                return values[0]
            if url in self.overrides:
                return self.overrides[url]
        parsed = urlparse(url)
        for scope, catalogue in jangseong.JANGSEONG_CATALOGUES.items():
            detail_prefix = catalogue.path + "/show/"
            if parsed.path.startswith(detail_prefix):
                identity = parsed.path[len(detail_prefix) :]
                course = next(
                    row for row in self.courses[scope] if row.identity == identity
                )
                return _detail_html(course)
            if parsed.path == catalogue.path:
                page = int(parse_qs(parsed.query, keep_blank_values=True)["page"][0])
                total = len(self.courses[scope])
                pages = (
                    math.ceil(total / jangseong.JANGSEONG_PAGE_SIZE)
                    if total
                    else 0
                )
                return _list_html(
                    scope,
                    page,
                    self.courses[scope],
                    sentinel=page > pages,
                )
        raise AssertionError(f"unexpected URL {url}")


def _target(**changes: str) -> Target:
    values = {
        "provider": jangseong.JANGSEONG_PROVIDER,
        "url": jangseong.JANGSEONG_CANONICAL_URL,
        "candidate_id": "",
    }
    values.update(changes)
    return Target(**values)


def _collect(fixture: Fixture, **kwargs: object):
    return jangseong.collect_jangseong_education(
        _target(),
        today="2026-07-21",
        session_factory=DummySession,
        fetcher=fixture.fetch,
        **kwargs,
    )


def test_target_and_candidate_ownership_are_strict() -> None:
    assert jangseong.is_target(_target())
    assert not jangseong.is_target(_target(provider="MUNI_WRONG"))
    assert not jangseong.is_target(_target(url=jangseong.JANGSEONG_ROOT_URL))
    assert not jangseong.is_target(
        _target(url="https://gusle.kr/jangseong-gov-website/")
    )
    assert not jangseong.is_target(
        _target(url=jangseong.JANGSEONG_CANONICAL_URL + "?page=1")
    )
    assert not jangseong.is_target(
        _target(url=jangseong.JANGSEONG_CANONICAL_URL + "#fragment")
    )
    alias = _target(
        provider=jangseong.JANGSEONG_UNTRUSTED_CANDIDATE_PROVIDER,
        url="https://gusle.kr/jangseong-gov-website/",
        candidate_id=jangseong.JANGSEONG_CANDIDATE_ID,
    )
    assert jangseong.is_jangseong_candidate_alias(alias)
    assert not jangseong.is_target(alias)
    assert (
        jangseong.JANGSEONG_CANDIDATE_AUDIT[
            "SEPARATE_JANGSEONG_COUNTY_LIBRARY"
        ]["owner"]
        == "jangseong_county_library"
    )


def test_default_fetcher_retries_transient_tls_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 200
        url = jangseong.JANGSEONG_CANONICAL_URL
        content = b"<html><body>ok</body></html>"
        headers = {"Content-Type": "text/html; charset=utf-8"}
        encoding = ""

        def raise_for_status(self) -> None:
            return None

    class Session:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, *_args: object, **_kwargs: object) -> Response:
            self.calls += 1
            if self.calls < jangseong.JANGSEONG_FETCH_ATTEMPTS:
                raise requests.SSLError("temporary TLS handshake failure")
            return Response()

    session = Session()
    monkeypatch.setattr(jangseong.time, "sleep", lambda _seconds: None)

    response = jangseong._default_fetcher(  # noqa: SLF001 - transport contract.
        session,
        jangseong.JANGSEONG_CANONICAL_URL,
        1,
    )

    assert response.status_code == 200
    assert session.calls == jangseong.JANGSEONG_FETCH_ATTEMPTS


def test_complete_snapshot_reconciles_every_scope_and_safe_details() -> None:
    fixture = Fixture()
    rows, parser, meta = _collect(fixture)

    assert parser == jangseong.JANGSEONG_PARSER
    assert meta["source_rows"] == 22
    assert meta["source_rows_by_scope"] == {
        "digital": 17,
        "lifelong_children": 1,
        "lifelong_disabled": 0,
        "lifelong_senior": 1,
        "lifelong_job": 1,
        "lifelong_health": 2,
        "lifelong_culture": 0,
        "lifelong_hobby": 0,
        "lifelong_resident": 0,
    }
    assert meta["data_pages"] == 6
    assert meta["required_list_requests"] == 29
    assert meta["list_requests"] == 29
    assert meta["sentinel_requests"] == 5
    assert meta["empty_scope_rechecks"] == 4
    assert meta["stability_rechecks"] == 14
    assert meta["current_source_count"] == 4
    assert meta["expired_count"] == 18
    assert meta["detail_pages"] == 4
    assert meta["returned_count"] == 4
    assert meta["status_counts"] == {"CLOSED": 3, "OPEN": 1}
    assert meta["visible_public_application_control_count"] == 1
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["current_branch_names"] == [
        "군청 4층 전산교육장",
        "삼계테니스장",
        "장성군청소년수련관 2층",
        "홍길동체육관 지하1층",
    ]

    open_rows = [row for row in rows if row["status"] == "OPEN"]
    assert len(open_rows) == 1
    assert open_rows[0]["raw_fields"]["identity"] == "702"
    assert open_rows[0]["application_url"] == jangseong.jangseong_application_url(
        "lifelong_health", "702"
    )
    assert open_rows[0]["reservation_available"] is True
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["municipality_code"] == "1284000000" for row in rows)
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["fee"] == "요금 별도 안내" for row in rows)
    assert all(row["target"] for row in rows)
    assert all(row["schedule_raw"] for row in rows)
    digital = next(
        row for row in rows if row["raw_fields"]["scope"] == "digital"
    )
    assert digital["target"] == "대상 별도 안내"
    assert digital["schedule_raw"] == "2시간 총16시간"
    assert digital["raw_fields"]["schedule_evidence"] == (
        "official_structured_detail_total_time"
    )
    assert all(
        row["schedule_raw"] == "시간 별도 안내"
        for row in rows
        if row["raw_fields"]["scope"] != "digital"
    )
    payload = repr(rows)
    assert "010-7777-8888" not in payload
    assert "contact@example.test" not in payload
    assert "민감 상세주소" not in payload
    assert "민감강사" not in payload
    assert not any("request_list" in url for url in fixture.requested)
    assert not any("support/login" in url for url in fixture.requested)


def test_only_current_details_are_fetched() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture)
    requested = set(fixture.requested)
    historical = [
        row
        for scope_rows in fixture.courses.values()
        for row in scope_rows
        if row.end is None or row.end < "2026-07-21"
    ]
    assert len(rows) == 4
    assert meta["detail_attempts"] == 4
    for row in historical:
        assert jangseong.jangseong_detail_url(row.scope, row.identity) not in requested


def test_max_pages_cap_fails_before_pagination_expansion() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture, max_pages=28)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["list_requests"] == len(jangseong.JANGSEONG_CATALOGUES)
    assert "29 required list requests" in meta["configured_collection_error"]


def test_detail_limit_fails_before_any_detail_request() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture, detail_limit=3)
    assert rows == []
    assert meta["pagination_complete"] is True
    assert meta["detail_attempts"] == 0
    assert meta["source_cap_reached"] is True
    assert "3 of 4 required current details" in meta["configured_collection_error"]
    assert not any("/show/" in urlparse(url).path for url in fixture.requested)


def test_nonempty_immediate_sentinel_fails_closed() -> None:
    fixture = Fixture()
    sentinel = jangseong.jangseong_list_url("digital", 3)
    fixture.overrides[sentinel] = _list_html(
        "digital", 2, fixture.courses["digital"], sentinel=False
    )
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel" in meta["configured_collection_error"]


def test_first_page_boundary_drift_fails_closed() -> None:
    fixture = Fixture()
    url = jangseong.jangseong_list_url("lifelong_health", 1)
    original = _list_html(
        "lifelong_health", 1, fixture.courses["lifelong_health"]
    )
    changed = original.replace("테니스", "경계변경 테니스", 1)
    fixture.sequences[url] = [original, changed]
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "boundary changed" in meta["configured_collection_error"]


def test_application_control_must_bind_same_identity() -> None:
    fixture = Fixture()
    url = jangseong.jangseong_list_url("lifelong_health", 1)
    fixture.overrides[url] = _list_html(
        "lifelong_health", 1, fixture.courses["lifelong_health"]
    ).replace("idx=702", "idx=999")
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "application login control changed" in meta["configured_collection_error"]


def test_unknown_source_status_fails_closed() -> None:
    fixture = Fixture()
    url = jangseong.jangseong_list_url("lifelong_children", 1)
    fixture.overrides[url] = _list_html(
        "lifelong_children", 1, fixture.courses["lifelong_children"]
    ).replace('alt="신청마감"', 'alt="접수중"', 1)
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "source status changed" in meta["configured_collection_error"]


def test_current_detail_venue_mismatch_fails_closed() -> None:
    fixture = Fixture()
    course = fixture.courses["lifelong_health"][0]
    detail = jangseong.jangseong_detail_url(course.scope, course.identity)
    fixture.overrides[detail] = _detail_html(course).replace(
        "삼계테니스장", "임의 변경 장소"
    )
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert meta["detail_errors"] == 1
    assert "list/detail safe fields mismatch" in meta["configured_collection_error"]


def test_current_incomplete_education_period_fails_closed() -> None:
    fixture = Fixture()
    course = fixture.courses["lifelong_children"][0]
    broken = replace(course, start=None, end=None)
    fixture.courses["lifelong_children"] = [broken]
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert "current education period is incomplete" in meta["configured_collection_error"]


def test_complete_historical_snapshot_sets_no_current_data_without_details() -> None:
    fixture = Fixture(all_historical=True)
    rows, _, meta = _collect(fixture)
    assert rows == []
    assert meta["source_rows"] == 22
    assert meta["current_source_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert "no current/future courses" in meta["no_current_reason"]


def test_deduper_cannot_drop_official_identity() -> None:
    fixture = Fixture()
    rows, _, meta = _collect(fixture, dedupe_rows=lambda values: values[:-1])
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "dedupe changed official identity cardinality" in meta[
        "configured_collection_error"
    ]


@pytest.mark.skipif(
    os.getenv("JANGSEONG_LIVE_TEST") != "1",
    reason="set JANGSEONG_LIVE_TEST=1 for official live contract validation",
)
def test_live_jangseong_snapshot_contract() -> None:
    rows, parser, meta = jangseong.collect_jangseong_education(
        _target(),
        today="2026-07-21",
        timeout=30,
    )
    assert parser == jangseong.JANGSEONG_PARSER
    assert meta["source_rows"] == 43
    assert meta["source_rows_by_scope"] == {
        "digital": 32,
        "lifelong_children": 1,
        "lifelong_disabled": 0,
        "lifelong_senior": 1,
        "lifelong_job": 1,
        "lifelong_health": 8,
        "lifelong_culture": 0,
        "lifelong_hobby": 0,
        "lifelong_resident": 0,
    }
    assert meta["required_list_requests"] == 30
    assert meta["list_requests"] == 30
    assert meta["current_source_count"] == 9
    assert meta["expired_count"] == 34
    assert meta["detail_pages"] == 9
    assert meta["visible_public_application_control_count"] == 7
    assert meta["current_branch_names"] == [
        "삼계테니스장",
        "생활체육공원",
        "워라밸돔구장",
        "장성군청소년수련관 2층",
        "홍길동체육관",
        "홍길동체육관 지하1층",
    ]
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 9
    assert sum(row["reservation_available"] for row in rows) == 7
