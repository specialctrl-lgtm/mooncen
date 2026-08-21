from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import os
import re
from threading import Lock
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from Crawler import municipal_gyeongsan as gyeongsan


TOWN_TARGET = {
    "provider": gyeongsan.GYEONGSAN_TOWN_PROVIDER,
    "url": gyeongsan.GYEONGSAN_TOWN_LEGACY_URL,
}
PROGRAM_TARGET = {
    "provider": gyeongsan.GYEONGSAN_PROGRAM_PROVIDER,
    "url": gyeongsan.GYEONGSAN_PROGRAM_CANONICAL_URL,
}
PAGE_SIZE = gyeongsan.GYEONGSAN_PAGE_SIZE


@dataclass(frozen=True)
class SyntheticCourse:
    ledger: str
    edu_no: str
    source_title: str
    event_start: str
    event_end: str
    apply_start: str
    apply_end: str
    schedule: str
    venue: str
    fee: str
    source_status: str
    capacity_current: int | None
    capacity_total: int | None
    wait_current: int | None
    wait_total: int | None
    target: str


def _courses() -> dict[str, list[SyntheticCourse]]:
    result: dict[str, list[SyntheticCourse]] = {"town": [], "program": [], "women": []}
    town_branches = ("중앙동", "진량읍", "서부1동", "하양읍", "자인면")
    for index in range(17):
        current = index < 5
        branch = town_branches[index % len(town_branches)]
        result["town"].append(
            SyntheticCourse(
                "town",
                str(9100 - index),
                f"[{branch}] 합성 읍면동 강좌 {index + 1}",
                "2026-08-01" if current else "2026-01-02",
                "2026-09-30" if current else "2026-07-22",
                "2026-07-25" if current else "2025-12-01",
                "2026-07-31" if current else "2025-12-20",
                "화(10:00~12:00)" if current else "",
                f"{branch} 학습관 {index + 1}실" if current else "",
                "20,000원 (※교재비, 재료비 별도)",
                "접수대기" if current else "신청마감",
                0 if current else None,
                10 if current else None,
                0 if current else None,
                0 if current else None,
                "경산시민",
            )
        )
    for index in range(15):
        current = index < 4
        status = ("접수대기", "접수중", "신청마감", "신청마감")[index] if current else "신청마감"
        learning_place = " [배움터 : 합성공방]" if index == 0 else ""
        declared = index != 3
        result["program"].append(
            SyntheticCourse(
                "program",
                str(9200 - index),
                f"합성 평생학습 강좌 {index + 1}{learning_place}",
                "2026-08-04" if current else "2025-01-02",
                "2026-09-10" if current else "2026-07-22",
                "2026-07-20" if current else "2024-12-01",
                "2026-07-31" if current else "2024-12-20",
                "매주 화요일(14:00~16:00)" if current else "",
                f"합성교육장 {index + 1}" if current else "",
                "무료 (※교재비, 재료비 별도)",
                status,
                index if current and declared else None,
                15 if current and declared else None,
                0 if current and declared else None,
                5 if current and declared else None,
                "경산시민",
            )
        )
    for index in range(3):
        result["women"].append(
            SyntheticCourse(
                "women",
                str(9300 - index),
                f"[야간]합성 여성회관 강좌 {index + 1}",
                "2026-08-18",
                "2026-12-07",
                "2026-08-11",
                "2026-08-14",
                "목(19:00~21:00)",
                f"제{index + 1}강의실",
                "20,000원 (※교재비, 재료비 별도)",
                "접수대기",
                0,
                10 + index,
                0,
                0,
                "경산시민",
            )
        )
    return result


def _ledger(key: str) -> gyeongsan.GyeongsanLedger:
    return {
        "town": gyeongsan.GYEONGSAN_TOWN_LEDGER,
        "program": gyeongsan.GYEONGSAN_PROGRAM_LEDGER,
        "women": gyeongsan.GYEONGSAN_WOMEN_LEDGER,
    }[key]


