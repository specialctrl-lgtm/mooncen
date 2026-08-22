from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import hashlib
import html
import math
import os
from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
import requests

from Crawler import municipal_uljin as uljin


@dataclass(frozen=True)
class Course:
    identity: str
    title: str
    category: str
    event_start: str
    event_end: str
    apply_start: str
    apply_end: str
    schedule: str
    venue: str
    fee_material: str
    raw_status: str
    current: int
    total: int


COURSES = (
    Course(
        "RE0002001",
        "경제특강<65세 이상 과정> - 공개강사",
        "02",
        "2026-08-01",
        "2026-08-01",
        "2026-07-20",
        "2026-07-30",
        "토 10:00~12:00",
        "3층 강의실",
        "없음 / 없음",
        "접수중",
        4,
        20,
    ),
    Course(
        "RE0002002",
        "02. 생활공예-공개강사[주간]",
        "02",
        "2026-08-03",
        "2026-11-20",
        "2026-07-20",
        "2026-07-30",
        "월 10:00~12:00",
        "2층 예능교육실",
        "40,000원 / 120,000원",
        "대기자 접수중",
        12,
        10,
    ),
    Course(
        "RE0002003",
        "03. 기초영어회화-공개강사[주간]",
        "02",
        "2026-08-03",
        "2026-11-20",
        "2026-07-01",
        "2026-07-06",
        "화,목 10:00~11:00",
        "2층 강의실",
        "40,000원/15,000원",
        "접수완료",
        20,
        15,
    ),
    Course(
        "RE0002004",
        "가을 자격과정",
        "02",
        "2026-09-01",
        "2026-10-31",
        "2026-08-01",
        "2026-08-05",
        "수 19:00~21:00",
        "3층 강의실",
        "10,000원 / 없음",
        "접수예정",
        0,
        15,
    ),
    Course(
        "RE0002005",
        "[수련관]-교과연계수학",
        "03",
        "2026-07-29",
        "2026-08-21",
        "2026-07-15",
        "2026-07-16",
        "월수금 14:00~16:00",
        "울진군청소년수련관",
        "",
        "접수완료",
        6,
        10,
    ),
    Course(
        "RE0002006",
        "[수련관]-창의미술",
        "03",
        "2026-07-01",
        "2026-08-21",
        "2026-06-20",
        "2026-06-21",
        "화목 10:00~12:00",
        "울진군청소년수련관",
        "재료비 18,000원",
        "교육중",
        8,
        8,
    ),
    Course(
        "RE0002007",
        "[수련관]-겨울방학 종료과정",
        "03",
        "2026-01-05",
        "2026-01-30",
        "2025-12-29",
        "2025-12-30",
        "월수금 10:00~12:00",
        "울진군청소년수련관",
        "",
        "교육종료",
        5,
        8,
    ),
    Course(
        "RE0002008",
        "[문화의집] - 영어 핵심 공략",
        "03",
        "2026-08-01",
        "2026-08-25",
        "2026-07-10",
        "2026-07-11",
        "수,금 15:00~17:00",
        "(후포) 청소년문화의집",
        "교재 준비",
        "접수완료",
        3,
        8,
    ),
    Course(
        "RE0002009",
        "[수련관]-바이올린",
        "03",
        "2026-08-01",
        "2026-08-25",
        "2026-07-10",
        "2026-07-11",
        "화수금 14:30~16:30",
        "울진군청소년수련관",
        "연습용 악기 준비",
        "접수완료",
        8,
        8,
    ),
    Course(
        "RE0002010",
        "실험과학",
        "05",
        "2026-06-13",
        "2026-08-08",
        "2026-06-08",
        "2026-06-08",
        "10:00~10:50",
        "2F 체험학습실1",
        "8천원/없음",
        "교육중",
        20,
        10,
    ),
    Course(
        "RE0002011",
        "창의로봇",
        "05",
        "2026-08-10",
        "2026-09-10",
        "2026-07-01",
        "2026-07-02",
        "15:30~16:20",
        "2F 체험학습실2",
        "8천원/없음",
        "접수완료",
        10,
        10,
    ),
    Course(
        "RE0002012",
        "로봇 코딩",
        "05",
        "2026-08-10",
        "2026-09-10",
        "2026-07-20",
        "2026-07-30",
        "13:30~14:20",
        "2F 체험학습실1",
        "8천원/없음",
        "접수중",
        2,
        10,
    ),
)


