from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
import yaml
from bs4 import BeautifulSoup

from Crawler import Crawler_GeneratedYamlTargets as generated
from Crawler import Crawler_MunicipalIntegratedReservation as municipal_macro
from Crawler import Crawler_MunicipalYaml as municipal


PROVIDER = municipal.DALSEO_LEARNING_PROVIDER
TARGET_URL = municipal.DALSEO_LEARNING_HOME_URL


def _soup(value: str) -> BeautifulSoup:
    return BeautifulSoup(value, "lxml")


def _target(url: str = TARGET_URL, provider: str = PROVIDER) -> municipal.CrawlTarget:
    return municipal.CrawlTarget(
        provider=provider,
        name="달서구 교육포털",
        branch="대구광역시 달서구",
        url=url,
        source="test",
        priority=2,
        region="대구광역시",
        extra={
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
        },
    )


def _native_row(
    number: int,
    learning_id: str,
    status: str,
    organizer: str,
    *,
    title: str | None = None,
    period: str = "99.08.01.~99.08.31.",
    apply_period: str = "99.07.01 ~ 99.07.31",
    status_cell: str | None = None,
) -> str:
    title = title or f"공식 강좌 {learning_id}"
    status_cell = status_cell or f'<span class="s_btn blue">{status}</span>'
    return f"""
    <tr>
      <td>{number}</td>
      <td class="subject tal"><a href="javascript:;"
        onclick="fn_learning_detail('{learning_id}'); return false;">
        <span class="tit">{title}</span><span class="org">{organizer}</span></a></td>
      <td class="type tac"><span>무료</span><br/><span></span></td>
      <td class="tal"><span class="s_type blue"><em class="hidden">교육기간</em>
        {period}<span><pre>월, 10:00~12:00</pre></span></span></td>
      <td class="tal"><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
        <span class="s_type red1"><em class="hidden">일반접수</em>
        {apply_period} ( 접수인원 : 1 ) ( 대기 : 0 / 10 )</span></td>
      <td class="mobile">{status_cell}</td>
      <td class="tac"><a href="javascript:;"
        onclick="fn_learning_detail('{learning_id}'); return false;"><span>보기</span></a></td>
    </tr>
    """


def _external_row(
    number: int,
    *,
    host: str = "shared.example",
    href: str | None = None,
) -> str:
    href = href or f"https://{host}/courses/{number}"
    return f"""
    <tr>
      <td>{number}</td>
      <td class="subject tal"><a href="{href}" target="_blank">
        <span class="tit">외부 공유 강좌 {number}</span><span class="org">외부기관</span></a></td>
      <td class="type tac"><span>무료</span></td><td></td><td></td><td></td><td></td>
    </tr>
    """


def _list_page(rows: str, *, current: int, total: int, pages: int | None = None) -> str:
    total_pages = pages or max(
        1,
        (total + municipal.DALSEO_LEARNING_PAGE_SIZE - 1)
        // municipal.DALSEO_LEARNING_PAGE_SIZE,
    )
    return f"""
    <html><body><div id="content">
      <form id="learningVO" method="post"
        action="/yeyak/ilms/learning/learningList.do;jsessionid=FIXTURE.tomcat_1">
        <input name="pageIndex" value="{current}"/>
        <select name="pageUnit"><option value="10">10</option>
          <option value="50" selected="selected">50</option></select>
      </form>
      <span id="pageInfo_list">총 {total}건 ( {current}/{total_pages}페이지)</span>
      <table class="tbl lecture"><tbody>{rows}</tbody></table>
    </div></body></html>
    """