def _filters(key: str) -> str:
    if key == "town":
        values = (
            ("fnSearchRgnCode", code, label, code == "")
            for code, label in gyeongsan.GYEONGSAN_TOWN_REGIONS
        )
    elif key == "program":
        values = (
            ("fnSearchLlPrgrm", code, label, False)
            for code, label in gyeongsan.GYEONGSAN_PROGRAM_FILTERS
        )
    else:
        values = (
            ("fnSearchCategory", code, label, False)
            for code, label in gyeongsan.GYEONGSAN_WOMEN_FILTERS
        )
    return "".join(
        f'<li class="{"active" if active else ""}">'
        f'<a href="javascript:{function}(\'{code}\');">{label}</a></li>'
        for function, code, label, active in values
    )


def _form(key: str, page: int, *, registry_drift: bool = False) -> str:
    ledger = _ledger(key)
    filters = _filters(key)
    if registry_drift and key == "town":
        filters = filters.replace(
            '<li class=""><a href="javascript:fnSearchRgnCode(\'CTI0020007\');">중방동</a></li>',
            "",
        )
    return f"""
      <form id="form1" name="form1" method="post" action="">
        <input type="hidden" name="mn" value="{ledger.mn}">
        <input type="hidden" name="pageIndex" value="{page}">
        <input type="hidden" name="pageNo" value="{ledger.page_no}">
        <input type="hidden" name="paramIdx" value="">
        <input type="hidden" name="eduNo" value="-1">
        <input type="hidden" name="searchInstNo" value="{ledger.search_inst_no}">
        <input type="hidden" name="srchCtgryCd" value="{ledger.category}">
        <input type="hidden" name="srchLlPrgrmCd" value="">
        <input type="hidden" name="srchRgnCd" value="">
        <ul class="com_tab">{filters}</ul>
        <div class="search_txt"><div>전체 <span class="num">{{TOTAL}}</span>건</div>
          <div>페이지 <span class="num">{page}</span></div></div>
        <input type="text" id="srchEduNm" name="srchEduNm" value="">
        <div class="edu_content">{{CARDS}}</div>
        {{PAGER}}
      </form>
    """


def _status_contract(source_status: str) -> tuple[str, str]:
    return {
        "접수대기": ("acceptable", "receipt_gray"),
        "접수중": ("acceptable", "receipt_green"),
        "신청마감": ("", "receipt_dark"),
    }[source_status]


def _card(course: SyntheticCourse, ordinal: int) -> str:
    link_class, status_class = _status_contract(course.source_status)
    capacity = (
        "신청/정원 : -"
        if course.capacity_total is None
        else f"신청/정원 : {course.capacity_current} /{course.capacity_total}명, "
        f"후보/정원 : {course.wait_current} /{course.wait_total}명"
    )
    return f"""
      <ul class="content_list" id="data{ordinal}">
        <a href="javascript:;" class="{link_class}" onclick="fnDetail('{course.edu_no}');">
          <li>{course.source_title}</li>
          <li><ul class="list_item bf_img1">
            <li>수강기간 : {course.event_start} ~ {course.event_end}</li>
            <li>수강시간 : {course.schedule}</li>
            <li>접수기간 : {course.apply_start} ~ {course.apply_end}</li>
          </ul></li>
          <li><ul class="list_item bf_img2"><li>{capacity}</li></ul></li>
          <li>교육장소 : {course.venue}</li>
          <li>수강료 : {course.fee}</li>
        </a>
      </ul><li class="{status_class}">{course.source_status}</li>
    """


def _pager(page: int, last: int, *, has_rows: bool) -> str:
    active = (
        f'<a href="javascript:;" class="active" title="현재페이지{page}">{page}</a>'
        if has_rows
        else ""
    )
    links = "".join(
        f'<a href="#n" onclick="pageMove({value}); return false;">{value}</a>'
        for value in sorted({1, last})
    )
    return f'<div class="pagenation">{active}{links}</div>'


