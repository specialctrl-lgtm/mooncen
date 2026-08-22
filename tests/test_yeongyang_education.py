from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from hashlib import sha1, sha256
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from Crawler import municipal_yeongyang as yeongyang


TARGET = {
    "provider": yeongyang.YEONGYANG_PROVIDER,
    "url": yeongyang.YEONGYANG_CANONICAL_URL,
}


@dataclass(frozen=True)
class SyntheticCourse:
    teach_idx: str
    age: str
    group_idx: str
    status_code: str
    source_status: str
    title: str
    category: str
    apply_start: str
    apply_end: str
    event_start: str
    event_end: str
    start_time: str
    end_time: str
    days: str
    venue: str
    target: str
    material_fee: str
    grade_limit: str
    capacity_current: int
    capacity_total: int
    waitlist_current: int
    waitlist_capacity: int


GROUP_NAMES = {
    item.code: item.label for item in yeongyang.YEONGYANG_GROUP_FILTERS
}
STATUS_NAMES = {
    "0": "수강신청",
    "1": "대기자 신청",
    "2": "신청완료",
    "3": "대기자 신청완료",
    "4": "접수마감",
    "5": "정원마감",
    "6": "신청대기",
}


def _courses() -> list[SyntheticCourse]:
    rows: list[SyntheticCourse] = []
    groups = ("39", "34", "33", "32", "30", "29", "26")
    for index, status_code in enumerate(("0", "1", "2", "3", "4", "5", "6")):
        if status_code in {"0", "1", "5"}:
            apply_start, apply_end = "2026-07-20", "2026-07-31"
        elif status_code == "6":
            apply_start, apply_end = "2026-07-24", "2026-08-01"
        else:
            apply_start, apply_end = "2026-07-01", "2026-07-15"
        expired = status_code == "2"
        group_idx = groups[index]
        capacity_total = 12
        capacity_current = 12 if status_code in {"1", "5"} else index + 3
        rows.append(
            SyntheticCourse(
                teach_idx=str(92_000 + index),
                age="11" if index < 4 else "13",
                group_idx=group_idx,
                status_code=status_code,
                source_status=STATUS_NAMES[status_code],
                title=f"영양 합성 강좌 {index + 1}",
                category=GROUP_NAMES[group_idx],
                apply_start=apply_start,
                apply_end=apply_end,
                event_start="2026-07-01" if expired else "2026-08-04",
                event_end="2026-07-20" if expired else "2026-08-28",
                start_time="10:00",
                end_time="12:00",
                days="화, 목",
                venue=f"영양도서관 강좌실{index % 2 + 1}",
                target="초등학생" if index < 4 else "성인",
                material_fee=f"재료비 {(index + 1) * 1000:,}원",
                grade_limit=(
                    "" if index == 0 else "초등 1학년 ~ 초등 6학년"
                    if index < 4
                    else "제한없음"
                ),
                capacity_current=capacity_current,
                capacity_total=capacity_total,
                waitlist_current=2 if status_code == "1" else 0,
                waitlist_capacity=5,
            )
        )
    return rows


def _select_options(
    values: tuple[tuple[str, str], ...],
    selected: str,
) -> str:
    return "".join(
        f'<option value="{value}"{" selected" if value == selected else ""}>{label}</option>'
        for value, label in values
    )


def _owner_shell(route: yeongyang._Route, body: str) -> str:
    return f"""
    <html><head><meta charset="utf-8">
      <title>{yeongyang.YEONGYANG_BRANCH} &gt; 평생교육 &gt; 프로그램신청 &gt; {route.label}</title>
    </head><body>
      <div id="contentArea"><div class="body">{body}</div></div>
      <div id="footer">
        <h3>{yeongyang.YEONGYANG_BRANCH}</h3>
        <p>(우 36541) {yeongyang.YEONGYANG_BRANCH_ADDRESS} (동부리)</p>
        <p>전화 054-683-2829 팩스 054-683-1718</p>
      </div>
    </body></html>
    """


def _age_navigation() -> str:
    # The live navigation uses this non-normalized order.  The collector must
    # recognize its exact semantics without promoting it as the target URL.
    return "".join(
        f'<a href="{yeongyang.YEONGYANG_LIST_PATH}?menu_idx={route.menu_idx}'
        f'&searchCate1=18&searchAge={route.search_age}">{route.label}</a>'
        for route in yeongyang.YEONGYANG_ROUTES
    )