STATUS_CLASS = {
    "접수예정": "rec rec01",
    "접수중": "rec rec02",
    "대기자 접수중": "rec rec03",
    "접수완료": "rec rec04",
    "교육중": "rec rec04",
    "교육종료": "rec rec04",
}
ACTIVE = {"접수중", "대기자 접수중"}


def _params(*, page: int, category: str, date_mode: str) -> dict[str, str]:
    return {
        "menuCd": uljin.ULJIN_LIST_MENU,
        "searchCondition": "RE_NAME",
        "searchKeyword": "",
        "orderField": "RE_NAME" if category == "02" else "reSdate",
        "orderSort": "asc" if category == "02" else "desc",
        "searchDateGubun": date_mode,
        "gubun": category,
        "startPage": str(page),
    }


def _href(params: dict[str, str]) -> str:
    return "/index.uljin?" + urlencode(params)


def _detail_href(course: Course, *, page: int, category: str, date_mode: str) -> str:
    values = _params(page=page, category=category, date_mode=date_mode)
    values = {
        "menuCd": uljin.ULJIN_APPLICATION_MENU,
        "reUniqId": course.identity,
        "searchCondition": values["searchCondition"],
        "searchKeyword": values["searchKeyword"],
        "orderField": values["orderField"],
        "orderSort": values["orderSort"],
        "searchDateGubun": values["searchDateGubun"],
        "startPage": values["startPage"],
        "gubun": values["gubun"],
    }
    return _href(values)


def _category_registry(selected: str, *, wrong: bool = False) -> str:
    entries = [
        ("", "전체"),
        ("02", "울진군 평생학습관"),
        ("03", "청소년수련시설"),
        ("07", "울진문화예술회관"),
        ("05", "과학체험관"),
        ("01", "농업기술센터"),
    ]
    if wrong:
        entries[-1] = ("01", "농업교육")
    items = "".join(
        (
            f'<li class="{"on" if code == selected else ""}">'
            f'<a href="#" onclick="searchGuBun(\'{code}\');"><span>{label}</span><i></i></a>'
            "</li>"
        )
        for code, label in entries
    )
    return f'<article class="menu_tab"><ul class="col3">{items}</ul></article>'


def _form(*, page: int, category: str, date_mode: str) -> str:
    order_field = "RE_NAME" if category == "02" else "reSdate"
    order_sort = "asc" if category == "02" else "desc"
    return f"""
    <form action="/index.uljin" name="listForm" method="get">
      <input type="hidden" name="menuCd" value="{uljin.ULJIN_LIST_MENU}">
      <input type="hidden" name="startPage" value="{page}">
      <input type="hidden" name="searchCondition" value="RE_NAME">
      <input type="hidden" name="orderField" value="{order_field}">
      <input type="hidden" name="searchDateGubun" value="{date_mode}">
      <input type="hidden" name="gubun" value="{category}">
      <fieldset>
        <ul class="btn_condition">
          <li class="{'on' if date_mode == '3' else 'off'}"><button onclick="searchDatefunc('3')">전체</button></li>
          <li class="{'on' if date_mode == '1' else 'off'}"><button onclick="searchDatefunc('1')">모집중</button></li>
        </ul>
        <select id="date_order" name="orderSort">
          <option value="desc"{' selected' if order_sort == 'desc' else ''}>교육기간 빠른순</option>
          <option value="asc"{' selected' if order_sort == 'asc' else ''}>교육기간 느린순</option>
        </select>
        <input name="searchKeyword" value="">
      </fieldset>
    </form>
    """


def _course_html(
    course: Course,
    *,
    page: int,
    category: str,
    date_mode: str,
    missing_active_href: bool = False,
) -> str:
    href = html.escape(
        _detail_href(course, page=page, category=category, date_mode=date_mode),
        quote=True,
    )
    active = course.raw_status in ACTIVE
    if active:
        control = (
            '<a class="possible possible01 blink"'
            + ("" if missing_active_href else f' href="{href}"')
            + "><span>교육신청</span></a>"
        )
    else:
        control = '<a class="possible possible02"><span>접수마감</span></a>'
    introduction = ""
    if category == "02":
        introduction = (
            '<p class="rec rec00"><a href="https://www.uljin.go.kr/learning/board/view.uljin?'
            'dataSid=1">강좌소개 담당자 010-9999-9999 private@example.test</a></p>'
        )
    # Deliberately leave angle brackets in the title unescaped.  The official
    # source does this, and the collector must recover the following siblings.
    return f"""
    <li>
      <dl>
        <dt><a href="{href}">{course.title}</a></dt>
        <dd><strong>교육기간</strong> {course.event_start} ~ {course.event_end}</dd>
        <dd><strong>교육시간</strong> {course.schedule}</dd>
        <dd><strong>접수기간</strong> {course.apply_start} ~ {course.apply_end}</dd>
        <dd><strong>교육장</strong>{course.venue}</dd>
        <dd><strong>수강료/재료비</strong>{course.fee_material}</dd>
      </dl>
      <div class="r_btn">
        {introduction}
        <p class="{STATUS_CLASS[course.raw_status]}">{course.raw_status}<span>{course.current}/{course.total}</span></p>
        {control}
      </div>
    </li>
    """


