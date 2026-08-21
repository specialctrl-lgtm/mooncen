from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalIntegratedReservation as municipal_macro
from Crawler import Crawler_MunicipalYaml as municipal


PROVIDER = municipal.SACHEON_EDU_PROVIDER
TARGET_URL = municipal.SACHEON_EDU_LIST_URL


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target(url: str = TARGET_URL, provider: str = PROVIDER) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="사천시 통합예약 교육강좌",
        branch="경상남도 사천시",
        url=url,
        source="test",
        priority=1,
        region="경상남도",
        extra={
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
        },
    )


def _card(
    idx: str,
    status: str,
    *,
    title: str | None = None,
    category: str = "평생학습관",
    period: str = "2099.08.01. ~ 2099.08.31",
    apply_period: str = "2099.07.01. ~ 2099.07.31",
    page: int = 1,
    route_path: str = "",
    application_mode: str = "",
) -> str:
    title = title or f"공식 강좌 {idx}"
    route_prefix = route_path or ""
    mode = application_mode or ("ins" if route_path else "ins_realname")
    application = ""
    if status in {"신청하기", "대기자신청"}:
        application = (
            f'<a class="button muted radius" '
            f'href="{route_prefix}?amode={mode}&amp;lecIdx={idx}&amp;cpage={page}">'
            f'<span class="t1">{status}</span></a>'
        )
    elif status:
        application = f'<a class="button muted radius" href="#"><span class="t1">{status}</span></a>'
    return f"""
    <li class="li1"><div class="wrap1">
      <a class="col a1" href="{route_prefix}?amode=view&amp;idx={idx}&amp;cpage={page}">
        <span class="col texts">
          <span class="t2"><strong>[{category}]</strong></span>
          <strong class="t1">{title}</strong>
          <span class="t2">교육기간 : {period}</span>
          <span class="t2">교육시간 : 월요일 10시~12시</span>
          <span class="t2">모집기간 : {apply_period}</span>
          <span class="t2">모집대상 : 시민누구나 (사천시)</span>
          <span class="t2">모집인원 : 20 명</span>
          <span class="t2">접수방법 : 온라인접수</span>
        </span>
      </a>
      <div class="col btns">{application}</div>
      <div class="col btns02"></div>
    </div></li>
    """


def _list_page(cards: str, *, current: int, total_count: int, declared_pages: int | None = None) -> str:
    total_pages = declared_pages or max(
        1,
        (total_count + municipal.SACHEON_EDU_PAGE_SIZE - 1)
        // municipal.SACHEON_EDU_PAGE_SIZE,
    )
    return f"""
    <html><body><div id="body_content">
      <div class="infomenu1">
        <form id="frmLecture" method="get" action="/life/edu/00443.web">
          <input type="hidden" name="facCode" value="" />
          <select name="stype"><option value="title">과정명</option></select>
          <input name="sstring" value="" />
        </form>
        <div class="left"><div class="info1">
          총 <b class="em7">{total_count:,}</b>건의 교육이 있습니다.
          (<b class="em7">{current}</b>/{total_pages} 페이지)
        </div></div>
      </div>
      <div class="list1f1t2b2"><ul class="lst1">{cards}</ul></div>
      <div class="pagination"><span class="pages"><span class="m on">
        <a title="현재 {current} 페이지">{current}</a>
      </span></span></div>
    </div></body></html>
    """


VENUE_FIXTURES = (
    ("제일전산학원", "읍내1길 66, 지리산약국건물 4층", "제일전산학원"),
    ("사천시 사회종합복지관", "벌리6길 102", "사천시 종합사회복지관"),
    ("여성회관", "사천시 용현면 부곡3길90", "사천시 여성회관"),
    ("사천시 평생학습관", "무산로 21", "사천시 평생학습관"),
    ("서부사회복지관", "곤북로 20", "사천시 서부사회복지관"),
    ("장난감은행1호점", "사천읍 읍내로52", "사천시장난감은행 1호점"),
    ("장난감은행2호점", "벌용길54", "사천시장난감은행 2호점"),
    ("콩지은 교육농장", "경남 사천시 정동면 화암길 148", "콩지은 교육농장"),
)


