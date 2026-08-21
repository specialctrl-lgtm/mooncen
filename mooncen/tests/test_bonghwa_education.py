from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from threading import Lock
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_bonghwa as bonghwa


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(self, text: str, url: str, status_code: int = 200) -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.url = url
        self.status_code = status_code


class FixtureTransport:
    def __init__(
        self,
        pages: Mapping[tuple[str, int], Mapping[str, Any] | list[Mapping[str, Any]]],
        details: Mapping[str, str | list[str]],
        *,
        landing: str | FakeResponse | None = None,
    ) -> None:
        self.pages = dict(pages)
        self.details = dict(details)
        self.landing = landing or _landing()
        self.offsets: Counter[tuple[str, str | int]] = Counter()
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []
        self.lock = Lock()

    @staticmethod
    def _value(value: Any, offset: int) -> Any:
        if isinstance(value, list):
            return value[min(offset, len(value) - 1)]
        return value

    def __call__(
        self,
        _session: Any,
        method: str,
        url: str,
        _timeout: int,
        data: Mapping[str, str] | None,
    ) -> Any:
        parsed = urlparse(url)
        with self.lock:
            self.calls.append((method, url, dict(data) if data is not None else None))
        assert parsed.scheme == "https"
        assert parsed.hostname == bonghwa.BONGHWA_HOST
        assert "/apply/" not in parsed.path
        assert "/file/" not in parsed.path
        assert "/login/" not in parsed.path
        if method == "GET" and parsed.path == bonghwa.BONGHWA_LIST_PATH:
            assert parse_qs(parsed.query) == {"mid": [bonghwa.BONGHWA_MID]}
            return self.landing
        if method == "GET" and parsed.path == bonghwa.BONGHWA_DETAIL_PATH:
            query = parse_qs(parsed.query)
            assert query["mid"] == [bonghwa.BONGHWA_MID]
            identity = query["programAppIdx"][0]
            if identity not in self.details:
                raise AssertionError(f"unexpected detail identity {identity}")
            key = ("detail", identity)
            with self.lock:
                offset = self.offsets[key]
                self.offsets[key] += 1
            return self._value(self.details[identity], offset)
        if method == "POST" and parsed.path == bonghwa.BONGHWA_AJAX_PATH:
            assert not parsed.query
            assert data is not None
            partition, page = data["searchAppSortState"], int(data["page"])
            key = (partition, page)
            if key not in self.pages:
                raise AssertionError(f"unexpected JSON page {key}")
            counter_key = (partition, page)
            with self.lock:
                offset = self.offsets[counter_key]
                self.offsets[counter_key] += 1
            return deepcopy(self._value(self.pages[key], offset))
        raise AssertionError(f"unexpected endpoint {method} {url}")


def _target(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "provider": bonghwa.BONGHWA_PROVIDER,
        "url": bonghwa.BONGHWA_CANONICAL_URL,
    }
    value.update(changes)
    return value