def _page_html(
    rows: list[Course],
    *,
    requested: int,
    total: int,
    category: str,
    date_mode: str,
    global_open: int,
    last: int,
    wrong_registry: bool = False,
    bad_empty: bool = False,
    missing_active_href: bool = False,
) -> str:
    if total:
        body = "".join(
            _course_html(
                row,
                page=requested,
                category=category,
                date_mode=date_mode,
                missing_active_href=missing_active_href,
            )
            for row in rows
        )
    elif bad_empty:
        body = "<li><dl><dt>데이터 없음</dt></dl></li>"
    else:
        body = "<li><dl><dt>검색된 자료가 없습니다.</dt></dl></li>"
    pager_parts: list[str] = []
    for number in range(1, last + 1):
        class_value = ' class="on"' if requested == number else ""
        pager_parts.append(
            f'<span{class_value}><a href="{html.escape(_href(_params(page=number, category=category, date_mode=date_mode)), quote=True)}">{number}</a></span>'
        )
    if total == 0:
        pager_parts.append(
            f'<span class="control"><a href="{html.escape(_href(_params(page=0, category=category, date_mode=date_mode)), quote=True)}">다음</a></span>'
        )
    return f"""
    <!doctype html><html lang="ko"><head><title>교육/강좌</title></head><body>
      <section class="s_contents"><div class="s_top"><h3 class="s_tit_01">교육/강좌</h3></div>
        {_category_registry(category, wrong=wrong_registry)}
        {_form(page=requested, category=category, date_mode=date_mode)}
        <ul class="search_result"><li>모집중 : <span>{global_open}</span>건</li><li class="last">검색된 결과 : <span>{total}</span>건</li></ul>
        <div class="bbs_list01 type2"><ul>{body}</ul></div>
        <div class="bbs_page mt30">{''.join(pager_parts)}</div>
        <form action="/private/apply" method="post"><input name="applicantName" value="비공개신청자"><input name="phone" value="010-1111-2222"></form>
      </section>
    </body></html>
    """


@dataclass
class Response:
    url: str
    body: str
    status_code: int = 200
    history: tuple[Any, ...] = ()

    @property
    def content(self) -> bytes:
        return self.body.encode("utf-8")


class SyntheticSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SyntheticBackend:
    def __init__(
        self,
        *,
        duplicate_source: bool = False,
        partition_overlap: bool = False,
        partition_missing: bool = False,
        partition_drift: bool = False,
        overflow_drift: bool = False,
        unstable_recheck: bool = False,
        bad_empty: bool = False,
        wrong_registry: bool = False,
        missing_active_href: bool = False,
        response_url_drift: bool = False,
        unknown_youth_venue: bool = False,
    ) -> None:
        self.duplicate_source = duplicate_source
        self.partition_overlap = partition_overlap
        self.partition_missing = partition_missing
        self.partition_drift = partition_drift
        self.overflow_drift = overflow_drift
        self.unstable_recheck = unstable_recheck
        self.bad_empty = bad_empty
        self.wrong_registry = wrong_registry
        self.missing_active_href = missing_active_href
        self.response_url_drift = response_url_drift
        self.unknown_youth_venue = unknown_youth_venue
        self.urls: list[str] = []
        self.full_page_one_calls = 0

    def _selected(self, category: str, date_mode: str) -> list[Course]:
        selected = list(COURSES)
        if self.unknown_youth_venue:
            selected = [
                replace(row, venue="임시교실") if row.identity == "RE0002005" else row
                for row in selected
            ]
        if category:
            selected = [row for row in selected if row.category == category]
            if self.partition_overlap and category == "03":
                selected.append(next(row for row in COURSES if row.identity == "RE0002001"))
            if self.partition_missing and category == "03":
                selected = [row for row in selected if row.identity != "RE0002009"]
            if self.partition_drift and category == "05":
                selected = [
                    replace(row, title=row.title + " 변경")
                    if row.identity == "RE0002010"
                    else row
                    for row in selected
                ]
        if date_mode == "1":
            selected = [row for row in selected if row.raw_status in ACTIVE]
        if category == "02":
            selected.sort(key=lambda row: row.title)
        return selected

    def __call__(self, _session: Any, url: str, _timeout: int) -> Response:
        self.urls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        assert parsed.scheme == "https"
        assert parsed.hostname == uljin.ULJIN_HOST
        assert parsed.path == uljin.ULJIN_PATH
        assert query.get("menuCd") == [uljin.ULJIN_LIST_MENU], "application/detail endpoint fetched"
        page = int(query["startPage"][0])
        category = query["gubun"][0]
        date_mode = query["searchDateGubun"][0]
        selected = self._selected(category, date_mode)
        if self.duplicate_source and not category and date_mode == "3":
            selected[-1] = replace(selected[0])
        total = len(selected)
        last = max(1, math.ceil(total / uljin.ULJIN_PAGE_SIZE))
        effective = page
        if page > last:
            effective = 1 if self.overflow_drift else last
        start = (effective - 1) * uljin.ULJIN_PAGE_SIZE
        rows = selected[start : start + uljin.ULJIN_PAGE_SIZE]
        if not category and date_mode == "3" and page == 1:
            self.full_page_one_calls += 1
            if self.unstable_recheck and self.full_page_one_calls >= 2:
                rows = [
                    replace(row, current=row.current + 1)
                    if row.identity == "RE0002001"
                    else row
                    for row in rows
                ]
        global_open = sum(row.raw_status in ACTIVE for row in COURSES)
        body = _page_html(
            rows,
            requested=page,
            total=total,
            category=category,
            date_mode=date_mode,
            global_open=global_open,
            last=last,
            wrong_registry=self.wrong_registry,
            bad_empty=self.bad_empty and category == "07",
            missing_active_href=self.missing_active_href,
        )
        response_url = url + "&drift=1" if self.response_url_drift else url
        return Response(response_url, body)


def _target(*, legacy: bool = False) -> dict[str, str]:
    return {
        "provider": uljin.ULJIN_PROVIDER,
        "url": uljin.ULJIN_LEGACY_TARGET_URL if legacy else uljin.ULJIN_CANONICAL_URL,
    }


def _collect(
    backend: SyntheticBackend,
    *,
    target: dict[str, str] | None = None,
    max_pages: int = 10,
    detail_limit: int = 0,
    dedupe_rows: Any = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], SyntheticSession]:
    session = SyntheticSession()
    rows, parser, meta = uljin.collect_uljin_education(
        target or _target(),
        timeout=5,
        max_pages=max_pages,
        detail_limit=detail_limit,
        today="2026-07-23",
        session_factory=lambda: session,
        fetcher=backend,
        dedupe_rows=dedupe_rows,
    )
    return rows, parser, meta, session


def test_hashes_exact_targets_incumbent_and_owner_boundaries() -> None:
    digest = hashlib.sha256(uljin.ULJIN_CANONICAL_URL.encode()).hexdigest()
    assert digest == uljin.ULJIN_CANONICAL_URL_SHA256
    assert uljin.ULJIN_CANONICAL_CANDIDATE_ID == "MUNI_IR_" + digest[:12].upper()
    assert uljin.is_uljin_education_target(_target())
    assert uljin.is_uljin_education_target(_target(legacy=True))
    assert not uljin.is_uljin_education_target(
        {"provider": uljin.ULJIN_PROVIDER, "url": "https://www.uljin.go.kr/"}
    )
    assert not uljin.is_uljin_education_target(
        {"provider": "MUNI_WWW_ULJIN_GO_KR_C47E88C6", "url": uljin.ULJIN_CANONICAL_URL}
    )
    assert not uljin.is_uljin_education_target(
        {"provider": uljin.ULJIN_PROVIDER, "url": uljin.ULJIN_CANONICAL_URL + "&x=1"}
    )
    assert uljin.ULJIN_CANDIDATE_AUDIT[uljin.ULJIN_CANONICAL_CANDIDATE_ID][
        "provider"
    ] == uljin.ULJIN_PROVIDER
    boundary_providers = {entry["provider"] for entry in uljin.ULJIN_OWNER_BOUNDARIES}
    assert uljin.ULJIN_LIBRARY_PROVIDER in boundary_providers
    assert uljin.ULJIN_EDUCATION_OFFICE_PROVIDER in boundary_providers
    assert "NATIONAL_OCEAN_SCIENCE_MUSEUM" in boundary_providers


