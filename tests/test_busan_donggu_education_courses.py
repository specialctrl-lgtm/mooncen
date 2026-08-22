from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from html import escape
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from Crawler import municipal_busan_donggu as donggu


@dataclass
class Target:
    provider: str = donggu.BUSAN_DONGGU_PROVIDER
    url: str = donggu.BUSAN_DONGGU_URL
    name: str = "부산 동구 통합예약"
    branch: str = "부산광역시 동구"


class DummySession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FixtureSite:
    def __init__(self) -> None:
        self.routes: dict[str, str] = {}
        self.calls: list[str] = []
        self.sessions: list[DummySession] = []

    def session_factory(self) -> DummySession:
        current = DummySession()
        self.sessions.append(current)
        return current

    def fetcher(self, _session: DummySession, url: str, _timeout: int) -> str:
        self.calls.append(url)
        try:
            return self.routes[url]
        except KeyError as exc:
            raise AssertionError(f"unexpected fixture URL: {url}") from exc


def _root_html() -> str:
    links = "".join(
        f'<a href="/reserve/index.donggu?menuCd={item.root_menu}">{item.label}</a>'
        for item in donggu.BUSAN_DONGGU_CATEGORIES
    )
    return (
        "<html><head><title>부산광역시동구 통합예약홈페이지</title></head>"
        f"<body>{links}</body></html>"
    )


def _paging(category: donggu.DongguCategory, page: int, total: int) -> str:
    current = donggu.busan_donggu_list_url(category, page)
    last = donggu.busan_donggu_list_url(category, total)
    last_link = (
        f'<li><a href="{escape(last)}"><img alt="마지막 페이지"></a></li>'
        if total > page
        else ""
    )
    return (
        '<div class="paging"><ul>'
        f'<li class="on"><a href="{escape(current)}">{page}</a></li>'
        f"{last_link}</ul></div>"
    )


def _card(
    category: donggu.DongguCategory,
    identity: str,
    *,
    title: str,
    source_status: str = "수강신청중",
    start: str = "20260725",
    end: str = "20260825",
    apply_start: str = "20260701",
    apply_end: str = "20260724",
    venue: str = "동구도서관 3층 다목적강의실",
    available: bool | None = None,
) -> str:
    if available is None:
        available = source_status in {"수강신청중", "수강신청중(대기자)"}
    detail = donggu.busan_donggu_detail_url(category, identity)
    return f"""
    <dl>
      <dt>
        <span class="mark"><a class="btn lectureBtn" href="#"
          onclick="javascript:fnRegProc('{identity}', '', {str(available).lower()});">{escape(source_status)}</a></span>
        <a href="{escape(detail)}">{escape(title)}</a>
      </dt>
      <dd><ul class="liw2">
        <li><span class="name">신청/모집</span> 신청자 (2/10) / 대기자 (1/3)</li>
        <li><span class="name">교육대상</span> 부산 동구 주민</li>
        <li><span class="name">교육기간</span> {start} ~ {end}</li>
        <li><span class="name">접수기간</span> {apply_start} ~ {apply_end}</li>
        <li><span class="name">교육장소</span> {escape(venue)}</li>
        <li><span class="name">기타경비</span> 무료</li>
      </ul></dd>
    </dl>
    """


def _list_html(
    category: donggu.DongguCategory,
    page: int,
    total: int,
    cards: list[str],
    *,
    paging: bool = True,
) -> str:
    title_prefix = "조회 및 신청" if category.code == "708" else "강좌조회 및 신청"
    paging_html = _paging(category, page, total) if paging else ""
    return f"""
    <html><head><title>{title_prefix} &lt; {category.label} &lt; 부산광역시동구통합예약</title></head>
    <body><div class="bbs_ltype2">{''.join(cards)}</div>{paging_html}</body></html>
    """