def _detail_page(
    learning_id: str,
    title: str,
    organizer: str,
    address: str,
    status: str,
    *,
    venue: str | None = None,
    education_period: str = "2099.08.01 ~ 2099.08.31",
    general_apply_period: str = "2099.07.01 09:00 ~ 2099.07.31 23:59",
) -> str:
    venue = venue or f"{organizer} 강의실"
    button = ""
    if status == "접수중":
        button = '<a id="learning_aply_btn">일반모집신청</a>'
    elif status == "대기접수":
        button = '<a id="learning_aply_btn">대기자신청</a>'
    pairs = {
        "회차명": "1회차",
        "강좌분류": "인문교양 > 생활소양",
        "교육대상": "성인",
        "문의전화": "053-667-0000",
        "교육장소": venue,
        "총 교육시간": "8시간",
        "교육기간": education_period,
        "교육시간": "월 10:00 ~ 12:00",
        "수강료": "무료",
        "재료비": "무료",
        "접수인원": "인터넷 접수 1명 / 정원 20명 ( 대기 : 0 / 10 )",
        "우선신청기간": "해당사항없음",
        "일반신청기간": general_apply_period,
        "모집방법": "선착순",
        "신청상태": status,
        "교육상태": "교육예정",
        "강좌소개": "공식 상세 설명",
        "강좌소개 첨부파일": "없음",
        "강사": "공식 강사",
        "강의계획서": "없음",
        "결제방법": "무료",
        "주소": address,
        "주의사항": "없음",
        "검색키워드": "교육",
        "강좌제한": "일반",
    }
    groups = "".join(
        f'<div class="form_group"><dl><dt>{key}</dt><dd>{value}</dd></dl></div>'
        for key, value in pairs.items()
    )
    return f"""
    <html><body><form id="learningVO" method="post"
      action="/yeyak/ilms/learning/learningDetail.do;jsessionid=FIXTURE.tomcat_1?lctr_id={learning_id}">
      <input name="lctr_id" value="{learning_id}"/>
      <div id="content"><div class="conWrap"><h2 class="enrolTit">
        <span>[{organizer}]</span> {title}</h2>{groups}{button}</div></div>
    </form></body></html>
    """


ORGANIZERS = (
    ("달서평생학습관", 81),
    ("디지털정보과 공공데이터팀", 6),
    ("더케이평생교육원", 2),
    ("달서구 평생교육과", 1),
    ("달서목재문화관", 1),
    ("달서50플러스센터", 25),
    ("달서구청소년문화의집", 1),
)


def _organizer_sequence() -> list[str]:
    return [name for name, count in ORGANIZERS for _ in range(count)]


def _full_fixture() -> tuple[dict[int, str], dict[str, str]]:
    entries: list[str] = []
    details: dict[str, str] = {}
    for number in range(354, 142, -1):
        entries.append(
            _external_row(number, host="shared.example" if number % 2 else "collision.example")
        )
    statuses = (
        ["접수중"] * 5
        + ["접수예정"] * 5
        + ["대기접수"] * 2
        + ["마감"] * 42
        + ["교육중"] * 63
    )
    organizers = _organizer_sequence()
    for index, number in enumerate(range(142, 0, -1)):
        learning_id = f"LEARNING_{10_000_000 + index:08d}"
        current = index < 117
        status = statuses[index] if current else "교육완료"
        organizer = organizers[index] if current else "달서평생학습관"
        period = "99.08.01.~99.08.31." if current else "20.01.01.~20.01.31."
        apply_period = "99.07.01 ~ 99.07.31" if current else "19.12.01 ~ 19.12.31"
        title = f"공식 강좌 {learning_id}"
        entries.append(
            _native_row(
                number,
                learning_id,
                status,
                organizer,
                title=title,
                period=period,
                apply_period=apply_period,
            )
        )
        if current:
            details[learning_id] = _detail_page(
                learning_id,
                title,
                organizer,
                municipal.DALSEO_LEARNING_CANONICAL_ADDRESSES[organizer],
                status,
            )
    pages = {
        page: _list_page(
            "".join(entries[(page - 1) * 50 : page * 50]),
            current=page,
            total=354,
        )
        for page in range(1, 9)
    }
    return pages, details


def _fixture_detail_fetcher(
    details: dict[str, str],
    fetched: list[str] | None = None,
) -> Callable[[object, str, int], BeautifulSoup]:
    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        assert timeout > 0
        if fetched is not None:
            fetched.append(url)
        learning_id = parse_qs(urlparse(url).query)["lctr_id"][0]
        return _soup(details[learning_id])

    return fetch