def _detail_page(
    title: str,
    venue: str,
    address: str,
    *,
    period: str = "2099.08.01 ~ 2099.08.31",
    apply_period: str = "2099.07.01 ~ 2099.07.31",
    phone: str = "055-831-2595",
) -> str:
    return f"""
    <html><body><div id="body_content">
      <div class="view1pic1info1 panel5"><div class="texts">
        <h1 class="h1">{title}</h1>
        <div class="info1"><table class="t3 ttvam"><tbody>
          <tr><th>교육기간</th><td>{period}</td></tr>
          <tr><th>교육시간</th><td>월요일 10시~12시</td></tr>
          <tr><th>수강료</th><td>무료</td></tr>
          <tr><th>접수기간</th><td>{apply_period}</td></tr>
          <tr><th>모집대상</th><td>시민누구나</td></tr>
          <tr><th>모집지역</th><td>사천시</td></tr>
          <tr><th>교육장소</th><td>{venue}</td></tr>
          <tr><th>주소</th><td>{address}</td></tr>
          <tr><th>접수방법</th><td>온라인접수</td></tr>
          <tr><th>이용문의</th><td>{phone}</td></tr>
        </tbody></table></div>
      </div></div>
    </div></body></html>
    """


def _lifelong_detail_page(
    idx: str,
    title: str,
    status: str,
    *,
    period: str = "2099.08.01 ~ 2099.12.07",
    apply_period: str = "2099.07.01 ~ 2099.07.31",
) -> str:
    application = ""
    if status in {"접수중", "대기접수"}:
        application = (
            f'<a class="button apply" '
            f'href="?amode=ins_realname&amp;lecIdx={idx}&amp;facCode=001">신청하기</a>'
        )
    pairs = (
        ("period", "교육기간", period),
        ("time", "교육시간", "월요일 10시~13시"),
        ("fee", "수강료", "40,000 원"),
        ("apply-period", "접수기간", apply_period),
        ("target", "모집대상", "사천시민"),
        ("area", "모집지역", "관내"),
        ("address", "주소", "사천읍 무산로 21"),
        ("method", "접수방법", "온라인접수"),
        ("contact", "이용문의", "055-831-2595"),
    )
    items = "".join(
        f'<li class="{css_class}"><span class="t1">{label}</span>'
        f'<span class="t2">{value}</span></li>'
        for css_class, label, value in pairs
    )
    return f"""
    <html><body><div id="body_content">
      <div class="edu1view1">
        <span class="cate" data-process="{status}">{status}</span>
        <h3 class="hb2 h1">{title}</h3>
        <div class="cont"><div class="tg1"><ul>{items}</ul></div></div>
      </div>
      <div class="infomenu1">{application}</div>
    </div></body></html>
    """


def _fixture_fetcher(
    pages: dict[int, str],
    details: dict[str, str],
    fetched: list[str] | None = None,
) -> Callable[[object, str, int], BeautifulSoup]:
    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        if fetched is not None:
            fetched.append(url)
        query = parse_qs(urlparse(url).query)
        if query.get("amode") == ["view"]:
            return _soup(details[query["idx"][0]])
        return _soup(pages[int(query["cpage"][0])])

    return fetch


