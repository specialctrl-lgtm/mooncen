from __future__ import annotations

from collections import Counter
import hashlib
import html
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_sunchang as sunchang


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FixtureGetter:
    def __init__(
        self,
        pages: Mapping[tuple[str, int], str | list[str]],
        details: Mapping[str, str | list[str]],
    ) -> None:
        self.pages = dict(pages)
        self.details = dict(details)
        self.offsets: Counter[tuple[str, str, int]] = Counter()
        self.calls: list[str] = []

    @staticmethod
    def _value(value: str | list[str], offset: int) -> str:
        if isinstance(value, list):
            return value[min(offset, len(value) - 1)]
        return value

    def __call__(self, _session: Any, url: str, _timeout: int) -> str:
        self.calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == sunchang.SUNCHANG_LIST_PATH:
            category = query["category"][0]
            page = int(query["page"][0])
            key = (category, page)
            if key not in self.pages:
                raise AssertionError(f"unexpected list request {key}")
            offset_key = ("list", category, page)
            offset = self.offsets[offset_key]
            self.offsets[offset_key] += 1
            return self._value(self.pages[key], offset)
        if parsed.path == sunchang.SUNCHANG_DETAIL_PATH:
            identity = query["articleSeq"][0]
            if identity not in self.details:
                raise AssertionError(f"unexpected detail request {identity}")
            offset_key = ("detail", identity, 0)
            offset = self.offsets[offset_key]
            self.offsets[offset_key] += 1
            return self._value(self.details[identity], offset)
        raise AssertionError(f"unsafe/unexpected endpoint {url}")


def _target(**changes: str) -> dict[str, str]:
    target = {
        "provider": sunchang.SUNCHANG_PROVIDER,
        "url": sunchang.SUNCHANG_CANONICAL_URL,
    }
    target.update(changes)
    return target


def _course(
    category: str,
    identity: str,
    *,
    title: str | None = None,
    status: str = "접수중",
    apply_period: str = "2026-07-01 ~ 2026-08-01",
    period: str = "2026-08-02 ~ 2026-11-30",
    schedule: str = "10:00 ~ 12:00",
    venue: str = "평생학습관",
    applied: int = 3,
    capacity: int = 12,
) -> dict[str, Any]:
    return {
        "category": category,
        "identity": identity,
        "title": title or f"강좌 {identity}",
        "status": status,
        "apply_period": apply_period,
        "period": period,
        "schedule": schedule,
        "venue": venue,
        "applied": applied,
        "capacity": capacity,
    }


def _detail_href(course: Mapping[str, Any], page: int) -> str:
    return (
        f"{sunchang.SUNCHANG_DETAIL_PATH}?"
        f"menuId={sunchang.SUNCHANG_MENU_ID}&page={page}&"
        f"category={course['category']}&searchMode=&searchTxt=&"
        f"articleSeq={course['identity']}"
    )


def _list_row(course: Mapping[str, Any], page: int) -> str:
    href = html.escape(_detail_href(course, page), quote=True)
    return f"""
      <a href="{href}">
        <div class="edu_list">
          <div class="edu_tit">
            <strong>{html.escape(str(course['title']))}</strong>
            <span>{html.escape(str(course['status']))}</span>
          </div>
          <dl>
            <dt><ul>
              <li><b>모집기간</b> {course['apply_period']}</li>
              <li><b>운영시간</b> {course['schedule']}</li>
              <li><b>운영기간</b> {course['period']}</li>
              <li><b>교육장소</b> {html.escape(str(course['venue']))}</li>
            </ul></dt>
            <dd><ul>
              <li><b>{course['applied']}명</b></li><li>/</li>
              <li><b>{course['capacity']}명</b></li>
            </ul></dd>
          </dl>
        </div>
      </a>
    """


