from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import inspect
import json
from typing import Any, Mapping

from Crawler import municipal_suncheon as suncheon


@dataclass(frozen=True)
class LmsTarget:
    provider: str = suncheon.SUNCHEON_LMS_PROVIDER
    name: str = "순천시 평생교육포털"
    branch: str = suncheon.SUNCHEON_MUNICIPALITY_NAME
    url: str = suncheon.SUNCHEON_LMS_URL


@dataclass(frozen=True)
class ReservationTarget:
    provider: str = suncheon.SUNCHEON_RESERVATION_PROVIDER
    name: str = "순천시 바로예약 교육"
    branch: str = suncheon.SUNCHEON_MUNICIPALITY_NAME
    url: str = suncheon.SUNCHEON_RESERVATION_URL


@dataclass(frozen=True)
class GardenTarget:
    provider: str = suncheon.SUNCHEON_GARDEN_PROVIDER
    name: str = "순천시 정원교육"
    branch: str = suncheon.SUNCHEON_MUNICIPALITY_NAME
    url: str = suncheon.SUNCHEON_GARDEN_URL


class DummyResponse:
    def __init__(
        self,
        value: str | Mapping[str, Any],
        *,
        status_code: int = 200,
    ) -> None:
        self.status_code = status_code
        self._value = value
        self.content = value.encode("utf-8") if isinstance(value, str) else b"{}"
        self.text = value if isinstance(value, str) else json.dumps(value)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Mapping[str, Any]:
        if not isinstance(self._value, Mapping):
            raise ValueError("not JSON")
        return self._value


class DummySession:
    def __init__(self, post_handler: Any = None) -> None:
        self.headers: dict[str, str] = {}
        self.closed = False
        self.post_handler = post_handler
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> Any:
        self.posts.append((url, kwargs))
        if self.post_handler is None:
            raise AssertionError(f"unexpected POST {url}")
        return self.post_handler(url, kwargs)

    def close(self) -> None:
        self.closed = True


def _factory(post_handler: Any = None) -> tuple[Any, list[DummySession]]:
    sessions: list[DummySession] = []

    def make_session() -> DummySession:
        current = DummySession(post_handler)
        sessions.append(current)
        return current

    return make_session, sessions


def _lms_item(
    identity: int,
    *,
    current: bool,
    status: str = "접수마감",
    branch: str = "순천시평생학습관",
    detail_branch: str = "평생학습관",
    apply_mismatch: bool = False,
) -> dict[str, Any]:
    return {
        "id": str(identity),
        "number": str(identity - 1000),
        "title": f"테스트 강좌 {identity}",
        "teacher": "홍길동",
        "branch": branch,
        "detail_branch": detail_branch,
        "apply_start": "99-06-01" if current else "20-01-01",
        "apply_end": "99-06-30" if current else "20-01-31",
        "detail_apply_start": (
            "2099-05-01" if current and apply_mismatch else "2099-06-01"
        )
        if current
        else "2020-01-01",
        "detail_apply_end": (
            "2099-05-31" if current and apply_mismatch else "2099-06-30"
        )
        if current
        else "2020-01-31",
        "start": "99-07-01" if current else "20-02-01",
        "end": "99-08-31" if current else "20-03-01",
        "full_start": "2099-07-01" if current else "2020-02-01",
        "full_end": "2099-08-31" if current else "2020-03-01",
        "status": status,
    }


def _lms_row(item: Mapping[str, Any]) -> str:
    return f"""
      <tr>
        <td>{item['number']}</td><td>{item['branch']}</td>
        <td><a href="javascript:goView('1', '{item['id']}')">{item['title']}<br/>({item['teacher']})</a></td>
        <td>20명</td><td>무료</td>
        <td>{item['apply_start']} 09시 ~ {item['apply_end']} 18시</td>
        <td>{item['start']}~{item['end']} 월 / 오전</td>
        <td><span>선착순</span><span>{item['status']}</span></td>
        <td><a href="javascript:goView('1', '{item['id']}')">상세보기</a></td>
      </tr>
    """


