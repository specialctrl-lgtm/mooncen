from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_muan as muan


TARGET = {"provider": muan.MUAN_PROVIDER, "url": muan.MUAN_CANDIDATE_URL}


@dataclass(frozen=True)
class Course:
    source: str
    identity: str
    row_number: int
    title: str
    apply_start: str
    apply_end: str
    start: str
    end: str
    method: str
    source_status: str
    branch: str
    venue: str
    capacity_current: int = 0
    capacity_wait: int = 0
    capacity_total: int = 20
    selection: str = "선착순"
    target: str = "무안군민"
    fee: str = "0 원"
    direct_branch: bool = False


class Response:
    def __init__(
        self,
        html: str,
        url: str,
        *,
        content_type: str = "text/html; charset=UTF-8",
        history: tuple[Any, ...] = (),
        final_url: str | None = None,
    ) -> None:
        self.status_code = 200
        self.content = html.encode("utf-8")
        self.url = final_url or url
        self.headers = {"Content-Type": content_type}
        self.history = history


class DummySession:
    def close(self) -> None:
        return None


def _source(code: str):
    return muan._SOURCE_BY_CODE[code]


def _courses(*, no_current: bool = False) -> dict[str, list[Course]]:
    lifelong: list[Course] = [
        Course(
            "lifelong",
            "101",
            0,
            "AI 영상 제작",
            "2026-07-01 09:00",
            "2026-07-31 18:00",
            "2026-07-10",
            "2026-08-31",
            "온라인접수",
            "접수중",
            "무안군 자치행정과",
            "무안군 전산교육장",
            capacity_current=7,
            capacity_wait=1,
        ),
        Course(
            "lifelong",
            "100",
            0,
            "가을 시민 글쓰기",
            "2026-08-01 09:00",
            "2026-08-10 18:00",
            "2026-09-01",
            "2026-10-01",
            "병합(온라인+서면)접수",
            "접수대기",
            "무안군 자치행정과",
            "무안군복합문화센터 교육실",
        ),
    ]
    for offset in range(14):
        lifelong.append(
            Course(
                "lifelong",
                str(99 - offset),
                0,
                f"지난 평생학습 {offset + 1}",
                "2020-01-01 09:00",
                "2020-01-10 18:00",
                "2020-02-01",
                "2020-02-28",
                "서면접수" if offset % 2 else "온라인접수",
                "수강종료",
                "무안군 자치행정과",
                f"마을학습실 {offset + 1}",
                direct_branch=offset == 13,
            )
        )
    other = [
        Course(
            "other",
            "201",
            0,
            "어르신 한글교실",
            "2026-06-01 09:00",
            "2026-06-10 18:00",
            "2026-07-01",
            "2026-07-31",
            "서면접수",
            "수강확정",
            "무안군 주민생활과",
            "무안군 노인복지회관",
            capacity_total=16,
            target="무안군 어르신",
        ),
        Course(
            "other",
            "200",
            0,
            "지난 원예교실",
            "2020-03-01 09:00",
            "2020-03-10 18:00",
            "2020-04-01",
            "2020-05-01",
            "서면접수",
            "수강종료",
            "무안군 주민생활과",
            "일로노인복지관",
            capacity_total=14,
            target="무안군 어르신",
        ),
    ]
    if no_current:
        lifelong[0] = replace(
            lifelong[0],
            apply_start="2020-01-01 09:00",
            apply_end="2020-01-10 18:00",
            start="2020-02-01",
            end="2020-02-28",
            source_status="수강종료",
        )
        lifelong[1] = replace(
            lifelong[1],
            apply_start="2020-01-01 09:00",
            apply_end="2020-01-10 18:00",
            start="2020-02-01",
            end="2020-02-28",
            source_status="수강종료",
        )
        other[0] = replace(
            other[0],
            apply_start="2020-01-01 09:00",
            apply_end="2020-01-10 18:00",
            start="2020-02-01",
            end="2020-02-28",
            source_status="수강종료",
        )
    output = {"lifelong": lifelong, "other": other}
    for rows in output.values():
        total = len(rows)
        for index, row in enumerate(list(rows)):
            rows[index] = replace(row, row_number=total - index)
    return output