def _forms(
    route: yeongyang._Route,
    filter_value: yeongyang._Filter | None,
    *,
    registry_drift: bool = False,
) -> str:
    selected_group = (
        filter_value.code
        if filter_value is not None and filter_value.kind == "group"
        else ""
    )
    selected_status = (
        filter_value.code
        if filter_value is not None and filter_value.kind == "status"
        else ""
    )
    group_values = tuple(
        (item.code, item.label) for item in yeongyang.YEONGYANG_GROUP_FILTERS
    )
    if registry_drift:
        group_values = group_values[:-1]
    group_options = (("", "전체 보기"),) + group_values
    status_options = (("", "전체 보기"),) + tuple(
        (item.code, item.label) for item in yeongyang.YEONGYANG_STATUS_FILTERS
    )
    return f"""
      <form id="teach" method="POST" action="{yeongyang.YEONGYANG_APPLICATION_SAVE_PATH}">
        <input type="hidden" name="group_idx" value="{selected_group or '0'}">
        <input type="hidden" name="teach_idx" value="0">
        <input type="hidden" name="menu_idx" value="{route.menu_idx}">
        <input type="hidden" name="category_idx" value="0">
        <input type="hidden" name="large_category_idx" value="0">
        <input type="hidden" name="searchCate1" value="18">
        <input name="applicant_name"><input name="applicant_phone">
      </form>
      <form id="search_teach" method="GET" action="index.do">
        <input type="hidden" name="menu_idx" value="{route.menu_idx}">
        <input type="hidden" name="searchCate1" value="18">
        <input type="hidden" name="teach_day_arr" value="">
        <input type="hidden" name="noSearchCate" value="">
        <select id="group_idx" name="group_idx">
          {_select_options(group_options, selected_group)}
        </select>
        <select id="teach_status" name="teach_status">
          {_select_options(status_options, selected_status)}
        </select>
      </form>
      {_age_navigation()}
    """


def _active_control(course: SyntheticCourse) -> str:
    if course.status_code in {"0", "1"}:
        apply_status = "1" if course.status_code == "0" else "2"
        return f"""
          <a href="" class="btn btn1 add"
             keyvalue1="h17" keyvalue2="{course.group_idx}"
             keyvalue3="0" keyvalue4="{course.teach_idx}"
             keyvalue5="18" keyvalue6="" apply_status="{apply_status}">
            <span>{course.source_status}</span>
          </a>
        """
    return (
        '<a href="javascript:void(0);" class="btn" style="cursor: default;">'
        f"<span>{course.source_status}</span></a>"
    )


def _item_html(course: SyntheticCourse, *, title_suffix: str = "") -> str:
    identity = (
        f'keyvalue1="{course.group_idx}" keyvalue2="0" '
        f'keyvalue3="{course.teach_idx}" keyvalue4="18"'
    )
    grade = (
        f"<li><label>학년제한</label> : {course.grade_limit}</li>"
        if course.grade_limit
        else ""
    )
    return f"""
      <div class="item">
        <div class="op_title category">
          <span class="ca ty2">{course.category}</span>
          <a href="" class="name detail-btn" {identity}>{course.title}{title_suffix}</a>
          <a href="" class="name detail-btn btn btn6" {identity}>상세보기</a>
        </div>
        <div class="box"><div class="box2"><ul class="con2">
          <li><div><label>접수기간</label> : {course.apply_start} {course.start_time} ~ {course.apply_end} 16:00</div></li>
          <li><div><label>장소</label> : {course.venue}</div></li>
          <li><div><label>강좌일</label> : {course.event_start} ~ {course.event_end} ( {course.days} ) {course.start_time} ~ {course.end_time}</div></li>
          <li><div><label>강사명</label> : 저장하면 안 되는 개인강사</div></li>
          <li><div><label>강의계획서</label> : <a href="download/h17/{course.group_idx}/0/{course.teach_idx}.do">개인강의계획서.hwp</a></div></li>
          <li><div><label>모집인원</label> : 온라인 {course.capacity_total}명, (후보자 {course.waitlist_capacity}명)</div></li>
          <li><div><label>접수현황</label> : 온라인 : {course.capacity_current} / {course.capacity_total} (후보자 : {course.waitlist_current} / {course.waitlist_capacity})</div></li>
          <li><div><label>모집대상</label> : {course.target}</div></li>
          <li><div><label>준비물 및 재료비</label> : {course.material_fee}</div></li>
          {grade}
        </ul></div></div>
        <div class="stat">{_active_control(course)}</div>
      </div>
    """


