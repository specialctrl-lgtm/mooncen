from __future__ import annotations

from dataclasses import dataclass
from html import escape
import math
import os
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_incheon_yeonsu as yeonsu


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
    provider: str = yeonsu.YEONSU_PROVIDER,
    url: str = yeonsu.YEONSU_CANONICAL_URL,
) -> dict[str, str]:
    return {
        "provider": provider,
        "url": url,
        "name": "연수구 주민자치센터 프로그램",
        "branch": yeonsu.YEONSU_MUNICIPALITY_NAME,
    }


def _course(
    identity: str,
    *,
    title: str | None = None,
    list_status: str = "신청마감",
    detail_status: str | None = None,
    apply_start: str = "2026-07-01",
    apply_end: str = "2026-07-31",
    start: str = "2026-08-01",
    end: str = "2026-09-30",
    branch: str = "연수1동 주민자치센터",
    target: str = "일반",
    fee: str = "무료",
    capacity: str = "인터넷 [ 2 / 10 ] 방문 [ 1 / 5 ]",
    sessions: int = 8,
    online: bool = False,
) -> dict[str, Any]:
    if detail_status is None:
        detail_status = "접수중" if list_status == "접수가능" else list_status
    return {
        "identity": identity,
        "title": title or f"연수 안전교육 {identity}",
        "detail_title": (title or f"연수 안전교육 {identity}") + "(성인)",
        "list_status": list_status,
        "detail_status": detail_status,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "start": start,
        "end": end,
        "branch": branch,
        "target": target,
        "fee": fee,
        "capacity": capacity,
        "sessions": sessions,
        "online": online,
    }


def _short_range(start: str, end: str) -> str:
    start_parts = start.split("-")
    end_parts = end.split("-")
    return (
        f"{int(start_parts[0])}.{int(start_parts[1])}.{int(start_parts[2])}"
        f"~{int(end_parts[1])}.{int(end_parts[2])}"
    )


def _list_card(course: Mapping[str, Any]) -> str:
    identity = course["identity"]
    detail = (
        f"/edu/sub/apply.asp?page=v&lec_idx={identity}&gotopage=1&dept_idx="
        "&list_url=Lecture_list_life&edu_kind=&team_idx=&strMode=&strCode=0"
        "&strKind=&strSearch=ok&strSearch01=lec_name2&strSearch02=&s_target="
        "&s_date=&e_date="
    )
    return f"""
      <li class="{'close' if course['list_status'] == '신청마감' else ''}">
        <a href="{escape(detail, quote=True)}"><dl>
          <dt><p>{escape(str(course['title']))}</p></dt><dd><ul>
            <li><p class="q">신청기간</p><p class="a">:
              {_short_range(course['apply_start'], course['apply_end'])}
              <span class="lec_state">{course['list_status']}</span></p></li>
            <li><p class="q">교육기간</p><p class="a">:
              {int(course['start'][5:7])}.{int(course['start'][8:10])}
              ~{int(course['end'][5:7])}.{int(course['end'][8:10])}</p></li>
            <li><p class="q">수강료</p><p class="a">: {course['fee']}</p></li>
            <li><p class="q">교육기관</p><p class="a">: {course['branch']}</p></li>
          </ul></dd>
        </dl></a>
      </li>
    """


def _pager(partition: yeonsu.YeonsuPartition, page: int, last: int) -> str:
    links = []
    for value in range(1, last + 1):
        classes = ' class="select"' if value == page else ""
        links.append(
            f'<a{classes} title="{value} page" '
            f'href="{escape(yeonsu.yeonsu_list_url(partition, value), quote=True)}">'
            f"{value}</a>"
        )
    return f'<div class="paging">{"".join(links)}</div>'