def test_sacheon_full_70_page_fixture_matches_live_contract(monkeypatch) -> None:
    status_by_index = (
        ["신청하기"] * 13
        + ["대기자신청"] * 4
        + ["접수대기"] * 65
        + ["접수마감"] * 14
    )
    cards: list[str] = []
    details: dict[str, str] = {}
    for index in range(698):
        idx = str(10_000 + index)
        current = index < 96
        page = index // 10 + 1
        venue, address, _canonical_venue = VENUE_FIXTURES[index % len(VENUE_FIXTURES)]
        status = status_by_index[index] if current else ""
        period = "2099.08.01. ~ 2099.08.31" if current else "2020.01.01. ~ 2020.01.31"
        apply_period = "2099.07.01. ~ 2099.07.31" if current else "2019.12.01. ~ 2019.12.31"
        title = f"공식 강좌 {idx}"
        cards.append(
            _card(
                idx,
                status,
                title=title,
                category=f"공식분류{index % 8 + 1}",
                period=period,
                apply_period=apply_period,
                page=page,
            )
        )
        if current:
            details[idx] = _detail_page(title, venue, address, phone="" if index < 4 else "055-831-2595")

    pages = {
        page: _list_page(
            "".join(cards[(page - 1) * 10 : page * 10]),
            current=page,
            total_count=698,
        )
        for page in range(1, 71)
    }
    fetched: list[str] = []
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", _fixture_fetcher(pages, details, fetched))

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=100, detail_limit=200
    )

    assert parser == municipal.SACHEON_EDU_PARSER
    assert len(rows) == 96
    assert meta["pages"] == 70
    assert meta["total_pages"] == 70
    assert meta["total_count"] == 698
    assert meta["discovered_links"] == 698
    assert meta["valid_count"] == 698
    assert meta["expired_count"] == 602
    assert meta["current_count"] == 96
    assert meta["detail_candidates"] == 96
    assert meta["detail_attempts"] == meta["detail_pages"] == 96
    assert meta["detail_errors"] == 0
    assert meta["reservation_discovery_links"] == 17
    assert meta["status_counts"] == {
        "OPEN": 13,
        "WAITING": 4,
        "SCHEDULED": 65,
        "CLOSED": 14,
    }
    assert meta["logical_venue_count"] == 8
    assert meta["pagination_complete"] is True
    assert meta["list_pagination_complete"] is True
    assert meta["pagination_exhausted"] is True
    assert meta["source_cap_reached"] is False
    assert meta["no_current_data"] is False
    assert "configured_collection_error" not in meta
    assert len([url for url in fetched if "cpage=" in url]) == 70
    assert len([url for url in fetched if "amode=view" in url]) == 96

    assert len({row["provider_course_id"] for row in rows}) == 96
    assert len({row["raw_url"] for row in rows}) == 96
    assert len({(row["venue_name"], row["venue_address"]) for row in rows}) == 8
    for row in rows:
        idx = row["raw_fields"]["lecture_idx"]
        assert row["provider_course_id"] == f"{PROVIDER}:lecture:{idx}"
        assert row["raw_url"] == municipal.sacheon_education_detail_url(idx)
        assert row["service_group"] == "공공강좌"
        assert row["service_group_policy"] == "locked"
        assert row["domain_category"] == "교육·강좌"
        assert row["branch"] == row["venue_name"]
        assert row["address"] == row["venue_address"]
        assert row["preserve_branch"] is True
        if row["status"] in {"OPEN", "WAITING"}:
            assert row["reservation_available"] is True
            assert row["application_url"] == municipal.sacheon_education_application_url(idx)
            assert row["application_type"] == "ONLINE_RESERVATION"
        else:
            assert row["reservation_available"] is False
            assert "application_url" not in row
            assert row["raw_fields"]["clear_application_url"] is True


def test_sacheon_lifelong_owned_route_is_enriched_and_identity_stable(monkeypatch) -> None:
    open_idx = "3261"
    closed_idx = "3262"
    open_title = "공식 평생학습관 강좌 3261"
    closed_title = "공식 평생학습관 강좌 3262"
    page = _list_page(
        _card(
            open_idx,
            "신청하기",
            title=open_title,
            route_path=municipal.SACHEON_EDU_LIFELONG_PATH,
            application_mode="ins",
        )
        + _card(
            closed_idx,
            "접수마감",
            title=closed_title,
            route_path=municipal.SACHEON_EDU_LIFELONG_PATH,
        ),
        current=1,
        total_count=2,
    )
    details = {
        open_idx: _lifelong_detail_page(open_idx, open_title, "접수중"),
        closed_idx: _lifelong_detail_page(closed_idx, closed_title, "접수마감"),
    }
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        _fixture_fetcher({1: page}, details),
    )

    rows, parser, meta = municipal.collect_sacheon_education_courses(
        _target(), timeout=5, max_pages=2, detail_limit=2
    )

    assert parser == municipal.SACHEON_EDU_PARSER
    assert len(rows) == 2
    assert meta["pagination_complete"] is True
    assert meta["list_pagination_complete"] is True
    assert meta["detail_attempts"] == meta["detail_pages"] == 2
    assert meta["detail_errors"] == 0
    assert meta["invalid_count"] == 0
    assert "configured_collection_error" not in meta

    by_idx = {row["raw_fields"]["lecture_idx"]: row for row in rows}
    for idx, row in by_idx.items():
        assert row["provider_course_id"] == f"{PROVIDER}:lecture:{idx}"
        assert row["raw_url"] == municipal.sacheon_education_detail_url(
            idx, municipal.SACHEON_EDU_LIFELONG_PATH
        )
        assert row["branch"] == row["venue_name"] == "사천시 평생학습관"
        assert row["address"] == row["venue_address"] == (
            "경남 사천시 사천읍 무산로 21"
        )
        assert row["fee"] == "40,000 원"
        assert row["raw_fields"]["lecture_route_path"] == municipal.SACHEON_EDU_LIFELONG_PATH
        assert row["raw_fields"]["detail_status_raw"] in {"접수중", "접수마감"}

    assert by_idx[open_idx]["status"] == "OPEN"
    assert by_idx[open_idx]["application_url"] == municipal.sacheon_education_application_url(
        open_idx, municipal.SACHEON_EDU_LIFELONG_PATH, "ins"
    )
    assert by_idx[open_idx]["reservation_available"] is True
    assert by_idx[closed_idx]["status"] == "CLOSED"
    assert by_idx[closed_idx]["reservation_available"] is False
    assert "application_url" not in by_idx[closed_idx]