def _list_html(
    route: yeongyang._Route,
    courses: list[SyntheticCourse],
    filter_value: yeongyang._Filter | None,
    *,
    title_suffix: str = "",
    bad_empty: bool = False,
    registry_drift: bool = False,
    wrong_route_title: bool = False,
) -> str:
    forms = _forms(route, filter_value, registry_drift=registry_drift)
    if courses:
        items = "".join(
            _item_html(course, title_suffix=title_suffix if index == 0 else "")
            for index, course in enumerate(courses)
        )
        table_rows = "".join("<tr><td>parallel row</td></tr>" for _ in courses)
        ledger = (
            f'<div id="table_mode"><table class="bbs"><tbody>{table_rows}</tbody></table></div>'
            f'<div id="list_mode" style="display:none">{items}</div>'
        )
    elif bad_empty:
        ledger = (
            '<div id="table_mode"><table class="bbs"><tbody></tbody></table></div>'
            '<div class="nodata"><p>빈 원장 문구 변경</p></div>'
            '<div id="list_mode" style="display:none"></div>'
        )
    else:
        ledger = (
            '<div id="table_mode"><table class="bbs"><tbody></tbody></table></div>'
            '<div class="nodata"><p>등록된 프로그램이 없습니다.</p></div>'
            '<div id="list_mode" style="display:none"></div>'
        )
    shell_route = (
        yeongyang.YEONGYANG_ROUTES[0]
        if wrong_route_title and route.search_age == "13"
        else route
    )
    return _owner_shell(shell_route, forms + ledger)


def _detail_html(
    route: yeongyang._Route,
    course: SyntheticCourse,
    *,
    title_suffix: str = "",
    omit_application: bool = False,
) -> str:
    if course.status_code in {"0", "1"} and not omit_application:
        apply_status = "1" if course.status_code == "0" else "2"
        status_control = (
            f'<a href="" class="btn btn1 apply-btn" apply_status="{apply_status}">'
            f"<span>{course.source_status}</span></a>"
        )
    else:
        status_control = (
            '<a href="javascript:void(0);" class="btn" style="cursor: default;">'
            f"<span>{course.source_status}</span></a>"
        )
    grade = (
        f"<tr><th>학년제한</th><td>{course.grade_limit}</td></tr>"
        if course.grade_limit
        else ""
    )
    body = f"""
      <div class="teach_wrap">
      <div class="teach_top"><h3>{course.title}{title_suffix}</h3></div>
      <div class="teach_detail">
      <table id="teach_table" class="tstyle nohead"><tbody>
        <tr><th colspan="4"><img src="/data/teach/h17/img/private"></th></tr>
        <tr><th>강의 분류</th><td>{course.category}</td></tr>
        <tr><th>강의 설명</th><td>저장하면 안 되는 자유 본문 054-683-2829 private@example.test</td></tr>
        <tr><th>강의장소</th><td>{course.venue}</td><th>강사명</th><td>저장하면 안 되는 개인강사</td></tr>
        <tr><th>준비물 및 재료비</th><td>{course.material_fee}</td><th>강의대상</th><td>{course.target}</td></tr>
        <tr><th>강의장소</th><td>{course.venue}</td></tr>
        <tr><th>강사명</th><td>저장하면 안 되는 개인강사</td></tr>
        <tr><th>준비물 및 재료비</th><td>{course.material_fee}</td></tr>
        <tr><th>강의대상</th><td>{course.target}</td></tr>
        {grade}
        <tr><th>강의계획서</th><td><a href="download/h17/{course.group_idx}/0/{course.teach_idx}.do">개인강의계획서.hwp</a></td></tr>
        <tr><th>접수기간</th><td>{course.apply_start} {course.start_time} ~ {course.apply_end} 16:00</td></tr>
        <tr><th>강의기간(*)</th><td>{course.event_start} ~ {course.event_end}</td></tr>
        <tr><th>강의시간</th><td>{course.start_time} ~ {course.end_time}</td><th>강의요일</th><td>{course.days}</td></tr>
        <tr><th>강의시간</th><td>{course.start_time} ~ {course.end_time}</td></tr>
        <tr><th>강의요일</th><td>{course.days}</td></tr>
        <tr><th>현재 참여 / 모집</th><td>{course.capacity_current}명 / {course.capacity_total}명</td></tr>
        <tr><th>현재 대기자 / 대기자</th><td>{course.waitlist_current}명 / {course.waitlist_capacity}명</td></tr>
      </tbody></table></div>
      <div class="sbtn">
        <a id="back-btn" href="" class="btn"><span>목록으로</span></a>
        {status_control}
      </div></div>
    """
    return _owner_shell(route, body)


