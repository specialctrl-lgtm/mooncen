from __future__ import annotations

from dataclasses import dataclass
from html import escape
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_incheon_ganghwa as ganghwa


@dataclass
class _Response:
    url: str
    html: str
    status_code: int = 200

    @property
    def content(self) -> bytes:
        return self.html.encode("utf-8")


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(
    provider: str = ganghwa.GANGHWA_PROVIDER,
    url: str = ganghwa.GANGHWA_REGISTERED_URL,
    candidate_id: str = ganghwa.GANGHWA_REGISTERED_CANDIDATE_ID,
) -> dict[str, str]:
    return {
        "provider": provider,
        "candidate_id": candidate_id,
        "url": url,
        "name": "강화군 교육종합정보",
        "branch": ganghwa.GANGHWA_MUNICIPALITY_NAME,
    }


def _course(
    identity: str,
    *,
    title: str | None = None,
    branch: str = "강화문화원",
    status: str = "[접수중] [교육중]",
    start: str = "2026-06-01",
    end: str = "2026-12-18",
    apply_start: str = "2026-05-04",
    apply_end: str = "2026-12-18",
    schedule: str = "금 오후 13:00~15:00",
    list_schedule: str | None = None,
    category: str = "취미",
    target: str = "강화군민",
    fee: str = "0원",
    capacity: int = 30,
    method: str = "전화",
    venue: str = "강화문화원(강화군 강화읍 남문로 52)",
    phone: str = "032-932-0011",
    no_application: bool = False,
) -> dict[str, Any]:
    return {
        "identity": identity,
        "title": title or f"강화 안전교육 {identity}",
        "branch": branch,
        "status": status,
        "start": start,
        "end": end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule": schedule,
        "list_schedule": list_schedule or schedule,
        "category": category,
        "target": target,
        "fee": fee,
        "capacity": capacity,
        "method": method,
        "venue": venue,
        "phone": phone,
        "no_application": no_application,
    }


def _historical(identity: str) -> dict[str, Any]:
    return _course(
        identity,
        status="[접수마감] [교육종료]",
        start="2025-02-01",
        end="2025-12-31",
        apply_start="2025-01-01",
        apply_end="2025-01-31",
        method="방문",
    )


def _short(value: str) -> str:
    year, month, day = value.split("-")
    return f"{int(year) % 100:02d}.{int(month):02d}.{int(day):02d}"


def _list_row(course: Mapping[str, Any], page: int) -> str:
    detail = (
        f"/open_content/main/lecture/lecture.do?act=detail"
        f"&lecture_seq={course['identity']}&nowPage={page}"
    )
    reception = (
        "- 접수 :"
        if course["no_application"]
        else (
            f"- 접수 : {escape(str(course['method']))}("
            f"{_short(str(course['apply_start']))}~"
            f"{_short(str(course['apply_end']))})"
        )
    )
    return f"""
      <tr>
        <th class="title">
          <p class="lecture"><a href="{escape(detail, quote=True)}"
             title="강좌 내용보기">{escape(str(course['title']))}</a></p>
          <p class="time">({escape(str(course['list_schedule']))})</p>
        </th>
        <td class="place">{escape(str(course['branch']))}</td>
        <td class="price">{escape(str(course['fee']))}</td>
        <td class="date">{reception}<br>
          - 교육 : {course['start']} ~ {course['end']}<br></td>
        <td class="state"><span>{course['status'].split()[0]}</span><br>
          <span>{course['status'].split()[1]}</span></td>
      </tr>
    """


def _list_page(
    courses: list[dict[str, Any]],
    *,
    reported_page: int,
    link_page: int | None = None,
    total: int,
    last: int,
) -> str:
    link_page = reported_page if link_page is None else link_page
    return f"""
      <html><head><title>{ganghwa._LIST_TITLE}</title></head><body>
        <div id="contents">
          <form action="{ganghwa.GANGHWA_LIST_PATH}" method="get">
            <input type="hidden" name="act" value="list">
            <select name="dept0"><option value="">전체</option></select>
            <input name="keyWord" value="">
          </form>
          <p class="right">* 전체 건수 {total} 건, 현재페이지 :
             {reported_page}/{last}</p>
          <table><thead><tr>
            {''.join(f'<th>{header}</th>' for header in ganghwa._LIST_HEADERS)}
          </tr></thead><tbody>
            {''.join(_list_row(course, link_page) for course in courses)}
          </tbody></table>
        </div>
      </body></html>
    """