def test_sacheon_unowned_same_host_lecture_route_fails_closed(monkeypatch) -> None:
    page = _list_page(
        _card(
            "3263",
            "신청하기",
            route_path="/life/edu/99999.web",
            application_mode="ins",
        ),
        current=1,
        total_count=1,
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        _fixture_fetcher({1: page}, {}),
    )

    rows, _parser, meta = municipal.collect_sacheon_education_courses(
        _target(), timeout=5, max_pages=2, detail_limit=2
    )

    assert rows == []
    assert meta["invalid_count"] == 1
    assert meta["list_pagination_complete"] is False
    assert meta["pagination_complete"] is False
    assert meta["detail_attempts"] == 0
    assert meta["no_current_data"] is False
    assert "listed lecture cards were malformed" in meta["configured_collection_error"]


def test_sacheon_official_empty_list_is_complete_sentinel(monkeypatch) -> None:
    page = _list_page("", current=1, total_count=0)
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        _fixture_fetcher({1: page}, {}),
    )

    rows, parser, meta = municipal.collect_sacheon_education_courses(
        _target(), timeout=5, max_pages=2, detail_limit=2
    )

    assert rows == []
    assert parser == municipal.SACHEON_EDU_PARSER
    assert meta["total_count"] == 0
    assert meta["total_pages"] == 1
    assert meta["pagination_complete"] is True
    assert meta["list_pagination_complete"] is True
    assert meta["no_data_placeholders"] == 1
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == "explicit zero total"
    assert "configured_collection_error" not in meta


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sacheon.go.kr/life/edu/00443.web",
        "https://sacheon.go.kr/life/edu/00443.web",
        "https://evil.www.sacheon.go.kr/life/edu/00443.web",
        "https://www.sacheon.go.kr:443/life/edu/00443.web",
        "https://www.sacheon.go.kr/life/edu/00449.web",
        "https://www.sacheon.go.kr/life/edu/00443.web?cpage=0",
        "https://www.sacheon.go.kr/life/edu/00443.web?facCode=002",
        "https://www.sacheon.go.kr/life/edu/00443.web#fragment",
    ],
)
def test_sacheon_route_is_exact_owned_aggregate(url: str) -> None:
    assert municipal.is_sacheon_education_target(url) is False


def test_sacheon_canonical_urls_and_dispatch_are_stable(monkeypatch) -> None:
    assert municipal.sacheon_education_status("신청마감") == "CLOSED"
    assert municipal.is_sacheon_education_target(TARGET_URL) is True
    assert municipal.is_sacheon_education_target(f"{TARGET_URL}?cpage=70") is True
    assert municipal.sacheon_education_detail_url("3286") == (
        "https://www.sacheon.go.kr/life/edu/00443.web?amode=view&idx=3286"
    )
    assert municipal.sacheon_education_application_url("3286") == (
        "https://www.sacheon.go.kr/life/edu/00443.web?amode=ins_realname&lecIdx=3286"
    )
    for value in ("", "32x", "../admin", "3286&next=https://evil.example", "1" * 13):
        assert municipal.sacheon_education_detail_url(value) == ""
        assert municipal.sacheon_education_application_url(value) == ""
    with pytest.raises(ValueError, match="owned official HTTPS aggregate"):
        municipal.collect_sacheon_education_courses(
            _target(provider="MUNI_UNOWNED"), timeout=5, max_pages=100, detail_limit=200
        )

    sentinel = ([{"title": "sentinel"}], municipal.SACHEON_EDU_PARSER, {"pages": 1})
    monkeypatch.setattr(municipal, "collect_sacheon_education_courses", lambda *_args, **_kwargs: sentinel)
    assert municipal.collect_from_url(
        _target(), timeout=5, max_depth=0, max_pages=100, detail_limit=200
    ) == sentinel