def _lms_page(items: list[Mapping[str, Any]], *, total: int, page: int, last: int) -> str:
    headers = "".join(f"<th>{value}</th>" for value in suncheon._LMS_HEADERS)
    return f"""
      <html><body>
        <div>[전체 <span>{total}</span> 건, <span>{page}</span> /{last} page]</div>
        <div class="bbs_list"><table class="w100">
          <thead><tr>{headers}</tr></thead>
          <tbody>{''.join(_lms_row(item) for item in items)}</tbody>
        </table></div>
        <div class="pagination"><a onclick="nextPage(1)">1</a>
          <a onclick="nextPage({last})">end</a></div>
      </body></html>
    """


def _pairs_table(pairs: list[tuple[str, str]]) -> str:
    return "<table class='w100'><tbody>" + "".join(
        f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in pairs
    ) + "</tbody></table>"


def _lms_detail(item: Mapping[str, Any], *, missing_schedule: bool = False) -> str:
    first = [
        ("강의명", str(item["title"])),
        ("강사", str(item["teacher"])),
        ("모집구분", "선착순"),
        ("교육대상", "전체연령"),
        (
            "접수기간",
            f"{item['detail_apply_start']} 09시 ~ {item['detail_apply_end']} 18시",
        ),
        ("수강료", "무료"),
        ("교육기간", f"{item['full_start']} ~ {item['full_end']}"),
        ("모집정원", "20명"),
    ]
    second = [
        ("교육기관", str(item["detail_branch"])),
        ("교육장소", "2층 강의실"),
        ("문의전화", "061-749-0000"),
        ("강의일수", "8차시"),
        ("교육시간대", "오전"),
    ]
    if not missing_schedule:
        second.append(("교육일정", "월 10:00 ~ 12:00"))
    third = [("교육내용", f"{item['title']} 교육 내용")]
    action = (
        "<a href=\"javascript:goSubmit('')\">수강신청</a>"
        if str(item["status"]) in {"접수중", "접수마감"}
        else '<a href="javascript:void(0)">수강신청</a>'
    )
    return f"<html><body>{_pairs_table(first)}{_pairs_table(second)}{_pairs_table(third)}{action}</body></html>"


def _lms_fixture(
    *,
    broken_sentinel: bool = False,
    missing_schedule: bool = False,
) -> tuple[Any, Any, list[DummySession], list[dict[str, Any]]]:
    current = [
        _lms_item(
            2100,
            current=True,
            status="접수중",
            branch="순천시평생학습관",
            detail_branch="평생학습관",
            apply_mismatch=True,
        ),
        _lms_item(
            2099,
            current=True,
            status="접수준비",
            branch="별량별빛나루",
            detail_branch="별량 별빛나루",
        ),
    ]
    expired = [_lms_item(2098 - index, current=False) for index in range(8)]
    all_items = current + expired
    pages = {
        suncheon.suncheon_lms_list_url(1): _lms_page(
            all_items[:8], total=10, page=1, last=2
        ),
        suncheon.suncheon_lms_list_url(2): _lms_page(
            all_items[8:], total=10, page=2, last=2
        ),
        suncheon.suncheon_lms_list_url(3): _lms_page(
            [] if broken_sentinel else all_items[:8],
            total=10,
            page=3 if broken_sentinel else 1,
            last=2,
        ),
    }
    for item in current:
        pages[suncheon.suncheon_lms_detail_url(item["id"])] = _lms_detail(
            item,
            missing_schedule=missing_schedule and item is current[0],
        )
    calls: list[str] = []

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        calls.append(url)
        if url not in pages:
            raise AssertionError(f"unexpected GET {url}")
        return pages[url]

    make_session, sessions = _factory()
    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch, make_session, sessions, current


def _reservation_card(
    source: suncheon.SuncheonReservationSource,
    detail_id: str,
    title: str,
) -> str:
    return f"""
      <li><a href="javascript:fncRsrvIng('{source.product_id}','{detail_id}',
        '/image.jpg','{title}', 'RSV10003')"><img alt="{title}"/></a>
        <div class="box"><span class="cate">{source.product_name}</span>
        <span class="title"><strong>{title}</strong></span></div></li>
    """


