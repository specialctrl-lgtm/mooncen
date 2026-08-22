from __future__ import annotations

from collections import Counter
from datetime import date
from html import escape
import os
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from Crawler import municipal_busan_dongnae as dongnae


class _Response:
    def __init__(self, url: str, html: str, status_code: int = 200):
        self.url = url
        self.content = html.encode("utf-8")
        self.text = html
        self.status_code = status_code
        self.history: list[Any] = []


class _Session:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _target(
    provider: str = dongnae.BUSAN_DONGNAE_PROVIDER,
    url: str = dongnae.BUSAN_DONGNAE_URL,
) -> dict[str, str]:
    return {"provider": provider, "url": url, "name": "부산 동래구 교육"}


def _local_row(
    identity: str,
    title: str,
    *,
    status: str,
    start: str,
    end: str,
) -> str:
    href = (
        f"/lll/index.dongnae?menuCd={dongnae.BUSAN_DONGNAE_DETAIL_MENU}"
        f"&amp;docNo={identity}&amp;title={escape(title, quote=True)}"
    )
    onclick = href.replace("&amp;", "&")
    return f"""
      <tr onclick="location.href='{onclick}';">
        <td><span>{escape(status)}</span></td>
        <td><a href="{href}">{escape(title)}</a></td>
        <td><span class="pc_hidden eduList">접수기간</span>
          2099-07-01<br>~2099-07-31</td>
        <td><span class="pc_hidden eduList">교육기간</span>
          {start}<br>~{end}</td>
        <td><span class="pc_hidden eduList">교육시간</span>매주 수, 10:00~12:00</td>
        <td><span class="pc_hidden eduList">교육장소</span>동래구 평생학습관</td>
        <td>3/20<br>(0/5)</td>
      </tr>
    """


def _local_page(
    page: int,
    *,
    drift: bool = False,
    bad_sentinel: bool = False,
) -> str:
    body = ""
    if page == 1 or bad_sentinel:
        title = "변경된 동래 인문학" if drift else "동래 인문학 550-4466"
        body = _local_row(
            "20990701000001",
            title,
            status="접수중",
            start="2099-08-01",
            end="2099-08-31",
        )
        body += _local_row(
            "20980701000002",
            "지난 동래 강좌",
            status="마감",
            start="2098-08-01",
            end="2098-08-31",
        )
    current_rows = 2 if body else 0
    # The sentinel keeps the authoritative total/page control but no rows.
    return f"""
      <html><head><title>수강신청 &lt; 평생학습강좌신청</title></head><body>
        <form id="frmSearch" name="frmSearch" method="post"
          action="/culture/index.dongnae?menuCd={dongnae.BUSAN_DONGNAE_MENU}">
          <select name="possible"><option value="">선택하세요</option></select>
          <input name="lec_title" value=""><input type="submit" value="검색">
        </form>
        <p class="board_total"><span>총게시물 :</span><span>2</span>,
          <span>페이지 :</span><span>{page}/1</span></p>
        <table class="basic bbs_list"><thead><tr>
          <th>모집</th><th>강좌명</th><th>접수기간</th><th>강좌기간</th>
          <th>교육시간</th><th>교육장소</th>
          <th>신청/정원 (대기신청 / 대기정원)</th>
        </tr></thead><tbody>{body if current_rows else ''}</tbody></table>
      </body></html>
    """


