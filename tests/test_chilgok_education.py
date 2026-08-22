from __future__ import annotations

from collections import Counter
import html
import math
import os
from typing import Any, Mapping
from urllib.parse import urlparse

import pytest

from Crawler import municipal_chilgok as chilgok


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FixturePoster:
    def __init__(
        self,
        pages: Mapping[int, str | list[str]],
        details: Mapping[str, str | list[str]],
    ) -> None:
        self.pages = dict(pages)
        self.details = dict(details)
        self.offsets: Counter[tuple[str, str]] = Counter()
        self.calls: list[tuple[str, dict[str, str]]] = []

    @staticmethod
    def _value(value: str | list[str], offset: int) -> str:
        if isinstance(value, list):
            return value[min(offset, len(value) - 1)]
        return value

    def __call__(
        self,
        _session: Any,
        url: str,
        data: Mapping[str, str],
        _timeout: int,
    ) -> str:
        payload = dict(data)
        self.calls.append((url, payload))
        path = urlparse(url).path
        if path == chilgok.CHILGOK_LIST_PATH:
            page = int(payload["page"])
            if page not in self.pages:
                raise AssertionError(f"unexpected list page {page}")
            key = ("page", str(page))
            offset = self.offsets[key]
            self.offsets[key] += 1
            return self._value(self.pages[page], offset)
        if path == chilgok.CHILGOK_DETAIL_PATH:
            identity = payload["idx"]
            if identity not in self.details:
                raise AssertionError(f"unexpected detail idx {identity}")
            key = ("detail", identity)
            offset = self.offsets[key]
            self.offsets[key] += 1
            return self._value(self.details[identity], offset)
        raise AssertionError(f"unsafe/unexpected endpoint {url}")


def _target(**changes: str) -> dict[str, str]:
    target = {
        "provider": chilgok.CHILGOK_PROVIDER,
        "url": chilgok.CHILGOK_CANONICAL_URL,
    }
    target.update(changes)
    return target


def _agency_controls(
    agencies: Mapping[str, str] = chilgok.CHILGOK_OFFICIAL_AGENCIES,
) -> str:
    controls = [
        '<input type="checkbox" id="searchAgencyAll" name="searchAgencyAll">'
        '<label for="searchAgencyAll">전체</label>'
    ]
    for identity, name in agencies.items():
        controls.append(
            f'<input type="checkbox" id="searchAgency{identity}" '
            f'name="searchAgency" value="{identity}">'
            f'<label for="searchAgency{identity}">{html.escape(name)}</label>'
        )
    controls.extend(
        [
            '<input type="checkbox" id="searchGroupAll" name="searchGroupAll">'
            '<label for="searchGroupAll">전체</label>',
            '<input type="text" id="searchTxt" name="searchTxt" value="">',
            '<input type="checkbox" id="searchReceiptIng" '
            'name="searchReceiptIng" value="Y">',
        ]
    )
    return "".join(controls)


def _course(
    identity: str,
    sequence: int,
    *,
    branch: str = "청소년문화의집",
    title: str | None = None,
    fee: str = "무료",
    period: str = "2099-08-04 ~ 2099-08-14",
    schedule: str = "10:00~11:30 화요일 수요일",
    capacity_current: int = 8,
    capacity_total: int = 10,
    waitlist_current: int = 0,
    waitlist_total: int = 2,
    status: str = "접수마감",
    control_identity: str | None = None,
    force_control: bool = False,
) -> dict[str, Any]:
    return {
        "identity": identity,
        "sequence": sequence,
        "branch": branch,
        "title": title or f"강좌 {identity}",
        "fee": fee,
        "period": period,
        "schedule": schedule,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_current": waitlist_current,
        "waitlist_total": waitlist_total,
        "status": status,
        "control_identity": control_identity,
        "force_control": force_control,
    }