def _pager(page: int, last: int, *, empty: bool) -> str:
    if last == 0:
        return '<div class="board_pager"></div>'
    links = []
    for number in range(1, last + 1):
        if number == page and not empty:
            links.append(
                f'<a class="active" href="javascript:void(0)">{number}</a>'
            )
        else:
            links.append(
                '<a href="?menuId=002009000000&'
                f'page={number}&category=&searchMode=&searchTxt=">{number}</a>'
            )
    if not empty and page < last:
        links.append(
            '<a class="arr next" href="?menuId=002009000000&'
            f'page={page + 1}&category=&searchMode=&searchTxt=">다음</a>'
        )
    return f'<div class="board_pager">{"".join(links)}</div>'


def _list_page(
    category: str,
    page: int,
    rows: list[Mapping[str, Any]],
    last: int,
) -> str:
    heading = sunchang.SUNCHANG_CATEGORY_PAGE_TITLES[category]
    cards = "".join(_list_row(row, page) for row in rows)
    program = (
        f'<div class="program_list"><ul>{cards}</ul></div>'
        if rows
        else '<div class="program_list">자료가 없습니다</div>'
    )
    filters = "".join(
        f"<li><a><span>{value}</span></a></li>"
        for value in sunchang.SUNCHANG_STATUS_FILTERS
    )
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
      <title>순창평생학습관</title></head><body>
        <div class="subTitle"><p class="titSubject">{heading}</p></div>
        <div class="snb"><div class="category"><ul>
          <li><a>교육프로그램신청</a></li>
          <li><a>교육안내</a></li>
          <li><a>시설예약</a></li>
        </ul></div></div>
        <div class="inner">
          <form name="schForm">
            <input name="menuId" value="002009000000">
            <input name="page" value="{page}">
            <select name="searchMode">
              <option value="subject">교육명</option>
              <option value="tmpCol12">강사명</option>
            </select>
            <input name="searchTxt" value="">
          </form>
          <ul class="cate">{filters}</ul>
          {program}
          {_pager(page, last, empty=not rows)}
        </div>
      </body></html>
    """


def _pages(courses: list[Mapping[str, Any]]) -> dict[tuple[str, int], str]:
    output: dict[tuple[str, int], str] = {}
    for category in ("", *sunchang.SUNCHANG_CATEGORIES):
        selected = [
            row for row in courses if not category or row["category"] == category
        ]
        selected.sort(key=lambda row: int(str(row["identity"])), reverse=True)
        chunks = [
            selected[offset : offset + sunchang.SUNCHANG_PAGE_SIZE]
            for offset in range(0, len(selected), sunchang.SUNCHANG_PAGE_SIZE)
        ]
        last = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            output[(category, index)] = _list_page(category, index, chunk, last)
        sentinel = last + 1
        output[(category, sentinel)] = _list_page(category, sentinel, [], last)
    return output


def _detail(course: Mapping[str, Any], *, control: bool = True) -> str:
    category_label = sunchang.SUNCHANG_DETAIL_CATEGORY_LABELS[
        str(course["category"])
    ]
    button = '<a href="#none" onclick="fn_apply();">신청하기</a>' if control else ""
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
      <title>순창평생학습관</title></head><body>
        <div class="board_view"><div class="basic"><div class="item">
          <div class="title"><div class="state">
            <span class="type" data-label="{course['status']}">{course['status']}</span>
            <span class="type cate">{category_label}</span>
          </div><strong>{html.escape(str(course['title']))}</strong></div>
          <ul>
            <li><strong>운영기간</strong><span>{course['period']}</span></li>
            <li><strong>운영시간</strong><span>{course['schedule']}</span></li>
            <li><strong>교육장소</strong><span>{html.escape(str(course['venue']))}</span></li>
            <li><strong>교육대상</strong><span>{category_label}</span></li>
            <li><strong>모집기간</strong><span>{course['apply_period']}</span></li>
            <li><strong>예약인원</strong><span>신청 {course['applied']}명 / 정원 {course['capacity']}명</span></li>
            <li><strong>대기인원</strong><span>대기 1명 / 대기정원 5명</span></li>
          </ul>
          <div class="board_btns">{button}</div>
        </div></div></div>
        <form id="frm"><input name="articleSeq" value="{course['identity']}"></form>
        <script>
          function fn_apply() {{
            var articleSeq = "{course['identity']}";
            $.ajax({{
              url: "/api/scedu/reserve/eduApply",
              type: "POST",
              data: {{ articleSeq: articleSeq }}
            }});
          }}
        </script>
      </body></html>
    """