def _detail_page(
    course: Mapping[str, Any],
    *,
    title: str | None = None,
    branch: str | None = None,
    education_period: str | None = None,
    omit_label: str = "",
    application_control: bool = False,
) -> str:
    pairs = [
        ("분야", course["category"]),
        ("교육기관", branch or course["branch"]),
        ("교육장소", course["venue"]),
        ("교육대상", course["target"]),
        ("수강료", course["fee"]),
        (
            "접수기간",
            "~"
            if course["no_application"]
            else (
                f"{course['apply_start'].replace('-', '.')} (Mon) ~ "
                f"{course['apply_end'].replace('-', '.')} (Fri)"
            ),
        ),
        (
            "교육기간",
            education_period
            or (
                f"{course['start'].replace('-', '.')} (Mon) ~ "
                f"{course['end'].replace('-', '.')} (Fri)"
            ),
        ),
        ("교육요일/시간", course["schedule"]),
        ("접수방법", course["method"]),
        ("모집인원", f"{course['capacity']} 명"),
        ("문의전화", course["phone"]),
        ("교재및 재료비", "0"),
    ]
    pairs = [pair for pair in pairs if pair[0] != omit_label]
    control = '<a href="/apply.do">수강신청</a>' if application_control else ""
    return f"""
      <html><head><title>교육종합정보 상세</title></head><body>
        <div id="detail_con"><div class="board_view">
          <div class="edu_title">
            <p class="state"><span>{course['status'].split()[0]}</span>
              <span>{course['status'].split()[1]}</span></p>
            <p class="tit">{escape(title or str(course['title']))}</p>
            <p class="edu_btn">{control}</p>
          </div>
          {''.join(f'<dl class="list"><dt>{key}</dt><dd>{escape(str(value))}</dd></dl>' for key, value in pairs)}
          <table><thead><tr><th>상세정보(교육내용)</th><th>환불정책</th></tr></thead>
            <tbody><tr><td>공개 교육 설명</td><td></td></tr></tbody></table>
        </div></div>
      </body></html>
    """


def _default_courses() -> list[dict[str, Any]]:
    current = [
        _course("1001", title="전화 가곡 교실"),
        _course(
            "1002",
            title="예정 컴퓨터 교실",
            branch="강화군청",
            status="[접수예정] [강좌준비]",
            start="2026-08-10",
            end="2026-08-21",
            apply_start="2026-07-13",
            apply_end="2026-07-22",
            schedule="월,화,수,목금 10:00 ~ 12:00",
            list_schedule="월,화,수,목금 10:00 ~ 1...",
            category="컴퓨터",
            method="",
            venue="강화군행복센터 4층 디지털배움터(강화군 강화읍 남문로 19)",
        ),
        _course(
            "3363",
            title="건강댄스",
            branch="읍면사무소(삼산면)",
            status="[접수마감] [교육중]",
            start="2026-01-01",
            end="2026-12-31",
            schedule="목 18:00~20:00",
            method="",
            venue="삼산면 주민자치센터(강화군 삼산면 삼산북로 475)",
            no_application=True,
        ),
    ]
    return current + [_historical(str(identity)) for identity in range(2001, 2009)]


def _fetcher(
    courses: list[dict[str, Any]],
    *,
    calls: list[str] | None = None,
    sentinel_drift: bool = False,
    boundary_drift: bool = False,
    total_drift: bool = False,
    detail_overrides: Mapping[str, str] | None = None,
):
    pages = [
        courses[index : index + ganghwa.GANGHWA_PAGE_SIZE]
        for index in range(0, len(courses), ganghwa.GANGHWA_PAGE_SIZE)
    ]
    total = len(courses)
    last = len(pages)
    page_calls: dict[int, int] = {}
    details = {
        str(course["identity"]): _detail_page(course)
        for course in courses
    }
    details.update(detail_overrides or {})

    def fetch(session: _Session, url: str, timeout: int) -> _Response:
        assert timeout == 7
        if calls is not None:
            calls.append(url)
        query = parse_qs(urlparse(url).query, keep_blank_values=True)
        if query.get("act") == ["detail"]:
            identity = query["lecture_seq"][0]
            return _Response(url, details[identity])
        requested = int(query["nowPage"][0])
        reported = min(requested, last)
        page_calls[requested] = page_calls.get(requested, 0) + 1
        page_courses = pages[reported - 1]
        if sentinel_drift and requested == last + 1:
            page_courses = pages[0]
        if boundary_drift and requested == 1 and page_calls[requested] > 1:
            changed = dict(page_courses[0])
            changed["title"] = "경계에서 변경된 강좌"
            page_courses = [changed, *page_courses[1:]]
        page_total = total + int(total_drift and requested == 2)
        return _Response(
            url,
            _list_page(
                page_courses,
                reported_page=reported,
                link_page=requested,
                total=page_total,
                last=last,
            ),
        )

    return fetch