def _local_detail(*, wrong_title: bool = False) -> str:
    values = {
        "강좌명": "다른 동래 인문학" if wrong_title else "동래 인문학 550-4466",
        "교육기간": "2099-08-01 ~ 2099-08-31",
        "접수기간": "2099-07-01 ~ 2099-07-31",
        "접수시작시간": "0900",
        "접수종료시간": "1800",
        "교육요일": "수",
        "교육시간": "매주 수, 10:00~12:00",
        "교육신청자": "SECRET_LOCAL_ENROLLMENT 3 / 20",
        "대기자": "SECRET_LOCAL_WAITLIST 0 / 5",
        "교육장소": "동래구 평생학습관",
        "교육문의전화": "SECRET_LOCAL_PHONE 051-550-4466",
        "강사명": "SECRET_LOCAL_INSTRUCTOR 010-2222-3333",
        "수강료": "무료",
        "교육내용": "SECRET_LOCAL_DESCRIPTION local-private@example.test",
        "강좌상세정보URL": "",
        "첨부파일": "SECRET_LOCAL_ATTACHMENT.hwp",
    }
    rows = []
    for label in dongnae._LOCAL_DETAIL_LABELS:
        rows.append(
            f"<tr><th>{escape(label)}</th><td>{escape(values[label])}</td></tr>"
        )
    return f"""
      <html><head><title>수강신청 &lt; 평생학습강좌신청 &lt; 교육보기</title></head><body>
        <form id="frmWrite" name="frmWrite" method="post"
          action="{dongnae.BUSAN_DONGNAE_PATH}">
          <input name="contentsSid" value="1606">
          <table class="tb_t2">{''.join(rows)}</table>
          <div class="btnArea_right"><a href="#none"
            onclick="javascript:goLogin('W');"><img alt="신청"></a></div>
        </form>
      </body></html>
    """