def _reservation_page(cards: list[str], *, last: int = 1) -> str:
    return f"""
      <html><body><div id="cateList"><ul>{''.join(cards)}</ul>
        <div class="pageNum"><a href="javascript:fncPageMove('1')">first</a>
        <a href="javascript:fncPageMove('{last}')">end</a></div>
      </div></body></html>
    """


def _redirect_html(source: suncheon.SuncheonReservationSource, detail_id: str) -> str:
    return f"""
      <html><body><form id="listForm" action="/yeyak/program/calendar01/index.jsp">
        <input name="rsvGoodsId" value="{source.product_id}"/>
        <input name="dtlGoodsId" value="{detail_id}"/>
        <input name="goodsSeCd" value="RSV10003"/>
        <input name="rsvYmd" value=""/><input name="apntdtNo" value=""/>
        <input name="rsvPosblPd" value="30"/>
      </form></body></html>
    """


def _reservation_fixture(
    *,
    bad_sentinel: bool = False,
    calendar_product_mismatch: bool = False,
) -> tuple[Any, Any, list[DummySession], dict[str, str]]:
    detail_ids = {
        "digital_literacy": "DTL_INFO",
        "child_safety": "DTL_SAFE",
        "cpr": "DTL_CPR",
    }
    titles = {
        "digital_literacy": "시민 정보화 테스트",
        "child_safety": "교통안전교육A",
        "cpr": "심폐소생술 상설교육(수/목/금)",
    }
    pages: dict[str, str] = {}
    detail_to_source: dict[str, suncheon.SuncheonReservationSource] = {}
    for source in suncheon.SUNCHEON_RESERVATION_SOURCES:
        detail_id = detail_ids[source.code]
        detail_to_source[detail_id] = source
        card = _reservation_card(source, detail_id, titles[source.code])
        pages[suncheon.suncheon_reservation_list_url(source, 1)] = _reservation_page(
            [card]
        )
        pages[suncheon.suncheon_reservation_list_url(source, 2)] = _reservation_page(
            [card] if bad_sentinel and source.code == "child_safety" else []
        )
        pages[suncheon.suncheon_reservation_application_url(detail_id)] = (
            _redirect_html(source, detail_id)
        )

    calls: list[str] = []

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        calls.append(url)
        if url not in pages:
            raise AssertionError(f"unexpected GET {url}")
        return pages[url]

    def post_handler(url: str, kwargs: Mapping[str, Any]) -> DummyResponse:
        if "/calendar01/index.jsp" in url:
            return DummyResponse(
                '<html><script>comAjax.setUrl("/yeyak/program/'
                'selectCalendarList.json;jsessionid=TEST");</script></html>'
            )
        if "/selectCalendarList.json" not in url:
            raise AssertionError(f"unexpected POST {url}")
        data = json.loads(str(kwargs["data"]))
        detail_id = str(data["dtlGoodsId"])
        source = detail_to_source[detail_id]
        events: list[dict[str, Any]] = []
        if source.code == "child_safety":
            events = [
                {
                    "title": "예약하기 (0/25명)",
                    "url": "#",
                    "start": "2099-07-22",
                    "rsvYmd": "2099-07-22",
                    "apntdtNo": "1",
                    "rsvStartTime": "10:00",
                    "rsvEndTime": "12:00",
                    "dtlGoodsId": detail_id,
                }
            ]
        elif source.code == "cpr":
            events = [
                {
                    "title": "예약가능 (1/10명)",
                    "url": "#",
                    "start": "2099-07-23",
                    "rsvYmd": "2099-07-23",
                },
                {
                    "title": "예약가능 (0/10명)",
                    "url": "#",
                    "start": "2099-07-30",
                    "rsvYmd": "2099-07-30",
                },
            ]
        product_id = "WRONG" if calendar_product_mismatch and source.code == "cpr" else source.product_id
        return DummyResponse(
            {
                "calendarList": events,
                "rsvGoodsMgmt": {
                    "rsvGoodsId": product_id,
                    "rsvGoodsNm": source.product_name,
                    "sttusCd": "RSV11001",
                },
                "rsvPosblPd": "30",
            }
        )

    make_session, sessions = _factory(post_handler)
    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch, make_session, sessions, detail_ids