def _status_markup(
    course: Course,
    *,
    missing_open_control: bool = False,
    wrong_control_identity: bool = False,
    inactive_control: bool = False,
) -> str:
    css = {
        "접수대기": "bt1",
        "접수중": "bt2",
        "수강대기": "bt3",
        "수강중": "bt3",
        "수강종료": "bt4",
        "강의종료": "bt4",
        "수강확정": "bt5",
        "폐강": "bt6",
    }[course.source_status]
    online = "온라인" in course.method
    control = ""
    should_control = (course.source_status == "접수중" and online and not missing_open_control) or (
        inactive_control and course.source_status == "접수대기"
    )
    if should_control:
        identity = str(int(course.identity) + 1) if wrong_control_identity else course.identity
        control = f'<a href="{escape(muan.muan_application_url(course.source, identity), quote=True)}">접수하기</a>'
    if course.method == "서면접수":
        return f'<span>{course.source_status}</span><span class="s_bt bt2 mat5">서면접수</span>'
    return f'<span class="s_bt {css}">{course.source_status}</span>{control}'


def _list_row(
    course: Course,
    *,
    identity: str | None = None,
    missing_open_control: bool = False,
    wrong_control_identity: bool = False,
    inactive_control: bool = False,
) -> str:
    detail = muan.muan_detail_url(course.source, identity or course.identity)
    place = (
        escape(course.branch)
        if course.direct_branch
        else (f'<span>{escape(course.branch)}</span><span class="fc_blue3">{escape(course.venue)}</span>')
    )
    return f"""
      <tr>
        <td>{course.row_number}</td>
        <td class="lecture_title"><a href="{escape(detail, quote=True)}">
          <span class="fc_blue3">{escape(course.title)}</span>
          <span>강 사 명 : 저장금지 강사 010-7777-8888 staff@example.org</span>
          <span>신청기간 : {course.apply_start} ~ {course.apply_end}</span>
          <span>교육기간 : {course.start} ~ {course.end}</span>
          <span>접수방법 : {escape(course.method)}</span>
        </a></td>
        <td class="lecture_title">{place}</td>
        <td><div>{escape(course.selection)}</div>
          <span class="apply">{course.capacity_current}</span>
          <span class="wait">({course.capacity_wait})</span> /
          <span class="fix_poeple">{course.capacity_total}명</span></td>
        <td class="btn_style">{
        _status_markup(
            course,
            missing_open_control=missing_open_control,
            wrong_control_identity=wrong_control_identity,
            inactive_control=inactive_control,
        )
    }</td>
      </tr>
    """


def _search_form(source_code: str, *, header_mutation: bool = False) -> str:
    source = _source(source_code)
    statuses = list(muan.MUAN_SEARCH_STATUSES)
    if header_mutation:
        statuses[-1] = ("128", "완료")
    status_options = "".join(f'<option value="{value}">{label}</option>' for value, label in statuses)
    type_options = "".join(f'<option value="{value}">{label}</option>' for value, label in muan.MUAN_SEARCH_TYPES)
    return f"""
      <form id="list_search" class="list_sch2" action="{source.path}">
        <input type="hidden" name="csrf_token" value="{"a" * 64}">
        <select name="search_status" id="search_status">{status_options}</select>
        <select name="search_type" id="search_type">{type_options}</select>
        <input type="text" id="search_word" name="search_word" value="">
        <input type="submit" value="검색">
      </form>
    """