def _list_page(
    partition: yeonsu.YeonsuPartition,
    page: int,
    last: int,
    courses: list[dict[str, Any]],
) -> str:
    return f"""
      <html><head><title>{yeonsu._LIST_TITLE}</title></head><body>
        <form name="frm_dong" action="/edu/sub/apply.asp" method="get">
          <input name="strSearch01" value="lec_name2">
          <input name="strSearch" value="ok">
          <input name="s_date" value="{partition.start.isoformat()}">
          <input name="e_date" value="{partition.end.isoformat()}">
          <input name="strSearch02" value="">
        </form>
        <div class="donglec_list"><ul>
          {''.join(_list_card(course) for course in courses)}
        </ul></div>
        {_pager(partition, page, last)}
      </body></html>
    """


def _application_controls(course: Mapping[str, Any], *, bad: bool = False) -> str:
    if not course["online"]:
        return ""
    href = (
        "https://evil.example/apply"
        if bad
        else (
            f"/edu/sub/apply.asp?page=r&lec_idx={course['identity']}"
            "&age_idx=4&lec_onlineP=10&lec_limitMethod=1"
        )
    )
    anchor = f'<a class="btn btn_ok" href="{escape(href, quote=True)}">수강신청</a>'
    return anchor + anchor


def _detail_page(
    course: Mapping[str, Any],
    *,
    bad_application: bool = False,
    detail_title: str | None = None,
    branch: str | None = None,
    education_text: str | None = None,
) -> str:
    actual_branch = branch or str(course["branch"])
    period = education_text or (
        f"{course['start']} ~ {course['end']} (매주 수 10:00~12:00)"
    )
    return f"""
      <html><head><title>{yeonsu._LIST_TITLE}</title></head><body>
        {_application_controls(course, bad=bad_application)}
        <div class="board_view">
          <div class="board_title">
            {escape(detail_title or str(course['detail_title']))}
            <p class="state"><span>{course['detail_status']}</span></p>
          </div>
          <dl><dt>서버시간</dt><dd>SECRET SERVER</dd></dl>
          <dl><dt>교육대상</dt><dd>{course['target']}</dd></dl>
          <dl><dt>기수</dt><dd>2026년 3기</dd></dl>
          <dl><dt>수강료</dt><dd>{course['fee']}</dd></dl>
          <dl><dt>신청현황</dt><dd>{course['capacity']}</dd></dl>
          <dl><dt>신청기간</dt><dd>{course['apply_start']} (09:00)
            ~ {course['apply_end']} (18:00)</dd></dl>
          <dl><dt>교육기간</dt><dd>{period}</dd></dl>
          <dl><dt>교육기관</dt><dd>{actual_branch}</dd></dl>
          <dl><dt>교육장소</dt><dd>행정복지센터 3층</dd></dl>
          <dl><dt>총수강일수</dt><dd>{course['sessions']}일</dd></dl>
          <dl><dt>문의전화</dt><dd>SECRET PHONE 010-1111-2222</dd></dl>
          <dl><dt>강좌소개</dt><dd>SECRET DESCRIPTION private@example.test</dd></dl>
          <dl><dt>강좌소개문서</dt><dd>SECRET ATTACHMENT</dd></dl>
          <dl><dt>강사명</dt><dd>SECRET INSTRUCTOR</dd></dl>
          <dl><dt>입금정보</dt><dd>SECRET PAYMENT</dd></dl>
          <dl><dt>취소환불규정</dt><dd>SECRET REFUND</dd></dl>
          <dl><dt>인원제한방법</dt><dd>SECRET METHOD</dd></dl>
        </div>
      </body></html>
    """


def _page_map(
    partition: yeonsu.YeonsuPartition,
    courses: list[dict[str, Any]],
) -> dict[int, str]:
    last = max(1, math.ceil(len(courses) / yeonsu.YEONSU_PAGE_SIZE))
    result: dict[int, str] = {}
    for page in range(1, last + 1):
        start = (page - 1) * yeonsu.YEONSU_PAGE_SIZE
        result[page] = _list_page(
            partition,
            page,
            last,
            courses[start : start + yeonsu.YEONSU_PAGE_SIZE],
        )
    result[last + 1] = _list_page(partition, last + 1, last, [])
    return result