def _garden_row(
    identity: int,
    title: str,
    *,
    current: bool,
    status: str = "접수마감",
) -> str:
    apply_period = "99-06-01 ~ 99-06-02" if current else "20-01-01 ~ 20-01-02"
    education_period = "99-07-01 ~ 99-08-31" if current else "20-02-01 ~ 20-03-01"
    return f"""
      <tr onclick="goOrder('{identity}')"><td><a href="#" onclick="goOrder('{identity}')">{title}</a></td>
        <td>{apply_period}<br/>( {education_period} )</td><td>매주 월 10:00~12:00</td>
        <td>3/20</td><td>0 원</td><td><a>{status}</a></td></tr>
    """


def _garden_page(rows: list[str], *, no_data: bool = False) -> str:
    headers = "".join(f"<th>{value}</th>" for value in suncheon._GARDEN_HEADERS)
    body = (
        '<tr><td colspan="6">교육 데이터가 없습니다.</td></tr>'
        if no_data
        else "".join(rows)
    )
    return f"<html><body><table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></body></html>"


def _garden_fixture(
    *,
    bad_sentinel: bool = False,
    missing_login: bool = False,
) -> tuple[Any, Any, list[DummySession]]:
    current = _garden_row(104, "2099 정원관리사 양성교육", current=True)
    expired = _garden_row(103, "2020 시민정원사 양성교육", current=False)
    pages = {
        suncheon.SUNCHEON_GARDEN_URL: _garden_page([expired, current]),
        suncheon.suncheon_garden_sentinel_url(): (
            _garden_page([current]) if bad_sentinel else _garden_page([], no_data=True)
        ),
        suncheon.suncheon_garden_detail_url(104): (
            "<html><head><title>정원교육 예약</title></head><body>로그인 필요</body></html>"
            if missing_login
            else "<html><head><title>정원교육 예약</title></head>"
            "<body><a href='/scbay/login/index.jsp'>로그인</a></body></html>"
        ),
    }
    calls: list[str] = []

    def fetch(_session: Any, url: str, _timeout: int) -> str:
        calls.append(url)
        if url not in pages:
            raise AssertionError(f"unexpected GET {url}")
        return pages[url]

    make_session, sessions = _factory()
    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch, make_session, sessions


def test_target_ownership_builders_and_audited_exclusions() -> None:
    assert suncheon.is_suncheon_lms_target(LmsTarget())
    assert suncheon.is_suncheon_reservation_target(ReservationTarget())
    assert suncheon.is_suncheon_garden_target(GardenTarget())
    assert suncheon.is_suncheon_education_target(LmsTarget())
    assert not suncheon.is_suncheon_lms_target(
        {"provider": suncheon.SUNCHEON_LMS_PROVIDER, "url": suncheon.SUNCHEON_LMS_URL + "?mode=search"}
    )
    assert not suncheon.is_suncheon_reservation_target(
        {"provider": suncheon.SUNCHEON_RESERVATION_PROVIDER, "url": "http://www.sc.go.kr/yeyak/edu/0008/0001/"}
    )
    assert suncheon.suncheon_lms_list_url(2).endswith("nowPage=2")
    assert "iClassIdx=3219" in suncheon.suncheon_lms_detail_url("3219")
    assert suncheon.suncheon_reservation_application_url("DTL_B1601").endswith(
        "dtlGoodsId=DTL_B1601"
    )
    assert "swim" in " ".join(suncheon.SUNCHEON_WRONG_CATEGORY_URLS)
    assert "garden/0020/0013/0008" in " ".join(
        suncheon.SUNCHEON_STATIC_OR_DISCOVERY_URLS
    )
    lms_parameters = inspect.signature(
        suncheon.collect_suncheon_lms_courses
    ).parameters
    assert lms_parameters["max_pages"].default == 400
    assert lms_parameters["detail_limit"].default == 300
    assert {
        source.code: source.branch
        for source in suncheon.SUNCHEON_RESERVATION_SOURCES
    } == {
        "digital_literacy": "시청 교육장",
        "child_safety": "어린이안전교육",
        "cpr": "연향건강생활지원센터",
    }


