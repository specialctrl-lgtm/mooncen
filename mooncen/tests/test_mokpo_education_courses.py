from __future__ import annotations

from dataclasses import dataclass
import inspect
import math
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_mokpo as mokpo


@dataclass(frozen=True)
class Target:
    provider: str = mokpo.MOKPO_PROVIDER
    name: str = "목포시 평생학습포털 교육과정"
    branch: str = "전남광주통합특별시 목포시"
    url: str = mokpo.MOKPO_URL


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _items(total: int = 17, current_count: int = 16) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in range(total):
        current = index < current_count
        identity = str(200 - index)
        result.append(
            {
                "identity": identity,
                "branch": "상   동" if index % 2 == 0 else "용당1동",
                "title": f"목포 교육 {identity}",
                "instructor": f"강사 {identity}",
                "start": "2099-07-01" if current else "2020-01-01",
                "end": "2099-12-31" if current else "2020-12-31",
                "apply_start": "2099-06-01" if current else "2020-01-01",
                "apply_end": "2099-06-30" if current else "2020-01-31",
                "fee": "20,000원",
                "status": "접수중" if index == 0 else ("교육중" if current else "종료"),
                "schedule": "월,수 10:00~12:00",
                "method": "방문" if index == 0 else "",
            }
        )
    return result


def _pager(last_page: int, page: int) -> str:
    if last_page == 1:
        return ""
    values: list[str] = []
    for number in range(1, last_page + 1):
        if number == page:
            values.append(f'<strong class="pg_current">{number}</strong>')
        else:
            values.append(
                '<a class="pg_page" '
                f'href="./lecture_list_program.php?me_id={mokpo.MOKPO_MENU_ID}&amp;page={number}">'
                f"{number} 페이지</a>"
            )
    if page < last_page:
        values.append(
            '<a class="pg_page pg_end" '
            f'href="./lecture_list_program.php?me_id={mokpo.MOKPO_MENU_ID}&amp;page={last_page}">'
            "맨끝</a>"
        )
    return '<div class="pg_wrap"><span class="pg">' + "".join(values) + "</span></div>"


def _list_page(
    items: list[dict[str, Any]],
    *,
    page: int,
    last_page: int,
    bad_headers: bool = False,
) -> str:
    headers = list(mokpo._LIST_HEADERS)
    if bad_headers:
        headers[-1] = "잘못된 상태"
    rows: list[str] = []
    for item in items:
        page_query = f"&amp;page={page}" if page > 1 else ""
        rows.append(
            f"""
            <tr>
              <td>{item['branch']}</td>
              <td><a href="./lecture_list_view.php?le_id={item['identity']}&amp;me_id={mokpo.MOKPO_MENU_ID}{page_query}">{item['title']}</a></td>
              <td>{item['instructor']}</td>
              <td>{item['start']} ~ {item['end']}</td>
              <td>{item['apply_start']} ~ {item['apply_end']}</td>
              <td>{item['fee']}</td><td>{item['status']}</td>
            </tr>
            """
        )
    if not rows:
        rows.append('<tr><td colspan="7">자료가 없습니다.</td></tr>')
    current = page if page <= last_page else 0
    return f"""
      <html lang="ko"><body>
        <table><thead><tr>{''.join(f'<th>{value}</th>' for value in headers)}</tr></thead>
        <tbody>{''.join(rows)}</tbody></table>
        {_pager(last_page, current)}
      </body></html>
    """