def test_dalseo_full_354_row_fixture_matches_live_contract(monkeypatch) -> None:
    pages, details = _full_fixture()
    fetched: list[str] = []
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "dalseo_learning_post_soup",
        lambda _session, page, _timeout: _soup(pages[page]),
    )
    monkeypatch.setattr(municipal, "fetch_soup", _fixture_detail_fetcher(details, fetched))

    rows, parser, meta = municipal.collect_from_url(
        _target(), timeout=7, max_depth=0, max_pages=100, detail_limit=200
    )

    assert parser == municipal.DALSEO_LEARNING_PARSER
    assert len(rows) == 117
    assert meta["pages"] == 8
    assert meta["total_pages"] == 8
    assert meta["total_count"] == 354
    assert meta["valid_count"] == 354
    assert meta["native_count"] == 142
    assert meta["external_count"] == 212
    assert meta["excluded_external_count"] == 212
    assert meta["expired_count"] == 25
    assert meta["current_count"] == 117
    assert meta["detail_pages"] == 117
    assert meta["detail_attempts"] == 117
    assert meta["logical_branch_count"] == 7
    assert meta["status_counts"] == {
        "OPEN": 5,
        "SCHEDULED": 5,
        "WAITING": 2,
        "CLOSED": 105,
    }
    assert meta["reservation_discovery_links"] == 7
    assert meta["pagination_complete"] is True
    assert meta["detail_enrichment_complete"] is True
    assert "configured_collection_error" not in meta
    assert len(fetched) == 117
    assert Counter(row["branch"] for row in rows) == dict(ORGANIZERS)
    assert all(row["provider_course_id"].startswith(f"{PROVIDER}:LEARNING_") for row in rows)
    assert all("learningDetail.do?lctr_id=LEARNING_" in row["raw_url"] for row in rows)
    assert all("shared.example" not in row["raw_url"] for row in rows)
    assert all("collision.example" not in row["raw_url"] for row in rows)
    assert sum(bool(row.get("application_url")) for row in rows) == 7
    assert all(row["service_group"] == "공공강좌" for row in rows)
    assert all(row["domain_category"] == "교육·강좌" for row in rows)


def test_dalseo_official_empty_list_is_complete_sentinel(monkeypatch) -> None:
    page = _list_page("", current=1, total=0)
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "dalseo_learning_post_soup",
        lambda *_args: _soup(page),
    )
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an official empty list must not request details")
        ),
    )

    rows, parser, meta = municipal.collect_dalseo_learning_courses(
        _target(), timeout=5, max_pages=2, detail_limit=20
    )

    assert rows == []
    assert parser == municipal.DALSEO_LEARNING_PARSER
    assert meta["pages"] == 1
    assert meta["total_pages"] == 1
    assert meta["total_count"] == 0
    assert meta["list_pagination_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["collection_complete"] is True
    assert meta["detail_attempts"] == 0
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == "official learning list is empty"
    assert "configured_collection_error" not in meta


def test_dalseo_missing_status_badge_after_application_end_is_detail_verified(
    monkeypatch,
) -> None:
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    apply_start = today - timedelta(days=42)
    apply_end = today - timedelta(days=30)
    learning_id = "LEARNING_00089351"
    title = "오늘 교육중인 공식 강좌"
    organizer = "달서평생학습관"
    selection_cell = '<span class="tit s_type2 navy"><em class="hidden">선착순</em></span>'
    page = _list_page(
        _native_row(
            1,
            learning_id,
            "",
            organizer,
            title=title,
            period=f"{today:%y.%m.%d}.~{today:%y.%m.%d}.",
            apply_period=f"{apply_start:%y.%m.%d} ~ {apply_end:%y.%m.%d}",
            status_cell=selection_cell,
        ),
        current=1,
        total=1,
    )
    details = {
        learning_id: _detail_page(
            learning_id,
            title,
            organizer,
            municipal.DALSEO_LEARNING_CANONICAL_ADDRESSES[organizer],
            "교육중",
            education_period=f"{today:%Y.%m.%d} ~ {today:%Y.%m.%d}",
            general_apply_period=(
                f"{apply_start:%Y.%m.%d} 09:00 ~ {apply_end:%Y.%m.%d} 18:00"
            ),
        )
    }
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "dalseo_learning_post_soup", lambda *_args: _soup(page))
    monkeypatch.setattr(municipal, "fetch_soup", _fixture_detail_fetcher(details))

    rows, _parser, meta = municipal.collect_dalseo_learning_courses(
        _target(), timeout=5, max_pages=2, detail_limit=2
    )

    assert len(rows) == 1
    assert rows[0]["status"] == "CLOSED"
    assert rows[0]["reservation_available"] is False
    assert "application_url" not in rows[0]
    assert rows[0]["raw_fields"]["list_selection_method"] == "선착순"
    assert rows[0]["raw_fields"]["list_status_inference"] == (
        "missing status badge after application period ended"
    )
    assert meta["pagination_complete"] is True
    assert meta["detail_pages"] == 1
    assert "configured_collection_error" not in meta