def test_sacheon_archived_rows_allow_historical_empty_application_method_only() -> None:
    archived_html = _card(
        "2058",
        "",
        period="2023.10.27. ~ 2023.10.27",
        apply_period="2023.10.04. ~ 2023.10.18",
    ).replace("접수방법 : 온라인접수", "접수방법 :")
    archived_card = _soup(archived_html).select_one("li.li1")
    assert archived_card is not None
    archived = municipal.sacheon_education_list_row(_target(), archived_card, TARGET_URL)
    assert archived is not None
    assert municipal.should_skip_expired_course(archived) is True

    current_html = _card("3289", "접수대기").replace(
        "접수방법 : 온라인접수", "접수방법 :"
    )
    current_card = _soup(current_html).select_one("li.li1")
    assert current_card is not None
    assert municipal.sacheon_education_list_row(_target(), current_card, TARGET_URL) is None


@pytest.mark.parametrize("failure", ["missing_declaration", "clamped", "changed_total", "page_cap"])
def test_sacheon_declared_and_clamped_pagination_fail_closed(monkeypatch, failure: str) -> None:
    first = _list_page(_card("1001", "신청하기", page=1), current=1, total_count=11)
    second = _list_page(_card("1002", "신청하기", page=2), current=2, total_count=11)
    max_pages = 2
    if failure == "missing_declaration":
        first = first.replace('class="info1"', 'class="missing-info"')
    elif failure == "clamped":
        second = _list_page(_card("1001", "신청하기", page=1), current=1, total_count=11)
    elif failure == "changed_total":
        second = _list_page(_card("1002", "신청하기", page=2), current=2, total_count=12)
    else:
        max_pages = 1

    pages = {1: first, 2: second}
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        _fixture_fetcher(
            pages,
            {"1001": _detail_page("공식 강좌 1001", "여성회관", "부곡3길90")},
        ),
    )

    rows, _parser, meta = municipal.collect_sacheon_education_courses(
        _target(), timeout=5, max_pages=max_pages, detail_limit=20
    )

    assert len(rows) <= 1
    assert meta["pagination_complete"] is False
    assert meta["list_pagination_complete"] is False
    assert meta["no_current_data"] is False
    assert meta["detail_attempts"] == 0
    assert "configured_collection_error" in meta
    if failure == "clamped":
        assert "page 2 did not expose a valid current/total page declaration" in meta["configured_collection_error"]
    if failure == "changed_total":
        assert "changed the declaration" in meta["configured_collection_error"]
    if failure == "page_cap":
        assert "max_pages cap reached after 1 of 2" in meta["configured_collection_error"]


@pytest.mark.parametrize("failure", ["cap", "detail", "missing_address"])
def test_sacheon_detail_cap_failure_or_core_gap_blocks_completeness(monkeypatch, failure: str) -> None:
    page = _list_page(
        _card("1001", "신청하기") + _card("1002", "접수대기"),
        current=1,
        total_count=2,
    )
    details = {
        "1001": _detail_page("공식 강좌 1001", "여성회관", "사천시 용현면 부곡3길90"),
        "1002": _detail_page("공식 강좌 1002", "사천시 평생학습관", "사천읍 무산로 21"),
    }
    if failure == "missing_address":
        details["1002"] = details["1002"].replace("<td>사천읍 무산로 21</td>", "<td></td>")

    calls: list[str] = []

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        query = parse_qs(urlparse(url).query)
        if query.get("amode") != ["view"]:
            return _soup(page)
        idx = query["idx"][0]
        calls.append(idx)
        if failure == "detail" and idx == "1002":
            raise RuntimeError("fixture detail outage")
        return _soup(details[idx])

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "fetch_soup", fetch)

    rows, _parser, meta = municipal.collect_sacheon_education_courses(
        _target(), timeout=5, max_pages=2, detail_limit=1 if failure == "cap" else 2
    )

    assert len(rows) == 2
    assert meta["pagination_complete"] is False
    assert meta["list_pagination_complete"] is True
    assert meta["no_current_data"] is False
    assert "configured_collection_error" in meta
    if failure == "cap":
        assert calls == ["1001"]
        assert "detail enrichment capped at 1 of 2" in meta["configured_collection_error"]
    else:
        assert meta["detail_errors"] == 1
        assert "detail fetch failed for 1" in meta["configured_collection_error"]