class FakeResponse:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.text = html
        self.status_code = status_code
        self.history: list[object] = []

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def close(self) -> None:
        return None


class SyntheticBackend:
    def __init__(
        self,
        *,
        duplicate_identity: bool = False,
        overlap_age: bool = False,
        partition_drift: bool = False,
        missing_partition: bool = False,
        unstable: bool = False,
        bad_empty: bool = False,
        registry_drift: bool = False,
        wrong_route_title: bool = False,
        detail_title_mismatch: str = "",
        omit_application: str = "",
        response_url_drift: bool = False,
    ):
        self.courses = _courses()
        if duplicate_identity:
            self.courses[1] = replace(
                self.courses[1],
                teach_idx=self.courses[0].teach_idx,
            )
        self.overlap_age = overlap_age
        self.partition_drift = partition_drift
        self.missing_partition = missing_partition
        self.unstable = unstable
        self.bad_empty = bad_empty
        self.registry_drift = registry_drift
        self.wrong_route_title = wrong_route_title
        self.detail_title_mismatch = detail_title_mismatch
        self.omit_application = omit_application
        self.response_url_drift = response_url_drift
        self.urls: list[str] = []
        self.base_calls = {"11": 0, "13": 0}
        self.lock = Lock()

    def _list_response(
        self,
        url: str,
        query: dict[str, list[str]],
    ) -> FakeResponse:
        menu_idx = query.get("menu_idx", [""])[0]
        age = query.get("searchAge", [""])[0]
        route = yeongyang.YEONGYANG_ROUTE_BY_AGE[age]
        assert menu_idx == route.menu_idx
        group = query.get("group_idx", [""])[0]
        status = query.get("teach_status", [""])[0]
        rows = [course for course in self.courses if course.age == age]
        filter_value: yeongyang._Filter | None = None
        if group:
            filter_value = next(
                item for item in yeongyang.YEONGYANG_GROUP_FILTERS if item.code == group
            )
            rows = [course for course in rows if course.group_idx == group]
            if self.partition_drift and age == "11" and group == "34" and rows:
                rows[0] = replace(rows[0], title=rows[0].title + " 변경")
        elif status:
            filter_value = next(
                item
                for item in yeongyang.YEONGYANG_STATUS_FILTERS
                if item.code == status
            )
            rows = [course for course in rows if course.status_code == status]
            if self.missing_partition and age == "13" and status == "4":
                rows = []
        else:
            with self.lock:
                self.base_calls[age] += 1
                base_call = self.base_calls[age]
            if self.overlap_age and age == "13":
                rows.append(self.courses[0])
            title_suffix = (
                " 변경" if self.unstable and age == "11" and base_call >= 2 else ""
            )
            return FakeResponse(
                url,
                _list_html(
                    route,
                    rows,
                    filter_value,
                    title_suffix=title_suffix,
                    registry_drift=self.registry_drift and age == "13",
                    wrong_route_title=self.wrong_route_title,
                ),
            )
        return FakeResponse(
            url,
            _list_html(
                route,
                rows,
                filter_value,
                bad_empty=(self.bad_empty and age == "11" and group == "9"),
                registry_drift=self.registry_drift and age == "13",
                wrong_route_title=self.wrong_route_title,
            ),
        )

    def response(self, url: str) -> FakeResponse:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self.lock:
            self.urls.append(url)
        if parsed.path == yeongyang.YEONGYANG_LIST_PATH:
            response = self._list_response(url, query)
        elif parsed.path == yeongyang.YEONGYANG_DETAIL_PATH:
            identity = query.get("teach_idx", [""])[0]
            course = next(row for row in self.courses if row.teach_idx == identity)
            route = yeongyang.YEONGYANG_ROUTE_BY_MENU[
                query.get("menu_idx", [""])[0]
            ]
            response = FakeResponse(
                url,
                _detail_html(
                    route,
                    course,
                    title_suffix=(
                        " 불일치" if identity == self.detail_title_mismatch else ""
                    ),
                    omit_application=identity == self.omit_application,
                ),
            )
        else:
            response = FakeResponse(url, "not found", 404)
        if self.response_url_drift:
            response.url = response.url.replace("www.gbelib.kr", "gbelib.kr")
        return response