def test_dalseo_missing_status_badge_before_application_end_fails_closed(monkeypatch) -> None:
    selection_cell = '<span class="tit s_type2 navy"><em class="hidden">선착순</em></span>'
    page = _list_page(
        _native_row(
            1,
            "LEARNING_10000001",
            "",
            "달서평생학습관",
            status_cell=selection_cell,
        ),
        current=1,
        total=1,
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "dalseo_learning_post_soup", lambda *_args: _soup(page))
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an unproven missing status must block details")
        ),
    )

    rows, _parser, meta = municipal.collect_dalseo_learning_courses(
        _target(), timeout=5, max_pages=2, detail_limit=2
    )

    assert rows == []
    assert meta["invalid_count"] == 1
    assert meta["detail_attempts"] == 0
    assert meta["pagination_complete"] is False
    assert meta["no_current_data"] is False
    assert "classified 0 of 1" in meta["configured_collection_error"]


@pytest.mark.parametrize(
    "url",
    [
        "http://edu.dalseo.daegu.kr/",
        "https://www.edu.dalseo.daegu.kr/",
        "https://edu.dalseo.daegu.kr:443/",
        "https://edu.dalseo.daegu.kr/yeyak/",
        "https://edu.dalseo.daegu.kr/?page=1",
        "https://edu.dalseo.daegu.kr/#courses",
    ],
)
def test_dalseo_route_is_exact_official_portal_root(url: str) -> None:
    assert municipal.is_dalseo_learning_target(url) is False


def test_dalseo_canonical_urls_status_and_dispatch_are_stable(monkeypatch) -> None:
    assert municipal.is_dalseo_learning_target(TARGET_URL) is True
    assert municipal.dalseo_learning_detail_url("LEARNING_00089843") == (
        "https://edu.dalseo.daegu.kr/yeyak/ilms/learning/learningDetail.do?lctr_id=LEARNING_00089843"
    )
    for value in ("", "LEARNING_123", "learning_00089843", "LEARNING_00089843&x=1"):
        assert municipal.dalseo_learning_detail_url(value) == ""
    assert municipal.dalseo_learning_status("접수중") == "OPEN"
    assert municipal.dalseo_learning_status("접수예정") == "SCHEDULED"
    assert municipal.dalseo_learning_status("대기접수") == "WAITING"
    assert municipal.dalseo_learning_status("교육중") == "CLOSED"
    with pytest.raises(ValueError, match="owned official HTTPS portal root"):
        municipal.collect_dalseo_learning_courses(
            _target(provider="MUNI_UNOWNED"), timeout=5, max_pages=100, detail_limit=200
        )

    sentinel = ([{"title": "sentinel"}], municipal.DALSEO_LEARNING_PARSER, {"pages": 1})
    monkeypatch.setattr(municipal, "collect_dalseo_learning_courses", lambda *_a, **_k: sentinel)
    assert municipal.collect_from_url(
        _target(), timeout=5, max_depth=0, max_pages=100, detail_limit=200
    ) == sentinel