def _landing(*, title: str = "평생학습강좌 | 수강신청 | 홈페이지") -> str:
    fields = [("", "", "전체")] + [
        (code, "", name) for code, name in bonghwa._FIELD_NAMES.items()
    ]
    field_links = "".join(
        f'<a data-search="field" data-field="{code}" '
        f'data-field-detail="{detail}">{name}</a>'
        for code, detail, name in fields
    )
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
      <title>{title}</title>
      <script src="/reservation/edu/js/unit/academy/program/list.js"></script>
      </head><body>
        <div id="titWrap"><h3>평생학습강좌</h3></div>
        <div class="sorts">
          <a data-sort="wait">모집예정</a>
          <a data-sort="ing">모집중</a>
          <a data-sort="end">마감</a>
        </div>
        <div class="fields">{field_links}</div>
        <div class="enrolment-list"><ul></ul></div>
      </body></html>
    """


def _course(
    identity: int,
    partition: str,
    *,
    title: str | None = None,
    material_cost: str = "5,000원",
    event_start: str = "2026-08-01",
    event_end: str = "2026-09-30",
    source_status: str | None = None,
) -> dict[str, Any]:
    status = source_status or {
        "wait": "모집예정",
        "ing": "모집중",
        "end": "모집마감",
    }[partition]
    return {
        "programAppIdx": identity,
        "appStateValue": status,
        "eduSdate": event_start,
        "eduTuition": "10000",
        "appTypeValue": "추첨" if partition in {"wait", "ing"} else "선착순",
        "eduCost": material_cost,
        "appliedOnNum": 7,
        "eduDayValue": "월",
        "appSdate": "2026-07-01",
        "eduFieldDetailName": "생활건강",
        "appliedOffNum": 2,
        "appOnNum": 10,
        "appliedWaitNum": 3,
        "eduEtime": "12:00",
        "isFree": "N",
        "eduEdate": event_end,
        "appEdate": "2026-07-31",
        "appWaitNum": 5,
        "eduFieldName": "인문교양",
        "eduField": "1",
        "eduTitle": title or f"봉화 강좌 {identity}",
        "eduStime": "10:00",
        "appOffNum": 4,
    }


def _json_page(
    partition: str,
    page: int,
    declared: int,
    rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    advertised_last = max(1, (declared + bonghwa.BONGHWA_PAGE_SIZE - 1) // bonghwa.BONGHWA_PAGE_SIZE)
    first_on_list = ((page - 1) // bonghwa.BONGHWA_PAGE_SIZE) * bonghwa.BONGHWA_PAGE_SIZE + 1
    pagination = {
        "currentPageNo": page,
        "recordCountPerPage": bonghwa.BONGHWA_PAGE_SIZE,
        "pageSize": bonghwa.BONGHWA_PAGE_SIZE,
        "totalRecordCount": declared,
        "totalPageCount": advertised_last,
        "firstPageNoOnPageList": first_on_list,
        "lastPageNoOnPageList": min(first_on_list + 9, advertised_last),
        "firstRecordIndex": (page - 1) * bonghwa.BONGHWA_PAGE_SIZE,
        "lastRecordIndex": page * bonghwa.BONGHWA_PAGE_SIZE,
    }
    return {
        "listOrder": declared - (page - 1) * bonghwa.BONGHWA_PAGE_SIZE,
        "programListCnt": declared,
        "paginationInfo": json.dumps(pagination),
        "programList": [dict(row) for row in rows],
        "page": page,
    }


def _partition_pages(
    partition: str,
    courses: list[Mapping[str, Any]],
    *,
    declared: int | None = None,
) -> dict[tuple[str, int], dict[str, Any]]:
    actual_declared = (
        len(courses) + (1 if partition == "end" else 0)
        if declared is None
        else declared
    )
    advertised_last = max(
        1,
        (actual_declared + bonghwa.BONGHWA_PAGE_SIZE - 1)
        // bonghwa.BONGHWA_PAGE_SIZE,
    )
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for page in range(1, advertised_last + 1):
        start = (page - 1) * bonghwa.BONGHWA_PAGE_SIZE
        chunk = courses[start : start + bonghwa.BONGHWA_PAGE_SIZE]
        result[(partition, page)] = _json_page(
            partition, page, actual_declared, chunk
        )
    result[(partition, advertised_last + 1)] = _json_page(
        partition, advertised_last + 1, actual_declared, []
    )
    return result


def _pair(label: str, value: str) -> str:
    return f"<tr><th>{label}</th><td>{value}</td></tr>"


def _detail(
    course: Mapping[str, Any],
    partition: str,
    *,
    title: str | None = None,
    category: str = "인문교양 | 생활건강",
    target: str = "봉화군민",
    room: str = "봉화군 평생학습관 1층 강의실",
    material_cost: str | None = None,
    application_action: str = bonghwa.BONGHWA_APPLICATION_PATH,
    application_method: str = "get",
    hidden: Mapping[str, str] | None = None,
    control: bool | None = None,
    control_href: str = "javascript:void(0);",
    control_onclick: str = "document.getElementById('apply').submit();",
    include_draw_date: bool = True,
) -> str:
    identity = str(course["programAppIdx"])
    values = dict(
        hidden
        or {
            "searchStage": "17",
            "searchProgram": "11",
            "searchAppProgram": identity,
            "mid": bonghwa.BONGHWA_MID,
        }
    )
    hidden_html = "".join(
        f'<input type="hidden" name="{name}" value="{value}">'
        for name, value in values.items()
    )
    show_control = partition == "ing" if control is None else control
    control_html = (
        f'<div class="btn-wrap"><a href="{control_href}" '
        f'onclick="{control_onclick}">신청하기</a></div>'
        if show_control
        else '<div class="btn-wrap"></div>'
    )
    fields = (
        _pair("학습분야", category)
        + _pair("모집대상", target)
        + _pair("모집기간", f"{course['appSdate']} 09:00 ~ {course['appEdate']} 18:00")
        + (_pair("추첨예정일", "2026-08-01") if include_draw_date else "")
        + _pair("교육기간", f"{course['eduSdate']} ~ {course['eduEdate']}")
        + _pair("교육시간", f"{course['eduDayValue']} ({course['eduStime']}~{course['eduEtime']})")
        + _pair("강사", "홍길동 010-1111-2222")
        + _pair("재료비", material_cost if material_cost is not None else str(course["eduCost"]))
        + _pair("방문접수처 (오프라인신청)", "담당자 010-3333-4444")
        + _pair("교육장소", room)
        + _pair("강의내용", "수강생 이메일 student@example.com 및 긴 자유문구")
        + _pair("관련 이미지", "강좌 사진")
        + _pair("첨부파일", "강의계획서.hwp")
    )
    display_title = title if title is not None else str(course["eduTitle"])
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
      <title>평생학습강좌 | 수강신청 | 홈페이지</title></head><body>
        <div class="veiw-wrap">
          <div class="enrolment-tit">{display_title}
            <div class="organName"><span>{category}</span></div>
            <div class="statusWrap"><p>{course['appTypeValue']}</p><p>{course['appStateValue']}</p></div>
          </div>
          <table class="tbl"><tbody>{fields}</tbody></table>
          <form id="apply" method="{application_method}" action="{application_action}">
            {hidden_html}
          </form>
          {control_html}
        </div>
      </body></html>
    """