def _detail_page(
    item: dict[str, Any],
    *,
    wrong_title: bool = False,
    omit_key: str = "",
    status_control: bool = False,
) -> str:
    title = "다른 강좌" if wrong_title else item["title"]
    branch = "".join(item["branch"].split())
    values = {
        "프로그램명": "동 주민사랑방 프로그램",
        "강좌명": title,
        "분야": "체육/건강",
        "강의분야": "생활체육",
        "신청방법": item["method"],
        "강좌상태": (
            f'<div class="state lec_ing">{item["status"]}</div>'
            '<a class="reg apply" href="/bbs/login.php?url=">로그인</a>'
            if status_control
            else item["status"]
        ),
        "신청기간": f"{item['apply_start']} ~ {item['apply_end']}",
        "접수/정원": "접수 : 3명 / 정원 : 20명",
        "교육기간": f"{item['start']} ~ {item['end']}",
        "교육일시": item["schedule"],
        "교육대상": "목포시민",
        "강사명": item["instructor"],
        "수강료": "유료 20,000원",
        "재료비": "5,000원",
        "교육장소": f"{branch}행정복지센터",
        "교육기관": branch,
        "강좌소개": "즐거운 주민 교육",
        "강의자료": "",
        "홈페이지": "",
        "문의": "061-270-0000",
        "기타": "필기구 지참",
    }
    rows: list[str] = []
    pairs = list(values.items())
    index = 0
    while index < len(pairs):
        first_key, first_value = pairs[index]
        index += 1
        if first_key == omit_key:
            continue
        cells = f"<th>{first_key}</th><td>{first_value}</td>"
        if index < len(pairs):
            second_key, second_value = pairs[index]
            index += 1
            if second_key != omit_key:
                cells += f"<th>{second_key}</th><td>{second_value}</td>"
        rows.append(f"<tr>{cells}</tr>")
    return '<html><body><table class="td_left"><tbody>' + "".join(rows) + "</tbody></table></body></html>"


def _fixture(
    *,
    total: int = 17,
    current_count: int = 16,
    duplicate_identity: bool = False,
    nonempty_sentinel: bool = False,
    bad_headers_page: int = 0,
    wrong_detail_title: str = "",
    omit_detail_key: str = "",
    semantic_duplicate: bool = False,
):
    items = _items(total=total, current_count=current_count)
    if duplicate_identity and len(items) > 1:
        items[1]["identity"] = items[0]["identity"]
    if semantic_duplicate and len(items) > 1:
        items[1]["title"] = items[0]["title"]
        items[1]["branch"] = items[0]["branch"]
        items[1]["start"] = items[0]["start"]
        items[1]["end"] = items[0]["end"]
        items[1]["schedule"] = items[0]["schedule"]

    last_page = max(1, math.ceil(len(items) / mokpo.MOKPO_PAGE_SIZE))
    pages: dict[int, str] = {}
    for page in range(1, last_page + 1):
        start = (page - 1) * mokpo.MOKPO_PAGE_SIZE
        page_items = items[start : start + mokpo.MOKPO_PAGE_SIZE]
        pages[page] = _list_page(
            page_items,
            page=page,
            last_page=last_page,
            bad_headers=page == bad_headers_page,
        )
    sentinel_items = [items[0]] if nonempty_sentinel and items else []
    pages[last_page + 1] = _list_page(
        sentinel_items, page=last_page + 1, last_page=last_page
    )

    details: dict[str, str] = {}
    for item in items[:current_count]:
        details[item["identity"]] = _detail_page(
            item,
            wrong_title=item["identity"] == wrong_detail_title,
            omit_key=omit_detail_key if item["identity"] == items[0]["identity"] else "",
        )

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
        if parsed.path == mokpo.MOKPO_LIST_PATH:
            page = int((query.get("page") or ["1"])[0])
            return BeautifulSoup(pages[page], "lxml")
        if parsed.path == mokpo.MOKPO_DETAIL_PATH:
            return BeautifulSoup(details[query["le_id"][0]], "lxml")
        raise AssertionError(url)

    return items, pages, details, fetch, make_session, calls, sessions