def _small_pages() -> tuple[dict[int, str], dict[str, str]]:
    first_rows = [_external_row(number) for number in range(51, 2, -1)]
    first_rows.append(
        _native_row(2, "LEARNING_10000001", "접수중", "달서평생학습관")
    )
    second_row = _native_row(1, "LEARNING_10000002", "접수예정", "달서목재문화관")
    details = {
        "LEARNING_10000001": _detail_page(
            "LEARNING_10000001",
            "공식 강좌 LEARNING_10000001",
            "달서평생학습관",
            municipal.DALSEO_LEARNING_CANONICAL_ADDRESSES["달서평생학습관"],
            "접수중",
        ),
        "LEARNING_10000002": _detail_page(
            "LEARNING_10000002",
            "공식 강좌 LEARNING_10000002",
            "달서목재문화관",
            municipal.DALSEO_LEARNING_CANONICAL_ADDRESSES["달서목재문화관"],
            "접수예정",
        ),
    }
    return {
        1: _list_page("".join(first_rows), current=1, total=51),
        2: _list_page(second_row, current=2, total=51),
    }, details


@pytest.mark.parametrize("failure", ["missing_declaration", "clamped", "changed_total", "page_cap"])
def test_dalseo_declared_pagination_failures_block_details(monkeypatch, failure: str) -> None:
    pages, details = _small_pages()
    max_pages = 2
    if failure == "missing_declaration":
        pages[1] = pages[1].replace('id="pageInfo_list"', 'id="missing-page-info"')
    elif failure == "clamped":
        pages[2] = pages[1]
    elif failure == "changed_total":
        pages[2] = pages[2].replace("총 51건", "총 52건")
    else:
        max_pages = 1
    fetched: list[str] = []
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "dalseo_learning_post_soup",
        lambda _session, page, _timeout: _soup(pages[page]),
    )
    monkeypatch.setattr(municipal, "fetch_soup", _fixture_detail_fetcher(details, fetched))

    rows, _parser, meta = municipal.collect_dalseo_learning_courses(
        _target(), timeout=5, max_pages=max_pages, detail_limit=20
    )

    assert len(rows) <= 2
    assert meta["pagination_complete"] is False
    assert meta["list_pagination_complete"] is False
    assert meta["detail_attempts"] == 0
    assert fetched == []
    assert "configured_collection_error" in meta
    if failure == "page_cap":
        assert "max_pages cap reached after 1 of 2" in meta["configured_collection_error"]


def test_dalseo_unowned_same_host_external_link_fails_closed(monkeypatch) -> None:
    page = _list_page(
        _external_row(2, href="https://edu.dalseo.daegu.kr/shared/course/2")
        + _native_row(1, "LEARNING_10000001", "접수중", "달서평생학습관"),
        current=1,
        total=2,
    )
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "dalseo_learning_post_soup",
        lambda *_args: _soup(page),
    )
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("malformed ownership classification must block details")
        ),
    )
    rows, _parser, meta = municipal.collect_dalseo_learning_courses(
        _target(), timeout=5, max_pages=2, detail_limit=20
    )
    assert len(rows) == 1
    assert meta["invalid_count"] == 1
    assert meta["detail_attempts"] == 0
    assert meta["pagination_complete"] is False
    assert "classified 1 of 2" in meta["configured_collection_error"]