def _shell(key: str, body: str) -> str:
    ledger = _ledger(key)
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
      <title>경산시 평생학습관 &gt; 교육신청 &gt; {ledger.menu_name}</title></head>
      <body><h1>경산시 평생학습관</h1>{body}
      <div id="fnb">[38617] 경산시 남매로 159, 경산시청 2별관 1층 교육도시과
      Copyright©2022 GYEONGSAN LIFELONG LEARNING CENTER. All right reserved.</div>
      </body></html>
    """


def _list_html(
    key: str,
    rows: list[SyntheticCourse],
    *,
    page: int,
    last: int,
    total: int,
    registry_drift: bool = False,
) -> str:
    form = _form(key, page, registry_drift=registry_drift)
    if rows:
        cards = "".join(_card(row, total - index) for index, row in enumerate(rows))
    else:
        cards = (
            '<ul class="content_list"><a href="javascript:;" '
            "onclick=\"alert('등록된 자료가 없습니다.')\">자료가 없습니다.</a></ul>"
        )
    form = form.replace("{TOTAL}", str(total)).replace("{CARDS}", cards)
    form = form.replace("{PAGER}", _pager(page, last, has_rows=bool(rows)))
    return _shell(key, form)


def _detail_title(course: SyntheticCourse) -> str:
    return re.sub(r"^\[[^\]]+\]\s*", "", course.source_title) if course.ledger == "town" else course.source_title


def _detail_html(
    course: SyntheticCourse,
    page: int,
    *,
    title_drift: bool = False,
    identity_drift: bool = False,
    venue_drift: bool = False,
) -> str:
    ledger = _ledger(course.ledger)
    title = _detail_title(course) + (" 변경" if title_drift else "")
    venue = course.venue + (" 변경" if venue_drift else "")
    counts = ""
    if course.capacity_total is not None:
        counts = f"""
          <div class="com3"><p>신청현황</p><span>{course.capacity_current}명 /{course.capacity_total}명 (신청/정원)</span></div>
          <div class="com3"><p>후보현황</p><span>{course.wait_current}명 /{course.wait_total}명 (후보/정원)</span></div>
        """
    controls = (
        '<a class="com_btn button2 bg_green" href="javascript:;" '
        'onclick="fnRequest()">신청하기</a>'
        if course.source_status == "접수중"
        else ""
    )
    body = f"""
      <form id="form1" name="form1" method="post" action="">
        <input type="hidden" name="mn" value="{ledger.mn}">
        <input type="hidden" name="pageIndex" value="{page}">
        <input type="hidden" name="pageNo" value="{ledger.page_no}">
        <input type="hidden" name="paramIdx" value="">
        <input type="hidden" name="searchInstNo" value="{ledger.search_inst_no}">
        <input type="hidden" name="srchCtgryCd" value="{ledger.category}">
        <input type="hidden" name="srchLlPrgrmCd" value="">
        <input type="hidden" name="srchRgnCd" value="">
        <input type="hidden" name="srchEduNm" value="">
        <div class="img_jb"><div class="right">
          <div class="g_name">{title}</div>
          <div class="com3"><p>신청일정</p><span>{course.apply_start} (09:00) ~ {course.apply_end} (18:00)</span></div>
          <div class="com3"><p>교육일정</p><span>{course.event_start} (10:00) ~ {course.event_end} (12:00)</span></div>
          {counts}
          <div class="com3"><p>교육장소</p><span>{venue}</span></div>
          <div class="com3"><p>수 강 료</p><span>{course.fee.split(' (', 1)[0]}</span></div>
          <div class="com3"><p>{course.target}</p></div>
        </div></div>
        <div class="text_detail"><table class="com_table"><tbody>
          <tr><th>교육대상</th><td>{course.target}</td></tr>
          <tr><th>교육방법</th><td>저장하지 않는 교육 자유본문</td></tr>
          <tr><th>유의사항</th><td>private@example.test / 저장 금지</td></tr>
          <tr><th>수강시간</th><td>{course.schedule}</td></tr>
          <tr><th>강의계획서</th><td><a class="file_b" href="/lll/jfile/readFile.tc?fileId=PRIVATE">개인신청서.pdf</a></td></tr>
          <tr><th>기타 금액안내</th><td>000은행 123-456-789</td></tr>
          <tr><th>담당자명</th><td>홍길동</td></tr>
          <tr><th>문의처</th><td>053-000-0000</td></tr>
        </tbody></table></div>
        <div class="bot_btn">{controls}<a class="com_btn button2 bg_gray" href="javascript:;" onclick="selectList()">목록으로</a></div>
      </form>
      <script>function fnRequest() {{ var param = "?eduNo={'999999' if identity_drift else course.edu_no}"; }}</script>
    """
    return _shell(course.ledger, body)


class FakeResponse:
    def __init__(self, url: str, html: str):
        self.url = url
        self.content = html.encode("utf-8")
        self.text = html
        self.status_code = 200
        self.history: list[object] = []
        self.headers = {"Content-Type": "text/html;charset=UTF-8"}

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SyntheticBackend:
    def __init__(
        self,
        *,
        unstable_ledger: str = "",
        nonempty_sentinel: str = "",
        total_drift: str = "",
        registry_drift: str = "",
        duplicate_global_identity: bool = False,
        detail_title_drift: str = "",
        detail_identity_drift: str = "",
        detail_venue_drift: str = "",
        response_url_drift: bool = False,
    ) -> None:
        self.rows = _courses()
        if duplicate_global_identity:
            self.rows["women"][0] = replace(
                self.rows["women"][0], edu_no=self.rows["program"][0].edu_no
            )
        self.unstable_ledger = unstable_ledger
        self.nonempty_sentinel = nonempty_sentinel
        self.total_drift = total_drift
        self.registry_drift = registry_drift
        self.detail_title_drift = detail_title_drift
        self.detail_identity_drift = detail_identity_drift
        self.detail_venue_drift = detail_venue_drift
        self.response_url_drift = response_url_drift
        self.urls: list[str] = []
        self.calls: Counter[tuple[str, int]] = Counter()
        self.lock = Lock()

    @staticmethod
    def _key(parsed: Any) -> str:
        return {
            "/lll/page/2391/1649.tc": "town",
            "/lll/page/2400/1604.tc": "program",
            "/lll/page/2399/1650.tc": "women",
        }[parsed.path]

    def response(self, url: str) -> FakeResponse:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        with self.lock:
            self.urls.append(url)
        if parsed.path == "/lll/edu/detail.tc":
            identity = query["eduNo"][0]
            course = next(row for values in self.rows.values() for row in values if row.edu_no == identity)
            page = int(query["pageIndex"][0])
            response = FakeResponse(
                url,
                _detail_html(
                    course,
                    page,
                    title_drift=identity == self.detail_title_drift,
                    identity_drift=identity == self.detail_identity_drift,
                    venue_drift=identity == self.detail_venue_drift,
                ),
            )
        else:
            key = self._key(parsed)
            page = int(query["pageIndex"][0])
            with self.lock:
                self.calls[(key, page)] += 1
                call = self.calls[(key, page)]
            all_rows = list(self.rows[key])
            if self.unstable_ledger == key and page == 1 and call >= 2:
                all_rows[0] = replace(all_rows[0], source_title=all_rows[0].source_title + " 변경")
            last = max(1, (len(all_rows) + PAGE_SIZE - 1) // PAGE_SIZE)
            rows = all_rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
            if self.nonempty_sentinel == key and page == last + 1:
                rows = [all_rows[-1]]
            total = len(all_rows) + (1 if self.total_drift == key else 0)
            response = FakeResponse(
                url,
                _list_html(
                    key,
                    rows,
                    page=page,
                    last=last,
                    total=total,
                    registry_drift=self.registry_drift == key,
                ),
            )
        if self.response_url_drift:
            response.url = response.url.replace("www.gbgs.go.kr", "gbgs.go.kr")
        return response


def _fetch(backend: SyntheticBackend):
    def fetcher(_session: object, url: str, _timeout: int) -> FakeResponse:
        return backend.response(url)

    return fetcher


def _collect(target: Mapping[str, str], backend: SyntheticBackend, **options: Any):
    return gyeongsan.collect_gyeongsan_education(
        target,
        today="2026-07-23",
        session_factory=FakeSession,
        fetcher=_fetch(backend),
        detail_workers=4,
        **options,
    )


def _assert_atomic(meta: Mapping[str, Any]) -> None:
    assert meta["configured_collection_error"]
    assert not meta["snapshot_complete"]
    assert not meta["full_snapshot_validated"]


def test_exact_owner_aliases_canonicals_and_duplicate_exclusion() -> None:
    assert gyeongsan.is_gyeongsan_education_target(TOWN_TARGET)
    assert gyeongsan.is_gyeongsan_education_target(
        {**TOWN_TARGET, "url": gyeongsan.GYEONGSAN_TOWN_BARE_URL}
    )
    assert gyeongsan.is_gyeongsan_education_target(PROGRAM_TARGET)
    assert gyeongsan.is_gyeongsan_education_target(
        {**PROGRAM_TARGET, "url": gyeongsan.GYEONGSAN_WOMEN_CANDIDATE_URL}
    )
    assert not gyeongsan.is_gyeongsan_education_target(
        {
            "provider": gyeongsan.GYEONGSAN_DUPLICATE_TOWN_PROVIDER,
            "url": gyeongsan.GYEONGSAN_TOWN_CANONICAL_URL,
        }
    )
    assert not gyeongsan.is_gyeongsan_education_target(
        {**TOWN_TARGET, "url": gyeongsan.GYEONGSAN_TOWN_BARE_URL + "?pageIndex=17"}
    )
    assert not gyeongsan.is_gyeongsan_education_target(
        {**PROGRAM_TARGET, "url": gyeongsan.GYEONGSAN_PROGRAM_CANONICAL_URL + "&srchEduNm=x"}
    )
    assert [ledger.key for ledger in gyeongsan._owned_ledgers(gyeongsan.GYEONGSAN_TOWN_PROVIDER)] == ["town"]
    assert [ledger.key for ledger in gyeongsan._owned_ledgers(gyeongsan.GYEONGSAN_PROGRAM_PROVIDER)] == ["program", "women"]


def test_complete_owned_ledgers_are_disjoint_detailed_and_private() -> None:
    town_backend = SyntheticBackend()
    town_rows, parser, town_meta = _collect(TOWN_TARGET, town_backend)
    program_backend = SyntheticBackend()
    program_rows, parser2, program_meta = _collect(PROGRAM_TARGET, program_backend)
    assert parser == parser2 == gyeongsan.GYEONGSAN_PARSER
    assert town_meta["configured_collection_error"] == ""
    assert program_meta["configured_collection_error"] == ""
    assert town_meta["ledger_source_counts"] == {"town": 17}
    assert town_meta["ledger_current_counts"] == {"town": 5}
    assert town_meta["ledger_pages"] == {"town": 2}
    assert town_meta["ledger_post_last_pages"] == {"town": 3}
    assert town_meta["ledger_first_identities"] == {"town": "9100"}
    assert town_meta["ledger_final_identities"] == {"town": "9084"}
    assert town_meta["ledger_final_page_counts"] == {"town": 2}
    assert town_meta["list_requests"] == 6
    assert town_meta["post_last_requests"] == 2
    assert town_meta["detail_pages"] == 5
    assert town_meta["source_requests"] == town_meta["request_attempts"] == 11
    assert len(town_rows) == town_meta["returned_count"] == 5
    assert program_meta["ledger_source_counts"] == {"program": 15, "women": 3}
    assert program_meta["ledger_current_counts"] == {"program": 4, "women": 3}
    assert program_meta["ledger_pages"] == {"program": 1, "women": 1}
    assert program_meta["ledger_post_last_pages"] == {"program": 2, "women": 2}
    assert program_meta["ledger_first_identities"] == {"program": "9200", "women": "9300"}
    assert program_meta["ledger_final_identities"] == {"program": "9186", "women": "9298"}
    assert program_meta["ledger_final_page_counts"] == {"program": 15, "women": 3}
    assert program_meta["list_requests"] == 8
    assert program_meta["post_last_requests"] == 4
    assert program_meta["detail_pages"] == 7
    assert program_meta["source_requests"] == program_meta["request_attempts"] == 15
    assert len(program_rows) == program_meta["returned_count"] == 7
    town_ids = {row["raw_fields"]["identity"] for row in town_rows}
    program_ids = {row["raw_fields"]["identity"] for row in program_rows}
    assert town_ids.isdisjoint(program_ids)
    assert town_meta["global_identity_disjoint"]
    assert program_meta["global_identity_disjoint"]
    assert town_meta["status_counts"] == {"SCHEDULED": 5}
    assert program_meta["status_counts"] == {"CLOSED": 2, "OPEN": 1, "SCHEDULED": 4}
    assert town_meta["application_control_count"] == 0
    assert program_meta["application_control_count"] == 1
    assert town_meta["branch_counts"] == {
        "자인면": 1,
        "서부1동": 1,
        "진량읍": 1,
        "하양읍": 1,
        "중앙동": 1,
    }
    assert program_meta["branch_counts"] == {
        "합성공방": 1,
        "경산시 평생학습관": 3,
        "경산시 여성회관": 3,
    }
    assert all(row["provider"] == gyeongsan.GYEONGSAN_TOWN_PROVIDER for row in town_rows)
    assert all(row["provider"] == gyeongsan.GYEONGSAN_PROGRAM_PROVIDER for row in program_rows)
    assert all(row["description"] == row["title"] for row in town_rows + program_rows)
    assert all(bool(row["application_url"]) == row["reservation_available"] for row in town_rows + program_rows)
    payload = repr(town_rows + program_rows)
    for forbidden in (
        "private@example.test",
        "053-000-0000",
        "홍길동",
        "000은행",
        "개인신청서.pdf",
        "/lll/jfile/readFile.tc",
        "/lll/edu/request.tc",
    ):
        assert forbidden not in payload
    for meta in (town_meta, program_meta):
        assert meta["application_endpoints_called"] == 0
        assert meta["attachment_endpoints_called"] == 0
        assert meta["instructor_endpoints_called"] == 0
        assert meta["pii_endpoints_called"] == 0
        assert meta["privacy_violations"] == 0
        assert meta["pagination_complete"]
        assert meta["post_last_complete"]
        assert meta["details_complete"]
        assert meta["full_ledgers_rechecked_after_details"]
        assert meta["snapshot_complete"]
        assert meta["full_snapshot_validated"]
    requested = Counter(urlparse(url).path for url in town_backend.urls + program_backend.urls)
    assert requested == {
        "/lll/page/2391/1649.tc": 6,
        "/lll/page/2400/1604.tc": 4,
        "/lll/page/2399/1650.tc": 4,
        "/lll/edu/detail.tc": 12,
    }


@pytest.mark.parametrize("ledger,target", (("town", TOWN_TARGET), ("program", PROGRAM_TARGET), ("women", PROGRAM_TARGET)))
def test_full_recheck_drift_is_atomic(ledger: str, target: Mapping[str, str]) -> None:
    rows, _, meta = _collect(target, SyntheticBackend(unstable_ledger=ledger))
    assert rows == []
    _assert_atomic(meta)


@pytest.mark.parametrize("ledger,target", (("town", TOWN_TARGET), ("program", PROGRAM_TARGET), ("women", PROGRAM_TARGET)))
def test_nonempty_post_last_is_atomic(ledger: str, target: Mapping[str, str]) -> None:
    rows, _, meta = _collect(target, SyntheticBackend(nonempty_sentinel=ledger))
    assert rows == []
    _assert_atomic(meta)


@pytest.mark.parametrize(
    "options",
    (
        {"registry_drift": "town"},
        {"total_drift": "program"},
        {"duplicate_global_identity": True},
        {"response_url_drift": True},
    ),
)
def test_registry_total_global_identity_and_response_url_drift_fail_closed(
    options: Mapping[str, Any],
) -> None:
    target = TOWN_TARGET if options.get("registry_drift") == "town" else PROGRAM_TARGET
    rows, _, meta = _collect(target, SyntheticBackend(**options))
    assert rows == []
    _assert_atomic(meta)


@pytest.mark.parametrize(
    "option,identity",
    (
        ("detail_title_drift", "9100"),
        ("detail_identity_drift", "9200"),
        ("detail_venue_drift", "9300"),
    ),
)
def test_detail_title_identity_and_venue_binding_is_atomic(option: str, identity: str) -> None:
    target = TOWN_TARGET if identity.startswith("91") else PROGRAM_TARGET
    rows, _, meta = _collect(target, SyntheticBackend(**{option: identity}))
    assert rows == []
    _assert_atomic(meta)


def test_capacity_full_closed_course_can_retain_detail_link_class() -> None:
    soup = gyeongsan.BeautifulSoup(
        """
        <ul class="content_list">
          <a class="acceptable" href="javascript:;" onclick="fnDetail('10736');"></a>
        </ul>
        <li class="receipt_dark">신청마감</li>
        """,
        "html.parser",
    )
    card = soup.select_one("ul.content_list")
    assert card is not None
    link = card.find("a", recursive=False)
    assert link is not None
    assert gyeongsan._parse_status(card, link, "10736") == (
        "신청마감",
        "CLOSED",
        False,
    )

    link["class"] = ["bg_green"]
    with pytest.raises(gyeongsan.GyeongsanContractError, match="status control"):
        gyeongsan._parse_status(card, link, "10736")


def test_managed_session_limits_and_dedupe_cardinality_fail_closed() -> None:
    rows, _, meta = gyeongsan.collect_gyeongsan_education(TOWN_TARGET, today="2026-07-23")
    assert rows == []
    assert "session" in meta["configured_collection_error"]
    assert meta["source_requests"] == 0

    rows, _, meta = _collect(TOWN_TARGET, SyntheticBackend(), max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"]
    _assert_atomic(meta)

    rows, _, meta = _collect(TOWN_TARGET, SyntheticBackend(), detail_limit=4)
    assert rows == []
    assert meta["source_cap_reached"]
    assert meta["detail_pages"] == 0
    _assert_atomic(meta)

    rows, _, meta = _collect(
        PROGRAM_TARGET,
        SyntheticBackend(),
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    _assert_atomic(meta)


class RecordingSession:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.headers = self.session.headers
        self.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            }
        )
        self.urls: list[str] = []
        self.lock = Lock()

    def get(self, url: str, **kwargs: Any):
        with self.lock:
            self.urls.append(url)
        return self.session.get(url, **kwargs)

    def close(self) -> None:
        self.session.close()


def _live_one(target: Mapping[str, str]):
    tracker = RecordingSession()
    rows, parser, meta = gyeongsan.collect_gyeongsan_education(
        target,
        today="2026-07-23",
        timeout=30,
        max_pages=40,
        detail_limit=250,
        detail_workers=16,
        session_factory=lambda: tracker,
    )
    return rows, parser, meta, tracker.urls


def _live_snapshot():
    return _live_one(TOWN_TARGET), _live_one(PROGRAM_TARGET)


@pytest.mark.skipif(
    os.getenv("RUN_GYEONGSAN_LIVE") != "1",
    reason="set RUN_GYEONGSAN_LIVE=1 for two complete official-source snapshots",
)
def test_live_two_complete_snapshots_are_exact_stable_disjoint_and_private() -> None:
    first = _live_snapshot()
    second = _live_snapshot()
    assert first[0][0] == second[0][0]
    assert first[1][0] == second[1][0]
    baseline = gyeongsan.GYEONGSAN_LIVE_AUDIT_BASELINE
    for snapshot in (first, second):
        (town_rows, town_parser, town_meta, town_urls), (
            program_rows,
            program_parser,
            program_meta,
            program_urls,
        ) = snapshot
        assert town_parser == program_parser == gyeongsan.GYEONGSAN_PARSER
        assert town_meta["configured_collection_error"] == ""
        assert program_meta["configured_collection_error"] == ""
        assert town_meta["ledger_source_counts"] == {"town": 432}
        assert program_meta["ledger_source_counts"] == {"program": 105, "women": 110}
        assert town_meta["ledger_pages"] == {"town": 29}
        assert program_meta["ledger_pages"] == {"program": 7, "women": 8}
        assert town_meta["ledger_post_last_pages"] == {"town": 30}
        assert program_meta["ledger_post_last_pages"] == {"program": 8, "women": 9}
        assert town_meta["ledger_current_counts"] == {"town": 203}
        assert program_meta["ledger_current_counts"] == {"program": 46, "women": 55}
        assert town_meta["ledger_first_identities"] == {"town": "10837"}
        assert program_meta["ledger_first_identities"] == {"program": "10757", "women": "10664"}
        assert town_meta["ledger_final_identities"] == {"town": "10023"}
        assert program_meta["ledger_final_identities"] == {"program": "5990", "women": "10398"}
        assert town_meta["ledger_final_page_counts"] == {"town": 12}
        assert program_meta["ledger_final_page_counts"] == {"program": 15, "women": 5}
        assert town_meta["ledger_identity_sha256"] == {"town": baseline["full_identity_sha256"]["town"]}
        assert program_meta["ledger_identity_sha256"] == {
            "program": baseline["full_identity_sha256"]["program"],
            "women": baseline["full_identity_sha256"]["women"],
        }
        assert town_meta["ledger_current_identity_sha256"] == {"town": baseline["current_identity_sha256"]["town"]}
        assert program_meta["ledger_current_identity_sha256"] == {
            "program": baseline["current_identity_sha256"]["program"],
            "women": baseline["current_identity_sha256"]["women"],
        }
        assert len(town_rows) == 203
        assert len(program_rows) == 101
        town_ids = {row["raw_fields"]["identity"] for row in town_rows}
        program_ids = {row["raw_fields"]["identity"] for row in program_rows}
        assert town_ids.isdisjoint(program_ids)
        assert Counter(row["status"] for row in town_rows + program_rows) == baseline["status_counts"]
        assert town_meta["branch_counts"] == baseline["town_branch_counts"]
        expected_program_branches = Counter(baseline["program_branch_counts"])
        expected_program_branches.update(baseline["women_branch_counts"])
        assert program_meta["branch_counts"] == dict(expected_program_branches)
        assert town_meta["application_control_count"] == 0
        assert program_meta["application_control_count"] == 3
        assert town_meta["source_requests"] == town_meta["request_attempts"] == 263
        assert town_meta["list_requests"] == 60
        assert town_meta["detail_pages"] == 203
        assert town_meta["post_last_requests"] == 2
        assert program_meta["source_requests"] == program_meta["request_attempts"] == 135
        assert program_meta["list_requests"] == 34
        assert program_meta["detail_pages"] == 101
        assert program_meta["post_last_requests"] == 4
        assert len(town_urls) + len(program_urls) == 398
        paths = Counter(urlparse(url).path for url in town_urls + program_urls)
        assert paths == {
            "/lll/page/2391/1649.tc": 60,
            "/lll/page/2400/1604.tc": 16,
            "/lll/page/2399/1650.tc": 18,
            "/lll/edu/detail.tc": 304,
        }
        assert not any(
            forbidden in url
            for url in town_urls + program_urls
            for forbidden in (
                "/lll/edu/request.tc",
                "/lll/edu/instr/detail.json",
                "/lll/jfile/readFile.tc",
                "/lll/login/",
            )
        )
        for meta in (town_meta, program_meta):
            assert meta["application_endpoints_called"] == 0
            assert meta["attachment_endpoints_called"] == 0
            assert meta["instructor_endpoints_called"] == 0
            assert meta["pii_endpoints_called"] == 0
            assert meta["privacy_violations"] == 0
            assert meta["snapshot_complete"]
            assert meta["full_snapshot_validated"]
    total_requests = sum(len(part[3]) for snapshot in (first, second) for part in snapshot)
    assert total_requests == baseline["two_snapshot_requests"] == 796
