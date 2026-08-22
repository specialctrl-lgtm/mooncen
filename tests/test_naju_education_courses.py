from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_naju as naju


@dataclass(frozen=True)
class LifelongTarget:
    provider: str = naju.NAJU_LIFELONG_PROVIDER
    name: str = "나주시평생학습관 강좌전체"
    branch: str = "전남광주통합특별시 나주시"
    url: str = naju.NAJU_LIFELONG_URL


@dataclass(frozen=True)
class GongikTarget:
    provider: str = naju.NAJU_GONGIK_PROVIDER
    name: str = "나주시 공익활동지원센터 참여교육"
    branch: str = "나주시 공익활동지원센터"
    url: str = naju.NAJU_GONGIK_URL


class DummySession:
    def __init__(self) -> None:
        self.closed = False
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.payloads: dict[int, dict[str, Any]] = {}

    def close(self) -> None:
        self.closed = True

    def post(self, url: str, **kwargs: Any) -> Any:
        self.posts.append((url, kwargs))
        page = int((parse_qs(urlparse(url).query).get("page") or ["1"])[0])
        return JsonResponse(self.payloads[page])


class JsonResponse:
    status_code = 200
    history: list[Any] = []

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def json(self) -> dict[str, Any]:
        return self.payload


def _lifelong_items(total: int = 17, current_count: int = 2) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in range(total):
        current = index < current_count
        identity = str(900 - index)
        status = "접수중" if index == 0 else ("접수대기" if current else "수강종료")
        result.append(
            {
                "row_number": total - index,
                "identity": identity,
                "title": f"[2기] 나주 교육 {identity}" if index == 0 else f"나주 교육 {identity}",
                "detail_title": f"나주 교육 {identity} (2기)" if index == 0 else f"나주 교육 {identity}",
                "branch": "빛가람시립도서관" if index % 2 == 0 else "나주시평생학습관",
                "instructor": f"강사 {identity}",
                "apply_start": "2099-07-01" if current else "2020-01-01",
                "apply_end": "2099-07-31" if current else "2020-01-31",
                "start": "2099-08-01" if current else "2020-02-01",
                "end": "2099-12-31" if current else "2020-02-28",
                "status": status,
                "active": status == "접수중",
            }
        )
    return result


def _lifelong_pager(page: int, pages: int, *, sentinel: bool = False) -> str:
    links: list[str] = []
    if not sentinel:
        links.append(f'<a class="on">{page}</a>')
    if page < pages:
        links.append(
            f'<a class="last" href="{naju.NAJU_LIFELONG_PATH}?page={pages}'
            f'&amp;search_startdate={naju.NAJU_LIFELONG_HISTORY_START}&amp;search_status=all">&gt;&gt;</a>'
        )
    elif sentinel and pages:
        links.append(
            f'<a href="{naju.NAJU_LIFELONG_PATH}?page={pages}'
            f'&amp;search_startdate={naju.NAJU_LIFELONG_HISTORY_START}&amp;search_status=all">{pages}</a>'
        )
    return '<div class="list_paging"><div class="num">' + "".join(links) + "</div></div>"


def _lifelong_list_page(
    items: list[dict[str, Any]],
    *,
    total: int,
    page: int,
    pages: int,
    sentinel: bool = False,
    bad_headers: bool = False,
) -> str:
    headers = list(naju._LIFELONG_HEADERS)
    if bad_headers:
        headers[-1] = "잘못된 상태"
    rows: list[str] = []
    for item in items:
        apply = (
            f'<a href="{naju.NAJU_LIFELONG_PATH}?lecture_idx={item["identity"]}&amp;mode=reserve_form">접수하기</a>'
            if item["active"]
            else ""
        )
        rows.append(
            f"""
            <tr>
              <td>{item['row_number']}</td>
              <td class="lecture_title"><a href="{naju.NAJU_LIFELONG_PATH}?idx={item['identity']}&amp;mode=view">
                <span class="fc_blue3">{item['title']}</span>
                <span>강 사 명 : {item['instructor']}</span>
                <span>신청기간 : {item['apply_start']} 09:00 ~ {item['apply_end']} 18:00</span>
                <span>교육기간 : {item['start']} ~ {item['end']}</span>
              </a></td>
              <td>{item['branch']}</td>
              <td><div>선착순</div><span class="apply">3</span>
                <span class="wait">(1)</span> / <span class="fix_poeple">20명</span>
              </td>
              <td>0 원</td><td class="btn_style"><span>{item['status']}</span>{apply}</td>
            </tr>
            """
        )
    if not rows:
        rows.append('<tr><td colspan="6">개설된 강좌가 없습니다.</td></tr>')
    return f"""
      <html><body>
        <ul class="cate_list"><li class="first">전체<span>({total})</span></li></ul>
        <table class="list_table"><caption>강좌관리목록</caption>
          <thead><tr>{''.join(f'<th>{header}</th>' for header in headers)}</tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
        {_lifelong_pager(page, pages, sentinel=sentinel)}
      </body></html>
    """