@pytest.mark.parametrize("failure", ["cap", "detail", "missing_address", "id_mismatch"])
def test_dalseo_detail_failures_block_completeness(monkeypatch, failure: str) -> None:
    page = _list_page(
        _native_row(2, "LEARNING_10000001", "접수중", "달서평생학습관")
        + _native_row(1, "LEARNING_10000002", "접수예정", "달서목재문화관"),
        current=1,
        total=2,
    )
    details = {
        "LEARNING_10000001": _detail_page(
            "LEARNING_10000001",
            "공식 강좌 LEARNING_10000001",
            "달서평생학습관",
            municipal.DALSEO_LEARNING_CANONICAL_ADDRESSES["달서평생학습관"],
            "접수중",
        ),
        "LEARNING_10000002": _detail_page(
            "LEARNING_10000002",
            "공식 강좌 LEARNING_10000002",
            "달서목재문화관",
            municipal.DALSEO_LEARNING_CANONICAL_ADDRESSES["달서목재문화관"],
            "접수예정",
        ),
    }
    if failure == "missing_address":
        details["LEARNING_10000002"] = details["LEARNING_10000002"].replace(
            f'<dd>{municipal.DALSEO_LEARNING_CANONICAL_ADDRESSES["달서목재문화관"]}</dd>',
            "<dd></dd>",
        )
    elif failure == "id_mismatch":
        details["LEARNING_10000002"] = details["LEARNING_10000002"].replace(
            'name="lctr_id" value="LEARNING_10000002"',
            'name="lctr_id" value="LEARNING_19999999"',
        )
    calls: list[str] = []

    def fetch(_session: object, url: str, timeout: int) -> BeautifulSoup:
        learning_id = parse_qs(urlparse(url).query)["lctr_id"][0]
        calls.append(learning_id)
        if failure == "detail" and learning_id == "LEARNING_10000002":
            raise RuntimeError("fixture outage")
        return _soup(details[learning_id])

    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(municipal, "dalseo_learning_post_soup", lambda *_args: _soup(page))
    monkeypatch.setattr(municipal, "fetch_soup", fetch)
    rows, _parser, meta = municipal.collect_dalseo_learning_courses(
        _target(), timeout=5, max_pages=2, detail_limit=1 if failure == "cap" else 2
    )
    assert len(rows) == 2
    assert meta["pagination_complete"] is False
    assert meta["list_pagination_complete"] is True
    assert "configured_collection_error" in meta
    if failure == "cap":
        assert calls == ["LEARNING_10000001"]
        assert "detail enrichment capped at 1 of 2" in meta["configured_collection_error"]
    else:
        assert meta["detail_errors"] == 1
        assert "detail fetch failed for 1" in meta["configured_collection_error"]


def test_dalseo_partial_result_blocks_save_and_stale(monkeypatch) -> None:
    stale_calls: list[tuple[Any, ...]] = []
    partial_row = {
        "provider": PROVIDER,
        "provider_course_id": f"{PROVIDER}:LEARNING_10000001",
        "title": "부분 강좌",
        "branch": "달서평생학습관",
        "raw_url": municipal.dalseo_learning_detail_url("LEARNING_10000001"),
    }
    monkeypatch.setattr(municipal, "load_targets", lambda *_a, **_k: [_target()])
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_a, **_k: (
            [partial_row],
            municipal.DALSEO_LEARNING_PARSER,
            {
                "pages": 1,
                "pagination_complete": False,
                "configured_collection_error": "max_pages cap reached after 1 of 8 declared pages",
            },
        ),
    )
    monkeypatch.setattr(
        municipal,
        "get_db_connection",
        lambda: (_ for _ in ()).throw(
            AssertionError("partial collection must not open a DB transaction")
        ),
    )
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
    assert stale_calls == []


def test_dalseo_promoted_target_uses_only_full_macro_scheduler() -> None:
    target_document = yaml.safe_load(
        (municipal.ROOT / "config" / "crawl_targets" / "municipal_integrated_reservation.yaml").read_text(
            encoding="utf-8"
        )
    )
    target = next(row for row in target_document["targets"] if row["provider"] == PROVIDER)
    assert target["crawler_status"] == "ready"
    assert target["service_group"] == "공공강좌"
    assert target["service_group_policy"] == "locked"
    assert target["collection_type"] == municipal.DALSEO_LEARNING_PARSER
    assert target["ownership_scope"] == "native_learning_detail_only"
    assert target["external_url_policy"] == "excluded_shared_owner_candidates"

    operational = yaml.safe_load(
        (municipal.ROOT / "config" / "municipal_integrated_reservation_operational.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = next(row for row in operational["entries"] if row["provider"] == PROVIDER)
    assert entry["validation_outcome"] == "collected"
    assert entry["parser"] == municipal.DALSEO_LEARNING_PARSER
    assert entry["row_count"] == 117

    registry = yaml.safe_load(
        (municipal.ROOT / "config" / "generated_yaml_crawler_registry.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert PROVIDER not in {row["provider"] for row in registry["targets"]}
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
    assert parsed.parallel_workers == 3