def test_collects_every_current_course_after_complete_pages_and_details() -> None:
    items, pages, details, fetch, make_session, calls, sessions = _fixture()

    rows, parser, meta = mokpo.collect_mokpo_education_courses(
        Target(),
        timeout=7,
        max_pages=3,
        detail_limit=16,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )

    assert parser == mokpo.MOKPO_PARSER
    assert len(rows) == 16
    row = rows[0]
    assert row["provider_course_id"] == f"{mokpo.MOKPO_PROVIDER}:lecture:200"
    assert row["title"] == "목포 교육 200"
    assert row["branch"] == "상동"
    assert row["branch_code"].startswith("MOKPO_BRANCH_")
    assert row["status"] == "OPEN"
    assert row["category"] == "생활체육"
    assert row["period"] == "2099-07-01 ~ 2099-12-31"
    assert row["apply_period"] == "2099-06-01 ~ 2099-06-30"
    assert row["application_method_raw"] == "방문"
    assert row["reservation_available"] is False
    assert row["application_url"] == mokpo.mokpo_detail_url("200")
    assert row["capacity_current"] == 3
    assert row["capacity_total"] == 20
    assert row["material_fee"] == "5,000원"
    assert row["venue_name"] == "상동행정복지센터"
    assert row["phone"] == "061-270-0000"
    assert row["municipality_code"] == "1211000000"
    assert row["service_group"] == "공공강좌"

    assert meta["declared_pages"] == 2
    assert meta["required_list_requests"] == 3
    assert meta["sentinel_page"] == 3
    assert meta["page_counts"] == {1: 15, 2: 2, 3: 0}
    assert meta["source_rows"] == 17
    assert meta["current_count"] == 16
    assert meta["expired_count"] == 1
    assert meta["detail_attempts"] == 16
    assert meta["detail_pages"] == 16
    assert meta["detail_errors"] == 0
    assert meta["duplicate_count"] == 0
    assert meta["duplicate_url_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["branch_count"] == 2
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["snapshot_complete"] is True
    assert len(calls) == 3 + 16
    assert sessions and sessions[0].closed is True


def test_detail_status_ignores_login_control_text() -> None:
    item = _items(total=1, current_count=1)[0]
    soup = BeautifulSoup(_detail_page(item, status_control=True), "lxml")

    pairs = mokpo._detail_pairs(soup)

    assert pairs is not None
    assert pairs["강좌상태"] == "접수중"


def test_expired_only_catalogue_is_complete_no_current_data() -> None:
    items, pages, details, fetch, make_session, calls, _sessions = _fixture(
        total=1, current_count=0
    )

    rows, _parser, meta = mokpo.collect_mokpo_education_courses(
        Target(),
        timeout=7,
        max_pages=2,
        detail_limit=0,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )

    assert rows == []
    assert meta["source_rows"] == 1
    assert meta["current_count"] == 0
    assert meta["expired_count"] == 1
    assert meta["detail_attempts"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert "no current/future" in meta["no_current_reason"]
    assert calls == [mokpo.MOKPO_URL, mokpo.mokpo_list_url(2)]


def test_max_pages_must_include_declared_pages_and_empty_sentinel() -> None:
    _items_value, _pages, _details, fetch, make_session, calls, _sessions = _fixture()

    rows, _parser, meta = mokpo.collect_mokpo_education_courses(
        Target(),
        timeout=7,
        max_pages=2,
        detail_limit=100,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )

    assert rows == []
    assert calls == [mokpo.MOKPO_URL]
    assert meta["source_cap_reached"] is True
    assert meta["snapshot_complete"] is False
    assert "2 of 3 required list requests" in meta["configured_collection_error"]


def test_nonempty_sentinel_cannot_publish_a_partial_snapshot() -> None:
    _items_value, _pages, _details, fetch, make_session, _calls, _sessions = _fixture(
        nonempty_sentinel=True
    )

    rows, _parser, meta = mokpo.collect_mokpo_education_courses(
        Target(),
        timeout=7,
        max_pages=3,
        detail_limit=100,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )

    assert rows == []
    assert meta["page_counts"][3] == 1
    assert meta["snapshot_complete"] is False
    assert "sentinel page is not empty" in meta["configured_collection_error"]


def test_header_drift_fails_the_whole_snapshot() -> None:
    _items_value, _pages, _details, fetch, make_session, _calls, _sessions = _fixture(
        bad_headers_page=2
    )

    rows, _parser, meta = mokpo.collect_mokpo_education_courses(
        Target(),
        timeout=7,
        max_pages=3,
        detail_limit=100,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "malformed catalogue rows" in meta["configured_collection_error"]


def test_duplicate_identity_and_url_fail_before_details() -> None:
    _items_value, _pages, _details, fetch, make_session, calls, _sessions = _fixture(
        duplicate_identity=True
    )

    rows, _parser, meta = mokpo.collect_mokpo_education_courses(
        Target(),
        timeout=7,
        max_pages=3,
        detail_limit=100,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )

    assert rows == []
    assert meta["duplicate_count"] == 1
    assert meta["duplicate_url_count"] == 1
    assert meta["detail_attempts"] == 0
    assert len(calls) == 3


def test_detail_title_mismatch_fails_closed() -> None:
    _items_value, _pages, _details, fetch, make_session, _calls, _sessions = _fixture(
        wrong_detail_title="200"
    )

    rows, _parser, meta = mokpo.collect_mokpo_education_courses(
        Target(),
        timeout=7,
        max_pages=3,
        detail_limit=16,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )

    assert rows == []
    assert meta["detail_errors"] == 1
    assert meta["snapshot_complete"] is False
    assert "detail/list title mismatch" in meta["configured_collection_error"]


def test_missing_detail_key_fails_closed() -> None:
    _items_value, _pages, _details, fetch, make_session, _calls, _sessions = _fixture(
        omit_detail_key="문의"
    )

    rows, _parser, meta = mokpo.collect_mokpo_education_courses(
        Target(),
        timeout=7,
        max_pages=3,
        detail_limit=16,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )

    assert rows == []
    assert "missing detail keys 문의" in meta["configured_collection_error"]


def test_detail_limit_and_shared_dedupe_are_fail_closed() -> None:
    _items_value, _pages, _details, fetch, make_session, _calls, _sessions = _fixture()
    limited, _parser, limited_meta = mokpo.collect_mokpo_education_courses(
        Target(),
        timeout=7,
        max_pages=3,
        detail_limit=15,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )
    assert limited == []
    assert limited_meta["source_cap_reached"] is True
    assert "15 of 16 required details" in limited_meta["configured_collection_error"]

    _items_value, _pages, _details, fetch, make_session, _calls, _sessions = _fixture()
    deduped, _parser, dedupe_meta = mokpo.collect_mokpo_education_courses(
        Target(),
        timeout=7,
        max_pages=3,
        detail_limit=16,
        fetcher=fetch,
        session_factory=make_session,
        dedupe_rows=lambda rows: rows[:-1],
        today="2099-07-19",
    )
    assert deduped == []
    assert "dedupe changed complete row count 16 to 15" in dedupe_meta[
        "configured_collection_error"
    ]


def test_semantic_duplicates_fail_closed_even_with_distinct_ids() -> None:
    _items_value, _pages, _details, fetch, make_session, _calls, _sessions = _fixture(
        semantic_duplicate=True
    )

    rows, _parser, meta = mokpo.collect_mokpo_education_courses(
        Target(),
        timeout=7,
        max_pages=3,
        detail_limit=16,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )

    assert rows == []
    assert meta["semantic_duplicate_count"] == 1
    assert "semantic duplicate courses" in meta["configured_collection_error"]


def test_target_and_url_helpers_are_strict_and_aliases_are_explicit() -> None:
    assert mokpo.is_mokpo_education_target(Target()) is True
    assert mokpo.is_mokpo_education_target(Target(provider="WRONG")) is False
    assert mokpo.is_mokpo_education_target(Target(url=mokpo.MOKPO_URL + "&page=1")) is False
    assert mokpo.is_mokpo_education_target(
        Target(url=mokpo.MOKPO_DUPLICATE_ALIAS_URLS[0])
    ) is False
    assert mokpo.mokpo_list_url(1) == mokpo.MOKPO_URL
    assert mokpo.mokpo_list_url("2").endswith("me_id=sub222&page=2")
    assert mokpo.mokpo_list_url("../2") == ""
    assert mokpo.mokpo_detail_url("191").endswith("le_id=191&me_id=sub222")
    assert mokpo.mokpo_detail_url("191&evil=1") == ""
    assert mokpo._detail_identity(
        "./lecture_list_view.php?le_id=176&me_id=sub222&page=2",
        mokpo.mokpo_list_url(2),
    ) == ("176", mokpo.mokpo_detail_url("176"))
    assert mokpo._detail_identity(
        "./lecture_list_view.php?le_id=176&me_id=sub222&page=9",
        mokpo.mokpo_list_url(2),
    ) == ("", "")
    assert len(mokpo.MOKPO_DUPLICATE_ALIAS_URLS) == 2
    assert len(mokpo.MOKPO_EXCLUDED_STATIC_INFO_URLS) == 2


def test_production_path_requires_managed_http_and_never_disables_tls() -> None:
    rows, parser, meta = mokpo.collect_mokpo_education_courses(Target())

    assert rows == []
    assert parser == mokpo.MOKPO_PARSER
    assert meta["snapshot_complete"] is False
    assert "managed fetcher" in meta["configured_collection_error"]
    assert "verify=False" not in inspect.getsource(mokpo)