def _collect(
    courses: list[dict[str, Any]] | None = None,
    *,
    fetcher=None,
    **kwargs: Any,
):
    courses = _default_courses() if courses is None else courses
    return ganghwa.collect_incheon_ganghwa_education(
        _target(),
        timeout=7,
        max_pages=kwargs.pop("max_pages", 10),
        detail_limit=kwargs.pop("detail_limit", 20),
        max_requests=kwargs.pop("max_requests", 30),
        today=kwargs.pop("today", "2026-07-22"),
        fetcher=fetcher or _fetcher(courses),
        session_factory=_Session,
        sleeper=lambda _: None,
        max_workers=kwargs.pop("max_workers", 2),
        **kwargs,
    )


def test_constants_target_candidates_urls_and_owner_boundaries_are_exact() -> None:
    assert ganghwa.GANGHWA_PROVIDER == "MUNI_WWW_GANGHWA_GO_KR_E1374F0C"
    assert ganghwa.GANGHWA_CANONICAL_CANDIDATE_ID == "MUNI_IR_C5D5D85A5F6F"
    assert ganghwa.GANGHWA_REGISTERED_CANDIDATE_ID == "MUNI_IR_E7829E889C7E"
    assert ganghwa.GANGHWA_MUNICIPALITY_CODE == "2871000000"
    assert ganghwa.GANGHWA_CANONICAL_URL == (
        "https://www.ganghwa.go.kr/open_content/main/lecture/lecture.do?act=list"
    )
    assert ganghwa.is_target(_target())
    assert ganghwa.is_target(
        _target(
            url=ganghwa.GANGHWA_CANONICAL_URL,
            candidate_id=ganghwa.GANGHWA_CANONICAL_CANDIDATE_ID,
        )
    )
    assert not ganghwa.is_target(_target(provider="OTHER"))
    assert not ganghwa.is_target(_target(candidate_id="MUNI_IR_WRONG"))
    assert not ganghwa.is_target(_target(url=ganghwa.GANGHWA_DONG_DUPLICATE_URL))
    audit = ganghwa.GANGHWA_OWNER_BOUNDARY_AUDIT
    assert audit[ganghwa.GANGHWA_DONG_DUPLICATE_CANDIDATE_ID]["decision"] == (
        "duplicate_dong_surface_same_lecture_database"
    )
    assert audit["MUNI_IR_6FC6F8469CA1"]["owner"] == "INCHEON_RESERVATION"
    assert audit["MUNI_IR_A876988923EE"]["decision"] == (
        "separate_ganghwa_library_platform_three_registered_branches"
    )
    assert audit["MUNI_IR_C89B11F02457"]["official_name"] == "강화군 행복센터"
    assert audit["MUNI_IR_56A9C18BFC2F"]["official_name"] == (
        "강화교육지원청 평생학습관"
    )
    test_row = audit["MUNI_IR_C644B175C0E2"]["audited_test_row"]
    assert test_row == {
        "identity": "field_idx:1",
        "title": "웹접근성테스트",
        "detail_url": (
            "https://www.ghss.or.kr/user/reserv/fieldTrip/fieldView.do?idx=1"
        ),
        "detail_candidate_id": "MUNI_IR_BE88BA1E0DB9",
    }


def test_url_helpers_are_scoped_and_identity_bound() -> None:
    parsed = urlparse(ganghwa.ganghwa_list_url(7))
    assert parsed.hostname == ganghwa.GANGHWA_HOST
    assert parse_qs(parsed.query) == {"act": ["list"], "nowPage": ["7"]}
    current = ganghwa.ganghwa_list_url(7)
    assert ganghwa.canonical_ganghwa_detail_identity(
        current,
        "/open_content/main/lecture/lecture.do?act=detail&lecture_seq=123&nowPage=7",
        expected_page=7,
    ) == "123"
    assert not ganghwa.canonical_ganghwa_detail_identity(
        current,
        "https://evil.example/open_content/main/lecture/lecture.do?act=detail&lecture_seq=123&nowPage=7",
        expected_page=7,
    )
    assert not ganghwa.canonical_ganghwa_detail_identity(
        current,
        "/open_content/main/lecture/lecture.do?act=detail&lecture_seq=123&nowPage=8",
        expected_page=7,
    )
    with pytest.raises(ganghwa.GanghwaContractError):
        ganghwa.ganghwa_detail_url("../123")