def _detail_html(
    category: donggu.DongguCategory,
    identity: str,
    *,
    title: str,
    source_status: str,
    start: str = "2026-07-25",
    end: str = "2026-08-25",
    apply_start: str = "2026-07-01 10:00",
    apply_end: str = "2026-07-24 18:00",
    venue: str = "동구도서관 3층 다목적강의실",
    available: bool | None = None,
) -> str:
    if available is None:
        available = source_status in {"수강신청중", "수강신청중(대기자)"}
    if category.code == "708":
        fields = [
            ("접수명", title),
            ("접수내용", "상세 접수 내용"),
            ("운영시작일", start),
            ("운영종료일", end),
            ("운영시간", "10:00 ~ 12:00"),
            ("운영대상", "부산 동구 주민"),
            ("접수시작일", apply_start),
            ("접수종료일", apply_end),
            ("신청가능인원", "2/10"),
            ("대기가능인원", "1/3"),
            ("장소", venue),
            ("문의전화", "051-440-0000"),
            ("교육장소주소", "부산광역시 동구 중앙대로 1"),
        ]
    else:
        fields = [
            ("강좌명", title),
            ("강좌내용", "상세 강좌 내용"),
            ("교육시작일", start),
            ("교육종료일", end),
            ("교육시간", "10:00 ~ 12:00"),
            ("교육대상", "부산 동구 주민"),
            ("접수시작일", apply_start),
            ("접수종료일", apply_end),
            ("신청가능인원", "2/10"),
            ("대기가능인원", "1/3"),
            ("교육장소", venue),
            ("교육문의전화", "051-440-0000"),
            ("기타경비", "무료"),
            ("강사명", "홍길동"),
            ("교육장소주소", "부산광역시 동구 중앙대로 1"),
        ]
    rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
        for label, value in fields
    )
    return f"""
    <html><head><title>상세조회 및 신청 &lt; {category.label} &lt; 부산광역시동구통합예약</title></head>
    <body><table>{rows}</table>
      <a href="#"
        onclick="javascript:fnRegProc('{identity}', '', {str(available).lower()});">{escape(source_status)}</a>
    </body></html>
    """


def _complete_site(
    *,
    expired: bool = False,
    malformed_historical_category: str | None = None,
) -> tuple[FixtureSite, dict[str, tuple[donggu.DongguCategory, str, str]]]:
    site = FixtureSite()
    site.routes[donggu.BUSAN_DONGGU_URL] = _root_html()
    records: dict[str, tuple[donggu.DongguCategory, str, str]] = {}
    statuses = {
        "701": "수강신청중",
        "702": "접수대기",
        "703": "기간마감",
        "706": "수강신청중(대기자)",
        "708": "인원마감",
    }
    venues = {
        "701": "온라인",
        "702": "동구도서관 3층 다목적강의실",
        "703": "구민정보교육센터",
        "706": "동구어린이영어도서관 3층 다목적강의실",
        "708": "동구여성인력개발센터(YMCA 3층)",
    }
    for offset, category in enumerate(donggu.BUSAN_DONGGU_CATEGORIES, start=1):
        identity = str(17000 + offset)
        title = f"{category.label} 테스트 강좌"
        source_status = "기간마감" if expired else statuses[category.code]
        start = "20250101" if expired else "20260725"
        end = "20251231" if expired else "20260825"
        apply_start = "20250101" if expired else "20260701"
        apply_end = "20250131" if expired else "20260724"
        if category.code == malformed_historical_category:
            title = ""
            start = end = apply_start = apply_end = ""
            source_status = "기간마감"
        card = _card(
            category,
            identity,
            title=title,
            source_status=source_status,
            start=start,
            end=end,
            apply_start=apply_start,
            apply_end=apply_end,
            venue=venues[category.code],
        )
        site.routes[donggu.busan_donggu_list_url(category, 1)] = _list_html(
            category, 1, 1, [card]
        )
        site.routes[donggu.busan_donggu_list_url(category, 2)] = _list_html(
            category, 2, 1, [], paging=False
        )
        if not expired and category.code != malformed_historical_category:
            detail = donggu.busan_donggu_detail_url(category, identity)
            site.routes[detail] = _detail_html(
                category,
                identity,
                title=title,
                source_status=source_status,
                venue=venues[category.code],
            )
            records[identity] = (category, title, source_status)
    return site, records