def _default_courses() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    previous = [
        _course(
            "9001",
            apply_start="2025-01-01",
            apply_end="2025-01-31",
            start="2025-02-01",
            end="2025-02-28",
        )
    ]
    current: list[dict[str, Any]] = [
        _course(
            "1001",
            title="온라인 보이스피싱 예방",
            list_status="접수가능",
            detail_status="접수중",
            online=True,
        ),
        _course(
            "1002",
            title="방문 멀티체육",
            list_status="방문접수중",
            capacity="인터넷 [ 9 / 9 ] 방문 [ 6 / 11 ]",
        ),
        _course(
            "1003",
            title="대기 생활영어",
            list_status="대기접수중",
            capacity="인터넷 [ 15 / 15 ] 대기 [ 9 / 10 ]",
        ),
    ]
    current.extend(_course(str(identity)) for identity in range(1004, 1014))
    return previous, current


def _fetcher(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    detail_overrides: Mapping[str, str] | None = None,
    bad_sentinel_year: int | None = None,
    drift_year: int | None = None,
    calls: list[str] | None = None,
):
    partitions = yeonsu.yeonsu_partitions(yeonsu.date(2026, 7, 22))
    pages = {
        partitions[0].start.year: _page_map(partitions[0], previous),
        partitions[1].start.year: _page_map(partitions[1], current),
    }
    details = {
        course["identity"]: _detail_page(course)
        for course in previous + current
    }
    details.update(detail_overrides or {})
    page_one_calls: dict[int, int] = {}

    def fetch(session: _Session, url: str, timeout: int) -> _Response:
        assert timeout == 7
        if calls is not None:
            calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query.get("page") == ["v"]:
            identity = query["lec_idx"][0]
            return _Response(url, details[identity])
        year = int(query["s_date"][0][:4])
        page = int(query["gotopage"][0])
        page_one_calls[year] = page_one_calls.get(year, 0) + (page == 1)
        html = pages[year][page]
        last = max(pages[year]) - 1
        if bad_sentinel_year == year and page == last + 1:
            source = previous if year == 2025 else current
            html = _list_page(
                partitions[0] if year == 2025 else partitions[1],
                page,
                last,
                [source[0]],
            )
        if drift_year == year and page == 1 and page_one_calls[year] > 1:
            html = html.replace("연수 안전교육", "변경된 연수교육", 1)
        return _Response(url, html)

    return fetch


def _collect(
    previous: list[dict[str, Any]] | None = None,
    current: list[dict[str, Any]] | None = None,
    *,
    fetcher=None,
    **kwargs,
):
    default_previous, default_current = _default_courses()
    previous = default_previous if previous is None else previous
    current = default_current if current is None else current
    return yeonsu.collect_incheon_yeonsu_education(
        _target(),
        timeout=7,
        max_pages=kwargs.pop("max_pages", 10),
        detail_limit=kwargs.pop("detail_limit", 100),
        max_requests=kwargs.pop("max_requests", 100),
        today=kwargs.pop("today", "2026-07-22"),
        fetcher=fetcher or _fetcher(previous, current),
        session_factory=_Session,
        sleeper=lambda _: None,
        max_workers=kwargs.pop("max_workers", 2),
        **kwargs,
    )