def _complete_fixture(
    *, expired: bool = False
) -> tuple[
    dict[tuple[str, int], str | list[str]],
    dict[str, str | list[str]],
    list[dict[str, Any]],
]:
    if expired:
        values = [
            _course(
                "4",
                "300",
                status="교육마감",
                apply_period="2026-01-01 ~ 2026-01-20",
                period="2026-02-01 ~ 2026-06-30",
            ),
            _course(
                "3",
                "299",
                status="교육마감",
                apply_period="2026-01-01 ~ 2026-01-20",
                period="2026-02-01 ~ 2026-06-30",
            ),
        ]
        return _pages(values), {}, values
    values = [
        _course("4", "300", applied=4),
        _course("3", "299", applied=2),
        _course("4", "298", applied=1),
    ]
    return _pages(values), {row["identity"]: _detail(row) for row in values}, values


def _collect_fixture(
    pages: Mapping[tuple[str, int], str | list[str]],
    details: Mapping[str, str | list[str]],
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], FixtureGetter, FakeSession]:
    getter = FixtureGetter(pages, details)
    session = FakeSession()
    rows, parser, meta = sunchang.collect(
        _target(),
        today="2026-07-23",
        timeout=5,
        max_pages=10,
        detail_limit=10,
        session_factory=lambda: session,
        getter=getter,
        **kwargs,
    )
    return rows, parser, meta, getter, session


def test_candidate_hashes_owner_decision_and_boundaries_are_exact() -> None:
    bad_normalized = sunchang.SUNCHANG_BAD_CANDIDATE_URL
    canonical_normalized = (
        "https://www.scedulife.co.kr/scedu/board/list?"
        "category=&menuId=002009000000"
    )
    assert hashlib.sha1(bad_normalized.encode()).hexdigest() == (
        sunchang.SUNCHANG_BAD_NORMALIZED_SHA1
    )
    assert hashlib.sha256(bad_normalized.encode()).hexdigest() == (
        sunchang.SUNCHANG_BAD_NORMALIZED_SHA256
    )
    assert hashlib.sha1(canonical_normalized.encode()).hexdigest() == (
        sunchang.SUNCHANG_CANONICAL_NORMALIZED_SHA1
    )
    assert hashlib.sha256(canonical_normalized.encode()).hexdigest() == (
        sunchang.SUNCHANG_CANONICAL_NORMALIZED_SHA256
    )
    assert sunchang.SUNCHANG_CANDIDATE_AUDIT[
        sunchang.SUNCHANG_BAD_CANDIDATE_ID
    ]["decision"] == "reject_wrong_mayor_site"
    assert sunchang.SUNCHANG_CANDIDATE_AUDIT[
        sunchang.SUNCHANG_CANONICAL_CANDIDATE_ID
    ]["provider"] == sunchang.SUNCHANG_PROVIDER
    assert sunchang.SUNCHANG_RECOMMENDED_TARGET["max_pages"] == 30
    boundaries = {
        value["provider"] for value in sunchang.SUNCHANG_SEPARATE_OWNER_BOUNDARIES
    }
    assert "CULTURE_CULTURE_FOUNDATION_3A09706846" in boundaries
    assert "CULTURE_PUBLIC_LIBRARY_1B6144A9DB" in boundaries
    assert "CULTURE_PUBLIC_LIBRARY_8700CFFC50" in boundaries


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "MUNI_WRONG"},
        {"url": sunchang.SUNCHANG_BAD_CANDIDATE_URL},
        {
            "url": "https://scedulife.co.kr/scedu/board/list?"
            "menuId=002009000000&page=1&category="
        },
        {
            "url": "https://www.scedulife.co.kr/scedu/board/list?"
            "menuId=002009000000&page=2&category="
        },
        {
            "url": sunchang.SUNCHANG_CANONICAL_URL + "#fragment",
        },
        {
            "url": "https://user:secret@www.scedulife.co.kr/scedu/board/list?"
            "menuId=002009000000&page=1&category="
        },
    ],
)
def test_target_binding_rejects_wrong_owner_or_url(changes: dict[str, str]) -> None:
    assert not sunchang.is_target(_target(**changes))