def _fixture(
    *,
    wait: list[Mapping[str, Any]] | None = None,
    ing: list[Mapping[str, Any]] | None = None,
    end: list[Mapping[str, Any]] | None = None,
) -> tuple[dict[tuple[str, int], dict[str, Any]], dict[str, str]]:
    wait_rows = list(wait or [])
    ing_rows = list(ing or [_course(101, "ing"), _course(102, "ing")])
    end_rows = list(end or [_course(201, "end"), _course(202, "end"), _course(203, "end")])
    pages: dict[tuple[str, int], dict[str, Any]] = {}
    pages.update(_partition_pages("wait", wait_rows, declared=len(wait_rows)))
    pages.update(_partition_pages("ing", ing_rows, declared=len(ing_rows)))
    pages.update(_partition_pages("end", end_rows, declared=len(end_rows) + 1))
    details = {
        str(row["programAppIdx"]): _detail(row, partition)
        for partition, rows in (("wait", wait_rows), ("ing", ing_rows))
        for row in rows
    }
    return pages, details


def _run(
    *,
    pages: Mapping[tuple[str, int], Any] | None = None,
    details: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    landing: str | FakeResponse | None = None,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], FixtureTransport, list[FakeSession]]:
    fixture_pages, fixture_details = _fixture()
    transport = FixtureTransport(
        pages or fixture_pages,
        details or fixture_details,
        landing=landing,
    )
    sessions: list[FakeSession] = []

    def session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    rows, parser, meta = bonghwa.collect_bonghwa_education(
        target or _target(),
        today="2026-07-23",
        session_factory=session_factory,
        transport=transport,
        max_workers=3,
        **kwargs,
    )
    return rows, parser, meta, transport, sessions