def test_constants_target_urls_candidates_and_owner_boundaries_are_exact() -> None:
    assert yeonsu.YEONSU_PROVIDER == "MUNI_WWW_YEONSU_GO_KR_CB4C41BB"
    assert yeonsu.YEONSU_CANONICAL_CANDIDATE_ID == "MUNI_IR_876F7A68981B"
    assert yeonsu.YEONSU_CANONICAL_URL == "https://www.yeonsu.go.kr/edu/sub/apply.asp"
    assert yeonsu.is_target(_target())
    assert yeonsu.is_target(_target(url=yeonsu.YEONSU_LEGACY_DETAIL_URL))
    assert not yeonsu.is_target(_target(provider="OTHER"))
    assert not yeonsu.is_target(_target(url=yeonsu.YEONSU_CANONICAL_URL + "?gotopage=1"))
    audit = yeonsu.YEONSU_OWNER_BOUNDARY_AUDIT
    assert audit["MUNI_IR_13A1327A5249"]["decision"] == (
        "separate_lifelong_integrated_search_owner"
    )
    assert audit["MUNI_IR_F1A32FCD318C"]["decision"] == (
        "separate_culture_portal_education_owner"
    )
    assert audit["MUNI_IR_6FC6F8469CA1"]["owner"] == "INCHEON_RESERVATION"


def test_operational_arguments_cover_the_complete_two_partition_snapshot() -> None:
    from Crawler import Crawler_GeneratedYamlTargets as generated

    arguments = generated.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        yeonsu.YEONSU_PROVIDER
    ]
    parsed = generated.parse_args(arguments)

    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.allow_partial_save is False
    assert parsed.per_target_limit == 0
    assert parsed.max_pages == 400
    assert parsed.detail_limit == 700


def test_url_helpers_are_scoped_and_fail_closed() -> None:
    previous, current = yeonsu.yeonsu_partitions(yeonsu.date(2026, 7, 22))
    parsed = urlparse(yeonsu.yeonsu_list_url(current, 3))
    query = parse_qs(parsed.query, keep_blank_values=True)
    assert query["gotopage"] == ["3"]
    assert query["s_date"] == ["2026-01-01"]
    assert query["e_date"] == ["2099-12-31"]
    assert previous.end.isoformat() == "2025-12-31"
    assert yeonsu.canonical_yeonsu_detail_identity(
        yeonsu.yeonsu_list_url(current, 1),
        "/edu/sub/apply.asp?page=v&lec_idx=123&gotopage=1",
    ) == "123"
    assert not yeonsu.canonical_yeonsu_detail_identity(
        yeonsu.yeonsu_list_url(current, 1),
        "https://evil.example/edu/sub/apply.asp?page=v&lec_idx=123",
    )
    with pytest.raises(yeonsu.YeonsuContractError):
        yeonsu.yeonsu_detail_url("../123")


def test_atomic_two_partition_snapshot_is_complete_and_private_values_are_never_read() -> None:
    rows, parser, meta = _collect()
    assert parser == yeonsu.YEONSU_PARSER
    assert len(rows) == 13
    assert meta["source_rows"] == 14
    assert meta["previous_year_source_rows"] == 1
    assert meta["previous_year_current_count"] == 0
    assert meta["current_future_source_rows"] == 13
    assert meta["current_source_count"] == 13
    assert meta["pages"] == 3
    assert meta["list_requests"] == 9
    assert meta["detail_pages"] == 13
    assert meta["network_requests"] == 22
    assert meta["status_counts"] == {"OPEN": 3, "CLOSED": 10}
    assert meta["application_control_count"] == 1
    assert meta["application_anchor_count"] == 2
    assert meta["offline_application_count"] == 1
    assert meta["waitlist_information_only_count"] == 1
    assert meta["snapshot_complete"] is True
    by_id = {
        row["raw_fields"]["source_identity"]: row for row in rows
    }
    assert by_id["1001"]["reservation_available"] is True
    assert by_id["1001"]["application_url"].startswith(
        yeonsu.YEONSU_CANONICAL_URL + "?page=r&lec_idx=1001"
    )
    assert by_id["1002"]["application_type"] == "OFFLINE_RESERVATION"
    assert by_id["1003"]["application_type"] == "WAITLIST_INFO_ONLY"
    serialized = repr(rows)
    for secret in (
        "SECRET",
        "010-1111-2222",
        "private@example.test",
        "INSTRUCTOR",
        "PAYMENT",
        "REFUND",
    ):
        assert secret not in serialized


