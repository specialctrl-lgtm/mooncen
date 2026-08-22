from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_daejeon_ok as daejeon


TARGET = {
    "provider": daejeon.DAEJEON_OK_PROVIDER,
    "url": daejeon.DAEJEON_OK_CANONICAL_URL,
}
EXPERIENCE_TARGET = {
    "provider": daejeon.DAEJEON_OK_PROVIDER,
    "url": daejeon.DAEJEON_OK_EXPERIENCE_CANONICAL_URL,
}


class DummySession:
    def close(self) -> None:
        return None


def _milliseconds(value: str, end: bool = False) -> int:
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(
        hour=23 if end else 0,
        minute=59 if end else 0,
        second=59 if end else 0,
        tzinfo=ZoneInfo("Asia/Seoul"),
    )
    return int(parsed.timestamp() * 1000)


def _course(
    *,
    menu: str,
    district: str,
    identity: int,
    facility: str,
    title: str,
    status: str,
    use_start: str,
    use_end: str,
    apply_start: str,
    apply_end: str,
    current: int = 0,
    capacity: int = 10,
    fee: int = 0,
    dgr: str = "1",
    accepted: int | None = None,
    waiting: int = 0,
) -> dict[str, Any]:
    return {
        "menu": menu,
        "district": district,
        "itecd": "I100000",
        "facility_seq": str(100 + identity),
        "reservation_seq": str(1000 + identity),
        "detail_seq": str(2000 + identity),
        "method_code": "001",
        "method_label": "선착순",
        "status_code": status,
        "status_label": daejeon._STATUS_LABELS[status],
        "dgr": dgr,
        "facility": facility,
        "title": title,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "use_start": use_start,
        "use_end": use_end,
        "current": current,
        "accepted": current if accepted is None else accepted,
        "waiting": waiting,
        "capacity": capacity,
        "fee": fee,
    }


def _row_html(
    item: Mapping[str, Any], row_number: int, *, experience: bool = False
) -> str:
    fee = "무료" if not item["fee"] else f"{int(item['fee']):,}원"
    href = (
        "javascript: moveToFcltInfoMngDetail("
        f"'{item['itecd']}',{item['facility_seq']},{item['reservation_seq']},"
        f"{item['detail_seq']},'{item['method_code']}','{item['status_code']}',"
        f"'{item['dgr']}','tab2');"
    )
    capacity_cell = "" if experience else f"<td>{item['current']} / {item['capacity']}</td>"
    return f"""
      <tr>
        <td>{row_number}</td><td>{item['facility']}</td>
        <td><a href="{href}">{item['title']}</a></td>
        <td>{item['method_label']}</td>
        <td>{item['apply_start']} ~ {item['apply_end']}</td>
        <td>{item['use_start']} ~ {item['use_end']} (10:00 ~ 12:00)</td>
        <td>{fee}</td>{capacity_cell}<td>{item['status_label']}</td>
      </tr>
    """