def _lifelong_detail_page(
    item: dict[str, Any], *, wrong_title: bool = False, stale_status: bool = False
) -> str:
    title = "다른 강좌" if wrong_title else item["detail_title"]
    status = "접수중" if stale_status else item["status"]
    controls = (
        f'<a href="{naju.NAJU_LIFELONG_PATH}?lecture_idx={item["identity"]}&amp;mode=reserve_form">접수하기</a>'
        if item["active"]
        else ""
    )
    values = [
        ("강좌명(기수)", title),
        ("교육정보", '<a href="https://lib.naju.go.kr/">교육정보 바로가기</a>'),
        ("연계강좌여부", "비연계"),
        ("교육대상", "나주시민"),
        ("수강료", "0 원"),
        ("신청기간", f"{item['apply_start']} 09:00 ~ {item['apply_end']} 18:00"),
        ("교육기간", f"{item['start']} ~ {item['end']}"),
        ("교육장소", f"{item['branch']} 강의실"),
        ("교육분류", "문화예술 프로그램"),
        ("수강신청방법", "온라인"),
        ("수강신청선정방법", "선착순"),
        ("학점", "0 점"),
        ("보호자동의여부", "미동의"),
        ("교육기관", f"나주시청 / {item['branch']}"),
        ("계좌번호정보", "없음"),
        ("오시는 길", "나주시"),
        ("문의전화", "061-339-4673"),
        ("강좌소개 강의계획", "즐거운 나주 교육"),
        ("강사명", item["instructor"]),
        ("강사소개", "전문 강사"),
        ("모집정원", "20 명 : 선착순"),
        ("모집대기인원", "5 명"),
    ]
    rows: list[str] = []
    for index in range(0, len(values), 2):
        cells = ""
        for key, value in values[index : index + 2]:
            cells += f"<th>{key}</th><td>{value}</td>"
        rows.append(f"<tr>{cells}</tr>")
    return f"""
      <html><body><h3>수강신청자 목록 ({status})</h3>
        <table class="view_table"><tbody>{''.join(rows)}</tbody></table>{controls}
      </body></html>
    """