def test_complete_snapshot_partitions_clamp_open_controls_branches_and_privacy() -> None:
    backend = SyntheticBackend()
    dedupe_calls: list[list[str]] = []

    def dedupe(rows: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
        dedupe_calls.append([str(row["provider_course_id"]) for row in rows])
        return rows

    rows, parser, meta, session = _collect(backend, dedupe_rows=dedupe)
    assert parser == uljin.ULJIN_PARSER
    assert session.closed
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["advertised_total"] == 12
    assert meta["current_source_count"] == 11
    assert meta["expired_source_count"] == 1
    assert meta["data_pages"] == 2
    assert meta["source_requests"] == 12
    assert meta["request_attempts"] == 12
    assert meta["category_filter_requests"] == 5
    assert meta["open_filter_requests"] == 1
    assert meta["full_recheck_requests"] == 3
    assert meta["category_partition_counts"] == {
        "02": 4,
        "03": 5,
        "07": 0,
        "05": 3,
        "01": 0,
    }
    assert meta["category_partition_union_count"] == 12
    assert meta["category_partition_overlap_count"] == 0
    assert meta["empty_category_filter_count"] == 2
    assert meta["open_filter_source_count"] == 3
    assert meta["overflow_clamp_verified"] is True
    assert meta["stable_full_recheck"] is True
    assert meta["stable_full_recheck_after_filters"] is True
    assert meta["application_control_count"] == 3
    assert meta["detail_requests"] == 0
    assert meta["application_endpoint_requests"] == 0
    assert meta["login_endpoint_requests"] == 0
    assert len(rows) == 11
    assert len(dedupe_calls) == 1

    by_identity = {row["raw_fields"]["identity"]: row for row in rows}
    assert "RE0002007" not in by_identity
    assert by_identity["RE0002001"]["branch"] == "울진군 평생학습관"
    assert by_identity["RE0002001"]["fee_amount"] == 0
    assert by_identity["RE0002002"]["status"] == "WAITLIST"
    assert by_identity["RE0002002"]["fee_amount"] == 40_000
    assert by_identity["RE0002002"]["material_fee_amount"] == 120_000
    assert by_identity["RE0002004"]["status"] == "SCHEDULED"
    assert by_identity["RE0002005"]["branch"] == "울진군청소년수련관"
    assert by_identity["RE0002008"]["branch"] == "울진군청소년문화의집"
    assert by_identity["RE0002010"]["branch"] == "울진과학체험관"
    assert by_identity["RE0002010"]["fee_amount"] == 8_000
    assert by_identity["RE0002001"]["reservation_available"] is True
    assert by_identity["RE0002003"]["application_url"] == ""
    assert all(row["raw_url"].startswith(uljin.ULJIN_LEGACY_TARGET_URL) for row in rows)
    assert all(
        row["raw_fields"]["detail_endpoint_fetched"] is False
        and row["raw_fields"]["application_endpoint_fetched"] is False
        and row["raw_fields"]["attachment_endpoint_fetched"] is False
        for row in rows
    )
    payload = repr(rows)
    assert "010-9999-9999" not in payload
    assert "private@example.test" not in payload
    assert "비공개신청자" not in payload
    assert all(
        parse_qs(urlparse(url).query).get("menuCd") == [uljin.ULJIN_LIST_MENU]
        for url in backend.urls
    )


@pytest.mark.parametrize(
    ("kwargs", "error_fragment"),
    [
        ({"duplicate_source": True}, "duplicate course identity within filter"),
        ({"partition_overlap": True}, "facility partitions overlap"),
        ({"partition_missing": True}, "facility partition union is incomplete"),
        ({"partition_drift": True}, "facility partition data drift"),
        ({"overflow_drift": True}, "post-last page did not clamp exactly"),
        ({"unstable_recheck": True}, "full ledger changed during stable recheck"),
        ({"bad_empty": True}, "empty catalogue sentinel drift"),
        ({"wrong_registry": True}, "active facility registry vocabulary drift"),
        ({"missing_active_href": True}, "active control drift"),
        ({"response_url_drift": True}, "response URL drift"),
        ({"unknown_youth_venue": True}, "unknown youth facility venue"),
    ],
)
def test_contract_drift_is_atomic(kwargs: dict[str, bool], error_fragment: str) -> None:
    rows, parser, meta, session = _collect(SyntheticBackend(**kwargs))
    assert parser == uljin.ULJIN_PARSER
    assert rows == []
    assert session.closed
    assert meta["snapshot_complete"] is False
    assert error_fragment in meta["configured_collection_error"]


def test_limits_managed_session_alias_and_dedupe_contract_fail_closed() -> None:
    rows, parser, meta = uljin.collect_uljin_education(_target(), today="2026-07-23")
    assert rows == []
    assert parser == uljin.ULJIN_PARSER
    assert meta["configured_collection_error"] == "managed session_factory injection is required"

    rows, _, meta, _ = _collect(SyntheticBackend(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "source cap" in meta["configured_collection_error"]

    rows, _, meta, _ = _collect(SyntheticBackend(), detail_limit=-1)
    assert rows == []
    assert "detail_limit" in meta["configured_collection_error"]

    rows, _, meta, _ = _collect(
        SyntheticBackend(),
        target=_target(legacy=True),
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert "dedupe_rows changed complete identity cardinality" in meta[
        "configured_collection_error"
    ]


def _live_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawlerLiveTest/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _live_snapshot() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, parser, meta = uljin.collect_uljin_education(
        _target(),
        timeout=30,
        max_pages=10,
        detail_limit=0,
        today=date(2026, 7, 23),
        session_factory=_live_session,
    )
    assert parser == uljin.ULJIN_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    return rows, meta


@pytest.mark.skipif(
    os.getenv("RUN_ULJIN_LIVE") != "1",
    reason="set RUN_ULJIN_LIVE=1 for two bounded official-source snapshots",
)
def test_live_two_exact_stable_snapshots() -> None:
    first_rows, first_meta = _live_snapshot()
    second_rows, second_meta = _live_snapshot()
    assert first_rows == second_rows
    stable_meta_keys = (
        "advertised_total",
        "current_source_count",
        "expired_source_count",
        "data_pages",
        "category_partition_counts",
        "category_partition_pages",
        "open_filter_source_count",
        "source_status_counts",
        "status_counts",
        "branch_counts",
        "application_control_count",
        "source_requests",
        "request_attempts",
    )
    assert {key: first_meta[key] for key in stable_meta_keys} == {
        key: second_meta[key] for key in stable_meta_keys
    }
    for meta in (first_meta, second_meta):
        assert meta["advertised_total"] == 21
        assert meta["current_source_count"] == 21
        assert meta["expired_source_count"] == 0
        assert meta["data_pages"] == 3
        assert meta["category_partition_counts"] == {
            "02": 2,
            "03": 14,
            "07": 0,
            "05": 5,
            "01": 0,
        }
        assert meta["category_partition_pages"] == {
            "02": 1,
            "03": 2,
            "07": 1,
            "05": 1,
            "01": 1,
        }
        assert meta["open_filter_source_count"] == 2
        assert meta["source_status_counts"] == {
            "접수중": 2,
            "접수완료": 14,
            "교육중": 5,
        }
        assert meta["status_counts"] == {"OPEN": 2, "CLOSED": 19}
        assert meta["branch_counts"] == {
            "울진군 평생학습관": 2,
            "울진군청소년수련관": 14,
            "울진과학체험관": 5,
        }
        assert meta["application_control_count"] == 2
        assert meta["source_requests"] == 15
        assert meta["request_attempts"] == 15
        assert meta["detail_requests"] == 0
        assert meta["login_endpoint_requests"] == 0
        assert meta["application_endpoint_requests"] == 0
        assert meta["attachment_endpoint_requests"] == 0
        assert meta["overflow_clamp_verified"] is True
        assert meta["stable_full_recheck_after_filters"] is True
    assert len(first_rows) == 21
    identities = {row["raw_fields"]["identity"] for row in first_rows}
    assert identities == {
        "RE0001365",
        "RE0001364",
        "RE0001093",
        "RE0001391",
        "RE0001259",
        "RE0001257",
        "RE0000598",
        "RE0001092",
        "RE0001079",
        "RE0001008",
        "RE0000993",
        "RE0000764",
        "RE0000759",
        "RE0000605",
        "RE0000603",
        "RE0000599",
        "RE0001097",
        "RE0001098",
        "RE0001099",
        "RE0001100",
        "RE0001101",
    }
    assert sum(bool(row["application_url"]) for row in first_rows) == 2
    assert all(row["municipality_code"] == "4793000000" for row in first_rows)