def _list_html(
    menu: str,
    page: int,
    total: int,
    rows: list[Mapping[str, Any]],
    *,
    row_numbers: list[int] | None = None,
    experience: bool = False,
) -> str:
    last = max(1, (total + daejeon.DAEJEON_OK_PAGE_SIZE - 1) // daejeon.DAEJEON_OK_PAGE_SIZE)
    if row_numbers is None:
        row_numbers = [total - ((page - 1) * 10) - index for index in range(len(rows))]
    body = "".join(
        _row_html(item, row_number, experience=experience)
        for item, row_number in zip(rows, row_numbers, strict=True)
    )
    if not rows:
        body = f'<tr><td colspan="{8 if experience else 9}">해당내역이 없습니다.</td></tr>'
    action = "expRsvtList.do" if experience else "eduRsvtList.do"
    list_type = "expRsvtListType" if experience else "eduRsvtListType"
    capacity_header = "" if experience else "<th>접수자/정원</th>"
    waitlist_option = "" if experience else '<option value="005">대기자 접수중</option>'
    return f"""
    <html><head><title>OK예약서비스 - 대전광역시</title></head><body>
      <form id="pubFcltInfoListForm" method="POST" action="/okr2019/{action}">
        <input type="hidden" name="pageIdx" value="{page}">
        <input type="hidden" name="menuSeq" value="{menu}">
        <input type="hidden" name="fcltClsfcCd" value="">
        <input type="hidden" name="{list_type}" value="text">
        <select name="cityProvinceTpcd">
          <option value="000">전체</option><option value="001">동구</option>
          <option value="002">중구</option><option value="003">서구</option>
          <option value="004">유성구</option><option value="005">대덕구</option>
        </select>
        <select name="rsvtUseStatTpcd">
          <option value="">전체</option><option value="001">접수대기</option>
          <option value="002">접수중</option><option value="003">인원마감</option>
          <option value="004">접수종료</option>
          {waitlist_option}
        </select>
        <div class="total_counter">총 {total:,} 건 | {page}/{last} 페이지</div>
        <table class="ntable_styl">
          <thead><tr>
            <th>번호</th><th>시설명</th><th>예약명</th><th>모집방법</th>
            <th>접수기간</th><th>이용기간</th><th>수강료</th>
            {capacity_header}<th>접수상태</th>
          </tr></thead><tbody>{body}</tbody>
        </table>
      </form>
    </body></html>
    """


def _detail_html(item: Mapping[str, Any], *, include_control: bool = True) -> str:
    control = (
        '<div class="payment_button">'
        '<a class="btn_pay_red" href="javascript:void(0);" '
        'onclick="fnSetRsvtOk();">예약하기</a>'
        '<a class="btn_pay_red" href="javascript:void(0);" '
        'onclick="fnCallPay();">결제하기</a></div>'
        if include_control
        else ""
    )
    return f"""
    <html><head><title>OK예약서비스 - 대전광역시</title></head><body>
      <div id="tab2"><a href="javascript:void(0);" onclick="fnSelTab('2');">예약하기</a></div>
      {control}
      <script>
        var publicMenuSeq = '{item['menu']}';
        var publicItecd = '{item['itecd']}';
        var publicFcltSeq = '{item['facility_seq']}';
        var publicRsvtSeq = '{item['reservation_seq']}';
        var publicRsvtDtlSeq = '{item['detail_seq']}';
        var publicRsvtDgr = '{item['dgr']}';
        $('.btn_pay_red').hide();
        $('.btn_pay_red').show();
      </script>
    </body></html>
    """


def _detail_json(item: Mapping[str, Any]) -> dict[str, Any]:
    district = next(
        value
        for value in daejeon.DAEJEON_OK_DISTRICTS
        if value.source_code == item["district"]
    )
    return {
        "pblcVO": {
            "itecd": item["itecd"],
            "fcltSeq": int(item["facility_seq"]),
            "fcltNm": item["facility"],
            "addr": f"대전광역시 {district.label} 테스트로 1",
            "addrDtl": item["facility"],
            "regionDepth2": district.label,
            "useYn": "Y",
        },
        "rsvtVO": {
            "itecd": item["itecd"],
            "fcltSeq": int(item["facility_seq"]),
            "rsvtSeq": int(item["reservation_seq"]),
            "rsvtNm": item["title"],
            "rsvtMthdTpcd": item["method_code"],
            "rsvtUseStatTpcd": "002",
            "operBgdt": _milliseconds(item["apply_start"]),
            "operEddt": _milliseconds(item["apply_end"], end=True),
            "payYn": "Y" if item["fee"] else "N",
            "dgr": item["dgr"],
            "useYn": "Y",
        },
        "fcltAllVO": {
            "operBgdt": _milliseconds(item["apply_start"]),
            "operEddt": _milliseconds(item["apply_end"], end=True),
            "rsvtDateVO": {
                "rsvtPsblTpcd": "000",
                "rsvtRcitBgtm": "0000",
                "rsvtRcitEdtm": "0000",
                "useYn": "Y",
            },
        },
        "dtlVO": {
            "itecd": item["itecd"],
            "fcltSeq": int(item["facility_seq"]),
            "rsvtSeq": int(item["reservation_seq"]),
            "rsvtUseDtlSeq": int(item["detail_seq"]),
            "useTermBgdt": _milliseconds(item["use_start"]),
            "useTermEddt": _milliseconds(item["use_end"], end=True),
            "useAmt": item["fee"],
            "placeNm": f"{item['facility']} 강의실",
            "waitUseYn": "Y" if item["status_code"] == "005" else "N",
            "useYn": "Y",
        },
        "cntMap": {
            "MAX_LIMIT_CNT": item["capacity"],
            "RCPT_CNT": item["accepted"],
            "WAIT_CNT": item["waiting"],
            "RCPT_Y_CNT": item["accepted"],
        },
    }


def _fixture() -> tuple[
    dict[str, str],
    dict[tuple[str, str, str, str, str, str], dict[str, Any]],
    list[dict[str, Any]],
]:
    district_names = {
        "001": "동구청소년센터",
        "002": "한밭학습관",
        "003": "서구교육관",
        "004": "유성학습센터",
        "005": "대덕문화관",
    }
    courses: list[dict[str, Any]] = []
    identity = 1
    for menu in ("8101", "8102"):
        for district in ("001", "002", "003", "004", "005"):
            if menu == "8102" and district not in {"001", "004"}:
                continue
            is_open = menu == "8101" and district == "001"
            is_waitlist = menu == "8102" and district == "004"
            courses.append(
                _course(
                    menu=menu,
                    district=district,
                    identity=identity,
                    facility=district_names[district],
                    title=f"테스트 교육 {menu}-{district}",
                    status="002" if is_open else ("005" if is_waitlist else "004"),
                    use_start="2026-08-05" if (is_open or is_waitlist) else "2025-05-01",
                    use_end="2026-08-26" if (is_open or is_waitlist) else "2025-05-31",
                    apply_start="2026-07-01" if (is_open or is_waitlist) else "2025-04-01",
                    apply_end="2026-08-01" if (is_open or is_waitlist) else "2025-04-30",
                    current=3 if is_open else (13 if is_waitlist else 0),
                    accepted=2 if is_open else (10 if is_waitlist else None),
                    waiting=1 if is_open else (3 if is_waitlist else 0),
                    fee=20_000 if is_waitlist else 0,
                    dgr="" if is_waitlist else "1",
                )
            )
            identity += 1

    pages: dict[str, str] = {}
    for menu in ("8101", "8102"):
        menu_rows = [item for item in courses if item["menu"] == menu]
        pages[daejeon.daejeon_ok_list_url(menu, "000", 1)] = _list_html(
            menu,
            1,
            len(menu_rows),
            menu_rows,
            row_numbers=list(range(len(menu_rows), 0, -1)),
        )
        for district in ("001", "002", "003", "004", "005"):
            rows = [
                item
                for item in courses
                if item["menu"] == menu and item["district"] == district
            ]
            pages[daejeon.daejeon_ok_list_url(menu, district, 1)] = _list_html(
                menu, 1, len(rows), rows
            )
            pages[daejeon.daejeon_ok_list_url(menu, district, 2)] = _list_html(
                menu, 2, len(rows), []
            )
    details: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for item in courses:
        if item["use_end"] < "2026-07-21":
            continue
        detail_url = daejeon.daejeon_ok_detail_url(
            item["menu"],
            item["itecd"],
            item["facility_seq"],
            item["reservation_seq"],
            item["detail_seq"],
            item["status_code"],
            item["dgr"],
        )
        pages[detail_url] = _detail_html(item)
        key = (
            item["menu"],
            item["itecd"],
            item["facility_seq"],
            item["reservation_seq"],
            item["detail_seq"],
            item["dgr"],
        )
        details[key] = _detail_json(item)
    return pages, details, courses


def _collect(
    pages: Mapping[str, str],
    details: Mapping[tuple[str, str, str, str, str, str], Mapping[str, Any]],
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    def html_fetcher(_session: Any, url: str, _timeout: int) -> BeautifulSoup:
        if url not in pages:
            raise AssertionError(f"unexpected HTML URL {url}")
        return BeautifulSoup(pages[url], "lxml")

    def json_fetcher(
        _session: Any,
        url: str,
        payload: Mapping[str, Any],
        _timeout: int,
    ) -> Mapping[str, Any]:
        assert url == daejeon.DAEJEON_OK_DETAIL_AJAX_ENDPOINT
        key = (
            str(payload["menuSeq"]),
            str(payload["itecd"]),
            str(payload["fcltSeq"]),
            str(payload["rsvtSeq"]),
            str(payload["rsvtUseDtlSeq"]),
            str(payload["dgr"]),
        )
        if key not in details:
            raise AssertionError(f"unexpected AJAX identity {key}")
        return details[key]

    return daejeon.collect_daejeon_ok_education(
        TARGET,
        timeout=5,
        max_pages=kwargs.pop("max_pages", 2),
        detail_limit=kwargs.pop("detail_limit", 10),
        today=kwargs.pop("today", "2026-07-21"),
        max_workers=kwargs.pop("max_workers", 4),
        fetch_attempts=kwargs.pop("fetch_attempts", 1),
        session_factory=DummySession,
        html_fetcher=kwargs.pop("html_fetcher", html_fetcher),
        json_post_fetcher=kwargs.pop("json_post_fetcher", json_fetcher),
        **kwargs,
    )


def _experience_fixture() -> tuple[
    dict[str, str],
    dict[tuple[str, str, str, str, str, str], dict[str, Any]],
    list[dict[str, Any]],
]:
    courses = [
        _course(
            menu="8201",
            district=district.source_code,
            identity=index,
            facility=f"{district.label} 체험관",
            title=f"공식 체험 {district.label}",
            status="002" if index == 1 else "004",
            use_start="2026-08-05",
            use_end="2026-12-31",
            apply_start="2026-07-01",
            apply_end="2026-12-01",
            current=index,
            capacity=20,
        )
        for index, district in enumerate(daejeon.DAEJEON_OK_DISTRICTS, start=1)
    ]
    pages: dict[str, str] = {
        daejeon.daejeon_ok_experience_list_url("8201", "000", 1): _list_html(
            "8201",
            1,
            len(courses),
            courses,
            row_numbers=list(range(len(courses), 0, -1)),
            experience=True,
        )
    }
    for district in daejeon.DAEJEON_OK_DISTRICTS:
        district_rows = [
            item for item in courses if item["district"] == district.source_code
        ]
        pages[daejeon.daejeon_ok_experience_list_url("8201", district.source_code, 1)] = (
            _list_html("8201", 1, 1, district_rows, experience=True)
        )
        pages[daejeon.daejeon_ok_experience_list_url("8201", district.source_code, 2)] = (
            _list_html("8201", 2, 1, [], experience=True)
        )

    details: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for item in courses:
        detail_url = daejeon.daejeon_ok_experience_detail_url(
            item["menu"],
            item["itecd"],
            item["facility_seq"],
            item["reservation_seq"],
            item["detail_seq"],
            item["status_code"],
            item["dgr"],
        )
        pages[detail_url] = _detail_html(item)
        details[
            (
                item["menu"],
                item["itecd"],
                item["facility_seq"],
                item["reservation_seq"],
                item["detail_seq"],
                item["dgr"],
            )
        ] = _detail_json(item)
    return pages, details, courses


def _collect_experience(
    pages: Mapping[str, str],
    details: Mapping[tuple[str, str, str, str, str, str], Mapping[str, Any]],
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    def html_fetcher(_session: Any, url: str, _timeout: int) -> BeautifulSoup:
        if url not in pages:
            raise AssertionError(f"unexpected HTML URL {url}")
        return BeautifulSoup(pages[url], "lxml")

    def json_fetcher(
        _session: Any,
        url: str,
        payload: Mapping[str, Any],
        _timeout: int,
    ) -> Mapping[str, Any]:
        assert url == daejeon.DAEJEON_OK_EXPERIENCE_DETAIL_AJAX_ENDPOINT
        key = tuple(
            str(payload[name])
            for name in (
                "menuSeq",
                "itecd",
                "fcltSeq",
                "rsvtSeq",
                "rsvtUseDtlSeq",
                "dgr",
            )
        )
        if key not in details:
            raise AssertionError(f"unexpected AJAX identity {key}")
        return details[key]

    return daejeon.collect_daejeon_ok_courses(
        EXPERIENCE_TARGET,
        timeout=5,
        max_pages=kwargs.pop("max_pages", 2),
        detail_limit=kwargs.pop("detail_limit", 10),
        today=kwargs.pop("today", "2026-08-05"),
        max_workers=kwargs.pop("max_workers", 4),
        fetch_attempts=kwargs.pop("fetch_attempts", 1),
        session_factory=DummySession,
        html_fetcher=kwargs.pop("html_fetcher", html_fetcher),
        json_post_fetcher=kwargs.pop("json_post_fetcher", json_fetcher),
        **kwargs,
    )


def test_target_scope_and_fixed_urls_reject_category_or_district_aliases() -> None:
    assert daejeon.is_daejeon_ok_education_target(TARGET)
    assert daejeon.is_daejeon_ok_education_target(
        {
            "provider": daejeon.DAEJEON_OK_PROVIDER,
            "url": (
                "https://www.daejeon.go.kr/okr2019/eduRsvtList.do?"
                "menuUseYn=N&boardUseYn=N&ntatcDelYn=Y&menuSeq=8100"
            ),
        }
    )
    assert not daejeon.is_daejeon_ok_education_target(
        {"provider": daejeon.DAEJEON_OK_PROVIDER, "url": daejeon.daejeon_ok_list_url("8101", "000", 1)}
    )
    assert not daejeon.is_daejeon_ok_education_target(
        {"provider": "MUNI_WWW_DJJUNGGU_GO_KR_6A89B08A", "url": TARGET["url"]}
    )
    assert daejeon.daejeon_ok_list_url("8201", "001", 1) == ""
    assert daejeon.daejeon_ok_list_url("8101", "999", 1) == ""


def test_experience_target_scope_and_fixed_urls_are_independent() -> None:
    assert daejeon.is_daejeon_ok_target(EXPERIENCE_TARGET)
    assert daejeon.is_daejeon_ok_experience_target(EXPERIENCE_TARGET)
    assert not daejeon.is_daejeon_ok_education_target(EXPERIENCE_TARGET)
    assert daejeon.daejeon_ok_experience_list_url("8201", "001", 1).startswith(
        daejeon.DAEJEON_OK_EXPERIENCE_LIST_ENDPOINT
    )
    assert daejeon.daejeon_ok_experience_list_url("8101", "001", 1) == ""
    assert daejeon.daejeon_ok_experience_list_url("8201", "999", 1) == ""
    assert daejeon.daejeon_ok_experience_detail_url(
        "8201", "I100000", "1", "2", "3", "002", "1"
    ).startswith(daejeon.DAEJEON_OK_EXPERIENCE_DETAIL_ENDPOINT)


def test_complete_experience_scope_is_locked_and_district_attributed() -> None:
    pages, details, _courses = _experience_fixture()
    rows, parser, meta = _collect_experience(pages, details)

    assert parser == daejeon.DAEJEON_OK_EXPERIENCE_PARSER
    assert len(rows) == 5
    assert meta["source_total"] == 5
    assert meta["global_totals"] == {"8201": 5}
    assert meta["snapshot_complete"] is True
    assert meta["canonical_url"] == daejeon.DAEJEON_OK_EXPERIENCE_CANONICAL_URL
    assert meta["independent_catalogue_noncoverage_evidence"] == []
    assert {row["program_type"] for row in rows} == {"체험"}
    assert {row["domain_category"] for row in rows} == {"체험·견학"}
    assert {row["service_group"] for row in rows} == {"체험"}
    assert {row["service_group_policy"] for row in rows} == {"locked"}
    assert {row["capacity_total"] for row in rows} == {20}
    assert {row["capacity_current"] for row in rows} == {1, 2, 3, 4, 5}
    assert all(row["raw_fields"]["source_capacity_present"] is False for row in rows)
    assert all(
        row["raw_fields"]["detail_application_count_matches_list"] is None
        for row in rows
    )
    assert {row["municipality_code"] for row in rows} == {
        district.municipality_code for district in daejeon.DAEJEON_OK_DISTRICTS
    }
    assert all(":experience:" in row["provider_course_id"] for row in rows)

    education_pages, education_details, _ = _fixture()
    education_rows, _, _ = _collect(education_pages, education_details)
    assert {row["provider_course_id"] for row in rows}.isdisjoint(
        {row["provider_course_id"] for row in education_rows}
    )


def test_experience_schema_drift_fails_closed() -> None:
    pages, details, _courses = _experience_fixture()
    watched = daejeon.daejeon_ok_experience_list_url("8201", "001", 1)
    broken = dict(pages)
    broken[watched] = broken[watched].replace(
        'name="expRsvtListType"', 'name="eduRsvtListType"'
    )

    rows, parser, meta = _collect_experience(broken, details)

    assert rows == []
    assert parser == daejeon.DAEJEON_OK_EXPERIENCE_PARSER
    assert meta["snapshot_complete"] is False
    assert "expRsvtListType" in meta["configured_collection_error"]


def test_municipal_router_dispatches_experience_to_shared_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from Crawler import Crawler_MunicipalYaml as router

    expected = ([{"title": "체험"}], "experience-parser", {"snapshot_complete": True})
    seen: dict[str, Any] = {}

    def fake_collect(target: Any, **kwargs: Any) -> Any:
        seen["target"] = target
        seen["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(daejeon, "collect_daejeon_ok_courses", fake_collect)
    target = router.CrawlTarget(
        provider=daejeon.DAEJEON_OK_PROVIDER,
        name="대전 체험",
        branch="대전광역시",
        url=daejeon.DAEJEON_OK_EXPERIENCE_CANONICAL_URL,
        source="test",
    )

    assert router.collect_from_url(
        target, timeout=7, max_depth=0, max_pages=30, detail_limit=400
    ) == expected
    assert seen["target"] is target
    assert seen["kwargs"]["timeout"] == 7
    assert seen["kwargs"]["max_pages"] == 30
    assert seen["kwargs"]["detail_limit"] == 400


def test_thread_session_pool_reuses_connections_and_closes_owned_sessions() -> None:
    created: list[Any] = []

    class Session:
        def __init__(self) -> None:
            self.close_count = 0
            created.append(self)

        def close(self) -> None:
            self.close_count += 1

    pool = daejeon._ThreadSessionPool(Session)
    barrier = threading.Barrier(4)

    def borrow_twice(_index: int) -> int:
        first = pool()
        barrier.wait()
        second = pool()
        assert first is second
        first.close()
        return id(first.session)

    with ThreadPoolExecutor(max_workers=4) as executor:
        session_ids = list(executor.map(borrow_twice, range(4)))

    assert len(set(session_ids)) == 4
    assert len(created) == 4
    assert all(session.close_count == 0 for session in created)

    pool.close()
    pool.close()

    assert all(session.close_count == 1 for session in created)


def test_retry_backoff_runs_only_between_failed_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    calls = 0

    def fetcher(_session: Any, _url: str, _timeout: int) -> BeautifulSoup:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("temporary timeout")
        return BeautifulSoup("<html></html>", "lxml")

    monkeypatch.setattr(daejeon.time, "sleep", sleeps.append)
    result = daejeon._fetch_html(
        "https://www.daejeon.go.kr/okr2019/eduRsvtList.do",
        session_factory=DummySession,
        fetcher=fetcher,
        timeout=5,
        attempts=3,
        label="retry test",
    )

    assert isinstance(result, BeautifulSoup)
    assert calls == 3
    assert sleeps == list(daejeon.DAEJEON_OK_RETRY_DELAYS)


def test_collection_reuses_one_worker_pool_across_all_request_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[int] = []
    real_executor = ThreadPoolExecutor

    class TrackingExecutor(real_executor):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self._max_workers)

    monkeypatch.setattr(daejeon, "ThreadPoolExecutor", TrackingExecutor)
    pages, details, _courses = _fixture()

    rows, _parser, meta = _collect(pages, details)

    assert rows
    assert meta["snapshot_complete"] is True
    assert created == [4]


def test_complete_two_leaf_five_region_snapshot_details_and_noncoverage_metadata() -> None:
    pages, details, _courses = _fixture()
    rows, parser, meta = _collect(pages, details)

    assert parser == daejeon.DAEJEON_OK_PARSER
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"OPEN", "WAITLIST"}
    assert {row["municipality_code"] for row in rows} == {
        "3011000000",
        "3020000000",
    }
    assert all(row["application_url"] == row["raw_url"] for row in rows)
    assert {row["application_type"] for row in rows} == {
        "ONLINE_RESERVATION",
        "WAITLIST_APPLY",
    }
    assert {row["fee_amount"] for row in rows} == {0, 20_000}
    assert all(row["schedule_raw"] == "10:00 ~ 12:00" for row in rows)
    assert all("대전광역시" in row["address"] for row in rows)
    assert all("phone" not in row and "instructor" not in row for row in rows)
    assert all(
        set(row["raw_fields"]) <= {
            "identity",
            "itecd",
            "facility_seq",
            "reservation_seq",
            "reservation_detail_seq",
            "dgr",
            "menu_seq",
            "source_category",
            "service_region_code",
            "service_region_name",
            "list_page",
            "source_row_number",
            "source_method_code",
            "source_method_label",
                "source_fee_label",
                "source_capacity_present",
                "source_capacity_label",
                "source_status_code",
            "source_status",
            "source_application_period",
            "source_use_period",
            "source_application_period_reversed",
            "source_use_period_reversed",
            "detail_lifecycle_status",
            "application_rule_code",
            "application_rule_evidence",
            "detail_application_count",
            "detail_waiting_count",
            "detail_displayed_application_count",
            "detail_application_count_matches_list",
            "wait_enabled",
            "application_control_present",
            "application_control_contract",
            "municipality_evidence",
        }
        for row in rows
    )

    assert meta["global_totals"] == {"8101": 5, "8102": 2}
    assert meta["source_total"] == meta["source_rows"] == 7
    assert len(meta["partition_totals"]) == 10
    assert meta["pages"] == 10
    assert meta["global_declaration_requests"] == 2
    assert meta["sentinel_requests"] == 10
    assert meta["stability_rechecks"] == 10
    assert meta["required_list_requests"] == meta["list_requests"] == 32
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["expired_count"] == 5
    assert meta["identity_duplicate_count"] == 0
    assert meta["semantic_duplicate_group_count"] == 0
    assert meta["semantic_duplicate_excess_rows"] == 0
    assert meta["semantic_duplicate_policy"] == (
        "preserve_distinct_official_reservation_identities"
    )
    assert meta["full_snapshot_validated"] is True
    assert meta["district_coverage_claimed"] is False
    assert meta["district_candidate_aliases"] == []
    assert meta["promotion_municipality_codes"] == ["3000000000"]
    assert meta["independent_district_catalogues_included"] is False
    evidence = meta["independent_catalogue_noncoverage_evidence"]
    assert {item["municipality_code"] for item in evidence} == {
        "3014000000",
        "3023000000",
    }
    assert all(item["ok_title_search_totals"] == {"8101": 0, "8102": 0} for item in evidence)
    assert meta["excluded_service_menu_seqs"]["experience_observation"] == [
        "8200",
        "8201",
        "8203",
    ]
    assert meta["pii_payload_persisted"] is False
    assert daejeon._fee("현장결제") == ("현장결제", 0)


def test_global_partition_reconciliation_and_sentinel_fail_closed() -> None:
    pages, details, courses = _fixture()
    bad_global = dict(pages)
    menu_rows = [item for item in courses if item["menu"] == "8101"][:4]
    bad_global[daejeon.daejeon_ok_list_url("8101", "000", 1)] = _list_html(
        "8101", 1, 4, menu_rows, row_numbers=[4, 3, 2, 1]
    )
    rows, _parser, meta = _collect(bad_global, details)
    assert rows == []
    assert "does not match global total" in meta["configured_collection_error"]
    assert meta["snapshot_complete"] is False

    bad_sentinel = dict(pages)
    exposed = next(
        item
        for item in courses
        if item["menu"] == "8101" and item["district"] == "001"
    )
    bad_sentinel[daejeon.daejeon_ok_list_url("8101", "001", 2)] = _list_html(
        "8101", 2, 1, [exposed], row_numbers=[1]
    )
    rows2, _parser2, meta2 = _collect(bad_sentinel, details)
    assert rows2 == []
    assert "sentinel page is not empty" in meta2["configured_collection_error"]
    assert meta2["snapshot_complete"] is False


def test_stable_recheck_detail_control_caps_and_shared_dedupe_fail_closed() -> None:
    pages, details, courses = _fixture()
    watched = daejeon.daejeon_ok_list_url("8101", "001", 1)
    drifted = dict(next(item for item in courses if item["menu"] == "8101" and item["district"] == "001"))
    drifted["reservation_seq"] = "99999"
    drifted["detail_seq"] = "99998"
    counts: dict[str, int] = {}
    lock = threading.Lock()

    def changing_fetcher(_session: Any, url: str, _timeout: int) -> BeautifulSoup:
        with lock:
            counts[url] = counts.get(url, 0) + 1
            call = counts[url]
        if url == watched and call == 2:
            return BeautifulSoup(_list_html("8101", 1, 1, [drifted]), "lxml")
        return BeautifulSoup(pages[url], "lxml")

    rows, _parser, meta = _collect(
        pages, details, html_fetcher=changing_fetcher
    )
    assert rows == []
    assert "changed during stable recheck" in meta["configured_collection_error"]

    current_item = next(item for item in courses if item["status_code"] == "002")
    detail_url = daejeon.daejeon_ok_detail_url(
        current_item["menu"],
        current_item["itecd"],
        current_item["facility_seq"],
        current_item["reservation_seq"],
        current_item["detail_seq"],
        current_item["status_code"],
        current_item["dgr"],
    )
    bad_detail_pages = dict(pages)
    bad_detail_pages[detail_url] = _detail_html(current_item, include_control=False)
    rows2, _parser2, meta2 = _collect(bad_detail_pages, details)
    assert rows2 == []
    assert "reservation control contract changed" in meta2["configured_collection_error"]

    rows3, _parser3, meta3 = _collect(pages, details, max_pages=1)
    assert rows3 == []
    assert "sentinel beyond max_pages" in meta3["configured_collection_error"]

    rows4, _parser4, meta4 = _collect(pages, details, detail_limit=1)
    assert rows4 == []
    assert meta4["source_cap_reached"] is True
    assert "exceeds detail_limit" in meta4["configured_collection_error"]

    rows5, _parser5, meta5 = _collect(
        pages, details, dedupe_rows=lambda values: values[:-1]
    )
    assert rows5 == []
    assert "dedupe changed official identity cardinality" in meta5["configured_collection_error"]


def test_ajax_identity_or_district_drift_fails_closed_without_partial_rows() -> None:
    pages, details, courses = _fixture()
    bad_details = {key: dict(value) for key, value in details.items()}
    current = next(item for item in courses if item["status_code"] == "005")
    key = (
        current["menu"],
        current["itecd"],
        current["facility_seq"],
        current["reservation_seq"],
        current["detail_seq"],
        current["dgr"],
    )
    broken = dict(bad_details[key])
    broken["pblcVO"] = {**broken["pblcVO"], "regionDepth2": "서구"}
    bad_details[key] = broken

    rows, _parser, meta = _collect(pages, bad_details)
    assert rows == []
    assert "detail/filter district mismatch" in meta["configured_collection_error"]
    assert meta["returned_count"] == 0
    assert meta["full_snapshot_validated"] is False