@pytest.mark.parametrize("failure", ["sentinel", "drift"])
def test_sentinel_and_stability_changes_discard_the_entire_snapshot(failure: str) -> None:
    previous, current = _default_courses()
    fetcher = _fetcher(
        previous,
        current,
        bad_sentinel_year=2026 if failure == "sentinel" else None,
        drift_year=2026 if failure == "drift" else None,
    )
    rows, _, meta = _collect(previous, current, fetcher=fetcher)
    assert rows == []
    assert meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False


def test_detail_and_page_caps_fail_before_partial_publication() -> None:
    rows, _, meta = _collect(detail_limit=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]
    rows, _, meta = _collect(max_pages=1)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_pages cap" in meta["configured_collection_error"]


def test_application_title_and_branch_drift_are_fail_closed() -> None:
    previous, current = _default_courses()
    online = current[0]
    bad_cases = (
        _detail_page(online, bad_application=True),
        _detail_page(online, detail_title="다른 강좌(성인)"),
        _detail_page(online, branch="서울특별시 타구 주민센터"),
    )
    for bad_detail in bad_cases:
        fetcher = _fetcher(
            previous,
            current,
            detail_overrides={online["identity"]: bad_detail},
        )
        rows, _, meta = _collect(previous, current, fetcher=fetcher)
        assert rows == []
        assert meta["configured_collection_error"]


def test_waitlist_list_may_lag_closed_detail_and_detail_status_wins() -> None:
    current = [
        _course(
            "44578",
            title="기초생활영어",
            list_status="대기접수중",
            detail_status="신청마감",
            capacity="인터넷 [ 14 / 14 ] 방문 [ 2 / 2 ]",
        )
    ]

    rows, _, meta = _collect(current=current)

    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "CLOSED"
    assert row["application_type"] == "INFO_ONLY"
    assert row["reservation_available"] is False
    assert row["raw_fields"]["source_status"] == "대기접수중"
    assert row["raw_fields"]["detail_source_status"] == "신청마감"
    assert row["raw_fields"]["list_detail_status_mismatch"] is True
    assert meta["list_detail_status_mismatch_count"] == 1
    assert meta["list_detail_status_mismatch_ids"] == ["44578"]


def test_unreviewed_list_detail_status_transition_is_fail_closed() -> None:
    current = [
        _course(
            "1001",
            list_status="신청마감",
            detail_status="접수중",
            online=True,
        )
    ]

    rows, _, meta = _collect(current=current)

    assert rows == []
    assert "1001: list/detail status mismatch" in meta["configured_collection_error"]
    assert "'신청마감' -> '접수중'" in meta["configured_collection_error"]


def test_exact_reversed_period_is_excluded_but_identity_or_title_drift_fails() -> None:
    previous = [
        _course(
            "9001",
            apply_start="2025-01-01",
            apply_end="2025-01-31",
            start="2025-02-01",
            end="2025-02-28",
        )
    ]
    valid = _course("1001")
    reversed_row = _course(
        "43749",
        title="파워이브닝요가",
        apply_start="2026-03-16",
        apply_end="2026-03-31",
        start="2026-04-02",
        end="2027-03-31",
        branch="옥련1동 주민자치센터",
    )
    reversed_row["detail_title"] = "파워이브닝요가(성인)"
    exact_detail = _detail_page(
        reversed_row,
        education_text="2026-04-02 ~ 2026-03-31 (매주 화, 목 18:20~19:10)",
    )
    current = [valid, reversed_row]
    fetcher = _fetcher(
        previous,
        current,
        detail_overrides={"43749": exact_detail},
    )
    rows, _, meta = _collect(previous, current, fetcher=fetcher)
    assert [row["raw_fields"]["source_identity"] for row in rows] == ["1001"]
    assert meta["audited_reversed_period_count"] == 1
    assert meta["audited_reversed_period_ids"] == ["43749"]

    drifted = _detail_page(
        reversed_row,
        detail_title="재사용된 다른 강좌(성인)",
        education_text="2026-04-02 ~ 2026-03-31 (매주 화)",
    )
    fetcher = _fetcher(
        previous,
        current,
        detail_overrides={"43749": drifted},
    )
    rows, _, meta = _collect(previous, current, fetcher=fetcher)
    assert rows == []
    assert "title mismatch" in meta["configured_collection_error"]