def _collect(site: FixtureSite, **kwargs):
    return donggu._collect_busan_donggu_district_courses(
        Target(),
        timeout=3,
        max_pages=10,
        detail_limit=20,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today="2026-07-20",
        max_workers=1,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("OTHER", donggu.BUSAN_DONGGU_URL),
        (donggu.BUSAN_DONGGU_PROVIDER, "http://www.bsdonggu.go.kr/reserve/index.donggu"),
        (donggu.BUSAN_DONGGU_PROVIDER, donggu.BUSAN_DONGGU_URL + "?menuCd=x"),
        (donggu.BUSAN_DONGGU_PROVIDER, "https://evil.example/reserve/index.donggu"),
        (donggu.BUSAN_DONGGU_PROVIDER, donggu.BUSAN_DONGGU_URL + "#fragment"),
    ],
)
def test_target_boundary_is_exact(provider: str, url: str) -> None:
    assert donggu.is_busan_donggu_target(Target(provider=provider, url=url)) is False
    rows, parser, meta = donggu.collect_busan_donggu_education_courses(
        Target(provider=provider, url=url)
    )
    assert rows == []
    assert parser == donggu.BUSAN_DONGGU_PARSER
    assert meta["snapshot_complete"] is False


def test_complete_five_category_snapshot_and_detail_variants() -> None:
    site, _records = _complete_site()
    rows, parser, meta = _collect(site)

    assert parser == donggu.BUSAN_DONGGU_PARSER
    assert len(rows) == 5
    assert meta["snapshot_complete"] is True
    assert meta["full_snapshot_validated"] is True
    assert meta["total_count"] == 5
    assert meta["unique_id_count"] == 5
    assert meta["current_count"] == 5
    assert meta["detail_pages"] == 5
    assert meta["sentinel_requests"] == 5
    assert meta["page_one_rechecks"] == 5
    assert meta["duplicate_count"] == 0
    assert meta["semantic_duplicate_count"] == 0
    assert set(meta["category_current_counts"]) == {
        item.label for item in donggu.BUSAN_DONGGU_CATEGORIES
    }
    assert {row["category"] for row in rows} == {
        item.label for item in donggu.BUSAN_DONGGU_CATEGORIES
    }
    assert all(
        row["provider_course_id"].startswith(
            f"{donggu.BUSAN_DONGGU_PROVIDER}:course:"
        )
        for row in rows
    )
    assert all(row["prefer_incoming_provider_course_id"] is True for row in rows)
    assert all(row["period"] == "2026-07-25 ~ 2026-08-25" for row in rows)
    assert all(row["capacity_total"] == 10 for row in rows)
    assert all(row["waitlist_total"] == 3 for row in rows)

    by_category = {row["category"]: row for row in rows}
    assert by_category["평생학습"]["branch"] == "온라인"
    assert by_category["도서관"]["branch"] == "동구도서관"
    assert by_category["어린이영어도서관"]["branch"] == "동구어린이영어도서관"
    assert by_category["일반"]["branch"] == "동구여성인력개발센터"
    assert by_category["일반"]["description"] == by_category["일반"]["title"]
    assert all("phone" not in row for row in rows)
    assert all("instructor" not in row for row in rows)
    assert all(
        row["raw_fields"]["contact_value_never_read"] for row in rows
    )
    assert all(
        row["raw_fields"]["free_form_detail_never_read"] for row in rows
    )

    assert "application_url" in by_category["평생학습"]
    assert "application_url" in by_category["어린이영어도서관"]
    assert "application_url" not in by_category["도서관"]
    assert "application_url" not in by_category["정보화교육"]
    assert "application_url" not in by_category["일반"]
    assert all(current.closed for current in site.sessions)


def test_historical_blank_tail_record_is_counted_but_not_returned() -> None:
    site, _records = _complete_site(
        expired=True, malformed_historical_category="701"
    )
    rows, _parser, meta = _collect(site)

    assert rows == []
    assert meta["snapshot_complete"] is True
    assert meta["no_current_data"] is True
    assert meta["total_count"] == 5
    assert meta["historical_invalid_count"] == 1
    assert meta["expired_count"] == 4
    assert meta["detail_required_count"] == 0