def _fetch(backend: SyntheticBackend):
    def fetcher(_session: object, url: str, _timeout: int) -> FakeResponse:
        return backend.response(url)

    return fetcher


def _collect(backend: SyntheticBackend, **options):
    return yeongyang.collect_yeongyang_education(
        TARGET,
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=_fetch(backend),
        **options,
    )


def test_hashes_exact_target_deprecated_alias_and_owner_boundaries() -> None:
    canonical = yeongyang.YEONGYANG_CANONICAL_URL
    assert yeongyang.YEONGYANG_PROVIDER == (
        "MUNI_WWW_GBELIB_KR_" + sha1(canonical.encode()).hexdigest()[:8].upper()
    )
    assert yeongyang.YEONGYANG_CANONICAL_CANDIDATE_ID == (
        "MUNI_IR_" + sha256(canonical.encode()).hexdigest()[:12].upper()
    )
    assert yeongyang.YEONGYANG_CANONICAL_URL_SHA256 == sha256(
        canonical.encode()
    ).hexdigest()
    deprecated_url = yeongyang.YEONGYANG_DEPRECATED_ALIAS_URL
    assert yeongyang.YEONGYANG_DEPRECATED_ALIAS_PROVIDER == (
        "MUNI_WWW_GBELIB_KR_" + sha1(deprecated_url.encode()).hexdigest()[:8].upper()
    )
    assert yeongyang.is_target(TARGET)
    assert not yeongyang.is_target(
        {**TARGET, "provider": yeongyang.YEONGYANG_DEPRECATED_ALIAS_PROVIDER}
    )
    assert not yeongyang.is_target({**TARGET, "url": deprecated_url})
    assert not yeongyang.is_target({**TARGET, "url": yeongyang.YEONGYANG_CHILD_URL})
    assert not yeongyang.is_target({**TARGET, "url": canonical + "&group_idx=39"})
    assert not yeongyang.is_target(
        {**TARGET, "url": canonical.replace("https://", "http://")}
    )
    audit = yeongyang.YEONGYANG_CANDIDATE_AUDIT
    assert audit[yeongyang.YEONGYANG_CANONICAL_CANDIDATE_ID]["url_sha256"] == (
        yeongyang.YEONGYANG_CANONICAL_URL_SHA256
    )
    alias = yeongyang.YEONGYANG_PROVIDER_ALIAS_AUDIT[
        yeongyang.YEONGYANG_DEPRECATED_ALIAS_PROVIDER
    ]
    assert alias["state"] == "deprecated"
    assert "non_executing" in alias["decision"]
    assert {item["url"] for item in yeongyang.YEONGYANG_OWNER_BOUNDARIES} == {
        "https://www.gbelib.kr/yy/module/teach/index.do?menu_idx=177&searchCate1=16",
        "https://www.yyg.go.kr/",
    }
    assert not yeongyang._allowed_request_url(
        "https://www.gbelib.kr/yy/module/teach/index.do?menu_idx=48&searchAge=11&searchCate1=18"
    )