def test_sacheon_partial_result_blocks_save_and_stale(monkeypatch) -> None:
    save_calls: list[list[dict[str, Any]]] = []
    stale_calls: list[tuple[Any, ...]] = []
    partial_row = {
        "provider": PROVIDER,
        "provider_course_id": f"{PROVIDER}:lecture:1001",
        "title": "부분 수집 강좌",
        "branch": "사천시",
        "raw_url": municipal.sacheon_education_detail_url("1001"),
    }

    class FakeWriter:
        def __init__(self, provider: str) -> None:
            assert provider == PROVIDER

        def save_rows(self, rows: list[dict[str, Any]]) -> int:
            save_calls.append(rows)
            return len(rows)

    monkeypatch.setattr(municipal, "load_targets", lambda *_args, **_kwargs: [_target()])
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_args, **_kwargs: (
            [partial_row],
            municipal.SACHEON_EDU_PARSER,
            {
                "pages": 1,
                "pagination_complete": False,
                "configured_collection_error": "max_pages cap reached after 1 of 70 declared pages",
            },
        ),
    )
    monkeypatch.setattr(
        municipal,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("partial collection must not open a DB transaction")),
    )
    monkeypatch.setattr(municipal, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(municipal, "mark_stale_courses", lambda *args: stale_calls.append(args) or 0)

    reports = municipal.run(
        source="municipal",
        target_limit=None,
        per_target_limit=0,
        min_score=0,
        include_review=True,
        save_db=True,
        mark_stale=True,
        max_depth=0,
        max_pages=1,
        detail_limit=1,
        timeout=5,
    )

    assert reports[0].saved == 0
    assert reports[0].configured_collection_error.startswith("max_pages cap reached")
    assert save_calls == []
    assert stale_calls == []


def test_sacheon_promoted_target_uses_only_the_full_macro_scheduler() -> None:
    target_document = yaml.safe_load(
        (municipal.ROOT / "config" / "crawl_targets" / "municipal_integrated_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    target = next(row for row in target_document["targets"] if row["provider"] == PROVIDER)
    assert target["crawler_status"] == "ready"
    assert target["service_group"] == "공공강좌"
    assert target["service_group_policy"] == "locked"
    assert target["collection_type"] == municipal.SACHEON_EDU_PARSER

    old_target_document = yaml.safe_load(
        (municipal.ROOT / "config" / "crawl_targets" / "lifelong_learning.yaml").read_text(
            encoding="utf-8"
        )
    )
    old_target = next(
        row
        for row in old_target_document["targets"]
        if row["provider"] == "MUNI_WWW_SACHEON_GO_KR_8643DE0C"
    )
    assert old_target["superseded_by"] == PROVIDER
    assert old_target["disabled_reason"] == f"superseded_by:{PROVIDER}"

    operational = yaml.safe_load(
        (municipal.ROOT / "config" / "municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = next(row for row in operational["entries"] if row["provider"] == PROVIDER)
    assert entry["validation_outcome"] == "collected"
    assert entry["parser"] == municipal.SACHEON_EDU_PARSER
    assert entry["row_count"] == 95

    registry = yaml.safe_load(
        (municipal.ROOT / "config" / "generated_yaml_crawler_registry.yaml").read_text(encoding="utf-8")
    )
    assert PROVIDER not in {row["provider"] for row in registry["targets"]}
    old_registry = next(
        row
        for row in registry["targets"]
        if row["provider"] == "MUNI_WWW_SACHEON_GO_KR_8643DE0C"
    )
    assert old_registry["enabled"] is False
    selected = municipal_macro.load_municipal_targets(scheduled_providers=set())
    assert PROVIDER in {row["provider"] for row in selected}

    runner_text = (municipal.ROOT / "run_crawlers.py").read_text(encoding="utf-8")
    command_match = __import__("re").search(
        r'"MUNICIPAL_RESERVATION_TARGETS"\s*:\s*\[(.*?)\n\s*\],',
        runner_text,
        __import__("re").DOTALL,
    )
    assert command_match is not None
    command = __import__("re").findall(r'"([^"]+)"', command_match.group(1))
    script_index = next(index for index, value in enumerate(command) if value.endswith(".py"))
    parsed = generated.parse_args(command[script_index + 1 :])
    assert parsed.save_db is True
    assert parsed.mark_stale is True
    assert parsed.per_target_limit == 0
    assert parsed.allow_partial_save is False
    assert parsed.max_pages >= 100
    assert parsed.detail_limit >= 1200