def _assert_failed(meta: Mapping[str, Any], phrase: str) -> None:
    assert phrase in str(meta["configured_collection_error"])
    assert not meta["full_snapshot_validated"]


def test_owner_constants_candidate_decisions_and_hashes_are_fixed() -> None:
    assert bonghwa.BONGHWA_PROVIDER == "MUNI_WWW_BONGHWA_GO_KR_A33FDB5A"
    assert bonghwa.BONGHWA_DUPLICATE_PROVIDER == "MUNI_WWW_BONGHWA_GO_KR_C3F54364"
    assert bonghwa.BONGHWA_MUNICIPALITY_CODE == "4792000000"
    assert bonghwa.BONGHWA_BRANCH == "봉화군 평생학습관"
    assert bonghwa.BONGHWA_BRANCH_ADDRESS == "경상북도 봉화군 봉화읍 내성로 5길 13"
    assert hashlib.sha1(bonghwa.BONGHWA_CANONICAL_URL.encode()).hexdigest()[:8].upper() == "1423AF64"
    assert hashlib.sha256(bonghwa.BONGHWA_CANONICAL_URL.encode()).hexdigest()[:12].upper() == "752857C970F3"
    assert bonghwa.BONGHWA_NEW_URL_HASH_PROVIDER_NOT_TO_CREATE.endswith("1423AF64")
    canonical = bonghwa.BONGHWA_CANDIDATE_AUDIT[bonghwa.BONGHWA_CANONICAL_CANDIDATE_ID]
    portal = bonghwa.BONGHWA_CANDIDATE_AUDIT[bonghwa.BONGHWA_PORTAL_CANDIDATE_ID]
    stale = bonghwa.BONGHWA_CANDIDATE_AUDIT[bonghwa.BONGHWA_STALE_CANDIDATE_ID]
    assert canonical["owner"] == bonghwa.BONGHWA_PROVIDER
    assert canonical["new_provider_created"] is False
    assert portal["owner"] == bonghwa.BONGHWA_PROVIDER
    assert "reject_stale" in stale["decision"]


@pytest.mark.parametrize(
    "target",
    [
        {"provider": bonghwa.BONGHWA_DUPLICATE_PROVIDER, "url": bonghwa.BONGHWA_CANONICAL_URL},
        {"provider": bonghwa.BONGHWA_NEW_URL_HASH_PROVIDER_NOT_TO_CREATE, "url": bonghwa.BONGHWA_CANONICAL_URL},
        {"provider": bonghwa.BONGHWA_PROVIDER, "url": bonghwa.BONGHWA_OLD_URL},
        {"provider": bonghwa.BONGHWA_PROVIDER, "url": bonghwa.BONGHWA_PORTAL_CANDIDATE_URL},
        {"provider": bonghwa.BONGHWA_PROVIDER, "url": bonghwa.BONGHWA_CANONICAL_URL + "&page=1"},
        {"provider": bonghwa.BONGHWA_PROVIDER, "url": bonghwa.BONGHWA_CANONICAL_URL + "#x"},
    ],
)
def test_target_matcher_rejects_aliases_duplicates_and_query_drift(target: Mapping[str, str]) -> None:
    assert not bonghwa.is_bonghwa_education_target(target)
    rows, _, meta = bonghwa.collect_bonghwa_education(target)
    assert rows == []
    _assert_failed(meta, "target does not match")


def test_target_matcher_accepts_only_incumbent_on_canonical_url() -> None:
    assert bonghwa.is_bonghwa_education_target(_target())
    rows, _, meta = bonghwa.collect_bonghwa_education(_target())
    assert rows == []
    _assert_failed(meta, "managed session_factory injection is required")