def _lifelong_fixture(
    *,
    duplicate_identity: bool = False,
    nonempty_sentinel: bool = False,
    wrong_detail: bool = False,
    stale_detail_status: bool = False,
):
    items = _lifelong_items()
    if duplicate_identity:
        items[1]["identity"] = items[0]["identity"]
    pages = math.ceil(len(items) / naju.NAJU_LIFELONG_PAGE_SIZE)
    list_pages: dict[int, str] = {}
    for page in range(1, pages + 1):
        start = (page - 1) * naju.NAJU_LIFELONG_PAGE_SIZE
        list_pages[page] = _lifelong_list_page(
            items[start : start + naju.NAJU_LIFELONG_PAGE_SIZE],
            total=len(items),
            page=page,
            pages=pages,
        )
    sentinel_items = [items[0]] if nonempty_sentinel else []
    list_pages[pages + 1] = _lifelong_list_page(
        sentinel_items,
        total=len(items),
        page=pages + 1,
        pages=pages,
        sentinel=True,
    )
    details = {
        item["identity"]: _lifelong_detail_page(
            item,
            wrong_title=wrong_detail and index == 0,
            stale_status=stale_detail_status and index == 1,
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
        if parsed.path == naju.NAJU_LIFELONG_PATH and query.get("mode") == ["view"]:
            return BeautifulSoup(details[query["idx"][0]], "lxml")
        page = int((query.get("page") or ["1"])[0])
        return BeautifulSoup(list_pages[page], "lxml")

    return items, fetch, make_session, calls, sessions


def test_lifelong_collects_complete_pages_sentinel_and_current_details() -> None:
    items, fetch, make_session, calls, sessions = _lifelong_fixture(stale_detail_status=True)

    rows, parser, meta = naju.collect_naju_lifelong_courses(
        LifelongTarget(),
        timeout=7,
        max_pages=3,
        detail_limit=2,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
        dedupe_rows=lambda values: values,
    )

    assert parser == naju.NAJU_LIFELONG_PARSER
    assert len(rows) == 2
    assert rows[0]["provider_course_id"] == f"{naju.NAJU_LIFELONG_PROVIDER}:lecture:900"
    assert rows[0]["title"] == "[2기] 나주 교육 900"
    assert rows[0]["branch"] == "빛가람시립도서관"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["application_url"] == naju.naju_lifelong_application_url("900")
    assert rows[0]["reservation_available"] is True
    assert rows[0]["capacity_current"] == 3
    assert rows[0]["capacity_total"] == 20
    assert rows[0]["education_info_url"] == "https://lib.naju.go.kr/"
    assert rows[1]["reservation_available"] is False
    assert rows[1]["application_type"] == "ONLINE_RESERVATION"
    assert meta["source_total"] == 17
    assert meta["source_rows"] == 17
    assert meta["hidden_row_count"] == 0
    assert meta["required_list_requests"] == 3
    assert meta["page_counts"] == {1: 15, 2: 2, 3: 0}
    assert meta["current_count"] == 2
    assert meta["expired_count"] == 15
    assert meta["detail_pages"] == 2
    assert meta["detail_status_mismatch_count"] == 1
    assert meta["duplicate_count"] == 0
    assert meta["duplicate_url_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert meta["snapshot_complete"] is True
    assert len(calls) == 5
    assert sessions[0].closed is True


def test_lifelong_caps_and_sentinel_fail_closed() -> None:
    _items, fetch, make_session, calls, _sessions = _lifelong_fixture()
    rows, _parser, meta = naju.collect_naju_lifelong_courses(
        LifelongTarget(),
        timeout=7,
        max_pages=2,
        detail_limit=20,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )
    assert rows == []
    assert calls == [naju.naju_lifelong_list_url(1)]
    assert meta["source_cap_reached"] is True
    assert "2 of 3 required list requests" in meta["configured_collection_error"]

    _items, fetch, make_session, _calls, _sessions = _lifelong_fixture(nonempty_sentinel=True)
    rows, _parser, meta = naju.collect_naju_lifelong_courses(
        LifelongTarget(),
        timeout=7,
        max_pages=3,
        detail_limit=20,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel page is not empty" in meta["configured_collection_error"]


def test_lifelong_duplicate_detail_and_dedupe_fail_closed() -> None:
    _items, fetch, make_session, _calls, _sessions = _lifelong_fixture(duplicate_identity=True)
    rows, _parser, meta = naju.collect_naju_lifelong_courses(
        LifelongTarget(), timeout=7, max_pages=3, detail_limit=20,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert meta["duplicate_count"] == 1

    _items, fetch, make_session, _calls, _sessions = _lifelong_fixture(wrong_detail=True)
    rows, _parser, meta = naju.collect_naju_lifelong_courses(
        LifelongTarget(), timeout=7, max_pages=3, detail_limit=2,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "detail/list title mismatch" in meta["configured_collection_error"]

    _items, fetch, make_session, _calls, _sessions = _lifelong_fixture()
    rows, _parser, meta = naju.collect_naju_lifelong_courses(
        LifelongTarget(), timeout=7, max_pages=3, detail_limit=2,
        fetcher=fetch, session_factory=make_session, today="2099-07-19",
        dedupe_rows=lambda values: values[:1],
    )
    assert rows == []
    assert "dedupe changed complete row count" in meta["configured_collection_error"]


def test_lifelong_detail_limit_and_managed_injection_fail_closed() -> None:
    _items, fetch, make_session, calls, _sessions = _lifelong_fixture()
    rows, _parser, meta = naju.collect_naju_lifelong_courses(
        LifelongTarget(), timeout=7, max_pages=3, detail_limit=1,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert len(calls) == 3
    assert meta["source_cap_reached"] is True
    assert "1 of 2 required details" in meta["configured_collection_error"]

    rows, _parser, meta = naju.collect_naju_lifelong_courses(LifelongTarget())
    assert rows == []
    assert "managed fetcher and session_factory" in meta["configured_collection_error"]


def _gongik_item(*, current: bool, identity: str = "501") -> dict[str, Any]:
    return {
        "idx": identity,
        "organization": "",
        "title": f"공익 교육 {identity}",
        "list_num": int(identity),
        "quota": "25",
        "quota_standby": "5",
        "student_cnt": "3",
        "standby_cnt": 1,
        "lecturer": "나주강사",
        "cost": "0",
        "target": "나주시민",
        "receipt_start": "2099-07-01 09:00:00" if current else "2020-01-01 09:00:00",
        "receipt_end": "2099-07-31 18:00:00" if current else "2020-01-31 18:00:00",
        "lecture_start": "2099-08-01" if current else "2020-02-01",
        "lecture_end": "2099-12-31" if current else "2020-02-28",
        "category_1": "나주시 공익활동지원센터",
        "category_2": "시민교육",
        "introduce": "공익 역량 교육",
        "location": "나주시 공익활동지원센터 중회의실",
        "varchar_1": "매주 토 10:00~12:00",
        "varchar_2": "홈페이지",
        "varchar_4": "공익활동지원센터",
        "board_id": "gongik_edu",
        "status": ["ing", "접수중", "state_ing"] if current else ["end", "접수마감", "state_finish"],
    }


def _gongik_payload(items: list[dict[str, Any]], *, page: int, total: int) -> dict[str, Any]:
    return {
        "total_count": str(total),
        "page_scale": "15",
        "block_scale": "10",
        "page": str(page),
        "json_navi_parameter": (
            "sub_mode=all" if page == 1 else f"sub_mode=all&amp;page={page}"
        ),
        "list": items,
    }


def _gongik_landing() -> str:
    tabs = "".join(
        f'<a class="category_tab_btn" data-type="{value}">{value}</a>'
        for value in ("ing", "end", "wait", "all")
    )
    return f"""
      <html><body><form id="board_sch1" action="{naju.NAJU_GONGIK_PATH}"></form>
      {tabs}<script src="{naju.NAJU_GONGIK_PATH}/ybmodule.pkg/js/list_lecture.js"></script></body></html>
    """


def _gongik_detail(item: dict[str, Any], *, wrong_title: bool = False) -> str:
    title = "다른 교육" if wrong_title else item["title"]
    values = {
        "모집명": title,
        "주관부서": "공익활동지원센터",
        "모집기간": f"{item['receipt_start']} ~ {item['receipt_end']}",
        "모집대상": item["target"],
        "모집정원": "25명",
        "제출서류": "없음",
        "접수방법": "홈페이지",
        "선정방법": "선착순",
        "교육기간": f"{item['lecture_start']} ~ {item['lecture_end']}",
        "요일/시간": item["varchar_1"],
        "교육장소": "나주시 공익활동지원센터 중회의실",
        "교육소개": item["introduce"],
        "문의전화": "0613392633",
    }
    rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in values.items())
    control = (
        f'<a href="{naju.NAJU_GONGIK_PATH}?idx={item["idx"]}&amp;mode=write">신청하기</a>'
        if item["status"][0] == "ing"
        else ""
    )
    return f"""
      <html><body><table id="lecture_view_table" class="board_t1_view"><tbody>{rows}</tbody></table>
      <div class="lecture_btn_box">{control}<a href="?page=">목록</a></div></body></html>
    """


def _gongik_fixture(*, current: bool, nonempty_sentinel: bool = False, wrong_detail: bool = False):
    item = _gongik_item(current=current)
    session = DummySession()
    session.payloads = {
        1: _gongik_payload([item], page=1, total=1),
        2: _gongik_payload([item] if nonempty_sentinel else [], page=2, total=1),
    }
    calls: list[str] = []

    def make_session() -> DummySession:
        return session

    def fetch(_session: DummySession, url: str, timeout: int) -> BeautifulSoup:
        assert timeout == 7
        calls.append(url)
        query = parse_qs(urlparse(url).query)
        if query.get("mode") == ["view"]:
            return BeautifulSoup(_gongik_detail(item, wrong_title=wrong_detail), "lxml")
        return BeautifulSoup(_gongik_landing(), "lxml")

    return item, session, fetch, make_session, calls


def test_gongik_current_course_uses_json_sentinel_and_detail_application() -> None:
    item, session, fetch, make_session, calls = _gongik_fixture(current=True)
    rows, parser, meta = naju.collect_naju_gongik_courses(
        GongikTarget(), timeout=7, max_pages=3, detail_limit=1,
        fetcher=fetch, session_factory=make_session, today="2099-07-19",
        dedupe_rows=lambda values: values,
    )
    assert parser == naju.NAJU_GONGIK_PARSER
    assert len(rows) == 1
    assert rows[0]["provider_course_id"] == f"{naju.NAJU_GONGIK_PROVIDER}:lecture:501"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["reservation_available"] is True
    assert rows[0]["application_url"].endswith("idx=501&mode=write")
    assert rows[0]["branch"] == "나주시 공익활동지원센터"
    assert meta["source_total"] == 1
    assert meta["page_counts"] == {1: 1, 2: 0}
    assert meta["required_list_requests"] == 3
    assert meta["detail_pages"] == 1
    assert meta["snapshot_complete"] is True
    assert len(session.posts) == 2
    assert session.posts[0][1]["data"]["sub_mode"] == "all"
    assert calls == [naju.NAJU_GONGIK_URL, naju.naju_gongik_detail_url(item["idx"])]
    assert session.closed is True


def test_gongik_complete_expired_catalogue_is_no_current_data() -> None:
    _item, _session, fetch, make_session, calls = _gongik_fixture(current=False)
    rows, _parser, meta = naju.collect_naju_gongik_courses(
        GongikTarget(), timeout=7, max_pages=3, detail_limit=0,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert meta["source_total"] == 1
    assert meta["expired_count"] == 1
    assert meta["current_count"] == 0
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert calls == [naju.NAJU_GONGIK_URL]


def test_gongik_caps_sentinel_and_detail_mismatch_fail_closed() -> None:
    _item, session, fetch, make_session, _calls = _gongik_fixture(current=True)
    rows, _parser, meta = naju.collect_naju_gongik_courses(
        GongikTarget(), timeout=7, max_pages=2, detail_limit=1,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert len(session.posts) == 1
    assert meta["source_cap_reached"] is True
    assert "2 of 3 required list requests" in meta["configured_collection_error"]

    _item, _session, fetch, make_session, _calls = _gongik_fixture(
        current=True, nonempty_sentinel=True
    )
    rows, _parser, meta = naju.collect_naju_gongik_courses(
        GongikTarget(), timeout=7, max_pages=3, detail_limit=1,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "JSON sentinel page is not empty" in meta["configured_collection_error"]

    _item, _session, fetch, make_session, _calls = _gongik_fixture(
        current=True, wrong_detail=True
    )
    rows, _parser, meta = naju.collect_naju_gongik_courses(
        GongikTarget(), timeout=7, max_pages=3, detail_limit=1,
        fetcher=fetch, session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "detail/list title mismatch" in meta["configured_collection_error"]


def test_exact_targets_urls_dispatch_and_wrong_routes() -> None:
    assert naju.is_naju_lifelong_target(LifelongTarget())
    assert naju.is_naju_gongik_target(GongikTarget())
    assert naju.is_naju_education_target(LifelongTarget())
    assert naju.naju_lifelong_list_url(2).endswith(
        "page=2&search_startdate=2000-01-01&search_status=all"
    )
    assert naju.naju_lifelong_detail_url("2848").endswith("idx=2848&mode=view")
    assert naju.naju_lifelong_application_url("2842").endswith(
        "lecture_idx=2842&mode=reserve_form"
    )
    assert naju.naju_gongik_page_url(2).endswith("page=2&sub_mode=all")
    assert naju.naju_gongik_detail_url("506").endswith("idx=506&mode=view")
    assert not naju.naju_lifelong_list_url("bad")
    assert not naju.naju_gongik_detail_url("../506")

    wrong = LifelongTarget(url=naju.NAJU_LIFELONG_URL + "?search_status=all")
    assert not naju.is_naju_lifelong_target(wrong)
    rows, _parser, meta = naju.collect_naju_education_courses(wrong)
    assert rows == []
    assert "canonical Naju" in meta["configured_collection_error"]


def test_source_relationship_constants_are_explicit() -> None:
    assert naju.NAJU_MUNICIPALITY_CODE == "1217000000"
    assert naju.NAJU_LIFELONG_DUPLICATE_PROVIDER == "MUNI_WWW_NAJU_GO_KR_D8842639"
    assert naju.NAJU_LIFELONG_DUPLICATE_URLS[-1].endswith("/other")
    assert naju.NAJU_GONGIK_PROVIDER != naju.NAJU_LIFELONG_PROVIDER