def test_lms_collects_complete_history_replay_sentinel_and_details() -> None:
    fetch, make_session, sessions, _ = _lms_fixture()
    rows, parser, meta = suncheon.collect_suncheon_lms_courses(
        LmsTarget(),
        timeout=7,
        max_pages=3,
        detail_limit=2,
        fetcher=fetch,
        session_factory=make_session,
        today="2099-07-19",
        dedupe_rows=lambda values: values,
    )

    assert parser == suncheon.SUNCHEON_LMS_PARSER
    assert len(rows) == 2
    assert meta["source_total"] == meta["source_rows"] == 10
    assert meta["page_counts"] == {1: 8, 2: 2, 3: 8}
    assert meta["sentinel_mode"] == "page1_replay"
    assert meta["expired_count"] == 8
    assert meta["current_count"] == meta["detail_pages"] == 2
    assert meta["detail_apply_period_mismatch_count"] == 1
    assert meta["snapshot_complete"] is True
    assert rows[0]["branch"] == "평생학습관"
    assert rows[1]["branch"] == "별량 별빛나루"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["reservation_available"] is True
    assert rows[1]["status"] == "SCHEDULED"
    assert "061-749-0000" not in repr(rows)
    assert "홍길동" not in repr(rows)
    assert all(session.closed for session in sessions)


def test_lms_closed_course_may_retain_template_submit_action() -> None:
    item = _lms_item(3219, current=True, status="접수마감")
    page = suncheon.BeautifulSoup(
        _lms_page([item], total=1, page=1, last=1),
        "lxml",
    )
    rows, parse_errors = suncheon._lms_parse_page(
        page,
        suncheon.SUNCHEON_LMS_PROVIDER,
    )
    assert parse_errors == []
    assert len(rows) == 1

    errors, mismatch = suncheon._lms_detail_contract(
        rows[0],
        suncheon.BeautifulSoup(_lms_detail(item), "lxml"),
    )
    assert errors == []
    assert mismatch is False
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["reservation_available"] is False
    assert rows[0].get("application_url", "") == ""
    assert rows[0]["raw_fields"]["closed_application_action_retained"] is True
    assert rows[0]["venue_name"] == "2층 강의실"
    assert "061-749-0000" not in repr(rows[0])
    assert "홍길동" not in repr(rows[0])