def test_xhr_header_is_scoped_to_json_post_not_full_page_gets() -> None:
    session = bonghwa._default_session_factory()
    try:
        assert "X-Requested-With" not in session.headers
    finally:
        session.close()

    class SpySession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def get(self, _url: str, **kwargs: Any) -> object:
            self.calls.append(("GET", kwargs))
            return object()

        def post(self, _url: str, **kwargs: Any) -> object:
            self.calls.append(("POST", kwargs))
            return object()

    spy = SpySession()
    bonghwa._request(spy, "GET", bonghwa.BONGHWA_CANONICAL_URL, 30, None)
    bonghwa._request(spy, "POST", bonghwa.BONGHWA_AJAX_URL, 30, bonghwa._post_data("ing", 1))
    assert "headers" not in spy.calls[0][1]
    assert spy.calls[1][1]["headers"] == {"X-Requested-With": "XMLHttpRequest"}


def test_complete_snapshot_is_owner_complete_stable_and_privacy_safe() -> None:
    rows, parser, meta, transport, sessions = _run()
    assert parser == bonghwa.BONGHWA_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["full_snapshot_validated"] is True
    assert meta["partition_declared_counts"] == {"wait": 0, "ing": 2, "end": 4}
    assert meta["partition_returned_counts"] == {"wait": 0, "ing": 2, "end": 3}
    assert meta["partition_declared_deficits"] == {"wait": 0, "ing": 0, "end": 1}
    assert meta["partition_data_pages"] == {"wait": 0, "ing": 1, "end": 1}
    assert meta["partition_page_counts"] == {"wait": [], "ing": [2], "end": [3]}
    assert meta["partition_sentinel_pages"] == {"wait": 2, "ing": 2, "end": 2}
    assert meta["partition_union_count"] == meta["source_rows"] == 5
    assert meta["partition_overlap_count"] == 0
    assert meta["current_source_count"] == meta["detail_pages"] == len(rows) == 2
    assert meta["historical_source_count"] == 3
    assert meta["logical_requests"] == 15
    assert meta["post_requests"] == 12
    assert meta["get_requests"] == 3
    assert meta["physical_attempts"] == meta["logical_requests"]
    assert meta["application_control_count"] == 2
    assert meta["application_endpoints_called"] == 0
    assert meta["applicant_endpoints_called"] == 0
    assert meta["attachment_endpoints_called"] == 0
    assert meta["privacy_violations"] == 0
    assert all(s.closed for s in sessions)
    assert len(sessions) == 3
    assert {row["provider"] for row in rows} == {bonghwa.BONGHWA_PROVIDER}
    assert {row["branch"] for row in rows} == {bonghwa.BONGHWA_BRANCH}
    assert {row["address"] for row in rows} == {bonghwa.BONGHWA_BRANCH_ADDRESS}
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["reservation_available"] for row in rows)
    assert all(urlparse(row["application_url"]).path == bonghwa.BONGHWA_APPLICATION_PATH for row in rows)
    payload = repr(rows)
    for discarded in ("홍길동", "010-", "student@example.com", "강의계획서.hwp", "긴 자유문구"):
        assert discarded not in payload
    assert all(bonghwa.BONGHWA_APPLICATION_PATH not in url for _, url, _ in transport.calls)


def test_full_capacity_status_is_confined_to_closed_partition() -> None:
    end = [
        _course(201, "end", source_status="정원마감"),
        _course(202, "end"),
        _course(203, "end"),
    ]
    pages, details = _fixture(end=end)
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert rows
    assert meta["source_status_counts"]["정원마감"] == 1

    ing = [_course(101, "ing", source_status="정원마감")]
    pages, details = _fixture(ing=ing)
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert rows == []
    _assert_failed(meta, "source status escaped partition")