def _row(course: Mapping[str, Any]) -> str:
    identity = str(course["identity"])
    status = str(course["status"])
    show_control = status == "접수중" or bool(course.get("force_control"))
    if show_control:
        control_identity = str(course.get("control_identity") or identity)
        state = (
            f'<a href="#n" onclick="fn_apply(\'{control_identity}\', '
            f"'receiptIng');\">{html.escape(status)}</a>"
        )
    else:
        state = f"<span>{html.escape(status)}</span>"
    return f"""
      <tr>
        <td class="list_num" data-title="번호">{course['sequence']}</td>
        <td class="list_pub" data-title="기관명">{html.escape(str(course['branch']))}</td>
        <td class="list_tit" data-title="강좌명"><a href="#n"
          onclick="fn_view('{identity}');"><em>[분류]</em> {html.escape(str(course['title']))}</a></td>
        <td class="list_money" data-title="수강료">{html.escape(str(course['fee']))}</td>
        <td class="list_dudate" data-title="교육기간">{course['period']}</td>
        <td class="list_dutime" data-title="교육시간">{html.escape(str(course['schedule']))}</td>
        <td class="list_user" data-title="접수/정원(대기/정원)">
          <em>{course['capacity_current']}</em> / {course['capacity_total']}
          ({course['waitlist_current']} / {course['waitlist_total']})</td>
        <td class="list_state" data-title="접수현황">{state}</td>
      </tr>
    """