def test_complete_two_route_partitions_details_privacy_and_recheck() -> None:
    backend = SyntheticBackend()
    rows, parser, meta = _collect(backend)
    assert parser == yeongyang.YEONGYANG_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["existing_active_owner_count"] == 0
    assert meta["disabled_alias_owner_count"] == 1
    assert "deprecated non-executing alias" in meta["provider_decision"]
    assert meta["source_rows"] == 7
    assert meta["source_identity_count"] == 7
    assert meta["current_source_count"] == 6
    assert meta["expired_source_count"] == 1
    assert meta["returned_count"] == 6
    assert meta["source_requests"] == 56
    assert meta["request_attempts"] == 56
    assert meta["list_requests"] == 50
    assert meta["initial_route_requests"] == 2
    assert meta["group_partition_requests"] == 32
    assert meta["status_partition_requests"] == 14
    assert meta["detail_pages"] == 6
    assert meta["boundary_rechecks"] == 2
    assert meta["registry_checks"] == 50
    assert backend.base_calls == {"11": 2, "13": 2}
    assert meta["age_filter_counts"] == {"11": 4, "13": 3}
    assert meta["group_filter_counts"] == {
        code: 1 if code in {"39", "34", "33", "32", "30", "29", "26"} else 0
        for code in GROUP_NAMES
    }
    assert meta["status_filter_counts"] == {str(index): 1 for index in range(7)}
    assert meta["age_group_filter_counts"]["11"]["39"] == 1
    assert meta["age_group_filter_counts"]["13"]["30"] == 1
    assert meta["age_status_filter_counts"]["11"]["0"] == 1
    assert meta["age_status_filter_counts"]["13"]["4"] == 1
    assert meta["age_partition_union_count"] == 7
    assert meta["group_partition_union_count"] == 7
    assert meta["status_partition_union_count"] == 7
    assert meta["partition_overlap_count"] == 0
    assert meta["empty_age_route_count"] == 0
    assert meta["empty_partition_count"] == 32
    assert meta["age_partition_complete"]
    assert meta["group_partition_complete"]
    assert meta["status_partition_complete"]
    assert meta["pagination_complete"]
    assert meta["full_ledger_rechecked_after_details"]
    assert meta["details_complete"]
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]
    assert Counter(row["status"] for row in rows) == {
        "CLOSED": 3,
        "OPEN": 1,
        "WAITLIST": 1,
        "SCHEDULED": 1,
    }
    assert meta["application_control_count"] == 2
    assert meta["actionable_application_count"] == 2
    assert {row["provider_course_id"] for row in rows} == {
        f"{yeongyang.YEONGYANG_PROVIDER}:teach:{identity}"
        for identity in ("92000", "92001", "92003", "92004", "92005", "92006")
    }
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["branch"] == yeongyang.YEONGYANG_BRANCH for row in rows)
    assert all(
        row["branch_url"] == yeongyang.YEONGYANG_CANONICAL_URL for row in rows
    )
    optional = next(
        row for row in rows if row["raw_fields"]["identity"] == "92000"
    )
    assert optional["raw_fields"]["source_grade_limit"] == ""
    assert "menu_idx=46" in optional["raw_url"]
    assert all(
        bool(row["application_url"]) == row["reservation_available"]
        for row in rows
    )
    assert all(
        "homepage_id=h17" in row["application_url"]
        for row in rows
        if row["reservation_available"]
    )
    assert all(
        row["raw_fields"]["application_form_endpoint_fetched"] is False
        and row["raw_fields"]["application_save_endpoint_fetched"] is False
        and row["raw_fields"]["attachment_endpoint_fetched"] is False
        and row["raw_fields"]["pii_endpoint_fetched"] is False
        for row in rows
    )
    assert meta["application_endpoints_called"] == 0
    assert meta["application_save_endpoints_called"] == 0
    assert meta["attachment_endpoints_called"] == 0
    assert meta["pii_endpoints_called"] == 0
    assert meta["privacy_violations"] == 0
    payload = repr(rows)
    for forbidden in (
        "저장하면 안 되는 개인강사",
        "저장하면 안 되는 자유 본문",
        "054-683-2829",
        "private@example.test",
        "개인강의계획서.hwp",
        "applicant_name",
        "applicant_phone",
    ):
        assert forbidden not in payload
    requested_paths = Counter(urlparse(url).path for url in backend.urls)
    assert requested_paths == {
        yeongyang.YEONGYANG_LIST_PATH: 50,
        yeongyang.YEONGYANG_DETAIL_PATH: 6,
    }
    assert yeongyang.YEONGYANG_APPLICATION_PATH not in requested_paths
    assert yeongyang.YEONGYANG_APPLICATION_SAVE_PATH not in requested_paths
    assert not any("download/" in url for url in backend.urls)