def test_complete_no_current_snapshot_is_explicit() -> None:
    previous = [
        _course(
            "9001",
            apply_start="2025-01-01",
            apply_end="2025-01-31",
            start="2025-02-01",
            end="2025-02-28",
        )
    ]
    current = [
        _course(
            "1001",
            apply_start="2026-01-01",
            apply_end="2026-01-31",
            start="2026-02-01",
            end="2026-02-28",
        )
    ]
    rows, _, meta = _collect(previous, current)
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["detail_pages"] == 0
    assert meta["configured_collection_error"] == ""


@pytest.mark.skipif(
    os.getenv("RUN_INCHEON_YEONSU_LIVE_TEST") != "1",
    reason="set RUN_INCHEON_YEONSU_LIVE_TEST=1 for the exact live census",
)
def test_live_exact_yeonsu_snapshot() -> None:
    rows, parser, meta = yeonsu.collect_incheon_yeonsu_education(
        _target(),
        today="2026-07-22",
        timeout=45,
        max_pages=220,
        detail_limit=700,
        max_requests=1100,
        max_workers=8,
    )
    assert parser == yeonsu.YEONSU_PARSER
    assert meta["configured_collection_error"] == ""
    assert meta["previous_year_source_rows"] == 2208
    assert meta["previous_year_data_pages"] == 184
    assert meta["previous_year_current_count"] == 0
    assert meta["current_future_source_rows"] == 1749
    assert meta["current_future_data_pages"] == 146
    assert meta["source_rows"] == 3957
    assert meta["current_source_count"] == 585
    assert meta["audited_reversed_period_count"] == 5
    assert meta["audited_reversed_period_ids"] == [
        "42584",
        "43737",
        "43738",
        "43742",
        "43749",
    ]
    assert len(rows) == 580
    assert meta["returned_count"] == 580
    assert meta["status_counts"] == {"OPEN": 317, "CLOSED": 263}
    assert meta["source_status_counts"] == {
        "신청마감": 268,
        "방문접수중": 290,
        "대기접수중": 18,
        "접수가능": 9,
    }
    assert meta["branch_counts"] == {
        "동춘1동 주민자치센터": 26,
        "동춘2동 주민자치센터": 24,
        "동춘3동 주민자치센터": 31,
        "상생교류센터 2층 프로그램1실": 1,
        "선학동 주민자치센터": 28,
        "송도1동 주민자치센터": 42,
        "송도2동 주민자치센터": 59,
        "송도3동 주민자치센터": 54,
        "송도4동 주민자치센터": 47,
        "송도5동 주민자치센터": 54,
        "연수1동 주민자치센터": 40,
        "연수2동 주민자치센터": 35,
        "연수3동 주민자치센터": 30,
        "연수구청 교육지원과": 1,
        "옥련1동 주민자치센터": 16,
        "옥련2동 주민자치센터": 35,
        "청학동 안골창작플랫폼": 9,
        "청학동 주민자치센터": 47,
        "청학동 청능마을": 1,
    }
    assert meta["branch_count"] == 19
    assert meta["application_control_count"] == 9
    assert meta["application_anchor_count"] == 18
    assert meta["offline_application_count"] == 290
    assert meta["waitlist_information_only_count"] == 18
    assert meta["pages"] == 330
    assert meta["list_requests"] == 336
    assert meta["detail_pages"] == 585
    assert meta["network_requests"] == 921
    assert meta["network_retry_count"] == 0
    assert meta["privacy_violations"] == 0
    assert meta["snapshot_complete"] is True