def test_draw_date_is_optional_only_for_first_come_courses() -> None:
    first_come = _course(101, "ing")
    first_come["appTypeValue"] = "선착순"
    pages, details = _fixture(ing=[first_come])
    details["101"] = _detail(first_come, "ing", include_draw_date=False)
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert rows and meta["configured_collection_error"] == ""

    draw = _course(101, "ing")
    pages, details = _fixture(ing=[draw])
    details["101"] = _detail(draw, "ing", include_draw_date=False)
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert rows == []
    _assert_failed(meta, "draw schedule is missing")


def test_every_json_call_uses_exact_unfiltered_post_contract() -> None:
    rows, _, meta, transport, _ = _run()
    assert rows and not meta["configured_collection_error"]
    posts = [call for call in transport.calls if call[0] == "POST"]
    assert posts
    for _, url, data in posts:
        assert url == bonghwa.BONGHWA_AJAX_URL
        assert data is not None
        assert tuple(data) == bonghwa._POST_KEYS
        assert data["mid"] == bonghwa.BONGHWA_MID
        assert data["searchAppSortState"] in {"wait", "ing", "end"}
        for name in ("searchTxt", "searchField", "searchFieldDetail", "searchAppType", "searchEduTime"):
            assert data[name] == ""


def test_multiple_pages_and_boundaries_are_completely_read_and_rechecked() -> None:
    ing = [_course(1000 + offset, "ing") for offset in range(12)]
    end = [_course(2000 + offset, "end") for offset in range(11)]
    pages, details = _fixture(ing=ing, end=end)
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert meta["configured_collection_error"] == ""
    assert len(rows) == 12
    assert meta["partition_declared_counts"] == {"wait": 0, "ing": 12, "end": 12}
    assert meta["partition_returned_counts"] == {"wait": 0, "ing": 12, "end": 11}
    assert meta["partition_page_counts"] == {"wait": [], "ing": [10, 2], "end": [10, 1]}
    assert meta["partition_sentinel_pages"] == {"wait": 2, "ing": 3, "end": 3}
    assert meta["partition_union_count"] == 23
    assert meta["logical_requests"] == 29
    assert meta["post_requests"] == 16
    assert meta["get_requests"] == 13
    assert all(meta["partition_first_rechecked"].values())
    assert all(meta["partition_last_rechecked"].values())
    assert all(meta["partition_sentinel_rechecked"].values())


def test_empty_current_partitions_are_a_complete_no_current_snapshot() -> None:
    end = [_course(201, "end"), _course(202, "end"), _course(203, "end")]
    pages: dict[tuple[str, int], dict[str, Any]] = {}
    pages.update(_partition_pages("wait", [], declared=0))
    pages.update(_partition_pages("ing", [], declared=0))
    pages.update(_partition_pages("end", end, declared=len(end) + 1))

    rows, _, meta, _, _ = _run(pages=pages)

    assert rows == []
    assert meta["configured_collection_error"] == ""
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == "official wait and ing partitions are empty"
    assert meta["pages"] == 4
    assert meta["discovered_links"] == 3


def test_partition_overlap_fails_before_any_detail_request() -> None:
    shared = _course(101, "end")
    pages, details = _fixture(end=[shared])
    rows, _, meta, transport, _ = _run(pages=pages, details=details)
    assert rows == []
    _assert_failed(meta, "partitions overlap")
    assert not any(method == "GET" and urlparse(url).path == bonghwa.BONGHWA_DETAIL_PATH for method, url, _ in transport.calls)


def test_only_the_audited_one_row_end_deficit_is_accepted() -> None:
    pages, details = _fixture()
    pages[("end", 1)] = _json_page("end", 1, 5, pages[("end", 1)]["programList"])
    pages[("end", 2)] = _json_page("end", 2, 5, [])
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert rows == []
    _assert_failed(meta, "expected audited returned 4, got 3")