def test_lms_caps_sentinel_and_detail_contract_fail_closed() -> None:
    fetch, make_session, _, _ = _lms_fixture()
    rows, _, meta = suncheon.collect_suncheon_lms_courses(
        LmsTarget(), max_pages=2, detail_limit=2, fetcher=fetch,
        session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert meta["source_cap_reached"] is True

    fetch, make_session, _, _ = _lms_fixture(broken_sentinel=True)
    rows, _, meta = suncheon.collect_suncheon_lms_courses(
        LmsTarget(), max_pages=3, detail_limit=2, fetcher=fetch,
        session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "sentinel" in meta["configured_collection_error"]

    fetch, make_session, _, _ = _lms_fixture(missing_schedule=True)
    rows, _, meta = suncheon.collect_suncheon_lms_courses(
        LmsTarget(), max_pages=3, detail_limit=2, fetcher=fetch,
        session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "missing LMS detail labels" in meta["configured_collection_error"]


def test_reservation_collects_all_sources_sentinels_redirects_and_calendars() -> None:
    fetch, make_session, sessions, _ = _reservation_fixture()
    rows, parser, meta = suncheon.collect_suncheon_reservation_courses(
        ReservationTarget(), timeout=7, max_pages=6, detail_limit=3,
        fetcher=fetch, session_factory=make_session, today="2099-07-19",
        dedupe_rows=lambda values: values,
    )

    assert parser == suncheon.SUNCHEON_RESERVATION_PARSER
    assert len(rows) == 2
    assert meta["source_total"] == 3
    assert meta["required_list_requests"] == meta["list_requests"] == 6
    assert meta["detail_pages"] == 3
    assert meta["calendar_landing_pages"] == meta["calendar_api_requests"] == 3
    assert meta["inactive_product_count"] == 1
    assert meta["calendar_event_count"] == 3
    assert meta["snapshot_complete"] is True
    assert {row["branch"] for row in rows} == {
        "어린이안전교육",
        "연향건강생활지원센터",
    }
    assert {row["capacity_total"] for row in rows} == {10, 25}
    assert all(row["reservation_available"] for row in rows)
    assert all(session.closed for session in sessions)


def test_reservation_retries_two_severed_detail_transactions_on_fresh_sessions() -> None:
    fetch, make_session, sessions, detail_ids = _reservation_fixture()
    severed_url = suncheon.suncheon_reservation_application_url(
        detail_ids["child_safety"]
    )
    severed_count = 0

    def flaky_fetch(session: Any, url: str, timeout: int) -> str:
        nonlocal severed_count
        if url == severed_url and severed_count < 2:
            severed_count += 1
            raise ConnectionError("fixture transport severed")
        return fetch(session, url, timeout)

    rows, _, meta = suncheon.collect_suncheon_reservation_courses(
        ReservationTarget(), timeout=7, max_pages=6, detail_limit=3,
        fetcher=flaky_fetch, session_factory=make_session, today="2099-07-19",
    )

    assert severed_count == 2
    assert len(rows) == 2
    assert meta["snapshot_complete"] is True
    assert meta["detail_errors"] == 0
    assert meta["session_count"] == 3
    assert all(session.closed for session in sessions)


def test_reservation_caps_sentinel_and_calendar_contract_fail_closed() -> None:
    fetch, make_session, _, _ = _reservation_fixture()
    rows, _, meta = suncheon.collect_suncheon_reservation_courses(
        ReservationTarget(), max_pages=5, detail_limit=3, fetcher=fetch,
        session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert meta["source_cap_reached"] is True

    fetch, make_session, _, _ = _reservation_fixture(bad_sentinel=True)
    rows, _, meta = suncheon.collect_suncheon_reservation_courses(
        ReservationTarget(), max_pages=6, detail_limit=3, fetcher=fetch,
        session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "sentinel" in meta["configured_collection_error"]

    fetch, make_session, _, _ = _reservation_fixture(calendar_product_mismatch=True)
    rows, _, meta = suncheon.collect_suncheon_reservation_courses(
        ReservationTarget(), max_pages=6, detail_limit=3, fetcher=fetch,
        session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "calendar product mismatch" in meta["configured_collection_error"]


def test_garden_collects_complete_table_query_sentinel_and_auth_detail() -> None:
    fetch, make_session, sessions = _garden_fixture()
    rows, parser, meta = suncheon.collect_suncheon_garden_courses(
        GardenTarget(), timeout=7, max_pages=2, detail_limit=1,
        fetcher=fetch, session_factory=make_session, today="2099-07-19",
        dedupe_rows=lambda values: values,
    )

    assert parser == suncheon.SUNCHEON_GARDEN_PARSER
    assert len(rows) == 1
    assert meta["source_total"] == 2
    assert meta["expired_count"] == 1
    assert meta["current_count"] == meta["detail_pages"] == 1
    assert meta["sentinel_mode"] == "query_empty"
    assert meta["snapshot_complete"] is True
    assert rows[0]["branch"] == "순천시정원지원센터"
    assert rows[0]["application_type"] == "ONLINE_RESERVATION_AUTH_REQUIRED"
    assert all(session.closed for session in sessions)


def test_garden_sentinel_and_auth_gate_fail_closed() -> None:
    fetch, make_session, _ = _garden_fixture(bad_sentinel=True)
    rows, _, meta = suncheon.collect_suncheon_garden_courses(
        GardenTarget(), max_pages=2, detail_limit=1, fetcher=fetch,
        session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "sentinel" in meta["configured_collection_error"]

    fetch, make_session, _ = _garden_fixture(missing_login=True)
    rows, _, meta = suncheon.collect_suncheon_garden_courses(
        GardenTarget(), max_pages=2, detail_limit=1, fetcher=fetch,
        session_factory=make_session, today="2099-07-19"
    )
    assert rows == []
    assert "auth gate missing login" in meta["configured_collection_error"]


def test_managed_injection_is_required_and_tls_is_never_disabled() -> None:
    for target in (LmsTarget(), ReservationTarget(), GardenTarget()):
        rows, _, meta = suncheon.collect_suncheon_education_courses(target)
        assert rows == []
        assert "managed fetcher" in meta["configured_collection_error"]

    source = inspect.getsource(suncheon)
    assert "verify=False" not in source
    assert "verify = False" not in source
    assert "allow_redirects=True" not in source


def test_stable_ids_deduplication_and_dates() -> None:
    fetch, make_session, _, _ = _lms_fixture()
    rows, _, meta = suncheon.collect_suncheon_lms_courses(
        LmsTarget(), max_pages=3, detail_limit=2, fetcher=fetch,
        session_factory=make_session, today=date(2099, 7, 19),
        dedupe_rows=lambda values: values,
    )
    assert meta["duplicate_count"] == 0
    assert meta["duplicate_url_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert len({row["provider_course_id"] for row in rows}) == len(rows)
    assert len({row["raw_url"] for row in rows}) == len(rows)
    assert all(row["end_date"] == "2099-08-31" for row in rows)

    fetch, make_session, _, _ = _lms_fixture()
    replayed, _, replay_meta = suncheon.collect_suncheon_lms_courses(
        LmsTarget(), max_pages=3, detail_limit=2, fetcher=fetch,
        session_factory=make_session, today=date(2099, 7, 19),
        dedupe_rows=lambda values: values,
    )
    assert replay_meta["snapshot_complete"] is True
    assert [row["provider_course_id"] for row in replayed] == [
        row["provider_course_id"] for row in rows
    ]

    reservation_ids: list[list[str]] = []
    for _ in range(2):
        fetch, make_session, _, _ = _reservation_fixture()
        reservation_rows, _, reservation_meta = (
            suncheon.collect_suncheon_reservation_courses(
                ReservationTarget(), max_pages=6, detail_limit=3, fetcher=fetch,
                session_factory=make_session, today="2099-07-19",
            )
        )
        assert reservation_meta["snapshot_complete"] is True
        reservation_ids.append(
            [row["provider_course_id"] for row in reservation_rows]
        )
    assert reservation_ids[0] == reservation_ids[1]

    garden_ids: list[list[str]] = []
    for _ in range(2):
        fetch, make_session, _ = _garden_fixture()
        garden_rows, _, garden_meta = suncheon.collect_suncheon_garden_courses(
            GardenTarget(), max_pages=2, detail_limit=1, fetcher=fetch,
            session_factory=make_session, today="2099-07-19",
        )
        assert garden_meta["snapshot_complete"] is True
        garden_ids.append([row["provider_course_id"] for row in garden_rows])
    assert garden_ids[0] == garden_ids[1]


def test_complete_empty_snapshots_are_authoritative() -> None:
    fetch, make_session, _, _ = _lms_fixture()
    rows, _, meta = suncheon.collect_suncheon_lms_courses(
        LmsTarget(), max_pages=3, detail_limit=0, fetcher=fetch,
        session_factory=make_session, today="2100-01-01",
    )
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["configured_collection_error"] == ""

    fetch, make_session, _, _ = _reservation_fixture()
    rows, _, meta = suncheon.collect_suncheon_reservation_courses(
        ReservationTarget(), max_pages=6, detail_limit=3, fetcher=fetch,
        session_factory=make_session, today="2100-01-01",
    )
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["configured_collection_error"] == ""

    fetch, make_session, _ = _garden_fixture()
    rows, _, meta = suncheon.collect_suncheon_garden_courses(
        GardenTarget(), max_pages=2, detail_limit=0, fetcher=fetch,
        session_factory=make_session, today="2100-01-01",
    )
    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["configured_collection_error"] == ""