def test_wrong_target_fails_before_allocating_session() -> None:
    allocated = False

    def factory() -> FakeSession:
        nonlocal allocated
        allocated = True
        return FakeSession()

    rows, parser, meta = sunchang.collect(
        _target(url=sunchang.SUNCHANG_BAD_CANDIDATE_URL),
        session_factory=factory,
    )
    assert rows == []
    assert parser == sunchang.SUNCHANG_PARSER
    assert "exact new Sunchang" in meta["configured_collection_error"]
    assert not allocated


def test_complete_current_snapshot_reconciles_categories_and_details() -> None:
    pages, details, _courses = _complete_fixture()
    rows, parser, meta, getter, session = _collect_fixture(pages, details)
    assert parser == sunchang.SUNCHANG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == 3
    assert meta["current_source_count"] == 3
    assert meta["returned_count"] == 3
    assert meta["canonical_pages"] == 1
    assert meta["empty_sentinel_page"] == 2
    assert meta["category_pages"] == {"1": 0, "2": 0, "3": 1, "4": 1}
    assert meta["category_sentinel_pages"] == {
        "1": 1,
        "2": 1,
        "3": 2,
        "4": 2,
    }
    assert meta["source_category_counts"] == {"4": 2, "3": 1}
    assert meta["category_reconciliation_counts"] == {
        "1": 0,
        "2": 0,
        "3": 1,
        "4": 2,
    }
    assert meta["detail_pages"] == 3
    assert meta["boundary_rechecks"] == 2
    assert meta["current_list_pages_rechecked"] == [1, 2]
    assert meta["sentinel_rechecked"]
    assert meta["pagination_complete"]
    assert meta["category_reconciliation_complete"]
    assert meta["details_complete"]
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]
    assert not meta["no_current_data"]
    assert {row["branch"] for row in rows} == {"순창평생학습관"}
    assert {row["status"] for row in rows} == {"OPEN"}
    assert all(row["reservation_available"] for row in rows)
    assert all(row["application_url"] == row["raw_url"] for row in rows)
    assert all(row["raw_fields"]["detail_verified"] for row in rows)
    assert all(
        row["raw_fields"]["application_contract_verified"] for row in rows
    )
    assert all(not row["raw_fields"]["application_endpoint_fetched"] for row in rows)
    assert len({row["provider_course_id"] for row in rows}) == 3
    assert not any(
        urlparse(url).path == sunchang.SUNCHANG_APPLICATION_WRITE_PATH
        for url in getter.calls
    )
    assert not any(urlparse(url).path in {"/scedu/login", "/scedu/join/step1"} for url in getter.calls)
    assert session.closed


def test_expired_ledger_publishes_complete_no_current_data_snapshot() -> None:
    pages, details, _courses = _complete_fixture(expired=True)
    rows, _parser, meta, getter, session = _collect_fixture(pages, details)
    assert rows == []
    assert meta["configured_collection_error"] == ""
    assert meta["source_rows"] == 2
    assert meta["expired_source_count"] == 2
    assert meta["current_source_count"] == 0
    assert meta["detail_pages"] == 0
    assert meta["source_status_counts"] == {"교육마감": 2}
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]
    assert meta["no_current_data"]
    assert all(urlparse(url).path == sunchang.SUNCHANG_LIST_PATH for url in getter.calls)
    assert session.closed