def test_nonempty_post_last_page_fails_closed() -> None:
    pages, details = _fixture()
    pages[("ing", 2)] = _json_page("ing", 2, 2, [_course(999, "ing")])
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert rows == []
    _assert_failed(meta, "post-last sentinel changed")


@pytest.mark.parametrize("boundary", ["first", "sentinel"])
def test_boundary_drift_after_details_fails_closed(boundary: str) -> None:
    pages, details = _fixture()
    key = ("ing", 1 if boundary == "first" else 2)
    original = deepcopy(pages[key])
    changed = deepcopy(original)
    if boundary == "first":
        changed["programList"][0]["eduTitle"] = "경계 변경"
    else:
        changed["paginationInfo"] = json.dumps(
            {**json.loads(changed["paginationInfo"]), "firstPageNoOnPageList": 2}
        )
    pages[key] = [original, changed]
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert rows == []
    _assert_failed(meta, f"{boundary}-page stability failed" if boundary == "first" else "sentinel stability failed")


def test_last_page_drift_after_details_fails_closed() -> None:
    ing = [_course(1000 + offset, "ing") for offset in range(12)]
    pages, details = _fixture(ing=ing)
    original = deepcopy(pages[("ing", 2)])
    changed = deepcopy(original)
    changed["programList"][0]["eduCost"] = "6,000원"
    pages[("ing", 2)] = [original, changed]
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert rows == []
    _assert_failed(meta, "last-page stability failed")