def test_fails_closed_when_detail_limit_is_below_current_count() -> None:
    site, _records = _complete_site()
    rows, _parser, meta = donggu._collect_busan_donggu_district_courses(
        Target(),
        max_pages=10,
        detail_limit=4,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today="2026-07-20",
        max_workers=1,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap 4" in meta["configured_collection_error"]
    assert not any("002000000" in url for url in site.calls)


def test_fails_closed_when_declared_pages_exceed_max_pages() -> None:
    site, _records = _complete_site()
    category = donggu.BUSAN_DONGGU_CATEGORIES[0]
    identity = "17001"
    card = _card(category, identity, title="평생학습 테스트 강좌")
    site.routes[donggu.busan_donggu_list_url(category, 1)] = _list_html(
        category, 1, 2, [card]
    )

    rows, _parser, meta = donggu._collect_busan_donggu_district_courses(
        Target(),
        max_pages=1,
        detail_limit=20,
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today="2026-07-20",
        max_workers=1,
    )

    assert rows == []
    assert meta["snapshot_complete"] is False
    assert meta["source_cap_reached"] is True
    assert "max_pages cap 1" in meta["configured_collection_error"]


def test_fails_closed_on_detail_identity_or_title_mismatch() -> None:
    site, records = _complete_site()
    identity = "17002"
    category, _title, source_status = records[identity]
    site.routes[donggu.busan_donggu_detail_url(category, identity)] = _detail_html(
        category,
        identity,
        title="다른 강좌명",
        source_status=source_status,
        venue="동구도서관 3층 다목적강의실",
    )

    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "detail title mismatch" in meta["configured_collection_error"]


def test_fails_closed_when_immediate_sentinel_exposes_a_row() -> None:
    site, _records = _complete_site()
    category = donggu.BUSAN_DONGGU_CATEGORIES[2]
    unexpected = _card(category, "19999", title="센티널 유령 강좌")
    site.routes[donggu.busan_donggu_list_url(category, 2)] = _list_html(
        category, 2, 1, [unexpected], paging=False
    )

    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "sentinel 2: unexpected rows" in meta["configured_collection_error"]


def test_unknown_source_status_is_never_guessed_open_or_closed() -> None:
    site, _records = _complete_site()
    category = donggu.BUSAN_DONGGU_CATEGORIES[4]
    card = _card(
        category,
        "17005",
        title="일반 테스트 강좌",
        source_status="확인필요",
        available=False,
        venue="동구여성인력개발센터(YMCA 3층)",
    )
    site.routes[donggu.busan_donggu_list_url(category, 1)] = _list_html(
        category, 1, 1, [card]
    )

    rows, _parser, meta = _collect(site)
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "unknown source status" in meta["configured_collection_error"]


def test_generated_urls_keep_category_and_stable_identity() -> None:
    category = donggu.BUSAN_DONGGU_CATEGORIES[1]
    list_query = parse_qs(urlparse(donggu.busan_donggu_list_url(category, 7)).query)
    detail_query = parse_qs(
        urlparse(donggu.busan_donggu_detail_url(category, "16608")).query
    )
    application_query = parse_qs(
        urlparse(donggu.busan_donggu_application_url(category, "16608")).query
    )

    assert list_query["menuCd"] == [category.list_menu]
    assert list_query["search_Status"] == ["T"]
    assert list_query["page_no"] == ["7"]
    assert detail_query == {"menuCd": [category.detail_menu], "data_Sid": ["16608"]}
    assert application_query == {
        "menuCd": [category.application_menu],
        "data_Sid": ["16608"],
    }
    assert donggu.busan_donggu_detail_url(category, "bad") == ""


class SourceResponse:
    def __init__(self, url: str, html: str) -> None:
        self.url = url
        self.content = html.encode("utf-8")
        self.status_code = 200
        self.headers: dict[str, str] = {}


def _platform_row(
    sequence: int,
    *,
    identity: str,
    title: str,
    external_url: str = "",
) -> str:
    if external_url:
        title_action = f'href="{escape(external_url, quote=True)}" target="_blank"'
        action = f'<a href="{escape(external_url, quote=True)}">수강신청</a>'
    else:
        onclick = f"fn_learning_detail('{identity}'); return false;"
        title_action = f'href="javascript:;" onclick="{onclick}"'
        action = f'<a href="javascript:;" onclick="{onclick}">수강신청</a>'
    return f"""
      <tr><td>{sequence}</td>
        <td class="subject"><a {title_action}><span class="tit">{escape(title)}</span>
          <span class="org">동구청</span></a></td>
        <td><span>무료</span><br><span>SECRET_LIST_INSTRUCTOR</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          2099.08.01~2099.08.31<pre>수, 14:00~16:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          2099.07.01~2099.07.31 ( 접수인원 : 3 )</span></td>
        <td><span class="s_type2 mint"><em class="hidden">선착순</em></span>
          <span class="s_btn blue">접수중</span></td><td>{action}</td></tr>
    """


def _platform_page(page: int, *, unmatched_external: bool = False) -> str:
    body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    if page == 1:
        category = donggu.BUSAN_DONGGU_CATEGORIES[0]
        external = donggu.busan_donggu_detail_url(
            category, "19999" if unmatched_external else "17001"
        )
        body = _platform_row(
            2,
            identity="LEARNING_00090001",
            title="공유 인문학 051-440-9999",
        ) + _platform_row(
            1,
            identity=external,
            title="동구 구청 원장 외부복제",
            external_url=external,
        )
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" method="post" action="{donggu.BUSAN_LIFELONG_LIST_PATH}">
          <input name="inst_id" value="{donggu.BUSAN_LIFELONG_DONGGU_OFFICE}">
          <input name="display_type" value="2"><input name="pageIndex" value="{page}">
          <input name="l_search_ch" value="0">
          <select id="o_search_ch"><option value="{donggu.BUSAN_LIFELONG_DONGGU_OFFICE}"
            selected>동구청</option></select>
          <select id="learning_state"><option value="0" selected>전체</option></select>
        </form>
        <table><thead><tr><th>번호</th><th>강좌명 / 교육기관</th>
          <th>재료비 / 강사</th><th>교육기간 / 교육시간</th>
          <th>신청기간 / 접수인원 / 대기자</th><th>상태</th><th>보기</th>
        </tr></thead><tbody>{body}</tbody></table>
        <a class="page_nextend" href="?pageIndex=1"
          onclick="fn_list(1,'');return false;">마지막</a>
      </body></html>
    """


def _platform_detail() -> str:
    safe = {
        "강좌분류": "인문교양",
        "교육대상": "부산시민",
        "교육장소": "동구 평생학습관",
        "총 교육시간": "8시간",
        "교육기간": "2099.08.01 ~ 2099.08.31",
        "교육시간": "수, 14:00~16:00",
        "수강료": "무료",
        "재료비": "없음",
        "우선모집기간": "해당없음",
        "일반모집기간": "2099.07.01 ~ 2099.07.31",
        "모집방법": "온라인 선착순",
        "신청상태": "일반 접수중",
        "교육상태": "교육예정",
        "결제방법": "무료",
    }
    secrets = {
        "회차명": "SECRET_SESSION",
        "문의전화": "SECRET_PLATFORM_PHONE 051-440-9999",
        "접수인원": "SECRET_ENROLLMENT 3 / 20",
        "강좌소개": "SECRET_PLATFORM_DESCRIPTION private@example.test",
        "강좌소개 첨부파일": "SECRET_PLATFORM_ATTACHMENT.hwp",
        "강사": "SECRET_PLATFORM_INSTRUCTOR 010-2222-3333",
        "강의계획서": "SECRET_PLATFORM_PLAN.pdf",
        "주의사항": "SECRET_PLATFORM_WARNING",
        "검색키워드": "SECRET_PLATFORM_KEYWORD",
        "강좌제한": "SECRET_PLATFORM_LIMIT",
    }
    definitions = "".join(
        f"<dl><dt>{escape(label)}</dt>"
        f"<dd>{escape(safe.get(label, secrets.get(label, '')))}</dd></dl>"
        for label in donggu._PLATFORM_DETAIL_REQUIRED
    )
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" method="post">
          <input name="inst_id" value="{donggu.BUSAN_LIFELONG_DONGGU_OFFICE}">
          <input name="lng_id" value="LEARNING_00090001">
        </form>
        <h2 class="enrolTit"><span>[동구청]</span>공유 인문학 051-440-9999</h2>
        <div class="form_group">{definitions}</div>
        <a id="learning_aply_btn" onclick="fn_learning_apply(); return false;">일반모집신청</a>
      </body></html>
    """


def _city_page(page: int) -> str:
    cards = ""
    if page == 1:
        values = (
            ("기관", "동구 초량1동 주민자치회"),
            ("대상", "제한없음"),
            ("장소", "초량1동 프로그램실"),
            ("일자", "[신청] 2099-07-01 ~ 2099-07-31 [행사] 2099-08-01 ~ 2099-08-31"),
            ("방법", "방문접수"),
            ("문의", "SECRET_CITY_CARD_PHONE 051-440-8888"),
        )
        definitions = "".join(
            f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>"
            for label, value in values
        )
        cards = f"""
          <li><a class="reserveItem" href="javascript:void(0);"
            onclick="fn_viewProgrm('467', '10001');return false;">
            <div class="infoBox"><p class="tit" title="주민센터 생활요가">주민센터 생활요가</p>
              <span class="statusMark possible">접수중</span><dl>{definitions}</dl></div>
          </a></li>
        """
    reserve_list = f'<ul class="reserveList">{cards}</ul>' if cards else ""
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="srchForm" name="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}">
          <select name="srchGugun"><option value="5" selected>동구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
        </form>{reserve_list}
        <div class="paginate"><a class="pgEnd"
          href="?curPage=1&amp;srchGugun=5&amp;srchResveInsttCd=33">마지막</a></div>
      </body></html>
    """


def _city_detail() -> str:
    values = (
        ("운영기간", "2099-08-01(토) ~ 2099-08-31(월)"),
        ("신청기간", "2099-07-01(수) 09:00 ~ 2099-07-31(금) 18:00"),
        ("취소여부", "취소 가능"),
        ("신청방법", "방문접수"),
        ("수강료", "0 원"),
        ("요일 /시간", "수 / 10:00 ~ 12:00"),
        ("문의전화", "SECRET_CITY_DETAIL_PHONE 051-440-7777"),
        ("운영기관", "동구 초량1동 주민자치회"),
        ("대상", "제한없음"),
        ("첨부파일", "SECRET_CITY_ATTACHMENT.hwp"),
    )
    definitions = "".join(
        f"<dl><dt>{escape(label)}</dt><dd>{escape(value)}</dd></dl>"
        for label, value in values
    )
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="viewForm" method="post">
          <input name="resveGroupSn" value="467"><input name="progrmSn" value="10001">
          <div class="contHeader"><h3 class="titPage">주민센터 생활요가
            <span class="statusMark possible">접수중</span></h3></div>
          <div class="reserveStateWrap"><div class="reserveStateInfo">{definitions}</div>
            <div class="reserveBtnWrap"><a class="btnTypeXL">방문예약</a></div></div>
          <div class="reserveDetail">SECRET_CITY_FREE_FORM private@example.test</div>
        </form>
      </body></html>
    """


class SourceBackend:
    def __init__(self, *, unmatched_external: bool = False) -> None:
        self.unmatched_external = unmatched_external
        self.calls: Counter[str] = Counter()
        self.sessions: list[DummySession] = []

    def session_factory(self) -> DummySession:
        session = DummySession()
        self.sessions.append(session)
        return session

    def fetcher(self, _session: DummySession, url: str, _timeout: int) -> SourceResponse:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.hostname == "lll.busan.go.kr":
            if parsed.path == donggu.BUSAN_LIFELONG_LIST_PATH:
                page = int(query["pageIndex"][0])
                self.calls[f"platform-list-{page}"] += 1
                return SourceResponse(
                    url,
                    _platform_page(
                        page, unmatched_external=self.unmatched_external
                    ),
                )
            if parsed.path == donggu.BUSAN_LIFELONG_DETAIL_PATH:
                self.calls["platform-detail"] += 1
                return SourceResponse(url, _platform_detail())
        if parsed.hostname == donggu.BUSAN_CITY_HOST:
            if parsed.path == donggu.BUSAN_CITY_LIST_PATH:
                page = int(query["curPage"][0])
                self.calls[f"city-list-{page}"] += 1
                return SourceResponse(url, _city_page(page))
            if parsed.path == donggu.BUSAN_CITY_DETAIL_PATH:
                self.calls["city-detail"] += 1
                return SourceResponse(url, _city_detail())
        raise AssertionError(f"unexpected or private route: {url}")


def _district_stub(*_args: Any, **_kwargs: Any):
    row = {
        "provider": donggu.BUSAN_DONGGU_PROVIDER,
        "provider_course_id": f"{donggu.BUSAN_DONGGU_PROVIDER}:course:17001",
        "title": "구청 강좌 051-440-6666",
        "branch": "동구도서관",
        "raw_url": donggu.busan_donggu_detail_url(
            donggu.BUSAN_DONGGU_CATEGORIES[0], "17001"
        ),
        "status": "CLOSED",
        "start_date": "2099-08-01",
        "end_date": "2099-08-31",
        "application_url": "",
        "reservation_available": False,
        "raw_fields": {
            "source_catalog": "donggu_five_category_catalogue",
            "application_form_fetched": False,
            "applicant_list_fetched": False,
        },
    }
    return [row], donggu.BUSAN_DONGGU_PARSER, {
        "snapshot_complete": True,
        "full_snapshot_validated": True,
        "request_count": 10,
        "detail_pages": 1,
        "sentinel_requests": 5,
        "page_one_rechecks": 5,
        "list_pages": 5,
        "total_count": 2,
        "unique_id_count": 2,
        "_district_source_identities": ("17001", "16999"),
    }


def test_atomic_three_ledger_union_exact_duplicate_suppression_and_privacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = SourceBackend()
    monkeypatch.setattr(
        donggu, "_collect_busan_donggu_district_courses", _district_stub
    )
    rows, parser, meta = donggu.collect_busan_donggu_education_courses(
        Target(),
        timeout=3,
        max_pages=10,
        detail_limit=3,
        fetcher=backend.fetcher,
        session_factory=backend.session_factory,
        today="2099-07-20",
        max_workers=1,
    )

    assert parser == donggu.BUSAN_DONGGU_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{donggu.BUSAN_DONGGU_PROVIDER}:course:17001",
        f"{donggu.BUSAN_DONGGU_PROVIDER}:lifelong:LEARNING_00090001",
        f"{donggu.BUSAN_DONGGU_PROVIDER}:reserve:467:10001",
    ]
    assert meta["district_source_rows"] == 2
    assert meta["platform_source_rows"] == 2
    assert meta["platform_external_duplicate_rows"] == 1
    assert meta["platform_native_rows"] == 1
    assert meta["platform_native_current_count"] == 1
    assert meta["city_source_rows"] == 1
    assert meta["city_current_count"] == 1
    assert meta["source_total"] == 5
    assert meta["unique_education_source_rows"] == 4
    assert meta["current_source_count"] == 3
    assert meta["required_list_requests"] == 16
    assert meta["required_detail_requests"] == 3
    assert meta["network_requests"] == 19
    assert meta["sentinel_requests"] == 8
    assert meta["stability_rechecks"] == 8
    assert meta["status_counts"] == {"CLOSED": 1, "OPEN": 2}
    assert meta["snapshot_complete"] is True
    assert all(session.closed for session in backend.sessions)
    serialized = repr(rows)
    assert "[redacted]" in serialized
    for secret in (
        "SECRET_LIST_INSTRUCTOR",
        "SECRET_SESSION",
        "SECRET_PLATFORM_PHONE",
        "SECRET_ENROLLMENT",
        "SECRET_PLATFORM_DESCRIPTION",
        "SECRET_PLATFORM_ATTACHMENT",
        "SECRET_PLATFORM_INSTRUCTOR",
        "SECRET_PLATFORM_PLAN",
        "SECRET_PLATFORM_WARNING",
        "SECRET_PLATFORM_KEYWORD",
        "SECRET_PLATFORM_LIMIT",
        "SECRET_CITY_CARD_PHONE",
        "SECRET_CITY_DETAIL_PHONE",
        "SECRET_CITY_ATTACHMENT",
        "SECRET_CITY_FREE_FORM",
        "private@example.test",
    ):
        assert secret not in serialized
    assert all(
        row["raw_fields"]["application_form_fetched"] is False for row in rows
    )
    assert all(
        row["raw_fields"]["applicant_list_fetched"] is False for row in rows
    )