def test_immediate_sentinel_must_have_exact_empty_marker() -> None:
    pages, details, _courses = _complete_fixture(expired=True)
    pages[("", 2)] = str(pages[("", 2)]).replace(
        "자료가 없습니다", "등록된 자료가 없습니다"
    )
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "missing exact empty sentinel" in meta["configured_collection_error"]
    assert not meta["pagination_complete"]


def test_max_pages_must_include_immediate_empty_sentinel() -> None:
    courses = [
        _course(
            "4",
            str(500 - index),
            status="교육마감",
            apply_period="2025-01-01 ~ 2025-01-10",
            period="2025-02-01 ~ 2025-06-30",
        )
        for index in range(13)
    ]
    pages = _pages(courses)
    getter = FixtureGetter(pages, {})
    session = FakeSession()
    rows, _parser, meta = sunchang.collect(
        _target(),
        today="2026-07-23",
        max_pages=2,
        detail_limit=10,
        session_factory=lambda: session,
        getter=getter,
    )
    assert rows == []
    assert "max_pages reached before empty sentinel" in meta[
        "configured_collection_error"
    ]
    assert not meta["pagination_complete"]
    assert session.closed


def test_category_union_mismatch_fails_atomically() -> None:
    pages, details, _courses = _complete_fixture()
    pages[("4", 1)] = str(pages[("4", 1)]).replace("강좌 300", "분류 변조", 1)
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "category/canonical row mismatch" in meta["configured_collection_error"]
    assert not meta["category_reconciliation_complete"]
    assert meta["detail_pages"] == 0


def test_duplicate_identity_on_one_page_fails_before_details() -> None:
    duplicate = _course("4", "300", title="중복 강좌")
    courses = [_course("4", "300"), duplicate]
    pages = _pages(courses)
    rows, _parser, meta, getter, _session = _collect_fixture(pages, {})
    assert rows == []
    assert "duplicate identity" in meta["configured_collection_error"]
    assert all(urlparse(url).path == sunchang.SUNCHANG_LIST_PATH for url in getter.calls)


def test_detail_title_or_application_identity_drift_fails_atomically() -> None:
    pages, details, courses = _complete_fixture()
    details[courses[0]["identity"]] = str(details[courses[0]["identity"]]).replace(
        'var articleSeq = "300";', 'var articleSeq = "999";'
    )
    rows, _parser, meta, getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "application script contract changed" in meta[
        "configured_collection_error"
    ]
    assert not meta["snapshot_complete"]
    assert not any(
        urlparse(url).path == sunchang.SUNCHANG_APPLICATION_WRITE_PATH
        for url in getter.calls
    )


def test_non_open_application_button_is_rejected() -> None:
    course = _course(
        "4",
        "300",
        status="접수마감",
        apply_period="2026-06-01 ~ 2026-06-30",
        period="2026-08-01 ~ 2026-11-30",
    )
    pages = _pages([course])
    details = {"300": _detail(course, control=True)}
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "application control on non-open state" in meta[
        "configured_collection_error"
    ]


def test_detail_limit_and_argument_caps_fail_closed() -> None:
    pages, details, _courses = _complete_fixture()
    getter = FixtureGetter(pages, details)
    session = FakeSession()
    rows, _parser, meta = sunchang.collect(
        _target(),
        today="2026-07-23",
        detail_limit=2,
        max_pages=10,
        session_factory=lambda: session,
        getter=getter,
    )
    assert rows == []
    assert "detail_limit 2 below required 3" in meta[
        "configured_collection_error"
    ]
    assert meta["source_cap_reached"]
    assert meta["detail_pages"] == 0
    assert session.closed

    allocated = False

    def factory() -> FakeSession:
        nonlocal allocated
        allocated = True
        return FakeSession()

    rows, _parser, meta = sunchang.collect(
        _target(), max_pages=0, session_factory=factory
    )
    assert rows == []
    assert "caps are invalid" in meta["configured_collection_error"]
    assert not allocated