def _pager(source_code: str, page: int, pages: int, *, sentinel: bool) -> str:
    source = _source(source_code)
    if sentinel:
        body = (
            f'<a class="first" href="{source.path}?page=1">&lt;&lt;</a>'
            f'<a class="prev" href="{source.path}?page={pages}">&lt;</a>'
        )
    else:
        body = f'<a class="on">{page}</a>'
        if page < pages:
            body += f'<a class="last" href="{source.path}?page={pages}">&gt;&gt;</a>'
    return f'<div class="list_paging"><div class="num">{body}</div></div>'


def _list_html(
    source_code: str,
    all_rows: list[Course],
    page: int,
    *,
    first_mutation: bool = False,
    last_mutation: bool = False,
    sentinel_data: bool = False,
    duplicate_identity: bool = False,
    header_mutation: bool = False,
    missing_open_control: bool = False,
    wrong_control_identity: bool = False,
    inactive_control: bool = False,
) -> str:
    source = _source(source_code)
    total = len(all_rows)
    pages = (total + muan.MUAN_PAGE_SIZE - 1) // muan.MUAN_PAGE_SIZE
    sentinel = page == pages + 1
    start = (page - 1) * muan.MUAN_PAGE_SIZE
    visible = list(all_rows[start : start + muan.MUAN_PAGE_SIZE]) if page <= pages else []
    if first_mutation and visible:
        visible[0] = replace(visible[0], title=visible[0].title + " 변경")
    if last_mutation and visible:
        visible[0] = replace(visible[0], title=visible[0].title + " 변경")
    rows: list[str] = []
    if sentinel and sentinel_data:
        rows.append(_list_row(all_rows[-1]))
    else:
        for index, course in enumerate(visible):
            forced = all_rows[0].identity if duplicate_identity and page == 2 and index == 0 else None
            rows.append(
                _list_row(
                    course,
                    identity=forced,
                    missing_open_control=missing_open_control,
                    wrong_control_identity=wrong_control_identity,
                    inactive_control=inactive_control,
                )
            )
    if not rows:
        rows.append('<tr><td colspan="5">개설된 강좌가 없습니다.</td></tr>')
    headers = list(muan._LIST_HEADERS)
    if header_mutation:
        headers[-1] = "상태"
    shown = len(visible) if not sentinel else 0
    title_extra = "전체 < " if sentinel else ""
    return f"""
      <html><head><title>{page} 페이지 목록보기 &lt; {title_extra}{source.label}
        &lt; 교육신청 &lt; 평생교육 - 무안군청</title></head><body>
      <div id="content">{_search_form(source_code, header_mutation=header_mutation)}
        <table class="list_table">
          <caption>{source.label} 게시물. 총 {total}건, {pages}페이지 중 {page}페이지 {shown}건 입니다.</caption>
          <thead><tr>{"".join(f"<th>{escape(item)}</th>" for item in headers)}</tr></thead>
          <tbody>{"".join(rows)}</tbody>
        </table>{_pager(source_code, page, pages, sentinel=sentinel)}
      </div></body></html>
    """


