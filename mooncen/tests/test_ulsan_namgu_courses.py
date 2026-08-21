from __future__ import annotations

from datetime import date
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_ulsan_namgu as municipal


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target(source: Any) -> dict[str, Any]:
    return {
        "provider": source.provider,
        "url": source.list_url,
        "name": "울산광역시 남구 교육강좌",
        "branch": "울산광역시 남구",
        "extra": {
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "source_group": "municipal_reservation",
        },
    }


def _form(source: Any, page: int) -> str:
    return f"""
    <form method="post" action="{source.list_path}">
      <input name="bbsId" value="{source.bbs_id}" />
      <input name="pageIndex" value="{page}" />
    </form>
    """


def _lifelong_row(
    number: int,
    identity: int,
    title: str,
    *,
    period: str = "2020.01.01 ~ 01.31",
    apply_period: str = "2019-12-01 ~ 12-15",
    status: str = "마감",
    capacity: str = "5 / 10명",
    room: str = "남구 평생학습교실",
) -> str:
    return f"""
    <tr>
      <td>{number}</td>
      <td><a href="/edu/board/edu999Lecture/view.do?nttId={identity}">{title}</a></td>
      <td>{period}</td><td>{apply_period}</td><td>{capacity}</td><td>{room}</td>
      <td><img alt="온라인"/><img alt="전화"/></td>
      <td><img alt="{status}"/></td>
    </tr>
    """


def _library_row(
    number: int,
    identity: int,
    title: str,
    *,
    branch: str = "신복",
    period: str = "2020.01.01 ~ 01.31",
    apply_period: str = "2019-12-01 ~ 12-15",
    status: str = "마감",
    capacity: str = "5 / 10명",
    room: str = "2층 시청각실",
    target: str = "성인",
) -> str:
    return f"""
    <tr>
      <td>{branch}</td><td>{number}</td>
      <td><a href="/library/board/libLecture/view.do?nttId={identity}">{title}</a></td>
      <td>{period}</td><td>{apply_period}</td><td>{capacity}</td><td>{room}</td>
      <td>{target}</td><td><img alt="{status}"/></td>
    </tr>
    """


def _list_page(source: Any, rows: str, *, total: int, current: int, pages: int) -> str:
    if source == municipal.LIFELONG_SOURCE:
        summary = f"<p>Total : {total} 건, 현재 : {current}page / {pages}page</p>"
    else:
        summary = f"<p>총게시물 :{total} / 페이지 :{current}/{pages}</p>"
    headers = "".join(f"<th>{header}</th>" for header in source.headers)
    return f"""
    <html><body>{_form(source, current)}{summary}
      <table><tr>{headers}</tr>{rows}</table>
    </body></html>
    """


def _empty_sentinel_row() -> str:
    return (
        f'<tr><td colspan="{municipal.EMPTY_SENTINEL_COLSPAN}">'
        f"{municipal.EMPTY_SENTINEL_TEXT}</td></tr>"
    )


def _lifelong_detail(
    identity: int,
    title: str,
    *,
    period: str = "2099.08.01 ~ 2099.08.30",
    apply_period: str = "2099-07-01 09:00 ~ 2099-07-20 18:00",
    status: str = "마감",
    capacity: str = "5 / 10명",
    room: str = "남구 평생학습교실",
    content: str = "시민을 위한 상세 교육내용",
) -> str:
    is_open = status == "접수하기"
    onclick = (
        f"goNameCheck('edu999Lecture-{identity}', 'edu');return false;"
        if is_open
        else "return false;"
    )
    application_script = (
        '<script src="/edu/js/EgovCommon.js"></script>'
        if is_open
        else ""
    )
    return f"""
    <html><body>
      <form name="frm"><input name="nttId" value="{identity}"/></form>
      <table>
        <tr><th>학습방명</th><td>시민교양</td></tr>
        <tr><th>강좌명</th><td>{title}</td></tr>
        <tr><th>교육기관</th><td>남구청</td></tr>
        <tr><th>교육대상</th><td>성인</td></tr>
        <tr><th>교육방법</th><td>오프라인</td></tr>
        <tr><th>강사</th><td>김강사</td></tr>
        <tr><th>접수방법</th><td>온라인,전화</td></tr>
        <tr><th>접수 / 모집인원</th><td>{capacity}</td></tr>
        <tr><th>모집기간</th><td>{apply_period}</td></tr>
        <tr><th>접수여부</th><td>접수진행</td></tr>
        <tr><th>교육기간</th><td>{period}</td></tr>
        <tr><th>교육요일</th><td>화,목</td></tr>
        <tr><th>교육시간</th><td>10:00 ~ 12:00</td></tr>
        <tr><th>교육장소</th><td>{room}</td></tr>
        <tr><th>문의전화</th><td>052-226-5683</td></tr>
        <tr><th>교육내용</th><td>{content}</td></tr>
      </table>
      <button onclick="{onclick}">{status}</button>
      {application_script}
    </body></html>
    """


