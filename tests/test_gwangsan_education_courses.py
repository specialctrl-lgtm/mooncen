from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_gwangsan as gwangsan


@dataclass(frozen=True)
class Target:
    provider: str = gwangsan.GWANGSAN_PROVIDER
    name: str = "광산 평생학습포털 배우랑께"
    branch: str = gwangsan.GWANGSAN_MUNICIPALITY_NAME
    url: str = gwangsan.GWANGSAN_URL


class DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _items(
    total: int = gwangsan.GWANGSAN_PAGE_SIZE + 2,
    current_count: int = 2,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in range(total):
        current = index < current_count
        identity = str(900 + index)
        status = "접수중" if index == 0 else ("접수대기" if current else "접수마감")
        result.append(
            {
                "identity": identity,
                "title": f"광산 교육 {identity}",
                "category": "인문교양교육" if index % 2 == 0 else "문화예술교육",
                "dong": "송정1동" if index % 2 == 0 else "첨단1동",
                "branch": "교육도서관과 평생학습팀" if index % 2 == 0 else "광산구청소년수련관",
                "status": status,
                "education_status": "교육예정" if current else "교육완료",
                "apply_start": "2099.07.01" if current else "2020.01.01",
                "apply_end": "2099.07.31" if current else "2020.01.31",
                "start": "2099.08.01" if current else "2020.02.01",
                "end": "2099.12.31" if current else "2020.02.28",
                "schedule": "금 10:00 ~ 12:00",
                "target": "성인",
                "active": index == 0,
            }
        )
    return result


def _href(item: dict[str, Any], page: int) -> str:
    return (
        f"?act=view&amp;id={item['identity']}&amp;signReceiptState="
        f"&amp;searchMoney=&amp;AgencyClassification=&amp;pageIndex={page}"
        "&amp;searchCondition=&amp;searchEducationTime=&amp;searchAgencyId="
        "&amp;searchKeyword="
    )


def _list_page(
    items: list[dict[str, Any]],
    *,
    total: int,
    page: int,
    pages: int,
    bad_headers: bool = False,
    bad_branch: bool = False,
    unknown_status: bool = False,
) -> str:
    headers = list(gwangsan._LIST_HEADERS)
    if bad_headers:
        headers[-1] = "잘못된 요금"
    rows: list[str] = []
    for index, item in enumerate(items):
        branch = item["branch"] if bad_branch and index == 0 else f"[{item['dong']}] {item['branch']}"
        status = "알수없음" if unknown_status and index == 0 else item["status"]
        href = _href(item, page)
        rows.append(
            f"""
            <tr>
              <td><span class="tag_01">{status}</span><span>{item['education_status']}</span></td>
              <td class="tl"><ul class="c-title">
                <li>{item['category']}</li><li><a href="{href}">{item['title']}</a></li>
                <li>{branch}</li>
              </ul></td>
              <td class="tl">
                <p><span class="yellow">접수</span> {item['apply_start']} ~ {item['apply_end']}</p>
                <p><span class="red">교육</span> {item['start']} ~ {item['end']}</p>
                <p><span class="green">시간</span> {item['schedule']}</p>
              </td>
              <td>{item['target']}</td><td>3명 / 20명 <small>단체신청수 : 0팀</small></td>
              <td><span>무료</span><span>무료</span></td>
            </tr>
            <tr class="act"><td colspan="6"><a href="{href}">자세히보기</a></td></tr>
            """
        )
    if not rows:
        rows.append('<tr><td colspan="6">검색 결과가 없습니다.</td></tr>')
    pager = (
        f'<div class="pagination"><a href="?pageIndex={pages}">last</a></div>'
        if pages
        else ""
    )
    return f"""
      <html><body><div class="page-info">{total} 건의 강좌가 검색되었습니다.</div>
      <table class="table listtable_3"><thead><tr>
        {''.join(f'<th>{header}</th>' for header in headers)}
      </tr></thead><tbody>{''.join(rows)}</tbody></table>{pager}</body></html>
    """


def _detail_page(
    item: dict[str, Any],
    *,
    wrong_title: bool = False,
    wrong_application: bool = False,
    missing_label: bool = False,
    dual_status: bool = False,
    suppress_application: bool = False,
    detail_capacity: str = "3/20",
) -> str:
    title = "다른 강좌" if wrong_title else item["title"]
    detail_status = (
        item["status"] if item["status"] in {"접수중", "접수대기", "폐강"} else "교육중"
    )
    application = ""
    if item["active"] and not suppress_application:
        identity = "999999" if wrong_application else item["identity"]
        application = (
            f'<a class="btn btn-sign" href="/lecture.cs?act=signRequest&amp;id={identity}">'
            "수강신청</a>"
        )
    detail_rows = [
        ("강좌분류", f"{item['category']} / 시민 프로그램"),
        ("교육기간", f"{item['start'].replace('.', '-')} ~ {item['end'].replace('.', '-')}"),
        ("교육시간", item["schedule"]),
        ("교육장소", f"{item['branch']} 강의실"),
        ("교육대상", item["target"]),
        ("수강료", "무료"),
        ("교육문의", "062-960-6992"),
        ("홈페이지", "-"),
        ("강사", "광산 강사"),
        ("교육내용", f"즐거운 {item['title']}"),
        ("강의계획서", ""),
        ("기타안내", ""),
    ]
    if missing_label:
        detail_rows = [row for row in detail_rows if row[0] != "교육문의"]
    return f"""
      <html><body><div class="list_view lecture"><div class="view_info">
        <h4>{title}<span>{detail_status}</span>{'<span>교육중</span>' if dual_status else ''}</h4>
        <dl><dt>교육기관</dt><dd>{item['branch']}</dd></dl>
        <dl><dt>접수기간</dt><dd>{item['apply_start'].replace('.', '-')} 09:00 ~ {item['apply_end'].replace('.', '-')} 18:00</dd></dl>
        <dl><dt>교육기간</dt><dd>{item['start'].replace('.', '-')} ~ {item['end'].replace('.', '-')}</dd></dl>
        <dl><dt>교육장소</dt><dd>{item['branch']} 강의실</dd></dl>
        <dl><dt>접수방법</dt><dd>온라인접수 (선착순)</dd></dl>
        <dl><dt>신청인원/정원</dt><dd>{detail_capacity}</dd></dl>{application}
      </div></div>
      <table class="res_table"><tbody>
        {''.join(f'<tr><th>{key}</th><td>{value}</td></tr>' for key, value in detail_rows)}
      </tbody></table>
      <table class="res_table"><tbody>
        <tr><th>교육기관</th><td>{item['branch']} 교육기관을 클릭하시면 확인할 수 있습니다.</td></tr>
        <tr><th>전화번호</th><td>062-960-6992</td></tr>
        <tr><th>주소</th><td>광주광역시 광산구 광산로29번길 15</td></tr>
      </tbody></table></body></html>
    """


def _fixture(
    *,
    duplicate_identity: bool = False,
    nonempty_sentinel: bool = False,
    wrong_detail: bool = False,
    wrong_application: bool = False,
    missing_detail_label: bool = False,
    bad_branch: bool = False,
    unknown_status: bool = False,
    dual_detail_status: bool = False,
    missing_open_application: bool = False,
    full_open_without_application: bool = False,
    semantic_duplicate: bool = False,
):
    items = _items()
    if semantic_duplicate:
        second_identity = items[1]["identity"]
        items[1] = {**items[0], "identity": second_identity}
    if duplicate_identity:
        items[1]["identity"] = items[0]["identity"]
    pages = math.ceil(len(items) / gwangsan.GWANGSAN_PAGE_SIZE)
    list_pages: dict[int, str] = {}
    for page in range(1, pages + 1):
        start = (page - 1) * gwangsan.GWANGSAN_PAGE_SIZE
        list_pages[page] = _list_page(
            items[start : start + gwangsan.GWANGSAN_PAGE_SIZE],
            total=len(items),
            page=page,
            pages=pages,
            bad_branch=bad_branch and page == 1,
            unknown_status=unknown_status and page == 1,
        )
    sentinel_items = [items[0]] if nonempty_sentinel else []
    list_pages[pages + 1] = _list_page(
        sentinel_items,
        total=len(items),
        page=pages + 1,
        pages=pages,
    )
    details = {
        item["identity"]: _detail_page(
            item,
            wrong_title=wrong_detail and index == 0,
            wrong_application=wrong_application and index == 0,
            missing_label=missing_detail_label and index == 0,
            dual_status=dual_detail_status and index == 0,
            suppress_application=(
                (missing_open_application or full_open_without_application)
                and index == 0
            ),
            detail_capacity=(
                "20/20" if full_open_without_application and index == 0 else "3/20"
            ),
        )
        for index, item in enumerate(items[:2])
    }
    calls: list[str] = []
    sessions: list[DummySession] = []

    def make_session() -> DummySession:
        current = DummySession()
        sessions.append(current)
        return current

    def fetch(_session: DummySession, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        calls.append(url)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if query.get("act") == ["view"]:
            return BeautifulSoup(details[query["id"][0]], "lxml")
        page = int((query.get("pageIndex") or ["1"])[0])
        return BeautifulSoup(list_pages[page], "lxml")

    return items, fetch, make_session, calls, sessions


def test_collects_complete_pages_sentinel_and_current_details() -> None:
    items, fetch, make_session, calls, sessions = _fixture(dual_detail_status=True)

    rows, parser, meta = gwangsan.collect_gwangsan_education_courses(
        Target(),
        timeout=7,
        max_pages=3,
        detail_limit=2,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
        dedupe_rows=lambda values: values,
    )

    assert parser == gwangsan.GWANGSAN_PARSER
    assert len(rows) == 2
    assert rows[0]["provider_course_id"] == f"{gwangsan.GWANGSAN_PROVIDER}:lecture:900"
    assert rows[0]["title"] == items[0]["title"]
    assert rows[0]["branch"] == "교육도서관과 평생학습팀"
    assert rows[0]["raw_fields"]["dong"] == "송정1동"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["application_url"] == gwangsan.gwangsan_application_url("900")
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_type"] == "ONLINE_RESERVATION"
    assert rows[0]["capacity_current"] == 3
    assert rows[0]["capacity_total"] == 20
    assert rows[1]["status"] == "SCHEDULED"
    assert rows[1]["reservation_available"] is False
    assert meta["source_total"] == gwangsan.GWANGSAN_PAGE_SIZE + 2
    assert meta["source_rows"] == gwangsan.GWANGSAN_PAGE_SIZE + 2
    assert meta["required_list_requests"] == 3
    assert meta["page_counts"] == {
        1: gwangsan.GWANGSAN_PAGE_SIZE,
        2: 2,
        3: 0,
    }
    assert meta["expired_count"] == gwangsan.GWANGSAN_PAGE_SIZE
    assert meta["current_count"] == 2
    assert meta["detail_pages"] == 2
    assert meta["list_request_count"] == 3
    assert meta["detail_request_count"] == 2
    assert meta["request_count"] == 5
    assert meta["detail_workers"] == 2
    assert meta["session_count"] == 3
    assert meta["branch_count"] == 2
    assert meta["dong_count"] == 2
    assert meta["duplicate_count"] == 0
    assert meta["duplicate_url_count"] == 0
    assert meta["date_correction_count"] == 0
    assert meta["snapshot_complete"] is True
    assert calls[:3] == [
        gwangsan.gwangsan_list_url(1),
        gwangsan.gwangsan_list_url(2),
        gwangsan.gwangsan_list_url(3),
    ]
    assert len(calls) == 5
    assert len(sessions) == 3
    assert all(current.closed for current in sessions)


def test_caps_and_nonempty_sentinel_fail_closed() -> None:
    _items_value, fetch, make_session, calls, _sessions = _fixture()
    rows, _parser, meta = gwangsan.collect_gwangsan_education_courses(
        Target(), timeout=7, max_pages=2, detail_limit=20,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert calls == [gwangsan.gwangsan_list_url(1)]
    assert meta["source_cap_reached"] is True
    assert "2 of 3 required list requests" in meta["configured_collection_error"]

    _items_value, fetch, make_session, _calls, _sessions = _fixture(
        nonempty_sentinel=True
    )
    rows, _parser, meta = gwangsan.collect_gwangsan_education_courses(
        Target(), timeout=7, max_pages=3, detail_limit=20,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel page is not empty" in meta["configured_collection_error"]


def test_duplicate_and_malformed_list_rows_fail_closed() -> None:
    _items_value, fetch, make_session, _calls, _sessions = _fixture(
        duplicate_identity=True
    )
    rows, _parser, meta = gwangsan.collect_gwangsan_education_courses(
        Target(), timeout=7, max_pages=3, detail_limit=20,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert meta["duplicate_count"] == 1

    for fixture_args, expected in (
        ({"bad_branch": True}, "malformed dong/agency branch"),
        ({"unknown_status": True}, "unknown source status"),
    ):
        _items_value, fetch, make_session, _calls, _sessions = _fixture(
            **fixture_args
        )
        rows, _parser, meta = gwangsan.collect_gwangsan_education_courses(
            Target(), timeout=7, max_pages=3, detail_limit=20,
            fetcher=fetch, session_factory=make_session, today="2099-07-19"
        )
        assert rows == []
        assert expected in meta["configured_collection_error"]


def test_detail_contract_and_dedupe_fail_closed() -> None:
    for fixture_args, expected in (
        ({"wrong_detail": True}, "detail/list title mismatch"),
        ({"wrong_application": True}, "application URL contract changed"),
        ({"missing_detail_label": True}, "missing detail labels"),
    ):
        _items_value, fetch, make_session, _calls, _sessions = _fixture(
            **fixture_args
        )
        rows, _parser, meta = gwangsan.collect_gwangsan_education_courses(
            Target(), timeout=7, max_pages=3, detail_limit=2,
            fetcher=fetch, session_factory=make_session, today="2099-07-19"
        )
        assert rows == []
        assert expected in meta["configured_collection_error"]

    _items_value, fetch, make_session, _calls, _sessions = _fixture()
    rows, _parser, meta = gwangsan.collect_gwangsan_education_courses(
        Target(), timeout=7, max_pages=3, detail_limit=2,
        fetcher=fetch, session_factory=make_session, today="2099-07-19",
        dedupe_rows=lambda values: values[:1],
    )
    assert rows == []
    assert "dedupe changed canonical row count" in meta["configured_collection_error"]


def test_exact_source_duplicate_keeps_newer_official_identity() -> None:
    _items_value, fetch, make_session, _calls, _sessions = _fixture(
        semantic_duplicate=True
    )
    rows, _parser, meta = gwangsan.collect_gwangsan_education_courses(
        Target(), timeout=7, max_pages=3, detail_limit=2,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert len(rows) == 1
    assert rows[0]["provider_course_id"].endswith(":lecture:901")
    assert meta["current_count"] == 2
    assert meta["deduplicated_current_count"] == 1
    assert meta["returned_count"] == 1
    assert meta["semantic_candidate_duplicate_count"] == 1
    assert meta["semantic_duplicate_count"] == 1
    assert meta["semantic_duplicate_groups"] == [
        {"kept": "901", "removed": ["900"]}
    ]


def test_full_open_course_without_application_button_is_not_fabricated() -> None:
    _items_value, fetch, make_session, _calls, _sessions = _fixture(
        full_open_without_application=True
    )
    rows, _parser, meta = gwangsan.collect_gwangsan_education_courses(
        Target(), timeout=7, max_pages=3, detail_limit=2,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert len(rows) == 2
    assert meta["snapshot_complete"] is True
    assert rows[0]["capacity_current"] == 20
    assert rows[0]["capacity_total"] == 20
    assert rows[0]["application_type"] == "ONLINE_RESERVATION"
    assert rows[0]["reservation_available"] is False
    assert "application_url" not in rows[0]
    assert rows[0]["raw_fields"]["detail_capacity_full"] is True
    assert rows[0]["raw_fields"]["application_link_suppressed_full"] is True

    _items_value, fetch, make_session, _calls, _sessions = _fixture(
        missing_open_application=True
    )
    rows, _parser, meta = gwangsan.collect_gwangsan_education_courses(
        Target(), timeout=7, max_pages=3, detail_limit=2,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "open online course has no application URL" in (
        meta["configured_collection_error"]
    )


def test_detail_limit_and_managed_injection_fail_closed() -> None:
    _items_value, fetch, make_session, calls, _sessions = _fixture()
    rows, _parser, meta = gwangsan.collect_gwangsan_education_courses(
        Target(), timeout=7, max_pages=3, detail_limit=1,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert len(calls) == 3
    assert meta["source_cap_reached"] is True
    assert "1 of 2 required current/future details" in meta["configured_collection_error"]

    rows, _parser, meta = gwangsan.collect_gwangsan_education_courses(Target())
    assert rows == []
    assert "managed fetcher and session_factory" in meta["configured_collection_error"]


def test_exact_target_url_helpers_and_exclusions() -> None:
    assert gwangsan.is_gwangsan_target(Target()) is True
    for value in (
        Target(url=gwangsan.GWANGSAN_LIST_URL),
        Target(url="http://edu.gwangsan.go.kr/"),
        Target(url="https://edu.gwangsan.go.kr/?page=1"),
        Target(url="https://user@edu.gwangsan.go.kr/"),
        Target(provider="WRONG"),
    ):
        assert gwangsan.is_gwangsan_target(value) is False
    assert gwangsan.GWANGSAN_PAGE_SIZE == 1000
    assert gwangsan.GWANGSAN_DETAIL_WORKERS == 2
    assert gwangsan.gwangsan_list_url(1) == (
        f"{gwangsan.GWANGSAN_LIST_URL}?pageUnit=1000"
    )
    assert gwangsan.gwangsan_list_url(2).endswith(
        "?pageUnit=1000&pageIndex=2"
    )
    assert gwangsan.gwangsan_list_url(0) == ""
    assert gwangsan.gwangsan_detail_url("123").endswith("?act=view&id=123")
    assert gwangsan.gwangsan_application_url("123").endswith(
        "?act=signRequest&id=123"
    )
    assert gwangsan.gwangsan_detail_url("../123") == ""
    assert gwangsan.GWANGSAN_CANDIDATE_ID == "MUNI_IR_35D0DAC7F15D"
    assert gwangsan.GWANGSAN_NOTICE_URL in gwangsan.GWANGSAN_NON_COURSE_URLS
    assert gwangsan.GWANGSAN_DISCOVERY_URL in gwangsan.GWANGSAN_NON_COURSE_URLS
    assert gwangsan.GWANGSAN_CONTACT_URL in gwangsan.GWANGSAN_NON_COURSE_URLS
    assert "https://edu.gwangsan.go.kr/lecture.cs?m=3" in gwangsan.GWANGSAN_DUPLICATE_ALIAS_URLS


def test_exact_historical_date_corrections_only() -> None:
    start, end, corrected = gwangsan._date_range(
        "8255", "education", "2424.09.28 ~ 2424.09.28"
    )
    assert (start.isoformat(), end.isoformat(), corrected) == (
        "2024-09-28",
        "2024-09-28",
        True,
    )
    start, end, corrected = gwangsan._date_range(
        "2986", "application", "2022.09.14 ~ 6022.09.16"
    )
    assert (start.isoformat(), end.isoformat(), corrected) == (
        "2022-09-14",
        "2022-09-16",
        True,
    )
    assert gwangsan._date_range(
        "OTHER", "education", "2424.09.28 ~ 2424.09.28"
    ) == (None, None, False)
    start, end, corrected = gwangsan._date_range(
        "8255", "education", "2024-09-28 ~ 2024-09-28"
    )
    assert (start.isoformat(), end.isoformat(), corrected) == (
        "2024-09-28",
        "2024-09-28",
        False,
    )
