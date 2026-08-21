from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from hashlib import sha1, sha256
import os
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from Crawler import municipal_yeongdeok as yeongdeok


TARGET = {
    "provider": yeongdeok.YEONGDEOK_PROVIDER,
    "url": yeongdeok.YEONGDEOK_CANONICAL_URL,
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
    "5": "상반기 평생교육강좌",
    "6": "여름방학특강 평생교육강좌",
    "7": "하반기 평생교육강좌",
    "8": "겨울방학특강 평생교육강좌",
}
STATUS_NAMES = {
    "0": "수강신청",
    "1": "대기자신청",
    "2": "신청완료",
    "3": "대기자신청완료",
    "4": "접수마감",
    "5": "정원마감",
    "6": "신청대기",
}


def _courses() -> list[SyntheticCourse]:
    rows: list[SyntheticCourse] = []
    groups = ("5", "6", "7", "5", "6", "7", "5")
    for index, status_code in enumerate(("0", "1", "2", "3", "4", "5", "6")):
        source_status = STATUS_NAMES[status_code]
        if status_code in {"0", "1"}:
            apply_start, apply_end = "2026-07-20", "2026-07-31"
        elif status_code == "5":
            apply_start, apply_end = "2026-07-20", "2026-07-31"
        elif status_code == "6":
            apply_start, apply_end = "2026-07-24", "2026-08-01"
        else:
            apply_start, apply_end = "2026-07-01", "2026-07-15"
        group_idx = groups[index]
        capacity_total = 12
        capacity_current = 12 if status_code in {"1", "5"} else index + 3
        rows.append(
            SyntheticCourse(
                teach_idx=str(91_000 + index),
                age="11" if index < 4 else "13",
                group_idx=group_idx,
                status_code=status_code,
                source_status=source_status,
                title=f"영덕 합성 강좌 {index + 1}",
                category=GROUP_NAMES[group_idx],
                apply_start=apply_start,
                apply_end=apply_end,
                event_start="2026-08-04",
                event_end="2026-08-28",
                start_time="10:00",
                end_time="12:00",
                days="화 , 목",
                venue=f"강의실{index % 2 + 1}",
                target="초등 3-6" if index < 4 else "성인",
                material_fee=f"재료비 {(index + 1) * 1000:,}원",
                grade_limit=(
                    "초등 3학년 ~ 초등 6학년" if index < 4 else "제한없음"
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


def _owner_shell(title_tail: str, body: str) -> str:
    return f"""
    <html><head><meta charset="utf-8">
      <title>{yeongdeok.YEONGDEOK_BRANCH} &gt; 평생교육 &gt; {title_tail}</title>
    </head><body>
      {body}
      <div id="footer">
        <h3>{yeongdeok.YEONGDEOK_BRANCH}</h3>
        <p>(우 36431) {yeongdeok.YEONGDEOK_BRANCH_ADDRESS} (덕곡리, 영덕도서관)</p>
        <p>전화 054)734-3106 팩스 054)732-9945</p>
      </div>
    </body></html>
    """


def _age_navigation() -> str:
    return "".join(
        f'<a href="{urlparse(url).path}?{urlparse(url).query}">{label}</a>'
        for label, url in (
            ("전체", yeongdeok._list_url()),
            ("어린이 프로그램", yeongdeok._list_url(yeongdeok.YEONGDEOK_AGE_FILTERS[0])),
            ("성인 프로그램", yeongdeok._list_url(yeongdeok.YEONGDEOK_AGE_FILTERS[1])),
        )
    )


def _forms(filter_value: yeongdeok._Filter | None) -> str:
    menu_idx = filter_value.menu_idx if filter_value else yeongdeok.YEONGDEOK_MENU_IDX
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
    applicant_group = selected_group or "0"
    group_options = (
        ("", "전체 보기"),
        ("8", "겨울방학특강 평생교육강좌"),
        ("7", "하반기 평생교육강좌"),
        ("6", "여름방학특강 평생교육강좌"),
        ("5", "상반기 평생교육강좌"),
    )
    status_options = (
        ("", "전체 보기"),
        ("0", "수강신청"),
        ("1", "대기자 신청"),
        ("2", "신청완료"),
        ("3", "대기자 신청완료"),
        ("5", "정원마감"),
        ("4", "접수마감"),
        ("6", "신청대기"),
    )
    return f"""
      <form id="teach" method="POST" action="{yeongdeok.YEONGDEOK_APPLICATION_SAVE_PATH}">
        <input type="hidden" name="group_idx" value="{applicant_group}">
        <input type="hidden" name="teach_idx" value="0">
        <input type="hidden" name="menu_idx" value="{menu_idx}">
        <input type="hidden" name="category_idx" value="0">
        <input type="hidden" name="large_category_idx" value="0">
        <input type="hidden" name="searchCate1" value="18">
        <input name="applicant_name"><input name="applicant_phone">
      </form>
      <form id="search_teach" method="GET" action="index.do">
        <input type="hidden" name="menu_idx" value="{menu_idx}">
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
             keyvalue1="h18" keyvalue2="{course.group_idx}"
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
    return f"""
      <div class="item">
        <div class="op_title">
          <span class="ca">{course.category}</span>
          <a href="" class="name detail-btn" {identity}>{course.title}{title_suffix}</a>
          <a href="" class="name detail-btn btn btn6" {identity}>상세보기</a>
        </div>
        <ul class="con2">
          <li><label>접수기간</label>{course.apply_start} {course.start_time} ~ {course.apply_end} 16:00</li>
          <li><label>장소</label>{course.venue}</li>
          <li><label>강좌일</label>{course.event_start} ~ {course.event_end} ( {course.days} ) {course.start_time} ~ {course.end_time}</li>
          <li><label>모집인원</label>온라인 {course.capacity_total}명, (후보자 {course.waitlist_capacity}명)</li>
          <li><label>접수현황</label>온라인 : {course.capacity_current} / {course.capacity_total} (후보자 : {course.waitlist_current} / {course.waitlist_capacity})</li>
          <li><label>모집대상</label>{course.target}</li>
          <li><label>준비물 및 재료비</label>{course.material_fee}</li>
          <li><label>학년제한</label>{course.grade_limit}</li>
          <li><label>강사명</label>저장하면 안 되는 개인강사</li>
          <li><label>강의계획서</label><a href="download/private-{course.teach_idx}.do">개인강의계획서.hwp</a></li>
        </ul>
        <div class="stat">{_active_control(course)}</div>
      </div>
    """


def _list_html(
    courses: list[SyntheticCourse],
    filter_value: yeongdeok._Filter | None,
    *,
    title_suffix: str = "",
    bad_empty: bool = False,
) -> str:
    if filter_value is not None and filter_value.kind == "age":
        title_tail = f"평생강좌신청 > {filter_value.label}"
    else:
        title_tail = "평생학습강좌신청 > 전체"
    if courses:
        items = "".join(
            _item_html(
                course,
                title_suffix=title_suffix if index == 0 else "",
            )
            for index, course in enumerate(courses)
        )
        ledger = f'<div id="list_mode" style="display:none">{items}</div>'
    elif bad_empty:
        ledger = '<div id="list_mode" style="display:none"><p>구조가 바뀜</p></div>'
    else:
        ledger = '<div id="list_mode" style="display:none"></div>'
    return _owner_shell(title_tail, _forms(filter_value) + ledger)


def _detail_html(
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
    body = f"""
      <div class="teach_top"><h3>{course.title}{title_suffix}</h3></div>
      <table id="teach_table" class="tstyle nohead"><tbody>
        <tr><th>강의 분류</th><td>{course.category}</td></tr>
        <tr><th>강의장소</th><td>{course.venue}</td></tr>
        <tr><th>준비물 및 재료비</th><td>{course.material_fee}</td></tr>
        <tr><th>강의대상</th><td>{course.target}</td></tr>
        <tr><th>학년제한</th><td>{course.grade_limit}</td></tr>
        <tr><th>접수기간</th><td>{course.apply_start} {course.start_time} ~ {course.apply_end} 16:00</td></tr>
        <tr><th>강의기간(*)</th><td>{course.event_start} ~ {course.event_end}</td></tr>
        <tr><th>강의시간</th><td>{course.start_time} ~ {course.end_time}</td></tr>
        <tr><th>강의요일</th><td>{course.days}</td></tr>
        <tr><th>현재 참여 / 모집</th><td>{course.capacity_current}명 / {course.capacity_total}명</td></tr>
        <tr><th>현재 대기자 / 대기자</th><td>{course.waitlist_current}명 / {course.waitlist_capacity}명</td></tr>
        <tr><th>강의 설명</th><td>저장하면 안 되는 자유 본문과 054-734-3106</td></tr>
        <tr><th>강사명</th><td>저장하면 안 되는 개인강사</td></tr>
        <tr><th>강의계획서</th><td><a href="download/private-{course.teach_idx}.do">개인강의계획서.hwp</a></td></tr>
      </tbody></table>
      <div class="sbtn">
        <a id="back-btn" href="" class="btn"><span>목록으로</span></a>
        {status_control}
      </div>
    """
    return _owner_shell("평생학습강좌신청 > 상세", body)


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
        unstable: bool = False,
        bad_empty: bool = False,
        detail_title_mismatch: str = "",
        omit_application: str = "",
        response_url_drift: bool = False,
    ):
        self.courses = _courses()
        if duplicate_identity:
            self.courses[-1] = replace(
                self.courses[-1], teach_idx=self.courses[0].teach_idx
            )
        self.overlap_age = overlap_age
        self.partition_drift = partition_drift
        self.unstable = unstable
        self.bad_empty = bad_empty
        self.detail_title_mismatch = detail_title_mismatch
        self.omit_application = omit_application
        self.response_url_drift = response_url_drift
        self.urls: list[str] = []
        self.canonical_calls = 0
        self.lock = Lock()

    def _filter(self, query: dict[str, list[str]]) -> tuple[list[SyntheticCourse], yeongdeok._Filter | None]:
        menu_idx = query.get("menu_idx", [""])[0]
        age = query.get("searchAge", [""])[0]
        group = query.get("group_idx", [""])[0]
        status = query.get("teach_status", [""])[0]
        filter_value: yeongdeok._Filter | None = None
        rows = list(self.courses)
        if age:
            filter_value = next(item for item in yeongdeok.YEONGDEOK_AGE_FILTERS if item.code == age)
            rows = [course for course in rows if course.age == age]
            if self.overlap_age and age == "13":
                rows = list(self.courses)
        elif group:
            filter_value = next(item for item in yeongdeok.YEONGDEOK_GROUP_FILTERS if item.code == group)
            rows = [course for course in rows if course.group_idx == group]
            if self.partition_drift and group == "6" and rows:
                rows[0] = replace(rows[0], title=rows[0].title + " 변경")
        elif status:
            filter_value = next(item for item in yeongdeok.YEONGDEOK_STATUS_FILTERS if item.code == status)
            rows = [course for course in rows if course.status_code == status]
        elif menu_idx != yeongdeok.YEONGDEOK_MENU_IDX:
            raise AssertionError("unexpected menu")
        return rows, filter_value

    def response(self, url: str) -> FakeResponse:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self.lock:
            self.urls.append(url)
        if parsed.path == yeongdeok.YEONGDEOK_LIST_PATH:
            rows, filter_value = self._filter(query)
            is_canonical = filter_value is None
            with self.lock:
                if is_canonical:
                    self.canonical_calls += 1
                canonical_call = self.canonical_calls
            html = _list_html(
                rows,
                filter_value,
                title_suffix=" 변경" if self.unstable and is_canonical and canonical_call >= 2 else "",
                bad_empty=(
                    self.bad_empty
                    and filter_value is not None
                    and filter_value.kind == "group"
                    and filter_value.code == "8"
                ),
            )
            response = FakeResponse(url, html)
        elif parsed.path == yeongdeok.YEONGDEOK_DETAIL_PATH:
            identity = query.get("teach_idx", [""])[0]
            course = next(row for row in self.courses if row.teach_idx == identity)
            response = FakeResponse(
                url,
                _detail_html(
                    course,
                    title_suffix=" 불일치" if identity == self.detail_title_mismatch else "",
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
    return yeongdeok.collect_yeongdeok_education(
        TARGET,
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=_fetch(backend),
        **options,
    )


def test_hashes_exact_target_incumbent_and_owner_boundaries() -> None:
    canonical = yeongdeok.YEONGDEOK_CANONICAL_URL
    assert yeongdeok.YEONGDEOK_PROVIDER == (
        "MUNI_WWW_GBELIB_KR_" + sha1(canonical.encode()).hexdigest()[:8].upper()
    )
    assert yeongdeok.YEONGDEOK_CANONICAL_CANDIDATE_ID == (
        "MUNI_IR_" + sha256(canonical.encode()).hexdigest()[:12].upper()
    )
    assert yeongdeok.YEONGDEOK_CANONICAL_URL_SHA256 == sha256(
        canonical.encode()
    ).hexdigest()
    assert yeongdeok.is_target(TARGET)
    assert not yeongdeok.is_target({**TARGET, "url": canonical + "&group_idx=6"})
    assert not yeongdeok.is_target(
        {**TARGET, "url": canonical.replace("https://", "http://")}
    )
    assert not yeongdeok.is_target({**TARGET, "provider": "MUNI_OTHER"})
    audit = yeongdeok.YEONGDEOK_CANDIDATE_AUDIT
    assert audit[yeongdeok.YEONGDEOK_CANONICAL_CANDIDATE_ID]["url_sha256"] == (
        yeongdeok.YEONGDEOK_CANONICAL_URL_SHA256
    )
    rejected = audit[yeongdeok.YEONGDEOK_REJECTED_CANDIDATE_ID]
    assert "binary_notice_attachment" in rejected["decision"]
    assert rejected["provider"] == "MUNI_WWW_YDCT_ORG_9BB7628B"
    assert {item["url"] for item in yeongdeok.YEONGDEOK_OWNER_BOUNDARIES} >= {
        "https://www.ydct.org/",
        "https://www.yd.go.kr/",
    }


def test_complete_partition_snapshot_controls_privacy_and_post_detail_recheck() -> None:
    backend = SyntheticBackend()
    rows, parser, meta = _collect(backend)
    assert parser == yeongdeok.YEONGDEOK_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["existing_active_owner_count"] == 1
    assert "keep existing provider" in meta["provider_decision"]
    assert meta["source_rows"] == 7
    assert meta["source_identity_count"] == 7
    assert meta["current_source_count"] == 7
    assert meta["expired_source_count"] == 0
    assert meta["source_requests"] == 22
    assert meta["request_attempts"] == 22
    assert meta["list_requests"] == 15
    assert meta["detail_pages"] == 7
    assert meta["boundary_rechecks"] == 1
    assert meta["full_ledger_rechecked_after_details"]
    assert backend.canonical_calls == 2
    assert meta["age_filter_counts"] == {"11": 4, "13": 3}
    assert meta["group_filter_counts"] == {"5": 3, "6": 2, "7": 2, "8": 0}
    assert meta["status_filter_counts"] == {str(index): 1 for index in range(7)}
    assert meta["age_partition_union_count"] == 7
    assert meta["group_partition_union_count"] == 7
    assert meta["status_partition_union_count"] == 7
    assert meta["partition_overlap_count"] == 0
    assert meta["empty_partition_count"] == 1
    assert meta["age_partition_complete"]
    assert meta["group_partition_complete"]
    assert meta["status_partition_complete"]
    assert meta["pagination_complete"]
    assert meta["details_complete"]
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]
    assert len(rows) == 7
    assert Counter(row["status"] for row in rows) == {
        "CLOSED": 4,
        "OPEN": 1,
        "WAITLIST": 1,
        "SCHEDULED": 1,
    }
    assert meta["application_control_count"] == 2
    assert meta["actionable_application_count"] == 2
    assert all(row["description"] == row["title"] for row in rows)
    assert all(row["branch"] == yeongdeok.YEONGDEOK_BRANCH for row in rows)
    assert all(row["branch_url"] == yeongdeok.YEONGDEOK_CANONICAL_URL for row in rows)
    assert all(
        bool(row["application_url"]) == row["reservation_available"]
        for row in rows
    )
    assert all(
        row["application_url"].startswith(
            f"https://{yeongdeok.YEONGDEOK_HOST}{yeongdeok.YEONGDEOK_APPLICATION_PATH}"
        )
        for row in rows
        if row["reservation_available"]
    )
    assert all(
        row["raw_fields"]["application_form_endpoint_fetched"] is False
        and row["raw_fields"]["application_save_endpoint_fetched"] is False
        and row["raw_fields"]["attachment_endpoint_fetched"] is False
        for row in rows
    )
    assert meta["application_endpoints_called"] == 0
    assert meta["application_save_endpoints_called"] == 0
    assert meta["attachment_endpoints_called"] == 0
    assert meta["pii_endpoints_called"] == 0
    payload = repr(rows)
    for forbidden in (
        "저장하면 안 되는 개인강사",
        "저장하면 안 되는 자유 본문",
        "054-734-3106",
        "개인강의계획서.hwp",
        "applicant_name",
        "applicant_phone",
    ):
        assert forbidden not in payload
    requested_paths = Counter(urlparse(url).path for url in backend.urls)
    assert requested_paths == {
        yeongdeok.YEONGDEOK_LIST_PATH: 15,
        yeongdeok.YEONGDEOK_DETAIL_PATH: 7,
    }
    assert yeongdeok.YEONGDEOK_APPLICATION_PATH not in requested_paths
    assert yeongdeok.YEONGDEOK_APPLICATION_SAVE_PATH not in requested_paths
    assert not any("download/" in url for url in backend.urls)


@pytest.mark.parametrize(
    ("backend", "error_fragment"),
    (
        (SyntheticBackend(duplicate_identity=True), "duplicate compound course identity"),
        (SyntheticBackend(overlap_age=True), "age partition 13 overlaps"),
        (SyntheticBackend(partition_drift=True), "group partition 6 changed course"),
        (SyntheticBackend(unstable=True), "stability recheck changed"),
        (SyntheticBackend(bad_empty=True), "empty filter sentinel changed"),
        (
            SyntheticBackend(detail_title_mismatch="91000"),
            "list/detail title drift",
        ),
        (
            SyntheticBackend(omit_application="91000"),
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


def test_limits_and_managed_session_requirement_fail_closed() -> None:
    rows, _, meta = yeongdeok.collect_yeongdeok_education(
        TARGET,
        today="2026-07-23",
    )
    assert rows == []
    assert meta["configured_collection_error"] == "managed session_factory injection is required"

    rows, _, meta = _collect(SyntheticBackend(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"]
    assert "stable recheck" in meta["configured_collection_error"]

    rows, _, meta = _collect(SyntheticBackend(), detail_limit=6)
    assert rows == []
    assert meta["source_cap_reached"]
    assert "detail_limit" in meta["configured_collection_error"]
    assert meta["detail_pages"] == 0


def _live_snapshot():
    return yeongdeok.collect_yeongdeok_education(
        TARGET,
        today="2026-07-23",
        allow_raw_requests_for_tests=True,
        timeout=30,
        max_pages=2,
        detail_limit=50,
    )


@pytest.mark.skipif(
    os.getenv("RUN_YEONGDEOK_LIVE") != "1",
    reason="set RUN_YEONGDEOK_LIVE=1 for two bounded official-source snapshots",
)
def test_live_two_exact_stable_snapshots() -> None:
    first_rows, first_parser, first_meta = _live_snapshot()
    second_rows, second_parser, second_meta = _live_snapshot()
    assert first_parser == second_parser == yeongdeok.YEONGDEOK_PARSER
    assert first_meta["configured_collection_error"] == ""
    assert second_meta["configured_collection_error"] == ""
    assert first_rows == second_rows
    for meta in (first_meta, second_meta):
        assert meta["source_requests"] == 20
        assert meta["request_attempts"] == 20
        assert meta["list_requests"] == 15
        assert meta["detail_pages"] == 5
        assert meta["boundary_rechecks"] == 1
        assert meta["source_rows"] == 5
        assert meta["source_identity_count"] == 5
        assert meta["source_teach_ids"] == ["15719", "15720", "15721", "15722", "15723"]
        assert meta["source_identity_numeric_min"] == 15719
        assert meta["source_identity_numeric_max"] == 15723
        assert meta["current_source_count"] == 5
        assert meta["expired_source_count"] == 0
        assert meta["source_status_counts"] == {
            "수강신청": 2,
            "대기자신청": 2,
            "정원마감": 1,
        }
        assert meta["source_category_counts"] == {
            "여름방학특강 평생교육강좌": 5
        }
        assert meta["age_filter_counts"] == {"11": 5, "13": 0}
        assert meta["group_filter_counts"] == {"5": 0, "6": 5, "7": 0, "8": 0}
        assert meta["status_filter_counts"] == {
            "0": 2,
            "1": 2,
            "2": 0,
            "3": 0,
            "4": 0,
            "5": 1,
            "6": 0,
        }
        assert meta["age_partition_union_count"] == 5
        assert meta["group_partition_union_count"] == 5
        assert meta["status_partition_union_count"] == 5
        assert meta["partition_overlap_count"] == 0
        assert meta["empty_partition_count"] == 8
        assert meta["status_counts"] == {"CLOSED": 1, "WAITLIST": 2, "OPEN": 2}
        assert meta["raw_status_counts"] == {
            "정원마감": 1,
            "대기자신청": 2,
            "수강신청": 2,
        }
        assert meta["application_control_count"] == 4
        assert meta["actionable_application_count"] == 4
        assert meta["application_endpoints_called"] == 0
        assert meta["application_save_endpoints_called"] == 0
        assert meta["attachment_endpoints_called"] == 0
        assert meta["pii_endpoints_called"] == 0
        assert meta["privacy_violations"] == 0
        assert meta["semantic_duplicate_count"] == 0
        assert meta["full_ledger_rechecked_after_details"]
        assert meta["details_complete"]
        assert meta["snapshot_complete"]
    assert first_meta["source_requests"] + second_meta["source_requests"] == 40
    assert {row["provider_course_id"] for row in first_rows} == {
        f"{yeongdeok.YEONGDEOK_PROVIDER}:teach:{identity}"
        for identity in ("15719", "15720", "15721", "15722", "15723")
    }
    expected_capacity = {
        "15719": (14, 14, 5, 5),
        "15720": (14, 14, 0, 5),
        "15721": (14, 14, 2, 5),
        "15722": (12, 14, 0, 5),
        "15723": (9, 14, 0, 5),
    }
    for row in first_rows:
        identity = row["raw_fields"]["identity"]
        assert (
            row["capacity_current"],
            row["capacity_total"],
            row["waitlist_current"],
            row["waitlist_capacity"],
        ) == expected_capacity[identity]
        assert row["start_date"] == "2026-08-04"
        assert row["end_date"] == "2026-08-14"
        assert row["apply_start_date"] == "2026-07-14"
        assert row["apply_end_date"] == "2026-07-29"
        assert bool(row["application_url"]) == row["reservation_available"]
        assert row["raw_fields"]["application_form_endpoint_fetched"] is False
        assert row["raw_fields"]["application_save_endpoint_fetched"] is False
        assert row["raw_fields"]["attachment_endpoint_fetched"] is False