def _detail_html(
    course: Course,
    *,
    title_mismatch: bool = False,
    missing_control: bool = False,
    wrong_control_identity: bool = False,
    unknown_field: bool = False,
) -> str:
    source = _source(course.source)
    title = course.title + (" 변경" if title_mismatch else "")
    detail_method = {
        "서면접수": "서면",
        "온라인접수": "온라인",
        "병합(온라인+서면)접수": "병합(온라인+서면)",
    }[course.method]
    app = ""
    if course.source_status == "접수중" and "온라인" in course.method and not missing_control:
        identity = str(int(course.identity) + 1) if wrong_control_identity else course.identity
        app = f'<a href="{escape(muan.muan_application_url(course.source, identity), quote=True)}">수강신청</a>'
    rows = [
        ("강좌명(기수)", title),
        ("신청기간", f"{course.apply_start} ~ {course.apply_end}"),
        ("교육기간", f"{course.start} ~ {course.end}"),
        ("수강신청방법", detail_method),
        ("수강대상선정방법", course.selection),
        ("교육대상", course.target),
        ("보호자동의여부", "미동의"),
        ("모집정원", f"{course.capacity_total} 명 : {course.selection}"),
        ("모집대기인원", "3 명"),
        ("교육기관", course.branch),
        ("교육장소", course.venue),
        ("수강료", course.fee),
        ("문의전화", "061-450-5338"),
        ("강좌소개 강의계획", "저장하면 안 되는 자유 본문 staff@example.org"),
        ("강사명", "저장금지 강사"),
        ("강사소개", "010-7777-8888 저장금지"),
    ]
    if unknown_field:
        rows.append(("비밀필드", "비밀값"))
    body = "".join(f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>" for label, value in rows)
    return f"""
      <html><head><title>{escape(title)} &lt; {source.label} &lt; 교육신청
        &lt; 평생교육 - 무안군청</title></head><body>
      <div id="content"><h3>강좌정보</h3>
        <table class="view_table"><tbody>{body}</tbody></table>{app}
      </div></body></html>
    """


class FakeSite:
    def __init__(self, *, no_current: bool = False) -> None:
        self.rows = _courses(no_current=no_current)
        self.calls: list[str] = []
        self.page_calls: dict[tuple[str, int], int] = {}
        self.detail_calls: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        self.first_mutation = False
        self.last_mutation = False
        self.sentinel_data = False
        self.duplicate_identity = False
        self.header_mutation = False
        self.missing_open_control = False
        self.wrong_control_identity = False
        self.inactive_control = False
        self.title_mismatch = False
        self.missing_detail_control = False
        self.wrong_detail_control_identity = False
        self.unknown_detail_field = False
        self.bad_content_type = False
        self.redirected = False
        self.changed_final_url = False

    def fetcher(self, session: Any, url: str, timeout: int) -> Response:
        del session, timeout
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        source = muan._SOURCE_BY_PATH[parsed.path]
        with self.lock:
            self.calls.append(url)
        mode = (query.get("mode") or [""])[0]
        assert mode != "reserve_form", "application form was fetched"
        if mode == "view":
            identity = query["idx"][0]
            course = next(row for row in self.rows[source.code] if row.identity == identity)
            with self.lock:
                self.detail_calls.append((source.code, identity))
            html = _detail_html(
                course,
                title_mismatch=self.title_mismatch,
                missing_control=self.missing_detail_control,
                wrong_control_identity=self.wrong_detail_control_identity,
                unknown_field=self.unknown_detail_field,
            )
        else:
            page = int(query["page"][0])
            key = (source.code, page)
            with self.lock:
                self.page_calls[key] = self.page_calls.get(key, 0) + 1
                call = self.page_calls[key]
            pages = (len(self.rows[source.code]) + muan.MUAN_PAGE_SIZE - 1) // muan.MUAN_PAGE_SIZE
            html = _list_html(
                source.code,
                self.rows[source.code],
                page,
                first_mutation=(self.first_mutation and source.code == "lifelong" and page == 1 and call > 1),
                last_mutation=(self.last_mutation and source.code == "lifelong" and page == pages and call > 1),
                sentinel_data=self.sentinel_data and source.code == "lifelong",
                duplicate_identity=self.duplicate_identity and source.code == "lifelong",
                header_mutation=self.header_mutation and source.code == "lifelong",
                missing_open_control=self.missing_open_control,
                wrong_control_identity=self.wrong_control_identity,
                inactive_control=self.inactive_control,
            )
        return Response(
            html,
            url,
            content_type="application/json" if self.bad_content_type else "text/html;charset=UTF-8",
            history=(object(),) if self.redirected else (),
            final_url=(url + "&changed=1") if self.changed_final_url else None,
        )


def _collect(site: FakeSite, **kwargs: Any):
    return muan.collect_muan_education(
        TARGET,
        today=kwargs.pop("today", "2026-07-21"),
        session_factory=DummySession,
        fetcher=site.fetcher,
        **kwargs,
    )


def test_target_alias_and_owner_boundaries_are_exact() -> None:
    for url in muan.MUAN_ALIAS_URLS:
        assert muan.is_muan_education_target({"provider": muan.MUAN_PROVIDER, "url": url})
        assert muan.is_muan_alias_target({"url": url})
    assert not muan.is_muan_excluded_target(TARGET)
    assert not muan.is_muan_education_target({**TARGET, "provider": "MUNI_OTHER"})
    for unsafe in (
        muan.MUAN_CANDIDATE_URL.replace("https://", "http://"),
        muan.MUAN_CANDIDATE_URL + "?page=1",
        muan.MUAN_CANDIDATE_URL + "#fragment",
        muan.MUAN_CANDIDATE_URL.replace("www.muan.go.kr", "user@www.muan.go.kr"),
        muan.MUAN_CANDIDATE_URL.replace("www.muan.go.kr", "www.muan.go.kr:443"),
    ):
        assert not muan.is_muan_education_target({**TARGET, "url": unsafe})
    for url in (*muan.MUAN_EXCLUDED_URLS.values(), *muan.MUAN_SEPARATE_OWNER_URLS.values()):
        assert muan.is_muan_excluded_target({"url": url})
        assert not muan.is_muan_education_target({**TARGET, "url": url})
    assert muan.MUAN_MUNICIPALITY_NAME == "전남광주통합특별시 무안군"
    assert muan.MUAN_PROVIDER_NAME == "무안군청"


def test_url_builders_are_source_bound_and_deterministic() -> None:
    assert muan.muan_list_url("lifelong", 2) == muan.MUAN_LIFELONG_URL + "?page=2"
    assert muan.muan_detail_url("other", "201") == (muan.MUAN_OTHER_URL + "?idx=201&mode=view")
    assert muan.muan_application_url("lifelong", 101) == (muan.MUAN_LIFELONG_URL + "?lecture_idx=101&mode=reserve_form")
    for args in (("unknown", 1), ("lifelong", 0), ("lifelong", True)):
        with pytest.raises(ValueError):
            muan.muan_list_url(*args)
    with pytest.raises(ValueError):
        muan.muan_detail_url("lifelong", "1/2")


def test_complete_snapshot_traverses_both_sources_details_and_controls() -> None:
    site = FakeSite()
    rows, parser, meta = _collect(site)

    assert parser == muan.MUAN_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert meta["source_totals"] == {"lifelong": 16, "other": 2}
    assert meta["source_pages"] == {"lifelong": 2, "other": 1}
    assert meta["source_total"] == 18
    assert meta["required_list_requests"] == 9
    assert meta["list_requests"] == 9
    assert meta["sentinel_requests"] == 2
    assert meta["stability_rechecks"] == 4
    assert meta["detail_attempts"] == 3
    assert meta["detail_pages"] == 3
    assert meta["current_future_counts"] == {"lifelong": 2, "other": 1}
    assert meta["status_counts"] == {"OPEN": 1, "SCHEDULED": 1, "CLOSED": 1}
    assert meta["visible_public_application_control_count"] == 1
    assert len(rows) == 3
    assert {row["title"] for row in rows} == {
        "AI 영상 제작",
        "가을 시민 글쓰기",
        "어르신 한글교실",
    }
    assert {row["branch"] for row in rows} == {
        "무안군 자치행정과",
        "무안군 주민생활과",
    }
    assert {row["provider_course_id"].split(":")[-2] for row in rows} == {"lifelong", "other"}
    assert all(row["municipality_code"] == "1281000000" for row in rows)
    assert all(row["municipality_name"] == "전남광주통합특별시 무안군" for row in rows)
    assert all(row["raw_fields"]["detail_verified"] is True for row in rows)
    assert sum(row["reservation_available"] for row in rows) == 1
    assert all("mode=reserve_form" not in url for url in site.calls)
    payload = repr(rows)
    assert "010-7777-8888" not in payload
    assert "061-450-5338" not in payload
    assert "staff@example.org" not in payload
    assert "저장금지 강사" not in payload
    assert "자유 본문" not in payload


def test_complete_history_can_produce_valid_no_current_snapshot() -> None:
    site = FakeSite(no_current=True)
    rows, _, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["current_source_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["detail_pages"] == 0
    assert meta["details_complete"] is True
    assert meta["application_controls_complete"] is True
    assert site.detail_calls == []


@pytest.mark.parametrize(
    "flag",
    [
        "first_mutation",
        "last_mutation",
        "sentinel_data",
        "duplicate_identity",
        "header_mutation",
    ],
)
def test_list_boundary_or_schema_mutation_fails_closed(flag: str) -> None:
    site = FakeSite()
    setattr(site, flag, True)
    rows, _, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


@pytest.mark.parametrize(
    "flag",
    ["missing_open_control", "wrong_control_identity", "inactive_control"],
)
def test_list_application_control_contract_fails_closed(flag: str) -> None:
    site = FakeSite()
    setattr(site, flag, True)
    rows, _, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "control" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "flag",
    [
        "title_mismatch",
        "missing_detail_control",
        "wrong_detail_control_identity",
        "unknown_detail_field",
    ],
)
def test_current_detail_contract_mutation_fails_closed(flag: str) -> None:
    site = FakeSite()
    setattr(site, flag, True)
    rows, _, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["detail_errors"] >= 1
    assert meta["configured_collection_error"]


@pytest.mark.parametrize("flag", ["bad_content_type", "redirected", "changed_final_url"])
def test_transport_contract_fails_closed(flag: str) -> None:
    site = FakeSite()
    setattr(site, flag, True)
    rows, _, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["configured_collection_error"]


def test_page_and_detail_caps_never_emit_partial_snapshots() -> None:
    site = FakeSite()
    rows, _, meta = _collect(site, max_pages=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["list_requests"] == 2
    assert meta["required_list_requests"] == 9

    site = FakeSite()
    rows, _, meta = _collect(site, detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["current_source_count"] == 3
    assert meta["detail_attempts"] == 0


def test_dedupe_cannot_collapse_source_prefixed_identities() -> None:
    site = FakeSite()
    rows, _, meta = _collect(site, dedupe_rows=lambda values: values[:1])

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "cardinality" in meta["configured_collection_error"]


def test_open_status_outside_application_period_fails_closed() -> None:
    site = FakeSite()
    first = site.rows["lifelong"][0]
    site.rows["lifelong"][0] = replace(
        first,
        apply_start="2026-06-01 09:00",
        apply_end="2026-06-10 18:00",
    )
    rows, _, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "OPEN date contradiction" in meta["configured_collection_error"]


def test_invalid_target_and_limits_fail_without_partial_data() -> None:
    site = FakeSite()
    rows, _, meta = muan.collect_muan_education(
        {"provider": "OTHER", "url": muan.MUAN_CANDIDATE_URL},
        session_factory=DummySession,
        fetcher=site.fetcher,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert site.calls == []

    rows, _, meta = _collect(FakeSite(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["list_requests"] == 0


def test_discovery_audit_records_live_boundaries_and_exclusion_reasons() -> None:
    audit = muan.MUAN_DISCOVERY_AUDIT
    assert audit["checked_on"] == "2026-07-21"
    assert audit["structured_source_totals"] == {"lifelong": 140, "other": 6}
    assert audit["structured_data_pages"] == {"lifelong": 10, "other": 1}
    assert audit["current_future_total"] == 0
    assert audit["resident_centre_notice_programme_rows"] == 11
    assert audit["information_education_rows"] == 6
    assert audit["hope_course_resident_proposals"] == 29
    assert "per-course" in audit["resident_centre_notice_exclusion"]
    assert "phone/visit" in audit["information_education_exclusion"]
    assert muan.MUAN_CANDIDATE_AUDIT[muan.MUAN_CANDIDATE_ID]["provider"] == muan.MUAN_PROVIDER