def _library_detail(
    identity: int,
    title: str,
    *,
    branch: str,
    period: str,
    capacity: str,
    room: str,
    target: str,
    open_application: bool,
    wait_capacity: str = "1 / 5명",
) -> str:
    application = (
        f"""<a onclick="location.href='/library/board/libLecture-{identity}/write.do?nttDiv=';return false;">접수하기</a>"""
        if open_application
        else ""
    )
    return f"""
    <html><body><table>
      <tr><th>강좌명</th><td>{title}</td></tr>
      <tr><th>도서관</th><td>{branch}</td></tr>
      <tr><th>강사명</th><td>이강사</td></tr>
      <tr><th>대상</th><td>{target}</td></tr>
      <tr><th>신청인원 / 정원</th><td>{capacity}</td></tr>
      <tr><th>대기인원 / 정원</th><td>{wait_capacity}</td></tr>
      <tr><th>강의기간</th><td>{period}</td></tr>
      <tr><th>강의시간</th><td>토 10:00 ~ 12:00</td></tr>
      <tr><th>교육장소</th><td>{room}</td></tr>
      <tr><th>재료비</th><td>없음 / 준비물: 필기구</td></tr>
      <tr><th>교육내용</th><td>도서관 상세 교육내용</td></tr>
    </table>{application}</body></html>
    """


class FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _collect(
    source: Any,
    list_pages: dict[int, str],
    details: dict[str, str],
    *,
    max_pages: int = 10,
    detail_limit: int = 20,
    dedupe_rows: Any = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], list[str], FakeSession]:
    fetched: list[str] = []
    session = FakeSession()

    def fetcher(_session: Any, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        fetched.append(url)
        parsed = urlparse(url)
        if parsed.path == source.list_path:
            page = int((parse_qs(parsed.query).get("page") or ["1"])[0])
            return _soup(list_pages[page])
        assert parsed.path == source.detail_path
        identity = parse_qs(parsed.query)["nttId"][0]
        value = details[identity]
        if value == "__FAIL__":
            raise RuntimeError("fixture detail outage")
        return _soup(value)

    rows, parser, meta = municipal.collect_ulsan_namgu_courses(
        _target(source),
        timeout=7,
        max_pages=max_pages,
        detail_limit=detail_limit,
        fetcher=fetcher,
        session_factory=lambda: session,
        today=date(2026, 7, 20),
        dedupe_rows=dedupe_rows,
    )
    return rows, parser, meta, fetched, session


def _lifelong_fixture() -> tuple[dict[int, str], dict[str, str]]:
    rows = [
        _lifelong_row(
            11,
            1011,
            "현재 시민강좌",
            period="2099.08.01 ~ 08.30",
            apply_period="2099-07-01 ~ 07-20",
            status="접수하기",
        )
    ]
    rows.extend(
        _lifelong_row(number, 1000 + number, f"만료 강좌 {number}")
        for number in range(10, 1, -1)
    )
    page_one = _list_page(
        municipal.LIFELONG_SOURCE, "".join(rows), total=11, current=1, pages=2
    )
    page_two = _list_page(
        municipal.LIFELONG_SOURCE,
        _lifelong_row(1, 1001, "날짜 없는 과거 강좌", period=""),
        total=11,
        current=2,
        pages=2,
    )
    sentinel = _list_page(
        municipal.LIFELONG_SOURCE,
        _empty_sentinel_row(),
        total=11,
        current=3,
        pages=2,
    )
    details = {
        "1011": _lifelong_detail(1011, "현재 시민강좌", status="접수하기"),
        "1001": _lifelong_detail(
            1001,
            "날짜 없는 과거 강좌",
            period="~",
            apply_period="2020-01-01 09:00 ~ 2020-01-10 18:00",
        ),
    }
    return {1: page_one, 2: page_two, 3: sentinel}, details


def _library_fixture() -> tuple[dict[int, str], dict[str, str]]:
    rows = [
        _library_row(
            11,
            2011,
            "현재 신복 강좌",
            period="2099.08.01 ~ 08.30",
            apply_period="2099-07-01 ~ 07-31",
            status="접수",
            capacity="5 / 12명",
        ),
        _library_row(
            10,
            2010,
            "향후 옥현 강좌",
            branch="옥현",
            period="2099.09.01 ~ 09.30",
            apply_period="2099-08-01 ~ 08-31",
            status="접수전",
            capacity="0 / 10명",
            room="2층 다목적실",
            target="초등",
        ),
    ]
    rows.extend(
        _library_row(number, 2000 + number, f"만료 도서관 강좌 {number}")
        for number in range(9, 1, -1)
    )
    page_one = _list_page(
        municipal.LIBRARY_SOURCE, "".join(rows), total=11, current=1, pages=2
    )
    page_two = _list_page(
        municipal.LIBRARY_SOURCE,
        _library_row(1, 2001, "마지막 만료 강좌"),
        total=11,
        current=2,
        pages=2,
    )
    sentinel = _list_page(
        municipal.LIBRARY_SOURCE,
        _empty_sentinel_row(),
        total=11,
        current=3,
        pages=2,
    )
    details = {
        "2011": _library_detail(
            2011,
            "현재 신복 강좌",
            branch="신복도서관",
            period="2099.08.01 ~ 2099.08.30",
            capacity="5 / 12명",
            room="2층 시청각실",
            target="성인",
            open_application=True,
        ),
        "2010": _library_detail(
            2010,
            "향후 옥현 강좌",
            branch="옥현도서관",
            period="2099.09.01 ~ 2099.09.30",
            capacity="0 / 10명",
            room="2층 다목적실",
            target="초등",
            open_application=False,
        ),
    }
    return {1: page_one, 2: page_two, 3: sentinel}, details


def test_lifelong_complete_snapshot_resolves_current_and_undated_history() -> None:
    pages, details = _lifelong_fixture()
    rows, parser, meta, fetched, session = _collect(
        municipal.LIFELONG_SOURCE, pages, details
    )

    assert parser == municipal.ULSAN_NAMGU_LIFELONG_PARSER
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == municipal.ULSAN_NAMGU_LIFELONG_PROVIDER
    assert row["provider_course_id"].endswith(":lecture:1011")
    assert row["raw_url"].endswith("edu999Lecture/view.do?nttId=1011")
    assert row["branch"] == "울산광역시 남구 · 남구청"
    assert row["branch_code"].endswith(":남구청")
    assert row["municipality_code"] == "3114000000"
    assert row["period"] == "2099-08-01 ~ 2099-08-30"
    assert row["apply_period"] == "2099-07-01 ~ 2099-07-20"
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["application_url"].endswith("edu999Lecture/view.do?nttId=1011")
    assert (
        row["raw_fields"]["application_control_contract"]
        == "identity_bound_name_check_gate"
    )
    assert row["raw_fields"]["authentication_gate_fetched"] is False
    assert row["schedule_raw"] == "화,목 10:00 ~ 12:00"
    assert row["capacity_current"] == 5
    assert row["capacity_total"] == 10
    assert row["target"] == "성인"
    assert row["category"] == "시민교양"
    assert row["fee"] == "요금 별도 안내"
    assert row["material_fee"] == ""
    assert row["description"] == row["title"]
    assert "교육내용" not in row["raw_fields"]["detail_fields"]

    assert meta["source_rows"] == 11
    assert meta["declared_source_rows"] == 11
    assert meta["total_pages"] == 2
    assert meta["required_list_requests"] == 3
    assert meta["list_requests"] == 3
    assert meta["detail_candidates"] == 2
    assert meta["detail_pages"] == 2
    assert meta["expired_count"] == 10
    assert meta["undated_count"] == 1
    assert meta["undated_expired_count"] == 1
    assert meta["current_count"] == 1
    assert meta["duplicate_count"] == 0
    assert meta["duplicate_url_count"] == 0
    assert meta["semantic_candidate_duplicate_count"] == 0
    assert meta["empty_sentinel_verified"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert len(fetched) == 5
    assert session.closed is True


def test_lifelong_labeled_costs_are_allowlisted_without_free_text() -> None:
    fee, material_fee, evidence = municipal._lifelong_costs(
        "강사 홍길동 수 강 료: 3만원 계좌 301-0000 "
        "교재,재료비 : 10회 / 47,900원 환불 문의"
    )
    assert fee == "수강료 3만원"
    assert material_fee == "교재·재료비 10회 / 47,900원"
    assert evidence["fee_evidence"] == "수 강 료: 3만원"
    assert evidence["material_fee_evidence"] == "교재,재료비 : 10회 / 47,900원"
    assert "301-0000" not in json.dumps(evidence, ensure_ascii=False)


def test_lifelong_conflicting_labeled_costs_fail_closed() -> None:
    with pytest.raises(municipal.UlsanNamguContractError, match="conflicting 재료비"):
        municipal._lifelong_costs("재료비: 5,000원 재료비: 10,000원")


def test_library_complete_snapshot_preserves_five_branch_model_and_application() -> None:
    pages, details = _library_fixture()
    rows, parser, meta, _fetched, session = _collect(
        municipal.LIBRARY_SOURCE, pages, details
    )

    assert parser == municipal.ULSAN_NAMGU_LIBRARY_PARSER
    assert len(rows) == 2
    by_title = {row["title"]: row for row in rows}
    open_row = by_title["현재 신복 강좌"]
    assert open_row["branch"] == "울산광역시 남구 · 신복도서관"
    assert open_row["status"] == "OPEN"
    assert open_row["reservation_available"] is True
    assert open_row["application_url"] == (
        "https://www.ulsannamgu.go.kr/library/board/"
        "libLecture-2011/write.do?nttDiv="
    )
    assert open_row["waitlist_current"] == 1
    assert open_row["waitlist_total"] == 5
    assert open_row["material_note"] == "없음 / 준비물: 필기구"

    scheduled = by_title["향후 옥현 강좌"]
    assert scheduled["branch"] == "울산광역시 남구 · 옥현도서관"
    assert scheduled["status"] == "SCHEDULED"
    assert scheduled["reservation_available"] is False
    assert scheduled["application_url"] == ""
    assert scheduled["target"] == "초등"

    assert meta["source_kind"] == "library"
    assert meta["candidate_id"] == municipal.ULSAN_NAMGU_LIBRARY_CANDIDATE_ID
    assert meta["source_rows"] == 11
    assert meta["expired_count"] == 9
    assert meta["current_count"] == 2
    assert meta["reservation_discovery_links"] == 1
    assert meta["branch_count"] == 2
    assert meta["status_counts"] == {"OPEN": 1, "SCHEDULED": 1}
    assert meta["snapshot_complete"] is True
    assert session.closed is True


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (municipal.ULSAN_NAMGU_LIFELONG_LIST_URL, True),
        (municipal.ULSAN_NAMGU_LIBRARY_LIST_URL, True),
        ("http://www.ulsannamgu.go.kr/edu/board/edu999Lecture/list.do", False),
        ("https://ulsannamgu.go.kr/edu/board/edu999Lecture/list.do", False),
        ("https://evil.www.ulsannamgu.go.kr/edu/board/edu999Lecture/list.do", False),
        ("https://www.ulsannamgu.go.kr:443/edu/board/edu999Lecture/list.do", False),
        ("https://www.ulsannamgu.go.kr/edu/board/edu999Lecture/list.do?page=1", False),
        ("https://www.ulsannamgu.go.kr/library/board/libLecture/view.do", False),
        ("https://www.ulsannamgu.go.kr/library/board/libLecture/list.do#x", False),
    ],
)
def test_exact_source_routes(url: str, expected: bool) -> None:
    actual = municipal.is_ulsan_namgu_lifelong_url(url) or municipal.is_ulsan_namgu_library_url(url)
    assert actual is expected