def test_detail_limit_fails_before_detail_fetching() -> None:
    rows, _, meta, transport, _ = _run(detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    _assert_failed(meta, "detail_limit 1 below required 2")
    assert not any(method == "GET" and urlparse(url).path == bonghwa.BONGHWA_DETAIL_PATH for method, url, _ in transport.calls)


@pytest.mark.parametrize(
    ("change", "phrase"),
    [
        ({"title": "다른 강좌"}, "title mismatch"),
        ({"category": "문화예술 | 음악"}, "category mismatch"),
        ({"material_cost": "9,000원"}, "material cost mismatch"),
        ({"application_action": "https://evil.example/apply"}, "unsafe application form"),
        ({"application_method": "post"}, "unsafe application form"),
        ({"control": False}, "lost application control"),
        ({"control_href": "/reservation/edu/academy/apply/agree.do"}, "application control changed"),
    ],
)
def test_detail_and_application_contract_drift_fails_closed(change: Mapping[str, Any], phrase: str) -> None:
    pages, details = _fixture()
    course = pages[("ing", 1)]["programList"][0]
    details[str(course["programAppIdx"])] = _detail(course, "ing", **change)
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert rows == []
    _assert_failed(meta, phrase)


def test_application_hidden_identity_drift_fails_closed() -> None:
    pages, details = _fixture()
    course = pages[("ing", 1)]["programList"][0]
    details[str(course["programAppIdx"])] = _detail(
        course,
        "ing",
        hidden={
            "searchStage": "17",
            "searchProgram": "11",
            "searchAppProgram": "999999",
            "mid": bonghwa.BONGHWA_MID,
        },
    )
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert rows == []
    _assert_failed(meta, "application identity binding changed")


@pytest.mark.parametrize(
    "change",
    [
        {"target": "문의 010-1234-5678"},
        {"room": "봉화군 평생학습관 010-1234-5678"},
        {"material_cost": "문의 010-1234-5678"},
    ],
)
def test_pii_in_allowlisted_output_fields_fails_closed(change: Mapping[str, str]) -> None:
    pages, details = _fixture()
    course = pages[("ing", 1)]["programList"][0]
    if "material_cost" in change:
        course["eduCost"] = change["material_cost"]
    details[str(course["programAppIdx"])] = _detail(course, "ing", **change)
    rows, _, meta, _, _ = _run(pages=pages, details=details)
    assert rows == []
    _assert_failed(meta, "PII persisted")


def test_redirect_to_sensitive_or_off_owner_path_is_rejected() -> None:
    pages, details = _fixture()
    landing = FakeResponse(
        _landing(),
        f"https://{bonghwa.BONGHWA_HOST}{bonghwa.BONGHWA_APPLICATION_PATH}",
    )
    rows, _, meta, _, _ = _run(pages=pages, details=details, landing=landing)
    assert rows == []
    _assert_failed(meta, "unsafe request URL")


def test_max_pages_and_dedupe_identity_loss_fail_closed() -> None:
    ing = [_course(1000 + offset, "ing") for offset in range(11)]
    pages, details = _fixture(ing=ing)
    rows, _, meta, _, _ = _run(pages=pages, details=details, max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    _assert_failed(meta, "exceeds max_pages")

    rows, _, meta, _, _ = _run(dedupe_rows=lambda values: values[:-1])
    assert rows == []
    _assert_failed(meta, "dedupe changed the current identity set")


@pytest.mark.skipif(
    os.getenv("RUN_BONGHWA_LIVE") != "1",
    reason="set RUN_BONGHWA_LIVE=1 for the two-pass official-source audit",
)
def test_live_official_source_twice_matches_audited_complete_snapshot() -> None:
    snapshots: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for _ in range(2):
        rows, parser, meta = bonghwa.collect_bonghwa_education(
            _target(),
            today="2026-07-23",
            max_pages=bonghwa.BONGHWA_RECOMMENDED_MAX_PAGES,
            detail_limit=bonghwa.BONGHWA_RECOMMENDED_DETAIL_LIMIT,
            max_workers=bonghwa.BONGHWA_RECOMMENDED_MAX_WORKERS,
            allow_raw_requests_for_tests=True,
        )
        assert parser == bonghwa.BONGHWA_PARSER
        assert meta["configured_collection_error"] == ""
        assert meta["full_snapshot_validated"] is True
        baseline = bonghwa.BONGHWA_LIVE_AUDIT_BASELINE
        assert meta["partition_declared_counts"] == baseline["declared_counts"]
        assert meta["partition_returned_counts"] == baseline["returned_counts"]
        assert meta["partition_data_pages"] == baseline["data_pages"]
        assert meta["partition_page_counts"] == baseline["page_counts"]
        assert meta["partition_sentinel_pages"] == baseline["sentinel_pages"]
        assert meta["partition_declared_deficits"] == baseline["declared_deficits"]
        assert meta["partition_union_count"] == baseline["partition_union_count"]
        assert meta["partition_overlap_count"] == baseline["partition_overlap_count"]
        assert meta["source_status_counts"] == baseline["source_status_counts"]
        assert meta["current_source_count"] == baseline["current_rows"]
        assert meta["detail_pages"] == baseline["current_details"]
        assert meta["application_control_count"] == baseline["application_controls"]
        assert meta["logical_requests"] == baseline["expected_logical_requests"]
        assert meta["post_requests"] == baseline["expected_post_requests"]
        assert meta["get_requests"] == baseline["expected_get_requests"]
        assert len(rows) == baseline["current_rows"]
        assert {row["provider"] for row in rows} == {bonghwa.BONGHWA_PROVIDER}
        assert {row["branch"] for row in rows} == {bonghwa.BONGHWA_BRANCH}
        assert meta["application_endpoints_called"] == 0
        assert meta["applicant_endpoints_called"] == 0
        assert meta["attachment_endpoints_called"] == 0
        assert meta["privacy_violations"] == 0
        snapshots.append((rows, meta))
    first_rows, first_meta = snapshots[0]
    second_rows, second_meta = snapshots[1]
    assert first_rows == second_rows
    stable_keys = (
        "partition_declared_counts",
        "partition_returned_counts",
        "partition_page_counts",
        "partition_sentinel_pages",
        "partition_union_count",
        "source_status_counts",
        "current_source_count",
        "returned_count",
    )
    assert {key: first_meta[key] for key in stable_keys} == {
        key: second_meta[key] for key in stable_keys
    }