def _platform_row(
    sequence: int,
    *,
    identity: str,
    title: str,
    start: str,
    end: str,
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
          <span class="org">동래구청</span></a></td>
        <td><span>무료</span><br><span>SECRET_LIST_INSTRUCTOR</span></td>
        <td><span class="s_type blue"><em class="hidden">교육기간</em>
          {start}~{end}<pre>수, 14:00~16:00</pre></span></td>
        <td><span class="s_type indigo1"><em class="hidden">모집인원</em>20명</span>
          <span class="s_type red1"><em class="hidden">일반접수</em>
          2099.07.01~2099.07.31 ( 접수인원 : 3 )</span></td>
        <td><span class="s_type2 mint"><em class="hidden">선착순</em></span>
          <span class="s_btn blue">접수중</span></td><td>{action}</td></tr>
    """


def _platform_page(
    page: int,
    *,
    drift: bool = False,
    bad_sentinel: bool = False,
    unmatched_external: bool = False,
    changed_test: bool = False,
) -> str:
    body = ""
    if page == 1:
        external_id = "20990701999999" if unmatched_external else "20990701000001"
        external = (
            f"https://{dongnae.BUSAN_DONGNAE_HOST}{dongnae.BUSAN_DONGNAE_PATH}?"
            f"menuCd={dongnae.BUSAN_DONGNAE_DETAIL_MENU}&docNo={external_id}"
        )
        body = _platform_row(
            3,
            identity="LEARNING_00090001",
            title="변경된 공유 인문학" if drift else "공유 인문학",
            start="2099.08.01",
            end="2099.08.31",
        )
        body += _platform_row(
            2,
            identity=external,
            title="동래 인문학 외부연계",
            start="2099.08.01",
            end="2099.08.31",
            external_url=external,
        )
        body += _platform_row(
            1,
            identity="LEARNING_00087443",
            title="변경된 테스트" if changed_test else "테스트 강좌 (신청하기 연습)",
            start="2026.12.01",
            end="2026.12.31",
        )
    elif bad_sentinel:
        body = _platform_row(
            1,
            identity="LEARNING_00090002",
            title="경계 이탈 강좌",
            start="2099.08.01",
            end="2099.08.31",
        )
    else:
        body = '<tr><td colspan="7">등록된 교육강좌가 없습니다.</td></tr>'
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" method="post" action="{dongnae.BUSAN_LIFELONG_LIST_PATH}">
          <input name="inst_id" value="{dongnae.BUSAN_LIFELONG_DONGNAE_OFFICE}">
          <input name="display_type" value="2"><input name="pageIndex" value="{page}">
          <input name="l_search_ch" value="0">
          <select id="o_search_ch"><option value="{dongnae.BUSAN_LIFELONG_DONGNAE_OFFICE}"
            selected>동래구청</option></select>
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


def _platform_detail(*, wrong_title: bool = False) -> str:
    safe = {
        "강좌분류": "인문교양",
        "교육대상": "부산시민",
        "교육장소": "동래구 평생학습관",
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
    skipped = {
        "회차명": "SECRET_SESSION",
        "문의전화": "SECRET_PLATFORM_PHONE 051-550-4466",
        "접수인원": "SECRET_ENROLLMENT 3 / 20",
        "강좌소개": "SECRET_PLATFORM_DESCRIPTION platform-private@example.test",
        "강좌소개 첨부파일": "SECRET_PLATFORM_ATTACHMENT.hwp",
        "강사": "SECRET_PLATFORM_INSTRUCTOR 010-2222-3333",
        "강의계획서": "SECRET_PLATFORM_PLAN.pdf",
        "주의사항": "SECRET_PLATFORM_WARNING",
        "검색키워드": "SECRET_PLATFORM_KEYWORD",
        "강좌제한": "SECRET_PLATFORM_LIMIT",
    }
    definitions = "".join(
        f"<dl><dt>{escape(label)}</dt><dd>{escape(safe.get(label, skipped.get(label, '')))}</dd></dl>"
        for label in dongnae._PLATFORM_DETAIL_REQUIRED
    )
    title = "다른 공유 인문학" if wrong_title else "공유 인문학"
    return f"""
      <html><head><title>부산평생학습플랫폼</title></head><body>
        <form id="learningVO" method="post">
          <input name="inst_id" value="{dongnae.BUSAN_LIFELONG_DONGNAE_OFFICE}">
          <input name="lng_id" value="LEARNING_00090001">
        </form>
        <h2 class="enrolTit"><span>[동래구청]</span>{escape(title)}</h2>
        <div class="form_group">{definitions}</div>
        <a id="learning_aply_btn" onclick="fn_learning_apply(); return false;">일반모집신청</a>
      </body></html>
    """


def _city_card(*, title: str = "주민센터 생활요가", wrong_owner: bool = False) -> str:
    branch = "서구 다른동 주민자치회" if wrong_owner else "동래구 수민동 주민자치회"
    values = (
        ("기관", branch),
        ("대상", "제한없음"),
        ("장소", "수민동 프로그램실"),
        ("일자", "[신청] 2099-07-01 ~ 2099-07-31 [행사] 2099-08-01 ~ 2099-08-31"),
        ("방법", "온라인(선착순)"),
        ("문의", "SECRET_CITY_CARD_PHONE 051-550-9999"),
    )
    definitions = "".join(
        f"<dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in values
    )
    return f"""
      <li><a class="reserveItem" href="javascript:void(0);"
        onclick="fn_viewProgrm('186', '10001');return false;">
        <div class="infoBox"><p class="tit" title="{escape(title)}">{escape(title)}</p>
          <span class="statusMark possible">접수중</span><dl>{definitions}</dl></div>
      </a></li>
    """


def _city_page(
    page: int,
    *,
    drift: bool = False,
    bad_sentinel: bool = False,
    wrong_owner: bool = False,
) -> str:
    cards = ""
    if page == 1 or bad_sentinel:
        cards = _city_card(
            title="변경된 주민센터 생활요가" if drift else "주민센터 생활요가",
            wrong_owner=wrong_owner,
        )
    reserve_list = f'<ul class="reserveList">{cards}</ul>' if cards else ""
    return f"""
      <html><head><title>강좌/교육 : 부산광역시 통합예약</title></head><body>
        <form id="srchForm" name="srchForm" method="get" action="/lctre">
          <input name="curPage" value="{page}">
          <select name="srchGugun"><option value="6" selected>동래구</option></select>
          <select name="srchResveInsttCd"><option value="33" selected>주민자치회</option></select>
        </form>{reserve_list}
        <div class="paginate"><a class="pgEnd"
          href="?curPage=1&amp;srchGugun=6&amp;srchResveInsttCd=33">마지막</a></div>
      </body></html>
    """


def _city_detail(*, wrong_identity: bool = False) -> str:
    program = "99999" if wrong_identity else "10001"
    values = (
        ("운영기간", "2099-08-01(토) ~ 2099-08-31(월)"),
        ("신청기간", "2099-07-01(수) 09:00 ~ 2099-07-31(금) 18:00"),
        ("취소여부", "취소 가능"),
        ("신청방법", "온라인(선착순)"),
        ("수강료", "0 원"),
        ("요일 /시간", "수 / 10:00 ~ 12:00"),
        ("문의전화", "SECRET_CITY_DETAIL_PHONE 051-550-8888"),
        ("운영기관", "동래구 수민동 주민자치회"),
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
          <input name="resveGroupSn" value="186"><input name="progrmSn" value="{program}">
          <div class="contHeader"><h3 class="titPage">주민센터 생활요가
            <span class="statusMark possible">접수중</span></h3></div>
          <div class="reserveStateWrap"><div class="reserveStateInfo">{definitions}</div>
            <div class="reserveBtnWrap"><a class="btnTypeXL">예약하기</a></div></div>
          <div class="reserveDetail">SECRET_CITY_FREE_FORM city-private@example.test</div>
        </form>
      </body></html>
    """


class _Backend:
    def __init__(
        self,
        *,
        bad_local_sentinel: bool = False,
        bad_platform_sentinel: bool = False,
        bad_city_sentinel: bool = False,
        local_drift: bool = False,
        platform_drift: bool = False,
        city_drift: bool = False,
        unmatched_external: bool = False,
        changed_test: bool = False,
        wrong_city_owner: bool = False,
        wrong_local_title: bool = False,
        wrong_platform_title: bool = False,
        wrong_city_identity: bool = False,
        transient_local_detail: bool = False,
    ) -> None:
        self.bad_local_sentinel = bad_local_sentinel
        self.bad_platform_sentinel = bad_platform_sentinel
        self.bad_city_sentinel = bad_city_sentinel
        self.local_drift = local_drift
        self.platform_drift = platform_drift
        self.city_drift = city_drift
        self.unmatched_external = unmatched_external
        self.changed_test = changed_test
        self.wrong_city_owner = wrong_city_owner
        self.wrong_local_title = wrong_local_title
        self.wrong_platform_title = wrong_platform_title
        self.wrong_city_identity = wrong_city_identity
        self.transient_local_detail = transient_local_detail
        self.calls: Counter[str] = Counter()
        self.urls: list[str] = []
        self.lock = Lock()

    def session(self) -> _Session:
        return _Session()

    def _record(self, key: str, url: str) -> int:
        with self.lock:
            self.calls[key] += 1
            self.urls.append(url)
            return self.calls[key]

    def fetch(self, _session: _Session, url: str, timeout: int) -> _Response:
        assert timeout > 0
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.hostname == dongnae.BUSAN_DONGNAE_HOST:
            identity = (query.get("docNo") or [""])[0]
            if identity:
                count = self._record(f"local-detail-{identity}", url)
                if self.transient_local_detail and count == 1:
                    return _Response(
                        url,
                        "<html><head><title>temporary error</title></head><body>temporary</body></html>",
                    )
                return _Response(url, _local_detail(wrong_title=self.wrong_local_title))
            page = int((query.get("pageno") or ["1"])[0])
            count = self._record(f"local-list-{page}", url)
            return _Response(
                url,
                _local_page(
                    page,
                    drift=self.local_drift and page == 1 and count >= 2,
                    bad_sentinel=self.bad_local_sentinel,
                ),
            )
        if parsed.hostname == "lll.busan.go.kr":
            if parsed.path == dongnae.BUSAN_LIFELONG_LIST_PATH:
                page = int((query.get("pageIndex") or ["1"])[0])
                count = self._record(f"platform-list-{page}", url)
                return _Response(
                    url,
                    _platform_page(
                        page,
                        drift=self.platform_drift and page == 1 and count >= 2,
                        bad_sentinel=self.bad_platform_sentinel,
                        unmatched_external=self.unmatched_external,
                        changed_test=self.changed_test,
                    ),
                )
            if parsed.path == dongnae.BUSAN_LIFELONG_DETAIL_PATH:
                identity = (query.get("lng_id") or [""])[0]
                self._record(f"platform-detail-{identity}", url)
                return _Response(
                    url,
                    _platform_detail(wrong_title=self.wrong_platform_title),
                )
            raise AssertionError("platform applicant route must never be fetched")
        if parsed.hostname == dongnae.BUSAN_CITY_HOST:
            if parsed.path == dongnae.BUSAN_CITY_LIST_PATH:
                page = int((query.get("curPage") or ["1"])[0])
                count = self._record(f"city-list-{page}", url)
                return _Response(
                    url,
                    _city_page(
                        page,
                        drift=self.city_drift and page == 1 and count >= 2,
                        bad_sentinel=self.bad_city_sentinel,
                        wrong_owner=self.wrong_city_owner,
                    ),
                )
            if parsed.path == dongnae.BUSAN_CITY_DETAIL_PATH:
                self._record("city-detail", url)
                return _Response(
                    url, _city_detail(wrong_identity=self.wrong_city_identity)
                )
        raise AssertionError(f"unexpected fetch {url}")


def _collect(backend: _Backend, **kwargs: Any):
    return dongnae.collect_busan_dongnae_education(
        _target(),
        timeout=5,
        max_pages=kwargs.pop("max_pages", 3),
        detail_limit=kwargs.pop("detail_limit", 3),
        max_requests=kwargs.pop("max_requests", 20),
        today="2099-07-20",
        fetcher=backend.fetch,
        session_factory=backend.session,
        sleeper=lambda _seconds: None,
        max_workers=1,
        **kwargs,
    )


def test_candidate_classification_owner_boundary_and_audit_are_exact() -> None:
    assert dongnae.BUSAN_DONGNAE_PROVIDER == "MUNI_WWW_DONGNAE_GO_KR_742D8C71"
    assert dongnae.BUSAN_DONGNAE_CANDIDATE_ID == "MUNI_IR_30764A234E6F"
    audit = dongnae.BUSAN_DONGNAE_OWNER_BOUNDARY_AUDIT
    assert audit[dongnae.BUSAN_DONGNAE_PROVIDER]["decision"] == (
        "canonical_complete_district_education_owner"
    )
    assert audit[dongnae.BUSAN_DONGNAE_ALIAS_PROVIDER]["decision"].startswith(
        "duplicate_alias"
    )
    assert audit[dongnae.BUSAN_CITY_DONGNAE_PROVIDER]["filter"] == {
        "srchGugun": "6",
        "srchResveInsttCd": "33",
    }
    discovery = dongnae.BUSAN_DONGNAE_DISCOVERY_AUDIT
    assert discovery["canonical_rows"] == 224
    assert discovery["lifelong_rows"] == 232
    assert discovery["lifelong_external_rows"] == 222
    assert discovery["resident_rows"] == 83
    assert discovery["atomic_current_rows"] == 117
    office = dongnae._platform_office()
    assert office.code == "OFFICE_00002682"
    assert office.ownership == "duplicate_dedicated_dongnae_owner"
    assert office.municipality_code == ""
    assert office.municipality_name == ""


def test_target_urls_and_cross_platform_identity_are_exact() -> None:
    assert dongnae.is_busan_dongnae_education_target(_target())
    assert not dongnae.is_busan_dongnae_education_target(
        _target(url=dongnae.BUSAN_DONGNAE_ALIAS_URL)
    )
    assert not dongnae.is_busan_dongnae_education_target(
        _target(url=dongnae.BUSAN_DONGNAE_URL + "&possible=1")
    )
    assert dongnae.busan_dongnae_list_url(2).endswith(
        "menuCd=DOM_000000707002000000&pageno=2"
    )
    assert dongnae.busan_dongnae_city_list_url(3).endswith(
        "curPage=3&srchGugun=6&srchResveInsttCd=33"
    )
    assert parse_qs(urlparse(dongnae.busan_dongnae_lifelong_list_url()).query)[
        "pageUnit"
    ] == ["1000"]
    detail = dongnae.busan_dongnae_detail_url("20990701000001")
    titled = detail + "&title=" + "동래 인문학"
    assert dongnae.canonical_busan_dongnae_course_identity(detail) == (
        "docno:20990701000001"
    )
    assert dongnae.canonical_busan_dongnae_course_identity(titled) == (
        "docno:20990701000001"
    )
    assert dongnae.canonical_busan_dongnae_course_identity(titled + "&x=1") == ""
    with pytest.raises(dongnae.BusanDongnaeContractError):
        dongnae.busan_dongnae_list_url(True)
    with pytest.raises(dongnae.BusanDongnaeContractError):
        dongnae.busan_dongnae_city_detail_url("186", "https://evil.example")


def test_closed_stale_login_control_uses_verified_daily_fail_closed_rule() -> None:
    active, stale, kind = dongnae._local_application_decision(
        source_status="마감",
        apply_start="2026-07-07",
        apply_end="2026-07-22",
        cutoff=date(2026, 7, 22),
        control_actions=["javascript:goLogin('W');"],
    )
    assert (active, stale, kind) == (
        False,
        True,
        "stale_login_suppressed",
    )

    with pytest.raises(
        dongnae.BusanDongnaeContractError,
        match="precedes verified application end",
    ):
        dongnae._local_application_decision(
            source_status="마감",
            apply_start="2026-07-07",
            apply_end="2026-07-22",
            cutoff=date(2026, 7, 21),
            control_actions=["javascript:goLogin('W');"],
        )
    with pytest.raises(
        dongnae.BusanDongnaeContractError,
        match="scheduled Dongnae course unexpectedly exposes a control",
    ):
        dongnae._local_application_decision(
            source_status="접수예정",
            apply_start="2026-07-23",
            apply_end="2026-07-31",
            cutoff=date(2026, 7, 22),
            control_actions=["javascript:goLogin('W');"],
        )
    with pytest.raises(
        dongnae.BusanDongnaeContractError,
        match="application control changed",
    ):
        dongnae._local_application_decision(
            source_status="마감",
            apply_start="2026-07-07",
            apply_end="2026-07-22",
            cutoff=date(2026, 7, 22),
            control_actions=["javascript:goLogin('X');"],
        )

    url = dongnae.busan_dongnae_list_url(1)
    rows, _total, _last = dongnae._parse_local_page(
        BeautifulSoup(_local_page(1), "html.parser"), url, page=1
    )
    parent = dict(rows[0])
    parent["status"] = "CLOSED"
    parent["raw_fields"] = {**parent["raw_fields"], "source_status": "마감"}
    detail = BeautifulSoup(_local_detail(), "html.parser")
    result = dongnae._parse_local_detail(
        detail, parent["raw_url"], parent, cutoff=date(2099, 7, 31)
    )
    assert result["status"] == "CLOSED"
    assert result["application_url"] == ""
    assert result["reservation_available"] is False
    assert result["raw_fields"]["closed_stale_login_control_suppressed"] is True
    assert result["raw_fields"]["detail_application_control_kind"] == (
        "stale_login_suppressed"
    )
    assert "detail_application_control_action" not in result["raw_fields"]

    with pytest.raises(
        dongnae.BusanDongnaeContractError,
        match="precedes verified application end",
    ):
        dongnae._parse_local_detail(
            detail, parent["raw_url"], parent, cutoff=date(2099, 7, 30)
        )

    unknown = BeautifulSoup(
        _local_detail().replace("goLogin('W')", "goLogin('X')"), "html.parser"
    )
    with pytest.raises(
        dongnae.BusanDongnaeContractError,
        match="application control changed",
    ):
        dongnae._parse_local_detail(
            unknown, parent["raw_url"], parent, cutoff=date(2099, 7, 31)
        )


def test_complete_three_ledger_snapshot_identity_suppression_and_privacy() -> None:
    backend = _Backend()
    rows, parser, meta = _collect(backend)

    assert parser == dongnae.BUSAN_DONGNAE_PARSER
    assert [row["provider_course_id"] for row in rows] == [
        f"{dongnae.BUSAN_DONGNAE_PROVIDER}:education:20990701000001",
        f"{dongnae.BUSAN_DONGNAE_PROVIDER}:lifelong:LEARNING_00090001",
        f"{dongnae.BUSAN_DONGNAE_PROVIDER}:reserve:186:10001",
    ]
    assert meta["local_source_rows"] == 2
    assert meta["local_current_count"] == 1
    assert meta["platform_source_rows"] == 3
    assert meta["platform_external_duplicate_rows"] == 1
    assert meta["platform_external_unique_docnos"] == 1
    assert meta["platform_native_rows"] == 2
    assert meta["platform_training_test_rows"] == 1
    assert meta["platform_native_current_count"] == 1
    assert meta["city_source_rows"] == 1
    assert meta["city_current_count"] == 1
    assert meta["source_total"] == 6
    assert meta["unique_education_source_rows"] == 4
    assert meta["current_source_count"] == 3
    assert meta["expired_count"] == 1
    assert meta["required_list_requests"] == 10
    assert meta["list_requests"] == 10
    assert meta["sentinel_requests"] == 4
    assert meta["stability_rechecks"] == 4
    assert meta["detail_pages"] == 3
    assert meta["network_requests"] == 13
    assert meta["application_control_count"] == 3
    assert meta["local_closed_stale_login_control_count"] == 0
    assert meta["status_counts"] == {"OPEN": 3}
    assert meta["snapshot_complete"] is True
    assert meta["pagination_complete"] is True
    assert meta["details_complete"] is True
    assert meta["configured_collection_error"] == ""
    assert {row["raw_fields"]["source_catalog"] for row in rows} == {
        "dongnae_complete_lifelong_catalogue",
        "busan_lifelong_dongnae_native",
        "busan_reserve_dongnae_resident_councils",
    }
    assert all(row["municipality_code"] == "2626000000" for row in rows)
    assert all(row["reservation_available"] is True for row in rows)
    assert not any("goLogin" in url for url in backend.urls)
    assert not any("applicant" in url.casefold() for url in backend.urls)

    serialized = repr(rows)
    assert "goLogin" not in serialized
    for secret in (
        "550-4466",
        "SECRET_LOCAL_ENROLLMENT",
        "SECRET_LOCAL_WAITLIST",
        "SECRET_LOCAL_PHONE",
        "SECRET_LOCAL_INSTRUCTOR",
        "SECRET_LOCAL_DESCRIPTION",
        "local-private@example.test",
        "SECRET_LOCAL_ATTACHMENT",
        "SECRET_LIST_INSTRUCTOR",
        "SECRET_SESSION",
        "SECRET_PLATFORM_PHONE",
        "SECRET_ENROLLMENT",
        "SECRET_PLATFORM_DESCRIPTION",
        "platform-private@example.test",
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
        "city-private@example.test",
    ):
        assert secret not in serialized
    assert "[redacted]" in rows[0]["title"]
    assert all(row["raw_fields"]["application_form_fetched"] is False for row in rows)


def test_transient_status_200_error_page_is_retried_atomically() -> None:
    rows, _parser, meta = _collect(_Backend(transient_local_detail=True))
    assert len(rows) == 3
    assert meta["snapshot_complete"] is True
    assert meta["network_retry_count"] == 1
    assert meta["network_requests"] == 14


@pytest.mark.parametrize(
    ("flag", "needle"),
    (
        ("bad_local_sentinel", "sentinel"),
        ("bad_platform_sentinel", "sentinel"),
        ("bad_city_sentinel", "sentinel"),
        ("local_drift", "boundary page changed"),
        ("platform_drift", "complete censuses changed"),
        ("city_drift", "boundary page changed"),
        ("unmatched_external", "absent from canonical census"),
        ("changed_test", "training test changed"),
        ("wrong_city_owner", "left Dongnae owner"),
        ("wrong_local_title", "title mismatch"),
        ("wrong_platform_title", "title mismatch"),
        ("wrong_city_identity", "detail identity changed"),
    ),
)
def test_any_source_contract_failure_discards_all_three_ledgers(
    flag: str, needle: str
) -> None:
    rows, _parser, meta = _collect(_Backend(**{flag: True}))
    assert rows == []
    assert meta["snapshot_complete"] is False
    assert needle.casefold() in meta["configured_collection_error"].casefold()


def test_caps_dedupe_and_wrong_target_fail_closed() -> None:
    rows, _parser, meta = _collect(_Backend(), max_pages=0)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), detail_limit=2)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "detail_limit cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(_Backend(), max_requests=9)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert "max_requests cap" in meta["configured_collection_error"]

    rows, _parser, meta = _collect(
        _Backend(), dedupe_rows=lambda values: values[:2]
    )
    assert rows == []
    assert "dedupe changed" in meta["configured_collection_error"]

    backend = _Backend()
    rows, _parser, meta = dongnae.collect_busan_dongnae_education(
        _target(url=dongnae.BUSAN_DONGNAE_ALIAS_URL),
        fetcher=backend.fetch,
        session_factory=backend.session,
    )
    assert rows == []
    assert backend.urls == []
    assert "exact canonical" in meta["configured_collection_error"]


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MUNICIPAL_AUDIT") != "1",
    reason="set RUN_LIVE_MUNICIPAL_AUDIT=1 for the 145-request live audit",
)
def test_live_complete_snapshot_matches_latest_audit_floor() -> None:
    rows, _parser, meta = dongnae.collect_busan_dongnae_education(
        _target(), today="2026-07-22"
    )
    assert meta["snapshot_complete"] is True
    assert meta["local_source_rows"] >= 224
    assert meta["local_data_pages"] >= 9
    assert meta["local_current_count"] >= 25
    assert meta["platform_source_rows"] >= 232
    assert meta["platform_external_duplicate_rows"] >= 222
    assert meta["platform_native_rows"] >= 10
    assert meta["platform_native_current_count"] >= 9
    assert meta["city_source_rows"] >= 83
    assert meta["city_current_count"] >= 83
    assert len(rows) == meta["current_source_count"]
    stale = [
        row
        for row in rows
        if row.get("raw_fields", {}).get(
            "closed_stale_login_control_suppressed"
        )
    ]
    assert meta["local_closed_stale_login_control_count"] == len(stale)
    assert len(stale) >= 8
    assert all(row["status"] == "CLOSED" for row in stale)
    assert all(row["application_url"] == "" for row in stale)
    assert all(row["reservation_available"] is False for row in stale)
    assert "goLogin" not in repr(rows)
    assert not any(
        "detail_application_control_action" in row.get("raw_fields", {})
        for row in rows
    )