def test_current_page_mutation_after_details_fails_stability_recheck() -> None:
    pages, details, _courses = _complete_fixture()
    original = str(pages[("", 1)])
    changed = original.replace("강좌 300", "조회 중 변경된 강좌", 1)
    pages[("", 1)] = [original, changed]
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "stability recheck failed" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 3
    assert not meta["snapshot_complete"]


def test_custom_dedupe_cannot_drop_a_current_identity() -> None:
    pages, details, _courses = _complete_fixture()
    rows, _parser, meta, _getter, _session = _collect_fixture(
        pages,
        details,
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


def test_public_phone_or_email_is_not_allowed_to_leak() -> None:
    course = _course("4", "300", title="문의 063-650-1239")
    pages = _pages([course])
    details = {"300": _detail(course)}
    rows, _parser, meta, _getter, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "public row contains a phone number" in meta[
        "configured_collection_error"
    ]


@pytest.mark.skipif(
    os.environ.get("RUN_SUNCHANG_LIVE") != "1",
    reason="set RUN_SUNCHANG_LIVE=1 for the audited two-run official-source census",
)
def test_live_exact_snapshot_2026_07_23_is_stable_across_two_runs() -> None:
    results = []
    for _ in range(2):
        rows, parser, meta = sunchang.collect(
            _target(),
            today="2026-07-23",
            timeout=30,
            max_pages=sunchang.SUNCHANG_RECOMMENDED_MAX_PAGES,
            detail_limit=sunchang.SUNCHANG_RECOMMENDED_DETAIL_LIMIT,
        )
        assert rows == []
        assert parser == sunchang.SUNCHANG_PARSER
        assert meta["configured_collection_error"] == ""
        assert meta["source_rows"] == 131
        assert meta["current_source_count"] == 0
        assert meta["returned_count"] == 0
        assert meta["canonical_pages"] == 11
        assert meta["empty_sentinel_page"] == 12
        assert meta["category_pages"] == {"1": 0, "2": 0, "3": 4, "4": 7}
        assert meta["category_sentinel_pages"] == {
            "1": 1,
            "2": 1,
            "3": 5,
            "4": 8,
        }
        assert meta["source_category_counts"] == {"4": 84, "3": 47}
        assert meta["category_reconciliation_counts"] == {
            "1": 0,
            "2": 0,
            "3": 47,
            "4": 84,
        }
        assert meta["source_status_counts"] == {"교육마감": 131}
        assert meta["first_identity"] == "1017"
        assert meta["last_identity"] == "415"
        assert meta["snapshot_sha256"] == (
            "dc8a94e3b9eea0b9c5ec987c1e2cd2bd19f285d6758ca9ec5b7b8f6b889408a6"
        )
        assert meta["source_requests"] == 30
        assert meta["list_requests"] == 30
        assert meta["detail_pages"] == 0
        assert meta["boundary_rechecks"] == 3
        assert meta["current_list_pages_rechecked"] == [1, 11, 12]
        assert meta["application_endpoint_calls"] == 0
        assert meta["login_page_calls"] == 0
        assert meta["join_page_calls"] == 0
        assert meta["pii_form_calls"] == 0
        assert meta["pagination_complete"]
        assert meta["category_reconciliation_complete"]
        assert meta["details_complete"]
        assert meta["sentinel_rechecked"]
        assert meta["snapshot_complete"]
        assert meta["full_snapshot_validated"]
        assert meta["no_current_data"]
        results.append(meta["snapshot_sha256"])
    assert results[0] == results[1]