def test_complete_archive_clamp_boundaries_and_all_current_details() -> None:
    calls: list[str] = []
    courses = _default_courses()
    rows, parser, meta = _collect(
        courses,
        fetcher=_fetcher(courses, calls=calls),
    )
    assert parser == ganghwa.GANGHWA_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{ganghwa.GANGHWA_PROVIDER}:lecture:1001",
        f"{ganghwa.GANGHWA_PROVIDER}:lecture:1002",
        f"{ganghwa.GANGHWA_PROVIDER}:lecture:3363",
    ]
    assert rows[0]["category"] == "취미"
    assert rows[0]["capacity_total"] == 30
    assert rows[0]["apply_period"] == "2026-05-04 ~ 2026-12-18"
    assert rows[0]["venue_address"] == "강화군 강화읍 남문로 52"
    assert rows[0]["application_type"] == "OFFLINE_RESERVATION"
    assert "application_url" not in rows[0]
    assert rows[1]["status"] == "SCHEDULED"
    assert rows[1]["schedule_raw"] == "월,화,수,목금 10:00 ~ 12:00"
    assert rows[1]["raw_fields"]["list_schedule_truncated"] is True
    assert rows[1]["application_type"] == "SCHEDULED_INFORMATION"
    assert rows[2]["status"] == "CLOSED"
    assert "apply_period" not in rows[2]
    assert rows[2]["raw_fields"]["audited_no_application_period"] is True
    assert meta["source_total"] == 11
    assert meta["pages"] == 2
    assert meta["list_requests"] == 5
    assert meta["sentinel_requests"] == 1
    assert meta["sentinel_page"] == 3
    assert meta["sentinel_kind"] == "exact_final_page_clamp"
    assert meta["stability_rechecks"] == 2
    assert meta["current_source_count"] == 3
    assert meta["detail_pages"] == 3
    assert meta["returned_count"] == 3
    assert meta["network_requests"] == 8 == len(calls)
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert meta["semantic_duplicate_count"] == 0
    assert meta["application_control_count"] == 0
    assert meta["audited_reversed_application_period_count"] == 0
    assert meta["truncated_list_schedule_count"] == 1
    assert meta["audited_no_application_period_count"] == 1


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_pages": 2}, "exceeds max_pages cap"),
        ({"detail_limit": 2}, "detail_limit cap allows 2 of 3"),
        ({"max_requests": 7}, "max_requests cap allows 7 of 8"),
    ],
)
def test_all_caps_fail_closed(kwargs: Mapping[str, int], message: str) -> None:
    rows, _, meta = _collect(**kwargs)
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False


def test_wrong_target_returns_configured_error_without_network() -> None:
    rows, parser, meta = ganghwa.collect(
        _target(provider="OTHER"),
        fetcher=lambda *_: pytest.fail("wrong target must not touch network"),
    )
    assert rows == []
    assert parser == ganghwa.GANGHWA_PARSER
    assert "does not match" in meta["configured_collection_error"]
    assert "network_requests" not in meta


@pytest.mark.parametrize(
    ("fetcher_options", "message"),
    [
        ({"sentinel_drift": True}, "final-page clamp contents changed"),
        ({"boundary_drift": True}, "first page changed during stable recheck"),
        ({"total_drift": True}, "advertised source total changed"),
    ],
)
def test_census_sentinel_and_boundary_drift_fail_closed(
    fetcher_options: Mapping[str, bool],
    message: str,
) -> None:
    courses = _default_courses()
    rows, _, meta = _collect(
        courses,
        fetcher=_fetcher(courses, **fetcher_options),
    )
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_duplicate_identity_and_generic_test_or_notice_rows_fail_closed() -> None:
    duplicate = _default_courses()
    duplicate[1] = {**duplicate[1], "identity": duplicate[0]["identity"]}
    rows, _, meta = _collect(duplicate)
    assert rows == []
    assert "duplicate lecture identities" in meta["configured_collection_error"]

    for title in ("테스트", "교육 안내", "sample-2"):
        courses = _default_courses()
        courses[0] = {**courses[0], "title": title}
        rows, _, meta = _collect(courses)
        assert rows == []
        assert "unaudited test/information row" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"title": "다른 강좌"}, "detail title does not match"),
        ({"branch": "강화군청"}, "detail institution does not match"),
        (
            {"education_period": "2026.06.01 (Mon) ~ 2026.11.30 (Mon)"},
            "detail education period disagrees",
        ),
        ({"omit_label": "교육대상"}, "detail field schema changed"),
        ({"application_control": True}, "unaudited application control"),
    ],
)
def test_detail_identity_schema_period_and_write_controls_fail_closed(
    override: Mapping[str, Any],
    message: str,
) -> None:
    courses = _default_courses()
    first = courses[0]
    detail_overrides = {"1001": _detail_page(first, **override)}
    rows, _, meta = _collect(
        courses,
        fetcher=_fetcher(courses, detail_overrides=detail_overrides),
    )
    assert rows == []
    assert message in meta["configured_collection_error"]
    assert meta["details_complete"] is False