def test_unmatched_platform_external_identity_discards_atomic_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = SourceBackend(unmatched_external=True)
    monkeypatch.setattr(
        donggu, "_collect_busan_donggu_district_courses", _district_stub
    )
    rows, _parser, meta = donggu.collect_busan_donggu_education_courses(
        Target(),
        max_pages=10,
        detail_limit=3,
        fetcher=backend.fetcher,
        session_factory=backend.session_factory,
        today="2099-07-20",
        max_workers=1,
    )
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert "absent from district census" in meta["configured_collection_error"]


def test_cross_source_urls_and_owner_partition_are_exact() -> None:
    assert donggu.BUSAN_DONGGU_CANDIDATE_ID == "MUNI_IR_54C60E9E98D9"
    assert donggu.busan_donggu_city_list_url(4).endswith(
        "curPage=4&srchGugun=5&srchResveInsttCd=33"
    )
    assert parse_qs(
        urlparse(donggu.busan_donggu_lifelong_list_url()).query
    )["pageUnit"] == ["1000"]
    detail = donggu.busan_donggu_detail_url(
        donggu.BUSAN_DONGGU_CATEGORIES[0], "17001"
    )
    assert donggu.canonical_busan_donggu_course_identity(detail) == (
        "data_sid:17001"
    )
    assert donggu.canonical_busan_donggu_course_identity(detail + "&x=1") == ""
    with pytest.raises(donggu.BusanDongguContractError):
        donggu.busan_donggu_lifelong_list_url(True)
    with pytest.raises(donggu.BusanDongguContractError):
        donggu.busan_donggu_city_detail_url("467", "https://evil.example")


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_AUDIT") != "1",
    reason="set RUN_LIVE_MUNICIPAL_AUDIT=1 for the full Busan Dong-gu audit",
)
def test_live_complete_three_ledger_snapshot_matches_exact_audit() -> None:
    rows, parser, meta = donggu.collect_busan_donggu_education_courses(
        Target(),
        timeout=30,
        max_pages=400,
        detail_limit=200,
        today="2026-07-22",
    )
    assert parser == donggu.BUSAN_DONGGU_PARSER
    assert meta["snapshot_complete"] is True
    assert meta["district_source_rows"] == 3641
    assert meta["district_current_count"] == 65
    assert meta["platform_source_rows"] == 266
    assert meta["platform_external_duplicate_rows"] == 50
    assert meta["platform_native_rows"] == 216
    assert meta["platform_native_current_count"] == 54
    assert meta["city_source_rows"] == 36
    assert meta["city_current_count"] == 36
    assert meta["source_total"] == 3943
    assert meta["unique_education_source_rows"] == 3893
    assert meta["current_source_count"] == 155
    assert meta["required_detail_requests"] == 155
    assert meta["network_requests"] == 526
    assert len(rows) == 155
    assert not any("@" in repr(row) for row in rows)