def test_target_binds_provider_and_url() -> None:
    assert municipal.ULSAN_NAMGU_LIFELONG_CANDIDATE_ID == "MUNI_IR_A4B63464C899"
    assert municipal.ULSAN_NAMGU_LIBRARY_CANDIDATE_ID == "MUNI_IR_0EA9080F8206"
    assert municipal.is_ulsan_namgu_target(_target(municipal.LIFELONG_SOURCE)) is True
    wrong = _target(municipal.LIFELONG_SOURCE)
    wrong["provider"] = municipal.ULSAN_NAMGU_LIBRARY_PROVIDER
    assert municipal.is_ulsan_namgu_target(wrong) is False


def test_collector_requires_managed_network_injection() -> None:
    rows, _parser, meta = municipal.collect_ulsan_namgu_courses(
        _target(municipal.LIFELONG_SOURCE)
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "managed fetcher" in meta["configured_collection_error"]


def test_list_cap_fails_closed_before_partial_page_collection() -> None:
    pages, details = _lifelong_fixture()
    rows, _parser, meta, fetched, session = _collect(
        municipal.LIFELONG_SOURCE,
        pages,
        details,
        max_pages=2,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["list_requests"] == 1
    assert meta["detail_attempts"] == 0
    assert len(fetched) == 1
    assert session.closed is True


def test_detail_cap_fails_closed_without_partial_details() -> None:
    pages, details = _library_fixture()
    rows, _parser, meta, fetched, _session = _collect(
        municipal.LIBRARY_SOURCE,
        pages,
        details,
        detail_limit=1,
    )
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_candidates"] == 2
    assert meta["detail_attempts"] == 0
    assert len(fetched) == 3


def test_detail_contract_failure_discards_entire_snapshot() -> None:
    pages, details = _library_fixture()
    details["2011"] = details["2011"].replace("현재 신복 강좌", "다른 제목")
    rows, _parser, meta, _fetched, _session = _collect(
        municipal.LIBRARY_SOURCE, pages, details
    )
    assert rows == []
    assert meta["detail_errors"] == 1
    assert meta["snapshot_complete"] is False
    assert "detail title mismatch" in meta["configured_collection_error"]


def test_nonempty_overrun_page_discards_entire_snapshot() -> None:
    pages, details = _lifelong_fixture()
    pages[3] = _list_page(
        municipal.LIFELONG_SOURCE,
        _lifelong_row(1, 1001, "경계 밖 복제 행", period=""),
        total=11,
        current=3,
        pages=2,
    )
    rows, _parser, meta, _fetched, _session = _collect(
        municipal.LIFELONG_SOURCE, pages, details
    )
    assert rows == []
    assert meta["empty_sentinel_verified"] is False
    assert "post-boundary page" in meta["configured_collection_error"]


def test_malformed_official_overrun_sentinel_discards_entire_snapshot() -> None:
    pages, details = _lifelong_fixture()
    pages[3] = _list_page(
        municipal.LIFELONG_SOURCE,
        f'<tr><td colspan="7">{municipal.EMPTY_SENTINEL_TEXT}</td></tr>',
        total=11,
        current=3,
        pages=2,
    )
    rows, _parser, meta, _fetched, _session = _collect(
        municipal.LIFELONG_SOURCE, pages, details
    )
    assert rows == []
    assert meta["empty_sentinel_verified"] is False
    assert "post-boundary page" in meta["configured_collection_error"]


def test_expired_closed_zero_capacity_history_does_not_poison_current_snapshot() -> None:
    pages, details = _lifelong_fixture()
    original = _lifelong_row(10, 1010, "만료 강좌 10")
    anomalous = _lifelong_row(10, 1010, "만료 강좌 10", capacity="2 / 0명")
    assert original in pages[1]
    pages[1] = pages[1].replace(original, anomalous)

    rows, _parser, meta, _fetched, _session = _collect(
        municipal.LIFELONG_SOURCE, pages, details
    )

    assert len(rows) == 1
    assert meta["source_rows"] == 11
    assert meta["expired_count"] == 10
    assert meta["configured_collection_error"] == ""


def test_current_zero_capacity_row_remains_fail_closed() -> None:
    pages, details = _lifelong_fixture()
    original = _lifelong_row(
        11,
        1011,
        "현재 시민강좌",
        period="2099.08.01 ~ 08.30",
        apply_period="2099-07-01 ~ 07-20",
        status="접수하기",
    )
    anomalous = _lifelong_row(
        11,
        1011,
        "현재 시민강좌",
        period="2099.08.01 ~ 08.30",
        apply_period="2099-07-01 ~ 07-20",
        status="접수하기",
        capacity="2 / 0명",
    )
    assert original in pages[1]
    pages[1] = pages[1].replace(original, anomalous)

    rows, _parser, meta, _fetched, _session = _collect(
        municipal.LIFELONG_SOURCE, pages, details
    )

    assert rows == []
    assert "capacity total must be positive" in meta["configured_collection_error"]


def test_library_zero_waitlist_capacity_is_valid_but_nonzero_overflow_is_not() -> None:
    pages, details = _library_fixture()
    details["2011"] = _library_detail(
        2011,
        "현재 신복 강좌",
        branch="신복도서관",
        period="2099.08.01 ~ 2099.08.30",
        capacity="5 / 12명",
        room="2층 시청각실",
        target="성인",
        open_application=True,
        wait_capacity="0 / 0명",
    )
    rows, _parser, meta, _fetched, _session = _collect(
        municipal.LIBRARY_SOURCE, pages, details
    )
    assert len(rows) == 2
    row = next(item for item in rows if item["title"] == "현재 신복 강좌")
    assert row["waitlist_current"] == row["waitlist_total"] == 0
    assert meta["configured_collection_error"] == ""

    details["2011"] = details["2011"].replace("0 / 0명", "1 / 0명")
    rows, _parser, meta, _fetched, _session = _collect(
        municipal.LIBRARY_SOURCE, pages, details
    )
    assert rows == []
    assert "capacity total must be positive" in meta["configured_collection_error"]


def test_undated_row_requires_closed_and_expired_application_evidence() -> None:
    pages, details = _lifelong_fixture()
    details["1001"] = _lifelong_detail(
        1001,
        "날짜 없는 과거 강좌",
        period="~",
        apply_period="2099-01-01 09:00 ~ 2099-01-10 18:00",
    )
    rows, _parser, meta, _fetched, _session = _collect(
        municipal.LIFELONG_SOURCE, pages, details
    )
    assert rows == []
    assert meta["undated_expired_count"] == 0
    assert "not provably expired" in meta["configured_collection_error"]


def test_downstream_dedupe_may_not_shrink_complete_snapshot() -> None:
    pages, details = _library_fixture()
    rows, _parser, meta, _fetched, _session = _collect(
        municipal.LIBRARY_SOURCE,
        pages,
        details,
        dedupe_rows=lambda values: values[:1],
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "downstream dedupe" in meta["configured_collection_error"]