@pytest.mark.parametrize(
    ("backend", "error_fragment"),
    (
        (
            SyntheticBackend(duplicate_identity=True),
            "invalid or duplicate compound course identity",
        ),
        (SyntheticBackend(overlap_age=True), "age routes overlap at course"),
        (
            SyntheticBackend(partition_drift=True),
            "11 group partition 34 changed course",
        ),
        (
            SyntheticBackend(missing_partition=True),
            "13 status partitions do not cover route ledger",
        ),
        (
            SyntheticBackend(unstable=True),
            "age route 11 stability recheck changed",
        ),
        (SyntheticBackend(bad_empty=True), "empty filter sentinel changed"),
        (
            SyntheticBackend(registry_drift=True),
            "programme group registry changed",
        ),
        (
            SyntheticBackend(wrong_route_title=True),
            "official owner name/address evidence missing",
        ),
        (
            SyntheticBackend(detail_title_mismatch="92000"),
            "list/detail title drift",
        ),
        (
            SyntheticBackend(omit_application="92000"),
            "detail application control drift",
        ),
        (SyntheticBackend(response_url_drift=True), "response URL changed"),
    ),
)
def test_contract_drift_is_atomic(
    backend: SyntheticBackend,
    error_fragment: str,
) -> None:
    rows, _, meta = _collect(backend)
    assert rows == []
    assert error_fragment in meta["configured_collection_error"]
    assert not meta["snapshot_complete"]
    assert not meta["full_snapshot_validated"]


def test_limits_managed_session_alias_and_dedupe_fail_closed() -> None:
    rows, _, meta = yeongyang.collect_yeongyang_education(
        TARGET,
        today="2026-07-23",
    )
    assert rows == []
    assert meta["configured_collection_error"] == (
        "managed session_factory injection is required"
    )

    rows, _, meta = _collect(SyntheticBackend(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"]
    assert "stable recheck" in meta["configured_collection_error"]

    rows, _, meta = _collect(SyntheticBackend(), detail_limit=5)
    assert rows == []
    assert meta["source_cap_reached"]
    assert "detail_limit" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0

    rows, _, meta = _collect(
        SyntheticBackend(),
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]

    rows, _, meta = yeongyang.collect_yeongyang_education(
        {
            "provider": yeongyang.YEONGYANG_DEPRECATED_ALIAS_PROVIDER,
            "url": yeongyang.YEONGYANG_DEPRECATED_ALIAS_URL,
        },
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=_fetch(SyntheticBackend()),
    )
    assert rows == []
    assert "exact normalized Yeongyang owner" in meta["configured_collection_error"]
    assert meta["source_requests"] == 0


class RecordingSession:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            }
        )
        self.urls: list[str] = []

    def get(self, url: str, **kwargs):
        self.urls.append(url)
        return self.session.get(url, **kwargs)

    def close(self) -> None:
        self.session.close()


def _live_snapshot():
    tracker = RecordingSession()
    rows, parser, meta = yeongyang.collect_yeongyang_education(
        TARGET,
        today="2026-07-23",
        session_factory=lambda: tracker,
        timeout=30,
        max_pages=2,
        detail_limit=50,
    )
    return rows, parser, meta, tracker.urls