def test_unknown_current_institution_and_semantic_duplicates_fail_closed() -> None:
    courses = _default_courses()
    courses[0] = {**courses[0], "branch": "임의 교육기관"}
    rows, _, meta = _collect(courses)
    assert rows == []
    assert "unaudited current institution name" in meta["configured_collection_error"]

    courses = _default_courses()
    courses[1] = {
        **courses[1],
        "title": courses[0]["title"],
        "branch": courses[0]["branch"],
        "start": courses[0]["start"],
        "end": courses[0]["end"],
        "apply_start": courses[0]["apply_start"],
        "apply_end": courses[0]["apply_end"],
        "schedule": courses[0]["schedule"],
        "list_schedule": courses[0]["list_schedule"],
        "category": courses[0]["category"],
        "status": courses[0]["status"],
        "method": courses[0]["method"],
        "venue": courses[0]["venue"],
    }
    rows, _, meta = _collect(courses)
    assert rows == []
    assert "semantic duplicates" in meta["configured_collection_error"]


def test_no_current_data_is_an_explicit_complete_snapshot() -> None:
    courses = [_historical(str(identity)) for identity in range(3001, 3012)]
    rows, _, meta = _collect(courses)
    assert rows == []
    assert meta["source_total"] == 11
    assert meta["current_source_count"] == 0
    assert meta["detail_pages"] == 0
    assert meta["network_requests"] == 5
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert "complete Ganghwa archive" in meta["no_current_reason"]
    assert meta["configured_collection_error"] == ""


@pytest.mark.skipif(
    os.getenv("RUN_INCHEON_GANGHWA_LIVE_TEST") != "1",
    reason="set RUN_INCHEON_GANGHWA_LIVE_TEST=1 for exact Ganghwa live audit",
)
def test_live_exact_complete_archive_current_details_and_names() -> None:
    rows, parser, meta = ganghwa.collect(
        _target(),
        timeout=30,
        max_pages=200,
        detail_limit=400,
        max_requests=600,
        today="2026-07-22",
        max_workers=10,
    )
    assert parser == ganghwa.GANGHWA_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["source_total"] == 1650
    assert meta["pages"] == 165
    assert meta["list_requests"] == 168
    assert meta["sentinel_page"] == 166
    assert meta["sentinel_kind"] == "exact_final_page_clamp"
    assert meta["stability_rechecks"] == 2
    assert meta["current_source_count"] == 315
    assert meta["detail_pages"] == 315
    assert meta["returned_count"] == 315 == len(rows)
    assert meta["network_requests"] == 483
    assert meta["network_retry_count"] == 0
    assert meta["branch_count"] == 25
    assert meta["venue_count"] == 77
    assert set(meta["branch_counts"]) == ganghwa.GANGHWA_CURRENT_INSTITUTIONS
    assert meta["current_source_status_counts"] == {
        "[접수중] [교육중]": 76,
        "[접수마감] [교육중]": 210,
        "[접수예정] [강좌준비]": 21,
        "[접수마감] [강좌준비]": 8,
    }
    assert meta["status_counts"] == {"OPEN": 76, "CLOSED": 218, "SCHEDULED": 21}
    assert meta["semantic_duplicate_count"] == 0
    assert meta["test_or_notice_row_count"] == 0
    assert meta["application_control_count"] == 0
    assert meta["audited_reversed_application_period_count"] == 16
    assert meta["truncated_list_schedule_count"] == 9
    assert meta["audited_no_application_period_count"] == 6
    assert meta["snapshot_complete"] is True
    assert len({row["provider_course_id"] for row in rows}) == 315
    assert all(row["end_date"] >= "2026-07-22" for row in rows)
    assert all(row["branch"] in ganghwa.GANGHWA_CURRENT_INSTITUTIONS for row in rows)
    assert all("application_url" not in row for row in rows)