def _list_page(
    page: int,
    total: int,
    courses: list[Mapping[str, Any]],
    *,
    agencies: Mapping[str, str] = chilgok.CHILGOK_OFFICIAL_AGENCIES,
    total_override: int | None = None,
) -> str:
    advertised_total = total if total_override is None else total_override
    last = max(1, math.ceil(advertised_total / chilgok.CHILGOK_PAGE_SIZE))
    pagination = (
        f'<div class="bod_page"><a class="btn_end" href="#" '
        f'onclick="goPage({last}); return false;">끝 페이지</a></div>'
        if page <= last
        else '<div class="bod_page"></div>'
    )
    rows = "".join(_row(course) for course in courses)
    if not rows:
        rows = "<!-- old sample rows are comments and are not live records -->"
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
        <title>교육신청 | 통합예약</title></head><body>
        <form id="listForm" name="listForm" method="post"
          action="/reservation/edu/courseList.do?mId=">
          <input type="hidden" id="page" name="page" value="{page}">
          <input type="hidden" id="idx" name="idx" value="0">
          <input type="hidden" id="applyGubun" name="applyGubun" value="">
          {_agency_controls(agencies)}
        </form>
        <div class="bod_result">검색결과 총 <em>{advertised_total}</em> 건</div>
        <table class="bod_list edu">
          <caption>{chilgok._LIST_CAPTION}</caption>
          <thead><tr>
            <th class="list_num">번호</th><th class="list_pub">기관명</th>
            <th class="list_tit">강좌명</th><th class="list_money">수강료</th>
            <th class="list_dudate">교육기간</th><th class="list_dutime">교육시간</th>
            <th class="list_user">접수/정원<br>(대기/정원)</th>
            <th class="list_state">접수현황</th>
          </tr></thead><tbody>{rows}</tbody>
        </table>{pagination}
      </body></html>
    """


def _detail(
    course: Mapping[str, Any],
    page: int,
    *,
    receipt_period: str = "2099-07-21 ~ 2099-07-24",
    target: str = "초등3학년~초등6학년",
    venue: str = "청소년문화의집 2층 다목적활동실",
    branch: str | None = None,
    period: str | None = None,
    schedule: str | None = None,
    fee: str | None = None,
    capacity_current: int | None = None,
    capacity_total: int | None = None,
    hidden_identity: str | None = None,
    control_identity: str | None = None,
    force_control: bool | None = None,
) -> str:
    identity = str(course["identity"])
    open_control = course["status"] == "접수중" if force_control is None else force_control
    control = ""
    if open_control:
        control_id = control_identity or identity
        control = (
            f'<div class="btn_area"><a href="#n" '
            f'onclick="fn_apply(\'{control_id}\', \'receiptIng\');">수강신청</a></div>'
        )
    current = (
        int(course["capacity_current"])
        if capacity_current is None
        else capacity_current
    )
    capacity = (
        int(course["capacity_total"])
        if capacity_total is None
        else capacity_total
    )
    detail_schedule = schedule or f"{course['schedule']} /"
    # Put the slash between time and weekdays, like the real detail page.
    if schedule is None:
        tokens = str(course["schedule"]).split(" ", 1)
        detail_schedule = tokens[0] + (" / " + tokens[1] if len(tokens) > 1 else "")
    return f"""
      <!doctype html><html lang="ko"><head><meta charset="utf-8">
        <title>교육 상세 | 통합예약</title></head><body>
        <form id="listForm" name="listForm" method="post"
          action="/reservation/edu/courseList.do?mId=">
          <input type="hidden" name="page" value="{page}">
          <input type="hidden" name="idx" value="{hidden_identity or identity}">
          <input type="hidden" name="applyGubun" value="">
        </form>
        <table class="tbl frm"><caption>수납전문가2급 상세 내용입니다.</caption><tbody>
          <tr><th>접수기간</th><td>{receipt_period}</td>
              <th>주관기관</th><td>{html.escape(branch or str(course['branch']))}</td></tr>
          <tr><th>교육기간</th><td>{period or course['period']}</td>
              <th>교육시간/요일</th><td>{html.escape(detail_schedule)}</td></tr>
          <tr><th>수강료</th><td>{fee or course['fee']}</td>
              <th>재료비</th><td>무료</td></tr>
          <tr><th>교육대상</th><td>{html.escape(target)}</td>
              <th>모집인원(신청/정원)</th><td>{current}명 / {capacity}명</td></tr>
          <tr><th>강사명</th><td>저장 금지 강사</td>
              <th>강의장소</th><td>{html.escape(venue)}</td></tr>
          <tr><th>강의계획서</th><td colspan="3"><a href="/private-plan.png">계획서</a></td></tr>
          <tr><th>강좌정보</th><td colspan="3">자유 서술과 teacher@example.test</td></tr>
          <tr><th>유의사항</th><td colspan="3">신청자 정보를 입력하세요.</td></tr>
          <tr><th>문의전화</th><td colspan="3">054-979-6623</td></tr>
        </tbody></table>{control}
      </body></html>
    """


def _complete_fixture() -> tuple[
    dict[int, str | list[str]], dict[str, str | list[str]], list[dict[str, Any]]
]:
    courses: list[dict[str, Any]] = []
    for sequence in range(21, 0, -1):
        identity = str(3000 + sequence)
        if sequence == 21:
            course = _course(
                identity,
                sequence,
                title="방학 드론",
                status="접수중",
                period="2099-08-04 ~ 2099-08-14",
            )
        elif sequence == 2:
            course = _course(
                identity,
                sequence,
                branch="칠곡군보건소",
                title="취소된 미래 건강교실",
                status="폐강",
                fee="3,000",
                period="2099-09-01 ~ 2099-09-30",
            )
        elif sequence == 1:
            course = _course(
                identity,
                sequence,
                branch="동명면사무소",
                title="연중 서예",
                status="교육중",
                period="2099-03-30 ~ 2099-11-27",
                schedule="18:00~20:00 수요일 금요일",
            )
        else:
            course = _course(
                identity,
                sequence,
                branch="교육문화회관(사회교육)",
                status="교육마감",
                period="2099-06-01 ~ 2099-07-22",
            )
        courses.append(course)
    page_one = courses[:20]
    page_two = courses[20:]
    pages: dict[int, str | list[str]] = {
        1: _list_page(1, 21, page_one),
        2: _list_page(2, 21, page_two),
        3: _list_page(3, 21, []),
    }
    current = [course for course in courses if course["sequence"] in {21, 2, 1}]
    details: dict[str, str | list[str]] = {}
    for course in current:
        page = 1 if int(course["sequence"]) >= 2 else 2
        receipt = (
            "2099-07-21 ~ 2099-07-24"
            if course["status"] == "접수중"
            else "2099-03-01 ~ 2099-03-10"
        )
        details[str(course["identity"])] = _detail(
            course,
            page,
            receipt_period=receipt,
            venue=f"{course['branch']} 공식 강의실",
        )
    return pages, details, current


def _collect_fixture(
    pages: Mapping[int, str | list[str]],
    details: Mapping[str, str | list[str]],
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], FixturePoster, FakeSession]:
    poster = FixturePoster(pages, details)
    session = FakeSession()
    rows, parser, meta = chilgok.collect(
        _target(),
        today="2099-07-23",
        max_pages=10,
        detail_limit=20,
        session_factory=lambda: session,
        poster=poster,
        **kwargs,
    )
    return rows, parser, meta, poster, session


def test_exact_candidate_provider_override_and_owner_boundaries() -> None:
    assert chilgok.CHILGOK_PROVIDER == "MUNI_WWW_CHILGOK_GO_KR_B19807DD"
    assert chilgok.CHILGOK_CANONICAL_CANDIDATE_ID == "MUNI_IR_85F08E80ABFF"
    assert chilgok.CHILGOK_REJECTED_CANDIDATE_ID == "MUNI_IR_59ADEB392567"
    assert chilgok.CHILGOK_MUNICIPALITY_CODE == "4785000000"
    assert chilgok.CHILGOK_RECOMMENDED_MAX_PAGES == 50
    assert chilgok.CHILGOK_RECOMMENDED_DETAIL_LIMIT == 500
    assert chilgok.is_target(_target())

    canonical = chilgok.CHILGOK_CANDIDATE_AUDIT[
        chilgok.CHILGOK_CANONICAL_CANDIDATE_ID
    ]
    rejected = chilgok.CHILGOK_CANDIDATE_AUDIT[
        chilgok.CHILGOK_REJECTED_CANDIDATE_ID
    ]
    mirror = chilgok.CHILGOK_CANDIDATE_AUDIT["MUNI_IR_03B54318897D"]
    assert canonical["decision"] == "canonical_complete_owner"
    assert rejected["decision"] == "excluded_unofficial_third_party_homepage_guide"
    assert rejected["provider"] == "MUNI_DONGRYO_TISTORY_COM_B8888C93"
    assert mirror["decision"] == "duplicate_cross_host_presentation_alias"
    assert mirror["owner"] == chilgok.CHILGOK_PROVIDER

    override = chilgok.CHILGOK_RECOMMENDED_OVERRIDE
    assert override["code"] == "4785000000"
    assert override["candidates"][0]["url"] == chilgok.CHILGOK_CANONICAL_URL
    assert override["candidates"][1]["exclusion_reason"] == (
        "unofficial_third_party_homepage_guide_not_course_catalogue"
    )
    names = {item["name"] for item in chilgok.CHILGOK_SEPARATE_OWNER_BOUNDARIES}
    assert names == {
        "칠곡군립도서관 온라인수강신청",
        "칠곡문화관광재단 교육프로그램",
        "칠곡군 체육시설 통합예약",
        "칠곡군 농업기술센터 교육",
    }
    library = next(
        item
        for item in chilgok.CHILGOK_SEPARATE_OWNER_BOUNDARIES
        if item["name"] == "칠곡군립도서관 온라인수강신청"
    )
    assert library["branches"] == (
        "칠곡군립",
        "북삼",
        "석적",
        "동명작은",
        "약목작은",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "MUNI_DONGRYO_TISTORY_COM_B8888C93"},
        {"url": "https://www.chilgok.go.kr/reservation/edu/courseList.do"},
        {"url": chilgok.CHILGOK_CANONICAL_URL + "#top"},
        {"url": "http://www.chilgok.go.kr/reservation/edu/courseList.do?mId="},
        {"url": "https://www.chilgok.go.kr.evil.test/reservation/edu/courseList.do?mId="},
        {"url": "https://user@www.chilgok.go.kr/reservation/edu/courseList.do?mId="},
        {"url": "https://www.chilgok.go.kr:444/reservation/edu/courseList.do?mId="},
        {"url": "https://www.chilgok.go.kr/reservation/edu/courseList.do?mId=&page=1"},
    ],
)
def test_target_matching_is_exact(changes: dict[str, str]) -> None:
    assert not chilgok.is_target(_target(**changes))
    rows, parser, meta = chilgok.collect(_target(**changes))
    assert rows == []
    assert parser == chilgok.CHILGOK_PARSER
    assert "exact canonical" in meta["configured_collection_error"]


def test_only_list_and_detail_post_endpoints_are_allowed() -> None:
    assert chilgok._allowed_post_url(chilgok.CHILGOK_CANONICAL_URL)
    assert chilgok._allowed_post_url(chilgok.CHILGOK_DETAIL_POST_URL)
    assert not chilgok._allowed_post_url(chilgok.CHILGOK_APPLICATION_POST_URL)
    assert not chilgok._allowed_post_url(
        "https://www.chilgok.go.kr/reservation/edu/myCourseList.do?mId="
    )
    assert not chilgok._allowed_post_url(
        "https://library.chilgok.go.kr/cg/module/teach/index.do?menu_idx=362"
    )


def test_complete_all_page_sentinel_detail_branch_state_and_pii_contract() -> None:
    pages, details, _current = _complete_fixture()
    rows, parser, meta, poster, session = _collect_fixture(pages, details)

    assert parser == chilgok.CHILGOK_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_requests"] == 9
    assert meta["list_requests"] == 6
    assert meta["detail_pages"] == 3
    assert meta["source_total"] == 21
    assert meta["advertised_pages"] == 2
    assert meta["sentinel_page"] == 3
    assert meta["source_rows"] == 21
    assert meta["current_source_count"] == 3
    assert meta["returned_count"] == 3
    assert meta["current_list_pages_rechecked"] == [1, 2]
    assert meta["boundary_rechecks"] == 3
    assert meta["page1_rechecked"]
    assert meta["last_page_rechecked"]
    assert meta["sentinel_rechecked"]
    assert meta["pagination_complete"]
    assert meta["details_complete"]
    assert meta["snapshot_complete"]
    assert meta["full_snapshot_validated"]
    assert meta["status_counts"] == {"CLOSED": 1, "CANCELLED": 1, "OPEN": 1}
    assert meta["application_control_count"] == 1
    assert meta["application_endpoints_called"] == 0
    assert meta["pii_form_endpoints_called"] == 0
    assert session.closed

    by_status = {row["status"]: row for row in rows}
    assert by_status["OPEN"]["reservation_available"] is True
    assert by_status["OPEN"]["application_type"] == "ONLINE_RESERVATION"
    assert by_status["CLOSED"]["reservation_available"] is False
    assert by_status["CANCELLED"]["status"] == "CANCELLED"
    assert by_status["CANCELLED"]["fee_amount"] == 3000
    assert by_status["CANCELLED"]["fee"] == "3,000원"
    assert {row["branch"] for row in rows} == {
        "청소년문화의집",
        "칠곡군보건소",
        "동명면사무소",
    }
    for row in rows:
        assert row["description"] == row["title"]
        assert row["raw_url"] == row["application_url"]
        assert row["raw_url"].startswith(
            "https://www.chilgok.go.kr/reservation/edu/courseView.do?mId=&idx="
        )
        assert set(row["raw_fields"]) <= chilgok._SAFE_RAW_FIELDS
        assert row["raw_fields"]["application_endpoint_fetched"] is False
        assert row["raw_fields"]["pii_form_fetched"] is False
        payload = repr(row)
        assert "054-979-6623" not in payload
        assert "teacher@example.test" not in payload
        assert "저장 금지 강사" not in payload
        assert "private-plan.png" not in payload
        assert "자유 서술" not in payload

    called_paths = [urlparse(url).path for url, _data in poster.calls]
    assert set(called_paths) == {
        chilgok.CHILGOK_LIST_PATH,
        chilgok.CHILGOK_DETAIL_PATH,
    }
    assert chilgok.CHILGOK_APPLICATION_PATH not in called_paths
    detail_calls = [data for url, data in poster.calls if url == chilgok.CHILGOK_DETAIL_POST_URL]
    assert {data["idx"] for data in detail_calls} == {"3021", "3002", "3001"}
    assert all(data["applyGubun"] == "" for data in detail_calls)


def test_receipt_waiting_is_scheduled_without_application_control() -> None:
    course = _course(
        "4001",
        1,
        title="예정 강좌",
        status="접수대기",
        period="2099-08-04 ~ 2099-08-14",
    )
    pages = {
        1: _list_page(1, 1, [course]),
        2: _list_page(2, 1, []),
    }
    details = {
        "4001": _detail(
            course,
            1,
            receipt_period="2099-07-30 ~ 2099-08-03",
            venue="청소년문화의집 공식 강의실",
        )
    }

    rows, _parser, meta, poster, _session = _collect_fixture(pages, details)

    assert meta["configured_collection_error"] == ""
    assert meta["status_counts"] == {"SCHEDULED": 1}
    assert len(rows) == 1
    assert rows[0]["status"] == "SCHEDULED"
    assert rows[0]["reservation_available"] is False
    assert rows[0]["application_type"] == "INFO_ONLY"
    assert all(
        "fn_apply" not in str(value)
        for url, payload in poster.calls
        for value in (url, *payload.values())
    )


def test_current_page_mutation_after_details_fails_atomically() -> None:
    pages, details, _current = _complete_fixture()
    original = str(pages[1])
    pages[1] = [original, original.replace("방학 드론", "변경된 강좌", 1)]
    rows, _parser, meta, _poster, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "stability recheck failed" in meta["configured_collection_error"]
    assert not meta["snapshot_complete"]


def test_sentinel_must_be_immediate_empty_and_stable() -> None:
    pages, details, _current = _complete_fixture()
    extra = _course("3999", 1, status="교육마감", period="2099-01-01 ~ 2099-01-02")
    pages[3] = _list_page(3, 21, [extra])
    rows, _parser, meta, _poster, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "immediate empty sentinel missing" in meta["configured_collection_error"]

    pages, details, _current = _complete_fixture()
    sentinel = str(pages[3])
    pages[3] = [sentinel, sentinel.replace("검색결과 총 <em>21", "검색결과 총 <em>22")]
    rows, _parser, meta, _poster, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "sentinel" in meta["configured_collection_error"]


def test_sequence_duplicate_and_total_contracts_fail_closed() -> None:
    pages, details, _current = _complete_fixture()
    pages[2] = str(pages[2]).replace("fn_view('3001')", "fn_view('3021')")
    rows, _parser, meta, _poster, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "idx repeated" in meta["configured_collection_error"]

    pages, details, _current = _complete_fixture()
    pages[2] = str(pages[2]).replace(
        'class="list_num" data-title="번호">1',
        'class="list_num" data-title="번호">9',
    )
    rows, _parser, meta, _poster, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "display sequence" in meta["configured_collection_error"]

    pages, details, _current = _complete_fixture()
    pages[2] = str(pages[2]).replace("검색결과 총 <em>21", "검색결과 총 <em>22")
    rows, _parser, meta, _poster, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "total/pagination" in meta["configured_collection_error"]


def test_official_agency_directory_is_exact_and_rows_must_use_it() -> None:
    pages, details, _current = _complete_fixture()
    changed = dict(chilgok.CHILGOK_OFFICIAL_AGENCIES)
    changed["999"] = "미감사 신규기관"
    pages[1] = _list_page(1, 21, [], agencies=changed)
    rows, _parser, meta, _poster, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "official agency directory changed" in meta["configured_collection_error"]

    pages, details, _current = _complete_fixture()
    pages[1] = str(pages[1]).replace(
        '<td class="list_pub" data-title="기관명">청소년문화의집</td>',
        '<td class="list_pub" data-title="기관명">비공식기관</td>',
        1,
    )
    rows, _parser, meta, _poster, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "required course identity drift" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"branch": "석적읍사무소"}, "official branch drift"),
        ({"period": "2099-08-05 ~ 2099-08-14"}, "education period drift"),
        ({"schedule": "12:00~13:00 / 화요일"}, "schedule drift"),
        ({"fee": "9,000원"}, "tuition drift"),
        ({"capacity_total": 99}, "capacity drift"),
        ({"hidden_identity": "9999"}, "hidden idx/page binding drift"),
        ({"control_identity": "9999"}, "application identity/state drift"),
        ({"force_control": False}, "open detail lacks one application control"),
        ({"venue": "문의 054-979-6623"}, "venue contains contact data"),
    ],
)
def test_current_detail_identity_state_and_privacy_drift_fail_closed(
    change: dict[str, Any], error: str
) -> None:
    pages, details, current = _complete_fixture()
    open_course = next(course for course in current if course["status"] == "접수중")
    details[str(open_course["identity"])] = _detail(open_course, 1, **change)
    rows, _parser, meta, _poster, _session = _collect_fixture(pages, details)
    assert rows == []
    assert error in meta["configured_collection_error"]


def test_list_application_control_must_bind_same_idx_and_only_open_state() -> None:
    pages, details, _current = _complete_fixture()
    pages[1] = str(pages[1]).replace(
        "fn_apply('3021', 'receiptIng')",
        "fn_apply('9999', 'receiptIng')",
    )
    rows, _parser, meta, _poster, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "application identity/state binding drift" in meta["configured_collection_error"]

    pages, details, _current = _complete_fixture()
    pages[1] = str(pages[1]).replace(
        '<span>교육마감</span>',
        '<a href="#n" onclick="fn_apply(\'3019\', \'receiptIng\');">교육마감</a>',
        1,
    )
    rows, _parser, meta, _poster, _session = _collect_fixture(pages, details)
    assert rows == []
    assert "non-open course exposes application control" in meta["configured_collection_error"]


def test_caps_and_dedupe_never_return_partial_snapshot() -> None:
    pages, details, _current = _complete_fixture()
    poster = FixturePoster(pages, details)
    session = FakeSession()
    rows, _parser, meta = chilgok.collect(
        _target(),
        today="2099-07-23",
        max_pages=2,
        detail_limit=20,
        session_factory=lambda: session,
        poster=poster,
    )
    assert rows == []
    assert meta["source_cap_reached"]
    assert "required sentinel page 3" in meta["configured_collection_error"]

    poster = FixturePoster(pages, details)
    rows, _parser, meta = chilgok.collect(
        _target(),
        today="2099-07-23",
        max_pages=10,
        detail_limit=2,
        session_factory=FakeSession,
        poster=poster,
    )
    assert rows == []
    assert meta["source_cap_reached"]
    assert "below required 3" in meta["configured_collection_error"]
    assert not any(url == chilgok.CHILGOK_DETAIL_POST_URL for url, _ in poster.calls)

    pages, details, _current = _complete_fixture()
    rows, _parser, meta, _poster, _session = _collect_fixture(
        pages,
        details,
        dedupe_rows=lambda values: values[:-1],
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_CHILGOK") != "1",
    reason="set RUN_LIVE_CHILGOK=1 for the official live contract",
)
def test_live_chilgok_complete_snapshot_opt_in() -> None:
    rows, parser, meta = chilgok.collect(
        _target(),
        max_pages=chilgok.CHILGOK_RECOMMENDED_MAX_PAGES,
        detail_limit=chilgok.CHILGOK_RECOMMENDED_DETAIL_LIMIT,
    )
    assert parser == chilgok.CHILGOK_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] >= 288
    assert meta["advertised_pages"] >= 15
    assert meta["sentinel_page"] == meta["advertised_pages"] + 1
    assert meta["source_rows"] == meta["source_total"]
    assert meta["current_source_count"] == len(rows)
    assert meta["returned_count"] == len(rows)
    assert meta["detail_pages"] == len(rows)
    assert meta["pagination_complete"]
    assert meta["details_complete"]
    assert meta["full_snapshot_validated"]
    assert meta["page1_rechecked"]
    assert meta["last_page_rechecked"]
    assert meta["sentinel_rechecked"]
    assert meta["application_endpoints_called"] == 0
    assert meta["pii_form_endpoints_called"] == 0
    assert set(meta["branch_counts"]) <= set(chilgok.CHILGOK_OFFICIAL_AGENCIES.values())
    for row in rows:
        assert row["provider"] == chilgok.CHILGOK_PROVIDER
        assert row["municipality_code"] == "4785000000"
        assert row["end_date"] >= meta["cutoff"]
        assert row["branch"] in chilgok.CHILGOK_OFFICIAL_AGENCIES.values()
        assert set(row["raw_fields"]) <= chilgok._SAFE_RAW_FIELDS
        assert row["raw_fields"]["detail_verified"] is True
        assert row["raw_fields"]["application_endpoint_fetched"] is False
        assert row["raw_fields"]["pii_form_fetched"] is False
        assert not chilgok._PHONE.search(
            repr(
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"raw_url", "application_url"}
                }
            )
        )