@pytest.mark.skipif(
    os.getenv("RUN_YEONGYANG_LIVE") != "1",
    reason="set RUN_YEONGYANG_LIVE=1 for two bounded official-source snapshots",
)
def test_live_two_exact_stable_snapshots() -> None:
    first_rows, first_parser, first_meta, first_urls = _live_snapshot()
    second_rows, second_parser, second_meta, second_urls = _live_snapshot()
    assert first_parser == second_parser == yeongyang.YEONGYANG_PARSER
    assert first_meta["configured_collection_error"] == ""
    assert second_meta["configured_collection_error"] == ""
    assert first_rows == second_rows
    for meta in (first_meta, second_meta):
        assert meta["source_requests"] == 52
        assert 52 <= meta["request_attempts"] <= 104
        assert meta["list_requests"] == 50
        assert meta["initial_route_requests"] == 2
        assert meta["group_partition_requests"] == 32
        assert meta["status_partition_requests"] == 14
        assert meta["detail_pages"] == 2
        assert meta["boundary_rechecks"] == 2
        assert meta["registry_checks"] == 50
        assert meta["source_rows"] == 2
        assert meta["source_identity_count"] == 2
        assert meta["source_teach_ids"] == ["15569", "15570"]
        assert meta["source_identity_numeric_min"] == 15569
        assert meta["source_identity_numeric_max"] == 15570
        assert meta["current_source_count"] == 2
        assert meta["expired_source_count"] == 0
        assert meta["source_status_counts"] == {"접수마감": 2}
        assert meta["source_category_counts"] == {"2026년 여름방학특강": 2}
        assert meta["age_filter_counts"] == {"11": 2, "13": 0}
        assert meta["group_filter_counts"] == {
            code: 2 if code == "39" else 0 for code in GROUP_NAMES
        }
        assert meta["status_filter_counts"] == {
            "0": 0,
            "1": 0,
            "2": 0,
            "3": 0,
            "5": 0,
            "4": 2,
            "6": 0,
        }
        assert meta["age_partition_union_count"] == 2
        assert meta["group_partition_union_count"] == 2
        assert meta["status_partition_union_count"] == 2
        assert meta["partition_overlap_count"] == 0
        assert meta["empty_age_route_count"] == 1
        assert meta["empty_partition_count"] == 44
        assert meta["status_counts"] == {"CLOSED": 2}
        assert meta["raw_status_counts"] == {"접수마감": 2}
        assert meta["application_control_count"] == 0
        assert meta["actionable_application_count"] == 0
        assert meta["application_endpoints_called"] == 0
        assert meta["application_save_endpoints_called"] == 0
        assert meta["attachment_endpoints_called"] == 0
        assert meta["pii_endpoints_called"] == 0
        assert meta["privacy_violations"] == 0
        assert meta["semantic_duplicate_count"] == 0
        assert meta["full_ledger_rechecked_after_details"]
        assert meta["details_complete"]
        assert meta["snapshot_complete"]
        assert meta["full_snapshot_validated"]
    assert first_meta["source_requests"] + second_meta["source_requests"] == 104
    assert {row["provider_course_id"] for row in first_rows} == {
        f"{yeongyang.YEONGYANG_PROVIDER}:teach:15569",
        f"{yeongyang.YEONGYANG_PROVIDER}:teach:15570",
    }
    expected = {
        "15569": ("2026-07-18", "2026-08-08", 10, 10, 4, 5, ""),
        "15570": (
            "2026-07-19",
            "2026-08-09",
            20,
            20,
            2,
            5,
            "초등 1학년 ~ 초등 6학년",
        ),
    }
    for row in first_rows:
        identity = row["raw_fields"]["identity"]
        assert (
            row["start_date"],
            row["end_date"],
            row["capacity_current"],
            row["capacity_total"],
            row["waitlist_current"],
            row["waitlist_capacity"],
            row["raw_fields"]["source_grade_limit"],
        ) == expected[identity]
        assert row["apply_start_date"] == "2026-07-01"
        assert row["apply_end_date"] == "2026-07-09"
        assert row["status"] == "CLOSED"
        assert not row["reservation_available"]
        assert row["application_url"] == ""
        assert row["branch"] == yeongyang.YEONGYANG_BRANCH
        assert row["address"] == yeongyang.YEONGYANG_BRANCH_ADDRESS
        assert row["raw_fields"]["menu_idx"] == "46"
        assert row["raw_fields"]["search_age"] == "11"
    for urls in (first_urls, second_urls):
        paths = Counter(urlparse(url).path for url in urls)
        assert set(paths) <= {
            yeongyang.YEONGYANG_LIST_PATH,
            yeongyang.YEONGYANG_DETAIL_PATH,
        }
        assert yeongyang.YEONGYANG_APPLICATION_PATH not in paths
        assert yeongyang.YEONGYANG_APPLICATION_SAVE_PATH not in paths
        assert not any("download/" in url for url in urls)
        for url in urls:
            if urlparse(url).path != yeongyang.YEONGYANG_LIST_PATH:
                continue
            query = parse_qs(urlparse(url).query, keep_blank_values=True)
            assert (query["menu_idx"][0], query["searchAge"][0]) in {
                ("46", "11"),
                ("48", "13"),
            }
            assert query["searchCate1"] == ["18"]
